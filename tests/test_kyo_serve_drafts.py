"""KYO-703: `kyo serve`'s saved composer drafts.

Every request goes over a real socket, and each error that names a remedy is followed in the
same test by that remedy and the repeated request.
"""

# ruff: noqa: F811 - a fixture borrowed from another test module is named by its parameters

from __future__ import annotations

import http.client
import json
import stat
import time
from pathlib import Path

import pytest

from kyosei import cmd_check_clean, config, device_tokens, serve, serve_files

from .test_kyo_serve import body_json
from .test_kyo_serve_send import enable, issue
from .test_kyo_serve_sessions import (  # noqa: F401 - the fixtures, by name
    BASE,
    ENDED,
    KILLED,
    OLD,
    SESSIONS,
    Bed,
    bed,
    running,
    stream,
)

DRAFT = f"{SESSIONS}/{OLD}/draft"
#: KYO-763: the project's own draft, and the drafts of its two sessions that are not live.
PROJECT_DRAFT = f"{BASE}/draft"
ENDED_DRAFT = f"{SESSIONS}/{ENDED}/draft"
KILLED_DRAFT = f"{SESSIONS}/{KILLED}/draft"
BOXES = {"boxes": [{"text": "first box"}, {"text": "second", "anchors": ["u-1"]}]}


@pytest.fixture
def token(store_home: Path) -> str:
    return issue("ipad", device_tokens.PERMISSION_SEND)


def request(
    bed: Bed, method: str, document: dict | None, token: str | None, path: str = DRAFT, **headers
) -> tuple[int, dict, dict]:
    sent = {key.replace("_", "-"): value for key, value in headers.items()}
    if token is not None:
        sent["Authorization"] = f"Bearer {token}"
    body = None if document is None else json.dumps(document).encode("utf-8")
    status, answered, raw = bed.client.request(path, method=method, headers=sent, body=body)
    return status, answered, body_json(raw)


def create(bed: Bed, token: str, document: dict = BOXES) -> str:
    status, headers, made = request(bed, "PUT", document, token, If_None_Match="*")
    assert status == 201, made
    assert headers["ETag"] == made["etag"]
    return made["etag"]


def unnamed(boxes: list[dict]) -> list[dict]:
    """The boxes without the id each is given (KYO-798), to compare with what was written."""
    assert all(isinstance(box.get("id"), str) and box["id"] for box in boxes)
    return [{key: value for key, value in box.items() if key != "id"} for box in boxes]


def draft_file(store_home: Path) -> Path:
    return store_home / config.COMPOSER_DIRNAME / serve_files.DRAFTS / f"{OLD}.json"


def test_a_session_with_no_draft_reads_as_an_empty_one(bed: Bed, token: str) -> None:
    status, headers, read = request(bed, "GET", None, token)
    assert status == 200 and "ETag" not in headers
    assert read["boxes"] == [] and read["etag"] is None and read["updated_at"] is None


def test_reading_a_draft_creates_no_directory(bed: Bed, store_home: Path, token: str) -> None:
    assert request(bed, "GET", None, token)[0] == 200
    assert not (store_home / config.COMPOSER_DIRNAME).exists()


def test_a_draft_is_created_read_and_replaced(bed: Bed, store_home: Path, token: str) -> None:
    etag = create(bed, token)

    status, headers, read = request(bed, "GET", None, token)
    assert status == 200 and headers["ETag"] == etag == read["etag"]
    assert unnamed(read["boxes"]) == BOXES["boxes"]  # a box's other keys are kept as written
    assert read["device"]["name"] == "ipad" and read["updated_at"].endswith("Z")

    replacement = {"boxes": [{"text": "only one now"}]}
    status, headers, replaced = request(bed, "PUT", replacement, token, If_Match=etag)
    assert status == 200 and headers["ETag"] != etag
    assert unnamed(request(bed, "GET", None, token)[2]["boxes"]) == replacement["boxes"]


def test_an_empty_list_is_an_empty_draft(bed: Bed, token: str) -> None:
    etag = create(bed, token)
    assert request(bed, "PUT", {"boxes": []}, token, If_Match=etag)[0] == 200
    assert request(bed, "GET", None, token)[2]["boxes"] == []


