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

from kyosei import cmd_sessions, device_tokens, serve, serve_sessions, session_layer, worker_tokens

from .conftest import git_init, invoke, new_record, opt_in_transcripts
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


def issue_reader(name: str = "bed-reader") -> dict[str, str]:
    """The header of a token issued with no permission, which is all a session read needs."""
    _, token = device_tokens.issue(name, [])
    return {"Authorization": f"Bearer {token}"}


class ReaderClient(Client):
    """A client whose ``get`` presents a device token, as every session read route requires
    (KYO-791). ``request`` sends only what it is given, for the tests of a missing token."""

    def __init__(self, client: Client, reader: dict[str, str]) -> None:
        super().__init__(client.server)
        self.reader = reader

    def get(self, path: str, **kwargs) -> tuple[int, dict, bytes]:
        kwargs["headers"] = {**self.reader, **(kwargs.get("headers") or {})}
        return self.request(path, **kwargs)


class Bed:
    """A served project with a recorded session, a fake tmux and a fake Claude Code home."""

    def __init__(self, project: Path, tmp_path: Path, client: ReaderClient) -> None:
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
    # The box holds a message's text from its keys until an Enter takes it (KYO-810): the
    # layer reads the screen for both. `enter-ignored` is a pane whose Enter does not take.
    # The box is kept in a statement of its own, since tests rewrite the `case "$*"` below.
    holds = tmp_path / "box-holds-text"
    (tmp_path / "typed.txt").write_text(
        "─" * 100 + "\n❯ the message's text\n" + "─" * 100 + "\n  footer\n", encoding="utf-8"
    )
    fake.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{tmp_path / "tmux.log"}"\n'
        'called="$*"\n'
        'case "$called" in\n'
        f'  *"send-keys -t"*" -l -- "*|*paste-buffer*) : > "{holds}" ;;\n'
        f'  *"send-keys -t"*" Enter") [ -e "{tmp_path / "enter-ignored"}" ] || rm -f "{holds}" ;;\n'
        "esac\n"
        'case "$*" in\n'
        f'  *list-panes*) printf "{pane_row}" ;;\n'
        f'  *capture-pane*) if [ -e "{holds}" ]; then cat "{tmp_path / "typed.txt"}"; '
        f'else cat "{tmp_path / "screen.txt"}"; fi ;;\n'
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
    client = ReaderClient(running(environ=environ), issue_reader())
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
        for key, value in {**bed.client.reader, **headers}.items():
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


