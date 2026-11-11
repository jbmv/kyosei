"""``kyo turns``: the turn index and the hook that keeps it (KYO-657, KYO-683).

WHAT IT IS. In a project that has opted in to reading transcripts (KYO-656), the
UserPromptSubmit hook appends one line per prompt to ``kyosei/turns.jsonl``, beside
``sessions.json`` and gitignored like it: session, prompt id, the hook's own time, and
whether the prompt carried pasted content. It stores no words and prints nothing, because
what that hook prints is added to the session's context. A project that has not opted in
gets no file at all and no transcript opened.

WHO SAID IT. UserPromptSubmit's payload says only that a prompt arrived, and it fires for
subagent reports and task notifications as well as for the person, so a new line's
``origin`` is ``unconfirmed``. The Stop and SessionEnd hook reads the transcript records
written since its last run and rewrites the line with the transcript's own ``origin.kind``
(``human``, ``task-notification``, ...), which is what marks a line as the person's or not,
and with the transcript's answer about pastes.

WHAT THE STOP HOOK ADDS. A prompt the transcript holds and the index does not, for any
reason UserPromptSubmit did not fire, gains a line, ``source: transcript``. A prompt the
hook indexed that never appears in the transcript keeps ``origin: unconfirmed``.

A MESSAGE SENT MID-TURN. UserPromptSubmit fires for it, and the payload repeats the prompt
id of the turn it arrived in (observed under KYO-657.c2 and KYO-687). The transcript has no
row carrying that id for the message, only a ``queued_command`` attachment with no
``promptId``. So the hook appends nothing when the session's index already holds the prompt
id, which leaves the enclosing prompt its own line and time, and the Stop hook adds the
message's one line from the attachment, with ``prompt_id`` null and the record's ``uuid``
in ``turn``.

THE LOCK. Every write holds an advisory lock on ``turns-cursor.json``, the sidecar
recording how far each session's transcript has been read, because a Stop hook rewriting
the file and a UserPromptSubmit in another session appending to it would otherwise lose
the appended line. The sidecar is read and rewritten under its lock, and damage to it only
costs a re-read of records whose lines already exist.

The archive half of the Stop hook is :mod:`kyosei.transcript_archive`.
"""

from __future__ import annotations

import json
import os
import sys
from argparse import Namespace
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from . import transcript_archive
from .commands import Context, Output
from .config import (
    KYOSEI_DIR,
    LAYOUT_UNCONFIGURED,
    TURNS_CURSOR_NAME,
    TURNS_NAME,
    Config,
    load_config,
    transcripts_opted_in,
)
from .errors import KyoseiError
from .harness import claude_code_transcript as transcript

try:
    import fcntl
except ImportError:  # pragma: no cover - not available on Windows
    fcntl = None  # type: ignore[assignment]

#: A line's ``origin`` until the transcript has been read.
UNCONFIRMED = "unconfirmed"
#: The ``origin`` of a person's turn, the transcript's own word.
HUMAN = "human"
#: A line's ``source``: written by the UserPromptSubmit hook, or added from the transcript.
SOURCE_HOOK = "hook"
SOURCE_TRANSCRIPT = "transcript"

_PASTED_ELEMENT_OPEN = "<pasted_content"


def turns_path(store_root: Path | str) -> Path:
    """``<store>/kyosei/turns.jsonl``."""
    return Path(store_root) / KYOSEI_DIR / TURNS_NAME


def _cursor_path(store_root: Path | str) -> Path:
    return Path(store_root) / KYOSEI_DIR / TURNS_CURSOR_NAME


