"""Claude Code's terminal screen, read and driven through tmux (KYO-688).

The screen is not a published interface and may change with any release, so everything
that depends on its shape is in this module and pinned by tests against recorded captures
(``tests/fixtures/session_layer/``). Every shape below was measured from throwaway
sessions of Claude Code 2.1.319 on a private tmux socket.

WHICH PANE A SESSION IS IN. ``<config dir>/sessions/<pid>.json`` is Claude Code's own
record of a live process: its ``sessionId``, which changes in place on ``/clear``, and its
``pid``. :func:`find_pane` takes a session id, finds the process, and asks tmux for the
panes on the default socket and on ``-L kyosei`` (``list-panes`` only) for one whose
process is that process or an ancestor of it. A session that is not in tmux has none,
which :attr:`PaneLookup.why` states. Commands that read run on those sockets; nothing here
writes to one, and only :func:`send_message`, :func:`send_slash_command` and
:func:`send_interrupt` send keys, to the pane they are given.

WHAT IS ON THE SCREEN. The bottom of a captured pane is the input box (two ``─`` rules with a
``❯`` line between them) and a footer. When the box is present the session is idle or, if
the footer holds ``esc to interrupt``, working. When it is absent an overlay holds the
screen: a permission prompt (``Do you want ...?``, numbered options read from the screen,
``Esc to cancel``), an ``AskUserQuestion`` prompt (``Enter to select``), the ``/usage`` panel
(``Usage by model:``), the trust dialog, or something not catalogued. Reply text above the
box cannot be mistaken for an overlay, because the overlay checks run only when the box is
gone. An uncatalogued screen is logged by :func:`log_unrecognised` rather than guessed at,
because the survey, the share-transcript prompt and the feedback notice could not be
measured on demand (spike S3). The layer never answers an overlay (KYO-568).

BACKGROUND WORK (KYO-688.c7, measured). The footer reports it: ``· 2 shells`` counts the
session's background commands, including those a subagent started, and each running
subagent has a ``◯ <type>  <description>`` row beneath the footer. Those rows are gone
once the subagent has stopped; ``/tasks to see subagents`` then remains in the footer while
the finished subagents can still be listed, and is not a sign of work. The transcript cannot
replace this: a background command's completion is a ``task-notification`` row
written when it ends, a subagent's own shells are not in the parent's transcript at all,
and an agent's notification can fire more than once. ``status`` in the process registry is
``shell`` only while the session is otherwise idle.

SENDING. A message goes as typed segments and pasted segments, in order, after the input
box is cleared. Typed text is sent with ``send-keys -l --`` line by line and ``M-Enter``
between lines. Pasted text is sent as a bracketed paste from a tmux buffer. Measured in
this release: a paste of four or more lines or over 800 characters is folded by Claude
Code into a ``[Pasted text #n]`` placeholder and reaches the transcript as a
``<pasted_content>`` element with blank lines around it, and a smaller paste arrives as
plain text with no marker, indistinguishable from typing. Clearing uses ``C-u``, which
deletes to the start of the line and at a line's start deletes the line break, so it
repeats until the box reads empty. ``Escape`` is never used to clear, since it would
interrupt a running turn; it is what :func:`send_interrupt` presses.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..errors import KyoseiError

IDLE = "idle"
WORKING = "working"
PERMISSION_PROMPT = "permission-prompt"
ASK_USER_QUESTION = "ask-user-question"
USAGE_PANEL = "usage-panel"
TRUST_DIALOG = "trust-dialog"
UNRECOGNISED = "unrecognised"

#: The words of the trust dialog's "yes" option; ``cmd_controller`` reads the same screen.
_TRUST_TEXT = "Yes, I trust this folder"
_RULE = re.compile(r"^[─▔]{20,}$")
_SGR = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
#: What ``capture-pane -e`` carries besides colours: an OSC 8 hyperlink around a link's text.
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_BULLET_COLOUR = re.compile(r"(\x1b\[[0-9;:]*m)*⏺")
_OPTION = re.compile(r"^\s*(?:❯\s*)?(\d+)\.\s+(.+?)\s*$")
_QUESTION = re.compile(r"^\s*Do you want to .+\?\s*$")
_SHELLS = re.compile(r"·\s*(\d+)\s+shells?\b")
_AGENT_ROW = re.compile(r"^\s*◯\s+(\S+)\s{2,}(.+?)(?:\s{2,}\S.*)?$")
#: The spinner row of a turn, always at the left margin; a reply's own lines are indented.
_SPINNER_ROW = re.compile(r"^[·✢✳✶✻✽*]\s")
#: A spinner row of a turn still running. A finished turn's row keeps the glyph and has no
#: ellipsis (``✻ Baked for 4s · done 9:12 AM``), and the verb varies from one turn to the next.
_WORKING_ROW = re.compile(r"^[·✢✳✶✻✽*]\s+(.*….*?)\s*$")
_LIST_MARKER = re.compile(r"^\s*(?:[-*•]\s|\d+\.\s)")

#: The default theme's dim grey, which reply text is not drawn in.
_GREY = "38;5;246"

#: Bullet colours of the rows that are not reply text, measured on the default theme:
#: a tool or status row is grey (246), a finished agent row green (114).
_TOOL_BULLET_COLOURS = (_GREY, "38;5;114")

_STYLES = r"(?:\x1b\[[0-9;:]*m)*"
#: Two blank cells where a bullet goes, and the styling in front of the first word after them.
_BLANK_BULLET = re.compile(rf"^{_STYLES} {_STYLES} ({_STYLES})(?=[^ \x1b])")
_BOLD = "\x1b[1m"
#: A tool's bold name with its arguments after it, ``Bash(sleep 30)``.
_NAME_AND_ARGUMENTS = re.compile(r"[^\x1b]+(?:\x1b\[[0-9;:]*m)+\(")
#: A row in bold from its first word to its last.
_ALL_BOLD = re.compile(r"[^\x1b]+(?:\x1b\[[0-9;:]*m)*\s*$")
#: A group of tools or agents still running, ``Reading 1 file… (ctrl+o to expand)``.
_ACTIVE_GROUP = re.compile(r"…(?:\s*\([^()]*\))?\s*$")

#: How many letters and digits of a screen's reply are compared with a transcript record.
_SAME_REPLY_PREFIX = 200

_CODE_FENCE = re.compile(r"^```.*$", re.M)

#: How many times the input box is cleared before giving up. Each round removes a line.
_CLEAR_ATTEMPTS = 64
_CLEAR_PAUSE_SECONDS = 0.05
_PASTE_PAUSE_SECONDS = 0.2

#: How many screens the unrecognised log keeps; the first of each screen is enough to catalogue.
_UNRECOGNISED_LIMIT = 200

#: Sockets a session's pane can be on: tmux's default server and ``-L kyosei``.
PANE_SOCKETS: tuple[tuple[str, ...], ...] = (("-L", "default"), ("-L", "kyosei"))


# --------------------------------------------------------------------- reading a screen


def _strip(raw: str) -> str:
    return _SGR.sub("", _OSC.sub("", raw)).replace("\xa0", " ")


def plain_screen(raw: str) -> str:
    """A capture with its escape sequences and non-breaking spaces removed."""
    return _strip(raw)


@dataclass(frozen=True)
class PromptOption:
    """One option of a prompt as the screen shows it."""

    number: int
    label: str
    selected: bool
    description: str = ""


@dataclass(frozen=True)
class BackgroundWork:
    """What the footer says is still running (see this module's header)."""

    shells: int
    agents: tuple[tuple[str, str], ...]

    @property
    def running(self) -> bool:
        return self.shells > 0 or bool(self.agents)


NO_BACKGROUND_WORK = BackgroundWork(0, ())


@dataclass(frozen=True)
class Screen:
    """A captured screen as one of the known states."""

    state: str
    options: tuple[PromptOption, ...] = ()
    question: str = ""
    input_text: str = ""
    background: BackgroundWork = NO_BACKGROUND_WORK


@dataclass(frozen=True)
class _InputBox:
    first: int
    rule_below: int
    text: str


def _input_box(lines: list[str], raw_lines: list[str]) -> _InputBox | None:
    rules = [i for i, line in enumerate(lines) if _RULE.match(line.strip())]
    for top, bottom in zip(reversed(rules[:-1]), reversed(rules[1:]), strict=False):
        body = lines[top + 1 : bottom]
        if body and body[0].startswith("❯"):
            typed = [line[1:].strip() if i == 0 else line.strip() for i, line in enumerate(body)]
            text = "\n".join(typed).strip()
            return _InputBox(top + 1, bottom, "" if _is_placeholder(raw_lines[top + 1]) else text)
    return None


def _is_placeholder(raw_line: str) -> bool:
    """Whether the input line holds dim text: a suggestion, not something typed."""
    return re.search(r"❯[\s\xa0]*\x1b\[2m", raw_line) is not None


def _background(footer: list[str]) -> BackgroundWork:
    shells = 0
    agents: list[tuple[str, str]] = []
    for line in footer:
        count = _SHELLS.search(line)
        if count:
            shells = int(count.group(1))
        row = _AGENT_ROW.match(line)
        if row:
            agents.append((row.group(1), row.group(2).rstrip()))
    return BackgroundWork(shells, tuple(agents))


def _options_under(lines: list[str], start: int) -> tuple[PromptOption, ...]:
    """The numbered options after ``start``; an indented line continues the label."""
    options: list[list] = []
    for line in lines[start:]:
        match = _OPTION.match(line)
        if match:
            selected = line.lstrip().startswith("❯")
            options.append([int(match.group(1)), match.group(2), selected, []])
        elif options and line.strip() and line.startswith("   "):
            options[-1][3].append(line.strip())
    return tuple(PromptOption(n, label, sel, " ".join(rest)) for n, label, sel, rest in options)


def _permission_options(lines: list[str], start: int) -> tuple[PromptOption, ...]:
    return tuple(
        PromptOption(o.number, f"{o.label} {o.description}".strip(), o.selected)
        for o in _options_under(lines, start)
    )


def _ask_question(lines: list[str]) -> str:
    """The question text: the line before a blank line above the first option."""
    for index, line in enumerate(lines):
        if _OPTION.match(line):
            for candidate in reversed(lines[:index]):
                if candidate.strip() and not candidate.lstrip().startswith("☐"):
                    return candidate.strip()
    return ""


def classify(raw: str, *, log_unrecognised_screen: Callable[[str], None] | None = None) -> Screen:
    """Which state a captured screen is in. ``raw`` is ``capture-pane -p -e`` output."""
    plain = _strip(raw)
    raw_lines = raw.split("\n")
    lines = plain.split("\n")
    box = _input_box(lines, raw_lines)
    if box is not None:
        footer = lines[box.rule_below + 1 :]
        background = _background(footer)
        state = WORKING if any("esc to interrupt" in line for line in footer) else IDLE
        return Screen(state, input_text=box.text, background=background)
    if _TRUST_TEXT in " ".join(plain.split()):
        return Screen(TRUST_DIALOG)
    tail = [line for line in lines if line.strip()][-4:]
    if "Enter to select" in "\n".join(tail):
        return Screen(
            ASK_USER_QUESTION, options=_options_under(lines, 0), question=_ask_question(lines)
        )
    if "Esc to cancel" in "\n".join(tail):
        for index, line in enumerate(lines):
            if _QUESTION.match(line):
                return Screen(
                    PERMISSION_PROMPT,
                    options=_permission_options(lines, index + 1),
                    question=line.strip(),
                )
    if "Usage by model:" in plain and "Total cost:" in plain:
        return Screen(USAGE_PANEL)
    if log_unrecognised_screen is not None:
        log_unrecognised_screen(plain)
    return Screen(UNRECOGNISED)


@dataclass(frozen=True)
class Terminal:
    """The words on a captured screen, for a client to show (KYO-692)."""

    #: The screen with its styling removed, and no trailing spaces or trailing blank lines.
    text: str
    #: The working line's words without its spinner glyph, as the terminal has drawn them
    #: while a turn runs; None on any other screen, or while the turn's row is off the screen.
    working_line: str | None


def _working_line(lines: list[str], raw_lines: list[str]) -> str | None:
    """The spinner row between the input box and the person's prompt, by its words."""
    box = _input_box(lines, raw_lines)
    if box is None:
        return None
    for line in reversed(lines[: box.first - 1]):
        if line.startswith("❯ "):
            return None
        found = _WORKING_ROW.match(line)
        if found:
            return found.group(1)
    return None


def terminal(raw: str, screen: Screen) -> Terminal:
    """The text of ``raw``, the capture that was classified as ``screen``."""
    lines = _strip(raw).split("\n")
    working = _working_line(lines, raw.split("\n")) if screen.state == WORKING else None
    return Terminal("\n".join(line.rstrip() for line in lines).rstrip(), working)


def log_unrecognised(screen: str, directory: Path) -> Path | None:
    """Keep one uncatalogued screen in ``directory``, for cataloguing.

    Files are named by content, so a screen seen again adds nothing, and the directory
    keeps at most :data:`_UNRECOGNISED_LIMIT` of them. Returns the file written, or None
    when nothing was. ``directory`` must not be inside a repository: a screen holds
    whatever the session was showing.
    """
    from hashlib import sha256

    directory.mkdir(parents=True, exist_ok=True)
    name = sha256(screen.encode("utf-8")).hexdigest()[:16]
    path = directory / f"{name}.txt"
    if path.exists() or sum(1 for _ in directory.glob("*.txt")) >= _UNRECOGNISED_LIMIT:
        return None
    path.write_text(screen, encoding="utf-8")
    return path


# ----------------------------------------------------------------- the reply in progress


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("F", "W") else 1 for c in text)


