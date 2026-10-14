"""KYO-406: symbol regions are placed in the file the diff's NEW side came from.

Symbol-aware narrowing (KYO-300) tests diff hunks against the regions a claimed symbol
occupies. The hunks come from commits — ``prior_sha..current_sha`` for `kyo sweep`,
``against...branch`` for touch drift — so their new-side line numbers belong to
``current_sha`` and to the branch tip respectively. Both used to read the file from the
checkout instead, which numbers its lines differently whenever it carries uncommitted
edits (the sweep) or lacks the branch's edits (drift). A real change
then narrowed away unflagged, or an edit inside a claimed symbol was reported as
outside it.

Every test here fails when the new-side read is reverted to the working tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kyosei import cmd_sweep, touch_drift

from .conftest import git_init, new_record

MODULE = """\
def alpha():
    a = 1
    return a


def beta():
    b = 2
    return b


def gamma():
    c = 3
    return c
"""

#: Forty lines that name no symbol, prepended to the checkout's copy only. The sweep case's repro.
PREPENDED = "".join(f"# padding line {n}\n" for n in range(40))


def _git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(project: Path, message: str) -> str:
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", message)
    return _git(project, "rev-parse", "HEAD")


def _edit_function(path: Path, name: str, body: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith(f"def {name}("):
            lines[index + 1] = f"    {body}\n"
            break
    else:  # pragma: no cover
        raise AssertionError(f"no def {name} in {path}")
    path.write_text("".join(lines), encoding="utf-8")


def _prepend(path: Path, text: str) -> None:
    path.write_text(text + path.read_text(encoding="utf-8"), encoding="utf-8")


def _promote(kyo, project: Path, *, name: str, touches: list[str]) -> str:
    idea = project / "ideas" / f"{name}.md"
    idea.parent.mkdir(parents=True, exist_ok=True)
    idea.write_text(f"# {name}\n\nDo the thing.\n", encoding="utf-8")
    criteria = project.parent / f"{name}-criteria.txt"
    criteria.write_text("it works\n", encoding="utf-8")
    scope = project.parent / f"{name}-touches.txt"
    scope.write_text("".join(line + "\n" for line in touches), encoding="utf-8")
    result = kyo(
        "promote", str(idea), "--kind", "chore", "--criteria-file", str(criteria),
        "--touches-file", str(scope), "--json",
    )
    assert result.code == 0, result
    return result.json["record"]["id"]


def _declare(kyo, project: Path, record: str, *lines: str) -> None:
    scope = project.parent / f"{record}-touches.txt"
    scope.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    result = kyo("edit", record, "--touches-file", str(scope))
    assert result.code == 0, result


def _flags_for(kyo, record: str) -> list[dict]:
    swept = kyo("sweep", "--json")
    assert swept.code == 0, swept
    assert swept.json["coverage"] == "swept", swept.json
    return [f for f in swept.json["flags"] if f["record_id"] == record]


@pytest.fixture
def read_counter(monkeypatch):
    """Counts `_git_file_at` calls per (module, path), passing each through."""
    calls: list[tuple[str, str, str]] = []

    def wrap(module):
        real = module._git_file_at

        def counted(root, rev, path):
            calls.append((module.__name__, rev, path))
            return real(root, rev, path)

        monkeypatch.setattr(module, "_git_file_at", counted)

    wrap(cmd_sweep)
    wrap(touch_drift)
    return calls


# ------------------------------------------------------------------ the sweep, c1/c2/c6


def _sweep_setup(kyo, project: Path, *, name: str) -> str:
    (project / "module.py").write_text(MODULE, encoding="utf-8")
    record = _promote(kyo, project, name=name, touches=["module.py#beta"])
    git_init(project, commit_message="baseline")
    kyo("sweep", "--json")
    _edit_function(project / "module.py", "beta", "b = 99")
    _commit(project, "edit beta")
    return record


def test_sweep_flags_a_committed_change_inside_the_symbol_on_a_clean_tree(
    kyo, project: Path
) -> None:
    record = _sweep_setup(kyo, project, name="clean")
    flags = _flags_for(kyo, record)
    assert len(flags) == 1, flags
    assert "module.py#beta" in flags[0]["detail"]


def test_sweep_still_flags_it_with_uncommitted_lines_prepended_above_the_symbol(
    kyo, project: Path
) -> None:
    """KYO-406.c1/c6, the repro: clean tree 1 flag, dirty tree 1 flag — not 0.

    Reading the working tree places `beta` forty lines lower than the committed hunk,
    so the change narrowed away and nothing was flagged while the base advanced."""
    record = _sweep_setup(kyo, project, name="dirty")
    _prepend(project / "module.py", PREPENDED)
    flags = _flags_for(kyo, record)
    assert len(flags) == 1, flags
    assert "module.py#beta" in flags[0]["detail"]
    listed = kyo("flag", "list", "--json")
    assert listed.code == 0, listed


def test_sweep_counts_a_path_it_cannot_read_at_current_sha_as_changed(
    kyo, project: Path
) -> None:
    """KYO-406.c2: deleted in the swept commit, but a copy sits in the checkout. The
    checkout's copy must not be used to narrow the deletion away."""
    (project / "module.py").write_text(MODULE, encoding="utf-8")
    record = _promote(kyo, project, name="deleted", touches=["module.py#beta"])
    git_init(project, commit_message="baseline")
    kyo("sweep", "--json")
    (project / "module.py").unlink()
    _commit(project, "delete module")
    # An untracked copy in the checkout, as an interrupted restore would leave it.
    (project / "module.py").write_text(MODULE, encoding="utf-8")
    flags = _flags_for(kyo, record)
    assert len(flags) == 1, flags
    assert "module.py" in flags[0]["detail"]


