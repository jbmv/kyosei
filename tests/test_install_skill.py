"""``kyo install-skill``: the workflow travels with the enforcement.

KYO-15: the `/kyosei` skill lived only in this repo, so `kyo init` handed a consumer
the *enforcement* — hooks, transition contracts, the merge guard — with none of the
*instructions* for driving it, leaving a project with rules and no workflow.

A hand-copied skill falls behind the packaged one as soon as the skill changes. That
is the third instance of one shape in this codebase: Kyosei ships code that updates
itself and *copies* that never do. The skill is the worst of the three to let drift,
because it documents the CLI by name — a stale copy tells a controller to run commands
and flags that may not exist in the build it is talking to.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from kyosei import install_skill, scaffold
from kyosei.install_skill import (
    SKILL_ASSET,
    SKILL_ASSETS,
    SKILL_REFERENCE_ASSET,
    installed_skill_text,
    packaged_skill_reference_text,
    packaged_skill_text,
    split_skill_digest,
    stamp_skill_text,
)


def _installed_workflow() -> str:
    """What `kyo install-skill` writes for the workflow: the packaged text with the
    skill directory spelled out (KYO-593.c3)."""
    return installed_skill_text(packaged_skill_text())


def _skill(project: Path) -> Path:
    return project / SKILL_ASSET.target


def _reference(project: Path) -> Path:
    """The skill's second tier (KYO-328) — the histories the workflow cites."""
    return project / SKILL_REFERENCE_ASSET.target


def _source(project: Path, asset=SKILL_ASSET) -> Path:
    """Where this project keeps its own source for a packaged skill file, if it is
    the repository the package is built from (KYO-309's file test)."""
    return project / asset.source_path


def _make_source_repo(project: Path, text: str, asset=SKILL_ASSET) -> Path:
    path = _source(project, asset)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_install_skill_writes_the_packaged_workflow(kyo, project: Path) -> None:
    assert not _skill(project).exists()
    result = kyo("install-skill", "--copies")
    assert result.code == 0, result
    assert _skill(project).read_text(encoding="utf-8") == stamp_skill_text(_installed_workflow())


def test_install_skill_is_idempotent(kyo, project: Path) -> None:
    assert kyo("install-skill", "--copies").code == 0
    again = kyo("install-skill", "--copies", "--json")
    assert again.code == 0
    assert again.json["changed"] is False


def test_print_mode_writes_nothing(kyo, project: Path) -> None:
    result = kyo("install-skill", "--copies", "--print", "--json")
    assert result.code == 0
    assert result.json["printed_only"] is True
    assert not _skill(project).exists()


def test_install_hooks_installs_the_skill_too(kyo, project: Path) -> None:
    """KYO-15.c1's "or as part of install-hooks": a consumer that installs the
    enforcement gets the workflow in the same act, rather than having to know
    there is a second command."""
    assert kyo("install-hooks", "--copies").code == 0
    assert _skill(project).is_file()


def test_doctor_reports_a_stale_skill(kyo, project: Path) -> None:
    """A hand-copied skill can fall behind the packaged one with nothing saying so."""
    kyo("install-skill", "--copies")
    _skill(project).write_text("# an old copy\n\nRun `kyo frobnicate`.\n", encoding="utf-8")

    result = kyo("doctor")
    assert result.code != 0
    assert "skill" in result.err
    assert "kyo install-skill" in result.err


def test_the_remedy_doctor_names_clears_it(kyo, project: Path) -> None:
    kyo("install-skill", "--copies")
    _skill(project).write_text("# an old copy\n", encoding="utf-8")
    assert kyo("doctor").code != 0

    assert kyo("install-skill", "--copies").code == 0
    assert kyo("doctor").code == 0


def test_a_project_without_the_skill_is_not_reported(kyo, project: Path) -> None:
    """Driving `kyo` by hand is legitimate; a project that never installed the
    skill is not drifting."""
    assert not _skill(project).exists()
    assert not _reference(project).exists()
    assert kyo("doctor").code == 0


# ----------------------------------------------------------------------------- KYO-328
#
# The skill became two files: `SKILL.md`, the workflow a controller follows, and
# `REFERENCE.md` beside it, holding the incident behind each instruction. The workflow
# cites the reference by name at the top and at the end of every section, so a project
# that has one and not the other reads as a document full of dead pointers — which is
# the KYO-149 failure (a citation the reader cannot open) arriving by a new road.
#
# So the two are installed together, checked together, and refused together.


