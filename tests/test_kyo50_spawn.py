"""KYO-50: `kyo spawn` — the kind-to-kind edge, which nothing implemented.

The registry defines three sources an ``accepts_from`` entry may name. Two had a
command: ``intake`` -> `kyo new` (KYO-43), ``idea`` -> `kyo promote`. The third —
another kind — had none, so ``bug -> task``, ``investigation -> evidence`` and
``proposal -> decision`` were transitions `kyo map` drew and `kyo doctor` validated
the symmetry of, and nothing could walk.

These tests cover the command's behaviour; the registry lookup it walks is tested in
``tests/test_kinds.py`` (`spawn_targets`), and the edge it writes in
``tests/test_links.py``. The three questions KYO-50.c2 left open are each pinned by a
test here, so a later "simplification" that copies the source's body, settles the
source, or makes the edge one-shot fails loudly rather than quietly changing a
decision that was argued.
"""

from __future__ import annotations

import yaml

from kyosei import kinds
from kyosei.contracts import (
    CONTRACT_KIND_NOT_DIRECTLY_FILEABLE,
    CONTRACT_KIND_NOT_SPAWNABLE_FROM,
    CONTRACT_SPAWN_SOURCE_IS_RETIRED,
)

from .conftest import kinds_yml, new_record

TOUCHES = "kyosei/config.yml\n"


def _spawn(kyo, source: str, kind: str, *, title: str, write_file, **flags):
    args = ["spawn", source, "--kind", kind, "--title", title, "--json"]
    for flag, content in flags.items():
        if content is None:  # a bare switch such as --force (KYO-538)
            args.append(f"--{flag.replace('_', '-')}")
            continue
        args += [f"--{flag.replace('_', '-')}", write_file(f"{flag}-{kind}.txt", content)]
    return kyo(*args)


def _work_spawn(kyo, source: str, kind: str, write_file, *, title: str = "The next step"):
    """Spawn an archetype-work target, which needs the same ceremony `kyo promote`
    requires of one: criteria and a file scope."""
    return _spawn(
        kyo,
        source,
        kind,
        title=title,
        write_file=write_file,
        criteria_file="it does the thing\n",
        touches_file=TOUCHES,
    )


# ---------------------------------------------------------------------- KYO-50.c1


def test_spawn_creates_the_target_kind_from_the_source(kyo, write_file) -> None:
    """c1: a command that creates a record of kind Y from an existing record of
    kind X. ``bug -> task`` is the edge the shipped registry has carried since
    milestone 2 with nothing able to traverse it."""
    bug = new_record(kyo, kind="bug", title="Widget explodes", criteria=["it stops exploding"])
    result = _work_spawn(kyo, bug, "task", write_file, title="Stop the widget exploding")
    assert result.code == 0, result

    spawned = result.json["record"]
    assert spawned["kind"] == "task"
    assert spawned["status"] == "open"
    assert spawned["title"] == "Stop the widget exploding"
    # KYO-427: a write's --json is a receipt; the criteria are read where records are read.
    shown = kyo("show", spawned["id"], "--json").json
    assert [c["state"] for c in shown["criteria"]] == ["unmet-unbuilt"]
    assert [t["path"] for t in shown["touches"]] == ["kyosei/config.yml"]
    assert result.json["link"]["type"] == "produced-by"


def test_spawn_refuses_a_source_kind_the_target_does_not_accept(kyo, write_file) -> None:
    """c1's other half: the refusal. ``proposal.accepts_from`` is ``[intake, idea, reminder]``
    in the shipped registry (KYO-228 added ``intake``, KYO-497 the kind KYO-521 renamed
    ``reminder``), so a bug may not
    become one — and the refusal is a named contract (exit 2), exactly as `kyo new`'s intake
    refusal is."""
    bug = new_record(kyo, kind="bug", title="Widget explodes")
    result = _work_spawn(kyo, bug, "proposal", write_file)
    assert result.code == 2, result
    assert CONTRACT_KIND_NOT_SPAWNABLE_FROM in result.err
    assert "its accepts_from is [intake, idea, reminder]" in result.err
    # Law 6: the caller is told every door that IS open, not just the closed one.
    assert "can spawn: evidence, investigation, task" in result.err
    assert "kyo promote <idea-file> --kind proposal" in result.err
    assert kyo("list", "--kind", "proposal", "--json").json["records"] == []


