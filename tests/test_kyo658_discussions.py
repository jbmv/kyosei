"""The discussion kind, `kyo discuss`, turn references, and decisions requiring a
discussion (KYO-658; decision KYO-654, DC2 and DC3).

The transcripts written here copy the record shapes of ``tests/test_kyo657_turn_index.py``
and ``tests/test_kyo644_credits.py``; the words are invented.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from kyosei import cmd_turns
from kyosei.cli import build_parser

from .conftest import TEST_PERSON, declare_person, new_record, opt_in_transcripts

SESSION = "7c1d2e3f-1111-4a2b-9c3d-0123456789ab"
PROMPT_1 = "aaaaaaaa-0000-4000-8000-000000000001"
PROMPT_2 = "aaaaaaaa-0000-4000-8000-000000000002"
PROMPT_3 = "aaaaaaaa-0000-4000-8000-000000000003"
DECIDING_WORDS = "keep the cache, it is fast enough"


def _record(kind: str, uuid: str, stamp: str, **extra) -> dict:
    return {
        "type": kind,
        "uuid": uuid,
        "timestamp": stamp,
        "sessionId": SESSION,
        "isSidechain": False,
        **extra,
    }


def person(uuid: str, prompt_id: str, text: str, stamp: str) -> dict:
    return _record(
        "user",
        uuid,
        stamp,
        promptId=prompt_id,
        origin={"kind": "human"},
        promptSource="typed",
        message={"role": "user", "content": text},
    )


def notification(uuid: str, prompt_id: str, stamp: str) -> dict:
    return _record(
        "user",
        uuid,
        stamp,
        promptId=prompt_id,
        origin={"kind": "task-notification"},
        promptSource="system",
        message={"role": "user", "content": "<task-notification>done</task-notification>"},
    )


def agent_text(uuid: str, text: str, stamp: str) -> dict:
    return _record(
        "assistant",
        uuid,
        stamp,
        message={"role": "assistant", "content": [{"type": "text", "text": text}]},
    )


def transcript_path() -> Path:
    return Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-a-project" / f"{SESSION}.jsonl"


def write_transcript(records: list[dict]) -> None:
    path = transcript_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def append_transcript(records: list[dict]) -> None:
    with transcript_path().open("a", encoding="utf-8") as handle:
        handle.write("".join(json.dumps(r) + "\n" for r in records))


def prompt_hook(kyo, prompt_id: str) -> None:
    payload = {
        "session_id": SESSION,
        "hook_event_name": "UserPromptSubmit",
        "prompt_id": prompt_id,
        "prompt": "words that never reach the index",
        "transcript_path": str(transcript_path()),
    }
    assert kyo("turns", "--hook", stdin=json.dumps(payload)).code == 0


def index_line(project: Path, prompt_id: str) -> dict:
    return next(line for line in cmd_turns.read_lines(project) if line["prompt_id"] == prompt_id)


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    return SESSION


@pytest.fixture
def indexed(kyo, project: Path, session: str) -> Path:
    """An opted-in project whose index holds three prompts of one session, none confirmed
    by a Stop hook yet: the person's, a task notification's, and the person's again."""
    opt_in_transcripts(project)
    write_transcript(
        [
            person("p-1", PROMPT_1, "should the cache stay?", "2026-11-06T10:00:00.000Z"),
            agent_text("a-1", "I recommend keeping it.", "2026-11-06T10:00:30.000Z"),
            notification("n-2", PROMPT_2, "2026-11-06T10:00:40.000Z"),
            agent_text("a-2", "The run finished.", "2026-11-06T10:00:50.000Z"),
        ]
    )
    prompt_hook(kyo, PROMPT_1)
    prompt_hook(kyo, PROMPT_2)
    append_transcript(
        [person("p-3", PROMPT_3, DECIDING_WORDS, "2026-11-06T10:01:00.000Z")]
    )
    prompt_hook(kyo, PROMPT_3)
    return project


def _file(tmp: Path, name: str, text: str) -> str:
    path = tmp / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _refs(kyo, record_id: str) -> list[dict]:
    return kyo("show", record_id, "--json").json["turn_refs"]


# ------------------------------------------------------------------ KYO-658.c1


