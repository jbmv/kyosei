"""``kyo transcripts``: a search of what the person and the assistant said (KYO-812).

WHAT IS SEARCHED. In a project that opted in to reading transcripts (KYO-656), the text of
the person's prompts and of the assistant's replies on the main thread, across every session
the project knows of. The transcripts are read by
:class:`kyosei.harness.claude_code_transcript.SessionTranscript`, the reader ``kyo serve``'s
chat is drawn from, so a message is indexed exactly when the chat shows it: its ``turns``
and ``replies`` and nothing else. Tool calls, tool results, thinking, slash-command output,
messages delivered by anything but the person, and subagent transcripts are not indexed.

WHICH SESSIONS. The ids in ``sessions.json``, the ids the turn index names (KYO-657), and
the ids of the transcripts in the project's archive (KYO-683). ``sessions.json`` keeps only
the most recent sessions, so the other two are what reach the older ones. Each session's
transcript is found as the session layer finds it: the original while Claude Code keeps it,
and the archive's copy afterwards.

THE INDEX IS DERIVED. It is one SQLite file,
``$KYOSEI_HOME/transcript-search/<project>/index.db``, a sibling of the transcript archive
and named as the archive's directory is. It holds the text of the transcripts, so it is
kept where they are kept: outside every git repository (:func:`index_location_problem`
refuses a location inside one, as the archive's check does), in a directory of mode 0700
and a file of mode 0600. It is not beside ``kyosei.db``, because no file in a store's
directory holds a transcript's words, and under the in-repo layout that directory is in the
code repository. It is a file of its own rather than tables in ``kyosei.db`` because that
database is rebuilt from ``kyosei/export/``, which is committed: nothing here is exported,
tracked or logged. Deleting the directory loses nothing; ``kyo transcripts rebuild`` writes
it again from the transcripts.

KEPT UP TO DATE ON READ. Every search first compares each session's transcript (its path,
size and modification time) with what the index recorded, and reads again only the sessions
that differ. A session's rows are replaced whole, since a reply is joined from several
transcript records and the reader needs the file from its start. No hook writes the index:
the Stop hook runs after every turn and a search that follows it would find the session
changed in any case.

THE MATCH is the one find makes in the chat (KYO-754): the query as typed, as one phrase,
case ignored, a run of spaces matching any run of white space, and no character an
operator. The FTS5 table uses the trigram tokenizer, which finds a substring of three or
more characters, and only narrows the rows: every candidate is then matched in Python by
the rule above, which is also what places the marks in the excerpt. A query with no run of
three characters, or an SQLite built without FTS5 or the trigram tokenizer, reads every row
instead and answers the same.

The text matched is the transcript's, which for a reply is its markdown source. Find in the
chat matches the text as drawn, so a phrase that spans a markdown mark is found there and
not here.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sqlite3
from argparse import Namespace
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import cmd_turns
from .cmd_sessions import expand_home, read_sessions, sessions_path
from .commands import Context, Output
from .config import (
    READ_TRANSCRIPTS_KEY,
    Config,
    load_config,
    project_home_name,
    store_home,
    transcripts_opted_in,
)
from .errors import KyoseiError, UsageError
from .harness import claude_code_transcript as transcript
from .transcript_archive import (
    archive_dir,
    archived_session_ids,
    enclosing_git_repository,
    project_archive_name,
)

#: The directory directly under ``$KYOSEI_HOME`` holding one index directory per project.
INDEX_DIRNAME = "transcript-search"
INDEX_NAME = "index.db"

#: The index's layout, kept in ``PRAGMA user_version``; an index of another is rebuilt.
FORMAT = 1

ROLE_PERSON = "person"
ROLE_ASSISTANT = "assistant"

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

#: How many characters of the message an excerpt keeps on each side of the first match.
EXCERPT_CONTEXT = 80

#: The shortest run of characters the trigram tokenizer can look up.
_TRIGRAM = 3

NOT_OPTED_IN = (
    "this project has not opted in to reading transcripts, so its sessions are neither "
    f"indexed nor searched; to opt in, set `{READ_TRANSCRIPTS_KEY}: true` in kyosei/config.yml"
)

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

_COLUMNS = "session_id, message, message_id, role, at, text"

#: ``text`` is last, so that reading the columns before it never walks its overflow pages.
_FTS_TABLE = (
    "CREATE VIRTUAL TABLE messages USING fts5("
    "session_id UNINDEXED, message UNINDEXED, message_id UNINDEXED, role UNINDEXED, "
    "at UNINDEXED, text, tokenize='trigram')"
)
_PLAIN_TABLE = (
    "CREATE TABLE messages (session_id TEXT, message TEXT, message_id TEXT, role TEXT, "
    "at TEXT, text TEXT)"
)
_FILES_TABLE = (
    "CREATE TABLE files (session_id TEXT PRIMARY KEY, path TEXT NOT NULL, "
    "size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL)"
)


# ------------------------------------------------------------------------ the phrase


def fold(text: str) -> str:
    """``text`` in lower case, every character kept at its own length.

    A position in the result is then a position in the text. The chat's find folds the same
    way (``apps/apple/web/src/search.ts``).
    """
    lower = text.lower()
    if len(lower) == len(text):
        return lower
    return "".join(small if len(small := char.lower()) == 1 else char for char in text)


def phrase_parts(query: str) -> list[str]:
    """The query's runs of characters between white space, folded; empty for a blank query."""
    return fold(query).split()


