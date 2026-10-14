"""Shared fixtures: an initialised project and an in-process CLI runner.

Tests drive the real entry point (``kyosei.cli.main``) rather than the Python API,
because the contract this milestone owes its callers is the CLI's: its output, its
exit codes, and the files it leaves on disk.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from kyosei import config as config_module
from kyosei.cli import main
from kyosei.install_skill import CLAUDE_MD_SETUP_INCOMPLETE_SECTION


@dataclass
class Run:
    """The result of one CLI invocation."""

    code: int
    out: str
    err: str

    @property
    def json(self) -> dict:
        return json.loads(self.out)

    def __repr__(self) -> str:  # pragma: no cover - only used on failure
        return f"Run(code={self.code}, out={self.out!r}, err={self.err!r})"


class RealStoreHomeRefused(RuntimeError):
    """:func:`invoke` was about to run `kyo` against the account's own ``~/kyosei``."""


def refuse_the_real_store_home() -> None:
    """Raise unless the store home `kyo` would resolve right now is somewhere else (KYO-504).

    The session guard below (:func:`real_registry_untouched`) only exists while pytest
    runs, and :func:`store_home` only redirects ``$KYOSEI_HOME`` inside a test. KYO-504's
    leak was neither: ``tmpXXXXXXXX/provoked/proj`` is a ``tempfile.mkdtemp()`` name,
    which pytest's ``tmp_path`` never is, so it was this module's helpers called from
    outside pytest -- a probe that imported the remedy walk and ran a provoker by hand,
    with no fixture to redirect anything and no session to notice. Checked here, in the
    one function every in-process `kyo` call in this suite goes through, it holds for a
    test, a module- or session-scoped fixture that runs before :func:`store_home`, and a
    script alike, and it refuses before anything is written rather than after.
    """
    from kyosei.config import STORE_HOME_ENV
    from kyosei.config import store_home as resolved_store_home

    real = _real_registry().parent
    if os.path.realpath(resolved_store_home()) != os.path.realpath(real):
        return
    raise RealStoreHomeRefused(
        f"refusing to run `kyo` in-process against the real store home {real}: "
        f"${STORE_HOME_ENV} is not redirected, so `kyo init` would register its project "
        f"in {real / 'projects.json'}. Set {STORE_HOME_ENV} to a temporary directory first "
        f'({STORE_HOME_ENV}="$(mktemp -d)" in the shell, or the store_home fixture, '
        "which every test already gets)"
    )


def invoke(args: list[str], stdin: str | None = None) -> Run:
    refuse_the_real_store_home()
    out, err = io.StringIO(), io.StringIO()
    real_stdin = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(args)
            except SystemExit as exit_:  # argparse usage errors
                code = int(exit_.code or 0)
    finally:
        sys.stdin = real_stdin
    return Run(code, out.getvalue(), err.getvalue())


@pytest.fixture(autouse=True)
def store_home(tmp_path: Path):
    """Point ``$KYOSEI_HOME`` at the test's own tmp directory (KYO-54).

    Autouse and unconditional: `kyo init`'s default layout creates a store under
    ``~/kyosei/<project>/``, and a suite that ran that default against a real home
    directory would scatter store repositories through the developer's home the
    first time anyone ran it. This is the mechanism that makes the default safe to
    exercise honestly rather than only ever testing the alternative.

    Its OWN MonkeyPatch, not the test's ``monkeypatch`` fixture: a test that calls
    ``monkeypatch.undo()`` would otherwise undo this too, and every `kyo init` after it
    -- which registers in ``$KYOSEI_HOME/projects.json`` since KYO-455 -- lands in the
    developer's real registry. It once did, from
    ``test_a_changed_message_is_caught_though_its_key_does_not_move``.
    """
    home = tmp_path / "kyosei-home"
    patch = pytest.MonkeyPatch()
    patch.setenv("KYOSEI_HOME", str(home))
    # KYO-596: the harness item reads Claude Code's settings (`enabledPlugins`) and may
    # run `claude plugin list`; under the developer's real ~/.claude a plugin enabled
    # there would leak into every test. A test that wants a configuration sets its own.
    patch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    yield home
    patch.undo()


def _real_registry() -> Path:
    """The developer's own registry, found from the account, not the environment."""
    import pwd

    return Path(pwd.getpwuid(os.getuid()).pw_dir) / "kyosei" / "projects.json"


