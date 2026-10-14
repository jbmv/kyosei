"""Export completeness and ``kyo rebuild`` — charter, Layer 2.

The export must be complete enough to reconstruct the database: per-record JSON,
plus ``_events.jsonl`` (append-only ledger) and ``_allocations.json`` (id counters,
read by rebuild instead of ``max(seq)`` — law 4: a rebuilt db must not be able to
reissue an id whose highest-numbered sibling was later voided or deleted by hand).
"""

from __future__ import annotations

import json
from pathlib import Path

from .conftest import invoke, new_record, verify_criterion


def _events_path(project: Path) -> Path:
    """The one file this project's events ledger is in.

    KYO-268.c2: the ledger is a directory of bounded shards, and a store this small has
    exactly one. Resolved through the reader rather than by naming ``_events.jsonl``, so
    that tests about what the ledger CONTAINS stay about that; the layout is tested in
    ``tests/test_export.py``, and the two layouts are rebuilt against each other below.
    """
    from kyosei.events_ledger import events_files_in

    files = events_files_in(project / "kyosei" / "export")
    assert len(files) == 1, f"expected one events file, found {[p.name for p in files]}"
    return files[0]


def _allocations_path(project: Path) -> Path:
    return project / "kyosei" / "export" / "_allocations.json"


# ------------------------------------------------------------------ export artifacts


def test_events_jsonl_gets_one_line_per_event(kyo, project: Path) -> None:
    new_record(kyo, title="one")
    new_record(kyo, title="two")
    lines = _events_path(project).read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    for line in lines:
        event = json.loads(line)
        # KYO-181 added `schema_version` to this set — the version the line was written
        # at, stamped on the line rather than in a file header so that it is bound to the
        # bytes it versions and so that two branches appending different lines still
        # conflict on nothing. Asserted as an exact set rather than a subset, deliberately:
        # a ledger line is a format, and a key arriving in it unannounced is the drift this
        # test exists to catch.
        #
        # KYO-418: a creation carries no prior form at all -- no `before` (the old
        # format's key, and `null` on its creations) and no `changed` (there was
        # nothing to change from).
        assert set(event) == {
            "id",
            "seq",
            "at",
            "actor",
            "command",
            "record_id",
            "after",
            "schema_version",
        }


def test_events_jsonl_is_append_only(kyo, project: Path) -> None:
    new_record(kyo, title="one")
    first_bytes = _events_path(project).read_bytes()
    new_record(kyo, title="two")
    second_bytes = _events_path(project).read_bytes()
    assert second_bytes.startswith(first_bytes)
    assert len(second_bytes) > len(first_bytes)


def test_a_link_appends_two_events(kyo, project: Path) -> None:
    a = new_record(kyo, title="one")
    b = new_record(kyo, title="two")
    before = len(_events_path(project).read_text(encoding="utf-8").strip().split("\n"))
    kyo("link", a, "blocks", b)
    after = _events_path(project).read_text(encoding="utf-8").strip().split("\n")
    assert len(after) == before + 2


def test_allocations_json_reflects_the_counters(kyo, project: Path) -> None:
    record = new_record(kyo, criteria=["a", "b"])
    data = json.loads(_allocations_path(project).read_text(encoding="utf-8"))
    assert data["record"] >= 2
    assert data[f"criterion:{record}"] >= 3


def test_allocations_json_is_updated_on_every_mutation(kyo, project: Path) -> None:
    record = new_record(kyo)
    before = json.loads(_allocations_path(project).read_text(encoding="utf-8"))
    assert f"gate:{record}" not in before
    kyo("gate", record, "awaiting:human-review")
    kyo("gate", record, "awaiting:human-review")
    after = json.loads(_allocations_path(project).read_text(encoding="utf-8"))
    # Two gates allocated (values 1, 2); next_value is one past the last one handed out.
    assert after[f"gate:{record}"] == 3


# --------------------------------------------------------------------------- rebuild


def test_rebuild_refuses_to_overwrite_an_existing_db(kyo, project: Path) -> None:
    new_record(kyo)
    result = kyo("rebuild")
    assert result.code == 1
    assert "already exists" in result.err


