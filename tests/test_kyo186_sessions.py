"""KYO-186 stage 1: the session file the desktop app's claude.ai pane reads.

Every test here is written so that it fails if the mechanism it covers is
*removed*, which is this project's standing rule for a mechanism and the reason
several of these assert on a NAMED OUTCOME rather than on a falsy value. A reader
tempted to relax one of those into ``assert lookup.bridge_session_id is None``
should note that the whole point of the outcome vocabulary is that "this session
is not on Remote Control" and "the harness's transcript format changed under us"
are different facts that a None cannot tell apart.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kyosei import cmd_sessions, install_hooks, scaffold
from kyosei.cmd_sessions import (
    BRIDGE_ABSENT,
    BRIDGE_ID_PREFIX,
    BRIDGE_IN_HEAD,
    BRIDGE_IN_TAIL,
    BRIDGE_MALFORMED,
    BRIDGE_NO_TRANSCRIPT,
    BRIDGE_UNREADABLE,
    EVENT_END,
    EVENT_START,
    EVENT_STOP,
    REMOTE_INDEX_URL,
    SESSIONS_FILE,
    SESSIONS_SCHEMA,
    STATE_ENDED,
    STATE_RUNNING,
    BridgeLookup,
    apply_event,
    find_bridge_session,
    read_sessions,
    record_event,
    remote_url,
    resolve_pane_url,
    sessions_path,
    write_sessions,
)
from kyosei.config import SESSIONS_NAME

ULID = "01AbCdEfGhJkMnPqRsTvWxYz"
BRIDGE_ID = f"{BRIDGE_ID_PREFIX}{ULID}"


def bridge_record(bridge_id: str | None = BRIDGE_ID, session: str = "local-uuid") -> str:
    """One transcript record shaped exactly as the harness writes it.

    The two owner uuids are present ON PURPOSE. They are what a real record
    carries, and their presence here is what makes
    :func:`test_no_account_or_organization_identifier_is_ever_stored` a real check
    rather than a check against a fixture that never had them.
    """
    record: dict = {
        "type": "bridge-session",
        "sessionId": session,
        "lastSequenceNum": 0,
        "ownerAccountUuid": "acc0acc0-0000-4000-8000-000000000001",
        "ownerOrganizationUuid": "0a90a90a-0000-4000-8000-000000000002",
    }
    if bridge_id is not None:
        record["bridgeSessionId"] = bridge_id
    return json.dumps(record)


def noise(count: int) -> list[str]:
    return [json.dumps({"type": "user", "message": {"content": "x" * 200}}) for _ in range(count)]


def transcript(tmp_path: Path, lines: list[str], name: str = "t.jsonl") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------- the URL swap


def test_the_url_is_the_same_ulid_under_a_different_prefix() -> None:
    """The coupling, pinned. ``cse_<ULID>`` in a transcript is
    ``https://claude.ai/code/session_<ULID>`` on claude.ai — verified against a real
    session, and independently against Claude Code's own ``Claude-Session:`` commit
    trailer, which names that URL for the session whose transcript carries that id."""
    assert remote_url(BRIDGE_ID) == f"https://claude.ai/code/session_{ULID}"


def test_an_id_without_the_known_prefix_yields_no_url_rather_than_a_guessed_one() -> None:
    """The degradation this project's rule demands, at its smallest.

    A build that stripped whatever prefix it found and pasted the rest into a URL
    would send a reader confidently to the wrong page — or to a 404 that looks like
    the feature is broken rather than like the format moved."""
    assert remote_url("session_" + ULID) == ""
    assert remote_url(ULID) == ""
    assert remote_url(BRIDGE_ID_PREFIX) == ""


# ------------------------------------------------- the reach into the transcript


def test_a_record_in_the_opening_lines_is_found(tmp_path: Path) -> None:
    """The ordinary case, and the one that makes ``SessionStart`` able to write a
    URL at all: measured on this project's own transcripts, the first copy of the
    record stood at line 2 to 5 in every one that had any."""
    path = transcript(tmp_path, [*noise(1), bridge_record(), *noise(50)])
    lookup = find_bridge_session(str(path))
    assert lookup.outcome == BRIDGE_IN_HEAD
    assert lookup.bridge_session_id == BRIDGE_ID
    assert lookup.url == f"https://claude.ai/code/session_{ULID}"
    assert str(path) in lookup.why


def test_a_session_that_connects_to_remote_control_later_is_found_in_the_tail(
    tmp_path: Path,
) -> None:
    """KYO-186's stated reason for putting this on ``Stop`` at all.

    A transcript is append-only, so a late connection puts its first
    ``bridge-session`` record after everything already written. If the reader only
    looked at the head, the ``Stop`` refresh would re-read the same opening lines
    every turn and learn nothing — the refresh would be decorative and this case
    would never resolve. Delete the tail read and this test fails."""
    path = transcript(
        tmp_path,
        [*noise(cmd_sessions.TRANSCRIPT_WINDOW_LINES + 40), bridge_record(), *noise(3)],
    )
    lookup = find_bridge_session(str(path))
    assert lookup.outcome == BRIDGE_IN_TAIL
    assert lookup.bridge_session_id == BRIDGE_ID
    assert lookup.found


def test_a_transcript_with_no_record_is_absent_and_says_so(tmp_path: Path) -> None:
    """A real answer, not a failure: two of the thirteen transcripts measured on
    this project were in exactly this state."""
    path = transcript(tmp_path, noise(20))
    lookup = find_bridge_session(str(path))
    assert lookup.outcome == BRIDGE_ABSENT
    assert lookup.bridge_session_id is None
    assert not lookup.found


def test_a_record_whose_id_this_build_cannot_use_is_malformed_not_absent(
    tmp_path: Path,
) -> None:
    """The outcome that must never be folded into "not connected".

    The two have the same consequence for the pane and completely different causes:
    one is a fact about the session, the other is the harness's undocumented format
    having changed under us — which is the event the whole named-outcome discipline
    exists to make loud. Collapse this into ``absent`` and this test fails."""
    for bad in (bridge_record(bridge_id=None), bridge_record(bridge_id="brs_" + ULID)):
        path = transcript(tmp_path, [bad], name=f"{abs(hash(bad))}.jsonl")
        lookup = find_bridge_session(str(path))
        assert lookup.outcome == BRIDGE_MALFORMED, bad
        assert lookup.bridge_session_id is None
        assert "changed" in lookup.why


def test_a_missing_or_unnamed_transcript_each_get_their_own_outcome(tmp_path: Path) -> None:
    assert find_bridge_session(None).outcome == BRIDGE_NO_TRANSCRIPT
    assert find_bridge_session("").outcome == BRIDGE_NO_TRANSCRIPT
    assert find_bridge_session(str(tmp_path / "nope.jsonl")).outcome == BRIDGE_UNREADABLE


def test_a_line_that_is_not_json_does_not_stop_the_read(tmp_path: Path) -> None:
    """A truncated or half-flushed line is ordinary in a file something else is
    appending to. It must cost that line and nothing else."""
    path = transcript(tmp_path, ['{"type": "bridge-sess', bridge_record()])
    assert find_bridge_session(str(path)).bridge_session_id == BRIDGE_ID


def test_the_head_read_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """This runs from the ``Stop`` hook on every turn, and transcripts reach
    hundreds of megabytes. An unbounded read would be a cost paid per turn forever;
    ``cmd_dispatch``'s own reader caps for the same reason."""
    read_lines: list[int] = []
    original = cmd_sessions._head_lines

    def counting(path: Path, limit: int) -> list[str]:
        read_lines.append(limit)
        return original(path, limit)

    monkeypatch.setattr(cmd_sessions, "_head_lines", counting)
    path = transcript(tmp_path, [bridge_record(), *noise(50)])
    find_bridge_session(str(path))
    assert read_lines == [cmd_sessions.TRANSCRIPT_WINDOW_LINES]
    assert cmd_sessions.TRANSCRIPT_WINDOW_LINES < 10_000


