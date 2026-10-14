"""RECX.yaml as a governed document: the ``recx-manifest`` document format (KYO-600).

A document set in ``kyosei/documents.yml`` may declare ``format: recx-manifest``. Its one
file, ``RECX.yaml`` at the code root, is read here rather than by the markdown reader in
:mod:`kyosei.docs_parse`, and its sections come from the manifest's own structure:

* ``(preamble)``: the top-level fields. It governs ``recx_manifest.project_governs``.
* ``module <id>``: one per entry in ``modules``. It governs every changed path that the
  module's ``path`` prefixes, or the targets ``recx_manifest.module_governs[<id>]`` names in
  its place.
* ``evidence <id>``: one per entry in ``evidence``. It governs
  ``recx_manifest.evidence_governs[<id>]``.

Everything after that is the ordinary obligation machinery: the sections are
:class:`~kyosei.docs_parse.Section` values, a module's path is a
:class:`~kyosei.docs_parse.GovernsTarget` with :attr:`~kyosei.docs_parse.GovernsTarget.prefix`
set, and :func:`kyosei.docs_obligations._target_raises` matches it as a prefix of the
changed path.

The reader parses with PyYAML and never imports ``recx_cli``, so governance works whether
or not recx is installed. It does not validate the manifest against recx's schema; recx's
own check does that. It refuses to derive sections only when it cannot read them safely:
invalid YAML, an anchor, an alias or a merge key (forbidden by recx's SPEC.md, rule 6), or
no ``modules`` list of mappings with a string ``id`` and ``path``. The file is then one
ungoverned ``(preamble)`` section, and :attr:`ManifestSections.problems` says why, which
``kyo docs check`` reports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from .docs_parse import (
    PREAMBLE,
    Document,
    GovernsTarget,
    Section,
    TargetResolver,
    section_metadata,
)

#: The one file a ``recx-manifest`` set governs: recx's SPEC.md, "The file", puts one per
#: repository at its root.
MANIFEST_PATH = "RECX.yaml"

#: The section heading a module entry becomes, and the one an evidence entry becomes.
MODULE_HEADING = "module {id}"
EVIDENCE_HEADING = "evidence {id}"


def module_heading(module_id: str) -> str:
    return MODULE_HEADING.format(id=module_id)


def evidence_heading(evidence_id: str) -> str:
    return EVIDENCE_HEADING.format(id=evidence_id)


@dataclass(frozen=True)
class RecxManifestSet:
    """What ``documents.yml`` declared about the project's RECX.yaml.

    Read from the ``recx_manifest:`` block of the set declaring ``format: recx-manifest``
    by :func:`declared_manifest`. Every target list is in the ``governs:`` dialect and
    stays in ``documents.yml``, so the public manifest carries nothing only Kyosei reads.
    """

    #: The document set's name in ``documents.yml``.
    name: str
    #: Targets of the ``(preamble)`` section.
    project_governs: tuple[str, ...] = ()
    #: Targets of each ``evidence <id>`` section, by evidence id.
    evidence_governs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: Narrowed targets of a ``module <id>`` section, by module id. A module named here is
    #: governed by these targets in place of its path prefix.
    module_governs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def path(self) -> str:
        return MANIFEST_PATH

    @property
    def says(self) -> str:
        return f"{MANIFEST_PATH} read as a recx manifest (document set {self.name!r})"


def _target_list(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _target_map(value: object) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): _target_list(targets) for key, targets in value.items()}


def declared_manifest(registry: object) -> RecxManifestSet | None:
    """The ``recx-manifest`` set ``registry`` (the loaded ``documents.yml``) declares, or
    ``None`` when it declares none, which is every project that has not opted in."""
    from .registry import DOCUMENT_FORMAT_RECX

    for name in getattr(registry, "declared_names", ()):
        entry = registry[name]  # type: ignore[index]
        if entry.get("format") != DOCUMENT_FORMAT_RECX:
            continue
        block = entry.get("recx_manifest")
        block = block if isinstance(block, Mapping) else {}
        return RecxManifestSet(
            name=name,
            project_governs=_target_list(block.get("project_governs")),
            evidence_governs=_target_map(block.get("evidence_governs")),
            module_governs=_target_map(block.get("module_governs")),
        )
    return None


def project_manifest(kyosei_dir: Path) -> RecxManifestSet | None:
    """The ``recx-manifest`` set the project's ``documents.yml`` (in ``kyosei_dir``)
    declares, or ``None`` when it declares none or the file is one this build refuses.

    A refused ``documents.yml`` is reported by ``kyo docs check``; the callers here, the
    brief and ``kyo report``'s trailer warning, must not fail over it (KYO-614)."""
    from .errors import KyoseiError
    from .registry import load_document_registry

    try:
        return declared_manifest(load_document_registry(kyosei_dir))
    except KyoseiError:
        return None


