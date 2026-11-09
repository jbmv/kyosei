"""KYO-703: `kyo serve`'s saved composer drafts.

Every request goes over a real socket, and each error that names a remedy is followed in the
same test by that remedy and the repeated request.
"""

# ruff: noqa: F811 - a fixture borrowed from another test module is named by its parameters

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from kyosei import cmd_check_clean, config, device_tokens, serve, serve_files

from .test_kyo_serve import body_json
from .test_kyo_serve_send import enable, issue
from .test_kyo_serve_sessions import (  # noqa: F401 - the fixtures, by name
    OLD,
    SESSIONS,
    Bed,
    bed,
    running,
)

DRAFT = f"{SESSIONS}/{OLD}/draft"
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
    assert read["boxes"] == BOXES["boxes"]  # a box's other keys are kept as written
    assert read["device"]["name"] == "ipad" and read["updated_at"].endswith("Z")

    replacement = {"boxes": [{"text": "only one now"}]}
    status, headers, replaced = request(bed, "PUT", replacement, token, If_Match=etag)
    assert status == 200 and headers["ETag"] != etag
    assert request(bed, "GET", None, token)[2]["boxes"] == replacement["boxes"]


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
    assert request(bed, "GET", None, token)[2]["boxes"] == newer["boxes"]

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
    assert request(bed_two, "GET", None, token)[2]["boxes"] == BOXES["boxes"]


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