@pytest.fixture(autouse=True, scope="session")
def real_registry_untouched():
    """Fail the run if any test changed the developer's real ``~/kyosei/projects.json``.

    After the fact and only inside pytest, so it is the net for what does not pass
    through :func:`invoke` -- a subprocess `kyo` given an environment without the
    redirect. In-process calls are refused before they write by
    :func:`refuse_the_real_store_home`, which also holds outside pytest, where this
    fixture does not exist (KYO-504).
    """
    path = _real_registry()
    before = path.read_bytes() if path.is_file() else None
    yield
    after = path.read_bytes() if path.is_file() else None
    assert after == before, (
        f"the test run changed the real registry {path}; a test escaped $KYOSEI_HOME "
        "(see conftest.store_home). Restore it with `kyo projects remove <root>` for "
        "any temp-directory entry"
    )


def checkout_build_lock_path(root: Path) -> Path:
    """Where :func:`checkout_build_lock` keeps its lock file for the checkout at ``root``.

    Outside the checkout, so the build it guards never sees or packs it, and keyed by the
    checkout's resolved path, so two worktrees (each with its own ``build/``) do not wait
    on each other while two pytest workers, or two whole runs, in one checkout do.
    """
    key = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"kyosei-checkout-build-{key}.lock"


@contextlib.contextmanager
def checkout_build_lock(root: Path) -> Iterator[None]:
    """Hold an exclusive lock on building in the checkout at ``root`` (KYO-609).

    A build run with the checkout as its source tree (``uv build`` there) writes
    setuptools' ``build/`` directory inside it, and two at once delete each other's
    files: measured under ``pytest -n 6``, ``error: could not delete
    'build/lib/kyosei/dispatch_docs.py'``. pytest-xdist spreads one module's tests over
    several workers and a module-scoped fixture runs once per worker, so every test
    that builds in the checkout takes this lock around the build. ``flock`` is released
    by the kernel when the holder dies, so a killed worker leaves nothing to clean up.
    """
    import fcntl

    path = checkout_build_lock_path(root)
    with open(path, "a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


#: The real :func:`kyosei.config.path_program`, captured at import time — before the
#: fixture below has replaced it — so a test whose subject IS the PATH build can put
#: it back (``tests/test_kyo335_path_build_lag.py``).
REAL_PATH_PROGRAM = config_module.path_program


@pytest.fixture(autouse=True)
def no_developers_kyo_on_path(monkeypatch) -> None:
    """Stop the suite from seeing the developer's own `kyo` (KYO-335).

    Autouse and unconditional, on :func:`store_home`'s reasoning one dimension
    along. From KYO-335 this package asks what the `kyo` on ``PATH`` makes of a
    store — a real question a hook's shell answers, and on a developer's machine it
    is answered by whatever build they happen to have installed. Left alone, every
    `kyo init`, `rebuild` and `doctor` test would start a subprocess and then pass
    or fail on the machine's install state: the app's packed build is a snapshot
    behind `main` by design, so a fixture store written at the current
    ``schema_version`` is one it genuinely cannot open.

    The seam is the resolver rather than ``PATH`` itself, and that is the second
    version of this fixture. Editing ``PATH`` looks more faithful and is worse: where
    `kyo` shares a bin directory such as ``~/.local/bin`` with `uv` and other tools,
    dropping the directories that contain a `kyo` takes those
    with it and breaks tests that have nothing to do with this. Replacing the one
    function is exact, and a test that wants the real one says so.
    """
    monkeypatch.setattr(config_module, "path_program", lambda *_a, **_k: None)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """An initialised in-repo project rooted at ``tmp_path/proj``, prefix ``TST``.

    ``--in-repo`` is explicit here because KYO-54 made the split store the
    default. The overwhelming majority of this suite is about what the store
    *does*, not where it is committed, and the in-repo layout keeps store and code
    in one directory so those tests stay readable. The split layout has its own
    fixtures and its own file (``tests/test_split_store.py``).
    """
    root = tmp_path / "proj"
    result = invoke(["init", str(root), "--in-repo", "--prefix", "TST"])
    assert result.code == 0, result
    _without_inits_claude_md(root)
    return root


def _without_inits_claude_md(code_root: Path) -> None:
    """Remove the setup-incomplete CLAUDE.md `kyo init` writes since KYO-558.

    These fixtures are about what the store does, and a root-level Markdown file the
    suite did not ask for is a document row, a touch candidate and a path in pinned
    output for every test built on them. KYO-558's own tests
    (``tests/test_init_points_to_onboarding.py``) run `kyo init` themselves and keep it.
    """
    claude_md = code_root / "CLAUDE.md"
    text = claude_md.read_text(encoding="utf-8") if claude_md.is_file() else ""
    if CLAUDE_MD_SETUP_INCOMPLETE_SECTION in text:
        claude_md.unlink()


@pytest.fixture
def split_project(tmp_path: Path) -> tuple[Path, Path]:
    """A split-layout project: ``(code_root, store_root)``.

    The code repository is a git repo with one commit (a worker's worktree looks
    like this); the store is a separate git repository under ``$KYOSEI_HOME``.
    """
    code = tmp_path / "code"
    code.mkdir()
    (code / "src").mkdir()
    (code / "src" / "widget.py").write_text("x = 1\n", encoding="utf-8")
    result = invoke(["init", "--defaults", str(code), "--prefix", "TST", "--json"])
    assert result.code == 0, result
    store = Path(result.json["store_root"])
    _without_inits_claude_md(code)
    git_init(code)
    return code, store


@pytest.fixture
def kyo(project: Path):
    """Run a command against the fixture project."""

    def run(*args: str, stdin: str | None = None) -> Run:
        return invoke(["--root", str(project), *args], stdin=stdin)

    # Carried on the runner so helpers that only receive `kyo` can still reach the
    # project's own kind registry — see :func:`allow_direct_filing`.
    run.project = project
    return run


@pytest.fixture
def db(project: Path):
    """Direct sqlite access, for tests that must corrupt the store on purpose."""

    def connect() -> sqlite3.Connection:
        conn = sqlite3.connect(project / "kyosei" / "kyosei.db", isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    return connect


def git_init(project: Path, *, commit_message: str = "init") -> str:
    """Turn ``project`` into a minimal git repo with one commit, and return its
    HEAD sha — what `kyo dispatch` (KYO-7.c1) reads via `git rev-parse HEAD`.

    ``-b main`` names the branch explicitly (KYO-366): the suite must not depend
    on the host's global ``init.defaultBranch``, which is not this repo's to set."""
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args], cwd=project, check=True, capture_output=True, text=True
    )
    run("init", "-q", "-b", "main")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    run("add", "-A")
    run("commit", "-q", "-m", commit_message)
    return run("rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def git_project(project: Path) -> Path:
    """The ``project`` fixture, additionally a git repo with one commit."""
    git_init(project)
    return project


@pytest.fixture
def write_file(tmp_path: Path):
    """Write prose to a file and return its path — the only way prose enters (law 5)."""

    def write(name: str, content: str) -> str:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return str(path)

    return write


def export_of(project: Path, record_id: str) -> Path:
    return project / "kyosei" / "export" / f"{record_id}.json"


def events_text(project: Path) -> str:
    """Every file the events ledger is made of, concatenated (KYO-268.c2).

    One helper rather than ``project / "kyosei" / "export" / "_events.jsonl"`` spelled
    out in nine test modules: the ledger is a directory of bounded shards from
    schema_version 20 on, and a test that names the pre-migration file is asserting
    against a file this build never writes to.
    """
    from kyosei.events_ledger import events_files_in

    return "".join(
        p.read_text(encoding="utf-8")
        for p in events_files_in(project / "kyosei" / "export")
    )


def event_lines(project: Path) -> list[dict]:
    """Every event in the ledger, in order, parsed."""
    import json as _json

    return [_json.loads(line) for line in events_text(project).splitlines() if line.strip()]


def kinds_yml(project: Path) -> Path:
    return project / "kyosei" / "kinds.yml"


def accepts_intake(project: Path, kind: str) -> bool:
    """Does this project's registry let `kyo new` file ``kind`` directly? (KYO-43)"""
    raw = yaml.safe_load(kinds_yml(project).read_text(encoding="utf-8"))
    entry = raw["kinds"].get(kind) or {}
    return "intake" in (entry.get("accepts_from") or [])


def allow_direct_filing(project: Path, kind: str) -> None:
    """Add ``intake`` to ``kind``'s ``accepts_from`` in this project's registry.

    KYO-43 made `kyo new` enforce the registry's flow rule, so most kinds can no
    longer be filed directly. The escape hatch is deliberately the registry itself
    rather than a ``--force`` flag, and this is that hatch: a project that wants a
    kind filed directly says so in one reviewable line of its own ``kinds.yml``.

    Used by :func:`new_record` for every test that needs a record *of* some kind
    rather than the filing ceremony *for* it — which also means the suite exercises
    KYO-43.c2 ("reads the project's own kinds.yml, not a hardcoded list") on every
    run instead of only in the one test that names it.
    """
    if accepts_intake(project, kind):
        return
    path = kinds_yml(project)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    entry = raw["kinds"][kind]
    entry["accepts_from"] = [*(entry.get("accepts_from") or []), "intake"]
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def requires_evidence(project: Path, kind: str) -> bool:
    """Does this project's registry make `kyo new` refuse ``kind`` without
    ``--evidence-file``? (KYO-521: ``policy.requires_evidence_links``)"""
    raw = yaml.safe_load(kinds_yml(project).read_text(encoding="utf-8"))
    policy = (raw["kinds"].get(kind) or {}).get("policy") or {}
    return policy.get("requires_evidence_links") is True


def new_record(
    kyo, *, kind: str = "bug", title: str = "A record", criteria: list[str] | None = None
):
    """Create a record via stdin-fed criteria; returns its id.

    KYO-521: a kind whose policy sets ``requires_evidence_links`` (the default
    ``decision``) is refused by `kyo new` without ``--evidence-file``, so one is
    supplied here, from a temporary file -- stdin may already carry the criteria --
    for every test that needs a record *of* that kind rather than the filing rule.
    """
    project = getattr(kyo, "project", None)
    if project is not None:
        allow_direct_filing(project, kind)
    # KYO-538: `--force` because this helper's default title and criteria are shared by
    # every test, and a test filing two records "A record" is not filing a duplicate.
    args = ["new", kind, "--title", title, "--force", "--json"]
    stdin = None
    if criteria is not None:
        args += ["--criteria-file", "-"]
        stdin = "\n".join(criteria) + ("\n" if criteria else "")
    with contextlib.ExitStack() as cleanup:
        if project is not None and requires_evidence(project, kind):
            handle = cleanup.enter_context(
                tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8")
            )
            handle.write("The test suite, filing a record of this kind:\n\"evidence\"\n")
            handle.flush()
            args += ["--evidence-file", handle.name]
        result = kyo(*args, stdin=stdin)
    assert result.code == 0, result
    return result.json["record"]["id"]


def valid_report(
    *,
    dispatch_id: str,
    record_id: str,
    worked_from_sha: str,
    criteria_verdicts: list | None = None,
    premise_false: list | None = None,
    disagreements: list | None = None,
    corrected_numbers: list | None = None,
    commits: list | None = None,
    **extra,
) -> dict:
    """A minimal, schema-valid ``kyo report`` document (``kyo report-schema``)."""
    return {
        "dispatch_id": dispatch_id,
        "record_id": record_id,
        "worked_from_sha": worked_from_sha,
        "criteria_verdicts": criteria_verdicts or [],
        "premise_false": premise_false or [],
        "disagreements": disagreements or [],
        "corrected_numbers": corrected_numbers or [],
        "commits": commits or [],
        **extra,
    }


def verify_criterion(
    kyo,
    criterion_id: str,
    verdict: str = "met",
    *,
    method: str = "reasoned-about",
    why: str | None = None,
    evidence: str | None = None,
):
    """``kyo verify`` a criterion (milestone 4: the only path to a verified state —
    see ``kyosei.contracts.VERIFY_VERDICTS``). Asserts success and returns the run.
    """
    args = ["verify", criterion_id, "--verdict", verdict, "--method", method]
    stdin = None
    if why is not None:
        args += ["--why", why]
    if evidence is not None:
        args += ["--evidence-file", "-"]
        stdin = evidence
    result = kyo(*args, stdin=stdin)
    assert result.code == 0, result
    return result


def git_worktree(repo: Path, path: Path) -> str:
    """A detached linked worktree of ``repo`` at ``path``, and its path as a string.

    KYO-402: `kyo dispatch --worktree` refuses a directory that is not a worktree of
    the code repository (its own toplevel, sharing the repository's git-common-dir),
    because `kyo worktrees --prune` used to delete whatever a dispatch had recorded.
    A test that needs "a worktree of its own" therefore needs a real one; a bare
    ``mkdir`` is exactly the kind of directory that is now refused. Idempotent, like
    the ``mkdir(exist_ok=True)`` it replaces.
    """
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", "-q", "--detach", str(path)],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
    return str(path)


@contextlib.contextmanager
def legacy_unresolved_rows():
    """Write ``unresolved`` stops as events ROWS, the way every build before KYO-418 did.

    KYO-418.c6 counts them in place instead (``stop_audit.COUNTED_IN_PLACE``), but a
    store's ledger keeps every row written before that forever and every reader still
    reads them — so the tests of how those rows RENDER need rows to render. This is
    the one seam that makes them, and it forges nothing: the rows are written by the
    real ``record_stop_audit``, exactly as the earlier build's hook wrote them.
    """
    from unittest import mock

    from kyosei import stop_audit, stop_target

    with mock.patch.dict(stop_audit.COUNTED_IN_PLACE):
        del stop_audit.COUNTED_IN_PLACE[stop_target.STOP_TARGET_UNRESOLVED]
        yield
