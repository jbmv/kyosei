"""The Claude Code transcript reader behind the credit check (KYO-643).

Given a session id and a quote, this module finds the session's transcript and says
whether the quote is the person's own words in it. It is section 5 of the KYO-638
proposal as amended by decision KYO-640 (P6, P7). Its caller is :mod:`kyosei.credits`
(KYO-644), which every credit-writing command goes through.

READING A TRANSCRIPT IS OPT-IN PER PROJECT (KYO-654, DC12; KYO-656). Nothing in this module
consults a project's configuration, because it is given only a session id. Every caller
asks :func:`kyosei.config.transcripts_opted_in` first, and in a project that has not opted
in calls none of the readers below, so no transcript and no prompt history is opened.
``tests/test_kyo656_transcript_opt_in.py`` fails on a module that imports this one and does
not ask.

WHERE THE FILES ARE, FROM CLAUDE CODE'S OWN DOCUMENTATION (KYO-643.c5). Read on
2026-11-06 from three pages:

* https://code.claude.com/docs/en/env-vars, the ``CLAUDE_CONFIG_DIR`` row: "Override
  the configuration directory (default: ``~/.claude``). All settings, session history,
  and plugins are stored under this path." The same row says it can be set "in your
  shell, user settings, or managed settings".
* https://code.claude.com/docs/en/claude-directory: "If you set ``CLAUDE_CONFIG_DIR``,
  every ``~/.claude`` path on this page lives under that directory instead." That page
  lists ``projects/<project>/<session>.jsonl`` (the transcript),
  ``projects/<project>/<session>/subagents/`` (subagent transcripts) and
  ``history.jsonl`` ("Prompts you've typed, with timestamp and project path"), the last
  under "Kept until you delete them", while transcripts are deleted after
  ``cleanupPeriodDays`` (default 30).
* https://code.claude.com/docs/en/sessions, "Where transcripts are stored":
  ``<project>`` is "your working directory path with non-alphanumeric characters
  replaced by ``-``", truncated to 200 characters plus a hash for a long path, and
  ``CLAUDE_CODE_PROJECT_DIR_NAME`` (set together with ``CLAUDE_CONFIG_DIR``) replaces
  the derived name altogether.

So :func:`config_dir` is ``$CLAUDE_CONFIG_DIR`` when it is set and ``~/.claude``
otherwise, and both the transcript and ``history.jsonl`` are looked for under it. The
``<project>`` name is never recomputed: the fallback search matches the session id as
a file name under every project directory, which survives both the truncation rule and
``CLAUDE_CODE_PROJECT_DIR_NAME``. One limit follows from the first page: a
``CLAUDE_CONFIG_DIR`` set only in a settings file's ``env`` block reaches the processes
Claude Code starts (hooks, and the agent's shell commands) but not a ``kyo`` started
from an ordinary terminal, which then looks under ``~/.claude``. The primary lookup is
unaffected, because it reads the path the session's own hook recorded
(:func:`kyosei.cmd_sessions.recorded_transcript_path`).

The same page also says "The entry format is internal to Claude Code and changes
between versions". Every shape below was therefore read from this project's own
transcripts (Claude Code 2.1.241 to 2.1.319), and every step ends in a named outcome,
the rule :mod:`kyosei.stop_target` and :mod:`kyosei.cmd_sessions` already follow, so a
format change turns the check off loudly instead of producing a false answer.

WHICH RECORDS ARE THE PERSON'S WORDS. Three forms, per KYO-640:

* a typed prompt: a ``user`` record with ``origin.kind == "human"``, not ``isMeta``,
  not a sidechain, no ``toolUseResult``, whose content is a string or a list of
  ``text`` blocks (``image`` blocks beside them are ignored), and which is not ``!``
  shell input or output. A slash command's record contributes only its
  ``<command-args>``;
* a message sent while the agent is mid-turn: an ``attachment`` record whose
  attachment is a ``queued_command`` with ``origin.kind == "human"`` and
  ``commandMode == "prompt"``;
* an answer to a multiple-choice question: the values of ``toolUseResult.answers`` on
  the tool result of an ``AskUserQuestion`` call made in the same transcript. Only the
  answers are read, never the question text or the option descriptions beside them, so
  the agent's own words in the question cannot match.

A prompt the person sent from Claude Code's suggestions (``promptSource ==
"suggestion_accepted"`` on a typed record) is read as the person's turn but under its own
form, ``accepted-suggestion`` (KYO-640.n1): it is creditable as their choice and is never
shown as their writing. :attr:`CheckResult.words_form` names the form in the store's
vocabulary (``typed``, ``mid-turn``, ``choice``, ``accepted-suggestion``).

Everything else is excluded: the agent's text, other tool results (where the agent's
own commands echo a quote back), hook output and other attachments, injected skill
text, peer messages, task notifications, and records without ``origin``. A transcript
in which no record carries ``origin`` at all (Claude Code before about 2.1.250) is
``unreadable``, never guessed at. Only the top-level transcript is read: a subagent's
transcript carries the parent's session id, and its first prompt is the orchestrator's.

PASTED CONTENT IS NEVER THE PERSON'S WORDS (KYO-640, P7). The transcript stores a paste
expanded inside the same string as the typed text. ``history.jsonl`` holds the same
prompt with each paste as a placeholder (``[Pasted text #2 +37 lines]``). A turn is
paired with a history line of the same session whose text outside the placeholders
matches the turn's text around them, and then only the typed segments count. A bracketed
paste also reaches the transcript wrapped in a ``<pasted_content id=...>`` element (measured
in this project's transcripts, including a prompt that arrived through tmux); where that is
present its contents are excluded directly and no history line is needed. A turn neither
route resolves is settled by the turn index when the project keeps one (KYO-657): the
index line, confirmed from this transcript at Stop, records that the prompt carried no
paste, and a Remote Control turn, which the prompt history does not hold, can then match as
``matched``. A turn with no pairable history line and no index line can still match, as
``matched-paste-unknown``. One case is not covered and cannot be from these files: a paste
Claude Code inserts as plain text with neither a placeholder nor the element. It is
indistinguishable from typing.

THE SECOND SOURCE. When the transcript is absent or unreadable, the prompt history is
searched for the session's typed prompts (``display`` with placeholders removed), and a
match there is ``matched`` with the history named as its source. When the transcript is
present and readable it alone decides (KYO-640: a mismatch against a present transcript
is refused), and the history serves only to find paste boundaries.

THE ARCHIVE (KYO-683). Claude Code deletes transcripts after ``cleanupPeriodDays``. When no
original remains, :func:`candidate_transcripts` falls back to the project's copy under
``$KYOSEI_HOME/transcript-archive/`` (:mod:`kyosei.transcript_archive`), which holds the
same records, so every reader below answers from the archive with the outcomes it gives
for the original.

THE TURN INDEX (KYO-657). :func:`read_turn_facts` reads new records from a byte offset and
returns, without any text, each prompt's id, its record's uuid and time, the kind of its
``origin``, and whether a paste element is present. ``kyosei.cmd_turns`` confirms the
index's lines from them at Stop.

WHAT LEAVES THIS MODULE FOR THE STORE (KYO-643.c4). :meth:`CheckResult.store_fields` is
the matched record's id, its own timestamp, the outcome and the form of the matched words
(KYO-644), and nothing else: not the turn's text, not its neighbours, not the transcript
path, not a hash.

THE SESSION VIEW (KYO-688). :class:`SessionTranscript` reads a transcript incrementally
from a byte offset and returns the session as Kyosei objects: the person's turns, replies
(one per assistant message, with its usage), tool calls with their results, and subagent
references, each addressed by the uuid of its transcript record. :func:`clear_successor`
finds the transcript a session continued in after ``/clear``. Nothing outside this module
reads a row's fields; :mod:`kyosei.session_layer` composes these objects with the pane
adapter's. The samples these shapes are tested against are recorded from throwaway
sessions and live in ``tests/fixtures/session_layer/``.

WHAT ELSE THE SESSION HOLDS (KYO-692). A message delivered by anything other than the person
is a :class:`Delivery`: a ``user`` record or a ``queued_command`` attachment whose ``origin``
is present and not ``human``, and an older ``queued_command`` of mode ``task-notification``
with no ``origin``. Each delivery was found recorded one way only across this project's
transcripts, so nothing here pairs a ``user`` record with an attachment. An image the person
sent stays in the transcript: :attr:`Turn.images` lists what its record holds, and
:meth:`SessionTranscript.image` reads one image's bytes back when it is asked for, so a read
of the session never holds them. A reply's :attr:`Reply.thinking` is the text the
transcript's thinking blocks record. Most such blocks record only a signature, which
:attr:`Reply.thinking_omitted` counts, and which no text is made up for.

A DISCUSSION SPAN (KYO-640.n1, KYO-644). A decided credit may cite the discussion that led
to it by its first and last record. :func:`measure_span` reads the records between them and
returns counts and times only: the person's turns, the agent's turns, the active seconds
(gaps longer than :data:`SPAN_BREAK_SECONDS` are left out, because a long gap is time away)
and the first and last timestamps. No text from the span leaves the module.
"""

from __future__ import annotations

import base64
import json
import os
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------- outcomes

#: The quote is in exactly one of the person's turns, outside any pasted content.
MATCHED = "matched"
#: The quote is in exactly one of the person's turns, and no history line could be
#: paired with that turn, so pasted content in it cannot be ruled out (KYO-638 5.3).
MATCHED_PASTE_UNKNOWN = "matched-paste-unknown"
#: The transcript was read and none of the person's turns holds the quote. Refused.
NOT_FOUND = "not-found"
#: More than one of the person's turns holds the quote and none was named. Refused.
AMBIGUOUS = "ambiguous"
#: No transcript file exists for the session, and the history did not match either.
TRANSCRIPT_ABSENT = "transcript-absent"
#: A file exists and no record in it has a shape this reader recognises.
UNREADABLE = "unreadable"

