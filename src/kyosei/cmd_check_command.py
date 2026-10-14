"""``kyo check-command --pre-tool-use`` and ``kyo pre-tool-use`` — guards before a tool call.

KYO-298 adds this guard, and it is the first that runs *before* a tool call rather
than at a stop: ``kyo check-command --pre-tool-use`` refuses a command this project
has declared to be the whole suite when a worker is about to run it inside a running
dispatch's worktree. It lives beside the other two, because the question it
asks is the one they ask — *which worktree is this about, and whose is it?* — and
because it resolves a worktree by the same rules they do. Its `PreToolUse` plumbing
is `kyo check-push`'s (:mod:`kyosei.cmd_publish`), which is the only other command
that reads that payload; what it shares with that command is the payload reader and
the blocking exit, and nothing else.

KYO-312 gives that third guard a second refusal on the same payload: a Bash call whose
own declared ``timeout`` is longer than the ceiling this project states. It is here
rather than in a fourth command because it asks the guard's one question of the same
payload at the same instant — *which worktree is this about, and whose is it?* — and a
second hook on every Bash call would double the cost of the busiest path this package
has to buy nothing.
"""


from __future__ import annotations

import json
import sys
from argparse import Namespace
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import shell_words
from .cmd_publish import cmd_check_push, command_in_pre_tool_use_payload, pre_tool_use_cwd
from .commands import (
    Context,
    Output,
)
from .config import (
    load_config,
)
from .contracts import (
    CONTRACT_LONG_COMMAND_OUTLIVES_THE_CACHE,
    CONTRACT_SUITE_IS_THE_CONTROLLERS,
    LONG_COMMAND_OVERRIDE_ENV,
    SUITE_OVERRIDE_ENV,
)
from .errors import GuardRefusal, KyoseiError

# ------------------------------------------------------------------- KYO-298
#
# `kyo check-command --pre-tool-use` — the whole suite is the controller's.
#
# WHY THIS IS A MECHANISM AND NOT A SENTENCE. A rule that loses to a contradiction inside
# its own brief is not enforced by being restated more firmly; the suite run in a worker's
# worktree costs a long wait, a full-context cache re-write, and contention with the
# controller's own run (docs/design/guards.md#kyo-298).
#
# WHAT IS DECLARED AND WHAT IS DERIVED. Only the project knows which command line IS
# its suite, so that is declared (`controller_only_commands` in kyosei/config.yml) and
# never sniffed — the same posture, and the same reasoning, as `worktree_setup`. A
# project that declares nothing gets no refusals at all; that is not a degraded mode,
# it is the opt-out. Everything else is derived: which dispatches are running, where
# their worktrees are, and whether this particular command would run inside one.
#
# WHY IT BLOCKS RATHER THAN WARNS. The cost lands before anything is printed — by the
# time a `make test` has finished there is nothing left to save. That is the same
# reason `kyo check-push` blocks, one resource along, and it comes with the same
# obligation: a blocking hook that fires on commands it should not is trained away
# (KYO-33), so the matching below is narrow, and the override is one word.


# The segmenting, tokenising, env-prefix and cd-following that used to live here are
# :mod:`kyosei.shell_words`, shared with `kyo check-push` so the two guards cannot read a
# command line differently (docs/design/guards.md#kyo-409). What that reading follows and
# what it does not is stated there.

#: ``make`` options that take the next word as their value. ``-C`` is read for where
#: it moves the run; the rest are only stepped over so the targets can be found.
_MAKE_VALUE_OPTIONS = frozenset(
    {"-C", "-f", "-I", "-o", "-W", "--directory", "--file", "--makefile",
     "--include-dir", "--old-file", "--assume-old", "--what-if", "--new-file",
     "--assume-new"}
)
#: ``make`` options whose value is optional and, when given separately, numeric.
_MAKE_OPTIONAL_NUMBER = frozenset({"-j", "--jobs", "-l", "--load-average", "--max-load"})


def _make_targets(args: Sequence[str]) -> tuple[list[str], list[str]]:
    """``make``'s targets past its own options and ``VAR=value`` arguments, and the
    directories its ``-C`` options move it into, in order.

    KYO-409.c4: ``make -j4 test`` runs the suite, and a prefix match on ``make`` then
    ``test`` read ``-j4`` as the target.
    """
    targets: list[str] = []
    moves: list[str] = []
    index = 0
    while index < len(args):
        word = args[index]
        if word in _MAKE_VALUE_OPTIONS:
            if word in ("-C", "--directory") and index + 1 < len(args):
                moves.append(args[index + 1])
            index += 2
            continue
        if word in _MAKE_OPTIONAL_NUMBER:
            following = args[index + 1] if index + 1 < len(args) else ""
            index += 2 if following.replace(".", "", 1).isdigit() else 1
            continue
        if word.startswith("-C") and len(word) > 2:
            moves.append(word[2:])
        elif word.startswith("--directory="):
            moves.append(word.partition("=")[2])
        elif word.startswith("-") or "=" in word:
            pass
        else:
            targets.append(word)
        index += 1
    return targets, moves


