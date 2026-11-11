"""``kyo serve``'s file routes: a project's files, as a tree and as bytes (KYO-735).

Under ``/v1/projects/{project}/``, all GET and all read-only:

- ``files`` -- the roots: each one's name, its directory, and whether it is served;
- ``files/{root}/tree[/{relpath}]`` -- one directory's entries, each with its name, type,
  size and media type. The whole directory is listed, however many entries it holds;
- ``files/{root}/blob/{relpath}`` -- one file's bytes with its media type and an ``ETag``;
  ``If-None-Match`` naming that tag is answered 304 with no body;
- ``files/resolve?path=<text>`` -- a path as an agent writes one, absolute or relative to
  either repository and with or without a ``:line`` suffix, resolved to the root and relative
  path that hold it, or the answer that no root does;
- ``files/search?q=<text>[&root=<root>]`` -- the files of ``code`` and ``store`` whose path
  holds the text and the lines of them that do (KYO-814);
- ``files/recent`` -- GET lists, and POST ``{"root", "path"}`` records, the files opened in
  the app for the project, kept on the server (KYO-814);
- ``files/starred`` -- GET lists, POST ``{"root", "path"}`` stars and DELETE with the same
  body unstars the project's starred files, kept on the server (KYO-837).

THE PERMISSION. Every one of them needs a device token holding the ``files`` permission
(:mod:`kyosei.device_tokens`), checked before a path is looked at. It is a permission of its
own: a token issued to type into a session or to file notes was not issued to read source.

THE ROOTS. ``code`` is the project's code repository and ``store`` its store repository, the
two directories ``kyo where`` names. Under the in-repo layout they are one directory, and the
``store`` root is refused with a sentence naming ``code``, so that one tree is not served
under two names. ``scratch`` is a session's scratchpad directory, one per session, reached as
``files/scratch/{session}/tree[/{relpath}]`` and ``files/scratch/{session}/blob/{relpath}``.
The directory is the one the session reported with ``kyo sessions scratch <dir>``, stored as
``scratchpad_dir`` on that session's entry in the project's ``sessions.json`` (nothing a hook
is given names it, and a path is not guessed from a pattern). A session this project does not
record, one with no directory recorded, and a recorded directory that no longer passes
:func:`kyosei.cmd_sessions.scratch_dir_problem` are each refused with a reason.

WHAT IS VISIBLE. In ``code`` and ``store``, what git shows in the root, asked of git on every
request: tracked files and untracked files that no ignore rule covers
(``git ls-files --cached --others --exclude-standard``). So an ignored file, ``.git`` and
the contents of a nested repository are absent from a listing and refused by name, and a
root that is not inside a git work tree serves nothing. Nothing is cached: a file added a
moment ago is in the next listing, at the cost of one git process per request. A file and
its directory are both checked with the same command, so the tree cannot list what the blob
route refuses. The ``scratch`` root is not a repository: it shows every entry in its
directory, and the text and real-path checks below apply to it unchanged.

HOW A PATH IS CHECKED, in order, each before a byte of any file is read:

1. as text (:func:`kyosei.serve_files.check_relpath`): no absolute path, no NUL, no empty,
   ``.`` or ``..`` segment, and no ``.git`` segment;
2. by its real path, resolved through every symlink, which must lie inside the root's real
   path. A symlink inside the root that points outside it is refused;
3. by git, as above (not for ``scratch``, which has no git). When the real path is another
   path inside the root (a symlink to a file there), that path must be one git shows too.

This module reads and writes nothing else: no route here creates, changes or removes a
file, and ``tests/test_kyo_serve_files.py`` fails if it calls a file-writing function. A
read is not logged beyond the request line the server writes for every request.

What it cannot see: a path swapped between the check and the read by another process on
this machine, which could read the file itself (:mod:`kyosei.serve_files` says the same).
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import cmd_sessions, device_tokens, serve_files, serve_send
from .git_queries import run_git
from .serve import API_PREFIX, Refusal
from .transcript_archive import enclosing_git_repository

#: What follows ``/v1/projects/{project}/``.
ROUTE = "files"

#: What follows ``files/{root}/``.
TREE = "tree"
BLOB = "blob"

#: KYO-814: the routes under ``files/`` that are no root's.
SEARCH = "search"
RECENT = "recent"
STARRED = "starred"

#: The search's results: how many one response holds, and the longest text it takes.
SEARCH_LIMIT = 200
#: The matching lines a search returns for one file; ``count`` says how many it holds (KYO-818).
SEARCH_LINES_PER_FILE = 5
MAX_QUERY_CHARS = 256
#: A matching line is returned up to this many characters.
MAX_LINE_CHARS = 400
#: How much of a file is looked at for a NUL byte before it is called binary.
_BINARY_SNIFF = 8192

#: How many opened files a project's recent list keeps; the oldest is dropped.
RECENT_LIMIT = 50
_RECENT_LOCK = threading.Lock()

#: KYO-837: how many files a project may star. A star is the user's own mark, so a full
#: list refuses another and names the remedy instead of dropping the oldest.
STARRED_LIMIT = 200
_STARRED_LOCK = threading.Lock()

#: What follows ``files/`` on the route that resolves a path as text; never a root's name.
RESOLVE = "resolve"

ROOT_CODE = "code"
ROOT_STORE = "store"
ROOT_SCRATCH = "scratch"

#: Every root name a request may carry.
ROOTS = (ROOT_CODE, ROOT_STORE, ROOT_SCRATCH)

#: The largest file the blob route returns. The server reads a file whole before it sends
#: it; 32 MiB is past any source file or document and most images.
MAX_FILE_BYTES = 32 * 1024 * 1024

#: The longest text the resolve route takes.
MAX_RESOLVE_CHARS = 4096

#: A blob is the person's own file: only their client may keep it, and it asks again each
#: time, so an edit is seen (the ETag makes that a 304 when nothing changed).
_BLOB_CACHE = "private, no-cache"

_TEXT_CHARSET = "; charset=utf-8"

#: Media types stated here rather than left to :mod:`mimetypes`: markdown, and the source
#: and configuration types a browser does not know or the standard library names
#: differently by platform (``.ts`` is otherwise an MPEG transport stream).
_BY_SUFFIX: dict[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".rst": "text/x-rst",
    ".py": "text/x-python",
    ".pyi": "text/x-python",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".cjs": "text/javascript",
    ".jsx": "text/jsx",
    ".ts": "text/typescript",
    ".mts": "text/typescript",
    ".cts": "text/typescript",
    ".tsx": "text/tsx",
    ".swift": "text/x-swift",
    ".rs": "text/x-rust",
    ".go": "text/x-go",
    ".c": "text/x-c",
    ".h": "text/x-c",
    ".cc": "text/x-c++",
    ".cpp": "text/x-c++",
    ".hpp": "text/x-c++",
    ".m": "text/x-objective-c",
    ".java": "text/x-java",
    ".kt": "text/x-kotlin",
    ".rb": "text/x-ruby",
    ".sh": "text/x-shellscript",
    ".bash": "text/x-shellscript",
    ".zsh": "text/x-shellscript",
    ".sql": "text/x-sql",
    ".css": "text/css",
    ".html": "text/html",
    ".htm": "text/html",
    ".csv": "text/csv",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".yml": "application/yaml",
    ".yaml": "application/yaml",
    ".toml": "application/toml",
    ".xml": "application/xml",
    ".plist": "application/xml",
    ".svg": "image/svg+xml",
    ".lock": "text/plain",
    ".cfg": "text/plain",
    ".ini": "text/plain",
    ".conf": "text/plain",
    ".log": "text/plain",
    ".diff": "text/x-diff",
    ".patch": "text/x-diff",
    ".entitlements": "application/xml",
}

#: Files known by their whole name, which have no suffix to go by.
_BY_NAME: dict[str, str] = {
    "Makefile": "text/x-makefile",
    "Dockerfile": "text/x-dockerfile",
    "LICENSE": "text/plain",
    "NOTICE": "text/plain",
    "README": "text/plain",
    "AUTHORS": "text/plain",
    ".gitignore": "text/plain",
    ".gitattributes": "text/plain",
    ".editorconfig": "text/plain",
    ".kyosei": "text/plain",
}

#: The types outside ``text/`` whose bytes are text all the same.
_TEXT_APPLICATIONS = frozenset(
    {
        "application/json",
        "application/x-ndjson",
        "application/yaml",
        "application/toml",
        "application/xml",
        "application/javascript",
        "application/x-sh",
        "image/svg+xml",
    }
)

_UNKNOWN_TYPE = "application/octet-stream"

#: ``path:12``, ``path:12:5`` and ``path:12-20``: the line an agent names after a path.
_LINE_SUFFIX = re.compile(r"^(?P<path>.+?):(?P<line>\d+)(?:[:-]\d+)?$")


# ----------------------------------------------------------------------- media types


def media_type(name: str) -> str:
    """The media type of a file by its name; ``application/octet-stream`` when unknown.

    By name alone, so that a listing and a read of the same file agree and a listing reads
    no file. The stated tables come first, then the standard library's.
    """
    if name in _BY_NAME:
        return _BY_NAME[name]
    suffix = os.path.splitext(name)[1].lower()
    if suffix in _BY_SUFFIX:
        return _BY_SUFFIX[suffix]
    return mimetypes.guess_type(name)[0] or _UNKNOWN_TYPE


def is_text(kind: str) -> bool:
    """Whether a media type's bytes are text a client can show as text."""
    return kind.startswith("text/") or kind in _TEXT_APPLICATIONS


