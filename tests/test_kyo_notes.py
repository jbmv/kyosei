"""KYO-705: notes in the store, their anchors and tags, through `kyo notes`.

Written through the real CLI on a throwaway project; the transcript is one this test puts
under a fake ``CLAUDE_CONFIG_DIR``, so no session is read.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from kyosei import cmd_notes
from kyosei.contracts import REDACTION_MARKER
from kyosei.ledgers import PERSON_NOTE_TAG_CHANGES_FILENAME, PERSON_NOTES_FILENAME

from .conftest import declare_person, git_init, new_record, opt_in_transcripts

SESSION = "11111111-1111-4111-8111-111111111111"
PROMPT = "22222222-2222-4222-8222-222222222222"
REPLY = "33333333-3333-4333-8333-333333333333"
PROMPT_TEXT = "why don't we just reformat it ourselves?"
REPLY_TEXT = "the transcript does not stream; the screen does"
SPAN_WORDS = "does not stream"


def _ledger(project: Path) -> list[dict]:
    path = project / "kyosei" / "export" / PERSON_NOTES_FILENAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _add(kyo, *flags: str, text: str | None = "a note\n", entered: str = "typed", **kw):
    args = ["notes", "add", "--entered", entered, "--json", *flags]
    if text is not None:
        args += ["--file", "-"]
    return kyo(*args, stdin=text, **kw)


def _anchored(kyo, write_file, anchors: list, *flags: str):
    path = write_file("anchors.json", json.dumps(anchors))
    return _add(kyo, "--anchors-file", path, *flags)


@pytest.fixture
def transcript(tmp_path: Path, monkeypatch, project: Path) -> None:
    """A transcript this project may read, holding one prompt and one reply."""
    home = tmp_path / "claude-config"
    (home / "projects" / "-p").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    opt_in_transcripts(project)
    records = [
        {
            "type": "user",
            "uuid": PROMPT,
            "timestamp": "2026-11-09T10:00:00.000Z",
            "sessionId": SESSION,
            "message": {"role": "user", "content": PROMPT_TEXT},
        },
        {
            "type": "assistant",
            "uuid": REPLY,
            "timestamp": "2026-11-09T10:00:05.000Z",
            "sessionId": SESSION,
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": REPLY_TEXT},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
                ],
            },
        },
    ]
    (home / "projects" / "-p" / f"{SESSION}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )


def _turn(uuid: str = REPLY, start: int | None = None, end: int | None = None, **extra) -> dict:
    begin = REPLY_TEXT.index(SPAN_WORDS) if start is None else start
    finish = begin + len(SPAN_WORDS) if end is None else end
    anchor = {"kind": "turn", "session": SESSION, "uuid": uuid, "start": begin, "end": finish}
    return {**anchor, **extra}


# ------------------------------------------------------------------- c1: a note


def test_a_note_holds_its_text_device_time_and_how_it_was_entered(kyo, project: Path) -> None:
    result = _add(kyo, "--device", "ipad-pro", text="my own words\n", entered="dictated")
    assert result.code == 0, result
    note = result.json["note"]
    assert note["id"] == "TST-N1"
    assert note["text"] == "my own words"
    assert note["device"] == "ipad-pro"
    assert note["entered"] == "dictated"
    assert note["created_at"].endswith("Z")
    assert kyo("notes", "show", "TST-N1", "--json").json["note"] == note


def test_the_device_defaults_to_this_machines_name(kyo, project: Path) -> None:
    import socket

    assert _add(kyo).json["note"]["device"] == socket.gethostname()


def test_how_a_note_was_entered_must_be_stated(kyo, project: Path) -> None:
    result = kyo("notes", "add", "--file", "-", stdin="words\n")
    assert result.code != 0
    assert "--entered" in result.err
    assert _add(kyo).code == 0, "the flag the refusal names clears it"


def test_a_note_is_exported_to_the_ledger_with_its_anchors_and_tags(kyo, project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    result = _add(kyo, "--on", record, "--tag", "Design", "--tag", "#urgent", text="words\n")
    assert result.code == 0, result
    line = _ledger(project)[0]
    assert line["id"] == "TST-N1"
    assert line["text"] == "words"
    assert line["tags"] == ["design", "urgent"]
    assert line["anchors"] == [{"kind": "record", "target": record}]
    assert line["schema_version"]


def test_a_note_rebuilds_from_export_alone(kyo, project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    first = _add(kyo, "--on", record, "--tag", "keep", text="first\n").json["note"]
    second = _add(kyo, "--on", "TST-N1", text="a reply\n").json["note"]
    (project / "kyosei" / "kyosei.db").unlink()
    rebuilt = kyo("rebuild")
    assert rebuilt.code == 0, rebuilt
    assert kyo("notes", "show", "TST-N1", "--json").json["note"] == first
    assert kyo("notes", "show", "TST-N2", "--json").json["note"] == second
    assert _add(kyo).json["note"]["id"] == "TST-N3", "the counter survived"
    doctor = kyo("doctor")
    assert doctor.code == 0, doctor


def test_a_note_lost_from_the_ledger_is_repaired_from_the_database(kyo, project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    _add(kyo, "--on", record, "--tag", "t", text="keep me\n")
    (project / "kyosei" / "export" / PERSON_NOTES_FILENAME).write_text("", encoding="utf-8")
    assert kyo("export", "--refresh").code == 0
    line = _ledger(project)[0]
    assert line["text"] == "keep me"
    assert line["anchors"] == [{"kind": "record", "target": record}]
    assert line["tags"] == ["t"]


def test_a_person_note_is_redactable_and_the_redaction_reaches_the_ledger(
    kyo, project: Path
) -> None:
    _add(kyo, text="a secret I typed\n")
    result = kyo("redact", "TST-N1", "--why", "typed a secret", "--json")
    assert result.code == 0, result
    assert result.json["target_kind"] == "person-note"
    assert _ledger(project)[0]["text"] == REDACTION_MARKER
    note = kyo("notes", "show", "TST-N1", "--json").json["note"]
    assert note["text"] == REDACTION_MARKER and note["redacted_why"] == "typed a secret"
    doctor = kyo("doctor")
    assert doctor.code == 0, doctor


def test_a_note_needs_text_or_a_tag(kyo, project: Path) -> None:
    assert _add(kyo, text=None).code != 0
    bare = _add(kyo, "--tag", "look-here", text=None)
    assert bare.code == 0, bare
    assert bare.json["note"]["text"] == ""


# --------------------------------------------------------------- c2: the anchors


def test_a_turn_anchor_is_read_from_the_transcript_and_holds_no_words(
    kyo, project: Path, write_file, transcript
) -> None:
    anchors = [_turn(), _turn(PROMPT, 0, len(PROMPT_TEXT))]
    result = _anchored(kyo, write_file, anchors)
    assert result.code == 0, result
    agent, person = result.json["note"]["anchors"]
    start = REPLY_TEXT.index(SPAN_WORDS)
    assert agent == {
        "kind": "turn",
        "target": REPLY,
        "session": SESSION,
        "role": "agent",
        "char_start": start,
        "char_end": start + len(SPAN_WORDS),
        "digest": cmd_notes.span_digest(REPLY_TEXT, start, start + len(SPAN_WORDS)),
        "digest_check": "computed",
    }
    assert person["role"] == "person"
    exported = (project / "kyosei" / "export" / PERSON_NOTES_FILENAME).read_text(encoding="utf-8")
    assert SPAN_WORDS not in exported
    assert REPLY_TEXT not in exported
    assert PROMPT_TEXT not in exported


def test_the_digest_cannot_be_recomputed_from_the_span_alone() -> None:
    plain = cmd_notes.span_digest(SPAN_WORDS, 0, len(SPAN_WORDS))
    bound = cmd_notes.span_digest(
        REPLY_TEXT, REPLY_TEXT.index(SPAN_WORDS), REPLY_TEXT.index(SPAN_WORDS) + len(SPAN_WORDS)
    )
    assert plain != bound
    assert bound.startswith(f"{cmd_notes.DIGEST_SCHEME}:")


def test_a_digest_that_disagrees_with_the_transcript_is_refused(
    kyo, project: Path, write_file, transcript
) -> None:
    wrong = cmd_notes.span_digest("other text", 0, 5)
    result = _anchored(kyo, write_file, [_turn(digest=wrong)])
    assert result.code != 0
    assert "does not match" in result.err
    assert not (project / "kyosei" / "export" / PERSON_NOTES_FILENAME).exists()


def test_a_range_past_the_record_is_refused(kyo, project: Path, write_file, transcript) -> None:
    result = _anchored(kyo, write_file, [_turn(start=0, end=10_000)])
    assert result.code != 0
    assert "past the record" in result.err


def test_a_project_that_has_not_opted_in_reads_nothing_and_says_it_was_given(
    kyo, project: Path, write_file, monkeypatch
) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("a transcript was opened in a project that has not opted in")

    monkeypatch.setattr(cmd_notes.transcript, "candidate_transcripts", refuse)
    digest = cmd_notes.span_digest(REPLY_TEXT, 4, 9)
    result = _anchored(kyo, write_file, [_turn(start=4, end=9, digest=digest, role="agent")])
    assert result.code == 0, result
    anchor = result.json["note"]["anchors"][0]
    assert anchor["digest"] == digest
    assert anchor["digest_check"] == "given"


def test_a_turn_anchor_with_nothing_to_read_is_cleared_by_opting_in(
    kyo, project: Path, write_file, tmp_path: Path, monkeypatch
) -> None:
    result = _anchored(kyo, write_file, [_turn(start=0, end=4)])
    assert result.code != 0 and "read_transcripts" in result.err
    home = tmp_path / "claude"
    (home / "projects" / "-p").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    record = {"type": "assistant", "uuid": REPLY, "message": {"content": REPLY_TEXT}}
    (home / "projects" / "-p" / f"{SESSION}.jsonl").write_text(json.dumps(record) + "\n")
    opt_in_transcripts(project)
    cleared = _anchored(kyo, write_file, [_turn(start=0, end=4)])
    assert cleared.code == 0, "the second remedy the refusal names clears it"
    assert cleared.json["note"]["anchors"][0]["digest_check"] == "computed"


def test_a_turn_anchor_with_nothing_to_read_names_its_remedy(
    kyo, project: Path, write_file
) -> None:
    result = _anchored(kyo, write_file, [_turn(start=4, end=9)])
    assert result.code != 0
    assert "'digest' and 'role'" in result.err
    digest = cmd_notes.span_digest(REPLY_TEXT, 4, 9)
    cleared = _anchored(kyo, write_file, [_turn(start=4, end=9, digest=digest, role="agent")])
    assert cleared.code == 0, "the remedy the refusal names clears it"


@pytest.mark.parametrize("key", ["text", "quote", "excerpt"])
def test_an_anchor_refuses_to_carry_the_words_it_addresses(
    kyo, project: Path, write_file, key: str
) -> None:
    digest = cmd_notes.span_digest(REPLY_TEXT, 4, 9)
    result = _anchored(
        kyo, write_file, [_turn(start=4, end=9, digest=digest, role="agent", **{key: "words"})]
    )
    assert result.code != 0
    assert "never the text" in result.err


def test_record_criterion_and_gate_anchors(kyo, project: Path, write_file) -> None:
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "holding-for", "--do", "x")
    shown = kyo("show", record, "--json").json
    criterion, gate = shown["criteria"][0]["id"], shown["gates"][0]["id"]
    result = _add(kyo, "--on", record, "--on", criterion, "--on", gate)
    assert result.code == 0, result
    assert [a["target"] for a in result.json["note"]["anchors"]] == [record, criterion, gate]


@pytest.mark.parametrize("target", ["TST-999", "TST-1.c9", "TST-N9"])
def test_an_anchor_on_something_that_does_not_exist_is_refused(
    kyo, project: Path, target: str
) -> None:
    new_record(kyo, criteria=["a"])
    result = _add(kyo, "--on", target)
    assert result.code != 0
    assert not (project / "kyosei" / "export" / PERSON_NOTES_FILENAME).exists()


def test_a_note_anchored_to_a_note_threads_a_reply(kyo, project: Path) -> None:
    first = _add(kyo, text="first\n").json["note"]["id"]
    reply = _add(kyo, "--on", first, text="a reply\n")
    assert reply.code == 0, reply
    assert reply.json["note"]["anchors"] == [{"kind": "note", "target": first}]


def test_document_and_file_anchors_address_a_span_at_a_commit(
    kyo, project: Path, write_file
) -> None:
    (project / "plan.md").write_text("one\ntwo\nthree\n", encoding="utf-8")
    sha = git_init(project)
    anchors = [
        {"kind": "document", "path": "plan.md", "commit": sha, "lines": [2, 3]},
        {"kind": "document", "path": "plan.md", "commit": sha, "chars": [0, 3]},
        {"kind": "file", "path": "plan.md", "commit": sha, "lines": [1, 1]},
    ]
    result = _anchored(kyo, write_file, anchors)
    assert result.code == 0, result
    assert result.json["note"]["anchors"] == [
        {
            "kind": "document",
            "target": "plan.md",
            "commit_sha": sha,
            "line_start": 2,
            "line_end": 3,
        },
        {
            "kind": "document",
            "target": "plan.md",
            "commit_sha": sha,
            "char_start": 0,
            "char_end": 3,
        },
        {"kind": "file", "target": "plan.md", "commit_sha": sha, "line_start": 1, "line_end": 1},
    ]


def test_a_span_beyond_the_file_or_a_commit_without_the_file_is_refused(
    kyo, project: Path, write_file
) -> None:
    (project / "plan.md").write_text("one\n", encoding="utf-8")
    sha = git_init(project)
    long = _anchored(
        kyo, write_file, [{"kind": "file", "path": "plan.md", "commit": sha, "lines": [1, 9]}]
    )
    assert long.code != 0 and "within" in long.err
    absent = _anchored(
        kyo, write_file, [{"kind": "file", "path": "missing.md", "commit": sha, "lines": [1, 1]}]
    )
    assert absent.code != 0 and "neither" in absent.err


# ------------------------------------------------------------ c3: finding notes


def _ids(result) -> list[str]:
    assert result.code == 0, result
    return [note["id"] for note in result.json["notes"]]


def test_notes_are_found_by_tag_across_prompts_records_and_output(
    kyo, project: Path, write_file, transcript
) -> None:
    record = new_record(kyo, criteria=["a"])
    on_output = _anchored(kyo, write_file, [_turn()], "--tag", "idea").json["note"]["id"]
    on_prompt = _anchored(
        kyo, write_file, [_turn(PROMPT, 0, len(PROMPT_TEXT))], "--tag", "idea"
    ).json["note"]["id"]
    on_record = _add(kyo, "--on", record, "--tag", "idea").json["note"]["id"]
    _add(kyo, "--on", record, "--tag", "other")
    assert _ids(kyo("notes", "list", "--tag", "IDEA", "--json")) == [
        on_output,
        on_prompt,
        on_record,
    ]
    assert _ids(kyo("notes", "list", "--tag", "idea", "--anchored-to", "output", "--json")) == [
        on_output
    ]
    assert _ids(kyo("notes", "list", "--tag", "idea", "--anchored-to", "prompt", "--json")) == [
        on_prompt
    ]
    assert _ids(kyo("notes", "list", "--tag", "idea", "--anchored-to", "record", "--json")) == [
        on_record
    ]


def test_notes_are_found_by_what_they_anchor_to(
    kyo, project: Path, write_file, transcript
) -> None:
    record = new_record(kyo, criteria=["a"])
    criterion = kyo("show", record, "--json").json["criteria"][0]["id"]
    on_turn = _anchored(kyo, write_file, [_turn()]).json["note"]["id"]
    on_record = _add(kyo, "--on", record).json["note"]["id"]
    on_criterion = _add(kyo, "--on", criterion).json["note"]["id"]
    assert _ids(kyo("notes", "list", "--on", record, "--json")) == [on_record, on_criterion]
    assert _ids(kyo("notes", "list", "--on", criterion, "--json")) == [on_criterion]
    assert _ids(kyo("notes", "list", "--on", REPLY, "--json")) == [on_turn]
    assert _ids(kyo("notes", "list", "--on", SESSION, "--json")) == [on_turn]


def test_the_filters_must_hold_of_one_anchor(kyo, project: Path, write_file, transcript) -> None:
    """A note on a record and on an agent's reply is on output, but not a record's output."""
    record = new_record(kyo, criteria=["a"])
    path = write_file("a.json", json.dumps([_turn(), {"kind": "record", "id": record}]))
    _add(kyo, "--anchors-file", path)
    assert _ids(kyo("notes", "list", "--on", record, "--anchored-to", "output", "--json")) == []


def test_tags_must_all_match(kyo, project: Path) -> None:
    both = _add(kyo, "--tag", "a", "--tag", "b").json["note"]["id"]
    _add(kyo, "--tag", "a")
    assert _ids(kyo("notes", "list", "--tag", "a", "--tag", "b", "--json")) == [both]


def test_nothing_matched_says_so(kyo, project: Path) -> None:
    result = kyo("notes", "list", "--tag", "absent")
    assert result.code == 0 and "no notes match" in result.out


def test_a_bad_tag_is_refused(kyo, project: Path) -> None:
    result = _add(kyo, "--tag", "two words")
    assert result.code != 0 and "no space" in result.err


# ----------------------------------------------------- c5: tags after a note is filed


def _changes(project: Path) -> list[dict]:
    path = project / "kyosei" / "export" / PERSON_NOTE_TAG_CHANGES_FILENAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _retag(kyo, note: str, *flags: str):
    return kyo("notes", "tag", note, "--json", *flags)


def test_tags_are_added_and_removed_and_found_by_the_current_set(kyo, project: Path) -> None:
    _add(kyo, "--tag", "old", "--tag", "keep")
    result = _retag(kyo, "TST-N1", "--add", "#New", "--remove", "old")
    assert result.code == 0, result
    assert result.json["note"]["tags"] == ["keep", "new"]
    assert _ids(kyo("notes", "list", "--tag", "new", "--json")) == ["TST-N1"]
    assert _ids(kyo("notes", "list", "--tag", "old", "--json")) == []
    assert _ids(kyo("notes", "list", "--tag", "keep", "--json")) == ["TST-N1"]


def test_each_change_is_kept_in_the_ledger_and_attributed(kyo, project: Path) -> None:
    declare_person(project, "maintainer")
    _add(kyo, "--tag", "old")
    _retag(kyo, "TST-N1", "--add", "new", "--remove", "old")
    lines = _changes(project)
    assert [(line["id"], line["note_id"], line["action"], line["tag"]) for line in lines] == [
        ("TST-T1", "TST-N1", "remove", "old"),
        ("TST-T2", "TST-N1", "add", "new"),
    ]
    assert all(line["person"] == "maintainer" and line["author"] for line in lines)
    assert all(line["changed_at"].endswith("Z") and line["schema_version"] for line in lines)
    shown = kyo("notes", "show", "TST-N1")
    assert "tag -old by maintainer" in shown.out and "tag +new by maintainer" in shown.out
    history = kyo("notes", "show", "TST-N1", "--json").json["note"]["tag_changes"]
    assert [(c["action"], c["tag"], c["person"]) for c in history] == [
        ("remove", "old", "maintainer"),
        ("add", "new", "maintainer"),
    ]


def test_a_tag_change_writes_an_event_naming_the_note(kyo, project: Path) -> None:
    _add(kyo)
    _retag(kyo, "TST-N1", "--add", "a")
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    try:
        row = conn.execute(
            "SELECT after_json FROM events WHERE command = 'notes tag'"
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(row[0]) == {
        "person_note_id": "TST-N1",
        "person_note_tag_changes": ["TST-T1"],
    }


def test_a_tag_change_leaves_the_text_anchors_and_filed_line_alone(kyo, project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    filed = _add(kyo, "--on", record, "--tag", "old", text="my words\n").json["note"]
    ledger = project / "kyosei" / "export" / PERSON_NOTES_FILENAME
    before = ledger.read_bytes()
    after = _retag(kyo, "TST-N1", "--add", "new", "--remove", "old").json["note"]
    assert ledger.read_bytes() == before, "the note's own line stays as filed"
    assert after["text"] == filed["text"] == "my words"
    assert after["anchors"] == filed["anchors"] == [{"kind": "record", "target": record}]
    assert after["created_at"] == filed["created_at"]


@pytest.mark.parametrize(
    "flags",
    [
        ("--file", "-"),
        ("--on", "TST-1"),
        ("--anchors-file", "-"),
        ("--tag", "x"),
        ("--device", "ipad"),
        ("--entered", "typed"),
    ],
)
def test_the_tag_command_refuses_anything_but_tags(kyo, project: Path, flags) -> None:
    filed = _add(kyo).json["note"]
    result = kyo("notes", "tag", "TST-N1", "--add", "a", *flags, stdin="other words\n")
    assert result.code != 0
    assert flags[0] in result.err and "fixed" in result.err
    assert kyo("notes", "show", "TST-N1", "--json").json["note"] == filed


def test_the_refusal_to_change_a_note_names_a_remedy_that_works(kyo, project: Path) -> None:
    _add(kyo)
    refused = kyo("notes", "tag", "TST-N1", "--on", "TST-N1", "--add", "a")
    assert "kyo notes add --on TST-N1" in refused.err
    cleared = _add(kyo, "--on", "TST-N1", text="more to say\n")
    assert cleared.code == 0, cleared
    assert cleared.json["note"]["anchors"] == [{"kind": "note", "target": "TST-N1"}]


def test_a_tag_that_is_already_so_writes_nothing(kyo, project: Path) -> None:
    _add(kyo, "--tag", "here")
    allocations = project / "kyosei" / "export" / "_allocations.json"
    before = allocations.read_bytes()
    result = _retag(kyo, "TST-N1", "--add", "here", "--remove", "absent")
    assert result.code == 0, result
    assert result.json["changes"] == []
    assert result.json["unchanged"] == ["absent", "here"]
    assert not (project / "kyosei" / "export" / PERSON_NOTE_TAG_CHANGES_FILENAME).exists()
    assert allocations.read_bytes() == before


def test_a_partly_repeated_request_records_only_what_moved(kyo, project: Path) -> None:
    _add(kyo, "--tag", "here")
    result = _retag(kyo, "TST-N1", "--add", "here", "--add", "new")
    assert [change["tag"] for change in result.json["changes"]] == ["new"]
    assert result.json["unchanged"] == ["here"]
    assert len(_changes(project)) == 1


def test_a_note_may_lose_its_last_tag(kyo, project: Path) -> None:
    """The refusal of a contentless note is about filing; a person can undo a mark."""
    _add(kyo, "--tag", "look-here", text=None)
    result = _retag(kyo, "TST-N1", "--remove", "look-here")
    assert result.code == 0, result
    assert result.json["note"]["tags"] == []
    assert _ids(kyo("notes", "list", "--tag", "look-here", "--json")) == []


@pytest.mark.parametrize(
    ("args", "words"),
    [
        (("notes", "tag", "--add", "a"), "needs a note id"),
        (("notes", "tag", "TST-N1"), "at least one"),
        (("notes", "tag", "TST-N1", "--add", "a", "--remove", "A"), "both name"),
        (("notes", "tag", "TST-N1", "--add", "two words"), "no space"),
        (("notes", "tag", "TST-N9", "--add", "a"), "no note"),
        (("notes", "list", "--add", "a"), "the tag action"),
    ],
)
def test_a_malformed_tag_change_is_refused(kyo, project: Path, args, words: str) -> None:
    _add(kyo)
    result = kyo(*args)
    assert result.code != 0
    assert words in result.err
    assert not (project / "kyosei" / "export" / PERSON_NOTE_TAG_CHANGES_FILENAME).exists()


def test_tag_changes_rebuild_from_export_alone(kyo, project: Path) -> None:
    _add(kyo, "--tag", "old", "--tag", "keep")
    _retag(kyo, "TST-N1", "--add", "new", "--remove", "old")
    _retag(kyo, "TST-N1", "--add", "old")
    _retag(kyo, "TST-N1", "--remove", "new")
    shown = kyo("notes", "show", "TST-N1", "--json").json["note"]
    assert shown["tags"] == ["keep", "old"]
    (project / "kyosei" / "kyosei.db").unlink()
    rebuilt = kyo("rebuild")
    assert rebuilt.code == 0, rebuilt
    assert kyo("notes", "show", "TST-N1", "--json").json["note"] == shown
    assert _ids(kyo("notes", "list", "--tag", "new", "--json")) == []
    next_change = _retag(kyo, "TST-N1", "--add", "later")
    assert next_change.json["changes"][0]["id"] == "TST-T5", "the counter survived"
    doctor = kyo("doctor")
    assert doctor.code == 0, doctor


def test_a_lost_note_line_is_repaired_with_the_tags_it_was_filed_with(kyo, project: Path) -> None:
    _add(kyo, "--tag", "old")
    _retag(kyo, "TST-N1", "--add", "new", "--remove", "old")
    ledger = project / "kyosei" / "export" / PERSON_NOTES_FILENAME
    ledger.write_text("", encoding="utf-8")
    assert kyo("export", "--refresh").code == 0
    assert _ledger(project)[0]["tags"] == ["old"]
    assert "tag_changes" not in _ledger(project)[0]
    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert kyo("notes", "show", "TST-N1", "--json").json["note"]["tags"] == ["new"]


def test_a_lost_tag_change_is_repaired_from_the_database(kyo, project: Path) -> None:
    _add(kyo)
    _retag(kyo, "TST-N1", "--add", "a")
    path = project / "kyosei" / "export" / PERSON_NOTE_TAG_CHANGES_FILENAME
    path.write_text("", encoding="utf-8")
    assert kyo("export", "--refresh").code == 0
    assert [line["id"] for line in _changes(project)] == ["TST-T1"]


def test_a_rebuild_replays_changes_by_seq_not_ledger_order(kyo, project: Path) -> None:
    _add(kyo)
    _retag(kyo, "TST-N1", "--add", "a")
    _retag(kyo, "TST-N1", "--remove", "a")
    path = project / "kyosei" / "export" / PERSON_NOTE_TAG_CHANGES_FILENAME
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
    (project / "kyosei" / "kyosei.db").unlink()
    rebuilt = kyo("rebuild")
    assert rebuilt.code == 0, rebuilt
    assert kyo("notes", "show", "TST-N1", "--json").json["note"]["tags"] == []


def test_a_redacted_notes_tags_can_still_change(kyo, project: Path) -> None:
    _add(kyo, "--tag", "a", text="a secret I typed\n")
    assert kyo("redact", "TST-N1", "--why", "typed a secret").code == 0
    result = _retag(kyo, "TST-N1", "--add", "b")
    assert result.code == 0, result
    assert result.json["note"]["text"] == REDACTION_MARKER
    ledger = project / "kyosei" / "export" / PERSON_NOTES_FILENAME
    assert "a secret I typed" not in ledger.read_text(encoding="utf-8")
