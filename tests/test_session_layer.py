"""The session layer of chunk E2 (KYO-688), against recorded samples.

The samples under ``tests/fixtures/session_layer/`` were recorded from throwaway sessions
of Claude Code 2.1.319 (Haiku 5.5) on a private tmux socket, and scrubbed by a script that
keeps a fixed list of record fields, keeps only the screens' synthetic text, and rewrites
paths under ``/work``. Two screens are not recordings, because a throwaway must not raise
or answer them, and say so in their names: ``trust-dialog.synthetic.txt`` is the dialog as
``tests/test_kyo_controller.py`` already measured it, and
``idle-suggestion.reconstructed.txt`` is the input line's escape bytes as measured during
this work, placed under a recorded rule. The two files under ``facts/`` are synthetic too
(KYO-691): each row has the keys measured on 2.1.319 and text written for the test, including
a reply whose three token counts add to the 226,458 Claude Code's context panel showed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from kyosei import cmd_controller, session_layer
from kyosei.config import load_config
from kyosei.errors import KyoseiError
from kyosei.harness import claude_code_pane as pane
from kyosei.harness import claude_code_transcript as transcript

from .conftest import opt_in_transcripts

FIXTURES = Path(__file__).parent / "fixtures" / "session_layer"
OLD = "108d6543-12e9-4ae4-85fb-f303424bfa6a"
NEW = "718208ce-6423-4004-9771-10cfb6d615e8"
OLD_FILE = FIXTURES / "pair" / f"{OLD}.jsonl"
NEW_FILE = FIXTURES / "pair" / f"{NEW}.jsonl"
SCREENS = FIXTURES / "screens"
SRC = Path(__file__).parent.parent / "src" / "kyosei"


def screen(name: str) -> str:
    return (SCREENS / name).read_text(encoding="utf-8")


def view(path: Path = OLD_FILE) -> transcript.SessionTranscript:
    reader = transcript.SessionTranscript(path)
    reader.read()
    return reader


# ------------------------------------------------------------- c1: the transcript adapter


def test_person_turns_include_mid_turn_and_exclude_peers() -> None:
    reader = view()
    by_form: dict[str, list[transcript.Turn]] = {}
    for turn in reader.turns:
        by_form.setdefault(turn.form, []).append(turn)
    typed = [t.full_text for t in by_form[transcript.FORM_TYPED]]
    assert typed[0].startswith("Write three paragraphs")
    mid = by_form[transcript.FORM_MID_TURN]
    assert [t.full_text for t in mid] == [
        "Also, mid-turn message: end your final reply with the word purple."
    ]
    assert any(t.full_text == "Blue" for t in by_form[transcript.FORM_ANSWER])
    assert not any("peer report" in t.full_text for t in reader.turns)
    # The recording does hold peers' messages, both delivered and queued mid-turn.
    rows = [json.loads(line) for line in OLD_FILE.read_text(encoding="utf-8").splitlines()]
    assert any((r.get("origin") or {}).get("kind") == "peer" for r in rows)
    assert any(
        ((r.get("attachment") or {}).get("origin") or {}).get("kind") == "peer" for r in rows
    )
    # Every turn is addressed by its record's uuid.
    uuids = {r["uuid"] for r in rows if "uuid" in r}
    assert all(t.id in uuids for t in reader.turns)


def test_replies_carry_usage_and_are_addressed_by_record() -> None:
    reader = view()
    rows = [json.loads(line) for line in OLD_FILE.read_text(encoding="utf-8").splitlines()]
    replies = reader.replies
    assert replies and all(r.uuid in {x.get("uuid") for x in rows} for r in replies)
    prose = next(r for r in replies if r.text.startswith("A lighthouse is"))
    assert prose.usage == transcript.Usage(
        prose.usage.input_tokens,
        prose.usage.output_tokens,
        prose.usage.cache_read_tokens,
        prose.usage.cache_creation_tokens,
    )
    assert prose.usage.output_tokens > 0
    assert prose.model == "claude-haiku-5-5"
    # A message of thinking and text blocks is one reply over two records, its usage once.
    assert len(prose.record_uuids) == 2
    assert len({r.message_id for r in replies}) == len(replies)


def test_tool_calls_pair_with_their_results() -> None:
    reader = view()
    read = next(c for c in reader.tool_calls if c.name == "Read")
    assert read.result is not None
    assert "alpha" in read.result.text
    assert not read.result.is_error
    assert read.uuid != read.result.uuid
    bash = next(c for c in reader.tool_calls if c.name == "Bash" and "bg-done" in str(c.input))
    assert bash.result is not None and "background" in bash.result.text


def test_subagent_references() -> None:
    reader = view()
    agents = {ref.agent_id: ref for ref in reader.subagents}
    ref = agents["a731dd6521cb44bd4"]
    assert ref.description == "Run sleep and reply finished"
    assert ref.agent_type == "general-purpose"
    assert any(c.uuid == ref.tool_call_uuid and c.name == "Agent" for c in reader.tool_calls)


def _strip_path(row: dict, path: tuple[str, ...]) -> None:
    node = row
    for key in path[:-1]:
        node = node.get(key)
        if not isinstance(node, dict):
            return
    node.pop(path[-1], None)


#: Every record field the adapter depends on. Each entry names a field and what the
#: adapter returns without it.
DEPENDED_FIELDS = [
    ("type",),
    ("uuid",),
    ("timestamp",),
    ("message", "id"),
    ("message", "content"),
    ("message", "usage"),
    ("message", "usage", "output_tokens"),
    ("message", "model"),
    ("origin",),
    ("origin", "kind"),
    ("toolUseResult",),
    ("toolUseResult", "agentId"),
    ("attachment", "type"),
    ("attachment", "origin"),
    ("attachment", "commandMode"),
    ("attachment", "prompt"),
]


def _snapshot(reader: transcript.SessionTranscript) -> tuple:
    return (reader.turns, reader.replies, reader.tool_calls, reader.subagents, reader.readable)


@pytest.mark.parametrize("field", DEPENDED_FIELDS, ids=".".join)
def test_a_removed_field_changes_the_result(field: tuple[str, ...], tmp_path: Path) -> None:
    # Fails if a recorded sample lacks a field the adapter reads: a re-recorded sample
    # from a release that dropped or renamed one breaks here, and the repair is in the
    # adapter alone.
    baseline = _snapshot(view())
    rows = [json.loads(line) for line in OLD_FILE.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        _strip_path(row, field)
    without = tmp_path / f"{OLD}.jsonl"
    without.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert _snapshot(view(without)) != baseline


def test_no_module_outside_the_adapter_reads_a_transcript_row() -> None:
    fields = (
        '"toolUseResult"',
        '"isSidechain"',
        '"queued_command"',
        '"tool_use_id"',
        '"commandMode"',
        '"parentUuid"',
    )
    offenders = [
        path
        for path in SRC.rglob("*.py")
        if path.name != "claude_code_transcript.py"
        and any(name in path.read_text(encoding="utf-8") for name in fields)
    ]
    assert offenders == []


def test_a_transcript_of_no_known_shape_is_unreadable(tmp_path: Path) -> None:
    file = tmp_path / "x.jsonl"
    file.write_text(json.dumps({"type": "user", "uuid": "u", "timestamp": "t"}) + "\n")
    reader = transcript.SessionTranscript(file)
    reader.read()
    assert not reader.readable
    assert "format" in reader.why


# --------------------------------------------------------- c2: growth, and /clear


def test_a_partial_last_line_is_held_until_it_is_whole(tmp_path: Path) -> None:
    data = OLD_FILE.read_bytes()
    lines = data.splitlines(keepends=True)
    file = tmp_path / f"{OLD}.jsonl"
    # Cut mid-way through a line, and through a multi-byte character when there is one.
    chars = next(i for i, line in enumerate(lines) if "A lighthouse" in line.decode())
    cut = data.index(lines[chars]) + len(lines[chars]) // 2
    file.write_bytes(data[:cut])
    reader = transcript.SessionTranscript(file)
    reader.read()
    assert reader.offset == sum(len(line) for line in lines[:chars])
    assert not any(r.text.startswith("A lighthouse") for r in reader.replies)
    with file.open("ab") as handle:
        handle.write(data[cut:])
    delta = reader.read()
    assert any(r.text.startswith("A lighthouse") for r in delta.replies)
    # Read in two steps, the session is the one read whole.
    assert _snapshot(reader) == _snapshot(view())
    assert reader.read().replies == []


def test_a_multibyte_split_is_a_partial_line(tmp_path: Path) -> None:
    file = tmp_path / "s.jsonl"
    encoded = json.dumps({"type": "user", "uuid": "é", "timestamp": "t"}, ensure_ascii=False)
    raw = encoded.encode("utf-8")
    file.write_bytes(raw[: raw.index("é".encode()) + 1])
    reader = transcript.SessionTranscript(file)
    assert reader.read() == transcript.TranscriptDelta()
    assert reader.offset == 0


def test_a_rewritten_file_is_read_again_from_the_start(tmp_path: Path) -> None:
    file = tmp_path / "s.jsonl"
    data = OLD_FILE.read_bytes()
    file.write_bytes(data)
    reader = transcript.SessionTranscript(file)
    reader.read()
    first_line_end = data.index(b"\n") + 1
    file.write_bytes(data[:first_line_end])
    reader.read()
    assert reader.offset == first_line_end


def test_clear_is_followed_to_the_new_transcript_and_id(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    old, new = project / OLD_FILE.name, project / NEW_FILE.name
    shutil.copy(OLD_FILE, old)
    shutil.copy(NEW_FILE, new)
    (project / "unrelated.jsonl").write_text(OLD + "\n", encoding="utf-8")
    assert transcript.clear_successor(old) == transcript.ClearLink(NEW, new)
    assert transcript.clear_successor(new) is None


def test_clear_requires_the_clear_command_in_the_new_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy(OLD_FILE, project / OLD_FILE.name)
    rows = [json.loads(line) for line in NEW_FILE.read_text(encoding="utf-8").splitlines()]
    kept = [r for r in rows if "/clear" not in str((r.get("message") or {}).get("content"))]
    (project / NEW_FILE.name).write_text(
        "".join(json.dumps(r) + "\n" for r in kept), encoding="utf-8"
    )
    assert transcript.clear_successor(project / OLD_FILE.name) is None


def _write_registry(config: Path, pid: int, session_id: str, status: str = "idle") -> None:
    directory = config / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{pid}.json").write_text(
        json.dumps({"pid": pid, "sessionId": session_id, "status": status, "cwd": "/work/sample"}),
        encoding="utf-8",
    )


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> session_layer.LiveSession:
    config = tmp_path / "claude"
    project = config / "projects" / "sample"
    project.mkdir(parents=True)
    shutil.copy(OLD_FILE, project / OLD_FILE.name)
    shutil.copy(NEW_FILE, project / NEW_FILE.name)
    env = {"PATH": "/usr/bin:/bin"}
    monkeypatch.setattr(
        session_layer.pane,
        "find_pane",
        lambda *a, **k: pane.PaneLookup(None, "no pane in this test"),
    )
    return session_layer.LiveSession(
        OLD,
        project / OLD_FILE.name,
        config_dir=config,
        environ=env,
        log_dir=tmp_path / "unrecognised",
    )


def test_refresh_follows_clear_by_the_process_registry(
    live: session_layer.LiveSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_registry(live.config_dir, 1645, OLD)
    live.pane_lookup = pane.PaneLookup(pane.PaneRef(("-S", "s"), "%0", "tw", 1645), "test")
    monkeypatch.setattr(session_layer.pane, "capture", lambda *a: screen("idle.txt"))
    monkeypatch.setattr(session_layer.LiveSession, "tmux", lambda self: None)
    update = live.refresh()
    assert not update.followed_clear
    assert update.session_id == OLD
    _write_registry(live.config_dir, 1645, NEW)
    update = live.refresh()
    assert update.followed_clear
    assert update.session_id == NEW
    assert live.transcript.path.stem == NEW
    assert any(t.full_text.startswith("After clear") for t in update.delta.turns)


def test_refresh_follows_clear_by_the_transcript_link_without_a_pane(
    live: session_layer.LiveSession,
) -> None:
    update = live.refresh()
    assert update.followed_clear
    assert update.session_id == NEW


# ------------------------------------------------------------- c3: finding the pane


def _tmux_fake(by_socket: dict[tuple[str, ...], str]):
    calls: list[tuple[str, ...]] = []

    def run(socket: tuple[str, ...], *args: str) -> subprocess.CompletedProcess:
        calls.append(args)
        out = by_socket.get(socket)
        return subprocess.CompletedProcess(args, 0 if out is not None else 1, out or "", "")

    return run, calls


def test_find_pane_by_pane_pid_on_either_socket(tmp_path: Path) -> None:
    _write_registry(tmp_path, 1645, NEW)
    run, calls = _tmux_fake(
        {("-L", "default"): "%1\t900\tother\n", ("-L", "kyosei"): "%0\t1645\tKYO\n"}
    )
    found = pane.find_pane(NEW, run, config=tmp_path, parent_of=lambda p: None)
    assert found.pane == pane.PaneRef(("-L", "kyosei"), "%0", "KYO", 1645)
    assert all(call[0] == "list-panes" for call in calls)


def test_find_pane_through_a_shell_in_the_pane(tmp_path: Path) -> None:
    _write_registry(tmp_path, 1645, NEW)
    run, _ = _tmux_fake({("-L", "default"): "%5\t200\tshell\n"})
    parents = {1645: 200, 200: 1}
    found = pane.find_pane(NEW, run, config=tmp_path, parent_of=parents.get)
    assert found.pane is not None and found.pane.pane_id == "%5"


def test_a_session_outside_tmux_has_no_pane(tmp_path: Path) -> None:
    _write_registry(tmp_path, 1645, NEW)
    run, calls = _tmux_fake({("-L", "default"): "%1\t900\tother\n"})
    found = pane.find_pane(NEW, run, config=tmp_path, parent_of=lambda p: None)
    assert found.pane is None
    assert "not running in a tmux pane" in found.why
    assert all(call[0] == "list-panes" for call in calls)


def test_an_unregistered_session_has_no_pane(tmp_path: Path) -> None:
    run, _ = _tmux_fake({})
    found = pane.find_pane(NEW, run, config=tmp_path, parent_of=lambda p: None)
    assert found.pane is None
    assert "no live Claude Code process" in found.why


def test_the_layer_scrubs_tmux_from_the_environment(
    live: session_layer.LiveSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    live.environ.update({"TMUX": "/x,1,2", "TMUX_TMPDIR": "/y"})
    seen: list[dict[str, str]] = []

    class Spy:
        def __init__(self, binary, socket, env):
            seen.append(env)

        def run(self, *args):
            return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(session_layer, "Tmux", Spy)
    monkeypatch.setattr(session_layer, "find_tmux", lambda env: "/opt/homebrew/bin/tmux")
    live._run_tmux(("-L", "default"), "list-panes")
    assert "TMUX" not in seen[0]
    assert "TMUX_TMPDIR" not in seen[0]


def test_the_trust_text_is_the_one_the_orchestrator_matches() -> None:
    assert pane._TRUST_TEXT == cmd_controller.TRUST_PROMPT_TEXT


# ------------------------------------------------------------------ c4: classifying


@pytest.mark.parametrize(
    "name,state",
    [
        ("idle.txt", pane.IDLE),
        ("permission-bash.txt", pane.PERMISSION_PROMPT),
        ("ask.txt", pane.ASK_USER_QUESTION),
        ("usage.txt", pane.USAGE_PANEL),
        ("trust-dialog.synthetic.txt", pane.TRUST_DIALOG),
        ("unrecognised-help.txt", pane.UNRECOGNISED),
        ("background-shells.txt", pane.IDLE),
    ],
)
def test_recorded_screens_are_classified(name: str, state: str) -> None:
    assert pane.classify(screen(name)).state == state


def test_a_working_screen() -> None:
    raw = (FIXTURES / "sequence" / "frame-03.txt").read_text(encoding="utf-8")
    assert pane.classify(raw).state == pane.WORKING


def test_the_options_of_a_permission_prompt_are_read_from_the_screen() -> None:
    found = pane.classify(screen("permission-bash.txt"))
    assert found.question == "Do you want to proceed?"
    assert [o.number for o in found.options] == [1, 2, 3, 4]
    assert found.options[0].label == "Yes" and found.options[0].selected
    assert found.options[1].label.endswith("from this project")
    assert found.options[3].label == "No"


def test_the_options_of_a_question_prompt_are_read_from_the_screen() -> None:
    found = pane.classify(screen("ask.txt"))
    assert found.question == "Which colour do you prefer?"
    assert [o.label for o in found.options][:3] == ["Red", "Blue", "Type something."]
    assert found.options[0].selected
    assert found.options[0].description == "Choose red."


def test_an_unrecognised_screen_is_logged_once(tmp_path: Path) -> None:
    logged: list[str] = []
    found = pane.classify(screen("unrecognised-help.txt"), log_unrecognised_screen=logged.append)
    assert found.state == pane.UNRECOGNISED and len(logged) == 1
    target = tmp_path / "log"
    first = pane.log_unrecognised(logged[0], target)
    assert first is not None and first.is_file()
    assert first.read_text(encoding="utf-8") == logged[0]
    assert pane.log_unrecognised(logged[0], target) is None


def test_recognised_screens_are_not_logged() -> None:
    logged: list[str] = []
    pane.classify(screen("idle.txt"), log_unrecognised_screen=logged.append)
    assert logged == []


def test_reply_text_quoting_a_prompt_is_not_a_prompt() -> None:
    # An overlay check runs only when the input box is gone.
    idle = screen("idle.txt")
    quoting = "  Do you want to proceed?\n  Esc to cancel\n" + idle
    assert pane.classify(quoting).state == pane.IDLE


def test_a_placeholder_in_the_box_is_not_input() -> None:
    assert pane.classify(screen("idle-suggestion.reconstructed.txt")).input_text == ""


def test_typed_text_in_the_box_is_input() -> None:
    rule = "─" * 100
    raw = f"{rule}\n❯\xa0hello there\n  second\n{rule}\n  ⏵⏵ auto mode on\n"
    assert pane.classify(raw).input_text == "hello there\nsecond"


# ----------------------------------------------------------- c5: the reply in progress


def _frames() -> list[str]:
    files = sorted((FIXTURES / "sequence").glob("frame-*.txt"))
    return [file.read_text(encoding="utf-8") for file in files]


def test_a_reply_in_progress_is_unwrapped_and_retired_when_its_record_lands() -> None:
    live = session_layer.LiveReply()
    frames = _frames()
    seen = [live.observe_screen(raw) for raw in frames[:-1]]
    texts = [t for t in seen if t]
    assert len(texts) >= 2
    assert all(len(a) <= len(b) for a, b in zip(texts, texts[1:], strict=False))
    latest = texts[-1]
    assert latest.startswith("Tides are the regular rise and fall")
    assert "\n" not in latest.split("\n\n")[0]  # the wrapped paragraph is one line
    # The row for that reply lands: the text is retired.
    reader = view(NEW_FILE)
    record = next(r for r in reader.replies if r.text.startswith("Tides are"))
    assert pane.same_reply(latest, record.text)
    assert live.observe_record(record.text)
    assert live.text is None
    # The screen goes on showing it, and it is not offered again.
    assert live.observe_screen(frames[-1]) is None


def test_a_different_record_does_not_retire_the_reply() -> None:
    live = session_layer.LiveReply()
    assert live.observe_screen(_frames()[6]) is not None
    assert not live.observe_record("Something else entirely")
    assert live.text is not None


@pytest.mark.parametrize(
    "first,second,joined",
    [
        (f"  {'word ' * 19}word", "  next line here", True),
        ("  a short line", "  next", False),
        (f"  {'word ' * 19}word", "  - new item", False),
    ],
    ids=["full-line-wraps", "short-line-breaks", "list-item-breaks"],
)
def test_unwrapping(first: str, second: str, joined: bool) -> None:
    out = pane.unwrap_reply([first, second], 100)
    assert (out.count("\n") == 0) == joined


# ---------------------------------------- KYO-694: a running tool's blinking bullet


TOOL_REPLY = "The build finished without errors."


def _tool_frames() -> list[str]:
    """A landed reply above a running tool, as captured every 0.6 s while its bullet blinks.

    Claude Code draws a space in the bullet's cell on every other beat; frame 3 has the
    space carrying the bullet's colour, which is how a styled cell can reach a capture.
    """
    files = sorted((FIXTURES / "tool-running").glob("frame-*.txt"))
    assert len(files) == 6
    return [file.read_text(encoding="utf-8") for file in files]


def _with_rows(rows: str) -> str:
    """The bullet-off frame with the tool's two rows replaced by ``rows``."""
    frame = _tool_frames()[1]
    lines = frame.split("\n")
    return "\n".join([*lines[:4], *rows.split("\n"), *lines[6:]])


