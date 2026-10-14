"""KYO-318: the three install commands refuse to write an older build over their source.

KYO-309 turned the READ side round — in the repository this package is built from,
`kyo doctor` and `kyo brief` compare against `src/kyosei/defaults/` and never name a
remedy that would destroy the newer text. It deliberately left the write side alone,
and its worker said so in its hand-back. This is that half.

``tests/test_install_skill.py`` covers the skill's refusal and ``tests/test_install_hooks.py``
the hooks' (which is also where KYO-318.c2's measurement lives). Here: the ledger's
refusal, the direction-of-age reading all three share, and the one sentence outside the
commands themselves that still named them — `kyo version`'s upgrade line (KYO-318.n1).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from kyosei import buildinfo, install_procedures, scaffold
from kyosei.install_skill import SKILL_ASSET
from kyosei.scaffold import PACKAGED_ASSET_SOURCE_DIR, PROCEDURES_ASSET

from .conftest import invoke


def _source(root: Path, asset) -> Path:
    return root / asset.source_path


def _make_source_repo(root: Path, asset, text: str) -> Path:
    path = _source(root, asset)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _ledger(project: Path) -> Path:
    return project / PROCEDURES_ASSET.target


# ------------------------------------------- the ledger: `kyo install-procedures`


def _a_lagging_build(project: Path) -> str:
    """This repository's ledger and its packaged source edited together, with the
    installed `kyo` still shipping the previous text. Returns the newer text."""
    newer = install_procedures.packaged_procedures_text() + "\n### A rule added today\n\nText.\n"
    _make_source_repo(project, PROCEDURES_ASSET, newer)
    _ledger(project).write_text(newer, encoding="utf-8")
    return newer


def test_install_procedures_refuses_to_merge_an_older_builds_ledger(
    kyo, project: Path
) -> None:
    """KYO-318.c1 for the ledger. The merge is additive (KYO-212), so the older build's
    copy does not overwrite the newer one — it re-inserts a rule this checkout deleted,
    and `--adopt` takes its wording out of that same older copy."""
    newer = _a_lagging_build(project)

    result = kyo("install-procedures")
    assert result.code != 0, result
    assert _ledger(project).read_text(encoding="utf-8") == newer
    assert "Nothing was written" in result.err
    assert str(PACKAGED_ASSET_SOURCE_DIR / PROCEDURES_ASSET.source) in result.err
    assert "`--adopt`" in result.err


def test_the_refusal_covers_adopt_and_adopt_preamble_too(kyo, project: Path) -> None:
    """`--adopt` is the most destructive way in — it replaces a rule's text outright
    with the installed build's wording — so it has to be inside the refusal, not beside
    it, exactly as KYO-151.c3's worktree refusal covers the whole call."""
    newer = _a_lagging_build(project)
    title = "A rule added today"

    assert kyo("install-procedures", "--adopt", title).code != 0
    assert kyo("install-procedures", "--adopt-preamble").code != 0
    assert _ledger(project).read_text(encoding="utf-8") == newer


def test_print_is_never_refused(kyo, project: Path) -> None:
    _a_lagging_build(project)
    assert kyo("install-procedures", "--print", "--json").code == 0


def test_the_reinstall_the_refusal_names_clears_it(kyo, project: Path) -> None:
    """KYO-318.c1's "each named remedy has a test proving it clears the refusal without
    losing the newer text"."""
    newer = _a_lagging_build(project)
    assert kyo("install-procedures").code != 0

    # What re-packing and reinstalling does to the files involved: the packaged copy
    # becomes this checkout's source.
    _make_source_repo(project, PROCEDURES_ASSET, install_procedures.packaged_procedures_text())

    assert kyo("install-procedures").code == 0
    assert newer != install_procedures.packaged_procedures_text()


def test_the_hand_copy_the_refusal_names_reaches_what_was_asked(kyo, project: Path) -> None:
    """The second remedy, proved against the finding it is meant to clear: a rule the
    ledger is genuinely missing against this repository's OWN source is `kyo doctor`'s
    `procedures_rules_missing`, and copying it across from that file clears it."""
    newer = install_procedures.packaged_procedures_text() + "\n### A rule added today\n\nText.\n"
    _make_source_repo(project, PROCEDURES_ASSET, newer)
    # The ledger still has the build's text: behind its own source by one rule.
    _ledger(project).write_text(install_procedures.packaged_procedures_text(), encoding="utf-8")
    differences, errors = install_procedures.procedures_ledger_drift(project, project)
    assert errors == []
    assert [d.title for d in differences] == ["A rule added today"]

    _ledger(project).write_text(_source(project, PROCEDURES_ASSET).read_text(encoding="utf-8"))

    assert install_procedures.procedures_ledger_drift(project, project) == ([], [])
    assert _ledger(project).read_text(encoding="utf-8") == newer


def test_a_source_repo_in_step_with_its_build_installs_exactly_as_before(
    kyo, project: Path
) -> None:
    _make_source_repo(project, PROCEDURES_ASSET, install_procedures.packaged_procedures_text())
    assert install_procedures.install_procedures_source_refusal(project) is None
    assert kyo("install-procedures").code == 0


def test_a_consumer_project_is_unreachable_from_the_ledger_refusal(
    kyo, project: Path
) -> None:
    assert not _source(project, PROCEDURES_ASSET).exists()
    assert install_procedures.install_procedures_source_refusal(project) is None
    _ledger(project).write_text("# a ledger with no rules at all\n", encoding="utf-8")
    result = kyo("install-procedures", "--json")
    assert result.code == 0, result
    assert result.json["added"], "a consumer still gets the build's rules merged in"