# ------------------------------------------------------------------ reading the manifest


@dataclass(frozen=True)
class ManifestEntry:
    """One module or evidence entry, with the lines it occupies in the file."""

    #: ``module`` or ``evidence``.
    kind: str
    id: str
    #: The module's ``path`` as written; ``None`` for an evidence entry.
    path: str | None
    #: 0-based line span ``[first, end)``.
    first_line: int
    end_line: int

    @property
    def heading(self) -> str:
        return module_heading(self.id) if self.kind == "module" else evidence_heading(self.id)


@dataclass(frozen=True)
class Manifest:
    """RECX.yaml as far as governance reads it, or why it could not be read."""

    entries: tuple[ManifestEntry, ...] = ()
    #: Why no section was derived, or ``None`` when sections were.
    problem: str | None = None

    @property
    def modules(self) -> dict[str, str]:
        """Each module's ``path``, by id, in file order."""
        return {e.id: e.path for e in self.entries if e.kind == "module" and e.path is not None}


def _unsafe_construct(text: str) -> str | None:
    """Why ``text`` uses a YAML feature SPEC.md rule 6 forbids, or ``None``."""
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        if isinstance(event, yaml.AliasEvent):
            return f"it uses an alias (*{event.anchor}) at line {event.start_mark.line + 1}"
        anchor = getattr(event, "anchor", None)
        if anchor is not None:
            return f"it defines an anchor (&{anchor}) at line {event.start_mark.line + 1}"
        if (
            isinstance(event, yaml.ScalarEvent)
            and event.value == "<<"
            and event.implicit[0]
        ):
            return f"it uses a merge key (<<) at line {event.start_mark.line + 1}"
    return None


_STR_TAG = "tag:yaml.org,2002:str"


def _span(node: yaml.Node) -> tuple[int, int]:
    end = node.end_mark.line + (1 if node.end_mark.column > 0 else 0)
    return node.start_mark.line, max(end, node.start_mark.line + 1)


def _scalar(node: yaml.MappingNode, key: str) -> str | None:
    """The string under ``key`` in a mapping node, or ``None`` when there is none. A value
    the safe loader would read as a number, a boolean or a null is not a string."""
    for key_node, value_node in node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            if isinstance(value_node, yaml.ScalarNode) and value_node.tag == _STR_TAG:
                return value_node.value
            return None
    return None


def _items(top: yaml.MappingNode, key: str) -> yaml.Node | None:
    for key_node, value_node in top.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            return value_node
    return None


def read_manifest(text: str) -> Manifest:
    """Read ``text`` as a recx manifest, or say why its sections cannot be derived."""
    try:
        unsafe = _unsafe_construct(text)
        if unsafe is not None:
            return Manifest(problem=f"{unsafe}, which recx's SPEC.md rule 6 forbids")
        top = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        detail = " ".join(str(exc).split())
        return Manifest(problem=f"it is not valid YAML ({detail})")
    if not isinstance(top, yaml.MappingNode):
        return Manifest(problem="its top level is not a mapping")
    modules = _items(top, "modules")
    if not isinstance(modules, yaml.SequenceNode):
        return Manifest(problem="it has no modules list")
    entries: list[ManifestEntry] = []
    for position, item in enumerate(modules.value, start=1):
        if not isinstance(item, yaml.MappingNode):
            return Manifest(problem=f"modules entry {position} is not a mapping")
        module_id, path = _scalar(item, "id"), _scalar(item, "path")
        if module_id is None or not module_id.strip():
            return Manifest(problem=f"modules entry {position} has no string id")
        if path is None or not path.strip():
            return Manifest(
                problem=f"modules entry {position} ({module_id}) has no string path"
            )
        entries.append(ManifestEntry("module", module_id.strip(), path.strip(), *_span(item)))
    evidence = _items(top, "evidence")
    if isinstance(evidence, yaml.SequenceNode):
        for item in evidence.value:
            if not isinstance(item, yaml.MappingNode):
                continue
            evidence_id = _scalar(item, "id")
            if evidence_id is not None and evidence_id.strip():
                entries.append(
                    ManifestEntry("evidence", evidence_id.strip(), None, *_span(item))
                )
    return Manifest(entries=tuple(entries))


