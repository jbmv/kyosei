"""Milestone 6 (KYO-8): the mechanics behind ``kyo sweep`` and the creation-time
duplicate check — pure functions over plain data, no :class:`kyosei.store.Store`
dependency, mirroring how :mod:`kyosei.brief` composes a dispatch brief from
plain dicts rather than reaching back into the database itself. ``cmd_sweep.py``
owns the database reads/writes and calls into this module for the actual
detection logic, which keeps the string-matching and set-arithmetic testable
without a project fixture.

Four flag classes: the three KYO-8.c1/c2/c3 asked for (touch-changed,
touch-missing, near-duplicate-criteria) and KYO-137.c7's
description-predates-claim (:func:`find_descriptions_predating_their_claims`).
``docs/design/charter.md``'s "Sweep" section names several more (claims aged
past N days, doc-``governs:`` staleness, moved-but-inverse /
moved-but-not-merged); none of those is built here — KYO-8 left them out because
no record carried an acceptance criterion for them, and building them anyway
would have meant inventing untested behavior no one asked to verify.

Near-duplicate detection (KYO-8.c3/c4): stdlib-only (:mod:`difflib`), tuned for
precision over recall per this build's task brief — "a false 'duplicate!'
warning trains people to ignore it." Two knobs keep it conservative:

* a minimum normalised length, so two three-word criteria don't collide on
  sheer shortness;
* a high similarity threshold (0.90 on :class:`difflib.SequenceMatcher`'s
  ratio over normalised text — case-folded, punctuation stripped, whitespace
  collapsed).
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# The one internal import in this module, and it buys the same thing every other
# reference to `contracts` buys: "what does this touch path claim" has one answer
# (KYO-209), shared with the disjointness check and touch drift, rather than a second
# copy of the rule living here where only sweep can see it drift.
from .contracts import (
    ADDITIVE_SYMBOL,
    FLAG_TYPE_TOUCH_CHANGED,
    TOUCH_ADDITIVE_BRIEF_NOTE,
    TOUCH_ADDITIVE_SYMBOL,
    is_directory_touch,
    is_new_file_touch,
    touch_covers,
)

# KYO-300: and the second, for the same reason. "Does this file contain that symbol" is
# already answered once, wrap-tolerantly and whole-word, by the relation
# `_validate_touches` accepts a `path#symbol` touch by; asking it differently here would
# mean a touch that validates and then cannot be found.
from .prose import contains_phrase, phrase_words

#: Below this many normalised characters, two texts are never compared — short
#: criteria/titles collide on sheer brevity ("returns 200", "returns 201") at
#: high ratios despite meaning different things; precision over recall.
MIN_NORMALISED_LENGTH = 16

#: difflib.SequenceMatcher ratio threshold. Chosen high on purpose: a
#: near-duplicate flag is meant to be trusted, not triaged away.
SIMILARITY_THRESHOLD = 0.90

_PUNCT_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalise_text(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — the only
    normalisation applied before comparison; nothing here rewrites the stored
    text itself (sweep flags, never edits)."""
    folded = _PUNCT_RE.sub(" ", text.lower())
    return _WHITESPACE_RE.sub(" ", folded).strip()


def similarity_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def is_near_duplicate(a: str, b: str) -> tuple[bool, float]:
    """Whether ``a`` and ``b`` are a near-duplicate pair, and their ratio.

    Returns ``(False, ratio)`` — never raises — for texts too short to compare
    meaningfully, so callers can always log the ratio without a branch.
    """
    norm_a, norm_b = normalise_text(a), normalise_text(b)
    if len(norm_a) < MIN_NORMALISED_LENGTH or len(norm_b) < MIN_NORMALISED_LENGTH:
        return False, 0.0
    ratio = similarity_ratio(norm_a, norm_b)
    return ratio >= SIMILARITY_THRESHOLD, ratio


@dataclass(frozen=True)
class CriterionText:
    record_id: str
    criterion_id: str
    text: str


@dataclass(frozen=True)
class RecordClaims:
    """One record's description-edit time and the live claims attached to it (c7).

    A value type rather than a row, for the reason every other input in this module is
    one: sweep computes and never queries, so what it needs is stated as something a
    caller fills in — which is also what lets the class be tested without a database.

    ``description_at`` is when the description was last EDITED, which is not
    ``records.updated_at``: that moves when anything on the record moves, so comparing a
    claim against it would answer "has this record changed since the claim was
    recorded" — a question whose answer is nearly always yes, and not the question. The
    events ledger is what knows the real answer
    (:meth:`kyosei.store.Store.description_edited_at`).
    """

    record_id: str
    description_at: str
    #: ``(claim_id, created_at, statement)`` for each claim nothing has superseded.
    claims: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class DescriptionPredatesClaim:
    """One record whose description has not been edited since a claim was recorded."""

    record_id: str
    claim_id: str
    description_at: str
    claim_at: str
    statement: str


def find_descriptions_predating_their_claims(
    records: list[RecordClaims],
) -> list[DescriptionPredatesClaim]:
    """KYO-137.c7. Store-only, and a prompt rather than a finding.

    The consumer's third gap, and the one they judged "may be the cheapest of the three
    to catch": a record's own description going stale against its own cited evidence
    claim, "both inside the store — the one case where kyosei holds both halves and
    could compare them without reading the repository".

    **What this deliberately does not claim** is that the description is wrong. A claim
    recorded after a description was last written may confirm it word for word, and the
    honest reading of this flag is "somebody should look" — which is why it is raised as
    a flag, never acted on and resolved by a human's judgement, rather than as a refusal
    or an edit. The class is one sentence away from being noise, and the two things
    holding it back are that bound and the superseded-claim filter its input applies.

    **One flag per record, not per claim.** A record with six claims newer than its
    description has one thing wrong with it, and six rows saying so would bury that one
    finding under repetitions of it. The claim named is the
    NEWEST, because that is the one whose date makes the case.
    """
    found: list[DescriptionPredatesClaim] = []
    for record in records:
        newer = [claim for claim in record.claims if claim[1] > record.description_at]
        if not newer:
            continue
        claim_id, claim_at, statement = max(newer, key=lambda claim: claim[1])
        found.append(
            DescriptionPredatesClaim(
                record_id=record.record_id,
                claim_id=claim_id,
                description_at=record.description_at,
                claim_at=claim_at,
                statement=statement,
            )
        )
    return found


def format_description_predates_claim_detail(stale: DescriptionPredatesClaim) -> str:
    """The flag detail for one :class:`DescriptionPredatesClaim` -- and the finding's
    identity (KYO-529).

    The description's timestamp and the newest claim's id and timestamp are exactly the
    condition: the detail changes when, and only when, the description is edited or a
    newer claim is filed. That is what lets :func:`judged_already` treat an equal detail
    on a RESOLVED flag as the same finding a human has already read.
    """
    return (
        f"the description was last edited {stale.description_at} and "
        f"{stale.claim_id} was recorded {stale.claim_at}, after it: "
        f"{stale.statement!r}. A newer claim is not proof the description is "
        f"wrong — read it and see"
    )


