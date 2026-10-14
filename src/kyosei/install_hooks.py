"""``kyo install-hooks``: the packaged Claude Code hook fragments, their drift check, and the
git ``pre-push`` hook (KYO-13, KYO-20, KYO-60).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from .errors import KyoseiError
from .scaffold import (
    HOOKS_SOURCE_SUBDIR,
    PACKAGED_ASSET_SOURCE_DIR,
    _git,
    _reinstall_route,
    _source_repo_refusal,
    build_age_against,
    defaults_dir,
    install_report_gitignore,
    repo_hooks_source,
)

# --------------------------------------------------------------- KYO-13.c1: hooks

#: (settings.json event key, packaged hook-fragment filename under
#: ``defaults/hooks/``). ``stop.json`` installs under both ``Stop`` and
#: ``SessionEnd`` — templates/hooks/README.md's own instruction ("If your harness
#: fires SessionEnd separately from Stop, install this same block under
#: SessionEnd too") — since one packaged fragment describes one hook *action*,
#: not one event name; which event(s) fire it is `kyo install-hooks`'s call, not
#: the fragment's.
#:
#: ``sessions.json`` installs under ``SessionStart``, ``Stop`` and ``SessionEnd``, and `kyo
#: sessions --hook` tells them apart by the payload's own ``hook_event_name`` rather than by
#: three commands, so the one decision lives in one place (docs/design/scaffold.md#kyo-186).
HOOK_INSTALL_TARGETS: tuple[tuple[str, str], ...] = (
    ("SessionStart", "session-start.json"),
    ("SessionStart", "sessions.json"),
    ("Stop", "stop.json"),
    ("Stop", "sessions.json"),
    ("SessionEnd", "stop.json"),
    ("SessionEnd", "sessions.json"),
    ("SubagentStop", "subagent-stop.json"),
    ("PreToolUse", "pre-tool-use.json"),
    # KYO-398.c6: the per-prompt build-change check, for a re-pack mid-session.
    ("UserPromptSubmit", "prompt-submit.json"),
)

CLAUDE_SETTINGS_REL = Path(".claude") / "settings.json"


def _load_hook_fragment(name: str, hooks_dir: Path | None = None) -> list[dict]:
    """Read one packaged hook fragment (``defaults/hooks/<name>``) and return its
    list of hook-groups — the value under the fragment's own single top-level
    event key, whichever event that happens to name (see
    :data:`HOOK_INSTALL_TARGETS`'s docstring: the fragment's own key is not
    necessarily every event it gets installed under).

    ``hooks_dir`` is KYO-318's half: a caller that has established which copy of these
    fragments is the *source* for the project it is checking (:func:`repo_hooks_source`)
    passes that directory, and every comparison built on top reads it instead of the
    running build's. ``None`` — every consumer, and every call that means "what does
    this build install" — is the packaged copy, exactly as before.
    """
    if hooks_dir is not None:
        path = hooks_dir / name
        if not path.is_file():
            raise KyoseiError(f"hook template {name!r} is missing from {hooks_dir}")
        return _parse_hook_fragment(path)
    source_dir = defaults_dir()
    if source_dir is None:
        raise KyoseiError(
            "this build declares hook templates but ships no defaults/ directory; "
            "reinstall kyosei"
        )
    path = source_dir / HOOKS_SOURCE_SUBDIR / name
    if not path.is_file():
        raise KyoseiError(f"packaged hook template {name!r} is missing from {path}")
    return _parse_hook_fragment(path)


def _parse_hook_fragment(path: Path) -> list[dict]:
    """One fragment file's hook-groups, with the shape checks both readers share."""
    try:
        fragment = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise KyoseiError(f"packaged hook template {path} is not valid JSON: {exc}") from exc
    if not isinstance(fragment, dict) or len(fragment) != 1:
        raise KyoseiError(
            f"packaged hook template {path} must have exactly one top-level event key"
        )
    (groups,) = fragment.values()
    return groups


def _strip_comment_fields(hooks: list[dict]) -> list[dict]:
    """Drop the ``comment`` key each packaged hook entry carries for a human
    reader (templates/hooks/README.md explains each one). ``comment`` is not
    part of Claude Code's documented hook-handler shape (``type``/``command``/
    ``args``/``if``/``timeout``/``statusMessage``/``once``/``async``/
    ``asyncRewake``/``shell``), so it is stripped before anything is written
    into a consumer's real ``.claude/settings.json``, rather than trusting an
    unrecognised key to be silently ignored by every harness that reads it."""
    return [{k: v for k, v in hook.items() if k != "comment"} for hook in hooks]


#: KYO-316: the one line every packaged hook says when `kyo` is not on this shell's
#: PATH, and the prefix that says it. A hook's whole interface is its command string
#: (that is all a ``.claude/settings.json`` hook is), so the check has to live *in*
#: the string, under ``/bin/sh``, ahead of the command it guards.
#:
#: The rule, the same on every event: the message goes to STDERR, on ONE line, and the
#: exit is 127. Not 0 -- a hook that exits 0 has its stderr sent to the debug log only, so
#: the reader would lose the message. Not 2 -- on ``PreToolUse`` that blocks every Bash
#: call in the project (without `kyo` the hook cannot tell a `git push` from an `ls`), on
#: ``Stop``/``SubagentStop`` it is a block nobody woken can clear, and on ``SessionStart``/
#: ``SessionEnd`` it is not honoured at all. 127 is what the shell already returns for a
#: missing command. One line, because how much of stderr the harness renders has changed
#: between builds. When `kyo` IS on PATH the guard is one ``command -v`` and the hook keeps
#: its own exit status. The git ``pre-push`` hook keeps its own, deliberately silent guard
#: (:data:`PRE_PUSH_SCRIPT`). The per-event measurement: docs/design/scaffold.md#kyo-316, #kyo-362.
PATH_GUARD_MESSAGE = (
    "Kyosei CLI (kyo) is not on PATH, so this hook did not run. Install it: "
    "uv tool install kyosei (or pipx install kyosei), then start a new session"
)

