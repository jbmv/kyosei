"""KYO-706: `kyo serve`'s note routes.

Every request goes over a real socket against a store the test built, with tmux faked as in
the send and held tests. Each error that names a remedy is followed in the same test by that
remedy and the repeated request.
"""

# ruff: noqa: F811 - a fixture borrowed from another test module is named by its parameters

from __future__ import annotations

import io
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from kyosei import serve_notes
from kyosei import store as store_module

from .conftest import invoke
from .test_kyo_serve import body_json
from .test_kyo_serve_held import looks, wait_until
from .test_kyo_serve_send import FRAME_WORKING, IDLE, enable, issue, keys_sent, log_rows, post
from .test_kyo_serve_sessions import (  # noqa: F401 - the fixtures, by name
    OLD,
    OLD_FILE,
    SESSIONS,
    Bed,
    bed,
    running,
)

SEND_NOTES = f"{SESSIONS}/{OLD}/send-notes"

#: The fixture transcript's reply beginning "A lighthouse is a fixed structure...".
REPLY = "9b949b65-606d-47fa-bbfd-f46f11c8635d"
#: The fixture transcript's prompt beginning "Write three paragraphs...".
PROMPT = "a740d627-9040-4847-89e2-e06b1bf18621"


def reply_words() -> str:
    for line in OLD_FILE.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("uuid") == REPLY:
            return row["message"]["content"][0]["text"]
    raise AssertionError("the fixture's reply is gone")


def span(uuid: str = REPLY, start: int = 0, end: int = 200) -> dict:
    return {"kind": "turn", "session": OLD, "uuid": uuid, "start": start, "end": end}


@pytest.fixture(autouse=True)
def working_screen(request: pytest.FixtureRequest) -> None:
    """A session mid-turn, looked at often; a test moves the screen to what it is about."""
    if "bed" in request.fixturenames:
        made = request.getfixturevalue("bed")
        made.show_frame(FRAME_WORKING)
        made.server.held_poll_seconds = 0.03


@pytest.fixture
def notes_path(bed: Bed) -> str:
    return f"{SESSIONS.rsplit('/', 1)[0]}/notes"


@pytest.fixture
def token(store_home: Path) -> str:
    return issue("ipad", "notes", "send")


def get(bed: Bed, path: str, token: str | None) -> tuple[int, dict]:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    status, _, body = bed.client.request(path, headers=headers)
    return status, body_json(body)


def file_note(bed: Bed, path: str, token: str, **fields) -> dict:
    document = {"text": "a note", "entered": "typed", **fields}
    status, made = post(bed, path, document, token)
    assert status == 201, made
    return made["note"]


def on_the_cli(bed: Bed, *args: str) -> dict:
    result = invoke(["--root", str(bed.project), "notes", *args, "--json"])
    assert result.code == 0, result
    return json.loads(result.out)


# ------------------------------------------------------------------ c1: filing a note


def test_a_note_is_filed_with_everything_a_client_observed(
    bed: Bed, notes_path: str, token: str
) -> None:
    note = file_note(
        bed,
        notes_path,
        token,
        text="why not reformat it ourselves?",
        entered="pasted",
        tags=["#Ask", "later"],
        anchors=[span()],
    )
    assert note["text"] == "why not reformat it ourselves?"
    assert note["entered"] == "pasted" and note["device"] == "ipad"
    assert note["tags"] == ["ask", "later"]
    assert note["author"] == "kyo-serve:ipad"
    anchor = note["anchors"][0]
    assert anchor["target"] == REPLY and anchor["role"] == "agent"
    assert anchor["digest_check"] == "computed"
    # Filed by the store's own write path: the CLI reads what the route wrote.
    assert on_the_cli(bed, "show", note["id"])["note"]["text"] == note["text"]
    ledger = bed.project / "kyosei" / "export" / "_person_notes.jsonl"
    assert note["id"] in ledger.read_text("utf-8")


def test_a_note_may_have_no_anchor_and_its_words_stay_out_of_the_anchor(
    bed: Bed, notes_path: str, token: str
) -> None:
    assert file_note(bed, notes_path, token)["anchors"] == []
    status, refused = post(
        bed,
        notes_path,
        {"text": "x", "entered": "typed", "anchors": [{**span(), "text": "copied words"}]},
        token,
    )
    assert status == 400 and "never the text" in refused["error"]


