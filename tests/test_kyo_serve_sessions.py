"""KYO-689: `kyo serve`'s session routes, the read half of chunk E4.

The transcript samples are the recorded ones under ``tests/fixtures/session_layer/``. A
fake ``tmux`` script on the server's ``PATH`` answers ``list-panes`` with one pane and
``capture-pane`` with a fixture screen, and logs every call to a file: that log is what the
one-sampler test counts. Every request goes over a real socket, every server is stopped by
the fixture that started it, and every stream is read under a socket timeout, so a missing
event fails a test rather than hanging it.
"""

from __future__ import annotations

import ast
import http.client
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

import kyosei
from kyosei import cmd_sessions, serve, serve_sessions, session_layer

from .conftest import git_init, new_record, opt_in_transcripts
from .test_kyo_serve import Client, body_json, register, start, stop

FIXTURES = Path(__file__).parent / "fixtures" / "session_layer"
SCREENS = FIXTURES / "screens"
OLD = "108d6543-12e9-4ae4-85fb-f303424bfa6a"
NEW = "718208ce-6423-4004-9771-10cfb6d615e8"
OLD_FILE = FIXTURES / "pair" / f"{OLD}.jsonl"
NEW_FILE = FIXTURES / "pair" / f"{NEW}.jsonl"
FACTS_FILE = FIXTURES / "facts" / "session.synthetic.jsonl"
CATALOG = FIXTURES / "facts" / "model-catalog-cc.synthetic.json"
PERMISSION_MODE_FILE = FIXTURES / "permission-mode.synthetic.jsonl"
DELIVERED_FILE = FIXTURES / "delivered" / "session.synthetic.jsonl"
ENDED = "22222222-2222-4222-8222-222222222222"
KILLED = "33333333-3333-4333-8333-333333333333"
ELSEWHERE = "44444444-4444-4444-8444-444444444444"
SIGNATURE_ONLY = "c2lnbmF0dXJlLW9ubHk="

BASE = "/v1/projects/proj"
SESSIONS = f"{BASE}/sessions"

ROUTES = [
    SESSIONS,
    f"{SESSIONS}/{OLD}/turns",
    f"{SESSIONS}/{OLD}/screen",
    f"{SESSIONS}/{OLD}/events",
    f"{SESSIONS}/{OLD}/changes",
]


class Bed:
    """A served project with a recorded session, a fake tmux and a fake Claude Code home."""

    def __init__(self, project: Path, tmp_path: Path, client: Client) -> None:
        self.project = project
        self.client = client
        self.server: serve.KyoServer = client.server
        self.claude = tmp_path / "claude"
        self.tmux_log = tmp_path / "tmux.log"
        self.screen_file = tmp_path / "screen.txt"
        self.transcripts = self.claude / "projects" / "-work"
        self.transcript = self.transcripts / f"{OLD}.jsonl"

    def pane_reads(self) -> int:
        if not self.tmux_log.exists():
            return 0
        return sum("capture-pane" in line for line in self.tmux_log.read_text().splitlines())

    def show(self, name: str) -> None:
        self.screen_file.write_text((SCREENS / name).read_text(encoding="utf-8"), encoding="utf-8")

    def show_frame(self, name: str) -> None:
        text = (FIXTURES / "sequence" / name).read_text(encoding="utf-8")
        self.screen_file.write_text(text, encoding="utf-8")

    def rows(self, count: int | None = None, source: Path = OLD_FILE) -> list[str]:
        lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
        return lines if count is None else lines[:count]

    def write_transcript(self, lines: list[str]) -> None:
        self.transcript.write_text("".join(lines), encoding="utf-8")

    def append(self, lines: list[str]) -> None:
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.writelines(lines)

    def get(self, path: str, **kwargs):
        return self.client.get(path, **kwargs)


def _entry(
    session_id: str,
    *,
    state: str,
    seen: str,
    transcript: Path | None = None,
    cwd: str = "/work",
) -> dict:
    return {
        "session_id": session_id,
        "state": state,
        "first_seen": seen,
        "last_seen": seen,
        "last_event": "Stop",
        "cwd": cwd,
        "remote_control_id": None,
        "url": None,
        "remote_control_outcome": "absent",
        "remote_control_why": "no record",
        "ended_at": seen if state == cmd_sessions.STATE_ENDED else None,
        "pid": None,
        "process": None,
        "host": None,
        "transcript_path": str(transcript) if transcript else None,
    }


@pytest.fixture
def running():
    started: list[Client] = []

    def _start(**kwargs) -> Client:
        client = start(**kwargs)
        started.append(client)
        return client

    yield _start
    for client in started:
        stop(client)


@pytest.fixture
def bed(project: Path, store_home: Path, tmp_path: Path, running) -> Bed:
    """An opted-in project with one live session in a (fake) pane."""
    opt_in_transcripts(project)
    register(store_home, project)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "tmux"
    pane_row = f"%%1\\t{os.getpid()}\\tfake\\n"
    fake.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{tmp_path / "tmux.log"}"\n'
        'case "$*" in\n'
        f'  *list-panes*) printf "{pane_row}" ;;\n'
        f'  *capture-pane*) cat "{tmp_path / "screen.txt"}" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    claude = tmp_path / "claude"
    (claude / "sessions").mkdir(parents=True)
    (claude / "sessions" / f"{os.getpid()}.json").write_text(
        json.dumps({"pid": os.getpid(), "sessionId": OLD, "status": "busy"}), encoding="utf-8"
    )
    (claude / "projects" / "-work").mkdir(parents=True)
    environ = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "CLAUDE_CONFIG_DIR": str(claude),
    }
    client = running(environ=environ)
    client.server.session_sample_seconds = 0.03
    client.server.keepalive_seconds = 0.4
    made = Bed(project, tmp_path, client)
    made.write_transcript(made.rows())
    made.show("idle.txt")
    entries = [
        _entry(OLD, state="running", seen="2026-11-09T10:00:00.000Z"),
        _entry(ENDED, state="ended", seen="2026-11-09T11:00:00.000Z"),
        _entry(KILLED, state="running", seen="2026-11-09T09:00:00.000Z"),
    ]
    assert cmd_sessions.write_sessions(cmd_sessions.sessions_path(project), entries) is None
    return made


class Stream:
    """One open session event stream, read line by line under a socket timeout."""

    def __init__(self, bed: Bed, session_id: str = OLD, *, query: str = "", **headers: str) -> None:
        self.conn = http.client.HTTPConnection("127.0.0.1", bed.client.port, timeout=15)
        self.conn.putrequest("GET", f"{SESSIONS}/{session_id}/events{query}", skip_host=True)
        self.conn.putheader("Host", f"127.0.0.1:{bed.client.port}")
        for key, value in headers.items():
            self.conn.putheader(key.replace("_", "-"), value)
        self.conn.endheaders()
        self.response = self.conn.getresponse()

    def block(self) -> list[str]:
        lines: list[str] = []
        while True:
            line = self.response.fp.readline()
            if line == b"":
                raise EOFError("the stream ended")
            text = line.decode("utf-8").rstrip("\n")
            if text == "":
                if lines:
                    return lines
                continue
            lines.append(text)

    def event(self, name: str, *, within: float = 10.0) -> dict:
        """The next event called ``name``, skipping keepalives and other events."""
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            block = self.block()
            names = [line for line in block if line.startswith("event: ")]
            if names == [f"event: {name}"]:
                data = next(line for line in block if line.startswith("data: "))
                return json.loads(data.removeprefix("data: "))
        raise TimeoutError(f"no {name!r} event arrived")

    def close(self) -> None:
        self.response.close()  # http.client hands the socket to the response on HTTP/1.0
        self.conn.close()