def test_the_list_counts_each_sessions_prompts_from_the_turn_index(bed: Bed) -> None:
    """KYO-849: a prompt is an index line that is not a reply; a session the index does not
    hold has no count, and a line added later is counted on the next read."""
    from kyosei import cmd_turns

    assert all(row["prompts"] is None for row in json_body(bed.get(SESSIONS))["sessions"])
    index = cmd_turns.turns_path(bed.project)
    index.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"session": OLD, "role": "user", "prompt_id": "p1"},
        {"session": OLD, "role": "assistant", "prompt_id": "p1"},
        {"session": OLD, "prompt_id": "p2"},
    ]
    index.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    rows = {row["session_id"]: row for row in json_body(bed.get(SESSIONS))["sessions"]}
    assert rows[OLD]["prompts"] == 2 and rows[ENDED]["prompts"] is None
    with index.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"session": ENDED, "role": "user", "prompt_id": "p3"}) + "\n")
    rows = {row["session_id"]: row for row in json_body(bed.get(SESSIONS))["sessions"]}
    assert rows[OLD]["prompts"] == 2 and rows[ENDED]["prompts"] == 1


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
        "id", "at", "form", "text", "typed", "paste_known", "prompt_id", "images", "automatic"
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
    assert all(set(s) == {"agent_id", "tool_call_uuid", "description", "agent_type",
                          "model", "effort"}
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


# ------------------------------------------- KYO-714: the account's usage limits

LIMITS_RESET = 1_999_999_999  # 2033-05-18T03:33:19Z


def save_limits(store_home: Path, five_hour: float, seven_day_resets: int) -> None:
    store_home.mkdir(parents=True, exist_ok=True)
    (store_home / "usage-limits.json").write_text(
        json.dumps(
            {
                "read_at": "2026-11-09T12:00:00.000Z",
                "five_hour": {"used_percentage": five_hour, "resets_at": LIMITS_RESET},
                "seven_day": {"used_percentage": 41.2, "resets_at": seven_day_resets},
            }
        ),
        encoding="utf-8",
    )


def test_facts_carry_the_saved_limits_with_iso_reset_times(bed: Bed, store_home: Path) -> None:
    bed.write_transcript(bed.rows(source=FACTS_FILE))
    save_limits(store_home, 23.5, LIMITS_RESET + 3600)
    limits = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"]["limits"]
    assert limits == {
        "five_hour": {"used_percentage": 23.5, "resets_at": "2033-05-18T03:33:19.000Z"},
        "seven_day": {"used_percentage": 41.2, "resets_at": "2033-05-18T04:33:19.000Z"},
        "read_at": "2026-11-09T12:00:00.000Z",
    }


def test_facts_leave_out_a_window_whose_reset_has_passed(bed: Bed, store_home: Path) -> None:
    bed.write_transcript(bed.rows(source=FACTS_FILE))
    save_limits(store_home, 23.5, 1_000_000_000)  # 2001
    limits = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"]["limits"]
    assert set(limits) == {"five_hour", "read_at"}


def test_facts_state_no_limits_without_the_file(bed: Bed, store_home: Path) -> None:
    bed.write_transcript(bed.rows(source=FACTS_FILE))
    assert not (store_home / "usage-limits.json").exists()
    assert json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"]["limits"] is None


def test_a_changed_limits_file_is_read_again_without_a_restart(
    bed: Bed, store_home: Path, stream
) -> None:
    bed.write_transcript(bed.rows(source=FACTS_FILE))
    save_limits(store_home, 23.5, LIMITS_RESET)
    first = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"]["limits"]
    assert first["five_hour"]["used_percentage"] == 23.5
    opened = stream()
    assert opened.event("ready")["facts"]["limits"]["five_hour"]["used_percentage"] == 23.5
    save_limits(store_home, 61.0, LIMITS_RESET)
    second = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"]["limits"]
    assert second["five_hour"]["used_percentage"] == 61.0
    # The stream says so too, though no reply of this session followed the change.
    event = opened.event("facts")
    assert event["facts"]["limits"]["five_hour"]["used_percentage"] == 61.0


# ------------------------------------------- KYO-717: the weekly figure first read


def test_facts_carry_the_first_weekly_figure_and_null_without_one(
    bed: Bed, store_home: Path
) -> None:
    bed.write_transcript(bed.rows(source=FACTS_FILE))
    assert json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"]["weekly_at_start"] is None
    store_home.mkdir(parents=True, exist_ok=True)
    entry = {"weekly_at_start": {"used_percentage": 30.5, "resets_at": LIMITS_RESET}}
    (store_home / "prompt-cache.json").write_text(
        json.dumps({"sessions": {OLD: entry}}), encoding="utf-8"
    )
    found = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"]["weekly_at_start"]
    assert found == {"used_percentage": 30.5, "resets_at": "2033-05-18T03:33:19.000Z"}


# ------------------------------------------- KYO-715: the session's prompt cache


def save_cache(store_home: Path, session_id: str, *, warm: bool, expires_at: int | None) -> None:
    store_home.mkdir(parents=True, exist_ok=True)
    entry = {
        "warm": warm,
        "expires_at": expires_at,
        "recache_tokens_if_cold": 45000,
        "read_at": "2026-11-09T12:00:00.000Z",
    }
    (store_home / "prompt-cache.json").write_text(
        json.dumps({"sessions": {session_id: entry}}), encoding="utf-8"
    )


def test_facts_carry_the_sessions_saved_cache_with_an_iso_expiry(
    bed: Bed, store_home: Path
) -> None:
    bed.write_transcript(bed.rows(source=FACTS_FILE))
    save_cache(store_home, OLD, warm=True, expires_at=LIMITS_RESET)
    cache = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"]["cache"]
    assert cache == {
        "warm": True,
        "expires_at": "2033-05-18T03:33:19.000Z",
        "recache_tokens_if_cold": 45000,
        "read_at": "2026-11-09T12:00:00.000Z",
    }


def test_facts_state_no_cache_for_a_session_with_nothing_saved(
    bed: Bed, store_home: Path
) -> None:
    bed.write_transcript(bed.rows(source=FACTS_FILE))
    assert json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"]["cache"] is None
    save_cache(store_home, NEW, warm=True, expires_at=LIMITS_RESET)
    assert json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"]["cache"] is None


def test_a_changed_cache_is_read_again_without_a_restart(
    bed: Bed, store_home: Path, stream
) -> None:
    bed.write_transcript(bed.rows(source=FACTS_FILE))
    save_cache(store_home, OLD, warm=True, expires_at=LIMITS_RESET)
    assert json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"]["cache"]["warm"] is True
    opened = stream()
    assert opened.event("ready")["facts"]["cache"]["warm"] is True
    save_cache(store_home, OLD, warm=False, expires_at=None)
    second = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"]["cache"]
    assert second["warm"] is False and second["expires_at"] is None
    # The stream says so too, though the transcript did not change.
    assert opened.event("facts")["facts"]["cache"]["warm"] is False


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


# ------------------------------------------ KYO-711: a subagent's token use, read while it runs

USAGE_AGENT = "a5555555555555555"
USAGE_ROUTE = f"{SESSIONS}/{OLD}/subagents/{USAGE_AGENT}"


def _agent_row(kind: str, uuid: int, message: dict, *, sidechain: bool = True) -> str:
    row = {
        "type": kind,
        "uuid": f"00000000-0000-4000-8000-{uuid:012d}",
        "agentId": USAGE_AGENT,
        "timestamp": f"2026-11-09T12:{uuid:02d}:00.000Z",
        "sessionId": OLD,
        "message": message,
    }
    if sidechain:
        row["isSidechain"] = True
    return json.dumps(row) + "\n"


def _reply(message_id: str, inp: int, out: int, read: int, made: int) -> dict:
    usage = {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": made,
        "service_tier": "standard",
    }
    return {
        "id": message_id,
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": "working"}],
        "usage": usage,
    }


def place_usage_agent(bed: Bed, *, sidechain: bool = True) -> Path:
    """A subagent transcript of two messages; the first's two rows repeat its usage (KYO-292)."""
    directory = bed.transcripts / OLD / "subagents"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"agent-{USAGE_AGENT}.jsonl"
    prompt = {"role": "user", "content": "Count the files"}
    path.write_text(
        _agent_row("user", 1, prompt, sidechain=sidechain)
        + _agent_row("assistant", 2, _reply("msg_1", 3, 1, 1000, 200), sidechain=sidechain)
        + _agent_row("assistant", 3, _reply("msg_1", 3, 40, 1000, 200), sidechain=sidechain)
        + _agent_row("assistant", 4, _reply("msg_2", 5, 60, 1200, 50), sidechain=sidechain),
        encoding="utf-8",
    )
    return path


def test_a_subagents_usage_is_its_transcripts_sum(bed: Bed) -> None:
    path = place_usage_agent(bed)
    status, _, body = bed.get(USAGE_ROUTE)
    assert status == 200
    usage = body_json(body)["usage"]
    # msg_1 counts once, by its last row (40 output, not 1); msg_2 adds itself.
    assert usage == {
        "input_tokens": 8,
        "output_tokens": 100,
        "cache_read_tokens": 2200,
        "cache_creation_tokens": 250,
        "total": 2558,
        "latest_turn": worker_tokens.sum_transcript_usage(path).latest_turn_tokens,
    }
    # The latest turn is one message's size, which the sum of two messages exceeds.
    assert 0 < usage["latest_turn"] < usage["total"]
    counts = worker_tokens.sum_transcript_usage(path).counts
    assert usage["input_tokens"] == counts["input_tokens"]
    assert usage["output_tokens"] == counts["output_tokens"]
    assert usage["cache_read_tokens"] == counts["cache_read_input_tokens"]
    assert usage["cache_creation_tokens"] == counts["cache_creation_input_tokens"]
    assert usage["total"] == sum(counts.values())


def test_a_second_request_after_the_transcript_grows_gives_the_larger_sum(bed: Bed) -> None:
    path = place_usage_agent(bed)
    first = json_body(bed.get(USAGE_ROUTE))["usage"]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(_agent_row("assistant", 5, _reply("msg_3", 7, 70, 1500, 30)))
    second = json_body(bed.get(USAGE_ROUTE))["usage"]
    assert second["total"] == first["total"] + 7 + 70 + 1500 + 30
    assert second["output_tokens"] == first["output_tokens"] + 70
    assert second["latest_turn"] == 7 + 70 + 1500 + 30


def test_a_subagent_whose_transcript_cannot_be_read_has_no_usage_and_still_answers(
    bed: Bed,
) -> None:
    place_usage_agent(bed, sidechain=False)  # rows of no format this build recognises
    status, _, body = bed.get(USAGE_ROUTE)
    assert status == 200
    document = body_json(body)
    assert document["readable"] is False and "isSidechain" in document["why"]
    assert document["usage"] is None


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


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/name.git",
        "https://SECRET@github.com/owner/name.git",
        "git@github.com:owner/name.git",
    ],
)
def test_changes_state_the_origin_as_owner_and_name(bed: Bed, tmp_path: Path, url: str) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    subprocess.run(["git", "remote", "add", "origin", url], cwd=repo, check=True)
    record_cwd(bed, str(repo))
    status, _, body = bed.get(CHANGES_ROUTE)
    assert status == 200 and body_json(body)["remote"] == "owner/name"
    assert b"SECRET" not in body


