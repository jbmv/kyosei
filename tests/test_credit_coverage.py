"""KYO-646: ``kyo doctor`` finds the rows that need a credit and have none.

P12 of decision KYO-640. A human-observed verdict, a human-gate clearing or a decision
record with no credit is a NOTICE when the event that wrote it was stamped before schema
28 and a PROBLEM when it was stamped 28 or later; a credit clears either. Credits are
written here through ``Store.add_credit``, the one writer ``kyo credit`` (KYO-644) calls.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from kyosei import doctor_records, events_ledger, ledgers
from kyosei.errors import EXIT_DOCTOR
from kyosei.store import open_store

from .conftest import CREDIT_ELSEWHERE, declare_person, new_record, opt_in_transcripts

LEGACY = "credit_coverage_legacy"
NEW = "credit_coverage"
BEFORE_PEOPLE = "credit_coverage_before_people"


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


def _without_credits(project: Path) -> None:
    """KYO-644: every write path now credits a person's judgement, so a row with none --
    what this check exists to find -- is made by removing the credits the commands wrote,
    as a store written by an older build, or a merged ledger, would hold it."""
    store = open_store(project)
    try:
        with store.transaction():
            store.conn.execute("DELETE FROM credit_subjects")
            store.conn.execute("DELETE FROM credits")
            store.conn.execute("UPDATE gates SET cleared_as = NULL")
        from kyosei.export import write_export

        for row in store.conn.execute("SELECT id FROM records").fetchall():
            write_export(store, row["id"])
    finally:
        store.close()


def _human_verdict(kyo) -> tuple[str, str]:
    record = new_record(kyo, criteria=["a"])
    declare_person(kyo.project)
    result = kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed",
        "--evidence-file", "-", *CREDIT_ELSEWHERE, "--json", stdin="the person's words",
    )
    assert result.code == 0, result
    _without_credits(kyo.project)
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
    assert kyo("ungate", gate, "--withdrawn", "--why", "reviewed").code == 0
    decision = new_record(kyo, kind="decision", title="A decision")
    _without_credits(project)
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
    assert kyo("ungate", gate, "--withdrawn", "--why", "no longer matters").code == 0
    _without_credits(project)
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
    opt_in_transcripts(project)
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


def _undeclared_verdict(kyo) -> tuple[str, str]:
    """A human-observed verdict written while nobody is declared: no credit, and no refusal."""
    record = new_record(kyo, criteria=["a"])
    result = kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed",
        "--evidence-file", "-", "--json", stdin="the person's words",
    )
    assert result.code == 0, result
    return record, f"{record}.v1"


def _commit_config(project: Path, when: str) -> None:
    """Commit the project's config.yml with ``when`` as the commit's author date."""
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": when,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    subprocess.run(["git", "add", "kyosei/config.yml"], cwd=project, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "c"], cwd=project, check=True, env=env)


def _set_verdict_time(project: Path, verdict: str, stamp: str) -> None:
    store = open_store(project)
    try:
        with store.transaction():
            store.conn.execute(
                "UPDATE verifications SET created_at = ? WHERE id = ?", (stamp, verdict)
            )
        from kyosei.export import write_export

        write_export(store, store.conn.execute(
            "SELECT record_id FROM verifications WHERE id = ?", (verdict,)
        ).fetchone()["record_id"])
    finally:
        store.close()


def test_the_first_declaration_divides_the_notice_from_the_problem(kyo, project) -> None:
    """KYO-684.c1: in one store, the row from before the committed declaration is a notice
    and the row from after it is still a problem, each in words that say which it is."""
    _, before = _undeclared_verdict(kyo)
    _set_verdict_time(project, before, "2026-01-01T00:00:00.000Z")
    _, after = _human_verdict(kyo)
    _commit_config(project, "2026-01-02T00:00:00+00:00")
    result, checks = _doctor(kyo)
    assert result.code == EXIT_DOCTOR, result
    assert before in str(checks[BEFORE_PEOPLE]) and after not in str(checks[BEFORE_PEOPLE])
    assert after in str(checks[NEW]) and before not in str(checks[NEW])
    notice = " ".join(checks[BEFORE_PEOPLE]["failures"])
    assert "were written before `people:` first declared someone (2026-01-02)" in notice
    assert "bypassed" not in notice
    problem = " ".join(checks[NEW]["failures"])
    assert "after `people:` first declared someone (2026-01-02)" in problem
    assert "the enforcement was bypassed" in problem and "before anyone" not in problem


def test_a_row_only_from_before_the_declaration_leaves_doctor_exiting_0(kyo, project) -> None:
    """The reported case: a project declares its first person and its stops stay green."""
    _, verdict = _undeclared_verdict(kyo)
    declare_person(project)
    _commit_config(project, "2099-01-01T00:00:00+00:00")
    result, checks = _doctor(kyo)
    assert result.code == 0, result
    assert checks[NEW]["ok"]
    assert verdict in str(checks[BEFORE_PEOPLE])
    assert any(f"[{BEFORE_PEOPLE}]" in n for n in result.json["notices"])


def test_a_committed_declaration_is_dated_by_the_oldest_commit_that_declares(kyo, project) -> None:
    """A commit that touches config.yml without declaring anyone does not move the boundary,
    and a later edit of a file already declaring does not either."""
    _, verdict = _undeclared_verdict(kyo)
    config = project / "kyosei" / "config.yml"
    _commit_config(project, "2000-01-01T00:00:00+00:00")
    declare_person(project)
    _commit_config(project, "2099-01-01T00:00:00+00:00")
    config.write_text(config.read_text(encoding="utf-8") + "# later\n", encoding="utf-8")
    _commit_config(project, "2099-06-01T00:00:00+00:00")
    _, checks = _doctor(kyo)
    assert verdict in str(checks[BEFORE_PEOPLE])
    assert "(2099-01-01)" in str(checks[BEFORE_PEOPLE])


def test_an_uncommitted_declaration_is_dated_by_the_file(kyo, project) -> None:
    """No commit holds the declaration, so the file's modification time is the boundary."""
    _, verdict = _undeclared_verdict(kyo)
    declare_person(project)
    config = project / "kyosei" / "config.yml"
    future = 4_070_908_800  # 2099-01-01, later than the row
    os.utime(config, (future, future))
    result, checks = _doctor(kyo)
    assert result.code == 0, result
    assert verdict in str(checks[BEFORE_PEOPLE])
    past = 946_684_800  # 2000-01-01, earlier than the row
    os.utime(config, (past, past))
    result, checks = _doctor(kyo)
    assert result.code == EXIT_DOCTOR, result
    assert verdict in str(checks[NEW])


def test_the_before_people_notice_names_remedies_that_clear_it(kyo, project, tmp_path) -> None:
    """Both remedies the finding names clear the row: a credit, and restating the verdict."""
    credited, verdict = _undeclared_verdict(kyo)
    restated, other = _undeclared_verdict(kyo)
    declare_person(project)
    _commit_config(project, "2099-01-01T00:00:00+00:00")
    _, checks = _doctor(kyo)
    assert verdict in str(checks[BEFORE_PEOPLE]) and other in str(checks[BEFORE_PEOPLE])
    _attach(kyo, credited, tmp_path)
    _credit(project, credited, [("verification", verdict)])
    result = kyo(
        "verify", f"{restated}.c1", "--verdict", "met", "--method", "agent-observed",
        "--evidence-file", "-", stdin="seen by the agent",
    )
    assert result.code == 0, result
    _, checks = _doctor(kyo)
    assert checks[BEFORE_PEOPLE]["ok"], checks[BEFORE_PEOPLE]


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
