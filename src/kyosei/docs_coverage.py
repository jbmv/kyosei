"""Which of a project's code no document section governs (KYO-801, from KYO-797).

A ``governs:`` line under a heading names the code that section describes, and a branch
that changes that code raises an obligation to read the section. Code that no section
names raises nothing, so a document can fall behind it with no one asked. This module
measures that: it lists the tracked code files that no live section governs, and it reads
the project's ``docs_coverage:`` declaration in ``kyosei/config.yml``.

WHAT COUNTS AS CODE has two rules (proposal section 3). With a declared ``code:`` list it
is every tracked file one of those globs matches. Without one it is a heuristic: a tracked
file with a source-language suffix that is not in a document set, not under ``kyosei/`` or
``ideas/``, and not under a directory named like a test, fixture, dependency or build
directory. A heuristic may support a notice and should not fail a build, so the level
``problem`` requires the declared list (:func:`parse`).

WHAT COUNTS AS GOVERNED is what ``kyo docs check`` and the obligation join would say: a
file one live section's resolved ``governs:`` target names, or that a recx module path
prefixes. A file that is reached only by a ``<doc>#<anchor>`` pointer comment under
``src/`` (KYO-500) raises an obligation for the region around the comment and no more, so
it is listed apart and is not counted as governed.

Read-only, and opens no store: it reads the working tree, ``git ls-files`` and the
declaration. The doctor checks and ``kyo docs coverage`` are its two consumers.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import docs_obligations, docs_parse, docs_regions, docs_targets
from .config import config_path_for, guard_glob_matches, read_raw_config
from .contracts import GUARDED_BRANCH_DIRS
from .errors import KyoseiError
from .git_queries import run_git

#: The config key this module reads.
DOCS_COVERAGE_KEY = "docs_coverage"

LEVEL_OFF = "off"
LEVEL_NOTICE = "notice"
LEVEL_PROBLEM = "problem"
#: The values ``level:`` may take. Absent means nobody has answered, and acts as
#: :data:`LEVEL_NOTICE` (:func:`effective_level`).
LEVELS: tuple[str, ...] = (LEVEL_OFF, LEVEL_NOTICE, LEVEL_PROBLEM)

#: The keys ``docs_coverage:`` may carry, and the two an ``unmapped`` entry may.
KEYS: tuple[str, ...] = ("level", "code", "unmapped")
UNMAPPED_KEYS: tuple[str, ...] = ("glob", "why")

#: A target that resolves to more files than this is reported as broad: one section
#: governing ``src/**`` satisfies a count while asking no one anything specific.
BROAD_TARGET_FILES = 20

#: The suffixes the default rule reads as source code: the languages
#: :data:`kyosei.docs_targets.SEARCHABLE_SUFFIXES` searches, and the others a project
#: that Kyosei serves is likely to hold. Markup, data and configuration files are not
#: in it, which is why the default rule omits a project's hook JSON and registries.
SOURCE_SUFFIXES: frozenset[str] = docs_targets.SEARCHABLE_SUFFIXES | frozenset(
    {
        ".swift", ".kt", ".kts", ".php", ".cs", ".scala", ".vue", ".svelte", ".lua",
        ".m", ".mm", ".hpp", ".cxx", ".dart", ".ex", ".exs", ".clj", ".hs", ".pl",
        ".bash", ".zsh", ".ps1", ".bat",
    }
)

#: Directories whose contents the default rule never reads as code.
NON_CODE_DIRS: frozenset[str] = frozenset(
    {"tests", "test", "__tests__", "fixtures", "node_modules", "vendor", "dist", "build"}
)

#: How many directories the doctor's one finding names.
FINDING_DIRECTORIES = 3

#: The command a reader runs for the full list and the steps.
COVERAGE_COMMAND = "kyo docs coverage"


# ---------------------------------------------------------------- the declaration


@dataclass(frozen=True)
class Unmapped:
    """One ``unmapped`` entry: a glob whose files are not expected to be governed, and why."""

    glob: str
    why: str


@dataclass(frozen=True)
class CoverageConfig:
    """``docs_coverage:`` as declared. Every field is empty or ``None`` for a project that
    has declared nothing, which is every project written before the key."""

    #: ``off``, ``notice`` or ``problem``; ``None`` where nobody has answered.
    level: str | None = None
    #: The declared code globs; ``None`` where the default rule applies.
    code: tuple[str, ...] | None = None
    unmapped: tuple[Unmapped, ...] = ()

    @property
    def declared(self) -> bool:
        return self.level is not None or self.code is not None or bool(self.unmapped)


def effective_level(config: CoverageConfig) -> str:
    """The level acted on: what was declared, or the notice when nothing was."""
    return config.level or LEVEL_NOTICE


def _example(path: Path) -> str:
    return (
        f"write it as\n  {DOCS_COVERAGE_KEY}:\n    level: notice   # off | notice | problem\n"
        "    code:           # optional; required for problem\n"
        "      - src/**/*.py\n"
        "    unmapped:       # optional\n"
        "      - glob: src/**/__init__.py\n"
        "        why: no behaviour of its own\n"
        f"in {path}, or delete the key"
    )


def _refusal(path: Path, problem: str) -> KyoseiError:
    return KyoseiError(
        f"{path} key '{DOCS_COVERAGE_KEY}' {problem} — hand edit {path}: {_example(path)}"
    )


def _globs(value: object, path: Path, what: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise _refusal(path, f"{what} must be a non-empty list of globs, or absent")
    out = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise _refusal(path, f"{what} must hold non-empty strings; found {entry!r}")
        out.append(entry.strip())
    return tuple(out)


def parse(value: object, path: Path, *, code_root: Path | None = None) -> CoverageConfig:
    """``docs_coverage:`` into its parts, refusing anything else by name.

    The refusals: a value that is not a mapping, a key other than :data:`KEYS`, a level
    other than :data:`LEVELS`, a ``code`` or ``unmapped`` that is not the list it must be,
    an ``unmapped`` entry without a one-line ``why`` or with a key besides ``glob`` and
    ``why``, and ``level: problem`` with no ``code:``. The last one prints a draft
    ``code:`` list derived from ``code_root``'s tracked files, which parses.

    Parsed by its readers through :func:`read_config`, as ``guard_tests`` is, and not by
    :func:`kyosei.config.load_config`: a malformed value is named by `kyo doctor` and
    refuses `kyo docs coverage`, and never blinds a hook.
    """
    if value is None:
        return CoverageConfig()
    if not isinstance(value, dict):
        raise _refusal(path, f"must be a mapping; found {type(value).__name__}")
    unknown = sorted(str(key) for key in set(value) - set(KEYS))
    if unknown:
        raise _refusal(
            path, f"carries key(s) it does not read: {', '.join(unknown)} (it reads "
            f"{', '.join(KEYS)})"
        )
    level = value.get("level")
    if level is False:
        # YAML 1.1 reads a bare `off` as false. It is the level the writer meant.
        level = LEVEL_OFF
    if level is not None and level not in LEVELS:
        raise _refusal(path, f"level must be one of {', '.join(LEVELS)}; found {level!r}")
    code = _globs(value["code"], path, "code") if "code" in value else None
    unmapped: list[Unmapped] = []
    if "unmapped" in value:
        entries = value["unmapped"]
        if not isinstance(entries, list) or not entries:
            raise _refusal(path, "unmapped must be a non-empty list of {glob, why}, or absent")
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                raise _refusal(
                    path, f"unmapped entry {index} must be a mapping with glob and why; "
                    f"found {entry!r}"
                )
            extra = sorted(str(key) for key in set(entry) - set(UNMAPPED_KEYS))
            if extra:
                raise _refusal(
                    path, f"unmapped entry {index} carries key(s) it does not read: "
                    f"{', '.join(extra)} (it reads {', '.join(UNMAPPED_KEYS)})"
                )
            glob, why = entry.get("glob"), entry.get("why")
            if not isinstance(glob, str) or not glob.strip():
                raise _refusal(path, f"unmapped entry {index} needs a glob (a string)")
            if not isinstance(why, str) or not why.strip() or "\n" in why.strip():
                raise _refusal(
                    path, f"unmapped entry {index} ({glob.strip()}) needs a one-line why, "
                    "so that the list can be reviewed by someone who did not write it"
                )
            unmapped.append(Unmapped(glob.strip(), why.strip()))
    if level == LEVEL_PROBLEM and code is None:
        raise KyoseiError(
            f"{path} key '{DOCS_COVERAGE_KEY}' sets level: problem with no code: list. A "
            "failure needs a list the project declared, because the default rule is a "
            "heuristic about which files are source. Hand edit the file: add this under "
            f"'{DOCS_COVERAGE_KEY}:', review it, and keep level: problem:\n"
            f"{draft_text(code_root)}\n"
            "or set level: notice, which accepts the heuristic"
        )
    return CoverageConfig(level=level, code=code, unmapped=tuple(unmapped))


def read_config(root: Path, code_root: Path | None = None) -> CoverageConfig:
    """``docs_coverage`` from this project's ``kyosei/config.yml``, validated."""
    return parse(
        read_raw_config(root).get(DOCS_COVERAGE_KEY), config_path_for(root), code_root=code_root
    )


