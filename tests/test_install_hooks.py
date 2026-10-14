"""KYO-13.c1: ``kyo install-hooks`` merges the packaged Claude Code hook
templates into a consumer project's ``.claude/settings.json`` — idempotent,
merge-never-overwrite, with a ``--print`` preview mode.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from kyosei import buildinfo, install_hooks, install_procedures, install_skill, scaffold
from kyosei.contracts import WORKER_REPORT_PATH
from kyosei.install_hooks import (
    HOOK_INSTALL_TARGETS,
    PATH_GUARD_PREFIX,
    SUPERSEDED_HOOK_COMMANDS,
    guarded,
    hook_action,
)
from kyosei.install_skill import SKILL_REFERENCE_ASSET
from kyosei.scaffold import (
    HOOKS_SOURCE_SUBDIR,
    PACKAGED_ASSET_SOURCE_DIR,
    REPORT_GITIGNORE_MARKER,
    defaults_dir,
)

from .test_hook_templates_sync import SESSIONS_RECIPE, STOP_RECIPE


def _settings_path(project: Path) -> Path:
    return project / ".claude" / "settings.json"


def test_install_hooks_writes_settings_json_from_scratch(kyo, project: Path) -> None:
    assert not _settings_path(project).is_file()
    result = kyo("install-hooks", "--copies")
    assert result.code == 0, result
    assert _settings_path(project).is_file()

    settings = json.loads(_settings_path(project).read_text(encoding="utf-8"))
    hooks = settings["hooks"]
    assert "kyo brief --compact" in json.dumps(hooks["SessionStart"])
    # KYO-31: the order is part of the contract, so this asserts the whole line
    # rather than a substring of it — a `kyo doctor` sequenced ahead of a remedy
    # short-circuits the remedy its own message names.
    assert STOP_RECIPE in json.dumps(hooks["Stop"])
    assert STOP_RECIPE in json.dumps(hooks["SessionEnd"])
    subagent_stop = json.dumps(hooks["SubagentStop"])
    # KYO-17: was a raw `git status --porcelain`; now the CLI command that
    # excludes exactly what `check-branch` forbids, so the two halves agree.
    assert "kyo check-clean" in subagent_stop
    assert "kyo check-branch" in subagent_stop


def test_install_hooks_strips_the_comment_field(kyo, project: Path) -> None:
    """`comment` is a documentation-only field in the packaged templates -- not
    part of Claude Code's documented hook shape -- and must never land in a real
    settings.json."""
    kyo("install-hooks", "--copies")
    settings = json.loads(_settings_path(project).read_text(encoding="utf-8"))
    assert "comment" not in json.dumps(settings["hooks"])


def test_install_hooks_is_idempotent(kyo, project: Path) -> None:
    first = kyo("install-hooks", "--copies", "--json")
    assert first.code == 0
    assert first.json["changes"], "the first install should report what it added"
    on_disk_after_first = _settings_path(project).read_text(encoding="utf-8")

    second = kyo("install-hooks", "--copies", "--json")
    assert second.code == 0
    assert second.json["changes"] == []
    assert _settings_path(project).read_text(encoding="utf-8") == on_disk_after_first


def test_install_hooks_merges_never_overwrites_existing_settings(kyo, project: Path) -> None:
    settings_path = _settings_path(project)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Bash(ls)"]},
                "hooks": {
                    "PreCompact": [
                        {"matcher": "manual", "hooks": [{"type": "command", "command": "echo hi"}]}
                    ],
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    result = kyo("install-hooks", "--copies")
    assert result.code == 0

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["permissions"] == {"allow": ["Bash(ls)"]}
    # An event this build ships nothing for is untouched, wholesale.
    assert settings["hooks"]["PreCompact"] == [
        {"matcher": "manual", "hooks": [{"type": "command", "command": "echo hi"}]}
    ]
    # KYO-60 ships a `PreToolUse` hook of its own, so this event is now a merge
    # rather than a bystander: the consumer's group survives *and* ours is added
    # beside it, which is the actual claim this test is making.
    pre_tool_use = settings["hooks"]["PreToolUse"]
    assert {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]} in pre_tool_use
    assert "kyo check-push --pre-tool-use" in json.dumps(pre_tool_use)
    assert "SessionStart" in settings["hooks"]


def test_install_hooks_print_mode_writes_nothing(kyo, project: Path) -> None:
    result = kyo("install-hooks", "--copies", "--print", "--json")
    assert result.code == 0
    assert result.json["printed_only"] is True
    assert not _settings_path(project).exists()
    assert "SessionStart" in result.json["settings"]["hooks"]


# ------------------------------------------------------- KYO-17: upgrading a consumer


def test_install_hooks_removes_the_superseded_raw_git_check(kyo, project: Path) -> None:
    """`install-hooks` merges rather than replaces, so a consumer who upgrades would
    otherwise keep the old hook, which wrongly blocked their workers' stops, *and* gain
    its replacement — the broken half still firing. Removal is by exact command string."""
    settings_path = _settings_path(project)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    superseded = next(iter(SUPERSEDED_HOOK_COMMANDS))
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SubagentStop": [
                        {
                            "hooks": [
                                {"type": "command", "command": superseded},
                                {"type": "command", "command": "kyo check-branch --against main"},
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    result = kyo("install-hooks", "--copies")
    assert result.code == 0, result

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    commands = [h["command"] for g in settings["hooks"]["SubagentStop"] for h in g["hooks"]]
    assert superseded not in commands, "the superseded raw git check survived the upgrade"
    assert guarded("kyo check-clean --subagent-stop") in commands
    # KYO-128: the bare `kyo check-branch --against main` this fixture starts from is
    # itself superseded now — it audits whatever cwd the hook inherited, which is the
    # controller's checkout and never the stopping worker's worktree — so the upgrade
    # must REMOVE it, not merely avoid duplicating it. A settings file left carrying
    # both would go on blocking every stop against the controller's tree while the
    # corrected line sat beside it doing the right thing unheard.
    assert "kyo check-branch --against main" not in commands, (
        "the bare merge guard, which reads the wrong tree, survived the upgrade"
    )
    assert commands.count(guarded("kyo check-branch --subagent-stop --against main")) == 1, (
        "the leftover group left the merge guard wired twice"
    )


def test_upgrading_replaces_the_bare_session_start_brief_and_does_not_wire_two(
    kyo, project: Path
) -> None:
    """KYO-94. The SessionStart fragment gained `--session-start`, and containment cannot
    carry it: `kyo brief --compact` and `kyo brief --compact --session-start` are two
    different commands, so without a supersession entry an upgraded consumer would keep
    the old line AND gain the new one — two briefs per session start, the second of them
    stamping a watermark the first had already moved.

    It also has to actually be an upgrade: the flag is the only thing that tells the store
    a session began, so a settings file still running the bare form gets the weaker
    "most recent shift note" fallback rather than the handoff for its own session."""
    settings_path = _settings_path(project)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "kyo brief --compact"}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    assert kyo("install-hooks", "--copies").code == 0

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    commands = [h["command"] for g in settings["hooks"]["SessionStart"] for h in g["hooks"]]
    # KYO-186 put a second packaged fragment on this event. What is asserted is
    # that the superseded line was REPLACED rather than kept alongside its
    # replacement -- not that the event runs one command, which stopped being
    # true and was never this test's subject.
    assert "kyo brief --compact" not in commands, commands
    assert commands.count(guarded("kyo brief --compact --session-start")) == 1, commands
    assert commands == [
        guarded("kyo brief --compact --session-start"),
        guarded(SESSIONS_RECIPE),
    ], commands


def test_install_hooks_leaves_a_consumer_edited_hook_alone(kyo, project: Path) -> None:
    """Removal is by exact string on purpose: `.claude/settings.json` is a file we
    do not own, so anything a consumer changed themselves no longer matches and
    stays. Conservative is the right side to err on here."""
    settings_path = _settings_path(project)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    edited = next(iter(SUPERSEDED_HOOK_COMMANDS)).replace("exit 2", "exit 3")
    settings_path.write_text(
        json.dumps(
            {"hooks": {"SubagentStop": [{"hooks": [{"type": "command", "command": edited}]}]}}
        ),
        encoding="utf-8",
    )

    assert kyo("install-hooks", "--copies").code == 0
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    commands = [h["command"] for g in settings["hooks"]["SubagentStop"] for h in g["hooks"]]
    assert edited in commands


def test_install_hooks_is_still_idempotent_after_the_migration(kyo, project: Path) -> None:
    assert kyo("install-hooks", "--copies").code == 0
    first = _settings_path(project).read_text(encoding="utf-8")
    again = kyo("install-hooks", "--copies", "--json")
    assert again.code == 0
    assert again.json["changes"] == []
    assert _settings_path(project).read_text(encoding="utf-8") == first


# ------------------------------------------------- KYO-31: upgrading a hook's *order*


def _packaged_commands() -> set[str]:
    """Every whole command line the packaged fragments install, as installed."""
    source = defaults_dir()
    assert source is not None, "this checkout ships no defaults/ directory"
    commands: set[str] = set()
    for _event, name in HOOK_INSTALL_TARGETS:
        fragment = json.loads((source / "hooks" / name).read_text(encoding="utf-8"))
        (groups,) = fragment.values()
        for group in groups:
            for hook in group["hooks"]:
                commands.add(hook["command"])
    return commands


def test_install_hooks_rewrites_the_doctor_first_stop_hook(kyo, project: Path) -> None:
    """KYO-31.c3, first half. Element containment cannot carry an ordering change —
    the broken line and the fixed one have identical element sets — so exact-string
    supersession is the only thing that reaches a consumer who already installed the
    doctor-first recipe. It is what `kyo doctor` told them to run."""
    settings_path = _settings_path(project)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    doctor_first = "kyo doctor && kyo sweep && kyo html"
    assert doctor_first in SUPERSEDED_HOOK_COMMANDS
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    event: [{"hooks": [{"type": "command", "command": doctor_first}]}]
                    for event in ("Stop", "SessionEnd")
                }
            }
        ),
        encoding="utf-8",
    )

    assert kyo("install-hooks", "--copies").code == 0

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    for event in ("Stop", "SessionEnd"):
        commands = [h["command"] for g in settings["hooks"][event] for h in g["hooks"]]
        # KYO-186 put a second packaged fragment on this event. What is asserted is
        # that the superseded line was REPLACED rather than kept alongside its
        # replacement -- not that the event runs one command, which stopped being
        # true and was never this test's subject.
        assert commands.count(guarded(STOP_RECIPE)) == 1, f"{event}: {commands}"
        assert commands == [guarded(STOP_RECIPE), guarded(SESSIONS_RECIPE)], f"{event}: {commands}"


def test_install_hooks_rewrites_a_stop_hook_that_still_runs_the_retired_kyo_html(
    kyo, project: Path
) -> None:
    """KYO-215.c2. The removal case, which is containment's other blind spot.

    An installed `kyo sweep && kyo html && kyo doctor` runs every command the packaged
    chain runs and one more, so `merge_hooks` finds nothing missing and adds nothing:
    left to containment the consumer keeps a `kyo html` this build does not ship, on
    every stop, forever. What is asserted is that the line was REPLACED — the retired
    chain gone, its replacement there once.
    """
    settings_path = _settings_path(project)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    retired = "kyo sweep && kyo html && kyo doctor"
    assert retired in SUPERSEDED_HOOK_COMMANDS
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    event: [{"hooks": [{"type": "command", "command": retired}]}]
                    for event in ("Stop", "SessionEnd")
                }
            }
        ),
        encoding="utf-8",
    )

    assert kyo("install-hooks", "--copies").code == 0

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    for event in ("Stop", "SessionEnd"):
        commands = [h["command"] for g in settings["hooks"][event] for h in g["hooks"]]
        assert retired not in commands, f"{event}: {commands}"
        assert commands.count(guarded(STOP_RECIPE)) == 1, f"{event}: {commands}"
        assert commands == [guarded(STOP_RECIPE), guarded(SESSIONS_RECIPE)], f"{event}: {commands}"


# --------------------------------------------------------- KYO-269: SessionEnd timeout


def _packaged_sessionend_timeouts() -> dict[str, int]:
    """Every packaged ``SessionEnd`` command mapped to its packaged ``timeout``,
    read directly off the fragment files — not through `scaffold`'s own helpers,
    so this test does not just restate the mechanism it is checking."""
    source = defaults_dir()
    assert source is not None, "this checkout ships no defaults/ directory"
    timeouts: dict[str, int] = {}
    for event, name in HOOK_INSTALL_TARGETS:
        if event != "SessionEnd":
            continue
        fragment = json.loads((source / "hooks" / name).read_text(encoding="utf-8"))
        (groups,) = fragment.values()
        for group in groups:
            for hook in group["hooks"]:
                if "timeout" in hook:
                    timeouts[hook["command"]] = hook["timeout"]
    return timeouts


def test_every_packaged_sessionend_hook_carries_a_timeout() -> None:
    """KYO-269.c1. Claude Code shares a 1.5s budget across every `SessionEnd` hook
    combined unless a hook sets its own `timeout` — with none, `kyo sweep && kyo
    doctor` and `kyo sessions --hook` were both cut off, silently, at every
    session end in every consumer. A packaged `SessionEnd` command with no
    `timeout` is exactly that defect, shipped again."""
    timeouts = _packaged_sessionend_timeouts()
    assert timeouts == {guarded(STOP_RECIPE): 20, guarded(SESSIONS_RECIPE): 5}
    for command, timeout in timeouts.items():
        assert isinstance(timeout, int) and 0 < timeout <= 60, (command, timeout)


def test_the_stop_event_does_not_receive_the_sessionend_timeout(kyo, project: Path) -> None:
    """The other half of c1: Stop hooks get the standard (600s) default, so
    installing the SessionEnd-sized `timeout` there would only ever shrink it.
    `install-hooks` must strip the key back out for this event."""
    assert kyo("install-hooks", "--copies").code == 0
    settings = json.loads(_settings_path(project).read_text(encoding="utf-8"))
    for hook in [h for g in settings["hooks"]["Stop"] for h in g["hooks"]]:
        assert "timeout" not in hook, hook
    for hook in [h for g in settings["hooks"]["SessionStart"] for h in g["hooks"]]:
        assert "timeout" not in hook, hook
    session_end_timeouts = {
        h["command"]: h.get("timeout")
        for g in settings["hooks"]["SessionEnd"]
        for h in g["hooks"]
    }
    assert session_end_timeouts == _packaged_sessionend_timeouts()


def test_install_hooks_sets_the_timeout_on_an_existing_sessionend_entry_in_place(
    kyo, project: Path
) -> None:
    """KYO-269.c2. The state every existing consumer is in: the packaged command
    already installed under `SessionEnd`, with no `timeout` at all (`install-hooks`
    predates this record). The fix must land ON that entry, not beside it."""
    settings_path = _settings_path(project)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionEnd": [
                        {"hooks": [{"type": "command", "command": guarded(STOP_RECIPE)}]},
                        {"hooks": [{"type": "command", "command": guarded(SESSIONS_RECIPE)}]},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    result = kyo("install-hooks", "--copies", "--json")
    assert result.code == 0, result

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    session_end = settings["hooks"]["SessionEnd"]
    hooks = [h for g in session_end for h in g["hooks"]]
    # One entry per command, not two -- the fix must not duplicate.
    assert [h["command"] for h in hooks].count(guarded(STOP_RECIPE)) == 1
    assert [h["command"] for h in hooks].count(guarded(SESSIONS_RECIPE)) == 1
    by_command = {h["command"]: h["timeout"] for h in hooks}
    assert by_command == _packaged_sessionend_timeouts()
    assert any("raised" in c and STOP_RECIPE in c for c in result.json["changes"]), result.json[
        "changes"
    ]


def test_install_hooks_print_previews_the_sessionend_timeout_fix(kyo, project: Path) -> None:
    """KYO-269.c2's ``--print`` half: the preview must show the same in-place fix
    `install-hooks` would actually make, without writing anything."""
    settings_path = _settings_path(project)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
            "hooks": {
                "SessionEnd": [
                    {"hooks": [{"type": "command", "command": guarded(STOP_RECIPE)}]}
                ]
            }
        }
        ),
        encoding="utf-8",
    )
    before = settings_path.read_text(encoding="utf-8")

    result = kyo("install-hooks", "--copies", "--print", "--json")
    assert result.code == 0, result
    assert result.json["printed_only"] is True
    assert settings_path.read_text(encoding="utf-8") == before, "print mode must write nothing"

    session_end = result.json["settings"]["hooks"]["SessionEnd"]
    hooks = [h for g in session_end for h in g["hooks"]]
    stop_hook = next(h for h in hooks if h["command"] == guarded(STOP_RECIPE))
    assert stop_hook["timeout"] == _packaged_sessionend_timeouts()[guarded(STOP_RECIPE)]


def test_install_hooks_leaves_a_hand_set_sessionend_timeout_at_or_above_packaged_alone(
    kyo, project: Path
) -> None:
    """KYO-269.c2's other edge: a consumer project that set `timeout: 30` by hand on
    both SessionEnd hooks before the packaged fix existed. `install-hooks`
    must not report or touch a value that already meets the bar. Starts from a
    real, fully-installed settings file (rather than a bare SessionEnd-only
    fixture) so the only thing left to change, if anything did, would be the
    timeout this test is about -- not some unrelated event this fixture never set up."""
    settings_path = _settings_path(project)
    assert kyo("install-hooks", "--copies").code == 0
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    for group in settings["hooks"]["SessionEnd"]:
        for hook in group["hooks"]:
            hook["timeout"] = 30
    settings_path.write_text(json.dumps(settings), encoding="utf-8")

    result = kyo("install-hooks", "--copies", "--json")
    assert result.code == 0, result
    assert result.json["changes"] == [], "a value already at or above packaged is not a change"

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    hooks = [h for g in settings["hooks"]["SessionEnd"] for h in g["hooks"]]
    assert {h["command"]: h["timeout"] for h in hooks} == {
        guarded(STOP_RECIPE): 30,
        guarded(SESSIONS_RECIPE): 30,
    }


def test_every_superseded_command_names_a_replacement_this_build_installs() -> None:
    """The mapping's values are quoted verbatim by `kyo doctor` ("supersedes with
    X") and by install-hooks' change log, but nothing installs them — the packaged
    fragment does. Without this test an entry silently keeps naming a command the
    build stopped shipping, which is the exact drift `hook_config` exists to catch
    (law 3: derive, or the validator fails)."""
    packaged = _packaged_commands()
    for superseded, replacement in SUPERSEDED_HOOK_COMMANDS.items():
        assert replacement in packaged, (
            f"{superseded!r} claims to be replaced by {replacement!r}, which this "
            f"build does not install; packaged commands are {sorted(packaged)}"
        )
        assert superseded not in packaged, (
            f"{superseded!r} is listed as superseded and still shipped"
        )


# ----------------------------------------------------- KYO-316: the missing-`kyo` guard
#
# Every command this build shipped BARE is superseded by the same command behind the
# PATH guard, and that pairing is the whole upgrade path. Containment cannot carry it,
# in the direction that is easy to miss: `install_hooks.hook_action` deliberately makes the
# guarded line and the bare line compare EQUAL — which is what stops a second group and
# a false "you are missing this command" — so `merge_hooks` would otherwise leave every
# existing consumer's unguarded line exactly where it is, forever.


def _bare_settings_as_shipped_before_the_guard() -> dict:
    """What `kyo install-hooks` wrote into a consumer's settings before KYO-316: the
    same groups with the guard taken back off. Derived from the packaged fragments
    rather than typed out, so this fixture cannot describe a release that never was."""
    source = defaults_dir()
    assert source is not None
    hooks: dict[str, list[dict]] = {}
    for event, name in HOOK_INSTALL_TARGETS:
        fragment = json.loads((source / "hooks" / name).read_text(encoding="utf-8"))
        (groups,) = fragment.values()
        for group in groups:
            hooks.setdefault(event, []).append(
                {
                    **{k: v for k, v in group.items() if k != "hooks"},
                    "hooks": [
                        {
                            k: (hook_action(v) if k == "command" else v)
                            for k, v in hook.items()
                            if k != "comment"
                        }
                        for hook in group["hooks"]
                    ],
                }
            )
    return {"hooks": hooks}


def test_an_existing_project_picks_up_the_guard_by_re_running_install_hooks(
    kyo, project: Path
) -> None:
    """KYO-316.c2's upgrade half, on every event at once: no bare line left behind, and
    no second copy of a check the project was already running."""
    settings_path = _settings_path(project)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    before = _bare_settings_as_shipped_before_the_guard()
    settings_path.write_text(json.dumps(before), encoding="utf-8")
    for event, groups in before["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                assert not hook["command"].startswith(PATH_GUARD_PREFIX), (event, hook)

    assert kyo("install-hooks", "--copies").code == 0

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    for event in {event for event, _ in HOOK_INSTALL_TARGETS}:
        ran_before = {
            hook["command"] for group in before["hooks"][event] for hook in group["hooks"]
        }
        commands = [h["command"] for g in settings["hooks"][event] for h in g["hooks"]]
        assert all(c.startswith(PATH_GUARD_PREFIX) for c in commands), f"{event}: {commands}"
        # One entry per check, not two: the bare line was REPLACED, not joined.
        assert len(commands) == len(set(commands)) == len(ran_before), f"{event}: {commands}"
        assert {hook_action(c) for c in commands} == ran_before, f"{event}: {commands}"


def test_the_guard_upgrade_is_idempotent(kyo, project: Path) -> None:
    """A second run changes nothing — the guarded line is not itself superseded, and
    containment reads it as present. Without the strip in `hook_action` this is exactly
    where a duplicate group would appear."""
    assert kyo("install-hooks", "--copies").code == 0
    first = _settings_path(project).read_text(encoding="utf-8")
    again = kyo("install-hooks", "--copies", "--json")
    assert again.code == 0
    assert again.json["changes"] == []
    assert _settings_path(project).read_text(encoding="utf-8") == first


def test_a_line_behind_the_retired_guard_message_is_carried_to_today_s(
    kyo, project: Path
) -> None:
    """KYO-450: the guard stopped offering the desktop app's retired "Install command
    line tools". Every project that installed hooks since KYO-316 carries the OLD
    message on every line, and :func:`hook_action` reads it as the same recipe (no
    duplicate group, no false "missing") -- so only supersession can move it, and one
    re-run of install-hooks must leave exactly today's lines, once each."""
    settings_path = _settings_path(project)
    assert kyo("install-hooks", "--copies").code == 0
    current = json.loads(settings_path.read_text(encoding="utf-8"))
    # KYO-591 retired a second prefix (the same message without the marker walk); this
    # test is about KYO-450's, the first.
    retired = install_hooks.RETIRED_PATH_GUARD_PREFIXES[0]
    before = json.loads(
        json.dumps(current).replace(
            json.dumps(PATH_GUARD_PREFIX)[1:-1], json.dumps(retired)[1:-1]
        )
    )
    old_lines = [
        h["command"] for groups in before["hooks"].values() for g in groups for h in g["hooks"]
    ]
    assert old_lines and all(c.startswith(retired) for c in old_lines)
    settings_path.write_text(json.dumps(before), encoding="utf-8")

    result = kyo("install-hooks", "--copies", "--json")
    assert result.code == 0, result
    assert any("current message" in change for change in result.json["changes"]), result.json

    after = json.loads(settings_path.read_text(encoding="utf-8"))
    for event, groups in current["hooks"].items():
        want = [h["command"] for g in groups for h in g["hooks"]]
        got = [h["command"] for g in after["hooks"][event] for h in g["hooks"]]
        assert sorted(got) == sorted(want), f"{event}: {got}"
    assert "Install command line tools" not in settings_path.read_text(encoding="utf-8")


