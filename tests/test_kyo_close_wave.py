"""KYO-536: ``kyo close-wave`` — a merged wave's closing sequence as one command.

c1: the moves, the generators and one commit of store and generated files, refused
before any write when a record cannot finish (an unsettled criterion, an open docs
obligation), naming it. c2: no suite is run, a recorded pass at this code is required,
and the remedy the refusal names clears it. c3: after-steps from configuration, printed
and run only with ``--run-after``; ``--dry-run`` prints the plan and writes nothing.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from kyosei.cmd_close_wave import (
    BLOCKER_RECORD,
    BLOCKER_SUITE,
    BLOCKER_TREE,
    CONTRACT_CLOSE_WAVE_NOT_READY,
    CONTRACT_CLOSE_WAVE_PASS_NOT_RECORDABLE,
    PASS_FILE_NAME,
)
from kyosei.contracts import load_status_ladder
from kyosei.errors import EXIT_CONTRACT, EXIT_ERROR, EXIT_USAGE

from .conftest import git_init, invoke, new_record, valid_report, verify_criterion

#: The suite these tests declare. `true` is a real command a shell can run, so the
#: remedy line a refusal prints can be run here exactly as printed.
SUITE = "true"


# ------------------------------------------------------------------------ helpers


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _py(code: str) -> str:
    """A shell command line running ``code`` with this interpreter."""
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def _write_gen(path: str, text: str = "generated\n") -> str:
    return _py(
        f"import pathlib; p = pathlib.Path({path!r}); "
        f"p.parent.mkdir(parents=True, exist_ok=True); p.write_text({text!r})"
    )


def _declare(project: Path, block: str) -> None:
    config = project / "kyosei" / "config.yml"
    config.write_text(config.read_text(encoding="utf-8") + block, encoding="utf-8")


def _close_wave_config(*, suite: list[str] | None = (SUITE,), generate=(), after=()) -> str:
    lines = ["close_wave:\n"]
    if suite:
        lines += ["  suite:\n", *(f"    - {json.dumps(c)}\n" for c in suite)]
    if generate:
        lines += ["  generate:\n", *(f"    - {json.dumps(c)}\n" for c in generate)]
    if after:
        lines += ["  after:\n", *(f"    - {json.dumps(c)}\n" for c in after)]
    return "".join(lines)


@pytest.fixture
def wave(project: Path, kyo):
    """A git project declaring a suite, one generator and one after-step."""
    _declare(
        project,
        _close_wave_config(
            generate=[_write_gen("docs/generated.md")],
            after=[_write_gen("after-marker.txt", "ran\n")],
        ),
    )
    git_init(project)
    assert _git(project, "status", "--porcelain") == "", "fixture must start clean"
    return project


def _finished_record(kyo) -> str:
    record = new_record(kyo, criteria=["it works"])
    verify_criterion(kyo, f"{record}.c1", method="machine-checked")
    return record


def _status(kyo, record: str) -> str:
    return kyo("show", record, "--json").json["status"]


def _complete(project: Path) -> str:
    return load_status_ladder(project / "kyosei" / "statuses.yml").complete


def _export(project: Path) -> dict[str, bytes]:
    root = project / "kyosei" / "export"
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def _pass_file(project: Path) -> Path:
    return project / ".git" / PASS_FILE_NAME


def _record_pass(kyo, command: str = SUITE):
    result = kyo("close-wave", "--suite-passed", command)
    assert result.code == 0, result
    return result


def _snapshot(project: Path) -> tuple:
    pass_file = _pass_file(project)
    return (
        _export(project),
        _git(project, "rev-parse", "HEAD"),
        _git(project, "status", "--porcelain", "--untracked-files=all"),
        pass_file.read_bytes() if pass_file.is_file() else None,
    )


def _run_printed_remedy(project: Path, text: str) -> None:
    """Run the ``<suite> && kyo close-wave --suite-passed ...`` line a refusal printed:
    the suite part through a shell, the kyo part in-process, chained on the exit."""
    line = next(part for part in text.split("`") if "kyo close-wave --suite-passed" in part)
    suite, _, kyo_part = line.partition(" && ")
    assert subprocess.run(suite, shell=True, cwd=project, check=False).returncode == 0
    argv = shlex.split(kyo_part)
    assert argv[0] == "kyo"
    result = invoke(["--root", str(project), *argv[1:]])
    assert result.code == 0, result


# ------------------------------------------------------------------------------- c1


def test_close_wave_moves_generates_and_commits_store_and_generated_files_together(
    kyo, wave: Path
) -> None:
    record = _finished_record(kyo)
    _commit(wave, "store: record filed and verified")
    _record_pass(kyo)
    before = _git(wave, "rev-parse", "HEAD")

    result = kyo("close-wave", record, "--json")

    assert result.code == 0, result
    assert _status(kyo, record) == _complete(wave)
    assert (wave / "docs" / "generated.md").read_text() == "generated\n"
    # One commit, on top of the old HEAD, carrying the export AND the generated file.
    assert _git(wave, "rev-parse", "HEAD^") == before
    changed = _git(wave, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert "docs/generated.md" in changed
    assert f"kyosei/export/{record}.json" in changed
    assert record in _git(wave, "log", "-1", "--format=%s")
    # Nothing left behind outside the store, and the after-step only printed.
    assert _git(wave, "status", "--porcelain", "--", ".", ":(exclude)kyosei") == ""
    assert not (wave / "after-marker.txt").exists()
    assert result.json["complete"] is True
    assert [c["sha"] for c in result.json["commits"]] == [_git(wave, "rev-parse", "HEAD")]


def test_a_record_with_an_unsettled_criterion_is_refused_before_any_write_and_named(
    kyo, wave: Path
) -> None:
    finished = _finished_record(kyo)
    unfinished = new_record(kyo, criteria=["it works", "it is fast"])
    verify_criterion(kyo, f"{unfinished}.c1", method="machine-checked")
    _commit(wave, "store")
    _record_pass(kyo)
    before = _snapshot(wave)

    result = kyo("close-wave", finished, unfinished)

    assert result.code == EXIT_CONTRACT, result
    assert CONTRACT_CLOSE_WAVE_NOT_READY in result.err
    assert BLOCKER_RECORD in result.err
    assert unfinished in result.err and f"{unfinished}.c2" in result.err
    # Before ANY write: the finishable record was not moved either.
    assert _snapshot(wave) == before
    assert _status(kyo, finished) != _complete(wave)
    assert not (wave / "docs" / "generated.md").exists()

    # Settling the criterion it named clears the refusal.
    verify_criterion(kyo, f"{unfinished}.c2", method="machine-checked")
    cleared = kyo("close-wave", finished, unfinished)
    assert cleared.code == 0, cleared
    assert _status(kyo, unfinished) == _complete(wave)


def test_a_record_with_an_open_docs_obligation_is_refused_naming_it(
    kyo, wave: Path, tmp_path: Path
) -> None:
    (wave / "docs").mkdir(exist_ok=True)
    (wave / "docs" / "architecture.md").write_text(
        "# Architecture\n\n## How the app works\n\ngoverns: src/app.py\n\nProse.\n",
        encoding="utf-8",
    )
    (wave / "src").mkdir()
    (wave / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    _commit(wave, "baseline")
    record = new_record(kyo, criteria=["the app works"])
    touches = tmp_path / "touches.txt"
    touches.write_text("src/app.py\n", encoding="utf-8")
    assert kyo("edit", record, "--touches-file", str(touches)).code == 0
    _commit(wave, "store: record filed")
    _git(wave, "checkout", "-q", "-b", "work")
    (wave / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
    sha = _commit(wave, "work")
    _git(wave, "checkout", "-q", "main")
    dispatched = kyo("dispatch", record, "--branch", "work", "--json")
    assert dispatched.code == 0, dispatched
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatched.json["dispatch_id"], record_id=record, worked_from_sha=sha
            )
        ),
        encoding="utf-8",
    )
    reported = kyo("report", dispatched.json["dispatch_id"], "--file", str(report), "--json")
    assert reported.code == 0, reported
    obligation = reported.json["docs_obligations"]["raised"][0]["id"]
    verify_criterion(kyo, f"{record}.c1", method="machine-checked")
    _git(wave, "merge", "-q", "--no-edit", "work")
    _commit(wave, "store")
    _record_pass(kyo)
    before = _snapshot(wave)

    result = kyo("close-wave", record)

    assert result.code == EXIT_CONTRACT, result
    assert BLOCKER_RECORD in result.err
    assert "docs/architecture.md" in result.err
    assert _snapshot(wave) == before

    settled = kyo(
        "docs", "settle", obligation, "--verdict", "not-affected", "--method", "read it",
        "--why", "the prose still holds",
    )  # fmt: skip
    assert settled.code == 0, settled
    assert kyo("close-wave", record).code == 0
    assert _status(kyo, record) == _complete(wave)


def test_a_rerun_leaves_a_completed_record_where_it_is(kyo, wave: Path) -> None:
    record = _finished_record(kyo)
    _commit(wave, "store")
    _record_pass(kyo)
    assert kyo("close-wave", record).code == 0
    _record_pass(kyo)  # the commit changed the generated file, so the code changed

    again = kyo("close-wave", record)

    assert again.code == 0, again
    assert f"{record}: already {_complete(wave)}" in again.out
    assert "nothing to commit" in again.out


def test_a_failing_generator_stops_before_the_commit_and_says_so(kyo, project: Path) -> None:
    _declare(project, _close_wave_config(generate=[_py("raise SystemExit(3)")]))
    git_init(project)
    record = _finished_record(kyo)
    _commit(project, "store")
    _record_pass(kyo)
    head = _git(project, "rev-parse", "HEAD")

    result = kyo("close-wave", record)

    assert result.code == EXIT_ERROR, result
    assert "generate FAILED (exit 3)" in result.err
    assert "INCOMPLETE" in result.err
    assert _git(project, "rev-parse", "HEAD") == head


# ------------------------------------------------------------------------------- c2


def test_no_recorded_pass_is_refused_and_the_named_remedy_clears_it(kyo, wave: Path) -> None:
    record = _finished_record(kyo)
    _commit(wave, "store")
    before = _snapshot(wave)

    refused = kyo("close-wave", record)

    assert refused.code == EXIT_CONTRACT, refused
    assert BLOCKER_SUITE in refused.err
    assert f"{SUITE} && kyo close-wave --suite-passed {SUITE}" in refused.err
    assert _snapshot(wave) == before

    _run_printed_remedy(wave, refused.err)
    cleared = kyo("close-wave", record)
    assert cleared.code == 0, cleared
    assert _status(kyo, record) == _complete(wave)


def test_close_wave_runs_no_suite_itself(kyo, project: Path) -> None:
    """The declared suite writes a marker if it is ever run; close-wave never runs it."""
    marker = project / "suite-ran.txt"
    (project / "suite.sh").write_text("echo ran > suite-ran.txt\n", encoding="utf-8")
    suite = "sh suite.sh"
    _declare(project, _close_wave_config(suite=[suite]))
    git_init(project)
    record = _finished_record(kyo)
    _commit(project, "store")
    assert kyo("close-wave", "--suite-passed", suite).code == 0
    assert kyo("close-wave", record).code == 0
    assert not marker.exists()


def test_a_pass_survives_a_store_only_commit_and_goes_stale_when_code_changes(
    kyo, wave: Path
) -> None:
    record = _finished_record(kyo)
    _commit(wave, "store")
    _record_pass(kyo)
    new_record(kyo, criteria=["another"])
    _commit(wave, "store-only commit")

    dry = kyo("close-wave", record, "--dry-run", "--json")
    assert dry.json["ready"] is True, dry
    assert "differs only under" in kyo("close-wave", record, "--dry-run").out

    (wave / "code.py").write_text("y = 1\n", encoding="utf-8")
    _commit(wave, "a code change")
    stale = kyo("close-wave", record)
    assert stale.code == EXIT_CONTRACT, stale
    assert BLOCKER_SUITE in stale.err


def test_a_pass_for_an_undeclared_command_is_not_recorded(kyo, wave: Path) -> None:
    result = kyo("close-wave", "--suite-passed", "make something-else")
    assert result.code == EXIT_CONTRACT, result
    assert CONTRACT_CLOSE_WAVE_PASS_NOT_RECORDABLE in result.err
    assert not _pass_file(wave).exists()


def test_a_pass_against_uncommitted_code_is_not_recorded(kyo, wave: Path) -> None:
    (wave / "wip.py").write_text("z = 1\n", encoding="utf-8")
    result = kyo("close-wave", "--suite-passed", SUITE)
    assert result.code == EXIT_CONTRACT, result
    assert "wip.py" in result.err
    assert not _pass_file(wave).exists()


def test_the_orchestrator_only_commands_are_the_fallback_suite(kyo, project: Path) -> None:
    """Without `close_wave: suite:`, any one declared whole-suite command will do, and a
    `make` spelling with a variable (this repository's `make test LOG=...`) matches."""
    _declare(project, "orchestrator_only_commands:\n  - make test\n  - npm run check\n")
    git_init(project)
    record = _finished_record(kyo)
    _commit(project, "store")
    assert kyo("close-wave", "--suite-passed", "make test LOG=/tmp/t.log").code == 0

    result = kyo("close-wave", record)

    assert result.code == 0, result
    assert "suite rule: any one of" in result.out


def test_a_project_declaring_no_suite_is_told_how_to_declare_one(kyo, project: Path) -> None:
    git_init(project)
    record = _finished_record(kyo)
    _commit(project, "store")
    refused = kyo("close-wave", record)
    assert refused.code == EXIT_CONTRACT, refused
    assert "declares no whole-suite command" in refused.err
    assert "close_wave: suite:" in refused.err

    _declare(project, _close_wave_config())
    _commit(project, "declare the suite")
    _record_pass(kyo)
    assert kyo("close-wave", record).code == 0


def test_uncommitted_code_refuses_the_close(kyo, wave: Path) -> None:
    record = _finished_record(kyo)
    _commit(wave, "store")
    _record_pass(kyo)
    (wave / "stray.py").write_text("q = 1\n", encoding="utf-8")
    result = kyo("close-wave", record)
    assert result.code == EXIT_CONTRACT, result
    assert BLOCKER_TREE in result.err and "stray.py" in result.err


# ------------------------------------------------------------------------------- c3


def test_after_steps_are_printed_and_run_only_with_run_after(kyo, wave: Path) -> None:
    first = _finished_record(kyo)
    _commit(wave, "store")
    _record_pass(kyo)
    printed = kyo("close-wave", first)
    assert printed.code == 0, printed
    assert "pass --run-after to run them" in printed.out
    assert "after-marker.txt" in printed.out
    assert not (wave / "after-marker.txt").exists()

    second = _finished_record(kyo)
    _commit(wave, "store")
    _record_pass(kyo)
    ran = kyo("close-wave", second, "--run-after", "--json")
    assert ran.code == 0, ran
    assert (wave / "after-marker.txt").read_text() == "ran\n"
    assert [step["exit_code"] for step in ran.json["after"]] == [0]


def test_dry_run_prints_the_whole_plan_and_writes_nothing(kyo, wave: Path) -> None:
    record = _finished_record(kyo)
    _commit(wave, "store")
    before = _snapshot(wave)

    result = kyo("close-wave", record, "--suite-passed", SUITE, "--dry-run", "--run-after")

    assert result.code == 0, result
    out = result.out
    assert "ready — nothing written" in out
    assert "would record" in out
    assert f"{record}:" in out and _complete(wave) in out
    assert "docs/generated.md" in out  # the generator
    assert "after-marker.txt" in out  # the after-step
    assert "3. commit" in out
    assert _snapshot(wave) == before
    assert not (wave / "docs" / "generated.md").exists()
    assert not (wave / "after-marker.txt").exists()
    assert _status(kyo, record) != _complete(wave)


def test_dry_run_says_what_it_would_refuse_and_still_writes_nothing(kyo, wave: Path) -> None:
    record = new_record(kyo, criteria=["not yet"])
    _commit(wave, "store")
    before = _snapshot(wave)

    result = kyo("close-wave", record, "--dry-run", "--json")

    assert result.code == 0, result
    assert result.json["ready"] is False
    blockers = " ".join(result.json["blockers"])
    assert BLOCKER_RECORD in blockers and BLOCKER_SUITE in blockers
    assert "WOULD REFUSE" in kyo("close-wave", record, "--dry-run").out
    assert _snapshot(wave) == before


def test_a_malformed_close_wave_key_refuses_this_command_and_no_other(kyo, project: Path) -> None:
    _declare(project, "close_wave:\n  generte:\n    - echo hi\n")
    git_init(project)
    refused = kyo("close-wave", "--suite-passed", SUITE)
    assert refused.code == EXIT_ERROR, refused
    assert "generte" in refused.err
    assert kyo("list").code == 0


def test_an_empty_invocation_is_refused_and_naming_work_clears_it(kyo, wave: Path) -> None:
    refused = kyo("close-wave")
    assert refused.code == EXIT_USAGE, refused
    assert "--suite-passed" in refused.err
    assert kyo("close-wave", "--suite-passed", SUITE).code == 0


def test_an_empty_close_wave_step_is_refused_and_giving_it_a_command_clears_it(
    kyo, project: Path
) -> None:
    config = project / "kyosei" / "config.yml"
    original = config.read_text(encoding="utf-8")
    _declare(project, _close_wave_config(after=[""]))
    git_init(project)
    refused = kyo("close-wave", "--suite-passed", SUITE)
    assert refused.code == EXIT_ERROR, refused
    assert "close_wave.after" in refused.err and "hand edit" in refused.err

    config.write_text(original + _close_wave_config(after=["echo done"]), encoding="utf-8")
    _commit(project, "fix the after-step")
    assert kyo("close-wave", "--suite-passed", SUITE).code == 0


def test_under_the_split_layout_each_repository_gets_its_own_commit(
    split_project: tuple[Path, Path],
) -> None:
    """The store's export is committed in the store's repository, the generated file in
    the code repository, with the same message — never one repository's files in the
    other's history."""
    code, store = split_project

    def kyo(*args: str, stdin: str | None = None):
        return invoke(["--root", str(code), *args], stdin=stdin)

    _declare(store, _close_wave_config(generate=[_write_gen("docs/generated.md")]))
    assert _git(code, "status", "--porcelain") == ""
    record = new_record(kyo, criteria=["it works"])
    verify_criterion(kyo, f"{record}.c1", method="machine-checked")
    _commit(store, "store")
    assert kyo("close-wave", "--suite-passed", SUITE).code == 0

    result = kyo("close-wave", record, "--json")

    assert result.code == 0, result
    assert kyo("show", record, "--json").json["status"] == _complete(store)
    code_files = _git(code, "show", "--name-only", "--format=", "HEAD").splitlines()
    store_files = _git(store, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert code_files == ["docs/generated.md"]
    assert f"kyosei/export/{record}.json" in store_files
    assert _git(code, "log", "-1", "--format=%s") == _git(store, "log", "-1", "--format=%s")


def test_closing_a_record_writes_events(kyo, wave: Path) -> None:
    """tests/test_events.py lists `close-wave` among the commands whose event assertion
    lives beside its own fixture: every store write it makes is a status move, and each
    move is logged like `kyo move`'s."""
    import sqlite3

    record = _finished_record(kyo)
    _commit(wave, "store: record filed and verified")
    _record_pass(kyo)

    def count() -> int:
        with sqlite3.connect(wave / "kyosei" / "kyosei.db") as conn:
            return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    before = count()
    assert kyo("close-wave", record).code == 0
    assert count() > before
