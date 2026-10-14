"""``kyo redact``: evidence stops being unrecallable.

Evidence is stored verbatim and ``kyosei/export/`` is git-canonical, which is the
whole point of a provenance tool right up until the line you quoted contains an
identifier that must not be published.

The load-bearing test here is ``test_the_text_is_gone_from_the_events_ledger``.
Every mutating command writes the touched record's whole document into
``_events.jsonl``, so one piece of evidence is copied into every later event for
that record. Redacting the ``evidence_artifacts`` row alone would have been theatre.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from kyosei.contracts import REDACTION_MARKER

from .conftest import new_record

SECRET = "consumer id 4429117 seen in the queue"


def _attach(kyo, write_file, record: str, text: str = SECRET) -> str:
    result = kyo("attach", record, "--file", write_file("ev.txt", text), "--json")
    assert result.code == 0, result
    return result.json["evidence_id"]


def _export_dir(project: Path) -> Path:
    return project / "kyosei" / "export"


def _events(project: Path) -> list[dict]:
    return [json.loads(line) for line in _ledger_text(project).splitlines() if line.strip()]


def _ledger_text(project: Path) -> str:
    """Every file the events ledger is made of, concatenated (KYO-268.c2).

    Read through ``events_ledger.events_files_in`` rather than by naming ``_events.jsonl``: the
    ledger is a directory of shards from schema_version 20 on, and a test that read one
    file would be asserting "the text is gone" against a file the text was never in.
    """
    from kyosei.events_ledger import events_files_in

    return "".join(p.read_text() for p in events_files_in(_export_dir(project)))


def test_redacting_removes_the_text_from_the_store(kyo, project: Path, write_file) -> None:
    record = new_record(kyo, kind="bug", title="Saw an id")
    evidence_id = _attach(kyo, write_file, record)

    result = kyo("redact", evidence_id, "--why", "carries a consumer id", "--json")
    assert result.code == 0, result

    artifact = kyo("show", record, "--json").json["evidence_artifacts"][0]
    assert artifact["text"] == REDACTION_MARKER
    assert SECRET not in json.dumps(artifact)


def test_an_evidence_body_never_reaches_the_events_ledger_at_all(
    kyo, project: Path, write_file
) -> None:
    """KYO-268.c1, and the reason the test above it changed shape.

    This test used to assert a PRECONDITION — "the ledger holds the text" — smear the
    text across four events and prove the scrub removed it. The precondition was the
    defect: every mutating command wrote the touched record's whole document into the
    ledger, evidence bodies and all, so one artifact was copied into every later event
    for that record (541 rows in this repo when `kyo redact` was written; 55.0 MB of a
    224.1 MB ledger by the time KYO-268 measured it).

    From schema_version 19 a snapshot names each artifact by id and sha256 and carries no
    body, so the copies the scrub was written to chase are never made. The scrub is not
    redundant — it still has to reach the lines older builds wrote, which
    ``test_the_scrub_still_reaches_a_body_an_older_build_wrote`` below proves against a
    real one — but on a ledger this build wrote there is nothing for it to find.
    """
    record = new_record(kyo, kind="bug", title="Saw an id")
    evidence_id = _attach(kyo, write_file, record)
    for i in range(3):
        kyo("edit", record, "--note-file", write_file(f"n{i}.txt", f"note {i}"))

    assert SECRET not in _ledger_text(project)
    referenced = [
        artifact
        for event in _events(project)
        for side in (event.get("before"), event.get("changed"), event.get("after"))
        if isinstance(side, dict)
        for artifact in side.get("evidence_artifacts") or []
    ]
    assert referenced, "the snapshots stopped mentioning the artifact at all"
    assert all(set(artifact) == {"id", "sha256"} for artifact in referenced), referenced
    assert any(artifact["id"] == evidence_id for artifact in referenced)

    assert kyo("redact", evidence_id, "--why", "carries a consumer id").code == 0
    assert SECRET not in _ledger_text(project)


def test_the_scrub_still_reaches_a_body_an_older_build_wrote(
    kyo, project: Path, write_file
) -> None:
    """KYO-268.c1: the ledger is append-only, so every store that has ever run an older
    build still carries lines with the bodies in them — forever. A change that stopped
    WRITING the copies and also stopped FINDING them would have quietly retired
    `kyo redact`'s only real job on every existing store at once.

    The legacy line is built the way an older build built it: a real event, read back
    from the ledger, with the artifact's body put back into its snapshot. Written into a
    monolithic ``_events.jsonl`` beside the shards, which is also exactly the
    pre-migration state (KYO-268.c3) a consumer's export is in when it first runs this
    build.
    """
    record = new_record(kyo, kind="bug", title="Saw an id")
    evidence_id = _attach(kyo, write_file, record)

    # An older build wrote `export.record_document` straight into the snapshot, bodies
    # and all, so that document IS the legacy shape — taken from the export rather than
    # hand-written, so this stays a real old line and not an approximation of one.
    document = json.loads(
        (_export_dir(project) / f"{record}.json").read_text(encoding="utf-8")
    )
    assert any(a["text"] == SECRET for a in document["evidence_artifacts"])
    legacy = [
        event
        for event in _events(project)
        if isinstance(event.get("after"), dict)
        and any(
            artifact["id"] == evidence_id
            for artifact in event["after"].get("evidence_artifacts") or []
        )
    ][-1]
    legacy["after"] = document
    legacy["id"] = legacy["id"] + "-legacy"
    monolithic = _export_dir(project) / "_events.jsonl"
    monolithic.write_text(json.dumps(legacy, sort_keys=True) + "\n", encoding="utf-8")
    assert SECRET in _ledger_text(project), "precondition: the legacy line holds the text"

    result = kyo("redact", evidence_id, "--why", "carries a consumer id", "--json")
    assert result.code == 0, result
    assert result.json["ledger_lines_scrubbed"] >= 1
    assert SECRET not in _ledger_text(project)
    scrubbed = json.loads(monolithic.read_text())
    artifact = next(
        a for a in scrubbed["after"]["evidence_artifacts"] if a["id"] == evidence_id
    )
    assert artifact["text"] == REDACTION_MARKER
    assert artifact["sha256"] == hashlib.sha256(REDACTION_MARKER.encode()).hexdigest()
    # The legacy line keeps its legacy SHAPE: the scrub replaces the body, it does not
    # migrate the line to the new one. Rewriting history's format is what an append-only
    # ledger must not do, and `kyo export --migrate-events` — which moves bytes and
    # changes none — is the only thing that touches these lines otherwise.
    assert "author" in artifact and "created_at" in artifact


def test_the_redaction_event_carries_no_before_document(kyo, project: Path, write_file) -> None:
    """A `before` document is exactly the payload being removed, so the redaction
    must not write one."""
    record = new_record(kyo, kind="bug", title="Saw an id")
    evidence_id = _attach(kyo, write_file, record)
    kyo("redact", evidence_id, "--why", "carries a consumer id")

    redaction = [e for e in _events(project) if e["command"].startswith(f"redact {evidence_id}")]
    assert redaction, "the redaction is not in the ledger"
    # KYO-418: neither prior form -- no whole `before` and no `changed` map.
    assert redaction[0].get("before") is None and redaction[0].get("changed") is None
    assert SECRET not in json.dumps(redaction[0])


def test_the_audit_trail_records_that_it_happened(kyo, project: Path, write_file) -> None:
    """The trail records the redaction rather than hiding it."""
    record = new_record(kyo, kind="bug", title="Saw an id")
    evidence_id = _attach(kyo, write_file, record)
    kyo("redact", evidence_id, "--why", "carries a consumer id")

    artifact = kyo("show", record, "--json").json["evidence_artifacts"][0]
    assert artifact["redacted_at"]
    assert artifact["redacted_why"] == "carries a consumer id"
    commands = [e["command"] for e in _events(project)]
    assert any("carries a consumer id" in c for c in commands)


def test_the_original_digest_is_not_kept(kyo, project: Path, write_file) -> None:
    """Deliberate, and the reason is not obvious: a sha256 of a short identifier
    is brute-forceable in seconds, so keeping the original digest in a
    git-canonical export would republish the value the redaction exists to
    remove."""
    import hashlib

    record = new_record(kyo, kind="bug", title="Saw an id")
    evidence_id = _attach(kyo, write_file, record)
    original = hashlib.sha256(SECRET.encode()).hexdigest()
    assert original in _ledger_text(project), "precondition: the digest is in the ledger"

    kyo("redact", evidence_id, "--why", "carries a consumer id")

    artifact = kyo("show", record, "--json").json["evidence_artifacts"][0]
    assert artifact["sha256"] == hashlib.sha256(REDACTION_MARKER.encode()).hexdigest()
    assert original not in _ledger_text(project)
    assert original not in json.dumps(artifact)


def test_redacting_needs_a_reason(kyo, project: Path, write_file) -> None:
    record = new_record(kyo, kind="bug", title="Saw an id")
    evidence_id = _attach(kyo, write_file, record)

    result = kyo("redact", evidence_id)
    assert result.code != 0
    assert "--why" in result.err
    assert kyo("show", record, "--json").json["evidence_artifacts"][0]["text"] == SECRET


def test_redacting_twice_is_refused(kyo, project: Path, write_file) -> None:
    record = new_record(kyo, kind="bug", title="Saw an id")
    evidence_id = _attach(kyo, write_file, record)
    assert kyo("redact", evidence_id, "--why", "first").code == 0

    result = kyo("redact", evidence_id, "--why", "again")
    assert result.code != 0
    assert "already redacted" in result.err


def test_an_unknown_evidence_id_names_where_to_find_one(kyo, project: Path) -> None:
    result = kyo("redact", "TST-1.e9", "--why", "nope")
    assert result.code != 0
    assert "no evidence" in result.err
    assert "kyo show" in result.err


def test_verify_evidence_can_be_redacted_too(kyo, project: Path, write_file) -> None:
    """`kyo verify --evidence-file` is the other door secrets come through, and
    the natural one: quoting a log line as evidence is the whole point of the
    tool."""
    record = new_record(kyo, kind="bug", title="Saw an id", criteria=["it happened"])
    result = kyo(
        "verify",
        f"{record}.c1",
        "--verdict",
        "met",
        "--method",
        "human-observed",
        "--evidence-file",
        write_file("v.txt", SECRET),
        "--json",
    )
    assert result.code == 0, result
    artifact = kyo("show", record, "--json").json["evidence_artifacts"][0]

    assert kyo("redact", artifact["id"], "--why", "carries a consumer id").code == 0
    assert SECRET not in _ledger_text(project)
    assert SECRET not in (project / "kyosei" / "export" / f"{record}.json").read_text()


def test_doctor_catches_a_half_applied_redaction(kyo, project: Path, write_file, db) -> None:
    """A half-applied redaction is worse than none, because it reads as done."""
    record = new_record(kyo, kind="bug", title="Saw an id")
    evidence_id = _attach(kyo, write_file, record)
    conn = db()
    conn.execute(
        "UPDATE evidence_artifacts SET redacted_at = '2026-01-01T00:00:00.000Z',"
        " redacted_why = 'claimed' WHERE id = ?",
        (evidence_id,),
    )
    conn.close()

    result = kyo("doctor")
    assert result.code != 0
    assert "redactions" in result.err
    assert "still holds its text" in result.err


def test_the_store_rebuilds_and_stays_clean_after_a_redaction(
    kyo, project: Path, write_file
) -> None:
    record = new_record(kyo, kind="bug", title="Saw an id")
    evidence_id = _attach(kyo, write_file, record)
    kyo("redact", evidence_id, "--why", "carries a consumer id")

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    artifact = kyo("show", record, "--json").json["evidence_artifacts"][0]
    assert artifact["text"] == REDACTION_MARKER
    assert artifact["redacted_why"] == "carries a consumer id"
    assert kyo("doctor").code == 0


def test_the_command_says_what_it_did_not_do(kyo, project: Path, write_file) -> None:
    """It cannot un-publish a value already in a commit, and a tool that sounds
    like it did the job when it did not is worse than no tool."""
    record = new_record(kyo, kind="bug", title="Saw an id")
    evidence_id = _attach(kyo, write_file, record)
    result = kyo("redact", evidence_id, "--why", "carries a consumer id")
    assert "history" in result.out.lower()


# ------------------------------------------- KYO-25: the other three prose stores


def _shift_ledger(project: Path) -> str:
    return (project / "kyosei" / "export" / "_shift_notes.jsonl").read_text()


def test_a_shift_note_can_be_redacted(kyo, project: Path, write_file) -> None:
    """The reason `kyo redact` covering evidence alone was not enough: a shift note
    is where a controller quotes a log line while thinking out loud, so it is not an
    edge case."""
    result = kyo("note", "--shift", "--file", write_file("s.txt", SECRET), "--json")
    assert result.code == 0, result
    note_id = result.json["shift_note_id"]
    assert SECRET in _shift_ledger(project), "precondition: the shift ledger holds it"
    assert SECRET in _ledger_text(project), "precondition: so does the event ledger"

    assert kyo("redact", note_id, "--why", "quoted a log line with an id").code == 0
    assert SECRET not in _shift_ledger(project)
    assert SECRET not in _ledger_text(project)


def test_a_redacted_shift_note_stops_appearing_in_the_brief(kyo, project: Path, write_file) -> None:
    """`kyo brief` reads shift notes straight from the table, so a redaction that
    stopped at the ledgers would still recite the text at every session start."""
    note_id = kyo(
        "note", "--shift", "--file", write_file("s.txt", SECRET), "--json"
    ).json["shift_note_id"]
    kyo("redact", note_id, "--why", "quoted a log line with an id")

    assert SECRET not in kyo("brief").out


def test_a_claim_can_be_redacted(kyo, project: Path, write_file) -> None:
    """`kyo claim --file` is prose stored verbatim by the same argument."""
    record = new_record(kyo, kind="bug", title="Saw an id")
    claim_id = kyo(
        "claim", record, "--level", "asserted", "--method", "read the queue",
        "--file", write_file("c.txt", SECRET), "--json",
    ).json["claim_id"]
    assert SECRET in _ledger_text(project)

    assert kyo("redact", claim_id, "--why", "statement quoted an id").code == 0
    assert SECRET not in _ledger_text(project)
    claim = kyo("show", record, "--json").json["claims"][0]
    assert claim["statement"] == REDACTION_MARKER
    assert claim["redacted_why"] == "statement quoted an id"


def test_a_note_can_be_redacted(kyo, project: Path, write_file) -> None:
    record = new_record(kyo, kind="bug", title="Saw an id")
    kyo("edit", record, "--note-file", write_file("n.txt", SECRET))
    note_id = kyo("show", record, "--json").json["notes"][0]["id"]

    assert kyo("redact", note_id, "--why", "note quoted an id").code == 0
    assert SECRET not in _ledger_text(project)
    assert kyo("show", record, "--json").json["notes"][0]["text"] == REDACTION_MARKER


def test_every_redactable_store_is_reachable_from_the_cli(kyo, project: Path, write_file) -> None:
    """The table in contracts.REDACTABLE is the point: adding a prose store to the
    schema without adding it there is the mistake it exists to make visible. If a
    kind is listed, `kyo redact` must actually reach it."""
    from kyosei.contracts import REDACTABLE

    record = new_record(kyo, kind="bug", title="All four", criteria=["a"])
    made = {
        "evidence": _attach(kyo, write_file, record, "evidence text"),
        "claim": kyo(
            "claim", record, "--level", "asserted", "--method", "m",
            "--file", write_file("c2.txt", "claim text"), "--json",
        ).json["claim_id"],
        "shift-note": kyo(
            "note", "--shift", "--file", write_file("s2.txt", "shift text"), "--json"
        ).json["shift_note_id"],
    }
    kyo("edit", record, "--note-file", write_file("n2.txt", "note text"))
    made["note"] = kyo("show", record, "--json").json["notes"][0]["id"]

    assert set(made) == set(REDACTABLE), "a redactable kind has no test here"
    for kind, target in made.items():
        result = kyo("redact", target, "--why", f"redacting a {kind}", "--json")
        assert result.code == 0, (kind, result)
        assert result.json["target_kind"] == kind


def test_an_id_that_names_nothing_redactable_says_what_is(kyo, project: Path) -> None:
    record = new_record(kyo, kind="bug", title="Not redactable", criteria=["a"])
    # A criterion became a target in KYO-426; a bare record id names no single field.
    result = kyo("redact", record, "--why", "a record is not one field")
    assert result.code != 0
    for shape in ("RECORD.eN", "RECORD.mN", "RECORD.nN", "PREFIX-SN"):
        assert shape in result.err


def test_the_redaction_vocabulary_names_the_command_that_writes_each_shape(
    kyo, project: Path
) -> None:
    """KYO-131.c1: an id shape is named here together with its write path.

    This message and `kyo redact --help` are where a consumer met `RECORD.nN`,
    and neither said what produced one. They concluded the store could not hold
    such an object and proposed deleting the shape from this vocabulary — which
    would have removed the documented target for something the store does hold,
    and which `test_every_redactable_kind_can_be_redacted` above has been
    creating with `kyo edit --note-file` the whole time. The tool's own tests
    used the write path while no surface a controller reads ever named it.

    Asserted per shape rather than against the whole sentence, so that rewording
    stays free and a *dropped* attribution does not.
    """
    record = new_record(kyo, kind="bug", title="Not redactable", criteria=["a"])
    # A criterion became a target in KYO-426; a bare record id names no single field.
    result = kyo("redact", record, "--why", "a record is not one field")
    assert result.code != 0
    for shape, writer in (
        ("RECORD.eN", "kyo attach"),
        ("RECORD.mN", "kyo claim"),
        ("RECORD.nN", "kyo edit --note-file"),
        ("PREFIX-SN", "kyo note --shift"),
    ):
        assert writer in result.err, (
            f"the refusal names {shape} without naming `{writer}`, the command that "
            "writes one. A reader who cannot find the write path concludes the object "
            f"does not exist — the defect KYO-131 was filed for:\n{result.err}"
        )


def test_doctor_catches_a_half_applied_shift_note_redaction(
    kyo, project: Path, write_file, db
) -> None:
    note_id = kyo(
        "note", "--shift", "--file", write_file("s.txt", SECRET), "--json"
    ).json["shift_note_id"]
    conn = db()
    conn.execute(
        "UPDATE shift_notes SET redacted_at = '2026-01-01T00:00:00.000Z',"
        " redacted_why = 'claimed' WHERE id = ?",
        (note_id,),
    )
    conn.close()

    result = kyo("doctor")
    assert result.code != 0
    assert "shift-note" in result.err
    assert "still holds its text" in result.err


def test_redacted_prose_survives_a_rebuild_in_every_store(kyo, project: Path, write_file) -> None:
    record = new_record(kyo, kind="bug", title="All four", criteria=["a"])
    evidence_id = _attach(kyo, write_file, record, "evidence text")
    claim_id = kyo(
        "claim", record, "--level", "asserted", "--method", "m",
        "--file", write_file("c3.txt", "claim text"), "--json",
    ).json["claim_id"]
    note_ledger_id = kyo(
        "note", "--shift", "--file", write_file("s3.txt", "shift text"), "--json"
    ).json["shift_note_id"]
    for target in (evidence_id, claim_id, note_ledger_id):
        assert kyo("redact", target, "--why", "cleanup").code == 0

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    shown = kyo("show", record, "--json")
    assert shown.json["evidence_artifacts"][0]["text"] == REDACTION_MARKER
    assert shown.json["claims"][0]["statement"] == REDACTION_MARKER
    assert shown.json["claims"][0]["redacted_why"] == "cleanup"
    assert kyo("doctor").code == 0


# ------------------------ KYO-28: copies embedded in briefs and reports


def test_redacting_a_claim_clears_the_frozen_dispatch_brief(
    kyo, git_project: Path, write_file
) -> None:
    """A quoted log line lands in more than one place, *inside* the store as well as
    outside it. Brief generation renders claim statements into the
    frozen brief, so redacting the claim left the statement verbatim in every
    dispatch that had cited it, in the export, in git."""
    record = new_record(kyo, kind="task", title="Cited", criteria=["a"])
    claim_id = kyo(
        "claim", record, "--level", "asserted", "--method", "read it",
        "--file", write_file("cl.txt", SECRET), "--json",
    ).json["claim_id"]
    assert kyo("dispatch", record).code == 0
    brief = kyo("show", record, "--json").json["dispatches"][0]["brief"]
    assert SECRET in brief, "precondition: the brief quotes the claim"

    assert kyo("redact", claim_id, "--why", "carried an id").code == 0

    shown = kyo("show", record, "--json")
    assert SECRET not in shown.json["dispatches"][0]["brief"]
    assert SECRET not in (git_project / "kyosei" / "export" / f"{record}.json").read_text()
    assert SECRET not in _ledger_text(git_project)


def test_the_export_on_disk_is_rewritten_not_just_the_database(
    kyo, git_project: Path, write_file
) -> None:
    """`kyosei/export/` is what git tracks, so a fix that stopped at the db would
    leave the value in the only copy that matters."""
    record = new_record(kyo, kind="task", title="Cited", criteria=["a"])
    claim_id = kyo(
        "claim", record, "--level", "asserted", "--method", "read it",
        "--file", write_file("cl.txt", SECRET), "--json",
    ).json["claim_id"]
    kyo("dispatch", record)
    kyo("redact", claim_id, "--why", "carried an id")

    on_disk = (git_project / "kyosei" / "export" / f"{record}.json").read_text()
    assert SECRET not in on_disk
    assert kyo("doctor").code == 0, "the rewritten export must still match the database"


def test_a_short_value_skips_the_global_pass_and_says_so(
    kyo, git_project: Path, write_file
) -> None:
    """A candidate value can be two digits long. Replacing "42"
    everywhere would corrupt the store far worse than leaving it, so the pass is
    skipped below a length threshold — and the command says so rather than
    implying the value is gone."""
    record = new_record(kyo, kind="task", title="Short", criteria=["a"])
    claim_id = kyo(
        "claim", record, "--level", "asserted", "--method", "read it",
        "--file", write_file("s.txt", "42"), "--json",
    ).json["claim_id"]

    result = kyo("redact", claim_id, "--why", "short value", "--json")
    assert result.code == 0, result
    assert result.json["global_scrub_ran"] is False
    assert result.json["embedded_copies_removed"] == 0


def test_the_skipped_notice_is_printed(kyo, git_project: Path, write_file) -> None:
    record = new_record(kyo, kind="task", title="Short", criteria=["a"])
    claim_id = kyo(
        "claim", record, "--level", "asserted", "--method", "read it",
        "--file", write_file("s2.txt", "42"), "--json",
    ).json["claim_id"]
    result = kyo("redact", claim_id, "--why", "short value")
    assert "SKIPPED" in result.out
    assert "corrupt unrelated text" in result.out


def test_a_long_value_reports_that_the_pass_ran(kyo, git_project: Path, write_file) -> None:
    record = new_record(kyo, kind="task", title="Long", criteria=["a"])
    claim_id = kyo(
        "claim", record, "--level", "asserted", "--method", "read it",
        "--file", write_file("l.txt", SECRET), "--json",
    ).json["claim_id"]
    result = kyo("redact", claim_id, "--why", "carried an id", "--json")
    assert result.json["global_scrub_ran"] is True


# ------------------------------------ KYO-405: retry, the events table, the residue


def _make_redactable(kyo, write_file, kind: str) -> tuple[str, str | None]:
    """One item of ``kind`` holding SECRET, plus a later mutation of its record so the
    ledger carries more than one copy; returns ``(item id, record id or None)``."""
    if kind == "shift-note":
        result = kyo("note", "--shift", "--file", write_file("s.txt", SECRET), "--json")
        assert result.code == 0, result
        return result.json["shift_note_id"], None
    record = new_record(kyo, kind="bug", title="Saw an id")
    if kind == "evidence":
        item = _attach(kyo, write_file, record)
    elif kind == "claim":
        item = kyo(
            "claim", record, "--level", "asserted", "--method", "read the queue",
            "--file", write_file("c.txt", SECRET), "--json",
        ).json["claim_id"]
    else:
        kyo("edit", record, "--note-file", write_file("n.txt", SECRET))
        item = kyo("show", record, "--json").json["notes"][0]["id"]
    assert kyo("edit", record, "--title", "Saw an id, again").code == 0
    return item, record


def _original_digest() -> str:
    return hashlib.sha256(SECRET.encode("utf-8")).hexdigest()


def _db_events_carrying(db, needle: str) -> int:
    conn = db()
    try:
        return conn.execute(
            "SELECT count(*) FROM events WHERE instr(coalesce(before_json, ''), ?) > 0"
            " OR instr(coalesce(after_json, ''), ?) > 0",
            (needle, needle),
        ).fetchone()[0]
    finally:
        conn.close()


def _run_named_remedy(kyo, finding: str):
    """Run the `kyo redact ...` command a doctor finding names, verbatim."""
    import re
    import shlex

    match = re.search(r"`(kyo redact [^`]+)`", finding)
    assert match, f"the finding names no `kyo redact` rerun: {finding}"
    return kyo(*shlex.split(match.group(1))[1:])


@pytest.mark.parametrize("kind", ["evidence", "claim", "note", "shift-note"])
def test_an_interrupted_scrub_is_caught_by_doctor_and_finished_by_the_rerun_it_names(
    kyo, project: Path, write_file, monkeypatch, kind: str
) -> None:
    """KYO-405.c1/c2. The row commits first and everything after
    COMMIT can be interrupted; the rerun used to be refused with "already redacted" and
    doctor passed, so the ledger kept the text or the digest with nothing to say so."""
    from kyosei import events_ledger

    item, _record = _make_redactable(kyo, write_file, kind)

    def interrupted(*_args, **_kwargs):
        raise OSError("simulated interruption after COMMIT")

    with monkeypatch.context() as patch:
        patch.setattr(events_ledger, "append_events", interrupted)
        # KYO-421: reported by `cli.main` on one line, exit 1, rather than escaping.
        died = kyo("redact", item, "--why", "quoted an id")
        assert died.code == 1 and "OSError: simulated interruption" in died.err, died

    ledger = _ledger_text(project)
    assert SECRET in ledger or _original_digest() in ledger, "precondition: residue"

    doctor = kyo("doctor")
    assert doctor.code != 0
    assert "redactions" in doctor.err
    finding = next(line for line in doctor.err.splitlines() if item in line and "survives" in line)

    rerun = _run_named_remedy(kyo, finding)
    assert rerun.code == 0, rerun
    assert "already redacted" not in rerun.err
    assert "retried" in rerun.out

    ledger = _ledger_text(project)
    assert SECRET not in ledger
    assert _original_digest() not in ledger
    assert any(e["command"].startswith(f"redact {item} because:") for e in _events(project))
    if kind == "shift-note":
        assert SECRET not in _shift_ledger(project)
    assert kyo("doctor").code == 0, kyo("doctor")


def test_a_retry_still_refuses_a_different_reason(kyo, project: Path, write_file) -> None:
    """The retry finishes one redaction; a new reason would rewrite its audit trail."""
    item, _record = _make_redactable(kyo, write_file, "claim")
    assert kyo("redact", item, "--why", "first").code == 0
    assert kyo("redact", item, "--why", "first").code == 0
    refused = kyo("redact", item, "--why", "second")
    assert refused.code != 0
    assert "already redacted" in refused.err


@pytest.mark.parametrize("kind", ["evidence", "claim", "note", "shift-note"])
def test_the_database_events_table_keeps_neither_the_text_nor_the_digest(
    kyo, project: Path, write_file, db, kind: str
) -> None:
    """KYO-405.c3: `events.before_json`/`after_json` are a copy of
    the ledger's snapshots that every copy of kyosei.db carries, and nothing updated
    them — including rows written before the redaction."""
    item, _record = _make_redactable(kyo, write_file, kind)
    carried_before = _db_events_carrying(db, SECRET) + _db_events_carrying(db, _original_digest())
    assert carried_before, "precondition: the events table holds the text or the digest"

    assert kyo("redact", item, "--why", "quoted an id").code == 0

    assert _db_events_carrying(db, SECRET) == 0
    assert _db_events_carrying(db, _original_digest()) == 0


def test_doctor_fails_on_an_events_row_that_still_carries_the_text(
    kyo, project: Path, write_file, db
) -> None:
    """KYO-405.c2: the events-table half of the check, and its remedy clearing it."""
    item, _record = _make_redactable(kyo, write_file, "shift-note")
    assert kyo("redact", item, "--why", "quoted an id").code == 0
    conn = db()
    conn.execute(
        "UPDATE events SET after_json = ? WHERE instr(after_json, ?) > 0"
        " AND command = 'note --shift'",
        (json.dumps({"shift_note_id": item, "text": SECRET}, sort_keys=True), item),
    )
    conn.close()
    assert _db_events_carrying(db, SECRET) == 1, "precondition: one row put back"

    doctor = kyo("doctor")
    assert doctor.code != 0
    finding = next(line for line in doctor.err.splitlines() if item in line and "survives" in line)
    assert "`events` table" in finding

    assert _run_named_remedy(kyo, finding).code == 0
    assert _db_events_carrying(db, SECRET) == 0
    assert kyo("doctor").code == 0


def test_a_copy_left_in_a_record_field_is_named_and_not_reported_as_success(
    kyo, project: Path, write_file
) -> None:
    """KYO-405.c4: the same text quoted in the record's own
    description survived, and the output read as a clean success."""
    record = new_record(kyo, kind="bug", title="Saw an id")
    assert kyo(
        "edit", record, "--description-file", write_file("d.txt", f"Root cause: {SECRET}.")
    ).code == 0
    evidence_id = _attach(kyo, write_file, record)

    result = kyo("redact", evidence_id, "--why", "carries a consumer id", "--json")
    assert result.code == 0, result
    assert result.json["complete"] is False
    places = {(p["table"], p["column"]): p for p in result.json["surviving_occurrences"]}
    assert record in places[("records", "description")]["record_ids"]

    text = _redact_again_as_text(kyo, write_file, record)
    assert "INCOMPLETE" in text
    assert f"records.description on {record}" in text


def _redact_again_as_text(kyo, write_file, record: str) -> str:
    """A second artifact with the same text, redacted without --json: the text form."""
    evidence_id = _attach(kyo, write_file, record)
    result = kyo("redact", evidence_id, "--why", "carries a consumer id")
    assert result.code == 0, result
    return result.out


def test_a_clean_redaction_reports_complete(kyo, project: Path, write_file) -> None:
    record = new_record(kyo, kind="bug", title="Saw an id")
    evidence_id = _attach(kyo, write_file, record)
    result = kyo("redact", evidence_id, "--why", "carries a consumer id", "--json")
    assert result.json["complete"] is True
    assert result.json["surviving_occurrences"] == []
    assert "INCOMPLETE" not in kyo("redact", evidence_id, "--why", "carries a consumer id").out


def test_the_redaction_event_reaches_the_ledger_before_the_exports(
    kyo, project: Path, write_file, monkeypatch
) -> None:
    """KYO-405.c7, the KYO-404 ordering: a crash while the documents are written must
    not leave the redaction event in kyosei.db only."""
    from kyosei import export

    record = new_record(kyo, kind="bug", title="Saw an id")
    evidence_id = _attach(kyo, write_file, record)

    def crash(*_args, **_kwargs):
        raise OSError("simulated crash writing exports")

    with monkeypatch.context() as patch:
        patch.setattr(export, "write_exports", crash)
        # KYO-421: reported by `cli.main` on one line, exit 1, rather than escaping.
        died = kyo("redact", evidence_id, "--why", "carries a consumer id")
        assert died.code == 1 and "OSError: simulated crash" in died.err, died

    assert any(
        e["command"].startswith(f"redact {evidence_id} because:") for e in _events(project)
    )


def test_doctor_reads_the_ledger_once_for_every_redacted_id(
    kyo, project: Path, write_file, monkeypatch
) -> None:
    """KYO-405 follow-up: `check_redactions` runs from the Stop
    hook, and asking `redaction_residue` to find each id's shards itself re-read the whole
    ledger per redacted id — 0.85 s of a hook run on this repository's 266 MB ledger.
    It must hand each id the shards `shards_naming` found in its one pass."""
    from kyosei import ledger_scrub

    for title in ("one", "two"):
        record = new_record(kyo, kind="bug", title=title)
        evidence_id = _attach(kyo, write_file, record, text=f"{SECRET} {title}")
        assert kyo("redact", evidence_id, "--why", "leaked").code == 0

    seen: list[object] = []
    real = ledger_scrub.redaction_residue

    def recording(*args, **kwargs):
        seen.append(kwargs.get("paths"))
        return real(*args, **kwargs)

    monkeypatch.setattr(ledger_scrub, "redaction_residue", recording)
    assert kyo("doctor").code == 0
    assert len(seen) == 2, seen
    assert all(paths is not None for paths in seen), "each id re-read the whole ledger"
    assert all(paths for paths in seen), "the redaction events name the id, so a shard does"


# --- KYO-426: a record's own title, description and criteria as redaction targets ------


def _export_files_carrying(project: Path, needle: str) -> list[str]:
    """Every file under the git-canonical export that still holds ``needle``."""
    return sorted(
        str(path.relative_to(_export_dir(project)))
        for path in _export_dir(project).rglob("*")
        if path.is_file() and needle in path.read_text(encoding="utf-8", errors="replace")
    )


def _record_with_secret_in(kyo, write_file, field: str) -> tuple[str, str]:
    """A record holding SECRET in ``field``, mutated once more so the ledger carries
    several snapshots of it; returns ``(record id, redaction target id)``."""
    if field == "title":
        record = new_record(kyo, kind="bug", title=SECRET)
        target = f"{record}.title"
    elif field == "criterion":
        record = new_record(kyo, kind="bug", title="Saw an id", criteria=[SECRET])
        target = f"{record}.c1"
    else:
        record = new_record(kyo, kind="bug", title="Saw an id")
        assert kyo(
            "edit", record, "--description-file", write_file("d.txt", f"Root cause: {SECRET}.")
        ).code == 0
        target = f"{record}.description"
    assert kyo("edit", record, "--note-file", write_file("n.txt", "a later mutation")).code == 0
    return record, target


@pytest.mark.parametrize("field", ["title", "description", "criterion"])
def test_a_record_field_is_a_redaction_target_everywhere_it_is_stored(
    kyo, project: Path, write_file, db, field: str
) -> None:
    """KYO-426.c1: the db, the record's export and every ledger line that quoted it."""
    record, target = _record_with_secret_in(kyo, write_file, field)
    assert SECRET in _ledger_text(project), "precondition: the ledger quotes it"
    assert _db_events_carrying(db, SECRET), "precondition: so does the events table"

    result = kyo("redact", target, "--why", "quoted a consumer id", "--json")
    assert result.code == 0, result
    assert result.json["target_kind"] == field
    assert result.json["complete"] is True, result.json["surviving_occurrences"]

    shown = kyo("show", record, "--json").json
    value = shown["criteria"][0]["text"] if field == "criterion" else shown[field]
    assert REDACTION_MARKER in value and SECRET not in value
    assert SECRET not in json.dumps(shown)
    assert _export_files_carrying(project, SECRET) == []
    assert _db_events_carrying(db, SECRET) == 0
    redactions = [e for e in _events(project) if e["command"].startswith(f"redact {target} ")]
    assert len(redactions) == 1
    assert redactions[0].get("before") is None and redactions[0].get("changed") is None


