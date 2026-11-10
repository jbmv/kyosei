"""``kyo serve``'s send routes: the send half of chunk E4 (KYO-699, decision KYO-681 D1).

Under ``/v1/projects/{project}/sessions/{id}/``, each answering POST with a JSON body:

- ``send`` -- a message, ``{"segments": [{"kind": "typed"|"pasted", "text": ...}, ...]}``,
  to a session whose screen is idle or working;
- ``slash`` -- ``{"command": "/usage"}``, on an idle screen only, because a panel a command
  opens is modal;
- ``interrupt`` -- ``{}``, on a working screen only, because Escape on any other screen
  cancels a prompt or opens a menu.

Each may also carry ``"automatic": true``, which is written to the log. Tmux input reaches
the transcript as the person's own, so the log is what keeps an automatic send from being
credited to them.

THE ONLY ROUTE TO THE SENDING HALF. This is the one ``kyosei.serve*`` module that calls the
session layer's sending methods, and it reaches them only through :mod:`kyosei.session_layer`.
``tests/test_kyo_serve_send.py`` fails if another module reaches them.

WHAT STANDS IN FRONT OF A SEND, in order, each refused by a sentence naming its remedy:

1. the machine's owner has set ``serve_send: true`` in ``$KYOSEI_HOME/settings.yml``, read
   afresh on every request, so that clearing it takes effect without a restart. It is a
   machine setting rather than a project's, because a project's ``config.yml`` arrives with
   a clone and could otherwise switch this on;
2. a token held by a device, with the ``send`` permission (:mod:`kyosei.device_tokens`);
3. a body that parses and holds no control character that would act as a key;
4. the project's opt-in to reading transcripts, and its ``sessions.json`` recording the
   session, both as the read routes ask (:class:`kyosei.serve_sessions.ProjectSessions`);
5. a pane, and a screen the action may be sent to.

THE LOG. ``$KYOSEI_HOME/send-log.jsonl``, one JSON object per attempt, created private to
this account and refused if it would lie inside a git repository. Claude Code marks only
long pastes in its transcript (KYO-688.d1), so this is the only record of which segments of
a message were pasted. A row holds the time, the device, the project and session, each
segment's kind, length in characters and digest, whether the send was automatic, and the
outcome (``sent``, ``refused`` or ``failed``) with a reason code. It never holds a
segment's text. The file is opened before anything else, so a send that could not be
recorded does not happen, and a refused attempt is logged like any other. A request whose
token names no device has no device in its row, and a body is parsed only once its token has
been accepted, so a refused token's row has no segments.

A HELD MESSAGE (KYO-703) is stored by :mod:`kyosei.serve_held` and delivered from here, so
this stays the one module that reaches the sending methods. :func:`deliver_held` checks what
a send route checks, afresh at each attempt, because the message waited and the machine's
setting or the device may have changed. Its log rows have the actions ``hold``, ``retract``
and ``held-delivery``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import clock, device_tokens, home_settings, serve_files, session_layer
from .config import store_home
from .errors import KyoseiError
from .serve import Refusal
from .serve_sessions import ProjectSessions
from .transcript_archive import enclosing_git_repository

#: The log's name under ``$KYOSEI_HOME``: a file, so it is not taken for a project there.
LOG_FILE = "send-log.jsonl"

MESSAGE = "send"
SLASH = "slash"
INTERRUPT = "interrupt"
#: Log actions of a held message; a hold is parsed as a message is.
HOLD = "hold"
RETRACT = "retract"
HELD_DELIVERY = "held-delivery"

#: What follows ``sessions/{id}/``. Each answers POST only.
ROUTES = (MESSAGE, SLASH, INTERRUPT)

TYPED = "typed"
PASTED = "pasted"
#: How a slash command's one segment is marked in the log.
COMMAND = "command"

#: The log stores a request's session id as given, and an unauthenticated request chooses it.
_LOGGED_ID_LIMIT = 200

#: One message's keys must not interleave with another's. A send is a handful of tmux calls
#: per line, and one server sends to few panes, so a single lock is enough.
_SEND_LOCK = threading.Lock()

#: A first word that Claude Code would take as a command: a file path has a second slash.
_COMMAND_SHAPED = re.compile(r"/[^\s/]+(?:\s|$)")


def log_path(environ: dict[str, str]) -> Path:
    return store_home(environ) / LOG_FILE


@dataclass
class _Attempt:
    """One attempt, as its log row will read."""

    action: str
    project: str
    session_id: str
    at: str = field(default_factory=clock.now)
    device: dict | None = None
    segments: list[dict] | None = None
    automatic: bool = False
    outcome: str = "failed"
    status: int = 500
    reason: str | None = None
    #: What a hold's store said about the message, for the response. Not logged.
    held: dict | None = None

    def row(self) -> dict:
        return {
            "at": self.at,
            "action": self.action,
            "project": self.project[:_LOGGED_ID_LIMIT],
            "session_id": self.session_id[:_LOGGED_ID_LIMIT],
            "device": self.device,
            "segments": self.segments,
            "automatic": self.automatic,
            "outcome": self.outcome,
            "status": self.status,
            "reason": self.reason,
        }


class Body:
    """The request's body, read at most once.

    A body that is refused unread is still read and discarded, because closing a socket with
    data waiting resets the connection, and the client may then lose the refusal.
    """

    def __init__(self, read: Callable[[], bytes]) -> None:
        self._read = read
        self._done = False

    def read(self) -> bytes:
        self._done = True
        return self._read()

    def drain(self) -> None:
        if self._done:
            return
        with contextlib.suppress(Refusal):
            self.read()


@dataclass(frozen=True)
class _Segment:
    kind: str
    text: str

    def logged(self) -> dict:
        digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        return {"kind": self.kind, "length": len(self.text), "digest": f"sha256:{digest}"}


# ------------------------------------------------------------------------------- the log


def _open_log(environ: dict[str, str]) -> int:
    """The log, open to append. Raises the refusal that stops the send when it cannot be."""
    path = log_path(environ)
    try:
        serve_files.make_private_directory(path.parent)
        repository = enclosing_git_repository(path.parent)
        if repository is not None:
            raise _log_refusal(
                path,
                f"it would lie inside the git repository at {repository}, where it could be "
                "committed; point $KYOSEI_HOME at a directory outside every git repository",
            )
        return serve_files.open_private_log(path)
    except OSError as exc:
        raise _log_refusal(path, str(exc)) from exc


def _log_refusal(path: Path, why: str) -> Refusal:
    return Refusal(
        500,
        f"nothing was sent, because the send log {path} cannot be used: {why}. A send is only "
        "made when it can be recorded",
        reason="send-log-unusable",
    )


def _append(descriptor: int, attempt: _Attempt) -> None:
    os.write(descriptor, (json.dumps(attempt.row(), ensure_ascii=False) + "\n").encode("utf-8"))


# ------------------------------------------------------------------------- what is allowed


def _require_enabled(environ: dict[str, str]) -> None:
    try:
        settings = home_settings.load(environ)
    except KyoseiError as exc:
        raise Refusal(
            500,
            f"sending is refused because this machine's settings could not be read: {exc}",
            reason="settings-unreadable",
        ) from exc
    if not settings.serve_send:
        key = home_settings.SERVE_SEND_KEY
        raise Refusal(
            403,
            f"sending input to a session is off on this machine, and stays off until its owner "
            f"sets `{key}: true` in {settings.path}; nothing was sent",
            reason="send-disabled",
            setting=key,
        )


def _unauthorized(message: str, reason: str) -> Refusal:
    return Refusal(401, message, reason=reason, headers={"WWW-Authenticate": "Bearer"})


def _issue_one(permission: str) -> str:
    return f"`kyo devices issue <name> --permission {permission}`"


def _device_presented(
    environ: dict[str, str],
    authorization: str | None,
    permission: str = device_tokens.PERMISSION_SEND,
) -> device_tokens.Device:
    """The device this request's token was issued to, or a refusal naming ``permission``."""
    issue_one = _issue_one(permission)
    scheme, _, presented = (authorization or "").strip().partition(" ")
    if scheme.lower() != "bearer" or not presented.strip():
        raise _unauthorized(
            "this route needs the header `Authorization: Bearer <token>`; a token is issued "
            f"on this machine by {issue_one}",
            "no-token",
        )
    try:
        devices = device_tokens.read_devices(device_tokens.tokens_path(environ))
    except KyoseiError as exc:
        raise Refusal(500, str(exc), reason="tokens-unreadable") from exc
    device = device_tokens.holder_of(presented.strip(), devices)
    if device is None:
        raise _unauthorized(
            f"this token is not one this machine issued; issue one on it with {issue_one}",
            "unknown-token",
        )
    return device


