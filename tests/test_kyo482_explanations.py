"""KYO-489: write acknowledgements explain once.

KYO-482 measured twenty explanatory clauses that `kyo dispatch`, `kyo report`, `kyo
accept`, `kyo gate` and `kyo ungate` printed identically on every run. Nineteen of them
(#17 is a remedy, which KYO-488 prints once) now print a short form inline, and their
full text lives in one registry, ``kyosei.explanations``, which `kyo reference
<command>` renders as an "Output notes" subsection. KYO-482 settled three questions:
repeated text prints once per invocation by default; the explain-once form is the
default in every project rather than an opt-in key; and the co-occurrence "dragged in"
list stays in `kyo dispatch`'s default output.

What this file holds, criterion by criterion:

- c2: every clause has exactly one entry, and no command prints a full text inline;
- c3: `kyo reference <command>` lists each of that command's entries, full text and all;
- c4: a command that printed a short form ends with exactly one pointer line, and one
  that printed none prints no pointer;
- c5: no entry's text carries a remedy, so a remedy cannot be shortened away;
- c6: refusals print exactly what they printed before KYO-489;
- c7: `kyo report`'s review block is byte-identical before and after.

c6 and c7 pin normalised fixture files under tests/fixtures/pinned_output/ (KYO-542; they
were sha256 digests captured before KYO-489). ``KYOSEI_UPDATE_PINNED=1`` rewrites them.
"""

from __future__ import annotations

import json
import re
import string
from pathlib import Path

import pytest
import yaml

from kyosei import commands, explanations
from kyosei.cli import reference_command_names, reference_markdown

from .conftest import new_record, valid_report
from .test_kyo137_docs_obligations import (
    _branch_editing,
    _commit,
    _declare_touches,
    _git,
    _write,
)
from .test_kyo147_disjointness_worth import _promote, _report_touching, _worktree
from .test_output_flags import assert_pinned, pin_name
from .test_scopes import TWO_SCOPES, touching, write_scopes

_DOC = """# The architecture

## Why the CLI is wired this way

governs: src/app.py

The reasoning.
"""

_GUIDE = """# The operating guide

## Running the CLI

governs: src/app.py

How to run it.
"""

_SHA = re.compile(r"\b[0-9a-f]{40}\b")
_DIGEST = re.compile(r"sha256:[0-9a-f]{12}(?![0-9a-f])")


def _normalise(text: str, tmp_path: Path) -> str:
    """Scratch paths and commit shas vary run to run; nothing else here does."""
    for root in {str(tmp_path.resolve()), str(tmp_path)}:
        text = text.replace(root, "<TMP>")
    return _DIGEST.sub("sha256:<DIGEST>", _SHA.sub("<SHA>", text))


def _full_fragment(entry: explanations.Explanation) -> str:
    """The longest literal stretch of an entry's full text — what a command printing
    the full text inline would necessarily print, whatever its per-run values were."""
    literals = [literal for literal, *_ in string.Formatter().parse(entry.full)]
    constants = explanations._constants()  # noqa: SLF001 - the build's own values
    filled = (
        [explanations._fill(entry.full, {})]
        if all(  # noqa: SLF001
            name in constants for name in explanations.fields(entry.full)
        )
        else []
    )
    return max([*literals, *filled], key=len)


def _set_config(project: Path, **values) -> None:
    path = project / "kyosei" / "config.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw.update(values)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------- the scenarios


