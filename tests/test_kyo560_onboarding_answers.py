"""KYO-560: declined and deferred setup decisions are recorded under ``onboarding:`` in
``kyosei/config.yml``, and `kyo onboard --check` reports them as such rather than as
missing. KYO-563: the setup keys and rule forms onboarding names are shown in the files
it ships (the config.yml template, kinds.yml's schema comment, procedures.md).

Both come from a consumer project's report on its onboarding.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from kyosei import cmd_onboard
from kyosei.config import (
    CONFIG_KEY_EXAMPLES,
    OnboardingAnswer,
    load_config,
    parse_onboarding,
)
from kyosei.errors import KyoseiError
from kyosei.procedures import (
    PROJECT_RULES_SECTION,
    audiences_of,
    parse_procedures,
    preamble,
    rule_bodies,
    rule_sections,
)
from kyosei.scaffold import defaults_dir
from kyosei.schema import CONFIG_CONSUMERS, OPTIONAL_CONFIG_KEYS

from .conftest import git_init, invoke

PACKAGED = defaults_dir()
ANSWERS = "onboarding:\n  B2: declined\n  D4:\n    status: deferred\n    reason: no code yet\n"


def config_path(project: Path) -> Path:
    return project / "kyosei" / "config.yml"


def append_config(project: Path, text: str) -> None:
    path = config_path(project)
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def check(root: Path) -> dict[str, dict]:
    result = invoke(["--root", str(root), "onboard", "--check", "--json"])
    assert result.code == 0, result
    (project,) = result.json["projects"]
    return {item["label"]: item for item in project["items"]}


def check_text(root: Path) -> list[str]:
    result = invoke(["--root", str(root), "onboard", "--check"])
    assert result.code == 0, result
    return result.out.splitlines()


def uncommented(example: str) -> str:
    """A CONFIG_KEY_EXAMPLES block's YAML, as a person uncommenting it would leave it."""
    lines = example.splitlines()
    start = max(i for i, line in enumerate(lines) if line.strip() == "#") + 1
    return "\n".join(line[2:] for line in lines[start:]) + "\n"


# ------------------------------------------------------------- KYO-560.c1, c6, c2


def test_c1_the_key_is_registered_optional_and_loads(project: Path) -> None:
    assert "onboarding" in CONFIG_CONSUMERS and "onboarding" in OPTIONAL_CONFIG_KEYS
    append_config(project, ANSWERS)
    load_config(project)  # no "keys no code path reads" refusal
    assert parse_onboarding(yaml.safe_load(ANSWERS)["onboarding"], Path("c")) == {
        "B2": OnboardingAnswer("B2", "declined", ""),
        "D4": OnboardingAnswer("D4", "deferred", "no code yet"),
    }


def test_c6_b2_declined_and_d4_deferred_are_reported_without_a_fix(project: Path) -> None:
    git_init(project)  # a repository with no remote: B2's item is about the remote alone
    before = check(project)
    assert before["store remote"]["status"] == "missing" and before["store remote"]["fix"]
    assert before["worktree_setup"]["status"] == "missing" and before["worktree_setup"]["fix"]

    append_config(project, ANSWERS)
    items = check(project)
    remote, setup = items["store remote"], items["worktree_setup"]
    assert remote["status"] == "declined" and remote["fix"] is None
    assert "B2" in remote["detail"] and "no reason recorded" in remote["detail"]
    assert setup["status"] == "deferred" and setup["fix"] is None
    assert "D4" in setup["detail"] and "no code yet" in setup["detail"]
    # An unanswered decision is still missing, with its fix.
    assert items["orchestrator_only_commands"]["status"] == "missing"
    assert items["orchestrator_only_commands"]["fix"]
    for status in ("declined", "deferred"):
        assert status in cmd_onboard.STATUSES


def test_c2_the_text_readback_prints_no_fix_line_under_an_answered_item(
    project: Path,
) -> None:
    git_init(project)
    append_config(project, ANSWERS)
    lines = check_text(project)
    for label, status in (("store remote", "declined"), ("worktree_setup", "deferred")):
        (index,) = [i for i, line in enumerate(lines) if line.startswith(f"  {label}: ")]
        assert lines[index].startswith(f"  {label}: {status} -- "), lines[index]
        assert not lines[index + 1].lstrip().startswith("fix:"), lines[index + 1]


