"""KYO-418: the events ledger stops recording what did not change.

The git-canonical ledger grew about 3.8 MB a day, and most of
those bytes recorded nothing new: every mutation wrote the whole record document twice,
every non-worker SubagentStop wrote a stop-audit row, every Stop hook's `kyo sweep`
logged a run at an unchanged base, and a touch-changed widening logged two whole
documents to record one changed string. These tests pin each of the fixes, and each
one fails if its mechanism is removed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from kyosei import changelog, events_ledger, export
from kyosei.contracts import REDACTION_MARKER
from kyosei.stop_audit import UNRESOLVED_COUNTER_KEY
from kyosei.stop_target import STOP_TARGET_MATCHED, STOP_TARGET_UNRESOLVED
from kyosei.store import open_store

from .conftest import event_lines, export_of, git_init, new_record
from .test_kyo8_sweep import _commit_change, _promote_touching
from .test_kyo398_build_change import NEW, OLD, running  # noqa: F401 - a fixture

SECRET = "customer-id-4471-9902-alpha"


def _ledger_files(project: Path) -> list[Path]:
    return events_ledger.events_files_in(project / "kyosei" / "export")


def _ledger_text(project: Path) -> str:
    return "".join(path.read_text(encoding="utf-8") for path in _ledger_files(project))


def _ledger_bytes(project: Path) -> int:
    return sum(path.stat().st_size for path in _ledger_files(project))


# ------------------------------------------------------------------- c2: the diff shape


def test_a_record_mutation_writes_after_and_changed_and_no_before(kyo, project: Path) -> None:
    """KYO-418.c2. Fails if `record_event` goes back to writing a whole `before`."""
    record = new_record(kyo, title="First title")
    assert kyo("edit", record, "--title", "Second title").code == 0
    line = event_lines(project)[-1]
    assert line["command"] == f"edit {record}"
    assert "before" not in line, "a record mutation wrote a whole before-document again"
    changed = line[events_ledger.EVENT_CHANGED_KEY]
    assert changed["title"] == "First title"
    assert line["after"]["title"] == "Second title"
    # Only keys that moved: the description, criteria and every list are absent.
    assert set(changed) <= {"title", "updated_at"}, changed
    # And the database row matches the line: a diff, not a whole prior document.
    store = open_store(project)
    try:
        row = store.conn.execute(
            "SELECT before_json, changed_json FROM events WHERE id = ?", (line["id"],)
        ).fetchone()
    finally:
        store.close()
    assert row["before_json"] is None
    assert json.loads(row["changed_json"])["title"] == "First title"


def test_a_creation_carries_neither_prior_form(kyo, project: Path) -> None:
    record = new_record(kyo, title="Brand new")
    line = [e for e in event_lines(project) if e["record_id"] == record][0]
    assert "before" not in line and events_ledger.EVENT_CHANGED_KEY not in line
    assert events_ledger.event_prior(line) is None


def test_the_diff_is_what_the_old_before_reduced_to() -> None:
    """The two formats answer `event_prior` identically for one mutation."""
    before = {"id": "X-1", "title": "a", "notes": [], "gone": 1, "kept": None}
    after = {"id": "X-1", "title": "b", "notes": [{"id": "X-1.n1"}], "new": 2}
    old_line = {"before": before, "after": after}
    new_line = {
        "after": after,
        events_ledger.EVENT_CHANGED_KEY: events_ledger.event_diff(before, after),
    }
    assert events_ledger.event_prior(old_line) == events_ledger.event_prior(new_line) == {
        "gone": 1,
        "new": None,
        "notes": [],
        "title": "a",
    }
    rebuilt = events_ledger.event_before_document(new_line)
    assert {k: rebuilt.get(k) for k in set(before) | set(after)} == {
        k: before.get(k) for k in set(before) | set(after)
    }


# ---------------------------------------------------- c4: no body rides back via changed


def test_no_body_reaches_the_ledger_through_changed(kyo, project: Path) -> None:
    """KYO-418.c4. An evidence `text`, a dispatch `brief` and a report `body` in the
    PRIOR document are exactly what `changed` would carry if it skipped the stripping."""
    record = new_record(kyo, title="Bodies")
    store = open_store(project)
    try:
        before = {
            "id": record,
            "evidence_artifacts": [{"id": f"{record}.e1", "text": SECRET + "-evidence"}],
            "dispatches": [{"id": f"{record}.d1", "state": "running", "brief": SECRET + "-brief"}],
            "reports": [{"id": f"{record}.r1", "body": {"summary": SECRET + "-report"}}],
        }
        after = {
            "id": record,
            "evidence_artifacts": [],
            "dispatches": [{"id": f"{record}.d1", "state": "reported", "brief": "x"}],
            "reports": [],
        }
        with store.transaction():
            event = store.record_event(
                actor="test", command="bodies", record_id=record, before=before, after=after
            )
        events_ledger.append_events(store, [event])
        row = store.conn.execute(
            "SELECT changed_json FROM events WHERE id = ?", (event["id"],)
        ).fetchone()
    finally:
        store.close()
    assert SECRET not in json.dumps(event)
    assert SECRET not in row["changed_json"]
    assert SECRET not in _ledger_text(project)
    changed = event[events_ledger.EVENT_CHANGED_KEY]
    assert set(changed["evidence_artifacts"][0]) == {"id", "sha256"}
    assert "brief" not in changed["dispatches"][0] and changed["dispatches"][0]["brief_sha256"]
    assert "body" not in changed["reports"][0] and changed["reports"][0]["body_sha256"]


def test_existing_ledger_lines_are_never_rewritten(kyo, project: Path) -> None:
    """KYO-418.c4, second half: the new format is written going forward only."""
    record = new_record(kyo, title="Append only")
    first = _ledger_text(project)
    assert kyo("edit", record, "--title", "Still append only").code == 0
    assert _ledger_text(project).startswith(first)


# --------------------------------------------- c3: both formats, one ledger, same answers


def _to_old_format(line: dict) -> dict:
    """A schema-24 line for the same mutation: a whole `before`, no `changed`."""
    old = {k: v for k, v in line.items() if k != events_ledger.EVENT_CHANGED_KEY}
    old["before"] = events_ledger.event_before_document(line)
    old[export.LEDGER_VERSION_KEY] = 24
    return old


def _rewrite_ledger(project: Path, convert) -> None:
    """Rewrite every shard with ``convert(index, line)`` — a fixture's forgery of what a
    ledger written by an older build looks like, never something the tool does."""
    index = 0
    for path in _ledger_files(project):
        out = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            out.append(json.dumps(convert(index, json.loads(raw)), sort_keys=True))
            index += 1
        path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _history(kyo, project: Path, write_file) -> tuple[str, str]:
    """One record whose description was edited, then other fields; moved to done; and
    a claim that a later edit carries in its prior value."""
    record = new_record(kyo, title="History", criteria=["it works"])
    assert kyo(
        "claim", record, "--level", "asserted", "--method", "code read",
        "--file", write_file("claim.md", f"the value is {SECRET}"),
    ).code == 0
    assert kyo(
        "edit", record, "--description-file", write_file("d.md", "The first description.")
    ).code == 0
    assert kyo(
        "edit", record, "--description-file", write_file("d2.md", "The second description.")
    ).code == 0
    assert kyo(
        "claim", record, "--level", "asserted", "--method", "code read",
        "--file", write_file("claim2.md", "a second claim"),
    ).code == 0
    assert kyo("edit", record, "--title", "History, retitled").code == 0
    assert kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "reasoned-about").code == 0
    assert kyo("move", record, "done").code == 0
    claim_id = kyo("show", record, "--json").json["claims"][0]["id"]
    return record, claim_id


def _answers(kyo, project: Path, record: str) -> dict:
    """What each reader of a prior value says, after a rebuild from the ledger as it is."""
    assert kyo("rebuild", "--force").code == 0
    store = open_store(project)
    try:
        edited = store.description_edited_at(record)
        rows = store.conn.execute(
            "SELECT id, before_json, after_json, changed_json FROM events ORDER BY seq"
        ).fetchall()
        priors = {
            row["id"]: events_ledger.event_prior(
                events_ledger.event_from_columns(
                    row["before_json"], row["after_json"], row["changed_json"]
                )
            )
            for row in rows
        }
    finally:
        store.close()
    return {
        "description_edited_at": edited,
        "completion": changelog._completion_dates(project / "kyosei" / "export", "done"),
        "priors": priors,
    }


@pytest.mark.parametrize("mix", ["all-old", "mixed"])
def test_every_reader_answers_the_same_on_either_format_and_on_a_mix(
    kyo, project: Path, write_file, mix: str
) -> None:
    """KYO-418.c3: `description_edited_at`, `changelog._completion_dates` and rebuild's
    `before_json`/`changed_json` load give the same answers whether the ledger holds
    new lines, old lines, or both interleaved."""
    record, _claim = _history(kyo, project, write_file)
    new_answers = _answers(kyo, project, record)
    # Sanity: the answers are real answers, not the fallbacks.
    assert new_answers["completion"].get(record)
    assert new_answers["description_edited_at"] != kyo("show", record, "--json").json["created_at"]

    _rewrite_ledger(
        project,
        lambda i, line: _to_old_format(line) if mix == "all-old" or i % 2 == 0 else line,
    )
    text = _ledger_text(project)
    assert '"before": {' in text
    if mix == "mixed":
        assert f'"{events_ledger.EVENT_CHANGED_KEY}": {{' in text
    assert _answers(kyo, project, record) == new_answers


@pytest.mark.parametrize("mix", ["all-new", "all-old", "mixed"])
def test_the_redact_scrub_clears_both_formats(
    kyo, project: Path, write_file, mix: str
) -> None:
    """KYO-418.c3/c4: the id-keyed scrub reaches a claim's statement in an old line's
    `before`, a new line's `changed` and either line's `after`."""
    record, claim_id = _history(kyo, project, write_file)
    if mix != "all-new":
        _rewrite_ledger(
            project,
            lambda i, line: _to_old_format(line) if mix == "all-old" or i % 2 else line,
        )
        assert kyo("rebuild", "--force").code == 0
    assert SECRET in _ledger_text(project), "precondition: the ledger quotes the claim"
    result = kyo("redact", claim_id, "--why", "quoted a customer id", "--json")
    assert result.code == 0, result
    assert SECRET not in _ledger_text(project)
    store = open_store(project)
    try:
        rows = store.conn.execute(
            "SELECT before_json, after_json, changed_json FROM events"
        ).fetchall()
    finally:
        store.close()
    assert not any(SECRET in (value or "") for row in rows for value in row)
    assert REDACTION_MARKER in _ledger_text(project)


def test_a_field_redaction_reaches_a_prior_description_in_changed(
    kyo, project: Path, write_file
) -> None:
    """KYO-426's field scrub keys on the record id, which a `changed` map does not carry
    unless the id moved; it belongs to the record its `after` names."""
    record = new_record(kyo, title="Field")
    assert kyo(
        "edit", record, "--description-file", write_file("d.md", f"quoted {SECRET} here")
    ).code == 0
    assert kyo(
        "edit", record, "--description-file", write_file("d2.md", "clean now")
    ).code == 0
    last = event_lines(project)[-1]
    assert SECRET in last[events_ledger.EVENT_CHANGED_KEY]["description"], "precondition"
    result = kyo("redact", f"{record}.description", "--why", "quoted an id", "--json")
    assert result.code == 0, result
    assert result.json["complete"] is True, result.json
    assert SECRET not in _ledger_text(project)


# -------------------------------------------------- c5: a widening is a flag-level event


def test_a_widening_logs_a_flag_event_and_leaves_updated_at_alone(
    kyo, project: Path, write_file
) -> None:
    """KYO-418.c5. Fails if a widening writes a record snapshot or moves `updated_at`."""
    for name in ("alpha.py", "beta.py"):
        (project / name).write_text("x = 1\n", encoding="utf-8")
    record = _promote_touching(kyo, project, write_file, paths=["alpha.py", "beta.py"], name="w")
    git_init(project, commit_message="baseline")
    kyo("sweep")
    _commit_change(project, "alpha.py", "x = 2\n")
    first = kyo("sweep", "--json")
    flag_id = first.json["flags"][0]["flag_id"]
    updated_at = kyo("show", record, "--json").json["updated_at"]
    lines_before = len(event_lines(project))

    _commit_change(project, "beta.py", "x = 2\n")
    second = kyo("sweep", "--json")
    assert [w["flag_id"] for w in second.json["widened_flags"]] == [flag_id]
    shown = kyo("show", record, "--json").json
    assert shown["updated_at"] == updated_at, "a widening moved the record's updated_at"
    assert "beta.py" in next(f for f in shown["flags"] if f["id"] == flag_id)["detail"]

    new = event_lines(project)[lines_before:]
    about_record = [e for e in new if e["record_id"] == record]
    assert len(about_record) == 1, about_record
    widening = about_record[0]
    assert widening["after"]["flag_id"] == flag_id
    assert "beta.py" in widening["after"]["detail"]
    assert "beta.py" not in widening[events_ledger.EVENT_CHANGED_KEY]["detail"]
    assert set(widening[events_ledger.EVENT_CHANGED_KEY]) == {"detail"}
    assert "title" not in widening["after"], "a widening wrote a record snapshot"
    # The export still carries the widened detail -- it is what a rebuild reads.
    exported = json.loads(export_of(project, record).read_text(encoding="utf-8"))
    assert "beta.py" in next(f for f in exported["flags"] if f["id"] == flag_id)["detail"]


# ------------------------------------------ c9: one open flag per class that re-derives


def _open_flags(kyo, record: str, flag_type: str) -> list[dict]:
    return [
        f
        for f in kyo("show", record, "--json").json["flags"]
        if f["resolved_at"] is None and f["flag_type"] == flag_type
    ]


def test_a_wider_missing_set_widens_the_standing_touch_missing_flag(
    kyo, project: Path, write_file
) -> None:
    """KYO-418.c9, touch-missing. Fails if a second open flag is filed."""
    for name in ("alpha.py", "beta.py"):
        (project / name).write_text("x = 1\n", encoding="utf-8")
    record = _promote_touching(kyo, project, write_file, paths=["alpha.py", "beta.py"], name="m")
    (project / "alpha.py").unlink()
    first = kyo("sweep", "--json")
    assert [f["flag_type"] for f in first.json["flags"]] == ["touch-missing"]
    flag_id = first.json["flags"][0]["flag_id"]

    (project / "beta.py").unlink()
    second = kyo("sweep", "--json")
    assert second.json["flags"] == []
    assert [w["flag_id"] for w in second.json["widened_flags"]] == [flag_id]
    standing = _open_flags(kyo, record, "touch-missing")
    assert len(standing) == 1, standing
    assert "alpha.py" in standing[0]["detail"] and "beta.py" in standing[0]["detail"]


def test_a_newer_claim_supersedes_the_standing_description_flag(
    kyo, project: Path, write_file
) -> None:
    """KYO-418.c9, description-predates-claim. Fails if a second open flag is filed."""
    (project / "alpha.py").write_text("x = 1\n", encoding="utf-8")
    record = _promote_touching(kyo, project, write_file, paths=["alpha.py"], name="d")

    def claim(text: str) -> str:
        result = kyo(
            "claim", record, "--level", "asserted", "--method", "code read",
            "--file", write_file("c.md", text), "--json",
        )
        assert result.code == 0, result
        return kyo("show", record, "--json").json["claims"][-1]["id"]

    first_claim = claim("the first finding")
    first = kyo("sweep", "--json")
    flags = [f for f in first.json["flags"] if f["flag_type"] == "description-predates-claim"]
    assert len(flags) == 1 and first_claim in flags[0]["detail"]

    second_claim = claim("a newer finding")
    second = kyo("sweep", "--json")
    assert not [f for f in second.json["flags"] if f["flag_type"] == "description-predates-claim"]
    standing = _open_flags(kyo, record, "description-predates-claim")
    assert len(standing) == 1, standing
    assert second_claim in standing[0]["detail"]
    assert standing[0]["id"] == flags[0]["flag_id"]


# --------------------------------------------------- c8: a no-op sweep writes nothing


def _porcelain(project: Path, path: str) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain", path],
        cwd=project, capture_output=True, text=True, check=True,
    ).stdout


def _commit_all(project: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", message], cwd=project, check=True, capture_output=True
    )


def test_a_sweep_at_an_unchanged_base_with_nothing_new_writes_nothing(
    kyo, project: Path
) -> None:
    """KYO-418.c8. Fails if a no-op sweep appends to `_sweeps.jsonl`, the events
    ledger or `_allocations.json`."""
    git_init(project, commit_message="baseline")
    # The first run records the base ("baseline": nothing to diff yet); the second, at
    # the same base, is the first whose staleness question is ANSWERED ("swept"), which
    # is itself something to record. From the third on there is nothing new to say.
    assert kyo("sweep", "--json").json["coverage"] == "baseline"
    first = kyo("sweep", "--json")
    assert first.json["recorded"] is True and first.json["coverage"] == "swept"
    export_dir = project / "kyosei" / "export"
    before = {
        "sweeps": (export_dir / "_sweeps.jsonl").read_bytes(),
        "allocations": (export_dir / "_allocations.json").read_bytes(),
        "ledger": _ledger_text(project),
        "status": _porcelain(project, "kyosei/export"),
    }

    again = kyo("sweep", "--json")
    assert again.code == 0, again
    assert again.json["recorded"] is False
    assert again.json["unchanged_since"] == first.json["sweep_run_id"]
    assert (export_dir / "_sweeps.jsonl").read_bytes() == before["sweeps"]
    assert (export_dir / "_allocations.json").read_bytes() == before["allocations"]
    assert _ledger_text(project) == before["ledger"]
    assert _porcelain(project, "kyosei/export") == before["status"]
    assert "not recorded" in kyo("sweep").out


def test_a_no_op_sweep_leaves_a_committed_store_clean(split_project) -> None:
    """KYO-418.c8's `git status --porcelain` form. Under the split layout the store is
    its own repository, so committing it does not move the code HEAD the sweep diffs
    -- which is exactly the Stop hook's case: a sweep per assistant turn, no new code."""
    from .conftest import invoke

    code, store = split_project
    for _ in range(2):  # the baseline run, then the first answered one
        first = invoke(["--root", str(code), "sweep", "--json"])
        assert first.code == 0 and first.json["recorded"] is True, first
    _commit_all(store, "store after the first sweep")
    assert _porcelain(store, ".") == ""
    again = invoke(["--root", str(code), "sweep", "--json"])
    assert again.code == 0 and again.json["recorded"] is False, again
    assert _porcelain(store, ".") == "", "a no-op sweep dirtied the store"


