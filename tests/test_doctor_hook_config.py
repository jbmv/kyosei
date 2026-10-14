"""``kyo doctor``'s hook-currency check, and the element-wise comparison it shares
with ``kyo install-hooks``.

Kyosei ships consumers two kinds of thing: code, which updates the moment the
package does, and configuration, which is a *copy* and never does. Nothing told a
consumer their copy had gone stale. When KYO-17 fixed the `SubagentStop` loop that
had fired one project's worker ten times, the fix reached their `kyo` instantly
and their `.claude/settings.json` not at all — and the only reason anyone noticed
was that a human asked.

The comparison is element-wise on purpose. That project's `Stop` hook is
`kyo doctor && kyo sweep && kyo html` — their own workaround for a generated site
that read as current after it had gone stale.
Compared as whole strings it reads as *missing* the packaged `kyo doctor && kyo
sweep`, which would make this check cry wolf at exactly the consumer most likely
to be doing the right thing, and made `install-hooks` add a duplicate. Both now
read `install_hooks.command_elements`, so they cannot disagree about what "installed"
means.

Element-wise has two blind spots, and both are covered at the bottom of this module
by the exact-string path instead. KYO-31: an installed chain in the WRONG ORDER has
the same elements as the right one. KYO-215: an installed chain that still runs a
command the build REMOVED is a strict superset of the packaged one, so containment
calls it fully current while it runs a `kyo html` this build no longer has.

KYO-362 adds a third, and it changes what the two tests below can assert as their
premise. The packaged closeout chain now ends in `kyo doctor --hook`, and a FLAG is
a third thing containment cannot carry (KYO-94 first): `kyo doctor` and
`kyo doctor --hook` are two different `&&`-separated elements, so an installed chain
running the bare form reads as missing a command and gets a SECOND group wired beside
it — doctor twice per stop, once in the form that fills the terminal. So those two
settings files are no longer *entirely* current by containment; what is still true of
them, and what the premise now states, is that they already run every packaged
COMMAND, and that containment's answer to all three blind spots is to add rather than
to rewrite. :func:`_command_words` is that comparison, and the assertions that the old
line is GONE afterwards are what keep both tests about supersession.
"""

from __future__ import annotations

import json
from pathlib import Path

from kyosei.install_hooks import (
    PATH_GUARD_MESSAGE,
    STOP_HOOK_COMMAND,
    SUPERSEDED_HOOK_COMMANDS,
    _unwalked_guard_prefix,
    command_elements,
    guarded,
    hook_action,
    installed_elements,
    packaged_elements,
)

from .test_hook_templates_sync import SESSIONS_RECIPE, STOP_RECIPE


def _settings(project: Path) -> Path:
    return project / ".claude" / "settings.json"


def _command_words(elements: set[str]) -> set[str]:
    """Each element's command, with its flags dropped — exactly what containment was
    comparing before KYO-362 put `--hook` on the end of the closeout chain.

    Only the two setup assertions below use it, and only to say "this settings file
    already runs every packaged command". Nothing in ``src/kyosei/`` compares this way:
    a flag is a real difference in what a hook does, and the whole point of the
    supersession entry KYO-362 added is that containment must NOT be taught to squint
    past it.
    """
    return {element.split(" --")[0].strip() for element in elements}