def _above_spinner(lines: list[str], bottom: int) -> int:
    """Where the conversation ends: the running spinner's row, or ``bottom`` without one.

    The rows under the spinner are not the conversation, and a tip among them is drawn with
    the glyph of a tool's result.
    """
    for index in range(bottom - 1, -1, -1):
        line = lines[index]
        if _SPINNER_ROW.match(line):
            return index
        if line.startswith(("⏺ ", "❯ ")) or _RULE.match(line.strip()):
            break
    return bottom


def _tool_row_without_bullet(raw_line: str, lowest: bool) -> bool:
    """Whether a captured row belongs to a tool, read from a row with no bullet on it.

    While a tool runs, Claude Code draws a space in place of its bullet on every other
    600 ms beat, and a finished group of tools is drawn with no bullet at all. Those rows
    are indented as a reply's own continuation lines are. A bold or ellipsised row is only
    taken for a tool's when it is the ``lowest`` of the conversation, since a reply can
    hold a line like it higher up.
    """
    found = _BLANK_BULLET.match(raw_line)
    if found is None:
        return False
    styling, words = found.group(1), raw_line[found.end() :]
    if _strip(words).startswith("⎿") or _GREY in styling:
        return True
    bold = _BOLD in styling
    if bold and _NAME_AND_ARGUMENTS.match(words):
        return True
    return lowest and (
        bool(_ACTIVE_GROUP.search(_strip(words))) or (bold and bool(_ALL_BOLD.match(words)))
    )


