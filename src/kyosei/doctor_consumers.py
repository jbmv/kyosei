"""Doctor checks that every consumer string in ``schema.COLUMN_CONSUMERS`` and
``schema.CONFIG_CONSUMERS`` names something the package defines, and the doctor cache
the module index and the round-trip share. Registered in :data:`kyosei.doctor.CHECKS`.
"""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

from .config import CONFIG_NAME
from .schema import COLUMN_CONSUMERS, CONFIG_CONSUMERS, OPTIONAL_CONFIG_KEYS
from .store import Store

# ------------------------------------------------------------------ KYO-62: consumer strings
#
# Both sides of `COLUMN_CONSUMERS` and `CONFIG_CONSUMERS` are checked: the table and
# column NAMES against the live sqlite schema, and every consumer string against the
# package source. Without the second half a consumer string can name a deleted function
# and the check that makes law 2 answerable passes anyway
# (docs/design/doctor.md#kyo-62).
#
# A name is resolved against class members as well as module-level names, because
# `store.allocate` and `store.get_meta` are methods on `Store`, and an advisory check
# that fails on correct entries is the kind people switch off (KYO-33). The source is
# parsed rather than imported: parsing sees what a module *defines*, and runs no
# module's import side effects inside `kyo doctor`.

#: A consumer names ``module.symbol`` or ``module.Class.method``. Nothing else —
#: notably not a wildcard; see :func:`unresolvable_consumer`.
CONSUMER_REFERENCE_RE = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*){1,2}$")

#: The package's own source tree — the thing consumer strings are resolved against.
#: The registry ships *with* the tool, so this is always our source, never the
#: consumer project's.
PACKAGE_SOURCE_DIR = Path(__file__).resolve().parent

_ModuleSymbols = tuple[frozenset[str], dict[str, frozenset[str]]]


