"""``kyosei`` / ``kyo`` — argument parsing and process exit codes.

Two console scripts, one entry point: ``kyo`` is a blessed alias, never a subset.

Note what this parser deliberately does *not* offer: any flag that accepts a
timestamp (law 16 — the tool stamps time), and any flag that accepts multi-line
prose as a value (law 5 — prose arrives by file or stdin). ``tests/`` asserts both
by inspecting the parser, so a future flag cannot quietly reintroduce either.

What it *does* offer, and what law 5's own standing rule spent a milestone denying,
is a handful of flags carrying **one-line** text as a shell argument. Each one is
declared by carrying :data:`PROSE_ARGUMENT_METAVAR` as its metavar, so the set is a
readable property of the shipped parser (``kyo wont-do --help`` now says
``--why TEXT``) instead of a list maintained by hand in prose — see
:func:`prose_argument_flags` and KYO-86.
"""

from __future__ import annotations

import argparse
import difflib
import functools
import json
import os
import pathlib
import sqlite3
import sys
import traceback
from collections.abc import Callable, Sequence

from . import changelog as changelog_module
from . import (
    cmd_accept,
    cmd_brief,
    cmd_check_branch,
    cmd_check_build,
    cmd_check_clean,
    cmd_check_command,
    cmd_close_wave,
    cmd_confirm,
    cmd_controller,
    cmd_dispatch,
    cmd_evidence,
    cmd_flags,
    cmd_harness,
    cmd_install_agents,
    cmd_links,
    cmd_next,
    cmd_onboard,
    cmd_projects,
    cmd_publish,
    cmd_records,
    cmd_redact,
    cmd_sessions,
    cmd_store,
    cmd_sweep,
    cmd_views,
    cmd_worktrees,
    commands,
    events_ledger,
    explanations,
    prior_art,
    query,
    report_ingest,
    touch_drift,
)
from . import serve as serve_module
from . import store as store_module
from .commands import PROJECT_NONE, PROJECT_OPTIONAL, PROJECT_REQUIRED
from .contracts import (
    CLAIM_LEVELS,
    CRITERION_STATES,
    GATE_TYPES,
    KNOWLEDGE_STANDINGS,
    LINK_TYPES,
    OBLIGATION_VERDICTS,
    VERIFICATION_METHODS,
    VERIFY_VERDICTS,
    default_ladder,
)
from .errors import EXIT_ERROR, EXIT_OK, EXIT_USAGE, GuardRefusal, KyoseiError
from .prose import (
    check_not_shell_mangled,
    path_warning_line,
    pending_path_warnings,
    reset_path_warnings,
    shell_residue_warning,
)
from .registry import GATE_TAG_FILTER_FLAG, GATE_TAG_FLAG

PROG = "kyo"

#: Set to anything but empty or ``0`` and an unexpected exception prints its whole
#: traceback above the one-line report (KYO-421.c2). Named in that line, so the way
#: back to the traceback is on the screen at the moment it is wanted.
TRACEBACK_ENV = "KYOSEI_TRACEBACK"

Handler = Callable[[commands.Context, argparse.Namespace], commands.Output]

#: name -> (handler, the command's relationship to a project — one of
#: ``commands.PROJECT_REQUIRED`` / ``PROJECT_NONE`` / ``PROJECT_OPTIONAL``)
#:
#: KYO-57: this table is also the seam the implementations are split along. Each
#: ``cmd_*`` module below is one subject, so a record about (say) linking names
#: ``cmd_links.py`` in its touches and a record about promotion names
#: ``cmd_records.py`` — and `kyo dispatch`'s disjointness check has something to
#: answer with other than "collides".
#: KYO-519 (KYO-513): the spellings `kyo orchestrator` is also accepted under. Kept
#: permanently: the desktop app runs `kyo controller ...` on the wire (KYO-513), and an
#: app snapshot in the field cannot be upgraded by a kyo release. Each spelling is a key
#: of :data:`COMMAND_TABLE` and of ``serve.NOT_SERVED``.
ORCHESTRATOR_COMMAND_ALIASES: tuple[str, ...] = ("controller",)

COMMAND_TABLE: dict[str, tuple[Handler, str]] = {
    "init": (commands.cmd_init, PROJECT_NONE),
    "new": (cmd_records.cmd_new, PROJECT_REQUIRED),
    "promote": (cmd_records.cmd_promote, PROJECT_REQUIRED),
    "spawn": (cmd_records.cmd_spawn, PROJECT_REQUIRED),
    "show": (cmd_views.cmd_show, PROJECT_REQUIRED),
    "edit": (cmd_records.cmd_edit, PROJECT_REQUIRED),
    "restate": (cmd_records.cmd_restate, PROJECT_REQUIRED),
    # KYO-603: the typed move regenerates the declared documents when it enters or
    # leaves the completion status; composed moves (accept, confirm, close-wave) call
    # `cmd_records.cmd_move` directly and regenerate once for the whole act.
    "move": (cmd_close_wave.cmd_move, PROJECT_REQUIRED),
    "wont-do": (cmd_records.cmd_wont_do, PROJECT_REQUIRED),
    "void": (cmd_records.cmd_void, PROJECT_REQUIRED),
    "link": (cmd_links.cmd_link, PROJECT_REQUIRED),
    "unlink": (cmd_links.cmd_unlink, PROJECT_REQUIRED),
    "gate": (cmd_links.cmd_gate, PROJECT_REQUIRED),
    # KYO-130: its own verb rather than a `kyo gate --retag` mode, on the `restate`
    # precedent — a command addressing a child object by the child's own id is a verb
    # of its own here (`restate <criterion-id>`, `unlink <link-id>`, `ungate
    # <gate-id>`). The mode flag would also have to make `gate`'s two required
    # positionals optional to be expressible, which trades argparse's arity check on
    # the creation path for three hand-written refusals.
    "retag": (cmd_links.cmd_retag, PROJECT_REQUIRED),
    # KYO-148: `retag` corrects a gate's classification, `redo` corrects its payload —
    # the two things about a gate that can be wrong without the gate having cleared.
    # Named after the flag it rewrites exactly as `retag` is (`--tag` -> `retag`,
    # `--do` -> `redo`), and its own verb for the reason recorded on `retag` directly
    # above, which is a rule about child-object commands rather than about tags.
    "redo": (cmd_links.cmd_redo, PROJECT_REQUIRED),
    "ungate": (cmd_links.cmd_ungate, PROJECT_REQUIRED),
    "claim": (cmd_evidence.cmd_claim, PROJECT_REQUIRED),
    "verify": (cmd_evidence.cmd_verify, PROJECT_REQUIRED),
    "attach": (cmd_evidence.cmd_attach, PROJECT_REQUIRED),
    # KYO-585: prior-art search through recx, on demand, for any record.
    "prior-art": (prior_art.cmd_prior_art, PROJECT_REQUIRED),
    # KYO-586: records the project's choice in kyosei/config.yml (`recx.prior_art.state`).
    # Not in MUTATING_COMMANDS: it writes the config file, never the store's tables.
    "prior-art-setting": (prior_art.cmd_prior_art_setting, PROJECT_REQUIRED),
    "redact": (cmd_redact.cmd_redact, PROJECT_REQUIRED),
    "list": (cmd_views.cmd_list, PROJECT_REQUIRED),
    # KYO-108: the saved views. Read-only in both actions — `base run` is
    # `list`'s own engine (`kyosei.query`) plus the arrangement a rendering needs.
    "base": (cmd_views.cmd_base, PROJECT_REQUIRED),
    "map": (cmd_views.cmd_map, PROJECT_REQUIRED),
    "ideas": (cmd_views.cmd_ideas, PROJECT_REQUIRED),
    # KYO-184: PROJECT_OPTIONAL, and that is the whole point of the command. Its
    # subject is "where is this project, and can this build open its store", so a
    # directory with no project is an ANSWER (`layout: "unconfigured"`) rather than
    # the failure PROJECT_REQUIRED would make of it — and so is a store this build
    # cannot open, which is why `where` catches the schema refusal and carries its
    # payload rather than raising it. Not in MUTATING_COMMANDS: it opens the store in
    # order to answer whether the store opens, runs no transaction, and writes
    # nothing — no record, no events row, no export.
    "where": (cmd_store.cmd_where, PROJECT_OPTIONAL),
    "export": (cmd_store.cmd_export, PROJECT_REQUIRED),
    "rebuild": (cmd_store.cmd_rebuild, PROJECT_REQUIRED),
    "doctor": (cmd_store.cmd_doctor, PROJECT_REQUIRED),
    "dispatch": (cmd_dispatch.cmd_dispatch, PROJECT_REQUIRED),
    "report": (report_ingest.cmd_report, PROJECT_REQUIRED),
    # KYO-149.c1: the schema `report` validates against, printed from the installed
    # package. PROJECT_NONE and not PROJECT_OPTIONAL: this answers a question about
    # the *tool*, not about any project, and its reader is a worker standing in a
    # dispatch worktree that may have no store above it at all. Searching upward
    # could only find something irrelevant or fail — and failing here is the wall
    # this record exists to remove. Not in MUTATING_COMMANDS: it writes nothing.
    "report-schema": (report_ingest.cmd_report_schema, PROJECT_NONE),
    # KYO-331: the one act that applies a report you have read — `kyo verify
    # --from-report`, `kyo docs settle --from-report` and `kyo edit
    # --touches-from-drift`, each reached through this table's own handler, plus an
    # opt-in `--close`. Its own module on `cmd_*`'s own seam rule (KYO-57): its subject
    # is the acceptance sequence, which spans four of the modules below and belongs in
    # none of them.
    "accept": (cmd_accept.cmd_accept, PROJECT_REQUIRED),
    # KYO-494 (KYO-481's design): "the maintainer looked and approves" — `kyo verify`,
    # `kyo attach`, `kyo ungate` and `kyo move` composed through this table's own
    # handlers, the way `accept` composes its steps. Its own module on the same seam rule.
    "confirm": (cmd_confirm.cmd_confirm, PROJECT_REQUIRED),
    # KYO-536: a merged wave's closing sequence — the moves through `kyo move`'s own
    # handler, then the project's declared generators, a commit, and its after-steps.
    # Its own module on the same seam rule: the sequence spans the store and git.
    "close-wave": (cmd_close_wave.cmd_close_wave, PROJECT_REQUIRED),
    "split": (report_ingest.cmd_split, PROJECT_REQUIRED),
    # KYO-70: read-only by default, and it never writes to the store even with
    # `--prune` — what it removes is a directory in the filesystem, not a row.
    "worktrees": (cmd_worktrees.cmd_worktrees, PROJECT_REQUIRED),
    # KYO-454: what the desktop app read by SQL and git, as read-only
    # commands `kyo serve` serves. None is in MUTATING_COMMANDS: each is a projection of
    # the store's own tables (`kyosei.query`), opens no transaction and writes no row.
    "briefs": (cmd_views.cmd_briefs, PROJECT_REQUIRED),
    "shift-notes": (cmd_views.cmd_shift_notes, PROJECT_REQUIRED),
    "gates": (cmd_views.cmd_gates, PROJECT_REQUIRED),
    "activity": (cmd_worktrees.cmd_activity, PROJECT_REQUIRED),
    "touch-drift": (touch_drift.cmd_touch_drift, PROJECT_REQUIRED),
    "sweep": (cmd_sweep.cmd_sweep, PROJECT_REQUIRED),
    "flag": (cmd_flags.cmd_flag, PROJECT_REQUIRED),
    "next": (cmd_next.cmd_next, PROJECT_REQUIRED),
    "brief": (cmd_brief.cmd_brief, PROJECT_REQUIRED),
    "note": (cmd_brief.cmd_note, PROJECT_REQUIRED),
    "docs": (cmd_views.cmd_docs, PROJECT_REQUIRED),
    # KYO-138.c1: the command-and-option reference, generated from the parser below
    # rather than written. PROJECT_NONE on `report-schema`'s reasoning exactly: this
    # answers a question about the *tool*, and its reader is often a worker or a new
    # consumer standing somewhere that has no store above it. Searching upward could
    # only find something irrelevant or fail, and failing is the wall this removes.
    # Not in MUTATING_COMMANDS: it writes nothing to the store, and `--output` writes a
    # file in the filesystem rather than a row in the store.
    "reference": (cmd_views.cmd_reference, PROJECT_NONE),
    # KYO-226: which build is this, and what changed since it. Both PROJECT_NONE on
    # `reference`'s reasoning exactly — they answer a question about the *tool*, and
    # their reader is a consumer who has just installed it or a worker standing in a
    # worktree with no store above it. Neither is in MUTATING_COMMANDS: they write no
    # record, no event and no export, and `changelog --output` writes a file in the
    # filesystem rather than a row in the store.
    "version": (cmd_views.cmd_version, PROJECT_NONE),
    "changelog": (cmd_views.cmd_changelog, PROJECT_NONE),
    # KYO-54.c2: the two worktree guards are the only commands that run
    # usefully with no project configured at all — see PROJECT_OPTIONAL.
    "check-branch": (cmd_check_branch.cmd_check_branch, PROJECT_OPTIONAL),
    "check-clean": (cmd_check_clean.cmd_check_clean, PROJECT_OPTIONAL),
    # KYO-398.c6: PROJECT_OPTIONAL on `check-command`'s reasoning, sharper still — it
    # runs from a `UserPromptSubmit` hook on EVERY prompt, in every checkout, and a
    # checkout with no project must get silence rather than a hook error per prompt.
    # Not in MUTATING_COMMANDS: it opens the database read-only and writes nothing.
    "check-build": (cmd_check_build.cmd_check_build, PROJECT_OPTIONAL),
    # KYO-298: the third worktree guard, and PROJECT_OPTIONAL for a sharper version
    # of the same reason — it runs from a `PreToolUse` hook on EVERY Bash call an
    # agent makes, in every checkout that agent visits, and a project that configured
    # no store (or declared neither `controller_only_commands` nor KYO-312's
    # `worker_command_timeout_ceiling_seconds`) must get "nothing is controller-only
    # here" rather than a failed tool call.
    "check-command": (cmd_check_command.cmd_check_command, PROJECT_OPTIONAL),
    # KYO-60: also PROJECT_OPTIONAL, and for the same reason — it runs from a
    # `PreToolUse` hook on every Bash call, including in a checkout that has no
    # Kyosei project at all, where the honest answer is "there is no store here to
    # publish" rather than a failed tool call.
    "check-push": (cmd_publish.cmd_check_push, PROJECT_OPTIONAL),
    # KYO-422: both PreToolUse guards in one interpreter, PROJECT_OPTIONAL because both
    # of them are. Not in MUTATING_COMMANDS: neither guard writes anything, and
    # `check-push --acknowledge` (the one write either has) is not reachable from here.
    "pre-tool-use": (cmd_check_command.cmd_pre_tool_use, PROJECT_OPTIONAL),
    # KYO-186: PROJECT_OPTIONAL on the two guards' own reasoning. It runs from
    # `SessionStart`, `Stop` and `SessionEnd`, which fire in every session the
    # harness starts — including one opened in a checkout that configured no store
    # at all, where the honest answer is "there is no store here to record a
    # session into" rather than a failed hook at the top of every session. Not in
    # MUTATING_COMMANDS: it writes no record, no event and no export — the file it
    # writes is local state beside `kyosei.db`, gitignored and rebuilt by the next
    # session, exactly as the database is.
    "sessions": (cmd_sessions.cmd_sessions, PROJECT_OPTIONAL),
    "install-hooks": (commands.cmd_install_hooks, PROJECT_REQUIRED),
    "install-skill": (commands.cmd_install_skill, PROJECT_REQUIRED),
    # KYO-212: the third of the "your copy, updated" commands, and the only one that
    # merges instead of overwriting — `kyosei/procedures.md` is the project's to edit.
    # Not in MUTATING_COMMANDS: it writes a file beside the store, no record, no event
    # and no export, exactly as its two siblings do.
    "install-procedures": (commands.cmd_install_procedures, PROJECT_REQUIRED),
    # KYO-607: setup decision C4's writer. Not in MUTATING_COMMANDS: it writes
    # .claude/agents/ in the code repository, no record, no event and no export.
    "install-agents": (cmd_install_agents.cmd_install_agents, PROJECT_REQUIRED),
    # KYO-327.c1: the standing rules, in full, for the worker whose brief now carries
    # each one shortened to its title, a one-line enforced-by and one sentence. The
    # shortening is only honest if the rest is genuinely one command away, and this is
    # that command. PROJECT_OPTIONAL rather than PROJECT_REQUIRED because its reader is
    # standing in a dispatch worktree, which under the split layout has no store above
    # it — falling back to the rules this build ships, and saying so, is a better
    # answer there than a refusal. Not in MUTATING_COMMANDS: it opens no database,
    # writes no event and no export, and reads one markdown file.
    "rules": (commands.cmd_rules, PROJECT_OPTIONAL),
    # KYO-453: the loopback HTTP server over this table's own read
    # commands. PROJECT_NONE: one server answers for every project in the machine's
    # registry, named per request, so no project is resolved at start. Not in
    # MUTATING_COMMANDS: it opens every store read-only and runs only
    # `serve.SERVED_COMMANDS`.
    "serve": (serve_module.cmd_serve, PROJECT_NONE),
    # KYO-458: the app's controller launcher (KYO-383), as a command that can
    # arrive over SSH. PROJECT_NONE: its PROJECT is a positional naming an entry in the
    # machine's registry (or a directory), not the project around the caller -- over
    # SSH there is none. Not in MUTATING_COMMANDS: it starts and ends tmux sessions and
    # writes a LaunchAgent, and writes nothing to any store.
    "orchestrator": (cmd_controller.cmd_controller, PROJECT_NONE),
    # KYO-519 (KYO-513): the command's name before the rename, kept as an argparse
    # alias with no removal date -- every packed app snapshot runs `kyo controller ...`
    # on the wire, and so does every `ssh ... kyo controller attach` a person has
    # typed. argparse sets `args.command` to the spelling typed, so each spelling needs
    # its own key here.
    "controller": (cmd_controller.cmd_controller, PROJECT_NONE),
    # KYO-455: the machine's project registry and its one writer. PROJECT_NONE:
    # the registry is the store home's, not any project's, and `add` names its project
    # by path. Not in MUTATING_COMMANDS: it writes $KYOSEI_HOME/projects.json and no
    # store; not served (serve.NOT_SERVED), because kyo serve never registers projects.
    "projects": (cmd_projects.cmd_projects, PROJECT_NONE),
    # KYO-514: the build's own agent setup prompt, and `--check`, the same checklist read
    # back against existing projects. PROJECT_NONE: the prompt needs no project, and
    # `--check` resolves the project here (or every registered one) itself, reporting an
    # unconfigured directory rather than refusing it. Not in MUTATING_COMMANDS: it writes
    # nothing anywhere, and never opens a database.
    "onboard": (cmd_onboard.cmd_onboard, PROJECT_NONE),
    # KYO-594: the link to this build's Claude Code plugin and its
    # registration. PROJECT_OPTIONAL: it links and registers anywhere, and enables the
    # plugin only when a Kyosei project resolves here. Not in MUTATING_COMMANDS: it writes
    # $KYOSEI_HOME/harness/claude-code and, in a project, .gitignore, never the store;
    # `migrate` (KYO-595) also removes the copies from .claude/ and CLAUDE.md, and refuses
    # itself when no project resolves.
    "harness": (cmd_harness.cmd_harness, PROJECT_OPTIONAL),
}

