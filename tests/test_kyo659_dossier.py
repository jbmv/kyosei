"""`kyo dossier`: one decision as a page for a reader outside the project (KYO-659;
decision KYO-654, DC8; section 8 of the KYO-651 proposal).

The transcript and the index are written with the helpers of
``tests/test_kyo658_discussions.py``; the words are invented.
"""

from __future__ import annotations

import inspect
import re
import shutil
from pathlib import Path

import pytest

from kyosei import cmd_dossier
from kyosei.export import write_export
from kyosei.store import open_store

from .conftest import TEST_PERSON, declare_person, new_record, opt_in_transcripts
from .test_kyo658_discussions import (
    DECIDING_WORDS,
    PROMPT_1,
    PROMPT_3,
    SESSION,
    _file,
    agent_text,
    append_transcript,
    person,
    prompt_hook,
    transcript_path,
    write_transcript,
)

HEADINGS = [
    "The decision",
    "The problem",
    "What was on the table",
    "Who decided, and how",
    "Timeline",
    "Depth",
    "What happened next",
    "How this record was made",
]
PERSON_ASKED = "should the cache stay?"
AGENT_SAID = "I recommend keeping it."
DECISION_FILE = (
    "recommended: keep the cache\n"
    "outcome: accepted\n"
    "alternatives:\n"
    "  - {text: keep the cache, chosen: true}\n"
    "  - {text: drop the cache}\n"
)


