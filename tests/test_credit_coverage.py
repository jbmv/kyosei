"""KYO-646: ``kyo doctor`` finds the rows that need a credit and have none.

P12 of decision KYO-640. A human-observed verdict, a human-gate clearing or a decision
record with no credit is a NOTICE when the event that wrote it was stamped before schema
28 and a PROBLEM when it was stamped 28 or later; a credit clears either. Credits are
written here through ``Store.add_credit``, the one writer ``kyo credit`` (KYO-644) calls.
"""

from __future__ import annotations

from pathlib import Path

from kyosei import doctor_records, events_ledger, ledgers
from kyosei.errors import EXIT_DOCTOR
from kyosei.store import open_store

from .conftest import new_record

LEGACY = "credit_coverage_legacy"
NEW = "credit_coverage"


def _restamp(project: Path, version: int) -> None:
    """Rewrite every events-ledger stamp, as a ledger written by another build reads."""
    store = open_store(project)
    try:
        files = events_ledger.events_files(store)
    finally:
        store.close()
    for path in files:
        text = path.read_text(encoding="utf-8")
        for old in range(1, 40):
            text = text.replace(
                f'"schema_version": {old}, "seq"', f'"schema_version": {version}, "seq"'
            )
        path.write_text(text, encoding="utf-8")


def _human_verdict(kyo) -> tuple[str, str]:
    record = new_record(kyo, criteria=["a"])
    result = kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed",
        "--evidence-file", "-", "--json", stdin="the person's words",
    )
    assert result.code == 0, result
    return record, f"{record}.v1"


def _attach(kyo, record: str, tmp_path: Path) -> None:
    """The quote's evidence artifact, which a credit's ``quote_id`` must name."""
    quote = tmp_path / f"{record}-quote.txt"
    quote.write_text("the person's words", encoding="utf-8")
    assert kyo("attach", record, "--file", str(quote)).code == 0


def _credit(project: Path, record: str, subjects: list[tuple[str, str]], **extra) -> str:
    store = open_store(project)
    try:
        with store.transaction():
            credit = store.add_credit(
                record,
                person="jbmv",
                role=extra.pop("role", "observed"),
                quote_id=store.evidence_artifacts(record)[0]["id"],
                source_kind="claude-code",
                source_session=extra.pop("source_session", "7212f0bb-a568-49c4-9b87-d3209a201b6b"),
                source_turn="turn-1",
                transcript_check=extra.pop("transcript_check", "matched"),
                subjects=subjects,
                author="orchestrator",
                **extra,
            )
        from kyosei.export import write_export

        write_export(store, record)
        ledgers.write_allocations(store)
        return credit
    finally:
        store.close()


def _doctor(kyo):
    result = kyo("doctor", "--json")
    return result, result.json["checks"]


def _findings(checks: dict, name: str) -> list[str]:
    return checks[name]["failures"]


def test_a_verdict_written_before_schema_28_is_a_notice_and_doctor_exits_0(kyo, project) -> None:
    _human_verdict(kyo)
    _restamp(project, 27)
    result, checks = _doctor(kyo)
    assert result.code == 0, result
    assert checks[NEW]["ok"]
    assert not checks[LEGACY]["ok"]
    text = str(checks[LEGACY])
    assert "1 human-observed verdicts, 0 human-gate clearings and 0 decision records" in text
    assert "were written before credits existed (before schema_version 28)" in text
    assert "kyo verify --method agent-observed" in text
    assert any(f"[{LEGACY}]" in n for n in result.json["notices"])


def test_a_verdict_written_at_schema_28_with_no_credit_is_a_problem(kyo, project) -> None:
    record, verdict = _human_verdict(kyo)
    result, checks = _doctor(kyo)
    assert result.code == EXIT_DOCTOR, result
    assert not checks[NEW]["ok"]
    assert verdict in str(checks[NEW])
    assert checks[LEGACY]["ok"]