#: Mutating commands must each write an events row and regenerate an export.
MUTATING_COMMANDS = (
    "new",
    "promote",
    "spawn",
    "edit",
    "restate",
    "move",
    "wont-do",
    "void",
    "link",
    "unlink",
    "gate",
    "retag",
    "redo",
    "ungate",
    "claim",
    "verify",
    "attach",
    # KYO-585: writes a claim and an evidence artifact, with an events row.
    "prior-art",
    "redact",
    "dispatch",
    "report",
    # KYO-331: `kyo accept` writes through the commands it composes — each of them
    # already on this list — so every events row it causes is written under the
    # composed command's own name, and an acceptance that applies nothing writes
    # none. It is named here for what the declaration is FOR: a caller (records mode,
    # the desktop app, `kyo reference`) asking whether this command may change the
    # store, and the answer is yes.
    "accept",
    # KYO-494: `kyo confirm` writes through the commands it composes, as `accept` does.
    "confirm",
    # KYO-536: `kyo close-wave` writes through `kyo move`, as `accept --close` does.
    "close-wave",
    "split",
    # Milestone 6 (KYO-8): each of these always writes at least one events row
    # (a per-record one for anything `sweep`/`flag resolve` touches, a global
    # record_id=NULL one for `sweep`/`brief`/`note --shift` themselves even when
    # zero records are touched) — see ``cmd_sweep.py``, ``cmd_flags.py`` and
    # ``cmd_brief.py`` for where each writes it.
    "sweep",
    "flag",
    "brief",
    "note",
    # KYO-137: `kyo docs settle` writes the verdict that clears a docs obligation, and
    # writes an events row and a fresh export like every other mutation. The command's
    # other actions (`check`, `obligations`, `preview`) are pure reads, which is why this
    # entry names the command rather than promising that every `kyo docs` invocation
    # writes. KYO-299 adds a fourth shape this entry also does not describe: `assemble`
    # writes to the CODE tree — it appends sections to documents and deletes fragment
    # files — and writes nothing to the store, no record, no event and no export. It is
    # covered here only because its parent command is.
    "docs",
)


