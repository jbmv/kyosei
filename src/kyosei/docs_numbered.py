"""Numbered documents: doc fragments and their assembly, and matching a report's
``docs_verdicts[]`` to the obligations they answer.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from . import ids
from . import registry as registry_module
from .docs_parse import _fenced, _iter_docs_files
from .docs_targets import _is_glob
from .errors import KyoseiError

#: KYO-199.c4: why an entry in a report's ``docs_verdicts[]`` matched no obligation. Two
#: reasons, both of which have to be *named* rather than counted — a controller reading
#: "3 of 5 applied" and nothing else cannot tell a worker's typo from a worker's verdict
#: on a section its branch never touched, and those call for opposite responses.
VERDICT_UNMATCHED_NO_OBLIGATION = "no-obligation"
VERDICT_UNMATCHED_DUPLICATE = "duplicate-entry"


def section_number(section: str, template: str) -> int | None:
    """The number a stored section heading carries under a ``numbered:`` template.

    KYO-393. ``section`` is a heading as this module stores it — the text after the
    ``#`` markers (:data:`HEADING_RE`'s second group) — so the template's own ``#``
    run is dropped before it is matched; a report entry that quoted the markers too
    (``## 205. …``) is read the same way. ``None`` when the heading does not follow
    the template at all: only a heading the document's own declaration describes has
    a number here, never one that merely happens to begin with a digit.
    """
    before, _, after = template.partition(registry_module.HEADING_NUMBER)
    prefix = re.sub(r"^#{1,6}\s+", "", before)
    text = re.sub(r"^#{1,6}\s+", "", section.strip())
    match = re.match(rf"^{re.escape(prefix)}(\d+){re.escape(after)}", text)
    return int(match.group(1)) if match else None


def numbered_templates(kyosei_dir: Path, code_root: Path) -> dict[str, str]:
    """``{document path: heading template}`` for every document declared ``numbered:``.

    KYO-393: what :func:`match_docs_verdicts` needs to match a verdict by section
    number, read through :func:`numbered_documents` — the same reading ``kyo docs
    assemble`` and the brief use — so "this document is numbered" has one answer.
    Empty, never an exception, when ``documents.yml`` is refused: ``kyo docs check`` is
    where that is reported, and matching then falls back to the exact heading, which is
    what it did before a document could be numbered at all.
    """
    try:
        documents = numbered_documents(kyosei_dir, code_root)
    except KyoseiError:
        return {}
    return {document.path: document.heading for document in documents}


def match_docs_verdicts(
    obligations: Sequence[Mapping[str, object]],
    verdicts: Sequence[Mapping[str, object]],
    numbered: Mapping[str, str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Pair each report entry with the obligation it names, by (document, section).

    KYO-199.c4, and the whole of why the report's key is a *pair of strings* rather than
    an obligation id: the ids do not exist when the worker writes the report. They are
    minted by ``kyo report``, out of the very join that ingest is running — so the worker
    has no id to name, and the only thing both sides can independently know is which
    document and which heading. ``kyo docs preview`` prints exactly those two strings for
    the same reason.

    Returns ``(matched, unmatched)``. A matched pair carries the obligation row and the
    entry; an unmatched entry carries its own ``why`` from
    :data:`VERDICT_UNMATCHED_NO_OBLIGATION` / :data:`VERDICT_UNMATCHED_DUPLICATE`.
    Nothing is guessed at: a heading paraphrased, misspelled, or belonging to a section
    this branch never touched matches nothing and is *reported*, which is the mechanism
    behind the worker-audience rule (a verdict against a section you did not read cannot
    pass silently, even though nothing can check that you read the one you did).

    **KYO-393: in a ``numbered:`` document the section number is a second key.**
    ``numbered`` maps a document path to its declared heading template
    (:func:`numbered_templates`). When an entry's exact heading matches nothing and its
    document is declared numbered, the number its heading carries under that template
    (:func:`section_number`) is matched against the numbers of that document's
    obligations. That is not a guess: the number is the one key the worker and an
    append-only document share, where a heading's tail may differ
    (docs/design/docs.md#kyo-393). It is still *said*: such a pair carries
    ``by_number`` (the number), and every caller prints :func:`number_matched_line` for
    it. A number two obligations carry matches neither — that document has a history a
    matcher must not choose between.

    One matcher for every caller — ``kyo report``'s ingest, ``kyo docs settle
    --from-report`` and so ``kyo accept`` — so the three cannot come to disagree about
    which obligation a verdict answers.

    A second entry for one section is unmatched rather than overwriting the first,
    however it reached that section (heading or number): two verdicts about one section
    are a contradiction the controller resolves, not one the order of a JSON array
    resolves.
    """
    templates = dict(numbered or {})
    by_key: dict[tuple[str, str], Mapping[str, object]] = {
        (str(row["doc_path"]), str(row["section"])): row for row in obligations
    }
    by_number: dict[tuple[str, int], list[Mapping[str, object]]] = {}
    for row in obligations:
        template = templates.get(str(row["doc_path"]))
        number = section_number(str(row["section"]), template) if template else None
        if number is not None:
            by_number.setdefault((str(row["doc_path"]), number), []).append(row)
    matched: list[dict] = []
    unmatched: list[dict] = []
    claimed: set[tuple[str, str]] = set()
    for entry in verdicts:
        doc_path = str(entry["doc_path"])
        row = by_key.get((doc_path, str(entry["section"])))
        number: int | None = None
        if row is None and doc_path in templates:
            number = section_number(str(entry["section"]), templates[doc_path])
            candidates = by_number.get((doc_path, number), []) if number is not None else []
            row = candidates[0] if len(candidates) == 1 else None
        if row is None:
            unmatched.append(
                {"entry": dict(entry), "why": VERDICT_UNMATCHED_NO_OBLIGATION}
            )
            continue
        key = (doc_path, str(row["section"]))
        if key in claimed:
            duplicate = {"entry": dict(entry), "why": VERDICT_UNMATCHED_DUPLICATE}
            if number is not None:
                duplicate["by_number"] = number
                duplicate["obligation_section"] = str(row["section"])
            unmatched.append(duplicate)
            continue
        claimed.add(key)
        pair = {"obligation": dict(row), "entry": dict(entry)}
        if number is not None:
            pair["by_number"] = number
        matched.append(pair)
    return matched, unmatched


def number_matched_line(pair: Mapping[str, object]) -> str:
    """One verdict matched by section number whose heading differs (KYO-393.c1).

    Applied, and named anyway: the number is a shared key, but a heading that differs
    is still a difference the controller should see before trusting the verdict — a
    worker who wrote ``[205. …]`` from memory may have read section 204.
    """
    entry, obligation = pair["entry"], pair["obligation"]
    return (
        f"{entry['doc_path']} [{entry['section']}] matched [{obligation['section']}] by "
        f"its section number {pair['by_number']} — the report's heading differs from the "
        "document's"
    )


def unmatched_verdict_line(row: Mapping[str, object]) -> str:
    """One unmatched entry, said in a sentence that names what to do about it."""
    entry = row["entry"]
    where = f"{entry['doc_path']} [{entry['section']}]"
    if row["why"] == VERDICT_UNMATCHED_DUPLICATE:
        if row.get("by_number") is not None:
            return (
                f"{where}: names section {row['by_number']} "
                f"([{row['obligation_section']}]) by number, which another "
                "docs_verdicts[] entry already answered. Two verdicts about one section "
                "are a contradiction to resolve, not one to settle — this one was not "
                "applied"
            )
        return (
            f"{where}: a second docs_verdicts[] entry names this section. Two verdicts "
            "about one section are a contradiction to resolve, not one to settle — "
            "neither was applied"
        )
    return (
        f"{where}: no obligation was raised for this section, so nothing here can carry "
        f"the verdict {entry['verdict']!r}. Either the heading does not match the "
        "document's verbatim (this matches on the exact heading text — or, in a document "
        "declared `numbered:`, on its section number — never on a paraphrase), or this "
        "branch's diff does not reach what that section governs. "
        "`kyo docs preview --against <base>` prints the sections it does reach"
    )


# ==================================================================== KYO-299
#
# DOC FRAGMENTS: a document that grows by numbered section, written by workers who
# never collide and assembled by the controller who can see the whole wave.
#
# Two workers who each add the next numbered section to an append-only document collide
# on the number, on the tail of the file and on any index generated from it, no matter
# how their touches were declared, because a symbol-level touch narrows a CLAIM and does
# not move a byte (docs/design/docs.md#kyo-299).
#
# The mechanism is a division of labour, and each half is the party that can actually
# do its job:
#
#   the project   declares the heading template, the fragments directory and the
#                 commands to run afterwards (`numbered:` in documents.yml)
#   the worker    writes ONE new file per new section under that directory, named
#                 after its dispatch, with `{{next}}` where the number goes — a path
#                 no other dispatch can write, so there is nothing to collide on
#   the controller runs `kyo docs assemble` on the merged tree, where the whole wave is
#                 visible and the numbering is therefore decidable
#
# WHAT KYOSEI DOES NOT DO, and this is load-bearing rather than modest: it does not own
# the numbering format (the project declares it), it does not generate the index (the
# project's own command does), and it does not RUN that command. It names it. A tool
# that executed a project's command because a config file said so is a different tool
# from the one that prints `worktree_setup` and executes none of it.
#
# EDITS TO AN EXISTING SECTION ARE NOT THIS. They are ordinary edits, serialized the
# ordinary way by a `path#section heading` touch (KYO-66). Only the ADDITION of a new
# numbered section goes through a fragment, because only the addition has to agree with
# every other worker about a number.


#: KYO-299. Where the number goes, in a fragment, until the controller knows what it is.
#:
#: The spelling has three jobs and they pull against each other. It must be usable
#: INSIDE the section as well as in its heading — a notes-meta block's ``see_also``, a
#: "see section N" the section writes about itself — so it cannot be heading syntax. The
#: heading template must not be able to mistake it for a real number, which ``\d+``
#: cannot: no digit appears in it. And it has to be greppable by a human reviewing a
#: branch, which rules out anything that looks like ordinary prose.
#:
#: ``{{next}}`` over the alternatives considered: ``<N>`` reads as an HTML tag and
#: markdown eats it; ``%N%`` and ``@@N@@`` are greppable and say nothing; ``{number}``
#: would be the same string :data:`kyosei.registry.HEADING_NUMBER` uses for the *template*
#: and the two mean different things (one is "a number goes here in general", the other
#: is "MY number goes here"), which is exactly the confusion two similar spellings buy.
#:
#: The cost is stated rather than implied: a fragment whose subject is templating and
#: which legitimately writes ``{{next}}`` in a code fence would have that occurrence
#: substituted too. Substitution is deliberately whole-fragment and fence-blind, because
#: the placeholder's whole point is to work in the metadata block and the prose alike,
#: and a fence-aware substitution would silently leave one behind in a document nobody
#: re-reads. That fragment's author writes the number by hand after assembly, which is an
#: ordinary edit to an existing section.
FRAGMENT_PLACEHOLDER = "{{next}}"

#: KYO-299. The optional first line of a fragment, naming which document it belongs to.
#: Required only when the fragments directory's own document set enumerates more than
#: one document — the common case is one set, one file, and a worker should not have to
#: restate what the declaration already decides.
#:
#: KYO-493: the line carries a second key, ``amends:``, naming the sections of the same
#: document this one amends. Each key may appear once, in either order. Any first line
#: OPENING ``<!-- kyosei-fragment`` is read as a header (:data:`FRAGMENT_HEADER_OPEN_RE`)
#: and refused when it is not this grammar — before KYO-493 a misspelt key made the line
#: an ordinary HTML comment appended to the document, and the amendment it carried would
#: have gone unwritten without a word.
FRAGMENT_TARGET_RE = re.compile(r"^<!--\s*kyosei-fragment\s+document:\s*(\S+)\s*-->\s*$")
FRAGMENT_HEADER_OPEN_RE = re.compile(r"^<!--\s*kyosei-fragment(?![\w-])")
FRAGMENT_HEADER_RE = re.compile(r"^<!--\s*kyosei-fragment(?![\w-])(.*?)-->\s*$")
#: The keys a header may carry, in the order a refusal lists them.
FRAGMENT_HEADER_KEYS: tuple[str, ...] = ("document", "amends")
_HEADER_KEY_RE = re.compile(r"(?:^|(?<=\s))([A-Za-z][\w-]*):")

#: KYO-299. What separates a fragment's dispatch id from its slug in a file name.
#: Two hyphens rather than one because a dispatch id is ``KYO-299.d1`` — it contains a
#: hyphen of its own, and a single-hyphen separator would make the split ambiguous.
FRAGMENT_SEPARATOR = "--"

#: KYO-299. The refusal reasons `kyo docs assemble` can produce, named so the command,
#: its tests and the documentation cannot list different sets (law 3).
FRAGMENT_NO_PLACEHOLDER_HEADING = "no-placeholder-heading"
FRAGMENT_UNKNOWN_DOCUMENT = "unknown-document"
FRAGMENT_PLACEHOLDER_REMAINS = "placeholder-remains"
#: KYO-486. A fragment is ONE section: it receives one number and every
#: :data:`FRAGMENT_PLACEHOLDER` in it becomes that number, so a second placeholder
#: heading in the same file would be assembled under the same number as the first.
FRAGMENT_SEVERAL_SECTIONS = "several-sections"
#: KYO-493: the amends refusals, one per way an ``amends:`` header can fail.
FRAGMENT_HEADER_UNREADABLE = "fragment-header-unreadable"
AMENDS_UNDECLARED = "amends-undeclared"
AMENDS_UNKNOWN_SECTION = "amends-unknown-section"
AMENDS_UNASSEMBLED_SECTION = "amends-unassembled-section"
AMENDS_AMBIGUOUS_SECTION = "amends-ambiguous-section"
AMENDS_NO_STATUS_LINE = "amends-no-status-line"
AMENDS_UNREADABLE_BACK_LINK = "amends-unreadable-back-link"
#: Every amends refusal, in a fixed order — the set
#: docs/reference/topics/operating.md ("Doc fragments"), the charter and
#: docs/design/docs.md name, and the set `kyo check-branch` runs minus the one a worker's
#: tree cannot decide (:data:`AMENDS_UNASSEMBLED_SECTION`).
AMENDS_REFUSALS: tuple[str, ...] = (
    FRAGMENT_HEADER_UNREADABLE,
    AMENDS_UNDECLARED,
    AMENDS_UNKNOWN_SECTION,
    AMENDS_UNASSEMBLED_SECTION,
    AMENDS_AMBIGUOUS_SECTION,
    AMENDS_NO_STATUS_LINE,
    AMENDS_UNREADABLE_BACK_LINK,
)


@dataclass(frozen=True)
class NumberedDocument:
    """One document that grows by numbered section, and everything that acts on it.

    Built by :func:`numbered_documents` from one ``documents.yml`` entry's ``numbered:``
    block plus the files that entry's globs enumerate — so a set naming two files yields
    two of these, sharing a fragments directory and a heading template and numbered
    independently of each other.
    """

    #: The ``documents.yml`` entry that declared it, for a refusal that has to say where
    #: the rule came from.
    set_name: str
    #: The document itself, root-relative and posix-style.
    path: str
    #: Where its fragments are written, root-relative and posix-style.
    fragments_dir: str
    #: The heading template, carrying :data:`kyosei.registry.HEADING_NUMBER` once.
    heading: str
    #: The project's own commands to run after sections are appended. NAMED, never run.
    after: tuple[str, ...] = ()
    #: Every document this same set enumerates, this one included. A fragment in a
    #: multi-document set has to say which one it is for; in a single-document set it
    #: must not have to.
    siblings: tuple[str, ...] = ()
    #: KYO-493: ``numbered.amends.field`` — the back-link key written when an amended
    #: section has none — or ``None`` when the set declares no ``amends:``.
    amends_field: str | None = None
    #: KYO-493: ``numbered.amends.status`` — the status an amended section is given.
    amends_status: str | None = None

    @property
    def ambiguous(self) -> bool:
        """Does a fragment here have to name its document?"""
        return len(self.siblings) > 1

    def fragment_path(self, dispatch_id: str, slug: str) -> str:
        """Where the worker of ``dispatch_id`` writes its new section."""
        return f"{self.fragments_dir}/{dispatch_id}{FRAGMENT_SEPARATOR}{slug}.md"


def numbered_documents(kyosei_dir: Path, code_root: Path) -> list[NumberedDocument]:
    """Every document this project declares as numbered and append-only (KYO-299.c1).

    ``kyosei_dir`` is the STORE's registry directory and ``code_root`` is where the
    documents live — two places under the split layout, for :func:`document_set`'s
    reason, and the two callers pass both because they are the two that know.

    A set whose globs match no file yet still yields a document when a glob NAMES one
    literally (no wildcard): a project that declares `apps/server/NOTES.md` before the
    file exists has declared a document, and `kyo docs assemble` creating it on the first
    run is better than a silence indistinguishable from "this project declares none".
    A wildcard that currently matches nothing yields nothing, because it names nothing.
    """
    registry = registry_module.load_document_registry(kyosei_dir)
    found: list[NumberedDocument] = []
    for name in registry.declared_names:
        entry = registry[name]
        declared = entry.get("numbered")
        if not isinstance(declared, dict):
            continue
        globs = [
            glob
            for glob in (entry.get(registry_module.ENUMERATE.field_name) or ())
            if isinstance(glob, str)
        ]
        paths = [
            path.relative_to(code_root).as_posix()
            for path in _iter_docs_files(code_root, globs)
        ]
        if not paths:
            paths = [glob for glob in globs if not _is_glob(glob)]
        siblings = tuple(dict.fromkeys(paths))
        after = declared.get("after") or []
        amends = declared.get("amends") if isinstance(declared.get("amends"), dict) else {}
        found += [
            NumberedDocument(
                set_name=name,
                path=path,
                fragments_dir=str(declared["fragments"]).strip("/"),
                heading=str(declared["heading"]),
                after=tuple(item for item in after if isinstance(item, str)),
                siblings=siblings,
                amends_field=str(amends["field"]) if amends.get("field") else None,
                amends_status=str(amends["status"]).strip() if amends.get("status") else None,
            )
            for path in siblings
        ]
    return found


def heading_pattern(template: str) -> re.Pattern[str]:
    """The template, as a pattern matching a REAL numbered heading, capturing the number.

    Built by splitting on :data:`kyosei.registry.HEADING_NUMBER` and escaping the two
    literal halves, rather than by escaping the whole string and substituting inside the
    result: ``re.escape`` escapes braces, so the placeholder would no longer be findable
    in what came back.

    Anchored at the start and open at the end. ``"## {number}."`` therefore matches
    ``## 204. APP-58: the mis-listing dashboard (2026-09-16)`` without the project having
    to describe its own titles to us — which is the point of declaring only as much of
    the heading as it takes to find the number.
    """
    before, _, after = template.partition(registry_module.HEADING_NUMBER)
    return re.compile(rf"^{re.escape(before)}(\d+){re.escape(after)}")


def placeholder_heading_pattern(template: str) -> re.Pattern[str]:
    """The same template with :data:`FRAGMENT_PLACEHOLDER` where the number goes — what
    a fragment's first heading must look like."""
    before, _, after = template.partition(registry_module.HEADING_NUMBER)
    return re.compile(rf"^{re.escape(before + FRAGMENT_PLACEHOLDER + after)}")


def numbered_headings(text: str, template: str) -> list[tuple[int, str]]:
    """Every ``(number, line)`` in ``text`` whose heading matches ``template``, in file
    order, ignoring anything inside a fenced code block.

    Fence-aware for :func:`_fenced`'s reason, sharpened here: a project's README
    explaining its own notes format quotes a numbered heading in a fence, and reading
    that as the document's last section would number the next one from an example.
    """
    pattern = heading_pattern(template)
    found: list[tuple[int, str]] = []
    for line, in_code in _fenced(text.splitlines()):
        if in_code:
            continue
        match = pattern.match(line)
        if match:
            found.append((int(match.group(1)), line))
    return found


def last_number(text: str, template: str) -> int | None:
    """The number of the document's LAST numbered heading, or ``None`` if it has none.

    Last in file order, not the maximum, and that is the criterion's own wording
    (KYO-299.c3) rather than an accident: these documents are append-only, so the last
    heading is the one the next section follows, and a document whose tail number is not
    its highest has a history a tool guessing with ``max()`` would paper over. A document
    with no numbered heading at all starts at 1.
    """
    found = numbered_headings(text, template)
    return found[-1][0] if found else None


@dataclass(frozen=True)
class Fragment:
    """One file a worker wrote under a fragments directory, read but not yet assembled."""

    #: Root-relative posix path of the fragment file itself.
    path: str
    #: The document it will be appended to.
    document: NumberedDocument
    #: The dispatch that wrote it, parsed from the file name, or ``None`` for a fragment
    #: written by hand or by a controller — which is a supported case and not an error.
    dispatch_id: str | None
    #: The fragment's content with any ``kyosei-fragment`` header line removed.
    body: str
    #: What its header line named, or ``None`` when it carried none.
    declared_document: str | None = None
    #: Did :func:`_attribute` place it? ``False`` means :attr:`document` is a stand-in
    #: for a refusal to name the set and its members, and nothing may be appended.
    attributed: bool = True
    #: KYO-493: the section numbers its header's ``amends:`` names, deduplicated, in
    #: the order written. Empty when it names none.
    amends: tuple[int, ...] = ()
    #: KYO-493: why its header line could not be read, or ``None``. A fragment carrying
    #: this is refused as :data:`FRAGMENT_HEADER_UNREADABLE` before anything else.
    header_problem: str | None = None
    #: The header line exactly as written, for that refusal to quote.
    header_line: str | None = None


def fragment_dispatch_id(file_name: str) -> str | None:
    """The dispatch a fragment's file name claims, or ``None``.

    ``KYO-299.d1--doc-fragments.md`` -> ``KYO-299.d1``. Validated against
    :data:`kyosei.ids.CHILD_RE` rather than against a second copy of the id shape, and
    required to be a *dispatch* child specifically, so a file called
    ``notes--something.md`` resolves to nothing rather than to a plausible-looking id.
    """
    stem = file_name[: -len(".md")] if file_name.endswith(".md") else file_name
    head, separator, _ = stem.partition(FRAGMENT_SEPARATOR)
    if not separator:
        return None
    match = ids.CHILD_RE.match(head)
    if match is None or match.group(2) != ids.CHILD_LETTER_BY_KIND["dispatch"]:
        return None
    return head


def read_fragments(code_root: Path, documents: Sequence[NumberedDocument]) -> list[Fragment]:
    """Every fragment file present, paired with the document it is for.

    One directory may serve a set of several documents, so the same file is read once and
    attributed by its own header line; :func:`assemble_plan` is what refuses one that
    cannot be attributed. Sorted by path, which is the tie-break the ordering rule falls
    back on.
    """
    by_dir: dict[str, list[NumberedDocument]] = {}
    for document in documents:
        by_dir.setdefault(document.fragments_dir, []).append(document)

    found: list[Fragment] = []
    for fragments_dir, members in sorted(by_dir.items()):
        directory = code_root / fragments_dir
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):  # pragma: no cover - unreadable file
                text = ""
            found.append(
                fragment_from_text(path.relative_to(code_root).as_posix(), text, members)
            )
    return found


