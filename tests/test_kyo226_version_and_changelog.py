"""KYO-226: a consumer can tell which build they run and what changed since.

The measured defect. A consumer's report carried an item that KYO-173 had already
fixed, against an installed build that contained the fix — their own shift notes show
the remedy working on two records before they reported it broken. Nothing on either
side could have told them: there was no
`kyo version`, `pyproject.toml` is pinned at 0.1.0, the brief header carried no build
identity, and the README said how to reinstall but not when a reinstall was worth doing.

The six criteria map onto the six sections below. Two of them are the load-bearing ones,
and both are load-bearing for the same reason KYO-138's c4 was:

* :func:`test_a_real_wheel_built_from_this_tree_carries_the_stamp_and_the_changelog`
  builds an actual wheel from this checkout into a temp directory and reads the two
  files out of the archive. A mocked build would prove that the *hook* works and say
  nothing about whether the hook is wired into the build a consumer actually runs — and
  "the wheel exits 0, prints no warning, and contains no such file" is a failure this
  repository has already had once, recorded in ``pyproject.toml``'s own package-data
  comment.
* :func:`test_the_committed_changelog_is_what_the_store_yields_right_now` makes a stale
  committed document a build failure rather than a discovery, on exactly the terms
  ``tests/test_kyo138_command_reference.py`` enforces for ``docs/reference/cli.md``.
  ``test_the_changelog_staleness_check_can_fail`` is its canary: a checker never shown
  to fail is not known to check anything (KYO-34).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from kyosei import buildinfo, changelog, config, contracts, docs_check, docs_targets
from kyosei.buildinfo import BuildIdentity
from kyosei.cli import COMMAND_TABLE, MUTATING_COMMANDS, build_parser
from kyosei.commands import PROJECT_NONE
from kyosei.registry import DEFAULT_DOCUMENT_GLOBS

from .conftest import checkout_build_lock, checkout_build_lock_path, invoke
from .this_store import store_path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED = REPO_ROOT / changelog.CHANGELOG_PATH

#: This repository's own store export. Kyosei tracks its development in a store kept
#: beside the code in the maintainer's checkout; a public checkout does not carry it.
#: Tests that read that store, or compare a committed file against it, run only where it
#: is present; the mechanisms they lean on are also exercised on fixture stores (below,
#: and in ``test_kyo342_worktree_store.py``).
THIS_STORE = store_path("export")
needs_this_store = pytest.mark.skipif(
    not THIS_STORE.is_dir(),
    reason="reads this repository's own store, which only the maintainer's checkout carries",
)


@pytest.fixture
def nowhere(tmp_path: Path, monkeypatch) -> Path:
    """An empty directory with no Kyosei project in it or above it, as the working
    directory — the condition ``PROJECT_NONE`` is claimed to survive. The autouse
    ``store_home`` fixture already points ``$KYOSEI_HOME`` at this test's own tmp tree,
    so there is genuinely no store to find."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    return empty


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _utc_date(repo: Path, rev: str = "HEAD") -> str:
    """The commit's date on the UTC clock (KYO-398.c1), read from its epoch seconds —
    independent of both the committer's offset and this machine's timezone, so this
    is not the implementation's own arithmetic restated."""
    from datetime import UTC, datetime

    epoch = int(_git(repo, "show", "-s", "--format=%ct", rev))
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d")


# ----------------------------------------------------------- c1: `kyo version` answers


def test_kyo_version_names_the_package_the_commit_and_the_date(nowhere: Path) -> None:
    """KYO-226.c1's three facts, asserted against git rather than against a copy of the
    string — so this cannot pass by agreeing with a stale expectation."""
    result = invoke(["version"])
    assert result.code == 0, result
    head = _git(REPO_ROOT, "rev-parse", "HEAD")
    date = _utc_date(REPO_ROOT)
    first = result.out.splitlines()[0]
    assert "kyosei" in first and "0.1.0" in first
    assert head[: buildinfo.SHORT_SHA] in first, first
    assert date in first, first


def test_kyo_version_json_carries_the_same_fields(nowhere: Path) -> None:
    """c1's ``--json`` half. The human line is one of the fields, so a caller that wants
    to print what the tool prints does not have to reassemble it."""
    payload = json.loads(invoke(["version", "--json"]).out)
    assert payload["ok"] is True
    assert payload["version"] == "0.1.0"
    assert payload["commit"] == _git(REPO_ROOT, "rev-parse", "HEAD")
    assert payload["commit_short"] == payload["commit"][: buildinfo.SHORT_SHA]
    assert payload["commit_date"] == _utc_date(REPO_ROOT)
    assert payload["source"] in (buildinfo.SOURCE_GIT, buildinfo.SOURCE_STAMP)
    assert payload["line"] == invoke(["version"]).out.splitlines()[0]


def test_kyo_version_runs_with_no_project_at_all(nowhere: Path) -> None:
    """``PROJECT_NONE``, on `kyo reference`'s reasoning: the readers who need this are a
    worker in a dispatch worktree and someone who has just installed the tool, and
    neither reliably has a store above them."""
    assert COMMAND_TABLE["version"][1] == PROJECT_NONE
    assert COMMAND_TABLE["changelog"][1] == PROJECT_NONE
    assert "version" not in MUTATING_COMMANDS and "changelog" not in MUTATING_COMMANDS
    assert invoke(["version"]).code == 0
    assert invoke(["changelog", "--all"]).code == 0


def test_a_copy_with_neither_a_stamp_nor_a_checkout_says_so(tmp_path: Path) -> None:
    """The third state, and the one that must not be silently rendered as a build.

    An empty directory stands in for an installed package that was built without the
    hook: no ``_build_stamp.json``, and a parent that is not ``src/``, so
    :func:`buildinfo.source_tree_root` refuses to read anybody else's git.
    """
    identity = buildinfo.build_identity(tmp_path)
    assert identity.source == buildinfo.SOURCE_UNKNOWN
    assert identity.commit is None
    line = buildinfo.version_line(identity)
    assert "build unknown" in line
    assert "uv tool install --force ." in line, "the refusal names no remedy"


# -------------------------------------------------------------- c1: the dirty marker


