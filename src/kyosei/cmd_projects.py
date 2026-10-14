"""``kyo projects list|add|remove`` — the machine's project registry, and its one writer (KYO-455).

``$KYOSEI_HOME/projects.json``
lists the projects this machine serves: ``kyo serve`` answers for exactly them and
``kyo controller`` starts sessions for them. Until now the desktop app and its
server both wrote it (``apps/desktop/src/main/projects.ts``, "TWO WRITERS AND A
PERSON"). From here the CLI is the writer — this command, and ``kyo init`` adding the
project it creates — and ``kyo serve`` only reads it (it is on ``serve.NOT_SERVED``).

KYO-379'S RULES, KEPT. A person may still edit the file, so:

- **Every write is atomic**: a temporary file beside it, flushed, then renamed over
  it. A reader never sees half a file.
- **Nothing is cached**: every call reads the file, so an edit made by anybody is
  what the next call sees.
- **A file that cannot be parsed is REFUSED by name, never read as empty.** Read as
  empty, the next ``add`` would replace the person's file with one entry and lose the
  rest. A file that does not exist yet is the ordinary empty list.
- **Every entry is kept as it was written**: ``add`` appends and ``remove`` drops one
  entry; any other key a person put on an entry survives both.

THE NAME. A project is named by its per-project home name
(``config.project_home_name``) — the name ``kyo serve`` puts in its URLs, through the
same function (:func:`kyosei.serve.project_name`), so the name this command prints is
the name a viewer types. ``add`` refuses a second project with a name already
registered, because a URL could then not say which one it means.
"""

from __future__ import annotations

import json
import os
import tempfile
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path

from .commands import Context, Output
from .errors import KyoseiError

#: The registry's file name, in the store home (KYO-379).
REGISTRY_FILE = "projects.json"

#: What a well-formed registry looks like, quoted in every refusal that asks for a fix.
REGISTRY_SHAPE = '{"projects": [{"root": "/path/to/project"}]}'


def registry_path(environ: dict[str, str] | None = None) -> Path:
    """``$KYOSEI_HOME/projects.json`` (``~/kyosei/projects.json`` by default)."""
    from .config import store_home

    return store_home(environ) / REGISTRY_FILE


def _unreadable(path: Path, why: str) -> KyoseiError:
    return KyoseiError(
        f"this machine's project registry {path} cannot be read: {why}. It is not read "
        f"as empty, and nothing is written to it until it is fixed: correct it by hand "
        f"(JSON, {REGISTRY_SHAPE}) or move it aside"
    )


def read_entries(path: Path) -> list[dict]:
    """The registry's entries as they are on disk now, each exactly as written."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as exc:
        raise _unreadable(path, str(exc)) from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _unreadable(path, f"it is not JSON ({exc})") from exc
    if not isinstance(raw, dict):
        raise _unreadable(path, 'it is not an object with a "projects" list')
    if not isinstance(raw.get("projects"), list):
        raise _unreadable(path, 'it has no "projects" list')
    entries: list[dict] = []
    for index, entry in enumerate(raw["projects"]):
        root = entry.get("root") if isinstance(entry, dict) else None
        if not isinstance(root, str) or not root.strip():
            raise _unreadable(path, f'projects[{index}] has no "root" path')
        entries.append(entry)
    return entries


def write_entries(path: Path, entries: list[dict]) -> None:
    """Replace the file whole: a temporary file beside it, flushed, then a rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"projects": entries}, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


# ------------------------------------------------------------------- entries


@dataclass(frozen=True)
class Registered:
    """One registry entry, as the server will serve it."""

    name: str
    root: str
    prefix: str | None
    problem: str | None

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "root": self.root,
            "prefix": self.prefix,
            "problem": self.problem,
        }


def _normal(root: str) -> Path:
    return Path(root).expanduser().resolve()


def describe(root: str) -> Registered:
    """An entry's served name, its prefix, and why it cannot be opened, if it cannot."""
    from .config import read_raw_config, resolve_layout
    from .serve import project_name

    name = project_name(root)
    candidate = _normal(root)
    if not candidate.is_dir():
        return Registered(name, root, None, f"there is no directory at {candidate}")
    try:
        layout = resolve_layout(explicit_root=candidate, required=False)
    except KyoseiError as exc:
        return Registered(name, root, None, str(exc))
    if not layout.configured:
        return Registered(name, root, None, f"no initialised Kyosei project at {candidate}")
    prefix = read_raw_config(layout.store_root).get("project_prefix")
    return Registered(name, root, prefix if isinstance(prefix, str) else None, None)


def registered(environ: dict[str, str] | None = None) -> list[Registered]:
    """Every entry, described, in the file's order."""
    return [describe(entry["root"]) for entry in read_entries(registry_path(environ))]


# ---------------------------------------------------------------------- acts


@dataclass(frozen=True)
class Added:
    project: Registered
    added: bool  # False: this root was already listed, and nothing was written
    registry: Path