class KyoseiParser(argparse.ArgumentParser):
    """Exits 64 on usage errors, so exit code 2 can mean exactly one thing.

    KYO-421: an unknown subcommand close to a real one (`kyo lsit`, `kyo flags`) is
    answered with the nearest names FIRST, on their own line, and the full list of
    choices after it — argparse's own message puts every command on one line with no
    hint, which at 54 commands buries the one a typo meant. The offending value is
    caught where argparse checks it (:meth:`_check_value`) rather than parsed back
    out of the message, whose quoting differs between Python releases.
    """

    #: ``(value, choices)`` of the last subcommand this parser refused, read by
    #: :meth:`error`. Set per instance; a nested parser (`kyo docs setle`) refuses
    #: through its own instance, so its suggestion is phrased with its own prog.
    _unknown_subcommand: tuple[str, list[str]] | None = None

    def _check_value(self, action: argparse.Action, value: object) -> None:
        if (
            isinstance(action, argparse._SubParsersAction)
            and action.choices is not None
            and value not in action.choices
        ):
            self._unknown_subcommand = (str(value), [str(c) for c in action.choices])
        super()._check_value(action, value)  # type: ignore[misc]

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        suggestion = self.subcommand_suggestion()
        if suggestion:
            print(f"{self.prog}: {suggestion}", file=sys.stderr)
        print(f"{self.prog}: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)

    def subcommand_suggestion(self) -> str | None:
        """``did you mean `kyo list`?`` for the refused subcommand, or ``None``.

        ``None`` when nothing was refused as a subcommand or nothing is close enough
        (difflib's default 0.6 ratio): a guess that is not near the typo is noise
        placed above the list that actually answers the question.
        """
        if self._unknown_subcommand is None:
            return None
        value, choices = self._unknown_subcommand
        matches = difflib.get_close_matches(value, choices, n=3)
        if not matches:
            return None
        named = " or ".join(f"`{self.prog} {match}`" for match in matches)
        return f"unknown command '{value}' — did you mean {named}?"


def _add_common_flags(parser: argparse.ArgumentParser, *, suppress: bool) -> None:
    """Add the flags accepted both before and after the subcommand.

    The subparser copies carry ``SUPPRESS`` defaults so that ``kyo --json show X``
    is not silently undone by the subparser's own default. The top-level copies are
    built separately rather than shared through ``parents=``, because ``parents=``
    hands the *same* action objects to every parser — setting a real default on one
    would set it on all of them, which is exactly how the value gets lost.
    """
    value_default = argparse.SUPPRESS if suppress else None
    flag_default = argparse.SUPPRESS if suppress else False
    parser.add_argument(
        "--root", default=value_default, help="project root (default: search upward)"
    )
    parser.add_argument(
        "--actor",
        default=value_default,
        help="who is making this change; falls back to $KYOSEI_ACTOR, then 'orchestrator' "
        "(rows written before KYO-519 say 'controller', so a query for the orchestrator "
        "matches both)",
    )
    parser.add_argument(
        "--json", action="store_true", default=flag_default, help="machine-readable output"
    )
    # KYO-420 built two opt-in ways to make `--json` smaller for an agent that reads
    # every byte; KYO-427 made both the default and added their opt-outs. Common flags,
    # so they work before and after the command like `--json`. Not `--compact`:
    # `kyo brief --compact` already means something else, and a common flag of that
    # name would collide.
    parser.add_argument(
        FULL_FLAG,
        action="store_true",
        default=flag_default,
        help="with --json: embed every record document a command returns whole "
        "(`record`, `records`, and the same inside `kyo accept`'s steps[].data). "
        "Without it --json replaces each by its id, kind, status and updated_at plus "
        "the fields this invocation changed (`changed_fields`, and the new value of "
        "each short scalar one) — ids and changes, not documents. `kyo show --json` is "
        "the read and always prints the whole record; `kyo show --fields` narrows it",
    )
    parser.add_argument(
        SLIM_FLAG,
        action="store_true",
        default=flag_default,
        help="with --json: accepted and changes nothing — the slim form is what --json "
        "prints by default since KYO-427; kept so scripts that pass it keep working",
    )
    parser.add_argument(
        COMPACT_JSON_FLAG,
        action="store_true",
        default=flag_default,
        help="with --json: print the envelope on one line, error envelopes included, "
        "even on a terminal (the default already does so when stdout is not a terminal)",
    )
    parser.add_argument(
        PRETTY_FLAG,
        action="store_true",
        default=flag_default,
        help="with --json: print the envelope indented, error envelopes included, even "
        "when stdout is not a terminal (the default indents only on a terminal)",
    )


#: KYO-420 / KYO-427: the global output-shape flags, named once for the parser, the
#: tests and the prose that cites them.
FULL_FLAG = "--full"
SLIM_FLAG = "--slim"
COMPACT_JSON_FLAG = "--compact-json"
PRETTY_FLAG = "--pretty"


def _positive_int(value: str) -> int:
    """argparse ``type=`` for a count that must be at least one (``kyo list --limit``).

    Zero is refused rather than read as "no limit": a listing asked for zero rows that
    printed all of them would be the flag silently not doing what it says (law 6).
    """
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, not {number}")
    return number


def _common_flags() -> argparse.ArgumentParser:
    parent = KyoseiParser(add_help=False)
    _add_common_flags(parent, suppress=True)
    return parent


#: The metavar carried by every flag whose value is one line of text a person wrote.
#:
#: KYO-86. `kyosei/procedures.md` asserted, under ``enforced-by: mechanism``, that
#: "there is no free-text CLI flag for prose" — while ten subparsers offered ``--why``
#: and four more offered ``--title``/``--do``/``--method``. The sentence was false, it
#: was typeset as enforced, and it was injected verbatim into every worker's dispatch
#: brief: law 1's failure sitting inside the ledger that exists to prevent it. A
#: consumer lost a word of a ``--why`` to shell command substitution and the store
#: recorded the remainder without complaint.
#:
#: The fix is not another hand-kept list. argparse cannot tell prose from an enum on
#: its own, so the declaration is made once, at the ``add_argument`` site, in the one
#: place that is also user-visible: the metavar printed in ``--help``. The standing
#: rule then names the same flags and `tests/test_procedures.py` compares the two, so
#: a fifth such flag makes the suite fail rather than making the rule quietly false.
PROSE_ARGUMENT_METAVAR = "TEXT"


def prose_argument_flags(parser: argparse.ArgumentParser | None = None) -> list[str]:
    """Every option in ``parser``'s tree that takes one line of prose as an argument.

    Read off the shipped parser, never listed: this is the derived half of the
    comparison in ``tests/test_procedures.py`` (KYO-86.c2), and the honest statement
    of how wide law 5's admitted hole actually is.
    """
    found: set[str] = set()
    stack = [parser if parser is not None else build_parser()]
    while stack:
        current = stack.pop()
        for action in current._actions:
            # A subparsers action carries ``name -> parser`` in ``.choices``; a plain
            # enum-choice positional carries a list of strings and is not a parser.
            if isinstance(action.choices, dict) and action.choices:
                stack.extend(action.choices.values())
            elif action.metavar == PROSE_ARGUMENT_METAVAR and action.option_strings:
                found.update(action.option_strings)
    return sorted(found)


# ------------------------------------------------ KYO-91: the residue check, wired once
#
# The check that existed reached exactly one of these flags, because it was called by
# hand from `commands._reason`, which only ever sees `--why`. `--do` — a gate's payload,
# and the flag most likely to carry a backticked command, since its whole purpose is
# describing what someone must go and do — had no check at all, and neither did
# `--title` or `--method`.
#
# Wiring it at each call site would leave the fifth such flag uncovered on the day it is
# added, which is KYO-86's whole subject one layer down. So it is wired here instead, at
# the one place every invocation passes through, and driven off the SAME declaration
# `prose_argument_flags` reads: the metavar. A flag becomes prose by carrying
# `PROSE_ARGUMENT_METAVAR`, and carrying it is what subjects it to this check. There is
# no second list to keep in step.
#
# The flags are NOT all treated identically, and c2 does not ask them to be — it asks
# that the checked set equal `prose_argument_flags()`. What differs is the response, and
# it is derived rather than listed: a flag the same subparser also offers a
# `--<name>-file` form of is REFUSED, because the message can name a way to succeed that
# the shell never touches. A flag with no such form is WARNED about and stored, because
# `--title` is required on `kyo new` and refusing it on a doubled space someone meant
# would make filing a record impossible with no route around it. Ship a `--title-file`
# one day and `--title` starts refusing, here, with no edit.


def _selected_parsers(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> list[argparse.ArgumentParser]:
    """The chain of parsers this invocation actually went through, root first.

    Walking the whole tree instead would have to key values by ``dest``, and two
    subcommands can spell one ``dest`` for different things: ``kyo claim --method``
    is a line of prose, ``kyo verify --method`` is an enum. Following the chain asks
    the question of the exact actions that parsed this command line.
    """
    chain = [parser]
    current = parser
    while True:
        nested = next(
            (a for a in current._actions if isinstance(a.choices, dict) and a.choices), None
        )
        if nested is None:
            return chain
        chosen = getattr(args, nested.dest, None)
        if chosen not in nested.choices:
            return chain
        current = nested.choices[chosen]
        chain.append(current)


def check_prose_arguments(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> list[str]:
    """Run KYO-91's residue check over every prose-argument flag this invocation gave.

    Raises :class:`UsageError` — before the command runs, so nothing is written — for a
    flag whose subparser also offers the file form the message names. Returns the
    warning lines for the flags that have no file form, for the caller to emit once the
    write has happened.
    """
    warnings: list[str] = []
    for current in _selected_parsers(parser, args):
        offered = {opt for action in current._actions for opt in action.option_strings}
        for action in current._actions:
            if action.metavar != PROSE_ARGUMENT_METAVAR or not action.option_strings:
                continue
            value = getattr(args, action.dest, None)
            if not isinstance(value, str):
                continue
            flag = action.option_strings[0]
            if f"{flag}-file" in offered:
                check_not_shell_mangled(value, what=flag)
            else:
                line = shell_residue_warning(value, what=flag)
                if line is not None:
                    warnings.append(line)
    return warnings


def _add_reason_flags(parser: argparse.ArgumentParser, purpose: str) -> None:
    parser.add_argument(
        "--why", metavar=PROSE_ARGUMENT_METAVAR, help=f"one-line reason {purpose}"
    )
    parser.add_argument(
        "--why-file", metavar="FILE", help="read the reason from a file, or '-' for stdin"
    )


def build_parser() -> KyoseiParser:
    common = _common_flags()
    parser = KyoseiParser(
        prog=PROG,
        description="Kyosei (共生) — structured store for ideas, work, and evidence.",
    )
    _add_common_flags(parser, suppress=False)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    p_init = sub.add_parser(
        "init",
        parents=[common],
        help="scaffold ideas/ and kyosei/ in a project; in a new project, setup starts at "
        "`kyo onboard`",
        # KYO-558.c5: an agent asked to "run kyo init" reads this and nothing else.
        description="Create (or link) this project's Kyosei store. Setup starts at `kyo "
        "onboard`, which prints the guided setup: where the store lives, the record id "
        "prefix, hooks, the skill and worker models, with `kyo init` as one of its steps. "
        "In a project with no store yet, `kyo init` needs a layout choice -- --defaults, "
        "--in-repo, --store or --adopt -- and without one it writes nothing and names the "
        "routes, the same on a terminal and off one. --explain describes the layouts and "
        "writes nothing.",
    )
    p_init.add_argument("directory", nargs="?", help="project root (default: here)")
    p_init.add_argument("--prefix", help="project id prefix, e.g. KYO (default: from dir name)")
    p_init.add_argument(
        "--defaults",
        action="store_true",
        help="take the default for every choice no other flag makes: the store in its "
        "own git repository at ~/kyosei/<project>/ and the prefix from the directory "
        "name. What a script or CI passes; `kyo onboard` is the guided route (KYO-558)",
    )
    p_init.add_argument(
        "--explain",
        action="store_true",
        help="write nothing, and print what the store layout means here: where the store "
        "lives, which prose stays out of (or goes into) the code repository, and where "
        "dispatch worktrees go. For the project's own layout when it has a store, else for "
        "the layout the other flags choose, or both layouts when none is chosen",
    )
    p_init.add_argument(
        "--in-repo",
        action="store_true",
        help="put the store INSIDE the code repository (kyosei/ and ideas/ committed "
        "alongside the code) instead of the default, which is a store of its own at "
        "~/kyosei/<project>/ in its own git repository. The right choice when records "
        "are not sensitive and you want them reviewed in the same pull request as the "
        "code; it also means ideas/, record descriptions, shift notes, evidence stored "
        "verbatim and frozen briefs all become committed prose in this repository",
    )
    p_init.add_argument(
        "--store",
        metavar="DIR",
        help="put the split store at this path instead of ~/kyosei/<project>/ "
        "($KYOSEI_HOME overrides the ~/kyosei part)",
    )
    p_init.add_argument(
        "--adopt",
        action="store_true",
        help="link the existing store at the chosen path even when kyo init cannot "
        "confirm it belongs to this code repository: its recorded code repository "
        "(root commit) differs, or cannot be compared. Without it, such a store is "
        "refused, so a repository never silently adopts another project's store "
        "that merely shares its directory name",
    )

    p_init.add_argument(
        "--no-git-init",
        action="store_true",
        help="in a directory that is in no git repository, do NOT run `git init` and "
        "commit the files kyo init wrote there; the store then records no "
        "code-repository identity until you make the repository and commit, after "
        "which the next `kyo doctor` or session-start brief records it",
    )

    p_new = sub.add_parser("new", parents=[common], help="create a record")
    p_new.add_argument("kind", help="record kind, validated against kyosei/kinds.yml")
    p_new.add_argument(
        "--title",
        metavar=PROSE_ARGUMENT_METAVAR,
        help="one-line title Optional exactly when --from supplies the title instead (KYO-330).",
    )
    p_new.add_argument("--plan", metavar="ID", help="plan record this belongs to")
    p_new.add_argument(
        "--scope",
        metavar="SCOPE",
        help="the part of the system this work belongs to, from kyosei/scopes.yml; "
        "refused if the registry does not declare it",
    )
    p_new.add_argument(
        "--since-note",
        metavar="NOTE-ID",
        help="a carried kind only: the shift note (PREFIX-Sn) that first recorded this "
        "item, so its age counts from that note's time; refused for an unknown note or "
        "a non-carried kind (KYO-498)",
    )
    p_new.add_argument(
        "--description-file",
        metavar="FILE",
        help="description from a file, or '-' for stdin (law 5: prose never as an argument)",
    )
    p_new.add_argument(
        "--criteria-file",
        metavar="FILE",
        help="one criterion per line, from a file or '-'; an empty file means zero criteria",
    )
    p_new.add_argument(
        "--touches-file",
        metavar="FILE",
        help="one touch per line ('path', 'path#symbol', 'path#+' for additions to an "
        "existing file and nothing already in it, 'dir/' for a tree this work "
        "creates or owns, or '+path' for one file it creates), relative to the project "
        "root, validated against the repo now; "
        "optional here (unlike `kyo promote`) because this is the zero-ceremony intake path",
    )
    p_new.add_argument(
        "--evidence-file",
        metavar="FILE",
        help="evidence stored with the record in the same act, verbatim with its sha256 as "
        "`kyo attach` stores it, from a file or '-'; required by a kind whose policy sets "
        "requires_evidence_links (the default `decision`: the operator's words -- who, "
        "when, where, then the words verbatim) and allowed beside --from (KYO-521)",
    )
    p_new.add_argument(
        "--from",
        dest="from_file",
        metavar="FILE",
        help="file the whole record from one markdown file instead of --title/--plan/"
        "--scope/--description-file/--criteria-file/--touches-file: front matter for "
        "title/scope/plan/touches, a '## Criteria' section (one criterion per list "
        "item), everything else the description; refused together with any of those "
        "flags (KYO-330)",
    )
    p_new.add_argument(
        "--check",
        action="store_true",
        help="with --from, validate the file (kind, scope, touches against the repo) "
        "and file nothing (KYO-330)",
    )

    p_new.add_argument(
        "--force",
        action="store_true",
        help="file the record although one with an identical title and criteria was filed "
        "in this project within the last hour (KYO-538)",
    )

    p_promote = sub.add_parser(
        "promote",
        parents=[common],
        help="promote an ideas/ file into a record; the ceremony boundary (law 13)",
    )
    p_promote.add_argument("idea_file", metavar="IDEA_FILE", help="path to a file under ideas/")
    p_promote.add_argument(
        "--section",
        metavar="HEADING",
        help="promote one section of a multi-idea file instead of the whole file, named by "
        "its markdown heading exactly as the file writes it; the rest of the file survives",
    )
    p_promote.add_argument(
        "--kind",
        required=True,
        metavar="KIND",
        help="record kind; its accepts_from must include idea",
    )
    p_promote.add_argument(
        "--title",
        metavar=PROSE_ARGUMENT_METAVAR,
        help="one-line title (default: the idea's first '# heading')",
    )
    p_promote.add_argument(
        "--criteria-file",
        metavar="FILE",
        help="one criterion per line, from a file or '-'; required when the kind's "
        "policy.requires_criteria is true",
    )
    p_promote.add_argument(
        "--touches-file",
        metavar="FILE",
        help="one touch per line ('path', 'path#symbol', 'path#+' for additions to an "
        "existing file and nothing already in it, 'dir/' for a tree this work "
        "creates or owns, or '+path' for one file it creates), relative to the project "
        "root; required for archetype: work kinds",
    )
    p_promote.add_argument(
        "--scope",
        metavar="SCOPE",
        help="the part of the system this work belongs to, from kyosei/scopes.yml. Law 13's "
        "third piece of promotion-time structure, beside criteria and touches",
    )
    p_promote.add_argument(
        "--keep",
        action="store_true",
        help="keep the idea file (or, with --section, the promoted section) instead of "
        "removing it -- the pre-KYO-228 behaviour: a stub is left in its place, pointing "
        "at the record",
    )
    p_promote.add_argument(
        "--from",
        dest="from_file",
        metavar="FILE",
        help="take title/scope/plan/criteria/touches from one markdown file instead of "
        "--title/--scope/--criteria-file/--touches-file -- the same file shape `kyo new "
        "--from` accepts; front matter for title/scope/plan/touches, a '## Criteria' "
        "section (one criterion per list item); everything else is the description, but "
        "only when it differs (whitespace aside) from the idea being promoted, which "
        "remains the description otherwise; refused together with any of "
        "--title/--scope/--criteria-file/--touches-file (KYO-330/KYO-340)",
    )
    p_promote.add_argument(
        "--check",
        action="store_true",
        help="with --from, validate the file (kind, scope, touches against the repo) "
        "and file nothing (KYO-330)",
    )

    p_promote.add_argument(
        "--force",
        action="store_true",
        help="file the record although one with an identical title and criteria was filed "
        "in this project within the last hour (KYO-538)",
    )

    p_spawn = sub.add_parser(
        "spawn",
        parents=[common],
        help="create a record of another kind from an existing one; the kind-to-kind "
        "edge the registry declares (KYO-50)",
    )
    p_spawn.add_argument("id", metavar="SOURCE_ID", help="the record this one comes from")
    p_spawn.add_argument(
        "--kind",
        required=True,
        metavar="KIND",
        help="record kind to create; its accepts_from must include the SOURCE record's kind",
    )
    p_spawn.add_argument(
        "--title",
        metavar=PROSE_ARGUMENT_METAVAR,
        help="one-line title. Required, not defaulted from the source: what a decision "
        "decides is not what the proposal proposed, and a copied title would read as the "
        "same record twice. Optional exactly when --from supplies the title instead (KYO-330).",
    )
    p_spawn.add_argument(
        "--description-file",
        metavar="FILE",
        help="description from a file, or '-' for stdin. The source's own text is "
        "referenced, never copied (it stays live and would drift); this is where anything "
        "the new record should actually say goes",
    )
    p_spawn.add_argument(
        "--criteria-file",
        metavar="FILE",
        help="one criterion per line, from a file or '-'; required when the target kind's "
        "policy.requires_criteria is true",
    )
    p_spawn.add_argument(
        "--touches-file",
        metavar="FILE",
        help="one touch per line ('path', 'path#symbol', 'path#+' for additions to an "
        "existing file and nothing already in it, 'dir/' for a tree this work "
        "creates or owns, or '+path' for one file it creates), relative to the project "
        "root; required for archetype: work kinds, exactly as `kyo promote` requires it",
    )
    p_spawn.add_argument(
        "--scope",
        metavar="SCOPE",
        help="the part of the system this work belongs to, from kyosei/scopes.yml. Never "
        "defaulted from the source: a spawn crosses a kind boundary",
    )
    p_spawn.add_argument(
        "--from",
        dest="from_file",
        metavar="FILE",
        help="file the new record from one markdown file instead of --title/"
        "--description-file/--criteria-file/--touches-file: front matter for title/"
        "scope/touches, a '## Criteria' section (one criterion per list item), "
        "everything else the description; refused together with any of those flags "
        "(KYO-330)",
    )
    p_spawn.add_argument(
        "--evidence-file",
        metavar="FILE",
        help="evidence stored with the new record in the same act, from a file or '-'; "
        "required when the target kind's policy sets requires_evidence_links and the SOURCE "
        "is a carried kind (a reminder becoming a decision: the operator's words -- who, "
        "when, where, then the words verbatim); not asked of a proposal or investigation "
        "source (KYO-522)",
    )
    p_spawn.add_argument(
        "--check",
        action="store_true",
        help="with --from, validate the file (kind, scope, touches against the repo) "
        "and file nothing (KYO-330)",
    )

    p_spawn.add_argument(
        "--force",
        action="store_true",
        help="file the record although one with an identical title and criteria was filed "
        "in this project within the last hour (KYO-538)",
    )

    p_show = sub.add_parser(
        "show",
        parents=[common],
        help="print a record, a criterion, an evidence artifact, a flag, a dispatch "
        "or a shift note, by the id that names it",
    )
    # KYO-94: the same id shape `kyo redact` has always accepted. A shift note could be
    # found by id in order to be destroyed and not in order to be displayed.
    # KYO-177: dispatch ids too, for the same reason — a dispatch's frozen brief could
    # be found by id in order to be reported against (`kyo report`) and had no command
    # that read it back. Read-only, and running it twice changes nothing.
    # KYO-220: criterion, evidence and flag ids, on that same argument and from a
    # consumer who hit the other half of it — the help named three shapes, the command
    # answered `no record` for every other one, "so the id shapes do not tell you what
    # is showable". The list below is generated from `cmd_views.SHOWABLE_SHAPES`, which
    # is also what the command dispatches on and what its refusal names, so help,
    # behaviour and refusal are one table read three times rather than three copies.
    p_show.add_argument(
        "id",
        help="the id of the object to print — " + cmd_views.SHOWABLE_SHAPES_HELP,
    )
    # KYO-219.c2. Off by default: one `kyo docs settle --why` over a governed-file set
    # stamps one note onto every obligation it named, and the full listing repeats that
    # note once per obligation — 59 copies of a 400-character note in one measured case.
    # The listing is not deleted, only moved behind a word, and `--json` never collapses.
    p_show.add_argument(
        "--expand-obligations",
        action="store_true",
        help="list every docs obligation in full instead of collapsing the ones that "
        "share a settlement and a detail; --json carries them all either way",
    )
    # KYO-420.c3: `kyo show KYO-268 --json` was 116 KB for a caller that wanted two
    # keys. Opt-in, and refused rather than ignored where it cannot apply.
    p_show.add_argument(
        "--fields",
        metavar="FIELDS",
        help="with --json: print only these top-level keys of the document "
        "(comma-separated, e.g. status,title), plus the envelope's ok and command. An "
        "unknown key is refused naming the keys this object has",
    )

    p_edit = sub.add_parser("edit", parents=[common], help="change a record")
    p_edit.add_argument("id")
    p_edit.add_argument(
        "--title", metavar=PROSE_ARGUMENT_METAVAR, help="replace the one-line title"
    )
    p_edit.add_argument(
        "--description-file", metavar="FILE", help="replace the description from a file or '-'"
    )
    p_edit.add_argument("--note-file", metavar="FILE", help="append a note from a file or '-'")
    p_edit.add_argument(
        "--supersede-note",
        metavar="NOTE_ID",
        help="mark this note superseded by the note being appended",
    )
    p_edit.add_argument(
        "--add-criteria-file", metavar="FILE", help="add criteria, one per line, from a file or '-'"
    )
    p_edit.add_argument(
        "--touches-file",
        metavar="FILE",
        help="REPLACE this record's touches with the file's lines ('path', 'path#symbol', "
        "'path#+' for additions to an existing file and nothing already in it, "
        "'dir/' for a tree this work creates or owns, or '+path' for one file it "
        "creates), validated against the repo now; an "
        "empty file clears them. The prior list stays readable in the events ledger",
    )
    p_edit.add_argument(
        # KYO-147.c4. The same replacement `--touches-file` performs, with the lines
        # read from a dispatch's drift finding instead of from a file the controller
        # retyped. It lives on `kyo edit` rather than on `kyo touch-drift` because
        # this is the command KYO-71's own output already names as the remedy, and
        # because `kyo touch-drift` is a pure read that every write-capability
        # declaration in this project (cli.MUTATING_COMMANDS, records mode's
        # allowlist, the desktop app's KYO_WRITING_COMMANDS) agrees is a pure read.
        # Making it write would have made all three wrong at once.
        "--touches-from-drift",
        metavar="DISPATCH_ID",
        help="REPLACE this record's touches with the correction `kyo touch-drift "
        "DISPATCH_ID` computed — the declared paths plus the files the branch actually "
        "changed (KYO-147.c4). It makes the RECORD true; it does NOT improve the "
        "disjointness guard, which reads the NEXT record's table and not this one's",
    )
    p_edit.add_argument(
        "--doc-refs-file",
        metavar="FILE",
        help="REPLACE this record's doc-refs -- the documents its work affects -- with "
        "the file's lines ('path' or 'path#section'), validated against the repo now; an "
        "empty file clears them. The back path to a document's `governs:`, for a document "
        "about this record's subject that names none of its files (KYO-137)",
    )
    p_edit.add_argument(
        "--criterion",
        metavar="CRITERION_ID",
        # KYO-26: this said "criterion to restate", which reads as
        # though restating the TEXT is what it does. KYO-58: it now can, but only
        # while nothing has attested to the wording — see --text-file below.
        help="criterion to void (with --state and --why) or correct (with --text-file)",
    )
    p_edit.add_argument(
        "--state", metavar="STATE", help=f"criterion state: {', '.join(CRITERION_STATES)}"
    )
    p_edit.add_argument(
        "--text-file",
        metavar="FILE",
        # KYO-58.c1/c4: immutability starts at the first VERDICT, not at creation —
        # an unverified criterion has no attestation to protect, and a factual error
        # caught before anything was checked used to be uncorrectable forever.
        help="correct --criterion's wording from a file or '-'. Allowed only while nothing "
        "has attested to it (no verification recorded, still unmet-unbuilt); once a verdict "
        "exists the refusal points at `kyo restate`, which keeps the old text and its "
        "verdicts intact",
    )
    p_edit.add_argument("--plan", metavar="ID", help="join a plan record")
    p_edit.add_argument("--no-plan", action="store_true", help="leave the current plan")
    p_edit.add_argument(
        "--scope",
        metavar="SCOPE",
        help="set the part of the system this work belongs to, from kyosei/scopes.yml",
    )
    p_edit.add_argument(
        "--no-scope", action="store_true", help="clear this record's declared scope"
    )
    p_edit.add_argument(
        "--since-note",
        metavar="NOTE-ID",
        help="a carried kind only: the shift note (PREFIX-Sn) that first recorded this "
        "item, no later than the record's filing; its age counts from that note's time "
        "(KYO-498)",
    )
    p_edit.add_argument(
        "--kind",
        metavar="KIND",
        help="change this record's kind to another kind in kyosei/kinds.yml with the same "
        "archetype (work or knowledge) -- the remedy for a record whose kind kinds.yml no "
        "longer defines (KYO-509)",
    )
    _add_reason_flags(p_edit, "for a criterion state that requires one")

    p_restate = sub.add_parser(
        "restate",
        parents=[common],
        help="void a criterion and add its replacement atomically, back-linked (KYO-58)",
    )
    p_restate.add_argument("criterion_id", metavar="CRITERION_ID")
    p_restate.add_argument(
        "--text-file",
        metavar="FILE",
        required=True,
        help="the replacement wording, from a file or '-'. The old criterion becomes "
        "void-withdrawn and reads as 'superseded by' the new one everywhere; its verdict "
        "history stays attached to the text it was recorded against, and the replacement "
        "starts unverified",
    )

    p_move = sub.add_parser(
        "move", parents=[common], help="change status; contracts are enforced here"
    )
    p_move.add_argument("id")
    # KYO-101: the parser is built before any project is resolved, so it cannot list
    # this project's statuses — they live in kyosei/statuses.yml and are per-project
    # from this release on. It says where they are instead, and the refusal (which does
    # have the store open) names every declared one. Same treatment `--kind` has always
    # had, for the same reason.
    p_move.add_argument(
        "status",
        metavar="STATUS",
        help="a status declared in kyosei/statuses.yml; the default ladder is "
        + " | ".join(default_ladder().names),
    )
    _add_reason_flags(p_move, "(required to enter wont-do)")
    # KYO-308.c1: `plan`'s completion is a status distribution over its members, and
    # this is the escape hatch beside the one the refusal itself names — settle the
    # members, or detach them with `kyo edit --no-plan`. Ignored by every move this
    # refusal does not fire on (a non-plan record, or a plan not entering a
    # completion status), the same way `kyo dispatch --force` is ignored outside the
    # one refusal it overrides.
    p_move.add_argument(
        "--force",
        action="store_true",
        help="close a plan over unsettled (not complete or retired) members, recording "
        "that it was closed over them; or enter a completion or retired status with a "
        "gate still open, leaving it open (KYO-490)",
    )

    p_wont = sub.add_parser(
        "wont-do",
        parents=[common],
        help="retire a work-archetype record; its id is never reused",
    )
    p_wont.add_argument("id")
    _add_reason_flags(p_wont, "(required)")
    # KYO-490: the same meaning `kyo move --force` has for an open gate.
    p_wont.add_argument(
        "--force",
        action="store_true",
        help="retire the record with a gate still open, leaving it open",
    )

    # KYO-44.c1: the knowledge archetype's half of `wont-do`. Separate verb because
    # "we won't do this" is a statement about work; a decision or a piece of evidence
    # is retired by being declared void, not by being un-scheduled.
    p_void = sub.add_parser(
        "void",
        parents=[common],
        help="retire a knowledge-archetype record; its id is never reused",
    )
    p_void.add_argument("id")
    _add_reason_flags(p_void, "(required)")
    # KYO-490: the same meaning `kyo move --force` has for an open gate.
    p_void.add_argument(
        "--force",
        action="store_true",
        help="retire the record with a gate still open, leaving it open",
    )

    p_link = sub.add_parser("link", parents=[common], help="create a typed edge; cycles refused")
    p_link.add_argument("src", metavar="FROM_ID")
    p_link.add_argument("edge_type", metavar="EDGE", help=" | ".join(LINK_TYPES))
    p_link.add_argument("dst", metavar="TO_ID")

    p_unlink = sub.add_parser(
        "unlink",
        parents=[common],
        help="retract a typed edge by its own id; the edge and the reason stay in the "
        "events ledger",
    )
    p_unlink.add_argument("link_id", metavar="LINK_ID")
    _add_reason_flags(p_unlink, "for retracting it (required)")

    p_gate = sub.add_parser("gate", parents=[common], help="add a gate; orthogonal to status")
    p_gate.add_argument("id")
    p_gate.add_argument("gate_type", metavar="GATE", help=" | ".join(GATE_TYPES))
    p_gate.add_argument(
        "--do",
        metavar=PROSE_ARGUMENT_METAVAR,
        help="the exact condition, or the exact thing to run, when it fits on one line",
    )
    # KYO-148.c1: the compliant route law 5 always implied and the parser never offered.
    # Adding it also flips `--do` from warned-about to REFUSED for shell residue, with no
    # edit anywhere else: `check_prose_arguments` derives that from whether a
    # `--<flag>-file` twin exists on the same subparser (KYO-91).
    p_gate.add_argument(
        "--do-file",
        metavar="FILE",
        help="read the payload from a file, or '-' for stdin — for a procedure with a "
        "command and several links that cannot be written on one line",
    )
    p_gate.add_argument(
        GATE_TAG_FLAG,
        action="append",
        metavar="TAG",
        help="classify what this gate is waiting on, from kyosei/gate-tags.yml; repeat "
        "for several. Refused if the registry does not declare it, and beside --do "
        "rather than instead of it: the tag is what makes the gate findable "
        f"(`kyo list {GATE_TAG_FILTER_FLAG}`), the payload is what makes it clearable",
    )

    p_retag = sub.add_parser(
        "retag",
        parents=[common],
        help="replace the tags on an existing gate, by its own id; not a clearing (KYO-130)",
    )
    p_retag.add_argument("gate_id", metavar="GATE_ID")
    # Required, and mutually exclusive: an absent --tag cannot be allowed to mean both
    # "clear this gate's tags" and "you forgot the flag", so saying "none" gets its own
    # word — the --no-plan / --no-scope precedent on `kyo edit`.
    p_retag_tags = p_retag.add_mutually_exclusive_group(required=True)
    p_retag_tags.add_argument(
        GATE_TAG_FLAG,
        action="append",
        metavar="TAG",
        help="the tags the gate should carry from now on, from kyosei/gate-tags.yml; "
        "repeat for several. REPLACES the gate's tags rather than adding to them, and "
        "both sets are echoed. Refused if the registry does not declare one, in the "
        "same words `kyo gate` refuses it",
    )
    p_retag_tags.add_argument(
        "--no-tags",
        action="store_true",
        help="leave the gate carrying no tags at all",
    )

    p_redo = sub.add_parser(
        "redo",
        parents=[common],
        help="replace the --do payload on an existing gate, by its own id; not a "
        "clearing (KYO-148)",
    )
    p_redo.add_argument("gate_id", metavar="GATE_ID")
    # Required and mutually exclusive, on `retag`'s precedent: the command exists only to
    # put a new payload on the gate, so an absent flag cannot be allowed to mean anything
    # at all. Both forms are offered because c4 asks that a one-line payload not be
    # forced into a file — and because `--do-file` having a `--do` twin here is what
    # keeps it out of the skill's prose-write-path check, where it would otherwise be a
    # controller-facing surface KYO-152 owns the documenting of.
    p_redo_payload = p_redo.add_mutually_exclusive_group(required=True)
    p_redo_payload.add_argument(
        "--do",
        metavar=PROSE_ARGUMENT_METAVAR,
        help="the corrected payload, when it fits on one line. REPLACES the gate's "
        "payload rather than appending to it, and both texts are echoed",
    )
    p_redo_payload.add_argument(
        "--do-file",
        metavar="FILE",
        help="read the corrected payload from a file, or '-' for stdin",
    )

    p_ungate = sub.add_parser(
        "ungate",
        parents=[common],
        help="clear a gate by its own id; several ids, or every open gate with a tag, in "
        "one transaction (KYO-492)",
    )
    p_ungate.add_argument(
        "gate_id",
        metavar="GATE_ID",
        nargs="*",
        help="one or more gate ids; with several, every one is cleared or none is",
    )
    p_ungate.add_argument(
        GATE_TAG_FLAG,
        dest="tag",
        metavar="TAG",
        help="select every OPEN gate carrying this tag (from kyosei/gate-tags.yml) "
        "instead of naming ids; lists them and writes nothing without --apply",
    )
    p_ungate.add_argument(
        "--apply",
        action="store_true",
        help="with --tag: clear every gate the selection lists, in one transaction",
    )
    _add_reason_flags(p_ungate, "for clearing it")

    # KYO-585: the creating commands search recx for prior art when this
    # project's `recx.prior_art` names the kind and the command; this skips one search.
    for creating in (p_new, p_promote, p_spawn):
        creating.add_argument(
            "--no-prior-art",
            action="store_true",
            help="skip the prior-art search kyosei/config.yml's recx.prior_art would run "
            "for this record (KYO-585); `kyo prior-art <id>` runs it later",
        )

    p_prior_art = sub.add_parser(
        "prior-art",
        parents=[common],
        help="search recx's index for existing work matching a record's title and "
        "description, and store the results as evidence under a derived claim, "
        "superseding the record's previous one; needs the extra kyosei[recx] (KYO-585)",
    )
    p_prior_art.add_argument("id", metavar="RECORD_ID")

    p_prior_art_setting = sub.add_parser(
        "prior-art-setting",
        parents=[common],
        help="turn this project's prior-art search on or off: writes recx.prior_art.state "
        "in kyosei/config.yml, keeping its trigger, limit and index_url; `on` needs the "
        "extra kyosei[recx] (KYO-586)",
    )
    p_prior_art_setting.add_argument(
        "state",
        choices=("on", "off"),
        help="on: search recx when a record the trigger names is created (by default a "
        "proposal, by kyo new, promote or spawn); off: record that this project declined",
    )

    p_claim = sub.add_parser(
        "claim", parents=[common], help="record a claim about a record (law 9)"
    )
    p_claim.add_argument("id", metavar="RECORD_ID")
    p_claim.add_argument(
        "--level", required=True, metavar="LEVEL", help=" | ".join(CLAIM_LEVELS)
    )
    p_claim.add_argument(
        "--method",
        required=True,
        metavar=PROSE_ARGUMENT_METAVAR,
        help="one-line method or instrument",
    )
    p_claim.add_argument(
        "--file",
        required=True,
        metavar="FILE",
        help="the claim's statement from a file, or '-' for stdin (law 5)",
    )
    p_claim.add_argument(
        "--supersedes", metavar="CLAIM_ID", help="the prior claim this one supersedes"
    )

    p_verify = sub.add_parser(
        "verify", parents=[common], help="record a criterion verification (laws 3, 9, 10)"
    )
    # KYO-198: the positional and the two flags below are `required=True` in spirit and
    # optional in argparse, because `--from-report` supplies all three from the stored
    # report. Which of the two forms this invocation is, and what each one still needs,
    # is decided in `cmd_verify` — argparse cannot express "required unless" without
    # mutually exclusive groups that would render as noise in `--help` and in
    # `kyo reference`.
    p_verify.add_argument("criterion_id", metavar="CRITERION_ID", nargs="?")
    p_verify.add_argument(
        "--verdict", metavar="VERDICT", help=" | ".join(sorted(VERIFY_VERDICTS))
    )
    p_verify.add_argument(
        "--method", metavar="METHOD", help=" | ".join(VERIFICATION_METHODS)
    )
    p_verify.add_argument(
        "--evidence-file",
        metavar="FILE",
        help="captured evidence from a file, or '-' for stdin",
    )
    p_verify.add_argument(
        "--from-report",
        metavar="DISPATCH_ID",
        help="apply that dispatch's current report's own criteria_verdicts[] — verdict, "
        "method, evidence and reason each read from the report rather than retyped "
        "(KYO-198); takes no CRITERION_ID of its own",
    )
    p_verify.add_argument(
        "--only",
        metavar="CRITERION_ID",
        nargs="+",
        help="with --from-report: apply only these criteria's verdicts and leave the rest "
        "of the report's unapplied",
    )
    p_verify.add_argument(
        "--again",
        action="store_true",
        help="with --from-report: apply a verdict even though the criterion's latest "
        "verification already came from this report (KYO-415), or was recorded after "
        "this report was ingested (KYO-511); without it such an entry is listed as "
        "already applied, or as a newer verdict kept, and skipped",
    )
    _add_reason_flags(p_verify, "(required for unmet-unobserved)")

    p_attach = sub.add_parser(
        "attach", parents=[common], help="attach an evidence artifact, stored verbatim (law 5, 9)"
    )
    p_attach.add_argument("id", metavar="RECORD_ID")
    p_attach.add_argument(
        "--file", required=True, metavar="FILE", help="captured text from a file, or '-' for stdin"
    )
    p_attach.add_argument(
        "--supports", metavar="ID", help="the claim, criterion, or record id this supports"
    )

    p_redact = sub.add_parser(
        "redact",
        parents=[common],
        help="remove verbatim prose (evidence, claim, note, shift note, or a record's own "
        "title, description or criterion) from the store and the git-canonical ledgers, "
        "recording that it happened (KYO-426)",
    )
    p_redact.add_argument(
        "evidence_id",
        metavar="ID",
        # KYO-131: every shape names the command that writes one. A consumer read this
        # list, found no way to produce a RECORD.nN, and filed the id shape as a
        # redaction target for an object nothing could make — `kyo edit --note-file`
        # had been making them all along. The same gap was open on RECORD.eN and
        # RECORD.mN, whose writers no controller-facing surface named either: a
        # vocabulary that lists ids without their write paths teaches a careful reader
        # that the objects are unreachable.
        help="an evidence artifact (RECORD.eN, written by `kyo attach`), claim "
        "(RECORD.mN, by `kyo claim`), note (RECORD.nN, by `kyo edit --note-file`), "
        "or shift note (PREFIX-SN, by `kyo note --shift`); or a record's own title "
        "(RECORD.title), description (RECORD.description) or criterion (RECORD.cN), "
        "every version of it up to now (KYO-426)",
    )
    _add_reason_flags(p_redact, "for redacting it (required)")

    p_list = sub.add_parser("list", parents=[common], help="list records, filtered and summarised")
    p_list.add_argument("--kind", metavar="KIND", help="only records of this kind")
    p_list.add_argument(
        "--scope",
        metavar="SCOPE",
        help="only records declaring this scope; refused if kyosei/scopes.yml does not "
        "declare it, rather than answered with an empty listing",
    )
    p_list.add_argument(
        GATE_TAG_FILTER_FLAG,
        metavar="TAG",
        help="only records carrying an OPEN gate tagged this, from kyosei/gate-tags.yml "
        "— a query such as 'everything waiting on a human to review a UI element'. "
        "Refused if the registry does not declare the tag, rather than answered with "
        "an empty listing. Orthogonal to --status: a record can be working and gated",
    )
    p_list.add_argument(
        "--status",
        metavar="STATUS",
        # KYO-420.c4: repeatable. One `--status` stores the single string it always
        # did, so the engine and a Base's single-string `status:` see the same value.
        action="append",
        # KYO-44.c2: the knowledge standings are shown in the listing, so they must
        # also be askable — otherwise the listing shows a word no filter accepts.
        help="a status declared in kyosei/statuses.yml (default ladder: "
        + " | ".join(default_ladder().names)
        + "), or "
        + " | ".join(KNOWLEDGE_STANDINGS)
        + " for knowledge-archetype records. Repeat it to ask for any of several "
        "(`--status open --status working`)",
    )
    p_list.add_argument(
        "--plan",
        metavar="ID",
        help="only records belonging to this plan — membership is stored on the member, "
        "so this is the only way to walk the relation the other way",
    )
    # KYO-420.c4: the two ways to ask for fewer rows that are not filters a Base
    # declares — see `query.LIST_ONLY_FLAGS` for why they live on this command alone.
    p_list.add_argument(
        query.UNSETTLED_FLAG,
        action="store_true",
        help="only records not yet settled: leave out every record whose stored status "
        "has the `complete` or `retired` role in kyosei/statuses.yml. Combines with "
        "--status (both must hold)",
    )
    p_list.add_argument(
        "--limit",
        metavar="N",
        type=_positive_int,
        help="print at most N rows, in the listing's usual order; the summary line says "
        "how many matched and that the listing was limited",
    )

    # KYO-108. `kyo base` is `kyo list` with the question saved to a file and
    # the arrangement (columns, sort, group-by) declared beside it.
    #
    # The line this parser holds is between the two halves of a Base, and it is not the
    # line "a name and nothing else" (KYO-120 corrects that earlier wording, which was
    # a description of the flag set rather than of the rule behind it).
    #
    #   Which rows exist, and what is in them — source, filters, columns. Deliberately
    #   NO flags. A Base's query lives in kyosei/bases.yml, and a flag that overrode
    #   one here would be a second place a query is written, which is the drift
    #   KYO-108.c4 exists to prevent arriving through the CLI instead of the code.
    #   KYO-109 added two more sources and no flags for the same reason: which source
    #   a run reads is a fact about the reviewable file, not about how it was typed.
    #
    #   How the rows a run already produced are arranged — group-by. Overridable, and
    #   `--group-by` below is that override. Group-by (with color-by and size-by) is
    #   a binding a *renderer* may change; changing it selects no row,
    #   drops no row and alters no cell, so the reviewable file remains the whole
    #   account of what this run asked the store. `bases.run_base` enforces the split
    #   structurally: the override reaches `applied`, never `base`, and the rows come
    #   back from `kyosei.query` untouched by it.
    p_base = sub.add_parser(
        "base",
        parents=[common],
        help=(
            "saved view definitions (kyosei/bases.yml) over records, ideas/ or docs/: "
            "list them, or run one read-only"
        ),
    )
    p_base.add_argument("action", choices=["list", "run"])
    p_base.add_argument(
        "name",
        nargs="?",
        metavar="NAME",
        help="for `run`: the saved base to run. Refused with the file's declared names "
        "if it is not one of them, never answered with an empty view",
    )
    p_base.add_argument(
        "--group-by",
        metavar="FIELD",
        help="for `run`: arrange the rows by FIELD instead of the base's declared "
        "`group-by`. The one binding this command overrides, because grouping "
        "rearranges rows a run already produced while source, filters and columns "
        "decide which rows exist — those stay in the reviewable file "
        "(KYO-120). Validated by the rule the stored key gets: an unknown or "
        "multi-valued field is refused naming the field set, and `applied.group-by` "
        "in --json reports the field actually used",
    )

    sub.add_parser("map", parents=[common], help="render the kind registry as a graph")

    p_ideas = sub.add_parser("ideas", parents=[common], help="the ideas layer (never enforced)")
    p_ideas.add_argument(
        "action",
        choices=["review", "migrate-stamps"],
        help="review: unpromoted ideas by age + broken mentions; migrate-stamps: move "
        "bottom-of-file promotion stamps into top-of-file YAML frontmatter",
    )
    p_ideas.add_argument(
        "--apply",
        action="store_true",
        help="migrate-stamps: actually rewrite the files. Without it nothing under ideas/ "
        "is written — this is the only command that edits the human's own directory, so "
        "it shows the list first",
    )

    p_docs = sub.add_parser(
        "docs", parents=[common], help="Layer 3 docs-SOT skeleton validator (M8)"
    )
    p_docs.add_argument(
        "action",
        choices=["assemble", "check", "index", "obligations", "preview", "settle", "suggest"],
        help="check: every governs: target must exist. obligations: the document "
        "sections this project's dispatches put a question to. preview: the sections "
        "THIS worktree's branch would raise, read-only and store-free, for a worker to "
        "read before writing docs_verdicts[] (KYO-199). settle: record the verdict that "
        "clears one (KYO-137). assemble: append each dispatch's doc fragment to its "
        "numbered document with the next number and name the project's follow-up "
        "commands — the orchestrator's act on the merged tree (KYO-299). suggest: draft "
        "the declarations this repository looks like it wants — a numbered: block for a "
        "document whose headings are numbered, an ssot: block for documents whose "
        "headings carry requirement ids (KYO-556), an exclusion for a generated file "
        "inside a set's globs — each with its evidence and the exact YAML to paste, "
        "written nowhere and enforced by nothing (KYO-303). index: write the generated section "
        "index of each document set declaring `section_status.index` in documents.yml — "
        "per document its section counts by status and code pointers, its non-current "
        "sections with their links and the head of each chain, and the sections no code "
        "points at (KYO-502)",
    )
    p_docs.add_argument(
        "id",
        nargs="*",
        metavar="ID",
        help="settle: the docs obligation(s) to record a verdict against. Naming more "
        "than one settles them in a single act carrying one verdict, one method and one "
        "reason — for the case where several sections are one editorial act (KYO-159). "
        "Every member is named explicitly and nothing is derived from a document, a "
        "governed file or a dispatch: `kyo docs obligations` prints the line to copy. "
        "The one set the tool forms is the caller's own naming of a record: "
        "`--record <id> --all` (KYO-528)",
    )
    p_docs.add_argument(
        "--record",
        metavar="ID",
        help="obligations: only this record's. settle --all: the record whose open "
        "obligations are settled",
    )
    p_docs.add_argument(
        "--all",
        action="store_true",
        help="obligations: include the ones already settled, not just the open ones. "
        "settle: with --record <id>, every open obligation of that record as one act "
        "(KYO-528), in place of naming each id",
    )
    p_docs.add_argument(
        "--verdict",
        metavar="VERDICT",
        help=f"settle: {' | '.join(OBLIGATION_VERDICTS)} — whether the document was "
        "changed to match the work, or read and found to need no change. ONE verdict "
        "for the whole set: 'I changed all of these' and 'I read all of these and none "
        "needed changing' are each one defensible statement, and some-of-each is not, "
        "so there is deliberately no way to spell a per-member verdict",
    )
    p_docs.add_argument(
        "--method",
        metavar="TEXT",
        help="settle: how the verdict was reached (law 9). Required: a verdict without "
        "its instrument is a claim whose weight cannot be judged later",
    )
    p_docs.add_argument(
        "--against",
        metavar="REF",
        help="preview: the base to diff this branch against (default: main) — whatever "
        "your worktree was branched from",
    )
    p_docs.add_argument(
        "--branch",
        metavar="REF",
        help="preview: the branch to read (default: HEAD, the one checked out here)",
    )
    p_docs.add_argument(
        "--suppressed",
        action="store_true",
        help="preview: list the sections a declared `#symbol` stopped from raising on "
        "this diff — what a whole-file target would have raised and this one did not "
        "(KYO-352), and the code pointers raised at whole-file granularity because their "
        "file has no region finder (KYO-500). The count is printed either way, and --json "
        "always carries the list; this is how a project that has just narrowed many "
        "targets audits what went quiet",
    )
    p_docs.add_argument(
        "--from-report",
        metavar="DISPATCH_ID",
        help="settle: apply that dispatch's current report's own docs_verdicts[] — each "
        "entry's verdict, method and reason read from the report rather than retyped "
        "(KYO-199.c4); takes no ID, --verdict, --method or --why of its own",
    )
    p_docs.add_argument(
        "--check",
        action="store_true",
        help="index: write nothing, and exit 1 naming each index whose committed file "
        "differs from what `kyo docs index` would write now",
    )
    p_docs.add_argument(
        "--dry-run",
        action="store_true",
        help="assemble: print exactly the plan a real run would apply — which fragment "
        "becomes which number, and the follow-up commands — and write nothing",
    )
    p_docs.add_argument(
        "--only",
        metavar="OBLIGATION_ID",
        nargs="+",
        help="with --from-report: apply only these obligations' verdicts and leave the "
        "rest of the report's unapplied",
    )
    _add_reason_flags(
        p_docs,
        "settle: why, in the orchestrator's own words — required when settling more than "
        "one obligation at once, where it is the evidence the review covered every member",
    )

    p_check_branch = sub.add_parser(
        "check-branch",
        parents=[common],
        help="merge guard: fail if a branch diffs kyosei/ or ideas/ (M8)",
    )
    p_check_branch.add_argument(
        "branch", nargs="?", help="branch to check (default: the current branch)"
    )
    p_check_branch.add_argument(
        "--against",
        metavar="REF",
        help="base ref to diff against (default: this repository's trunk -- the "
        "`trunk:` config key, else origin/HEAD, init.defaultBranch, main, master; "
        "`main` written here means the same)",
    )
    p_check_branch.add_argument(
        "--subagent-stop",
        action="store_true",
        help="read Claude Code's SubagentStop payload on stdin and check the worktree "
        "the stopping worker was dispatched into, rather than the directory the hook "
        "inherited its cwd from; how the packaged hook invokes this (KYO-128)",
    )
    p_check_branch.add_argument(
        "--quiet",
        action="store_true",
        help="when the check passes, print one line naming the tree checked and the "
        "result instead of the full explanation; a failing check prints in full, with "
        "the same exit code, and --json is unchanged (KYO-420)",
    )

    p_check_build = sub.add_parser(
        "check-build",
        parents=[common],
        help="has the `kyo` under this session changed since its opening brief? Compares "
        "the running build with the one this session's session-start brief recorded; "
        "read-only (KYO-398)",
    )
    p_check_build.add_argument(
        "--hook",
        action="store_true",
        help="print one line only when the builds differ, and nothing otherwise, always "
        "exiting 0; how the packaged UserPromptSubmit hook invokes this",
    )

    p_check_clean = sub.add_parser(
        "check-clean",
        parents=[common],
        help="worker stop guard: fail if the worktree holds uncommitted work outside "
        "the directories `check-branch` forbids a branch to touch",
    )
    p_check_clean.add_argument(
        "--subagent-stop",
        action="store_true",
        help="read Claude Code's SubagentStop payload on stdin and check the worktree "
        "the stopping worker was dispatched into, rather than the directory the hook "
        "inherited its cwd from; how the packaged hook invokes this (KYO-128)",
    )
    p_check_clean.add_argument(
        "--quiet",
        action="store_true",
        help="when the check passes, print one line naming the tree checked and the "
        "result instead of the full explanation; a failing check prints in full, with "
        "the same exit code, and --json is unchanged (KYO-420)",
    )

    p_check_command = sub.add_parser(
        "check-command",
        parents=[common],
        help="worker command guard: refuse a command this project declares "
        "orchestrator-only (`controller_only_commands`), or one whose declared timeout is "
        "over its ceiling (`worker_command_timeout_ceiling_seconds`), inside a running "
        "dispatch's worktree (KYO-298, KYO-312)",
    )
    p_check_command.add_argument(
        # `dest` deliberately NOT `command`: the subparser itself stores the
        # subcommand name there (`sub = parser.add_subparsers(dest="command")`), so a
        # positional of that name silently overwrites it with the argument's value —
        # or with `None` when it is omitted, which makes `COMMAND_TABLE[args.command]`
        # a `KeyError` on the hook's own invocation.
        "command_line",
        metavar="COMMAND",
        nargs="?",
        help="the command line to check, as a worker would type it (default: read it "
        "from the PreToolUse payload with --pre-tool-use)",
    )
    p_check_command.add_argument(
        "--pre-tool-use",
        action="store_true",
        help="read Claude Code's PreToolUse payload on stdin and take the command, the "
        "directory it will run in and the timeout it declares from it; how the packaged "
        "hook invokes this",
    )
    p_check_command.add_argument(
        "--timeout-ms",
        type=int,
        metavar="MS",
        help="state by hand the timeout the Bash call declares, in milliseconds, as the "
        "PreToolUse payload states it for itself — so the ceiling check "
        "(`worker_command_timeout_ceiling_seconds`, KYO-312) is answerable by hand. "
        "Omitted means the call declares none, which is never refused",
    )

    sub.add_parser(
        "pre-tool-use",
        parents=[common],
        help="both PreToolUse guards (`kyo check-push --pre-tool-use` and `kyo "
        "check-command --pre-tool-use`) against ONE read of the payload on stdin, in one "
        "interpreter: exits 2 if either refuses, with both refusals when both do, and "
        "is silent otherwise. Opt-in: `kyo install-hooks` still wires the two commands "
        "(KYO-422)",
    )

    p_check_push = sub.add_parser(
        "check-push",
        parents=[common],
        help="publication guard: fail if pushing this repository would publish an "
        "unacknowledged Kyosei store (KYO-60)",
    )
    p_check_push.add_argument(
        "--pre-tool-use",
        action="store_true",
        help="read Claude Code's PreToolUse payload on stdin and check only when it "
        "describes a command that publishes; how the packaged hook invokes this",
    )
    p_check_push.add_argument(
        "--advisory",
        action="store_true",
        help="warn and exit 0 instead of blocking — what the git pre-push hook runs, "
        "because a human pushing their own repository owns that decision",
    )
    p_check_push.add_argument(
        "--acknowledge",
        action="store_true",
        help="accept, once and for all, that pushing publishes this store: records who "
        "and when in kyosei/config.yml, after which this command is silent",
    )

    p_sessions = sub.add_parser(
        "sessions",
        parents=[common],
        help="the Claude Code sessions this project has, and each one's claude.ai "
        "Remote Control page (KYO-186)",
    )
    p_sessions.add_argument(
        "--hook",
        action="store_true",
        help="read Claude Code's SessionStart / Stop / SessionEnd payload on stdin and "
        "record this session in the store's local sessions.json; how the packaged hook "
        "invokes this. Silent on success, and always exit 0 — a session that could not "
        "be recorded must still start, turn and end",
    )

    p_install_hooks = sub.add_parser(
        "install-hooks",
        parents=[common],
        help="merge Claude Code hook templates into .claude/settings.json (M8)",
    )
    p_install_hooks.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="show what would be written without writing it",
    )
    p_install_hooks.add_argument(
        "--copies",
        action="store_true",
        help="write the tracked copies in a project not already on them; without it, a "
        "project with neither copy or on the plugin is told to use `kyo harness install "
        "claude-code`, the default (KYO-595)",
    )

    p_install_skill = sub.add_parser(
        "install-skill",
        parents=[common],
        help="write the packaged /kyosei orchestrator workflow, and the reference beside "
        "it, into .claude/skills/ (KYO-328)",
    )
    p_install_skill.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="show what would be written without writing it",
    )
    p_install_skill.add_argument(
        "--copies",
        action="store_true",
        help="write the tracked copies in a project not already on them; without it, a "
        "project with neither copy or on the plugin is told to use `kyo harness install "
        "claude-code`, the default (KYO-595)",
    )

    p_install_agents = sub.add_parser(
        "install-agents",
        parents=[common],
        help="write the worker agent types the operator chose into .claude/agents/, "
        "from the packaged templates, with the operator's model and effort (setup "
        "decision C4, KYO-607)",
    )
    p_install_agents.add_argument(
        "--type",
        dest="types",
        action="append",
        default=[],
        metavar="TEMPLATE=MODEL[:EFFORT]",
        help="one agent type to write (repeatable): TEMPLATE is worker (build tasks whose "
        "design is settled; effort medium unless named) or designer (proposals, "
        "measurements, judgement calls; effort high unless named), written as "
        ".claude/agents/kyosei-TEMPLATE.md; EFFORT is one of low, medium, high, xhigh, max",
    )
    p_install_agents.add_argument(
        "--force",
        action="store_true",
        help="replace an existing file that differs from what would be written",
    )
    p_install_agents.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="show what would be written without writing it",
    )

    p_install_procedures = sub.add_parser(
        "install-procedures",
        parents=[common],
        help="add the standing rules this build ships that kyosei/procedures.md does "
        "not have yet, leaving every rule already there untouched unless --adopt "
        "names it (KYO-212, KYO-151.c2)",
    )
    p_install_procedures.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="show which rules would be added without writing them",
    )
    p_install_procedures.add_argument(
        "--adopt",
        action="append",
        default=[],
        metavar="RULE_TITLE",
        help="rewrite this rule's text in the store copy from the packaged default "
        "(repeatable); refuses a rule the packaged default does not ship or one "
        "already identical (KYO-151.c2)",
    )
    p_install_procedures.add_argument(
        "--adopt-preamble",
        action="store_true",
        help="rewrite the preamble (everything above the first rule) from the "
        "packaged default the same way; refuses if the two already agree",
    )

    p_rules = sub.add_parser(
        "rules",
        parents=[common],
        help="print the standing rules in full — one by title, or the list; the text a "
        "dispatch brief carries one line of (KYO-327); `kyo rules add` writes a project "
        "rule into kyosei/procedures.md (KYO-565)",
    )
    p_rules.add_argument(
        "title",
        nargs="?",
        metavar="TITLE",
        help="print the rule whose title contains this text, in full; omit to list "
        "every rule with its audience. The word `add`, exactly, is `kyo rules add` "
        "(KYO-565): to print a rule whose title contains 'add', spell it any other "
        "way, e.g. `kyo rules Add`",
    )
    # KYO-565: `kyo rules add` is the positional TITLE spelled `add`, not a subparser
    # -- an argparse subparser beside an optional positional would make every
    # `kyo rules "<title>"` a choice error. These four flags are the add form's and
    # are refused anywhere else, by the command, before anything is read or written.
    p_rules.add_argument(
        "--title",
        dest="add_title",
        metavar=PROSE_ARGUMENT_METAVAR,
        help="with `add`: the new rule's short imperative title",
    )
    p_rules.add_argument(
        "--audience",
        metavar="TOKEN[,TOKEN...]",
        help="with `add`: who the rule reaches -- worker, orchestrator, human "
        "(controller is orchestrator's earlier name), comma-separated",
    )
    p_rules.add_argument(
        "--enforced-by",
        dest="enforced_by",
        metavar=PROSE_ARGUMENT_METAVAR,
        help="with `add`: one line naming what holds the rule up, or `aspirational`",
    )
    p_rules.add_argument(
        "--reason-file",
        dest="reason_file",
        metavar="FILE",
        help="with `add`: the rule's reasoning, one to three sentences, from a file or "
        "'-' for stdin",
    )

    p_serve = sub.add_parser(
        "serve",
        parents=[common],
        help="serve this machine's projects to any viewer over loopback HTTP: the read "
        "commands' own --json, each store opened read-only (KYO-453)",
    )
    p_serve.add_argument(
        "--port",
        type=int,
        default=serve_module.DEFAULT_PORT,
        metavar="N",
        help=f"the port to listen on (default {serve_module.DEFAULT_PORT}; 0 picks a free one)",
    )
    p_serve.add_argument(
        "--bind",
        default=serve_module.DEFAULT_BIND,
        metavar="ADDR",
        help="the loopback address to listen on (default 127.0.0.1, or ::1). Any other "
        "address is refused, with no override: the server has no password and no TLS, "
        "and another machine reaches it through Tailscale Serve",
    )
    p_serve.add_argument(
        "--allow-host",
        action="append",
        default=[],
        metavar="NAME",
        help="also answer requests whose Host is NAME (repeatable), such as the "
        "<machine>.<tailnet>.ts.net name Tailscale Serve forwards; every other non-loopback "
        "Host is refused as possible DNS rebinding",
    )
    p_serve.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help="answer a browser page from ORIGIN (repeatable, e.g. http://localhost:5173); "
        "a request carrying any other Origin is refused",
    )
    p_serve.add_argument(
        "--install",
        action="store_true",
        help="print the per-user agent that runs this kyo serve from login (launchd "
        "dev.kyosei.serve on macOS, the systemd user unit kyosei-serve.service on Linux), "
        "with this command's --port, --bind, --allow-host and --allow-origin; with --apply, "
        "write it and start it (KYO-457)",
    )
    p_serve.add_argument(
        "--uninstall",
        action="store_true",
        help="print what removing that agent does; with --apply, stop it and remove it",
    )
    p_serve.add_argument(
        "--status",
        action="store_true",
        help="report the installed agent, what answers on the port, and whether that "
        "server is stale (started as a build no longer installed)",
    )
    p_serve.add_argument(
        "--restart",
        action="store_true",
        help="restart the installed agent (launchctl kickstart -k, systemctl --user restart)",
    )
    p_serve.add_argument(
        "--apply",
        action="store_true",
        help="with --install or --uninstall: do it rather than print it",
    )

    p_controller = sub.add_parser(
        "orchestrator",
        aliases=list(ORCHESTRATOR_COMMAND_ALIASES),
        parents=[common],
        help="start, stop, attach to or list detached claude Remote Control sessions, one "
        "per project, on this machine's `tmux -L kyosei` server -- safe to run over SSH; "
        "--install the LaunchAgent that keeps that server running from login (KYO-458, "
        "KYO-474)",
    )
    p_controller.add_argument(
        "action",
        nargs="?",
        choices=("start", "stop", "attach", "list"),
        help="start PROJECT's session (orchestrator_command from $KYOSEI_HOME/settings.yml, "
        "default claude, through a login shell, in the project directory, detached), stop "
        "it, attach this terminal to it (`tmux -L kyosei attach -t =PREFIX`; over SSH, "
        "`ssh -t HOST \"zsh -lc 'kyo orchestrator attach PREFIX'\"`), or list every "
        "registered project's session, whether claude is alive in it and the command it "
        "was started with",
    )
    p_controller.add_argument(
        "project",
        nargs="?",
        metavar="PROJECT",
        help="for start, stop and attach: a project in the machine's registry, by name or "
        "prefix, or the path of a project directory",
    )
    p_controller.add_argument(
        "--install",
        action="store_true",
        help="print the per-user LaunchAgent (gui/<uid>) that keeps `tmux -L kyosei` "
        "running from login; with --apply, write it and load it",
    )
    p_controller.add_argument(
        "--uninstall",
        action="store_true",
        help="print what removing that LaunchAgent does; with --apply, boot it out (ending "
        "its server and every session in it) and remove it",
    )
    p_controller.add_argument(
        "--apply",
        action="store_true",
        help="with --install or --uninstall: do it rather than print it",
    )

    p_projects = sub.add_parser(
        "projects",
        parents=[common],
        help="list, add or remove the projects in this machine's registry "
        "($KYOSEI_HOME/projects.json), which kyo serve and kyo orchestrator answer for; the "
        "registry's one writer (KYO-455)",
    )
    p_projects.add_argument(
        "action",
        choices=("list", "add", "remove"),
        help="list every registered project with the name kyo serve uses for it, add the "
        "initialised project at PATH, or remove one entry by name or root path",
    )
    p_projects.add_argument(
        "target",
        nargs="?",
        metavar="PATH|NAME",
        help="for add: the project's directory; for remove: its registered name or root",
    )

    p_onboard = sub.add_parser(
        "onboard",
        parents=[common],
        # KYO-558.c12: the line a person scanning `kyo --help` needs to stop at.
        help="guided project setup, where a new project starts: prints this build's "
        "setup steps for an agent to follow in a new or existing project, or with --check "
        "reads a project back against the same checklist and writes nothing (KYO-514)",
    )
    p_onboard.add_argument(
        "--check",
        action="store_true",
        help="report every readback item of the project here (or --root) as present, "
        "missing or behind this build, each with the command that fixes it; reads files "
        "and git only, never opens the database, writes nothing",
    )
    p_onboard.add_argument(
        "--all",
        action="store_true",
        help="with --check: check every project in $KYOSEI_HOME/projects.json, one block "
        "per project (implies --check)",
    )

    p_harness = sub.add_parser(
        "harness",
        parents=[common],
        help="install Kyosei into an agent harness: `install claude-code` links "
        "$KYOSEI_HOME/harness/claude-code to this build's packaged plugin, registers it with "
        "`claude plugin marketplace add`, and inside a Kyosei project enables it there at "
        "local scope (KYO-594)",
    )
    p_harness.add_argument(
        "action",
        choices=("install", "migrate"),
        help="`install` the harness integration; `migrate` a project from the copies "
        "`kyo install-skill` and `kyo install-hooks` wrote to the plugin: it enables the "
        "plugin here and removes the unedited copies, naming what it leaves (KYO-595)",
    )
    p_harness.add_argument(
        "harness",
        nargs="?",
        choices=cmd_harness.HARNESSES,
        help="the harness to install into (`migrate` assumes claude-code, the only one)",
    )

    p_dispatch = sub.add_parser(
        "dispatch",
        parents=[common],
        help="dispatch a record with a frozen brief, or `dispatch close` a dispatch (M5)",
    )
    p_dispatch.add_argument(
        "target", metavar="RECORD_ID|close", help="a record id to dispatch, or the literal 'close'"
    )
    p_dispatch.add_argument(
        "dispatch_id",
        nargs="?",
        metavar="DISPATCH_ID",
        help="with 'close': the dispatch id to close",
    )
    p_dispatch.add_argument(
        "--force",
        action="store_true",
        help="dispatch over a touch-disjointness or worktree collision with a running dispatch",
    )
    p_dispatch.add_argument(
        "--worktree",
        metavar="DIR",
        help="where this dispatch's work will happen (default: this project root). "
        "Recorded on the dispatch, stated in the frozen brief, and refused if another "
        "running dispatch already declares it",
    )
    p_dispatch.add_argument(
        "--branch",
        metavar="NAME",
        help="the branch this dispatch's commits must land on (KYO-32; default: none "
        "declared). Recorded on the dispatch, stated in the frozen brief, and enforced "
        "by `kyo report`, which refuses a report whose commits[] are not reachable from it. "
        "Without --worktree, the dispatch's worktree is derived under this project's Kyosei "
        "home and CREATED there with `git worktree add` in the code repository (KYO-485): "
        "`-b NAME` when the branch does not exist, the branch checked out when it does",
    )
    p_dispatch.add_argument(
        "--no-create-worktree",
        action="store_true",
        help="with --branch: record and derive the worktree path but do not run "
        "`git worktree add`; the command is printed for you to run instead (the "
        "behaviour before KYO-485)",
    )
    p_dispatch.add_argument(
        "--base",
        metavar="REF",
        help="where this dispatch's work starts (KYO-485): the commit REF names becomes "
        "the dispatch's recorded base sha and is stated in the brief, and a created "
        "worktree's new branch is cut there. For a re-dispatch deliberately cut from a "
        "predecessor's unmerged branch. Default: the code repository's HEAD",
    )
    p_dispatch.add_argument(
        "--model",
        metavar="NAME",
        help="the model this dispatch's worker runs on (KYO-61), overriding the kind's "
        "policy.worker_model and the project's worker_model in kyosei/config.yml. "
        "Recorded on the dispatch and stated in the frozen brief; NOT enforced — Kyosei "
        "does not spawn workers, so nothing can make a harness honour it",
    )
    p_dispatch.add_argument(
        "--brief-out",
        metavar="FILE",
        help="also write the frozen brief to this file, at the moment this invocation "
        "freezes it — not a way to re-read a brief that already exists (KYO-177): "
        "`kyo show <dispatch-id>` does that, read-only, for any dispatch already on record",
    )
    p_dispatch.add_argument(
        "--print-brief",
        action="store_true",
        help="print the whole frozen brief to the terminal too (the old default), in place "
        "of the length/digest line naming `kyo show <dispatch-id>` (KYO-326); --brief-out "
        "and --json are unaffected either way",
    )
    p_dispatch.add_argument(
        "--task-file",
        metavar="FILE",
        help="orchestrator-written task-specific prose for the brief, from a file or '-'",
    )
    p_dispatch.add_argument(
        "--also-covers",
        nargs="+",
        # KYO-476: `extend`, not the default `store`, which kept only the last
        # occurrence of a repeated flag and said nothing (a consumer report).
        action="extend",
        metavar="RECORD_ID",
        help="additional record id(s) this dispatch formally covers (KYO-12 fix): their "
        "criteria are appended to the frozen brief, and `kyo report` will accept "
        "criteria_verdicts naming any of them, not just the primary record. May be "
        "repeated: `--also-covers A --also-covers B` is `--also-covers A B`, and an id "
        "named twice is covered once",
    )
    # KYO-345: the two ways a dispatch can end with no report, and they are different
    # facts — one worker died mid-work, the other was never spawned. Mutually exclusive
    # in the parser rather than in `cmd_dispatch_close`, because argparse already
    # refuses the pair by name at exit 64 and a hand-written refusal would be a second
    # statement of the same rule for a reader to find (law 3).
    p_dispatch_close_state = p_dispatch.add_mutually_exclusive_group()
    p_dispatch_close_state.add_argument(
        "--terminated-no-report",
        action="store_true",
        help="with 'close': mark closure with no ingested report because the work "
        "STOPPED — a worker was spawned and died, crashed or was cut off (law 8). "
        "Requires --why, and stays loud in `kyo brief` until the record is answered",
    )
    p_dispatch_close_state.add_argument(
        "--withdrawn",
        action="store_true",
        help="with 'close': mark closure with no ingested report because the dispatch "
        "was never handed to a worker at all — a brief frozen by mistake, which a "
        "frozen brief cannot have amended out of it (KYO-345). Requires --why. Not a "
        "death: it is listed nowhere in `kyo brief`, and `kyo show <record-id>` prints "
        "it in the record's dispatch history under its own word",
    )
    _add_reason_flags(p_dispatch, "for --terminated-no-report or --withdrawn")

    p_worktrees = sub.add_parser(
        "worktrees",
        parents=[common],
        help="list dispatch worktrees, and remove the ones whose dispatch is finished (KYO-70)",
    )
    p_worktrees.add_argument(
        "--prune",
        action="store_true",
        help="actually remove them (law 11: listing is the dry run). Refuses any worktree "
        "holding uncommitted work — that directory may be the only surviving copy of a "
        "dead worker's output — leaves one whose branch is not merged into the base "
        "(KYO-207), and never touches a running dispatch's, or any branch",
    )
    p_worktrees.add_argument(
        "--include-unmerged",
        action="store_true",
        help="with --prune: remove a finished worktree even though its branch is not "
        "contained in the base (or git could not tell). A dispatch that has reported is "
        "not a dispatch anybody has merged, and the rehearsal of that branch runs in "
        "that directory (KYO-207)",
    )
    p_worktrees.add_argument(
        "--against",
        metavar="REF",
        help="the base a worktree's branch must be contained in to count as merged "
        "(default: main) — name your trunk here if it is called something else",
    )
    # KYO-223.c2. A row whose dispatch is finished and whose directory is already gone
    # prints its unaudited-stop COUNT and not the ids, because the ids are the same ids
    # on every such row and six of them buried the rows that still needed a decision.
    # The detail KYO-134 added is not removed — it is behind this word, and always in
    # `--json`.
    p_worktrees.add_argument(
        "--expand-stops",
        action="store_true",
        help="name the unaudited stop events on finished-and-gone rows too, instead of "
        "printing their count, and print the stop-audit census (per-dispatch counts by "
        "reason, then each unmatched stop with its candidates) that the default output "
        "folds into one line (KYO-423); --json carries them either way",
    )

    # KYO-454: the read-only views of what the desktop app read by SQL
    # and git. Plural nouns beside the singular verbs that write (`brief`, `gate`), and
    # `activity` for the desktop view whose rows it is.
    p_briefs = sub.add_parser(
        "briefs",
        parents=[common],
        help="list the session briefs the store kept, newest first, or show one with the "
        "shift notes and ledger entries between it and the brief before; read-only, "
        "unlike `kyo brief` (KYO-454)",
    )
    p_briefs.add_argument(
        "brief_id",
        nargs="?",
        metavar="BRIEF_ID",
        help="one brief run's id (PREFIX-Bn): print it, the previous brief's id, and what "
        "lies between the two",
    )
    p_briefs.add_argument(
        "--latest",
        action="store_true",
        help="the newest brief run, as BRIEF_ID would print it",
    )
    p_shift_notes = sub.add_parser(
        "shift-notes",
        parents=[common],
        help="list the shift log newest first, each note marked with the session-start "
        "brief it handed off to; read-only (KYO-454)",
    )
    p_shift_notes.add_argument(
        "--latest",
        action="store_true",
        help="only the newest shift note, including one written after the last brief",
    )
    p_gates = sub.add_parser(
        "gates",
        parents=[common],
        help="list every open gate across every record, with the record it is on, and "
        "each record's open-gate count; read-only (KYO-454)",
    )
    p_gates.add_argument(
        "--type",
        action="append",
        choices=GATE_TYPES,
        metavar="TYPE",
        help=f"only gates of this type (repeatable): {', '.join(GATE_TYPES)}",
    )
    p_activity = sub.add_parser(
        "activity",
        parents=[common],
        help="the dispatch activity rows: every running dispatch and the newest finished "
        "ones, with record, verdict counts, worker tokens, end time and what git says "
        "about each worktree; read-only (KYO-454)",
    )
    p_activity.add_argument(
        "--finished",
        type=int,
        metavar="N",
        help="how many finished dispatches to show beside every running one "
        f"(default: {cmd_worktrees.ACTIVITY_FINISHED_DEFAULT})",
    )
    p_activity.add_argument(
        "--against",
        metavar="REF",
        help="the branch a worktree's HEAD is asked to be contained in (default: main)",
    )

    p_drift = sub.add_parser(
        "touch-drift",
        parents=[common],
        help="compare a dispatch's declared touches against what its branch actually "
        "changed (KYO-71) — reports, never refuses",
    )
    p_drift.add_argument("dispatch_id", metavar="DISPATCH_ID")
    p_drift.add_argument(
        "--against",
        metavar="REF",
        help="the ref to diff from (default: main), same three-dot diff `kyo check-branch` uses",
    )

    p_report = sub.add_parser(
        "report", parents=[common], help="ingest a dispatch's structured report (M5)"
    )
    p_report.add_argument("dispatch_id", metavar="DISPATCH_ID")
    p_report.add_argument(
        "--file",
        required=True,
        metavar="FILE",
        help="the report JSON, validated against the schema `kyo report-schema` prints",
    )
    # KYO-477: a worker's dry run. Same checks as the ingest, through the same
    # functions, and nothing written -- so it runs from a dispatch worktree, whose
    # store is the main checkout's opened read-only (KYO-342).
    p_report.add_argument(
        "--check",
        action="store_true",
        help="run every check the ingest runs before writing, print any problem, write "
        "nothing; exits non-zero when the report would be refused (KYO-477)",
    )

    # KYO-149: its own subcommand rather than `kyo report --schema`, and the reason is
    # the reader, not tidiness. `report` is PROJECT_REQUIRED and takes a required
    # DISPATCH_ID and a required --file; a `--schema` flag on it would have to make
    # both optional — loosening argparse's guarantees on the one command that ingests
    # a worker's account — and would still refuse to run for a worker whose worktree
    # has no store above it, which is exactly the reader the flag would exist for.
    #
    # Nor `kyo schema report`: `schema` is already a taken word here. `schema_version`
    # is a store concept the compat machinery and `kyo doctor` both surface, so a top
    # -level `kyo schema` noun would read as "this project's schema version" to the
    # only people who use this CLI. `report-schema` invents no noun and follows the
    # hyphenated shape already established by `check-branch`, `check-clean`,
    # `check-push`, `touch-drift`, `install-hooks` and `install-skill`.
    sub.add_parser(
        "report-schema",
        parents=[common],
        help="print the report schema `kyo report` validates against — from the "
        "installed package, no project needed (KYO-149)",
    )

    # KYO-331. A positional dispatch id and opt-in flags, and there is deliberately no
    # `--only`: the composed commands have one each, and a flag here would have to
    # decide which of them it selects from. A controller who wants part of a report runs
    # the step itself, which is what this command prints beside every step it runs.
    # KYO-343 adds the one exception worth making: `--except` narrows the criteria step
    # alone, by handing `kyo verify --from-report` the complement as its own `--only` —
    # so the flag this command still declines to have is answered by composing the flag
    # the composed command already has, rather than by growing a second selector.
    p_accept = sub.add_parser(
        "accept",
        parents=[common],
        help="apply an ingested report you have read — its criteria verdicts, its docs "
        "verdicts and its touch-drift correction — as one act (KYO-331)",
    )
    p_accept.add_argument(
        "dispatch_id",
        metavar="DISPATCH_ID",
        help="the dispatch whose ingested report to apply; `kyo report <id> --file "
        "<report.json>` is what ingests one, and this applies what that stored",
    )
    p_accept.add_argument(
        "--reviewed",
        action="store_true",
        help="say that you have read this report's premise_false[] and disagreements[]; "
        "without it, a report carrying either is refused and both are printed. Never "
        "defaulted: reading them is the orchestrator's act, and the tool cannot do it",
    )
    p_accept.add_argument(
        "--close",
        action="store_true",
        help="afterwards, move each covered record whose criteria are all settled to its "
        "completion status, through the ladder's intermediate rungs, and say which "
        "records were left and why",
    )
    p_accept.add_argument(
        "--except",
        dest="except_ids",
        metavar="CRITERION_ID",
        nargs="+",
        help="apply every verdict this report carries except these criteria's — passed "
        "to `kyo verify --from-report` as the complement, as its own --only. The "
        "excepted verdicts stay unapplied and readable in the report; --close leaves a "
        "record that holds one of them unmoved, naming it as the reason (KYO-343)",
    )

    # KYO-494 (KYO-481). `--quote` has a `--quote-file` twin, so the residue check
    # refuses a mangled one rather than warning (KYO-91); there is no --verdict or
    # --method, because a confirmation records met, human-observed, and nothing else.
    p_confirm = sub.add_parser(
        "confirm",
        parents=[common],
        help="the maintainer looked and approves: verify the criterion met "
        "(human-observed) with their words as evidence, clear the answered human gate, "
        "and walk the record to completion (KYO-494)",
    )
    p_confirm.add_argument("record_id", metavar="RECORD_ID")
    p_confirm.add_argument(
        "--quote",
        metavar=PROSE_ARGUMENT_METAVAR,
        help="the maintainer's words, when they fit on one line",
    )
    p_confirm.add_argument(
        "--quote-file",
        metavar="FILE",
        help="the maintainer's words from a file, or '-' for stdin; stored verbatim",
    )
    p_confirm.add_argument(
        "--criterion",
        metavar="CRITERION_ID",
        nargs="+",
        help="the criteria the words answer; overrides resolution from the gate's payload "
        "or the record's sole unmet-unobserved criterion",
    )
    p_confirm.add_argument(
        "--gate",
        metavar="GATE_ID",
        help="the awaiting:human-* gate the words answer; needed only when more than one "
        "is open",
    )
    p_confirm.add_argument(
        "--no-close",
        action="store_true",
        help="record the words and clear the gate, but leave the record's status alone",
    )

    # KYO-536. The project-specific half (which suite, which generators, which
    # after-steps) is `close_wave:` in kyosei/config.yml, so the flags here are only the
    # ones that change what THIS run does.
    p_close_wave = sub.add_parser(
        "close-wave",
        parents=[common],
        help="close a merged wave: move each record to completion, run the declared "
        "generators, commit store and generated files together, then print (or, with "
        "--run-after, run) the declared after-steps — refused before any write unless "
        "every record can finish and the declared suite passed at this code (KYO-536)",
    )
    p_close_wave.add_argument(
        "record_ids",
        metavar="RECORD_ID",
        nargs="*",
        help="the records the wave finished; each is walked up the status ladder by "
        "`kyo move`. A record already at completion is left where it is",
    )
    p_close_wave.add_argument(
        "--suite-passed",
        metavar="COMMAND",
        help="record that this whole-suite command just passed at HEAD -- chain it on the "
        'suite\'s own exit status: `make test && kyo close-wave --suite-passed "make '
        'test"`. Must be a declared `close_wave: suite:` or `orchestrator_only_commands` '
        "entry; refused while anything outside kyosei/ and ideas/ is uncommitted. Kept "
        "in the checkout's git directory, never tracked",
    )
    p_close_wave.add_argument(
        "--dry-run",
        action="store_true",
        help="print the whole plan -- what it would refuse, move, run, commit -- and "
        "write nothing",
    )
    p_close_wave.add_argument(
        "--run-after",
        action="store_true",
        help="also run the `close_wave: after:` steps (reinstall, restart, prune), in "
        "order, stopping at the first that fails; without it they are printed only",
    )

    p_split = sub.add_parser(
        "split",
        parents=[common],
        help="split unmet criteria to a new successor record, back-linked both ways (M5)",
    )
    p_split.add_argument("id", metavar="RECORD_ID")
    p_split.add_argument(
        "--to-successor",
        nargs="+",
        required=True,
        metavar="CRITERION_ID",
        help="unmet criterion id(s) to carry to the successor; a met or void one is refused",
    )

    p_sweep = sub.add_parser(
        "sweep",
        parents=[common],
        help="flag stale touches and near-duplicate criteria; never edits (M6)",
    )
    p_sweep.add_argument(
        "--hook",
        action="store_true",
        help="how the packaged Stop hook invokes this: exactly the bare command inside a "
        "Kyosei project, and silent with exit 0 where none resolves (KYO-591)",
    )

    p_flag = sub.add_parser(
        "flag", parents=[common], help="list, acknowledge/resolve a sweep flag (M6)"
    )
    p_flag.add_argument(
        "action",
        choices=["list", "resolve", "collapse"],
        help=(
            "list: every open flag, one line each (--all includes resolved ones). "
            "resolve: acknowledge and clear one flag, or with --settled, every open "
            "staleness flag under a record already at a completion or retired status "
            "(KYO-346; dry run without --apply). "
            "collapse: merge each record's duplicate touch-changed flags into its "
            "earliest one and close the rest as superseded (KYO-69; dry run without "
            "--apply)"
        ),
    )
    # KYO-69: optional because `collapse` operates on the whole store, not one flag.
    # KYO-220: and so does `list`. KYO-346: and so does `resolve --settled`.
    # `cmd_flag` refuses each action with the wrong shape by name rather than letting
    # argparse say something generic about a positional argument.
    p_flag.add_argument("flag_id", metavar="FLAG_ID", nargs="?", default=None)
    # KYO-528.c1: `resolve` takes several ids as one act with one --why.
    p_flag.add_argument(
        "more_flag_ids",
        metavar="FLAG_ID",
        nargs="*",
        default=[],
        help="resolve only: further flags to resolve in the same act, with the one --why; "
        "an unknown or already-resolved id refuses the whole call before anything is written",
    )
    p_flag.add_argument(
        "--apply",
        action="store_true",
        help="collapse/resolve --settled only: actually write it (default is a dry run)",
    )
    # KYO-220.c1. Open-only by default, because the question the listing answers is
    # "what is still owed a judgment"; resolved flags are history and history is opt-in.
    p_flag.add_argument(
        "--all",
        action="store_true",
        help="list only: include flags that have already been resolved",
    )
    # KYO-346.c3: the repair for a store that accumulated staleness flags under
    # records that had already settled before the completion move learned to close
    # them itself (c1). Takes no flag id — like `collapse`, it operates on every
    # record in the store at once — and, unlike a single `resolve`, requires --why:
    # this is an operator repairing old debt, not a human judging one finding.
    p_flag.add_argument(
        "--settled",
        action="store_true",
        help="resolve only: every open staleness flag under a record already at a "
        "completion or retired status, as one act (KYO-346; dry run without --apply, "
        "requires --why)",
    )
    _add_reason_flags(p_flag, "for resolving it (required with --settled)")

    sub.add_parser(
        "next",
        parents=[common],
        help="ready work: dispatchable, ungated, disjoint from running dispatches (M6)",
    )

    p_brief = sub.add_parser(
        "brief", parents=[common], help="the session briefing, derived from the store (M6)"
    )
    p_brief.add_argument(
        "--compact", action="store_true", help="condensed rendering for hook injection"
    )
    # KYO-94: the store's only session concept. The `SessionStart` hook is the one thing
    # that knows a session began, and until this flag existed that fact never reached the
    # store — so "the shift note authored before this session began" was not a question
    # `kyo brief` could answer, and a handoff note was surfaced once and then unreachable.
    p_brief.add_argument(
        "--session-start",
        action="store_true",
        help="record this brief as the start of a session (what the SessionStart hook "
        "runs); anchors the handoff note every later brief in the session re-surfaces",
    )

    p_note = sub.add_parser(
        "note",
        parents=[common],
        help="append to the continuously-written shift log (M6); for a note ON a record "
        "see `kyo edit --note-file`",
    )
    # KYO-131: `--shift` is still the only note kind `kyo note` writes, and a stray
    # positional is still refused — but both refusals are made by `cmd_note`, not by
    # argparse, so they can say where to go instead.
    #
    # A controller who meets `RECORD.nN` in `kyo redact --help` reasonably tries
    # `kyo note <record> --file ...`. argparse answered that with "the following
    # arguments are required: --shift" — byte-identical to the answer for a plain
    # missing flag, naming no way forward, and not even acknowledging the record id it
    # had just been handed. A consumer followed exactly that path and concluded
    # record-scoped notes did not exist, then filed the id shape as a defect. This is
    # the third surface where that reader arrives with the question already formed, so
    # it is the third surface that has to answer it.
    p_note.add_argument("record", nargs="?", help=argparse.SUPPRESS)
    p_note.add_argument("--shift", action="store_true", help="the only note kind v1 ships")
    p_note.add_argument(
        "--file",
        metavar="FILE",
        help="the shift note's text from a file, or '-' for stdin (law 5)",
    )

    # KYO-184. No flags of its own, deliberately: the question has one answer and
    # every part of it is in the payload, so there is nothing to select. `--root` and
    # `--json` come from `common`, and `--root` is how a consumer asks about a
    # directory other than the one it is standing in.
    sub.add_parser(
        "where",
        parents=[common],
        help="locate this project and say whether this build can open its store (KYO-184)",
    )

    p_export = sub.add_parser(
        "export", parents=[common], help="rewrite drifted export documents from the db (KYO-6)"
    )
    p_export.add_argument(
        "--refresh",
        action="store_true",
        help="rewrite every record's export document from the database, repairing drift "
        "a format-version bump left behind; required (kyo export alone is a usage error)",
    )
    p_export.add_argument(
        "--force",
        action="store_true",
        help="refresh even where kyosei/export/ is ahead of the database, overwriting the "
        "newer side with the older one (KYO-81); the mirror of kyo rebuild --force",
    )
    p_export.add_argument(
        "--migrate-events",
        action="store_true",
        help="rewrite a monolithic kyosei/export/_events.jsonl as bounded shards under "
        "kyosei/export/_events/ and remove the old file, in one run (KYO-268); every line "
        "moves verbatim, and an export already sharded is a no-op that says so",
    )

    p_rebuild = sub.add_parser(
        "rebuild", parents=[common], help="reconstruct kyosei.db from kyosei/export/"
    )
    p_rebuild.add_argument(
        "--force", action="store_true", help="overwrite an existing kyosei.db"
    )

    p_doctor = sub.add_parser(
        "doctor", parents=[common], help="run the tool's own invariant checks"
    )
    p_doctor.add_argument(
        "--hook",
        action="store_true",
        help="print ONE line to stderr instead of the whole report, and write the "
        "report to doctor-report.txt beside kyosei.db; how the packaged Stop / "
        "SessionEnd hook invokes this (KYO-362). The same checks run and the exit "
        "status is unchanged — silent, and the report file removed, when nothing "
        "fails",
    )

    # KYO-138. Registered LAST on purpose: it renders whatever `sub` holds at the moment
    # it runs, so its position in this function is irrelevant to what it can see — and
    # putting it here means the next person to add a command adds it above this line and
    # is rendered anyway, with no edit to the generator.
    p_reference = sub.add_parser(
        "reference",
        parents=[common],
        help="print the command-and-option reference — all of it, or one command's "
        "section — generated from this parser; no project needed (KYO-138, KYO-221)",
    )
    # KYO-221. The whole reference is 1,000+ lines: right for a file and wrong for a
    # terminal, where the question is almost always about one command. The section is
    # the same bytes the full document carries, sliced by `reference_markdown`, so this
    # cannot become a second, thinner description of the same parser.
    # The dest is `of_command` and not `command`: the top-level subparser already owns
    # `args.command` (it is how `main` finds the handler), and a positional named the
    # same thing overwrites it with its own value and sends the dispatcher looking for a
    # command called `show`. Found by running it, not by reading it.
    p_reference.add_argument(
        "of_command",
        metavar="COMMAND",
        nargs="?",
        default=None,
        help="print only this command's section of the reference; omit it for the whole "
        "document. An unknown name is refused with the list of known ones",
    )
    p_reference.add_argument(
        "--output",
        metavar="FILE",
        help=f"write the reference to this file instead of stdout; Kyosei's own "
        f"committed copy is {REFERENCE_PATH} (in Kyosei's own repository), where a test "
        f"fails if it falls behind the parser — nothing checks yours (KYO-156)",
    )

    # KYO-226. Registered after `reference` and for the same reason it is registered
    # last: neither command knows any other command's name, so where they sit in this
    # function decides nothing.
    sub.add_parser(
        "version",
        parents=[common],
        help="print which build this copy is — package version, the commit it was built "
        "from and that commit's date; no project needed (KYO-226)",
    )

    p_changelog = sub.add_parser(
        "changelog",
        parents=[common],
        help="print what changed in Kyosei: one line per completed record, grouped by "
        "scope, newest first; defaults to what is newer than the build you are running; "
        "no project needed (KYO-226)",
    )
    # NOT a timestamp flag, and law 16 is untouched: this filters a read and nothing
    # about it is ever stored. `tests/test_time.py` quantifies over option *spellings*,
    # and `--since` is deliberately not spelled `--since-date`.
    p_changelog.add_argument(
        "--since",
        metavar="DATE-OR-COMMIT",
        help="list only records completed on or after this point — a YYYY-MM-DD date "
        "(UTC), a UTC timestamp such as a build's commit_time from `kyo version --json` "
        "(strictly after it), or a commit this checkout can resolve (an installed build "
        "has no checkout, so give it a date or a timestamp). Omit it and the window is the "
        "build you are running, which `kyo version` prints; every record listed is in "
        "the build printing it",
    )
    p_changelog.add_argument(
        "--all",
        action="store_true",
        help="every completed record ever, ignoring the default window; refused together "
        "with --since, which asks for a different one",
    )
    p_changelog.add_argument(
        "--output",
        metavar="FILE",
        help=f"write the changelog to this file as markdown instead of printing it; "
        f"Kyosei's own committed copy is {changelog_module.CHANGELOG_PATH} (in Kyosei's "
        f"own repository), where a test fails if it falls behind the store — nothing "
        f"checks yours",
    )
    return parser


