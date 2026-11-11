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
import subprocess
import threading
from pathlib import Path

import pytest

from kyosei import cmd_notes, cmd_sessions, commands, serve, serve_notes
from kyosei import store as store_module
from kyosei.cmd_notes import mark_sent as cmd_notes_mark_sent
from kyosei.cmd_notes import span_digest as cmd_notes_span_digest

from .conftest import git_init, invoke, new_record, opt_in_transcripts
from .test_kyo_serve import body_json, register
from .test_kyo_serve_held import looks, wait_until
from .test_kyo_serve_send import FRAME_WORKING, IDLE, enable, issue, keys_sent, log_rows, post
from .test_kyo_serve_sessions import (  # noqa: F401 - the fixtures, by name
    ENDED,
    OLD,
    OLD_FILE,
    SESSIONS,
    Bed,
    bed,
    json_body,
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
    assert "`kyo devices grant ipad --permission notes`" in refused["error"]
    # The remedy the refusal names (KYO-747): grant, and the same token is then taken.
    assert invoke(["devices", "grant", "ipad", "--permission", "notes"]).code == 0
    assert post(bed, notes_path, document, send_only)[0] == 201


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
        f'on my prompt {PROMPT} "Write three paragraphs, roughly 120 words each, about how '
        f'lighthouses..." at "Write three paragraphs"\n'
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
        ([{"line": 0}], "marks[0] holds no anchor, no tag and no attachment"),
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


def test_an_anchor_made_in_a_session_that_has_ended_is_sent_as_its_line_of_words(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    # KYO-763: a prompt written in one session and sent in the next keeps its anchors. The
    # line names the session the words are in, since the receiving session never held them.
    (bed.transcripts / f"{ENDED}.jsonl").write_text("".join(bed.rows()), encoding="utf-8")
    anchor = {"kind": "turn", "session": ENDED, "message": REPLY_MESSAGE, "start": 0, "end": 200}
    document = {
        "segments": [{"kind": "typed", "text": "about this"}],
        "marks": [{"line": 0, "anchors": [anchor]}],
    }
    status, sent = post(bed, SEND_PROMPT, document, can_send)
    assert status == 200, sent
    words = " ".join(reply_words().split()[:6])
    assert sent["message"] == (
        f'on your reply {REPLY} in session {ENDED} "{words}..."\nabout this'
    )
    bed.show(IDLE)
    message_id = sent["held"]["id"]
    wait_until(lambda: len(filed_by(bed, notes_path, can_send, message_id)) == 1)
    (note,) = filed_by(bed, notes_path, can_send, message_id)
    (stored,) = note["anchors"]
    assert stored["session"] == ENDED and stored["target"] == REPLY


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
    turns = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))
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


# ------------------------------------------------- KYO-733: a short selection's line


def _line_for(text: str, selected: str, role: str = "agent") -> str:
    """The line `_anchor_line` writes for `selected` within one record whose text is `text`."""
    start = text.index(selected)
    end = start + len(selected)
    uuid = "11111111-1111-4111-8111-111111111111"
    record = {"message": {"content": text}}
    anchor = {
        "kind": "turn",
        "role": role,
        "session": OLD,
        "target": uuid,
        "char_start": start,
        "char_end": end,
        "digest": cmd_notes_span_digest(text, start, end),
    }
    return serve_notes._anchor_line(None, anchor, {uuid: record}, OLD).replace(uuid, "<uuid>")


PARAGRAPH = (
    "Intro line here.\n"
    "The count was blank because this repository's store was never opened by the reader. "
    "Then the second sentence ends the paragraph."
)


def test_c1_a_short_selection_quotes_its_sentence_from_the_start_and_names_the_selection() -> None:
    assert _line_for(PARAGRAPH, "blank") == (
        'on your reply <uuid> "The count was blank because this repository\'s store was never..."'
        ' at "blank"'
    )
    # Five words is still short; six is quoted as it always was.
    assert _line_for(PARAGRAPH, "count was blank because this").endswith(
        'at "count was blank because this"'
    )
    six = _line_for(PARAGRAPH, "count was blank because this repository's")
    assert six == 'on your reply <uuid> "count was blank because this repository\'s"'
    # A selection that is the whole sentence is the quote, and is not named again.
    assert _line_for(PARAGRAPH, "Intro line here.") == 'on your reply <uuid> "Intro line here."'
    # A sentence of ten words or fewer is quoted whole, with no ellipsis.
    assert _line_for(PARAGRAPH, "Intro") == 'on your reply <uuid> "Intro line here." at "Intro"'