@pytest.fixture
def stream(bed: Bed):
    opened: list[Stream] = []

    def _open(session_id: str = OLD, *, query: str = "", **headers: str) -> Stream:
        made = Stream(bed, session_id, query=query, **headers)
        opened.append(made)
        assert made.response.status == 200
        assert made.response.headers["Content-Type"].startswith("text/event-stream")
        assert made.block() == ["retry: 1000", ": kyo serve session events"]
        return made

    yield _open
    for made in opened:
        made.close()


def json_body(status_headers_body) -> dict:
    status, _, body = status_headers_body
    assert status == 200, body
    return body_json(body)


# ------------------------------------------------------------------- c1: the session list


def test_the_list_names_each_session_with_its_pane_and_whether_work_runs(bed: Bed) -> None:
    document = json_body(bed.get(SESSIONS))
    rows = {row["session_id"]: row for row in document["sessions"]}

    live = rows[OLD]
    assert live["state"] == "running" and live["live"] is True
    assert live["has_pane"] is True and live["work_running"] is False
    assert live["screen_state"] == "idle"

    ended = rows[ENDED]
    assert ended["state"] == "ended" and ended["live"] is False
    assert ended["has_pane"] is False and ended["work_running"] is False


def test_the_list_reports_work_running_from_the_screen(bed: Bed) -> None:
    bed.show_frame("frame-05.txt")
    row = next(r for r in json_body(bed.get(SESSIONS))["sessions"] if r["session_id"] == OLD)
    assert row["work_running"] is True and row["screen_state"] == "working"


def test_the_list_reports_background_work_on_an_idle_screen(bed: Bed) -> None:
    bed.show("background-shells.txt")
    row = next(r for r in json_body(bed.get(SESSIONS))["sessions"] if r["session_id"] == OLD)
    assert row["work_running"] is True and row["background"]["shells"] > 0


def test_a_session_marked_running_whose_process_is_gone_is_not_live(bed: Bed) -> None:
    rows = {r["session_id"]: r for r in json_body(bed.get(SESSIONS))["sessions"]}
    assert rows[KILLED]["state"] == "running" and rows[KILLED]["live"] is False
    assert rows[KILLED]["has_pane"] is False
    assert rows[KILLED]["work_running"] is None and rows[KILLED]["pane_why"]


def test_live_sessions_come_first(bed: Bed) -> None:
    ids = [row["session_id"] for row in json_body(bed.get(SESSIONS))["sessions"]]
    assert ids[0] == OLD and ENDED in ids[1:]


# ----------------------------------------------------- c2: turns, replies, tool calls, cursor


#: Keys of a Claude Code transcript row that no object of the API carries.
ROW_KEYS = {"parentUuid", "isSidechain", "sessionId", "origin", "toolUseResult", "promptSource"}


def assert_no_raw_row(value: object) -> None:
    """Fail if any object in ``value`` is, or holds, a transcript row."""
    if isinstance(value, dict):
        assert not (ROW_KEYS & value.keys()), f"a raw transcript row: {sorted(value)[:8]}"
        for item in value.values():
            assert_no_raw_row(item)
    elif isinstance(value, list):
        for item in value:
            assert_no_raw_row(item)
    elif isinstance(value, str) and value.startswith("{"):
        try:
            parsed = json.loads(value)
        except ValueError:
            return
        if isinstance(parsed, dict):
            assert not (ROW_KEYS & parsed.keys()), "a raw transcript row inside a string"


def test_the_no_raw_row_check_catches_a_real_row() -> None:
    row = json.loads(OLD_FILE.read_text(encoding="utf-8").splitlines()[0])
    with pytest.raises(AssertionError):
        assert_no_raw_row({"turns": [row]})
    with pytest.raises(AssertionError):
        assert_no_raw_row({"turns": [json.dumps(row)]})


def test_turns_are_the_session_layers_objects_and_never_a_row(bed: Bed) -> None:
    document = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))
    assert_no_raw_row(document)
    assert document["ok"] is True and document["session_id"] == OLD
    assert document["readable"] is True and document["reset"] is False

    assert set(document["turns"][0]) == {
        "id", "at", "form", "text", "typed", "paste_known", "prompt_id", "images"
    }  # fmt: skip
    assert {t["form"] for t in document["turns"]} >= {"typed", "mid-turn", "answer"}
    assert document["turns"][0]["text"].startswith("Write three paragraphs")

    assert set(document["replies"][0]) == {
        "message_id", "uuid", "at", "text", "thinking", "thinking_omitted", "model", "usage",
        "record_uuids"
    }  # fmt: skip
    assert any(r["text"].startswith("A lighthouse is") for r in document["replies"])
    assert len({r["message_id"] for r in document["replies"]}) == len(document["replies"])

    calls = document["tool_calls"]
    assert set(calls[0]) == {"call_id", "uuid", "at", "name", "input", "result"}
    assert any(c["name"] == "AskUserQuestion" for c in calls)
    assert len({c["call_id"] for c in calls}) == len(calls)
    resulted = [c["result"] for c in calls if c["result"]]
    assert resulted and all(set(r) == {"uuid", "at", "text", "is_error"} for r in resulted)
    assert all(set(s) == {"agent_id", "tool_call_uuid", "description", "agent_type"}
               for s in document["subagents"])  # fmt: skip


def test_a_cursor_returns_only_what_is_new(bed: Bed) -> None:
    rows = bed.rows()
    bed.write_transcript(rows[: len(rows) // 2])
    first = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))
    seen_turns = {t["id"] for t in first["turns"]}

    bed.append(rows[len(rows) // 2 :])
    second = json_body(bed.get(f"{SESSIONS}/{OLD}/turns?cursor={first['cursor']}"))
    assert second["reset"] is False
    assert second["cursor"] != first["cursor"]
    assert not (seen_turns & {t["id"] for t in second["turns"]})
    assert second["turns"] or second["replies"] or second["tool_calls"]

    third = json_body(bed.get(f"{SESSIONS}/{OLD}/turns?cursor={second['cursor']}"))
    assert third["cursor"] == second["cursor"]
    assert (third["turns"], third["replies"], third["tool_calls"]) == ([], [], [])


def test_a_tool_result_landing_later_redelivers_its_call(bed: Bed) -> None:
    rows = bed.rows()
    parsed = [json.loads(line) for line in rows]
    with_call = next(
        i
        for i, row in enumerate(parsed)
        if any(
            isinstance(block, dict) and block.get("type") == "tool_use"
            for block in (row.get("message") or {}).get("content", [])
            if isinstance(row.get("message"), dict)
            and isinstance((row.get("message") or {}).get("content"), list)
        )
    )
    bed.write_transcript(rows[: with_call + 1])
    first = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))
    pending = [c for c in first["tool_calls"] if c["result"] is None]
    assert pending

    bed.append(rows[with_call + 1 :])
    second = json_body(bed.get(f"{SESSIONS}/{OLD}/turns?cursor={first['cursor']}"))
    resolved = {c["call_id"]: c for c in second["tool_calls"]}
    assert resolved[pending[0]["call_id"]]["result"] is not None


