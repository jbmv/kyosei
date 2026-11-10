"""``kyo serve`` — the CLI serves its own ``--json`` over loopback HTTP (KYO-453).

The whole server is the standard library's :class:`http.server.ThreadingHTTPServer`; the runtime
dependency set stays pyyaml alone.

WHAT IT SERVES. Three routes under ``/v1``, and every response carries
``Kyosei-Version``:

- ``GET /v1/version`` — ``kyo version --json`` as ``kyosei``, plus this build's
  ``schema_version``, the server's ``pid`` and ``started_at``, and ``stale``: whether
  the build installed on disk is no longer the one this process started as.
- ``GET /v1/projects`` — the machine's registry (``$KYOSEI_HOME/projects.json``),
  read afresh on every request, one ``kyo where --json`` per entry.
- ``GET /v1/projects/{project}/cmd/{command}[/{positional}…][?flag=value…]`` —
  exactly ``kyo <command> <positionals> --<flag>=<value> --root <root> --json``, and
  the body is the bytes that invocation prints. Flags are parsed by the CLI's own
  parser (``cli.build_parser``); no second argument grammar exists here.

HOW A COMMAND RUNS. In-process, through :func:`kyosei.cli.run`, under ONE lock (the
path-warning collector is module state, and so are the redirected streams), on a
store opened read-only: ``PRAGMA query_only`` underneath and
:meth:`kyosei.store.Store.transaction` refusing by a sentence naming `kyo serve`
above it. What may run at all is :data:`SERVED_COMMANDS`; every other command in
``cli.COMMAND_TABLE`` is on :data:`NOT_SERVED` with its reason, and
``tests/test_kyo_serve.py`` fails when a command is on neither.

THE NETWORK STANCE. It binds a loopback address and nothing else — there is no
override, because with no password and no TLS a LAN bind makes every device on the
LAN a reader of every project. Loopback is not a boundary against a web page in the
same browser, so three mechanical checks stand in front of every route: the ``Host``
must be a loopback name or one given with ``--allow-host`` (DNS rebinding), an
``Origin``, when sent, must be one given with ``--allow-origin`` (a cross-site
``fetch``), and a ``POST`` must carry ``Content-Type: application/json`` (a
cross-site form cannot send it without a preflight nobody answers).

THE FILE ROUTES AND THE EVENTS (KYO-456). Under
``/v1/projects/{project}/``: ``ideas/`` (GET, the tree), ``ideas/{relpath}`` (GET, PUT,
DELETE, with ``ETag``/``If-Match``/``If-None-Match``), ``ideas-move`` and
``ideas-mkdir`` (POST, JSON), ``scratchpad`` (GET, PUT) and ``events`` (GET,
Server-Sent Events). This module only routes them: every file they read or write goes
through :mod:`kyosei.serve_files`, the write boundary, and the stream is
:mod:`kyosei.serve_events`. ``docs/{relpath}`` (GET, KYO-467) is the one read-only file
route beside them: a governed document's text, for a file ``kyo docs check`` lists and
nothing else (:func:`kyosei.serve_files.document_target`).
``tests/test_kyo_serve_files.py`` fails if any other ``kyosei.serve*`` module calls a
file-writing function.

THE SESSION ROUTES (KYO-689). Under ``/v1/projects/{project}/``: ``sessions`` (GET),
``sessions/{id}/turns``, ``sessions/{id}/events`` (Server-Sent Events),
``sessions/{id}/screen``, ``sessions/{id}/changes``, ``sessions/{id}/images/{turn}/{index}``
and ``sessions/{id}/subagents/{agent}``, all read-only and in a project that has opted in to
reading transcripts. This module only routes them;
:mod:`kyosei.serve_sessions` holds the rest.

THE SEND ROUTES (KYO-699). ``sessions/{id}/send``, ``.../slash`` and ``.../interrupt`` (POST)
change a session, each after reading its screen. They are off until the machine's owner sets
``serve_send: true`` in ``$KYOSEI_HOME/settings.yml``, need a device token holding the send
permission, and are logged; :mod:`kyosei.serve_send` holds all of that, and is the one
``kyosei.serve*`` module that can reach the session layer's sending half.

THE COMPOSER ROUTES (KYO-703). ``sessions/{id}/draft`` (GET, PUT) is a session's saved
composer boxes (:mod:`kyosei.serve_drafts`); ``sessions/{id}/held`` (GET, POST, DELETE) is a
message held until the turn ends (:mod:`kyosei.serve_held`), and ``sessions/{id}/held-now`` (POST,
KYO-722) delivers it at once, during a turn. Both need a device token.
``sessions/{id}/attachments`` (POST) and ``sessions/{id}/attachments/{attachment}`` (GET,
KYO-715) are the files a prompt attaches (:mod:`kyosei.serve_attachments`); that POST is the
one that carries a file's own bytes and type rather than JSON, and only the types that module
accepts, none of which a cross-site form can send.

THE NOTE ROUTES (KYO-706). ``notes`` (GET, POST), ``notes/{id}`` (GET), ``notes/{id}/tags``
(POST) and ``sessions/{id}/send-notes`` (POST) are :mod:`kyosei.serve_notes`, and need a device
token holding the ``notes`` permission. So is ``sessions/{id}/send-prompt`` (POST, KYO-709), a
composer prompt with its marks, which needs the ``send`` permission and ``notes`` with marks,
and so are ``sessions/{id}/marks`` and ``tags`` (GET, KYO-710), which only read.
They are the one exception to a read-only store: each write opens its own connection and goes
through the transaction ``kyo notes`` uses.

THE TERMINAL ROUTES (KYO-721). ``sessions/{id}/terminal`` (GET, Server-Sent Events) streams a
session's pane as its program draws it, and ``.../terminal/input`` and ``.../terminal/resize``
(POST) type into it and state the size of the client's view. They send whatever is typed to
whatever the screen shows, so they need a device token holding the ``terminal`` permission as
well as the machine's ``serve_send``, and each connection is logged;
:mod:`kyosei.serve_terminal` holds that, and reaches the pane through a tmux control client of
its own.

THE START ROUTE (KYO-719, decision KYO-718). ``orchestrator/start`` (POST) starts the
project's orchestrator session as ``kyo orchestrator start`` does, and is the one process
this server starts. It needs a device token holding the ``start`` permission and is logged;
:mod:`kyosei.serve_orchestrator` holds that. Nothing else is served under ``orchestrator/``,
and the ``orchestrator`` command stays on :data:`NOT_SERVED`.

AT LOGIN (KYO-457). ``--install``, ``--uninstall``, ``--status`` and
``--restart`` are :mod:`kyosei.serve_install`; this module only hands them over. Before
it binds, ``kyo serve`` asks its port who is there (:func:`serve_install.refuse_if_held`)
and refuses by name: another kyo serve by its pid and build, anything else by ``--port``.

Not here, and deliberately not stubbed: the registry's writer (``kyo projects``, KYO-455).
"""

from __future__ import annotations

import contextlib
import io
import ipaddress
import json
import os
import re
import select
import signal
import socket
import sys
import threading
import urllib.parse
from argparse import Namespace
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .errors import EXIT_OK, EXIT_USAGE, KyoseiError

#: The default port. 47369, one below it, was the port of the server the desktop app
#: once ran; the app now runs no server and reads through kyo serve.
DEFAULT_PORT = 47370

