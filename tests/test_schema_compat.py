"""KYO-3: schema-version compatibility, rebuild-from-any-earlier-format, and the
DDL pin that keeps the whole thing honest.

Found live: M3 added the ``touches`` table and export key without
bumping ``schema_version`` or tolerating the prior format. Three observed
failures: (1) ``kyo promote`` died mid-command with a bare
``sqlite3.OperationalError`` against a pre-M3 db; (2) ``kyo rebuild`` died with a
bare ``KeyError`` against a pre-M3 export document; (3) the failed rebuild left a
partial ``kyosei.db`` that blocked the retry.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from kyosei.config import load_config
from kyosei.errors import EXIT_ERROR
from kyosei.rebuild import rebuild_from_export
from kyosei.schema import SCHEMA_VERSION, ddl_fingerprint

from .conftest import git_worktree, new_record

# --------------------------------------------------------------------- KYO-3.c1


def _stamp_db_schema_version(project: Path, version: int) -> None:
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(version),))
    conn.commit()
    conn.close()


def test_opening_an_old_db_fails_loudly_not_mid_command(kyo, project: Path) -> None:
    """The db's meta table predates this build's schema: a clean one-line error at
    open, naming `kyo rebuild`, exit 1 — never a traceback mid-command."""
    new_record(kyo, criteria=["a"])
    _stamp_db_schema_version(project, SCHEMA_VERSION - 1)

    result = kyo("show", "TST-1")
    assert result.code == EXIT_ERROR
    assert result.out == ""  # never partial command output before the crash
    assert "kyo rebuild" in result.err
    assert str(SCHEMA_VERSION - 1) in result.err


def test_opening_a_db_from_a_future_schema_also_fails_loudly(kyo, project: Path) -> None:
    """KYO-168 changed what this direction is allowed to say. It used to assert
    ``kyo rebuild`` here, which is what the defect looked like from the outside: the
    store is AHEAD of the build, and rebuilding reconstructs it under the build's OLDER
    schema (``rebuild.py`` stamps ``SCHEMA_VERSION`` and rewrites ``config.yml`` to
    match — KYO-5.c1), dropping whatever the later schema added. It still fails loudly
    at open with exit 1; it must not name that command. The message and the split are
    pinned in ``tests/test_kyo5_error_remedies.py``
    (``test_the_two_schema_directions_do_not_share_a_message_or_a_remedy``)."""
    new_record(kyo, criteria=["a"])
    _stamp_db_schema_version(project, SCHEMA_VERSION + 1)
    result = kyo("show", "TST-1")
    assert result.code == EXIT_ERROR
    assert result.out == ""  # never partial command output before the crash
    assert "kyo rebuild" not in result.err, result.err
    assert str(SCHEMA_VERSION + 1) in result.err


def test_a_totally_foreign_sqlite_file_fails_loudly_not_with_a_traceback(
    kyo, project: Path
) -> None:
    """Defense in depth: even a db with no `meta` table at all — not just a
    version mismatch — is reported cleanly, never as a bare OperationalError."""
    db_path = project / "kyosei" / "kyosei.db"
    db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE something_else (x INTEGER)")
    conn.commit()
    conn.close()

    result = kyo("show", "TST-1")
    assert result.code == EXIT_ERROR
    assert "kyo rebuild" in result.err


def test_config_and_db_schema_version_drift_is_its_own_loud_error(kyo, project: Path) -> None:
    """Law 3: the two copies of the version (config.yml, meta table) must agree;
    disagreement between them is reported distinctly from "this db predates the
    code"."""
    import yaml

    new_record(kyo, criteria=["a"])
    config_path = project / "kyosei" / "config.yml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    # The db is untouched (still stamped SCHEMA_VERSION); hand-edit config.yml alone.
    raw["schema_version"] = SCHEMA_VERSION + 5
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    result = kyo("show", "TST-1")
    assert result.code == EXIT_ERROR
    assert "must agree" in result.err


# --------------------------------------------------------------------- KYO-3.c2


def test_rebuild_accepts_a_pre_m3_export_missing_touches(kyo, project: Path) -> None:
    """A document with no `touches` key loads as empty touches — the exact shape
    of the pre-M3 export that triggered this bug live."""
    record = new_record(kyo, title="widget", criteria=["a"])
    export_path = project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    doc["schema_version"] = 1
    del doc["touches"]
    del doc["claims"]
    del doc["evidence_artifacts"]
    for criterion in doc["criteria"]:
        del criterion["verifications"]
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result

    shown = kyo("show", record, "--json").json
    assert shown["touches"] == []
    assert shown["claims"] == []
    assert shown["evidence_artifacts"] == []
    assert shown["criteria"][0]["verifications"] == []
    assert shown["title"] == "widget"


def test_rebuild_accepts_a_v1_export_with_touches_but_no_provenance(
    kyo, project: Path
) -> None:
    """The narrower, later case: touches present (M3), claims/verifications/
    evidence not yet invented (pre-M4) — still `schema_version: 1`."""
    record = new_record(kyo, title="widget", criteria=["a"])
    export_path = project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    doc["schema_version"] = 1
    del doc["claims"]
    del doc["evidence_artifacts"]
    for criterion in doc["criteria"]:
        del criterion["verifications"]
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result
    shown = kyo("show", record, "--json").json
    assert shown["claims"] == []
    assert shown["evidence_artifacts"] == []


