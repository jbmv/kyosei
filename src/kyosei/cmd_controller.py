"""``kyo controller`` — detached claude Remote Control sessions on this machine (KYO-458).

The desktop app's Start controller button (KYO-383,
``apps/desktop/src/main/controller.ts``) moved out of the app and into the CLI, so that
another machine — or the app — starts a controller on the dev box with ``ssh host kyo
controller start <project>`` and the SSH connection can close at once.

WHERE THE SESSION RUNS, AND WHY THERE (KYO-458.m1). A process
started directly in an SSH session cannot use the login keychain
(``SecKeychainCopySettings: User interaction is not allowed``), and claude's
credentials live there. A session CREATED over SSH inside an already-running tmux
server gets the keychain anyway, because the server spawns it and the server was
started in the GUI login session. So every session lives on ONE dedicated server,
``tmux -L kyosei``, which a per-user LaunchAgent in ``gui/<uid>`` keeps running from
login (``--install``), and ``start`` refuses over SSH when that server is not running
rather than starting one in the SSH context — a server started there would carry the
SSH context's missing keychain into every session it ever holds.

WHY A LOGIN SHELL. The same measurement: a session created over SSH, and one in a
server launchd started, has a bare ``/usr/bin:/bin:/usr/sbin:/sbin`` PATH, on which
neither ``claude`` nor ``kyo`` resolves. The program is therefore ``zsh -lc 'exec
<command>'``: the login shell builds the user's PATH, and
``exec`` makes claude the pane's own process, so claude exiting ends the pane and
(with ``remain-on-exit off``) the session — "is claude alive" and "does the session
exist with a live pane" are one question.

THE SOCKET IS NAMED ON EVERY CALL, AND THE ENVIRONMENT THAT WOULD MOVE IT IS DROPPED.
``TMUX`` beats ``TMUX_TMPDIR`` and ``TMUX_TMPDIR`` decides where ``-L kyosei`` lives;
a command run from inside the app's own pane carries both.
Every call here passes ``-L kyosei`` (or ``-S`` under :data:`SOCKET_ENV`) and runs
with ``TMUX``, ``TMUX_PANE`` and ``TMUX_TMPDIR`` removed, so the socket is the one a
launchd-started server, which has none of them, also resolves.

WHAT RUNS (KYO-462). ``<command>`` is ``controller_command`` from
``$KYOSEI_HOME/settings.yml`` (:mod:`kyosei.home_settings`), with ``{prefix}`` replaced
by the project's prefix, shell-quoted into the login shell's one command string; with
no file or no key it is ``claude`` alone. A machine setting, never a project's config
(KYO-335). The command a session was started with is kept on the session as the tmux
user option ``@kyosei_command``, which is how ``list`` shows it.

THE NAME. The session is named after the project's prefix (``KYO``), exactly as the
app's launcher names its own; with ``--remote-control {prefix}`` in the command, that
is also the name claude.ai lists it under. Targets are written ``=<name>`` so tmux
matches exactly rather than by prefix.

ATTACH (KYO-474). ``kyo controller attach <project>`` is the one verb that knows both
rules above -- the socket, and the exact ``=<prefix>`` target -- so a person on another
machine types ``ssh -t HOST "zsh -lc 'kyo controller attach KYO'"`` and the desktop
app's terminal tab runs the same words. It replaces itself with ``tmux <socket args>
attach -t =<prefix>`` in the same scrubbed environment ``start`` uses. Attaching over
SSH does not change claude's credentials: the session was spawned by the
launchd-owned server, and the SSH client only views it (KYO-458.m1). A client that
goes away -- a closed terminal, a dropped connection -- detaches and ends nothing.

THE TRUST PROMPT (KYO-567). In a folder Claude Code has
never trusted, the interactive claude stops on its workspace-trust dialog before it
registers with Remote Control, so claude.ai never lists the session and says nothing
about why. ``start`` therefore reads the new pane (``tmux capture-pane``, read only)
for a short bounded wait, and ``list`` reads each live session's pane the same way:
when :data:`TRUST_PROMPT_TEXT` is on screen the state is ``waiting-for-trust``, naming
the folder and where the prompt is. The answer is the person's: nothing here sends a
keystroke to the pane, and nothing here writes Claude Code's own settings file in the
home directory, where trust is kept under an undocumented key -- a test fails if this
module names that file in code. The prompt's wording is Claude Code's, not ours; when
it changes, the match misses and ``start`` says the session has not registered with
Remote Control yet and where to look, rather than reporting plain success.
"""

from __future__ import annotations

import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import time
from argparse import Namespace
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import home_settings
from .commands import Context, Output
from .errors import KyoseiError

#: The dedicated tmux server every controller session lives on (``tmux -L kyosei``).
SOCKET_NAME = "kyosei"

