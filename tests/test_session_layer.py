"""The session layer of chunk E2 (KYO-688), against recorded samples.

The samples under ``tests/fixtures/session_layer/`` were recorded from throwaway sessions
of Claude Code 2.1.319 (Haiku 5.5) on a private tmux socket, and scrubbed by a script that
keeps a fixed list of record fields, keeps only the screens' synthetic text, and rewrites
paths under ``/work``. Two screens are not recordings, because a throwaway must not raise
or answer them, and say so in their names: ``trust-dialog.synthetic.txt`` is the dialog as
``tests/test_kyo_controller.py`` already measured it, and
``idle-suggestion.reconstructed.txt`` is the input line's escape bytes as measured during
this work, placed under a recorded rule.
"""

from __future__ import annotations

import json
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