def test_a_redacted_criterion_keeps_its_id_and_state(kyo, project: Path, write_file) -> None:
    """KYO-58: criterion text is immutable after a verdict, so the redaction replaces it
    with the marker rather than restating it; nothing else about the criterion moves."""
    record, target = _record_with_secret_in(kyo, write_file, "criterion")
    before = kyo("show", record, "--json").json["criteria"][0]
    assert kyo("redact", target, "--why", "quoted a consumer id").code == 0
    after = kyo("show", record, "--json").json["criteria"][0]
    assert after["text"] == REDACTION_MARKER
    assert (after["id"], after["state"]) == (before["id"], before["state"])


def test_an_edit_after_a_description_redaction_copies_no_old_text(
    kyo, project: Path, write_file
) -> None:
    """KYO-426.c2: the defect as filed — an edit after the redaction wrote the old text
    into its before-snapshot, where a rerun could no longer match it."""
    record, target = _record_with_secret_in(kyo, write_file, "description")
    assert kyo("redact", target, "--why", "quoted a consumer id").code == 0
    assert kyo(
        "edit", record, "--description-file", write_file("d2.txt", "Root cause: a queue id.")
    ).code == 0

    edits = [e for e in _events(project) if e["command"] == f"edit {record}"]
    last = edits[-1]
    # KYO-418: the prior description is in `changed` on a line written from 25 on.
    assert last["changed"]["description"] == REDACTION_MARKER, last["changed"]
    assert SECRET not in json.dumps(last)
    assert SECRET not in _ledger_text(project)
    assert kyo("show", record, "--json").json["description"] == "Root cause: a queue id."


