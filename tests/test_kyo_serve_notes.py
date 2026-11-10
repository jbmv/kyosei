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

from kyosei import commands, serve_notes
from kyosei import store as store_module
from kyosei.cmd_notes import mark_sent as cmd_notes_mark_sent
from kyosei.cmd_notes import span_digest as cmd_notes_span_digest

from .conftest import invoke, opt_in_transcripts
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
    unopted,
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
    _, held = post(bed, SEND_NOTES, {"notes": ids}, can_send)
    looks(bed)
    for note_id in ids:
        assert get(bed, f"{notes_path}/{note_id}", can_send)[1]["note"]["sent"] is None
    bed.show(IDLE)

    def all_sent() -> bool:
        return get(bed, f"{notes_path}?sent=true", can_send)[1]["count"] == 3

    wait_until(all_sent)
    assert any("why not reformat it?" in key for key in keys_sent(bed))
    note = get(bed, f"{notes_path}/{ids[0]}", can_send)[1]["note"]
    # KYO-709.c7: a field naming the session and the held message, and no tag.
    assert note["sent"]["session"] == OLD and note["sent"]["message"] == held["held"]["id"]
    assert note["sent"]["at"].endswith("Z")
    assert note["tags"] == [] and note["tag_changes"] == []
    assert get(bed, f"{notes_path}?sent=false", can_send)[1]["count"] == 0
    rows = [row for row in log_rows(store_home) if row["action"] == "held-delivery"]
    assert [row["outcome"] for row in rows] == ["sent"]


def untouched(bed: Bed, notes_path: str, token: str, *note_ids: str) -> None:
    for note_id in note_ids:
        note = get(bed, f"{notes_path}/{note_id}", token)[1]["note"]
        assert note["tags"] == [] and note["tag_changes"] == [] and note["sent"] is None


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


def test_a_sent_state_that_cannot_be_written_is_logged_and_written_at_the_next_start(
    bed: Bed, notes_path: str, can_send: str, monkeypatch: pytest.MonkeyPatch, store_home: Path
) -> None:
    note = file_note(bed, notes_path, can_send)

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(serve_notes.cmd_notes, "mark_sent", locked)
    written = io.StringIO()
    monkeypatch.setattr(bed.server, "log_stream", written)
    _, held = post(bed, SEND_NOTES, {"notes": [note["id"]]}, can_send)
    bed.show(IDLE)
    wait_until(lambda: "filed when kyo serve next starts" in written.getvalue())
    assert held["held"]["id"] in written.getvalue()
    unfiled = store_home / "composer" / "unfiled" / f"{held['held']['id']}.json"
    assert unfiled.is_file()
    # Delivered, and not held again.
    assert get(bed, f"{SESSIONS}/{OLD}/held", can_send)[1]["held"] is None
    monkeypatch.setattr(serve_notes.cmd_notes, "mark_sent", cmd_notes_mark_sent)
    bed.server.held_messages.resume()
    sent = get(bed, f"{notes_path}/{note['id']}", can_send)[1]["note"]["sent"]
    assert sent["message"] == held["held"]["id"] and sent["session"] == OLD
    assert not unfiled.exists()


def test_the_sent_state_is_not_a_field_a_route_can_set(
    bed: Bed, notes_path: str, token: str
) -> None:
    sent = {"at": "2026-11-09T12:00:00.000Z", "session": OLD, "message": "x"}
    status, refused = post(bed, notes_path, {"text": "x", "entered": "typed", "sent": sent}, token)
    assert status == 400 and "'sent'" in refused["error"]
    note = file_note(bed, notes_path, token)
    status, refused = post(bed, f"{notes_path}/{note['id']}/tags", {"sent": None}, token)
    assert status == 400 and "'sent'" in refused["error"]
    status, refused = get(bed, f"{notes_path}?sent=maybe", token)
    assert status == 400 and "?sent must be true or false" in refused["error"]


# ------------------------------------------------------- KYO-709: sending a marked prompt