# ------------------------------------------------------------------ the file


def test_the_file_sits_beside_the_database(tmp_path: Path) -> None:
    assert sessions_path(tmp_path) == tmp_path / "kyosei" / SESSIONS_NAME
    assert sessions_path(tmp_path).parent == (tmp_path / "kyosei")


def test_an_absent_file_is_not_a_problem_and_a_broken_one_is(tmp_path: Path) -> None:
    """Both are "no sessions", and the difference between them is the whole point:
    one is the ordinary state of a project no session hook has run in, the other is
    a fact somebody should see. Neither may raise, and neither may invent an
    entry."""
    path = sessions_path(tmp_path)
    absent = read_sessions(path)
    assert absent.entries == [] and absent.problem is None and absent.present is False

    path.parent.mkdir(parents=True)
    path.write_text("{ this is not json", encoding="utf-8")
    broken = read_sessions(path)
    assert broken.entries == [] and broken.present is True
    assert broken.problem is not None and "JSON" in broken.problem


def test_a_document_from_a_future_schema_is_refused_rather_than_mis_parsed(
    tmp_path: Path,
) -> None:
    path = sessions_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema": SESSIONS_SCHEMA + 1, "sessions": [{}]}), encoding="utf-8")
    read = read_sessions(path)
    assert read.entries == []
    assert read.problem is not None and str(SESSIONS_SCHEMA) in read.problem


