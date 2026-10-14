"""KYO-130: a gate's tags are correctable in place, without a false clearing.

The gap, reported by a consumer adopting KYO-102's gate-tag registry: ``--tag`` was
accepted only at gate creation, so every gate that predated the registry — which is
every gate in every store, because the dimension is new — could be classified only by
`ungate` + `gate`. That route costs a new gate id, an ``ungate`` event on a gate that
never cleared, and (for ``holding-for``) a KYO-59 ``gate-cleared-stale-criteria`` flag
raised against a premise that is false. Each classification so made writes a false
clearing into a git-canonical ledger, which a store with many gates cannot reasonably
accept.

Every test here is written against the distinction that decides the shape: a tag is a
CLASSIFICATION, not a claim about the gate's condition — the same line the store already
draws between a record's ``touches`` (corrected in place) and its criterion text
(immutable from first verdict). So the tests assert what retagging leaves alone at least
as hard as they assert what it changes.
"""

from __future__ import annotations

import json
from pathlib import Path

from .conftest import event_lines, new_record

TWO_TAGS = """\
gate-tags:
  ui-review:
    summary: A person has to look at a rendered interface and judge it.
  deploy:
    summary: Waiting on a release to reach an environment where this can be seen.
"""


def gate_tags_yml(project: Path) -> Path:
    return project / "kyosei" / "gate-tags.yml"


def write_gate_tags(project: Path, text: str = TWO_TAGS) -> Path:
    path = gate_tags_yml(project)
    path.write_text(text, encoding="utf-8")
    return path


def only_gate(kyo, record: str) -> dict:
    gates = kyo("show", record, "--json").json["gates"]
    assert len(gates) == 1, gates
    return gates[0]


def events(db) -> list[dict]:
    with db() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM events ORDER BY seq")]


# ------------------------------------------------------------------------ KYO-130.c1


def test_an_existing_gates_tags_change_without_clearing_it(kyo, project: Path) -> None:
    """c1 whole: the gate is named by its OWN id, and it is still open afterwards.

    The second half is the criterion's point. Re-tagging by re-creating also ends with
    the right tags in the store; what distinguishes this from that is that the gate
    holding the record is the same gate, still holding it.
    """
    write_gate_tags(project)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review")
    gate_id = only_gate(kyo, record)["id"]

    result = kyo("retag", gate_id, "--tag", "ui-review")
    assert result.code == 0, result

    gate = only_gate(kyo, record)
    assert gate["tags"] == ["ui-review"]
    assert gate["cleared_at"] is None, "retagging cleared the gate"


def test_the_tags_a_retag_stores_are_the_ones_it_echoes(kyo, project: Path) -> None:
    """Law 5, and the reason replacing rather than accumulating is safe to offer: the
    previous set is printed beside the stored one, so a caller who meant `--tag b` as
    "and b" can see that `a` is gone."""
    write_gate_tags(project)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review", "--tag", "deploy")
    gate_id = only_gate(kyo, record)["id"]

    result = kyo("retag", gate_id, "--tag", "ui-review")
    assert result.code == 0, result
    assert "stored tags: ui-review" in result.out
    assert "previous tags: deploy" in result.out
    assert only_gate(kyo, record)["tags"] == ["ui-review"]


def test_retagging_replaces_rather_than_accumulates(kyo, project: Path) -> None:
    write_gate_tags(project)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review", "--tag", "deploy")
    gate_id = only_gate(kyo, record)["id"]
    kyo("retag", gate_id, "--tag", "ui-review")
    assert only_gate(kyo, record)["tags"] == ["ui-review"]


def test_several_tags_are_stored_in_the_order_given(kyo, project: Path) -> None:
    write_gate_tags(project)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review")
    gate_id = only_gate(kyo, record)["id"]
    kyo("retag", gate_id, "--tag", "deploy", "--tag", "ui-review")
    assert only_gate(kyo, record)["tags"] == ["deploy", "ui-review"]


def test_a_repeated_tag_is_stored_once(kyo, project: Path) -> None:
    """Same normalization as creation's, because it is the same function."""
    write_gate_tags(project)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review")
    gate_id = only_gate(kyo, record)["id"]
    result = kyo("retag", gate_id, "--tag", "deploy", "--tag", "deploy")
    assert result.code == 0, result
    assert "stored tags: deploy" in result.out
    assert only_gate(kyo, record)["tags"] == ["deploy"]


def test_no_tags_clears_the_classification(kyo, project: Path) -> None:
    """A gate wrongly classified must be un-classifiable, or the first typo is
    permanent — which would put tags back on the criterion-text side of the line."""
    write_gate_tags(project)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review", "--tag", "deploy")
    gate_id = only_gate(kyo, record)["id"]

    result = kyo("retag", gate_id, "--no-tags")
    assert result.code == 0, result
    assert "stored tags: (none)" in result.out
    assert only_gate(kyo, record)["tags"] == []


