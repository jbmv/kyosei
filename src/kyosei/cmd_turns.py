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

THE SEAL (KYO-663, decision KYO-654 DC6). Every line written from schema 33 on is chained:
it carries ``prev``, the hash of the line before it in its session, so the session's lines
cannot be edited, reordered or added to without a later hash disagreeing
(:mod:`kyosei.seal`). A prompt's line also commits its text when it is sent, as a salted
per-line Merkle root (``root``) and a line count (``lines``), and the Stop hook commits the
agent's final reply the same way on a line of its own, ``role: assistant``, from the
payload's ``last_assistant_message``. Still no words are stored. A line the Stop hook adds
from the transcript was not committed when it was sent, so it is chained with no root;
that is every message sent mid-turn. Lines written before this are left as they are,
unchained, and a session that spans the change has a chain that begins at its first new
line. ``kyo turns --verify`` checks every chain and every credit's ``span_seal``;
``kyo turns --seal-key`` shows or chooses where the key is kept.

The archive half of the Stop hook is :mod:`kyosei.transcript_archive`.
"""

from __future__ import annotations

import json
import os
import sys
from argparse import Namespace
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from . import seal, transcript_archive
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
from .errors import KyoseiError, UsageError
from .harness import claude_code_transcript as transcript

try:
    import fcntl
except ImportError:  # pragma: no cover - not available on Windows
    fcntl = None  # type: ignore[assignment]

#: A line's ``origin`` until the transcript has been read.
UNCONFIRMED = "unconfirmed"
#: The ``origin`` of a person's turn, the transcript's own word.
HUMAN = "human"
#: The ``origin`` of the line that commits the agent's final reply.
ASSISTANT = "assistant"
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


def _rewrite(store_root: Path | str, lines: list[dict]) -> None:
    path = turns_path(store_root)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        "".join(json.dumps(line, sort_keys=True) + "\n" for line in lines), encoding="utf-8"
    )
    os.replace(temporary, path)


def index_prompt(
    store_root: Path | str, payload: dict, environ: Mapping[str, str] | None = None
) -> None:
    """UserPromptSubmit: one line for this prompt.

    Nothing is written for a payload with no prompt id, or for a prompt id this session's
    index already holds, which is what a message sent mid-turn repeats. The check and the
    append hold one lock, so two payloads with one id cannot both find it absent.

    The line is chained to the session's last one and commits the prompt's text
    (:func:`kyosei.seal.seal_fields`); the text itself is not written.
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
    key = _key(environ)
    with _locked(store_root):
        held_lines = read_lines(store_root)
        if any(
            held.get("session") == session
            and held.get("prompt_id") == prompt_id
            and held.get("role") != seal.ROLE_ASSISTANT
            for held in held_lines
        ):
            return
        line.update(
            seal.seal_fields(
                held_lines,
                session,
                prompt_id,
                seal.ROLE_USER,
                prompt if isinstance(prompt, str) else None,
                key,
            )
        )
        _append(store_root, line)


def _key(environ: Mapping[str, str] | None) -> bytes | None:
    """The seal key, or None when its store cannot be used; the line is then chained and
    commits nothing, because the hook must not fail the prompt it runs on."""
    try:
        return seal.load_key(environ)
    except (seal.SealKeyError, OSError, ValueError, AttributeError):
        return None


def _append(store_root: Path | str, line: dict) -> None:
    with turns_path(store_root).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, sort_keys=True) + "\n")


def commit_reply(
    store_root: Path | str, payload: dict, environ: Mapping[str, str] | None = None
) -> None:
    """Stop: one line committing the agent's final reply, from ``last_assistant_message``.

    The line takes the prompt id the payload names, or else that of the session's latest
    prompt line. Nothing is written for a payload with no reply, or for a reply the
    session's last line already commits, which is what a second Stop with nothing said in
    between repeats.
    """
    session = payload.get("session_id")
    reply = payload.get("last_assistant_message")
    if not isinstance(session, str) or not isinstance(reply, str) or not reply:
        return
    key = _key(environ)
    with _locked(store_root):
        lines = read_lines(store_root)
        mine = [line for line in lines if line.get("session") == session]
        prompt_id = payload.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            prompt_id = next(
                (
                    line["prompt_id"]
                    for line in reversed(mine)
                    if line.get("role") != seal.ROLE_ASSISTANT
                    and isinstance(line.get("prompt_id"), str)
                ),
                None,
            )
        last = mine[-1] if mine else None
        if (
            key is not None
            and last is not None
            and last.get("role") == seal.ROLE_ASSISTANT
            and seal.verify_message(key, last, reply)
        ):
            return
        line = {
            "session": session,
            "prompt_id": prompt_id,
            "at": _now(),
            "pasted": None,
            "origin": ASSISTANT,
            "source": SOURCE_HOOK,
        }
        line.update(seal.seal_fields(lines, session, prompt_id, seal.ROLE_ASSISTANT, reply, key))
        _append(store_root, line)


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
        if line.get("session") == session_id
        and isinstance(line.get("prompt_id"), str)
        and line.get("role") != seal.ROLE_ASSISTANT
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
                # Chained, with no root: its text was not committed when it was sent.
                **seal.seal_fields(lines, session_id, fact.prompt_id, seal.ROLE_USER, None, None),
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
            index_prompt(ctx.root, payload, ctx.environ)
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
    if through_main is None and event == "Stop":
        # After the fold, so that a message sent mid-turn precedes the reply to it.
        commit_reply(ctx.root, payload, ctx.environ)
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

    if getattr(args, "seal_key", None) is not None:
        return _seal_key(ctx, args)
    if getattr(args, "key_leaves_this_machine", False):
        raise UsageError("--key-leaves-this-machine goes with --seal-key icloud-keychain")
    config = _config_of(ctx)
    opted_in = config is not None and transcripts_opted_in(config)
    lines = read_lines(ctx.root) if opted_in else []
    if getattr(args, "verify", False):
        return _verify(ctx, lines, opted_in, getattr(args, "session", None))
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