def _now() -> str:
    moment = datetime.now(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


@contextmanager
def _locked(store_root: Path | str) -> Iterator[int]:
    """Hold the index's advisory lock; yields the open sidecar's file descriptor."""
    path = _cursor_path(store_root)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        os.close(fd)


def read_lines(store_root: Path | str) -> list[dict]:
    """Every line of the index, skipping any that does not parse. ``[]`` with no file."""
    path = turns_path(store_root)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    lines: list[dict] = []
    for raw in text.splitlines():
        try:
            line = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(line, dict):
            lines.append(line)
    return lines


def confirmed_paste_flags(store_root: Path | str, session_id: str) -> dict[str, bool]:
    """For the credit reader: this session's confirmed person turns and whether each pasted.

    Keyed by prompt id, and by the record's uuid for a mid-turn message. A line whose
    origin is unconfirmed or is not the person's says nothing about their words, so it
    is left out (:func:`kyosei.harness.claude_code_transcript.apply_paste_index`).
    """
    flags: dict[str, bool] = {}
    for line in read_lines(store_root):
        if line.get("session") != session_id or line.get("origin") != HUMAN:
            continue
        pasted = line.get("pasted")
        if not isinstance(pasted, bool):
            continue
        for key in ("prompt_id", "turn"):
            value = line.get(key)
            if isinstance(value, str):
                flags[value] = pasted
    return flags


#: KYO-662: the ``role`` of a line that stands for the agent's reply, not for a prompt. Such
#: a line repeats its prompt's id, and a skip is never written on it.
REPLY_ROLE = "assistant"


def prompt_lines(store_root: Path | str, session_id: str) -> list[dict]:
    """One session's lines that each stand for a prompt or a message sent mid-turn, in file
    order: every line of the session but those whose ``role`` is :data:`REPLY_ROLE`."""
    return [
        line
        for line in read_lines(store_root)
        if line.get("session") == session_id and line.get("role") != REPLY_ROLE
    ]


def mark_skipped(store_root: Path | str, session_id: str, key: str, why: str) -> dict | None:
    """KYO-662: write ``skip`` on one index line, the reason a turn the Stop check asked
    about is not a discussion. ``key`` is the line's prompt id or record uuid. Returns the
    line as written, or None when the session's index holds no such line. The reason and
    the time it was given are the only words the index holds, and they are the agent's.
    """
    with _locked(store_root):
        lines = read_lines(store_root)
        for line in lines:
            if (
                line.get("session") == session_id
                and line.get("role") != REPLY_ROLE
                and key in (line.get("prompt_id"), line.get("turn"))
            ):
                line["skip"] = {"why": why, "at": _now()}
                _rewrite(store_root, lines)
                return line
    return None


def _rewrite(store_root: Path | str, lines: list[dict]) -> None:
    path = turns_path(store_root)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        "".join(json.dumps(line, sort_keys=True) + "\n" for line in lines), encoding="utf-8"
    )
    os.replace(temporary, path)


def index_prompt(store_root: Path | str, payload: dict) -> None:
    """UserPromptSubmit: one line for this prompt.

    Nothing is written for a payload with no prompt id, or for a prompt id this session's
    index already holds, which is what a message sent mid-turn repeats. The check and the
    append hold one lock, so two payloads with one id cannot both find it absent.
    """
    session = payload.get("session_id")
    prompt_id = payload.get("prompt_id")
    if not isinstance(session, str) or not isinstance(prompt_id, str) or not prompt_id:
        return
    prompt = payload.get("prompt")
    line = {
        "session": session,
        "prompt_id": prompt_id,
        "at": _now(),
        "pasted": _PASTED_ELEMENT_OPEN in prompt if isinstance(prompt, str) else None,
        "origin": UNCONFIRMED,
        "source": SOURCE_HOOK,
    }
    with _locked(store_root):
        if any(
            held.get("session") == session and held.get("prompt_id") == prompt_id
            for held in read_lines(store_root)
        ):
            return
        with turns_path(store_root).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, sort_keys=True) + "\n")


def _read_cursor(fd: int) -> dict[str, int]:
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        data = json.loads(os.read(fd, 16 * 1024 * 1024) or b"{}")
    except (json.JSONDecodeError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, int)} if isinstance(data, dict) else {}


def _write_cursor(fd: int, cursors: dict[str, int]) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, json.dumps(cursors, sort_keys=True).encode("utf-8"))


def confirm_from_transcript(store_root: Path | str, session_id: str, path: Path) -> None:
    """Stop: read the transcript past the session's cursor and bring the index up to date.

    A line present gets the transcript's origin and paste answer; a prompt with no line
    gets one. The file is rewritten only when something changed. Raises :class:`OSError`.
    """
    with _locked(store_root) as fd:
        cursors = _read_cursor(fd)
        offset = cursors.get(session_id, 0)
        size = path.stat().st_size
        if offset > size:
            offset = 0
        facts, reached = transcript.read_turn_facts(path, offset)
        if facts:
            lines = read_lines(store_root)
            if _fold(lines, session_id, facts):
                _rewrite(store_root, lines)
        if reached != cursors.get(session_id):
            cursors[session_id] = reached
            _write_cursor(fd, cursors)


