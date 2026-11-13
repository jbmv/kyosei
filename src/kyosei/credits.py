"""Credits: who a person's judgement in the store belongs to (KYO-644).

KYO-642 built the tables and :meth:`kyosei.store.Store.add_credit`, the one writer. This
module is what every command that writes a credit goes through, so the rules are stated
once (law 3): which person, which words, where they were said, whether the transcript
holds them, and, for a decision, what was recommended and what else was weighed. It is
section 4 of the KYO-638 proposal as amended by decision KYO-640 (P3, P5, P8, P9) and by
the maintainer's later words recorded as KYO-644.c8 and c9.

THE ENFORCEMENT POINTS (KYO-640, P5). Each refuses, stores nothing, and names its remedy:

* ``kyo verify --method human-observed`` (:func:`kyosei.cmd_evidence._verify_one`):
  ``human-observed-requires-credit``. The verdict's ``--evidence-file`` is the person's
  words; an agent's own observation is ``--method agent-observed``.
* ``kyo confirm`` writes ONE credit whose subjects are every verification it records
  and the gate it clears (:mod:`kyosei.cmd_confirm`).
* ``kyo ungate`` of a gate in :data:`HUMAN_GATE_TYPES`, and a carried settle that would
  clear one: ``person-gate-clear-requires-credit``, cleared by a credit (the gate's
  ``cleared_as`` becomes ``answered``) or by ``--withdrawn`` with a reason (``withdrawn``:
  no person answered, the wait stopped mattering).
* ``kyo new`` and ``kyo spawn`` of a kind whose policy sets ``requires_credit`` (the
  shipped ``decision``): ``filing-requires-credit``, cleared by a ``decided`` credit.
* ``kyo credit`` (:func:`cmd_credit`), for a credit written after its subject:
  ``reconstructed-credit-requires-basis``, ``credit-basis-not-approval`` and
  ``credit-subject-already-credited``.

WITHDRAWAL (KYO-670). ``kyo credit --withdraw <credit-id> --why <reason>`` retracts a
current credit that should not exist, for example one quoting a process correction as an
override. Nothing replaces it: the row and its words stay readable, ``withdrawn_at``,
``withdrawn_by`` and ``withdrawn_why`` say when, by whom and why, and it counts nowhere a
current credit counts, so the rows it named are uncredited again.

WHERE THE ENFORCEMENT APPLIES (KYO-654 DC12, KYO-656). Only in a project whose ``people:``
declares someone (:func:`enforced`). Until then the points above write their row with no
credit, so an upgrade of kyo refuses nothing before the project's operator has chosen, and
``kyo doctor`` carries one notice saying so. A credit flag given in such a project still
asks for a credit, and is refused for naming nobody declared.

Shared by all of them: ``credit-person-undeclared``, ``credit-quote-not-found`` (the
transcript is present and none of the person's turns holds the words; KYO-640 P6 allows
no override) and ``credit-quote-ambiguous``. Passing both ``--session`` and
``--source-other`` is a usage error.

THE PERSON (P3). ``--person`` defaults to the only person ``people:`` declares, and every
write echoes who was credited. With none or several declared and no ``--person``, the
credit is incomplete and refused.

THE WORDS, BY TURN REFERENCE (KYO-900, decision KYO-898). In a project that reads
transcripts a credit names the turn or turns the person spoke in and kyo copies the words:
nobody retypes them. ``--turn <id>``, once per turn, takes a prompt id or a transcript
record uuid, whole or by a unique prefix (:func:`cite_turns`). With neither ``--turn`` nor
``--quote-file`` the credit takes the person's latest turn of the session, after the
session's transcript is folded into the turn index, so that the prompt being answered is
the one taken. The turns are stored in transcript order whatever order they were named in,
each in ``credit_turns`` with its session, record uuid, time, stored words and the index
line's commitment where it has one. One of them is the APPROVAL, the turn that carries the
person's judgement, and it is the credit's ``source_turn`` and ``quote_id``; the others
are CONTEXT, earlier turns in which what was approved was specified. The approval is the
latest turn cited, or, when ``--quote-file`` is given beside ``--turn``, the latest cited
turn that holds those words, which are then what the credit stores for it. A turn that is
not the person's is refused (``credit-turn-not-the-persons``), as is one whose words the
transcript does not hold (``credit-turn-words-unread``).

WHAT IS STORED OF A TURN (:func:`turn_words`). The person's typed words: pasted content is
left out and its place marked ``[...]``, as a quote marks an omission, and a turn longer
than :data:`TURN_WORDS_HEAD` plus :data:`TURN_WORDS_TAIL` characters keeps its head and
tail around the same mark. The stored text therefore still checks against the transcript
as a quote does. ``--quote-file`` narrows the approval further.

WHERE THE WORDS WERE SAID. ``--session`` defaults to ``$CLAUDE_CODE_SESSION_ID``, which
Claude Code sets in its shell. Given alone, ``--quote-file`` is checked against that
session's transcript by :mod:`kyosei.harness.claude_code_transcript`, and only
``CheckResult.store_fields()`` is stored. ``--source-other`` is one line saying where
words said outside Claude Code were said, recorded ``not-applicable``. So is every credit
in a project that has not opted in to reading transcripts
(:func:`kyosei.config.transcripts_opted_in`, KYO-656): the session is stored as given and
no transcript is opened, for the quote, the refining turn or a discussion span.

ROLES. ``observed``: the person looked at something (a verdict, a gate). ``decided``: the
person decided a decision record, with ``--decision-file`` saying what was recommended,
by whom, the outcome (accepted, refined, overrode, unprompted) and the alternatives,
each with who proposed it where the file says (``proposed_by``, KYO-786); a
``refined`` outcome cites the refining turn beside the accepting one. ``authorized``
(KYO-644.c8): a go-ahead approving a plan already stated ("sounds good", "build it now"),
credited as authorizing the work to proceed and never as deciding its content, so it
takes no decision file; a decision record still needs ``decided``.

A DISCUSSION SPAN (KYO-640.n1). A credit may cite the discussion that led to it:
``--span-first`` and ``--span-last`` (record uuids) in ``--span-session`` (by default the
credit's session). kyo measures it when the credit is written
(:func:`kyosei.harness.claude_code_transcript.measure_span`) and stores counts and times,
never text: the person's turns; the agent's REPLIES to the person, one per agent turn
(the run of the agent's records between two of the person's turns) that ends in text
before the person's next turn, so a reply split across records between tool calls counts
once (KYO-644.c9); active seconds, leaving out every gap longer than
``SPAN_BREAK_SECONDS`` (fifteen minutes); and the span's first and last timestamps. With
no transcript, or in a project that has not opted in, the span's ids are stored and its
measures left empty.

A SPAN FROM A DISCUSSION (KYO-658). A decided credit filed from a discussion
(:mod:`kyosei.cmd_discuss`) takes its span from the discussion's turn references
(:func:`discussion_span`) when no ``--span-first`` and ``--span-last`` are given: from the
turn it was opened at to the turn holding the credited words, in the opening's session, and
it is measured as an explicit span is. A discussion whose opening the index did not stamp
gives no span.
"""

from __future__ import annotations

import json
import shlex
from argparse import ArgumentParser, Namespace
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import commands, export, ids, seal
from .cmd_sessions import recorded_transcript_path
from .cmd_turns import (
    HUMAN,
    confirm_from_transcript,
    confirmed_paste_flags,
    read_lines,
    session_lines,
)
from .commands import Context, Output, _add_evidence, _touch_record
from .config import READ_TRANSCRIPTS_KEY, declared_people, person_label, transcripts_opted_in
from .contracts import CREDIT_OUTCOME_REFINED, CREDIT_OUTCOMES, CREDIT_ROLES
from .errors import ContractViolation, NotFound, UsageError
from .harness import claude_code_transcript as transcript
from .prose import read_prose, single_line

#: The gates that wait on a person to look. Clearing one needs a credit or ``--withdrawn``.
HUMAN_GATE_TYPES: frozenset[str] = frozenset({"awaiting:human-review", "awaiting:human-run"})

#: The variable Claude Code sets in its shell to the session's id.
ENV_SESSION = "CLAUDE_CODE_SESSION_ID"

ROLE_OBSERVED = "observed"
ROLE_DECIDED = "decided"
ROLE_AUTHORIZED = "authorized"

SOURCE_CLAUDE_CODE = "claude-code"
SOURCE_OTHER = "other"
CHECK_NOT_APPLICABLE = "not-applicable"

#: Why nothing was read in a project that has not opted in (KYO-656).
NOT_OPTED_IN_WHY = (
    "this project has not opted in to reading transcripts "
    f"(`{READ_TRANSCRIPTS_KEY}:` in kyosei/config.yml), so no transcript was read"
)

#: ``gates.cleared_as`` values (``contracts.GATE_CLEARED_AS``).
CLEARED_ANSWERED = "answered"
CLEARED_WITHDRAWN = "withdrawn"

CONTRACT_HUMAN_OBSERVED_REQUIRES_CREDIT = "human-observed-requires-credit"
CONTRACT_PERSON_GATE_CLEAR_REQUIRES_CREDIT = "person-gate-clear-requires-credit"
CONTRACT_FILING_REQUIRES_CREDIT = "filing-requires-credit"
CONTRACT_CREDIT_PERSON_UNDECLARED = "credit-person-undeclared"
CONTRACT_CREDIT_QUOTE_NOT_FOUND = "credit-quote-not-found"
CONTRACT_CREDIT_QUOTE_AMBIGUOUS = "credit-quote-ambiguous"
CONTRACT_RECONSTRUCTED_CREDIT_REQUIRES_BASIS = "reconstructed-credit-requires-basis"
CONTRACT_CREDIT_BASIS_NOT_APPROVAL = "credit-basis-not-approval"
CONTRACT_CREDIT_SUBJECT_ALREADY_CREDITED = "credit-subject-already-credited"
CONTRACT_CREDIT_BATCH_INVALID = "credit-batch-invalid"
CONTRACT_CREDIT_TRANSCRIPT_STILL_ABSENT = "credit-transcript-still-absent"
CONTRACT_CREDIT_TRANSCRIPTS_NOT_OPTED_IN = "credit-transcripts-not-opted-in"

#: KYO-900: a ``--turn`` that names a turn of the index that is not the person's, and a
#: turn of the person's whose words the transcript does not hold.
CONTRACT_CREDIT_TURN_NOT_THE_PERSONS = "credit-turn-not-the-persons"
CONTRACT_CREDIT_TURN_WORDS_UNREAD = "credit-turn-words-unread"

#: ``credit_turns.role``: the turn that carries the person's judgement, and an earlier turn
#: in which what was approved was specified.
TURN_ROLE_APPROVAL = "approval"
TURN_ROLE_CONTEXT = "context"

#: The shortest ``--turn`` prefix accepted, so a one-character typo matches nothing.
MIN_TURN_PREFIX = 4
#: How many of the session's turns a refusal over ``--turn`` lists.
LISTED_TURNS = 5
#: How much of a long turn is stored: its first and last characters, around an omission
#: mark. A credit keeps the words so that it outlives its transcript, and a turn can be
#: many pages; what is left out is still covered by the index line's commitment.
TURN_WORDS_HEAD = 3000
TURN_WORDS_TAIL = 1000
#: How an omission is marked in stored words, the mark a quote file uses.
OMISSION = "[...]"
#: A sole cited turn of at most this many words gets one line in the receipt saying that
#: earlier turns can be cited as context. It refuses nothing and asks for no reply.
SHORT_TURN_WORDS = 4

#: The keys a ``--decision-file`` may carry.
DECISION_FILE_KEYS = frozenset(
    {
        "recommended",
        "recommended_by",
        "outcome",
        "alternatives",
        "refining_turn",
        "refining_session",
    }
)

#: The keys one entry of a decision file's ``alternatives`` may carry.
ALTERNATIVE_KEYS = frozenset({"text", "chosen", "proposed_by"})