def _write_settings(project: Path, doc: dict) -> None:
    path = _settings(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _replace_installed(kyo, project: Path, event: str | tuple[str, ...], old: str) -> None:
    """Install today's hooks, then put ``old`` in place of the packaged line it is
    superseded by under ``event`` -- a project that is current except for that one line,
    so a finding can only be about that line (KYO-604)."""
    assert kyo("install-hooks", "--copies").code == 0
    replacement = SUPERSEDED_HOOK_COMMANDS[old]
    events = (event,) if isinstance(event, str) else event
    settings = json.loads(_settings(project).read_text(encoding="utf-8"))
    replaced = 0
    for name in events:
        for group in settings["hooks"][name]:
            for hook in group["hooks"]:
                if hook["command"] == replacement:
                    hook["command"] = old
                    replaced += 1
    assert replaced == len(events), (old, replacement)
    _write_settings(project, settings)


def test_a_project_with_no_settings_file_is_not_behind(kyo, project: Path) -> None:
    """Hooks are a convenience, not a requirement for driving `kyo` by hand — a
    project that never installed them has nothing to be stale."""
    assert not _settings(project).exists()
    result = kyo("doctor")
    assert result.code == 0, result
    assert "hook_config" in result.out


def test_freshly_installed_hooks_pass(kyo, project: Path) -> None:
    assert kyo("install-hooks", "--copies").code == 0
    assert kyo("doctor").code == 0


def test_a_superseded_hook_is_reported(kyo, project: Path) -> None:
    """The case that motivated this: a consumer bitten by the SubagentStop loop,
    whose settings still ran the half that caused it after the fix shipped.

    KYO-604: reported as a NOTICE under ``hook_config_superseded``, which exits 0 on its
    own; ``hook_config`` itself is clean, because nothing packaged is missing."""
    superseded = next(iter(SUPERSEDED_HOOK_COMMANDS))
    _replace_installed(kyo, project, "SubagentStop", superseded)
    result = kyo("doctor", "--json")
    assert result.code == 0, result
    checks = result.json["checks"]
    assert checks["hook_config"]["ok"], checks["hook_config"]
    notice = checks["hook_config_superseded"]
    assert notice["tier"] == "notice"
    assert "supersedes" in json.dumps(notice["failures"])
    assert "kyo install-hooks" in json.dumps(notice["failures"])


def test_install_hooks_clears_what_doctor_reports(kyo, project: Path) -> None:
    """CLAUDE.md's standing rule: an error naming a remedy ships with a test that
    the remedy clears it. Both messages here name `kyo install-hooks`: the superseded
    line (a notice since KYO-604) and every packaged command the file does not run."""
    superseded = next(iter(SUPERSEDED_HOOK_COMMANDS))
    _write_settings(
        project,
        {"hooks": {"SubagentStop": [{"hooks": [{"type": "command", "command": superseded}]}]}},
    )
    before = kyo("doctor", "--json")
    assert before.code != 0
    assert before.json["checks"]["hook_config_superseded"]["failures"]
    assert kyo("install-hooks", "--copies").code == 0
    after = kyo("doctor", "--json")
    assert after.code == 0
    assert after.json["checks"]["hook_config"]["ok"]
    assert after.json["checks"]["hook_config_superseded"]["ok"]


def test_a_missing_packaged_hook_is_reported(kyo, project: Path) -> None:
    _write_settings(
        project,
        {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}},
    )
    result = kyo("doctor")
    assert result.code != 0
    assert "kyo brief --compact" in result.err


def _extend_stop_hooks(project: Path, extension: str) -> None:
    """Append a step of the consumer's own to the installed Stop/SessionEnd line,
    the way a real consumer extends a hook: one more `&&` element, not a new entry."""
    settings = json.loads(_settings(project).read_text(encoding="utf-8"))
    for event in ("Stop", "SessionEnd"):
        for group in settings["hooks"][event]:
            for hook in group["hooks"]:
                # KYO-316: what is installed is the closeout chain behind the PATH
                # guard, and a consumer extends THAT line -- the guard stays in front,
                # since it is a precondition rather than a step.
                if hook["command"] == guarded(STOP_RECIPE):
                    hook["command"] = f"{guarded(STOP_RECIPE)} && {extension}"
    _write_settings(project, settings)


def test_a_consumer_extended_command_still_counts_as_installed(kyo, project: Path) -> None:
    """The false positive this check must not produce. A line that runs every
    packaged command plus one of the consumer's own is not behind; flagging it would
    train a consumer to ignore the check, and the consumer in question extended this
    very line to close a real gap the tool had left them."""
    assert kyo("install-hooks", "--copies").code == 0
    _extend_stop_hooks(project, "make docs")

    result = kyo("doctor")
    assert result.code == 0, result