def test_the_device_comes_from_the_token_and_cannot_be_claimed(
    bed: Bed, notes_path: str, token: str
) -> None:
    document = {"text": "x", "entered": "typed", "device": "someone-elses"}
    status, refused = post(bed, notes_path, document, token)
    assert status == 400 and "'device'" in refused["error"] and "token" in refused["error"]
    del document["device"]
    assert post(bed, notes_path, document, token)[0] == 201


def test_a_note_route_refuses_without_a_token_and_the_remedy_clears_it(
    bed: Bed, notes_path: str, store_home: Path
) -> None:
    document = {"text": "x", "entered": "typed"}
    status, refused = post(bed, notes_path, document, None)
    assert status == 401 and refused["reason"] == "no-token"
    assert "kyo devices issue <name> --permission notes" in refused["error"]
    assert post(bed, notes_path, document, issue("phone", "notes"))[0] == 201


def test_a_token_without_notes_permission_is_refused_and_the_remedy_clears_it(
    bed: Bed, notes_path: str, store_home: Path
) -> None:
    send_only = issue("ipad", "send")
    document = {"text": "x", "entered": "typed"}
    status, refused = post(bed, notes_path, document, send_only)
    assert status == 403 and refused["reason"] == "no-notes-permission"
    assert "kyo devices revoke ipad" in refused["error"]
    assert "--permission notes" in refused["error"]
    assert invoke(["devices", "revoke", "ipad"]).code == 0
    assert post(bed, notes_path, document, issue("ipad", "notes", "send"))[0] == 201


def test_a_revoked_token_is_refused_at_once(
    bed: Bed, notes_path: str, store_home: Path
) -> None:
    token = issue("ipad", "notes")
    document = {"text": "x", "entered": "typed"}
    assert post(bed, notes_path, document, token)[0] == 201
    assert invoke(["devices", "revoke", "ipad"]).code == 0
    status, refused = post(bed, notes_path, document, token)
    assert status == 401 and refused["reason"] == "revoked-token"
    assert post(bed, notes_path, document, issue("ipad", "notes"))[0] == 201


@pytest.mark.parametrize(
    ("document", "words"),
    [
        ({"text": "x"}, '"entered"'),
        ({"text": "x", "entered": "scribbled"}, '"entered"'),
        ({"entered": "typed"}, "text"),
        ({"text": 5, "entered": "typed"}, '"text"'),
        ({"text": "x", "entered": "typed", "anchors": {}}, '"anchors"'),
        ({"text": "x", "entered": "typed", "tags": [1]}, '"tags"'),
        ({"text": "x", "entered": "typed", "tags": ["a b"]}, "tag"),
        ({"text": "x", "entered": "typed", "anchors": [{"kind": "record", "id": "nope"}]}, "nope"),
    ],
)
def test_a_malformed_note_is_refused_and_nothing_is_filed(
    bed: Bed, notes_path: str, token: str, document: dict, words: str
) -> None:
    status, refused = post(bed, notes_path, document, token)
    assert status == 400 and words in refused["error"]
    assert get(bed, notes_path, token)[1]["count"] == 0


def test_a_served_write_waits_for_the_cli_on_the_stores_write_lock(
    bed: Bed, notes_path: str, token: str
) -> None:
    """A second connection holding the write lock stands in for a kyo command in flight."""
    held = sqlite3.connect(bed.project / "kyosei" / "kyosei.db", isolation_level=None)
    held.execute("BEGIN IMMEDIATE")
    try:
        results: list[tuple[int, dict]] = []
        writer = threading.Thread(
            target=lambda: results.append(
                post(bed, notes_path, {"text": "x", "entered": "typed"}, token)
            )
        )
        writer.start()
        writer.join(0.5)
        assert writer.is_alive(), "the write should be waiting for the lock"
    finally:
        held.execute("ROLLBACK")
        held.close()
    writer.join(20)
    assert results[0][0] == 201, results