def fragment_from_text(
    path: str, text: str, members: Sequence[NumberedDocument]
) -> Fragment:
    """One fragment, read from its text — the file on disk for ``kyo docs assemble``,
    the branch's blob for ``kyo check-branch`` (KYO-493), so the two read one grammar.

    ``members`` is every document sharing the fragment's directory.
    """
    header = _fragment_header(text)
    document = (
        None if header.problem is not None else _attribute(header.document, members)
    )
    return Fragment(
        path=path,
        # A stand-in when attribution failed, so the refusal below can name the set and
        # its members. Nothing reads it for an unattributed fragment except that message.
        document=document if document is not None else members[0],
        dispatch_id=fragment_dispatch_id(path.rsplit("/", 1)[-1]),
        body=header.body,
        declared_document=header.document,
        attributed=document is not None,
        amends=header.amends,
        header_problem=header.problem,
        header_line=header.line,
    )


@dataclass(frozen=True)
class _Header:
    """What a fragment's first non-blank line said, and the text without it."""

    body: str
    line: str | None = None
    document: str | None = None
    amends: tuple[int, ...] = ()
    problem: str | None = None


def _fragment_header(text: str) -> _Header:
    """Read the ``kyosei-fragment`` header, if the first non-blank line is one.

    A first line that does not OPEN ``<!-- kyosei-fragment`` is not a header and stays
    in the body, as before. One that does is either this grammar or a refusal (KYO-493):
    ``document: <path>`` and/or ``amends: <n>[, <n>...]`` (brackets allowed), each
    once, nothing else.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        stripped = line.strip()
        if not FRAGMENT_HEADER_OPEN_RE.match(stripped):
            return _Header(body=text)
        body = "\n".join(lines[:index] + lines[index + 1 :])
        document, amends, problem = _parse_header(stripped)
        return _Header(
            body=body, line=stripped, document=document, amends=amends, problem=problem
        )
    return _Header(body=text)


def _parse_header(line: str) -> tuple[str | None, tuple[int, ...], str | None]:
    """``(document, amends, problem)`` for one header line."""
    match = FRAGMENT_HEADER_RE.match(line)
    if match is None:
        return None, (), (
            "it opens `<!-- kyosei-fragment` but does not close with `-->` on the same line"
        )
    content = match.group(1).strip()
    keys = list(_HEADER_KEY_RE.finditer(content))
    if not keys:
        return None, (), "it names no key at all"
    if keys[0].start() != 0:
        stray = content[: keys[0].start()].strip()
        return None, (), f"it carries {stray!r} before its first key"
    values: dict[str, str] = {}
    for position, key in enumerate(keys):
        name = key.group(1)
        end = keys[position + 1].start() if position + 1 < len(keys) else len(content)
        value = content[key.end() : end].strip()
        if name not in FRAGMENT_HEADER_KEYS:
            return None, (), f"it carries the unknown key {name!r}"
        if name in values:
            return None, (), f"it carries the key {name!r} twice"
        values[name] = value
    document = values.get("document")
    if document is not None and (not document or len(document.split()) != 1):
        return None, (), f"its document: value {document!r} is not one path"
    amends: list[int] = []
    if "amends" in values:
        raw = values["amends"]
        inner = raw[1:-1] if raw.startswith("[") and raw.endswith("]") else raw
        items = [item.strip() for item in inner.split(",")]
        if not any(items):
            return None, (), "its amends: value names no section"
        for item in items:
            if not re.fullmatch(r"\d+", item):
                lettered = re.fullmatch(r"(\d+)[A-Za-z]+", item)
                if lettered:
                    return None, (), (
                        f"its amends: value carries {item!r}, a lettered anchor; amends "
                        f"names numbered sections only — name its parent section "
                        f"{lettered.group(1)}"
                    )
                return None, (), (
                    f"its amends: value carries {item!r}, which is not a section number"
                )
            if int(item) not in amends:
                amends.append(int(item))
    return document, tuple(amends), None


def _attribute(named: str | None, members: Sequence[NumberedDocument]) -> NumberedDocument | None:
    """Which of a fragments directory's documents a fragment is for."""
    if named is not None:
        wanted = named.strip("/")
        return next((document for document in members if document.path == wanted), None)
    return members[0] if len(members) == 1 else None


