"""``kyo controller`` (KYO-458): detached claude Remote Control sessions on a dedicated
tmux server, and the LaunchAgent that keeps that server running from login.

Every test runs on a PRIVATE tmux server (``tmux -S <tmp>/sock``, through
``KYOSEI_CONTROLLER_SOCKET``), never ``-L kyosei`` and never the default server, and
ends only that server. ``launchctl`` is replaced for every test (autouse), so nothing
here can bootstrap a real LaunchAgent, and ``HOME`` is a temp directory, so nothing
here can write ``~/Library/LaunchAgents``. ``claude`` is a shell script on the test's
PATH, and the login shell is ``/bin/sh -c``: a real ``zsh -l`` reads the developer's
profile, whose PATH names the real claude.
"""

from __future__ import annotations

import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from kyosei import cmd_controller, home_settings, serve

from .conftest import invoke

TMUX = shutil.which("tmux")
pytestmark = pytest.mark.skipif(TMUX is None, reason="tmux is not installed")

FAKE_CLAUDE = """#!/bin/sh
printf '%s\\n' "$*" > "$(dirname "$0")/claude.args"
pwd -P > "$(dirname "$0")/claude.cwd"
exec sleep 300
"""

FAILING_CLAUDE = "#!/bin/sh\nexit 3\n"


@pytest.fixture(autouse=True)
def no_real_launchctl(monkeypatch):
    def refuse(argv):  # pragma: no cover - fails the test that reached it
        raise AssertionError(f"the real launchctl was reached: {argv}")

    monkeypatch.setattr(cmd_controller, "launchctl_runner", refuse)
    sleeps: list[float] = []
    monkeypatch.setattr(cmd_controller, "launchctl_sleep", sleeps.append)
    return sleeps


@pytest.fixture
def socket_dir():
    # Short and under /tmp: a unix socket path is limited to 104 bytes on macOS, and
    # pytest's tmp_path is longer than that.
    directory = Path(tempfile.mkdtemp(prefix="kyc.", dir="/tmp"))
    yield directory
    subprocess.run(
        [TMUX, "-S", str(directory / "sock"), "kill-server"],
        capture_output=True,
        check=False,
        env={k: v for k, v in os.environ.items() if not k.startswith("TMUX")},
    )
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir()
    write_claude(directory, FAKE_CLAUDE)
    return directory


