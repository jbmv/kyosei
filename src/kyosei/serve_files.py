"""``kyo serve``'s write boundary: every file it touches, in one module (KYO-456).

Ported once from the desktop app's ``assertWritable`` (``apps/desktop/README.md``, "The one
write path"). Law 12: the structured store is read-only from every viewer, and the
ideas layer is writable from anywhere -- so the server, where every remote viewer's
writes land, is where that line has to hold.

WHAT IS WRITABLE. Exactly two things per project:

- files under the project's ``ideas/`` (the store root's, which is the project root
  under the in-repo layout and the store directory under the split one -- the same
  ``store_root`` `kyo where` reports and the desktop joins ``ideas/`` to);
- the session scratchpad, ``<per-project home>/scratchpad.md``, one fixed path.

And for this machine rather than a project: the send log, and the composer's unsent text
(KYO-703), ``$KYOSEI_HOME/composer/{drafts,held}/<session id>.json`` (and, KYO-709,
``unfiled/<message id>.json``), private to this account and refused if it would lie inside a
git repository. KYO-715 adds the files a prompt attaches,
``$KYOSEI_HOME/composer/attachments/<project>/<session id>/<sha256>.<extension>``, written
once by :func:`write_attachment` under a name it checks itself and never replaced.

HOW IT HOLDS. One resolver, :func:`ideas_target`, for every idea route, read or
write: the relative path is checked as text first (no absolute path, no NUL, no empty,
``.`` or ``..`` segment -- refused before anything touches the disk), then joined to
``ideas/`` and resolved to its REAL path, which must lie inside the real path of
``ideas/``. A symlink planted under ``ideas/`` that points at ``kyosei/`` therefore
resolves outside and is refused, and the refusal names the store when that is where it
pointed. The scratchpad has its own resolver, :func:`scratchpad_target`, with no
relative path at all. The module is the only ``kyosei.serve*`` module that calls a
file-writing function; ``tests/test_kyo_serve_files.py`` fails if another one does.

NO LOST UPDATE, NO SILENT OVERWRITE. Every file carries an ``ETag`` (a hash of its
bytes). A ``PUT`` needs ``If-Match: <etag>`` to replace a file or ``If-None-Match: *``
to create one; ``DELETE`` needs ``If-Match``. A mismatch is a 412 carrying the current
etag. A write goes to a temporary file beside the target and then a rename; a create
links the temporary file into place, which fails rather than overwrite a file that
appeared in between. One lock serializes the check and the write inside this process.

WHAT IS READABLE BESIDE THEM (KYO-467). A governed document's text, read-only: the one
resolver :func:`document_target` takes a path relative to the CODE root, checks it as
text exactly as an idea path is checked, and serves it only when it is one of the files
``kyo docs check`` itself enumerates (``kyosei/documents.yml``'s globs, or the default
set, through the same ``docs_parse`` walk the check uses) and its real path is still
inside the code root. It is never a general file reader: a source file, ``kyosei/``, or a
``..`` is refused before a byte is read.

What it cannot see: a symlink swapped in between the resolve and the write by another
process on the same machine. That process could write ``kyosei/`` directly anyway; the
boundary is against what a request can ask for, not against the machine's own users.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import mimetypes
import os
import re
import stat
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .config import COMPOSER_DIRNAME, store_home
from .serve import Refusal
from .transcript_archive import enclosing_git_repository

if TYPE_CHECKING:
    from .config import Layout

#: The directory the ideas layer lives in, under the store root.
IDEAS_DIR = "ideas"

#: The session scratchpad's file name, in the per-project home.
SCRATCHPAD_NAME = "scratchpad.md"

#: The largest body a PUT may carry. An idea is prose; 16 MiB is far past any of them
#: and short of anything that would hold the server's memory hostage.
MAX_BODY_BYTES = 16 * 1024 * 1024

#: Serializes check-then-write inside this process, so two requests cannot both pass
#: the same If-Match and both write.
_WRITE_LOCK = threading.Lock()

_BOUNDARY = (
    "kyo serve writes exactly two things in a project -- files under its ideas/ and "
    "its session scratchpad -- because those are the human's own surfaces. Everything "
    "else, kyosei/ above all, belongs to `kyo`: records, promotion and the export are "
    "one transaction inside the CLI, and a second writer to them is the failure the "
    "read/write split exists to prevent (law 12, KYO-72)"
)


# --------------------------------------------------------------- where things are


@dataclass(frozen=True)
class ProjectPaths:
    """The paths one served project's files and change events are read from."""

    store_root: Path
    #: Where the project's code and documents are: the store root under the in-repo
    #: layout, the code repository under the split one (KYO-467, the document route).
    code_root: Path
    ideas: Path
    kyosei_dir: Path
    db: Path
    export: Path
    sessions: Path
    scratchpad: Path


