"""``kyo serve --install|--uninstall|--status|--restart`` — kyo serve at login (KYO-457).

So that a viewer such as the desktop app finds ``kyo serve`` running without a
terminal. On macOS the agent is a
per-user launchd agent, ``~/Library/LaunchAgents/dev.kyosei.serve.plist``, bootstrapped
into ``gui/<uid>``; on Linux it is a systemd user unit,
``~/.config/systemd/user/kyosei-serve.service``, started with ``systemctl --user enable
--now``. Anything else is refused by name: the frame names launchd and systemd only.

ONE SEAM, NOT TWO. ``kyo controller --install`` (KYO-458) already keeps a gui/<uid>
agent, and its measured premise (KYO-458.m1) is that such an agent runs in the login
session. The launchd acts here are that module's — :data:`cmd_controller.launchctl_runner`,
``load_agent``, ``unload_agent`` — looked up at call time so the suite's replacement
reaches them, and the agent file is written and removed there too: only
``serve_files`` may write among the ``serve*`` modules (KYO-456). systemd has its own
runner, :data:`systemctl_runner`, replaced in the suite the same way.

LAW 11. ``--install`` and ``--uninstall`` print what they would do; ``--apply`` does
it. ``--status`` only reads. ``--restart`` restarts an agent that is already there.

WHAT THE AGENT RUNS. The absolute path of the ``kyo`` that ran ``--install`` (the uv
tool's ``~/.local/bin/kyo``, not the file it links to), so an upgrade takes effect at
the next restart; with ``KYOSEI_HOME`` when it is set and the installing shell's
``PATH``. The server runs ``git`` and nothing else, so no login shell is needed.

WHAT ANSWERS ON THE PORT (:func:`probe`). ``GET /v1/version`` on the loopback port:
nothing listening, another ``kyo serve`` (its pid, build and ``stale``), or something
else. ``kyo serve`` itself asks the same question before it binds (c3).
"""

from __future__ import annotations

import http.client
import json
import os
import plistlib
import re
import shlex
import shutil
import socket
import subprocess
import sys
from argparse import Namespace
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .errors import KyoseiError

#: The launchd label. Deliberately not ``dev.kyosei.server``, the label an earlier
#: desktop app used for a server of its own, so the two can never collide.
LAUNCH_AGENT_LABEL = "dev.kyosei.serve"

#: The systemd user unit's name.
SYSTEMD_UNIT = "kyosei-serve.service"

#: The environment the agent carries from the installing shell, when set.
CARRIED_ENV = ("KYOSEI_HOME", "PATH")

#: How long the port probe waits for a connection, and then for an answer.
PROBE_TIMEOUT_SECONDS = 2.0

#: ``(argv) -> (returncode, stdout, stderr)``: how ``systemctl --user`` is run, with
#: ``argv`` the words after ``--user``. Replaced in the suite, which must never reach
#: a real systemd.
SystemctlRunner = Callable[[list[str]], tuple[int, str, str]]


def _run_systemctl(argv: list[str]) -> tuple[int, str, str]:
    done = subprocess.run(  # noqa: S603 - a fixed program, argv built here
        ["systemctl", "--user", *argv], capture_output=True, text=True, timeout=30, check=False
    )
    return done.returncode, done.stdout, done.stderr


#: The runner the systemd acts use; the suite replaces it.
systemctl_runner: SystemctlRunner = _run_systemctl


def platform_manager() -> str | None:
    """``launchd`` on macOS, ``systemd`` on Linux, ``None`` anywhere else."""
    if sys.platform == "darwin":
        return "launchd"
    if sys.platform.startswith("linux"):
        return "systemd"
    return None


# ------------------------------------------------------------------ the probe


