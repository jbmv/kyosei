"""KYO-61: a dispatch declares which model its worker runs on.

Raised on noticing that every worker in a session had run on the same model as the
controller, not by a decision but by the absence of one. Nothing in Kyosei expressed a
worker model, so the choice existed, was
consequential, and lived entirely outside the store: `kyo dispatch` recorded the
worktree, the branch, the base sha and the frozen brief — the things that make a
dispatch reproducible — and said nothing about what actually executed it.

**The enforcement level is the whole design decision, and it is `aspirational`.**
Kyosei does not spawn workers and must not start; the controller's harness does, so
no `kyo` command can make a harness honour this value. A field the tool records but
cannot verify is exactly the decoration law 2 exists to prevent *unless it says so*,
which is why every surface below states the level rather than implying a guarantee.

The self-reported variant was considered and refused on the record: a worker
restating the condition its own dispatch declared is a second, weaker source of
truth for one fact (KYO-32's lesson), and law 3 says the two then get to disagree.
"""

from __future__ import annotations

import yaml

from kyosei import cmd_dispatch
from kyosei.errors import EXIT_USAGE

from .conftest import kinds_yml, new_record


def _set_config(project, **values) -> None:
    path = project / "kyosei" / "config.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw.update(values)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def _set_kind_model(project, kind: str, model: str) -> None:
    path = kinds_yml(project)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["kinds"][kind]["policy"]["worker_model"] = model
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


# --------------------------------------------------------------------- KYO-61.c1


def test_a_project_declares_a_default_worker_model_in_config(kyo, git_project) -> None:
    """The base layer: a project property, not a session property. A controller
    re-deciding this from scratch every session is the same failure as any other
    unrecorded standing decision."""
    _set_config(git_project, worker_model="project-default")
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--json")
    assert result.code == 0, result
    assert result.json["worker_model"] == "project-default"
    assert result.json["worker_model_source"] == cmd_dispatch.MODEL_SOURCE_PROJECT


def test_the_config_key_passes_doctors_config_consumers_check(kyo, git_project) -> None:
    """KYO-61.c1 names this explicitly, and it is the law-2 half: `worker_model` is
    registered in CONFIG_CONSUMERS, so a project that sets it is not carrying a key
    no code path reads — and `doctor` resolves the named reader, so the entry cannot
    rot into a name that no longer exists (KYO-62)."""
    _set_config(git_project, worker_model="project-default")
    result = kyo("doctor", "--json")
    assert result.code == 0, result
    assert result.json["checks"]["config_consumers"]["failures"] == []


def test_a_kind_may_override_the_project_default(kyo, git_project) -> None:
    """Beside `dispatchable` / `unattended_ok` / `write_scope`, because it is the
    same shape of thing: per-kind dispatch policy."""
    _set_config(git_project, worker_model="project-default")
    record = new_record(kyo, criteria=["a"])
    kind = kyo("show", record, "--json").json["kind"]
    _set_kind_model(git_project, kind, "kind-override")

    result = kyo("dispatch", record, "--json")
    assert result.code == 0, result
    assert result.json["worker_model"] == "kind-override"
    assert result.json["worker_model_source"] == cmd_dispatch.MODEL_SOURCE_KIND


def test_a_dispatch_may_override_both(kyo, git_project) -> None:
    _set_config(git_project, worker_model="project-default")
    record = new_record(kyo, criteria=["a"])
    kind = kyo("show", record, "--json").json["kind"]
    _set_kind_model(git_project, kind, "kind-override")

    result = kyo("dispatch", record, "--model", "dispatch-override", "--json")
    assert result.code == 0, result
    assert result.json["worker_model"] == "dispatch-override"
    assert result.json["worker_model_source"] == cmd_dispatch.MODEL_SOURCE_DISPATCH


