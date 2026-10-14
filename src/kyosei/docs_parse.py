"""The Layer 3 documentation format: which files are documents, their sections, and
the metadata a section declares.

The format itself is stated on :mod:`kyosei.docs_check`, the command a consumer runs;
this module parses it and resolves nothing (that is :mod:`kyosei.docs_targets`). The
history behind these rules is in docs/design/docs.md.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import registry as registry_module
from .prose import flatten

if TYPE_CHECKING:
    from .docs_recx import RecxManifestSet

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")

#: A fenced code block's delimiter: three or more backticks or tildes, indented at most
#: three spaces, with an optional info string. See :func:`_fenced` for why this module
#: has to know what a code fence is at all.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*(.*)$")

#: The heading a ``governs:`` line before the first heading is attributed to.
PREAMBLE = "(preamble)"

#: KYO-109. The section-level metadata keys this format defines, spelled once.
#:
#: ``docs/design/charter.md``'s Layer 3 names exactly these — "``governs:`` (code
#: symbols/files) and status (``current | amended-by <sec>``)" — and this tuple is a
#: **closed** vocabulary on purpose. A general "any ``key: value`` line is metadata"
#: rule would read ordinary prose (``Note: this is deliberate``) as a property, and a
#: query surface whose field set is whatever a sentence happened to start with is one
#: no refusal can name (law 6). Adding a key here is a format decision, made once.
#:
#: ``superseded-by`` joined the vocabulary at KYO-204, beside ``amended-by`` rather than
#: replacing it: the two answer different questions about a section (corrected later and
#: still current, vs. retired), and a store that already reads one carelessly is exactly
#: law 3's ``amended-by``/``status`` footgun this project was written to avoid repeating.
SECTION_METADATA_KEYS: tuple[str, ...] = ("governs", "status", "amended-by", "superseded-by")

#: KYO-204. Underscore spellings of two keys (``amended_by:``, ``superseded_by:``), read
#: as the canonical hyphenated key, so the two spellings are one field and not two nobody
#: reconciles. Only keys a real document writes this way get an alias; an alias nobody
#: writes is a guess at a format that does not exist (docs/design/docs.md#kyo-204).
_KEY_ALIASES: dict[str, str] = {"amended_by": "amended-by", "superseded_by": "superseded-by"}

#: Every spelling :data:`_METADATA_RE` and :data:`_BARE_METADATA_RE` accept, canonical
#: keys and their aliases together — built once so the two regexes cannot drift apart on
#: which spellings they recognise.
_ALL_KEY_SPELLINGS: tuple[str, ...] = tuple(SECTION_METADATA_KEYS) + tuple(_KEY_ALIASES)

#: A metadata line: one of the keys above (or an alias), at the start of a line, and its
#: value. Built from that tuple rather than written out, so a key cannot be declared and
#: unread (docs/design/docs.md#kyo-98).
_METADATA_RE = re.compile(
    rf"^\s*({'|'.join(re.escape(key) for key in _ALL_KEY_SPELLINGS)}):\s*(.+?)\s*$",
    re.IGNORECASE,
)

#: KYO-200. The same keys, with an **empty** value — ``governs:`` and nothing after it.
#: Deliberately a second pattern rather than relaxing :data:`_METADATA_RE`'s ``(.+?)``
#: to ``(.*?)``: a bare key opens a list continuation and a key with a value does not,
#: so :func:`section_metadata` has to tell them apart anyway, and one pattern returning
#: an empty string for the first would push that decision into the caller where it is
#: easy to forget. A bare key that is *not* ``governs`` is matched here and then
#: deliberately does nothing — see :func:`section_metadata` on why ``amended-by:`` and
#: ``status:`` are not list-valued.
_BARE_METADATA_RE = re.compile(
    rf"^\s*({'|'.join(re.escape(key) for key in _ALL_KEY_SPELLINGS)}):\s*$",
    re.IGNORECASE,
)

#: One list item under a bare key: a dash, at least one space, and a value. ``*`` and
#: ``+`` are accepted as the bullet too (KYO-412) — markdown's other two bullets,
#: which an author writing a list in a markdown file reaches for without thinking.
#:
#: The space after the dash is load-bearing, not decoration. The real documents this
#: was written for hold these blocks inside an HTML comment, so the line **after** the
#: last list item is ``-->`` — which starts with a dash. Requiring whitespace is what
#: makes the comment terminator a non-item and therefore the line the list stops at,
#: rather than a target named ``->``.
_LIST_ITEM_RE = re.compile(r"^\s*[-*+]\s+(\S.*?)\s*$")


@dataclass(frozen=True)
class SsotDeclaration:
    """A set's ``ssot:`` block, read (KYO-552, slice 1 of KYO-551).

    The registry has already refused a malformed block by name
    (:func:`kyosei.registry.ssot_block`), so this holds a valid one. The rules that act
    on it are :mod:`kyosei.docs_ssot`.

    KYO-553: the id fields are all set or all unset. An id-less declaration (``ssot: {}``)
    has :attr:`has_ids` false; code that indexes or resolves ids skips it, and its
    :attr:`heading_re` and :attr:`token_re` refuse to be built rather than match with
    empty values. Its empty :attr:`prefixes` declare no id at all, never every one.
    """

    #: The template an owning heading's text follows (``'{id}. {title}'``), or ``None``
    #: for an id-less set.
    id_heading: str | None = None
    #: The regular expression an id matches (``'[A-Z]{1,4}-\\d+'``), unanchored, or
    #: ``None`` for an id-less set.
    id_pattern: str | None = None
    #: ``(prefix, owning document)`` pairs, in declaration order; empty for an id-less set.
    prefixes: tuple[tuple[str, str], ...] = ()
    #: How many non-blank lines a superseded section keeps below its metadata.
    tombstone_max_lines: int = registry_module.SSOT_TOMBSTONE_MAX_LINES_DEFAULT
    #: KYO-555: one of :data:`kyosei.registry.SSOT_HISTORY_LINT_VALUES` -- whether the
    #: history lint (:mod:`kyosei.docs_history`) ignores, prints or blocks on diary prose
    #: a branch newly introduces into this set's documents.
    history_lint: str = registry_module.SSOT_HISTORY_LINT_DEFAULT

    @property
    def has_ids(self) -> bool:
        """Does this set's sections carry ids (all three id keys declared)?"""
        return bool(self.id_heading and self.id_pattern and self.prefixes)

    def _ids_required(self, what: str) -> tuple[str, str]:
        if not self.has_ids or self.id_heading is None or self.id_pattern is None:
            raise ValueError(f"an id-less ssot declaration has no {what}; skip the set")
        return self.id_heading, self.id_pattern

    @property
    def owners(self) -> dict[str, str]:
        """``{prefix: owning document}`` (empty for an id-less set)."""
        return dict(self.prefixes)

    @property
    def heading_re(self) -> re.Pattern[str]:
        """Matches a whole heading text the template describes; group ``id`` is the id.

        The literals are escaped, ``{id}`` becomes the id pattern and ``{title}`` any
        non-empty text. Raises :class:`ValueError` for an id-less set."""
        id_heading, id_pattern = self._ids_required("id_heading")
        pattern = re.escape(id_heading)
        pattern = pattern.replace(
            re.escape(registry_module.SSOT_ID), f"(?P<id>(?:{id_pattern}))", 1
        )
        pattern = pattern.replace(re.escape(registry_module.SSOT_TITLE), "(?P<title>.+?)", 1)
        return re.compile(pattern)

    @property
    def token_re(self) -> re.Pattern[str]:
        """Matches an id as a whole token in prose: on word boundaries, and not as part
        of a longer hyphenated or dotted-path word (``KYO-12.c1`` still ends at ``12``).
        Raises :class:`ValueError` for an id-less set."""
        _, id_pattern = self._ids_required("id_pattern")
        return re.compile(rf"(?<![\w-])(?:{id_pattern})(?![\w-])")

    @staticmethod
    def prefix_of(identifier: str) -> str:
        """The part of an id before its ``-`` (the whole id when it has none)."""
        return identifier.partition("-")[0]

    def declares(self, identifier: str) -> bool:
        """Is ``identifier`` an id in this set's sense: its prefix is a declared key?
        Never, in an id-less set."""
        return self.has_ids and self.prefix_of(identifier) in self.owners

    @property
    def says(self) -> str:
        tombstones = (
            f"tombstones at most {self.tombstone_max_lines} line(s); "
            f"history lint {self.history_lint}"
        )
        if not self.has_ids:
            return f"no section ids; {tombstones}"
        return (
            f"ids `{self.id_heading}` matching {self.id_pattern} with prefixes "
            f"{', '.join(prefix for prefix, _ in self.prefixes)}; {tombstones}"
        )


