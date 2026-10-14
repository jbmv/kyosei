"""KYO-514: `kyo onboard` prints the packaged agent setup prompt; `kyo onboard --check`
reads a project back against the same checklist and writes nothing.

The prompt is checked against ``docs/guide/setup-decisions.md``, the one list of setup
decisions it is built from: every decision id there must appear in the prompt, asked or
skipped with a reason. The check is checked against fixture projects under the suite's
temporary ``$KYOSEI_HOME`` (``conftest.store_home``), never the real one (KYO-504).
"""

from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from kyosei import buildinfo, cmd_onboard
from kyosei.cli import COMMAND_TABLE, MUTATING_COMMANDS, build_parser
from kyosei.commands import PROJECT_NONE

from .conftest import git_init, invoke

REPO_ROOT = Path(__file__).resolve().parents[1]
DECISIONS = REPO_ROOT / "docs" / "guide" / "setup-decisions.md"

DECISION_HEADING = re.compile(r"^### ([A-Z]\d+)\. ", re.MULTILINE)
PROMPT_BULLET = re.compile(r"^- \*\*\[([A-Z]\d+)\]", re.MULTILINE)


def prompt() -> str:
    return cmd_onboard.render_prompt()


def decision_ids(text: str) -> list[str]:
    return DECISION_HEADING.findall(text)


def prompt_entries(text: str) -> dict[str, str]:
    """Each ``- **[ID]** ...`` bullet's text, up to the next bullet or heading."""
    entries: dict[str, str] = {}
    matches = list(PROMPT_BULLET.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.start() : end]
        body = re.split(r"^(?:#|Do not ask these|Then apply)", body, flags=re.MULTILINE)[0]
        entries.setdefault(match.group(1), body)
    return entries


def decisions_the_prompt_mishandles(decisions: str, text: str) -> list[str]:
    """Every decision id the prompt omits, or carries with neither a question nor a
    skip that gives its reason; and every id the prompt invents."""
    entries = prompt_entries(text)
    problems = []
    for ident in decision_ids(decisions):
        body = entries.get(ident)
        if body is None:
            problems.append(f"{ident}: not in the prompt")
            continue
        asked = "Ask" in body and "?" in body
        skipped = re.search(r"Do not ask[^:]*:\s*\S", body) is not None
        if not (asked or skipped):
            problems.append(f"{ident}: neither asked nor skipped with a reason")
    for ident in sorted(set(entries) - set(decision_ids(decisions))):
        problems.append(f"{ident}: in the prompt but not a setup decision")
    return problems


@pytest.fixture
def nowhere(tmp_path: Path, monkeypatch) -> Path:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    return empty


# ------------------------------------------------------------------ c1: the prompt


def test_onboard_prints_the_packaged_prompt_stamped_with_this_build(nowhere: Path) -> None:
    result = invoke(["onboard"])
    assert result.code == 0, result
    assert result.out.startswith("# Set up Kyosei in this project")
    assert buildinfo.version_line() in result.out
    assert cmd_onboard.BUILD_PLACEHOLDER not in result.out
    assert cmd_onboard.packaged_prompt_text().count(cmd_onboard.BUILD_PLACEHOLDER) == 1


def test_onboard_needs_no_project_and_writes_nothing(nowhere: Path) -> None:
    assert COMMAND_TABLE["onboard"] == (cmd_onboard.cmd_onboard, PROJECT_NONE)
    assert "onboard" not in MUTATING_COMMANDS
    result = invoke(["onboard", "--json"])
    assert result.code == 0, result
    assert result.json["prompt"] == prompt()
    assert result.json["build"] == cmd_onboard.build_stamp()
    assert list(nowhere.iterdir()) == []


# ------------------------------------------------- c2: every decision, asked or skipped


def test_every_setup_decision_is_asked_or_skipped_with_its_reason() -> None:
    decisions = DECISIONS.read_text(encoding="utf-8")
    assert len(decision_ids(decisions)) >= 25, "the decisions document stopped parsing"
    assert decisions_the_prompt_mishandles(decisions, prompt()) == []


def test_the_decision_check_fails_on_an_omitted_decision() -> None:
    decisions = DECISIONS.read_text(encoding="utf-8")
    text = prompt().replace("- **[D5]", "- **D5")
    assert decisions_the_prompt_mishandles(decisions, text) == ["D5: not in the prompt"]


