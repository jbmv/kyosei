"""KYO-414: four places that read store state outside the write that acts on it.

Every race here is driven deterministically: one writer is paused at a hook point
between its read and its write, a second complete ``kyo`` command runs in-process on
its own connection, and the first then resumes. Nothing depends on timing.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from kyosei import cmd_links, ids, ledgers, rebuild, sweep
from kyosei import store as store_module

from .conftest import allow_direct_filing, event_lines, git_init, invoke, new_record


def _once(fn):
    """Run ``fn`` the first time the returned callable is called, and never again —
    including from inside ``fn`` itself, where the nested command reaches the same
    hook point."""
    state = {"fired": False}

    def fire() -> None:
        if not state["fired"]:
            state["fired"] = True
            fn()

    fire.state = state
    return fire


def _db(project: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db", isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _alloc_file(project: Path) -> dict[str, int]:
    return json.loads(
        (project / "kyosei" / "export" / "_allocations.json").read_text(encoding="utf-8")
    )


def _max_event_seq(project: Path) -> int:
    return max(int(line["id"].rsplit("-E", 1)[1]) for line in event_lines(project))


# ---------------------------------------------------------------- allocations snapshot


def test_interleaved_snapshot_writers_cannot_leave_the_file_below_the_ledger(
    kyo, project: Path, monkeypatch
) -> None:
    """KYO-414.c1, the interleaving itself. A commits, A reads the counters, B commits
    and writes its snapshot, A writes its older one last. Before the fix nothing ordered
    A's read-and-write against B's commit, and the file ended below an id the ledger
    held. With the fix A holds the write lock across its read and write, so B's commit
    cannot land inside that window (it is refused here with a zero busy-timeout rather
    than left to wait), and the file A writes is current."""
    allow_direct_filing(project, "bug")
    real_connect = store_module.sqlite3.connect
    outcome: dict = {}

    def second_writer() -> None:
        # B, on its own connection, with no patience: under the fix the lock is held
        # and B is refused at once; without it B commits and writes a higher snapshot.
        monkeypatch.setattr(
            store_module.sqlite3,
            "connect",
            lambda *a, **k: real_connect(*a, **{**k, "timeout": 0}),
        )
        try:
            outcome["b"] = invoke(
                ["--root", str(project), "new", "bug", "--title", "B's record", "--json"]
            )
        except sqlite3.OperationalError as exc:  # the lock A holds, refused at once
            outcome["b_refused"] = str(exc)
        finally:
            monkeypatch.setattr(store_module.sqlite3, "connect", real_connect)

    fire = _once(second_writer)
    real_serialise = ledgers.serialise_allocations

    def paused_serialise(allocations):
        # A has read its counters (the argument) and not yet written them.
        fire()
        return real_serialise(allocations)

    monkeypatch.setattr(ledgers, "serialise_allocations", paused_serialise)
    first = kyo("new", "bug", "--title", "A's record", "--json")
    assert first.code == 0, first
    assert fire.state["fired"], "the hook point was never reached"

    snapshot = _alloc_file(project)
    assert snapshot["event"] > _max_event_seq(project), (
        f"_allocations.json says event next={snapshot['event']} while the ledger already "
        f"holds E{_max_event_seq(project)} (B: {outcome.get('b')!r})"
    )
    assert snapshot["record"] > max(
        int(r["seq"]) for r in _db(project).execute("SELECT seq FROM records")
    )
    # Under the fix, B met A's lock (a real B waits out the busy timeout instead); run
    # it now and the snapshot still tracks the ledger.
    # KYO-421 turned the raw OperationalError into a worded refusal (exit 1, naming the
    # lock) at cli.main; either form is B meeting A's lock.
    refused = outcome.get("b_refused") or (
        outcome["b"].out + outcome["b"].err if outcome["b"].code != 0 else ""
    )
    assert "lock" in refused.lower(), outcome
    assert kyo("new", "bug", "--title", "B's record").code == 0
    assert _alloc_file(project)["event"] == _max_event_seq(project) + 1


def _lower_snapshot_the_way_the_race_does(kyo, project: Path) -> dict[str, int]:
    """A's stale snapshot, written after B's: captured before B's mutation and put back
    after it — byte-for-byte what the late post-commit writer leaves on disk."""
    path = project / "kyosei" / "export" / "_allocations.json"
    stale = path.read_text(encoding="utf-8")
    record = new_record(kyo, title="B's record", criteria=["b holds"])
    assert kyo("edit", record, "--note-file", "-", stdin="a note on B's record\n").code == 0
    path.write_text(stale, encoding="utf-8")
    return json.loads(stale)


def test_rebuild_from_a_lowered_snapshot_allocates_above_every_loaded_id(
    kyo, project: Path
) -> None:
    """KYO-414.c1, the consequence, and c2. Before the fix `_load` inserted the
    snapshot verbatim, so the rebuilt db reissued B's ids and the next mutation died on
    an IntegrityError. Seeding each scope at max(snapshot, highest loaded seq + 1) makes
    every counter strictly above every id the export carries."""
    new_record(kyo, title="A's record", criteria=["a holds"])
    lowered = _lower_snapshot_the_way_the_race_does(kyo, project)
    assert lowered["event"] <= _max_event_seq(project), "the setup did not lower the file"

    rebuilt = kyo("rebuild", "--force")
    assert rebuilt.code == 0, rebuilt

    conn = _db(project)
    allocations = {
        row["scope"]: int(row["next_value"])
        for row in conn.execute("SELECT scope, next_value FROM allocations")
    }
    highs = rebuild.loaded_allocation_highs(conn)
    assert highs, "nothing was loaded"
    for scope, high, what in highs:
        assert allocations.get(scope, 0) > high, (scope, allocations.get(scope), high, what)

    after = new_record(kyo, title="the next mutation", criteria=["it lands"])
    assert after, "the next mutation after rebuild failed"
    assert kyo("edit", after, "--note-file", "-", stdin="and a child id too\n").code == 0


def test_rebuild_never_lowers_a_counter_the_snapshot_holds_higher(kyo, project: Path) -> None:
    """KYO-414.c2, the other direction: the snapshot is still the floor it always was.
    A counter above every loaded row (an id voided or hand-deleted after issue) must
    survive rebuild, or law 4's original incident — a reissued id — comes back."""
    record = new_record(kyo, title="one", criteria=["c"])
    path = project / "kyosei" / "export" / "_allocations.json"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    snapshot["record"] = 50
    snapshot[f"criterion:{record}"] = 9
    path.write_text(json.dumps(snapshot, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    assert kyo("rebuild", "--force").code == 0
    allocations = {
        row["scope"]: int(row["next_value"])
        for row in _db(project).execute("SELECT scope, next_value FROM allocations")
    }
    assert allocations["record"] == 50
    assert allocations[f"criterion:{record}"] == 9


def test_every_child_kind_has_an_allocated_table() -> None:
    """KYO-414.c2/c3: rebuild's seeding and doctor's check read one list. A child kind
    `ids` knows and the list omits is a scope neither the seeding nor the check covers —
    exactly how doc-ref and docs-obligation fell out of `check_allocations`."""
    covered = {kind for _table, kind in rebuild.ALLOCATED_CHILD_TABLES}
    assert covered == set(ids.CHILD_KIND_BY_LETTER.values())


@pytest.mark.parametrize(
    ("table", "kind", "columns"),
    [
        (
            "doc_refs",
            "doc-ref",
            "(id, record_id, seq, path, created_at) VALUES (?, ?, 3, 'README.md', 'x')",
        ),
        (
            "docs_obligations",
            "docs-obligation",
            "(id, record_id, seq, dispatch_id, doc_path, section, cause, detail, created_at)"
            " VALUES (?, ?, 3, 'nope', 'README.md', 's', 'c', 'd', 'x')",
        ),
    ],
)
def test_doctor_reports_a_lowered_docs_child_counter(
    kyo, project: Path, table: str, kind: str, columns: str
) -> None:
    """KYO-414.c3: `check_allocations` compared ten child scopes and left out 'x' and
    'o', so a lowered doc-ref or docs-obligation counter was never reported."""
    record = new_record(kyo, title="docs")
    letter = ids.CHILD_LETTER_BY_KIND[kind]
    conn = _db(project)
    conn.execute(f"INSERT INTO {table} {columns}", (f"{record}.{letter}3", record))
    conn.execute(
        "INSERT OR REPLACE INTO allocations (scope, next_value) VALUES (?, 2)",
        (f"{kind}:{record}",),
    )
    doctor = kyo("doctor")
    assert f"allocation scope '{kind}:{record}' would reissue" in doctor.out + doctor.err, doctor


# ------------------------------------------------------------------- concurrent sweeps


def _commit(project: Path, name: str, text: str) -> None:
    (project / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", f"change {name}"],
        cwd=project,
        check=True,
        capture_output=True,
    )


def _swept_records(kyo, project: Path, write_file, names: list[str]) -> list[str]:
    out = []
    for name in names:
        (project / f"{name}.py").write_text("x = 1\n", encoding="utf-8")
        idea = project / "ideas" / f"{name}.md"
        idea.write_text(f"# {name}\n\nDo the thing.\n", encoding="utf-8")
        result = kyo(
            "promote",
            str(idea),
            "--kind",
            "chore",
            "--criteria-file",
            write_file(f"{name}-criteria.txt", "it works\n"),
            "--touches-file",
            write_file(f"{name}-touches.txt", f"{name}.py\n"),
            "--json",
        )
        assert result.code == 0, result
        out.append(result.json["record"]["id"])
    return out


def _open_touch_changed(kyo, record: str) -> list[dict]:
    shown = kyo("show", record, "--json")
    assert shown.code == 0, shown
    return [
        f
        for f in shown.json["flags"]
        if f["resolved_at"] is None and f["flag_type"] == "touch-changed"
    ]


def _pause_sweep(monkeypatch, project: Path, then=None):
    """Pause the outer sweep after its reads and before it writes, run
    a second whole sweep there, then (optionally) something else. Returns the result
    holder."""
    holder: dict = {}

    def second_sweep() -> None:
        holder["b"] = invoke(["--root", str(project), "sweep", "--json"])
        if then is not None:
            then()

    fire = _once(second_sweep)
    real = sweep.find_descriptions_predating_their_claims

    def paused(*args, **kwargs):
        # The last thing a sweep computes before it opens its write transaction: the
        # prior run, the open flags and every widening have all been read by now.
        found = real(*args, **kwargs)
        fire()
        return found

    monkeypatch.setattr(sweep, "find_descriptions_predating_their_claims", paused)
    holder["fire"] = fire
    return holder


def test_two_interleaved_sweeps_file_one_touch_changed_flag_per_record(
    kyo, project: Path, write_file, monkeypatch
) -> None:
    """KYO-414.c4, the reproduced case: both runs read "no open flag", each filed one,
    and `kyo flag list` said 12 open of 12 for six records."""
    records = _swept_records(kyo, project, write_file, ["alpha", "beta", "gamma"])
    git_init(project, commit_message="baseline")
    assert kyo("sweep").code == 0
    for name in ("alpha", "beta", "gamma"):
        (project / f"{name}.py").write_text("x = 2\n", encoding="utf-8")
    _commit(project, "alpha.py", "x = 2\n")

    holder = _pause_sweep(monkeypatch, project)
    first = kyo("sweep", "--json")
    assert first.code == 0, first
    assert holder["fire"].state["fired"]
    assert holder["b"].code == 0, holder["b"]
    assert len(holder["b"].json["flags"]) == 3

    for record in records:
        (flag,) = _open_touch_changed(kyo, record)
        # One commit window, observed once: B's run. A's re-plan under the lock finds
        # B's run as its base and nothing new since it.
        assert sweep.parse_touch_changed_detail(flag["detail"]).sweeps == 1, flag


def test_a_widening_done_by_both_interleaved_sweeps_counts_every_sweep(
    kyo, project: Path, write_file, monkeypatch
) -> None:
    """KYO-414.c4, the lost increment: when a flag already stands, both runs computed
    the same n+1 from the same read and one sweep vanished from the count. Here B
    widens it to 2, a new commit lands, and A's widening must build on B's."""
    (record,) = _swept_records(kyo, project, write_file, ["alpha"])
    git_init(project, commit_message="baseline")
    assert kyo("sweep").code == 0
    _commit(project, "alpha.py", "x = 2\n")
    assert kyo("sweep").code == 0
    (flag,) = _open_touch_changed(kyo, record)
    assert sweep.parse_touch_changed_detail(flag["detail"]).sweeps == 1
    _commit(project, "alpha.py", "x = 3\n")

    holder = _pause_sweep(
        monkeypatch, project, then=lambda: _commit(project, "alpha.py", "x = 4\n")
    )
    first = kyo("sweep", "--json")
    assert first.code == 0, first
    assert holder["b"].code == 0, holder["b"]
    assert holder["b"].json["widened_flags"], holder["b"]

    (flag,) = _open_touch_changed(kyo, record)
    finding = sweep.parse_touch_changed_detail(flag["detail"])
    assert finding.sweeps == 3, flag["detail"]
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=project, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert head.startswith(finding.to_sha), (finding.to_sha, head)


# --------------------------------------------------------------- the brief's watermark


def test_an_event_committed_during_a_brief_is_listed_by_the_next_one(
    kyo, project: Path, monkeypatch
) -> None:
    """KYO-414.c5: the brief read its deltas, then read MAX(seq) in a separate statement
    and stored that as `last_event_seq`. An event committed between the two was inside
    the watermark and in no brief, ever."""
    allow_direct_filing(project, "bug")
    assert kyo("brief").code == 0
    created: dict = {}

    def commit_between() -> None:
        created["run"] = invoke(
            ["--root", str(project), "new", "bug", "--title", "written mid-brief", "--json"]
        )

    fire = _once(commit_between)
    real = store_module.Store.shift_notes_since

    def paused(self, seq_after):
        # Runs after the delta read and before the old code's MAX(seq) read.
        rows = real(self, seq_after)
        fire()
        return rows

    monkeypatch.setattr(store_module.Store, "shift_notes_since", paused)
    during = kyo("brief", "--json")
    assert during.code == 0, during
    assert fire.state["fired"]
    record = created["run"].json["record"]["id"]
    assert record not in {d["record_id"] for d in during.json["deltas"]}

    monkeypatch.setattr(store_module.Store, "shift_notes_since", real)
    after = kyo("brief", "--json")
    assert record in {d["record_id"] for d in after.json["deltas"]}, after.json["deltas"]


# -------------------------------------------------------------------- concurrent unlink


def test_a_second_concurrent_unlink_writes_nothing_and_says_not_found(
    kyo, project: Path, monkeypatch
) -> None:
    """KYO-414.c6: both unlinks read the row, the first deleted it, and the second
    deleted zero rows yet still logged a retraction for both endpoints and printed
    "REMOVED this edge" — two retractions in the ledger for one edge."""
    blocker = new_record(kyo, title="blocker")
    blocked = new_record(kyo, title="blocked")
    link_id = kyo("link", blocker, "blocks", blocked, "--json").json["link"]["id"]
    holder: dict = {}

    def other_unlink() -> None:
        holder["b"] = invoke(
            ["--root", str(project), "unlink", link_id, "--why", "the other session's reason"]
        )

    fire = _once(other_unlink)
    real = cmd_links._titled_edge

    def paused(*args, **kwargs):
        sentence = real(*args, **kwargs)
        fire()
        return sentence

    monkeypatch.setattr(cmd_links, "_titled_edge", paused)
    first = kyo("unlink", link_id, "--why", "this session's reason")
    assert fire.state["fired"]
    assert holder["b"].code == 0, holder["b"]
    assert "REMOVED this edge" in holder["b"].out

    assert first.code != 0, first
    assert "REMOVED this edge" not in first.out
    assert f"no link {link_id!r}" in first.out + first.err, first
    retractions = [
        line for line in event_lines(project) if line["command"].startswith(f"unlink {link_id} ")
    ]
    assert len(retractions) == 2, [r["command"] for r in retractions]  # one per endpoint
    assert all("the other session's reason" in r["command"] for r in retractions)