def phrase_pattern(parts: list[str]) -> re.Pattern[str]:
    """What matches the phrase in folded text: its parts in order, white space between."""
    return re.compile(r"\s+".join(re.escape(part) for part in parts))


def find_spans(text: str, pattern: re.Pattern[str]) -> list[tuple[int, int]]:
    """Where the phrase occurs in ``text``, as ``(start, end)`` spans that do not overlap."""
    return [found.span() for found in pattern.finditer(fold(text))]


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def excerpt(text: str, spans: list[tuple[int, int]]) -> list[dict]:
    """The text around the first match, as pieces each marked as matched or not.

    Pieces rather than offsets, so that no client counts characters the way Python does. A
    run of white space is drawn as one space, and an ellipsis stands where the text is cut.
    """
    first_start, first_end = spans[0]
    start = max(0, first_start - EXCERPT_CONTEXT)
    end = min(len(text), first_end + EXCERPT_CONTEXT)
    pieces: list[dict] = []
    at = start
    for span_start, span_end in spans:
        if span_start >= end:
            break
        span_end = min(span_end, end)
        if span_start > at:
            pieces.append({"text": _one_line(text[at:span_start]), "match": False})
        pieces.append({"text": _one_line(text[span_start:span_end]), "match": True})
        at = span_end
    if at < end:
        pieces.append({"text": _one_line(text[at:end]), "match": False})
    if start > 0:
        pieces.insert(0, {"text": "…", "match": False})
    if end < len(text):
        pieces.append({"text": "…", "match": False})
    return pieces


# ---------------------------------------------------------------------- the sessions


def index_dir(config: Config, environ: Mapping[str, str] | None = None) -> Path:
    """This project's index directory. Nothing here creates it."""
    env = None if environ is None else dict(environ)
    return store_home(env) / INDEX_DIRNAME / project_archive_name(config)


def index_path(config: Config, environ: Mapping[str, str] | None = None) -> Path:
    return index_dir(config, environ) / INDEX_NAME


def index_location_problem(directory: Path) -> str | None:
    """Why ``directory`` must not hold the index, or None when no git repository encloses it."""
    repository = enclosing_git_repository(directory)
    if repository is None:
        return None
    return (
        f"the transcript search index {directory} is inside the git repository at "
        f"{repository}, where the text of a transcript could be committed; point $KYOSEI_HOME "
        "at a directory outside every git repository"
    )