def test_edit_first_redact_second_reaches_the_version_the_edit_replaced(
    kyo, project: Path, write_file, db
) -> None:
    """KYO-426: the old workaround's order still works — every version a snapshot holds
    is in scope, not only the value standing now."""
    record, target = _record_with_secret_in(kyo, write_file, "description")
    assert kyo(
        "edit", record, "--description-file", write_file("d2.txt", "Root cause: a queue id.")
    ).code == 0
    assert SECRET in _ledger_text(project), "precondition: the replaced value survives"

    assert kyo("redact", target, "--why", "quoted a consumer id").code == 0
    assert SECRET not in _ledger_text(project)
    assert _db_events_carrying(db, SECRET) == 0
    assert _export_files_carrying(project, SECRET) == []


def test_a_later_edit_is_not_scrubbed_by_an_earlier_field_redaction(
    kyo, project: Path, write_file
) -> None:
    """The scope is every snapshot up to the redaction's own event: a description written
    afterwards is new text, and neither a rerun nor doctor treats it as residue."""
    record, target = _record_with_secret_in(kyo, write_file, "description")
    assert kyo("redact", target, "--why", "quoted a consumer id").code == 0
    fresh = "Root cause: the queue dropped a message on retry."
    assert kyo("edit", record, "--description-file", write_file("d2.txt", fresh)).code == 0

    assert kyo("doctor").code == 0, kyo("doctor")
    assert fresh in _ledger_text(project)
    assert kyo("show", record, "--json").json["description"] == fresh


