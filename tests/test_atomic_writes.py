"""KYO-407: the export's whole-file writers replace atomically, its readers name a torn
file with its remedy, `rebuild --force` validates before it deletes, the events
migration survives being interrupted and cannot race an append, and the temporary files
all of this leaves behind on a crash are gitignored.

Every writer of the git-canonical export
used ``Path.write_text``, which truncates and then writes, so a concurrent reader saw
torn JSON and a writer killed mid-write left it on disk -- and then `kyo list`,
`kyo doctor` and `kyo rebuild --force` all died on a bare ``JSONDecodeError``, the last
with the database already unlinked.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path

import pytest

from kyosei import docs_numbered, scaffold, stop_audit, stop_target
from kyosei import events_ledger as events_ledger_module
from kyosei import export as export_module
from kyosei import ledger_scrub as ledger_scrub_module
from kyosei import ledgers as ledgers_module
from kyosei.events_ledger import (
    events_dir_in,
    events_path_in,
    migrate_events_to_shards,
    read_events_in,
)
from kyosei.export import ATOMIC_TEMP_SUFFIX, ExportFileUnreadable, atomic_write_text
from kyosei.store import open_store

from .conftest import git_init, new_record

# --------------------------------------------------------------------------- c1

#: Every function the criterion names as replacing a whole export file, by module.
_WHOLE_FILE_WRITERS = {
    export_module: (
        "write_export",
        "refresh_exports",
    ),
    ledgers_module: ("write_allocations",),
    ledger_scrub_module: (
        "_scrub_one_event_file",
        "scrub_text_from_ledgers",
        "_scrub_shift_notes",
    ),
    docs_numbered: ("apply_assembly",),
}

#: The shapes a truncating whole-file write takes.
_TRUNCATING_CALLS = frozenset({"write_text", "write_bytes"})


def _function(module, name: str) -> ast.FunctionDef:
    tree = ast.parse(inspect.getsource(module))
    return next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _calls(function: ast.FunctionDef) -> list[ast.Call]:
    return [node for node in ast.walk(function) if isinstance(node, ast.Call)]


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _opens_for_writing(call: ast.Call) -> bool:
    if _called_name(call) != "open":
        return False
    modes = [a for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    modes += [k.value for k in call.keywords if k.arg == "mode"]
    return any(isinstance(m, ast.Constant) and "w" in str(m.value) for m in modes)


@pytest.mark.parametrize(
    ("module", "name"),
    [(module, name) for module, names in _WHOLE_FILE_WRITERS.items() for name in names],
    ids=lambda value: value if isinstance(value, str) else value.__name__.rsplit(".", 1)[-1],
)
def test_every_whole_file_export_writer_goes_through_the_atomic_helper(module, name) -> None:
    """KYO-407.c1, pinned as the property: switching any of these back to a truncating
    write -- ``write_text``, ``write_bytes`` or ``open(..., "w")`` -- fails here, and so
    does a writer that stops calling :func:`atomic_write_text` at all."""
    function = _function(module, name)
    calls = _calls(function)
    truncating = [c for c in calls if _called_name(c) in _TRUNCATING_CALLS or _opens_for_writing(c)]
    assert not truncating, f"{name} writes with a truncating call at line {truncating[0].lineno}"
    assert any(_called_name(c) == "atomic_write_text" for c in calls), (
        f"{name} no longer replaces its file through atomic_write_text"
    )


def test_the_hook_writers_reach_the_export_only_through_the_atomic_writers() -> None:
    """KYO-407.c1's "and the writes reached from record_stop_audit / record_tick_audit":
    those run from SubagentStop every ~32 s per agent, outside any transaction, so they
    must write the export through the functions pinned above and nothing else."""
    for name in ("record_stop_audit", "record_tick_audit"):
        calls = _calls(_function(stop_audit, name))
        assert not [c for c in calls if _called_name(c) in _TRUNCATING_CALLS], name
        exported = {
            _called_name(c)
            for c in calls
            if isinstance(c.func, ast.Attribute)
            and isinstance(c.func.value, ast.Name)
            and c.func.value.id in {"export", "events_ledger", "ledgers"}
        }
        assert exported <= {"append_events", "write_allocations", "write_exports"}, exported
    # ...and write_exports is write_export per record, which is pinned above.
    per_record = _calls(_function(export_module, "write_exports"))
    assert "write_export" in {_called_name(c) for c in per_record}


def test_a_concurrent_reader_never_sees_a_torn_allocations_file(project: Path) -> None:
    """The torn read, measured in miniature: one thread rewrites the counter snapshot
    over and over while another parses it. With a truncating write the reader can catch
    the file between the truncate and the write; through the atomic helper it only ever
    opens a whole old or a whole new file."""
    store = open_store(project)
    try:
        path = ledgers_module.allocations_path(store)
        # A snapshot big enough that a truncating write takes more than one syscall.
        store.conn.execute("BEGIN IMMEDIATE")
        for i in range(4000):
            store.conn.execute(
                "INSERT OR REPLACE INTO allocations (scope, next_value) VALUES (?, ?)",
                (f"PAD-{i:05d}.x", i),
            )
        store.conn.execute("COMMIT")
        ledgers_module.write_allocations(store)
        failures: list[str] = []
        stop = threading.Event()

        def read() -> None:
            while not stop.is_set():
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                except ValueError as exc:
                    failures.append(str(exc))

        reader = threading.Thread(target=read)
        reader.start()
        try:
            for _ in range(150):
                ledgers_module.write_allocations(store)
        finally:
            stop.set()
            reader.join()
    finally:
        store.close()
    assert failures == []
    assert not list(path.parent.glob(f"*{ATOMIC_TEMP_SUFFIX}")), "a temporary was left behind"


def test_a_failed_atomic_write_leaves_the_target_whole_and_no_temporary(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "doc.json"
    target.write_text('{"old": true}\n', encoding="utf-8")
    os.chmod(target, 0o644)

    def refuse(*_args, **_kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr(export_module.os, "replace", refuse)
    with pytest.raises(OSError, match="disk went away"):
        atomic_write_text(target, '{"new": true}\n')
    assert target.read_text(encoding="utf-8") == '{"old": true}\n'
    assert sorted(p.name for p in tmp_path.iterdir()) == ["doc.json"]

    monkeypatch.undo()
    atomic_write_text(target, '{"new": true}\n')
    assert target.read_text(encoding="utf-8") == '{"new": true}\n'
    assert target.stat().st_mode & 0o777 == 0o644, "an atomic write must keep the file's mode"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["doc.json"]


# --------------------------------------------------------------------------- c2


def test_a_redaction_rewrites_only_the_shards_its_text_is_in(
    kyo, project: Path, write_file, monkeypatch
) -> None:
    """KYO-407.c2. Shards are made tiny so the ledger spans many of them; the redacted
    note's events live in the last few. Every other shard keeps its bytes AND its mtime
    -- the mtime is what catches a rewrite that happened to produce the same bytes,
    which is exactly what re-serialising an untouched line does."""
    monkeypatch.setattr(events_ledger_module, "EVENTS_SHARD_MAX_BYTES", 1500)
    for i in range(6):
        new_record(kyo, title=f"Unrelated {i}", criteria=["a"])
    target = new_record(kyo, title="Carries the secret", criteria=["a"])
    secret = "a sentence that has to come out of the ledger again"
    assert kyo("edit", target, "--note-file", write_file("n.txt", secret)).code == 0

    shards = sorted(events_dir_in(project / "kyosei" / "export").iterdir())
    naming = {p for p in shards if f"{target}.n1".encode() in p.read_bytes()}
    untouched = [p for p in shards if p not in naming]
    assert len(shards) > 3 and naming and untouched, (len(shards), naming)
    past = time.time() - 3600
    for path in shards:
        os.utime(path, (past, past))
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in untouched}

    result = kyo("redact", f"{target}.n1", "--why", "a test secret")
    assert result.code == 0, result
    for path in untouched:
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before[path], path.name
    assert all(secret.encode() not in p.read_bytes() for p in shards)


# --------------------------------------------------------------------------- c3

_REMEDY_RE = re.compile(r"`(git -C .+? checkout -- .+?)`")


def _committed_project(kyo, project: Path, write_file) -> dict[str, Path]:
    """A project with a line in every ledger and one record document, committed."""
    record = new_record(kyo, title="Torn", criteria=["a"])
    assert kyo("sweep").code == 0
    assert kyo("brief").code == 0
    assert kyo("note", "--shift", "--file", write_file("s.txt", "a shift note")).code == 0
    git_init(project)
    export = project / "kyosei" / "export"
    files = {
        "allocations": export / "_allocations.json",
        "record document": export / f"{record}.json",
        "event shard": sorted(events_dir_in(export).iterdir())[-1],
        "sweeps": export / "_sweeps.jsonl",
        "briefs": export / "_briefs.jsonl",
        "shift notes": export / "_shift_notes.jsonl",
    }
    for name, path in files.items():
        assert path.is_file() and path.stat().st_size, name
    return files


def _tear(path: Path) -> None:
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])


def _assert_names_the_torn_file(output: str, path: Path) -> str:
    assert "JSONDecodeError" not in output and "Traceback" not in output, output
    assert str(path) in output, output
    assert re.search(r" line \d+ ", output), output
    match = _REMEDY_RE.search(output)
    assert match, output
    assert f"checkout -- {shlex.quote(path.name)}" in match.group(1)
    return match.group(1)


@pytest.mark.parametrize(
    "which",
    ["allocations", "record document", "event shard", "sweeps", "briefs", "shift notes"],
)
@pytest.mark.parametrize("command", [("list",), ("doctor",), ("rebuild", "--force")])
def test_a_torn_export_file_is_named_with_the_checkout_that_clears_it(
    kyo, project: Path, write_file, which: str, command: tuple[str, ...]
) -> None:
    """KYO-407.c3 and the standing remedy rule: tear the file, see the message, run
    EXACTLY the command it printed, and the same command then succeeds. A command that
    never reads the torn file (`kyo list` reads only the counters) simply succeeds."""
    files = _committed_project(kyo, project, write_file)
    path = files[which]
    _tear(path)

    torn = kyo(*command)
    output = torn.out + torn.err
    if torn.code == 0:
        assert command == ("list",) and which != "allocations", output
        return
    remedy = _assert_names_the_torn_file(output, path)

    subprocess.run(shlex.split(remedy), check=True, capture_output=True)
    again = kyo(*command)
    assert again.code == 0, again


def test_the_loader_error_is_still_a_value_error_for_callers_that_tolerated_one(
    tmp_path: Path,
) -> None:
    """Doctor's ledger-gap check catches ``ValueError`` to report a bad line as a finding
    rather than crash the run; the named error keeps that working."""
    path = tmp_path / "_sweeps.jsonl"
    path.write_text('{"id": "S1"}\n{"id": "S', encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        list(export_module.iter_ledger_lines(path))
    assert isinstance(caught.value, ExportFileUnreadable)
    assert "line 2 " in str(caught.value)


# --------------------------------------------------------------------------- c4


@pytest.mark.parametrize("which", ["allocations", "event shard", "record document"])
def test_rebuild_force_over_a_torn_export_leaves_the_database_untouched(
    kyo, project: Path, write_file, which: str
) -> None:
    """KYO-407.c4: the whole export -- counters included -- is read before the existing
    database is unlinked, so a refusal costs nothing: the db is byte-for-byte what it
    was, and once the file is restored `kyo list` works without any rebuild."""
    files = _committed_project(kyo, project, write_file)
    db = project / "kyosei" / "kyosei.db"
    before = db.read_bytes()
    _tear(files[which])

    result = kyo("rebuild", "--force")
    assert result.code != 0
    remedy = _assert_names_the_torn_file(result.out + result.err, files[which])
    assert db.is_file(), "rebuild --force deleted the database before refusing"
    assert db.read_bytes() == before

    subprocess.run(shlex.split(remedy), check=True, capture_output=True)
    listed = kyo("list")
    assert listed.code == 0, listed


# ---------------------------------------------------------------------- c5 / c6


def _line(event_id: str) -> str:
    return json.dumps({"id": event_id, "seq": int(event_id[1:])}, sort_keys=True) + "\n"


def _ids(directory: Path) -> list[str]:
    return [event["id"] for event in read_events_in(directory)]


def _write(path: Path, *ids: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(_line(i) for i in ids), encoding="utf-8")


def test_a_migration_killed_before_the_unlink_reruns_to_each_event_once(tmp_path: Path) -> None:
    """KYO-407.c5: the state `staging.rename(shards)` leaves when the process dies
    before `monolithic.unlink()`. The shards already hold everything; re-packing would
    lay the monolithic lines down twice (E1, E2, E1, E2, E3) and rebuild would then
    fail forever on the duplicate key. An event appended after the interruption (E4)
    must not defeat the recognition."""
    directory = tmp_path / "export"
    _write(events_path_in(directory), "E1", "E2")
    _write(events_dir_in(directory) / "000001.jsonl", "E1", "E2", "E3", "E4")
    _write(directory / "_events.superseded" / "000001.jsonl", "E3")

    result = migrate_events_to_shards(directory)
    assert _ids(directory) == ["E1", "E2", "E3", "E4"]
    assert result["events"] == 4
    assert not events_path_in(directory).exists()
    assert not (directory / "_events.superseded").exists()


def test_a_migration_killed_between_the_renames_keeps_every_shard_event(tmp_path: Path) -> None:
    """KYO-407.c6: killed after `shards.rename(superseded)` and before
    `staging.rename(shards)`, the superseded directory is the only copy of E3. The old
    entry step deleted it; now it is renamed back and the migration runs normally."""
    directory = tmp_path / "export"
    _write(events_path_in(directory), "E1", "E2")
    _write(directory / "_events.superseded" / "000001.jsonl", "E3")
    _write(directory / "_events.migrating" / "000001.jsonl", "E1", "E2", "E3")

    migrate_events_to_shards(directory)
    assert _ids(directory) == ["E1", "E2", "E3"]
    assert not events_path_in(directory).exists()
    assert not (directory / "_events.superseded").exists()
    assert not (directory / "_events.migrating").exists()


# --------------------------------------------------------------------------- c7


def test_an_append_during_a_migration_lands_in_the_migrated_shards(
    kyo, project: Path, monkeypatch
) -> None:
    """KYO-407.c7. The append is started at the worst moment -- after the copy has
    read every file, just before the live shard directory is renamed aside to be
    deleted -- and given time to finish. Without the shared lock it writes into that
    doomed directory and the event is gone; with it, it waits and lands in the new
    shards, exactly once."""
    record = new_record(kyo, title="Migrating", criteria=["a"])
    assert kyo("edit", record, "--title", "Migrating again").code == 0
    directory = project / "kyosei" / "export"
    shard = sorted(events_dir_in(directory).iterdir())[-1]
    lines = shard.read_text(encoding="utf-8").splitlines(keepends=True)
    assert len(lines) >= 2
    events_path_in(directory).write_text("".join(lines[:-1]), encoding="utf-8")
    shard.write_text(lines[-1], encoding="utf-8")

    store = open_store(project)
    appended = {"id": "TST-E-DURING", "seq": 999999, "record_id": None}
    real_rename = Path.rename
    worker: list[threading.Thread] = []

    def rename(self: Path, target):
        if self.name == events_ledger_module.EVENTS_DIRNAME and not worker:
            thread = threading.Thread(
                target=events_ledger_module.append_events, args=(store, [dict(appended)])
            )
            worker.append(thread)
            thread.start()
            thread.join(timeout=0.5)
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    try:
        migrate_events_to_shards(directory)
        assert worker, "the migration never renamed the live shard directory"
        worker[0].join(timeout=10)
        assert not worker[0].is_alive()
    finally:
        monkeypatch.undo()
        store.close()
    ids = _ids(directory)
    assert ids.count(appended["id"]) == 1, ids[-3:]
    assert not (directory / "_events.superseded").exists()


# --------------------------------------------------------------------------- c8


def _ignored(repo: Path, relative: str) -> bool:
    return (
        subprocess.run(
            ["git", "check-ignore", "-q", relative], cwd=repo, capture_output=True
        ).returncode
        == 0
    )


_TEMPORARIES = (
    "kyosei/sessions.json.123.tmp",
    f"kyosei/export/.TST-1.json.a1b2c3{ATOMIC_TEMP_SUFFIX}",
    f"kyosei/export/_events/.000001.jsonl.x9{ATOMIC_TEMP_SUFFIX}",
    f"kyosei/export/.{ledgers_module.ALLOCATIONS_FILENAME}.q{ATOMIC_TEMP_SUFFIX}",
)


def test_the_temporaries_are_ignored_in_a_fresh_project(project: Path) -> None:
    """KYO-407.c8: `git check-ignore kyosei/sessions.json.123.tmp` matches, and so does
    every name :func:`atomic_write_text` can leave behind, under the export and its
    shard directory alike."""
    git_init(project)
    for relative in _TEMPORARIES:
        assert _ignored(project, relative), relative
    assert not _ignored(project, "kyosei/export/TST-1.json"), "the export itself is tracked"


def test_install_procedures_tops_up_an_existing_projects_temporary_patterns(
    kyo, project: Path
) -> None:
    gitignore = project / "kyosei" / ".gitignore"
    gitignore.write_text(
        "kyosei.db\nkyosei.db-shm\nkyosei.db-wal\nsessions.json\ndoctor-report.txt\n",
        encoding="utf-8",
    )
    git_init(project)
    assert not _ignored(project, "kyosei/sessions.json.123.tmp")

    result = kyo("install-procedures", "--json")
    assert result.code == 0, result
    for relative in _TEMPORARIES:
        assert _ignored(project, relative), relative


def test_the_temporary_patterns_come_from_the_names_they_cover() -> None:
    """The patterns are derived, so renaming the sessions file moves its temporary's
    pattern with it and the atomic helper's suffix is the one constant both use."""
    patterns = scaffold.store_gitignore_text("db", "s.json", "r.txt").splitlines()
    assert "s.json.*.tmp" in patterns
    assert f"*{ATOMIC_TEMP_SUFFIX}" in patterns
    assert scaffold.store_gitignore_text() == scaffold.KYOSEI_GITIGNORE