def content_type(kind: str) -> str:
    """The ``Content-Type`` for a media type: a text type carries ``charset=utf-8``."""
    return kind + _TEXT_CHARSET if is_text(kind) else kind


# ---------------------------------------------------------------------------- roots


@dataclass(frozen=True)
class Root:
    """One served root: its name and the directory it is."""

    name: str
    path: Path
    #: Whether what is visible is what git shows. False for ``scratch``: every entry.
    git: bool = True

    @property
    def real(self) -> Path:
        return Path(os.path.realpath(self.path))


def _same_tree(paths: serve_files.ProjectPaths) -> bool:
    """Whether the store and the code are one directory (the in-repo layout)."""
    return os.path.realpath(paths.store_root) == os.path.realpath(paths.code_root)


def served_roots(paths: serve_files.ProjectPaths) -> list[Root]:
    """The roots this project serves, in the order a relative path is tried against them."""
    roots = [Root(ROOT_CODE, paths.code_root)]
    if not _same_tree(paths):
        roots.append(Root(ROOT_STORE, paths.store_root))
    return roots


def _recorded_scratch(paths: serve_files.ProjectPaths, session: str) -> Path:
    """The scratchpad directory this project's ``sessions.json`` holds for ``session``."""
    entries = cmd_sessions.read_sessions(paths.sessions).entries
    entry = next((item for item in entries if item.get("session_id") == session), None)
    if entry is None:
        raise Refusal(
            404,
            f"session {session} is not recorded for this project, so it has no `{ROOT_SCRATCH}` "
            f"root here (GET {API_PREFIX}/projects/{{project}}/{ROUTE} lists the sessions that "
            "have one)",
            reason="no-such-session",
            root=ROOT_SCRATCH,
            session=session,
        )
    directory = entry.get("scratchpad_dir")
    if not isinstance(directory, str) or not directory:
        raise Refusal(
            404,
            f"session {session} has not reported a scratchpad directory, and the server "
            "does not guess one; run `kyo sessions scratch <dir>` in that session, with the "
            "directory its instructions name",
            reason="scratch-not-recorded",
            root=ROOT_SCRATCH,
            session=session,
        )
    return Path(directory)