def test_doctor_is_clean_after_a_field_redaction(kyo, project: Path, write_file) -> None:
    """KYO-426.c3: nothing to report for the redacted field once the scrub ran."""
    _record, target = _record_with_secret_in(kyo, write_file, "description")
    assert kyo("redact", target, "--why", "quoted a consumer id").code == 0
    doctor = kyo("doctor")
    assert doctor.code == 0, doctor


@pytest.mark.parametrize("field", ["title", "description", "criterion"])
def test_an_interrupted_field_scrub_is_caught_by_doctor_and_finished_by_its_rerun(
    kyo, project: Path, write_file, monkeypatch, field: str
) -> None:
    """KYO-426.c3: doctor reads a field redaction back from its event and names the
    residue up to it; the rerun it names finishes the scrub. Fails if the field target
    path is taken out of either `kyo redact` or `check_redactions`."""
    from kyosei import events_ledger

    _record, target = _record_with_secret_in(kyo, write_file, field)

    def interrupted(*_args, **_kwargs):
        raise OSError("simulated interruption after COMMIT")

    with monkeypatch.context() as patch:
        patch.setattr(events_ledger, "append_events", interrupted)
        died = kyo("redact", target, "--why", "quoted an id")
        assert died.code == 1 and "OSError: simulated interruption" in died.err, died
    assert SECRET in _ledger_text(project), "precondition: residue in the ledger"

    doctor = kyo("doctor")
    assert doctor.code != 0
    finding = next(
        line for line in doctor.err.splitlines() if target in line and "survives" in line
    )
    assert "kyosei/export/_events/" in finding

    rerun = _run_named_remedy(kyo, finding)
    assert rerun.code == 0, rerun
    assert "retried" in rerun.out
    assert SECRET not in _ledger_text(project)
    assert _export_files_carrying(project, SECRET) == []
    redactions = [e for e in _events(project) if e["command"].startswith(f"redact {target} ")]
    assert len(redactions) == 1, "the retry recorded a second redaction event"
    assert kyo("doctor").code == 0, kyo("doctor")