def test_the_tool_frames_show_the_bullet_on_and_off() -> None:
    assert ["⏺" in frame.split("\n")[4] for frame in _tool_frames()] == [
        True,
        False,
        True,
        False,
        True,
        False,
    ]
    assert all(pane.classify(frame).state == pane.WORKING for frame in _tool_frames())


def test_a_landed_reply_is_not_offered_again_on_any_beat_of_a_running_tool() -> None:
    live = session_layer.LiveReply()
    live.observe_record(TOOL_REPLY)
    assert [live.observe_screen(frame) for frame in _tool_frames()] == [None] * 6


@pytest.mark.parametrize("index", range(6))
def test_a_reply_in_progress_never_holds_a_running_tools_lines(index: int) -> None:
    # No record has landed here, so nothing but the reading of the screen can keep them out.
    found = pane.reply_in_progress(_tool_frames()[index])
    assert found is None


@pytest.mark.parametrize(
    "rows",
    [
        "  Reading 1 file… (ctrl+o to expand)",
        "  Running \x1b[1m3\x1b[0m agents… (ctrl+o to expand)",
        "  \x1b[38;5;246mRan \x1b[1m1\x1b[0m\x1b[38;5;246m shell command \x1b[39m",
        "  \x1b[1mTaskList\x1b[0m",
        "  \x1b[1mBash\x1b[0m(a command that wraps\n       onto a second row)",
        '  \x1b[1mfiles - read (MCP)\x1b[0m(path: "a.txt")',
    ],
    ids=["active-group", "agents", "finished-group", "no-arguments", "wrapped", "mcp"],
)
def test_every_row_a_tool_can_draw_without_its_bullet_ends_the_reply(rows: str) -> None:
    assert pane.reply_in_progress(_with_rows(rows)) is None