#: KYO-450: every message the guard has carried before this one. KYO-316's named "in the
#: Kyosei app, Install command line tools" as the first remedy; that action went when the
#: app stopped bundling a runtime, so the line stopped offering it. A line a consumer
#: installed under an earlier message is still the SAME recipe -- :func:`hook_action`
#: strips these too -- and :data:`SUPERSEDED_HOOK_COMMANDS` carries each one to today's
#: message, the same way KYO-316 carried the bare lines to the guard.
RETIRED_PATH_GUARD_MESSAGES = (
    "Kyosei CLI (kyo) is not on PATH, so this hook did not run. Install it: in the "
    "Kyosei app, Install command line tools; or run uv tool install kyosei",
)

#: KYO-591: with `kyo` missing, whether this directory is a Kyosei project at all,
#: answered by the shell alone -- `kyosei/config.yml` or the `.kyosei` store link at or
#: above ``$CLAUDE_PROJECT_DIR`` (the hook's cwd when that is unset), or beside the main
#: checkout ``git rev-parse --git-common-dir`` names, which is how a dispatch worktree of
#: a split project reaches the store link it reads through (KYO-580). Found: the guard
#: says its line and exits 127, as KYO-316 measured. Not found: exit 0 and say nothing,
#: because a plugin's hooks fire in every session, Kyosei project or not. When `kyo` IS on
#: PATH none of this
#: runs and `kyo` decides with its own resolution (`cmd_check_build.hook_outside_any_project`);
#: this walk is only the fallback for its absence. POSIX ``sh`` only: ``case`` strips one
#: path component per turn and an empty remainder ends the walk at ``/``.
_MARKER_WALK = (
    'd="${CLAUDE_PROJECT_DIR:-$PWD}"; '
    'c="$(git -C "$d" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"; '
    '{ [ -n "$c" ] && { [ -e "${c%/*}/kyosei/config.yml" ] || [ -e "${c%/*}/.kyosei" ]; }; } || '
    'while [ ! -e "$d/kyosei/config.yml" ] && [ ! -e "$d/.kyosei" ]; do '
    'case "$d" in */*) d="${d%/*}" ;; *) exit 0 ;; esac; done; '
)


#: The exact prefix every packaged hook command carries. Exact, because
#: :func:`hook_action` strips it by literal match — a consumer's own edit of it stops
#: matching and is then left alone, the same conservatism
#: :data:`SUPERSEDED_HOOK_COMMANDS` errs on for a file we do not own.
def _guard_prefix(message: str) -> str:
    return (
        f"command -v kyo >/dev/null 2>&1 || {{ {_MARKER_WALK}echo '{message}' >&2; exit 127; }}; "
    )


def _unwalked_guard_prefix(message: str) -> str:
    """The shape every build from KYO-316 to KYO-591 installed: the line and exit 127
    in ANY directory, a Kyosei project or not."""
    return f"command -v kyo >/dev/null 2>&1 || {{ echo '{message}' >&2; exit 127; }}; "


PATH_GUARD_PREFIX = _guard_prefix(PATH_GUARD_MESSAGE)

#: The prefixes an earlier build installed, each with what changed since, as `kyo
#: doctor` and `install-hooks`' change log name it (:func:`_supersession_note`).
RETIRED_PATH_GUARD_NOTES: dict[str, str] = {
    **{
        _unwalked_guard_prefix(m): (
            "behind the PATH guard's current message, which names `uv tool install "
            "kyosei` rather than the desktop app's retired action"
        )
        for m in RETIRED_PATH_GUARD_MESSAGES
    },
    # KYO-591: the same message, without the marker walk.
    _unwalked_guard_prefix(PATH_GUARD_MESSAGE): (
        "behind the PATH guard that is silent outside a Kyosei project (KYO-591)"
    ),
}

#: The prefixes an earlier build installed (:data:`RETIRED_PATH_GUARD_NOTES`).
RETIRED_PATH_GUARD_PREFIXES = tuple(RETIRED_PATH_GUARD_NOTES)


def guarded(command: str) -> str:
    """The packaged spelling of a hook command: :data:`PATH_GUARD_PREFIX`, then the
    command. Derived rather than written out, so the guard cannot say one thing in a
    fragment and another in :data:`SUPERSEDED_HOOK_COMMANDS`
    (``tests/test_hook_templates_sync.py`` holds the fragments to it)."""
    return PATH_GUARD_PREFIX + command


def hook_action(command: object) -> object:
    """A hook command with the PATH guard stripped — what the hook actually *does*.

    The guard is a precondition, not a step of the recipe, and every comparison in
    this module and in the checks that read it asks about the recipe: is this command
    installed, does this line already run it, what does this event run. Stripping here
    is what keeps KYO-316 from reading as a change of recipe everywhere at once — most
    of all in :func:`command_elements`, where a guarded line and the bare line a
    consumer installed before this build have to compare as the same thing or every
    existing settings.json would be told it is missing a command it plainly runs, and
    a consumer who extended the line with a step of their own would get a duplicate
    group (KYO-27's defect, re-shipped).
    """
    if isinstance(command, str):
        for prefix in (PATH_GUARD_PREFIX, *RETIRED_PATH_GUARD_PREFIXES):
            if command.startswith(prefix):
                return command[len(prefix) :]
    return command


#: The packaged `Stop` / `SessionEnd` chain (``defaults/hooks/stop.json``), named once
#: because every older chain below is superseded by it.
STOP_HOOK_COMMAND = "kyo sweep --hook && kyo doctor --hook"