# ------------------------------------------------------------------ module path targets


def _normalised(path: str) -> str:
    """A module path as a root-relative prefix: no leading ``./``, no trailing ``/``."""
    while path.startswith("./"):
        path = path[2:]
    return path.rstrip("/")


def prefix_matches(path: str, changed_path: str) -> bool:
    """Whether a module ``path`` governs ``changed_path`` (recx's ``drift.affected_modules``
    rule): the path itself, or anything under it as a directory. A trailing ``/`` is not
    required, so a module path naming a directory without one still covers its files."""
    prefix = _normalised(path)
    if not prefix:
        return True
    return changed_path == prefix or changed_path.startswith(prefix + "/")


def module_target(path: str, root: Path | None = None) -> GovernsTarget:
    """The prefix target a module's ``path`` becomes.

    Resolved against ``root`` only to say whether it names anything on disk, which is
    ``kyo docs check``'s unresolved-target problem. Matching never consults the disk: a
    deleted file under the path still raises.
    """
    prefix = _normalised(path)
    reason = None
    if root is not None and not (root / prefix).exists():
        reason = (
            f"{path!r}: a module path in {MANIFEST_PATH}, and nothing exists at "
            f"{prefix or '.'} under {root}"
        )
    return GovernsTarget(
        raw=path,
        shape="path",
        path=path,
        symbol=None,
        paths=(prefix,) if reason is None and prefix else (),
        reason=reason,
        prefix=True,
    )


# ------------------------------------------------------------------ sections


@dataclass(frozen=True)
class ManifestProblem:
    """Something ``kyo docs check`` reports about the manifest or its declaration."""

    section: str
    target: str
    reason: str


@dataclass(frozen=True)
class ManifestSections:
    sections: list[Section]
    problems: tuple[ManifestProblem, ...] = ()


def _section(
    heading: str,
    level: int,
    body: list[str],
    written: Sequence[str],
    resolved: tuple[GovernsTarget, ...],
) -> Section:
    metadata = section_metadata([])
    metadata["governs"] = list(written)
    return Section(heading, level, body, metadata, resolved, False)


def manifest_sections(
    text: str,
    declaration: RecxManifestSet | None = None,
    resolve: TargetResolver | None = None,
    root: Path | None = None,
) -> ManifestSections:
    """RECX.yaml's sections, in file order: ``(preamble)``, each module, each evidence entry.

    ``resolve`` resolves the ``governs:``-dialect targets from ``documents.yml``, as it does
    a markdown section's; without one they are carried as written. A module's own path is
    always a resolved prefix target, because matching it needs no resolver.
    """
    declaration = declaration or RecxManifestSet(name="")
    lines = text.splitlines()
    manifest = read_manifest(text)

    def dialect(targets: Sequence[str]) -> tuple[GovernsTarget, ...]:
        return tuple(resolve(target) for target in targets) if resolve else ()

    project = declaration.project_governs
    if manifest.problem is not None:
        return ManifestSections(
            [_section(PREAMBLE, 0, lines, (), ())],
            (
                ManifestProblem(
                    PREAMBLE,
                    MANIFEST_PATH,
                    f"{MANIFEST_PATH} cannot be read as a recx manifest because "
                    f"{manifest.problem}, so it is one ungoverned (preamble) section and no "
                    f"module or evidence section governs anything; fix the file (recx check "
                    f"names what the schema requires)",
                ),
            ),
        )
    taken = {line for e in manifest.entries for line in range(e.first_line, e.end_line)}
    preamble_body = [line for number, line in enumerate(lines) if number not in taken]
    sections = [_section(PREAMBLE, 0, preamble_body, project, dialect(project))]
    for entry in manifest.entries:
        body = lines[entry.first_line : entry.end_line]
        if entry.kind == "module":
            narrowed = declaration.module_governs.get(entry.id)
            if narrowed:
                sections.append(_section(entry.heading, 1, body, narrowed, dialect(narrowed)))
                continue
            assert entry.path is not None
            sections.append(
                _section(entry.heading, 1, body, [entry.path], (module_target(entry.path, root),))
            )
        else:
            targets = declaration.evidence_governs.get(entry.id, ())
            sections.append(_section(entry.heading, 1, body, targets, dialect(targets)))
    problems: list[ManifestProblem] = []
    module_ids = list(manifest.modules)
    evidence_ids = [e.id for e in manifest.entries if e.kind == "evidence"]
    for key, known, what in (
        ("module_governs", module_ids, "module"),
        ("evidence_governs", evidence_ids, "evidence entry"),
    ):
        for named in getattr(declaration, key):
            if named not in known:
                problems.append(
                    ManifestProblem(
                        PREAMBLE,
                        f"recx_manifest.{key}.{named}",
                        f"documents.yml names {what} {named!r} under recx_manifest.{key}, "
                        f"and {MANIFEST_PATH} has no {what} with that id (it has: "
                        f"{', '.join(known) or 'none'}); rename the key to an id the manifest "
                        f"declares, or remove it",
                    )
                )
    return ManifestSections(sections, tuple(problems))


