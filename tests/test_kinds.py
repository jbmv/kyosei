"""The kind registry: loading, validation, and the failure modes each rule guards.

Milestone 2, item 1. Every validation rule gets a test that breaks it — the same
discipline ``kyo doctor``'s checks follow (law 14).

KYO-104 moved the loading and the validating into :mod:`kyosei.registry`, where the
other three registry dimensions already were, and left the kind-specific rules declared on
:data:`kinds.KIND`. Every test above the KYO-104 section is unchanged in what it asks
for, which is the point: the rules are the same rules, checked by shared code. The
section at the bottom is about the fold itself — that it happened, and that it cannot be
half-undone without something going red.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kyosei import kinds as kinds_module
from kyosei import registry as registry_module
from kyosei.kinds import KindRegistryError, load_kind_registry

from .conftest import new_record

VALID = """
kinds:
  bug:
    archetype: work
    summary: A defect.
    accepts_from: [intake]
    produces: [task]
    policy: {requires_criteria: true, dispatchable: true, unattended_ok: true,
              write_scope: code, swept: true, counted: true}
  task:
    archetype: work
    summary: A unit of work.
    accepts_from: [bug]
    produces: []
    policy: {requires_criteria: true, dispatchable: true, unattended_ok: true,
              write_scope: code, swept: true, counted: true}
