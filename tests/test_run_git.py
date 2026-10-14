"""KYO-417: every git subprocess goes through one ``run_git()``.

Kyosei shelled out to git from some thirty places, each building its own argv and
environment, and none of them pinned what decides what git's output looks like or which
repository it answers for. Three defects followed, and each was therefore thirty
defects:

* **quoting** -- without ``core.quotePath=false`` a path with a non-ASCII byte comes back
  C-quoted and octal-escaped wherever the call does not use ``-z``, and every prefix or
  equality test against the real path fails without an error (c3);
* **the wrong repository** -- git exports ``GIT_DIR`` into its hooks, and while it is set
  git ignores ``cwd=`` (c4);
* **no bound** -- one call in thirty had a timeout, none refused a credential prompt, and
  in a partial clone a hunk diff fetched blobs from the promisor remote on the spot, from
  the Stop hook (c5, c6, c7).

The helper is :func:`kyosei.git_queries.run_git`. This file proves each thing it pins,
each against the removal of that one thing, and walks the AST of the whole package so a
new ``subprocess.run(["git", ...])`` cannot quietly reappear beside it (c2).
"""

from __future__ import annotations

import ast
import math
import os
import subprocess
import time
from pathlib import Path

import pytest

import kyosei
from kyosei import cmd_publish, docs_regions, git_queries, scaffold
from kyosei.errors import KyoseiError
from kyosei.git_queries import (
    GIT_PINNED_ENV,
    GIT_SCRUBBED_ENV,
    GitTimeout,
    _git_commit_exists,
    _git_current_branch,
    _git_diff_hunks,
    _git_file_at,
    _git_head_or_none,
    _git_rev_parse_head,
    last_commit_at,
    run_git,
)

from .conftest import invoke

PACKAGE = Path(kyosei.__file__).resolve().parent


# --------------------------------------------------------------------------- helpers


def _sh(cwd: Path, *args: str) -> str:
    """Plain git for FIXTURES only -- deliberately not ``run_git``, so a fixture never
    depends on the thing under test."""
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(path: Path, *, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _sh(path, "init", "-q", "-b", branch)
    _sh(path, "config", "user.email", "test@example.com")
    _sh(path, "config", "user.name", "Test")
    return path


def _commit(repo: Path, message: str) -> str:
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", message)
    return _sh(repo, "rev-parse", "HEAD")


class _Recorder:
    """A ``subprocess.run`` stand-in that records what it was asked and answers."""

    def __init__(self, answer=None, raise_timeout: bool = False) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self.answer = answer
        self.raise_timeout = raise_timeout

    def __call__(self, argv, *args, **kwargs):
        self.calls.append((list(argv), kwargs))
        timeout = kwargs.get("timeout")
        # c5: the helper must pass a finite timeout. A call without one reaches this
        # assertion rather than the induced timeout, so removing the bound fails here.
        assert timeout is not None and math.isfinite(timeout) and timeout > 0, (
            f"git was run with no finite timeout: {argv} {kwargs}"
        )
        if self.raise_timeout:
            raise subprocess.TimeoutExpired(argv, timeout)
        return subprocess.CompletedProcess(argv, 0, self.answer or "", "")


# ------------------------------------------------------------------ c1: the one helper


def test_run_git_pins_config_env_and_timeout(monkeypatch, tmp_path: Path) -> None:
    """KYO-417.c1 and c6: argv, environment and bound, as git actually receives them."""
    for name in GIT_SCRUBBED_ENV:
        monkeypatch.setenv(name, str(tmp_path / "elsewhere"))
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")  # an inherited opposite is overridden
    monkeypatch.delenv("GIT_NO_LAZY_FETCH", raising=False)
    recorder = _Recorder(answer="ok\n")
    monkeypatch.setattr(subprocess, "run", recorder)

    run_git(tmp_path, "status", "--porcelain")

    [(argv, kwargs)] = recorder.calls
    assert argv[:3] == ["git", "-c", "core.quotePath=false"]
    assert argv[3:] == ["status", "--porcelain"]
    assert kwargs["cwd"] == tmp_path
    env = kwargs["env"]
    for name in GIT_SCRUBBED_ENV:
        assert name not in env, f"{name} reached git"
    # c6: both named explicitly, not only through the constant, so emptying the
    # constant fails too.
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_NO_LAZY_FETCH"] == "1"
    for name, value in GIT_PINNED_ENV.items():
        assert env[name] == value
    # The rest of the environment is inherited, not replaced: PATH still finds git.
    assert env["PATH"] == os.environ["PATH"]


def test_run_git_refuses_an_unbounded_call(tmp_path: Path) -> None:
    for timeout in (float("inf"), 0, -1):
        with pytest.raises(ValueError, match="finite positive timeout"):
            run_git(tmp_path, "status", timeout=timeout)


# ------------------------------------------------ c2: nothing else spawns git directly

_SPAWNERS = {"run", "Popen", "check_output", "check_call", "call", "getoutput", "getstatusoutput"}

#: The one place a literal ``"git"`` argv head may appear: the helper itself.
_ALLOWED = {("git_queries.py", "run_git")}


def _literal_git_argv(call: ast.Call) -> bool:
    argv = call.args[0] if call.args else None
    for keyword in call.keywords:
        if keyword.arg == "args":
            argv = keyword.value
    if not isinstance(argv, (ast.List, ast.Tuple)) or not argv.elts:
        return False
    head = argv.elts[0]
    return isinstance(head, ast.Constant) and head.value == "git"


def _direct_git_spawns(source: str, filename: str) -> list[str]:
    """Every ``subprocess.<spawner>([...'git'...])`` in ``source``, with the function it
    sits in -- also a bare ``run(...)`` when ``run`` came from ``from subprocess import``.
    """
    tree = ast.parse(source, filename=filename)
    from_subprocess = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess"
        for alias in node.names
    }
    found: list[str] = []

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = function
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name if function == "<module>" else function
            if isinstance(child, ast.Call):
                func = child.func
                spawner = (
                    isinstance(func, ast.Attribute)
                    and func.attr in _SPAWNERS
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "subprocess"
                ) or (isinstance(func, ast.Name) and func.id in from_subprocess)
                if spawner and _literal_git_argv(child) and (filename, name) not in _ALLOWED:
                    found.append(f"{filename}:{child.lineno} (in {name})")
            visit(child, name)

    visit(tree, "<module>")
    return found