def test_install_skill_writes_both_tiers(kyo, project: Path) -> None:
    """KYO-328.c1: one command, two files, and the reference is the packaged one."""
    assert kyo("install-skill", "--copies").code == 0
    assert _skill(project).read_text(encoding="utf-8") == stamp_skill_text(_installed_workflow())
    assert _reference(project).read_text(encoding="utf-8") == stamp_skill_text(
        packaged_skill_reference_text()
    )


def test_install_hooks_writes_both_tiers_too(kyo, project: Path) -> None:
    """`kyo install-hooks` installs the skill (KYO-15), so it installs both halves —
    otherwise the consumer who never learns there is a second command gets the
    workflow with its citations pointing at nothing."""
    assert kyo("install-hooks", "--copies").code == 0
    assert _skill(project).is_file() and _reference(project).is_file()


def test_the_install_names_every_file_it_wrote(kyo, project: Path) -> None:
    """A run that wrote two files and reported one is how the second goes unnoticed.
    Both paths are printed, and `--json` carries them as a list."""
    result = kyo("install-skill", "--copies", "--json")
    assert result.code == 0, result
    assert set(result.json["paths"]) == {
        str(_skill(project)),
        str(_reference(project)),
        str(project / "CLAUDE.md"),
    }
    assert str(SKILL_REFERENCE_ASSET.target) in result.out


def test_install_skill_is_idempotent_across_both_tiers(kyo, project: Path) -> None:
    kyo("install-skill", "--copies")
    again = kyo("install-skill", "--copies", "--json")
    assert again.code == 0
    assert again.json["changed"] is False


def test_doctor_reports_a_stale_reference_and_install_skill_clears_it(
    kyo, project: Path
) -> None:
    """The drift check covers the second tier on the first's terms: a reference that
    fell behind the build documents incidents against commands that may have moved,
    and the remedy named is proved to clear it."""
    kyo("install-skill", "--copies")
    _reference(project).write_text("# an old copy\n", encoding="utf-8")

    result = kyo("doctor")
    assert result.code != 0
    assert SKILL_REFERENCE_ASSET.target in result.err
    assert "kyo install-skill" in result.err

    assert kyo("install-skill", "--copies").code == 0
    assert kyo("doctor").code == 0


def test_doctor_reports_a_missing_reference_beside_an_installed_workflow(
    kyo, project: Path
) -> None:
    """Absent is not drift for a project that installed no skill at all — and IS
    drift when the other half is there, because `kyo install-skill` writes both, so
    one alone is a half-applied install or a hand-deleted file. The workflow's
    citations of it are what the finding is about."""
    kyo("install-skill", "--copies")
    _reference(project).unlink()

    result = kyo("doctor")
    assert result.code != 0
    assert SKILL_REFERENCE_ASSET.target in result.err
    assert "kyo install-skill" in result.err

    assert kyo("install-skill", "--copies").code == 0
    assert kyo("doctor").code == 0


def test_the_workflow_cites_the_reference_that_ships_with_it(kyo, project: Path) -> None:
    """The point of shipping the second file at all: what the installed workflow
    names is a path the installed project actually has."""
    kyo("install-skill", "--copies")
    workflow = _skill(project).read_text(encoding="utf-8")
    assert SKILL_REFERENCE_ASSET.target in workflow
    assert (project / SKILL_REFERENCE_ASSET.target).is_file()


def test_install_skill_refuses_when_only_the_references_source_is_newer(
    kyo, project: Path
) -> None:
    """KYO-318's refusal, extended to the file KYO-328 added. Writing only the half
    that happened to agree would leave the two tiers of one document at different
    ages, which is the half-done state a refusal exists to prevent (law 6)."""
    kyo("install-skill", "--copies")
    newer = packaged_skill_reference_text() + "\n<!-- edited today -->\n"
    _make_source_repo(project, packaged_skill_text())
    _make_source_repo(project, newer, SKILL_REFERENCE_ASSET)
    _reference(project).write_text(newer, encoding="utf-8")

    result = kyo("install-skill", "--copies")
    assert result.code != 0, result
    assert str(_source(project, SKILL_REFERENCE_ASSET).relative_to(project)) in result.err
    # ...and nothing was written: the newer text is the whole thing being protected.
    assert _reference(project).read_text(encoding="utf-8") == newer


