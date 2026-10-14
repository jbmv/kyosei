"""Prove a module split moved code without changing it (KYO-425).

A split child's first commit is a pure move: every top-level function, class and named
assignment of the module being split lands, unchanged, in some module of the package at the
move commit. This compares the two by AST (so a moved definition may change position and
surrounding blank lines, never its body, decorators or docstring) and exits non-zero on:

  MISSING  a definition of the old module that no module at HEAD defines any more
  DIFFERS  one that exists at HEAD, but not identical to the original in any module
  ADDED    a definition in a module new at HEAD that no split module had at BASE
  SHIM     the old module, still present at HEAD, imports back a name that moved out of it and
           never uses it -- an import kept only so callers can reach the name through the old
           path. An import the module itself uses (a registry calling the moved checks) is not
           a shim.

Imports are not compared: a move rewrites them by necessity. Nor is an old module's
``__all__`` compared whole: a split must shrink it, so it passes when it only LOST names --
one it gained is still a DIFFERS.

    .venv/bin/python tools/pure_move.py --base <ref> [--head <ref>] --old src/kyosei/export.py

Without --head the working tree is read. Run it on the move commit, before the docstring pass.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PACKAGE = "src/kyosei"


@dataclass(frozen=True)
class Finding:
    kind: str
    name: str
    detail: str

    def line(self) -> str:
        return f"{self.kind:8} {self.name}  {self.detail}"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def _read(root: Path, ref: str | None, path: str) -> str | None:
    if ref is None:
        target = root / path
        return target.read_text() if target.exists() else None
    try:
        return _git(root, "show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return None


def _modules(root: Path, ref: str | None) -> list[str]:
    if ref is None:
        return sorted(str(p.relative_to(root)) for p in (root / PACKAGE).glob("*.py"))
    listed = _git(root, "ls-tree", "--name-only", f"{ref}:{PACKAGE}").split()
    return sorted(f"{PACKAGE}/{name}" for name in listed if name.endswith(".py"))


def definitions(source: str) -> dict[str, ast.stmt]:
    """Top-level named definitions: functions, classes, and assignments to plain names."""
    found: dict[str, ast.stmt] = {}
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found[node.target.id] = node
    return found


def _all_names(node: ast.stmt | None) -> set[str] | None:
    value = getattr(node, "value", None)
    if not isinstance(value, (ast.List, ast.Tuple)):
        return None
    if not all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts):
        return None
    return {e.value for e in value.elts}


def _only_shrank(before: ast.stmt, after: ast.stmt | None) -> bool:
    """``__all__`` at HEAD names a subset of what it named at BASE."""
    old, new = _all_names(before), _all_names(after)
    return old is not None and new is not None and new <= old


def _imported_from_siblings(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _used_names(source: str) -> set[str]:
    """Every name the module reads outside its own import statements."""
    return {
        node.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def check(root: Path, base: str, head: str | None, old: list[str]) -> list[Finding]:
    before: dict[str, tuple[str, ast.stmt]] = {}
    for path in old:
        source = _read(root, base, path)
        if source is None:
            raise SystemExit(f"pure_move: {path} does not exist at {base}")
        for name, node in definitions(source).items():
            before[name] = (path, node)

    base_modules = set(_modules(root, base))
    after: dict[str, dict[str, ast.stmt]] = {}
    for path in _modules(root, head):
        source = _read(root, head, path)
        if source is not None:
            after[path] = definitions(source)

    findings: list[Finding] = []
    for name, (origin, node) in sorted(before.items()):
        wanted = ast.dump(node)
        homes = [path for path, defs in after.items() if name in defs]
        if not homes:
            findings.append(Finding("MISSING", name, f"(was {origin})"))
        elif name == "__all__" and origin in after and _only_shrank(node, after[origin].get(name)):
            continue
        elif not any(ast.dump(after[path][name]) == wanted for path in homes):
            findings.append(Finding("DIFFERS", name, f"(was {origin}; now {', '.join(homes)})"))

    for path, defs in sorted(after.items()):
        if path in base_modules:
            continue
        for name in sorted(defs):
            if name not in before:
                findings.append(Finding("ADDED", name, f"(in new module {path})"))

    for path in old:
        source = _read(root, head, path)
        if source is None:
            continue
        used = _used_names(source)
        for name in sorted(_imported_from_siblings(source) & before.keys()):
            if name not in definitions(source) and name not in used:
                findings.append(Finding("SHIM", name, f"({path} imports it back)"))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="the commit the split started from")
    parser.add_argument("--head", help="the move commit (default: the working tree)")
    parser.add_argument("--old", action="append", required=True, help="a module being split")
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    args = parser.parse_args(argv)
    findings = check(Path(args.root).resolve(), args.base, args.head, args.old)
    for finding in findings:
        print(finding.line())
    print(f"pure_move: {len(findings)} finding(s) over {', '.join(args.old)}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