#: An explicit socket PATH to use instead of ``-L kyosei`` (``tmux -S <path>``). For a
#: second, private server — the suite's, which never touches the real one.
SOCKET_ENV = "KYOSEI_CONTROLLER_SOCKET"

#: The per-user LaunchAgent that keeps the server running from login.
LAUNCH_AGENT_LABEL = "dev.kyosei.controller-tmux"

#: The program's shell: a login shell, because the session's own PATH is bare (m1).
LOGIN_SHELL: tuple[str, ...] = ("/bin/zsh", "-lc")

#: The flag whose value is the name claude.ai lists a Remote Control session under.
REMOTE_CONTROL_FLAG = "--remote-control"

#: The session's user option holding the command it was started with (KYO-462).
COMMAND_OPTION = "@kyosei_command"

#: Where tmux usually is when PATH does not say: an SSH command's PATH is bare (m1).
TMUX_FALLBACKS: tuple[str, ...] = ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux")

#: Variables that would move ``-L kyosei`` to another server, or claim we are inside one.
DROPPED_ENV = ("TMUX", "TMUX_PANE", "TMUX_TMPDIR")

#: Any of these set means this command arrived over SSH.
SSH_ENV = ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")

#: How long ``start`` waits before asking whether claude is still alive: long enough
#: for a login shell that cannot find claude to have exited.
SETTLE_SECONDS = 1.5

#: The words Claude Code's workspace-trust dialog shows, matched in the pane (KYO-567).
#: Its "yes" option, as Claude Code 2.1.296 draws it -- measured in a detached tmux
#: pane on an untrusted folder: "Accessing workspace:", the folder, "Quick
#: safety check: Is this a project you created or one you trust? ...", then the options
#: "No, exit" and "Yes, I trust this folder". The option label rather than the question,
#: because it is short enough never to wrap on a narrow pane and names the act exactly.
TRUST_PROMPT_TEXT = "Yes, I trust this folder"

#: The state ``start`` and ``list`` report while that dialog is on screen.
WAITING_FOR_TRUST = "waiting-for-trust"

#: The state ``start`` reports otherwise: claude is alive, and kyo cannot see claude.ai,
#: so whether it has registered with Remote Control is not something kyo can confirm.
STARTED = "started"

#: How long ``start`` watches the new pane for the trust prompt, counted from the spawn
#: (``SETTLE_SECONDS`` included). Measured: the dialog was on screen 1.2s
#: after a login zsh exec'd claude in an untrusted folder; this leaves room for a
#: loaded machine. ``list`` re-reads the pane on every ask, so a prompt drawn later
#: than this is still reported -- the desktop app asks every couple of seconds.
TRUST_WAIT_SECONDS = 5.0

#: How often the pane is read inside that wait.
TRUST_POLL_SECONDS = 0.25

#: A pane that has shown the same non-blank screen this many reads in a row has
#: finished drawing, and the wait ends early: claude is past (or never had) the dialog.
TRUST_STABLE_READS = 4

#: How long one tmux command is given.
TMUX_TIMEOUT_SECONDS = 10

#: ``list-panes`` fields; the path is last because it may hold anything.
PANE_FORMAT = (
    "#{session_name}\t#{pane_dead}\t#{pane_pid}\t#{pane_current_command}\t#{pane_current_path}"
)

#: tmux's own words for "there is no server on this socket" (an answer, not a fault).
_NO_SERVER = re.compile(r"no server running|error connecting to .*no such file or directory", re.I)
#: ... and for "the server has no sessions" from ``list-panes -a``.
_NO_TARGET = re.compile(r"no current target|no sessions", re.I)

_PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")

#: ``(argv) -> (returncode, stdout, stderr)``: how ``launchctl`` is run. Replaced in the
#: suite, which must never bootstrap a real LaunchAgent.
LaunchctlRunner = Callable[[list[str]], tuple[int, str, str]]


def _run_launchctl(argv: list[str]) -> tuple[int, str, str]:
    done = subprocess.run(  # noqa: S603 - a fixed program, argv built here
        ["/bin/launchctl", *argv], capture_output=True, text=True, timeout=30, check=False
    )
    return done.returncode, done.stdout, done.stderr


#: The runner ``--install --apply`` and ``--uninstall --apply`` use; the suite replaces it.
launchctl_runner: LaunchctlRunner = _run_launchctl

#: How :func:`load_agent` waits between polls; the suite replaces it so a test that keeps
#: a job listed for a while neither sleeps for real nor races a clock (KYO-472).
launchctl_sleep: Callable[[float], None] = time.sleep

#: ``launchctl bootout`` returns before the job is gone, and a bootstrap in that window
#: fails with launchctl's generic I/O error (KYO-472, seen while reinstalling kyo
#: serve). After a bootout, :func:`load_agent` polls ``launchctl print`` this often, for
#: at most this long, before it bootstraps -- bounded, so a job that never leaves still
#: gets its bootstrap attempt (and its retry) rather than a hang.
BOOTOUT_POLL_SECONDS = 0.1
BOOTOUT_WAIT_SECONDS = 10.0
#: The pause before the one retry of a failed bootstrap.
BOOTSTRAP_RETRY_SECONDS = 1.0