def test_the_decision_check_fails_on_a_skip_with_no_reason() -> None:
    decisions = DECISIONS.read_text(encoding="utf-8")
    text = re.sub(r"(- \*\*\[D3\][^\n]*\n)(?:  [^\n]*\n)*", "- **[D3] Long commands.**\n", prompt())
    assert decisions_the_prompt_mishandles(decisions, text) == [
        "D3: neither asked nor skipped with a reason"
    ]


# --------------------------------------------- c3: detection, who does what, the readback


def test_the_prompt_says_how_to_tell_the_three_situations_apart() -> None:
    text = prompt()
    for probe in ("`kyo where --json`", "`git rev-parse HEAD`", "`ls -A`"):
        assert probe in text
    for situation in ("**Existing store**", "**Existing repository, no store**", "**Empty folder"):
        assert situation in text
    assert "Run `kyo onboard --check`" in text


def test_the_prompt_splits_what_the_agent_runs_from_what_the_operator_decides() -> None:
    text = prompt()
    assert "Run every command yourself" in text
    # KYO-596: the consent covers the plugin (the default) or the copies.
    consent = "consent to set up Kyosei's Claude Code plugin or to write\n`.claude/settings.json`"
    assert consent in text
    assert "never run either without it" in text
    # Two rounds of questions, not one per turn.
    assert "## Step 3: round one" in text and "## Step 5: round two" in text


def readback_block(text: str) -> str:
    blocks = re.findall(r"^```\n(.*?)^```", text, re.MULTILINE | re.DOTALL)
    assert blocks, "the prompt has no fenced readback block"
    return blocks[-1]


def test_the_prompt_ends_with_the_fixed_readback_block() -> None:
    text = prompt()
    block = readback_block(text)
    assert text.rstrip().endswith("```"), "the readback block is not the prompt's end"
    labels = re.findall(r"^  ([^:\n]+):", block, re.MULTILINE)
    assert labels == [label for label in labels if label in cmd_onboard.READBACK_LABELS]
    assert sorted(labels) == sorted(cmd_onboard.READBACK_LABELS)
    assert "Next: write your first idea as its own file in ideas/" in block


def test_every_kyo_command_the_prompt_names_exists_in_this_build() -> None:
    parser = build_parser()
    sub = next(a for a in parser._actions if getattr(a, "_name_parser_map", None))
    commands = sub._name_parser_map
    named = re.findall(r"`(kyo [^`]+)`", prompt())
    assert len(named) > 15
    for invocation in named:
        words = shlex.split(invocation.replace("<", "").replace(">", "").replace("...", ""))
        if len(words) < 2:
            continue
        assert words[1] in commands, f"the prompt names `{invocation}`, no such command"
        known = {opt for action in commands[words[1]]._actions for opt in action.option_strings}
        for word in words[2:]:
            if word.startswith("--"):
                assert word.split("=")[0] in known, f"`{invocation}`: no flag {word}"


# ------------------------------------------------------- c4: the check, on fixtures


def check(root: Path) -> dict[str, dict]:
    result = invoke(["--root", str(root), "onboard", "--check", "--json"])
    assert result.code == 0, result
    (project,) = result.json["projects"]
    items = {item["label"]: item for item in project["items"]}
    # KYO-596: a project on the plugin reads `harness`; one on the copies reads the three.
    on_plugin = set(cmd_onboard.PROJECT_LABELS) - set(cmd_onboard.COPIES_LABELS)
    on_copies = set(cmd_onboard.PROJECT_LABELS) - set(cmd_onboard.PLUGIN_LABELS)
    assert set(items) in (on_plugin, on_copies)
    return items


def tree_state(*roots: Path) -> dict[str, str]:
    state = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                state[str(path)] = f"{digest}:{path.stat().st_mtime_ns}"
            else:
                state[str(path)] = "dir"
    return state