"""


def _write(tmp_path: Path, text: str) -> Path:
    kyosei_dir = tmp_path / "kyosei"
    kyosei_dir.mkdir(exist_ok=True)
    (kyosei_dir / "kinds.yml").write_text(text, encoding="utf-8")
    return kyosei_dir


def test_a_valid_registry_loads(tmp_path: Path) -> None:
    registry = load_kind_registry(_write(tmp_path, VALID))
    assert registry.names == ["bug", "task"]
    assert registry["bug"].archetype == "work"
    assert registry["bug"].requires_criteria is True
    assert registry.require("task").name == "task"


def test_the_shipped_default_registry_validates() -> None:
    """The registry ``kyo init`` actually ships must itself pass validation."""
    packaged = Path(__file__).resolve().parents[1] / "src" / "kyosei" / "defaults"
    registry = load_kind_registry(packaged)
    assert "bug" in registry
    assert registry["decision"].archetype == "knowledge"
    assert registry["plan"].requires_criteria is False


def test_every_work_kind_in_the_shipped_default_accepts_intake() -> None:
    """KYO-228: the idea-first rule exists so structure appears at promotion, but
    `kyo new` takes criteria and touches too, so filing directly loses no structure;
    what it loses is the idea file as provenance, which adds nothing for text the
    controller has just written.

    Every ``archetype: work`` kind the packaged default ships now carries ``intake`` in
    ``accepts_from``, so ``kyo new`` will file any of them directly — no kind is left
    reachable only through ``kyo promote``. ``archetype: knowledge`` kinds are untouched
    by this decision.
    """
    packaged = Path(__file__).resolve().parents[1] / "src" / "kyosei" / "defaults"
    registry = load_kind_registry(packaged)
    work_kinds = [name for name in registry.names if registry[name].archetype == "work"]
    assert work_kinds, "expected at least one archetype: work kind in the packaged default"
    not_direct = [name for name in work_kinds if not registry[name].accepts_intake]
    assert not_direct == [], (
        f"these archetype: work kinds do not accept intake: {', '.join(not_direct)}"
    )


def test_an_investigation_can_spawn_a_bug_in_the_shipped_default(kyo, write_file) -> None:
    """KYO-341: before this, `bug.accepts_from` was ``[intake, idea]`` and
    `investigation.produces` did not list ``bug`` -- "the investigation found the
    defect" is the most natural kind-to-kind edge there is, and the packaged
    default had no edge for it at all, so `kyo spawn <investigation-id> --kind bug`
    was refused (a consumer project's controller filed the bug directly and added
    the `produced-by` link by hand). Fails against the pre-KYO-341 registry, where
    that refusal is exactly what happens.

    ``kyo`` (via the ``project`` fixture) scaffolds from the packaged default
    (``kyo init`` copies ``src/kyosei/defaults/kinds.yml`` verbatim), so this reads
    the same two lines a project actually ships with.
    """
    investigation = new_record(
        kyo,
        kind="investigation",
        title="Why does the widget explode?",
        criteria=["root cause identified"],
    )
    result = kyo(
        "spawn",
        investigation,
        "--kind",
        "bug",
        "--title",
        "The widget explodes under cold cache",
        "--criteria-file",
        write_file("crit.txt", "it stops exploding\n"),
        "--touches-file",
        write_file("touch.txt", "kyosei/config.yml\n"),
        "--json",
    )
    assert result.code == 0, result
    bug = result.json["record"]
    assert bug["kind"] == "bug"
    assert result.json["link"] == {
        "id": result.json["link"]["id"],
        "src": bug["id"],
        "type": "produced-by",
        "dst": investigation,
    }


def test_missing_registry_file_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(KindRegistryError, match="does not exist"):
        load_kind_registry(tmp_path / "kyosei")


def test_unknown_archetype_is_named(tmp_path: Path) -> None:
    text = VALID.replace(
        "archetype: work\n    summary: A defect.",
        "archetype: sideways\n    summary: A defect.",
        1,
    )
    with pytest.raises(KindRegistryError, match="'bug'.*archetype"):
        load_kind_registry(_write(tmp_path, text))


def test_unknown_entry_key_is_named(tmp_path: Path) -> None:
    """A kind entry may carry the five keys ``KIND`` declares and nothing else — law 2's
    rot with a config file around it, otherwise.

    KYO-104 folded this onto the shared loader, so the refusal now also LISTS what a
    kind entry may carry, read off the dimension rather than off a hand-kept constant.
    That is the half worth pinning: a reader who misspelled ``accepts_from`` is told the
    spelling instead of only that theirs is wrong.
    """
    raw = yaml.safe_load(VALID)
    raw["kinds"]["bug"]["extra_field"] = "surprise"
    with pytest.raises(KindRegistryError, match="unknown key.*extra_field") as caught:
        load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))
    message = str(caught.value)
    for legal in ("accepts_from", "archetype", "policy", "produces", "summary"):
        assert legal in message, message


def test_incomplete_work_policy_is_named(tmp_path: Path) -> None:
    raw = yaml.safe_load(VALID)
    del raw["kinds"]["bug"]["policy"]["dispatchable"]
    with pytest.raises(KindRegistryError, match="'bug'.*missing.*dispatchable"):
        load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))


def test_unknown_work_policy_key_is_named(tmp_path: Path) -> None:
    raw = yaml.safe_load(VALID)
    raw["kinds"]["bug"]["policy"]["amendable"] = True
    with pytest.raises(KindRegistryError, match="'bug'.*unknown key.*amendable"):
        load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))


def test_bad_write_scope_is_named(tmp_path: Path) -> None:
    raw = yaml.safe_load(VALID)
    raw["kinds"]["bug"]["policy"]["write_scope"] = "everywhere"
    with pytest.raises(KindRegistryError, match="write_scope.*everywhere"):
        load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))


def test_knowledge_policy_requires_amendable_swept_counted(tmp_path: Path) -> None:
    raw = yaml.safe_load(VALID)
    raw["kinds"]["decision"] = {
        "archetype": "knowledge",
        "summary": "A decision.",
        "accepts_from": [],
        "produces": [],
        "policy": {"amendable": True},
    }
    with pytest.raises(KindRegistryError, match="'decision'.*missing.*(swept|counted)"):
        load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))


def test_knowledge_policy_accepts_the_optional_evidence_links_key(tmp_path: Path) -> None:
    raw = yaml.safe_load(VALID)
    raw["kinds"]["decision"] = {
        "archetype": "knowledge",
        "summary": "A decision.",
        "accepts_from": [],
        "produces": [],
        "policy": {
            "amendable": True,
            "swept": False,
            "counted": False,
            "requires_evidence_links": True,
        },
    }
    registry = load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))
    assert registry["decision"].policy["requires_evidence_links"] is True


def test_asymmetric_produces_is_named(tmp_path: Path) -> None:
    """bug.produces includes task, but task.accepts_from does not include bug."""
    raw = yaml.safe_load(VALID)
    raw["kinds"]["task"]["accepts_from"] = []
    with pytest.raises(KindRegistryError, match="symmetric"):
        load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))


def test_asymmetric_accepts_from_is_named(tmp_path: Path) -> None:
    raw = yaml.safe_load(VALID)
    raw["kinds"]["bug"]["produces"] = []
    with pytest.raises(KindRegistryError, match="symmetric"):
        load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))


def test_accepts_from_names_an_undefined_kind(tmp_path: Path) -> None:
    raw = yaml.safe_load(VALID)
    raw["kinds"]["task"]["accepts_from"] = ["ghost"]
    with pytest.raises(KindRegistryError, match="undefined kind 'ghost'"):
        load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))


def test_produces_may_not_target_intake_or_idea(tmp_path: Path) -> None:
    raw = yaml.safe_load(VALID)
    raw["kinds"]["bug"]["produces"].append("idea")
    with pytest.raises(KindRegistryError, match="produces must not target 'idea'"):
        load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))


def test_accepts_from_may_freely_use_intake_and_idea(tmp_path: Path) -> None:
    raw = yaml.safe_load(VALID)
    raw["kinds"]["bug"]["accepts_from"] = ["intake", "idea"]
    registry = load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))
    assert set(registry["bug"].accepts_from) == {"intake", "idea"}


def test_multiple_problems_are_all_reported_together(tmp_path: Path) -> None:
    """Loud validation collects every problem in one pass, not one-error-per-run."""
    raw = yaml.safe_load(VALID)
    del raw["kinds"]["bug"]["policy"]["dispatchable"]
    raw["kinds"]["task"]["accepts_from"] = ["ghost"]
    with pytest.raises(KindRegistryError) as caught:
        load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))
    message = str(caught.value)
    assert "dispatchable" in message
    assert "ghost" in message


# ------------------------------------------------------------------------ KYO-43


def test_accepts_intake_reads_the_registry(tmp_path: Path) -> None:
    """`kyo new`'s rule, as the registry states it: ``intake`` means "a human files
    this kind directly". VALID gives it to `bug` and withholds it from `task`, which
    is the asymmetry the default registry encodes and KYO-43 made enforceable."""
    registry = load_kind_registry(_write(tmp_path, VALID))
    assert registry["bug"].accepts_intake is True
    assert registry["task"].accepts_intake is False


def test_direct_filing_names_are_derived_not_listed(tmp_path: Path) -> None:
    """KYO-43.c2: the set `kyo new` will file comes from this project's own file.
    Give `task` ``intake`` and it joins, with no code change anywhere."""
    raw = yaml.safe_load(VALID)
    registry = load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))
    assert registry.direct_filing_names == ["bug"]

    raw["kinds"]["task"]["accepts_from"] = ["bug", "intake"]
    registry = load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))
    assert registry.direct_filing_names == ["bug", "task"]


def test_promotable_names_are_derived_too(tmp_path: Path) -> None:
    """The other half of what a refused caller needs: which kinds `kyo promote`
    takes from an idea."""
    raw = yaml.safe_load(VALID)
    assert load_kind_registry(_write(tmp_path, yaml.safe_dump(raw))).promotable_names == []

    raw["kinds"]["task"]["accepts_from"] = ["bug", "idea"]
    registry = load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))
    assert registry.promotable_names == ["task"]


# ------------------------------------------------------------------------ KYO-50


def test_spawn_targets_are_derived_from_the_registry_too(tmp_path: Path) -> None:
    """KYO-50.c4: the third member of the same family. ``accepts_from``'s
    kind-to-kind entries stop being a picture here — this is the lookup
    `kyo spawn` walks, read from the project's own file like the other two."""
    raw = yaml.safe_load(VALID)
    registry = load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))
    assert registry.spawn_targets("bug") == ["task"]
    assert registry.spawn_targets("task") == []
    assert registry["task"].accepts_kind("bug") is True
    assert registry["bug"].accepts_kind("task") is False

    # Add the edge in both directions (the registry validates symmetry) and the
    # traversal follows, with no code change.
    raw["kinds"]["bug"]["accepts_from"] = ["intake", "task"]
    raw["kinds"]["task"]["produces"] = ["bug"]
    registry = load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))
    assert registry.spawn_targets("task") == ["bug"]