def test_every_retired_guard_line_is_superseded_by_today_s() -> None:
    """The pairing KYO-450 derives, stated where it can fail: every packaged command,
    behind every retired message, maps to that command behind today's."""
    for command in _packaged_commands():
        for retired in install_hooks.RETIRED_PATH_GUARD_PREFIXES:
            old = retired + hook_action(command)
            assert hook_action(old) == hook_action(command)
            assert SUPERSEDED_HOOK_COMMANDS.get(old) == command, old


def test_every_bare_command_this_build_shipped_is_superseded_by_its_guarded_form() -> None:
    """The pairing itself, stated where it can fail: drop an entry and that hook's
    consumers keep the unguarded line silently, since containment cannot see the
    difference by design."""
    for command in _packaged_commands():
        bare = hook_action(command)
        assert bare != command, f"{command!r} carries no guard"
        assert SUPERSEDED_HOOK_COMMANDS.get(bare) == command, (
            f"the bare form {bare!r} is not superseded by the guarded one this build "
            "installs, so an existing project would never pick the guard up"
        )


# ------------------------------------------------------------------------ KYO-206
#
# c1's second half: the brief names one report path, and that path is gitignored in
# every consumer project WITHOUT the maintainer editing `.gitignore` by hand. A path a
# brief merely names still lands in `git status` as an untracked file — which is how a
# consumer's workers came to commit theirs and how `report.json` reached main three
# merges running.