def _block_lines(
    raw_lines: list[str], lines: list[str], bottom: int
) -> tuple[int, list[str]] | None:
    """The last reply text block above line ``bottom``: its first index and its lines."""
    start = None
    lowest = True
    for index in range(_above_spinner(lines, bottom) - 1, -1, -1):
        line = lines[index]
        if line.startswith("⏺ "):
            colour = _BULLET_COLOUR.match(raw_lines[index])
            prefix = colour.group(0) if colour else ""
            if any(code in prefix for code in _TOOL_BULLET_COLOURS):
                return None
            start = index
            break
        if line.startswith("❯ ") or _RULE.match(line.strip()):
            return None
        if line.strip():
            if _tool_row_without_bullet(raw_lines[index], lowest):
                return None
            lowest = False
    if start is None:
        return None
    block: list[str] = []
    for line in lines[start:bottom]:
        if _SPINNER_ROW.match(line):
            break
        block.append(line.rstrip())
    while block and not block[-1].strip():
        block.pop()
    return start, block


def _next_word(line: str) -> str:
    return line.strip().split(" ", 1)[0]


def unwrap_reply(lines: list[str], width: int) -> str:
    """Undo Claude Code's own line wrapping, which ``capture-pane -J`` cannot do.

    Claude Code breaks a long line by word and indents the continuation; the terminal
    never sees a wrapped line. A line that is a continuation of the one before can only
    have been broken because its first word did not fit, so a line whose next word would
    have fit is a real line break. A new list item is never joined to the line before it.
    """
    out: list[str] = []
    previous: str | None = None
    for line in lines:
        if not line.strip():
            out.append("")
            previous = None
            continue
        text = line.strip()
        if text.startswith("⏺ "):
            text = text[2:]
        if (
            previous is not None
            and not _LIST_MARKER.match(line)
            and _width(previous) + 1 + _width(_next_word(line)) > width
        ):
            out[-1] = f"{out[-1]} {text}"
        else:
            out.append(text)
        previous = line.rstrip()
    return "\n".join(out)