def project_session_ids(
    config: Config, store_root: Path | str, environ: Mapping[str, str] | None = None
) -> set[str]:
    """Every session id this project records: ``sessions.json``, the turn index, the archive.

    All three are this project's own files, so an id found here is one of its sessions and
    never another project's.
    """
    env = dict(os.environ if environ is None else environ)
    ids = {
        str(entry.get("session_id")) for entry in read_sessions(sessions_path(store_root)).entries
    }
    ids |= {str(line.get("session")) for line in cmd_turns.read_lines(store_root)}
    ids |= set(archived_session_ids(archive_dir(config, env)))
    return {session_id for session_id in ids if _UUID.match(session_id)}


def _recorded_paths(store_root: Path | str) -> dict[str, Path]:
    recorded: dict[str, Path] = {}
    for entry in read_sessions(sessions_path(store_root)).entries:
        path = expand_home(entry.get("transcript_path"))
        if path is not None:
            recorded[str(entry.get("session_id"))] = path
    return recorded


# -------------------------------------------------------------------------- the index


@dataclass
class Sync:
    """What bringing the index up to date did."""

    sessions: int = 0
    #: Sessions whose transcript was read again.
    read: list[str] = field(default_factory=list)
    #: Sessions whose rows were dropped because no transcript of theirs remains.
    removed: list[str] = field(default_factory=list)
    #: Transcripts that could not be read, named without their contents.
    problems: list[str] = field(default_factory=list)

    def shown(self) -> dict:
        return {
            "sessions": self.sessions,
            "read": len(self.read),
            "removed": len(self.removed),
            "problems": self.problems,
        }


def _create(conn: sqlite3.Connection) -> None:
    """Create the tables, unless another process did so while this one waited to."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]:
            try:
                conn.execute(_FTS_TABLE)
            except sqlite3.OperationalError:
                # No FTS5, or no trigram tokenizer (SQLite before 3.34): a plain table,
                # read whole.
                conn.execute(_PLAIN_TABLE)
            conn.execute(_FILES_TABLE)
            conn.execute(f"PRAGMA user_version = {FORMAT}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _connect(directory: Path) -> sqlite3.Connection:
    """Open the index, creating it empty when it is absent, damaged or of another layout.

    Refuses a directory inside a git repository before anything is written.
    """
    problem = index_location_problem(directory)
    if problem is not None:
        raise KyoseiError(problem)
    path = directory / INDEX_NAME
    for attempt in (0, 1):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not path.exists():
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
        conn = sqlite3.connect(path, timeout=30.0)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            tables = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
            if version == FORMAT and tables:
                return conn
            if not tables:
                _create(conn)
                return conn
        except sqlite3.OperationalError:
            conn.close()  # busy or unwritable, which is not damage: the file is left alone
            raise
        except sqlite3.DatabaseError:
            if attempt:
                conn.close()
                raise
        conn.close()
        shutil.rmtree(directory)
    raise AssertionError("unreachable")


def _uses_fts(conn: sqlite3.Connection) -> bool:
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'messages'").fetchone()[0]
    return "fts5" in sql


def _rows_of(session_id: str, path: Path) -> list[tuple[str, str, str | None, str, str, str]]:
    """The session's indexed messages: what the chat draws as a prompt or as a reply."""
    reader = transcript.SessionTranscript(path)
    reader.read()
    rows: list[tuple[str, str, str | None, str, str, str]] = []
    for turn in reader.turns:
        if turn.full_text.strip():
            rows.append((session_id, turn.id, None, ROLE_PERSON, turn.timestamp, turn.full_text))
    for reply in reader.replies:
        if reply.text.strip():
            rows.append(
                (session_id, reply.uuid, reply.message_id, ROLE_ASSISTANT, reply.at, reply.text)
            )
    return rows


