"""A scripted session: what Claude Code does that ``kyo serve`` and the app can observe (KYO-946).

The check harness needs a live session behind its test server, so that a scenario can use the
chat, the composer and sending, and it must not start a real ``claude``. This program stands
in for one. It is started in a pane of the run's private tmux server by the test server's own
Start route, as the machine's ``orchestrator_command``, and it does three things a session
does:

- It records itself as Claude Code and the Kyosei plugin's hooks record a session: the process
  record ``<config dir>/sessions/<pid>.json``, a transcript under ``<config dir>/projects/``,
  and ``kyo sessions --hook`` and ``kyo turns --hook`` run with the payloads of
  ``SessionStart``, ``UserPromptSubmit``, ``PreToolUse``, ``Stop`` and ``SessionEnd``.
- It draws the screen the server's reader classifies (``kyosei.harness.claude_code_pane``):
  the input box between two rules, a working row and ``esc to interrupt`` while a turn runs,
  the footer's background work, and the ``AskUserQuestion`` prompt. The shapes are those of
  the recorded captures in ``tests/fixtures/session_layer/`` (Claude Code 2.1.319).
- It reads the keys the server sends, in raw mode: typed text, Control-E and Control-U,
  Option-Return, a bracketed paste, Return and Escape.

On a submitted prompt it appends the person's record to the transcript and plays the next
turn of its script.

THE SCRIPT is a JSON document, ``{"turns": [[step, ...], ...], "named": {text: [step, ...]}}``.
A prompt whose whole text is a key of ``named`` is answered by that list of steps; any other
prompt by the next list of ``turns``, in order, and past the last of them by one short reply.
A step is an object with exactly one of these keys, and any step may also carry ``after``,
the seconds the session works before the step happens.

- ``{"say": text}``: an assistant record holding text. ``written_after`` is how long after
  the event the record is written.
- ``{"tool": {"name", "input", "result", "seconds"}}``: a tool call, written when it is made,
  and its result, written ``seconds`` later.
- ``{"ask": {"questions": [...], "hook": false | "written" | "held", "answer": {...}}}``: an
  ``AskUserQuestion`` call. The prompt holds the screen until it is answered, by keys sent to
  the pane or, with ``answer`` (``{"after": seconds, "choose": label}`` or ``{"after":
  seconds, "dismiss": true}``), by the session itself as though the person had pressed them.
- ``{"working": words}``: what the working row says from now on; the words hold an ellipsis.
- ``{"background": {"shells": n, "agents": [[type, description], ...]}}``: what the footer
  says is running in the background.
- ``{"wait": seconds}``: the session works for that long and writes nothing.

A step that writes may carry ``partial``, seconds: the write is then made in two parts with
that pause between them, the first ending inside a line.

THE ORDER AND TIMING RULES. A script names events and never transcript rows, so that a
scenario cannot write an order Claude Code does not produce; :func:`check_script` refuses
anything else. The rules, each measured of Claude Code and each with a test in
``test_scripted_session.py``:

1. The record of a call that waits on the person is written with its result (KYO-940,
   measured on 2.1.325 for ``AskUserQuestion``: no record reached the file while the dialog
   stood open, and the call and its result arrived together). The screen shows the question
   for the whole interval. With a ``PreToolUse`` hook matched on the tool (``hook``), the hook
   is run before the dialog opens; the same measurement found that Claude Code then usually
   writes the call's record at once, which is ``"written"``, and ``"held"`` is the case the
   server does not rely on that for.
2. A result is never written before its call: the two are one step, and the call's record is
   always in an earlier write or earlier in the same one.
3. Records are appended as whole lines. ``partial`` reproduces a line that is in the file in
   part for a while, which a reader must wait for.
4. A record's timestamp is the time of its event and not of its write, so the two differ
   wherever a record is held (``written_after``, and every question).

THE START (KYO-964, KYO-965, KYO-966). With ``--trust-prompt`` the session opens on Claude
Code's workspace-trust dialog, whose "Yes, I trust this folder" is what ``kyo orchestrator``
and the server's reader recognise, and until it is answered it writes no process record,
runs no hook and has no transcript, so the project records no session: the state the
orchestrator's terminal exists for. ``1`` or Return answers yes and the session begins as
it does without the flag; ``2`` or Escape answers no and it exits. Whether Claude Code writes
its process record before the dialog is answered was not measured. The input box is drawn
as 2.1.328 draws it (KYO-810): the text wrapped by word, at most a third of the pane's rows
less two (seven in a pane of 24, as one capture of 2.1.328 at 80x24 showed), and, when it
holds more, its first rows scrolled out above under the rule ``─── ↑ N more ───`` with the
``❯`` row not drawn. How many rows Claude Code gives the box in a pane of another size was not
measured. ``/resume`` makes the session another one in the same process and pane, as
choosing a session in Claude Code's ``/resume`` picker does: a new session id and transcript
beside the old one, the process record naming the new id, and ``SessionStart`` run with
``"source": "resume"``. The picker is not drawn, the transcript begins empty, and whether
Claude Code runs ``SessionEnd`` for the session left was not measured, so it is not run.

WHAT IT DOES NOT REPRODUCE. A paste is put in the box as text; Claude Code folds one of four
lines or more into a placeholder. A prompt submitted while a turn runs is recorded when the
turn ends and then answered; where Claude Code puts such a prompt in the transcript was not
measured for this program. There is no permission prompt, no subagent and no slash command
other than ``/exit`` and ``/resume``. The model's own behaviour is the script.

THE LOG (``--log``) is one JSON line for each thing the session did, with the wall clock at
that moment: every write to the transcript with the timestamps of the records in it, every
hook, every prompt submitted and every change of what the screen shows. The harness's tests
read the order and the timing from it.
"""