#: ``(binary, argv, env) -> None``: how ``attach`` replaces this process with tmux. The
#: real one never returns; the suite's records the argv and does, so a test asserts
#: what would run rather than handing its own process to a tmux client.
Execer = Callable[[str, list[str], dict[str, str]], None]


def _execve(binary: str, argv: list[str], env: dict[str, str]) -> None:
    # Nothing buffered may be lost when this process image is replaced.
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(binary, argv, env)  # noqa: S606 - a resolved tmux, argv built here


#: What ``attach`` execs through; the suite replaces it.
exec_runner: Execer = _execve


# ---------------------------------------------------------------------- tmux


@dataclass(frozen=True)
class Tmux:
    """One tmux binary, on one socket, in one scrubbed environment."""

    binary: str
    socket_args: tuple[str, ...]
    env: dict[str, str]

    @property
    def spelled(self) -> str:
        """How a message spells this server for a reader to type."""
        return shlex.join(["tmux", *self.socket_args])

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S603 - a resolved binary, argv built here
            [self.binary, *self.socket_args, *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=TMUX_TIMEOUT_SECONDS,
            check=False,
        )


def controller_env(environ: dict[str, str]) -> dict[str, str]:
    env = dict(environ)
    for name in DROPPED_ENV:
        env.pop(name, None)
    return env


def socket_args(environ: dict[str, str]) -> tuple[str, ...]:
    explicit = (environ.get(SOCKET_ENV) or "").strip()
    return ("-S", explicit) if explicit else ("-L", SOCKET_NAME)


def find_tmux(environ: dict[str, str]) -> str | None:
    found = shutil.which("tmux", path=environ.get("PATH") or os.defpath)
    if found:
        return found
    for candidate in TMUX_FALLBACKS:
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def no_tmux_message(environ: dict[str, str]) -> str:
    looked = environ.get("PATH") or os.defpath
    return (
        f"kyo orchestrator needs tmux and found none (looked on PATH={looked} and in "
        f"{', '.join(TMUX_FALLBACKS)}); install it (`brew install tmux` on macOS), or run "
        "this through a login shell (`zsh -lc 'kyo orchestrator ...'`) if tmux is installed "
        "somewhere only your login PATH names"
    )


def resolve_tmux(environ: dict[str, str]) -> Tmux:
    binary = find_tmux(environ)
    if binary is None:
        raise KyoseiError(no_tmux_message(environ))
    return Tmux(binary, socket_args(environ), controller_env(environ))


def _ensure_socket_dir(tmux: Tmux) -> None:
    """``-S`` does not create the socket's directory; ``-L`` does."""
    if tmux.socket_args[0] != "-S":
        return
    directory = Path(tmux.socket_args[1]).parent
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    except OSError:
        pass  # tmux's own message about it is about to be the better one


def server_running(tmux: Tmux) -> bool:
    done = tmux.run("list-sessions", "-F", "#{session_name}")
    if done.returncode == 0:
        return True
    problem = done.stderr.strip()
    if _NO_SERVER.search(problem):
        return False
    raise KyoseiError(f"could not ask {tmux.spelled} whether it is running: {problem}")


@dataclass(frozen=True)
class Pane:
    session: str
    dead: bool
    pid: int | None
    command: str
    cwd: str

    def to_json(self) -> dict:
        return {
            "session": self.session,
            "claude_alive": not self.dead,
            "pid": self.pid,
            "command": self.command,
            "cwd": self.cwd,
        }


def panes(tmux: Tmux) -> list[Pane]:
    """Every pane on the server; none when there is no server or no session."""
    done = tmux.run("list-panes", "-a", "-F", PANE_FORMAT)
    if done.returncode != 0:
        problem = done.stderr.strip()
        if _NO_SERVER.search(problem) or _NO_TARGET.search(problem):
            return []
        raise KyoseiError(f"could not list the sessions on {tmux.spelled}: {problem}")
    found: list[Pane] = []
    for line in done.stdout.splitlines():
        if not line:
            continue
        session, dead, pid, command, *rest = [*line.split("\t"), "", "", "", ""][:5]
        found.append(
            Pane(
                session, dead == "1", int(pid) if pid.isdigit() else None, command, "\t".join(rest)
            )
        )
    return found


def started_with(tmux: Tmux) -> dict[str, str]:
    """Each session's ``@kyosei_command``: the command ``start`` ran in it (KYO-462).

    A session kyo did not start carries none, and is absent here."""
    done = tmux.run("list-sessions", "-F", f"#{{session_name}}\t#{{{COMMAND_OPTION}}}")
    if done.returncode != 0:
        return {}
    found: dict[str, str] = {}
    for line in done.stdout.splitlines():
        session, _, command = line.partition("\t")
        if session and command:
            found[session] = command
    return found


