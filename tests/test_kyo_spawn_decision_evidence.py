"""KYO-522: `kyo spawn <reminder> --kind decision` needs the operator's words, like `kyo new`.

A reminder that was waiting on the operator becomes a decision exactly when they answer in
conversation, so the spawn is refused without `--evidence-file` (the same helper and the
same refusal as `kyo new`, KYO-521). A decision spawned from a proposal or an investigation
has its provenance in the source and asks for none. Each test fails if its mechanism is
removed: the carried-source rule, the flag clearing it, or the proposal/investigation edge
staying free.
"""

from __future__ import annotations

import hashlib

import pytest

from kyosei.contracts import CONTRACT_FILING_REQUIRES_EVIDENCE
from kyosei.errors import EXIT_CONTRACT

from .conftest import new_record

WORDS = (
    "The maintainer, in the orchestrator session:\n"
    '"yes, keep the cache"\n'
)


def _reminder(kyo) -> str:
    filed = kyo("new", "reminder", "--title", "Ask whether to keep the cache", "--json")
    assert filed.code == 0, filed
    return filed.json["record_id"]


def test_spawning_a_decision_from_a_reminder_is_refused_naming_the_flag(kyo) -> None:
    """c1: refused under the KYO-521 contract, exit 2, naming the flag; nothing created."""
    reminder = _reminder(kyo)
    refused = kyo("spawn", reminder, "--kind", "decision", "--title", "Keep the cache")
    assert refused.code == EXIT_CONTRACT, refused
    assert CONTRACT_FILING_REQUIRES_EVIDENCE in refused.err
    assert "--evidence-file" in refused.err
    assert [r["id"] for r in kyo("list", "--json").json["records"]] == [reminder]


def test_the_flag_clears_the_refusal_and_stores_the_words(kyo, write_file) -> None:
    """c1: the remedy the refusal names clears it; the evidence is stored verbatim with its
    sha256 on the new record in the same act."""
    reminder = _reminder(kyo)
    assert (
        kyo("spawn", reminder, "--kind", "decision", "--title", "Keep it").code == EXIT_CONTRACT
    )
    words = write_file("words.md", WORDS)
    spawned = kyo(
        "spawn", reminder, "--kind", "decision", "--title", "Keep it",
        "--evidence-file", words, "--json",
    )  # fmt: skip
    assert spawned.code == 0, spawned
    decision = spawned.json["record_id"]
    assert spawned.json["evidence_id"] == f"{decision}.e1"
    (evidence,) = kyo("show", decision, "--json").json["evidence_artifacts"]
    stored = WORDS.rstrip("\n")
    assert evidence["text"] == stored
    assert evidence["sha256"] == hashlib.sha256(stored.encode("utf-8")).hexdigest()


def test_an_empty_evidence_file_is_refused(kyo, write_file) -> None:
    reminder = _reminder(kyo)
    empty = write_file("empty.md", "\n")
    refused = kyo(
        "spawn", reminder, "--kind", "decision", "--title", "x", "--evidence-file", empty
    )
    assert refused.code != 0 and "must not be empty" in refused.err, refused


@pytest.mark.parametrize(
    ("source_kind", "criteria"),
    [("proposal", ["a store is chosen"]), ("investigation", None)],
)
def test_a_decision_spawned_from_a_proposal_or_investigation_needs_no_evidence(
    kyo, source_kind, criteria
) -> None:
    """c2: unchanged -- the source record and its produced-by link are the provenance."""
    source = new_record(kyo, kind=source_kind, title="the source", criteria=criteria)
    spawned = kyo("spawn", source, "--kind", "decision", "--title", "Decided", "--json")
    assert spawned.code == 0, spawned
    assert spawned.json["evidence_id"] is None
    assert kyo("show", spawned.json["record_id"], "--json").json["evidence_artifacts"] == []


def test_the_registry_page_says_which_spawn_routes_need_evidence() -> None:
    """c3: the kind registry topic page names the rule, the flag and the exempt sources."""
    from pathlib import Path

    page = (Path(__file__).resolve().parents[1] / "docs/reference/topics/registries.md").read_text(
        encoding="utf-8"
    )
    assert "Which spawn routes need evidence (KYO-522)" in page
    assert "`kyo spawn <reminder-id> --kind decision" in page
    assert "--evidence-file" in page and "proposal or an investigation" in page