def test_changes_state_no_remote_for_a_repository_without_an_origin(
    bed: Bed, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    record_cwd(bed, str(repo))
    document = json_body(bed.get(CHANGES_ROUTE))
    assert document["remote"] is None and document["why"] is None


def test_changes_state_how_many_commits_the_upstream_lacks(bed: Bed, tmp_path: Path) -> None:
    upstream = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "-q", "--bare", str(upstream)], check=True)
    repo = tmp_path / "repo"
    init_repo(repo)
    who = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")
    env = {**os.environ, **dict.fromkeys(who, "t")}

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, env=env, check=True)

    record_cwd(bed, str(repo))
    assert json_body(bed.get(CHANGES_ROUTE))["ahead"] is None  # no upstream
    git("remote", "add", "origin", str(upstream))
    git("push", "-q", "-u", "origin", "HEAD")
    assert json_body(bed.get(CHANGES_ROUTE))["ahead"] == 0
    for name in ("a", "b"):
        (repo / f"{name}.txt").write_text(name, encoding="utf-8")
        git("add", f"{name}.txt")
        git("commit", "-q", "-m", name)
    assert json_body(bed.get(CHANGES_ROUTE))["ahead"] == 2


def test_changes_for_a_working_directory_that_is_gone_say_so(bed: Bed) -> None:
    document = json_body(bed.get(CHANGES_ROUTE))  # the bed records /work, which does not exist
    assert document["root"] is None and document["remote"] is None and "/work" in document["why"]
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
    "send_message", "send_slash_command", "send_interrupt", "clear_input", "_send_typed",
    "_send_paste", "Tmux", "resolve_tmux", "tmux",
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
    assert not {"send_message", "send_slash_command", "send_interrupt"} & public


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
    client = ReaderClient(
        running(environ={**os.environ, "CLAUDE_CONFIG_DIR": str(claude)}), issue_reader()
    )
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
        conn.putheader("Authorization", unopted.reader["Authorization"])
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
    conn.putheader("Authorization", bed.client.reader["Authorization"])
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
        "event: error",
        "event: resumed",
        "`stopped: true`",
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