#: KYO-786: who may be named as the proposer of an alternative besides a person. An
#: alternative that names nobody is read as :data:`PROPOSER_DEFAULT`.
AGENT_PROPOSERS: tuple[str, ...] = ("orchestrator", "worker")
PROPOSER_DEFAULT = "orchestrator"
#: The earlier name of the orchestrator, accepted and stored as the current one.
AGENT_PROPOSER_ALIASES = {"controller": "orchestrator"}
#: ``proposed_by: person`` names the person the credit itself names.
PROPOSER_PERSON = "person"


# ------------------------------------------------------------------------ the flags


def add_credit_flags(
    parser: ArgumentParser, *, quote: bool = True, decision: bool = False, span: bool = False
) -> None:
    """The flags a credit is given by, on every command that writes one."""
    parser.add_argument(
        "--person",
        metavar="KEY",
        help="the person whose judgement this is, a key under `people:` in "
        "kyosei/config.yml; defaults to the only one declared (KYO-644)",
    )
    if quote:
        parser.add_argument(
            "--quote-file",
            metavar="FILE",
            help="the person's words, verbatim, from a file or '-' for stdin. Needed where "
            "kyo cannot copy them from a turn: a project that does not read transcripts, "
            "or words said outside Claude Code. Beside --turn it narrows what is stored to "
            "part of a named turn",
        )
    parser.add_argument(
        "--session",
        metavar="ID",
        help=f"the Claude Code session the words were said in; defaults to ${ENV_SESSION}. "
        "The words are checked against that session's transcript",
    )
    parser.add_argument(
        "--turn",
        action="append",
        metavar="ID",
        help="a turn of the person's that the credit cites, repeatable: a prompt id or a "
        "transcript record uuid from the turn index (`kyo turns --said` lists them), whole "
        "or by a unique prefix. kyo copies the words from the transcript, so no --quote-file is "
        "needed, and stores the turns in transcript order: the latest is the approval and "
        "the earlier ones the context it approved. With neither --turn nor --quote-file, "
        "in a Claude Code session of a project that reads transcripts, the person's latest "
        "turn is taken. With --quote-file, the words must occur in a named turn (KYO-900)",
    )
    parser.add_argument(
        "--source-other",
        # Not PROSE_ARGUMENT_METAVAR: one line naming a place, single-line enforced, like
        # --why without its file twin; the words themselves always come from a file.
        metavar="WHERE",
        help="one line saying where the words were said, when not in a Claude Code session",
    )
    if decision:
        parser.add_argument(
            "--decision-file",
            metavar="FILE",
            help="YAML: recommended, recommended_by, outcome (accepted | refined | overrode "
            "| unprompted), alternatives [{text, chosen, proposed_by}] where proposed_by is "
            "person, orchestrator or worker and an entry without one is read as the "
            "agent's, and refining_turn for a refined outcome",
        )
        parser.add_argument(
            "--outcome",
            choices=CREDIT_OUTCOMES,
            help="in place of --decision-file, for a decision that lists no alternatives: "
            "accepted, refined, overrode or unprompted (KYO-900)",
        )
        parser.add_argument(
            "--recommended",
            # One line stored beside the outcome, like --source-other: not prose with a
            # file twin. A decision with alternatives to list takes --decision-file.
            metavar="WHAT",
            help="with --outcome: what was recommended, one line; none for unprompted",
        )
        parser.add_argument(
            "--recommended-by",
            metavar="WHO",
            help="with --recommended: who recommended it; defaults to the acting author",
        )
    if span:
        parser.add_argument(
            "--span-first",
            metavar="UUID",
            help="the first transcript record of the discussion the credit cites",
        )
        parser.add_argument(
            "--span-last",
            metavar="UUID",
            help="the last transcript record of the discussion the credit cites",
        )
        parser.add_argument(
            "--span-session",
            metavar="ID",
            help="the session the discussion was in; defaults to the credit's session",
        )


def requested(args: Namespace, *, words_flag: bool = True) -> bool:
    """Whether this invocation gave any flag that only a credit reads.

    ``words_flag`` False leaves ``--quote-file`` out, for a command that reads the person's
    words from it whether or not a credit is written (`kyo confirm`)."""
    return any(
        getattr(args, name, None)
        for name in ("person", "session", "turn", "source_other", *DECISION_FLAGS)
    ) or bool(words_flag and getattr(args, "quote_file", None))


#: The flags that say what a decided credit decided: the YAML file, or its three scalars.
DECISION_FLAGS = ("decision_file", "outcome", "recommended", "recommended_by")


def _decision_text(args: Namespace) -> str | None:
    """KYO-900: ``--outcome``, ``--recommended`` and ``--recommended-by`` as the YAML a
    ``--decision-file`` would hold, or None when none was given."""
    given = {
        name: getattr(args, name, None)
        for name in ("outcome", "recommended", "recommended_by")
        if getattr(args, name, None)
    }
    if not given:
        return None
    if getattr(args, "decision_file", None):
        raise UsageError(
            "--decision-file holds the outcome and what was recommended, and so do "
            f"{', '.join('--' + name.replace('_', '-') for name in given)}; give the file "
            "or the flags"
        )
    if "outcome" not in given:
        raise UsageError(
            "--recommended and --recommended-by go with --outcome (accepted, refined, "
            "overrode or unprompted)"
        )
    return yaml.safe_dump(given)


# ------------------------------------------------------------------- the resolution


# ------------------------------------------------------------- the turns (KYO-900)


@dataclass
class CitedTurn:
    """One turn of the person's that a credit cites, read from the transcript and joined
    to its line of the turn index where it has one."""

    session: str
    turn: str
    prompt_id: str | None
    at: str
    form: str
    #: The words as stored (:func:`turn_words`), and how long the turn's typed words are.
    words: str
    total_chars: int
    paste_known: bool
    #: The index line's commitment to the prompt's text (KYO-663), where it has one.
    root: str | None = None
    lines: int | None = None
    role: str = TURN_ROLE_CONTEXT
    segments: tuple[str, ...] = field(default=(), repr=False)

    @property
    def words_form(self) -> str | None:
        return transcript.WORDS_FORMS.get(self.form)

    def label(self) -> str:
        return f"prompt {self.prompt_id}" if self.prompt_id else f"turn {self.turn}"


@dataclass
class Cited:
    """The turns one credit cites, resolved: which is the approval, and the words the
    credit stores as its quote."""

    turns: list[CitedTurn]
    approval: CitedTurn
    quote_text: str
    defaulted: bool = False

    def check(self) -> dict:
        return {
            "transcript_check": transcript.MATCHED
            if self.approval.paste_known
            else transcript.MATCHED_PASTE_UNKNOWN,
            "source_turn": self.approval.turn,
            "observed_at": self.approval.at,
            "words_form": self.approval.words_form,
        }

    def why(self) -> str:
        approval = self.approval
        how = (
            f"the person's latest turn of session {approval.session} was taken, since no "
            "--turn was given"
            if self.defaulted
            else f"the words were copied from the transcript at {len(self.turns)} turn(s)"
        )
        if not approval.paste_known:
            how += (
                "; no prompt-history line pairs with the approving turn, so pasted "
                "content in it cannot be ruled out"
            )
        return how


@dataclass
class _SessionView:
    """One session as a credit reads it: the person's turns in its transcript, and its
    lines of the turn index that stand for a prompt or a mid-turn message."""

    session: str
    turns: list
    lines: list[dict]
    readable: bool
    why: str


def turn_refs_of(args: Namespace) -> list[str]:
    """``--turn`` as a list. The flag is repeatable; a caller that builds its own
    Namespace (a batch line, a composed step) may still hand one string."""
    given = getattr(args, "turn", None)
    if not given:
        return []
    return [given] if isinstance(given, str) else [ref for ref in given if ref]


def turn_words(turn) -> tuple[str, int]:
    """What is stored of one turn of the person's: ``(words, length of the typed words)``.

    The turn's segments are the person's typing with pastes taken out, so they are joined
    around :data:`OMISSION`; the answers to a question are joined by a newline. A text
    longer than the head and tail kept is cut to them around the same mark."""
    parts = [segment.strip() for segment in turn.segments if segment.strip()]
    joiner = "\n" if turn.form == transcript.FORM_ANSWER else f"\n{OMISSION}\n"
    text = joiner.join(parts)
    total = len(text)
    if total > TURN_WORDS_HEAD + TURN_WORDS_TAIL:
        head, tail = text[:TURN_WORDS_HEAD].rstrip(), text[-TURN_WORDS_TAIL:].lstrip()
        text = f"{head}\n{OMISSION}\n{tail}"
    return text, total


def _fold_index(ctx: Context, session: str) -> None:
    """Fold the session's transcript into the turn index, as the Stop hook would. The index
    learns a prompt's transcript record, and whether it was the person's, at the Stop that
    ends its turn, and a credit is written before that Stop. Best effort; a worktree's kyo
    never writes the main checkout's index."""
    if ctx.layout is not None and ctx.layout.store_via_main_checkout is not None:
        return
    try:
        paths = transcript.candidate_transcripts(
            session,
            config=transcript.config_dir(ctx.environ),
            recorded=recorded_transcript_path(ctx.root, session),
        )
        if paths:
            confirm_from_transcript(ctx.root, session, Path(paths[0]))
    except (OSError, ValueError):
        pass


def _prompt_lines(lines: list[dict]) -> list[dict]:
    return [line for line in lines if line.get("role") != seal.ROLE_ASSISTANT]


def _session_view(ctx: Context, session: str) -> _SessionView:
    _fold_index(ctx, session)
    lines = _prompt_lines(session_lines(ctx.root, session))
    try:
        read = transcript.find_person_turns(
            session,
            recorded=recorded_transcript_path(ctx.root, session),
            environ=ctx.environ,
            archive=_archive(ctx),
        )
    except ValueError as exc:
        raise UsageError(f"{exc}; --session takes the session's UUID") from exc
    except OSError as exc:
        return _SessionView(
            session, [], lines, False, f"the transcript could not be read ({exc.strerror or exc})"
        )
    if not read.readable:
        return _SessionView(session, [], lines, False, read.why)
    turns = transcript.apply_paste_index(read.turns, confirmed_paste_flags(ctx.root, session))
    return _SessionView(session, sorted(turns, key=lambda t: t.timestamp), lines, True, read.why)


def _line_of(turn, lines: list[dict]) -> dict | None:
    """The index line that stands for a transcript turn: the one naming its record, or the
    person's line with its prompt id."""
    for line in lines:
        if line.get("turn") == turn.id:
            return line
    if turn.prompt_id is not None:
        for line in lines:
            if line.get("prompt_id") == turn.prompt_id and line.get("origin") == HUMAN:
                return line
    return None


def _names(ref: str, ids: tuple, *, prefix: bool) -> bool:
    values = [value for value in ids if isinstance(value, str)]
    if prefix:
        return len(ref) >= MIN_TURN_PREFIX and any(value.startswith(ref) for value in values)
    return ref in values


def _turn_listing(view: _SessionView) -> list[str]:
    return [
        f"  {'prompt ' + turn.prompt_id if turn.prompt_id else 'turn ' + turn.id}  "
        f"{turn.timestamp}  ({transcript.WORDS_FORMS.get(turn.form, turn.form)})"
        for turn in view.turns[-LISTED_TURNS:]
    ]


def _cited(view: _SessionView, turn) -> CitedTurn:
    words, total = turn_words(turn)
    if not words:
        raise ContractViolation(
            CONTRACT_CREDIT_TURN_WORDS_UNREAD,
            [
                f"turn {turn.id} of session {view.session} holds no typed words of the "
                "person's (a paste or an image alone), so there is nothing to copy: "
                "nothing was written",
                "name another of their turns with --turn, or give their words in "
                "--quote-file FILE",
            ],
        )
    line = _line_of(turn, view.lines) or {}
    root = line.get("root") if isinstance(line.get("root"), str) else None
    return CitedTurn(
        session=view.session,
        turn=turn.id,
        prompt_id=turn.prompt_id,
        at=turn.timestamp,
        form=turn.form,
        words=words,
        total_chars=total,
        paste_known=turn.paste_known,
        root=root,
        lines=line.get("lines") if root and isinstance(line.get("lines"), int) else None,
        segments=tuple(turn.segments),
    )