def _rich_report(kyo, project: Path):
    """A live-branch report raising obligations on two documents, touch drift, and
    every review field the report schema carries."""
    _write(project, "docs/architecture.md", _DOC)
    _write(project, "docs/guide.md", _GUIDE)
    _write(project, "src/app.py", "def parse_args():\n    return 1\n")
    _write(project, "src/other.py", "y = 1\n")
    _commit(project, "baseline")
    record = new_record(kyo, criteria=["the CLI is wired", "the banner shows"])
    _declare_touches(kyo, project, record, "src/app.py")
    _branch_editing(
        project,
        "kyo-489",
        {"src/app.py": "def parse_args():\n    return 2\n", "src/other.py": "y = 2\n"},
    )
    dispatched = kyo("dispatch", record, "--branch", "kyo-489", "--no-create-worktree", "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    path = project / "report.json"
    path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatch_id,
                record_id=record,
                worked_from_sha=_git(project, "rev-parse", "kyo-489"),
                criteria_verdicts=[
                    {
                        "criterion_id": f"{record}.c1",
                        "verdict": "met",
                        "method": "machine-checked",
                        "evidence": "pytest: exit=0",
                    },
                    {
                        "criterion_id": f"{record}.c2",
                        "verdict": "unmet-unobserved",
                        "method": "reasoned-about",
                        "reason": "needs a person in front of the app",
                    },
                ],
                premise_false=[
                    {"claim": "the file exists", "correction": "it did not", "impact": "none"}
                ],
                disagreements=[
                    {
                        "topic": "naming",
                        "brief_said": "call it x",
                        "worker_did": "called it y",
                        "rationale": "y is clearer",
                    }
                ],
                corrected_numbers=[
                    {
                        "claim": "lines",
                        "brief_value": "10",
                        "actual_value": "12",
                        "method": "wc -l",
                    }
                ],
                commits=[{"sha": _git(project, "rev-parse", "kyo-489"), "subject": "work"}],
                touched_files=["src/app.py", "src/other.py"],
                docs_verdicts=[
                    {
                        "doc_path": "docs/architecture.md",
                        "section": "Why the CLI is wired this way",
                        "verdict": "not-affected",
                        "method": "reasoned-about",
                        "reason": "the wiring did not change",
                    }
                ],
            )
        ),
        encoding="utf-8",
    )
    return record, dispatch_id, kyo("report", dispatch_id, "--file", str(path))


def _dispatch_with_everything(kyo, project: Path, tmp_path: Path):
    """A dispatch that prints every dispatch clause: a declared `worktree_setup`, a
    project worker model, a co-occurrence finding from history, and one running
    dispatch nothing has checked."""
    history = _promote(kyo, project, tmp_path, "hist", ["kyosei/config.yml"])
    for n in range(commands.COOCCURRENCE_MIN_REPORTS):
        _report_touching(
            kyo, tmp_path, history, _worktree(tmp_path, f"wh{n}"),
            ["kyosei/config.yml", "README.md"],
        )  # fmt: skip
    running = _promote(kyo, project, tmp_path, "run", ["src/run.py"])
    started = kyo("dispatch", running, "--worktree", _worktree(tmp_path, "wr"), "--json")
    assert started.code == 0, started
    _set_config(project, worker_model="model-x", worktree_setup=["make venv"])
    _commit(project, "config and fixtures")
    fresh = _promote(kyo, project, tmp_path, "fresh", ["kyosei/config.yml"])
    return kyo("dispatch", fresh, "--branch", "kyo-fresh")


def _tour(kyo, project: Path, tmp_path: Path) -> dict[str, object]:
    """One run of every command whose text output carries a short form."""
    runs: dict[str, object] = {}
    _record, dispatch_id, runs["report"] = _rich_report(kyo, project)
    runs["accept"] = kyo("accept", dispatch_id, "--reviewed")
    runs["docs settle"] = kyo(
        "docs", "settle", "TST-1.o2", "--verdict", "not-affected", "--method",
        "reasoned-about", "--why", "the guide still holds",
    )  # fmt: skip
    gated = new_record(kyo, criteria=["it shows"])
    runs["gate"] = kyo("gate", gated, "awaiting:human-run", "--do", "look at it")
    gate_id = kyo("show", gated, "--json").json["gates"][0]["id"]
    runs["ungate"] = kyo("ungate", gate_id, "--why", "looked")
    write_scopes(project, TWO_SCOPES)
    runs["new"] = kyo(
        "new", "bug", "--title", "A mislabelled thing", "--criteria-file", "-",
        "--touches-file", touching(project, "apps/desktop/main.ts"), "--scope", "cli",
        stdin="something is true\n",
    )  # fmt: skip
    runs["dispatch"] = _dispatch_with_everything(kyo, project, tmp_path)
    for label, result in runs.items():
        assert result.code == 0, (label, result)
    return runs


