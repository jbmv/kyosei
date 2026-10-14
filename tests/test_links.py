"""Typed edges, and law 6's cycle refusal at write time."""

from __future__ import annotations

from kyosei import links
from kyosei.contracts import LINK_TYPES
from kyosei.render import MISSING_TITLE, edge_sentence

from .conftest import new_record


def test_every_link_type_is_accepted(kyo) -> None:
    src = new_record(kyo, title="source")
    dst = new_record(kyo, title="target")
    for edge in LINK_TYPES:
        assert kyo("link", src, edge, dst).code == 0, edge
    assert len(kyo("show", src, "--json").json["links"]) == len(LINK_TYPES)


def test_a_cycle_is_refused_at_write_time(kyo) -> None:
    first = new_record(kyo, title="one")
    second = new_record(kyo, title="two")
    assert kyo("link", first, "blocks", second).code == 0
    result = kyo("link", second, "blocks", first)
    assert result.code == 1
    assert "refusing cycle" in result.err
    assert f"{second} -> {first} -> {second}" in result.err
    links = kyo("show", second, "--json").json["links"]
    assert len(links) == 1
    link = links[0]
    assert link["id"] == "TST-L1"
    assert link["direction"] == "incoming"
    assert link["type"] == "blocks"
    assert link["other"] == first


def test_a_long_cycle_is_refused(kyo) -> None:
    ids = [new_record(kyo, title=f"r{n}") for n in range(3)]
    assert kyo("link", ids[0], "blocks", ids[1]).code == 0
    assert kyo("link", ids[1], "blocks", ids[2]).code == 0
    result = kyo("link", ids[2], "blocks", ids[0])
    assert result.code == 1
    assert "refusing cycle" in result.err


def test_non_dependency_edges_may_be_mutual(kyo) -> None:
    """``duplicates`` and ``same-question-as`` are symmetric by nature."""
    first = new_record(kyo, title="one")
    second = new_record(kyo, title="two")
    assert kyo("link", first, "duplicates", second).code == 0
    assert kyo("link", second, "duplicates", first).code == 0


def test_self_links_are_refused(kyo) -> None:
    record = new_record(kyo)
    result = kyo("link", record, "same-question-as", record)
    assert result.code == 1
    assert "self-link" in result.err


def test_duplicate_edges_are_refused(kyo) -> None:
    """Law 6: "success" must mean the thing happened, not "it was already there"."""
    first = new_record(kyo, title="one")
    second = new_record(kyo, title="two")
    assert kyo("link", first, "supersedes", second).code == 0
    result = kyo("link", first, "supersedes", second)
    assert result.code == 1
    assert "already exists" in result.err


def test_unknown_endpoints_fail_loudly(kyo) -> None:
    record = new_record(kyo)
    assert kyo("link", record, "blocks", "TST-404").code == 1
    assert kyo("link", "TST-404", "blocks", record).code == 1


def test_unknown_edge_type_lists_the_known_ones(kyo) -> None:
    first = new_record(kyo, title="one")
    second = new_record(kyo, title="two")
    result = kyo("link", first, "relates-to", second)
    assert result.code == 64
    assert "same-question-as" in result.err


def test_both_endpoints_see_the_edge(kyo) -> None:
    first = new_record(kyo, title="one")
    second = new_record(kyo, title="two")
    kyo("link", first, "blocks", second)
    outgoing = kyo("show", first, "--json").json["links"][0]
    incoming = kyo("show", second, "--json").json["links"][0]
    assert outgoing["direction"] == "outgoing" and outgoing["other"] == second
    assert incoming["direction"] == "incoming" and incoming["other"] == first
    assert outgoing["id"] == incoming["id"]


# ------------------------------------------------------------------------ KYO-47
#
# Four wrong edges were written in this project in a single day: two reversed, one
# with a semantically wrong edge type, and one whose *retraction* destroyed a
# correct edge because `unlink`'s output was read as confirming a creation. Every
# one of them was well-formed, accepted, and echoed faithfully as ids. The remedy
# is that a human is never shown an edge as ids alone.

BLOCKER = "kyo new files any kind directly, without a note file"
BLOCKED = "Decision: the seam, authorship, and the shape of v2"


def _ordered(text: str, *fragments: str) -> bool:
    """Do ``fragments`` appear in ``text``, all of them, in this order?"""
    at = -1
    for fragment in fragments:
        found = text.find(fragment, at + 1)
        if found <= at:
            return False
        at = found
    return True


def test_link_echoes_the_edge_as_a_directional_sentence_with_titles(kyo) -> None:
    """KYO-47.c1: the id echo confirms what was stored; the sentence says what it
    means, which is the half the operator got wrong four times."""
    blocker = new_record(kyo, title=BLOCKER)
    blocked = new_record(kyo, title=BLOCKED)
    result = kyo("link", blocker, "blocks", blocked)
    assert result.code == 0, result
    assert f"stored edge: {blocker} blocks {blocked}" in result.out
    assert _ordered(result.out, BLOCKER, "BLOCKS", BLOCKED), result.out