def add(path: str | Path, environ: dict[str, str] | None = None) -> Added:
    """Register the project at ``path``; refuses a directory with no initialised project
    and a name another entry already has. Re-adding a listed root writes nothing."""
    from .config import refuse_real_home_from_a_dispatch_worktree, resolve_layout

    candidate = _normal(str(path))
    # KYO-535.c2: the one function that registers a project (`kyo projects add` and
    # `kyo init` both come here), so the real registry is refused from a running
    # dispatch's worktree whichever command asked.
    refuse_real_home_from_a_dispatch_worktree(
        environ if environ is not None else dict(os.environ),
        command="kyo projects add",
        what="register a project in projects.json",
        given=[("the directory named", candidate)],
    )
    registry = registry_path(environ)
    entries = read_entries(registry)  # an unreadable registry refuses before anything else
    if not candidate.is_dir():
        raise KyoseiError(
            f"there is no directory at {candidate}; name the directory of a Kyosei project "
            "(`kyo projects add /path/to/project`)"
        )
    layout = resolve_layout(explicit_root=candidate, required=False)
    if not layout.configured:
        raise KyoseiError(
            f"{candidate} holds no initialised Kyosei project (looked for kyosei/config.yml "
            "and for a .kyosei store link there, without searching above it); run `kyo init "
            f"{candidate}` to make one, or name the project's own top directory"
        )
    project = describe(str(candidate))
    for entry in entries:
        if _normal(entry["root"]) == candidate:
            return Added(project, False, registry)
    for entry in entries:
        other = describe(entry["root"])
        if other.name == project.name:
            raise KyoseiError(
                f"a project named {project.name!r} is already registered in {registry} (root "
                f"{other.root}), and kyo serve names projects by that name, so a URL could not "
                f"say which one it means; remove that entry first (`kyo projects remove "
                f"{other.root}`) or register a project whose home name differs"
            )
    write_entries(registry, [*entries, {"root": str(candidate)}])
    return Added(project, True, registry)


def remove(target: str, environ: dict[str, str] | None = None) -> Registered:
    """Drop the one entry named ``target`` (its served name, or its root path)."""
    registry = registry_path(environ)
    entries = read_entries(registry)
    by_path = [i for i, entry in enumerate(entries) if _normal(entry["root"]) == _normal(target)]
    matches = by_path or [
        i for i, entry in enumerate(entries) if describe(entry["root"]).name == target
    ]
    if not matches:
        raise KyoseiError(
            f"no project named or rooted at {target!r} is registered in {registry}; "
            "`kyo projects list` shows each registered name and root"
        )
    if len(matches) > 1:
        roots = ", ".join(entries[i]["root"] for i in matches)
        raise KyoseiError(
            f"{target!r} matches {len(matches)} registry entries ({roots}); remove one by its "
            "root path (`kyo projects remove /path/to/project`)"
        )
    (index,) = matches
    gone = describe(entries[index]["root"])
    write_entries(registry, [entry for i, entry in enumerate(entries) if i != index])
    return gone


def register_on_init(
    code_root: Path, environ: dict[str, str] | None = None
) -> tuple[list[str], dict]:
    """``kyo init``'s registration of the project it just made.

    Never raises: the project exists by now, and a registry that refuses it (unreadable,
    or a name already taken) costs the registration, which is said with its remedy, not
    the init.
    """
    registry = registry_path(environ)
    try:
        result = add(code_root, environ)
    except KyoseiError as exc:
        return (
            [
                f"not registered in this machine's project registry: {exc}",
                f"  once that is resolved: kyo projects add {code_root}",
            ],
            {"registered": False, "registry": str(registry), "problem": str(exc), "name": None},
        )
    said = "registered" if result.added else "already registered"
    return (
        [
            f"{said} in this machine's project registry {registry} as "
            f"{result.project.name!r} (the name kyo serve and kyo orchestrator use for it)"
        ],
        {
            "registered": True,
            "registry": str(registry),
            "problem": None,
            "name": result.project.name,
        },
    )


# ------------------------------------------------------------------- command


def cmd_projects(ctx: Context, args: Namespace) -> Output:
    """``kyo projects list``, ``kyo projects add PATH``, ``kyo projects remove NAME|PATH``."""
    environ = ctx.environ
    action = args.action
    if action == "list":
        if args.target:
            raise KyoseiError(
                f"kyo projects list takes no argument; drop {args.target!r} (`kyo projects list`)"
            )
        registry = registry_path(environ)
        rows = registered(environ)
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.name] = counts.get(row.name, 0) + 1
        lines = [f"{registry}: {len(rows)} project(s)"]
        for row in rows:
            state = row.prefix or "-"
            lines.append(f"  {row.name:<24} {state:<8} {row.root}")
            if row.problem:
                lines.append(f"    cannot be opened: {row.problem}")
            if counts[row.name] > 1:
                lines.append(
                    f"    shares its name with another entry, so kyo serve refuses the name; "
                    f"remove one (`kyo projects remove {row.root}`)"
                )
        data = {
            "ok": True,
            "command": "projects",
            "action": "list",
            "registry": str(registry),
            "projects": [row.to_json() for row in rows],
        }
        return Output(lines, data)

    if not args.target:
        example = "/path/to/project" if action == "add" else "<name>"
        raise KyoseiError(
            f"kyo projects {action} needs a project; name one (`kyo projects {action} {example}`)"
        )
    if action == "add":
        result = add(args.target, environ)
        verb = "added" if result.added else "already registered"
        lines = [f"{verb}: {result.project.name} ({result.project.root}) in {result.registry}"]
        return Output(
            lines,
            {
                "ok": True,
                "command": "projects",
                "action": "add",
                "added": result.added,
                "registry": str(result.registry),
                "project": result.project.to_json(),
            },
        )
    gone = remove(args.target, environ)
    registry = registry_path(environ)
    return Output(
        [f"removed: {gone.name} ({gone.root}) from {registry}"],
        {
            "ok": True,
            "command": "projects",
            "action": "remove",
            "registry": str(registry),
            "project": gone.to_json(),
        },
    )
