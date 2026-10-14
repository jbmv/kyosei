"""KYO-43: `kyo new` files only kinds whose registry entry carries ``intake``.

The registry stated a flow rule that only one of the two write paths consulted.
`kyo promote` validated ``idea`` in ``accepts_from`` and its ``--kind`` help said
so; `kyo new` checked nothing, so every kind in the default registry could be
filed directly — including ``task``, whose entry once carried a comment written
specifically to explain why it must not be. `kyo new` being unaware of the registry's
own asymmetry meant the decision was unenforced everywhere except promotion — while
the `/kyosei` skill tells the controller the registry *is* the source of truth.

The escape hatch question (KYO-43.c3) is answered here by
``test_kyo_new_has_no_force_flag``: there is no flag. The registry is the escape
hatch — see ``test_a_project_that_adds_intake_can_file_the_kind_immediately``.

KYO-228 reversed the asymmetry this file was written to pin: every ``archetype: work``
kind in the packaged default now carries ``intake`` (``kinds.yml`` quotes that record's
reasoning). What this file
still pins is the *mechanism* -- `kyo new` reads the registry's own ``accepts_from``
and refuses whatever it does not list -- which is exactly as true as it was; only the
shipped registry's *content* changed, and ``decision`` (``archetype: knowledge``,
outside KYO-228's scope) is what is left in the shipped registry to demonstrate a
refusal against. Where a test specifically needs a kind that accepts ``idea`` but not
``intake`` -- a combination no kind in the shipped registry has any more -- it
reconstructs one with :func:`_without_intake` rather than losing that coverage.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from kyosei.contracts import CONTRACT_KIND_NOT_DIRECTLY_FILEABLE
from kyosei.errors import EXIT_CONTRACT

from .conftest import kinds_yml, new_record

#: The kinds the shipped registry lets a human file directly (`kyo init`'s kinds.yml).
#: Read back from the file in the parametrised test below rather than trusted — this
#: tuple only says what the assertion is *about*.
#:
#: KYO-228 widened this from ``("bug", "evidence")``: every ``archetype: work`` kind
#: now carries ``intake``. KYO-497 added ``carry``, a work kind filed only directly, which
#: KYO-521 renamed ``reminder``; KYO-521 also gave ``decision`` ``intake`` (filed directly
#: only with ``--evidence-file``), so every default kind is in this tuple now.
DEFAULT_INTAKE_KINDS = (
    "bug", "chore", "decision", "evidence", "experiment", "investigation", "plan",
    "proposal", "reminder", "task",
)


def _registry(project: Path) -> dict:
    return yaml.safe_load(kinds_yml(project).read_text(encoding="utf-8"))["kinds"]


def _write_registry(project: Path, kinds: dict) -> None:
    kinds_yml(project).write_text(yaml.safe_dump({"kinds": kinds}, sort_keys=False), "utf-8")


def _without_intake(project: Path, kind: str) -> None:
    """Reconstructs the pre-KYO-228 shape of ``kind``'s ``accepts_from`` -- for tests
    below that need a kind which accepts ``idea`` but not ``intake``, a combination the
    shipped registry no longer has among its ``archetype: work`` kinds."""
    kinds = _registry(project)
    kinds[kind]["accepts_from"] = [s for s in kinds[kind]["accepts_from"] if s != "intake"]
    _write_registry(project, kinds)


def _decision_without_intake(project: Path) -> None:
    """Reconstructs the pre-KYO-521 ``decision``: ``accepts_from: [proposal,
    investigation]``, and ``reminder`` no longer producing it (the registry refuses one
    half of an edge without the other). KYO-521 opened ``intake`` on ``decision``, so no
    default kind is refused direct filing any more; the refusal tests below need one."""
    kinds = _registry(project)
    kinds["decision"]["accepts_from"] = ["proposal", "investigation"]
    kinds["reminder"]["produces"] = [k for k in kinds["reminder"]["produces"] if k != "decision"]
    _write_registry(project, kinds)


# --------------------------------------------------------------------- KYO-43.c1


def test_the_default_registry_still_says_what_this_test_assumes(kyo, project: Path) -> None:
    """A canary: if the shipped registry's `intake` set changes, the cases below
    are testing something other than what they claim."""
    kinds = _registry(project)
    with_intake = sorted(k for k, v in kinds.items() if "intake" in (v.get("accepts_from") or []))
    assert with_intake == sorted(DEFAULT_INTAKE_KINDS)


def test_kyo_new_files_the_kinds_that_accept_intake(kyo, write_file) -> None:
    # KYO-521: `decision` is filed directly with the evidence its policy requires.
    words = write_file("words.md", "The maintainer:\n\"yes\"\n")
    for kind in DEFAULT_INTAKE_KINDS:
        evidence = ["--evidence-file", words] if kind == "decision" else []
        result = kyo("new", kind, "--title", f"a {kind}", *evidence)
        assert result.code == 0, result
        assert "created" in result.out


#: KYO-228.c4: every `archetype: work` kind the record names, on a fresh `kyo init`
#: store, filed directly with `kyo new` -- no `ideas/` detour, and criteria/touches
#: authored at filing time exactly as `kyo promote` would take them, proving filing
#: directly loses no structure (the record's own claim).
KYO_228_WORK_KINDS = ("task", "chore", "investigation", "experiment", "proposal", "plan")


def test_every_kyo_228_work_kind_is_filed_directly_on_a_fresh_store(
    kyo, write_file
) -> None:
    for kind in KYO_228_WORK_KINDS:
        result = kyo(
            "new", kind, "--title", f"a {kind}",
            "--criteria-file", write_file(f"{kind}-c.txt", "it is done\n"),
            "--touches-file", write_file(f"{kind}-t.txt", "kyosei/kinds.yml\n"),
            "--json", "--full",
        )
        assert result.code == 0, result
        record = result.json["record"]
        assert record["kind"] == kind, (kind, result)
        assert record["status"] == "open", (kind, result)
        assert len(record["criteria"]) == 1, (kind, result)
        assert [t["path"] for t in record["touches"]] == ["kyosei/kinds.yml"], (kind, result)
    stored_kinds = sorted(r["kind"] for r in kyo("list", "--json").json["records"])
    assert stored_kinds == sorted(KYO_228_WORK_KINDS)


def test_kyo_new_refuses_a_kind_that_does_not_accept_intake(kyo, project: Path) -> None:
    """``decision`` was the one kind KYO-228 left refused in the shipped registry --
    ``archetype: knowledge``, and the record's reasoning was scoped to ``work`` -- until
    KYO-521; :func:`_decision_without_intake` puts it back."""
    _decision_without_intake(project)
    result = kyo("new", "decision", "--title", "use SQLite")
    assert result.code == EXIT_CONTRACT, result
    assert CONTRACT_KIND_NOT_DIRECTLY_FILEABLE in result.err
    assert "kind 'decision' does not accept direct filing" in result.err


def test_the_refusal_names_the_kinds_that_do_accept_direct_filing(kyo, project: Path) -> None:
    _decision_without_intake(project)
    result = kyo("new", "decision", "--title", "use SQLite")
    assert "kinds you can file directly:" in result.err
    for kind in DEFAULT_INTAKE_KINDS:
        assert kind in result.err


def test_the_refusal_points_at_promote_when_the_kind_accepts_an_idea(kyo, project: Path) -> None:
    """A kind that accepts ``idea`` but not ``intake`` — no default kind is that shape
    any more (KYO-228), so this reconstructs `task`'s pre-KYO-228 ``accepts_from`` to
    keep pinning that the refusal names `kyo promote` as the door still open."""
    _without_intake(project, "task")
    result = kyo("new", "task", "--title", "a task")
    assert result.code == EXIT_CONTRACT, result
    assert "kyo promote <idea-file> --kind task" in result.err


def test_the_refusal_says_so_when_promote_will_not_take_it_either(kyo, project: Path) -> None:
    """`decision.accepts_from` was [proposal, investigation] before KYO-521 — neither
    `intake` nor `idea`. Sending that caller to `kyo promote` would be a remedy that
    fails, so the message says which kinds promote *does* take and names the registry
    edit as the way through."""
    _decision_without_intake(project)
    result = kyo("new", "decision", "--title", "use SQLite")
    assert "`kyo promote --kind decision` will refuse it too" in result.err
    assert "kinds promotable from an idea:" in result.err
    assert "add 'intake' to its accepts_from" in result.err


def test_nothing_is_stored_when_the_kind_is_refused(kyo, project: Path) -> None:
    _decision_without_intake(project)
    assert kyo("new", "decision", "--title", "use SQLite").code == EXIT_CONTRACT
    assert kyo("list", "--json").json["records"] == []


def test_promote_and_new_agree_about_the_registry(kyo, project: Path) -> None:
    """The two write paths now consult the same file. `decision` is refused by
    both, and the messages name the field each one reads.

    KYO-68.c3 added the exit code to what "agree" means here. Both refusals are the
    same class of rejection — the registry's flow rules turning a kind away — and
    until KYO-68 they reported it with different exit codes.
    """
    _decision_without_intake(project)
    idea = project / "ideas" / "decide.md"
    idea.write_text("# Decide\n\nPick a store.\n", encoding="utf-8")
    promoted = kyo("promote", str(idea), "--kind", "decision")
    filed = kyo("new", "decision", "--title", "x")
    assert promoted.code == EXIT_CONTRACT, promoted
    assert filed.code == EXIT_CONTRACT, filed
    assert "does not accept promotion from idea" in promoted.err
    assert "does not accept direct filing" in filed.err


# --------------------------------------------------------------------- KYO-43.c2


def test_a_project_that_adds_intake_can_file_the_kind_immediately(kyo, project: Path) -> None:
    """The refusal reads this project's own kinds.yml, not a hardcoded list.

    This is also the answer to KYO-43.c3's escape-hatch question: the hatch is the
    registry, which is one reviewable line in a versioned file, rather than a flag
    that would restore the old behaviour in the first script that reached for it.

    ``task`` carries ``intake`` in the shipped registry since KYO-228, so this starts
    from :func:`_without_intake` to put it back in the refused state the test is about,
    then adds the edge back the same way a project actually would.
    """
    _without_intake(project, "task")
    assert kyo("new", "task", "--title", "a task").code == EXIT_CONTRACT

    kinds = _registry(project)
    kinds["task"]["accepts_from"] = [*kinds["task"]["accepts_from"], "intake"]
    _write_registry(project, kinds)

    result = kyo("new", "task", "--title", "a task", "--json")
    assert result.code == 0, result
    assert result.json["record"]["kind"] == "task"


def test_removing_intake_from_a_kind_immediately_refuses_it(kyo, project: Path) -> None:
    """The other direction, which is what proves the check is not a hardcoded
    allow-list of `bug`/`evidence` that happens to match the default registry."""
    assert kyo("new", "bug", "--title", "a bug").code == 0

    kinds = _registry(project)
    kinds["bug"]["accepts_from"] = [
        s for s in kinds["bug"]["accepts_from"] if s != "intake"
    ]
    _write_registry(project, kinds)

    result = kyo("new", "bug", "--title", "another bug")
    assert result.code == EXIT_CONTRACT
    assert CONTRACT_KIND_NOT_DIRECTLY_FILEABLE in result.err


def test_a_project_defined_kind_with_intake_is_fileable(kyo, project: Path) -> None:
    """A kind that exists in no default registry anywhere — nothing about this
    check can be keyed to names Kyosei ships."""
    kinds = _registry(project)
    kinds["sighting"] = {
        "archetype": "knowledge",
        "summary": "something a human noticed",
        "accepts_from": ["intake"],
        "produces": [],
        "policy": {"amendable": True, "swept": False, "counted": False},
    }
    _write_registry(project, kinds)

    result = kyo("new", "sighting", "--title", "saw it happen", "--json")
    assert result.code == 0, result
    assert result.json["record"]["kind"] == "sighting"


# --------------------------------------------------------------------- KYO-43.c3


def test_kyo_new_force_does_not_skip_the_flow_rule(kyo) -> None:
    """KYO-43.c3, decided: no escape hatch from the flow rule on the command.

    The argument for one was that this repo has legitimately needed to file a
    `decision` without a preceding `proposal`. The argument against -- the one that
    won -- is that the flow rule is the thing being protected, and a flag that skips
    it restores today's behaviour by default in any script that reaches for it,
    silently and without leaving a trace in the registry that the project's own
    rule was bypassed. A project that wants a kind filed directly says so in
    `kinds.yml`, where the change is reviewable and versioned
    (`test_a_project_that_adds_intake_can_file_the_kind_immediately`).

    KYO-538 gave `kyo new` a `--force` that means one other thing only -- file a
    record identical to one filed within the hour -- so this test now holds the
    decision where it lives: `--force` still does not file a kind the registry does
    not let in directly (and, in tests/test_kyo_uniform_receipts.py, is not an
    unknown flag any more).
    """
    refused = kyo("new", "reminder", "--title", "a reminder", "--force")  # accepts intake
    assert refused.code == 0, refused
    assert kyo("new", "decision", "--title", "a decision", "--force").code == EXIT_CONTRACT


# =========================================================== KYO-64.c1/c2/c3
#
# The refusals above were written under KYO-43, when the kind-to-kind edge had no
# implementation at all and "add 'intake' to the registry" genuinely was the only
# way through. KYO-50 built `kyo spawn` and did not revisit them, so a caller
# refused by `kyo new decision` was told the two edges that will not work and then
# offered a registry edit — steering them to weaken their own rule while the
# transition `decision.accepts_from` had described all along was one command away.
#
# The hard half is the conditional (KYO-64.c3): the line must never be printed as a
# dead suggestion, so both directions are asserted here and the "absent" cases are
# what stop it from silently becoming unconditional.

REGISTRY_EDIT_LINE = "add 'intake' to its accepts_from"


def _a_live_proposal(kyo) -> str:
    """A `proposal` record — `decision.accepts_from` names it, so this is exactly
    what makes `kyo spawn ... --kind decision` a real remedy in this store."""
    return new_record(kyo, kind="proposal", title="Pick a store", criteria=["a store is chosen"])


def test_the_refusal_names_spawn_when_a_live_source_exists(kyo, project: Path) -> None:
    """KYO-64.c1, the positive direction: `decision.accepts_from` is
    [proposal, investigation] and a proposal exists, so the message names the exact
    command — with a real id in it, which it could not do if nothing were there."""
    _decision_without_intake(project)
    source = _a_live_proposal(kyo)
    result = kyo("new", "decision", "--title", "use SQLite")
    assert result.code == EXIT_CONTRACT, result
    assert f"`kyo spawn {source} --kind decision" in result.err, result
    assert f"{source} (a proposal)" in result.err, result


def test_the_spawn_line_precedes_the_registry_edit(kyo, project: Path) -> None:
    """Order carries the argument KYO-43 made and this record sharpened: follow the
    rule first, change the rule second. A spawn line printed *after* "edit
    kinds.yml" would still leave the registry edit as the first thing read."""
    _decision_without_intake(project)
    _a_live_proposal(kyo)
    err = kyo("new", "decision", "--title", "use SQLite").err
    assert err.index("kyo spawn") < err.index(REGISTRY_EDIT_LINE), err