def test_a_stale_writer_is_refused_and_reading_again_clears_it(bed: Bed, token: str) -> None:
    etag = create(bed, token)
    newer = {"boxes": [{"text": "written on the other device"}]}
    status, _, written = request(bed, "PUT", newer, token, If_Match=etag)
    assert status == 200

    status, _, refused = request(bed, "PUT", {"boxes": [{"text": "stale"}]}, token, If_Match=etag)

    assert status == 412 and refused["etag"] == written["etag"]
    assert "GET it again" in refused["error"]
    assert unnamed(request(bed, "GET", None, token)[2]["boxes"]) == newer["boxes"]

    fresh = request(bed, "GET", None, token)[2]["etag"]  # the remedy the refusal named
    assert request(bed, "PUT", {"boxes": [{"text": "merged"}]}, token, If_Match=fresh)[0] == 200


def test_the_same_boxes_written_twice_still_change_the_etag(bed: Bed, token: str) -> None:
    # An etag over the boxes alone would let a writer holding the older one overwrite the
    # device that saved identical boxes in between.
    etag = create(bed, token)
    second = request(bed, "PUT", BOXES, token, If_Match=etag)[2]["etag"]
    assert second != etag
    assert request(bed, "PUT", BOXES, token, If_Match=etag)[0] == 412


def test_a_write_stating_no_precondition_is_refused_and_stating_one_clears_it(
    bed: Bed, token: str
) -> None:
    status, _, refused = request(bed, "PUT", BOXES, token)
    assert status == 428 and "If-None-Match: *" in refused["error"]
    assert request(bed, "PUT", BOXES, token, If_None_Match="*")[0] == 201


def test_creating_over_a_draft_is_refused_and_if_match_clears_it(bed: Bed, token: str) -> None:
    etag = create(bed, token)
    status, _, refused = request(bed, "PUT", BOXES, token, If_None_Match="*")
    assert status == 412 and refused["etag"] == etag and "If-Match" in refused["error"]
    assert request(bed, "PUT", BOXES, token, If_Match=etag)[0] == 200


def test_replacing_a_draft_that_is_not_there_is_refused_and_creating_clears_it(
    bed: Bed, token: str
) -> None:
    status, _, refused = request(bed, "PUT", BOXES, token, If_Match='"nothing"')
    assert status == 412 and "If-None-Match: *" in refused["error"]
    assert request(bed, "PUT", BOXES, token, If_None_Match="*")[0] == 201


def test_a_draft_survives_a_server_restart(
    bed: Bed, project: Path, tmp_path: Path, token: str, running
) -> None:
    from .test_kyo_serve import stop

    create(bed, token)
    stop(bed.client)
    bed_two = Bed(project, tmp_path, running(environ=bed.server.environ))
    assert unnamed(request(bed_two, "GET", None, token)[2]["boxes"]) == BOXES["boxes"]


def test_a_draft_is_private_to_this_account(bed: Bed, store_home: Path, token: str) -> None:
    create(bed, token)
    path = draft_file(store_home)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    leftovers = [p.name for p in path.parent.iterdir() if p != path]
    assert leftovers == []


def test_a_replaced_draft_stays_private(bed: Bed, store_home: Path, token: str) -> None:
    etag = create(bed, token)
    assert request(bed, "PUT", BOXES, token, If_Match=etag)[0] == 200
    assert stat.S_IMODE(draft_file(store_home).stat().st_mode) == 0o600


def test_a_draft_that_would_lie_in_a_repository_is_refused_and_moving_the_home_clears_it(
    bed: Bed, store_home: Path, token: str, tmp_path: Path
) -> None:
    repository = tmp_path / "a-repo"
    (repository / ".git").mkdir(parents=True)
    moved = repository / "home"
    moved.mkdir()
    for name in (device_tokens.TOKENS_FILE, serve.REGISTRY_FILE):
        (moved / name).write_text((store_home / name).read_text(encoding="utf-8"), encoding="utf-8")
    bed.server.environ["KYOSEI_HOME"] = str(moved)

    status, _, refused = request(bed, "PUT", BOXES, token, If_None_Match="*")

    assert status == 500 and refused["reason"] == "composer-state-unusable"
    assert str(repository.resolve()) in refused["error"]
    assert "$KYOSEI_HOME" in refused["error"]
    assert not list(moved.rglob(f"{OLD}.json"))

    bed.server.environ["KYOSEI_HOME"] = str(store_home)  # the remedy the refusal named
    assert request(bed, "PUT", BOXES, token, If_None_Match="*")[0] == 201


