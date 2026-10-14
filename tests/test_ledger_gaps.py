"""KYO-404: a row committed to the database but never appended to its ledger.

Every ledger (events, sweep runs, brief runs, shift notes) is appended after its
transaction commits. An interruption in between used to leave the row in ``kyosei.db``
only; ``kyo doctor`` said nothing (or, via the currency check, sent the reader to
``kyo export --refresh`` "so nothing is lost", which never touched a ledger), and the
next ``kyo rebuild`` dropped the row for good.

These tests interrupt the real commands by making one post-commit writer raise — the
closest in-process stand-in for a kill between COMMIT and the append — and then follow
the remedy the check names, exactly as a reader would.
"""

from __future__ import annotations

import json
import shlex
import sqlite3
from pathlib import Path

import pytest

from kyosei import commands, events_ledger, export, ledger_repair, ledgers
from kyosei.errors import EXIT_DOCTOR

from .conftest import events_text


class Interrupted(BaseException):
    """Stands in for a kill, a SIGINT or a hook timeout after COMMIT.

    A ``BaseException``, like the ``KeyboardInterrupt`` it models: since KYO-421 an
    ordinary ``Exception`` from a handler is caught by ``cli.main`` and reported on one
    line with exit 1, so only something outside that class still ends the process
    mid-command the way these tests need.
    """


def _raise(*_args, **_kwargs):
    raise Interrupted("interrupted after commit")