def test_every_git_subprocess_in_the_package_goes_through_run_git() -> None:
    """KYO-417.c2. Reverting any one call site to ``subprocess.run(["git", ...])`` --
    in any module, nested helper or not -- is named here by file and line."""
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        offenders += _direct_git_spawns(path.read_text(encoding="utf-8"), rel)
    assert offenders == [], (
        "git spawned outside kyosei.git_queries.run_git -- route these through it: "
        + ", ".join(offenders)
    )


def test_the_walker_sees_the_helper_it_exempts_and_the_shapes_it_refuses() -> None:
    """The c2 walk would pass vacuously if it recognised nothing, so prove it recognises
    the helper's own call, and every shape a regression could take."""
    helper = (PACKAGE / "git_queries.py").read_text(encoding="utf-8")
    exempt = _direct_git_spawns(helper, "not-the-helper.py")
    assert len(exempt) == 1 and "(in run_git)" in exempt[0]

    shapes = {
        "module": 'import subprocess\nsubprocess.run(["git", "status"])\n',
        "nested": (
            "import subprocess\ndef outer():\n    def run(*a):\n"
            '        return subprocess.run(["git", *a], cwd=".")\n'
        ),
        "keyword": 'import subprocess\nsubprocess.check_output(args=("git", "log"))\n',
        "from-import": 'from subprocess import Popen as P\nP(["git", "fetch"])\n',
    }
    for label, source in shapes.items():
        assert _direct_git_spawns(source, "x.py"), f"the walk missed the {label} shape"
    assert not _direct_git_spawns('import subprocess\nsubprocess.run(["ps"])\n', "x.py")


# ---------------------------------------------- c3: a non-ASCII path comes back literal


def test_tracked_store_paths_returns_a_non_ascii_path_verbatim(tmp_path: Path) -> None:
    """KYO-417.c3, on ``git ls-files`` -- a listing that does not use ``-z``, so only
    ``core.quotePath=false`` stands between it and ``"ideas/caf\\303\\251.md"``."""
    repo = _repo(tmp_path / "repo")
    (repo / "ideas").mkdir()
    (repo / "ideas" / "café.md").write_text("# café\n", encoding="utf-8")
    _commit(repo, "a non-ASCII idea")

    assert cmd_publish._tracked_store_paths(repo) == ["ideas/café.md"]