def _require_permission(device: device_tokens.Device, permission: str) -> None:
    issue_one = _issue_one(permission)
    if device.revoked_at is not None:
        raise _unauthorized(
            f"the token of device {device.name} was revoked at {device.revoked_at}; issue a "
            f"new one with {issue_one}",
            "revoked-token",
        )
    if permission not in device.permissions:
        raise Refusal(
            403,
            f"the token of device {device.name} does not carry the {permission} permission; "
            f"revoke it (`kyo devices revoke {device.name}`) and issue another with {issue_one}, "
            "repeating --permission for each permission the device needs",
            reason=f"no-{permission}-permission",
        )


def _require_may_send(device: device_tokens.Device) -> None:
    _require_permission(device, device_tokens.PERMISSION_SEND)


def require_device(
    environ: dict[str, str],
    authorization: str | None,
    permission: str = device_tokens.PERMISSION_SEND,
) -> device_tokens.Device:
    """The device presenting this token, if it holds ``permission``; else the refusal.

    The composer's routes, which change no session, ask for this and not for the machine's
    ``serve_send`` setting, so that text can be saved and taken back while sending is off.
    The note routes (:mod:`kyosei.serve_notes`) ask for the ``notes`` permission.
    """
    device = _device_presented(environ, authorization, permission)
    _require_permission(device, permission)
    return device