def judged_already(detail: str, resolved_details: set[str]) -> bool:
    """KYO-529: has a human already resolved this exact finding?

    ``description-predates-claim`` is re-derived whole from the store on every sweep,
    and before KYO-529 the sweep asked only whether an UNRESOLVED flag carried the
    finding. Resolving the flag therefore made the next sweep see nothing standing and
    file the identical finding again (a consumer report saw it twice on one
    record), so a resolution lasted until the next session stop. A resolution is a
    judgement of one finding -- this description against this newest claim -- and it
    holds until the condition moves, which for this class means the detail moves
    (:func:`format_description_predates_claim_detail`): a newer claim, or a description
    edit followed by a later claim, raises a fresh flag; an unchanged store does not.

    KYO-532 applies it to ``near-duplicate-criteria`` too, keyed on the pair's ids and
    texts (:func:`format_near_duplicate_detail`). Deliberately NOT applied to
    ``touch-missing``: ``tests/test_kyo8_sweep.py`` pins re-raising a still-missing touch
    after resolution as intended behaviour -- a missing file stays a real problem until
    the touches are corrected, where a judged "not duplicates" is a fact about the text.
    ``touch-changed`` does not share it: its finding is a commit window,
    and a sweep after the resolution diffs a new window.
    """
    return detail in resolved_details


@dataclass(frozen=True)
class NearDuplicateCriteriaMatch:
    """One near-duplicate pair, always ordered ``a`` before ``b`` by the
    caller's own record order (KYO-8.c3 attaches the flag to ``a``)."""

    record_a: str
    criterion_a: str
    text_a: str
    record_b: str
    criterion_b: str
    text_b: str
    ratio: float


def format_near_duplicate_detail(match: NearDuplicateCriteriaMatch) -> str:
    """The flag detail for one near-duplicate pair -- and the finding's identity (KYO-532).

    It names both criterion ids and both texts, so it changes when, and only when, either
    criterion is restated (a new id) or edited; :func:`judged_already` then treats an equal
    detail on a RESOLVED flag as the pair a human has already read.
    """
    return (
        f"{match.criterion_a} ({match.ratio:.2f} similar to {match.criterion_b} on "
        f"{match.record_b}): {match.text_a!r} vs {match.text_b!r}"
    )


def find_near_duplicate_criteria(
    criteria: list[CriterionText],
) -> list[NearDuplicateCriteriaMatch]:
    """Every near-duplicate criterion pair *across different records* in
    ``criteria``. Same-record pairs are skipped — a record's own restated
    criteria are visible on ``kyo show`` already and are not sweep's job
    (KYO-8.c3: "across open records"). Deterministic: iterates in the order
    given, so callers control tie-breaking by how they order ``criteria``
    (``cmd_records.py`` passes it record-seq order, so ``record_a`` is always the
    earlier-created record of the pair).
    """
    matches: list[NearDuplicateCriteriaMatch] = []
    for i, left in enumerate(criteria):
        for right in criteria[i + 1 :]:
            if left.record_id == right.record_id:
                continue
            is_dup, ratio = is_near_duplicate(left.text, right.text)
            if is_dup:
                matches.append(
                    NearDuplicateCriteriaMatch(
                        record_a=left.record_id,
                        criterion_a=left.criterion_id,
                        text_a=left.text,
                        record_b=right.record_id,
                        criterion_b=right.criterion_id,
                        text_b=right.text,
                        ratio=ratio,
                    )
                )
    return matches


@dataclass(frozen=True)
class DuplicateWarning:
    """One near-duplicate hit surfaced at creation time (KYO-8.c4) — never
    stored; ``kyo new``/``kyo promote`` print it and move on. The *next*
    `kyo sweep` is what turns the same overlap into a durable ``flags`` row via
    :func:`find_near_duplicate_criteria`, once the new record actually exists."""

    kind: str  # "title" | "criterion"
    new_text: str
    existing_record_id: str
    existing_text: str
    ratio: float


def find_creation_time_duplicates(
    *,
    new_title: str,
    new_criteria_texts: list[str],
    existing_titles: list[tuple[str, str]],
    existing_criteria: list[tuple[str, str, str]],
) -> list[DuplicateWarning]:
    """KYO-8.c4: compare a not-yet-created record's title and criteria against
    the existing corpus. ``existing_titles`` is ``(record_id, title)``;
    ``existing_criteria`` is ``(record_id, criterion_id, text)`` — kept as two
    flat lists rather than one record shape so ``cmd_new`` (no criteria
    argument required) and ``cmd_promote`` share exactly this one call.
    """
    warnings: list[DuplicateWarning] = []
    for record_id, title in existing_titles:
        is_dup, ratio = is_near_duplicate(new_title, title)
        if is_dup:
            warnings.append(
                DuplicateWarning(
                    kind="title",
                    new_text=new_title,
                    existing_record_id=record_id,
                    existing_text=title,
                    ratio=ratio,
                )
            )
    for new_text in new_criteria_texts:
        for record_id, _criterion_id, text in existing_criteria:
            is_dup, ratio = is_near_duplicate(new_text, text)
            if is_dup:
                warnings.append(
                    DuplicateWarning(
                        kind="criterion",
                        new_text=new_text,
                        existing_record_id=record_id,
                        existing_text=text,
                        ratio=ratio,
                    )
                )
    return warnings


# --------------------------------------------------------------------- touches


def changed_touch_paths(
    touch_paths: list[str],
    changed_files: set[str],
    *,
    created: set[str] | None = None,
) -> list[str]:
    """KYO-8.c1: the subset of a record's declared touch paths that changed in
    the git diff since the last sweep. Sorted for deterministic output.

    KYO-209: a directory touch counts as changed when anything *under* it changed.
    What comes back is still a subset of the DECLARED paths — the directory line
    itself, never the files beneath it — because the caller writes these into a
    ``touch-changed`` flag detail that a controller reads back against the record's
    touches table, and a detail naming paths that appear nowhere in that table reads
    as a tool error rather than as a finding.

    KYO-225: a new-file touch counts as changed when the file it names changed, EXCEPT
    when that change was the file's own creation — ``created`` is the window's added
    paths, and the whole of what this parameter is for is stated at the `if` below.
    """
    changed = set(changed_files)
    created = set(created or ())
    return sorted(
        declared
        for declared in touch_paths
        if declared in changed
        or (
            (is_directory_touch(declared) or is_new_file_touch(declared))
            and any(
                touch_covers(declared, path)
                for path in changed
                # KYO-225.c3: a new-file touch says "this work CREATES that file", so
                # the commit that creates it is the declaration coming true, not the
                # ground moving under the record. It is suppressed for that touch and
                # for nothing else — a later EDIT to the same file still flags, a
                # directory touch still counts a file created under it (that form
                # claims a tree, and a new file in it is news), and an ordinary file
                # touch is untouched. `created` empty (the caller could not ask git)
                # means nothing is suppressed, which is the behaviour before this
                # existed: a false flag is recoverable and a silent suppression is not.
                if not (is_new_file_touch(declared) and path in created)
            )
        )
    )


