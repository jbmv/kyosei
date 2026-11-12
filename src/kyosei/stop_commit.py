"""What the Stop hook does about an uncommitted store (KYO-879, decision KYO-872 F6).

``kyo confirm`` and ``kyo close-wave`` commit a split store; every other command that
writes it (``attach``, ``new``, ``gate``, ``discuss``, ``dispatch``) leaves the export
uncommitted. ``kyo doctor --hook``, the last command of the Stop chain's first group,
calls :func:`commit_at_stop` once the checks have run, since sweep and doctor can both
write the store.

* A split store, in its own repository: what is uncommitted under ``kyosei/`` and
  ``ideas/`` is committed as one commit, by the paths of those two directories only. The
  message is generated: the records whose export file changed, by id. It carries none of
  the person's words. A Stop with nothing uncommitted makes no commit.
* A store inside the code repository: nothing is committed, because those paths belong to
  the person's own commits. One line names them.
* A worktree's session (a dispatched worker's) commits nothing and says nothing.

A failure is one line and never a failed stop: this function returns lines and raises
nothing that the hook should turn into a status.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .cmd_close_wave import _commit, _repo_top
from .commands import Context
from .contracts import GUARDED_BRANCH_DIRS
from .errors import KyoseiError
from .git_queries import run_git

#: Names listed in the one line for an in-repository store, and in a commit message.
NAMED_LIMIT = 5
MESSAGE_ID_LIMIT = 20

_RECORD_FILE = re.compile(r"^kyosei/export/([A-Za-z][A-Za-z0-9]*-\d+)\.json$")


def _in_linked_worktree(ctx: Context) -> bool:
    layout = ctx.layout
    if layout is not None and layout.main_checkout is not None:
        return True
    try:
        git_dir = run_git(ctx.code_root, "rev-parse", "--path-format=absolute", "--git-dir")
        common = run_git(
            ctx.code_root, "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if git_dir.returncode != 0 or common.returncode != 0:
        return False
    return git_dir.stdout.strip() != common.stdout.strip()


def _dirty(top: Path, pathspecs: list[str]) -> list[str]:
    """Uncommitted paths (tracked or untracked) under ``pathspecs``, relative to ``top``."""
    result = run_git(
        top, "status", "--porcelain", "-z", "--untracked-files=all", "--", *pathspecs
    )
    if result.returncode != 0:
        raise KyoseiError(f"git status failed in {top}: {result.stderr.strip()}")
    paths: list[str] = []
    fields = result.stdout.split("\0")
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if len(field) < 4:
            continue
        code, path = field[:2], field[3:]
        paths.append(path)
        if ("R" in code or "C" in code) and index < len(fields):
            paths.append(fields[index])
            index += 1
    return list(dict.fromkeys(paths))


def _record_ids(paths: list[str], prefix_in_repo: str) -> list[str]:
    ids = []
    for path in paths:
        match = _RECORD_FILE.match(path[len(prefix_in_repo) :] if prefix_in_repo else path)
        if match:
            ids.append(match.group(1))
    return sorted(set(ids), key=lambda i: (i.rsplit("-", 1)[0], int(i.rsplit("-", 1)[1])))


def commit_message(ids: list[str], other: int) -> str:
    """The generated message: record ids, and a count of the other store files."""
    shown = ids[:MESSAGE_ID_LIMIT]
    named = ", ".join(shown)
    if len(ids) > len(shown):
        named += f" and {len(ids) - len(shown)} more"
    if ids:
        tail = f"; {other} other store file(s)" if other else ""
        return f"store: {named} changed{tail} (kyo stop hook)"
    return f"store: {other} file(s) changed outside the record export (kyo stop hook)"


def commit_at_stop(ctx: Context) -> list[str]:
    """Commit or name the uncommitted store; the lines for stderr (usually none)."""
    layout = ctx.layout
    if layout is None or not layout.configured:
        return []
    try:
        if _in_linked_worktree(ctx):
            return []
        names = [d for d in GUARDED_BRANCH_DIRS if (layout.store_root / d.rstrip("/")).exists()]
        if not names:
            return []
        if not layout.split:
            top = _repo_top(layout.store_root)
            if top is None:
                return []
            rel = layout.store_root.resolve().relative_to(top.resolve())
            specs = [str(rel / n.rstrip("/")) for n in names]
            dirty = _dirty(top, specs)
            if not dirty:
                return []
            shown = ", ".join(dirty[:NAMED_LIMIT])
            more = f" and {len(dirty) - NAMED_LIMIT} more" if len(dirty) > NAMED_LIMIT else ""
            return [
                f"kyosei: {len(dirty)} uncommitted store path(s) in this repository, "
                f"which the store does not commit for you: {shown}{more}"
            ]
        top = _repo_top(layout.store_root)
        if top is None:
            return []
        rel = layout.store_root.resolve().relative_to(top.resolve())
        prefix = "" if str(rel) == "." else f"{rel}/"
        specs = [prefix + n.rstrip("/") for n in names]
        dirty = _dirty(top, specs)
        if not dirty:
            return []
        ids = _record_ids(dirty, prefix)
        other = len(dirty) - len(
            [p for p in dirty if _RECORD_FILE.match(p[len(prefix) :])]
        )
        _commit(top, specs, commit_message(ids, other))
        return []
    except (KyoseiError, OSError, subprocess.SubprocessError, ValueError) as exc:
        return [f"kyosei: the store was not committed at this stop: {exc}"]