@dataclass(frozen=True)
class FragmentRefusal:
    """One fragment that cannot be assembled, and what clears it (KYO-299.c4)."""

    fragment: str
    reason: str
    says: str
    remedy: str

    def line(self) -> str:
        return f"{self.fragment}: {self.says}\n      remedy: {self.remedy}"


@dataclass(frozen=True)
class Assignment:
    """One fragment, the number it becomes, and the text that will be appended."""

    fragment: str
    doc_path: str
    number: int
    dispatch_id: str | None
    #: Did the store place this fragment in dispatch-creation order, or was it ordered
    #: after the ones it could place? Printed, never inferred from ``dispatch_id``:
    #: a fragment can carry a well-formed id this store has never heard of.
    ordered_by_dispatch: bool
    heading: str


@dataclass(frozen=True)
class Amendment:
    """One earlier section's write-back: the back-link it gains and the status it is
    given (KYO-493). One per SECTION, however many fragments in the run amend it."""

    doc_path: str
    number: int
    heading: str
    #: The new section numbers written onto the back-link, in assembly order.
    by: tuple[int, ...]
    #: The fragments those numbers came from, in the same order.
    fragments: tuple[str, ...]
    #: The back-link key as it is written — the line's own spelling when it already
    #: existed, the declared one when it is inserted.
    field: str
    #: The back-link's value before (``None``: there was no line) and after.
    field_before: str | None
    field_after: str
    status_before: str
    status_after: str
    #: Why the status was not changed even though it differs from the declared value —
    #: set only for a dead section (``docs_parse.section_is_dead``).
    status_kept_because: str | None = None

    def as_json(self) -> dict:
        return {
            "doc_path": self.doc_path,
            "section": {"number": self.number, "heading": self.heading},
            "by": list(self.by),
            "fragments": list(self.fragments),
            "field": self.field,
            "field_before": self.field_before,
            "field_after": self.field_after,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "status_kept_because": self.status_kept_because,
        }

    def line(self) -> str:
        """The dry-run / apply line: section, field before -> after, status."""
        before = self.field_before if self.field_before is not None else "(no line)"
        if self.status_kept_because:
            status = f"status: {self.status_before} (kept: {self.status_kept_because})"
        elif self.status_before == self.status_after:
            status = f"status: {self.status_after} (unchanged)"
        else:
            status = f"status: {self.status_before} -> {self.status_after}"
        return (
            f"amends §{self.number}   {self.field}: {before} -> {self.field_after}   {status}"
        )