OUTCOMES: tuple[str, ...] = (
    MATCHED,
    MATCHED_PASTE_UNKNOWN,
    NOT_FOUND,
    AMBIGUOUS,
    TRANSCRIPT_ABSENT,
    UNREADABLE,
)

#: Where a turn was read from.
SOURCE_TRANSCRIPT = "transcript"
SOURCE_HISTORY = "history"

#: The three forms KYO-640 counts as the person's words.
FORM_TYPED = "typed"
FORM_MID_TURN = "mid-turn"
FORM_ANSWER = "answer"
#: KYO-640.n1: a prompt the person sent from Claude Code's suggestions. Read as their
#: turn, under its own form, so it is credited as their choice and never as their writing.
FORM_ACCEPTED_SUGGESTION = "accepted-suggestion"

#: The ``promptSource`` value Claude Code writes on a prompt sent from its suggestions.
SUGGESTION_PROMPT_SOURCE = "suggestion_accepted"

#: Each form as the store names it (``contracts.CREDIT_WORDS_FORMS``). An answer to a
#: multiple-choice question is stored as ``choice``.
WORDS_FORMS: dict[str, str] = {
    FORM_TYPED: "typed",
    FORM_MID_TURN: "mid-turn",
    FORM_ANSWER: "choice",
    FORM_ACCEPTED_SUGGESTION: "accepted-suggestion",
}

#: A gap between two records of a span longer than this is a break and is not counted
#: as active time (KYO-640.n1: "that time could have been spent eating"). Fifteen
#: minutes: longer than an agent's ordinary tool run or a person's reading of a reply,
#: shorter than a meal.
SPAN_BREAK_SECONDS = 15 * 60

#: The prefix of a history turn's id. A history line carries no uuid, so its id is its
#: own timestamp in milliseconds, which is all the store keeps of it in any case.
HISTORY_ID_PREFIX = "history:"

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

#: A paste placeholder in ``history.jsonl``'s ``display``. Measured forms:
#: ``[Pasted text #2 +37 lines]`` and, for a one-line paste, ``[Pasted text #1]``.
PASTE_PLACEHOLDER = re.compile(r"\[Pasted text #\d+(?: \+\d+ lines?)?\]")

#: Spans the harness inserts into a turn's text. None was found inside a person's turn
#: in this project's transcripts; they are removed so that one cannot match if it is.
_INJECTED = re.compile(r"<(system-reminder|pasted-content)\b[^>]*>.*?</\1>", re.S)

#: A paste as the transcript carries it (KYO-657.c4). The closing tag repeats the opening
#: tag's id (measured: ``</pasted_content id="8cf2">``), which the backreference requires, so
#: text inside a paste that spells a bare closing tag does not end the element early.
PASTED_ELEMENT = re.compile(
    r"<pasted_content\b(?P<attrs>[^>]*)>.*?</pasted_content\b(?P=attrs)>", re.S
)

#: How a typed record's content begins when it is ``!`` shell input or its output.
_SHELL_PREFIXES = ("<bash-input>", "<bash-stdout>", "<bash-stderr>")

_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)

#: An omission inside a quote (KYO-638 5.4): `` [...] `` or ``[…]``.
_OMISSION = re.compile(r"\s*\[(?:\.\.\.|…)\]\s*")


# ---------------------------------------------------------------- locating


def config_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Claude Code's configuration directory: ``$CLAUDE_CONFIG_DIR`` or ``~/.claude``.

    Stated from the documentation cited in this module's header.
    """
    env = os.environ if environ is None else environ
    value = env.get("CLAUDE_CONFIG_DIR")
    return Path(value).expanduser() if value else Path.home() / ".claude"


def history_path(config: Path) -> Path:
    """The prompt history, ``<config dir>/history.jsonl``."""
    return config / "history.jsonl"


def archived_transcripts(session_id: str, archive: Path) -> list[Path]:
    """The archived copies of ``session_id``'s transcript in ``archive``.

    The copy itself, and any copy kept aside when the original was rewritten
    (:mod:`kyosei.transcript_archive`). :func:`read_transcripts` merges them by record id.
    """
    if not archive.is_dir():
        return []
    found = [archive / f"{session_id}.jsonl"]
    found += sorted(archive.glob(f"{session_id}.rewritten-*.jsonl"))
    return [p for p in found if p.is_file()]


def candidate_transcripts(
    session_id: str, *, config: Path, recorded: Path | None = None, archive: Path | None = None
) -> list[Path]:
    """The transcript files for ``session_id``, the recorded one first.

    ``archive`` is the project's transcript archive (KYO-683), consulted only when no
    original remains.

    ``recorded`` is the path the session's own hook wrote into ``sessions.json``. The
    search after it matches ``<session id>.jsonl`` under every project directory and
    never recomputes the project directory's name. More than one file is possible when a
    transcript was copied; all are returned, and :func:`read_transcripts` reads them as
    one session, since they hold the same records.
    """
    if not _UUID.match(session_id):
        raise ValueError(f"{session_id!r} is not a Claude Code session id (a UUID)")
    found: list[Path] = []
    if recorded is not None and recorded.is_file():
        found.append(recorded)
    projects = config / "projects"
    if projects.is_dir():
        for project in sorted(projects.iterdir()):
            path = project / f"{session_id}.jsonl"
            if path.is_file() and all(not path.samefile(p) for p in found):
                found.append(path)
    if not found and archive is not None:
        found = archived_transcripts(session_id, archive)
    return found


# ---------------------------------------------------------------- turns


@dataclass(frozen=True)
class ImageRef:
    """An image in a person's turn, without its bytes (KYO-692).

    ``index`` counts the record's image blocks, so a block that cannot be served leaves a
    gap rather than renumbering the others. ``size`` is the decoded length in bytes.
    """

    index: int
    media_type: str
    size: int


@dataclass(frozen=True)
class SessionImage:
    """One image's bytes, read back from the transcript."""

    media_type: str
    data: bytes


@dataclass(frozen=True)
class Turn:
    """One message of the person's, as far as this reader can tell.

    ``segments`` are the person's words: the whole text, or, when a history line has
    been paired with the turn, the typed text between its pastes. ``full_text`` is the
    whole turn, kept only so that a quote found inside a paste can be named as such; it
    never leaves the module through :class:`CheckResult`.
    """

    id: str
    timestamp: str
    form: str
    source: str
    segments: tuple[str, ...]
    paste_known: bool
    full_text: str = field(default="", repr=False, compare=False)
    #: The record's ``promptId``, which the turn index is keyed on; absent on a mid-turn
    #: message and on a history line.
    prompt_id: str | None = field(default=None, compare=False)
    #: The images the turn's record holds. Set by :class:`SessionTranscript` only.
    images: tuple[ImageRef, ...] = field(default=(), compare=False)


@dataclass
class TranscriptRead:
    """What reading a session's transcript files found."""

    turns: list[Turn]
    #: False when no record in any file carries ``origin``: the format is unrecognised.
    readable: bool
    why: str