# ---------------------------------------------------------------- the files


def tracked_files(code_root: Path) -> list[str] | None:
    """Every file git tracks under ``code_root``, root-relative; ``None`` where git could
    not say (not a repository, git missing). Tracked files only, so ignored build output
    never counts as code."""
    try:
        result = run_git(code_root, "ls-files", "-z", text=False)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return sorted(
        name.decode("utf-8", errors="replace") for name in result.stdout.split(b"\0") if name
    )


def _guarded(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in GUARDED_BRANCH_DIRS)


def _suffix(path: str) -> str:
    return Path(path).suffix.lower()


def _default_code(path: str) -> bool:
    if _guarded(path) or _suffix(path) not in SOURCE_SUFFIXES:
        return False
    return not NON_CODE_DIRS.intersection(part.lower() for part in path.split("/")[:-1])


def _matches(globs: Sequence[str], path: str) -> bool:
    return any(guard_glob_matches(glob, path) for glob in globs)


def draft_code_globs(files: Sequence[str]) -> list[str]:
    """A ``code:`` list for ``files``: one glob per directory and source suffix that the
    default rule finds code in. Directories are the first path component, or the first two
    under ``apps``, ``packages``, ``libs`` and ``services``, where the first is a grouping."""
    found: set[tuple[str, str]] = set()
    for path in files:
        if not _default_code(path):
            continue
        parts = path.split("/")
        if len(parts) == 1:
            found.add(("", _suffix(path)))
            continue
        depth = 2 if parts[0] in {"apps", "packages", "libs", "services"} and len(parts) > 2 else 1
        found.add(("/".join(parts[:depth]), _suffix(path)))
    return [
        (f"{directory}/**/*{suffix}" if directory else f"*{suffix}")
        for directory, suffix in sorted(found)
    ]


