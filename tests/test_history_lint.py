"""KYO-555: the history lint in ``ssot:`` sets — diary prose a branch newly introduces
into a set's documents is printed, and blocks a worker's stop per set
(src/kyosei/docs_history.py, docs/reference/topics/docs-governance.md)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from kyosei import docs_history
from kyosei import registry as registry_module
from kyosei.contracts import CONTRACT_HISTORY_PROSE_INTRODUCED
from kyosei.docs_parse import ssot_declaration
from kyosei.docs_regions import FileChange

from .conftest import git_init, new_record, valid_report

_REPO_ROOT = Path(__file__).resolve().parent.parent

# An id-bearing ssot set and an id-less one, each with its own history_lint.
_SETS = """\
documents:
  specs:
    summary: The specs.
    globs: [docs/specs/**/*.md]
    section_status:
      statuses: [current, superseded]
    ssot:
      id_heading: '{{id}}. {{title}}'
      id_pattern: '[A-Z]{{1,4}}-\\d+'
      prefixes:
        A: docs/specs/a.md
{specs_lint}  reference:
    summary: The reference.
    globs: [docs/reference/**/*.md]
    section_status:
      statuses: [current, superseded]
    ssot: {{{reference_lint}}}
  notes:
    summary: Not an ssot set.
    globs: [docs/notes/**/*.md]
"""


def _sets(specs: str | None = None, reference: str | None = None) -> str:
    return _SETS.format(
        specs_lint=f"      history_lint: {specs}\n" if specs is not None else "",
        reference_lint=f"history_lint: {reference}" if reference is not None else "",
    )


def _write(project: Path, rel: str, text: str) -> None:
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(project: Path, message: str) -> str:
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", message)
    return _git(project, "rev-parse", "HEAD")


_BASE_DOC = "## Running\n\nThe sweep runs hourly.\n\nIt used to run nightly, says this row.\n"


def _branch(project: Path, sets: str, files: dict[str, str], base: dict[str, str] | None = None):
    """``main`` carrying ``sets`` and ``base``; a ``feature`` branch writing ``files``."""
    _write(project, "kyosei/documents.yml", sets)
    for rel, text in (base or {}).items():
        _write(project, rel, text)
    git_init(project)
    _git(project, "checkout", "-q", "-b", "feature")
    for rel, text in files.items():
        _write(project, rel, text)
    _commit(project, "work")


def _stop(kyo, project: Path, *args: str):
    payload = json.dumps(
        {"session_id": "s", "cwd": str(project), "hook_event_name": "SubagentStop",
         "stop_hook_active": False}
    )
    return kyo("check-branch", "--subagent-stop", "--against", "main", *args, stdin=payload)


def _history(result) -> dict:
    data = result.json
    return (data.get("data") or data)["history_lint"]


# ------------------------------------------------------------- c1: the documents.yml key


@pytest.mark.parametrize("value", ["off", "warn", "block", "'off'"])
@pytest.mark.parametrize("which", ["specs", "reference"])
def test_history_lint_is_accepted_on_a_set_with_or_without_ids(
    kyo, project: Path, value: str, which: str
) -> None:
    """A bare `off` is YAML's False; it is `off` all the same."""
    _write(project, "kyosei/documents.yml", _sets(**{which: value}))
    result = kyo("docs", "check")
    assert result.code == 0, result
    assert f"history lint {value.strip(chr(39))}" in result.out


def test_history_lint_defaults_to_warn() -> None:
    declaration = ssot_declaration({})
    assert declaration is not None
    assert declaration.history_lint == registry_module.SSOT_HISTORY_LINT_DEFAULT == "warn"
    assert ssot_declaration({"history_lint": False}).history_lint == "off"
    assert ssot_declaration({"history_lint": "block"}).history_lint == "block"


@pytest.mark.parametrize("which", ["specs", "reference"])
@pytest.mark.parametrize("bad", ["loud", "on", "3", "'Warn'"])
def test_any_other_value_is_refused_naming_the_three_and_the_remedy_clears(
    kyo, project: Path, which: str, bad: str
) -> None:
    _write(project, "kyosei/documents.yml", _sets(**{which: bad}))
    result = kyo("docs", "check")
    assert result.code != 0, result
    assert "history_lint must be one of off, warn, block" in result.err, result.err

    _write(project, "kyosei/documents.yml", _sets(**{which: "warn"}))
    assert kyo("docs", "check").code == 0


# --------------------------------------------------------- c2: the patterns, verbatim