def test_open_stamps_the_latest_confirmed_person_turn_from_the_index(
    kyo, indexed: Path
) -> None:
    """The prompt being answered has no Stop hook behind it yet; `open` folds the transcript
    into the index first, so it is confirmed and stamped, with the index's own time."""
    assert all(line["origin"] == "unconfirmed" for line in cmd_turns.read_lines(indexed))
    opened = kyo("discuss", "open", "--title", "Whether the cache stays", "--json")
    assert opened.code == 0, opened
    (ref,) = _refs(kyo, opened.json["id"])
    line = index_line(indexed, PROMPT_3)
    assert line["origin"] == "human"
    assert ref["role"] == "opened" and ref["index_state"] == "indexed" and not ref["late"]
    assert (ref["session"], ref["prompt_id"], ref["turn"]) == (SESSION, PROMPT_3, "p-3")
    assert ref["at"] == line["at"] and ref["source"] == "hook"
    assert kyo("show", opened.json["id"], "--json").json["kind"] == "discussion"


def test_a_task_notification_is_never_the_turn_stamped(kyo, indexed: Path) -> None:
    """With the person's last turn earlier than a notification, the notification is passed
    over: only a line confirmed as the person's is stamped."""
    lines = cmd_turns.read_lines(indexed)
    cmd_turns._rewrite(indexed, [line for line in lines if line["prompt_id"] != PROMPT_3])
    write_transcript(
        [
            person("p-1", PROMPT_1, "should the cache stay?", "2026-11-06T10:00:00.000Z"),
            notification("n-2", PROMPT_2, "2026-11-06T10:00:40.000Z"),
        ]
    )
    (indexed / "kyosei" / cmd_turns.TURNS_CURSOR_NAME).unlink(missing_ok=True)
    opened = kyo("discuss", "open", "--title", "x", "--json")
    assert opened.code == 0, opened
    assert _refs(kyo, opened.json["id"])[0]["prompt_id"] == PROMPT_1


def test_no_flag_of_kyo_discuss_takes_a_time(kyo) -> None:
    """Nothing is typed by an agent: the session comes from the environment and the time
    from the index, so the command has no flag a time could be given in."""
    parser = build_parser()
    discuss = parser._subparsers._group_actions[0].choices["discuss"]  # noqa: SLF001
    flags = {o for a in discuss._actions for o in a.option_strings}  # noqa: SLF001
    assert not flags & {"--time", "--when", "--started", "--session", "--opened-at"}


def test_at_opens_at_an_earlier_indexed_turn_and_shows_the_lag(kyo, indexed: Path) -> None:
    several = kyo("discuss", "open", "--title", "x", "--at-prompt", PROMPT_1[:12])
    assert several.code == 64 and "matches several" in several.err
    opened = kyo("discuss", "open", "--title", "x", "--at-prompt", "p-1", "--json")
    assert opened.code == 0, opened
    ref = _refs(kyo, opened.json["id"])[0]
    assert ref["prompt_id"] == PROMPT_1 and ref["turn"] == "p-1"
    shown = kyo("show", opened.json["id"])
    assert f"prompt {PROMPT_1} at {ref['at']}" in shown.out
    refused = kyo("discuss", "open", "--title", "x", "--at-prompt", "zzzz")
    assert refused.code != 0 and "matches none" in refused.err and PROMPT_3 in refused.err


def test_with_no_index_the_opening_is_recorded_index_absent(kyo, session: str) -> None:
    opened = kyo("discuss", "open", "--title", "x", "--json")
    assert opened.code == 0, opened
    (ref,) = _refs(kyo, opened.json["id"])
    assert ref["index_state"] == "absent" and ref["session"] == SESSION
    assert ref["at"] is None and ref["prompt_id"] is None and ref["turn"] is None
    assert "index absent" in kyo("show", opened.json["id"]).out
    refused = kyo("discuss", "open", "--title", "x", "--at-prompt", PROMPT_1)
    assert refused.code != 0 and "none to name" in refused.err


# ------------------------------------------------------------------ note and close


def test_note_and_close_dropped(kyo, indexed: Path, tmp_path: Path) -> None:
    opened = kyo("discuss", "open", "--title", "x", "--json").json["id"]
    noted = kyo(
        "discuss", "note", opened, "--file", _file(tmp_path, "n.md", "A or B; I recommend A.")
    )
    assert noted.code == 0, noted
    refs = _refs(kyo, opened)
    assert [r["role"] for r in refs] == ["opened", "note"]
    doc = kyo("show", opened, "--json").json
    assert doc["notes"][0]["id"] == refs[1]["note_id"]
    assert "open" in kyo("show", opened).out.split("discussion:")[1].splitlines()[0]

    assert kyo("discuss", "close", opened).code != 0  # neither --dropped nor a spawn
    closed = kyo("discuss", "close", opened, "--dropped", "--why", "the person decided later")
    assert closed.code == 0, closed
    last = _refs(kyo, opened)[-1]
    assert last["role"] == "closed" and last["why"] == "the person decided later"
    assert "closed, nothing produced" in kyo("show", opened).out
    again = kyo("discuss", "close", opened, "--dropped", "--why", "again")
    assert again.code != 0 and "already closed" in again.err