# ------------------------------------- KYO-701: lines changed by a session and its subagents

LINES_FILE = FIXTURES / "lines-total.synthetic.jsonl"


def place_lines_session(bed: Bed) -> list[dict]:
    """The fixture's session rows in the bed's transcript, and each subagent's in its file."""
    rows = [json.loads(line) for line in LINES_FILE.read_text(encoding="utf-8").splitlines()]
    bed.write_transcript([json.dumps(row) + "\n" for row in rows if not row["isSidechain"]])
    agents: dict[str, list[dict]] = {}
    for row in rows:
        if row["isSidechain"]:
            agents.setdefault(row["agentId"], []).append(row)
    directory = bed.transcripts / OLD / "subagents"
    directory.mkdir(parents=True, exist_ok=True)
    for agent, held in agents.items():
        text = "".join(json.dumps(row) + "\n" for row in held)
        (directory / f"agent-{agent}.jsonl").write_text(text, encoding="utf-8")
    return rows


def test_facts_state_the_lines_of_the_session_and_its_subagents(bed: Bed) -> None:
    place_lines_session(bed)
    facts = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))["facts"]
    assert facts["lines"] == {"added": 11, "removed": 3}


def test_the_stream_states_lines_a_running_subagent_wrote(bed: Bed, stream) -> None:
    place_lines_session(bed)
    opened = stream()
    assert opened.event("ready")["facts"]["lines"] == {"added": 11, "removed": 3}
    agent = bed.transcripts / OLD / "subagents" / "agent-a4444444444444444.jsonl"
    row = {"isSidechain": True, "toolUseResult": {"structuredPatch": [{"lines": ["+x", "-y"]}]}}
    with agent.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    assert opened.event("facts")["facts"]["lines"] == {"added": 12, "removed": 4}


# ------------------------------------------ KYO-726: a subagent's model and effort, from its file


def _agent_with_effort(bed: Bed, effort: str | None) -> None:
    directory = bed.transcripts / OLD / "subagents"
    directory.mkdir(parents=True, exist_ok=True)
    row = json.loads(_agent_row("assistant", 2, _reply("msg_1", 3, 1, 10, 20)))
    row["message"]["model"] = "claude-fable-5-2"
    if effort is not None:
        row["effort"] = effort
    (directory / f"agent-{USAGE_AGENT}.jsonl").write_text(
        _agent_row("user", 1, {"role": "user", "content": "Count"}) + json.dumps(row) + "\n",
        encoding="utf-8",
    )


def test_the_subagent_route_states_the_model_and_effort_its_transcript_records(bed: Bed) -> None:
    _agent_with_effort(bed, "medium")
    document = json_body(bed.get(USAGE_ROUTE))
    assert document["model"] == "claude-fable-5-2"
    assert document["effort"] == "medium"


def test_a_transcript_without_an_effort_states_none_and_one_without_a_reply_neither(
    bed: Bed,
) -> None:
    _agent_with_effort(bed, None)
    document = json_body(bed.get(USAGE_ROUTE))
    assert document["model"] == "claude-fable-5-2"
    assert document["effort"] is None
    path = bed.transcripts / OLD / "subagents" / f"agent-{USAGE_AGENT}.jsonl"
    path.write_text(_agent_row("user", 1, {"role": "user", "content": "Count"}), encoding="utf-8")
    document = json_body(bed.get(USAGE_ROUTE))
    assert (document["model"], document["effort"]) == (None, None)


def test_turns_state_a_listed_subagents_model_and_effort(bed: Bed) -> None:
    from kyosei.harness import claude_code_transcript as transcript

    # The pair fixture lists AGENT; its file is placed by the earlier KYO-693 tests' helper.
    place_agent = bed.transcripts / OLD / "subagents"
    place_agent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(AGENT_FILE, place_agent / AGENT_FILE.name)
    turns = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))
    listed = {item["agent_id"]: item for item in turns["subagents"]}
    expected = transcript.subagent_model_effort(place_agent / AGENT_FILE.name)
    assert expected[0] == "claude-haiku-5-5"
    assert (listed[AGENT]["model"], listed[AGENT]["effort"]) == expected