def test_a_cursor_from_an_incremental_read_matches_a_read_from_the_start(bed: Bed) -> None:
    """The sampler's position is built read by read and a route's from one read of the file."""
    from kyosei.harness.claude_code_transcript import SessionTranscript

    rows = bed.rows()
    bed.write_transcript(rows[:20])
    reader = SessionTranscript(bed.transcript)
    incremental = serve_sessions._Position()
    for chunk in (rows[20:40], rows[40:]):
        incremental.feed(serve_sessions._lists(reader.read()))
        bed.append(chunk)
    incremental.feed(serve_sessions._lists(reader.read()))

    whole = serve_sessions._Position()
    whole.feed(serve_sessions._lists(SessionTranscript(bed.transcript).read()))
    assert incremental.cursor == whole.cursor


def test_a_rewritten_transcript_resets_the_cursor(bed: Bed) -> None:
    rows = bed.rows()
    first = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))
    # The same number of records, not the same records.
    bed.write_transcript(bed.rows(source=NEW_FILE) + rows[: len(rows) // 2])
    second = json_body(bed.get(f"{SESSIONS}/{OLD}/turns?cursor={first['cursor']}"))
    assert second["reset"] is True
    assert second["turns"], "a reset sends the whole session again"


def test_a_cursor_past_the_end_resets(bed: Bed) -> None:
    past = "999.999.999.999.999.999.abcdefabcdef"
    second = json_body(bed.get(f"{SESSIONS}/{OLD}/turns?cursor={past}"))
    assert second["reset"] is True and second["turns"]


# --------------------------------------------- KYO-691: facts and slash-command output


def install_catalog(bed: Bed) -> None:
    directory = bed.claude / "cache" / "model-catalog"
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy(CATALOG, directory / "acct-org-cc.json")


def test_turns_state_model_effort_branch_and_context(bed: Bed) -> None:
    install_catalog(bed)
    bed.write_transcript(bed.rows(source=FACTS_FILE))
    facts = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"]
    assert facts["model"] == "claude-opus-5-5"
    assert facts["effort"] == "medium"
    assert facts["git_branch"] == "feature/facts"
    assert facts["context"] == {"tokens": 226_458, "window": 1_000_000, "percent": 23}


def test_turns_state_the_permission_mode_last_recorded(bed: Bed) -> None:
    bed.write_transcript(bed.rows(source=PERMISSION_MODE_FILE)[:4])
    facts = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"]
    assert facts["permission_mode"] == "plan"


def test_turns_state_no_permission_mode_where_the_transcript_records_none(bed: Bed) -> None:
    bed.write_transcript(bed.rows(source=FACTS_FILE))
    assert json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"]["permission_mode"] is None


def test_a_model_of_unknown_window_gets_tokens_and_no_percent(bed: Bed) -> None:
    bed.write_transcript(bed.rows(source=FACTS_FILE))  # no catalogue installed
    context = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"]["context"]
    assert context == {"tokens": 226_458, "window": None, "percent": None}


def test_facts_are_stated_on_a_read_with_nothing_new(bed: Bed) -> None:
    install_catalog(bed)
    bed.write_transcript(bed.rows(source=FACTS_FILE))
    first = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))
    second = json_body(bed.get(f"{SESSIONS}/{OLD}/turns?cursor={first['cursor']}"))
    assert second["turns"] == [] and second["replies"] == []
    assert second["facts"] == first["facts"]


def test_a_session_with_no_reply_has_no_facts(bed: Bed) -> None:
    bed.write_transcript(bed.rows(1, source=FACTS_FILE))
    assert json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"] is None


def test_turns_return_slash_command_output_in_time_order(bed: Bed) -> None:
    bed.write_transcript(bed.rows(source=FACTS_FILE))
    document = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))
    assert_no_raw_row(document)
    outputs = document["command_outputs"]
    assert all(set(o) == {"uuid", "at", "command", "args", "text"} for o in outputs)
    assert [o["command"] for o in outputs] == ["/context", "/effort"]
    # Between the first reply and the last, on the same clock as the other objects.
    ats = [o["at"] for o in outputs]
    assert ats == sorted(ats)
    assert document["replies"][0]["at"] < ats[0] and ats[-1] < document["replies"][1]["at"]


def test_a_cursor_returns_only_new_command_output(bed: Bed) -> None:
    rows = bed.rows(source=FACTS_FILE)
    bed.write_transcript(rows[:4])
    first = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))
    assert [o["command"] for o in first["command_outputs"]] == ["/context"]
    bed.append(rows[4:6])
    second = json_body(bed.get(f"{SESSIONS}/{OLD}/turns?cursor={first['cursor']}"))
    assert second["reset"] is False
    assert [o["command"] for o in second["command_outputs"]] == ["/effort"]


def test_a_cursor_counts_command_output_so_a_changed_one_resets(bed: Bed) -> None:
    bed.write_transcript(bed.rows(source=FACTS_FILE)[:4])
    first = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))
    bed.write_transcript(bed.rows(source=FACTS_FILE)[:2])
    second = json_body(bed.get(f"{SESSIONS}/{OLD}/turns?cursor={first['cursor']}"))
    assert second["reset"] is True


def test_the_stream_states_facts_when_it_opens_and_when_a_reply_changes_them(
    bed: Bed, stream
) -> None:
    install_catalog(bed)
    rows = bed.rows(source=FACTS_FILE)
    bed.write_transcript(rows[:2])
    opened = stream()
    ready = opened.event("ready")
    assert ready["facts"]["effort"] == "high" and ready["facts"]["git_branch"] == "main"
    assert ready["facts"]["context"]["tokens"] == 125
    bed.append(rows[2:])
    event = opened.event("facts")
    assert event["session_id"] == OLD
    assert event["facts"]["effort"] == "medium"
    assert event["facts"]["context"]["percent"] == 23


def test_the_stream_states_a_change_of_permission_mode_that_no_reply_followed(
    bed: Bed, stream
) -> None:
    rows = bed.rows(source=PERMISSION_MODE_FILE)
    bed.write_transcript(rows[:3])
    opened = stream()
    assert opened.event("ready")["facts"]["permission_mode"] == "default"
    bed.append(rows[3:4])
    event = opened.event("facts")
    assert event["session_id"] == OLD
    assert event["facts"]["permission_mode"] == "plan"


def test_the_stream_delivers_command_output(bed: Bed, stream) -> None:
    rows = bed.rows(source=FACTS_FILE)
    bed.write_transcript(rows[:2])
    opened = stream()
    opened.event("ready")
    bed.append(rows[2:4])
    event = opened.event("transcript")
    assert [o["command"] for o in event["command_outputs"]] == ["/context"]


def test_a_garbled_cursor_is_refused_and_dropping_it_clears_the_refusal(bed: Bed) -> None:
    status, _, body = bed.get(f"{SESSIONS}/{OLD}/turns?cursor=nonsense")
    assert status == 400 and "cursor" in body_json(body)["error"]
    assert json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["ok"] is True


def test_a_query_key_the_session_routes_do_not_take_is_refused(bed: Bed) -> None:
    status, _, body = bed.get(f"{SESSIONS}/{OLD}/turns?since=1")
    assert status == 400 and "?cursor" in body_json(body)["error"]
    assert json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["ok"] is True


def test_an_unknown_session_id_is_refused_by_name(bed: Bed) -> None:
    status, _, body = bed.get(f"{SESSIONS}/55555555-5555-4555-8555-555555555555/turns")
    assert status == 404 and "/sessions" in body_json(body)["error"]