# ------------------------------------------- KYO-138: the reference, derived not written
#
# The industry answer to "where is the list of every command and flag" is not a wiki
# page somebody keeps up: Cobra emits `GenMarkdownTree`, Python projects run
# `sphinx-argparse` or `argparse-manpage`, Click has `sphinx-click`. Generation is
# categorically stronger than the drift *detection* KYO-137 built, because there is no
# window in which the reference is wrong — it cannot omit a flag nobody remembered.
#
# That is not an abstract preference here. KYO-131 was filed because `kyo edit
# --note-file` is the only writer of `RECORD.nN` notes and no prose surface named it, so
# a consumer read `kyo redact --help`, found the id shape in its vocabulary, could find
# no command that produced one, and wrote the object up as unproducible. It had been
# producible the whole time. A reference derived from the parser lists every flag of
# every command whether or not anyone remembered it, so that gap would have been visible
# without anybody noticing it was missing.
#
# This lives in `cli.py`, beside `prose_argument_flags`, for that function's reason: both
# are derived facts about the shipped parser, and the parser is the only authority either
# one may consult. `cmd_views.cmd_reference` is the thin command that prints what this
# returns; `tests/test_kyo138_command_reference.py` is what fails when the committed copy
# and the parser disagree.

#: Where this repository keeps its committed copy, root-relative and posix-style.
#:
#: Chosen so that `docs_check`'s DEFAULT_DOCUMENT_GLOBS (`*.md`, `docs/**/*.md`) already
#: reaches it (KYO-138.c3): docs mode renders it and `kyo base run --source docs` queries
#: it with no second format and no `documents.yml` entry. It is a path in *this* project,
#: not a promise about a consumer's — `kyo reference` prints to stdout by default and a
#: consumer redirects wherever their documents live.
REFERENCE_PATH = "docs/reference/cli.md"

