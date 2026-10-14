"""``kyo sessions`` — which Claude Code sessions this project has, and where each
one is on claude.ai.

KYO-186 stage 1. The desktop app wants to open a claude.ai pane on *this
project's* Remote Control session, and nothing in the system knew which one that
was. This command is the half that knows: it runs from a session hook, in the
session itself, and writes what it observes into the store's local state.

WHY THE STORE AND NOT A SCAN. KYO-186 first proposed that the app scan
``~/.claude/projects/<path-slug>/`` for ``bridge-session`` records. That works and
was measured, but it is the wrong shape for three reasons and the third decides
it: it would put knowledge of the harness's private file layout in a *second*
consumer; it cannot tell a running session from an ended one, because nothing
holds a transcript open and a running ``claude`` does not name its session on its
command line; and it cannot cross hosts. The motivating case is a dedicated dev
box — project remote-mounted, no IDE on the client machine, the work driven through web
Remote Control — where a scan of ``~/.claude`` looks at the wrong machine. A file
that travels with the store is written by the box and read wherever the store is
readable, which is the arrangement the feature is for.

THE COUPLING, STATED OUT LOUD. This is the second place in this codebase that
reaches into the harness's private files; :mod:`kyosei.stop_target` is the first,
and it pairs its reach with a rule that is not optional and is followed here
verbatim: **every step degrades to a NAMED outcome rather than a guess**, so a
layout change turns the mechanism off loudly instead of turning it into a
confident answer about the wrong session. See :data:`BRIDGE_OUTCOMES`. What is
matched here is not prose: ``bridge-session`` is a structured record with a
``type`` discriminator, and the only field taken off it is the one the URL needs.

WHAT IS DELIBERATELY NOT COPIED. A ``bridge-session`` record carries
``ownerAccountUuid`` and ``ownerOrganizationUuid``. Neither is read, neither is
stored, and neither has a consumer — which is a stronger guarantee than KYO-186.c5
asks for. c5 says no account or organization identifier may reach
``kyosei/export/``; a field that never enters this process cannot reach anywhere.

WHERE THE FILE GOES, AND WHY BESIDE THE DATABASE. ``<store_root>/kyosei/``, beside
``kyosei.db`` — the other piece of local, gitignored, rebuildable store state.
*Not* "in the project directory", which is the same place only under the in-repo
layout: under the split layout the store is its own repository elsewhere, and the
app finds it by asking ``kyo``, never by assuming the project path. Following the
store rather than the project is what makes one path correct under both layouts,
and it keeps the cross-host case intact — whatever arrangement lets the app read
this project's records lets it read this file.

WHY ``sessions.json`` AND NOT ``active-sessions.json``. The file holds ended
sessions too; that is *how* it says a session stopped being active (KYO-186.c7).
A name asserting liveness would be falsified by the file's own contents the moment
the first session ended. ``sessions.json`` names the subject, not a state, and
survives every entry it can ever hold.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
from argparse import Namespace
from dataclasses import dataclass, field
from pathlib import Path

from .clock import now
from .commands import Context, Output
from .config import KYOSEI_DIR, SESSIONS_NAME

#: The file's basename, beside ``kyosei.db`` under ``<store_root>/kyosei/``.
#: Re-exported from :mod:`kyosei.config`, which is where :mod:`kyosei.scaffold`
#: also reads it to write the gitignore — one spelling, two consumers.
SESSIONS_FILE = SESSIONS_NAME

#: The document's own version, so a reader that does not recognise it can say so
#: rather than mis-parse it. Bumped only when the *shape* below changes; this is
#: local state and not the export, so it is deliberately NOT the store's
#: ``schema_version`` and no compat machinery is involved.
SESSIONS_SCHEMA = 1

#: How many entries the file keeps. It is a record of recent sessions, not a log:
#: without a cap a long-lived project accumulates one entry per session forever,
#: and every reader here wants the newest few. Oldest ``last_seen`` first out.
MAX_SESSIONS = 32

#: The prefix the harness puts on a Remote Control session id in a transcript.
BRIDGE_ID_PREFIX = "cse_"

#: The prefix the same ULID carries in a claude.ai URL. Verified two ways rather
#: than assumed: a ULID taken from a transcript resolved to that session's real
#: page, and — independently — Claude Code's own ``Claude-Session:`` commit trailer
#: names ``https://claude.ai/code/session_<ULID>`` for the session whose transcript
#: carries ``cse_<the same ULID>``. The local session uuid used as a URL returns
#: "This session couldn't be found", so the two ids are not interchangeable.
REMOTE_URL_PREFIX = "https://claude.ai/code/session_"

#: The page that lists the account's own Remote Control sessions. The resolution's
#: last step, and not a consolation prize: on a machine where Claude Code runs
#: somewhere else this is what the feature *is*.
REMOTE_INDEX_URL = "https://claude.ai/code"

# ------------------------------------------------------- the named outcomes
#
# stop_target's rule, applied to this reach. Each of these is an answer a reader
# can act on; none of them is a guess, and none is a bare ``None`` that a caller
# would turn into a silent fallback.

#: A ``bridge-session`` record was found in the transcript's opening lines — the
#: ordinary case for a session that was on Remote Control from the start.
BRIDGE_IN_HEAD = "found-in-head"
#: None in the head, one in the closing lines: a session that connected to Remote
#: Control *after* it began. This outcome is the entire reason the ``Stop`` event
#: writes at all (see :func:`session_event_outcome`), and the entire reason the
#: read has a second half.
BRIDGE_IN_TAIL = "found-in-tail"
#: The transcript was read at both ends and carries no ``bridge-session`` record.
#: A real answer: this session is not on Remote Control. Two of thirteen
#: transcripts measured on this project were in exactly this state.
BRIDGE_ABSENT = "absent"
#: The payload named no transcript, so nothing was looked at.
BRIDGE_NO_TRANSCRIPT = "no-transcript-path"
#: A transcript was named and could not be read — missing, or not permitted.
BRIDGE_UNREADABLE = "transcript-unreadable"
#: A ``bridge-session`` record was found and did not carry a usable
#: ``bridgeSessionId``. The harness's format changed under us; this is the outcome
#: that must never be quietly turned into "not on Remote Control", because the two
#: have the same consequence and completely different causes.
BRIDGE_MALFORMED = "record-malformed"

BRIDGE_OUTCOMES: tuple[str, ...] = (
    BRIDGE_IN_HEAD,
    BRIDGE_IN_TAIL,
    BRIDGE_ABSENT,
    BRIDGE_NO_TRANSCRIPT,
    BRIDGE_UNREADABLE,
    BRIDGE_MALFORMED,
)

#: An entry for a session the harness has not told us has ended.
STATE_RUNNING = "running"
#: An entry whose session fired ``SessionEnd``. The half of KYO-186.c7 that the
#: rejected design could not do at all.
STATE_ENDED = "ended"

#: How many lines at each end of a transcript are read.
#:
#: Measured on this project's own thirteen transcripts rather than assumed: a
#: ``bridge-session`` record is re-emitted roughly every twenty-five lines for the
#: whole life of a connected session (35 to 118 copies per file), and the first
#: copy stood at line 2 to line 5 in every one of the eleven that had any. So the
#: head window finds it whenever the session was connected at start, with a wide
#: margin against the harness moving the record a little later.
#:
#: The window exists because transcripts are large — 7.7 MB and 2,818 lines was the
#: biggest here, and stop_target's own reader caps at 4,000 lines because "some of
#: them are hundreds of megabytes". This runs from the ``Stop`` hook on every turn,
#: so an unbounded read would be a cost paid per turn forever.
TRANSCRIPT_WINDOW_LINES = 400

#: How many bytes at the end of a transcript are read when the head has no record.
#: Sized against the same measurement: at ~25 lines between copies and the long
#: lines these files carry, the last quarter-megabyte holds several copies.
TRANSCRIPT_TAIL_BYTES = 256 * 1024


def sessions_path(store_root: Path | str) -> Path:
    """Where this project keeps the file, beside ``kyosei.db``."""
    return Path(store_root) / KYOSEI_DIR / SESSIONS_FILE


def remote_url(bridge_session_id: str) -> str:
    """The claude.ai page for a ``cse_``-prefixed Remote Control session id.

    The prefix is *swapped*, not stripped-and-hoped: an id that does not carry the
    prefix this build knows is not silently pasted into a URL, because a URL built
    from an unrecognised id is the confident answer about the wrong thing that this
    module's whole outcome vocabulary exists to prevent. The caller gets ``""`` and
    :data:`BRIDGE_MALFORMED` instead.
    """
    if not bridge_session_id.startswith(BRIDGE_ID_PREFIX):
        return ""
    ulid = bridge_session_id[len(BRIDGE_ID_PREFIX) :]
    return f"{REMOTE_URL_PREFIX}{ulid}" if ulid else ""


@dataclass
class BridgeLookup:
    """What one look at a transcript established, and how.

    :attr:`why` is rendered on every run, found and not-found alike, for the reason
    KYO-128 gives for :class:`kyosei.stop_target.StopTarget`: a verdict that does
    not name its subject cannot be acted on.
    """

    #: The ``cse_``-prefixed id, or None for every outcome except the two "found"s.
    bridge_session_id: str | None
    outcome: str
    why: str

    @property
    def found(self) -> bool:
        return self.outcome in (BRIDGE_IN_HEAD, BRIDGE_IN_TAIL)

    @property
    def url(self) -> str | None:
        if self.bridge_session_id is None:
            return None
        built = remote_url(self.bridge_session_id)
        return built or None

    def as_json(self) -> dict:
        return {
            "outcome": self.outcome,
            "found": self.found,
            "bridge_session_id": self.bridge_session_id,
            "url": self.url,
            "why": self.why,
        }


def _bridge_record_in(lines: list[str]) -> tuple[str | None, bool]:
    """The last ``bridge-session`` id among ``lines``, and whether one was seen.

    The two halves of the answer are separate because "no record here" and "a
    record here whose id this build cannot use" are different facts with different
    outcomes, and collapsing them would report a format change as "not connected".

    The substring pre-filter is not a parse: it only avoids running ``json.loads``
    over hundreds of long lines that cannot possibly match. Every candidate is then
    parsed properly and checked on its ``type`` discriminator.
    """
    seen = False
    found: str | None = None
    for line in lines:
        if "bridge-session" not in line:
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("type") != "bridge-session":
            continue
        seen = True
        value = record.get("bridgeSessionId")
        # The *last* one wins: the record repeats for the life of the session, and
        # a resumed session could in principle re-emit a different id. Taking the
        # newest is the only reading that stays right if it ever does.
        if isinstance(value, str) and remote_url(value):
            found = value
    return found, seen


def _head_lines(path: Path, limit: int) -> list[str]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        out: list[str] = []
        for index, line in enumerate(handle):
            if index >= limit:
                break
            out.append(line)
        return out


def _tail_lines(path: Path, window: int) -> list[str]:
    """The last ``window`` bytes of a file, as whole lines.

    The first line of the window is dropped: a byte offset lands mid-line, and a
    truncated JSON line would fail to parse anyway — dropping it says so on purpose
    rather than relying on the parse to swallow it.
    """
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > window:
            handle.seek(size - window)
        chunk = handle.read()
    text = chunk.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return lines[1:] if size > window and lines else lines


def find_bridge_session(transcript_path: str | None) -> BridgeLookup:
    """The Remote Control session id for one transcript, or a named reason.

    Read at both ends and nowhere in the middle, which is a decision about *when*
    each half pays off rather than an optimisation:

    * the HEAD answers for a session that was on Remote Control when it began,
      which is every ordinary case and is why ``SessionStart`` can write anything
      at all;
    * the TAIL answers for a session that connected LATER. A transcript is
      append-only, so a late connection puts its first ``bridge-session`` record
      after everything already written — which means a re-read of the head at
      ``Stop`` would learn nothing, and the refresh KYO-186 asks for would be
      decorative. The tail read is what makes the ``Stop`` event load-bearing.
    """
    if not isinstance(transcript_path, str) or not transcript_path:
        return BridgeLookup(
            None, BRIDGE_NO_TRANSCRIPT, "the hook payload carried no transcript_path"
        )
    path = Path(transcript_path)
    try:
        head = _head_lines(path, TRANSCRIPT_WINDOW_LINES)
    except OSError as exc:
        return BridgeLookup(None, BRIDGE_UNREADABLE, f"{path} could not be read ({exc})")
    found, seen = _bridge_record_in(head)
    if found is not None:
        return BridgeLookup(
            found,
            BRIDGE_IN_HEAD,
            f"bridge-session record in the first {TRANSCRIPT_WINDOW_LINES} lines of {path}",
        )
    try:
        tail = _tail_lines(path, TRANSCRIPT_TAIL_BYTES)
    except OSError as exc:
        return BridgeLookup(None, BRIDGE_UNREADABLE, f"{path} could not be read to its end ({exc})")
    tail_found, tail_seen = _bridge_record_in(tail)
    if tail_found is not None:
        return BridgeLookup(
            tail_found,
            BRIDGE_IN_TAIL,
            f"bridge-session record in the last {TRANSCRIPT_TAIL_BYTES} bytes of {path} — "
            "this session connected to Remote Control after it began",
        )
    if seen or tail_seen:
        # The record is there and this build cannot use it. Never folded into
        # "absent": the two have the same consequence for the pane and completely
        # different causes, and only one of them is worth anybody's attention.
        return BridgeLookup(
            None,
            BRIDGE_MALFORMED,
            f"{path} carries a bridge-session record with no usable "
            f"{BRIDGE_ID_PREFIX}-prefixed bridgeSessionId; the transcript format has "
            "changed and this build cannot build a claude.ai URL from it",
        )
    return BridgeLookup(
        None,
        BRIDGE_ABSENT,
        f"no bridge-session record at either end of {path}; this session is not on "
        "Remote Control",
    )


# ---------------------------------------------------------------- the file


@dataclass
class SessionsFile:
    """The file's contents, plus how the read went. Never raises; see :func:`read_sessions`."""

    entries: list[dict] = field(default_factory=list)
    #: None when the file was read cleanly (including "it is not there yet").
    problem: str | None = None
    #: True when a file exists at the path at all.
    present: bool = False