def missing_touch_paths(root: Path, touch_paths: list[str]) -> list[str]:
    """KYO-8.c2: the subset of a record's declared touch paths that no longer
    resolve to a real file under ``root``. Sorted for deterministic output.

    KYO-209: a directory touch is never missing. ``touch-missing`` means "the file you
    declared has been deleted or moved, so this record's premise may be gone"; a
    directory touch's whole meaning is that the tree may not exist yet, so applying
    that reading to it would flag every record filed for work that creates one — on
    every sweep, from the moment it was filed until the work landed, which is exactly
    the window in which the record is live and being read.
    """
    return sorted(
        path
        for path in touch_paths
        if not is_directory_touch(path)
        and not is_new_file_touch(path)
        and not (root / path).is_file()
    )


# ----------------------------------- KYO-300: what region of a file a symbol occupies
#
# `path#symbol` touches have existed since KYO-66 and until now only ONE reader was
# precise about them: the disjointness check (`commands._narrow_by_symbol`). Everything
# that asked "did this record's ground move" compared FILES, so a store where README.md
# is named by 64 records and cli.py by 64 re-flagged every one of them for any edit
# anywhere in those files. Measured on this repository's own store: 1932 touches,
# 73 of them naming a symbol, and 116 unresolved touch-changed flags.
#
# What is built here is deliberately textual and deliberately approximate. This tool
# sweeps Python, TypeScript, YAML, CSS and prose in the same run, so a language parser
# is out: it would answer well for one of those and refuse the rest, and a check that
# silently does nothing for four file types out of five is worse than one whose limits
# are written down. THE RULE IS STATED (`SYMBOL_REGION_RULE`) and it is tested.
#
# The inviolable property, and the reason every fallback below leans one way: anything
# the approximation CANNOT place counts as OVERLAPPING. A false "this change does not
# concern you" is a staleness flag that never fires — unrecoverable, and invisible. A
# false "this concerns you" is one line a controller reads and dismisses. So a symbol
# that is not found, a file that cannot be read, a diff that cannot be run and a phrase
# that wrapped across two lines all resolve to "flag it", never to "clean".

#: KYO-311: ``ADDITIVE_SYMBOL``, ``TOUCH_ADDITIVE_SYMBOL`` and ``TOUCH_ADDITIVE_BRIEF_NOTE``
#: live in ``contracts`` beside their siblings ``TOUCH_DIRECTORY_SYMBOL`` and
#: ``TOUCH_NEW_FILE_SYMBOL``. KYO-300 defined them here only because two other dispatches
#: of its wave had claimed that file; they are imported above and stay importable from
#: this module for the tests that name them.

#: The approximation c1 asks to have *stated*. Named once, quoted by the flag detail, by
#: `kyo touch-drift` and by docs/reference/topics/operating.md, so the rule a reader is
#: told and the rule the
#: code applies cannot drift apart (law 3).
SYMBOL_REGION_RULE = (
    "a symbol's region is found textually, not parsed: for a markdown heading it is the "
    "heading line through to the next heading at the same or a shallower depth; for "
    "anything else it is every line the symbol's name appears on, each widened to the "
    "unbroken run of non-blank lines around it and then taken through to the next "
    "non-blank line at the same or a shallower indentation that is not a bare closing "
    "bracket. A symbol this cannot place — not found, wrapped across two lines, in a "
    "file that cannot be read, or in a diff that cannot be run — counts as CHANGED, "
    "never as clean"
)


def is_additive_symbol(symbol: str | None) -> bool:
    """Is this stored ``touches.symbol`` the additive claim (KYO-300.c3)?"""
    return (symbol or "").strip() == ADDITIVE_SYMBOL


@dataclass(frozen=True)
class LineRange:
    """A 1-based inclusive span of lines."""

    start: int
    end: int

    def overlaps(self, other: LineRange) -> bool:
        return self.start <= other.end and other.start <= self.end


@dataclass(frozen=True)
class DiffHunk:
    """One ``@@`` header of a ``git diff --unified=0``, both sides kept.

    Both sides, because two different questions are asked of it. "Did the change touch
    this symbol's region" is about the NEW side (the regions are found in the file as it
    stands now). "Did this branch edit something that already existed" — the additive
    claim's guard, KYO-300.c4 — is about the OLD side, and only the old-side count can
    tell a pure insertion from a rewrite.
    """

    old_start: int
    old_count: int
    new_start: int
    new_count: int

    @property
    def new_range(self) -> LineRange:
        """The new-side lines this hunk wrote.

        A hunk with ``new_count == 0`` is a pure deletion: nothing in the new file is
        the change, so there is no line to point at. It is widened to the pair of lines
        the deletion happened between, which is the conservative reading — a symbol
        whose neighbouring lines were deleted has had its ground moved.
        """
        if self.new_count == 0:
            return LineRange(max(1, self.new_start), self.new_start + 1)
        return LineRange(self.new_start, self.new_start + self.new_count - 1)

    @property
    def removes_existing(self) -> bool:
        """Did this hunk change or delete lines that were already in the file?"""
        return self.old_count > 0


_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


def parse_diff_hunks(diff_text: str) -> list[DiffHunk]:
    """The ``@@`` headers of a unified diff, as data.

    Lives here rather than beside the ``git`` call for the reason every other function
    in this module does: it is pure, and a hunk parser that is only reachable through a
    subprocess is a parser nobody can write a test for.
    """
    hunks: list[DiffHunk] = []
    for line in diff_text.splitlines():
        match = _HUNK_RE.match(line)
        if match is None:
            continue
        groups = match.groupdict()
        hunks.append(
            DiffHunk(
                old_start=int(groups["old_start"]),
                old_count=1 if groups["old_count"] is None else int(groups["old_count"]),
                new_start=int(groups["new_start"]),
                new_count=1 if groups["new_count"] is None else int(groups["new_count"]),
            )
        )
    return hunks


_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.*?)\s*#*\s*$")

#: A line that is only punctuation a block closes with — ``}``, ``};``, ``)``, ``],``.
#: Skipped when looking for where a region ends, because in a brace language the closing
#: brace sits at the definition's own indentation and would end every region at its
#: first line.
_CLOSER_RE = re.compile(r"^[\s)\]};,]+$")

def _indent_width(line: str) -> int:
    expanded = line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip())


def _heading_depth(line: str) -> int | None:
    match = _HEADING_RE.match(line)
    return len(match.group("hashes")) if match else None


def _heading_section(lines: list[str], index: int) -> LineRange:
    depth = _heading_depth(lines[index]) or 6
    end = len(lines)
    for j in range(index + 1, len(lines)):
        other = _heading_depth(lines[j])
        if other is not None and other <= depth:
            end = j
            break
    return LineRange(index + 1, end)


