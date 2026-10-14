"""KYO-498 (from KYO-483): ``records.since_note``.

An item a project already carried in its closing notes is weeks old on the day it is filed
as a record, and an age counted from the record's ``created_at`` would read 0d. The record
stores only the id of the shift note that first recorded the item; every age reads that
note's tool-stamped ``created_at``, so no agent ever types a time (Law 16).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .conftest import new_record


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _shift_note(kyo, text: str = "carried: SIGTERM is undecided") -> str:
    result = kyo("note", "--shift", "--file", "-", "--json", stdin=text)
    assert result.code == 0, result
    return result.json["shift_note_id"]


def _backdate_note(db, note_id: str, ago: timedelta) -> str:
    stamp = _stamp(datetime.now(UTC) - ago)
    conn = db()
    conn.execute("UPDATE shift_notes SET created_at = ? WHERE id = ?", (stamp, note_id))
    conn.close()
    return stamp


def _backdate_record(db, record_id: str, ago: timedelta) -> None:
    conn = db()
    conn.execute(
        "UPDATE records SET created_at = ? WHERE id = ?",
        (_stamp(datetime.now(UTC) - ago), record_id),
    )
    conn.close()


def _carried_item(brief_json: dict, record_id: str) -> dict:
    return next(item for item in brief_json["carried"] if item["id"] == record_id)


# --- c1: the note is stored and every age counts from it ---------------------------


def test_new_with_since_note_counts_the_age_from_the_note(kyo, db) -> None:
    note = _shift_note(kyo)
    note_stamp = _backdate_note(db, note, timedelta(days=22, hours=1))
    result = kyo("new", "reminder", "--title", "SIGTERM", "--since-note", note, "--json")
    assert result.code == 0, result
    record = result.json["record"]["id"]
    assert result.json["record"]["since_note"] == note

    item = _carried_item(kyo("brief", "--json").json, record)
    # If the basis fell back to the record's own created_at this would read 0.
    assert item["age_days"] == 22
    assert item["since"] == note_stamp
    assert item["since_note"] == note

    shown = kyo("show", record, "--json").json
    assert shown["since_note"] == note
    assert shown["carried"] == {"since": note_stamp, "since_note": note, "age_days": 22}
    assert f"carried: since {note_stamp} ({note}), 22d" in kyo("show", record).out
    assert f"{record}  22d  SIGTERM" in kyo("brief").out


def test_edit_since_note_sets_it_later_and_orders_the_brief_by_the_basis(kyo, db) -> None:
    older = kyo("new", "reminder", "--title", "filed first", "--json").json["record"]["id"]
    _backdate_record(db, older, timedelta(days=5))
    note = _shift_note(kyo)
    _backdate_note(db, note, timedelta(days=30))
    later = kyo("new", "reminder", "--title", "filed second", "--json").json["record"]["id"]
    _backdate_record(db, later, timedelta(days=3))

    # The note is earlier than both records, so `later` may name it.
    edited = kyo("edit", later, "--since-note", note, "--json")
    assert edited.code == 0, edited
    assert edited.json["record"]["since_note"] == note

    carried = kyo("brief", "--json").json["carried"]
    ids = [item["id"] for item in carried]
    assert ids.index(later) < ids.index(older), "oldest basis first"
    assert _carried_item({"carried": carried}, later)["age_days"] == 30
    assert _carried_item({"carried": carried}, older)["age_days"] == 5
    assert kyo("show", older, "--json").json["carried"]["since_note"] is None
    assert "(filed), 5d" in kyo("show", older).out


# --- c2: refusals, each naming a remedy that clears it ------------------------------


def test_an_unknown_note_is_refused_and_naming_a_listed_note_clears_it(kyo) -> None:
    refused = kyo("new", "reminder", "--title", "x", "--since-note", "TST-S99")
    assert refused.code != 0
    assert "no shift note 'TST-S99'" in refused.err
    assert "kyo shift-notes" in refused.err
    note = _shift_note(kyo)
    assert note in kyo("shift-notes").out  # the remedy's command lists it
    cleared = kyo("new", "reminder", "--title", "x", "--since-note", note)
    assert cleared.code == 0, cleared


def test_a_note_later_than_the_record_is_refused_and_an_earlier_note_clears_it(
    kyo, db
) -> None:
    record = kyo("new", "reminder", "--title", "old item", "--json").json["record"]["id"]
    _backdate_record(db, record, timedelta(days=2))
    late = _shift_note(kyo, "written after the record")
    refused = kyo("edit", record, "--since-note", late)
    assert refused.code != 0
    assert "cannot be the note that first recorded it" in refused.err
    assert "Name an earlier shift note" in refused.err
    assert kyo("show", record, "--json").json["since_note"] is None

    early = _shift_note(kyo, "the first mention")
    _backdate_note(db, early, timedelta(days=9))
    cleared = kyo("edit", record, "--since-note", early)
    assert cleared.code == 0, cleared
    assert kyo("show", record, "--json").json["carried"]["age_days"] == 9


def test_a_non_carried_kind_is_refused_and_filing_as_carry_clears_it(kyo) -> None:
    note = _shift_note(kyo)
    refused = kyo("new", "task", "--title", "x", "--since-note", note)
    assert refused.code != 0
    assert "does not carry policy.carried" in refused.err
    assert "(reminder)" in refused.err
    cleared = kyo("new", "reminder", "--title", "x", "--since-note", note)
    assert cleared.code == 0, cleared

    task = new_record(kyo, title="a task", criteria=["a"])
    refused_edit = kyo("edit", task, "--since-note", note)
    assert refused_edit.code != 0
    assert "does not carry policy.carried" in refused_edit.err


# --- c4: the export carries it and rebuild round-trips it ---------------------------


def test_the_export_document_round_trips_since_note_through_rebuild(
    kyo, db, project: Path
) -> None:
    note = _shift_note(kyo)
    conn = db()
    note_stamp = conn.execute(
        "SELECT created_at FROM shift_notes WHERE id = ?", (note,)
    ).fetchone()["created_at"]
    conn.close()
    record = kyo("new", "reminder", "--title", "x", "--since-note", note, "--json").json[
        "record"
    ]["id"]
    doc = json.loads(
        (project / "kyosei" / "export" / f"{record}.json").read_text(encoding="utf-8")
    )
    assert doc["since_note"] == note

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    shown = kyo("show", record, "--json").json
    assert shown["since_note"] == note
    assert shown["carried"]["since"] == note_stamp