def _text_of(content: object) -> str | None:
    """A typed record's text: a string, or the ``text`` blocks of a list.

    ``image`` blocks beside the text are ignored. Any other block type makes the record
    something other than typed text, and None is returned.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            return None
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif kind != "image":
            return None
    return "\n".join(parts) if parts else None


def _clean(text: str) -> str:
    return _INJECTED.sub("", text)


def _typed_words(text: str) -> str | None:
    """The person's words in a typed record's text, or None when it holds none."""
    stripped = text.lstrip()
    if stripped.startswith(_SHELL_PREFIXES):
        return None
    if stripped.startswith(("<command-message>", "<command-name>")):
        args = _COMMAND_ARGS.search(text)
        return args.group(1) if args and args.group(1).strip() else None
    return _clean(text)


def _segments_of(words: str) -> tuple[tuple[str, ...], bool]:
    """The person's segments of ``words`` and whether paste boundaries are known.

    A turn holding a ``<pasted_content>`` element is split around it, the paste left out;
    the boundaries are then known from the transcript alone.
    """
    if PASTED_ELEMENT.search(words) is None:
        return (words,), False
    parts = tuple(part for part in PASTED_ELEMENT.split(words)[0::2] if part.strip())
    return parts, True


def _is_human(origin: object) -> bool:
    return isinstance(origin, dict) and origin.get("kind") == "human"


def _parse_lines(path: Path) -> Iterable[dict]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(record, dict):
                yield record


def _questions_asked(record: dict) -> set[str]:
    """The ids of the ``AskUserQuestion`` calls in an assistant record."""
    found: set[str] = set()
    content = (record.get("message") or {}).get("content")
    for block in content if isinstance(content, list) else []:
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("name") == "AskUserQuestion"
            and isinstance(block.get("id"), str)
        ):
            found.add(block["id"])
    return found


def _is_queued_command(record: dict) -> bool:
    attachment = record.get("attachment")
    return isinstance(attachment, dict) and attachment.get("type") == "queued_command"


def _person_turn(record: dict, asked: set[str]) -> Turn | None:
    """The person's message in one non-sidechain record with a uuid and a timestamp, or None.

    ``asked`` holds the ``AskUserQuestion`` call ids seen so far in the same file. A
    ``queued_command`` from anyone but the person, such as a peer's message delivered
    while the agent was mid-turn, is not the person's and yields None.
    """
    uuid, stamp, kind = record["uuid"], record["timestamp"], record.get("type")
    if kind == "attachment":
        if not _is_queued_command(record):
            return None
        attachment = record["attachment"]
        if (
            not _is_human(attachment.get("origin"))
            or attachment.get("commandMode") != "prompt"
            or attachment.get("isMeta")
        ):
            return None
        text = _text_of(attachment.get("prompt"))
        words = _typed_words(text) if text is not None else None
        if words is None:
            return None
        segments, known = _segments_of(words)
        return Turn(uuid, stamp, FORM_MID_TURN, SOURCE_TRANSCRIPT, segments, known, words)
    if kind != "user":
        return None
    content = (record.get("message") or {}).get("content")
    result = record.get("toolUseResult")
    if result is not None:
        answers = _answers(record, content, result, asked)
        if not answers:
            return None
        joined = "\n".join(answers)
        return Turn(uuid, stamp, FORM_ANSWER, SOURCE_TRANSCRIPT, answers, True, joined)
    if record.get("isMeta") or not _is_human(record.get("origin")):
        return None
    text = _text_of(content)
    words = _typed_words(text) if text is not None else None
    if words is None:
        return None
    form = (
        FORM_ACCEPTED_SUGGESTION
        if record.get("promptSource") == SUGGESTION_PROMPT_SOURCE
        else FORM_TYPED
    )
    segments, known = _segments_of(words)
    prompt_id = record.get("promptId")
    return Turn(
        uuid,
        stamp,
        form,
        SOURCE_TRANSCRIPT,
        segments,
        known,
        words,
        prompt_id if isinstance(prompt_id, str) else None,
    )


def read_transcripts(paths: list[Path]) -> TranscriptRead:
    """The person's turns in a session's transcript files.

    Raises :class:`OSError` when a file cannot be opened; :func:`check_quote` turns that
    into the ``unreadable`` outcome.
    """
    turns: dict[str, Turn] = {}
    has_origin = False
    for path in paths:
        asked: set[str] = set()
        for record in _parse_lines(path):
            kind = record.get("type")
            if "origin" in record:
                has_origin = True
            if record.get("isSidechain"):
                continue
            if kind == "assistant":
                asked |= _questions_asked(record)
                continue
            if not isinstance(record.get("uuid"), str) or not isinstance(
                record.get("timestamp"), str
            ):
                continue
            if (
                kind == "attachment"
                and _is_queued_command(record)
                and "origin" in (record.get("attachment") or {})
            ):
                has_origin = True
            turn = _person_turn(record, asked)
            if turn is not None:
                turns[turn.id] = turn
    ordered = sorted(turns.values(), key=lambda t: t.timestamp)
    if not has_origin:
        return TranscriptRead(
            ordered,
            False,
            "no record in the transcript carries `origin`, so this reader cannot tell the "
            "person's turns from anything else (a Claude Code older than the format it "
            "was built against, or a changed format)",
        )
    return TranscriptRead(ordered, True, f"{len(ordered)} of the person's turns read")


def _answers(record: dict, content: object, result: object, asked: set[str]) -> tuple[str, ...]:
    """The person's answers in an ``AskUserQuestion`` tool result, or ``()``."""
    if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
        return ()
    if not isinstance(content, list):
        return ()
    paired = any(
        isinstance(block, dict)
        and block.get("type") == "tool_result"
        and block.get("tool_use_id") in asked
        and not block.get("is_error")
        for block in content
    )
    if not paired:
        return ()
    return tuple(v for v in result["answers"].values() if isinstance(v, str) and v.strip())


# ---------------------------------------------------------------- history


@dataclass(frozen=True)
class HistoryLine:
    """One ``history.jsonl`` line of the session: its typed segments and its time."""

    millis: int
    segments: tuple[str, ...]
    has_paste: bool

    @property
    def id(self) -> str:
        return f"{HISTORY_ID_PREFIX}{self.millis}"

    @property
    def timestamp(self) -> str:
        moment = datetime.fromtimestamp(self.millis / 1000, UTC)
        return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{self.millis % 1000:03d}Z"


def read_history(path: Path, session_id: str) -> list[HistoryLine]:
    """The session's prompts in ``history.jsonl``. ``[]`` when the file is absent.

    ``display`` holds the prompt as typed, each paste replaced by a placeholder. A line
    beginning ``!`` is a shell command, not words, and is skipped; a slash command keeps
    only its arguments, as a transcript's slash-command record does.
    """
    if not path.is_file():
        return []
    lines: list[HistoryLine] = []
    for record in _parse_lines(path):
        if record.get("sessionId") != session_id:
            continue
        display = record.get("display")
        millis = record.get("timestamp")
        if not isinstance(display, str) or not isinstance(millis, int):
            continue
        if display.lstrip().startswith("!"):
            continue
        command = re.match(r"\s*/[\w:.-]+(?:\s+|$)", display)
        if command:
            display = display[command.end() :]
        segments = tuple(PASTE_PLACEHOLDER.split(display))
        lines.append(HistoryLine(millis, segments, len(segments) > 1))
    return lines


def _pairs(turn_text: str, line: HistoryLine) -> bool:
    """Whether ``line`` is the history of the turn whose full text is ``turn_text``.

    The typed segments must make up the turn around the pastes: the first is its
    beginning, the last its end, and the rest occur in order between them. Without a
    paste the two texts must be equal.
    """
    text = normalise(turn_text)
    parts = [normalise(s) for s in line.segments]
    if len(parts) == 1:
        return text == parts[0]
    first, *middle, last = parts
    if not text.startswith(first) or not text.endswith(last):
        return False
    pos = len(first)
    end = len(text) - len(last)
    for part in middle:
        if not part:
            continue
        found = text.find(part, pos, end)
        if found < 0:
            return False
        pos = found + len(part)
    return pos <= end


def pair_with_history(turns: list[Turn], history: list[HistoryLine]) -> list[Turn]:
    """Each typed or mid-turn turn, with its paste boundaries when a history line pairs.

    A paired turn's segments become the history line's typed segments, and it is marked
    ``paste_known``. An answer is already ``paste_known``. A turn no line pairs with is
    returned unchanged, and a match in it is ``matched-paste-unknown``.
    """
    out: list[Turn] = []
    for turn in turns:
        if turn.form == FORM_ANSWER:
            out.append(turn)
            continue
        line = next((h for h in history if _pairs(turn.full_text, h)), None)
        if line is None:
            out.append(turn)
            continue
        out.append(
            Turn(
                turn.id,
                turn.timestamp,
                turn.form,
                turn.source,
                tuple(s for s in line.segments if s.strip()),
                True,
                turn.full_text,
                turn.prompt_id,
            )
        )
    return out


def apply_paste_index(turns: list[Turn], flags: Mapping[str, bool]) -> list[Turn]:
    """Each turn, marked ``paste_known`` when the turn index records no paste in it (KYO-657).

    ``flags`` maps a ``promptId`` or a record's ``uuid`` to whether that prompt carried
    pasted content (:func:`kyosei.cmd_turns.confirmed_paste_flags`); only a person's lines
    confirmed from this transcript are in it. A turn already resolved, or one the index
    does not mention or marks as pasted, is left unchanged.
    """
    out: list[Turn] = []
    for turn in turns:
        key = turn.prompt_id if turn.prompt_id in flags else turn.id
        if not turn.paste_known and flags.get(key) is False:
            turn = replace(turn, paste_known=True)
        out.append(turn)
    return out


def history_turns(history: list[HistoryLine]) -> list[Turn]:
    """The history lines as turns, for a session whose transcript cannot be read."""
    return [
        Turn(
            h.id,
            h.timestamp,
            FORM_TYPED,
            SOURCE_HISTORY,
            tuple(s for s in h.segments if s.strip()),
            True,
            "".join(h.segments),
        )
        for h in history
        if any(s.strip() for s in h.segments)
    ]


# ---------------------------------------------------------------- matching


