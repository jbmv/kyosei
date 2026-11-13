"""``kyo agenda``: a session's task list, kept for the person and the orchestrator (KYO-770).

A session has one ordered list of items. The person writes theirs from a device, through
``kyo serve`` (:mod:`kyosei.serve_agenda`); the orchestrator writes its own with this command,
from the session's shell. Each item carries its text, who wrote it, whether it is checked, and
when it was added and last changed.

WHO MAY CHANGE WHAT. The orchestrator may edit, check, uncheck, remove and reorder any item.
A device may add an item as the person and change or remove one the person wrote; it may not
edit, check or remove an item of the orchestrator's (:func:`merge_from_device`). An item's
author is the side that added it and never changes, and a device cannot state it.

WHERE. ``$KYOSEI_HOME/composer/agendas/<session id>.json``, beside the session's draft, private
to this account and written through :mod:`kyosei.serve_files`, which refuses a home inside a
git repository. It is session state and not the store's: no record, event or export names it,
and a dispatched worker's branch cannot carry it.

TWO PROCESSES WRITE IT: the server, for a device, and this command, for the orchestrator. Every
write is made by :func:`write`, which holds an advisory lock on the directory while it reads
the stored copy, judges the change against it and replaces it, so neither writer can overwrite
what the other saved in between. A device's write also states the etag it read, as a draft's
does; this command reads the current copy under the lock and needs none.

WHAT OTHER COMMANDS DO WITH IT, each only for the session named by ``$CLAUDE_CODE_SESSION_ID``
and only when that session already has a list, and none of them able to fail its command:

- ``kyo dispatch`` adds an item for the dispatch it creates; ``kyo accept`` (when everything it
  applies lands) and ``kyo dispatch close`` check it.
- ``kyo accept`` adds one item for closing the wave, unless ``--close`` finished every record
  it covers; ``kyo close-wave`` checks it when it has closed its records.
- ``kyo check-build --hook``, the prompt hook, prints the unchecked items with who wrote each
  (:func:`prompt_lines`) at the session's first prompt and afterwards only when they differ
  from what the session was last shown (KYO-875).
- ``kyo sessions --hook`` at ``Stop`` announces the person's unchecked items to the person when
  they differ from what the session's last stop announced (KYO-875), and
  says when dispatches are in flight and the list holds nothing of the orchestrator's, once
  per session and again only when the count has risen (:func:`stop_message`, KYO-775). It is
  printed as a hook's ``systemMessage``, which is shown and does not continue the
  conversation: at ``Stop`` anything that reaches the agent makes it take another turn,
  which is what an announcement must not do.
- ``kyo brief --session-start`` prints what the project's previous session left unchecked and
  carries the person's items into the new session's list, once (:func:`carry_lines`).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import sqlite3
from argparse import Namespace
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from . import clock
from .commands import Context, Output
from .config import COMPOSER_DIRNAME, store_home
from .errors import KyoseiError, UsageError

#: What follows ``$KYOSEI_HOME/composer/``: one file per session, named by its id.
AGENDAS = "agendas"

PERSON = "person"
ORCHESTRATOR = "orchestrator"

#: The environment variable Claude Code sets to the running session's id.
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"

ACTIONS = ("list", "add", "edit", "check", "uncheck", "remove", "move")

#: Bounds on what one list may hold, so a mistaken write cannot grow the hook's output
#: without limit: the prompt hook prints every unchecked item on every turn.
MAX_ITEMS = 100
MAX_TEXT = 500

#: What an item written by a command is for (``for`` on the item), so the command that
#: finishes that work can find the item it checks.
FOR_DISPATCH = "dispatch:"
FOR_WAVE = "wave-close"

_SESSION_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class Forbidden(Exception):
    """A change a device may not make to an item of the orchestrator's."""


class BadItems(Exception):
    """A list a device sent that is not a list of items."""


# ------------------------------------------------------------------------ reading


def path_of(environ: dict[str, str], session_id: str) -> Path | None:
    """The file holding ``session_id``'s list; None for an id that cannot name a file."""
    if not _SESSION_FILE.match(session_id or ""):
        return None
    return store_home(environ) / COMPOSER_DIRNAME / AGENDAS / f"{session_id}.json"


