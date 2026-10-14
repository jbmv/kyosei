"""KYO-193: a long run written to a file keeps its own exit status and its whole log.

The defect: ``make test 2>&1 | tail -3`` reports ``tail``'s exit status (success whenever
it could read its input) and, because ``tail`` buffers until its input closes, leaves a
zero-byte log when the run is interrupted, and measurements have been lost that way.
The Makefile's ``logged`` macro is the mechanism: with ``LOG=<file>`` the run
goes to the file, its last lines and the command's own status are printed, and the recipe
fails when that status is nonzero. These tests drive the macro through the ``logged``
target with a command whose status and output are known, so they fail the moment the
status stops propagating or the log stops being written — the rule in
``kyosei/procedures.md`` cites this file as its ``enforced-by``, and a cited test that
would pass with the mechanism deleted is the failure this project keeps meeting.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from kyosei.procedures import orchestrator_rules, worker_rules

from .this_store import store_path

REPO = Path(__file__).resolve().parents[1]
RULE_TITLE = "Write a long run to a file and read the file"

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def _make_logged(cmd: str, log: Path | None) -> subprocess.CompletedProcess[str]:
    # This suite is itself run as `make test LOG=<file>`, and a command-line variable
    # reaches every nested make through MAKEFLAGS — so "no LOG" has to be said out loud
    # (`LOG=` overrides the inherited value) and the inherited make state scrubbed, or
    # the without-LOG case silently tests the with-LOG one. It did, once.
    args = ["make", "-s", "logged", f"CMD={cmd}", f"LOG={log if log is not None else ''}"]
    env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL")}
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, check=False, env=env)


def test_a_failing_command_fails_the_recipe_and_its_own_status_is_printed(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    result = _make_logged('sh -c "echo line-in-the-log; exit 3"', log)
    # `make` reports 2 for any failed recipe; the command's own status travels on the
    # printed line, which is where the rule tells a reader to look.
    assert result.returncode != 0, result.stdout + result.stderr
    assert "exit=3" in result.stdout, result.stdout
    assert log.read_text() == "line-in-the-log\n"


def test_a_passing_command_passes_and_the_whole_run_is_in_the_file(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    body = "; ".join(f"echo line-{i}" for i in range(20))
    result = _make_logged(f'sh -c "{body}"', log)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "exit=0" in result.stdout
    # The terminal shows only the tail; the file holds every line, which is the half of
    # the defect a pipe through `tail` loses.
    assert "line-0" not in result.stdout and "line-19" in result.stdout
    assert log.read_text().splitlines() == [f"line-{i}" for i in range(20)]


def test_without_log_the_target_runs_the_command_unchanged() -> None:
    result = _make_logged('sh -c "echo straight-through; exit 4"', None)
    assert result.returncode != 0
    assert "straight-through" in result.stdout
    assert "exit=" not in result.stdout


@pytest.mark.parametrize(
    "ledger",
    [
        # This repository's own store copy, present only in the maintainer's checkout.
        pytest.param(
            str(store_path("procedures.md")),
            marks=pytest.mark.skipif(
                not store_path("procedures.md").is_file(),
                reason="this repository's own store is not in this checkout",
            ),
        ),
        "src/kyosei/defaults/procedures.md",
    ],
)
def test_the_rule_reaches_both_audiences_and_names_both_failure_modes(ledger: str) -> None:
    text = (REPO / ledger).read_text(encoding="utf-8")
    for audience in (worker_rules, orchestrator_rules):
        matches = [r for r in audience(text) if RULE_TITLE in r.title]
        assert len(matches) == 1, [r.title for r in audience(text)]
    (rule,) = [r for r in worker_rules(text) if RULE_TITLE in r.title]
    # The parsed Rule carries only what the brief injects; the reason lives in the section
    # body, so read the section itself for the two failure modes.
    start = text.index(f"### {rule.title}")
    end = text.find("\n### ", start + 1)
    section = text[start : end if end != -1 else len(text)]
    # KYO-193.c1: both failure modes stated where the reader meets them; KYO-193.c2: the
    # honest statement that a hand-typed pipe is caught by nothing.
    assert "exit status" in section
    assert "zero-byte log" in section
    assert "Nothing catches a pipe typed by hand" in rule.enforced_by
    assert Path(__file__).name in rule.enforced_by
