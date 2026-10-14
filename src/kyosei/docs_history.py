"""The history lint for ``ssot:`` document sets (KYO-555, slice 5 of KYO-551).

An ``ssot:`` set states what is true now. A sentence narrating how it came to be ("this
used to run nightly", "previously the cap was 3") belongs in a decision record or a
commit message. This module finds such sentences in what a branch newly introduces into
a set's documents, with the patterns a measurement for KYO-551 chose: 21 patterns over
four projects, 497 hits labelled by hand, of which the seven in :data:`PATTERNS` were
precise enough to keep.

A lint judging every line would be noisy. One judging only what a branch **newly
introduces** is precise: per file and per pattern, the count of matches on the branch's
added lines must exceed the count on its removed lines. Re-adding a line that already
carried the phrase (an edit to a long table row) introduces nothing, and a document the
branch adds counts all of its matches. A match counts only outside fenced code and
inline code spans.

Each set chooses with ``ssot.history_lint`` (:data:`kyosei.registry.SSOT_HISTORY_LINT_VALUES`):
``off`` runs nothing on its documents, ``warn`` (the default) prints every newly
introduced match and blocks nothing, and ``block`` also blocks a worker's stop on a newly
introduced **block-grade** match, unless a commit on the branch carries the trailer
``Kyosei-History-OK: <path> <pattern> <reason>`` naming that file and pattern. Warn-grade
matches never block. ``kyo report`` lists every such trailer with the report's
disagreements, so the orchestrator reviews each override before accepting.

Read by ``kyo check-branch`` (prints and blocks), ``kyo docs preview`` (lists) and ``kyo
report`` (lists the overrides).

The module also reads one document's whole commit history (:func:`document_history`,
KYO-562), which `kyo docs suggest` judges to decide whether a numbered file grows only at
its end before it drafts ``numbered:`` for it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import registry as registry_module
from .docs_parse import SectionStatusSet, _fenced
from .docs_regions import FileChange, branch_file_changes
from .git_queries import run_git

# ---------------------------------------------------------------- the measured patterns
#
# Verbatim from KYO-555's description, which is verbatim from the measurement. Python
# regexes, applied case-insensitively to one line at a time.

MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
DATE = r"(?:\d{4}-\d\d(?:-\d\d)?|{MONTH}\.? \d{1,2}|\d{1,2} {MONTH}|{MONTH} \d{4})".replace(
    "{MONTH}", MONTH
)
REC = r"\b[A-Z][A-Z0-9]{1,5}-\d+(?:\.[a-z]?\d+)?\b"

#: A newly introduced match of this grade blocks a worker's stop in a ``block`` set.
GRADE_BLOCK = "block"
#: A newly introduced match of this grade is printed and never blocks.
GRADE_WARN = "warn"


@dataclass(frozen=True)
class HistoryPattern:
    """One measured pattern: its name (what a trailer names), grade and regex."""

    name: str
    grade: str
    regex: str
    #: The measurement's precision, stated beside the pattern it scored.
    precision: str

    @property
    def compiled(self) -> re.Pattern[str]:
        return _compiled(self.regex)


_COMPILED: dict[str, re.Pattern[str]] = {}


def _compiled(regex: str) -> re.Pattern[str]:
    if regex not in _COMPILED:
        _COMPILED[regex] = re.compile(regex, re.IGNORECASE)
    return _COMPILED[regex]


#: The seven patterns, in the measurement's order, block grade first.
PATTERNS: tuple[HistoryPattern, ...] = (
    HistoryPattern(
        "used-to",
        GRADE_BLOCK,
        r"(?<!\bis )(?<!\bare )(?<!\bbe )(?<!\bwas )(?<!\bwere )(?<!\bbeen )(?<!\bbeing )"
        r"(?<!\bget )(?<!\bgets )(?<!\bgot )(?<!\bnever )(?<!\bnot )(?<!\bonly )(?<!\balso )"
        r"\bused to\b",
        "97% (65 hits)",
    ),
    HistoryPattern("previously", GRADE_BLOCK, r"\bpreviously\b", "100% (13)"),
    HistoryPattern(
        "no-longer",
        GRADE_WARN,
        r"\bno longer\b(?! than)(?<!\bthat no longer)(?<!\bwhich no longer)"
        r"(?<!\bthat are no longer)(?<!\bthat can no longer)(?<!\bor no longer)",
        "88% (84)",
    ),
    HistoryPattern(
        "was-changed",
        GRADE_WARN,
        r"\b(?:was|were|has been|have been) (?:renamed|replaced|removed|dropped|retired"
        r"|moved|changed|rewritten|reverted|deleted)\b",
        "92% (36)",
    ),
    HistoryPattern(
        "until",
        GRADE_WARN,
        r"\buntil (?:{DATE}|{REC}|recently)".replace("{DATE}", DATE).replace("{REC}", REC),
        "87% (30)",
    ),
    HistoryPattern("anymore", GRADE_WARN, r"\bany ?more\b", "86% (7)"),
    HistoryPattern(
        "dated-change",
        GRADE_WARN,
        r"(?=.*\b20\d\d-\d\d(?:-\d\d)?\b)(?=.*\b(?:fixed|added|removed|deleted|dropped"
        r"|renamed|replaced|rewritten|changed|retired|moved|shortened|raised|used to"
        r"|no longer|until|before (?:it|that|this)|now)\b)",
        "88% (sampled)",
    ),
)

PATTERN_NAMES: tuple[str, ...] = tuple(pattern.name for pattern in PATTERNS)
_BY_NAME = {pattern.name: pattern for pattern in PATTERNS}

#: The trailer a commit carries to let one block-grade match through, naming the file and
#: the pattern: ``Kyosei-History-OK: <path> <pattern> <reason>``.
TRAILER = "Kyosei-History-OK"

#: What every match's line says to do instead.
REMEDY = (
    "state the current fact; put the history in a decision record or the commit message"
)


# ------------------------------------------------------------------ reading one document

#: An inline code span: a run of backticks, its content, the same run closing it.
_CODE_SPAN_RE = re.compile(r"(`+)(?!`).*?(?<!`)\1(?!`)")


def prose_lines(text: str | None) -> dict[int, str]:
    """``{line number: line}`` for every line of ``text`` outside fenced code, with its
    inline code spans blanked, numbered from 1 the way a diff's hunks number them."""
    if text is None:
        return {}
    found: dict[int, str] = {}
    for number, (line, in_code) in enumerate(_fenced(text.split("\n")), start=1):
        if not in_code:
            found[number] = _CODE_SPAN_RE.sub(" ", line)
    return found