def test_doctor_names_a_field_snapshot_put_back_in_the_events_table(
    kyo, project: Path, write_file, db
) -> None:
    """KYO-426.c3, the events-table half, and the remedy clearing it."""
    record, target = _record_with_secret_in(kyo, write_file, "title")
    assert kyo("redact", target, "--why", "quoted an id").code == 0
    conn = db()
    row = conn.execute(
        "SELECT id, after_json FROM events WHERE record_id = ? AND command = 'new bug'",
        (record,),
    ).fetchone()
    document = json.loads(row["after_json"])
    document["title"] = SECRET
    conn.execute(
        "UPDATE events SET after_json = ? WHERE id = ?",
        (json.dumps(document, sort_keys=True), row["id"]),
    )
    conn.close()

    doctor = kyo("doctor")
    assert doctor.code != 0
    finding = next(
        line for line in doctor.err.splitlines() if target in line and "survives" in line
    )
    assert "`events` table" in finding
    assert _run_named_remedy(kyo, finding).code == 0
    assert _db_events_carrying(db, SECRET) == 0
    assert kyo("doctor").code == 0, kyo("doctor")


def test_a_field_with_nothing_to_redact_is_refused(kyo, project: Path) -> None:
    record = new_record(kyo, kind="bug", title="No description")
    result = kyo("redact", f"{record}.description", "--why", "nothing there")
    assert result.code != 0
    assert "no description text to redact" in result.err
    assert not any(e["command"].startswith("redact ") for e in _events(project))


