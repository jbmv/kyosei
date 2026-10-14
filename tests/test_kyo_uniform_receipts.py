"""KYO-538: the three filing commands share one receipt, and an identical filing is refused.

The incident: an orchestrator filtered `kyo spawn`'s output for "created KYO-", missed
"spawned KYO-524 from KYO-523", spawned again and made a duplicate; `kyo new --json` had no
top-level `id`; and the near-duplicate warning fired at 1.00 similarity without stopping
the second filing.

The receipt shape chosen: the first line of `kyo new`, `kyo spawn` and `kyo promote` all
begin ``created <ID>`` (a spawn or a promotion adds its provenance in a parenthesis after
the id), and each `--json` receipt carries the id at a top-level ``id``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from .conftest import new_record

FIRST_LINE = re.compile(r"^created ([A-Z]+-\d+)( \(.*\))?$")


def _new(kyo, title: str = "Receipts", *extra: str):
    return kyo("new", "proposal", "--title", title, *extra)


def _spawn(kyo, source: str, title: str, *extra: str):
    return kyo("spawn", source, "--kind", "evidence", "--title", title, *extra)


def _promote(kyo, project: Path, name: str, *extra: str):
    idea = project / "ideas" / f"{name}.md"
    idea.write_text(f"# {name}\n\nSome words.\n", encoding="utf-8")
    criteria = project / "criteria.txt"
    criteria.write_text("it is promoted\n", encoding="utf-8")
    (project / "scope.txt").write_text("README.md\n", encoding="utf-8")
    (project / "README.md").write_text("x\n", encoding="utf-8")
    return kyo(
        "promote", str(idea), "--kind", "proposal", "--criteria-file", str(criteria),
        "--touches-file", str(project / "scope.txt"), *extra,
    )  # fmt: skip


def test_the_three_receipts_open_with_one_shape_naming_the_new_id(kyo, project) -> None:
    source = new_record(kyo, kind="investigation", title="Source")
    receipts = [
        _new(kyo, "from new"),
        _spawn(kyo, source, "from spawn"),
        _promote(kyo, project, "from-promote"),
    ]
    for receipt in receipts:
        assert receipt.code == 0, receipt
        assert FIRST_LINE.match(receipt.out.splitlines()[0]), receipt.out


def test_the_provenance_rides_after_the_id_not_before_it(kyo, project) -> None:
    source = new_record(kyo, kind="investigation", title="Source")
    spawned = _spawn(kyo, source, "from spawn").out.splitlines()[0]
    assert f"(spawned from {source}" in spawned
    promoted = _promote(kyo, project, "from-promote").out.splitlines()[0]
    assert "(promoted from ideas/from-promote.md" in promoted


def test_each_json_receipt_has_a_top_level_id(kyo, project) -> None:
    source = new_record(kyo, kind="investigation", title="Source")
    for receipt in (
        _new(kyo, "j new", "--json"),
        _spawn(kyo, source, "j spawn", "--json"),
        _promote(kyo, project, "j-promote", "--json"),
    ):
        assert receipt.code == 0, receipt
        assert receipt.json["id"] == receipt.json["record_id"] == receipt.json["record"]["id"]
        assert re.fullmatch(r"[A-Z]+-\d+", receipt.json["id"])


def test_an_identical_filing_within_the_hour_is_refused_naming_the_record_and_force(
    kyo,
) -> None:
    first = _new(kyo, "Same thing", "--json")
    assert first.code == 0, first
    refused = _new(kyo, "Same thing")
    assert refused.code == 1, refused
    assert first.json["id"] in refused.err and "--force" in refused.err
    assert len(kyo("list", "--json").json["records"]) == 1


def test_force_clears_the_refusal(kyo) -> None:
    """The remedy the refusal names, run: the second filing lands."""
    assert _new(kyo, "Same thing").code == 0
    assert _new(kyo, "Same thing").code == 1
    forced = _new(kyo, "Same thing", "--force", "--json")
    assert forced.code == 0, forced
    assert len(kyo("list", "--json").json["records"]) == 2


def test_spawn_and_promote_refuse_an_identical_filing_too(kyo, project) -> None:
    source = new_record(kyo, kind="investigation", title="Source")
    assert _spawn(kyo, source, "Twice").code == 0
    refused = _spawn(kyo, source, "Twice")
    assert refused.code == 1 and "--force" in refused.err, refused
    assert _spawn(kyo, source, "Twice", "--force").code == 0

    assert _promote(kyo, project, "twice").code == 0
    again = _promote(kyo, project, "twice")  # same file name, same title and text
    assert again.code == 1 and "--force" in again.err, again
    assert _promote(kyo, project, "twice", "--force").code == 0


@pytest.mark.parametrize("what", ["title", "criteria"])
def test_a_near_duplicate_only_warns(kyo, what: str) -> None:
    title = "Widget explodes under sustained load"
    if what == "title":
        assert _new(kyo, "Cold cache timings under load").code == 0
        second = _new(kyo, "Cold cache timings under loads", "--json")
    else:
        make = ["new", "bug", "--criteria-file", "-"]
        assert kyo(*make, "--title", title, stdin="the widget stops exploding\n").code == 0
        second = kyo(
            *make, "--title", "A wholly different heading", "--json",
            stdin="the widget stops exploding now\n",
        )  # fmt: skip
    assert second.code == 0, second
    assert second.json["duplicate_warnings"], second.json


def test_different_criteria_under_one_title_is_not_identical(kyo) -> None:
    assert kyo("new", "bug", "--title", "One", "--criteria-file", "-", stdin="a\n").code == 0
    other = kyo("new", "bug", "--title", "One", "--criteria-file", "-", stdin="b\n")
    assert other.code == 0, other


def test_a_record_older_than_an_hour_is_not_a_duplicate(kyo, db) -> None:
    first = _new(kyo, "Old news", "--json")
    conn = db()
    conn.execute(
        "UPDATE records SET created_at = '2000-01-01T00:00:00.000Z' WHERE id = ?",
        (first.json["id"],),
    )
    conn.close()
    assert _new(kyo, "Old news").code == 0