def ssot_declaration(block: object) -> SsotDeclaration | None:
    """A validated ``ssot:`` mapping as a :class:`SsotDeclaration`, or ``None``."""
    if not isinstance(block, dict):
        return None
    limit = block.get("tombstone_max_lines")
    prefixes = block.get("prefixes") or {}
    return SsotDeclaration(
        id_heading=str(block["id_heading"]) if "id_heading" in block else None,
        id_pattern=str(block["id_pattern"]) if "id_pattern" in block else None,
        prefixes=tuple((str(prefix), str(path)) for prefix, path in prefixes.items()),
        tombstone_max_lines=(
            limit
            if isinstance(limit, int) and not isinstance(limit, bool)
            else registry_module.SSOT_TOMBSTONE_MAX_LINES_DEFAULT
        ),
        history_lint=(
            registry_module.ssot_history_lint(block.get("history_lint"))
            or registry_module.SSOT_HISTORY_LINT_DEFAULT
        ),
    )


@dataclass(frozen=True)
class SectionStatusSet:
    """One document set that declared ``section_status:`` (KYO-499.c2).

    Carried per set, not collapsed across the file the way the globs and bases are,
    because its rules are per set: a status this set's vocabulary refuses may be one
    another set's documents use. ``globs`` are the set's own, so the checker can tell
    which files the stricter rules apply to (docs/design/docs.md#kyo-499).
    """

    #: The set's name in ``documents.yml``.
    name: str
    #: The set's own ``globs:``, in declaration order.
    globs: tuple[str, ...]
    #: The closed vocabulary a section's ``status:`` may take in this set.
    statuses: tuple[str, ...]
    #: Whether a code pointer into this set raises a docs obligation;
    #: default true. Stated by ``kyo docs check``.
    pointer_obligations: bool = True
    #: Where the generated section index goes, or ``None``. Stated by
    #: ``kyo docs check``.
    index: str | None = None
    #: KYO-505: the set's own ``numbered.heading`` template (``## {number}.``), or
    #: ``None`` when the set declares no ``numbered:``. In a numbered set a bare number
    #: in ``amended-by``/``superseded-by`` names the heading this template renders for
    #: it — the form ``kyo docs assemble`` writes (KYO-493) — and A3 accepts the
    #: section's number in a citation spelling (``docs_check.NUMBERED_CITE_SPELLINGS``).
    #: Stated by ``kyo docs check``.
    numbered_heading: str | None = None
    #: KYO-512: what the generated index lists below its summary, one of
    #: :data:`kyosei.registry.INDEX_LISTS_VALUES` — the non-current sections (default) or
    #: every section, one row each. Read by :mod:`kyosei.docs_index`; stated here.
    index_lists: str = registry_module.INDEX_LISTS_NON_CURRENT
    #: KYO-512: with ``index_lists: every``, the deepest heading level that gets a row,
    #: or ``None`` for every level. Read by :mod:`kyosei.docs_index`; stated here.
    index_depth: int | None = None
    #: KYO-520: with ``index_lists: every``, the metadata field names that get a column
    #: each after the fixed ones. Read by :mod:`kyosei.docs_index`; stated here.
    index_fields: tuple[str, ...] = ()
    #: KYO-552: the set's ``ssot:`` declaration, or ``None``. The registry refuses one
    #: without ``section_status:``, so every ssot set is one of these. Read by
    #: :mod:`kyosei.docs_ssot`; stated here.
    ssot: SsotDeclaration | None = None

    @property
    def says(self) -> str:
        """One line naming the set and what it declared."""
        index = self.index or "none declared"
        if self.index and self.index_lists == registry_module.INDEX_LISTS_EVERY:
            depth = (
                f" to heading level {self.index_depth}" if self.index_depth is not None else ""
            )
            index = f"{index}, listing every section{depth}"
            if self.index_fields:
                index += f", with column(s) {', '.join(self.index_fields)}"
        numbered = (
            f"; a bare number names its `{self.numbered_heading}` heading"
            if self.numbered_heading
            else ""
        )
        ssot = f"; ssot: {self.ssot.says}" if self.ssot else ""
        return (
            f"{self.name} (statuses: {', '.join(self.statuses)}; pointer obligations "
            f"{'on' if self.pointer_obligations else 'off'}; index: {index}{numbered}{ssot})"
        )