def test_a_retag_must_say_which_of_the_two_it_means(kyo, project: Path) -> None:
    """An absent `--tag` cannot mean both "clear them" and "you forgot the flag", so
    neither flag is a usage error rather than a silent clearing."""
    write_gate_tags(project)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review", "--tag", "deploy")
    gate_id = only_gate(kyo, record)["id"]

    assert kyo("retag", gate_id).code != 0
    assert only_gate(kyo, record)["tags"] == ["deploy"], "a bare retag cleared the tags"
    assert kyo("retag", gate_id, "--tag", "ui-review", "--no-tags").code != 0
    assert only_gate(kyo, record)["tags"] == ["deploy"]


def test_retagging_an_unknown_gate_is_refused(kyo, project: Path) -> None:
    write_gate_tags(project)
    new_record(kyo, criteria=["a"])
    result = kyo("retag", "KYO-999.g1", "--tag", "deploy")
    assert result.code != 0
    assert "no gate" in result.err


def test_a_retagged_gate_answers_the_query_the_tags_exist_for(kyo, project: Path) -> None:
    """The dimension exists for queries such as "everything waiting on a human to
    review a UI element". A retag that did not reach `kyo list --gate-tag`
    would have classified nothing."""
    write_gate_tags(project)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review")
    gate_id = only_gate(kyo, record)["id"]
    assert kyo("list", "--gate-tag", "ui-review", "--json").json["records"] == []

    kyo("retag", gate_id, "--tag", "ui-review")
    listed = kyo("list", "--gate-tag", "ui-review", "--json").json["records"]
    assert [r["id"] for r in listed] == [record]


# ------------------------------------------------------------------------ KYO-130.c2


def test_retagging_leaves_id_payload_and_history_untouched(kyo, project: Path) -> None:
    """c2 in full, as a whole-object comparison rather than a field-by-field one: every
    key of the exported gate except `tags` must be equal before and after. Written this
    way so that a field added to gates later is covered by this test on the day it is
    added, without anyone remembering to come back here.
    """
    write_gate_tags(project)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "holding-for", "--do", "the v2 design decides what a stamp is")
    before = only_gate(kyo, record)

    assert kyo("retag", before["id"], "--tag", "ui-review").code == 0
    after = only_gate(kyo, record)

    assert set(before) == set(after)
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {"tags"}, f"retag changed more than the tags: {changed}"
    # Named individually as well, because these three are what the criterion says and a
    # set-difference assertion alone would pass if `id` and `do` both vanished.
    assert after["id"] == before["id"]
    assert after["do"] == "the v2 design decides what a stamp is"
    assert (after["created_at"], after["cleared_at"], after["cleared_why"]) == (
        before["created_at"],
        before["cleared_at"],
        before["cleared_why"],
    )


def test_retagging_creates_no_second_gate(kyo, project: Path) -> None:
    """The ungate/gate route's most visible cost was a new gate id. `only_gate` asserts
    the count, so a retag that appended a gate fails here."""
    write_gate_tags(project)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review")
    gate_id = only_gate(kyo, record)["id"]
    kyo("retag", gate_id, "--tag", "deploy")
    assert only_gate(kyo, record)["id"] == gate_id


def test_a_cleared_gate_is_retaggable_and_stays_cleared(kyo, project: Path) -> None:
    """Classifying history is bookkeeping, not a claim that the gate is open — and
    `kyo list --gate-tag` selects on open gates, so no query's answer changes. What must
    hold is that retagging does not resurrect it."""
    write_gate_tags(project)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review")
    gate_id = only_gate(kyo, record)["id"]
    kyo("ungate", gate_id, "--why", "the reviewer looked")
    cleared_at = only_gate(kyo, record)["cleared_at"]
    assert cleared_at is not None

    result = kyo("retag", gate_id, "--tag", "ui-review")
    assert result.code == 0, result
    gate = only_gate(kyo, record)
    assert gate["tags"] == ["ui-review"]
    assert gate["cleared_at"] == cleared_at, "retagging reopened or re-cleared the gate"


def test_retagging_to_the_same_tags_says_nothing_changed(kyo, project: Path) -> None:
    """Law 6: "ran and changed nothing" is a different statement from "did not run", and
    the caller who expected a change is the one who needs to hear it."""
    write_gate_tags(project)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review", "--tag", "deploy")
    gate_id = only_gate(kyo, record)["id"]
    result = kyo("retag", gate_id, "--tag", "deploy")
    assert result.code == 0, result
    assert "nothing changed" in result.out


# ------------------------------------------------------------------------ KYO-130.c3


def test_retagging_records_an_event_of_its_own(kyo, project: Path, db) -> None:
    """c3: the change carries provenance rather than appearing as an unexplained
    difference between two exports. Its own command string — neither a gating nor a
    clearing — and before/after documents that show the tags actually moving.
    """
    write_gate_tags(project)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review", "--tag", "deploy")
    gate_id = only_gate(kyo, record)["id"]
    before_count = len(events(db))

    assert kyo("retag", gate_id, "--tag", "ui-review").code == 0
    written = events(db)[before_count:]
    assert len(written) == 1, written
    event = written[0]

    assert event["command"] == f"retag {gate_id}"
    assert event["record_id"] == record
    tags_of = lambda doc: [  # noqa: E731
        g["tags"] for g in json.loads(doc)["gates"] if g["id"] == gate_id
    ]
    # KYO-418: the prior `gates` list is in `changed_json` on a row written from 25 on.
    assert event["before_json"] is None
    assert tags_of(event["changed_json"]) == [["deploy"]]
    assert tags_of(event["after_json"]) == [["ui-review"]]


