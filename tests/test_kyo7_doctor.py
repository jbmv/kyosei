"""KYO-7's three new `kyo doctor` checks, each with a test that breaks the
invariant on purpose and watches doctor catch it (law 14)."""

from __future__ import annotations

import json

from kyosei.errors import EXIT_DOCTOR

from .conftest import new_record, valid_report


def _dispatch(kyo, record: str) -> dict:
    result = kyo("dispatch", record, "--json")
    assert result.code == 0, result
    return result.json


def _report_and_ingest(kyo, tmp_path, dispatch: dict, record: str) -> None:
    report = valid_report(
        dispatch_id=dispatch["dispatch_id"], record_id=record,
        worked_from_sha=dispatch["worked_from_sha"],
    )
    path = tmp_path / "r.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    result = kyo("report", dispatch["dispatch_id"], "--file", str(path))
    assert result.code == 0, result


# --------------------------------------------------------- dispatch_state_consistency


def test_doctor_passes_on_a_clean_dispatch_lifecycle(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["a"])
    _dispatch(kyo, record)
    assert kyo("doctor").code == 0


def test_doctor_catches_a_reported_dispatch_with_no_report_id(kyo, git_project, db) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    with db() as conn:
        conn.execute(
            "UPDATE dispatches SET state = 'reported', report_id = NULL WHERE id = ?",
            (d["dispatch_id"],),
        )
        conn.commit()
    result = kyo("doctor")
    assert result.code == EXIT_DOCTOR
    assert "reported but has no report_id" in result.err


def test_doctor_catches_a_terminated_dispatch_with_no_why(kyo, git_project, db) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    with db() as conn:
        conn.execute(
            "UPDATE dispatches SET state = 'terminated-no-report', terminated_why = NULL"
            " WHERE id = ?",
            (d["dispatch_id"],),
        )
        conn.commit()
    result = kyo("doctor")
    assert result.code == EXIT_DOCTOR
    assert "terminated-no-report but has no recorded why" in result.err


# ------------------------------------------------------------ report_dispatch_agreement


def test_doctor_catches_a_report_dispatch_back_link_disagreement(
    kyo, git_project, db, tmp_path
) -> None:
    record = new_record(kyo, criteria=["a"])
    d = _dispatch(kyo, record)
    _report_and_ingest(kyo, tmp_path, d, record)
    with db() as conn:
        conn.execute(
            "UPDATE dispatches SET report_id = 'TST-999.r9' WHERE id = ?", (d["dispatch_id"],)
        )
        conn.commit()
    result = kyo("doctor")
    assert result.code == EXIT_DOCTOR
    assert "back-link disagreement" in result.err


def test_doctor_catches_a_report_filed_under_the_wrong_record(
    kyo, git_project, db, tmp_path
) -> None:
    record = new_record(kyo, criteria=["a"])
    other = new_record(kyo, criteria=["b"])
    d = _dispatch(kyo, record)
    _report_and_ingest(kyo, tmp_path, d, record)
    with db() as conn:
        report_id = conn.execute(
            "SELECT id FROM reports WHERE dispatch_id = ?", (d["dispatch_id"],)
        ).fetchone()["id"]
        conn.execute("UPDATE reports SET record_id = ? WHERE id = ?", (other, report_id))
        conn.commit()
    result = kyo("doctor")
    assert result.code == EXIT_DOCTOR
    assert "filed under record" in result.err


# ------------------------------------------------------------- split_backlink_symmetry


def test_doctor_passes_after_a_clean_split(kyo, project) -> None:
    record = new_record(kyo, criteria=["a", "b"])
    kyo("split", record, "--to-successor", f"{record}.c2")
    assert kyo("doctor").code == 0


def test_doctor_catches_a_split_reason_naming_a_missing_successor_criterion(
    kyo, project, db
) -> None:
    record = new_record(kyo, criteria=["a", "b"])
    result = kyo("split", record, "--to-successor", f"{record}.c2", "--json")
    successor_id = result.json["successor_id"]
    with db() as conn:
        conn.execute(
            "UPDATE criteria SET reason = ? WHERE id = ?",
            (f"split off to successor {successor_id} as {successor_id}.c99", f"{record}.c2"),
        )
        conn.commit()
    doctor = kyo("doctor")
    assert doctor.code == EXIT_DOCTOR
    assert "does not exist" in doctor.err


def test_doctor_catches_a_split_reason_with_no_matching_produced_by_link(
    kyo, project, db
) -> None:
    record = new_record(kyo, criteria=["a", "b"])
    result = kyo("split", record, "--to-successor", f"{record}.c2", "--json")
    successor_id = result.json["successor_id"]
    with db() as conn:
        conn.execute(
            "DELETE FROM links WHERE src_id = ? AND edge_type = 'produced-by' AND dst_id = ?",
            (successor_id, record),
        )
        conn.commit()
    doctor = kyo("doctor")
    assert doctor.code == EXIT_DOCTOR
    assert "back-link asymmetry" in doctor.err