def test_a_sweep_at_a_new_base_or_with_a_finding_still_records(
    kyo, project: Path, write_file
) -> None:
    (project / "alpha.py").write_text("x = 1\n", encoding="utf-8")
    record = _promote_touching(kyo, project, write_file, paths=["alpha.py"], name="r")
    git_init(project, commit_message="baseline")
    first = kyo("sweep", "--json")
    (project / "other.txt").write_text("unrelated\n", encoding="utf-8")
    _commit_all(project, "an unrelated commit")
    moved = kyo("sweep", "--json")
    assert moved.json["recorded"] is True
    assert moved.json["sweep_run_id"] != first.json["sweep_run_id"]
    # Same base again, but a finding: still recorded.
    (project / "alpha.py").unlink()
    flagged = kyo("sweep", "--json")
    assert flagged.json["recorded"] is True and flagged.json["flags"]
    assert flagged.json["flags"][0]["record_id"] == record


# -------------------------------------- c6/c7: stops counted in place, on running only


@pytest.fixture
def git_project_dirty(git_project: Path) -> Path:
    from .test_subagent_stop_worktree import CONTROLLER_EDIT

    (git_project / CONTROLLER_EDIT).write_text("edited by the controller\n", encoding="utf-8")
    return git_project


def test_an_unresolved_stop_is_counted_not_written(
    kyo, git_project_dirty: Path, tmp_path: Path
) -> None:
    """KYO-418.c6. Fails if an unresolved stop appends a ledger line."""
    from .test_subagent_stop_worktree import _running_dispatch, _stop

    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-418-unresolved")
    lines = len(event_lines(git_project_dirty))
    size = _ledger_bytes(git_project_dirty)
    for _ in range(2):
        result = _stop(kyo, "check-clean", tmp_path, git_project_dirty, None, "--json")
        assert result.code == 0, result
        assert result.json["subject"]["outcome"] == STOP_TARGET_UNRESOLVED
    assert len(event_lines(git_project_dirty)) == lines
    assert _ledger_bytes(git_project_dirty) == size

    def counted() -> dict:
        audits = kyo("worktrees", "--json").json["stop_audits"]
        return audits["unresolved_counted"][dispatch_id]

    assert counted()["count"] == 2
    assert counted()["reasons"] == {"prompt-names-none": 2}
    # It survives a rebuild: it lives on the dispatch's own row, in its record's export.
    assert kyo("rebuild", "--force").code == 0
    assert counted()["count"] == 2
    # And a consumer shows it: the census and the doctor notice both fold it in.
    census = kyo("worktrees", "--json").json["stop_audits"]["census"]
    assert census["unmatched_worker"] == 2, census
    doctor = kyo("doctor", "--json").json
    notice = json.dumps(doctor)
    assert dispatch_id in notice and "could not be matched" in notice