def test_sweep_reads_each_file_once_per_run(kyo, project: Path, read_counter) -> None:
    """KYO-406.c5: two records, three symbols, one file — one read."""
    (project / "module.py").write_text(MODULE, encoding="utf-8")
    _promote(kyo, project, name="one", touches=["module.py#alpha", "module.py#beta"])
    _promote(kyo, project, name="two", touches=["module.py#gamma"])
    git_init(project, commit_message="baseline")
    kyo("sweep", "--json")
    _edit_function(project / "module.py", "gamma", "c = 99")
    head = _commit(project, "edit gamma")
    read_counter.clear()
    kyo("sweep", "--json")
    reads = [call for call in read_counter if call[0] == cmd_sweep.__name__]
    assert reads == [(cmd_sweep.__name__, head, "module.py")], reads


# ------------------------------------------------------------------- drift, c3/c4/c7


def _branch(project: Path, branch: str, edit) -> str:
    base = _git(project, "rev-parse", "--abbrev-ref", "HEAD")
    _git(project, "checkout", "-q", "-b", branch)
    edit()
    _commit(project, f"work on {branch}")
    _git(project, "checkout", "-q", base)
    return base


def _drift(kyo, record: str, branch: str, against: str) -> tuple[str, dict]:
    dispatch_id = kyo("dispatch", record, "--branch", branch, "--json").json["dispatch_id"]
    result = kyo("touch-drift", dispatch_id, "--against", against, "--json")
    assert result.code == 0, result
    return dispatch_id, result.json["touch_drift"]


def test_drift_places_regions_in_the_branch_tip_not_the_controller_checkout(
    kyo, git_project: Path
) -> None:
    """KYO-406.c3/c7. The branch edits inside `beta`, which it claims. The controller's
    checkout has forty extra lines above it, so read there, `beta` sits forty lines
    lower than the hunk and the edit was reported as outside the claimed symbol."""
    (git_project / "module.py").write_text(MODULE, encoding="utf-8")
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "module.py#beta")
    against = _branch(
        git_project,
        "kyo-406-inside",
        lambda: _edit_function(git_project / "module.py", "beta", "b = 99"),
    )
    _prepend(git_project / "module.py", PREPENDED)  # the controller's own edit

    _, drift = _drift(kyo, record, "kyo-406-inside", against)
    assert drift["symbol_check"] == touch_drift.SYMBOL_CHECK_RUN
    assert drift["outside_claimed_symbols"] == [], drift["outside_claimed_symbols"]


def test_drift_still_finds_a_real_outside_edit_with_a_differing_checkout(
    kyo, git_project: Path
) -> None:
    """The fix must not simply silence the check: a real outside edit is still found."""
    (git_project / "module.py").write_text(MODULE, encoding="utf-8")
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "module.py#alpha")
    against = _branch(
        git_project,
        "kyo-406-outside",
        lambda: _edit_function(git_project / "module.py", "gamma", "c = 99"),
    )
    _prepend(git_project / "module.py", PREPENDED)

    _, drift = _drift(kyo, record, "kyo-406-outside", against)
    outside = drift["outside_claimed_symbols"]
    assert [entry["suggested_touches"] for entry in outside] == [["module.py#gamma"]]


def test_drift_says_not_run_for_a_path_unreadable_at_the_branch_tip(
    kyo, git_project: Path
) -> None:
    """KYO-406.c4: the branch deletes a file claimed by symbol, so there is no tip copy
    to place a region in. That is "not run" for that path — never silently clean — and
    the correction `kyo edit --touches-from-drift` applies does not widen the claim."""
    (git_project / "module.py").write_text(MODULE, encoding="utf-8")
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "module.py#beta")
    against = _branch(
        git_project, "kyo-406-deleted", lambda: (git_project / "module.py").unlink()
    )

    dispatch_id, drift = _drift(kyo, record, "kyo-406-deleted", against)
    assert drift["symbol_check"].startswith(touch_drift.SYMBOL_CHECK_NOT_RUN), drift
    assert "module.py" in drift["symbol_check"]
    assert drift["outside_claimed_symbols"] == []
    assert drift["touches_file_lines"] == ["module.py#beta"]
    printed = kyo("touch-drift", dispatch_id, "--against", against)
    assert "symbol-level drift not-run for module.py" in printed.out

    applied = kyo("edit", record, "--touches-from-drift", dispatch_id, "--json")
    assert applied.code == 0, applied
    touches = [
        (t["path"], t.get("symbol")) for t in kyo("show", record, "--json").json["touches"]
    ]
    assert touches == [("module.py", "beta")], touches


def test_drift_reads_each_file_once_per_computation(
    kyo, git_project: Path, read_counter
) -> None:
    """KYO-406.c5: two symbols claimed on one file, one read of the tip's copy."""
    (git_project / "module.py").write_text(MODULE, encoding="utf-8")
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "module.py#alpha", "module.py#beta")
    against = _branch(
        git_project,
        "kyo-406-once",
        lambda: _edit_function(git_project / "module.py", "gamma", "c = 99"),
    )
    dispatch_id = kyo("dispatch", record, "--branch", "kyo-406-once", "--json").json[
        "dispatch_id"
    ]
    read_counter.clear()
    assert kyo("touch-drift", dispatch_id, "--against", against, "--json").code == 0
    reads = [call for call in read_counter if call[0] == touch_drift.__name__]
    assert reads == [(touch_drift.__name__, "kyo-406-once", "module.py")], reads