#: Hook commands a previous release shipped and this one supersedes, each mapped to the
#: packaged command that replaces it. `install-hooks` merges rather than replaces, so a
#: superseded line is removed by EXACT command string: anything a consumer edited no longer
#: matches and is left alone, the conservative side to err on for a file we do not own.
#:
#: Exact-string supersession is also the ONLY path by which a reordering or a removal
#: reaches an installed chain -- :func:`command_elements` is a set-containment test and is
#: blind to both. Each value is named in `kyo doctor`'s report and `install-hooks`' change
#: log -- verbatim, except an entry whose only change is the PATH guard, which
#: :func:`_supersession_note` names by what changed (KYO-316, KYO-450) -- and
#: `tests/test_install_hooks.py` fails if one names a command this build does not
#: install. A value is only ever the NAME of the replacement
#: (what is installed always comes from the fragment), so re-pointing an old key at
#: today's line is always safe. Why each entry exists: docs/design/scaffold.md#kyo-17
#: and the ids below.
SUPERSEDED_HOOK_COMMANDS: dict[str, str] = {
    (
        "if [ -n \"$(git status --porcelain)\" ]; then echo 'Uncommitted or unstaged changes "
        "in this worktree. Commit before finishing -- an uncommitted worktree at stop time is "
        "treated as work that does not durably exist yet.' >&2; exit 2; fi"
        # Re-pointed by KYO-128 (docs/design/scaffold.md#kyo-128).
    ): guarded("kyo check-clean --subagent-stop"),
    # KYO-27; re-pointed by KYO-31 and KYO-215, then behind the PATH guard by KYO-316 and
    # at the `--hook` chain by KYO-362 (docs/design/scaffold.md#kyo-27).
    "kyo doctor && kyo sweep": guarded(STOP_HOOK_COMMAND),
    # KYO-31: the ordering fix; containment cannot see a reordering
    # (docs/design/scaffold.md#kyo-31).
    "kyo doctor && kyo sweep && kyo html": guarded(STOP_HOOK_COMMAND),
    # KYO-215: removing `kyo html` made the packaged line a strict SUBSET of this one, which
    # containment read as already present. Since KYO-362 the packaged chain ends in
    # `kyo doctor --hook`, an element this line lacks, so containment alone would now add a
    # second doctor group beside it; either way only this entry rewrites the line in place
    # (docs/design/scaffold.md#kyo-215, #kyo-362).
    "kyo sweep && kyo html && kyo doctor": guarded(STOP_HOOK_COMMAND),
    # KYO-94: containment cannot carry a new flag (docs/design/scaffold.md#kyo-94).
    "kyo brief --compact": guarded("kyo brief --compact --session-start"),
    # KYO-128: the bare forms audit the hook's inherited cwd -- the controller's project
    # root, never the stopping worker's worktree -- and only supersession removes them from a
    # consumer's settings.json (docs/design/scaffold.md#kyo-128).
    "kyo check-clean": guarded("kyo check-clean --subagent-stop"),
    "kyo check-branch --against main": guarded("kyo check-branch --subagent-stop --against main"),
    # KYO-316: every command this build shipped bare, superseded by itself behind the PATH
    # guard. :func:`hook_action` makes the two compare EQUAL (no duplicate group, no false
    # "missing"), which is exactly why containment can never upgrade them: the equality and
    # these entries are one mechanism, and dropping either silently reverts KYO-316 for every
    # project that already installed hooks (docs/design/scaffold.md#kyo-316).
    "kyo brief --compact --session-start": guarded("kyo brief --compact --session-start"),
    "kyo sessions --hook": guarded("kyo sessions --hook"),
    # KYO-591 re-pointed this one at `kyo sweep --hook`, which is silent outside a
    # Kyosei project; containment cannot carry a new flag (KYO-94).
    "kyo sweep && kyo doctor --hook": guarded(STOP_HOOK_COMMAND),
    STOP_HOOK_COMMAND: guarded(STOP_HOOK_COMMAND),
    # KYO-362 re-pointed this one at today's chain (docs/design/scaffold.md#kyo-362).
    "kyo sweep && kyo doctor": guarded(STOP_HOOK_COMMAND),
    "kyo check-clean --subagent-stop": guarded("kyo check-clean --subagent-stop"),
    "kyo check-branch --subagent-stop --against main": guarded(
        "kyo check-branch --subagent-stop --against main"
    ),
    "kyo check-push --pre-tool-use": guarded("kyo check-push --pre-tool-use"),
    "kyo check-command --pre-tool-use": guarded("kyo check-command --pre-tool-use"),
    # KYO-398: no build ever shipped this line bare — it arrived after KYO-316 — so the
    # entry supersedes nothing a consumer carries. It is here because the pairing is
    # the invariant `tests/test_install_hooks.py` holds for EVERY packaged command: a
    # hand-merged copy of the bare line picks the guard up by re-running install-hooks.
    "kyo check-build --hook": guarded("kyo check-build --hook"),
    # KYO-362: containment cannot carry the new `--hook` flag, so without this every
    # post-KYO-316 install would gain a second doctor group; this supersedes the guarded
    # pre-`--hook` chain they carry (docs/design/scaffold.md#kyo-362).
    guarded("kyo sweep && kyo doctor"): guarded(STOP_HOOK_COMMAND),
}

# KYO-450: every line an earlier build installed behind a RETIRED guard message is
# superseded by the same command behind today's -- derived from the entries above rather
# than typed out, so a command added to them later is covered too. A bare key that the
# table guards as-is (KYO-316's pairing) was installed guarded under the old message by
# every build since KYO-316; a guarded key is the same line one message back. Keys with
# a different replacement (a reordering, a new flag) were never shipped behind the old
# guard in their bare form, and are left as they are.
#
# KYO-591 widened the second branch to every bare key. Its new guard (the marker walk)
# retired the prefix every build since KYO-450 installed, and the same change re-pointed
# the stop chain at `kyo sweep --hook`: so the line those builds installed is the OLD
# chain behind the OLD guard, a key whose replacement is no longer its own guarded form.
# A pairing that names a line no build shipped removes nothing, so the wider rule costs
# nothing where it is not needed.
for _retired in RETIRED_PATH_GUARD_PREFIXES:
    for _key, _replacement in list(SUPERSEDED_HOOK_COMMANDS.items()):
        if _key.startswith(PATH_GUARD_PREFIX):
            SUPERSEDED_HOOK_COMMANDS[_retired + _key[len(PATH_GUARD_PREFIX) :]] = _replacement
        elif not _key.startswith(RETIRED_PATH_GUARD_PREFIXES):
            SUPERSEDED_HOOK_COMMANDS[_retired + _key] = _replacement