#: The exact command that regenerates the committed copy. Named here once so the
#: generated file's own header, and the test that fails on a stale copy, quote the same
#: string — an error message naming a remedy has to name one that works (law 1).
REFERENCE_REGENERATE_COMMAND = f"kyo reference --output {REFERENCE_PATH}"

#: Deliberately NOT a `governs:` line in the generated file. A `governs:
#: src/kyosei/cli.py` section would raise a KYO-137 docs obligation every time `cli.py`
#: changed for any reason, and the obligation's remedy is a controller act (`kyo docs
#: settle`) applied to a file no human maintains. The equality check in
#: `tests/test_kyo138_command_reference.py` is strictly stronger than governs-staleness
#: for a generated artifact — it compares the bytes against what the parser yields right
#: now, rather than comparing two git timestamps — so the weaker mechanism would only add
#: noise. Stated here rather than left to be inferred from the absence of a line.
REFERENCE_DECLARES_GOVERNS = False


# ------------------------------------------------------------------------ KYO-194
#
# Where a write command's `--json` payload puts the id it minted.
#
# Measured on main before this change: `kyo new`, `kyo promote`, `kyo spawn`
# and `kyo edit` put the minted record id at `record.id` and nowhere else; `kyo claim`
# put `claim_id` at the top level and `kyo attach` put `evidence_id` there; `kyo link`
# nested the link id under a `link` object. A consumer's
# `kyo new bug --json | jq .id` printed `null` three times in one day and the id was
# recovered from `kyo list` instead — which is the act the controller-audience rule
# "never state a tool-derivable fact from memory" is about, since a listing is a human
# surface and reading an id off it three times is three chances to transpose one.
#
# The consumer was not wrong about the convention. There was no convention: two
# commands that mint a child id put it at the top level, four that mint or address a
# record id did not, and `kyo link` did a third thing. A script written against
# `kyo claim` failed against `kyo new`, and the failure was `None`, not an error.
#
# The convention, in one sentence: **an object in the payload has its id beside it at
# the top level**. Nothing was removed to get there — `record` and `link` are still the
# whole object — and the keys are the ones `claim_id`/`evidence_id` already established.