def test_a_session_of_another_project_is_not_served(bed: Bed) -> None:
    """The layer finds a transcript by id alone; ``sessions.json`` is what scopes it."""
    other = bed.transcripts / f"{ELSEWHERE}.jsonl"
    shutil.copyfile(OLD_FILE, other)
    for route in ("turns", "screen", "events"):
        status, _, body = bed.get(f"{SESSIONS}/{ELSEWHERE}/{route}")
        assert status == 404, route
        assert "not recorded for this project" in body_json(body)["error"]


# --------------------------------------------------------------- c3: the per-session stream


# -------------------------------------------------------- KYO-692: deliveries and images


def delivered_row(position: int) -> str:
    return f"00000000-0000-4000-8000-{position:012d}"


IMAGE_ROUTE = f"{SESSIONS}/{OLD}/images/{delivered_row(1)}/0"


def show_delivered(bed: Bed) -> None:
    bed.write_transcript(bed.rows(source=DELIVERED_FILE))


def test_turns_return_deliveries_as_their_own_objects_in_time_order(bed: Bed) -> None:
    show_delivered(bed)
    document = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))
    assert_no_raw_row(document)
    deliveries = document["deliveries"]
    assert [d["kind"] for d in deliveries] == [
        "task-notification",
        "peer",
        "task-notification",
        "task-notification",
        "peer",
        "channel",
    ]
    assert [d["at"] for d in deliveries] == sorted(d["at"] for d in deliveries)
    assert not {d["uuid"] for d in deliveries} & {t["id"] for t in document["turns"]}
    assert set(deliveries[0]) == {
        "uuid", "at", "kind", "text", "sender", "sender_id", "handback", "task_id",
        "call_id", "status", "summary", "usage",
    }  # fmt: skip
    handback = next(d for d in deliveries if d["handback"])
    assert handback["sender"] == "kyosei-worker" and "written for this test" in handback["text"]


def test_a_cursor_returns_only_new_deliveries(bed: Bed) -> None:
    rows = bed.rows(source=DELIVERED_FILE)
    bed.write_transcript(rows[:8])  # through the first two user-record deliveries
    first = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))
    assert len(first["deliveries"]) == 2
    bed.append(rows[8:9])
    second = json_body(bed.get(f"{SESSIONS}/{OLD}/turns?cursor={first['cursor']}"))
    assert second["reset"] is False
    assert [d["uuid"] for d in second["deliveries"]] == [delivered_row(9)]
    assert second["turns"] == [] and second["replies"] == []


def test_the_stream_delivers_a_delivery(bed: Bed, stream) -> None:
    rows = bed.rows(source=DELIVERED_FILE)
    bed.write_transcript(rows[:1])
    opened = stream()
    opened.event("ready")
    bed.append(rows[6:7])
    event = opened.event("transcript")
    assert [d["kind"] for d in event["deliveries"]] == ["peer"]


def test_a_turn_names_its_images_and_carries_no_bytes(bed: Bed) -> None:
    show_delivered(bed)
    status, _, body = bed.get(f"{SESSIONS}/{OLD}/turns")
    assert status == 200
    document = body_json(body)
    turn = next(t for t in document["turns"] if t["id"] == delivered_row(1))
    assert [i["index"] for i in turn["images"]] == [0, 2]
    assert all(i["media_type"] == "image/png" and i["size"] > 0 for i in turn["images"])
    assert b"iVBORw0KGgo" not in body  # the base64 opening of every PNG
    damaged = next(t for t in document["turns"] if t["id"] == delivered_row(17))
    assert damaged["images"]  # listed by its header; the bytes route answers for the data


def test_an_image_is_served_with_its_own_type(bed: Bed) -> None:
    show_delivered(bed)
    status, headers, body = bed.get(IMAGE_ROUTE)
    assert status == 200 and body.startswith(b"\x89PNG\r\n\x1a\n")
    assert headers["Content-Type"] == "image/png"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cache-Control"].startswith("private")
    other = bed.get(f"{SESSIONS}/{OLD}/images/{delivered_row(1)}/2")[2]
    assert other != body


def test_a_refused_image_names_where_to_find_those_the_turn_holds(bed: Bed) -> None:
    show_delivered(bed)
    status, _, body = bed.get(f"{SESSIONS}/{OLD}/images/{delivered_row(1)}/5")
    assert status == 404
    error = body_json(body)["error"]
    assert "`images`" in error and f"sessions/{OLD}/turns" in error
    listed = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["turns"][0]["images"]  # the remedy
    assert bed.get(f"{SESSIONS}/{OLD}/images/{delivered_row(1)}/{listed[0]['index']}")[0] == 200


@pytest.mark.parametrize(
    "tail",
    [
        f"images/{delivered_row(16)}/0",  # a tool's image
        f"images/{delivered_row(17)}/0",  # data that is not base64
        f"images/{delivered_row(1)}/1",  # a type that could run as a page
    ],
)
def test_an_image_the_person_cannot_be_shown_is_refused(bed: Bed, tail: str) -> None:
    show_delivered(bed)
    assert bed.get(f"{SESSIONS}/{OLD}/{tail}")[0] == 404


@pytest.mark.parametrize("index", ["x", "-1", "1.5", "²"])
def test_an_image_index_must_be_a_number(bed: Bed, index: str) -> None:
    show_delivered(bed)
    quoted = urllib.parse.quote(index)
    status, _, body = bed.get(f"{SESSIONS}/{OLD}/images/{delivered_row(1)}/{quoted}")
    assert status == 404, body


def test_an_image_of_a_session_of_another_project_is_not_served(bed: Bed) -> None:
    show_delivered(bed)
    unrecorded = bed.transcripts / f"{ELSEWHERE}.jsonl"
    shutil.copyfile(DELIVERED_FILE, unrecorded)
    route = f"{SESSIONS}/{ELSEWHERE}/images/{delivered_row(1)}/0"
    assert bed.get(route)[0] == 404