def session_lines(store_root: Path | str, session: str) -> list[dict]:
    """One session's lines in file order, which is the order its chain links them in."""
    return [line for line in read_lines(store_root) if line.get("session") == session]


def _seal_key(ctx: Context, args: Namespace) -> Output:
    """``kyo turns --seal-key [STORE]``: show where the seal key is kept, or choose."""
    target = args.seal_key
    acknowledged = getattr(args, "key_leaves_this_machine", False)
    if target == "show":
        if acknowledged:
            raise UsageError("--key-leaves-this-machine goes with --seal-key icloud-keychain")
        recorded = seal.recorded_choice(ctx.environ)
        store = seal.chosen_store(ctx.environ)
        local = seal.local_store(ctx.environ)
        if store == seal.STORE_ICLOUD:
            head = (
                f"seal key: kept in iCloud Keychain, with a copy in the {local} store on "
                "this machine, which the hooks read"
            )
        else:
            how = "chosen" if recorded else "nothing chosen, so the local file is used"
            head = f"seal key: kept in the {store} store ({how})"
        return Output(
            lines=[
                head,
                f"  file             {seal.key_path(ctx.environ)}, mode 0600",
                "  keychain         a generic password in the macOS login keychain",
                "  icloud-keychain  a synchronizable keychain item, reached through the "
                "signed Mac app. " + seal.ICLOUD_DISCLOSURE,
                "choose with `kyo turns --seal-key <store>`; the key moves with the choice",
            ],
            data={
                "ok": True,
                "key_store": store,
                "local_store": local,
                "chosen": recorded is not None,
            },
        )
    if target == seal.STORE_ICLOUD and not acknowledged:
        raise UsageError(
            f"{seal.ICLOUD_DISCLOSURE} Nothing was changed. If that is what the person "
            "wants, run `kyo turns --seal-key icloud-keychain --key-leaves-this-machine`"
        )
    if target != seal.STORE_ICLOUD and acknowledged:
        raise UsageError("--key-leaves-this-machine goes with --seal-key icloud-keychain")
    try:
        done = seal.move_key(target, ctx.environ)
    except seal.SealKeyError as exc:
        raise KyoseiError(f"the seal key stays where it was: {exc}") from exc
    local = seal.local_store(ctx.environ)
    if target == seal.STORE_ICLOUD:
        lines = [f"seal key: now kept in iCloud Keychain, with a copy in the {local} store; {done}"]
        lines.append(seal.ICLOUD_DISCLOSURE)
    else:
        lines = [f"seal key: now kept in the {target} store; {done}"]
    return Output(
        lines=lines,
        data={"ok": True, "key_store": target, "local_store": local, "chosen": True},
    )


def _verify(ctx: Context, lines: list[dict], opted_in: bool, wanted: str | None) -> Output:
    """``kyo turns --verify``: every session's chain, and every credit's ``span_seal``."""
    by_session: dict[str, list[dict]] = {}
    for line in lines:
        session = line.get("session")
        if isinstance(session, str) and (not wanted or session == wanted):
            by_session.setdefault(session, []).append(line)
    failures: list[str] = []
    chained = 0
    for session, mine in by_session.items():
        chained += sum(1 for line in mine if seal.is_sealed(line))
        failures += [f"session {session}: {problem}" for problem in seal.verify_chain(mine)]
    credits_checked = 0
    if opted_in:
        for credit in ctx.store().sealed_credits():
            session = credit["span_session"]
            if wanted and session != wanted:
                continue
            credits_checked += 1
            if credit["span_seal"] not in seal.recomputed_hashes(by_session.get(session, [])):
                failures.append(
                    f"credit {credit['id']}: session {session}, recomputed from its first "
                    "chained line, does not reach the hash its span_seal holds, so a line "
                    "at or before the span's last was changed or removed"
                )
    data = {
        "ok": not failures,
        "opted_in": opted_in,
        "sessions": len(by_session),
        "chained_lines": chained,
        "credits_checked": credits_checked,
        "failures": failures,
    }
    if failures:
        detail = "\n".join(f"  - {failure}" for failure in failures)
        raise KyoseiError(f"turns: {len(failures)} line(s) or seal(s) do not verify:\n{detail}")
    return Output(
        lines=[
            f"turns: {chained} chained line(s) in {len(by_session)} session(s) verify, "
            f"and {credits_checked} credit seal(s) match"
        ],
        data=data,
    )