def draft_text(code_root: Path | None) -> str:
    """The ``code:`` block :func:`draft_code_globs` drafts for ``code_root``, as the YAML
    to paste under ``docs_coverage:``. A project whose files cannot be listed gets the
    shape with one placeholder glob, which parses."""
    files = tracked_files(code_root) if code_root is not None else None
    globs = draft_code_globs(files or []) or ["src/**/*.py"]
    return "  code:\n" + "\n".join(f"    - {glob}" for glob in globs)


# ---------------------------------------------------------------- the measurement


@dataclass(frozen=True)
class SectionReach:
    """One governing target that reaches more files than :data:`BROAD_TARGET_FILES`."""

    doc_path: str
    section: str
    target: str
    files: int


@dataclass(frozen=True)
class DocumentReach:
    """How many of one document's live headed sections govern a file."""

    doc_path: str
    sections: int
    governing: int
    #: Headings of the live headed sections with no ``governs:`` target.
    governing_nothing: tuple[str, ...]


@dataclass(frozen=True)
class SymbolOnly:
    """One governed file that every governing target narrows to a symbol (KYO-832)."""

    path: str
    #: The symbols the targets name in it, sorted.
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class Coverage:
    """What :func:`measure` found."""

    #: False where the project has no document at all, so nothing could govern.
    has_documents: bool
    #: False where the code files could not be listed (no git).
    listed: bool
    #: Whether a ``code:`` list was declared (otherwise the default rule found the code).
    code_declared: bool
    code: tuple[str, ...]
    governed: tuple[str, ...]
    #: Code that no section governs and no ``unmapped`` entry names, pointer-only included.
    ungoverned: tuple[str, ...]
    #: The subset of ``ungoverned`` that a ``src/`` pointer comment reaches.
    pointer_only: tuple[str, ...]
    #: Code an ``unmapped`` glob names.
    excluded: tuple[str, ...]
    broad_targets: tuple[SectionReach, ...]
    documents: tuple[DocumentReach, ...]
    #: The governed files that no target governs whole (KYO-832): each of their targets
    #: is narrowed to a symbol's region, so a change outside those regions, a new
    #: function included, raises nothing. Reported, and no part of :func:`finding`.
    symbol_only: tuple[SymbolOnly, ...] = ()
    #: ``unmapped`` globs that match no tracked file.
    unmatched_unmapped: tuple[str, ...] = ()

    @property
    def in_neither(self) -> tuple[str, ...]:
        """Ungoverned code that no pointer reaches either."""
        pointed = set(self.pointer_only)
        return tuple(path for path in self.ungoverned if path not in pointed)