def set_config(project: Path, line: str) -> None:
    path = project / "kyosei" / "config.yml"
    path.write_text(path.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")


def test_the_check_writes_nothing_in_the_project_or_the_store_home(
    project: Path, store_home: Path
) -> None:
    git_init(project)
    assert invoke(["--root", str(project), "install-skill", "--copies"]).code == 0
    before = tree_state(project, store_home)
    check(project)
    assert invoke(["--root", str(project), "onboard", "--check"]).code == 0
    assert tree_state(project, store_home) == before


def test_every_item_reports_a_known_status(project: Path) -> None:
    for item in check(project).values():
        assert item["status"] in cmd_onboard.STATUSES, item


def test_a_missing_claude_md_section_is_reported_and_install_skill_clears_it(
    project: Path,
) -> None:
    assert invoke(["--root", str(project), "install-skill", "--copies"]).code == 0
    (project / "CLAUDE.md").write_text("# Notes\n\nNothing about the board.\n", encoding="utf-8")
    section = check(project)["CLAUDE.md section"]
    assert section["status"] == "missing"
    assert section["fix"] == "`kyo install-skill`"
    assert invoke(["--root", str(project), "install-skill", "--copies"]).code == 0
    assert check(project)["CLAUDE.md section"]["status"] == "present"


def test_no_skill_means_no_section_and_one_command_fixes_both(project: Path) -> None:
    # KYO-596: with no copies and no plugin, the project reads as on the plugin, missing;
    # `kyo install-skill` puts it on the copies, where the skill and section items apply.
    items = check(project)
    assert items["harness"]["status"] == "missing"
    assert "skill" not in items and "CLAUDE.md section" not in items
    assert invoke(["--root", str(project), "install-skill", "--copies"]).code == 0
    items = check(project)
    assert "harness" not in items
    assert items["skill"]["status"] == "present"
    assert items["CLAUDE.md section"]["status"] == "present"


def test_a_stale_skill_copy_is_behind_and_install_skill_clears_it(project: Path) -> None:
    assert invoke(["--root", str(project), "install-skill", "--copies"]).code == 0
    skill = project / ".claude" / "skills" / "kyosei" / "SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "\nAn older line.\n", encoding="utf-8")
    item = check(project)["skill"]
    assert item["status"] == "behind"
    assert item["fix"] == "`kyo install-skill`"
    assert invoke(["--root", str(project), "install-skill", "--copies"]).code == 0
    assert check(project)["skill"]["status"] == "present"


def test_unset_worker_model_is_missing_and_setting_it_clears_it(project: Path) -> None:
    item = check(project)["worker_model"]
    assert item["status"] == "missing"
    assert "worker_model: <model id>" in item["fix"]
    set_config(project, "worker_model: claude-sonnet-4-5")
    item = check(project)["worker_model"]
    assert item["status"] == "present"
    assert item["detail"] == "claude-sonnet-4-5"


AGENT_TYPE = """---
name: worker
description: a Kyosei worker
model: {model}
---

Read your brief with `kyo show <dispatch-id>`.
"""


def test_an_agent_type_whose_model_disagrees_with_worker_model_is_reported(
    project: Path,
) -> None:
    set_config(project, "worker_model: claude-sonnet-4-5")
    agents = project / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "worker.md").write_text(AGENT_TYPE.format(model="opus"), encoding="utf-8")
    (agents / "unrelated.md").write_text("---\nmodel: haiku\n---\nNot Kyosei.\n", "utf-8")
    item = check(project)["agent types"]
    assert item["status"] == "disagrees"
    assert "worker.md runs opus" in item["detail"]
    assert "unrelated" not in item["detail"]
    assert ".claude/agents/worker.md" in item["fix"]
    (agents / "worker.md").write_text(AGENT_TYPE.format(model="sonnet"), encoding="utf-8")
    assert check(project)["agent types"]["status"] == "present"


def test_an_agent_type_matching_a_kind_override_does_not_disagree(project: Path) -> None:
    set_config(project, "worker_model: claude-sonnet-4-5")
    kinds = project / "kyosei" / "kinds.yml"
    text = kinds.read_text(encoding="utf-8")
    kinds.write_text(
        text.replace(
            "      write_scope: code\n",
            "      write_scope: code\n      worker_model: claude-opus-5\n",
            1,
        ),
        encoding="utf-8",
    )
    agents = project / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "designer.md").write_text(AGENT_TYPE.format(model="opus"), encoding="utf-8")
    items = check(project)
    assert "kind overrides: bug=claude-opus-5" in items["worker_model"]["detail"]
    assert items["agent types"]["status"] == "present"


def test_a_store_with_no_remote_is_reported_and_adding_one_clears_it(
    split_project: tuple[Path, Path], tmp_path: Path
) -> None:
    code, store = split_project
    item = check(code)["store remote"]
    assert item["status"] == "missing"
    assert f"git -C {shlex.quote(str(store))} remote add origin" in item["fix"]
    backup = tmp_path / "backup.git"
    subprocess.run(["git", "init", "-q", "--bare", str(backup)], check=True)
    subprocess.run(["git", "-C", str(store), "remote", "add", "origin", str(backup)], check=True)
    item = check(code)["store remote"]
    assert item["status"] == "present"
    assert str(backup) in item["detail"]