def test_the_image_route_refuses_until_the_project_opts_in(unopted: Client, project: Path) -> None:
    transcript = Path(unopted.server.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-work"
    shutil.copyfile(DELIVERED_FILE, transcript / f"{OLD}.jsonl")
    status, _, body = unopted.get(IMAGE_ROUTE)
    assert status == 403 and "read_transcripts: true" in body_json(body)["error"]
    assert b"PNG" not in body
    opt_in_transcripts(project)
    status, headers, _ = unopted.get(IMAGE_ROUTE)
    assert status == 200 and headers["Content-Type"] == "image/png"


def test_the_image_route_refuses_a_host_it_does_not_know(bed: Bed) -> None:
    show_delivered(bed)
    status, _, body = bed.get(IMAGE_ROUTE, host="evil.example")
    assert status == 403 and "--allow-host" in body_json(body)["error"]


def test_the_image_route_refuses_a_foreign_origin(bed: Bed) -> None:
    show_delivered(bed)
    status, _, body = bed.get(IMAGE_ROUTE, headers={"Origin": "https://evil.example"})
    assert status == 403 and "--allow-origin" in body_json(body)["error"]


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_the_image_route_answers_get_only(bed: Bed, method: str) -> None:
    show_delivered(bed)
    headers = {"Content-Type": "application/json"} if method == "POST" else {}
    status, response_headers, _ = bed.client.request(IMAGE_ROUTE, method=method, headers=headers)
    assert status == 405 and response_headers["Allow"] == "GET"


# ------------------------------------- KYO-693: titles, a subagent's calls, uncommitted lines

TITLED_FILE = FIXTURES / "titled" / "session.synthetic.jsonl"
AGENT = "a14ca9deaf9c51bd1"  # the pair fixture's subagent, so /turns names it too
AGENT_FILE = FIXTURES / "subagent" / f"agent-{AGENT}.jsonl"
AGENT_ROUTE = f"{SESSIONS}/{OLD}/subagents/{AGENT}"
CHANGES_ROUTE = f"{SESSIONS}/{OLD}/changes"


def place_agent(bed: Bed, session_id: str = OLD) -> None:
    directory = bed.transcripts / session_id / "subagents"
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(AGENT_FILE, directory / AGENT_FILE.name)


def test_each_row_states_the_title_its_transcript_holds(bed: Bed) -> None:
    shutil.copyfile(TITLED_FILE, bed.transcript)
    rows = {row["session_id"]: row for row in json_body(bed.get(SESSIONS))["sessions"]}
    assert rows[OLD]["title"] == "The latest title"


def test_an_ended_session_has_its_title_without_a_pane_being_read(bed: Bed) -> None:
    shutil.copyfile(TITLED_FILE, bed.transcripts / f"{ENDED}.jsonl")
    entries = [_entry(ENDED, state="ended", seen="2026-11-09T11:00:00.000Z")]
    assert cmd_sessions.write_sessions(cmd_sessions.sessions_path(bed.project), entries) is None
    rows = json_body(bed.get(SESSIONS))["sessions"]
    assert rows[0]["title"] == "The latest title"
    assert bed.pane_reads() == 0


def test_a_transcript_holding_no_title_gets_none_rather_than_an_invented_one(bed: Bed) -> None:
    rows = {row["session_id"]: row for row in json_body(bed.get(SESSIONS))["sessions"]}
    assert rows[OLD]["title"] is None  # the fixture has no ai-title row
    assert rows[ENDED]["title"] is None  # and this session has no transcript at all


def test_a_subagents_tool_calls_come_from_its_own_transcript(bed: Bed) -> None:
    place_agent(bed)
    document = json_body(bed.get(AGENT_ROUTE))
    assert document["session_id"] == OLD and document["agent_id"] == AGENT
    assert document["readable"] is True
    calls = document["tool_calls"]
    assert [call["name"] for call in calls] == ["Bash", "Read"]
    assert calls[0]["input"] == {"command": "ls /work | wc -l"}
    assert calls[0]["result"]["text"] == "4" and calls[0]["result"]["is_error"] is False
    assert calls[1]["result"] is None  # that call has not finished
    assert_no_raw_row(document)


def test_turns_list_a_subagent_the_route_serves(bed: Bed) -> None:
    place_agent(bed)
    turns = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))
    listed = [item["agent_id"] for item in turns["subagents"]]
    assert AGENT in listed
    assert json_body(bed.get(AGENT_ROUTE))["tool_calls"]


def test_an_agent_id_that_is_not_this_sessions_is_refused_and_names_where_to_look(bed: Bed) -> None:
    place_agent(bed)
    status, _, body = bed.get(f"{SESSIONS}/{OLD}/subagents/nosuch")
    assert status == 404
    error = body_json(body)["error"]
    assert "nosuch" in error and f"/sessions/{OLD}/turns" in error


def test_an_agent_of_another_recorded_session_is_refused_here(bed: Bed) -> None:
    place_agent(bed, ENDED)
    assert bed.get(AGENT_ROUTE)[0] == 404


@pytest.mark.parametrize("agent", ["..%2F..%2F" + OLD, "..", "agent-" + AGENT + ".jsonl"])
def test_an_agent_id_cannot_name_a_file_outside_the_sessions_subagents(
    bed: Bed, agent: str
) -> None:
    place_agent(bed)
    assert bed.get(f"{SESSIONS}/{OLD}/subagents/{agent}")[0] == 404


def test_an_agent_of_a_session_this_project_does_not_record_is_not_served(bed: Bed) -> None:
    shutil.copyfile(OLD_FILE, bed.transcripts / f"{ELSEWHERE}.jsonl")
    place_agent(bed, ELSEWHERE)
    assert bed.get(f"{SESSIONS}/{ELSEWHERE}/subagents/{AGENT}")[0] == 404


# --------------------------------------------- KYO-695: a task's usage, a dispatch's lines

AGENT_USAGE_FILE = FIXTURES / "agent-usage.synthetic.jsonl"
WORKER, PLAIN, UNKNOWN = ("a1111111111111111", "a2222222222222222", "a3333333333333333")


def place_usage_session(bed: Bed) -> None:
    shutil.copyfile(AGENT_USAGE_FILE, bed.transcript)
    directory = bed.transcripts / OLD / "subagents"
    directory.mkdir(parents=True, exist_ok=True)
    for agent in (WORKER, PLAIN, UNKNOWN):
        shutil.copyfile(AGENT_FILE, directory / f"agent-{agent}.jsonl")


def worker_on_a_branch(bed: Bed, kyo) -> tuple[str, str]:
    """Dispatch TST-1 on branch ``feat``, which adds two lines and removes one past its base."""
    git_init(bed.project)
    record = new_record(kyo)
    dispatched = kyo("dispatch", record, "--no-create-worktree", "--branch", "feat", "--json")
    assert dispatched.code == 0, dispatched
    assert dispatched.json["dispatch_id"] == "TST-1.d1"
    base = dispatched.json["worked_from_sha"]

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=bed.project, check=True, capture_output=True)

    run("checkout", "-q", "-b", "feat", base)
    (bed.project / "work.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    run("add", "work.txt")
    run("commit", "-q", "-m", "a file")
    base_of_change = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=bed.project, check=True, capture_output=True, text=True
    ).stdout.strip()
    (bed.project / "work.txt").write_text("one\nTWO\nthree\nfour\n", encoding="utf-8")
    run("commit", "-q", "-am", "a change")
    run("checkout", "-q", "main")
    return base, base_of_change


def test_a_deliverys_usage_is_served_and_is_none_where_the_row_records_none(bed: Bed) -> None:
    place_usage_session(bed)
    document = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))
    deliveries = {item["task_id"]: item for item in document["deliveries"]}
    assert deliveries[WORKER]["usage"] == {
        "total_tokens": 77593,
        "tool_uses": 12,
        "duration_ms": 122833,
    }
    assert deliveries[PLAIN]["usage"] == {
        "total_tokens": None,
        "tool_uses": 4,
        "duration_ms": None,
    }
    assert deliveries["bg7"]["usage"] is None


def test_a_dispatched_workers_route_states_its_record_branch_and_lines(bed: Bed, kyo) -> None:
    base, _ = worker_on_a_branch(bed, kyo)
    place_usage_session(bed)
    document = json_body(bed.get(f"{SESSIONS}/{OLD}/subagents/{WORKER}"))
    # From the dispatch's base: the file's three new lines and the change's +2 -1 net to +4 -0.
    assert document["dispatch"] == {
        "id": "TST-1.d1",
        "record": "TST-1",
        "branch": "feat",
        "added": 4,
        "removed": 0,
    }
    assert document["dispatch_why"] is None
    assert document["tool_calls"]  # the subagent's own calls are still served
    assert base


def test_the_lines_are_between_the_base_and_the_tip_not_the_last_commit(bed: Bed, kyo) -> None:
    _, change_base = worker_on_a_branch(bed, kyo)
    assert session_layer.branch_lines(bed.project, change_base, "feat") == (2, 1)


def test_a_subagent_no_dispatch_names_has_none(bed: Bed, kyo) -> None:
    worker_on_a_branch(bed, kyo)
    place_usage_session(bed)
    document = json_body(bed.get(f"{SESSIONS}/{OLD}/subagents/{PLAIN}"))
    assert document["dispatch"] is None and "no dispatch names" in document["dispatch_why"]