def normalise(text: str) -> str:
    """Unicode NFC, whitespace runs collapsed to one space, ends stripped.

    Case and punctuation are kept (KYO-638 5.4).
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def quote_pieces(quote: str) -> list[str]:
    """The quote split at its omission marks, each piece normalised."""
    return [p for p in (normalise(x) for x in _OMISSION.split(quote)) if p]


def _find_whole(haystack: str, needle: str, start: int) -> int:
    """The first occurrence of ``needle`` at or after ``start`` that does not begin or
    end inside a word, or -1. Without this, the quote "yes" would match "yesterday"."""
    pos = start
    while True:
        at = haystack.find(needle, pos)
        if at < 0:
            return -1
        before = haystack[at - 1] if at > 0 else ""
        after = haystack[at + len(needle)] if at + len(needle) < len(haystack) else ""
        head_ok = not (needle[0].isalnum() and before.isalnum())
        tail_ok = not (needle[-1].isalnum() and after.isalnum())
        if head_ok and tail_ok:
            return at
        pos = at + 1


def pieces_in(pieces: list[str], segments: Iterable[str]) -> bool:
    """Whether every piece occurs, in order, each inside one segment.

    A piece never spans two segments, so a quote cannot run across a paste.
    """
    normalised = [normalise(s) for s in segments]
    index, pos = 0, 0
    for piece in pieces:
        while index < len(normalised):
            at = _find_whole(normalised[index], piece, pos)
            if at >= 0:
                pos = at + len(piece)
                break
            index, pos = index + 1, 0
        else:
            return False
    return True


# ---------------------------------------------------------------- the check


@dataclass(frozen=True)
class CheckResult:
    """The answer to one check, and everything a caller may show or store.

    ``why`` names the turns and files involved by id and count only, never by their
    text, so it can be shown without leaking the transcript.
    """

    outcome: str
    why: str
    #: Where the matched turn was read: ``transcript`` or ``history``; None if nothing
    #: matched.
    source: str | None = None
    #: The matched record's ``uuid``, or a history line's ``history:<ms>`` id.
    turn: str | None = None
    #: The matched record's own timestamp.
    observed_at: str | None = None
    #: The matched turn's form (typed, mid-turn, answer, accepted-suggestion).
    form: str | None = None
    #: For ``ambiguous``: each matching turn's (id, timestamp), to name one with ``turn``.
    candidates: tuple[tuple[str, str], ...] = ()

    @property
    def accepted(self) -> bool:
        """Whether a credit may be written: refused outcomes are not-found and ambiguous."""
        return self.outcome not in (NOT_FOUND, AMBIGUOUS)

    @property
    def words_form(self) -> str | None:
        """The matched turn's form in the store's vocabulary, or None if nothing matched."""
        return WORDS_FORMS.get(self.form) if self.form is not None else None

    def store_fields(self) -> dict[str, str | None]:
        """Everything the store keeps from the transcript (KYO-643.c4, KYO-638 5.5), and
        the form of the matched words (KYO-640.n1, KYO-644)."""
        return {
            "transcript_check": self.outcome,
            "source_turn": self.turn,
            "observed_at": self.observed_at,
            "words_form": self.words_form,
        }


def _matching(turns: list[Turn], pieces: list[str]) -> list[Turn]:
    return [t for t in turns if pieces_in(pieces, t.segments)]


def _decide(
    turns: list[Turn], pieces: list[str], chosen: str | None, scope: str
) -> CheckResult | None:
    """The outcome among ``turns``, or None when no turn holds the quote."""
    hits = _matching(turns, pieces)
    if chosen is not None:
        named = [t for t in turns if t.id == chosen]
        if not named:
            return CheckResult(
                NOT_FOUND, f"no turn of the person's in the {scope} has the id {chosen}"
            )
        if named[0] not in hits:
            others = ", ".join(t.id for t in hits) or "none"
            return CheckResult(
                NOT_FOUND,
                f"turn {chosen} in the {scope} does not hold the quote (turns that do: {others})",
            )
        hits = named
    if not hits:
        return None
    if len(hits) > 1:
        return CheckResult(
            AMBIGUOUS,
            f"the quote is in {len(hits)} of the person's turns in the {scope}; name one",
            candidates=tuple((t.id, t.timestamp) for t in hits),
        )
    hit = hits[0]
    outcome = MATCHED if hit.paste_known else MATCHED_PASTE_UNKNOWN
    why = f"the quote is in the person's {hit.form} turn {hit.id} in the {scope}"
    if outcome == MATCHED_PASTE_UNKNOWN:
        why += (
            "; no prompt-history line pairs with that turn, so pasted content in it "
            "cannot be ruled out"
        )
    return CheckResult(outcome, why, hit.source, hit.id, hit.timestamp, hit.form)


def check_quote(
    session_id: str,
    quote: str,
    *,
    turn: str | None = None,
    recorded: Path | None = None,
    config: Path | None = None,
    environ: Mapping[str, str] | None = None,
    archive: Path | None = None,
    paste_flags: Mapping[str, bool] | None = None,
) -> CheckResult:
    """Whether ``quote`` is the person's own words in session ``session_id``.

    ``turn`` names one of the person's turns (a record ``uuid`` or a ``history:<ms>``
    id) when the quote occurs in more than one. ``recorded`` is the transcript path the
    hooks recorded (:func:`kyosei.cmd_sessions.recorded_transcript_path`). ``config``
    overrides :func:`config_dir`. ``archive`` is the project's transcript archive, read when
    no original remains (KYO-683). ``paste_flags`` is the turn index's confirmed answer
    about pastes (:func:`apply_paste_index`, KYO-657.c4).

    Raises :class:`ValueError` for a session id that is not a UUID or a quote with no
    words; every other condition is a named outcome.
    """
    pieces = quote_pieces(quote)
    if not pieces:
        raise ValueError("the quote has no words to look for")
    base = config if config is not None else config_dir(environ)
    paths = candidate_transcripts(session_id, config=base, recorded=recorded, archive=archive)
    history = read_history(history_path(base), session_id)

    if paths:
        try:
            read = read_transcripts(paths)
        except OSError as exc:
            fallback = CheckResult(
                UNREADABLE, f"the transcript could not be read ({exc.strerror or exc})"
            )
        else:
            if read.readable:
                turns = apply_paste_index(pair_with_history(read.turns, history), paste_flags or {})
                decided = _decide(turns, pieces, turn, SOURCE_TRANSCRIPT)
                if decided is not None:
                    return decided
                return CheckResult(NOT_FOUND, _not_found_why(read.turns, turns, pieces, history))
            fallback = CheckResult(UNREADABLE, read.why)
    else:
        fallback = CheckResult(
            TRANSCRIPT_ABSENT,
            f"no transcript for session {session_id} under {base / 'projects'}"
            + (" or at the recorded path" if recorded is not None else ""),
        )

    # The transcript is absent or unreadable: the prompt history is the second source.
    decided = _decide(history_turns(history), pieces, turn, SOURCE_HISTORY)
    if decided is not None and decided.outcome != NOT_FOUND:
        return decided
    consulted = (
        f"the prompt history holds {len(history)} line(s) for the session and none holds "
        "the quote outside pasted content"
        if history
        else "the prompt history holds no line for the session"
    )
    return CheckResult(fallback.outcome, f"{fallback.why}; {consulted}")


def _not_found_why(
    raw: list[Turn], paired: list[Turn], pieces: list[str], history: list[HistoryLine]
) -> str:
    """Why a readable transcript did not match, naming a paste when that is the cause."""
    in_paste = [
        p.id
        for p in paired
        if p.paste_known and pieces_in(pieces, (p.full_text,)) and not pieces_in(pieces, p.segments)
    ]
    if in_paste:
        return (
            f"the words occur only inside pasted content of turn {', '.join(in_paste)}, "
            "and pasted content is not the person's words"
        )
    why = f"none of the person's {len(raw)} turns in the transcript holds the quote"
    if any(pieces_in(pieces, h.segments) for h in history):
        why += (
            "; the prompt history does, but the transcript is present and decides "
            "(KYO-640: a mismatch against a present transcript is refused)"
        )
    return why


def find_person_turns(
    session_id: str,
    *,
    recorded: Path | None = None,
    config: Path | None = None,
    environ: Mapping[str, str] | None = None,
    archive: Path | None = None,
) -> TranscriptRead:
    """The person's turns in a session's transcript, with paste boundaries applied.

    The one function another harness would provide its own version of (KYO-638 5.6).
    An absent transcript gives no turns and ``readable`` False.
    """
    base = config if config is not None else config_dir(environ)
    paths = candidate_transcripts(session_id, config=base, recorded=recorded, archive=archive)
    if not paths:
        return TranscriptRead([], False, f"no transcript for session {session_id}")
    read = read_transcripts(paths)
    history = read_history(history_path(base), session_id)
    return TranscriptRead(pair_with_history(read.turns, history), read.readable, read.why)


# ---------------------------------------------------------------- discussion spans


#: :attr:`SpanMeasure.outcome` values.
SPAN_MEASURED = "measured"
#: No transcript for the session: the span's ids are stored and its measures are empty.
SPAN_TRANSCRIPT_ABSENT = TRANSCRIPT_ABSENT
#: The transcript is present and readable, and one of the cited records is not in it.
SPAN_TURN_NOT_FOUND = "turn-not-found"
#: The cited first record comes after the cited last one.
SPAN_REVERSED = "reversed"


@dataclass(frozen=True)
class SpanMeasure:
    """What :func:`measure_span` found. Counts and timestamps only, never text."""

    outcome: str
    why: str
    person_turns: int | None = None
    agent_turns: int | None = None
    active_seconds: int | None = None
    started_at: str | None = None
    ended_at: str | None = None

    @property
    def measured(self) -> bool:
        return self.outcome == SPAN_MEASURED

    def store_fields(self) -> dict[str, int | str | None]:
        """The ``span_*`` measures a credit stores; all None when nothing was measured."""
        return {
            "span_person_turns": self.person_turns,
            "span_agent_turns": self.agent_turns,
            "span_active_seconds": self.active_seconds,
            "span_started_at": self.started_at,
            "span_ended_at": self.ended_at,
        }


def _moment(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _has_text(record: dict) -> bool:
    """Whether an assistant record carries text addressed to the person (not only tool
    calls or thinking)."""
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
        and block["text"].strip()
        for block in content
    )


def _main_chain(paths: list[Path]) -> list[dict]:
    """The session's top-level records in file order, each uuid once, sidechains out."""
    seen: set[str] = set()
    records: list[dict] = []
    for path in paths:
        for record in _parse_lines(path):
            uuid = record.get("uuid")
            if record.get("isSidechain") or not isinstance(uuid, str) or uuid in seen:
                continue
            if not isinstance(record.get("timestamp"), str):
                continue
            seen.add(uuid)
            records.append(record)
    return records