def test_docs_check_reads_hunks_for_a_non_ascii_path(tmp_path: Path) -> None:
    """KYO-417.c3 again, where the quoting silently lost data: the docs gate parses
    ``+++ b/<path>`` headers out of a plain ``git diff``, and a quoted header
    (``+++ "b/docs/caf\\303\\251.md"``) matched nothing -- the file's changed lines were
    simply absent from what the gate saw."""
    repo = _repo(tmp_path / "repo")
    (repo / "docs").mkdir()
    doc = repo / "docs" / "café.md"
    doc.write_text("one\ntwo\nthree\n", encoding="utf-8")
    _commit(repo, "base")
    _sh(repo, "checkout", "-q", "-b", "work")
    doc.write_text("one\nTWO\nthree\n", encoding="utf-8")
    _commit(repo, "edit")

    changes = docs_regions.branch_file_changes(repo, "main", "work")

    assert list(changes) == ["docs/café.md"]
    assert changes["docs/café.md"].after_lines == frozenset({2})


def test_the_already_nul_terminated_listings_stay_literal(tmp_path: Path) -> None:
    """Not c3's mechanism, recorded beside it: ``branch_changed_files`` and
    ``commit_changed_files`` have used ``-z`` since KYO-401, and under ``-z`` git prints
    paths verbatim whatever ``core.quotePath`` says. The brief named them as the c3
    witnesses; they cannot be, and this pins that they stay correct regardless."""
    repo = _repo(tmp_path / "repo")
    (repo / "seed.txt").write_text("x\n", encoding="utf-8")
    _commit(repo, "seed")
    _sh(repo, "checkout", "-q", "-b", "work")
    (repo / "ideas").mkdir()
    (repo / "ideas" / "café.md").write_text("# café\n", encoding="utf-8")
    sha = _commit(repo, "idea")

    assert git_queries.branch_changed_files(repo, "main", "work") == ["ideas/café.md"]
    assert git_queries.commit_changed_files(repo, sha) == ["ideas/café.md"]


# ------------------------------------------ c4: GIT_DIR cannot redirect a routed query


def test_an_exported_git_dir_does_not_redirect_a_routed_query(
    tmp_path: Path, monkeypatch
) -> None:
    """KYO-417.c4. Two repositories on different branches with different heads; the
    environment points at the second, the query names the first."""
    asked = _repo(tmp_path / "asked", branch="asked-branch")
    (asked / "a.txt").write_text("a\n", encoding="utf-8")
    asked_head = _commit(asked, "asked")
    other = _repo(tmp_path / "other", branch="other-branch")
    (other / "b.txt").write_text("b\n", encoding="utf-8")
    other_head = _commit(other, "other")
    assert asked_head != other_head

    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))

    assert _git_current_branch(asked) == "asked-branch"
    assert _git_rev_parse_head(asked) == asked_head
    assert _git_head_or_none(asked) == asked_head


def test_a_query_run_the_way_a_git_hook_runs_it_still_answers(
    tmp_path: Path, monkeypatch
) -> None:
    """KYO-417.c8's audit, pinned: the one place `kyo` runs under a real ``GIT_DIR`` is
    the advisory ``pre-push`` hook (``kyo check-push --warn``). git runs a hook with the
    working tree's top level as its cwd and ``GIT_DIR`` relative to it -- including in a
    linked worktree, where ``GIT_DIR`` is the worktree's admin directory. With the
    scrub, git rediscovers the same repository from ``cwd``, so no caller needed the
    variable kept; this proves it for the linked-worktree case, the one where the
    rediscovered directory differs from the main checkout's."""
    main = _repo(tmp_path / "main")
    (main / "ideas").mkdir()
    (main / "ideas" / "x.md").write_text("x\n", encoding="utf-8")
    _commit(main, "seed")
    linked = tmp_path / "linked"
    _sh(main, "worktree", "add", "-q", "-b", "side", str(linked))
    (linked / "ideas" / "y.md").write_text("y\n", encoding="utf-8")
    side_head = _commit(linked, "side only")

    monkeypatch.chdir(linked)
    monkeypatch.setenv("GIT_DIR", _sh(linked, "rev-parse", "--git-dir"))

    assert _git_current_branch(linked) == "side"
    assert _git_rev_parse_head(linked) == side_head
    assert cmd_publish._tracked_store_paths(linked) == ["ideas/x.md", "ideas/y.md"]