def session_turns(ctx: Context, session: str) -> list[CitedTurn]:
    """The person's turns of one session as a credit can cite them, oldest first, for
    `kyo turns --said`. A turn with no typed words is left out, since a credit cannot
    cite it."""
    view = _session_view(ctx, session)
    out: list[CitedTurn] = []
    for turn in view.turns:
        try:
            out.append(_cited(view, turn))
        except ContractViolation:
            continue
    return out


def _find_in(view: _SessionView, ref: str) -> CitedTurn | None:
    """The turn of the person's that ``ref`` names in one session, or None when it names
    nothing there. Refuses a reference that names several turns, a line of the index that
    is not the person's, and a turn of the person's the transcript does not hold."""

    def ids(turn) -> tuple:
        line = _line_of(turn, view.lines) or {}
        return (turn.id, turn.prompt_id, line.get("prompt_id"), line.get("turn"))

    for prefix in (False, True):
        found = [turn for turn in view.turns if _names(ref, ids(turn), prefix=prefix)]
        if len(found) == 1:
            return _cited(view, found[0])
        if found:
            raise UsageError(
                "\n".join(
                    [
                        f"--turn {ref!r} names {len(found)} of the person's turns in session "
                        f"{view.session}; give more of the id:",
                        *(
                            f"  {'prompt ' + t.prompt_id if t.prompt_id else 'turn ' + t.id}"
                            f"  {t.timestamp}"
                            for t in found
                        ),
                    ]
                )
            )
    for prefix in (False, True):
        lines = [
            line
            for line in view.lines
            if _names(ref, (line.get("prompt_id"), line.get("turn")), prefix=prefix)
        ]
        if not lines:
            continue
        line = lines[0]
        if line.get("origin") == HUMAN:
            raise ContractViolation(
                CONTRACT_CREDIT_TURN_WORDS_UNREAD,
                [
                    f"--turn {ref} is the person's turn of {line.get('at')} in session "
                    f"{view.session}, and its words cannot be copied: {view.why}; nothing "
                    "was written",
                    "give their words in --quote-file FILE without --turn, which records "
                    "what the transcript check could and could not confirm",
                ],
            )
        raise ContractViolation(
            CONTRACT_CREDIT_TURN_NOT_THE_PERSONS,
            [
                f"--turn {ref} is not a turn of the person's: the turn index records the "
                f"prompt of {line.get('at')} in session {view.session} as "
                f"{line.get('origin') or 'of unknown origin'} (a task notification and a "
                "subagent's report arrive as prompts too), and a credit cites the "
                "person's own words: nothing was written",
                "name one of the person's turns with --turn; `kyo turns --said` lists "
                "them" + (". Their latest in that session:" if view.turns else ""),
                *_turn_listing(view),
                "or leave --turn out, and their latest turn of the session is taken",
            ],
        )
    return None


def _resolve_turn(ctx: Context, views: dict[str, _SessionView], session: str | None, ref: str):
    """One ``--turn`` reference as a :class:`CitedTurn`: looked for in the credit's own
    session first, then in whichever one other session of the turn index holds it."""
    if session is not None:
        found = _find_in(views[session], ref)
        if found is not None:
            return found
    elsewhere: set[str] = set()
    for prefix in (False, True):
        elsewhere = {
            line["session"]
            for line in _prompt_lines(read_lines(ctx.root))
            if isinstance(line.get("session"), str)
            and line["session"] != session
            and _names(ref, (line.get("prompt_id"), line.get("turn")), prefix=prefix)
        }
        if elsewhere:
            break
    if len(elsewhere) == 1:
        other = elsewhere.pop()
        if other not in views:
            views[other] = _session_view(ctx, other)
        found = _find_in(views[other], ref)
        if found is not None:
            return found
    where = f"session {session}" if session is not None else "the turn index"
    listing = _turn_listing(views[session]) if session is not None else []
    raise UsageError(
        "\n".join(
            [
                f"--turn {ref!r} names "
                + (f"turns of {len(elsewhere)} sessions" if elsewhere else "no turn")
                + f" of the person's in {where}; give a prompt id or a transcript record "
                "uuid from the turn index, whole or by a unique prefix of at least "
                f"{MIN_TURN_PREFIX} characters"
                + (". The person's latest turns there:" if listing else ""),
                *listing,
            ]
        )
    )


def _holds(turn: CitedTurn, quote: str) -> bool:
    """Whether ``quote`` is words of this turn: found in its typed segments as a quote is
    checked, or the turn's stored words whole."""
    pieces = transcript.quote_pieces(quote)
    if not pieces:
        return False
    if transcript.pieces_in(pieces, turn.segments):
        return True
    return transcript.normalise(quote) == transcript.normalise(turn.words)


def cite_turns(
    ctx: Context, store, args: Namespace, quote_text: str | None
) -> tuple[Cited | None, str]:
    """KYO-900: the turns this invocation's credit cites, or None with why none can be read
    and the words must come from ``--quote-file``.

    Turns are cited when the project reads transcripts and either ``--turn`` names them
    or, with no ``--quote-file`` either, a Claude Code session is known, whose latest turn
    of the person's is taken. ``--quote-file`` given alone is the earlier path, unchanged:
    the words are checked against the session's transcript. So is ``--quote-file`` beside
    one ``--turn`` when no transcript can be read, or the turn is a prompt-history line,
    since that pair has always meant "the record that holds these words".

    The result is cached on ``args``: a command that stores the words itself reads them
    first (:func:`words_from_turns`), and :func:`resolve` must see the same turns."""
    held = getattr(args, "_kyo_cited", None)
    if held is not None and quote_text in held[0]:
        return held[1]
    result = _cite_turns(ctx, store, args, quote_text)
    keys = {quote_text}
    if result[0] is not None and not (quote_text or "").strip():
        # The words were copied from the turns: asked again with those words, as the
        # command that stored them will, the answer is the same turns.
        keys.add(result[0].quote_text)
    args._kyo_cited = (keys, result)
    return result


def _cite_turns(
    ctx: Context, store, args: Namespace, quote_text: str | None
) -> tuple[Cited | None, str]:
    refs = turn_refs_of(args)
    quoted = bool((quote_text or "").strip())
    if getattr(args, "source_other", None) is not None:
        return None, ""
    if not transcripts_opted_in(store.config):
        return None, (
            "this project does not read transcripts "
            f"(`{READ_TRANSCRIPTS_KEY}:` in kyosei/config.yml), so kyo cannot copy them "
            "from a turn: pass --quote-file FILE"
        )
    session = getattr(args, "session", None) or ctx.environ.get(ENV_SESSION) or None
    if not refs and quoted:
        return None, ""
    if session is None and not refs:
        return None, (
            "no Claude Code session is known, so there is no turn to copy them from: pass "
            "--quote-file FILE, with --source-other WHERE if they were said outside Claude "
            "Code"
        )
    views: dict[str, _SessionView] = {}
    if session is not None:
        views[session] = _session_view(ctx, session)
    if not refs:
        assert session is not None
        view = views[session]
        if not view.turns:
            return None, (
                f"session {session} holds no turn of the person's that kyo can read "
                f"({view.why}): pass --quote-file FILE"
            )
        chosen = [_cited(view, view.turns[-1])]
    else:
        legacy = (
            quoted
            and len(refs) == 1
            and (
                refs[0].startswith(transcript.HISTORY_ID_PREFIX)
                or (session is not None and not views[session].readable)
            )
        )
        if legacy:
            return None, ""
        chosen = []
        for ref in refs:
            turn = _resolve_turn(ctx, views, session, ref)
            if not any((t.session, t.turn) == (turn.session, turn.turn) for t in chosen):
                chosen.append(turn)
    chosen.sort(key=lambda turn: turn.at)
    if quoted:
        assert quote_text is not None
        holders = [turn for turn in chosen if _holds(turn, quote_text)]
        if not holders:
            raise ContractViolation(
                CONTRACT_CREDIT_QUOTE_NOT_FOUND,
                [
                    "the words in --quote-file occur in none of the turns --turn names ("
                    + ", ".join(turn.label() for turn in chosen)
                    + "); pasted content in a turn is not the person's words: nothing was "
                    "written",
                    "correct the quote to their exact words, marking an omission with "
                    "[...]; or drop --quote-file, and the whole of each named turn is stored",
                ],
            )
        approval = holders[-1]
        stored = quote_text
    else:
        approval = chosen[-1]
        stored = approval.words
    approval.role = TURN_ROLE_APPROVAL
    return Cited(chosen, approval, stored, defaulted=not refs), ""


def words_from_turns(ctx: Context, store, args: Namespace, given: str | None) -> str | None:
    """The person's words for a command that stores them itself (a verdict's evidence, a
    filing's evidence, a confirmation's quote): ``given`` when the command was handed
    words, else the approving turn's words when the credit can cite turns, else None."""
    if (given or "").strip():
        return given
    cited, _why = cite_turns(ctx, store, args, None)
    return cited.quote_text if cited is not None else given


def _legacy_turn(args: Namespace) -> str | None:
    """``--turn`` for the path that reads no turn from the index: at most one, the
    transcript record (or prompt-history line) holding the quoted words."""
    refs = turn_refs_of(args)
    if len(refs) > 1:
        raise UsageError(
            "several --turn cite several turns of the turn index, and here the words come "
            "from --quote-file alone, which names at most one: the project does not read "
            "transcripts, or the session's transcript cannot be read"
        )
    return refs[0] if refs else None


@dataclass
class Credit:
    """A credit resolved and checked, not yet written."""

    person: str
    role: str
    quote_text: str
    source_kind: str
    source_session: str | None
    source_note: str | None
    check: dict = field(default_factory=dict)
    check_why: str = ""
    decision: dict = field(default_factory=dict)
    span: dict = field(default_factory=dict)
    span_why: str = ""
    #: KYO-900: the turns the credit cites, in transcript order; empty for a credit whose
    #: words were given in a file.
    turns: list[CitedTurn] = field(default_factory=list)
    #: Whether the turn was taken by default, as the person's latest of the session.
    turn_defaulted: bool = False

    @property
    def transcript_check(self) -> str:
        return self.check["transcript_check"]

    def summary(self, label: str) -> str:
        """``observed by jbmv (transcript matched, turn …)``."""
        verb = {ROLE_OBSERVED: "observed", ROLE_DECIDED: "decided", ROLE_AUTHORIZED: "authorized"}
        where = f"transcript {self.transcript_check}"
        if self.check.get("source_turn"):
            where += f", turn {self.check['source_turn']}"
        if self.check.get("words_form"):
            where += f", {self.check['words_form']}"
        if self.source_kind == SOURCE_OTHER:
            where = f"said elsewhere: {self.source_note}; not checked"
        return f"{verb[self.role]} by {label} ({where})"

    def flags(self) -> list[str]:
        """The flags that give this same credit to another command (``kyo confirm``)."""
        out = ["--person", self.person]
        if self.source_kind == SOURCE_OTHER:
            out += ["--source-other", self.source_note or ""]
        else:
            out += ["--session", self.source_session or ""]
            if self.turns:
                for turn in self.turns:
                    out += ["--turn", turn.turn]
            elif self.check.get("source_turn"):
                out += ["--turn", self.check["source_turn"]]
        return out


def enforced(store) -> bool:
    """KYO-656 (KYO-654, DC12): whether credits are required in this project at all, which
    is whether its ``people:`` declares anyone."""
    return bool(declared_people(store.config))


def exempt(store, args: Namespace, *, words_flag: bool = True) -> bool:
    """KYO-656: no credit is needed for this write, because the project declares nobody and
    the invocation asked for none."""
    return not enforced(store) and not requested(args, words_flag=words_flag)


def _person(store, given: str | None) -> str | None:
    """``--person``, or the only declared person; None when it cannot be decided."""
    declared = declared_people(store.config)
    if given is None:
        return next(iter(declared)) if len(declared) == 1 else None
    _check_declared(given, declared)
    return given


def _check_declared(given: str, declared: dict) -> None:
    if given in declared:
        return
    raise ContractViolation(
        CONTRACT_CREDIT_PERSON_UNDECLARED,
        [
            f"{given!r} is not declared under `people:` in kyosei/config.yml (declared: "
            + (", ".join(declared) or "nobody")
            + "): nothing was written",
            "a credit names a declared person; declare them there, or give --person one "
            "of the declared keys",
        ],
    )


