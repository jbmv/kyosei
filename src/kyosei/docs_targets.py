"""Resolving a section's ``governs:`` targets to project files, and enumerating the
documents with their targets resolved.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Sequence
from pathlib import Path

from .docs_parse import (
    DEFAULT,
    Document,
    DocumentSet,
    GovernsTarget,
    TargetResolver,
    _iter_docs_files,
    read_document,
    split_target,
)
from .docs_regions import narrows_by_symbol, symbol_regions
from .prose import contains_phrase


def target_resolver(root: Path, documents_in: DocumentSet) -> TargetResolver:
    """The resolver a whole scan shares (KYO-200).

    One :class:`_SymbolIndex` per scan and not per target, which is the difference
    between reading the searchable tree once and reading it once per bare-symbol target
    (docs/design/docs.md#kyo-200).
    """
    index = _SymbolIndex(root, documents_in.bases)

    def resolve(target: str) -> GovernsTarget:
        return resolve_target(root, target, documents_in.bases, index=index)

    return resolve


#: A target naming a file and nothing narrower.
SHAPE_PATH = "path"
#: A target naming a file and a symbol inside it — ``path#symbol`` or ``path:symbol``.
SHAPE_PATH_SYMBOL = "path-symbol"
#: A target naming a symbol with **no path at all** — ``global_queue.brain.step``,
#: ``OrderSnapshot.total_cents``, ``ImportRun`` (docs/design/docs.md#kyo-200).
SHAPE_SYMBOL = "symbol"

TARGET_SHAPES: tuple[str, ...] = (SHAPE_PATH, SHAPE_PATH_SYMBOL, SHAPE_SYMBOL)

#: Suffixes that make a slash-free target a *file* rather than a symbol
#: (``docker-compose.yml``, ``Makefile.inc``). A closed list, because the alternative —
#: "any short dotted suffix" — reads ``global_queue.brain.step`` as a file named
#: ``.step`` and reads ``OrderSnapshot.subtotal_cents`` as one named ``.subtotal_cents``,
#: and those are the two commonest shapes in the corpus this was measured against. Ground
#: truth overrides it in both directions: a slash-free target that *does* exist as a
#: file under some base is a path whatever its suffix (``Makefile``, ``Dockerfile``),
#: which is what keeps this list from having to be complete.
FILE_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".sql",
        ".md", ".rst", ".txt", ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg",
        ".html", ".css", ".c", ".h", ".cc", ".cpp", ".go", ".rs", ".rb", ".java",
    }
)

#: Which files a bare-symbol target may be found in: **code**, and not prose.
#:
#: Narrower than :data:`FILE_SUFFIXES` on purpose, and the narrowing is correctness
#: rather than cost. A ``governs:`` target naming a symbol is a claim about code, and
#: the most reliable place to find the string ``ImportRun`` in any project is the
#: document that just declared it governs ``ImportRun`` — so a search including markdown
#: resolves every symbol target to the document itself, and no symbol target could ever
#: fail. That is a consumer project's checker's ``CODE_TOKEN_EXTS`` distinction, and it
#: is here for the same reason.
SEARCHABLE_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".sql",
        ".c", ".h", ".cc", ".cpp", ".go", ".rs", ".rb", ".java",
    }
)

#: The directories never walked looking for a symbol. Cost, this time: an unbounded
#: search reads a consumer's ``node_modules`` once per target.
_UNSEARCHED_DIRS: frozenset[str] = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
     ".pytest_cache", ".ruff_cache", "dist", "build", ".tox", ".next", "target"}
)

#: Every segment of a dotted ``module.sub.symbol`` reference must look like this.
#: Borrowed, deliberately, from a consumer project's own ``check_governs_drift.py``
#: (``MODULE_PATH_SEGMENT_RE``) so the two tools classify the same string the same way.
_MODULE_SEGMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: The identifier-shaped words of a symbol target, for the whole-word search.
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: Below this length a token is not evidence of anything. ``id`` appears in every file
#: any project has ever had; requiring it would make a symbol target resolve everywhere,
#: which is indistinguishable from not checking it. That consumer checker draws the
#: same line for the same reason (``MIN_TOKEN_LEN``).
_MIN_TOKEN_LEN = 3


def symbol_tokens(symbol: str) -> frozenset[str]:
    """The words a file must all carry for ``symbol`` to be found in it.

    ``ImportRun`` is one token and ``OrderSnapshot.total_cents`` is two, and
    requiring *all* of them is what lets one rule serve both — which is exactly that
    consumer checker's "the two-token symbol match already used for 1-dot SYMBOL
    entries handles either shape identically". Requiring the dotted string *verbatim*
    would not work at all: a class and its attribute are declared on different lines,
    so ``OrderSnapshot.total_cents`` as a literal appears in no source file.
    """
    return frozenset(
        token for token in _TOKEN_RE.findall(symbol) if len(token) >= _MIN_TOKEN_LEN
    )


def carries_symbol(text: str, symbol: str) -> bool:
    """Does ``text`` carry ``symbol``? The one symbol test, for all three shapes.

    Two rules, tried in that order, and the order is what keeps this only ever *more*
    permissive than what shipped before it:

    1. the whole symbol as a wrap-tolerant phrase (:func:`kyosei.prose.contains_phrase`,
       KYO-66) — the pre-KYO-200 rule, unchanged, so nothing that resolved before stops
       resolving and a target naming a heading that wrapped still matches;
    2. failing that, **every one of its tokens** as a whole word (:func:`symbol_tokens`).

    The second rule is what a dotted symbol needs, and a dotted symbol turns up to the
    right of a separator as readily as on its own: ``adapters/base.py:Adapter.prepare``
    names a method, and a class and its method are declared on different lines, so the
    literal ``Adapter.prepare`` appears in no source file
    (docs/design/docs.md#kyo-200). Using one rule whether or not the symbol was
    written after a path is also c2 in the direction nobody thinks to check:
    ``path:Class.attr`` and a bare ``Class.attr`` are the same question about the same
    file, and two answers to it would be the drift this record exists to remove.
    """
    if contains_phrase(text, symbol, whole_word=True):
        return True
    tokens = symbol_tokens(symbol)
    return bool(tokens) and all(
        token in text and contains_phrase(text, token, whole_word=True) for token in tokens
    )


def target_shape(path: str, symbol: str | None) -> str:
    """Which of :data:`TARGET_SHAPES` a split target is, from its spelling alone.

    Spelling alone, with no filesystem — so the answer is the same for a project being
    parsed and one being unit-tested, and so :func:`resolve_target` can report *what it
    tried to resolve* even when nothing resolved. :func:`resolve_target` may still
    promote a :data:`SHAPE_SYMBOL` to :data:`SHAPE_PATH` when ground truth says the
    string is a real file; it never demotes in the other direction.

    A symbol half settles it. Otherwise a ``/`` makes it a path, a known file suffix
    (:data:`FILE_SUFFIXES`) makes it a path, and anything else — an undotted identifier
    like ``ImportRun``, a dotted ``Model.attribute`` or ``module.sub.symbol`` — is a
    symbol. That last clause is c6's rule and that consumer checker's rule at once:
    *no path separator and no file extension means a symbol*.
    """
    if symbol is not None:
        return SHAPE_PATH_SYMBOL
    if "/" in path:
        return SHAPE_PATH
    suffix = path[path.rfind(".") :].lower() if "." in path else ""
    if suffix in FILE_SUFFIXES:
        return SHAPE_PATH
    return SHAPE_SYMBOL


def _dotted_module(symbol: str) -> tuple[str, str] | None:
    """``global_queue.brain.step`` → ``("global_queue/brain.py", "step")``, or ``None``.

    A consumer project's ``tools/check_governs_drift.py:_dotted_module_symbol`` rule,
    matched deliberately rather than reinvented, including its **two-dot minimum**. A
    single dot is left alone because ``OrderSnapshot.subtotal_cents`` and
    ``constants.SCHEMA_SHA256`` are genuinely ambiguous between ``Class.attribute`` and
    ``module.symbol``, and reading the first as a module would look for a file called
    ``OrderSnapshot.py`` that no project has. The whole-word search the one-dot form
    falls through to handles either reading, which is why guessing is unnecessary.

    Every segment must be a plain identifier, which is what keeps a hostname
    (``api.example.com``, ``example.org/graphql``) from being read as a
    module path — a real hostname carries a hyphen or a bare TLD segment, and the ``/``
    case never reaches here because :func:`target_shape` called it a path.
    """
    parts = symbol.split(".")
    if len(parts) < 3 or not all(_MODULE_SEGMENT_RE.match(part) for part in parts):
        return None
    return "/".join(parts[:-1]) + ".py", parts[-1]


class _SymbolIndex:
    """Every searchable file under a set of bases, read once, for bare-symbol targets.

    A :data:`SHAPE_SYMBOL` target has no path to check, so the only honest way to
    resolve one is to look for it, and looking naively is a re-read of the tree per
    target. This reads each file at most once and answers every target from that
    (docs/design/docs.md#kyo-200).

    Cheap substring test before :func:`kyosei.prose.contains_phrase`, which is the
    expensive whole-word one: a token that is not present as a substring cannot be
    present as a word, so the regex runs only on real candidates.
    """

    def __init__(self, root: Path, bases: Sequence[str]) -> None:
        self._root = root
        self._bases = tuple(bases)
        self._files: list[tuple[str, str]] | None = None

    def _read(self) -> list[tuple[str, str]]:
        if self._files is not None:
            return self._files
        seen: dict[str, str] = {}
        for base in self._bases:
            start = _under_root(self._root, base)
            if start is None or not start.is_dir():
                continue
            # KYO-422: pruned, not filtered -- :data:`_UNSEARCHED_DIRS` are removed from
            # `dirnames` so they are never entered. A directory is excluded wherever it
            # sits below the base (never the base itself), symlinked directories are not
            # followed, and the order is the final sort below, not the walk's
            # (docs/design/docs.md#kyo-422).
            for directory, dirnames, filenames in os.walk(start):
                dirnames[:] = [name for name in dirnames if name not in _UNSEARCHED_DIRS]
                for filename in filenames:
                    path = Path(directory, filename)
                    if path.suffix.lower() not in SEARCHABLE_SUFFIXES or not path.is_file():
                        continue
                    rel = path.relative_to(self._root).as_posix()
                    if rel in seen:
                        continue
                    seen[rel] = path.read_text(encoding="utf-8", errors="replace")
        self._files = sorted(seen.items())
        return self._files

    def find(self, symbol: str) -> tuple[str, ...]:
        """Every file under the bases carrying every one of ``symbol``'s tokens.

        :func:`carries_symbol`'s second rule and not the whole of it: the phrase rule
        it tries first can only succeed where the token rule already does, so running
        it here would be one more regex per file per target for an answer that cannot
        differ (docs/design/docs.md#kyo-200).
        """
        tokens = symbol_tokens(symbol)
        if not tokens:
            return ()
        return tuple(
            rel
            for rel, text in self._read()
            if all(
                token in text and contains_phrase(text, token, whole_word=True)
                for token in tokens
            )
        )

    def defining(self, symbol: str, paths: Sequence[str]) -> tuple[str, ...]:
        """Which of ``paths`` (already :meth:`find` hits) define ``symbol`` (KYO-397).

        "Define" means the file's region finder (:func:`symbol_regions`) locates a region
        for it — the same instrument the obligation join then asks of each diff, so a
        file counted here is one the join would call a definition too. A Python file is
        first put through :func:`_may_define_in_python`, a regex that every file the AST
        walk could locate the symbol in must match, because parsing every hit for every
        target is a docs check nobody would run (docs/design/docs.md#kyo-397). Whatever the regex
        wrongly rejects is a file counted as a mention, which can only shrink this set,
        and an empty set raises MORE, not less.
        """
        texts = dict(self._read())
        maybe_py = _may_define_in_python(symbol)
        found: list[str] = []
        for rel in paths:
            text = texts.get(rel)
            if text is None or not narrows_by_symbol(rel):
                continue
            if rel.endswith((".py", ".pyi")) and not maybe_py(text):
                continue
            if symbol_regions(rel, text, symbol):
                found.append(rel)
        return tuple(found)


def _may_define_in_python(symbol: str) -> Callable[[str], bool]:
    """A cheap necessary condition for :func:`_python_regions` locating ``symbol``.

    A dotted ``A.b.c`` is located only under a ``def``/``class`` named ``A``; an undotted
    ``name`` only at a ``def``/``class`` of it or an assignment to it (``name =``,
    ``name:``, ``x = name =``). Over-inclusive on purpose — a keyword argument or a dict
    key matches too, and costs one parse.
    """
    first = re.escape(symbol.split(".", 1)[0].strip())
    pattern = rf"\b(?:def|class)\s+{first}\b"
    if "." not in symbol:
        pattern += rf"|\b{first}\s*(?::|=(?!=))"
    compiled = re.compile(pattern)
    return lambda text: compiled.search(text) is not None


def _under_root(root: Path, relative: str) -> Path | None:
    """``root / relative``, or ``None`` if that would land outside ``root``.

    The bases are validated at load time (:func:`kyosei.registry.relative_bases`) and
    the *targets* are not — they come from a document, which anyone may edit. So the
    containment check is here, on the join, rather than trusted from the config: a
    ``governs: ../../etc/passwd`` must fail to resolve, not read a file.
    """
    if not relative or relative == ".":
        return root
    candidate = Path(os.path.normpath(str(root / relative)))
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _first_existing(root: Path, bases: Sequence[str], relative: str) -> str | None:
    """The root-relative path of ``relative`` under the first base holding it as a file.

    c3's "resolves under the first listed base where it exists", spelled once. The
    return is root-relative because that is what every consumer downstream of the
    parser is entitled to assume it got.
    """
    for base in bases:
        candidate = _under_root(root, f"{base}/{relative}" if base and base != "." else relative)
        if candidate is not None and candidate.is_file():
            return candidate.relative_to(root).as_posix()
    return None


def _tried(bases: Sequence[str]) -> str:
    """The base list, named, for a failure message (c3).

    A resolver that searched three directories and reports only "no such file" hands
    the reader a fact they cannot act on, because the set it searched is the one thing
    they would need to change.
    """
    named = [base if base and base != "." else "the project root" for base in bases]
    return ", ".join(named) if named else "the project root"


# ------------------------------------------------- KYO-202: glob and brace shorthand
#
# Two path notations `resolve_target` reads besides a plain path: a single ``{a,b,c}``
# brace group naming several files in one line (``adapters/{a,b}/normalize.py``), and a
# shell glob resolving against whatever real files exist (``deploy/run_*.sh``). Where
# the brace rule comes from: docs/design/docs.md#kyo-202.
#
# The two are independent notations and compose: brace expansion happens BEFORE
# resolution, one target per alternative, and each alternative is then resolved exactly
# as any other path — AND semantics, every alternative must exist (mirroring a normal
# path target's all-or-nothing existence check, which is more useful for catching drift
# than silently tolerating one deleted file). A glob is its own resolution rule,
# with OR semantics — at least one file under the bases must match, and every match
# governs. An alternative that is itself a glob combines both, in that order.

#: A single ``{...}`` group. Matches a non-nested group only (``[^{}]*``), which is
#: enough to both find one and to notice a SECOND one (two matches means two groups).
_BRACE_GROUP_RE = re.compile(r"\{([^{}]*)\}")
#: A brace alternative that looks like a plain path token — that consumer checker's own
#: ``_BRACE_ALT_RE``, matched deliberately rather than widened: guessing at any other
#: shape (whitespace, a nested brace, a glob character inside the group) risks silently
#: mis-expanding something that was never meant to be a list.
_BRACE_ALT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _brace_expand(path_part: str) -> tuple[list[str], str | None]:
    """Expand a single ``{a,b,c}`` group in ``path_part``, or say why not (KYO-202.c1/c3).

    Three outcomes, as ``(alternatives, refusal)``:

    * no group at all — ``([path_part], None)``, a one-element list so every caller can
      iterate the result uniformly whether or not this did anything, matching the
      consumer checker's own ``expand_governs_entry`` return convention.
    * exactly one well-formed group — ``(expanded, None)``, one string per
      comma-separated alternative, everything else about the raw string untouched
      (``adapters/{a,b}/normalize.py`` becomes ``adapters/a/normalize.py`` and
      ``adapters/b/normalize.py``).
    * two or more groups — ``([], reason)``: refused by name (c3), not expanded into a
      guess about which group was meant.

    A single group that is **not** a well-formed comma list (fewer than two
    alternatives, or an alternative outside :data:`_BRACE_ALT_RE`'s plain path-token
    charset) is treated as carrying no group at all — the same tolerance that consumer
    checker's own function has for a group that "doesn't look like a plain comma list": left as
    literal text for the ordinary file-existence check to fail on its own terms, rather
    than a shape this function invents a special reason for.
    """
    groups = list(_BRACE_GROUP_RE.finditer(path_part))
    if len(groups) >= 2:
        return [], (
            f"{path_part!r}: a governs: target may carry at most one {{a,b,c}} brace "
            f"group, and this one carries {len(groups)}"
        )
    if not groups:
        return [path_part], None
    match = groups[0]
    alts = match.group(1).split(",")
    if len(alts) < 2 or not all(_BRACE_ALT_RE.match(alt) for alt in alts):
        return [path_part], None
    return [path_part[: match.start()] + alt + path_part[match.end() :] for alt in alts], None


#: Shell-glob metacharacters this format recognises — the same three
#: :mod:`fnmatch`/:meth:`pathlib.Path.glob` already do, and the only ones the record's
#: own example (``deploy/run_*.sh``) needs. Deliberately closed rather than "any
#: fnmatch-special character", matching the rest of this module's stated preference for
#: a narrow, named rule over a general one.
_GLOB_CHARS: frozenset[str] = frozenset({"*", "?", "["})


def _is_glob(path_part: str) -> bool:
    """Does ``path_part`` carry a shell-glob metacharacter (KYO-202)?"""
    return any(char in path_part for char in _GLOB_CHARS)


def _resolve_glob(
    root: Path, bases: Sequence[str], pattern: str
) -> tuple[tuple[str, ...], str | None]:
    """Every file under the bases ``pattern`` matches, or a reason naming the pattern if
    none does (KYO-202.c1/c3).

    :meth:`pathlib.Path.glob` semantics — the same dialect :func:`_iter_docs_files`
    already uses for a document set's own globs (``*`` stops at ``/``, ``**`` crosses
    it) — so a ``governs:`` glob and a ``documents.yml`` glob mean the same thing to the
    one person maintaining both. The first base with any match wins, the same "first
    hit" convention :func:`_first_existing` already uses, rather than a union across
    every base — a governed file lives in one place, and a project declaring three
    bases did not mean "match this glob in all three at once".
    """
    for base in bases:
        start = _under_root(root, base)
        if start is None or not start.is_dir():
            continue
        matches = sorted(
            p.relative_to(root).as_posix() for p in start.glob(pattern) if p.is_file()
        )
        if matches:
            return tuple(matches), None
    return (), (
        f"{pattern}: a shell-glob governs target, and no file under {_tried(bases)} "
        f"matches it"
    )


def resolve_target(
    root: Path,
    target: str,
    bases: Sequence[str] = (),
    *,
    index: _SymbolIndex | None = None,
) -> GovernsTarget:
    """One ``governs:`` target, split, classified and resolved against the filesystem.

    The single resolution rule, for all three shapes and both separators:

    * :data:`SHAPE_PATH` — the path must exist as a file under one of ``bases``.
    * :data:`SHAPE_PATH_SYMBOL` — that, and the file must carry the symbol
      (:func:`carries_symbol`), which begins with the same coarse wrap-tolerant phrase
      check ``cmd_records._validate_touches`` uses for a record's ``touches``, so the
      two cannot drift apart on anything that resolved before this record.
    * :data:`SHAPE_SYMBOL` — a dotted ``module.sub.symbol`` resolves as the module's
      file under the bases *plus* that symbol, exactly as if it had been written
      ``module/sub.py#symbol``; anything else resolves when every one of its tokens
      (:func:`symbol_tokens`) is a whole-word match in at least one searchable file
      under the bases, and the target then governs every file it was found in.

    ``bases`` empty means the project root, so a project that has declared no
    ``paths_relative_to`` gets precisely the pre-KYO-200 behaviour on precisely the
    pre-KYO-200 shapes. ``index`` is the shared :class:`_SymbolIndex` for a whole run;
    left out, one is built per call, which is correct and slow and fine for a test.

    KYO-202 widens the :data:`SHAPE_PATH`/:data:`SHAPE_PATH_SYMBOL` path half with two
    more notations, both expanded/matched *before* the plain file-existence check above,
    so every other rule in this function (the symbol check included) is unchanged and
    applies to what they produce:

    * a single ``{a,b,c}`` brace group (:func:`_brace_expand`) becomes one path per
      alternative, each then required to exist exactly as any other path (AND
      semantics) — two or more groups refuse by name rather than guess which was meant.
    * a shell glob (:func:`_is_glob`, :func:`_resolve_glob`) resolves when at least one
      file under the bases matches, and every match governs (OR semantics) — a glob
      matching nothing refuses by name rather than silently resolving to no files.
    """
    raw = target.strip()
    path_part, symbol_part = split_target(raw)
    bases = tuple(bases) or ("",)
    shape = target_shape(path_part, symbol_part)

    if shape == SHAPE_SYMBOL:
        return _resolve_symbol(root, raw, path_part, bases, index)

    if not path_part:
        return GovernsTarget(
            raw, shape, None, symbol_part,
            reason=(
                f"{raw!r}: a governs: target needs a path before its symbol separator"
            ),
        )

    alternatives, refusal = _brace_expand(path_part)
    if refusal is not None:
        return GovernsTarget(raw, shape, path_part, symbol_part, reason=refusal)

    resolved_paths: list[str] = []
    problems: list[str] = []
    for alt in alternatives:
        if _is_glob(alt):
            matches, glob_reason = _resolve_glob(root, bases, alt)
        else:
            found = _first_existing(root, bases, alt)
            matches = (found,) if found is not None else ()
            glob_reason = (
                None if found is not None else (
                    f"{alt}: no such file under {_tried(bases)} (governs target {raw!r})"
                )
            )
        if not matches:
            problems.append(glob_reason or f"{alt}: did not resolve")
            continue
        if not symbol_part:
            resolved_paths.extend(matches)
            continue
        for found in matches:
            text = (root / found).read_text(encoding="utf-8", errors="replace")
            if carries_symbol(text, symbol_part):
                resolved_paths.append(found)
            else:
                problems.append(f"{raw}: {symbol_part!r} not found in {found}")

    if problems or not resolved_paths:
        return GovernsTarget(
            raw, shape, path_part, symbol_part, reason="; ".join(problems)
        )
    return GovernsTarget(
        raw, shape, path_part, symbol_part, paths=tuple(dict.fromkeys(resolved_paths))
    )


def _resolve_symbol(
    root: Path,
    raw: str,
    symbol: str,
    bases: Sequence[str],
    index: _SymbolIndex | None,
) -> GovernsTarget:
    """A target that named a symbol and no path. See :func:`resolve_target`.

    The dotted-module branch is tried first and, when it resolves, the target is
    reported as a :data:`SHAPE_PATH_SYMBOL` — because that is what it turned out to be,
    and a consumer reading ``kyo docs check --json`` should see the shape that was
    resolved rather than the shape it was written in. A dotted string that is *not* a
    module path falls through to the whole-word search rather than failing, which is
    what keeps ``OrderSnapshot.total_cents`` working.
    """
    dotted = _dotted_module(symbol)
    if dotted is not None:
        module_path, module_symbol = dotted
        found = _first_existing(root, bases, module_path)
        if found is not None:
            text = (root / found).read_text(encoding="utf-8", errors="replace")
            if carries_symbol(text, module_symbol):
                return GovernsTarget(
                    raw, SHAPE_PATH_SYMBOL, module_path, module_symbol, paths=(found,)
                )
    searcher = _SymbolIndex(root, bases) if index is None else index
    hits = searcher.find(symbol)
    if not hits:
        return GovernsTarget(
            raw, SHAPE_SYMBOL, None, symbol,
            reason=(
                f"{raw!r}: a symbol target, and no code file under {_tried(bases)} "
                f"carries every word of it ({', '.join(sorted(symbol_tokens(symbol)))}) "
                f"as a whole word"
                + (
                    f" (nor is there a module {dotted[0]} defining {dotted[1]!r})"
                    if dotted is not None
                    else ""
                )
            ),
        )
    return GovernsTarget(
        raw, SHAPE_SYMBOL, None, symbol, paths=hits,
        defined_in=searcher.defining(symbol, hits),
    )


def _resolve_target(root: Path, target: str, bases: Sequence[str] = ()) -> str | None:
    """Why ``target`` does not resolve, or ``None`` if it does.

    The pre-KYO-200 signature, kept because it is the shape two callers want and
    because a boolean question deserves a boolean-shaped answer. It is
    :func:`resolve_target`'s :attr:`GovernsTarget.reason` and nothing more, so there is
    still one resolution rule rather than two that agree today.
    """
    return resolve_target(root, target, bases).reason


def documents(root: Path, *, documents_in: DocumentSet = DEFAULT) -> list[Document]:
    """Every file under ``root`` that ``documents_in`` selects, parsed once.

    Same walk :func:`check_docs` uses, through the same :func:`_iter_docs_files` and the
    same :class:`DocumentSet` — KYO-129.c4, and the reason it is a shared function
    rather than a shared intention: this is the KYO-109 query source behind ``kyo base
    run --source docs``, and a file the check can see must be a row here too
    (docs/design/docs.md#kyo-129).

    A project whose paths match nothing has no documents, which is the empty list rather
    than an error. The *reporting* of that emptiness is :class:`DocsCheck`'s job, on the
    command that claims to have checked something; a query that returns no rows has
    already said what it found.
    """
    found: list[Document] = []
    resolve = target_resolver(root, documents_in)
    for doc_path in _iter_docs_files(root, documents_in.globs):
        text = doc_path.read_text(encoding="utf-8", errors="replace")
        # KYO-600: a RECX.yaml a `recx-manifest` set declares is read as a manifest.
        found.append(
            read_document(
                doc_path.relative_to(root).as_posix(), text, documents_in, resolve, root
            )
        )
    return found


def broken_governs_targets(
    root: Path, targets: list[str], bases: Sequence[str] = ()
) -> list[str]:
    """Which of ``targets`` do not resolve — the query-side view of what
    :func:`check_docs` fails the build for, through the same
    :func:`resolve_target` so the banner and the build cannot disagree.

    ``bases`` defaults to empty, meaning the project root, which is what every caller
    predating KYO-200 meant by passing nothing."""
    return [target for target in targets if _resolve_target(root, target, bases) is not None]


__all__ = [
    "broken_governs_targets",
    "documents",
]