def region_end(lines: Sequence[str], index: int) -> int:
    """Where the block anchored at 0-based ``index`` ends, as a 1-based inclusive line.

    The unbroken run of non-blank lines below the anchor, then on to the line before the
    next non-blank line at the same or a shallower indentation. Blank lines and lines
    that are only a block's closing punctuation are skipped when looking for that
    terminator: in a brace language the closing brace sits at the definition's own
    indentation and would end every region at its first line.

    Public and separate from :func:`_block_region` because a second caller wants this
    half and not the other one (KYO-352): ``docs_regions`` regions a ``governs:
    path#symbol`` target on a brace-language file by anchoring on the symbol's
    *declaration* rather than on every line that mentions it, and then asks exactly this
    question about where that declaration's block ends. One implementation of "where does
    a block end", two policies about where a block starts — which is the whole of the
    difference between the two finders, and what ``tests/test_docs_check.py`` pins.
    """
    indent = _indent_width(lines[index])
    after = index
    while after + 1 < len(lines) and lines[after + 1].strip():
        after += 1
    end = len(lines)
    for j in range(after + 1, len(lines)):
        line = lines[j]
        if not line.strip() or _CLOSER_RE.match(line):
            continue
        if _indent_width(line) <= indent:
            end = j
            break
    return max(end, after + 1)


def _block_region(lines: list[str], index: int) -> LineRange:
    """The anchor line's block: the unbroken run of non-blank lines around it, then on
    to the next non-blank line at the same or a shallower indentation.

    The run is the half that makes this usable on prose. Without it, a symbol found on
    one line of a hard-wrapped paragraph gets a one-line region — every line of the
    paragraph is at indentation zero, so the "next line at the same indentation" is the
    very next one — and an edit two lines further down the same sentence reads as clean.
    That is the silent direction. Widening to the run costs nothing in code, where a
    definition and its body are one unbroken run and the scan below still stops at the
    next definition, and it absorbs the decorator or comment sitting directly above a
    definition without a rule of its own.
    """
    start = index
    while start > 0 and lines[start - 1].strip():
        start -= 1
    return LineRange(start + 1, region_end(lines, index))


def symbol_regions(content: str, symbol: str) -> list[LineRange] | None:
    """Where in ``content`` the touch ``path#symbol`` claims, or ``None``.

    ``None`` means *this cannot be placed* and every caller reads it as "overlapping" —
    see the section comment above for why that direction is not negotiable.

    Two shapes, in this order:

    * **a markdown heading**, whose region is its section: the heading line through to
      the line before the next heading at the same or a shallower depth. Exact, and the
      reason the heading form is tried first — a heading's own words very often appear
      again in the prose beneath it, and unioning those in would drag most of a document
      into the region of one of its sections.
    * **anything else**, whose region is the union of :func:`_block_region` over EVERY
      line the symbol appears on. Every line, not the one that looks most like a
      definition: picking a definition would need a per-language notion of one, and
      guessing wrong there fails in the silent direction. A union is a superset, so the
      only thing it costs is a flag a controller dismisses. What a block is, and why it
      starts at the top of the anchor's paragraph rather than at the anchor, is in
      :func:`_block_region`.

    The match is per line and whole-word (``prose.contains_phrase``), the same relation
    ``_validate_touches`` accepts a symbol by — except that this one cannot follow a
    phrase that wrapped across two lines, and a touch validated that way therefore
    resolves to ``None`` here and stays flagged.
    """
    if not phrase_words(symbol):
        return None
    lines = content.splitlines()
    headings = [
        index
        for index, line in enumerate(lines)
        if (match := _HEADING_RE.match(line)) is not None
        and contains_phrase(match.group("text"), symbol, whole_word=True)
    ]
    if headings:
        return [_heading_section(lines, index) for index in headings]
    anchors = [
        index
        for index, line in enumerate(lines)
        if contains_phrase(line, symbol, whole_word=True)
    ]
    if not anchors:
        return None
    return [_block_region(lines, index) for index in anchors]


_NAME_RES = (
    re.compile(
        r"\b(?:def|class|function|interface|type|enum|struct|trait|impl|fn|func|"
        r"const|let|var)\s+([A-Za-z_$][\w$]*)"
    ),
    # A class member: the name at the start of the line, behind any run of the words a
    # brace language lets stand in front of one. KYO-358: without them only a BARE
    # `showLeft(): void {` was a declaration, and `private showLeft(`, `async load(`,
    # `static get foo(` were not -- which a TypeScript consumer measured as most of the
    # methods their documents describe. A closed list rather than "any word", because
    # `return showLeft(` and `await showLeft(` are calls and must stay calls.
    re.compile(
        r"^\s*(?:(?:export|default|declare|public|private|protected|static|abstract|"
        r"override|readonly|accessor|async|get|set)\s+)*\*?\s*"
        r"([A-Za-z_$][\w$]*)\s*(?:[?!]?:|[=(<])"
    ),
    re.compile(r"^\s*([.#][A-Za-z_][\w-]*)"),
)


def enclosing_symbol(content: str, line_no: int, *, markdown: bool) -> str | None:
    """The name of the thing that contains line ``line_no`` (1-based), or ``None``.

    :func:`symbol_regions` pointed the other way, and the reason it exists is KYO-300.c2:
    a drift finding that says "you edited a file you claim by symbol, outside the symbols
    you named" is only half a finding if it cannot say which symbol to add. It is the
    same textual approximation, so a suggestion it makes is a touch that
    :func:`symbol_regions` will then be able to place.

    ``None`` whenever nothing can be named, and the caller falls back to suggesting the
    plain path — which claims the whole file and is always safe.
    """
    lines = content.splitlines()
    if not 1 <= line_no <= len(lines):
        return None
    if markdown:
        for index in range(line_no - 1, -1, -1):
            match = _HEADING_RE.match(lines[index])
            if match is not None:
                return match.group("text").strip() or None
        return None
    for index in range(line_no - 1, -1, -1):
        line = lines[index]
        if not line.strip() or _CLOSER_RE.match(line) or _indent_width(line) > 0:
            continue
        for pattern in _NAME_RES:
            found = pattern.search(line)
            if found is not None:
                return found.group(1)
        return None
    return None