def _scratch_root(paths: serve_files.ProjectPaths, session: str) -> Root:
    """The ``scratch`` root of ``session``: its recorded directory, if it still may be one."""
    directory = _recorded_scratch(paths, session)
    if not directory.is_dir():
        raise Refusal(
            404,
            f"the scratchpad directory {directory} that session {session} reported is not a "
            "directory now; run `kyo sessions scratch <dir>` again with the current one",
            reason="scratch-gone",
            root=ROOT_SCRATCH,
            session=session,
        )
    real = Path(os.path.realpath(directory))
    problem = cmd_sessions.scratch_dir_problem(real, [paths.code_root, paths.store_root])
    if problem is not None:
        raise Refusal(
            403,
            f"the scratchpad directory {real} that session {session} reported is not served: "
            f"{problem}; run `kyo sessions scratch <dir>` with another directory",
            reason="scratch-unfit",
            root=ROOT_SCRATCH,
            session=session,
        )
    return Root(ROOT_SCRATCH, real, git=False)


def _root(paths: serve_files.ProjectPaths, name: str) -> Root:
    """The served git root ``name``, or the refusal saying why it is not one."""
    if name == ROOT_SCRATCH:
        raise Refusal(
            404,
            f"name a session after `{ROOT_SCRATCH}/`: {ROUTE}/{ROOT_SCRATCH}/{{session}}/"
            f"{TREE} or {ROUTE}/{ROOT_SCRATCH}/{{session}}/{BLOB}/{{relpath}}",
            reason="bad-request",
            root=ROOT_SCRATCH,
        )
    if name == ROOT_STORE and _same_tree(paths):
        raise Refusal(
            404,
            f"this project keeps its store inside its code repository, so its `{ROOT_STORE}` "
            f"root is its `{ROOT_CODE}` root and is not served a second time; ask for the "
            f"same path under `{ROUTE}/{ROOT_CODE}/`",
            reason="store-is-code",
            root=ROOT_STORE,
            same_as=ROOT_CODE,
        )
    for root in served_roots(paths):
        if root.name == name:
            return root
    raise Refusal(
        404,
        f"{name!r} is not a root; a project's roots are `{ROOT_CODE}`, `{ROOT_STORE}` and "
        f"`{ROOT_SCRATCH}` (GET {API_PREFIX}/projects/{{project}}/{ROUTE} lists them)",
        reason="no-such-root",
        root=name,
    )


# ------------------------------------------------------------------ what git shows


def _git_shows(root: Root, relpath: str) -> list[str]:
    """The paths git shows at or under ``relpath`` in ``root``, relative to the root.

    Tracked files, and untracked files no ignore rule covers; the whole root when
    ``relpath`` is empty. A nested repository is one entry ending in ``/``. The pathspec is
    literal, so a ``*`` or a ``:(`` in a file's name is not read as a pattern.
    """
    arguments = [
        "--literal-pathspecs",
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
    ]
    if relpath:
        arguments += ["--", relpath]
    try:
        done = run_git(root.path, *arguments)
    except OSError as exc:
        raise _git_failed(root, str(exc)) from exc
    if done.returncode != 0:
        if enclosing_git_repository(root.path) is None:
            raise Refusal(
                409,
                f"kyo serve shows a root's files as git shows them, and the {root.name} root "
                f"{root.path} is not inside a git repository, so nothing is served from it; "
                "run `git init` there",
                reason="not-a-repository",
                root=root.name,
            )
        raise _git_failed(root, (done.stderr or "").strip() or f"exit {done.returncode}")
    return list(dict.fromkeys(item for item in done.stdout.split("\0") if item))


def _git_failed(root: Root, why: str) -> Refusal:
    return Refusal(
        502,
        f"git could not say which files the {root.name} root {root.path} shows, so none "
        f"was served: {why}",
        reason="git-failed",
        root=root.name,
    )


# --------------------------------------------------------------------- the resolver


def _where(root: Root) -> str:
    return f"the {root.name} root"


def _check_text(root: Root, relpath: str) -> list[str]:
    """``relpath``'s segments, checked as text; a ``.git`` segment is refused here."""
    segments = serve_files.check_relpath(relpath, _where(root))
    if any(segment.lower() == ".git" for segment in segments):
        raise Refusal(
            403,
            f"kyo serve will not read {relpath} in {_where(root)}: `.git` is the "
            "repository's own data and is never served",
            reason="not-shown",
            root=root.name,
            path=relpath,
        )
    return segments


def _real_inside(root: Root, relpath: str, segments: list[str]) -> tuple[Path, str]:
    """The real path ``relpath`` names and that path relative to the real root.

    Refused when the real path is outside the root's real path, whatever git says of it.
    """
    real_root = root.real
    real = Path(os.path.realpath(root.path.joinpath(*segments)))
    if real != real_root and not serve_files._within(real_root, real):
        raise Refusal(
            403,
            f"kyo serve will not read {relpath} in {_where(root)}: it resolves outside "
            f"{real_root}, and a file is served from inside its root, never through a link "
            "that leaves it",
            reason="outside-root",
            root=root.name,
            path=relpath,
        )
    return real, real.relative_to(real_root).as_posix() if real != real_root else ""


