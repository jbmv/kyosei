"""The store repository's own ignore lines, topped up after `kyo init` (KYO-322, KYO-403)."""

from __future__ import annotations

from pathlib import Path

from .config import (
    DB_NAME,
    DOCTOR_REPORT_NAME,
    KYOSEI_DIR,
)
from .scaffold import (
    KYOSEI_GITIGNORE,
)

# ------------------------------- KYO-322: the same reach, for the STORE's own .gitignore

#: The comment line that identifies the block below as this tool's, on
#: :data:`REPORT_GITIGNORE_MARKER`'s precedent and for its reader: presence of each
#: PATTERN is what makes the write idempotent, and the marker is for the human who
#: finds two new lines in a file they did not edit.
STORE_GITIGNORE_MARKER = (
    "# local state beside the store's database — SQLite's WAL sidecars exist whenever "
    "anything has the database open (KYO-322)"
)


def store_gitignore_path(store_root: Path) -> Path:
    """The store's own ``.gitignore`` — ``<store>/kyosei/.gitignore``, under either
    layout, because that is where :data:`DB_NAME` and its sidecars actually land."""
    return store_root / KYOSEI_DIR / ".gitignore"


def missing_store_gitignore_lines(store_root: Path) -> list[str]:
    """Patterns :data:`KYOSEI_GITIGNORE` declares that this store's copy does not have.

    Matched per pattern rather than against the file as a whole, so a project that
    added a line of its own, or reordered ours, or wrote ``/kyosei.db-wal`` with git's
    anchoring slash, is left exactly as it is — the conservatism
    :func:`install_report_gitignore` already errs on for a file we do not own.
    """
    target = store_gitignore_path(store_root)
    existing = (
        target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
    )
    present = {
        line.strip().lstrip("/")
        for line in existing.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    return [
        pattern
        for pattern in KYOSEI_GITIGNORE.splitlines()
        if pattern.strip() and pattern.strip() not in present
    ]


def install_store_gitignore(
    store_root: Path, *, print_only: bool = False
) -> tuple[list[str], list[str]]:
    """Top up the store's ``.gitignore`` with anything :data:`KYOSEI_GITIGNORE` has
    gained since this project was scaffolded. Returns ``(changes, notes)``.

    `kyo init` writes the whole file once, so a line added later reaches an existing
    project only through a command it already reruns after an upgrade
    (docs/design/scaffold.md#kyo-322).

    **Called by `kyo install-procedures`, and deliberately not by `kyo install-hooks`.**
    The two commands own different repositories, and this file is in the store's:
    :func:`install_report_gitignore` writes the CODE repository's root ``.gitignore``
    and belongs to `install-hooks`, which configures that repository and takes
    ``code_root``; this one writes ``<store>/kyosei/.gitignore`` and belongs to
    `install-procedures`, the one upgrade command that already takes the store root and
    already writes into the store. Under the split layout those are two different git
    repositories, so a top-up hung off `install-hooks` would either reach into a
    repository that command has no business in or miss the file entirely. The store's
    own guard comes with the same host: :func:`install_procedures_refusal` already
    refuses to write the store from a dispatch worktree or off ``main``, which is
    exactly the guard a write under ``kyosei/`` needs (KYO-151.c3).

    Idempotent, additive, never destructive: an existing file is appended to, never
    rewritten, and a run with every pattern already listed reports no change.
    """
    missing = missing_store_gitignore_lines(store_root)
    if not missing:
        return [], []
    target = store_gitignore_path(store_root)
    existing = (
        target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
    )
    if not print_only:
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        separator = "\n" if existing else ""
        block = (
            f"{STORE_GITIGNORE_MARKER}\n" + "".join(f"{line}\n" for line in missing)
            if existing
            else KYOSEI_GITIGNORE
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(existing + prefix + separator + block, encoding="utf-8")
    verb = "would add" if print_only else "added"
    return [
        f"gitignore: {verb} {', '.join(missing)} to {target} — these are local state "
        f"beside {DB_NAME}, written and removed under the store while sessions run "
        "(SQLite's `-shm`/`-wal` sidecars for as long as anything has the database "
        f"open, {DOCTOR_REPORT_NAME} for as long as a check is failing), and an "
        "untracked file that appears and vanishes is a failed `git add` and a dirty "
        "tree at somebody's stop"
    ], []


# ----------------------- KYO-403: a split store ignores the worktrees nested under it

#: The marker for the block below, on :data:`REPORT_GITIGNORE_MARKER`'s precedent:
#: presence of the PATTERN makes the write idempotent, and the comment is for the
#: maintainer who finds a line in their store's `.gitignore` they did not write.
WORKTREE_HOME_GITIGNORE_MARKER = (
    "# dispatch worktrees of the code repository — checkouts of a different "
    "repository, created here by `kyo dispatch --branch` (KYO-70, KYO-403)"
)


def install_worktree_home_gitignore(
    store_root: Path, worktree_home: Path, *, print_only: bool = False
) -> list[str]:
    """Make the store repository ignore a worktree home that sits inside it. Returns
    the changes made (or that would be), empty when there was nothing to do.

    Under the split layout the worktree home (``~/kyosei/<store>/dispatch worktrees/``)
    is inside the store's git working tree, so every worktree would show up in the store
    repository's `git status` -- one ``git add -A`` away from committing a checkout of the
    code repository into the store's history. The home is ignored rather than moved
    (docs/design/scaffold.md#kyo-403).

    The root ``.gitignore`` of the STORE repository, because that is the repository
    whose status is wrong; the ``kyosei/.gitignore`` :func:`install_store_gitignore`
    tops up covers only paths under ``kyosei/``. A no-op when the home is not inside
    ``store_root`` (every in-repo project: its home is ``~/kyosei/<code repo>/``,
    which is no repository at all) or when ``store_root`` is not the top of a git
    checkout. Idempotent and additive like its two siblings above.
    """
    try:
        relative = worktree_home.resolve().relative_to(store_root.resolve())
    except ValueError:
        return []
    if not relative.parts or not (store_root / ".git").exists():
        return []
    pattern = f"/{relative.as_posix()}/"
    target = store_root / ".gitignore"
    existing = (
        target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
    )
    accepted = {pattern, pattern.rstrip("/"), pattern.lstrip("/"), pattern.strip("/")}
    if any(line.strip() in accepted for line in existing.splitlines()):
        return []
    if not print_only:
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        separator = "\n" if existing else ""
        target.write_text(
            existing + prefix + separator + f"{WORKTREE_HOME_GITIGNORE_MARKER}\n{pattern}\n",
            encoding="utf-8",
        )
    verb = "would add" if print_only else "added"
    return [
        f"gitignore: {verb} {pattern} to {target} — this project's dispatch worktrees "
        "live inside the store directory, and are checkouts of the code repository, "
        "not store content"
    ]