def narrow_changed_by_symbol(
    hit: Sequence[str],
    symbols: Mapping[str, Sequence[str | None]],
    *,
    regions_for: Callable[[str, str], list[LineRange] | None],
    hunks_for: Callable[[str], list[DiffHunk] | None],
) -> tuple[list[str], list[str]]:
    """KYO-300.c1: ``changed_touch_paths``' answer, narrowed by symbol where it can be.

    Takes the file-level answer and never recomputes it, exactly as
    ``commands._narrow_by_symbol`` takes ``shared_touch_paths``' — this is a refinement
    inside an established hit, so nothing here can make a path change that the file-level
    check did not already see change.

    Returns ``(entries, dropped)``. ``entries`` replaces the flag's path list: a plain,
    directory, new-file or additively-claimed path stays exactly the string it was
    (flagged exactly as today), while a path claimed only by ``#symbol`` becomes one
    ``path#symbol`` entry per symbol whose region the change actually reached — which is
    how the flag says *which* symbol's region changed. ``dropped`` is the paths that
    narrowed away entirely: every symbol claimed on them was placed and the change
    reached none of them.
    """
    entries: list[str] = []
    dropped: list[str] = []
    for declared in hit:
        named = list(symbols.get(declared) or [None])
        unplaceable = (
            is_directory_touch(declared)
            or is_new_file_touch(declared)
            or None in named
            or any(is_additive_symbol(symbol) for symbol in named)
        )
        if unplaceable:
            entries.append(declared)
            continue
        hunks = hunks_for(declared)
        if not hunks:
            # The diff could not be run, or said nothing about a file the file-level
            # check already reported as changed (a rename, a mode change, a binary
            # file). Either way the region question is unanswerable: flag the file.
            entries.append(declared)
            continue
        reached = []
        for symbol in named:
            assert symbol is not None  # `None in named` short-circuited above
            regions = regions_for(declared, symbol)
            if regions is None or any(
                region.overlaps(hunk.new_range) for region in regions for hunk in hunks
            ):
                reached.append(f"{declared}#{symbol}")
        if reached:
            entries += reached
        else:
            dropped.append(declared)
    return sorted(set(entries)), sorted(set(dropped))


def changed_outside_claimed_symbols(
    claimed: Sequence[str],
    hunks: Sequence[DiffHunk],
    *,
    content: str,
    markdown: bool,
    regions_for: Callable[[str], list[LineRange] | None],
) -> list[str] | None:
    """KYO-300.c2: the symbols to suggest adding, or ``None`` if the file was edited
    only inside the symbols it already claims.

    ``None`` is the clean answer here and an empty list is not: "edited outside, and
    nothing could be named for it" is still a finding, and the caller renders it with the
    plain path as the suggestion.
    """
    regions: list[LineRange] = []
    for symbol in claimed:
        found = regions_for(symbol)
        if found is None:
            # An unplaceable symbol claim cannot be shown to exclude anything, so it
            # excludes nothing and the file reads as fully claimed. Same direction as
            # everywhere else in this section: never a silent "you are clean".
            return None
        regions += found
    outside = [
        hunk
        for hunk in hunks
        if not any(region.overlaps(hunk.new_range) for region in regions)
    ]
    if not outside:
        return None
    suggested: list[str] = []
    for hunk in outside:
        name = enclosing_symbol(content, hunk.new_range.start, markdown=markdown)
        if name and name not in suggested and name not in claimed:
            suggested.append(name)
    return suggested


# ------------------------------------------------------- KYO-63: staleness coverage

#: KYO-63, law 6: whether a sweep run's *staleness* question — "which declared touches
#: changed since the last sweep" — was actually answered, and if not, why not. Named
#: once here so `kyo sweep`'s text, its ``--json``, `kyo brief` and the stored
#: ``sweep_runs.coverage`` column cannot drift apart (law 3). An amendment to the design
#: also asked the v2 board and the docs-mode banner to render it as a third state; neither
#: reads it (docs/design/review.md#kyo-63).
#:
#: The failure this vocabulary exists for: sweep records a base sha of the CODE
#: repository in the STORE repository, and under the split layout (KYO-54, the default
#: since) those are two repositories whose histories advance independently. Nothing
#: guarantees a later clone of the code contains that sha — shallow clone, fork,
#: unmerged branch, rewritten history — and a sweep that cannot diff creates no
#: touch-changed flags, which is byte-for-byte what a clean project looks like.

#: The diff ran from the recorded base to the current HEAD: staleness IS answered for
#: the window between them.
SWEEP_COVERAGE_SWEPT = "swept"
#: No earlier run recorded a base, so there was no window to diff. This run records one.
SWEEP_COVERAGE_BASELINE = "baseline"
#: No git repository (or no commits) at the code root — the pre-existing KYO-8 skip.
SWEEP_COVERAGE_NO_GIT = "no-git"
#: The recorded base sha is not a commit this code repository has.
SWEEP_COVERAGE_BASE_UNRESOLVABLE = "base-unresolvable"
#: This code repository's history is not the one the previous run swept.
SWEEP_COVERAGE_FOREIGN_HISTORY = "foreign-history"
#: The base resolved and the identity matched, but git still could not produce a diff.
SWEEP_COVERAGE_DIFF_FAILED = "diff-failed"
#: Not a run outcome: what `kyo brief` reports when the store holds no sweep run at all.
SWEEP_COVERAGE_NEVER_SWEPT = "never-swept"
#: Not a run outcome either: a row written before schema_version 10, which recorded no
#: coverage. Whether staleness was answered is not knowable from such a row, and this
#: value says exactly that rather than letting a NULL read as "fine" (see
#: ``schema.SWEEP_RUN_FIELD_DEFAULTS``).
SWEEP_COVERAGE_UNRECORDED = "unrecorded"

SWEEP_COVERAGE_OUTCOMES: tuple[str, ...] = (
    SWEEP_COVERAGE_SWEPT,
    SWEEP_COVERAGE_BASELINE,
    SWEEP_COVERAGE_NO_GIT,
    SWEEP_COVERAGE_BASE_UNRESOLVABLE,
    SWEEP_COVERAGE_FOREIGN_HISTORY,
    SWEEP_COVERAGE_DIFF_FAILED,
    SWEEP_COVERAGE_NEVER_SWEPT,
    SWEEP_COVERAGE_UNRECORDED,
)

#: One headline per outcome. Only :data:`SWEEP_COVERAGE_SWEPT` reads as answered; every
#: other value says "not answered" in its first three words, because the whole point is
#: that a reader skimming a brief must not be able to mistake one for the other.
COVERAGE_HEADLINES: dict[str, str] = {
    SWEEP_COVERAGE_SWEPT: "answered — the diff ran against the recorded base",
    SWEEP_COVERAGE_BASELINE: "not answered — first sweep with a base to record",
    SWEEP_COVERAGE_NO_GIT: "not answered — no git repository at the code root",
    SWEEP_COVERAGE_BASE_UNRESOLVABLE: (
        "NOT ANSWERED — the recorded base sha is not in this code repository"
    ),
    SWEEP_COVERAGE_FOREIGN_HISTORY: (
        "NOT ANSWERED — this is not the code repository the last sweep swept"
    ),
    SWEEP_COVERAGE_DIFF_FAILED: "NOT ANSWERED — git could not diff the recorded base",
    SWEEP_COVERAGE_NEVER_SWEPT: "not answered — this store has never been swept",
    SWEEP_COVERAGE_UNRECORDED: (
        "not answered — the last sweep predates this bookkeeping (schema_version < 10)"
    ),
}