def test_the_echoed_sentence_names_the_source_first_whichever_way_it_points(kyo) -> None:
    """KYO-47.c3: the guard against a future refactor reversing the *rendering*.

    A renderer that printed the two titles in argument order by accident would pass
    the test above and still read backwards here, where the same two records are
    linked the other way round.
    """
    blocker = new_record(kyo, title=BLOCKER)
    blocked = new_record(kyo, title=BLOCKED)
    forwards = kyo("link", blocker, "blocks", blocked)
    assert _ordered(forwards.out, BLOCKER, "BLOCKS", BLOCKED), forwards.out

    backwards = kyo("link", blocked, "supersedes", blocker)
    assert _ordered(backwards.out, BLOCKED, "SUPERSEDES", BLOCKER), backwards.out
    assert not _ordered(backwards.out, BLOCKER, "SUPERSEDES", BLOCKED), backwards.out


def test_titles_in_the_echo_are_never_truncated(kyo) -> None:
    """The decision this record asked for: no truncation anywhere.

    A truncation short enough to fit two titles on one line cuts exactly the
    distinguishing tail — the two titles below share their first 46 characters,
    which is longer than any width two of them would fit in.
    """
    shared = "Decision: the store lives outside the code repo"
    first = new_record(kyo, title=f"{shared} by default, under ~/kyosei/<project>/")
    second = new_record(kyo, title=f"{shared} only when the consumer says so")
    result = kyo("link", first, "blocks", second)
    assert f"{shared} by default, under ~/kyosei/<project>/" in result.out
    assert f"{shared} only when the consumer says so" in result.out


def test_show_renders_every_edge_in_the_direction_it_means(kyo) -> None:
    """KYO-47.c2: `kyo show` renders the edge source-first on *both* endpoints.

    The rendering this replaces flipped an arrow around the record you were
    reading, so the blocked record's page read `KYO-39 <- blocks <- KYO-43` — the
    same two ids in the opposite order from the page next door.
    """
    blocker = new_record(kyo, title=BLOCKER)
    blocked = new_record(kyo, title=BLOCKED)
    kyo("link", blocker, "blocks", blocked)

    for record in (blocker, blocked):
        out = kyo("show", record).out
        section = out.split("links (1):")[1]
        assert _ordered(section, BLOCKER, "BLOCKS", BLOCKED), section


def test_a_missing_title_is_named_rather_than_left_blank() -> None:
    """A renderer handed an edge it has no title for says so.

    The title index is built from the store, so today every endpoint resolves;
    a blank where a title belongs would read as "this record has no title" rather
    than "this renderer could not find one", which is the failure mode this whole
    record is about.

    KYO-215: the second half of this test read `html_site._meta_links`, the static
    viewer's rendering of the same edge. That surface is retired; `edge_sentence`
    is now the only renderer of an edge and so the only one that can go blank.
    """
    assert MISSING_TITLE in "\n".join(edge_sentence("TST-1", None, "blocks", "TST-2", "two"))


def test_unlink_says_it_removed_the_edge_and_never_reuses_links_wording(kyo) -> None:
    """KYO-47.c4: the destructive command must not echo like the creating one.

    `unlink` printed `stored edge: KYO-50 blocks KYO-39` — `link`'s own phrasing
    for a creation — under which a correct edge was destroyed while the operator
    read the line as confirmation that the *wrong* one had been.
    """
    blocker = new_record(kyo, title=BLOCKER)
    blocked = new_record(kyo, title=BLOCKED)
    link_id = kyo("link", blocker, "blocks", blocked, "--json").json["link"]["id"]

    result = kyo("unlink", link_id, "--why", "backwards: the decision constrains the bug")
    assert result.code == 0, result
    assert "stored edge" not in result.out, result.out
    assert "REMOVED this edge" in result.out
    assert _ordered(result.out, BLOCKER, "BLOCKS", BLOCKED), result.out


def test_unlink_shows_the_edge_before_removing_anything(kyo) -> None:
    """KYO-47.c4: `KYO-Lnn` carries no human-readable content, so the refusal for a
    missing `--why` is the one moment the caller can be shown what they typed.

    No prompt: this CLI is driven by agents too, and an interactive confirmation
    would hang a dispatch.
    """
    blocker = new_record(kyo, title=BLOCKER)
    blocked = new_record(kyo, title=BLOCKED)
    link_id = kyo("link", blocker, "blocks", blocked, "--json").json["link"]["id"]

    refused = kyo("unlink", link_id)
    assert refused.code != 0
    assert "nothing has been removed" in refused.err
    assert _ordered(refused.err, BLOCKER, "BLOCKS", BLOCKED), refused.err
    assert [link["id"] for link in kyo("show", blocker, "--json").json["links"]] == [link_id]