def test_kyo_init_does_not_list_an_answered_decision_as_setup_still_to_do(
    project: Path,
) -> None:
    git_init(project)
    append_config(project, ANSWERS)
    from kyosei.config import resolve_layout

    _, items = cmd_onboard.setup_incomplete(resolve_layout(explicit_root=project))
    labels = [item.label for item in items]
    assert "store remote" not in labels and "worktree_setup" not in labels
    assert "orchestrator_only_commands" in labels


def test_an_answer_a_setting_contradicts_disagrees_and_names_both_ways_out(
    split_project: tuple[Path, Path], tmp_path: Path
) -> None:
    code, store = split_project
    append_config(store, "onboarding:\n  B2: declined\n")
    assert check(code)["store remote"]["status"] == "declined"
    backup = tmp_path / "backup.git"
    subprocess.run(["git", "init", "-q", "--bare", str(backup)], check=True)
    subprocess.run(["git", "-C", str(store), "remote", "add", "origin", str(backup)], check=True)
    item = check(code)["store remote"]
    assert item["status"] == "disagrees"
    assert str(backup) in item["detail"] and "B2" in item["detail"]
    assert "delete B2 from onboarding:" in item["fix"]


def test_no_git_repository_is_not_answered_by_a_declined_remote(project: Path) -> None:
    append_config(project, "onboarding:\n  B2: declined\n")
    item = check(project)["store remote"]
    assert item["status"] == "missing"
    assert "git" in item["fix"] and "init" in item["fix"]


# ------------------------------------------------------------------- KYO-560.c8


def test_c8_the_onboarding_item_reads_the_whole_map_back(project: Path) -> None:
    assert check(project)["onboarding"]["status"] == "not-needed"
    append_config(project, ANSWERS + "  D11:\n    status: declined\n    reason: no PII\n")
    item = check(project)["onboarding"]
    assert item["status"] == "present"
    assert item["detail"] == "B2 declined; D4 deferred (no code yet); D11 declined (no PII)"
    assert item["fix"] is None


# ------------------------------------------------------------------- KYO-560.c4


@pytest.mark.parametrize(
    "value",
    [
        ["B2"],  # a list, not a mapping
        "declined",  # a bare scalar for the whole map
        {"B2": False},  # `B2: no`, which YAML reads as false
        {"B2": "maybe"},
        {"B2": {"status": "declined", "why": "x"}},
        {"B2": {"reason": "x"}},
        {"B2": {"status": "declined", "reason": ""}},
        {"B2": {"status": "declined", "reason": 3}},
        {"b2": "declined"},
        {"Remote": "declined"},
        {4: "declined"},
    ],
)
def test_c4_any_other_shape_is_refused_naming_the_key(value: object) -> None:
    with pytest.raises(KyoseiError) as raised:
        parse_onboarding(value, Path("kyosei/config.yml"))
    message = str(raised.value)
    assert "key 'onboarding'" in message
    assert "hand edit" in message and "B2: declined" in message


def test_c4_the_check_reports_the_refusal_and_its_remedy_clears_it(project: Path) -> None:
    git_init(project)
    append_config(project, "onboarding:\n  B2: no\n")
    items = check(project)
    assert items["onboarding"]["status"] == "unreadable"
    assert "key 'onboarding' entry 'B2'" in items["onboarding"]["detail"]
    # Nothing is guessed from a map this build cannot read.
    assert items["store remote"]["status"] == "missing"
    # The remedy the message names: write it as the example shows.
    path = config_path(project)
    path.write_text(
        path.read_text(encoding="utf-8").replace("  B2: no\n", "  B2: declined\n"),
        encoding="utf-8",
    )
    items = check(project)
    assert items["onboarding"]["status"] == "present"
    assert items["store remote"]["status"] == "declined"