#: A payload key holding an object, and the top-level key that must carry that object's
#: id beside it. Two entries because those are the two objects a command's payload
#: nests today; a third nested object added later fails
#: ``test_every_command_payload_carries_its_minted_id_at_the_top_level`` until it is
#: named here, which is the point.
PAYLOAD_ID_CONVENTION: dict[str, str] = {"record": "record_id", "link": "link_id"}


def command_payload_keys() -> dict[str, tuple[str, ...]]:
    """Every command's ``--json`` payload, as ``{command: its top-level literal keys}``.

    Computed once per process and copied out (KYO-578): ``kyo reference`` asks once
    per command, and re-parsing every source file each time cost about 14 s of its
    15. The answer depends only on this package's own source, which a running
    process cannot change.

    Read from this package's own sources with :mod:`ast`, not from a list kept here.
    That is the property KYO-194.c2 asks for and the only one worth having: a list
    could omit the command that was added yesterday, which is exactly how the store
    ended up with three conventions and no convention. The key is the payload's own
    ``"command"`` value — ``"dispatch close"``, ``"flag resolve"``, ``"docs settle"``
    included — so the mapping is keyed by the same string every payload already
    carries, rather than by a parser name that would need translating.

    **Honest scope**, because the answer is static and the payloads are not:

    * only ``Output(...)`` calls whose payload is a **dict literal** carrying a
      constant ``"command"`` are seen. ``kyo sweep``, ``kyo brief`` and
      ``kyo base run`` build theirs by spreading ``**data``, so their spread keys are
      invisible here. None of the three nests a ``record`` or a ``link`` today, and if
      one grows a nested object this function will not notice — a real hole, named
      rather than papered over.
    * a command whose handler builds more than one payload (``kyo move`` has a
      no-op branch and a transition branch, ``kyo check-push`` has three) is reported
      as the **intersection** of its variants: the keys every one of them carries. A
      union would let one branch's ``record_id`` speak for a sibling that omits it,
      which is the shape of the defect, not the fix.

    A frozen build's source is what is read. ``pyproject.toml`` ships ``src/kyosei``
    as an ordinary package, so the ``.py`` files are present in an installed consumer
    exactly as they are here; a build where they are not returns ``{}`` and every
    caller below degrades to saying nothing rather than to saying something wrong.
    """
    return dict(_command_payload_keys_once())