@pytest.mark.parametrize(
    "rows",
    [
        "  \x1b[1mFindings\x1b[0m\n\n  The second paragraph.",
        "  \x1b[1mNote\x1b[0m that the bold word opens a paragraph.",
        "  Calling read(path) is how this is done.",
    ],
    ids=["heading-above-text", "bold-opening", "parenthesis"],
)
def test_reply_text_shaped_a_little_like_a_tool_row_stays_in_the_reply(rows: str) -> None:
    found = pane.reply_in_progress(_with_rows(rows))
    assert found is not None and found.startswith(TOOL_REPLY)


def test_the_rows_under_the_spinner_do_not_end_the_reply() -> None:
    # The Tip row is drawn with the same glyph as a tool's result row.
    frame = _with_rows("  More of the reply.")
    assert "⎿  Tip" in frame
    assert pane.reply_in_progress(frame) == f"{TOOL_REPLY}\n\nMore of the reply."


def test_a_short_landed_reply_is_recognised_with_rows_below_it() -> None:
    assert pane.same_reply(f"{TOOL_REPLY}\n\nAdvising using a model", TOOL_REPLY)


def test_a_reply_that_only_begins_like_a_landed_one_is_another_reply() -> None:
    assert not pane.same_reply(f"{TOOL_REPLY} And then it went on.", TOOL_REPLY)


def test_idle_screen_has_no_reply_in_progress() -> None:
    assert pane.reply_in_progress(screen("idle.txt")) is None
    assert pane.reply_in_progress(screen("permission-bash.txt")) is None


# ---------------------------------------------------------------- c6: sending


class FakeTmux:
    """Answers ``capture-pane`` from a list of screens and records every command."""

    def __init__(self, screens: list[str]) -> None:
        self.screens = list(screens)
        self.commands: list[tuple[tuple[str, ...], str | None]] = []

    def run(self, *args: str, input: str | None = None) -> subprocess.CompletedProcess:
        if args[0] == "capture-pane":
            shown = self.screens.pop(0) if len(self.screens) > 1 else self.screens[0]
            return subprocess.CompletedProcess(args, 0, shown, "")
        self.commands.append((args, input))
        return subprocess.CompletedProcess(args, 0, "", "")


EMPTY_BOX = "─" * 100 + "\n❯ \n" + "─" * 100 + "\n  footer\n"
FULL_BOX = "─" * 100 + "\n❯ leftover text\n" + "─" * 100 + "\n  footer\n"


def _no_sleep(_: float) -> None:
    return None


def test_a_message_is_cleared_then_sent_in_segments_in_order() -> None:
    tmux = FakeTmux([EMPTY_BOX, FULL_BOX, EMPTY_BOX])
    pane.send_message(
        tmux,
        "%3",
        [pane.Typed("- first\nsecond"), pane.Pasted("pasted\nblock"), pane.Typed("tail")],
        sleep=_no_sleep,
    )
    commands = [c for c, _ in tmux.commands]
    assert commands[0] == ("send-keys", "-t", "%3", "C-e", "C-u")
    assert ("send-keys", "-t", "%3", "-l", "--", "- first") in commands
    assert commands[commands.index(("send-keys", "-t", "%3", "-l", "--", "- first")) + 1] == (
        "send-keys",
        "-t",
        "%3",
        "M-Enter",
    )
    load = next(i for i, c in enumerate(commands) if c[0] == "load-buffer")
    assert tmux.commands[load][1] == "pasted\nblock"
    assert commands[load + 1][:4] == ("paste-buffer", "-p", "-d", "-b")
    assert commands[-1] == ("send-keys", "-t", "%3", "Enter")
    assert commands.index(("send-keys", "-t", "%3", "-l", "--", "tail")) < len(commands) - 1


@pytest.mark.parametrize(
    "name",
    ["permission-bash.txt", "ask.txt", "usage.txt", "trust-dialog.synthetic.txt"],
)
def test_a_message_never_reaches_a_prompt(name: str) -> None:
    tmux = FakeTmux([screen(name)])
    with pytest.raises(pane.PaneNotReady):
        pane.send_message(tmux, "%3", [pane.Typed("yes")], sleep=_no_sleep)
    assert tmux.commands == []


def test_a_message_to_an_unrecognised_screen_is_refused_and_logged() -> None:
    logged: list[str] = []
    tmux = FakeTmux([screen("unrecognised-help.txt")])
    with pytest.raises(pane.PaneNotReady):
        pane.send_message(
            tmux, "%3", [pane.Typed("x")], log_unrecognised_screen=logged.append, sleep=_no_sleep
        )
    assert tmux.commands == [] and len(logged) == 1


def test_a_message_during_a_turn_is_sent() -> None:
    working = _frames()[3]
    assert pane.classify(working).state == pane.WORKING
    tmux = FakeTmux([working])
    pane.send_message(tmux, "%3", [pane.Typed("mid-turn")], sleep=_no_sleep)
    assert ("send-keys", "-t", "%3", "-l", "--", "mid-turn") in [c for c, _ in tmux.commands]


def test_clearing_gives_up_rather_than_sending() -> None:
    tmux = FakeTmux([FULL_BOX])
    with pytest.raises(pane.PaneNotReady, match="would not clear"):
        pane.send_message(tmux, "%3", [pane.Typed("x")], sleep=_no_sleep)
    assert not any("-l" in c for c, _ in tmux.commands)


def test_slash_commands_are_sent_only_on_an_idle_screen() -> None:
    idle = FakeTmux([EMPTY_BOX])
    pane.send_slash_command(idle, "%3", "/usage", sleep=_no_sleep)
    assert [c for c, _ in idle.commands] == [
        ("send-keys", "-t", "%3", "-l", "--", "/usage"),
        ("send-keys", "-t", "%3", "Enter"),
    ]
    working = FakeTmux([_frames()[3]])
    with pytest.raises(pane.PaneNotReady):
        pane.send_slash_command(working, "%3", "/usage", sleep=_no_sleep)
    assert working.commands == []
    with pytest.raises(KyoseiError):
        pane.send_slash_command(idle, "%3", "not a command", sleep=_no_sleep)