def _sync(
    conn: sqlite3.Connection,
    config: Config,
    store_root: Path | str,
    environ: Mapping[str, str] | None,
) -> Sync:
    env = dict(os.environ if environ is None else environ)
    ids = project_session_ids(config, store_root, env)
    done = Sync(sessions=len(ids))
    held = {
        row[0]: (row[1], row[2], row[3])
        for row in conn.execute("SELECT session_id, path, size, mtime_ns FROM files")
    }
    recorded = _recorded_paths(store_root)
    claude = transcript.config_dir(env)
    archive = archive_dir(config, env)
    changed: list[tuple[str, Path | None, tuple[str, int, int] | None]] = []
    for session_id in sorted(ids | set(held)):
        source: Path | None = None
        if session_id in ids:
            known = held.get(session_id)
            if known is not None and Path(known[0]).is_file():
                source = Path(known[0])  # one stat, rather than a look in every directory
            else:
                found = transcript.candidate_transcripts(
                    session_id, config=claude, recorded=recorded.get(session_id), archive=archive
                )
                source = found[0] if found else None
        stamp = None
        if source is not None:
            try:
                stat = source.stat()
                stamp = (str(source), stat.st_size, stat.st_mtime_ns)
            except OSError:
                source = None
        if held.get(session_id) != stamp:
            changed.append((session_id, source, stamp))
    if not changed:
        return done
    conn.execute("BEGIN IMMEDIATE")
    try:
        for session_id, source, stamp in changed:
            rows: list[tuple] = []
            if source is not None:
                try:
                    rows = _rows_of(session_id, source)
                except OSError as exc:
                    done.problems.append(f"{source.name}: {exc.strerror or exc}")
                    continue
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM files WHERE session_id = ?", (session_id,))
            if stamp is None:
                done.removed.append(session_id)
                continue
            conn.executemany(f"INSERT INTO messages ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)", rows)
            conn.execute("INSERT INTO files VALUES (?, ?, ?, ?)", (session_id, *stamp))
            done.read.append(session_id)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return done


def _require_opt_in(config: Config) -> None:
    if not transcripts_opted_in(config):
        raise KyoseiError(NOT_OPTED_IN)


def rebuild(
    config: Config, store_root: Path | str, environ: Mapping[str, str] | None = None
) -> Sync:
    """Delete the index and write it again from the transcripts and the archive."""
    _require_opt_in(config)
    directory = index_dir(config, environ)
    if directory.exists():
        shutil.rmtree(directory)
    conn = _connect(directory)
    try:
        return _sync(conn, config, store_root, environ)
    finally:
        conn.close()


# -------------------------------------------------------------------------- the search