SEND_PROMPT = f"{SESSIONS}/{OLD}/send-prompt"
HELD_ROUTE = f"{SESSIONS}/{OLD}/held"
#: The message id of the fixture's reply REPLY; its other record holds thinking only.
REPLY_MESSAGE = "msg_011CgsUrszPMRurWqUwuNvoD"

PROMPT_SEGMENTS = [
    {"kind": "typed", "text": "the transcript does not stream\nwhy not reformat?\n"},
    {"kind": "pasted", "text": "pasted line\nsecond pasted"},
    {"kind": "typed", "text": "\nlast words"},
]


def marked_prompt(first: dict) -> dict:
    return {
        "segments": PROMPT_SEGMENTS,
        "marks": [
            {"line": 1, "anchors": [span()], "tags": ["#Ask"]},
            {"line": 2, "anchors": [{"kind": "note", "id": first["id"]}]},
        ],
    }


def expected_prompt(first: dict) -> str:
    words = " ".join(reply_words().split()[:6])
    return (
        "the transcript does not stream\n"
        f'on your reply {REPLY} "{words}..."\n'
        "why not reformat?\n"
        f"on note {first['id']}\n"
        "pasted line\n"
        "second pasted\n"
        "last words"
    )


def filed_by(bed: Bed, notes_path: str, token: str, message_id: str) -> list[dict]:
    notes = get(bed, f"{notes_path}?sent=true", token)[1]["notes"]
    return [note for note in notes if note["sent"]["message"] == message_id]