def declared_section_status_sets(
    registry: registry_module.Registry,
) -> tuple[SectionStatusSet, ...]:
    """Every set in ``registry`` that declares a ``section_status:`` block, in order.

    The registry has already refused a malformed block by name (KYO-499.c2,
    :func:`kyosei.registry.section_status_block`), so this reads a valid one.
    """
    found: list[SectionStatusSet] = []
    for name in registry.declared_names:
        entry = registry[name]
        block = entry.get("section_status")
        if not isinstance(block, dict):
            continue
        globs = tuple(
            glob
            for glob in (entry.get(registry_module.ENUMERATE.field_name) or ())
            if isinstance(glob, str)
        )
        index = block.get("index")
        lists = block.get("index_lists")
        depth = block.get("index_depth")
        fields = block.get("index_fields")
        numbered = entry.get("numbered")
        heading = numbered.get("heading") if isinstance(numbered, dict) else None
        found.append(
            SectionStatusSet(
                name=name,
                globs=globs,
                statuses=tuple(str(item).strip() for item in block.get("statuses") or ()),
                pointer_obligations=block.get("pointer_obligations", True) is not False,
                index=index.strip() if isinstance(index, str) and index.strip() else None,
                numbered_heading=str(heading) if heading else None,
                index_lists=(
                    lists
                    if lists in registry_module.INDEX_LISTS_VALUES
                    else registry_module.INDEX_LISTS_NON_CURRENT
                ),
                index_depth=(
                    depth if isinstance(depth, int) and not isinstance(depth, bool) else None
                ),
                index_fields=(
                    tuple(item.strip() for item in fields if isinstance(item, str))
                    if isinstance(fields, list)
                    else ()
                ),
                ssot=ssot_declaration(entry.get("ssot")),
            )
        )
    return tuple(found)


# ------------------------------------------- KYO-129: which files are the documents
#
# The project declares them (`kyosei/documents.yml`), with a default that covers
# root-level `*.md` as well as `docs/`. The check and the query source enumerate through
# the same `DocumentSet` and `_iter_docs_files`, so neither can see a file the other
# cannot, and a scan that matched no file is a named state (`DocsCheck`), never a clean
# one. Why: docs/design/docs.md#kyo-129.


@dataclass(frozen=True)
class DocumentSet:
    """Which files this project calls Layer 3 documents, and who decided that.

    ``declared`` is what keeps :data:`registry_module.DEFAULT_DOCUMENT_GLOBS` from being
    a silent answer. A caller can always say whether it scanned what the project asked
    for or what this build ships, and `kyo docs check` prints which — because a report
    that cannot be told apart from a real answer is the defect this whole record is
    about, and defaulting is exactly the move that produces one.
    """

    #: Root-relative :meth:`pathlib.Path.glob` patterns, in declaration order.
    globs: tuple[str, ...]
    #: Did ``documents.yml`` name these, or are they the built-in default?
    declared: bool
    #: The registry file consulted, or ``None`` when none was (:data:`DEFAULT`).
    registry_path: Path | None = None
    #: Did that file exist? Carried for the same reason :attr:`Registry.exists` is: a
    #: project that has never had a ``documents.yml`` and one that has an empty one are
    #: both on the default, and telling the first that its file "declares no document
    #: set" would name a state of a file that is not there.
    registry_exists: bool = False
    #: KYO-200. The directories a ``governs:`` target's path resolves under, in the
    #: order to try them, or empty for "the project root and nothing else". Empty
    #: rather than ``("",)`` so :attr:`says` can tell a project that declared the root
    #: from one that declared nothing — the distinction :attr:`declared` already makes
    #: for the globs, made once more for the field beside them.
    paths_relative_to: tuple[str, ...] = ()
    #: KYO-499. The sets that declared ``section_status:``, each with its own globs —
    #: per set rather than collapsed, see :class:`SectionStatusSet`. Empty for a project
    #: that declared none, which is every project until it opts in.
    section_status: tuple[SectionStatusSet, ...] = ()
    #: KYO-600. The set declaring ``format: recx-manifest``, read into
    #: :class:`kyosei.docs_recx.RecxManifestSet`, or ``None`` for a project that declares
    #: none. When present, its file is read by :mod:`kyosei.docs_recx` and every other
    #: document by the markdown reader (:func:`document_sections`).
    recx_manifest: RecxManifestSet | None = None

    def is_recx_manifest(self, rel_path: str) -> bool:
        """Whether ``rel_path`` is the file this set reads as a recx manifest."""
        return self.recx_manifest is not None and rel_path == self.recx_manifest.path

    @property
    def bases(self) -> tuple[str, ...]:
        """The bases to actually try, which is the project root when none was declared.

        The one place the empty-means-root convention is spelled, so no resolver has to
        remember it and no two of them can remember it differently. ``""`` is the root
        because that is what :meth:`pathlib.Path.__truediv__` treats as a no-op join.
        """
        return self.paths_relative_to or ("",)

    @property
    def says(self) -> str:
        """One line naming the paths scanned and the authority for them."""
        globs = ", ".join(self.globs)
        if self.paths_relative_to:
            globs = f"{globs}; governs: targets resolved under {', '.join(self.paths_relative_to)}"
        if self.declared:
            return f"{globs} (declared in {self.registry_path})"
        if self.registry_path is None:
            return f"{globs} (this build's default; no registry was consulted)"
        if not self.registry_exists:
            return (
                f"{globs} (this build's default; this project has no "
                f"{self.registry_path} and so declares no documents of its own)"
            )
        return (
            f"{globs} (this build's default; {self.registry_path} declares no document "
            f"set, so nothing narrowed or widened it)"
        )