#: The default and only kind of bind address.
DEFAULT_BIND = "127.0.0.1"

#: The header every response carries.
VERSION_HEADER = "Kyosei-Version"

#: The one API prefix this server speaks (never two side by side).
API_PREFIX = "/v1"

#: What a refusal made by the server itself (rather than by a command) says it is.
REFUSED_BY = "kyo serve"

#: The machine's project registry, beside the per-project homes (KYO-379).
REGISTRY_FILE = "projects.json"

#: The names a loopback request's ``Host`` may carry.
LOOPBACK_HOST_NAMES = ("127.0.0.1", "localhost", "[::1]")

#: The remote path a refused bind names (KYO-441).
REMOTE_PATH = (
    f"to reach it from another machine, keep it on loopback and put Tailscale Serve in "
    f"front of it (`tailscale serve --bg {DEFAULT_PORT}`, then start this server with "
    "--allow-host <machine>.<tailnet>.ts.net); never `tailscale funnel`, which publishes "
    "it to the internet"
)


# ------------------------------------------------------------- what is served


@dataclass(frozen=True)
class Served:
    """How one command is served.

    ``actions``: the first positional values allowed (the command's sub-action), or
    ``None`` for a command without one. ``refused``: argparse destinations that must
    stay at their default — each a flag that writes, or reads stdin. Both are checked
    against the PARSED namespace, so an abbreviation argparse accepts (``?pru`` for
    ``--prune``) is caught as surely as the flag spelled out.
    """

    actions: frozenset[str] | None = None
    refused: tuple[str, ...] = ()


#: The commands `kyo serve` runs, with their limits.
SERVED_COMMANDS: dict[str, Served] = {
    "show": Served(),
    "list": Served(),
    "map": Served(),
    "next": Served(),
    "where": Served(),
    "version": Served(),
    # `--output` writes a file in the filesystem.
    "changelog": Served(refused=("output",)),
    # KYO-565: `rules add` writes kyosei/procedures.md; without its flags it refuses
    # before reading anything, so refusing the flags keeps the served form read-only.
    "rules": Served(refused=("add_title", "audience", "enforced_by", "reason_file")),
    "reference": Served(refused=("output",)),
    "report-schema": Served(),
    "base": Served(actions=frozenset({"list", "run"})),
    # `settle` writes the store and `assemble` writes the code tree; `suggest` is not
    # needed by any viewer.
    "docs": Served(actions=frozenset({"check", "obligations", "preview"})),
    # `--prune` removes directories; `--include-unmerged` only qualifies it.
    "worktrees": Served(refused=("prune", "include_unmerged")),
    # `--hook` reads stdin and writes sessions.json.
    "sessions": Served(refused=("hook",)),
    # KYO-454: what the desktop app read by SQL and git; each is read-only.
    "briefs": Served(),
    "shift-notes": Served(),
    "gates": Served(),
    "activity": Served(),
}

_WRITES_STORE = "it writes the store (cli.MUTATING_COMMANDS)"
_GUARD = "it is a worktree guard run by a hook, answering about the checkout it runs in"
_WRITES_FILES = "it writes files in the project"

#: Every other command, each with the reason it is not served.
NOT_SERVED: dict[str, str] = {
    "init": "it creates a project",
    "new": _WRITES_STORE,
    "notes": _WRITES_STORE,
    "credit": _WRITES_STORE,
    "promote": _WRITES_STORE,
    "spawn": _WRITES_STORE,
    "edit": _WRITES_STORE,
    "restate": _WRITES_STORE,
    "move": _WRITES_STORE,
    "wont-do": _WRITES_STORE,
    "void": _WRITES_STORE,
    "link": _WRITES_STORE,
    "unlink": _WRITES_STORE,
    "gate": _WRITES_STORE,
    "retag": _WRITES_STORE,
    "redo": _WRITES_STORE,
    "ungate": _WRITES_STORE,
    "claim": _WRITES_STORE,
    "verify": _WRITES_STORE,
    "attach": _WRITES_STORE,
    "prior-art": _WRITES_STORE,
    # KYO-586: records a project's choice in kyosei/config.yml.
    "prior-art-setting": "it writes kyosei/config.yml",
    "redact": _WRITES_STORE,
    "dispatch": _WRITES_STORE,
    "report": _WRITES_STORE,
    "accept": _WRITES_STORE,
    "confirm": _WRITES_STORE,
    "close-wave": _WRITES_STORE,
    "split": _WRITES_STORE,
    "sweep": _WRITES_STORE,
    "flag": _WRITES_STORE,
    # Every run writes a brief_runs row.
    "brief": _WRITES_STORE,
    "note": _WRITES_STORE,
    "ideas": "`migrate-stamps --apply` rewrites ideas/, and the idea tree is the ideas/ file route",
    "export": "it writes kyosei/export/",
    "rebuild": "it rebuilds the database",
    "doctor": "it is a maintenance check run where the store is, not a viewer's read",
    "touch-drift": "it is an orchestrator's check on a dispatch branch, not a viewer's read",
    "check-branch": _GUARD,
    "check-clean": _GUARD,
    "check-build": _GUARD,
    "check-command": _GUARD,
    "check-push": _GUARD,
    "pre-tool-use": _GUARD,
    "turns": "it is a session hook writing the local turn index and archive, not a read",
    "install-hooks": _WRITES_FILES,
    "install-skill": _WRITES_FILES,
    "install-procedures": _WRITES_FILES,
    "install-agents": _WRITES_FILES,
    "serve": "it is this server",
    # Machine changes go over SSH, never through a route. A start alone has a route of its
    # own (KYO-719), which is not this command: nothing served stops a session.
    "orchestrator": "it starts and ends processes on this machine (over SSH only)",
    # KYO-519 (KYO-513): the same command under its pre-rename alias.
    "controller": "it is `orchestrator` under its old name (KYO-513), never served",
    # KYO-455: the CLI is the registry's one writer; kyo serve never registers.
    "projects": "it writes this machine's project registry, which kyo serve only reads",
    # KYO-514: a setup prompt and a machine-wide audit, for a terminal, not a viewer.
    "onboard": "it prints a setup prompt and audits this machine's projects for a terminal",
    # KYO-594: links and registers a harness plugin on this machine.
    "harness": "it writes a link in this machine's Kyosei home and runs `claude`",
    # KYO-699: a device must not be able to issue itself a token.
    "devices": "it issues and revokes the tokens this server checks, on this machine only",
    "statusline": "it is Claude Code's status line command, and writes this machine's settings",
}

#: Query keys refused by name: the server sets `--root` from `{project}`, runs as
#: nobody in particular, and `--json` is always on.
REFUSED_QUERY_KEYS = {
    "root": "the server sets --root from the {project} in the path",
    "actor": "a served command is a read, and a read has no actor",
    "json": "--json is always on",
    "help": "help is text, not a --json document; run `kyo <command> --help` in a terminal",
}

#: KYO-456: the per-project file and event routes, with the methods each
#: answers. Everything they touch on disk goes through :mod:`kyosei.serve_files`.
FILE_ROUTES: dict[str, tuple[str, ...]] = {
    "ideas": ("GET", "PUT", "DELETE"),
    "ideas-move": ("POST",),
    "ideas-mkdir": ("POST",),
    "scratchpad": ("GET", "PUT"),
    "events": ("GET",),
    # KYO-467: a governed document's text, read-only (serve_files.document_target).
    "docs": ("GET",),
}