def measure_span(
    session_id: str,
    first: str,
    last: str,
    *,
    recorded: Path | None = None,
    config: Path | None = None,
    environ: Mapping[str, str] | None = None,
    break_seconds: int = SPAN_BREAK_SECONDS,
    archive: Path | None = None,
) -> SpanMeasure:
    """Measure the discussion from record ``first`` to record ``last`` of a session.

    The person's turns are counted by the rule :func:`read_transcripts` applies. The
    agent's count is its REPLIES to the person (KYO-644.c9): one per agent turn -- the run
    of ``assistant`` records between two of the person's turns, or between the last one
    and the end of the span -- whose last assistant record carries text. Counting every
    assistant record with text would count one reply several times, because Claude Code
    writes a reply as several records split between tool calls; that inflated an early
    sheet to about nineteen agent messages per four of the person's. Active time is the sum of the
    gaps between consecutive records of the span, leaving out every gap longer than
    ``break_seconds``. ``first`` and ``last`` are record uuids and may be any records of
    the top-level transcript.

    Raises :class:`ValueError` for a session id that is not a UUID.
    """
    base = config if config is not None else config_dir(environ)
    paths = candidate_transcripts(session_id, config=base, recorded=recorded, archive=archive)
    if not paths:
        return SpanMeasure(
            SPAN_TRANSCRIPT_ABSENT,
            f"no transcript for session {session_id}; the span is cited by id and not measured",
        )
    try:
        read = read_transcripts(paths)
        records = _main_chain(paths)
    except OSError as exc:
        return SpanMeasure(
            UNREADABLE,
            f"the transcript could not be read ({exc.strerror or exc}); the span is cited "
            "by id and not measured",
        )
    if not read.readable:
        return SpanMeasure(UNREADABLE, f"{read.why}; the span is cited by id and not measured")
    index = {record["uuid"]: n for n, record in enumerate(records)}
    missing = [turn for turn in (first, last) if turn not in index]
    if missing:
        return SpanMeasure(
            SPAN_TURN_NOT_FOUND,
            f"the transcript of session {session_id} has no record {', '.join(missing)} "
            f"({len(records)} top-level records read)",
        )
    start, end = index[first], index[last]
    if start > end:
        return SpanMeasure(
            SPAN_REVERSED,
            f"record {first} comes after record {last} in session {session_id}",
        )
    person_ids = {turn.id for turn in read.turns}
    span = records[start : end + 1]
    person = 0
    agent = 0
    # The agent's last assistant record since the person's last turn: a reply counts once,
    # when that run ends in text (KYO-644.c9).
    last_said: bool | None = None
    for record in span:
        if record["uuid"] in person_ids:
            person += 1
            agent += 1 if last_said else 0
            last_said = None
        elif record.get("type") == "assistant":
            last_said = _has_text(record)
    agent += 1 if last_said else 0
    moments = [m for m in (_moment(record["timestamp"]) for record in span) if m is not None]
    moments.sort()
    active = 0.0
    for before, after in zip(moments, moments[1:], strict=False):
        gap = (after - before).total_seconds()
        if gap <= break_seconds:
            active += gap
    return SpanMeasure(
        SPAN_MEASURED,
        f"{len(span)} records of session {session_id} measured; gaps over "
        f"{break_seconds} seconds left out of the active time",
        person_turns=person,
        agent_turns=agent,
        active_seconds=int(round(active)),
        started_at=span[0]["timestamp"],
        ended_at=span[-1]["timestamp"],
    )


# ---------------------------------------------------------------- the turn index


@dataclass(frozen=True)
class TurnFact:
    """One prompt as the transcript records it, with no text (KYO-657).

    ``prompt_id`` is None for a message sent while the agent is mid-turn, which Claude Code
    records as a ``queued_command`` attachment without one; ``turn`` is the record's uuid.
    ``origin`` is the transcript's ``origin.kind``, or None when the record has no origin.
    """

    prompt_id: str | None
    turn: str
    at: str
    origin: str | None
    pasted: bool | None


def _origin_kind(origin: object) -> str | None:
    if isinstance(origin, dict) and isinstance(origin.get("kind"), str):
        return origin["kind"]
    return None


def _pasted(text: object) -> bool | None:
    """Whether a prompt's content holds a paste element; None when it has no text."""
    words = _text_of(text)
    return None if words is None else PASTED_ELEMENT.search(words) is not None


def read_turn_facts(path: Path, offset: int) -> tuple[list[TurnFact], int]:
    """The prompts in ``path`` past byte ``offset``, and the offset to read from next.

    Reads only whole lines, so a line still being written is left for the next call, and
    the returned offset never passes it. Each ``promptId`` yields one fact: the record
    that opened the prompt, never the tool results and injected records sharing its id. A
    ``queued_command`` is included only when it is the person's. Raises :class:`OSError`.
    """
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()
    end = data.rfind(b"\n") + 1
    if end == 0:
        return [], offset
    facts: list[TurnFact] = []
    seen: set[str] = set()
    for raw in data[:end].split(b"\n"):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("isSidechain"):
            continue
        uuid, stamp = record.get("uuid"), record.get("timestamp")
        if not isinstance(uuid, str) or not isinstance(stamp, str):
            continue
        kind = record.get("type")
        if kind == "attachment":
            attachment = record.get("attachment")
            if (
                isinstance(attachment, dict)
                and attachment.get("type") == "queued_command"
                and attachment.get("commandMode") == "prompt"
                and _is_human(attachment.get("origin"))
                and not attachment.get("isMeta")
            ):
                prompt = attachment.get("prompt")
                facts.append(TurnFact(None, uuid, stamp, "human", _pasted(prompt)))
            continue
        if kind != "user" or record.get("toolUseResult") is not None or record.get("isMeta"):
            continue
        prompt_id = record.get("promptId")
        if not isinstance(prompt_id, str) or prompt_id in seen:
            continue
        seen.add(prompt_id)
        content = (record.get("message") or {}).get("content")
        facts.append(
            TurnFact(prompt_id, uuid, stamp, _origin_kind(record.get("origin")), _pasted(content))
        )
    return facts, offset + end


# ---------------------------------------------------------------- the session view


#: The tool name a subagent is started with.
AGENT_TOOL = "Agent"

#: How far into a sibling file :func:`clear_successor` looks for the record naming the
#: session it continues. In the recorded pair the link is in the first 42 records, within
#: 90 kilobytes; 512 kilobytes allows for a large instructions attachment before it.
_CLEAR_LINK_WINDOW_BYTES = 512 * 1024

#: The command row at the head of a transcript started by ``/clear``.
_CLEAR_COMMAND = "<command-name>/clear</command-name>"

#: ``message.model`` on a reply Claude Code wrote itself, such as an API error; its usage is
#: all zeros and says nothing about the session (KYO-691).
_SYNTHETIC_MODEL = "<synthetic>"

_COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.S)

#: A row holding a slash command's output and nothing else. Both forms are measured to 2.1.319:
#: a ``system`` row of subtype ``local_command`` and an older ``user`` row. A row beginning
#: with ``<local-command-caveat>`` is the harness's notice, not output.
_COMMAND_OUTPUT = re.compile(r"<local-command-(stdout|stderr)>(.*)</local-command-\1>", re.S)

#: Terminal styling in a command's output: CSI sequences such as colours, and OSC sequences.
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")

#: Claude Code's cached model catalogue, under its configuration directory.
_MODEL_CATALOG_DIR = Path("cache") / "model-catalog"

#: The catalogue's surface for the command-line tool; the desktop app's is ``ccd``.
_CATALOG_SURFACE = "cc"

#: Models the catalogue gives 1M for but which Claude Code runs at 200K unless the session
#: asked for ``[1m]`` (2.1.319's built-in table, ``context.maximum_window``). The transcript
#: shows the request in ``requestedModel``; without it the window is not known.
_WINDOW_NEEDS_SUFFIX = frozenset({"claude-opus-4-6", "claude-sonnet-4-6"})

_LONG_CONTEXT_SUFFIX = "[1m]"

#: The ``commandMode`` of a queued notification, and the kind of a delivery recorded so.
_TASK_NOTIFICATION = "task-notification"

_PEER = "peer"

#: What an image may be served as: the four types the Messages API accepts. A transcript is
#: not trusted to name a Content-Type, since ``image/svg+xml`` would run as a page.
_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})

#: The row holding a session's title, and how much of a transcript is read at a time looking
#: for the last one.
_TITLE_ROW = "ai-title"
_TITLE_NEEDLE = b'"ai-title"'
_TITLE_BLOCK_BYTES = 1 << 20


@dataclass(frozen=True)
class Usage:
    """Token usage of one assistant message, in Kyosei's names."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int


@dataclass(frozen=True)
class Reply:
    """One assistant message. Claude Code writes one record per content block.

    ``uuid`` is the record of its first text block, or the first record when the message
    is tool calls only, which is the address a note anchors to.
    ``record_uuids`` holds every record the message spans, and ``text`` the joined text
    blocks, thinking left out; ``usage`` is on every record of a message, and the last
    record's is kept.
    """

    uuid: str
    at: str
    message_id: str
    text: str
    usage: Usage | None
    model: str | None
    record_uuids: tuple[str, ...]
    #: The text of the message's thinking blocks, or None when no block records any.
    thinking: str | None = None
    #: How many of its thinking blocks record no text, only a signature.
    thinking_omitted: int = 0


@dataclass(frozen=True)
class ToolResult:
    """A tool call's result: the record it arrived in and its text."""

    uuid: str
    at: str
    text: str
    is_error: bool


@dataclass(frozen=True)
class ToolCall:
    """One tool call, addressed by the record that made it. ``result`` is None until it lands."""

    uuid: str
    at: str
    call_id: str
    name: str
    input: Mapping[str, object]
    result: ToolResult | None = None


@dataclass(frozen=True)
class SubagentRef:
    """A subagent the session started, with the tool call that started it."""

    agent_id: str
    tool_call_uuid: str
    description: str
    agent_type: str | None
    #: The prompt it was started with, which is where a dispatched worker's dispatch is named.
    prompt: str = ""


@dataclass(frozen=True)
class CommandOutput:
    """What a slash command printed, as the transcript records it (KYO-691).

    ``text`` is the output without its terminal styling. ``command`` and ``args`` come from
    the command row the output answers, and are None when that row is not in the transcript.
    A command that printed nothing has no row of output and yields no object.
    """

    uuid: str
    at: str
    command: str | None
    args: str | None
    text: str