#: The set used when no registry is consulted at all — the direct-API default, and what
#: :func:`check_docs` and :func:`documents` fall back to. It is a real, nameable set
#: rather than an implicit ``if globs is None`` at two call sites.
DEFAULT = DocumentSet(globs=registry_module.DEFAULT_DOCUMENT_GLOBS, declared=False)


def document_set(kyosei_dir: Path) -> DocumentSet:
    """What ``kyosei/documents.yml`` says this project's documents are (KYO-129.c2).

    One resolver, called by both consumers, which is what makes "the query source
    enumerates the same set the check does" (KYO-129.c4) true by construction rather
    than by two functions agreeing today.

    ``kyosei_dir`` is the STORE's registry directory, which is a different repository
    from the code root under the split layout — the globs are declared where the
    project's other registries are and resolved against where its code is, and the two
    call sites pass both because they are the two that know.
    """
    registry = registry_module.load_document_registry(kyosei_dir)
    declared = registry_module.declared_document_globs(registry)
    # KYO-200. Read once and carried onto both branches. `DOCUMENT.globs` is required,
    # so today an entry declaring a base has necessarily declared globs too and the
    # default branch always gets an empty tuple — passing it anyway is what keeps that
    # a property of the *registry's* shape rather than an assumption baked in here, so
    # making `globs` optional later cannot silently drop a declared base.
    bases = registry_module.declared_document_bases(registry)
    section_status = declared_section_status_sets(registry)
    from .docs_recx import declared_manifest  # docs_recx builds on this module

    recx_manifest = declared_manifest(registry)
    if declared is None:
        return DocumentSet(
            globs=registry_module.DEFAULT_DOCUMENT_GLOBS,
            declared=False,
            registry_path=registry.path,
            registry_exists=registry.exists,
            paths_relative_to=bases,
            section_status=section_status,
            recx_manifest=recx_manifest,
        )
    return DocumentSet(
        globs=declared,
        declared=True,
        registry_path=registry.path,
        registry_exists=registry.exists,
        paths_relative_to=bases,
        section_status=section_status,
        recx_manifest=recx_manifest,
    )


def _iter_docs_files(root: Path, globs: Sequence[str]) -> list[Path]:
    """Every file under ``root`` matching any of ``globs``, deduplicated and sorted.

    :meth:`pathlib.Path.glob` semantics, where ``*`` stops at a ``/`` and ``**`` crosses
    directories — so ``*.md`` is the repository root only and ``docs/**/*.md`` is the
    tree. **This is not the dialect ``scopes.yml``'s ``paths`` uses**, which is
    ``fnmatch`` over an already-known path, where ``*`` crosses ``/``. The two look
    alike and are not, because one enumerates a filesystem and the other filters a list,
    and only the first has a spelling for "recursively".

    Which is why the field read here is ``globs:`` and the one there is ``paths:``
    (:class:`registry.PathDialect`, docs/design/docs.md#kyo-142): a line copied between
    the two files lands on a key the destination does not accept and is refused naming
    both dialects. Both are stated in the packaged registry file that uses them, because
    a glob dialect a reader has to discover by experiment is a config field that will be
    written wrong.

    Sorted across the whole set rather than per glob, so the enumeration order is a
    property of the tree and not of the order the registry happened to list its entries
    in — two projects declaring the same paths in a different order get the same rows.
    """
    found: set[Path] = set()
    for glob in globs:
        found.update(path for path in root.glob(glob) if path.is_file())
    return sorted(found)


@dataclass(frozen=True)
class Section:
    """One heading in a docs file, its body, and the metadata that body declares.

    ``level`` is the number of ``#`` characters, or ``0`` for the preamble — content
    before the first heading, which is a real section of the file and not a heading of
    level one. ``metadata`` is :func:`section_metadata` over ``body``.
    """

    heading: str
    level: int
    body: list[str]
    metadata: dict[str, object]
    #: KYO-200. Each ``governs:`` target, split and resolved against the project's
    #: document-set bases — one entry per entry in :attr:`governs`, same order.
    #:
    #: Empty for a section parsed with no resolver at all (the direct-API shape a unit
    #: test uses; :func:`documents` and :func:`check_docs` both pass one, so every
    #: section a consumer ever sees has a resolver) **and empty for a dead section**
    #: (KYO-204, :attr:`dead`) — a dead section's targets are never handed to
    #: :func:`resolve_target` at all, per :func:`section_is_dead`'s docstring. Callers
    #: that need a real path read :attr:`governed_paths`, which copes with all three.
    resolved: tuple[GovernsTarget, ...] = ()
    #: KYO-204. :func:`section_is_dead` over :attr:`metadata`, computed once at parse
    #: time and carried here so every later reader asks this field instead of
    #: recomputing the rule — the same reason :attr:`resolved` is computed once instead
    #: of re-derived by each of its three consumers.
    dead: bool = False

    @property
    def governs(self) -> list[str]:
        """Every target this section declares, **exactly as it wrote them**.

        The rendering answer, unchanged by KYO-200 and deliberately so: a tool asked to
        check a document must quote that document back, not its own normalisation of it.
        Unaffected by :attr:`dead` — a dead section's ``governs:`` list is kept as
        history (KYO-204) and rendering it back is exactly what "kept as history" means.
        """
        return list(self.metadata.get("governs") or [])

    @property
    def governed_paths(self) -> tuple[str, ...]:
        """Every root-relative path this section's targets resolved to, deduplicated.

        The mechanism answer, and the counterpart to :attr:`governs`. Falls back to the
        targets' written path halves when the section was parsed without a resolver —
        which is what the pre-KYO-200 consumers did with them, so a direct-API caller
        that never asked for resolution keeps the behaviour it had. A dead section
        (KYO-204) never falls back: its ``governs:`` list is history, not a set of paths
        for a mechanism to act on, so this is empty for one regardless of whether a
        resolver was ever passed.
        """
        if self.dead:
            return ()
        if self.resolved:
            found = [path for target in self.resolved for path in target.paths]
        else:
            found = [path for path, _ in map(split_target, self.governs) if path]
        return tuple(dict.fromkeys(found))