def test_spawn_writes_the_same_produced_by_edge_split_does(kyo, write_file) -> None:
    """KYO-50.c1: the kind-to-kind edge reuses `kyo split`'s linking half rather
    than inventing a second convention — same edge type, same direction (successor
    ``produced-by`` original), so `kyo show`, the html link graph and `kyo doctor`'s
    backlink check all read one shape.
    """
    investigation = new_record(kyo, kind="investigation", title="Why is it slow?")
    spawned = kyo(
        "spawn", investigation, "--kind", "evidence", "--title", "Cold-cache timings", "--json"
    )
    assert spawned.code == 0, spawned
    new_id = spawned.json["record"]["id"]

    outgoing = kyo("show", new_id, "--json").json["links"]
    assert len(outgoing) == 1
    assert outgoing[0]["type"] == "produced-by"
    assert outgoing[0]["direction"] == "outgoing"
    assert outgoing[0]["other"] == investigation

    incoming = kyo("show", investigation, "--json").json["links"]
    assert [(link["direction"], link["other"]) for link in incoming] == [("incoming", new_id)]


# ------------------------------------------------------------------------ KYO-350
#
# A consumer got a `blocks` edge's direction backwards and found out only on
# re-reading; they had piped `kyo link`'s output through `tail -1`, so the one line
# they actually saw was the id pair, not a statement of what the edge meant. The
# fix is a sentence of consequence — "APP-58 cannot finish until APP-57 does"
# rather than the pair — printed as the LAST line, so a `tail -1` reader lands on it.


#: KYO-570.c6: the `blocks` sentence says what is enforced -- the destination is not
#: offered or dispatched until the source settles -- not "cannot finish".
BLOCKS_PHRASE = "is not offered by `kyo next` or dispatched until"


def test_every_link_type_has_a_consequence_sentence() -> None:
    """KYO-350.c1: a type added to LINK_TYPES with no matching sentence here is a
    silent gap in the echo, not a loud one -- this is the test that makes it loud."""
    assert set(links.EDGE_CONSEQUENCE) == set(LINK_TYPES)


def test_link_prints_a_consequence_sentence_naming_both_ids_and_titles(kyo) -> None:
    """KYO-350.c1: for `blocks`, the consequence is that the second record cannot
    finish until the first does -- stated with both ids and both titles, not just
    the pair."""
    blocker = new_record(kyo, title=BLOCKER)
    blocked = new_record(kyo, title=BLOCKED)
    result = kyo("link", blocker, "blocks", blocked)
    assert result.code == 0, result
    assert _ordered(result.out, blocked, BLOCKED, BLOCKS_PHRASE, blocker, BLOCKER), result.out


def test_the_consequence_sentence_is_the_last_line_and_json_carries_it(kyo) -> None:
    """KYO-350.c2: a caller reading only the tail -- exactly what the consumer who
    filed this record did with `tail -1` -- must read the direction, and `--json`
    carries the same sentence under its own key rather than making a caller
    reconstruct it from the edge fields."""
    blocker = new_record(kyo, title=BLOCKER)
    blocked = new_record(kyo, title=BLOCKED)
    text = kyo("link", blocker, "blocks", blocked)
    assert text.code == 0, text
    last_line = text.out.rstrip("\n").splitlines()[-1]
    assert blocked in last_line and blocker in last_line
    assert BLOCKS_PHRASE in last_line

    reversed_blocker = new_record(kyo, title=BLOCKER)
    reversed_blocked = new_record(kyo, title=BLOCKED)
    as_json = kyo("link", reversed_blocker, "blocks", reversed_blocked, "--json")
    assert as_json.code == 0, as_json
    assert as_json.json["consequence"] == links.edge_consequence(
        "blocks", reversed_blocker, BLOCKER, reversed_blocked, BLOCKED
    )


def test_a_reversed_link_produces_a_visibly_different_consequence_sentence(kyo) -> None:
    """KYO-350: the guard against a renderer that reads the same order regardless of
    which way the edge actually points -- the exact mistake the record is about."""
    blocker = new_record(kyo, title=BLOCKER)
    blocked = new_record(kyo, title=BLOCKED)
    forwards = kyo("link", blocker, "blocks", blocked)
    backwards_pair = new_record(kyo, title="third"), new_record(kyo, title="fourth")
    backwards = kyo("link", backwards_pair[1], "blocks", backwards_pair[0])

    forwards_last = forwards.out.rstrip("\n").splitlines()[-1]
    backwards_last = backwards.out.rstrip("\n").splitlines()[-1]
    assert _ordered(forwards_last, blocked, BLOCKS_PHRASE, blocker)
    assert _ordered(backwards_last, backwards_pair[0], BLOCKS_PHRASE, backwards_pair[1])
    assert not _ordered(backwards_last, backwards_pair[1], BLOCKS_PHRASE, backwards_pair[0])