def test_spawning_from_a_discussion_closes_it(kyo, indexed: Path, tmp_path: Path) -> None:
    opened = kyo("discuss", "open", "--title", "x", "--json").json["id"]
    task = kyo(
        "spawn", opened, "--kind", "task", "--title", "Build it",
        "--criteria-file", _file(tmp_path, "c.txt", "it works\n"),
        "--touches-file", _file(tmp_path, "t.txt", ""), "--json",
    )  # fmt: skip
    assert task.code == 0, task
    last = _refs(kyo, opened)[-1]
    assert last["role"] == "closed" and last["produced_id"] == task.json["record_id"]
    assert f"closed, produced {task.json['record_id']}" in kyo("show", opened).out


# ------------------------------------------------------------------ KYO-658.c2, c3


def _decision_flags(project: Path, tmp: Path, *, words: str = DECIDING_WORDS) -> list[str]:
    declare_person(project)
    return [
        "--quote-file", _file(tmp, "q.txt", words),
        "--decision-file", _file(tmp, "d.yml", "outcome: unprompted\n"),
        "--person", TEST_PERSON,
    ]  # fmt: skip


def test_a_decision_with_no_discussion_is_refused_and_opened_late_files_it(
    kyo, project: Path, tmp_path: Path
) -> None:
    flags = [*_decision_flags(project, tmp_path), "--source-other", "a call"]
    refused = kyo("new", "decision", "--title", "Keep the cache", *flags)
    assert refused.code == 2 and "decision-requires-discussion" in refused.err, refused
    assert "--opened-late" in refused.err and "--discussion" in refused.err
    filed = kyo("new", "decision", "--title", "Keep the cache", *flags, "--opened-late", "--json")
    assert filed.code == 0, filed
    discussion = filed.json["discussion_id"]
    shown = kyo("show", filed.json["record_id"], "--json").json
    assert shown["discussion"]["id"] == discussion and shown["discussion"]["late"] is True
    # c3: with no index, the late opening records index absent.
    assert shown["discussion"]["index_state"] == "absent"
    text = kyo("show", filed.json["record_id"]).out
    assert f"discussed in: {discussion} (opened late, after the decision, index absent)" in text
    export = json.loads(
        (project / "kyosei" / "export" / f"{discussion}.json").read_text(encoding="utf-8")
    )
    opened = export["turn_refs"][0]
    assert opened["role"] == "opened" and opened["late"] is True
    assert opened["index_state"] == "absent"
    assert export["turn_refs"][1]["produced_id"] == filed.json["record_id"]
    assert "opened late" in kyo("show", discussion).out and " LATE" in kyo("show", discussion).out


def test_a_decision_needs_no_discussion_where_nobody_is_declared(kyo, tmp_path: Path) -> None:
    filed = kyo(
        "new", "decision", "--title", "Keep it",
        "--evidence-file", _file(tmp_path, "e.md", "the words"),
    )  # fmt: skip
    assert filed.code == 0, filed


def test_opened_late_with_an_index_names_where_it_began(
    kyo, indexed: Path, tmp_path: Path
) -> None:
    flags = _decision_flags(indexed, tmp_path)
    refused = kyo("new", "decision", "--title", "Keep the cache", *flags, "--opened-late")
    assert refused.code == 2 and "--at-prompt <prompt>" in refused.err and PROMPT_1 in refused.err
    filed = kyo(
        "new", "decision", "--title", "Keep the cache", *flags, "--opened-late",
        "--at-prompt", PROMPT_1, "--json",
    )  # fmt: skip
    assert filed.code == 0, filed
    refs = _refs(kyo, filed.json["discussion_id"])
    assert refs[0]["late"] is True and refs[0]["prompt_id"] == PROMPT_1
    assert refs[1]["role"] == "closed" and refs[1]["prompt_id"] == PROMPT_3