def _cursor_text(at: str, message: str, session_id: str) -> str:
    raw = json.dumps([at, message, session_id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _cursor_key(cursor: str | None) -> tuple[str, str, str] | None:
    """The place a ``cursor`` of an earlier response names, or a refusal."""
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        at, message, session_id = json.loads(raw)
        if not all(isinstance(part, str) for part in (at, message, session_id)):
            raise ValueError(cursor)
    except (ValueError, TypeError) as exc:
        raise UsageError(
            "the cursor is not one a transcript search returned; pass the `cursor` of the "
            "previous response unchanged, or none for the first page"
        ) from exc
    return at, message, session_id


def _candidates(
    conn: sqlite3.Connection, parts: list[str], after: tuple[str, str, str] | None
) -> Iterator[tuple]:
    """Rows that may hold the phrase, newest first, past the cursor's place."""
    where: list[str] = []
    values: list[str] = []
    long_parts = [part for part in parts if len(part) >= _TRIGRAM]
    if long_parts and _uses_fts(conn):
        where.append("messages MATCH ?")
        quoted = " AND ".join('"' + part.replace('"', '""') + '"' for part in long_parts)
        values.append(f"text : ({quoted})")
    if after is not None:
        where.append("(at, message, session_id) < (?, ?, ?)")
        values.extend(after)
    condition = f"WHERE {' AND '.join(where)} " if where else ""
    return conn.execute(
        f"SELECT {_COLUMNS} FROM messages {condition}"
        "ORDER BY at DESC, message DESC, session_id DESC",
        values,
    )


def search(
    config: Config,
    store_root: Path | str,
    project: str,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict:
    """One page of the messages holding ``query`` as a phrase, newest first.

    Refuses in a project that has not opted in (KYO-656), before any transcript is opened.
    """
    _require_opt_in(config)
    parts = phrase_parts(query)
    if not parts:
        raise UsageError(
            "a transcript search needs a phrase to look for; give the words to find, "
            'as in `kyo transcripts search "the words"`'
        )
    if not 1 <= limit <= MAX_LIMIT:
        raise UsageError(
            f"a transcript search returns between 1 and {MAX_LIMIT} results a page, not "
            f"{limit}; ask for a number in that range and pass the response's cursor for "
            "the next page"
        )
    after = _cursor_key(cursor)
    pattern = phrase_pattern(parts)
    conn = _connect(index_dir(config, environ))
    try:
        synced = _sync(conn, config, store_root, environ)
        results: list[dict] = []
        more = False
        for session_id, message, message_id, role, at, text in _candidates(conn, parts, after):
            spans = find_spans(text, pattern)
            if not spans:
                continue
            if len(results) == limit:
                more = True
                break
            results.append(
                {
                    "project": project,
                    "session_id": session_id,
                    "message": message,
                    "message_id": message_id,
                    "role": role,
                    "at": at,
                    "matches": len(spans),
                    "excerpt": excerpt(text, spans),
                }
            )
        messages = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
    finally:
        conn.close()
    last = results[-1] if more else None
    return {
        "ok": True,
        "project": project,
        "query": query,
        "results": results,
        "more": more,
        "cursor": _cursor_text(last["at"], last["message"], last["session_id"]) if last else None,
        "index": {**synced.shown(), "messages": messages},
    }


# ------------------------------------------------------------------------ the command


def _marked(pieces: list[dict]) -> str:
    return "".join(f"[{piece['text']}]" if piece["match"] else piece["text"] for piece in pieces)


def _config_of(ctx: Context) -> Config:
    layout = ctx.layout
    return load_config(
        Path(ctx.root),
        code_root=layout.code_root if layout is not None else None,
        store_via_main_checkout=layout.store_via_main_checkout if layout is not None else None,
    )


def cmd_transcripts(ctx: Context, args: Namespace) -> Output:
    """``kyo transcripts search PHRASE [--limit N] [--cursor C]``, ``kyo transcripts rebuild``."""
    config = _config_of(ctx)
    phrase = " ".join(args.phrase)
    if args.action == "rebuild":
        if phrase or args.cursor or args.limit is not None:
            raise UsageError(
                "kyo transcripts rebuild takes no argument; run `kyo transcripts rebuild`"
            )
        done = rebuild(config, ctx.root, ctx.environ)
        lines = [
            f"transcripts: index rebuilt at {index_path(config, ctx.environ)} from "
            f"{len(done.read)} of {done.sessions} session(s); the others have no transcript "
            "left to read"
        ]
        lines += [f"  could not be read: {problem}" for problem in done.problems]
        return Output(
            lines,
            {"ok": True, "command": "transcripts", "action": "rebuild", "index": done.shown()},
        )
    project = project_home_name(ctx.layout) if ctx.layout is not None else Path(ctx.root).name
    found = search(
        config,
        ctx.root,
        project,
        phrase,
        limit=DEFAULT_LIMIT if args.limit is None else args.limit,
        cursor=args.cursor,
        environ=ctx.environ,
    )
    results = found["results"]
    lines = [f"transcripts: {len(results)} message(s) holding the phrase, newest first"]
    if not results:
        lines = ["transcripts: no message holds the phrase"]
    for result in results:
        lines.append(
            f"  {result['at']}  {result['role']:<9}  session {result['session_id']}  "
            f"message {result['message']}"
        )
        lines.append(f"      {_marked(result['excerpt'])}")
    if found["more"]:
        lines.append(f"  more: pass --cursor {found['cursor']}")
    return Output(lines, {**found, "command": "transcripts", "action": "search"})