def test_doctor_scans_the_events_table_once_for_every_redacted_id(
    kyo, project: Path, write_file, monkeypatch
) -> None:
    """KYO-426.c4: the table-side twin of the ledger test above. Each id's residue must
    read the rows `event_rows_naming` found in its one scan, never scan the table
    itself (`_event_rows_naming`, one `instr` query per id: ~0.18 s each on this
    repository, in every Stop hook)."""
    from kyosei import ledger_scrub

    for title in ("one", "two"):
        record = new_record(kyo, kind="bug", title=title)
        evidence_id = _attach(kyo, write_file, record, text=f"{SECRET} {title}")
        assert kyo("redact", evidence_id, "--why", "leaked").code == 0
    _record, target = _record_with_secret_in(kyo, write_file, "description")
    assert kyo("redact", target, "--why", "leaked").code == 0

    scans: list[int] = []
    real_scan = ledger_scrub.event_rows_naming

    def counting(store, needles):
        needles = list(needles)
        scans.append(len(needles))
        return real_scan(store, needles)

    def per_id_scan(*_args, **_kwargs):
        raise AssertionError("an id scanned the events table on its own")

    monkeypatch.setattr(ledger_scrub, "event_rows_naming", counting)
    monkeypatch.setattr(ledger_scrub, "_event_rows_naming", per_id_scan)
    doctor = kyo("doctor")
    assert doctor.code == 0, doctor
    assert scans == [3], f"expected one scan naming all three ids, saw {scans}"


