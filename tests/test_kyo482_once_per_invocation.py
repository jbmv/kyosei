"""KYO-488: each explanation a write command prints, printed once per invocation.

KYO-482 measured what `kyo dispatch`, `kyo report` and `kyo accept` print on a fixture
store and found three pieces of text that repeat
inside one invocation: the disjointness census's staleness sentence (once per running
dispatch), the whole-file `governs:` narrowing explanation (once per document), and
accept's unobserved-criteria gate remedy (once by its verify step, again by its own
summary). This file holds that each now prints once, that every per-entry fact and
remedy still prints, and that no `--json` payload moved.

KYO-489 then shortened the words: each sentence now prints as its short form from
``kyosei.explanations``, with the full text in `kyo reference <command>`, so the
assertions below count the short forms that print in the sentences' place.
"""

from __future__ import annotations

import json
from pathlib import Path

from kyosei import cmd_evidence, explanations

from .conftest import new_record, valid_report
from .test_kyo137_docs_obligations import (
    _branch_editing,
    _commit,
    _declare_touches,
    _git,
    _governed_doc_and_a_branch_that_only_touches_code,
    _merged_ingest,
    _write,
)
from .test_kyo147_disjointness_worth import _promote, _worktree
from .test_output_flags import assert_pinned

#: KYO-489: the census line as printed, its short form and the colon after it.
NEVER_CHECKED = explanations.short("never-checked-against-work") + ":"


def _three_running_then_a_fourth(kyo, project: Path, tmp_path: Path):
    """Three running dispatches nobody has reported on, and a fourth record to dispatch."""
    ids = []
    for name in ("a", "b", "c"):
        record = _promote(kyo, project, tmp_path, name, [f"src/{name}.py"])
        started = kyo("dispatch", record, "--worktree", _worktree(tmp_path, f"w{name}"), "--json")
        assert started.code == 0, started
        ids.append((started.json["dispatch_id"], record))
    fourth = _promote(kyo, project, tmp_path, "d", ["src/d.py"])
    return ids, fourth


# ------------------------------------------------------------------------ KYO-488.c2


def test_the_census_prints_each_staleness_sentence_once_then_the_dispatches(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    ids, fourth = _three_running_then_a_fourth(kyo, git_project, tmp_path)
    spoken = kyo("dispatch", fourth, "--worktree", _worktree(tmp_path, "wd"))
    assert spoken.code == 0, spoken
    assert spoken.out.count(NEVER_CHECKED) == 1, spoken.out
    lines = spoken.out.splitlines()
    at = next(n for n, line in enumerate(lines) if NEVER_CHECKED in line)
    # Followed by the ids of the running dispatches in that state, each with its own
    # declared-path count — the per-entry fact the sentence used to be glued to.
    following = lines[at + 1 : at + 1 + len(ids)]
    for (dispatch_id, record), line in zip(ids, following, strict=True):
        assert line.strip() == f"{dispatch_id} (dispatching {record}): 1 declared path(s)", (
            spoken.out
        )


def test_a_known_incomplete_entry_keeps_its_own_line_and_remedy(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """The one state whose line carries per-entry facts (its file list) and a remedy
    of its own, beside two never-checked dispatches that share one sentence."""
    a = _promote(kyo, git_project, tmp_path, "a", ["kyosei/config.yml"])
    first = kyo("dispatch", a, "--worktree", _worktree(tmp_path, "wa"), "--json")
    assert first.code == 0, first
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            valid_report(
                dispatch_id=first.json["dispatch_id"],
                record_id=a,
                worked_from_sha=first.json["worked_from_sha"],
                touched_files=["kyosei/config.yml", "kyosei/procedures.md"],
            )
        ),
        encoding="utf-8",
    )
    assert kyo("report", first.json["dispatch_id"], "--file", str(report)).code == 0
    again = kyo("dispatch", a, "--worktree", _worktree(tmp_path, "wa2"), "--json")
    assert again.code == 0, again
    for name in ("b", "c"):
        record = _promote(kyo, git_project, tmp_path, name, [f"src/{name}.py"])
        assert kyo("dispatch", record, "--worktree", _worktree(tmp_path, f"w{name}")).code == 0
    last = _promote(kyo, git_project, tmp_path, "e", ["src/e.py"])

    spoken = kyo("dispatch", last, "--worktree", _worktree(tmp_path, "we"))
    assert spoken.code == 0, spoken
    incomplete = [line for line in spoken.out.splitlines() if "KNOWN INCOMPLETE" in line]
    assert len(incomplete) == 1, spoken.out
    assert again.json["dispatch_id"] in incomplete[0]
    assert "kyosei/procedures.md" in incomplete[0]
    assert "kyo touch-drift <dispatch-id> --apply" in incomplete[0]
    assert spoken.out.count(NEVER_CHECKED) == 1, spoken.out


# ------------------------------------------------------------------------ KYO-488.c3

_FIRST_DOC = """# The architecture

## Why the CLI is wired this way

governs: src/app.py

The reasoning.
"""

_SECOND_DOC = """# The operating guide

## Running the CLI

governs: src/app.py

How to run it.
"""