#: The entries each tour run must print the short form of — every entry is in one.
EXPECTED_IN_TOUR = {
    "report": {
        "verdicts-not-applied",
        "obligations-need-verdicts",
        "whole-file-narrowing",
        "changed-paths-branch-diff",
        "drift-reported-not-refused",
    },
    "accept": {
        "review-entries-unsettled",
        "drift-fed-back",
        "accept-wrote-no-gate",
        "accept-not-closed",
    },
    "docs settle": {"docs-nothing-blocks"},
    "gate": {"gates-orthogonal"},
    "ungate": {"ungate-no-stale-flag"},
    "new": {"scope-derivation-never-overrides"},
    "dispatch": {
        "worktree-setup-not-run",
        "worker-model-not-enforced",
        "disjointness-declared",
        "never-checked-against-work",
        "dragged-in",
        "print-brief",
    },
}


# ------------------------------------------------------------------------ c2


def test_every_clause_but_17_has_exactly_one_entry() -> None:
    clauses = [entry.clause for entry in explanations.REGISTRY]
    assert sorted(clauses) == [n for n in range(1, 21) if n != 17]
    keys = [entry.key for entry in explanations.REGISTRY]
    assert len(set(keys)) == len(keys)
    for entry in explanations.REGISTRY:
        assert entry.short and entry.full and entry.kyo_id, entry
        assert re.search(r"KYO-\d+|milestone 1", entry.kyo_id), entry
        assert len(explanations.short(entry.key)) < len(explanations.full(entry.key)), entry
    assert set().union(*EXPECTED_IN_TOUR.values()) == set(keys)


def test_no_command_prints_a_full_text_inline(kyo, git_project: Path, tmp_path: Path) -> None:
    runs = _tour(kyo, git_project, tmp_path)
    for label, result in runs.items():
        for entry in explanations.REGISTRY:
            fragment = _full_fragment(entry)
            assert fragment not in result.out, (label, entry.key, result.out)
        for key in EXPECTED_IN_TOUR[label]:
            # A short form that is itself a template is checked by its literal part.
            literal = max(
                (lit for lit, *_ in string.Formatter().parse(explanations.BY_KEY[key].short)),
                key=len,
            )
            assert literal in result.out, (label, key, result.out)


def test_each_tour_command_is_listed_by_every_entry_it_printed() -> None:
    """A pointer names the command that ran; that command's reference must list the
    entry, or the pointer leads nowhere."""
    for label, keys in EXPECTED_IN_TOUR.items():
        command = label.split()[0]
        for key in keys:
            assert command in explanations.BY_KEY[key].commands, (label, key)


# ------------------------------------------------------------------------ c3


def test_reference_lists_each_entry_under_its_commands() -> None:
    names = set(reference_command_names())
    for entry in explanations.REGISTRY:
        for command in entry.commands:
            assert command in names, (entry.key, command)
            section = reference_markdown(command=command)
            assert "### Output notes" in section, command
            assert explanations._fill(entry.full, {}) in section, (entry.key, command)  # noqa: SLF001
            assert explanations._fill(entry.short, {}) in section, (entry.key, command)  # noqa: SLF001
            assert entry.kyo_id in section
    assert "### Output notes" not in reference_markdown(command="move")


def test_kyo_reference_prints_the_output_notes(kyo) -> None:
    printed = kyo("reference", "dispatch")
    assert printed.code == 0, printed
    assert "### Output notes" in printed.out
    assert explanations.full("never-checked-against-work") in printed.out


# ------------------------------------------------------------------------ c4