def test_the_refusal_reads_the_code_root_and_not_the_store(
    split_project: tuple[Path, Path],
) -> None:
    """Under the split layout the source of the packaged default is in the CHECKOUT and
    the ledger it would be merged into is in another repository, so a refusal that read
    one root would answer for the wrong one — `procedures_ledger_drift`'s precedent."""
    code, store = split_project
    newer = install_procedures.packaged_procedures_text() + "\n### A rule added today\n\nText.\n"
    _make_source_repo(code, PROCEDURES_ASSET, newer)
    (store / PROCEDURES_ASSET.target).write_text(newer, encoding="utf-8")

    # The store carries no `src/kyosei/defaults/`, so reading it would find nothing.
    assert install_procedures.install_procedures_source_refusal(store) is None
    assert install_procedures.install_procedures_source_refusal(code) is not None
    assert invoke(["--root", str(code), "install-procedures"]).code != 0


# ------------------------------------------- which way round the build is, from git


def _a_repo_with_two_commits(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "checkout"
    root.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=T", "-c", "user.email=t@localhost", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "one")
    first = git("rev-parse", "HEAD")
    (root / "a.txt").write_text("two\n", encoding="utf-8")
    git("commit", "-qam", "two")
    second = git("rev-parse", "HEAD")
    return root, first, second


def _pretend_the_build_is(monkeypatch, commit: str | None) -> None:
    identity = buildinfo.BuildIdentity(
        version="0.1.0",
        commit=commit,
        commit_date="2026-09-19",
        dirty=False,
        source=buildinfo.SOURCE_STAMP,
        kind=buildinfo.INSTALL_KIND_UV_TOOL,
    )
    monkeypatch.setattr(buildinfo, "build_identity", lambda *a, **k: identity)


def test_the_build_is_older_when_its_commit_is_one_this_checkout_already_has(
    tmp_path: Path, monkeypatch
) -> None:
    """KYO-318.c1's "older or newer", answered by ancestry rather than by dates or by
    the files: a commit reachable from HEAD is one this checkout already contains,
    which is exactly what "the build is older" means."""
    root, first, _second = _a_repo_with_two_commits(tmp_path)
    _pretend_the_build_is(monkeypatch, first)
    assert scaffold.build_age_against(root) == scaffold.BUILD_OLDER


def test_the_build_is_newer_when_this_checkout_is_the_ancestor(
    tmp_path: Path, monkeypatch
) -> None:
    root, first, second = _a_repo_with_two_commits(tmp_path)
    subprocess.run(["git", "checkout", "-q", first], cwd=root, check=True)
    _pretend_the_build_is(monkeypatch, second)
    assert scaffold.build_age_against(root) == scaffold.BUILD_NEWER


def test_an_unplaceable_build_says_so_rather_than_guessing(
    tmp_path: Path, monkeypatch
) -> None:
    """The third answer is not a failure. A build with no stamp, a checkout with no git,
    or a commit out of a clone this one has never seen are all real situations, and a
    refusal that asserted a direction it could not establish would be the kind of
    confident wrong sentence this record is about."""
    root, _first, _second = _a_repo_with_two_commits(tmp_path)
    _pretend_the_build_is(monkeypatch, None)
    assert scaffold.build_age_against(root) == scaffold.BUILD_AGE_UNKNOWN

    _pretend_the_build_is(monkeypatch, "0" * 40)
    assert scaffold.build_age_against(root) == scaffold.BUILD_AGE_UNKNOWN

    _pretend_the_build_is(monkeypatch, "0" * 40)
    assert scaffold.build_age_against(tmp_path / "not-a-repo") == scaffold.BUILD_AGE_UNKNOWN


# ------------------------------------------- KYO-318.n1: `kyo version`'s upgrade line


def test_the_per_project_step_is_its_own_sentence(project: Path) -> None:
    """The bundled kind's route ends "...has no separate uv install to move", which ran
    straight into ", then `kyo install-hooks`" and read as one instruction. Two lines
    now, and the route no longer has a clause glued to it."""
    result = invoke(["--root", str(project), "version"])
    assert result.code == 0, result
    lines = [line.strip() for line in result.out.splitlines()]
    route = [line for line in lines if line.startswith("to move to a newer build:")]
    step = [line for line in lines if line.startswith("then, in each project:")]
    assert len(route) == 1 and len(step) == 1, result.out
    assert "install-hooks" not in route[0]
    for kind in buildinfo.INSTALL_KIND_LABELS:
        assert "then" not in buildinfo.upgrade_route(kind)


def test_a_consumer_on_the_copies_is_told_the_three_commands(project: Path) -> None:
    # KYO-595.c4: only a project on the copies runs them; tests/test_harness_migrate.py
    # covers the plugin and neither.
    assert invoke(["--root", str(project), "install-skill", "--copies"]).code == 0
    assert buildinfo.per_project_upgrade_step(project).startswith(
        buildinfo.PER_PROJECT_UPGRADE_COMMANDS
    )


def test_the_source_repository_is_not_told_to_run_the_destructive_two(
    project: Path,
) -> None:
    """KYO-318.n1. `kyo version` is the command a reader consults precisely because they
    are unsure which build they have, so a line there naming `kyo install-skill` in this
    repository is the destructive advice this record is about, printed at the worst
    possible moment."""
    _make_source_repo(project, SKILL_ASSET, "# whatever this checkout's skill says\n")

    step = buildinfo.per_project_upgrade_step(project)
    assert step != buildinfo.PER_PROJECT_UPGRADE_COMMANDS
    assert "SOURCE of what those commands install" in step
    assert "refuse" in step
    assert str(PACKAGED_ASSET_SOURCE_DIR) in step

    result = invoke(["--root", str(project), "version"])
    assert result.code == 0, result
    assert "SOURCE of what those commands install" in result.out
