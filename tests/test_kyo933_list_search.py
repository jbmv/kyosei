"""``kyo list --search`` (KYO-933): a record found by the words of its text.

A listing's rows carry a record's id and title, so a viewer holding only the rows cannot
find a record by what a gate, a criterion or the description says. The search is made
where the text is, and a row found by a word its id and title lack says where.
"""

from __future__ import annotations

import pytest

from kyosei import query

from .conftest import new_record
from .test_kyo_serve import Client, register, start, stop


def described(kyo, title: str, description: str, criteria: list[str] | None = None) -> str:
    record = new_record(kyo, title=title, criteria=criteria or ["it works"])
    result = kyo("edit", record, "--description-file", "-", stdin=description)
    assert result.code == 0, result.err
    return record


@pytest.fixture
def records(kyo) -> dict[str, str]:
    """Four records that hold a word each in a different place, and one that holds none."""
    gated = described(kyo, "Polish, second session", "A round of small fixes.")
    assert (
        kyo("gate", gated, "awaiting:human-run", "--do", "Watch the Dock icon BOUNCE once").code
        == 0
    )
    criterion = described(
        kyo, "A terminal question", "Nothing here.", ["the icon stops", "The dock is left alone"]
    )
    summary = described(
        kyo, "Search the text", "First line.\n\nThe maintainer looked for the\nDock   bounce."
    )
    titled = described(kyo, "The Dock bounces when away", "Unrelated prose.")
    other = described(kyo, "Unrelated", "Nothing of note.", ["nothing"])
    return {
        "gate": gated,
        "criterion": criterion,
        "description": summary,
        "title": titled,
        "other": other,
    }


@pytest.fixture
def served(project, store_home, kyo):
    """The fixture project, registered and served, as the Mac app reads it."""
    register(store_home, project)
    client = start()
    yield client
    stop(client)


def found(kyo, text: str) -> dict[str, dict | None]:
    listing = kyo("list", "--search", text, "--json")
    assert listing.code == 0, listing.err
    return {row["id"]: row["found"] for row in listing.json["records"]}


def test_a_word_is_found_in_a_gate_a_criterion_the_description_and_the_title(kyo, records) -> None:
    """KYO-933.c1 and c2: each place a word can be, and where the row says it was."""
    answer = found(kyo, "dock")
    assert set(answer) == {records[name] for name in ("gate", "criterion", "description", "title")}
    assert answer[records["gate"]] == {
        "in": "gate",
        "id": f"{records['gate']}.g1",
        "excerpt": "Watch the Dock icon BOUNCE once",
    }
    assert answer[records["criterion"]] == {
        "in": "criterion",
        "id": f"{records['criterion']}.c2",
        "excerpt": "The dock is left alone",
    }
    # The description is flattened onto one line, so the excerpt holds no line break, and
    # it begins a set distance before the word, at a word.
    assert answer[records["description"]] == {
        "in": "description",
        "id": records["description"],
        "excerpt": "… line. The maintainer looked for the Dock bounce.",
    }
    # A record whose title holds every word has nothing further to show.
    assert answer[records["title"]] is None


def test_every_word_must_be_found_in_any_case_and_each_may_be_in_a_different_place(
    kyo, records
) -> None:
    assert set(found(kyo, "DOCK Bounce")) == {
        records["gate"],
        records["description"],
        records["title"],
    }
    # One word in the title and one in the gate: the row names where the second was found.
    assert found(kyo, "polish bounce") == {
        records["gate"]: {
            "in": "gate",
            "id": f"{records['gate']}.g1",
            "excerpt": "Watch the Dock icon BOUNCE once",
        }
    }
    assert found(kyo, "dock zebra") == {}
    # The record's id is searched as its title is.
    assert found(kyo, records["other"]) == {records["other"]: None}


def test_a_cleared_gate_is_not_searched(kyo, records) -> None:
    """KYO-933.c1 names an OPEN gate: a cleared one is what the record waited on once."""
    assert records["gate"] in found(kyo, "watch")
    gate = f"{records['gate']}.g1"
    assert kyo("ungate", gate, "--withdrawn", "--why", "moot").code == 0
    assert found(kyo, "watch") == {}


def test_the_search_combines_with_the_filters_and_the_limit(kyo, records) -> None:
    assert kyo("list", "--search", "dock", "--kind", "task", "--json").json["records"] == []
    assert len(kyo("list", "--search", "dock", "--kind", "bug", "--json").json["records"]) == 4
    limited = kyo("list", "--search", "dock", "--limit", "1", "--json").json
    assert [row["id"] for row in limited["records"]] == [records["gate"]]
    assert limited["summary"]["total"] == 4


def test_without_the_flag_no_row_carries_the_field_and_an_empty_text_narrows_nothing(
    kyo, records
) -> None:
    plain = kyo("list", "--json").json["records"]
    assert len(plain) == 5
    assert all("found" not in row for row in plain)
    assert len(kyo("list", "--search", "  ", "--json").json["records"]) == 5


def test_the_listing_prints_where_a_word_was_found(kyo, records) -> None:
    lines = kyo("list", "--search", "dock").out.splitlines()
    assert f"    found in {records['gate']}.g1: Watch the Dock icon BOUNCE once" in lines
    assert "    found in description: … line. The maintainer looked for the Dock bounce." in lines
    assert lines[-1].startswith("4 record(s)")


def test_a_long_text_is_cut_to_a_line_around_the_word_at_word_boundaries() -> None:
    text = "alpha " * 30 + "the needle sits here " + "omega " * 40
    line = query.excerpt(text, "needle")
    assert line is not None
    assert line.startswith("… alpha ") and line.endswith("omega …")
    assert "needle" in line
    assert len(line) <= query.EXCERPT_BEFORE + query.EXCERPT_AFTER + len("needle") + 4
    assert query.excerpt("Short TEXT", "text") == "Short TEXT"
    assert query.excerpt("nothing", "needle") is None
    assert query.search_words("  Dock  BOUNCE dock ") == ("dock", "bounce")


def test_the_search_is_served_as_the_cli_prints_it(served: Client, kyo, project) -> None:
    """What the Mac app's Records search asks: `cmd/list?search=`, a read like any other."""
    record = described(kyo, "Another", "The words Dock bounce are only here.")
    status, _headers, body = served.get("/v1/projects/proj/cmd/list?search=dock+bounce")
    assert status == 200
    assert body.decode("utf-8") == kyo("list", "--search", "dock bounce", "--json").out
    assert f'"id":"{record}"' in body.decode("utf-8").replace(": ", ":")