def _held_device(environ: dict[str, str], device_id: str) -> device_tokens.Device:
    """The device that held a message, as the token file says it is now."""
    try:
        devices = device_tokens.read_devices(device_tokens.tokens_path(environ))
    except KyoseiError as exc:
        raise Refusal(500, str(exc), reason="tokens-unreadable") from exc
    for device in devices:
        if device.id == device_id:
            return device
    raise Refusal(
        403,
        f"the device that held this message, {device_id}, is no longer in the token file; "
        "retract the message and send it from a device with a token",
        reason="held-device-unknown",
    )


def bad_request(message: str) -> Refusal:
    return Refusal(400, message, reason="bad-request")


def json_object(raw: bytes) -> dict:
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise bad_request(f"the body is not JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise bad_request("the body must be a JSON object")
    return document


def _refuse_controls(text: str, allowed: str, what: str) -> None:
    """Refuse a control character, by its code point: sent to a pane it would act as a key."""
    for character in text:
        if character not in allowed and unicodedata.category(character) == "Cc":
            raise bad_request(
                f"{what} holds the control character U+{ord(character):04X}, which would act "
                "as a key in the pane. Remove it; a line break is a newline alone, and a tab "
                "can only be sent in a pasted segment"
            )


def _message_segments(document: dict) -> list[_Segment]:
    raw = document.get("segments")
    if not isinstance(raw, list) or not raw:
        raise bad_request(
            'the body needs "segments": a list of {"kind": "typed" or "pasted", "text": ...}'
        )
    segments: list[_Segment] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or item.get("kind") not in (TYPED, PASTED):
            raise bad_request(f'segments[{index}] needs a "kind" of "typed" or "pasted"')
        text = item.get("text")
        if not isinstance(text, str) or not text:
            raise bad_request(f'segments[{index}] needs a non-empty "text"')
        kind = item["kind"]
        _refuse_controls(text, "\n" if kind == TYPED else "\n\t", f"segments[{index}]")
        segments.append(_Segment(kind, text))
    whole = "".join(segment.text for segment in segments).lstrip()
    if not whole:
        raise bad_request("the segments hold only white space, so there is no message to send")
    if _COMMAND_SHAPED.match(whole):
        raise Refusal(
            400,
            "this message begins with what Claude Code would run as a slash command, and the "
            "message route sends on a working screen where a command's panel must not open; "
            'POST it to the slash route as {"command": ...}, or begin the message with other '
            "words",
            reason="message-is-a-command",
        )
    return segments


def message_segments(document: dict) -> list[dict[str, str]]:
    """A body's ``segments`` checked as a message's are, as plain objects, or the refusal.

    For a route that builds a message from them (``send-prompt``, KYO-709) before holding
    it, which checks the message it was given again.
    """
    return [{"kind": s.kind, "text": s.text} for s in _message_segments(document)]


def _command_segment(document: dict) -> _Segment:
    command = document.get("command")
    if not isinstance(command, str) or not command.startswith("/"):
        raise bad_request('the body needs "command": one line beginning with /')
    _refuse_controls(command, "", '"command"')
    return _Segment(COMMAND, command)


def _parsed(action: str, document: dict) -> tuple[list[_Segment], bool]:
    automatic = document.get("automatic", False)
    if not isinstance(automatic, bool):
        raise bad_request('"automatic" must be true or false')
    if action in (MESSAGE, HOLD):
        return _message_segments(document), automatic
    if action == SLASH:
        return [_command_segment(document)], automatic
    return [], automatic


# ------------------------------------------------------------------------------- sending


def _keys(live: session_layer.LiveSession, action: str, segments: list[_Segment]) -> None:
    """Hand the keys to the session layer. The caller holds :data:`_SEND_LOCK`."""
    if action in (MESSAGE, HELD_DELIVERY):
        live.send_message(
            [
                session_layer.Typed(s.text) if s.kind == TYPED else session_layer.Pasted(s.text)
                for s in segments
            ]
        )
    elif action == SLASH:
        live.send_slash_command(segments[0].text)
    else:
        live.send_interrupt()


def _deliver(live: session_layer.LiveSession, action: str, segments: list[_Segment]) -> None:
    """Hand the keys to the session layer, one send at a time."""
    with _SEND_LOCK:
        _keys(live, action, segments)


#: Called with the device, whether the message is automatic and its segments; stores the
#: message and returns its description for the response.
HoldMessage = Callable[[dict, bool, list[dict]], dict]


def _attempt(
    environ: dict[str, str],
    root_of: Callable[[], str],
    session_id: str,
    action: str,
    authorization: str | None,
    body: Body,
    attempt: _Attempt,
    hold: HoldMessage | None = None,
) -> None:
    """Everything that stands in front of a send, then the send. Fills in ``attempt``."""
    _require_enabled(environ)
    device = _device_presented(environ, authorization)
    attempt.device = {"id": device.id, "name": device.name}
    _require_may_send(device)
    segments, attempt.automatic = _parsed(action, json_object(body.read()))
    attempt.segments = [segment.logged() for segment in segments]
    project = ProjectSessions(root_of(), environ)
    project.require(session_id)
    live = project.open_live(session_id)
    if live.pane is None:
        raise Refusal(
            409,
            f"session {session_id} has no pane to send to ({live.pane_lookup.why}); it must be "
            "running in tmux on this machine",
            reason="no-pane",
        )
    if hold is not None:
        attempt.held = hold(
            attempt.device, attempt.automatic, [{"kind": s.kind, "text": s.text} for s in segments]
        )
        return
    try:
        _deliver(live, action, segments)
    except session_layer.PaneNotReady as exc:
        raise Refusal(409, str(exc), reason="screen-not-ready") from exc
    except (KyoseiError, OSError, subprocess.SubprocessError) as exc:
        raise Refusal(
            502,
            f"sending to session {session_id} did not finish, and keys may have reached the "
            f"pane: {exc}",
            reason="send-failed",
        ) from exc


def perform(
    environ: dict[str, str],
    project_name: str,
    root_of: Callable[[], str],
    session_id: str,
    action: str,
    authorization: str | None,
    read_body: Callable[[], bytes],
    *,
    hold: HoldMessage | None = None,
) -> dict[str, Any]:
    """Carry out one send route and log it; returns the response document or raises.

    With ``hold``, the message passes every check a send does and is then given to it to
    keep instead of being sent.
    """
    body = Body(read_body)
    attempt = _Attempt(action, project_name, session_id)
    try:
        descriptor = _open_log(environ)
    except Refusal:
        body.drain()
        raise
    try:
        try:
            _attempt(environ, root_of, session_id, action, authorization, body, attempt, hold)
        except Refusal as refusal:
            body.drain()
            attempt.status = refusal.status
            attempt.reason = refusal.extra.get("reason", "session-route")
            attempt.outcome = "failed" if attempt.reason == "send-failed" else "refused"
            raise
        except BaseException:
            attempt.reason = "server-error"
            raise
        attempt.outcome, attempt.status = ("held" if hold is not None else "sent"), 200
    finally:
        try:
            _append(descriptor, attempt)
        except OSError as exc:
            if attempt.outcome == "sent":
                raise Refusal(
                    500,
                    f"the keys were sent, but the send log {log_path(environ)} could not be "
                    f"written: {exc}",
                    reason="send-log-unusable",
                ) from exc
        finally:
            os.close(descriptor)
    document: dict[str, Any] = {
        "ok": True,
        "session_id": session_id,
        "action": action,
        "at": attempt.at,
        "device": attempt.device,
        "segments": attempt.segments,
        "automatic": attempt.automatic,
    }
    if hold is not None:
        document["held"] = attempt.held
    return document


# ------------------------------------------------------------------------ a held message


@dataclass(frozen=True)
class HeldOutcome:
    """What one attempt at a held message came to."""

    #: ``delivered``, ``waiting`` (tried again later) or ``failed`` (keys may have reached
    #: the pane, so it is not tried again).
    outcome: str
    reason: str | None = None
    detail: str | None = None
    #: This attempt saw an idle screen and has not yet sent (see ``settled``).
    idle: bool = False
    #: Set when the keys were sent and the log could not record it.
    log_problem: str | None = None


def _waiting(reason: str | None = None, detail: str | None = None, *, idle: bool = False):
    return HeldOutcome("waiting", reason, detail, idle)


#: The layer's names for the two states of a screen with its input box showing, as the screen
#: route publishes them. The session layer names no constant for them.
_IDLE = "idle"
_WORKING = "working"


#: The reason on the log row of a held message sent to a working screen (KYO-722).
DURING_TURN = "during-turn"


def _not_idle(screen: Any, *, during_turn: bool = False) -> HeldOutcome | None:
    """Why this screen must not receive a held message, or None when it may.

    ``during_turn`` is a person's Send now (KYO-722): a working screen may take it, since Claude
    Code queues what is typed during a turn and gives it to the agent at its next tool call.
    Every other screen is refused as it is for an ordinary delivery.
    """
    if screen is None:
        return _waiting("no-pane", "the pane could not be read")
    if screen.state == _WORKING and not during_turn:
        return _waiting()
    if screen.state not in (_IDLE, _WORKING):
        return _waiting(
            "screen-not-ready",
            f"the screen shows {screen.state}, which is left to the person to answer; the "
            "message is delivered once the session is idle again",
        )
    if screen.input_text:
        return _waiting(
            "input-box-in-use",
            "text is in the terminal's input box, and delivering would erase it; the message "
            "is delivered once the box is empty",
        )
    return None


def deliver_held(
    environ: dict[str, str],
    message: dict[str, Any],
    root_of: Callable[[], str],
    *,
    settled: bool,
    mark_delivering: Callable[[], None],
    during_turn: bool = False,
) -> HeldOutcome:
    """Try to deliver one held message, and say what happened. Reads the pane once or twice.

    ``settled`` says a previous look found this screen idle. A message goes only on the second
    of two looks a poll apart, because one wrong reading would put it into a running turn,
    which is the thing holding exists to prevent. The screen is read again under the send lock
    immediately before the keys, since the layer's own check would accept a working screen.
    ``mark_delivering`` runs before the first key, so a server that dies during the keys
    leaves a message that says so and is not sent twice.

    ``during_turn`` (KYO-722, Send now) delivers at once, with no second look, to a working
    screen as well as an idle one; the log row says so in its reason. A permission prompt, an
    overlay, an unrecognised screen and a used input box are refused as before.
    """
    project_name, session_id = message["project"], message["session_id"]
    try:
        _require_enabled(environ)
        _require_may_send(_held_device(environ, message["device"]["id"]))
        project = ProjectSessions(root_of(), environ)
        project.require(session_id)
        live = project.open_live(session_id)
        if live.pane is None:
            raise Refusal(
                409,
                f"session {session_id} has no pane ({live.pane_lookup.why}); the message is "
                "delivered if the session returns to a tmux pane, or it can be retracted",
                reason="no-pane",
            )
        screen = live.screen()
    except Refusal as refusal:
        return _waiting(refusal.extra.get("reason", "session-route"), str(refusal))
    except (KyoseiError, OSError, subprocess.SubprocessError) as exc:
        return _waiting("pane-unreadable", f"the pane could not be read: {exc}")
    blocked = _not_idle(screen, during_turn=during_turn)
    if blocked is not None:
        return blocked
    if not settled and not during_turn:
        return _waiting(idle=True)
    segments = [_Segment(s["kind"], s["text"]) for s in message["segments"]]
    attempt = _Attempt(
        HELD_DELIVERY,
        project_name,
        session_id,
        device=message["device"],
        segments=[segment.logged() for segment in segments],
        automatic=message["automatic"],
    )
    try:
        descriptor = _open_log(environ)
    except Refusal as refusal:
        return _waiting("send-log-unusable", str(refusal))
    result: HeldOutcome
    try:
        with _SEND_LOCK:
            try:
                blocked = _not_idle(live.screen(), during_turn=during_turn)
            except (KyoseiError, OSError, subprocess.SubprocessError) as exc:
                return _waiting("pane-unreadable", f"the pane could not be read: {exc}")
            if blocked is not None:
                return blocked
            mark_delivering()
            try:
                _keys(live, HELD_DELIVERY, segments)
            except session_layer.PaneNotReady as exc:
                attempt.outcome, attempt.status, attempt.reason = "refused", 409, "screen-not-ready"
                result = _waiting("screen-not-ready", str(exc))
            except (KyoseiError, OSError, subprocess.SubprocessError) as exc:
                attempt.outcome, attempt.status, attempt.reason = "failed", 502, "send-failed"
                result = HeldOutcome(
                    "failed",
                    "send-failed",
                    f"delivering to session {session_id} did not finish, and keys may have "
                    f"reached the pane, so it is not tried again: {exc}. Retract the message "
                    "and send it again if the transcript does not show it",
                )
            else:
                attempt.outcome, attempt.status = "sent", 200
                if during_turn:
                    attempt.reason = DURING_TURN
                result = HeldOutcome("delivered")
        try:
            _append(descriptor, attempt)
        except OSError as exc:
            if result.outcome == "delivered":
                result = HeldOutcome("delivered", log_problem=str(exc))
    finally:
        os.close(descriptor)
    return result


def log_held(
    environ: dict[str, str], message: dict[str, Any], action: str, device: dict | None
) -> None:
    """Record what became of a held message that did not reach the pane.

    Best effort: the text is back with the person, and refusing the retraction because the log
    is unusable would keep it from them.
    """
    segments = [_Segment(s["kind"], s["text"]).logged() for s in message["segments"]]
    attempt = _Attempt(
        action,
        message["project"],
        message["session_id"],
        device=device,
        segments=segments,
        automatic=message["automatic"],
        outcome="retracted",
        status=200,
    )
    try:
        descriptor = _open_log(environ)
    except Refusal:
        return
    try:
        with contextlib.suppress(OSError):
            _append(descriptor, attempt)
    finally:
        os.close(descriptor)