def test_c4_a_malformed_map_never_blinds_another_command(project: Path) -> None:
    append_config(project, "onboarding:\n  B2: no\n")
    load_config(project)
    assert invoke(["--root", str(project), "list"]).code == 0


# ------------------------------------------------------ KYO-560.c5, KYO-563.c1


def test_c5_init_writes_a_commented_onboarding_example_that_parses(project: Path) -> None:
    text = config_path(project).read_text(encoding="utf-8")
    assert "onboarding" not in yaml.safe_load(text)
    example = CONFIG_KEY_EXAMPLES["onboarding"]
    assert "# onboarding: read by cmd_onboard.answered_items" in text
    assert example in text
    answers = parse_onboarding(yaml.safe_load(uncommented(example))["onboarding"], Path("c"))
    assert {a.decision: a.status for a in answers.values()} == {
        "B2": "declined",
        "D4": "deferred",
    }


def test_563_c1_init_writes_a_commented_worker_model_stub_with_its_consumer(
    project: Path,
) -> None:
    text = config_path(project).read_text(encoding="utf-8")
    assert "worker_model" not in yaml.safe_load(text)
    assert f"# worker_model: read by {', '.join(CONFIG_CONSUMERS['worker_model'])}\n" in text
    example = CONFIG_KEY_EXAMPLES["worker_model"]
    assert example in text
    append_config(project, uncommented(example))
    assert load_config(project).worker_model == "claude-sonnet-5-5"


# ---------------------------------------------------------- KYO-560.c3, c8 (prompt)


def test_c3_the_prompt_tells_the_agent_to_record_declined_and_deferred_answers() -> None:
    text = cmd_onboard.packaged_prompt_text()
    apply = text[text.index("Then apply round two yourself") : text.index("## Step 6")]
    assert "`onboarding:` map in `kyosei/config.yml`" in apply
    assert "declined or deferred" in apply
    assert "`B2: declined`" in apply and "status: deferred" in apply
    # The example the prompt gives is one the parser accepts.
    assert parse_onboarding({"D4": {"status": "deferred", "reason": "no code yet"}}, Path("c"))


def test_c8_the_readback_step_reads_the_onboarding_map_back() -> None:
    text = cmd_onboard.packaged_prompt_text()
    step6 = text[text.index("## Step 6") :]
    assert re.search(r"^  onboarding: +<each declined or deferred decision", step6, re.M)
    assert "Read the `onboarding:` line back" in step6
    assert "onboarding" in cmd_onboard.PROJECT_LABELS


# ------------------------------------------------------------------- KYO-563.c4


def test_563_c4_the_install_steps_match_what_each_command_writes(project: Path) -> None:
    text = cmd_onboard.packaged_prompt_text()
    step4 = text[text.index("## Step 4") : text.index("## Step 5")]
    # KYO-596: the plugin is the default and the copies the alternative.
    assert "If **[C1]** chose the plugin (the default): `kyo harness install claude-code`" in step4
    assert "If **[C1]** chose the copies: `kyo install-hooks --copies`" in step4
    assert "If **[C1]** was no: `kyo install-skill --copies`" in step4
    assert "do not run\n   `kyo install-skill` after it" in step4
    # Measured, not asserted from prose: install-hooks alone leaves the skill and the
    # CLAUDE.md section current, so a following install-skill would write nothing.
    git_init(project)
    assert invoke(["--root", str(project), "install-hooks", "--copies"]).code == 0
    items = check(project)
    for label in ("hooks", "skill", "CLAUDE.md section"):
        assert items[label]["status"] == "present", items[label]
    again = invoke(["--root", str(project), "install-skill", "--copies"])
    assert again.code == 0
    assert all("already current" in line for line in again.out.splitlines() if line)


# ------------------------------------------------------------------- KYO-563.c5