def test_a_dirty_working_tree_is_reported_and_a_clean_one_is_not(tmp_path: Path) -> None:
    """The dirty half of c1, measured on a real repository rather than asserted.

    Both directions, because a marker that is always on is the same as no marker. The
    repository is built here rather than reusing this checkout, whose cleanliness is not
    something a test may assume either way.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "one"], cwd=repo, check=True)

    clean = buildinfo.git_identity(repo)
    assert clean is not None and clean["dirty"] is False
    assert "dirty" not in buildinfo.version_line(
        BuildIdentity(
            "0.1.0",
            clean["commit"],
            clean["commit_date"],
            False,
            buildinfo.SOURCE_STAMP,
            buildinfo.INSTALL_KIND_UNKNOWN,
        )
    )

    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    dirty = buildinfo.git_identity(repo)
    assert dirty is not None and dirty["dirty"] is True
    assert dirty["commit"] == clean["commit"], "the commit did not change; only the tree did"
    line = buildinfo.version_line(
        BuildIdentity(
            "0.1.0",
            dirty["commit"],
            dirty["commit_date"],
            True,
            buildinfo.SOURCE_STAMP,
            buildinfo.INSTALL_KIND_UNKNOWN,
        )
    )
    assert "built from a dirty working tree" in line, line
    assert dirty["commit"][: buildinfo.SHORT_SHA] in line, "the sha left the line with the marker"


def test_a_tree_whose_state_git_would_not_report_is_not_called_clean(tmp_path: Path) -> None:
    """The distinction :func:`buildinfo._git` exists to keep: no answer is not ``clean``.

    A directory that is not a git repository at all answers ``None`` rather than an
    identity with ``dirty=False``, so "we could not ask" can never render as "we asked
    and it was clean" — which would be a false statement in the one line a consumer
    reads to decide whether their build matches a commit.
    """
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert buildinfo.git_identity(plain) is None
    assert buildinfo._git(plain, "rev-parse", "HEAD") is None


# ------------------------------------------- KYO-338: the store's churn is not the code's


def _checkout_with_a_store(tmp_path: Path) -> tuple[Path, Path]:
    """A committed git repository shaped like a source install of this package.

    ``src/kyosei/`` is what :func:`buildinfo.source_tree_root` recognises as "this
    package is being imported out of a checkout", and ``kyosei/`` beside it is an
    in-repo Kyosei store — the arrangement this repository itself has, built here rather
    than asserted against this checkout, whose cleanliness a test may not assume.

    Returns the repository root and the package directory inside it.
    """
    repo = tmp_path / "checkout"
    package = repo / "src" / "kyosei"
    package.mkdir(parents=True)
    (package / "cli.py").write_text("# code\n", encoding="utf-8")
    config.write_config(repo, project_prefix="TST")
    export = config.load_config(repo).export_path
    export.mkdir(parents=True, exist_ok=True)
    (export / "_sweeps.jsonl").write_text("{}\n", encoding="utf-8")
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "Test"),
        ("add", "-A"),
        ("commit", "-q", "-m", "one"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True)
    return repo, package


def test_the_store_s_own_ledgers_do_not_make_the_build_dirty(tmp_path: Path) -> None:
    """KYO-338.c1 and c3, on a real repository with a real store in it.

    Both shapes of churn the session hooks produce between commits: a ledger rewritten
    in place and a ledger that did not exist yet. ``kyo version``'s line is checked
    through :func:`buildinfo.version_line`, which
    :func:`test_kyo_version_json_carries_the_same_fields` holds to be the line the
    command prints.
    """
    repo, package = _checkout_with_a_store(tmp_path)
    export = config.load_config(repo).export_path
    assert buildinfo.store_export_relpath(repo) == "kyosei/export"

    (export / "_sweeps.jsonl").write_text('{"swept": 1}\n', encoding="utf-8")
    (export / "_events.jsonl").write_text('{"event": 1}\n', encoding="utf-8")
    identity = buildinfo.build_identity(package)
    assert identity.source == buildinfo.SOURCE_GIT, identity
    assert identity.dirty is False, "the store's own ledgers were counted as code"
    assert "dirty" not in buildinfo.version_line(identity), buildinfo.version_line(identity)


def test_a_modified_source_file_still_stamps_dirty(tmp_path: Path) -> None:
    """KYO-338.c2 — the other direction, which is the whole value of the marker.

    Same repository, same untouched store churn, one edited source file: an exclusion
    that swallowed this case would be indistinguishable from deleting the marker.
    """
    repo, package = _checkout_with_a_store(tmp_path)
    export = config.load_config(repo).export_path
    (export / "_sweeps.jsonl").write_text('{"swept": 2}\n', encoding="utf-8")
    (package / "cli.py").write_text("# code, edited\n", encoding="utf-8")

    identity = buildinfo.build_identity(package)
    assert identity.dirty is True, "a modified source file no longer stamps dirty"
    assert "built from a dirty working tree" in buildinfo.version_line(identity)


def test_a_split_layout_store_excludes_nothing_from_the_code_repository(
    tmp_path: Path,
) -> None:
    """The layout that must not acquire an exclusion (KYO-338.c1's "from the layout").

    A store in its own repository elsewhere cannot dirty the code repository, so there
    is nothing here to skip — and skipping a ``kyosei/export`` that this checkout does
    not have would be exactly the hard-coded path the criterion refuses.
    """
    store = tmp_path / "store"
    config.write_config(store, project_prefix="TST")
    repo, package = _checkout_with_a_store(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()

    assert buildinfo.store_export_relpath(store) == "kyosei/export"
    assert buildinfo.store_export_relpath(plain) is None
    assert buildinfo._status_pathspec(plain) == ()
    assert buildinfo._status_pathspec(repo)[-1] == ":(exclude,top)kyosei/export"


def test_the_exclusion_follows_a_project_that_renamed_its_export_directory(
    tmp_path: Path,
) -> None:
    """``export_dir`` is a config key, so the name is the project's and not ours."""
    repo, _package = _checkout_with_a_store(tmp_path)
    config.write_config(repo, project_prefix="TST", export_dir="ledger")
    assert buildinfo.store_export_relpath(repo) == "kyosei/ledger"


# -------------------------- KYO-445: schema_version, release, and the version line's wording


def test_kyo_version_json_carries_schema_version_and_release(nowhere: Path) -> None:
    """KYO-445.c1. ``schema_version`` is the constant this build speaks, compared with
    the schema module rather than a literal so a bump cannot leave it stale; ``release``
    is a bool, and a working-tree run is never a release."""
    from kyosei.schema import SCHEMA_VERSION

    payload = json.loads(invoke(["version", "--json"]).out)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert isinstance(payload["schema_version"], int)
    assert payload["release"] is False, "a run out of this checkout is not a release"
    assert "last_release" in payload


def _tagged_repo(tmp_path: Path) -> Path:
    """A committed repository whose history is: a commit tagged as the previous release
    ``v0.0.9``, then HEAD. What HEAD carries is up to each test."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "Test"),
        ("add", "-A"),
        ("commit", "-q", "-m", "one"),
        ("tag", "v0.0.9"),
    ):
        _git(repo, *args)
    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "two")
    return repo


def _stamped(tmp_path: Path, repo: Path) -> tuple[dict, BuildIdentity]:
    package = tmp_path / "pkg"
    package.mkdir(exist_ok=True)
    stamp = buildinfo.write_stamp(package, repo)
    return stamp, buildinfo.build_identity(package)


def test_a_tagged_clean_build_is_stamped_a_release(tmp_path: Path) -> None:
    """KYO-445.c2, tagged: HEAD carries ``v<version>`` and the tree is clean."""
    repo = _tagged_repo(tmp_path)
    _git(repo, "tag", buildinfo.release_tag(buildinfo.__version__))
    stamp, identity = _stamped(tmp_path, repo)
    assert stamp["release"] is True, stamp
    assert stamp["last_release"] == buildinfo.__version__
    assert identity.release is True
    assert identity.as_data()["release"] is True


def test_an_untagged_build_is_not_a_release(tmp_path: Path) -> None:
    """KYO-445.c2, untagged: a clean tree whose HEAD carries no ``v<version>`` -- only an
    older release tag behind it, which is what it came after -- and a HEAD tagged with
    some OTHER version, which is not this build's release either."""
    repo = _tagged_repo(tmp_path)
    stamp, identity = _stamped(tmp_path, repo)
    assert stamp["dirty"] is False
    assert stamp["release"] is False, stamp
    assert stamp["last_release"] == "0.0.9"
    assert identity.release is False and identity.last_release == "0.0.9"

    _git(repo, "tag", "v9.9.9")
    stamp, _identity = _stamped(tmp_path, repo)
    assert stamp["release"] is False, "a tag for another version made this a release"


def test_a_tagged_but_dirty_build_is_not_a_release(tmp_path: Path) -> None:
    """KYO-445.c2, dirty: the tag is on HEAD but the tree being built has edits in it,
    so the wheel matches no published commit."""
    repo = _tagged_repo(tmp_path)
    _git(repo, "tag", buildinfo.release_tag(buildinfo.__version__))
    (repo / "a.txt").write_text("three\n", encoding="utf-8")
    stamp, identity = _stamped(tmp_path, repo)
    assert stamp["dirty"] is True
    assert stamp["release"] is False, stamp
    assert identity.release is False


def test_a_live_checkout_is_never_a_release_even_on_its_tag(tmp_path: Path) -> None:
    """The brief's "a working-tree run (no stamp) is never a release": a checkout whose
    clean HEAD carries the tag still runs whatever the tree holds, live."""
    repo, package = _checkout_with_a_store(tmp_path)
    _git(repo, "tag", buildinfo.release_tag(buildinfo.__version__))
    identity = buildinfo.build_identity(package)
    assert identity.source == buildinfo.SOURCE_GIT
    assert identity.release is False
    assert identity.last_release == buildinfo.__version__


def test_a_stamp_from_before_release_existed_reads_as_unreleased(tmp_path: Path) -> None:
    (tmp_path / buildinfo.STAMP_FILENAME).write_text(
        json.dumps({"version": "0.1.0", "commit": "a" * 40, "commit_date": "2026-09-24"}),
        encoding="utf-8",
    )
    identity = buildinfo.build_identity(tmp_path)
    assert identity.release is False and identity.last_release is None
    assert "after release" not in buildinfo.version_line(identity)


def test_the_version_line_reads_as_specified() -> None:
    """KYO-445.c3: the line's two specified forms, a release and an unreleased build,
    verbatim up to the source clause the line always carried."""
    common = {
        "version": "0.2.0",
        "commit_date": "2026-10-01",
        "dirty": False,
        "source": buildinfo.SOURCE_STAMP,
        "kind": buildinfo.INSTALL_KIND_UNKNOWN,
        "commit_time": "2026-10-01T12:00:00.000Z",
    }
    released = buildinfo.version_line(
        BuildIdentity(commit="1a2b3c4d" + "0" * 32, release=True, last_release="0.2.0", **common)
    )
    assert released.startswith("kyosei 0.2.0 (release) — build 1a2b3c4d (2026-10-01 UTC); ")
    assert "unreleased" not in released

    unreleased = buildinfo.version_line(
        BuildIdentity(commit="c99aab56" + "0" * 32, last_release="0.2.0", **common)
    )
    assert unreleased.startswith(
        "kyosei 0.2.0 — unreleased build c99aab56 (2026-10-01 UTC), after release 0.2.0; "
    ), unreleased

    no_release_yet = buildinfo.version_line(
        BuildIdentity(commit="c99aab56" + "0" * 32, **common)
    )
    assert "unreleased build c99aab56 (2026-10-01 UTC); " in no_release_yet
    assert "after release" not in no_release_yet


# --------------------------------------------------------------- c2: the brief header


def test_the_brief_header_states_the_version_line(kyo) -> None:
    """KYO-226.c2, full form. The line is the SAME line ``kyo version`` prints, read off
    the tool on both sides rather than restated here, so the two surfaces cannot answer
    a consumer's "which build is this" differently."""
    expected = json.loads(invoke(["version", "--json"]).out)["line"]
    result = kyo("brief")
    assert result.code == 0, result
    first = result.out.splitlines()[0]
    # Stated BESIDE the brief id and the timestamp, not instead of them: this record
    # adds a fact to the session's first line and may not cost it the two it had.
    assert re.match(
        rf"^\S+-B\d+  \d{{4}}-\d{{2}}-\d{{2}}T\S+Z  {re.escape(expected)}$", first
    ), first


def test_the_compact_brief_states_it_too(kyo) -> None:
    """``--compact`` is what the SessionStart hook injects, which is the one place this
    has to arrive — a build identity abbreviated out of the session's first surface is
    a build identity nobody sees."""
    expected = json.loads(invoke(["version", "--json"]).out)["line"]
    result = kyo("brief", "--compact")
    assert result.code == 0, result
    assert expected in result.out.splitlines()[0]


def test_the_brief_document_carries_the_build(kyo) -> None:
    """c2's ``--json`` half: the same fields ``kyo version --json`` returns, so a caller
    reading the brief document never has to shell out a second time."""
    payload = kyo("brief", "--json").json
    assert payload["build"]["commit"] == json.loads(invoke(["version", "--json"]).out)["commit"]
    assert payload["build"]["line"]
    assert payload["build"]["source"] in (buildinfo.SOURCE_GIT, buildinfo.SOURCE_STAMP)


def test_a_brief_document_from_before_this_field_still_renders(kyo) -> None:
    """The compatibility the ``.get`` in ``render.brief_text`` buys, asserted rather
    than left to be discovered by whoever next renders an old document.

    The document is a REAL one with the new key removed, not a hand-built stand-in: a
    stand-in would be this test's own idea of what a brief document is, and it would go
    stale the next time the document grew a section — which is the class of fixture that
    passes for years while testing something that no longer exists.
    """
    from kyosei.render import brief_text

    payload = kyo("brief", "--json").json
    payload.pop("ok", None)
    payload.pop("command", None)
    assert payload.pop("build")["line"], "the field this test removes was never there"
    text = brief_text(payload, compact=True)
    first = text.splitlines()[0]
    assert first == f"{payload['brief_run_id']}  {payload['generated_at']}", first


# ------------------------------------------------------- c3: `kyo changelog` and --since


@needs_this_store
def test_kyo_changelog_prints_a_line_per_completed_record(nowhere: Path) -> None:
    """c3's shape: date, id, scope heading, title — one line per completed record, and
    every id in the output is a record this store actually completed."""
    result = invoke(["changelog", "--all"])
    assert result.code == 0, result
    payload = json.loads(invoke(["changelog", "--all", "--json"]).out)
    assert payload["entries"], "this repository has completed records and the changelog has none"
    for entry in payload["entries"][:5]:
        assert entry["id"] in result.out
        assert entry["completed_on"] in result.out
        assert entry["title"] in result.out
        assert len(entry["completed_on"]) == 10, entry


def test_the_entries_are_newest_first_and_grouped_by_scope(nowhere: Path) -> None:
    payload = json.loads(invoke(["changelog", "--all", "--json"]).out)
    dates = [entry["completed_on"] for entry in payload["entries"]]
    assert dates == sorted(dates, reverse=True), "the changelog is not newest first"

    text = invoke(["changelog", "--all"]).out
    scopes = [entry["scope"] for entry in payload["entries"] if entry["scope"]]
    for scope in sorted(set(scopes))[:3]:
        assert f"{scope} ({scopes.count(scope)}):" in text, scope


@needs_this_store
def test_since_narrows_the_window_and_narrows_it_correctly(nowhere: Path) -> None:
    """c3's ``--since``. Asserted as a partition of the same set rather than as a count:
    every entry on or after the date is in, and every entry before it is out."""
    every = json.loads(invoke(["changelog", "--all", "--json"]).out)["entries"]
    days = sorted({entry["completed_on"] for entry in every})
    cut = days[len(days) // 2]
    narrowed = json.loads(invoke(["changelog", "--since", cut, "--json"]).out)
    assert narrowed["since"] == cut
    shown = {entry["id"] for entry in narrowed["entries"]}
    assert shown == {entry["id"] for entry in every if entry["completed_on"] >= cut}
    assert shown, "the window excluded everything; this assertion would be vacuous"
    assert len(shown) < len(every), "the window excluded nothing; so would any check of it"


def test_a_bare_changelog_defaults_to_the_build_s_own_snapshot_date(nowhere: Path) -> None:
    """The default that makes a bare `kyo changelog` after a reinstall answer "what
    changed since I last installed" — c3's whole point, and the reason it is not
    ``--all``."""
    version = json.loads(invoke(["version", "--json"]).out)
    payload = json.loads(invoke(["changelog", "--json"]).out)
    assert payload["since"] == version["commit_date"]
    assert payload["shown"] == len(payload["entries"])
    assert payload["total"] >= payload["shown"]


def test_since_and_all_are_refused_together(nowhere: Path) -> None:
    """Law 1: the refusal names a remedy, and the remedy clears the error."""
    refused = invoke(["changelog", "--all", "--since", "2026-01-01"])
    assert refused.code != 0
    assert "different windows" in refused.err
    assert invoke(["changelog", "--all"]).code == 0
    assert invoke(["changelog", "--since", "2026-01-01"]).code == 0


def test_since_accepts_a_commit_and_resolves_it_to_that_commit_s_date(nowhere: Path) -> None:
    """c3 says "a YYYY-MM-DD date or commit sha". The sha path is resolved against the
    checkout this copy runs from, and the two spellings must agree."""
    head = _git(REPO_ROOT, "rev-parse", "HEAD")
    date = _utc_date(REPO_ROOT)
    by_sha = json.loads(invoke(["changelog", "--since", head, "--json"]).out)
    by_date = json.loads(invoke(["changelog", "--since", date, "--json"]).out)
    assert by_sha["since"] == date
    assert by_sha["entries"] == by_date["entries"]


def test_an_unresolvable_since_is_refused_with_the_form_that_works(nowhere: Path) -> None:
    refused = invoke(["changelog", "--since", "last-tuesday"])
    assert refused.code != 0
    assert changelog.DATE_FORMAT in refused.err
    cleared = invoke(["changelog", "--since", "2026-01-01"])
    assert cleared.code == 0, cleared


@needs_this_store
def test_the_completion_date_is_the_move_and_not_the_last_edit(nowhere: Path) -> None:
    """Why the events ledger and not ``updated_at``, measured rather than reasoned.

    Notes, evidence and edits land on a record after it is finished, so a changelog
    dated by ``updated_at`` states when somebody last touched a record, not when it was
    completed. On this store the two disagree by at least a day for 129 of 199 completed
    records, and that number is asserted as a floor below: it is the size of the error
    this derivation exists to avoid, and a refactor that quietly reverted to
    ``updated_at`` would collapse it to zero.

    The subtler half, and the reason :func:`changelog._completion_dates` matches a
    *transition*: an event's ``after`` is a full snapshot of the record, so every later
    write to a finished record carries ``status: done`` inside it. Written to match the
    snapshot alone — which is how this was written first — the derivation dated all 199
    records exactly as ``updated_at`` did, and looked entirely correct.
    """
    export_dir = changelog.store_export_dir(REPO_ROOT)
    assert export_dir is not None
    moved = changelog._completion_dates(export_dir, contracts.default_ladder().complete)
    document = json.loads(
        (THIS_STORE / "KYO-173.json").read_text(encoding="utf-8")
    )
    assert moved["KYO-173"] < document["updated_at"], (
        "KYO-173's completion event and its updated_at are the same instant, so this "
        "check cannot tell the two sources apart — pick a record that was written to "
        "after it was completed"
    )

    payload = json.loads(invoke(["changelog", "--all", "--json"]).out)
    entries = {e["id"]: e for e in payload["entries"]}
    entry = entries["KYO-173"]
    assert entry["dated_by"] == changelog.DATED_BY_EVENT
    assert entry["completed_on"] == moved["KYO-173"][:10]

    # The size of the error, as a floor. `updated_at` would state a different DAY for
    # most of this store's completed records, so a derivation that fell back to it
    # could not pass this by accident.
    export_files = THIS_STORE
    differing = [
        record_id
        for record_id, item in entries.items()
        if json.loads((export_files / f"{record_id}.json").read_text(encoding="utf-8"))[
            "updated_at"
        ][:10]
        != item["completed_on"]
    ]
    assert len(differing) >= 100, (
        f"only {len(differing)} of {len(entries)} completed records are dated "
        "differently from their updated_at — if that number collapsed, the derivation "
        "has gone back to matching the status in an event's snapshot rather than the "
        "transition into it"
    )


# ------------------------------------- c3 on a fixture store: the same claims, anywhere
#
# The tests above read this repository's own store. These make the same claims about a
# store built here, so that a checkout without that store still exercises them:
# ``source_tree_root`` is pointed at the fixture project, which is where ``load()``
# looks first.


def _walk_to_done(kyo, title: str) -> str:
    """File a record and move it up the whole ladder, so that its completion is an
    event in the ledger rather than a status written by hand."""
    from .conftest import new_record

    record = new_record(kyo, criteria=["a criterion to settle"], title=title)
    for status in ("working", "built"):
        assert kyo("move", record, status).code == 0
    verified = kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed")
    assert verified.code == 0, verified
    for status in ("verified", "done"):
        assert kyo("move", record, status).code == 0
    return record


@pytest.fixture
def fixture_store_changelog(kyo, project: Path, monkeypatch) -> tuple[str, str, str]:
    """``(first completed, second completed, still open)``, with ``kyo changelog``
    reading this fixture project's store."""
    first = _walk_to_done(kyo, "the first completed record")
    second = _walk_to_done(kyo, "the second completed record")
    from .conftest import new_record

    still_open = new_record(kyo, criteria=["not yet"], title="a record still open")
    monkeypatch.setattr(buildinfo, "source_tree_root", lambda *a, **k: project)
    return first, second, still_open


def test_changelog_lists_each_completed_record_of_a_fixture_store(
    fixture_store_changelog: tuple[str, str, str],
) -> None:
    """c3's shape on a store of known content: one line per completed record, with its
    date and title, and nothing for a record that is not complete."""
    first, second, still_open = fixture_store_changelog
    result = invoke(["changelog", "--all"])
    assert result.code == 0, result
    payload = json.loads(invoke(["changelog", "--all", "--json"]).out)
    assert payload["source"] == changelog.SOURCE_STORE
    ids = [entry["id"] for entry in payload["entries"]]
    assert sorted(ids) == sorted([first, second]), ids
    assert still_open not in result.out
    for entry in payload["entries"]:
        assert entry["id"] in result.out
        assert entry["title"] in result.out
        assert entry["completed_on"] in result.out
        assert len(entry["completed_on"]) == 10, entry


def test_a_later_edit_does_not_move_a_fixture_records_completion_date(
    kyo, project: Path, fixture_store_changelog: tuple[str, str, str]
) -> None:
    """The completion instant is the move into the complete status, read from the
    events ledger, and not ``updated_at``: a note added after completion advances
    ``updated_at`` and leaves the changelog's instant where it was. A derivation that
    matched the status inside each event's snapshot, rather than the transition into
    it, would date the record by the note."""
    first, _second, _open = fixture_store_changelog
    export_dir = changelog.store_export_dir(project)
    assert export_dir is not None
    before = changelog._completion_dates(export_dir, contracts.default_ladder().complete)
    noted = kyo(
        "edit", first, "--note-file", "-", stdin="a note written after the record was completed\n"
    )
    assert noted.code == 0, noted
    document = json.loads((export_dir / f"{first}.json").read_text(encoding="utf-8"))
    after = changelog._completion_dates(export_dir, contracts.default_ladder().complete)
    assert after[first] == before[first]
    assert after[first] < document["updated_at"], (
        "the note did not advance updated_at past the completion, so this test cannot "
        "tell the two sources apart"
    )
    payload = json.loads(invoke(["changelog", "--all", "--json"]).out)
    entry = {e["id"]: e for e in payload["entries"]}[first]
    assert entry["dated_by"] == changelog.DATED_BY_EVENT
    assert entry["completed_at"] == after[first]


# --------------------------------------------- c4: the generated document cannot go stale


@pytest.fixture(scope="module")
def rendered() -> str:
    loaded = changelog.load()
    return changelog.markdown(loaded, loaded.entries, None)


@needs_this_store
def test_the_committed_changelog_is_what_the_store_yields_right_now(rendered: str) -> None:
    """KYO-226.c4, on ``test_the_committed_reference_is_what_the_parser_yields_right_now``'s
    terms exactly. Equality and not a subset: this file has one author and it is the
    generator, so any difference at all is drift."""
    assert COMMITTED.is_file(), (
        f"{changelog.CHANGELOG_PATH} is missing. Generate it: "
        f"{changelog.CHANGELOG_REGENERATE_COMMAND}"
    )
    committed = COMMITTED.read_text(encoding="utf-8")
    assert committed == rendered, (
        f"{changelog.CHANGELOG_PATH} has fallen behind this repository's store — a "
        "record has been completed (or restated, or rescoped) since it was generated. A "
        "committed changelog that is out of date is worse than none, because it looks "
        f"authoritative. Regenerate it and commit the result:\n\n    "
        f"{changelog.CHANGELOG_REGENERATE_COMMAND}\n"
    )


@needs_this_store
def test_the_changelog_staleness_check_can_fail(rendered: str) -> None:
    """The canary. The specific way this one could quietly stop checking is a generator
    that returned "" or a comparison that normalised both sides to nothing (KYO-34)."""
    assert rendered.strip(), "the generator produced no output at all"
    assert "## " in rendered, "the generator produced no scope sections"
    stale = rendered.replace("- **", "- (removed) **", 1)
    assert stale != rendered, "the substitution found nothing; this canary is vacuous"


def test_the_changelog_is_where_docs_check_already_looks() -> None:
    """c4's other half, on KYO-138.c3's reasoning: a generated document outside the
    default globs is invisible to docs mode and to ``kyo base run --source docs``."""
    reached = {
        path.relative_to(REPO_ROOT).as_posix()
        for glob in DEFAULT_DOCUMENT_GLOBS
        for path in REPO_ROOT.glob(glob)
        if path.is_file()
    }
    assert changelog.CHANGELOG_PATH in reached, (
        f"{changelog.CHANGELOG_PATH} is not matched by any of {DEFAULT_DOCUMENT_GLOBS}"
    )


def test_the_changelog_declares_no_metadata_and_so_raises_no_docs_obligation() -> None:
    """Deliberate, and for :data:`kyosei.cli.REFERENCE_DECLARES_GOVERNS`'s reason: a
    ``governs:`` line here would raise an obligation on every completed record, whose
    remedy is a controller act against a file no human maintains. The equality check
    above is strictly stronger for a generated artifact.

    This also guards an accident the reference does not have to worry about: the text in
    this document is *record titles*, and ``docs_check`` reads ``governs:``/``status:``/
    ``amended-by:`` anywhere outside a fence, so a title that began with one of those
    words would silently turn this file into a governing document.
    """
    documents = docs_targets.documents(REPO_ROOT)
    doc = next((d for d in documents if d.path == changelog.CHANGELOG_PATH), None)
    assert doc is not None, f"docs_targets.documents() did not enumerate {changelog.CHANGELOG_PATH}"
    declaring = [section.heading for section in doc.sections if section.metadata.get("governs")]
    assert not declaring, f"the generated changelog declares governs: targets in {declaring}"
    check = docs_check.check_docs(REPO_ROOT)
    offending = [p for p in check.problems if p.doc_path == changelog.CHANGELOG_PATH]
    assert not offending, f"`kyo docs check` fails on the generated changelog: {offending}"


def test_output_writes_the_same_document_the_committed_copy_holds(
    rendered: str, nowhere: Path
) -> None:
    destination = nowhere / "changelog.md"
    result = invoke(["changelog", "--all", "--output", str(destination)])
    assert result.code == 0, result
    assert destination.read_text(encoding="utf-8") == rendered
    # KYO-156: the regenerate line names the caller's OWN destination, not Kyosei's.
    assert f"kyo changelog --all --output {destination}" in result.out


@needs_this_store
def test_nearby_source_checkout_finds_this_repository_from_inside_it() -> None:
    """The half of KYO-372.c2 that has nothing to do with which store is loaded --
    walking up from a directory finds this checkout by its shape, ``src/kyosei/``
    beside its store (the ``.kyosei`` link, since this repository's split)."""
    assert changelog._nearby_source_checkout(REPO_ROOT / "src" / "kyosei") == REPO_ROOT
    assert changelog._nearby_source_checkout(REPO_ROOT) == REPO_ROOT


def test_nearby_source_checkout_finds_nothing_from_an_unrelated_directory(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert changelog._nearby_source_checkout(empty) is None


def test_live_store_hint_names_the_checkouts_own_generator_when_the_snapshot_might_be_stale() -> (
    None
):
    """KYO-372.c2's hint, isolated from ``load()``'s filesystem probing: it fires only
    when BOTH ingredients hold -- the reading came from the packaged snapshot, and a
    live checkout is reachable -- and names the reader's own destination, not
    Kyosei's."""
    reading = changelog.Changelog([], changelog.SOURCE_PACKAGED, nearby_checkout=REPO_ROOT)
    hint = changelog.live_store_hint(reading, "out.md")
    assert hint is not None
    assert str(REPO_ROOT) in hint
    assert ".venv/bin/python -m kyosei changelog --all --output out.md" in hint

    # Neither ingredient alone is enough -- reading the live store already answers the
    # question, and a packaged reading with no checkout nearby has nothing to point at.
    assert (
        changelog.live_store_hint(
            changelog.Changelog([], changelog.SOURCE_STORE, nearby_checkout=REPO_ROOT),
            "out.md",
        )
        is None
    )
    assert (
        changelog.live_store_hint(
            changelog.Changelog([], changelog.SOURCE_PACKAGED, nearby_checkout=None),
            "out.md",
        )
        is None
    )


@needs_this_store
def test_output_names_which_data_it_read_and_the_live_command_when_a_checkout_is_near(
    monkeypatch, tmp_path: Path
) -> None:
    """KYO-372.c2, exercised through the command itself. ``buildinfo.source_tree_root``
    is forced to ``None`` here -- standing in for `kyo` on PATH being a packaged build
    -- while the working directory stays this repository's own checkout, so
    ``changelog.load()``'s packaged branch finds a live store right beside it and says
    so, naming the command that reads it directly."""
    from kyosei import buildinfo

    buildinfo.regenerate(REPO_ROOT)  # a real, current packaged snapshot to read
    monkeypatch.setattr(buildinfo, "source_tree_root", lambda *a, **k: None)
    monkeypatch.chdir(REPO_ROOT)
    destination = tmp_path / "out.md"
    result = invoke(["changelog", "--all", "--output", str(destination)])
    assert result.code == 0, result
    assert "packaged with this build" in result.out
    assert "NOTE:" in result.out
    assert str(REPO_ROOT) in result.out
    assert f".venv/bin/python -m kyosei changelog --all --output {destination}" in result.out


def test_the_named_remedy_clears_the_failure_when_kyo_on_path_is_packaged(
    tmp_path: Path, rendered: str
) -> None:
    """KYO-372.c1 and c3: the remedy the freshness test names must work when `kyo` on
    PATH is a packaged build whose snapshot predates the store -- the condition
    whenever `kyo` is a bundled runtime rather than this checkout (KYO-372's own
    record).

    The stand-in: a shell script named `kyo`, first on PATH, that writes an obviously
    stale placeholder to whatever `--output FILE` it is given -- standing in for a
    packaged build's snapshot, which is baked at pack time and cannot see a record
    completed since. If ``CHANGELOG_REGENERATE_COMMAND`` regressed back to its
    pre-KYO-372 form (`kyo changelog --all --output ...`), running the remedy would
    shell out to this stand-in and this test would fail on CONTENT, not a crash -- the
    silent-success shape this record is about.

    Run in an isolated directory rather than this checkout's own tree: the remedy's
    `--output docs/reference/changelog.md` is a real write, and this repository's own
    committed copy is not this test's to touch. `.venv` is a symlink to this checkout's
    real one rather than a copy -- an editable install's `__file__` answers for the
    checkout it was installed FROM regardless of which copy of the interpreter it is
    run through, which is exactly `buildinfo.source_tree_root`'s contract, so the
    symlinked interpreter still renders REPO_ROOT's own live store.
    """
    venv = REPO_ROOT / ".venv"
    if not (venv / "bin" / "python").exists():  # pragma: no cover - provisioning gap
        pytest.skip("`.venv` is not provisioned here -- run `make venv` first")

    stand_in_bin = tmp_path / "stand-in-bin"
    stand_in_bin.mkdir()
    stand_in_kyo = stand_in_bin / "kyo"
    stand_in_kyo.write_text(
        "#!/bin/sh\n"
        'prev=""\n'
        'out=""\n'
        'for arg in "$@"; do\n'
        '  if [ "$prev" = "--output" ]; then out="$arg"; fi\n'
        '  prev="$arg"\n'
        "done\n"
        'if [ -n "$out" ]; then\n'
        "  printf 'STALE-PACKAGED-SNAPSHOT (KYO-372.c3 stand-in)\\n' > \"$out\"\n"
        "fi\n",
        encoding="utf-8",
    )
    stand_in_kyo.chmod(0o755)

    workdir = tmp_path / "workdir"
    (workdir / "docs" / "reference").mkdir(parents=True)
    (workdir / ".venv").symlink_to(venv)

    env = {**os.environ}
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    env["PATH"] = f"{stand_in_bin}:{os.environ.get('PATH', '')}"

    result = subprocess.run(
        changelog.CHANGELOG_REGENERATE_COMMAND.split(),
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    written = (workdir / changelog.CHANGELOG_PATH).read_text(encoding="utf-8")
    assert written == rendered, (
        "the named remedy did not clear the failure -- with a stand-in packaged `kyo` "
        "first on PATH, it produced:\n\n" + written[:200]
    )


def test_output_into_a_directory_that_does_not_exist_is_refused(nowhere: Path) -> None:
    """Law 1 again: the remedy clears the error."""
    refused = invoke(["changelog", "--all", "--output", str(nowhere / "nope" / "c.md")])
    assert refused.code != 0
    assert "does not exist" in refused.err and "--output" in refused.err
    (nowhere / "nope").mkdir()
    cleared = invoke(["changelog", "--all", "--output", str(nowhere / "nope" / "c.md")])
    assert cleared.code == 0, cleared


# ------------------------------------ c6: the build writes both, in a wheel that is real


def _build_wheel_in_the_checkout(out: Path) -> subprocess.CompletedProcess:
    """``uv build --wheel`` in THIS checkout, one at a time (KYO-609).

    The build stays in the checkout because the stamp must name this checkout's HEAD,
    which a copied tree cannot show (KYO-601.d1). It runs under
    :func:`checkout_build_lock`, since under pytest-xdist each worker that receives a
    test of this module builds its own wheel, and two builds sharing the checkout's
    ``build/`` directory fail.
    """
    with checkout_build_lock(REPO_ROOT):
        return subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(out)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )


def test_a_build_in_the_checkout_waits_for_the_one_already_running(
    tmp_path: Path, monkeypatch
) -> None:
    """KYO-609.c1: while another holder has the checkout's build lock, the wheel build
    does not start; it starts once the lock is released. Fails if the build stops
    taking the lock. A stub ``uv`` records that it ran, so nothing is built here."""
    import threading

    started = tmp_path / "uv-started"
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "uv"
    stub.write_text(f'#!/bin/sh\ntouch "{started}"\n', encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    import fcntl

    with open(checkout_build_lock_path(REPO_ROOT), "a+b") as other_holder:
        fcntl.flock(other_holder.fileno(), fcntl.LOCK_EX)
        builder = threading.Thread(target=_build_wheel_in_the_checkout, args=(tmp_path,))
        builder.start()
        builder.join(timeout=1.0)
        assert not started.exists(), "the build ran while another build held the lock"
        fcntl.flock(other_holder.fileno(), fcntl.LOCK_UN)
    builder.join(timeout=30)
    assert not builder.is_alive()
    assert started.exists(), "the build never ran after the lock was released"


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory) -> Path:
    """A wheel built from THIS checkout, by the real build frontend, into a temp dir.

    Not a mock and not a call to the hook: what c6 asks about is whether the hook is
    wired into the build a consumer runs, and the only thing that can answer that is a
    build a consumer's frontend would perform. ``uv build --wheel`` builds in an
    isolated environment provisioned from ``build-system.requires``, so this also proves
    that list is complete — a missing ``pyyaml`` there would fail here and nowhere else.
    """
    if shutil.which("uv") is None:  # pragma: no cover - depends on the machine
        pytest.skip("uv is not installed, so a real wheel cannot be built here")
    out = tmp_path_factory.mktemp("wheel")
    result = _build_wheel_in_the_checkout(out)
    assert result.returncode == 0, f"uv build failed:\n{result.stdout}\n{result.stderr}"
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


def test_a_real_wheel_built_from_this_tree_carries_the_stamp_and_the_changelog(
    built_wheel: Path,
) -> None:
    """KYO-226.c1 and c6. The two files are IN the archive, and the stamp names this
    checkout's HEAD — so an installed copy can answer both questions with no ``.git``,
    no store and no network."""
    with zipfile.ZipFile(built_wheel) as archive:
        names = set(archive.namelist())
        assert f"kyosei/{buildinfo.STAMP_FILENAME}" in names, sorted(names)[:20]
        assert f"kyosei/{changelog.CHANGELOG_FILENAME}" in names, sorted(names)[:20]
        stamp = json.loads(archive.read(f"kyosei/{buildinfo.STAMP_FILENAME}"))
    assert stamp["commit"] == _git(REPO_ROOT, "rev-parse", "HEAD")
    assert stamp["commit_date"] == _utc_date(REPO_ROOT)
    # KYO-398.c1: the full UTC instant, of which commit_date is the date.
    assert stamp["commit_time"].endswith("Z") and stamp["commit_time"][:10] == stamp["commit_date"]
    assert stamp["version"] == "0.1.0"
    assert isinstance(stamp["dirty"], bool)
    # KYO-445.c2: the real build hook records the release bit too, and this checkout's
    # HEAD is a release only if it carries the tag and was clean when built.
    head_tags = _git(REPO_ROOT, "tag", "--points-at", "HEAD").split()
    expected = f"v{stamp['version']}" in head_tags and stamp["dirty"] is False
    assert stamp["release"] is expected, stamp


def test_the_wheel_declares_apache_2_and_carries_the_license_text(built_wheel: Path) -> None:
    """KYO-624.c1: the wheel's metadata names the license as an SPDX expression and the
    archive carries the repository's LICENSE byte for byte. Fails if `license` or
    `license-files` leaves pyproject.toml, or if LICENSE stops being the file shipped."""
    with zipfile.ZipFile(built_wheel) as archive:
        names = archive.namelist()
        (metadata_name,) = [n for n in names if n.endswith(".dist-info/METADATA")]
        metadata = archive.read(metadata_name).decode("utf-8")
        headers = metadata.split("\n\n", 1)[0].splitlines()
        assert "License-Expression: Apache-2.0" in headers, headers
        assert "License-File: LICENSE" in headers, headers
        (license_name,) = [n for n in names if n.endswith(".dist-info/licenses/LICENSE")]
        shipped = archive.read(license_name)
    assert shipped == (REPO_ROOT / "LICENSE").read_bytes()
    assert b"Apache License\n                           Version 2.0, January 2004" in shipped


@needs_this_store
def test_the_wheel_s_changelog_is_the_store_as_it_is_now(built_wheel: Path) -> None:
    """c6's "the packaged data file being regenerated by the build". Compared against
    the live store rather than against the previous wheel: the property is that the
    packaged copy cannot be older than the build carrying it, and only a comparison with
    the source of truth can see that."""
    with zipfile.ZipFile(built_wheel) as archive:
        packaged = json.loads(archive.read(f"kyosei/{changelog.CHANGELOG_FILENAME}"))
    assert "unavailable" not in packaged, packaged.get("unavailable")
    live = changelog.entries_from_store(REPO_ROOT)
    assert [entry["id"] for entry in packaged["entries"]] == [entry.id for entry in live]
    assert packaged["entries"][0]["title"] == live[0].title
    # KYO-398.c2: each packaged entry carries its full UTC completion instant.
    assert packaged["entries"][0]["completed_at"] == live[0].completed_at
    assert packaged["generated_from_commit"] == _git(REPO_ROOT, "rev-parse", "HEAD")


def test_the_installed_copy_reads_the_packaged_file_and_not_a_store(
    built_wheel: Path, tmp_path: Path
) -> None:
    """The property the packaging exists for: an installed copy answers both commands
    with no checkout above it and no Kyosei store anywhere near it.

    Installed into its own throwaway environment — never into the environment running
    this suite, whose ``kyosei`` is this working tree.
    """
    if shutil.which("uv") is None:  # pragma: no cover - depends on the machine
        pytest.skip("uv is not installed")
    venv = tmp_path / "venv"
    subprocess.run(["uv", "venv", "--python", "3.12", str(venv)], check=True, capture_output=True)
    subprocess.run(
        ["uv", "pip", "install", "--quiet", str(built_wheel)],
        check=True,
        capture_output=True,
        # VIRTUAL_ENV set EXPLICITLY, which is the whole point: `uv pip install` honours
        # an inherited one, and the inherited one here is the venv running this suite.
        # Installing this wheel into it would overwrite the working tree under test.
        env={**os.environ, "VIRTUAL_ENV": str(venv)},
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    kyo = venv / "bin" / "kyo"

    version = subprocess.run(
        [str(kyo), "version", "--json"], cwd=elsewhere, capture_output=True, text=True, check=True
    )
    payload = json.loads(version.stdout)
    assert payload["source"] == buildinfo.SOURCE_STAMP, (
        "an installed copy read something other than its stamp — if it found a git "
        "checkout above it, `source_tree_root` is answering for somebody else's tree"
    )
    assert payload["commit"] == _git(REPO_ROOT, "rev-parse", "HEAD")

    log = subprocess.run(
        [str(kyo), "changelog", "--all", "--json"],
        cwd=elsewhere,
        capture_output=True,
        text=True,
        check=True,
    )
    entries = json.loads(log.stdout)
    assert entries["source"] == changelog.SOURCE_PACKAGED
    if THIS_STORE.is_dir():
        assert entries["total"] == len(changelog.entries_from_store(REPO_ROOT))


# -------------------------------------------------------------------- the generator itself


def test_entries_are_read_through_the_existing_readers_and_not_a_second_parser() -> None:
    """The rule the record names, asserted structurally rather than by review.

    ``rebuild.read_record_documents`` is the one door a record document enters through
    (KYO-179) and ``events_ledger.read_events_in`` is the events ledger's reader. A second
    parser over ``kyosei/export/*.json`` would be a second opinion about what a record
    is — so this module must open no export file itself.
    """
    source = (REPO_ROOT / "src" / "kyosei" / "changelog.py").read_text(encoding="utf-8")
    assert "rebuild.read_record_documents" in source
    assert "events_ledger.read_events_in" in source
    for forbidden in (".glob(", "read_text(", "iterdir("):
        assert forbidden not in source.split("def _completion_dates", 1)[0], forbidden


def test_grouping_puts_a_record_with_no_scope_last() -> None:
    """The group order the committed markdown depends on, including the case this
    store has no instance of — so it is right the first time somebody files one."""
    entries = [
        changelog.Entry("A-1", "task", "zzz", "z", "2026-01-01", changelog.DATED_BY_EVENT),
        changelog.Entry("A-2", "task", None, "n", "2026-01-02", changelog.DATED_BY_EVENT),
        changelog.Entry("A-3", "task", "aaa", "a", "2026-01-03", changelog.DATED_BY_EVENT),
    ]
    assert [name for name, _ in changelog.grouped(entries)] == [
        "aaa",
        "zzz",
        changelog.NO_SCOPE,
    ]


def test_same_day_records_are_ordered_by_id_so_the_document_is_stable() -> None:
    """``docs/reference/changelog.md`` is compared byte for byte, so the render may not
    depend on the order the filesystem happened to list the export in."""
    entries = [
        changelog.Entry(f"KYO-{n}", "task", "cli", str(n), "2026-01-01", changelog.DATED_BY_EVENT)
        for n in (9, 100, 11)
    ]
    assert [e.id for e in changelog.sort_entries(entries)] == ["KYO-100", "KYO-11", "KYO-9"]
    assert [e.id for e in changelog.sort_entries(list(reversed(entries)))] == [
        "KYO-100",
        "KYO-11",
        "KYO-9",
    ]


def test_the_render_does_not_depend_on_terminal_width(monkeypatch, rendered: str) -> None:
    """The hazard the equality check creates, closed — KYO-138's lesson, applied before
    it could cost anything here."""
    monkeypatch.setenv("COLUMNS", "40")
    loaded = changelog.load()
    narrow = changelog.markdown(loaded, loaded.entries, None)
    monkeypatch.setenv("COLUMNS", "400")
    wide = changelog.markdown(loaded, loaded.entries, None)
    assert narrow == wide == rendered


def test_both_commands_appear_in_the_generated_reference() -> None:
    """KYO-138's property, holding over this record's two new commands: the reference is
    generated, so they are in it without anybody having written a sentence about them."""
    from kyosei.cli import reference_markdown

    text = reference_markdown(build_parser())
    for name in ("version", "changelog"):
        assert f"## `kyo {name}`" in text
        section = text.split(f"## `kyo {name}`", 1)[-1].split("\n## ", 1)[0]
        assert "Needs a Kyosei project: **no**" in section
        assert "Writes to the store: **no**" in section


# ------------------------------- KYO-577: a completion outside a wave, then the close
#
# The sequence: a record is confirmed done with `kyo confirm`, outside any wave; the
# next wave merges; `make test` fails on the committed changelog, which was generated
# before that record closed; and `kyo close-wave` refuses (`suite-not-passed`) before
# its own regeneration could fix it. The project here declares its generator and its
# suite in `close_wave:` and nowhere else — a different path from this repository's own
# changelog, so nothing below can pass by knowing that path.

GENERATED = "docs/changes.md"
CONFIRM_WORDS = "The maintainer: \"looks right\""


#: The project's own generator: every completed record's id and title, from the
#: store's git-canonical export — a document a store change makes stale, which is all
#: this needs. (`kyo changelog` is Kyosei's own release changelog, not a project's.)
GENERATOR_SCRIPT = """\
import json, pathlib, sys
root = pathlib.Path(__file__).resolve().parent
rows = []
for path in sorted((root / "kyosei" / "export").glob("*.json")):
    doc = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(doc, dict) and doc.get("status") == sys.argv[2]:
        rows.append(f"- {doc['id']}: {doc['title']}")
pathlib.Path(sys.argv[1]).write_text("\\n".join(rows) + "\\n", encoding="utf-8")
"""


def _changelog_project(project: Path, tmp_path: Path, *, generate: bool = True) -> dict:
    """Declare a generator writing GENERATED and a suite holding the committed copy to
    the store, the way this repository's changelog test does; commit a first copy."""
    declared = _declare_changelog(project, tmp_path, generate=generate)
    from .conftest import git_init

    git_init(project)
    assert _shell(project, declared["generator"]) == 0
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "the first generated copy")
    return declared


def _declare_changelog(project: Path, tmp_path: Path, *, generate: bool = True) -> dict:
    """:func:`_changelog_project`'s declarations alone, for a project that is already a
    git repository with history of its own."""
    complete = _complete_status(project)
    fresh = tmp_path / "fresh.md"
    python = shlex.quote(sys.executable)
    (project / "gen.py").write_text(GENERATOR_SCRIPT, encoding="utf-8")
    (project / "check.sh").write_text(
        f"{python} gen.py {shlex.quote(str(fresh))} {complete} && "
        f"cmp -s {shlex.quote(str(fresh))} {GENERATED}\n",
        encoding="utf-8",
    )
    generator = f"{python} gen.py {GENERATED} {complete}"
    suite = "sh check.sh"
    block = "close_wave:\n  suite:\n    - " + json.dumps(suite) + "\n"
    if generate:
        block += "  generate:\n    - " + json.dumps(generator) + "\n"
    config_file = project / "kyosei" / "config.yml"
    config_file.write_text(config_file.read_text(encoding="utf-8") + block, encoding="utf-8")
    (project / "docs").mkdir(exist_ok=True)
    return {"suite": suite, "generator": generator}


def _shell(project: Path, line: str) -> int:
    return subprocess.run(line, shell=True, cwd=project, check=False).returncode


def _store_commit(project: Path, message: str = "store") -> None:
    """The orchestrator's habitual store commit: `kyosei/` only, when it changed."""
    _git(project, "add", "-A", "--", "kyosei")
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=project).returncode:
        _git(project, "commit", "-q", "-m", message)


def _finished(kyo, title: str) -> str:
    from .conftest import new_record, verify_criterion

    record = new_record(kyo, title=title, criteria=["it works"])
    verify_criterion(kyo, f"{record}.c1", method="machine-checked")
    return record


def _awaiting_the_maintainer(kyo, title: str) -> str:
    from .conftest import new_record

    record = new_record(kyo, title=title, criteria=["the maintainer looks"])
    unobserved = kyo(
        "verify", f"{record}.c1", "--verdict", "unmet-unobserved", "--method",
        "reasoned-about", "--why", "needs a person to look",
    )  # fmt: skip
    assert unobserved.code == 0, unobserved
    gated = kyo("gate", record, "awaiting:human-review")
    assert gated.code == 0, gated
    return record


def _confirm(kyo, record: str):
    return kyo("confirm", record, "--quote", CONFIRM_WORDS, "--json")


def _closing_sequence(project: Path, suite: str, *ids: str):
    """`<suite> && kyo close-wave <ids> --suite-passed "<suite>"`, exactly: the suite
    through a shell, the kyo part in-process, chained on the suite's own exit status."""
    status = _shell(project, suite)
    assert status == 0, (
        f"the suite failed (exit {status}) before close-wave could run — the committed "
        f"{GENERATED} has fallen behind the store"
    )
    return invoke(["--root", str(project), "close-wave", *ids, "--suite-passed", suite])


def _status_of(kyo, record: str) -> str:
    return kyo("show", record, "--json").json["status"]


def _complete_status(project: Path) -> str:
    return contracts.load_status_ladder(project / "kyosei" / "statuses.yml").complete


def _clean_outside_store(project: Path) -> str:
    return _git(
        project, "status", "--porcelain", "--untracked-files=all", "--", ".",
        ":(exclude)kyosei",
    )  # fmt: skip


def test_kyo577_c1_a_record_confirmed_done_outside_a_wave_leaves_the_close_passing(
    kyo, project: Path, tmp_path: Path
) -> None:
    """KYO-577.c1: the sequence described above, with no hand-run regeneration
    and no extra commit anywhere in it. Fails on the code before KYO-577, at the suite."""
    declared = _changelog_project(project, tmp_path)
    first = _finished(kyo, "The first wave's record")
    _store_commit(project)
    closed = _closing_sequence(project, declared["suite"], first)
    assert closed.code == 0, closed  # the last close-wave before the confirmation

    gated = _awaiting_the_maintainer(kyo, "A record the maintainer must look at")
    wave = _finished(kyo, "The next wave's record")
    _store_commit(project)

    confirmed = _confirm(kyo, gated)
    assert confirmed.code == 0, confirmed
    assert confirmed.json["complete"] is True, confirmed
    assert _status_of(kyo, gated) == _complete_status(project)
    _store_commit(project)  # whatever the orchestrator commits by habit; nothing by hand

    result = _closing_sequence(project, declared["suite"], wave)

    assert result.code == 0, result
    assert _status_of(kyo, wave) == _complete_status(project)
    assert _shell(project, declared["suite"]) == 0, "the closed wave's own copy is stale"
    text = (project / GENERATED).read_text(encoding="utf-8")
    assert "A record the maintainer must look at" in text
    assert "The next wave's record" in text
    assert _clean_outside_store(project) == ""


def test_kyo577_confirm_commits_the_generated_documents_with_the_store(
    kyo, project: Path, tmp_path: Path
) -> None:
    """What `kyo confirm` now runs, and the one commit it makes: the declared generate
    line, and its output beside the store's export."""
    _changelog_project(project, tmp_path)
    gated = _awaiting_the_maintainer(kyo, "Confirmed into the changelog")
    _store_commit(project)
    head = _git(project, "rev-parse", "HEAD")

    confirmed = _confirm(kyo, gated)

    assert confirmed.code == 0, confirmed
    refresh = confirmed.json["refresh"]
    assert refresh["status"] == "committed", refresh
    assert [step["exit_code"] for step in refresh["generate"]] == [0]
    assert _git(project, "rev-parse", "HEAD^") == head
    changed = _git(project, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert GENERATED in changed
    assert f"kyosei/export/{gated}.json" in changed
    assert "(kyo confirm)" in _git(project, "log", "-1", "--format=%s")
    assert _clean_outside_store(project) == ""


def test_kyo577_a_project_declaring_no_generate_steps_confirms_exactly_as_before(
    kyo, project: Path, tmp_path: Path
) -> None:
    """A consumer that declares no `close_wave: generate:` gets no shell command, no
    commit and no new line from `kyo confirm`."""
    _changelog_project(project, tmp_path, generate=False)
    gated = _awaiting_the_maintainer(kyo, "No generators here")
    _store_commit(project)
    head = _git(project, "rev-parse", "HEAD")
    copy = (project / GENERATED).read_bytes()

    confirmed = _confirm(kyo, gated)

    assert confirmed.code == 0, confirmed
    assert confirmed.json["complete"] is True
    assert confirmed.json["refresh"]["status"] == "none-declared"
    assert confirmed.json["refresh"]["generate"] == []
    assert _git(project, "rev-parse", "HEAD") == head
    assert (project / GENERATED).read_bytes() == copy
    second = _awaiting_the_maintainer(kyo, "Nor here")
    printed = kyo("confirm", second, "--quote", CONFIRM_WORDS)
    assert printed.code == 0, printed
    assert "generate" not in printed.out and "INCOMPLETE" not in printed.out
    assert _git(project, "rev-parse", "HEAD") == head


def _named_line(text: str, prefix: str = "kyo close-wave ") -> list[str]:
    """The first backticked `kyo close-wave ...` line in ``text``, as argv after `kyo`."""
    line = next(part for part in text.split("`") if part.startswith(prefix))
    return shlex.split(line)[1:]


def test_kyo577_a_dirty_tree_skips_the_refresh_and_the_line_it_names_clears_it(
    kyo, project: Path, tmp_path: Path
) -> None:
    declared = _changelog_project(project, tmp_path)
    gated = _awaiting_the_maintainer(kyo, "Confirmed over uncommitted work")
    wave = _finished(kyo, "The wave after it")
    _store_commit(project)
    head = _git(project, "rev-parse", "HEAD")
    (project / "wip.py").write_text("x = 1\n", encoding="utf-8")

    confirmed = _confirm(kyo, gated)

    assert confirmed.code == 0, confirmed  # KYO-415.c3: incompleteness keeps exit 0
    assert confirmed.json["complete"] is False
    assert confirmed.json["refresh"]["status"] == "skipped"
    assert _status_of(kyo, gated) == _complete_status(project)  # the confirmation stands
    assert _git(project, "rev-parse", "HEAD") == head  # and nothing was committed
    remedy = confirmed.json["refresh"]["remedy"]
    assert remedy == f"kyo close-wave {gated}"
    assert _shell(project, declared["suite"]) != 0  # the stale copy

    # The remedy, as named: commit the stray work, then the line.
    _git(project, "add", "wip.py")
    _git(project, "commit", "-q", "-m", "the work in progress")
    _store_commit(project)
    cleared = invoke(["--root", str(project), *shlex.split(remedy)[1:]])
    assert cleared.code == 0, cleared
    assert _clean_outside_store(project) == ""
    assert _closing_sequence(project, declared["suite"], wave).code == 0


def test_kyo577_the_skipped_refresh_is_printed_with_its_line(
    kyo, project: Path, tmp_path: Path
) -> None:
    _changelog_project(project, tmp_path)
    gated = _awaiting_the_maintainer(kyo, "Printed")
    _store_commit(project)
    (project / "wip.py").write_text("x = 1\n", encoding="utf-8")
    out = kyo("confirm", gated, "--quote", CONFIRM_WORDS).out
    assert out.startswith(f"confirmed {gated}")
    assert "INCOMPLETE" in out and "not refreshed (skipped)" in out
    assert "generated documents NOT refreshed (skipped)" in out and "wip.py" in out
    assert f"`kyo close-wave {gated}`" in out


def test_kyo577_a_failing_generate_step_leaves_the_confirmation_applied_and_says_so(
    kyo, project: Path, tmp_path: Path
) -> None:
    declared = _changelog_project(project, tmp_path)
    config_file = project / "kyosei" / "config.yml"
    working = config_file.read_text(encoding="utf-8")
    failing = shlex.join([sys.executable, "-c", "raise SystemExit(3)"])
    broken = working.replace(json.dumps(declared["generator"]), json.dumps(failing))
    assert broken != working
    config_file.write_text(broken, encoding="utf-8")
    gated = _awaiting_the_maintainer(kyo, "Confirmed while the generator is broken")
    _store_commit(project)
    head = _git(project, "rev-parse", "HEAD")

    confirmed = _confirm(kyo, gated)

    assert confirmed.code == 0, confirmed
    assert confirmed.json["complete"] is False
    refresh = confirmed.json["refresh"]
    assert refresh["status"] == "failed"
    assert [step["exit_code"] for step in refresh["generate"]] == [3]
    assert _status_of(kyo, gated) == _complete_status(project)
    assert _git(project, "rev-parse", "HEAD") == head

    # Fix the step, then run the line it named.
    config_file.write_text(working, encoding="utf-8")
    _store_commit(project, "store: the generator fixed")
    cleared = invoke(["--root", str(project), *shlex.split(refresh["remedy"])[1:]])
    assert cleared.code == 0, cleared
    assert _shell(project, declared["suite"]) == 0
    assert _clean_outside_store(project) == ""


def test_kyo577_a_pass_recorded_before_the_confirm_is_carried_across_its_commit(
    kyo, project: Path, tmp_path: Path
) -> None:
    """A wave whose close-wave refused on a gated record: the suite passed, the
    maintainer confirms, close-wave runs again. The confirmation's commit changed only
    generator output, so the pass holds — no second whole suite."""
    declared = _changelog_project(project, tmp_path)
    gated = _awaiting_the_maintainer(kyo, "Gated inside the wave")
    wave = _finished(kyo, "Its wave-mate")
    _store_commit(project)
    refused = _closing_sequence(project, declared["suite"], wave, gated)
    assert refused.code != 0 and "record-not-closable" in refused.err, refused

    confirmed = _confirm(kyo, gated)
    assert confirmed.json["refresh"]["status"] == "committed", confirmed
    again = invoke(["--root", str(project), "close-wave", wave, gated])

    assert again.code == 0, again
    assert "carried across" in again.out
    assert _shell(project, declared["suite"]) == 0
    assert _clean_outside_store(project) == ""


def test_kyo577_a_pass_already_stale_is_not_carried(
    kyo, project: Path, tmp_path: Path
) -> None:
    """The exemption is the regeneration commit only: a pass that a code change had
    already made stale stays stale across it."""
    declared = _changelog_project(project, tmp_path)
    gated = _awaiting_the_maintainer(kyo, "Gated")
    wave = _finished(kyo, "Wave")
    _store_commit(project)
    assert _shell(project, declared["suite"]) == 0
    recorded = invoke(["--root", str(project), "close-wave", "--suite-passed", declared["suite"]])
    assert recorded.code == 0, recorded
    (project / "code.py").write_text("y = 2\n", encoding="utf-8")
    _git(project, "add", "code.py")
    _git(project, "commit", "-q", "-m", "a code change nobody ran the suite on")

    assert _confirm(kyo, gated).json["refresh"]["status"] == "committed"
    stale = invoke(["--root", str(project), "close-wave", wave])

    assert stale.code != 0, stale
    assert "suite-not-passed" in stale.err
    assert "carried" not in stale.err


def _move_to_completion(kyo, project: Path, record: str):
    """`kyo move` up the ladder's rungs to completion, as typed; the last move's run."""
    from kyosei.cmd_accept import _rungs_to_completion

    ladder = contracts.load_status_ladder(project / "kyosei" / "statuses.yml")
    last = None
    for rung in _rungs_to_completion(ladder, _status_of(kyo, record)):
        last = kyo("move", record, rung, "--json")
        assert last.code == 0, last
    assert last is not None
    return last


def test_kyo577_c2_a_completion_that_did_not_regenerate_is_named_by_the_refusal(
    kyo, project: Path, tmp_path: Path
) -> None:
    """KYO-577.c2: a completion that could not regenerate leaves the manual step — and
    the refusal the orchestrator meets names it in full, and running it as printed
    clears the way for the documented sequence. Since KYO-603 a `kyo move` regenerates
    by itself from a clean tree, so the route that leaves the step here is a move made
    over uncommitted work, committed afterwards."""
    declared = _changelog_project(project, tmp_path)
    moved = _finished(kyo, "Moved by hand")
    wave = _finished(kyo, "The wave")
    (project / "wip.py").write_text("x = 1\n", encoding="utf-8")
    assert _move_to_completion(kyo, project, moved).json["refresh"]["status"] == "skipped"
    _git(project, "add", "wip.py")
    _git(project, "commit", "-q", "-m", "the work in progress")
    _store_commit(project)
    assert _shell(project, declared["suite"]) != 0  # the manual step remains

    refused = invoke(["--root", str(project), "close-wave", wave])

    assert refused.code != 0, refused
    assert "suite-not-passed" in refused.err
    assert f"`kyo close-wave {moved}`" in refused.err
    cleared = invoke(["--root", str(project), *_named_line(refused.err)])
    assert cleared.code == 0, cleared
    assert "suite: not required" in cleared.out
    assert _closing_sequence(project, declared["suite"], wave).code == 0
    assert _shell(project, declared["suite"]) == 0


# ------------------------------------------ KYO-603: a typed move, and accept --close
#
# close-wave regenerated the changelog, then the orchestrator moved a plan to done
# with `kyo move`. Every whole-suite run from that commit on failed the freshness
# test above until somebody regenerated by hand, and two dispatched workers reproduced it
# as a pre-existing trunk failure. `kyo confirm` already regenerated (KYO-577); `kyo move`
# and `kyo accept --close` now do the same.


def _head(project: Path) -> str:
    return _git(project, "rev-parse", "HEAD")


def test_kyo603_c1_a_record_moved_to_completion_leaves_the_suite_passing_at_that_commit(
    kyo, project: Path, tmp_path: Path
) -> None:
    """KYO-603.c1: the sequence above, with no hand regeneration and no commit by
    hand after the move. Fails on the code before KYO-603, at the suite: the move wrote
    the store and nothing regenerated the document the suite holds to it."""
    declared = _changelog_project(project, tmp_path)
    wave = _finished(kyo, "The wave's record")
    _store_commit(project)
    closed = _closing_sequence(project, declared["suite"], wave)
    assert closed.code == 0, closed
    after_close = _head(project)

    moved = _finished(kyo, "Moved to done by hand after the close")
    last = _move_to_completion(kyo, project, moved)

    assert _status_of(kyo, moved) == _complete_status(project)
    refresh = last.json["refresh"]
    assert refresh["status"] == "committed", refresh
    assert [step["exit_code"] for step in refresh["generate"]] == [0]
    # One commit past the close, holding the store's export and the generator output.
    assert _git(project, "rev-parse", "HEAD^") == after_close
    changed = _git(project, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert GENERATED in changed and f"kyosei/export/{moved}.json" in changed
    assert "(kyo move)" in _git(project, "log", "-1", "--format=%s")
    # "At that commit": the committed copy, not just the working tree's.
    assert "Moved to done by hand after the close" in _git(
        project, "show", f"HEAD:{GENERATED}"
    )
    assert _git(project, "status", "--porcelain", "--", GENERATED, "kyosei/export") == ""
    assert _shell(project, declared["suite"]) == 0
    # The pass close-wave recorded is carried across the regeneration commit, so the
    # next wave's close needs no second whole suite to vouch for unchanged code.
    nxt = _finished(kyo, "The next wave")
    _store_commit(project)
    again = invoke(["--root", str(project), "close-wave", nxt])
    assert again.code == 0, again
    assert "carried across" in again.out


def test_kyo603_the_typed_move_prints_what_it_regenerated(
    kyo, project: Path, tmp_path: Path
) -> None:
    _changelog_project(project, tmp_path)
    moved = _finished(kyo, "Printed")
    _store_commit(project)
    from kyosei.cmd_accept import _rungs_to_completion

    ladder = contracts.load_status_ladder(project / "kyosei" / "statuses.yml")
    rungs = _rungs_to_completion(ladder, _status_of(kyo, moved))
    for rung in rungs[:-1]:
        assert kyo("move", moved, rung).code == 0
    out = kyo("move", moved, rungs[-1]).out
    assert out.startswith(f"moved {moved}")
    assert "generated: " in out and "committed " in out


def test_kyo603_a_move_short_of_completion_regenerates_nothing(
    kyo, project: Path, tmp_path: Path
) -> None:
    """Only a move into or out of the completion status touches the documents."""
    _changelog_project(project, tmp_path)
    record = _finished(kyo, "Part way")
    _store_commit(project)
    head = _head(project)
    from kyosei.cmd_accept import _rungs_to_completion

    ladder = contracts.load_status_ladder(project / "kyosei" / "statuses.yml")
    first = _rungs_to_completion(ladder, _status_of(kyo, record))[0]
    assert first != ladder.complete
    moved = kyo("move", record, first, "--json")
    assert moved.code == 0, moved
    assert "refresh" not in moved.json
    assert _head(project) == head


def test_kyo603_a_project_declaring_no_generate_steps_moves_exactly_as_before(
    kyo, project: Path, tmp_path: Path
) -> None:
    _changelog_project(project, tmp_path, generate=False)
    record = _finished(kyo, "No generators")
    second = _finished(kyo, "Nor here")
    _store_commit(project)
    head = _head(project)
    copy = (project / GENERATED).read_bytes()

    last = _move_to_completion(kyo, project, record)

    assert "refresh" not in last.json
    assert _head(project) == head
    assert (project / GENERATED).read_bytes() == copy
    from kyosei.cmd_accept import _rungs_to_completion

    ladder = contracts.load_status_ladder(project / "kyosei" / "statuses.yml")
    rungs = _rungs_to_completion(ladder, _status_of(kyo, second))
    for rung in rungs[:-1]:
        assert kyo("move", second, rung).code == 0
    printed = kyo("move", second, rungs[-1])
    assert printed.code == 0, printed
    assert "generate" not in printed.out and "committed" not in printed.out
    assert _head(project) == head


def test_kyo603_a_move_over_uncommitted_work_names_the_regeneration_as_its_next_step(
    kyo, project: Path, tmp_path: Path
) -> None:
    """c1's other half: when the move cannot regenerate, it names the line that does,
    and that line, run as printed once the stray work is committed, clears the suite."""
    declared = _changelog_project(project, tmp_path)
    record = _finished(kyo, "Moved over uncommitted work")
    _store_commit(project)
    head = _head(project)
    (project / "wip.py").write_text("x = 1\n", encoding="utf-8")

    last = _move_to_completion(kyo, project, record)

    assert last.code == 0  # the move stands, and exits as a move does
    assert _status_of(kyo, record) == _complete_status(project)
    refresh = last.json["refresh"]
    assert refresh["status"] == "skipped", refresh
    assert refresh["remedy"] == f"kyo close-wave {record}"
    assert _head(project) == head
    printed = kyo("move", record, _complete_status(project)).out
    assert "nothing to do" in printed  # a self-move regenerates nothing either

    _git(project, "add", "wip.py")
    _git(project, "commit", "-q", "-m", "the work in progress")
    assert _shell(project, declared["suite"]) != 0
    cleared = invoke(["--root", str(project), *shlex.split(refresh["remedy"])[1:]])
    assert cleared.code == 0, cleared
    assert _shell(project, declared["suite"]) == 0
    assert _clean_outside_store(project) == ""


def test_kyo603_the_skipped_refresh_is_printed_with_its_line(
    kyo, project: Path, tmp_path: Path
) -> None:
    _changelog_project(project, tmp_path)
    record = _finished(kyo, "Printed when skipped")
    _store_commit(project)
    from kyosei.cmd_accept import _rungs_to_completion

    ladder = contracts.load_status_ladder(project / "kyosei" / "statuses.yml")
    rungs = _rungs_to_completion(ladder, _status_of(kyo, record))
    for rung in rungs[:-1]:
        assert kyo("move", record, rung).code == 0
    (project / "wip.py").write_text("x = 1\n", encoding="utf-8")
    out = kyo("move", record, rungs[-1]).out
    assert "generated documents NOT refreshed (skipped)" in out and "wip.py" in out
    assert f"`kyo close-wave {record}`" in out


def test_kyo603_a_record_moved_out_of_completion_regenerates_too(
    kyo, project: Path, tmp_path: Path
) -> None:
    """Leaving the completion status changes the changelog as much as entering it."""
    declared = _changelog_project(project, tmp_path)
    record = _finished(kyo, "Reopened after all")
    _move_to_completion(kyo, project, record)
    assert "Reopened after all" in (project / GENERATED).read_text(encoding="utf-8")
    head = _head(project)

    reopened = kyo("move", record, "working", "--json")

    assert reopened.code == 0, reopened
    assert reopened.json["refresh"]["status"] == "committed", reopened.json["refresh"]
    assert _git(project, "rev-parse", "HEAD^") == head
    assert f"{record} working;" in _git(project, "log", "-1", "--format=%s")
    assert "Reopened after all" not in (project / GENERATED).read_text(encoding="utf-8")
    assert _shell(project, declared["suite"]) == 0


def test_kyo603_accept_close_regenerates_once_for_what_it_closed(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """`kyo accept --close` is the third completion route the record names. Its moves
    are composed through `cmd_records.cmd_move`, so the typed move's refresh does not
    run per rung; the acceptance runs it once, after every record it closed."""
    from .test_kyo329_accept import _closable

    record, dispatch_id = _closable(kyo, git_project)
    declared = _declare_changelog(git_project, tmp_path)
    assert _shell(git_project, declared["generator"]) == 0
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "declare the generator")
    head = _head(git_project)
    assert _shell(git_project, declared["suite"]) == 0

    accepted = kyo("accept", dispatch_id, "--close", "--json")

    assert accepted.code == 0, accepted
    assert accepted.json["complete"] is True, accepted.json
    assert _status_of(kyo, record) == "done"
    refresh = accepted.json["refresh"]
    assert refresh["status"] == "committed", refresh
    assert _git(git_project, "rev-parse", "HEAD^") == head  # one commit, not one per rung
    assert "(kyo accept --close)" in _git(git_project, "log", "-1", "--format=%s")
    assert _shell(git_project, declared["suite"]) == 0
    text = kyo("accept", dispatch_id, "--close").out  # a rerun closes nothing new
    assert "generated documents" not in text