def read_sessions(path: Path) -> SessionsFile:
    """Read the file, treating every failure as "nothing recorded, and here is why".

    Total by design, on :func:`kyosei.facts.readProjectState`'s own argument one
    layer out: a corrupt or half-written file must cost a reader the session list,
    never the command. What it must never do is invent an entry — an unreadable
    file yields no entries and a stated reason, and every consumer then falls
    through to the step below it.
    """
    if not path.exists():
        return SessionsFile([], None, False)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return SessionsFile([], f"{path} could not be read as JSON ({exc})", True)
    if not isinstance(raw, dict):
        return SessionsFile([], f"{path} is not a JSON object", True)
    schema = raw.get("schema")
    if schema != SESSIONS_SCHEMA:
        return SessionsFile(
            [],
            f"{path} declares schema {schema!r}, and this build writes and reads "
            f"schema {SESSIONS_SCHEMA}",
            True,
        )
    entries = raw.get("sessions")
    if not isinstance(entries, list):
        return SessionsFile([], f"{path} carries no `sessions` array", True)
    kept = [entry for entry in entries if isinstance(entry, dict) and entry.get("session_id")]
    return SessionsFile(kept, None, True)


def write_sessions(path: Path, entries: list[dict]) -> str | None:
    """Write the file atomically. Returns a problem, never raises.

    Atomic because two sessions in one project write this file independently and a
    reader must never see half a document. NOT transactional, and that is stated
    rather than hidden: two writes that interleave between their reads can lose the
    later of the two entries. The consequence is bounded — each session rewrites
    its own entry on its next turn, so a lost update costs at most one turn's
    freshness — and a lock file that a crashed session could leave behind would be
    a worse failure than the one it prevents.
    """
    document = {
        "schema": SESSIONS_SCHEMA,
        "written": now(),
        # Newest first, so "the most recently active session" is the head for every
        # reader and two readers cannot disagree about which one that is.
        "sessions": sorted(entries, key=lambda e: str(e.get("last_seen", "")), reverse=True)[
            :MAX_SESSIONS
        ],
    }
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
        return f"{path} could not be written ({exc})"
    return None