#: What `kyo brief` says when there is no sweep run at all. A store nobody has swept has
#: the same empty flag list as a store with nothing stale.
NEVER_SWEPT_DETAIL = (
    "no `kyo sweep` has run in this store, so no record's touches have ever been "
    "checked for staleness"
)


def staleness_answered(coverage: str) -> bool:
    """Whether ``coverage`` means the staleness question was actually answered.

    Deliberately a whitelist of exactly one value: every future outcome added to this
    vocabulary is unanswered until someone decides otherwise, which is the safe
    direction for a check whose dangerous failure is an ABSENT flag, not a wrong one.
    """
    return coverage == SWEEP_COVERAGE_SWEPT


def coverage_sentence(coverage: str, detail: str | None = None) -> str:
    """The one sentence every surface says about a run's staleness coverage (law 3):
    `kyo sweep`'s output and `kyo brief`'s section are the same words, so a controller
    who has seen one recognises the other."""
    headline = COVERAGE_HEADLINES.get(coverage, f"unknown coverage {coverage!r}")
    return f"{headline}: {detail}" if detail else headline


@dataclass(frozen=True)
class StalenessBase:
    """The verdict on whether the previous run's recorded base may be diffed from.

    ``coverage`` :data:`SWEEP_COVERAGE_SWEPT` here means "the base is usable, go and
    diff" — the run only really *is* swept once the diff comes back (a diff that fails
    for some other reason lands on :data:`SWEEP_COVERAGE_DIFF_FAILED`).
    """

    coverage: str
    detail: str | None


#: Appended to every unanswered-and-recoverable verdict. It is the honest remedy and it
#: is also the whole reason "flag nothing" is defensible here: the gap is never scanned,
#: but the run says so, and the next run works. `tests/test_split_store.py` proves the
#: second sweep does return to `swept`, per this project's rule that a remedy named in a
#: message has a test that runs it.
REBASELINE_NOTE = (
    "this run re-baselines at {sha}, so the next sweep answers staleness from there — "
    "the window before it stays unswept and is not silently counted as clean"
)


def classify_staleness_base(
    *,
    current_sha: str | None,
    current_repo_id: str | None,
    prior_sha: str | None,
    prior_repo_id: str | None,
    base_resolves: bool,
) -> StalenessBase:
    """KYO-63.c1/c2: decide, from what the previous run recorded and what the code
    repository in front of us actually is, whether the touch-changed diff may run.

    ``base_resolves`` is :func:`kyosei.git_queries._git_commit_exists` for ``prior_sha``
    in the code repository; ``*_repo_id`` are
    :func:`kyosei.git_queries._git_history_identity` values, either of which may be
    ``None`` for "identity not established" — which is never read as a match and never
    as a mismatch (law 6). A ``None`` on the prior side is the ordinary case for a run
    recorded before schema_version 10, and it downgrades the answer to "only the sha
    could be checked" rather than inventing agreement.
    """
    if current_sha is None:
        return StalenessBase(
            SWEEP_COVERAGE_NO_GIT,
            "the code root is not a git repository (or has no commits), so no diff "
            "was possible and no touch-changed flag can be trusted to be absent",
        )
    if prior_sha is None:
        return StalenessBase(
            SWEEP_COVERAGE_BASELINE,
            f"no earlier sweep recorded a base sha; {current_sha[:12]} is recorded as "
            "the baseline the next sweep diffs from",
        )
    if (
        prior_repo_id is not None
        and current_repo_id is not None
        and prior_repo_id != current_repo_id
    ):
        return StalenessBase(
            SWEEP_COVERAGE_FOREIGN_HISTORY,
            f"the last sweep swept a code repository whose root commit(s) were "
            f"{_short_ids(prior_repo_id)}; this one's are {_short_ids(current_repo_id)}, "
            f"so its base sha {prior_sha[:12]} means nothing here even if it resolves. "
            + REBASELINE_NOTE.format(sha=current_sha[:12]),
        )
    if not base_resolves:
        return StalenessBase(
            SWEEP_COVERAGE_BASE_UNRESOLVABLE,
            f"the last sweep's base sha {prior_sha[:12]} is not a commit this code "
            "repository has — a shallow clone, an unmerged branch, a rewritten "
            "history or a different repository would all look like this. "
            + REBASELINE_NOTE.format(sha=current_sha[:12]),
        )
    if prior_repo_id is None or current_repo_id is None:
        return StalenessBase(
            SWEEP_COVERAGE_SWEPT,
            "the base sha resolves here, but the code repository's identity could not "
            "be compared (the earlier run recorded none, or this clone is shallow), so "
            "only the sha itself was checked",
        )
    return StalenessBase(SWEEP_COVERAGE_SWEPT, None)


def swept_detail(prior_sha: str, current_sha: str, *, caveat: str | None = None) -> str:
    """The detail stored on a run that did diff. Says the window it covered, so a later
    reader can see *what* was answered and not merely that something was."""
    window = f"diffed {prior_sha[:12]}..{current_sha[:12]}"
    return f"{window} ({caveat})" if caveat else window


def diff_failed_detail(prior_sha: str, current_sha: str) -> str:
    return (
        f"the base sha {prior_sha[:12]} resolves here, but `git diff` from it to "
        f"{current_sha[:12]} could not be run, so no touch-changed flag was computed. "
        + REBASELINE_NOTE.format(sha=current_sha[:12])
    )


def _short_ids(repo_id: str) -> str:
    return ",".join(sha[:12] for sha in repo_id.split(","))


# ------------------------------------------------- KYO-69: one open finding per record
#
# `kyo sweep` runs on every Stop hook, and before KYO-69 each run filed a NEW
# ``touch-changed`` flag for every open record whose declared touches intersected that
# run's diff. Nothing superseded the previous unresolved flag for the same record and
# the same condition, so the count grew with the number of SWEEPS rather than the number
# of problems: at one point 75 unresolved flags in this repository, every one of them
# ``touch-changed``, nine on a single record, each individually true and all of them
# saying the same thing — *the files this record declares have been changing while it
# sat open*.
#
# That is KYO-33's failure at scale ("a purge list with routine false positives is one
# people stop reading"), and it lands on `kyo brief`: the document a controller reads at
# the start of every session. An advisory surface that costs more to read than it returns
# gets skipped, and the one flag that mattered is skipped with it.
#
# The fix is law 4 applied to flags: supersede, never accumulate. A record's open
# ``touch-changed`` finding is ONE flag whose sha range widens and whose file set unions
# as later sweeps re-observe the same condition. The finding below is that flag's payload,
# and :func:`format_touch_changed_detail` / :func:`parse_touch_changed_detail` are the
# only two functions that know how it is written down — kept as an exact pair, with a
# round-trip test, so widening never has to guess at text some other code path wrote.
#
# Why the payload lives in ``flags.detail`` rather than in columns of its own: the record
# for this defect names widening the text as the natural operation ("the flag's text
# already carries a range"), and a new column is a schema bump plus export, rebuild,
# doctor and viewer changes, each of them a risk on a store that is being actively swept.