#: The file routes that take a path after their name.
_PATH_ROUTES = ("ideas", "docs")

#: KYO-689: what follows ``sessions/{id}/``. Every session route answers GET only.
SESSION_ROUTES = ("turns", "events", "screen", "changes")

#: KYO-692: ``sessions/{id}/images/{turn}/{index}``, one image the person sent.
SESSION_IMAGE_ROUTE = "images"

#: KYO-693: ``sessions/{id}/subagents/{agent}``, one subagent's tool calls.
SESSION_SUBAGENT_ROUTE = "subagents"

#: KYO-699: what follows ``sessions/{id}/`` on the routes that send input (POST only).
#: :data:`kyosei.serve_send.ROUTES`, spelled here because that module imports this one.
SESSION_SEND_ROUTES = ("send", "slash", "interrupt")

#: KYO-703: the composer's routes under ``sessions/{id}/``, and the methods each answers.
SESSION_COMPOSER_ROUTES: dict[str, tuple[str, ...]] = {
    "draft": ("GET", "PUT"),
    "held": ("GET", "POST", "DELETE"),
    # KYO-722: deliver the held message now, to a working screen (serve_held.send_now).
    "held-now": ("POST",),
    # KYO-706: a set of notes, sent as one held message (serve_notes).
    "send-notes": ("POST",),
    # KYO-709: one composer prompt with its marks in place (serve_notes).
    "send-prompt": ("POST",),
}

#: KYO-715: ``sessions/{id}/attachments`` (POST) and ``.../attachments/{id}`` (GET).
SESSION_ATTACHMENTS_ROUTE = "attachments"

#: KYO-706: the note routes, under ``/v1/projects/{project}/``.
NOTES_ROUTE = "notes"
_NOTE_TAGS_ROUTE = "tags"

#: KYO-710: ``sessions/{id}/marks`` (GET), a session's sent notes in reply offsets, and
#: ``tags`` (GET), the tags in use; both :mod:`kyosei.serve_notes`.
SESSION_MARKS_ROUTE = "marks"
TAGS_ROUTE = "tags"

#: KYO-721: ``sessions/{id}/terminal`` (GET, a stream) and the POST routes beneath it.
#: :data:`kyosei.serve_terminal.ROUTE`, ``INPUT`` and ``RESIZE``, spelled here because that
#: module imports this one.
SESSION_TERMINAL_ROUTE = "terminal"
SESSION_TERMINAL_ACTIONS = ("input", "resize")

#: KYO-719: ``orchestrator/start`` (POST), the one process this server starts.
#: :data:`kyosei.serve_orchestrator.ROUTE` and ``START``, spelled here because that module
#: imports this one.
ORCHESTRATOR_ROUTE = "orchestrator"
ORCHESTRATOR_START = "start"

#: An image at one turn and index never changes. It is the person's, so only their browser
#: may keep it, and a client that re-renders a chat need not read the transcript again.
_IMAGE_CACHE = "private, max-age=86400"

#: The one query key a session route takes.
_SESSION_QUERY_KEY = "cursor"

_JSON_TYPE = "application/json; charset=utf-8"

_QUERY_KEY = re.compile(r"^[a-z][a-z0-9-]*$")
_HOST_PORT = re.compile(r"^(?P<name>\[[^\]]*\]|[^:]*)(?::(?P<port>\d+))?$")


# ------------------------------------------------------------------ refusals


class Refusal(Exception):
    """A request the server itself refuses: an HTTP status and a sentence naming the
    remedy. Rendered as ``{"ok": false, "error": …, "refused_by": "kyo serve"}``."""

    def __init__(
        self, status: int, message: str, *, headers: dict[str, str] | None = None, **extra
    ) -> None:
        super().__init__(message)
        self.status = status
        self.extra = extra
        self.headers = dict(headers or {})

    def body(self) -> dict:
        return {"ok": False, "error": str(self), "refused_by": REFUSED_BY, **self.extra}


def check_bind(address: str) -> str:
    """The address to bind for ``--bind address``, or a refusal naming the remote path.

    Loopback only, with no override. ``localhost`` is taken as ``127.0.0.1``.
    """
    candidate = address.strip()
    if candidate.lower() == "localhost":
        return DEFAULT_BIND
    try:
        parsed = ipaddress.ip_address(candidate.strip("[]"))
    except ValueError:
        parsed = None
    if parsed is not None and parsed.is_loopback:
        return str(parsed)
    raise KyoseiError(
        f"kyo serve binds a loopback address only, and {address!r} is not one: it has no "
        "password and no TLS, so any other address would make every device that can "
        "reach it a reader of every project and a writer of every ideas/, and there is "
        f"no flag that allows it. Bind 127.0.0.1 (the default) or ::1; {REMOTE_PATH}"
    )


def version_header(version_payload: dict) -> str:
    """``Kyosei-Version``'s value: the release (``0.2.0``), or for an unreleased build
    the version with its commit as a local part (``0.2.0+c99aab56``)."""
    version = str(version_payload.get("version") or "unknown")
    if version_payload.get("release") or not version_payload.get("commit_short"):
        return version
    return f"{version}+{version_payload['commit_short']}"


# ------------------------------------------------------------------ registry


@dataclass(frozen=True)
class Project:
    name: str
    root: str


def registry_path(environ: dict[str, str]) -> Path:
    from .config import store_home

    return store_home(environ) / REGISTRY_FILE


def read_registry(path: Path) -> list[str]:
    """The roots ``projects.json`` lists, read now.

    KYO-379's guard, kept: a missing file is the ordinary empty list, and a file that
    cannot be read is REFUSED by its own sentence, never read as empty.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise _unreadable_registry(path, str(exc)) from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _unreadable_registry(path, f"it is not JSON ({exc})") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("projects"), list):
        raise _unreadable_registry(path, 'it is not an object with a "projects" list')
    roots: list[str] = []
    for index, entry in enumerate(raw["projects"]):
        root = entry.get("root") if isinstance(entry, dict) else None
        if not isinstance(root, str) or not root.strip():
            raise _unreadable_registry(path, f'projects[{index}] has no "root" path')
        roots.append(root)
    return roots


def _unreadable_registry(path: Path, why: str) -> Refusal:
    return Refusal(
        500,
        f"kyo serve cannot read the project registry {path}: {why}. It serves no project "
        "until the file is fixed, rather than reading it as empty; correct it by hand "
        '(`{"projects": [{"root": "/path/to/project"}]}`) or remove it',
        registry=str(path),
    )


def project_name(root: str) -> str:
    """A registry entry's name in URLs: its per-project home name."""
    from .config import project_home_name, resolve_layout

    try:
        return project_home_name(resolve_layout(explicit_root=root, required=False))
    except Exception:  # noqa: BLE001 - an unopenable entry is still listed, by its path
        return Path(root).expanduser().name


# -------------------------------------------------------------------- server