def test_the_registry_edit_the_refusal_names_makes_the_spawn_legal(kyo, write_file) -> None:
    """KYO-5's rule: an error that names a remedy owes a test proving the remedy
    clears it. The remedy here is deliberately the registry rather than a
    ``--force`` — one reviewable line in a versioned file (KYO-43's precedent) —
    and the message names both halves, because the edges are validated symmetric.
    """
    bug = new_record(kyo, kind="bug", title="Widget explodes")
    assert _work_spawn(kyo, bug, "proposal", write_file).code == 2

    path = kinds_yml(kyo.project)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["kinds"]["proposal"]["accepts_from"].append("bug")
    raw["kinds"]["bug"]["produces"].append("proposal")
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    result = _work_spawn(kyo, bug, "proposal", write_file)
    assert result.code == 0, result
    assert result.json["record"]["kind"] == "proposal"


def test_only_half_the_registry_edit_is_refused_at_load(kyo, write_file) -> None:
    """The remedy names both directions for a reason: the registry validates the
    symmetry of every kind-to-kind edge at load time, so a one-sided edit fails the
    next command outright instead of producing a half-real transition."""
    bug = new_record(kyo, kind="bug", title="Widget explodes")
    path = kinds_yml(kyo.project)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["kinds"]["proposal"]["accepts_from"].append("bug")
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    result = _work_spawn(kyo, bug, "proposal", write_file)
    assert result.code != 0
    assert "edges must be symmetric" in result.err


# ---------------------------------------------------------------------- KYO-50.c2


def test_the_source_s_text_is_referenced_never_copied(kyo, write_file) -> None:
    """c2, question 1. `kyo promote` copies the whole idea body because promotion
    *consumes* the idea (the file is stamped and stops being a live parallel copy).
    A spawn source stays live and editable, so a copy would be two texts that drift
    — law 3. The target carries a provenance stub and the ``produced-by`` edge."""
    investigation = new_record(kyo, kind="investigation", title="Why is it slow?")
    body = "The cache is cold on every first request, measured over 200 runs."
    assert kyo(
        "edit", investigation, "--description-file", write_file("src.md", body)
    ).code == 0

    result = _spawn(
        kyo, investigation, "evidence", title="Cold-cache timings", write_file=write_file
    )
    assert result.code == 0, result
    description = result.json["record"]["description"]
    assert body not in description
    assert f"Spawned from: {investigation}" in description
    assert "Why is it slow?" in description  # referenced by title, so the stub reads


def test_a_description_file_carries_the_new_record_s_own_prose(kyo, write_file) -> None:
    """The corollary: what the new record should say is authored, through a file
    (law 5) — not inherited from a record that was about something else."""
    investigation = new_record(kyo, kind="investigation", title="Why is it slow?")
    result = _spawn(
        kyo,
        investigation,
        "evidence",
        title="Cold-cache timings",
        write_file=write_file,
        description_file="p50 rose from 40ms to 900ms on a cold cache.\n",
    )
    assert result.code == 0, result
    description = result.json["record"]["description"]
    assert "p50 rose from 40ms to 900ms" in description
    assert f"Spawned from: {investigation}" in description