def test_install_hooks_does_not_duplicate_an_extended_command(kyo, project: Path) -> None:
    """The same comparison, on the other side. Adding a second group here is not
    cosmetic: every `kyo sweep` writes a sweep run and advances the prior-sha
    bookkeeping that touch-changed staleness reads."""
    assert kyo("install-hooks", "--copies").code == 0
    _extend_stop_hooks(project, "make docs")

    result = kyo("install-hooks", "--copies", "--json")
    assert result.code == 0, result
    stop = [h["command"] for g in result.json["settings"]["hooks"]["Stop"] for h in g["hooks"]]
    # The consumer's extended closeout line, still one entry and not two. KYO-186
    # put a SECOND packaged fragment on this event (`kyo sessions --hook`), so what
    # is asserted is that the closeout line was not duplicated -- not that it is the
    # only thing `Stop` runs, which stopped being true and was never this test's
    # subject.
    assert stop.count(f"{guarded(STOP_RECIPE)} && make docs") == 1, stop
    assert not any(command == guarded(STOP_RECIPE) for command in stop), stop
    assert stop == [f"{guarded(STOP_RECIPE)} && make docs", guarded(SESSIONS_RECIPE)], stop


# ------------------------------------- KYO-31: what the containment check cannot see


def test_the_doctor_first_stop_hook_is_reported_as_superseded(kyo, project: Path) -> None:
    """KYO-31.c3, second half: doctor must say so *before* install-hooks is run.
    Containment alone reads this settings file as entirely current — it runs every
    packaged command — and the ordering is the whole defect, so the report can only
    come from the exact-string supersession path."""
    doctor_first = "kyo doctor && kyo sweep && kyo html"
    _replace_installed(kyo, project, ("Stop", "SessionEnd"), doctor_first)

    # No packaged COMMAND is missing here — this settings file runs every one of them,
    # and the ordering is the whole defect (KYO-362 added a flag to the last of them,
    # which containment cannot carry either; see this module's docstring). KYO-186 added
    # a second, unrelated packaged fragment on this event, which this hand-written
    # file legitimately does not have; subtracting it too would make this setup
    # assertion about that fragment instead of about the ordering it exists to
    # isolate.
    closeout = set(command_elements(STOP_RECIPE))
    assert not _command_words(closeout) - _command_words(
        installed_elements(json.loads(_settings(project).read_text(encoding="utf-8")), "Stop")
    )
    assert closeout < packaged_elements("Stop"), "the closeout chain is one of several"

    # KYO-604: an earlier build's hook is a notice only while it still works; this one
    # calls `kyo html`, which this build does not have, so it stays a problem.
    result = kyo("doctor", "--json")
    assert result.code != 0, result
    assert not result.json["checks"]["hook_config"]["ok"]
    findings = json.dumps(result.json["checks"]["hook_config"])
    assert "supersedes" in findings
    assert "`kyo html`" in findings, "the report names the subcommand that is gone"
    assert STOP_RECIPE in findings, "the report names the ordering to move to"
    assert "kyo install-hooks" in findings

    # And the remedy the message names clears it (CLAUDE.md's standing rule).
    assert kyo("install-hooks", "--copies").code == 0
    after = kyo("doctor", "--json").json["checks"]
    assert after["hook_config"]["ok"] and after["hook_config_superseded"]["ok"]
    # Rewritten, not joined: containment's answer to an ordering defect is to add a
    # second group and leave the doctor-first chain firing beside it (KYO-362 made
    # that concrete — the flag alone would have triggered exactly that), so the line
    # being GONE is what says supersession did this.
    stop = [
        h["command"]
        for g in json.loads(_settings(project).read_text(encoding="utf-8"))["hooks"]["Stop"]
        for h in g["hooks"]
    ]
    assert doctor_first not in stop, "the doctor-first chain was left beside its replacement"
    assert guarded(STOP_RECIPE) in stop