def test_the_refusal_names_no_runnable_spawn_when_no_source_record_exists(
    kyo, project: Path
) -> None:
    """KYO-64.c1's negative direction, corrected by KYO-68.c1.

    KYO-64 dropped the whole line when the store held nothing to spawn from, on the
    rule that naming a remedy which fails is worse than naming none. The rule is
    right; the scope was too wide. *Is `kyo spawn` a way into a `decision`* is a fact
    about the registry and is true of an empty store; only the **id** depends on what
    the store holds. Dropping the line answered the second question by deleting the
    first, so an empty store silently omitted a working verb — the exact defect
    KYO-68 is the general form of, in the message KYO-64 had just fixed.

    So: the verb is named, the source kinds are named, and the command carries a
    ``<source-id>`` placeholder plus the precondition — no real id, nothing that
    reads as runnable, which is what KYO-64's rule was actually protecting.
    """
    _decision_without_intake(project)
    result = kyo("new", "decision", "--title", "use SQLite")
    assert result.code == EXIT_CONTRACT, result
    assert "`kyo spawn <source-id> --kind decision" in result.err, result
    assert "'proposal' or 'investigation'" in result.err, result
    assert "no id to put there" in result.err, result
    assert REGISTRY_EDIT_LINE in result.err, result


def test_the_refusal_omits_spawn_when_the_kind_has_no_kind_to_kind_edge(kyo, project: Path) -> None:
    """`chore.accepts_from` is [idea] (pre-KYO-228 shape, reconstructed here since
    `chore` now also carries `intake`) — no kind token at all, so no `kyo spawn` could
    ever create one, however many records the store holds."""
    _without_intake(project, "chore")
    _a_live_proposal(kyo)
    result = kyo("new", "chore", "--title", "tidy the exports")
    assert result.code == EXIT_CONTRACT, result
    assert "kyo spawn" not in result.err, result
    assert "kyo promote <idea-file> --kind chore" in result.err, result