def reply_in_progress(raw: str) -> str | None:
    """The reply being written on a working screen, line wrapping undone; None if there is none.

    This is the text the terminal has drawn so far. Markdown has already been rendered,
    so it differs in places from the transcript record that replaces it.
    """
    if classify(raw).state != WORKING:
        return None
    plain = _strip(raw)
    raw_lines = raw.split("\n")
    lines = plain.split("\n")
    box = _input_box(lines, raw_lines)
    if box is None:
        return None
    rules = [i for i, line in enumerate(lines[: box.first]) if _RULE.match(line.strip())]
    bottom = rules[-1] if rules else box.first
    found = _block_lines(raw_lines, lines, bottom)
    if found is None:
        return None
    width = max((len(line) for line in lines if _RULE.match(line.strip())), default=0)
    return unwrap_reply(found[1], width) or None


def same_reply(screen_text: str, record_text: str) -> bool:
    """Whether a transcript reply's text is what the screen was drawing.

    Compared by letters and digits alone, since the terminal has already rendered the
    markdown, and over the first :data:`_SAME_REPLY_PREFIX` of them, since the screen may
    hold only the beginning and diverges from the markdown later (a code fence's language
    is not drawn). A record shorter than that prefix also matches a screen text whose
    paragraphs begin with the whole of it, as when rows no reading recognised follow it.
    """

    def key(text: str) -> str:
        return "".join(c for c in _CODE_FENCE.sub("", text) if c.isalnum())

    head = key(screen_text)[:_SAME_REPLY_PREFIX]
    record = key(record_text)
    if not head or not record:
        return False
    if record.startswith(head):
        return True
    paragraphs = screen_text.split("\n\n")
    return any(key("\n\n".join(paragraphs[:n])) == record for n in range(1, len(paragraphs)))