def session_panes(tmux: Tmux, session: str) -> list[Pane]:
    # Filtered by exact name here: a `-t` target matches as a PREFIX when nothing
    # matches exactly.
    return [pane for pane in panes(tmux) if pane.session == session]


def pane_text(tmux: Tmux, session: str) -> str | None:
    """What ``session``'s pane shows now, wrapped lines joined; None when tmux cannot say.

    Read only: ``capture-pane -p`` prints the screen and changes nothing in the pane."""
    done = tmux.run("capture-pane", "-p", "-J", "-t", f"={session}:")
    return done.stdout if done.returncode == 0 else None


def shows_trust_prompt(text: str | None) -> bool:
    """Is Claude Code's workspace-trust dialog on this screen (:data:`TRUST_PROMPT_TEXT`)?

    Whitespace is collapsed first, so a dialog drawn with padding or split across
    columns differently still matches; anything else -- a changed wording, an empty
    pane, no answer from tmux -- is "not recognised", never an error."""
    if not text:
        return False
    return TRUST_PROMPT_TEXT in " ".join(text.split())


def watch_for_trust(tmux: Tmux, session: str, deadline: float) -> bool:
    """Read the new pane until the trust prompt shows, the screen settles, or ``deadline``.

    True only when the prompt was seen. The early end on a settled screen is what keeps
    a start in an already-trusted folder from waiting the whole bound."""
    previous: str | None = None
    same = 0
    while True:
        text = pane_text(tmux, session)
        if shows_trust_prompt(text):
            return True
        if text is not None and text.strip() and text == previous:
            same += 1
            if same >= TRUST_STABLE_READS - 1:
                return False
        else:
            same = 0
        previous = text
        if time.monotonic() >= deadline:
            return False
        time.sleep(TRUST_POLL_SECONDS)


# ------------------------------------------------------------------ projects


@dataclass(frozen=True)
class ControllerProject:
    name: str
    prefix: str
    root: Path
    registered: bool

    def to_json(self) -> dict:
        return {
            "project": self.name,
            "prefix": self.prefix,
            "root": str(self.root),
            "registered": self.registered,
        }


def _project_at(root: str | Path, *, registered: bool) -> ControllerProject | None:
    from .config import project_home_name, read_raw_config, resolve_layout

    try:
        layout = resolve_layout(explicit_root=root, required=False)
    except KyoseiError:
        return None
    if not layout.configured:
        return None
    prefix = read_raw_config(layout.store_root).get("project_prefix")
    if not isinstance(prefix, str) or not _PREFIX_RE.match(prefix.strip()):
        return None
    return ControllerProject(
        project_home_name(layout), prefix.strip(), layout.code_root, registered
    )


def registered_projects(environ: dict[str, str]) -> list[ControllerProject]:
    from . import serve

    try:
        roots = serve.read_registry(serve.registry_path(environ))
    except serve.Refusal as exc:
        raise KyoseiError(str(exc).replace("kyo serve", "kyo orchestrator", 1)) from exc
    found = [_project_at(root, registered=True) for root in roots]
    return [project for project in found if project is not None]