# ------------------------------------------------------------- the events
#
# Three events, each with a job the other two cannot do. KYO-186's task asks for
# each choice to be stated with its reason, so they are stated here rather than in
# a commit message that the next reader will not find.

#: ``SessionStart`` — the session begins. The bridge record is already in the
#: transcript's opening lines for a session that was connected at start (measured:
#: first copy at line 2-5 in every transcript here that had one), so this is where
#: an entry usually gets its URL, and it is the only event that can create one
#: before the session has done any work.
EVENT_START = "SessionStart"
#: ``Stop`` — fires at the end of every turn. Here for exactly one reason, and it
#: is not liveness: a session that connects to Remote Control *after* it began has
#: no record in the head and gains one in the tail, and this is the only event that
#: comes back and looks again. It also keeps ``last_seen`` current, which is what
#: lets a reader rank two entries and what makes a crashed session's entry age out
#: of first place instead of outranking a live one forever.
EVENT_STOP = "Stop"
#: ``SessionEnd`` — the session is over. Marks *its own* entry ended, which is the
#: whole reason this design beats the scan KYO-186 rejected (c7): a file that only
#: ever grows is a file that says everything is running.
EVENT_END = "SessionEnd"

#: ``SubagentStop`` is deliberately absent. A dispatched worker's stop is not a
#: session of its own — the payload's ``session_id`` there is the *controller's* —
#: so recording it would write the controller's entry a second time under a
#: different event and teach a reader nothing. `kyo check-clean --subagent-stop`
#: already owns that event and already knows how to tell a worker from a helper.
SESSION_EVENTS: tuple[str, ...] = (EVENT_START, EVENT_STOP, EVENT_END)