def _not_shown(root: Root, relpath: str, what: str) -> Refusal:
    if not root.git:
        return Refusal(
            404,
            f"{relpath} is not a {what} in {_where(root)}",
            reason="not-shown",
            root=root.name,
            path=relpath,
        )
    return Refusal(
        404,
        f"{relpath} is not a {what} git shows in {_where(root)}: it is absent, ignored, or "
        f"inside a nested repository. GET {ROUTE}/{root.name}/{TREE} lists what is shown",
        reason="not-shown",
        root=root.name,
        path=relpath,
    )


def _entry(root: Root, relpath: str, *, directory: bool) -> dict[str, Any] | None:
    """One listed path as the routes describe it; None when it is no longer on disk."""
    path = root.path / relpath
    name = relpath.rsplit("/", 1)[-1]
    try:
        info = path.lstat()
    except OSError:
        return None
    entry: dict[str, Any] = {"name": name, "path": relpath, "mtime_ns": info.st_mtime_ns}
    if stat.S_ISLNK(info.st_mode):
        # What a link names is decided when a route is asked for it, by the resolver.
        entry["type"] = "symlink"
    elif directory or stat.S_ISDIR(info.st_mode):
        entry["type"] = "dir"
    elif stat.S_ISREG(info.st_mode):
        kind = media_type(name)
        entry.update(type="file", size=info.st_size, media_type=kind, text=is_text(kind))
    else:
        return None
    return entry


def _file_target(root: Root, relpath: str) -> Path:
    """The real path of the file ``relpath`` names, checked in the module's three steps."""
    segments = _check_text(root, relpath)
    real, real_rel = _real_inside(root, relpath, segments)
    if not root.git:
        if real.is_dir():
            raise Refusal(
                409,
                f"{relpath} is a directory in {_where(root)}; GET "
                f"{ROUTE}/{ROOT_SCRATCH}/{{session}}/{TREE}/{relpath} for its entries",
                reason="is-a-directory",
                root=root.name,
                path=relpath,
            )
        if not real.exists():
            raise _not_shown(root, relpath, "file")
        return real
    shown = _git_shows(root, relpath)
    if relpath not in shown:
        if any(item.startswith(relpath + "/") for item in shown):
            raise Refusal(
                409,
                f"{relpath} is a directory in {_where(root)}; GET "
                f"{ROUTE}/{root.name}/{TREE}/{relpath} for its entries",
                reason="is-a-directory",
                root=root.name,
                path=relpath,
            )
        raise _not_shown(root, relpath, "file")
    if real_rel != relpath and real_rel not in _git_shows(root, real_rel):
        raise _not_shown(root, relpath, "file")
    return real


# ----------------------------------------------------------------------- the reads


def roots_document(paths: serve_files.ProjectPaths) -> dict[str, Any]:
    """The roots: which are served, where each is, and why the others are not."""
    same = _same_tree(paths)
    reported = [
        str(entry["session_id"])
        for entry in cmd_sessions.read_sessions(paths.sessions).entries
        if entry.get("scratchpad_dir")
    ]
    return {
        "ok": True,
        "roots": [
            {"name": ROOT_CODE, "path": str(paths.code_root), "served": True},
            {"name": ROOT_STORE, "path": str(paths.store_root), "served": not same}
            | ({"same_as": ROOT_CODE} if same else {}),
            {
                "name": ROOT_SCRATCH,
                "served": bool(reported),
                "per_session": True,
                "sessions": reported,
                **({} if reported else {"reason": "scratch-not-recorded"}),
            },
        ],
        "max_file_bytes": MAX_FILE_BYTES,
    }


def tree(root: Root, relpath: str) -> dict[str, Any]:
    """Every entry of one directory, in name order; the root itself when ``relpath`` is ''."""
    if relpath:
        segments = _check_text(root, relpath)
        _real, real_rel = _real_inside(root, relpath, segments)
        if real_rel != relpath:
            raise Refusal(
                409,
                f"{relpath} in {_where(root)} is reached through a symbolic link; ask for "
                f"the directory it names, {real_rel or 'the root'}",
                reason="is-a-symlink",
                root=root.name,
                path=relpath,
            )
    if not root.git:
        return _scratch_tree(root, relpath)
    shown = _git_shows(root, relpath)
    prefix = f"{relpath}/" if relpath else ""
    if relpath and relpath in shown:
        raise Refusal(
            409,
            f"{relpath} is a file in {_where(root)}; GET {ROUTE}/{root.name}/{BLOB}/{relpath} "
            "for its bytes",
            reason="is-a-file",
            root=root.name,
            path=relpath,
        )
    children: dict[str, bool] = {}
    for item in shown:
        if not item.startswith(prefix):
            continue
        name, slash, _rest = item[len(prefix) :].partition("/")
        if name:
            children[name] = children.get(name, False) or bool(slash)
    if relpath and not children:
        raise _not_shown(root, relpath, "directory")
    entries = []
    for name in sorted(children):
        entry = _entry(root, prefix + name, directory=children[name])
        if entry is not None:
            entries.append(entry)
    return {"ok": True, "root": root.name, "path": relpath, "entries": entries}