def resolve(
    ctx: Context,
    store,
    args: Namespace,
    *,
    role: str,
    quote_text: str | None,
    contract: str,
    act: str,
    remedies: list[str],
    decision_text: str | None = None,
    reconstructed: bool = False,
) -> Credit:
    """Resolve and check one credit from the invocation's flags. Writes nothing.

    ``contract`` and ``act`` name the enforcement point the refusal is raised under when
    a part of the credit is missing, and ``remedies`` are its ready lines. ``decision_text``
    is the decision's YAML when a caller holds it (``kyo credit --batch``), in place of a
    ``--decision-file``. ``reconstructed`` is true for a credit written with a ``--basis``:
    only such a credit may leave its decision's outcome unrecorded (KYO-666).
    """
    session = getattr(args, "session", None)
    other = getattr(args, "source_other", None)
    if session and other:
        raise UsageError(
            "--session and --source-other both say where the words were said; give one"
        )
    if other is not None and turn_refs_of(args):
        raise UsageError("--turn names a transcript record, and --source-other has none")
    person = _person(store, getattr(args, "person", None))
    if session is None and other is None:
        session = ctx.environ.get(ENV_SESSION) or None
    # KYO-900: the turns the credit cites, and with them the words, where kyo can copy them.
    cited, no_turn_why = cite_turns(ctx, store, args, quote_text)
    if cited is not None:
        quote_text = cited.quote_text
        session = cited.approval.session
    flags_text = _decision_text(args)
    if flags_text is not None:
        if decision_text is not None:
            raise UsageError(
                "--outcome states one credit's outcome, and this line already states its own"
            )
        if role != ROLE_DECIDED:
            raise UsageError(
                f"--outcome, --recommended and --recommended-by belong to a decided credit; "
                f"this one is {role}, which records no recommendation or outcome"
            )
        decision_text = flags_text
    missing = []
    if person is None:
        declared = declared_people(store.config)
        missing.append(
            "the person (--person KEY: "
            + (
                f"{len(declared)} are declared, {', '.join(declared)}"
                if declared
                else "nobody is declared under `people:` in kyosei/config.yml"
            )
            + ")"
        )
    if not (quote_text or "").strip():
        missing.append("the person's words" + (f" ({no_turn_why})" if no_turn_why else ""))
    if session is None and other is None:
        missing.append(
            f"where they were said (--session ID, or ${ENV_SESSION} in a Claude Code "
            "shell, or --source-other for anywhere else)"
        )
    decision_path = getattr(args, "decision_file", None)
    if role == ROLE_DECIDED and decision_path is None and decision_text is None:
        missing.append(
            "what was decided (--outcome, with --recommended unless it is unprompted; or "
            "--decision-file for a decision with alternatives)"
        )
    if missing:
        raise ContractViolation(
            contract,
            [
                f"{act} names the person whose judgement it records, and this invocation "
                f"lacks {'; '.join(missing)}: nothing was written",
                *remedies,
            ],
        )
    assert person is not None and quote_text is not None
    if other is not None:
        credit = Credit(
            person,
            role,
            quote_text,
            SOURCE_OTHER,
            None,
            single_line(other, what="--source-other"),
            check={
                "transcript_check": CHECK_NOT_APPLICABLE,
                "source_turn": None,
                "observed_at": None,
                "words_form": None,
            },
            check_why="said outside Claude Code, so no transcript was read",
        )
    elif not transcripts_opted_in(store.config):
        assert session is not None
        credit = Credit(
            person,
            role,
            quote_text,
            SOURCE_CLAUDE_CODE,
            session,
            None,
            check={
                "transcript_check": CHECK_NOT_APPLICABLE,
                # As given: nothing here read a transcript to confirm it.
                "source_turn": _legacy_turn(args),
                "observed_at": None,
                "words_form": None,
            },
            check_why=NOT_OPTED_IN_WHY,
        )
    elif cited is not None:
        credit = Credit(
            person,
            role,
            quote_text,
            SOURCE_CLAUDE_CODE,
            cited.approval.session,
            None,
            check=cited.check(),
            check_why=cited.why(),
            turns=cited.turns,
            turn_defaulted=cited.defaulted,
        )
    else:
        assert session is not None
        result = _check(ctx, session, quote_text, _legacy_turn(args), person)
        credit = Credit(
            person,
            role,
            quote_text,
            SOURCE_CLAUDE_CODE,
            session,
            None,
            check=result.store_fields(),
            check_why=result.why,
        )
    if role == ROLE_DECIDED:
        if decision_text is None:
            decision_text = read_prose(decision_path, what="--decision-file")
        credit.decision = _decision(ctx, store, decision_text, credit, reconstructed=reconstructed)
    elif decision_path is not None:
        raise UsageError(
            f"--decision-file belongs to a decided credit; this one is {role}, which records "
            "no recommendation, outcome or alternatives"
        )
    credit.span, credit.span_why = _span(ctx, store, args, credit)
    return credit


def _archive(ctx: Context):
    """This project's transcript archive, where the readers look once an original is gone."""
    from .transcript_archive import archive_dir

    return archive_dir(ctx.store().config, ctx.environ)


def _check(ctx: Context, session: str, quote: str, turn: str | None, person: str):
    try:
        result = transcript.check_quote(
            session,
            quote,
            turn=turn,
            recorded=recorded_transcript_path(ctx.root, session),
            environ=ctx.environ,
            archive=_archive(ctx),
            paste_flags=confirmed_paste_flags(ctx.root, session),
        )
    except ValueError as exc:
        raise UsageError(f"{exc}; --session takes the session's UUID") from exc
    if result.outcome == transcript.NOT_FOUND:
        raise ContractViolation(
            CONTRACT_CREDIT_QUOTE_NOT_FOUND,
            [
                f"the words are not {person}'s in session {session}: {result.why}; nothing "
                "was written (KYO-640: a present transcript decides, with no override)",
                "correct the quote to their exact words, marking an omission with [...]; "
                "or give --session for the session they said it in, or --source-other if "
                "they said it outside Claude Code",
            ],
        )
    if result.outcome == transcript.AMBIGUOUS:
        raise ContractViolation(
            CONTRACT_CREDIT_QUOTE_AMBIGUOUS,
            [
                f"the words occur in {len(result.candidates)} of {person}'s turns in session "
                f"{session}, so they do not say which one is credited: nothing was written",
                *(f"  turn {turn_id}  {stamp}" for turn_id, stamp in result.candidates),
                "pass --turn with the uuid of the turn the words answer",
            ],
        )
    return result



def _check_role_subjects(role: str, kinds: set[str]) -> None:
    """Which subjects a role may name (KYO-668, KYO-671). An observed credit names
    verifications or gates. A decided credit or an authorized go-ahead names either a record
    or the verifications and gates that recorded it, never a mix: the agents recorded some of
    the person's decisions and go-aheads as a criterion's verdict or a gate's clearing, and
    the credit follows the person's words, wherever the row that holds them is."""
    if role == ROLE_OBSERVED and "record" in kinds:
        raise UsageError("an observed credit names verifications or gates, not a record")
    if role in (ROLE_DECIDED, ROLE_AUTHORIZED) and "record" in kinds and kinds != {"record"}:
        raise UsageError(
            f"a {role} credit names a record, or verifications and gates, not both: "
            f"these subjects are {sorted(kinds)}"
        )