def test_kyo_init_writes_the_report_ignore_entry(kyo, project: Path) -> None:
    """The `project` fixture IS a `kyo init` run. A new project ignores the path from
    the moment it exists."""
    ignored = (project / ".gitignore").read_text(encoding="utf-8")
    assert WORKER_REPORT_PATH in ignored.splitlines()
    assert REPORT_GITIGNORE_MARKER in ignored, (
        "a line in somebody's .gitignore has to say who wrote it and why"
    )


def test_install_hooks_adds_the_entry_to_a_project_that_predates_it(
    kyo, project: Path
) -> None:
    """The reader who needs this most is a project that already exists — which is the
    reader `kyo install-hooks` serves, and the command that installs the `SubagentStop`
    hook whose dirty check made workers commit their reports in the first place."""
    (project / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    result = kyo("install-hooks", "--copies")
    assert result.code == 0, result
    ignored = (project / ".gitignore").read_text(encoding="utf-8")
    assert ignored.startswith("*.pyc\n"), "an existing .gitignore is appended to, never rewritten"
    assert WORKER_REPORT_PATH in ignored.splitlines()
    assert f"gitignore: added {WORKER_REPORT_PATH}" in result.out


def test_the_report_ignore_entry_is_idempotent(kyo, project: Path) -> None:
    """`install-hooks` is contractually idempotent: a second run with nothing to do
    reports no change and writes byte-identical content."""
    first = kyo("install-hooks", "--copies", "--json")
    assert first.code == 0, first
    before = (project / ".gitignore").read_text(encoding="utf-8")
    second = kyo("install-hooks", "--copies", "--json")
    assert second.code == 0, second
    assert (project / ".gitignore").read_text(encoding="utf-8") == before
    assert not any("gitignore:" in change for change in second.json["changes"]), second.json


def test_a_hand_written_entry_is_left_alone(kyo, project: Path) -> None:
    """Matching is on the path line, not on our comment — a maintainer who added the
    entry themselves is not given a second copy of it."""
    (project / ".gitignore").write_text(f"# mine\n{WORKER_REPORT_PATH}\n", encoding="utf-8")
    result = kyo("install-hooks", "--copies", "--json")
    assert result.code == 0, result
    ignored = (project / ".gitignore").read_text(encoding="utf-8")
    assert ignored == f"# mine\n{WORKER_REPORT_PATH}\n"
    assert not any("gitignore:" in change for change in result.json["changes"])


def test_the_ignored_path_is_the_one_the_brief_names(kyo, git_project: Path) -> None:
    """c1's whole point is that these are ONE path. A brief naming a path nothing
    ignores, or an ignore entry no brief names, is the defect wearing new clothes."""
    from .conftest import new_record

    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record, "--json").code == 0
    brief = kyo("show", record, "--json").json["dispatches"][0]["brief"]
    report_section = brief.split("## Report")[1]
    assert WORKER_REPORT_PATH in report_section
    assert WORKER_REPORT_PATH in (git_project / ".gitignore").read_text(encoding="utf-8")
    assert "Do not commit the report itself" in report_section