def test_a_prompt_naming_a_dispatch_the_store_lacks_has_none(bed: Bed, kyo) -> None:
    worker_on_a_branch(bed, kyo)
    place_usage_session(bed)
    document = json_body(bed.get(f"{SESSIONS}/{OLD}/subagents/{UNKNOWN}"))
    assert document["dispatch"] is None and "TST-99.d1" in document["dispatch_why"]


def test_a_deleted_branch_has_none(bed: Bed, kyo) -> None:
    worker_on_a_branch(bed, kyo)
    place_usage_session(bed)
    subprocess.run(["git", "branch", "-D", "feat"], cwd=bed.project, check=True)
    document = json_body(bed.get(f"{SESSIONS}/{OLD}/subagents/{WORKER}"))
    assert document["dispatch"] is None and "TST-1.d1" in document["dispatch_why"]


def test_a_store_that_cannot_be_read_leaves_the_tool_calls_served(bed: Bed, kyo) -> None:
    worker_on_a_branch(bed, kyo)
    place_usage_session(bed)
    (bed.project / "kyosei" / "kyosei.db").unlink()
    document = json_body(bed.get(f"{SESSIONS}/{OLD}/subagents/{WORKER}"))
    assert document["dispatch"] is None and "could not be read" in document["dispatch_why"]
    assert document["tool_calls"]