@pytest.mark.parametrize(("method", "document"), [("GET", None), ("PUT", BOXES)])
def test_a_route_with_no_token_is_refused_and_a_token_clears_it(
    bed: Bed, store_home: Path, method: str, document: dict | None
) -> None:
    status, headers, refused = request(bed, method, document, None, If_None_Match="*")
    assert status == 401 and refused["reason"] == "no-token"
    assert headers["WWW-Authenticate"] == "Bearer" and "kyo devices issue" in refused["error"]
    assert not draft_file(store_home).exists()

    token = issue("ipad", device_tokens.PERMISSION_SEND)
    assert request(bed, method, document, token, If_None_Match="*")[0] in (200, 201)


@pytest.mark.parametrize(("method", "document"), [("GET", None), ("PUT", BOXES)])
def test_a_token_without_send_permission_is_refused_and_the_remedy_clears_it(
    bed: Bed, store_home: Path, method: str, document: dict | None
) -> None:
    from .conftest import invoke

    bare = issue("reader")
    status, _, refused = request(bed, method, document, bare, If_None_Match="*")
    assert status == 403 and refused["reason"] == "no-send-permission"
    assert not draft_file(store_home).exists()

    assert invoke(["devices", "revoke", "reader"]).code == 0
    token = issue("reader", device_tokens.PERMISSION_SEND)
    assert request(bed, method, document, token, If_None_Match="*")[0] in (200, 201)


def test_a_draft_needs_no_send_setting(bed: Bed, store_home: Path, token: str) -> None:
    enable(store_home, False)
    assert request(bed, "PUT", BOXES, token, If_None_Match="*")[0] == 201


@pytest.mark.parametrize(
    "document",
    [{}, {"boxes": "x"}, {"boxes": ["x"]}, {"boxes": [{}]}, {"boxes": [{"text": 7}]}, ["x"]],
)
def test_a_malformed_draft_is_refused_and_correcting_it_clears_it(
    bed: Bed, token: str, document: object
) -> None:
    status, _, refused = request(bed, "PUT", document, token, If_None_Match="*")
    assert status == 400 and refused["reason"] == "bad-request"
    assert request(bed, "PUT", BOXES, token, If_None_Match="*")[0] == 201


def test_a_session_this_project_does_not_record_is_refused(bed: Bed, token: str) -> None:
    other = "55555555-5555-4555-8555-555555555555"
    status, _, refused = request(bed, "GET", None, token, path=f"{SESSIONS}/{other}/draft")
    assert status == 404 and "not recorded" in refused["error"]


def test_a_query_string_is_refused_so_a_token_cannot_reach_the_request_log(
    bed: Bed, token: str
) -> None:
    status, _, refused = request(bed, "GET", None, token, path=f"{DRAFT}?token={token}")
    assert status == 400 and "Authorization" in refused["error"]
    assert request(bed, "GET", None, token)[0] == 200


def test_the_draft_route_answers_get_and_put_only(bed: Bed, token: str) -> None:
    status, headers, _ = request(bed, "DELETE", None, token)
    assert status == 405 and headers["Allow"] == "GET, PUT"


def test_an_unreadable_draft_is_refused_and_removing_it_clears_it(
    bed: Bed, store_home: Path, token: str
) -> None:
    create(bed, token)
    path = draft_file(store_home)
    path.write_text("not json", encoding="utf-8")
    status, _, refused = request(bed, "GET", None, token)
    assert status == 500 and str(path) in refused["error"] and "remove" in refused["error"]

    path.unlink()  # the remedy the refusal named
    assert request(bed, "GET", None, token)[0] == 200


def test_the_composer_directory_is_not_taken_for_a_stray_home_directory(tmp_path: Path) -> None:
    (tmp_path / config.COMPOSER_DIRNAME).mkdir()
    (tmp_path / "a-project").mkdir()
    assert cmd_check_clean._home_dirs(tmp_path) == ["a-project"]