def test_a_stop_chain_that_still_runs_the_retired_kyo_html_is_reported(
    kyo, project: Path
) -> None:
    """KYO-215.c2. The other thing containment cannot see: a REMOVED command.

    `kyo sweep && kyo html && kyo doctor` is what KYO-31 shipped and what every
    project installed between then and now. Its element set is a strict SUPERSET of
    the packaged chain, so the containment check reads it as entirely current — the
    consumer would keep running a `kyo html` this build does not have, their closeout
    would fail at step two on every stop, and `kyo doctor` would say nothing about it.
    Only `install_hooks.SUPERSEDED_HOOK_COMMANDS` reaches this, and the message must name
    `kyo install-hooks`, which must then clear it (CLAUDE.md's standing rule).
    """
    retired = "kyo sweep && kyo html && kyo doctor"
    assert retired in SUPERSEDED_HOOK_COMMANDS
    _replace_installed(kyo, project, ("Stop", "SessionEnd"), retired)

    # The premise: every packaged command is already here, because this line is a
    # superset of them. Compared by command word since KYO-362 (this module's
    # docstring) — the packaged chain's last element carries `--hook` now, and if this
    # subtraction were done element-wise the test would pass for the ordinary
    # missing-command reason and prove nothing about removal.
    installed = installed_elements(
        json.loads(_settings(project).read_text(encoding="utf-8")), "Stop"
    )
    assert not _command_words(set(command_elements(STOP_RECIPE))) - _command_words(
        installed
    ), (
        "if this subtraction is non-empty the test is passing for the ordinary "
        "missing-command reason and proves nothing about removal"
    )

    result = kyo("doctor", "--json")
    assert result.code != 0, result  # KYO-604: it does not still work, so not a notice
    findings = json.dumps(result.json["checks"]["hook_config"])
    assert "supersedes" in findings
    assert "`kyo html`" in findings
    assert STOP_RECIPE in findings, "the report names the chain to move to"
    assert "kyo install-hooks" in findings

    assert kyo("install-hooks", "--copies").code == 0
    after = kyo("doctor", "--json").json["checks"]
    assert after["hook_config"]["ok"] and after["hook_config_superseded"]["ok"]
    stop = [
        h["command"]
        for g in json.loads(_settings(project).read_text(encoding="utf-8"))["hooks"]["Stop"]
        for h in g["hooks"]
    ]
    assert retired not in stop, "the retired chain was left beside its replacement"
    assert guarded(STOP_RECIPE) in stop


# ------------------------------------- KYO-316: a line with no missing-`kyo` guard


def test_a_hook_without_the_path_guard_is_reported_and_install_hooks_clears_it(
    kyo, project: Path
) -> None:
    """KYO-316.c2. Containment cannot see this one: `install_hooks.hook_action` makes the
    guarded and unguarded lines compare EQUAL on purpose, so `missing` is empty and
    doctor's ordinary "does not run X" path says nothing. Exact-string supersession is
    the only thing that reaches a project still running the bare line -- the one every
    consumer installed before this build -- and what that project loses without it is
    the message itself: on a machine with no `kyo` the hook fails with the shell's
    `command not found`, naming no remedy, and the guards silently do not run.
    """
    bare = "kyo check-clean --subagent-stop"
    assert bare in SUPERSEDED_HOOK_COMMANDS
    _replace_installed(kyo, project, "SubagentStop", bare)
    # The premise: containment sees nothing missing here, because the guard is not a
    # command. Without this the test could pass for the ordinary missing-command reason.
    installed = installed_elements(
        json.loads(_settings(project).read_text(encoding="utf-8")), "SubagentStop"
    )
    assert not set(command_elements(bare)) - installed

    result = kyo("doctor", "--json")
    assert result.code == 0, result
    findings = json.dumps(result.json["checks"]["hook_config_superseded"]["failures"])
    assert "PATH guard" in findings, "the report says what is missing"
    assert "kyo install-hooks" in findings

    assert kyo("install-hooks", "--copies").code == 0
    assert kyo("doctor", "--json").json["checks"]["hook_config_superseded"]["ok"]
    commands = [
        h["command"]
        for g in json.loads(_settings(project).read_text(encoding="utf-8"))["hooks"][
            "SubagentStop"
        ]
        for h in g["hooks"]
    ]
    assert bare not in commands, "the unguarded line survived the upgrade"
    assert commands.count(guarded(bare)) == 1, commands