def _fenced(lines: Sequence[str]) -> Iterator[tuple[str, bool]]:
    """Each line, paired with whether it sits inside a fenced code block.

    KYO-129. A code fence is a thing this module would rather not know about, and it has
    to: root-level ``*.md`` is a document by default, and a README is the one markdown
    file *guaranteed* to contain examples of the format. A ``governs:`` line quoted in a
    fence is an illustration, not a live claim the build enforces; likewise a ``# comment``
    in a shell fence is not a heading and a ``status:`` in a YAML example is not section
    metadata (docs/design/docs.md#kyo-129).

    Deliberately coarse, matching the rest of this skeleton validator: a fence opens on
    three or more backticks or tildes and closes on the same character, at least as long
    and carrying no info string. Indented (four-space) code blocks are NOT recognised,
    which means a ``governs:`` line indented under a list item is still read — the format
    has always allowed a metadata line to be indented (:data:`_METADATA_RE` leads with
    ``\\s*``), and taking that back to catch a rarer quoting style would break documents
    that are correct today.

    The fence lines themselves are reported as inside, because a bare ```` ``` ```` is
    not prose either way and no metadata key can match one.
    """
    fence: str | None = None
    for line in lines:
        match = _FENCE_RE.match(line)
        if match:
            marker, info = match.group(1), match.group(2).strip()
            if fence is None:
                fence = marker
                yield line, True
                continue
            if marker[0] == fence[0] and len(marker) >= len(fence) and not info:
                fence = None
                yield line, True
                continue
        yield line, fence is not None


#: How :func:`_sections` turns one written target into a resolved one. A callable rather
#: than a ``(root, bases)`` pair so a whole run can share one :class:`_SymbolIndex`
#: without that class appearing in five signatures.
TargetResolver = Callable[[str], "GovernsTarget"]


def _sections(text: str, resolve: TargetResolver | None = None) -> list[Section]:
    """Split markdown text into its sections, in the order the file writes them.

    Content before the first heading is its own section, titled
    :data:`PREAMBLE` — a ``governs:`` line there is still checked; nothing in
    the format requires a heading, only that the file be a markdown document.

    A heading or a metadata line inside a fenced code block is quoted rather than
    declared, and is read as neither (KYO-129; see :func:`_fenced`). :attr:`Section.body`
    still carries every line the section holds, fenced ones included — what is filtered
    is what :func:`section_metadata` is shown, not what the section *is*, so a body-prose
    filter over a document still matches text in its examples.
    """
    sections: list[Section] = []
    heading, level = PREAMBLE, 0
    body: list[str] = []
    prose: list[str] = []

    def close() -> None:
        metadata = section_metadata(prose)
        targets = list(metadata.get("governs") or [])
        dead = section_is_dead(metadata)
        # KYO-200: resolution happens HERE, once, and not in each of the five consumers
        # that used to partition a target for themselves. That is what lets everything
        # downstream — staleness, obligation raising, the renderers — see an ordinary
        # root-relative path and stay ignorant of separators and bases.
        #
        # KYO-204: a dead section's targets are never handed to `resolve` at all, not
        # merely ignored once resolved — the whole point is that they are history, not
        # a claim the filesystem is asked to confirm, and a governs entry naming a file
        # deleted a year ago should not even be looked up.
        resolved = () if resolve is None or dead else tuple(resolve(target) for target in targets)
        sections.append(Section(heading, level, body, metadata, resolved, dead))

    for line, in_code in _fenced(text.splitlines()):
        match = None if in_code else HEADING_RE.match(line)
        if match:
            close()
            heading, level = match.group(2), len(match.group(1))
            body, prose = [], []
        else:
            body.append(line)
            if not in_code:
                prose.append(line)
    close()
    return sections


def read_document(
    rel_path: str,
    text: str,
    documents_in: DocumentSet,
    resolve: TargetResolver | None = None,
    root: Path | None = None,
) -> Document:
    """One document, read in the format its document set declares (KYO-600).

    A project's RECX.yaml, when a set declares ``format: recx-manifest``, is read by
    :func:`kyosei.docs_recx.manifest_document`; every other file is markdown, read by
    :func:`_sections` exactly as before. ``root`` lets a module path be checked for
    existence, which is ``kyo docs check``'s concern and nobody else's.
    """
    if documents_in.is_recx_manifest(rel_path):
        from .docs_recx import manifest_document

        return manifest_document(text, documents_in.recx_manifest, resolve, root)
    return Document(path=rel_path, text=text, sections=_sections(text, resolve))