def test_the_retag_event_reaches_the_exported_ledger(kyo, project: Path) -> None:
    """The events table is not the artifact consumers read: `_events.jsonl` is, and it
    is what makes the difference between two exports explicable at all."""
    write_gate_tags(project)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review")
    gate_id = only_gate(kyo, record)["id"]
    kyo("retag", gate_id, "--tag", "ui-review")

    commands = [event["command"] for event in event_lines(project)]
    assert f"retag {gate_id}" in commands


# ------------------------------------------------------------------------ KYO-130.c4


def test_an_undeclared_tag_is_refused_in_creations_exact_words(kyo, project: Path) -> None:
    """c4. Asserted as string EQUALITY against what `kyo gate` says, not as a pair of
    substring checks that both happen to mention the registry: the criterion is that the
    two refusals cannot drift, and only comparing them whole can fail when they do.
    """
    write_gate_tags(project)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review")
    gate_id = only_gate(kyo, record)["id"]

    at_creation = kyo("gate", record, "awaiting:human-review", "--tag", "ui-reveiw")
    at_retag = kyo("retag", gate_id, "--tag", "ui-reveiw")

    assert at_creation.code != 0 and at_retag.code != 0
    assert at_retag.err == at_creation.err
    assert "gate-tag 'ui-reveiw' is not declared" in at_retag.err
    assert str(gate_tags_yml(project)) in at_retag.err
    assert "declared gate-tags: deploy, ui-review" in at_retag.err


def test_a_refused_tag_stores_nothing_at_all(kyo, project: Path) -> None:
    """The creation path's rule, which retagging inherits by sharing it: a partly
    applied classification is worse than none. One bad tag in the list leaves the gate
    carrying exactly what it carried before — not the good tags with the bad one dropped,
    and not an empty set."""
    write_gate_tags(project)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review", "--tag", "deploy")
    gate_id = only_gate(kyo, record)["id"]

    assert kyo("retag", gate_id, "--tag", "ui-review", "--tag", "nope").code != 0
    assert only_gate(kyo, record)["tags"] == ["deploy"]


def test_the_remedy_the_refusal_names_clears_it_on_retag_too(kyo, project: Path) -> None:
    """The project's standing rule: an error naming a remedy ships with a test proving
    the remedy works — on this path, not only on the one it was written for."""
    write_gate_tags(project)
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review")
    gate_id = only_gate(kyo, record)["id"]
    assert kyo("retag", gate_id, "--tag", "security").code != 0

    write_gate_tags(
        project,
        TWO_TAGS + "  security:\n    summary: Waiting on a security review.\n",
    )
    result = kyo("retag", gate_id, "--tag", "security")
    assert result.code == 0, result
    assert only_gate(kyo, record)["tags"] == ["security"]


# ------------------------------------------------------------------------ KYO-130.c5


def test_a_gate_older_than_the_registry_is_classifiable_without_a_false_clearing(
    kyo, project: Path, db
) -> None:
    """c5, which is the reported case reproduced end to end.

    The setup is the consumer's exactly: a project with no `gate-tags.yml`, a
    `holding-for` gate filed against criteria that predate it, and then the registry
    adopted afterwards. The old route produced an `ungate` event on a gate that never
    cleared and a `gate-cleared-stale-criteria` flag from KYO-59 — a correct response to
    an incorrect premise. Both absences are asserted, plus the gate's identity, because
    "it got tagged" was never the part that was hard.
    """
    gate_tags_yml(project).unlink()
    record = new_record(kyo, criteria=["a", "b"])
    gated = kyo("gate", record, "holding-for", "--do", "the v2 design decides this")
    assert gated.code == 0, gated
    gate = only_gate(kyo, record)
    assert gate["tags"] == [], "the fixture gate was already classified"

    # The project adopts the dimension only now — which is the state every gate in
    # every pre-existing store is in.
    write_gate_tags(project)
    result = kyo("retag", gate["id"], "--tag", "ui-review")
    assert result.code == 0, result

    after = only_gate(kyo, record)
    assert after["tags"] == ["ui-review"]
    assert after["id"] == gate["id"], "a new gate id — the ungate/gate route's cost"
    assert after["cleared_at"] is None and after["cleared_why"] is None

    commands = [e["command"] for e in events(db)]
    assert not [c for c in commands if c.startswith("ungate")], (
        "retagging produced an ungate event on a gate that never cleared"
    )
    flags = kyo("show", record, "--json").json["flags"]
    assert [f for f in flags if f["flag_type"] == "gate-cleared-stale-criteria"] == [], (
        "retagging raised KYO-59's flag against a premise that is false"
    )
    assert flags == [], f"retagging raised a flag at all: {flags}"