@dataclass(frozen=True)
class TaskUsage:
    """What a task notification records a task as having used (KYO-695).

    A figure the row does not state is None, and a row stating none of them has no object.
    """

    total_tokens: int | None
    tool_uses: int | None
    duration_ms: int | None


@dataclass(frozen=True)
class Delivery:
    """A message delivered to the session by anything other than the person (KYO-692).

    ``kind`` is the transcript's ``origin.kind``, such as ``peer`` for a subagent's message
    or ``task-notification``, and is passed on when it is one this reader has not met.
    ``text`` is the message without the harness's wrapper. The other fields are None where
    the kind does not carry them.
    """

    uuid: str
    at: str
    kind: str
    text: str
    sender: str | None = None
    sender_id: str | None = None
    handback: bool = False
    task_id: str | None = None
    #: The id of the tool call the notification answers (the ``call_id`` of a :class:`ToolCall`).
    call_id: str | None = None
    status: str | None = None
    summary: str | None = None
    #: What the task used, on a notification whose row records it. A background command's
    #: notification records none.
    usage: TaskUsage | None = None


@dataclass(frozen=True)
class ReplyFacts:
    """What Claude Code recorded about the session on one reply (KYO-691).

    ``effort`` and ``git_branch`` are None on a row without them, as on older releases.
    ``requested_model`` is the model as the session asked for it, which alone shows ``[1m]``.
    """

    at: str
    message_id: str
    model: str | None
    requested_model: str | None
    effort: str | None
    git_branch: str | None
    usage: Usage


@dataclass
class TranscriptDelta:
    """What one :meth:`SessionTranscript.read` found or changed; a tool call reappears
    here when its result lands, and a reply when a later record of it lands."""

    turns: list[Turn] = field(default_factory=list)
    replies: list[Reply] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    subagents: list[SubagentRef] = field(default_factory=list)
    command_outputs: list[CommandOutput] = field(default_factory=list)
    deliveries: list[Delivery] = field(default_factory=list)


@dataclass
class _Message:
    uuid: str
    at: str
    message_id: str
    text_parts: list[str]
    usage: Usage | None
    model: str | None
    record_uuids: list[str]
    has_text: bool
    thinking_parts: list[str] = field(default_factory=list)
    thinking_omitted: int = 0

    def reply(self) -> Reply:
        return Reply(
            self.uuid,
            self.at,
            self.message_id,
            reply_text(self.text_parts),
            self.usage,
            self.model,
            tuple(self.record_uuids),
            "\n\n".join(self.thinking_parts) or None,
            self.thinking_omitted,
        )


#: What a reply's text blocks are joined with in :attr:`Reply.text` (KYO-709).
REPLY_BLOCK_SEPARATOR = "\n\n"


def is_text_block(block: object) -> bool:
    """A content block whose text a reply carries and a note may anchor to."""
    return (
        isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    )


def reply_text(parts: Sequence[str]) -> str:
    """A reply's text from its text blocks in order: the one joining of them (KYO-709)."""
    return REPLY_BLOCK_SEPARATOR.join(parts)


def reply_layout(parts: Sequence[str]) -> list[tuple[int, int]]:
    """Where each text block lies in :func:`reply_text` of ``parts``, as (start, end).

    A client that measures a span against the text the session API returns finds each
    block's records through this, which is why it sits beside the joining.
    """
    layout: list[tuple[int, int]] = []
    position = 0
    for part in parts:
        layout.append((position, position + len(part)))
        position += len(part) + len(REPLY_BLOCK_SEPARATOR)
    return layout


def reply_blocks(records: Iterable[dict], message_id: str) -> list[tuple[str, str]]:
    """The text blocks of one reply, each with its record's uuid, in the order the session
    reader takes them (:meth:`SessionTranscript._take_assistant`): the main thread's
    assistant records of that message, each record once."""
    blocks: list[tuple[str, str]] = []
    seen: set[str] = set()
    for record in records:
        message = record.get("message")
        uuid = record.get("uuid")
        if (
            record.get("type") != "assistant"
            or record.get("isSidechain")
            or not isinstance(uuid, str)
            or not isinstance(record.get("timestamp"), str)
            or not isinstance(message, dict)
            or message.get("id") != message_id
            or uuid in seen
        ):
            continue
        seen.add(uuid)
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if is_text_block(block):
                blocks.append((uuid, block["text"]))
    return blocks


def _usage_of(raw: object) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    names = (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    )
    values = [raw.get(name) for name in names]
    if not all(isinstance(value, int) for value in values):
        return None
    return Usage(*values)


def _text_field(record: dict, key: str) -> str | None:
    value = record.get(key)
    return value if isinstance(value, str) and value else None


def _command_row_text(record: dict) -> str | None:
    """The text of a row that may be a slash command or its output, or None."""
    if record.get("type") == "system":
        content = record.get("content") if record.get("subtype") == "local_command" else None
        return content if isinstance(content, str) else None
    if record.get("type") != "user" or record.get("toolUseResult") is not None:
        return None
    return _text_of((record.get("message") or {}).get("content"))


def _catalog_window(document: object, model: str) -> int | None:
    """``model``'s window in one cached catalogue file's document, or None."""
    catalog = document.get("catalog") if isinstance(document, dict) else None
    config = catalog.get("config") if isinstance(catalog, dict) else None
    models = config.get("models") if isinstance(config, dict) else None
    for row in models if isinstance(models, list) else []:
        if isinstance(row, dict) and row.get("id") == model:
            runtime = row.get("runtime")
            window = runtime.get("max_input_tokens") if isinstance(runtime, dict) else None
            if isinstance(window, int) and not isinstance(window, bool) and window > 0:
                return window
    return None


def _catalog_documents(config: Path) -> list[dict]:
    """The cached catalogue files, the command-line tool's first and then the newest first."""
    documents = []
    for path in sorted((config / _MODEL_CATALOG_DIR).glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(document, dict) and isinstance(document.get("catalog"), dict):
            documents.append(document)

    def rank(document: dict) -> tuple[bool, float]:
        fetched = document.get("fetchedAt")
        at = fetched if isinstance(fetched, int | float) else 0
        return (document["catalog"].get("surface") == _CATALOG_SURFACE, at)

    return sorted(documents, key=rank, reverse=True)


def context_window(model: str | None, requested_model: str | None, config: Path) -> int | None:
    """The context window of ``model`` in tokens, or None when it cannot be known (KYO-691).

    The transcript does not state a window. Claude Code keeps the catalogue it works from
    under its configuration directory, each model's window in ``runtime.max_input_tokens``,
    so a window is read from there. A model the catalogue does not name, a catalogue that
    is absent or changes shape, and a model whose window depends on an opt-in the session
    did not record all yield None: the caller then gives no percentage. Settings that
    change a window for one process, such as ``CLAUDE_CODE_DISABLE_1M_CONTEXT``, are not
    recorded anywhere this can read.
    """
    if model is None:
        return None
    if model in _WINDOW_NEEDS_SUFFIX and not (requested_model or "").endswith(_LONG_CONTEXT_SUFFIX):
        return None
    for document in _catalog_documents(config):
        window = _catalog_window(document, model)
        if window is not None:
            return window
    return None


def _result_text(content: object) -> str:
    """A tool result's text: a string, or its ``text`` blocks. Images are left out."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block["text"]
        for block in content
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    )


def _person_content(record: dict) -> object:
    """A record's content when it is a message the person sent, otherwise None."""
    if record.get("type") == "attachment":
        if not _is_queued_command(record):
            return None
        attachment = record["attachment"]
        if (
            not _is_human(attachment.get("origin"))
            or attachment.get("commandMode") != "prompt"
            or attachment.get("isMeta")
        ):
            return None
        return attachment.get("prompt")
    if record.get("type") != "user" or record.get("toolUseResult") is not None:
        return None
    if record.get("isMeta") or not _is_human(record.get("origin")):
        return None
    return (record.get("message") or {}).get("content")


def _image_blocks(content: object) -> list[dict]:
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "image"]


def _servable(block: dict) -> tuple[str, str] | None:
    """An image block's media type and base64 data, or None when it cannot be served."""
    source = block.get("source")
    if not isinstance(source, dict) or source.get("type") != "base64":
        return None
    media_type, data = source.get("media_type"), source.get("data")
    if not isinstance(media_type, str) or media_type not in _IMAGE_TYPES:
        return None
    return (media_type, data) if isinstance(data, str) else None