_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_DATE = r"(?:\d{4}-\d\d(?:-\d\d)?|{MONTH}\.? \d{1,2}|\d{1,2} {MONTH}|{MONTH} \d{4})"
_REC = r"\b[A-Z][A-Z0-9]{1,5}-\d+(?:\.[a-z]?\d+)?\b"

# KYO-555's description, line for line: (name, grade, regex as written there).
_MEASURED = [
    ("used-to", "block",
     r"(?<!\bis )(?<!\bare )(?<!\bbe )(?<!\bwas )(?<!\bwere )(?<!\bbeen )(?<!\bbeing )"
     r"(?<!\bget )(?<!\bgets )(?<!\bgot )(?<!\bnever )(?<!\bnot )(?<!\bonly )(?<!\balso )"
     r"\bused to\b"),
    ("previously", "block", r"\bpreviously\b"),
    ("no-longer", "warn",
     r"\bno longer\b(?! than)(?<!\bthat no longer)(?<!\bwhich no longer)"
     r"(?<!\bthat are no longer)(?<!\bthat can no longer)(?<!\bor no longer)"),
    ("was-changed", "warn",
     r"\b(?:was|were|has been|have been) (?:renamed|replaced|removed|dropped|retired|moved"
     r"|changed|rewritten|reverted|deleted)\b"),
    ("until", "warn", r"\buntil (?:{DATE}|{REC}|recently)"),
    ("anymore", "warn", r"\bany ?more\b"),
    ("dated-change", "warn",
     r"(?=.*\b20\d\d-\d\d(?:-\d\d)?\b)(?=.*\b(?:fixed|added|removed|deleted|dropped|renamed"
     r"|replaced|rewritten|changed|retired|moved|shortened|raised|used to|no longer|until"
     r"|before (?:it|that|this)|now)\b)"),
]


def test_the_seven_patterns_are_the_measured_ones_verbatim_with_their_grades() -> None:
    assert docs_history.MONTH == _MONTH
    expected_date = _DATE.replace("{MONTH}", _MONTH)
    assert expected_date == docs_history.DATE
    assert docs_history.REC == _REC
    expanded = [
        (name, grade, regex.replace("{DATE}", docs_history.DATE).replace("{REC}", _REC))
        for name, grade, regex in _MEASURED
    ]
    assert [(p.name, p.grade, p.regex) for p in docs_history.PATTERNS] == expanded
    assert all(p.compiled.flags & 2 for p in docs_history.PATTERNS)  # re.IGNORECASE


# One hit each pattern must flag and one false positive it must not, from the kinds of
# hit the measurement named (KYO-551.r1): passive "is used to", "no longer than", a
# record-id "Since KYO-n", "until" naming an ordinary noun, a date with no change verb.
_LABELLED = [
    ("used-to", "The sweep used to run nightly; it runs hourly now.",
     "The token is used to sign each request."),
    ("previously", "Previously the cap was 3 workers.",
     "Remove the previous-run marker before starting."),
    ("no-longer", "The flag no longer exists.",
     "Keep each line no longer than 100 characters."),
    ("was-changed", "The field was renamed to `owner` in this release.",
     "A field that is renamed keeps its old name as an alias."),
    ("until", "The panel stayed hidden until 2026-09-01.",
     "The guard waits until the worker stops."),
    ("anymore", "Workers do not commit the report anymore.",
     "Add more workers when the queue grows."),
    ("dated-change", "On 2026-09-03 the default changed to warn.",
     "Since KYO-5 the flag is on (released 2026-09-03 with the CLI)."),
]


@pytest.mark.parametrize(("name", "hit", "false_positive"), _LABELLED)
def test_each_pattern_flags_its_labelled_hit_and_not_its_false_positive(
    name: str, hit: str, false_positive: str
) -> None:
    pattern = docs_history.pattern(name)
    assert docs_history.matches_in(pattern, hit) == 1, hit
    assert docs_history.matches_in(pattern, false_positive) == 0, false_positive


def test_since_a_record_id_is_provenance_and_flagged_by_nothing() -> None:
    line = "Since KYO-412 the export carries the field."
    assert [p.name for p in docs_history.PATTERNS if docs_history.matches_in(p, line)] == []


# ------------------------------------------- c3: newly introduced, outside code, ssot only


def _change(before: str | None, after: str | None, before_lines=(), after_lines=()):
    return FileChange(
        path="docs/specs/a.md",
        before_text=before,
        after_text=after,
        before_lines=frozenset(before_lines),
        after_lines=frozenset(after_lines),
    )