# ----------------------------------------------------------------- finding a session's pane


@dataclass(frozen=True)
class ProcessEntry:
    """A live Claude Code process as ``<config dir>/sessions/<pid>.json`` records it."""

    pid: int
    session_id: str
    status: str | None


def read_registry(config: Path) -> list[ProcessEntry]:
    """Every live process Claude Code has recorded. ``[]`` when there is no registry."""
    directory = config / "sessions"
    if not directory.is_dir():
        return []
    found: list[ProcessEntry] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            isinstance(data, dict)
            and isinstance(data.get("pid"), int)
            and isinstance(data.get("sessionId"), str)
        ):
            status = data.get("status")
            state = status if isinstance(status, str) else None
            found.append(ProcessEntry(data["pid"], data["sessionId"], state))
    return found


def session_of_process(pid: int, config: Path) -> str | None:
    """The session a process is on now; ``/clear`` changes it in place."""
    for entry in read_registry(config):
        if entry.pid == pid:
            return entry.session_id
    return None


@dataclass(frozen=True)
class PaneRef:
    """A tmux pane a session is running in, and how to reach its server."""

    socket_args: tuple[str, ...]
    pane_id: str
    session_name: str
    pid: int


@dataclass(frozen=True)
class PaneLookup:
    """Where a session's pane is, or why there is none."""

    pane: PaneRef | None
    why: str