def test_missing_hooks_are_reported_and_install_hooks_clears_them(project: Path) -> None:
    git_init(project)
    # KYO-596: the plugin is the default, and the copies are the alternative its fix names.
    item = check(project)["harness"]
    assert item["status"] == "missing"
    assert item["fix"].startswith("`kyo harness install claude-code`")
    # KYO-595: in a fresh project the copies are written only under --copies.
    assert "`kyo install-hooks --copies`" in item["fix"]
    assert invoke(["--root", str(project), "install-hooks", "--copies"]).code == 0
    assert check(project)["hooks"]["status"] == "present"


def test_a_store_behind_this_build_names_the_rebuild_sequence(project: Path) -> None:
    from kyosei.schema import SCHEMA_VERSION

    path = project / "kyosei" / "config.yml"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(f"schema_version: {SCHEMA_VERSION}", f"schema_version: {SCHEMA_VERSION - 1}"),
        encoding="utf-8",
    )
    item = check(project)["store"]
    assert item["status"] == "behind"
    assert "`kyo rebuild --force`" in item["fix"]


def test_a_directory_with_no_project_is_reported_not_refused(nowhere: Path) -> None:
    result = invoke(["onboard", "--check", "--json"])
    assert result.code == 0, result
    (project,) = result.json["projects"]
    (store,) = project["items"]
    assert store["status"] == "missing"
    assert "`kyo init`" in store["fix"]


# ------------------------------------------------------------- c5: every project


def test_check_all_prints_one_block_per_registered_project(
    tmp_path: Path, store_home: Path
) -> None:
    first, second = tmp_path / "alpha", tmp_path / "beta"
    assert invoke(["init", str(first), "--in-repo", "--prefix", "ALP"]).code == 0
    assert invoke(["init", str(second), "--in-repo", "--prefix", "BET"]).code == 0
    registry = store_home / "projects.json"
    gone = tmp_path / "gone"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace(
            '"projects": [', f'"projects": [{{"root": "{gone}"}}, ', 1
        ),
        encoding="utf-8",
    )
    before = tree_state(first, second, store_home)
    result = invoke(["onboard", "--check", "--all"])
    assert result.code == 0, result
    assert tree_state(first, second, store_home) == before
    headings = [line for line in result.out.splitlines() if line.startswith("project ")]
    assert headings == [
        f"project {gone}",
        f"project {first.resolve()}",
        f"project {second.resolve()}",
    ]
    assert result.out.count("\nmachine\n") == 1
    data = invoke(["onboard", "--all", "--json"]).json
    assert [p["root"] for p in data["projects"]] == [
        str(gone),
        str(first.resolve()),
        str(second.resolve()),
    ]
    assert data["projects"][0]["items"][0]["status"] == "missing"


def test_check_all_with_an_empty_registry_says_so(store_home: Path) -> None:
    result = invoke(["onboard", "--check", "--all"])
    assert result.code == 0, result
    assert f"no projects are registered in {store_home / 'projects.json'}" in result.out


def test_a_rule_on_an_earlier_packaged_wording_is_behind_and_the_rerun_clears_it(
    project: Path,
) -> None:
    # KYO-515: nobody in the project wrote an earlier packaged wording, and a plain
    # `kyo install-procedures` moves it, so --check calls it behind rather than an edit.
    from tests.test_kyo_brief_inherited_wording import (
        PROSE_RULE_HEADING,
        _with_the_prose_rule_reverted,
        _write,
    )

    _write(project, _with_the_prose_rule_reverted)
    item = check(project)["procedures"]
    assert item["status"] == "behind", item
    assert PROSE_RULE_HEADING in item["detail"]
    assert item["fix"] == "`kyo install-procedures`"
    assert invoke(["--root", str(project), "install-procedures"]).code == 0
    assert check(project)["procedures"]["status"] == "present"


def test_a_rule_the_project_reworded_is_still_present_not_behind(project: Path) -> None:
    from tests.test_kyo_brief_inherited_wording import _with_the_prose_rule_reworded, _write

    _write(project, _with_the_prose_rule_reworded)
    item = check(project)["procedures"]
    assert item["status"] == "present", item
    assert "own edits" in item["detail"]


# ------------------------------------------------- KYO-556.c1/c2: D8's document styles


def decision_section(text: str, ident: str) -> str:
    """One ``### <ID>. ...`` section of setup-decisions.md, up to the next heading."""
    match = re.search(rf"^### {ident}\. .*?(?=^##)", text, re.MULTILINE | re.DOTALL)
    assert match, f"no {ident} section"
    return match.group(0)