def _two_documents_reported(kyo, project: Path, *, as_json: bool = False):
    _write(project, "docs/architecture.md", _FIRST_DOC)
    _write(project, "docs/guide.md", _SECOND_DOC)
    _write(project, "src/app.py", "def parse_args():\n    return 1\n")
    _commit(project, "baseline")
    record = new_record(kyo, criteria=["the CLI is wired"])
    _declare_touches(kyo, project, record, "src/app.py")
    _branch_editing(project, "kyo-488", {"src/app.py": "def parse_args():\n    return 2\n"})
    dispatched = kyo("dispatch", record, "--branch", "kyo-488", "--no-create-worktree", "--json")
    assert dispatched.code == 0, dispatched
    path = project / "report.json"
    path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatched.json["dispatch_id"],
                record_id=record,
                worked_from_sha=_git(project, "rev-parse", "kyo-488"),
            )
        ),
        encoding="utf-8",
    )
    flags = ["--json"] if as_json else []
    return kyo("report", dispatched.json["dispatch_id"], "--file", str(path), *flags)


def test_report_explains_the_narrowing_once_and_offers_it_per_document(
    kyo, git_project: Path
) -> None:
    reported = _two_documents_reported(kyo, git_project)
    assert reported.code == 0, reported
    explanation = explanations.short("whole-file-narrowing")
    assert reported.out.count(explanation) == 1, reported.out
    offers = [
        line.strip()
        for line in reported.out.splitlines()
        if "this document governs whole files" in line
    ]
    offer = "this document governs whole files (src/app.py) — `governs: <path>#<symbol>`"
    assert len(offers) == 2, reported.out
    assert all(line.startswith(offer) for line in offers), offers


# ------------------------------------------------------------------------ KYO-488.c4

UNOBSERVED_REASON = "the banner is only visible to a human with the app in front of them"


def _accepted_with_an_unobserved_criterion(kyo, project: Path, *flags: str):
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, project)
    dispatch_id, reported = _merged_ingest(
        kyo,
        project,
        record,
        criteria_verdicts=[
            {
                "criterion_id": f"{record}.c1",
                "verdict": "unmet-unobserved",
                "method": "reasoned-about",
                "reason": UNOBSERVED_REASON,
            }
        ],
        touched_files=["src/app.py"],
    )
    assert reported.code == 0, reported
    return record, kyo("accept", dispatch_id, *flags)


def test_accept_prints_the_gate_remedy_once_in_its_summary(kyo, git_project: Path) -> None:
    record, accepted = _accepted_with_an_unobserved_criterion(kyo, git_project)
    assert accepted.code == 0, accepted
    gate_lines = [
        line
        for line in accepted.out.splitlines()
        if "kyo gate" in line and "awaiting:human-run" in line
    ]
    assert len(gate_lines) == 1, accepted.out
    summary_at = next(
        n
        for n, line in enumerate(accepted.out.splitlines())
        if line.startswith("unobserved criteria nothing makes findable")
    )
    assert accepted.out.splitlines().index(gate_lines[0]) > summary_at, (
        "the one copy left is the summary's, which covers every covered record"
    )


def test_accept_json_keeps_the_verify_steps_lines_verbatim(kyo, git_project: Path) -> None:
    """Only the text moved: `--json` still carries the step's own output whole."""
    _record, accepted = _accepted_with_an_unobserved_criterion(kyo, git_project, "--json", "--full")
    assert accepted.code == 0, accepted
    verify = next(step for step in accepted.json["steps"] if step["argv"][0] == "verify")
    block = cmd_evidence.gate_prompt_lines(verify["data"]["ungated_unobserved"])
    assert block, verify
    lines = verify["lines"]
    assert any(lines[n : n + len(block)] == block for n in range(len(lines))), lines


# ------------------------------------------------------------------------ KYO-488.c5


def _key_paths(value, prefix: str = "") -> set[str]:
    """Every key path in a JSON document, list positions collapsed to ``[]``."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            found.add(path)
            found |= _key_paths(item, path)
    elif isinstance(value, list):
        for item in value:
            found |= _key_paths(item, f"{prefix}[]")
    return found


def _pinned_paths(paths: set[str]) -> str:
    return "\n".join(sorted(paths)) + "\n"


# KYO-542: each command's sorted ``--json`` key paths is a fixture file under
# tests/fixtures/pinned_output/kyo482-once-per-invocation/ (they were sha256 digests captured
# before KYO-488). The key sets are what KYO-488.c5 holds unchanged; the
# step-lines test above holds the one value this change could have moved. A key added by a
# later record (KYO-511, KYO-535) is a one-line fixture diff.


def test_dispatch_json_keys_are_unchanged(kyo, git_project: Path, tmp_path: Path) -> None:
    _ids, fourth = _three_running_then_a_fourth(kyo, git_project, tmp_path)
    result = kyo("dispatch", fourth, "--worktree", _worktree(tmp_path, "wd"), "--json")
    assert result.code == 0, result
    paths = _key_paths(result.json)
    assert_pinned("kyo482-once-per-invocation/dispatch-json-keys", _pinned_paths(paths))


def test_report_json_keys_are_unchanged(kyo, git_project: Path) -> None:
    result = _two_documents_reported(kyo, git_project, as_json=True)
    assert result.code == 0, result
    paths = _key_paths(result.json)
    assert_pinned("kyo482-once-per-invocation/report-json-keys", _pinned_paths(paths))


def test_accept_json_keys_are_unchanged(kyo, git_project: Path) -> None:
    _record, result = _accepted_with_an_unobserved_criterion(kyo, git_project, "--json")
    assert result.code == 0, result
    paths = _key_paths(result.json)
    assert_pinned("kyo482-once-per-invocation/accept-json-keys", _pinned_paths(paths))