def matches_in(pattern: HistoryPattern, line: str) -> int:
    """How many times ``pattern`` matches ``line``. ``dated-change`` is two lookaheads
    over the whole line, so it says something about a line and counts once."""
    compiled = pattern.compiled
    if pattern.name == "dated-change":
        return 1 if compiled.match(line) else 0
    return sum(1 for _ in compiled.finditer(line))


# --------------------------------------------------------------- newly introduced matches


@dataclass(frozen=True)
class HistoryMatch:
    """One pattern a branch newly introduced into one ssot document, with every added
    line it matches on."""

    path: str
    pattern: str
    grade: str
    #: The set's ``history_lint`` (``warn`` or ``block``).
    mode: str
    set_name: str
    #: ``(line number on the branch, the line)`` for every added line it matches.
    lines: tuple[tuple[int, str], ...]
    added: int
    removed: int
    #: Whether a ``Kyosei-History-OK`` trailer on the branch names this path and pattern.
    overridden: bool = False

    @property
    def introduced(self) -> int:
        return self.added - self.removed

    @property
    def blocks(self) -> bool:
        return (
            self.mode == registry_module.SSOT_HISTORY_LINT_BLOCK
            and self.grade == GRADE_BLOCK
            and not self.overridden
        )

    def line_texts(self) -> list[str]:
        """One line per matching added line: file, line, pattern, and the remedy."""
        counted = (
            f"{self.introduced} new on this branch"
            if not self.removed
            else f"{self.introduced} new on this branch ({self.added} on added lines, "
            f"{self.removed} on removed)"
        )
        if self.overridden:
            verdict = f"let through by a {TRAILER} trailer"
        elif self.blocks:
            verdict = "BLOCKS (set declares history_lint: block)"
        else:
            verdict = f"warning ({self.grade} grade, set history_lint: {self.mode})"
        return [
            f"{self.path}:{number}: {self.pattern} — {verdict}; {counted}: "
            f"{_excerpt(text)!r} — remedy: {REMEDY}"
            for number, text in self.lines
        ]

    def as_json(self) -> dict:
        return {
            "path": self.path,
            "pattern": self.pattern,
            "grade": self.grade,
            "mode": self.mode,
            "set": self.set_name,
            "lines": [{"line": number, "text": text} for number, text in self.lines],
            "added": self.added,
            "removed": self.removed,
            "introduced": self.introduced,
            "overridden": self.overridden,
            "blocks": self.blocks,
        }