def _pointer_reached(
    root: Path, docs: Sequence[docs_parse.Document], documents_in: docs_parse.DocumentSet,
    files: Sequence[str],
) -> set[str]:
    """Tracked files under ``src/`` carrying a ``<doc>#<anchor>`` pointer to a live heading
    of a document a ``pointer_obligations`` set enumerates (KYO-500)."""
    pointable = docs_obligations._pointable(root, docs, documents_in)  # noqa: SLF001
    if not pointable:
        return set()
    pattern = re.compile(
        rf"(?<![\w/.-])({'|'.join(re.escape(p) for p in sorted(pointable, key=len, reverse=True))})"
        r"#([A-Za-z0-9][\w-]*)"
    )
    reached: set[str] = set()
    for path in docs_obligations.pointer_sources(list(files)):
        try:
            text = (root / path).read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        for match in pattern.finditer(text):
            if match.group(2).lower() in pointable[match.group(1)]:
                reached.add(path)
                break
    return reached


def _governs(path: str, targets: Sequence[docs_parse.GovernsTarget]) -> bool:
    for target in targets:
        if target.prefix:
            prefix = (target.path or "").strip("/")
            if not prefix or path == prefix or path.startswith(prefix + "/"):
                return True
        elif path in target.paths:
            return True
    return False