# ------------------------------------------------- KYO-763: the project's draft, between sessions


def put(bed: Bed, token: str, path: str, texts: list[str]) -> str:
    """Create the draft at ``path`` with one box for each text; its etag."""
    document = {"boxes": [{"text": text} for text in texts]}
    status, _, made = request(bed, "PUT", document, token, path=path, If_None_Match="*")
    assert status == 201, made
    return made["etag"]


def texts(bed: Bed, token: str, path: str) -> list[str]:
    status, _, read = request(bed, "GET", None, token, path=path)
    assert status == 200, read
    return [box["text"] for box in read["boxes"]]


def project_draft_file(store_home: Path) -> Path:
    return store_home / config.COMPOSER_DIRNAME / serve_files.PROJECT_DRAFTS / "proj.json"


def test_a_project_with_no_draft_reads_as_an_empty_one_and_creates_nothing(
    bed: Bed, store_home: Path, token: str
) -> None:
    status, headers, read = request(bed, "GET", None, token, path=PROJECT_DRAFT)
    assert status == 200 and "ETag" not in headers
    assert read["project"] == "proj" and read["boxes"] == [] and read["etag"] is None
    assert not (store_home / config.COMPOSER_DIRNAME).exists()


def test_a_project_draft_is_saved_read_and_replaced_with_its_marks(
    bed: Bed, store_home: Path, token: str
) -> None:
    marked = {"boxes": [{"text": "between sessions", "marks": [{"line": 0, "items": []}]}]}
    status, headers, made = request(
        bed, "PUT", marked, token, path=PROJECT_DRAFT, If_None_Match="*"
    )
    assert status == 201 and headers["ETag"] == made["etag"]

    status, headers, read = request(bed, "GET", None, token, path=PROJECT_DRAFT)
    assert status == 200 and headers["ETag"] == made["etag"] == read["etag"]
    assert unnamed(read["boxes"]) == marked["boxes"] and read["device"]["name"] == "ipad"

    path = project_draft_file(store_home)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    replacement = {"boxes": [{"text": "rewritten"}]}
    status, _, _ = request(
        bed, "PUT", replacement, token, path=PROJECT_DRAFT, If_Match=made["etag"]
    )
    assert status == 200 and texts(bed, token, PROJECT_DRAFT) == ["rewritten"]


def test_a_stale_save_of_a_project_draft_is_refused_and_reading_again_clears_it(
    bed: Bed, token: str
) -> None:
    etag = put(bed, token, PROJECT_DRAFT, ["first"])
    newer = {"boxes": [{"text": "written on the other device"}]}
    status, _, written = request(bed, "PUT", newer, token, path=PROJECT_DRAFT, If_Match=etag)
    assert status == 200

    stale = {"boxes": [{"text": "stale"}]}
    status, _, refused = request(bed, "PUT", stale, token, path=PROJECT_DRAFT, If_Match=etag)

    assert status == 412 and refused["etag"] == written["etag"]
    assert "GET it again" in refused["error"]
    assert texts(bed, token, PROJECT_DRAFT) == ["written on the other device"]

    fresh = request(bed, "GET", None, token, path=PROJECT_DRAFT)[2]["etag"]  # the remedy named
    merged = {"boxes": [{"text": "merged"}]}
    assert request(bed, "PUT", merged, token, path=PROJECT_DRAFT, If_Match=fresh)[0] == 200


def test_a_project_draft_write_stating_no_precondition_is_refused(bed: Bed, token: str) -> None:
    status, _, refused = request(bed, "PUT", BOXES, token, path=PROJECT_DRAFT)
    assert status == 428 and "If-None-Match: *" in refused["error"]
    assert request(bed, "PUT", BOXES, token, path=PROJECT_DRAFT, If_None_Match="*")[0] == 201