def _db(project: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    conn.row_factory = sqlite3.Row
    return conn


def _export(project: Path) -> Path:
    return project / "kyosei" / "export"


def _new(kyo, title: str) -> None:
    from .conftest import allow_direct_filing

    allow_direct_filing(kyo.project, "bug")
    kyo("new", "bug", "--title", title)


#: The module each export writer these tests interrupt is defined in -- the one a
#: monkeypatch must target to reach the writer's callers.
_WRITER_MODULES = {
    "append_events": events_ledger,
    "append_sweep_run": ledgers,
    "append_brief_run": ledgers,
    "append_shift_note": ledgers,
    "write_exports": export,
    "write_allocations": ledgers,
}


def _interrupted_new(kyo, monkeypatch, name: str, title: str = "interrupted") -> None:
    """Run `kyo new` with the writer ``name`` raising, then put the writer back."""
    from .conftest import allow_direct_filing

    allow_direct_filing(kyo.project, "bug")
    with monkeypatch.context() as patch:
        patch.setattr(_WRITER_MODULES[name], name, _raise)
        with pytest.raises(Interrupted):
            kyo("new", "bug", "--title", title)


def _max_event(project: Path) -> sqlite3.Row:
    with _db(project) as conn:
        return conn.execute("SELECT * FROM events ORDER BY seq DESC LIMIT 1").fetchone()


def _doctor_findings(kyo, check: str) -> list[str]:
    result = kyo("doctor", "--json")
    return result.json["checks"][check]["failures"]


def _remedy_command(finding: str) -> list[str]:
    """The backticked command a finding tells the reader to run, as argv."""
    command = finding.split("Run `", 1)[1].split("`", 1)[0]
    argv = shlex.split(command)
    assert argv[0] == "kyo"
    return argv[1:]


# ------------------------------------------------------------------- c1: the ordering


def test_an_event_is_in_the_ledger_even_when_the_document_write_fails(
    kyo, project: Path, monkeypatch
) -> None:
    """c1: `_finish_mutation` appends first, so a `write_exports` that dies after COMMIT
    still leaves the event in `_events/`. Reverting the order fails this test."""
    _new(kyo, "before")
    _interrupted_new(kyo, monkeypatch, "write_exports")

    last = _max_event(project)
    assert last["command"].startswith("new"), last["command"]
    assert last["id"] in ledger_repair.ledger_ids_in(
        _export(project), events_ledger.EVENTS_FILENAME
    )
    assert _doctor_findings(kyo, "ledger_gaps") == []


def _spy_order(monkeypatch) -> list[str]:
    calls: list[str] = []
    for name in (
        "append_events",
        "append_sweep_run",
        "append_brief_run",
        "append_shift_note",
        "write_exports",
        "write_allocations",
    ):
        real = getattr(_WRITER_MODULES[name], name)

        def spy(*args, _name=name, _real=real, **kwargs):
            calls.append(_name)
            return _real(*args, **kwargs)

        monkeypatch.setattr(_WRITER_MODULES[name], name, spy)
    return calls


def test_finish_mutation_writes_the_ledger_before_anything_re_derivable(
    kyo, project: Path, monkeypatch
) -> None:
    calls = _spy_order(monkeypatch)
    _new(kyo, "spied")
    assert calls == ["append_events", "write_exports", "write_allocations"]


@pytest.mark.parametrize(
    ("argv", "row_writer"),
    [
        (("sweep",), "append_sweep_run"),
        (("brief",), "append_brief_run"),
        (("note", "--shift", "--file", "-"), "append_shift_note"),
    ],
)
def test_the_three_ledger_commands_append_both_rows_before_anything_else(
    kyo, project: Path, monkeypatch, argv, row_writer
) -> None:
    """c2, first half: event, then its own ledger row, then the rest."""
    _new(kyo, "a record for the sweep to look at")
    calls = _spy_order(monkeypatch)
    result = kyo(*argv, stdin="a shift note\n")
    assert result.code == 0, result
    assert calls[:2] == ["append_events", row_writer], calls
    assert calls[-1] == "write_allocations", calls


def test_a_sweep_that_flags_a_record_appends_before_it_rewrites_the_document(
    kyo, project: Path, monkeypatch, write_file
) -> None:
    """The sweep's own misordering: it wrote the flagged records' documents first."""
    from .test_kyo8_sweep import _promote_with_touches

    target = project / "widget.py"
    target.write_text("x = 1\n", encoding="utf-8")
    _promote_with_touches(kyo, project, write_file, touch_path="widget.py")
    target.unlink()
    calls = _spy_order(monkeypatch)
    result = kyo("sweep", "--json")
    assert result.code == 0 and result.json["flags"], result
    assert calls == [
        "append_events",
        "append_sweep_run",
        "write_exports",
        "write_allocations",
    ], calls


# --------------------------------------- c2/c3: the gap is detected, in all four tables


def test_doctor_names_an_event_the_ledger_is_missing(kyo, project: Path, monkeypatch) -> None:
    """c3, the repro: `append_events` raises; the new check fails and names it."""
    _new(kyo, "first")
    _interrupted_new(kyo, monkeypatch, "append_events")
    lost = _max_event(project)["id"]

    doctor = kyo("doctor")
    assert doctor.code == EXIT_DOCTOR
    findings = _doctor_findings(kyo, "ledger_gaps")
    assert len(findings) == 1, findings
    assert "`events` table" in findings[0]
    assert "1 row(s)" in findings[0]
    assert lost in findings[0]


def test_the_gap_is_still_named_after_a_later_command_moves_the_counters_on(
    kyo, project: Path, monkeypatch
) -> None:
    """The next successful command rewrites `_allocations.json`, so the counters no
    longer say the database is ahead — the silent case. The ledger check does not
    depend on the counters, and still names the row."""
    _new(kyo, "first")
    _interrupted_new(kyo, monkeypatch, "append_events")
    lost = _max_event(project)["id"]
    _new(kyo, "a later command that succeeds")

    with _db(project) as conn:
        in_db = {r["scope"]: r["next_value"] for r in conn.execute("SELECT * FROM allocations")}
    on_disk = ledgers.read_allocations_in(_export(project))
    assert on_disk == in_db
    findings = _doctor_findings(kyo, "ledger_gaps")
    assert any(lost in finding for finding in findings), findings


@pytest.mark.parametrize(
    ("argv", "row_writer", "table"),
    [
        (("sweep",), "append_sweep_run", "sweep_runs"),
        (("brief",), "append_brief_run", "brief_runs"),
        (("note", "--shift", "--file", "-"), "append_shift_note", "shift_notes"),
    ],
)
def test_an_interruption_between_the_two_appends_is_detected_and_repaired(
    kyo, project: Path, monkeypatch, argv, row_writer, table
) -> None:
    """c2, second half: the event made it, its sweep/brief/shift-note row did not. The
    check names the row; the remedy it names restores it; a rebuild keeps it."""
    _new(kyo, "a record")
    with monkeypatch.context() as patch:
        patch.setattr(_WRITER_MODULES[row_writer], row_writer, _raise)
        with pytest.raises(Interrupted):
            kyo(*argv, stdin="a shift note\n")
    with _db(project) as conn:
        lost = conn.execute(f"SELECT id FROM {table} ORDER BY seq DESC LIMIT 1").fetchone()[0]

    findings = _doctor_findings(kyo, "ledger_gaps")
    assert len(findings) == 1, findings
    assert f"`{table}` table" in findings[0] and lost in findings[0]

    assert kyo(*_remedy_command(findings[0])).code == 0
    assert kyo("doctor").code == 0
    assert kyo("rebuild", "--force").code == 0
    with _db(project) as conn:
        assert conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (lost,)).fetchone()