def test_a_command_that_shortened_anything_ends_with_one_pointer(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    runs = _tour(kyo, git_project, tmp_path)
    for label, result in runs.items():
        pointer = explanations.footer_line(label.split()[0])
        lines = result.out.rstrip("\n").splitlines()
        assert lines[-1] == pointer, (label, result.out)
        assert result.out.count("explained in `kyo reference") == 1, (label, result.out)


def test_a_command_that_shortened_nothing_prints_no_pointer(kyo) -> None:
    record = new_record(kyo, criteria=["a", "b"])
    for args in (
        ("move", record, "working"),
        ("verify", f"{record}.c1", "--verdict", "met", "--method", "reasoned-about"),
        ("show", record),
        ("list",),
    ):
        result = kyo(*args)
        assert result.code == 0, (args, result)
        assert "explained in" not in result.out, (args, result.out)


def test_a_json_write_prints_no_pointer(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    result = kyo("gate", record, "awaiting:human-run", "--do", "look", "--json")
    assert result.code == 0, result
    assert "explained in" not in result.out


def test_the_pointer_needs_the_short_form_in_the_printed_lines() -> None:
    """Said but dropped (a composing command may leave a step's line out of its text):
    no pointer. Said and printed: exactly one."""
    explanations.reset()
    said = explanations.say("gates-orthogonal")
    assert explanations.footer("gate", ["status is unchanged"]) == []
    assert explanations.footer("gate", [f"status is unchanged ({said})"]) == [
        "explained in `kyo reference gate`"
    ]
    explanations.reset()
    assert explanations.footer("gate", [f"status is unchanged ({said})"]) == []


# ------------------------------------------------------------------------ c5


def test_no_entry_carries_a_remedy() -> None:
    for entry in explanations.REGISTRY:
        for text in (entry.short, entry.full, explanations.full(entry.key)):
            assert "`kyo " not in text, (entry.key, text)
            assert "governs:" not in text, (entry.key, text)


# ------------------------------------------------------------------------ c6


def test_refusals_are_untouched(kyo, git_project: Path, tmp_path: Path) -> None:
    record, dispatch_id, reported = _rich_report(kyo, git_project)
    assert reported.code == 0, reported
    refusals = {"accept without --reviewed": kyo("accept", dispatch_id)}
    assert kyo("accept", dispatch_id, "--reviewed").code == 0
    refusals["move onto done over an unobserved criterion"] = kyo("move", record, "done")
    a = _promote(kyo, git_project, tmp_path, "a", ["src/a.py"])
    b = _promote(kyo, git_project, tmp_path, "b", ["src/a.py"])
    assert kyo("dispatch", a, "--worktree", _worktree(tmp_path, "wa")).code == 0
    refusals["dispatch colliding on a touch"] = kyo(
        "dispatch", b, "--worktree", _worktree(tmp_path, "wb")
    )
    for label, result in refusals.items():
        assert result.code != 0, (label, result)
        text = _normalise(f"{result.code}\n{result.out}\n--err--\n{result.err}", tmp_path)
        assert_pinned(pin_name("kyo482-explanations", "refusal", label), text)


# ------------------------------------------------------------------------ c7

#: Lines of `kyo report`'s text output that carry one of the shortened clauses. Every
#: other line — the branch check, premise_false, disagreements, corrected_numbers,
#: criteria_verdicts, commits, each obligation's detail and worker proposal, the
#: settle lines and the touch-drift file list with its remedies — is the review block.
_EXPLANATION_LINE_PREFIXES = (
    "note: verdicts above",
    "docs obligations:",
    "this document governs whole files",
    "changed paths:",
    "touch drift:",
    "explained in `kyo reference",
)


def _review_block(out: str) -> str:
    return "\n".join(
        line for line in out.splitlines() if not line.strip().startswith(_EXPLANATION_LINE_PREFIXES)
    )


def test_the_report_review_block_is_byte_identical(kyo, git_project: Path, tmp_path: Path) -> None:
    _record, _dispatch_id, reported = _rich_report(kyo, git_project)
    assert reported.code == 0, reported
    block = _review_block(reported.out)
    # The block is the substance, not an empty filter result.
    for needle in (
        "branch check:",
        "premise_false (1):",
        "disagreements (1):",
        "corrected_numbers (1):",
        "criteria_verdicts (2):",
        "commits (1):",
        "worker proposes not-affected",
        "  + src/other.py",
    ):
        assert needle in block, (needle, block)
    assert_pinned("kyo482-explanations/review-block", _normalise(block, tmp_path))


@pytest.mark.parametrize("key", [entry.key for entry in explanations.REGISTRY])
def test_full_text_round_trips_through_match_full(key: str) -> None:
    values = {name: f"v-{name}" for name in explanations.fields(explanations.BY_KEY[key].full)}
    constants = explanations._constants()  # noqa: SLF001
    values = {name: value for name, value in values.items() if name not in constants}
    assert explanations.match_full(key, explanations.full(key, **values)) == values