#: A sha as this module writes and reads one: the 12-character prefix
#: :func:`format_touch_changed_detail` emits, but tolerant of anything git-shaped so a
#: detail written by an older build (or by hand) still parses.
_SHA_RE = r"[0-9a-f]{4,40}"

#: What `kyo sweep` wrote before KYO-69: one flag per run, one run's window.
_TOUCH_CHANGED_LEGACY_RE = re.compile(
    rf"^changed since last sweep \((?P<from>{_SHA_RE})\.\.(?P<to>{_SHA_RE})\): (?P<paths>\S.*)$"
)

#: What it writes from KYO-69 on. The sweep count is part of the text because it is the
#: one thing a reader cannot recover from the range: "this has been true across nine
#: sweeps" is the difference between a record that drifted once and one that has been
#: drifting all day.
_TOUCH_CHANGED_RE = re.compile(
    rf"^changed over (?P<from>{_SHA_RE})\.\.(?P<to>{_SHA_RE}) in (?P<n>\d+) sweeps?: "
    rf"(?P<paths>\S.*)$"
)

#: How a path list is joined inside a detail, and therefore how it is split back out.
_PATH_SEP = ", "


@dataclass(frozen=True)
class TouchChangedFinding:
    """One record's open ``touch-changed`` finding: the window its declared touches
    changed over, which of them changed, and how many sweeps have observed it.

    ``paths`` is always sorted and deduplicated, so two findings built from the same
    facts render identically — which is what makes both supersession and the collapse
    migration idempotent (KYO-69.c3: a second run must be a no-op, not a second rewrite).
    """

    from_sha: str
    to_sha: str
    paths: tuple[str, ...]
    sweeps: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "paths", tuple(sorted(set(self.paths))))

    def widened(self, *, to_sha: str, paths: list[str]) -> TouchChangedFinding:
        """This finding re-observed by a later sweep: same start, new end, union of
        files, one more sweep. The start never moves — it is when the ground first
        moved under this record, and a reader who has not looked since then needs the
        whole window, not the most recent slice of it."""
        return TouchChangedFinding(
            from_sha=self.from_sha,
            to_sha=to_sha,
            paths=tuple(self.paths) + tuple(paths),
            sweeps=self.sweeps + 1,
        )


def format_touch_changed_detail(finding: TouchChangedFinding) -> str:
    """The one place a ``touch-changed`` detail string is written (law 3)."""
    sweeps = f"{finding.sweeps} sweep" + ("" if finding.sweeps == 1 else "s")
    return (
        f"changed over {finding.from_sha[:12]}..{finding.to_sha[:12]} in {sweeps}: "
        + _PATH_SEP.join(finding.paths)
    )


def parse_touch_changed_detail(detail: str) -> TouchChangedFinding | None:
    """The exact inverse of :func:`format_touch_changed_detail`, and also the reader for
    the pre-KYO-69 one-window-per-flag form the collapse migration has to consume.

    Returns ``None`` — never raises, never guesses — for anything it does not recognise.
    A caller that cannot parse an existing flag must not widen it: widening a string it
    did not understand would destroy a finding a human has not yet read, and the safe
    direction here is a second flag (visible, judgeable) rather than a silent rewrite.
    """
    match = _TOUCH_CHANGED_RE.match(detail) or _TOUCH_CHANGED_LEGACY_RE.match(detail)
    if match is None:
        return None
    paths = [p for p in match.group("paths").split(_PATH_SEP) if p.strip()]
    if not paths:
        return None
    groups = match.groupdict()
    return TouchChangedFinding(
        from_sha=groups["from"],
        to_sha=groups["to"],
        paths=tuple(paths),
        sweeps=int(groups["n"]) if groups.get("n") else 1,
    )


def merge_touch_changed_findings(
    findings: list[TouchChangedFinding],
) -> TouchChangedFinding:
    """The collapse migration's arithmetic (KYO-69.c3): several accumulated findings for
    one record, in the order they were filed, become the single finding they were always
    describing — earliest start, latest end, union of files, total sweeps.

    Note what this does *not* do: it never discards a path and never narrows the window,
    so the surviving flag says everything all of the originals said. That is the whole
    difference between collapsing and bulk-resolving, and it is why the duplicates can be
    closed without a human having read each one.
    """
    if not findings:
        raise ValueError("merge_touch_changed_findings needs at least one finding")
    paths: tuple[str, ...] = ()
    for finding in findings:
        paths += tuple(finding.paths)
    return TouchChangedFinding(
        from_sha=findings[0].from_sha,
        to_sha=findings[-1].to_sha,
        paths=paths,
        sweeps=sum(f.sweeps for f in findings),
    )


#: The machine-readable opening of the ``resolved_why`` the collapse migration writes on
#: every duplicate it closes. It exists so a reader (and a later query) can tell a flag
#: closed by merging from a flag a human judged and dismissed — the schema has one
#: resolution path and this build does not add a second, so the distinction has to be
#: legible in the reason. `kyo flag resolve` never writes this prefix.
SUPERSEDED_WHY_PREFIX = "superseded by "


def superseded_why(survivor_flag_id: str) -> str:
    return (
        f"{SUPERSEDED_WHY_PREFIX}{survivor_flag_id}: the same finding, re-filed by a "
        "later sweep. Its range and file set were merged into that flag by "
        "`kyo flag collapse`, so nothing it said was discarded — this row is closed as a "
        "duplicate, NOT judged and dismissed."
    )


def is_superseded(resolved_why: str | None) -> bool:
    return bool(resolved_why) and str(resolved_why).startswith(SUPERSEDED_WHY_PREFIX)


# ------------------------------------------------ KYO-69.c2: whose ground actually moved

#: The record has been dispatched (or reported on), so something was built against its
#: premises and "your declared touches changed" is a genuine staleness warning.
TOUCH_CHANGED_SCOPE_WORKED = "worked"
#: The record has no dispatch history at all. Nothing has been built against its premises,
#: so the same observation only says the codebase is being worked on — which is its normal
#: state. Still flagged (a touch list declared long ago can simply be wrong, and a sweep
#: that declines to look never gets that window back), but ranked below the above.
TOUCH_CHANGED_SCOPE_OPEN_ONLY = "open-only"

TOUCH_CHANGED_SCOPES: tuple[str, ...] = (
    TOUCH_CHANGED_SCOPE_WORKED,
    TOUCH_CHANGED_SCOPE_OPEN_ONLY,
)

#: One heading per scope, named once so `kyo brief` cannot describe the split in words
#: that drift from what the split actually is (law 3).
TOUCH_CHANGED_SCOPE_HEADINGS: dict[str, str] = {
    TOUCH_CHANGED_SCOPE_WORKED: (
        "staleness under work that was actually done (dispatched or reported)"
    ),
    TOUCH_CHANGED_SCOPE_OPEN_ONLY: (
        "drift under records that have merely sat open (never dispatched)"
    ),
}


