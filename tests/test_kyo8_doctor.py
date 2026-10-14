"""KYO-8's new `kyo doctor` checks, each with a test that breaks the invariant
on purpose and watches doctor catch it (law 14), plus the rebuild round-trip
for the schema-v4 tables."""

from __future__ import annotations

from pathlib import Path

from kyosei.errors import EXIT_DOCTOR


def _failing_checks(kyo) -> dict:
    result = kyo("doctor", "--json")
    assert result.code == EXIT_DOCTOR, result
    return {name: c for name, c in result.json["checks"].items() if not c["ok"]}


def _flag_via_missing_touch(kyo, project: Path, write_file) -> tuple[str, str]:
    """Returns ``(record_id, flag_id)`` for a deterministic touch-missing flag."""
    target = project / "widget.py"
    target.write_text("x = 1\n", encoding="utf-8")
    idea = project / "ideas" / "a.md"
    idea.write_text("# A\n\n\n", encoding="utf-8")
    rec = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("c.txt", "x\n"),
        "--touches-file", write_file("t.txt", "widget.py\n"),
        "--json",
    )
    assert rec.code == 0, rec
    target.unlink()
    swept = kyo("sweep", "--json")
    assert swept.json["flags"], swept
    return rec.json["record"]["id"], swept.json["flags"][0]["flag_id"]


# --------------------------------------------------------------------- flag_types


def test_doctor_catches_an_unknown_flag_type(kyo, project: Path, write_file, db) -> None:
    record, flag_id = _flag_via_missing_touch(kyo, project, write_file)
    with db() as conn:
        conn.execute("UPDATE flags SET flag_type = 'not-a-real-type' WHERE id = ?", (flag_id,))
    failures = _failing_checks(kyo)
    assert "flag_types" in failures
    assert flag_id in failures["flag_types"]["failures"][0]


# ------------------------------------------------------- flag_resolution_consistency


def test_doctor_catches_resolved_at_without_resolved_by(
    kyo, project: Path, write_file, db
) -> None:
    record, flag_id = _flag_via_missing_touch(kyo, project, write_file)
    with db() as conn:
        conn.execute(
            "UPDATE flags SET resolved_at = '2026-08-21T00:00:00.000Z' WHERE id = ?", (flag_id,)
        )
    failures = _failing_checks(kyo)
    assert "flag_resolution_consistency" in failures


def test_doctor_passes_a_properly_resolved_flag(kyo, project: Path, write_file) -> None:
    record, flag_id = _flag_via_missing_touch(kyo, project, write_file)
    resolved = kyo("flag", "resolve", flag_id, "--why", "acknowledged")
    assert resolved.code == 0, resolved
    assert kyo("doctor").code == 0


# ------------------------------------------------------------------------- orphans


def test_doctor_catches_a_flag_pointing_at_a_missing_record(
    kyo, project: Path, write_file, db
) -> None:
    record, flag_id = _flag_via_missing_touch(kyo, project, write_file)
    with db() as conn:
        conn.execute("UPDATE flags SET record_id = 'TST-999' WHERE id = ?", (flag_id,))
    failures = _failing_checks(kyo)
    assert "orphans" in failures


def test_doctor_catches_a_flag_other_record_id_pointing_nowhere(
    kyo, project: Path, write_file, db
) -> None:
    record, flag_id = _flag_via_missing_touch(kyo, project, write_file)
    with db() as conn:
        conn.execute(
            "UPDATE flags SET other_record_id = 'TST-999' WHERE id = ?", (flag_id,)
        )
    failures = _failing_checks(kyo)
    assert "orphans" in failures


# --------------------------------------------------------------------- allocations


def test_doctor_catches_a_sweep_allocation_that_would_reissue(
    kyo, project: Path, write_file, db
) -> None:
    _flag_via_missing_touch(kyo, project, write_file)
    with db() as conn:
        conn.execute("UPDATE allocations SET next_value = 1 WHERE scope = 'sweep'")
    failures = _failing_checks(kyo)
    assert "allocations" in failures


# ------------------------------------------------------------- export / rebuild round-trip


def test_doctor_passes_after_a_full_m6_lifecycle(kyo, project: Path, write_file) -> None:
    """Sweep flags, a resolve, a shift note, and a brief — the export must stay
    re-derivable and the rebuild round-trip must hold (charter, Layer 2)."""
    record, flag_id = _flag_via_missing_touch(kyo, project, write_file)
    kyo("flag", "resolve", flag_id, "--why", "acknowledged")
    kyo("note", "--shift", "--file", "-", stdin="handoff thought")
    kyo("brief")
    result = kyo("doctor", "--json")
    assert result.code == 0, result.json


def test_rebuild_reconstructs_flags_sweep_and_shift_state(
    kyo, project: Path, write_file
) -> None:
    record, flag_id = _flag_via_missing_touch(kyo, project, write_file)
    kyo("note", "--shift", "--file", "-", stdin="handoff thought")
    kyo("brief")

    before = kyo("show", record, "--json").json
    (project / "kyosei" / "kyosei.db").unlink()
    rebuilt = kyo("rebuild")
    assert rebuilt.code == 0, rebuilt

    after = kyo("show", record, "--json").json
    assert after["flags"] == before["flags"]
    assert kyo("doctor").code == 0


def test_the_export_carries_flags(kyo, project: Path, write_file) -> None:
    record, flag_id = _flag_via_missing_touch(kyo, project, write_file)
    doc = kyo("show", record, "--json").json
    assert doc["flags"][0]["id"] == flag_id
    assert doc["flags"][0]["flag_type"] == "touch-missing"