def test_rebuild_with_force_replaces_the_db(kyo, project: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    verify_criterion(kyo, f"{record}.c1", "met")
    result = kyo("rebuild", "--force")
    assert result.code == 0
    doc = kyo("show", record, "--json").json
    assert doc["criteria"][0]["state"] == "met"


def test_rebuild_reconstructs_a_deleted_db(kyo, project: Path) -> None:
    record = new_record(kyo, title="widget", criteria=["a", "b"])
    verify_criterion(kyo, f"{record}.c1", "met")
    kyo("wont-do", new_record(kyo, title="dead"), "--why", "dead premise")
    (project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0
    doc = kyo("show", record, "--json").json
    assert doc["title"] == "widget"
    assert doc["criteria"][0]["state"] == "met"


def test_rebuild_round_trip_reproduces_the_export_byte_for_byte(kyo, project: Path) -> None:
    record = new_record(kyo, title="widget", criteria=["a", "b"])
    other = new_record(kyo, title="other")
    kyo("link", record, "blocks", other)
    kyo("gate", record, "holding-for", "--do", "the measurement finishes")
    verify_criterion(kyo, f"{record}.c1", "met")
    kyo(
        "claim", record, "--level", "asserted", "--method", "code read",
        "--file", "-", stdin="the widget explodes when the cache is cold",
    )
    kyo("attach", record, "--file", "-", stdin="console excerpt: TypeError at line 40")

    export_dir = project / "kyosei" / "export"
    before = {p.name: p.read_bytes() for p in export_dir.glob("*.json")}
    before_events = _events_path(project).read_bytes()

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0

    # `kyo rebuild` reads the export, it never writes it — so the files on disk are
    # untouched; the real assertion is that the *rebuilt db* still agrees with them
    # (kyo doctor's check_exports) and that rebuilding a second time from the same
    # export is self-consistent (check_export_roundtrip).
    assert kyo("doctor").code == 0
    after = {p.name: p.read_bytes() for p in export_dir.glob("*.json")}
    assert after == before
    assert _events_path(project).read_bytes() == before_events


def test_rebuild_reads_ids_from_allocations_never_max_seq(kyo, project: Path) -> None:
    """Law 4: a scope whose highest id was voided/deleted must not reissue it."""
    record = new_record(kyo, criteria=["a"])
    kyo(
        "edit", record, "--criterion", f"{record}.c1", "--state", "void-withdrawn",
        "--why", "not needed after all",
    )
    another = new_record(kyo, criteria=["b", "c"])
    kyo("wont-do", another, "--why", "dead")

    allocations = json.loads(_allocations_path(project).read_text(encoding="utf-8"))
    assert allocations["record"] > 2  # ahead of the highest issued record id

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    fresh = new_record(kyo, title="after rebuild")
    assert fresh == f"TST-{allocations['record']}"


def test_rebuild_without_an_export_directory_fails_loudly(tmp_path: Path) -> None:
    result = invoke(["--root", str(tmp_path / "nowhere"), "rebuild"])
    assert result.code != 0


# ------------------------------------------------------- KYO-268: the sharded ledger


def _history(kyo, project: Path, write_file) -> str:
    """A store with enough history that a rebuild has something to get wrong."""
    record = new_record(kyo, title="widget", criteria=["a", "b"])
    other = new_record(kyo, title="other")
    assert kyo("link", record, "blocks", other).code == 0
    assert kyo("gate", record, "holding-for", "--do", "the measurement finishes").code == 0
    verify_criterion(kyo, f"{record}.c1", "met")
    assert kyo(
        "claim", record, "--level", "asserted", "--method", "code read",
        "--file", write_file("cl.txt", "the widget explodes when the cache is cold"),
    ).code == 0
    assert kyo("attach", record, "--file", write_file("ev.txt", "console excerpt")).code == 0
    assert kyo("move", record, "working").code == 0
    return record


def _as_monolithic(export_dir: Path) -> Path:
    """Put an export back into the pre-KYO-268 layout, moving every line verbatim."""
    from kyosei.events_ledger import events_files_in

    text = "".join(p.read_text(encoding="utf-8") for p in events_files_in(export_dir))
    shards = export_dir / "_events"
    for shard in sorted(shards.iterdir()):
        shard.unlink()
    shards.rmdir()
    path = export_dir / "_events.jsonl"
    path.write_text(text, encoding="utf-8")
    return path


def _rebuilt_db(root: Path) -> bytes:
    (root / "kyosei" / "kyosei.db").unlink(missing_ok=True)
    result = invoke(["--root", str(root), "rebuild"])
    assert result.code == 0, result
    return (root / "kyosei" / "kyosei.db").read_bytes()


def test_a_rebuild_from_shards_matches_a_rebuild_from_the_monolithic_ledger(
    kyo, project: Path, write_file, tmp_path: Path
) -> None:
    """KYO-268.c2's other half, and the assertion that makes the migration safe to run.

    The two exports hold the same lines in the same order and differ only in which files
    those lines are in, so the databases they rebuild to must not differ at all —
    byte-for-byte, not merely equivalent. Anything less and `kyo export --migrate-events`
    would be a format change dressed as a file move.
    """
    import shutil

    _history(kyo, project, write_file)

    sharded = tmp_path / "sharded"
    monolithic = tmp_path / "monolithic"
    shutil.copytree(project, sharded)
    shutil.copytree(project, monolithic)
    old_file = _as_monolithic(monolithic / "kyosei" / "export")
    assert old_file.is_file()
    assert not (monolithic / "kyosei" / "export" / "_events").exists()
    assert (sharded / "kyosei" / "export" / "_events").is_dir()

    assert _rebuilt_db(sharded) == _rebuilt_db(monolithic)


def test_a_rebuild_reads_every_shard_in_order(kyo, project: Path, monkeypatch) -> None:
    """A ledger spread over several shards loads as one sequence. Ordering is not
    incidental here: the shard names are what sorts, so a reader that listed the
    directory in filesystem order would reassemble history at random."""
    from kyosei import events_ledger as events_ledger_module

    monkeypatch.setattr(events_ledger_module, "EVENTS_SHARD_MAX_BYTES", 2048)
    for i in range(12):
        assert kyo("new", "bug", "--title", f"record {i}", "--json").code == 0
    shards = sorted((project / "kyosei" / "export" / "_events").iterdir())
    assert len(shards) > 2, [p.name for p in shards]

    from kyosei.events_ledger import read_events_in

    events = read_events_in(project / "kyosei" / "export")
    assert [event["seq"] for event in events] == sorted(event["seq"] for event in events)
    assert kyo("rebuild", "--force").code == 0
    assert kyo("doctor").code == 0


def test_a_store_whose_export_is_still_monolithic_rebuilds_unchanged(
    kyo, project: Path, write_file
) -> None:
    """KYO-268.c3: accepting either layout means the OLD one keeps working, not merely
    that the new one does. This is every existing store's first run under this build."""
    _history(kyo, project, write_file)
    before = _rebuilt_db(project)
    _as_monolithic(project / "kyosei" / "export")
    assert _rebuilt_db(project) == before
    assert kyo("doctor").code == 0


# --------------------------------------------------------------------- KYO-268.c5


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    import os
    import subprocess

    full = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
            **(env or {})}
    return subprocess.run(
        ["git", *args], cwd=repo, env=full, check=True, capture_output=True, text=True
    ).stdout


def test_a_rebuild_survives_the_monolithic_path_being_dropped_from_history(
    kyo, project: Path, write_file, tmp_path: Path
) -> None:
    """KYO-268.c5: the disk already spent is recoverable, and recovering it loses nothing.

    The argument the README states, proved rather than asserted. The ledger is
    append-only and `kyo redact` only ever scrubs text out of existing lines, so every
    earlier committed version of ``_events.jsonl`` is the latest one minus later appends
    and minus later scrubs. Once the migration has written the shards and deleted that
    file, its whole history is redundant — and dropping it removes the un-scrubbed copies
    a redaction could never reach.

    Run on a fixture repository this test creates, and nowhere else: this is a real
    ``git filter-branch``, the thing the README says is the maintainer's call and rewrites
    every clone. ``filter-branch`` rather than ``filter-repo`` because it ships with git
    and this assertion must not depend on what is installed; the README names
    ``filter-repo``, which is the right tool for a real repository and does the same thing
    to the same path.
    """
    import shutil

    _history(kyo, project, write_file)
    repo = tmp_path / "fixture-repo"
    shutil.copytree(project, repo)
    shutil.rmtree(repo / ".git", ignore_errors=True)
    (repo / "kyosei" / "kyosei.db").unlink()
    _as_monolithic(repo / "kyosei" / "export")

    _git(repo, "init", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "store with a monolithic ledger")
    _git(repo, "log", "--oneline", "--", "kyosei/export/_events.jsonl")

    # The deliberate act, in the order the README states it: migrate FIRST, then rewrite.
    assert invoke(["--root", str(repo), "export", "--migrate-events"]).code == 0
    assert not (repo / "kyosei" / "export" / "_events.jsonl").exists()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "migrate the events ledger to shards")

    expected = _rebuilt_db(repo)

    _git(
        repo, "filter-branch", "--force", "--prune-empty", "--index-filter",
        "git rm --cached --ignore-unmatch kyosei/export/_events.jsonl",
        "--", "--all",
        env={"FILTER_BRANCH_SQUELCH_WARNING": "1"},
    )
    # `filter-branch` keeps the pre-rewrite tips under `refs/original/`, so the path is
    # still reachable until those are dropped — the step a real rewrite also has to take,
    # and the one that makes "no longer in history" true rather than nearly true.
    for ref in _git(repo, "for-each-ref", "--format=%(refname)", "refs/original/").split():
        _git(repo, "update-ref", "-d", ref)
    _git(repo, "reflog", "expire", "--expire=now", "--all")
    remaining = _git(repo, "log", "--all", "--oneline", "--", "kyosei/export/_events.jsonl")
    assert remaining.strip() == "", (
        "the path is still in this repository's history, so the rewrite this test is "
        f"about did not happen:\n{remaining}"
    )

    assert _rebuilt_db(repo) == expected
    assert invoke(["--root", str(repo), "doctor"]).code == 0
