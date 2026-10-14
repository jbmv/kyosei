"""Layer 3's format and its skeleton validator, ``kyo docs check`` (KYO-13.c3).

``docs/design/charter.md``, "Layer 3 — Documentation SOT" names two checks over
a section's machine-readable metadata, and both are enforced here: a ``governs:``
target that no longer exists fails the build (KYO-13.c3), and so does a section whose
``status`` and ``amended-by``/``superseded-by`` disagree ("pairs agree", KYO-499,
:func:`section_status_problems`). A document set declaring ``section_status:`` in
``documents.yml`` adds four more rules over its own sections and the ``src/`` pointers
into them; the rules, their refusals and their remedies are the
:data:`SECTION_STATUS_REFUSALS` block below, and why: docs/design/docs.md#kyo-499.
Cited rather than quoted at length, per the charter's rule that a docstring may cite
another authority's behavior but must not copy it.

The readers of the same format live beside it (KYO-109): :mod:`kyosei.docs_parse`
(sections and their metadata), :mod:`kyosei.docs_targets` (resolution),
:mod:`kyosei.docs_staleness` (whether a document was touched since the code it
governs was), :mod:`kyosei.docs_obligations`, :mod:`kyosei.docs_numbered` and
:mod:`kyosei.docs_suggest`. Reading is all they do; this module is where a section's
metadata is checked. What KYO-109 changed about the pairs-agree half before it was
built: docs/design/docs.md#kyo-98. The format below is stated here, on the command a
consumer runs, and implemented in the modules above.

**A dead section's `governs:` list is history, not a live claim (KYO-204).** A
section whose ``status`` is ``historical`` or ``superseded``, or whose
``superseded-by`` names something, is never resolved at all — not merely
excused from failing, but never handed to :func:`resolve_target` in the first
place, so a governs entry naming a file deleted a year ago is not even
looked up. ``amended`` is unaffected by this and is a *different* question from
"pairs agree" above: an amended section is still current, corrected later, and
its targets still resolve exactly as before. One rule, :func:`section_is_dead`,
decides this for :func:`check_docs`, :func:`governs_staleness` (via
:func:`document_row`) and :func:`obligations_for_changed_files` alike, so the
three cannot disagree about the same section. A consumer may spell
``amended-by``/``superseded-by`` with an underscore (``amended_by``,
``superseded_by``); both spellings are read as the one field
(:data:`_KEY_ALIASES`).

**Which files are documents** is the project's declaration since KYO-129, not this
module's assumption: ``kyosei/documents.yml`` (:data:`kyosei.registry.DOCUMENT`),
defaulting to root-level ``*.md`` plus ``docs/**/*.md`` for a project that declares
nothing. The check and the query source both resolve it through :func:`document_set`
and walk it through :func:`_iter_docs_files`, which is what makes "the check and the
query source see the same files" a property of the code rather than a coincidence
between two functions (docs/design/docs.md#kyo-129).

**Format** (this build's own call, since ``docs/design/charter.md`` leaves it
unspecified, "format your call, flagged" per the KYO-13 brief): a ``governs:``
line is any line, anywhere inside a markdown section (i.e. after one heading and
before the next) *and outside a fenced code block*, matching :data:`_METADATA_RE`
for the ``governs`` key — see :func:`_fenced` for the fence half, which is the one
part of markdown this skeleton validator has to understand and would rather not.

**Two list forms, and a section may use either or both** (KYO-200). One line,
comma-separated::

    governs: src/kyosei/brief.py, src/kyosei/brief.py#generate_brief

or ``governs:`` with nothing after it, followed by ``- target`` list lines, which
end at the first line that is not one::

    governs:
      - src/kyosei/brief.py
      - src/kyosei/brief.py#generate_brief

The second is YAML's own list spelling, accepted because documents that predate this
parser already write it (docs/design/docs.md#kyo-200). The other metadata keys stay
single-valued and first-line-wins; only ``governs`` accumulates.

**Two symbol separators**: ``path#symbol`` and ``path:symbol`` resolve identically,
for the same reason. :func:`split_target` owns the rule for telling a separating ``:``
from one inside a path, and it is the single function every target-splitting caller
goes through.

**Three target shapes** (:func:`target_shape`): a bare ``path``; a ``path`` narrowed
to a symbol; and a **bare symbol with no path at all** — ``global_queue.brain.step``
resolving as the module file plus that symbol, or ``ImportRun`` resolving to whatever
file under the bases carries it as a whole word. Resolving and raising are two questions
(KYO-397): the target *resolves* wherever its words appear, and it *raises* only on a
change to the file(s) whose region finder locates its definition — a file that merely
uses ``ImportRun`` raises nothing for it — unless no file's finder locates one, in
which case every resolved file raises, as before, and ``kyo docs check`` says so.

**Where a target's path is rooted**: at the project root by default, and under a
document set's ``paths_relative_to:`` bases when it declares any
(:data:`kyosei.registry.DOCUMENT`), tried in the order declared, first hit winning.
A document lives where its app lives: ``apps/server/NOTES.md`` naming
``adapters/alpha/client.py`` means that path under ``apps/server/src/app``,
and the alternative to the field is a validator telling a project where to keep its
documents. Resolution happens once, at parse time, so everything downstream of
:class:`Section` — staleness, obligation raising, the renderers — sees a
**root-relative** path and needs to know nothing about bases.

A target resolves much as a touch does: the path must exist as a file under one of
the bases, and a symbol must additionally appear as a whole-word match somewhere in
that file's text. This is intentionally the same coarse "does the string appear in
the file" check ``cmd_records._validate_touches`` uses, not a real symbol table — a
skeleton validator, per the charter.

**Glob and brace shorthand in a path** (KYO-202), expanded/matched *before* the
existence check above so every downstream consumer sees plain, resolved paths: one
``{a,b,c}`` brace group (``adapters/{alpha,beta}/normalize.py``, docs/design/docs.md#kyo-202)
expands to one target per alternative, each then required to exist (:func:`_brace_expand`); a
shell glob (``deploy/run_*.sh``) resolves when at least one file under the bases
matches, and every match governs (:func:`_resolve_glob`). Two brace groups, or a glob
matching nothing, are refused by name rather than expanded into a guess — see
:data:`GOVERNS_FORMAT` for the sentence a consumer actually sees.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import docs_recx
from . import registry as registry_module
from .docs_numbered import heading_pattern
from .docs_parse import (
    _BARE_METADATA_RE,
    _METADATA_RE,
    DEAD_STATUSES,
    DEFAULT,
    DocumentSet,
    GovernsTarget,
    Section,
    SectionStatusSet,
    _fenced,
    _iter_docs_files,
    _read_section_metadata,
    _sections,
    _uncommented,
    heading_anchor,
    link_values,
    section_is_dead,
)
from .docs_regions import narrows_by_symbol, region_suffixes_says, symbol_regions
from .docs_targets import SHAPE_PATH_SYMBOL, SHAPE_SYMBOL, target_resolver

if TYPE_CHECKING:
    from .docs_ssot import SsotIndex

#: KYO-200.c5. Every shape a ``governs:`` target may take, in one line, carried by the
#: text ``kyo docs check`` prints when a target fails to resolve.
#:
#: It is on the FAILURE and not only in :attr:`DocsCheck.says`, and that placement is
#: the point rather than an accident of where it fit. `kyo docs check`'s failure path
#: raises with the per-problem reasons and nothing else (``cmd_views._cmd_docs``), so a
#: consumer who writes a shape this parser does not accept sees the reasons and no
#: format anywhere near them — which is exactly the moment the format was learned from a
#: message thread instead of from the tool, and exactly what this record was filed
#: about. Repeated per problem rather than printed once, because there is no "once" in
#: that surface to print it in and inventing one means editing a command another
#: dispatch holds.
#:
#: Spelled once here, so the docstring above, this message, and the record cannot come
#: apart into three descriptions of one format (law 3).
GOVERNS_FORMAT = (
    "A governs: list is one comma-separated line (commas inside a `{a,b,c}` group do "
    "not split it), or `governs:` alone followed by `  - target` lines (`*` and `+` "
    "bullets too, blank lines allowed before the first) ending at the first line that "
    "is not one; either may sit in an HTML comment, one-line (`<!-- governs: x.py -->`) "
    "or across lines, and a governs: that names no target is a problem; a target is a path, "
    "`path#symbol` or `path:symbol` (both separators resolve identically), or a bare "
    "symbol with no path (`module.sub.symbol`, or an identifier searched for as a whole "
    "word); paths resolve under the document set's `paths_relative_to:` bases in "
    "documents.yml, in order, and under the project root when it declares none. A "
    "path may carry one `{a,b,c}` brace group (one target per alternative, each "
    "required to exist) or a shell glob (`deploy/run_*.sh`, resolving when at least "
    "one file under the bases matches and governing every match); two brace groups, "
    "or a glob matching nothing, are refused rather than guessed at."
)


@dataclass(frozen=True)
class GovernsProblem:
    """One ``governs:`` target that failed to resolve."""

    doc_path: str  # relative to the project root, posix-style
    section: str  # the heading text the governs: line was found under
    target: str  # the raw "path" or "path#symbol" text, as written
    reason: str


@dataclass(frozen=True)
class GovernsNotice:
    """One ``governs:`` target that resolves and does less than it looks like it does.

    KYO-351.c2, and the reason it is a second list rather than a
    :class:`GovernsProblem` with a gentle ``reason``: this target is **valid**. It
    resolves, it raises its section at file level, and a consumer who wrote it has not
    made a mistake that a build should fail on — ``kyo docs check``'s exit status is
    unchanged by anything here (the test for c2 asserts the status, not only the text).
    What the reader has is a declaration whose extra precision is inert on this file
    type, and the only thing that was ever wrong is that nothing said so.
    """

    doc_path: str  # relative to the project root, posix-style
    section: str  # the heading text the governs: line was found under
    target: str  # the raw "path#symbol" text, as written
    says: str


#: Documents were found and every ``governs:`` target in them resolves.
DOCS_CLEAN = "clean"
#: Documents were found and at least one ``governs:`` target no longer resolves.
DOCS_PROBLEMS = "problems"
#: **No file matched this project's document paths at all**, so nothing was checked.
#: Named rather than folded into :data:`DOCS_CLEAN` for the reason
#: :data:`STALENESS_UNGOVERNED` is named rather than folded into
#: :data:`STALENESS_CURRENT`, one level up: "every claim in this project's documents
#: still resolves" and "this project has no documents to make claims" are different
#: facts, and reporting the second as the first is a clean bill of health issued
#: without an examination (docs/design/docs.md#kyo-129).
DOCS_NO_DOCUMENTS = "no-documents"

DOCS_STATES: tuple[str, ...] = (DOCS_CLEAN, DOCS_PROBLEMS, DOCS_NO_DOCUMENTS)


def docs_check_answered(state: str) -> bool:
    """Whether ``state`` means documents were actually examined.

    A whitelist of exactly two values, for the reason :func:`staleness_answered` gives
    for its whitelist of two: the dangerous failure is an ABSENT flag, so every state
    added to this vocabulary is unanswered until someone decides otherwise.
    """
    return state in (DOCS_CLEAN, DOCS_PROBLEMS)


@dataclass(frozen=True)
class DocsCheck:
    """What ``kyo docs check`` found, including whether it found anything to check.

    ``says`` is filled in every state, including the clean one — the same rule
    :class:`Staleness` holds itself to, and for the same reason: a result that can show
    a failure with evidence and a pass with nothing behind it invites the reader to
    trust the pass more than it has earned.
    """

    state: str
    says: str
    #: The documents enumerated, root-relative and posix-style, in scan order.
    scanned: tuple[str, ...]
    #: One entry per unresolved ``governs:`` target, in the order they were found.
    problems: tuple[GovernsProblem, ...]
    #: Which paths were scanned, and on whose authority.
    document_set: DocumentSet
    #: KYO-204. How many sections, across every scanned document, :func:`section_is_dead`
    #: said were history rather than a live claim — their ``governs:`` targets were never
    #: resolved and could not become a :attr:`problems` entry either way. Always present
    #: and accurate, ``0`` included, in every state — the same "say it even when it's
    #: zero" rule :attr:`says` already holds itself to, so a project with nothing dead
    #: is told that rather than left to infer it from an absent count.
    dead_sections_skipped: int = 0
    #: KYO-351.c2. One entry per resolved ``path#symbol`` target whose file type the
    #: region instrument cannot read, or in whose file it finds no region for the symbol
    #: (KYO-358), so the ``#symbol`` narrows nothing. **Never a
    #: failure**: these do not reach :attr:`state`, do not reach ``ok``, and do not move
    #: the exit code — the same two-tier shape `kyo doctor` has carried since KYO-214,
    #: for the same reason (a finding a reader needs and a build has no business
    #: refusing).
    notices: tuple[GovernsNotice, ...] = ()
    #: KYO-499. One entry per section-status refusal (:func:`section_status_problems`):
    #: pairs that disagree in any document, and — in a set declaring ``section_status:`` —
    #: an unknown status, a link that resolves to no heading or to its own section, a
    #: linked section that never cites the linking one back, and a ``src/`` pointer to a
    #: superseded section. Each is a failure exactly as a :attr:`problems` entry is.
    section_problems: tuple[SectionStatusProblem, ...] = ()

    @property
    def answered(self) -> bool:
        return docs_check_answered(self.state)


def check_docs(root: Path, *, documents_in: DocumentSet = DEFAULT) -> DocsCheck:
    """Validate every ``governs:`` line in every file ``documents_in`` selects.

    Failing on an unresolved target is KYO-13.c3 and unchanged. What KYO-129 adds is the
    third outcome: a project where the scan matched **no file** is told so, rather than
    receiving the empty problem list a fully checked project receives. That is not a
    build failure — a project may legitimately hold no Layer 3 documents, and failing
    every such consumer on upgrade would be a validator asserting a defect it has not
    observed, which is the mirror image of the bug this fixes. It is a distinguishable,
    reportable state that says which paths were scanned and who chose them, so the
    reader can see whether the answer came from an examination.
    """
    scanned: list[str] = []
    problems: list[GovernsProblem] = []
    notices: list[GovernsNotice] = []
    dead_sections_skipped = 0
    resolve = target_resolver(root, documents_in)
    for doc_path in _iter_docs_files(root, documents_in.globs):
        rel = doc_path.relative_to(root).as_posix()
        scanned.append(rel)
        text = doc_path.read_text(encoding="utf-8", errors="replace")
        if documents_in.is_recx_manifest(rel):
            # KYO-600: RECX.yaml under a `recx-manifest` set. Its sections come from its
            # entries and carry no `governs:` lines, so only the targets they resolved and
            # what the reader could not read are problems.
            read = docs_recx.manifest_sections(
                text, documents_in.recx_manifest, resolve, root
            )
            problems += [
                GovernsProblem(rel, found.section, found.target, found.reason)
                for found in read.problems
            ]
            problems += [
                GovernsProblem(rel, section.heading, target.raw, target.reason)
                for section in read.sections
                for target in section.resolved
                if target.reason is not None
            ]
            continue
        for section in _sections(text, resolve):
            if section.dead:
                # KYO-204: never even reaches `target.reason` below -- `_sections`
                # never resolved this section's targets in the first place, so there is
                # nothing here to become a `problems` entry either way.
                dead_sections_skipped += 1
                continue
            # KYO-412: a `governs:` that parsed to no target is a claim the author
            # made and the parser could not read -- a problem like an unresolved target,
            # so GOVERNS_FORMAT is shown, and naming a real target is what clears it.
            # (docs/design/docs.md#kyo-412)
            prose = [line for line, in_code in _fenced(section.body) if not in_code]
            for _ in range(_read_section_metadata(prose)[1]):
                problems.append(
                    GovernsProblem(
                        rel,
                        section.heading,
                        "governs:",
                        "this governs: names no target, so the section governs nothing "
                        "— name at least one path or symbol, or remove the line",
                    )
                )
            for target in section.resolved:
                if target.reason is not None:
                    problems.append(
                        GovernsProblem(rel, section.heading, target.raw, target.reason)
                    )
                    continue
                notice = (
                    _narrowing_notice(rel, section.heading, target)
                    or _no_region_notice(root, rel, section.heading, target)
                    or _undefined_symbol_notice(rel, section.heading, target)
                )
                if notice is not None:
                    notices.append(notice)
    # KYO-499: the section-status rules, over the same files. A1 on every document; the
    # rest on the documents of a set that declared `section_status:`, plus the src/
    # pointers into them (docs/design/docs.md#kyo-499).
    opted = opted_documents(root, documents_in.section_status)
    # KYO-600: a recx manifest has no section metadata, so the status rules skip it.
    markdown = [rel for rel in scanned if not documents_in.is_recx_manifest(rel)]
    section_problems = tuple(
        section_status_problems(
            _read_under(root), markdown, opted, source_files(root) if opted else []
        )
    )
    where = documents_in.says
    if documents_in.section_status:
        where = (
            f"{where}; section status declared by "
            f"{', '.join(chosen.says for chosen in documents_in.section_status)}"
        )
    if documents_in.recx_manifest is not None:
        where = f"{where}; {documents_in.recx_manifest.says}"
    # KYO-204.c2: stated once, reused in both states that actually scanned something —
    # `no-documents` below is truthfully 0 by construction (nothing was scanned to find
    # a dead section in) and does not need the sentence repeated over it.
    dead_note = (
        f"{dead_sections_skipped} dead section(s) skipped (status: historical or "
        f"superseded, or a non-empty superseded-by) — their governs: list is kept as "
        f"history and was not resolved"
    )
    kept = tuple(notices)
    if not scanned:
        return DocsCheck(
            DOCS_NO_DOCUMENTS,
            f"no file under {root} matched this project's document paths, so nothing "
            f"was checked — this is not a clean result, it is no result. Scanned: "
            f"{where}. Either this project keeps its documents somewhere else, in which "
            f"case declare where in a `documents:` mapping in "
            f"{_documents_yml(documents_in)} with a `globs:` list, or it has none yet",
            (),
            (),
            documents_in,
            0,
        )
    if problems or section_problems:
        found = []
        if problems:
            found.append(f"{len(problems)} unresolved governs: target(s)")
        if section_problems:
            found.append(f"{len(section_problems)} section-status problem(s)")
        return DocsCheck(
            DOCS_PROBLEMS,
            f"{' and '.join(found)} across {len(scanned)} document(s). Scanned: {where}. "
            f"{dead_note}." + (f" {GOVERNS_FORMAT}" if problems else ""),
            tuple(scanned),
            tuple(problems),
            documents_in,
            dead_sections_skipped,
            kept,
            section_problems,
        )
    return DocsCheck(
        DOCS_CLEAN,
        f"every governs: target in {len(scanned)} document(s) resolves, and every "
        f"section's status agrees with its links. Scanned: {where}. {dead_note}",
        tuple(scanned),
        (),
        documents_in,
        dead_sections_skipped,
        kept,
    )


# ------------------------------------------------ KYO-499: section status and its links
#
# The rules KYO-499 accepted; the reasoning is
# docs/design/docs.md#kyo-499. A1 (pairs agree) holds in every document of every
# project; A2-A5 hold in a set that declares `section_status:` in documents.yml.

#: A1. A section names a section that changed it and its status says nothing changed.
SECTION_LINK_WITHOUT_STATUS = "section-link-without-status"
#: A1. A section's status says something changed it and it names nothing.
SECTION_STATUS_WITHOUT_LINK = "section-status-without-link"
#: A4. A status outside the set's declared ``statuses``.
SECTION_STATUS_UNKNOWN = "section-status-unknown"
#: A2. A link that names no heading.
SECTION_LINK_UNRESOLVED = "section-link-unresolved"
#: A2. A link that names the section it sits in.
SECTION_LINK_TO_ITSELF = "section-link-to-itself"
#: A3. A linked section whose prose never mentions the section that links to it.
SECTION_LINK_NOT_CITED_BACK = "section-link-not-cited-back"
#: A5. A ``src/`` pointer to a superseded section whose comment does not name its successor.
POINTER_TO_SUPERSEDED_SECTION = "pointer-to-superseded-section"

#: Every section-status refusal, in KYO-499's order — the set
#: docs/reference/topics/docs-governance.md, the charter and docs/design/docs.md name
#: (law 3).
SECTION_STATUS_REFUSALS: tuple[str, ...] = (
    SECTION_LINK_WITHOUT_STATUS,
    SECTION_STATUS_WITHOUT_LINK,
    SECTION_LINK_UNRESOLVED,
    SECTION_LINK_TO_ITSELF,
    SECTION_LINK_NOT_CITED_BACK,
    SECTION_STATUS_UNKNOWN,
    POINTER_TO_SUPERSEDED_SECTION,
)

#: The status a non-empty ``amended-by`` may sit beside. ``superseded`` because a retired
#: section may keep the amendments it had before it was retired.
_AMENDED_STATUSES = frozenset({"amended", "superseded"})

#: Where A5 looks for code pointers into an opted-in set: the code is the elsewhere that
#: cites sections as current, and it lives in this directory.
POINTER_SOURCE_ROOT = "src"

#: Directories under :data:`POINTER_SOURCE_ROOT` that hold no source a person wrote.
_SKIPPED_SOURCE_DIRS = frozenset({"__pycache__", "node_modules"})

#: A line that is a comment and nothing else, in the languages a ``src/`` tree holds.
_COMMENT_LINE_RE = re.compile(r"^\s*(#|//|/\*|\*|--|<!--)")


@dataclass(frozen=True)
class SectionStatusProblem:
    """One section-status refusal (KYO-499): which section, which rule, and the remedy."""

    doc_path: str  # the file the problem sits in, root-relative (a src/ file for A5)
    section: str  # the heading, or ``line N`` for a pointer in a src/ file
    reason: str  # one of SECTION_STATUS_REFUSALS
    says: str
    remedy: str
    #: Every file this problem is about, so ``kyo check-branch`` can keep only the
    #: problems a branch's own changes are part of.
    paths: tuple[str, ...] = ()

    def line(self) -> str:
        return f"{self.doc_path} [{self.section}] {self.reason}: {self.says} — {self.remedy}"

    def as_json(self) -> dict:
        return {
            "doc_path": self.doc_path,
            "section": self.section,
            "reason": self.reason,
            "says": self.says,
            "remedy": self.remedy,
        }


@dataclass(frozen=True)
class _Located:
    doc_path: str
    index: int
    section: Section


def _status(section: Section) -> str | None:
    status = section.metadata.get("status")
    if not isinstance(status, str) or not status.strip():
        return None
    return status.strip().lower()


def _pairs_agree(doc_path: str, section: Section) -> list[SectionStatusProblem]:
    """A1: the status and the links a section declares say the same thing."""
    status = _status(section)
    amended_by = link_values(section.metadata.get("amended-by"))
    superseded_by = link_values(section.metadata.get("superseded-by"))
    shown = status or "absent"
    problems: list[SectionStatusProblem] = []

    def refuse(reason: str, says: str, remedy: str) -> None:
        problems.append(
            SectionStatusProblem(doc_path, section.heading, reason, says, remedy, (doc_path,))
        )

    if amended_by and status not in _AMENDED_STATUSES:
        refuse(
            SECTION_LINK_WITHOUT_STATUS,
            f"amended-by names {', '.join(amended_by)} and status is {shown}",
            "set `status: amended` (or `superseded`), or remove the link if nothing later "
            "changed this section",
        )
    if superseded_by and status not in DEAD_STATUSES:
        refuse(
            SECTION_LINK_WITHOUT_STATUS,
            f"superseded-by names {', '.join(superseded_by)} and status is {shown}",
            "set `status: superseded`, or remove the link if nothing later retired this "
            "section",
        )
    if status == "amended" and not amended_by:
        refuse(
            SECTION_STATUS_WITHOUT_LINK,
            "status is amended and amended-by names nothing",
            "add `amended-by:` naming the section that changed this one",
        )
    if status == "superseded" and not superseded_by:
        refuse(
            SECTION_STATUS_WITHOUT_LINK,
            "status is superseded and superseded-by names nothing",
            "add `superseded-by:` naming the section that retired this one",
        )
    return problems


def _mentions(lines: Iterable[str], words: Iterable[str]) -> bool:
    """Whether any of ``words`` appears in ``lines`` as a whole word, ignoring case.

    ``KYO-27`` is mentioned by ``KYO-27`` and by ``KYO-27.c1``, and not by ``KYO-270``.
    """
    patterns = [
        re.compile(rf"(?<![\w]){re.escape(word)}(?![\w])", re.IGNORECASE)
        for word in words
        if word
    ]
    return any(pattern.search(line) for line in lines for pattern in patterns)


#: KYO-505. The spellings A3 accepts, in a set declaring ``numbered:``, as the linked
#: section citing the linking one back by its number ``N`` — beside the heading and the
#: ``<path>#<anchor>`` pointer every set accepts. Stated here once; the remedy, the
#: documents.yml comment, docs/reference/topics/docs-governance.md and
#: docs/design/docs.md#kyo-505 name the same list.
#:
#: * ``§N`` (``§ N``, ``§§N`` too) — the section sign, how one consumer project's notes
#:   cite one another (166 of its 293 links were cited back this way when KYO-505 was filed);
#: * ``<path> §N`` — the same with the document named first, backticked or not; the
#:   path is the document's root-relative path or its path relative to the citing
#:   document's directory, so ``NOTES.md §N`` inside ``apps/server/NOTES.md`` names it;
#: * ``section N`` / ``sections A, B and N`` — the words, which the same notes write as
#:   "This amends section 61", optionally after a path the way ``§N`` may be.
#:
#: A part letter after the number cites the section it is a part of (``§53b``,
#: ``Section 71h``: the same notes letter their sub-decisions). Never inside a longer
#: number (``§150`` does not cite 15), and never when the path
#: written before it names a different document (``apps/desktop/NOTES.md §62`` in a
#: server note cites the desktop's 62, not the server's). A BARE number does not count:
#: in the documents this was measured on nearly every bare-only cite-back was a
#: ``see_also: [..]`` list, which is a link line and not prose saying what changed, and a
#: bare ``15`` is also a date, a version or a count.
NUMBERED_CITE_SPELLINGS: tuple[str, ...] = (
    "§N",
    "<path> §N",
    "section N",
)

#: One citation by number: an optional document path, the section sign or the word, and
#: a number or a list of them (``sections 178, 186, 201 and 242``, ``§25/§27``), each
#: optionally followed by one part letter (``§53b``).
_NUMBER_CITE_RE = re.compile(
    r"(?:(?:`(?P<ticked>[^`\s]+\.md)`|(?<![\w./-])(?P<plain>[\w./-]+\.md))\s*)?"
    r"(?:§{1,2}\s?|(?<![\w])sections?\s+)"
    r"(?P<numbers>\d+[a-z]?(?:(?:\s*,\s*(?:and\s+|or\s+)?|\s+(?:and|or|&)\s+|\s*/\s*§?)\d+[a-z]?)*)"
    r"(?![\w])",
    re.IGNORECASE,
)


def _names_document(written: str | None, cited_doc: str, citing_doc: str) -> bool:
    """Does a path written before a ``§N`` name ``cited_doc``? No path names the
    document the citation sits in."""
    if written is None:
        return cited_doc == citing_doc
    if written == cited_doc:
        return True
    base = posixpath.dirname(citing_doc)
    return posixpath.normpath(posixpath.join(base, written)) == cited_doc


def _cites_number(
    lines: Iterable[str], number: int, cited_doc: str, citing_doc: str
) -> bool:
    """Whether ``lines`` (in ``citing_doc``) cite section ``number`` of ``cited_doc`` in
    one of :data:`NUMBERED_CITE_SPELLINGS` (KYO-505.c3)."""
    for line in lines:
        for match in _NUMBER_CITE_RE.finditer(line):
            written = match.group("ticked") or match.group("plain")
            if not _names_document(written, cited_doc, citing_doc):
                continue
            if number in {int(found) for found in re.findall(r"\d+", match.group("numbers"))}:
                return True
    return False


def _prose_lines(section: Section) -> list[str]:
    """A section's body without its metadata lines: a back-link line naming a section is
    a link, not the prose that says what that section changed (A3)."""
    return [
        line
        for line in section.body
        if not _METADATA_RE.match(_uncommented(line))
        and not _BARE_METADATA_RE.match(_uncommented(line))
    ]


def _region_prose(corpus: _Corpus, target: _Located) -> list[str]:
    """The prose of a section's whole region: its own text and every deeper sub-heading's
    text, up to the next heading at the same or a shallower depth (KYO-510). The preamble
    (level 0) has no region beyond itself."""
    prose = _prose_lines(target.section)
    level = target.section.level
    if not level:
        return prose
    for section in (corpus.sections(target.doc_path) or [])[target.index + 1 :]:
        if section.level <= level:
            break
        prose.extend(_prose_lines(section))
    return prose


class _Corpus:
    """Documents read once and indexed by path, for resolving links between them.

    ``numbered`` maps each document of a set declaring ``numbered:`` to that set's heading
    template (:func:`numbered_headings`); in those documents a bare number names the
    section whose heading the template renders for it (KYO-505). Every other document
    resolves exactly as it did before that record.
    """

    def __init__(
        self, read: Callable[[str], str | None], numbered: Mapping[str, str] | None = None
    ) -> None:
        self._read = read
        self._sections: dict[str, list[Section] | None] = {}
        self._numbered = dict(numbered or {})
        #: KYO-552: the ssot id index (:class:`kyosei.docs_ssot.SsotIndex`), or ``None``
        #: when no set declares ``ssot:``. Through it a bare id names its owning section
        #: in any ssot document, so the link rules apply through the resolution.
        self.ids: SsotIndex | None = None

    def sections(self, doc_path: str) -> list[Section] | None:
        if doc_path not in self._sections:
            text = self._read(doc_path)
            self._sections[doc_path] = None if text is None else _sections(text)
        return self._sections[doc_path]

    def by_anchor(self, doc_path: str, anchor: str) -> _Located | None:
        for index, section in enumerate(self.sections(doc_path) or ()):
            if section.level and heading_anchor(section.heading) == anchor.lower():
                return _Located(doc_path, index, section)
        return None

    def by_key(self, doc_path: str, key: str) -> _Located | None:
        """A bare key names a heading in the SAME document only (c2): 30 record ids have
        sections in more than one design file, so a bare id cannot resolve across them."""
        wanted = " ".join(key.split()).lower()
        for index, section in enumerate(self.sections(doc_path) or ()):
            if not section.level:
                continue
            if " ".join(section.heading.split()).lower() == wanted:
                return _Located(doc_path, index, section)
        return self.by_anchor(doc_path, heading_anchor(key))

    def number_of(self, doc_path: str, section: Section) -> int | None:
        """The number ``section`` carries under its document's ``numbered.heading``
        template, or ``None`` (an unnumbered document, or a heading the template does not
        describe — a sub-heading, a title)."""
        template = self._numbered.get(doc_path)
        if template is None or not section.level:
            return None
        match = heading_pattern(template).match(f"{'#' * section.level} {section.heading}")
        return int(match.group(1)) if match else None

    def by_number(self, doc_path: str, number: int) -> list[_Located]:
        """Every section of a numbered document whose heading carries ``number``."""
        return [
            _Located(doc_path, index, section)
            for index, section in enumerate(self.sections(doc_path) or ())
            if self.number_of(doc_path, section) == number
        ]

    def resolve(self, doc_path: str, value: str) -> _Located | str:
        """The section ``value`` names, or why it names none."""
        if "#" in value:
            path, _, anchor = value.partition("#")
            path, anchor = path.strip(), anchor.strip()
            if self.sections(path) is None:
                return f"{path} is not a file in this repository"
            found = self.by_anchor(path, anchor)
            return found or f"{path} has no heading whose anchor is #{anchor}"
        if self.ids is not None:
            owned = self.ids.resolve(doc_path, value)
            if owned is not None:
                return owned
        template = self._numbered.get(doc_path)
        looked_for = ""
        if template is not None and re.fullmatch(r"\d+", value, re.ASCII):
            # KYO-505: the number names the heading the set's template renders for it.
            numbered = self.by_number(doc_path, int(value))
            if len(numbered) == 1:
                return numbered[0]
            if numbered:
                headings = "; ".join(
                    f"{'#' * found.section.level} {found.section.heading}" for found in numbered
                )
                return (
                    f"{len(numbered)} sections of {doc_path} carry the number {value} "
                    f"({headings})"
                )
            rendered = template.replace(registry_module.HEADING_NUMBER, str(int(value)))
            looked_for = (
                f" (looked for a heading starting `{rendered}`, the set's numbered.heading)"
            )
        found = self.by_key(doc_path, value)
        return found or f"{value} has no section in {doc_path}{looked_for}"


def numbered_headings(opted: Mapping[str, SectionStatusSet]) -> dict[str, str]:
    """``{document: heading template}`` for each opted-in document whose set declares
    ``numbered:`` — what :class:`_Corpus` resolves a bare number through (KYO-505)."""
    return {
        doc_path: chosen.numbered_heading
        for doc_path, chosen in opted.items()
        if chosen.numbered_heading
    }


def _set_rules(
    corpus: _Corpus, doc_path: str, index: int, section: Section, statuses: Sequence[str]
) -> list[SectionStatusProblem]:
    """A2, A3 and A4 for one section of an opted-in set."""
    problems: list[SectionStatusProblem] = []
    status = _status(section)
    if status is not None and status not in {value.lower() for value in statuses}:
        problems.append(
            SectionStatusProblem(
                doc_path,
                section.heading,
                SECTION_STATUS_UNKNOWN,
                f"status {status!r} is not one of this set's statuses "
                f"({', '.join(statuses)})",
                "use one of those, or add the value to `section_status.statuses` in "
                "documents.yml",
                (doc_path,),
            )
        )
    own_pointer = f"{doc_path}#{heading_anchor(section.heading)}"
    for key in ("amended-by", "superseded-by"):
        for value in link_values(section.metadata.get(key)):
            target = corpus.resolve(doc_path, value)
            if isinstance(target, str):
                problems.append(
                    SectionStatusProblem(
                        doc_path,
                        section.heading,
                        SECTION_LINK_UNRESOLVED,
                        f"{key} names {value}, and {target}",
                        "write that section (the record that changed this one owes the "
                        "reason), or name the section where it is as `<path>.md#<anchor>`; "
                        "a bare key names a heading in this same document only",
                        (doc_path,),
                    )
                )
                continue
            if target.doc_path == doc_path and target.index == index:
                problems.append(
                    SectionStatusProblem(
                        doc_path,
                        section.heading,
                        SECTION_LINK_TO_ITSELF,
                        f"{key} names {value}, which is this section",
                        "name the section that changed this one, or remove the link",
                        (doc_path,),
                    )
                )
                continue
            words = [section.heading, own_pointer]
            own_id = corpus.ids.id_of(doc_path, index) if corpus.ids is not None else None
            if own_id:
                # KYO-552: in an ssot set the linked section cites this one back by id.
                words.append(own_id)
            prose = _region_prose(corpus, target)
            number = corpus.number_of(doc_path, section)
            if _mentions(prose, words) or (
                number is not None and _cites_number(prose, number, doc_path, target.doc_path)
            ):
                continue
            spellings = f"{section.heading} or {own_pointer}"
            if number is not None:
                cite = f"§{number}" if target.doc_path == doc_path else f"{doc_path} §{number}"
                spellings = f"{cite}, section {number}, {spellings}"
            problems.append(
                SectionStatusProblem(
                    doc_path,
                    section.heading,
                    SECTION_LINK_NOT_CITED_BACK,
                    f"{key} names {value}, and {target.doc_path} [{target.section.heading}] "
                    f"never mentions {section.heading}",
                    f"add a sentence to {target.section.heading} saying what it changed "
                    f"about {section.heading} (naming it as {spellings})",
                    (doc_path, target.doc_path),
                )
            )
    return problems


def _comment_block(lines: Sequence[str], at: int) -> list[str]:
    """The comment block line ``at`` sits in: the run of comment lines around it, or —
    for a pointer in a docstring or in prose — the run of non-blank lines around it."""
    is_comment = bool(_COMMENT_LINE_RE.match(lines[at]))

    def belongs(line: str) -> bool:
        if not line.strip():
            return False
        return bool(_COMMENT_LINE_RE.match(line)) if is_comment else True

    start = at
    while start > 0 and belongs(lines[start - 1]):
        start -= 1
    end = at
    while end + 1 < len(lines) and belongs(lines[end + 1]):
        end += 1
    return list(lines[start : end + 1])


def _successor_words(corpus: _Corpus, target: _Located) -> list[str]:
    """Every spelling that names one of ``target``'s successors: each ``superseded-by``
    value as written, and the heading and pointer of the section it resolves to."""
    words: list[str] = []
    for value in link_values(target.section.metadata.get("superseded-by")):
        words.append(value)
        found = corpus.resolve(target.doc_path, value)
        if isinstance(found, _Located):
            words.append(found.section.heading)
            words.append(f"{found.doc_path}#{heading_anchor(found.section.heading)}")
    return words


def _pointer_problems(
    corpus: _Corpus,
    source_path: str,
    text: str,
    pointer_re: re.Pattern[str],
) -> list[SectionStatusProblem]:
    """A5 for one source file."""
    problems: list[SectionStatusProblem] = []
    lines = text.splitlines()
    for number, line in enumerate(lines):
        for match in pointer_re.finditer(line):
            doc_path, anchor = match.group(1), match.group(2)
            target = corpus.by_anchor(doc_path, anchor)
            if target is None or not section_is_dead(target.section.metadata):
                continue
            successors = _successor_words(corpus, target)
            if successors and _mentions(_comment_block(lines, number), successors):
                continue
            pointer = f"{doc_path}#{anchor}"
            named = ", ".join(link_values(target.section.metadata.get("superseded-by")))
            retired = f"which {named} superseded" if named else "which is retired"
            problems.append(
                SectionStatusProblem(
                    source_path,
                    f"line {number + 1}",
                    POINTER_TO_SUPERSEDED_SECTION,
                    f"{source_path}:{number + 1} points at {pointer}, {retired}",
                    "re-point it at the successor, or name the successor in the same "
                    "comment if the history is the point",
                    (source_path, doc_path),
                )
            )
    return problems


def section_status_problems(
    read: Callable[[str], str | None],
    documents: Sequence[str],
    opted: Mapping[str, SectionStatusSet],
    sources: Sequence[str],
) -> list[SectionStatusProblem]:
    """Every section-status refusal over a tree, read through ``read`` (KYO-499).

    ``documents`` are every document's root-relative path (A1 runs on all of them);
    ``opted`` maps each document in a set that declared ``section_status:`` to its set
    (A2-A5 run on those); ``sources`` are the files A5 reads for pointers. ``read``
    returns a file's text, or ``None`` for a file the tree does not hold — a callable so
    ``kyo docs check`` reads the working tree and ``kyo check-branch`` the branch, through
    one implementation of the rules (c4 of KYO-499: "the same refusals").
    """
    corpus = _Corpus(read, numbered_headings(opted))
    # KYO-552: the ssot id index is built over EVERY ssot document before any link is
    # resolved, because an id resolves repo-wide; its own refusals are reported for the
    # documents asked about. Imported here because docs_ssot builds on this module.
    from . import docs_ssot

    declarations = {path: chosen.ssot for path, chosen in opted.items() if chosen.ssot}
    if declarations:
        corpus.ids = docs_ssot.SsotIndex(corpus, declarations)
    problems: list[SectionStatusProblem] = []
    for doc_path in documents:
        sections = corpus.sections(doc_path) or []
        chosen = opted.get(doc_path)
        for index, section in enumerate(sections):
            problems += _pairs_agree(doc_path, section)
            if chosen is not None:
                problems += _set_rules(corpus, doc_path, index, section, chosen.statuses)
    if corpus.ids is not None:
        problems += docs_ssot.ssot_problems(corpus.ids, [d for d in documents if d in declarations])
    pointable = sorted(opted, key=len, reverse=True)
    if pointable and sources:
        pointer_re = re.compile(
            rf"(?<![\w/.-])({'|'.join(re.escape(path) for path in pointable)})"
            r"#([A-Za-z0-9][\w-]*)"
        )
        for source_path in sources:
            text = read(source_path)
            if text is None or "\0" in text[:8192]:
                continue
            problems += _pointer_problems(corpus, source_path, text, pointer_re)
    return problems


def opted_documents(root: Path, sets: Sequence[SectionStatusSet]) -> dict[str, SectionStatusSet]:
    """Each document a ``section_status:`` set enumerates under ``root``, mapped to the set
    (the first set to name it, in declaration order)."""
    opted: dict[str, SectionStatusSet] = {}
    for chosen in sets:
        for path in _iter_docs_files(root, chosen.globs):
            opted.setdefault(path.relative_to(root).as_posix(), chosen)
    return opted


def source_files(root: Path) -> list[str]:
    """Every file under ``root/src``, root-relative, skipping caches (A5's reading set)."""
    base = root / POINTER_SOURCE_ROOT
    if not base.is_dir():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in base.rglob("*")
        if path.is_file() and not _SKIPPED_SOURCE_DIRS.intersection(path.parts)
    )