def test_spawning_does_not_change_the_source_s_status(kyo, write_file) -> None:
    """c2, question 2. A status is a claim about the work and `check_transition` is
    what licenses it; a spawn that moved a source to ``done`` would either bypass
    those contracts or fail inside a command asked only to create a record. Said
    out loud in the output, on the `kyo gate` precedent, because "the source is now
    settled" is exactly what a spawn invites a reader to assume."""
    proposal = new_record(
        kyo, kind="proposal", title="Adopt the new cache", criteria=["cites two alternatives"]
    )
    assert kyo("move", proposal, "working").code == 0

    result = _spawn(
        kyo, proposal, "decision", title="We adopt the new cache", write_file=write_file
    )
    assert result.code == 0, result
    assert result.json["source"]["status"] == "working"
    assert kyo("show", proposal, "--json").json["status"] == "working"

    human = kyo(
        "spawn", proposal, "--kind", "decision", "--title", "And we keep the old one warm"
    )
    assert human.code == 0, human
    assert f"{proposal} is unchanged at working: spawning is not a status change" in human.out
    assert "`kyo move`" in human.out


def test_a_knowledge_source_is_told_it_has_no_status_flow(kyo, write_file) -> None:
    """The same line, honest about the archetype: half the registry's kind-to-kind
    edges start at a ``knowledge`` kind, which has no status to advance at all — so
    "settle the source on spawn" could never have been a uniform rule."""
    kinds_path = kinds_yml(kyo.project)
    raw = yaml.safe_load(kinds_path.read_text(encoding="utf-8"))
    raw["kinds"]["evidence"]["produces"] = ["task"]
    raw["kinds"]["task"]["accepts_from"].append("evidence")
    kinds_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    evidence = new_record(kyo, kind="evidence", title="Cold-cache timings")
    result = kyo(
        "spawn", evidence, "--kind", "task",
        "--title", "Warm the cache on boot",
        "--criteria-file", write_file("c.txt", "boot warms the cache\n"),
        "--touches-file", write_file("t.txt", TOUCHES),
    )
    assert result.code == 0, result
    assert f"{evidence} is unchanged at current" in result.out
    assert "no status flow to advance" in result.out
    assert "`kyo move`" not in result.out


def test_the_edge_is_repeatable(kyo, write_file) -> None:
    """c2, question 3. One investigation may produce five evidence records. Refusing
    the second would push the caller to `kyo new`, which is the "add ``intake`` to
    every unreachable kind" flattening this record was filed to stop."""
    investigation = new_record(kyo, kind="investigation", title="Why is it slow?")
    first = _spawn(
        kyo, investigation, "evidence", title="Cold-cache timings", write_file=write_file
    )
    second = _spawn(
        kyo, investigation, "evidence", title="Warm-cache timings", write_file=write_file
    )
    assert first.code == 0 and second.code == 0, (first, second)
    assert first.json["record"]["id"] != second.json["record"]["id"]

    incoming = [
        link
        for link in kyo("show", investigation, "--json").json["links"]
        if link["type"] == "produced-by"
    ]
    assert sorted(link["other"] for link in incoming) == sorted(
        [first.json["record"]["id"], second.json["record"]["id"]]
    )


def test_a_repeated_spawn_is_warned_about_never_refused(kyo, write_file) -> None:
    """What repeatability costs is the ability to notice a re-spawn nobody meant, so
    the creation-time duplicate check `kyo new`/`kyo promote` run applies here too.
    Charter: flags, never enforces — a warning on stdout and exit 0."""
    investigation = new_record(kyo, kind="investigation", title="Why is it slow?")
    title = "Cold-cache timings"
    assert _spawn(
        kyo, investigation, "evidence", title=title, write_file=write_file
    ).code == 0
    # KYO-538: an IDENTICAL record filed within the hour is refused (the incident
    # that record came from); `--force` is the repeat nobody has to re-argue, and
    # what it files is still warned about, never silently.
    refused = _spawn(kyo, investigation, "evidence", title=title, write_file=write_file)
    assert refused.code == 1 and "--force" in refused.err, refused
    again = _spawn(
        kyo, investigation, "evidence", title=title, write_file=write_file, force=None
    )
    assert again.code == 0, again
    assert [w["kind"] for w in again.json["duplicate_warnings"]] == ["title"]