# --------------------------------------------- KYO-728: a reader that has stopped reading


def _next_event(opened: Stream, *, within: float = 10.0) -> tuple[str, dict]:
    """The next event of any name, skipping keepalives."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        block = opened.block()
        names = [line for line in block if line.startswith("event: ")]
        if names:
            data = next(line for line in block if line.startswith("data: "))
            return names[0].removeprefix("event: "), json.loads(data.removeprefix("data: "))
    raise TimeoutError("no event arrived")


def _ends(opened: Stream, *, within: float = 10.0) -> bool:
    """Whether the server ends the stream, having sent no further event."""
    deadline = time.monotonic() + within
    try:
        while time.monotonic() < deadline:
            assert not any(line.startswith("event: ") for line in opened.block())
    except EOFError:
        return True
    return False


def _log(bed: Bed) -> str:
    return bed.server.log_stream.stream.getvalue()


def test_a_reader_that_dies_is_logged_its_subscribers_told_and_the_next_gets_a_new_reader(
    bed: Bed, stream, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = bed.rows()
    bed.write_transcript(rows[: len(rows) // 2])
    one = stream()
    one.event("ready")
    hub = bed.server.session_hub
    first = hub.samplers()[0]

    def refresh(self):
        raise ValueError("a row of a shape nothing expected")

    # Not one of the errors a pass is expected to fail with, so it ends the reader's thread.
    monkeypatch.setattr(session_layer.LiveSession, "refresh", refresh)
    said = one.event("error")
    assert said["stopped"] is True
    assert "ValueError: a row of a shape nothing expected" in said["error"]
    assert _ends(one), "the stream stayed open on a reader that had died"
    _wait_for(lambda: _sampler_threads() == 0)
    assert hub.samplers() == []

    logged = _log(bed)
    assert "Traceback (most recent call last)" in logged
    assert "ValueError: a row of a shape nothing expected" in logged
    dated = next(line for line in logged.splitlines() if "the reader of session" in line)
    assert dated[:2] == "20" and f"Z kyo serve: the reader of session {OLD} stopped" in dated

    monkeypatch.undo()
    two = stream()
    two.event("ready")
    assert hub.samplers()[0] is not first
    bed.append(rows[len(rows) // 2 :])
    assert two.event("transcript")["session_id"] == OLD


def test_rows_read_before_a_capture_raised_arrive_on_the_next_pass(
    bed: Bed, stream, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = bed.rows()
    bed.write_transcript(rows[: len(rows) // 2])
    opened = stream()
    ready = opened.event("ready")
    real = session_layer.pane.capture
    state = {"failing": True, "failed": 0}

    def capture(tmux, pane_id):
        if state["failing"]:
            state["failed"] += 1
            raise OSError("the pane could not be captured")
        return real(tmux, pane_id)

    monkeypatch.setattr(session_layer.pane, "capture", capture)
    assert "the pane could not be captured" in opened.event("error")["error"]
    # The rows land while every pass fails, so a failing pass is the one that reads them.
    bed.append(rows[len(rows) // 2 :])
    failed = state["failed"]
    _wait_for(lambda: state["failed"] >= failed + 3)

    # A subscriber that joins meanwhile is told, after the state the reader last read.
    late = stream()
    assert late.event("ready")["cursor"] == ready["cursor"]
    name, said = _next_event(late)
    assert name == "error" and "stopped" not in said

    state["failing"] = False
    # KYO-728.c4: the stream says reading has resumed, and then carries what was read.
    name, said = _next_event(opened)
    assert (name, said) == ("resumed", {"session_id": OLD})
    name, event = _next_event(opened)
    assert name == "transcript" and event["cursor"] != ready["cursor"]
    whole = json_body(bed.get(f"{SESSIONS}/{OLD}/turns"))
    assert event["cursor"] == whole["cursor"], "rows read in the failed pass were not sent"
    assert _next_event(late)[0] == "resumed"


def test_a_stream_that_never_failed_carries_no_resumed(bed: Bed, stream) -> None:
    rows = bed.rows()
    bed.write_transcript(rows[: len(rows) // 2])
    opened = stream()
    opened.event("ready")
    bed.append(rows[len(rows) // 2 :])
    assert _next_event(opened)[0] == "transcript"


def _project_sessions(bed: Bed) -> serve_sessions.ProjectSessions:
    return serve_sessions.ProjectSessions(str(bed.project), bed.server.environ)


def test_a_new_subscriber_is_not_answered_from_a_reader_that_has_returned(bed: Bed) -> None:
    # Subscribed without a stream, so that only the new subscription can notice the reader.
    hub = bed.server.session_hub
    project = _project_sessions(bed)
    one = hub.subscribe(project, OLD)
    try:
        stopped = one.sampler
        stopped.stop.set()
        _wait_for(lambda: _sampler_threads() == 0)
        assert hub.samplers() == [stopped], "the stopped reader left the hub by itself"

        two = hub.subscribe(project, OLD)
        try:
            assert two.sampler is not stopped and hub.samplers() == [two.sampler]
            assert b"event: ready" in two.queue.get_nowait()
            ticks = two.sampler.ticks
            _wait_for(lambda: two.sampler.ticks > ticks + 2)
            # The subscriber the stopped reader still had is told, and its stream is ended.
            one.queue.get_nowait()  # its own ready
            said = one.queue.get_nowait()
            assert b"event: error" in said and b'"stopped": true' in said
            assert one.ended and b"its thread has ended" in said
            assert "its thread has ended" in _log(bed)
        finally:
            hub.unsubscribe(two)
    finally:
        hub.unsubscribe(one)
    _wait_for(lambda: hub.samplers() == [] and _sampler_threads() == 0)


def test_a_reader_stuck_in_a_pass_is_replaced_and_its_stream_ended(bed: Bed, stream) -> None:
    bed.server.session_stall_seconds = 0.3
    rows = bed.rows()
    bed.write_transcript(rows[: len(rows) // 2])
    one = stream()
    one.event("ready")
    hub = bed.server.session_hub
    stuck = hub.samplers()[0]
    release = threading.Event()
    reading = stuck._live.refresh

    def refresh():
        release.wait(20)
        return reading()

    stuck._live.refresh = refresh
    try:
        # The stream itself notices: its keepalives never depended on the reader.
        said = one.event("error")
        assert said["stopped"] is True and "has not finished" in said["error"]
        assert _ends(one)
        assert hub.samplers() == []

        two = stream()
        two.event("ready")
        assert hub.samplers()[0] is not stuck
        bed.append(rows[len(rows) // 2 :])
        assert two.event("transcript")["session_id"] == OLD
    finally:
        release.set()
    # Released, the stuck reader's thread ends and sends nothing: one reader remains.
    _wait_for(lambda: _sampler_threads() == 1)


def test_a_reader_that_is_reading_is_not_retired(bed: Bed, stream) -> None:
    bed.server.session_stall_seconds = 0.3
    opened = stream()
    opened.event("ready")
    sampler = bed.server.session_hub.samplers()[0]
    time.sleep(1.0)
    assert bed.server.session_hub.samplers() == [sampler] and not sampler.retired
    again = stream()
    again.event("ready")
    assert bed.server.session_hub.samplers() == [sampler]


# ------------------------------------------------ KYO-791: a device token on every session read

#: Every route ``_session_route`` answers: the session read routes, the stream included.
READ_ROUTES = [*ROUTES, IMAGE_ROUTE, AGENT_ROUTE]


def read_bed(bed: Bed) -> Bed:
    """The bed holding what the image and the subagent routes serve."""
    show_delivered(bed)
    place_agent(bed)
    return bed


def status_of(bed: Bed, route: str, headers: dict[str, str]) -> tuple[int, dict, dict | None]:
    """The status, the headers and, for a refusal, the document. A stream that opens is closed
    once its status has been read."""
    conn = http.client.HTTPConnection("127.0.0.1", bed.client.port, timeout=15)
    try:
        conn.putrequest("GET", route, skip_host=True)
        conn.putheader("Host", f"127.0.0.1:{bed.client.port}")
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.endheaders()
        response = conn.getresponse()
        document = None if response.status == 200 else body_json(response.read())
        response.close()
        return response.status, dict(response.headers), document
    finally:
        conn.close()


def test_the_read_routes_under_test_are_all_the_session_route_serves() -> None:
    """``READ_ROUTES`` holds one route for each thing ``_session_route`` serves, so a read
    route added to the server's tables without a row here fails this test."""
    tested = {(route.removeprefix(SESSIONS).split("/") + ["", ""])[2] for route in READ_ROUTES}
    assert tested == {
        "",  # the list
        *serve.SESSION_ROUTES,
        serve.SESSION_IMAGE_ROUTE,
        serve.SESSION_SUBAGENT_ROUTE,
    }


