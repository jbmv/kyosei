"""The local archive of a session's Claude Code transcripts (KYO-683, decision KYO-681 D3).

Claude Code deletes transcripts after ``cleanupPeriodDays``, and an earlier deletion cost this
project sessions it could no longer check credits against. In a project that opted in to
reading transcripts (:func:`kyosei.config.transcripts_opted_in`, KYO-656), the Stop and
SessionEnd hook copies the session's transcript and its subagent transcripts here, and the
credit reader falls back to the copy once the original is gone.

WHERE. ``$KYOSEI_HOME/transcript-archive/<project>/``. A store under the split layout is its
own git repository at ``$KYOSEI_HOME/<project>/``, so the archive is a sibling of the stores
and never a child of one. Directories are created mode 0700 and files 0600, because a
transcript holds what was said and what the agent read. :func:`archive_location_problem`
refuses a location inside any git repository; the hook writes nothing there and
``kyo doctor`` reports it, so a transcript cannot reach a repository's history by an
unnoticed ``$KYOSEI_HOME``.

THE LAYOUT MIRRORS CLAUDE CODE'S. ``<session id>.jsonl``, and
``<session id>/subagents/*.jsonl`` for the subagents. The name comes from the transcript
file, one session id per file, so ``/clear``, which starts a new transcript under a new
session id, simply gives the next Stop a second file to copy and nothing assumes one file
per run of a session.

COPYING IS INCREMENTAL, BECAUSE THE HOOK RUNS AFTER EVERY TURN. A transcript is appended to,
so the bytes past the archive copy's length are what is new. Equal lengths with an unchanged
tail write nothing. A source that shrank, or whose bytes under the copy's tail changed, was
rewritten by something other than an append: the existing copy is kept beside the new one as
``<name>.rewritten-<time>.jsonl`` rather than overwritten, and
:func:`kyosei.harness.claude_code_transcript.archived_transcripts` reads both.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import TRANSCRIPT_ARCHIVE_DIRNAME, Config, store_home

#: How much of a copy's tail is compared with the source before bytes are appended.
TAIL_COMPARE_BYTES = 4096

_DIR_MODE = 0o700
_FILE_MODE = 0o600

_SESSION_FILE = re.compile(r"^[0-9a-fA-F-]{36}$")


def project_archive_name(config: Config) -> str:
    """The per-project directory's name, on :func:`kyosei.config.project_home_name`'s rule."""
    if config.repo_root.resolve() != config.root.resolve():
        return config.root.name
    through = config.store_via_main_checkout
    return (through if through is not None else config.root).name


def archive_dir(config: Config, environ: dict[str, str] | None = None) -> Path:
    """Where this project's transcripts are archived. Nothing here creates it."""
    return store_home(environ) / TRANSCRIPT_ARCHIVE_DIRNAME / project_archive_name(config)


def enclosing_git_repository(path: Path) -> Path | None:
    """The directory holding ``.git`` at or above ``path``, or None.

    Walks the resolved path's ancestors, so a symbolic link cannot hide a repository. A
    linked worktree's ``.git`` is a file, and it counts. Nothing is asked of git, since this
    runs from a hook on every turn.
    """
    resolved = path.resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def archive_location_problem(archive: Path) -> str | None:
    """Why ``archive`` must not be used, or None when it lies outside every git repository."""
    repository = enclosing_git_repository(archive)
    if repository is None:
        return None
    return (
        f"the transcript archive {archive} is inside the git repository at {repository}, "
        "where a transcript could be committed; point $KYOSEI_HOME at a directory outside "
        "every git repository"
    )


@dataclass
class ArchiveResult:
    """What one archiving run did."""

    #: Copies that received new bytes, as paths relative to the archive.
    written: list[str] = field(default_factory=list)
    bytes_copied: int = 0
    #: Files that could not be copied, named without their contents.
    problems: list[str] = field(default_factory=list)


def _same_tail(source: Path, copy: Path, copy_size: int) -> bool:
    """Whether the last bytes of ``copy`` are the bytes at the same offset in ``source``."""
    span = min(TAIL_COMPARE_BYTES, copy_size)
    offset = copy_size - span
    with source.open("rb") as left, copy.open("rb") as right:
        left.seek(offset)
        right.seek(offset)
        return left.read(span) == right.read(span)


def _copy_new_bytes(source: Path, copy: Path) -> int:
    """Append what ``source`` has past ``copy``'s length; the number of bytes written.

    Returns 0 without opening ``copy`` for writing when there is nothing new.
    """
    source_size = source.stat().st_size
    if copy.exists():
        copy_size = copy.stat().st_size
        if copy_size > source_size or not _same_tail(source, copy, copy_size):
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            copy.rename(copy.with_name(f"{copy.stem}.rewritten-{stamp}{copy.suffix}"))
            copy_size = 0
        elif copy_size == source_size:
            return 0
    else:
        copy_size = 0
    copy.parent.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
    fd = os.open(copy, os.O_WRONLY | os.O_APPEND | os.O_CREAT, _FILE_MODE)
    with os.fdopen(fd, "ab") as sink, source.open("rb") as origin:
        origin.seek(copy_size)
        before = sink.tell()
        shutil.copyfileobj(origin, sink)
        return sink.tell() - before


def archive_session(transcript: Path, archive: Path) -> ArchiveResult:
    """Copy ``transcript`` and its subagent transcripts into ``archive``, new bytes only.

    A file that cannot be read is named in the result and the rest are still copied. The
    caller has already asked :func:`archive_location_problem` and the project's opt-in.
    """
    result = ArchiveResult()
    if not transcript.is_file() or not _SESSION_FILE.match(transcript.stem):
        return result
    sources = [(transcript, archive / transcript.name)]
    subagents = transcript.parent / transcript.stem / "subagents"
    if subagents.is_dir():
        for path in sorted(subagents.rglob("*.jsonl")):
            relative = path.relative_to(transcript.parent)
            sources.append((path, archive / relative))
    for source, copy in sources:
        try:
            copied = _copy_new_bytes(source, copy)
        except OSError as exc:
            result.problems.append(f"{source.name}: {exc.strerror or exc}")
            continue
        if copied:
            result.written.append(str(copy.relative_to(archive)))
            result.bytes_copied += copied
    return result