def manifest_document(
    text: str,
    declaration: RecxManifestSet | None = None,
    resolve: TargetResolver | None = None,
    root: Path | None = None,
) -> Document:
    return Document(
        path=MANIFEST_PATH,
        text=text,
        sections=manifest_sections(text, declaration, resolve, root).sections,
        format="recx-manifest",
    )


def widen_with_sides(
    document: Document, other_sides: Sequence[str], declaration: RecxManifestSet | None
) -> Document:
    """``document`` with the module paths the other sides of a diff declare added.

    The module paths are read from both the base and the branch side, and their union is
    used: a branch that
    moves a module and updates its ``path`` still raises on the old location, and a module
    the branch removed still raises on its own section. A module whose targets
    ``module_governs`` narrowed is left alone, since its targets come from
    ``documents.yml`` and not from either side. A side that cannot be read adds nothing.
    """
    narrowed = set(declaration.module_governs) if declaration is not None else set()
    sections = list(document.sections)
    by_heading = {section.heading: index for index, section in enumerate(sections)}
    for text in other_sides:
        manifest = read_manifest(text)
        if manifest.problem is not None:
            continue
        lines = text.splitlines()
        for entry in manifest.entries:
            if entry.kind != "module" or entry.id in narrowed or entry.path is None:
                continue
            target = module_target(entry.path)
            index = by_heading.get(entry.heading)
            if index is None:
                by_heading[entry.heading] = len(sections)
                sections.append(
                    _section(
                        entry.heading,
                        1,
                        lines[entry.first_line : entry.end_line],
                        [entry.path],
                        (target,),
                    )
                )
                continue
            section = sections[index]
            have = {_normalised(t.path or "") for t in section.resolved if t.prefix}
            if _normalised(entry.path) in have:
                continue
            sections[index] = replace(
                section,
                metadata={**section.metadata, "governs": [*section.governs, entry.path]},
                resolved=(*section.resolved, target),
            )
    return replace(document, sections=sections)


def manifest_at(root: Path, revision: str) -> str | None:
    """RECX.yaml's text at ``revision`` in the repository at ``root``, or ``None``."""
    from .git_queries import run_git

    try:
        shown = run_git(root, "show", f"{revision}:./{MANIFEST_PATH}")
    except OSError:
        return None
    return shown.stdout if shown.returncode == 0 else None


def diff_sides(root: Path, against: str, branch: str) -> list[str]:
    """RECX.yaml at the merge base of ``against`` and ``branch``, and at ``branch``: the
    two sides whose module paths a branch's obligations are joined against."""
    from .git_queries import run_git

    sides: list[str] = []
    try:
        base = run_git(root, "merge-base", against, branch)
    except OSError:
        base = None
    revisions = [branch]
    if base is not None and base.returncode == 0 and base.stdout.strip():
        revisions.insert(0, base.stdout.strip())
    for revision in revisions:
        text = manifest_at(root, revision)
        if text is not None:
            sides.append(text)
    return sides