def _supersession_note(command: str) -> str:
    """How `kyo doctor` and `install-hooks`' change log name the replacement for one
    superseded command.

    KYO-316 made most supersessions guard-only — the same command, behind
    :data:`PATH_GUARD_PREFIX` — and quoting the replacement verbatim there would print
    a ~300 character line whose only difference from the installed one is a prefix the
    reader has to diff by eye. So a guard-only supersession says what changed instead
    of restating the command, and every other entry keeps naming its replacement
    verbatim, which is what `tests/test_install_hooks.py` holds those entries to.
    """
    replacement = SUPERSEDED_HOOK_COMMANDS[command]
    if replacement == guarded(command):
        return f"{command!r} behind the PATH guard, which says what to do when `kyo` is missing"
    action = hook_action(command)
    if action != command and replacement == guarded(action):
        # KYO-450, KYO-591: the same command behind a retired guard.
        retired = next(p for p in RETIRED_PATH_GUARD_PREFIXES if command.startswith(p))
        return f"{action!r} {RETIRED_PATH_GUARD_NOTES[retired]}"
    return repr(replacement)


def _drop_superseded_hooks(hooks_section: dict) -> tuple[list[str], dict[str, list[int]]]:
    """Strip every hook whose command is an exact :data:`SUPERSEDED_HOOK_COMMANDS`
    key. Groups left empty by the strip are dropped too.

    Returns ``(changes, edited)`` where ``edited`` maps each event to the ``id()``
    of every group this touched — :func:`merge_hooks` uses it to prune a leftover
    that the freshly-installed group fully covers, so upgrading does not leave the
    same check wired twice. Identity, not equality, so only groups this function
    actually edited are ever candidates for pruning; anything a consumer authored
    independently is untouchable.
    """
    changes: list[str] = []
    edited: dict[str, list[int]] = {}
    for event, groups in list(hooks_section.items()):
        if not isinstance(groups, list):
            continue
        for group in list(groups):
            if not isinstance(group, dict):
                continue
            kept = [
                h
                for h in group.get("hooks", [])
                if not (
                    isinstance(h, dict) and h.get("command") in SUPERSEDED_HOOK_COMMANDS
                )
            ]
            removed = len(group.get("hooks", [])) - len(kept)
            if not removed:
                continue
            for h in group.get("hooks", []):
                if isinstance(h, dict) and h.get("command") in SUPERSEDED_HOOK_COMMANDS:
                    changes.append(
                        f"{event}: removed superseded hook, replaced by "
                        f"{_supersession_note(h['command'])}"
                    )
            group["hooks"] = kept
            if kept:
                edited.setdefault(event, []).append(id(group))
            else:
                groups.remove(group)
    return changes, edited


def _hook_commands(group: dict) -> set[tuple[object, object]]:
    return {(h.get("type"), h.get("command")) for h in group.get("hooks", [])}


def command_elements(command: object) -> set[str]:
    """The ``&&``-separated commands inside one hook command string.

    A consumer who wants one more step extends the line rather than adding an entry, so
    compared as whole strings an extended line reads as *absent* and `install-hooks` would
    add a second group -- a duplicate sweep per stop (docs/design/scaffold.md#kyo-27).

    Splitting on ``&&`` is a deliberately shallow parse — it does not understand
    quoting, ``;``, or subshells, and it does not need to: it is only ever asked
    "does this line already run that command", and a false *negative* costs a
    duplicate entry (the status quo), never a missing check. Both `merge_hooks`
    and `kyo doctor`'s hook-currency check read it, so the two cannot disagree
    about whether something is installed.

    It is a *set* read as a *containment* test, so it is order-blind (KYO-31) and
    removal-blind (KYO-215) by design: making it order-aware, or an equality, would tell a
    consumer who appended a step of their own that they are missing what they plainly run.
    Reorderings and removals travel by :data:`SUPERSEDED_HOOK_COMMANDS` instead
    (docs/design/scaffold.md#kyo-31).

    KYO-316: the PATH guard is stripped first (:func:`hook_action`), so the elements
    are the hook's own commands and a line that gained the guard is not read as having
    gained a step. Without that, every consumer's installed line would read as missing
    a command, and a consumer who had extended theirs would be handed a second group.
    """
    command = hook_action(command)
    if not isinstance(command, str):
        return set()
    return {part.strip() for part in command.split("&&") if part.strip()}


def installed_elements(settings: dict, event: str) -> set[str]:
    """Every ``&&``-separated command wired under ``event`` in a settings doc."""
    found: set[str] = set()
    for group in settings.get("hooks", {}).get(event, []) or []:
        if not isinstance(group, dict):
            continue
        for hook in group.get("hooks", []) or []:
            if isinstance(hook, dict):
                found |= command_elements(hook.get("command"))
    return found


def packaged_elements(event: str, hooks_dir: Path | None = None) -> set[str]:
    """Every command the packaged fragments wire under ``event``.

    ``hooks_dir`` (KYO-318) reads a different copy of those fragments — see
    :func:`_load_hook_fragment`.
    """
    found: set[str] = set()
    for target_event, source_name in HOOK_INSTALL_TARGETS:
        if target_event != event:
            continue
        for group in _load_hook_fragment(source_name, hooks_dir):
            for hook in group.get("hooks", []):
                found |= command_elements(hook.get("command"))
    return found


#: KYO-269: Claude Code's ``SessionEnd`` hooks share a 1.5 second budget across all
#: hooks combined by default, and a hook that sets its own ``timeout`` raises that
#: shared budget to match — up to a 60 second ceiling
#: (code.claude.com/docs/en/hooks). No other event this project installs into has
#: that exception; ``Stop`` already gets the ordinary 600 second default. So the
#: packaged fragments carry a ``timeout`` key on the one hook each needs it for, and
#: it is only ever meant for the event that budget applies to — see each fragment's
#: own ``comment`` (``defaults/hooks/stop.json``, ``sessions.json``) for the
#: measurement behind the number.
SESSIONEND_EVENT = "SessionEnd"