def test_a_marked_prompt_is_its_text_with_each_marks_on_lines_above_its_line(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    first = file_note(bed, notes_path, can_send)
    status, sent = post(bed, SEND_PROMPT, marked_prompt(first), can_send)
    assert status == 200, sent
    assert sent["message"] == expected_prompt(first)
    assert "[kyo-message" not in sent["message"] and "Note 1" not in sent["message"]
    # The on lines are typed; the pasted segment stays a paste.
    held = get(bed, HELD_ROUTE, can_send)[1]["held"]
    assert [segment["kind"] for segment in held["segments"]] == ["typed", "pasted", "typed"]
    assert held["segments"][1]["text"] == "pasted line\nsecond pasted"
    assert sent["action"] == "hold" and sent["held"]["prompt"] == {"marks": 2}
    looks(bed)
    assert keys_sent(bed) == []


def test_a_prompt_without_marks_is_its_typed_text_exactly(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    status, sent = post(bed, SEND_PROMPT, {"segments": PROMPT_SEGMENTS}, can_send)
    assert status == 200, sent
    assert sent["message"] == "".join(segment["text"] for segment in PROMPT_SEGMENTS)
    held = get(bed, HELD_ROUTE, can_send)[1]["held"]
    assert held["segments"] == PROMPT_SEGMENTS
    assert held["prompt"] == {"segments": PROMPT_SEGMENTS, "marks": []}


@pytest.mark.parametrize(
    ("marks", "words"),
    [
        ([{"line": 0, "anchors": [{"kind": "record", "id": "TST-999"}]}], "marks[0].anchors[0]"),
        ([{"line": 0, "anchors": [{**span(), "end": 99999}]}], "marks[0].anchors[0]"),
        ([{"line": 5, "tags": ["x"]}], "marks[0]: line 5 is outside the text"),
        ([{"line": -1, "tags": ["x"]}], "marks[0]: line -1"),
        ([{"line": 0, "tags": ["x"]}, {"line": 0, "tags": ["y"]}], "marks[1] marks line 0"),
        ([{"line": 0}], "marks[0] holds no anchor and no tag"),
        ([{"line": "0", "tags": ["x"]}], "marks[0]"),
        ([{"line": 0, "tags": ["x"], "text": "copied"}], "marks[0] takes no 'text'"),
        ({"line": 0}, '"marks" must be a list'),
    ],
)
def test_a_bad_mark_is_refused_naming_it_and_nothing_is_held(
    bed: Bed, notes_path: str, can_send: str, marks: object, words: str
) -> None:
    document = {"segments": PROMPT_SEGMENTS, "marks": marks}
    status, refused = post(bed, SEND_PROMPT, document, can_send)
    assert status == 400 and refused["reason"] == "bad-request", refused
    assert words in refused["error"]
    assert get(bed, HELD_ROUTE, can_send)[1]["held"] is None
    assert post(bed, SEND_PROMPT, {"segments": PROMPT_SEGMENTS}, can_send)[0] == 200


def test_on_delivery_each_marked_line_is_filed_as_a_note_and_no_other(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    first = file_note(bed, notes_path, can_send)
    _, sent = post(bed, SEND_PROMPT, marked_prompt(first), can_send)
    message_id = sent["held"]["id"]
    looks(bed)
    assert filed_by(bed, notes_path, can_send, message_id) == [], "held, so not yet filed"
    bed.show(IDLE)
    wait_until(lambda: len(filed_by(bed, notes_path, can_send, message_id)) == 2)
    asked, pasted = filed_by(bed, notes_path, can_send, message_id)
    assert asked["text"] == "why not reformat?" and asked["entered"] == "typed"
    assert asked["tags"] == ["ask"] and asked["device"] == "ipad"
    assert [a["target"] for a in asked["anchors"]] == [REPLY]
    assert asked["author"] == "kyo-serve:ipad"
    assert pasted["text"] == "pasted line" and pasted["entered"] == "pasted"
    assert pasted["anchors"] == [{"kind": "note", "target": first["id"]}]
    assert asked["sent"]["session"] == OLD and asked["sent"]["message"] == message_id
    # One note per marked line: the three unmarked lines filed nothing.
    assert get(bed, notes_path, can_send)[1]["count"] == 3
    assert any("why not reformat?" in key for key in keys_sent(bed))


def test_a_retracted_prompt_files_nothing_and_gives_back_its_segments_and_marks(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    first = file_note(bed, notes_path, can_send)
    document = marked_prompt(first)
    _, sent = post(bed, SEND_PROMPT, document, can_send)
    held = get(bed, HELD_ROUTE, can_send)[1]["held"]
    assert held["prompt"] == {"segments": PROMPT_SEGMENTS, "marks": document["marks"]}
    status, _, body = bed.client.request(
        HELD_ROUTE,
        method="DELETE",
        headers={"Authorization": f"Bearer {can_send}", "If-Match": sent["held"]["id"]},
    )
    retracted = body_json(body)
    assert status == 200, retracted
    assert retracted["prompt"] == {"segments": PROMPT_SEGMENTS, "marks": document["marks"]}
    # Nothing is left to deliver, so nothing reads the pane or files a note.
    bed.show(IDLE)
    bed.server.held_messages.attend(OLD)
    assert get(bed, notes_path, can_send)[1]["count"] == 1
    assert keys_sent(bed) == []


def test_an_empty_marked_line_is_sent_as_its_on_lines_alone_and_filed(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    first = file_note(bed, notes_path, can_send)
    document = {
        "segments": [{"kind": "typed", "text": "first\n\nlast"}],
        "marks": [{"line": 1, "anchors": [{"kind": "note", "id": first["id"]}]}],
    }
    status, sent = post(bed, SEND_PROMPT, document, can_send)
    assert status == 200, sent
    assert sent["message"] == f"first\non note {first['id']}\nlast"
    bed.show(IDLE)
    message_id = sent["held"]["id"]
    wait_until(lambda: len(filed_by(bed, notes_path, can_send, message_id)) == 1)
    (note,) = filed_by(bed, notes_path, can_send, message_id)
    assert note["text"] == "" and note["anchors"] == [{"kind": "note", "target": first["id"]}]


def test_a_mark_by_reply_message_is_stored_by_record_and_reads_the_right_words(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    anchor = {"kind": "turn", "session": OLD, "message": REPLY_MESSAGE, "start": 0, "end": 200}
    document = {
        "segments": [{"kind": "typed", "text": "about this"}],
        "marks": [{"line": 0, "anchors": [anchor]}],
    }
    status, sent = post(bed, SEND_PROMPT, document, can_send)
    assert status == 200, sent
    words = " ".join(reply_words().split()[:6])
    assert sent["message"] == f'on your reply {REPLY} "{words}..."\nabout this'
    bed.show(IDLE)
    message_id = sent["held"]["id"]
    wait_until(lambda: len(filed_by(bed, notes_path, can_send, message_id)) == 1)
    (note,) = filed_by(bed, notes_path, can_send, message_id)
    (stored,) = note["anchors"]
    assert stored["target"] == REPLY and stored["char_end"] == 200
    past = {**anchor, "end": len(reply_words()) + 3}
    status, refused = post(
        bed, SEND_PROMPT, {**document, "marks": [{"line": 0, "anchors": [past]}]}, can_send
    )
    assert status == 400 and "past the reply's" in refused["error"]


def test_a_prompt_whose_notes_cannot_be_filed_is_kept_delivered_and_filed_at_the_next_start(
    bed: Bed,
    notes_path: str,
    can_send: str,
    monkeypatch: pytest.MonkeyPatch,
    store_home: Path,
) -> None:
    first = file_note(bed, notes_path, can_send)
    real = serve_notes.cmd_notes.file_notes

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(serve_notes.cmd_notes, "file_notes", locked)
    written = io.StringIO()
    monkeypatch.setattr(bed.server, "log_stream", written)
    _, sent = post(bed, SEND_PROMPT, marked_prompt(first), can_send)
    message_id = sent["held"]["id"]
    bed.show(IDLE)
    wait_until(lambda: "filed when kyo serve next starts" in written.getvalue())
    assert message_id in written.getvalue() and "database is locked" in written.getvalue()
    unfiled = store_home / "composer" / "unfiled" / f"{message_id}.json"
    assert unfiled.is_file()
    assert get(bed, HELD_ROUTE, can_send)[1]["held"] is None, "delivered, not held again"
    assert keys_sent(bed) and filed_by(bed, notes_path, can_send, message_id) == []

    # The next start tries again; this time the store takes them.
    monkeypatch.setattr(serve_notes.cmd_notes, "file_notes", real)
    bed.server.held_messages.resume()
    assert len(filed_by(bed, notes_path, can_send, message_id)) == 2
    assert not unfiled.exists()
    bed.server.held_messages.resume()
    assert get(bed, notes_path, can_send)[1]["count"] == 3, "filed once"


def test_send_prompt_needs_the_send_permission_and_the_remedy_clears_it(
    bed: Bed, notes_path: str, store_home: Path
) -> None:
    enable(store_home)
    notes_only = issue("phone", "notes")
    status, refused = post(bed, SEND_PROMPT, {"segments": PROMPT_SEGMENTS}, notes_only)
    assert status == 403 and refused["reason"] == "no-send-permission"
    assert "--permission send" in refused["error"]
    assert get(bed, HELD_ROUTE, issue("laptop", "send"))[1]["held"] is None
    assert invoke(["devices", "revoke", "phone"]).code == 0
    token = issue("phone", "notes", "send")
    assert post(bed, SEND_PROMPT, {"segments": PROMPT_SEGMENTS}, token)[0] == 200


def test_a_marked_prompt_needs_the_notes_permission_and_the_remedy_clears_it(
    bed: Bed, notes_path: str, store_home: Path
) -> None:
    enable(store_home)
    send_only = issue("laptop", "send")
    document = {"segments": PROMPT_SEGMENTS, "marks": [{"line": 0, "tags": ["x"]}]}
    status, refused = post(bed, SEND_PROMPT, document, send_only)
    assert status == 403 and refused["reason"] == "no-notes-permission"
    assert "--permission notes" in refused["error"]
    assert get(bed, HELD_ROUTE, send_only)[1]["held"] is None
    # Without marks the same token may send.
    status, sent = post(bed, SEND_PROMPT, {"segments": PROMPT_SEGMENTS}, send_only)
    assert status == 200, sent
    _, _, _ = bed.client.request(
        HELD_ROUTE,
        method="DELETE",
        headers={"Authorization": f"Bearer {send_only}", "If-Match": sent["held"]["id"]},
    )
    assert invoke(["devices", "revoke", "laptop"]).code == 0
    assert post(bed, SEND_PROMPT, document, issue("laptop", "send", "notes"))[0] == 200


def test_send_prompt_refuses_a_key_it_does_not_take(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    for document in (
        {"segments": PROMPT_SEGMENTS, "automatic": True},
        {"segments": PROMPT_SEGMENTS, "notes": []},
    ):
        status, refused = post(bed, SEND_PROMPT, document, can_send)
        assert status == 400 and "does not take" in refused["error"]
    status, refused = post(bed, SEND_PROMPT, {"segments": []}, can_send)
    assert status == 400 and '"segments"' in refused["error"]


# ------------------------------------------- KYO-710: a session's marks, and the tags in use

MARKS = f"{SESSIONS}/{OLD}/marks"
OTHER_SESSION = "0d1e2f3a-4b5c-4d6e-8f70-819203a4b5c6"
#: KYO-710.c2: a reply written as two records, the first holding two text blocks.
SPLIT_MESSAGE = "msg_kyo710_split_reply"
SPLIT_FIRST = "71071071-0000-4000-8000-000000000001"
SPLIT_SECOND = "71071071-0000-4000-8000-000000000002"
#: A record the transcript never held.
MISSING_RECORD = "71071071-dead-4000-8000-0000000000ff"
#: The fixture transcript's last record, which the split reply follows.
LAST_RECORD = "14879f01-da16-4f48-94a9-efb05b15b88f"


def tags_path(bed: Bed) -> str:
    return f"/v1/projects/{bed.project.name}/tags"


def given_turn(session: str, uuid: str, role: str = "agent") -> dict:
    """A turn anchor kyo cannot read, so filed on the caller's word (``digest_check: given``)."""
    digest = cmd_notes_span_digest("words nobody has", 0, 5)
    return {**span(uuid, 0, 5), "session": session, "digest": digest, "role": role}


def sent_on_the_store(bed: Bed, note_ids: list[str], message: str = "held-test") -> None:
    """What `kyo serve` does when a message holding these notes is delivered."""
    ctx = commands.build_context(
        str(bed.project),
        "test",
        dict(bed.server.environ),
        ["notes"],
        project=commands.PROJECT_REQUIRED,
        read_only=False,
    )
    try:
        cmd_notes_mark_sent(
            ctx, note_ids, at="2026-11-09T12:00:00.000Z", session=OLD, message=message
        )
    finally:
        ctx.close()


def append_split_reply(bed: Bed) -> str:
    """The split reply, appended to the session; returns its text as the session routes do."""

    def record(uuid: str, parent: str, stamp: str, blocks: list[str]) -> str:
        row = {
            "type": "assistant",
            "uuid": uuid,
            "parentUuid": parent,
            "isSidechain": False,
            "timestamp": stamp,
            "sessionId": OLD,
            "message": {
                "id": SPLIT_MESSAGE,
                "role": "assistant",
                "content": [{"type": "text", "text": text} for text in blocks],
            },
        }
        return json.dumps(row) + "\n"

    bed.append(
        [
            record(SPLIT_FIRST, LAST_RECORD, "2026-11-09T14:11:00.000Z", ["alpha beta", "gamma"]),
            record(SPLIT_SECOND, SPLIT_FIRST, "2026-11-09T14:11:01.000Z", ["delta epsilon"]),
        ]
    )
    status, turns = get(bed, f"{SESSIONS}/{OLD}/turns", None)
    assert status == 200, turns
    (reply,) = [r for r in turns["replies"] if r["message_id"] == SPLIT_MESSAGE]
    return reply["text"]


def by_message(text: str, words: str) -> dict:
    start = text.index(words)
    return {
        "kind": "turn",
        "session": OLD,
        "message": SPLIT_MESSAGE,
        "start": start,
        "end": start + len(words),
    }


def marks_of(bed: Bed, token: str) -> list[dict]:
    status, document = get(bed, MARKS, token)
    assert status == 200, document
    assert document["count"] == len(document["marks"]) and document["session"] == OLD
    return document["marks"]


def test_marks_are_the_sent_notes_on_turns_of_the_session_oldest_first(
    bed: Bed, notes_path: str, token: str
) -> None:
    first = file_note(bed, notes_path, token, text="on the prompt", anchors=[span(PROMPT, 0, 5)])
    unsent = file_note(bed, notes_path, token, text="never sent", anchors=[span()])
    no_turn = file_note(bed, notes_path, token, text="on a note", anchors=[])
    mixed = file_note(
        bed,
        notes_path,
        token,
        text="on the reply",
        tags=["ask"],
        anchors=[
            {"kind": "note", "id": first["id"]},
            span(),
            given_turn(OTHER_SESSION, REPLY),
        ],
    )
    elsewhere = file_note(bed, notes_path, token, anchors=[given_turn(OTHER_SESSION, REPLY)])
    sent_on_the_store(bed, [mixed["id"], first["id"], no_turn["id"], elsewhere["id"]])
    marks = marks_of(bed, token)
    assert [mark["note"] for mark in marks] == [first["id"], mixed["id"]]
    assert unsent["id"] not in {mark["note"] for mark in marks}
    on_prompt, on_reply = marks
    assert on_prompt["text"] == "on the prompt" and on_prompt["tags"] == []
    assert on_prompt["sent"] == {
        "at": "2026-11-09T12:00:00.000Z",
        "session": OLD,
        "message": "held-test",
    }
    assert set(on_reply) == {"note", "text", "tags", "sent", "anchors"}
    assert on_reply["tags"] == ["ask"]
    # The note anchor and the other session's turn are left out.
    assert on_reply["anchors"] == [
        {"role": "agent", "message": REPLY_MESSAGE, "start": 0, "end": 200}
    ]


def test_a_redacted_notes_text_is_left_out_of_its_marks(
    bed: Bed, notes_path: str, token: str
) -> None:
    note = file_note(bed, notes_path, token, text="remove me", anchors=[span()])
    sent_on_the_store(bed, [note["id"]])
    connection = sqlite3.connect(bed.project / "kyosei" / "kyosei.db", isolation_level=None)
    connection.execute(
        "UPDATE person_notes SET text = '', redacted_at = 'now', redacted_why = 'test' "
        "WHERE id = ?",
        (note["id"],),
    )
    connection.close()
    (mark,) = marks_of(bed, token)
    assert mark["text"] is None and mark["anchors"][0]["message"] == REPLY_MESSAGE


def test_a_span_stored_across_two_records_comes_back_as_one_anchor_on_the_reply(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    text = append_split_reply(bed)
    assert text == "alpha beta\n\ngamma\n\ndelta epsilon"
    document = {
        "segments": [{"kind": "typed", "text": "across\nwithin"}],
        "marks": [
            {"line": 0, "anchors": [by_message(text, "gamma\n\ndelta")]},
            {"line": 1, "anchors": [by_message(text, "beta\n\ngamma")]},
        ],
    }
    status, sent = post(bed, SEND_PROMPT, document, can_send)
    assert status == 200, sent
    bed.show(IDLE)
    message_id = sent["held"]["id"]
    wait_until(lambda: len(filed_by(bed, notes_path, can_send, message_id)) == 2)
    across, within = filed_by(bed, notes_path, can_send, message_id)
    # Stored one anchor per record, as KYO-709 does.
    assert [a["target"] for a in across["anchors"]] == [SPLIT_FIRST, SPLIT_SECOND]
    marks = {mark["note"]: mark for mark in marks_of(bed, can_send)}
    (joined,) = marks[across["id"]]["anchors"]
    assert joined["role"] == "agent" and joined["message"] == SPLIT_MESSAGE
    assert text[joined["start"] : joined["end"]] == "gamma\n\ndelta"
    (one,) = marks[within["id"]]["anchors"]
    assert text[one["start"] : one["end"]] == "beta\n\ngamma"


def test_two_anchors_a_note_was_given_apart_stay_apart(
    bed: Bed, notes_path: str, token: str
) -> None:
    text = append_split_reply(bed)
    note = file_note(
        bed,
        notes_path,
        token,
        anchors=[by_message(text, "alpha"), by_message(text, "epsilon")],
    )
    sent_on_the_store(bed, [note["id"]])
    (mark,) = marks_of(bed, token)
    assert [text[a["start"] : a["end"]] for a in mark["anchors"]] == ["alpha", "epsilon"]


def test_a_prompt_anchor_has_its_uuid_and_a_moved_or_missing_record_is_stale(
    bed: Bed, notes_path: str, token: str
) -> None:
    note = file_note(
        bed,
        notes_path,
        token,
        anchors=[span(PROMPT, 0, 22), span(), given_turn(OLD, MISSING_RECORD)],
    )
    sent_on_the_store(bed, [note["id"]])
    (mark,) = marks_of(bed, token)
    prompt, reply, missing = mark["anchors"]
    assert prompt == {"role": "person", "uuid": PROMPT, "start": 0, "end": 22}
    assert reply["message"] == REPLY_MESSAGE
    assert missing == {"role": "agent", "uuid": MISSING_RECORD, "stale": True}
    # The reply's words change, so its digest no longer matches; the prompt's stay.
    bed.write_transcript(
        [
            line.replace("lighthouse", "beacon") if REPLY in line else line
            for line in bed.rows()
        ]
    )
    (mark,) = marks_of(bed, token)
    assert mark["anchors"][1] == {"role": "agent", "uuid": REPLY, "stale": True}
    assert "start" not in mark["anchors"][1] and mark["anchors"][0]["start"] == 0


def test_tags_in_use_are_counted_most_used_first_then_by_name(
    bed: Bed, notes_path: str, token: str
) -> None:
    file_note(bed, notes_path, token, tags=["beta", "alpha"])
    file_note(bed, notes_path, token, tags=["beta", "gamma"])
    gone = file_note(bed, notes_path, token, tags=["delta"])
    status, listed = get(bed, tags_path(bed), token)
    assert status == 200, listed
    assert listed["tags"] == [
        {"tag": "beta", "count": 2},
        {"tag": "alpha", "count": 1},
        {"tag": "delta", "count": 1},
        {"tag": "gamma", "count": 1},
    ]
    assert listed["count"] == 4
    post(bed, f"{notes_path}/{gone['id']}/tags", {"remove": ["delta"]}, token)
    listed = get(bed, tags_path(bed), token)[1]
    assert "delta" not in {row["tag"] for row in listed["tags"]}
    # The CLI prints the same list.
    assert on_the_cli(bed, "tags")["tags"] == listed["tags"]


@pytest.mark.parametrize("route", ["marks", "tags"])
def test_marks_and_tags_need_the_notes_permission_and_the_remedy_clears_it(
    bed: Bed, store_home: Path, route: str
) -> None:
    path = MARKS if route == "marks" else tags_path(bed)
    status, refused = get(bed, path, None)
    assert status == 401 and refused["reason"] == "no-token"
    assert "kyo devices issue <name> --permission notes" in refused["error"]
    status, refused = get(bed, path, issue("ipad", "send"))
    assert status == 403 and refused["reason"] == "no-notes-permission"
    assert "--permission notes" in refused["error"]
    assert invoke(["devices", "revoke", "ipad"]).code == 0
    assert get(bed, path, issue("ipad", "notes"))[0] == 200


def test_marks_and_tags_take_no_query_and_answer_get_only(bed: Bed, token: str) -> None:
    for path in (MARKS, tags_path(bed)):
        status, refused = get(bed, f"{path}?x=1", token)
        assert status == 400 and "query string" in refused["error"]
        status, headers, _ = bed.client.request(
            path, method="DELETE", headers={"Authorization": f"Bearer {token}"}
        )
        assert status == 405 and headers["Allow"] == "GET"


def test_marks_refuse_until_the_project_opts_in_and_opting_in_clears_it(
    unopted, project: Path, store_home: Path
) -> None:
    token = issue("ipad", "notes")
    path = f"/v1/projects/{project.name}/sessions/{OLD}/marks"
    status, _, body = unopted.request(path, headers={"Authorization": f"Bearer {token}"})
    refused = body_json(body)
    assert status == 403 and "read_transcripts: true" in refused["error"]
    opt_in_transcripts(project)
    status, _, body = unopted.request(path, headers={"Authorization": f"Bearer {token}"})
    assert status == 200 and body_json(body)["marks"] == []
