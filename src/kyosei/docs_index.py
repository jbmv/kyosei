"""``kyo docs index``: the generated section index of a ``section_status:`` set (KYO-502).

A set that declares
``section_status.index`` in documents.yml gets one generated markdown file at that path:

* a summary table, one row per document — how many sections, how many carry each of the
  set's statuses, and how many ``src/`` code pointers reach it;
* one table per document with a non-current section, one row per such section — its
  status, its links, the current head of its chain, and its opening line;
* the sections no code points at.

Current sections are counted and not listed: listing every one would bury the few that
matter. A set that declares ``index_lists: every`` (KYO-512) asks for the other shape:
its per-document tables list every section instead, one row each — number (in a
numbered set) and title, status, head of chain and ``governs`` targets, optionally only
to ``index_depth`` — so the index can be a project's per-section table of contents.
Those rows carry nothing a prose edit changes: no opening line, no line count, no
position, so editing a section's prose without touching its heading, status, links or
``governs:`` leaves the file byte-identical and a worker branch's prose edit cannot make
it stale. The index is built from Kyosei's own Layer 3 fields, which is why Kyosei renders
it itself rather than naming a project's command the way it does for numbered documents
(KYO-299, KYO-480).

Kept from going stale the way ``kyo reference`` is (KYO-138): ``kyo docs index --check``
writes nothing and exits 1 when a committed file differs from what this renders now, and
this repository's test holds its committed copy to it
(``tests/test_kyo491_design_index.py``).

Links resolve through :class:`kyosei.docs_check._Corpus` and pointers are found with the
pattern A5 reads (:func:`kyosei.docs_check.section_status_problems`), so the index and the
checker cannot disagree about which section a link or a pointer names.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .docs_check import (
    _Corpus,
    _Located,
    _read_under,
    numbered_headings,
    opted_documents,
    source_files,
)
from .docs_numbered import heading_pattern
from .docs_parse import (
    _BARE_METADATA_RE,
    _METADATA_RE,
    Section,
    SectionStatusSet,
    _fenced,
    _uncommented,
    heading_anchor,
    link_values,
)
from .errors import EXIT_ERROR, BrokenInvariants
from .registry import INDEX_LISTS_EVERY

#: The command that writes every index. Named once so the generated file's header, the
#: ``--check`` refusal and the committed-file test quote the same string (law 1).
INDEX_COMMAND = "kyo docs index"

#: The status a section with no ``status:`` line is counted under, when the set's
#: vocabulary has it. In this repository that is every section nothing has changed.
CURRENT = "current"

#: The summary column for a status outside the set's vocabulary (an A4 refusal) or an
#: absent one in a set whose vocabulary has no ``current``. Shown only when non-zero.
OTHER = "other"

_LINK_KEYS = ("superseded-by", "amended-by")


class IndexStale(BrokenInvariants):
    """``kyo docs index --check`` found a committed index that differs from the render.

    Exit 1, the criterion's code (KYO-502.c1), rather than :class:`BrokenInvariants`'s 3
    — a stale generated file is one failed comparison, not a doctor verdict — and
    otherwise that class: one line per stale file, and the ``--json`` payload carried
    out through the failure (KYO-187).
    """

    exit_code = EXIT_ERROR


@dataclass(frozen=True)
class IndexFile:
    """One generated index: where it goes, what it says, and which sets declared it."""

    path: str  # root-relative, posix
    text: str
    sets: tuple[str, ...]


def _status(section: Section) -> str | None:
    status = section.metadata.get("status")
    if not isinstance(status, str) or not status.strip():
        return None
    return status.strip().lower()


def _links(section: Section) -> list[str]:
    return [value for key in _LINK_KEYS for value in link_values(section.metadata.get(key))]


def _is_listed(section: Section) -> bool:
    """Non-current: a status other than ``current``, or a link of any kind."""
    return _status(section) not in (None, CURRENT) or bool(_links(section))


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _field_value(section: Section, name: str) -> str:
    """KYO-520: the value of the first ``name: value`` line in ``section`` outside a code
    fence (an HTML comment's delimiters taken off), whitespace-flattened; empty when the
    section does not declare it. Spelled case-insensitively and read from the body as
    written, so a project's own metadata key (``area``) needs no entry in the parser's
    closed vocabulary, and a prose edit that leaves the line alone changes nothing."""
    pattern = re.compile(rf"^\s*{re.escape(name)}:\s*(.*?)\s*$", re.IGNORECASE)
    for line, in_code in _fenced(section.body):
        if in_code:
            continue
        found = pattern.match(_uncommented(line))
        if found:
            return _cell(found.group(1))
    return ""


def _opening_line(section: Section) -> str:
    """The section's first line of prose: not blank, not metadata, not in a code fence."""
    for line, in_code in _fenced(section.body):
        if in_code or not line.strip():
            continue
        bare = _uncommented(line)
        if _METADATA_RE.match(bare) or _BARE_METADATA_RE.match(bare):
            continue
        return line.strip()
    return ""


class _Renderer:
    def __init__(
        self,
        root: Path,
        index_path: str,
        read: Callable[[str], str | None],
        numbered: Mapping[str, str] | None = None,
    ) -> None:
        self.root = root
        self.index_dir = os.path.dirname(index_path) or "."
        # KYO-505: the checker's resolver, numbered documents included, so a bare
        # number in a numbered set links here exactly as `kyo docs check` resolves it.
        self.corpus = _Corpus(read, numbered)
        self.numbered = dict(numbered or {})
        self.read = read

    def href(self, doc_path: str, heading: str | None = None) -> str:
        rel = os.path.relpath(doc_path, self.index_dir).replace(os.sep, "/")
        return rel if heading is None else f"{rel}#{heading_anchor(heading)}"

    def section_link(self, located: _Located, *, qualify: bool) -> str:
        label = located.section.heading
        if qualify:
            label = f"{located.doc_path} {label}"
        return f"[{_cell(label)}]({self.href(located.doc_path, located.section.heading)})"

    def heads(self, start: _Located) -> list[_Located | str]:
        """Every section ``start``'s links lead to that has no links of its own — the
        current head of its chain — followed through both keys. An unresolved link is
        returned as its text; a cycle stops where it closes."""
        heads: list[_Located | str] = []
        seen: set[tuple[str, int]] = {(start.doc_path, start.index)}
        frontier = [start]
        while frontier:
            here = frontier.pop(0)
            for value in _links(here.section):
                found = self.corpus.resolve(here.doc_path, value)
                if isinstance(found, str):
                    if value not in heads:
                        heads.append(value)
                    continue
                key = (found.doc_path, found.index)
                if key in seen:
                    continue
                seen.add(key)
                if _links(found.section):
                    frontier.append(found)
                else:
                    heads.append(found)
        return heads

    def pointer_counts(self, documents: Sequence[str]) -> dict[tuple[str, int], int]:
        """How many ``src/`` pointers name each section, keyed by (document, index)."""
        counts: dict[tuple[str, int], int] = {}
        pointable = sorted(documents, key=len, reverse=True)
        if not pointable:
            return counts
        # The pattern A5 reads (docs_check.section_status_problems), so the index counts
        # exactly the pointers the checker holds to their sections.
        pointer_re = re.compile(
            rf"(?<![\w/.-])({'|'.join(re.escape(path) for path in pointable)})"
            r"#([A-Za-z0-9][\w-]*)"
        )
        for source_path in source_files(self.root):
            text = self.read(source_path)
            if text is None or "\0" in text[:8192]:
                continue
            for match in pointer_re.finditer(text):
                found = self.corpus.by_anchor(match.group(1), match.group(2))
                if found is not None:
                    key = (found.doc_path, found.index)
                    counts[key] = counts.get(key, 0) + 1
        return counts

    def ref(self, found: _Located, doc_path: str, *, by_number: bool) -> str:
        """A section named in a row: its bare number when ``by_number`` and it is a
        numbered section of ``doc_path``, a link otherwise."""
        number = self.corpus.number_of(found.doc_path, found.section)
        if by_number and number is not None and found.doc_path == doc_path:
            return str(number)
        return self.section_link(found, qualify=found.doc_path != doc_path)

    def links_cell(self, located: _Located, doc_path: str, *, by_number: bool) -> str:
        """The section's own ``amended-by``/``superseded-by`` targets, in written order.
        Shown beside the head of chain because the head skips a successor that was
        itself amended — still current — and a reader needs that successor too."""
        cells: list[str] = []
        for value in _links(located.section):
            found = self.corpus.resolve(doc_path, value)
            if isinstance(found, str):
                cells.append(f"{_cell(value)} (unresolved)")
            else:
                cells.append(self.ref(found, doc_path, by_number=by_number))
        return ", ".join(cells)

    def heads_cell(self, located: _Located, doc_path: str, *, by_number: bool) -> str:
        """The head(s) of ``located``'s chain in document order: a numbered section of the
        same document by its bare number when ``by_number``, any other as a link."""
        found = self.heads(located)
        resolved = sorted(
            (head for head in found if not isinstance(head, str)),
            key=lambda head: (head.doc_path != doc_path, head.doc_path, head.index),
        )
        cells = [self.ref(head, doc_path, by_number=by_number) for head in resolved]
        cells += [f"{_cell(head)} (unresolved)" for head in found if isinstance(head, str)]
        return ", ".join(cells)

    def every_table(
        self,
        doc_path: str,
        vocabulary: Sequence[str],
        depth: int | None,
        fields: Sequence[str] = (),
    ) -> list[str]:
        """KYO-512: one row per section of ``doc_path`` (to ``depth``), built only from
        what a prose edit cannot change — the heading, the status, the links and the
        ``governs:`` list — so the table is byte-identical across one.

        In a numbered document the number is the reference — what ``kyo docs assemble``
        writes into a back-link and what A3 accepts as a cite-back — so its rows carry
        the number and the title as text and no anchor links: measured on a consumer's
        246-section notes file, the anchors were 46% of the rendered bytes of a file
        written to be read in place of the document."""
        headed = [
            _Located(doc_path, index, section)
            for index, section in enumerate(self.corpus.sections(doc_path) or [])
            if section.level and (depth is None or section.level <= depth)
        ]
        template = self.numbered.get(doc_path)
        pattern = heading_pattern(template) if template else None
        governed = any(located.section.governs for located in headed)
        header = [
            *(["#", "title"] if pattern else ["section"]),
            "status",
            "links",
            "head of chain",
            *(["governs"] if governed else []),
            *fields,
        ]
        out = [
            f"### [{doc_path}]({self.href(doc_path)})",
            "",
            "| " + " | ".join(header) + " |",
            "|" + "---|" * len(header),
        ]
        for located in headed:
            section = located.section
            if pattern:
                written = f"{'#' * section.level} {section.heading}"
                match = pattern.match(written)
                if match:
                    title = written[match.end() :].strip() or section.heading
                    lead = [match.group(1), _cell(title)]
                else:
                    lead = ["", _cell(section.heading)]
            else:
                lead = [f"[{_cell(section.heading)}]({self.href(doc_path, section.heading)})"]
            status = _status(section)
            if status is None:
                shown = "current (no status line)" if CURRENT in vocabulary else "(no status line)"
            else:
                shown = _cell(status)
            by_number = pattern is not None
            cells = [
                *lead,
                shown,
                self.links_cell(located, doc_path, by_number=by_number),
                self.heads_cell(located, doc_path, by_number=by_number) if _links(section) else "",
            ]
            if governed:
                cells.append(
                    ", ".join(
                        _cell(target) if "`" in target else f"`{_cell(target)}`"
                        for target in section.governs
                    )
                )
            cells += [_field_value(section, name) for name in fields]
            out.append("| " + " | ".join(cells) + " |")
        out.append("")
        return out

    def render_set(self, chosen: SectionStatusSet) -> list[str]:
        documents = sorted(opted_documents(self.root, [chosen]))
        every = chosen.index_lists == INDEX_LISTS_EVERY
        depth = chosen.index_depth
        counts = self.pointer_counts(documents)
        vocabulary = [status.lower() for status in chosen.statuses]
        out: list[str] = [f"## {chosen.name}", ""]
        if not documents:
            out += [f"No file matches this set's globs ({', '.join(chosen.globs)}).", ""]
            return out

        rows: list[list[str]] = []
        listed: list[tuple[str, list[_Located]]] = []
        unpointed: list[_Located] = []
        unpointed_documents: list[tuple[str, int]] = []
        any_other = False
        for doc_path in documents:
            headed = [
                _Located(doc_path, index, section)
                for index, section in enumerate(self.corpus.sections(doc_path) or [])
                if section.level
            ]
            # KYO-520: an index that lists to a depth counts to the same depth, so the
            # summary's number is the number of rows below it.
            counted = [
                located
                for located in headed
                if not (every and depth is not None and located.section.level > depth)
            ]
            tally = dict.fromkeys(vocabulary, 0)
            other = 0
            for located in counted:
                status = _status(located.section)
                if status is None and CURRENT in tally:
                    status = CURRENT
                if status in tally:
                    tally[status] += 1
                else:
                    other += 1
            any_other = any_other or bool(other)
            pointers = sum(counts.get((doc_path, located.index), 0) for located in headed)
            rows.append(
                [
                    f"[{doc_path}]({self.href(doc_path)})",
                    str(len(counted)),
                    *(str(tally[status]) for status in vocabulary),
                    str(other),
                    str(pointers),
                ]
            )
            mine = [located for located in counted if _is_listed(located.section)]
            if mine:
                listed.append((doc_path, mine))
            live = [
                located
                for located in headed
                if located.section.level > 1 and not located.section.dead
            ]
            if pointers == 0:
                # A document no code points into at all is one fact, not one per section:
                # the charter is read whole, and listing its every heading would bury the
                # few sections in a pointed-at document that nothing names.
                unpointed_documents.append((doc_path, len(live)))
            else:
                unpointed += [
                    located
                    for located in live
                    if not counts.get((located.doc_path, located.index))
                ]

        header = ["document", "sections", *vocabulary, *([OTHER] if any_other else []), "pointers"]
        out += [
            "| " + " | ".join(header) + " |",
            "|" + "---|" * len(header),
        ]
        for row in rows:
            if not any_other:
                row = row[:-2] + row[-1:]
            out.append("| " + " | ".join(row) + " |")
        total = sum(int(row[1]) for row in rows)
        listed_count = sum(len(mine) for _, mine in listed)
        if every:
            out += [
                "",
                f"{total} section(s) across {len(documents)} document(s); {listed_count} carry "
                "a status other than current or a link. Every section"
                + (f" at heading level {depth} or above" if depth is not None else "")
                + " is listed below, one row each.",
                "",
            ]
            for doc_path in documents:
                out += self.every_table(doc_path, vocabulary, depth, chosen.index_fields)
            listed = []
        else:
            out += [
                "",
                f"{total} section(s) across {len(documents)} document(s); {listed_count} "
                "carry a status other than current or a link, and are listed below.",
                "",
            ]

        for doc_path, mine in listed:
            out += [
                f"### [{doc_path}]({self.href(doc_path)})",
                "",
                "| section | status | links | head of chain | opening line |",
                "|---|---|---|---|---|",
            ]
            for located in mine:
                links = []
                for value in _links(located.section):
                    found = self.corpus.resolve(doc_path, value)
                    links.append(
                        _cell(value)
                        if isinstance(found, str)
                        else f"[{_cell(value)}]({self.href(found.doc_path, found.section.heading)})"
                    )
                heads = [
                    f"{_cell(head)} (unresolved)"
                    if isinstance(head, str)
                    else self.section_link(head, qualify=head.doc_path != doc_path)
                    for head in self.heads(located)
                ]
                out.append(
                    "| "
                    + " | ".join(
                        [
                            self.section_link(located, qualify=False),
                            _cell(_status(located.section) or ""),
                            ", ".join(links),
                            ", ".join(heads),
                            _cell(_opening_line(located.section)),
                        ]
                    )
                    + " |"
                )
            out.append("")

        out += ["### Sections no code points at", ""]
        if unpointed_documents:
            out += [
                *(
                    f"- [{doc_path}]({self.href(doc_path)}): no `src/` pointer names any of "
                    f"its {live} section(s)"
                    for doc_path, live in unpointed_documents
                ),
                "",
            ]
        if unpointed:
            out += [
                f"Live sections, in documents code does point into, that no `src/` pointer "
                f"names ({len(unpointed)}):",
                "",
                *(f"- {self.section_link(located, qualify=True)}" for located in unpointed),
                "",
            ]
        elif not unpointed_documents:
            out += ["Every live section below a document title is named by a `src/` pointer.", ""]
        return out


def render(root: Path, index_path: str, sets: Sequence[SectionStatusSet]) -> str:
    """The index at ``index_path`` for ``sets`` (every set naming that path), as text."""
    renderer = _Renderer(
        root, index_path, _read_under(root), numbered_headings(opted_documents(root, sets))
    )
    names = ", ".join(f"`{chosen.name}`" for chosen in sets)
    out = [
        "# Section index",
        "",
        f"Generated by `{INDEX_COMMAND}` from the `section_status:` of the {names} document "
        f"set(s) in documents.yml; do not edit. Regenerate with `{INDEX_COMMAND}`; "
        f"`{INDEX_COMMAND} --check` exits 1 when this file is stale.",
        "",
    ]
    for chosen in sets:
        out += renderer.render_set(chosen)
    return "\n".join(out).rstrip("\n") + "\n"


def indexes(root: Path, sets: Sequence[SectionStatusSet]) -> list[IndexFile]:
    """One :class:`IndexFile` per distinct ``index`` path declared in ``sets``."""
    by_path: dict[str, list[SectionStatusSet]] = {}
    for chosen in sets:
        if chosen.index:
            by_path.setdefault(Path(chosen.index).as_posix(), []).append(chosen)
    return [
        IndexFile(path, render(root, path, chosen), tuple(item.name for item in chosen))
        for path, chosen in by_path.items()
    ]


def stale(root: Path, files: Sequence[IndexFile]) -> list[IndexFile]:
    """The files whose committed copy under ``root`` is absent or differs."""
    found: list[IndexFile] = []
    for item in files:
        try:
            committed = (root / item.path).read_text(encoding="utf-8")
        except OSError:
            committed = None
        if committed != item.text:
            found.append(item)
    return found


__all__ = ["INDEX_COMMAND", "IndexFile", "IndexStale", "indexes", "render", "stale"]