def test_c2_the_sentence_is_bounded_by_a_stop_a_line_break_or_the_end_of_the_block() -> None:
    # The last sentence of a paragraph: it starts after the earlier full stop, not the block.
    assert _line_for(PARAGRAPH, "paragraph") == (
        'on your reply <uuid> "Then the second sentence ends the paragraph." at "paragraph"'
    )
    # A list item is its own line, and its marker is not part of the quote.
    listed = "Before.\n- first item reads like this. and more\n- second item ends"
    assert _line_for(listed, "second") == (
        'on your reply <uuid> "second item ends" at "second" (list item)'
    )
    assert _line_for(listed, "first") == (
        'on your reply <uuid> "first item reads like this." at "first" (list item)'
    )
    numbered = "1. numbered entry text"
    assert _line_for(numbered, "entry") == (
        'on your reply <uuid> "numbered entry text" at "entry" (list item)'
    )
    # A full stop inside a word ends nothing.
    assert _line_for("See serve_notes.py now", "now") == (
        'on your reply <uuid> "See serve_notes.py now" at "now"'
    )


def test_c3_a_heading_a_list_item_a_cell_and_code_are_named_and_a_paragraph_is_not() -> None:
    text = (
        "# The Install\n"
        "Plain paragraph words.\n"
        "| name | value |\n"
        "| alpha one | beta two |\n"
        "```py\n"
        "x = compute(1)\n"
        "```\n"
        "After the fence."
    )
    assert _line_for(text, "Install") == 'on your reply <uuid> "The Install" at "Install" (heading)'
    assert _line_for(text, "Plain") == 'on your reply <uuid> "Plain paragraph words." at "Plain"'
    assert _line_for(text, "beta") == 'on your reply <uuid> "beta two" at "beta" (table cell)'
    assert _line_for(text, "alpha") == 'on your reply <uuid> "alpha one" at "alpha" (table cell)'
    assert _line_for(text, "compute") == (
        'on your reply <uuid> "x = compute(1)" at "compute" (code block)'
    )
    assert _line_for(text, "After") == 'on your reply <uuid> "After the fence." at "After"'
    # A long selection is quoted as before and still names its block.
    assert _line_for("# One two three four five six seven", "One two three four five six") == (
        'on your reply <uuid> "One two three four five six" (heading)'
    )