def test_a_write_that_loses_the_lock_is_refused_and_a_retry_clears_it(
    bed: Bed, notes_path: str, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_module, "BUSY_TIMEOUT_MS", 30)
    held = sqlite3.connect(bed.project / "kyosei" / "kyosei.db", isolation_level=None)
    held.execute("BEGIN IMMEDIATE")
    document = {"text": "x", "entered": "typed"}
    try:
        status, refused = post(bed, notes_path, document, token)
    finally:
        held.execute("ROLLBACK")
        held.close()
    assert status == 503 and refused["reason"] == "store-locked"
    assert "again" in refused["error"]
    assert post(bed, notes_path, document, token)[0] == 201


def test_the_cmd_route_stays_read_only_for_kyo_notes(bed: Bed, token: str) -> None:
    status, _ = get(bed, f"/v1/projects/{bed.project.name}/cmd/notes/list", token)
    assert status == 403


# ------------------------------------------------------------ c2: finding, reading, tagging


def test_notes_are_found_by_tag_anchor_and_session(
    bed: Bed, notes_path: str, token: str
) -> None:
    on_reply = file_note(bed, notes_path, token, tags=["alpha"], anchors=[span()])
    on_prompt = file_note(bed, notes_path, token, anchors=[span(PROMPT, 0, 5)])
    unanchored = file_note(bed, notes_path, token, tags=["alpha", "beta"])

    def ids(query: str) -> list[str]:
        status, found = get(bed, f"{notes_path}{query}", token)
        assert status == 200, found
        return [note["id"] for note in found["notes"]]

    assert ids("") == [on_reply["id"], on_prompt["id"], unanchored["id"]]
    assert ids("?tag=alpha") == [on_reply["id"], unanchored["id"]]
    assert ids("?tag=alpha&tag=beta") == [unanchored["id"]]
    assert ids(f"?session={OLD}") == [on_reply["id"], on_prompt["id"]]
    assert ids(f"?on={REPLY}") == [on_reply["id"]]
    assert ids("?anchored-to=output") == [on_reply["id"]]
    assert ids("?anchored-to=prompt") == [on_prompt["id"]]
    assert ids("?session=other") == []
    assert ids("?person=nobody") == []


def test_one_note_is_read_and_a_missing_one_is_refused(
    bed: Bed, notes_path: str, token: str
) -> None:
    note = file_note(bed, notes_path, token)
    status, read = get(bed, f"{notes_path}/{note['id']}", token)
    assert status == 200 and read["note"] == note
    status, refused = get(bed, f"{notes_path}/TST-N999", token)
    assert status == 404 and refused["reason"] == "not-found"
    assert "notes route" in refused["error"]
    assert get(bed, notes_path, token)[0] == 200


@pytest.mark.parametrize("path", ["/notes", "/notes/TST-N1", "/notes?tag=a"])
def test_reading_needs_a_token_with_notes_permission(
    bed: Bed, store_home: Path, path: str
) -> None:
    url = f"/v1/projects/{bed.project.name}{path}"
    assert get(bed, url, None)[0] == 401
    assert get(bed, url, issue("ipad", "send"))[0] == 403


@pytest.mark.parametrize(
    ("query", "words"),
    [
        ("?colour=red", "colour"),
        ("?on=a&on=b", "once"),
        ("?anchored-to=margin", "anchored-to"),
        ("?token=abc", "token"),
    ],
)
def test_a_bad_filter_is_refused(
    bed: Bed, notes_path: str, token: str, query: str, words: str
) -> None:
    status, refused = get(bed, f"{notes_path}{query}", token)
    assert status == 400 and words in refused["error"]


def test_tags_are_added_and_removed_each_recorded_and_the_text_stays(
    bed: Bed, notes_path: str, token: str
) -> None:
    note = file_note(bed, notes_path, token, tags=["old"])
    path = f"{notes_path}/{note['id']}/tags"
    status, changed = post(bed, path, {"add": ["#New"], "remove": ["old"]}, token)
    assert status == 200, changed
    assert changed["note"]["tags"] == ["new"]
    assert {(c["action"], c["tag"], c["author"]) for c in changed["changes"]} == {
        ("add", "new", "kyo-serve:ipad"),
        ("remove", "old", "kyo-serve:ipad"),
    }
    assert changed["note"]["text"] == note["text"]
    status, again = post(bed, path, {"add": ["new"]}, token)
    assert status == 200 and again["changes"] == [] and again["unchanged"] == ["new"]
    assert on_the_cli(bed, "show", note["id"])["note"]["tags"] == ["new"]


