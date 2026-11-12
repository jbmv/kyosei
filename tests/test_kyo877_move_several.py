"""KYO-877: `kyo move` takes several records as one act with one commit.

KYO-878's tests, on `kyo discuss close --produced`, are in tests/test_kyo658_discussions.py,
which holds the discussion fixtures."""

from __future__ import annotations

from pathlib import Path

from .conftest import new_record
from .test_kyo226_version_and_changelog import (
    _changelog_project,
    _clean_outside_store,
    _complete_status,
    _finished,
    _git,
    _status_of,
    _store_commit,
)


def test_kyo877_c1_c2_three_records_move_in_one_invocation_and_one_commit(
    kyo, project: Path, tmp_path: Path
) -> None:
    _changelog_project(project, tmp_path)
    ids = [_finished(kyo, f"Record {n}") for n in range(3)]
    _store_commit(project)
    head = _git(project, "rev-parse", "HEAD")
    done = _complete_status(project)

    moved = kyo("move", *ids, done, "--json")

    assert moved.code == 0, moved
    assert [_status_of(kyo, rid) for rid in ids] == [done] * 3
    assert moved.json["moved"] == ids and moved.json["refused"] == []
    assert moved.json["refresh"]["status"] == "committed", moved.json["refresh"]
    assert _git(project, "rev-parse", "HEAD^") == head  # one commit, none per record
    subject = _git(project, "log", "-1", "--format=%s")
    assert all(rid in subject for rid in ids), subject
    changed = _git(project, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert all(f"kyosei/export/{rid}.json" in changed for rid in ids)
    assert _clean_outside_store(project) == ""


def test_kyo877_c3_a_refused_record_is_left_named_and_the_exit_status_says_so(
    kyo, project: Path, tmp_path: Path
) -> None:
    _changelog_project(project, tmp_path)
    first = _finished(kyo, "Ready one")
    blocked = new_record(kyo, title="Not ready", criteria=["nobody checked this"])
    second = _finished(kyo, "Ready two")
    _store_commit(project)
    head = _git(project, "rev-parse", "HEAD")
    done = _complete_status(project)
    before = _status_of(kyo, blocked)

    moved = kyo("move", first, blocked, second, done)

    assert moved.code == 1, moved
    assert _status_of(kyo, first) == done and _status_of(kyo, second) == done
    assert _status_of(kyo, blocked) == before  # left as it was
    assert f"refused {blocked}" in moved.err and f"{blocked}.c1" in moved.err
    assert "2 of 3 records moved" in moved.err
    assert _git(project, "rev-parse", "HEAD^") == head  # the two that moved, one commit
    subject = _git(project, "log", "-1", "--format=%s")
    assert first in subject and second in subject and blocked not in subject


def test_kyo877_one_id_is_unchanged_and_a_status_alone_is_a_usage_error(kyo) -> None:
    record = new_record(kyo, title="Lone", criteria=["it works"])
    one = kyo("move", record, "working")
    assert one.code == 0 and f"moved {record}" in one.out
    bare = kyo("move", "working")
    assert bare.code == 64 and "at least one record" in bare.err
    unknown = kyo("move", record, "no-such-status")
    assert unknown.code == 64
