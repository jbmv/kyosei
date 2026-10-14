"""KYO-607: `kyo install-agents` writes the worker agent types the operator chose in setup
decision C4 into ``.claude/agents/``, from the packaged templates, with the operator's
model and effort; it refuses to overwrite a differing file without ``--force``; and
`kyo onboard --check` reads the files it wrote.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kyosei import cmd_install_agents
from kyosei.cli import COMMAND_TABLE, MUTATING_COMMANDS

from .conftest import invoke
from .test_kyo_onboard import check, set_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def install(project: Path, *args: str):
    return invoke(["--root", str(project), "install-agents", *args])


def front_matter(path: Path) -> tuple[dict, str]:
    _, front, body = path.read_text(encoding="utf-8").split("---", 2)
    return yaml.safe_load(front), body


def agents(project: Path) -> Path:
    return project / ".claude" / "agents"


def test_install_agents_is_registered_and_writes_no_store() -> None:
    assert "install-agents" in COMMAND_TABLE
    assert "install-agents" not in MUTATING_COMMANDS


def test_each_chosen_type_is_written_with_the_operators_model_and_effort(
    project: Path,
) -> None:
    result = install(project, "--type", "worker=claude-sonnet-4-5", "--type", "designer=opus:xhigh")
    assert result.code == 0, result
    worker_meta, worker_body = front_matter(agents(project) / "kyosei-worker.md")
    designer_meta, designer_body = front_matter(agents(project) / "kyosei-designer.md")
    assert worker_meta["name"] == "kyosei-worker"
    assert worker_meta["model"] == "claude-sonnet-4-5"
    assert worker_meta["effort"] == "medium"  # the worker template's default
    assert designer_meta["name"] == "kyosei-designer"
    assert designer_meta["model"] == "opus"
    assert designer_meta["effort"] == "xhigh"
    for body in (worker_body, designer_body):
        assert "kyo show <dispatch-id>" in body
        assert "Never write under `kyosei/` or `ideas/`" in body
        assert "Do not run the whole\nsuite" in body or "Do not run the whole suite" in body
        assert "{{" not in body


def test_only_the_chosen_types_are_written(project: Path) -> None:
    assert install(project, "--type", "designer=opus").code == 0
    assert sorted(p.name for p in agents(project).iterdir()) == ["kyosei-designer.md"]
    assert front_matter(agents(project) / "kyosei-designer.md")[0]["effort"] == "high"


def test_a_provider_model_id_with_a_colon_is_read_whole(project: Path) -> None:
    assert install(project, "--type", "worker=anthropic.claude-sonnet-v1:0").code == 0
    meta, _ = front_matter(agents(project) / "kyosei-worker.md")
    assert meta["model"] == "anthropic.claude-sonnet-v1:0"
    assert meta["effort"] == "medium"


def test_an_existing_differing_file_is_refused_and_force_clears_it(project: Path) -> None:
    assert install(project, "--type", "worker=sonnet").code == 0
    designer = agents(project) / "kyosei-designer.md"
    designer.write_text("---\nmodel: opus\n---\nThe operator's own text.\n", "utf-8")
    worker_before = (agents(project) / "kyosei-worker.md").read_text(encoding="utf-8")

    refused = install(project, "--type", "worker=haiku", "--type", "designer=opus")
    assert refused.code != 0
    assert "--force" in refused.err and "kyosei-designer.md" in refused.err
    # Nothing was written, not even the file that could have been.
    assert designer.read_text(encoding="utf-8").endswith("The operator's own text.\n")
    assert (agents(project) / "kyosei-worker.md").read_text(encoding="utf-8") == worker_before

    # The remedy the message names clears it.
    forced = install(project, "--type", "worker=haiku", "--type", "designer=opus", "--force")
    assert forced.code == 0, forced
    assert front_matter(designer)[0]["model"] == "opus"
    assert front_matter(agents(project) / "kyosei-worker.md")[0]["model"] == "haiku"


def test_a_rerun_with_the_same_choice_is_current_and_needs_no_force(project: Path) -> None:
    assert install(project, "--type", "worker=sonnet").code == 0
    again = install(project, "--type", "worker=sonnet", "--json")
    assert again.code == 0, again
    assert again.json["changed"] is False
    assert again.json["files"][0]["state"] == "current"


def test_print_writes_nothing(project: Path) -> None:
    result = install(project, "--type", "designer=opus", "--print")
    assert result.code == 0
    assert "would write .claude/agents/kyosei-designer.md" in result.out
    assert "model: opus" in result.out
    assert not agents(project).exists()


@pytest.mark.parametrize(
    "spec",
    ["worker", "tester=opus", "worker=", "worker=two words", "designer=opus\nmodel: x"],
)
def test_a_malformed_type_is_refused_and_writes_nothing(project: Path, spec: str) -> None:
    result = install(project, "--type", spec)
    assert result.code != 0
    assert "TEMPLATE=MODEL" in result.err or "not a model id" in result.err
    assert not agents(project).exists()


def test_no_type_names_the_usage_and_the_default(project: Path) -> None:
    result = install(project)
    assert result.code != 0
    assert "--type TEMPLATE=MODEL[:EFFORT]" in result.err
    assert "general-purpose subagent" in result.err
    assert not agents(project).exists()


def test_a_type_named_twice_is_refused(project: Path) -> None:
    result = install(project, "--type", "worker=opus", "--type", "worker=sonnet")
    assert result.code != 0
    assert "twice" in result.err
    # The remedy the message names, each template once, clears it.
    assert install(project, "--type", "worker=sonnet").code == 0


@pytest.mark.parametrize(("template", "effort"), [("worker", "medium"), ("designer", "high")])
def test_the_templates_are_this_repositorys_hand_written_types(template: str, effort: str) -> None:
    # The hand-written files in this repository are the model for the templates
    # (KYO-607); rendered with the same model and effort they must read the same.
    hand_written = REPO_ROOT / ".claude" / "agents" / f"kyosei-{template}.md"
    if not hand_written.is_file():
        pytest.skip("not Kyosei's own repository")
    assert cmd_install_agents.render(template, "opus", effort) == hand_written.read_text(
        encoding="utf-8"
    )


# ------------------------------------------------- c3: kyo onboard --check reads them


def test_onboard_check_reads_the_written_types_and_reports_a_disagreeing_model(
    project: Path,
) -> None:
    set_config(project, "worker_model: claude-sonnet-4-5")
    assert (
        install(project, "--type", "worker=claude-sonnet-4-5", "--type", "designer=opus").code == 0
    )
    item = check(project)["agent types"]
    assert item["status"] == "disagrees"
    assert "kyosei-designer.md runs opus" in item["detail"]
    assert "kyosei-worker.md" not in item["fix"]
    assert ".claude/agents/kyosei-designer.md" in item["fix"]

    assert install(project, "--type", "designer=sonnet:high", "--force").code == 0
    item = check(project)["agent types"]
    assert item["status"] == "present"
    assert "kyosei-designer.md: sonnet" in item["detail"]
    assert "kyosei-worker.md: claude-sonnet-4-5" in item["detail"]


def test_a_project_with_no_types_still_runs_the_general_purpose_subagent(
    project: Path,
) -> None:
    item = check(project)["agent types"]
    assert item["status"] == "not-needed"
    assert "general-purpose subagent" in item["detail"]


# ------------------------------------------------- c1: the prompt asks C4 after D1


def test_the_prompt_asks_c4_on_its_own_after_d1_with_examples() -> None:
    from kyosei import cmd_onboard

    from .test_kyo_onboard import prompt_entries

    text = cmd_onboard.render_prompt()
    assert text.index("- **[C4]") > text.index("- **[D1]")
    c4 = " ".join(prompt_entries(text)["C4"].split())
    assert "Ask after **[D1]**, as its own question" in c4
    assert "An agent type is a subagent definition in `.claude/agents/`" in c4
    assert "Default: none" in c4
    assert "a designer on Opus 5.5 at high effort" in c4
    assert "proposals, measurements and design work" in c4
    assert "worker on a faster model" in c4 and "at medium effort" in c4
    assert "build tasks whose design is settled" in c4
    assert "confirm that the operator's harness offers each model" in c4
    assert "Do not ask separately" not in c4
    assert "kyo install-agents --type" in text


def test_setup_decisions_c4_is_settled_and_matches_the_prompt() -> None:
    decisions = (REPO_ROOT / "docs" / "guide" / "setup-decisions.md").read_text("utf-8")
    heading = next(line for line in decisions.splitlines() if line.startswith("### C4."))
    assert "not settled" not in heading
    c4 = " ".join(decisions.split("### C4.", 1)[1].split("### D1.", 1)[0].split())
    assert "It is not asked as a separate question" not in c4
    assert "a designer on Opus 5.5 at high effort" in c4
    assert "kyo install-agents" in c4
    assert "**Default:** none" in c4
