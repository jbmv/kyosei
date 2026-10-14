"""``ssot:`` document sets: ids, their one owner, and tombstones (KYO-552, slice 1 of
KYO-551).

A set declaring ``ssot:`` in ``kyosei/documents.yml`` (validated by
:func:`kyosei.registry.ssot_block`, read into :class:`kyosei.docs_parse.SsotDeclaration`)
is a set of current-state documents whose requirements carry ids. The owner of an id is
the section whose heading carries it under the set's ``id_heading`` template, at any
heading level; nothing else declares ownership (charter law 3: derived, never stored).

A *bare id* is a token matching ``id_pattern`` as a whole word whose prefix (the part
before its ``-``) is a key of ``prefixes``. Every other token is ignored, which is what
keeps ``UTF-8``, ``SHA-256`` and ``KYO-12`` out of it. Ids are unique across every
document of every set declaring ``ssot:``, and resolve across all of them:

* in ``superseded-by:``/``amended-by:`` — :class:`SsotIndex` is handed to
  :class:`kyosei.docs_check._Corpus`, so the existing link rules
  (``section-link-unresolved``, ``section-link-to-itself``,
  ``section-link-not-cited-back``) apply through the resolution rather than beside it;
* in prose — :func:`ssot_problems`, the rules named in :data:`SSOT_REFUSALS`.

A set declaring ``ssot:`` with none of the id keys (KYO-553, ``ssot: {}``) carries no
ids: it is not indexed, no token in it is a bare id, its own ``superseded-by:`` values
resolve as a non-ssot document's do, and of the refusals only ``ssot-tombstone-body``
applies to it.

The rules run inside :func:`kyosei.docs_check.section_status_problems`, so ``kyo docs
check`` and ``kyo check-branch`` run the same ones. Resolution reads every ssot document;
the refusals are reported for the documents the caller asked about.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .docs_check import (
    SectionStatusProblem,
    _Corpus,
    _Located,
    _mentions,
    _read_under,
    numbered_headings,
    opted_documents,
)
from .docs_parse import (
    _BARE_METADATA_RE,
    _LIST_ITEM_RE,
    _METADATA_RE,
    DocumentSet,
    Section,
    SsotDeclaration,
    _fenced,
    _uncommented,
    link_values,
)
from .registry import SSOT_ID, SSOT_TITLE

#: A heading carries an id whose prefix is not a key of the set's ``prefixes``.
SSOT_PREFIX_UNDECLARED = "ssot-prefix-undeclared"
#: A heading owns an id in a document other than the one its prefix maps to.
SSOT_ID_WRONG_DOCUMENT = "ssot-id-wrong-document"
#: A second heading, anywhere across the ssot sets, owns an id already owned.
SSOT_ID_DUPLICATE = "ssot-id-duplicate"
#: A bare id in prose with a declared prefix and no owner.
SSOT_ID_UNRESOLVED = "ssot-id-unresolved"
#: A bare id in prose naming a superseded section, in a paragraph that does not also name
#: the head of its ``superseded-by`` chain.
SSOT_CITES_TOMBSTONE = "ssot-cites-tombstone"
#: A superseded section with more than ``tombstone_max_lines`` non-blank lines after its
#: metadata lines.
SSOT_TOMBSTONE_BODY = "ssot-tombstone-body"

#: Every ssot refusal, in KYO-552's criteria order — the list
#: docs/reference/topics/docs-governance.md names (law 3).
SSOT_REFUSALS: tuple[str, ...] = (
    SSOT_PREFIX_UNDECLARED,
    SSOT_ID_WRONG_DOCUMENT,
    SSOT_ID_DUPLICATE,
    SSOT_ID_UNRESOLVED,
    SSOT_CITES_TOMBSTONE,
    SSOT_TOMBSTONE_BODY,
)

#: A metadata line in an ssot section's opening block: any ``key: value`` or bare
#: ``key:`` line, not only :data:`kyosei.docs_parse.SECTION_METADATA_KEYS` — an ssot
#: section's metadata carries the project's own fields too (``priority: P0``,
#: ``area: billing``). A colon followed by text that is not whitespace (``http://``)
#: is not one.
_KEY_LINE_RE = re.compile(r"^[A-Za-z][\w-]*:(?:\s.*)?$")


def _rendered(declaration: SsotDeclaration, identifier: str) -> str:
    template = declaration.id_heading or ""
    return template.replace(SSOT_ID, identifier).replace(SSOT_TITLE, "<title>")


def _superseded(section: Section) -> bool:
    """A tombstone: status ``superseded``, or a ``superseded-by`` naming something."""
    status = section.metadata.get("status")
    if isinstance(status, str) and status.strip().lower() == "superseded":
        return True
    return bool(link_values(section.metadata.get("superseded-by")))


def metadata_lines(body: Sequence[str]) -> set[int]:
    """The indexes of ``body``'s opening metadata lines: after any blank lines, the run of
    ``key: value`` lines (with a bare key's ``- item`` list), or an HTML comment holding
    them, up to the first blank or prose line."""
    marked: set[int] = set()
    started = False
    in_comment = False
    in_list = False
    for index, raw in enumerate(body):
        stripped = raw.strip()
        if in_comment:
            marked.add(index)
            if stripped.endswith("-->"):
                in_comment = False
            continue
        if not stripped:
            if started:
                break
            continue
        if stripped.startswith("<!--") and not stripped.endswith("-->"):
            marked.add(index)
            started = in_comment = True
            continue
        if in_list and _LIST_ITEM_RE.match(raw):
            marked.add(index)
            continue
        text = _uncommented(raw)
        if _KEY_LINE_RE.match(text):
            marked.add(index)
            started = True
            in_list = not text.split(":", 1)[1].strip()
            continue
        break
    return marked


def _prose_paragraphs(section: Section) -> list[list[str]]:
    """A section's own prose as paragraphs: outside fenced code, outside its opening
    metadata lines and any other metadata line, split at blank lines and fences."""
    skipped = metadata_lines(section.body)
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for index, (line, in_code) in enumerate(_fenced(section.body)):
        text = _uncommented(line)
        if (
            in_code
            or not line.strip()
            or index in skipped
            or _METADATA_RE.match(text)
            or _BARE_METADATA_RE.match(text)
        ):
            if current:
                paragraphs.append(current)
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(current)
    return paragraphs


class SsotIndex:
    """Every id owned by a heading in an ssot document, read once (KYO-552.c2).

    ``declarations`` maps each document of every set declaring ``ssot:`` to that set's
    declaration. Built over all of them, whatever the caller reports on, because an id
    resolves repo-wide. A document whose set declares no ids (KYO-553) is skipped: it
    owns nothing, and its tokens are never ids.
    """

    def __init__(self, corpus: _Corpus, declarations: Mapping[str, SsotDeclaration]) -> None:
        self._corpus = corpus
        self._declarations = dict(declarations)
        self._owners: dict[str, list[_Located]] = {}
        self._ids: dict[tuple[str, int], str] = {}
        #: The heading refusals (c2), found while indexing.
        self.heading_problems: list[SectionStatusProblem] = []
        for doc_path in sorted(self._declarations):
            if self._declarations[doc_path].has_ids:
                self._read(doc_path, self._declarations[doc_path])
        for identifier, owners in self._owners.items():
            first = owners[0]
            for other in owners[1:]:
                self.heading_problems.append(
                    SectionStatusProblem(
                        other.doc_path,
                        other.section.heading,
                        SSOT_ID_DUPLICATE,
                        f"{identifier} is owned by this heading in {other.doc_path} and by "
                        f"{first.doc_path} [{first.section.heading}]",
                        "give one of them a new id (an id is never reused or renumbered), "
                        "or remove the second heading",
                        (other.doc_path, first.doc_path),
                    )
                )

    def _read(self, doc_path: str, declaration: SsotDeclaration) -> None:
        heading_re = declaration.heading_re
        owners = declaration.owners
        for index, section in enumerate(self._corpus.sections(doc_path) or ()):
            if not section.level:
                continue
            match = heading_re.fullmatch(section.heading.strip())
            if not match:
                continue
            identifier = match.group("id")
            prefix = declaration.prefix_of(identifier)
            if prefix not in owners:
                self.heading_problems.append(
                    SectionStatusProblem(
                        doc_path,
                        section.heading,
                        SSOT_PREFIX_UNDECLARED,
                        f"this heading carries {identifier}, and its prefix {prefix} is not "
                        f"in ssot.prefixes ({', '.join(owners)})",
                        f"add {prefix} to ssot.prefixes in documents.yml, mapped to the "
                        "document that owns its ids, or give the section an id with a "
                        "declared prefix",
                        (doc_path,),
                    )
                )
                continue
            if owners[prefix] != doc_path:
                self.heading_problems.append(
                    SectionStatusProblem(
                        doc_path,
                        section.heading,
                        SSOT_ID_WRONG_DOCUMENT,
                        f"this heading owns {identifier} in {doc_path}, and ssot.prefixes "
                        f"maps {prefix} to {owners[prefix]}",
                        f"move the section to {owners[prefix]}, or give it an id whose "
                        f"prefix maps to {doc_path}",
                        (doc_path,),
                    )
                )
            located = _Located(doc_path, index, section)
            self._owners.setdefault(identifier, []).append(located)
            self._ids[(doc_path, index)] = identifier

    def declaration(self, doc_path: str) -> SsotDeclaration | None:
        return self._declarations.get(doc_path)

    def id_of(self, doc_path: str, index: int) -> str | None:
        """The id the section at ``index`` of ``doc_path`` owns, or ``None``."""
        return self._ids.get((doc_path, index))

    def owner(self, identifier: str) -> _Located | None:
        """The section owning ``identifier`` (the first, when it is a duplicate)."""
        found = self._owners.get(identifier)
        return found[0] if found else None

    def _bare_id(self, doc_path: str, value: str) -> SsotDeclaration | None:
        """The declaration under which ``value`` is a bare id: the citing document's own
        when it is in an id-bearing ssot set, else the first id-bearing declaration that
        has it. A document in an id-less set (KYO-553) cites as a non-ssot one does."""
        own = self._declarations.get(doc_path)
        candidates: Iterable[SsotDeclaration] = (
            (own,)
            if own is not None and own.has_ids
            else [
                declaration
                for declaration in dict.fromkeys(self._declarations.values())
                if declaration.has_ids
            ]
        )
        for declaration in candidates:
            if declaration.declares(value) and re.fullmatch(
                f"(?:{declaration.id_pattern})", value
            ):
                return declaration
        return None

    def resolve(self, doc_path: str, value: str) -> _Located | str | None:
        """The owner of ``value`` when it is a bare id, why it has none, or ``None`` when
        ``value`` is not a bare id at all (so the caller resolves it as before)."""
        declaration = self._bare_id(doc_path, value.strip())
        if declaration is None:
            return None
        identifier = value.strip()
        found = self.owner(identifier)
        if found is not None:
            return found
        home = declaration.owners[declaration.prefix_of(identifier)]
        return (
            f"no heading in any ssot document owns {identifier} (looked for a heading "
            f"`{_rendered(declaration, identifier)}` in {home})"
        )

    def chain_head(self, start: _Located) -> _Located | None:
        """``start``'s ``superseded-by`` chain followed to a section that is not
        superseded, or ``None`` when a link does not resolve or the chain loops (the
        link rules refuse both)."""
        seen: set[tuple[str, int]] = set()
        current = start
        while _superseded(current.section):
            key = (current.doc_path, current.index)
            if key in seen:
                return None
            seen.add(key)
            values = link_values(current.section.metadata.get("superseded-by"))
            if not values:
                return None
            following = self._corpus.resolve(current.doc_path, values[0])
            if isinstance(following, str):
                return None
            current = following
        return current

    def name_of(self, located: _Located) -> str:
        """The id ``located`` owns, or its heading when it owns none."""
        return self.id_of(located.doc_path, located.index) or located.section.heading


def ssot_problems(index: SsotIndex, documents: Sequence[str]) -> list[SectionStatusProblem]:
    """Every ssot refusal in ``documents`` (each one an ssot document): the heading
    problems :class:`SsotIndex` found, bare ids in prose that resolve to nothing or to a
    tombstone, and tombstones that kept their body (KYO-552.c2, c4-c6). A document in an
    id-less set (KYO-553) is read for its tombstones only."""
    wanted = set(documents)
    problems = [
        problem
        for problem in index.heading_problems
        if wanted.intersection(problem.paths)
    ]
    corpus = index._corpus
    for doc_path in documents:
        declaration = index.declaration(doc_path)
        if declaration is None:
            continue
        for position, section in enumerate(corpus.sections(doc_path) or ()):
            if declaration.has_ids:
                problems += _prose_problems(index, declaration, doc_path, position, section)
            if section.level and _superseded(section):
                problems += _tombstone_problems(index, declaration, doc_path, position, section)
    return problems


def _prose_problems(
    index: SsotIndex,
    declaration: SsotDeclaration,
    doc_path: str,
    position: int,
    section: Section,
) -> list[SectionStatusProblem]:
    """c4 and c5 for one section's prose."""
    problems: list[SectionStatusProblem] = []
    own = index.id_of(doc_path, position)
    token_re = declaration.token_re
    for paragraph in _prose_paragraphs(section):
        for line in paragraph:
            for match in token_re.finditer(line):
                identifier = match.group(0)
                if not declaration.declares(identifier):
                    continue
                owner = index.owner(identifier)
                if owner is None:
                    home = declaration.owners[declaration.prefix_of(identifier)]
                    problems.append(
                        SectionStatusProblem(
                            doc_path,
                            section.heading,
                            SSOT_ID_UNRESOLVED,
                            f"the prose names {identifier}, and no heading in any ssot "
                            "document owns it",
                            f"write the section that owns it in {home} (a heading "
                            f"`{_rendered(declaration, identifier)}`), or correct the id",
                            (doc_path,),
                        )
                    )
                    continue
                if identifier == own or not _superseded(owner.section):
                    continue
                head = index.chain_head(owner)
                if head is None:
                    continue
                successor = index.name_of(head)
                # The section the chain ends at may name what it replaced: its own id
                # counts as named in its own paragraphs.
                if successor == own or _mentions(paragraph, [successor]):
                    continue
                problems.append(
                    SectionStatusProblem(
                        doc_path,
                        section.heading,
                        SSOT_CITES_TOMBSTONE,
                        f"the prose names {identifier}, which is superseded, and this "
                        f"paragraph does not name {successor}, the head of its "
                        "superseded-by chain",
                        f"cite {successor} instead, or name {successor} in the same "
                        "paragraph if the history is the point",
                        (doc_path, owner.doc_path),
                    )
                )
    return problems


def _tombstone_problems(
    index: SsotIndex,
    declaration: SsotDeclaration,
    doc_path: str,
    position: int,
    section: Section,
) -> list[SectionStatusProblem]:
    """c6 for one superseded section."""
    skipped = metadata_lines(section.body)
    count = sum(
        1
        for number, line in enumerate(section.body)
        if line.strip()
        and number not in skipped
        and not _METADATA_RE.match(_uncommented(line))
        and not _BARE_METADATA_RE.match(_uncommented(line))
    )
    limit = declaration.tombstone_max_lines
    if count <= limit:
        return []
    head = index.chain_head(_Located(doc_path, position, section))
    successor = index.name_of(head) if head is not None else "its successor"
    return [
        SectionStatusProblem(
            doc_path,
            section.heading,
            SSOT_TOMBSTONE_BODY,
            f"this superseded section keeps {count} non-blank line(s) after its metadata, "
            f"and tombstone_max_lines is {limit}",
            f"cut it to at most {limit} line(s): what replaced it belongs in {successor}, "
            "and the history in the record or commit that retired it",
            (doc_path,),
        )
    ]


# ------------------------------------------- KYO-554: ids a record's text names


def index_under(root: Path, documents_in: DocumentSet) -> SsotIndex | None:
    """The id index over the working tree at ``root``, or ``None`` when no set declares
    an id-bearing ``ssot:`` block (KYO-554).

    The same index ``kyo docs check`` builds (:func:`kyosei.docs_check.section_status_problems`),
    over the same documents, so a record and the docs check agree about which section
    owns an id. Built for a record write, which has no branch to read: the tree on disk
    is the state the record is filed against.
    """
    opted = opted_documents(root, documents_in.section_status)
    declarations = {path: chosen.ssot for path, chosen in opted.items() if chosen.ssot}
    if not any(declaration.has_ids for declaration in declarations.values()):
        return None
    return SsotIndex(_Corpus(_read_under(root), numbered_headings(opted)), declarations)


@dataclass(frozen=True)
class NamedId:
    """A bare id a record's text names, and its owner (KYO-554)."""

    identifier: str
    #: The section owning it, or ``None`` when no heading does.
    owner: _Located | None
    #: The document its prefix maps to: where an owning heading would have to be.
    home: str
    #: The heading an owner would carry there, rendered from the set's template.
    expected_heading: str


def ids_named_in(index: SsotIndex, text: str) -> list[NamedId]:
    """Every bare id ``text`` names, once each, in order of first appearance.

    A bare id is what it is in an ssot document's prose: a whole token matching an
    id-bearing set's ``id_pattern`` whose prefix that set declares, so ``UTF-8`` and
    ``KYO-12`` are never ids. A record cites from outside every set, so every id-bearing
    declaration is asked, as :meth:`SsotIndex._bare_id` asks for a non-ssot document.
    """
    found: dict[str, tuple[int, NamedId]] = {}
    declarations = dict.fromkeys(
        declaration for declaration in index._declarations.values() if declaration.has_ids
    )
    for declaration in declarations:
        for match in declaration.token_re.finditer(text):
            identifier = match.group(0)
            if identifier in found or not declaration.declares(identifier):
                continue
            found[identifier] = (
                match.start(),
                NamedId(
                    identifier=identifier,
                    owner=index.owner(identifier),
                    home=declaration.owners[declaration.prefix_of(identifier)],
                    expected_heading=_rendered(declaration, identifier),
                ),
            )
    return [named for _, named in sorted(found.values(), key=lambda pair: pair[0])]