# ------------------------------------------------------------------ the Recx-Checked trailer


#: KYO-614: the commit trailer recx honours (RECX-24): ``Recx-Checked: <module-id>[, ...]``.
#: A trailer naming module M on commit C tells recx's drift reminder that M's entry was read
#: against C's tree, so the reminder disregards M's changes in C and in the session's
#: earlier commits C descends from. The key matches case-insensitively, the trailer may
#: repeat, and ids are separated by commas or spaces. Plain git: nothing here imports recx.
CHECKED_TRAILER = "Recx-Checked"

#: The first recx-cli release that honours :data:`CHECKED_TRAILER` (tag v0.1.1).
CHECKED_TRAILER_SINCE = "0.1.1"


def answered_modules(docs_verdicts: Sequence[Mapping] | None) -> list[str]:
    """The module ids a report's ``docs_verdicts[]`` answers, in report order, once each:
    every entry on RECX.yaml whose section is a ``module <id>`` heading."""
    prefix = MODULE_HEADING.format(id="")
    found: list[str] = []
    for entry in docs_verdicts or ():
        if not isinstance(entry, Mapping) or entry.get("doc_path") != MANIFEST_PATH:
            continue
        section = entry.get("section")
        if not isinstance(section, str) or not section.startswith(prefix):
            continue
        module_id = section[len(prefix):].strip()
        if module_id and module_id not in found:
            found.append(module_id)
    return found


def checked_modules(root: Path, revision: str) -> set[str] | None:
    """The module ids the :data:`CHECKED_TRAILER` trailers on commit ``revision`` name, or
    ``None`` when git cannot read that commit. Read with git's own trailer parser
    (``%(trailers:key=...)``, whose key match is case-insensitive), never from the body."""
    from .git_queries import run_git

    try:
        shown = run_git(
            root,
            "log",
            "-1",
            f"--format=%(trailers:key={CHECKED_TRAILER},valueonly=true,unfold=true)",
            revision,
            "--",
        )
    except OSError:
        return None
    if shown.returncode != 0:
        return None
    return {token for token in shown.stdout.replace(",", " ").split() if token}


def unchecked_modules(
    root: Path, branch: str | None, docs_verdicts: Sequence[Mapping] | None
) -> dict:
    """KYO-614.c2: which module ids ``docs_verdicts[]`` answers that no
    :data:`CHECKED_TRAILER` trailer on ``branch``'s tip commit names.

    Returns ``{"branch", "answered", "unchecked", "detail"}``; ``detail`` is ``None`` when
    the tip was read and says why when it was not (no branch declared, or git could not
    read it), in which case ``unchecked`` is every answered id. Only the tip counts:
    recx disregards changes in commits a trailer's commit descends from, never later ones,
    so a trailer on an earlier commit leaves the tip's own changes reminded.
    """
    answered = answered_modules(docs_verdicts)
    result: dict = {"branch": branch, "answered": answered, "unchecked": [], "detail": None}
    if not answered:
        return result
    if not branch:
        result["unchecked"] = list(answered)
        result["detail"] = "the dispatch declared no branch, so no tip commit could be read"
        return result
    named = checked_modules(root, branch)
    if named is None:
        result["unchecked"] = list(answered)
        result["detail"] = f"git could not read the tip commit of {branch}"
        return result
    result["unchecked"] = [module_id for module_id in answered if module_id not in named]
    return result


__all__ = [
    "CHECKED_TRAILER",
    "CHECKED_TRAILER_SINCE",
    "MANIFEST_PATH",
    "Manifest",
    "ManifestEntry",
    "ManifestProblem",
    "ManifestSections",
    "RecxManifestSet",
    "answered_modules",
    "checked_modules",
    "declared_manifest",
    "diff_sides",
    "evidence_heading",
    "manifest_document",
    "manifest_sections",
    "module_heading",
    "module_target",
    "prefix_matches",
    "project_manifest",
    "read_manifest",
    "unchecked_modules",
    "widen_with_sides",
]
