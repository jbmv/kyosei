"""KYO-457: kyo serve at login, and kyo serve's refusal of a held port.

``launchctl`` and ``systemctl`` are replaced for every test (autouse), so nothing here
can load a real agent; ``HOME`` is a temp directory, so nothing here can write
``~/Library/LaunchAgents`` or ``~/.config/systemd``; and every server or listener runs
on an ephemeral loopback port and is stopped by the test that started it.
"""

from __future__ import annotations

import io
import os
import plistlib
import shlex
import socket
import sys
import threading
from pathlib import Path

import pytest

from kyosei import cmd_controller, serve, serve_install

from .conftest import invoke


@pytest.fixture(autouse=True)
def no_real_managers(monkeypatch, tmp_path: Path):
    def refuse(argv):  # pragma: no cover - fails the test that reached it
        raise AssertionError(f"a real service manager was reached: {argv}")

    monkeypatch.setattr(cmd_controller, "launchctl_runner", refuse)
    monkeypatch.setattr(serve_install, "systemctl_runner", refuse)
    monkeypatch.setattr(cmd_controller, "launchctl_sleep", lambda seconds: None)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(serve_install, "PROBE_TIMEOUT_SECONDS", 0.5)
    return home


class FakeLaunchctl:
    def __init__(self, pid: int = 4242) -> None:
        self.calls: list[list[str]] = []
        self.loaded: set[str] = set()
        self.pid = pid

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        verb = argv[0]
        if verb == "print":
            if argv[1] in self.loaded:
                return 0, f"{argv[1]} = {{\n\tstate = running\n\tpid = {self.pid}\n}}\n", ""
            return 113, "", "Could not find service"
        if verb == "bootstrap":
            label = plistlib.loads(Path(argv[2]).read_bytes())["Label"]
            self.loaded.add(f"{argv[1]}/{label}")
            return 0, "", ""
        if verb == "bootout":
            self.loaded.discard(argv[1])
            return 0, "", ""
        if verb == "kickstart":
            return (0, "", "") if argv[2] in self.loaded else (113, "", "no such service")
        raise AssertionError(argv)


class FakeSystemctl:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.active = False

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        verb = argv[0]
        if verb == "is-active":
            return (0, "active\n", "") if self.active else (3, "inactive\n", "")
        if verb == "enable" and "--now" in argv:
            self.active = True
        elif verb == "disable" and "--now" in argv:
            self.active = False
        elif verb == "show":
            return 0, "4343\n" if self.active else "0\n", ""
        elif verb not in ("daemon-reload", "restart"):
            raise AssertionError(argv)
        return 0, "", ""


@pytest.fixture
def launchd(monkeypatch) -> FakeLaunchctl:
    fake = FakeLaunchctl()
    monkeypatch.setattr(serve_install, "platform_manager", lambda: "launchd")
    monkeypatch.setattr(cmd_controller, "launchctl_runner", fake)
    return fake


@pytest.fixture
def systemd(monkeypatch) -> FakeSystemctl:
    fake = FakeSystemctl()
    monkeypatch.setattr(serve_install, "platform_manager", lambda: "systemd")
    monkeypatch.setattr(serve_install, "systemctl_runner", fake)
    return fake