def test_the_subagent_route_refuses_until_the_project_opts_in(
    unopted: Client, project: Path
) -> None:
    home = Path(unopted.server.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-work"
    shutil.copyfile(OLD_FILE, home / f"{OLD}.jsonl")
    (home / OLD / "subagents").mkdir(parents=True)
    shutil.copyfile(AGENT_FILE, home / OLD / "subagents" / AGENT_FILE.name)
    status, _, body = unopted.get(AGENT_ROUTE)
    assert status == 403 and "read_transcripts: true" in body_json(body)["error"]
    assert b"toolu_sub1" not in body
    opt_in_transcripts(project)
    assert unopted.get(AGENT_ROUTE)[0] == 200


@pytest.mark.parametrize("route", [AGENT_ROUTE, CHANGES_ROUTE])
def test_the_new_routes_refuse_a_host_and_an_origin_they_do_not_know(bed: Bed, route: str) -> None:
    place_agent(bed)
    status, _, body = bed.get(route, host="evil.example")
    assert status == 403 and "--allow-host" in body_json(body)["error"]
    status, _, body = bed.get(route, headers={"Origin": "https://evil.example"})
    assert status == 403 and "--allow-origin" in body_json(body)["error"]


@pytest.mark.parametrize("route", [AGENT_ROUTE, CHANGES_ROUTE])
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_the_new_routes_answer_get_only(bed: Bed, route: str, method: str) -> None:
    headers = {"Content-Type": "application/json"} if method == "POST" else {}
    status, response_headers, _ = bed.client.request(route, method=method, headers=headers)
    assert status == 405 and response_headers["Allow"] == "GET"


def init_repo(path: Path) -> None:
    path.mkdir()
    who = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")
    env = {**os.environ, **dict.fromkeys(who, "t")}
    (path / "kept.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "kept.txt"], ["commit", "-q", "-m", "first"]):
        subprocess.run(["git", *args], cwd=path, env=env, check=True)


def record_cwd(bed: Bed, cwd: str) -> None:
    entries = [_entry(OLD, state="running", seen="2026-11-09T10:00:00.000Z", cwd=cwd)]
    assert cmd_sessions.write_sessions(cmd_sessions.sessions_path(bed.project), entries) is None


def test_changes_are_the_tracked_lines_with_untracked_files_counted_apart(
    bed: Bed, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "kept.txt").write_text("one\nTWO\nthree\nfour\n", encoding="utf-8")  # +2 -1
    (repo / "new.txt").write_text("a\nb\nc\n", encoding="utf-8")  # untracked, three lines
    record_cwd(bed, str(repo))
    document = json_body(bed.get(CHANGES_ROUTE))
    assert document["cwd"] == str(repo) and document["why"] is None
    assert Path(document["root"]).resolve() == repo.resolve()
    assert (document["added"], document["removed"], document["files"]) == (2, 1, 1)
    assert document["untracked"] == {"files": 1, "lines": 3, "uncounted": 0}


def test_changes_for_a_working_directory_that_is_gone_say_so(bed: Bed) -> None:
    document = json_body(bed.get(CHANGES_ROUTE))  # the bed records /work, which does not exist
    assert document["root"] is None and "/work" in document["why"]
    assert (document["added"], document["removed"]) == (0, 0)


def test_the_changes_of_a_session_this_project_does_not_record_are_not_served(bed: Bed) -> None:
    assert bed.get(f"{SESSIONS}/{ELSEWHERE}/changes")[0] == 404


def test_turns_return_what_the_transcript_records_of_thinking(bed: Bed) -> None:
    show_delivered(bed)
    replies = {r["message_id"]: r for r in json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["replies"]}
    assert replies["msg_b"]["thinking"] == "Checking the colours."
    assert replies["msg_b"]["thinking_omitted"] == 1
    assert replies["msg_a"]["thinking"] is None and replies["msg_a"]["thinking_omitted"] == 1
    assert SIGNATURE_ONLY not in json.dumps(replies)


def _wait_for(predicate, *, within: float = 10.0) -> None:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("the condition did not become true in time")


def _sampler_threads() -> int:
    return sum(t.name == "kyo-serve-session-sampler" for t in threading.enumerate())


def test_no_pane_is_read_while_no_client_is_subscribed(bed: Bed) -> None:
    time.sleep(0.2)
    assert bed.pane_reads() == 0
    assert bed.server.session_hub.samplers() == []


def test_two_subscribers_share_one_sampler_and_reads_stop_with_the_last(bed: Bed, stream) -> None:
    # A long keepalive, so that only the check of the closed socket can end these streams.
    bed.server.keepalive_seconds = 60
    first, second = stream(), stream()
    assert first.event("ready")["session_id"] == OLD
    assert second.event("ready")["session_id"] == OLD

    hub = bed.server.session_hub
    samplers = hub.samplers()
    assert len(samplers) == 1 and _sampler_threads() == 1
    sampler = samplers[0]
    _wait_for(lambda: sampler.ticks >= 8)

    # One subscriber leaving keeps the sampler for the other.
    first.close()
    time.sleep(0.5)
    assert hub.samplers() == [sampler] and not sampler.stop.is_set()
    ticks_before = sampler.ticks
    _wait_for(lambda: sampler.ticks > ticks_before + 2)

    # The last one leaving stops it.
    second.close()
    _wait_for(lambda: hub.samplers() == [] and _sampler_threads() == 0)
    reads = bed.pane_reads()
    time.sleep(0.2)
    assert bed.pane_reads() == reads, "a pane was read after the last client left"
    # An idle screen is read once per sample and once for the baseline, with two clients.
    assert reads == sampler.ticks + 1


def test_a_new_subscriber_after_the_last_left_starts_a_new_sampler(bed: Bed, stream) -> None:
    one = stream()
    one.event("ready")
    hub = bed.server.session_hub
    first = hub.samplers()[0]
    one.close()
    _wait_for(lambda: hub.samplers() == [])

    two = stream()
    two.event("ready")
    assert hub.samplers()[0] is not first


def test_the_stream_delivers_new_transcript_objects(bed: Bed, stream) -> None:
    rows = bed.rows()
    bed.write_transcript(rows[: len(rows) // 2])
    opened = stream()
    ready = opened.event("ready")
    assert ready["screen"]["state"] == "idle" and ready["pane"]["present"] is True
    bed.append(rows[len(rows) // 2 :])
    event = opened.event("transcript")
    assert event["session_id"] == OLD and event["cursor"] != ready["cursor"]
    assert event["turns"] or event["replies"] or event["tool_calls"]
    assert_no_raw_row(event)


def test_the_stream_delivers_the_reply_in_progress(bed: Bed, stream) -> None:
    opened = stream()
    opened.event("ready")
    bed.show_frame("frame-05.txt")
    reply = opened.event("reply")
    assert reply["text"].startswith("Tides are the regular rise")
    bed.show("idle.txt")
    assert opened.event("reply")["text"] is None


def test_the_stream_delivers_an_overlay_change(bed: Bed, stream) -> None:
    opened = stream()
    opened.event("ready")
    bed.show("permission-bash.txt")
    event = opened.event("screen")
    assert event["screen"]["state"] == "permission-prompt"
    assert event["screen"]["question"].startswith("Do you want")
    assert [o["number"] for o in event["screen"]["options"]][:1] == [1]
    bed.show("idle.txt")
    assert opened.event("screen")["screen"]["state"] == "idle"


def test_an_idle_stream_sends_a_keepalive(bed: Bed, stream) -> None:
    opened = stream()
    opened.event("ready")
    assert opened.block() == [": keepalive"]


def test_a_cursor_on_the_stream_catches_up_past_it(bed: Bed, stream) -> None:
    rows = bed.rows()
    bed.write_transcript(rows[: len(rows) // 2])
    cursor = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["cursor"]
    bed.append(rows[len(rows) // 2 :])

    opened = stream(query=f"?cursor={cursor}")
    caught = opened.event("transcript")
    assert caught["reset"] is False and caught["cursor"] != cursor
    assert caught["turns"] or caught["replies"] or caught["tool_calls"]


def test_last_event_id_is_honoured_and_a_bad_one_ignored(bed: Bed, stream) -> None:
    rows = bed.rows()
    bed.write_transcript(rows[: len(rows) // 2])
    cursor = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["cursor"]
    bed.append(rows[len(rows) // 2 :])

    resumed = stream(Last_Event_ID=cursor)
    assert resumed.event("transcript")["cursor"] != cursor

    fresh = stream(Last_Event_ID="not-a-cursor")
    assert fresh.event("ready")["session_id"] == OLD


def test_the_stream_follows_clear_and_a_subscriber_to_the_new_id_joins(bed: Bed, stream) -> None:
    opened = stream()
    opened.event("ready")
    follows = bed.transcripts / f"{NEW}.jsonl"
    shutil.copyfile(NEW_FILE, follows)
    registry = bed.claude / "sessions" / f"{os.getpid()}.json"
    registry.write_text(json.dumps({"pid": os.getpid(), "sessionId": NEW}), encoding="utf-8")

    moved = opened.event("session")
    assert moved["session_id"] == NEW and moved["previous_session_id"] == OLD
    assert moved["followed_clear"] is True
    assert opened.event("transcript")["session_id"] == NEW

    entries = cmd_sessions.read_sessions(cmd_sessions.sessions_path(bed.project)).entries
    entries.append(_entry(NEW, state="running", seen="2026-11-09T12:00:00.000Z"))
    assert cmd_sessions.write_sessions(cmd_sessions.sessions_path(bed.project), entries) is None
    joined = stream(NEW)
    assert joined.event("ready")["session_id"] == NEW
    assert len(bed.server.session_hub.samplers()) == 1 and _sampler_threads() == 1


def test_a_pane_that_closes_is_not_reported_as_an_unrecognised_screen(
    bed: Bed, stream, store_home: Path
) -> None:
    """A gone pane captures as nothing; the layer looks it up again instead of logging it."""
    opened = stream()
    opened.event("ready")
    bed.screen_file.write_text("", encoding="utf-8")
    event = opened.event("screen")
    assert event["screen"] is None
    assert not (store_home / "pane-unrecognised").exists()


# ------------------------------------------------------------------------- c4: the screen


@pytest.mark.parametrize(
    ("fixture", "state"),
    [
        ("idle.txt", "idle"),
        ("permission-bash.txt", "permission-prompt"),
        ("ask.txt", "ask-user-question"),
        ("usage.txt", "usage-panel"),
        ("trust-dialog.synthetic.txt", "trust-dialog"),
        ("unrecognised-help.txt", "unrecognised"),
    ],
)
def test_the_screen_route_returns_the_layers_classification(
    bed: Bed, fixture: str, state: str
) -> None:
    bed.show(fixture)
    document = json_body(bed.get(f"{SESSIONS}/{OLD}/screen"))
    assert document["session_id"] == OLD and document["screen"]["state"] == state
    assert document["pane"]["present"] is True and document["pane"]["pane_id"] == "%1"
    assert set(document["screen"]) == {"state", "question", "options", "input_text", "background"}
    # A classification only: no text of the screen is carried.
    assert "text" not in document["screen"] and "raw" not in document["screen"]


def test_the_screen_route_returns_the_panes_text_without_styling(bed: Bed) -> None:
    bed.show_frame("frame-03.txt")
    document = json_body(bed.get(f"{SESSIONS}/{OLD}/screen"))
    assert "\x1b" not in document["terminal"]["text"]
    assert "Claude Code" in document["terminal"]["text"]
    assert document["terminal"]["working_line"] == "Thinking… (2s · ↓ 139 tokens)"
    assert document["screen"]["state"] == "working"


def test_the_working_line_is_absent_once_the_turn_is_over(bed: Bed) -> None:
    bed.show("idle.txt")
    document = json_body(bed.get(f"{SESSIONS}/{OLD}/screen"))
    assert document["terminal"]["working_line"] is None and document["terminal"]["text"]


def test_the_stream_states_the_terminal_when_it_opens_and_when_it_changes(bed: Bed, stream) -> None:
    bed.show("idle.txt")
    opened = stream()
    assert opened.event("ready")["terminal"]["working_line"] is None
    bed.show_frame("frame-03.txt")
    event = opened.event("terminal")
    assert event["terminal"]["working_line"] == "Thinking… (2s · ↓ 139 tokens)"
    assert "\x1b" not in event["terminal"]["text"]
    bed.show_frame("frame-04.txt")
    assert opened.event("terminal")["terminal"]["working_line"] == "Thinking… (3s · ↓ 146 tokens)"


def test_the_screen_route_for_a_session_with_no_pane(bed: Bed) -> None:
    registry = bed.claude / "sessions" / f"{os.getpid()}.json"
    registry.write_text(json.dumps({"pid": os.getpid(), "sessionId": ENDED}), encoding="utf-8")
    document = json_body(bed.get(f"{SESSIONS}/{OLD}/screen"))
    assert document["screen"] is None and document["work_running"] is None
    assert document["terminal"] is None
    assert document["pane"]["present"] is False and document["pane"]["why"]


#: What would let a ``kyosei.serve*`` module send input to a session.
FORBIDDEN_NAMES = {
    "send_message", "send_slash_command", "clear_input", "_send_typed", "_send_paste",
    "Tmux", "resolve_tmux", "tmux",
}  # fmt: skip
FORBIDDEN_STRINGS = ("send-keys", "paste-buffer", "load-buffer")
FORBIDDEN_MODULES = ("claude_code_pane", "claude_code_transcript", "harness")
FORBIDDEN_CALLS = {"__import__", "eval", "exec", "import_module"}


def reaches_send(source: str, name: str) -> list[str]:
    """What, in one module, could reach the session layer's sending half."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source, name)):
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            found.append(f"{node.lineno}: .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES | FORBIDDEN_CALLS:
            found.append(f"{node.lineno}: {node.id}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and any(word in ast.dump(node.args[0]) for word in ("session_layer", "pane"))
        ):
            found.append(f"{node.lineno}: getattr on the session layer")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found += [f"{node.lineno}: {s!r}" for s in FORBIDDEN_STRINGS if s in node.value]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or "", *(alias.name for alias in node.names)]
            if any(m in n for n in names for m in FORBIDDEN_MODULES) or any(
                alias.name in FORBIDDEN_NAMES for alias in node.names
            ):
                found.append(f"{node.lineno}: from {node.module} import …")
        elif isinstance(node, ast.Import) and any(
            m in alias.name for alias in node.names for m in FORBIDDEN_MODULES
        ):
            found.append(f"{node.lineno}: import …")
    return found


def test_no_serve_module_can_reach_the_send_function() -> None:
    package = Path(kyosei.__file__).parent
    modules = sorted(package.glob("serve*.py"))
    assert {"serve.py", "serve_events.py", "serve_files.py", "serve_sessions.py"} <= {
        p.name for p in modules
    }
    offenders = {
        path.name: found
        for path in modules
        if (found := reaches_send(path.read_text(encoding="utf-8"), path.name))
    }
    assert offenders == {}, (
        "no kyosei.serve* module may reach the session layer's sending half until the send "
        f"routes of chunk E4 exist, and those routes change this test on purpose: {offenders}"
    )


@pytest.mark.parametrize(
    "planted",
    [
        "from . import session_layer\nsession_layer.pane.send_message(None, '', [])\n",
        "from .harness import claude_code_pane\n",
        "from .cmd_controller import Tmux\n",
        "live.tmux().run('send-keys')\n",
        "getattr(session_layer.pane, 'send_' + 'message')\n",
    ],
)
def test_the_send_check_catches_a_planted_reach(planted: str) -> None:
    assert reaches_send(planted, "planted.py")


def test_the_session_layer_itself_offers_no_send_function() -> None:
    from kyosei import session_layer

    public = {name for name in dir(session_layer) if not name.startswith("_")}
    assert "send_message" not in public and "send_slash_command" not in public


# ------------------------------------------------------------------- c5: the opt-in


@pytest.fixture
def unopted(project: Path, store_home: Path, tmp_path: Path, running) -> Client:
    """The same routes in a project that never set ``read_transcripts``."""
    register(store_home, project)
    claude = tmp_path / "claude"
    (claude / "projects" / "-work").mkdir(parents=True)
    shutil.copyfile(OLD_FILE, claude / "projects" / "-work" / f"{OLD}.jsonl")
    (claude / "sessions").mkdir(parents=True)
    entries = [_entry(OLD, state="running", seen="2026-11-09T10:00:00.000Z")]
    assert cmd_sessions.write_sessions(cmd_sessions.sessions_path(project), entries) is None
    client = running(environ={**os.environ, "CLAUDE_CONFIG_DIR": str(claude)})
    client.server.session_sample_seconds = 0.03
    return client


@pytest.mark.parametrize("route", ROUTES)
def test_every_session_route_refuses_until_the_project_opts_in(
    unopted: Client, project: Path, route: str
) -> None:
    status, _, body = unopted.get(route)
    assert status == 403, body
    document = body_json(body)
    assert "read_transcripts: true" in document["error"]
    assert "kyosei/config.yml" in document["error"]
    assert document["setting"] == "read_transcripts"

    opt_in_transcripts(project)  # the remedy the refusal names
    if route.endswith("/events"):
        conn = http.client.HTTPConnection("127.0.0.1", unopted.port, timeout=10)
        conn.putrequest("GET", route, skip_host=True)
        conn.putheader("Host", f"127.0.0.1:{unopted.port}")
        conn.endheaders()
        response = conn.getresponse()
        assert response.status == 200
        response.close()
        conn.close()
    else:
        status, _, body = unopted.get(route)
        assert status == 200, body


def test_nothing_is_read_before_the_opt_in_refusal(
    unopted: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args, **kwargs):
        raise AssertionError("sessions.json was read before the opt-in check")

    monkeypatch.setattr(serve_sessions, "read_sessions", explode)
    assert unopted.get(SESSIONS)[0] == 403


# ------------------------------------------------------------- c6: the checks, and the docs


@pytest.mark.parametrize("route", ROUTES)
def test_a_session_route_refuses_a_host_it_does_not_know(bed: Bed, route: str) -> None:
    status, _, body = bed.get(route, host="evil.example")
    assert status == 403 and "--allow-host" in body_json(body)["error"]


@pytest.mark.parametrize("route", ROUTES)
def test_a_session_route_refuses_a_foreign_origin(bed: Bed, route: str) -> None:
    status, _, body = bed.get(route, headers={"Origin": "https://evil.example"})
    assert status == 403 and "--allow-origin" in body_json(body)["error"]


@pytest.mark.parametrize("route", ROUTES)
def test_a_session_route_answers_an_origin_given_with_allow_origin(
    bed: Bed, running, route: str
) -> None:
    trusted = running(environ=bed.server.environ, allow_origins=("http://localhost:5173",))
    trusted.server.session_sample_seconds = 0.03
    conn = http.client.HTTPConnection("127.0.0.1", trusted.port, timeout=10)
    conn.putrequest("GET", route, skip_host=True)
    conn.putheader("Host", f"127.0.0.1:{trusted.port}")
    conn.putheader("Origin", "http://localhost:5173")
    conn.endheaders()
    response = conn.getresponse()
    assert response.status == 200
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    response.close()
    conn.close()


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_a_session_route_answers_get_only(bed: Bed, route: str, method: str) -> None:
    headers = {"Content-Type": "application/json"} if method == "POST" else {}
    status, response_headers, _ = bed.client.request(route, method=method, headers=headers)
    assert status == 405 and response_headers["Allow"] == "GET"


def test_an_unknown_session_sub_route_is_refused(bed: Bed) -> None:
    status, _, body = bed.get(f"{SESSIONS}/{OLD}/transcript")
    assert status == 404 and "/turns" in body_json(body)["error"]


def test_the_operating_reference_states_the_shapes() -> None:
    reference = (
        Path(__file__).parent.parent / "docs" / "reference" / "topics" / "serve-and-desktop.md"
    ).read_text(encoding="utf-8")
    for needle in (
        "/sessions/<id>/turns",
        "/sessions/<id>/events",
        "/sessions/<id>/screen",
        "`work_running`",
        "`has_pane`",
        "`cursor`",
        "reset: true",
        "`message_id`",
        "`call_id`",
        "record_uuids",
        "`read_transcripts: true`",
        "event: transcript",
        "event: reply",
        "event: screen",
        "event: session",
        "event: facts",
        "event: terminal",
        "`deliveries`",
        "`images`",
        "/sessions/<id>/images/<turn>/<index>",
        "`thinking_omitted`",
        "`working_line`",
        "`command_outputs`",
        "git_branch",
        "cache/model-catalog/",
        "`dispatch_why`",
        "total_tokens",
    ):
        assert needle in reference, needle