def test_an_unknown_kind_policy_key_is_still_refused(kyo, git_project) -> None:
    """`worker_model` became legal in a work kind's policy; nothing else did. The
    registry's "exactly these keys" discipline is what makes a typo a failure rather
    than a silently ignored line, and widening it by one name must not widen it by
    two."""
    path = kinds_yml(git_project)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["kinds"]["task"]["policy"]["worker_modle"] = "typo"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    result = kyo("doctor")
    assert result.code != 0
    assert "worker_modle" in result.out + result.err


def test_an_empty_kind_policy_model_is_refused_rather_than_ignored(
    kyo, git_project
) -> None:
    path = kinds_yml(git_project)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["kinds"]["task"]["policy"]["worker_model"] = ""
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    result = kyo("doctor")
    assert result.code != 0
    assert "worker_model" in result.out + result.err


def test_an_empty_config_model_is_refused_rather_than_read_as_absent(
    kyo, git_project
) -> None:
    """`worker_model:` with nothing after it reads to a human as a declaration.
    Treating it as absence is the unknown-borrowing-a-value failure law 6 forbids."""
    _set_config(git_project, worker_model="")
    result = kyo("list")
    assert result.code != 0
    assert "worker_model" in result.err


def test_an_empty_model_flag_is_refused(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--model", "  ")
    assert result.code == EXIT_USAGE
    assert "--model" in result.err


# --------------------------------------------------------------------- KYO-61.c2


def test_the_resolved_model_is_recorded_on_the_dispatch(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record, "--model", "some-model").code == 0
    dispatch = kyo("show", record, "--json").json["dispatches"][0]
    assert dispatch["worker_model"] == "some-model"


def test_the_brief_states_the_model_as_a_machine_filled_slot(kyo, git_project) -> None:
    """The same slot style as `--worktree` and `--branch`, one line down — and,
    unlike those two, it states its own enforcement level, because it has none."""
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--model", "some-model")
    assert result.code == 0, result
    brief = kyo("show", record, "--json").json["dispatches"][0]["brief"]
    assert "**Worker model**: `some-model`" in brief
    assert "enforced-by: aspirational" in brief


def test_a_dispatch_that_declares_no_model_says_so_rather_than_inventing_one(
    kyo, git_project
) -> None:
    """KYO-61.c2's second half. Rendering a default nobody chose would answer "what
    were the conditions of this work" with a guess — the same defect as inventing a
    branch name, and the reason `dispatches.branch` is NULL when undeclared."""
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--json")
    assert result.code == 0, result
    assert result.json["worker_model"] is None
    assert result.json["worker_model_source"] == cmd_dispatch.MODEL_SOURCE_NONE

    brief = kyo("show", record, "--json").json["dispatches"][0]["brief"]
    assert "**Worker model**: (not declared at dispatch time)" in brief


def test_dispatch_output_states_the_model_either_way(kyo, git_project) -> None:
    """The absence has to be as visible as the presence: a project that never
    declared one is the state every dispatch on this board ran in unnoticed."""
    record = new_record(kyo, criteria=["a"])
    undeclared = kyo("dispatch", record, "--force")
    assert undeclared.code == 0, undeclared
    assert "worker model" in undeclared.out
    assert "not declared" in undeclared.out

    _set_config(git_project, worker_model="project-default")
    second = new_record(kyo, criteria=["b"])
    declared = kyo("dispatch", second, "--force")
    assert declared.code == 0, declared
    assert "project-default" in declared.out


def test_show_renders_the_model_on_every_dispatch(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record).code == 0
    assert "worker model: (none declared" in kyo("show", record).out


# --------------------------------------------------------------------- KYO-61.c3


def test_procedures_states_the_enforcement_level_verbatim() -> None:
    """The level had to be decided and written verbatim. `aspirational` is the
    honest one: Kyosei does not spawn workers, so it cannot make a harness honour
    this — and this project's own best idea, applied to its own new field."""
    from kyosei import scaffold

    text = (scaffold.defaults_dir() / "procedures.md").read_text(encoding="utf-8")
    assert "`kyo dispatch --model`" in text
    section = text.split("### Declare the worker model")[1]
    head = section.split("###")[0]
    assert "**enforced-by**: aspirational" in head


def test_the_model_rule_reaches_the_controller_not_the_worker(kyo, git_project) -> None:
    """Audience matters (law 15): choosing the model is the controller's act, and a
    worker cannot act on this rule at all — it is told the answer, and is explicitly
    not asked to report it back."""
    from kyosei import procedures, scaffold

    text = (scaffold.defaults_dir() / "procedures.md").read_text(encoding="utf-8")
    titles = {r.title for r in procedures.orchestrator_rules(text)}
    assert any("worker model" in t.lower() for t in titles), titles
    worker_titles = {r.title for r in procedures.worker_rules(text)}
    assert not any("worker model" in t.lower() for t in worker_titles), worker_titles


# --------------------------------------------------------------------- KYO-61.c4


def test_the_model_column_has_registered_consumers(kyo, git_project) -> None:
    """Law 2, the same check `dispatches.branch` shipped with: the field and its
    readers land in one commit, and `kyo doctor` resolves every named reader."""
    from kyosei.schema import COLUMN_CONSUMERS

    consumers = COLUMN_CONSUMERS["dispatches"]["worker_model"]
    assert "cmd_dispatch.resolve_worker_model" in consumers
    assert "brief.generate_brief" in consumers

    result = kyo("doctor", "--json")
    assert result.code == 0, result
    assert result.json["checks"]["column_consumers"]["failures"] == []


def test_the_model_survives_a_rebuild(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record, "--model", "some-model").code == 0
    (git_project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert kyo("show", record, "--json").json["dispatches"][0]["worker_model"] == "some-model"


def test_a_rebuild_keeps_the_projects_declared_model(kyo, git_project) -> None:
    """`kyo rebuild` rewrites config.yml to keep `schema_version` in step with the
    database it just wrote, passing only the required keys — so an optional key that
    is not carried forward is deleted on every rebuild. KYO-60 found this with
    `publication_acknowledged`; the same trap is set for every optional key after."""
    _set_config(git_project, worker_model="project-default")
    new_record(kyo, criteria=["a"])
    (git_project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    raw = yaml.safe_load(
        (git_project / "kyosei" / "config.yml").read_text(encoding="utf-8")
    )
    assert raw["worker_model"] == "project-default"


def test_resolution_order_is_one_function_not_three_call_sites() -> None:
    """Law 3, at the unit level: the dispatch echo, the frozen brief and `--json`
    are three renderings of one resolution, so they cannot disagree about which
    declaration won."""

    class _Config:
        worker_model = "from-project"

    class _Kind:
        policy = {"worker_model": "from-kind"}

    class _KindNoOpinion:
        policy: dict = {}

    resolve = cmd_dispatch.resolve_worker_model
    assert resolve(_Config(), _Kind(), "from-flag") == (
        "from-flag",
        cmd_dispatch.MODEL_SOURCE_DISPATCH,
    )
    assert resolve(_Config(), _Kind(), None) == ("from-kind", cmd_dispatch.MODEL_SOURCE_KIND)
    assert resolve(_Config(), _KindNoOpinion(), None) == (
        "from-project",
        cmd_dispatch.MODEL_SOURCE_PROJECT,
    )

    class _NoProject:
        worker_model = None

    assert resolve(_NoProject(), _KindNoOpinion(), None) == (
        None,
        cmd_dispatch.MODEL_SOURCE_NONE,
    )


def test_nothing_asks_a_worker_to_report_the_model_back() -> None:
    """The refused option, kept refused. KYO-32's lesson applies even though the
    incentive does not: a worker reporting the condition its own dispatch declared
    is a second source of truth for one fact, and law 3 says the two then get to
    disagree. If a future change adds it to the report schema, this fails."""
    from kyosei import report_schema

    schema = report_schema.schema_document()  # KYO-149: the packaged copy
    assert "worker_model" not in schema.get("properties", {})
    assert "model" not in schema.get("properties", {})