def project_paths(root: str, environ: dict[str, str]) -> ProjectPaths:
    """Where one registry entry's files are, by the CLI's own layout rules (law 3)."""
    from .config import (
        DB_NAME,
        KYOSEI_DIR,
        SESSIONS_NAME,
        resolve_layout,
    )

    layout = resolve_layout(explicit_root=root, required=False)
    kyosei_dir = layout.store_root / KYOSEI_DIR
    return ProjectPaths(
        store_root=layout.store_root,
        code_root=layout.code_root,
        ideas=layout.store_root / IDEAS_DIR,
        kyosei_dir=kyosei_dir,
        db=kyosei_dir / DB_NAME,
        export=kyosei_dir / _export_dir(layout.store_root),
        sessions=kyosei_dir / SESSIONS_NAME,
        scratchpad=scratchpad_path(layout, environ),
    )


def scratchpad_path(layout: Layout, environ: dict[str, str] | None = None) -> Path:
    """The session scratchpad: ``<store home>/<project home>/scratchpad.md``.

    The one spelling of that path. :func:`project_paths` (``kyo serve``, and the desktop
    app through it) and ``kyo where``'s ``scratchpad`` line both read it from here, so an
    orchestrator told to write where ``kyo where`` says writes the file the app shows
    (KYO-572).
    """
    from .config import project_home_name, store_home

    return store_home(environ) / project_home_name(layout) / SCRATCHPAD_NAME


def _export_dir(store_root: Path) -> str:
    from .config import load_config

    try:
        return load_config(store_root).export_dir
    except Exception:  # noqa: BLE001 - an unreadable config still has the default export
        return "export"


# ------------------------------------------------------------------ the resolvers


def check_relpath(relpath: str, where: str = f"{IDEAS_DIR}/") -> list[str]:
    """``relpath``'s segments, or a 400 -- before anything touches the disk.

    Every idea route takes a path RELATIVE to ``ideas/`` (and the document route one
    relative to the code root, named by ``where``). An absolute path, a NUL, and an
    empty, ``.`` or ``..`` segment are all refused as text: a ``..`` that stays inside
    would resolve harmlessly, but no viewer needs one, and refusing every one is a rule
    a reader can check without a filesystem.
    """
    if relpath == "":
        raise _bad_path(relpath, f"it is empty; name a path relative to {where}", where)
    if "\x00" in relpath:
        raise _bad_path(relpath, "it contains a NUL byte", where)
    if relpath.startswith(("/", "\\")) or (len(relpath) > 1 and relpath[1] == ":"):
        raise _bad_path(
            relpath, f"it is absolute; this route takes a path relative to {where}", where
        )
    segments = relpath.split("/")
    for segment in segments:
        if segment == "":
            raise _bad_path(
                relpath, "it has an empty segment (two slashes, or a trailing one)", where
            )
        if segment in (".", ".."):
            raise _bad_path(
                relpath,
                f"it has a {segment!r} segment; name the path inside {where} directly",
                where,
            )
    return segments


def ideas_target(paths: ProjectPaths, relpath: str) -> Path:
    """The REAL path ``relpath`` names inside ``ideas/``, or a refusal.

    Resolved through every symlink that exists, so ``ideas/x -> ../kyosei`` makes
    ``x/config.yml`` resolve into the store and be refused by name.
    """
    segments = check_relpath(relpath)
    real_root = Path(os.path.realpath(paths.ideas))
    real = Path(os.path.realpath(paths.ideas.joinpath(*segments)))
    if real != real_root and _within(real_root, real):
        return real
    real_store = Path(os.path.realpath(paths.kyosei_dir))
    if real == real_store or _within(real_store, real):
        why = f"it resolves to {real}, inside the store's kyosei/ directory"
    else:
        why = f"it resolves to {real}, outside {real_root}"
    raise Refusal(
        403,
        f"kyo serve will not touch ideas/{relpath}: {why}. {_BOUNDARY}",
        path=relpath,
    )


#: What a document path is relative to, as the refusals name it.
DOCS_WHERE = "the project's code root"