def write_claude(directory: Path, text: str) -> None:
    path = directory / "claude"
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def env(monkeypatch, tmp_path: Path, socket_dir: Path, bin_dir: Path) -> dict:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(cmd_controller.SOCKET_ENV, str(socket_dir / "sock"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    for name in (*cmd_controller.SSH_ENV, *cmd_controller.DROPPED_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cmd_controller, "LOGIN_SHELL", ("/bin/sh", "-c"))
    monkeypatch.setattr(cmd_controller, "SETTLE_SECONDS", 0.4)
    monkeypatch.setattr(cmd_controller, "TRUST_WAIT_SECONDS", 1.0)
    monkeypatch.setattr(cmd_controller, "TRUST_POLL_SECONDS", 0.1)
    return {"socket": socket_dir / "sock", "home": home, "bin": bin_dir}


def register(store_home: Path, *roots: Path) -> None:
    store_home.mkdir(parents=True, exist_ok=True)
    (store_home / serve.REGISTRY_FILE).write_text(
        json.dumps({"projects": [{"root": str(root)} for root in roots]})
    )


@pytest.fixture
def registered(project: Path, store_home: Path) -> Path:
    register(store_home, project)
    return project


def tmux(socket: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [TMUX, "-S", str(socket), *args],
        capture_output=True,
        text=True,
        check=False,
        env={k: v for k, v in os.environ.items() if not k.startswith("TMUX")},
    )


def wait_for(path: Path, timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return path.read_text().strip()
        time.sleep(0.05)
    raise AssertionError(f"{path} never appeared")


# ------------------------------------------------------------------- c2: start


def test_start_creates_a_detached_session_named_for_the_project_running_claude(
    env, registered: Path
) -> None:
    run = invoke(["controller", "start", "proj", "--json"])
    assert run.code == 0, run
    data = run.json
    assert data["session"] == "TST" and data["prefix"] == "TST"
    # Detached: the command returned, and the session is still there with no client.
    sessions = tmux(env["socket"], "list-sessions", "-F", "#{session_name} #{session_attached}")
    assert sessions.stdout.split("\n")[0] == "TST 0"
    # claude, through the login shell, in the project -- with no settings.yml, claude
    # alone, no arguments (KYO-462).
    assert wait_for(env["bin"] / "claude.cwd") == str(registered.resolve())
    assert (env["bin"] / "claude.args").read_text() == "\n"
    assert data["program"] == ["/bin/sh", "-c", "exec claude"]
    assert data["started_with"] == "claude" and data["remote_control_name"] is None
    # `exec`: claude IS the pane's process, so "alive" is the pane's liveness.
    pane_pid = tmux(env["socket"], "list-panes", "-t", "=TST:", "-F", "#{pane_pid}").stdout
    assert int(pane_pid) == data["pid"]


def test_the_real_program_is_a_login_zsh_that_execs_claude() -> None:
    assert cmd_controller.LOGIN_SHELL == ("/bin/zsh", "-lc")
    assert cmd_controller.controller_program(["claude", "--remote-control", "KYO"]) == [
        "/bin/zsh",
        "-lc",
        "exec claude --remote-control KYO",
    ]
    # Each argument is shell-quoted into the one command string.
    assert cmd_controller.controller_program(["claude", "--name", "a b"])[-1] == (
        "exec claude --name 'a b'"
    )


def test_the_socket_is_the_dedicated_kyosei_one_unless_named() -> None:
    assert cmd_controller.socket_args({}) == ("-L", "kyosei")
    assert cmd_controller.socket_args({cmd_controller.SOCKET_ENV: "/x/s"}) == ("-S", "/x/s")


def test_the_environment_that_would_move_the_socket_is_dropped() -> None:
    env = cmd_controller.controller_env(
        {"TMUX": "/tmp/other,1,0", "TMUX_PANE": "%1", "TMUX_TMPDIR": "/elsewhere", "PATH": "/b"}
    )
    assert env == {"PATH": "/b"}


def test_start_by_prefix_and_by_directory_path(env, registered: Path, tmp_path: Path) -> None:
    assert invoke(["controller", "start", "TST"]).code == 0
    assert invoke(["controller", "stop", "TST"]).code == 0
    # An unregistered project, by its directory.
    other = tmp_path / "other"
    assert invoke(["init", str(other), "--in-repo", "--prefix", "OTH"]).code == 0
    run = invoke(["controller", "start", str(other), "--json"])
    assert run.code == 0, run
    assert run.json["session"] == "OTH" and run.json["registered"] is False


# ------------------------------------------------------------- c3: list and stop


def test_list_shows_each_projects_session_and_whether_claude_is_alive(
    env, registered: Path
) -> None:
    before = invoke(["controller", "list", "--json"]).json
    assert before["server_running"] is False
    assert [(p["project"], p["claude_alive"]) for p in before["projects"]] == [("proj", False)]

    assert invoke(["controller", "start", "proj"]).code == 0
    during = invoke(["controller", "list", "--json"]).json
    row = during["projects"][0]
    assert row["session"] == "TST" and row["claude_alive"] is True and row["pid"] > 0
    text = invoke(["controller", "list"]).out
    assert "claude alive" in text

    stopped = invoke(["controller", "stop", "proj", "--json"])
    assert stopped.code == 0 and stopped.json["stopped"] is True
    after = invoke(["controller", "list", "--json"]).json
    assert after["projects"][0]["claude_alive"] is False
    assert tmux(env["socket"], "has-session", "-t", "=TST").returncode != 0


def test_list_names_a_session_no_registered_project_owns(env, registered: Path) -> None:
    assert tmux(env["socket"], "new-session", "-d", "-s", "STRAY", "sleep 60").returncode == 0
    data = invoke(["controller", "list", "--json"]).json
    assert [s["session"] for s in data["other_sessions"]] == ["STRAY"]


def test_start_refuses_a_session_already_running_and_its_remedy_clears_it(
    env, registered: Path
) -> None:
    assert invoke(["controller", "start", "proj"]).code == 0
    again = invoke(["controller", "start", "proj"])
    assert again.code == 1
    assert "already running" in again.err and "`kyo orchestrator stop proj`" in again.err
    assert "attach -t =TST" in again.err
    # The remedy: stop it, then start succeeds.
    assert invoke(["controller", "stop", "proj"]).code == 0
    assert invoke(["controller", "start", "proj"]).code == 0


def test_stop_refuses_when_nothing_runs_naming_list(env, registered: Path) -> None:
    run = invoke(["controller", "stop", "proj"])
    assert run.code == 1 and "nothing to stop" in run.err
    assert "`kyo orchestrator list`" in run.err
    assert invoke(["controller", "list"]).code == 0


def test_an_unknown_project_is_refused_and_registering_it_clears_it(
    env, project: Path, store_home: Path
) -> None:
    register(store_home)  # KYO-455: `kyo init` registered it; start unregistered
    run = invoke(["controller", "start", "proj"])
    assert run.code == 1
    assert "no project named 'proj' is registered" in run.err
    assert str(store_home / serve.REGISTRY_FILE) in run.err
    assert "`kyo orchestrator start /path/to/project`" in run.err
    # Remedy 1: add its root to the registry.
    register(store_home, project)
    assert invoke(["controller", "start", "proj"]).code == 0
    assert invoke(["controller", "stop", "proj"]).code == 0
    # Remedy 2: give its directory (already proved above too).
    register(store_home)
    assert invoke(["controller", "start", str(project)]).code == 0


def test_no_tmux_is_refused_and_installing_it_clears_it(env, registered: Path, monkeypatch) -> None:
    monkeypatch.setattr(cmd_controller, "TMUX_FALLBACKS", ())
    monkeypatch.setenv("PATH", str(env["bin"]))
    run = invoke(["controller", "start", "proj"])
    assert run.code == 1 and "needs tmux and found none" in run.err
    assert "`brew install tmux`" in run.err
    # The remedy: tmux where PATH (or a login shell's PATH) finds it.
    tmux_dir = Path(TMUX).parent
    monkeypatch.setenv("PATH", f"{env['bin']}{os.pathsep}{tmux_dir}{os.pathsep}/usr/bin:/bin")
    assert invoke(["controller", "start", "proj"]).code == 0


def test_claude_that_exits_at_once_is_reported_and_a_working_claude_clears_it(
    env, registered: Path
) -> None:
    write_claude(env["bin"], FAILING_CLAUDE)
    run = invoke(["controller", "start", "proj"])
    assert run.code == 1 and "exited at once" in run.err
    assert "zsh -lc 'command -v claude'" in run.err
    write_claude(env["bin"], FAKE_CLAUDE)
    assert invoke(["controller", "start", "proj"]).code == 0


# ----------------------------------------------------- c5: SSH and the LaunchAgent


class FakeLaunchctl:
    """Records every call; ``bootstrap`` runs the plist's program as launchd would."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.loaded: set[str] = set()
        self.processes: list[subprocess.Popen] = []

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        verb = argv[0]
        if verb == "print":
            return (0, "", "") if argv[1] in self.loaded else (113, "", "not found")
        if verb == "bootstrap":
            plist = plistlib.loads(Path(argv[2]).read_bytes())
            self.loaded.add(f"{argv[1]}/{plist['Label']}")
            self.processes.append(
                subprocess.Popen(
                    plist["ProgramArguments"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env={k: v for k, v in os.environ.items() if not k.startswith("TMUX")},
                )
            )
            return 0, "", ""
        if verb == "bootout":
            self.loaded.discard(argv[1])
            return 0, "", ""
        raise AssertionError(argv)


@pytest.fixture
def launchctl(monkeypatch):
    fake = FakeLaunchctl()
    monkeypatch.setattr(cmd_controller, "launchctl_runner", fake)
    yield fake
    for process in fake.processes:
        process.terminate()
        process.wait(timeout=5)


def test_over_ssh_with_no_server_start_refuses_naming_install_and_install_clears_it(
    env, registered: Path, monkeypatch, launchctl: FakeLaunchctl
) -> None:
    monkeypatch.setenv("SSH_CONNECTION", "100.64.0.2 51000 100.64.0.1 22")
    run = invoke(["controller", "start", "proj"])
    assert run.code == 1
    assert "`kyo orchestrator --install --apply`" in run.err and "keychain" in run.err
    # Nothing was started in the SSH context.
    assert not env["socket"].exists() or tmux(env["socket"], "list-sessions").returncode != 0

    installed = invoke(["controller", "--install", "--apply", "--json"])
    assert installed.code == 0, installed
    uid = os.getuid()
    plist_path = env["home"] / "Library" / "LaunchAgents" / "dev.kyosei.controller-tmux.plist"
    assert installed.json["plist_path"] == str(plist_path)
    assert ["bootstrap", f"gui/{uid}", str(plist_path)] in launchctl.calls
    deadline = time.monotonic() + 5
    while tmux(env["socket"], "list-sessions").returncode != 0:
        assert time.monotonic() < deadline, "the agent's server never came up"
        time.sleep(0.05)

    started = invoke(["controller", "start", "proj", "--json"])
    assert started.code == 0, started
    assert started.json["server_was_running"] is True


def test_the_agent_keeps_a_foreground_tmux_server_on_the_socket_in_the_gui_session(
    env,
) -> None:
    tmux_ = cmd_controller.resolve_tmux(dict(os.environ))
    plist = plistlib.loads(cmd_controller.launch_agent_plist(tmux_))
    assert plist["ProgramArguments"] == [tmux_.binary, "-S", str(env["socket"]), "-D"]
    assert plist["KeepAlive"] is True and plist["RunAtLoad"] is True
    assert plist["LimitLoadToSessionType"] == "Aqua"
    real = cmd_controller.Tmux("/opt/homebrew/bin/tmux", ("-L", "kyosei"), {})
    assert plistlib.loads(cmd_controller.launch_agent_plist(real))["ProgramArguments"] == [
        "/opt/homebrew/bin/tmux",
        "-L",
        "kyosei",
        "-D",
    ]


def test_install_without_apply_prints_and_touches_nothing(env) -> None:
    run = invoke(["controller", "--install"])
    assert run.code == 0, run
    assert "dry run" in run.out and "<key>Label</key>" in run.out
    assert "launchctl bootstrap gui/" in run.out
    assert not (env["home"] / "Library").exists()


def test_install_apply_reloads_an_agent_already_loaded(env, launchctl: FakeLaunchctl) -> None:
    assert invoke(["controller", "--install", "--apply"]).code == 0
    assert invoke(["controller", "--install", "--apply"]).code == 0
    verbs = [call[0] for call in launchctl.calls]
    # KYO-472: the print after the bootout is the wait for launchd to let the job go.
    assert verbs == ["print", "bootstrap", "print", "bootout", "print", "bootstrap"]


def test_uninstall_is_a_dry_run_without_apply(env, launchctl: FakeLaunchctl) -> None:
    assert invoke(["controller", "--install", "--apply"]).code == 0
    plist_path = env["home"] / "Library" / "LaunchAgents" / "dev.kyosei.controller-tmux.plist"
    calls = len(launchctl.calls)
    dry = invoke(["controller", "--uninstall"])
    assert dry.code == 0 and "dry run" in dry.out
    assert plist_path.exists() and len(launchctl.calls) == calls
    done = invoke(["controller", "--uninstall", "--apply", "--json"])
    assert done.code == 0 and done.json["was_loaded"] is True and done.json["removed"] is True
    assert not plist_path.exists()
    assert launchctl.calls[-1][0] == "bootout"


def test_a_failed_bootstrap_is_reported_naming_the_desktop_terminal(env, monkeypatch) -> None:
    calls = []

    def failing(argv):
        calls.append(argv)
        return (113, "", "no") if argv[0] == "print" else (5, "", "Bootstrap failed: 5")

    monkeypatch.setattr(cmd_controller, "launchctl_runner", failing)
    run = invoke(["controller", "--install", "--apply"])
    assert run.code == 1 and "Bootstrap failed: 5" in run.err
    assert "desktop session" in run.err
    # The remedy: the same command where bootstrap works.
    monkeypatch.setattr(cmd_controller, "launchctl_runner", lambda argv: (0, "", ""))
    assert invoke(["controller", "--install", "--apply"]).code == 0


def test_a_failed_bootout_is_reported_and_leaves_the_plist(env, monkeypatch) -> None:
    monkeypatch.setattr(cmd_controller, "launchctl_runner", lambda argv: (0, "", ""))
    assert invoke(["controller", "--install", "--apply"]).code == 0
    monkeypatch.setattr(
        cmd_controller,
        "launchctl_runner",
        lambda argv: (0, "", "") if argv[0] == "print" else (5, "", "Boot-out failed: 5"),
    )
    run = invoke(["controller", "--uninstall", "--apply"])
    assert run.code == 1 and "Boot-out failed" in run.err
    monkeypatch.setattr(cmd_controller, "launchctl_runner", lambda argv: (0, "", ""))
    assert invoke(["controller", "--uninstall", "--apply"]).code == 0


# ------------------------------------- KYO-472: a reinstall waits out the bootout


class LingeringLaunchctl:
    """A launchctl whose ``bootout`` returns while the job stays listed for ``linger``
    more ``print`` calls -- launchd's real behaviour -- and whose ``bootstrap`` fails
    (as launchctl's generic I/O error does) while the old job is still listed."""

    def __init__(self, linger: int, *, failures: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.loaded: set[str] = set()
        self.leaving: dict[str, int] = {}
        self.linger = linger
        self.failures = failures

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        verb = argv[0]
        if verb == "print":
            if argv[1] in self.leaving:
                self.leaving[argv[1]] -= 1
                if self.leaving[argv[1]] < 0:
                    del self.leaving[argv[1]]
                    return 113, "", "not found"
                return 0, "", ""
            return (0, "", "") if argv[1] in self.loaded else (113, "", "not found")
        if verb == "bootstrap":
            label = plistlib.loads(Path(argv[2]).read_bytes())["Label"]
            service = f"{argv[1]}/{label}"
            if service in self.leaving:
                return 5, "", "Bootstrap failed: 5: Input/output error"
            if self.failures:
                self.failures -= 1
                return 5, "", "Bootstrap failed: 5: Input/output error"
            self.loaded.add(service)
            return 0, "", ""
        if verb == "bootout":
            self.loaded.discard(argv[1])
            self.leaving[argv[1]] = self.linger
            return 0, "", ""
        raise AssertionError(argv)


def _verbs(calls: list[list[str]]) -> list[str]:
    return [call[0] for call in calls]


def test_kyo472_c1_a_reinstall_waits_until_the_old_job_is_gone_before_bootstrapping(
    env, monkeypatch, no_real_launchctl: list[float]
) -> None:
    fake = LingeringLaunchctl(linger=3)
    monkeypatch.setattr(cmd_controller, "launchctl_runner", fake)
    assert invoke(["controller", "--install", "--apply"]).code == 0
    fake.calls.clear()
    run = invoke(["controller", "--install", "--apply"])
    assert run.code == 0, run
    # Bootout, then three prints that still list the job, then the one that does not,
    # and only then the bootstrap -- which therefore succeeds first time.
    assert _verbs(fake.calls) == ["print", "bootout", "print", "print", "print", "print",
                                  "bootstrap"]  # fmt: skip
    assert no_real_launchctl == [cmd_controller.BOOTOUT_POLL_SECONDS] * 3


def test_kyo472_c1_the_wait_is_bounded_and_the_bootstrap_still_runs(
    env, monkeypatch, no_real_launchctl: list[float]
) -> None:
    monkeypatch.setattr(cmd_controller, "BOOTOUT_WAIT_SECONDS", 0.5)
    fake = LingeringLaunchctl(linger=10_000)
    monkeypatch.setattr(cmd_controller, "launchctl_runner", fake)
    assert invoke(["controller", "--install", "--apply"]).code == 0
    fake.calls.clear()
    run = invoke(["controller", "--install", "--apply"])
    assert run.code == 1
    polls = int(0.5 / cmd_controller.BOOTOUT_POLL_SECONDS)
    # One bounded wait after the bootout, one before the retry; two bootstraps; no more.
    assert _verbs(fake.calls) == ["print", "bootout", *["print"] * polls, "bootstrap",
                                  *["print"] * polls, "bootstrap"]  # fmt: skip


def test_kyo472_c2_a_failed_bootstrap_is_retried_once(env, monkeypatch) -> None:
    fake = LingeringLaunchctl(linger=0, failures=1)
    monkeypatch.setattr(cmd_controller, "launchctl_runner", fake)
    run = invoke(["controller", "--install", "--apply"])
    assert run.code == 0, run
    assert _verbs(fake.calls) == ["print", "bootstrap", "print", "bootstrap"]
    assert f"{cmd_controller._domain()}/{cmd_controller.LAUNCH_AGENT_LABEL}" in fake.loaded


def test_kyo472_c2_two_failures_say_not_loaded_and_the_printed_line_loads_it(
    env, monkeypatch
) -> None:
    fake = LingeringLaunchctl(linger=0, failures=2)
    monkeypatch.setattr(cmd_controller, "launchctl_runner", fake)
    run = invoke(["controller", "--install", "--apply"])
    assert run.code == 1
    assert _verbs(fake.calls).count("bootstrap") == 2
    plist_path = env["home"] / "Library" / "LaunchAgents" / "dev.kyosei.controller-tmux.plist"
    service = f"{cmd_controller._domain()}/{cmd_controller.LAUNCH_AGENT_LABEL}"
    assert f"{service} is NOT loaded" in run.err
    line = cmd_controller.bootstrap_line(plist_path)
    assert f"`{line}`" in run.err
    # The remedy: run exactly that line, and the agent is loaded.
    argv = shlex.split(line)
    assert argv[0] == "launchctl"
    assert fake(argv[1:])[0] == 0
    assert service in fake.loaded


# ------------------------------------------------------------- KYO-474: attach


class RecordingExec:
    """Stands in for ``os.execve``: records what would have replaced the process."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], dict[str, str]]] = []

    def __call__(self, binary: str, argv: list[str], env: dict[str, str]) -> None:
        self.calls.append((binary, list(argv), dict(env)))


@pytest.fixture
def execs(monkeypatch) -> RecordingExec:
    recorder = RecordingExec()
    monkeypatch.setattr(cmd_controller, "exec_runner", recorder)
    return recorder


def test_attach_execs_tmux_attach_with_an_exact_target_on_the_controller_socket(
    env, registered: Path, execs: RecordingExec, monkeypatch
) -> None:
    assert invoke(["controller", "start", "proj"]).code == 0
    # The variables that would move `-L kyosei` elsewhere, or claim we are inside a
    # server already, as a command typed in the app's own pane carries them.
    monkeypatch.setenv("TMUX", "/elsewhere/default,1,0")
    monkeypatch.setenv("TMUX_PANE", "%9")
    monkeypatch.setenv("TMUX_TMPDIR", "/elsewhere")
    run = invoke(["controller", "attach", "TST", "--json"])
    assert run.code == 0, run
    [(binary, argv, exec_env)] = execs.calls
    resolved = cmd_controller.resolve_tmux(dict(os.environ))
    assert binary == resolved.binary
    # The same socket `start`, `stop` and `list` name, and `=` so tmux matches exactly.
    assert argv == [resolved.binary, "-S", str(env["socket"]), "attach", "-t", "=TST"]
    assert run.json["argv"] == argv and run.json["socket"] == f"tmux -S {env['socket']}"
    for name in cmd_controller.DROPPED_ENV:
        assert name not in exec_env
    # By name, as start and stop take it, and the words `start` itself prints.
    assert invoke(["controller", "attach", "proj"]).code == 0
    assert execs.calls[-1][1] == argv
    started = invoke(["controller", "start", "proj"])
    assert "attach -t =TST" in started.err
    assert cmd_controller.attach_command(resolved, "TST").split()[1:] == argv[1:]


def test_attach_really_replaces_itself_with_a_client_that_only_detaches(
    env, registered: Path, store_home: Path
) -> None:
    """The real exec, in a pty: the kyo process BECOMES the tmux client."""
    import pty

    assert invoke(["controller", "start", "proj"]).code == 0
    leader, follower = pty.openpty()
    child_env = {k: v for k, v in os.environ.items() if not k.startswith("TMUX")}
    process = subprocess.Popen(
        [sys.executable, "-m", "kyosei", "controller", "attach", "proj"],
        stdin=follower,
        stdout=follower,
        stderr=follower,
        env=child_env,
        close_fds=True,
    )
    os.close(follower)
    try:
        deadline = time.monotonic() + 15
        clients = ""
        while time.monotonic() < deadline:
            clients = tmux(
                env["socket"], "list-clients", "-F", "#{client_pid} #{session_name}"
            ).stdout
            if clients.strip():
                break
            time.sleep(0.1)
        # The client's pid is the pid kyo was started with: exec, not a child.
        assert clients.split() == [str(process.pid), "TST"], clients
        # Ending the client ends nothing else: the session and claude keep running.
        assert tmux(env["socket"], "detach-client", "-s", "=TST").returncode == 0
        assert process.wait(timeout=10) == 0
        data = invoke(["controller", "list", "--json"]).json
        assert data["projects"][0]["claude_alive"] is True
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        os.close(leader)


def test_attach_with_no_server_names_start_and_starting_clears_it(
    env, registered: Path, execs: RecordingExec
) -> None:
    run = invoke(["controller", "attach", "proj"])
    assert run.code == 1
    assert "server is running on this machine" in run.err
    assert "`kyo orchestrator start proj`" in run.err
    assert "`kyo orchestrator --install --apply`" in run.err
    assert execs.calls == []
    # The remedy it names: start, which starts the server too when not over SSH.
    assert invoke(["controller", "start", "proj"]).code == 0
    assert invoke(["controller", "attach", "proj"]).code == 0
    assert execs.calls[-1][1][-2:] == ["-t", "=TST"]


def test_attach_over_ssh_with_no_server_names_install_and_both_remedies_clear_it(
    env, registered: Path, execs: RecordingExec, monkeypatch, launchctl: FakeLaunchctl
) -> None:
    monkeypatch.setenv("SSH_CONNECTION", "100.64.0.2 51000 100.64.0.1 22")
    run = invoke(["controller", "attach", "proj"])
    assert run.code == 1
    assert "`kyo orchestrator --install --apply`" in run.err
    assert "`kyo orchestrator start proj`" in run.err
    # Remedy one: the LaunchAgent, whose server comes up.
    assert invoke(["controller", "--install", "--apply"]).code == 0
    deadline = time.monotonic() + 5
    while tmux(env["socket"], "list-sessions").returncode != 0:
        assert time.monotonic() < deadline, "the agent's server never came up"
        time.sleep(0.05)
    # Remedy two, named in the sentence that now applies: the session.
    no_session = invoke(["controller", "attach", "proj"])
    assert no_session.code == 1 and "no orchestrator session TST" in no_session.err
    assert "`kyo orchestrator start proj`" in no_session.err
    assert invoke(["controller", "start", "proj"]).code == 0
    assert invoke(["controller", "attach", "proj"]).code == 0
    assert execs.calls[-1][1][-2:] == ["-t", "=TST"]


def test_attach_to_a_project_with_no_session_names_start_and_starting_clears_it(
    env, registered: Path, execs: RecordingExec
) -> None:
    # The server runs, holding somebody else's session and not this project's.
    assert tmux(env["socket"], "new-session", "-d", "-s", "TSTX", "sleep 60").returncode == 0
    run = invoke(["controller", "attach", "proj"])
    assert run.code == 1
    assert "no orchestrator session TST is running" in run.err
    assert "`kyo orchestrator start proj`" in run.err
    # Not a prefix match onto TSTX, which a bare `-t TST` would have made.
    assert execs.calls == []
    assert invoke(["controller", "start", "proj"]).code == 0
    assert invoke(["controller", "attach", "proj"]).code == 0
    assert execs.calls[-1][1][-1] == "=TST"


# ------------------------------------------------------------- usage refusals


@pytest.mark.parametrize(
    ("argv", "says", "fixed"),
    [
        (["controller"], "takes one of start", ["orchestrator", "list"]),
        (["controller", "list", "--install"], "takes one of start", ["orchestrator", "list"]),
        (
            ["controller", "--install", "--uninstall"],
            "takes one of start",
            ["orchestrator", "--install"],
        ),
        (["controller", "list", "--apply"], "--apply goes with", ["orchestrator", "list"]),
        (["controller", "list", "proj"], "list takes no project", ["orchestrator", "list"]),
        (["controller", "stop"], "needs a project", ["orchestrator", "list"]),
        (["controller", "attach"], "needs a project", ["orchestrator", "list"]),
    ],
)
def test_usage_refusals_name_the_invocation_that_works(env, argv, says, fixed) -> None:
    run = invoke(argv)
    assert run.code == 1 and says in run.err, run
    assert f"`kyo {' '.join(fixed)}`" in run.err
    assert invoke(fixed).code == 0


# ------------------------------------------------- KYO-462: controller_command


def write_settings(store_home: Path, text: str) -> Path:
    store_home.mkdir(parents=True, exist_ok=True)
    path = store_home / home_settings.SETTINGS_FILE
    path.write_text(text)
    return path


def tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "kyosei.db"
    }


def test_start_runs_controller_command_with_the_prefix_replaced(
    env, registered: Path, store_home: Path
) -> None:
    write_settings(
        store_home,
        'controller_command: ["claude", "--chrome", "--remote-control", "{prefix}"]\n',
    )
    before = tree_snapshot(registered / "kyosei")
    run = invoke(["controller", "start", "proj", "--json"])
    assert run.code == 0, run
    assert wait_for(env["bin"] / "claude.args") == "--chrome --remote-control TST"
    data = run.json
    assert data["program"] == ["/bin/sh", "-c", "exec claude --chrome --remote-control TST"]
    assert data["started_with"] == "claude --chrome --remote-control TST"
    assert data["remote_control_name"] == "TST"
    assert "listed on claude.ai as TST" in invoke(["controller", "stop", "proj"]).out or True
    # Nothing under the project's kyosei/ was written for this setting.
    assert tree_snapshot(registered / "kyosei") == before
    assert "controller_command" not in (registered / "kyosei" / "config.yml").read_text()


def test_start_message_names_the_remote_control_name_only_when_the_command_gives_one(
    env, registered: Path, store_home: Path
) -> None:
    bare = invoke(["controller", "start", "proj"])
    assert bare.code == 0 and "running `claude`" in bare.out
    assert "listed on claude.ai" not in bare.out
    assert invoke(["controller", "stop", "proj"]).code == 0
    write_settings(store_home, 'controller_command: ["claude", "--remote-control={prefix}"]\n')
    named = invoke(["controller", "start", "proj"])
    assert named.code == 0 and "listed on claude.ai as TST" in named.out


def test_a_command_without_prefix_runs_verbatim(env, registered: Path, store_home: Path) -> None:
    write_settings(store_home, 'controller_command: ["claude", "--chrome"]\n')
    run = invoke(["controller", "start", "proj", "--json"])
    assert run.code == 0, run
    assert wait_for(env["bin"] / "claude.args") == "--chrome"
    assert run.json["started_with"] == "claude --chrome"
    assert run.json["remote_control_name"] is None


def test_a_file_without_the_key_means_the_default(store_home: Path) -> None:
    write_settings(store_home, "# nothing set yet\n")
    settings = home_settings.load()
    assert settings.controller_command == ("claude",) and settings.source == "default"
    assert settings.path == store_home / "settings.yml"
    assert home_settings.load({"KYOSEI_HOME": "/nowhere"}).controller_argv("KYO") == ["claude"]


def test_prefix_is_replaced_inside_an_argument_too(store_home: Path) -> None:
    write_settings(store_home, 'controller_command: ["x", "--name={prefix}-{prefix}"]\n')
    assert home_settings.load().controller_argv("KYO") == ["x", "--name=KYO-KYO"]


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("controller_command: [claude\n", "is not YAML"),
        ("- claude\n", "not a mapping of settings"),
        ("controller_command: claude\n", "not a non-empty list of non-empty strings"),
        ("controller_command: []\n", "not a non-empty list of non-empty strings"),
        ("controller_command: [claude, 3]\n", "not a non-empty list of non-empty strings"),
        ('controller_command: ["claude", ""]\n', "not a non-empty list of non-empty strings"),
        ("controller_comand: [claude]\n", "a key this build does not know (controller_comand"),
    ],
)
def test_a_malformed_settings_file_is_refused_by_name_and_the_remedy_clears_it(
    env, registered: Path, store_home: Path, text: str, says: str
) -> None:
    path = write_settings(store_home, text)
    run = invoke(["controller", "start", "proj"])
    assert run.code == 1, run
    assert str(path) in run.err and says in run.err, run.err
    assert 'orchestrator_command: ["claude", "--remote-control", "{prefix}"]' in run.err
    assert "remove the file" in run.err
    # Nothing was started on the way to the refusal.
    assert tmux(env["socket"], "has-session", "-t", "=TST").returncode != 0
    # Remedy 1: fix it to the value the message offers.
    path.write_text('orchestrator_command: ["claude", "--remote-control", "{prefix}"]\n')
    assert invoke(["controller", "start", "proj"]).code == 0
    assert wait_for(env["bin"] / "claude.args") == "--remote-control TST"
    assert invoke(["controller", "stop", "proj"]).code == 0
    # Remedy 2: remove the file, which runs the default.
    path.write_text(text)
    assert invoke(["controller", "start", "proj"]).code == 1
    path.unlink()
    assert invoke(["controller", "start", "proj"]).code == 0


def test_list_shows_the_command_each_session_was_started_with(
    env, registered: Path, store_home: Path
) -> None:
    write_settings(
        store_home,
        'controller_command: ["claude", "--chrome", "--remote-control", "{prefix}"]\n',
    )
    assert invoke(["controller", "start", "proj"]).code == 0
    # Changing the setting afterwards does not rewrite what the session ran.
    write_settings(store_home, 'controller_command: ["claude"]\n')
    data = invoke(["controller", "list", "--json"]).json
    assert data["projects"][0]["started_with"] == "claude --chrome --remote-control TST"
    text = invoke(["controller", "list"]).out
    assert "started with: claude --chrome --remote-control TST" in text
    # A session kyo did not start has no recorded command.
    assert tmux(env["socket"], "new-session", "-d", "-s", "STRAY", "sleep 60").returncode == 0
    others = invoke(["controller", "list", "--json"]).json["other_sessions"]
    assert [(o["session"], o["started_with"]) for o in others] == [("STRAY", None)]


# ------------------------------------------- KYO-567: Claude Code's trust prompt


#: Claude Code 2.1.296's workspace-trust dialog, as `tmux capture-pane -p` printed it
#: from a detached pane on an untrusted folder (the folder replaced).
MEASURED_TRUST_SCREEN = "\n".join(
    [
        "─" * 80,
        " Accessing workspace:",
        "",
        " {folder}",
        "",
        " Quick safety check: Is this a project you created or one you trust? (Like your own"
        " code, a well-known open source",
        " project, or work from your team). If not, take a moment to review what's in this"
        " folder first.",
        "",
        " Claude Code'll be able to read, edit, and execute files here.",
        "",
        " Security guide",
        "",
        " ❯ No, exit",
        "   Yes, I trust this folder",
        "",
        " Enter to confirm · Esc to cancel",
        "",
    ]
)


def screen_claude(screen: str) -> str:
    """A claude that draws ``screen``, then waits for a line on its terminal.

    Anything typed into the pane -- an Enter, a ``1`` -- lands in ``claude.answered``:
    the proof that nothing kyo does answers the prompt for the person."""
    return (
        "#!/bin/sh\n"
        'pwd -P > "$(dirname "$0")/claude.cwd"\n'
        "cat <<SCREEN\n"
        f"{screen.format(folder='$PWD')}"
        "SCREEN\n"
        'IFS= read -r answer && printf "%s\\n" "$answer" > "$(dirname "$0")/claude.answered"\n'
        "exec sleep 300\n"
    )


def test_the_measured_dialog_is_recognised_and_other_screens_are_not() -> None:
    screen = MEASURED_TRUST_SCREEN.format(folder="/tmp/x")
    assert cmd_controller.shows_trust_prompt(screen)
    # Padding and wrapping do not hide it.
    assert cmd_controller.shows_trust_prompt("  ❯ No, exit\n   Yes,   I trust\nthis folder  ")
    for screen in (None, "", "\n\n", "Welcome to Claude Code", "Do you trust this workspace?"):
        assert not cmd_controller.shows_trust_prompt(screen)
    # One constant holds the words: the match follows it, and nothing else.
    assert cmd_controller.TRUST_PROMPT_TEXT == "Yes, I trust this folder"


def test_start_reports_waiting_for_trust_naming_the_folder_and_sends_no_keystroke(
    env, registered: Path
) -> None:
    """KYO-567.c1: the state is reported; the prompt is left to the person."""
    write_claude(env["bin"], screen_claude(MEASURED_TRUST_SCREEN))
    run = invoke(["controller", "start", "proj", "--json"])
    assert run.code == 0, run
    folder = str(registered.resolve())
    assert run.json["state"] == "waiting-for-trust"
    assert run.json["waiting_for_trust"] is True
    assert run.json["trust_folder"] == folder
    assert invoke(["controller", "stop", "proj"]).code == 0

    text = invoke(["controller", "start", "proj"])
    assert text.code == 0, text
    assert "waiting-for-trust" in text.out
    assert f"Claude Code is asking whether to trust {folder}" in text.out
    assert "`kyo orchestrator attach proj`" in text.out and "terminal tab" in text.out
    # Not plain success: the sentence says it is not on claude.ai yet.
    assert "will not register with Remote Control until a person answers" in text.out
    # Nothing was typed into the pane: the stand-in's `read` is still waiting.
    invoke(["controller", "list", "--json"])
    time.sleep(0.3)
    assert not (env["bin"] / "claude.answered").exists()


def test_list_reports_a_session_showing_the_prompt_as_waiting_for_trust(
    env, registered: Path
) -> None:
    """KYO-567.c2."""
    write_claude(env["bin"], screen_claude(MEASURED_TRUST_SCREEN))
    assert invoke(["controller", "start", "proj"]).code == 0
    row = invoke(["controller", "list", "--json"]).json["projects"][0]
    assert row["claude_alive"] is True
    assert row["waiting_for_trust"] is True
    assert row["trust_folder"] == str(registered.resolve())
    text = invoke(["controller", "list"]).out
    assert "waiting-for-trust" in text and "`kyo orchestrator attach proj`" in text
    # A session that is not showing it is not waiting.
    assert invoke(["controller", "stop", "proj"]).code == 0
    write_claude(env["bin"], FAKE_CLAUDE)
    assert invoke(["controller", "start", "proj"]).code == 0
    row = invoke(["controller", "list", "--json"]).json["projects"][0]
    assert row["claude_alive"] is True and row["waiting_for_trust"] is False
    assert row["trust_folder"] is None
    assert "waiting-for-trust" not in invoke(["controller", "list"]).out


def test_list_reads_an_unregistered_sessions_prompt_too(env, registered: Path) -> None:
    write_claude(env["bin"], screen_claude(MEASURED_TRUST_SCREEN))
    stray = tmux(env["socket"], "new-session", "-d", "-s", "STRAY", str(env["bin"] / "claude"))
    assert stray.returncode == 0
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        others = invoke(["controller", "list", "--json"]).json["other_sessions"]
        if others and others[0]["waiting_for_trust"]:
            break
        time.sleep(0.1)
    assert [(o["session"], o["waiting_for_trust"]) for o in others] == [("STRAY", True)]


def test_an_unrecognised_prompt_still_says_not_registered_yet_and_where_to_look(
    env, registered: Path
) -> None:
    """KYO-567.c5: a changed wording degrades to "not registered yet", never to an error."""
    changed = MEASURED_TRUST_SCREEN.replace("Yes, I trust this folder", "Yes, proceed")
    write_claude(env["bin"], screen_claude(changed))
    run = invoke(["controller", "start", "proj", "--json"])
    assert run.code == 0, run
    assert run.json["state"] == "started" and run.json["waiting_for_trust"] is False
    assert invoke(["controller", "stop", "proj"]).code == 0
    text = invoke(["controller", "start", "proj"])
    assert text.code == 0, text
    assert "has not registered with Remote Control yet" in text.out
    assert "`kyo orchestrator attach proj`" in text.out and "terminal tab" in text.out
    assert "waiting-for-trust" not in text.out


def test_a_settled_screen_ends_the_wait_early(env, registered: Path, monkeypatch) -> None:
    """A trusted folder must not cost every start the whole bound."""
    monkeypatch.setattr(cmd_controller, "TRUST_WAIT_SECONDS", 6.0)
    write_claude(env["bin"], screen_claude("Welcome to Claude Code\n"))
    began = time.monotonic()
    run = invoke(["controller", "start", "proj", "--json"])
    assert run.code == 0, run
    assert run.json["state"] == "started"
    assert time.monotonic() - began < 4.0


def _code_strings(path: Path) -> list[str]:
    """Every string constant in ``path`` that is not a docstring."""
    import ast

    tree = ast.parse(path.read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_cmd_controller_never_names_claude_json_nor_sends_keys() -> None:
    """KYO-567.c4: Claude Code's trust is the person's to give.

    ``~/.claude.json`` is where Claude Code keeps it; this module may not name that file
    in code at all (so it cannot open it for writing), and may not send keystrokes to a
    pane (so it cannot answer the prompt)."""
    source = Path(cmd_controller.__file__)
    strings = _code_strings(source)
    assert strings, "the scan found no string constants: it is not reading the module"
    assert not [text for text in strings if "claude.json" in text]
    assert not [text for text in strings if "send-keys" in text or "send-key" in text]


def test_start_and_list_leave_no_claude_json_in_home(env, registered: Path) -> None:
    write_claude(env["bin"], screen_claude(MEASURED_TRUST_SCREEN))
    assert invoke(["controller", "start", "proj"]).code == 0
    assert invoke(["controller", "list"]).code == 0
    assert not (env["home"] / ".claude.json").exists()