@pytest.mark.parametrize(
    "document",
    [{}, {"add": []}, {"add": ["a"], "remove": ["a"]}, {"add": "a"}, {"text": "new words"}],
)
def test_a_bad_tag_change_is_refused(
    bed: Bed, notes_path: str, token: str, document: dict
) -> None:
    note = file_note(bed, notes_path, token)
    status, _ = post(bed, f"{notes_path}/{note['id']}/tags", document, token)
    assert status == 400


def test_tagging_a_missing_note_is_refused(bed: Bed, notes_path: str, token: str) -> None:
    status, refused = post(bed, f"{notes_path}/TST-N999/tags", {"add": ["a"]}, token)
    assert status == 404 and refused["reason"] == "not-found"


def test_a_note_route_answers_only_its_methods(bed: Bed, notes_path: str, token: str) -> None:
    status, headers, _ = bed.client.request(
        f"{notes_path}/TST-N1", method="DELETE", headers={"Authorization": f"Bearer {token}"}
    )
    assert status == 405 and headers["Allow"] == "GET"
    status, _ = post(bed, f"{notes_path}/TST-N1/other", {}, token)
    assert status == 404


def test_only_listing_notes_takes_a_query_string(bed: Bed, notes_path: str, token: str) -> None:
    status, refused = get(bed, f"{notes_path}/TST-N1?x=1", token)
    assert status == 400 and "query string" in refused["error"]


# --------------------------------------------------------------- c3: sending a set of notes


@pytest.fixture
def can_send(bed: Bed, store_home: Path, token: str) -> str:
    enable(store_home)
    return token


def expected(first: dict, second: dict, third: dict) -> str:
    words = " ".join(reply_words().split()[:6])
    return (
        "[kyo-message, 3 notes]\n"
        "\n"
        f"Note 1 ({first['id']})\n"
        f'on your reply {REPLY} "{words}..."\n'
        "why not reformat it?\n"
        "and keep it short\n"
        "\n"
        f"Note 2 ({second['id']})\n"
        f"on note {first['id']}\n"
        f'on my prompt {PROMPT} "Write three paragraphs"\n'
        "second thoughts\n"
        "\n"
        f"Note 3 ({third['id']})\n"
        "no anchor"
    )


def three_notes(bed: Bed, notes_path: str, token: str) -> tuple[dict, dict, dict]:
    first = file_note(
        bed, notes_path, token, text="why not reformat it?\nand keep it short", anchors=[span()]
    )
    second = file_note(
        bed,
        notes_path,
        token,
        text="second thoughts",
        anchors=[{"kind": "note", "id": first["id"]}, span(PROMPT, 0, 22)],
    )
    third = file_note(bed, notes_path, token, text="no anchor")
    return first, second, third