def _scratch_tree(root: Root, relpath: str) -> dict[str, Any]:
    """Every entry of one directory of the ``scratch`` root, in name order; git is not asked."""
    target = root.path / relpath if relpath else root.path
    if relpath and target.is_file():
        raise Refusal(
            409,
            f"{relpath} is a file in {_where(root)}; GET {ROUTE}/{ROOT_SCRATCH}/{{session}}/"
            f"{BLOB}/{relpath} for its bytes",
            reason="is-a-file",
            root=root.name,
            path=relpath,
        )
    try:
        # A scratchpad may hold scratch repositories (KYO-735); their `.git` is never listed,
        # as the text check never reads into one.
        names = sorted(name for name in os.listdir(target) if name.lower() != ".git")
    except OSError as exc:
        raise _not_shown(root, relpath, "directory") from exc
    prefix = f"{relpath}/" if relpath else ""
    entries = [
        entry for name in names if (entry := _entry(root, prefix + name, directory=False))
    ]
    return {"ok": True, "root": root.name, "path": relpath, "entries": entries}


def blob(root: Root, relpath: str, if_none_match: str | None) -> tuple[int, bytes, str, dict]:
    """One file's bytes, type and ``ETag``; 304 and no body when the tag was named."""
    if relpath == "":
        raise Refusal(
            404,
            f"name a file after {ROUTE}/{root.name}/{BLOB}/ (a path relative to "
            f"{_where(root)}); GET {ROUTE}/{root.name}/{TREE} lists them",
            reason="bad-request",
        )
    target = _file_target(root, relpath)
    try:
        info = target.stat()
    except OSError as exc:
        raise _not_shown(root, relpath, "file") from exc
    if not stat.S_ISREG(info.st_mode):
        raise Refusal(
            409,
            f"{relpath} in {_where(root)} is not a regular file, and only a file's bytes are "
            "served",
            reason="not-a-file",
            root=root.name,
            path=relpath,
        )
    if info.st_size > MAX_FILE_BYTES:
        raise Refusal(
            413,
            f"{relpath} is {info.st_size} bytes, and kyo serve returns a file of at most "
            f"{MAX_FILE_BYTES} ({MAX_FILE_BYTES // (1024 * 1024)} MiB); open it on the "
            "machine that holds it",
            reason="file-too-large",
            root=root.name,
            path=relpath,
            size=info.st_size,
            limit=MAX_FILE_BYTES,
        )
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise _not_shown(root, relpath, "file") from exc
    etag = serve_files.etag_of(data)
    headers = {
        "ETag": etag,
        "Cache-Control": _BLOB_CACHE,
        "X-Content-Type-Options": "nosniff",
    }
    kind = content_type(media_type(relpath.rsplit("/", 1)[-1]))
    if if_none_match is not None and (
        if_none_match.strip() == "*" or etag in serve_files._tags(if_none_match)
    ):
        return 304, b"", kind, headers
    return 200, data, kind, headers


# ------------------------------------------------------- a path as an agent writes one


def _lexically_inside(base: Path, path: str) -> str | None:
    """``path`` relative to ``base`` when it is ``base`` or under it, as text; else None."""
    base_text = str(base)
    if path == base_text:
        return ""
    if path.startswith(base_text.rstrip("/") + "/"):
        return path[len(base_text.rstrip("/")) + 1 :]
    return None


def _candidates(root: Root, text: str) -> list[str]:
    """The relative paths ``text`` could name inside ``root``, judged as text alone.

    An absolute path counts only when it is written under the root's path or its real
    path, so nothing outside a root is looked at on disk to answer.
    """
    if text.startswith("/"):
        normal = os.path.normpath(text)
        found = [
            relative
            for base in dict.fromkeys((root.path, root.real))
            if (relative := _lexically_inside(base, normal)) is not None
        ]
        return list(dict.fromkeys(found))
    normal = os.path.normpath(text)
    if normal == ".":
        return [""]
    if normal == ".." or normal.startswith("../"):
        return []
    return [normal]


def _held(root: Root, relpath: str) -> dict[str, Any] | None:
    """The entry for ``relpath`` when ``root`` holds it as something git shows, else None."""
    if relpath == "":
        return {"name": "", "path": "", "type": "dir"}
    try:
        segments = _check_text(root, relpath)
        _real, real_rel = _real_inside(root, relpath, segments)
    except Refusal:
        return None
    shown = _git_shows(root, relpath)
    if relpath in shown:
        if real_rel != relpath and real_rel not in _git_shows(root, real_rel):
            return None
        return _entry(root, relpath, directory=False)
    if any(item.startswith(relpath + "/") for item in shown):
        return _entry(root, relpath, directory=True)
    return None