@dataclass(frozen=True)
class AssemblePlan:
    """What `kyo docs assemble` would do, computed in full before anything is written."""

    assignments: tuple[Assignment, ...] = ()
    refusals: tuple[FragmentRefusal, ...] = ()
    #: ``{document path: the whole new text}``. Empty when anything was refused — the
    #: plan is either wholly applicable or wholly inert, which is what makes "it writes
    #: nothing at all if any fragment is refused" true by construction rather than by a
    #: caller remembering to check.
    new_text: Mapping[str, str] = MappingProxyType({})
    #: The fragment files the apply step removes.
    removes: tuple[str, ...] = ()
    #: ``{document path: its declared follow-up commands}`` — named, never run.
    follow_up: Mapping[str, tuple[str, ...]] = MappingProxyType({})
    #: Every declared numbered document, so a run that assembles nothing can still say
    #: what it looked at rather than printing a bare "nothing to do".
    documents: tuple[NumberedDocument, ...] = ()
    #: KYO-493: every earlier section a fragment's ``amends:`` header names, and what
    #: is written onto it. Already folded into :attr:`new_text`; carried for the output.
    amendments: tuple[Amendment, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.refusals


def assemble_plan(
    kyosei_dir: Path,
    code_root: Path,
    *,
    #: ``{dispatch id: created_at}``, from the store. ``None`` when no store was
    #: consulted at all, which is a different fact from "the store knows none of these"
    #: and is why this is not merely an empty mapping.
    created_at: Mapping[str, str] | None = None,
) -> AssemblePlan:
    """The whole act, decided before a byte is written (KYO-299.c3, c4).

    ORDER. Fragments whose dispatch this store can date are taken in dispatch-creation
    order; the rest — written by hand, written by a controller, or naming a dispatch this
    store has never heard of — follow, ordered by file name. That is a decision and it is
    the conservative one: a hand-written fragment has no place in a sequence derived from
    the board, and putting it last means its number never depends on when somebody
    happened to name the file. Each assignment records which of the two it got, and the
    command prints it, because "your fragment was ordered after the others" is a fact the
    reader would otherwise have to infer from a number.

    REFUSAL. Every fragment is checked and every problem is collected before anything is
    written, and one refusal voids the whole run — including the documents that had no
    problem. Partial assembly is the one outcome this must not have: the numbers are
    consumed, the fragments are deleted, and the run that "half worked" leaves a
    controller reconstructing which half.
    """
    documents = numbered_documents(kyosei_dir, code_root)
    fragments = read_fragments(code_root, documents)
    if not documents or not fragments:
        return AssemblePlan(documents=tuple(documents))

    refusals: list[FragmentRefusal] = []
    usable: list[Fragment] = []
    for fragment in fragments:
        early = header_refusals(fragment)
        if early:
            refusals += early
            continue
        if not fragment.attributed:
            refusals.append(_unknown_document_refusal(fragment, fragment.declared_document or ""))
            continue
        pattern = placeholder_heading_pattern(fragment.document.heading)
        if not any(pattern.match(line) for line in fragment.body.splitlines()):
            refusals.append(_no_placeholder_refusal(fragment))
            continue
        # KYO-486: one number per fragment, so one section per fragment. Counted outside
        # code fences, the way :func:`numbered_headings` will read the assembled
        # document: a fenced example of the format is not a second section.
        headings = [
            line
            for line, in_code in _fenced(fragment.body.splitlines())
            if not in_code and pattern.match(line)
        ]
        if len(headings) > 1:
            refusals.append(_several_sections_refusal(fragment, headings))
            continue
        usable.append(fragment)

    def sort_key(fragment: Fragment) -> tuple[int, str, str]:
        dated = (
            created_at.get(fragment.dispatch_id or "")
            if created_at is not None and fragment.dispatch_id
            else None
        )
        return (0, dated, fragment.path) if dated else (1, "", fragment.path)

    assignments: list[Assignment] = []
    texts: dict[str, str] = {}
    #: KYO-493: each document as it is on disk when the command starts — what an
    #: ``amends:`` number refers to, and the prefix the write-backs are folded into.
    pre_run: dict[str, str] = {}
    ordered = sorted(usable, key=sort_key)
    for fragment in ordered:
        document = fragment.document
        existing = texts.get(document.path)
        if existing is None:
            source = code_root / document.path
            existing = source.read_text(encoding="utf-8") if source.is_file() else ""
            pre_run[document.path] = existing
        number = (last_number(existing, document.heading) or 0) + 1
        section = fragment.body.replace(FRAGMENT_PLACEHOLDER, str(number)).strip("\n")
        texts[document.path] = (
            f"{existing.rstrip(chr(10))}\n\n{section}\n" if existing.strip() else f"{section}\n"
        )
        heading = next(
            (
                line
                for line in section.splitlines()
                if heading_pattern(document.heading).match(line)
            ),
            "",
        )
        assignments.append(
            Assignment(
                fragment=fragment.path,
                doc_path=document.path,
                number=number,
                dispatch_id=fragment.dispatch_id,
                ordered_by_dispatch=sort_key(fragment)[0] == 0,
                heading=heading,
            )
        )

    for doc_path, text in texts.items():
        if FRAGMENT_PLACEHOLDER in text:
            refusals.append(_placeholder_remains_refusal(doc_path, text))

    # KYO-493: the write-backs, decided on the pre-run text and refused as a whole.
    numbered_by = {
        (assignment.doc_path, assignment.number): assignment.fragment
        for assignment in assignments
    }
    number_of = {assignment.fragment: assignment.number for assignment in assignments}
    targets: dict[tuple[str, int], list[tuple[AmendTarget, int, str]]] = {}
    for fragment in ordered:
        if not fragment.amends:
            continue
        document = fragment.document
        pre_text = pre_run.get(document.path, "")
        located, beyond, problems = amend_targets(fragment, pre_text)
        refusals += problems
        for number in beyond:
            refusals.append(
                _unassembled_refusal(
                    fragment, number, last_number(pre_text, document.heading),
                    numbered_by.get((document.path, number)),
                )
            )
        for target in located:
            targets.setdefault((document.path, target.number), []).append(
                (target, number_of[fragment.path], fragment.path)
            )
    amendments: list[Amendment] = []
    for doc_path in list(texts):
        mine = {key: value for key, value in targets.items() if key[0] == doc_path}
        if not mine or refusals:
            continue
        document = next(d for d in documents if d.path == doc_path)
        pre_text = pre_run[doc_path]
        amended, made = _apply_amends(pre_text, document, mine)
        amendments += made
        texts[doc_path] = amended.rstrip("\n") + texts[doc_path][len(pre_text.rstrip("\n")) :]

    if refusals:
        # KYO-299.c3: nothing is written if anything is refused, so the applicable half
        # of the plan is not merely unused — it is not carried out of here at all.
        return AssemblePlan(
            refusals=tuple(refusals),
            documents=tuple(documents),
        )
    return AssemblePlan(
        assignments=tuple(assignments),
        amendments=tuple(amendments),
        new_text=MappingProxyType(dict(texts)),
        removes=tuple(assignment.fragment for assignment in assignments),
        follow_up=MappingProxyType(
            {
                document.path: document.after
                for document in documents
                if document.path in texts and document.after
            }
        ),
        documents=tuple(documents),
    )


# ==================================================================== KYO-493
#
# AMENDS: a fragment's header names earlier sections of its own document that it
# amends, and `kyo docs assemble` — the one party that knows the new number — writes
# that number onto each one's back-link and sets its status, on the merged tree, in the
# same all-or-nothing plan.
#
# The project declares the spelling (`numbered.amends: {field, status}`); Kyosei owns
# the relation (charter Layer 3's `status` / `amended-by`) and the act.

_STATUS_LINE_RE = re.compile(r"^(\s*status:[ \t]*)(.*?)\s*$", re.IGNORECASE)
_BACK_LINK_RE = re.compile(r"^(\s*(amended[-_]by):[ \t]*)(.*?)\s*$", re.IGNORECASE)
_FLOW_LIST_RE = re.compile(r"^\[\s*(\d+(?:\s*,\s*\d+)*)?\s*\]$")


@dataclass(frozen=True)
class AmendTarget:
    """One earlier section an ``amends:`` header names, located and found writable."""

    number: int
    heading: str
    #: Line indices into the pre-run text split on newlines.
    status_index: int
    back_link_index: int | None
    #: The back-link's numbers before this run, when it has a line.
    back_link: tuple[int, ...]
    dead: bool


def _sections(lines: Sequence[str], template: str) -> list[tuple[int, int, int]]:
    """``(number, heading index, end index)`` per numbered section, fence-aware: a
    section is its heading through the line before the next template-matching heading."""
    pattern = heading_pattern(template)
    starts = [
        (int(match.group(1)), index)
        for index, (line, in_code) in enumerate(_fenced(lines))
        if not in_code and (match := pattern.match(line))
    ]
    return [
        (number, start, starts[position + 1][1] if position + 1 < len(starts) else len(lines))
        for position, (number, start) in enumerate(starts)
    ]


def header_refusals(fragment: Fragment) -> list[FragmentRefusal]:
    """The two refusals decidable from the fragment alone: an unreadable header, and an
    ``amends:`` on a document whose set declares none."""
    if fragment.header_problem is not None:
        return [_header_unreadable_refusal(fragment)]
    if fragment.amends and fragment.attributed and fragment.document.amends_field is None:
        return [_undeclared_refusal(fragment)]
    return []


def amend_targets(
    fragment: Fragment, pre_text: str
) -> tuple[list[AmendTarget], list[int], list[FragmentRefusal]]:
    """``(located, beyond, refusals)`` for a fragment's ``amends:`` numbers against
    ``pre_text``, the document before this run.

    ``beyond`` are the numbers above the pre-run last section: ``kyo docs assemble``
    refuses each as :data:`AMENDS_UNASSEMBLED_SECTION`; ``kyo check-branch`` cannot
    decide them on a worker's tree and lets them pass. Shared by both so the worker and
    the controller are refused for the same reasons in the same words.
    """
    document = fragment.document
    lines = pre_text.split("\n")
    sections = _sections(lines, document.heading)
    last = sections[-1][0] if sections else 0
    located: list[AmendTarget] = []
    beyond: list[int] = []
    refusals: list[FragmentRefusal] = []
    from .docs_parse import section_is_dead, section_metadata

    for number in fragment.amends:
        if number > last:
            beyond.append(number)
            continue
        matching = [section for section in sections if section[0] == number]
        if not matching:
            refusals.append(_unknown_section_refusal(fragment, number, last))
            continue
        if len(matching) > 1:
            refusals.append(_ambiguous_refusal(fragment, number, [lines[s[1]] for s in matching]))
            continue
        _, start, end = matching[0]
        heading = lines[start]
        status_index: int | None = None
        back_links: list[int] = []
        for offset, (line, in_code) in enumerate(_fenced(lines[start + 1 : end])):
            if in_code:
                continue
            index = start + 1 + offset
            status = _STATUS_LINE_RE.match(line)
            if status_index is None and status and status.group(2):
                status_index = index
            if _BACK_LINK_RE.match(line):
                back_links.append(index)
        if status_index is None:
            refusals.append(_no_status_refusal(fragment, number, heading))
            continue
        if len(back_links) > 1:
            refusals.append(
                _unreadable_back_link_refusal(
                    fragment, number, heading,
                    f"the back-link key appears {len(back_links)} times in it "
                    f"({'; '.join(repr(lines[i].strip()) for i in back_links)})",
                )
            )
            continue
        numbers: tuple[int, ...] = ()
        if back_links:
            value = _BACK_LINK_RE.match(lines[back_links[0]]).group(3)
            flow = _FLOW_LIST_RE.match(value)
            if flow is None:
                refusals.append(
                    _unreadable_back_link_refusal(
                        fragment, number, heading,
                        f"its back-link line {lines[back_links[0]].strip()!r} is not a "
                        "flow list of section numbers such as [188, 249]",
                    )
                )
                continue
            numbers = tuple(int(item) for item in re.findall(r"\d+", flow.group(1) or ""))
        located.append(
            AmendTarget(
                number=number,
                heading=heading,
                status_index=status_index,
                back_link_index=back_links[0] if back_links else None,
                back_link=numbers,
                dead=section_is_dead(section_metadata(lines[start + 1 : end])),
            )
        )
    return located, beyond, refusals


def _apply_amends(
    pre_text: str,
    document: NumberedDocument,
    targets: Mapping[tuple[str, int], Sequence[tuple[AmendTarget, int, str]]],
) -> tuple[str, list[Amendment]]:
    """``pre_text`` with every write-back made, and one :class:`Amendment` per section."""
    lines = pre_text.split("\n")
    made: list[Amendment] = []
    inserts: list[tuple[int, str]] = []
    declared_status = document.amends_status or ""
    for (doc_path, number), entries in sorted(targets.items(), key=lambda item: item[0][1]):
        target = entries[0][0]
        by: list[int] = []
        fragments: list[str] = []
        for _, new_number, fragment in entries:
            if new_number not in by:
                by.append(new_number)
                fragments.append(fragment)
        after_numbers = [*target.back_link, *(n for n in by if n not in target.back_link)]
        field_after = f"[{', '.join(str(n) for n in after_numbers)}]"
        status_match = _STATUS_LINE_RE.match(lines[target.status_index])
        status_before = status_match.group(2)
        if target.back_link_index is not None:
            match = _BACK_LINK_RE.match(lines[target.back_link_index])
            field, field_before = match.group(2), match.group(3)
            lines[target.back_link_index] = f"{match.group(1)}{field_after}"
        else:
            field, field_before = str(document.amends_field), None
            indent = re.match(r"^\s*", lines[target.status_index]).group(0)
            inserts.append((target.status_index + 1, f"{indent}{field}: {field_after}"))
        kept = None
        status_after = status_before
        if target.dead:
            kept = (
                "the section is dead (its status or superseded-by retires it), and an "
                "amendment does not revive it"
            )
        elif status_before != declared_status:
            status_after = declared_status
            lines[target.status_index] = f"{status_match.group(1)}{declared_status}"
        made.append(
            Amendment(
                doc_path=doc_path,
                number=number,
                heading=target.heading,
                by=tuple(by),
                fragments=tuple(fragments),
                field=field,
                field_before=field_before,
                field_after=field_after,
                status_before=status_before,
                status_after=status_after,
                status_kept_because=kept,
            )
        )
    for index, line in sorted(inserts, reverse=True):
        lines.insert(index, line)
    return "\n".join(lines), made


def _header_unreadable_refusal(fragment: Fragment) -> FragmentRefusal:
    return FragmentRefusal(
        fragment=fragment.path,
        reason=FRAGMENT_HEADER_UNREADABLE,
        says=(
            f"its first line {fragment.header_line!r} opens a kyosei-fragment header, "
            f"but {fragment.header_problem}. Left as it is, it would be appended to the "
            "document as an invisible comment and what it asks for would go unwritten"
        ),
        remedy=(
            "write the header as `<!-- kyosei-fragment amends: 178, 186 -->` (section "
            "numbers of this fragment's own document, comma-separated, brackets allowed) "
            "and/or `document: <path>` — each key once, no other key — or delete the line"
        ),
    )


def _undeclared_refusal(fragment: Fragment) -> FragmentRefusal:
    document = fragment.document
    return FragmentRefusal(
        fragment=fragment.path,
        reason=AMENDS_UNDECLARED,
        says=(
            f"its header amends section(s) {', '.join(str(n) for n in fragment.amends)} "
            f"of {document.path}, but the {document.set_name} set declares no "
            "`numbered.amends:`, so there is no declared back-link or status to write"
        ),
        remedy=(
            f"declare `amends: {{field: amended_by, status: amended}}` under "
            f"{document.set_name}'s `numbered:` in kyosei/documents.yml (field: "
            "amended-by or amended_by), or remove `amends:` from the fragment's header"
        ),
    )


def _unknown_section_refusal(fragment: Fragment, number: int, last: int) -> FragmentRefusal:
    path = fragment.document.path
    return FragmentRefusal(
        fragment=fragment.path,
        reason=AMENDS_UNKNOWN_SECTION,
        says=(
            f"its header amends section {number}, and no section of {path} carries that "
            f"number (its last is {last})"
        ),
        remedy=f"correct the number in the fragment's `amends:` header to a section {path} has",
    )


def _unassembled_refusal(
    fragment: Fragment, number: int, last: int | None, numbered_now: str | None
) -> FragmentRefusal:
    path = fragment.document.path
    this_run = (
        f"; this run would give {number} to {numbered_now}"
        if numbered_now
        else ""
    )
    return FragmentRefusal(
        fragment=fragment.path,
        reason=AMENDS_UNASSEMBLED_SECTION,
        says=(
            f"its header amends section {number}, above {path}'s last section before "
            f"this run ({last if last is not None else 'none'}){this_run}. A section that "
            "did not exist when the fragment was written cannot be what it amends, and "
            "resolving it would make the meaning depend on assembly order"
        ),
        remedy=(
            "move this fragment out of the fragments directory, run `kyo docs assemble`, "
            "put it back with the number the other section received in its `amends:` "
            "header, and run `kyo docs assemble` again"
        ),
    )


def _ambiguous_refusal(fragment: Fragment, number: int, headings: Sequence[str]) -> FragmentRefusal:
    path = fragment.document.path
    return FragmentRefusal(
        fragment=fragment.path,
        reason=AMENDS_AMBIGUOUS_SECTION,
        says=(
            f"its header amends section {number}, and {len(headings)} sections of {path} "
            f"carry that number ({'; '.join(repr(h.strip()) for h in headings)}) — a "
            "history the tool must not choose between"
        ),
        remedy=(
            f"renumber the duplicate section {number} in {path} so one section carries it, "
            "then run `kyo docs assemble` again"
        ),
    )


def _no_status_refusal(fragment: Fragment, number: int, heading: str) -> FragmentRefusal:
    return FragmentRefusal(
        fragment=fragment.path,
        reason=AMENDS_NO_STATUS_LINE,
        says=(
            f"its header amends section {number} ({heading.strip()!r}), which has no "
            "`status:` line — the field written, and where the back-link goes. Kyosei "
            "does not invent where a project's metadata lives"
        ),
        remedy=(
            f"give section {number} a `status:` line in its metadata block, or remove "
            f"{number} from the fragment's `amends:` header"
        ),
    )


def _unreadable_back_link_refusal(
    fragment: Fragment, number: int, heading: str, why: str
) -> FragmentRefusal:
    return FragmentRefusal(
        fragment=fragment.path,
        reason=AMENDS_UNREADABLE_BACK_LINK,
        says=f"its header amends section {number} ({heading.strip()!r}), and {why}",
        remedy=(
            f"make section {number}'s back-link one line holding a flow list of section "
            "numbers (`amended_by: [188, 249]`, or `[]`), then run `kyo docs assemble` again"
        ),
    )


def _no_placeholder_refusal(fragment: Fragment) -> FragmentRefusal:
    document = fragment.document
    example = document.heading.replace(registry_module.HEADING_NUMBER, FRAGMENT_PLACEHOLDER)
    return FragmentRefusal(
        fragment=fragment.path,
        reason=FRAGMENT_NO_PLACEHOLDER_HEADING,
        says=(
            f"no line in it matches {document.path}'s heading template with the "
            f"placeholder in it, so there is nowhere to write the number. "
            f"{document.set_name} declares `heading: {document.heading}`"
        ),
        remedy=(
            f"head the section `{example} <title>` — {FRAGMENT_PLACEHOLDER} exactly as "
            "written, and use it anywhere else in the section that cites this section's "
            "own number; `kyo docs assemble` replaces every occurrence"
        ),
    )


def _several_sections_refusal(fragment: Fragment, headings: Sequence[str]) -> FragmentRefusal:
    document = fragment.document
    directory = document.fragments_dir
    return FragmentRefusal(
        fragment=fragment.path,
        reason=FRAGMENT_SEVERAL_SECTIONS,
        says=(
            f"it carries {len(headings)} headings matching {document.path}'s placeholder "
            f"heading ({', '.join(repr(h.strip()) for h in headings)}), and a fragment "
            f"receives ONE number — every {FRAGMENT_PLACEHOLDER} in it would become that "
            "same number, so these sections would be assembled under one number"
        ),
        remedy=(
            "split it into one file per section, each "
            f"`{directory}/<dispatch-id>--<slug>.md` with a distinct slug and one "
            f"`{FRAGMENT_PLACEHOLDER}` heading; one dispatch's files are numbered in "
            "file-name order"
        ),
    )


def _unknown_document_refusal(fragment: Fragment, named: str) -> FragmentRefusal:
    document = fragment.document
    known = ", ".join(document.siblings) or "(none)"
    if named:
        says = (
            f"its header names {named!r}, which is not one of the documents the "
            f"{document.set_name} set enumerates: {known}"
        )
        remedy = (
            f"correct the header to `<!--kyosei-fragment document: <path> -->` naming "
            f"one of {known}, or declare {named!r} in kyosei/documents.yml with its own "
            "`numbered:` block"
        )
    else:
        says = (
            f"the {document.set_name} set enumerates {len(document.siblings)} documents "
            f"({known}) and this fragment names none, so which one it belongs to is not "
            "decidable"
        )
        remedy = (
            "add `<!--kyosei-fragment document: <path> -->` as the fragment's first "
            f"line, naming one of {known}"
        )
    return FragmentRefusal(
        fragment=fragment.path,
        reason=FRAGMENT_UNKNOWN_DOCUMENT,
        says=says,
        remedy=remedy,
    )


def _placeholder_remains_refusal(doc_path: str, text: str) -> FragmentRefusal:
    left = [
        line.strip()
        for line in text.splitlines()
        if FRAGMENT_PLACEHOLDER in line
    ]
    return FragmentRefusal(
        fragment=doc_path,
        reason=FRAGMENT_PLACEHOLDER_REMAINS,
        says=(
            f"{doc_path} would still carry {FRAGMENT_PLACEHOLDER} after this assembly, "
            f"on {len(left)} line(s), the first being {left[0]!r} — the placeholder means "
            "'the number of the section being assembled', and one left in a document "
            "means a number nobody will ever write"
        ),
        remedy=(
            f"open {doc_path}, replace each remaining {FRAGMENT_PLACEHOLDER} with the "
            "number of the section it refers to, commit that, and run `kyo docs "
            "assemble` again"
        ),
    )


def apply_assembly(code_root: Path, plan: AssemblePlan) -> None:
    """Write the plan. Refuses to write a plan that carries a refusal (KYO-299.c3)."""
    if not plan.ok:
        raise ValueError("a plan carrying refusals is never applied")
    # KYO-407.c1: each document is replaced atomically, so an assembly interrupted
    # part-way leaves every document either as it was or as assembled, never truncated.
    from .export import atomic_write_text

    for doc_path, text in plan.new_text.items():
        atomic_write_text(code_root / doc_path, text)
    for fragment in plan.removes:
        (code_root / fragment).unlink(missing_ok=True)


def added_numbered_headings(
    before_text: str | None, after_text: str | None, template: str
) -> list[str]:
    """Numbered headings this branch ADDED to a document (KYO-299.c5).

    Compared as heading text present after and absent before, rather than by reading the
    diff's line numbers: a branch that also edits an existing section shifts every later
    heading's line number without adding one, and a line-based answer would refuse it.
    That distinction is the criterion — editing existing sections must stay an ordinary
    edit — so it is made by the comparison rather than downstream of it.

    A side this cannot read (a document added by the branch, an unreadable blob) is
    treated as empty, which makes a whole new numbered document read as every one of its
    headings added. That is the right direction for a guard: a worker creating the
    project's numbered document from scratch is exactly the act that must go past a
    controller.
    """
    before = {line for _, line in numbered_headings(before_text or "", template)}
    return [line for _, line in numbered_headings(after_text or "", template) if line not in before]


__all__ = [
    "AMENDS_AMBIGUOUS_SECTION",
    "AMENDS_NO_STATUS_LINE",
    "AMENDS_REFUSALS",
    "AMENDS_UNASSEMBLED_SECTION",
    "AMENDS_UNDECLARED",
    "AMENDS_UNKNOWN_SECTION",
    "AMENDS_UNREADABLE_BACK_LINK",
    "FRAGMENT_HEADER_UNREADABLE",
    "AmendTarget",
    "Amendment",
    "amend_targets",
    "fragment_from_text",
    "header_refusals",
    "FRAGMENT_NO_PLACEHOLDER_HEADING",
    "FRAGMENT_PLACEHOLDER",
    "FRAGMENT_PLACEHOLDER_REMAINS",
    "FRAGMENT_SEVERAL_SECTIONS",
    "FRAGMENT_SEPARATOR",
    "FRAGMENT_UNKNOWN_DOCUMENT",
    "AssemblePlan",
    "Assignment",
    "Fragment",
    "FragmentRefusal",
    "NumberedDocument",
    "added_numbered_headings",
    "apply_assembly",
    "assemble_plan",
    "fragment_dispatch_id",
    "heading_pattern",
    "last_number",
    "numbered_documents",
    "numbered_headings",
    "placeholder_heading_pattern",
    "read_fragments",
    "match_docs_verdicts",
    "unmatched_verdict_line",
]