@pytest.mark.parametrize(("method", "document"), [("GET", None), ("PUT", BOXES)])
def test_a_project_draft_needs_the_permission_a_session_draft_needs(
    bed: Bed, store_home: Path, method: str, document: dict | None
) -> None:
    status, _, refused = request(
        bed, method, document, None, path=PROJECT_DRAFT, If_None_Match="*"
    )
    assert status == 401 and refused["reason"] == "no-token"

    bare = issue("reader")
    status, _, refused = request(
        bed, method, document, bare, path=PROJECT_DRAFT, If_None_Match="*"
    )
    assert status == 403 and refused["reason"] == "no-send-permission"
    assert not project_draft_file(store_home).exists()

    token = issue("ipad", device_tokens.PERMISSION_SEND)
    status, _, _ = request(bed, method, document, token, path=PROJECT_DRAFT, If_None_Match="*")
    assert status in (200, 201)


def test_the_project_draft_route_refuses_a_query_string_and_other_methods(
    bed: Bed, token: str
) -> None:
    status, _, refused = request(bed, "GET", None, token, path=f"{PROJECT_DRAFT}?token={token}")
    assert status == 400 and "Authorization" in refused["error"]
    status, headers, _ = request(bed, "DELETE", None, token, path=PROJECT_DRAFT)
    assert status == 405 and headers["Allow"] == "GET, PUT"
    status, _, _ = request(bed, "GET", None, token, path=f"{PROJECT_DRAFT}/more")
    assert status == 404


def test_a_project_no_registry_names_has_no_draft(bed: Bed, token: str) -> None:
    status, _, refused = request(bed, "GET", None, token, path="/v1/projects/other/draft")
    assert status == 404 and "registry" in refused["error"]


def test_a_live_session_with_no_draft_takes_the_projects_once(bed: Bed, token: str) -> None:
    put(bed, token, PROJECT_DRAFT, ["written between sessions"])

    status, headers, first = request(bed, "GET", None, token)

    assert status == 200 and first["session_id"] == OLD
    assert [box["text"] for box in first["boxes"]] == ["written between sessions"]
    assert first["etag"] and headers["ETag"] == first["etag"]
    # The project's draft is emptied, so the next session is not offered the same boxes.
    assert texts(bed, token, PROJECT_DRAFT) == []
    # A second device opening the session reads the draft the first was given.
    status, _, second = request(bed, "GET", None, token)
    assert second["boxes"] == first["boxes"] and second["etag"] == first["etag"]
    # And the session's draft is replaced under the etag the taking returned.
    kept = {"boxes": [{"text": "edited in the session"}]}
    assert request(bed, "PUT", kept, token, If_Match=first["etag"])[0] == 200


def test_a_session_with_a_draft_of_its_own_leaves_the_projects(bed: Bed, token: str) -> None:
    put(bed, token, DRAFT, ["the session's own"])
    put(bed, token, PROJECT_DRAFT, ["the project's"])
    assert texts(bed, token, DRAFT) == ["the session's own"]
    assert texts(bed, token, PROJECT_DRAFT) == ["the project's"]


def test_a_session_whose_draft_was_emptied_takes_nothing(bed: Bed, token: str) -> None:
    etag = put(bed, token, DRAFT, ["sent since"])
    assert request(bed, "PUT", {"boxes": []}, token, If_Match=etag)[0] == 200
    put(bed, token, PROJECT_DRAFT, ["the project's"])
    assert texts(bed, token, DRAFT) == []
    assert texts(bed, token, PROJECT_DRAFT) == ["the project's"]


@pytest.mark.parametrize("path", [ENDED_DRAFT, KILLED_DRAFT])
def test_a_session_that_is_not_live_takes_nothing(bed: Bed, token: str, path: str) -> None:
    put(bed, token, PROJECT_DRAFT, ["the project's"])
    status, _, read = request(bed, "GET", None, token, path=path)
    assert status == 200 and read["boxes"] == [] and read["etag"] is None
    assert texts(bed, token, PROJECT_DRAFT) == ["the project's"]


def test_an_ended_sessions_boxes_become_the_projects_draft(bed: Bed, token: str) -> None:
    put(bed, token, ENDED_DRAFT, ["left unsent when the session ended"])

    status, headers, read = request(bed, "GET", None, token, path=PROJECT_DRAFT)

    assert status == 200 and headers["ETag"] == read["etag"]
    assert [box["text"] for box in read["boxes"]] == ["left unsent when the session ended"]
    assert texts(bed, token, ENDED_DRAFT) == []  # moved, not copied
    # Read again, the project's draft is the same one: nothing is moved twice.
    assert request(bed, "GET", None, token, path=PROJECT_DRAFT)[2]["etag"] == read["etag"]