def _decision(
    ctx: Context, store, text: str, credit: Credit, *, reconstructed: bool = False
) -> dict:
    """The ``--decision-file``: what was recommended, the outcome, the alternatives.

    A reconstructed credit (one with a ``--basis``) that names neither an outcome nor a
    recommendation records both empty: nobody observed whether a recommendation preceded
    the decision, and a stored outcome would be one nobody measured (KYO-666). A live
    credit must state its outcome.
    """
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise UsageError(f"--decision-file is not YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise UsageError("--decision-file holds a mapping of recommended, outcome, alternatives")
    extra = sorted(set(map(str, raw)) - DECISION_FILE_KEYS)
    if extra:
        raise UsageError(
            f"--decision-file carries {', '.join(extra)}; it reads "
            f"{', '.join(sorted(DECISION_FILE_KEYS))}"
        )
    outcome = raw.get("outcome")
    unrecorded = outcome is None and raw.get("recommended") is None
    if outcome not in CREDIT_OUTCOMES and not (reconstructed and unrecorded):
        extra_hint = (
            "; state one in the decision file (a credit written after the fact with "
            "--basis may leave outcome and recommended both out)"
            if outcome is None and not reconstructed
            else ""
        )
        raise UsageError(
            f"--decision-file outcome is {outcome!r}; it is one of "
            f"{', '.join(CREDIT_OUTCOMES)}{extra_hint}"
        )
    recommended = raw.get("recommended")
    if recommended is not None:
        recommended = single_line(str(recommended), what="--decision-file recommended")
    if outcome is None:
        pass
    elif outcome == "unprompted" and recommended:
        raise UsageError(
            "--decision-file says the person decided unprompted and also names a "
            "recommendation; an unprompted decision had none"
        )
    if outcome is not None and outcome != "unprompted" and not recommended:
        raise UsageError(
            f"--decision-file outcome {outcome!r} is an answer to a recommendation, and "
            "names none in `recommended`"
        )
    alternatives = raw.get("alternatives") or []
    if not isinstance(alternatives, list) or not all(
        isinstance(a, dict) and isinstance(a.get("text"), str) and a["text"].strip()
        for a in alternatives
    ):
        raise UsageError(
            "--decision-file alternatives is a list of {text, chosen, proposed_by} entries"
        )
    alternatives = [_alternative(store, a, credit) for a in alternatives]
    if alternatives and not any(a["chosen"] for a in alternatives):
        raise UsageError("--decision-file lists alternatives and marks none `chosen: true`")
    refining_turn = raw.get("refining_turn")
    refining_session = raw.get("refining_session") or (
        credit.source_session if refining_turn else None
    )
    if outcome == CREDIT_OUTCOME_REFINED:
        if not refining_turn or not refining_session:
            raise UsageError(
                "--decision-file outcome 'refined' cites the turn that refined the "
                "recommendation as refining_turn (and refining_session when it is not "
                "the credit's session)"
            )
        _require_person_turn(ctx, store, str(refining_session), str(refining_turn))
    elif refining_turn:
        raise UsageError(
            f"--decision-file refining_turn belongs to outcome 'refined', not {outcome!r}"
        )
    return {
        "recommended": recommended,
        "recommended_by": (
            single_line(str(raw["recommended_by"]), what="recommended_by")
            if raw.get("recommended_by")
            else (ctx.actor if recommended else None)
        ),
        "outcome": outcome,
        "alternatives": alternatives or None,
        "refining_session": str(refining_session) if refining_session else None,
        "refining_turn": str(refining_turn) if refining_turn else None,
    }


def _alternative(store, raw: dict, credit: Credit) -> dict:
    """One alternative as stored: its text, whether it was chosen, and who proposed it when
    the file says (KYO-786).

    ``proposed_by`` is ``person`` (the person the credit names), a key under ``people:``,
    or one of :data:`AGENT_PROPOSERS`. An entry that names no proposer stores none, and a
    reader credits the agent (:func:`proposer_of`): the maintainer's rule is that an option
    is the person's only when their own words introduced it, so the person is credited
    with an option only where the file says so.
    """
    extra = sorted(set(map(str, raw)) - ALTERNATIVE_KEYS)
    if extra:
        raise UsageError(
            f"--decision-file gives an alternative {', '.join(extra)}; an alternative "
            f"reads {', '.join(sorted(ALTERNATIVE_KEYS))}"
        )
    out = {
        "text": single_line(raw["text"], what="an alternative"),
        "chosen": raw.get("chosen") is True,
    }
    proposer = raw.get("proposed_by")
    if proposer is None:
        return out
    proposer = AGENT_PROPOSER_ALIASES.get(str(proposer), str(proposer))
    if proposer == PROPOSER_PERSON:
        proposer = credit.person
    elif proposer not in AGENT_PROPOSERS and proposer not in store.config.people:
        raise UsageError(
            f"--decision-file gives an alternative proposed_by {proposer!r}; it is "
            f"`{PROPOSER_PERSON}` for the person the credit names, a key under `people:` "
            f"in kyosei/config.yml, or one of {', '.join(AGENT_PROPOSERS)}"
        )
    return {**out, "proposed_by": proposer}


def proposer_of(alternative: dict) -> str:
    """Who proposed a stored alternative: what the credit recorded, or the orchestrator
    where it recorded nothing (KYO-786). A credit written before alternatives carried a
    proposer reads the same way."""
    return alternative.get("proposed_by") or PROPOSER_DEFAULT


def _require_person_turn(ctx: Context, store, session: str, turn: str) -> None:
    """A refining turn must be one of the person's when the transcript can say so."""
    if not transcripts_opted_in(store.config):
        return
    try:
        read = transcript.find_person_turns(
            session,
            recorded=recorded_transcript_path(ctx.root, session),
            environ=ctx.environ,
            archive=_archive(ctx),
        )
    except (ValueError, OSError):
        return
    if read.readable and turn not in {t.id for t in read.turns}:
        raise UsageError(
            f"refining_turn {turn} is not one of the person's turns in session {session} "
            f"({len(read.turns)} read)"
        )


def _span(ctx: Context, store, args: Namespace, credit: Credit) -> tuple[dict, str]:
    first = getattr(args, "span_first", None)
    last = getattr(args, "span_last", None)
    if not first and not last:
        if getattr(args, "span_session", None):
            raise UsageError("--span-session goes with --span-first and --span-last")
        return {}, ""
    if not (first and last):
        raise UsageError("a discussion span is cited by both --span-first and --span-last")
    session = getattr(args, "span_session", None) or credit.source_session
    if not session:
        raise UsageError(
            "--span-first and --span-last name records of a Claude Code session, and this "
            "credit names none: give --span-session"
        )
    return _measured(ctx, store, session, first, last)


def _measured(
    ctx: Context, store, session: str, first: str, last: str, *, derived: bool = False
) -> tuple[dict, str]:
    """A span's ids and what kyo measured of it, with why (KYO-640.n1). ``derived`` words
    a refusal for a span read from a discussion's turn references (KYO-658)."""
    fields = {"span_session": session, "span_first_turn": first, "span_last_turn": last}
    if not transcripts_opted_in(store.config):
        fields.update(transcript.SpanMeasure(CHECK_NOT_APPLICABLE, NOT_OPTED_IN_WHY).store_fields())
        return fields, NOT_OPTED_IN_WHY
    try:
        measure = transcript.measure_span(
            session,
            first,
            last,
            recorded=recorded_transcript_path(ctx.root, session),
            environ=ctx.environ,
            archive=_archive(ctx),
        )
    except ValueError as exc:
        raise UsageError(f"{exc}; --span-session takes the session's UUID") from exc
    if measure.outcome in (transcript.SPAN_TURN_NOT_FOUND, transcript.SPAN_REVERSED):
        if derived:
            raise UsageError(
                "the span this credit takes from its discussion's turn references cannot be "
                f"measured: {measure.why}; cite it with --span-first and --span-last"
            )
        raise UsageError(f"the discussion span cannot be measured: {measure.why}")
    fields.update(measure.store_fields())
    fields["span_seal"], _ = _span_seal(ctx, session, last)
    return fields, measure.why


def _span_seal(ctx: Context, session: str, last: str) -> tuple[str | None, str]:
    """KYO-663: the hash of the span's last line in the turn index, or None with why.

    The index learns a prompt's transcript record at the Stop that ends its turn, and a
    credit is usually written before that Stop, so the session's transcript is folded
    into the index first, as the Stop hook would (best effort).
    """
    _fold_index(ctx, session)
    return seal.span_seal(session_lines(ctx.root, session), last)


def discussion_span(
    refs: list[dict], credit: Credit, current: dict | None
) -> tuple[tuple[str, str, str] | None, str]:
    """KYO-658 (decision KYO-654, DC3): the span a decided credit takes from the discussion
    it was filed from, as ``(session, first turn, last turn)``, or None with why.

    ``refs`` are the discussion's turn references (export shape; a late opening made in the
    filing's own transaction included), and ``current`` is the person's latest confirmed
    turn as the index holds it now, in the same shape, or None. The first turn is where the
    discussion was opened. The last is, in the opening's session, the turn holding the
    credited words when the transcript check found one; else the person's latest turn;
    else the latest turn the discussion's references name. A span lies in one session, so
    a later turn in another session does not end it.
    """
    opened = next((r for r in refs if r.get("role") == "opened"), None)
    if opened is None or not opened.get("turn"):
        return None, (
            "the discussion's opening names no transcript record (index absent), so it "
            "gives the span no first turn"
        )
    session = opened["session"]
    last = None
    if credit.source_session == session and credit.check.get("source_turn"):
        last = credit.check["source_turn"]
    elif current and current.get("session") == session and current.get("turn"):
        last = current["turn"]
    else:
        named = [r for r in refs if r.get("session") == session and r.get("turn") and r.get("at")]
        last = max(named, key=lambda r: r["at"])["turn"] if named else opened["turn"]
    return (session, opened["turn"], last), ""


def apply_discussion_span(
    ctx: Context, store, args: Namespace, credit: Credit, refs: list[dict], current: dict | None
) -> None:
    """Give ``credit`` the span its discussion's turn references derive, measured as an
    explicit span is, unless ``--span-first``/``--span-last`` already cited one."""
    if credit.span or getattr(args, "span_first", None) or getattr(args, "span_last", None):
        return
    span, why = discussion_span(refs, credit, current)
    if span is None:
        credit.span_why = why
        return
    credit.span, credit.span_why = _measured(ctx, store, *span, derived=True)


# ------------------------------------------------------------------------- writing


def write(
    store,
    record_id: str,
    credit: Credit,
    *,
    quote_id: str,
    subjects: list[tuple[str, str]],
    author: str,
    basis_id: str | None = None,
    created_at: str | None = None,
) -> str:
    """Store ``credit`` through :meth:`Store.add_credit`; return its id.

    ``created_at`` is the subject's own write time when the credit is written in the
    subject's transaction (KYO-644, for KYO-647's exact "reconstructed" derivation)."""
    credit_id = store.add_credit(
        record_id,
        created_at=created_at,
        person=credit.person,
        role=credit.role,
        quote_id=quote_id,
        source_kind=credit.source_kind,
        subjects=subjects,
        author=author,
        source_session=credit.source_session,
        source_note=credit.source_note,
        basis_id=basis_id,
        **credit.check,
        **credit.decision,
        **credit.span,
    )
    # KYO-900: each cited turn, in transcript order. The approval's words are the credit's
    # own quote; a context turn's are stored as an evidence artifact of their own, so that
    # `kyo redact` and the sha256 cover them as they cover the quote.
    for position, turn in enumerate(credit.turns, 1):
        words_id = quote_id
        if turn.role != TURN_ROLE_APPROVAL:
            words_id, _digest = _add_evidence(
                store, record_id, turn.words, supports_id=None, author=author
            )
        store.add_credit_turn(
            credit_id,
            position=position,
            role=turn.role,
            session=turn.session,
            turn=turn.turn,
            prompt_id=turn.prompt_id,
            at=turn.at,
            words_form=turn.words_form,
            quote_id=words_id,
            root=turn.root,
            lines=turn.lines,
        )
    return credit_id


def lines(store, credit_id: str, credit: Credit) -> list[str]:
    """What a credit-writing command echoes: who was credited, always (P3)."""
    label = person_label(store.config, credit.person)
    out = [f"credit {credit_id}: {credit.summary(label)}"]
    if credit.check_why and credit.source_kind == SOURCE_CLAUDE_CODE:
        out.append(f"  {credit.check_why}")
    out += turn_lines(store, credit_id, credit)
    if credit.decision:
        d = credit.decision
        out.append(
            f"  outcome: {d['outcome']}"
            + (
                f"; recommended by {d['recommended_by']}: {d['recommended']}"
                if d["recommended"]
                else ""
            )
        )
    if credit.span:
        s = credit.span
        if s.get("span_active_seconds") is not None:
            out.append(
                f"  discussion: {s['span_person_turns']} turn(s) of the person's, "
                f"{s['span_agent_turns']} reply(ies) of the agent's, "
                f"{s['span_active_seconds']}s active, {s['span_started_at']} to "
                f"{s['span_ended_at']}"
            )
        else:
            out.append(f"  discussion cited by id, not measured: {credit.span_why}")
        if s.get("span_seal"):
            out.append(f"  span sealed: {s['span_seal']}")
        else:
            out.append(
                "  span not sealed: the turn index holds no sealed line for the span's "
                "last turn"
            )
    return out


def turn_lines(store, credit_id: str, credit: Credit | None) -> list[str]:
    """KYO-900: what a receipt says of the turns a credit cites: each turn's role, time
    and the first line of its words as stored, so that a wrong turn is seen at once; and,
    for a credit resolved by this invocation (``credit``), how much of a long turn was
    kept and, for a sole turn of a few words, that earlier turns can be cited as context."""
    from .render import credit_turn_lines

    stored = [export._credit_turn(row) for row in store.credit_turns(credit_id)]
    if not stored:
        return []
    words = {
        turn["quote_id"]: store.evidence_artifact(turn["quote_id"])["text"] for turn in stored
    }
    out = credit_turn_lines({"turns": stored}, words, indent="  ")
    if credit is None or not credit.turns:
        return out
    for turn in credit.turns:
        if turn.total_chars > len(turn.words) and (
            turn.role != TURN_ROLE_APPROVAL or credit.quote_text == turn.words
        ):
            out.append(
                f"    {turn.label()} is {turn.total_chars} characters of typed words; its "
                f"first {TURN_WORDS_HEAD} and last {TURN_WORDS_TAIL} are stored around {OMISSION}"
            )
    approval = next(turn for turn in credit.turns if turn.role == TURN_ROLE_APPROVAL)
    count = len(approval.words.split())
    if len(credit.turns) == 1 and count <= SHORT_TURN_WORDS:
        out.append(
            f"  the cited turn is {count} word(s). Where earlier turns specified what it "
            "approves, a credit can cite them as context: --turn <id> for each of them and "
            "for this one"
        )
    return out


def join(store, credit_id: str, record_id: str, *, quote_sha256: str | None = None):
    """An existing credit another row of the same act is added to (``--credit``).

    It must be current, on the same record, and, when the joining command stores words
    too, hold the same words: ``--credit`` adds a row to one act, it does not lend an old
    credit to a new one.
    """
    try:
        row = store.credit(credit_id)
    except NotFound as exc:
        raise UsageError(f"--credit {credit_id}: no such credit") from exc
    if row["superseded_by"]:
        raise UsageError(f"--credit {credit_id} was superseded by {row['superseded_by']}")
    if row["withdrawn_at"]:
        raise UsageError(f"--credit {credit_id} was withdrawn: {row['withdrawn_why']}")
    if row["record_id"] != record_id:
        raise UsageError(f"--credit {credit_id} is {row['record_id']}'s, not {record_id}'s")
    if quote_sha256 is not None:
        held = store.evidence_artifact(row["quote_id"])["sha256"]
        if held != quote_sha256:
            raise UsageError(
                f"--credit {credit_id} holds other words than this verdict's evidence; one "
                "credit is one act of the person's"
            )
    return row


# ------------------------------------------------------------------------ kyo credit


def _subject(store, subject_id: str) -> tuple[str, str, str]:
    """``(kind, record id, write time)`` of one subject, or a refusal naming why not."""
    kind = ids.child_kind(subject_id)
    if kind == "verification":
        row = store.conn.execute(
            "SELECT record_id, method, created_at FROM verifications WHERE id = ?", (subject_id,)
        ).fetchone()
        if row is None:
            raise UsageError(f"no verification {subject_id!r}")
        if row["method"] != "human-observed":
            raise UsageError(
                f"{subject_id} is {row['method']}, and a credit names the person behind a "
                "human-observed verdict"
            )
        return "verification", row["record_id"], row["created_at"]
    if kind == "gate":
        row = store.gate(subject_id)
        if row["gate_type"] not in HUMAN_GATE_TYPES or not row["cleared_at"]:
            raise UsageError(
                f"{subject_id} is {'an open' if not row['cleared_at'] else 'a cleared'} "
                f"{row['gate_type']} gate; a credit names the person who answered a "
                f"cleared {' or '.join(sorted(HUMAN_GATE_TYPES))} gate"
            )
        return "gate", row["record_id"], row["cleared_at"]
    if ids.is_record_id(subject_id):
        row = store.record(subject_id)
        return "record", subject_id, row["created_at"]
    raise UsageError(
        f"{subject_id!r} is not a verification (RECORD.vN), a gate (RECORD.gN) or a record id"
    )


def check_basis(store, basis_id: str, person: str) -> None:
    """``--basis`` must be ``person``'s own current ``decided`` credit, or a refusal."""
    try:
        basis = store.credit(basis_id)
    except NotFound:
        basis = None
    if (
        basis is None
        or basis["role"] != ROLE_DECIDED
        or basis["person"] != person
        or basis["superseded_by"]
        or basis["withdrawn_at"]
    ):
        state = (
            "superseded "
            if basis is not None and basis["superseded_by"]
            else "withdrawn "
            if basis is not None and basis["withdrawn_at"]
            else ""
        )
        what = (
            "no credit"
            if basis is None
            else f"a {state}{basis['role']} credit by {basis['person']}"
        )
        raise ContractViolation(
            CONTRACT_CREDIT_BASIS_NOT_APPROVAL,
            [
                f"--basis {basis_id} is {what}, and the basis of a credit to "
                f"{person} is {person}'s own current decided credit "
                "approving it: nothing was written",
                f"give as --basis the id of {person}'s decided credit on the "
                "record that approved this batch",
            ],
        )


def cmd_credit(ctx: Context, args: Namespace) -> Output:
    """``kyo credit <subject-id>... --person … --quote-file … (--session … |
    --source-other …) [--turn …] [--role …] [--decision-file …] [--span-first … --span-last
    …] --basis <credit-id> [--supersede <credit-id>]`` (KYO-644, KYO-638 section 4.5).

    Credits rows that already exist: a human-observed verification, a cleared human gate,
    or a record (``decided`` or ``authorized``). Such a credit is written after its subject
    and so is reconstructed, and every reconstructed credit needs ``--basis``: the
    person's own current ``decided`` credit approving the batch (P8). One exception, for
    the role KYO-644.c8 adds: an ``authorized`` go-ahead whose words the transcript dates
    after the record was filed is the go-ahead itself, credited when it was given, and
    needs no basis, provided it is credited from the session that holds those words
    (KYO-653: the credit is then not derived as reconstructed either).

    ``--recheck <credit-id>`` and ``--batch FILE`` are the two other forms: they are
    :func:`recheck` and :func:`batch`.
    """
    if getattr(args, "withdraw", None):
        return withdraw(ctx, args)
    if getattr(args, "why", None) is not None or getattr(args, "why_file", None) is not None:
        raise UsageError(
            "--why and --why-file give the reason for --withdraw, and this is no "
            "withdrawal: drop them, or add --withdraw CREDIT_ID"
        )
    if args.recheck:
        return recheck(ctx, args)
    if args.batch:
        return batch(ctx, args)
    if not args.subject_ids:
        raise UsageError(
            "kyo credit names the rows to credit: give one or more SUBJECT_ID, or "
            "--recheck CREDIT_ID, or --batch FILE"
        )
    store = ctx.store()
    subject_ids = list(dict.fromkeys(args.subject_ids))
    resolved = [(sid, *_subject(store, sid)) for sid in subject_ids]
    kinds = {kind for _sid, kind, _rec, _at in resolved}
    records = {rec for _sid, _kind, rec, _at in resolved}
    if len(records) > 1:
        raise UsageError(
            f"one credit is filed on one record, and these subjects are on "
            f"{', '.join(sorted(records))}"
        )
    record_id = records.pop()
    role = args.role or (ROLE_DECIDED if kinds == {"record"} else ROLE_OBSERVED)
    if role not in CREDIT_ROLES:
        raise UsageError(f"--role {role!r}; known: {', '.join(CREDIT_ROLES)}")
    _check_role_subjects(role, kinds)
    quote_text = read_prose(args.quote_file, what="--quote-file") if args.quote_file else None
    rerun = "kyo " + shlex.join(["credit", *subject_ids])
    credit = resolve(
        ctx,
        store,
        args,
        role=role,
        quote_text=quote_text,
        contract=CONTRACT_HUMAN_OBSERVED_REQUIRES_CREDIT
        if role == ROLE_OBSERVED
        else CONTRACT_FILING_REQUIRES_CREDIT,
        act="a credit",
        remedies=[
            f"give the missing parts to the same command: {rerun} with --person, and "
            "--turn <id> for the turn the words were said in (a project that reads "
            "transcripts) or --quote-file with --session or --source-other"
        ],
        reconstructed=args.basis is not None,
    )

    already = {
        sid: store.current_credits_of(sid) for sid, *_ in resolved if store.current_credits_of(sid)
    }
    superseded = args.supersede
    if superseded is not None:
        old = store.credit(superseded)
        if old["superseded_by"]:
            raise UsageError(
                f"--supersede {superseded} was already superseded by {old['superseded_by']}"
            )
        if old["withdrawn_at"]:
            raise UsageError(
                f"--supersede {superseded} was withdrawn, so it is no longer a current "
                "credit and nothing needs replacing: rerun this command without --supersede"
            )
        named = {s["subject_id"] for s in store.credit_subjects(superseded)}
        if not named & set(subject_ids):
            raise UsageError(
                f"--supersede {superseded} names {', '.join(sorted(named))}, none of these subjects"
            )
    blocking = {
        sid: rows for sid, rows in already.items() if any(r["id"] != superseded for r in rows)
    }
    if blocking:
        raise ContractViolation(
            CONTRACT_CREDIT_SUBJECT_ALREADY_CREDITED,
            [
                *(
                    f"{sid} already has a current credit: "
                    + ", ".join(
                        f"{r['id']} ({r['role']} by {r['person']})"
                        for r in rows
                        if r["id"] != superseded
                    )
                    for sid, rows in blocking.items()
                ),
                "nothing was written. A second current credit would leave two answers to "
                "who the row belongs to; rerun this command with --supersede "
                + next(r["id"] for rows in blocking.values() for r in rows if r["id"] != superseded)
                + " to replace that credit, keeping it readable",
            ],
        )

    latest_subject = max(at for *_rest, at in resolved)
    go_ahead_after = bool(
        role == ROLE_AUTHORIZED
        and credit.check.get("observed_at")
        and credit.check["observed_at"] > latest_subject
    )
    # KYO-653: live only from the session that holds the words; credited from anywhere else
    # it is a credit written after the fact, and rests on a basis like any other.
    live_go_ahead = go_ahead_after and ctx.environ.get(ENV_SESSION) == credit.source_session
    basis_id = args.basis
    if basis_id is None and not live_go_ahead:
        raise ContractViolation(
            CONTRACT_RECONSTRUCTED_CREDIT_REQUIRES_BASIS,
            [
                f"{', '.join(subject_ids)} already exist, so this credit is written after "
                "the fact (reconstructed), and a reconstructed credit rests on the person's "
                "approval of it: nothing was written",
                "file the batch on a record, have the person approve it, credit that "
                "approval with role decided, and give its id as --basis",
                *(
                    [
                        f"or, for a go-ahead given in session {credit.source_session}, run "
                        f"this command from that session (${ENV_SESSION} names it), which "
                        "needs no basis"
                    ]
                    if go_ahead_after
                    else []
                ),
            ],
        )
    if basis_id is not None:
        check_basis(store, basis_id, credit.person)

    with store.transaction():
        before = {record_id: export.record_document(store, record_id)}
        quote_id, _digest = _add_evidence(
            store, record_id, credit.quote_text, supports_id=None, author=ctx.actor
        )
        credit_id = write(
            store,
            record_id,
            credit,
            quote_id=quote_id,
            subjects=[(kind, sid) for sid, kind, _rec, _at in resolved],
            author=ctx.actor,
            basis_id=basis_id,
        )
        if superseded is not None:
            store.supersede_credit(superseded, credit_id)
        _touch_record(store, record_id)
        after, events = commands._commit_and_export(
            store,
            command=f"credit {' '.join(subject_ids)}",
            actor=ctx.actor,
            record_ids=[record_id],
            before=before,
        )
    commands._finish_mutation(store, [record_id], events)
    out = [f"credited {', '.join(subject_ids)} on {record_id}, written by {ctx.actor}"]
    out += lines(store, credit_id, credit)
    out.append(f"  words stored as {quote_id}")
    if basis_id:
        out.append(f"  reconstructed; approved in {basis_id}")
    elif live_go_ahead:
        out.append("  a go-ahead given after the record was filed; no basis needed")
    if superseded:
        out.append(f"  supersedes {superseded}")
    return Output(
        out,
        {
            "ok": True,
            "command": "credit",
            "credit_id": credit_id,
            "record_id": record_id,
            "quote_id": quote_id,
            "basis_id": basis_id,
            "superseded": superseded,
            "record": after[record_id],
        },
    )


# -------------------------------------------------------------------- --withdraw


def withdraw(ctx: Context, args: Namespace) -> Output:
    """``kyo credit --withdraw <credit-id> --why <reason>`` (KYO-670): retract a current
    credit that should not exist, with no replacement.

    The model is ``--supersede``: the credit leaves the current set and stays readable.
    Unlike a supersession nothing takes its place, so the rows it named are uncredited
    again, and doctor's credit-coverage notice counts them. The withdrawal records who
    withdrew it (the acting author), when, and the reason, which is required.
    """
    credit_id = args.withdraw
    extra = [
        flag
        for flag, given in (
            ("a SUBJECT_ID", args.subject_ids),
            ("--recheck", args.recheck),
            ("--batch", args.batch),
            ("--role", args.role),
            ("--basis", args.basis),
            ("--supersede", args.supersede),
            ("--decision-file", args.decision_file),
            ("--span-first", args.span_first),
            ("--span-last", args.span_last),
            ("--span-session", args.span_session),
        )
        if given
    ]
    if requested(args):
        extra.append("a credit flag")
    if extra:
        raise UsageError(
            f"--withdraw retracts {credit_id} as it stands and writes no new credit; give it "
            f"no {', '.join(extra)}"
        )
    why = commands._reason(args)
    if not why:
        raise UsageError(
            f"--withdraw {credit_id} needs a reason, stored with the withdrawal: rerun "
            f"`kyo credit --withdraw {credit_id} --why <reason>`"
        )
    store = ctx.store()
    try:
        old = store.credit(credit_id)
    except NotFound as exc:
        raise UsageError(f"--withdraw {credit_id}: no such credit") from exc
    if old["withdrawn_at"]:
        raise UsageError(
            f"--withdraw {credit_id}: it was already withdrawn at {old['withdrawn_at']} by "
            f"{old['withdrawn_by']} ({old['withdrawn_why']}); nothing was written"
        )
    if old["superseded_by"]:
        raise UsageError(
            f"--withdraw {credit_id}: it was superseded by {old['superseded_by']}, which is "
            "the current credit: withdraw that one with "
            f"`kyo credit --withdraw {old['superseded_by']} --why <reason>`"
        )
    record_id = old["record_id"]
    subjects = [r["subject_id"] for r in store.credit_subjects(credit_id)]
    resting = [
        row["id"]
        for row in store.conn.execute(
            "SELECT id FROM credits WHERE basis_id = ? AND superseded_by IS NULL"
            " AND withdrawn_at IS NULL ORDER BY record_id, seq",
            (credit_id,),
        )
    ]
    with store.transaction():
        before = {record_id: export.record_document(store, record_id)}
        store.withdraw_credit(credit_id, ctx.actor, why)
        _touch_record(store, record_id)
        after, events = commands._commit_and_export(
            store,
            command=f"credit --withdraw {credit_id}",
            actor=ctx.actor,
            record_ids=[record_id],
            before=before,
        )
    commands._finish_mutation(store, [record_id], events)
    label = person_label(store.config, old["person"])
    out = [
        f"withdrew {credit_id} on {record_id}, written by {ctx.actor}",
        f"  it was {old['role']} by {label}; its words stay readable as {old['quote_id']}",
        f"  why: {why}",
        f"  uncredited again: {', '.join(subjects)}",
    ]
    if resting:
        out.append(
            f"  {len(resting)} current credit(s) cite it as their basis and are unchanged: "
            + ", ".join(resting)
        )
    return Output(
        out,
        {
            "ok": True,
            "command": "credit",
            "withdrawn": credit_id,
            "record_id": record_id,
            "why": why,
            "subjects": subjects,
            "basis_of": resting,
            "record": after[record_id],
        },
    )


# --------------------------------------------------------------------- --recheck


def recheck(ctx: Context, args: Namespace) -> Output:
    """``kyo credit --recheck <credit-id>`` (KYO-653): redo the transcript check of a credit
    recorded ``transcript-absent`` and supersede it with the new outcome.

    The new credit copies the old one whole (person, role, subjects, basis, decision, span,
    the stored words, and its ``created_at``, so that a reconstructed credit stays
    reconstructed) and takes only the transcript's outcome from the new check. The old one
    stays readable as superseded. A transcript that is still absent changes nothing.
    """
    credit_id = args.recheck
    extra = [
        flag
        for flag, given in (
            ("a SUBJECT_ID", args.subject_ids),
            ("--batch", args.batch),
            ("--role", args.role),
            ("--basis", args.basis),
            ("--supersede", args.supersede),
            ("--decision-file", args.decision_file),
            ("--span-first", args.span_first),
            ("--span-last", args.span_last),
            ("--span-session", args.span_session),
        )
        if given
    ]
    if requested(args):
        extra.append("a credit flag")
    if extra:
        raise UsageError(
            f"--recheck re-reads the words and the place already stored on {credit_id}; "
            f"give it no {', '.join(extra)}"
        )
    store = ctx.store()
    try:
        old = store.credit(credit_id)
    except NotFound as exc:
        raise UsageError(f"--recheck {credit_id}: no such credit") from exc
    if old["superseded_by"]:
        raise UsageError(
            f"--recheck {credit_id}: it was superseded by {old['superseded_by']}; recheck that one"
        )
    if old["withdrawn_at"]:
        raise UsageError(
            f"--recheck {credit_id}: it was withdrawn ({old['withdrawn_why']}), and only a "
            "current credit is rechecked"
        )
    if old["transcript_check"] != "transcript-absent":
        raise UsageError(
            f"--recheck {credit_id}: its transcript check is {old['transcript_check']}, and "
            "only a credit recorded transcript-absent is rechecked"
        )
    record_id = old["record_id"]
    if not transcripts_opted_in(store.config):
        raise ContractViolation(
            CONTRACT_CREDIT_TRANSCRIPTS_NOT_OPTED_IN,
            [
                f"--recheck {credit_id} reads a transcript, and {NOT_OPTED_IN_WHY}: nothing "
                "was written",
                f"add `{READ_TRANSCRIPTS_KEY}: true` to kyosei/config.yml if the operator "
                "agrees (setup decision D14), then rerun this command",
            ],
        )
    quote = store.evidence_artifact(old["quote_id"])["text"]
    result = _check(ctx, old["source_session"], quote, old["source_turn"], old["person"])
    if result.outcome == transcript.TRANSCRIPT_ABSENT:
        raise ContractViolation(
            CONTRACT_CREDIT_TRANSCRIPT_STILL_ABSENT,
            [
                f"the transcript of session {old['source_session']} is still not on this "
                f"machine, so {credit_id} stays as it is: nothing was written",
                "put the transcript file where Claude Code keeps it (under "
                f"$CLAUDE_CONFIG_DIR/projects/), then rerun `kyo credit --recheck {credit_id}`",
            ],
        )
    fields = {
        name: old[name]
        for name in (
            "recommended",
            "recommended_by",
            "outcome",
            "refining_session",
            "refining_turn",
            "span_session",
            "span_first_turn",
            "span_last_turn",
            "span_person_turns",
            "span_agent_turns",
            "span_active_seconds",
            "span_started_at",
            "span_ended_at",
            "span_seal",
        )
    }
    subjects = [(r["subject_kind"], r["subject_id"]) for r in store.credit_subjects(credit_id)]
    with store.transaction():
        before = {record_id: export.record_document(store, record_id)}
        new_id = store.add_credit(
            record_id,
            created_at=old["created_at"],
            person=old["person"],
            role=old["role"],
            quote_id=old["quote_id"],
            source_kind=old["source_kind"],
            subjects=subjects,
            author=ctx.actor,
            source_session=old["source_session"],
            source_note=old["source_note"],
            basis_id=old["basis_id"],
            alternatives=json.loads(old["alternatives_json"]) if old["alternatives_json"] else None,
            **fields,
            **result.store_fields(),
        )
        for turn in store.credit_turns(credit_id):
            store.add_credit_turn(
                new_id,
                **{name: turn[name] for name in turn.keys() if name != "credit_id"},  # noqa: SIM118
            )
        store.supersede_credit(credit_id, new_id)
        _touch_record(store, record_id)
        after, events = commands._commit_and_export(
            store,
            command=f"credit --recheck {credit_id}",
            actor=ctx.actor,
            record_ids=[record_id],
            before=before,
        )
    commands._finish_mutation(store, [record_id], events)
    label = person_label(store.config, old["person"])
    out = [
        f"rechecked {credit_id} on {record_id}, written by {ctx.actor}",
        f"credit {new_id}: transcript {result.outcome} for {label}",
        f"  {result.why}",
        f"  supersedes {credit_id}",
    ]
    return Output(
        out,
        {
            "ok": True,
            "command": "credit",
            "credit_id": new_id,
            "record_id": record_id,
            "superseded": credit_id,
            "transcript_check": result.outcome,
            "record": after[record_id],
        },
    )


# ----------------------------------------------------------------------- --batch

#: The columns of a ``--batch`` file, tab-separated, one credit per line. The last column
#: takes the rest of the line, so the words may hold tabs.
BATCH_COLUMNS = ("subjects", "role", "where", "turn", "outcome", "recommended", "quote")

_BATCH_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\"}


def _unescape(text: str) -> str:
    r"""``\n`` and ``\t`` in a batch column are a newline and a tab; ``\\`` is a backslash."""
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text) and text[i + 1] in _BATCH_ESCAPES:
            out.append(_BATCH_ESCAPES[text[i + 1]])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _batch_rows(text: str) -> list[tuple[int, dict[str, str]]]:
    """``(line number, columns)`` of each credit line; blank and ``#`` lines are skipped."""
    rows = []
    for number, line in enumerate(text.split("\n"), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t", len(BATCH_COLUMNS) - 1)
        parts += [""] * (len(BATCH_COLUMNS) - len(parts))
        cells = [p.strip() for p in parts[:-1]] + [parts[-1]]
        rows.append((number, dict(zip(BATCH_COLUMNS, cells, strict=True))))
    return rows


def _batch_line(ctx: Context, store, args: Namespace, row: dict[str, str]) -> dict:
    """One line of a batch resolved and checked: the credit and where it goes. Raises the
    refusal the same credit would get from `kyo credit`, which the caller attributes to
    its line."""
    subject_ids = list(dict.fromkeys(i.strip() for i in row["subjects"].split(",") if i.strip()))
    if not subject_ids:
        raise UsageError("the subjects column is empty")
    resolved = [(sid, *_subject(store, sid)) for sid in subject_ids]
    kinds = {kind for _sid, kind, _rec, _at in resolved}
    records = {rec for _sid, _kind, rec, _at in resolved}
    if len(records) > 1:
        raise UsageError(f"these subjects are on {', '.join(sorted(records))}, not one record")
    role = row["role"] or (ROLE_DECIDED if kinds == {"record"} else ROLE_OBSERVED)
    if role not in CREDIT_ROLES:
        raise UsageError(f"role {role!r}; known: {', '.join(CREDIT_ROLES)}")
    _check_role_subjects(role, kinds)
    where = row["where"]
    session, other = (None, where[len("other:") :].strip()) if where.startswith("other:") else (
        where or None,
        None,
    )
    if session is None and other is None:
        raise UsageError(
            "the where column is empty: a Claude Code session id, or other:<where it was said>"
        )
    line_args = Namespace(
        person=args.person,
        session=session,
        source_other=other,
        turn=row["turn"] or None,
        decision_file=None,
        span_first=None,
        span_last=None,
        span_session=None,
    )
    decision_text = None
    if role == ROLE_DECIDED:
        decision = {"outcome": row["outcome"] or None}
        if row["recommended"]:
            decision["recommended"] = row["recommended"]
        decision_text = yaml.safe_dump(decision)
    elif row["outcome"] or row["recommended"]:
        raise UsageError(
            f"the outcome and recommended columns belong to a decided credit, not {role}"
        )
    credit = resolve(
        ctx,
        store,
        line_args,
        role=role,
        quote_text=_unescape(row["quote"]),
        contract=CONTRACT_CREDIT_BATCH_INVALID,
        act="a batch credit",
        remedies=["fill the missing column of that line"],
        decision_text=decision_text,
        reconstructed=True,
    )
    if role == ROLE_DECIDED and credit.decision.get("outcome") == CREDIT_OUTCOME_REFINED:
        raise UsageError(
            "a refined outcome cites its refining turn, which a batch line has no column for; "
            "credit that one alone with `kyo credit`"
        )
    return {
        "subjects": [(kind, sid) for sid, kind, _rec, _at in resolved],
        "record_id": records.pop(),
        "credit": credit,
    }


def batch(ctx: Context, args: Namespace) -> Output:
    """``kyo credit --batch FILE --basis <credit-id> [--person KEY]`` (KYO-653, KYO-638
    section 4.5): one reconstructed credit per line of FILE, written in one transaction.

    Every line shares the one ``--basis``, the person's own decided credit approving the
    batch. Every line is resolved and checked before anything is written; if any line is
    refused, the whole batch is, naming each bad line with the reason. The file is
    described in ``kyo reference credit``.
    """
    extra = [
        flag
        for flag, given in (
            ("a SUBJECT_ID", args.subject_ids),
            ("--role", args.role),
            ("--supersede", args.supersede),
            ("--quote-file", args.quote_file),
            ("--session", args.session),
            ("--turn", args.turn),
            ("--source-other", args.source_other),
            ("--decision-file", args.decision_file),
            ("--outcome", getattr(args, "outcome", None)),
            ("--recommended", getattr(args, "recommended", None)),
            ("--recommended-by", getattr(args, "recommended_by", None)),
            ("--span-first", args.span_first),
            ("--span-last", args.span_last),
            ("--span-session", args.span_session),
        )
        if given
    ]
    if extra:
        raise UsageError(
            f"--batch takes each line's subjects, words and place from the file; give it no "
            f"{', '.join(extra)} (only --basis and --person are shared)"
        )
    if args.basis is None:
        raise ContractViolation(
            CONTRACT_RECONSTRUCTED_CREDIT_REQUIRES_BASIS,
            [
                "every credit of a batch is written after its subject (reconstructed), and "
                "rests on the person's approval of the batch: nothing was written",
                "file the batch on a record, have the person approve it, credit that "
                "approval with role decided, and give its id as --basis",
            ],
        )
    store = ctx.store()
    person = _person(store, args.person)
    if person is None:
        declared = declared_people(store.config)
        raise ContractViolation(
            CONTRACT_CREDIT_BATCH_INVALID,
            [
                "a batch credits one person and none was named ("
                + (
                    f"{len(declared)} are declared, {', '.join(declared)}"
                    if declared
                    else "nobody is declared under `people:` in kyosei/config.yml"
                )
                + "): nothing was written",
                "give --person KEY, a key under `people:` in kyosei/config.yml",
            ],
        )
    args.person = person
    check_basis(store, args.basis, person)
    rows = _batch_rows(read_prose(args.batch, what="--batch"))
    if not rows:
        raise UsageError(f"--batch {args.batch} holds no credit lines")
    prepared: list[dict] = []
    problems: list[str] = []
    seen: dict[str, int] = {}
    for number, row in rows:
        try:
            line = _batch_line(ctx, store, args, row)
            for _kind, sid in line["subjects"]:
                if sid in seen:
                    raise UsageError(f"{sid} is also credited on line {seen[sid]}")
                current = store.current_credits_of(sid)
                if current:
                    raise UsageError(
                        f"{sid} already has a current credit ({current[0]['id']}); credit "
                        "it alone with `kyo credit` --supersede"
                    )
            for _kind, sid in line["subjects"]:
                seen[sid] = number
            prepared.append(line)
        except (UsageError, ContractViolation, NotFound) as exc:
            reason = " ".join(
                (exc.unmet[0] if isinstance(exc, ContractViolation) else str(exc)).split()
            )
            problems.append(f"line {number}: {reason}")
    if problems:
        raise ContractViolation(
            CONTRACT_CREDIT_BATCH_INVALID,
            [
                f"{len(problems)} of {len(rows)} line(s) of {args.batch} cannot be credited, "
                "so none of the batch was written",
                *problems,
                "correct or remove those lines and rerun the same command",
            ],
        )
    record_ids = list(dict.fromkeys(line["record_id"] for line in prepared))
    credit_ids: list[str] = []
    with store.transaction():
        before = {rid: export.record_document(store, rid) for rid in record_ids}
        for line in prepared:
            quote_id, _digest = _add_evidence(
                store, line["record_id"], line["credit"].quote_text, supports_id=None,
                author=ctx.actor,
            )  # fmt: skip
            credit_ids.append(
                write(
                    store,
                    line["record_id"],
                    line["credit"],
                    quote_id=quote_id,
                    subjects=line["subjects"],
                    author=ctx.actor,
                    basis_id=args.basis,
                )
            )
        for rid in record_ids:
            _touch_record(store, rid)
        after, events = commands._commit_and_export(
            store,
            command=f"credit --batch {args.batch}",
            actor=ctx.actor,
            record_ids=record_ids,
            before=before,
        )
    commands._finish_mutation(store, record_ids, events)
    label = person_label(store.config, person)
    out = [
        f"credited {len(credit_ids)} line(s) of {args.batch} to {label} on "
        f"{len(record_ids)} record(s), written by {ctx.actor}",
        f"  reconstructed; approved in {args.basis}",
    ]
    return Output(
        out,
        {
            "ok": True,
            "command": "credit",
            "written": len(credit_ids),
            "credit_ids": credit_ids,
            "record_ids": record_ids,
            "basis_id": args.basis,
        },
    )


def credit_json(store, credit_id: str) -> dict:
    """One credit as the export writes it, for a command's ``--json``."""
    return export._credit(store, store.credit(credit_id))


def refuse_unused_flags(args: Namespace, why: str) -> None:
    """A credit flag given where no credit is written is refused, not dropped."""
    given = [
        flag
        for name, flag in (
            ("person", "--person"),
            ("session", "--session"),
            ("turn", "--turn"),
            ("source_other", "--source-other"),
            ("credit", "--credit"),
        )
        if getattr(args, name, None)
    ]
    if given:
        raise UsageError(f"{', '.join(given)} belong to a credit, and {why}")


# ------------------------------------------------------------- clearing a person's gate


@dataclass
class GateClearing:
    """How one `kyo ungate` (or carried settle) clears its gates (KYO-640 P5).

    ``credit``: the person answered, in words resolved and checked here. ``joined``: the
    answer is an existing credit of the same act (`kyo confirm`). ``withdrawn``: no person
    answered and the wait stopped mattering. None of the three: no gate waits on a person.
    """

    credit: Credit | None = None
    joined: str | None = None
    withdrawn: bool = False

    def cleared_as(self) -> str | None:
        if self.withdrawn:
            return CLEARED_WITHDRAWN
        if self.credit is not None or self.joined is not None:
            return CLEARED_ANSWERED
        return None

    def write(
        self, store, gates: list[tuple[str, str]], author: str, cleared_at: str | None = None
    ) -> str | None:
        """Write the credit naming the cleared gates ``(gate id, record id)``, inside the
        caller's transaction; return its id, or None when there is none."""
        if not gates or (self.credit is None and self.joined is None):
            return None
        record_ids = {record for _gate, record in gates}
        if len(record_ids) > 1:
            raise UsageError(
                "one credit is filed on one record, and these gates are on "
                f"{', '.join(sorted(record_ids))}; clear each record's gates on their own"
            )
        record_id = record_ids.pop()
        if self.joined is not None:
            join(store, self.joined, record_id)
            for gate_id, _record in gates:
                store.add_credit_subject(self.joined, "gate", gate_id)
            return self.joined
        assert self.credit is not None
        quote_id, _digest = _add_evidence(
            store, record_id, self.credit.quote_text, supports_id=record_id, author=author
        )
        return write(
            store,
            record_id,
            self.credit,
            quote_id=quote_id,
            subjects=[("gate", gate_id) for gate_id, _record in gates],
            author=author,
            created_at=cleared_at,
        )


def gate_clearing(
    ctx: Context,
    store,
    args: Namespace,
    gates: list,
    reason: str | None,
    *,
    command: str,
) -> GateClearing:
    """Decide how ``gates`` are cleared, refusing a person's gate cleared with neither a
    credit nor ``--withdrawn``. ``command`` is the refused line as typed, for the remedies.
    Writes nothing."""
    withdrawn = bool(getattr(args, "withdrawn", False))
    joined = getattr(args, "credit", None)
    if withdrawn:
        if requested(args) or joined:
            raise UsageError(
                "--withdrawn says no person answered, and the credit flags say one did; give one"
            )
        if not (reason or "").strip():
            raise UsageError(
                "--withdrawn records that the wait stopped mattering, and the reason it did "
                "goes in --why or --why-file"
            )
        return GateClearing(withdrawn=True)
    if joined is not None:
        if requested(args):
            raise UsageError("--credit names an existing credit; give no other credit flag")
        return GateClearing(joined=joined)
    human = [g for g in gates if g["gate_type"] in HUMAN_GATE_TYPES and not g["cleared_at"]]
    remedies = [
        "the person's answer, with their words and where they said them: "
        f"`{command} --quote-file FILE --person KEY --source-other <where>` (or --session "
        "ID for a Claude Code session, whose transcript is checked; in a project that "
        "reads transcripts --turn <id> names the turn they answered in, and no file is "
        "needed)",
        f"or, when the wait no longer applies: `{command} --withdrawn --why <reason>`",
    ]
    # KYO-900: with no credit flag at all, the person's latest turn of the session is their
    # answer where kyo can read it; the receipt echoes the turn, so a wrong one is seen.
    takes_latest = (
        not requested(args)
        and bool(human)
        and enforced(store)
        and cite_turns(ctx, store, args, None)[0] is not None
    )
    if not requested(args) and not takes_latest:
        if human and enforced(store):
            raise ContractViolation(
                CONTRACT_PERSON_GATE_CLEAR_REQUIRES_CREDIT,
                [
                    *(f"{g['id']} ({g['gate_type']}) waits on a person" for g in human),
                    "clearing it says the person answered, so it names who and in what "
                    "words, or says plainly that nobody did: nothing was cleared",
                    *remedies,
                ],
            )
        return GateClearing()
    quote_file = getattr(args, "quote_file", None)
    quote_text = read_prose(quote_file, what="--quote-file") if quote_file else None
    credit = resolve(
        ctx,
        store,
        args,
        role=ROLE_OBSERVED,
        quote_text=quote_text,
        contract=CONTRACT_PERSON_GATE_CLEAR_REQUIRES_CREDIT,
        act="clearing a gate that waits on a person",
        remedies=remedies,
    )
    return GateClearing(credit=credit)


def add_gate_clearing_flags(parser: ArgumentParser, *, credit_join: bool = True) -> None:
    """The credit flags plus ``--withdrawn`` (and ``--credit``) on a command that clears
    gates."""
    add_credit_flags(parser)
    parser.add_argument(
        "--withdrawn",
        action="store_true",
        help="no person answered: the wait stopped mattering. Needs --why; the gate is "
        "recorded cleared_as withdrawn (KYO-644)",
    )
    if credit_join:
        parser.add_argument(
            "--credit",
            metavar="CREDIT_ID",
            help="add this clearing to an existing credit of the same act (kyo confirm "
            "uses it); the credit must be current and on the same record",
        )


def refuse_settle_over_person_gate(gates: list, command: str) -> None:
    """KYO-644 (KYO-640 P5): a carried settle that would clear a gate waiting on a person
    needs the person's answer, or ``--withdrawn``, as `kyo ungate` does."""
    if not gates:
        return
    raise ContractViolation(
        CONTRACT_PERSON_GATE_CLEAR_REQUIRES_CREDIT,
        [
            *(f"{g['id']} ({g['gate_type']}) waits on a person" for g in gates),
            "settling this carried record clears its open gates, and clearing one that "
            "waits on a person says the person answered, so it names who and in what "
            "words, or says plainly that nobody did: nothing was written",
            "the person's answer, with their words and where they said them: "
            f"`{command} --why <reason> --quote-file FILE --person KEY --source-other "
            "<where>` (in a project that reads transcripts, --turn <id> in place of "
            "--quote-file names the turn they answered in)",
            f"or, when the wait no longer applies: `{command} --why <reason> --withdrawn`",
        ],
    )


def settle_clearing(ctx: Context, store, args: Namespace, reason: str | None) -> GateClearing:
    """The clearing a carried settle (`kyo move`, `kyo wont-do`) was given, resolved before
    its transaction. With no credit flag and no ``--withdrawn`` it is empty, and
    :func:`refuse_settle_over_person_gate` decides inside the settle whether that is
    allowed."""
    if not (requested(args) or getattr(args, "withdrawn", False)):
        return GateClearing()
    return gate_clearing(ctx, store, args, [], reason, command="kyo move")


# ---------------------------------------------------------------- filing a decision


def filing_credit(
    ctx: Context, store, kind_def, args: Namespace, evidence_text: str | None, command: str
) -> Credit | None:
    """KYO-644 (KYO-640 P5, P9): the ``decided`` credit `kyo new` / `kyo spawn` of a kind
    with ``policy.requires_credit`` needs, resolved before anything is written; None for
    any other kind. ``--quote-file`` holds the person's words deciding it; given without
    ``--evidence-file`` it is also the filing's evidence, read once
    (``cmd_records._evidence_at_filing``), so a direct filing stays one command."""
    if not kind_def.requires_credit or exempt(store, args):
        return None
    quote_file = getattr(args, "quote_file", None)
    if quote_file and not getattr(args, "evidence_file", None):
        quote_text = evidence_text
    else:
        quote_text = read_prose(quote_file, what="--quote-file") if quote_file else None
    return resolve(
        ctx,
        store,
        args,
        role=ROLE_DECIDED,
        quote_text=quote_text,
        contract=CONTRACT_FILING_REQUIRES_CREDIT,
        act=f"kind {kind_def.name!r} sets policy.requires_credit, so `{command}`",
        remedies=[
            "give the same command what was decided: --outcome (with --recommended unless "
            "it is unprompted), or --decision-file for a decision with alternatives. In a "
            "Claude Code session of a project that reads transcripts the person's latest "
            "turn is taken as their words, and --turn <id> names another or adds context",
            "elsewhere give their words in --quote-file, the person in --person, and "
            "--session ID for a Claude Code session or --source-other <where> for "
            "anywhere else",
        ],
    )


def filing_credit_flag_problem(args: Namespace, kind_def) -> None:
    """A credit flag on a filing of a kind that takes no credit is refused, not dropped."""
    if kind_def.requires_credit:
        return
    refuse_unused_flags(args, f"kind {kind_def.name!r} does not set policy.requires_credit")
    for name, flag in (
        ("quote_file", "--quote-file"),
        ("decision_file", "--decision-file"),
        ("outcome", "--outcome"),
        ("recommended", "--recommended"),
        ("recommended_by", "--recommended-by"),
    ):
        if getattr(args, name, None):
            raise UsageError(
                f"{flag} belongs to a credit, and kind {kind_def.name!r} does not set "
                "policy.requires_credit"
            )