def section_metadata(body: list[str]) -> dict[str, object]:
    """The machine-readable metadata a section's body declares (KYO-109).

    One key per entry in :data:`SECTION_METADATA_KEYS`, always present, so a caller
    never has to distinguish "this section declares no ``status:``" from "I forgot to
    look" — the two absences are the reason the mapping is filled rather than sparse.

    ``governs`` is a **list**, accumulated across every ``governs:`` line in the section,
    in **either of two spellings** and both may appear in one section (KYO-200):

    * ``governs: a, b`` — one line, comma-split, the format ``kyo docs check`` has
      enforced since KYO-13 and which nothing here takes away;
    * ``governs:`` with an empty value, followed by ``  - a`` / ``  - b`` list lines
      (``*`` and ``+`` bullets too, KYO-412). The list ends at **the first line that is
      not a list item** — blank line, prose, the next key, or an HTML comment's ``-->``
      — rather than at the section's end, so a ``- `` bullet in the prose below cannot
      silently join the list. Blank lines *before the first item* are skipped rather
      than ending an empty list (KYO-412).

    Either spelling may sit inside an HTML comment, one-line (``<!-- governs: a -->``)
    or spread over lines; the delimiters are taken off a line's ends before it is read
    (:func:`_uncommented`, KYO-412). A one-line value splits on commas outside a
    ``{a,b,c}`` brace group only (:func:`_split_targets`).

    The other keys are single values and the **first** line wins; a section declaring
    two ``status:`` lines is malformed, and nothing validates the
    format — see :mod:`kyosei.docs_check` on what is and is not enforced. Values are
    whitespace-flattened, so a value is compared and displayed as the one-line thing it
    is rather than as however the line was indented.

    **Only ``governs`` accumulates a list, and that is a decision rather than an
    oversight.** A ``see_also: [159, 165]`` is not read as a list because nothing
    consumes a see-also, and a field parsed into a shape no consumer reads is law 2's
    rot (docs/design/docs.md#kyo-204). ``amended_by: []`` and ``superseded_by: [177]``
    are the underscore spelling of ``amended-by`` and ``superseded-by``, read as the
    same field (:data:`_KEY_ALIASES`). A bare ``status:``, ``amended-by:`` or
    ``superseded-by:`` (either spelling) matches :data:`_BARE_METADATA_RE`, contributes
    nothing, and — this is the part worth stating — does **not** open a list that the
    ``- `` lines below it would fall into.

    Targets are stored **exactly as written**. Nothing is normalised here: not the
    ``:``/``#`` separator, not the path's base. This function has no project root and
    no document set, and giving it one so it could resolve would make the format's
    parser depend on the filesystem. Resolution is :func:`resolve_target`'s job and
    happens in :func:`_sections`, which does have both.

    Reading ``status:`` and ``amended-by:`` here cross-checks nothing: an ``amended-by``
    on a ``status: current`` section parses without complaint, and the "pairs agree"
    check that refuses it is :func:`kyosei.docs_check.section_status_problems` (KYO-499,
    docs/design/docs.md#kyo-499), which reads this function's output. ``superseded-by``
    is also acted on, with ``status``, by :func:`section_is_dead` — see that function
    for the rule.
    """
    return _read_section_metadata(body)[0]


def _uncommented(line: str) -> str:
    """``line`` with an HTML comment's delimiters taken off its ends (KYO-412).

    ``<!-- governs: src/x.py -->`` is the one-line compression of the multi-line comment
    form, and the metadata patterns are anchored at the key, so without this the
    ``<!--`` hides the line and a trailing ``-->`` joins the last target
    (docs/design/docs.md#kyo-412). Only the ends are touched — a ``-->`` in the middle of
    a value is the author's text, not a delimiter.
    """
    text = line.strip()
    if text.startswith("<!--"):
        text = text[4:].strip()
    if text.endswith("-->"):
        text = text[:-3].strip()
    return text


def _split_targets(value: str) -> list[str]:
    """A one-line ``governs:`` value split on the commas **outside** ``{...}`` (KYO-412).

    :data:`GOVERNS_FORMAT` — printed with every failure — tells the author a path may
    carry a ``{a,b,c}`` brace group, so a comma inside one must not split the value
    (docs/design/docs.md#kyo-412).
    """
    targets: list[str] = []
    depth = 0
    current: list[str] = []
    for char in value:
        if char == "{":
            depth += 1
        elif char == "}" and depth:
            depth -= 1
        elif char == "," and not depth:
            targets.append("".join(current))
            current = []
            continue
        current.append(char)
    targets.append("".join(current))
    return [target.strip() for target in targets if target.strip()]


def _read_section_metadata(body: Sequence[str]) -> tuple[dict[str, object], int]:
    """:func:`section_metadata`, plus how many ``governs:`` declarations named nothing.

    The count is KYO-412's other half: a ``governs:`` that parses to no target leaves
    the section governing nothing, and :data:`GOVERNS_FORMAT` is printed only on a
    failure, so :func:`check_docs` reads the count and reports each one as a problem
    (docs/design/docs.md#kyo-412).
    """
    metadata: dict[str, object] = dict.fromkeys(SECTION_METADATA_KEYS)
    targets: list[str] = []
    naming_nothing = 0
    #: Set by a bare ``governs:`` and cleared by the first line that is not a list item.
    #: A plain flag rather than a mode enum because there is exactly one list-valued key.
    in_list = False
    #: How many items the open list has taken — blank lines are skipped only before the
    #: first (KYO-412): after it, a blank line still ends the list, so a bullet list in
    #: the prose a paragraph further down cannot silently join it.
    items = 0
    for raw in body:
        if in_list:
            if not raw.strip() and not items:
                continue
            # Matched against the RAW line: a lone `-->` must stay a non-item that ends
            # the list (see `_LIST_ITEM_RE`), which stripping it first would undo.
            item = _LIST_ITEM_RE.match(raw)
            if item:
                value = _uncommented(item.group(1))
                if value:
                    targets.append(value)
                    items += 1
                continue
            in_list = False
            if not items:
                naming_nothing += 1
            # Fall through: the line that ended the list is still a candidate metadata
            # line in its own right, so `governs:` immediately followed by `status:`
            # loses neither.
        line = _uncommented(raw)
        bare = _BARE_METADATA_RE.match(line)
        if bare:
            key = _KEY_ALIASES.get(bare.group(1).lower(), bare.group(1).lower())
            in_list = key == "governs"
            items = 0
            continue
        match = _METADATA_RE.match(line)
        if not match:
            continue
        raw_key, value = match.group(1).lower(), match.group(2)
        key = _KEY_ALIASES.get(raw_key, raw_key)
        if key == "governs":
            named = _split_targets(value)
            targets += named
            if not named:
                naming_nothing += 1
        elif metadata[key] is None:
            metadata[key] = flatten(value)
    if in_list and not items:
        naming_nothing += 1
    metadata["governs"] = targets
    return metadata, naming_nothing