def test_the_check_reports_a_ledger_line_the_database_has_no_row_for(
    kyo, project: Path
) -> None:
    """The other direction: the export is ahead (a pull, a restored db). The remedy it
    names — a rebuild — loads the line, and the check clears."""
    _new(kyo, "first")
    db_path = project / "kyosei" / "kyosei.db"
    saved = db_path.read_bytes()
    _new(kyo, "second — recorded, then the database is put back")
    ahead = ledger_repair.ledger_ids_in(_export(project), events_ledger.EVENTS_FILENAME)
    db_path.write_bytes(saved)

    findings = _doctor_findings(kyo, "ledger_gaps")
    assert len(findings) == 1, findings
    assert "have no row in the database's `events` table" in findings[0]
    with _db(project) as conn:
        in_db = {row[0] for row in conn.execute("SELECT id FROM events")}
    assert all(item in findings[0] for item in sorted(ahead - in_db))

    rebuild = findings[0].split("Run `", 1)[1].split("`", 1)[0]
    assert kyo(*shlex.split(rebuild)[1:]).code == 0
    assert _doctor_findings(kyo, "ledger_gaps") == []


# ------------------------------------------------------------ c4: the remedy repairs it


def test_the_named_remedy_re_appends_the_event_exactly_and_a_rebuild_keeps_it(
    kyo, project: Path, monkeypatch
) -> None:
    _new(kyo, "first")
    captured: list[dict] = []

    def capture_then_die(_store, events):
        captured.extend(events)
        raise Interrupted("interrupted after commit")

    from .conftest import allow_direct_filing

    allow_direct_filing(project, "bug")
    with monkeypatch.context() as patch:
        patch.setattr(events_ledger, "append_events", capture_then_die)
        with pytest.raises(Interrupted):
            kyo("new", "bug", "--title", "the lost one")
    # A later event, so the repaired line lands after it — seq order among the rows
    # it appends, never a rewrite of what is already in the file.
    _new(kyo, "a later one")
    assert len(captured) == 1
    lost = captured[0]

    finding = _doctor_findings(kyo, "ledger_gaps")[0]
    repaired = kyo(*_remedy_command(finding), "--json")
    assert repaired.code == 0, repaired
    assert repaired.json["ledger_rows_appended"]["events"] == 1

    # Byte-for-byte what the original append would have written.
    expected = events_ledger.event_line(export.stamp_ledger_row(lost))
    assert expected in events_text(project).splitlines(keepends=True)
    assert kyo("doctor").code == 0

    assert kyo("rebuild", "--force").code == 0
    with _db(project) as conn:
        seqs = [row[0] for row in conn.execute("SELECT seq FROM events ORDER BY seq")]
    assert seqs == list(range(1, len(seqs) + 1)), seqs
    assert lost["seq"] in seqs
    assert kyo("doctor").code == 0


def test_several_missing_rows_go_back_in_seq_order(kyo, project: Path, monkeypatch) -> None:
    _new(kyo, "first")
    _interrupted_new(kyo, monkeypatch, "append_events", "one")
    _interrupted_new(kyo, monkeypatch, "append_events", "two")
    with _db(project) as conn:
        lost = [row[0] for row in conn.execute("SELECT id FROM events ORDER BY seq")][-2:]

    assert kyo("export", "--refresh").code == 0
    tail = [json.loads(line)["id"] for line in events_text(project).splitlines()[-2:]]
    assert tail == lost


def test_a_re_appended_event_does_not_bring_back_redacted_text(
    kyo, project: Path, monkeypatch, write_file
) -> None:
    """The database's own event snapshots are not scrubbed by `kyo redact`, so a row
    re-appended after a redaction is scrubbed on its way back into the ledger."""
    from .conftest import new_record

    record = new_record(kyo, title="has a note")
    secret = "consumer-id-7730-4411-SECRET"
    with monkeypatch.context() as patch:
        patch.setattr(events_ledger, "append_events", _raise)
        with pytest.raises(Interrupted):
            kyo("edit", record, "--note-file", write_file("note.txt", f"quoting {secret}"))
    with _db(project) as conn:
        note_id = conn.execute("SELECT id FROM notes ORDER BY seq DESC LIMIT 1").fetchone()[0]
        assert secret in _max_event(project)["after_json"]
    assert kyo("redact", note_id, "--why", "a consumer id").code == 0
    assert secret not in events_text(project)

    assert kyo("export", "--refresh").code == 0
    assert _doctor_findings(kyo, "ledger_gaps") == []
    assert secret not in events_text(project)