@pytest.mark.parametrize("route", READ_ROUTES)
def test_a_session_read_without_a_token_is_refused_and_issuing_one_clears_it(
    bed: Bed, route: str
) -> None:
    read_bed(bed)
    status, headers, refused = status_of(bed, route, {})
    assert status == 401 and refused["reason"] == "no-token", refused
    assert headers["WWW-Authenticate"] == "Bearer"
    assert "`kyo devices issue <name>`" in refused["error"]
    assert "Authorization: Bearer <token>" in refused["error"]

    result = invoke(["devices", "issue", "laptop", "--json"])  # the remedy, as it is written
    assert result.code == 0, result
    token = json.loads(result.out)["token"]
    assert status_of(bed, route, {"Authorization": f"Bearer {token}"})[0] == 200


@pytest.mark.parametrize("route", READ_ROUTES)
@pytest.mark.parametrize("permission", [None, *device_tokens.PERMISSIONS])
def test_a_token_of_any_permission_reads_a_session(
    bed: Bed, route: str, permission: str | None
) -> None:
    read_bed(bed)
    _, token = device_tokens.issue("laptop", [permission] if permission else [])
    assert status_of(bed, route, {"Authorization": f"Bearer {token}"})[0] == 200


@pytest.mark.parametrize("route", READ_ROUTES)
def test_a_token_this_machine_did_not_issue_reads_no_session(bed: Bed, route: str) -> None:
    read_bed(bed)
    for header in ("Bearer kyo_not-one-of-ours", "Basic abc", "Bearer "):
        status, headers, refused = status_of(bed, route, {"Authorization": header})
        assert status == 401, refused
        assert headers["WWW-Authenticate"] == "Bearer"
    status, _, refused = status_of(bed, route, {"Authorization": "Bearer kyo_not-one-of-ours"})
    assert refused["reason"] == "unknown-token"
    assert "`kyo devices issue <name>`" in refused["error"]