def document_target(paths: ProjectPaths, relpath: str) -> Path:
    """The REAL path of the governed document ``relpath`` names, or a refusal (KYO-467).

    Checked as text first (:func:`check_relpath`), then required to be one of the files
    ``kyo docs check`` enumerates for this project -- the same ``document_set`` and
    ``_iter_docs_files`` the check walks, so the route cannot serve a file the docs tree
    does not list -- and finally resolved through every symlink and required to stay
    inside the code root. A path that is not a document is a 404 whether or not a file
    is there, so the route is not an oracle for the rest of the tree.
    """
    from .docs_parse import _iter_docs_files, document_set
    from .errors import KyoseiError

    segments = check_relpath(relpath, DOCS_WHERE)
    try:
        documents_in = document_set(paths.kyosei_dir)
    except KyoseiError as exc:
        raise Refusal(
            422,
            f"kyo serve cannot say which files are this project's documents: {exc}",
            path=relpath,
        ) from exc
    listed = {
        found.relative_to(paths.code_root).as_posix()
        for found in _iter_docs_files(paths.code_root, documents_in.globs)
    }
    if "/".join(segments) not in listed:
        raise Refusal(
            404,
            f"{relpath} is not one of this project's documents, and kyo serve reads a "
            "document's text only for a file `kyo docs check` lists (its paths: "
            f"{documents_in.says}); GET the cmd/docs/check route for the list",
            path=relpath,
        )
    real_root = Path(os.path.realpath(paths.code_root))
    real = Path(os.path.realpath(paths.code_root.joinpath(*segments)))
    if not _within(real_root, real):
        raise Refusal(
            403,
            f"kyo serve will not read {relpath}: it resolves to {real}, outside the code "
            f"root {real_root}. A document is served from inside its project, never "
            "through a link that leaves it",
            path=relpath,
        )
    return real


def scratchpad_target(paths: ProjectPaths) -> Path:
    """The one scratchpad path, refused if it is a symlink to somewhere else."""
    parent = Path(os.path.realpath(paths.scratchpad.parent))
    expected = parent / paths.scratchpad.name
    real = Path(os.path.realpath(paths.scratchpad))
    if real != expected:
        raise Refusal(
            403,
            f"kyo serve will not touch the scratchpad {paths.scratchpad}: it is a symlink "
            f"resolving to {real}, and the scratchpad is written only at its own path. "
            f"Replace the link with a plain file. {_BOUNDARY}",
        )
    return real