def test_the_pseudo_sources_are_not_spawn_targets(tmp_path: Path) -> None:
    """``intake``/``idea`` are sources, never kinds, so they can never appear as
    something to spawn — the registry refuses them in ``produces`` and this lookup
    only ever returns defined kind names."""
    registry = load_kind_registry(_write(tmp_path, VALID))
    assert registry.spawn_targets("intake") == []
    assert registry.spawn_targets("idea") == []


# ----------------------------------------------------------------------- KYO-104
#
# The fold itself. Everything above asks the same questions it asked before the kind
# registry moved onto the shared loader; these ask whether it actually moved, and each
# one goes red if some part of it is put back.


def test_the_kind_registry_is_loaded_by_the_shared_loader(tmp_path: Path) -> None:
    """KYO-104.c1: one code path, evidenced rather than asserted.

    A ``KindRegistry`` is a view over the :class:`registry.Registry` the shared loader
    produced, for the dimension ``kinds.py`` declares — so a second loader here could
    not produce one without producing that too. The name regex is the same *object* as
    every other dimension's, which is what "no name regex of its own" means when the
    pattern is three characters long and easy to retype identically.
    """
    loaded = load_kind_registry(_write(tmp_path, VALID))
    assert isinstance(loaded.registry, registry_module.Registry)
    assert loaded.registry.dimension is kinds_module.KIND
    assert loaded.path == loaded.registry.path
    assert kinds_module.KIND_NAME_RE is registry_module.NAME_RE
    # The keys a kind entry may carry are read off the declaration, not off a constant
    # this module keeps a second copy of.
    assert not hasattr(kinds_module, "TOP_LEVEL_KIND_KEYS")


