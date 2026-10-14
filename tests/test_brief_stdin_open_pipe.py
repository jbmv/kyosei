"""KYO-442: a session-start brief never waits on a stdin pipe that stays open."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from kyosei import cmd_brief


def _spawn(root: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "kyosei", "--root", str(root), "brief", "--session-start"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_an_open_silent_pipe_does_not_hang_the_brief(project: Path) -> None:
    proc = _spawn(project)
    started = time.monotonic()
    try:
        proc.wait(timeout=10)  # stdin is held open and never written or closed
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("kyo brief --session-start waited on an open stdin pipe (KYO-442)")
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
    assert proc.returncode == 0, proc.stderr.read() if proc.stderr else ""
    assert time.monotonic() - started < 10


def test_a_payload_on_a_pipe_left_open_is_still_read(monkeypatch) -> None:
    """The hook's payload arrives whole; the reader stops at a complete JSON object."""
    import os

    read_end, write_end = os.pipe()
    os.write(write_end, json.dumps({"session_id": "abc-123"}).encode())
    with os.fdopen(read_end) as reader:
        monkeypatch.setattr(sys, "stdin", reader)
        assert cmd_brief._hook_session_id() == "abc-123"
    os.close(write_end)