def _image_refs(blocks: list[dict]) -> tuple[ImageRef, ...]:
    refs: list[ImageRef] = []
    for index, block in enumerate(blocks):
        found = _servable(block)
        if found is not None:
            refs.append(ImageRef(index, found[0], len(found[1].rstrip("=")) * 3 // 4))
    return tuple(refs)


def _is_images_only(record: dict, blocks: list[dict]) -> bool:
    return isinstance(content := _person_content(record), list) and len(content) == len(blocks)


def _decoded(blocks: list[dict], index: int) -> SessionImage | None:
    """Block ``index``'s image, or None when there is none or its data is not base64."""
    found = _servable(blocks[index]) if 0 <= index < len(blocks) else None
    if found is None:
        return None
    try:
        return SessionImage(found[0], base64.b64decode(found[1], validate=True))
    except ValueError:  # binascii.Error is one
        return None


def _image_only_turn(record: dict, uuid: str, stamp: str) -> Turn:
    """The turn of a message that is images alone, which holds none of the person's words."""
    form = FORM_MID_TURN if record.get("type") == "attachment" else FORM_TYPED
    prompt_id = record.get("promptId")
    return Turn(
        uuid,
        stamp,
        form,
        SOURCE_TRANSCRIPT,
        (),
        True,
        "",
        prompt_id if isinstance(prompt_id, str) else None,
    )


def _element(text: str, name: str) -> str | None:
    """The contents of ``<name>`` in a notification, or None. Greedy: a result is free text."""
    found = re.search(rf"<{name}>(.*)</{name}>", text, re.S)
    return found.group(1).strip() if found else None


def _task_usage(raw: object) -> TaskUsage | None:
    """The usage object of a notification's row, or None when it states no figure."""
    if not isinstance(raw, dict):
        return None
    figures = [raw.get(name) for name in ("totalTokens", "toolUses", "durationMs")]
    known = [f if isinstance(f, int) and not isinstance(f, bool) else None for f in figures]
    return TaskUsage(*known) if any(f is not None for f in known) else None


def _delivery(record: dict, uuid: str, stamp: str) -> Delivery | None:
    """The message another party delivered in this record, or None."""
    usage = None
    if record.get("type") == "attachment":
        if not _is_queued_command(record):
            return None
        attachment = record["attachment"]
        origin, content = attachment.get("origin"), attachment.get("prompt")
        usage = _task_usage(attachment.get("usage"))
        kind = _origin_kind(origin)
        if kind is None and attachment.get("commandMode") == _TASK_NOTIFICATION:
            kind = _TASK_NOTIFICATION
    elif record.get("type") == "user" and record.get("toolUseResult") is None:
        origin, content = record.get("origin"), (record.get("message") or {}).get("content")
        kind = _origin_kind(origin)
    else:
        return None
    if kind is None or kind == "human":
        return None
    delivered = _result_text(content)
    if kind == _PEER and isinstance(origin, dict):
        return _peer_delivery(uuid, stamp, origin, delivered)
    if kind == _TASK_NOTIFICATION:
        summary = _element(delivered, "summary")
        return Delivery(
            uuid,
            stamp,
            kind,
            _element(delivered, "result") or summary or delivered,
            task_id=_element(delivered, "task-id"),
            call_id=_element(delivered, "tool-use-id"),
            status=_element(delivered, "status"),
            summary=summary,
            usage=usage,
        )
    return Delivery(uuid, stamp, kind, delivered)


def _peer_delivery(uuid: str, stamp: str, origin: dict, delivered: str) -> Delivery:
    """A subagent's message. A hand-back's ``body`` holds the harness's preamble, its report not."""
    handback = origin.get("handback") is True
    report, body = _text_field(origin, "handbackReportText"), _text_field(origin, "body")
    return Delivery(
        uuid,
        stamp,
        _PEER,
        (report if handback else None) or body or delivered,
        sender=_text_field(origin, "name"),
        sender_id=_text_field(origin, "from"),
        handback=handback,
    )


class SessionTranscript:
    """A session's transcript as Kyosei objects, read incrementally from a byte offset.

    :meth:`read` consumes whole lines past the offset and leaves a partial last line for
    the next call, so a file being written is never mis-parsed and no record is read
    twice. A file shorter than the offset has been rewritten, and is read again from the
    start. Sidechain records are left out of a session's own transcript: a subagent's
    transcript is its own file, which :class:`SubagentRef` points at and
    :func:`subagent_transcript` finds, and is read with ``sidechain=True``, since every
    record in it is one.
    """

    def __init__(self, path: Path, offset: int = 0, *, sidechain: bool = False) -> None:
        self.path = path
        self.offset = offset
        self.sidechain = sidechain
        self._reset()

    def _reset(self) -> None:
        self.has_origin = False
        self.why = "nothing read yet"
        self._turns: dict[str, Turn] = {}
        self._messages: dict[str, _Message] = {}
        self._calls: dict[str, ToolCall] = {}
        self._call_ids: dict[str, str] = {}
        self._subagents: dict[str, SubagentRef] = {}
        self._asked: set[str] = set()
        self._commands: dict[str, tuple[str, str]] = {}
        self._outputs: dict[str, CommandOutput] = {}
        self._deliveries: dict[str, Delivery] = {}
        self._latest_facts: ReplyFacts | None = None
        self._permission_mode: str | None = None

    @property
    def turns(self) -> list[Turn]:
        return sorted(self._turns.values(), key=lambda t: t.timestamp)

    @property
    def replies(self) -> list[Reply]:
        return [m.reply() for m in sorted(self._messages.values(), key=lambda m: m.at)]

    @property
    def tool_calls(self) -> list[ToolCall]:
        return sorted(self._calls.values(), key=lambda c: c.at)

    @property
    def subagents(self) -> list[SubagentRef]:
        return list(self._subagents.values())

    @property
    def command_outputs(self) -> list[CommandOutput]:
        return sorted(self._outputs.values(), key=lambda o: o.at)

    @property
    def deliveries(self) -> list[Delivery]:
        return sorted(self._deliveries.values(), key=lambda d: d.at)

    @property
    def latest_facts(self) -> ReplyFacts | None:
        """What the latest reply recorded; a reply Claude Code wrote itself does not count."""
        return self._latest_facts

    @property
    def latest_permission_mode(self) -> str | None:
        """The permission mode the transcript last recorded; None when it records none (KYO-698).

        A ``permission-mode`` row and a person's row both carry it. Such a row can follow the
        latest reply, so this is kept apart from what that reply recorded.
        """
        return self._permission_mode

    @property
    def readable(self) -> bool:
        """False when no record carries ``origin``: the format is not one this reader knows.

        A subagent's records carry none, so in its file the mark is ``isSidechain`` instead.
        """
        return self.has_origin

    def read(self) -> TranscriptDelta:
        """Read past the offset. Raises :class:`OSError` when the file cannot be opened."""
        delta = TranscriptDelta()
        if self.offset and self.path.stat().st_size < self.offset:
            self.offset = 0
            self._reset()  # the file was rewritten, so nothing held still stands
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            data = handle.read()
        end = data.rfind(b"\n") + 1
        if end == 0:
            return delta
        for raw in data[:end].split(b"\n"):
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(record, dict):
                self._take(record, delta)
        self.offset += end
        self.why = (
            f"{len(self._turns)} turns and {len(self._messages)} replies read"
            if self.has_origin
            else f"no record in the transcript carries `{self._format_mark}`, so its format is "
            "not one this reader knows"
        )
        return delta

    @property
    def _format_mark(self) -> str:
        return "isSidechain" if self.sidechain else "origin"

    def _take(self, record: dict, delta: TranscriptDelta) -> None:
        kind = record.get("type")
        if self._format_mark in record or (
            kind == "attachment"
            and _is_queued_command(record)
            and "origin" in (record.get("attachment") or {})
        ):
            self.has_origin = True
        uuid, stamp = record.get("uuid"), record.get("timestamp")
        left_out = record.get("isSidechain") and not self.sidechain
        if not left_out:
            # Before the check below: a ``permission-mode`` row has neither uuid nor timestamp.
            self._permission_mode = _text_field(record, "permissionMode") or self._permission_mode
        if left_out or not isinstance(uuid, str) or not isinstance(stamp, str):
            return
        if kind == "assistant":
            self._asked |= _questions_asked(record)
            self._take_assistant(record, uuid, stamp, delta)
            return
        self._take_command(record, uuid, stamp, delta)
        self._take_delivery(record, uuid, stamp, delta)
        turn = _person_turn(record, self._asked)
        blocks = _image_blocks(_person_content(record))
        images = _image_refs(blocks)
        if turn is None and images and _is_images_only(record, blocks):
            turn = _image_only_turn(record, uuid, stamp)
        if turn is not None:
            turn = replace(turn, images=images) if images else turn
            self._turns[turn.id] = turn
            delta.turns.append(turn)
        if kind == "user":
            self._take_results(record, uuid, stamp, delta)

    def _take_delivery(self, record: dict, uuid: str, stamp: str, delta: TranscriptDelta) -> None:
        found = _delivery(record, uuid, stamp)
        if found is not None:
            self._deliveries[uuid] = found
            delta.deliveries.append(found)

    def image(self, turn_id: str, index: int) -> SessionImage | None:
        """Image ``index`` of the person's turn ``turn_id``, or None when it holds no such image.

        The record is found by reading the file again, so a read of the session keeps no
        image in memory. Only a record that is the person's message is looked at, so this
        cannot return an image that reached the session in a tool's result. Raises
        :class:`OSError` when the file cannot be opened.
        """
        needle = turn_id.encode("utf-8", errors="replace")
        with self.path.open("rb") as handle:
            for raw in handle:
                if needle not in raw:
                    continue
                try:
                    record = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if (
                    isinstance(record, dict)
                    and record.get("uuid") == turn_id
                    and not record.get("isSidechain")
                ):
                    return _decoded(_image_blocks(_person_content(record)), index)
        return None

    def _take_assistant(self, record: dict, uuid: str, stamp: str, delta: TranscriptDelta) -> None:
        message = record.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("id"), str):
            return
        message_id = message["id"]
        content = message.get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        held = self._messages.get(message_id)
        if held is None:
            held = _Message(uuid, stamp, message_id, [], None, message.get("model"), [], False)
            self._messages[message_id] = held
        held.record_uuids.append(uuid)
        usage = _usage_of(message.get("usage"))
        if usage is not None:
            held.usage = usage
            if message.get("model") != _SYNTHETIC_MODEL:
                self._latest_facts = ReplyFacts(
                    stamp,
                    message_id,
                    _text_field(message, "model"),
                    _text_field(record, "requestedModel"),
                    _text_field(record, "effort"),
                    _text_field(record, "gitBranch"),
                    usage,
                )
        for block in blocks:
            if is_text_block(block):
                if not held.has_text:
                    held.uuid, held.at, held.has_text = uuid, stamp, True
                held.text_parts.append(block["text"])
            elif block.get("type") == "tool_use":
                self._take_tool_use(block, uuid, stamp, delta)
            elif block.get("type") in ("thinking", "redacted_thinking"):
                said = block.get("thinking")
                if isinstance(said, str) and said.strip():
                    held.thinking_parts.append(said)
                else:
                    held.thinking_omitted += 1
        delta.replies.append(held.reply())

    def _take_command(self, record: dict, uuid: str, stamp: str, delta: TranscriptDelta) -> None:
        text = _command_row_text(record)
        if text is None:
            return
        stripped = text.strip()
        if stripped.startswith(("<command-message>", "<command-name>")):
            name = _COMMAND_NAME.search(stripped)
            if name is not None:
                args = _COMMAND_ARGS.search(stripped)
                self._commands[uuid] = (name.group(1), args.group(1) if args else "")
            return
        output = _COMMAND_OUTPUT.fullmatch(stripped)
        if output is None or "origin" in record:  # with an origin, a person typed those tags
            return
        printed = _ANSI.sub("", output.group(2)).strip()
        if not printed:
            return
        command, args = self._commands.get(record.get("parentUuid"), (None, None))
        found = CommandOutput(uuid, stamp, command, args, printed)
        self._outputs[uuid] = found
        delta.command_outputs.append(found)

    def _take_tool_use(self, block: dict, uuid: str, stamp: str, delta: TranscriptDelta) -> None:
        call_id, name = block.get("id"), block.get("name")
        tool_input = block.get("input")
        if not isinstance(call_id, str) or not isinstance(name, str):
            return
        given = tool_input if isinstance(tool_input, dict) else {}
        call = ToolCall(uuid, stamp, call_id, name, given)
        self._calls[uuid] = call
        self._call_ids[call_id] = uuid
        delta.tool_calls.append(call)

    def _take_results(self, record: dict, uuid: str, stamp: str, delta: TranscriptDelta) -> None:
        content = (record.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            call_uuid = self._call_ids.get(block.get("tool_use_id"))
            call = self._calls.get(call_uuid) if call_uuid else None
            if call is None:
                continue
            is_error = bool(block.get("is_error"))
            result = ToolResult(uuid, stamp, _result_text(block.get("content")), is_error)
            call = replace(call, result=result)
            self._calls[call.uuid] = call
            delta.tool_calls.append(call)
            self._take_subagent(call, record.get("toolUseResult"), delta)

    def _take_subagent(self, call: ToolCall, result: object, delta: TranscriptDelta) -> None:
        if call.name != AGENT_TOOL or not isinstance(result, dict):
            return
        agent_id = result.get("agentId")
        if not isinstance(agent_id, str):
            return
        description = call.input.get("description")
        agent_type = call.input.get("subagent_type")
        prompt = call.input.get("prompt")
        ref = SubagentRef(
            agent_id,
            call.uuid,
            description if isinstance(description, str) else "",
            agent_type if isinstance(agent_type, str) else None,
            prompt if isinstance(prompt, str) else "",
        )
        self._subagents[agent_id] = ref
        delta.subagents.append(ref)


def subagent_transcript(session_path: Path, agent_id: str) -> Path | None:
    """The transcript of subagent ``agent_id`` of the session at ``session_path``, or None.

    It is ``<session>/subagents/agent-<id>.jsonl`` beside the session's own file. The id is
    compared with the names that directory holds and is never built into a path, so an id
    from a request cannot leave the directory, and an agent of another session is not there.
    """
    wanted = f"agent-{agent_id}.jsonl"
    directory = session_path.parent / session_path.stem / "subagents"
    try:
        names = [entry.name for entry in directory.iterdir() if entry.is_file()]
    except OSError:
        return None
    return directory / wanted if wanted in names else None


# ------------------------------------------------------------- lines a session changed (KYO-701)

#: Only a row holding this is parsed, since most rows of a long transcript are not edits.
_PATCH_NEEDLE = b'"structuredPatch"'


@dataclass(frozen=True)
class LinesChanged:
    """Lines added and removed, as the patches of a session's Edit and Write results state."""

    added: int = 0
    removed: int = 0


def patch_lines(result: object) -> LinesChanged:
    """The lines one tool result changed, by Claude Code's own rule for its running total.

    A hunk's lines beginning ``+`` and ``-`` are counted. A Write that creates a file records
    no hunks, and Claude Code counts its newlines plus one, so content ending in a newline
    counts one more than the file has lines; this is kept so that the total is the figure
    Claude Code states. Measured on 2.1.319 against the ``lastLinesAdded`` it saves.
    """
    if not isinstance(result, dict):
        return LinesChanged()
    hunks = result.get("structuredPatch")
    if not isinstance(hunks, list):
        return LinesChanged()
    added = removed = 0
    for hunk in hunks:
        lines = hunk.get("lines") if isinstance(hunk, dict) else None
        for line in lines if isinstance(lines, list) else []:
            if isinstance(line, str):
                added += line.startswith("+")
                removed += line.startswith("-")
    content = result.get("content")
    if not hunks and result.get("type") == "create" and isinstance(content, str) and content:
        added = content.count("\n") + 1
    return LinesChanged(added, removed)


@dataclass
class _FileLines:
    offset: int = 0
    added: int = 0
    removed: int = 0


class SessionLines:
    """The running total of lines changed by a session and all of its subagents.

    Each file is followed from where it was last read, and one that has not grown is not
    opened, so a sample costs a directory listing however many subagents ran. A file that
    has shrunk was rewritten and is counted again. A file that cannot be read keeps the
    count already held for it, so a total never falls because of a transient error.
    """

    def __init__(self, session_path: Path) -> None:
        self.session_path = session_path
        self.directory = session_path.parent / session_path.stem / "subagents"
        self._files: dict[str, _FileLines] = {}

    def read(self) -> LinesChanged:
        """Read what has been appended, and return the total."""
        self._follow(str(self.session_path), self._size(self.session_path))
        try:
            with os.scandir(self.directory) as entries:
                found = [e for e in entries if e.name.endswith(".jsonl") and e.is_file()]
                for entry in found:
                    self._follow(entry.path, entry.stat().st_size)
        except OSError:
            pass  # no subagent has run, or the listing failed; what is held still stands
        return LinesChanged(
            sum(held.added for held in self._files.values()),
            sum(held.removed for held in self._files.values()),
        )

    @staticmethod
    def _size(path: Path) -> int | None:
        try:
            return path.stat().st_size
        except OSError:
            return None

    def _follow(self, path: str, size: int | None) -> None:
        held = self._files.setdefault(path, _FileLines())
        if size is None or size == held.offset:
            return
        if size < held.offset:
            held.offset, held.added, held.removed = 0, 0, 0
        try:
            with open(path, "rb") as handle:
                handle.seek(held.offset)
                for raw in handle:
                    if not raw.endswith(b"\n"):
                        break  # a line still being written is left for the next read
                    held.offset += len(raw)
                    if _PATCH_NEEDLE in raw:
                        self._count(raw, held)
        except OSError:
            return

    @staticmethod
    def _count(raw: bytes, held: _FileLines) -> None:
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return
        if isinstance(record, dict):
            lines = patch_lines(record.get("toolUseResult"))
            held.added += lines.added
            held.removed += lines.removed


def session_title(path: Path) -> str | None:
    """The title the transcript at ``path`` holds, or None when it holds none.

    Claude Code writes the title as an ``ai-title`` row, again and again through a session
    and with the latest one standing, so the file is read backwards from its end, which
    finds it in the last block however long the file is. A session list asks this of every
    row. Raises :class:`OSError` when the file cannot be opened.
    """
    with path.open("rb") as handle:
        position = handle.seek(0, os.SEEK_END)
        carried = b""  # the part of a line that began in the block before this one
        while position > 0:
            step = min(_TITLE_BLOCK_BYTES, position)
            position -= step
            handle.seek(position)
            lines = (handle.read(step) + carried).split(b"\n")
            carried = lines.pop(0) if position > 0 else b""
            for raw in reversed(lines):
                title = _title_of(raw)
                if title is not None:
                    return title
    return None


def _title_of(raw: bytes) -> str | None:
    if _TITLE_NEEDLE not in raw:
        return None
    try:
        record = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("type") != _TITLE_ROW:
        return None
    title = record.get("aiTitle")
    return title if isinstance(title, str) and title.strip() else None


@dataclass(frozen=True)
class ClearLink:
    """The transcript a session continued in after ``/clear``."""

    session_id: str
    path: Path


def clear_successor(path: Path) -> ClearLink | None:
    """The transcript that continued ``path``'s session after ``/clear``, or None.

    ``/clear`` starts a new transcript under a new session id in the same project
    directory. The new file's head holds the ``/clear`` command record and attachment
    records that still name the old session in ``session_id``; both are required, so a
    file that merely mentions the old id is not taken for its continuation. The attachment
    records are written with the first prompt after ``/clear``, so a session just cleared
    has no link yet; while its process is alive, the session registry (the pane adapter)
    gives the new id at once.
    """
    old = path.stem
    siblings = sorted(p for p in path.parent.glob("*.jsonl") if p != path)
    for candidate in siblings:
        head = _head_records(candidate)
        names_old = any(
            record.get("session_id") == old and record.get("sessionId") != old for record in head
        )
        if names_old and any(_is_clear_command(record) for record in head):
            return ClearLink(candidate.stem, candidate)
    return None


def _head_records(path: Path) -> list[dict]:
    with path.open("rb") as handle:
        data = handle.read(_CLEAR_LINK_WINDOW_BYTES)
    records = []
    for raw in data[: data.rfind(b"\n") + 1].split(b"\n"):
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _is_clear_command(record: dict) -> bool:
    content = (record.get("message") or {}).get("content")
    return isinstance(content, str) and _CLEAR_COMMAND in content