@functools.cache
def _command_payload_keys_once() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """:func:`command_payload_keys`' scan itself, cached as an immutable value."""
    import ast

    per_command: dict[str, list[frozenset[str]]] = {}
    order: dict[str, list[str]] = {}
    for source in sorted(pathlib.Path(__file__).resolve().parent.glob("*.py")):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover - a build without sources
            continue
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Output"
            ):
                continue
            for argument in [*node.args, *(kw.value for kw in node.keywords)]:
                if not isinstance(argument, ast.Dict):
                    continue
                keys = [
                    key.value
                    for key in argument.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                ]
                command = next(
                    (
                        value.value
                        for key, value in zip(argument.keys, argument.values, strict=True)
                        if isinstance(key, ast.Constant)
                        and key.value == "command"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    ),
                    None,
                )
                if command is None:
                    continue
                per_command.setdefault(command, []).append(frozenset(keys))
                order.setdefault(command, keys)
    return tuple(
        (command, tuple(key for key in order[command] if key in frozenset.intersection(*variants)))
        for command, variants in per_command.items()
    )


def command_payload_id_keys() -> dict[str, tuple[str, ...]]:
    """The minted-id keys of every command's payload: :func:`command_payload_keys`
    filtered to the keys ending in ``_id``.

    One function feeds both consumers KYO-194 has — the reference section rendered by
    :func:`_command_section`, and the test that fails when a payload drops one — so
    the reference cannot state a key the payload does not carry, and cannot omit one
    it does. That is the whole of c3: a script author reads the key from
    ``kyo reference`` rather than from a `None`.
    """
    return {
        command: tuple(key for key in keys if key.endswith("_id"))
        for command, keys in command_payload_keys().items()
    }


def _collapse(text: str) -> str:
    """One line, single-spaced. Applied to every help string and to argparse's own
    ``format_usage`` output.

    The usage half is load-bearing for KYO-138.c4, not cosmetic.
    :class:`argparse.HelpFormatter` wraps usage to ``shutil.get_terminal_size()``, so
    the same parser formats to different *bytes* under a different ``COLUMNS`` — and a
    generated file whose content depends on the terminal that generated it would make
    the committed-copy check fail for a reason that has nothing to do with the parser.
    Collapsing whitespace removes the wrapping without removing a token: argparse emits
    prog, then optionals, then positionals, in that order in every one of its three
    width branches. ``test_the_render_does_not_depend_on_terminal_width`` pins it.
    """
    return " ".join(text.split())


def _escape_cell(text: str) -> str:
    """Markdown-table-safe: a ``|`` inside help text would otherwise open a column."""
    return _collapse(text).replace("|", "\\|")


def _value_form(action: argparse.Action) -> str:
    """How this option's value is written on a command line, or ``""`` for a switch.

    Derived from the action rather than from a table of flag names: ``nargs=0`` is a
    switch, an explicit ``metavar`` wins, a ``choices`` list renders as argparse itself
    renders one, and the fallback is argparse's own uppercased ``dest``.
    """
    if action.nargs == 0:
        return ""
    if action.metavar:
        base = action.metavar
    elif action.choices:
        base = "{" + ",".join(str(choice) for choice in action.choices) + "}"
    else:
        base = action.dest.upper()
    if action.nargs == "+":
        return f"{base} [{base} ...]"
    if action.nargs == "*":
        return f"[{base} ...]"
    if action.nargs == "?":
        return f"[{base}]"
    if isinstance(action.nargs, int):
        return " ".join([base] * action.nargs)
    return base


def _option_line(action: argparse.Action) -> str:
    """One bullet for one option: every spelling, its value, whether it is required
    (KYO-138.c5), and its help text verbatim."""
    value = _value_form(action)
    spellings = ", ".join(
        f"`{option}{' ' + value if value else ''}`" for option in action.option_strings
    )
    necessity = "**required**" if action.required else "optional"
    help_text = _collapse(action.help or "")
    tail = f" {help_text}" if help_text else ""
    return f"- {spellings} — {necessity}.{tail}"


def _positional_line(action: argparse.Action) -> str:
    """One bullet for one positional argument. ``nargs`` of ``?`` or ``*`` is how
    argparse spells "optional" for a positional; there is no ``required`` attribute to
    read, which is why this does not share :func:`_option_line`'s answer."""
    name = _value_form(action) or action.dest.upper()
    optional = action.nargs in ("?", "*")
    necessity = "optional" if optional else "**required**"
    help_text = _collapse(action.help or "")
    tail = f" {help_text}" if help_text else ""
    return f"- `{name}` — {necessity}.{tail}"


def _visible(actions: list[argparse.Action]) -> list[argparse.Action]:
    """Every action a reader can actually type, in declaration order.

    Drops ``help=SUPPRESS`` actions (a flag the tool hides is not a flag the reference
    should advertise) and the subparsers action itself, which is a container rather than
    an argument.
    """
    return [
        action
        for action in actions
        if action.help is not argparse.SUPPRESS
        and not (isinstance(action.choices, dict) and action.choices)
    ]


def _subcommand_action(parser: argparse.ArgumentParser) -> argparse.Action | None:
    """The one action holding ``name -> subparser``. Same discovery
    :func:`prose_argument_flags` and ``tests/test_skill_cli_sync.py`` use."""
    return next(
        (a for a in parser._actions if isinstance(a.choices, dict) and a.choices), None
    )


def _subcommand_subjects(action: argparse.Action) -> dict[str, str]:
    """Each subcommand's one-line subject — the ``help=`` given to ``add_parser`` —
    read off the pseudo-actions argparse builds for its own command list.

    This is the "names each command's subject" half of KYO-138.c5, and it comes from
    the same place ``kyo --help`` gets it, so a command whose subject changes cannot
    have two answers.
    """
    return {
        choice.dest: _collapse(choice.help or "")
        for choice in getattr(action, "_choices_actions", [])
    }


def _canonical(action: argparse.Action) -> str:
    """The spelling to refer to an option by: its longest, so ``-h/--help`` is cited as
    ``--help`` rather than by whichever alias sorted first."""
    return max(action.option_strings, key=len)


def _universal_options(
    subparsers: dict[str, argparse.ArgumentParser],
) -> tuple[frozenset[str], list[argparse.Action]]:
    """The options every single subcommand accepts: the spellings, for membership, and
    one representative action apiece, for rendering.

    Derived by intersection, never listed. In this build that is ``-h/--help`` plus the
    three ``_add_common_flags`` puts on every subparser through ``parents=``; stating it
    as an intersection means a fourth common flag joins the hoisted section on the day it
    is added, and a flag that stops being universal drops out of it, with no edit here.

    Hoisted rather than repeated because repetition is what makes a reference unread: the
    same four bullets under all forty-odd commands would bury the flags that differ. They
    are still documented in full, once, and every command section points at them — so the
    document is complete (c1) without being noise.

    Representatives come from the first subparser in declaration order, and an action is
    only universal if *every* spelling it offers is — so a flag that grew a second alias
    on one command alone is reported per-command rather than hoisted with a spelling most
    commands do not accept.
    """
    if len(subparsers) < 2:
        return frozenset(), []
    common: set[str] | None = None
    for subparser in subparsers.values():
        here = {
            option for action in _visible(subparser._actions) for option in action.option_strings
        }
        common = here if common is None else (common & here)
    spellings = frozenset(common or set())
    first = next(iter(subparsers.values()))
    representatives = [
        action
        for action in _visible(first._actions)
        if action.option_strings and set(action.option_strings) <= spellings
    ]
    # Keep the two halves in agreement: a spelling nobody represents is not hoisted,
    # or the "also accepts" line would cite a flag the section below never documents.
    represented = frozenset(
        option for action in representatives for option in action.option_strings
    )
    return represented, representatives


def reference_command_names(parser: argparse.ArgumentParser | None = None) -> list[str]:
    """Every command name :func:`reference_markdown` documents, sorted (KYO-221.c1).

    Read off the parser, like everything else here, so the refusal for an unknown name
    lists what the build actually has rather than a hand-kept list that would document
    the parser it was written beside.
    """
    parser = parser if parser is not None else build_parser()
    sub_action = _subcommand_action(parser)
    return sorted(_canonical_subparsers(sub_action)) if sub_action else []


def _canonical_subparsers(action: argparse.Action) -> dict[str, argparse.ArgumentParser]:
    """``name -> subparser`` with each subparser once, under its canonical name.

    argparse puts an alias in ``.choices`` beside the name, pointing at the same parser
    object, and inserts the name first (KYO-519: `kyo controller` is an alias of
    `kyo orchestrator`). The reference documents each command once and names its aliases
    in that one section, rather than printing the same section twice.
    """
    seen: set[int] = set()
    out: dict[str, argparse.ArgumentParser] = {}
    for name, sub in dict(action.choices).items():
        if id(sub) in seen:
            continue
        seen.add(id(sub))
        out[name] = sub
    return out


def _subcommand_aliases(action: argparse.Action) -> dict[str, list[str]]:
    """canonical name -> the alias spellings argparse also accepts for it."""
    canonical = {id(sub): name for name, sub in _canonical_subparsers(action).items()}
    aliases: dict[str, list[str]] = {}
    for name, sub in dict(action.choices).items():
        if canonical[id(sub)] != name:
            aliases.setdefault(canonical[id(sub)], []).append(name)
    return aliases


def reference_markdown(
    parser: argparse.ArgumentParser | None = None, *, command: str | None = None
) -> str:
    """The complete command reference for ``parser``, as markdown (KYO-138.c1).

    With ``command``, ONE command's section of that same reference and nothing else
    (KYO-221). A consumer report found the full reference, over a thousand lines, right
    for a file and wrong for grep: a per-command section would have saved several round
    trips.

    The slice is the same :func:`_command_section` call the full document makes, with
    the same arguments, so what comes back is verbatim what a reader would have found by
    scrolling: not a second rendering with its own idea of what a command is, which is
    the way a per-command view normally drifts from the whole (and the way this project
    lost `kyo edit --note-file` from every prose surface — KYO-131, the reason
    `kyo reference` is generated at all).

    Takes a parser rather than always building one for the same reason
    :func:`prose_argument_flags` does: it makes the "no hand-maintained list" property
    *testable*. ``test_a_command_added_to_the_parser_appears_with_no_other_edit`` hands
    this a parser with a subcommand and a flag that do not exist in this build and
    asserts both are rendered — which is only possible because nothing in here knows any
    command's name.

    The output is markdown ``docs_check`` parses (c3): ordinary ATX headings, no fenced
    code blocks, and no line matching ``docs_parse._METADATA_RE`` — so every ``##``
    becomes a section, docs mode renders it, and ``kyo base run --source docs`` can query
    it without a second format.
    """
    parser = parser if parser is not None else build_parser()
    sub_action = _subcommand_action(parser)
    subparsers: dict[str, argparse.ArgumentParser] = (
        _canonical_subparsers(sub_action) if sub_action else {}
    )
    subjects = _subcommand_subjects(sub_action) if sub_action else {}
    aliases = _subcommand_aliases(sub_action) if sub_action else {}
    for canonical_name, spellings in aliases.items():
        if canonical_name in subjects:
            listed = ", ".join(f"`{parser.prog} {alias}`" for alias in spellings)
            subjects[canonical_name] = f"{subjects[canonical_name]} (also accepted as {listed})"
    universal_spellings, universal_actions = _universal_options(subparsers)

    if command is not None:
        command = next(
            (name for name, spellings in aliases.items() if command in spellings), command
        )
        if command not in subparsers:
            known = ", ".join(sorted(subparsers)) or "(this parser has no commands)"
            raise KyoseiError(
                f"{command!r} is not a command of `{parser.prog}`, so there is no "
                f"section of the reference to print. Known commands: {known}"
            )
        return (
            "\n".join(
                _command_section(
                    parser.prog,
                    command,
                    subparsers[command],
                    subjects,
                    universal_spellings,
                    universal_actions,
                )
            ).rstrip("\n")
            + "\n"
        )

    out: list[str] = []
    out.append(f"# `{parser.prog}` command reference")
    out.append("")
    # KYO-156: this header is read in two places at once — this repository, where
    # `docs/reference/cli.md` and the equality test are real files, and a consumer's
    # project, where neither exists. It was written for the first reader only, so it
    # told a consumer to regenerate into a directory they do not have and cited, as the
    # reason a stale copy cannot ship, a test file they cannot open. The fix is not to
    # go vague — "regenerate with `kyo reference`" is true everywhere and tells the
    # source-tree reader less than they need — but to name both paths exactly and mark
    # them, in the prose itself, as Kyosei's rather than the reader's: the same
    # UPSTREAM_MARKER wording KYO-149 built for worker-audience citations, and checked
    # by the same function. The closing sentence is the one a consumer always needed
    # and never had — nothing guards *their* copy.
    out.append(
        "**This file is generated from the argparse tree in `src/kyosei/cli.py`. Do not "
        "edit it by hand — regenerate it with `kyo reference --output <wherever you keep "
        f"it>`.** Kyosei's own copy is `{REFERENCE_PATH}` (in Kyosei's own repository), "
        f"regenerated with `{REFERENCE_REGENERATE_COMMAND}` and kept honest by "
        "`tests/test_kyo138_command_reference.py` (in Kyosei's own repository), which "
        "fails if that copy and the parser disagree. Nothing checks yours, so regenerate "
        "it after any upgrade (KYO-138.c4, KYO-156)."
    )
    out.append("")
    if parser.description:
        out.append(_collapse(parser.description))
        out.append("")

    # The root parser's own options — the ones accepted BEFORE a subcommand — and the
    # ones every subcommand accepts. In this build they are the same four flags, because
    # `_add_common_flags` puts each on both sides so that `kyo --json show X` and
    # `kyo show --json X` both work. That sameness is CHECKED here rather than assumed:
    # the two are separate action objects, and if they ever diverge the reference says
    # so in two sections instead of quietly documenting one and implying the other.
    root_options = [a for a in _visible(parser._actions) if a.option_strings]
    root_spellings = frozenset(o for a in root_options for o in a.option_strings)
    if root_options and root_spellings == universal_spellings:
        out.append("## Options accepted everywhere")
        out.append("")
        out.append(
            f"Each of these is accepted both before the command (`{parser.prog} --json "
            f"show ID`) and after it (`{parser.prog} show --json ID`). They are listed "
            "once here and not repeated under each command below; every command section "
            "names them by reference."
        )
        out.append("")
        out.extend(_option_line(action) for action in root_options)
        out.append("")
    else:
        if root_options:
            out.append("## Global options")
            out.append("")
            out.append(f"Accepted before the command, as in `{parser.prog} --json show ID`.")
            out.append("")
            out.extend(_option_line(action) for action in root_options)
            out.append("")
        if universal_actions:
            out.append("## Options accepted by every command")
            out.append("")
            out.append(
                "Accepted after the command by all of them. Listed once here and not "
                "repeated below; every command section names them by reference."
            )
            out.append("")
            out.extend(_option_line(action) for action in universal_actions)
            out.append("")

    if subparsers:
        out.append("## Command index")
        out.append("")
        out.append("| command | needs a project | writes to the store | subject |")
        out.append("| --- | --- | --- | --- |")
        for name in sorted(subparsers):
            handler = COMMAND_TABLE.get(name)
            needs = {
                PROJECT_REQUIRED: "yes",
                PROJECT_OPTIONAL: "optional",
                PROJECT_NONE: "no",
            }.get(handler[1], "?") if handler else "?"
            writes = "yes" if name in MUTATING_COMMANDS else "no"
            out.append(
                f"| [`{parser.prog} {name}`](#{_anchor(parser.prog, name)}) | {needs} | "
                f"{writes} | {_escape_cell(subjects.get(name, ''))} |"
            )
        out.append("")

    for name in sorted(subparsers):
        out.extend(
            _command_section(
                parser.prog,
                name,
                subparsers[name],
                subjects,
                universal_spellings,
                universal_actions,
            )
        )

    return "\n".join(out).rstrip("\n") + "\n"