def test_a_named_discussion_is_linked_and_closed(kyo, project: Path, tmp_path: Path) -> None:
    discussion = kyo("discuss", "open", "--title", "x", "--json").json["id"]
    flags = [*_decision_flags(project, tmp_path), "--source-other", "a call"]
    filed = kyo(
        "new", "decision", "--title", "Keep", *flags, "--discussion", discussion, "--json"
    )
    assert filed.code == 0, filed
    shown = kyo("show", filed.json["record_id"], "--json").json
    assert shown["discussion"] == {
        **shown["discussion"], "id": discussion, "late": False, "open": False,
    }  # fmt: skip
    task = kyo("new", "task", "--title", "t", "--discussion", discussion)
    assert task.code == 64 and "requires_discussion" in task.err


# ------------------------------------------------------------------ KYO-658.c4


def test_a_decided_credit_from_a_discussion_takes_its_span_from_the_turn_references(
    kyo, indexed: Path, tmp_path: Path
) -> None:
    opened = kyo("discuss", "open", "--title", "x", "--at-prompt", PROMPT_1, "--json").json["id"]
    decided = kyo(
        "spawn", opened, "--kind", "decision", "--title", "Keep the cache",
        *_decision_flags(indexed, tmp_path), "--json",
    )  # fmt: skip
    assert decided.code == 0, decided
    credit = kyo("show", decided.json["record_id"], "--json").json["credits"][0]
    assert credit["source_turn"] == "p-3"
    assert (credit["span_session"], credit["span_first_turn"], credit["span_last_turn"]) == (
        SESSION, "p-1", "p-3",
    )  # fmt: skip
    # Measured as an explicit span is: the person's two turns and the agent's reply between.
    assert credit["span_person_turns"] == 2
    assert credit["span_started_at"] == "2026-11-06T10:00:00.000Z"
    assert credit["span_ended_at"] == "2026-11-06T10:01:00.000Z"
    last = _refs(kyo, opened)[-1]
    assert last["role"] == "closed" and last["produced_id"] == decided.json["record_id"]


def test_an_explicit_span_is_kept_over_the_derived_one(kyo, indexed: Path, tmp_path: Path) -> None:
    opened = kyo("discuss", "open", "--title", "x", "--at-prompt", PROMPT_1, "--json").json["id"]
    decided = kyo(
        "spawn", opened, "--kind", "decision", "--title", "Keep the cache",
        *_decision_flags(indexed, tmp_path), "--span-first", "a-1", "--span-last", "p-3",
        "--json",
    )  # fmt: skip
    assert decided.code == 0, decided
    credit = kyo("show", decided.json["record_id"], "--json").json["credits"][0]
    assert credit["span_first_turn"] == "a-1"


def test_a_decision_spawned_from_a_proposal_the_discussion_produced_inherits_it(
    kyo, indexed: Path, tmp_path: Path
) -> None:
    opened = kyo("discuss", "open", "--title", "x", "--at-prompt", PROMPT_1, "--json").json["id"]
    proposal = kyo(
        "spawn", opened, "--kind", "proposal", "--title", "Design it",
        "--criteria-file", _file(tmp_path, "c.txt", "it is argued\n"),
        "--touches-file", _file(tmp_path, "t.txt", ""), "--json",
    ).json["record_id"]  # fmt: skip
    decided = kyo(
        "spawn", proposal, "--kind", "decision", "--title", "Keep the cache",
        *_decision_flags(indexed, tmp_path), "--json",
    )  # fmt: skip
    assert decided.code == 0, decided
    assert decided.json["discussion_id"] == opened
    credit = kyo("show", decided.json["record_id"], "--json").json["credits"][0]
    assert credit["span_first_turn"] == "p-1"


# ------------------------------------------------------------------ the usage refusals