def _module_symbols(source: str) -> _ModuleSymbols:
    """``(module-level names, {class name: its members})`` for one module's source.

    Module-level names include imported ones: ``rebuild.stored_path_from_export``
    is a true reference (rebuild imports the function from ``schema`` and is the
    module that reads the column through it), and refusing it would be exactly the
    over-report this check must not make.
    """
    tree = ast.parse(source)
    top: set[str] = set()
    classes: dict[str, frozenset[str]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top.add(node.name)
        elif isinstance(node, ast.ClassDef):
            top.add(node.name)
            members: set[str] = set()
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    members.add(sub.name)
                elif isinstance(sub, ast.Assign):
                    members.update(t.id for t in sub.targets if isinstance(t, ast.Name))
                elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    members.add(sub.target.id)
            classes[node.name] = frozenset(members)
        elif isinstance(node, ast.Assign):
            top.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            top.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name != "*":
                    top.add(alias.asname or alias.name.split(".")[0])
    return frozenset(top), classes


# ------------------------------------------------------------------------ KYO-408
#
# What `kyo doctor` has already verified, kept so the `Stop` hook does not pay for it
# again. `kyo sweep && kyo doctor --hook` runs after every assistant turn, and without
# this every run re-parses the package's own source and rebuilds the whole store from
# export, at a cost linear in the size of the events ledger
# (docs/design/doctor.md#kyo-408).

#: Beside ``kyosei.db``, in a directory that ignores itself: the directory carries a
#: ``.gitignore`` of ``*`` written with its first file, the convention pytest's own cache
#: directory uses. Not a new line in ``kyosei/.gitignore``, because that file reaches an
#: existing project only when somebody runs `kyo install-procedures` -- until then every
#: stop would leave an untracked file in the store, which is the dirty-tree failure
#: :func:`kyosei.scaffold.store_gitignore_text` records for the WAL sidecars. Never under
#: ``export/``: this is derived local state, and one machine's verdict about its own
#: files is not something another clone can use.
DOCTOR_CACHE_DIRNAME = ".doctor-cache"
ROUNDTRIP_CACHE_NAME = "export-roundtrip.json"
MODULE_INDEX_CACHE_NAME = "module-index.json"
#: Bumped whenever either file's shape changes; any other value reads as "no cache".
DOCTOR_CACHE_FORMAT = 1


def doctor_cache_dir(store: Store) -> Path:
    """Where this store's doctor cache lives: the directory ``kyosei.db`` is in."""
    return store.config.db_path.parent / DOCTOR_CACHE_DIRNAME


def read_cache(path: Path) -> dict | None:
    """A cache file's content, or ``None`` for absent, unreadable or another format.

    Every failure reads as "nothing is cached", which costs a recomputation and never
    a verdict: nothing read from here is ever reported as a finding.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("format") != DOCTOR_CACHE_FORMAT:
        return None
    return data


def write_cache(path: Path, data: dict) -> None:
    """Write ``data`` atomically; a store doctor cannot write to simply goes uncached."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        ignore = path.parent / ".gitignore"
        if not ignore.is_file():
            ignore.write_text("# kyo doctor's local cache (KYO-408); never committed\n*\n")
        scratch = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        scratch.write_text(
            json.dumps({**data, "format": DOCTOR_CACHE_FORMAT}, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(scratch, path)
    except OSError:
        return


#: In-process memo for :func:`module_index`, keyed on the file's path and checked
#: against its (size, mtime) on every call -- so a long-lived process sees an edited
#: module.
_MODULE_SYMBOL_MEMO: dict[str, tuple[int, int, _ModuleSymbols]] = {}


def _symbols_to_json(symbols: _ModuleSymbols) -> dict:
    top, classes = symbols
    return {
        "top": sorted(top),
        "classes": {name: sorted(members) for name, members in classes.items()},
    }


def _symbols_from_json(entry: dict) -> _ModuleSymbols:
    return (
        frozenset(entry["top"]),
        {name: frozenset(members) for name, members in entry["classes"].items()},
    )


def module_index(
    source_dir: Path, cache_dir: Path | None = None
) -> dict[str, _ModuleSymbols] | None:
    """Every module in ``source_dir``, indexed by name — or ``None`` if there is none.

    ``None`` means "this build cannot see its own source" (a sourceless install),
    and every caller treats that as *say nothing*. Reporting 360 broken references
    because the .py files are absent would be the loudest possible version of the
    over-reporting failure this check is written to avoid.

    KYO-408: a module is parsed only when its (size, mtime) differs from the last parse,
    remembered in-process and, when ``cache_dir`` is given, in
    :data:`MODULE_INDEX_CACHE_NAME` there -- the source changes when the build does, and
    the ``Stop`` hook runs this in a fresh process after every turn.
    """
    cache_path = cache_dir / MODULE_INDEX_CACHE_NAME if cache_dir is not None else None
    on_disk: dict | None = None
    dirty = False
    index: dict[str, _ModuleSymbols] = {}
    present: set[str] = set()
    for path in sorted(source_dir.glob("*.py")):
        try:
            stat = path.stat()
        except OSError:
            continue
        stamp = (stat.st_size, stat.st_mtime_ns)
        present.add(path.name)
        memo = _MODULE_SYMBOL_MEMO.get(str(path))
        if memo is not None and memo[:2] == stamp:
            index[path.stem] = memo[2]
            continue
        if on_disk is None:
            cached = read_cache(cache_path) if cache_path is not None else None
            same_dir = cached is not None and cached.get("source_dir") == str(source_dir)
            on_disk = dict(cached.get("modules", {})) if same_dir else {}
        entry = on_disk.get(path.name)
        symbols: _ModuleSymbols | None = None
        if isinstance(entry, dict) and [entry.get("size"), entry.get("mtime_ns")] == list(
            stamp
        ):
            try:
                symbols = _symbols_from_json(entry)
            except (KeyError, TypeError, AttributeError):
                symbols = None
        if symbols is None:
            try:
                symbols = _module_symbols(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            on_disk[path.name] = {
                "size": stamp[0],
                "mtime_ns": stamp[1],
                **_symbols_to_json(symbols),
            }
            dirty = True
        _MODULE_SYMBOL_MEMO[str(path)] = (*stamp, symbols)
        index[path.stem] = symbols
    if on_disk is not None and cache_path is not None and index:
        stale = set(on_disk) - present
        if dirty or stale:
            for name in stale:
                del on_disk[name]
            write_cache(cache_path, {"source_dir": str(source_dir), "modules": on_disk})
    return index or None


def unresolvable_consumer(ref: str, index: dict[str, _ModuleSymbols]) -> str | None:
    """``None`` if ``ref`` names something that exists, else why it does not.

    Resolves ``module.symbol``, ``module.Class.method``, and — the case a first
    attempt at this got wrong — a bare ``module.method`` naming a method of *some*
    class the module defines, which is how ``store.allocate`` and ``store.get_meta``
    are written.
    """
    if not CONSUMER_REFERENCE_RE.match(ref):
        return (
            f"{ref!r} is not a consumer reference: name one reader as `module.symbol` "
            "or `module.Class.method` (a wildcard resolves to nothing checkable — "
            "list the readers explicitly)"
        )
    module, *rest = ref.split(".")
    symbols = index.get(module)
    if symbols is None:
        return f"{ref} names module {module}, which this package does not have"
    top, classes = symbols
    if len(rest) == 1:
        name = rest[0]
        if name in top or any(name in members for members in classes.values()):
            return None
        return (
            f"{ref} names nothing in {module}.py — no module-level symbol {name}, "
            f"and no class defined there has a {name}"
        )
    class_name, attribute = rest
    if class_name not in classes:
        return f"{ref} names class {class_name}, which {module}.py does not define"
    if attribute not in classes[class_name]:
        return f"{ref} names nothing on {module}.py's {class_name}"
    return None


def unresolvable_consumer_failures(
    registry: str,
    sites: list[tuple[str, str]],
    *,
    source_dir: Path | None = None,
    cache_dir: Path | None = None,
) -> list[str]:
    """One failure line per distinct unresolvable reference in ``sites``.

    ``sites`` is ``(where, reference)`` pairs. Grouped by reference rather than by
    site: a single deleted function is one fact about the registry, and printing it
    once per site buries every other finding under it (docs/design/doctor.md#kyo-62).
    """
    index = module_index(source_dir or PACKAGE_SOURCE_DIR, cache_dir)
    if index is None:  # sourceless install — say nothing rather than everything
        return []
    grouped: dict[str, tuple[str, list[str]]] = {}
    for where, ref in sites:
        why = unresolvable_consumer(ref, index)
        if why is None:
            continue
        grouped.setdefault(ref, (why, []))[1].append(where)
    failures = []
    for ref in sorted(grouped):
        why, where = grouped[ref]
        first = where[0]
        more = f" (+{len(where) - 1} more)" if len(where) > 1 else ""
        failures.append(
            f"schema.{registry} declares {ref} as a consumer of {first}{more}, but {why} "
            "— name the reader that exists, or drop the entry (law 2)"
        )
    return failures


def check_column_consumers(store: Store) -> list[str]:
    """Law 2, made mechanical.

    Compares the live schema against ``COLUMN_CONSUMERS`` in both directions, so a
    column added without a registered reader fails here rather than rotting quietly.
    KYO-62: and resolves every consumer string, so a *registered* reader that no
    longer exists fails here too.
    """
    failures: list[str] = []
    live: dict[str, set[str]] = {}
    for row in store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ):
        table = row["name"]
        live[table] = {c["name"] for c in store.conn.execute(f"PRAGMA table_info({table})")}

    for table in sorted(set(live) - set(COLUMN_CONSUMERS)):
        failures.append(f"table {table} has no entry in schema.COLUMN_CONSUMERS (law 2)")
    for table in sorted(set(COLUMN_CONSUMERS) - set(live)):
        failures.append(f"schema.COLUMN_CONSUMERS names table {table}, which does not exist")

    for table in sorted(set(live) & set(COLUMN_CONSUMERS)):
        declared = COLUMN_CONSUMERS[table]
        for column in sorted(live[table] - set(declared)):
            failures.append(
                f"{table}.{column} is stored but no code path is registered as reading it "
                "(law 2: ship the checker in the same commit as the field)"
            )
        for column in sorted(set(declared) - live[table]):
            failures.append(f"schema.COLUMN_CONSUMERS names {table}.{column}, which does not exist")
        for column, consumers in sorted(declared.items()):
            if column in live[table] and not consumers:
                failures.append(f"{table}.{column} declares an empty consumer list (law 2)")

    sites = [
        (f"{table}.{column}", ref)
        for table, columns in sorted(COLUMN_CONSUMERS.items())
        for column, refs in sorted(columns.items())
        for ref in refs
    ]
    failures.extend(
        unresolvable_consumer_failures(
            "COLUMN_CONSUMERS", sites, cache_dir=doctor_cache_dir(store)
        )
    )
    return failures


def check_config_consumers(store: Store) -> list[str]:
    """Every config key has a reader, that reader exists, and every reader's key is present.

    KYO-62: the middle clause closes the same law-2 hole ``COLUMN_CONSUMERS`` had —
    the strings are the same shape, written by the same hand, and drift the same way.
    """
    import yaml

    raw = yaml.safe_load(store.config.config_path.read_text(encoding="utf-8")) or {}
    failures = [
        f"{CONFIG_NAME} key {key!r} has no registered consumer (law 2)"
        for key in sorted(set(raw) - set(CONFIG_CONSUMERS))
    ]
    failures += [
        f"{CONFIG_NAME} is missing key {key!r}, which {', '.join(CONFIG_CONSUMERS[key])} reads"
        # KYO-60: an optional key's absence *is* its value — no layout was named at
        # init, nobody has accepted publication — so reporting it here would fail
        # `kyo doctor` in every project written before the key existed, which is
        # all of them, including this one.
        for key in sorted(set(CONFIG_CONSUMERS) - OPTIONAL_CONFIG_KEYS - set(raw))
    ]
    sites = [
        (f"{CONFIG_NAME} key {key!r}", ref)
        for key, refs in sorted(CONFIG_CONSUMERS.items())
        for ref in refs
    ]
    failures += unresolvable_consumer_failures(
        "CONFIG_CONSUMERS", sites, cache_dir=doctor_cache_dir(store)
    )
    return failures