@dataclass(frozen=True)
class PortAnswer:
    """What answers on one loopback port."""

    host: str
    port: int
    #: ``nothing``, ``kyo-serve`` or ``other``.
    state: str
    pid: int | None = None
    build: str | None = None
    stale: bool | None = None
    stale_detail: str | None = None
    detail: str = ""

    @property
    def shown(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"

    def to_json(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "state": self.state,
            "pid": self.pid,
            "build": self.build,
            "stale": self.stale,
            "stale_detail": self.stale_detail,
            "detail": self.detail,
        }


def probe(host: str, port: int, *, timeout: float | None = None) -> PortAnswer:
    """Ask ``host:port`` for ``/v1/version``; never raises."""
    timeout = PROBE_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except ConnectionRefusedError:
        return PortAnswer(host, port, "nothing")
    except OSError as exc:
        return PortAnswer(host, port, "other", detail=f"connecting failed: {exc}")
    host_header = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.putrequest("GET", "/v1/version", skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", host_header)
        conn.endheaders()
        response = conn.getresponse()
        body = response.read()
        version_header = response.headers.get("Kyosei-Version")
    except (OSError, http.client.HTTPException) as exc:
        return PortAnswer(host, port, "other", detail=f"it did not answer HTTP: {exc}")
    finally:
        conn.close()
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        document = None
    if (
        response.status != 200
        or not version_header
        or not isinstance(document, dict)
        or not isinstance(document.get("pid"), int)
        or not isinstance(document.get("kyosei"), dict)
    ):
        return PortAnswer(
            host,
            port,
            "other",
            detail=f"GET /v1/version answered {response.status}, not as kyo serve does",
        )
    return PortAnswer(
        host,
        port,
        "kyo-serve",
        pid=document["pid"],
        build=document["kyosei"].get("line") or version_header,
        stale=bool(document.get("stale")),
        stale_detail=document.get("stale_detail"),
    )


def refuse_if_held(host: str, port: int) -> None:
    """``kyo serve``'s start check (c3): refuse when anything answers on its port."""
    if port == 0:
        return
    answer = probe(host, port)
    if answer.state == "kyo-serve":
        raise KyoseiError(
            f"another kyo serve already answers on http://{answer.shown}/v1/ (pid "
            f"{answer.pid}, {answer.build}); stop it (`kill {answer.pid}`; when it is "
            "the installed agent, `kyo serve --status` says so and `kyo serve --restart` "
            "restarts it), or start this one on another port with --port N"
        )
    if answer.state == "other":
        raise KyoseiError(
            f"something other than kyo serve holds {answer.shown} ({answer.detail}); "
            "start this one on another port with --port N"
        )


# ------------------------------------------------------------------ the agent


def kyo_program(environ: dict[str, str]) -> list[str]:
    """The command that runs this kyo: the ``kyo`` on disk that ran ``--install``, by
    its own absolute path (a symlink is kept, so an upgrade behind it takes effect);
    ``<python> -m kyosei`` when this process was not started as ``kyo``."""
    argv0 = sys.argv[0] if sys.argv else ""
    if Path(argv0).name in ("kyo", "kyosei"):
        found = argv0 if os.sep in argv0 else shutil.which(argv0, path=environ.get("PATH"))
        if found and os.access(found, os.X_OK):
            return [os.path.abspath(found)]
    return [os.path.abspath(sys.executable), "-m", "kyosei"]


def serve_argv(environ: dict[str, str], args: Namespace) -> list[str]:
    from . import serve

    argv = [*kyo_program(environ), "serve", "--port", str(args.port)]
    if args.bind != serve.DEFAULT_BIND:
        argv += ["--bind", args.bind]
    for host in args.allow_host or ():
        argv += ["--allow-host", host]
    for origin in args.allow_origin or ():
        argv += ["--allow-origin", origin]
    return argv


def agent_environment(environ: dict[str, str]) -> dict[str, str]:
    return {name: environ[name] for name in CARRIED_ENV if (environ.get(name) or "").strip()}


def _home(environ: dict[str, str]) -> Path:
    home = (environ.get("HOME") or "").strip()
    return Path(home) if home else Path.home()


def agent_path(manager: str, environ: dict[str, str]) -> Path:
    if manager == "launchd":
        return _home(environ) / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"
    return _home(environ) / ".config" / "systemd" / "user" / SYSTEMD_UNIT


def log_path(environ: dict[str, str]) -> Path:
    """Where launchd puts the server's stderr (its one line per request)."""
    return _home(environ) / "Library" / "Logs" / "kyosei-serve.log"


def launch_agent_plist(argv: list[str], env: dict[str, str], log: Path) -> bytes:
    return plistlib.dumps(
        {
            "Label": LAUNCH_AGENT_LABEL,
            "ProgramArguments": argv,
            "EnvironmentVariables": env,
            "RunAtLoad": True,
            "KeepAlive": True,
            "LimitLoadToSessionType": "Aqua",
            "StandardErrorPath": str(log),
            "StandardOutPath": str(log),
        }
    )


def _systemd_quote(word: str) -> str:
    """One word of a unit file line: ``%`` and ``$`` escaped, quoted when needed."""
    word = word.replace("%", "%%").replace("$", "$$")
    if word and not re.search(r'[\s"\'\\;]', word):
        return word
    return '"' + word.replace("\\", "\\\\").replace('"', '\\"') + '"'


def systemd_unit(argv: list[str], env: dict[str, str]) -> bytes:
    lines = [
        "[Unit]",
        f"Description=kyo serve: this machine's Kyosei projects over loopback HTTP "
        f"({LAUNCH_AGENT_LABEL})",
        "",
        "[Service]",
        "ExecStart=" + " ".join(_systemd_quote(word) for word in argv),
        *(f"Environment={_systemd_quote(f'{k}={v}')}" for k, v in env.items()),
        "Restart=on-failure",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _launchd_service() -> str:
    return f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}"


def _unsupported(action: str) -> KyoseiError:
    return KyoseiError(
        f"kyo serve --{action} knows launchd (macOS) and systemd (Linux) only, and this "
        f"is {sys.platform}; run `kyo serve` in the foreground here instead"
    )


def _failed(what: str, code: int, out: str, err: str) -> str:
    return f"`{what}` failed ({(err or out).strip() or f'exit {code}'})"


@dataclass
class Agent:
    """The installed agent's facts, as ``--status`` reports them."""

    manager: str | None
    path: Path | None
    service: str | None
    installed: bool = False
    loaded: bool = False
    pid: int | None = None
    program: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "manager": self.manager,
            "path": str(self.path) if self.path else None,
            "service": self.service,
            "installed": self.installed,
            "loaded": self.loaded,
            "pid": self.pid,
            "program": self.program,
        }


def read_agent(environ: dict[str, str]) -> Agent:
    from . import cmd_controller

    manager = platform_manager()
    if manager is None:
        return Agent(None, None, None)
    path = agent_path(manager, environ)
    if manager == "launchd":
        agent = Agent(manager, path, _launchd_service(), installed=path.is_file())
        if agent.installed:
            try:
                agent.program = list(plistlib.loads(path.read_bytes())["ProgramArguments"])
            except Exception:  # noqa: BLE001 - an unreadable plist is reported, not raised
                agent.program = []
        code, out, _ = cmd_controller.launchctl_runner(["print", agent.service])
        agent.loaded = code == 0
        found = re.search(r"^\s*pid = (\d+)", out, re.M) if agent.loaded else None
        agent.pid = int(found.group(1)) if found else None
        return agent
    agent = Agent(manager, path, SYSTEMD_UNIT, installed=path.is_file())
    if agent.installed:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("ExecStart="):
                try:
                    agent.program = shlex.split(line[len("ExecStart="):])
                except ValueError:
                    agent.program = []
    code, out, _ = systemctl_runner(["is-active", SYSTEMD_UNIT])
    agent.loaded = code == 0 and out.strip() == "active"
    if agent.loaded:
        _, out, _ = systemctl_runner(["show", "-p", "MainPID", "--value", SYSTEMD_UNIT])
        agent.pid = int(out.strip()) if out.strip().isdigit() and out.strip() != "0" else None
    return agent


def install(environ: dict[str, str], args: Namespace, *, apply: bool) -> tuple[list[str], dict]:
    from . import cmd_controller, serve

    manager = platform_manager()
    if manager is None:
        raise _unsupported("install")
    serve.check_bind(args.bind)
    argv = serve_argv(environ, args)
    env = agent_environment(environ)
    path = agent_path(manager, environ)
    if manager == "launchd":
        content = launch_agent_plist(argv, env, log_path(environ))
        service = _launchd_service()
        domain = service.rsplit("/", 1)[0]
        steps = [
            f"write {path}",
            f"launchctl bootout {service}   (only if it is already loaded)",
            f"launchctl bootstrap {domain} {path}",
        ]
    else:
        content = systemd_unit(argv, env)
        service = SYSTEMD_UNIT
        steps = [
            f"write {path}",
            "systemctl --user daemon-reload",
            f"systemctl --user enable --now {SYSTEMD_UNIT}",
            f"systemctl --user restart {SYSTEMD_UNIT}   (only if it was already running)",
        ]
    data = {
        "manager": manager,
        "path": str(path),
        "service": service,
        "program": argv,
        "environment": env,
        "steps": steps,
        "applied": apply,
    }
    if not apply:
        return [
            f"kyo serve --install: dry run; `kyo serve --install --apply` would write {path}:",
            "",
            *content.decode("utf-8").rstrip("\n").splitlines(),
            "",
            "and then run:",
            *(f"  {step}" for step in steps[1:]),
        ], {**data, "content": content.decode("utf-8")}
    cmd_controller.write_agent_file(path, content)
    if manager == "launchd":
        code, out, err = cmd_controller.load_agent(path)
        if code != 0:
            failed = _failed(f"launchctl bootstrap {domain} {path}", code, out, err)
            raise KyoseiError(
                f"wrote {path}, and {failed} twice; {cmd_controller.not_loaded_remedy(path)} "
                "(or run `kyo serve --install --apply` again there)"
            )
    else:
        was_active = systemctl_runner(["is-active", SYSTEMD_UNIT])[0] == 0
        for words in (
            ["daemon-reload"],
            ["enable", "--now", SYSTEMD_UNIT],
            *([["restart", SYSTEMD_UNIT]] if was_active else []),
        ):
            code, out, err = systemctl_runner(words)
            if code != 0:
                failed = _failed("systemctl --user " + " ".join(words), code, out, err)
                raise KyoseiError(
                    f"wrote {path}, and {failed}; "
                    "check that this login has a systemd user instance (`systemctl --user "
                    "status`), then run `kyo serve --install --apply` again"
                )
    return [
        f"kyo serve --install: wrote {path} and started {service}; it runs "
        f"`{shlex.join(argv)}` from login (`kyo serve --status` shows it)"
    ], data


def uninstall(environ: dict[str, str], *, apply: bool) -> tuple[list[str], dict]:
    from . import cmd_controller

    manager = platform_manager()
    if manager is None:
        raise _unsupported("uninstall")
    path = agent_path(manager, environ)
    if manager == "launchd":
        service = _launchd_service()
        steps = [f"launchctl bootout {service}   (only if it is loaded)", f"remove {path}"]
    else:
        service = SYSTEMD_UNIT
        steps = [
            f"systemctl --user disable --now {SYSTEMD_UNIT}",
            f"remove {path}",
            "systemctl --user daemon-reload",
        ]
    data = {"manager": manager, "path": str(path), "service": service, "steps": steps,
            "applied": apply}  # fmt: skip
    if not apply:
        return [
            "kyo serve --uninstall: dry run; `kyo serve --uninstall --apply` would:",
            *(f"  {step}" for step in steps),
            "Stopping the agent stops its kyo serve, and every viewer reading from it.",
        ], data
    if manager == "launchd":
        was_loaded, code, out, err = cmd_controller.unload_agent(service)
        what = f"launchctl bootout {service}"
    else:
        was_loaded = path.is_file() or systemctl_runner(["is-active", SYSTEMD_UNIT])[0] == 0
        code, out, err = (
            systemctl_runner(["disable", "--now", SYSTEMD_UNIT]) if was_loaded else (0, "", "")
        )
        what = f"systemctl --user disable --now {SYSTEMD_UNIT}"
    if code != 0:
        raise KyoseiError(
            f"{_failed(what, code, out, err)}; {path} is left in place — run "
            "`kyo serve --uninstall --apply` again"
            + (" from a terminal in the desktop session" if manager == "launchd" else "")
        )
    removed = cmd_controller.remove_agent_file(path)
    if manager == "systemd":
        systemctl_runner(["daemon-reload"])
    stopped = f"stopped {service}" if was_loaded else f"{service} was not loaded"
    file_ = f"removed {path}" if removed else f"{path} was not there"
    return [f"kyo serve --uninstall: {stopped}; {file_}"], {
        **data,
        "was_loaded": was_loaded,
        "removed": removed,
    }


def _port_of(program: list[str]) -> int | None:
    for index, word in enumerate(program):
        if word == "--port" and index + 1 < len(program) and program[index + 1].isdigit():
            return int(program[index + 1])
        if word.startswith("--port=") and word[7:].isdigit():
            return int(word[7:])
    return None


def status(environ: dict[str, str], args: Namespace) -> tuple[list[str], dict]:
    from . import serve

    host = serve.check_bind(args.bind)
    agent = read_agent(environ)
    port = args.port
    agent_port = _port_of(agent.program)
    if port == serve.DEFAULT_PORT and agent_port:
        port = agent_port  # the installed agent's port, unless one was asked for
    answer = probe(host, port)
    lines: list[str] = []
    if agent.manager is None:
        lines.append(f"agent: none -- {sys.platform} has neither launchd nor systemd")
    elif agent.installed:
        state = (
            f"loaded{f', pid {agent.pid}' if agent.pid else ''}" if agent.loaded else "not loaded"
        )
        lines.append(
            f"agent: {agent.service} installed at {agent.path} ({state}); runs "
            f"`{shlex.join(agent.program)}`"
        )
    else:
        lines.append(
            f"agent: none installed ({agent.path} is not there; `kyo serve --install "
            "--apply` installs it)"
            + (f"; but {agent.service} is loaded" if agent.loaded else "")
        )
    if answer.state == "nothing":
        lines.append(f"port {answer.shown}: nothing answers")
    elif answer.state == "other":
        lines.append(
            f"port {answer.shown}: something other than kyo serve holds it ({answer.detail})"
        )
    else:
        whose = ""
        if agent.pid is not None:
            whose = " -- the agent" if agent.pid == answer.pid else " -- not the agent's pid"
        lines.append(f"port {answer.shown}: kyo serve pid {answer.pid}, {answer.build}{whose}")
        if answer.stale:
            restart = (
                "`kyo serve --restart` restarts it"
                if agent.loaded
                else "stop it and start it again"
            )
            detail = answer.stale_detail or "the installed build differs"
            lines.append(f"stale: {detail}; {restart}")
        else:
            lines.append("stale: no -- it serves the build installed on disk")
    return lines, {
        "agent": agent.to_json(),
        "answering": answer.to_json(),
        "stale": answer.stale,
    }


def restart(environ: dict[str, str]) -> tuple[list[str], dict]:
    from . import cmd_controller

    agent = read_agent(environ)
    if agent.manager is None:
        raise _unsupported("restart")
    if not agent.loaded:
        raise KyoseiError(
            f"no {agent.service} agent is loaded, so there is nothing to restart; "
            "`kyo serve --install --apply` installs and starts it (a kyo serve run in the "
            "foreground restarts by stopping it and running it again)"
        )
    if agent.manager == "launchd":
        words = ["kickstart", "-k", agent.service]
        code, out, err = cmd_controller.launchctl_runner(words)
        what = "launchctl " + " ".join(words)
    else:
        words = ["restart", SYSTEMD_UNIT]
        code, out, err = systemctl_runner(words)
        what = "systemctl --user " + " ".join(words)
    if code != 0:
        raise KyoseiError(
            f"{_failed(what, code, out, err)}; `kyo serve --status` shows what is loaded, "
            "and `kyo serve --install --apply` reloads the agent"
        )
    return [f"kyo serve --restart: restarted {agent.service} (`{what}`)"], {
        "manager": agent.manager,
        "service": agent.service,
        "ran": what,
    }


# -------------------------------------------------------------------- command

MODES = ("install", "uninstall", "status", "restart")


def requested_mode(args: Namespace) -> str | None:
    """The one mode ``args`` asks for, or ``None`` for the foreground server."""
    modes = [mode for mode in MODES if getattr(args, mode, False)]
    if len(modes) > 1:
        raise KyoseiError(
            "kyo serve takes one of --install, --uninstall, --status or --restart at a "
            f"time, not {' and '.join('--' + m for m in modes)}; run them one after another"
        )
    mode = modes[0] if modes else None
    if getattr(args, "apply", False) and mode not in ("install", "uninstall"):
        raise KyoseiError(
            "--apply goes with --install or --uninstall and nothing else; drop it, or add "
            "one of them (`kyo serve --install --apply`)"
        )
    return mode


def run(ctx, args: Namespace, mode: str):
    from .commands import Output

    environ = ctx.environ
    if mode == "install":
        lines, data = install(environ, args, apply=args.apply)
    elif mode == "uninstall":
        lines, data = uninstall(environ, apply=args.apply)
    elif mode == "status":
        lines, data = status(environ, args)
    else:
        lines, data = restart(environ)
    return Output(lines, {"ok": True, "command": "serve", "action": mode, **data})