def test_c4_an_anchor_sent_with_the_longer_line_is_stored_and_read_back_unchanged(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    text = reply_words()
    word = text.split()[2]
    start = text.index(word)
    anchor = {**span(start=start, end=start + len(word))}
    document = {
        "segments": [{"kind": "typed", "text": "about this"}],
        "marks": [{"line": 0, "anchors": [anchor]}],
    }
    status, sent = post(bed, SEND_PROMPT, document, can_send)
    assert status == 200, sent
    first_line = sent["message"].splitlines()[0]
    assert first_line.startswith(f'on your reply {REPLY} "')
    assert first_line.endswith(f' at "{word}"')
    bed.show(IDLE)
    message_id = sent["held"]["id"]
    wait_until(lambda: len(filed_by(bed, notes_path, can_send, message_id)) == 1)
    (note,) = filed_by(bed, notes_path, can_send, message_id)
    (stored,) = note["anchors"]
    assert (stored["char_start"], stored["char_end"]) == (start, start + len(word))
    assert cmd_notes.span_text(
        None, {**stored, "kind": "turn"}, {REPLY: _fixture_record()}
    ) == word


def _fixture_record() -> dict:
    for line in OLD_FILE.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("uuid") == REPLY:
            return row
    raise AssertionError("the fixture's reply is gone")


# ------------------------------------- KYO-737: anchors to a record and to a served file


def kyo_of(bed: Bed):
    """The CLI against the bed's project, as the ``kyo`` fixture runs it."""

    def run(*args: str, stdin: str | None = None):
        return invoke(["--root", str(bed.project), *args], stdin=stdin)

    run.project = bed.project
    return run


def one_mark(*anchors: dict, text: str = "about this") -> dict:
    return {
        "segments": [{"kind": "typed", "text": text}],
        "marks": [{"line": 0, "anchors": list(anchors)}],
    }


def on_lines(sent: dict) -> list[str]:
    return [line for line in sent["message"].splitlines() if line.startswith("on ")]


@pytest.fixture
def with_files(bed: Bed, can_send: str, store_home: Path) -> str:
    """A token that may also read files, as the app's does."""
    return issue("mac", "notes", "send", "files")


@pytest.fixture
def elements(bed: Bed) -> dict[str, str]:
    """A record with one of each element a prompt can cite, by the element's kind."""
    kyo = kyo_of(bed)
    record = new_record(
        kyo,
        title="Widgets explode when the cache is cold",
        criteria=["widgets stop exploding in every build we ship from now on"],
    )
    assert kyo("gate", record, "holding-for", "--do", "the iPad build is checked").code == 0
    noted = kyo("edit", record, "--note-file", "-", stdin="seen again on the laptop\n")
    assert noted.code == 0, noted
    claimed = kyo(
        "claim", record, "--level", "asserted", "--method", "read the code", "--file", "-",
        stdin="the cache eviction races the write",
    )  # fmt: skip
    assert claimed.code == 0, claimed
    git_init(bed.project)
    dispatched = kyo("dispatch", record, "--task-file", "-", stdin="Fix the race.")
    assert dispatched.code == 0, dispatched
    shown = kyo("show", record, "--json").json
    return {
        "record": record,
        "criterion": shown["criteria"][0]["id"],
        "gate": shown["gates"][0]["id"],
        "note": shown["notes"][0]["id"],
        "claim": shown["claims"][0]["id"],
        "dispatch": shown["dispatches"][0]["id"],
    }


def test_c1_a_paragraph_is_anchored_to_a_record_and_to_each_element_of_one(
    bed: Bed, notes_path: str, can_send: str, elements: dict[str, str]
) -> None:
    order = ("record", "criterion", "gate", "dispatch", "note", "claim")
    anchors = [{"kind": "record", "id": elements[kind]} for kind in order]
    status, sent = post(bed, SEND_PROMPT, one_mark(*anchors), can_send)
    assert status == 200, sent
    assert sent["message"].splitlines() == [
        f'on record {elements["record"]} "Widgets explode when the cache is..."',
        f'on record {elements["criterion"]} "widgets stop exploding in every build..."',
        f'on record {elements["gate"]} "the iPad build is checked"',
        # A dispatch has no words of its own; `kyo show` reads its id.
        f"on record {elements['dispatch']}",
        f'on record {elements["note"]} "seen again on the laptop"',
        f'on record {elements["claim"]} "the cache eviction races the write"',
        "about this",
    ]
    bed.show(IDLE)
    wait_until(lambda: len(filed_by(bed, notes_path, can_send, sent["held"]["id"])) == 1)
    (note,) = filed_by(bed, notes_path, can_send, sent["held"]["id"])
    # Stored as the existing kind, by the element's own id, with no new key.
    assert note["anchors"] == [
        {"kind": "record", "target": elements[kind]} for kind in order
    ]
    # What takes an agent to the place: `kyo show` reads a record and a dispatch by their own
    # ids, and prints every other element under the id of the record that holds it.
    kyo = kyo_of(bed)
    assert kyo("show", elements["dispatch"]).code == 0
    shown = kyo("show", elements["record"])
    assert shown.code == 0 and all(elements[kind] in shown.out for kind in order)
    # A note on an element is found from its record.
    found = on_the_cli(bed, "list", "--on", elements["record"])
    assert [item["id"] for item in found["notes"]] == [note["id"]]


@pytest.mark.parametrize("letter", ["c", "g", "d", "n", "m"])
def test_c1_an_id_the_store_does_not_hold_is_refused_and_a_listed_id_clears_it(
    bed: Bed, can_send: str, elements: dict[str, str], letter: str
) -> None:
    record = elements["record"]
    missing = f"{record}.{letter}99"
    status, refused = post(bed, SEND_PROMPT, one_mark({"kind": "record", "id": missing}), can_send)
    assert status == 400, refused
    assert f"{missing!r} in this store" in refused["error"]
    assert f"`kyo show {record}` prints the ids that record holds" in refused["error"]
    assert get(bed, HELD_ROUTE, can_send)[1]["held"] is None
    # The remedy: an id that command prints.
    shown = kyo_of(bed)("show", record)
    listed = next(
        word.strip(".,:()|") for word in shown.out.split() if f"{record}.{letter}" in word
    )
    status, sent = post(bed, SEND_PROMPT, one_mark({"kind": "record", "id": listed}), can_send)
    assert status == 200, sent
    assert on_lines(sent)[0].startswith(f"on record {listed}")


def test_c1_a_record_the_store_does_not_hold_names_the_list_and_other_ids_are_refused(
    bed: Bed, can_send: str, elements: dict[str, str]
) -> None:
    missing = one_mark({"kind": "record", "id": "TST-999"})
    status, refused = post(bed, SEND_PROMPT, missing, can_send)
    assert status == 400 and "`kyo list` prints the ids of the records" in refused["error"]
    listed = kyo_of(bed)("list")
    assert elements["record"] in listed.out
    # A report is not an element a prompt anchors to, and the refusal says which are.
    status, refused = post(
        bed, SEND_PROMPT, one_mark({"kind": "record", "id": f"{elements['record']}.r1"}), can_send
    )
    assert status == 400 and "criterion, gate, dispatch, note or claim" in refused["error"]


# ---------------------------------------------- KYO-757: anchors to an entry of a report


@pytest.fixture
def reported(bed: Bed, elements: dict[str, str], tmp_path: Path) -> dict[str, str]:
    """A report on the record's dispatch with one of each entry that has an id."""
    kyo = kyo_of(bed)
    record, dispatch = elements["record"], elements["dispatch"]
    body = {
        "dispatch_id": dispatch,
        "record_id": record,
        "worked_from_sha": kyo("show", dispatch, "--json").json["worked_from_sha"],
        "premise_false": [
            {"claim": "the cache is warm when the first widget is drawn", "correction": "cold"}
        ],
        "disagreements": [
            {
                "topic": "where the eviction is fixed",
                "brief_said": "in the reader",
                "worker_did": "in the writer",
                "rationale": "the writer holds the lock",
            }
        ],
        "corrected_numbers": [
            {"claim": "widgets drawn", "brief_value": "3", "actual_value": "4", "method": "ran"}
        ],
        "criteria_verdicts": [
            {
                "criterion_id": elements["criterion"],
                "verdict": "met",
                "method": "machine-checked",
                "evidence": "the cold start test passes",
            }
        ],
        "commits": [],
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    filed = kyo("report", dispatch, "--file", str(path), "--json")
    assert filed.code == 0, filed
    report = filed.json["report_id"]
    return {"report": report, "record": record, "path": str(path)} | {
        letter: f"{report}.{letter}1" for letter in "pxnv"
    }


def test_kyo757_a_paragraph_is_anchored_to_each_entry_of_a_report(
    bed: Bed, notes_path: str, can_send: str, reported: dict[str, str]
) -> None:
    """The line is the entry's id and its words, and `kyo show` reads the id."""
    anchors = [{"kind": "record", "id": reported[letter]} for letter in "pxnv"]
    status, sent = post(bed, SEND_PROMPT, one_mark(*anchors), can_send)
    assert status == 200, sent
    assert sent["message"].splitlines() == [
        f'on record {reported["p"]} "the cache is warm when the..."',
        f'on record {reported["x"]} "where the eviction is fixed"',
        f'on record {reported["n"]} "widgets drawn"',
        f'on record {reported["v"]} "the cold start test passes"',
        "about this",
    ]
    bed.show(IDLE)
    wait_until(lambda: len(filed_by(bed, notes_path, can_send, sent["held"]["id"])) == 1)
    (note,) = filed_by(bed, notes_path, can_send, sent["held"]["id"])
    # Stored as a record anchor by the entry's id, with no new key and no schema change.
    assert note["anchors"] == [{"kind": "record", "target": reported[letter]} for letter in "pxnv"]
    kyo = kyo_of(bed)
    for letter in "pxnv":
        assert kyo("show", reported[letter]).code == 0
    # A note on an entry is found from its record, and the marks route gives it as a place.
    found = on_the_cli(bed, "list", "--on", reported["record"])
    assert [item["id"] for item in found["notes"]] == [note["id"]]
    (mark,) = marks_of(bed, can_send)
    assert mark["anchors"] == [{"kind": "record", "id": reported[letter]} for letter in "pxnv"]


def test_kyo757_an_entry_of_a_superseded_report_is_still_anchored_to(
    bed: Bed, can_send: str, reported: dict[str, str]
) -> None:
    """An amendment mints a report with ids of its own and leaves the first one's readable."""
    kyo = kyo_of(bed)
    body = json.loads(Path(reported["path"]).read_text(encoding="utf-8"))
    body["amends"] = reported["report"]
    body["premise_false"][0]["claim"] = "the cache is cold at the first widget"
    Path(reported["path"]).write_text(json.dumps(body), encoding="utf-8")
    amended = kyo("report", body["dispatch_id"], "--file", reported["path"], "--json")
    assert amended.code == 0, amended
    second = amended.json["report_id"]
    anchors = [{"kind": "record", "id": reported["p"]}, {"kind": "record", "id": f"{second}.p1"}]
    status, sent = post(bed, SEND_PROMPT, one_mark(*anchors), can_send)
    assert status == 200, sent
    assert on_lines(sent) == [
        f'on record {reported["p"]} "the cache is warm when the..."',
        f'on record {second}.p1 "the cache is cold at the..."',
    ]


def test_kyo757_an_entry_the_report_does_not_hold_is_refused_and_a_listed_id_clears_it(
    bed: Bed, can_send: str, reported: dict[str, str]
) -> None:
    report, record = reported["report"], reported["record"]
    missing = f"{report}.x9"
    status, refused = post(bed, SEND_PROMPT, one_mark({"kind": "record", "id": missing}), can_send)
    assert status == 400, refused
    assert f"no disagreement {missing!r} in this store" in refused["error"]
    assert f"`kyo show {report}` prints the ids of that report's entries" in refused["error"]
    assert get(bed, HELD_ROUTE, can_send)[1]["held"] is None
    # The remedy: an id that command prints.
    shown = kyo_of(bed)("show", report)
    listed = next(word for word in shown.out.split() if word.startswith(f"{report}.x"))
    status, sent = post(bed, SEND_PROMPT, one_mark({"kind": "record", "id": listed}), can_send)
    assert status == 200, sent
    assert on_lines(sent)[0].startswith(f"on record {listed}")
    # A report the record does not hold is answered with the record's own listing.
    absent = one_mark({"kind": "record", "id": f"{record}.r9.p1"})
    status, refused = post(bed, SEND_PROMPT, absent, can_send)
    assert status == 400, refused
    assert f"`kyo show {record}` prints the ids that record holds" in refused["error"]
    assert report in kyo_of(bed)("show", record).out
    # A docs verdict and a commit have no id, and the refusal says which entries have.
    status, refused = post(
        bed, SEND_PROMPT, one_mark({"kind": "record", "id": f"{report}.c1"}), can_send
    )
    assert status == 400 and "KYO-12.r1.p1, .x1, .n1 or .v1" in refused["error"]


SOURCE = "import os\n\n\ndef reformat(text):\n    return text.strip()\n\nLIMIT = 3\n"
PLAN = (
    "# The plan\n"
    "\n"
    "The first chunk is the reader, and it is done.\n"
    "\n"
    "- The second chunk waits on the signing key that the lab holds.\n"
    "- The third chunk is the composer.\n"
)


@pytest.fixture
def code(bed: Bed) -> Path:
    """The bed's project as a repository with a source file and a plan, committed."""
    (bed.project / "src").mkdir()
    (bed.project / "src" / "tidy.py").write_text(SOURCE, encoding="utf-8")
    (bed.project / "docs").mkdir(exist_ok=True)
    (bed.project / "docs" / "plan.md").write_text(PLAN, encoding="utf-8")
    git_init(bed.project)
    return bed.project


def served(path: str, first: int, last: int, **more) -> dict:
    return {"kind": "file", "root": "code", "path": path, "lines": [first, last], **more}


def head_of(root: Path) -> str:
    done = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def test_c2_a_span_of_a_served_file_is_its_path_its_lines_and_its_words(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    status, sent = post(
        bed,
        SEND_PROMPT,
        one_mark(served("src/tidy.py", 4, 5), served("docs/plan.md", 5, 6)),
        with_files,
    )
    assert status == 200, sent
    assert on_lines(sent) == [
        'on file src/tidy.py lines 4-5 "def reformat(text): return text.strip()"',
        # A markdown block is quoted without its mark, and labelled as a reply's is.
        'on file docs/plan.md lines 5-6 "The second chunk waits on the..." (list item)',
    ]
    bed.show(IDLE)
    wait_until(lambda: len(filed_by(bed, notes_path, with_files, sent["held"]["id"])) == 1)
    (note,) = filed_by(bed, notes_path, with_files, sent["held"]["id"])
    source, plan = note["anchors"]
    # The existing kinds: a markdown file is a document, any other a file.
    assert (source["kind"], plan["kind"]) == ("file", "document")
    assert (source["target"], source["line_start"], source["line_end"]) == ("src/tidy.py", 4, 5)
    # The commit the file was read at, and the digest of the lines as served.
    assert source["commit_sha"] == plan["commit_sha"] == head_of(code)
    assert source["digest"] == cmd_notes.lines_digest(SOURCE.split("\n")[3:5])
    assert source["digest_check"] == "computed" and "session" not in source
    assert set(source) <= {
        "kind", "target", "commit_sha", "line_start", "line_end", "digest", "digest_check",
    }  # fmt: skip
    shown = on_the_cli(bed, "show", note["id"])
    assert shown["note"]["anchors"] == note["anchors"]


def chars_of(text: str, words: str) -> list[int]:
    start = text.index(words)
    return [start, start + len(words)]


def test_c2_a_short_selection_quotes_its_sentence_and_a_long_one_is_quoted_itself(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    long = "waits on the signing key that the lab"
    status, sent = post(
        bed,
        SEND_PROMPT,
        one_mark(
            served("docs/plan.md", 5, 5, chars=chars_of(PLAN, "waits")),
            served("docs/plan.md", 5, 5, chars=chars_of(PLAN, long)),
            served("docs/plan.md", 1, 1, chars=chars_of(PLAN, "The plan")),
            served("src/tidy.py", 5, 5, chars=chars_of(SOURCE, "strip")),
            served("src/tidy.py", 4, 5, chars=chars_of(SOURCE, "reformat(text):\n    return")),
        ),
        with_files,
    )
    assert status == 200, sent
    assert on_lines(sent) == [
        'on file docs/plan.md line 5 "The second chunk waits on the signing key that the..."'
        ' at "waits" (list item)',
        'on file docs/plan.md line 5 "waits on the signing key that..." (list item)',
        # A selection that is the whole of its sentence is the quote already.
        'on file docs/plan.md line 1 "The plan" (heading)',
        # A source file has no blocks: the line is the sentence, and nothing is labelled.
        'on file src/tidy.py line 5 "return text.strip()" at "strip"',
        # A few words over two lines are in no one sentence, and are quoted themselves.
        'on file src/tidy.py lines 4-5 "reformat(text): return"',
    ]
    bed.show(IDLE)
    wait_until(lambda: len(filed_by(bed, notes_path, with_files, sent["held"]["id"])) == 1)
    (note,) = filed_by(bed, notes_path, with_files, sent["held"]["id"])
    first = note["anchors"][0]
    assert [first["char_start"], first["char_end"]] == chars_of(PLAN, "waits")


def test_c2_a_file_with_uncommitted_changes_is_anchored_as_it_is_served(
    bed: Bed, with_files: str, code: Path
) -> None:
    (code / "src" / "tidy.py").write_text("# edited since the commit\n" + SOURCE, encoding="utf-8")
    (code / "src" / "new.py").write_text("fresh = True\n", encoding="utf-8")
    document = one_mark(served("src/tidy.py", 1, 1), served("src/new.py", 1, 1))
    status, sent = post(bed, SEND_PROMPT, document, with_files)
    assert status == 200, sent
    assert on_lines(sent) == [
        'on file src/tidy.py line 1 "# edited since the commit"',
        'on file src/new.py line 1 "fresh = True"',
    ]


def test_c2_what_cannot_be_anchored_is_refused_by_name_and_nothing_is_held(
    bed: Bed, with_files: str, code: Path
) -> None:
    (code / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    for anchor, status, words in (
        (served("src/tidy.py", 7, 9), 400, "do not fall within the 7 lines src/tidy.py has now"),
        (served("src/tidy.py", 0, 1), 400, "do not fall within"),
        (served("src/gone.py", 1, 1), 404, "marks[0].anchors[0]: src/gone.py is not a file"),
        (served("pic.png", 1, 1), 415, "no lines to anchor to"),
        (served("../outside.py", 1, 1), 400, "marks[0].anchors[0]"),
        ({**served("src/tidy.py", 1, 1), "root": "attic"}, 400, "code, store, scratch"),
        ({**served("src/tidy.py", 1, 1), "session": OLD}, 400, "leave it out"),
        ({**served("src/tidy.py", 1, 1), "root": "scratch"}, 400, "needs 'session'"),
        ({**served("src/tidy.py", 1, 1), "commit": "abc1234"}, 400, "kyo reads the commit"),
        ({**served("src/tidy.py", 1, 1), "words": "import os"}, 400, "takes no 'words'"),
        (served("src/tidy.py", 4, 4, chars=[0, 3]), 400, "not a selection inside lines 4-4"),
    ):
        got, refused = post(bed, SEND_PROMPT, one_mark(anchor), with_files)
        assert got == status and words in refused["error"], (anchor, refused)
    assert get(bed, HELD_ROUTE, with_files)[1]["held"] is None
    # The remedy the first refusal names: lines the file has now.
    assert post(bed, SEND_PROMPT, one_mark(served("src/tidy.py", 7, 7)), with_files)[0] == 200


def test_c2_reading_a_file_for_an_anchor_needs_the_files_permission_and_issuing_it_clears_it(
    bed: Bed, can_send: str, code: Path, store_home: Path
) -> None:
    document = one_mark(served("src/tidy.py", 1, 1))
    status, refused = post(bed, SEND_PROMPT, document, can_send)
    assert status == 403 and refused["reason"] == "no-files-permission"
    assert "--permission files" in refused["error"]
    assert get(bed, HELD_ROUTE, can_send)[1]["held"] is None
    # A record needs no file read, so the same token still anchors to one.
    again = issue("studio", "notes", "send", "files")
    status, sent = post(bed, SEND_PROMPT, document, again)
    assert status == 200 and on_lines(sent) == ['on file src/tidy.py line 1 "import os"']


def test_c2_a_repository_with_no_commit_is_refused_and_a_first_commit_clears_it(
    bed: Bed, with_files: str
) -> None:
    (bed.project / "first.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=bed.project, check=True)
    document = one_mark(served("first.py", 1, 1))
    status, refused = post(bed, SEND_PROMPT, document, with_files)
    assert status == 400 and "has no commit yet" in refused["error"], refused
    assert "`git commit`" in refused["error"]
    git_init(bed.project)
    assert post(bed, SEND_PROMPT, document, with_files)[0] == 200


def report_scratch(bed: Bed, directory: Path) -> None:
    """What `kyo sessions scratch <dir>` records for the bed's session."""
    path = cmd_sessions.sessions_path(bed.project)
    entries = [
        {**entry, "scratchpad_dir": str(directory)} if entry["session_id"] == OLD else entry
        for entry in cmd_sessions.read_sessions(path).entries
    ]
    assert any(entry.get("scratchpad_dir") for entry in entries)
    assert cmd_sessions.write_sessions(path, entries) is None


def test_c2_a_scratchpad_file_names_its_root_and_its_session_stands_for_a_commit(
    bed: Bed, notes_path: str, with_files: str, code: Path, tmp_path: Path
) -> None:
    scratch = tmp_path / "scratchpad" / OLD
    scratch.mkdir(parents=True)
    (scratch / "notes.md").write_text("# Notes\n\nTry the tunnel first.\n", encoding="utf-8")
    report_scratch(bed, scratch)
    anchor = {**served("notes.md", 3, 3), "root": "scratch", "session": OLD}
    status, sent = post(bed, SEND_PROMPT, one_mark(anchor), with_files)
    assert status == 200, sent
    assert on_lines(sent) == [
        'on file notes.md in the scratchpad line 3 "Try the tunnel first."'
    ]
    bed.show(IDLE)
    wait_until(lambda: len(filed_by(bed, notes_path, with_files, sent["held"]["id"])) == 1)
    (note,) = filed_by(bed, notes_path, with_files, sent["held"]["id"])
    (stored,) = note["anchors"]
    assert stored["session"] == OLD and "commit_sha" not in stored
    assert stored["kind"] == "document" and stored["digest"].startswith("lines-sha256:")
    (mark,) = marks_of(bed, with_files)
    assert mark["anchors"] == [
        {"kind": "document", "root": "scratch", "path": "notes.md", "session": OLD, "lines": [3, 3]}
    ]
    # Read from another session, the line says whose scratchpad it is.
    ctx = commands.build_context(
        str(bed.project), None, dict(bed.server.environ), ["serve", "notes"],
        project=commands.PROJECT_REQUIRED, read_only=True,
    )  # fmt: skip
    try:
        files = serve_notes._Files(bed.server, str(bed.project), None)
        line = serve_notes._anchor_line(ctx, stored, {}, "another-session", files)
    finally:
        ctx.close()
    assert line == (
        f'on file notes.md in the scratchpad of session {OLD} line 3 "Try the tunnel first."'
    )


def sent_with(bed: Bed, notes_path: str, token: str, *anchors: dict) -> dict:
    """A prompt anchored to these places, delivered, as its filed note."""
    status, sent = post(bed, SEND_PROMPT, one_mark(*anchors), token)
    assert status == 200, sent
    bed.show(IDLE)
    wait_until(lambda: len(filed_by(bed, notes_path, token, sent["held"]["id"])) == 1)
    bed.show_frame(FRAME_WORKING)
    return filed_by(bed, notes_path, token, sent["held"]["id"])[0]


def commit_all(root: Path, message: str) -> None:
    for args in (("add", "-A"), ("commit", "-q", "-m", message)):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_c3_the_marks_route_gives_records_and_files_with_the_turn_anchors(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    kyo = kyo_of(bed)
    record = new_record(kyo, criteria=["the reader is done"])
    criterion = kyo("show", record, "--json").json["criteria"][0]["id"]
    note = sent_with(
        bed,
        notes_path,
        with_files,
        span(PROMPT, 0, 5),
        {"kind": "record", "id": criterion},
        served("src/tidy.py", 4, 5, chars=chars_of(SOURCE, "reformat")),
    )
    (mark,) = marks_of(bed, with_files)
    assert mark["note"] == note["id"] and mark["text"] == "about this"
    assert mark["anchors"] == [
        {"role": "person", "uuid": PROMPT, "start": 0, "end": 5},
        {"kind": "record", "id": criterion},
        {
            "kind": "file",
            "root": "code",
            "path": "src/tidy.py",
            "commit": head_of(code),
            "lines": [4, 5],
            "chars": chars_of(SOURCE, "reformat"),
        },
    ]
    # A note with no turn anchor is given too, when it was sent to this session.
    alone = sent_with(bed, notes_path, with_files, {"kind": "record", "id": record})
    assert [item["note"] for item in marks_of(bed, with_files)] == [note["id"], alone["id"]]
    other = "11111111-2222-4333-8444-555555555555"
    assert get(bed, f"{SESSIONS}/{other}/marks", with_files)[1]["marks"] == []


def test_c3_a_file_anchor_is_stale_when_its_lines_no_longer_hold_the_words_and_not_before(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    sent_with(bed, notes_path, with_files, served("src/tidy.py", 4, 5))

    def stale() -> bool:
        (mark,) = marks_of(bed, with_files)
        return mark["anchors"][0].get("stale", False)

    assert not stale()
    # A commit that leaves the file alone, and one that changes it below the lines.
    (code / "other.txt").write_text("elsewhere\n", encoding="utf-8")
    commit_all(code, "another file")
    assert not stale()
    (code / "src" / "tidy.py").write_text(SOURCE + "MORE = 4\n", encoding="utf-8")
    commit_all(code, "a line added below")
    assert not stale()
    # The words changed in place, uncommitted: the file as served is what is compared.
    (code / "src" / "tidy.py").write_text(SOURCE.replace("strip", "rstrip"), encoding="utf-8")
    assert stale()
    (code / "src" / "tidy.py").write_text(SOURCE, encoding="utf-8")
    assert not stale()
    # The words moved down a line: they are no longer at those lines.
    (code / "src" / "tidy.py").write_text("import sys\n" + SOURCE, encoding="utf-8")
    assert stale()
    (code / "src" / "tidy.py").unlink()
    assert stale()


def test_c3_a_record_anchor_is_stale_when_the_store_no_longer_holds_the_element(
    bed: Bed, notes_path: str, with_files: str
) -> None:
    kyo = kyo_of(bed)
    record = new_record(kyo, criteria=["the reader is done"])
    criterion = kyo("show", record, "--json").json["criteria"][0]["id"]
    sent_with(bed, notes_path, with_files, {"kind": "record", "id": criterion})
    (mark,) = marks_of(bed, with_files)
    assert mark["anchors"] == [{"kind": "record", "id": criterion}]
    connection = sqlite3.connect(bed.project / "kyosei" / "kyosei.db", isolation_level=None)
    connection.execute("DELETE FROM criteria WHERE id = ?", (criterion,))
    connection.close()
    (mark,) = marks_of(bed, with_files)
    assert mark["anchors"] == [{"kind": "record", "id": criterion, "stale": True}]


def test_c3_an_anchor_filed_with_a_commit_alone_is_compared_with_the_file_at_that_commit(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    sha = head_of(code)
    note = file_note(
        bed,
        notes_path,
        with_files,
        anchors=[{"kind": "file", "path": "src/tidy.py", "commit": sha, "lines": [4, 5]}],
    )
    sent_on_the_store(bed, [note["id"]])
    place = {"kind": "file", "root": "code", "path": "src/tidy.py", "commit": sha, "lines": [4, 5]}
    assert marks_of(bed, with_files)[0]["anchors"] == [place]
    (code / "src" / "tidy.py").write_text(SOURCE + "MORE = 4\n", encoding="utf-8")
    commit_all(code, "a line added below")
    assert marks_of(bed, with_files)[0]["anchors"] == [place]
    (code / "src" / "tidy.py").write_text("import sys\n" + SOURCE, encoding="utf-8")
    assert marks_of(bed, with_files)[0]["anchors"] == [place | {"stale": True}]


def test_c2_a_file_in_the_store_repository_names_that_root_by_its_commit(
    split_project, store_home: Path, running
) -> None:
    code, store = split_project
    (store / "docs").mkdir(exist_ok=True)
    (store / "docs" / "plan.md").write_text(PLAN, encoding="utf-8")
    if not (store / ".git").exists():
        git_init(store)
    else:
        commit_all(store, "a plan")
    (code / "docs").mkdir(exist_ok=True)
    (code / "docs" / "plan.md").write_text("# Another plan, in the code\n", encoding="utf-8")
    commit_all(code, "a plan of the same name")
    register(store_home, code)
    client = running()
    name = serve.project_name(str(code))
    token = issue("mac", "notes", "files")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    anchor = {"kind": "file", "root": "store", "path": "docs/plan.md", "lines": [3, 3]}
    status, _, body = client.request(
        f"/v1/projects/{name}/notes",
        method="POST",
        headers=headers,
        body=json.dumps({"text": "x", "entered": "typed", "anchors": [anchor]}).encode(),
    )
    assert status == 201, body
    (stored,) = body_json(body)["note"]["anchors"]
    assert stored["commit_sha"] == head_of(store) != head_of(code)
    ctx = commands.build_context(
        str(code), None, dict(client.server.environ), ["serve", "notes"],
        project=commands.PROJECT_REQUIRED, read_only=True,
    )  # fmt: skip
    try:
        files = serve_notes._Files(client.server, str(code), None)
        line = serve_notes._anchor_line(ctx, stored, {}, OLD, files)
        place = serve_notes._file_place(ctx, files, stored)
    finally:
        ctx.close()
    assert line == (
        'on file docs/plan.md in the store repository line 3 "The first chunk is the reader,..."'
    )
    assert place["root"] == "store" and "stale" not in place


def test_c1_c2_a_set_of_notes_is_sent_with_the_same_lines(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    kyo = kyo_of(bed)
    record = new_record(kyo, title="The reader", criteria=["the reader is done"])
    note = file_note(
        bed,
        notes_path,
        with_files,
        anchors=[{"kind": "record", "id": record}, served("src/tidy.py", 7, 7)],
    )
    status, sent = post(bed, SEND_NOTES, {"notes": [note["id"]]}, with_files)
    assert status == 200, sent
    assert on_lines(sent) == [
        f'on record {record} "The reader"',
        'on file src/tidy.py line 7 "LIMIT = 3"',
    ]
    # The file's words go into the message, so sending them needs the files permission too.
    status, refused = post(bed, SEND_NOTES, {"notes": [note["id"]]}, issue("pad", "notes", "send"))
    assert status == 403 and refused["reason"] == "no-files-permission"