def _symbol_only(
    code_root: Path, governed: Sequence[str], targets: Sequence[docs_parse.GovernsTarget]
) -> tuple[SymbolOnly, ...]:
    """The files of ``governed`` that every target governing them narrows to a symbol.

    A target governs a file whole when it names no symbol, when it is a recx module's
    prefix, when the file's suffix has no region reader, or when the reader finds no
    region for the symbol in the file as it stands: in each case
    :func:`kyosei.docs_obligations._target_raises` raises on any change to the file. A
    bare-symbol target narrows the files that define the symbol and, where some file
    defines it, raises nothing for the others, so it is counted for the first and left
    out for the second. A file is reported when at least one target narrows it and
    none governs it whole.
    """
    narrowed: dict[str, set[str]] = {}
    whole: set[str] = set()
    wanted = set(governed)
    texts: dict[str, str | None] = {}

    def placed(path: str, symbol: str) -> bool:
        if path not in texts:
            try:
                texts[path] = (code_root / path).read_text(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                texts[path] = None
        text = texts[path]
        return text is not None and bool(docs_regions.symbol_regions(path, text, symbol))

    for target in targets:
        if target.prefix or not target.symbol:
            if target.prefix:
                prefix = (target.path or "").strip("/")
                whole.update(
                    p for p in wanted
                    if not prefix or p == prefix or p.startswith(prefix + "/")
                )
            else:
                whole.update(p for p in target.paths if p in wanted)
            continue
        for path in target.paths:
            if path not in wanted or path in whole:
                continue
            if target.path is None and target.defined_in:
                if path in target.defined_in:
                    narrowed.setdefault(path, set()).add(target.symbol)
            elif docs_regions.narrows_by_symbol(path) and placed(path, target.symbol):
                narrowed.setdefault(path, set()).add(target.symbol)
            else:
                whole.add(path)
    return tuple(
        SymbolOnly(path, tuple(sorted(narrowed[path])))
        for path in governed
        if path in narrowed and path not in whole
    )


def measure(
    code_root: Path, documents_in: docs_parse.DocumentSet, config: CoverageConfig
) -> Coverage:
    """The coverage of the tracked code in ``code_root`` by ``documents_in``'s sections."""
    docs = docs_targets.documents(code_root, documents_in=documents_in)
    files = tracked_files(code_root)
    if not docs or files is None:
        return Coverage(
            has_documents=bool(docs), listed=files is not None,
            code_declared=config.code is not None, code=(), governed=(), ungoverned=(),
            pointer_only=(), excluded=(), broad_targets=(), documents=(),
            unmatched_unmapped=(),
        )
    document_paths = {document.path for document in docs}
    if config.code is not None:
        code = [p for p in files if _matches(config.code, p) and not _guarded(p)]
    else:
        code = [p for p in files if _default_code(p)]
    code = [p for p in code if p not in document_paths]

    unmapped_globs = [entry.glob for entry in config.unmapped]
    excluded = [p for p in code if _matches(unmapped_globs, p)]
    unmatched = tuple(
        glob for glob in unmapped_globs if not any(guard_glob_matches(glob, p) for p in files)
    )
    counted = [p for p in code if p not in set(excluded)]

    live_targets: list[docs_parse.GovernsTarget] = []
    broad: list[SectionReach] = []
    reaches: list[DocumentReach] = []
    for document in docs:
        if _guarded(document.path):
            continue
        sections = [s for s in document.sections if s.level and not s.dead]
        empty = []
        for section in sections:
            targets = [t for t in section.resolved if t.resolved]
            if not targets:
                empty.append(section.heading)
            live_targets.extend(targets)
            for target in targets:
                if len(target.paths) > BROAD_TARGET_FILES:
                    broad.append(
                        SectionReach(document.path, section.heading, target.raw, len(target.paths))
                    )
        reaches.append(
            DocumentReach(
                document.path, len(sections), len(sections) - len(empty), tuple(empty)
            )
        )
    governed = [p for p in counted if _governs(p, live_targets)]
    governed_set = set(governed)
    ungoverned = [p for p in counted if p not in governed_set]
    pointed = _pointer_reached(code_root, docs, documents_in, ungoverned)
    return Coverage(
        has_documents=True,
        listed=True,
        code_declared=config.code is not None,
        code=tuple(counted),
        governed=tuple(governed),
        ungoverned=tuple(ungoverned),
        pointer_only=tuple(p for p in ungoverned if p in pointed),
        excluded=tuple(excluded),
        broad_targets=tuple(broad),
        documents=tuple(reaches),
        symbol_only=_symbol_only(code_root, governed, live_targets),
        unmatched_unmapped=unmatched,
    )


def directory_of(path: str) -> str:
    """The directory a path is listed under: its own, or ``.`` at the root."""
    return path.rsplit("/", 1)[0] if "/" in path else "."


def by_directory(paths: Sequence[str]) -> list[tuple[str, list[str]]]:
    """``(directory, file names)`` for each directory holding ``paths``, the one holding
    the most first."""
    grouped: dict[str, list[str]] = {}
    for path in paths:
        grouped.setdefault(directory_of(path), []).append(path.rsplit("/", 1)[-1])
    return sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))


def _summary_directories(paths: Sequence[str]) -> list[tuple[str, int]]:
    """The directories holding the most of ``paths``, grouped two components deep."""
    counts = Counter("/".join(directory_of(p).split("/")[:2]) for p in paths)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:FINDING_DIRECTORIES]


# ---------------------------------------------------------------- the doctor's findings