@pytest.mark.parametrize("route", READ_ROUTES)
def test_a_revoked_token_reads_no_session_and_a_new_one_does(bed: Bed, route: str) -> None:
    read_bed(bed)
    _, token = device_tokens.issue("laptop", [device_tokens.PERMISSION_SEND])
    presented = {"Authorization": f"Bearer {token}"}
    assert status_of(bed, route, presented)[0] == 200
    device_tokens.revoke("laptop")  # read afresh on each request, so no restart
    status, _, refused = status_of(bed, route, presented)
    assert status == 401 and refused["reason"] == "revoked-token", refused
    assert "laptop" in refused["error"] and "`kyo devices issue <name>`" in refused["error"]

    result = invoke(["devices", "issue", "laptop", "--json"])  # the remedy the refusal names
    assert result.code == 0, result
    again = json.loads(result.out)["token"]
    assert status_of(bed, route, {"Authorization": f"Bearer {again}"})[0] == 200


def test_a_refused_read_says_nothing_of_the_project(unopted: ReaderClient) -> None:
    """The token is asked for first: without one, a project that has not opted in and a
    project the server does not serve answer as an opted-in one does."""
    for path in (SESSIONS, "/v1/projects/nobody/sessions"):
        status, _, body = unopted.request(path)
        assert status == 401 and body_json(body)["reason"] == "no-token", body
    assert unopted.get(SESSIONS)[0] == 403
    assert unopted.get("/v1/projects/nobody/sessions")[0] == 404


def test_an_unreadable_token_file_refuses_every_read_by_name(bed: Bed, store_home: Path) -> None:
    path = store_home / device_tokens.TOKENS_FILE
    kept = path.read_text(encoding="utf-8")
    path.write_text("{not json", encoding="utf-8")
    status, _, body = bed.get(SESSIONS)
    assert status == 500 and body_json(body)["reason"] == "tokens-unreadable", body
    path.write_text(kept, encoding="utf-8")
    assert bed.get(SESSIONS)[0] == 200


def test_records_documents_and_ideas_are_still_read_without_a_token(bed: Bed) -> None:
    """Only the session routes ask for a token (KYO-791.c2): what a project's records,
    documents and idea files answer to a request carrying none is unchanged."""
    (bed.project / "docs").mkdir(exist_ok=True)
    (bed.project / "docs" / "note.md").write_text("# A document\n", encoding="utf-8")
    for path in (
        "/v1/version",
        "/v1/projects",
        f"{BASE}/cmd/list",
        f"{BASE}/cmd/sessions",
        f"{BASE}/docs/docs/note.md",
        f"{BASE}/ideas",
    ):
        status, _, body = bed.client.request(path)
        assert status == 200, (path, body)
    # The same request, on a session route, is the one that is refused.
    assert bed.client.request(SESSIONS)[0] == 401


# ------------------------------------------------- KYO-812: the search of what was said

SEARCH = f"{BASE}/search"
#: Words of a reply in ``OLD_FILE``, and of the prompt it answers.
REPLY_PHRASE = "a fixed structure"
PROMPT_PHRASE = "how lighthouses work"


def search_route(phrase: str, **more: str) -> str:
    return f"{SEARCH}?{urllib.parse.urlencode({'q': phrase, **more})}"


def test_the_search_route_finds_a_reply_and_a_prompt_and_names_where(bed: Bed) -> None:
    status, _, body = bed.get(search_route(REPLY_PHRASE))
    assert status == 200, body
    document = body_json(body)
    (reply,) = document["results"]
    assert (reply["project"], reply["session_id"], reply["role"]) == ("proj", OLD, "assistant")
    assert {"text": "a fixed structure", "match": True} in reply["excerpt"]
    # The result's place is one the chat's own objects carry: a reply's record and message.
    turns = body_json(bed.get(f"{SESSIONS}/{OLD}/turns")[2])
    drawn = {(r["uuid"], r["message_id"]): r for r in turns["replies"]}
    assert drawn[(reply["message"], reply["message_id"])]["at"] == reply["at"]

    (prompt,) = body_json(bed.get(search_route(PROMPT_PHRASE.upper()))[2])["results"]
    assert prompt["role"] == "person"
    assert prompt["message"] in {turn["id"] for turn in turns["turns"]}