def test_the_recorded_mixed_message_marks_long_pastes_only() -> None:
    # The message of KYO-688.c6, sent by send_message into a throwaway session. A paste of
    # four or more lines is a <pasted_content> element around the typed segments; a short
    # paste has no marker and reads as typing.
    turn = next(t for t in view(NEW_FILE).turns if t.full_text.startswith("library typed head"))
    assert turn.paste_known
    assert '<pasted_content id="' in turn.full_text
    assert turn.full_text.count('<pasted_content id="') == 1
    assert (
        turn.segments[0] == "library typed head, reply with exactly: ok\n- second typed line\n\n\n"
    )
    assert turn.segments[1].endswith("typed after paste\nshort paste\ntyped tail")
    assert "library paste line" not in "".join(turn.segments)


def test_several_long_pastes_share_one_id_and_each_is_a_paste() -> None:
    # Measured on a message mixing typed text with two long pastes: both elements carry
    # the same id, which the closing-tag backreference still separates correctly.
    turn = next(t for t in view().turns if t.full_text.startswith("Typed head."))
    assert turn.full_text.count('id="39bc"') == 4
    assert turn.segments[0].startswith("Typed head.")
    assert "typed between pastes" in turn.segments[1]
    assert not any("pasted log line" in s for s in turn.segments)


def test_a_short_paste_reads_as_typing() -> None:
    turn = next(t for t in view().turns if t.full_text.startswith("Typed intro"))
    assert "pasted line one" in turn.full_text
    assert not turn.paste_known


# ------------------------------------------------------------ c7: background work


def test_the_footer_reports_background_shells_and_subagents() -> None:
    work = pane.classify(screen("background-shells.txt")).background
    assert work.shells == 2
    assert work.agents == (("general-purpose", "Run sleep and reply finished"),)
    assert work.running


def test_finished_subagents_are_not_background_work() -> None:
    work = pane.classify(screen("tasks-footer-no-agents.txt")).background
    assert work.shells == 0
    assert work.agents == ()
    assert not work.running