def _carries_as_id(value, needle: str) -> bool:
    """Whether a parsed snapshot holds ``needle`` as an ``id``/``shift_note_id`` value."""
    if isinstance(value, dict):
        if needle in (value.get("id"), value.get("shift_note_id")):
            return True
        return any(_carries_as_id(v, needle) for v in value.values())
    if isinstance(value, list):
        return any(_carries_as_id(v, needle) for v in value)
    return False


def test_event_rows_naming_finds_every_row_the_scrub_rules_key_on(
    project: Path, kyo, write_file
) -> None:
    """KYO-426.c4: the single scan returns, per id, exactly the rows whose snapshots carry
    it as an id — every row a scrub rule could change — and no prefix match."""
    from kyosei import ledger_scrub
    from kyosei.store import open_store

    records = [new_record(kyo, kind="bug", title=f"record {n}") for n in range(1, 13)]
    evidence = _attach(kyo, write_file, records[0])
    shift = kyo("note", "--shift", "--file", write_file("s.txt", "a note"), "--json").json[
        "shift_note_id"
    ]
    needles = [records[0], records[1], evidence, shift, "NOPE-1"]
    store = open_store(project)
    try:
        found = ledger_scrub.event_rows_naming(store, needles)
        rows = store.conn.execute("SELECT id, before_json, after_json FROM events").fetchall()
        for needle in needles:
            expected = {
                row["id"]
                for row in rows
                if any(
                    _carries_as_id(json.loads(column), needle)
                    for column in (row["before_json"], row["after_json"])
                    if column
                )
            }
            assert {row["id"] for row in found[needle]} == expected, needle
        assert found[records[0]] and found[evidence] and found[shift]
        assert found["NOPE-1"] == []
        # Exact, not textual: `records[0]` is a prefix of `records[9]`..`records[11]`,
        # and the equality above holds only if none of their rows came along.
        assert records[9].startswith(records[0])
    finally:
        store.close()


@pytest.mark.parametrize("field", ["title", "description", "criterion"])
def test_a_field_redaction_survives_a_rebuild(
    kyo, project: Path, write_file, db, field: str
) -> None:
    """The redaction lives in its event, so a store rebuilt from the export still holds
    the marker, still knows the field was redacted, and doctor still passes."""
    record, target = _record_with_secret_in(kyo, write_file, field)
    assert kyo("redact", target, "--why", "quoted a consumer id").code == 0

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    shown = kyo("show", record, "--json").json
    assert SECRET not in json.dumps(shown)
    assert _db_events_carrying(db, SECRET) == 0
    assert kyo("doctor").code == 0, kyo("doctor")