from __future__ import annotations

import argparse
import json
import os
import select
import signal
import subprocess
import sys
import termios
import time
import tty
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VERSION = "2.1.319"
MODEL = "claude-scripted"
QUESTION_TOOL = "AskUserQuestion"
STEP_KINDS = ("say", "tool", "ask", "working", "background", "wait")
STEP_OPTIONS = ("after", "written_after", "partial")
HOOK_MODES = (False, "written", "held")

GREY = "\x1b[38;5;246m"
WHITE = "\x1b[38;5;231m"
SALMON = "\x1b[38;5;174m"
RESET = "\x1b[39m"
PROMPT_GROUND = "\x1b[48;5;237m"
GROUND_OFF = "\x1b[49m"
BOLD = "\x1b[1m"
BOLD_OFF = "\x1b[0m"


#: The trust dialog's option, as ``kyosei.cmd_controller.TRUST_PROMPT_TEXT`` matches it.
TRUST_YES = "Yes, I trust this folder"


def box_rows(rows: int) -> int:
    """How many rows of text the input box shows in a pane of ``rows`` (see THE START)."""
    return max(1, (rows - 2) // 3)


class ScriptError(ValueError):
    """A script that names something the session does not play."""


def check_script(
    document: Any,
) -> tuple[list[list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """The turns of a script, in order and by name, or :class:`ScriptError` saying what it
    may not hold."""
    if (
        not isinstance(document, dict)
        or not isinstance(document.get("turns", []), list)
        or not isinstance(document.get("named", {}), dict)
    ):
        raise ScriptError('a script is {"turns": [[step, ...], ...], "named": {text: [step, ...]}}')
    unknown = sorted(set(document) - {"turns", "named"})
    if unknown:
        raise ScriptError(f"a script holds turns and named turns and nothing else, not {unknown}")
    ordered = document.get("turns", [])
    named = document.get("named", {})
    turns = [(str(number), turn) for number, turn in enumerate(ordered, 1)] + list(named.items())
    _check_turns(turns)
    return ordered, named


def _check_turns(turns: list[tuple[str, Any]]) -> None:
    for number, turn in turns:
        if not isinstance(turn, list):
            raise ScriptError(f"turn {number} is not a list of steps")
        for step in turn:
            where = f"turn {number}: {json.dumps(step)[:80]}"
            if not isinstance(step, dict):
                raise ScriptError(f"{where}: a step is an object")
            kinds = [key for key in step if key in STEP_KINDS]
            other = [key for key in step if key not in STEP_KINDS + STEP_OPTIONS]
            if other:
                raise ScriptError(
                    f"{where}: a step is one of {', '.join(STEP_KINDS)}, which name events; "
                    f"{other} names none. A transcript row cannot be written by itself, so "
                    "that a result is never written before its call"
                )
            if len(kinds) != 1:
                raise ScriptError(f"{where}: a step holds exactly one of {', '.join(STEP_KINDS)}")
            for option in STEP_OPTIONS:
                if option in step and not _seconds(step[option]):
                    raise ScriptError(f"{where}: {option} is a number of seconds")
            kind, value = kinds[0], step[kinds[0]]
            if kind == "say" and not (isinstance(value, str) and value):
                raise ScriptError(f"{where}: say takes the reply's text")
            if kind == "wait" and not _seconds(value):
                raise ScriptError(f"{where}: wait takes a number of seconds")
            if kind == "working" and not (isinstance(value, str) and "…" in value):
                raise ScriptError(f"{where}: a working row's words hold an ellipsis (…)")
            if kind == "tool":
                _check_tool(where, value)
            if kind == "ask":
                _check_ask(where, value)
            if kind == "background":
                _check_background(where, value)


def _seconds(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value >= 0


def _check_tool(where: str, value: Any) -> None:
    if not isinstance(value, dict) or set(value) - {"name", "input", "result", "seconds"}:
        raise ScriptError(f"{where}: tool takes name, input, result and seconds")
    if value.get("name") == QUESTION_TOOL:
        raise ScriptError(
            f"{where}: {QUESTION_TOOL} waits on the person, and its record is written with "
            "its result; it is an ask step"
        )
    if not isinstance(value.get("name"), str) or not isinstance(value.get("result", ""), str):
        raise ScriptError(f"{where}: a tool has a name, and its result is text")
    if not isinstance(value.get("input", {}), dict) or not _seconds(value.get("seconds", 0)):
        raise ScriptError(f"{where}: a tool's input is an object and its seconds a number")


def _check_ask(where: str, value: Any) -> None:
    if not isinstance(value, dict) or set(value) - {"questions", "hook", "answer"}:
        raise ScriptError(f"{where}: ask takes questions, hook and answer")
    questions = value.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ScriptError(f"{where}: ask takes at least one question")
    for question in questions:
        options = question.get("options") if isinstance(question, dict) else None
        if (
            not isinstance(question, dict)
            or not isinstance(question.get("question"), str)
            or not isinstance(options, list)
            or not all(isinstance(o, dict) and isinstance(o.get("label"), str) for o in options)
        ):
            raise ScriptError(f"{where}: a question has its words and options with labels")
    if value.get("hook", False) not in HOOK_MODES:
        raise ScriptError(f'{where}: hook is false, "written" or "held"')
    answer = value.get("answer")
    if answer is not None and not (
        isinstance(answer, dict)
        and _seconds(answer.get("after"))
        and not set(answer) - {"after", "choose", "dismiss"}
    ):
        raise ScriptError(f"{where}: answer is after, with choose or dismiss")


def _check_background(where: str, value: Any) -> None:
    agents = value.get("agents", []) if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) - {"shells", "agents"}
        or not isinstance(value.get("shells", 0), int)
        or not isinstance(agents, list)
        or not all(isinstance(a, list) and len(a) == 2 for a in agents)
    ):
        raise ScriptError(f"{where}: background takes shells and agents, each [type, description]")


def stamp(moment: float | None = None) -> str:
    """A moment as a transcript writes it: UTC, to the millisecond."""
    at = datetime.fromtimestamp(time.time() if moment is None else moment, UTC)
    return at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{at.microsecond // 1000:03d}Z"


def transcript_directory(config: Path, cwd: str) -> Path:
    """Where Claude Code keeps the transcripts of sessions started in ``cwd``."""
    slug = "".join(c if c.isalnum() else "-" for c in cwd)
    return config / "projects" / slug


def wrap(text: str, width: int, first: str, rest: str) -> list[str]:
    """Break text by word, as Claude Code does before the terminal sees it."""
    out: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            prefix = first if not out and not line else rest
            if line and len(prefix) + len(line) + 1 + len(word) > width:
                out.append(line)
                line = word
            else:
                line = f"{line} {word}" if line else word
        out.append(line)
    return out


class Session:
    def __init__(
        self,
        arguments: argparse.Namespace,
        turns: list[list[dict[str, Any]]],
        named: dict[str, list[dict[str, Any]]],
    ) -> None:
        self.turns = turns
        self.named = named
        self.played = 0
        self.config = Path(arguments.config)
        self.kyosei_home = arguments.kyosei_home
        self.log_path = Path(arguments.log) if arguments.log else None
        self.cwd = os.getcwd()
        self.session_id = str(uuid.uuid4())
        self.pid = os.getpid()
        directory = transcript_directory(self.config, self.cwd)
        directory.mkdir(parents=True, exist_ok=True)
        self.transcript = directory / f"{self.session_id}.jsonl"
        self.registry = self.config / "sessions" / f"{self.pid}.json"
        self.last_uuid: str | None = None
        self.prompt_id: str | None = None
        self.last_reply = ""
        # The screen.
        self.history: list[str] = []
        self.mode = "idle"
        self.working_words: str | None = None
        self.turn_began = 0.0
        self.text = ""
        self.queued: list[str] = []
        self.shells = 0
        self.agents: list[list[str]] = []
        self.question: dict[str, Any] | None = None
        self.selected = 0
        self.answer: dict[str, Any] | None = None
        # The turn being played, and when its next step is due.
        self.playing: Iterator[float] | None = None
        self.due = 0.0
        self.running = True
        #: Whether the trust dialog holds the screen; nothing is recorded while it does.
        self.trusting = bool(getattr(arguments, "trust_prompt", False))

    # ------------------------------------------------------------------ records and hooks

    def log(self, event: str, **fields: Any) -> None:
        if self.log_path is None:
            return
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"t": time.time(), "event": event, **fields}) + "\n")

    def record(self, kind: str, at: str, **fields: Any) -> dict[str, Any]:
        """One transcript record, a child of the last one made."""
        made = {
            "isSidechain": False,
            "sessionId": self.session_id,
            "userType": "external",
            "entrypoint": "cli",
            "cwd": self.cwd,
            "version": VERSION,
            "parentUuid": self.last_uuid,
            "uuid": str(uuid.uuid4()),
            "timestamp": at,
            "gitBranch": "main",
            "type": kind,
            **fields,
        }
        self.last_uuid = made["uuid"]
        return made

    def assistant(self, at: str, content: dict[str, Any], stop: str) -> dict[str, Any]:
        return self.record(
            "assistant",
            at,
            message={
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "type": "message",
                "role": "assistant",
                "model": MODEL,
                "content": [content],
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 40,
                    "cache_read_input_tokens": 1000,
                    "cache_creation_input_tokens": 20,
                },
                "stop_reason": stop,
            },
        )

    def write(self, records: list[dict[str, Any]], partial: float | None = None) -> Iterator[float]:
        """Append records to the transcript in one write, or in two with a pause between."""
        data = "".join(json.dumps(record) + "\n" for record in records).encode("utf-8")
        said = [
            {
                "uuid": record["uuid"],
                "type": record["type"],
                "timestamp": record["timestamp"],
                "content": [
                    part.get("type")
                    for part in record["message"]["content"]
                    if isinstance(part, dict)
                ]
                if isinstance(record.get("message", {}).get("content"), list)
                else "text",
            }
            for record in records
        ]
        descriptor = os.open(self.transcript, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            if partial:
                cut = max(1, len(data) // 2)
                os.write(descriptor, data[:cut])
                self.log("write", records=said, bytes=cut, part="first", ends_in_a_line=True)
                yield partial
                os.write(descriptor, data[cut:])
                self.log("write", records=said, bytes=len(data) - cut, part="second")
            else:
                os.write(descriptor, data)
                self.log("write", records=said, bytes=len(data), part="whole")
        finally:
            os.close(descriptor)

    def hook(self, command: str, event: str, **fields: Any) -> None:
        """Run ``kyo <command> --hook`` with the payload Claude Code sends for ``event``."""
        payload = {
            "session_id": self.session_id,
            "transcript_path": str(self.transcript),
            "cwd": self.cwd,
            "hook_event_name": event,
            **fields,
        }
        environment = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(self.config),
            "KYOSEI_HOME": self.kyosei_home,
        }
        try:
            done = subprocess.run(  # noqa: S603 - this interpreter and its kyosei
                [sys.executable, "-m", "kyosei", command, "--hook"],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                cwd=self.cwd,
                env=environment,
                timeout=30,
                check=False,
            )
            said = {"status": done.returncode, "stderr": done.stderr[-400:]}
        except (OSError, subprocess.SubprocessError) as failure:
            said = {"status": None, "stderr": str(failure)}
        self.log("hook", command=command, hook_event=event, **said)

    def register(self, status: str) -> None:
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "pid": self.pid,
            "sessionId": self.session_id,
            "status": status,
            "cwd": self.cwd,
        }
        temporary = self.registry.with_suffix(".tmp")
        temporary.write_text(json.dumps(record), encoding="utf-8")
        temporary.replace(self.registry)

    # ------------------------------------------------------------------------ the screen

    def draw(self) -> None:
        try:
            columns, rows = os.get_terminal_size(1)
        except OSError:
            columns, rows = 80, 24
        rule = f"\x1b[38;5;244m{'─' * columns}{RESET}"
        if self.trusting:
            lines = [
                rule,
                f" {BOLD}Accessing workspace:{BOLD_OFF}",
                "",
                f" {self.cwd}",
                "",
                " Quick safety check: Is this a project you created or one you trust? (Like your",
                " own code, a well-known open source project, or work from your team).",
                "",
                f" {GREY}❯{RESET} 1. {TRUST_YES}",
                "   2. No, exit",
                "",
                f" {GREY}Enter to confirm · Esc to cancel{RESET}",
            ]
            shown = lines[-rows:]
            sys.stdout.write("\x1b[H\x1b[2J" + "\r\n".join(shown))
            sys.stdout.flush()
            return
        lines = [
            f"{SALMON} ▐▛███▛█{RESET}   {BOLD}Claude Code{BOLD_OFF} {GREY}v{VERSION}{RESET}",
            f"{SALMON}▝▜██████▀{RESET}  {GREY}Scripted · check harness{RESET}",
            f"{SALMON} ▝▝   ▝▝ {RESET}  {GREY}{self.cwd}{RESET}",
            "",
            *self.history,
        ]
        if self.mode == "ask" and self.question is not None:
            lines += [
                "",
                rule,
                *self.question_lines(),
                rule,
                f"  {self.option_count() + 2}. Chat about this",
            ]
            lines += ["", "Enter to select · ↑/↓ to navigate · Esc to cancel"]
        else:
            if self.mode == "working":
                lines += ["", f"{SALMON}✻{RESET} {GREY}{self.working_row()}{RESET}", ""]
            typed = wrap(self.text, columns - 2, "❯ ", "  ")
            hidden = max(0, len(typed) - box_rows(rows))
            if hidden:
                above = f"─── ↑ {hidden} more "
                lines.append(f"\x1b[38;5;244m{above}{'─' * (columns - len(above))}{RESET}")
            else:
                lines.append(rule)
            lines += [
                f"{GREY}❯ {RESET}{line}" if index == 0 else f"  {line}"
                for index, line in enumerate(typed)
            ][hidden:]
            lines.append(rule)
            footer = "  ⏵⏵ auto mode on"
            if self.shells:
                footer += f" · {self.shells} shell{'s' if self.shells != 1 else ''}"
            elif self.mode != "working":
                footer += " (shift+tab to cycle)"
            if self.mode == "working":
                footer += " · esc to interrupt"
            elif self.shells:
                footer += " · ↓ to manage"
            lines.append(f"{GREY}{footer}{RESET}")
            if self.agents:
                lines.append("  ⏺ main")
                lines += [f"  ◯ {kind}  {words}" for kind, words in self.agents]
        shown = lines[-rows:]
        sys.stdout.write("\x1b[H\x1b[2J" + "\r\n".join(line[: columns + 200] for line in shown))
        sys.stdout.flush()

    def working_row(self) -> str:
        if self.working_words:
            return self.working_words
        return f"Working… ({int(time.monotonic() - self.turn_began)}s · ↓ 216 tokens)"

    def option_count(self) -> int:
        return len((self.question or {}).get("options", []))

    def question_lines(self) -> list[str]:
        question = self.question or {}
        lines = [f" ☐ {question.get('header', 'Question')}", "", question["question"], ""]
        for index, option in enumerate(question["options"]):
            mark = "❯" if index == self.selected else " "
            lines.append(f"{mark} {index + 1}. {option['label']}")
            if option.get("description"):
                lines.append(f"     {option['description']}")
        mark = "❯" if self.selected == self.option_count() else " "
        lines.append(f"{mark} {self.option_count() + 1}. Type something.")
        return lines

    def show(self, what: str) -> None:
        self.log("screen", shows=what)
        self.draw()

    def say_on_screen(self, text: str) -> None:
        columns = os.get_terminal_size(1).columns if sys.stdout.isatty() else 80
        lines = wrap(text, columns - 1, "⏺ ", "  ")
        self.history.append("")
        self.history += [
            f"{WHITE}⏺{RESET} {line}" if index == 0 else f"  {line}"
            for index, line in enumerate(lines)
        ]

    def prompt_on_screen(self, text: str) -> None:
        self.history.append("")
        for index, line in enumerate(text.split("\n")):
            lead = "❯ " if index == 0 else "  "
            self.history.append(f"{GREY}{PROMPT_GROUND}{lead}{WHITE}{line}{RESET}{GROUND_OFF}")

    # -------------------------------------------------------------------------- a turn

    def submit(self, text: str) -> None:
        """The person's prompt: recorded, told to the hook, and answered by the next turn."""
        self.prompt_id = str(uuid.uuid4())
        self.prompt_on_screen(text)
        self.mode = "working"
        self.working_words = None
        self.turn_began = time.monotonic()
        self.register("busy")
        self.show("working")
        record = self.record(
            "user",
            stamp(),
            origin={"kind": "human"},
            promptId=self.prompt_id,
            promptSource="typed",
            message={"role": "user", "content": text},
        )
        for _ in self.write([record]):
            pass
        self.log("prompt", text=text, uuid=record["uuid"])
        self.hook("turns", "UserPromptSubmit", prompt=text, prompt_id=self.prompt_id)
        if text in self.named:
            steps = self.named[text]
        elif self.played < len(self.turns):
            steps = self.turns[self.played]
            self.played += 1
        else:
            self.played += 1
            steps = [{"say": f"Scripted reply {self.played}."}]
        self.playing = self.play(steps)
        self.due = time.monotonic()

    def play(self, steps: list[dict[str, Any]]) -> Iterator[float]:
        for step in steps:
            if step.get("after"):
                yield step["after"]
            partial = step.get("partial")
            if "wait" in step:
                yield step["wait"]
            elif "working" in step:
                self.working_words = step["working"]
                self.show("working")
            elif "background" in step:
                self.shells = step["background"].get("shells", 0)
                self.agents = step["background"].get("agents", [])
                self.show(self.mode)
            elif "say" in step:
                record = self.assistant(stamp(), {"type": "text", "text": step["say"]}, "end_turn")
                if step.get("written_after"):
                    yield step["written_after"]
                yield from self.write([record], partial)
                self.last_reply = step["say"]
                self.say_on_screen(step["say"])
                self.show("working")
            elif "tool" in step:
                yield from self.tool(step["tool"], partial)
            elif "ask" in step:
                yield from self.ask(step["ask"], partial)
        self.finish()

    def tool(self, tool: dict[str, Any], partial: float | None) -> Iterator[float]:
        call_id = f"toolu_{uuid.uuid4().hex[:24]}"
        call = self.assistant(
            stamp(),
            {
                "type": "tool_use",
                "id": call_id,
                "name": tool["name"],
                "input": tool.get("input", {}),
                "caller": {"type": "direct"},
            },
            "tool_use",
        )
        yield from self.write([call], partial)
        words = json.dumps(tool.get("input", {}))[:60]
        self.history += ["", f"{GREY}⏺{RESET} {BOLD}{tool['name']}{BOLD_OFF}({words})"]
        self.show("working")
        yield tool.get("seconds", 0)
        result = self.record(
            "user",
            stamp(),
            promptId=self.prompt_id,
            message={
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "content": tool.get("result", ""),
                        "tool_use_id": call_id,
                    }
                ],
            },
            toolUseResult={"stdout": tool.get("result", ""), "stderr": ""},
        )
        yield from self.write([result], partial)
        first = (tool.get("result", "").splitlines() or ["(No output)"])[0]
        self.history.append(f"  {GREY}⎿  {first}{RESET}")
        self.show("working")

    def ask(self, ask: dict[str, Any], partial: float | None) -> Iterator[float]:
        """A question: the dialog holds the screen, and the call is written with its result."""
        questions = ask["questions"]
        call_id = f"toolu_{uuid.uuid4().hex[:24]}"
        call = self.assistant(
            stamp(),
            {
                "type": "tool_use",
                "id": call_id,
                "name": QUESTION_TOOL,
                "input": {"questions": questions},
                "caller": {"type": "direct"},
            },
            "tool_use",
        )
        held: list[dict[str, Any]] = [call]
        mode = ask.get("hook", False)
        if mode == "written":
            yield from self.write(held, partial)
            held = []
        if mode:
            self.hook(
                "sessions",
                "PreToolUse",
                permission_mode="default",
                tool_name=QUESTION_TOOL,
                tool_input={"questions": questions},
                tool_use_id=call_id,
            )
        answers: dict[str, str] = {}
        dismissed = False
        script = ask.get("answer")
        for question in questions:
            self.question, self.selected, self.answer = question, 0, None
            self.mode = "ask"
            self.register("waiting")
            self.show("ask-user-question")
            limit = time.monotonic() + script["after"] if script else None
            while self.answer is None:
                if limit is not None and time.monotonic() >= limit:
                    assert script is not None
                    self.answer = (
                        {"dismiss": True}
                        if script.get("dismiss")
                        else {"choose": script.get("choose") or question["options"][0]["label"]}
                    )
                    break
                yield 0.02
            if self.answer.get("dismiss"):
                dismissed = True
                break
            answers[question["question"]] = self.answer["choose"]
        self.question = None
        self.mode = "working"
        self.register("busy")
        if dismissed:
            content: dict[str, Any] = {
                "type": "tool_result",
                "content": "The user doesn't want to proceed with this tool use. The tool use "
                "was rejected (eg. if it was a file edit, the new_string was NOT written to "
                "the file). STOP what you are doing and wait for the user to tell you how to "
                "proceed.",
                "is_error": True,
                "tool_use_id": call_id,
            }
            outcome: Any = "User rejected tool use"
        else:
            said = ", ".join(f'"{asked}"="{given}"' for asked, given in answers.items())
            content = {
                "type": "tool_result",
                "content": f"Your questions have been answered: {said}. You can now continue "
                "with these answers in mind.",
                "tool_use_id": call_id,
            }
            outcome = {"questions": questions, "answers": answers, "annotations": {}}
        result = self.record(
            "user",
            stamp(),
            promptId=self.prompt_id,
            message={"role": "user", "content": [content]},
            toolUseResult=outcome,
        )
        self.log("answered", call_id=call_id, answers=answers, dismissed=dismissed)
        yield from self.write([*held, result], partial)
        self.history += ["", f"{GREY}⏺{RESET} {BOLD}User answered Claude's questions:{BOLD_OFF}"]
        self.history += [
            f"  {GREY}⎿  · {asked} → {given}{RESET}" for asked, given in answers.items()
        ]
        self.show("working")
        if dismissed:
            raise _Interrupted

    def finish(self, how: str = "Cooked") -> None:
        """The turn is over: the finished row, the idle screen and the ``Stop`` hooks."""
        self.playing = None
        self.mode = "idle"
        seconds = int(time.monotonic() - self.turn_began)
        self.history += ["", f"{GREY}✻ {how} for {seconds}s{RESET}"]
        self.register("idle")
        self.show("idle")
        self.hook("turns", "Stop", last_assistant_message=self.last_reply, prompt_id=self.prompt_id)
        self.hook("sessions", "Stop")
        if self.queued:
            self.submit(self.queued.pop(0))

    def interrupt(self) -> None:
        """Escape while a turn runs: the turn stops where it is."""
        if self.playing is not None:
            self.playing.close()
        record = self.record(
            "user",
            stamp(),
            promptId=self.prompt_id,
            message={
                "role": "user",
                "content": [{"type": "text", "text": "[Request interrupted by user]"}],
            },
        )
        for _ in self.write([record]):
            pass
        self.history.append(f"  {GREY}⎿  Interrupted · What should Claude do instead?{RESET}")
        self.log("interrupted")
        self.finish("Brewed")

    # ------------------------------------------------------------------------- the keys

    def answer_trust(self, data: str) -> None:
        """The trust dialog's keys: yes begins the session, no ends the process."""
        for key in data:
            if key in ("1", "\r"):
                self.trusting = False
                self.log("trusted")
                self.begin()
                return
            if key in ("2", "\x1b"):
                self.log("not-trusted")
                self.running = False
                return

    def keys(self, data: str) -> None:
        if self.trusting:
            self.answer_trust(data)
            return
        index = 0
        while index < len(data) and self.running:
            rest = data[index:]
            if rest.startswith("\x1b[200~"):
                end = rest.find("\x1b[201~")
                pasted = rest[6 : end if end >= 0 else len(rest)]
                self.typed(pasted.replace("\r\n", "\n").replace("\r", "\n"))
                index += (end + 6) if end >= 0 else len(rest)
            elif rest.startswith(("\x1b\r", "\x1b\n")):
                self.typed("\n")
                index += 2
            elif rest.startswith("\x1b["):
                end = 2
                while end < len(rest) and not ("@" <= rest[end] <= "~"):
                    end += 1
                self.arrow(rest[end : end + 1])
                index += end + 1
            elif rest[0] == "\x1b":
                self.escape()
                index += 1
            elif rest[0] == "\r":
                self.enter()
                index += 1
            elif rest[0] == "\x15":
                # Control-U deletes to the start of the line, and at a line's start the break.
                if self.text.endswith("\n"):
                    self.text = self.text[:-1]
                else:
                    self.text = self.text[: self.text.rfind("\n") + 1]
                index += 1
            elif rest[0] == "\x7f":
                self.text = self.text[:-1]
                index += 1
            elif rest[0] == "\x04" and not self.text:
                self.running = False
                index += 1
            elif rest[0] >= " ":
                self.typed(rest[0])
                index += 1
            else:
                index += 1  # Control-E and the other controls move nothing here.

    def typed(self, text: str) -> None:
        if self.mode == "ask":
            if text.isdigit() and self.question is not None:
                self.choose(int(text) - 1)
            return
        self.text += text

    def arrow(self, final: str) -> None:
        if self.mode == "ask":
            step = {"A": -1, "B": 1}.get(final, 0)
            self.selected = max(0, min(self.option_count() - 1, self.selected + step))

    def choose(self, index: int) -> None:
        options = (self.question or {}).get("options", [])
        if 0 <= index < len(options):
            self.answer = {"choose": options[index]["label"]}

    def enter(self) -> None:
        if self.mode == "ask":
            self.choose(self.selected)
            return
        text = self.text.strip("\n")
        if not text.strip():
            return
        self.text = ""
        if text.strip() == "/exit":
            self.running = False
        elif text.strip() == "/resume" and self.mode != "working":
            self.resume()
        elif self.mode == "working":
            self.queued.append(text)
            self.log("queued", text=text)
        else:
            self.submit(text)

    def escape(self) -> None:
        if self.mode == "ask":
            self.answer = {"dismiss": True}
        elif self.mode == "working":
            self.interrupt()

    def resume(self) -> None:
        """``/resume``: the process becomes another session, beside the one it was."""
        previous = self.session_id
        self.session_id = str(uuid.uuid4())
        self.transcript = self.transcript.with_name(f"{self.session_id}.jsonl")
        self.transcript.touch()
        self.last_uuid = None
        self.history = []
        self.register("idle")
        self.log(
            "resumed", session_id=self.session_id, previous=previous,
            transcript=str(self.transcript),
        )  # fmt: skip
        self.show("idle")
        self.hook("sessions", "SessionStart", source="resume")

    # ------------------------------------------------------------------------- the loop

    def begin(self) -> None:
        """What Claude Code does once it has begun: the process record, the transcript, the
        idle screen and the ``SessionStart`` hook."""
        self.transcript.touch()
        self.register("idle")
        self.log(
            "started", session_id=self.session_id, pid=self.pid, transcript=str(self.transcript)
        )
        self.show("idle")
        self.hook("sessions", "SessionStart", source="startup")

    def run(self) -> None:
        sys.stdout.write("\x1b[?2004h")  # bracketed paste, which the server's paste relies on
        if self.trusting:
            self.log("trust-prompt", cwd=self.cwd)
            self.show("trust-prompt")
        else:
            self.begin()
        pending = b""
        while self.running:
            now = time.monotonic()
            wait = 0.5 if self.playing is None else max(0.0, min(0.5, self.due - now))
            ready, _, _ = select.select([0], [], [], wait)
            if ready:
                chunk = os.read(0, 65536)
                if not chunk:
                    break
                pending += chunk
                # A lone Escape is told from the start of a sequence by what follows at once.
                if pending.endswith(b"\x1b") and select.select([0], [], [], 0.05)[0]:
                    continue
                text = pending.decode("utf-8", "ignore")
                pending = b""
                self.keys(text)
                self.draw()
            if self.playing is not None and time.monotonic() >= self.due:
                try:
                    self.due = time.monotonic() + next(self.playing)
                except StopIteration:
                    pass
                except _Interrupted:
                    self.interrupt()
            elif self.mode == "working" and not ready and self.working_words is None:
                self.draw()  # the row's seconds
        self.log("ended")
        if not self.trusting:  # a session never begun has nothing to end
            self.hook("sessions", "SessionEnd", reason="prompt_input_exit")