#: KYO-204. `status:` values that alone mark a section dead -- its `governs:` list is
#: history from here on, not a claim `kyo docs check` still enforces. `amended` is
#: deliberately absent: an amended section is still current, corrected later, and its
#: targets still resolve -- KYO-98's "pairs agree" check (built by KYO-499,
#: `docs_check.section_status_problems`) is a different question about a section that
#: claims both at once, not this one.
DEAD_STATUSES: frozenset[str] = frozenset({"historical", "superseded"})


def _names_something(value: object) -> bool:
    """Does a `superseded-by` value name a real successor, as opposed to declaring
    that there is none (KYO-204)?

    The consumer writes `superseded_by: []` for "nothing supersedes this" and
    `superseded_by: [177]` for a real one; `[]`, whitespace-insensitive, is the one
    spelling this reads as empty. Anything else -- `[177]`, `177`, a bare section
    title written by hand -- names something, bracketed or not: this function does not
    parse the bracket syntax as a list, only tell its one empty spelling apart from
    everything that is not it.
    """
    return isinstance(value, str) and value.strip() not in ("", "[]")


def section_is_dead(metadata: Mapping[str, object]) -> bool:
    """Is this section's `governs:` list history rather than a live claim? (KYO-204)

    **One rule, called from every surface that decides whether a `governs:` target
    still counts** -- :func:`_sections` at parse time (so a dead section's targets are
    never even resolved), and the two surfaces that reason over what parsing already
    resolved, :func:`document_row` (feeding :func:`governs_staleness`) and
    :func:`obligations_for_changed_files`. Spelled once here rather than three times so
    those three cannot disagree about the same section (the whole point of this record,
    per its own task prose).

    Dead: `status` is `historical` or `superseded` (case-insensitive,
    :data:`DEAD_STATUSES`), or `superseded-by` names something
    (:func:`_names_something`). Current: everything else -- `amended`, and a
    non-empty `amended-by` alone, do NOT make a section dead. An amended section is
    still current, corrected later; a superseded or historical one is retired, and its
    `governs:` list is kept as the record of what it used to govern rather than pruned
    to satisfy a checker that no longer applies to it.
    """
    status = metadata.get("status")
    if isinstance(status, str) and status.strip().lower() in DEAD_STATUSES:
        return True
    return _names_something(metadata.get("superseded-by"))


def link_values(value: object) -> tuple[str, ...]:
    """The sections an ``amended-by`` or ``superseded-by`` value names, in order (KYO-499).

    One value parser for both shapes the format has: a comma-separated list
    (``KYO-316, KYO-362``) and a bracketed flow list (``[188, 249]``, the shape KYO-493's
    ``kyo docs assemble`` writes into numbered documents). ``[]`` and an absent value
    name nothing — :func:`_names_something`'s rule, which this calls first so the two
    cannot disagree about what is empty.
    """
    if not _names_something(value):
        return ()
    text = str(value).strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return tuple(item.strip() for item in text.split(",") if item.strip())


def heading_anchor(heading: str) -> str:
    """The anchor a markdown renderer gives ``heading``: ``KYO-485`` is ``kyo-485``.

    GitHub's rule, as far as this format needs it: lower case, every character that is
    not a letter, digit, space, hyphen or underscore dropped, and each space a hyphen.
    It is what the code's ``docs/design/<file>.md#kyo-<n>`` pointers are spelled in, so
    a pointer and a section link resolve through the same function (KYO-499).
    """
    kept = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return kept.replace(" ", "-")


# ------------------------------------------------- KYO-200: what a target is, and where
#
# Three questions, answered by three functions, in this order and nowhere else:
# `split_target` (does this name a symbol, and where does the path stop?),
# `docs_targets.target_shape` (is the left half a path at all?), and
# `docs_targets.resolve_target` (which real file, under which base?). Every caller goes
# through them, which is what makes "a `:` separator resolves exactly as a `#` one" a
# property of the code (docs/design/docs.md#kyo-200).


def split_target(target: str) -> tuple[str, str | None]:
    """A target's path half and its symbol half, for both separator spellings.

    ``#`` is the separator this format shipped with (KYO-13); ``:`` is the one documents
    predating this parser already use (docs/design/docs.md#kyo-200). ``#`` wins wherever
    both appear, because it is unambiguous — no path contains one — and the colon is not.

    **The rule for telling a separating colon from one inside a path**, stated here
    because it is the whole of what makes the colon form safe, and tested from both
    sides:

        A ``:`` separates a symbol iff it is not immediately followed by ``/`` **and**
        no ``/`` appears after it. The first such colon in the string wins.

    Both clauses earn their place. "Not followed by ``/``" is what leaves a URL-ish
    target (``https://example.com/x.py``) alone: its colon is a scheme's. "No ``/``
    after it" is what leaves a colon *inside a directory or file name* alone
    (``docs/a:b/thing.md``, ``notes/2026:Q1/plan.md``) — a colon before the last path
    separator is part of the path, since a symbol cannot contain one. What survives
    both is a colon in the final path segment, which is exactly where ``file.py:symbol``
    puts it.

    The residual ambiguity is honest and small: a *filename* that itself ends in
    ``:something`` is unspellable in the colon form. Use the ``#`` form for it — which
    is the reason the older separator is kept rather than migrated away from.

    Returns ``(path, symbol)`` with both halves stripped, and ``symbol`` ``None`` when
    the target names none. Neither half is resolved, normalised, or checked to exist.
    """
    if "#" in target:
        path_part, _, symbol_part = target.partition("#")
        return path_part.strip(), (symbol_part.strip() or None)
    for index, char in enumerate(target):
        if char != ":":
            continue
        if target[index + 1 : index + 2] == "/":
            continue  # a scheme's colon: `https://…`
        if "/" in target[index + 1 :]:
            continue  # a colon inside a directory name, before the last separator
        before, after = target[:index].strip(), target[index + 1 :].strip()
        if before and after:
            return before, after
    return target.strip(), None


