"""KYO-739: `kyo show` reads a criterion, gate, note or claim by its own id.

An anchor in a prompt can name an element of a record (KYO-737); each element id is read
on its own, under a heading naming the record that owns it, in the words the record's own
`kyo show` prints for that element.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kyosei import cmd_views, serve
from kyosei.render import claim_lines, gate_lines, note_lines

from .conftest import new_record
from .test_kyo_serve import register, start, stop


@pytest.fixture
def record(kyo, tmp_path: Path) -> str:
    rid = new_record(kyo, title="Owning record", criteria=["first thing", "second thing"])
    assert kyo("gate", rid, "awaiting:human-review", "--do", "look at it").code == 0
    assert kyo("edit", rid, "--note-file", "-", stdin="a note\nin two lines\n").code == 0
    body = tmp_path / "claim.md"
    body.write_text("the statement\n", encoding="utf-8")
    assert (
        kyo("claim", rid, "--level", "verified", "--method", "read it", "--file", str(body)).code
        == 0
    )
    return rid


def _doc(kyo, rid: str) -> dict:
    return kyo("show", rid, "--json").json


def test_every_element_prints_under_its_record_and_in_json(kyo, record: str) -> None:
    """c1: text and --json, for a criterion, a gate, a note and a claim."""
    doc = _doc(kyo, record)
    cases = {
        f"{record}.c1": ("criterion", doc["criteria"][0], ["first thing", "unmet"]),
        f"{record}.g1": ("gate", doc["gates"][0], ["awaiting:human-review", "look at it", "open"]),
        f"{record}.n1": ("note", doc["notes"][0], ["a note", "in two lines"]),
        f"{record}.m1": ("claim", doc["claims"][0], ["verified", "read it", "the statement"]),
    }
    for element_id, (kind, element, words) in cases.items():
        shown = kyo("show", element_id)
        assert shown.code == 0, shown
        first = shown.out.splitlines()[0]
        assert element_id in first and record in first and "Owning record" in first, first
        for word in words:
            assert word in shown.out, (element_id, word)
        as_json = kyo("show", element_id, "--json")
        assert as_json.code == 0, as_json
        assert as_json.json["record_id"] == record
        assert as_json.json["record_title"] == "Owning record"
        assert as_json.json["object"] == kind
        for key, value in element.items():
            assert as_json.json[key] == value, (element_id, key)


def test_the_element_view_is_the_record_blocks_own_lines(kyo, record: str) -> None:
    """The two readings use one function, so they cannot disagree."""
    doc = _doc(kyo, record)
    for element_id, lines in (
        (f"{record}.g1", gate_lines(doc, doc["gates"][0], {})),
        (f"{record}.n1", note_lines(doc["notes"][0])),
        (f"{record}.m1", claim_lines(doc["claims"][0])),
    ):
        printed = kyo("show", element_id).out.splitlines()[1:]
        assert printed[: len(lines)] == lines
        assert all(line in kyo("show", record).out for line in lines if line.strip())


def test_an_element_the_record_does_not_hold_names_the_record_and_the_listing(
    kyo, record: str
) -> None:
    """c2, and the remedy it names runs and lists the elements held."""
    for bad in (f"{record}.c9", f"{record}.g9", f"{record}.n9", f"{record}.m9"):
        refused = kyo("show", bad)
        assert refused.code == 1, refused
        assert record in refused.err and "Owning record" in refused.err
        assert f"`kyo show {record}`" in refused.err
    listing = kyo("show", record)
    assert listing.code == 0
    assert f"{record}.c1" in listing.out and f"{record}.g1" in listing.out


def test_an_element_of_an_unknown_record_is_refused(kyo, record: str) -> None:
    refused = kyo("show", "TST-999.c1")
    assert refused.code == 1, refused
    assert "no criterion" in refused.err


def test_a_shape_show_does_not_read_lists_the_ones_it_does(kyo, record: str) -> None:
    """c3: the refusal and `kyo show --help` name the element shapes."""
    refused = kyo("show", f"{record}.t1")
    assert refused.code == 64, refused
    for shape in ("PREFIX-N.gN", "PREFIX-N.nN", "PREFIX-N.mN", "PREFIX-N.cN"):
        assert shape in refused.err, shape
    reference = kyo("reference", "show")
    assert reference.code == 0, reference
    for shape, _what in cmd_views.SHOWABLE_SHAPES:
        assert shape in reference.out, shape


def test_kyo_serve_reads_an_element_id_through_the_route(
    kyo, record: str, project: Path, store_home: Path
) -> None:
    """c4: `show` is a served command, and an element id goes through it."""
    register(store_home, project)
    served = start()
    try:
        status, _, body = served.get(f"/v1/projects/proj/cmd/show/{record}.g1")
        assert status == 200, body
        import json

        document = json.loads(body)
        assert document["record_id"] == record
        assert document["record_title"] == "Owning record"
        assert document["object"] == "gate"
        assert "show" in serve.SERVED_COMMANDS
    finally:
        stop(served)


def test_the_skill_says_to_show_the_element_by_its_own_id() -> None:
    """c5."""
    text = (
        Path(__file__).parent.parent
        / "src/kyosei/harness/claude-code/plugin/skills/kyosei/REFERENCE.md"
    ).read_text(encoding="utf-8")
    section = text.split("### An `on record ...`", 1)[1].split("\n### ", 1)[0]
    assert "`kyo show KYO-717.c4`" in section
    assert "whole record" not in section