def resolve(paths: serve_files.ProjectPaths, given: str) -> dict[str, Any]:
    """Where a path written in prose is: its root, relative path and line, or nowhere.

    The text is tried whole first, so a file whose name ends in ``:12`` is found, and then
    without a ``:line`` suffix. ``~/`` is the home of the account the server runs as. A
    relative path is tried against the code root and then the store root.
    """
    if "\x00" in given or not given.strip() or len(given) > MAX_RESOLVE_CHARS:
        raise Refusal(
            400,
            "the resolve route takes ?path=<text>, a path of at most "
            f"{MAX_RESOLVE_CHARS} characters with no NUL byte",
            reason="bad-request",
        )
    text = given.strip()
    forms: list[tuple[str, int | None]] = [(text, None)]
    suffixed = _LINE_SUFFIX.match(text)
    if suffixed:
        forms.append((suffixed.group("path"), int(suffixed.group("line"))))
    roots = served_roots(paths)
    inside_a_root = False
    for form, line in forms:
        written = os.path.expanduser(form) if form.startswith("~/") else form
        if written.startswith("./"):
            written = written[2:] or "."
        for root in roots:
            for relpath in _candidates(root, written):
                inside_a_root = True
                entry = _held(root, relpath)
                if entry is not None:
                    return {
                        "ok": True,
                        "given": given,
                        "found": True,
                        "root": root.name,
                        "line": line,
                        **entry,
                    }
    line = forms[-1][1]
    absolute = text.startswith(("/", "~/"))
    if absolute and not inside_a_root:
        reason = "outside-every-root"
        detail = "the path lies outside every root this project serves"
    elif absolute and any(os.path.lexists(os.path.expanduser(form)) for form, _ in forms):
        # Inside a root and on disk, but not something git shows: build output, an ignored
        # file. It is not served, and a client may still offer to copy or reveal the path.
        reason = "not-shown"
        detail = "the path is inside a root and on disk, but git does not show it there"
    else:
        reason = "not-found"
        detail = "no root holds a file or directory git shows at that path"
    return {
        "ok": True,
        "given": given,
        "found": False,
        "root": None,
        "path": None,
        "line": line,
        "reason": reason,
        "detail": detail,
        "roots": [root.name for root in roots],
    }


# ------------------------------------------------------------------------ the search


def _regular_file(root: Root, relpath: str, shown: set[str]) -> Path | None:
    """The real path of ``relpath`` when the blob route would serve it, else None.

    The tree's own set is ``shown`` (one git call per root), so the check is the blob
    route's without a git process per file: a path of no forbidden text, whose real path
    lies inside the root and, when it is another path, is shown too.
    """
    try:
        segments = serve_files.check_relpath(relpath, _where(root))
    except Refusal:
        return None
    if any(segment.lower() == ".git" for segment in segments):
        return None
    real = Path(os.path.realpath(root.path.joinpath(*segments)))
    if real != root.real and not serve_files._within(root.real, real):
        return None
    real_rel = real.relative_to(root.real).as_posix()
    if real_rel != relpath and real_rel not in shown:
        return None
    return real


def _mtime_ns(real: Path | None) -> int | None:
    """A file's modification time, by which a client orders what it found (KYO-818)."""
    try:
        return real.stat().st_mtime_ns if real is not None else None
    except OSError:
        return None


def _lines_holding(real: Path, needle: str) -> list[tuple[int, str]]:
    """The ``(line number, line)`` pairs of a text file holding ``needle``, lower-cased.

    A file over :data:`MAX_FILE_BYTES`, an unreadable one and one with a NUL byte near its
    start (binary) are read for nothing.
    """
    try:
        info = real.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            return []
        data = real.read_bytes()
    except OSError:
        return []
    if b"\0" in data[:_BINARY_SNIFF]:
        return []
    found = []
    for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
        if needle in line.lower():
            found.append((number, line[:MAX_LINE_CHARS]))
    return found


def search(paths: serve_files.ProjectPaths, text: str, only_root: str | None) -> dict[str, Any]:
    """Files whose path or whose lines hold ``text``, case-insensitively, as one phrase.

    The files searched are the ones the tree serves (git's listing, per root). The contents
    are read directly rather than by ``git grep``, which would not see an untracked file and
    would read what the size limit forbids. Results are in root order, and within a root the
    files whose name matches come before the matching lines; the first
    :data:`SEARCH_LIMIT` are returned and ``more`` says whether another was found. A file gives
    at most :data:`SEARCH_LINES_PER_FILE` lines, each carrying the file's ``count`` of
    matching lines, and every result carries the file's ``mtime_ns`` (KYO-818).
    """
    if not text.strip():
        raise Refusal(
            400,
            f"name the text to find: GET {ROUTE}/{SEARCH}?q=<text>",
            reason="bad-request",
        )
    if len(text) > MAX_QUERY_CHARS:
        raise Refusal(
            400,
            f"the search text is {len(text)} characters, and kyo serve takes at most "
            f"{MAX_QUERY_CHARS}",
            reason="bad-request",
        )
    roots = [_root(paths, only_root)] if only_root else served_roots(paths)
    needle = text.lower()
    results: list[dict[str, Any]] = []
    more = False
    for root in roots:
        shown = [item for item in _git_shows(root, "") if not item.endswith("/")]
        shown_set = set(shown)
        by_name = [item for item in shown if needle in item.lower()]
        by_contents: list[dict[str, Any]] = []
        found: list[dict[str, Any]] = [
            {
                "root": root.name, "path": item, "match": "name",
                "mtime_ns": _mtime_ns(_regular_file(root, item, shown_set)),
            }
            for item in by_name
        ]
        for item in shown:
            real = _regular_file(root, item, shown_set)
            if real is None:
                continue
            lines = _lines_holding(real, needle)
            mtime = _mtime_ns(real) if lines else None
            for number, line in lines[:SEARCH_LINES_PER_FILE]:
                by_contents.append({
                    "root": root.name, "path": item, "match": "contents", "line": number,
                    "text": line, "count": len(lines), "mtime_ns": mtime,
                })
            if len(results) + len(found) + len(by_contents) > SEARCH_LIMIT:
                break
        found += by_contents
        room = SEARCH_LIMIT - len(results)
        results += found[:room]
        if len(found) > room:
            more = True
            break
    return {
        "ok": True,
        "query": text,
        "roots": [root.name for root in roots],
        "limit": SEARCH_LIMIT,
        "more": more,
        "results": results,
    }


# ------------------------------------------------------------------ the recent list