def plist_path(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / "dev.kyosei.serve.plist"


def unit_path(home: Path) -> Path:
    return home / ".config" / "systemd" / "user" / "kyosei-serve.service"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def kyo_server():
    servers: list[serve.KyoServer] = []

    def _start() -> serve.KyoServer:
        server = serve.KyoServer(port=0, log_stream=io.StringIO())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def listener():
    """A socket that holds a loopback port and never answers HTTP."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    yield sock
    sock.close()


# ------------------------------------------------- c1: install, apply, uninstall


def test_install_prints_the_launchd_agent_and_touches_nothing(
    launchd: FakeLaunchctl, no_real_managers: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PATH", "/opt/tools/bin:/usr/bin:/bin")
    run = invoke(["serve", "--install", "--allow-host", "host.tail.ts.net", "--json"])
    assert run.code == 0, run
    data = run.json
    assert data["action"] == "install" and data["applied"] is False
    assert data["path"] == str(plist_path(no_real_managers))
    assert "<string>dev.kyosei.serve</string>" in data["content"]
    assert f"launchctl bootstrap gui/{os.getuid()} {plist_path(no_real_managers)}" in data["steps"]
    program = data["program"]
    assert os.path.isabs(program[0])
    assert program[program.index("serve"):] == [
        "serve", "--port", "47370", "--allow-host", "host.tail.ts.net",
    ]  # fmt: skip
    assert data["environment"] == {
        "KYOSEI_HOME": os.environ["KYOSEI_HOME"],
        "PATH": "/opt/tools/bin:/usr/bin:/bin",
    }
    assert not (no_real_managers / "Library").exists()
    assert launchd.calls == []
    human = invoke(["serve", "--install"])
    assert "dry run" in human.out and "<key>ProgramArguments</key>" in human.out


def test_install_apply_writes_and_bootstraps_the_agent_and_reloads_it_the_second_time(
    launchd: FakeLaunchctl, no_real_managers: Path
) -> None:
    run = invoke(["serve", "--install", "--apply", "--port", "47999", "--json"])
    assert run.code == 0, run
    path = plist_path(no_real_managers)
    plist = plistlib.loads(path.read_bytes())
    assert plist["Label"] == "dev.kyosei.serve"
    assert plist["ProgramArguments"][-3:] == ["serve", "--port", "47999"]
    assert plist["RunAtLoad"] is True and plist["KeepAlive"] is True
    assert plist["EnvironmentVariables"]["KYOSEI_HOME"] == os.environ["KYOSEI_HOME"]
    assert plist["StandardErrorPath"].startswith(str(no_real_managers))
    assert ["bootstrap", f"gui/{os.getuid()}", str(path)] in launchd.calls
    assert invoke(["serve", "--install", "--apply"]).code == 0
    # KYO-472: the print after the bootout is the wait for launchd to let the job go.
    assert [call[0] for call in launchd.calls] == [
        "print", "bootstrap", "print", "bootout", "print", "bootstrap",
    ]  # fmt: skip


def test_the_program_is_the_kyo_that_ran_install_by_its_own_path(
    tmp_path: Path, monkeypatch
) -> None:
    real = tmp_path / "tool" / "bin" / "kyo"
    real.parent.mkdir(parents=True)
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    link = tmp_path / "local" / "bin" / "kyo"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    monkeypatch.setattr(sys, "argv", [str(link), "serve", "--install"])
    assert serve_install.kyo_program({}) == [str(link)]  # the link, not what it names
    monkeypatch.setattr(sys, "argv", ["kyo", "serve"])
    assert serve_install.kyo_program({"PATH": str(link.parent)}) == [str(link)]
    monkeypatch.setattr(sys, "argv", ["/somewhere/pytest"])
    assert serve_install.kyo_program({}) == [os.path.abspath(sys.executable), "-m", "kyosei"]


def test_uninstall_is_a_dry_run_without_apply(
    launchd: FakeLaunchctl, no_real_managers: Path
) -> None:
    assert invoke(["serve", "--install", "--apply"]).code == 0
    path = plist_path(no_real_managers)
    calls = len(launchd.calls)
    dry = invoke(["serve", "--uninstall"])
    assert dry.code == 0 and "dry run" in dry.out
    assert path.exists() and len(launchd.calls) == calls
    done = invoke(["serve", "--uninstall", "--apply", "--json"])
    assert done.code == 0, done
    assert done.json["was_loaded"] is True and done.json["removed"] is True
    assert not path.exists()
    assert launchd.calls[-1] == ["bootout", f"gui/{os.getuid()}/dev.kyosei.serve"]


def test_systemd_install_prints_the_unit_and_apply_enables_it(
    systemd: FakeSystemctl, no_real_managers: Path
) -> None:
    dry = invoke(["serve", "--install", "--json"])
    assert dry.code == 0, dry
    assert "[Service]" in dry.json["content"] and "ExecStart=" in dry.json["content"]
    assert "WantedBy=default.target" in dry.json["content"]
    assert systemd.calls == [] and not (no_real_managers / ".config").exists()

    assert invoke(["serve", "--install", "--apply"]).code == 0
    unit = unit_path(no_real_managers).read_text()
    assert "serve --port 47370" in unit
    assert f'Environment="KYOSEI_HOME={os.environ["KYOSEI_HOME"]}"' in unit or (
        f"Environment=KYOSEI_HOME={os.environ['KYOSEI_HOME']}" in unit
    )
    assert systemd.calls == [
        ["is-active", "kyosei-serve.service"],
        ["daemon-reload"],
        ["enable", "--now", "kyosei-serve.service"],
    ]
    # A second install over a running unit restarts it onto the new file.
    assert invoke(["serve", "--install", "--apply"]).code == 0
    assert systemd.calls[-1] == ["restart", "kyosei-serve.service"]

    dry = invoke(["serve", "--uninstall"])
    assert dry.code == 0 and unit_path(no_real_managers).exists()
    assert invoke(["serve", "--uninstall", "--apply"]).code == 0
    assert not unit_path(no_real_managers).exists() and systemd.active is False
    assert ["disable", "--now", "kyosei-serve.service"] in systemd.calls


def test_systemd_unit_quotes_what_systemd_would_expand() -> None:
    unit = serve_install.systemd_unit(
        ["/a b/kyo", "serve", "--allow-origin", "http://x:1/%p"], {"PATH": "/x:$HOME"}
    ).decode()
    assert 'ExecStart="/a b/kyo" serve --allow-origin http://x:1/%%p' in unit
    assert "Environment=PATH=/x:$$HOME" in unit


def test_another_platform_is_refused_naming_launchd_and_systemd(monkeypatch) -> None:
    monkeypatch.setattr(serve_install, "platform_manager", lambda: None)
    run = invoke(["serve", "--install"])
    assert run.code == 1 and "launchd" in run.err and "systemd" in run.err


def test_apply_alone_or_two_modes_are_refused_and_naming_one_clears_it(
    launchd: FakeLaunchctl,
) -> None:
    run = invoke(["serve", "--apply"])
    assert run.code == 1 and "`kyo serve --install --apply`" in run.err
    run = invoke(["serve", "--status", "--apply"])
    assert run.code == 1 and "--apply goes with" in run.err
    run = invoke(["serve", "--install", "--restart"])
    assert run.code == 1 and "one of --install" in run.err
    assert invoke(["serve", "--install", "--apply"]).code == 0


def test_a_failed_bootstrap_names_the_desktop_terminal_and_rerunning_clears_it(
    launchd: FakeLaunchctl, monkeypatch
) -> None:
    def failing(argv):
        return (113, "", "no") if argv[0] == "print" else (5, "", "Bootstrap failed: 5")

    monkeypatch.setattr(cmd_controller, "launchctl_runner", failing)
    run = invoke(["serve", "--install", "--apply"])
    assert run.code == 1 and "Bootstrap failed: 5" in run.err and "desktop session" in run.err
    monkeypatch.setattr(cmd_controller, "launchctl_runner", launchd)
    assert invoke(["serve", "--install", "--apply"]).code == 0


def test_kyo472_c1_a_serve_reinstall_waits_for_the_old_job_before_bootstrapping(
    launchd: FakeLaunchctl, monkeypatch
) -> None:
    """kyo serve reinstalls through the same helper: a bootout whose job stays listed
    for two more prints gets no bootstrap until the print says it is gone."""
    assert invoke(["serve", "--install", "--apply"]).code == 0
    service = f"gui/{os.getuid()}/dev.kyosei.serve"
    lingering = {"left": 0}
    sleeps: list[float] = []
    monkeypatch.setattr(cmd_controller, "launchctl_sleep", sleeps.append)

    def slow_bootout(argv):
        if argv[0] == "bootout":
            result = launchd(argv)
            lingering["left"] = 2
            return result
        if argv[0] == "print" and lingering["left"]:
            lingering["left"] -= 1
            launchd.calls.append(list(argv))
            return 0, "", ""
        if argv[0] == "bootstrap" and lingering["left"]:  # pragma: no cover - the bug
            raise AssertionError("bootstrapped while the old job was still listed")
        return launchd(argv)

    monkeypatch.setattr(cmd_controller, "launchctl_runner", slow_bootout)
    launchd.calls.clear()
    assert invoke(["serve", "--install", "--apply"]).code == 0
    assert [call[0] for call in launchd.calls] == [
        "print", "bootout", "print", "print", "print", "bootstrap",
    ]  # fmt: skip
    assert len(sleeps) == 2 and service in launchd.loaded


def test_kyo472_c2_serve_bootstrap_failing_twice_says_not_loaded_and_names_the_line(
    launchd: FakeLaunchctl, monkeypatch, no_real_managers: Path
) -> None:
    attempts = []

    def failing(argv):
        if argv[0] == "bootstrap":
            attempts.append(argv)
            return 5, "", "Bootstrap failed: 5: Input/output error"
        return launchd(argv)

    monkeypatch.setattr(cmd_controller, "launchctl_runner", failing)
    run = invoke(["serve", "--install", "--apply"])
    assert run.code == 1 and len(attempts) == 2
    line = cmd_controller.bootstrap_line(plist_path(no_real_managers))
    assert f"gui/{os.getuid()}/dev.kyosei.serve is NOT loaded" in run.err
    assert f"`{line}`" in run.err
    # The remedy: that exact line, run where bootstrap works, loads the agent.
    monkeypatch.setattr(cmd_controller, "launchctl_runner", launchd)
    assert launchd(shlex.split(line)[1:])[0] == 0
    assert f"gui/{os.getuid()}/dev.kyosei.serve" in launchd.loaded


def test_a_failed_systemd_enable_names_the_user_instance_and_rerunning_clears_it(
    systemd: FakeSystemctl, monkeypatch
) -> None:
    """serve_install.py:install#1 -- its remedy (`kyo serve --install --apply` again, once
    `systemctl --user status` answers) is run here to exit 0."""

    def failing(argv):
        if argv[0] == "enable":
            return 1, "", "Failed to connect to bus"
        return systemd(argv)

    monkeypatch.setattr(serve_install, "systemctl_runner", failing)
    run = invoke(["serve", "--install", "--apply"])
    assert run.code == 1 and "systemctl --user" in run.err and "--install --apply" in run.err
    monkeypatch.setattr(serve_install, "systemctl_runner", systemd)
    assert invoke(["serve", "--install", "--apply"]).code == 0


def test_a_failed_bootout_leaves_the_plist_and_rerunning_uninstall_clears_it(
    launchd: FakeLaunchctl, monkeypatch, no_real_managers: Path
) -> None:
    """serve_install.py:uninstall#0 -- its remedy (`kyo serve --uninstall --apply` again,
    from the desktop session) is run here to exit 0, and the plist is then gone."""
    assert invoke(["serve", "--install", "--apply"]).code == 0

    def failing(argv):
        return (5, "", "Boot-out failed: 5") if argv[0] == "bootout" else launchd(argv)

    monkeypatch.setattr(cmd_controller, "launchctl_runner", failing)
    run = invoke(["serve", "--uninstall", "--apply"])
    assert run.code == 1 and "--uninstall --apply" in run.err and "desktop session" in run.err
    assert plist_path(no_real_managers).is_file()
    monkeypatch.setattr(cmd_controller, "launchctl_runner", launchd)
    assert invoke(["serve", "--uninstall", "--apply"]).code == 0
    assert not plist_path(no_real_managers).exists()


def test_a_failed_restart_names_install_apply_and_running_it_clears_it(
    launchd: FakeLaunchctl, monkeypatch
) -> None:
    """serve_install.py:restart#1 -- its remedy (`kyo serve --install --apply` reloads the
    agent) is run here, after which --restart exits 0."""
    assert invoke(["serve", "--install", "--apply"]).code == 0

    def failing(argv):
        return (1, "", "kickstart failed") if argv[0] == "kickstart" else launchd(argv)

    monkeypatch.setattr(cmd_controller, "launchctl_runner", failing)
    run = invoke(["serve", "--restart"])
    assert run.code == 1 and "`kyo serve --install --apply`" in run.err
    monkeypatch.setattr(cmd_controller, "launchctl_runner", launchd)
    assert invoke(["serve", "--install", "--apply"]).code == 0
    assert invoke(["serve", "--restart"]).code == 0


# ------------------------------------------------------------ c2: status, restart


def test_status_with_no_agent_and_nothing_on_the_port(
    launchd: FakeLaunchctl, no_real_managers: Path
) -> None:
    port = free_port()
    run = invoke(["serve", "--status", "--port", str(port), "--json"])
    assert run.code == 0, run
    assert run.json["agent"]["installed"] is False and run.json["agent"]["loaded"] is False
    assert run.json["answering"]["state"] == "nothing" and run.json["stale"] is None
    human = invoke(["serve", "--status", "--port", str(port)])
    assert "none installed" in human.out and "nothing answers" in human.out


def test_status_names_the_agent_the_server_answering_and_whether_it_is_stale(
    launchd: FakeLaunchctl, kyo_server, no_real_managers: Path
) -> None:
    server = kyo_server()
    launchd.pid = server.pid
    assert invoke(["serve", "--install", "--apply", "--port", str(server.port)]).code == 0
    # The agent's own port is probed when none is asked for.
    run = invoke(["serve", "--status", "--json"])
    assert run.code == 0, run
    assert run.json["agent"]["installed"] is True and run.json["agent"]["loaded"] is True
    assert run.json["agent"]["pid"] == server.pid
    answering = run.json["answering"]
    assert answering["state"] == "kyo-serve" and answering["port"] == server.port
    assert answering["pid"] == server.pid and answering["build"]
    assert run.json["stale"] is False
    assert "-- the agent" in invoke(["serve", "--status"]).out

    server.started_version = {**server.started_version, "commit": "an-older-build"}
    run = invoke(["serve", "--status"])
    assert run.code == 0 and "stale: this server started as" in run.out
    assert "`kyo serve --restart` restarts it" in run.out


def test_status_names_something_else_on_the_port(launchd: FakeLaunchctl, listener) -> None:
    port = listener.getsockname()[1]
    run = invoke(["serve", "--status", "--port", str(port), "--json"])
    assert run.code == 0, run
    assert run.json["answering"]["state"] == "other"


def test_restart_kickstarts_the_agent_and_refuses_naming_install_when_none_is_loaded(
    launchd: FakeLaunchctl,
) -> None:
    run = invoke(["serve", "--restart"])
    assert run.code == 1 and "`kyo serve --install --apply`" in run.err
    assert invoke(["serve", "--install", "--apply"]).code == 0
    run = invoke(["serve", "--restart"])
    assert run.code == 0, run
    assert launchd.calls[-1] == ["kickstart", "-k", f"gui/{os.getuid()}/dev.kyosei.serve"]


def test_restart_on_systemd_restarts_the_unit(systemd: FakeSystemctl) -> None:
    assert invoke(["serve", "--restart"]).code == 1
    assert invoke(["serve", "--install", "--apply"]).code == 0
    assert invoke(["serve", "--restart"]).code == 0
    assert systemd.calls[-1] == ["restart", "kyosei-serve.service"]


# ----------------------------------------------------- c3: a held port at start


def test_start_refuses_when_another_kyo_serve_answers_naming_its_pid_and_build(
    kyo_server,
) -> None:
    server = kyo_server()
    run = invoke(["serve", "--port", str(server.port)])
    assert run.code == 1, run
    assert f"pid {server.pid}" in run.err
    assert server.started_version["line"] in run.err
    assert "another kyo serve" in run.err and "--port N" in run.err
    # The remedies: another port is free to serve on, and so is this one once the
    # other server stops.
    serve_install.refuse_if_held("127.0.0.1", free_port())
    server.shutdown()
    server.server_close()
    serve_install.refuse_if_held("127.0.0.1", server.port)


def test_start_refuses_when_something_else_holds_the_port_naming_port(listener) -> None:
    port = listener.getsockname()[1]
    run = invoke(["serve", "--port", str(port)])
    assert run.code == 1, run
    assert "something other than kyo serve" in run.err and "--port N" in run.err
    assert str(port) in run.err
    serve_install.refuse_if_held("127.0.0.1", free_port())  # the remedy: another port

