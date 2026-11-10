"""``kyo serve``'s orchestrator start route (KYO-719, decision KYO-718).

``POST /v1/projects/{project}/orchestrator/start``, with a JSON object as its body, starts
that project's orchestrator session on the server's machine and answers with what ``kyo
orchestrator start <project> --json`` prints, ``waiting-for-trust`` and its folder included.
It exists so that a device with no SSH path to the machine can start a session.

THE ONE PROCESS ``kyo serve`` STARTS. Every other route reads, writes a file or a note, or
sends keys to a session that already runs. This one is therefore alone under
``orchestrator/``: no route stops a session (``/exit`` in its terminal ends one) or runs
another ``kyo orchestrator`` subcommand, and the command itself stays on
:data:`kyosei.serve.NOT_SERVED`.

WHAT STANDS IN FRONT OF A START, in order, each refused by a sentence naming its remedy:

1. a token held by a device, with the ``start`` permission (:mod:`kyosei.device_tokens`).
   The permission is its own, so a token issued to type into a session or to file notes does
   not start a process;
2. a body that is a JSON object;
3. a project this machine's registry names;
4. everything ``kyo orchestrator start`` checks, in :func:`kyosei.cmd_controller.start`. A
   session already running is a 409 with the reason ``already-running``, and no second
   session is started.

The machine's ``serve_send`` setting is not asked for: it governs keys sent to a session,
and this route sends none.

WHERE IT RUNS. :func:`kyosei.cmd_controller.start_registered` resolves the tmux server from
the server's own environment, with the variables that would move the socket dropped, so the
session is created on ``tmux -L kyosei`` as it is from a shell. The session's program is a
login shell, which builds its own ``PATH``, so the bare environment of a server that launchd
started does not reach claude.

THE LOG. Each start, and each refusal, is a row of the send log
(:data:`kyosei.serve_send.LOG_FILE`) with the action ``orchestrator-start``, the device, the
project and the outcome (``started``, ``refused`` or ``failed``) with a reason code. The row
has no session id and no segments. As for a send, the log is opened first, so a start that
could not be recorded does not happen.
"""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable
from typing import Any

from . import cmd_controller, device_tokens, serve_send
from .errors import KyoseiError
from .serve import Refusal

#: What follows ``/v1/projects/{project}/``.
ROUTE = "orchestrator"

#: The one action served under :data:`ROUTE`. It answers POST only.
START = "start"

#: The row's action in the send log.
LOG_ACTION = "orchestrator-start"

#: One start at a time: two requests for one project would otherwise both find no session,
#: and the second would fail in tmux instead of being told that one is running.
_START_LOCK = threading.Lock()


def not_served(action: str) -> Refusal:
    """The refusal for any other path under :data:`ROUTE`."""
    return Refusal(
        404,
        f"kyo serve starts an orchestrator session and does nothing else to one (KYO-718), so "
        f"`{ROUTE}/{action}` is not a route; POST to `{ROUTE}/{START}` to start one, end one "
        "with /exit in its terminal, and run every other `kyo orchestrator` subcommand in a "
        "terminal on the machine or over SSH",
        reason="not-served",
    )


def _start(
    environ: dict[str, str],
    root_of: Callable[[], str],
    authorization: str | None,
    body: serve_send.Body,
    attempt: serve_send._Attempt,
) -> dict[str, Any]:
    """Everything that stands in front of a start, then the start. Fills in ``attempt``."""
    permission = device_tokens.PERMISSION_START
    device = serve_send._device_presented(environ, authorization, permission)
    attempt.device = {"id": device.id, "name": device.name}
    serve_send._require_permission(device, permission)
    serve_send.json_object(body.read())
    root = root_of()
    with _START_LOCK:
        try:
            return cmd_controller.start_registered(root, environ)
        except cmd_controller.AlreadyRunning as exc:
            raise Refusal(
                409,
                f"no second session was started: {exc}",
                reason="already-running",
            ) from exc
        except (KyoseiError, OSError, subprocess.SubprocessError) as exc:
            raise Refusal(
                502,
                f"the orchestrator session was not started: {exc}",
                reason="start-failed",
            ) from exc


def perform(
    environ: dict[str, str],
    project_name: str,
    root_of: Callable[[], str],
    authorization: str | None,
    read_body: Callable[[], bytes],
) -> dict[str, Any]:
    """Carry out one start and log it; returns the response document or raises."""
    body = serve_send.Body(read_body)
    attempt = serve_send._Attempt(LOG_ACTION, project_name, "")
    try:
        descriptor = serve_send._open_log(environ)
    except Refusal as refusal:
        body.drain()
        raise Refusal(
            refusal.status,
            f"no orchestrator session was started, because a start is only made when it can "
            f"be recorded: {refusal}",
            **refusal.extra,
        ) from refusal
    try:
        try:
            document = _start(environ, root_of, authorization, body, attempt)
        except Refusal as refusal:
            body.drain()
            attempt.status = refusal.status
            attempt.reason = refusal.extra.get("reason", "project-route")
            attempt.outcome = "failed" if attempt.reason == "start-failed" else "refused"
            raise
        except BaseException:
            attempt.reason = "server-error"
            raise
        attempt.outcome, attempt.status, attempt.reason = "started", 200, document["state"]
    finally:
        try:
            serve_send._append(descriptor, attempt)
        except OSError as exc:
            if attempt.outcome == "started":
                raise Refusal(
                    500,
                    f"the session was started, but the send log {serve_send.log_path(environ)} "
                    f"could not be written: {exc}",
                    reason="send-log-unusable",
                ) from exc
        finally:
            os.close(descriptor)
    return {**document, "at": attempt.at, "device": attempt.device}