def test_spawn_refuses_a_retired_source_and_names_a_live_one(kyo, write_file) -> None:
    """A retired record produces nothing further. Nothing is wrong with the registry
    edge here, so the refusal points at the target's other accepted sources rather
    than at ``kinds.yml`` — and that named remedy is exercised, per KYO-5."""
    proposal = new_record(kyo, kind="proposal", title="Adopt the new cache")
    assert kyo("wont-do", proposal, "--why", "superseded by the vendor's fix").code == 0

    refused = _spawn(
        kyo, proposal, "decision", title="We adopt the new cache", write_file=write_file
    )
    assert refused.code == 2, refused
    assert CONTRACT_SPAWN_SOURCE_IS_RETIRED in refused.err
    # KYO-521 added `intake` and `reminder` to `decision`.
    assert "other sources 'decision' accepts: [intake, investigation, reminder]" in refused.err

    investigation = new_record(kyo, kind="investigation", title="Which cache?")
    allowed = _spawn(
        kyo, investigation, "decision", title="We adopt the new cache", write_file=write_file
    )
    assert allowed.code == 0, allowed


def test_a_work_target_needs_criteria_and_a_scope(kyo, write_file) -> None:
    """Ceremony matches `kyo promote` rather than `kyo new`: spawning is a judgment
    about what to do next, which is the moment law 13 says structure is worth
    authoring. Two conventions in this codebase, not a third."""
    bug = new_record(kyo, kind="bug", title="Widget explodes")
    bare = kyo("spawn", bug, "--kind", "task", "--title", "Fix it")
    assert bare.code != 0
    assert "policy.requires_criteria: true; pass --criteria-file" in bare.err

    no_scope = kyo(
        "spawn", bug, "--kind", "task", "--title", "Fix it",
        "--criteria-file", write_file("c.txt", "it stops exploding\n"),
    )
    assert no_scope.code != 0
    assert "pass --touches-file" in no_scope.err

    assert _work_spawn(kyo, bug, "task", write_file).code == 0


# ---------------------------------------------------------------------- KYO-50.c3


def test_a_decision_is_reachable_from_a_proposal_without_intake(kyo, write_file) -> None:
    """c3: ``decision.accepts_from`` was ``[proposal, investigation]`` in the shipped
    registry and carried no ``intake``. Before this command that made `decision`
    creatable by nothing at all — the discovery that produced this record. Both
    declared sources are checked, because a fix that only reached one of them would
    leave the other still undrawable. KYO-521 gave the shipped `decision` ``intake``
    (with ``--evidence-file``) and ``reminder``; this reconstructs the earlier entry,
    because the property pinned is that spawn reaches a kind `kyo new` cannot."""
    path = kinds_yml(kyo.project)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["kinds"]["decision"]["accepts_from"] = ["proposal", "investigation"]
    raw["kinds"]["reminder"]["produces"].remove("decision")
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    refused = kyo("new", "decision", "--title", "We adopt the new cache")
    assert refused.code == 2
    assert CONTRACT_KIND_NOT_DIRECTLY_FILEABLE in refused.err

    proposal = new_record(kyo, kind="proposal", title="Adopt the new cache")
    from_proposal = _spawn(
        kyo, proposal, "decision", title="We adopt the new cache", write_file=write_file
    )
    assert from_proposal.code == 0, from_proposal
    assert from_proposal.json["record"]["kind"] == "decision"

    investigation = new_record(kyo, kind="investigation", title="Which cache?")
    from_investigation = _spawn(
        kyo, investigation, "decision", title="We keep the old one warm", write_file=write_file
    )
    assert from_investigation.code == 0, from_investigation


# ---------------------------------------------------------------------- KYO-50.c4


def test_the_kinds_module_names_its_acting_consumer(kyo) -> None:
    """c4: law 2 asks for a *consumer*, and these two fields had one — `kyo map`,
    which renders them. A field whose only consumer draws it governs a picture. The
    module docstring is where this project states each field's consumer, so it must
    name the command that acts on these, not only the one that draws them."""
    docstring = kinds.__doc__ or ""
    assert "cmd_spawn" in docstring
    assert "cmd_views.cmd_map" in docstring  # still true; now no longer the whole list