def test_the_file_is_capped_and_written_newest_first(tmp_path: Path) -> None:
    path = sessions_path(tmp_path)
    entries = [
        {"session_id": f"s{i:03d}", "last_seen": f"2026-01-01T00:00:{i:02d}.000Z"}
        for i in range(cmd_sessions.MAX_SESSIONS + 10)
    ]
    assert write_sessions(path, entries) is None
    document = json.loads(path.read_text(encoding="utf-8"))
    kept = document["sessions"]
    assert len(kept) == cmd_sessions.MAX_SESSIONS
    assert [e["last_seen"] for e in kept] == sorted(
        (e["last_seen"] for e in kept), reverse=True
    )
    # The newest survived the cap and the oldest did not: a cap that dropped the
    # newest would be worse than no cap at all.
    assert kept[0]["session_id"] == f"s{cmd_sessions.MAX_SESSIONS + 9:03d}"


# ------------------------------------------------------------ the three events


def head_lookup() -> BridgeLookup:
    return BridgeLookup(BRIDGE_ID, BRIDGE_IN_HEAD, "found")


def test_session_end_marks_its_own_entry_ended(tmp_path: Path) -> None:
    """KYO-186.c7, and the half the rejected design could not do at all.

    Nothing holds a transcript open and a running ``claude`` does not name its
    session on its command line, so a scan of ``~/.claude`` cannot tell a running
    session from a finished one. A file that only ever grows is a file that says
    everything is running."""
    entries, _ = apply_event(
        [],
        session_id="s1",
        event=EVENT_START,
        cwd="/p",
        lookup=head_lookup(),
        stamp="2026-01-01T00:00:00.000Z",
    )
    assert entries[0]["state"] == STATE_RUNNING
    assert entries[0]["ended_at"] is None

    ended, entry = apply_event(
        entries,
        session_id="s1",
        event=EVENT_END,
        cwd="/p",
        lookup=head_lookup(),
        stamp="2026-01-01T00:05:00.000Z",
    )
    assert len(ended) == 1, "an end marks the entry, it does not add a second one"
    assert entry["state"] == STATE_ENDED
    assert entry["ended_at"] == "2026-01-01T00:05:00.000Z"
    assert entry["first_seen"] == "2026-01-01T00:00:00.000Z", "the start is not forgotten"