def _fold(lines: list[dict], session_id: str, facts: list[transcript.TurnFact]) -> bool:
    """Fold ``facts`` into ``lines`` in place; whether anything changed."""
    by_prompt = {
        line["prompt_id"]: line
        for line in lines
        if line.get("session") == session_id and isinstance(line.get("prompt_id"), str)
    }
    known_turns = {
        line["turn"]
        for line in lines
        if line.get("session") == session_id and isinstance(line.get("turn"), str)
    }
    changed = False
    for fact in facts:
        line = by_prompt.get(fact.prompt_id) if fact.prompt_id is not None else None
        if line is not None:
            updated = {
                "origin": fact.origin or "unknown",
                "pasted": fact.pasted if fact.pasted is not None else line.get("pasted"),
                "turn": fact.turn,
            }
            if any(line.get(key) != value for key, value in updated.items()):
                line.update(updated)
                changed = True
            continue
        if fact.turn in known_turns or fact.origin is None:
            continue
        lines.append(
            {
                "session": session_id,
                "prompt_id": fact.prompt_id,
                "turn": fact.turn,
                "at": fact.at,
                "pasted": fact.pasted,
                "origin": fact.origin,
                "source": SOURCE_TRANSCRIPT,
            }
        )
        known_turns.add(fact.turn)
        changed = True
    return changed


def _config_of(ctx: Context) -> Config | None:
    """This project's configuration, or None when there is none to read."""
    if ctx.layout is None or ctx.layout.kind == LAYOUT_UNCONFIGURED:
        return None
    try:
        return load_config(
            Path(ctx.root),
            code_root=ctx.layout.code_root,
            store_via_main_checkout=ctx.layout.store_via_main_checkout,
        )
    except KyoseiError:
        return None


def _payload_of(raw: str) -> dict:
    try:
        payload = json.loads(raw) if raw and raw.strip() else {}
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _run_hook(ctx: Context, config: Config, payload: dict) -> dict:
    """Apply one hook event. Returns what was done, for ``--json``; prints nothing."""
    event = payload.get("hook_event_name")
    session = payload.get("session_id")
    if not isinstance(session, str) or not session:
        return {"ok": False, "problem": "no session_id in the hook payload"}
    through_main = ctx.layout.store_via_main_checkout if ctx.layout is not None else None
    if event == "UserPromptSubmit":
        if through_main is None:
            index_prompt(ctx.root, payload)
        return {"ok": True, "event": event}
    if event not in ("Stop", "SessionEnd"):
        return {"ok": False, "problem": f"unhandled hook_event_name {event!r}"}
    raw_path = payload.get("transcript_path")
    if not isinstance(raw_path, str) or not raw_path:
        return {"ok": False, "problem": "no transcript_path in the hook payload"}
    path = Path(raw_path).expanduser()
    data: dict = {"ok": True, "event": event}
    if through_main is None and path.is_file():
        confirm_from_transcript(ctx.root, session, path)
    archive = transcript_archive.archive_dir(config, ctx.environ)
    problem = transcript_archive.archive_location_problem(archive)
    if problem is not None:
        data.update(ok=False, problem=problem)
        return data
    result = transcript_archive.archive_session(path, archive)
    data.update(archived=result.written, bytes_copied=result.bytes_copied)
    if result.problems:
        data.update(ok=False, problem="; ".join(result.problems))
    return data


def cmd_turns(ctx: Context, args: Namespace) -> Output:
    """``kyo turns`` lists the index; ``kyo turns --hook`` is the hook that keeps it."""
    if getattr(args, "hook", False):
        config = _config_of(ctx)
        if config is None or not transcripts_opted_in(config):
            return Output(lines=[], data={"ok": True, "opted_in": False})
        payload = _payload_of(sys.stdin.read() if not sys.stdin.isatty() else "")
        try:
            data = _run_hook(ctx, config, payload)
        except OSError as exc:
            data = {"ok": False, "problem": str(exc.strerror or exc)}
        return Output(lines=[], data=data)

    config = _config_of(ctx)
    opted_in = config is not None and transcripts_opted_in(config)
    lines = read_lines(ctx.root) if opted_in else []
    wanted = getattr(args, "session", None)
    if wanted:
        lines = [line for line in lines if line.get("session") == wanted]
    origins: dict[str, int] = {}
    for line in lines:
        key = str(line.get("origin"))
        origins[key] = origins.get(key, 0) + 1
    rendered: list[str]
    if config is None:
        rendered = ["turns: no Kyosei project here"]
    elif not opted_in:
        rendered = ["turns: this project has not opted in to reading transcripts (D14)"]
    elif not lines:
        rendered = [f"turns: no lines in {turns_path(ctx.root)}"]
    else:
        rendered = [f"turns: {len(lines)} line(s) in {turns_path(ctx.root)}"]
        rendered += [f"  {count:>6}  {origin}" for origin, count in sorted(origins.items())]
    return Output(
        lines=rendered,
        data={"ok": True, "opted_in": opted_in, "origins": origins, "turns": lines},
    )