def test_rebuild_accepts_a_pre_kyo32_export_whose_dispatch_declares_no_branch(
    kyo, git_project: Path
) -> None:
    """KYO-32.c4: `dispatches.branch` arrived at schema_version 8. A dispatch
    exported under 7 has no `branch` key at all, and that absence must rebuild as an
    explicit "no branch was declared" -- never as a crash, and never as some
    invented branch the check at report time would then compare against."""
    record = new_record(kyo, title="widget", criteria=["a"])
    assert kyo("dispatch", record, "--branch", "kyo-99").code == 0

    export_path = git_project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    doc["schema_version"] = 7
    assert doc["dispatches"], "the fixture must actually carry a dispatch to strip"
    for dispatch in doc["dispatches"]:
        del dispatch["branch"]
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (git_project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result

    rebuilt = kyo("show", record, "--json").json["dispatches"][0]
    assert rebuilt["branch"] is None
    # The rest of the dispatch is untouched by the default -- this is a missing
    # field filled in, not a document reinterpreted.
    assert rebuilt["state"] == "running"


def test_rebuild_accepts_a_pre_kyo61_export_whose_dispatch_declares_no_model(
    kyo, git_project: Path
) -> None:
    """KYO-61.c4: `dispatches.worker_model` arrived at schema_version 11. Every
    dispatch exported under 10 has no `worker_model` key at all, and that absence
    must rebuild as an explicit "nothing declared a model" — which is precisely what
    was true of them: they ran on whatever the controller happened to be on, not by
    a decision but by the absence of one. Inventing a model name at load time would
    retroactively manufacture a choice nobody made."""
    record = new_record(kyo, title="widget", criteria=["a"])
    assert kyo("dispatch", record, "--model", "some-model").code == 0

    export_path = git_project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    doc["schema_version"] = 10
    assert doc["dispatches"], "the fixture must actually carry a dispatch to strip"
    for dispatch in doc["dispatches"]:
        del dispatch["worker_model"]
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (git_project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result

    rebuilt = kyo("show", record, "--json").json["dispatches"][0]
    assert rebuilt["worker_model"] is None
    # A missing field filled in, not a document reinterpreted.
    assert rebuilt["state"] == "running"


# --------------------------------------------------------------------- KYO-375.c1


def test_rebuild_accepts_a_pre_kyo375_export_whose_dispatch_has_no_tick_audit(
    kyo, git_project: Path
) -> None:
    """KYO-375.c1: `dispatches.tick_audit_json` arrived at schema_version 23. Every
    dispatch exported under 22 has no `tick_audit` key at all, and that absence must
    rebuild as an explicit "no tick has ever been counted against it" — which is
    precisely what was true of them: a not-a-worker tick before this shipped wrote a
    row to the events ledger instead (read back through
    `StopAuditLedger.untranscribed`), and this column carries only what has been
    counted since."""
    record = new_record(kyo, title="widget", criteria=["a"])
    assert kyo("dispatch", record).code == 0

    export_path = git_project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    doc["schema_version"] = 22
    assert doc["dispatches"], "the fixture must actually carry a dispatch to strip"
    for dispatch in doc["dispatches"]:
        del dispatch["tick_audit"]
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (git_project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result

    rebuilt = kyo("show", record, "--json").json["dispatches"][0]
    assert rebuilt["tick_audit"] is None
    # A missing field filled in, not a document reinterpreted.
    assert rebuilt["state"] == "running"


# --------------------------------------------------------------------- KYO-52.c4


def test_rebuild_accepts_a_pre_kyo52_export_whose_paths_are_absolute(
    kyo, git_project: Path, tmp_path
) -> None:
    """KYO-52.c4: schema_version 9 changed what `dispatches.worktree` and
    `touches.path` *mean* (root-relative, not absolute) without changing a table.

    A document written at 8 holds absolute paths, and they load verbatim: rewriting
    them at rebuild time would quietly migrate git-canonical history on the next
    export refresh, and what is already committed is the maintainer's decision, not
    a rebuild's. Nothing crashes, and nothing is reinterpreted."""
    tree = tmp_path / "wt-legacy"
    git_worktree(kyo.project, tree)
    record = new_record(kyo, title="widget", criteria=["a"])
    assert kyo("dispatch", record, "--worktree", str(tree)).code == 0

    export_path = git_project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    doc["schema_version"] = 8
    assert doc["dispatches"], "the fixture must actually carry a dispatch to rewrite"
    doc["dispatches"][0]["worktree"] = str(tree.resolve())
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (git_project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result

    rebuilt = kyo("show", record, "--json").json["dispatches"][0]
    assert rebuilt["worktree"] == str(tree.resolve())
    assert rebuilt["state"] == "running"


def test_rebuild_says_how_many_legacy_absolute_paths_it_loaded(
    kyo, git_project: Path, tmp_path
) -> None:
    """Loaded verbatim is not the same as unremarked. A rebuild that said nothing
    would be reporting a clean store it does not have — an absolute path is inert
    data that resolves fine and therefore survives in silence, which is why KYO-52
    was found by someone asking what this repo would leak, rather than by anything
    failing."""
    tree = tmp_path / "wt-legacy-counted"
    git_worktree(kyo.project, tree)
    record = new_record(kyo, title="widget", criteria=["a"])
    assert kyo("dispatch", record, "--worktree", str(tree)).code == 0

    export_path = git_project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    doc["schema_version"] = SCHEMA_VERSION - 1
    doc["dispatches"][0]["worktree"] = str(tree.resolve())
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (git_project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild", "--json")
    assert result.code == 0, result
    reported = result.json["legacy_absolute_paths"]
    assert len(reported) == 1, reported
    assert str(tree.resolve()) in reported[0]
    assert "dispatches.worktree" in reported[0]


def test_a_rebuild_of_a_current_export_reports_no_legacy_paths(
    kyo, git_project: Path, tmp_path
) -> None:
    """The other half of the same mechanism: on a store written by this build the
    note must not appear at all, or it is noise rather than a signal."""
    tree = tmp_path / "wt-current"
    git_worktree(kyo.project, tree)
    record = new_record(kyo, title="widget", criteria=["a"])
    assert kyo("dispatch", record, "--worktree", str(tree)).code == 0

    (git_project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result
    assert "KYO-52" not in result.out


# --------------------------------------------------------------------- KYO-63.c4


def test_rebuild_accepts_a_pre_kyo63_sweep_ledger_with_no_coverage(
    kyo, git_project: Path
) -> None:
    """`sweep_runs.code_repo_id`/`coverage`/`coverage_detail` arrived at schema_version
    10. A `_sweeps.jsonl` line written under 9 carries none of them, and its own absent
    keys are the version signal (`schema.SWEEP_RUN_FIELD_DEFAULTS`).

    The default is an explicit "unrecorded", never "swept" and never NULL: such a run
    genuinely cannot say whether it answered the staleness question, and the whole record
    is about that unknown not reading as an answer.

    KYO-181.c4: the constructed line drops `schema_version` along with the three fields,
    because a line written under 9 carried no stamp either — ledger lines only began
    carrying one at 17. Deleting the fields but leaving a current stamp would build a line
    no build has ever written, and would quietly stop testing the case that matters: that
    the tolerance is keyed on the absent FIELD and works on a line with no version
    information at all."""
    assert kyo("sweep").code == 0
    ledger = git_project / "kyosei" / "export" / "_sweeps.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    for field in ("code_repo_id", "coverage", "coverage_detail", "schema_version"):
        del row[field]
    ledger.write_text(json.dumps(row) + "\n", encoding="utf-8")

    (git_project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result

    # And the value it loads as is consumed, not merely stored: the brief reports the
    # last sweep as not having answered, rather than as a clean board.
    brief = kyo("brief", "--json")
    assert brief.json["staleness"]["coverage"] == "unrecorded"
    assert brief.json["staleness"]["sweep_run_id"] == row["id"]
    assert "not a clean bill" in kyo("brief").out


# --------------------------------------------------------------------- KYO-94


def test_rebuild_accepts_a_pre_kyo94_brief_ledger_with_no_session_start(
    kyo, project: Path, write_file
) -> None:
    """`brief_runs.session_start` arrived at schema_version 12. A `_briefs.jsonl` line
    written under 11 carries no such key — same ledger shape as the sweep case above, so
    the absent key is the version signal (`schema.BRIEF_RUN_FIELD_DEFAULTS`).

    It loads as None — *never asked* — and never as 0 or 1. What that means is checked
    through behaviour, not through the column: a brief run from before the field existed
    must not anchor a session, so the handoff falls back to "the most recent shift note"
    and the brief says which of the two questions it answered.

    KYO-181.c4: `schema_version` goes with it, for the reason the sweep case above gives —
    a line written under 11 predates the ledger stamp entirely, and a constructed line
    that kept a current stamp would be a shape no build has ever written."""
    assert kyo("note", "--shift", "--file", write_file("s.md", "the old shift")).code == 0
    assert kyo("brief", "--session-start").code == 0

    ledger = project / "kyosei" / "export" / "_briefs.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]
    assert all("session_start" in row for row in rows), "this build writes the key"
    for row in rows:
        del row["session_start"]
        del row["schema_version"]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0

    brief = kyo("brief", "--json")
    assert brief.json["handoff"]["basis"] == "no-session-recorded"
    assert brief.json["handoff"]["note"]["text"] == "the old shift"
    assert "no brief run has ever declared a session start" in kyo("brief").out


# --------------------------------------------------------------------- KYO-100.c4


def test_rebuild_accepts_a_pre_kyo100_export_with_no_scope(kyo, project: Path) -> None:
    """`records.scope` arrived at schema_version 13. Every record exported under 12 has
    no `scope` key at all, and that absence must rebuild as an explicit "no scope was
    declared" — which is precisely what was true of them: the field did not exist, so
    nobody declined to fill it in and nobody filled it in either.

    The one thing rebuild must NOT do here is derive one. Every such record has touches,
    and a rebuild that read them would manufacture a declaration nobody made, silently,
    for the whole store at once. Derivation is a suggester, never the source.
    """
    record = new_record(kyo, title="widget", criteria=["a"])
    export_path = project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    assert "scope" in doc, "this build writes the key"
    doc["schema_version"] = 12
    del doc["scope"]
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result

    rebuilt = kyo("show", record, "--json").json
    assert rebuilt["scope"] is None
    # A missing field filled in, not a document reinterpreted.
    assert rebuilt["title"] == "widget"


# --------------------------------------------------------------------- KYO-102.c4


def test_rebuild_accepts_a_pre_kyo137_export_with_no_docs_layer(
    kyo, project: Path
) -> None:
    """`doc_refs` and `docs_obligations` arrived at schema_version 16. A record exported
    under 15 has neither key, and both absences must rebuild as the explicit empty list:
    this record declared no documents its work affects, and no dispatch raised a question
    against any.

    The one thing rebuild must NOT do is derive a doc-ref from the record's `touches`. A
    doc-ref is a person's statement that a document is affected; manufacturing one from a
    path would invent that statement silently, across the whole store at once — the move
    KYO-100 refused for `scope` and KYO-102 refused for a gate's tags. The touches on this
    record include a markdown file precisely so that a deriving rebuild would be caught.
    """
    record = new_record(kyo, criteria=["a"])
    (project / "NOTES.md").write_text("# Notes\n", encoding="utf-8")
    (project / "src").mkdir(exist_ok=True)
    (project / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    touches = project / "touches.txt"
    touches.write_text("NOTES.md\nsrc/app.py\n", encoding="utf-8")
    assert kyo("edit", record, "--touches-file", str(touches)).code == 0

    export_path = project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    assert doc["doc_refs"] == [], "this build writes the key"
    assert doc["docs_obligations"] == [], "this build writes the key"
    doc["schema_version"] = 15
    del doc["doc_refs"]
    del doc["docs_obligations"]
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result

    rebuilt = kyo("show", record, "--json").json
    assert rebuilt["doc_refs"] == []
    assert rebuilt["docs_obligations"] == []
    # Missing fields filled in, not a document reinterpreted: the touches a deriving
    # rebuild would have read are still there, and still say only what they said.
    assert [t["path"] for t in rebuilt["touches"]] == ["NOTES.md", "src/app.py"]


def test_rebuild_accepts_a_pre_kyo102_export_whose_gates_have_no_tags(
    kyo, project: Path
) -> None:
    """`gates.tags_json` arrived at schema_version 15. Every gate exported under 14 has
    no `tags` key at all, and that absence must rebuild as an explicit "this gate was
    never classified" — which is precisely what was true of it: the registry did not
    exist, so nobody tagged it and nobody declined to.

    The one thing rebuild must NOT do is derive tags from the `do` payload. Every such
    gate has one, and a rebuild that read it for keywords would manufacture a
    classification nobody made, silently, for the whole store at once — the same thing
    KYO-100 refused to do to `scope` from `touches`.
    """
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "awaiting:human-review", "--do", "review the deploy dashboard")
    export_path = project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    assert doc["gates"][0]["tags"] == [], "this build writes the key"
    doc["schema_version"] = 14
    del doc["gates"][0]["tags"]
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result

    rebuilt = kyo("show", record, "--json").json
    assert rebuilt["gates"][0]["tags"] == []
    # A missing field filled in, not a document reinterpreted: the payload that a
    # keyword-deriving rebuild would have read is still there, and still says only
    # what it said.
    assert rebuilt["gates"][0]["do"] == "review the deploy dashboard"
    assert rebuilt["gates"][0]["type"] == "awaiting:human-review"


def test_a_pre_kyo102_gate_still_behaves_exactly_as_it_did(kyo, project: Path) -> None:
    """KYO-102.c4's second half: "no behavior change". A gate rebuilt from a document
    that predates tags is not a lesser gate — it still holds its record, still clears by
    its own id, and clearing it still raises the KYO-59 flag its type raises."""
    record = new_record(kyo, criteria=["a"])
    gate_id = kyo("gate", record, "holding-for", "--do", "the design lands", "--json").json[
        "gate_id"
    ]
    export_path = project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    doc["schema_version"] = 14
    for gate in doc["gates"]:
        del gate["tags"]
    export_path.write_text(json.dumps(doc), encoding="utf-8")
    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0

    gated = kyo("brief", "--json").json["gated"]
    assert [(g["record_id"], g["gate_type"], g["tags"]) for g in gated] == [
        (record, "holding-for", [])
    ]
    cleared = kyo("ungate", gate_id, "--why", "the design landed")
    assert cleared.code == 0, cleared
    flags = kyo("show", record, "--json").json["flags"]
    assert [f["flag_type"] for f in flags] == ["gate-cleared-stale-criteria"]


# --------------------------------------------------------------------- KYO-3.c3


def test_a_failed_rebuild_leaves_no_partial_db(project: Path) -> None:
    """The second live failure: a corrupt/incompatible export must not leave a
    kyosei.db that blocks the retry with "already exists"."""
    export_dir = project / "kyosei" / "export"
    (export_dir / "TST-1.json").write_text("{not valid json at all", encoding="utf-8")
    config = load_config(project)
    config.db_path.unlink()  # the `project` fixture's `kyo init` already created one
    assert not config.db_path.exists()

    with pytest.raises(Exception):  # noqa: B017 - json.JSONDecodeError, deliberately broad here
        rebuild_from_export(export_dir, config, force=False)

    assert not config.db_path.exists(), "a failed rebuild must not leave a partial kyosei.db"


def test_a_failed_rebuild_via_the_cli_also_leaves_no_partial_db(kyo, project: Path) -> None:
    """Same guarantee, exercised through the CLI: a dangling foreign key (a note
    superseded by an id that does not exist) fails the rebuild loudly and cleanly."""
    record = new_record(kyo, criteria=["a"])
    kyo("edit", record, "--note-file", "-", stdin="a thought")
    export_path = project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    doc["notes"][0]["superseded_by"] = f"{record}.n99"
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == EXIT_ERROR
    assert not (project / "kyosei" / "kyosei.db").exists()


# --------------------------------------------------------------------- KYO-3.c4


def test_ddl_hash_is_pinned_to_the_declared_schema_version() -> None:
    """A schema change without a version bump must fail this suite.

    This test pins today's DDL fingerprint to today's ``SCHEMA_VERSION``. Change
    the DDL without bumping ``SCHEMA_VERSION`` and this assertion fails; the fix is
    to bump ``kyosei.schema.SCHEMA_VERSION`` and update the pinned hash below in
    the same commit as the schema change (KYO-3.c4).
    """
    pinned = {
        2: "2cd81e07a27af20e230ffa29a8af39ef972c801579b8e263b6573a0c74a0c2c6",
        3: "5818601dac1a333583e3b8dc9a983bcd7e5182e1537ca68db9d151e3ac5cee6f",
        4: "1b4655bb0220cbbe7d56b31345f6b1204c4e054fcd79ef410ee17d984856b3d7",
        5: "60b90e342d99b7547c2493c7e412da492311e86fa50f995c2ecea9621e98105c",
        6: "ed8b5a863d1c298ddb20ba1c21c5482f7cc00f5138bf5278eff405facedb2cd9",
        7: "87e2be34b7301da8b9cfd1f08386f3df50091c9f12565e3d9255f7c7ba39a9fd",
        # KYO-32: dispatches.branch — the branch a dispatch's commits must land on.
        8: "a2d4d54230204b9d3104683fc182f698bbd0024892045aa68b025ee27b7554c4",
        # KYO-52: deliberately identical to 8's. No table changed; what changed is
        # what a value in `dispatches.worktree` / `touches.path` *means* — relative
        # to the project root from 9 on, absolute before it. The pin's job is "no DDL
        # change without a version bump"; a version bump with no DDL change is the
        # honest way to record a format change no column shape can express, and the
        # alternative (leaving it at 8) is exactly the silent
        # same-name-different-meaning drift KYO-3 exists to prevent.
        9: "a2d4d54230204b9d3104683fc182f698bbd0024892045aa68b025ee27b7554c4",
        # KYO-63: sweep_runs gains code_repo_id / coverage / coverage_detail — which
        # code history a run's base sha belongs to, and whether the staleness question
        # was answered at all. A real DDL change this time, so a real new hash.
        10: "851fe9d6702b3ffb3912cdb6901d2bcf613d0f6bff3f578295c998f6da3a6e9e",
        # KYO-61: dispatches gains worker_model — which model executed this dispatch,
        # resolved from kyosei/config.yml -> the kind's policy.worker_model ->
        # `kyo dispatch --model`. A real DDL change, so a real new hash.
        11: "7039e4f9e28d8fe21d2169e0c397b614a62d39a450c3868b98ba392bfcf1eb3c",
        # KYO-94: brief_runs gains session_start — whether this brief is the one a
        # controller with fresh context opened on. It is the store's only session concept,
        # and the anchor for "the shift note authored before this session began", which
        # was not a question this store could answer. A real DDL change, so a real hash.
        12: "d25a9398d150ed8617ca0b397fd17b2fa69e85547dde1a7785ff782a7b57d45d",
        # KYO-100: records gains scope — which part of the system this record's work
        # belongs to, named from the per-project registry in kyosei/scopes.yml. The
        # first of the four config dimensions to become a schema dimension. A real
        # DDL change, so a real new hash.
        13: "09fb63d6839ef0eccb4c946d5146b47af8f0282400646b439f7e073fd023d231",
        # KYO-101: deliberately identical to 13's, for the same reason 9's is identical
        # to 8's. No table changed; what changed is what a value in `records.status`
        # *means* — a name declared in the per-project registry kyosei/statuses.yml and
        # mapped to an archetype role, where 13 and earlier held a member of a fixed
        # six-tuple in contracts.py. The pin's job is "no DDL change without a version
        # bump"; a version bump with no DDL change is the honest way to record a format
        # change no column shape can express.
        14: "09fb63d6839ef0eccb4c946d5146b47af8f0282400646b439f7e073fd023d231",
        # KYO-102: gates gains tags_json — what KIND of waiting a gate is, named from
        # the per-project registry in kyosei/gate-tags.yml. The third of the four
        # config dimensions to become a registry, and the first whose whole reason for
        # existing is a query the free-text payload could not answer. A real DDL change,
        # so a real new hash.
        15: "c59a644fdc234bc4149dea7093cd2210771357d768c85687cc3f62f823252d27",
        # KYO-137: two new tables — `doc_refs` (a record declares the documents its
        # work affects, the back path to a document's `governs:`) and
        # `docs_obligations` (one document SECTION a dispatch put a question to,
        # settled only by a controller's asserted verdict). A real DDL change, so a
        # real new hash.
        16: "0b0b5ae5a11055bb52afb5426538cb55e5914025efd27d6d6ad97b2c7d27c71b",
        # KYO-181: deliberately identical to 16's, for the same reason 9's is identical to
        # 8's and 14's to 13's. No table changed; what changed is the EXPORT format — every
        # line of every append-only ledger now carries the schema_version it was written
        # at, where 16 and earlier carried none anywhere, which is what let a bump that
        # touched only a ledger (10, 12) rebuild silently and lossily. The pin's job is "no
        # DDL change without a version bump"; a version bump with no DDL change is the
        # honest way to record a format change no column shape can express, and leaving it
        # at 16 would be exactly the silent same-name-different-meaning drift KYO-3 exists
        # to prevent — sharper here than anywhere, because the number left unbumped is the
        # very number the new stamp writes.
        17: "0b0b5ae5a11055bb52afb5426538cb55e5914025efd27d6d6ad97b2c7d27c71b",
        # KYO-172: `idx_events_command` on events(command, seq) — the first bump whose
        # whole content is an index. No table, no column and no export field changed, so
        # unlike 9, 14 and 17 (the other no-DDL-change bumps) this one is the opposite
        # case: a REAL DDL change carrying no format change at all. The fingerprint covers
        # indexes, so it moves; and it has to, because this store has no migration path —
        # `kyo rebuild --force` under the new DDL is the only thing that puts the index on
        # an existing database, and `open_store` only demands it once the version differs.
        18: "ef87ec0aae00f277b3c70d48954aebbef697cf81261964665d3cc603dea8c5db",
        # KYO-268.c1: deliberately identical to 18's, for the same reason 9's is
        # identical to 8's, 14's to 13's and 17's to 16's. No table and no index changed;
        # what changed is the EXPORT format — an event's before/after snapshot now
        # carries each evidence artifact as {id, sha256} instead of embedding its body,
        # so the ledger stops being a second copy of every artifact in the store. The
        # pin's job is "no DDL change without a version bump"; a version bump with no DDL
        # change is the honest way to record a format change no column shape can express.
        19: "ef87ec0aae00f277b3c70d48954aebbef697cf81261964665d3cc603dea8c5db",
        # KYO-268.c2: identical again, and for the same reason — no DDL change. What
        # changed is which FILES the export is made of: the events ledger is a directory
        # of bounded shards (`kyosei/export/_events/NNNNNN.jsonl`) rather than one
        # `_events.jsonl` that every commit re-stores whole. Not one line's content
        # moves, which is what makes `kyo export --migrate-events` lossless; the version
        # still has to move, because "the ledger is where?" is a format question and 19
        # answers it differently from 20.
        20: "ef87ec0aae00f277b3c70d48954aebbef697cf81261964665d3cc603dea8c5db",
        # KYO-274.c1: identical again, and for the same reason 19's is identical to 18's
        # — no table and no index changed. What changed is the EXPORT format, and it is
        # 19's change applied to the two bodies 19 left behind: an event's before/after
        # now carries a dispatch's frozen brief as `brief_sha256` and a report's ingested
        # body as `body_sha256` instead of embedding either, so the ledger stops being a
        # second copy of 68.3 MB of briefs and 49.1 MB of reports. The pin's job is "no
        # DDL change without a version bump"; a version bump with no DDL change is the
        # honest way to record a format change no column shape can express.
        21: "ef87ec0aae00f277b3c70d48954aebbef697cf81261964665d3cc603dea8c5db",
        # KYO-345: identical again, and for the same reason 14's is identical to 13's —
        # no table, no column and no index changed. What changed is what two stored
        # VALUES mean: `dispatches.state` has a fourth member, `withdrawn`, for a brief
        # frozen by mistake and never handed to a worker (where 21 and earlier had only
        # `terminated-no-report`, which means "died mid-work"), and `terminated_why`
        # holds the controller's reason for either report-less state rather than only
        # for a termination. The pin's job is "no DDL change without a version bump"; a
        # version bump with no DDL change is the honest way to record a format change no
        # column shape can express — and here it is the only thing that stops a build at
        # 21 from rebuilding a `withdrawn` dispatch into a store whose state vocabulary
        # does not contain it.
        22: "ef87ec0aae00f277b3c70d48954aebbef697cf81261964665d3cc603dea8c5db",
        # KYO-375: dispatches gains tick_audit_json -- the per-dispatch counter a
        # not-a-worker tick increments in place instead of writing an events-ledger
        # row. A real DDL change, so a real new hash.
        23: "7eb6c5fcb70697357c2ba9843e1b1068d3e10ae11f2552f8886e70bc5a9836ee",
        # KYO-398: brief_runs gains build_commit / build_commit_time / build_source --
        # the build that produced each brief, so a session-start brief can compare its
        # own with the previous session's. A real DDL change, so a real new hash.
        24: "a49f019269001fd13f9cc8af4d87703e34d19757c74c0fd792021351e03b94ae",
        # KYO-418: events gains changed_json (an event records the prior value of what
        # changed rather than a second whole document) and brief_runs gains session_id
        # (check-build compares against its own session's opening brief). A real DDL
        # change, so a real new hash.
        25: "da24d8ae640ea3059047b5500f17d5b0cb5423ebf4873d58a435227833df05ff",
        # KYO-498: records gains since_note -- the id of the shift note that first
        # recorded a carried item, whose created_at its age counts from. A real DDL
        # change, so a real new hash.
        26: "abc923ce60a10eed92ff541bf8d53077c60a63c4f6dfcf03f40534d3b20b7297",
        # KYO-554: docs_obligations.dispatch_id becomes nullable -- a standing obligation,
        # raised by a record when it is filed or edited, has no dispatch. A real DDL
        # change, so a real new hash.
        27: "8a8a8fa22e24c1dd087c1d5d6bb2a767b8bd97bf4ecb89052ead5c756e8f83ee",
        # KYO-642: the credits and credit_subjects tables and gates.cleared_as -- who a
        # verification, a gate clearing or a decision rests on, and the words and session
        # it cites. A real DDL change, so a real new hash.
        28: "968520f4377dc54d0dbf2a17dd51c87119f3e0f9d62eb4727533c6508d279d25",
        # KYO-670: credits gains withdrawn_at, withdrawn_by and withdrawn_why -- a credit
        # retracted with no replacement, kept readable. A real DDL change, so a real new
        # hash.
        29: "fed4bf489447031a8eeb333da551c7af3a54988a4b319be2459f9d29c4baa52e",
        # KYO-705: person_notes, person_note_anchors, person_note_tags and
        # person_note_tag_changes -- what a person wrote, on the places it addresses, and
        # each later change to its tags. A real DDL change, so a real new hash.
        30: "2cd81e07a27af20e230ffa29a8af39ef972c801579b8e263b6573a0c74a0c2c6",
        # KYO-709: person_notes gains sent_at, sent_session and sent_message -- a note's
        # sent state as a field set once, in place of the `sent` tag. A real DDL change, so
        # a real new hash.
        31: "1bd4e138ed4cda1f98f15e5e37d6b3bc602fdacff57246848e9a78d12b6543bd",
        # KYO-658: the turn_refs table -- a discussion's references to the person's turns
        # as the turn index stamped them. A real DDL change, so a real new hash.
        32: "aaf2d2ee3bb1aca4306f80e59278015eecf3455cc38e02326eeef8c32b2e34be",
        # KYO-663: credits gains span_seal -- the hash of the span's last line in the turn
        # index, whose chain commits every line before it. A real DDL change, so a real
        # new hash.
        33: "fabcb9af44ea15cc15bc3538d85acf8a47f513317dfc31a59d7fb38019da65eb",
        # KYO-661: dispatches gains answered_prompt_json -- the prompt the dispatch was
        # made in reply to. A real DDL change, so a real new hash.
        34: "70b5951201caf8e8fbdf72ffa7d3e399c18eb69525ca99bccab630808e14bda9",
        # KYO-882: turn_refs gains outcome -- on a closing reference, whether the discussion
        # produced something or was dropped. A real DDL change, so a real new hash.
        35: "2605ef4744082bd9e2fb99ad0b0227127b19895b6d677d090d44e9c1d299e0e5",
    }
    assert SCHEMA_VERSION in pinned, (
        f"schema_version {SCHEMA_VERSION} has no pinned DDL hash in "
        "tests/test_schema_compat.py — add one (and see the module docstring for why)"
    )
    assert ddl_fingerprint() == pinned[SCHEMA_VERSION], (
        "the DDL changed without a matching schema_version bump (or the pin is stale): "
        f"bump kyosei.schema.SCHEMA_VERSION and update the pinned hash for the new version "
        f"in tests/test_schema_compat.py to {ddl_fingerprint()!r} once the bump is deliberate"
    )


# ------------------------------------------------------------------------- KYO-179
#
# The export boundary's version guard. KYO-168 split ``open_store``'s refusal so a *db*
# written by a later schema is told to upgrade the build rather than to reconstruct
# itself under the older one. ``kyo rebuild`` had no equivalent at all, and it is the
# boundary that matters more: ``kyosei/export/`` is the git-canonical serialization —
# the thing that actually travels between builds — while ``kyosei.db`` is gitignored.
#
# What made the defect silent is worth keeping in view while reading these tests. The
# compat machinery infers the source format from the ABSENCE of a field
# (``schema.EXPORT_FIELD_DEFAULTS``), which is sound for an older document and
# structurally incapable of noticing a newer one: a newer document is missing nothing,
# it merely carries keys ``rebuild`` never looks at. So the old behaviour was a full
# exit 0 — a store reconstructed from the fields this build happens to know, this
# build's ``SCHEMA_VERSION`` stamped into the db, ``kyosei/config.yml`` rewritten to
# match (KYO-5.c1) so the project's own record claimed the downgrade was intended, and
# ``kyo doctor``'s round-trip green because the export was immediately regenerated from
# the reduced db. Everything below is a test that some part of that no longer happens.


def _bump_document_schema_version(project: Path, record: str, version: int) -> Path:
    """Rewrite one record's export document to stamp ``version``, and return its path."""
    export_path = project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    assert doc["schema_version"] == SCHEMA_VERSION, "this build stamps its own version"
    doc["schema_version"] = version
    export_path.write_text(json.dumps(doc), encoding="utf-8")
    return export_path


# --------------------------------------------------------------------- KYO-179.c1


def test_rebuild_refuses_an_export_document_from_a_later_schema(kyo, project: Path) -> None:
    """The refusal itself, through the CLI: exit 1, and never a reconstructed store.

    The two assertions about the *message* are the ones that carry KYO-168's settled
    shape into this boundary. It must name the later version (so the reader can tell
    which side is which, and what to install), and it must NOT name ``kyo rebuild`` —
    every command that would make this export loadable by this build writes the older
    schema over the newer one, which is the harm rather than the fix.
    """
    record = new_record(kyo, title="widget", criteria=["a"])
    _bump_document_schema_version(project, record, SCHEMA_VERSION + 1)
    db_path = project / "kyosei" / "kyosei.db"
    db_path.unlink()

    result = kyo("rebuild")
    assert result.code == EXIT_ERROR, result
    assert str(SCHEMA_VERSION + 1) in result.err, result.err
    assert f"{record}.json" in result.err, result.err
    assert "kyo rebuild" not in result.err, result.err
    assert not db_path.exists(), "a refused rebuild must not reconstruct anything"


def test_the_refusal_names_upgrading_the_build_and_not_a_command(kyo, project: Path) -> None:
    """KYO-168's wording, reused rather than reinvented for the same situation: which
    side is older, that the export is intact and this build is the stale party, and an
    installation as the remedy. Checked as the *clauses that matter* rather than word
    for word — a message pinned by its full text is a message nobody may improve."""
    record = new_record(kyo, criteria=["a"])
    _bump_document_schema_version(project, record, SCHEMA_VERSION + 4)
    (project / "kyosei" / "kyosei.db").unlink()

    err = kyo("rebuild").err
    assert "intact" in err, err
    assert "stale" in err, err
    assert f"install a kyosei that speaks schema_version {SCHEMA_VERSION + 4} or later" in err, err
    # And the same reader-is-not-at-a-shell sentence KYO-168 wrote for the db boundary:
    # KYO-175 points the desktop app at stores it does not control, and the app runs
    # the machine's one `kyo` rather than a runtime of its own (KYO-466).
    assert "the desktop app and every project's hooks run" in err, err
    assert "bundled with the application" not in err, err


def test_one_document_from_a_later_schema_refuses_the_whole_export(kyo, project: Path) -> None:
    """Nothing writes ``kyosei/export/`` transactionally, so a half-written export — some
    documents rewritten by the newer build, some not yet — is a state that exists. It is
    refused on the strength of any one of them, and the message says which, because with
    a store's worth of files on disk "somewhere in here" is not an answer."""
    first = new_record(kyo, title="one", criteria=["a"])
    second = new_record(kyo, title="two", criteria=["a"])
    _bump_document_schema_version(project, second, SCHEMA_VERSION + 1)
    (project / "kyosei" / "kyosei.db").unlink()

    result = kyo("rebuild")
    assert result.code == EXIT_ERROR, result
    assert f"{second}.json" in result.err, result.err
    assert f"{first}.json" not in result.err, result.err
    assert "1 record document(s)" in result.err, result.err


def test_a_document_whose_stamp_is_not_a_version_number_is_refused_by_name(
    kyo, project: Path
) -> None:
    """A stamp that is not an integer means the document cannot say what format it is in,
    and that is not a fact to guess at: ``int("16")`` and ``int(16.5)`` both succeed and
    the second is silently wrong, which is the failure shape this whole record is about.
    Refused cleanly and by filename, never as a bare ``TypeError`` traceback out of the
    compat machinery (the KYO-3 house rule, one boundary over)."""
    record = new_record(kyo, criteria=["a"])
    export_path = project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    doc["schema_version"] = "sixteen"
    export_path.write_text(json.dumps(doc), encoding="utf-8")
    (project / "kyosei" / "kyosei.db").unlink()

    result = kyo("rebuild")
    assert result.code == EXIT_ERROR, result
    assert f"{record}.json" in result.err, result.err
    assert "not a version number" in result.err, result.err
    assert not (project / "kyosei" / "kyosei.db").exists()


def test_a_config_that_alone_declares_a_later_schema_still_rebuilds(kyo, project: Path) -> None:
    """The edge the record left open, and the decision taken on it, pinned so that the
    next person to reach for the obvious guard trips over the reason first.

    ``kyosei/config.yml`` stamps a ``schema_version`` too. It is cheaper to read than the
    documents, it is present when no record document is, and it is the ONLY version signal
    covering a change that touched only the ledgers (``_sweeps.jsonl`` and its siblings
    carry no stamp — see ``test_only_the_per_record_documents_carry_a_schema_version``).
    Every reason to consult it is real.

    It is still not consulted, and the reason is mechanical rather than aesthetic:
    KYO-5.c1 makes ``kyo rebuild --force`` the *remedy* for a config.yml whose
    ``schema_version`` disagrees, ``store.open_store``'s config/db drift message names
    that command in both directions, and ``tests/test_kyo5_error_remedies.py`` proves the
    named remedy clears the error it is named in. A guard here would make that remedy fail
    on the exact case those tests use — and nothing can tell a store genuinely on a later
    schema from a config.yml somebody typed a wrong number into, because that stamp is
    bound to no data. The signal this boundary trusts is the one carried by the bytes
    about to be loaded.

    Overlaps ``test_rebuild_writes_schema_version_into_config_yml`` deliberately: that one
    asserts KYO-5's mechanism works, this one asserts KYO-179 did not take it away, and
    the second reason is the one that would be lost if only the first existed.
    """
    import yaml

    record = new_record(kyo, title="widget", criteria=["a"])
    config_path = project / "kyosei" / "config.yml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["schema_version"] = SCHEMA_VERSION + 5
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    result = kyo("rebuild", "--force")
    assert result.code == 0, result
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["schema_version"] == (
        SCHEMA_VERSION
    )
    assert kyo("show", record, "--json").json["title"] == "widget"


def test_an_ahead_config_does_not_soften_the_refusal_the_documents_earn(
    kyo, project: Path
) -> None:
    """The two signals in the same store: config.yml ahead is ignored, a document ahead is
    refused, and the refusal is not masked by the signal that is deliberately silent."""
    import yaml

    record = new_record(kyo, criteria=["a"])
    _bump_document_schema_version(project, record, SCHEMA_VERSION + 2)
    config_path = project / "kyosei" / "config.yml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["schema_version"] = SCHEMA_VERSION + 9
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    (project / "kyosei" / "kyosei.db").unlink()

    result = kyo("rebuild")
    assert result.code == EXIT_ERROR, result
    assert f"{record}.json" in result.err, result.err
    assert "record document(s) written by a later schema" in result.err, result.err


# --------------------------------------------------------------------- KYO-179.c2


def test_the_refusal_fires_before_anything_is_written(kyo, project: Path) -> None:
    """The criterion with teeth. ``--force`` is the case that matters: it unlinks the
    existing database four lines into ``rebuild_from_export``, and ``write_config``
    rewrites ``kyosei/config.yml`` at the end. A guard placed after either would leave a
    project whose store was deleted, or whose own declaration was rewritten to an older
    version, BY THE COMMAND THAT THEN REFUSED — strictly worse than not running it."""
    record = new_record(kyo, criteria=["a"])
    _bump_document_schema_version(project, record, SCHEMA_VERSION + 1)
    db_path = project / "kyosei" / "kyosei.db"
    config_path = project / "kyosei" / "config.yml"
    db_before = db_path.read_bytes()
    config_before = config_path.read_text(encoding="utf-8")

    result = kyo("rebuild", "--force")
    assert result.code == EXIT_ERROR, result
    assert db_path.read_bytes() == db_before, "the live database was touched"
    assert config_path.read_text(encoding="utf-8") == config_before, "config.yml was rewritten"
    # And the store still opens: the refusal cost the project nothing.
    assert kyo("show", record, "--json").json["id"] == record


# --------------------------------------------------------------------- KYO-179.c3


def test_an_export_at_this_builds_own_schema_version_still_rebuilds(kyo, project: Path) -> None:
    """The boundary is ``>``, not ``>=``. A guard off by one here would refuse every
    healthy store in existence, which is a worse outcome than the defect."""
    record = new_record(kyo, title="widget", criteria=["a"])
    export_path = project / "kyosei" / "export" / f"{record}.json"
    assert json.loads(export_path.read_text(encoding="utf-8"))["schema_version"] == SCHEMA_VERSION

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert kyo("show", record, "--json").json["title"] == "widget"


def test_an_export_one_version_behind_still_rebuilds(kyo, project: Path) -> None:
    """The other side of the same boundary, stated on its own so that a change to the
    comparison cannot pass by only ever being tested at equality. The pre-9, pre-11,
    pre-13 and KYO-52 tolerances above are the substantive version of this and are
    deliberately left exactly as they were — this guard added a ceiling, not a floor."""
    record = new_record(kyo, title="widget", criteria=["a"])
    _bump_document_schema_version(project, record, SCHEMA_VERSION - 1)

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert kyo("show", record, "--json").json["title"] == "widget"


def test_an_export_with_no_record_documents_still_rebuilds(kyo, project: Path) -> None:
    """A freshly-initialised store has no record documents and no ledger LINES, so it
    offers no version signal at all. NO SIGNAL IS NOT "FROM THE FUTURE": law 6 forbids an
    unknown reading as either answer, and refusing here would make ``kyo rebuild``
    unusable on exactly the store it exists to reconstruct — a fresh clone, whose
    gitignored database is missing and whose first command this is.

    KYO-181 narrowed what "no signal" covers without changing this case. When this test
    was written the ledgers carried no stamp *ever*, so a store with a thousand ledger
    lines and no record documents was equally silent; now such a store speaks, and only a
    genuinely empty one is silent. That is a strictly smaller silence covering the same
    store this test is about — the empty one — which is why the assertion below is
    unchanged."""
    export_directory = project / "kyosei" / "export"
    assert not list(export_directory.glob("TST-*.json")), "the fixture files no records"

    (project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result
    assert kyo("doctor").code == 0


def test_the_ledgers_and_the_record_documents_both_carry_a_schema_version(
    kyo, project: Path, write_file
) -> None:
    """KYO-181, replacing KYO-179's ``test_only_the_per_record_documents_carry_a_schema_version``.

    That test asserted the ledgers carry no stamp, and said in its own docstring what
    would follow if one ever started: "no signal" stops meaning "a new store". It was
    doing its job — this record is the change it was watching for, and the honest response
    is to replace it with the new fact rather than to delete it in passing, so the
    assertion that a ledger's version is checked can never silently become an assertion
    about nothing.

    What is checked here is that the stamp is on EVERY line of every ledger, which is the
    form decision (see ``export.stamp_ledger_row``): a header line at the top of a file
    would satisfy "the ledgers carry a version" and would be the wrong answer, because it
    would have to be rewritten the first time the version moved — losing the append-only
    merge-cleanly property the event ledger exists for. A per-line stamp is written with
    its line and never touched again.

    ``_allocations.json`` still carries none, asserted rather than left to chance. KYO-181
    left that as a stated residue; KYO-188 settled it, and the assertion below now says WHY
    rather than only THAT — it is not an omission awaiting a fix but a decision with a
    reason, and the reason is that a stamp there would witness nothing this export does not
    already witness. Every function that writes the snapshot writes a stamped artifact in
    the same command, so counters written by a later build never arrive without a
    later-stamped line or document beside them, and ``kyo rebuild`` refuses on that signal
    before it reads a counter.

    That premise is what makes the absence safe, so it is pinned rather than trusted:
    ``test_nothing_writes_the_allocation_snapshot_without_a_stamped_artifact_beside_it``
    below is the other half of this assertion, and ``ledgers.LEDGER_READERS`` carries the
    full argument, including what a stamp would cost the three consumers that read this
    file as a mapping.
    """
    new_record(kyo, criteria=["a"])
    assert kyo("sweep").code == 0
    assert kyo("brief").code == 0
    assert kyo("note", "--shift", "--file", write_file("s.txt", "handing off")).code == 0
    export_directory = project / "kyosei" / "export"

    for name in ("_events.jsonl", "_sweeps.jsonl", "_briefs.jsonl", "_shift_notes.jsonl"):
        path = _ledger_path(project, name)
        assert path.is_file(), name
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert lines, f"{name} has no lines to check"
        for line in lines:
            assert json.loads(line)["schema_version"] == SCHEMA_VERSION, name

    record_documents = list(export_directory.glob("TST-*.json"))
    assert record_documents, "no record documents to check"
    for path in record_documents:
        assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == SCHEMA_VERSION

    allocations = json.loads((export_directory / "_allocations.json").read_text(encoding="utf-8"))
    assert "schema_version" not in allocations, (
        "KYO-188 decided this file carries no version of its own, because it cannot be "
        "written without a stamped artifact being written in the same command (see "
        "ledgers.LEDGER_READERS and the co-write test below). If a stamp is being added, "
        "that decision is what has to be reopened first — and a flat key here collides "
        "with a namespace whose every other entry is a scope name and a counter"
    )


# --------------------------------------------------------------------- KYO-179.c4
#
# "A test fails if rebuild ever again reads a record document without consulting the
# schema_version stamped in it." A test that only exercised the refusal would not do
# that: it stays green the day someone adds a SECOND document-reading path that skips
# the check, which is exactly how the field came to be written by every export and read
# by nothing in the first place. So what is pinned below is the PROPERTY — one door into
# the module, and that door consults the stamp — not the instance. The precedent for
# scanning source rather than calling it is ``tests/test_kyo5_error_remedies.py``, which
# walks the AST of every error site instead of provoking each one.

#: What "this function is opening or parsing an export document" looks like in the AST.
#: Deliberately over-broad — a new reader that used ``open()`` and ``json.load`` would
#: slip past a set keyed only on today's spelling, so the entries are the *shapes*
#: (listing the files, reading a file's bytes, parsing JSON) rather than one call chain.
_DOCUMENT_READING_CALLS = frozenset(
    {"record_export_files_in", "existing_export_files", "loads", "load", "read_text", "open"}
)

#: The one function in ``rebuild.py`` allowed to make them.
_THE_ONE_DOOR = "read_record_documents"


def _rebuild_source() -> str:
    from kyosei import rebuild as rebuild_module

    return Path(rebuild_module.__file__).resolve().read_text(encoding="utf-8")


def _functions_that_read_a_record_document(source: str) -> set[str]:
    """Every function in ``source`` that opens, lists or parses an export document.

    A nested function's calls are attributed to both it and its host, which
    over-collects; that is the safe direction here, since the assertion is that the set
    is exactly one name and an over-collection can only fail loudly.
    """
    import ast

    owners: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and _called_name(inner) in _DOCUMENT_READING_CALLS:
                owners.add(node.name)
    return owners


def _called_name(call) -> str | None:
    import ast

    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _the_door_consults_the_stamp(
    source: str, *, door: str = _THE_ONE_DOOR, stamp_reader: str = "document_schema_version"
) -> bool:
    """Whether ``door``, or anything it calls in this module, reads the stamp and compares
    it against this build's ``SCHEMA_VERSION``.

    Follows calls into module-level functions of the same file, so splitting the check
    out into a helper — which is how it is actually written — keeps this true, while
    deleting it does not.

    KYO-181 gave it the two parameters. There are now two doors and two stamp readers —
    one per artifact class, record documents and ledgers — and they need the identical
    property checked, so they get the identical checker rather than a copy of it that can
    be improved on one side only (law 3). The defaults keep KYO-179's call sites reading
    as they did.
    """
    import ast

    tree = ast.parse(source)
    defined = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    seen: set[str] = set()
    pending = [door]
    reads_the_stamp = False
    compares_to_schema_version = False
    while pending:
        name = pending.pop()
        if name in seen or name not in defined:
            continue
        seen.add(name)
        for inner in ast.walk(defined[name]):
            if isinstance(inner, ast.Call):
                called = _called_name(inner)
                if called == stamp_reader:
                    reads_the_stamp = True
                if called is not None:
                    pending.append(called)
            if isinstance(inner, ast.Compare) and any(
                isinstance(operand, ast.Name) and operand.id == "SCHEMA_VERSION"
                for operand in [inner.left, *inner.comparators]
            ):
                compares_to_schema_version = True
    return reads_the_stamp and compares_to_schema_version


def test_rebuild_reads_record_documents_through_exactly_one_door() -> None:
    """KYO-179.c4, first half: there is one place in ``rebuild.py`` that opens a record
    document, so there is one place the stamp can be skipped."""
    assert _functions_that_read_a_record_document(_rebuild_source()) == {_THE_ONE_DOOR}


def test_the_one_door_consults_the_schema_version_stamped_in_the_document() -> None:
    """KYO-179.c4, second half: that door reads the stamp and compares it to this
    build's, rather than merely being the place where reading happens."""
    assert _the_door_consults_the_stamp(_rebuild_source())


def test_a_second_document_reading_path_fails_this_check() -> None:
    """The canary, and the reason the two tests above are worth anything.

    A check that only ever runs against source it passes on proves nothing about what it
    would catch. This adds the exact regression KYO-179.c4 names — a second function that
    reads record documents on its own, the shape a future refactor would arrive in — and
    asserts the check reports it. Without this, "the AST says one door" is a sentence,
    not a test.
    """
    reintroduced = _rebuild_source() + (
        "\n\n"
        "def _load_documents_the_quick_way(export_dir):\n"
        "    return [\n"
        '        json.loads(p.read_text(encoding="utf-8"))\n'
        "        for p in export_module.record_export_files_in(export_dir)\n"
        "    ]\n"
    )
    assert _functions_that_read_a_record_document(reintroduced) == {
        _THE_ONE_DOOR,
        "_load_documents_the_quick_way",
    }


def test_a_door_that_stopped_consulting_the_stamp_fails_this_check() -> None:
    """The other canary: the door survives, the check inside it does not. This is the
    regression that would otherwise be invisible — every test of the refusal above would
    fail too, but only because they provoke it; nothing would say that the *property* had
    been lost rather than one message reworded."""
    source = _rebuild_source()
    without_the_check = source.replace("export_module.document_schema_version(", "int(")
    assert without_the_check != source, (
        "this canary is keyed on the call it removes; the call was renamed and this test "
        "no longer removes anything — re-point it at the new spelling"
    )
    assert not _the_door_consults_the_stamp(without_the_check)


# ------------------------------------------------------------------------- KYO-181
#
# The half KYO-179 named and left open. Its guard reads the stamp a RECORD DOCUMENT
# carries, which cannot see a bump that touched only a ledger — and two of this store's
# own bumps were exactly that: KYO-63 added three fields to a `_sweeps.jsonl` line at
# schema_version 10, KYO-94 added one to a `_briefs.jsonl` line at 12. Under either, an
# export from the later build has ledger rows carrying keys the older build does not read,
# NOT ONE RECORD DOCUMENT NEED HAVE CHANGED, and `rebuild` read the rows by name and
# dropped the extra keys in silence. That is the original defect intact, in the case
# history says is the common one.
#
# The form these tests are really about: the stamp is a key on every LINE, not a header
# line at the top of the file. Both would version correctly and only one keeps the
# property the event ledger was chosen for — append-only, merges cleanly (charter, Layer
# 2). A header has to be rewritten the first time the version moves, which is the only
# time it matters, and the byte it rewrites is line 1 of a file two branches are both
# appending to. `test_appending_after_a_bump_rewrites_nothing_and_keeps_each_line_at_its
# _own_version` is the one that a header-line implementation would fail.


def _ledger_path(project: Path, name: str) -> Path:
    """The file one ledger's lines are in.

    KYO-268.c2: the events ledger is a directory of bounded shards, and in these tests —
    small stores, a handful of lines — it is exactly one shard. Resolved here rather than
    in each test, so "the events ledger" keeps meaning one file to tests that are about
    VERSIONING and would otherwise all have to learn the layout. A test that is about the
    layout lives in ``tests/test_export.py``, where it belongs.
    """
    from kyosei.events_ledger import EVENTS_FILENAME, events_files_in

    export_directory = project / "kyosei" / "export"
    if name == EVENTS_FILENAME:
        files = events_files_in(export_directory)
        assert len(files) == 1, f"expected one events file, found {[p.name for p in files]}"
        return files[0]
    return export_directory / name


def _ledger_rows(project: Path, name: str) -> list[dict]:
    text = _ledger_path(project, name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _write_ledger_rows(project: Path, name: str, rows: list[dict]) -> None:
    _ledger_path(project, name).write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )


def _restamp_ledger(project: Path, name: str, version: int) -> list[dict]:
    """Rewrite every line of one ledger to stamp ``version``, and return the rows."""
    rows = _ledger_rows(project, name)
    assert rows, f"{name} has no lines to restamp"
    for row in rows:
        assert row["schema_version"] == SCHEMA_VERSION, "this build stamps its own version"
        row["schema_version"] = version
    _write_ledger_rows(project, name, rows)
    return rows


# --------------------------------------------------------------------- KYO-181.c1


def test_appending_after_a_bump_rewrites_nothing_and_keeps_each_line_at_its_own_version(
    kyo, project: Path
) -> None:
    """The form decision, tested as the property it was made for (KYO-181.c1).

    A ledger that has outlived a version bump holds lines written under both. This
    constructs that state — restamp the existing lines to the previous version, then
    append through the CLI — and asserts three things a HEADER-LINE form could not all
    satisfy at once:

    * the file still holds both versions, each line saying what wrote *it*. A header
      states one version for a whole file, so it would either misstate the old lines or
      misstate the new ones.
    * appending rewrote no byte that was already there, which is the append-only,
      merges-cleanly property the event ledger exists for. A header form would have had to
      rewrite line 1 at this exact moment — the one line in a file two branches are both
      appending to that a three-way merge cannot resolve by position.
    * the mixed file still rebuilds. Lines at or below this build are ordinary; the guard
      added a ceiling, not a requirement that a file be uniform.
    """
    new_record(kyo, title="before the bump", criteria=["a"])
    _restamp_ledger(project, "_events.jsonl", SCHEMA_VERSION - 1)
    before = _ledger_path(project, "_events.jsonl").read_bytes()

    new_record(kyo, title="after the bump", criteria=["a"])

    after = _ledger_path(project, "_events.jsonl").read_bytes()
    assert after.startswith(before), "appending rewrote a byte that was already written"
    assert len(after) > len(before)

    stamps = [row["schema_version"] for row in _ledger_rows(project, "_events.jsonl")]
    assert set(stamps) == {SCHEMA_VERSION - 1, SCHEMA_VERSION}, stamps
    assert stamps == sorted(stamps), "the older lines are the earlier ones"

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert kyo("doctor").code == 0


def test_no_ledger_carries_a_header_line(kyo, project: Path) -> None:
    """The negative half of the same decision, so "per line" cannot quietly become "per
    file" later. Every line of every ledger is a DATA row that also carries a version —
    never a bare version line, which is what a header form would leave at the top and what
    every consumer of these files would then have to learn to skip."""
    new_record(kyo, criteria=["a"])
    assert kyo("sweep").code == 0
    assert kyo("brief").code == 0

    for name in ("_events.jsonl", "_sweeps.jsonl", "_briefs.jsonl"):
        for row in _ledger_rows(project, name):
            assert "id" in row, f"{name} holds a line that is not a data row: {row}"
            assert set(row) != {"schema_version"}, name


def test_every_jsonl_ledger_the_export_declares_is_read_through_the_registry() -> None:
    """KYO-181.c1's mechanism, not its instance: a FIFTH ledger must be versioned and
    guarded by the act of existing, not by somebody remembering to extend a list.

    ``rebuild`` iterates ``ledgers.LEDGER_READERS`` rather than naming four files, so this
    is the assertion that keeps that registry complete — the same shape as
    ``schema.CONFIG_CONSUMERS`` (law 2), one boundary over. Add a ledger and forget the
    registry entry and this fails, rather than the ledger shipping unversioned.
    """
    from kyosei import events_ledger as events_ledger_module
    from kyosei import export as export_module
    from kyosei import ledger_repair as ledger_repair_module
    from kyosei import ledger_scrub as ledger_scrub_module
    from kyosei import ledgers as ledgers_module

    declared = {
        value
        for module in (
            export_module,
            events_ledger_module,
            ledgers_module,
            ledger_scrub_module,
            ledger_repair_module,
        )
        for name, value in vars(module).items()
        if name.endswith("_FILENAME") and isinstance(value, str) and value.endswith(".jsonl")
    }
    assert declared, "no ledger filenames found — has the naming convention changed?"
    assert declared == set(ledgers_module.LEDGER_READERS)


# --------------------------------------------------------------------- KYO-181.c2


def test_rebuild_refuses_a_ledger_line_from_a_later_schema(kyo, project: Path) -> None:
    """The refusal itself, through the CLI: exit 1, the file named, the later version
    named, and no reconstructed store. Same two message assertions KYO-179 makes at this
    boundary, for the same reason — the reader must be able to tell which side is older
    and what to install, and must not be pointed at a command that would write the older
    schema over the newer one."""
    new_record(kyo, criteria=["a"])
    assert kyo("sweep").code == 0
    _restamp_ledger(project, "_sweeps.jsonl", SCHEMA_VERSION + 1)
    db_path = project / "kyosei" / "kyosei.db"
    db_path.unlink()

    result = kyo("rebuild")
    assert result.code == EXIT_ERROR, result
    assert "_sweeps.jsonl" in result.err, result.err
    assert str(SCHEMA_VERSION + 1) in result.err, result.err
    assert "ledger line(s) written by a later schema" in result.err, result.err
    assert "kyo rebuild" not in result.err, result.err
    assert not db_path.exists(), "a refused rebuild must not reconstruct anything"


def test_the_ledger_refusal_reads_as_the_same_mechanism_as_the_document_one(
    kyo, project: Path
) -> None:
    """KYO-179's wording, reused rather than reinvented for the same situation one file
    over. Checked as the clauses that matter rather than word for word — a message pinned
    by its full text is a message nobody may improve — and checked against the DOCUMENT
    refusal's own text rather than against a copy of it here, so the two cannot drift
    apart without this failing."""
    record = new_record(kyo, criteria=["a"])
    assert kyo("sweep").code == 0
    as_written = _ledger_rows(project, "_sweeps.jsonl")
    _restamp_ledger(project, "_sweeps.jsonl", SCHEMA_VERSION + 4)
    (project / "kyosei" / "kyosei.db").unlink()
    ledger_err = kyo("rebuild").err

    assert "intact" in ledger_err, ledger_err
    assert "stale" in ledger_err, ledger_err
    assert (
        f"install a kyosei that speaks schema_version {SCHEMA_VERSION + 4} or later"
    ) in ledger_err, ledger_err
    assert "the desktop app and every project's hooks run" in ledger_err, ledger_err
    assert "bundled with the application" not in ledger_err, ledger_err

    # The same store, the same later version, the other half of the export: everything
    # after the list of offending files must be identical prose.
    _write_ledger_rows(project, "_sweeps.jsonl", as_written)
    _bump_document_schema_version(project, record, SCHEMA_VERSION + 4)
    document_err = kyo("rebuild").err

    shared = "This kyosei build speaks schema_version"
    assert ledger_err[ledger_err.index(shared) :].replace("KYO-181", "") == (
        document_err[document_err.index(shared) :].replace("KYO-179", "")
    )


def test_the_ledger_refusal_fires_before_anything_is_written(kyo, project: Path) -> None:
    """KYO-181.c2's teeth, and the reason the ledger read had to move out of ``_load``.

    ``--force`` unlinks the existing database near the top of ``rebuild_from_export`` and
    ``write_config`` rewrites ``kyosei/config.yml`` at the end. The ledgers used to be read
    between those two, deep inside the load — so a guard left where the reading was would
    have deleted the store it was protecting before it could refuse. Under the common,
    ledger-only bump this record is about, that is the ONLY guard there is."""
    record = new_record(kyo, criteria=["a"])
    assert kyo("sweep").code == 0
    _restamp_ledger(project, "_sweeps.jsonl", SCHEMA_VERSION + 1)
    db_path = project / "kyosei" / "kyosei.db"
    config_path = project / "kyosei" / "config.yml"
    db_before = db_path.read_bytes()
    config_before = config_path.read_text(encoding="utf-8")

    result = kyo("rebuild", "--force")
    assert result.code == EXIT_ERROR, result
    assert db_path.read_bytes() == db_before, "the live database was touched"
    assert config_path.read_text(encoding="utf-8") == config_before, "config.yml was rewritten"
    # And the store still opens: the refusal cost the project nothing.
    assert kyo("show", record, "--json").json["id"] == record


def test_the_ledger_refusal_carries_a_payload_a_program_can_branch_on(
    kyo, project: Path
) -> None:
    """KYO-178's shape at this boundary. The desktop app (KYO-175) points a pinned runtime
    at stores it does not control and has to tell "this export is newer than me" from
    every other reason a rebuild failed — opposite responses, one of them an update
    prompt. Substring-matching the prose makes that branching a hostage of the next
    improvement to the wording, so the refusal carries the facts alongside it.

    ``files`` lists every offending file, not the three the sentence had room for: prose
    truncates for a human, a payload must not truncate for a program."""
    new_record(kyo, criteria=["a"])
    assert kyo("sweep").code == 0
    assert kyo("brief").code == 0
    _restamp_ledger(project, "_sweeps.jsonl", SCHEMA_VERSION + 2)
    _restamp_ledger(project, "_briefs.jsonl", SCHEMA_VERSION + 1)
    (project / "kyosei" / "kyosei.db").unlink()

    result = kyo("rebuild", "--json")
    assert result.code == EXIT_ERROR, result
    payload = result.json["export_schema_ahead"]
    assert payload["source"] == "ledgers"
    assert payload["build_schema_version"] == SCHEMA_VERSION
    assert payload["export_schema_version"] == SCHEMA_VERSION + 2
    assert {entry["file"] for entry in payload["files"]} == {"_sweeps.jsonl", "_briefs.jsonl"}
    assert all(entry["lines"] >= 1 for entry in payload["files"])


def test_the_document_refusal_carries_the_same_payload_under_the_same_key(
    kyo, project: Path
) -> None:
    """The other half of "one mechanism, not two". A consumer asking "is this export from
    a later build" must not have to know which artifact class tripped it — so the two
    refusals answer under the same key, in the same shape, and differ only in ``source``.
    Without this, the ledger guard would be a second mechanism wearing the first one's
    wording."""
    record = new_record(kyo, criteria=["a"])
    _bump_document_schema_version(project, record, SCHEMA_VERSION + 3)
    (project / "kyosei" / "kyosei.db").unlink()

    payload = kyo("rebuild", "--json").json["export_schema_ahead"]
    assert payload["source"] == "record-documents"
    assert payload["build_schema_version"] == SCHEMA_VERSION
    assert payload["export_schema_version"] == SCHEMA_VERSION + 3
    assert [entry["file"] for entry in payload["files"]] == [f"{record}.json"]


def test_a_ledger_stamp_that_is_not_a_version_number_is_refused_by_name(
    kyo, project: Path
) -> None:
    """A stamp that is not an integer means the line cannot say what format it is in, and
    that is not a fact to guess at — ``int("17")`` and ``int(17.5)`` both succeed and the
    second is silently wrong, which is the failure shape this whole record is about.
    Refused cleanly and by filename, never as a bare ``TypeError`` out of the compat
    machinery. Same rule, same wording, as the document case one file over."""
    new_record(kyo, criteria=["a"])
    assert kyo("sweep").code == 0
    rows = _ledger_rows(project, "_sweeps.jsonl")
    rows[0]["schema_version"] = "seventeen"
    _write_ledger_rows(project, "_sweeps.jsonl", rows)
    (project / "kyosei" / "kyosei.db").unlink()

    result = kyo("rebuild")
    assert result.code == EXIT_ERROR, result
    assert "_sweeps.jsonl" in result.err, result.err
    assert "not a version number" in result.err, result.err
    assert not (project / "kyosei" / "kyosei.db").exists()


def test_a_ledger_line_at_this_builds_own_version_still_rebuilds(kyo, project: Path) -> None:
    """The boundary is ``>``, not ``>=``. Off by one here and every healthy store in
    existence is refused, which is a worse outcome than the defect — and this is the
    likelier direction to get wrong, because every line of every ledger in every live
    store stamps exactly this number."""
    record = new_record(kyo, title="widget", criteria=["a"])
    assert kyo("sweep").code == 0
    assert all(
        row["schema_version"] == SCHEMA_VERSION for row in _ledger_rows(project, "_sweeps.jsonl")
    )

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert kyo("show", record, "--json").json["title"] == "widget"


# --------------------------------------------------------------------- KYO-181.c3


def _a_ledger_only_bump(kyo, project: Path) -> dict:
    """Build the export a LEDGER-ONLY schema bump produces — the KYO-63 / KYO-94 shape.

    One `_sweeps.jsonl` row gains a field this build does not read and the stamp the later
    build would have written. Nothing else in the export changes: every record document is
    left exactly as this build wrote it, which is the whole point. Returns the row.
    """
    new_record(kyo, title="widget", criteria=["a"])
    assert kyo("sweep").code == 0
    rows = _ledger_rows(project, "_sweeps.jsonl")
    assert len(rows) == 1
    rows[0]["coverage_confidence"] = "high"  # the field the later build added
    rows[0]["schema_version"] = SCHEMA_VERSION + 1
    _write_ledger_rows(project, "_sweeps.jsonl", rows)
    return rows[0]


def test_a_bump_that_changed_only_a_ledger_is_caught(kyo, project: Path) -> None:
    """KYO-181.c3, and the case history says is the common one rather than the exotic one.

    The precondition is asserted, not assumed: EVERY record document still stamps this
    build's own version. If the guard passed because some document had also moved, it
    would not have been tested at all — it would be KYO-179's guard firing, with this
    record's mechanism never exercised.
    """
    row = _a_ledger_only_bump(kyo, project)
    export_directory = project / "kyosei" / "export"

    documents = list(export_directory.glob("TST-*.json"))
    assert documents, "no record documents in this export"
    for path in documents:
        stamp = json.loads(path.read_text(encoding="utf-8"))["schema_version"]
        assert stamp == SCHEMA_VERSION, (
            f"{path.name} moved too — this test would then be exercising KYO-179's guard"
        )

    (project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == EXIT_ERROR, result
    assert "_sweeps.jsonl" in result.err, result.err
    assert str(SCHEMA_VERSION + 1) in result.err, result.err
    assert not (project / "kyosei" / "kyosei.db").exists()
    assert row["coverage_confidence"] == "high"


def test_that_ledger_only_bump_is_invisible_to_the_document_guard(kyo, project: Path) -> None:
    """The proof that the test above passes for this record's reason and not KYO-179's.

    The two doors are called directly on the same export: the document door returns a full
    set of documents without complaint, because there is genuinely nothing wrong with them
    and no stamp anywhere in them is ahead — and the ledger door refuses. That gap is
    exactly what KYO-179 described when it left this open, reproduced here so that a
    future change which made the ledger guard redundant would have to explain itself.
    """
    from kyosei.errors import KyoseiError
    from kyosei.rebuild import read_ledger_rows, read_record_documents

    _a_ledger_only_bump(kyo, project)
    export_directory = project / "kyosei" / "export"

    assert read_record_documents(export_directory), "the document door found nothing to check"

    with pytest.raises(KyoseiError) as caught:
        read_ledger_rows(export_directory)
    assert "_sweeps.jsonl" in str(caught.value)


def test_the_extra_key_a_later_build_wrote_was_being_dropped_in_silence(
    kyo, project: Path
) -> None:
    """The defect itself, reproduced with the stamp removed — which is the only way to see
    it now, and is worth seeing because it says precisely what the stamp buys.

    The same row, the same unreadable extra field, no version stamp: rebuild succeeds and
    the field reaches nothing — the reconstructed store has nowhere to put it and said so
    to nobody. That was the behaviour for EVERY ledger-only bump before this record. It is
    unchanged for a line that carries no stamp, and that is not a gap left open: a build
    cannot detect a format it has no information about, which is why the fix had to be a
    stamp rather than a cleverer reader (law 6: an unknown must not read as either answer).

    Checked against the DATABASE, not the file. The file is the input to a rebuild and is
    never rewritten by one, so the extra key is still sitting in it afterwards — which is
    the point, and is precisely why this was invisible: the export looked untouched while
    the store built from it had silently lost a field.
    """
    row = _a_ledger_only_bump(kyo, project)
    del row["schema_version"]
    _write_ledger_rows(project, "_sweeps.jsonl", [row])

    db_path = project / "kyosei" / "kyosei.db"
    db_path.unlink()
    assert kyo("rebuild").code == 0

    conn = sqlite3.connect(db_path)
    try:
        columns = {info[1] for info in conn.execute("PRAGMA table_info(sweep_runs)")}
        stored = conn.execute("SELECT COUNT(*) FROM sweep_runs").fetchone()[0]
    finally:
        conn.close()
    assert stored == 1, "the row loaded"
    assert "coverage_confidence" not in columns, "the later build's field had nowhere to land"
    # And it is still in the export, unremarked: the input was never rewritten.
    assert "coverage_confidence" in _ledger_rows(project, "_sweeps.jsonl")[0]


# --------------------------------------------------------------------- KYO-181.c4


def test_a_ledger_mixing_stamped_and_unstamped_lines_still_rebuilds(
    kyo, project: Path, write_file
) -> None:
    """KYO-181.c4 in the shape a real store reaches it. Every ledger in every existing
    store is a file whose lines predate this bump; the first append after upgrading makes
    it a mixed file, and it stays mixed forever after — the lines are never rewritten.
    That is the normal state, not a corrupt one, and both halves have to load.

    The unstamped half is a genuine pre-17 line: no ``schema_version`` key at all. It is
    read through the same door as the stamped one and tolerated by the same rule that
    always tolerated it — the absence of a FIELD, never a version comparison.
    """
    older = kyo("note", "--shift", "--file", write_file("s.md", "an older shift"), "--json")
    assert older.code == 0, older
    rows = _ledger_rows(project, "_shift_notes.jsonl")
    for row in rows:
        del row["schema_version"]
    _write_ledger_rows(project, "_shift_notes.jsonl", rows)

    newer = kyo("note", "--shift", "--file", write_file("s2.md", "a newer shift"), "--json")
    assert newer.code == 0, newer
    stamps = [row.get("schema_version") for row in _ledger_rows(project, "_shift_notes.jsonl")]
    assert stamps == [None, SCHEMA_VERSION], stamps

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert kyo("doctor").code == 0
    # Both halves survived the round trip, the unstamped one included.
    assert kyo("show", older.json["shift_note_id"], "--json").json["text"] == "an older shift"
    assert kyo("show", newer.json["shift_note_id"], "--json").json["text"] == "a newer shift"


# --------------------------------------------------------------------- KYO-181: one door
#
# The same property KYO-179.c4 pins for record documents, for the artifact class this
# record adds a guard to. A test that only exercised the refusal would stay green the day
# somebody adds a second ledger-reading path that skips the check — which is exactly how
# the ledgers came to be read by name, with their extra keys dropped, in the first place.

#: What "this function is reading a ledger" looks like in ``rebuild.py``. The registry name
#: is included because reading through it is how the door is actually written, and the four
#: reader names because a hand-rolled second path would use one of them.
_LEDGER_READING_NAMES = frozenset(
    {
        "LEDGER_READERS",
        "read_events_in",
        "read_sweep_runs_in",
        "read_brief_runs_in",
        "read_shift_notes_in",
        "read_person_notes_in",
        "read_person_note_tag_changes_in",
    }
)

#: The one function in ``rebuild.py`` allowed to name them.
_THE_LEDGER_DOOR = "read_ledger_rows"


def _functions_that_read_a_ledger(source: str) -> set[str]:
    """Every function in ``source`` that names a ledger reader or the reader registry."""
    import ast

    owners: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            named = None
            if isinstance(inner, ast.Attribute):
                named = inner.attr
            elif isinstance(inner, ast.Name):
                named = inner.id
            if named in _LEDGER_READING_NAMES:
                owners.add(node.name)
    return owners


def test_rebuild_reads_the_ledgers_through_exactly_one_door() -> None:
    """First half: one place in ``rebuild.py`` reads a ledger, so there is one place the
    stamp can be skipped. ``_load`` used to be that place, which is what made the version
    question unaskable — by the time it ran, a database already existed."""
    assert _functions_that_read_a_ledger(_rebuild_source()) == {_THE_LEDGER_DOOR}


def test_the_ledger_door_consults_the_schema_version_stamped_in_the_line() -> None:
    """Second half: that door reads the stamp and compares it to this build's, rather than
    merely being the place where reading happens."""
    assert _the_door_consults_the_stamp(
        _rebuild_source(), door=_THE_LEDGER_DOOR, stamp_reader="ledger_schema_version"
    )


def test_a_second_ledger_reading_path_fails_this_check() -> None:
    """The canary. A check that only ever runs against source it passes on proves nothing
    about what it would catch, so this adds the exact regression — a second function
    reading a ledger on its own, the shape a future refactor arrives in — and asserts the
    check reports it."""
    reintroduced = _rebuild_source() + (
        "\n\n"
        "def _load_the_sweeps_the_quick_way(export_dir):\n"
        "    return ledgers_module.read_sweep_runs_in(export_dir)\n"
    )
    assert _functions_that_read_a_ledger(reintroduced) == {
        _THE_LEDGER_DOOR,
        "_load_the_sweeps_the_quick_way",
    }


def test_a_ledger_door_that_stopped_consulting_the_stamp_fails_this_check() -> None:
    """The other canary: the door survives, the check inside it does not. Every test of
    the refusal above would fail too — but only because they provoke it; nothing else
    would say the PROPERTY had been lost rather than one message reworded."""
    source = _rebuild_source()
    without_the_check = source.replace("export_module.ledger_schema_version(", "int(")
    assert without_the_check != source, (
        "this canary is keyed on the call it removes; the call was renamed and this test "
        "no longer removes anything — re-point it at the new spelling"
    )
    assert not _the_door_consults_the_stamp(
        without_the_check, door=_THE_LEDGER_DOOR, stamp_reader="ledger_schema_version"
    )


def test_the_export_ahead_refusal_cannot_be_raised_without_a_payload() -> None:
    """KYO-181, mirroring KYO-178.c4's
    ``test_no_schema_version_refusal_can_be_raised_without_a_payload``.

    Two halves, and the first one is why this test exists at all rather than being a
    formality.

    STATICALLY: both version guards route through the single refusal helper, and that
    helper raises :class:`kyosei.errors.ExportSchemaAhead` INLINE. The inline part is
    load-bearing: ``tests/test_kyo5_error_remedies.py`` walks every ``raise`` in
    ``src/kyosei/`` and matches it BY CLASS NAME on the raise node, so building an error,
    assigning ``.data`` and raising the variable takes the site off the remedy-coverage
    surface entirely — after which this message could start naming a destructive command
    with nothing left to notice. That is not hypothetical; it is what the first draft of
    this record did, and that check is what caught it.

    BY CONSTRUCTION: the class cannot be built without the fields the payload is made of.
    There is no partially-populated form of it to raise, which is why the payload lives in
    a class rather than in a ``.data`` assignment beside each raise — an assignment is a
    line somebody can forget, and forgetting it fails nothing at the moment it happens.
    """
    import ast

    from kyosei.errors import EXPORT_AHEAD_LEDGERS, EXPORT_SCHEMA_AHEAD_KEY, ExportSchemaAhead

    tree = ast.parse(_rebuild_source())
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    helper = "_refuse_an_export_from_a_later_build"

    raised = {
        _called_name(node.exc)
        for node in ast.walk(functions[helper])
        if isinstance(node, ast.Raise) and node.exc is not None
    }
    assert raised == {"ExportSchemaAhead"}, raised

    for guard in ("_refuse_documents_from_a_later_build", "_refuse_ledger_rows_from_a_later_build"):
        calls = {
            _called_name(node)
            for node in ast.walk(functions[guard])
            if isinstance(node, ast.Call)
        }
        assert helper in calls, f"{guard} no longer refuses through the shared message"

    complete = {
        "source": EXPORT_AHEAD_LEDGERS,
        "build_schema_version": 1,
        "export_schema_version": 2,
        "files": [{"file": "_sweeps.jsonl", "schema_version": 2, "lines": 1}],
    }
    assert ExportSchemaAhead("refused", **complete).data[EXPORT_SCHEMA_AHEAD_KEY]["source"] == (
        EXPORT_AHEAD_LEDGERS
    )
    for omitted in complete:
        with pytest.raises(TypeError):
            ExportSchemaAhead("refused", **{k: v for k, v in complete.items() if k != omitted})
    with pytest.raises(ValueError):
        ExportSchemaAhead("refused", **{**complete, "source": "somewhere-else"})


# ------------------------------------------------------------------------- KYO-172
#
# The stop-audit read selects `events` by `command`, and until schema_version 18 no index
# covered that column: every caller scanned the whole ledger to reach the rows it wanted.
# Three callers pay it -- `kyo doctor` (the Stop hook runs it at every session end), `kyo
# worktrees`, and `check-clean --subagent-stop`, which since KYO-169 runs once per running
# subagent per ~32 seconds. The first two are human-paced; the third is machine-paced and
# scales with concurrency, which is what turned a background cost into one worth an index.
#
# What is pinned below is the PROPERTY rather than a number, for the reason a timing
# assertion cannot be a test: a wall clock on a laptop under load is not reproducible and a
# threshold picked to pass today is a flake tomorrow. The plan the query gets -- SEARCH
# through the index, and no sort -- is exact, cache-independent, and goes red the moment the
# index is dropped or the query is rewritten so it can no longer be used. The before/after
# timings this record asked for are in KYO-172.d1's report, measured with KYO-169's own
# instrument against a clone of this repository's live ledger.

#: The one row shape `record_stop_audit` writes (`stop_audit.record_stop_audit`), reduced
#: to what `stop_audits` reads back out of it.
def _a_stop_audit_after(outcome: str, *, agent_id: str, candidates: list[str]) -> dict:
    return {
        "stop_audit": {
            "outcome": outcome,
            "resolved": False,
            "directory": "/tmp/wt",
            "why": "no running dispatch claimed this tree",
            "dispatch_id": None,
            "candidates": candidates,
            "helper_of": None,
        },
        "session_id": "s-1",
        "agent_id": agent_id,
    }


def _seed_a_mixed_ledger(project: Path, *, stops: int = 6) -> list[str]:
    """Write stop-audit rows interleaved with events of other commands, through the real
    write path, and return the stop rows' event ids in the order `stop_audits` must
    return them.

    Interleaved on purpose: a ledger of nothing but stop rows cannot tell an index that
    selects the right rows from one that returns everything.
    """
    from kyosei import events_ledger as events_ledger_module
    from kyosei import ledgers as ledgers_module
    from kyosei.stop_audit import STOP_AUDIT_COMMAND
    from kyosei.store import open_store

    store = open_store(project)
    stop_ids: list[str] = []
    try:
        for index in range(stops):
            with store.transaction():
                noise = store.record_event(
                    actor="controller",
                    command=f"some-other-command {index}",
                    record_id=None,
                    before=None,
                    after={"unrelated": index},
                )
                stop = store.record_event(
                    actor="hook",
                    command=STOP_AUDIT_COMMAND,
                    record_id=None,
                    before=None,
                    after=_a_stop_audit_after(
                        "unresolved",
                        agent_id=f"agent-{index}",
                        candidates=["TST-1.d1", "TST-1.d2"],
                    ),
                )
            events_ledger_module.append_events(store, [noise, stop])
            ledgers_module.write_allocations(store)
            stop_ids.append(stop["id"])
    finally:
        store.conn.close()
    return stop_ids


def _the_query_stop_audits_actually_runs() -> str:
    """The SQL literal inside ``stop_audit.stop_audits``, read out of its AST.

    Read rather than retyped so this test asks about the query that RUNS. A copy here
    would keep passing after somebody reworded the real one into a shape the index cannot
    serve, which is the only regression worth catching.
    """
    import ast

    from kyosei import stop_audit

    source = Path(stop_audit.__file__).resolve().read_text(encoding="utf-8")
    function = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "stop_audits"
    )
    literals = [
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "FROM events" in node.value
    ]
    assert len(literals) == 1, literals
    return literals[0]


def test_the_stop_audit_read_searches_the_index_instead_of_scanning_the_ledger(
    kyo, project: Path, db
) -> None:
    """KYO-172.c1's mechanism. Delete ``idx_events_command`` from ``schema.DDL`` and this
    fails, which is what makes the index a mechanism rather than an optimisation somebody
    remembers.

    Two assertions, and the second is the one the two-column form buys. ``SEARCH`` (not
    ``SCAN``) is the index being used at all; the absence of ``TEMP B-TREE`` is the
    ``ORDER BY seq`` being served by the same index rather than by sorting the result. An
    index on ``command`` alone passes the first and fails the second.
    """
    from kyosei.stop_audit import STOP_AUDIT_COMMAND

    new_record(kyo, criteria=["a"])
    _seed_a_mixed_ledger(project)

    conn = db()
    try:
        plan = [
            row["detail"]
            for row in conn.execute(
                f"EXPLAIN QUERY PLAN {_the_query_stop_audits_actually_runs()}",
                (STOP_AUDIT_COMMAND,),
            )
        ]
    finally:
        conn.close()

    assert any("idx_events_command" in line and "SEARCH" in line for line in plan), plan
    assert not any("TEMP B-TREE" in line for line in plan), plan


def test_every_caller_of_the_stop_audit_read_returns_exactly_what_it_returned_before(
    kyo, git_project: Path, db
) -> None:
    """KYO-172.c3: the index changes cost and nothing else.

    Run all three callers -- ``stop_audits`` itself, ``kyo doctor`` and ``kyo worktrees``
    -- against one store, drop the index, run them again, and require identical answers.
    Dropping the index rather than adding it is what makes this a fair comparison: the
    same database, the same rows, the same build, one index apart.

    The row set is compared first and by id, so a query that started returning a
    DIFFERENT set of rows at the same cost -- the failure an index is most likely to be
    the occasion for -- is named as such instead of surfacing as some downstream count.
    """
    import json as json_module
    import re

    from kyosei import stop_audit
    from kyosei.store import open_store

    new_record(kyo, criteria=["a"])
    seeded = _seed_a_mixed_ledger(git_project)

    def _answers() -> tuple[list[str], int, str, str]:
        store = open_store(git_project)
        try:
            ledger = stop_audit.stop_audits(store)
        finally:
            store.conn.close()
        doctor = kyo("doctor", "--json")
        worktrees = kyo("worktrees", "--json")
        # Timestamps and durations move between two runs of the same command and are not
        # what this test is about; the row set and every count derived from it are.
        scrub = lambda blob: re.sub(  # noqa: E731
            r"\d{4}-\d{2}-\d{2}T[0-9:.]+Z", "<at>", json_module.dumps(blob, sort_keys=True)
        )
        return (
            [audit.event_id for audit in ledger.audits],
            len(ledger.unresolved),
            scrub(doctor.json),
            scrub(worktrees.json),
        )

    with_index = _answers()
    assert with_index[0] == seeded, with_index[0]

    conn = db()
    try:
        conn.execute("DROP INDEX idx_events_command")
    finally:
        conn.close()

    without_index = _answers()
    assert without_index[0] == with_index[0]
    assert without_index[1] == with_index[1]
    assert without_index[2] == with_index[2]
    assert without_index[3] == with_index[3]


def test_a_store_rebuilt_from_a_pre_kyo172_export_gets_the_index(
    kyo, project: Path
) -> None:
    """KYO-172.c2, and the reason this bump needed no rebuild default.

    An export written at 17 is missing no key: the index is a property of the DATABASE,
    not of the serialization, so every document and every ledger line at the prior format
    loads exactly as it did and there is nothing for ``EXPORT_FIELD_DEFAULTS`` to fill in.
    What the version bump actually buys is this test's last assertion -- ``open_store``
    refuses a database behind the build and names ``kyo rebuild --force``, and the rebuild
    runs the new DDL, so an existing store acquires the index by the ordinary upgrade path
    rather than by a migration this system does not have.
    """
    record = new_record(kyo, title="widget", criteria=["a"])
    assert kyo("sweep").code == 0

    export_directory = project / "kyosei" / "export"
    _bump_document_schema_version(project, record, SCHEMA_VERSION - 1)
    for name in ("_events.jsonl", "_sweeps.jsonl"):
        rows = _ledger_rows(project, name)
        for row in rows:
            row["schema_version"] = SCHEMA_VERSION - 1
        _write_ledger_rows(project, name, rows)
    assert json.loads((export_directory / f"{record}.json").read_text(encoding="utf-8"))[
        "schema_version"
    ] == SCHEMA_VERSION - 1

    (project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result

    assert kyo("show", record, "--json").json["title"] == "widget"
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    try:
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'events'"
            )
        }
    finally:
        conn.close()
    assert "idx_events_command" in indexes, indexes


# ------------------------------------------------------------------------- KYO-188
#
# `_allocations.json` was the one export artifact left unversioned after KYO-181, recorded
# there as a stated residue. KYO-188 settles it: it stays unversioned, and the reason is
# not "it is not a ledger" (which explains only why the PER-LINE form does not apply) but
# that a stamp on it would witness nothing the export does not already witness.
#
# The whole argument rests on one premise, so the premise is what is tested here rather
# than the conclusion. `write_allocations` is never called alone: every caller writes, in
# the same function and the same command, an artifact carrying this build's
# `schema_version` -- because the snapshot exists to record that an id was handed out,
# handing one out is a mutation, and a mutation writes an event. An export whose counters
# came from a later build therefore always holds a later-stamped line or document beside
# them, and `kyo rebuild` refuses on that before it reads a counter.
#
# Add an eighth writer that touches the snapshot alone and this goes red, which is exactly
# when the decision has to be reopened. Without it, the reasoning in
# `ledgers.LEDGER_READERS` would be a paragraph that could quietly stop being true.

#: Every export writer that stamps what it writes with this build's ``schema_version`` —
#: the four ledger appenders (``export.stamp_ledger_row``) and the two record-document
#: writers (``export.record_document``). Any one of them beside a ``write_allocations``
#: call is enough: the guard in ``rebuild.py`` refuses the whole export on any single
#: later-stamped artifact, so the counters do not need their own.
_STAMPED_EXPORT_WRITERS = frozenset(
    {
        "append_events",
        "append_sweep_run",
        "append_brief_run",
        "append_shift_note",
        "append_person_note",
        "append_person_note_tag_change",
        "write_exports",
        "refresh_exports",
    }
)


def _functions_calling(source: str, name: str) -> dict[str, set[str]]:
    """Every function in ``source`` that calls ``name``, mapped to all the names it calls.

    Attribute and bare calls both count (``ledgers.write_allocations`` and
    ``write_allocations``), because which spelling a module uses is an import style rather
    than a fact about what it writes.
    """
    import ast

    found: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        calls = {_called_name(inner) for inner in ast.walk(node) if isinstance(inner, ast.Call)}
        if name in calls:
            found[node.name] = {call for call in calls if call is not None}
    return found


def _package_sources() -> dict[str, str]:
    import kyosei

    package = Path(kyosei.__file__).resolve().parent
    return {
        path.name: path.read_text(encoding="utf-8") for path in sorted(package.glob("*.py"))
    }


def test_nothing_writes_the_allocation_snapshot_without_a_stamped_artifact_beside_it() -> None:
    """KYO-188.c1's premise, pinned across the whole package.

    Seven writers today, and every one of them writes something stamped in the same
    function. The set of writers is not asserted — a new mutating command is expected to
    write counters — only the accompaniment, which is the property the decision needs.
    """
    writers: dict[str, set[str]] = {}
    for name, source in _package_sources().items():
        for function, calls in _functions_calling(source, "write_allocations").items():
            if name == "ledgers.py" and function == "write_allocations":
                continue  # the definition itself, not a caller
            writers[f"{name}:{function}"] = calls

    assert writers, "no writers of _allocations.json found — has the function been renamed?"
    unaccompanied = {
        site: sorted(calls)
        for site, calls in writers.items()
        if not (calls & _STAMPED_EXPORT_WRITERS)
    }
    assert not unaccompanied, (
        "KYO-188: these write kyosei/export/_allocations.json without writing any "
        f"schema_version-stamped artifact in the same command: {sorted(unaccompanied)}. "
        "That is the premise the decision not to version the snapshot rests on (see "
        "ledgers.LEDGER_READERS), so either the new writer gains a stamped companion or "
        "the decision has to be reopened — it must not simply be re-pinned here"
    )


def test_a_writer_with_no_stamped_companion_fails_this_check() -> None:
    """The canary. A check that only ever runs against source it passes on proves nothing
    about what it would catch, so this adds the exact regression the test above exists for
    — a command that refreshes the counters and writes nothing else — and asserts it is
    reported."""
    reintroduced = (
        "def cmd_recount(ctx):\n"
        "    store = ctx.store()\n"
        "    ledgers.write_allocations(store)\n"
        "    return Output(['recounted'], {'ok': True})\n"
    )
    found = _functions_calling(reintroduced, "write_allocations")
    assert set(found) == {"cmd_recount"}
    assert not (found["cmd_recount"] & _STAMPED_EXPORT_WRITERS)


def test_the_counters_a_later_build_wrote_are_refused_by_the_stamp_beside_them(
    kyo, project: Path
) -> None:
    """KYO-188.c1 as behaviour, and the answer to "what would a stamp here have bought?".

    The state a stamp on ``_allocations.json`` would exist to catch is an export whose
    COUNTERS came from a later build. This constructs it the only way it can occur — a
    later build ran a mutating command, which moved a counter and appended an event line
    stamped with its version — and shows the rebuild refused before any counter was read.
    The counters are still sitting in the file, unread and unremarked, which is the point:
    what refused is the artifact written beside them.
    """
    new_record(kyo, criteria=["a"])
    export_directory = project / "kyosei" / "export"
    counters = json.loads(
        (export_directory / "_allocations.json").read_text(encoding="utf-8")
    )
    assert counters, "the fixture must have allocated something"
    assert "schema_version" not in counters

    _restamp_ledger(project, "_events.jsonl", SCHEMA_VERSION + 1)
    (project / "kyosei" / "kyosei.db").unlink()

    result = kyo("rebuild")
    assert result.code == EXIT_ERROR, result
    assert "_events.jsonl" in result.err, result.err
    assert str(SCHEMA_VERSION + 1) in result.err, result.err
    assert not (project / "kyosei" / "kyosei.db").exists()
    # Untouched and unread — the refusal came from the line written beside them.
    assert (
        json.loads((export_directory / "_allocations.json").read_text(encoding="utf-8"))
        == counters
    )


def test_the_three_consumers_still_read_the_snapshot_as_a_plain_mapping(
    kyo, project: Path
) -> None:
    """KYO-188.c2's other half, kept as a live assertion rather than a claim in prose.

    The decision's cost side is that three consumers read this file as ``{scope: counter}``
    — ``doctor.check_exports``, ``currency.db_export_currency``, and
    ``apps/desktop/tests/fixture.ts`` (``Record<string, number>``). The first two are
    exercised here through the commands that call them; the TypeScript one cannot be run
    from this suite, and the shape it depends on is the shape asserted below.

    If a version is ever added, this is the test that says what it has to survive.
    """
    from kyosei import currency
    from kyosei.store import open_store

    new_record(kyo, criteria=["a"])
    counters = json.loads(
        (project / "kyosei" / "export" / "_allocations.json").read_text(encoding="utf-8")
    )
    assert counters and all(
        isinstance(key, str) and isinstance(value, int) for key, value in counters.items()
    ), counters

    assert kyo("doctor").code == 0, "check_exports compares this file as a parsed dict"

    store = open_store(project)
    try:
        seam = currency.db_export_currency(store)
    finally:
        store.conn.close()
    assert seam.would_reissue_an_id is False, seam


# ------------------------------------------------------------------------- KYO-418.c1
#
# Schema 25 is a single bump: an event line carries `after` and a
# `changed` map instead of a whole `before`, and a brief run records its session. Every
# ledger on disk before it holds the old shapes, never rewritten, so a schema-24 export
# has to rebuild into a store whose readers answer exactly as they did.


def test_rebuild_accepts_a_schema_24_export_whose_ledger_holds_whole_before_lines(
    kyo, project: Path, tmp_path: Path
) -> None:
    from kyosei import events_ledger, ledgers
    from kyosei.store import open_store

    record = new_record(kyo, title="Written at 24")
    description = tmp_path / "d.md"
    description.write_text("The description, as a schema-24 build edited it.\n", encoding="utf-8")
    assert kyo("edit", record, "--description-file", str(description)).code == 0
    assert kyo("edit", record, "--title", "Retitled at 24").code == 0
    assert kyo("brief", "--session-start").code == 0

    # What a schema-24 build wrote for the same history: every event with a whole
    # `before` (null on a creation) and no `changed`, every brief line without a
    # session, every line and document stamped 24.
    rows = _ledger_rows(project, events_ledger.EVENTS_FILENAME)
    edit_ids = []
    for row in rows:
        prior = events_ledger.event_before_document(row)
        if prior is not None and row["record_id"] == record:
            edit_ids.append(row["id"])
        row.pop(events_ledger.EVENT_CHANGED_KEY, None)
        row["before"] = prior
        row["schema_version"] = 24
    assert len(edit_ids) == 2, rows
    _write_ledger_rows(project, events_ledger.EVENTS_FILENAME, rows)
    briefs = _ledger_rows(project, ledgers.BRIEFS_FILENAME)
    for row in briefs:
        row.pop("session_id")
        row["schema_version"] = 24
    _write_ledger_rows(project, ledgers.BRIEFS_FILENAME, briefs)
    _bump_document_schema_version(project, record, 24)

    result = kyo("rebuild", "--force")
    assert result.code == 0, result

    store = open_store(project)
    try:
        loaded = {
            row["id"]: row
            for row in store.conn.execute(
                "SELECT id, at, before_json, changed_json FROM events"
            )
        }
        # Each old line loads into `before_json`, as it always did, and nothing is
        # invented for the column it never had.
        for event_id in edit_ids:
            assert loaded[event_id]["before_json"] is not None
            assert loaded[event_id]["changed_json"] is None
        # The reader keyed on content finds the description edit in the old format.
        description_edit = next(row for row in rows if row["id"] == edit_ids[0])
        assert store.description_edited_at(record) == description_edit["at"]
        # A brief run from before the column belongs to no session (the rebuild default).
        assert [r[0] for r in store.conn.execute("SELECT session_id FROM brief_runs")] == [None]
    finally:
        store.close()

    # And the next mutation appends a NEW-format line beside the old ones, rewriting none.
    before_bytes = _ledger_path(project, events_ledger.EVENTS_FILENAME).read_bytes()
    assert kyo("edit", record, "--title", "Retitled at 25").code == 0
    after_bytes = _ledger_path(project, events_ledger.EVENTS_FILENAME).read_bytes()
    assert after_bytes.startswith(before_bytes)
    newest = json.loads(after_bytes.decode("utf-8").splitlines()[-1])
    assert "before" not in newest
    assert newest[events_ledger.EVENT_CHANGED_KEY]["title"] == "Retitled at 24"
    assert kyo("doctor").code == 0


def test_every_prior_format_event_default_is_in_rebuild() -> None:
    """KYO-418.c1: the events ledger's two missing-key rules are declared, and rebuild
    applies them -- `changed` absent before 25, `before` absent from 25 on."""
    from kyosei import schema

    assert schema.EVENT_FIELD_DEFAULTS[25] == {"changed": None}
    assert schema.EVENT_RETIRED_FIELDS == {"before": None}
    assert schema.BRIEF_RUN_FIELD_DEFAULTS[25] == {"session_id": None}
    old = schema.apply_event_defaults({"id": "X-E1", "before": {"a": 1}, "after": {"a": 2}})
    new = schema.apply_event_defaults({"id": "X-E2", "changed": {"a": 1}, "after": {"a": 2}})
    assert old["changed"] is None and new["before"] is None
    source = (Path(__file__).parent.parent / "src" / "kyosei" / "rebuild.py").read_text()
    assert "apply_event_defaults(" in source and "apply_brief_run_defaults(" in source


# --------------------------------------------------------------------- KYO-498.c3


def test_rebuild_accepts_a_schema_25_export_with_no_since_note(kyo, project: Path) -> None:
    """`records.since_note` arrived at schema_version 26. A record exported under 25 has no
    `since_note` key, and that absence must rebuild as "no first-recorded note was named",
    so a carried record's age counts from its own `created_at` exactly as it did at 25.
    Never derived from the description's prose, for the reason KYO-100 refused to derive
    `scope`: it would manufacture a statement nobody made, store-wide, at once.
    """
    note = kyo("note", "--shift", "--file", "-", "--json", stdin="a carried item")
    assert note.code == 0, note
    carried = kyo("new", "reminder", "--title", "an old item", "--json").json["record"]["id"]
    task = new_record(kyo, title="widget", criteria=["a"])
    export_dir = project / "kyosei" / "export"
    for record in (carried, task):
        path = export_dir / f"{record}.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert "since_note" in doc, "this build writes the key"
        doc["schema_version"] = 25
        del doc["since_note"]
        path.write_text(json.dumps(doc), encoding="utf-8")

    (project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result

    for record in (carried, task):
        assert kyo("show", record, "--json").json["since_note"] is None
    shown = kyo("show", carried, "--json").json
    assert shown["carried"]["since"] == shown["created_at"]
    assert shown["title"] == "an old item"


# --------------------------------------------------------------------- KYO-642.c1


def _credited_record(kyo, project: Path, **credit) -> tuple[str, str]:
    """A record with one met verdict, its evidence, and one credit on both; returns the
    record id and the credit id. Written through the store's one credit writer, because
    the commands that call it are KYO-644's."""
    from kyosei.store import open_store

    record = new_record(kyo, criteria=["a"])
    result = kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "reasoned-about",
        "--evidence-file", "-", "--json", stdin="the person's words",
    )
    assert result.code == 0, result
    store = open_store(project)
    try:
        evidence = store.evidence_artifacts(record)[0]["id"]
        verification = store.verifications_for_criterion(f"{record}.c1")[0]["id"]
        with store.transaction():
            credit_id = store.add_credit(
                record,
                person="jbmv",
                role=credit.pop("role", "observed"),
                quote_id=evidence,
                source_kind="claude-code",
                source_session="7212f0bb-a568-49c4-9b87-d3209a201b6b",
                source_turn="turn-1",
                transcript_check="matched",
                subjects=[("verification", verification)],
                author="orchestrator",
                **credit,
            )
        from kyosei.export import write_export

        write_export(store, record)
    finally:
        store.close()
    return record, credit_id


def test_rebuild_accepts_a_schema_27_export_with_no_credits(kyo, project: Path) -> None:
    """`credits` and `gates.cleared_as` arrived at schema_version 28. A record exported
    under 27 has neither key, and that absence must rebuild as "no credit was ever
    written and no gate says how it was cleared" -- the empty list and NULL -- and never
    as a credit manufactured from the quoted words in the record's evidence."""
    record = new_record(kyo, criteria=["a"])
    kyo("gate", record, "holding-for", "--do", "the design lands")
    gate_ids = [g["id"] for g in kyo("show", record, "--json").json["gates"]]
    cleared = kyo("ungate", gate_ids[0], "--why", "landed")
    assert cleared.code == 0, cleared
    export_path = project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    assert doc["credits"] == [] and "cleared_as" in doc["gates"][0], "this build writes both"
    doc["schema_version"] = 27
    del doc["credits"]
    del doc["gates"][0]["cleared_as"]
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result

    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    try:
        assert conn.execute("SELECT count(*) FROM credits").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM credit_subjects").fetchone()[0] == 0
        assert conn.execute("SELECT DISTINCT cleared_as FROM gates").fetchall() == [(None,)]
    finally:
        conn.close()
    rebuilt = kyo("show", record, "--json").json
    assert rebuilt["credits"] == [] and rebuilt["gates"][0]["cleared_as"] is None


def test_rebuild_accepts_a_schema_29_export_with_no_person_notes(kyo, project: Path) -> None:
    """The notes ledger arrived at schema_version 30, as a new file. An export written at
    29 has no such file, and that must rebuild as no notes, with nothing refused."""
    from kyosei import ledgers as ledgers_module

    record = new_record(kyo, criteria=["a"])
    export_directory = project / "kyosei" / "export"
    assert not (export_directory / ledgers_module.PERSON_NOTES_FILENAME).exists()
    export_path = export_directory / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    doc["schema_version"] = 29
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result

    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    try:
        for table in (
            "person_notes",
            "person_note_anchors",
            "person_note_tags",
            "person_note_tag_changes",
        ):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
    finally:
        conn.close()
    assert kyo("notes", "list", "--json").json["notes"] == []
    assert kyo("show", record, "--json").json["id"] == record


def test_a_credit_round_trips_through_export_and_rebuild(kyo, project: Path) -> None:
    record, credit_id = _credited_record(kyo, project)
    assert credit_id == f"{record}.p1"
    export_path = project / "kyosei" / "export" / f"{record}.json"
    before = json.loads(export_path.read_text(encoding="utf-8"))
    assert before["credits"][0]["subjects"][0]["kind"] == "verification"

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    after = json.loads(export_path.read_text(encoding="utf-8"))
    assert after["credits"] == before["credits"]
    assert after["credits"][0]["person"] == "jbmv"


def test_rebuild_accepts_a_schema_28_export_whose_credits_have_no_withdrawal(
    kyo, project: Path
) -> None:
    """`withdrawn_at`, `withdrawn_by` and `withdrawn_why` arrived at schema_version 29
    (KYO-670). A credit exported under 28 has none of the keys, and that absence rebuilds
    as NULL: no credit could be withdrawn then, so the credit is still current."""
    record, credit_id = _credited_record(kyo, project)
    export_path = project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    assert "withdrawn_at" in doc["credits"][0], "this build writes the keys"
    doc["schema_version"] = 28
    for key in ("withdrawn_at", "withdrawn_by", "withdrawn_why"):
        del doc["credits"][0][key]
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    try:
        assert conn.execute(
            "SELECT withdrawn_at, withdrawn_by, withdrawn_why FROM credits WHERE id = ?",
            (credit_id,),
        ).fetchall() == [(None, None, None)]
    finally:
        conn.close()
    rebuilt = kyo("show", record, "--json").json
    assert rebuilt["criteria"][0]["verifications"][0]["credit"] == credit_id


def test_rebuild_accepts_a_schema_32_export_whose_credits_have_no_span_seal(
    kyo, project: Path
) -> None:
    """`span_seal` arrived at schema_version 33 (KYO-663). A credit exported under 32 has
    no such key, and that absence rebuilds as NULL: no index line was sealed then, and a
    seal is never computed at load time."""
    record, credit_id = _credited_record(kyo, project)
    export_path = project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    assert "span_seal" in doc["credits"][0], "this build writes the key"
    doc["schema_version"] = 32
    del doc["credits"][0]["span_seal"]
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    try:
        assert conn.execute(
            "SELECT span_seal FROM credits WHERE id = ?", (credit_id,)
        ).fetchall() == [(None,)]
    finally:
        conn.close()
    rebuilt = kyo("show", record, "--json").json
    assert rebuilt["criteria"][0]["verifications"][0]["credit"] == credit_id


def test_a_span_seal_round_trips_through_export_and_rebuild(kyo, project: Path) -> None:
    """KYO-663: the seal is the credit's own stored value, so a rebuild restores it from
    the export and does not need the turn index."""
    record, credit_id = _credited_record(
        kyo, project, span_session="s", span_first_turn="a", span_last_turn="b",
        span_seal="ab" * 32,
    )  # fmt: skip
    exported = json.loads(
        (project / "kyosei" / "export" / f"{record}.json").read_text(encoding="utf-8")
    )
    assert exported["credits"][0]["span_seal"] == "ab" * 32
    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    try:
        assert conn.execute(
            "SELECT span_seal FROM credits WHERE id = ?", (credit_id,)
        ).fetchall() == [("ab" * 32,)]
    finally:
        conn.close()


# --------------------------------------------------------------------- KYO-642.c4


def test_a_refined_credit_stores_the_refining_turn_beside_the_accepting_one(
    kyo, project: Path
) -> None:
    record, _ = _credited_record(
        kyo, project, role="decided", recommended="adopt A", recommended_by="orchestrator",
        outcome="refined", alternatives=[{"text": "A", "chosen": True}],
        refining_session="aaaaaaaa-0000-0000-0000-000000000000", refining_turn="turn-2",
    )
    export_path = project / "kyosei" / "export" / f"{record}.json"
    credit = json.loads(export_path.read_text(encoding="utf-8"))["credits"][0]
    assert credit["outcome"] == "refined"
    assert (credit["source_session"], credit["source_turn"]) == (
        "7212f0bb-a568-49c4-9b87-d3209a201b6b", "turn-1",
    )
    assert (credit["refining_session"], credit["refining_turn"]) == (
        "aaaaaaaa-0000-0000-0000-000000000000", "turn-2",
    )
    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert json.loads(export_path.read_text(encoding="utf-8"))["credits"][0] == credit


@pytest.mark.parametrize("outcome", ["accepted", "overrode", "unprompted"])
def test_every_other_outcome_is_accepted_and_carries_no_refining_turn(
    kyo, project: Path, outcome: str
) -> None:
    record, _ = _credited_record(kyo, project, role="decided", outcome=outcome)
    export_path = project / "kyosei" / "export" / f"{record}.json"
    credit = json.loads(export_path.read_text(encoding="utf-8"))["credits"][0]
    assert credit["outcome"] == outcome and credit["refining_turn"] is None


def test_a_refined_credit_without_its_refining_turn_is_refused(kyo, project: Path) -> None:
    from kyosei.errors import KyoseiError

    with pytest.raises(KyoseiError, match="refining_session and refining_turn"):
        _credited_record(kyo, project, role="decided", outcome="refined")
    with pytest.raises(KyoseiError, match="not one of accepted, refined, overrode, unprompted"):
        _credited_record(kyo, project, role="decided", outcome="changed")
    with pytest.raises(KyoseiError, match="belong to outcome 'refined'"):
        _credited_record(
            kyo, project, role="decided", outcome="accepted",
            refining_session="s", refining_turn="t",
        )


# --------------------------------------------------------------------- KYO-642.c2


def test_the_people_key_is_declared_read_and_refused_when_malformed(
    kyo, project: Path
) -> None:
    from kyosei.config import declared_people, person_label, read_raw_config
    from kyosei.errors import KyoseiError

    config_path = project / "kyosei" / "config.yml"
    original = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        original + "people:\n  jbmv: {}\n  ada:\n    name: Ada L\n", encoding="utf-8"
    )
    config = load_config(project)
    assert declared_people(config) == {"jbmv": None, "ada": "Ada L"}
    assert person_label(config, "jbmv") == "jbmv"
    assert person_label(config, "ada") == "Ada L"
    assert person_label(config, "gone") == "gone"
    assert "people" in read_raw_config(project)

    config_path.write_text(original + "people:\n  jbmv:\n    names: x\n", encoding="utf-8")
    with pytest.raises(KyoseiError, match="only key an entry reads is name"):
        load_config(project)
    config_path.write_text(original + "people: [jbmv]\n", encoding="utf-8")
    with pytest.raises(KyoseiError, match="must be a mapping"):
        load_config(project)
    config_path.write_text(original, encoding="utf-8")
    assert declared_people(load_config(project)) == {}


def test_a_rebuild_carries_the_people_key_forward(kyo, project: Path) -> None:
    config_path = project / "kyosei" / "config.yml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "people:\n  jbmv: {}\n", encoding="utf-8"
    )
    new_record(kyo, criteria=["a"])
    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert "jbmv" in config_path.read_text(encoding="utf-8")
    assert kyo("doctor").code in (0, 1)


def test_every_new_column_has_a_consumer_and_the_doctor_check_passes(kyo, project: Path) -> None:
    from kyosei.doctor_consumers import check_column_consumers, check_config_consumers
    from kyosei.schema import COLUMN_CONSUMERS, CONFIG_CONSUMERS
    from kyosei.store import open_store

    # KYO-670 adds the three withdrawn_*, KYO-663 span_seal.
    assert len(COLUMN_CONSUMERS["credits"]) == 36
    assert len(COLUMN_CONSUMERS["credit_subjects"]) == 3
    assert len(COLUMN_CONSUMERS["turn_refs"]) == 17  # KYO-658, and KYO-882's outcome
    assert COLUMN_CONSUMERS["gates"]["cleared_as"]
    assert CONFIG_CONSUMERS["people"]
    store = open_store(project)
    try:
        assert check_column_consumers(store) == []
        assert check_config_consumers(store) == []
    finally:
        store.close()


# --------------------------------------------------------------------- KYO-642.c3


def test_agent_observed_is_a_method_kyo_verify_and_a_worker_report_accept(
    kyo, project: Path
) -> None:
    from kyosei.report_schema import WORKER_REPORT_METHODS

    assert "agent-observed" in WORKER_REPORT_METHODS
    record = new_record(kyo, criteria=["a"])
    verified = kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "agent-observed",
        "--evidence-file", "-", "--json", stdin="read the live store myself",
    )
    assert verified.code == 0, verified
    shown = kyo("show", record, "--json").json
    assert "agent-observed" in json.dumps(shown)


# --------------------------------------------------------------------- KYO-642.c5

_SPAN = {
    "span_session": "7212f0bb-a568-49c4-9b87-d3209a201b6b",
    "span_first_turn": "u-1",
    "span_last_turn": "u-9",
    "span_person_turns": 4,
    "span_agent_turns": 5,
    "span_active_seconds": 620,
    "span_started_at": "2026-11-06T10:00:00.000Z",
    "span_ended_at": "2026-11-06T11:30:00.000Z",
}


def test_a_credit_stores_the_form_of_its_words_and_a_discussion_span(
    kyo, project: Path
) -> None:
    record, _ = _credited_record(
        kyo, project, role="decided", outcome="accepted",
        words_form="accepted-suggestion", **_SPAN,
    )
    export_path = project / "kyosei" / "export" / f"{record}.json"
    credit = json.loads(export_path.read_text(encoding="utf-8"))["credits"][0]
    assert credit["words_form"] == "accepted-suggestion"
    assert {k: credit[k] for k in _SPAN} == _SPAN
    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    # KYO-647: `kyo show --json` adds the derived `reconstructed` and `person_name`
    # beside the stored fields; the export holds only the latter.
    shown = kyo("show", record, "--json").json["credits"][0]
    assert {k: v for k, v in shown.items() if k not in ("reconstructed", "person_name")} == credit


@pytest.mark.parametrize("form", ["typed", "mid-turn", "choice", "accepted-suggestion"])
def test_every_form_of_words_is_accepted_and_a_span_is_optional(
    kyo, project: Path, form: str
) -> None:
    record, _ = _credited_record(kyo, project, words_form=form)
    credit = kyo("show", record, "--json").json["credits"][0]
    assert credit["words_form"] == form and credit["span_session"] is None


def test_an_unknown_form_or_a_partial_span_is_refused(kyo, project: Path) -> None:
    from kyosei.errors import KyoseiError

    with pytest.raises(KyoseiError, match="not one of typed, mid-turn, choice"):
        _credited_record(kyo, project, words_form="pasted")
    with pytest.raises(KyoseiError, match="whole or not at all"):
        _credited_record(kyo, project, span_session="s")


# --------------------------------------------------------------------- KYO-709.c7


def _as_schema_30(project: Path) -> None:
    """Make the notes ledgers and the db read as a store written at schema_version 30."""
    from kyosei import ledgers as ledgers_module

    export_directory = project / "kyosei" / "export"
    for name in (
        ledgers_module.PERSON_NOTES_FILENAME,
        ledgers_module.PERSON_NOTE_TAG_CHANGES_FILENAME,
    ):
        path = export_directory / name
        if not path.exists():
            continue
        lines = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(raw)
            row.pop("sent", None)  # schema 30 had no such key
            row["schema_version"] = 30
            lines.append(json.dumps(row, sort_keys=True) + "\n")
        path.write_text("".join(lines), encoding="utf-8")
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    try:
        conn.execute("UPDATE meta SET value = '30' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()


def test_a_schema_30_store_with_sent_tags_opens_with_the_field_and_without_the_tag(
    kyo, project: Path
) -> None:
    """The `sent` tag was the sent state until 31. Tagged later, the field takes the time of
    the tag's change; filed with it, the note's filing. Either way the tag is gone, and a
    note whose tag was removed again was not sent."""
    later = kyo("notes", "add", "--entered", "typed", "--file", "-", "--json", stdin="a\n")
    filed = kyo(
        "notes", "add", "--entered", "typed", "--tag", "sent", "--tag", "keep", "--file", "-",
        "--json", stdin="b\n",
    )
    removed = kyo("notes", "add", "--entered", "typed", "--file", "-", "--json", stdin="c\n")
    later_id, filed_id, removed_id = (r.json["note"]["id"] for r in (later, filed, removed))
    assert kyo("notes", "tag", later_id, "--add", "sent").code == 0
    assert kyo("notes", "tag", removed_id, "--add", "sent").code == 0
    assert kyo("notes", "tag", removed_id, "--remove", "sent").code == 0
    tagged_at = kyo("notes", "show", later_id, "--json").json["note"]["tag_changes"][0]
    filed_at = filed.json["note"]["created_at"]
    _as_schema_30(project)

    refused = kyo("notes", "show", later_id)
    assert refused.code != 0 and "kyo rebuild --force" in refused.err
    rebuilt = kyo("rebuild", "--force")
    assert rebuilt.code == 0, rebuilt

    later_note = kyo("notes", "show", later_id, "--json").json["note"]
    assert later_note["sent"] == {"at": tagged_at["changed_at"], "session": None, "message": None}
    assert later_note["tags"] == []
    # The change itself stays in the note's history.
    assert [c["tag"] for c in later_note["tag_changes"]] == ["sent"]
    filed_note = kyo("notes", "show", filed_id, "--json").json["note"]
    assert filed_note["sent"] == {"at": filed_at, "session": None, "message": None}
    assert filed_note["tags"] == ["keep"]
    removed_note = kyo("notes", "show", removed_id, "--json").json["note"]
    assert removed_note["sent"] is None and removed_note["tags"] == []
    sent_ids = [n["id"] for n in kyo("notes", "list", "--sent", "--json").json["notes"]]
    assert sent_ids == [later_id, filed_id]

    # A `sent` tag written at 31 is a tag, and moves no field.
    assert kyo("notes", "tag", removed_id, "--add", "sent").code == 0
    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    again = kyo("notes", "show", removed_id, "--json").json["note"]
    assert again["sent"] is None and again["tags"] == ["sent"]
    assert kyo("notes", "show", later_id, "--json").json["note"]["tags"] == []
    doctor = kyo("doctor")
    assert doctor.code == 0, doctor


def test_a_schema_30_note_line_rebuilds_with_no_sent_state(kyo, project: Path) -> None:
    from kyosei.schema import PERSON_NOTE_FIELD_DEFAULTS, SENT_FIELD_SCHEMA_VERSION

    assert PERSON_NOTE_FIELD_DEFAULTS[SENT_FIELD_SCHEMA_VERSION] == {"sent": None}
    note = kyo("notes", "add", "--entered", "typed", "--file", "-", "--json", stdin="a\n")
    _as_schema_30(project)
    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    shown = kyo("notes", "show", note.json["note"]["id"], "--json").json["note"]
    assert shown["sent"] is None and shown["text"] == "a"


# --------------------------------------------------------------------- KYO-658.c5


def test_this_change_bumps_the_schema_by_one_from_its_base() -> None:
    """KYO-658 was built on schema 31 (KYO-709) and bumped it by one, to 32; KYO-663 was
    built on 32 and bumps it by one, to 33; KYO-661 was built on 33 and bumps it to 34;
    KYO-882 was built on 34 and bumps it to 35."""
    from kyosei.schema import (
        CREDIT_FIELD_DEFAULTS,
        EXPORT_FIELD_DEFAULTS,
        TURN_REF_FIELD_DEFAULTS,
    )

    assert SCHEMA_VERSION == 35
    assert TURN_REF_FIELD_DEFAULTS[35] == {"outcome": None}
    assert EXPORT_FIELD_DEFAULTS[32] == {"turn_refs": []}
    assert CREDIT_FIELD_DEFAULTS[33] == {"span_seal": None}
    from kyosei.schema import DISPATCH_FIELD_DEFAULTS

    assert DISPATCH_FIELD_DEFAULTS[34] == {"answered_prompt": None}


def _a_discussion(kyo, project: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A discussion opened with no turn index, then closed with nothing produced."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "7c1d2e3f-1111-4a2b-9c3d-0123456789ab")
    opened = kyo("discuss", "open", "--title", "Whether to keep the cache", "--json")
    assert opened.code == 0, opened
    record = opened.json["id"]
    closed = kyo("discuss", "close", record, "--dropped", "--why", "decided elsewhere")
    assert closed.code == 0, closed
    return record


def test_rebuild_accepts_a_schema_31_export_with_no_turn_references(
    kyo, project: Path
) -> None:
    """`turn_refs` arrived at schema_version 32. A record exported under 31 has no such key,
    and that absence must rebuild as "no discussion was ever opened on it", the empty list,
    never as a turn reference derived from a credit's span."""
    record = new_record(kyo, criteria=["a"])
    export_path = project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    assert doc["turn_refs"] == [], "this build writes the key"
    doc["schema_version"] = 31
    del doc["turn_refs"]
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    try:
        assert conn.execute("SELECT count(*) FROM turn_refs").fetchone()[0] == 0
        version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        assert version == (str(SCHEMA_VERSION),)
    finally:
        conn.close()
    assert kyo("show", record, "--json").json["turn_refs"] == []


def test_a_schema_31_db_is_refused_and_rebuilt_at_32(
    kyo, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store whose db says 31 is refused with the rebuild remedy, and `kyo rebuild --force`
    brings it to 32 with its discussions' turn references intact."""
    record = _a_discussion(kyo, project, monkeypatch)
    before = kyo("show", record, "--json").json["turn_refs"]
    assert [r["role"] for r in before] == ["opened", "closed"]
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    try:
        conn.execute("UPDATE meta SET value = '31' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()
    refused = kyo("show", record)
    assert refused.code != 0 and "kyo rebuild --force" in refused.err
    assert kyo("rebuild", "--force").code == 0
    assert kyo("show", record, "--json").json["turn_refs"] == before


def test_a_discussion_round_trips_through_export_and_rebuild(
    kyo, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _a_discussion(kyo, project, monkeypatch)
    export_path = project / "kyosei" / "export" / f"{record}.json"
    before = json.loads(export_path.read_text(encoding="utf-8"))
    assert before["turn_refs"][0]["id"] == f"{record}.s1"
    assert before["turn_refs"][1]["why"] == "decided elsewhere"
    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    after = json.loads(export_path.read_text(encoding="utf-8"))
    assert after["turn_refs"] == before["turn_refs"]
    # The counter is seeded above the loaded references, so the next one is s3.
    noted = kyo("discuss", "note", record, "--file", "-", "--json", stdin="A or B.\n")
    assert noted.code == 0, noted
    assert noted.json["turn_ref"] == f"{record}.s3"
    assert kyo("doctor").code in (0, 1)


# ------------------------------------------------------------------------- KYO-882


def _as_schema_34(project: Path, record: str, *, closing_why: str | None = None) -> None:
    """Rewrite ``record``'s export document as schema_version 34 wrote it: no ``outcome``
    on any turn reference, and the closing's ``why`` as given when one is."""
    export_path = project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    doc["schema_version"] = 34
    for ref in doc["turn_refs"]:
        assert "outcome" in ref, "this build writes the key"
        del ref["outcome"]
        if ref["role"] == "closed" and closing_why is not None:
            ref["why"] = closing_why
    export_path.write_text(json.dumps(doc), encoding="utf-8")


def _rebuilt_refs(kyo, project: Path, record: str) -> list[dict]:
    (project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result
    return kyo("show", record, "--json").json["turn_refs"]


def test_a_schema_34_closing_whose_reason_has_the_prefix_rebuilds_as_produced_without_it(
    kyo, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At 34 `kyo discuss close --produced --why` with no record stored its reason as
    `produced: <reason>` (KYO-878). The rebuild reads that as produced, takes the prefix
    off, and `kyo export --refresh` then writes the field and the bare reason."""
    record = _a_discussion(kyo, project, monkeypatch)
    _as_schema_34(project, record, closing_why="produced: ideas/idea-file.md")

    opened, closed = _rebuilt_refs(kyo, project, record)

    assert opened["outcome"] is None
    assert closed["outcome"] == "produced" and closed["why"] == "ideas/idea-file.md"
    assert closed["produced_id"] is None
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    try:
        rows = conn.execute("SELECT role, outcome, why FROM turn_refs ORDER BY seq").fetchall()
    finally:
        conn.close()
    assert rows == [("opened", None, None), ("closed", "produced", "ideas/idea-file.md")]
    shown = kyo("show", record).out
    assert "closed, produced ideas/idea-file.md" in shown
    assert "produced: " not in shown
    assert kyo("export", "--refresh").code == 0
    written = json.loads(
        (project / "kyosei" / "export" / f"{record}.json").read_text(encoding="utf-8")
    )
    assert written["schema_version"] == SCHEMA_VERSION
    assert written["turn_refs"][1]["outcome"] == "produced"
    assert written["turn_refs"][1]["why"] == "ideas/idea-file.md"


def test_a_schema_34_closing_with_a_produced_record_rebuilds_as_produced(
    kyo, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A closing that names a record, by `kyo spawn` or by `close --produced <record>`, was
    produced, and its reason is kept whole: the prefix was written only where no record
    was named."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "7c1d2e3f-1111-4a2b-9c3d-0123456789ab")
    task = new_record(kyo, kind="task", title="Built", criteria=["it works"])
    record = kyo("discuss", "open", "--title", "Whether to build it", "--json").json["id"]
    closed = kyo("discuss", "close", record, "--produced", task, "--why", "and a file")
    assert closed.code == 0, closed
    _as_schema_34(project, record)

    refs = _rebuilt_refs(kyo, project, record)

    assert refs[-1]["outcome"] == "produced"
    assert refs[-1]["produced_id"] == task and refs[-1]["why"] == "and a file"
    assert f"closed, produced {task}" in kyo("show", record).out


def test_any_other_schema_34_closing_rebuilds_as_dropped(
    kyo, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _a_discussion(kyo, project, monkeypatch)
    _as_schema_34(project, record)

    refs = _rebuilt_refs(kyo, project, record)

    assert refs[-1]["outcome"] == "dropped" and refs[-1]["why"] == "decided elsewhere"
    assert "closed, nothing produced" in kyo("show", record).out


def test_the_prefix_is_read_from_the_prior_format_alone(
    kyo, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A document this build wrote states its outcome, so a dropped closing whose reason
    begins `produced: ` rebuilds as it was stored: the migration does not run on it."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "7c1d2e3f-1111-4a2b-9c3d-0123456789ab")
    record = kyo("discuss", "open", "--title", "x", "--json").json["id"]
    closed = kyo("discuss", "close", record, "--dropped", "--why", "produced: nothing")
    assert closed.code == 0, closed

    refs = _rebuilt_refs(kyo, project, record)

    assert refs[-1]["outcome"] == "dropped" and refs[-1]["why"] == "produced: nothing"


def test_a_closing_reference_cannot_be_stored_without_its_outcome(
    kyo, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The table refuses a `closed` row with no outcome and any other row with one, so the
    field cannot be left for a reader to guess."""
    _a_discussion(kyo, project, monkeypatch)
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE turn_refs SET outcome = NULL WHERE role = 'closed'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE turn_refs SET outcome = 'dropped' WHERE role = 'opened'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE turn_refs SET outcome = 'unsure' WHERE role = 'closed'")
    finally:
        conn.close()