# ------------------------------------ c5: a timeout is the caller's "cannot tell" value


def test_a_timed_out_git_is_each_callers_cannot_tell(monkeypatch, tmp_path: Path) -> None:
    """KYO-417.c5. Every shape a caller had for "git could not say" receives a timeout
    as that answer: ``None``, ``False``, or a worded :class:`KyoseiError` -- never a
    ``TimeoutExpired`` traceback. ``last_commit_at`` is the case that was a live bug:
    it was the one call with a timeout, and ``TimeoutExpired`` is not an ``OSError``,
    so it escaped the ``except OSError`` beside it."""
    monkeypatch.setattr(subprocess, "run", _Recorder(raise_timeout=True))

    assert _git_head_or_none(tmp_path) is None
    assert _git_commit_exists(tmp_path, "HEAD") is False
    assert _git_diff_hunks(tmp_path, ["a", "b"], "x.py") is None
    assert _git_file_at(tmp_path, "HEAD", "x.py") is None
    assert last_commit_at(tmp_path, "x.py") is None
    assert git_queries.commit_changed_files(tmp_path, "HEAD") is None
    assert git_queries.worktree_admin_entries(tmp_path) is None
    assert cmd_publish._git_toplevel(tmp_path) is None
    with pytest.raises(KyoseiError, match="did not finish within"):
        _git_rev_parse_head(tmp_path)
    with pytest.raises(KyoseiError, match="did not finish within"):
        _git_current_branch(tmp_path)
    with pytest.raises(KyoseiError, match="did not finish within"):
        git_queries.branch_changed_files(tmp_path, "main", "work")
    # scaffold keeps its own shape for the same fact (KYO-416): a failed call, rc 124.
    stopped = scaffold._git(tmp_path, "commit", "-m", "x")
    assert stopped is not None and stopped.returncode == 124


