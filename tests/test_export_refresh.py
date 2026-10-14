"""KYO-6: ``kyo export --refresh`` rewrites every drifted export document from the
authoritative db; plain ``kyo doctor`` still fails on drift it was not asked to
repair.

After a format-version bump, every export document on disk stays in the old
writer format until its record happens to be mutated — ``kyo doctor`` flags all
of them, and the only prior refresh path was a dummy mutation per record. This
file exercises the new command end to end: manufacture drift, watch doctor catch
it, run the refresh, and watch doctor pass.
"""

from __future__ import annotations

import json
from pathlib import Path

from kyosei.errors import EXIT_DOCTOR, EXIT_USAGE

from .conftest import events_text, new_record


def _export_path(project: Path, record_id: str) -> Path:
    return project / "kyosei" / "export" / f"{record_id}.json"


def _drift(project: Path, record_id: str) -> None:
    """Simulate a document written by an older export format: an extra top-level
    key doctor's byte-for-byte comparison would catch, with no db mutation at all
    — the exact shape KYO-6 was filed against (drift that predates the record's
    next real edit)."""
    path = _export_path(project, record_id)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["title"] = doc["title"] + " (stale writer format)"
    path.write_text(json.dumps(doc, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def test_doctor_catches_drift_that_export_refresh_then_repairs(kyo, project: Path) -> None:
    a = new_record(kyo, title="alpha", criteria=["a"])
    b = new_record(kyo, title="beta", criteria=["b"])
    _drift(project, a)
    _drift(project, b)

    broken = kyo("doctor")
    assert broken.code == EXIT_DOCTOR
    assert a in broken.err
    assert b in broken.err

    refreshed = kyo("export", "--refresh")
    assert refreshed.code == 0, refreshed
    assert a in refreshed.out
    assert b in refreshed.out

    fixed = kyo("doctor")
    assert fixed.code == 0, fixed


def test_export_refresh_reports_exactly_which_records_were_drifted(kyo, project: Path) -> None:
    a = new_record(kyo, title="alpha", criteria=["a"])
    b = new_record(kyo, title="beta", criteria=["b"])
    _drift(project, a)

    result = kyo("export", "--refresh", "--json")
    assert result.code == 0, result
    assert result.json["drifted"] == [a]
    assert result.json["refreshed"] == 2
    assert b not in result.json["drifted"]


def test_export_refresh_is_a_clean_no_op_when_nothing_is_drifted(kyo, project: Path) -> None:
    new_record(kyo, title="alpha", criteria=["a"])
    result = kyo("export", "--refresh", "--json")
    assert result.code == 0, result
    assert result.json["drifted"] == []
    before = kyo("doctor")
    assert before.code == 0, before


def test_plain_doctor_never_auto_repairs_drift(kyo, project: Path) -> None:
    """`kyo doctor` alone (no `export --refresh`) must keep failing on drift it
    was not asked to repair — it diagnoses, it does not fix."""
    a = new_record(kyo, title="alpha", criteria=["a"])
    _drift(project, a)

    first = kyo("doctor")
    assert first.code == EXIT_DOCTOR
    second = kyo("doctor")
    assert second.code == EXIT_DOCTOR, "doctor must not silently repair drift as a side effect"
    assert "(stale writer format)" in _export_path(project, a).read_text(encoding="utf-8")


def test_export_without_refresh_flag_is_a_usage_error(kyo, project: Path) -> None:
    new_record(kyo, title="alpha", criteria=["a"])
    result = kyo("export")
    assert result.code == EXIT_USAGE
    assert "--refresh" in result.err


def test_export_refresh_does_not_touch_the_events_ledger(kyo, project: Path) -> None:
    """Refresh repairs derived-from-current-state artifacts; the append-only
    events ledger is history, not something a refresh should rewrite."""
    a = new_record(kyo, title="alpha", criteria=["a"])
    _drift(project, a)
    before = events_text(project)
    kyo("export", "--refresh")
    assert events_text(project) == before


# ------------------------------------------------------------------------- KYO-81
# `kyo export --refresh` is the one writer to `kyosei/export/` that never opens a
# `Store.transaction()`, so KYO-75's guard — placed on that seam precisely so no
# future writer has to remember it — cannot see this one. Over a database whose
# export is ahead of it, it does that guard's whole damage by the other route.


def _allocations(project: Path) -> Path:
    return project / "kyosei" / "export" / "_allocations.json"


def _export_ahead(project: Path, record_id: str, *, title: str) -> tuple[str, str]:
    """Manufacture the pulled state without git: an export holding a record edit and
    a counter this database has not reached.

    A real pull produces this (`tests/test_split_store.py` builds it with two
    checkouts and asserts the same refusal over the genuine article); written by hand
    here so the state is exact and the fixture is one file read.

    Returns the two byte sequences that must survive an unforced refresh.
    """
    counters = json.loads(_allocations(project).read_text(encoding="utf-8"))
    counters["event"] = int(counters["event"]) + 3
    _allocations(project).write_text(
        json.dumps(counters, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    document = json.loads(_export_path(project, record_id).read_text(encoding="utf-8"))
    document["title"] = title
    _export_path(project, record_id).write_text(
        json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return (
        _allocations(project).read_text(encoding="utf-8"),
        _export_path(project, record_id).read_text(encoding="utf-8"),
    )


def test_export_refresh_over_an_export_ahead_database_changes_nothing(
    kyo, project: Path
) -> None:
    """KYO-81.c1/.c3. Without consent, not one byte moves.

    Asserted on the two artifacts the damage lands in rather than on the exit code
    alone: the record document (rewritten from the older database) and the counter
    snapshot (lowered, so the next allocation reissues an id the export has issued).
    A refusal that still wrote one of them would pass an exit-code assertion.
    """
    a = new_record(kyo, title="alpha", criteria=["a"])
    counters_before, document_before = _export_ahead(project, a, title="retitled elsewhere")
    assert kyo("list", "--json").json["db_export_currency"]["direction"] == "export-ahead"

    refused = kyo("export", "--refresh")
    assert refused.code != 0, refused
    said = refused.out + refused.err
    # It says which side it was about to overwrite with which, and names the remedy
    # that resolves the state rather than the one that discards a side of it.
    assert "database is behind its export" in said, said
    assert "kyo rebuild --force" in said, said
    assert "--force" in said, said

    assert _allocations(project).read_text(encoding="utf-8") == counters_before
    assert _export_path(project, a).read_text(encoding="utf-8") == document_before


def test_the_refusal_names_a_flag_that_actually_clears_it(kyo, project: Path) -> None:
    """KYO-5's discipline: the remedy the message names must work (and here, must be
    the only thing that does). `--force` is `kyo rebuild --force`'s shape, in the
    other direction — a flag, because a prompt is unavailable to the worker, the hook
    and the desktop app that run this command (KYO-81.c2)."""
    a = new_record(kyo, title="alpha", criteria=["a"])
    _export_ahead(project, a, title="retitled elsewhere")

    forced = kyo("export", "--refresh", "--force", "--json")
    assert forced.code == 0, forced
    assert forced.json["forced_over_export_ahead"] is True
    assert forced.json["counters_lowered"] == ["event"]
    # Law 6: it did the destructive thing, so it says so rather than reporting the
    # same clean success as a refresh that destroyed nothing.
    assert "--force" in forced.out
    assert "database is behind its export" in forced.out
    assert json.loads(_export_path(project, a).read_text(encoding="utf-8"))["title"] == "alpha"


def test_an_ordinary_drift_repair_is_not_caught_by_the_new_guard(kyo, project: Path) -> None:
    """The trap the predicate has to avoid. Drift — an export document differing from
    its record over counters that agree — is what this command exists for (KYO-6), and
    a guard built on the read predicate `db_is_behind` would refuse a class of it.

    So the guard asks `would_reissue_an_id` (KYO-80's distinction): an export document
    with no row behind it sets `db_is_behind` and is untouched by a refresh, which
    deletes nothing and, over agreeing counters, lowers nothing.
    """
    a = new_record(kyo, title="alpha", criteria=["a"])
    _drift(project, a)
    orphan = _export_path(project, "TST-999")
    orphan.write_text(
        json.dumps({"id": "TST-999", "title": "no row behind it"}, indent=2) + "\n",
        encoding="utf-8",
    )
    seam = kyo("list", "--json").json["db_export_currency"]
    assert seam["db_is_behind"] is True, "the read surfaces have something to warn about"
    assert seam["export_ahead_scopes"] == [], "and yet no counter is ahead"

    repaired = kyo("export", "--refresh", "--json")
    assert repaired.code == 0, repaired
    assert repaired.json["drifted"] == [a]
    assert repaired.json["forced_over_export_ahead"] is False
    # supersede, never delete (law 4): the orphan is doctor's finding, not refresh's.
    assert orphan.is_file()