def _excerpt(text: str, limit: int = 120) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def introduced_matches(
    change: FileChange, *, mode: str, set_name: str
) -> list[HistoryMatch]:
    """Each pattern that ``change`` newly introduces, as defined in the module docstring.

    A file the branch deleted introduces nothing. A file the branch added has every line
    added and none removed, so all of its matches count.
    """
    if change.after_text is None:
        return []
    after = prose_lines(change.after_text)
    before = prose_lines(change.before_text)
    added_lines = [(n, after[n]) for n in sorted(change.after_lines) if n in after]
    removed_lines = [before[n] for n in sorted(change.before_lines) if n in before]
    found: list[HistoryMatch] = []
    for pattern in PATTERNS:
        hits = [(n, line) for n, line in added_lines if matches_in(pattern, line)]
        added = sum(matches_in(pattern, line) for _, line in hits)
        removed = sum(matches_in(pattern, line) for line in removed_lines)
        if added > removed:
            found.append(
                HistoryMatch(
                    path=change.path,
                    pattern=pattern.name,
                    grade=pattern.grade,
                    mode=mode,
                    set_name=set_name,
                    lines=tuple(hits),
                    added=added,
                    removed=removed,
                )
            )
    return found


# --------------------------------------------------------------------- trailer overrides


@dataclass(frozen=True)
class HistoryOverride:
    """One ``Kyosei-History-OK`` trailer on a commit of the branch."""

    sha: str
    path: str
    pattern: str
    reason: str

    def line(self) -> str:
        return (
            f"{TRAILER} on {self.sha[:12]}: {self.path} {self.pattern} — "
            f"{self.reason or '(no reason given)'}"
        )

    def as_json(self) -> dict:
        return {
            "sha": self.sha,
            "path": self.path,
            "pattern": self.pattern,
            "reason": self.reason,
        }