def _anchor(prog: str, name: str) -> str:
    """The GitHub-style anchor for a ``## `kyo <name>``` heading — backticks and spaces
    dropped, so the index above actually links to the section below."""
    return f"{prog}-{name}".replace(" ", "-").replace("`", "").lower()


def _json_id_lines(prog: str, name: str) -> list[str]:
    """KYO-194.c3: the top-level id keys this command's ``--json`` payload carries.

    Generated from :func:`command_payload_id_keys`, which is the same function the test
    reads, so this cannot name a key the payload lacks and cannot omit one it has. It
    exists because a script author's only way to learn where the minted id lives used
    to be to guess, get ``None``, and go read it off ``kyo list``.

    Silent for a command whose payload mints nothing, and silent for a name the scan
    could not see (``reference_markdown`` is handed arbitrary parsers by its own test,
    and a build without ``.py`` sources yields an empty scan) — saying nothing is the
    only honest answer in both cases.
    """
    by_command = command_payload_id_keys()
    matching = {
        command: keys
        for command, keys in by_command.items()
        if (command == name or command.startswith(f"{name} ")) and keys
    }
    if not matching:
        return []
    if len(matching) == 1 and name in matching:
        listed = ", ".join(f"`{key}`" for key in matching[name])
        detail = f"Top-level ids in `--json`: {listed}."
    else:
        parts = [
            f"`{prog} {command} --json` carries "
            + ", ".join(f"`{key}`" for key in keys)
            for command, keys in sorted(matching.items())
        ]
        detail = "Top-level ids in `--json`: " + "; ".join(parts) + "."
    return [
        detail
        + " (KYO-194: an id the payload names an object for is repeated at the payload"
        + " root, so a script reads it from there rather than from inside the object.)",
        "",
    ]


def _command_section(
    prog: str,
    name: str,
    subparser: argparse.ArgumentParser,
    subjects: dict[str, str],
    universal_spellings: frozenset[str],
    universal_actions: list[argparse.Action],
) -> list[str]:
    """One command's whole entry: subject, whether it needs a project and whether it
    writes, its real usage line, its positionals, and its own options."""
    handler = COMMAND_TABLE.get(name)
    out = [f"## `{prog} {name}`", ""]

    subject = subjects.get(name, "")
    if subject:
        out.extend([subject, ""])

    if handler:
        needs = {
            PROJECT_REQUIRED: "Needs a Kyosei project: **yes** — it searches upward for one "
            "and fails if there is none.",
            PROJECT_OPTIONAL: "Needs a Kyosei project: **optional** — it runs, and says so, "
            "where there is no project.",
            PROJECT_NONE: "Needs a Kyosei project: **no** — it never searches for one.",
        }.get(handler[1])
        if needs:
            out.extend([needs, ""])
        writes = (
            "Writes to the store: **yes** — an events row and a fresh export."
            if name in MUTATING_COMMANDS
            else "Writes to the store: **no** — this command only reads."
        )
        out.extend([writes, ""])
        out.extend(_json_id_lines(prog, name))

    # argparse's own usage string, whitespace-collapsed. Derived rather than assembled,
    # so the brackets a reader sees are the arity argparse actually enforces.
    usage = _collapse(subparser.format_usage())
    if usage.lower().startswith("usage: "):
        usage = usage[len("usage: ") :]
    out.extend([f"Usage: `{usage}`", ""])

    actions = _visible(subparser._actions)
    positionals = [a for a in actions if not a.option_strings]
    options = [
        a
        for a in actions
        if a.option_strings and not set(a.option_strings) <= universal_spellings
    ]

    if positionals:
        out.extend(["### Positional arguments", ""])
        out.extend(_positional_line(action) for action in positionals)
        out.append("")

    if options:
        out.extend(["### Options", ""])
        out.extend(_option_line(action) for action in options)
        out.append("")
    elif not positionals:
        out.extend(["This command takes no arguments of its own.", ""])

    if universal_actions:
        out.extend(
            [
                "Also accepts the options every command accepts: "
                + ", ".join(f"`{_canonical(action)}`" for action in universal_actions)
                + ".",
                "",
            ]
        )
    # KYO-489: the full text of every clause this command's text output shortens, from
    # the one registry the command's own renderers print the short forms from.
    out.extend(explanations.reference_lines(name))
    return out


# ------------------------------------------------ KYO-420 / KYO-427: the shape of `--json`
#
# Both are applied here, at the one place every command's envelope passes through,
# rather than in each command: a post-processing step cannot drift from the commands
# it covers the way nine hand-edited payloads would, and a command added later is
# covered the day it ships. KYO-420 built them as opt-ins (`--slim`, `--compact-json`);
# KYO-427 made them the defaults, with `--full` and `--pretty` as the opt-outs.


def json_is_compact(args: argparse.Namespace, stdout_is_tty: bool) -> bool:
    """Whether a ``--json`` envelope prints on one line.

    ``--pretty`` forces indentation, ``--compact-json`` forces one line, and with
    neither the reader decides: a terminal gets indentation (a person is reading),
    anything else — an agent, a pipe, the desktop app — gets one line. Passing both is
    ``--pretty``: asking to read it is the more specific request.
    """
    if getattr(args, "pretty", False):
        return False
    if getattr(args, "compact_json", False):
        return True
    return not stdout_is_tty


def _stdout_is_tty() -> bool:
    """``sys.stdout.isatty()``, false for a stream that cannot say (a replaced stdout
    without the method, or one already closed)."""
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def render_json(payload: dict, compact: bool) -> str:
    """One ``--json`` envelope as printed: one line when ``compact``, else indented.
    The same object either way — only the whitespace differs."""
    if compact:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False)


def _slimming(args: argparse.Namespace) -> bool:
    """Slim is ``--json``'s default (KYO-427) and ``--full`` turns it off; text output
    is never touched. ``--slim`` is accepted and changes nothing."""
    return bool(getattr(args, "json", False) and not getattr(args, "full", False))


#: The keys that mark a dict as a whole record document (``export.record_document``),
#: which is what slim ``--json`` replaces. The heavy lists are the discriminator: a
#: ``kyo list`` row or a plan-member summary carries ``id`` and ``status`` too, and is
#: already small.
RECORD_DOCUMENT_KEYS = frozenset(
    {"id", "kind", "status", "updated_at", "criteria", "evidence_artifacts"}
)

#: The fields a slimmed record always keeps, whatever changed.
SLIM_RECORD_FIELDS = ("id", "kind", "status", "updated_at")

#: A changed scalar longer than this is named in ``changed_fields`` and not carried:
#: a rewritten description is exactly the kind of body slim ``--json`` exists to leave out.
SLIM_SCALAR_LIMIT = 500


def _slim_event_baseline(ctx: commands.Context, args: argparse.Namespace) -> int | None:
    """The highest events-row sequence before a mutating command runs, or ``None``.

    Only a command that can write is asked, so a read-only one opens nothing it would
    not have opened. A store that will not open is left for the handler to refuse in
    its own words; slim ``--json`` then reports ``changed_fields: null`` rather than failing.
    """
    if args.command not in MUTATING_COMMANDS:
        return None
    try:
        row = ctx.store().conn.execute("SELECT MAX(seq) FROM events").fetchone()
    except Exception:  # noqa: BLE001 - the handler refuses in its own words
        return None
    return int(row[0] or 0)


def _slim_changes(ctx: commands.Context, baseline: int | None) -> dict[str, dict] | None:
    """``{record_id: {"created": bool, "fields": set}}`` from the events rows this
    invocation wrote — the fields whose value differs between each row's before and
    after snapshot, unioned over every row for that record (``kyo accept`` writes
    several). ``None`` when there is no baseline to read from."""
    store = ctx._store  # noqa: SLF001 - the store the handler already opened
    if baseline is None or store is None:
        return None
    changes: dict[str, dict] = {}
    for record_id, before_json, after_json, changed_json in store.conn.execute(
        "SELECT record_id, before_json, after_json, changed_json FROM events WHERE seq > ? "
        "AND record_id IS NOT NULL ORDER BY seq",
        (baseline,),
    ):
        # KYO-418: either row format -- a whole `before` or a `changed` map.
        event = events_ledger.event_from_columns(before_json, after_json, changed_json)
        prior = events_ledger.event_prior(event)
        after = event["after"] or {}
        entry = changes.setdefault(record_id, {"created": False, "fields": set()})
        if prior is None:
            entry["created"] = True
            entry["fields"].update(k for k, v in after.items() if v not in (None, [], ""))
            continue
        entry["fields"].update(prior)
    return changes


def slim_record(document: dict, changes: dict[str, dict] | None) -> dict:
    """One record document as slim ``--json`` prints it: identity, status, and what moved."""
    slim = {key: document.get(key) for key in SLIM_RECORD_FIELDS}
    slim["slim"] = True
    if changes is None:
        # Nothing to diff against (a read-only command, or a store that did not
        # open): null rather than [], which would read as "nothing changed".
        slim["changed_fields"] = None
        return slim
    change = changes.get(document["id"])
    fields = sorted(change["fields"]) if change else []
    slim["changed_fields"] = fields
    if change and change["created"]:
        slim["created"] = True
    for key in fields:
        value = document.get(key)
        if key in slim or isinstance(value, (list, dict)):
            continue
        if isinstance(value, str) and len(value) > SLIM_SCALAR_LIMIT:
            continue
        slim[key] = value
    return slim


def slim_payload(data: dict, changes: dict[str, dict] | None) -> dict:
    """``data`` with every EMBEDDED record document replaced by :func:`slim_record`.

    Embedded only: the envelope itself is never replaced, so ``kyo show``'s own
    document — the record, spread at the top level — is left whole. Walks every
    depth, which is what reaches ``kyo accept``'s ``steps[].data.record``.
    """

    def walk(value, top: bool = False):
        if isinstance(value, dict):
            if not top and value.keys() >= RECORD_DOCUMENT_KEYS:
                return slim_record(value, changes)
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value

    return walk(data, top=True)


def main(argv: Sequence[str] | None = None) -> int:
    """The console script: :func:`run`, keeping only its exit code."""
    return run(argv)[0]


#: KYO-453: what :func:`run` says when a command it ran on a read-only store tried to
#: write anyway and SQLite's ``query_only`` refused the statement itself -- the belt
#: under :meth:`kyosei.store.Store.transaction`'s braces, which says the same thing
#: for every write that opens a transaction.
def _read_only_refusal(command: str) -> str:
    return store_module.read_only_refusal(f"kyo {command}")


def run(
    argv: Sequence[str] | None = None,
    environ: dict[str, str] | None = None,
    *,
    read_only: bool = False,
) -> tuple[int, dict | None]:
    """One invocation, in-process: ``(exit_code, payload)`` (KYO-453).

    Everything :func:`main` does -- it prints to ``sys.stdout`` and ``sys.stderr``
    exactly as the console script does -- and hands back the exit code and, under
    ``--json``, the envelope it printed (``None`` otherwise). `kyo serve` drives this
    with both streams captured, so a served body is the CLI's own bytes rather than a
    second rendering of them.

    ``environ`` defaults to ``os.environ``. ``read_only`` opens the store with
    ``PRAGMA query_only`` and makes :meth:`kyosei.store.Store.transaction` refuse by
    a sentence naming `kyo serve`, which is its one caller. A usage error still raises
    ``SystemExit`` from the parser, as it does under :func:`main`.
    """
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    environ = dict(os.environ) if environ is None else dict(environ)
    handler, project_mode = COMMAND_TABLE[args.command]
    # KYO-427: whether stdout is a terminal is read once, here, and decides the
    # whitespace of every envelope this invocation prints -- success and error alike.
    compact = json_is_compact(args, _stdout_is_tty())

    ctx: commands.Context | None = None
    residue_warnings: list[str] = []
    # KYO-52: one invocation, one collector. Cleared here rather than after the
    # command, so a command that raises cannot leave its findings to be reported
    # against the next one (the test suite drives `main` in-process, and so does
    # anything else that embeds the CLI).
    reset_path_warnings()
    # KYO-489: the same, for the short forms that point at `kyo reference <command>`.
    explanations.reset()
    # KYO-591: a hook-mode invocation where no Kyosei project resolves says nothing and
    # exits 0 -- a plugin's hooks fire in every session, Kyosei project or not. Before
    # `build_context`, which would refuse first for the commands that require a project.
    # Not under `--json`: no packaged hook asks for it, and a consumer that did asked for
    # the command's own envelope, which it gets exactly as before.
    if not args.json and cmd_check_build.hook_outside_any_project(args):
        return EXIT_OK, None
    try:
        # KYO-91: before the store is opened, so a refusal refuses a write that has
        # not happened. The warnings it returns are emitted after the write, below.
        residue_warnings = check_prose_arguments(parser, args)
        ctx = commands.build_context(
            args.root,
            args.actor,
            environ,
            list(argv) if argv is not None else sys.argv[1:],
            project=project_mode,
            read_only=read_only,
        )
        # KYO-420: where the events ledger stood before this invocation, read only
        # when slim `--json` (the default; not `--full`) will need it to say what
        # the command changed.
        slim_baseline = _slim_event_baseline(ctx, args) if _slimming(args) else None
        try:
            output = handler(ctx, args)
        except sqlite3.OperationalError as exc:
            # KYO-421.c5: two kyo processes writing at once (a Stop hook and the
            # controller, say) is expected, and the loser used to get a bare
            # `database is locked` traceback. Converted here, on the one path every
            # command's writes pass through, so it travels the KyoseiError branch
            # below — one line, the `--json` envelope, exit 1. Only the store that
            # is actually open is named; a command that never opened it did not
            # lose a race on it.
            if store_module.is_lock_contention(exc):
                opened = ctx._store  # noqa: SLF001 - the open store, never a fresh open
                raise KyoseiError(
                    store_module.lock_contention_message(
                        opened.config.db_path if opened is not None else None
                    )
                ) from exc
            # KYO-453: a content write refused by `query_only` on a store `kyo serve`
            # opened read-only. Every write that opens a transaction is refused
            # before this by `Store.transaction`; this is the one that did not.
            if read_only and store_module.is_read_only_refusal(exc):
                raise KyoseiError(_read_only_refusal(args.command)) from exc
            raise
        if _slimming(args):
            # Inside the try, while the store is still open: the changed fields are
            # read back out of the events this invocation wrote.
            output.data = slim_payload(output.data, _slim_changes(ctx, slim_baseline))
    except KyoseiError as exc:
        # KYO-335: for a hook-shaped invocation whose store cannot be read, the first
        # line of stderr is the only thing a reader gets (KYO-316's measurement), and
        # what it used to carry was a config or schema refusal that said nothing about
        # every guard in the project having silently stopped. The reasoning, and why
        # the exit code is forced non-blocking rather than kept, is on
        # `cmd_check_build.hook_store_unreadable_line`.
        #
        # KYO-421.c7 (from KYO-409.r1): not for a GuardRefusal. A guard that refused
        # DID check — `kyo check-push` reads `kyosei/config.yml` raw and never needs
        # the database — so its refusal is the one thing that must keep its blocking
        # exit. Rewriting it into NOTHING WAS CHECKED, exit 1, turned a blocked push
        # into an allowed one whenever the store was also unreadable to this build.
        hook_line = (
            None
            if isinstance(exc, GuardRefusal)
            else cmd_check_build.hook_store_unreadable_line(ctx, args)
        )
        # KYO-362, and the same shape one line up: a hook-shaped invocation whose
        # failure has a one-line form prints that instead of its whole report.
        # `kyo doctor --hook` puts the line on its own `--json` envelope, which
        # travels on the failure, so this reads it back rather than re-deriving it —
        # the line a reader sees and the line `--json` reports are one string (law 3).
        # Unlike the store-unreadable line above, the EXIT CODE is untouched: the
        # whole promise of `--hook` is that only the printing changes, and the store
        # is perfectly readable here.
        doctor_line = (getattr(exc, "data", None) or {}).get(
            cmd_store.DOCTOR_HOOK_LINE_KEY
        )
        print(hook_line or doctor_line or f"{PROG}: {exc}", file=sys.stderr)
        if args.json:
            payload = {
                "ok": False,
                "command": args.command,
                "error": hook_line or str(exc),
                "store_unreadable": hook_line is not None,
                "exit_code": EXIT_ERROR if hook_line else exc.exit_code,
            }
            payload.update(getattr(exc, "data", None) or {})
            payload["ok"] = False
            print(render_json(payload, compact))
        return (EXIT_ERROR if hook_line else exc.exit_code), (payload if args.json else None)
    except Exception as exc:  # noqa: BLE001 - the CLI's error contract, KYO-421.c1-c2
        # Anything a handler did not mean to raise — an OSError, a PermissionError, a
        # sqlite3 error other than lock contention. It used to escape as a whole
        # traceback, and under `--json` with no envelope at all, so an agent parsing
        # stdout got nothing. Now: one line on stderr, the same envelope shape the
        # branch above emits (plus `exception_type`), exit 1. The traceback is not
        # lost, only opt-in: the one line names the variable that prints it.
        # KeyboardInterrupt and SystemExit are BaseException, not Exception, and
        # keep their own behaviour.
        exception_type = type(exc).__name__
        if os.environ.get(TRACEBACK_ENV, "").strip() not in ("", "0"):
            traceback.print_exc(file=sys.stderr)
        print(
            f"{PROG}: {exception_type}: {exc} (unexpected; set {TRACEBACK_ENV}=1 to "
            "print the traceback)",
            file=sys.stderr,
        )
        if args.json:
            payload = {
                "ok": False,
                "command": args.command,
                "error": str(exc),
                "exception_type": exception_type,
                "exit_code": EXIT_ERROR,
            }
            print(render_json(payload, compact))
        return EXIT_ERROR, (payload if args.json else None)
    finally:
        if ctx is not None:
            ctx.close()

    # KYO-52.c3: a path the command just stored in prose is warned about here, in
    # both output modes, and never refused — the write already happened and the
    # value is legitimate often enough that refusing it would be wrong. Emitted from
    # the one place every command's output passes through, so a prose surface added
    # later cannot forget to carry it.
    path_warnings = pending_path_warnings()
    if path_warnings:
        output.lines.append(path_warning_line(path_warnings))
        output.data["path_warnings"] = path_warnings

    # KYO-91.c1, the "stores it having said plainly" branch: a prose flag with no
    # file-based twin cannot be refused without making `kyo new` impossible, so the
    # residue is said here instead — in both output modes, after the write, from the
    # one place every command's output passes through.
    if residue_warnings:
        output.lines.extend(residue_warnings)
        output.data["shell_residue_warnings"] = residue_warnings

    if args.json:
        print(render_json(output.data, compact))
    elif output.lines:
        # KYO-362: a command that deliberately returns NO lines prints nothing, not a
        # blank one. `kyo doctor --hook` on a clean store is the first command in this
        # package whose success is silence, and `print("\n".join([]))` would have made
        # it emit a newline — which is a small thing on a terminal and not a small
        # thing in a hook log, where "this hook printed something" is the signal.
        # KYO-489: explain once. A command that printed a short form in place of a
        # clause's full text ends with one line naming where the full text is -- here,
        # after every other line, so it is the last line and is printed exactly once
        # however many renderers contributed a short form.
        print("\n".join(output.lines + explanations.footer(args.command, output.lines)))
    return EXIT_OK, (output.data if args.json else None)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