def parse(raw: bytes | None, where: object = "the list") -> dict | None:
    """The stored document; None when there is none. A file that is not a list is refused."""
    if raw is None:
        return None
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KyoseiError(
            f"the session list file {where} cannot be read ({exc}); remove the file and add "
            "its items again"
        ) from exc
    if not isinstance(document, dict) or not isinstance(document.get("items"), list):
        raise KyoseiError(
            f"the session list file {where} holds no list of items; remove the file and add "
            "its items again"
        )
    document["items"] = [item for item in document["items"] if _is_item(item)]
    return document


def _is_item(item: object) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("text"), str)
        and item.get("author") in (PERSON, ORCHESTRATOR)
    )


def read_raw(environ: dict[str, str], session_id: str) -> bytes | None:
    """The stored bytes, or None when the session has no list or the file cannot be read."""
    path = path_of(environ, session_id)
    if path is None:
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def read(environ: dict[str, str], session_id: str) -> dict | None:
    """The session's list, or None when it has none."""
    return parse(read_raw(environ, session_id), path_of(environ, session_id))


def unchecked(document: dict | None, author: str | None = None) -> list[dict]:
    """The items not yet checked, in order; ``author`` keeps one side's."""
    items = document.get("items", []) if document else []
    return [
        item
        for item in items
        if not item.get("checked") and (author is None or item.get("author") == author)
    ]


def public_item(item: dict) -> dict:
    """An item as a route and ``--json`` state it."""
    return {
        "id": item["id"],
        "text": item["text"],
        "author": item["author"],
        "checked": bool(item.get("checked")),
        "added_at": item.get("added_at"),
        "changed_at": item.get("changed_at"),
        "for": item.get("for"),
    }


# ------------------------------------------------------------------------ writing