def test_an_end_that_cannot_read_the_transcript_keeps_the_url_it_already_had(
    tmp_path: Path,
) -> None:
    """``SessionEnd`` can arrive after a transcript has been rotated or moved. An
    end that blanked the URL would take the pane's target away at exactly the moment
    the entry became the most interesting one in the file."""
    entries, _ = apply_event(
        [],
        session_id="s1",
        event=EVENT_START,
        cwd="/p",
        lookup=head_lookup(),
        stamp="2026-01-01T00:00:00.000Z",
    )
    _, entry = apply_event(
        entries,
        session_id="s1",
        event=EVENT_END,
        cwd="/p",
        lookup=BridgeLookup(None, BRIDGE_UNREADABLE, "gone"),
        stamp="2026-01-01T00:05:00.000Z",
    )
    assert entry["url"] == f"https://claude.ai/code/session_{ULID}"
    assert entry["state"] == STATE_ENDED
    assert "kept from an earlier event" in entry["remote_control_why"]


def test_stop_refreshes_an_entry_that_had_no_url(tmp_path: Path) -> None:
    """The other half of why ``Stop`` is in the list: a session that was not on
    Remote Control when it started, and joined later."""
    entries, first = apply_event(
        [],
        session_id="s1",
        event=EVENT_START,
        cwd="/p",
        lookup=BridgeLookup(None, BRIDGE_ABSENT, "not connected"),
        stamp="2026-01-01T00:00:00.000Z",
    )
    assert first["url"] is None
    _, later = apply_event(
        entries,
        session_id="s1",
        event=EVENT_STOP,
        cwd="/p",
        lookup=BridgeLookup(BRIDGE_ID, BRIDGE_IN_TAIL, "found late"),
        stamp="2026-01-01T00:01:00.000Z",
    )
    assert later["url"] == f"https://claude.ai/code/session_{ULID}"
    assert later["remote_control_outcome"] == BRIDGE_IN_TAIL


def test_no_account_or_organization_identifier_is_ever_stored(tmp_path: Path) -> None:
    """KYO-186.c5, met by never reading the fields at all.

    c5 says no account or organization identifier may reach ``kyosei/export/``.
    This is stronger and cheaper to keep true: the fields are not copied out of the
    transcript record, so there is nothing anywhere in this process to leak. The
    fixture record carries both, so this fails the moment somebody widens the read
    to "keep the whole record"."""
    path = transcript(tmp_path, [bridge_record()])
    result = record_event(
        tmp_path,
        session_id="s1",
        event=EVENT_START,
        cwd="/p",
        transcript_path=str(path),
    )
    written = sessions_path(tmp_path).read_text(encoding="utf-8")
    for forbidden in ("ownerAccountUuid", "ownerOrganizationUuid", "acc0acc0", "0a90a90a"):
        assert forbidden not in written, forbidden
        assert forbidden not in json.dumps(result), forbidden


def test_the_events_this_build_records_are_exactly_the_three_it_installs() -> None:
    """The fragment and the command cannot drift about which events are handled.

    An event installed but unhandled would write nothing and say so at every fire;
    an event handled but not installed is a mechanism with no trigger."""
    installed = {
        event for event, source in install_hooks.HOOK_INSTALL_TARGETS if source == "sessions.json"
    }
    assert installed == set(cmd_sessions.SESSION_EVENTS)
    assert installed == {EVENT_START, EVENT_STOP, EVENT_END}


# -------------------------------------------------------------- the resolution


def entry(session: str, *, state: str, seen: str, url: str | None) -> dict:
    return {
        "session_id": session,
        "state": state,
        "last_seen": seen,
        "ended_at": seen if state == STATE_ENDED else None,
        "url": url,
    }


def test_a_running_session_outranks_an_ended_one_however_recent() -> None:
    file = cmd_sessions.SessionsFile(
        [
            entry("old", state=STATE_RUNNING, seen="2026-01-01T00:00:00.000Z", url="u-running"),
            entry("new", state=STATE_ENDED, seen="2026-01-02T00:00:00.000Z", url="u-ended"),
        ],
        None,
        True,
    )
    url, why = resolve_pane_url(file)
    assert url == "u-running"
    assert "running" in why