#: ``(socket_args, *tmux_args) -> completed process``: how the pane finder runs tmux.
TmuxRunner = Callable[..., "subprocess.CompletedProcess[str]"]
#: ``pid -> its parent's pid or None``.
ParentOf = Callable[[int], int | None]


def _parent_pid(pid: int) -> int | None:
    done = subprocess.run(  # noqa: S603, S607 - fixed argv, a pid
        ["ps", "-o", "ppid=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    text = done.stdout.strip()
    return int(text) if text.isdigit() else None


def _ancestors(pid: int, parent_of: ParentOf) -> list[int]:
    chain = [pid]
    for _ in range(32):
        parent = parent_of(chain[-1])
        if parent is None or parent <= 1 or parent in chain:
            break
        chain.append(parent)
    return chain


def find_pane(
    session_id: str,
    run_tmux: TmuxRunner,
    *,
    config: Path,
    sockets: Sequence[tuple[str, ...]] = PANE_SOCKETS,
    parent_of: ParentOf = _parent_pid,
) -> PaneLookup:
    """The tmux pane ``session_id`` is running in, by read-only tmux commands, or why none is."""
    entry = next((e for e in read_registry(config) if e.session_id == session_id), None)
    if entry is None:
        return PaneLookup(None, "no live Claude Code process records this session")
    chain = _ancestors(entry.pid, parent_of)
    for socket in sockets:
        done = run_tmux(
            socket, "list-panes", "-a", "-F", "#{pane_id}\t#{pane_pid}\t#{session_name}"
        )
        if done.returncode != 0:
            continue
        for row in done.stdout.splitlines():
            pane_id, _, rest = row.partition("\t")
            pid_text, _, session_name = rest.partition("\t")
            if pid_text.isdigit() and int(pid_text) in chain:
                return PaneLookup(
                    PaneRef(socket, pane_id, session_name, entry.pid),
                    f"pane {pane_id} on {' '.join(socket)}",
                )
    return PaneLookup(None, "this session is not running in a tmux pane on either socket")


# --------------------------------------------------------------------------- sending


class PaneNotReady(KyoseiError):
    """The pane is on a screen that must not receive keys."""


@dataclass(frozen=True)
class Typed:
    """Text sent as keystrokes. A newline in it becomes ``M-Enter``."""

    text: str


@dataclass(frozen=True)
class Pasted:
    """Text sent as one bracketed paste."""

    text: str


#: A tmux client bound to one server: ``run(*args, input=None)`` as ``cmd_controller.Tmux``.
class _Tmux:  # pragma: no cover - a structural type
    socket_args: tuple[str, ...]

    def run(self, *args: str, input: str | None = None) -> subprocess.CompletedProcess: ...


def capture(tmux: _Tmux, pane_id: str) -> str:
    """The pane's visible screen, with escape sequences, as ``classify`` reads it."""
    return tmux.run("capture-pane", "-p", "-e", "-t", pane_id).stdout


def _ready_screen(
    tmux: _Tmux,
    pane_id: str,
    *,
    allow: tuple[str, ...],
    log_unrecognised_screen: Callable[[str], None] | None,
) -> Screen:
    screen = classify(capture(tmux, pane_id), log_unrecognised_screen=log_unrecognised_screen)
    if screen.state not in allow:
        raise PaneNotReady(
            f"pane {pane_id} is showing {screen.state}, and this is sent only to "
            f"{' or '.join(allow)}; a prompt or dialog is left to the person to answer, and "
            "nothing was sent to it"
        )
    return screen


def clear_input(tmux: _Tmux, pane_id: str, *, sleep: Callable[[float], None] = time.sleep) -> None:
    """Empty the input box, or raise when it will not empty."""
    for _ in range(_CLEAR_ATTEMPTS):
        screen = classify(capture(tmux, pane_id))
        if screen.state not in (IDLE, WORKING):
            raise PaneNotReady(f"pane {pane_id} left the input box while it was being cleared")
        if not screen.input_text:
            return
        tmux.run("send-keys", "-t", pane_id, "C-e", "C-u")
        sleep(_CLEAR_PAUSE_SECONDS)
    raise PaneNotReady(f"the input box of pane {pane_id} would not clear; nothing was sent to it")


def _send_typed(tmux: _Tmux, pane_id: str, text: str) -> None:
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if line:
            tmux.run("send-keys", "-t", pane_id, "-l", "--", line)
        if index < len(lines) - 1:
            tmux.run("send-keys", "-t", pane_id, "M-Enter")


def _send_paste(tmux: _Tmux, pane_id: str, text: str) -> None:
    name = f"kyosei-paste-{pane_id.lstrip('%')}"
    tmux.run("load-buffer", "-b", name, "-", input=text)
    tmux.run("paste-buffer", "-p", "-d", "-b", name, "-t", pane_id)


def send_message(
    tmux: _Tmux,
    pane_id: str,
    segments: Sequence[Typed | Pasted],
    *,
    log_unrecognised_screen: Callable[[str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Send a message: clear the input, send each segment in order, press Enter.

    Refuses any screen other than idle or working, so that keys never reach a permission
    prompt, a question or a dialog.
    """
    _ready_screen(
        tmux, pane_id, allow=(IDLE, WORKING), log_unrecognised_screen=log_unrecognised_screen
    )
    clear_input(tmux, pane_id, sleep=sleep)
    for segment in segments:
        if isinstance(segment, Typed):
            _send_typed(tmux, pane_id, segment.text)
        else:
            _send_paste(tmux, pane_id, segment.text)
            sleep(_PASTE_PAUSE_SECONDS)
    tmux.run("send-keys", "-t", pane_id, "Enter")


def send_slash_command(
    tmux: _Tmux,
    pane_id: str,
    command: str,
    *,
    log_unrecognised_screen: Callable[[str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Send a slash command, only on an idle screen: a panel it opens is modal."""
    if not command.startswith("/") or "\n" in command:
        raise KyoseiError(f"{command!r} is not a slash command (one line beginning with /)")
    _ready_screen(tmux, pane_id, allow=(IDLE,), log_unrecognised_screen=log_unrecognised_screen)
    clear_input(tmux, pane_id, sleep=sleep)
    tmux.run("send-keys", "-t", pane_id, "-l", "--", command)
    tmux.run("send-keys", "-t", pane_id, "Enter")


def send_interrupt(
    tmux: _Tmux,
    pane_id: str,
    *,
    log_unrecognised_screen: Callable[[str], None] | None = None,
) -> None:
    """Press Escape, only on a working screen: on any other it cancels or opens something."""
    _ready_screen(tmux, pane_id, allow=(WORKING,), log_unrecognised_screen=log_unrecognised_screen)
    tmux.run("send-keys", "-t", pane_id, "Escape")