def _sessionend_timeout_specs(hooks_dir: Path | None = None) -> list[tuple[set[str], int, str]]:
    """``(wanted_elements, packaged_timeout, packaged_command)`` for every packaged
    hook that declares a ``timeout`` under :data:`SESSIONEND_EVENT`. Read fresh off
    the packaged fragments every time, on :func:`packaged_elements`'s precedent, so
    a change to the fragment's number reaches both `merge_hooks` and `hook_drift`
    without a second place to update."""
    specs: list[tuple[set[str], int, str]] = []
    for target_event, source_name in HOOK_INSTALL_TARGETS:
        if target_event != SESSIONEND_EVENT:
            continue
        for group in _load_hook_fragment(source_name, hooks_dir):
            for hook in group.get("hooks", []):
                timeout = hook.get("timeout")
                if not isinstance(timeout, int):
                    continue
                wanted = command_elements(hook.get("command"))
                if wanted:
                    specs.append((wanted, timeout, hook.get("command")))
    return specs


def _covering_hooks(groups: object, wanted: set[str]) -> list[dict]:
    """Every installed hook dict, under one event's list of groups, whose own
    command runs at least every element of ``wanted`` — the same containment test
    `merge_hooks`'s ``already_present`` and `hook_drift`'s ``missing`` already use,
    so a consumer's own extension of the line is still "the entry" for timeout
    purposes and not a stranger next to it."""
    found: list[dict] = []
    for group in groups or []:
        if not isinstance(group, dict):
            continue
        for hook in group.get("hooks", []) or []:
            if isinstance(hook, dict) and wanted <= command_elements(hook.get("command")):
                found.append(hook)
    return found


def _hook_drift_remedy(root: Path, behind: list[str]) -> str:
    """What to do about a hook finding — which depends on whether `kyo install-hooks`
    would install the right text (KYO-318.c2).

    The plain answer everywhere except a source repository whose fragments the running
    build has fallen behind. There, the command this check has named since KYO-20 is
    the destructive one: it would merge the OLDER build's fragments into
    ``.claude/settings.json``, which is how a group deleted from the source comes back.
    """
    if not behind:
        return "run `kyo install-hooks`"
    source_path = PACKAGED_ASSET_SOURCE_DIR / HOOKS_SOURCE_SUBDIR
    return (
        f"do NOT run `kyo install-hooks` here — this build's packaged copies of "
        f"{', '.join(sorted(behind))} are {build_age_against(root)} {source_path}, this "
        "repository's own source for them, so the command would merge the build's text "
        f"into {CLAUDE_SETTINGS_REL} and it refuses to for that reason. Either move this "
        f"copy to a build made from this checkout ({_reinstall_route()}) and run it "
        f"again, or merge the group by hand from {source_path}"
    )


def hook_fragments_behind(root: Path) -> list[str]:
    """Fragment filenames whose source under ``root`` differs from this build's copy.

    KYO-318.c2's measurement, made into the mechanism. Empty for every consumer (no
    :func:`repo_hooks_source`) and empty whenever the two copies agree — which is the
    case for a build made from this checkout, including an editable install, where the
    two paths resolve to the same directory.

    Compared as *parsed* fragments, not as bytes: what matters is what a fragment
    installs, and a reformatted file that installs the same hooks is not a build that
    is behind. A fragment the running build does not ship at all (the source added one)
    counts as a difference, which is the same answer read from the other end.
    """
    hooks_dir = repo_hooks_source(root)
    if hooks_dir is None:
        return []
    behind: list[str] = []
    for _event, source_name in HOOK_INSTALL_TARGETS:
        if source_name in behind:
            continue
        try:
            source = _load_hook_fragment(source_name, hooks_dir)
        except KyoseiError:
            continue  # this checkout does not carry it; not a statement about the build
        try:
            packaged = _load_hook_fragment(source_name)
        except KyoseiError:  # pragma: no cover - a broken install, reported elsewhere
            behind.append(source_name)
            continue
        if source != packaged:
            behind.append(source_name)
    return behind


def hook_drift(root: Path) -> list[str]:
    """Every way a project's installed hooks have fallen behind this build (KYO-20), both
    halves of :func:`hook_drift_by_tier` in one list -- what `kyo brief` and `kyo onboard
    --check` print, which name a superseded hook and its remedy exactly as they always did
    (KYO-604.c3)."""
    problems, superseded = hook_drift_by_tier(root)
    return superseded + problems


def _subcommands_this_build_lacks(command: object) -> list[str]:
    """The `kyo` subcommands a hook command calls that this build's CLI does not have.

    KYO-604: an earlier build's hook is a notice only while it still works. Each
    ``&&``-separated element of the form ``kyo <name> ...`` is checked against the CLI's
    command table; anything else in the line is not Kyosei's to judge.
    """
    from .cli import COMMAND_TABLE  # imported here: cli imports this module's neighbours

    lacking: list[str] = []
    for element in sorted(command_elements(command)):
        words = element.split()
        if (
            len(words) >= 2
            and words[0] == "kyo"
            and not words[1].startswith("-")
            and words[1] not in COMMAND_TABLE
            and words[1] not in lacking
        ):
            lacking.append(words[1])
    return lacking