def test_the_most_recent_running_session_wins_among_several() -> None:
    file = cmd_sessions.SessionsFile(
        [
            entry("a", state=STATE_RUNNING, seen="2026-01-01T00:00:00.000Z", url="u-a"),
            entry("b", state=STATE_RUNNING, seen="2026-01-03T00:00:00.000Z", url="u-b"),
        ],
        None,
        True,
    )
    assert resolve_pane_url(file)[0] == "u-b"


def test_every_way_the_file_can_fail_falls_through_to_the_account_session_list() -> None:
    """Step 3 of KYO-186's resolution order, which the record is explicit is not a
    consolation prize: on a machine where Claude Code runs somewhere else it is what
    the feature IS. Each of these reaches it for a *different* stated reason, and
    the reason is carried so a reader can tell them apart."""
    cases = {
        "never written": cmd_sessions.SessionsFile([], None, False),
        "unreadable": cmd_sessions.SessionsFile([], "it is not JSON", True),
        "no session has a page": cmd_sessions.SessionsFile(
            [entry("a", state=STATE_RUNNING, seen="2026-01-01T00:00:00.000Z", url=None)],
            None,
            True,
        ),
    }
    whys = set()
    for label, file in cases.items():
        url, why = resolve_pane_url(file)
        assert url == REMOTE_INDEX_URL, label
        assert why, label
        whys.add(why)
    assert len(whys) == len(cases), "three different reasons, not one message three times"


# ---------------------------------------------------- the gitignore, and the trap


def test_the_scaffold_template_ignores_the_session_file_beside_the_database() -> None:
    """KYO-186.c8's template half, and KYO-151 met for the third time.

    A worker cannot edit ``kyosei/.gitignore`` — that file is inside the store, which
    `kyo check-branch` forbids a dispatch branch to touch. What a worker CAN change
    is the template `kyo init` writes, which is outside ``kyosei/`` and is where the
    ignore has to be shipped from anyway. THIS repository's own ``kyosei/.gitignore``
    predates the change and is the controller's to update after the merge, exactly as
    the export regeneration is."""
    lines = scaffold.KYOSEI_GITIGNORE.splitlines()
    assert SESSIONS_NAME in lines
    assert "kyosei.db" in lines, "beside the database, on the same side of the line"


def test_the_session_file_is_never_written_under_the_export_directory(tmp_path: Path) -> None:
    """KYO-186.c5's other half, and the trap the brief names as most likely to be
    skipped because nothing fails when you skip it. ``kyosei/export/`` is
    git-canonical and this project intends to publish it."""
    path = transcript(tmp_path, [bridge_record()])
    record_event(
        tmp_path, session_id="s1", event=EVENT_START, cwd="/p", transcript_path=str(path)
    )
    written = sorted(p for p in (tmp_path / "kyosei").rglob("*") if p.is_file())
    assert written == [sessions_path(tmp_path)]
    assert not (tmp_path / "kyosei" / "export").exists()


def test_the_guarded_directories_already_exclude_the_file_from_a_workers_stop() -> None:
    """The gitignore is hygiene; THIS is why a worker stops green regardless.

    ``kyo check-clean`` excludes every path under :data:`GUARDED_BRANCH_DIRS` from
    what it counts as a worker's uncommitted work, and the session file is written
    under ``kyosei/``. So an untracked ``kyosei/sessions.json`` in a worktree cannot
    dirty a stop even in a checkout whose ``kyosei/.gitignore`` predates this change
    — and an untracked file cannot make a branch diff, so `kyo check-branch` is
    unaffected too. Stated as a test because the brief for this work asserted the
    opposite, and a reader deciding whether the ignore is load-bearing for c8 needs
    the real answer."""
    from kyosei.contracts import GUARDED_BRANCH_DIRS

    relative = f"kyosei/{SESSIONS_NAME}"
    assert any(relative.startswith(guarded) for guarded in GUARDED_BRANCH_DIRS)
    assert SESSIONS_FILE == SESSIONS_NAME