def test_the_hand_copy_that_refusal_names_clears_it_for_the_reference(
    kyo, project: Path
) -> None:
    """Each named remedy has a test proving it reaches what was asked without losing
    the newer text — the bar KYO-318 set, applied to the second file."""
    kyo("install-skill", "--copies")
    newer = packaged_skill_reference_text() + "\n<!-- the newer source -->\n"
    _make_source_repo(project, newer, SKILL_REFERENCE_ASSET)
    assert install_skill.skill_drift(project) != []

    _reference(project).write_text(
        _source(project, SKILL_REFERENCE_ASSET).read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert install_skill.skill_drift(project) == []
    assert _reference(project).read_text(encoding="utf-8") == newer


def test_a_consumer_never_meets_the_reference_refusal_either(kyo, project: Path) -> None:
    """Structurally, not by luck: a consumer carries no source for either file."""
    assert not _source(project).exists()
    assert not _source(project, SKILL_REFERENCE_ASSET).exists()
    kyo("install-skill", "--copies")
    _reference(project).write_text("# an old copy\n", encoding="utf-8")
    assert kyo("install-skill", "--copies").code == 0
    assert _reference(project).read_text(encoding="utf-8") == stamp_skill_text(
        packaged_skill_reference_text()
    )


def test_every_skill_asset_is_packaged_and_lands_under_the_skill_directory() -> None:
    """The canary on the tuple itself: a third tier added to SKILL_ASSETS without a
    packaged file, or written somewhere a consumer's skill directory is not, would
    pass every check above by being silently absent."""
    assert SKILL_ASSETS == (SKILL_ASSET, SKILL_REFERENCE_ASSET)
    for asset in SKILL_ASSETS:
        assert install_skill.packaged_skill_asset_text(asset).strip()
        assert asset.target.startswith(".claude/skills/kyosei/")


# ----------------------------------------------------------------------------- KYO-318
#
# KYO-309 turned the READ side round in the repository this package is built from: the
# checks there compare against `src/kyosei/defaults/` and never name a remedy that
# destroys the newer text. It left the write side alone, and the write side is a command
# a person can still type. These are the tests for that half: run by hand here from an
# older installed build, `kyo install-skill` wrote that build's older skill over the
# newer one and reported success.


def _a_lagging_build(kyo, project: Path) -> str:
    """The measured shape: this repository's skill and its packaged source are edited
    together, and the installed `kyo` still ships the previous text. Returns the newer
    source text, which is what the refusal exists to protect; the project's copy holds
    it with the skill directory spelled out (KYO-593.c3)."""
    kyo("install-skill", "--copies")
    newer = packaged_skill_text() + "\n<!-- edited today -->\n"
    _skill(project).write_text(installed_skill_text(newer), encoding="utf-8")
    _make_source_repo(project, newer)
    return newer


def test_install_skill_refuses_to_overwrite_this_repositorys_own_source(
    kyo, project: Path
) -> None:
    """KYO-318.c1. The bug as filed: the newer skill is gone and the command says it
    wrote it."""
    newer = _a_lagging_build(kyo, project)

    result = kyo("install-skill", "--copies")
    assert result.code != 0, result
    # ...and nothing was written. This is the whole criterion: refuse BEFORE writing.
    assert _skill(project).read_text(encoding="utf-8") == installed_skill_text(newer)
    assert _source(project).read_text(encoding="utf-8") == newer


def test_the_refusal_says_which_checkout_is_the_source_and_how_the_build_stands(
    kyo, project: Path
) -> None:
    """KYO-318.c1's "say that this checkout is the source and the running build is
    older or newer". The direction is asked of git rather than inferred from the two
    texts, so a checkout that cannot place the build's commit says that instead of
    guessing — all three answers are true sentences."""
    _a_lagging_build(kyo, project)
    err = kyo("install-skill", "--copies").err

    assert str(_source(project).relative_to(project)) in err
    assert "this repository's own source" in err
    assert any(
        phrase in err
        for phrase in (scaffold.BUILD_OLDER, scaffold.BUILD_NEWER, scaffold.BUILD_AGE_UNKNOWN)
    ), err
    assert "Nothing was written" in err


def test_the_reinstall_the_refusal_names_clears_it_without_losing_the_newer_text(
    kyo, project: Path
) -> None:
    """KYO-318.c1's "each named remedy has a test proving it clears the refusal without
    losing the newer text". A reinstall is a build rather than a command, so what it
    does is modelled exactly as KYO-309 modelled it: the packaged copy becomes this
    checkout's source."""
    newer = _a_lagging_build(kyo, project)
    assert kyo("install-skill", "--copies").code != 0

    # What re-packing and reinstalling does, and the only thing it does here.
    _make_source_repo(project, packaged_skill_text())
    _skill(project).write_text(_installed_workflow(), encoding="utf-8")

    assert kyo("install-skill", "--copies").code == 0
    # The text the refusal protected is not lost BY the remedy — it is carried into the
    # build, which is what makes the two copies agree again.
    assert newer != packaged_skill_text()


def test_the_hand_copy_the_refusal_names_reaches_what_was_asked_and_keeps_the_text(
    kyo, project: Path
) -> None:
    """KYO-318.c1's second remedy. What somebody running `kyo install-skill` in this
    repository actually wants is the project's skill brought up to the current source;
    the `cp` the refusal prints is that same write with the older build taken out of the
    middle, and `kyo doctor`'s `skill` check — the read side — is what it clears.
    KYO-593.c3: the line is a `sed` that spells the skill directory out, and the test
    runs the line the refusal prints rather than a copy of its own."""
    kyo("install-skill", "--copies")
    newer = packaged_skill_text() + "\n<!-- the newer source -->\n"
    _make_source_repo(project, newer)
    # The project's own copy is behind its source: `skill_drift`'s real finding here.
    assert install_skill.skill_drift(project) != []

    refusal = install_skill.install_skill_refusal(project)
    assert refusal is not None
    (line,) = [ln.strip() for ln in refusal.splitlines() if ln.strip().startswith("sed ")]
    subprocess.run(["/bin/sh", "-c", line], cwd=project, check=True)

    assert install_skill.skill_drift(project) == []
    assert _skill(project).read_text(encoding="utf-8") == installed_skill_text(newer)


def test_print_is_never_refused(kyo, project: Path) -> None:
    """On `install_procedures_refusal`'s precedent: a preview that touches nothing is
    exactly what lets a reader see the situation rather than only be refused it."""
    _a_lagging_build(kyo, project)
    result = kyo("install-skill", "--copies", "--print", "--json")
    assert result.code == 0, result


def test_a_source_repo_in_step_with_its_build_installs_exactly_as_before(
    kyo, project: Path
) -> None:
    """Both halves have to be true or there is nothing to protect. Identical copies is
    also every run from a build made out of this checkout, an editable install
    included — which is how this repository's own suite runs."""
    kyo("install-skill", "--copies")
    _make_source_repo(project, packaged_skill_text())
    _skill(project).write_text("# stale\n", encoding="utf-8")

    assert kyo("install-skill", "--copies").code == 0
    # KYO-604: no digest line here -- this repository's copy is a mirror of its source.
    assert _skill(project).read_text(encoding="utf-8") == _installed_workflow()


def test_a_consumer_project_is_unreachable_from_any_of_this(kyo, project: Path) -> None:
    """KYO-318.c1's last clause, stated structurally: a consumer carries no source
    here, so the refusal cannot fire for them whatever their skill says."""
    assert not _source(project).exists()
    assert install_skill.install_skill_refusal(project) is None
    kyo("install-skill", "--copies")
    _skill(project).write_text("# an old copy\n", encoding="utf-8")
    assert kyo("install-skill", "--copies").code == 0
    assert _skill(project).read_text(encoding="utf-8") == stamp_skill_text(_installed_workflow())


def test_install_hooks_refuses_before_it_writes_anything(kyo, project: Path) -> None:
    """`kyo install-hooks` installs the skill too (KYO-15), so it inherits this
    refusal — and has to raise it BEFORE merging the hooks, or a repository whose skill
    alone is behind gets a written settings.json and then an error (law 6)."""
    kyo("install-hooks", "--copies")
    settings = project / ".claude" / "settings.json"
    before = settings.read_text(encoding="utf-8")
    _a_lagging_build(kyo, project)
    settings.unlink()

    result = kyo("install-hooks", "--copies")
    assert result.code != 0, result
    assert not settings.exists(), "the hooks were merged before the refusal was raised"
    assert before  # the fixture above really did write one the first time


def test_init_gitignores_obsidian_ui_state(kyo, project: Path) -> None:
    """KYO-14.c3. Opening `ideas/` as an Obsidian vault is the intended way to use
    the ideas layer, and Obsidian writes pane positions into it immediately."""
    gitignore = project / "ideas" / ".gitignore"
    assert gitignore.is_file(), "kyo init did not write ideas/.gitignore"
    body = gitignore.read_text(encoding="utf-8")
    assert ".obsidian/workspace.json" in body
    assert ".obsidian/graph.json" in body


def test_the_vaults_shared_config_is_not_ignored(kyo, project: Path) -> None:
    """Deliberate: app.json/appearance.json/core-plugins.json are project content,
    and ignoring the whole `.obsidian/` directory would drop them."""
    body = (project / "ideas" / ".gitignore").read_text(encoding="utf-8")
    # Only the non-comment lines are patterns; the shared filenames appear in the
    # comment precisely because it explains that they are kept.
    patterns = [
        line.strip()
        for line in body.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert ".obsidian/" not in patterns, "ignoring the whole vault would drop shared config"
    for shared in ("app.json", "appearance.json", "core-plugins.json"):
        assert not any(shared in pattern for pattern in patterns)


# ----------------------------------------------------------------------------- KYO-361
#
# `install-skill`'s "already current" line is true on its own terms, but a consumer
# who just ran `kyo install-hooks` (which writes the same files) and then ran
# `kyo install-skill` read the truthful no-changes line as contradicting a write that
# had, in fact, just happened -- because nothing in the line said the two commands
# write the same file.


def test_install_skill_no_changes_line_names_install_hooks(kyo, project: Path) -> None:
    """KYO-361.c3: the no-changes line, for each file already current, says that
    `kyo install-hooks` also writes it -- so a reader who ran both in sequence can
    connect this truthful line to the write install-hooks just reported."""
    assert kyo("install-hooks", "--copies").code == 0

    result = kyo("install-skill", "--copies")
    assert result.code == 0, result
    lines = result.out.splitlines()
    assert len(lines) == 3, lines
    for line in lines:
        assert "is already current (no changes" in line
        assert "kyo install-hooks" in line


# KYO-508: the CLAUDE.md section that makes the skill load.


def test_install_skill_creates_claude_md_and_rerun_changes_nothing(kyo, project: Path) -> None:
    md = project / "CLAUDE.md"
    md.unlink(missing_ok=True)
    assert kyo("install-skill", "--copies").code == 0
    text = md.read_text(encoding="utf-8")
    assert install_skill.CLAUDE_MD_SECTION in text and "uses Kyosei" in text
    again = kyo("install-skill", "--copies", "--json")
    assert again.code == 0
    assert md.read_text(encoding="utf-8") == text
    assert again.json["changed"] is False


def test_rerun_replaces_only_between_markers(kyo, project: Path) -> None:
    md = project / "CLAUDE.md"
    before, after = "# Mine\n\nhand text\n\n", "\n\ntrailing notes\n"
    stale = f"{install_skill.CLAUDE_MD_BEGIN}\nold wording\n{install_skill.CLAUDE_MD_END}"
    md.write_text(before + stale + after, encoding="utf-8")
    assert kyo("install-skill", "--copies").code == 0
    assert md.read_text(encoding="utf-8") == before + install_skill.CLAUDE_MD_SECTION + after


def test_install_skill_appends_without_touching_existing_claude_md(kyo, project: Path) -> None:
    md = project / "CLAUDE.md"
    md.write_text("# Mine\nkeep me", encoding="utf-8")
    assert kyo("install-skill", "--copies").code == 0
    assert md.read_text(encoding="utf-8").startswith("# Mine\nkeep me\n\n")


def test_print_shows_claude_md_change_without_writing(kyo, project: Path) -> None:
    md = project / "CLAUDE.md"
    md.unlink(missing_ok=True)
    result = kyo("install-skill", "--copies", "--print")
    assert result.code == 0
    assert "would write" in result.out and "CLAUDE.md" in result.out
    assert not md.exists()


def test_doctor_reports_missing_or_old_section_and_install_skill_clears_it(
    kyo, project: Path
) -> None:
    assert kyo("install-skill", "--copies").code == 0
    md = project / "CLAUDE.md"
    for broken in (None, f"{install_skill.CLAUDE_MD_BEGIN}\nold\n{install_skill.CLAUDE_MD_END}\n"):
        if broken is None:
            md.unlink()
        else:
            md.write_text(broken, encoding="utf-8")
        found = kyo("doctor")
        assert "Kyosei section" in found.err and "kyo install-skill" in found.err
        assert kyo("install-skill", "--copies").code == 0
        assert "Kyosei section" not in kyo("doctor").err


# ------------------ KYO-604: an earlier build's unedited skill is a notice, not a failure
#
# A release that changes the skill's text used to fail `kyo doctor` -- and with it the
# Stop hook it ends -- in every project that had not rerun install-skill. Nothing recorded
# what an earlier build wrote, so `kyo install-skill` now ends each file with the digest
# of the text it wrote, and a copy whose text still matches that digest is an earlier
# build's skill, unedited.

EARLIER_SKILL_TEXT = (
    "---\nname: kyosei\ndescription: an earlier build's skill\n---\n\nRun `kyo old`.\n"
)


def _an_earlier_builds_install(project: Path) -> None:
    """What an earlier build's `kyo install-skill` (one that recorded digests) wrote: its
    own packaged text, with the digest of that text on the last line."""
    for asset in SKILL_ASSETS:
        path = project / asset.target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stamp_skill_text(EARLIER_SKILL_TEXT + asset.source), encoding="utf-8")


def test_install_skill_records_a_digest_of_what_it_wrote(kyo, project: Path) -> None:
    """KYO-604.c4, first half: the digest is of the packaged text exactly, and it is on
    the file itself, so no new file needs committing."""
    import hashlib

    assert kyo("install-skill", "--copies").code == 0
    for asset, packaged in (
        # KYO-593: the copy is the installed form (the skill's own directory named).
        (SKILL_ASSET, _installed_workflow()),
        (SKILL_REFERENCE_ASSET, packaged_skill_reference_text()),
    ):
        body, digest = split_skill_digest((project / asset.target).read_text(encoding="utf-8"))
        assert body == packaged
        assert digest == hashlib.sha256(packaged.encode("utf-8")).hexdigest()
    assert sorted(p.name for p in _skill(project).parent.iterdir()) == ["REFERENCE.md", "SKILL.md"]


def test_an_earlier_builds_unedited_skill_is_a_notice_and_the_stop_hook_exits_0(
    kyo, project: Path
) -> None:
    """KYO-604.c4: a copy an earlier skill text wrote, untouched since. A notice naming
    `kyo install-skill`; `kyo doctor --hook` exits 0; the remedy clears it."""
    kyo("install-skill", "--copies")  # the CLAUDE.md section, so only the skill files differ
    _an_earlier_builds_install(project)

    result = kyo("doctor", "--hook", "--json")
    assert result.code == 0, result
    checks = result.json["checks"]
    assert checks["skill"]["ok"], checks["skill"]
    notice = checks["skill_superseded"]
    assert notice["tier"] == "notice"
    assert len(notice["failures"]) == len(SKILL_ASSETS)
    assert all("kyo install-skill" in line for line in notice["failures"])

    assert kyo("install-skill", "--copies").code == 0
    after = kyo("doctor", "--hook", "--json")
    assert after.code == 0
    assert after.json["checks"]["skill_superseded"]["ok"]


def test_an_edited_skill_copy_is_still_a_problem(kyo, project: Path) -> None:
    """KYO-604.c5: edited since install-skill wrote it -- the digest no longer matches,
    so this is not an earlier build's text and `kyo doctor --hook` fails."""
    kyo("install-skill", "--copies")
    _an_earlier_builds_install(project)
    text = _skill(project).read_text(encoding="utf-8")
    _skill(project).write_text(text.replace("Run `kyo old`.", "Run `kyo mine`."), encoding="utf-8")

    result = kyo("doctor", "--hook", "--json")
    assert result.code == 3, result
    failures = json.dumps(result.json["checks"]["skill"]["failures"])
    assert "edited since `kyo install-skill` wrote it" in failures
    assert "kyo install-skill" in failures
    # The untouched reference is still only a notice.
    assert len(result.json["checks"]["skill_superseded"]["failures"]) == 1


def test_a_skill_copy_with_no_digest_is_still_a_problem_until_one_install_skill(
    kyo, project: Path
) -> None:
    """KYO-604.c5: every copy written before KYO-604 carries no digest, so nothing says
    it is unedited; it stays a problem, and the one `kyo install-skill` the finding names
    makes the next text change a notice."""
    kyo("install-skill", "--copies")
    _skill(project).write_text(EARLIER_SKILL_TEXT, encoding="utf-8")

    result = kyo("doctor", "--hook", "--json")
    assert result.code == 3, result
    failures = json.dumps(result.json["checks"]["skill"]["failures"])
    assert "carries no digest" in failures
    assert "kyo install-skill" in failures

    assert kyo("install-skill", "--copies").code == 0
    assert kyo("doctor", "--hook").code == 0