def test_the_kind_rules_and_the_shape_rules_arrive_in_one_refusal(tmp_path: Path) -> None:
    """The reason the kind rules are a hook on the dimension and not a second pass.

    ``summary`` is the shared shape's rule; ``policy`` keys per archetype and edge
    symmetry are this dimension's own. A registry that breaks all three is fixed in one
    pass (law 6) only if all three are collected before anything is raised — split them
    into two passes and this message loses whichever half raised second.
    """
    raw = yaml.safe_load(VALID)
    raw["kinds"]["bug"]["summary"] = ""
    del raw["kinds"]["task"]["policy"]["counted"]
    raw["kinds"]["task"]["accepts_from"] = []
    with pytest.raises(KindRegistryError) as caught:
        load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))
    message = str(caught.value)
    assert "summary must be a non-empty string" in message, message
    assert "policy is missing counted" in message, message
    assert "symmetric" in message, message


def test_a_sibling_with_its_own_mistake_is_still_a_declared_kind(tmp_path: Path) -> None:
    """One mistake, reported once.

    ``bug`` is refused for its own summary, and that must not also make every
    ``accepts_from: [bug]`` elsewhere in the file read as a reference to an undefined
    kind — which is what a cross-reference check run against the *validated* entries
    alone would say.
    """
    raw = yaml.safe_load(VALID)
    raw["kinds"]["bug"]["summary"] = ""
    with pytest.raises(KindRegistryError) as caught:
        load_kind_registry(_write(tmp_path, yaml.safe_dump(raw)))
    assert "undefined kind 'bug'" not in str(caught.value), caught.value


def test_the_missing_file_refusal_is_the_shared_one(tmp_path: Path) -> None:
    """KYO-104.c1's other half: ``Dimension.required`` is what makes absence loud here,
    and the recovery it names is :func:`registry.missing_required_remedy` — the KYO-82
    message, generalised rather than copied. ``kinds.yml`` is the only dimension that
    sets ``required``, and this is what setting it buys."""
    assert kinds_module.KIND.required is True
    (tmp_path / "kyosei").mkdir()
    with pytest.raises(KindRegistryError) as caught:
        load_kind_registry(tmp_path / "kyosei")
    message = str(caught.value)
    assert "does not exist" in message
    assert registry_module.missing_required_remedy(tmp_path / "kyosei" / "kinds.yml") in message


def test_a_kinds_yml_that_declares_nothing_is_refused(tmp_path: Path) -> None:
    """"Load-bearing" is a claim about the entries, not about the inode.

    A ``kinds.yml`` that parses and names no kind leaves every command with no
    vocabulary for what a record is, so it fails the same way an absent file does rather
    than loading as an empty registry the way an absent ``scopes.yml`` legitimately does.
    """
    for text in ("kinds:\n", "kinds: {}\n"):
        with pytest.raises(KindRegistryError, match="declares no kinds"):
            load_kind_registry(_write(tmp_path, text))


def test_an_unknown_kind_is_refused_in_the_registry_s_own_words(tmp_path: Path) -> None:
    """`KindRegistry.require` raises the shared refusal, which names the file, what it
    does declare, and the entry that would make the name legal — the sentence every
    dimension's unknown-name refusal is (KYO-100), reaching kinds with KYO-104."""
    loaded = load_kind_registry(_write(tmp_path, VALID))
    with pytest.raises(KindRegistryError) as caught:
        loaded.require("ghost")
    message = str(caught.value)
    assert "kind 'ghost' is not declared" in message, message
    assert "declared kinds: bug, task" in message, message