def _invocation_matches(tokens: Sequence[str], declared_tokens: Sequence[str]) -> bool:
    """Does ``tokens`` INVOKE the command ``declared_tokens`` names?

    ``tokens`` is what :func:`kyosei.shell_words.invocation` left once the env
    prefix, redirections and any transparent wrapper (``nohup``, ``time``, ``env``,
    ``command``, ``timeout 900`` …) were stepped over. KYO-298.c2's "matching is on
    the invoked command and not a substring of an argument". Three properties, each
    one a case that would otherwise go wrong:

    * A **prefix** match, so ``make test LOG=/tmp/t.log`` — a suite run with its
      output sent to a log — is still `make test`.
    * On **tokens**, so ``make test-data`` is not `make test`: the token is
      ``test-data``, and a substring test would have matched it.
    * From **the command word**, so ``echo "make test"`` is not `make test` (its
      first token is ``echo``) and neither is ``grep -r 'npm run check' .``. An
      argument that happens to spell the command is an argument.

    ``make`` is the one command read past its own options (KYO-409.c4): a declared
    ``make test`` matches any ``make`` whose targets include ``test`` — ``make -j4
    test``, ``make LOG=x test``, ``make lint test`` — because make's options and
    variables may stand anywhere and every named target runs.

    The first token is compared by basename, so ``/usr/bin/make test`` matches what
    ``make test`` declares — the same normalisation the push guard makes.
    """
    if not declared_tokens or not tokens:
        return False
    if Path(tokens[0]).name != Path(declared_tokens[0]).name:
        return False
    if Path(declared_tokens[0]).name == "make":
        wanted, _ = _make_targets(declared_tokens[1:])
        present, _ = _make_targets(tokens[1:])
        return all(target in present for target in wanted)
    if len(tokens) < len(declared_tokens):
        return False
    return list(tokens[1 : len(declared_tokens)]) == list(declared_tokens[1:])


@dataclass(frozen=True)
class CommandMatch:
    """One segment of a command line that invokes a declared controller-only command."""

    #: The segment as the worker wrote it, for a message that quotes them.
    segment: str
    #: The ``controller_only_commands`` entry it matched, normalised as the config
    #: parser stored it.
    declared: str
    #: The directory this segment would run in — the payload's cwd, or wherever the
    #: ``cd``s ahead of it left us. ``None`` when a ``cd`` this parser cannot follow
    #: made the answer unknown, which is reported as unknown and never as "here".
    cwd: Path | None
    #: KYO-298.c3: the worker prefixed this segment with the override.
    overridden: bool

    def as_json(self) -> dict:
        return {
            "segment": self.segment,
            "declared": self.declared,
            "cwd": str(self.cwd) if self.cwd is not None else None,
            "overridden": self.overridden,
        }




def controller_only_matches(
    command: str, declared: tuple[str, ...] | list[str], *, cwd: Path | None
) -> list[CommandMatch]:
    """Every segment of ``command`` that invokes a declared controller-only command,
    each with the directory it would run in.

    The registered consumer of ``controller_only_commands``
    (:data:`kyosei.schema.CONFIG_CONSUMERS`), and the whole of the matching: the
    command below only adds "…and is that directory a running dispatch's worktree?".

    WHAT IT FOLLOWS. :func:`kyosei.shell_words.walk`'s reading: segments in order
    (``&`` included), with a running cwd that starts at ``cwd`` (the `PreToolUse`
    payload's) and moves on ``cd``, ``pushd``/``popd``, ``env -C`` and — for a
    ``make`` — ``make -C``, undone at the end of a ``( … )`` subshell. That is the
    route this record was filed about: a worker's Bash cwd is usually the
    CONTROLLER's checkout, and the command it types is ``cd "<worktree>" && make
    test``. Since KYO-409 the command word is read past transparent wrappers, so
    ``nohup make test > /tmp/r.log 2>&1 &`` — the detached form KYO-312's timeout
    refusal recommends — ``time``, ``env``, ``command`` and ``timeout 900`` in front
    of it all still match.

    WHAT IT DOES NOT FOLLOW, stated rather than discovered later. ``cd -``, a
    directory spelled with a variable or a substitution, ``git -C``, shell keywords
    (``for … do make test``), ``sudo``, ``xargs``, ``bash -c``, and a suite invoked
    from inside a script this project ships. Each of those leaves the cwd UNKNOWN
    (``None``) or the command unrecognised rather than silently assumed, and an
    unknown cwd never produces a refusal — a blocking hook guessing where a command
    will run is the failure mode that gets a hook disabled, and the cost of the gap
    is that a worker who really wants the suite has a second way past a guard that
    already has a one-word override. The rule stays a rule with a stated hole rather
    than a hole nobody wrote down (KYO-193.c2's posture).
    """
    declared_tokens = [shell_words.tokens(entry) for entry in declared]
    if not any(declared_tokens):
        return []
    found: list[CommandMatch] = []
    for step in shell_words.walk(command, cwd):
        argv = step.invocation.argv
        if not argv:
            continue
        for entry, entry_tokens in zip(declared, declared_tokens, strict=True):
            if _invocation_matches(argv, entry_tokens):
                where = step.cwd
                if step.invocation.name == "make":
                    for target in _make_targets(argv[1:])[1]:
                        where = shell_words.moved(where, target)
                found.append(
                    CommandMatch(
                        segment=step.segment,
                        declared=entry,
                        cwd=where,
                        overridden=step.invocation.env.get(SUITE_OVERRIDE_ENV, "")
                        not in ("", "0"),
                    )
                )
                break
    return found