def test_a_set_of_notes_becomes_one_message_in_the_fixed_form(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    first, second, third = three_notes(bed, notes_path, can_send)
    ids = [first["id"], second["id"], third["id"]]
    status, sent = post(bed, SEND_NOTES, {"notes": ids}, can_send)
    assert status == 200, sent
    assert sent["message"] == expected(first, second, third)
    assert sent["notes"] == ids and sent["held"]["notes"] == ids
    # A message during a turn is held, not sent, and no key has reached the pane.
    assert sent["action"] == "hold" and sent["held"]["state"] == "held"
    looks(bed)
    assert keys_sent(bed) == []


def test_a_note_carries_only_the_first_words_of_its_span(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    note = file_note(bed, notes_path, can_send, anchors=[span()])
    _, sent = post(bed, SEND_NOTES, {"notes": [note["id"]]}, can_send)
    text = reply_words()
    assert " ".join(text.split()[:6]) in sent["message"]
    assert " ".join(text.split()[:7]) not in sent["message"]
    assert sent["message"].startswith("[kyo-message, 1 note]\n")


def test_a_span_that_moved_says_its_words_are_unavailable(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    note = file_note(bed, notes_path, can_send, anchors=[span()])
    rows = [line.replace("lighthouse", "beacon") for line in bed.rows()]
    bed.write_transcript(rows)
    status, sent = post(bed, SEND_NOTES, {"notes": [note["id"]]}, can_send)
    assert status == 200, sent
    assert f"on your reply {REPLY} (words unavailable)" in sent["message"]


def test_a_note_entered_as_a_paste_is_sent_as_one(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    pasted = file_note(bed, notes_path, can_send, text="line\twith a tab", entered="pasted")
    typed = file_note(bed, notes_path, can_send, text="plain words")
    status, sent = post(bed, SEND_NOTES, {"notes": [pasted["id"], typed["id"]]}, can_send)
    assert status == 200, sent
    kinds = [segment["kind"] for segment in sent["segments"]]
    assert kinds == ["typed", "pasted", "typed"]
    assert "line\twith a tab" in sent["message"]


def test_a_note_with_a_tab_goes_as_a_paste_whatever_it_was_entered_as(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    note = file_note(bed, notes_path, can_send, text="a\ttab")
    status, sent = post(bed, SEND_NOTES, {"notes": [note["id"]]}, can_send)
    assert status == 200, sent
    assert [segment["kind"] for segment in sent["segments"]] == ["typed", "pasted"]


def test_a_note_with_a_control_character_is_refused_and_another_note_clears_it(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    bad = file_note(bed, notes_path, can_send, text="an escape \x1b here")
    status, refused = post(bed, SEND_NOTES, {"notes": [bad["id"]]}, can_send)
    assert status == 409 and refused["reason"] == "note-unsendable"
    assert "U+001B" in refused["error"] and "another note" in refused["error"]
    clean = file_note(bed, notes_path, can_send, text="an escape here")
    assert post(bed, SEND_NOTES, {"notes": [clean["id"]]}, can_send)[0] == 200


def test_the_delivered_message_reaches_the_pane_and_the_notes_are_filed_as_sent(
    bed: Bed, notes_path: str, can_send: str, store_home: Path
) -> None:
    first, second, third = three_notes(bed, notes_path, can_send)
    ids = [first["id"], second["id"], third["id"]]
    assert post(bed, SEND_NOTES, {"notes": ids}, can_send)[0] == 200
    looks(bed)
    for note_id in ids:
        assert get(bed, f"{notes_path}/{note_id}", can_send)[1]["note"]["tags"] == []
    bed.show(IDLE)

    def sent_tag() -> bool:
        found = get(bed, f"{notes_path}?tag={serve_notes.SENT_TAG}", can_send)[1]
        return found["count"] == 3

    wait_until(sent_tag)
    assert any("why not reformat it?" in key for key in keys_sent(bed))
    change = get(bed, f"{notes_path}/{ids[0]}", can_send)[1]["note"]["tag_changes"][0]
    assert change["tag"] == "sent" and change["author"] == "kyo-serve:ipad"
    rows = [row for row in log_rows(store_home) if row["action"] == "held-delivery"]
    assert [row["outcome"] for row in rows] == ["sent"]


def untouched(bed: Bed, notes_path: str, token: str, *note_ids: str) -> None:
    for note_id in note_ids:
        note = get(bed, f"{notes_path}/{note_id}", token)[1]["note"]
        assert note["tags"] == [] and note["tag_changes"] == []


def test_a_message_refused_because_sending_is_off_files_nothing_as_sent(
    bed: Bed, notes_path: str, token: str, store_home: Path
) -> None:
    note = file_note(bed, notes_path, token)
    status, refused = post(bed, SEND_NOTES, {"notes": [note["id"]]}, token)
    assert status == 403 and refused["reason"] == "send-disabled"
    untouched(bed, notes_path, token, note["id"])
    enable(store_home)
    assert post(bed, SEND_NOTES, {"notes": [note["id"]]}, token)[0] == 200


def test_a_token_that_may_not_send_files_nothing_as_sent_and_the_remedy_clears_it(
    bed: Bed, notes_path: str, store_home: Path
) -> None:
    enable(store_home)
    notes_only = issue("phone", "notes")
    note = file_note(bed, notes_path, notes_only)
    status, refused = post(bed, SEND_NOTES, {"notes": [note["id"]]}, notes_only)
    assert status == 403 and refused["reason"] == "no-send-permission"
    assert "--permission send" in refused["error"]
    untouched(bed, notes_path, notes_only, note["id"])
    assert invoke(["devices", "revoke", "phone"]).code == 0
    token = issue("phone", "notes", "send")
    assert post(bed, SEND_NOTES, {"notes": [note["id"]]}, token)[0] == 200


def test_a_second_set_held_while_one_is_waiting_files_nothing_as_sent(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    first = file_note(bed, notes_path, can_send)
    second = file_note(bed, notes_path, can_send)
    assert post(bed, SEND_NOTES, {"notes": [first["id"]]}, can_send)[0] == 200
    status, refused = post(bed, SEND_NOTES, {"notes": [second["id"]]}, can_send)
    assert status == 409 and refused["reason"] == "already-held"
    untouched(bed, notes_path, can_send, first["id"], second["id"])


def test_a_retracted_set_files_nothing_as_sent_and_gives_back_its_notes(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    note = file_note(bed, notes_path, can_send)
    _, sent = post(bed, SEND_NOTES, {"notes": [note["id"]]}, can_send)
    held = f"{SESSIONS}/{OLD}/held"
    status, _, body = bed.client.request(
        held,
        method="DELETE",
        headers={"Authorization": f"Bearer {can_send}", "If-Match": sent["held"]["id"]},
    )
    retracted = body_json(body)
    assert status == 200 and retracted["notes"] == [note["id"]]
    untouched(bed, notes_path, can_send, note["id"])


def test_a_missing_or_malformed_set_is_refused_and_nothing_is_held(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    note = file_note(bed, notes_path, can_send)
    for document in (
        {"notes": []},
        {"notes": [note["id"], note["id"]]},
        {"notes": "x"},
        {"notes": [note["id"]], "automatic": True},
    ):
        assert post(bed, SEND_NOTES, document, can_send)[0] == 400
    status, refused = post(bed, SEND_NOTES, {"notes": ["TST-N999"]}, can_send)
    assert status == 404 and refused["reason"] == "not-found"
    assert get(bed, f"{SESSIONS}/{OLD}/held", can_send)[1]["held"] is None


def test_send_notes_needs_the_notes_permission(
    bed: Bed, notes_path: str, can_send: str, store_home: Path
) -> None:
    note = file_note(bed, notes_path, can_send)
    status, refused = post(bed, SEND_NOTES, {"notes": [note["id"]]}, issue("laptop", "send"))
    assert status == 403 and refused["reason"] == "no-notes-permission"


def test_a_note_redacted_since_filing_is_refused_and_leaving_it_out_clears_it(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    gone = file_note(bed, notes_path, can_send, text="remove me")
    kept = file_note(bed, notes_path, can_send, text="keep me")
    connection = sqlite3.connect(bed.project / "kyosei" / "kyosei.db", isolation_level=None)
    connection.execute(
        "UPDATE person_notes SET text = '', redacted_at = 'now', redacted_why = 'test' "
        "WHERE id = ?",
        (gone["id"],),
    )
    connection.close()
    status, refused = post(bed, SEND_NOTES, {"notes": [gone["id"], kept["id"]]}, can_send)
    assert status == 409 and refused["reason"] == "note-redacted"
    assert "leave it out" in refused["error"]
    assert post(bed, SEND_NOTES, {"notes": [kept["id"]]}, can_send)[0] == 200


def test_a_sent_mark_that_cannot_be_written_is_logged_and_the_named_remedy_clears_it(
    bed: Bed, notes_path: str, can_send: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = file_note(bed, notes_path, can_send)

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(serve_notes.cmd_notes, "change_tags", locked)
    written = io.StringIO()
    monkeypatch.setattr(bed.server, "log_stream", written)
    assert post(bed, SEND_NOTES, {"notes": [note["id"]]}, can_send)[0] == 200
    bed.show(IDLE)
    remedy = f"`kyo notes tag {note['id']} --add sent`"
    wait_until(lambda: remedy in written.getvalue())
    monkeypatch.undo()
    result = invoke(["--root", str(bed.project), "notes", "tag", note["id"], "--add", "sent"])
    assert result.code == 0, result
    assert on_the_cli(bed, "show", note["id"])["note"]["tags"] == ["sent"]