@contextlib.contextmanager
def _locked(directory: Path) -> Iterator[None]:
    """Hold the directory's advisory lock: one writer at a time, across processes."""
    fd = os.open(directory / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _empty() -> dict:
    return {"items": [], "next_id": 1}


def new_item(document: dict, text: str, author: str, at: str, purpose: str | None = None) -> dict:
    """A new item with the list's next id; the caller places it."""
    number = int(document.get("next_id") or 1)
    while any(item["id"] == str(number) for item in document["items"]):
        number += 1
    document["next_id"] = number + 1
    item = {
        "id": str(number),
        "text": text,
        "author": author,
        "checked": False,
        "added_at": at,
        "changed_at": at,
    }
    if purpose is not None:
        item["for"] = purpose
    return item


def write(
    environ: dict[str, str],
    session_id: str,
    change: Callable[[dict, str], bool],
    *,
    headers: tuple[str | None, str | None] | None = None,
    create: bool = True,
) -> tuple[int, dict | None, str | None]:
    """Change the session's list under the lock; the status, the document and its etag.

    ``change(document, at)`` edits the stored document in place (an empty one when there is
    none) and says whether anything changed; nothing is written when it did not, and the
    status is then 0. ``headers`` is a device's ``(If-Match, If-None-Match)``, checked
    against the stored copy before ``change`` is asked, so a stale device is told it is
    stale and not that its change is forbidden. With ``create`` false a session that has no
    list is left without one.

    Refusals are :class:`kyosei.serve.Refusal` (a precondition, or a home that cannot hold
    the file), :class:`KyoseiError`, and whatever ``change`` raises.
    """
    from . import serve_files

    shown = f"the list of session {session_id}"
    if not create and read_raw(environ, session_id) is None:
        return 0, None, None
    target = serve_files.composer_target(environ, AGENDAS, session_id)
    with _locked(target.parent):
        if headers is not None:
            raw = serve_files.expected(
                target, shown, if_match=headers[0], if_none_match=headers[1]
            )
        else:
            raw = read_raw(environ, session_id)
        if raw is None and not create:
            return 0, None, None
        document = parse(raw, target) or _empty()
        at = clock.now()
        if not change(document, at) and raw is not None:
            return 0, document, serve_files.etag_of(raw)
        document["updated_at"] = at
        data = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        status, written = serve_files.put_file(
            target,
            shown,
            data,
            if_match=None if raw is None else serve_files.etag_of(raw),
            if_none_match="*" if raw is None else None,
            private=True,
        )
    return status, document, written["etag"]


# ------------------------------------------------------------- a device's whole list


def _device_items(sent: object) -> list[dict]:
    if not isinstance(sent, list):
        raise BadItems(
            'the body needs "items": a list of objects, each with a "text" string and '
            'optionally "id" and "checked"; an empty list removes every item of the person\'s'
        )
    if len(sent) > MAX_ITEMS:
        raise BadItems(f"a list holds at most {MAX_ITEMS} items, and this one has {len(sent)}")
    seen: set[str] = set()
    for index, item in enumerate(sent):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise BadItems(f'items[{index}] needs a "text" string')
        if not item["text"].strip() or len(item["text"]) > MAX_TEXT:
            raise BadItems(
                f"items[{index}].text must hold between 1 and {MAX_TEXT} characters"
            )
        if "checked" in item and not isinstance(item["checked"], bool):
            raise BadItems(f"items[{index}].checked must be true or false")
        if "id" in item and item["id"] is not None:
            if not isinstance(item["id"], str) or item["id"] in seen:
                raise BadItems(f"items[{index}].id must be a string that names one item once")
            seen.add(item["id"])
    return sent


def merge_from_device(document: dict, sent: object, at: str) -> bool:
    """Make the stored list the one a device sent, as far as a device may; True if it changed.

    The sent list is the whole list in the order the person sees it. An item with no ``id``
    is added as the person's. An item of the person's takes the sent text and check. An item
    of the orchestrator's must be sent as it is stored: a different text or check, or its
    absence, is :class:`Forbidden`.
    """
    items = _device_items(sent)
    stored = {item["id"]: item for item in document["items"]}
    sent_ids = {item["id"] for item in items if item.get("id") is not None}
    for item in document["items"]:
        if item["author"] == ORCHESTRATOR and item["id"] not in sent_ids:
            raise Forbidden(
                f"item {item['id']} is the orchestrator's, and a device may not remove it; "
                "GET the list and PUT it with that item as it was read"
            )
    merged: list[dict] = []
    changed = False
    for index, item in enumerate(items):
        text, checked = item["text"], bool(item.get("checked", False))
        held = stored.get(item["id"]) if item.get("id") is not None else None
        if item.get("id") is not None and held is None:
            raise BadItems(
                f"items[{index}].id names no item of this list; leave the id out to add an item"
            )
        if held is None:
            if checked:
                raise BadItems(f"items[{index}] is new, so it cannot arrive checked")
            merged.append(new_item(document, text, PERSON, at))
            changed = True
            continue
        if text != held["text"] or checked != bool(held.get("checked")):
            if held["author"] == ORCHESTRATOR:
                raise Forbidden(
                    f"item {held['id']} is the orchestrator's, and a device may not edit or "
                    "check it; GET the list and PUT it with that item as it was read"
                )
            held = {**held, "text": text, "checked": checked, "changed_at": at}
            changed = True
        merged.append(held)
    if [item["id"] for item in merged] != [item["id"] for item in document["items"]]:
        changed = True
    document["items"] = merged
    return changed


# ---------------------------------------------------------------- the orchestrator's verb


def _session_of(ctx: Context, args: Namespace) -> str:
    session_id = getattr(args, "session", None) or ctx.environ.get(SESSION_ENV) or ""
    if not session_id:
        raise UsageError(
            f"kyo agenda needs a session: ${SESSION_ENV} is not set, which Claude Code sets "
            "inside a session. Outside one, pass --session <id>; `kyo sessions` lists this "
            "project's sessions"
        )
    if path_of(ctx.environ, session_id) is None:
        raise UsageError(
            f"{session_id!r} cannot name a session's list: a session id holds only letters, "
            "digits, dots, hyphens and underscores; `kyo sessions` lists this project's "
            "sessions"
        )
    return session_id


def _text_of(args: Namespace, action: str) -> str:
    """The item's words, from ``--text`` or ``--text-file``, as one line."""
    from .prose import check_not_shell_mangled, read_prose, single_line

    given, source = getattr(args, "text", None), getattr(args, "text_file", None)
    if source:
        text = " ".join(read_prose(source, what="--text-file").split())
    elif given:
        # The list is not the store, so `--text` is not one of the store's prose flags, but
        # a shell eats a backticked word out of it just the same, and `--text-file` is the
        # way to succeed that the refusal names.
        check_not_shell_mangled(given, what="--text")
        text = " ".join(single_line(given, what="--text").split())
    else:
        text = ""
    if not text:
        raise UsageError(
            f"kyo agenda {action} needs the item's words: pass --text \"...\" (one line), "
            "or --text-file FILE ('-' for stdin)"
        )
    if len(text) > MAX_TEXT:
        raise UsageError(
            f"an item holds at most {MAX_TEXT} characters and this text has {len(text)}; "
            "shorten it, or split it into two items with `kyo agenda add --text`"
        )
    if given and source:
        raise UsageError("kyo agenda takes the item's words once: --text or --text-file")
    return text


def _found(document: dict, item_id: str, session_id: str) -> dict:
    for item in document["items"]:
        if item["id"] == item_id:
            return item
    raise UsageError(
        f"no item {item_id} on the list of session {session_id}; "
        f"`kyo agenda --session {session_id}` lists its items with their ids"
    )


def _place(document: dict, item: dict, position: int | None) -> None:
    """Put ``item`` at the 1-based ``position``; at the end when none is given."""
    items = [held for held in document["items"] if held is not item]
    if position is None:
        items.append(item)
    else:
        if position < 1:
            raise UsageError("--to counts from 1: `--to 1` is the top of the list")
        items.insert(position - 1, item)
    document["items"] = items


def _line(item: dict) -> str:
    box = "[x]" if item.get("checked") else "[ ]"
    return f"  {box} {item['id']:>3}  {item['author']:<12}  {item['text']}"


def cmd_agenda(ctx: Context, args: Namespace) -> Output:
    """``kyo agenda [add|edit|check|uncheck|remove|move] [ID ...]``: the session's list, as
    the orchestrator. With no action it prints the list. See the module for the rules."""
    from .serve import Refusal

    action = getattr(args, "action", None) or "list"
    ids = list(getattr(args, "ids", None) or [])
    session_id = _session_of(ctx, args)
    position = getattr(args, "to", None)

    if action == "list":
        worded = getattr(args, "text", None) or getattr(args, "text_file", None)
        if ids or worded or position is not None:
            raise UsageError(
                "kyo agenda with no action prints the list and takes nothing else; name "
                f"an action first ({', '.join(ACTIONS[1:])}), as in `kyo agenda add --text`"
            )
        document = read(ctx.environ, session_id)
        return _listing(session_id, document, [])
    if action == "add":
        if ids:
            raise UsageError(
                "kyo agenda add takes no item id (the list gives the new item one); pass "
                "the words with --text"
            )
    elif not ids:
        raise UsageError(
            f"kyo agenda {action} needs the id of an item; `kyo agenda` lists them"
        )
    elif action in ("edit", "move") and len(ids) != 1:
        raise UsageError(f"kyo agenda {action} takes one item id, and {len(ids)} were given")
    if action == "move" and position is None:
        raise UsageError(
            "kyo agenda move needs where to put the item: pass --to POSITION, counted "
            "from 1 at the top of the list"
        )
    text = _text_of(args, action) if action in ("add", "edit") else None
    said: list[str] = []

    def change(document: dict, at: str) -> bool:
        if action == "add":
            if len(document["items"]) >= MAX_ITEMS:
                raise UsageError(
                    f"the list holds {MAX_ITEMS} items, which is its limit; remove one "
                    "with `kyo agenda remove <id>` first"
                )
            item = new_item(document, text or "", ORCHESTRATOR, at)
            _place(document, item, position)
            said.append(f"added item {item['id']}")
            return True
        changed = False
        for item_id in ids:
            item = _found(document, item_id, session_id)
            if action == "edit":
                if item["text"] != text:
                    item["text"], item["changed_at"] = text, at
                    changed = True
                said.append(f"item {item_id} now reads: {text}")
            elif action in ("check", "uncheck"):
                want = action == "check"
                if bool(item.get("checked")) != want:
                    item["checked"], item["changed_at"] = want, at
                    changed = True
                said.append(f"item {item_id} {'checked' if want else 'unchecked'}")
            elif action == "remove":
                document["items"] = [held for held in document["items"] if held is not item]
                changed = True
                said.append(f"removed item {item_id}: {item['text']}")
            else:
                before = [held["id"] for held in document["items"]]
                _place(document, item, position)
                changed = before != [held["id"] for held in document["items"]]
                said.append(f"item {item_id} moved to position {position}")
        return changed

    try:
        _, document, _ = write(ctx.environ, session_id, change)
    except Refusal as refusal:
        raise KyoseiError(str(refusal)) from refusal
    return _listing(session_id, document, said)


def _listing(session_id: str, document: dict | None, said: list[str]) -> Output:
    items = document.get("items", []) if document else []
    open_count = len(unchecked(document))
    lines = list(said)
    if items:
        lines.append(f"session {session_id}: {len(items)} item(s), {open_count} unchecked")
        lines += [_line(item) for item in items]
    else:
        lines.append(
            f"session {session_id}: the list is empty; `kyo agenda add --text \"...\"` adds "
            "an item"
        )
    return Output(
        lines,
        {
            "ok": True,
            "command": "agenda",
            "session_id": session_id,
            "items": [public_item(item) for item in items],
            "unchecked": open_count,
            "updated_at": document.get("updated_at") if document else None,
        },
    )


# ------------------------------------------------- what other commands write to the list


def listed_session(environ: dict[str, str]) -> str | None:
    """The session this command runs in, when it has a list; None otherwise.

    One environment lookup outside a session, and one file read inside one.
    """
    session_id = environ.get(SESSION_ENV) or ""
    if not session_id or read_raw(environ, session_id) is None:
        return None
    return session_id


def _note(environ: dict[str, str], change: Callable[[dict, str], bool]) -> None:
    """Apply ``change`` to this session's list, if there is a session and it has a list.

    Called after another command has done its own work, so it never raises.
    """
    session_id = listed_session(environ)
    if session_id is None:
        return
    with contextlib.suppress(Exception):
        write(environ, session_id, change, create=False)


def _check_for(document: dict, purpose: str, at: str) -> bool:
    changed = False
    for item in document["items"]:
        if item.get("for") == purpose and not item.get("checked"):
            item["checked"], item["changed_at"] = True, at
            changed = True
    return changed


def note_dispatched(environ: dict[str, str], dispatch_id: str, title: str | None) -> None:
    """``kyo dispatch`` made ``dispatch_id``: one item for it, until it is accepted or closed."""
    purpose = FOR_DISPATCH + dispatch_id
    text = f"Accept {dispatch_id}" + (f": {title}" if title else "")

    def change(document: dict, at: str) -> bool:
        if len(document["items"]) >= MAX_ITEMS or any(
            item.get("for") == purpose for item in document["items"]
        ):
            return False
        document["items"].append(new_item(document, text[:MAX_TEXT], ORCHESTRATOR, at, purpose))
        return True

    _note(environ, change)


def note_dispatch_finished(environ: dict[str, str], dispatch_id: str) -> None:
    """``dispatch_id`` was accepted or closed: its item is checked."""
    _note(environ, lambda document, at: _check_for(document, FOR_DISPATCH + dispatch_id, at))


def note_wave_open(environ: dict[str, str], record_ids: list[str]) -> None:
    """``kyo accept`` left records to close: one unchecked item for closing the wave."""

    def change(document: dict, at: str) -> bool:
        for item in document["items"]:
            if item.get("for") == FOR_WAVE and not item.get("checked"):
                missing = [rid for rid in record_ids if rid not in item["text"].split()]
                if not missing or len(item["text"]) + len(" ".join(missing)) + 1 > MAX_TEXT:
                    return False
                item["text"], item["changed_at"] = f"{item['text']} {' '.join(missing)}", at
                return True
        if len(document["items"]) >= MAX_ITEMS:
            return False
        text = "Close the wave (kyo close-wave): " + " ".join(record_ids)
        document["items"].append(new_item(document, text[:MAX_TEXT], ORCHESTRATOR, at, FOR_WAVE))
        return True

    _note(environ, change)


def note_wave_closed(environ: dict[str, str]) -> None:
    """``kyo close-wave`` closed its records: the wave's item is checked."""
    _note(environ, lambda document, at: _check_for(document, FOR_WAVE, at))


def after_dispatch(ctx: Context, output: Output) -> None:
    """After ``kyo dispatch`` created a dispatch, or ``kyo dispatch close`` ended one."""
    with contextlib.suppress(Exception):
        if listed_session(ctx.environ) is None:
            return
        data = output.data or {}
        if data.get("command") == "dispatch close":
            note_dispatch_finished(ctx.environ, str(data["dispatch_id"]))
            return
        title = ctx.store().record(str(data["record_id"]))["title"]
        note_dispatched(ctx.environ, str(data["dispatch_id"]), title)


def after_accept(ctx: Context, args: Namespace, output: Output) -> None:
    """After ``kyo accept``: the dispatch's item is checked once everything it applies has
    landed, and the records still to be closed are named on the wave's item."""
    with contextlib.suppress(Exception):
        data = output.data or {}
        if listed_session(ctx.environ) is None or not data.get("complete"):
            return
        note_dispatch_finished(ctx.environ, str(data["dispatch_id"]))
        if getattr(args, "close", False):
            waiting = [str(row["record_id"]) for row in data.get("left_open") or []]
        else:
            waiting = [str(record_id) for record_id in data.get("covers") or []]
        if waiting:
            note_wave_open(ctx.environ, waiting)


def after_close_wave(ctx: Context, args: Namespace, output: Output) -> None:
    """After ``kyo close-wave`` closed the records it was given."""
    with contextlib.suppress(Exception):
        data = output.data or {}
        if data.get("complete") and not data.get("dry_run") and getattr(args, "record_ids", None):
            note_wave_closed(ctx.environ)


# ------------------------------------------------------------------- what the hooks say


def _quietly_read(environ: dict[str, str], session_id: str | None) -> dict | None:
    """The list for a hook: None for no session, no list, or one that cannot be read."""
    if not session_id:
        return None
    try:
        return read(environ, session_id)
    except (KyoseiError, OSError):
        return None


#: What follows ``$KYOSEI_HOME/composer/``: one file per session holding a digest of the
#: unchecked items each hook last had in view, ``prompt`` for the prompt hook and ``stop``
#: for the Stop line (KYO-875). Session state, like the list, and never the store's.
SHOWN = "agenda-shown"


def _digest(items: list[dict]) -> str:
    """A digest of the items' ids, authors and texts; a check or an edit changes it."""
    import hashlib

    rows = [[str(i.get("id")), str(i.get("author")), str(i.get("text"))] for i in items]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _shown(environ: dict[str, str], session_id: str, which: str) -> str | None:
    """The digest last remembered for ``which`` hook, or None for none or an unreadable file."""
    try:
        from . import serve_files

        raw = serve_files.composer_path(environ, SHOWN, session_id).read_bytes()
        value = json.loads(raw.decode("utf-8")).get(which)
        return value if isinstance(value, str) else None
    except Exception:  # noqa: BLE001 - a hook never fails over this file
        return None


def _remember_shown(environ: dict[str, str], session_id: str, which: str, digest: str) -> None:
    """Keep ``digest`` as what ``which`` hook has shown, leaving the other hook's alone."""
    with contextlib.suppress(Exception):
        from . import serve_files

        target = serve_files.composer_target(environ, SHOWN, session_id)
        try:
            current = json.loads(target.read_bytes().decode("utf-8"))
        except Exception:  # noqa: BLE001
            current = {}
        if not isinstance(current, dict):
            current = {}
        current[which] = digest
        data = (json.dumps(current) + "\n").encode("utf-8")
        with serve_files._WRITE_LOCK:
            serve_files._write_atomically(
                target, data, create=not target.exists(), private=True
            )


def _changed_since_shown(
    environ: dict[str, str], session_id: str | None, which: str, items: list[dict]
) -> bool:
    """Whether ``items`` differ from what ``which`` hook last showed this session.

    A session never shown anything counts every non-empty list as changed. The digest is
    remembered whether or not it prints, so a list that empties and later returns prints.
    """
    if not session_id:
        return bool(items)
    digest = _digest(items)
    changed = digest != (_shown(environ, session_id, which) or _digest([]))
    if changed:
        _remember_shown(environ, session_id, which, digest)
    return changed


def prompt_lines(environ: dict[str, str], session_id: str | None) -> list[str]:
    """What the prompt hook adds to the session's context: the unchecked items when they
    differ from what this session was last shown (the first prompt shows them), or nothing
    (KYO-875)."""
    waiting = unchecked(_quietly_read(environ, session_id))
    if not _changed_since_shown(environ, session_id, "prompt", waiting) or not waiting:
        return []
    return ["session list, unchecked (`kyo agenda`):"] + [
        f"  {item['id']} {item['author']}: {item['text']}" for item in waiting
    ]


def dispatches_in_flight(db_path: Path, session_id: str) -> int:
    """How many dispatches are running, or reported in this session and not yet accepted.

    Read without opening a store, and 0 for a database that cannot answer. A reported
    dispatch counts when it was created since this session's opening brief, is not closed,
    and no ``kyo verify --from-report`` (the first step of ``kyo accept``) names it; one
    from an earlier session is left to the brief.
    """
    if not db_path.is_file():
        return 0
    try:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return 0
    try:
        running = conn.execute(
            "SELECT COUNT(*) FROM dispatches WHERE state = 'running' AND closed_at IS NULL"
        ).fetchone()[0]
        opened = conn.execute(
            "SELECT MIN(at) FROM brief_runs WHERE session_start = 1 AND session_id = ?",
            (session_id,),
        ).fetchone()[0]
        reported = 0
        if opened:
            reported = conn.execute(
                "SELECT COUNT(*) FROM dispatches d WHERE d.state = 'reported'"
                " AND d.closed_at IS NULL AND d.created_at >= ? AND NOT EXISTS ("
                " SELECT 1 FROM events e WHERE e.command = 'verify --from-report ' || d.id)",
                (opened,),
            ).fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        conn.close()
    return int(running) + int(reported)


#: What follows ``$KYOSEI_HOME/composer/``: one file per session holding the count of
#: dispatches in flight when the stop hook last had it in view (KYO-775). Session state, like
#: the list, and never the store's.
SAID = "agenda-said"


def _said_count(environ: dict[str, str], session_id: str) -> int:
    """The in-flight count last remembered for the session; 0 for none or an unreadable file."""
    try:
        from . import serve_files

        raw = serve_files.composer_path(environ, SAID, session_id).read_bytes()
        return max(0, int(json.loads(raw.decode("utf-8"))["in_flight"]))
    except Exception:  # noqa: BLE001 - a hook never fails over this file
        return 0


def _remember_said(environ: dict[str, str], session_id: str, flying: int) -> None:
    """Keep ``flying`` as the count the sentence has been said for, or has fallen to."""
    with contextlib.suppress(Exception):
        from . import serve_files

        target = serve_files.composer_target(environ, SAID, session_id)
        data = (json.dumps({"in_flight": flying}) + "\n").encode("utf-8")
        with serve_files._WRITE_LOCK:
            serve_files._write_atomically(
                target, data, create=not target.exists(), private=True
            )


def stop_message(environ: dict[str, str], session_id: str | None, db_path: Path) -> str | None:
    """What the stop hook announces, or None when there is nothing to say.

    One sentence for the person's unchecked items, and one when dispatches are in flight
    and the list holds no item of the orchestrator's. Neither blocks the stop. The first is said
    when the person's unchecked items differ from what the last stop of the session said
    (KYO-875); the second once per session for a given count,
    and again only when the count has risen since it was last said or fell (KYO-775).
    """
    if not session_id:
        return None
    document = _quietly_read(environ, session_id)
    parts: list[str] = []
    mine = unchecked(document, PERSON)
    if _changed_since_shown(environ, session_id, "stop", mine) and mine:
        parts.append(
            f"Unchecked on the session list, yours ({len(mine)}): "
            + "; ".join(item["text"] for item in mine)
        )
    items = document.get("items", []) if document else []
    if not any(item["author"] == ORCHESTRATOR for item in items):
        flying = dispatches_in_flight(db_path, session_id)
        said = _said_count(environ, session_id)
        if flying > said:
            parts.append(
                f"{flying} dispatch(es) are running or unaccepted and the session list holds "
                "no item of the orchestrator's (kyo agenda add --text \"...\")"
            )
        if flying != said:
            _remember_said(environ, session_id, flying)
    return ". ".join(parts) if parts else None


def stop_hook_output(message: str) -> str:
    """The line a ``Stop`` hook prints for Claude Code to show ``message`` and stop as usual."""
    return json.dumps({"systemMessage": message}, ensure_ascii=False)


def carry_lines(
    environ: dict[str, str], session_id: str | None, previous_id: str | None
) -> list[str]:
    """What a session's first brief says of the previous session's list, carrying once.

    The previous session's unchecked items are printed. The person's are added to the new
    session's list; the orchestrator's are only shown, since a new orchestrator plans its
    own turns. The previous list is then marked as carried, so a second brief prints nothing.
    The new list is written before the old one is marked: a failure between the two repeats
    the lines and adds nothing twice, because an item already carried is not added again.
    """
    if not session_id or not previous_id or previous_id == session_id:
        return []
    try:
        previous = read(environ, previous_id)
        if previous is None or previous.get("carried_to"):
            return []
        left = unchecked(previous)
        if not left:
            return []
        theirs = [item for item in left if item["author"] == PERSON]

        def carry(document: dict, at: str) -> bool:
            held = {item["text"] for item in document["items"] if item["author"] == PERSON}
            changed = False
            for item in theirs:
                if item["text"] in held or len(document["items"]) >= MAX_ITEMS:
                    continue
                added = new_item(document, item["text"], PERSON, at)
                added["added_at"] = item.get("added_at") or at
                document["items"].append(added)
                changed = True
            return changed

        def mark(document: dict, at: str) -> bool:
            document["carried_to"] = session_id
            return True

        if theirs:
            write(environ, session_id, carry)
        write(environ, previous_id, mark, create=False)
    except Exception:  # noqa: BLE001 - a brief is never refused over a list
        return []
    lines = [f"the previous session ({previous_id[:8]}) left {len(left)} unchecked on its list:"]
    for item in left:
        carried = " (carried into this session's list)" if item["author"] == PERSON else ""
        lines.append(f"  {item['author']}: {item['text']}{carried}")
    lines.append("  `kyo agenda` prints this session's list")
    return lines


def latest_session(db_path: Path) -> str | None:
    """The project's most recent session, by its opening brief; None when it has had none.

    Its list is the project's list between sessions (KYO-901): the next session's first
    brief carries the person's unchecked items from exactly this one (:func:`carry_lines`
    reads :func:`previous_session`, the same row), so an item a device adds while no session
    runs is carried. Read without opening a store, as :func:`dispatches_in_flight` is.
    """
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return None
    try:
        return previous_session(conn, "")
    finally:
        conn.close()


def directory_stamp(environ: dict[str, str]) -> str:
    """A stamp of the directory holding every list, which differs after a save of any.

    What a project's event stream compares (KYO-901). A list is written by rename, which
    changes its directory's modification time. A save of another project's list changes it
    too, and costs a device one read of a list that has not changed.
    """
    try:
        info = os.stat(store_home(environ) / COMPOSER_DIRNAME / AGENDAS)
    except OSError:
        return "gone"
    return str(info.st_mtime_ns)


def previous_session(conn: Any, session_id: str) -> str | None:
    """The session whose opening brief most recently preceded ``session_id``'s, if any."""
    try:
        row = conn.execute(
            "SELECT session_id FROM brief_runs WHERE session_start = 1 AND session_id IS NOT"
            " NULL AND session_id != ? ORDER BY seq DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    except sqlite3.Error:
        return None
    return None if row is None else str(row[0])