# ------------------------------------------------------------------ c5: idempotent


def test_the_remedy_on_a_complete_store_appends_nothing_and_says_so(
    kyo, project: Path, monkeypatch
) -> None:
    _new(kyo, "first")
    _interrupted_new(kyo, monkeypatch, "append_events")
    first = kyo("export", "--refresh", "--json")
    assert first.json["ledger_rows_appended"]["events"] == 1
    before = {p: p.read_bytes() for p in _export(project).rglob("*.jsonl")}

    second = kyo("export", "--refresh", "--json")
    assert second.code == 0
    assert second.json["ledger_rows_appended"] == {
        "events": 0,
        "sweep_runs": 0,
        "brief_runs": 0,
        "shift_notes": 0,
    }
    assert "re-appended 0 ledger row(s)" in kyo("export", "--refresh").out
    assert {p: p.read_bytes() for p in _export(project).rglob("*.jsonl")} == before


# ------------------------------------------------------ c6: the currency remedy's claim


def test_a_documents_only_refresh_would_leave_doctor_failing(
    kyo, project: Path, monkeypatch
) -> None:
    """c6. The repro: before KYO-404 `kyo export --refresh` rewrote documents and
    counters only, and doctor then went green over a ledger that had lost the event.
    With the ledger half of the refresh taken away again, doctor must NOT be all-ok —
    the new check still names the row — and only the full refresh clears it."""
    _new(kyo, "first")
    _interrupted_new(kyo, monkeypatch, "append_events")
    currency_findings = _doctor_findings(kyo, "db_export_currency")
    assert currency_findings, "the interruption left the counters behind too"
    assert "re-appends any ledger row" in currency_findings[0]

    with monkeypatch.context() as patch:
        patch.setattr(ledger_repair, "repair_ledgers", lambda _store: {})
        assert kyo("export", "--refresh").code == 0
    doctor = kyo("doctor", "--json")
    assert doctor.code == EXIT_DOCTOR
    assert doctor.json["checks"]["db_export_currency"]["failures"] == []
    assert doctor.json["checks"]["ledger_gaps"]["failures"]

    assert kyo("export", "--refresh").code == 0
    assert kyo("doctor").code == 0


def test_the_db_ahead_remedy_no_longer_promises_what_it_does_not_do() -> None:
    from kyosei import currency

    seam = currency.Currency(currency.CURRENCY_DB_AHEAD)
    remedy = seam.remedy()
    assert "(the database is the newer side here, so nothing is lost)" not in remedy
    assert "ledger" in remedy


# ------------------------------------------------------------- the id-set reader


def test_the_ledger_tables_cover_every_registered_ledger() -> None:
    assert set(ledger_repair.LEDGER_TABLES) == set(ledgers.LEDGER_READERS)


def test_an_event_line_the_tail_match_does_not_fit_is_still_read(
    kyo, project: Path
) -> None:
    """The fast path reads only the end of each line; a line in another key order
    falls back to a full parse rather than being skipped (or the check would report a
    gap that is not there)."""
    _new(kyo, "first")
    shard = events_ledger.event_shard_paths_in(_export(project))[-1]
    lines = shard.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[-1])
    reordered = {"seq": event["seq"], **{k: v for k, v in event.items() if k != "seq"}}
    lines[-1] = json.dumps(reordered)
    shard.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert event["id"] in ledger_repair.ledger_ids_in(
        _export(project), events_ledger.EVENTS_FILENAME
    )
    assert _doctor_findings(kyo, "ledger_gaps") == []


def test_the_check_is_registered(kyo, project: Path) -> None:
    from kyosei import doctor, doctor_dispatch

    assert doctor.CHECKS["ledger_gaps"] is doctor_dispatch.check_ledger_gaps
    assert "ledger_gaps" in doctor.CHECK_ORDER
    assert doctor.tier_of("ledger_gaps") == doctor.TIER_PROBLEM


def test_finish_mutation_is_still_reached_through_the_seam() -> None:
    # The ordering test above patches `export.*`, which `_finish_mutation` reaches by
    # module attribute; this pins that it still does.
    import inspect

    source = inspect.getsource(commands._finish_mutation)
    assert source.index("append_events") < source.index("write_exports")