def _found(change: FileChange) -> list[tuple[str, int]]:
    return [
        (match.pattern, match.introduced)
        for match in docs_history.introduced_matches(change, mode="warn", set_name="specs")
    ]


def test_a_re_added_line_carrying_used_to_raises_nothing() -> None:
    before = "| a | It used to run nightly | x |\n"
    after = "| a | It used to run nightly | y |\n"
    assert _found(_change(before, after, {1}, {1})) == []


def test_an_added_line_with_a_new_phrase_raises_and_an_added_document_counts_all() -> None:
    before = "Runs hourly.\n"
    after = "Runs hourly.\nIt used to run nightly.\n"
    assert _found(_change(before, after, (), {2})) == [("used-to", 1)]
    added = "Previously one.\nPreviously two.\n"
    assert _found(_change(None, added, (), {1, 2})) == [("previously", 2)]


def test_more_added_than_removed_counts_the_difference() -> None:
    before = "It used to run nightly.\n"
    after = "It used to run nightly, and it used to page.\n"
    assert _found(_change(before, after, {1}, {1})) == [("used-to", 1)]


def test_fenced_code_and_inline_code_spans_are_not_prose() -> None:
    after = "Prose.\n```\nit used to run\n```\nThe phrase `used to` is linted.\n"
    assert _found(_change(None, after, (), {1, 2, 3, 4, 5})) == []


def test_check_branch_lints_only_documents_of_an_ssot_set(kyo, project: Path) -> None:
    _branch(
        project,
        _sets(),
        {
            "docs/notes/n.md": "It used to run nightly.\n",
            "docs/specs/a.md": "## A-1. Runs\n\nIt used to run nightly.\n",
        },
    )
    history = _history(kyo("check-branch", "--against", "main", "--json"))
    assert [(m["path"], m["pattern"]) for m in history["matches"]] == [
        ("docs/specs/a.md", "used-to")
    ]


def test_a_re_added_line_raises_nothing_at_the_stop(kyo, project: Path) -> None:
    _branch(
        project,
        _sets(specs="block"),
        {"docs/specs/a.md": _BASE_DOC.replace("this row", "this row, edited")},
        base={"docs/specs/a.md": _BASE_DOC},
    )
    result = _stop(kyo, project)
    assert result.code == 0, result
    assert "history lint" not in result.out


def test_history_lint_off_lints_nothing(kyo, project: Path) -> None:
    _branch(project, _sets(specs="off"), {"docs/specs/a.md": "It used to run nightly.\n"})
    assert _history(kyo("check-branch", "--against", "main", "--json"))["matches"] == []


# ------------------------------------------------- c4: printed at the stop, warn never blocks


def test_a_warn_set_prints_file_line_pattern_and_remedy_and_does_not_block(
    kyo, project: Path
) -> None:
    _branch(
        project,
        _sets(),
        {
            "docs/specs/a.md": (
                "## A-1. Runs\n\nIt used to run nightly.\n\nThe flag no longer exists.\n"
            )
        },
    )
    result = _stop(kyo, project)
    assert result.code == 0, result
    assert "docs/specs/a.md:3: used-to" in result.out
    assert "docs/specs/a.md:5: no-longer" in result.out
    assert docs_history.REMEDY in result.out
    assert "none blocks this stop" in result.out


def test_a_warn_grade_match_in_a_block_set_does_not_block(kyo, project: Path) -> None:
    _branch(
        project,
        _sets(reference="block"),
        {"docs/reference/r.md": "## R\n\nThe flag no longer exists.\n"},
    )
    result = _stop(kyo, project)
    assert result.code == 0, result
    assert "docs/reference/r.md:3: no-longer" in result.out


# ------------------------------------------------ c5: a block set blocks, a trailer lets one by


def test_a_block_set_blocks_a_block_grade_match_and_the_trailer_clears_it(
    kyo, project: Path
) -> None:
    _branch(
        project,
        _sets(reference="block"),
        {"docs/reference/r.md": "## R\n\nThe sweep used to run nightly.\n"},
    )
    refused = _stop(kyo, project)
    assert refused.code == 2, refused
    message = refused.out + refused.err
    assert CONTRACT_HISTORY_PROSE_INTRODUCED in message
    assert "docs/reference/r.md:3: used-to" in message
    assert docs_history.REMEDY in message
    assert f"{docs_history.TRAILER}: <path> <pattern> <reason>" in message
    assert _history(_stop(kyo, project, "--json"))["blocking"] == 1

    # A trailer naming another pattern, or another file, lets nothing through.
    _git(project, "commit", "-q", "--allow-empty", "-m", "note",
         "-m", f"{docs_history.TRAILER}: docs/reference/r.md previously not this one")
    _git(project, "commit", "-q", "--allow-empty", "-m", "note",
         "-m", f"{docs_history.TRAILER}: docs/reference/other.md used-to not this one")
    assert _stop(kyo, project).code == 2

    _git(project, "commit", "-q", "--allow-empty", "-m", "keep it",
         "-m", f"{docs_history.TRAILER}: docs/reference/r.md used-to quoting a changelog")
    cleared = _stop(kyo, project)
    assert cleared.code == 0, cleared
    assert "let through by a Kyosei-History-OK trailer" in cleared.out