#: What D8 must carry in both documents: the four styles, the history lint's choice, the
#: docs-written-first question and its id scheme.
D8_TERMS = (
    "SSOT with ids",
    "SSOT without",
    "numbered",
    "no set",
    "history lint",
    "`warn`",
    "`block`",
    "Kyosei-History-OK",
    "one prefix per owning document",
    "<DOC>-<n>",
    "id_pattern",
)


def test_d8_names_the_four_styles_obligations_the_lint_and_the_docs_first_path() -> None:
    d8 = decision_section(DECISIONS.read_text(encoding="utf-8"), "D8")
    for term in (*D8_TERMS, "raises_docs_obligations", "plans, ADRs, dated snapshots"):
        assert term in d8, term
    assert "**Documents written before the code.**" in d8
    assert "declare the `ssot:` set" in d8


def test_the_prompt_asks_d8s_questions() -> None:
    body = prompt_entries(prompt())["D8"]
    for term in D8_TERMS:
        assert term in body, term
    # The docs-written-first path is its own question, asked before the first spec.
    assert body.count("?") >= 3, body
    assert "Will requirements get ids?" in body
    assert "before the first spec exists" in body


# ----------------------------------------------------------------------------- KYO-564


def _break_one_check(project: Path) -> None:
    """Make `kyo doctor` fail on a problem-tier check: an export file the db does not
    have, which `exports` reports."""
    (project / "kyosei" / "export" / "TST-999.json").write_text("{}\n", encoding="utf-8")


def test_doctor_readback_is_not_run_until_doctor_records_a_result(project: Path) -> None:
    from kyosei import doctor

    assert not doctor.last_result_path(project).exists()
    item = check(project)["kyo doctor"]
    assert item["status"] == "not-run"
    assert item["fix"] == "`kyo doctor`"


def test_doctor_readback_changes_after_a_clean_doctor_run(project: Path) -> None:
    """KYO-564.c4: the readback reads the result `kyo doctor` just recorded -- clean, its
    age and this build -- and the remedy the not-run line names clears it."""
    assert check(project)["kyo doctor"]["status"] == "not-run"
    assert invoke(["--root", str(project), "doctor"]).code == 0
    item = check(project)["kyo doctor"]
    assert item["status"] == "present", item
    assert item["detail"].startswith("clean -- `kyo doctor` ")
    assert "ago" in item["detail"] or "just now" in item["detail"]
    ours = buildinfo.build_identity(with_dirty=False).short_commit
    assert f"this build ({ours})" in item["detail"]


def test_doctor_readback_names_the_failing_checks_and_clears_when_fixed(
    project: Path,
) -> None:
    from kyosei import doctor

    _break_one_check(project)
    assert invoke(["--root", str(project), "doctor"]).code != 0
    recorded = doctor.read_last_result(project)
    assert recorded is not None and recorded["failing"], recorded
    item = check(project)["kyo doctor"]
    assert item["status"] == "failing", item
    for name in recorded["failing"]:
        assert name in item["detail"]
    assert item["fix"].startswith("`kyo doctor`")
    (project / "kyosei" / "export" / "TST-999.json").unlink()
    assert invoke(["--root", str(project), "doctor"]).code == 0
    assert check(project)["kyo doctor"]["status"] == "present"


def test_doctor_hook_records_its_result_too(project: Path) -> None:
    from kyosei import doctor

    assert invoke(["--root", str(project), "doctor", "--hook"]).code == 0
    recorded = doctor.read_last_result(project)
    assert recorded is not None and recorded["hook"] is True and recorded["clean"]
    assert "`kyo doctor --hook`" in check(project)["kyo doctor"]["detail"]


def test_doctor_result_is_recorded_outside_the_export_and_ignored_by_git(
    project: Path,
) -> None:
    """KYO-564.c1: machine-local state, never under the git-canonical export, and never
    something a commit of the store would carry."""
    from kyosei import doctor

    git_init(project)
    assert invoke(["--root", str(project), "doctor"]).code == 0
    path = doctor.last_result_path(project)
    assert path.is_file()
    assert (project / "kyosei" / "export") not in path.parents
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", str(path)], cwd=project, check=False
    )
    assert ignored.returncode == 0


def test_an_unreadable_doctor_result_is_not_reported_as_not_run(project: Path) -> None:
    from kyosei import doctor

    path = doctor.last_result_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    item = check(project)["kyo doctor"]
    assert item["status"] == "unreadable", item
    assert invoke(["--root", str(project), "doctor"]).code == 0
    assert check(project)["kyo doctor"]["status"] == "present"