def _within(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _bad_path(relpath: str, why: str, where: str = f"{IDEAS_DIR}/") -> Refusal:
    return Refusal(400, f"{relpath!r} is not a path inside {where}: {why}", path=relpath)


# ------------------------------------------------------------------------ etags


def etag_of(data: bytes) -> str:
    """A strong ETag for these bytes: the quoted head of their sha256."""
    return '"' + hashlib.sha256(data).hexdigest()[:32] + '"'


def _tags(header: str) -> list[str]:
    tags = []
    for item in header.split(","):
        item = item.strip()
        if item.startswith("W/"):
            item = item[2:]
        if item and item != "*" and not item.startswith('"'):
            item = f'"{item}"'
        if item:
            tags.append(item)
    return tags


def _matches(if_match: str, current: bytes) -> bool:
    """Whether an ``If-Match`` header names ``current`` (or is ``*``)."""
    return "*" in (t.strip() for t in if_match.split(",")) or etag_of(current) in _tags(
        if_match
    )


def _current(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except IsADirectoryError as exc:
        raise Refusal(409, f"{path} is a directory, not a file") from exc


# ----------------------------------------------------------------------- reads


@dataclass(frozen=True)
class FileBody:
    data: bytes
    etag: str
    content_type: str


def read_file(path: Path, shown: str) -> FileBody:
    """One file's bytes, its ETag and a content type, or a 404 naming how to create it."""
    if path.is_dir():
        raise Refusal(
            409,
            f"{shown} is a directory; GET the ideas/ route for the tree",
            path=shown,
        )
    data = _current(path)
    if data is None:
        raise Refusal(
            404,
            f"{shown} does not exist; create it with PUT and the header If-None-Match: *",
            path=shown,
            etag=None,
        )
    kind = "text/markdown" if path.suffix == ".md" else mimetypes.guess_type(path.name)[0]
    if kind is None:
        kind = "application/octet-stream"
    if kind.startswith("text/"):
        kind += "; charset=utf-8"
    return FileBody(data, etag_of(data), kind)


def created_ns(info: os.stat_result) -> int:
    """When a path came to be, in nanoseconds: its birth time where the filesystem
    keeps one (APFS, HFS+, and Windows), else the earliest stamp it does keep -- the
    smaller of ``mtime`` and ``ctime``, which is never later than the birth (KYO-467).

    A birth time of zero is a filesystem saying it has none, and falls back the same
    way. A file copied with its mtime preserved can carry an mtime older than its birth
    on this disk; the birth is still the answer, because "created" in the app means
    when the file appeared here.
    """
    birth = getattr(info, "st_birthtime_ns", None)
    if birth is None:
        seconds = getattr(info, "st_birthtime", None)
        birth = int(seconds * 1_000_000_000) if seconds else None
    if birth:
        return birth
    return min(info.st_mtime_ns, info.st_ctime_ns)


def idea_tree(paths: ProjectPaths) -> dict:
    """Every path under ``ideas/``, each with its type, size, ``mtime_ns``,
    ``created_ns`` (:func:`created_ns`) and etag.

    Walked without following symlinks. A symlink is listed as ``symlink`` with no
    size or etag: its content is whatever the resolver says it is when a route asks.
    """
    root = paths.ideas
    entries: list[dict] = []
    if not root.is_dir():
        return {"ok": True, "root": str(root), "exists": False, "entries": entries}

    def walk(directory: Path, prefix: str) -> None:
        with os.scandir(directory) as scan:
            items = sorted(scan, key=lambda e: e.name)
        for item in items:
            rel = f"{prefix}{item.name}"
            info = item.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                entries.append(
                    {
                        "path": rel,
                        "type": "symlink",
                        "mtime_ns": info.st_mtime_ns,
                        "created_ns": created_ns(info),
                    }
                )
            elif stat.S_ISDIR(info.st_mode):
                entries.append(
                    {
                        "path": rel,
                        "type": "dir",
                        "mtime_ns": info.st_mtime_ns,
                        "created_ns": created_ns(info),
                    }
                )
                walk(Path(item.path), f"{rel}/")
            elif stat.S_ISREG(info.st_mode):
                data = _current(Path(item.path)) or b""
                entries.append(
                    {
                        "path": rel,
                        "type": "file",
                        "size": info.st_size,
                        "mtime_ns": info.st_mtime_ns,
                        "created_ns": created_ns(info),
                        "etag": etag_of(data),
                    }
                )

    walk(root, "")
    return {"ok": True, "root": str(root), "exists": True, "entries": entries}


# ---------------------------------------------------------------------- writes


def put_file(
    path: Path,
    shown: str,
    data: bytes,
    *,
    if_match: str | None,
    if_none_match: str | None,
    private: bool = False,
) -> tuple[int, dict]:
    """Write ``data`` to ``path`` under the precondition the headers state.

    ``(201, …)`` for a create, ``(200, …)`` for a replace; the body carries the new
    etag. A missing parent is created: for an idea, folders inside ``ideas/``, where
    the resolver has already placed ``path``; for the scratchpad, its per-project home.
    ``private`` creates the file readable by this account alone (a composer draft).
    """
    if if_match is None and if_none_match is None:
        raise Refusal(
            428,
            f"a PUT to {shown} must say what it expects to replace: send If-Match: "
            "<etag> (the ETag a GET returned) to replace the file, or If-None-Match: * "
            "to create it -- so two viewers never overwrite each other silently",
            path=shown,
        )
    if if_none_match is not None and if_none_match.strip() != "*":
        raise Refusal(
            400,
            "If-None-Match on a PUT is honoured only as '*' (create, never overwrite)",
            path=shown,
        )
    with _WRITE_LOCK:
        if path.is_dir():
            raise Refusal(409, f"{shown} is a directory, not a file", path=shown)
        current = _current(path)
        if if_none_match is not None:
            if current is not None:
                raise Refusal(
                    412,
                    f"{shown} already exists, and a create never overwrites a file; GET "
                    "it and PUT with If-Match: <its etag> to replace it",
                    path=shown,
                    etag=etag_of(current),
                )
        elif current is None:
            raise Refusal(
                412,
                f"{shown} does not exist, so nothing matches If-Match; create it with "
                "If-None-Match: *",
                path=shown,
                etag=None,
            )
        elif not _matches(if_match, current):
            raise Refusal(
                412,
                f"{shown} changed since that etag was read: GET it again, merge, and PUT "
                "with the current etag",
                path=shown,
                etag=etag_of(current),
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(path, data, create=current is None, private=private)
    return (201 if current is None else 200), {
        "ok": True,
        "path": shown,
        "etag": etag_of(data),
        "created": current is None,
    }


def _write_atomically(path: Path, data: bytes, *, create: bool, private: bool = False) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        created_mode = 0o600 if private else 0o644
        os.chmod(tmp, created_mode if create else stat.S_IMODE(path.stat().st_mode))
        if create:
            try:
                os.link(tmp, path)
            except FileExistsError as exc:
                raise Refusal(
                    412,
                    f"{path.name} appeared while it was being created, and a create never "
                    "overwrites; GET it and PUT with If-Match",
                ) from exc
        else:
            os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def delete_path(path: Path, shown: str, *, if_match: str | None) -> dict:
    """Delete a file (``If-Match`` required) or an EMPTY folder.

    A folder with files in it is refused: deleting it would delete each file without
    the etag that proves the deleter saw what it held.
    """
    with _WRITE_LOCK:
        if path.is_dir() and not path.is_symlink():
            try:
                path.rmdir()
            except OSError as exc:
                if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
                    raise Refusal(
                        409,
                        f"{shown} is a folder with entries in it; delete each file (with "
                        "its If-Match) first, then the empty folder",
                        path=shown,
                    ) from exc
                raise
            return {"ok": True, "path": shown, "deleted": "dir"}
        current = _current(path)
        if current is None:
            raise Refusal(404, f"{shown} does not exist", path=shown, etag=None)
        if if_match is None:
            raise Refusal(
                428,
                f"a DELETE of {shown} must send If-Match: <etag> (the ETag a GET "
                "returned), so a file is never deleted unseen",
                path=shown,
            )
        if not _matches(if_match, current):
            raise Refusal(
                412,
                f"{shown} changed since that etag was read: GET it again before deleting",
                path=shown,
                etag=etag_of(current),
            )
        path.unlink()
    return {"ok": True, "path": shown, "deleted": "file"}


def move_path(source: Path, target: Path, shown_from: str, shown_to: str) -> dict:
    """Rename or move within ``ideas/``; the destination must not exist."""
    with _WRITE_LOCK:
        if not os.path.lexists(source):
            raise Refusal(404, f"{shown_from} does not exist", path=shown_from)
        if os.path.lexists(target):
            raise Refusal(
                412,
                f"{shown_to} already exists, and a move never overwrites; move to a name "
                "that is free, or delete that one first",
                path=shown_to,
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(source, target)
        except OSError as exc:
            raise Refusal(
                409, f"{shown_from} cannot move to {shown_to}: {exc.strerror or exc}"
            ) from exc
    return {"ok": True, "from": shown_from, "to": shown_to}


def make_dir(path: Path, shown: str) -> tuple[int, dict]:
    """Create a folder under ``ideas/`` (and its parents); an existing one is fine."""
    with _WRITE_LOCK:
        if path.is_dir():
            return 200, {"ok": True, "path": shown, "created": False}
        if os.path.lexists(path):
            raise Refusal(409, f"{shown} exists and is not a folder", path=shown)
        path.mkdir(parents=True)
    return 201, {"ok": True, "path": shown, "created": True}


# ----------------------------------------------------------------- the send log (KYO-699)


def make_private_directory(path: Path) -> None:
    """The directory holding one of this machine's logs, created if it is absent."""
    path.mkdir(parents=True, exist_ok=True)


def open_private_log(path: Path) -> int:
    """A log of this machine's, open to append and readable by this account alone."""
    return os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)


# --------------------------------------------- the composer's unsent text (KYO-703)


#: What follows ``$KYOSEI_HOME/composer/``: one directory per kind of unsent text.
DRAFTS = "drafts"
HELD = "held"
#: KYO-709: a delivered message whose notes could not be filed yet, named by the message's id.
UNFILED = "unfiled"

#: A session id as a file name: Claude Code's are UUIDs, and nothing else may name a file.
_SESSION_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def composer_kind_directory(environ: dict[str, str], kind: str) -> Path:
    """Where one kind of unsent text lives, without creating or checking anything."""
    return store_home(environ) / COMPOSER_DIRNAME / kind


def composer_directory(environ: dict[str, str], kind: str) -> Path:
    """The directory of one kind of unsent text, created private, or the refusal to use it.

    Unsent text is as sensitive as a transcript. A project's own directory under the split
    layout is a git repository (the store), which is why this is ``$KYOSEI_HOME/composer``
    and why a home that lies inside a repository is refused.
    """
    directory = composer_kind_directory(environ, kind)
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise _composer_refusal(directory, str(exc)) from exc
    repository = enclosing_git_repository(directory)
    if repository is not None:
        raise _composer_refusal(
            directory,
            f"it lies inside the git repository at {repository}, where unsent text could be "
            "committed; point $KYOSEI_HOME at a directory outside every git repository",
        )
    return directory


def composer_path(environ: dict[str, str], kind: str, session_id: str) -> Path:
    """The one file holding this kind of unsent text for ``session_id``, for reading."""
    if not _SESSION_FILE.match(session_id):
        raise Refusal(
            400,
            f"{session_id!r} cannot name a session's unsent text; a session id holds only "
            "letters, digits, dots, hyphens and underscores",
            reason="bad-request",
        )
    return composer_kind_directory(environ, kind) / f"{session_id}.json"


def composer_target(environ: dict[str, str], kind: str, session_id: str) -> Path:
    """That file, for writing: its directory exists, private, and outside any repository."""
    path = composer_path(environ, kind, session_id)
    composer_directory(environ, kind)
    return path


def _composer_refusal(directory: Path, why: str) -> Refusal:
    return Refusal(
        500,
        f"unsent text cannot be kept in {directory}: {why}. Nothing was stored",
        reason="composer-state-unusable",
    )


def write_private(path: Path, data: bytes) -> None:
    """Replace ``path`` whole, durably, readable by this account alone.

    ``mkstemp`` creates the temporary file private, so a reader never finds a wider mode
    and a crash leaves either the old text or the new.
    """
    with _WRITE_LOCK:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.lexists(tmp):
                os.unlink(tmp)


def remove_private(path: Path) -> None:
    """Remove a file of unsent text; one already gone is the wanted outcome."""
    with _WRITE_LOCK, contextlib.suppress(FileNotFoundError):
        path.unlink()


# ------------------------------------------------- the files a prompt attaches (KYO-715)


#: KYO-715: ``attachments/<project>/<session id>/<sha256>.<extension>``.
ATTACHMENTS = "attachments"

#: An attachment's file name: the hex sha256 of its bytes and the extension of its type.
_ATTACHMENT_NAME = re.compile(r"^[0-9a-f]{64}\.(png|jpg|gif|webp|pdf)$")


def _segment_refusal(value: str, what: str) -> Refusal | None:
    if (
        not value
        or len(value) > 128
        or value in (".", "..")
        or value.startswith(".")
        or any(char in value for char in "/\\\x00")
    ):
        return Refusal(
            400,
            f"{value!r} cannot name a directory of attachments: a {what} that names one is "
            "not empty, holds no slash or NUL, and does not begin with a dot",
            reason="bad-request",
        )
    return None


def attachment_directory(
    environ: dict[str, str], project: str, session_id: str, *, create: bool
) -> Path:
    """The directory of one project's session's attachments, or the refusal to use it.

    With ``create`` it is made private and outside any git repository (as the other
    composer files are); without, nothing is created. Its real path must lie inside the
    real path of the attachments directory, so a planted symlink cannot move it.
    """
    for value, what in ((project, "project name"), (session_id, "session id")):
        refusal = _segment_refusal(value, what)
        if refusal is not None:
            raise refusal
    base = (
        composer_directory(environ, ATTACHMENTS)
        if create
        else composer_kind_directory(environ, ATTACHMENTS)
    )
    directory = base / project / session_id
    if create:
        try:
            (base / project).mkdir(mode=0o700, exist_ok=True)
            directory.mkdir(mode=0o700, exist_ok=True)
        except OSError as exc:
            raise _composer_refusal(directory, str(exc)) from exc
    real_base = Path(os.path.realpath(base))
    real = Path(os.path.realpath(directory))
    if real == real_base or not _within(real_base, real):
        raise _composer_refusal(
            directory, f"it resolves to {real}, outside {real_base}; remove the link"
        )
    return real


def write_attachment(directory: Path, name: str, data: bytes) -> bool:
    """Write an attachment once; True when it was created, False when it was there.

    The name is checked here, whatever the caller did, so this function cannot write
    outside ``directory`` or under a name a client chose. A file already at that name
    holds the same bytes, because the name is their digest, so it is kept as it is.
    """
    if not _ATTACHMENT_NAME.match(name):
        raise ValueError(f"{name!r} is not an attachment's name")
    target = directory / name
    with _WRITE_LOCK:
        if os.path.lexists(target):
            return False
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(tmp, target)
            except FileExistsError:
                return False
        finally:
            if os.path.lexists(tmp):
                os.unlink(tmp)
    return True
