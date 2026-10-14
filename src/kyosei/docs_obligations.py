"""Docs obligations: the governed sections a branch's changed files raise, and their
grouped rendering.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from . import explanations
from .contracts import (
    GUARDED_BRANCH_DIRS,
    OBLIGATION_NOT_AFFECTED,
    OBLIGATION_UPDATED,
    OBLIGATION_VERDICTS,
)
from .docs_parse import (
    DEFAULT,
    PREAMBLE,
    Document,
    DocumentSet,
    GovernsTarget,
    Section,
    _iter_docs_files,
    heading_anchor,
    read_document,
    split_target,
)
from .docs_recx import diff_sides, prefix_matches, widen_with_sides
from .docs_regions import (
    COMMENT_ONLY_SUFFIXES,
    FileChange,
    _located_both_sides,
    _region_verdict,
    branch_file_changes,
    comment_only_change,
    narrowable_targets,
    narrows_by_symbol,
    pointer_region,
    symbol_region_changed,
)
from .docs_staleness import STALENESS_STALE, _dated_paths
from .docs_targets import SHAPE_PATH, SHAPE_SYMBOL, documents, target_shape

# ------------------------------------------- KYO-137: obligations, raised at report time
#
# An obligation is raised at the worker's report, one per SECTION, and cleared only by a
# controller's asserted verdict, never a timestamp; nothing below decides that a document
# is fresh. What raises one is a JOIN: what a dispatch's branch changed, intersected with
# the paths a `governs:` section names. `doc-refs` (below, and on the record side) covers
# only the residual the join provably misses — a document about a record's subject that
# shares no file with it. The decisions behind this: docs/design/docs.md#kyo-137.


#: A `governs:`-bearing section speaks for a path this dispatch's branch changed.
OBLIGATION_GOVERNS = "governs"
#: The record itself declared this document under `doc-refs` — the back path, for a
#: document about the record's subject that names none of its files.
OBLIGATION_DOC_REFS = "doc-refs"
#: KYO-500: a `<doc path>#<anchor>` pointer into a set with `pointer_obligations` on sits
#: in a code region this dispatch's branch changed. The same relation
#: `governs:` states, derived from the code's side instead of written on the section's.
OBLIGATION_POINTER = "pointer"

#: KYO-554: a record whose kind sets ``policy.raises_docs_obligations`` (the default
#: ``decision``) names this section by its ssot id in its title or description. Raised
#: when the record is written, with no dispatch: a STANDING obligation.
OBLIGATION_DECISION = "decision"

OBLIGATION_CAUSES: tuple[str, ...] = (
    OBLIGATION_GOVERNS,
    OBLIGATION_DOC_REFS,
    OBLIGATION_POINTER,
    OBLIGATION_DECISION,
)

#: Path prefixes that never raise a docs obligation, in **either** direction: neither a
#: document living under one, nor a changed file under one, may produce a single
#: obligation (KYO-137.c6).
#:
#: Aliased from :data:`kyosei.contracts.GUARDED_BRANCH_DIRS` rather than spelled again,
#: the same move ``touch_drift._DRIFT_IGNORED_PREFIXES`` makes over the same tuple —
#: "the store and the intake layer" is one fact and this project spells it once (law 3).
#: The two halves it buys here are both wanted and only one of them is asked for:
#:
#: - ``ideas/`` is KYO-137.c6. An idea file is *supposed* to be a
#:   stale snapshot of a thought, so an obligation over one would be noise proportional
#:   to how well the layer is used (docs/design/docs.md#kyo-137).
#: - ``kyosei/`` is not in any criterion and is excluded for a duller reason: a worker
#:   cannot diff it at all (`kyo check-branch`), so an obligation naming it would name
#:   work no dispatch is permitted to do.
UNOBLIGATED_PREFIXES: tuple[str, ...] = GUARDED_BRANCH_DIRS


def _unobligated(path: str) -> bool:
    """Is ``path`` (root-relative, posix) under a prefix that never raises? (c6)"""
    return any(
        path == prefix.rstrip("/") or path.startswith(prefix) for prefix in UNOBLIGATED_PREFIXES
    )


def governed_symbol_paths(root: Path, *, documents_in: DocumentSet = DEFAULT) -> list[str]:
    """Every path some ``governs:`` target narrows with a ``#symbol``, deduplicated.

    What :func:`branch_file_changes` should be asked to read, and nothing else: a target
    with no symbol is answered at file level from the name alone, so fetching its two
    revisions would buy nothing. Derived from the documents rather than hand-listed, so a
    document that starts naming a symbol is covered without anyone remembering to.
    """
    return sorted(_governed_paths(root, documents_in)[1])


def _governed_paths(
    root: Path, documents_in: DocumentSet, manifest_sides: Sequence[str] = ()
) -> tuple[set[str], set[str], set[str]]:
    """Every path a ``governs:`` target resolves to, the subset a ``#symbol`` narrows, and
    every recx module path (a prefix target, KYO-600).

    One walk of the documents for all three, because
    :func:`obligations_and_suppressions_for_branch` needs them all: the narrowed paths for
    symbol precision, and every governed path whose language :func:`comment_only_change`
    can lex (KYO-530), since a whole-file target is quieted by a comment-only change as
    much as a narrowed one. A module path governs what it prefixes rather than what it
    resolved to, so it is returned as the prefix and the caller matches changed paths
    against it (KYO-606); ``manifest_sides`` adds the module paths the other sides of the
    diff declare, as the join itself does (:func:`_documents_with_sides`).
    """
    governed: set[str] = set()
    narrowed: set[str] = set()
    prefixes: set[str] = set()
    for document in _documents_with_sides(root, documents_in, manifest_sides):
        if _unobligated(document.path):
            continue
        for target in document.resolved:
            if target.prefix:
                prefixes.add(target.path or "")
                continue
            # The RESOLVED paths, root-relative, because that is the vocabulary
            # `branch_file_changes` speaks — git names a path from the repository root,
            # and handing it a base-relative one would fetch nothing and look like a
            # file that simply had not changed (KYO-200).
            governed.update(target.paths)
            if target.symbol:
                narrowed.update(target.paths)
    return governed, narrowed, prefixes


def _documents_with_sides(
    root: Path, documents_in: DocumentSet, manifest_sides: Sequence[str]
) -> list[Document]:
    """The project's documents, a ``recx-manifest`` one widened with the module paths
    ``manifest_sides`` declare (KYO-600, :func:`kyosei.docs_recx.widen_with_sides`)."""
    docs = documents(root, documents_in=documents_in)
    if not manifest_sides:
        return list(docs)
    return [
        widen_with_sides(document, manifest_sides, documents_in.recx_manifest)
        if documents_in.is_recx_manifest(document.path)
        else document
        for document in docs
    ]


def comment_only_paths(
    changed: Sequence[str], changes: Mapping[str, FileChange]
) -> frozenset[str]:
    """The changed paths whose diff moved no code token (KYO-530).

    A path with no ``changes`` entry is not among them: no diff evidence is cannot-tell,
    and cannot-tell raises (decision 9, docs/design/docs.md#kyo-530).
    """
    return frozenset(
        path
        for path in changed
        if path in changes and comment_only_change(changes[path])
    )


def staleness_raises_obligation(state: str) -> bool:
    """Whether a staleness ``state`` may raise a docs obligation (KYO-137.c8).

    A whitelist of exactly one value, for the reason :func:`staleness_answered` gives for
    its whitelist of two and :func:`docs_check_answered` for its own: every state added to
    the vocabulary is non-raising until someone decides otherwise, because the failure
    that costs here is a flag raised on no evidence rather than one withheld.

    The two states c8 names by name land on the right side of that whitelist for two
    different reasons, and the difference matters more than the shared answer:

    - :data:`STALENESS_UNGOVERNED` is a document that declares no ``governs:`` target at
      all. It cannot intersect a changed-file set — there is nothing to intersect — so a
      project that declared nothing is never flagged as stale. That is the state a
      consumer upgrading into this mechanism is in on day one, and flagging them would be
      this build asserting a defect it has not observed.
    - :data:`STALENESS_CANNOT_TELL` is git declining to answer. It is *not* evidence of
      currency and it is not evidence of staleness, and the honest thing to do with an
      undecided question at a blocking gate is not to block on it.

    **The obligation path does not consult :func:`governs_staleness` for its answer**, and
    that is deliberate rather than an oversight this function papers over. Decision 3 says
    a timestamp comparison must not settle the docs question; a gate that blocks a
    terminal move must not rest on one either. :func:`obligations_for_changed_files`
    determines staleness from the dispatch's own diff — a strictly better instrument than
    a pair of git dates, and one that returns :data:`STALENESS_STALE` or nothing at all —
    and then filters through this predicate, so both the good instrument and the weaker
    one are held to one rule and neither of c8's two states can reach a raise.
    """
    return state == STALENESS_STALE


@dataclass(frozen=True)
class DocsObligation:
    """One document section a dispatch's work puts a question to.

    Not an assertion that the section is wrong — nothing here can know that. It is the
    question raised at the moment someone can still answer it cheaply, and it is
    :attr:`says` that has to carry enough for the controller to answer it without
    re-deriving why it was asked.
    """

    #: The document, relative to the code root, posix-style.
    doc_path: str
    #: The heading whose section declared the relation — :data:`PREAMBLE` for a
    #: ``governs:`` line before the first heading. **Per section, not per file**
    #: (KYO-137.c3): a README with one stale paragraph is not a
    #: stale README, and one obligation per file makes the signal mush.
    section: str
    #: One of :data:`OBLIGATION_CAUSES` — which of the two directions raised this.
    cause: str
    #: The section's ``governs:`` targets that matched, in declared order. Empty for a
    #: ``doc-refs`` obligation, which names a document rather than a path in it.
    targets: tuple[str, ...]
    #: The dispatch's changed paths that matched, sorted.
    changed: tuple[str, ...]
    #: Why this was raised, in one line, carrying the evidence.
    says: str
    #: KYO-500.c2: a ``pointer`` obligation raised at FILE granularity, because no region
    #: finder could place one of its pointers (:func:`docs_regions.pointer_region`
    #: answered ``None`` or ``()``). Read by :func:`coarse_pointer_lines`, which is how
    #: ``kyo docs preview``'s suppression report says so. Never stored: ``says`` already
    #: carries the same fact into the obligation's ``detail``.
    coarse: bool = False
    #: KYO-530: a row that was NOT raised because every changed file it would have raised
    #: on changed only comments and docstrings. Only ever true on a suppressed row; read by
    #: :func:`suppression_lines` and by ``kyo docs preview``'s bucketing. Never stored.
    comment_only: bool = False


def _target_raises(
    target: str | GovernsTarget,
    changed: frozenset[str],
    changes: Mapping[str, FileChange],
) -> str | None:
    """The changed path ``target`` raises on, or ``None`` if it raises on nothing.

    The whole of KYO-153.c1 and c2 is here. A bare ``path`` target raises on any change
    to that file, exactly as it did before — precision is opt-in and no declaration
    written against the old behaviour silently weakens (c2). A ``path#symbol`` target
    additionally asks :func:`symbol_region_changed`, and declines to raise on the single
    answer that instrument is strong enough to give: ``False``, meaning the region was
    located on both sides of the diff and neither side's changed lines fall inside it.
    ``None`` (cannot tell) and a missing ``changes`` entry both raise (decision 9).

    KYO-200 adds the resolved currency and a plural: the paths come from
    :func:`_dated_paths`, so a ``path:symbol`` target is treated exactly as its ``#``
    twin, a base-relative path is asked about by its root-relative name, and a bare
    symbol governs every file it resolved to — raising on the first of them the dispatch
    changed. The symbol half is asked for once and applied to each, which is what makes
    a symbol target's precision the same precision a ``path#symbol`` target already had.
    """
    if isinstance(target, GovernsTarget):
        if target.prefix:
            # KYO-600: a recx module's path governs every changed path it prefixes,
            # added, edited or deleted alike, so it is matched as text and not through
            # the files it resolved to.
            return next(
                (path for path in sorted(changed) if prefix_matches(target.path or "", path)),
                None,
            )
        if target.shape == SHAPE_SYMBOL and target.symbol:
            return _symbol_target_raises(target, changed, changes)
        symbol_part = (target.symbol or "").strip()
    else:
        symbol_part = (split_target(target)[1] or "").strip()
    for path_part in _dated_paths(target):
        if path_part not in changed:
            continue
        if not symbol_part:
            return path_part
        change = changes.get(path_part)
        if change is None:
            # No diff evidence was supplied at all — every caller predating KYO-153, and
            # every caller whose git could not answer. The pre-KYO-153 file-level answer
            # is the conservative one, so this degrades to raising rather than to
            # suppressing.
            return path_part
        if symbol_region_changed(change, symbol_part) is not False:
            return path_part
    return None


def _symbol_target_raises(
    target: GovernsTarget,
    changed: frozenset[str],
    changes: Mapping[str, FileChange],
) -> str | None:
    """:func:`_target_raises` for a bare-symbol target (KYO-397): raise on a changed file
    that DEFINES the symbol, not on one that merely mentions it.

    A bare ``OrderSnapshot.total_cents`` resolves by text to every searchable file
    carrying its words, and a file that only *uses* the field gets ``()`` from the region
    finder, which is cannot-tell (docs/design/docs.md#kyo-397). Being unable to say where
    the symbol lives in a caller is not the question a governs: line asks; the question is
    whether the thing it names changed. So, per changed resolved
    file, in the order resolved:

    * **no diff evidence, or a file type with no region finder** (a ``.go`` or ``.sh``
      file the search reaches) — cannot tell, raises, exactly as before. The asymmetry
      decision 9 requires is kept: only a finder that ran can quiet anything.
    * **a side that will not parse or cannot be read** — cannot tell, raises.
    * **the finder locates a definition on either side** — this is a defining file, and
      it answers exactly as a ``path#symbol`` target on it would
      (:func:`symbol_region_changed`): raises when the definition's lines change, when
      the definition appears or disappears, and not when only other lines do.
    * **the finder ran on both sides and located nothing** — a mention. It raises
      nothing *provided some file defines the symbol*: either the working tree's
      :attr:`GovernsTarget.defined_in`, or a changed file in this same diff that located
      a definition on either side.

    That proviso is c2's fallback: a symbol no finder can place anywhere — imported from
    a library, declared in a language without a finder, spelled in a shape the finders
    do not read — keeps today's behaviour and raises on every resolved file, and
    ``kyo docs check`` says so by name (:func:`_undefined_symbol_notice`). Narrowing to
    the definition is a claim that the definition was found; with nothing found there is
    nothing to narrow to.

    **Why the "defined anywhere" half is read from the working tree and the per-file half
    from the diff.** Whether *this changed file* defines the symbol is asked of the diff's
    own before and after text, never of the tree: a diff that moves the definition from
    one file to another makes the tree's answer wrong for one of the two, whichever tree
    the join runs in (the worktree holds the after side, the controller's checkout the
    before side) — asked of both sides, both files raise, which is what a moved
    definition deserves. Whether the symbol is defined *somewhere else* can only be asked
    of files the diff did not touch, and for those the tree is the only copy there is and
    the two sides agree by definition. The union of the two is what makes a definition
    added or removed by this very diff count.
    """
    symbol = target.symbol or ""
    defined = bool(target.defined_in)
    mentions: list[str] = []
    for path in target.paths:
        if path not in changed:
            continue
        change = changes.get(path)
        if change is None or not narrows_by_symbol(path):
            return path
        located = _located_both_sides(change, symbol)
        if located is None:
            return path
        if not any(regions for regions, _ in located):
            mentions.append(path)
            continue
        defined = True
        if _region_verdict(located) is not False:
            return path
    if mentions and not defined:
        return mentions[0]
    return None


def _governs_obligations(
    document: Document,
    changed: frozenset[str],
    changes: Mapping[str, FileChange] = MappingProxyType({}),
) -> Iterator[DocsObligation]:
    for section in document.sections:
        # KYO-204: a dead section raises nothing, checked explicitly and before the
        # `resolved or governs` fallback below rather than relied on to fall out of it —
        # `section.resolved` is correctly empty for a dead section (never resolved at
        # all), but `section.resolved or section.governs` would silently fall back to
        # the dead section's raw, unfiltered target list, which is exactly the back door
        # KYO-204.c3 exists to close. `section_is_dead` is the one rule; this is the
        # earliest point in this function it can be asked, before any of its answers
        # would otherwise be used.
        if section.dead:
            continue
        # Resolved where the parse supplied it, written otherwise — the same pairing
        # `document_row` uses, and for the same reason: the mechanism reasons over
        # resolved paths and the obligation still QUOTES the target the document wrote,
        # because a controller answering it has to find that line in that file.
        targets: Sequence[str | GovernsTarget] = section.resolved or section.governs
        if not targets:
            # STALENESS_UNGOVERNED at the section level: nothing to intersect. c8's
            # first half is true here by construction, not by a check.
            continue
        matched_targets: list[str] = []
        matched_changed: set[str] = set()
        for target in targets:
            hit = _target_raises(target, changed, changes)
            if hit is not None:
                matched_targets.append(
                    target.raw if isinstance(target, GovernsTarget) else target
                )
                matched_changed.add(hit)
        if not matched_targets:
            continue
        hits = tuple(sorted(matched_changed))
        # The dispatch's own diff, as a staleness answer: this instrument observed the
        # governed file change on this branch, where `governs_staleness` can only compare
        # two git dates. It is put through the same predicate anyway (c8) so there is one
        # rule about what may raise, not two.
        state = STALENESS_STALE
        if not staleness_raises_obligation(state):  # pragma: no cover - see c8's test
            continue
        yield DocsObligation(
            doc_path=document.path,
            section=section.heading,
            cause=OBLIGATION_GOVERNS,
            targets=tuple(matched_targets),
            changed=hits,
            says=(
                f"{document.path} [{section.heading}] governs "
                f"{', '.join(matched_targets)}, and this dispatch changed "
                f"{', '.join(hits)}"
            ),
        )


def _doc_ref_obligations(
    document: Document, doc_refs: Sequence[tuple[str, str | None]]
) -> Iterator[DocsObligation]:
    for path, section_name in doc_refs:
        if path != document.path:
            continue
        # A bare `doc-refs` target names the whole file, and the whole file's obligation
        # is filed against its first heading (or the preamble) rather than against every
        # section it happens to have: c3 forbids one obligation per file, and it equally
        # forbids manufacturing twelve from a declaration that named one thing.
        heading = section_name or (document.title or PREAMBLE)
        yield DocsObligation(
            doc_path=document.path,
            section=heading,
            cause=OBLIGATION_DOC_REFS,
            targets=(),
            changed=(),
            says=(
                f"{document.path} [{heading}] is declared in this record's doc-refs, so "
                f"this dispatch's work was expected to affect it"
            ),
        )


# ------------------------------------------- KYO-554: standing obligations, raised by a record
#
# In an `ssot:` set the decision record is the history and the section is the current
# state, so filing a decision that names a section obliges someone to bring the section
# into line, or to say it needs no change. A record has no branch, so this is not a join
# against a diff: it reads the record's own words for ids (derived, law 3) and its
# explicit `doc-refs` for what words cannot say, against the working tree as it stands.


@dataclass(frozen=True)
class StandingObligations:
    """What a record's text and doc-refs raise, computed and not yet stored (KYO-554)."""

    #: One per section, ids first and then doc-refs; a section both name appears once,
    #: as the id's (``decision``), which says which id named it.
    found: tuple[DocsObligation, ...]
    #: Each bare id with a declared prefix that no heading owns, as one line apiece.
    #: Raises nothing (c4); the write's output names it.
    unresolved: tuple[str, ...]
    #: Each id or doc-ref that resolved and still raised nothing, with why (a document
    #: outside the document set, or under a path that never raises).
    skipped: tuple[str, ...]


def standing_obligations(
    root: Path,
    documents_in: DocumentSet,
    *,
    text: str,
    doc_refs: Sequence[tuple[str, str | None]] = (),
) -> StandingObligations:
    """The standing obligations a record whose words are ``text`` and whose doc-refs are
    ``doc_refs`` raises against the tree at ``root``.

    An id raises on the section owning it, resolved through
    :class:`kyosei.docs_ssot.SsotIndex` exactly as `kyo docs check` resolves one. A
    ``doc-refs`` entry raises on the section it names, or, for a bare path, on the
    document's first heading, exactly as :func:`_doc_ref_obligations` does for a dispatch
    -- and only for a path in the document set, as there.
    """
    from . import docs_ssot  # docs_ssot builds on docs_check, which is heavier to import

    found: dict[tuple[str, str], DocsObligation] = {}
    unresolved: list[str] = []
    skipped: list[str] = []
    index = docs_ssot.index_under(root, documents_in)
    for named in docs_ssot.ids_named_in(index, text) if index is not None else ():
        if named.owner is None:
            unresolved.append(
                f"{named.identifier}: no heading in any ssot document owns it (looked for "
                f"`{named.expected_heading}` in {named.home}), so it raised nothing"
            )
            continue
        doc_path, heading = named.owner.doc_path, named.owner.section.heading
        if _unobligated(doc_path):
            skipped.append(f"{named.identifier}: {doc_path} is under a path that never raises")
            continue
        found.setdefault(
            (doc_path, heading),
            DocsObligation(
                doc_path=doc_path,
                section=heading,
                cause=OBLIGATION_DECISION,
                targets=(),
                changed=(),
                says=(
                    f"{doc_path} [{heading}] owns {named.identifier}, which this record "
                    "names, so the section is expected to say what the record decided"
                ),
            ),
        )
    governed = {
        path.relative_to(root).as_posix() for path in _iter_docs_files(root, documents_in.globs)
    }
    for path, section_name in doc_refs:
        shown = f"{path}#{section_name}" if section_name else path
        if path not in governed:
            skipped.append(f"doc-ref {shown}: not in this project's document set")
            continue
        if _unobligated(path):
            skipped.append(f"doc-ref {shown}: under a path that never raises")
            continue
        try:
            body = (root / path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            skipped.append(f"doc-ref {shown}: could not be read ({exc.strerror or exc})")
            continue
        document = read_document(path, body, documents_in)
        for obligation in _doc_ref_obligations(document, [(path, section_name)]):
            found.setdefault(
                (obligation.doc_path, obligation.section),
                replace(
                    obligation,
                    says=(
                        f"{obligation.doc_path} [{obligation.section}] is declared in this "
                        "record's doc-refs, so the section is expected to say what the "
                        "record decided"
                    ),
                ),
            )
    return StandingObligations(tuple(found.values()), tuple(unresolved), tuple(skipped))


# ------------------------------------------- KYO-500: a code pointer raises on its section
#
# A branch that changes the
# code region a `<doc path>#<anchor>` pointer sits in raises an obligation on the section
# the pointer names. The relation is the one `governs:` states, read from the code's side,
# because the code already carries it (227 pointers when it was measured) and a pointer
# moves with its code where a `governs:` line would go stale (docs/design/docs.md#kyo-500).
#
# Decision 9 holds exactly as it does for a `#symbol`: the region may quiet a pointer only
# when it was located and none of that side's changed lines fall inside it. A pointer no
# finder can place raises at file granularity, and says so.


def pointer_sources(changed: Sequence[str]) -> list[str]:
    """The changed paths a pointer is read from: under ``src/``, the same tree A5 reads
    (:data:`kyosei.docs_check.POINTER_SOURCE_ROOT`), and never an unobligated one."""
    from .docs_check import POINTER_SOURCE_ROOT

    prefix = POINTER_SOURCE_ROOT.rstrip("/") + "/"
    return sorted(
        path for path in set(changed) if path.startswith(prefix) and not _unobligated(path)
    )


def pointer_obligations_on(documents_in: DocumentSet) -> bool:
    """Does any set in ``documents_in`` have ``section_status.pointer_obligations`` on?"""
    return any(chosen.pointer_obligations for chosen in documents_in.section_status)


def _pointable(
    root: Path, docs: Sequence[Document], documents_in: DocumentSet
) -> dict[str, dict[str, Section]]:
    """Each document a pointer may raise on, mapped anchor -> live section.

    Only documents a set with ``pointer_obligations: true`` enumerates (KYO-499's flag,
    which this record makes act), and only headed, live sections: a dead one is history,
    and ``governs:`` skips it for the same reason (KYO-204).
    """
    from .docs_check import opted_documents

    sets = [chosen for chosen in documents_in.section_status if chosen.pointer_obligations]
    if not sets:
        return {}
    opted = opted_documents(root, sets)
    found: dict[str, dict[str, Section]] = {}
    for document in docs:
        if document.path not in opted or _unobligated(document.path):
            continue
        anchors: dict[str, Section] = {}
        for section in document.sections:
            if section.level and not section.dead:
                anchors.setdefault(heading_anchor(section.heading), section)
        found[document.path] = anchors
    return found


@dataclass(frozen=True)
class _PointerSite:
    """One pointer occurrence on one side of one changed file."""

    doc_path: str
    heading: str
    pointer: str
    path: str
    line: int
    side: str  # "before", "after", or "tree" (no diff evidence: the working tree's copy)
    region: tuple[int, int, str] | None  # None: no finder could place it
    region_text: str = ""
    hit: bool = False

    def where(self) -> str:
        when = " before this branch" if self.side == "before" else ""
        label = self.region[2] if self.region else "no region"
        return f"{self.path}:{self.line}{when} ({label})"


def _pointer_join(
    root: Path,
    docs: Sequence[Document],
    live: frozenset[str],
    changes: Mapping[str, FileChange],
    documents_in: DocumentSet,
) -> tuple[dict[str, list[DocsObligation]], list[DocsObligation]]:
    """What the code pointers in ``live``'s changed source files raise, per document, and
    what their regions quieted (KYO-500.c1, c2).

    Both sides of each changed file are read, because a pointer whose region this branch
    deleted is on the before side only and one it added is on the after side only. A
    region whose text is identical on the other side for the same pointer did not change
    — it moved, or shifted down because lines were added above it — and raises nothing:
    that is positive evidence, the only kind decision 9 lets quiet a raise.

    With no diff evidence for a file (no ``changes`` entry: a caller that read no diff, or
    a git that could not answer) the working tree's copy is read for its pointers and
    every one raises at file granularity — the conservative answer, as for ``governs:``.
    """
    pointable = _pointable(root, docs, documents_in)
    if not pointable:
        return {}, []
    pointer_re = re.compile(
        rf"(?<![\w/.-])({'|'.join(re.escape(p) for p in sorted(pointable, key=len, reverse=True))})"
        r"#([A-Za-z0-9][\w-]*)"
    )
    sites: list[_PointerSite] = []
    for path in pointer_sources(sorted(live)):
        change = changes.get(path)
        if change is not None:
            sides = [
                ("before", change.before_text, change.before_lines),
                ("after", change.after_text, change.after_lines),
            ]
        else:
            try:
                text = (root / path).read_text(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                continue
            sides = [("tree", text, None)]
        per_side: dict[str, list[_PointerSite]] = {}
        for side, text, lines in sides:
            if text is None or "\0" in text[:8192]:
                continue
            split = text.splitlines()
            found: list[_PointerSite] = []
            for number, line in enumerate(split, 1):
                for match in pointer_re.finditer(line):
                    doc_path, anchor = match.group(1), match.group(2).lower()
                    section = pointable[doc_path].get(anchor)
                    if section is None:
                        # A pointer naming no live heading is `kyo docs check`'s to
                        # refuse (A2/A5), not a question this join can put to anyone.
                        continue
                    region = pointer_region(path, text, number) if lines is not None else None
                    if not region:
                        found.append(
                            _PointerSite(
                                doc_path, section.heading, f"{doc_path}#{anchor}", path,
                                number, side, None, hit=True,
                            )
                        )
                        continue
                    start, end, _ = region
                    found.append(
                        _PointerSite(
                            doc_path, section.heading, f"{doc_path}#{anchor}", path,
                            number, side, region,
                            # Blank lines at either end are not the region's text: a
                            # function inserted above a commented statement adds one, and
                            # the statement and its comment did not change.
                            region_text="\n".join(split[start - 1 : end]).strip("\n"),
                            hit=any(start <= changed <= end for changed in lines or ()),
                        )
                    )
            per_side[side] = found
        for side, found in per_side.items():
            other = "after" if side == "before" else "before"
            unchanged_elsewhere = {
                (site.pointer, site.region_text)
                for site in per_side.get(other, ())
                if site.region is not None
            }
            for site in found:
                if (
                    site.hit
                    and site.region is not None
                    and (site.pointer, site.region_text) in unchanged_elsewhere
                ):
                    site = _PointerSite(**{**site.__dict__, "hit": False})
                sites.append(site)

    by_section: dict[tuple[str, str], list[_PointerSite]] = {}
    for site in sites:
        by_section.setdefault((site.doc_path, site.heading), []).append(site)
    raised: dict[str, list[DocsObligation]] = {}
    suppressed: list[DocsObligation] = []
    for (doc_path, heading), members in by_section.items():
        hits = [site for site in members if site.hit]
        paths = tuple(sorted({site.path for site in (hits or members)}))
        if not hits:
            quiet = list(dict.fromkeys(site.where() for site in members))
            suppressed.append(
                DocsObligation(
                    doc_path=doc_path,
                    section=heading,
                    cause=OBLIGATION_POINTER,
                    targets=(),
                    changed=paths,
                    says=(
                        f"pointer(s) at {', '.join(quiet)}: this branch changed "
                        f"{', '.join(paths)} outside the pointer's region"
                    ),
                )
            )
            continue
        # One site per pointer and place: a region changed on both sides is one change,
        # named by its after-side line, which is the line a reader can open today.
        named: dict[tuple[str, str, str], _PointerSite] = {}
        for site in sorted(hits, key=lambda s: s.side != "after"):
            label = site.region[2] if site.region else ""
            named.setdefault((site.path, site.pointer, label), site)
        regioned = [site for site in named.values() if site.region is not None]
        coarse = [site for site in named.values() if site.region is None]
        parts: list[str] = []
        if regioned:
            parts.append(
                f"{'; '.join(f'{s.where()} points at {s.pointer}' for s in regioned)}, "
                "and this dispatch changed that region"
            )
        if coarse:
            parts.append(
                f"{'; '.join(f'{s.path}:{s.line} points at {s.pointer}' for s in coarse)}, "
                "and no region finder can place a pointer there "
                f"({', '.join(sorted({s.path for s in coarse}))}), so it raised at file "
                "granularity on this dispatch's change to the file"
            )
        raised.setdefault(doc_path, []).append(
            DocsObligation(
                doc_path=doc_path,
                section=heading,
                cause=OBLIGATION_POINTER,
                targets=(),
                changed=tuple(sorted({site.path for site in named.values()})),
                says=f"{doc_path} [{heading}] is cited by a code pointer: " + "; ".join(parts),
                coarse=bool(coarse),
            )
        )
    return raised, suppressed


def obligations_for_changed_files(
    root: Path,
    changed: Sequence[str],
    *,
    documents_in: DocumentSet = DEFAULT,
    doc_refs: Sequence[tuple[str, str | None]] = (),
    changes: Mapping[str, FileChange] = MappingProxyType({}),
) -> list[DocsObligation]:
    """Every docs obligation a dispatch that changed ``changed`` raises (c2, c3, c6, c8).

    ``changed`` is the branch's changed files, root-relative and posix-style — what
    ``kyosei.git_queries.branch_changed_files`` returns and what ``kyo report`` already
    computes for touch drift. ``doc_refs`` is the record's own back-path declaration,
    as ``(path, section or None)`` pairs.

    ``changes`` (KYO-153) is the same branch's diff, keyed by the same paths, and it is
    what lets a ``governs: path#symbol`` target mean what it says: without it every
    target is answered at file level, exactly as before. Optional rather than required
    on purpose — an absent or incomplete map degrades to the old, more-raising answer
    rather than to a quieter one (see :func:`_target_raises`).

    **Two sections governing one file raise two obligations, deliberately (KYO-153.c4).**
    Deduplicating per governed file is refused, whatever it would save
    (docs/design/docs.md#kyo-153), for the reason decision 9 gives about vetoes: collapsing
    two sections to one would have to pick a winner, and the loser's staleness becomes a
    silent false negative in a store that has no other way to
    notice it. The two documents genuinely go stale independently; one editorial act
    answering both is a cost on the SETTLE path, where one verdict discharging a set is
    cheap and visible, not a reason for the join to stop seeing one of them. The
    section was chosen as the unit, and this keeps that choice whole.

    Two directions, one function, because the controller reading the result cares about
    the list and not about which half produced each row — and because a document named by
    BOTH must appear once. The join wins that tie: it carries the evidence (which target,
    which changed file), and a ``doc-refs`` row for the same section would say strictly
    less about the same question.

    Documents are enumerated through :func:`documents`, hence through
    :func:`document_set` and :func:`_iter_docs_files` like everything else in this module
    — KYO-129.c4's one-enumerator property, which this record inherits rather than
    re-decides. A ``governs:`` line inside a fenced code block is quoted rather than
    declared (:func:`_fenced`) and raises nothing, which matters here more than it did to
    the validator: root-level ``*.md`` is a document by default, README files explain
    formats by example, and this repository's own README quotes a ``governs:`` line.

    **KYO-500**: a third direction, ``pointer`` — a ``<doc path>#<anchor>`` pointer in a
    changed ``src/`` file, inside a region this branch changed, into a set whose
    ``section_status.pointer_obligations`` is on (:func:`_pointer_join`). It ranks between
    the two ties above: below ``governs`` (a declared relation says as much and was
    written on purpose) and above ``doc-refs`` (which says strictly less).
    """
    return _obligations_and_pointer_suppressions(
        root, changed, documents_in=documents_in, doc_refs=doc_refs, changes=changes
    )[0]


def _obligations_and_pointer_suppressions(
    root: Path,
    changed: Sequence[str],
    *,
    documents_in: DocumentSet = DEFAULT,
    doc_refs: Sequence[tuple[str, str | None]] = (),
    changes: Mapping[str, FileChange] = MappingProxyType({}),
    pointers: bool = True,
    comments: bool = True,
    manifest_sides: Sequence[str] = (),
) -> tuple[list[DocsObligation], list[DocsObligation]]:
    """:func:`obligations_for_changed_files`, and the rows a region or a comment-only
    change quieted.

    ``pointers=False`` leaves the pointer join out: the file-level pass
    :func:`obligations_and_suppressions_for_branch` differences against is a question about
    ``governs:`` targets only, and pointer suppressions are computed in the region pass
    itself, from both sides of the diff.

    KYO-530: a changed file whose diff moved no code token (:func:`comment_only_paths`)
    raises nothing, through ``governs:`` or a pointer. ``comments=False`` is the join
    without that rule; when the rule quieted anything, the join is run that way too and
    every section it raised that this run did not comes back in the second list marked
    :attr:`DocsObligation.comment_only` — computed, not predicted, like KYO-352's list.

    KYO-600: ``manifest_sides`` is RECX.yaml's text on the other sides of the diff
    (:func:`kyosei.docs_recx.diff_sides`). A ``recx-manifest`` document's module paths are
    the union of the tree's and theirs (:func:`kyosei.docs_recx.widen_with_sides`).
    """
    quiet = comment_only_paths(sorted(changed), changes) if comments else frozenset()
    if quiet:
        raised, _ = _obligations_and_pointer_suppressions(
            root,
            [path for path in changed if path not in quiet],
            documents_in=documents_in,
            doc_refs=doc_refs,
            changes=changes,
            pointers=pointers,
            comments=False,
            manifest_sides=manifest_sides,
        )
        unruled, unruled_quieted = _obligations_and_pointer_suppressions(
            root,
            changed,
            documents_in=documents_in,
            doc_refs=doc_refs,
            changes=changes,
            pointers=pointers,
            comments=False,
            manifest_sides=manifest_sides,
        )
        raised_keys = {(o.doc_path, o.section) for o in raised}
        by_comment = [
            replace(
                o,
                comment_only=True,
                says=(
                    f"this branch changed only comments and docstrings in "
                    f"{', '.join(sorted(set(o.changed) & quiet) or sorted(quiet))} "
                    f"(no code token moved), so this was not raised; it would have said: "
                    f"{o.says}"
                ),
            )
            for o in unruled
            if (o.doc_path, o.section) not in raised_keys
        ]
        comment_keys = {(o.doc_path, o.section) for o in by_comment}
        return raised, [
            *by_comment,
            *(
                o
                for o in unruled_quieted
                if (o.doc_path, o.section) not in raised_keys | comment_keys
            ),
        ]
    live = frozenset(path for path in changed if not _unobligated(path))
    refs = [(path, section) for path, section in doc_refs if not _unobligated(path)]
    found: list[DocsObligation] = []
    seen: set[tuple[str, str]] = set()
    docs = _documents_with_sides(root, documents_in, manifest_sides)
    by_pointer, pointer_suppressed = (
        _pointer_join(root, docs, live, changes, documents_in) if pointers else ({}, [])
    )
    for document in docs:
        if _unobligated(document.path):
            # c6, the other direction: a document that lives under `ideas/` raises
            # nothing even when a dispatch changed exactly what it governs.
            continue
        raised = [
            *_governs_obligations(document, live, changes),
            *by_pointer.get(document.path, ()),
            *_doc_ref_obligations(document, refs),
        ]
        for obligation in raised:
            key = (obligation.doc_path, obligation.section)
            if key in seen:
                continue
            seen.add(key)
            found.append(obligation)
    # A `doc-refs` target the enumerator never reached still raises. The declaration is a
    # human's statement that this dispatch affects that document, and it was validated
    # against the repository when it was written (`kyo edit --doc-refs-file`) — so the
    # file exists and only `documents.yml` disagrees that it is a document. Dropping it
    # here would make the back path silently conditional on a glob the person writing the
    # declaration never saw, which is KYO-129's own failure wearing a different hat.
    for path, section_name in refs:
        heading = section_name or PREAMBLE
        if (path, heading) in seen or any(o.doc_path == path for o in found):
            continue
        seen.add((path, heading))
        found.append(
            DocsObligation(
                doc_path=path,
                section=heading,
                cause=OBLIGATION_DOC_REFS,
                targets=(),
                changed=(),
                says=(
                    f"{path} [{heading}] is declared in this record's doc-refs. It is "
                    f"not in this project's document set ({documents_in.says}), so only "
                    f"the declaration relates it to this work"
                ),
            )
        )
    raised_keys = {(o.doc_path, o.section) for o in found}
    return found, [o for o in pointer_suppressed if (o.doc_path, o.section) not in raised_keys]


def obligations_for_branch(
    root: Path,
    against: str,
    branch: str,
    changed: Sequence[str],
    *,
    documents_in: DocumentSet = DEFAULT,
    doc_refs: Sequence[tuple[str, str | None]] = (),
) -> list[DocsObligation]:
    """:func:`obligations_for_changed_files`, with the symbol-precision diff read for you.

    KYO-199.c6. The two halves of the join — which paths changed, and *where inside* the
    narrowed ones — were assembled in ``dispatch_docs._docs_obligations_for_dispatch`` and
    nowhere else, so the only way to ask "what would this branch raise?" was to have a
    stored dispatch to ask it about. A worker has a branch and no dispatch row it may
    read, which is exactly the reader ``kyo docs preview`` serves, and a second assembly
    of the same two halves would be a second answer that could disagree with the one
    ``kyo report`` gives (law 3).

    Only paths some ``governs:`` target actually narrows with a symbol are diffed
    (:func:`governed_symbol_paths`), which is what keeps this to a couple of ``git show``
    calls; a branch git cannot diff yields an empty map, and an empty map raises
    everything the file-level join would have raised rather than quietly less.
    """
    raised, _ = obligations_and_suppressions_for_branch(
        root, against, branch, changed, documents_in=documents_in, doc_refs=doc_refs
    )
    return raised


def obligations_and_suppressions_for_branch(
    root: Path,
    against: str,
    branch: str,
    changed: Sequence[str],
    *,
    documents_in: DocumentSet = DEFAULT,
    doc_refs: Sequence[tuple[str, str | None]] = (),
) -> tuple[list[DocsObligation], list[DocsObligation]]:
    """What this branch raises, and what a declared ``#symbol`` stopped it raising.

    KYO-352.c4 (docs/design/docs.md#kyo-352). A project narrowing many targets at once has to be
    able to SEE what stopped raising, in the same run, or the first evidence of a wrong
    narrowing is a stale document nobody noticed.

    The second list is the same join run twice over one diff read: once with the symbol
    evidence and once without it — an empty ``changes`` map is the file-level answer, by
    :func:`_target_raises`'s own documented degradation — differenced by (document,
    section). A "suppressed" row is therefore exactly what the pre-KYO-153 behaviour
    would have raised and this one did not, computed rather than predicted.

    **Per section, not per target**, because the obligation is per section: a section
    with two matching targets, one narrowed away and one still raising, has not been
    suppressed — the question it asks is still being asked, and listing it would
    manufacture an alarm about a section the controller is already reading.
    """
    changed_set = set(changed)
    # KYO-600: a recx manifest's module paths are read from both sides of the diff.
    sides = diff_sides(root, against, branch) if documents_in.recx_manifest else []
    governed, symbol_paths, module_paths = _governed_paths(root, documents_in, sides)
    narrowed = [path for path in sorted(symbol_paths) if path in changed_set]
    # KYO-500: a pointer's region is read from the same diff, so every changed source
    # file a pointer could sit in is read too — when some set has pointer obligations on.
    pointed = pointer_sources(list(changed)) if pointer_obligations_on(documents_in) else []
    # KYO-530: every changed governed file a lexer can read, so a comment-only change to a
    # whole-file target is seen as one. A pointer source is already in `pointed`. KYO-606:
    # a changed file under a recx module's path is governed by it, so it is read too and
    # the same rule quiets it; there is no second rule for module targets.
    lexable = [
        path
        for path in changed_set
        if (
            path in governed
            or any(prefix_matches(prefix, path) for prefix in module_paths)
        )
        and not _unobligated(path)
        and Path(path).suffix.lower() in COMMENT_ONLY_SUFFIXES
    ]
    wanted = sorted({*narrowed, *pointed, *lexable})
    changes = branch_file_changes(root, against, branch, wanted) if wanted else {}
    raised, pointer_quieted = _obligations_and_pointer_suppressions(
        root,
        list(changed),
        documents_in=documents_in,
        doc_refs=doc_refs,
        changes=changes,
        manifest_sides=sides,
    )
    if not changes or not narrowed:
        # Nothing was read at symbol precision, so no `governs:` row could have been
        # suppressed — and the file-level pass below would be a second walk of every
        # document for an answer already known to be empty.
        return raised, pointer_quieted
    file_level, _ = _obligations_and_pointer_suppressions(
        root,
        list(changed),
        documents_in=documents_in,
        doc_refs=doc_refs,
        pointers=False,
        manifest_sides=sides,
    )
    # A section the comment-only rule quieted (KYO-530) is already in `pointer_quieted`
    # under its own reason, and is not listed a second time as narrowed away.
    still_raised = {(o.doc_path, o.section) for o in raised} | {
        (o.doc_path, o.section) for o in pointer_quieted if o.comment_only
    }
    return raised, [
        *(o for o in file_level if (o.doc_path, o.section) not in still_raised),
        *pointer_quieted,
    ]


def suppression_lines(suppressed: Sequence[DocsObligation]) -> list[str]:
    """How a suppressed obligation prints (KYO-352.c4), one line per section.

    Its own rendering rather than :func:`grouped_obligation_lines`: these rows are not
    obligations and must not read like them. Nothing was raised, there is no id, there is
    nothing to settle, and the only act the list invites is a reader checking that the
    section really is about the symbol its ``governs:`` line now names.
    """
    return [
        f"    {row.doc_path} [{row.section}] — {row.says}"
        if row.cause == OBLIGATION_POINTER or row.comment_only
        else f"    {row.doc_path} [{row.section}] — {', '.join(row.targets)} narrowed this "
        f"away: this branch changed {', '.join(row.changed)} outside the symbol's own "
        f"lines"
        for row in suppressed
    ]


def coarse_pointer_lines(raised: Sequence[DocsObligation]) -> list[str]:
    """The ``pointer`` obligations raised at file granularity, one line each (KYO-500.c2).

    The other half of the suppression report: a region quiets a pointer, and a pointer no
    finder could place is NOT quieted — it raised on the whole file. Said beside the
    suppressions because both answer the same audit question, "how precise was this?",
    and a reader who sees only the quiet half would assume every pointer was placed.
    """
    return [
        f"    {row.doc_path} [{row.section}] — raised at file granularity: {row.says}"
        for row in raised
        if row.coarse
    ]


# ------------------- KYO-199: one grouped rendering, read by three surfaces
#
# The stored unit stays one obligation per section (two sections that go stale
# independently must be answerable independently); the *rendering* is grouped per
# document, with one ready-to-run settle line per document rather than one per row,
# because that is how a list of obligations is actually read (docs/design/docs.md#kyo-199).
#
# Three surfaces, one function: `kyo report` (obligations it just raised), `kyo docs
# obligations` (what is open in the store) and `kyo docs preview` (what a worktree's diff
# WOULD raise, with no store and therefore no ids at all). Three renderings could disagree
# about the grouping unit, and the preview exists precisely so a worker sees what its
# controller will see.


def whole_file_targets(targets: Sequence[object]) -> tuple[str, ...]:
    """Which of these written ``governs:`` targets name a whole file (KYO-199.c2).

    A target is whole-file when :func:`target_shape` calls it :data:`SHAPE_PATH` — a path
    with no symbol half. :data:`SHAPE_PATH_SYMBOL` is already narrow, and
    :data:`SHAPE_SYMBOL` (a bare ``module.sub.symbol``) is narrow in a different way:
    both raise only when the symbol's own region moved, so neither has a narrower form to
    be offered.

    Deduplicated, in first-written order, and the *written* spelling is kept — a
    consumer who wrote ``client.py`` must be able to find that line in that file
    (KYO-200's rendering rule).
    """
    found: list[str] = []
    for target in targets:
        raw = target.raw if isinstance(target, GovernsTarget) else str(target)
        path, symbol = split_target(raw)
        if target_shape(path, symbol) == SHAPE_PATH:
            found.append(raw)
    return tuple(dict.fromkeys(found))


def whole_file_nudge(targets: Sequence[str]) -> str:
    """The one-per-document sentence that offers the narrower declaration (KYO-199.c2).

    Once per document and never once per obligation, because the remedy is a single edit
    to one ``governs:`` line and repeating it beside each of a document's five sections
    would be the per-row noise this record was filed about. It is an offer and not a
    finding: a whole-file target is a legitimate declaration for a section that really is
    about the whole file, and nothing here knows which case this is.

    **KYO-351: offered only for the targets it is true of, and empty when that is none.**
    The narrowing is :data:`REGION_SUFFIXES` and nothing else, so on any other suffix the
    sentence would be advice to make an edit that changes nothing
    (docs/design/docs.md#kyo-351). Filtered through :func:`narrowable_targets` rather than
    left to the caller: every surface that prints this holds the same unfiltered list, and
    one of them
    forgetting is the bug coming back. The empty string is the "no offer" answer, and the
    renderers test it rather than printing a sentence with nothing in its parentheses.

    KYO-489: the explanation after the offer prints as its short form; the full text
    (:func:`whole_file_narrowing_explanation`) is in `kyo reference report`.
    """
    offer = whole_file_offer(targets)
    if not offer:
        return ""
    return f"{offer} {explanations.say('whole-file-narrowing')}"


def whole_file_offer(targets: Sequence[str]) -> str:
    """The per-document half of :func:`whole_file_nudge`: which targets, and the form.

    KYO-488. The offer names this document's own targets and is printed for every
    document that has one; the explanation after it is the same words for every
    document, so a rendering of several documents prints it once
    (:func:`grouped_obligation_lines`). Empty when nothing can be narrowed (KYO-351).
    """
    offered = narrowable_targets(targets)
    if not offered:
        return ""
    return "governs whole files (" + ", ".join(offered) + ") — `governs: <path>#<symbol>`"


def whole_file_narrowing_explanation() -> str:
    """How the narrowing works and which suffixes it holds for — the half of
    :func:`whole_file_nudge` that is identical for every document (KYO-488)."""
    return explanations.full("whole-file-narrowing")


def section_whole_file_targets(
    root: Path, *, documents_in: DocumentSet = DEFAULT
) -> dict[tuple[str, str], tuple[str, ...]]:
    """Every ``(document, section)`` that declares a whole-file target, and which ones.

    KYO-199.c2 for the surface that reads STORED obligations. ``kyo report`` and
    ``kyo docs preview`` both hold the :class:`DocsObligation` that was just computed, so
    they know which targets matched; ``kyo docs obligations`` reads rows out of the store
    and the matched targets were never a column (c1: the stored unit is unchanged). So
    this asks the documents, through the same enumerator everything else uses.

    The answer is therefore "what this section declares", not "what matched on that
    branch" — a slightly wider question, and the wider one is the right one for a nudge
    whose remedy is a single edit to the section's ``governs:`` line. Dead sections are
    skipped, as they are everywhere else (:func:`section_is_dead`).
    """
    found: dict[tuple[str, str], tuple[str, ...]] = {}
    for document in documents(root, documents_in=documents_in):
        for section in document.sections:
            if section.dead:
                continue
            targets = whole_file_targets(section.resolved or section.governs)
            if targets:
                found[(document.path, section.heading)] = targets
    return found


def settle_command(ids: Sequence[str]) -> str:
    """The ``kyo docs settle`` line that would discharge ``ids``, ids spelled out.

    KYO-159 decision 1, and the KYO-71 pattern it is modelled on: ``kyo report`` prints
    the exact ``kyo edit --touches-file`` line its finding implies and applies none of
    it. The tool has the ids and the caller should not have to retype five of them; the
    tool does NOT have the verdict, the method or the reason, and those stay as
    placeholders because they are the review, and only a person performed it.

    So this line is copied and completed rather than run as printed — which is the
    difference between making the honest path cheap and asserting on someone's behalf
    that they read the whole document.

    KYO-199 moved it here from ``cmd_views``: ``kyo report`` prints this line per
    document now too, and the alternative was cmd_dispatch importing a private helper out
    of cmd_views or spelling the same line a second time.
    """
    line = (
        f"kyo docs settle {' '.join(ids)} "
        f"--verdict {OBLIGATION_UPDATED}|{OBLIGATION_NOT_AFFECTED} "
        "--method '<how you decided>'"
    )
    if len(ids) > 1:
        line += " --why '<what made these one editorial act>'"
    return line


def group_obligations_per_document(rows: Sequence[Mapping[str, object]]) -> list[dict]:
    """``rows`` grouped per governed document, in first-seen order (KYO-199.c1).

    ``rows`` are mappings carrying at least ``doc_path`` and ``section``. Every other key
    is optional and each is absent on at least one of the three surfaces, which is why
    none of them is required: ``id`` and ``detail`` (a preview has no stored row, so no
    id), ``targets`` (the matched ``governs:`` targets, for the c2 nudge), and
    ``proposed`` (the worker's own ``docs_verdicts[]`` entry, matched at ingest).

    Nothing is dropped, collapsed or deduplicated: every row is still a member of exactly
    one group and every group names every member. This is a rendering of the same rows,
    not a second set of them — the same property KYO-159's grouping was careful to keep.
    """
    order: list[str] = []
    members: dict[str, list[dict]] = {}
    for row in rows:
        path = str(row["doc_path"])
        if path not in members:
            members[path] = []
            order.append(path)
        members[path].append(dict(row))
    groups: list[dict] = []
    for path in order:
        here = members[path]
        ids = [str(row["id"]) for row in here if row.get("id")]
        # KYO-351: the offer, not the census. `whole_file_targets` answers "which of
        # these name a whole file"; `narrowable_targets` answers "and which of those
        # could be narrowed at all", and this key exists only to be rendered into the
        # nudge — so it carries the second answer and a `--json` reader is told the same
        # thing the terminal is. The two surfaces that build this key (here, and
        # `section_whole_file_targets` feeding it through `targets`) therefore cannot
        # offer different sets, which is what put the filter at this one join.
        nudge = tuple(
            dict.fromkeys(
                target
                for row in here
                for target in narrowable_targets(whole_file_targets(row.get("targets") or ()))
            )
        )
        groups.append(
            {
                "doc_path": path,
                "members": here,
                "sections": [str(row["section"]) for row in here],
                "obligation_ids": ids,
                "whole_file_targets": list(nudge),
                # None, not an empty string, for a preview: there is no row to settle
                # yet, and printing a settle line naming no id would be a command that
                # cannot run (law 6 — say what is absent, do not render it as present).
                "settle_command": settle_command(ids) if ids else None,
            }
        )
    return groups


#: The state an obligation with no verdict reads as, in the counts line and as the
#: ``state`` of its collapsed group (KYO-219). The word `kyo show` prints in the
#: ``[...]`` slot for an unsettled obligation, named once here so the counts line and the
#: group header cannot drift apart from the listing they summarise.
OBLIGATION_STATE_OPEN = "open"


def collapse_obligations(rows: Sequence[Mapping[str, object]]) -> dict:
    """``rows`` collapsed for reading, and counted by state (KYO-219.c1/c3).

    ``rows`` are the mappings ``kyo show`` carries under ``docs_obligations``: at least
    ``id``, ``doc_path``, ``section``, ``cause``, ``dispatch_id``, ``detail``,
    ``verdict``, ``method``, ``author``, ``settled_at`` and ``settled_why``.

    The shape returned::

        {
          "total": int,                     # len(rows)
          "open": int,                      # rows with no verdict
          "counts_by_state": [              # `open` first, then each verdict seen,
            {"state": str, "count": int},   # in OBLIGATION_VERDICTS order; a state
          ],                                # with no members is omitted, never zero
          "groups": [
            {
              "state": str,                 # the verdict, or OBLIGATION_STATE_OPEN
              "method": str | None,
              "author": str | None,
              "settled_at": str | None,
              "settled_why": str | None,    # carried ONCE for every member
              "count": int,
              "obligation_ids": [str, ...], # every member, in row order
              "causes": [str, ...],         # distinct, first-seen order
              "dispatch_ids": [str, ...],   # distinct, first-seen order
              "standing": int,              # members with no dispatch (KYO-554)
              "details": [
                {
                  "detail": str,
                  "count": int,
                  "obligation_ids": [str, ...],
                  "doc_paths": [str, ...],  # distinct, first-seen order
                  "sections": [str, ...],   # distinct, first-seen order
                },
              ],
              "members": [dict, ...],       # the rows themselves, unmodified
            },
          ],
        }

    Two levels, because there are two repeated texts and they repeat at different rates.
    The outer key is the *settlement* — ``(state, method, author, settled_at,
    settled_why)`` — which is exactly what one ``kyo docs settle`` act stamps onto every
    obligation it names, so KYO-159's one ``--why`` over a governed-file set produces one
    group however many obligations it settled, and the 400-character note is carried once
    rather than once per member. The inner key is ``detail``, so obligations that share
    both texts are one entry carrying the shared text once, the count and the member ids
    (c1's literal requirement).

    The inner level is not redundant with the outer one and neither subsumes the other:
    a ``governs`` obligation's ``detail`` names its own document and section (see
    :func:`_governs_obligations`), so obligations settled together generally do *not*
    share a detail, and a single-level collapse on ``(settled_why, detail)`` would have
    collapsed nothing at all in the case this record was filed about. What the two levels
    guarantee together is the bound c1 asks for: no stored text is printed twice, so the
    output grows with the number of DISTINCT texts and not with the obligation count.

    Nothing is dropped, reordered across groups, or deduplicated away: every row is a
    member of exactly one group and of exactly one of that group's details, ``members``
    carries the rows themselves, and ``total`` equals ``len(rows)``. This is a rendering
    of the same rows, not a second set of them — the property
    :func:`group_obligations_per_document` was careful to keep, kept here for the same
    reason: ``kyo show --json`` still carries every obligation individually (c2), and a
    consumer that reads this grouping must be reading the same facts.
    """
    order: list[tuple] = []
    members: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (
            row.get("verdict") or OBLIGATION_STATE_OPEN,
            row.get("method"),
            row.get("author"),
            row.get("settled_at"),
            row.get("settled_why"),
        )
        if key not in members:
            members[key] = []
            order.append(key)
        members[key].append(dict(row))

    groups: list[dict] = []
    for key in order:
        here = members[key]
        state, method, author, settled_at, settled_why = key
        detail_order: list[str] = []
        by_detail: dict[str, list[dict]] = {}
        for row in here:
            detail = str(row.get("detail") or "")
            if detail not in by_detail:
                by_detail[detail] = []
                detail_order.append(detail)
            by_detail[detail].append(row)
        groups.append(
            {
                "state": state,
                "method": method,
                "author": author,
                "settled_at": settled_at,
                "settled_why": settled_why,
                "count": len(here),
                "obligation_ids": [str(row["id"]) for row in here],
                "causes": list(dict.fromkeys(str(row["cause"]) for row in here)),
                # KYO-554: a standing obligation has no dispatch, and a null is not an
                # id -- it is counted under `standing` instead of printed as "None".
                "dispatch_ids": list(
                    dict.fromkeys(
                        str(row["dispatch_id"]) for row in here if row.get("dispatch_id")
                    )
                ),
                "standing": sum(1 for row in here if not row.get("dispatch_id")),
                "details": [
                    {
                        "detail": detail,
                        "count": len(by_detail[detail]),
                        "obligation_ids": [str(row["id"]) for row in by_detail[detail]],
                        "doc_paths": list(
                            dict.fromkeys(str(row["doc_path"]) for row in by_detail[detail])
                        ),
                        "sections": list(
                            dict.fromkeys(str(row["section"]) for row in by_detail[detail])
                        ),
                    }
                    for detail in detail_order
                ],
                "members": here,
            }
        )

    counts: dict[str, int] = {}
    for row in rows:
        state = str(row.get("verdict") or OBLIGATION_STATE_OPEN)
        counts[state] = counts.get(state, 0) + 1
    # `open` first because it is the only state that is a question rather than an
    # answer, then the verdict vocabulary in the order contracts.py declares it — a
    # fixed order rather than first-seen, so two records' counts lines compare by eye.
    # A state with no members is omitted: a zero printed beside a real count reads as
    # something that happened (law 6), and what did not happen is not a state.
    state_order = [OBLIGATION_STATE_OPEN, *OBLIGATION_VERDICTS]
    counts_by_state = [
        {"state": state, "count": counts[state]} for state in state_order if state in counts
    ]
    # Anything the verdict vocabulary does not name (an older export, a verdict added
    # after this code was written) still appears, after the known states, rather than
    # being silently dropped from a line whose whole job is to add up to the total.
    counts_by_state += [
        {"state": state, "count": count}
        for state, count in counts.items()
        if state not in state_order
    ]
    return {
        "total": len(rows),
        "open": counts.get(OBLIGATION_STATE_OPEN, 0),
        "counts_by_state": counts_by_state,
        "groups": groups,
    }


def grouped_obligation_lines(
    groups: Sequence[Mapping[str, object]], *, indent: str = "  "
) -> list[str]:
    """One block per governed document: its sections, then its one settle line (c1).

    The ``kyo docs settle`` line sits at the *foot* of its document's block rather than
    in a separate tail section, because the reading it belongs to is the reading of that
    document — a tail that lists which documents hold more than one obligation is a
    second rendering of the same grouping, and KYO-199 removed it from both surfaces that
    had one rather than leaving two.
    """
    lines: list[str] = []
    explained = False
    for group in groups:
        members = list(group.get("members") or ())
        # `dispatch_id` is present only on `kyo docs obligations`, whose listing spans
        # dispatches and where "which diff raised this" is the question a reader has
        # before they open anything. `kyo report` and the preview are one dispatch by
        # construction and would be repeating themselves.
        raised_by = f" from {group['dispatch_id']}" if group.get("dispatch_id") else ""
        if group.get("standing_on"):
            # KYO-554: raised by the record itself, which no dispatch's diff did.
            raised_by = f" standing on {group['standing_on']}"
        lines.append(f"{indent}{group['doc_path']} — {len(members)} section(s){raised_by}")
        for row in members:
            head = f"{indent}    "
            if row.get("id"):
                head += f"{row['id']}  "
            head += f"[{row['section']}]"
            if row.get("verdict"):
                head += f"  [{row['verdict']}]"
            lines.append(head)
            if row.get("detail"):
                lines.append(f"{indent}        {row['detail']}")
            proposed = row.get("proposed")
            if proposed:
                lines.append(
                    f"{indent}        worker proposes {proposed['verdict']} "
                    f"({proposed['method']}): {proposed['reason']}"
                )
        # KYO-351: the sentence decides whether it is printed. `whole_file_nudge`
        # returns "" when nothing it was handed can be narrowed, so a document whose
        # whole-file targets are all `.ts` gets no line at all rather than an offer that
        # is false for every target in it.
        #
        # KYO-488: the explanation after the offer is the same for every document, so it
        # rides on the first offer this rendering prints and every later document gets
        # its own offer alone — once per invocation, not once per document.
        targets = list(group.get("whole_file_targets") or ())
        if whole_file_offer(targets):
            nudge = whole_file_offer(targets) if explained else whole_file_nudge(targets)
            explained = True
            lines.append(f"{indent}    this document {nudge}")
        if group.get("settle_command"):
            lines.append(f"{indent}    {group['settle_command']}")
    return lines


__all__ = [
    "OBLIGATION_CAUSES",
    "OBLIGATION_DECISION",
    "OBLIGATION_DOC_REFS",
    "OBLIGATION_GOVERNS",
    "OBLIGATION_STATE_OPEN",
    "UNOBLIGATED_PREFIXES",
    "DocsObligation",
    "StandingObligations",
    "OBLIGATION_POINTER",
    "coarse_pointer_lines",
    "collapse_obligations",
    "comment_only_paths",
    "pointer_obligations_on",
    "pointer_sources",
    "governed_symbol_paths",
    "group_obligations_per_document",
    "grouped_obligation_lines",
    "obligations_and_suppressions_for_branch",
    "obligations_for_branch",
    "obligations_for_changed_files",
    "section_whole_file_targets",
    "settle_command",
    "standing_obligations",
    "staleness_raises_obligation",
    "suppression_lines",
    "whole_file_narrowing_explanation",
    "whole_file_nudge",
    "whole_file_offer",
    "whole_file_targets",
]