def hook_drift_by_tier(root: Path) -> tuple[list[str], list[str]]:
    """Ways a project's installed hooks have fallen behind this build (KYO-20), as
    ``(problems, superseded)``.

    Installed hooks are a *copy* of this build's configuration and never update
    themselves; this is what tells a project its copy has gone stale
    (docs/design/scaffold.md#kyo-20).

    KYO-604 splits the findings by what they say. ``superseded`` is one line per hook whose
    command is EXACTLY a line an earlier build packaged (:data:`SUPERSEDED_HOOK_COMMANDS`):
    a hook that still works, whose text a later build changed, and which `kyo doctor`
    reports as a notice (``hook_config_superseded``). ``problems`` is everything else --
    unreadable settings, a packaged command no installed hook runs, a ``SessionEnd`` hook
    without its timeout -- which `kyo doctor` reports as ``hook_config`` and fails on. A
    packaged command that is missing ONLY because a superseded hook under the same event
    runs the earlier form of it (the earlier `Stop` chain lacks `kyo sweep --hook`, which
    its replacement runs) is not reported a second time as missing: the superseded line
    already names that replacement and the remedy, and reporting it as a problem as well
    would fail every stop hook of every project an upgrade changed the text for, which is
    the case KYO-604 exists to stop. A command matching neither today's text nor any
    superseded one covers nothing, so what it leaves out is still missing.

    Each list is empty when current. Says nothing at all when the settings file is
    absent: hooks are a convenience, not a requirement for driving `kyo` by hand (the
    `/kyosei` skill says so), and a project that never installed them is not behind.

    In the repository this package is built FROM, the comparison is against that
    repository's own fragments (:func:`repo_hooks_source`), not the running build's: the
    installed `kyo` may predate the last edit there, and naming `kyo install-hooks` would
    put a deleted group back. The build's own age is :func:`installed_build_lag`'s notice
    (docs/design/scaffold.md#kyo-318).

    Every comparison goes through :func:`command_elements`, which strips the PATH guard,
    so a guarded packaged line and an unguarded installed one compare equal.
    :data:`SUPERSEDED_HOOK_COMMANDS` is the one finding not read from a fragment -- it is
    compiled into the running build -- so it names `kyo install-hooks` only through
    :func:`_hook_drift_remedy`, which withholds that command where it is unsafe.
    """
    settings_path = root / CLAUDE_SETTINGS_REL
    if not settings_path.is_file():
        return [], []
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{CLAUDE_SETTINGS_REL} is not valid JSON: {exc}"], []
    if not isinstance(settings, dict):
        return [f"{CLAUDE_SETTINGS_REL} must contain a JSON object at its top level"], []

    problems: list[str] = []
    superseded: list[str] = []
    # KYO-318.c2: whichever copy of the fragments is the SOURCE for this project —
    # this repository's own where it has them, the running build's everywhere else.
    hooks_dir = repo_hooks_source(root)
    behind = hook_fragments_behind(root)
    remedy = _hook_drift_remedy(root, behind)
    wires_them = (
        "this repository's own source for the packaged hook templates wires there"
        if hooks_dir is not None
        else "this build's hook templates wire there"
    )
    events = {event for event, _ in HOOK_INSTALL_TARGETS}
    for event in sorted(events):
        installed = installed_elements(settings, event)
        # KYO-604: what the superseded hooks under this event will run once replaced.
        replaced_by: set[str] = set()
        for group in settings.get("hooks", {}).get(event, []) or []:
            if not isinstance(group, dict):
                continue
            for hook in group.get("hooks", []) or []:
                command = hook.get("command") if isinstance(hook, dict) else None
                if command in SUPERSEDED_HOOK_COMMANDS:
                    replaced_by |= command_elements(SUPERSEDED_HOOK_COMMANDS[command])
                    gone = _subcommands_this_build_lacks(command)
                    if gone:
                        # A notice is for a hook that still works. One calling a kyo
                        # subcommand this build no longer has fails every time it fires,
                        # so it stays a problem (KYO-604).
                        problems.append(
                            f"{event} runs a hook this build supersedes with "
                            f"{_supersession_note(command)}, and it calls "
                            f"{', '.join(f'`kyo {name}`' for name in gone)}, which this "
                            f"build does not have, so it fails each time it runs — {remedy}"
                        )
                        continue
                    superseded.append(
                        f"{event} runs a hook this build supersedes with "
                        f"{_supersession_note(command)} — {remedy}"
                    )
        missing = sorted(packaged_elements(event, hooks_dir) - installed - replaced_by)
        for command in missing:
            problems.append(
                f"{event} does not run {command!r}, which {wires_them} — {remedy}"
            )
        if event == SESSIONEND_EVENT:
            groups = settings.get("hooks", {}).get(event, [])
            for wanted, packaged_timeout, packaged_command in _sessionend_timeout_specs(
                hooks_dir
            ):
                if wanted - installed:
                    continue  # already reported above as missing entirely
                covering = _covering_hooks(groups, wanted)
                if covering and all(
                    not isinstance(hook.get("timeout"), (int, float))
                    or hook["timeout"] < packaged_timeout
                    for hook in covering
                ):
                    problems.append(
                        f"{event} runs {packaged_command!r} with no `timeout` of at least "
                        f"{packaged_timeout} — Claude Code shares a 1.5 second budget across "
                        f"all {event} hooks combined unless a hook sets its own, and this "
                        "build's packaged fragment sets one for exactly this reason — run "
                        "`kyo install-hooks`"
                    )
    return problems, superseded


def merge_hooks(existing_settings: dict) -> tuple[dict, list[str]]:
    """Compute the merged ``.claude/settings.json`` document.

    Returns ``(new_settings, changes)`` where ``changes`` describes every hook
    group actually added. Merges into (never replaces) the ``hooks`` section, and
    within it, into (never replaces) each event's own list — exactly
    templates/hooks/README.md's instruction ("merge the hooks arrays — do not
    replace the file wholesale"). Idempotent: a hook group already present (same
    event, same set of ``(type, command)`` pairs) is not added again, so running
    this twice in a row produces byte-identical output the second time.
    """
    settings = copy.deepcopy(existing_settings)
    hooks_section = settings.setdefault("hooks", {})
    changes, edited = _drop_superseded_hooks(hooks_section)
    for event, source_name in HOOK_INSTALL_TARGETS:
        target_groups = hooks_section.setdefault(event, [])
        for group in _load_hook_fragment(source_name):
            clean_hooks = _strip_comment_fields(group.get("hooks", []))
            if event != SESSIONEND_EVENT:
                # KYO-269: the packaged fragment's `timeout` is sized for the
                # `SessionEnd` budget (1.5s aggregate default, raised to match up to
                # a 60s ceiling) — it is not meant for this event, which already gets
                # the ordinary 600s default, and installing it here would only ever
                # shrink that.
                for hook in clean_hooks:
                    hook.pop("timeout", None)
            clean_group = {
                **{k: v for k, v in group.items() if k != "hooks"},
                "hooks": clean_hooks,
            }
            candidate = _hook_commands(clean_group)
            # Presence is asked per *command*, not per whole line: a consumer who
            # extended `kyo sweep && kyo doctor` with a step of their own is already
            # running both, and adding a second group would make them sweep twice.
            # `kyo doctor`'s hook-currency check reads the same helper, so the two
            # cannot disagree about what "installed" means (law 3).
            wanted_elements: set[str] = set()
            for _kind, command in candidate:
                wanted_elements |= command_elements(command)
            already_present = bool(wanted_elements) and wanted_elements <= installed_elements(
                settings, event
            )
            if event == SESSIONEND_EVENT:
                # KYO-269.c2: a `SessionEnd` entry that already runs the packaged
                # command — every existing consumer, pre-fix — gets its `timeout`
                # raised IN PLACE, whether or not a new group is about to be added
                # below; never a second entry beside it, and never touched if it
                # already carries at least the packaged value (a consumer project
                # set 30 by hand).
                for hook in clean_hooks:
                    packaged_timeout = hook.get("timeout")
                    if packaged_timeout is None:
                        continue
                    hook_wanted = command_elements(hook.get("command"))
                    if not hook_wanted:
                        continue
                    for existing in _covering_hooks(target_groups, hook_wanted):
                        current = existing.get("timeout")
                        if not isinstance(current, (int, float)) or current < packaged_timeout:
                            existing["timeout"] = packaged_timeout
                            changes.append(
                                f"{event}: raised {existing.get('command')!r} timeout to "
                                f"{packaged_timeout} (was {current!r})"
                            )
            if already_present:
                continue
            # A group this run just stripped a superseded hook out of, whose
            # remainder the incoming group already contains, is a leftover of the
            # upgrade — not a second opinion. Dropping it keeps `check-branch`
            # from being wired twice after a migration.
            for leftover in list(target_groups):
                if (
                    id(leftover) in edited.get(event, [])
                    and _hook_commands(leftover)
                    and _hook_commands(leftover) <= candidate
                ):
                    target_groups.remove(leftover)
                    changes.append(f"{event}: dropped the emptied remainder of that hook group")
            target_groups.append(clean_group)
            changes.append(f"{event}: added hook group from defaults/hooks/{source_name}")
    return settings, changes