class KyoServer(ThreadingHTTPServer):
    """One `kyo serve`: its guards' configuration, its lock, and its start identity."""

    daemon_threads = True
    #: socketserver's default backlog is 5, and a viewer composing one page makes
    #: several reads at once; the sixth connection was reset (measured in the suite).
    request_queue_size = 64

    def __init__(
        self,
        bind: str = DEFAULT_BIND,
        port: int = DEFAULT_PORT,
        *,
        allow_hosts: tuple[str, ...] = (),
        allow_origins: tuple[str, ...] = (),
        environ: dict[str, str] | None = None,
        log_stream=None,
    ) -> None:
        from . import clock

        address = check_bind(bind)
        if ipaddress.ip_address(address).version == 6:
            self.address_family = socket.AF_INET6
        self.environ = dict(os.environ if environ is None else environ)
        self.allow_hosts = tuple(h.strip().lower() for h in allow_hosts if h.strip())
        self.allow_origins = tuple(o.strip().rstrip("/") for o in allow_origins if o.strip())
        # Captured before any command runs: the streams are redirected under the
        # lock while one does, and a request line logged by another thread then must
        # not land inside that command's captured output.
        self.log_stream = log_stream if log_stream is not None else sys.stderr
        self.lock = threading.Lock()
        #: Set when the server stops, so an open event stream ends with it.
        self.stopping = threading.Event()
        #: The event stream's cadence; a test shortens both.
        from .serve_events import KEEPALIVE_SECONDS, POLL_SECONDS

        self.poll_seconds = POLL_SECONDS
        self.keepalive_seconds = KEEPALIVE_SECONDS
        #: KYO-689: the per-session streams' samplers, and their cadence (a test shortens it).
        from .serve_sessions import SAMPLE_SECONDS, SessionHub

        self.session_sample_seconds = SAMPLE_SECONDS
        self.session_hub = SessionHub(self)
        #: KYO-703: messages held until a turn ends, and how often one is looked at.
        from .serve_held import HELD_POLL_SECONDS, HeldMessages

        self.held_poll_seconds = HELD_POLL_SECONDS
        self.held_messages = HeldMessages(self)
        #: KYO-721: the open terminal connections, each a tmux control client.
        from .serve_terminal import Terminals

        self.terminals = Terminals(self)
        self.pid = os.getpid()
        self.started_at = clock.now()
        try:
            super().__init__((address, port), ServeHandler)
        except OSError as exc:
            raise KyoseiError(
                f"kyo serve cannot listen on {address} port {port}: {exc.strerror or exc}. "
                "If another server holds that port, stop it or start this one with "
                "--port N"
            ) from exc
        self.port = self.server_address[1]
        #: The build this process started as, the reference `stale` compares with.
        self.started_version = self.version_payload()
        self.held_messages.resume()

    def shutdown(self) -> None:
        self.stopping.set()
        self.terminals.close_all()
        super().shutdown()

    # ------------------------------------------------------------- execution

    def execute(self, argv: list[str]) -> tuple[int, str, str]:
        """``(exit_code, stdout, stderr)`` of one CLI invocation, in-process and
        read-only, under the lock. A usage error's ``SystemExit`` is its exit code."""
        from . import cli

        out, err = io.StringIO(), io.StringIO()
        with self.lock, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code, _payload = cli.run(argv, self.environ, read_only=True)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else EXIT_USAGE
        return code, out.getvalue(), err.getvalue()

    def parse(self, argv: list[str], command: str) -> Namespace:
        """The CLI's own parse of ``argv``, or a 400 carrying the parser's words."""
        from . import cli

        err = io.StringIO()
        with self.lock, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            err
        ):
            try:
                return cli.build_parser().parse_args(argv)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else EXIT_USAGE
        if code == EXIT_OK:
            raise Refusal(
                403, f"help is not served: {REFUSED_QUERY_KEYS['help']}", command=command
            )
        raise UsageError(command, err.getvalue())

    def version_payload(self) -> dict:
        code, out, err = self.execute(["version", "--json"])
        if code != EXIT_OK:  # pragma: no cover - `kyo version` has no failure path
            raise KyoseiError(f"kyo serve could not read its own build: {err.strip()}")
        return json.loads(out)

    # -------------------------------------------------------------- guards

    def host_allowed(self, host: str | None) -> bool:
        if not host:
            return False
        host = host.strip().lower()
        match = _HOST_PORT.match(host)
        name = match.group("name") if match else host
        return name in LOOPBACK_HOST_NAMES or name in self.allow_hosts or host in self.allow_hosts

    def origin_allowed(self, origin: str) -> bool:
        return origin.strip().rstrip("/") in self.allow_origins

    # -------------------------------------------------------------- projects

    def projects(self) -> list[Project]:
        return [
            Project(project_name(root), root)
            for root in read_registry(registry_path(self.environ))
        ]

    def project(self, name: str) -> Project:
        matches = [p for p in self.projects() if p.name == name]
        if not matches:
            raise Refusal(
                404,
                f"no project named {name!r} is in this server's registry "
                f"({registry_path(self.environ)}); GET {API_PREFIX}/projects lists the "
                "names it serves, and a project is served once its root is added to that "
                "file",
            )
        if len(matches) > 1:
            roots = ", ".join(p.root for p in matches)
            raise Refusal(
                409,
                f"two registry entries are both named {name!r} ({roots}), so the name "
                f"cannot say which one is meant; remove one of them from "
                f"{registry_path(self.environ)}",
            )
        return matches[0]


class UsageError(Exception):
    """The CLI's parser refused the argv a request became (exit 64)."""

    def __init__(self, command: str, stderr: str) -> None:
        lines = [line for line in stderr.strip().splitlines() if line.strip()]
        message = lines[-1] if lines else "usage error"
        prefix = "kyo: "
        super().__init__(message)
        self.command = command
        self.envelope = {
            "ok": False,
            "command": command,
            # The parser's own last line, without the program-name prefix.
            "error": message[message.find(prefix) + len(prefix) :]
            if prefix in message
            else message,
            "usage": "\n".join(lines[:-1]),
            "exit_code": EXIT_USAGE,
        }


# ------------------------------------------------------------------- handler