@pytest.fixture
def live(kyo, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """A decision filed from a discussion that was opened when it started, in an indexed
    session: ``{"decision", "discussion", "task"}``. The task's description names a record
    by id, which the decision's description repeats."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    opt_in_transcripts(project)
    declare_person(project)
    write_transcript(
        [
            person("p-1", PROMPT_1, PERSON_ASKED, "2026-11-06T10:00:00.000Z"),
            agent_text("a-1", AGENT_SAID, "2026-11-06T10:00:30.000Z"),
        ]
    )
    prompt_hook(kyo, PROMPT_1)
    discussion = kyo("discuss", "open", "--title", "The cache", "--json").json["id"]
    append_transcript([person("p-3", PROMPT_3, DECIDING_WORDS, "2026-11-06T10:01:00.000Z")])
    prompt_hook(kyo, PROMPT_3)
    task = new_record(kyo, kind="task", title="Measure the cache", criteria=["it is measured"])
    decision = kyo(
        "spawn", discussion, "--kind", "decision", "--title", "Decision: the cache stays",
        "--description-file",
        _file(tmp_path, "desc.md", f"{task} measured the cache as fast enough. It stays.\n"),
        "--quote-file", _file(tmp_path, "q.txt", DECIDING_WORDS),
        "--decision-file", _file(tmp_path, "d.yml", DECISION_FILE),
        "--person", TEST_PERSON, "--json",
    )  # fmt: skip
    assert decision.code == 0, decision
    return {"decision": decision.json["record_id"], "discussion": discussion, "task": task}


def _above_references(page: str) -> str:
    return page.split("\n## References\n")[0]


# ------------------------------------------------------------------ KYO-659.c1


def test_the_page_has_the_eight_parts_in_order_each_naming_its_source(kyo, live: dict) -> None:
    page = kyo("dossier", live["decision"]).out
    assert re.findall(r"^## (.+)$", page, flags=re.M) == [*HEADINGS, "References"]
    for heading, body in zip(HEADINGS, page.split("\n## ")[1:], strict=False):
        assert body.startswith(heading)
        assert re.search(r"^\*Source: .+\*$", body, flags=re.M), heading
    data = kyo("dossier", live["decision"], "--json").json
    assert [part["heading"] for part in data["parts"]] == HEADINGS
    assert all(part["source"] and part["blocks"] for part in data["parts"])
    assert data["markdown"] == page
    assert data["reconstructed"] is False and data["sealed"] is False


def test_the_parts_are_drawn_from_the_credit_the_discussion_and_the_index(
    kyo, live: dict
) -> None:
    page = kyo("dossier", live["decision"]).out
    assert page.startswith("# The cache stays\n")
    assert "- keep the cache (chosen)\n- drop the cache" in page
    assert "The agent (orchestrator) recommended: keep the cache." in page
    assert f"{TEST_PERSON} decided, accepting the recommendation." in page
    assert f"> {DECIDING_WORDS}" in page
    # The opening's time is the index's stamp of the person's first message.
    opened = kyo("show", live["discussion"], "--json").json["turn_refs"][0]
    assert opened["index_state"] == "indexed"
    stamp = f"{opened['at'][:10]} {opened['at'][11:16]} UTC"
    assert f"- {stamp}: A discussion record is opened at a message from the person." in page
    assert f"at the person's message of {stamp}, stamped from the turn index" in page
    assert f"- 2026-11-06 10:01 UTC: {TEST_PERSON} sends the words quoted above." in page
    assert f"2 messages from {TEST_PERSON} and 1 reply from the agent" in page
    assert "Recorded live: the credit was written with the decision record." in page
    assert "and were found in a message the person sent" in page
    assert cmd_dossier.NOT_SEALED in page


def test_record_ids_appear_only_in_the_references(kyo, live: dict) -> None:
    page = kyo("dossier", live["decision"]).out
    assert "TST-" not in _above_references(page)
    references = page.split("\n## References\n")[1]
    for cited in (live["decision"], live["discussion"], live["task"]):
        assert f"`{cited}`" in references
    # The description's id became the number its record is listed under.
    number = re.search(rf"^(\d+)\. `{live['task']}`", references, flags=re.M).group(1)
    assert f"[{number}] measured the cache as fast enough." in page


def test_work_that_only_names_the_decision_is_listed_apart_from_linked_work(
    kyo, live: dict, tmp_path: Path
) -> None:
    assert "The store links no work to this decision." in kyo("dossier", live["decision"]).out
    waiting = new_record(kyo, kind="task", title="Keep the cache warm", criteria=["it is warm"])
    assert kyo("link", live["decision"], "blocks", waiting).code == 0
    naming = kyo(
        "new", "task", "--title", "Unrelated tidying", "--description-file",
        _file(tmp_path, "n.md", f"Written the day after {live['decision']}.\n"),
    )  # fmt: skip
    assert naming.code == 0, naming
    page = kyo("dossier", live["decision"]).out
    linked, _, apart = page.split("## What happened next")[1].partition(
        "Other work records name this decision in their text."
    )
    assert '"Keep the cache warm"' in linked
    assert "recorded as waiting on this decision" in linked
    assert "0 of 1 criterion met" in linked
    assert '"Unrelated tidying"' in apart and '"Unrelated tidying"' not in linked


# ------------------------------------------------------------------ KYO-659.c2


def test_the_page_holds_no_transcript_text_but_the_credited_quote(kyo, live: dict) -> None:
    page = kyo("dossier", live["decision"]).out
    assert DECIDING_WORDS in page
    assert PERSON_ASKED not in page and AGENT_SAID not in page


def test_the_page_is_the_same_with_the_transcript_gone(kyo, live: dict) -> None:
    before = kyo("dossier", live["decision"]).out
    shutil.rmtree(transcript_path().parent)
    assert kyo("dossier", live["decision"]).out == before


def test_the_command_has_no_way_to_open_a_transcript() -> None:
    source = inspect.getsource(cmd_dossier)
    assert "claude_code_transcript" not in source
    assert "recorded_transcript_path" not in source and "transcript_archive" not in source


def test_an_id_inside_the_quoted_words_is_left_as_the_person_wrote_it(
    kyo, project: Path, tmp_path: Path
) -> None:
    declare_person(project)
    words = "do it as TST-1 says"
    filed = kyo(
        "new", "decision", "--title", "Follow the first record",
        "--quote-file", _file(tmp_path, "q.txt", words),
        "--decision-file", _file(tmp_path, "d.yml", "outcome: unprompted\n"),
        "--person", TEST_PERSON, "--source-other", "a call", "--opened-late", "--json",
    )  # fmt: skip
    assert filed.code == 0, filed
    page = kyo("dossier", filed.json["record_id"]).out
    assert f"> {words}" in page
    assert "said outside a recorded session: a call." in page
    assert "No recommendation was made: the person decided unprompted." in page
    assert "The discussion record was opened late, after the decision." in page


# ------------------------------------------------------------------ KYO-659.c3


def _sql(project: Path, record: str, statement: str, *values: str) -> None:
    store = open_store(project)
    try:
        with store.transaction():
            store.conn.execute(statement, values)
        write_export(store, record)
    finally:
        store.close()


def test_a_reconstructed_decision_says_so_with_its_basis(kyo, live: dict, project: Path) -> None:
    approval = new_record(kyo, kind="decision", title="The backfill sheet is approved")
    _sql(
        project, live["decision"],
        "UPDATE credits SET created_at = '2099-01-01T00:00:00.000Z', basis_id = ? "
        "WHERE id = ?",
        f"{approval}.p1", f"{live['decision']}.p1",
    )  # fmt: skip
    result = kyo("dossier", live["decision"], "--json").json
    page = result["markdown"]
    assert result["reconstructed"] is True
    made = page.split("## How this record was made")[1]
    assert f"Reconstructed: the credit naming {TEST_PERSON} was written on 2099-01-01" in made
    assert f"It rests on an approval {TEST_PERSON} gave on" in made
    assert 'recorded as "The backfill sheet is approved"' in made
    assert "Recorded live" not in page
    assert f"`{approval}.p1`" in page.split("\n## References\n")[1]
    assert "- 2099-01-01 00:00 UTC: The credit naming who decided is written" in page


def test_a_reconstructed_credit_with_no_basis_says_no_approval_is_recorded(
    kyo, live: dict, project: Path
) -> None:
    _sql(
        project, live["decision"],
        "UPDATE credits SET created_at = '2099-01-01T00:00:00.000Z', observed_at = NULL "
        "WHERE id = ?",
        f"{live['decision']}.p1",
    )  # fmt: skip
    assert "The store records no approval for it." in kyo("dossier", live["decision"]).out


def _authorize(project: Path, record: str) -> None:
    store = open_store(project)
    try:
        with store.transaction():
            quote = store.evidence_artifacts(record)[0]["id"]
            store.add_credit(
                record, quote_id=quote, subjects=[("record", record)], person=TEST_PERSON,
                role="authorized", source_kind="other", source_note="a call",
                transcript_check="not-applicable", author="orchestrator",
            )  # fmt: skip
        write_export(store, record)
    finally:
        store.close()


def test_a_go_ahead_is_printed_apart_and_never_as_a_decision(
    kyo, live: dict, project: Path
) -> None:
    _authorize(project, live["decision"])
    page = kyo("dossier", live["decision"]).out
    who = page.split("## Who decided, and how")[1].split("## Timeline")[0]
    assert who.count(f"{TEST_PERSON} decided") == 1
    assert f"{TEST_PERSON} gave a go-ahead for work to proceed." in who
    assert "it is not a decision and is not counted as one" in who
    # With the decided credit withdrawn, the go-ahead alone decides nothing.
    _sql(
        project, live["decision"],
        "UPDATE credits SET withdrawn_at = '2026-11-07T00:00:00.000Z', withdrawn_by = 'x', "
        "withdrawn_why = 'a test' WHERE id = ?",
        f"{live['decision']}.p1",
    )  # fmt: skip
    data = kyo("dossier", live["decision"], "--json").json
    page = data["markdown"]
    assert data["credit_ids"] == []
    assert f"{TEST_PERSON} decided" not in page
    assert "The store holds no credit naming who decided this." in page
    assert f"{TEST_PERSON} gave a go-ahead for work to proceed." in page


def test_a_record_with_no_decision_on_it_is_refused(kyo, live: dict, project: Path) -> None:
    refused = kyo("dossier", live["task"])
    assert refused.code == 64 and "a dossier describes one decision" in refused.err
    assert kyo("dossier", live["discussion"]).code == 64
    _authorize(project, live["decision"])
    go_ahead = kyo("dossier", f"{live['decision']}.p2")
    assert go_ahead.code == 64 and "names who decided" in go_ahead.err
    assert kyo("dossier", f"{live['decision']}.p1").code == 0
    assert kyo("dossier", f"{live['decision']}.c1").code == 64


def test_a_sealed_credit_is_said_to_be_sealed() -> None:
    assert cmd_dossier.seal_sentence({"span_seal": "ab12"}) == cmd_dossier.SEALED
    assert cmd_dossier.seal_sentence({"span_seal": None}) == cmd_dossier.NOT_SEALED
    assert cmd_dossier.seal_sentence(None) == cmd_dossier.NOT_SEALED