def branch_overrides(repo: Path, against: str, branch: str) -> list[HistoryOverride]:
    """Every ``Kyosei-History-OK`` trailer on the commits ``branch`` has and ``against``
    does not, oldest first. Empty when git cannot answer: an override nobody can read
    lets nothing through."""
    try:
        result = run_git(
            repo,
            "log",
            "--reverse",
            f"--format=%H%x1f%(trailers:key={TRAILER},valueonly=true,unfold=true)%x1e",
            f"{against}..{branch}",
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    found: list[HistoryOverride] = []
    for record in result.stdout.split("\x1e"):
        sha, _, values = record.strip("\n").partition("\x1f")
        sha = sha.strip()
        for value in values.splitlines():
            parts = value.strip().split(None, 2)
            if len(parts) < 2:
                continue
            found.append(
                HistoryOverride(
                    sha=sha,
                    path=parts[0],
                    pattern=parts[1],
                    reason=parts[2] if len(parts) > 2 else "",
                )
            )
    return found


# ---------------------------------------------------------------------- the branch's lint


@dataclass(frozen=True)
class HistoryLint:
    """What the history lint found on one branch."""

    matches: tuple[HistoryMatch, ...] = ()
    overrides: tuple[HistoryOverride, ...] = ()
    #: The ssot documents the branch changed that the lint read.
    documents: tuple[str, ...] = ()

    @property
    def blocking(self) -> list[HistoryMatch]:
        return [match for match in self.matches if match.blocks]

    def lines(self) -> list[str]:
        out: list[str] = []
        for match in self.matches:
            out += match.line_texts()
        return out

    def as_json(self) -> dict:
        return {
            "documents": list(self.documents),
            "matches": [match.as_json() for match in self.matches],
            "overrides": [override.as_json() for override in self.overrides],
            "blocking": len(self.blocking),
        }


def linted_documents(
    sets: Sequence[SectionStatusSet], changed: Sequence[str]
) -> dict[str, SectionStatusSet]:
    """Each changed path in a document set declaring ``ssot:`` with ``history_lint`` other
    than ``off``, mapped to that set (the first set whose globs match it, in declaration
    order — :func:`kyosei.docs_check.opted_documents`' rule)."""
    found: dict[str, SectionStatusSet] = {}
    for path in changed:
        for chosen in sets:
            if not any(registry_module.glob_matches(glob, path) for glob in chosen.globs):
                continue
            if (
                chosen.ssot is not None
                and chosen.ssot.history_lint != registry_module.SSOT_HISTORY_LINT_OFF
            ):
                found[path] = chosen
            break
    return found


def branch_history_lint(
    repo: Path,
    against: str,
    branch: str,
    changed: Sequence[str],
    sets: Sequence[SectionStatusSet],
) -> HistoryLint:
    """The history lint over the ssot documents ``against...branch`` changed."""
    wanted = linted_documents(sets, changed)
    if not wanted:
        return HistoryLint()
    changes = branch_file_changes(repo, against, branch, sorted(wanted))
    overrides = branch_overrides(repo, against, branch)
    allowed = {(override.path, override.pattern) for override in overrides}
    matches: list[HistoryMatch] = []
    for path, chosen in sorted(wanted.items()):
        change = changes.get(path)
        if change is None or chosen.ssot is None:
            continue
        for match in introduced_matches(
            change, mode=chosen.ssot.history_lint, set_name=chosen.name
        ):
            if (match.path, match.pattern) in allowed:
                match = HistoryMatch(**{**match.__dict__, "overridden": True})
            matches.append(match)
    return HistoryLint(
        matches=tuple(matches), overrides=tuple(overrides), documents=tuple(sorted(wanted))
    )


def pattern(name: str) -> HistoryPattern:
    """The pattern named ``name``."""
    return _BY_NAME[name]


# ------------------------------------------------------- one document's commit history
#
# KYO-562: `kyo docs suggest` drafts ``numbered:`` only for a file whose history shows it
# growing at its end, so it needs every revision of one document and where each commit
# changed it. One ``git log`` call reads all of it: the first-parent chain (so the
# revisions form one line and each diff is against the revision before it), followed
# across renames, at ``--unified=0`` so the hunk headers are the changed line numbers.
# Each revision's text is rebuilt by applying those hunks in order from the commit that
# created the file, and every removed line is checked against the text being rebuilt —
# a mismatch means this reader misread something, and the answer is "cannot tell", never
# a guess.

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class Hunk:
    """One ``--unified=0`` hunk. ``old_count == 0`` is a pure insertion after line
    ``old_start`` of the revision before (0: at the top)."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int


@dataclass(frozen=True)
class DocumentRevision:
    """One commit on the first-parent chain that changed a document."""

    sha: str
    #: The document as the commit found it; ``None`` for the commit that created it.
    before_text: str | None
    #: The document as the commit left it (``""`` if the commit deleted it).
    after_text: str
    hunks: tuple[Hunk, ...]


def document_history(repo: Path, path: str) -> tuple[DocumentRevision, ...] | None:
    """Every commit that changed ``path`` on HEAD's first-parent chain, oldest first.

    ``path`` is relative to ``repo``, which may be a subdirectory of the work tree. An
    empty tuple is a real answer (git knows the file has no commits: it is untracked);
    ``None`` is "git could not say": not a repository, git missing or timed out, or a
    diff this reader could not apply exactly.
    """
    try:
        result = run_git(
            repo,
            "log",
            "--first-parent",
            "--diff-merges=first-parent",
            "--follow",
            "--format=%x1e%H",
            "--unified=0",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "-p",
            "--",
            path,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    records = [record for record in result.stdout.split("\x1e") if record.strip()]
    lines: list[str] = []
    found: list[DocumentRevision] = []
    for index, record in enumerate(reversed(records)):
        sha, _, diff = record.partition("\n")
        parsed = _hunks(diff)
        if parsed is None:
            return None
        before = list(lines)
        for hunk, removed, added in reversed(parsed):
            at = hunk.old_start - 1 if hunk.old_count else hunk.old_start
            if lines[at : at + hunk.old_count] != removed:
                return None
            lines[at : at + hunk.old_count] = added
        created = index == 0 or "\nnew file mode" in f"\n{diff}"
        found.append(
            DocumentRevision(
                sha=sha.strip(),
                before_text=None if created else "\n".join(before),
                after_text="\n".join(lines),
                hunks=tuple(hunk for hunk, _, _ in parsed),
            )
        )
    return tuple(found)


def _hunks(diff: str) -> list[tuple[Hunk, list[str], list[str]]] | None:
    """Each hunk of one commit's ``--unified=0`` diff with its removed and added lines,
    read by the counts in its header, or ``None`` if the diff runs short of them."""
    rows = diff.split("\n")
    found: list[tuple[Hunk, list[str], list[str]]] = []
    position = 0
    while position < len(rows):
        header = _HUNK_RE.match(rows[position])
        position += 1
        if not header:
            continue
        old_start, old_count, new_start, new_count = (
            int(value) if value is not None else 1 for value in header.groups()
        )
        hunk = Hunk(old_start, old_count, new_start, new_count)
        taken: dict[str, list[str]] = {"-": [], "+": []}
        for sign, count in (("-", old_count), ("+", new_count)):
            while len(taken[sign]) < count:
                if position >= len(rows):
                    return None
                row = rows[position]
                position += 1
                if row.startswith("\\"):
                    continue
                if not row.startswith(sign):
                    return None
                taken[sign].append(row[1:])
        found.append((hunk, taken["-"], taken["+"]))
    return found


__all__ = [
    "DATE",
    "GRADE_BLOCK",
    "GRADE_WARN",
    "MONTH",
    "PATTERNS",
    "PATTERN_NAMES",
    "REC",
    "REMEDY",
    "TRAILER",
    "DocumentRevision",
    "HistoryLint",
    "HistoryMatch",
    "HistoryOverride",
    "HistoryPattern",
    "Hunk",
    "branch_history_lint",
    "branch_overrides",
    "document_history",
    "introduced_matches",
    "linted_documents",
    "matches_in",
    "pattern",
    "prose_lines",
]