# ------------------------------------------- KYO-269: the SessionEnd 1.5s budget


def test_a_sessionend_hook_missing_the_packaged_timeout_is_reported(kyo, project: Path) -> None:
    """KYO-269.c3. The state every existing consumer is in before this fix: the
    packaged command installed under `SessionEnd`, no `timeout` at all. Claude
    Code's `SessionEnd` hooks share a 1.5s budget across all hooks combined unless
    one sets its own, so this settings file is silently cutting `kyo doctor` off
    at every session end -- and nothing but this check would ever say so."""
    _write_settings(
        project,
        {
            "hooks": {
                "SessionEnd": [
                    {"hooks": [{"type": "command", "command": STOP_RECIPE}]},
                    {"hooks": [{"type": "command", "command": SESSIONS_RECIPE}]},
                ]
            }
        },
    )
    result = kyo("doctor")
    assert result.code != 0
    assert "hook_config" in result.err
    assert "timeout" in result.err
    assert "kyo install-hooks" in result.err

    # And the remedy the message names clears it (CLAUDE.md's standing rule).
    assert kyo("install-hooks", "--copies").code == 0
    assert kyo("doctor").code == 0


def test_a_hand_set_sessionend_timeout_at_least_the_packaged_value_is_not_reported(
    kyo, project: Path
) -> None:
    """KYO-269.c3's other half: a consumer project set `timeout: 30` on both
    `SessionEnd` hooks by hand, ahead of the packaged fix. That consumer is not behind and must
    not be reported. Starts from a real, fully-installed settings file so nothing
    unrelated is missing and drifting doctor's report for a different reason."""
    assert kyo("install-hooks", "--copies").code == 0
    settings = json.loads(_settings(project).read_text(encoding="utf-8"))
    for group in settings["hooks"]["SessionEnd"]:
        for hook in group["hooks"]:
            hook["timeout"] = 30
    _write_settings(project, settings)

    result = kyo("doctor")
    assert result.code == 0, result