def test_a_real_hang_is_stopped_at_the_bound(tmp_path: Path, monkeypatch) -> None:
    """The same, with no stub: a ``git`` that never answers, first on ``PATH``."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")

    started = time.monotonic()
    with pytest.raises(GitTimeout, match="did not finish within 0.5s"):
        run_git(tmp_path, "status", timeout=0.5)
    assert time.monotonic() - started < 10


# ------------------------- c7: a partial clone's missing blob is not fetched by a sweep

MODULE_ONE = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"
MODULE_TWO = "def alpha():\n    return 1\n\n\ndef beta():\n    return 20\n"


def _missing_objects(repo: Path) -> set[str]:
    listed = _sh(repo, "rev-list", "--objects", "--missing=print", "--all")
    return {line[1:] for line in listed.splitlines() if line.startswith("?")}


def _partial_clone(tmp_path: Path) -> tuple[Path, Path, str, str]:
    """``(origin, clone, first, second)``: ``clone`` is a ``--filter=blob:none`` clone
    of ``origin`` whose HEAD is ``second`` and which does NOT have ``second``'s blob of
    ``m.py`` -- the exact state a hunk diff over ``first..second`` meets."""
    origin = _repo(tmp_path / "origin")
    _sh(origin, "config", "uploadpack.allowFilter", "true")
    _sh(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
    (origin / "m.py").write_text(MODULE_ONE, encoding="utf-8")
    first = _commit(origin, "one")
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--filter=blob:none", origin.as_uri(), str(clone)],
        check=True,
        capture_output=True,
    )
    (origin / "m.py").write_text(MODULE_TWO, encoding="utf-8")
    second = _commit(origin, "two")
    # Move HEAD without checking out: a fetch under the clone's filter brings the
    # commit and tree, never the blob.
    _sh(clone, "fetch", "-q", "origin")
    _sh(clone, "update-ref", "refs/heads/main", second)
    return origin, clone, first, second


def test_a_hunk_diff_in_a_partial_clone_does_not_fetch(tmp_path: Path) -> None:
    """KYO-417.c7 at the function sweep's ``_hunks_for`` calls. Reproduced first on
    git 2.55 by hand: the same argv without ``GIT_NO_LAZY_FETCH`` ran ``git fetch
    origin ... --filter=blob:none --stdin`` against the promisor remote and returned
    the hunk; with it, git refused (``lazy fetching disabled``) and exited 128."""
    _, clone, first, second = _partial_clone(tmp_path)
    missing = _missing_objects(clone)
    assert missing, "the fixture did not leave a blob out"

    assert _git_diff_hunks(clone, [first, second], "m.py") is None
    assert _git_file_at(clone, second, "m.py") is None
    assert _missing_objects(clone) == missing, "a routed query fetched from the promisor"


def test_a_hung_promisor_remote_does_not_hold_the_query(tmp_path: Path) -> None:
    """The lazy-fetch pin's other half: a promisor that never answers. Without the pin
    the diff would sit in the fetch for the remote's full 30 seconds; with it, no fetch
    starts."""
    _, clone, first, second = _partial_clone(tmp_path)
    stall = tmp_path / "stalling-upload-pack"
    stall.write_text('#!/bin/sh\nsleep 30\nexec git-upload-pack "$@"\n', encoding="utf-8")
    stall.chmod(0o755)
    _sh(clone, "config", "remote.origin.uploadpack", str(stall))

    started = time.monotonic()
    assert _git_diff_hunks(clone, [first, second], "m.py") is None
    assert time.monotonic() - started < 10


def test_a_stop_hook_sweep_in_a_partial_clone_does_not_fetch(
    tmp_path: Path, monkeypatch
) -> None:
    """KYO-417.c7 end to end: ``kyo sweep`` (what the Stop hook runs) over a window whose
    new-side blob the clone lacks, for a record that touches ``m.py#alpha``. The sweep
    completes, fetches nothing, and -- because the region question is unanswerable
    without the blob -- flags the file, which is the direction KYO-300 leans."""
    origin = tmp_path / "origin"
    result = invoke(["init", str(origin), "--in-repo", "--prefix", "TST"])
    assert result.code == 0, result
    _repo(origin)
    _sh(origin, "config", "uploadpack.allowFilter", "true")
    _sh(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
    (origin / "m.py").write_text(MODULE_ONE, encoding="utf-8")
    scope = tmp_path / "touches.txt"
    scope.write_text("m.py#alpha\n", encoding="utf-8")
    criteria = tmp_path / "criteria.txt"
    criteria.write_text("it works\n", encoding="utf-8")
    (origin / "ideas").mkdir(exist_ok=True)
    idea = origin / "ideas" / "thing.md"
    idea.write_text("# thing\n\nDo it.\n", encoding="utf-8")
    promoted = invoke(
        [
            "--root",
            str(origin),
            "promote",
            str(idea),
            "--kind",
            "chore",
            "--criteria-file",
            str(criteria),
            "--touches-file",
            str(scope),
            "--json",
        ]
    )
    assert promoted.code == 0, promoted
    record = promoted.json["record"]["id"]
    first = _commit(origin, "one")

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--filter=blob:none", origin.as_uri(), str(clone)],
        check=True,
        capture_output=True,
    )
    assert invoke(["--root", str(clone), "rebuild"]).code == 0
    baseline = invoke(["--root", str(clone), "sweep", "--json"])
    assert baseline.code == 0, baseline

    # An edit to beta only: with both blobs, the region check would clear alpha.
    (origin / "m.py").write_text(MODULE_TWO, encoding="utf-8")
    second = _commit(origin, "two")
    _sh(clone, "fetch", "-q", "origin")
    _sh(clone, "update-ref", "refs/heads/main", second)
    missing = _missing_objects(clone)
    assert missing

    swept = invoke(["--root", str(clone), "sweep", "--json"])

    assert swept.code == 0, swept
    assert _missing_objects(clone) == missing, "the sweep fetched from the promisor"
    assert first != second
    flagged = invoke(["--root", str(clone), "show", record, "--json"])
    assert flagged.code == 0, flagged
    assert "m.py" in flagged.out and "touch-changed" in flagged.out