def test_ended_sessions_boxes_follow_the_projects_own_oldest_session_first(
    bed: Bed, token: str
) -> None:
    put(bed, token, PROJECT_DRAFT, ["the project's own", "in both"])
    put(bed, token, ENDED_DRAFT, ["ended at eleven", "in both"])  # ended 11:00
    put(bed, token, KILLED_DRAFT, ["killed at nine"])  # last seen 09:00, no process
    # A session recorded as running is known to have ended once its transcript is found
    # and no process runs it.
    (bed.transcripts / f"{KILLED}.jsonl").write_text("".join(bed.rows()), encoding="utf-8")
    assert texts(bed, token, PROJECT_DRAFT) == [
        "the project's own",
        "in both",
        "killed at nine",
        "ended at eleven",
    ]
    assert texts(bed, token, ENDED_DRAFT) == [] and texts(bed, token, KILLED_DRAFT) == []


def test_a_live_sessions_draft_is_not_moved_to_the_project(bed: Bed, token: str) -> None:
    put(bed, token, DRAFT, ["being written in the live session"])
    assert texts(bed, token, PROJECT_DRAFT) == []
    assert texts(bed, token, DRAFT) == ["being written in the live session"]


def test_a_new_session_takes_what_the_ended_one_left(bed: Bed, token: str) -> None:
    # No device read the project's draft in between: the ended session's boxes pass
    # straight to the live session that opens with none.
    put(bed, token, ENDED_DRAFT, ["left unsent"])
    assert texts(bed, token, DRAFT) == ["left unsent"]
    assert texts(bed, token, ENDED_DRAFT) == [] and texts(bed, token, PROJECT_DRAFT) == []