def test_563_c5_d5_is_named_by_its_orchestrator_spelling_and_the_alias_it_reads(
    project: Path,
) -> None:
    assert "orchestrator_only_commands" in cmd_onboard.PROJECT_LABELS
    assert "controller_only_commands" not in cmd_onboard.PROJECT_LABELS
    item = check(project)["orchestrator_only_commands"]
    assert item["status"] == "missing"
    assert "`controller_only_commands`" in item["detail"]
    assert "orchestrator_only_commands:" in item["fix"]
    append_config(project, "controller_only_commands:\n  - make test\n")
    item = check(project)["orchestrator_only_commands"]
    assert item["status"] == "present"
    assert item["detail"].startswith("make test (declared as `controller_only_commands`")
    assert "`orchestrator_only_commands`" in item["detail"]


# ------------------------------------------------------------------- KYO-563.c6


def test_563_c6_the_kinds_schema_comment_lists_worker_model_under_policy() -> None:
    text = (PACKAGED / "kinds.yml").read_text(encoding="utf-8")
    schema = text[text.index("#     policy:") : text.index("# `intake`")]
    work = schema[: schema.index("# archetype: knowledge")]
    assert re.search(r"^#\s+worker_model: <model id>", work, re.M)


# --------------------------------------------------------- KYO-563.c2, c3, c7


def packaged_ledger() -> str:
    return (PACKAGED / "procedures.md").read_text(encoding="utf-8")


def test_563_c2_the_packaged_ledger_ships_the_project_rules_section() -> None:
    text = packaged_ledger()
    assert text.count(f"\n## {PROJECT_RULES_SECTION}\n") == 1
    note = text[text.index(f"## {PROJECT_RULES_SECTION}") :]
    assert "`kyo install-procedures` keeps this section\nas written" in note
    assert "`kyo rules add`" in note


def test_563_c2_the_sections_note_is_not_read_as_part_of_the_rule_before_it() -> None:
    """The note sits under a ``##`` heading above any rule; a parser that ended a rule
    only at the next ``###`` gave it to the last human rule, which then compared as
    reworded in every project ledger without the note."""
    text = packaged_ledger()
    last = [r.title for r in parse_procedures(text)][-1]
    assert "Project rules" not in rule_sections(text)[last]
    assert "install-procedures" not in rule_bodies(text)[last]
    without = text[: text.index(f"\n---\n\n## {PROJECT_RULES_SECTION}")] + "\n"
    assert rule_bodies(without) == rule_bodies(text)


def test_563_c3_the_rule_format_shows_several_audiences_with_an_example() -> None:
    head = preamble(packaged_ledger())
    assert "One rule may name several audiences" in head
    assert "comma-separated" in head
    example = "- **audience**: worker, orchestrator"
    assert example in head
    rule = f"### Example\n\n{example}\n- **enforced-by**: aspirational\n\nWhy.\n"
    (parsed,) = parse_procedures(rule)
    assert audiences_of(parsed) == {"worker", "orchestrator"}


def test_563_c7_install_procedures_leaves_a_project_rule_unchanged(
    kyo, project: Path, tmp_path: Path
) -> None:
    ledger = project / "kyosei" / "procedures.md"
    reason = tmp_path / "reason.md"
    reason.write_text("Customer exports are regulated. One leaked in a fixture.\n", "utf-8")
    added = kyo(
        "rules", "add", "--title", "Never paste a customer export into evidence",
        "--audience", "worker,orchestrator", "--enforced-by", "aspirational",
        "--reason-file", str(reason),
    )
    assert added.code == 0, added
    text = ledger.read_text(encoding="utf-8")
    section = text[text.index(f"## {PROJECT_RULES_SECTION}") :]
    assert "### Never paste a customer export into evidence" in section
    # Take a packaged rule out -- the one just above the section -- so the merge has
    # something to write, right beside the project's own.
    human = "### Purge stale ideas from `kyo ideas review` periodically"
    start = text.index(human)
    end = text.index("\n---\n\n## Project rules")
    ledger.write_text(text[:start] + text[end + 1 :].lstrip("\n"), encoding="utf-8")
    assert human not in ledger.read_text(encoding="utf-8")

    result = kyo("install-procedures")
    assert result.code == 0, result
    after = ledger.read_text(encoding="utf-8")
    assert human in after
    assert after[after.index(f"## {PROJECT_RULES_SECTION}") :] == section
    assert after.index(human) < after.index(f"## {PROJECT_RULES_SECTION}")