def _read_under(root: Path) -> Callable[[str], str | None]:
    def read(rel: str) -> str | None:
        try:
            return (root / rel).read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            return None

    return read


def _narrowing_notice(
    doc_path: str, section: str, target: GovernsTarget
) -> GovernsNotice | None:
    """KYO-351.c2: this ``path#symbol`` resolves and narrows nothing — or ``None``.

    Only :data:`SHAPE_PATH_SYMBOL`, and only when the *written* path's suffix is outside
    :data:`REGION_SUFFIXES`. A bare-symbol target (:data:`SHAPE_SYMBOL`) is deliberately
    not judged here: it may resolve to several files at once, and "this declaration is
    inert" is not a thing that can be said about a target half of whose files might be
    Python. The narrower claim is the one that can be made honestly.
    """
    if target.shape != SHAPE_PATH_SYMBOL or not target.path:
        return None
    if narrows_by_symbol(target.path):
        return None
    return GovernsNotice(
        doc_path,
        section,
        target.raw,
        f"accepted, and narrows nothing: `#{target.symbol}` raises this section whenever "
        f"{target.path} changes at all, exactly as the bare path would. A `#symbol` "
        f"narrows to the symbol's own lines only on {region_suffixes_says()} targets",
    )


def _no_region_notice(
    root: Path, doc_path: str, section: str, target: GovernsTarget
) -> GovernsNotice | None:
    """KYO-358.c3: :func:`_narrowing_notice` one case further along — the file type IS one
    the instrument reads, and it finds no region for this symbol in it.

    The target resolves because the symbol appears as whole-word text; narrowing needs a
    *declaration* the region finder can place, and a name that is only imported, only
    called, or declared in a shape the finder does not read gets ``()``, which raises
    like the bare path. A consumer narrowing a hundred targets learns which ones took
    from this list rather than from a preview against old merges. ``None`` — the source
    will not parse — is not reported: that is the file's state today, not the
    declaration's.
    """
    if target.shape != SHAPE_PATH_SYMBOL or not target.symbol or len(target.paths) != 1:
        return None
    path = target.paths[0]
    if not narrows_by_symbol(path):
        return None
    try:
        text = (root / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if symbol_regions(path, text, target.symbol) != ():
        return None
    return GovernsNotice(
        doc_path,
        section,
        target.raw,
        f"accepted, but no region found: `{target.symbol}` appears in {path} and nothing "
        f"there declares it in a shape the region instrument places, so this section "
        f"raises whenever the file changes at all, like the bare path",
    )


def _undefined_symbol_notice(
    doc_path: str, section: str, target: GovernsTarget
) -> GovernsNotice | None:
    """KYO-397.c2: a bare-symbol target no region finder locates a definition of.

    Such a target keeps the pre-KYO-397 behaviour — every file it resolved to by text
    raises its section on any change — because narrowing to a definition nobody found
    would be narrowing to nothing. That cost is invisible from the declaration (a bare
    ``ImportRun`` looks as narrow as a target can be), so the check names the target and
    how many files it is paying for.
    """
    if target.shape != SHAPE_SYMBOL or target.defined_in or not target.paths:
        return None
    count = len(target.paths)
    return GovernsNotice(
        doc_path,
        section,
        target.raw,
        f"accepted, but no definition found: `{target.symbol}` resolves by text to "
        f"{count} file(s), and in none of them does a region finder locate a declaration "
        f"of it, so this section raises whenever any of those {count} file(s) changes. "
        f"A symbol with a located definition raises only when that definition changes",
    )


def _documents_yml(documents_in: DocumentSet) -> str:
    """Where to write the declaration the no-documents message asks for.

    The real path when a registry was consulted, and the bare filename when the caller
    went through the direct API and there is no store in view — rather than a plausible
    path guessed from the code root, which is the wrong file under the split layout and
    would be a remedy naming somewhere the reader's registry is not (KYO-73's class).
    """
    path = documents_in.registry_path
    return str(path) if path is not None else f"kyosei/{registry_module.DOCUMENT.filename}"


__all__ = [
    "DOCS_CLEAN",
    "DOCS_NO_DOCUMENTS",
    "DOCS_PROBLEMS",
    "DOCS_STATES",
    "POINTER_TO_SUPERSEDED_SECTION",
    "NUMBERED_CITE_SPELLINGS",
    "SECTION_LINK_NOT_CITED_BACK",
    "SECTION_LINK_TO_ITSELF",
    "SECTION_LINK_UNRESOLVED",
    "SECTION_LINK_WITHOUT_STATUS",
    "SECTION_STATUS_REFUSALS",
    "SECTION_STATUS_UNKNOWN",
    "SECTION_STATUS_WITHOUT_LINK",
    "DocsCheck",
    "GovernsNotice",
    "GovernsProblem",
    "SectionStatusProblem",
    "check_docs",
    "docs_check_answered",
    "numbered_headings",
    "opted_documents",
    "section_status_problems",
    "source_files",
]