def test_devices_opening_a_new_session_together_are_given_the_same_draft(
    bed: Bed, token: str
) -> None:
    import threading

    put(bed, token, PROJECT_DRAFT, ["written between sessions"])
    answers: list[dict] = []

    def ask() -> None:
        answers.append(request(bed, "GET", None, token)[2])  # each on its own connection

    threads = [threading.Thread(target=ask) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(answers) == 6
    assert {answer["etag"] for answer in answers} == {answers[0]["etag"]}
    assert all(
        [box["text"] for box in answer["boxes"]] == ["written between sessions"]
        for answer in answers
    )
    assert texts(bed, token, PROJECT_DRAFT) == []


def test_a_save_made_to_the_project_during_a_taking_is_kept(
    bed: Bed, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The session takes the copy it read; a device that saved to the project's draft after
    # that read holds the newer copy, which is not emptied from under it.
    from kyosei import serve_drafts

    put(bed, token, PROJECT_DRAFT, ["first"])
    original = serve_drafts._Drafts.move
    saved: list[bool] = []

    def move(self, session_id, boxes, etag):
        if session_id is not None and not saved:
            saved.append(True)
            newer = [{"text": "saved meanwhile"}]
            self.write(None, newer, if_match=self.read(None)[1], if_none_match=None)
        return original(self, session_id, boxes, etag)

    monkeypatch.setattr(serve_drafts._Drafts, "move", move)
    assert texts(bed, token, DRAFT) == ["first"]
    assert texts(bed, token, PROJECT_DRAFT) == ["saved meanwhile"]


def test_a_session_whose_state_cannot_be_read_keeps_its_draft(bed: Bed, token: str) -> None:
    # KILLED is recorded as running and has no transcript here, so nothing says whether a
    # process runs it; its boxes are not taken from a session that may be live.
    put(bed, token, KILLED_DRAFT, ["perhaps still running"])
    assert texts(bed, token, PROJECT_DRAFT) == []
    assert texts(bed, token, KILLED_DRAFT) == ["perhaps still running"]


def test_the_reference_states_the_project_draft_route_and_its_two_moves() -> None:
    topic = (
        Path(__file__).parent.parent / "docs" / "reference" / "topics" / "serve-and-desktop.md"
    ).read_text(encoding="utf-8")
    assert "| `/v1/projects/<name>/draft` | GET |" in topic
    assert "| `/v1/projects/<name>/draft` | PUT |" in topic
    assert f"composer/{serve_files.PROJECT_DRAFTS}/<name>.json" in topic
    assert "A session that has ended leaves its boxes to the project" in topic
    assert "A live session with no draft of its own takes the project's" in topic


# ------------------------------------- KYO-798: a box's id, and a save announced to other devices


def test_a_box_keeps_the_id_its_device_gave_it_and_one_without_is_given_one(
    bed: Bed, token: str
) -> None:
    written = {"boxes": [{"id": "from-the-laptop", "text": "one"}, {"text": "two"}]}
    made = request(bed, "PUT", written, token, If_None_Match="*")[2]
    first, second = made["boxes"]
    assert first == {"id": "from-the-laptop", "text": "one"}
    assert second["text"] == "two" and second["id"] not in ("", "from-the-laptop")
    # Every device reading the draft is given the same ids, and a save keeps them.
    assert request(bed, "GET", None, token)[2]["boxes"] == made["boxes"]
    edited = {"boxes": [{**second, "text": "two, edited"}, first]}
    again = request(bed, "PUT", edited, token, If_Match=made["etag"])[2]
    assert [box["id"] for box in again["boxes"]] == [second["id"], "from-the-laptop"]


def test_a_draft_stored_before_boxes_had_ids_reads_with_the_same_ids_on_every_device(
    bed: Bed, store_home: Path, token: str
) -> None:
    create(bed, token)
    path = draft_file(store_home)
    stored = {"boxes": [{"text": "same"}, {"text": "same"}, {"text": "other"}]}
    path.write_text(json.dumps(stored), encoding="utf-8")

    first = request(bed, "GET", None, token)[2]
    second = request(bed, "GET", None, token)[2]

    ids = [box["id"] for box in first["boxes"]]
    assert len(set(ids)) == 3 and all(ids)
    assert second["boxes"] == first["boxes"]
    assert json.loads(path.read_text(encoding="utf-8")) == stored  # a read wrote nothing


def test_an_id_repeated_in_a_draft_is_replaced_on_the_later_box(bed: Bed, token: str) -> None:
    twice = {"boxes": [{"id": "a", "text": "one"}, {"id": "a", "text": "two"}]}
    made = request(bed, "PUT", twice, token, If_None_Match="*")[2]
    assert made["boxes"][0]["id"] == "a" and made["boxes"][1]["id"] not in ("", "a")


def test_an_id_that_is_not_a_string_is_refused_and_leaving_it_out_clears_it(
    bed: Bed, token: str
) -> None:
    for bad in (7, "", None):
        status, _, refused = request(
            bed, "PUT", {"boxes": [{"id": bad, "text": "x"}]}, token, If_None_Match="*"
        )
        assert status == 400 and "leave the key out" in refused["error"]
    assert request(bed, "PUT", {"boxes": [{"text": "x"}]}, token, If_None_Match="*")[0] == 201


def test_boxes_keep_their_ids_from_a_sessions_draft_to_the_projects_and_into_the_next(
    bed: Bed, token: str
) -> None:
    document = {"boxes": [{"id": "kept", "text": "left unsent"}]}
    assert request(bed, "PUT", document, token, path=ENDED_DRAFT, If_None_Match="*")[0] == 201

    gathered = request(bed, "GET", None, token, path=PROJECT_DRAFT)[2]
    assert gathered["boxes"] == document["boxes"]
    taken = request(bed, "GET", None, token)[2]
    assert taken["boxes"] == document["boxes"]


def test_a_box_already_moved_to_the_project_is_not_added_again_by_its_id(
    bed: Bed, token: str
) -> None:
    """The emptying after a move failed, and the project's copy was edited since."""
    left = {"boxes": [{"id": "moved", "text": "as the session left it"}]}
    assert request(bed, "PUT", left, token, path=ENDED_DRAFT, If_None_Match="*")[0] == 201
    edited = {"boxes": [{"id": "moved", "text": "edited between sessions"}]}
    assert request(bed, "PUT", edited, token, path=PROJECT_DRAFT, If_None_Match="*")[0] == 201

    assert texts(bed, token, PROJECT_DRAFT) == ["edited between sessions"]


def test_a_session_draft_saved_from_one_device_is_announced_to_another_which_reads_it(
    bed: Bed, stream
) -> None:
    """KYO-798.c1: two devices, each with its own token, against one server."""
    laptop = issue("laptop", device_tokens.PERMISSION_SEND)
    studio = issue("studio", device_tokens.PERMISSION_SEND)
    following = stream()  # the stream the Studio's app already holds on the session
    assert following.event("ready")["draft"] == {"etag": None}

    typed = {"boxes": [{"id": "a", "text": "typed on the laptop"}]}
    made = request(bed, "PUT", typed, laptop, If_None_Match="*")[2]

    started = time.monotonic()
    announced = following.event("draft")
    assert time.monotonic() - started < 3
    assert announced == {"session_id": OLD, "etag": made["etag"]}  # the etag, and no box
    read = request(bed, "GET", None, studio)[2]
    assert read["boxes"] == typed["boxes"] and read["etag"] == announced["etag"]
    assert read["device"]["name"] == "laptop"

    # The Studio's own save is announced with the etag its save returned, which is how it
    # knows not to read its own draft back; a stream opened later states the etag at once.
    edited = {"boxes": [{"id": "a", "text": "typed on the laptop, then the Studio"}]}
    saved = request(bed, "PUT", edited, studio, If_Match=read["etag"])[2]
    assert following.event("draft")["etag"] == saved["etag"]
    assert stream().event("ready")["draft"] == {"etag": saved["etag"]}
    # The etag rule stands: the laptop's save over the draft it read first is refused.
    assert request(bed, "PUT", typed, laptop, If_Match=made["etag"])[0] == 412


def test_a_prompt_sent_from_one_device_leaves_the_draft_the_other_reads(
    bed: Bed, stream
) -> None:
    """KYO-798.c4: the sender saves the draft without the box, and the other device is told."""
    laptop = issue("laptop", device_tokens.PERMISSION_SEND)
    studio = issue("studio", device_tokens.PERMISSION_SEND)
    both = {"boxes": [{"id": "sent", "text": "to send"}, {"id": "kept", "text": "to keep"}]}
    made = request(bed, "PUT", both, laptop, If_None_Match="*")[2]
    following = stream()
    assert following.event("ready")["draft"] == {"etag": made["etag"]}

    left = {"boxes": [{"id": "kept", "text": "to keep"}]}
    after = request(bed, "PUT", left, laptop, If_Match=made["etag"])[2]

    assert following.event("draft")["etag"] == after["etag"]
    assert [box["id"] for box in request(bed, "GET", None, studio)[2]["boxes"]] == ["kept"]


def project_changes(bed: Bed) -> http.client.HTTPResponse:
    """The project's event stream, opened and read past its first lines."""
    conn = http.client.HTTPConnection("127.0.0.1", bed.client.port, timeout=10)
    conn.request("GET", f"{BASE}/events", headers=bed.client.reader)
    response = conn.getresponse()
    assert response.status == 200
    while response.fp.readline().strip():
        pass
    return response


def next_change(response: http.client.HTTPResponse) -> str:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        line = response.fp.readline().decode("utf-8").strip()
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))["kind"]
    raise TimeoutError("no change event arrived")


def test_a_project_draft_saved_from_one_device_is_announced_on_the_projects_stream(
    bed: Bed, token: str
) -> None:
    """KYO-798.c1, with no session live: the stream says that it changed, and nothing of it."""
    bed.server.poll_seconds = 0.05
    following = project_changes(bed)
    try:
        etag = put(bed, token, PROJECT_DRAFT, ["between sessions, on the laptop"])
        assert next_change(following) == "draft"
        studio = issue("studio", device_tokens.PERMISSION_SEND)
        read = request(bed, "GET", None, studio, path=PROJECT_DRAFT)[2]
        assert read["etag"] == etag
        assert [box["text"] for box in read["boxes"]] == ["between sessions, on the laptop"]

        replacement = {"boxes": [{"text": "replaced"}]}
        assert request(
            bed, "PUT", replacement, studio, path=PROJECT_DRAFT, If_Match=etag
        )[0] == 200
        assert next_change(following) == "draft"
    finally:
        following.close()