#: What a hook's ancestry passes through on its way up from ``kyo`` to the session
#: that ran it. Claude Code runs a hook command through a shell, and the ``kyo`` on
#: PATH may itself be a ``#!/bin/sh`` shim that ``exec``s the runtime, so how many
#: of these sit between the two is not fixed -- which is why the walk skips them by
#: name rather than counting levels.
HOOK_SHELLS: frozenset[str] = frozenset(
    {"sh", "bash", "zsh", "dash", "ksh", "mksh", "fish", "tcsh", "csh", "env"}
)

#: How far up the walk goes before it gives up and records no process.
HOOK_ANCESTRY_LIMIT = 8


@dataclass(frozen=True)
class SessionProcess:
    """The process a session runs as, on the machine that ran its hook (KYO-390).

    Recorded so a reader can ask whether a session marked running still is: the
    ``state`` field only changes when a hook fires, and a session that is killed
    fires none. ``name`` is checked beside ``pid`` so a pid the system has since
    handed to something else does not read as the session; ``host`` because a pid
    means nothing on any other machine.
    """

    pid: int
    name: str
    host: str


def _parent_and_name(pid: int) -> tuple[int, str] | None:
    try:
        run = subprocess.run(
            ["ps", "-o", "ppid=,comm=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    parts = run.stdout.strip().split(None, 1)
    if run.returncode != 0 or len(parts) != 2 or not parts[0].isdigit():
        return None
    return int(parts[0]), os.path.basename(parts[1].strip()).lstrip("-")


def session_process(start: int | None = None) -> SessionProcess | None:
    """The session this hook runs under: the nearest ancestor that is not a shell.

    ``None`` when the walk cannot tell -- ``ps`` missing, a chain of nothing but
    shells up to ``init``, or deeper than :data:`HOOK_ANCESTRY_LIMIT`. A reader treats
    an entry with no process as one whose liveness cannot be checked, never as dead.

    One ``ps -p`` per level ON PURPOSE (KYO-422). A single ``ps -A -o
    pid=,ppid=,comm=`` snapshot walked in memory was built and measured on one
    macOS machine with ~940 processes: the snapshot alone takes ~27 ms, because ``comm``
    makes ``ps`` read every process's arguments, against ~2.2 ms per ``ps -p``. The
    real hook chain is ``kyo`` (or a shim that ``exec``s it) under the hook's
    ``sh`` under the session, so this walk is two or three calls, ~7 ms, and ~18 ms
    even at :data:`HOOK_ANCESTRY_LIMIT`. The snapshot loses at every depth this walk
    can reach.
    """
    pid = os.getppid() if start is None else start
    for _ in range(HOOK_ANCESTRY_LIMIT):
        if pid <= 1:
            return None
        found = _parent_and_name(pid)
        if found is None:
            return None
        parent, name = found
        if name not in HOOK_SHELLS:
            return SessionProcess(pid=pid, name=name, host=socket.gethostname())
        pid = parent
    return None


def session_event_outcome(event: str) -> str:
    """The entry state one hook event implies."""
    return STATE_ENDED if event == EVENT_END else STATE_RUNNING


def apply_event(
    entries: list[dict],
    *,
    session_id: str,
    event: str,
    cwd: str | None,
    lookup: BridgeLookup,
    stamp: str,
    process: SessionProcess | None = None,
) -> tuple[list[dict], dict]:
    """Fold one hook event into the entry list, returning the list and the entry.

    A pure function of its inputs so the whole decision — what an event does to an
    entry — is exercisable without a hook, a store, or a transcript.

    A ``SessionEnd`` never *removes* an entry. It marks it, because "ended at
    14:02" is a fact the pane can use (it is still the session this project was
    last driven from) and "gone" is not. Removal would also make an end
    indistinguishable from a session that was never recorded.
    """
    kept = [entry for entry in entries if entry.get("session_id") != session_id]
    previous = next((e for e in entries if e.get("session_id") == session_id), {})
    state = session_event_outcome(event)
    entry: dict = {
        "session_id": session_id,
        "state": state,
        "first_seen": previous.get("first_seen") or stamp,
        "last_seen": stamp,
        "last_event": event,
        "cwd": cwd,
        # Only ever the id and the URL. The account and organization uuids that sit
        # beside them in the transcript record are not read and not stored: see this
        # module's header, and KYO-186.c5.
        "remote_control_id": lookup.bridge_session_id,
        "url": lookup.url,
        "remote_control_outcome": lookup.outcome,
        "remote_control_why": lookup.why,
        "ended_at": stamp if state == STATE_ENDED else None,
        # KYO-390: what a reader checks to tell a live session from one that was
        # killed with no hook to say so. Kept from an earlier event when this one
        # could not find it, like the URL below.
        "pid": process.pid if process is not None else previous.get("pid"),
        "process": process.name if process is not None else previous.get("process"),
        "host": process.host if process is not None else previous.get("host"),
    }
    if not lookup.found:
        # A later event that cannot see the record must not erase one an earlier
        # event DID see. `SessionEnd` in particular can arrive after the transcript
        # has been rotated or moved, and an end that blanked the URL would take the
        # pane's target away at exactly the moment the entry became the most
        # interesting one in the file.
        for key in ("remote_control_id", "url"):
            if previous.get(key):
                entry[key] = previous[key]
                entry["remote_control_outcome"] = previous.get(
                    "remote_control_outcome", lookup.outcome
                )
                entry["remote_control_why"] = (
                    f"kept from an earlier event ({previous.get('last_event')}); "
                    f"this {event} saw: {lookup.why}"
                )
    return [entry, *kept], entry


def record_event(
    store_root: Path,
    *,
    session_id: str,
    event: str,
    cwd: str | None,
    transcript_path: str | None,
    process: SessionProcess | None = None,
) -> dict:
    """Read, fold and write, returning what happened. Never raises."""
    path = sessions_path(store_root)
    current = read_sessions(path)
    lookup = find_bridge_session(transcript_path)
    stamp = now()
    entries, entry = apply_event(
        current.entries,
        session_id=session_id,
        event=event,
        cwd=cwd,
        lookup=lookup,
        stamp=stamp,
        process=process,
    )
    problem = write_sessions(path, entries)
    return {
        "path": str(path),
        "event": event,
        "entry": entry,
        "bridge": lookup.as_json(),
        "read_problem": current.problem,
        "write_problem": problem,
    }


# --------------------------------------------------------------- resolution


def resolve_pane_url(file: SessionsFile) -> tuple[str, str]:
    """The URL a claude.ai pane should open, and why — the store's half of it.

    Steps 2 and 3 of KYO-186's resolution order. Step 1 (the session running in
    *this window's* terminal pane) is the app's, because only the app knows what is
    in its own pane; it falls through to here, which is why this returns a URL for
    every input rather than an Optional.
    """
    running = [e for e in file.entries if e.get("state") == STATE_RUNNING and e.get("url")]
    if running:
        # `entries` is written newest-first; sorted again here so a hand-edited or
        # foreign-ordered file cannot make two readers disagree.
        best = max(running, key=lambda e: str(e.get("last_seen", "")))
        return str(best["url"]), (
            f"the most recently active running session in {SESSIONS_FILE} "
            f"({best.get('session_id')}, last seen {best.get('last_seen')})"
        )
    ended = [e for e in file.entries if e.get("url")]
    if ended:
        best = max(ended, key=lambda e: str(e.get("last_seen", "")))
        return str(best["url"]), (
            f"no running session in {SESSIONS_FILE} has a Remote Control page; the most "
            f"recent one that does and has ended "
            f"({best.get('session_id')}, {best.get('ended_at')})"
        )
    if file.problem is not None:
        return REMOTE_INDEX_URL, f"{file.problem} — falling back to the account's own session list"
    if not file.present:
        return REMOTE_INDEX_URL, (
            f"no {SESSIONS_FILE} in this store yet (no session hook has run here) — "
            "the account's own session list"
        )
    return REMOTE_INDEX_URL, (
        f"{SESSIONS_FILE} names no session with a Remote Control page — "
        "the account's own session list"
    )


# ----------------------------------------------------------------- the command


def _payload_of(raw: str) -> dict:
    """A hook payload, or an empty dict for anything unreadable.

    :func:`kyosei.stop_target._payload_of`'s stance, and the same reasoning: a
    command that guessed at an unfamiliar payload shape would act confidently on
    something it cannot read. An unreadable payload becomes a named refusal below,
    never an exception.
    """
    try:
        payload = json.loads(raw) if raw and raw.strip() else {}
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def cmd_sessions(ctx: Context, args: Namespace) -> Output:
    """``kyo sessions`` — list the file, or (``--hook``) write this session into it.

    Two modes and one subject. Without ``--hook`` it prints what the file holds and
    which URL a pane would open, which is the file's consumer at the command line —
    every stored field here is rendered by it, so no field ships without one. With
    ``--hook`` it reads Claude Code's payload on stdin and folds the event in.

    ALWAYS EXIT 0. This runs from ``SessionStart``, ``Stop`` and ``SessionEnd``.
    None of those is a gate on anything, and a session that could not be *recorded*
    must still start, turn and end. Every failure below is a sentence, never an
    exit code — the same stance ``kyo check-clean`` takes for an unresolvable stop,
    for the same reason: the only party a refusal would reach is powerless to clear
    it.

    SILENT ON A SUCCESSFUL HOOK WRITE. ``SessionStart``'s stdout is injected into
    the session's context, where the compact brief is the only thing that belongs,
    and ``Stop`` fires every turn. So a hook that worked says nothing; a hook that
    could not write says one line. Run by hand, it says everything.
    """
    path = sessions_path(ctx.root)
    if not getattr(args, "hook", False):
        file = read_sessions(path)
        url, why = resolve_pane_url(file)
        lines = [f"sessions file: {path}"]
        if file.problem is not None:
            lines.append(f"  unusable: {file.problem}")
        elif not file.present:
            lines.append("  not written yet — no session hook has run in this project")
        for entry in file.entries:
            mark = "·" if entry.get("state") == STATE_RUNNING else "×"
            lines.append(
                f"  {mark} {entry.get('session_id')}  {entry.get('state')}  "
                f"last seen {entry.get('last_seen')}  ({entry.get('last_event')})"
            )
            where = entry.get("host") or "an unrecorded host"
            lines.append(
                f"      in {entry.get('cwd') or '—'}; process "
                + (
                    f"{entry.get('pid')} ({entry.get('process')}) on {where}"
                    if entry.get("pid")
                    else "not recorded"
                )
            )
            lines.append(
                f"      remote control: {entry.get('url') or '—'} "
                f"[{entry.get('remote_control_outcome')}]"
            )
            if not entry.get("url"):
                lines.append(f"      {entry.get('remote_control_why')}")
        lines.append(f"a pane here would open: {url}")
        lines.append(f"  because {why}")
        return Output(
            lines=lines,
            data={
                "ok": True,
                "path": str(path),
                "present": file.present,
                "problem": file.problem,
                "sessions": file.entries,
                "pane_url": url,
                "pane_url_why": why,
            },
        )

    # KYO-342, and a sentence rather than a refusal because of the rule stated above:
    # this command always exits 0. From a linked git worktree whose store resolved
    # through to the main checkout, `path` is THAT checkout's session ledger, and
    # folding this session into it would record one working tree's session as the
    # other's. Saying so is the whole remedy: the session that belongs in that ledger
    # is the one running in that checkout.
    reached_through = None if ctx.layout is None else ctx.layout.store_via_main_checkout
    if reached_through is not None:
        return Output(
            lines=[
                f"{SESSIONS_FILE}: {ctx.code_root} is a linked git worktree, reading the "
                f"store at {reached_through}; nothing recorded, because that ledger "
                "belongs to the session running in that checkout"
            ],
            data={
                "ok": False,
                "problem": "a linked worktree does not write the main checkout's ledger",
                "path": str(path),
            },
        )

    payload = _payload_of(sys.stdin.read() if not sys.stdin.isatty() else "")
    session_id = payload.get("session_id")
    event = payload.get("hook_event_name")
    if not isinstance(session_id, str) or not session_id:
        return Output(
            lines=[f"{SESSIONS_FILE}: the hook payload carried no session_id; nothing recorded"],
            data={"ok": False, "problem": "no session_id in the hook payload", "path": str(path)},
        )
    if not isinstance(event, str) or event not in SESSION_EVENTS:
        # Named rather than defaulted. A payload from an event this build does not
        # handle must not be folded in as though it were a `Stop`: the state that
        # would imply ("running") is a claim, and this command would be making it up.
        return Output(
            lines=[
                f"{SESSIONS_FILE}: hook_event_name {event!r} is not one this build records "
                f"({', '.join(SESSION_EVENTS)}); nothing recorded"
            ],
            data={
                "ok": False,
                "problem": f"unhandled hook_event_name {event!r}",
                "path": str(path),
            },
        )
    cwd = payload.get("cwd")
    result = record_event(
        Path(ctx.root),
        session_id=session_id,
        event=event,
        cwd=cwd if isinstance(cwd, str) else None,
        transcript_path=payload.get("transcript_path"),
        process=session_process(),
    )
    lines: list[str] = []
    if result["write_problem"] is not None:
        lines.append(f"{SESSIONS_FILE}: {result['write_problem']}")
    if result["read_problem"] is not None:
        lines.append(f"{SESSIONS_FILE}: {result['read_problem']} (rewritten from this session)")
    return Output(lines=lines, data={"ok": result["write_problem"] is None, **result})