@dataclass(frozen=True)
class GovernsTarget:
    """One ``governs:`` target, as written and as resolved (KYO-200).

    Both halves matter and they are kept apart on purpose. :attr:`raw` is what every
    rendering surface shows — a consumer who wrote ``adapters/alpha/client.py:fetch``
    should not read ``apps/server/src/app/adapters/alpha/client.py#fetch`` back
    out of a tool they asked to *check* their file. :attr:`paths` is what every
    mechanism uses — root-relative, base-resolved, so staleness, obligation raising and
    the renderers see an ordinary project path and need to know nothing about bases.

    :attr:`paths` is plural because a bare symbol legitimately resolves to more than one
    file, and collapsing that to the first would make an obligation depend on directory
    walk order.
    """

    #: Exactly the text the document wrote, separator and base included.
    raw: str
    #: One of :data:`TARGET_SHAPES`.
    shape: str
    #: The path half as written, or ``None`` for a bare-symbol target.
    path: str | None
    #: The symbol half, or ``None``.
    symbol: str | None
    #: Root-relative posix paths this resolved to. Empty when it did not resolve.
    paths: tuple[str, ...] = ()
    #: Why it did not resolve, or ``None`` when it did. The two are exclusive, and
    #: ``reason is None`` is the single test every consumer uses for "this resolves".
    reason: str | None = None
    #: KYO-397. For a :data:`SHAPE_SYMBOL` target, the subset of :attr:`paths` in which a
    #: region finder locates a *definition* of the symbol in the working tree — the file
    #: that declares ``OrderSnapshot.total_cents``, not the 109 that use it. Empty
    #: for every other shape, and empty for a symbol no finder can place, which is the
    #: state :func:`_symbol_target_raises` falls back to file level on and
    #: :func:`_undefined_symbol_notice` reports. Read by both, and by nothing else.
    defined_in: tuple[str, ...] = ()
    #: KYO-600. A recx module's ``path`` (:func:`kyosei.docs_recx.module_target`): it
    #: governs every changed path it prefixes, the file itself or anything under it as a
    #: directory, whether or not that path exists on either side. Matched by
    #: :func:`kyosei.docs_obligations._target_raises` against :attr:`path`, never against
    #: :attr:`paths`, so a deletion raises as an edit does. False for every target a
    #: ``governs:`` line writes.
    prefix: bool = False

    @property
    def resolved(self) -> bool:
        return self.reason is None


# ------------------------------------------------- KYO-109: docs as a query source
#
# A document read for querying rather than validation: the per-document state a Base over
# `source: docs` renders. One parser (`section_metadata`), one target-resolution rule
# (`docs_targets.resolve_target`), two consumers -- the check and the query source
# (docs/design/docs.md#kyo-109).


@dataclass(frozen=True)
class Document:
    """One of this project's Layer 3 documents, read for querying not for validation."""

    path: str  # relative to the project root, posix-style
    text: str
    sections: list[Section]
    #: KYO-600. ``markdown``, or ``recx-manifest`` for a RECX.yaml read by
    #: :mod:`kyosei.docs_recx`, whose sections are its entries and which has no title.
    format: str = "markdown"

    @property
    def governs(self) -> list[str]:
        """Every ``governs:`` target in the file, in the order it declares them, and
        in the spelling it declared them in (KYO-200: as written, never normalised).
        Includes a dead section's targets (KYO-204) — this is the rendering answer, and
        a dead section's ``governs:`` list is kept as history, not hidden from view."""
        return [target for section in self.sections for target in section.governs]

    @property
    def live_governs(self) -> list[str]:
        """Every ``governs:`` target a **non-dead** section declares (KYO-204).

        The mechanism's fallback, not the rendering answer: :func:`document_row` reaches
        for this instead of :attr:`governs` when :attr:`resolved` comes back empty, so
        that fallback cannot reintroduce a dead section's raw targets through the back
        door — a document whose only ``governs:``-bearing sections are dead, or that has
        none live at all, must not have :func:`governs_staleness` or
        :func:`broken_governs_targets` reasoning over what a dead section wrote, which is
        exactly the shape KYO-204.c3's test proves against.
        """
        return [
            target
            for section in self.sections
            if not section.dead
            for target in section.governs
        ]

    @property
    def resolved(self) -> tuple[GovernsTarget, ...]:
        """The same targets, split and resolved — the mechanism's view of
        :attr:`governs` (KYO-200)."""
        return tuple(target for section in self.sections for target in section.resolved)

    @property
    def title(self) -> str | None:
        """The file's first heading, or ``None`` for a file that has none. A recx
        manifest has none: its first module is not a title for the file (KYO-600)."""
        if self.format != "markdown":
            return None
        for section in self.sections:
            if section.level:
                return section.heading
        return None

    def body(self) -> str:
        """The prose a body-prose operator matches against: the whole document.

        Deliberately the whole file rather than the sections' bodies joined: a docs
        file has no frontmatter fence to strip by construction (the metadata is
        section-level), and a phrase that spans a heading is still a phrase in this
        document.
        """
        return self.text


__all__ = [
    "DEAD_STATUSES",
    "DEFAULT",
    "PREAMBLE",
    "SECTION_METADATA_KEYS",
    "Document",
    "DocumentSet",
    "Section",
    "SectionStatusSet",
    "declared_section_status_sets",
    "document_set",
    "heading_anchor",
    "link_values",
    "read_document",
    "section_is_dead",
    "section_metadata",
]