def touch_changed_scope(*, has_dispatch_history: bool) -> str:
    return TOUCH_CHANGED_SCOPE_WORKED if has_dispatch_history else TOUCH_CHANGED_SCOPE_OPEN_ONLY


#: How many characters of a sha the grouping key and the line that prints it carry —
#: the same 12 :func:`format_touch_changed_detail` writes into the flag itself, so a
#: controller reading a group line and a controller reading a flag detail see one string.
RANGE_SHA_CHARS = 12


def touch_changed_range(flag: dict) -> str | None:
    """``from..to`` for a ``touch-changed`` flag, or ``None`` (KYO-222.c1/c2).

    Read out of the flag's OWN stored detail through
    :func:`parse_touch_changed_detail` — never by re-running git. The range is a fact
    the sweep that raised the flag already recorded, and re-deriving it would be a
    second answer to a question the store has already answered, computed from a
    different HEAD.

    ``None`` for every other flag class, and for a ``touch-changed`` detail this build
    cannot parse. Both mean "this flag has no group", never "this flag belongs with the
    other ungrouped ones": a key nothing could be read out of must not become a bucket
    (law 6).
    """
    if flag.get("flag_type") != FLAG_TYPE_TOUCH_CHANGED:
        return None
    finding = parse_touch_changed_detail(str(flag.get("detail") or ""))
    if finding is None:
        return None
    return (
        f"{finding.from_sha[:RANGE_SHA_CHARS]}..{finding.to_sha[:RANGE_SHA_CHARS]}"
    )


def group_flags_by_range(flags: list[dict]) -> list[dict]:
    """``flags`` grouped by the commit range that raised them (KYO-222.c1).

    ``flags`` is the brief's already-ranked list, and the order is preserved exactly:
    each group is placed where its FIRST member sat, so the worked/open-only ranking
    KYO-69.c2 established still decides what is read first — a group holding any worked
    member has that member first in the ranked list, and so the group lands with the
    worked flags rather than after them.

    The shape returned, one entry per group, in that order::

        {
          "range": str | None,      # `from..to`, short shas; None = no group
          "flags": [dict, ...],     # the members, in ranked order
          "record_ids": [str, ...], # distinct, first-seen order
          "worked": int,            # members whose record was dispatched or reported
          "open_only": int,         # members whose record has merely sat open
        }

    A flag with no range (:func:`touch_changed_range` returned ``None``) is its own
    group of one, which is how every non-touch-changed class stays exactly where and
    how it was. So is a range with only one flag: the grouping exists to say "these
    four are one event", and a group of one is a longer way of writing a flag.

    Nothing is dropped and nothing is merged that did not share a stored range: this is
    a rendering of the same flags, and `kyo flag resolve` still takes one flag id
    (c2) — every member is named by its own id in ``flags``.
    """
    slots: dict[str, int] = {}
    members: dict[str, list[dict]] = {}
    out: list[dict] = []
    for flag in flags:
        key = touch_changed_range(flag)
        if key is None:
            out.append(_flag_group(None, [flag]))
            continue
        if key not in slots:
            # The group takes the place of its first member, which is what preserves
            # the ranking: `rank_unresolved_flags` put the worked flags first, so a
            # group with a worked member is claimed by one.
            slots[key] = len(out)
            members[key] = []
            out.append({})
        members[key].append(flag)
    for key, slot in slots.items():
        out[slot] = _flag_group(key, members[key])
    return out


def _flag_group(key: str | None, members: list[dict]) -> dict:
    return {
        "range": key,
        "flags": members,
        "record_ids": list(dict.fromkeys(str(f["record_id"]) for f in members)),
        "worked": sum(
            1 for f in members if f.get("touch_changed_scope") == TOUCH_CHANGED_SCOPE_WORKED
        ),
        "open_only": sum(
            1 for f in members if f.get("touch_changed_scope") == TOUCH_CHANGED_SCOPE_OPEN_ONLY
        ),
    }


def rank_unresolved_flags(flags: list[dict]) -> list[dict]:
    """KYO-69.c2's ordering: worked-record staleness first, open-record drift last,
    everything else (other flag classes) in between and otherwise untouched.

    Stable, so within a group the store's own order survives. Ranking rather than
    suppressing is the decision this build made; the reasoning is on KYO-69's report.
    """
    order = {
        TOUCH_CHANGED_SCOPE_WORKED: 0,
        None: 1,
        TOUCH_CHANGED_SCOPE_OPEN_ONLY: 2,
    }
    return sorted(flags, key=lambda f: order.get(f.get("touch_changed_scope"), 1))


__all__ = [
    "ADDITIVE_SYMBOL",
    "COVERAGE_HEADLINES",
    "MIN_NORMALISED_LENGTH",
    "NEVER_SWEPT_DETAIL",
    "REBASELINE_NOTE",
    "SIMILARITY_THRESHOLD",
    "SWEEP_COVERAGE_BASELINE",
    "SWEEP_COVERAGE_BASE_UNRESOLVABLE",
    "SWEEP_COVERAGE_DIFF_FAILED",
    "SWEEP_COVERAGE_FOREIGN_HISTORY",
    "SWEEP_COVERAGE_NEVER_SWEPT",
    "SWEEP_COVERAGE_NO_GIT",
    "SWEEP_COVERAGE_OUTCOMES",
    "SWEEP_COVERAGE_SWEPT",
    "SWEEP_COVERAGE_UNRECORDED",
    "RANGE_SHA_CHARS",
    "SUPERSEDED_WHY_PREFIX",
    "SYMBOL_REGION_RULE",
    "TOUCH_ADDITIVE_BRIEF_NOTE",
    "TOUCH_ADDITIVE_SYMBOL",
    "TOUCH_CHANGED_SCOPES",
    "TOUCH_CHANGED_SCOPE_HEADINGS",
    "TOUCH_CHANGED_SCOPE_OPEN_ONLY",
    "TOUCH_CHANGED_SCOPE_WORKED",
    "CriterionText",
    "DiffHunk",
    "DuplicateWarning",
    "LineRange",
    "NearDuplicateCriteriaMatch",
    "StalenessBase",
    "TouchChangedFinding",
    "changed_outside_claimed_symbols",
    "changed_touch_paths",
    "group_flags_by_range",
    "classify_staleness_base",
    "coverage_sentence",
    "diff_failed_detail",
    "enclosing_symbol",
    "find_creation_time_duplicates",
    "find_near_duplicate_criteria",
    "format_description_predates_claim_detail",
    "format_near_duplicate_detail",
    "format_touch_changed_detail",
    "judged_already",
    "is_additive_symbol",
    "is_near_duplicate",
    "is_superseded",
    "merge_touch_changed_findings",
    "missing_touch_paths",
    "narrow_changed_by_symbol",
    "normalise_text",
    "parse_diff_hunks",
    "parse_touch_changed_detail",
    "symbol_regions",
    "rank_unresolved_flags",
    "similarity_ratio",
    "staleness_answered",
    "superseded_why",
    "swept_detail",
    "touch_changed_scope",
]