def test_the_search_route_does_not_search_a_tool_result(bed: Bed) -> None:
    turns = body_json(bed.get(f"{SESSIONS}/{OLD}/turns")[2])
    result = next(c["result"]["text"] for c in turns["tool_calls"] if c["name"] == "Read")
    assert "alpha\n2\tbeta" in result
    assert body_json(bed.get(search_route("alpha 2 beta"))[2])["results"] == []


def test_the_search_route_is_refused_without_a_token_and_answers_with_one(bed: Bed) -> None:
    route = search_route(REPLY_PHRASE)
    status, headers, refused = status_of(bed, route, {})
    assert status == 401 and refused["reason"] == "no-token", refused
    assert headers["WWW-Authenticate"] == "Bearer"
    assert "a fixed structure" not in json.dumps(refused)

    result = invoke(["devices", "issue", "search-laptop", "--json"])  # the remedy, as written
    assert result.code == 0, result
    token = json.loads(result.out)["token"]
    assert status_of(bed, route, {"Authorization": f"Bearer {token}"})[0] == 200
    unknown = {"Authorization": "Bearer kyo_dev_not-one-this-machine-issued"}
    assert status_of(bed, route, unknown)[0] == 401


def test_the_search_route_refuses_until_the_project_opts_in(unopted: Client, project: Path) -> None:
    status, _, body = unopted.get(search_route(REPLY_PHRASE))
    document = body_json(body)
    assert status == 403 and "read_transcripts: true" in document["error"], document
    assert "results" not in document
    opt_in_transcripts(project)  # the remedy, as it is written
    status, _, body = unopted.get(search_route(REPLY_PHRASE))
    assert status == 200 and len(body_json(body)["results"]) == 1


def test_the_search_route_pages_and_refuses_what_it_does_not_take(bed: Bed) -> None:
    every = body_json(bed.get(search_route("ok", limit="100"))[2])
    assert len(every["results"]) > 2 and every["more"] is False
    first = body_json(bed.get(search_route("ok", limit="2"))[2])
    assert first["results"] == every["results"][:2] and first["more"] is True
    second = body_json(bed.get(search_route("ok", limit="2", cursor=first["cursor"]))[2])
    assert second["results"] == every["results"][2:4]

    for route, named in (
        (f"{SEARCH}?q=ok&session=x", "?session is not a query the search route takes"),
        (f"{SEARCH}?q=ok&limit=many", "?limit is a number"),
        (f"{SEARCH}?q=ok&limit=1000", "between 1 and 100"),
        (f"{SEARCH}?q=ok&cursor=zzz", "cursor"),
        (SEARCH, "needs a phrase"),
    ):
        status, _, body = bed.get(route)
        assert status == 400 and named in body_json(body)["error"], (route, body)
    status, _, body = bed.client.request(
        SEARCH, method="POST", headers={**bed.client.reader, "Content-Type": "application/json"}
    )
    assert status == 405, body


def test_the_request_log_does_not_hold_the_phrase_searched_for(bed: Bed) -> None:
    assert bed.get(search_route("a fixed structure"))[0] == 200
    assert bed.get(f"{SESSIONS}/{OLD}/turns?cursor=")[0] in (200, 400)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        written = bed.server.log_stream.stream.getvalue()
        if f"{SESSIONS}/{OLD}/turns" in written:
            break
        time.sleep(0.02)
    assert f'"GET {SEARCH}?[not logged] HTTP/1.1" 200' in written, written
    assert "fixed" not in written and "structure" not in written
    assert f"{SESSIONS}/{OLD}/turns?cursor=" in written  # another route's query is logged


def test_a_session_sessions_json_dropped_is_served_from_the_archive_and_searched(
    bed: Bed, project: Path, store_home: Path
) -> None:
    """``sessions.json`` keeps the most recent sessions only. An older one the project's
    archive holds is still the project's, so a search result in it can be opened."""
    from kyosei import transcript_archive
    from kyosei.config import load_config

    older = bed.transcripts / f"{NEW}.jsonl"
    shutil.copyfile(NEW_FILE, older)
    assert bed.get(f"{SESSIONS}/{NEW}/turns")[0] == 404  # in no file of this project's
    archive = transcript_archive.archive_dir(load_config(project), bed.server.environ)
    transcript_archive.archive_session(older, archive)
    older.unlink()

    status, _, body = bed.get(f"{SESSIONS}/{NEW}/turns")
    assert status == 200, body
    turns = body_json(body)
    assert turns["replies"] or turns["turns"]
    words = (turns["replies"] or turns["turns"])[0]["text"].split("\n")[0][:24]
    found = body_json(bed.get(search_route(words))[2])["results"]
    assert NEW in {result["session_id"] for result in found}
    # A session in none of the project's files is still refused, whatever Claude Code holds.
    shutil.copyfile(OLD_FILE, bed.transcripts / f"{ELSEWHERE}.jsonl")
    assert bed.get(f"{SESSIONS}/{ELSEWHERE}/turns")[0] == 404