def test_the_layer_reports_background_work_from_the_screen(
    live: session_layer.LiveSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    live.pane_lookup = pane.PaneLookup(pane.PaneRef(("-S", "s"), "%0", "tw", 1), "test")
    monkeypatch.setattr(session_layer.pane, "capture", lambda *a: screen("background-shells.txt"))
    monkeypatch.setattr(session_layer.LiveSession, "tmux", lambda self: None)
    update = live.refresh()
    assert update.background is not None and update.background.shells == 2
    assert update.screen is not None and update.screen.state == pane.IDLE


# ---------------------------------------------------------------- c8: the opt-in


def _claude_with_transcript(tmp_path: Path, session_id: str) -> dict[str, str]:
    config = tmp_path / "claude-config"
    project = config / "projects" / "-a-project"
    project.mkdir(parents=True)
    shutil.copy(OLD_FILE, project / f"{session_id}.jsonl")
    return {"CLAUDE_CONFIG_DIR": str(config), "PATH": "/usr/bin:/bin"}


def test_the_layer_refuses_a_project_that_has_not_opted_in(project: Path, tmp_path: Path) -> None:
    env = _claude_with_transcript(tmp_path, OLD)
    with pytest.raises(KyoseiError, match="read_transcripts: true"):
        session_layer.open_session(load_config(project), project, OLD, environ=env)
    assert session_layer.NOT_OPTED_IN


def test_the_refusal_names_the_remedy_and_the_remedy_clears_it(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _claude_with_transcript(tmp_path, OLD)
    with pytest.raises(KyoseiError) as refused:
        session_layer.open_session(load_config(project), project, OLD, environ=env)
    assert "kyosei/config.yml" in str(refused.value)
    opt_in_transcripts(project)
    monkeypatch.setattr(
        session_layer.pane,
        "find_pane",
        lambda *a, **k: pane.PaneLookup(None, "no pane in this test"),
    )
    opened = session_layer.open_session(load_config(project), project, OLD, environ=env)
    assert opened.refresh().delta.turns


def test_opening_an_unknown_session_says_so(project: Path, tmp_path: Path) -> None:
    opt_in_transcripts(project)
    env = _claude_with_transcript(tmp_path, OLD)
    with pytest.raises(session_layer.SessionNotFound):
        session_layer.open_session(
            load_config(project), project, "00000000-0000-4000-8000-000000000000", environ=env
        )


# -------------------------------------------------------- c9: the samples are clean


#: A home directory or an email address, which no committed sample may hold.
PRIVATE = re.compile(r"/Users/|/home/|C:\\Users|~/|[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+")


def test_the_samples_hold_no_private_text() -> None:
    offenders = []
    for file in FIXTURES.rglob("*"):
        if file.is_file():
            found = PRIVATE.search(file.read_text(encoding="utf-8"))
            if found:
                offenders.append((file.relative_to(FIXTURES), found.group(0)))
    assert offenders == []


@pytest.mark.parametrize(
    "text",
    ["read /Users/someone/notes", "at /home/alex/file", "~/project", "mail a@b.org"],
)
def test_the_sample_guard_catches_a_leak(text: str) -> None:
    assert PRIVATE.search(text) is not None


# ---------------------------------------------- KYO-691: facts and slash-command output


FACTS_FILE = FIXTURES / "facts" / "session.synthetic.jsonl"
CATALOG = FIXTURES / "facts" / "model-catalog-cc.synthetic.json"


def install_catalog(config: Path, name: str = "acct-org-cc.json", text: str | None = None) -> Path:
    directory = config / "cache" / "model-catalog"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_text(text if text is not None else CATALOG.read_text(encoding="utf-8"), "utf-8")
    return target


def test_the_latest_reply_states_model_effort_and_branch() -> None:
    latest = view(FACTS_FILE).latest_facts
    assert latest is not None
    assert (latest.model, latest.effort, latest.git_branch) == (
        "claude-opus-5-5",
        "medium",
        "feature/facts",
    )
    assert latest.message_id == "msg_two"


def test_a_reply_claude_code_wrote_itself_does_not_count_as_the_latest() -> None:
    rows = [json.loads(line) for line in FACTS_FILE.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["message"]["model"] == "<synthetic>"
    assert view(FACTS_FILE).latest_facts.git_branch != rows[-1]["gitBranch"]


def test_a_transcript_without_those_keys_states_none_of_them() -> None:
    latest = view().latest_facts  # the recorded pair's scrub kept neither key
    assert latest is not None and latest.effort is None and latest.git_branch is None


def test_context_is_the_three_input_counts_added() -> None:
    latest = view(FACTS_FILE).latest_facts
    use = session_layer.context_use(latest.usage, 1_000_000)
    assert use == session_layer.ContextUse(226_458, 1_000_000, 23)  # the panel's 226.5k and 23%


def test_percent_rounds_half_up_as_the_panel_does() -> None:
    usage = transcript.Usage(1, 0, 0, 0)
    assert session_layer.context_use(usage, 200).percent == 1  # round() would give 0
    assert session_layer.context_use(transcript.Usage(250, 0, 0, 0), 200).percent == 100


def test_an_unknown_window_gives_tokens_and_no_percent() -> None:
    use = session_layer.context_use(transcript.Usage(5, 6, 7, 8), None)
    assert use == session_layer.ContextUse(20, None, None)


def test_the_window_comes_from_claude_codes_catalogue(tmp_path: Path) -> None:
    install_catalog(tmp_path)
    assert transcript.context_window("claude-opus-5-5", "claude-opus-5-5", tmp_path) == 1_000_000
    assert transcript.context_window("claude-haiku-4-5-20251001", None, tmp_path) == 200_000


@pytest.mark.parametrize("model", ["claude-unreleased-9", None])
def test_a_model_the_catalogue_does_not_name_has_no_window(tmp_path: Path, model) -> None:
    install_catalog(tmp_path)
    assert transcript.context_window(model, model, tmp_path) is None


def test_no_catalogue_means_no_window(tmp_path: Path) -> None:
    assert transcript.context_window("claude-opus-5-5", None, tmp_path) is None


@pytest.mark.parametrize(
    "text",
    [
        "{ not json",
        "[]",
        '{"catalog": 3}',
        '{"catalog": {"config": {"models": [{"id": "claude-opus-5-5", "runtime": {}}]}}}',
    ],
)
def test_a_catalogue_of_another_shape_means_no_window(tmp_path: Path, text: str) -> None:
    install_catalog(tmp_path, text=text)
    assert transcript.context_window("claude-opus-5-5", None, tmp_path) is None


def test_a_window_that_depends_on_an_opt_in_is_stated_only_when_the_session_opted_in(
    tmp_path: Path,
) -> None:
    install_catalog(tmp_path)
    assert transcript.context_window("claude-opus-4-6", "claude-opus-4-6", tmp_path) is None
    assert transcript.context_window("claude-opus-4-6", None, tmp_path) is None
    assert (
        transcript.context_window("claude-opus-4-6", "claude-opus-4-6[1m]", tmp_path) == 1_000_000
    )


def test_the_command_line_tools_newest_catalogue_is_preferred(tmp_path: Path) -> None:
    def document(surface: str, fetched: int, window: int) -> str:
        row = {"id": "claude-x", "runtime": {"max_input_tokens": window}}
        return json.dumps(
            {"fetchedAt": fetched, "catalog": {"surface": surface, "config": {"models": [row]}}}
        )

    install_catalog(tmp_path, "a-ccd.json", document("ccd", 900, 111_000))
    assert transcript.context_window("claude-x", None, tmp_path) == 111_000
    install_catalog(tmp_path, "b-cc.json", document("cc", 100, 222_000))
    install_catalog(tmp_path, "c-cc.json", document("cc", 500, 333_000))
    assert transcript.context_window("claude-x", None, tmp_path) == 333_000


def test_slash_command_output_is_returned_in_time_order() -> None:
    outputs = view(FACTS_FILE).command_outputs
    assert [(o.command, o.args) for o in outputs] == [("/context", ""), ("/effort", "medium")]
    assert [o.at for o in outputs] == sorted(o.at for o in outputs)


def test_command_output_has_no_terminal_styling() -> None:
    first = view(FACTS_FILE).command_outputs[0]
    assert "\x1b" not in first.text
    assert first.text.startswith("Context Usage")
    assert "227.4k/1m tokens (23%)" in first.text


def test_an_output_the_transcript_does_not_record_is_not_invented() -> None:
    reader = view(FACTS_FILE)
    # /rename has a command row and no output; /color has an output row that is empty.
    assert {o.command for o in reader.command_outputs} == {"/context", "/effort"}
    # The caveat notice is the harness's own text, not a command's output.
    assert not any("run directly" in o.text for o in reader.command_outputs)


def test_a_subagents_command_output_is_left_out() -> None:
    assert not any("subagent" in o.text for o in view(FACTS_FILE).command_outputs)


def test_a_person_typing_the_tags_is_a_turn_and_not_an_output() -> None:
    reader = view(FACTS_FILE)
    assert any("typed by a person" in t.full_text for t in reader.turns)
    assert not any("typed by a person" in o.text for o in reader.command_outputs)


def test_a_command_outputs_row_is_not_also_a_turn() -> None:
    assert not any("Set effort level" in t.full_text for t in view(FACTS_FILE).turns)


def test_an_output_whose_command_row_is_absent_names_no_command(tmp_path: Path) -> None:
    rows = FACTS_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [r for r in rows if "<command-name>/context" not in r]
    path = tmp_path / "t.jsonl"
    path.write_text("".join(kept), encoding="utf-8")
    first = view(path).command_outputs[0]
    assert first.text.startswith("Context Usage")
    assert first.command is None and first.args is None


def test_command_outputs_arrive_in_the_delta_of_the_read_that_found_them(tmp_path: Path) -> None:
    rows = FACTS_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    path = tmp_path / "t.jsonl"
    path.write_text("".join(rows[:4]), encoding="utf-8")
    reader = transcript.SessionTranscript(path)
    assert [o.command for o in reader.read().command_outputs] == ["/context"]
    with path.open("a", encoding="utf-8") as handle:
        handle.writelines(rows[4:])
    assert [o.command for o in reader.read().command_outputs] == ["/effort"]


def test_a_rewritten_transcript_forgets_its_facts_and_outputs(tmp_path: Path) -> None:
    rows = FACTS_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    path = tmp_path / "t.jsonl"
    path.write_text("".join(rows), encoding="utf-8")
    reader = view(path)
    assert reader.latest_facts is not None and reader.command_outputs
    path.write_text(rows[0], encoding="utf-8")
    reader.read()
    assert reader.latest_facts is None and reader.command_outputs == []


def test_a_live_session_states_its_facts(tmp_path: Path) -> None:
    install_catalog(tmp_path)
    live = session_layer.LiveSession(
        "55555555-5555-4555-8555-555555555555",
        FACTS_FILE,
        config_dir=tmp_path,
        environ={"PATH": ""},
        log_dir=tmp_path / "log",
    )
    assert live.facts() is None  # nothing read yet
    live.transcript.read()
    facts = live.facts()
    assert facts is not None
    assert (facts.model, facts.effort, facts.git_branch) == (
        "claude-opus-5-5",
        "medium",
        "feature/facts",
    )
    assert facts.context == session_layer.ContextUse(226_458, 1_000_000, 23)


# --------------------------------------------------- KYO-692: what else the session holds
#
# ``delivered/session.synthetic.jsonl`` is synthetic: each row has the keys measured on
# 2.1.319 and text written for the test. Its comments are in the script that wrote it, and
# each row's position is fixed by the assertions below.

DELIVERED_FILE = FIXTURES / "delivered" / "session.synthetic.jsonl"


def delivered_view() -> transcript.SessionTranscript:
    return view(DELIVERED_FILE)


def row_id(position: int) -> str:
    return f"00000000-0000-4000-8000-{position:012d}"


def test_every_delivery_is_its_own_object_in_time_order_with_its_kind() -> None:
    deliveries = delivered_view().deliveries
    assert [(d.kind, d.uuid) for d in deliveries] == [
        ("task-notification", row_id(11)),  # recorded after rows stamped later
        ("peer", row_id(7)),
        ("task-notification", row_id(8)),
        ("task-notification", row_id(9)),
        ("peer", row_id(10)),
        ("channel", row_id(14)),
    ]
    assert [d.at for d in deliveries] == sorted(d.at for d in deliveries)


def test_no_delivery_is_returned_as_the_persons_turn() -> None:
    reader = delivered_view()
    delivered = {d.uuid for d in reader.deliveries}
    assert delivered and not delivered & {t.id for t in reader.turns}
    said = " ".join(t.full_text for t in reader.turns)
    for words in ("hand-back", "Agent finished", "peer's own words", "From a channel"):
        assert words not in said


def test_the_person_is_not_a_delivery() -> None:
    reader = delivered_view()
    mid_turn = next(t for t in reader.turns if t.form == transcript.FORM_MID_TURN)
    assert mid_turn.full_text == "A mid-turn message from the person."
    assert mid_turn.id not in {d.uuid for d in reader.deliveries}


def test_text_the_harness_injected_is_no_delivery() -> None:
    reader = delivered_view()
    assert row_id(15) not in {d.uuid for d in reader.deliveries}
    assert row_id(15) not in {t.id for t in reader.turns}


def test_a_hand_back_is_its_report_and_names_its_sender() -> None:
    handback = next(d for d in delivered_view().deliveries if d.uuid == row_id(7))
    assert handback.handback is True
    assert handback.text == "The hand-back report, written for this test."
    assert (handback.sender, handback.sender_id) == ("kyosei-worker", "agent42")


def test_a_peers_message_is_its_body_without_the_wrapper() -> None:
    peer = next(d for d in delivered_view().deliveries if d.uuid == row_id(10))
    assert peer.text == "A peer's own words." and peer.handback is False


def test_a_notification_is_read_out_of_its_tags() -> None:
    deliveries = {d.uuid: d for d in delivered_view().deliveries}
    finished = deliveries[row_id(9)]
    assert finished.text == "The agent's result text." and finished.summary == "Agent finished"
    assert (finished.task_id, finished.call_id, finished.status) == (
        "abc123",
        "toolu_1",
        "completed",
    )
    failed = deliveries[row_id(8)]
    assert failed.text == "Background command failed" and failed.status == "failed"
    assert failed.call_id is None
    assert deliveries[row_id(11)].task_id == "bg8"


def test_a_turn_lists_its_images_without_their_bytes() -> None:
    reader = delivered_view()
    first = next(t for t in reader.turns if t.id == row_id(1))
    assert [(i.index, i.media_type) for i in first.images] == [(0, "image/png"), (2, "image/png")]
    data = reader.image(row_id(1), 0)
    assert data is not None and first.images[0].size == len(data.data)
    assert all("base64" not in repr(i) and len(repr(i)) < 120 for i in first.images)


def test_an_image_is_read_back_from_its_record() -> None:
    reader = delivered_view()
    found = reader.image(row_id(1), 2)
    assert found is not None and found.media_type == "image/png"
    assert found.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert reader.image(row_id(1), 0).data != found.data  # the index picks the block


def test_a_message_that_is_an_image_alone_is_a_turn_with_no_words() -> None:
    turn = next(t for t in delivered_view().turns if t.id == row_id(13))
    assert turn.full_text == "" and turn.segments == () and len(turn.images) == 1


def test_a_type_that_could_run_as_a_page_is_not_served() -> None:
    reader = delivered_view()
    assert reader.image(row_id(1), 1) is None
    assert 1 not in {i.index for t in reader.turns if t.id == row_id(1) for i in t.images}


@pytest.mark.parametrize(
    ("turn", "index"),
    [
        (row_id(1), 9),  # an index the record does not hold
        ("00000000-0000-4000-8000-999999999999", 0),  # no such record
        (row_id(16), 0),  # an image in a tool's result is not the person's
        (row_id(7), 0),  # a delivery holds none
        (row_id(17), 0),  # data that is not base64
    ],
)
def test_an_image_that_is_not_there_is_none(turn: str, index: int) -> None:
    assert delivered_view().image(turn, index) is None


def test_the_credit_check_does_not_count_a_message_that_is_an_image_alone() -> None:
    read = transcript.read_transcripts([DELIVERED_FILE])
    assert row_id(13) not in {turn.id for turn in read.turns}


def test_thinking_is_the_text_the_transcript_records() -> None:
    reply = next(r for r in delivered_view().replies if r.message_id == "msg_b")
    assert reply.thinking == "Checking the colours." and reply.thinking_omitted == 1
    assert reply.text == "And blue."


def test_nothing_is_invented_for_a_block_that_records_only_a_signature() -> None:
    reply = next(r for r in delivered_view().replies if r.message_id == "msg_a")
    assert reply.thinking is None and reply.thinking_omitted == 1
    assert reply.text == "A red square."


def test_a_reply_with_no_thinking_blocks_says_so() -> None:
    reply = view(FIXTURES / "facts" / "session.synthetic.jsonl").replies[0]
    assert reply.thinking is None and reply.thinking_omitted == 0


FRAMES = FIXTURES / "sequence"
ALL_CAPTURES = sorted(SCREENS.glob("*.txt")) + sorted(FRAMES.glob("*.txt"))


@pytest.mark.parametrize("capture", ALL_CAPTURES, ids=lambda p: p.name)
def test_the_terminals_text_has_no_styling_left(capture: Path) -> None:
    raw = capture.read_text(encoding="utf-8")
    words = pane.terminal(raw, pane.classify(raw))
    assert "\x1b" not in words.text and "\xa0" not in words.text
    assert words.text and all(line == line.rstrip() for line in words.text.split("\n"))


def test_the_terminals_text_keeps_the_screens_words() -> None:
    raw = (FRAMES / "frame-03.txt").read_text(encoding="utf-8")
    assert "\x1b]8;" in raw, "the sample should hold an OSC 8 hyperlink for this to test"
    text = pane.terminal(raw, pane.classify(raw)).text
    assert "Claude Code v2.1.319" in text
    assert "/rc" in text and "claude.ai/code/session" not in text  # the link's target is not drawn


@pytest.mark.parametrize(
    ("frame", "line"),
    [
        ("frame-00.txt", "Thinking… (2s · ↓ 57 tokens)"),
        ("frame-03.txt", "Thinking… (2s · ↓ 139 tokens)"),
        ("frame-09.txt", "Thinking… (4s · ↓ 363 tokens)"),
    ],
)
def test_the_working_line_is_read_while_a_turn_runs(frame: str, line: str) -> None:
    raw = (FRAMES / frame).read_text(encoding="utf-8")
    screen_now = pane.classify(raw)
    assert screen_now.state == pane.WORKING
    assert pane.terminal(raw, screen_now).working_line == line


def test_a_turn_whose_working_line_is_off_the_screen_has_none() -> None:
    raw = (FRAMES / "frame-07.txt").read_text(encoding="utf-8")
    screen_now = pane.classify(raw)
    assert screen_now.state == pane.WORKING
    assert pane.terminal(raw, screen_now).working_line is None


@pytest.mark.parametrize("name", ["idle.txt", "tasks-footer-no-agents.txt", "permission-bash.txt"])
def test_no_working_line_on_a_screen_that_is_not_working(name: str) -> None:
    raw = screen(name)
    assert pane.terminal(raw, pane.classify(raw)).working_line is None


def test_a_finished_turns_row_is_no_working_line() -> None:
    raw = (FRAMES / "frame-10.txt").read_text(encoding="utf-8")
    assert pane.terminal(raw, pane.classify(raw)).working_line is None
    assert "Baked for 4s" in raw


def test_a_working_row_above_the_persons_prompt_is_not_the_working_line() -> None:
    raw = (FRAMES / "frame-07.txt").read_text(encoding="utf-8").replace(
        "❯ ", "✶ Stale… (9s)\n❯ earlier\n\n❯ ", 1
    )
    screen_now = pane.classify(raw)
    assert screen_now.state == pane.WORKING
    assert pane.terminal(raw, screen_now).working_line is None


# ---------------------------------------- KYO-693: titles, subagents, uncommitted changes
#
# ``titled/session.synthetic.jsonl`` and ``subagent/agent-*.jsonl`` are synthetic: each row
# has the keys measured on 2.1.319 and text written for the test.

TITLED_FILE = FIXTURES / "titled" / "session.synthetic.jsonl"
AGENT_FILE = FIXTURES / "subagent" / "agent-a14ca9deaf9c51bd1.jsonl"


def test_the_latest_title_wins_and_other_rows_naming_it_do_not_count() -> None:
    assert transcript.session_title(TITLED_FILE) == "The latest title"


def test_a_title_is_found_when_a_block_boundary_splits_its_row(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(transcript, "_TITLE_BLOCK_BYTES", 16)
    assert transcript.session_title(TITLED_FILE) == "The latest title"
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    assert transcript.session_title(empty) is None


def test_a_transcript_holding_no_title_has_none() -> None:
    assert transcript.session_title(OLD_FILE) is None


def test_a_partial_last_line_is_not_read_as_a_title(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    whole = '{"type":"ai-title","aiTitle":"Whole"}\n'
    path.write_text(whole + '{"type":"ai-title","aiTi', encoding="utf-8")
    assert transcript.session_title(path) == "Whole"


def test_a_session_title_refuses_until_the_project_opts_in(project: Path) -> None:
    config = load_config(project)
    with pytest.raises(KyoseiError, match="read_transcripts: true"):
        session_layer.session_title(config, project, "x", environ=dict(os.environ))


def test_a_subagent_transcript_is_found_only_beside_its_own_session(tmp_path: Path) -> None:
    session = tmp_path / "abc.jsonl"
    session.write_text("", encoding="utf-8")
    directory = tmp_path / "abc" / "subagents"
    directory.mkdir(parents=True)
    (directory / "agent-a1.jsonl").write_text("", encoding="utf-8")
    assert transcript.subagent_transcript(session, "a1") == directory / "agent-a1.jsonl"
    assert transcript.subagent_transcript(session, "a2") is None
    assert transcript.subagent_transcript(session, "../../abc") is None
    assert transcript.subagent_transcript(tmp_path / "other.jsonl", "a1") is None


def test_a_subagents_rows_are_read_only_when_asked_for() -> None:
    sidechain = transcript.SessionTranscript(AGENT_FILE, sidechain=True)
    sidechain.read()
    assert [call.name for call in sidechain.tool_calls] == ["Bash", "Read"]
    assert sidechain.readable is True
    main = transcript.SessionTranscript(AGENT_FILE)
    main.read()
    assert main.tool_calls == [] and main.replies == []


def test_a_subagent_file_of_no_known_format_says_so(tmp_path: Path) -> None:
    path = tmp_path / "agent-x.jsonl"
    path.write_text('{"type":"user","uuid":"u","timestamp":"t"}\n', encoding="utf-8")
    reader = transcript.SessionTranscript(path, sidechain=True)
    reader.read()
    assert reader.readable is False and "isSidechain" in reader.why


GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, env={**os.environ, **GIT_ENV}, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    (path / "kept.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    run_git(path, "init", "-q")
    run_git(path, "add", "kept.txt", ".gitignore")
    run_git(path, "commit", "-q", "-m", "first")
    return path


def changes(path: Path, **env: str) -> session_layer.UncommittedChanges:
    return session_layer.uncommitted_changes(str(path), environ={**os.environ, **env})


def test_a_modified_file_counts_and_an_untracked_file_stands_apart(repo: Path) -> None:
    (repo / "kept.txt").write_text("one\nTWO\nthree\nfour\n", encoding="utf-8")
    (repo / "new.txt").write_text("a\nb\nno newline", encoding="utf-8")
    (repo / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    found = changes(repo)
    assert (found.added, found.removed, found.files) == (2, 1, 1)
    assert found.untracked == session_layer.UntrackedFiles(1, 3, 0)
    assert found.why is None and Path(found.root).resolve() == repo.resolve()


def test_staged_lines_count_as_uncommitted(repo: Path) -> None:
    (repo / "kept.txt").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    run_git(repo, "add", "kept.txt")
    found = changes(repo)
    assert (found.added, found.removed, found.files) == (1, 0, 1)


def test_a_session_in_a_subdirectory_sees_the_whole_repository(repo: Path) -> None:
    deeper = repo / "sub" / "deeper"
    deeper.mkdir(parents=True)
    (repo / "new.txt").write_text("a\n", encoding="utf-8")
    (repo / "kept.txt").write_text("one\ntwo\n", encoding="utf-8")
    found = changes(deeper)
    assert (found.added, found.removed) == (0, 1)
    assert found.untracked.files == 1


def test_binary_and_unreadable_untracked_files_are_counted_as_uncounted(repo: Path) -> None:
    (repo / "image.bin").write_bytes(b"\0\x01\x02")
    (repo / "link").symlink_to(repo / "kept.txt")
    assert changes(repo).untracked == session_layer.UntrackedFiles(2, 0, 2)


def test_a_binary_tracked_file_adds_to_files_and_to_no_line_count(repo: Path) -> None:
    (repo / "image.bin").write_bytes(b"\0\x01\x02")
    run_git(repo, "add", "image.bin")
    found = changes(repo)
    assert (found.added, found.removed, found.files) == (0, 0, 1)


def test_a_repository_before_its_first_commit_counts_what_is_staged(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    run_git(fresh, "init", "-q")
    (fresh / "a.txt").write_text("a\nb\n", encoding="utf-8")
    run_git(fresh, "add", "a.txt")
    found = changes(fresh)
    assert found.why is None and (found.added, found.removed, found.files) == (2, 0, 1)


def test_a_directory_outside_any_repository_says_so(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    found = changes(plain, GIT_CEILING_DIRECTORIES=str(tmp_path))
    assert found.root is None and found.why and (found.added, found.removed) == (0, 0)


def test_a_session_without_a_working_directory_says_so() -> None:
    found = session_layer.uncommitted_changes(None, environ=dict(os.environ))
    assert found.root is None and "no working directory" in found.why


def test_no_git_on_the_path_says_so(repo: Path) -> None:
    found = session_layer.uncommitted_changes(str(repo), environ={"PATH": "/nonexistent"})
    assert found.root is None and "git" in found.why


def test_an_inherited_git_dir_does_not_point_the_read_elsewhere(repo: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    run_git(other, "init", "-q")
    (repo / "kept.txt").write_text("one\n", encoding="utf-8")
    found = changes(repo, GIT_DIR=str(other / ".git"))
    assert (found.added, found.removed) == (0, 2)


def test_reading_changes_writes_nothing_to_the_index(repo: Path) -> None:
    (repo / "kept.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")  # same bytes, new mtime
    os.utime(repo / "kept.txt", (2_000_000_000, 2_000_000_000))
    before = (repo / ".git" / "index").read_bytes()
    changes(repo)
    assert (repo / ".git" / "index").read_bytes() == before


def test_a_repositorys_fsmonitor_setting_runs_nothing_from_here(repo: Path, tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    hook = tmp_path / "monitor.sh"
    hook.write_text(f'#!/bin/sh\ntouch "{marker}"\n', encoding="utf-8")
    hook.chmod(0o755)
    run_git(repo, "config", "core.fsmonitor", str(hook))
    (repo / "kept.txt").write_text("one\n", encoding="utf-8")
    changes(repo)
    assert not marker.exists()


# ------------------------------------------------- KYO-700: the repository's remote


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/owner/name.git", "owner/name"),
        ("https://github.com/owner/name", "owner/name"),
        ("https://github.com/owner/name/", "owner/name"),
        ("https://github.com/owner/name.dots.git", "owner/name.dots"),
        ("http://example.com:8080/owner/name.git", "owner/name"),
        ("git@github.com:owner/name.git", "owner/name"),
        ("git@github.com:owner/name", "owner/name"),
        ("github.com:owner/name.git", "owner/name"),
        ("ssh://git@github.com/owner/name.git", "owner/name"),
        ("ssh://git@example.com:2222/owner/name.git", "owner/name"),
        ("git://example.com/owner/name.git", "owner/name"),
        ("https://gitlab.com/group/sub/name.git", "group/sub/name"),
    ],
)
def test_the_origin_is_stated_as_owner_and_name(repo: Path, url: str, expected: str) -> None:
    run_git(repo, "remote", "add", "origin", url)
    assert changes(repo).remote == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://SECRET@github.com/owner/name.git",
        "https://user:SECRET@github.com/owner/name.git",
        "https://x-access-token:SECRET@github.com/owner/name",
        "ssh://user:SECRET@example.com/owner/name.git",
        "https://github.com/owner/name.git?token=SECRET",
        "https://github.com/owner/name.git#SECRET",
    ],
)
def test_a_credential_in_the_url_does_not_reach_the_remote(repo: Path, url: str) -> None:
    run_git(repo, "remote", "add", "origin", url)
    found = changes(repo)
    assert found.remote == "owner/name"
    assert "SECRET" not in repr(found)


@pytest.mark.parametrize(
    "url",
    [
        # git reads the first as host "user:pa", the second as host "user".
        "https://user:pa/SECRET@github.com/owner/name.git",
        "user:SECRET@github.com:owner/name.git",
        "/srv/git/owner/name.git",
        "../owner/name.git",
        "file:///srv/git/owner/name.git",
        "https://github.com/name.git",
        "https://github.com/owner/.git",
        "https://github.com/../name",
        "https://github.com",
    ],
)
def test_an_origin_naming_no_repository_on_a_host_gives_none(repo: Path, url: str) -> None:
    run_git(repo, "remote", "add", "origin", url)
    found = changes(repo)
    assert found.remote is None and found.why is None
    assert "SECRET" not in repr(found)


def test_a_repository_without_an_origin_has_no_remote(repo: Path) -> None:
    run_git(repo, "remote", "add", "upstream", "https://github.com/owner/name.git")
    found = changes(repo)
    assert found.remote is None and found.why is None


def test_the_remote_is_read_from_a_subdirectory_and_before_the_first_commit(
    tmp_path: Path,
) -> None:
    fresh = tmp_path / "fresh"
    (fresh / "sub").mkdir(parents=True)
    run_git(fresh, "init", "-q")
    run_git(fresh, "remote", "add", "origin", "git@github.com:owner/name.git")
    assert changes(fresh / "sub").remote == "owner/name"


def test_the_remote_follows_git_s_own_url_rewriting(repo: Path) -> None:
    run_git(repo, "config", "url.https://github.com/.insteadOf", "hub:")
    run_git(repo, "remote", "add", "origin", "hub:owner/name.git")
    assert changes(repo).remote == "owner/name"


def test_reading_the_remote_leaves_the_config_alone(repo: Path) -> None:
    run_git(repo, "remote", "add", "origin", "https://SECRET@github.com/owner/name.git")
    before = (repo / ".git" / "config").read_bytes()
    changes(repo)
    assert (repo / ".git" / "config").read_bytes() == before


# ------------------------------------------------ KYO-698: the session's permission mode
#
# ``permission-mode.synthetic.jsonl`` is synthetic: each row has the keys measured on a real
# transcript and text written for the test. Its rows are a mode row, a person's row, a reply,
# a mode row with a new mode, a subagent's row, a person's row with a new mode, and a reply.

PERMISSION_MODE_FILE = FIXTURES / "permission-mode.synthetic.jsonl"


@pytest.mark.parametrize(
    ("rows", "mode"),
    [
        (1, "default"),  # a mode row, which has no uuid and no timestamp
        (3, "default"),
        (4, "plan"),  # recorded after the latest reply
        (5, "plan"),  # a subagent's row is not the session's
        (6, "acceptEdits"),  # a person's row carries it too
        (7, "acceptEdits"),
    ],
)
def test_the_permission_mode_is_the_one_last_recorded(tmp_path: Path, rows: int, mode: str) -> None:
    lines = PERMISSION_MODE_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    partial = tmp_path / "session.jsonl"
    partial.write_text("".join(lines[:rows]), encoding="utf-8")
    assert view(partial).latest_permission_mode == mode


def test_a_transcript_recording_no_permission_mode_states_none() -> None:
    assert view(FACTS_FILE).latest_permission_mode is None


def test_a_rewritten_transcript_forgets_its_permission_mode(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    shutil.copyfile(PERMISSION_MODE_FILE, path)
    reader = view(path)
    assert reader.latest_permission_mode == "acceptEdits"
    path.write_text("", encoding="utf-8")
    reader.read()
    assert reader.latest_permission_mode is None


@pytest.mark.parametrize(
    ("source", "mode"), [(PERMISSION_MODE_FILE, "acceptEdits"), (FACTS_FILE, None)]
)
def test_a_live_session_states_its_permission_mode(
    tmp_path: Path, source: Path, mode: str | None
) -> None:
    live = session_layer.LiveSession(
        "66666666-6666-4666-8666-666666666666",
        source,
        config_dir=tmp_path,
        environ={"PATH": ""},
        log_dir=tmp_path / "log",
    )
    live.transcript.read()
    facts = live.facts()
    assert facts is not None and facts.permission_mode == mode


# --------------------------------------- KYO-695: a task's usage and its dispatch's lines
#
# ``agent-usage.synthetic.jsonl`` is synthetic: each row has the keys measured on 2.1.319
# and figures written for the test. Three subagents are started; the first notification
# states all three figures, the second one, the third none and the fourth an invalid object.

AGENT_USAGE_FILE = FIXTURES / "agent-usage.synthetic.jsonl"


def test_a_notification_states_the_usage_its_row_records() -> None:
    usage = {d.task_id: d.usage for d in view(AGENT_USAGE_FILE).deliveries}
    assert usage["a1111111111111111"] == transcript.TaskUsage(77593, 12, 122833)


def test_a_figure_the_row_leaves_out_is_none_and_so_is_a_row_with_no_figure() -> None:
    usage = {d.task_id: d.usage for d in view(AGENT_USAGE_FILE).deliveries}
    assert usage["a2222222222222222"] == transcript.TaskUsage(None, 4, None)
    assert usage["bg7"] is None
    assert usage["a3333333333333333"] is None  # the row's usage is not an object


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("You are the dispatched worker for KYO-692.d1. Your worktree is here.", "KYO-692.d1"),
        ("you are the dispatched worker for ABC-7.d12", "ABC-7.d12"),
        ("  the dispatched worker for KYO-1.d3 follows.", "KYO-1.d3"),
        ("Review the work of the dispatched worker for KYO-1.d1.", None),
        ("Count the files.", None),
        ("", None),
    ],
)
def test_only_a_prompts_opening_names_its_dispatch(prompt: str, expected: str | None) -> None:
    assert session_layer.dispatch_named(prompt) == expected


def test_a_subagent_keeps_the_prompt_it_was_started_with() -> None:
    prompts = {ref.agent_id: ref.prompt for ref in view(AGENT_USAGE_FILE).subagents}
    assert prompts["a2222222222222222"] == "Count the files under /work."


def test_branch_lines_reports_none_for_a_name_the_repository_does_not_hold(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    assert session_layer.branch_lines(repo, "0" * 40, "gone") is None


# ------------------------------------- KYO-701: lines changed by a session and its subagents
#
# ``lines-total.synthetic.jsonl`` has the keys measured on 2.1.319 and figures written for the
# test. Its sidechain rows are the subagents' files, split out by ``place_lines_session``: the
# session's own rows change +6 -1, the first subagent's +4 -2 and the second's +1 -0.

LINES_FILE = FIXTURES / "lines-total.synthetic.jsonl"
LINES_SESSION = "77777777-7777-4777-8777-777777777777"
LINES_AGENT = "a4444444444444444"


def place_lines_session(directory: Path) -> Path:
    """The fixture as Claude Code lays it out: the session's file, each subagent's beside it."""
    session: list[str] = []
    agents: dict[str, list[str]] = {}
    for line in LINES_FILE.read_text(encoding="utf-8").splitlines(keepends=True):
        record = json.loads(line)
        (agents.setdefault(record["agentId"], []) if record["isSidechain"] else session).append(
            line
        )
    path = directory / f"{LINES_SESSION}.jsonl"
    path.write_text("".join(session), encoding="utf-8")
    (directory / LINES_SESSION / "subagents").mkdir(parents=True)
    for agent, lines in agents.items():
        agent_path = directory / LINES_SESSION / "subagents" / f"agent-{agent}.jsonl"
        agent_path.write_text("".join(lines), encoding="utf-8")
    return path


def lines_agent_row(added: int) -> str:
    patch = {"structuredPatch": [{"lines": [f"+{i}" for i in range(added)]}]}
    return json.dumps({"isSidechain": True, "toolUseResult": patch}) + "\n"


def test_the_total_sums_the_session_and_every_subagent(tmp_path: Path) -> None:
    lines = transcript.SessionLines(place_lines_session(tmp_path))
    assert lines.read() == transcript.LinesChanged(11, 3)


def test_a_session_with_no_subagents_counts_its_own_patches(tmp_path: Path) -> None:
    path = place_lines_session(tmp_path)
    shutil.rmtree(tmp_path / LINES_SESSION)
    assert transcript.SessionLines(path).read() == transcript.LinesChanged(6, 1)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"structuredPatch": [{"lines": [" c", "-a", "+b"]}]}, (1, 1)),
        ({"type": "create", "content": "one\ntwo\n", "structuredPatch": []}, (3, 0)),
        ({"type": "create", "content": "one", "structuredPatch": []}, (1, 0)),
        ({"type": "create", "content": "", "structuredPatch": []}, (0, 0)),
        ({"type": "update", "content": "same\n", "structuredPatch": []}, (0, 0)),
        ({"structuredPatch": [{"lines": ["\\ No newline at end of file"]}]}, (0, 0)),
        ({"structuredPatch": "not a list"}, (0, 0)),
        ("Error: refused", (0, 0)),
        (None, (0, 0)),
    ],
)
def test_one_results_lines(result: object, expected: tuple[int, int]) -> None:
    assert transcript.patch_lines(result) == transcript.LinesChanged(*expected)


def test_a_running_session_and_a_running_subagent_stay_current(tmp_path: Path) -> None:
    path = place_lines_session(tmp_path)
    agent = tmp_path / LINES_SESSION / "subagents" / f"agent-{LINES_AGENT}.jsonl"
    lines = transcript.SessionLines(path)
    assert lines.read() == transcript.LinesChanged(11, 3)
    with agent.open("a", encoding="utf-8") as handle:
        handle.write(lines_agent_row(5))
    assert lines.read() == transcript.LinesChanged(16, 3)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(lines_agent_row(2))
    assert lines.read() == transcript.LinesChanged(18, 3)
    late = tmp_path / LINES_SESSION / "subagents" / "agent-a6666666666666666.jsonl"
    late.write_text(lines_agent_row(1), encoding="utf-8")
    assert lines.read() == transcript.LinesChanged(19, 3)


def test_a_line_still_being_written_waits_for_its_end(tmp_path: Path) -> None:
    path = place_lines_session(tmp_path)
    lines = transcript.SessionLines(path)
    lines.read()
    whole = lines_agent_row(4)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(whole[:20])
    assert lines.read() == transcript.LinesChanged(11, 3)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(whole[20:])
    assert lines.read() == transcript.LinesChanged(15, 3)


def test_only_what_was_appended_is_read(tmp_path: Path) -> None:
    path = place_lines_session(tmp_path)
    agent = tmp_path / LINES_SESSION / "subagents" / f"agent-{LINES_AGENT}.jsonl"
    lines = transcript.SessionLines(path)
    lines.read()
    # What is already counted is blanked at the same length, so a total that read the file
    # again would fall.
    held = agent.read_bytes()
    blank = (b" " * (len(held) - 1)) + b"\n"
    agent.write_bytes(blank)
    assert lines.read() == transcript.LinesChanged(11, 3)
    with agent.open("a", encoding="utf-8") as handle:
        handle.write(lines_agent_row(1))
    assert lines.read() == transcript.LinesChanged(12, 3)


def test_a_rewritten_file_is_counted_again(tmp_path: Path) -> None:
    path = place_lines_session(tmp_path)
    agent = tmp_path / LINES_SESSION / "subagents" / f"agent-{LINES_AGENT}.jsonl"
    lines = transcript.SessionLines(path)
    lines.read()
    agent.write_text(lines_agent_row(1), encoding="utf-8")
    assert lines.read() == transcript.LinesChanged(8, 1)


def test_facts_state_the_total_and_the_layer_keeps_one_follower(tmp_path: Path) -> None:
    path = place_lines_session(tmp_path)
    live = session_layer.LiveSession(
        LINES_SESSION, path, config_dir=tmp_path, environ={"PATH": ""}, log_dir=tmp_path / "log"
    )
    live.transcript.read()
    facts = live.facts()
    assert facts is not None and facts.lines == transcript.LinesChanged(11, 3)
    agent = tmp_path / LINES_SESSION / "subagents" / f"agent-{LINES_AGENT}.jsonl"
    agent.write_bytes(b" " * (agent.stat().st_size - 1) + b"\n")
    # A second opening, as a route makes on each request, reads nothing it has read.
    again = session_layer.LiveSession(
        LINES_SESSION, path, config_dir=tmp_path, environ={"PATH": ""}, log_dir=tmp_path / "log"
    )
    assert again.lines_changed() == transcript.LinesChanged(11, 3)


# -------------------------------- KYO-716: working under a status line with no interrupt hint
#
# ``working-statusline.txt`` is the screen of a working session whose footer is a custom
# status row and the mode row, as captured on 2026-11-10; the reply text is synthetic.


def test_a_working_row_above_the_box_is_working_without_the_interrupt_hint() -> None:
    raw = screen("working-statusline.txt")
    assert "esc to interrupt" not in raw
    assert pane.classify(raw).state == pane.WORKING


def test_the_working_row_rule_is_what_makes_it_working(monkeypatch) -> None:
    monkeypatch.setattr(pane, "_working_row_above", lambda lines, box: None)
    assert pane.classify(screen("working-statusline.txt")).state == pane.IDLE


def test_a_finished_turns_row_with_that_footer_is_idle() -> None:
    assert pane.classify(screen("finished-statusline.txt")).state == pane.IDLE


def test_a_screen_with_that_footer_and_no_spinner_row_is_idle() -> None:
    raw = screen("working-statusline.txt").replace("✻ Hatching…", "Hatching…")
    assert pane.classify(raw).state == pane.IDLE


def test_a_quoted_working_row_higher_up_does_not_make_a_screen_working() -> None:
    raw = screen("quoted-working-row.txt")
    assert pane.classify(raw).state == pane.IDLE


def test_the_terminal_returns_the_working_line_under_that_footer() -> None:
    raw = screen("working-statusline.txt")
    found = pane.terminal(raw, pane.classify(raw))
    assert found.working_line == "Hatching… (40s · ↓ 3.6k tokens · thought for 3s)"


def test_an_interrupt_is_sent_on_that_screen() -> None:
    tmux = FakeTmux([screen("working-statusline.txt")])
    pane.send_interrupt(tmux, "%3")
    assert [c for c, _ in tmux.commands] == [("send-keys", "-t", "%3", "Escape")]


def test_an_indented_notice_between_the_row_and_the_box_does_not_hide_it() -> None:
    # Captured live on 2026-11-10: ``✔ Update installed`` is drawn right-aligned on the line
    # above the box, and the screen is still a working one.
    raw = screen("working-statusline-notice.txt")
    assert "Update installed" in raw and "esc to interrupt" not in raw
    assert pane.classify(raw).state == pane.WORKING
    assert pane.terminal(raw, pane.classify(raw)).working_line.startswith("Tempering…")


def test_ahead_counts_commits_the_upstream_lacks_and_is_none_without_one(
    repo: Path, tmp_path: Path
) -> None:
    assert changes(repo).ahead is None
    upstream = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "-q", "--bare", str(upstream)], check=True)
    run_git(repo, "remote", "add", "origin", str(upstream))
    run_git(repo, "push", "-q", "-u", "origin", "HEAD")
    assert changes(repo).ahead == 0
    (repo / "more.txt").write_text("x\n", encoding="utf-8")
    run_git(repo, "add", "more.txt")
    run_git(repo, "commit", "-q", "-m", "second")
    assert changes(repo).ahead == 1


def test_ahead_counts_against_origins_branch_when_no_upstream_is_configured(
    repo: Path, tmp_path: Path
) -> None:
    # Pushed without -u, as `git push origin main` leaves it: no upstream, but a
    # remote-tracking branch of the same name.
    upstream = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "-q", "--bare", str(upstream)], check=True)
    run_git(repo, "remote", "add", "origin", str(upstream))
    run_git(repo, "push", "-q", "origin", "HEAD")
    assert changes(repo).ahead == 0
    (repo / "more.txt").write_text("x\n", encoding="utf-8")
    run_git(repo, "add", "more.txt")
    run_git(repo, "commit", "-q", "-m", "second")
    assert changes(repo).ahead == 1