def test_the_refusal_stops_naming_a_retired_source_by_id(kyo, project: Path) -> None:
    """`kyo spawn` refuses a source in a terminal status (its own
    ``spawn-source-is-retired`` contract), so a refusal that named a wont-done
    proposal would be recommending a command that is about to refuse in turn.

    KYO-68: the *id* goes, not the verb. The registry edge is untouched by a source
    being retired, so the message falls back to the placeholder form — same rule as
    an empty store, and the assertion that matters is that no retired id is offered
    as something to run.
    """
    _decision_without_intake(project)
    source = _a_live_proposal(kyo)
    assert f"`kyo spawn {source}" in kyo("new", "decision", "--title", "x").err

    assert kyo("wont-do", source, "--why", "superseded").code == 0

    result = kyo("new", "decision", "--title", "x")
    assert result.code == EXIT_CONTRACT, result
    assert source not in result.err, result
    assert "`kyo spawn <source-id> --kind decision" in result.err, result


def test_promotes_refusal_also_names_spawn_and_the_doors_it_never_named(kyo, project: Path) -> None:
    """KYO-64.c2: `kyo promote`'s refusal was checked for the same omission and had
    it — worse, in fact. It was one line that named the failed edge and stopped: no
    spawn, no `kyo new`, not even the registry edit `kyo new`'s refusal offered.
    Now it collects every open door in one message (law 6), same as its sibling."""
    source = _a_live_proposal(kyo)
    idea = project / "ideas" / "decide.md"
    idea.write_text("# Decide\n\nPick a store.\n", encoding="utf-8")

    result = kyo("promote", str(idea), "--kind", "decision")
    assert result.code == EXIT_CONTRACT, result  # KYO-68.c3: was EXIT_ERROR
    assert "does not accept promotion from idea" in result.err
    assert f"`kyo spawn {source} --kind decision" in result.err, result
    assert "kinds promotable from an idea:" in result.err
    assert "add 'idea' to its accepts_from" in result.err


def test_promotes_refusal_names_no_runnable_spawn_with_nothing_to_spawn_from(
    kyo, project: Path
) -> None:
    """The same conditional, on the same helper, from the other command — and so the
    same KYO-68 correction: the verb and its source kinds survive an empty store, the
    id does not."""
    idea = project / "ideas" / "decide.md"
    idea.write_text("# Decide\n\nPick a store.\n", encoding="utf-8")

    result = kyo("promote", str(idea), "--kind", "decision")
    assert result.code == EXIT_CONTRACT, result  # KYO-68.c3: was EXIT_ERROR
    assert "`kyo spawn <source-id> --kind decision" in result.err, result
    assert "no id to put there" in result.err, result