def test_rewriting_the_sentence_as_the_current_fact_clears_the_block(
    kyo, project: Path
) -> None:
    _branch(
        project,
        _sets(specs="block"),
        {"docs/specs/a.md": "## A-1. Runs\n\nPreviously the sweep ran nightly.\n"},
    )
    assert _stop(kyo, project).code == 2
    _write(project, "docs/specs/a.md", "## A-1. Runs\n\nThe sweep runs hourly.\n")
    _commit(project, "state the current fact")
    assert _stop(kyo, project).code == 0


# ------------------------------------------------------ c6: kyo report lists the trailers


def test_kyo_report_lists_every_trailer_with_the_disagreements(kyo, project: Path) -> None:
    _write(project, "kyosei/documents.yml", _sets(reference="block"))
    git_init(project)
    record = new_record(kyo, criteria=["a"])
    _git(project, "checkout", "-q", "-b", "kyo-history")
    _write(project, "docs/reference/r.md", "## R\n\nThe sweep used to run nightly.\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "work", "-m",
         f"{docs_history.TRAILER}: docs/reference/r.md used-to quoting the old manual")
    _git(project, "checkout", "-q", "main")
    dispatched = kyo("dispatch", record, "--branch", "kyo-history", "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    sha = _git(project, "rev-parse", "kyo-history")
    path = project.parent / "report.json"
    path.write_text(
        json.dumps(valid_report(dispatch_id=dispatch_id, record_id=record, worked_from_sha=sha)),
        encoding="utf-8",
    )
    result = kyo("report", dispatch_id, "--file", str(path))
    assert result.code == 0, result
    block = result.out.split("disagreements (0):", 1)[1].split("corrected_numbers", 1)[0]
    assert "history-lint overrides on kyo-history (1)" in block
    assert "docs/reference/r.md used-to — quoting the old manual" in block


# ----------------------------------------------------------- c7: kyo docs preview lists them


def test_docs_preview_lists_both_grades(kyo, project: Path) -> None:
    _branch(
        project,
        _sets(reference="block"),
        {"docs/reference/r.md": "## R\n\nPreviously nightly.\n\nThe flag no longer exists.\n"},
    )
    result = kyo("docs", "preview", "--against", "main", "--branch", "feature")
    assert result.code == 0, result
    assert "docs/reference/r.md:3: previously" in result.out
    assert "docs/reference/r.md:5: no-longer" in result.out
    assert "1 of which would block `kyo check-branch`" in result.out
    data = kyo("docs", "preview", "--against", "main", "--branch", "feature", "--json").json
    history = (data.get("data") or data)["history_lint"]
    assert sorted(m["grade"] for m in history["matches"]) == ["block", "warn"]


# --------------------------------------------------------------- c8: the documentation


def test_the_reference_documents_the_key_patterns_grades_and_trailer() -> None:
    text = (_REPO_ROOT / "docs/reference/topics/docs-governance.md").read_text(encoding="utf-8")
    assert "history_lint" in text
    for value in registry_module.SSOT_HISTORY_LINT_VALUES:
        assert f"`{value}`" in text, value
    for pattern in docs_history.PATTERNS:
        assert f"`{pattern.name}`" in text, pattern.name
    assert "newly introduced" in text.lower()
    assert docs_history.TRAILER in text


def test_the_guide_says_when_to_choose_warn_or_block() -> None:
    text = (_REPO_ROOT / "docs/guide/06-kinds-scopes-documents.md").read_text(encoding="utf-8")
    assert "history_lint" in text and "block" in text and "warn" in text


def test_the_charter_layer_3_ssot_bullet_lists_the_lint() -> None:
    text = (_REPO_ROOT / "docs/design/charter.md").read_text(encoding="utf-8")
    bullet = text.split("**Ids with one owner**", 1)[1].split("  - *", 1)[0]
    assert "history_lint" in bullet