def test_a_matched_stop_still_writes_its_row(
    kyo, git_project_dirty: Path, tmp_path: Path
) -> None:
    from .test_subagent_stop_worktree import _running_dispatch, _stop

    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-418-matched")
    lines = len(event_lines(git_project_dirty))
    result = _stop(kyo, "check-clean", tmp_path, git_project_dirty, dispatch_id, "--json")
    assert result.json["subject"]["outcome"] == STOP_TARGET_MATCHED
    assert len(event_lines(git_project_dirty)) == lines + 1


def test_a_tick_never_counts_or_rewrites_a_finished_dispatch(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """KYO-418.c7, reproduced first: before the fix a tick incremented the counter on a
    dispatch that had already ended and rewrote its record's export. Fails if the tick
    counts a non-running dispatch."""
    from .test_dispatch_worktree import _session_with_a_worker, _tick_stop
    from .test_subagent_stop_worktree import _running_dispatch, _stop

    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-418-finished")
    record = dispatch_id.rsplit(".", 1)[0]
    closed = kyo(
        "dispatch", "close", dispatch_id, "--terminated-no-report", "--why", "stopped here"
    )
    assert closed.code == 0, closed
    exported = export_of(git_project, record).read_bytes()

    transcript = _session_with_a_worker(tmp_path)
    tick = _tick_stop(kyo, "check-clean", transcript, git_project, "--json")
    assert tick.code == 0, tick
    assert dispatch_id in tick.json["subject"]["candidates"], "KYO-201 keeps it a candidate"
    assert export_of(git_project, record).read_bytes() == exported
    assert kyo("show", dispatch_id, "--json").json["tick_audit"] is None

    # KYO-201's matching still resolves against every existing worktree.
    matched = _stop(kyo, "check-clean", tmp_path, git_project, dispatch_id, "--json")
    assert matched.json["subject"]["outcome"] == STOP_TARGET_MATCHED
    assert matched.json["subject"]["dispatch_id"] == dispatch_id


def test_the_unresolved_counter_key_is_what_the_reader_reads() -> None:
    assert UNRESOLVED_COUNTER_KEY == "unresolved"


# ------------------------------------------------ c12/c13: check-build is per session


def _brief_as(kyo, session: str) -> None:
    payload = json.dumps({"session_id": session, "hook_event_name": "SessionStart"})
    assert kyo("brief", "--session-start", stdin=payload).code == 0


def _check_build_as(kyo, session: str | None):
    payload = {"hook_event_name": "UserPromptSubmit"}
    if session is not None:
        payload["session_id"] = session
    return kyo("check-build", "--hook", stdin=json.dumps(payload))


def test_check_build_compares_against_its_own_sessions_opening_brief(
    kyo, running  # noqa: F811 - the fixture
) -> None:
    """KYO-418.c12. Session A opened on build OLD, session B later on NEW; the running
    build is OLD. A's prompt is unchanged; B's is changed. Fails if the session filter
    is removed (A would then compare against B's newer opening brief)."""
    running(*OLD)
    _brief_as(kyo, "session-a")
    running(*NEW)
    _brief_as(kyo, "session-b")
    running(*OLD)

    a = _check_build_as(kyo, "session-a")
    assert (a.code, a.out) == (0, "")
    b = _check_build_as(kyo, "session-b")
    assert b.code == 0 and "build bbbbbbbb" in b.out and "build aaaaaaaa" in b.out

    running(*NEW)
    a_changed = _check_build_as(kyo, "session-a")
    assert "build aaaaaaaa" in a_changed.out and "build bbbbbbbb" in a_changed.out
    assert (_check_build_as(kyo, "session-b").out) == ""


def test_check_build_is_silent_for_a_session_with_no_opening_brief(
    kyo, running  # noqa: F811 - the fixture
) -> None:
    """KYO-418.c13: an unknown session, or a payload with no session at all."""
    running(*OLD)
    _brief_as(kyo, "session-a")
    running(*NEW)
    for session in ("session-z", None):
        result = _check_build_as(kyo, session)
        assert (result.code, result.out, result.err) == (0, "", "")
    assert _check_build_as(kyo, "session-a").out  # the control: A itself does see it


def test_the_brief_run_records_its_session_in_the_db_and_the_ledger(
    kyo, project: Path
) -> None:
    _brief_as(kyo, "session-q")
    rows = [
        json.loads(line)
        for line in (project / "kyosei" / "export" / "_briefs.jsonl").read_text().splitlines()
    ]
    assert rows[-1]["session_id"] == "session-q"
    assert kyo("brief").code == 0  # an ordinary brief records no session
    rows = [
        json.loads(line)
        for line in (project / "kyosei" / "export" / "_briefs.jsonl").read_text().splitlines()
    ]
    assert rows[-1]["session_id"] is None
