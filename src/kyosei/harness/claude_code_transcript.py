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
matches the turn's text around them, and then only the typed segments count. A turn
with no pairable history line (for example one typed through claude.ai, which the
prompt history does not record) can still match, as ``matched-paste-unknown``. One
case is not covered and cannot be from these files: whether Claude Code inserts a short
paste as plain text with no placeholder. If it does, such a paste is indistinguishable
from typing.

THE SECOND SOURCE. When the transcript is absent or unreadable, the prompt history is
searched for the session's typed prompts (``display`` with placeholders removed), and a
match there is ``matched`` with the history named as its source. When the transcript is
present and readable it alone decides (KYO-640: a mismatch against a present transcript
is refused), and the history serves only to find paste boundaries.

WHAT LEAVES THIS MODULE FOR THE STORE (KYO-643.c4). :meth:`CheckResult.store_fields` is
the matched record's id, its own timestamp, the outcome and the form of the matched words
(KYO-644), and nothing else: not the turn's text, not its neighbours, not the transcript
path, not a hash.

A DISCUSSION SPAN (KYO-640.n1, KYO-644). A decided credit may cite the discussion that led
to it by its first and last record. :func:`measure_span` reads the records between them and
returns counts and times only: the person's turns, the agent's turns, the active seconds
(gaps longer than :data:`SPAN_BREAK_SECONDS` are left out, because a long gap is time away)
and the first and last timestamps. No text from the span leaves the module.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
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


def candidate_transcripts(
    session_id: str, *, config: Path, recorded: Path | None = None
) -> list[Path]:
    """The transcript files for ``session_id``, the recorded one first.

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
    return found


# ---------------------------------------------------------------- turns


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
            uuid = record.get("uuid")
            stamp = record.get("timestamp")
            if kind == "assistant":
                for block in (record.get("message") or {}).get("content") or []:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "AskUserQuestion"
                        and isinstance(block.get("id"), str)
                    ):
                        asked.add(block["id"])
                continue
            if not isinstance(uuid, str) or not isinstance(stamp, str):
                continue
            if kind == "attachment":
                attachment = record.get("attachment") or {}
                if not isinstance(attachment, dict) or attachment.get("type") != "queued_command":
                    continue
                if "origin" in attachment:
                    has_origin = True
                if (
                    not _is_human(attachment.get("origin"))
                    or attachment.get("commandMode") != "prompt"
                    or attachment.get("isMeta")
                ):
                    continue
                text = _text_of(attachment.get("prompt"))
                words = _typed_words(text) if text is not None else None
                if words is not None:
                    turns[uuid] = Turn(
                        uuid, stamp, FORM_MID_TURN, SOURCE_TRANSCRIPT, (words,), False, words
                    )
                continue
            if kind != "user":
                continue
            content = (record.get("message") or {}).get("content")
            result = record.get("toolUseResult")
            if result is not None:
                answers = _answers(record, content, result, asked)
                if answers:
                    joined = "\n".join(answers)
                    turns[uuid] = Turn(
                        uuid, stamp, FORM_ANSWER, SOURCE_TRANSCRIPT, answers, True, joined
                    )
                continue
            if record.get("isMeta") or not _is_human(record.get("origin")):
                continue
            text = _text_of(content)
            words = _typed_words(text) if text is not None else None
            if words is not None:
                form = (
                    FORM_ACCEPTED_SUGGESTION
                    if record.get("promptSource") == SUGGESTION_PROMPT_SOURCE
                    else FORM_TYPED
                )
                turns[uuid] = Turn(uuid, stamp, form, SOURCE_TRANSCRIPT, (words,), False, words)
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
            )
        )
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
) -> CheckResult:
    """Whether ``quote`` is the person's own words in session ``session_id``.

    ``turn`` names one of the person's turns (a record ``uuid`` or a ``history:<ms>``
    id) when the quote occurs in more than one. ``recorded`` is the transcript path the
    hooks recorded (:func:`kyosei.cmd_sessions.recorded_transcript_path`). ``config``
    overrides :func:`config_dir`.

    Raises :class:`ValueError` for a session id that is not a UUID or a quote with no
    words; every other condition is a named outcome.
    """
    pieces = quote_pieces(quote)
    if not pieces:
        raise ValueError("the quote has no words to look for")
    base = config if config is not None else config_dir(environ)
    paths = candidate_transcripts(session_id, config=base, recorded=recorded)
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
                turns = pair_with_history(read.turns, history)
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
) -> TranscriptRead:
    """The person's turns in a session's transcript, with paste boundaries applied.

    The one function another harness would provide its own version of (KYO-638 5.6).
    An absent transcript gives no turns and ``readable`` False.
    """
    base = config if config is not None else config_dir(environ)
    paths = candidate_transcripts(session_id, config=base, recorded=recorded)
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
    paths = candidate_transcripts(session_id, config=base, recorded=recorded)
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