def install_hooks_refusal(root: Path) -> str | None:
    """Why `kyo install-hooks` must not merge here, or ``None`` (KYO-318.c2).

    The same reversal one step removed, and the reason it is a refusal rather than a
    silent substitution: `merge_hooks` is additive by design (KYO-27), so an older
    build's fragment merged in here does not replace anything — it ADDS a group the
    source deleted, and the project then runs a hook nobody wired on purpose.
    """
    behind = hook_fragments_behind(root)
    if not behind:
        return None
    source_path = PACKAGED_ASSET_SOURCE_DIR / HOOKS_SOURCE_SUBDIR
    return _source_repo_refusal(
        root,
        command="kyo install-hooks",
        what=f"hook templates ({', '.join(sorted(behind))})",
        source_path=source_path,
        instead=[
            f"move this copy to a build made from this checkout ({_reinstall_route()}) "
            "and run it again",
            f"or merge the group by hand out of {source_path} into "
            f"{CLAUDE_SETTINGS_REL} — `kyo doctor`'s hook_config check names exactly "
            "which command is not wired",
        ],
    )


# -------------------------------------------- KYO-60: the human half, in git itself

#: The marker line that identifies a ``pre-push`` hook as this tool's. Presence is
#: how :func:`install_git_pre_push_hook` tells "already installed" from "the
#: consumer has a pre-push hook of their own", which it never overwrites.
PRE_PUSH_MARKER = "# installed by `kyo install-hooks` (KYO-60)"

#: Warns, and exits 0 whatever happens. The asymmetry with the ``PreToolUse`` hook
#: is the design, not an oversight: an agent asking to push has no standing to
#: publish the maintainer's private thinking, so that hook *blocks*; a human
#: pushing their own repository owns that decision, so this one only tells them
#: what it is. `git push --no-verify` skips it entirely, which is correct.
#:
#: ``|| true`` is load-bearing twice over: `kyo check-push` exits 2 by design when
#: it has something to say, and a `kyo` that is not on this shell's PATH at all
#: must not turn every push in the repository into a failure.
PRE_PUSH_SCRIPT = f"""#!/bin/sh
{PRE_PUSH_MARKER}
#
# Advisory only. Prints, once, what pushing this repository would publish from its
# Kyosei store, and never blocks: `git push --no-verify` skips it, and so does a
# machine where `kyo` is not installed. Silence means one of three things -- the
# store is not in this repository, somebody has already acknowledged this, or
# there is no `kyo` here.
#
# NOTE: .git/hooks is not versioned. A fresh clone of this repository does NOT
# have this file; run `kyo harness install claude-code` there to get it back
# (`kyo install-hooks` in a project on the tracked copies).
command -v kyo >/dev/null 2>&1 || exit 0
kyo check-push --advisory || true
exit 0
"""


def git_hooks_dir(code_root: Path) -> Path | None:
    """Where this repository's git hooks live, honouring ``core.hooksPath``.

    Asked of git rather than assumed to be ``.git/hooks``: a repository that sets
    ``core.hooksPath`` (or a worktree, whose ``.git`` is a file) would otherwise
    get a hook written somewhere git never reads, and a hook nothing runs is worse
    than no hook at all because it looks installed.
    """
    result = _git(code_root, "rev-parse", "--git-path", "hooks")
    if result is None or result.returncode != 0:
        return None
    shown = result.stdout.strip()
    if not shown:
        return None
    path = Path(shown)
    return path if path.is_absolute() else code_root / path


def git_common_dir(code_root: Path) -> Path | None:
    """This repository's own git directory — the common one, which a linked
    worktree shares with its main checkout and where its ``hooks/`` live — or
    ``None`` when git cannot say."""
    result = _git(code_root, "rev-parse", "--git-common-dir")
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return None
    path = Path(result.stdout.strip())
    return path if path.is_absolute() else code_root / path