class ServeHandler(BaseHTTPRequestHandler):
    server: KyoServer
    server_version = "kyo-serve"

    # --------------------------------------------------------------- methods

    def do_GET(self) -> None:  # noqa: N802 - http.server's naming
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - the stdlib's name
        self.server.log_stream.write(
            f"kyo serve: {self.address_string()} {format % args}\n"
        )

    # ------------------------------------------------------------- dispatch

    def _handle(self, method: str) -> None:
        headers: dict[str, str] = {}
        content_type = _JSON_TYPE
        try:
            self._guard(method)
            reply = self._route(method)
            if reply is None:  # a stream, already written
                return
            status, body, *more = reply
            if more:
                content_type, headers = more
        except Refusal as refusal:
            status, body = refusal.status, _json_bytes(refusal.body())
            headers = refusal.headers
        except UsageError as usage:
            status, body = 400, _json_bytes(usage.envelope)
        self._send(status, body, content_type, headers)

    def _guard(self, method: str) -> None:
        host = self.headers.get("Host")
        if not self.server.host_allowed(host):
            name = _HOST_PORT.match((host or "").strip().lower())
            bare = name.group("name") if name else host
            raise Refusal(
                403,
                f"kyo serve refused a request whose Host is {host!r}: it answers only "
                "the loopback names (127.0.0.1, localhost, [::1]) and names given with "
                "--allow-host, because any other Host may be a web page reaching "
                "loopback through DNS rebinding. If this is the name you reach the "
                f"server by (a Tailscale Serve address, say), start it with "
                f"--allow-host {bare}",
            )
        origin = self.headers.get("Origin")
        if origin is not None and not self.server.origin_allowed(origin):
            raise Refusal(
                403,
                f"kyo serve refused a request from the page at Origin {origin!r}: a "
                "browser page is answered only from an origin named with "
                "--allow-origin, so a site you visit cannot read your projects. If this "
                f"page is a viewer you trust, start the server with --allow-origin "
                f"{origin}",
            )
        if method == "POST" and _is_attachment_upload(self.path):
            from . import serve_attachments

            # KYO-715: a file's own type, and only one no HTML form can send.
            serve_attachments.declared_type(self.headers.get("Content-Type"))
        elif method == "POST":
            content_type = (self.headers.get("Content-Type") or "").split(";")[0]
            if content_type.strip().lower() != "application/json":
                raise Refusal(
                    415,
                    "kyo serve accepts a POST only with Content-Type: application/json, "
                    "which a cross-site HTML form cannot send; send the body as JSON with "
                    f"that header (this one was {content_type.strip() or 'not given'!r})",
                )

    def _route(self, method: str) -> tuple | None:
        split = urllib.parse.urlsplit(self.path)
        segments = [urllib.parse.unquote(s) for s in split.path.split("/")[1:]]
        if segments and segments[-1] == "" and len(segments) > 1:
            segments = segments[:-1]
        if segments[:1] != ["v1"]:
            raise _no_route(method, split.path)
        rest = segments[1:]
        if rest in (["version"], ["projects"]) or (
            len(rest) >= 4 and rest[0] == "projects" and rest[2] == "cmd"
        ):
            allowed: tuple[str, ...] = ("GET",)
        elif len(rest) >= 3 and rest[0] == "projects" and rest[2] in FILE_ROUTES:
            if rest[2] not in _PATH_ROUTES and len(rest) > 3:
                raise _no_route(method, split.path)
            allowed = FILE_ROUTES[rest[2]]
        elif len(rest) >= 3 and rest[0] == "projects" and rest[2] == ORCHESTRATOR_ROUTE:
            if rest[3:] != [ORCHESTRATOR_START]:
                from . import serve_orchestrator

                raise serve_orchestrator.not_served("/".join(rest[3:]))
            allowed = ("POST",)
        elif len(rest) >= 3 and rest[0] == "projects" and rest[2] == NOTES_ROUTE:
            if len(rest) == 3:
                allowed = ("GET", "POST")
            elif len(rest) == 4:
                allowed = ("GET",)
            elif len(rest) == 5 and rest[4] == _NOTE_TAGS_ROUTE:
                allowed = ("POST",)
            else:
                raise _no_route(method, split.path)
        elif (len(rest) == 3 and rest[0] == "projects" and rest[2] == TAGS_ROUTE) or (
            len(rest) == 5
            and rest[0] == "projects"
            and rest[2] == "sessions"
            and rest[4] == SESSION_MARKS_ROUTE
        ):
            allowed = ("GET",)
        elif (
            len(rest) == 5
            and rest[0] == "projects"
            and rest[2] == "sessions"
            and rest[4] in SESSION_SEND_ROUTES
        ):
            allowed = ("POST",)
        elif (
            len(rest) == 5
            and rest[0] == "projects"
            and rest[2] == "sessions"
            and rest[4] in SESSION_COMPOSER_ROUTES
        ):
            allowed = SESSION_COMPOSER_ROUTES[rest[4]]
        elif (
            len(rest) in (5, 6)
            and rest[0] == "projects"
            and rest[2] == "sessions"
            and rest[4] == SESSION_ATTACHMENTS_ROUTE
        ):
            allowed = ("POST",) if len(rest) == 5 else ("GET",)
        elif (
            len(rest) in (5, 6)
            and rest[0] == "projects"
            and rest[2] == "sessions"
            and rest[4] == SESSION_TERMINAL_ROUTE
        ):
            if len(rest) == 6 and rest[5] not in SESSION_TERMINAL_ACTIONS:
                raise _no_route(method, split.path)
            allowed = ("GET",) if len(rest) == 5 else ("POST",)
        elif len(rest) >= 3 and rest[0] == "projects" and rest[2] == "sessions":
            if not (
                len(rest) == 3
                or (len(rest) == 5 and rest[4] in SESSION_ROUTES)
                or (len(rest) == 7 and rest[4] == SESSION_IMAGE_ROUTE)
                or (len(rest) == 6 and rest[4] == SESSION_SUBAGENT_ROUTE)
            ):
                raise _no_route(method, split.path)
            allowed = ("GET",)
        else:
            raise _no_route(method, split.path)
        if method not in allowed:
            raise Refusal(
                405,
                f"{method} {split.path} is not served: this route answers "
                f"{', '.join(allowed)}",
                headers={"Allow": ", ".join(allowed)},
            )
        if rest == ["version"]:
            return 200, _json_bytes(self._version())
        if rest == ["projects"]:
            return 200, _json_bytes(self._projects())
        if rest[2] == "cmd":
            return self._command(rest[1], rest[3], rest[4:], split.query)
        if rest[2] == ORCHESTRATOR_ROUTE:
            return self._orchestrator_start(rest[1], split.query)
        if rest[2] == NOTES_ROUTE:
            return self._note_route(method, rest[1], rest[3:], split.query)
        if rest[2] == TAGS_ROUTE or (len(rest) == 5 and rest[4] == SESSION_MARKS_ROUTE):
            return self._note_read(rest[1], rest[3] if len(rest) == 5 else None, split.query)
        if rest[2] == "sessions":
            if len(rest) == 5 and rest[4] in SESSION_SEND_ROUTES:
                return self._session_send(rest[1], rest[3], rest[4], split.query)
            if len(rest) == 5 and rest[4] in SESSION_COMPOSER_ROUTES:
                return self._session_composer(method, rest[1], rest[3], rest[4], split.query)
            if len(rest) >= 5 and rest[4] == SESSION_ATTACHMENTS_ROUTE:
                return self._session_attachments(rest[1], rest[3], rest[5:], split.query)
            if len(rest) >= 5 and rest[4] == SESSION_TERMINAL_ROUTE:
                return self._session_terminal(rest[1], rest[3], rest[5:], split.query)
            return self._session_route(rest[1], rest[3:], split.query)
        return self._file_route(method, rest[1], rest[2], "/".join(rest[3:]))

    # --------------------------------------------------------- files and events

    def _file_route(self, method: str, name: str, route: str, relpath: str) -> tuple | None:
        """Every path is resolved, and every byte read or written, by
        :mod:`kyosei.serve_files`; this method only maps HTTP onto it."""
        from . import serve_files

        project = self.server.project(name)
        paths = serve_files.project_paths(project.root, self.server.environ)
        if route == "events":
            self._events(paths)
            return None
        if route == "docs":
            if relpath == "":
                raise Refusal(
                    404,
                    "name a document after docs/ (a path relative to the code root); GET "
                    f"{API_PREFIX}/projects/{{project}}/cmd/docs/check lists them",
                )
            found = serve_files.read_file(serve_files.document_target(paths, relpath), relpath)
            return 200, found.data, found.content_type, {"ETag": found.etag}
        if route == "ideas-move":
            document = self._json_body()
            source = serve_files.ideas_target(paths, _field(document, "from"))
            target = serve_files.ideas_target(paths, _field(document, "to"))
            moved = serve_files.move_path(source, target, document["from"], document["to"])
            return 200, _json_bytes(moved)
        if route == "ideas-mkdir":
            path = _field(self._json_body(), "path")
            status, made = serve_files.make_dir(serve_files.ideas_target(paths, path), path)
            return status, _json_bytes(made)
        if route == "scratchpad":
            target, shown = serve_files.scratchpad_target(paths), "the scratchpad"
        elif relpath == "":
            if method != "GET":
                raise Refusal(
                    405,
                    f"{method} of ideas/ itself is not served: name a file under it",
                    headers={"Allow": "GET"},
                )
            return 200, _json_bytes(serve_files.idea_tree(paths))
        else:
            target, shown = serve_files.ideas_target(paths, relpath), f"ideas/{relpath}"
        if method == "GET":
            found = serve_files.read_file(target, shown)
            return 200, found.data, found.content_type, {"ETag": found.etag}
        if method == "DELETE":
            deleted = serve_files.delete_path(
                target, shown, if_match=self.headers.get("If-Match")
            )
            return 200, _json_bytes(deleted)
        status, written = serve_files.put_file(
            target,
            shown,
            self._body(),
            if_match=self.headers.get("If-Match"),
            if_none_match=self.headers.get("If-None-Match"),
        )
        return status, _json_bytes(written), _JSON_TYPE, {"ETag": written["etag"]}

    def _body(self) -> bytes:
        from .serve_files import MAX_BODY_BYTES

        length = self.headers.get("Content-Length")
        if length is None or not length.strip().isdigit():
            raise Refusal(411, "send the body with a Content-Length header")
        size = int(length)
        if size > MAX_BODY_BYTES:
            raise Refusal(
                413,
                f"the body is {size} bytes, and kyo serve takes at most {MAX_BODY_BYTES}",
            )
        return self.rfile.read(size)

    def _json_body(self) -> dict:
        try:
            document = json.loads(self._body().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise Refusal(400, f"the body is not JSON: {exc}") from exc
        if not isinstance(document, dict):
            raise Refusal(400, "the body must be a JSON object")
        return document

    def _events(self, paths) -> None:
        """A ``text/event-stream`` that lasts until the client leaves or the
        server stops. It takes no lock: a fingerprint reads no store content."""
        from . import serve_events

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self._common_headers()
        self.end_headers()
        self.close_connection = True

        def write(chunk: bytes) -> None:
            self.wfile.write(chunk)
            self.wfile.flush()

        serve_events.stream(
            paths,
            write,
            self.server.stopping,
            poll_seconds=self.server.poll_seconds,
            keepalive_seconds=self.server.keepalive_seconds,
        )

    # ------------------------------------------------------------ session routes

    def _session_route(self, name: str, rest: list[str], query: str) -> tuple | None:
        """Every session route, opt-in checked first (:mod:`kyosei.serve_sessions`)."""
        from . import serve_sessions

        root = self.server.project(name).root
        project = serve_sessions.ProjectSessions(root, self.server.environ)
        cursor = _session_cursor(query)
        if not rest:
            return 200, _json_bytes(project.list_sessions())
        session_id, route, *tail = rest
        if route == SESSION_IMAGE_ROUTE:
            return self._session_image(project, session_id, *tail)
        if route == SESSION_SUBAGENT_ROUTE:
            return 200, _json_bytes(project.subagent(session_id, tail[0]))
        if route == "changes":
            return 200, _json_bytes(project.changes(session_id))
        if route == "turns":
            return 200, _json_bytes(
                project.turns(session_id, serve_sessions.parse_cursor(cursor))
            )
        if route == "screen":
            return 200, _json_bytes(project.screen(session_id))
        given = (
            serve_sessions.parse_cursor(cursor)
            if cursor
            else serve_sessions.parse_cursor(self.headers.get("Last-Event-ID"), refuse=False)
        )
        self._session_events(project, session_id, given)
        return None

    def _session_send(self, name: str, session_id: str, action: str, query: str) -> tuple:
        """One send route. Everything it decides is :mod:`kyosei.serve_send`'s."""
        from . import serve_send

        if query:
            # A token in a query string would be written to this server's request log.
            raise Refusal(
                400,
                "a send route takes no query string; send the token as "
                "`Authorization: Bearer <token>` and everything else in the JSON body",
            )
        document = serve_send.perform(
            self.server.environ,
            name,
            lambda: self.server.project(name).root,
            session_id,
            action,
            self.headers.get("Authorization"),
            self._body,
        )
        return 200, _json_bytes(document)

    def _orchestrator_start(self, name: str, query: str) -> tuple:
        """The start route. Everything it decides is :mod:`kyosei.serve_orchestrator`'s."""
        from . import serve_orchestrator

        if query:
            raise Refusal(
                400,
                "the start route takes no query string; send the token as "
                "`Authorization: Bearer <token>` and `{}` as the JSON body",
            )
        document = serve_orchestrator.perform(
            self.server.environ,
            name,
            lambda: self.server.project(name).root,
            self.headers.get("Authorization"),
            self._body,
        )
        return 200, _json_bytes(document)

    def _session_composer(
        self, method: str, name: str, session_id: str, route: str, query: str
    ) -> tuple:
        """The draft and held routes, each carrying a device token, so no query string."""
        from . import serve_drafts

        if query:
            raise Refusal(
                400,
                f"the {route} route takes no query string; send the token as "
                "`Authorization: Bearer <token>` and everything else in the body",
            )

        def root_of() -> str:
            return self.server.project(name).root

        authorization = self.headers.get("Authorization")
        if route in ("send-notes", "send-prompt"):
            from . import serve_notes

            sender = serve_notes.send if route == "send-notes" else serve_notes.send_prompt
            return 200, _json_bytes(
                sender(self.server, name, root_of, session_id, authorization, self._body)
            )
        if route == "draft":
            status, document = serve_drafts.perform(
                self.server.environ,
                root_of,
                session_id,
                method,
                authorization,
                if_match=self.headers.get("If-Match"),
                if_none_match=self.headers.get("If-None-Match"),
                read_body=self._body,
            )
            etag = document["etag"]
            return status, _json_bytes(document), _JSON_TYPE, {"ETag": etag} if etag else {}
        held = self.server.held_messages
        if route == "held-now":
            return 200, _json_bytes(
                held.send_now(root_of, session_id, authorization, self.headers.get("If-Match"))
            )
        if method == "POST":
            return 200, _json_bytes(
                held.hold(name, root_of, session_id, authorization, self._body)
            )
        if method == "GET":
            return 200, _json_bytes(held.read(root_of, session_id, authorization))
        return 200, _json_bytes(
            held.retract(root_of, session_id, authorization, self.headers.get("If-Match"))
        )

    def _session_attachments(
        self, name: str, session_id: str, rest: list[str], query: str
    ) -> tuple:
        """Save an attachment, or read one back. :mod:`kyosei.serve_attachments` decides."""
        from . import serve_attachments

        if query:
            raise Refusal(
                400,
                "the attachments route takes no query string; send the token as "
                "`Authorization: Bearer <token>`",
            )

        def root_of() -> str:
            return self.server.project(name).root

        environ, authorization = self.server.environ, self.headers.get("Authorization")
        if rest:
            media_type, data = serve_attachments.read(
                environ, name, root_of, session_id, authorization, rest[0]
            )
            return (
                200,
                data,
                media_type,
                {"Cache-Control": _IMAGE_CACHE, "X-Content-Type-Options": "nosniff"},
            )
        try:
            status, document = serve_attachments.upload(
                environ,
                name,
                root_of,
                session_id,
                authorization,
                self.headers.get("Content-Type"),
                self.headers.get("Content-Length"),
                self.rfile.read,
            )
        except Refusal as refusal:
            if serve_attachments.too_large(refusal):
                # The body was left unread, so what follows on the socket is not a request.
                self.close_connection = True
            raise
        return status, _json_bytes(document)

    def _session_terminal(
        self, name: str, session_id: str, rest: list[str], query: str
    ) -> tuple | None:
        """The terminal stream and its two POST routes. :mod:`kyosei.serve_terminal` decides."""
        from . import serve_terminal

        terminals = self.server.terminals
        authorization = self.headers.get("Authorization")
        if rest:
            if query:
                raise Refusal(
                    400,
                    f"the terminal {rest[0]} route takes no query string; send the token as "
                    "`Authorization: Bearer <token>` and everything else in the JSON body",
                )
            act = terminals.input if rest[0] == serve_terminal.INPUT else terminals.resize
            return 200, _json_bytes(act(name, session_id, authorization, self._body))
        connection = terminals.open(
            name,
            lambda: self.server.project(name).root,
            session_id,
            authorization,
            lambda: serve_terminal.size_from_query(_query_pairs(query)),
        )
        reason = "client-left"
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self._common_headers()
            self.end_headers()
            self.close_connection = True

            def write(chunk: bytes) -> None:
                self.wfile.write(chunk)
                self.wfile.flush()

            reason = terminals.serve(
                connection,
                write,
                self.server.stopping,
                self._client_gone,
                keepalive_seconds=self.server.keepalive_seconds,
            )
        except OSError:  # the client left before the headers were written
            pass
        finally:
            terminals.close(connection, reason)
        return None

    def _note_route(self, method: str, name: str, rest: list[str], query: str) -> tuple:
        """Every note route. Everything it decides is :mod:`kyosei.serve_notes`'s."""
        from . import serve_notes

        if query and not (method == "GET" and not rest):
            raise Refusal(
                400,
                "only GET of notes takes a query string; send the token as "
                "`Authorization: Bearer <token>` and everything else in the JSON body",
            )

        def root_of() -> str:
            return self.server.project(name).root

        server, authorization = self.server, self.headers.get("Authorization")
        if not rest and method == "POST":
            return 201, _json_bytes(
                serve_notes.create(server, root_of, authorization, self._body)
            )
        if not rest:
            return 200, _json_bytes(
                serve_notes.find(server, root_of, authorization, _query_pairs(query))
            )
        if len(rest) == 1:
            return 200, _json_bytes(serve_notes.read(server, root_of, authorization, rest[0]))
        return 200, _json_bytes(
            serve_notes.tag(server, root_of, authorization, rest[0], self._body)
        )

    def _note_read(self, name: str, session_id: str | None, query: str) -> tuple:
        """``tags`` and ``sessions/{id}/marks``: note reads, each carrying a device token."""
        from . import serve_notes

        if query:
            raise Refusal(
                400,
                "this route takes no query string; send the token as "
                "`Authorization: Bearer <token>`",
            )

        def root_of() -> str:
            return self.server.project(name).root

        server, authorization = self.server, self.headers.get("Authorization")
        if session_id is None:
            return 200, _json_bytes(serve_notes.tags(server, root_of, authorization))
        return 200, _json_bytes(serve_notes.marks(server, root_of, authorization, session_id))

    def _session_image(self, project, session_id: str, turn: str, index: str) -> tuple:
        if not (index.isascii() and index.isdigit()):
            raise _no_route("GET", f".../images/{turn}/{index}")
        media_type, data = project.image(session_id, turn, int(index))
        return (
            200,
            data,
            media_type,
            {"Cache-Control": _IMAGE_CACHE, "X-Content-Type-Options": "nosniff"},
        )

    def _session_events(self, project, session_id: str, given) -> None:
        """A ``text/event-stream`` on one session, from the sampler it shares with any other
        client of that session; a refusal comes before the status line."""
        from . import serve_sessions

        hub = self.server.session_hub
        sub = hub.subscribe(project, session_id)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self._common_headers()
            self.end_headers()
            self.close_connection = True

            def write(chunk: bytes) -> None:
                self.wfile.write(chunk)
                self.wfile.flush()

            serve_sessions.stream(
                project,
                session_id,
                sub,
                given,
                write,
                self.server.stopping,
                self._client_gone,
                keepalive_seconds=self.server.keepalive_seconds,
            )
        finally:
            hub.unsubscribe(sub)

    def _client_gone(self) -> bool:
        """Whether this stream's client has closed: it sends nothing after its request."""
        try:
            ready, _, _ = select.select([self.connection], [], [], 0)
            return bool(ready) and self.connection.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    # --------------------------------------------------------------- routes

    def _version(self) -> dict:
        from .schema import SCHEMA_VERSION

        now = self.server.version_payload()
        started = self.server.started_version
        stale = (now.get("version"), now.get("commit")) != (
            started.get("version"),
            started.get("commit"),
        )
        document = {
            "ok": True,
            "kyosei": started,
            "schema_version": SCHEMA_VERSION,
            "pid": self.server.pid,
            "started_at": self.server.started_at,
            "stale": stale,
        }
        if stale:
            document["stale_detail"] = (
                f"this server started as {started.get('line')}, and the build installed "
                f"on disk is now {now.get('line')}; stop kyo serve and start it again to "
                "serve the installed build"
            )
        return document

    def _projects(self) -> dict:
        path = registry_path(self.server.environ)
        listed = []
        for project in self.server.projects():
            code, out, err = self.server.execute(
                ["where", "--root", project.root, "--json"]
            )
            where = _json_or_none(out) or {}
            listed.append(
                {
                    "name": project.name,
                    "root": project.root,
                    "store_root": where.get("store_root"),
                    "prefix": where.get("project_prefix"),
                    "can_open": bool(where.get("can_open")),
                    "problem": where.get("store_problem")
                    if code == EXIT_OK
                    else (where.get("error") or err.strip() or f"kyo where exited {code}"),
                }
            )
        return {"ok": True, "registry": str(path), "projects": listed}

    def _command(
        self, name: str, command: str, positionals: list[str], query: str
    ) -> tuple[int, bytes]:
        project = self.server.project(name)
        served = SERVED_COMMANDS.get(command)
        if served is None:
            why = NOT_SERVED.get(command)
            if why is None:
                raise Refusal(
                    404,
                    f"there is no kyo command {command!r}; the commands kyo serve runs "
                    f"are {', '.join(sorted(SERVED_COMMANDS))}",
                    command=command,
                )
            raise Refusal(
                403,
                f"kyo serve does not run `kyo {command}`: {why}. Run it in a terminal on "
                "the machine that holds the store",
                command=command,
            )
        # `--root` goes BEFORE the command, so a query key that argparse reads as an
        # abbreviation of it (`?roo=`) comes after and wins -- and is then caught by
        # `_check_namespace` rather than silently overridden by the server's own.
        argv = ["--root", project.root, command]
        for value in positionals:
            if not value or value.startswith("-"):
                raise Refusal(
                    400,
                    f"the path segment {value!r} is not a positional argument: flags go "
                    "in the query string (?flag=value, or ?flag for a switch)",
                    command=command,
                )
            argv.append(value)
        for key, value, bare in _query_pairs(query):
            if key in REFUSED_QUERY_KEYS:
                raise Refusal(
                    403,
                    f"?{key} is refused: {REFUSED_QUERY_KEYS[key]}; drop it from the query",
                    command=command,
                )
            if not _QUERY_KEY.match(key):
                raise Refusal(
                    400,
                    f"?{key} is not a flag name: a query key is a long flag without its "
                    "dashes, such as ?status=open",
                    command=command,
                )
            argv.append(f"--{key}" if bare else f"--{key}={value}")
        argv.append("--json")
        args = self.server.parse(argv, command)
        _check_namespace(command, served, args, project.root)
        code, out, err = self.server.execute(argv)
        if code == EXIT_OK:
            status = 200
        elif code == EXIT_USAGE:
            status = 400
        else:
            status = 422
        body = out.encode("utf-8")
        if not body.strip():
            # A failure the CLI printed no envelope for: said here, never an empty body.
            body = _json_bytes(
                {
                    "ok": False,
                    "command": command,
                    "error": err.strip() or f"kyo {command} exited {code}",
                    "exit_code": code,
                }
            )
        return status, body

    # ---------------------------------------------------------------- output

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str = _JSON_TYPE,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self._common_headers()
        self.end_headers()
        self.wfile.write(body)

    def _common_headers(self) -> None:
        self.send_header(VERSION_HEADER, version_header(self.server.started_version))
        origin = self.headers.get("Origin")
        if origin is not None and self.server.origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Expose-Headers", "ETag")
            self.send_header("Vary", "Origin")


def _check_namespace(command: str, served: Served, args: Namespace, root: str) -> None:
    """Refuse a parsed invocation that reaches past what ``served`` allows."""
    if args.root != root or getattr(args, "actor", None) is not None:
        raise Refusal(
            403,
            "the query reached --root or --actor (an abbreviation of either counts); "
            "the server sets --root from the {project} in the path, so drop it",
            command=command,
        )
    if served.actions is not None:
        action = getattr(args, "action", None)
        if action not in served.actions:
            raise Refusal(
                403,
                f"kyo serve runs `kyo {command}` only as "
                f"{', '.join(sorted(served.actions))}, not {action!r}: the others write. "
                "Run it in a terminal on the machine that holds the store",
                command=command,
            )
    for dest in served.refused:
        if getattr(args, dest, None) not in (None, False, [], ""):
            flag = "--" + dest.replace("_", "-")
            raise Refusal(
                403,
                f"kyo serve runs `kyo {command}` without {flag}, which writes or reads "
                "stdin; drop it from the query, or run the command in a terminal on the "
                "machine that holds the store",
                command=command,
            )


def _session_cursor(query: str) -> str | None:
    """The ``cursor`` a session route was given; any other query key is refused."""
    cursor = None
    for key, value, _bare in _query_pairs(query):
        if key != _SESSION_QUERY_KEY:
            raise Refusal(
                400,
                f"?{key} is not a query a session route takes; it takes ?{_SESSION_QUERY_KEY}, "
                "the cursor of an earlier response",
            )
        cursor = value
    return cursor


def _query_pairs(query: str) -> list[tuple[str, str, bool]]:
    """``(key, value, bare)`` for each query item, in order; a repeated key repeats."""
    pairs = []
    for item in query.split("&") if query else []:
        if not item:
            continue
        key, eq, value = item.partition("=")
        pairs.append(
            (urllib.parse.unquote_plus(key), urllib.parse.unquote_plus(value), not eq)
        )
    return pairs


def _no_route(method: str, path: str) -> Refusal:
    project = f"{API_PREFIX}/projects/{{project}}"
    return Refusal(
        404,
        f"{method} {path} is not a route; kyo serve answers GET {API_PREFIX}/version, GET "
        f"{API_PREFIX}/projects, GET {project}/cmd/{{command}}, GET {project}/ideas/, "
        f"GET/PUT/DELETE {project}/ideas/{{relpath}}, POST {project}/ideas-move and "
        f"{project}/ideas-mkdir, GET/PUT {project}/scratchpad, GET {project}/docs/{{relpath}}, "
        f"GET {project}/events, GET {project}/sessions and GET "
        f"{project}/sessions/{{id}}/turns, .../events, .../screen, .../changes, "
        f".../images/{{turn}}/{{index}} or .../subagents/{{agent}}, POST "
        f"{project}/sessions/{{id}}/send, .../slash, .../interrupt, .../send-notes or "
        f".../send-prompt or .../attachments, GET .../attachments/{{attachment}}, GET/POST "
        f"{project}/notes, GET {project}/notes/{{id}}, POST {project}/notes/{{id}}/tags, "
        f"GET {project}/tags, GET {project}/sessions/{{id}}/marks, GET "
        f"{project}/sessions/{{id}}/terminal, POST .../terminal/input or .../terminal/resize, "
        f"and POST {project}/orchestrator/start",
    )


def _field(document: dict, key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str):
        raise Refusal(400, f'the JSON body needs "{key}": a path relative to ideas/')
    return value


def _is_attachment_upload(path: str) -> bool:
    """Whether a request path is ``/v1/projects/{p}/sessions/{id}/attachments``."""
    parts = [urllib.parse.unquote(s) for s in urllib.parse.urlsplit(path).path.split("/")[1:]]
    if len(parts) > 1 and parts[-1] == "":
        parts = parts[:-1]
    return (
        len(parts) == 6
        and parts[0] == "v1"
        and parts[1] == "projects"
        and parts[3] == "sessions"
        and parts[5] == SESSION_ATTACHMENTS_ROUTE
    )


def _json_bytes(document: dict) -> bytes:
    from .cli import render_json

    return (render_json(document, True) + "\n").encode("utf-8")


def _json_or_none(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


# ------------------------------------------------------------------- command


def cmd_serve(ctx, args: Namespace):
    """``kyo serve [--port N] [--bind ADDR] [--allow-host H…] [--allow-origin O…]``.

    Foreground: one line per request on stderr, and a clean stop on SIGINT or SIGTERM.
    ``--install``/``--uninstall``/``--status``/``--restart`` are :mod:`serve_install`'s.
    """
    from . import serve_install
    from .commands import Output

    mode = serve_install.requested_mode(args)
    if mode is not None:
        return serve_install.run(ctx, args, mode)
    serve_install.refuse_if_held(check_bind(args.bind), args.port)
    server = KyoServer(
        args.bind,
        args.port,
        allow_hosts=tuple(args.allow_host or ()),
        allow_origins=tuple(args.allow_origin or ()),
        environ=ctx.environ,
    )
    host, port = server.server_address[:2]
    shown = f"[{host}]" if ":" in host else host
    print(
        f"kyo serve: serving http://{shown}:{port}{API_PREFIX}/ "
        f"({server.started_version.get('line')}); Ctrl-C stops it",
        file=server.log_stream,
        flush=True,
    )
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(
        signal.SIGTERM,
        lambda *_: threading.Thread(target=server.shutdown, daemon=True).start(),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous)
        server.server_close()
    return Output(
        [f"kyo serve: stopped (was http://{shown}:{port})"],
        {"ok": True, "command": "serve", "bind": host, "port": port},
    )
