"""A credit names its turns and kyo copies the person's words from the transcript (KYO-900,
decision KYO-898).

The transcripts written here reuse the record shapes of ``tests/test_kyo658_discussions.py``;
the words are invented.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from kyosei import cmd_turns, credits

from .conftest import TEST_PERSON, declare_person, new_record, opt_in_transcripts
from .test_kyo658_discussions import (
    PROMPT_1,
    PROMPT_2,
    PROMPT_3,
    SESSION,
    _file,
    _record,
    agent_text,
    append_transcript,
    notification,
    person,
    prompt_hook,
    write_transcript,
)

SPEC_WORDS = "the cache should stay, keyed by commit, and be dropped on a schema bump"
APPROVAL = "looks good"
PROMPT_4 = "bbbbbbbb-0000-4000-8000-000000000004"
T1 = "2026-11-06T10:00:00.000Z"
T3 = "2026-11-06T10:01:00.000Z"
T4 = "2026-11-06T10:02:00.000Z"


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    return SESSION


@pytest.fixture
def talked(kyo, project: Path, session: str) -> Path:
    """An opted-in project that declares a person, and one session: the person specifies
    the matter (p-1), a task notification arrives (n-2), and the person approves in two
    words (p-3). No Stop hook has run, so the index has not confirmed whose each prompt
    was."""
    opt_in_transcripts(project)
    declare_person(project)
    write_transcript(
        [
            person("p-1", PROMPT_1, SPEC_WORDS, T1),
            agent_text("a-1", "I recommend keeping it.", "2026-11-06T10:00:30.000Z"),
            notification("n-2", PROMPT_2, "2026-11-06T10:00:40.000Z"),
            agent_text("a-2", "The run finished.", "2026-11-06T10:00:50.000Z"),
        ]
    )
    prompt_hook(kyo, PROMPT_1)
    prompt_hook(kyo, PROMPT_2)
    append_transcript([person("p-3", PROMPT_3, APPROVAL, T3)])
    prompt_hook(kyo, PROMPT_3)
    return project


def _criterion(kyo) -> tuple[str, str]:
    record = new_record(kyo, kind="task", title="Keep the cache", criteria=["it stays"])
    return record, f"{record}.c1"


def _credit(kyo, record: str, index: int = -1) -> dict:
    return kyo("show", record, "--json").json["credits"][index]


def _words(kyo, record: str, quote_id: str) -> str:
    shown = kyo("show", record, "--json").json
    return next(e["text"] for e in shown["evidence_artifacts"] if e["id"] == quote_id)


def _human_gate(kyo, record: str) -> str:
    done = kyo("gate", record, "awaiting:human-review", "--do", "look at it", "--json")
    assert done.code == 0, done
    return done.json["gate_id"]


# ------------------------------------------------------------------ c2: the latest turn


def test_with_no_turn_and_no_quote_the_persons_latest_turn_is_taken(kyo, talked: Path) -> None:
    """The index has not confirmed PROMPT_3 as the person's until the transcript is folded
    in, which the credit does first, so the prompt being answered is the one taken."""
    assert cmd_turns.read_lines(talked)[-1]["origin"] == "unconfirmed"
    record, cid = _criterion(kyo)
    done = kyo("verify", cid, "--verdict", "met", "--method", "human-observed")
    assert done.code == 0, done
    credit = _credit(kyo, record)
    assert credit["source_session"] == SESSION and credit["source_turn"] == "p-3"
    assert credit["transcript_check"] in ("matched", "matched-paste-unknown")
    assert credit["observed_at"] == T3 and credit["words_form"] == "typed"
    assert _words(kyo, record, credit["quote_id"]) == APPROVAL
    (turn,) = credit["turns"]
    assert turn == {
        **turn, "role": "approval", "session": SESSION, "turn": "p-3", "prompt_id": PROMPT_3,
        "at": T3, "quote_id": credit["quote_id"],
    }  # fmt: skip
    assert "latest turn" in done.out and "no --turn was given" in done.out


# ------------------------------------------------------------------ c1: every command


def test_confirm_takes_a_turn_reference_and_no_quote(kyo, talked: Path) -> None:
    record, cid = _criterion(kyo)
    done = kyo("confirm", record, "--criterion", cid, "--turn", PROMPT_1, "--no-close")
    assert done.code == 0, done
    credit = _credit(kyo, record)
    assert credit["source_turn"] == "p-1"
    assert _words(kyo, record, credit["quote_id"]) == SPEC_WORDS
    assert kyo("show", cid.rsplit(".", 1)[0], "--json").json["criteria"][0]["state"] == "met"


def test_verify_takes_a_prompt_id_and_a_record_uuid(kyo, talked: Path) -> None:
    """A unique prefix is taken by `test_a_message_sent_mid_turn_is_named_by_its_record_uuid`."""
    line = ("--verdict", "met", "--method", "human-observed", "--turn")
    record, cid = _criterion(kyo)
    assert kyo("verify", cid, *line, "p-1").code == 0
    assert _credit(kyo, record)["source_turn"] == "p-1"
    other, other_cid = _criterion(kyo)
    done = kyo("verify", other_cid, *line, PROMPT_3)
    assert done.code == 0, done
    assert _credit(kyo, other)["source_turn"] == "p-3"


def test_a_prefix_that_names_several_turns_or_none_is_refused_and_a_longer_one_clears_it(
    kyo, talked: Path
) -> None:
    record, cid = _criterion(kyo)
    line = ("verify", cid, "--verdict", "met", "--method", "human-observed", "--turn")
    several = kyo(*line, "aaaaaaaa")
    assert several.code == 64 and "names 2 of the person's turns" in several.err
    assert PROMPT_1 in several.err and PROMPT_3 in several.err
    none = kyo(*line, "cccccccc")
    assert none.code == 64 and "names no turn" in none.err and PROMPT_3 in none.err
    assert kyo(*line, PROMPT_3).code == 0


def test_ungate_takes_a_turn_reference_and_the_latest_turn_by_default(kyo, talked: Path) -> None:
    record, _cid = _criterion(kyo)
    gate = _human_gate(kyo, record)
    done = kyo("ungate", gate, "--turn", PROMPT_3)
    assert done.code == 0, done
    credit = _credit(kyo, record)
    assert credit["source_turn"] == "p-3" and credit["subjects"] == [{"kind": "gate", "id": gate}]
    assert f'"{APPROVAL}"' in done.out and T3 in done.out
    second = _human_gate(kyo, record)
    bare = kyo("ungate", second)
    assert bare.code == 0, bare
    assert _credit(kyo, record)["source_turn"] == "p-3" and "latest turn" in bare.out


def test_a_decision_is_filed_and_spawned_with_no_file_at_all(kyo, talked: Path) -> None:
    """c1 and c10 together: the words from the turn, the outcome from the flags."""
    filed = kyo(
        "new", "decision", "--title", "Keep the cache", "--outcome", "accepted",
        "--recommended", "keep it, keyed by commit", "--recommended-by", "orchestrator",
        "--turn", PROMPT_1, "--turn", PROMPT_3, "--json",
    )  # fmt: skip
    assert filed.code == 0, filed
    record = filed.json["record_id"]
    credit = _credit(kyo, record)
    assert credit["role"] == "decided" and credit["outcome"] == "accepted"
    assert credit["recommended"] == "keep it, keyed by commit"
    assert credit["recommended_by"] == "orchestrator" and credit["alternatives"] == []
    assert credit["source_turn"] == "p-3"
    assert _words(kyo, record, credit["quote_id"]) == APPROVAL
    proposal = new_record(kyo, kind="proposal", title="A proposal", criteria=["approved"])
    spawned = kyo(
        "spawn", proposal, "--kind", "decision", "--title", "Keep it", "--outcome",
        "unprompted", "--json",
    )  # fmt: skip
    assert spawned.code == 0, spawned
    assert _credit(kyo, spawned.json["record_id"])["outcome"] == "unprompted"
    assert _credit(kyo, spawned.json["record_id"])["source_turn"] == "p-3"


def test_kyo_credit_takes_turn_references_for_a_row_that_exists(kyo, talked: Path) -> None:
    approval = kyo(
        "new", "decision", "--title", "Credit the backfill", "--outcome", "unprompted", "--json"
    ).json["credit_id"]
    record, cid = _criterion(kyo)
    verified = kyo("verify", cid, "--verdict", "met", "--method", "agent-observed", "--json")
    vid = verified.json["verification_id"]
    conn = sqlite3.connect(talked / "kyosei" / "kyosei.db")
    conn.execute("UPDATE verifications SET method = 'human-observed' WHERE id = ?", (vid,))
    conn.commit()
    conn.close()
    done = kyo("credit", vid, "--turn", PROMPT_3, "--turn", PROMPT_1, "--basis", approval)
    assert done.code == 0, done
    credit = _credit(kyo, record)
    assert [t["turn"] for t in credit["turns"]] == ["p-1", "p-3"]
    assert f'"{SPEC_WORDS}"' in done.out and f'"{APPROVAL}"' in done.out


# ------------------------------------------------------------------ c3: the receipt


def test_the_receipt_prints_each_turns_time_and_first_line(kyo, talked: Path) -> None:
    record, cid = _criterion(kyo)
    done = kyo(
        "verify", cid, "--verdict", "met", "--method", "human-observed",
        "--turn", PROMPT_3, "--turn", PROMPT_1,
    )  # fmt: skip
    assert done.code == 0, done
    lines = done.out.splitlines()
    context = next(line for line in lines if line.lstrip().startswith("context"))
    approval = next(line for line in lines if line.lstrip().startswith("approval"))
    assert T1 in context and f'"{SPEC_WORDS}"' in context and PROMPT_1[:8] in context
    assert T3 in approval and f'"{APPROVAL}"' in approval
    assert lines.index(context) < lines.index(approval)
    other, other_cid = _criterion(kyo)
    confirmed = kyo("confirm", other, "--criterion", other_cid, "--turn", PROMPT_3, "--no-close")
    assert confirmed.code == 0 and T3 in confirmed.out and f'"{APPROVAL}"' in confirmed.out


def test_a_sole_turn_of_a_few_words_gets_one_line_naming_context_and_refuses_nothing(
    kyo, talked: Path
) -> None:
    record, cid = _criterion(kyo)
    short = kyo("verify", cid, "--verdict", "met", "--method", "human-observed")
    assert short.code == 0 and short.err == ""
    cue = [line for line in short.out.splitlines() if "can cite them as context" in line]
    assert len(cue) == 1 and "the cited turn is 2 word(s)" in cue[0] and "--turn <id>" in cue[0]
    _other, other_cid = _criterion(kyo)
    line = ("verify", other_cid, "--verdict", "met", "--method", "human-observed", "--turn")
    with_context = kyo(*line, PROMPT_3, "--turn", PROMPT_1)
    assert with_context.code == 0 and "can cite them as context" not in with_context.out
    _third, third_cid = _criterion(kyo)
    long_turn = kyo("verify", third_cid, "--verdict", "met", "--method", "human-observed",
                    "--turn", PROMPT_1)  # fmt: skip
    assert long_turn.code == 0 and "can cite them as context" not in long_turn.out


# ------------------------------------------------------------------ c4: several turns


def test_several_turns_are_stored_in_transcript_order_whatever_order_they_were_named_in(
    kyo, talked: Path
) -> None:
    record, cid = _criterion(kyo)
    done = kyo(
        "verify", cid, "--verdict", "met", "--method", "human-observed",
        "--turn", PROMPT_3, "--turn", "p-1",
    )  # fmt: skip
    assert done.code == 0, done
    credit = _credit(kyo, record)
    context, approval = credit["turns"]
    assert (context["role"], context["session"], context["turn"], context["at"]) == (
        "context", SESSION, "p-1", T1,
    )  # fmt: skip
    assert (approval["role"], approval["session"], approval["turn"], approval["at"]) == (
        "approval", SESSION, "p-3", T3,
    )  # fmt: skip
    assert approval["quote_id"] == credit["quote_id"] and context["quote_id"] != credit["quote_id"]
    assert _words(kyo, record, context["quote_id"]) == SPEC_WORDS
    assert _words(kyo, record, approval["quote_id"]) == APPROVAL
    text = kyo("show", record).out
    assert "turns cited (2), in transcript order:" in text
    shown = [line.strip() for line in text.splitlines() if T1 in line or T3 in line]
    assert any(line.startswith("context") and f'"{SPEC_WORDS}"' in line for line in shown)
    assert any(line.startswith("approval") and f'"{APPROVAL}"' in line for line in shown)


def test_cited_turns_survive_export_and_rebuild(kyo, talked: Path) -> None:
    record, cid = _criterion(kyo)
    assert kyo(
        "verify", cid, "--verdict", "met", "--method", "human-observed",
        "--turn", PROMPT_1, "--turn", PROMPT_3,
    ).code == 0  # fmt: skip
    before = _credit(kyo, record)
    exported = json.loads((talked / "kyosei" / "export" / f"{record}.json").read_text("utf-8"))
    assert exported["credits"][-1]["turns"] == before["turns"]
    assert kyo("rebuild", "--force").code == 0
    assert _credit(kyo, record) == before
    assert kyo("doctor").code == 0


def test_a_turn_of_another_session_is_cited_with_its_own_session(
    kyo, talked: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A matter specified in one session and approved in the next: the context turn is
    found in the turn index, and stored with the session it is in."""
    later = "9d9d9d9d-2222-4a2b-9c3d-0123456789ab"
    target = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-a-project" / f"{later}.jsonl"
    target.write_text(
        json.dumps({**person("q-1", PROMPT_4, "yes, go ahead", T4), "sessionId": later}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", later)
    payload = {
        "session_id": later, "hook_event_name": "UserPromptSubmit", "prompt_id": PROMPT_4,
        "prompt": "yes, go ahead", "transcript_path": str(target),
    }  # fmt: skip
    assert kyo("turns", "--hook", stdin=json.dumps(payload)).code == 0
    record, cid = _criterion(kyo)
    done = kyo(
        "verify", cid, "--verdict", "met", "--method", "human-observed",
        "--turn", PROMPT_4, "--turn", PROMPT_1,
    )  # fmt: skip
    assert done.code == 0, done
    credit = _credit(kyo, record)
    assert [(t["role"], t["session"], t["turn"]) for t in credit["turns"]] == [
        ("context", SESSION, "p-1"),
        ("approval", later, "q-1"),
    ]
    assert credit["source_session"] == later
    assert f"session {SESSION[:8]}" in kyo("show", record).out


# ------------------------------------------------------------------ c5: not the person's


def test_a_turn_that_is_not_the_persons_is_refused_and_the_remedy_clears_it(
    kyo, talked: Path
) -> None:
    record, cid = _criterion(kyo)
    line = ("verify", cid, "--verdict", "met", "--method", "human-observed")
    refused = kyo(*line, "--turn", PROMPT_2)
    assert refused.code == 2 and credits.CONTRACT_CREDIT_TURN_NOT_THE_PERSONS in refused.err
    assert "task-notification" in refused.err and "nothing was written" in refused.err
    assert PROMPT_3 in refused.err and "--turn" in refused.err
    assert "kyo turns --said" in refused.err and PROMPT_3 in kyo("turns", "--said").out
    assert kyo("show", record, "--json").json["credits"] == []
    assert kyo(*line, "--turn", PROMPT_3).code == 0


# ------------------------------------------------------------------ c6: --quote-file


def test_a_quote_file_beside_a_turn_narrows_what_is_stored(
    kyo, talked: Path, tmp_path: Path
) -> None:
    filed = kyo(
        "new", "decision", "--title", "Key the cache", "--outcome", "unprompted",
        "--turn", PROMPT_1, "--turn", PROMPT_3,
        "--quote-file", _file(tmp_path, "q.txt", "keyed by commit"), "--json",
    )  # fmt: skip
    assert filed.code == 0, filed
    record = filed.json["record_id"]
    credit = _credit(kyo, record)
    # The words are in p-1, so p-1 carries the judgement and p-3 is context beside it.
    assert credit["source_turn"] == "p-1"
    assert _words(kyo, record, credit["quote_id"]) == "keyed by commit"
    assert [(t["role"], t["turn"]) for t in credit["turns"]] == [
        ("approval", "p-1"), ("context", "p-3"),
    ]  # fmt: skip


def test_a_quote_that_is_in_no_named_turn_is_refused(kyo, talked: Path, tmp_path: Path) -> None:
    record, cid = _criterion(kyo)
    line = ("verify", cid, "--verdict", "met", "--method", "human-observed")
    refused = kyo(
        *line, "--turn", PROMPT_3, "--evidence-file", _file(tmp_path, "q.txt", "keyed by commit")
    )
    assert refused.code == 2 and credits.CONTRACT_CREDIT_QUOTE_NOT_FOUND in refused.err
    assert "none of the turns --turn names" in refused.err
    assert kyo(*line, "--turn", PROMPT_1, "--evidence-file", str(tmp_path / "q.txt")).code == 0


def test_a_quote_file_alone_is_checked_against_the_transcript_as_before(
    kyo, talked: Path, tmp_path: Path
) -> None:
    record, cid = _criterion(kyo)
    done = kyo(
        "verify", cid, "--verdict", "met", "--method", "human-observed",
        "--evidence-file", _file(tmp_path, "q.txt", "keyed by commit"),
    )  # fmt: skip
    assert done.code == 0, done
    credit = _credit(kyo, record)
    assert credit["source_turn"] == "p-1" and credit["transcript_check"].startswith("matched")
    assert credit["turns"] == []
    assert "turns cited" not in kyo("show", record).out


# ------------------------------------------------------------------ c7: no turn to read


def test_a_project_that_does_not_read_transcripts_is_told_to_pass_the_quote_file(
    kyo, project: Path, session: str, tmp_path: Path
) -> None:
    declare_person(project)
    record, cid = _criterion(kyo)
    line = ("verify", cid, "--verdict", "met", "--method", "human-observed")
    refused = kyo(*line)
    assert refused.code == 2 and "human-observed-requires-credit" in refused.err
    assert "does not read transcripts" in refused.err and "--quote-file FILE" in refused.err
    assert "--source-other" in refused.err
    words = _file(tmp_path, "q.txt", "it is fast")
    assert kyo(*line, "--evidence-file", words).code == 0
    assert _credit(kyo, record)["transcript_check"] == "not-applicable"


def test_outside_a_session_the_refusal_names_both_flags(
    kyo, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    opt_in_transcripts(project)
    declare_person(project)
    record, cid = _criterion(kyo)
    line = ("verify", cid, "--verdict", "met", "--method", "human-observed")
    refused = kyo(*line)
    assert refused.code == 2 and "no Claude Code session is known" in refused.err
    assert "--quote-file FILE" in refused.err and "--source-other WHERE" in refused.err
    words = _file(tmp_path, "q.txt", "it is fast")
    assert kyo(*line, "--evidence-file", words, "--source-other", "a call").code == 0
    bare = kyo("confirm", record)
    assert bare.code == 64 and "--quote-file FILE" in bare.err


# ------------------------------------------------------------------ c8: the commitment


def test_a_credit_carries_the_index_lines_commitment_and_verify_passes(
    kyo, talked: Path
) -> None:
    record, cid = _criterion(kyo)
    assert kyo(
        "verify", cid, "--verdict", "met", "--method", "human-observed",
        "--turn", PROMPT_1, "--turn", PROMPT_3,
    ).code == 0  # fmt: skip
    lines = {line["prompt_id"]: line for line in cmd_turns.read_lines(talked)}
    assert lines[PROMPT_1]["root"] and lines[PROMPT_3]["root"]
    turns = _credit(kyo, record)["turns"]
    assert [(t["root"], t["lines"]) for t in turns] == [
        (lines[PROMPT_1]["root"], lines[PROMPT_1]["lines"]),
        (lines[PROMPT_3]["root"], lines[PROMPT_3]["lines"]),
    ]
    verified = kyo("turns", "--verify")
    assert verified.code == 0, verified
    assert "2 commitment(s) of turns that credits cite match" in verified.out
    assert kyo("turns", "--verify", "--json").json["credit_turns_checked"] == 2


def test_verify_fails_when_a_cited_turns_line_is_removed(kyo, talked: Path) -> None:
    record, cid = _criterion(kyo)
    assert kyo(
        "verify", cid, "--verdict", "met", "--method", "human-observed", "--turn", PROMPT_3
    ).code == 0  # fmt: skip
    credit = _credit(kyo, record)
    kept = [line for line in cmd_turns.read_lines(talked) if line.get("prompt_id") != PROMPT_3]
    cmd_turns._rewrite(talked, kept)
    failed = kyo("turns", "--verify")
    assert failed.code != 0 and credit["id"] in failed.err and "was removed" in failed.err


# ------------------------------------------------------------------ c9: a second confirm


def test_a_second_confirm_with_the_same_turn_reference_skips_what_it_applied(
    kyo, talked: Path
) -> None:
    record, cid = _criterion(kyo)
    line = ("confirm", record, "--criterion", cid, "--turn", PROMPT_3, "--no-close", "--json")
    first = kyo(*line)
    assert first.code == 0, first
    before = kyo("show", record, "--json").json
    second = kyo(*line)
    assert second.code == 0, second
    assert [item["criterion_id"] for item in second.json["skipped"]] == [cid]
    assert "already applied" in second.json["skipped"][0]["reason"]
    after = kyo("show", record, "--json").json
    assert len(after["credits"]) == len(before["credits"]) == 1
    assert after["evidence_artifacts"] == before["evidence_artifacts"]
    assert after["criteria"] == before["criteria"]


# ------------------------------------------------------------------ c10: the decision flags


def test_the_decision_flags_stand_in_for_a_decision_file(kyo, talked: Path, tmp_path: Path) -> None:
    line = ("new", "decision", "--title", "Key the cache")
    none = kyo(*line)
    assert none.code == 2 and "--outcome" in none.err and "--decision-file" in none.err
    both = kyo(
        *line, "--outcome", "unprompted",
        "--decision-file", _file(tmp_path, "d.yml", "outcome: unprompted\n"),
    )  # fmt: skip
    assert both.code == 64 and "give the file or the flags" in both.err
    alone = kyo(*line, "--recommended", "keep it")
    assert alone.code == 64 and "go with --outcome" in alone.err
    unanswered = kyo(*line, "--outcome", "accepted")
    assert unanswered.code == 64 and "names none in `recommended`" in unanswered.err
    filed = kyo(*line, "--outcome", "accepted", "--recommended", "keep it", "--json")
    assert filed.code == 0, filed
    credit = _credit(kyo, filed.json["record_id"])
    assert (credit["outcome"], credit["recommended"]) == ("accepted", "keep it")
    assert credit["recommended_by"]  # the acting author, as a decision file defaults it
    on_a_task = kyo("new", "task", "--title", "t", "--outcome", "unprompted")
    assert on_a_task.code == 64 and "--outcome" in on_a_task.err


# ------------------------------------------------------------------ what is stored of a turn


def test_a_message_sent_mid_turn_is_named_by_its_record_uuid(kyo, talked: Path) -> None:
    """Its index line has a record uuid and no prompt id, and no commitment: it is added
    from the transcript when the transcript is folded in."""
    queued = _record(
        "attachment",
        "dddddddd-0000-4000-8000-00000000000d",
        "2026-11-06T10:01:30.000Z",
        attachment={
            "type": "queued_command", "commandMode": "prompt", "origin": {"kind": "human"},
            "prompt": "and drop it on a schema bump",
        },
    )  # fmt: skip
    append_transcript([queued])
    record, cid = _criterion(kyo)
    line = ("verify", cid, "--verdict", "met", "--method", "human-observed")
    done = kyo(*line, "--turn", "dddddddd")
    assert done.code == 0, done
    credit = _credit(kyo, record)
    (turn,) = credit["turns"]
    assert turn["turn"] == "dddddddd-0000-4000-8000-00000000000d" and turn["prompt_id"] is None
    assert turn["words_form"] == "mid-turn" and turn["root"] is None
    assert _words(kyo, record, credit["quote_id"]) == "and drop it on a schema bump"
    assert "turn dddddddd" in done.out and "mid-turn" in done.out
    # It is also the person's latest turn of the session now.
    other, other_cid = _criterion(kyo)
    assert kyo("verify", other_cid, "--verdict", "met", "--method", "human-observed").code == 0
    assert _credit(kyo, other)["source_turn"] == "dddddddd-0000-4000-8000-00000000000d"


def test_a_long_turn_is_stored_as_its_head_and_tail_and_the_receipt_says_so(
    kyo, talked: Path
) -> None:
    long_words = "start " + ("word " * 2000) + "end"
    append_transcript([person("p-4", PROMPT_4, long_words, T4)])
    prompt_hook(kyo, PROMPT_4)
    record, cid = _criterion(kyo)
    done = kyo("verify", cid, "--verdict", "met", "--method", "human-observed")
    assert done.code == 0, done
    stored = _words(kyo, record, _credit(kyo, record)["quote_id"])
    assert stored.startswith("start word") and stored.endswith("word end")
    assert f"\n{credits.OMISSION}\n" in stored
    assert len(stored) < len(long_words)
    assert f"is {len(long_words)} characters of typed words" in done.out


def test_turn_words_leave_a_paste_out_and_mark_its_place() -> None:
    from kyosei.harness import claude_code_transcript as transcript

    turn = transcript.Turn(
        "u", T1, transcript.FORM_TYPED, transcript.SOURCE_TRANSCRIPT,
        ("use this one", "and nothing else"), True,
    )  # fmt: skip
    words, total = credits.turn_words(turn)
    assert words == f"use this one\n{credits.OMISSION}\nand nothing else" and total == len(words)
    assert transcript.pieces_in(transcript.quote_pieces(words), turn.segments)
    answer = transcript.Turn(
        "u", T1, transcript.FORM_ANSWER, transcript.SOURCE_TRANSCRIPT, ("Option A", "Yes"), True
    )
    assert credits.turn_words(answer)[0] == "Option A\nYes"


def test_source_other_and_a_turn_are_refused_together(kyo, talked: Path) -> None:
    _record_id, cid = _criterion(kyo)
    refused = kyo(
        "verify", cid, "--verdict", "met", "--method", "human-observed",
        "--turn", PROMPT_3, "--source-other", "a call",
    )  # fmt: skip
    assert refused.code == 64 and "--source-other has none" in refused.err


def test_the_person_is_not_asked_for_where_nobody_is_declared(
    kyo, project: Path, session: str
) -> None:
    """KYO-656 stands: a project that declares nobody writes no credit and reads no turn."""
    opt_in_transcripts(project)
    record, cid = _criterion(kyo)
    done = kyo("verify", cid, "--verdict", "met", "--method", "human-observed")
    assert done.code == 0, done
    assert kyo("show", record, "--json").json["credits"] == []
    assert TEST_PERSON not in done.out


def test_a_turn_with_no_typed_words_is_refused_naming_both_remedies(
    kyo, talked: Path, tmp_path: Path
) -> None:
    """A turn whose typed words are empty once its paste is taken out: there is nothing to
    copy, and the refusal's two remedies each write the credit."""
    from kyosei.errors import ContractViolation
    from kyosei.harness import claude_code_transcript as transcript

    view = credits._SessionView(SESSION, [], [], True, "")
    turn = transcript.Turn(
        "u", T1, transcript.FORM_TYPED, transcript.SOURCE_TRANSCRIPT, ("  ",), True
    )
    with pytest.raises(ContractViolation) as refused:
        credits._cited(view, turn)
    assert refused.value.contract == credits.CONTRACT_CREDIT_TURN_WORDS_UNREAD
    said = " ".join(refused.value.unmet)
    assert "--turn" in said and "--quote-file FILE" in said
    line = ("--verdict", "met", "--method", "human-observed")
    _first, first_cid = _criterion(kyo)
    assert kyo("verify", first_cid, *line, "--turn", PROMPT_1).code == 0
    _second, second_cid = _criterion(kyo)
    words = _file(tmp_path, "q.txt", "keyed by commit")
    assert kyo("verify", second_cid, *line, "--evidence-file", words).code == 0


def test_said_lists_the_persons_turns_with_the_ids_a_credit_takes(
    kyo, talked: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed = kyo("turns", "--said")
    assert listed.code == 0, listed
    first, second = listed.out.splitlines()[1:]
    assert T1 in first and PROMPT_1 in first and SPEC_WORDS in first
    assert T3 in second and PROMPT_3 in second and APPROVAL in second
    assert PROMPT_2 not in listed.out  # the task notification is not the person's
    said = kyo("turns", "--said", "--json").json["said"]
    assert [(turn["id"], turn["turn"], turn["first_line"]) for turn in said] == [
        (PROMPT_1, "p-1", SPEC_WORDS),
        (PROMPT_3, "p-3", APPROVAL),
    ]
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    refused = kyo("turns", "--said")
    assert refused.code == 64 and "--session ID" in refused.err
    assert kyo("turns", "--said", "--session", SESSION).out == listed.out


def test_said_is_refused_in_a_project_that_reads_no_transcripts(
    kyo, project: Path, session: str
) -> None:
    refused = kyo("turns", "--said")
    assert refused.code == 64 and "has not opted in" in refused.err


# ------------------------------------------------------- KYO-914: refining turn, context, fix

REFINING_WORDS = "keep it, but key it by branch"
PROMPT_5 = "bbbbbbbb-0000-4000-8000-000000000005"
LATER_1 = "2099-01-01T00:00:00.000Z"
LATER_2 = "2099-01-01T00:01:00.000Z"


def _refined(kyo, *flags: str):
    return kyo(
        "new", "decision", "--title", "Key the cache", "--recommended", "keep it", *flags,
        "--json",
    )  # fmt: skip


def test_kyo914_refining_turn_takes_whatever_turn_takes(kyo, talked: Path, tmp_path: Path) -> None:
    """c1 and c2: a prompt id, a record uuid or a unique prefix, from the flag or the file,
    stored as the turn's record uuid and its session."""
    append_transcript([person("p-4", PROMPT_4, REFINING_WORDS, T4)])
    prompt_hook(kyo, PROMPT_4)
    by_prompt = _refined(kyo, "--outcome", "refined", "--refining-turn", PROMPT_1)
    assert by_prompt.code == 0, by_prompt
    credit = _credit(kyo, by_prompt.json["record_id"])
    assert credit["outcome"] == "refined" and credit["source_turn"] == "p-4"
    assert (credit["refining_session"], credit["refining_turn"]) == (SESSION, "p-1")
    assert f"refined in turn p-3 of session {SESSION}" in kyo(
        "new", "decision", "--title", "Again", "--recommended", "keep it", "--outcome",
        "refined", "--refining-turn", "p-3",
    ).out  # fmt: skip
    for given, stored in (("bbbbbbbb", "p-4"), ("p-3", "p-3"), (PROMPT_3, "p-3")):
        filed = kyo(
            "new", "decision", "--title", f"Key it {given}", "--decision-file",
            _file(tmp_path, "d.yml",
                  f"recommended: keep it\noutcome: refined\nrefining_turn: {given}\n"),
            "--json",
        )  # fmt: skip
        assert filed.code == 0, filed
        assert _credit(kyo, filed.json["record_id"])["refining_turn"] == stored


def test_kyo914_a_refining_turn_that_is_not_the_persons_or_not_refined_is_refused(
    kyo, talked: Path
) -> None:
    not_theirs = _refined(kyo, "--outcome", "refined", "--refining-turn", PROMPT_2)
    assert not_theirs.code == 2, not_theirs
    assert credits.CONTRACT_CREDIT_TURN_NOT_THE_PERSONS in not_theirs.err
    assert "refining_turn" in not_theirs.err
    none = _refined(kyo, "--outcome", "refined", "--refining-turn", "cccccccc")
    assert none.code == 64 and "refining_turn cccccccc names no turn" in none.err
    missing = _refined(kyo, "--outcome", "refined")
    assert missing.code == 64 and "--refining-turn ID" in missing.err
    accepted = _refined(kyo, "--outcome", "accepted", "--refining-turn", PROMPT_1)
    assert accepted.code == 64 and "belongs to outcome 'refined'" in accepted.err
    alone = kyo("new", "decision", "--title", "t", "--refining-turn", PROMPT_1)
    assert alone.code == 64 and "go with --outcome" in alone.err
    on_a_task = kyo("new", "task", "--title", "t", "--refining-turn", PROMPT_1)
    assert on_a_task.code == 64 and "--refining-turn" in on_a_task.err
    assert _refined(kyo, "--outcome", "refined", "--refining-turn", PROMPT_1).code == 0


def test_kyo914_context_turns_sit_beside_the_latest_turn_as_the_approval(
    kyo, talked: Path
) -> None:
    """c4: --context-turn names context, and the person's latest turn stays the approval."""
    record, cid = _criterion(kyo)
    done = kyo("confirm", record, "--criterion", cid, "--context-turn", PROMPT_1, "--no-close")
    assert done.code == 0, done
    credit = _credit(kyo, record)
    assert [(t["role"], t["turn"]) for t in credit["turns"]] == [
        ("context", "p-1"), ("approval", "p-3"),
    ]  # fmt: skip
    assert credit["source_turn"] == "p-3"
    assert _words(kyo, record, credit["quote_id"]) == APPROVAL
    assert "is not cited" not in done.out
    other, other_cid = _criterion(kyo)
    verified = kyo(
        "verify", other_cid, "--verdict", "met", "--method", "human-observed",
        "--context-turn", "p-1",
    )  # fmt: skip
    assert verified.code == 0, verified
    assert [t["role"] for t in _credit(kyo, other)["turns"]] == ["context", "approval"]
    assert "can cite them as context" not in verified.out


def test_kyo914_the_receipt_says_when_turn_left_the_latest_turn_uncited(
    kyo, talked: Path
) -> None:
    record, cid = _criterion(kyo)
    slipped = kyo("confirm", record, "--criterion", cid, "--turn", PROMPT_1, "--no-close")
    assert slipped.code == 0, slipped
    cue = [line for line in slipped.out.splitlines() if "is not cited" in line]
    assert cue and f"prompt {PROMPT_3} of {T3}" in cue[0] and "--context-turn" in cue[0]
    assert f"here prompt {PROMPT_1}" in cue[0]
    other, other_cid = _criterion(kyo)
    latest = kyo("confirm", other, "--criterion", other_cid, "--turn", PROMPT_3, "--no-close")
    assert latest.code == 0 and "is not cited" not in latest.out
    _third, third_cid = _criterion(kyo)
    both = kyo(
        "verify", third_cid, "--verdict", "met", "--method", "human-observed",
        "--turn", PROMPT_1, "--turn", PROMPT_3,
    )  # fmt: skip
    assert both.code == 0 and "is not cited" not in both.out


def test_kyo914_a_context_turn_that_is_the_approval_or_later_is_refused(
    kyo, talked: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record, cid = _criterion(kyo)
    line = ("verify", cid, "--verdict", "met", "--method", "human-observed")
    itself = kyo(*line, "--context-turn", PROMPT_3)
    assert itself.code == 64 and "is the approving turn" in itself.err
    later = kyo(*line, "--turn", PROMPT_1, "--context-turn", PROMPT_3)
    assert later.code == 64 and "later than the approving turn" in later.err
    assert kyo("show", record, "--json").json["credits"] == []
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    unknown = kyo(*line, "--context-turn", PROMPT_1)
    assert unknown.code == 64 and "name the approving turn with --turn" in unknown.err
    done = kyo(*line, "--turn", PROMPT_3, "--context-turn", PROMPT_1)
    assert done.code == 0, done
    assert [t["turn"] for t in _credit(kyo, record)["turns"]] == ["p-1", "p-3"]


def test_kyo914_a_credit_written_in_this_turn_is_corrected_without_a_basis(
    kyo, talked: Path
) -> None:
    """c3: the slip KYO-914 met, caught at once: the confirmation cited the earlier turn as
    the approval, and the correction replaces it, keeping it readable as superseded."""
    record, cid = _criterion(kyo)
    assert kyo("confirm", record, "--criterion", cid, "--turn", PROMPT_1, "--no-close").code == 0
    slip = _credit(kyo, record)
    assert slip["source_turn"] == "p-1"
    fixed = kyo("credit", "--supersede", slip["id"], "--context-turn", PROMPT_1, "--json")
    assert fixed.code == 0, fixed
    assert fixed.json["correction"] is True and fixed.json["basis_id"] is None
    shown = kyo("show", record, "--json").json["credits"]
    old = next(c for c in shown if c["id"] == slip["id"])
    new = next(c for c in shown if c["id"] == fixed.json["credit_id"])
    assert old["superseded_by"] == new["id"] and old["source_turn"] == "p-1"
    assert [(t["role"], t["turn"]) for t in new["turns"]] == [
        ("context", "p-1"), ("approval", "p-3"),
    ]  # fmt: skip
    assert [s["id"] for s in new["subjects"]] == [s["id"] for s in old["subjects"]]
    # The correction stands in for the live credit, so it is not read as reconstructed.
    assert new["created_at"] == old["created_at"] and new["basis_id"] is None
    assert "reconstructed" not in kyo("show", record).out
    text = kyo("credit", "--supersede", new["id"], "--context-turn", PROMPT_1).out
    assert f"corrects {new['id']}" in text and "since the person's previous turn" in text


def test_kyo914_outside_the_window_a_replacement_still_needs_a_basis(
    kyo, talked: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record, cid = _criterion(kyo)
    assert kyo("confirm", record, "--criterion", cid, "--turn", PROMPT_1, "--no-close").code == 0
    slip = _credit(kyo, record)
    other_session = "9d9d9d9d-2222-4a2b-9c3d-0123456789ab"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", other_session)
    elsewhere = kyo("credit", "--supersede", slip["id"], "--turn", PROMPT_3, "--session", SESSION)
    assert elsewhere.code == 2, elsewhere
    assert credits.CONTRACT_RECONSTRUCTED_CREDIT_REQUIRES_BASIS in elsewhere.err
    assert f"this command runs in session {other_session}" in elsewhere.err
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    # The person speaks twice after the credit was written: it is two turns old.
    append_transcript([person("p-5", PROMPT_5, "next thing", LATER_1)])
    prompt_hook(kyo, PROMPT_5)
    append_transcript([person("p-6", PROMPT_4, "and another", LATER_2)])
    prompt_hook(kyo, PROMPT_4)
    late = kyo("credit", "--supersede", slip["id"], "--turn", PROMPT_3)
    assert late.code == 2, late
    assert credits.CONTRACT_RECONSTRUCTED_CREDIT_REQUIRES_BASIS in late.err
    assert "before the person's previous turn" in late.err and PROMPT_5 in late.err
    assert _credit(kyo, record)["superseded_by"] is None
    basis = kyo(
        "new", "decision", "--title", "Correct the credit", "--outcome", "unprompted",
        "--turn", PROMPT_4, "--json",
    ).json["credit_id"]  # fmt: skip
    done = kyo("credit", "--supersede", slip["id"], "--turn", PROMPT_3, "--basis", basis, "--json")
    assert done.code == 0, done
    assert done.json["correction"] is False and done.json["basis_id"] == basis