def test_each_usage_refusal_is_cleared_by_the_invocation_it_names(
    kyo, project: Path, tmp_path: Path, session: str
) -> None:
    """The re-invocations the refusals name, each run from the state that printed it."""
    no_title = kyo("discuss", "open")
    assert no_title.code == 64 and "--title" in no_title.err
    opened = kyo("discuss", "open", "--title", "x", "--json")
    assert opened.code == 0, opened
    discussion = opened.json["id"]

    no_id = kyo("discuss", "note", "--file", _file(tmp_path, "n.md", "A or B"))
    assert no_id.code == 64 and "give its id" in no_id.err
    assert kyo("discuss", "note", discussion, "--file", _file(tmp_path, "n.md", "A or B")).code == 0

    no_id = kyo("discuss", "close", "--dropped", "--why", "nothing came of it")
    assert no_id.code == 64 and "give its id" in no_id.err

    flags = [*_decision_flags(project, tmp_path), "--source-other", "a call"]
    both = kyo(
        "new", "decision", "--title", "d", *flags, "--discussion", discussion, "--opened-late"
    )
    assert both.code == 64 and "give one" in both.err
    assert kyo("new", "decision", "--title", "d", *flags, "--discussion", discussion).code == 0

    task = kyo("new", "task", "--title", "t", "--opened-late")
    assert task.code == 64 and "requires_discussion" in task.err
    assert kyo("new", "task", "--title", "t").code == 0

    again = kyo("discuss", "open", "--title", "y", "--json").json["id"]
    assert kyo("discuss", "close", again, "--dropped", "--why", "nothing came of it").code == 0


def test_a_registry_without_the_discussion_kind_is_cleared_by_copying_the_entry(
    kyo, project: Path, session: str
) -> None:
    """A store scaffolded before KYO-658 has no `discussion` kind; the refusal says to copy
    the packaged entry, and copying it (with its edges) lets a discussion open."""
    import yaml

    path = project / "kyosei" / "kinds.yml"
    packaged = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(packaged)
    raw["kinds"].pop("discussion")
    for entry in raw["kinds"].values():
        entry["accepts_from"] = [k for k in entry.get("accepts_from") or [] if k != "discussion"]
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    refused = kyo("discuss", "open", "--title", "x")
    assert refused.code == 1 and "copy the `discussion:` entry" in refused.err
    path.write_text(packaged, encoding="utf-8")
    assert kyo("discuss", "open", "--title", "x").code == 0


def test_the_one_writer_refuses_an_absent_reference_carrying_a_stamp(kyo, project: Path) -> None:
    from kyosei.errors import KyoseiError
    from kyosei.store import open_store

    discussion = kyo("discuss", "open", "--title", "x", "--json").json["id"]
    store = open_store(project)
    try:
        with pytest.raises(KyoseiError, match="stamps nothing from the index"):
            store.add_turn_ref(
                discussion, role="note", index_state="absent", author="t", at="2026-11-06T10:00:00Z"
            )
        with store.transaction():
            ref = store.add_turn_ref(discussion, role="note", index_state="absent", author="t")
        assert ref == f"{discussion}.s2"
    finally:
        store.close()


# ------------------------------------------------------------------------- KYO-749.c2


def _spawn_task(kyo, opened: str, tmp_path: Path, title: str, *extra: str):
    return kyo(
        "spawn", opened, "--kind", "task", "--title", title, *extra,
        "--criteria-file", _file(tmp_path, "c.txt", "it works\n"),
        "--touches-file", _file(tmp_path, "t.txt", ""), "--force", "--json",
    )  # fmt: skip


def test_keep_open_files_the_record_and_leaves_the_discussion_open(
    kyo, indexed: Path, tmp_path: Path
) -> None:
    opened = kyo("discuss", "open", "--title", "x", "--json").json["id"]
    first = _spawn_task(kyo, opened, tmp_path, "Server side", "--keep-open")
    assert first.code == 0, first
    assert [r["role"] for r in _refs(kyo, opened)] == ["opened"]
    second = _spawn_task(kyo, opened, tmp_path, "App side")
    assert second.code == 0, second
    last = _refs(kyo, opened)[-1]
    assert last["role"] == "closed" and last["produced_id"] == second.json["record_id"]


def test_without_keep_open_the_output_says_the_discussion_was_closed(
    kyo, indexed: Path, tmp_path: Path
) -> None:
    opened = kyo("discuss", "open", "--title", "x", "--json").json["id"]
    kept = kyo(
        "spawn", opened, "--kind", "task", "--title", "One", "--keep-open",
        "--criteria-file", _file(tmp_path, "c.txt", "it works\n"),
        "--touches-file", _file(tmp_path, "t.txt", ""),
    )  # fmt: skip
    assert kept.code == 0 and f"left open: {opened}" in kept.out, kept
    plain = kyo(
        "spawn", opened, "--kind", "task", "--title", "Two",
        "--criteria-file", _file(tmp_path, "c.txt", "it works\n"),
        "--touches-file", _file(tmp_path, "t.txt", ""),
    )  # fmt: skip
    assert plain.code == 0 and f"this filing closed {opened}" in plain.out, plain