# ------------------------------------------------------------ KYO-390: the process


_SESSION_PROBE = (
    "from kyosei.cmd_sessions import session_process; "
    "p = session_process(); "
    "print(p.pid if p else 'none', p.name if p else '', p.host if p else '')"
)


def _two_shells_deep(python: str) -> list[str]:
    """The probe run by ``python`` under two nested shells, every path quoted (KYO-437):
    a dispatch worktree's path holds a space, and an unquoted one split the command."""
    import shlex

    inner = f"{shlex.quote(python)} -c {shlex.quote(_SESSION_PROBE)}"
    return ["/bin/sh", "-c", f"/bin/sh -c {shlex.quote(inner)}; true"]


def test_the_probe_command_survives_a_python_path_with_a_space(tmp_path) -> None:
    """KYO-437: the controller's suite runs from a checkout with no space in its path, so
    the regression is pinned here, where the path has one whatever the checkout's is."""
    import os
    import subprocess
    import sys

    spaced = tmp_path / "dispatch worktrees" / "python"
    spaced.parent.mkdir()
    spaced.symlink_to(sys.executable)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    run = subprocess.run(
        _two_shells_deep(str(spaced)), capture_output=True, text=True, check=True, env=env
    )
    assert len(run.stdout.split()) == 3, run.stdout + run.stderr


def test_the_session_process_is_the_nearest_ancestor_that_is_not_a_shell() -> None:
    """KYO-390. A hook runs ``kyo`` through a shell, and ``kyo`` may be a ``#!/bin/sh``
    shim, so the session is found by skipping shells rather than by counting levels.
    Here this test's own process plays the session: two shells sit between it and
    the ``kyo`` that walks, and the walk must land on it."""
    import os
    import subprocess
    import sys

    run = subprocess.run(
        _two_shells_deep(sys.executable), capture_output=True, text=True, check=True
    )
    pid, name, host = run.stdout.split()
    assert int(pid) == os.getpid()
    assert name not in cmd_sessions.HOOK_SHELLS
    assert host == __import__("socket").gethostname()


def test_a_walk_through_nothing_but_shells_records_no_process() -> None:
    """``None``, never a shell's pid: a reader treats an entry without a process as
    uncheckable, and a shell's pid would read as a live session for as long as that
    shell happened to live."""
    assert cmd_sessions.session_process(start=1) is None


def test_the_process_is_stored_and_kept_by_an_event_that_could_not_find_it() -> None:
    """KYO-390: pid, process name and host land in the entry, and a later event whose
    walk found nothing keeps them, as the URL is kept."""
    process = cmd_sessions.SessionProcess(pid=4242, name="claude", host="workstation")
    entries, first = apply_event(
        [],
        session_id="s1",
        event=EVENT_START,
        cwd="/p",
        lookup=head_lookup(),
        stamp="2026-01-01T00:00:00.000Z",
        process=process,
    )
    assert (first["pid"], first["process"], first["host"]) == (4242, "claude", "workstation")
    _, later = apply_event(
        entries,
        session_id="s1",
        event=EVENT_STOP,
        cwd="/p/sub",
        lookup=head_lookup(),
        stamp="2026-01-01T00:01:00.000Z",
        process=None,
    )
    assert (later["pid"], later["process"], later["host"]) == (4242, "claude", "workstation")


def test_the_listing_names_each_sessions_process(project: Path, kyo) -> None:
    """The stored fields' command-line consumer: ``kyo sessions`` prints them."""
    write_sessions(
        sessions_path(project),
        [
            {
                "session_id": "s1",
                "state": STATE_RUNNING,
                "last_seen": "x",
                "last_event": EVENT_STOP,
                "cwd": str(project),
                "url": None,
                "remote_control_why": "none",
                "pid": 4242,
                "process": "claude",
                "host": "workstation",
            }
        ],
    )
    run = kyo("sessions")
    assert run.code == 0, run
    assert "process 4242 (claude) on workstation" in run.out, run.out