def resolve_project(name: str, environ: dict[str, str]) -> ControllerProject:
    from . import serve

    known = registered_projects(environ)
    matches = [p for p in known if name in (p.name, p.prefix)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        roots = ", ".join(str(p.root) for p in matches)
        raise KyoseiError(
            f"more than one registered project answers to {name!r} ({roots}); name it by "
            "its directory instead (`kyo orchestrator start /path/to/project`)"
        )
    looks_like_path = os.sep in name or name.startswith((".", "~"))
    if looks_like_path and Path(name).expanduser().is_dir():
        project = _project_at(Path(name).expanduser(), registered=False)
        if project is not None:
            return project
    names = ", ".join(sorted(p.name for p in known)) or "none"
    raise KyoseiError(
        f"no project named {name!r} is registered on this machine (registered: {names}; "
        f"the registry is {serve.registry_path(environ)}); name one of those, give the "
        "project's directory instead (`kyo orchestrator start /path/to/project`), or add "
        'its root to that file (`{"projects": [{"root": "/path/to/project"}]}`)'
    )


# ------------------------------------------------------------- the three acts


def over_ssh(environ: dict[str, str]) -> bool:
    return any((environ.get(name) or "").strip() for name in SSH_ENV)


def controller_program(command: list[str]) -> list[str]:
    """The session's program, as argv: a login shell that execs ``command``."""
    return [*LOGIN_SHELL, f"exec {shlex.join(command)}"]


def remote_control_name(command: list[str]) -> str | None:
    """The name claude.ai lists the session under, when the command gives one."""
    for index, arg in enumerate(command):
        if arg == REMOTE_CONTROL_FLAG and index + 1 < len(command):
            return command[index + 1]
        if arg.startswith(f"{REMOTE_CONTROL_FLAG}="):
            return arg.split("=", 1)[1]
    return None


def attach_command(tmux: Tmux, prefix: str) -> str:
    return f"{tmux.spelled} attach -t ={prefix}"


def attach_argv(tmux: Tmux, prefix: str) -> list[str]:
    """The argv ``attach`` execs: the words :func:`attach_command` spells."""
    return [tmux.binary, *tmux.socket_args, "attach", "-t", f"={prefix}"]


def start(project: ControllerProject, tmux: Tmux, environ: dict[str, str]) -> dict:
    session = project.prefix
    command = home_settings.load(environ).controller_argv(session)
    running = server_running(tmux)
    if not running and over_ssh(environ):
        raise KyoseiError(
            f"no {tmux.spelled} server is running, and this command arrived over SSH: a "
            "server started from here would give every session it holds the SSH login's "
            "context, which cannot read the login keychain (KYO-458.m1). Install the "
            "LaunchAgent that keeps it running from login (`kyo orchestrator --install "
            "--apply`, once, then log in to the desktop), or run this command once from a "
            "terminal on the machine itself"
        )
    existing = session_panes(tmux, session) if running else []
    live = [pane for pane in existing if not pane.dead]
    if live:
        raise KyoseiError(
            f"orchestrator session {session} is already running on {tmux.spelled} (claude "
            f"process {live[0].pid}, in {live[0].cwd}); attach to it "
            f"(`{attach_command(tmux, session)}`), or end it first (`kyo orchestrator stop "
            f"{project.name}`)"
        )
    program = controller_program(command)
    spelled = shlex.join(command)
    _ensure_socket_dir(tmux)
    if existing:
        # A session left behind with its program exited (a user whose tmux keeps dead
        # panes): respawned rather than killed, as the app's launcher does.
        done = tmux.run(
            "respawn-pane", "-t", f"={session}:", "-c", str(project.root), *program,
            ";", "set-option", "-t", f"={session}:", COMMAND_OPTION, spelled,
        )  # fmt: skip
    else:
        done = tmux.run(
            "new-session", "-d", "-s", session, "-c", str(project.root), *program,
            ";", "set-option", "-w", "-t", f"={session}:", "remain-on-exit", "off",
            ";", "set-option", "-t", f"={session}:", COMMAND_OPTION, spelled,
        )  # fmt: skip
    if done.returncode != 0:
        raise KyoseiError(
            f"tmux would not start orchestrator session {session} on {tmux.spelled}: "
            f"{done.stderr.strip()}"
        )
    spawned = time.monotonic()
    time.sleep(SETTLE_SECONDS)
    alive = [pane for pane in session_panes(tmux, session) if not pane.dead]
    if not alive:
        raise KyoseiError(
            f"started `{shlex.join(program)}` in tmux session {session} on {tmux.spelled}, "
            f"and it exited at once; check that `{command[0]}` resolves in a login "
            f"shell on this machine (`zsh -lc 'command -v {command[0]}'`), and that it "
            f"accepts the arguments {home_settings.ORCHESTRATOR_COMMAND_KEY} in "
            f"{home_settings.settings_path(environ)} gives it"
        )
    waiting = watch_for_trust(tmux, session, spawned + TRUST_WAIT_SECONDS)
    return {
        "state": WAITING_FOR_TRUST if waiting else STARTED,
        "waiting_for_trust": waiting,
        "trust_folder": alive[0].cwd if waiting else None,
        "session": session,
        "socket": tmux.spelled,
        "program": program,
        "started_with": spelled,
        "pid": alive[0].pid,
        "cwd": alive[0].cwd,
        "attach": attach_command(tmux, session),
        "remote_control_name": remote_control_name(command),
        "server_was_running": running,
    }


def stop(project: ControllerProject, tmux: Tmux) -> dict:
    session = project.prefix
    existing = session_panes(tmux, session)
    if not existing:
        raise KyoseiError(
            f"no orchestrator session {session} is running on {tmux.spelled}, so there is "
            "nothing to stop; `kyo orchestrator list` shows what is running"
        )
    done = tmux.run("kill-session", "-t", f"={session}")
    if done.returncode != 0:
        raise KyoseiError(
            f"tmux would not end orchestrator session {session} on {tmux.spelled}: "
            f"{done.stderr.strip()}"
        )
    return {"session": session, "socket": tmux.spelled, "stopped": True}


def attach(project: ControllerProject, tmux: Tmux, environ: dict[str, str]) -> dict:
    """Replace this process with a tmux client on ``project``'s controller session.

    Refuses, naming the remedy, when there is no server or no session for it: a bare
    ``tmux attach`` would say only "no sessions" or "can't find session", and the
    reader of that over SSH is the person least able to guess which socket was meant.
    """
    session = project.prefix
    if not server_running(tmux):
        install = (
            "install the LaunchAgent that keeps it running from login (`kyo orchestrator "
            "--install --apply`, once, then log in to the desktop), then start the "
            f"session (`kyo orchestrator start {project.name}`)"
        )
        start_here = (
            f"start the session (`kyo orchestrator start {project.name}`), which starts the "
            "server too; `kyo orchestrator --install --apply` keeps the server running from "
            "login, so a start can later arrive over SSH"
        )
        raise KyoseiError(
            f"no {tmux.spelled} server is running on this machine, so there is no "
            f"orchestrator session {session} to attach to; "
            + (install if over_ssh(environ) else start_here)
        )
    if not session_panes(tmux, session):
        raise KyoseiError(
            f"no orchestrator session {session} is running on {tmux.spelled}, so there is "
            f"nothing to attach to; start it (`kyo orchestrator start {project.name}`) and "
            "attach again (`kyo orchestrator list` shows what is running)"
        )
    argv = attach_argv(tmux, session)
    data = {"session": session, "socket": tmux.spelled, "argv": argv}
    exec_runner(tmux.binary, argv, tmux.env)
    return data


def listing(tmux: Tmux | None, environ: dict[str, str]) -> dict:
    projects = registered_projects(environ)
    running = server_running(tmux) if tmux is not None else False
    all_panes = panes(tmux) if running else []
    commands = started_with(tmux) if running else {}
    by_session: dict[str, list[Pane]] = {}
    for pane in all_panes:
        by_session.setdefault(pane.session, []).append(pane)
    rows = []
    for project in projects:
        found = by_session.pop(project.prefix, [])
        live = next((pane for pane in found if not pane.dead), None)
        waiting = (
            tmux is not None
            and live is not None
            and shows_trust_prompt(pane_text(tmux, project.prefix))
        )
        rows.append(
            {
                **project.to_json(),
                "session": project.prefix if found else None,
                "claude_alive": live is not None,
                "pid": live.pid if live else None,
                "command": (live or (found[0] if found else None)).command if found else None,
                "started_with": commands.get(project.prefix) if found else None,
                "waiting_for_trust": waiting,
                "trust_folder": live.cwd if waiting and live else None,
            }
        )
    others = []
    for group in by_session.values():
        for pane in group:
            waiting = (
                tmux is not None
                and not pane.dead
                and shows_trust_prompt(pane_text(tmux, pane.session))
            )
            others.append(
                {
                    **pane.to_json(),
                    "started_with": commands.get(pane.session),
                    "waiting_for_trust": waiting,
                    "trust_folder": pane.cwd if waiting else None,
                }
            )
    return {
        "socket": tmux.spelled if tmux is not None else shlex.join(["tmux", *socket_args(environ)]),
        "tmux": tmux.binary if tmux is not None else None,
        "server_running": running,
        "projects": rows,
        "other_sessions": others,
    }


def trust_sentence(folder: str | None) -> str:
    """The one sentence every surface uses for the waiting state."""
    named = f" {folder}" if folder else " the project's folder"
    return f"Claude Code is asking whether to trust{named}"


# ------------------------------------------------------------- the LaunchAgent


def launch_agent_path(environ: dict[str, str]) -> Path:
    home = (environ.get("HOME") or "").strip()
    base = Path(home) if home else Path.home()
    return base / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def launch_agent_plist(tmux: Tmux) -> bytes:
    """The agent: ``tmux -L kyosei -D`` — a foreground server with ``exit-empty`` off,
    restarted by launchd if it ends, in the GUI login session only."""
    return plistlib.dumps(
        {
            "Label": LAUNCH_AGENT_LABEL,
            "ProgramArguments": [tmux.binary, *tmux.socket_args, "-D"],
            "RunAtLoad": True,
            "KeepAlive": True,
            "LimitLoadToSessionType": "Aqua",
            "ProcessType": "Interactive",
        }
    )


def _domain() -> str:
    return f"gui/{os.getuid()}"


# The acts below are shared with ``kyo serve --install`` (KYO-457, in
# :mod:`kyosei.serve_install`), which keeps its own agent through this same seam: one
# launchctl runner the suite replaces, one way to load and unload a gui/<uid> agent, and
# the agent file written and removed here rather than in a ``serve*`` module, where
# only ``serve_files`` may write (KYO-456, ``tests/test_kyo_serve_files.py``).


def write_agent_file(path: Path, content: bytes) -> None:
    """Write a per-user agent's file (a plist, or a systemd user unit)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def remove_agent_file(path: Path) -> bool:
    """Remove a per-user agent's file; whether it was there."""
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def agent_loaded(service: str) -> bool:
    loaded, _, _ = launchctl_runner(["print", service])
    return loaded == 0


def wait_until_gone(service: str) -> bool:
    """Poll ``launchctl print`` until ``service`` is no longer listed, for at most
    :data:`BOOTOUT_WAIT_SECONDS`; whether it went."""
    polls = max(1, int(BOOTOUT_WAIT_SECONDS / BOOTOUT_POLL_SECONDS))
    for poll in range(polls):
        if not agent_loaded(service):
            return True
        if poll < polls - 1:
            launchctl_sleep(BOOTOUT_POLL_SECONDS)
    return False


def bootstrap_line(path: Path) -> str:
    """The exact ``launchctl bootstrap`` command that loads ``path``, quoted for a shell."""
    return shlex.join(["launchctl", "bootstrap", _domain(), str(path)])


def load_agent(path: Path) -> tuple[int, str, str]:
    """Boot out ``path``'s agent when it is loaded, wait until launchd has let it go, then
    bootstrap it into gui/<uid>, retrying the bootstrap once (KYO-472).

    The result is the last bootstrap's; a nonzero code means the agent is NOT loaded, and
    the caller says so with :func:`not_loaded_remedy`.
    """
    service = f"{_domain()}/{plistlib.loads(path.read_bytes())['Label']}"
    if agent_loaded(service):
        launchctl_runner(["bootout", service])
        wait_until_gone(service)
    result = launchctl_runner(["bootstrap", _domain(), str(path)])
    if result[0] != 0:
        launchctl_sleep(BOOTSTRAP_RETRY_SECONDS)
        wait_until_gone(service)
        result = launchctl_runner(["bootstrap", _domain(), str(path)])
    return result


def not_loaded_remedy(path: Path) -> str:
    """What a caller says when :func:`load_agent` failed twice: the agent is not loaded,
    and the one line that loads it."""
    service = f"{_domain()}/{plistlib.loads(path.read_bytes())['Label']}"
    return (
        f"{service} is NOT loaded; load it by running `{bootstrap_line(path)}` from a "
        "terminal in the desktop session, where the gui domain is yours"
    )


def unload_agent(service: str) -> tuple[bool, int, str, str]:
    """Boot ``service`` out when it is loaded: ``(was_loaded, code, stdout, stderr)``."""
    if not agent_loaded(service):
        return False, 0, "", ""
    return (True, *launchctl_runner(["bootout", service]))


def install(tmux: Tmux, environ: dict[str, str], *, apply: bool) -> tuple[list[str], dict]:
    path = launch_agent_path(environ)
    content = launch_agent_plist(tmux)
    service = f"{_domain()}/{LAUNCH_AGENT_LABEL}"
    steps = [
        f"write {path}",
        f"launchctl bootout {service}   (only if it is already loaded)",
        f"launchctl bootstrap {_domain()} {path}",
    ]
    data = {"plist_path": str(path), "service": service, "steps": steps, "applied": apply}
    if not apply:
        lines = [
            f"kyo orchestrator --install: dry run; `kyo orchestrator --install --apply` would "
            f"write {path}:",
            "",
            *content.decode("utf-8").rstrip("\n").splitlines(),
            "",
            "and then run:",
            *(f"  {step}" for step in steps[1:]),
        ]
        return lines, {**data, "plist": content.decode("utf-8")}
    write_agent_file(path, content)
    code, out, err = load_agent(path)
    if code != 0:
        raise KyoseiError(
            f"wrote {path}, and `launchctl bootstrap {_domain()} {path}` failed twice "
            f"({(err or out).strip() or f'exit {code}'}); {not_loaded_remedy(path)}"
        )
    return [
        f"kyo orchestrator --install: wrote {path} and loaded {service}; {tmux.spelled} now "
        "runs from login, and `kyo orchestrator start <project>` may arrive over SSH"
    ], data


def uninstall(environ: dict[str, str], *, apply: bool) -> tuple[list[str], dict]:
    path = launch_agent_path(environ)
    service = f"{_domain()}/{LAUNCH_AGENT_LABEL}"
    steps = [f"launchctl bootout {service}   (only if it is loaded)", f"remove {path}"]
    data = {"plist_path": str(path), "service": service, "steps": steps, "applied": apply}
    if not apply:
        return [
            "kyo orchestrator --uninstall: dry run; `kyo orchestrator --uninstall --apply` would:",
            *(f"  {step}" for step in steps),
            "Booting the agent out ends its tmux server, and every orchestrator session in it.",
        ], data
    was_loaded, code, out, err = unload_agent(service)
    if code != 0:
        raise KyoseiError(
            f"`launchctl bootout {service}` failed ({(err or out).strip() or f'exit {code}'}); "
            f"{path} is left in place — run the same command again from a terminal in the "
            "desktop session"
        )
    removed = remove_agent_file(path)
    agent = f"booted out {service}" if was_loaded else f"{service} was not loaded"
    plist = f"removed {path}" if removed else f"{path} was not there"
    return [f"kyo orchestrator --uninstall: {agent}; {plist}"], {
        **data,
        "was_loaded": was_loaded,
        "removed": removed,
    }


# -------------------------------------------------------------------- command


def cmd_controller(ctx: Context, args: Namespace) -> Output:
    """``kyo controller start|stop|attach|list [PROJECT]`` and ``--install``/``--uninstall``."""
    environ = ctx.environ
    modes = [m for m, on in (("--install", args.install), ("--uninstall", args.uninstall)) if on]
    if (args.action and modes) or len(modes) > 1 or not (args.action or modes):
        raise KyoseiError(
            "kyo orchestrator takes one of start, stop, attach, list, --install or "
            "--uninstall; run one of them (`kyo orchestrator list`, `kyo orchestrator --install`)"
        )
    if args.apply and not modes:
        raise KyoseiError(
            "--apply goes with --install or --uninstall and nothing else; drop it "
            f"(`kyo orchestrator {args.action}{' ' + args.project if args.project else ''}`)"
        )
    if modes:
        # A project beside --install is refused by argparse: it lands on `action`.
        if modes[0] == "--install":
            lines, data = install(resolve_tmux(environ), environ, apply=args.apply)
        else:
            lines, data = uninstall(environ, apply=args.apply)
        return Output(
            lines, {"ok": True, "command": "orchestrator", "action": modes[0][2:], **data}
        )

    if args.action == "list":
        if args.project:
            raise KyoseiError(
                f"kyo orchestrator list takes no project; drop {args.project!r} "
                "(`kyo orchestrator list`)"
            )
        binary = find_tmux(environ)
        tmux = Tmux(binary, socket_args(environ), controller_env(environ)) if binary else None
        data = listing(tmux, environ)
        lines = [
            f"{data['socket']}: "
            + ("server running" if data["server_running"] else "no server running")
            + ("" if tmux else " (no tmux found)")
        ]
        for row in data["projects"]:
            state = (
                f"claude alive (pid {row['pid']})"
                if row["claude_alive"]
                else ("session there, claude exited" if row["session"] else "not running")
            )
            if row["waiting_for_trust"]:
                state += (
                    f", {WAITING_FOR_TRUST}: {trust_sentence(row['trust_folder'])}; answer "
                    f"it with `kyo orchestrator attach {row['project']}` or in the desktop "
                    "app's terminal tab"
                )
            lines.append(
                f"  {row['project']:<20} {row['prefix']:<8} {state}"
                + (f"; started with: {row['started_with']}" if row["started_with"] else "")
            )
        if not data["projects"]:
            lines.append("  (no registered projects)")
        for other in data["other_sessions"]:
            lines.append(
                f"  (unregistered) session {other['session']}: "
                + ("alive" if other["claude_alive"] else "exited")
                + (
                    f", {WAITING_FOR_TRUST}: {trust_sentence(other['trust_folder'])}"
                    if other["waiting_for_trust"]
                    else ""
                )
                + (f"; started with: {other['started_with']}" if other["started_with"] else "")
            )
        return Output(lines, {"ok": True, "command": "orchestrator", "action": "list", **data})

    if not args.project:
        raise KyoseiError(
            f"kyo orchestrator {args.action} needs a project; name one "
            f"(`kyo orchestrator {args.action} <project>`; `kyo orchestrator list` shows them)"
        )
    tmux = resolve_tmux(environ)
    project = resolve_project(args.project, environ)
    if args.action == "start":
        data = start(project, tmux, environ)
        listed = (
            f"it is listed on claude.ai as {data['remote_control_name']} once it registers "
            "with Remote Control, and "
            if data["remote_control_name"]
            else ""
        )
        where = f"`kyo orchestrator attach {project.name}` here, or the desktop app's terminal tab"
        if data["waiting_for_trust"]:
            lines = [
                f"kyo orchestrator: started {data['session']} for {project.name} on "
                f"{data['socket']} running `{data['started_with']}` (pid {data['pid']}), "
                f"and it is {WAITING_FOR_TRUST}: {trust_sentence(data['trust_folder'])}. "
                "It will not register with Remote Control until a person answers; answer "
                f"it in {where}"
            ]
        else:
            lines = [
                f"kyo orchestrator: started {data['session']} for {project.name} on "
                f"{data['socket']} running `{data['started_with']}` (pid {data['pid']}, in "
                f"{data['cwd']}); {listed}`{data['attach']}` attaches to it here",
                "It has not registered with Remote Control yet as far as kyo can see (kyo "
                "reads the terminal, not claude.ai); if claude.ai does not list it within a "
                f"minute, look at the session: {where}",
            ]
    elif args.action == "attach":
        # Returns only when the exec did not replace this process: in the suite.
        data = attach(project, tmux, environ)
        lines = [f"kyo orchestrator: attached to {data['session']} on {data['socket']}"]
    else:
        data = stop(project, tmux)
        lines = [
            f"kyo orchestrator: stopped {data['session']} for {project.name} on {data['socket']}"
        ]
    return Output(
        lines,
        {"ok": True, "command": "orchestrator", "action": args.action, **project.to_json(), **data},
    )