def test_unreadable_settings_are_reported_not_crashed(kyo, project: Path) -> None:
    path = _settings(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    result = kyo("doctor")
    assert result.code != 0
    assert "not valid JSON" in result.err


# ---------------------- KYO-604: an earlier build's hooks are a notice, not a failure

#: The `Stop`/`SessionEnd` chain every build from KYO-362 to KYO-591 packaged.
EARLIER_STOP_CHAIN = "kyo sweep && kyo doctor --hook"


def _earlier_build_settings(kyo, project: Path) -> dict:
    """Exactly what the build before KYO-591 installed: today's hooks, each behind the
    guard without the marker walk, and the closeout chain without `kyo sweep --hook`.
    Derived from today's install so that a hook added later is covered too; every line
    is asserted to be one :data:`SUPERSEDED_HOOK_COMMANDS` knows, so the fixture cannot
    drift into describing a build that never existed."""
    assert kyo("install-hooks", "--copies").code == 0
    settings = json.loads(_settings(project).read_text(encoding="utf-8"))
    prefix = _unwalked_guard_prefix(PATH_GUARD_MESSAGE)
    for groups in settings["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                action = hook_action(hook["command"])
                if action == STOP_HOOK_COMMAND:
                    action = EARLIER_STOP_CHAIN
                hook["command"] = prefix + action
                assert hook["command"] in SUPERSEDED_HOOK_COMMANDS, hook["command"]
    _write_settings(project, settings)
    return settings


def test_an_earlier_builds_hooks_are_notices_and_the_stop_hook_exits_0(
    kyo, project: Path
) -> None:
    """KYO-604.c1: a consumer's state after KYO-591's build -- every hook a line the
    earlier build packaged. Each is a notice naming `kyo install-hooks`; nothing is a
    problem, so `kyo doctor --hook`, the last step of the Stop chain, exits 0. Before
    KYO-604 this exited 3 on every stop, and the earlier Stop chain's lack of
    `kyo sweep --hook` was reported as a missing command on top."""
    settings = _earlier_build_settings(kyo, project)
    hooks = sum(len(g["hooks"]) for groups in settings["hooks"].values() for g in groups)

    result = kyo("doctor", "--hook", "--json")
    assert result.code == 0, result
    checks = result.json["checks"]
    assert checks["hook_config"]["ok"], checks["hook_config"]
    notices = checks["hook_config_superseded"]
    assert notices["tier"] == "notice"
    assert len(notices["failures"]) == hooks, notices["failures"]
    assert all("kyo install-hooks" in line for line in notices["failures"])
    assert any(STOP_HOOK_COMMAND in line for line in notices["failures"]), (
        "the earlier Stop chain names the chain it moves to"
    )
    assert not result.json["problems"]

    # The remedy the notice names clears it.
    assert kyo("install-hooks", "--copies").code == 0
    assert kyo("doctor", "--hook", "--json").json["checks"]["hook_config_superseded"]["ok"]


def test_an_edited_kyosei_hook_is_still_a_problem(kyo, project: Path) -> None:
    """KYO-604.c2, first half: a Kyosei command matching neither today's text nor any
    superseded one covers nothing, so the packaged command it no longer runs is missing
    and `kyo doctor --hook` fails."""
    edited = guarded("kyo sweep --hook && kyo doctor")
    assert edited not in SUPERSEDED_HOOK_COMMANDS
    assert kyo("install-hooks", "--copies").code == 0
    settings = json.loads(_settings(project).read_text(encoding="utf-8"))
    for group in settings["hooks"]["Stop"]:
        for hook in group["hooks"]:
            if hook["command"] == guarded(STOP_HOOK_COMMAND):
                hook["command"] = edited
    _write_settings(project, settings)

    result = kyo("doctor", "--hook", "--json")
    assert result.code == 3, result
    failures = json.dumps(result.json["checks"]["hook_config"]["failures"])
    assert "Stop does not run 'kyo doctor --hook'" in failures
    assert result.json["checks"]["hook_config_superseded"]["ok"]


def test_a_missing_packaged_hook_beside_superseded_ones_is_still_a_problem(
    kyo, project: Path
) -> None:
    """KYO-604.c2, second half: an earlier build's settings with one packaged hook
    deleted. The superseded lines stay notices; the deleted one fails the stop hook,
    because nothing superseded runs any form of it."""
    settings = _earlier_build_settings(kyo, project)
    del settings["hooks"]["PreToolUse"]
    _write_settings(project, settings)

    result = kyo("doctor", "--hook", "--json")
    assert result.code == 3, result
    failures = json.dumps(result.json["checks"]["hook_config"]["failures"])
    assert "PreToolUse does not run" in failures
    assert result.json["checks"]["hook_config_superseded"]["failures"]


def test_brief_and_onboard_check_still_name_superseded_hooks(kyo, project: Path) -> None:
    """KYO-604.c3: softening the doctor tier must not lose the finding. `kyo brief`'s
    asset-drift block and `kyo onboard --check` both still name the superseded hook and
    `kyo install-hooks`."""
    _earlier_build_settings(kyo, project)

    brief = kyo("brief", "--json")
    assert brief.code == 0, brief
    text = json.dumps(brief.json)
    assert "supersedes" in text
    assert "kyo install-hooks" in text

    onboard = kyo("onboard", "--check")
    combined = onboard.out + onboard.err
    assert "supersedes" in combined, combined
    assert "kyo install-hooks" in combined, combined