# ------------------------------------------------------------------ KYO-298.c4
#
# The command guard travels to consumers the same way every other hook does — in the
# packaged fragment, through `kyo install-hooks` — and these are the three properties
# that make "upgrade and run install-hooks" a complete instruction: it arrives, it
# arrives beside check-push rather than instead of it, and running the command twice
# (or running it for the first time in a project that installed hooks before this
# build) does not wire anything twice.


def _pre_tool_use(project: Path) -> str:
    return json.dumps(
        json.loads(_settings_path(project).read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    )


def test_install_hooks_distributes_the_command_guard_beside_check_push(
    kyo, project: Path
) -> None:
    """c4: both `PreToolUse` checks are installed, and neither displaces the other.

    They answer different questions about the same tool call — may this agent publish
    the store, and may this worker run the whole suite here — so a consumer needs both
    wired, and a fragment change that dropped one would otherwise be invisible.
    """
    assert kyo("install-hooks", "--copies").code == 0

    installed = _pre_tool_use(project)
    assert "kyo check-push --pre-tool-use" in installed
    assert "kyo check-command --pre-tool-use" in installed
    assert "comment" not in installed


def test_the_command_guard_reaches_a_project_that_installed_hooks_before_it_existed(
    kyo, project: Path
) -> None:
    """The upgrade path, which is the only one that matters for a shipped mechanism.

    Hooks are a COPY and never update themselves. A consumer whose settings.json
    predates this build has a `PreToolUse` section holding check-push and nothing else
    — modelled here by deleting the new group — and what has to be true is that
    `kyo doctor` says so by name and one `kyo install-hooks` clears it.
    """
    assert kyo("install-hooks", "--copies").code == 0
    path = _settings_path(project)
    settings = json.loads(path.read_text(encoding="utf-8"))
    settings["hooks"]["PreToolUse"] = [
        group
        for group in settings["hooks"]["PreToolUse"]
        if "check-command" not in json.dumps(group)
    ]
    assert "check-push" in json.dumps(settings["hooks"]["PreToolUse"])
    path.write_text(json.dumps(settings), encoding="utf-8")

    reported = kyo("doctor", "--json")
    findings = json.dumps(reported.json["checks"]["hook_config"])
    assert "kyo check-command --pre-tool-use" in findings
    assert "kyo install-hooks" in findings

    assert kyo("install-hooks", "--copies").code == 0
    assert kyo("doctor", "--json").json["checks"]["hook_config"]["ok"]
    # ...and the half that was already there is still there exactly once.
    assert _pre_tool_use(project).count("kyo check-push --pre-tool-use") == 1


def test_installing_the_command_guard_twice_wires_it_once(kyo, project: Path) -> None:
    """c4's "idempotently", asserted where the shape makes it non-obvious.

    The guard ships as a SECOND matcher group inside the same fragment rather than as a
    second entry in check-push's group, and that is exactly why: `merge_hooks` decides
    `already_present` per group, over the union of the commands in it. As one group of
    two, a consumer who had check-push installed and not this would have had the whole
    group appended — check-push included — and would have run the publication guard
    twice on every Bash call ever after.
    """
    assert kyo("install-hooks", "--copies").code == 0
    first = _settings_path(project).read_text(encoding="utf-8")

    second = kyo("install-hooks", "--copies", "--json")

    assert second.json["changes"] == []
    assert _settings_path(project).read_text(encoding="utf-8") == first
    assert _pre_tool_use(project).count("kyo check-command --pre-tool-use") == 1


def test_the_hooks_readme_explains_what_the_command_guard_is_for(kyo) -> None:
    """c4's second half: `templates/hooks/README.md` is where a maintainer reads what a
    hook they are about to install actually does, and a blocking hook nobody can look
    up is one a maintainer removes rather than configures.

    What is asserted is that the section exists, names the config key that turns it on,
    and names the override — because those two are the whole of the consumer's decision.
    """
    readme = (
        Path(__file__).resolve().parent.parent / "templates" / "hooks" / "README.md"
    ).read_text(encoding="utf-8")

    section = readme.split("`kyo check-command --pre-tool-use`", 1)
    assert len(section) > 1, "the hooks README does not document the command guard"
    assert "controller_only_commands" in readme
    assert "KYOSEI_SUITE_OK" in readme
    # The file list at the bottom is what a reader scans first; a fragment that grew a
    # second command without it is a file list that quietly stopped being a list.
    assert "check-command --pre-tool-use" in readme.split("## The packaged fragments")[1]


# ----------------------------------------------------------------------------- KYO-318
#
# KYO-318.c2 asked whether `install_hooks.hook_drift` and `kyo install-hooks` have KYO-309's
# reversal against `src/kyosei/defaults/hooks/`, one step removed. KYO-309's worker said
# it "has the shape" and did not assert it, and KYO-316 changed how hook commands are
# compared in between (`hook_action`, the PATH guard), so it was established against the
# code as it is now rather than inherited.
#
# THE ANSWER IS YES, IN BOTH HALVES, and the first test below is the measurement: every
# comparison in `hook_drift` was made against `packaged_elements`, which reads the
# RUNNING BUILD's fragments through `defaults_dir()` and never looked for a source in
# the checkout being examined. So in this repository a hook group deleted from
# `src/kyosei/defaults/hooks/` and from `.claude/settings.json` was reported by an older
# installed build as one the project is MISSING, naming `kyo install-hooks` — which
# merges additively (KYO-27), so running it would have put the deleted group back rather
# than replacing anything. KYO-316's PATH-guard stripping does not touch any of that:
# `command_elements` strips the guard on both sides, so the reversal is about which
# DIRECTORY is read and survives the stripping untouched.
#
# Fixed on KYO-309's terms: `hook_drift` compares against this repository's own
# fragments where it has them, `installed_build_lag` carries the build's age as a
# notice, and the write side refuses instead of merging the older text.


def _the_current_fragments_as_this_projects_source(project: Path) -> Path:
    """Make ``project`` the repository this package is built from, carrying THIS build's
    fragments as its own source — the state of a checkout that has just been packed."""
    target = project / PACKAGED_ASSET_SOURCE_DIR / HOOKS_SOURCE_SUBDIR
    shutil.copytree(defaults_dir() / HOOKS_SOURCE_SUBDIR, target)
    return target


#: A command no fragment in this repository wires, so its presence in a finding can only
#: have come from the fake older build below.
RETIRED_COMMAND = "kyo retired-guard --pre-tool-use"


def _an_older_build_that_still_ships_a_retired_group(monkeypatch, tmp_path: Path) -> Path:
    """Point the running code at a copy of this build's ``defaults/`` that still wires a
    ``PreToolUse`` group this checkout has since deleted.

    A whole copy rather than a stub, because `defaults_dir` is also where the skill, the
    ledger and every `kyo init` asset come from: an older build is a build, not a
    directory with one file in it.
    """
    fake = tmp_path / "older-build" / "defaults"
    shutil.copytree(defaults_dir(), fake)
    fragment = fake / HOOKS_SOURCE_SUBDIR / "pre-tool-use.json"
    document = json.loads(fragment.read_text(encoding="utf-8"))
    document["PreToolUse"].append(
        {"matcher": "Bash", "hooks": [{"type": "command", "command": RETIRED_COMMAND}]}
    )
    fragment.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    # KYO-593: install_skill no longer reads `defaults_dir`; the skill moved into the
    # plugin's tree, which an older build of this shape would ship unchanged.
    for module in (scaffold, install_hooks, install_procedures):
        monkeypatch.setattr(module, "defaults_dir", lambda: fake)
    return fake


def test_hook_drift_had_the_reversal_and_now_reads_this_repositorys_own_fragments(
    kyo, project: Path, tmp_path: Path, monkeypatch
) -> None:
    """KYO-318.c2's "established with a test that shows which". Both halves are asserted
    here: the comparison the old code made, shown producing the finding, and the one it
    makes now, shown silent."""
    _the_current_fragments_as_this_projects_source(project)
    assert kyo("install-hooks", "--copies").code == 0
    _an_older_build_that_still_ships_a_retired_group(monkeypatch, tmp_path)

    settings = json.loads(_settings_path(project).read_text(encoding="utf-8"))
    # The comparison `hook_drift` used to make, in full: the build wires it, the project
    # does not, so every element of a "missing" finding is present.
    assert RETIRED_COMMAND in install_hooks.packaged_elements("PreToolUse")
    assert RETIRED_COMMAND not in install_hooks.installed_elements(settings, "PreToolUse")

    # ...and the comparison it makes now. Nothing here is stale except the build.
    assert install_hooks.hook_drift(project) == []


def test_the_builds_age_is_said_once_as_a_notice(
    kyo, project: Path, tmp_path: Path, monkeypatch
) -> None:
    """KYO-309's terms exactly: the condition is real and the reader may need it, and
    nobody in the session can clear it, so it is a notice naming this build's upgrade
    route (KYO-450: the re-snapshot or `uv tool upgrade`, by release) — never the
    `install-*` command, which is the one thing that would make it worse."""
    _the_current_fragments_as_this_projects_source(project)
    kyo("install-hooks", "--copies")
    _an_older_build_that_still_ships_a_retired_group(monkeypatch, tmp_path)

    lag = install_skill.installed_build_lag(project)
    assert len(lag) == 1, lag
    assert "pre-tool-use.json" in lag[0]
    assert str(PACKAGED_ASSET_SOURCE_DIR / HOOKS_SOURCE_SUBDIR) in lag[0]
    assert buildinfo.upgrade_route() in lag[0]
    assert "re-pack" not in lag[0]
    assert "Never `kyo install-hooks`" in lag[0]


def test_install_hooks_refuses_to_merge_an_older_builds_fragments(
    kyo, project: Path, tmp_path: Path, monkeypatch
) -> None:
    """KYO-318's write half for the hooks. `merge_hooks` is additive by design, so the
    older build's group would not replace anything — it would ADD back a group this
    checkout deleted, and the project would then run a hook nobody wired on purpose."""
    _the_current_fragments_as_this_projects_source(project)
    kyo("install-hooks", "--copies")
    before = _settings_path(project).read_text(encoding="utf-8")
    _an_older_build_that_still_ships_a_retired_group(monkeypatch, tmp_path)

    result = kyo("install-hooks", "--copies")
    assert result.code != 0, result
    assert _settings_path(project).read_text(encoding="utf-8") == before
    assert "Nothing was written" in result.err
    assert str(PACKAGED_ASSET_SOURCE_DIR / HOOKS_SOURCE_SUBDIR) in result.err


def test_the_reinstall_the_refusal_names_clears_it(
    kyo, project: Path, tmp_path: Path, monkeypatch
) -> None:
    """KYO-318.c1's bar, applied to this refusal: the first remedy it names is proved to
    clear it. Re-packing is a build, so what it does is modelled — the packaged
    fragments become this checkout's."""
    source = _the_current_fragments_as_this_projects_source(project)
    kyo("install-hooks", "--copies")
    fake = _an_older_build_that_still_ships_a_retired_group(monkeypatch, tmp_path)
    assert kyo("install-hooks", "--copies").code != 0

    shutil.rmtree(fake / HOOKS_SOURCE_SUBDIR)
    shutil.copytree(source, fake / HOOKS_SOURCE_SUBDIR)

    assert install_hooks.install_hooks_refusal(project) is None
    assert kyo("install-hooks", "--copies").code == 0
    assert RETIRED_COMMAND not in _settings_path(project).read_text(encoding="utf-8")


def test_the_hand_merge_the_refusal_names_clears_the_finding_it_was_asked_about(
    kyo, project: Path, tmp_path: Path, monkeypatch
) -> None:
    """The second remedy. Somebody running `kyo install-hooks` in this repository wants
    a group wired that is not wired; the refusal sends them to the source file for it,
    and doing that by hand clears the finding the command would have been run for."""
    source = _the_current_fragments_as_this_projects_source(project)
    kyo("install-hooks", "--copies")
    _an_older_build_that_still_ships_a_retired_group(monkeypatch, tmp_path)

    # A group the project is genuinely missing against its OWN source: the real finding
    # this repository can have, with the build's age on top of it.
    settings = json.loads(_settings_path(project).read_text(encoding="utf-8"))
    settings["hooks"]["PreToolUse"] = []
    _settings_path(project).write_text(json.dumps(settings, indent=2), encoding="utf-8")
    findings = install_hooks.hook_drift(project)
    assert findings, "the project is missing a group its own source wires"
    assert "do NOT run `kyo install-hooks` here" in findings[0]
    assert str(PACKAGED_ASSET_SOURCE_DIR / HOOKS_SOURCE_SUBDIR) in findings[0]

    # The hand merge the refusal names: the group out of the source file, into settings.
    from_source = json.loads((source / "pre-tool-use.json").read_text(encoding="utf-8"))
    settings["hooks"]["PreToolUse"] = [
        {**group, "hooks": [{k: v for k, v in hook.items() if k != "comment"}
                            for hook in group["hooks"]]}
        for group in from_source["PreToolUse"]
    ]
    _settings_path(project).write_text(json.dumps(settings, indent=2), encoding="utf-8")

    assert install_hooks.hook_drift(project) == []
    assert RETIRED_COMMAND not in _settings_path(project).read_text(encoding="utf-8")


def test_print_previews_and_says_a_real_run_is_refused(
    kyo, project: Path, tmp_path: Path, monkeypatch
) -> None:
    """`--print` is exempt on `install_procedures_refusal`'s precedent, but a preview
    that said nothing would show a document this command will never write."""
    _the_current_fragments_as_this_projects_source(project)
    kyo("install-hooks", "--copies")
    before = _settings_path(project).read_text(encoding="utf-8")
    _an_older_build_that_still_ships_a_retired_group(monkeypatch, tmp_path)

    result = kyo("install-hooks", "--copies", "--print", "--json")
    assert result.code == 0, result
    assert _settings_path(project).read_text(encoding="utf-8") == before
    assert any("REFUSED" in note for note in result.json["notes"]), result.json["notes"]


def test_a_source_repo_in_step_with_its_build_is_not_refused(
    kyo, project: Path
) -> None:
    """Both halves have to be true. A checkout whose fragments this build already ships
    — every run from a build made out of it, an editable install included — installs
    exactly as it always did."""
    _the_current_fragments_as_this_projects_source(project)
    assert install_hooks.hook_fragments_behind(project) == []
    assert install_hooks.install_hooks_refusal(project) is None
    assert kyo("install-hooks", "--copies").code == 0


def test_a_consumer_project_is_unreachable_from_any_of_this(
    kyo, project: Path, tmp_path: Path, monkeypatch
) -> None:
    """Structural, not incidental: a consumer carries no `src/kyosei/defaults/hooks/`,
    so `repo_hooks_source` answers None and every line above is out of reach — their
    findings, and the command that clears them, are what they always were."""
    _an_older_build_that_still_ships_a_retired_group(monkeypatch, tmp_path)
    assert scaffold.repo_hooks_source(project) is None
    assert install_hooks.install_hooks_refusal(project) is None
    assert install_hooks.hook_fragments_behind(project) == []
    assert install_skill.installed_build_lag(project) == []

    assert kyo("install-hooks", "--copies").code == 0
    settings = _settings_path(project).read_text(encoding="utf-8")
    assert RETIRED_COMMAND in settings, "a consumer gets exactly what their build ships"
    assert install_hooks.hook_drift(project) == []


# ----------------------------------------------------------------------------- KYO-361
#
# `install-hooks` used to print the change list -- the only lines saying what
# happened -- FIRST, then dump the whole rendered `settings.json` after it. A
# controller reading a long command with `| tail` (KYO-193's own habit) saw only
# the JSON and missed the `+ skill: wrote ...` line, so a truthful write read as
# nothing having happened.


def test_install_hooks_output_ends_with_the_change_list(kyo, project: Path) -> None:
    """KYO-361.c1: a run that writes a skill file (REFERENCE.md is missing on a
    fresh project, same as the incident) shows the `skill: wrote` line among the
    LAST lines of stdout -- `| tail -5` on this output must not miss it."""
    reference_name = str(SKILL_REFERENCE_ASSET.target)
    assert not (project / SKILL_REFERENCE_ASSET.target).exists()

    result = kyo("install-hooks", "--copies")
    assert result.code == 0, result
    lines = result.out.rstrip("\n").splitlines()
    tail = lines[-5:]
    assert any(reference_name in line for line in tail), lines
    assert any(line.startswith("  + skill: wrote") for line in tail), lines
    # And the change list really is the end of the output: nothing (like a JSON
    # dump) follows the last `+`/`!` line.
    assert lines[-1].startswith("  + ") or lines[-1].startswith("  ! "), lines


def test_install_hooks_output_ends_with_no_changes_line_on_a_repeat_run(
    kyo, project: Path
) -> None:
    """The no-changes case ends the same way: idempotent runs still read as
    "nothing to do" from the tail, not as a JSON dump. A `! ` note (e.g. this test's
    project is not a git repository, so the advisory pre-push hook note fires) is
    allowed to trail it -- KYO-361.c1 only forbids a JSON dump after the change
    list, and notes are part of that list."""
    assert kyo("install-hooks", "--copies").code == 0
    result = kyo("install-hooks", "--copies")
    assert result.code == 0, result
    lines = result.out.rstrip("\n").splitlines()
    assert "  (no changes: already installed)" in lines, lines
    # Nothing after the change-list-and-notes block: no blank separator, no JSON.
    assert lines[-1].startswith("  (no changes") or lines[-1].startswith("  ! "), lines
    assert "{" not in lines[-1] and "}" not in lines[-1], lines


def test_install_hooks_no_longer_prints_the_settings_json(kyo, project: Path) -> None:
    """KYO-361.c2: a real run never renders `settings.json` to stdout -- it is
    already on disk -- while `--json` still carries the full `settings` document."""
    result = kyo("install-hooks", "--copies")
    assert result.code == 0, result
    assert '"hooks"' not in result.out
    assert '"SessionStart"' not in result.out

    as_json = kyo("install-hooks", "--copies", "--json")
    assert as_json.code == 0, as_json
    assert "hooks" in as_json.json["settings"]


def test_install_hooks_print_still_renders_settings_json_but_after_the_header(
    kyo, project: Path
) -> None:
    """`--print` is the preview: it still shows the settings.json a real run would
    write, but the change list comes after it now, not before -- KYO-361 moves the
    JSON dump earlier in the output, never removes it from `--print`."""
    result = kyo("install-hooks", "--copies", "--print")
    assert result.code == 0, result
    assert '"hooks"' in result.out
    lines = result.out.rstrip("\n").splitlines()
    json_line_indexes = [i for i, line in enumerate(lines) if '"hooks"' in line]
    no_changes_line = "  (no changes: already installed)"
    change_line_indexes = [
        i
        for i, line in enumerate(lines)
        if line.startswith("  + ") or line.startswith("  ! ") or line == no_changes_line
    ]
    assert json_line_indexes and change_line_indexes
    assert max(json_line_indexes) < min(change_line_indexes), lines
