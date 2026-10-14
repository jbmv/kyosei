"""``kyo unlink``: a typed edge stops being permanent.

`kyo link` had no inverse, and the first consumer project outside this repo filed
three wrong edges in one day — one annotated with a note that existed only to apologise
for an edge.

The third occurrence is the one that decides the design: a `blocks` edge filed
backwards could not be corrected, because adding the right-way edge would close a
cycle and law 6 refuses cycles at write time. A mis-directed edge was not merely
permanent, it actively blocked its own correction. Cycle refusal assumes edges are
right on the first try, which the absence of an inverse guaranteed they would not
be — ``test_a_backwards_blocks_edge_can_be_turned_around`` is that whole story in
one function.
"""

from __future__ import annotations

from pathlib import Path

from .conftest import event_lines, new_record


def _link_ids(kyo, record_id: str) -> list[str]:
    return [link["id"] for link in kyo("show", record_id, "--json").json["links"]]


def test_an_edge_can_be_retracted(kyo) -> None:
    first = new_record(kyo, kind="bug", title="One")
    second = new_record(kyo, kind="bug", title="Two")
    link_id = kyo("link", first, "blocks", second, "--json").json["link"]["id"]
    assert _link_ids(kyo, first) == [link_id]

    result = kyo("unlink", link_id, "--why", "filed against the wrong record")
    assert result.code == 0, result
    assert _link_ids(kyo, first) == []
    assert _link_ids(kyo, second) == []


def test_a_backwards_blocks_edge_can_be_turned_around(kyo) -> None:
    """The third occurrence, end to end: the wrong edge blocked its own
    correction until there was a way to withdraw it."""
    decision = new_record(kyo, kind="bug", title="The decision")
    defect = new_record(kyo, kind="bug", title="The defect")
    wrong = kyo("link", defect, "blocks", decision, "--json").json["link"]["id"]

    blocked = kyo("link", decision, "blocks", defect)
    assert blocked.code != 0
    assert "refusing cycle" in blocked.err

    assert kyo("unlink", wrong, "--why", "backwards: the decision constrains the bug").code == 0
    fixed = kyo("link", decision, "blocks", defect)
    assert fixed.code == 0, fixed


def test_retracting_needs_a_reason(kyo) -> None:
    """The ungate precedent: withdrawing a claim someone deliberately filed is
    worthless without why."""
    first = new_record(kyo, kind="bug", title="One")
    second = new_record(kyo, kind="bug", title="Two")
    link_id = kyo("link", first, "blocks", second, "--json").json["link"]["id"]

    result = kyo("unlink", link_id)
    assert result.code != 0
    assert "--why" in result.err
    assert _link_ids(kyo, first) == [link_id], "a refused unlink must not remove the edge"

    assert kyo("unlink", link_id, "--why", "duplicate of another edge").code == 0


def test_the_retracted_edge_and_its_reason_stay_in_the_ledger(kyo, project: Path) -> None:
    """Why deleting the row is safe: this project keeps history in the events
    ledger, not in tombstoned rows that every reader would have to filter."""
    first = new_record(kyo, kind="bug", title="One")
    second = new_record(kyo, kind="bug", title="Two")
    link_id = kyo("link", first, "needs-same-change-as", second, "--json").json["link"]["id"]
    kyo("unlink", link_id, "--why", "they do not need the same change after all")

    events = event_lines(project)
    retraction = [e for e in events if e["command"].startswith(f"unlink {link_id}")]
    assert retraction, "the retraction is not in the ledger"
    assert "they do not need the same change after all" in retraction[0]["command"]
    # KYO-418: the prior document, whichever line format carried it.
    from kyosei.events_ledger import event_before_document

    prior = event_before_document(retraction[0]) or {}
    assert link_id in [link["id"] for link in prior["links"]]
    assert link_id not in [link["id"] for link in retraction[0]["after"]["links"]]


def test_unlinking_an_unknown_id_fails(kyo) -> None:
    result = kyo("unlink", "TST-l99", "--why", "nope")
    assert result.code != 0
    assert "no link" in result.err


def test_the_same_edge_can_be_filed_again_after_retraction(kyo) -> None:
    """Retraction is not a ban — `link` refuses a duplicate of a *live* edge, and
    a retracted one is not live."""
    first = new_record(kyo, kind="bug", title="One")
    second = new_record(kyo, kind="bug", title="Two")
    link_id = kyo("link", first, "blocks", second, "--json").json["link"]["id"]
    kyo("unlink", link_id, "--why", "premature")

    again = kyo("link", first, "blocks", second, "--json")
    assert again.code == 0, again
    assert again.json["link"]["id"] != link_id, "a retracted edge's id is never reissued (law 4)"


def test_the_store_survives_a_rebuild_after_a_retraction(kyo, project: Path) -> None:
    first = new_record(kyo, kind="bug", title="One")
    second = new_record(kyo, kind="bug", title="Two")
    link_id = kyo("link", first, "blocks", second, "--json").json["link"]["id"]
    kyo("unlink", link_id, "--why", "wrong direction")

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert _link_ids(kyo, first) == []
    assert kyo("doctor").code == 0