def _recent_entries(environ: dict[str, str], project: str) -> list[dict[str, Any]]:
    """The saved list, newest first; one that is absent or unreadable is an empty one."""
    try:
        document = json.loads(
            serve_files.project_recent_path(environ, project).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return []
    entries = document.get("entries") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        return []
    return [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("root"), str)
        and isinstance(entry.get("path"), str)
    ]


def _recent_document(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {"ok": True, "limit": RECENT_LIMIT, "entries": entries}


def recent(environ: dict[str, str], project: str) -> dict[str, Any]:
    """The files opened in the app for ``project``, newest first."""
    return _recent_document(_recent_entries(environ, project)[:RECENT_LIMIT])


def record_recent(
    environ: dict[str, str],
    paths: serve_files.ProjectPaths,
    project: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Put one opened file at the top of the list; a path the blob route refuses is refused."""
    root_name, relpath = body.get("root"), body.get("path")
    if not isinstance(root_name, str) or not isinstance(relpath, str):
        raise Refusal(
            400,
            f'the JSON body needs "root" (`{ROOT_CODE}` or `{ROOT_STORE}`) and "path" (a '
            "path relative to that root), both text",
            reason="bad-request",
        )
    root = _root(paths, root_name)
    target = _file_target(root, relpath)
    try:
        regular = stat.S_ISREG(target.stat().st_mode)
    except OSError as exc:
        raise _not_shown(root, relpath, "file") from exc
    if not regular:
        raise Refusal(
            409,
            f"{relpath} in {_where(root)} is not a regular file, and only a file is listed "
            "as opened",
            reason="not-a-file",
            root=root.name,
            path=relpath,
        )
    entry = {
        "root": root.name,
        "path": relpath,
        "opened_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    with _RECENT_LOCK:
        kept = [
            item
            for item in _recent_entries(environ, project)
            if (item["root"], item["path"]) != (root.name, relpath)
        ]
        entries = ([entry] + kept)[:RECENT_LIMIT]
        file = serve_files.project_recent_path(environ, project, create=True)
        serve_files.write_private(
            file, json.dumps({"entries": entries}, indent=1).encode("utf-8")
        )
    return _recent_document(entries)


# ---------------------------------------------------------------- the starred list


def _starred_entries(environ: dict[str, str], project: str) -> list[dict[str, Any]]:
    """The saved list, newest first; one that is absent or unreadable is an empty one."""
    try:
        document = json.loads(
            serve_files.project_starred_path(environ, project).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return []
    entries = document.get("entries") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        return []
    return [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("root"), str)
        and isinstance(entry.get("path"), str)
    ]


def _starred_document(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {"ok": True, "limit": STARRED_LIMIT, "entries": entries}


def starred(environ: dict[str, str], project: str) -> dict[str, Any]:
    """The files starred for ``project``, in the order they were starred, newest first."""
    return _starred_document(_starred_entries(environ, project))


def _starred_body(body: dict[str, Any]) -> tuple[str, str]:
    root_name, relpath = body.get("root"), body.get("path")
    if not isinstance(root_name, str) or not isinstance(relpath, str):
        raise Refusal(
            400,
            f'the JSON body needs "root" (`{ROOT_CODE}` or `{ROOT_STORE}`) and "path" (a '
            "path relative to that root), both text",
            reason="bad-request",
        )
    return root_name, relpath


def _save_starred(environ: dict[str, str], project: str, entries: list[dict[str, Any]]) -> None:
    file = serve_files.project_starred_path(environ, project, create=True)
    serve_files.write_private(file, json.dumps({"entries": entries}, indent=1).encode("utf-8"))


def star(
    environ: dict[str, str],
    paths: serve_files.ProjectPaths,
    project: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Star one file; a path the blob route refuses is refused, and a repeat changes nothing."""
    root_name, relpath = _starred_body(body)
    root = _root(paths, root_name)
    target = _file_target(root, relpath)
    try:
        regular = stat.S_ISREG(target.stat().st_mode)
    except OSError as exc:
        raise _not_shown(root, relpath, "file") from exc
    if not regular:
        raise Refusal(
            409,
            f"{relpath} in {_where(root)} is not a regular file, and only a file is starred",
            reason="not-a-file",
            root=root.name,
            path=relpath,
        )
    with _STARRED_LOCK:
        kept = _starred_entries(environ, project)
        if any((item["root"], item["path"]) == (root.name, relpath) for item in kept):
            return _starred_document(kept)
        if len(kept) >= STARRED_LIMIT:
            raise Refusal(
                409,
                f"{len(kept)} files are starred, the most a project keeps; DELETE "
                f"{ROUTE}/{STARRED} with one file's root and path unstars it, which makes "
                "room",
                reason="starred-full",
            )
        entry = {
            "root": root.name,
            "path": relpath,
            "starred_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
        entries = [entry, *kept]
        _save_starred(environ, project, entries)
    return _starred_document(entries)


def unstar(
    environ: dict[str, str],
    paths: serve_files.ProjectPaths,
    project: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Unstar one file. The root and the path's text are checked, but the file need not
    still exist, so a star whose file was deleted can be removed; one not starred is a no-op."""
    root_name, relpath = _starred_body(body)
    root = _root(paths, root_name)
    _check_text(root, relpath)
    with _STARRED_LOCK:
        kept = _starred_entries(environ, project)
        entries = [
            item for item in kept if (item["root"], item["path"]) != (root.name, relpath)
        ]
        if len(entries) != len(kept):
            _save_starred(environ, project, entries)
    return _starred_document(entries)


# ------------------------------------------------------------------------ the route


def _no_query(query_pairs: list, what: str) -> None:
    if query_pairs:
        raise Refusal(
            400,
            f"{what} takes no query string; send the token as `Authorization: Bearer "
            "<token>` and the path in the URL's path",
            reason="bad-request",
        )


def not_served(rest: list[str]) -> Refusal:
    """The refusal for a path under :data:`ROUTE` that is none of its routes."""
    return Refusal(
        404,
        f"`{ROUTE}/{'/'.join(rest)}` is not a route; kyo serve answers GET {ROUTE} (the "
        f"roots), GET {ROUTE}/{{root}}/{TREE}[/{{relpath}}] (a directory), GET "
        f"{ROUTE}/{{root}}/{BLOB}/{{relpath}} (a file's bytes) and GET "
        f"{ROUTE}/{RESOLVE}?path=<text>, GET {ROUTE}/{SEARCH}?q=<text>, GET and POST "
        f"{ROUTE}/{RECENT}, GET, POST and DELETE {ROUTE}/{STARRED}, {{root}} being "
        f"`{ROOT_CODE}` or `{ROOT_STORE}`; "
        f"a session's scratchpad is {ROUTE}/{ROOT_SCRATCH}/{{session}}/{TREE} and "
        f"{ROUTE}/{ROOT_SCRATCH}/{{session}}/{BLOB}/{{relpath}}",
        reason="not-served",
    )


def respond(
    environ: dict[str, str],
    root_of: Callable[[], str],
    authorization: str | None,
    rest: list[str],
    query_pairs: list[tuple[str, str, bool]],
    if_none_match: str | None,
    *,
    method: str = "GET",
    project: str = "",
    read_body: Callable[[], dict[str, Any]] | None = None,
) -> tuple[int, dict[str, Any] | bytes, str | None, dict[str, str]]:
    """Answer one request under :data:`ROUTE`: ``(status, document or bytes, type, headers)``.

    A document is JSON for the caller to encode, with no type; bytes carry theirs. The
    token is checked first, so a request without the permission learns nothing of a path.
    """
    serve_send.require_device(environ, authorization, device_tokens.PERMISSION_FILES)
    if rest and rest[0] == RECENT:
        if len(rest) != 1:
            raise not_served(rest)
        _no_query(query_pairs, f"`{ROUTE}/{RECENT}`")
        if method == "POST":
            assert read_body is not None
            paths = serve_files.project_paths(root_of(), environ)
            return 200, record_recent(environ, paths, project, read_body()), None, {}
        return 200, recent(environ, project), None, {}
    if rest and rest[0] == STARRED:
        if len(rest) != 1:
            raise not_served(rest)
        _no_query(query_pairs, f"`{ROUTE}/{STARRED}`")
        if method in ("POST", "DELETE"):
            assert read_body is not None
            paths = serve_files.project_paths(root_of(), environ)
            change = star if method == "POST" else unstar
            return 200, change(environ, paths, project, read_body()), None, {}
        return 200, starred(environ, project), None, {}
    if method != "GET":
        raise Refusal(
            405,
            f"{method} is not served under {ROUTE}; only {ROUTE}/{RECENT} and "
            f"{ROUTE}/{STARRED} are written",
            reason="bad-request",
        )
    if rest and rest[0] == SEARCH:
        if len(rest) != 1:
            raise not_served(rest)
        keys = [key for key, _value, _bare in query_pairs]
        if keys.count("q") != 1 or any(key not in ("q", "root") for key in keys) or keys.count(
            "root"
        ) > 1:
            raise Refusal(
                400,
                f"the search route takes ?q=<text> and optionally &root=<root>: GET "
                f"{ROUTE}/{SEARCH}?q=widget",
                reason="bad-request",
            )
        values = {key: value for key, value, _bare in query_pairs}
        paths = serve_files.project_paths(root_of(), environ)
        return 200, search(paths, values["q"], values.get("root")), None, {}
    if rest and rest[0] == RESOLVE:
        if len(rest) != 1:
            raise not_served(rest)
        given = [value for key, value, _bare in query_pairs if key == "path"]
        if len(given) != 1 or len(query_pairs) != 1:
            raise Refusal(
                400,
                f"the resolve route takes exactly one query, ?path=<text>: GET "
                f"{ROUTE}/{RESOLVE}?path=src/main.py:12",
                reason="bad-request",
            )
        paths = serve_files.project_paths(root_of(), environ)
        return 200, resolve(paths, given[0]), None, {}
    if not rest:
        _no_query(query_pairs, f"`{ROUTE}`")
        return 200, roots_document(serve_files.project_paths(root_of(), environ)), None, {}
    if rest[0] != ROOT_SCRATCH and (len(rest) < 2 or rest[1] not in (TREE, BLOB)):
        raise not_served(rest)
    paths = serve_files.project_paths(root_of(), environ)
    if rest[0] == ROOT_SCRATCH:
        if len(rest) < 3 or rest[2] not in (TREE, BLOB):
            raise _root(paths, ROOT_SCRATCH)
        _no_query(query_pairs, f"`{ROUTE}/{ROOT_SCRATCH}/{{session}}/{rest[2]}`")
        root = _scratch_root(paths, rest[1])
        operation, relpath = rest[2], "/".join(rest[3:])
    else:
        _no_query(query_pairs, f"`{ROUTE}/{{root}}/{rest[1]}`")
        root = _root(paths, rest[0])
        operation, relpath = rest[1], "/".join(rest[2:])
    if operation == TREE:
        return 200, tree(root, relpath), None, {}
    status, data, kind, headers = blob(root, relpath, if_none_match)
    return status, data, kind, headers