def _payload_cwd(raw: str) -> Path | None:
    """The ``cwd`` a `PreToolUse` payload states — :func:`kyosei.cmd_publish.pre_tool_use_cwd`,
    which both `PreToolUse` guards read (KYO-409)."""
    return pre_tool_use_cwd(raw)


def _is_inside(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


@dataclass(frozen=True)
class RunningWorktree:
    """One running dispatch and the directory it declared, resolved on this machine."""

    dispatch_id: str
    record_id: str
    stored: str
    directory: Path


def running_worktrees(ctx: Context) -> tuple[list[RunningWorktree], str | None]:
    """Every RUNNING dispatch's declared worktree, resolved — and why, if it could not.

    Best-effort in the same strong sense as :func:`_population_profile`: this is read
    from inside a hook that is about to let a tool call through or block it, and a
    store that cannot be opened must produce "I could not tell" rather than a
    traceback in front of the agent. The second element is that reason, and every
    caller renders it rather than swallowing it — a guard that silently stopped
    guarding would be indistinguishable from one that found nothing (KYO-54.c2).

    RUNNING is the whole predicate, and it is what makes the criterion's "the same
    command in the worktree of a dispatch that has reported is allowed" true by
    construction rather than by a second check: :meth:`Store.running_dispatches` is
    already the set of dispatches whose state is ``running``, and a dispatch that has
    reported has left it.
    """
    try:
        rows = ctx.store().running_dispatches()
    except Exception as exc:  # noqa: BLE001 - a hook must never die on the store
        return [], f"this project's store could not be read ({exc})"
    found: list[RunningWorktree] = []
    for row in rows:
        stored = row["worktree"]
        if not stored:
            continue
        try:
            directory = ctx.resolve_worktree(stored).resolve()
        except (OSError, ValueError):  # pragma: no cover - a stored path this box hates
            continue
        found.append(
            RunningWorktree(
                dispatch_id=row["id"],
                record_id=row["record_id"],
                stored=stored,
                directory=directory,
            )
        )
    return found, None


def refusal_lines(
    match: CommandMatch, worktree: RunningWorktree, declared: tuple[str, ...]
) -> list[str]:
    """What the worker is told, and it is three things: the rule, the remedy, the way past.

    The remedy is the project's own and is checked (KYO-298.c2): running the test
    files that cover the change is not refused by this guard, in this worktree, at
    this moment — ``tests/test_kyo298_command_guard.py`` refuses the suite and then
    runs the remedy through the same entry point to prove it comes back clean.

    The override is stated in the same breath as the refusal rather than left to be
    found, because the standing stance this project dispatches under says
    contradicting a rule must be CHEAPER than complying with one that is wrong. A
    guard with no way past it does not produce agreement, it produces a worker who
    stops saying what they think — and the report it asks for is what turns the
    override into evidence the rule needs changing.
    """
    return [
        f"`{match.segment}` runs `{match.declared}`, which this project declares in "
        f"kyosei/config.yml as `orchestrator_only_commands` (spelled "
        "`controller_only_commands` before KYO-519) — the whole suite, and the "
        f"orchestrator's to run",
        f"  where: {match.cwd} — inside {worktree.directory}, the worktree declared by "
        f"{worktree.dispatch_id} ({worktree.record_id}), whose state is running",
        "  why: the suite here answers a question nobody asked — it is green on your "
        "branch BEFORE every other worker's merge — while it contends with the "
        "orchestrator's own run for whatever the suite holds exclusively (a port, a "
        "display, a database, a device), and while it runs your prompt cache expires "
        "and your whole context is re-written. Measured on one wave of five workers: "
        "two of them did this, 172,733 and 325,752 tokens of cache "
        "creation between them, 28% of everything that wave's workers wrote",
        "  remedy: run the test FILES that cover your change instead — your orchestrator "
        "runs the suite once, after the wave merges. Nothing else about this command "
        "line is refused, and no other command is",
        f"  override: re-run it as `{SUITE_OVERRIDE_ENV}=1 {match.segment}` and it is "
        "allowed. If you use it, say so in your report's `disagreements[]` — that is "
        "what makes the override evidence rather than a bypass",
        "  this project's declared orchestrator-only commands: " + ", ".join(declared),
    ]


@dataclass(frozen=True)
class CommandPolicy:
    """Everything ``kyo check-command`` reads out of ``kyosei/config.yml``, read once.

    Two independent declarations, in one value because they are answered by ONE config
    read on every Bash call an agent makes (KYO-312). Splitting them into a function
    each would have doubled that read to save nothing: both are wanted at the top of
    the same command, and neither is derivable from the other.
    """

    #: KYO-298: the command lines this project calls "the whole suite". Empty means
    #: nothing is controller-only here.
    controller_only: tuple[str, ...]
    #: KYO-312: the longest timeout a worker's Bash call may declare inside its
    #: worktree, in seconds. ``None`` means no ceiling was declared and no command is
    #: refused for being long.
    timeout_ceiling_seconds: int | None
    #: KYO-335: why the config could not be read, when it could not be. The empty policy has
    #: two causes and they are not the same fact: this project declared nothing, or this
    #: build cannot read what it declared. The first is an answer and the second is the
    #: absence of one, and saying the first for both is a confident false claim (law 6,
    #: docs/design/guards.md#kyo-335).
    unreadable: str | None = None

    @property
    def declares_nothing(self) -> bool:
        """Nothing is declared *and* we were able to find that out."""
        return (
            self.unreadable is None
            and not self.controller_only
            and self.timeout_ceiling_seconds is None
        )


def declared_command_policy(ctx: Context) -> CommandPolicy:
    """This project's command declarations, or the empty policy — never an exception.

    Read from ``kyosei/config.yml`` directly rather than through
    :meth:`Context.store`, and that is a cost decision with a correctness side: this
    runs on EVERY Bash call an agent makes, a YAML read is a few milliseconds where
    opening the database is not, and the overwhelmingly common answer is "this
    project declared nothing" — which must be reachable without touching a database
    that, in a worker's worktree under the in-repo layout, does not exist at all
    (``kyosei.db`` is gitignored).

    A config that will not load yields the empty policy: a blocking hook is the wrong
    place to surface a malformed config, `kyo doctor` and every other command already
    refuse it loudly, and the failure direction that matters here is refusing a command
    the project never declared.

    KYO-335: it yields the empty policy *carrying the reason*. Refusing nothing stays
    exactly right; the caller must still be able to tell this apart from a project that
    declares nothing (docs/design/guards.md#kyo-335).
    """
    empty = CommandPolicy(controller_only=(), timeout_ceiling_seconds=None)
    if ctx.layout is None or not ctx.layout.configured:
        return empty
    try:
        config = load_config(ctx.root, code_root=ctx.code_root)
    except Exception as exc:  # noqa: BLE001 - a hook must never die on a config it cannot read
        return CommandPolicy(
            controller_only=(), timeout_ceiling_seconds=None, unreadable=str(exc)
        )
    return CommandPolicy(
        controller_only=tuple(config.controller_only_commands),
        timeout_ceiling_seconds=config.worker_command_timeout_ceiling_seconds,
    )


# ------------------------------------------------------------------- KYO-312
#
# The timeout ceiling — a worker waits in slices, never in one loop.
#
# WHAT IS BEING REFUSED, and it is not a long command. It is a long command a worker
# DECLARED it would wait through: the `PreToolUse` payload carries the Bash tool's own
# `timeout`, so the intent is legible before the wait begins, which is the only moment
# at which refusing it saves anything. A call that declares no timeout runs under the
# harness's default and is never refused here — the guard reads a declaration, it does
# not predict a duration, and a `make test` that will take twenty minutes under the
# default timeout is KYO-298's business rather than this one's.
#
# WHY A WORKER AND NOT A CONTROLLER. A dispatched worker has no way to be idle except a
# foreground command, so "run it detached and look once, at about the time it should be
# done" — which is what the rule itself used to say — IS a foreground wait as long as
# the run. The cache expires in it and the next turn re-writes the whole context at the
# cache-write price. A controller's long commands are a different trade (the suite is
# its job, once per wave), which is why the predicate below is "inside a running
# dispatch's worktree" exactly as KYO-298's is.
#
# WHY THE NUMBER IS THE PROJECT'S. The cost exists only because the prompt cache
# expires, and how long that cache lives belongs to the consumer's harness. There is no
# shipped default and the check does not exist until a number is declared.


def _duration(seconds: float) -> str:
    """``240`` -> ``"240s (4.0 min)"``. Both units, because the refusal is an argument.

    The payload speaks milliseconds, the config key speaks seconds, and the rule the
    refusal is defending speaks minutes. A message that picked one of the three would
    make the reader do the conversion that the disagreement turns on.
    """
    return f"{seconds:.0f}s ({seconds / 60:.1f} min)"


def _payload_timeout_ms(raw: str) -> int | None:
    """The timeout a `PreToolUse` payload's Bash call DECLARES, in milliseconds.

    ``None`` for every payload that does not carry one, which is the common case and the
    allowed one: a call with no declared timeout runs under the harness's own default,
    the agent has asserted nothing about how long it will wait, and there is nothing here
    to refuse. Read beside :func:`kyosei.cmd_publish.command_in_pre_tool_use_payload`
    rather than through it because that reader answers a different question (*what* is
    being run) and returning two unrelated values from it would make every caller of the
    older one carry the newer one's shape.

    A bool is excluded before the numeric test for the reason
    :func:`kyosei.config._parse_timeout_ceiling_seconds` excludes it: ``True`` is an
    ``int`` in Python, and a payload carrying one would otherwise read as a one-
    millisecond wait.
    """
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    value = tool_input.get("timeout")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value > 0 else None


def command_run_directories(command: str, *, cwd: Path | None) -> list[Path]:
    """Every directory this command line would actually run something in, in order.

    THE DELIBERATE DIFFERENCE from :func:`controller_only_matches`, which follows the
    same ``cd``s over the same segments. There the question is per-segment — *this*
    command, in *that* directory — because only the segment that runs the suite is
    refused. A timeout belongs to the whole tool call: one number covers every segment,
    and the wait it pays for is the worker's wherever the shell happens to be standing.
    So the starting directory counts even when the line immediately ``cd``s out of it (a
    worker that types ``cd /tmp && <long thing>`` from inside its worktree is still that
    worker, still waiting, still re-writing its own context), and each segment's
    directory counts too (the common shape is a payload cwd in the CONTROLLER's checkout
    and a ``cd "<worktree>" &&`` in front of the command).

    A ``cd`` this parser cannot follow leaves the place unknown, and an unknown place is
    omitted rather than guessed at — :func:`kyosei.shell_words.moved`'s reasoning, unchanged: a
    blocking hook that guesses where a command will run is the one that gets disabled.
    """
    places: list[Path] = [] if cwd is None else [cwd]
    for step in shell_words.walk(command, cwd):
        if step.invocation.argv and step.cwd is not None:
            places.append(step.cwd)
    seen: list[Path] = []
    for place in places:
        if place not in seen:
            seen.append(place)
    return seen


def command_sets_override(command: str, name: str) -> bool:
    """Does this command line set ``name`` to something other than empty or ``0``?

    The whole of KYO-312's override, and it reads the line rather than the environment
    for :data:`kyosei.contracts.SUITE_OVERRIDE_ENV`'s reason exactly: the hook is a fresh
    process, so an ``export`` in an earlier Bash call is invisible to it, and a prefix is
    what a transcript records.

    Both shells count — a prefix (``KYOSEI_LONG_OK=1 sleep 600``) and an assignment
    segment (``KYOSEI_LONG_OK=1; until ...; do sleep 30; done``) — and the second is not
    a nicety: no env prefix may precede a compound command in a POSIX shell, and a
    compound command is exactly the shape a long wait takes. An override that could not
    be typed in front of the command it is about would be an override in name only.
    """
    return any(
        step.invocation.env.get(name, "") not in ("", "0")
        for step in shell_words.walk(command, None)
    )


def timeout_refusal_lines(
    command: str,
    *,
    timeout_ms: int,
    ceiling_seconds: int,
    place: Path,
    worktree: RunningWorktree,
) -> list[str]:
    """What the worker is told: the rule, the measurement, the remedy, the way past.

    :func:`refusal_lines`'s shape, and the remedy is held to the same standard (KYO-312.c2
    asks for "a test proving the remedy clears it"): the sliced wait this message spells
    out is run back through this same guard, in the same worktree, in
    ``tests/test_kyo298_command_guard.py``, and comes back clean.

    The measurement is carried rather than asserted for KYO-298's reason. This refusal
    asks a worker to do something more awkward than what it typed; the only honest basis
    for that is the number, and a number that lives in a controller's transcript instead
    of in the message is one the worker cannot check.
    """
    return [
        f"this Bash call declares a timeout of {_duration(timeout_ms / 1000)}, over the "
        f"{_duration(ceiling_seconds)} ceiling this project declares in kyosei/config.yml "
        f"as `worker_command_timeout_ceiling_seconds`",
        f"  where: {place} — inside {worktree.directory}, the worktree declared by "
        f"{worktree.dispatch_id} ({worktree.record_id}), whose state is running",
        "  why: you have no way to be idle except a foreground command, so waiting ONCE "
        "for a long run IS a long foreground command. Your prompt cache expires inside it "
        "— a subagent gets five minutes unless its operator chose otherwise — and your "
        "next turn re-writes your whole context at the cache-WRITE price instead of "
        "reading it at a tenth. Measured on one dispatch whose brief stated the rule in "
        "prose: two waits of 10.1 and 6.1 minutes in single `until ...; do "
        "sleep 30; done` loops, 250,466 and 255,899 tokens re-written",
        "  remedy: wait in SLICES. Detach the run with its output to a file (`nohup "
        "<command> > /tmp/run.log 2>&1 &`), then take one bounded look at a time, each "
        "under the ceiling: `sleep 180; cat /tmp/run.status`, and again if it is not "
        "done. Each look is a cache READ, about a tenth of the input price, and it keeps "
        "the cache alive; one expiry is a full cache WRITE at 1.25x. Slices are about ten "
        "times cheaper than one wait, for any run up to roughly fifty minutes. A call "
        "that declares no timeout at all is not refused here",
        f"  override: re-run the call with `{LONG_COMMAND_OVERRIDE_ENV}=1` in front of the "
        f"command line — `{LONG_COMMAND_OVERRIDE_ENV}=1 <command>`, or "
        f"`{LONG_COMMAND_OVERRIDE_ENV}=1; <command>` when the line is a compound command "
        "no prefix may precede — and it is allowed. If you use it, say so in your report's "
        "`disagreements[]` — that is what makes the override evidence rather than a bypass",
    ]


def cmd_check_command(ctx: Context, args: Namespace, raw: str | None = None) -> Output:
    """``kyo check-command [--pre-tool-use] [COMMAND]`` — KYO-298, KYO-312.

    Bare, it answers the question about a command line you hand it, from the
    directory you are standing in, and exits 2 if that command would be refused —
    which is what makes the behaviour testable by hand and what lets a controller see
    exactly what a worker would be told. ``--timeout-ms`` states by hand what the
    payload states for itself.

    ``--pre-tool-use`` is how the packaged hook invokes it: the payload arrives on
    stdin, and the command, the cwd and the call's own declared timeout are read from
    it rather than from this process's own environment.

    TWO refusals, and a project opts into each one separately (KYO-312.c2): the
    command is the whole suite (``controller_only_commands``), or the call declares it
    will wait longer than this project's ceiling
    (``worker_command_timeout_ceiling_seconds``). The suite is checked first, because
    when both apply the honest thing to tell a worker is that the run was not its to
    make at all — the wait is the consequence, not the cause.

    **Silent unless it fires.** Every Bash call an agent makes reaches this hook.
    Anything that is not a declared command, anywhere that is not a running
    dispatch's worktree, in a project that declared nothing, is none of its business
    — and saying so on each one would bury the one message that matters. That is
    `kyo check-push`'s lesson (KYO-33) and it is repeated here because this hook is on
    a far busier path than that one.

    ``raw`` is the ``--pre-tool-use`` payload when the caller has already read it
    (KYO-422, :func:`cmd_pre_tool_use`); omitted, it is read from stdin here.
    """
    policy = declared_command_policy(ctx)
    declared = policy.controller_only
    ceiling = policy.timeout_ceiling_seconds

    if getattr(args, "pre_tool_use", False):
        if raw is None:
            raw = "" if sys.stdin is None or sys.stdin.closed else sys.stdin.read()
        command = command_in_pre_tool_use_payload(raw)
        cwd = _payload_cwd(raw)
        timeout_ms = _payload_timeout_ms(raw)
    else:
        command = getattr(args, "command_line", None)
        cwd = Path.cwd().resolve()
        timeout_ms = getattr(args, "timeout_ms", None)

    data: dict = {
        "ok": True,
        "command": "check-command",
        "declared": list(declared),
        "timeout_ceiling_seconds": ceiling,
        "checked": command,
        "timeout_ms": timeout_ms,
        "cwd": str(cwd) if cwd is not None else None,
        "matched": False,
        "matches": [],
        "over_ceiling": False,
        "run_directories": [],
        "overridden": False,
        "blocks": False,
        "running_worktrees": [],
        "store_unreadable": None,
        "config_unreadable": policy.unreadable,
    }

    if policy.unreadable is not None:
        # KYO-335.c3, and the only hook line here that is NOT reached through
        # `cli.main`'s catch: this command swallows the config refusal on purpose (a
        # guard on every Bash call must not die on one), so the sentence has to be
        # said where the swallowing happens. Exit 0 and refuse nothing, unchanged —
        # what changes is that the exit no longer claims the project declared
        # nothing.
        return Output(
            [
                "check-command: NOTHING WAS CHECKED — this `kyo` cannot read "
                f"kyosei/config.yml ({policy.unreadable}), so whether this project "
                "declares `orchestrator_only_commands` (or its pre-KYO-519 spelling "
                "`controller_only_commands`) or a "
                "`worker_command_timeout_ceiling_seconds` is not known. Nothing is "
                "refused, and exit 0 is NOT evidence that nothing is declared"
            ],
            data,
        )
    if policy.declares_nothing:
        # KYO-298.c1 and KYO-312.c2: the opt-out, and the reason this command is cheap
        # enough to sit on every Bash call. Nothing is parsed, no store is opened.
        return Output(
            [
                "check-command: this project declares no `orchestrator_only_commands` "
                "(nor its pre-KYO-519 spelling `controller_only_commands`) and "
                "no `worker_command_timeout_ceiling_seconds` in kyosei/config.yml, so "
                "nothing is orchestrator-only here, no command is too long, and nothing is "
                "refused"
            ],
            data,
        )
    if not command:
        return Output(
            ["check-command: no Bash command to check; nothing checked"], data
        )

    matches = controller_only_matches(command, declared, cwd=cwd) if declared else []
    data["matches"] = [match.as_json() for match in matches]
    data["matched"] = bool(matches)
    over_ceiling = ceiling is not None and timeout_ms is not None and timeout_ms > ceiling * 1000
    data["over_ceiling"] = over_ceiling
    if not matches and not over_ceiling:
        return Output(
            [
                "check-command: not an orchestrator-only command"
                + (
                    ""
                    if ceiling is None
                    else (
                        ", and it declares no timeout"
                        if timeout_ms is None
                        else f", and its {_duration(timeout_ms / 1000)} timeout is within "
                        f"the {_duration(ceiling)} ceiling"
                    )
                )
                + "; nothing checked"
            ],
            data,
        )

    # Only now is the store opened: a matching command is rare, and this runs on
    # every Bash call an agent makes.
    worktrees, unreadable = running_worktrees(ctx)
    data["store_unreadable"] = unreadable
    data["running_worktrees"] = [
        {
            "dispatch_id": worktree.dispatch_id,
            "record_id": worktree.record_id,
            "stored": worktree.stored,
            "directory": str(worktree.directory),
        }
        for worktree in worktrees
    ]
    if unreadable is not None:
        return Output(
            [
                f"check-command: NOTHING WAS CHECKED — {unreadable}, so this run cannot "
                "say whether you are inside a running dispatch's worktree. Exit 0 is "
                "NOT evidence that you are not",
            ],
            data,
        )

    for match in matches:
        if match.cwd is None:
            continue
        for worktree in worktrees:
            if not _is_inside(match.cwd, worktree.directory):
                continue
            if match.overridden:
                data["overridden"] = True
                return Output(
                    [
                        f"check-command: allowed by {SUITE_OVERRIDE_ENV} — `{match.declared}` "
                        f"is orchestrator-only and you are inside {worktree.dispatch_id}'s "
                        f"worktree, and you said so deliberately. Report it in your "
                        f"report's `disagreements[]`",
                    ],
                    data,
                )
            data["ok"] = False
            data["blocks"] = True
            raise GuardRefusal(
                CONTRACT_SUITE_IS_THE_CONTROLLERS,
                refusal_lines(match, worktree, declared),
                data=data,
            )

    # KYO-312, and only now: the suite refusal above is the more specific claim, so a
    # `make test` with a two-hour timeout is refused as the suite rather than as a long
    # wait. Reaching here means it was neither the suite nor inside a worktree.
    if over_ceiling and ceiling is not None and timeout_ms is not None:
        places = command_run_directories(command, cwd=cwd)
        data["run_directories"] = [str(place) for place in places]
        for place in places:
            for worktree in worktrees:
                if not _is_inside(place, worktree.directory):
                    continue
                if command_sets_override(command, LONG_COMMAND_OVERRIDE_ENV):
                    data["overridden"] = True
                    return Output(
                        [
                            f"check-command: allowed by {LONG_COMMAND_OVERRIDE_ENV} — this "
                            f"call declares {_duration(timeout_ms / 1000)}, over the "
                            f"{_duration(ceiling)} ceiling, and you are inside "
                            f"{worktree.dispatch_id}'s worktree, and you said so "
                            f"deliberately. Report it in your report's `disagreements[]`",
                        ],
                        data,
                    )
                data["ok"] = False
                data["blocks"] = True
                raise GuardRefusal(
                    CONTRACT_LONG_COMMAND_OUTLIVES_THE_CACHE,
                    timeout_refusal_lines(
                        command,
                        timeout_ms=timeout_ms,
                        ceiling_seconds=ceiling,
                        place=place,
                        worktree=worktree,
                    ),
                    data=data,
                )

    reason = (
        "no dispatch is running here"
        if not worktrees
        else "not inside any running dispatch's worktree"
    )
    subject = (
        f"`{matches[0].declared}` is orchestrator-only"
        if matches
        else f"this call declares {_duration((timeout_ms or 0) / 1000)}, over this "
        f"project's {_duration(ceiling or 0)} ceiling"
    )
    return Output([f"check-command: {subject}, but {reason} — allowed"], data)


# ------------------------------------------------------------------------- KYO-422
#
# ONE INTERPRETER FOR BOTH PreToolUse GUARDS. `kyo check-push --pre-tool-use` and `kyo
# check-command --pre-tool-use` are two hook groups, so every Bash call an agent makes
# starts two interpreters before either does any work (docs/design/guards.md#kyo-422). This
# reads the payload once and hands the same text to both.
#
# OPT-IN IN THIS BUILD, and the packaged fragment still names the two commands. A consumer's
# installed `kyo` changes only when it is upgraded (KYO-335), and a hook naming a subcommand that
# build does not know fails on every Bash call as a non-blocking error — both guards silent at once,
# the blinded-hook class KYO-325 measured. Switching the default is a separate change: the fragment
# plus a `install_hooks.SUPERSEDED_HOOK_COMMANDS` fold of the two old groups into one, after a
# release has shipped this command.


def _pre_tool_use_lines_worth_saying(output: Output) -> list[str]:
    """What one guard's clean return says that a hook reader must still see.

    Silence is the success on this path, as it is for each guard's own hook. Three
    clean returns are not success, and `kyo check-command` says each in a line: it
    could not read the config or the store (NOTHING WAS CHECKED, KYO-335.c3), or a
    worker overrode a refusal deliberately and is asked to report it.
    """
    data = output.data
    if data.get("config_unreadable") or data.get("store_unreadable") or data.get("overridden"):
        return list(output.lines)
    return []


def cmd_pre_tool_use(ctx: Context, args: Namespace) -> Output:
    """``kyo pre-tool-use`` — both PreToolUse guards against one payload (KYO-422).

    Reads Claude Code's PreToolUse payload from stdin ONCE and runs
    :func:`kyosei.cmd_publish.cmd_check_push` and :func:`cmd_check_command` against
    it, each exactly as its own ``--pre-tool-use`` hook runs it; neither decision is
    re-implemented here. Exits 2 when either refuses, with that refusal's own text;
    when both refuse, both texts, the publication refusal first as the packaged
    groups order them. Silent and exit 0 when neither fires.

    check-command runs first because it never raises anything but its refusal (it
    swallows an unreadable config or store into a NOTHING WAS CHECKED line). A
    check-push failure that is not a refusal — a store it cannot measure — is re-raised
    unchanged when check-command did not refuse, so `cli.main` treats it exactly as it
    treats the separate hook's; when check-command DID refuse, the refusal blocks and
    the failure travels in its text rather than letting a broken store unblock a
    command the other guard refused.
    """
    raw = "" if sys.stdin is None or sys.stdin.closed else sys.stdin.read()
    guard = Namespace(
        pre_tool_use=True, advisory=False, acknowledge=False, command_line=None, timeout_ms=None
    )
    checks: dict[str, dict] = {}
    lines: list[str] = []
    command_refusal: GuardRefusal | None = None
    try:
        command_output = cmd_check_command(ctx, guard, raw=raw)
    except GuardRefusal as refusal:
        command_refusal = refusal
        checks["check-command"] = refusal.data
    else:
        checks["check-command"] = command_output.data
        lines.extend(_pre_tool_use_lines_worth_saying(command_output))

    push_refusal: GuardRefusal | None = None
    push_failure: str | None = None
    try:
        push_output = cmd_check_push(ctx, guard, raw=raw)
    except GuardRefusal as refusal:
        push_refusal = refusal
        checks["check-push"] = refusal.data
    except KyoseiError as error:
        if command_refusal is None:
            raise
        push_failure = f"check-push could not check this call: {error}"
        checks["check-push"] = {"ok": False, "error": str(error)}
    else:
        checks["check-push"] = push_output.data

    refusals = [r for r in (push_refusal, command_refusal) if r is not None]
    data = {
        "ok": not refusals,
        "command": "pre-tool-use",
        "blocks": bool(refusals),
        "refused_by": [
            name
            for name, refusal in (("check-push", push_refusal), ("check-command", command_refusal))
            if refusal is not None
        ],
        "checks": checks,
    }
    if refusals:
        raise GuardRefusal(
            " + ".join(refusal.contract for refusal in refusals),
            [
                *(item for refusal in refusals for item in refusal.unmet),
                *([push_failure] if push_failure else []),
            ],
            data=data,
        )
    return Output(lines, data)