def test_a_record_spawned_from_a_closed_discussion_is_refused_naming_discuss_open(
    kyo, indexed: Path, tmp_path: Path
) -> None:
    opened = kyo("discuss", "open", "--title", "x", "--json").json["id"]
    assert _spawn_task(kyo, opened, tmp_path, "First").code == 0
    refused = _spawn_task(kyo, opened, tmp_path, "Second")
    assert refused.code != 0
    assert "spawn-from-closed-discussion" in refused.err and "kyo discuss open" in refused.err
    assert "--keep-open" in refused.err
    # the remedy it names runs: a fresh discussion takes the record
    fresh = kyo("discuss", "open", "--title", "y", "--json").json["id"]
    assert _spawn_task(kyo, fresh, tmp_path, "Second").code == 0


def test_keep_open_with_no_discussion_is_refused(kyo, indexed: Path, tmp_path: Path) -> None:
    bug = kyo("new", "bug", "--title", "A bug", "--json").json["id"]
    refused = kyo(
        "spawn", bug, "--kind", "task", "--title", "T", "--keep-open",
        "--criteria-file", _file(tmp_path, "c.txt", "it works\n"),
        "--touches-file", _file(tmp_path, "t.txt", ""),
    )  # fmt: skip
    assert refused.code != 0 and "--keep-open" in refused.err
    discussion = kyo("discuss", "open", "--title", "x", "--json").json["id"]
    assert _spawn_task(kyo, discussion, tmp_path, "T", "--keep-open").code == 0


# ------------------------------------------------------------------ KYO-878


def _open(kyo) -> str:
    return kyo("discuss", "open", "--title", "x", "--json").json["id"]


def _task(kyo, title: str) -> str:
    return new_record(kyo, kind="task", title=title, criteria=["it works"])


def test_kyo878_c1_produced_links_each_record_and_show_says_what_it_produced(
    kyo, indexed: Path
) -> None:
    opened = _open(kyo)
    a, b = _task(kyo, "First"), _task(kyo, "Second")

    closed = kyo("discuss", "close", opened, "--produced", a, b, "--json")

    assert closed.code == 0, closed
    assert closed.json["produced"] == [a, b]
    doc = kyo("show", opened, "--json").json
    links = {(x["other"], x["type"], x["direction"]) for x in doc["links"]}
    assert {(a, "produced-by", "incoming"), (b, "produced-by", "incoming")} <= links
    shown = kyo("show", opened).out
    assert f"closed, produced {a}, {b}" in shown
    assert "nothing produced" not in shown
    for rid in (a, b):
        assert any(
            x["type"] == "produced-by" and x["other"] == opened
            for x in kyo("show", rid, "--json").json["links"]
        )


def test_kyo878_c2_why_covers_what_is_not_a_record_and_the_two_flags_exclude_each_other(
    kyo, indexed: Path
) -> None:
    opened = _open(kyo)
    task = _task(kyo, "Built")
    both = kyo("discuss", "close", opened, "--produced", task, "--dropped", "--why", "x")
    assert both.code == 64 and "give one" in both.err
    assert "open" in kyo("show", opened).out  # nothing was written
    empty = kyo("discuss", "close", opened, "--produced")
    assert empty.code == 64 and "--why" in empty.err

    closed = kyo("discuss", "close", opened, "--produced", "--why", "ideas/idea-file.md")
    assert closed.code == 0, closed
    shown = kyo("show", opened).out
    assert "closed, produced ideas/idea-file.md" in shown
    assert "nothing produced" not in shown

    other = _open(kyo)
    both_why = kyo("discuss", "close", other, "--produced", task, "--why", "also a file")
    assert both_why.code == 0, both_why
    assert f"closed, produced {task}" in kyo("show", other).out


def test_kyo878_c3_dropped_reads_as_it_did_and_an_unknown_record_writes_nothing(
    kyo, indexed: Path
) -> None:
    opened = _open(kyo)
    refused = kyo("discuss", "close", opened, "--produced", "TST-999")
    assert refused.code != 0
    assert "open" in kyo("show", opened).out.split("discussion:")[1].splitlines()[0]
    closed = kyo("discuss", "close", opened, "--dropped", "--why", "nothing came of it")
    assert closed.code == 0 and f"closed {opened} with nothing produced" in closed.out
    assert "closed, nothing produced" in kyo("show", opened).out