# --------------------------------------------------------------------------- c9


def test_the_stop_audit_appends_its_event_before_it_writes_the_counters(
    project: Path, monkeypatch
) -> None:
    """KYO-407.c9, the KYO-404 ordering: a stop audit's event reaches the ledger before
    the counter snapshot that accounts for it, so a hook killed between the two leaves
    a ledger line and a stale snapshot (which rebuild's max(snapshot, seq) heals) and
    never a snapshot counting an event no ledger holds."""
    from kyosei.commands import Context

    calls: list[str] = []
    real_append = events_ledger_module.append_events
    real_allocations = ledgers_module.write_allocations

    def append(store, events):
        calls.append("append_events")
        return real_append(store, events)

    def allocations(store):
        calls.append("write_allocations")
        return real_allocations(store)

    monkeypatch.setattr(events_ledger_module, "append_events", append)
    monkeypatch.setattr(ledgers_module, "write_allocations", allocations)
    target = stop_target.StopTarget(
        directory=project, outcome=stop_target.STOP_TARGET_UNRESOLVED, why="a test stop"
    )
    ctx = Context(root=project, actor="test", argv=[])
    event_id, sentence = stop_audit.record_stop_audit(ctx, target, "{}")
    assert event_id is not None, sentence
    assert calls == ["append_events", "write_allocations"], calls


def test_the_tick_audit_writes_no_event_so_has_no_order_to_keep() -> None:
    """KYO-407.c9's other half. `record_tick_audit` writes NO events row by design
    (KYO-375.c1: a counter on the dispatch row, exported with the record), so there is
    no append for its export write to precede; this pins that, so the day it gains one
    the ordering question is asked again rather than skipped."""
    # KYO-418: the tick and the unresolved stop share one in-place writer.
    for writer in ("record_tick_audit", "record_unresolved_audit"):
        names = [_called_name(c) for c in _calls(_function(stop_audit, writer))]
        assert "append_events" not in names and "_count_in_place" in names, (writer, names)
    names = [_called_name(c) for c in _calls(_function(stop_audit, "_count_in_place"))]
    assert "append_events" not in names
    assert "write_exports" in names