def test_a_credit_clears_both_findings(kyo, project) -> None:
    record, verdict = _human_verdict(kyo)
    for version, name in ((27, LEGACY), (28, NEW)):
        _restamp(project, version)
        _, checks = _doctor(kyo)
        assert not checks[name]["ok"], (version, checks[name])
    _credit(project, record, [("verification", verdict)])
    for version in (27, 28):
        _restamp(project, version)
        result, checks = _doctor(kyo)
        assert checks[NEW]["ok"] and checks[LEGACY]["ok"], (version, checks)
        assert result.code == 0, result


def test_a_gate_cleared_by_a_person_and_a_decision_are_counted_and_cleared(
    kyo, project, tmp_path
) -> None:
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review")
    gate = kyo("show", record, "--json").json["gates"][0]["id"]
    assert kyo("ungate", gate, "--why", "reviewed").code == 0
    decision = new_record(kyo, kind="decision", title="A decision")
    _restamp(project, 27)
    _, checks = _doctor(kyo)
    text = " ".join(_findings(checks, LEGACY))
    assert "0 human-observed verdicts, 1 human-gate clearings and 1 decision records" in text
    _attach(kyo, record, tmp_path)
    _attach(kyo, decision, tmp_path)
    _credit(project, record, [("gate", gate)])
    _credit(project, decision, [("record", decision)], role="decided")
    _, checks = _doctor(kyo)
    assert checks[LEGACY]["ok"]


def test_a_gate_withdrawn_names_no_person_and_is_not_counted(kyo, project, db) -> None:
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review")
    gate = kyo("show", record, "--json").json["gates"][0]["id"]
    assert kyo("ungate", gate, "--why", "no longer matters").code == 0
    conn = db()
    conn.execute("UPDATE gates SET cleared_as = 'withdrawn' WHERE id = ?", (gate,))
    conn.close()
    _restamp(project, 28)
    _, checks = _doctor(kyo)
    assert checks[NEW]["ok"] and checks[LEGACY]["ok"]


def test_the_cut_off_is_per_row_not_per_store(kyo, project) -> None:
    """One verdict stamped 27 and a later one stamped 28 land in different tiers."""
    _human_verdict(kyo)
    _restamp(project, 27)
    _, new_verdict = _human_verdict(kyo)
    result, checks = _doctor(kyo)
    assert result.code == EXIT_DOCTOR
    assert new_verdict in str(checks[NEW]["failures"])
    assert "1 human-observed verdicts" in " ".join(_findings(checks, LEGACY))


def test_a_transcript_that_has_appeared_is_noticed(kyo, project, tmp_path, monkeypatch) -> None:
    record, verdict = _human_verdict(kyo)
    session = "7212f0bb-a568-49c4-9b87-d3209a201b6b"
    credit = _credit(
        project, record, [("verification", verdict)], transcript_check="transcript-absent"
    )
    config = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    _, checks = _doctor(kyo)
    assert checks["credit_transcripts_appeared"]["ok"]
    (config / "projects" / "p").mkdir(parents=True)
    (config / "projects" / "p" / f"{session}.jsonl").write_text("{}\n", encoding="utf-8")
    result, checks = _doctor(kyo)
    assert not checks["credit_transcripts_appeared"]["ok"]
    assert credit in str(checks["credit_transcripts_appeared"])
    assert "kyo credit --recheck" in str(checks["credit_transcripts_appeared"])
    assert "credit_transcripts_appeared" in str(result.json["notices"])


def test_the_ledger_is_read_once_per_file_then_from_the_cache(kyo, project, monkeypatch) -> None:
    _human_verdict(kyo)
    _restamp(project, 27)
    reads: list[Path] = []
    real = doctor_records._shard_runs
    monkeypatch.setattr(doctor_records, "_shard_runs", lambda p: reads.append(p) or real(p))
    _doctor(kyo)
    first = len(reads)
    assert first >= 1
    _doctor(kyo)
    assert len(reads) == first


def test_a_store_with_no_uncredited_row_never_reads_the_ledger(kyo, project, monkeypatch) -> None:
    new_record(kyo, criteria=["a"])
    reads: list[Path] = []
    monkeypatch.setattr(doctor_records, "_shard_runs", lambda p: reads.append(p) or [])
    _, checks = _doctor(kyo)
    assert checks[NEW]["ok"] and checks[LEGACY]["ok"]
    assert reads == []
