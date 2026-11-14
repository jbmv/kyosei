"""KYO-944.c5: the test server's tmux is a wrapper, and no call of it reaches a real socket.

``kyo serve`` asks the tmux servers ``-L default`` and ``-L kyosei`` for panes, and on the
machine the harness runs on those hold the person's live sessions. The first test is of the
wrapper alone, with a stand-in for the real tmux that records what it was run with. The
second starts the real server with the wrapper on its ``PATH``, gives it a session to look
for, and reads the wrapper's log of every call.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

import harness


def _stand_in(directory: Path) -> tuple[Path, Path]:
    """A program in place of the real tmux that records its arguments, one call a line."""
    record = directory / "real.log"
    program = directory / "real-tmux"
    program.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{record}"\n', encoding="utf-8")
    program.chmod(0o755)
    return program, record


def test_the_wrapper_maps_the_two_servers_and_refuses_every_other_call(tmp_path: Path) -> None:
    real, record = _stand_in(tmp_path)
    private = tmp_path / "private.sock"
    log = tmp_path / "tmux.log"
    wrapper = harness.write_tmux_wrapper(
        tmp_path / "bin", real_tmux=str(real), socket_path=private, log=log
    )

    def call(*arguments: str) -> int:
        environment = {"PATH": "/usr/bin:/bin", "TMUX": "/private/tmp/tmux-501/default,1,0"}
        return subprocess.run([str(wrapper), *arguments], env=environment, check=False).returncode

    assert call("-L", "default", "list-panes", "-a") == 0
    assert call("-L", "kyosei", "send-keys", "-t", "%1", "two words") == 0
    refused = [
        ("list-panes", "-a"),
        ("kill-server",),
        ("-L", "other", "list-panes"),
        ("-L", "kyosei-other", "list-panes"),
        ("-S", "/private/tmp/tmux-501/default", "kill-server"),
        ("-Ldefault", "list-panes"),
        (),
    ]
    for arguments in refused:
        assert call(*arguments) == 1, arguments

    # The real tmux was run twice, each time against the private socket and with no
    # configuration file, and never for a refused call.
    assert record.read_text(encoding="utf-8").splitlines() == [
        f"-S {private} -f /dev/null list-panes -a",
        f"-S {private} -f /dev/null send-keys -t %1 two words",
    ]
    calls = harness.read_tmux_log(log)
    assert [call.verdict for call in calls] == ["mapped", "mapped"] + ["refused"] * len(refused)
    assert harness.calls_reaching_another_socket(log, private) == []
    # The reading that finds none would find one: a line naming another socket is reported.
    with log.open("a", encoding="utf-8") as stream:
        stream.write("mapped\t/private/tmp/tmux-501/default\t-L default list-panes\n")
    assert len(harness.calls_reaching_another_socket(log, private)) == 1


def test_without_a_tmux_installed_the_wrapper_refuses_everything(tmp_path: Path) -> None:
    log = tmp_path / "tmux.log"
    wrapper = harness.write_tmux_wrapper(
        tmp_path / "bin", real_tmux=None, socket_path=tmp_path / "s", log=log
    )
    done = subprocess.run([str(wrapper), "-L", "default", "list-panes"], check=False)
    assert done.returncode == 1
    assert [call.verdict for call in harness.read_tmux_log(log)] == ["refused"]


def test_no_tmux_call_of_the_test_server_reaches_a_real_socket(run: harness.Run) -> None:
    project = harness.make_project(run)
    environment = run.server_environment()
    # The server finds the wrapper, and so never falls back to a tmux at a fixed path.
    assert shutil.which("tmux", path=environment["PATH"]) == str(run.wrapper)
    for name in ("TMUX", "TMUX_TMPDIR", "KYOSEI_CONTROLLER_SOCKET"):
        assert name not in environment

    server = harness.start_server(run, project)
    # A live session for the server to look for, recorded as Claude Code and the plugin's
    # hook record one: the process record, with the server's own pid, which is running, and
    # the session's start told to `kyo sessions --hook`. Looking for its pane calls tmux.
    session = "0f0f0f0f-1111-4222-8333-444444444444"
    record = {"pid": server.process.pid, "sessionId": session, "status": "busy", "cwd": str(project)}
    (run.claude / "sessions" / f"{server.process.pid}.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    transcript = run.claude / "projects" / "-checked" / f"{session}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("", encoding="utf-8")
    started = {
        "hook_event_name": "SessionStart",
        "session_id": session,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "source": "startup",
    }
    run.kyo("sessions", "--hook", cwd=project, stdin=json.dumps(started))

    def get(path: str) -> int:
        request = urllib.request.Request(
            f"{server.url}{path}", headers={"Authorization": f"Bearer {server.token}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as answer:  # noqa: S310
                answer.read()
                return answer.status
        except urllib.error.HTTPError as refusal:
            refusal.read()
            return refusal.code

    assert get(f"/v1/projects/{server.name}/sessions") == 200
    calls = harness.poll("the server to call tmux", lambda: harness.read_tmux_log(run.tmux_log))

    asked = {call.arguments.split(" list-panes")[0] for call in calls if "list-panes" in call.arguments}
    assert asked == {"-L default", "-L kyosei"}, calls
    assert harness.calls_reaching_another_socket(run.tmux_log, run.tmux_socket) == []
    assert all(call.socket in ("-", str(run.tmux_socket)) for call in calls)
    # No tmux server was started on the private socket by asking for panes.
    assert not run.tmux_socket.exists()