#: KYO-416.c6: the line a consumer adds by hand to a pre-push hook Kyosei will not
#: write for them. Guarded on `kyo` existing, on :data:`PRE_PUSH_SCRIPT`'s terms — a
#: machine without `kyo` must not fail every push — but without that script's
#: ``exit 0``, which would end a shared hook before whatever else it runs.
PRE_PUSH_MANUAL_LINE = (
    "command -v kyo >/dev/null 2>&1 && { kyo check-push --advisory || true; }"
)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def install_git_pre_push_hook(
    code_root: Path, *, print_only: bool = False
) -> tuple[list[str], list[str]]:
    """Install the advisory ``pre-push`` hook. Returns ``(changes, notes)``.

    Called by `kyo install-hooks` on the copies and, since KYO-612, by `kyo harness
    install claude-code` and `kyo harness migrate` on the plugin route: the plugin's
    ``PreToolUse`` hook covers an agent's push, and nothing in a plugin sees a push
    typed by hand.

    Never fatal and never destructive. A consumer's own ``pre-push`` is left
    exactly as it is and *reported*, because this file is theirs and a hook that
    silently replaced it could break a push discipline we know nothing about.

    The two return lists are separate because ``install-hooks`` is contractually
    idempotent — a second run with nothing to do reports no changes — and the
    reasons this half could not install (no git, somebody else's hook) are true
    on every run. Filed as changes they would make every run report a change
    forever; left out entirely they would be a silent failure to install the only
    warning a human at a terminal ever sees. So they are notes: stated every time,
    counted as a change never.
    """
    hooks_dir = git_hooks_dir(code_root)
    if hooks_dir is None:
        return [], [
            f"git pre-push: {code_root} is not a git repository (or git is not on PATH), "
            "so the advisory pre-push hook was not installed"
        ]
    target = hooks_dir / "pre-push"
    # KYO-416.c6: `--git-path hooks` honours `core.hooksPath`, which may be a global
    # dotfiles directory every repository on the machine runs (`~/.githooks`) or a
    # tracked directory inside this one (husky's `.husky/`). Writing there would make
    # every repository run `kyo check-push`, or add a tracked file to the consumer's
    # code that the script's own ".git/hooks is not versioned" note calls a lie. So a
    # hooks directory outside this repository's own git dir is never written: the
    # consumer is told where it is and what to add, and decides.
    own_git_dir = git_common_dir(code_root)
    if own_git_dir is None or not _is_within(hooks_dir, own_git_dir):
        return [], [
            f"git pre-push: this repository's hooks directory is {hooks_dir} "
            "(core.hooksPath), which is outside its own git directory — shared with "
            "other repositories, or tracked in this one — so no pre-push hook was "
            f"written there. To get the advisory warning, add this line to "
            f"{target} yourself: {PRE_PUSH_MANUAL_LINE}"
        ]
    if target.is_file():
        existing = target.read_text(encoding="utf-8", errors="replace")
        if PRE_PUSH_MARKER in existing:
            if existing == PRE_PUSH_SCRIPT:
                return [], []
            if not print_only:
                target.write_text(PRE_PUSH_SCRIPT, encoding="utf-8")
                target.chmod(0o755)
            return [f"git pre-push: updated {target}"], []
        # KYO-612.c2: the same guarded line the core.hooksPath note names, on both
        # routes that call this (`kyo install-hooks` and `kyo harness install`).
        return [], [
            f"git pre-push: {target} already exists and is not ours, so it was left alone "
            "— to get the advisory warning before a push, add this line to it yourself: "
            f"{PRE_PUSH_MANUAL_LINE}"
        ]
    if not print_only:
        hooks_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(PRE_PUSH_SCRIPT, encoding="utf-8")
        target.chmod(0o755)
    return [
        f"git pre-push: wrote {target} (advisory; `git push --no-verify` skips it, and "
        ".git/hooks is not versioned, so a fresh clone will not have it)"
    ], []


def install_hooks(
    root: Path, *, print_only: bool = False
) -> tuple[dict, list[str], Path, list[str]]:
    """``kyo install-hooks`` (KYO-13.c1): merge the packaged Claude Code hook
    templates into ``<root>/.claude/settings.json``, and (KYO-60) install the
    advisory git ``pre-push`` hook beside them.

    Returns ``(settings, changes, path, notes)``. Idempotent — a second call with
    nothing changed on disk produces the same ``changes == []`` result and writes
    byte-identical content; ``notes`` may repeat, since a note states a standing
    condition rather than something this run did. ``print_only`` (``--print``)
    computes and returns the merged document without writing it, so a controller
    can preview exactly what would land before it does.

    KYO-318.c2: refuses to write in the repository this package is built from when the
    running build's fragments have fallen behind that checkout's own
    (:func:`install_hooks_refusal`). ``print_only`` is exempt and carries the refusal as
    a ``note`` instead — a preview that said nothing would show a document this command
    would never write, which is worse than showing it with the reason beside it.
    """
    refusal = install_hooks_refusal(root)
    if refusal is not None and not print_only:
        raise KyoseiError(refusal)
    settings_path = root / CLAUDE_SETTINGS_REL
    existing: dict = {}
    if settings_path.is_file():
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise KyoseiError(f"{settings_path} is not valid JSON: {exc}") from exc
        if not isinstance(existing, dict):
            raise KyoseiError(f"{settings_path} must contain a JSON object at its top level")

    new_settings, changes = merge_hooks(existing)
    # KYO-60's other half. The harness hooks above bind agents; this one is for the
    # human at the keyboard, and it lives in git rather than in settings.json
    # because that is the only place a `git push` typed by hand passes through.
    pre_push_changes, notes = install_git_pre_push_hook(root, print_only=print_only)
    changes.extend(pre_push_changes)
    # KYO-206.c1. Here rather than only in `kyo init` because the projects that most
    # need it are the ones that already exist: `install-hooks` is what a consumer runs
    # to pick up a new guard, and the `SubagentStop` hook this command installs is the
    # exact mechanism that made workers commit their reports.
    ignore_changes, ignore_notes = install_report_gitignore(root, print_only=print_only)
    changes.extend(ignore_changes)
    notes.extend(ignore_notes)
    if refusal is not None:
        # Reachable only under `--print` (a real run raised above). A note rather than
        # a change: it is a standing condition this run did not create and could not
        # clear, which is exactly what `notes` is for.
        notes.append(
            "this is a preview only, and a real run is REFUSED here — "
            + refusal.splitlines()[0]
        )
    if not print_only:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(new_settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return new_settings, changes, settings_path, notes