class _Interrupted(Exception):
    """A dismissed question ends the turn as an interrupt does."""


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--script", help="the script, a JSON file")
    parser.add_argument("--config", required=True, help="the run's Claude config directory")
    parser.add_argument("--kyosei-home", required=True, help="the run's KYOSEI_HOME")
    parser.add_argument("--log", help="where the session logs what it did")
    parser.add_argument(
        "--trust-prompt", action="store_true",
        help="open on the workspace-trust dialog, as Claude Code does in a new folder",
    )  # fmt: skip
    arguments = parser.parse_args()
    document = json.loads(Path(arguments.script).read_text("utf-8")) if arguments.script else {}
    turns, named = check_script(document)
    if not sys.stdin.isatty():
        print("scripted_session: runs in a terminal, as a session does", file=sys.stderr)
        return 2
    session = Session(arguments, turns, named)
    before = termios.tcgetattr(0)

    def ended(*_: object) -> None:
        session.running = False

    signal.signal(signal.SIGTERM, ended)
    signal.signal(signal.SIGHUP, ended)
    signal.signal(signal.SIGWINCH, lambda *_: session.draw())
    try:
        tty.setraw(0)
        session.run()
    finally:
        termios.tcsetattr(0, termios.TCSADRAIN, before)
        sys.stdout.write("\x1b[?2004l\r\n")
        session.registry.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