def finding(coverage: Coverage, config: CoverageConfig) -> list[str]:
    """The findings the doctor's one active check reports, as a notice or as a problem.

    Nothing for a project with no documents, nothing where every counted file is governed
    or listed as unmapped, and one ``unmapped`` glob that matches no tracked file per
    finding as the unresolved ``governs:`` target is, so that an exclusion cannot outlive
    its path.
    """
    if not coverage.has_documents or not coverage.listed:
        return []
    out = []
    if coverage.ungoverned:
        named = ", ".join(f"{d} {n}" for d, n in _summary_directories(coverage.ungoverned))
        rule = "declared" if coverage.code_declared else "source-suffix"
        tail = (
            f"`{COVERAGE_COMMAND}` lists them and the steps to a map; `{COVERAGE_COMMAND} "
            "--level notice|problem|off` records the decision"
        )
        if effective_level(config) == LEVEL_PROBLEM:
            tail = (
                f"`{COVERAGE_COMMAND}` lists them; add each to a section's `governs:` line or "
                f"to `unmapped:` with a reason under `{DOCS_COVERAGE_KEY}:` in kyosei/config.yml"
            )
        out.append(
            f"{len(coverage.ungoverned)} of {len(coverage.code)} {rule} code files are "
            "governed by no document section, so a change to them asks nobody to check a "
            f"document (largest: {named}). {tail}"
        )
    for glob in coverage.unmatched_unmapped:
        out.append(
            f"{DOCS_COVERAGE_KEY} unmapped glob {glob!r} matches no tracked file, so the "
            f"exclusion outlived its path -- hand edit kyosei/config.yml: correct or remove "
            "the entry"
        )
    return out


# ---------------------------------------------------------------- the level, written


def _scalar(level: str) -> str:
    """``level`` as YAML writes it: ``off`` is quoted, since a bare one reads as false."""
    import yaml

    return yaml.safe_dump(level).splitlines()[0]


def write_level(root: Path, level: str) -> Path:
    """Record ``level`` under ``docs_coverage:`` in ``kyosei/config.yml``, keeping the
    rest of the key and of the file, and the file's schema version as it stands.

    The key is appended as a block when absent and its ``level:`` line replaced when
    present, so the project's comments survive; any other shape of the file is
    re-rendered from its values, as :func:`kyosei.config.record_publication_acknowledgement`
    does. The result is read back and compared with what was meant before it is written.
    """
    import yaml

    from .config import render_config

    if level not in LEVELS:
        raise KyoseiError(f"level must be one of {', '.join(LEVELS)}; got {level!r}")
    path = config_path_for(root)
    raw = read_raw_config(root)
    if not path.is_file() or "project_prefix" not in raw:
        raise KyoseiError(
            f"{path} does not exist or has no project_prefix, so there is nothing to record "
            "the level in -- run this in a Kyosei project"
        )
    current = raw.get(DOCS_COVERAGE_KEY)
    block = dict(current) if isinstance(current, dict) else {}
    block["level"] = level
    expected = dict(raw)
    expected[DOCS_COVERAGE_KEY] = block
    before = path.read_text(encoding="utf-8")
    after = None
    if current is None and DOCS_COVERAGE_KEY not in raw:
        candidate = (
            before.rstrip("\n")
            + f"\n\n# KYO-801. Whether, and how strictly, to report code that no document "
            "section governs.\n"
            f"{DOCS_COVERAGE_KEY}:\n  level: {_scalar(level)}\n"
        )
        if yaml.safe_load(candidate) == expected:
            after = candidate
    elif isinstance(current, dict) and "level" in current:
        lines = before.splitlines(keepends=True)
        start = next(
            (i for i, line in enumerate(lines) if re.match(rf"{DOCS_COVERAGE_KEY}[ \t]*:", line)),
            None,
        )
        if start is not None:
            for index in range(start + 1, len(lines)):
                if lines[index].strip() and not lines[index][0].isspace():
                    break
                match = re.match(r"^([ \t]+)level[ \t]*:", lines[index])
                if match:
                    lines[index] = f"{match.group(1)}level: {_scalar(level)}\n"
                    candidate = "".join(lines)
                    if yaml.safe_load(candidate) == expected:
                        after = candidate
                    break
    if after is None:
        after = render_config(expected)
    if after != before:
        path.write_text(after, encoding="utf-8")
    return path

