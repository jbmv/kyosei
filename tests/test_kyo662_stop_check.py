"""The Stop-hook discussion check after its retirement (KYO-899, decision KYO-898), and
the unindexed-session problem KYO-662 added to doctor, which stays.

KYO-662 and KYO-876 ran `kyo discuss check --hook` at Stop and at UserPromptSubmit and let
the agent dismiss a turn with `kyo discuss skip`. The check is retired: the packaged hooks
no longer run it, and both verbs stay in the parser, write nothing and exit 0, so a
project's older hook copy keeps working.

The transcripts written here reuse the record shapes of ``tests/test_kyo658_discussions.py``;
the words are invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kyosei import cmd_discuss, cmd_sessions, cmd_turns, doctor, install_hooks
from kyosei.harness import claude_code_transcript as transcript

from .conftest import invoke, opt_in_transcripts
from .test_kyo658_discussions import (
    DECIDING_WORDS,
    PROMPT_1,
    PROMPT_2,
    PROMPT_3,
    SESSION,
    _decision_flags,
    agent_text,
    index_line,
    notification,
    person,
    prompt_hook,
    transcript_path,
    write_transcript,
)


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    return SESSION


@pytest.fixture
def indexed(kyo, project: Path, session: str) -> Path:
    """An opted-in project whose index holds three prompts of one session, as in
    ``tests/test_kyo658_discussions.py``: the person's question and the agent's
    recommendation, a task notification, and the person's deciding words."""
    opt_in_transcripts(project)
    write_transcript(
        [
            person("p-1", PROMPT_1, "what if the cache stays?", "2026-11-06T10:00:00.000Z"),
            agent_text("a-1", "I recommend keeping it.", "2026-11-06T10:00:30.000Z"),
            notification("n-2", PROMPT_2, "2026-11-06T10:00:40.000Z"),
            agent_text("a-2", "The run finished.", "2026-11-06T10:00:50.000Z"),
            person("p-3", PROMPT_3, DECIDING_WORDS, "2026-11-06T10:01:00.000Z"),
        ]
    )
    for prompt_id in (PROMPT_1, PROMPT_2, PROMPT_3):
        prompt_hook(kyo, prompt_id)
    return project


def stop(kyo, prompt_id: str | None, reply: str, *, json_flag: bool = False, **extra):
    payload = {
        "session_id": SESSION,
        "hook_event_name": "Stop",
        "transcript_path": str(transcript_path()),
        "last_assistant_message": reply,
        "stop_hook_active": False,
        **extra,
    }
    if prompt_id is not None:
        payload["prompt_id"] = prompt_id
    args = ("--json",) if json_flag else ()
    return kyo(*args, "discuss", "check", "--hook", stdin=json.dumps(payload))


def prompt(kyo, prompt_id: str = "99999999-9999-4999-8999-999999999999", **extra):
    """The session's next UserPromptSubmit, through `kyo discuss check --hook`."""
    payload = {
        "session_id": SESSION,
        "hook_event_name": "UserPromptSubmit",
        "prompt_id": prompt_id,
        "prompt": "the next words",
        **extra,
    }
    return kyo("discuss", "check", "--hook", stdin=json.dumps(payload))


def silent(run) -> bool:
    return run.code == 0 and run.out == "" and run.err == ""


# ------------------------------------------------------------------ KYO-899.c2: the verbs


def _snapshot(root: Path) -> dict[str, bytes]:
    """Every file under the store directory, by path, with its bytes."""
    store = root / "kyosei"
    return {
        str(path.relative_to(store)): path.read_bytes()
        for path in sorted(store.rglob("*"))
        if path.is_file()
    }


def test_the_check_at_stop_prints_nothing_and_writes_nothing(kyo, indexed: Path) -> None:
    """The turn that made the check mark a candidate before KYO-899: the person's question
    and a recommendation in reply, with no discussion open."""
    before = _snapshot(indexed)
    assert silent(stop(kyo, PROMPT_1, "I recommend keeping it."))
    assert _snapshot(indexed) == before
    assert "candidate" not in index_line(indexed, PROMPT_1)
    told = stop(kyo, PROMPT_1, "I recommend keeping it.", json_flag=True).json
    assert told["ok"] is True and told["fire"] is False and told["retired"] is True


def test_the_check_at_the_next_prompt_prints_nothing_and_writes_nothing(
    kyo, indexed: Path
) -> None:
    """An index line an earlier build marked a candidate is named by nothing now."""
    lines = cmd_turns.read_lines(indexed)
    for line in lines:
        if line.get("prompt_id") == PROMPT_1:
            line["candidate"] = {"cues": ["a cue"], "at": "2026-11-06T10:00:31.000Z", "asked": None}
    cmd_turns._rewrite(indexed, lines)
    before = _snapshot(indexed)
    assert silent(prompt(kyo))
    assert _snapshot(indexed) == before
    assert index_line(indexed, PROMPT_1)["candidate"]["asked"] is None


@pytest.mark.parametrize(
    "stdin",
    ["", "not json", "[]", json.dumps({"hook_event_name": "Stop", "session_id": SESSION})],
)
def test_the_check_is_silent_and_exits_zero_on_any_payload(kyo, indexed: Path, stdin: str) -> None:
    assert silent(kyo("discuss", "check", "--hook", stdin=stdin))
    assert silent(kyo("discuss", "check", stdin=stdin))


def test_the_check_is_silent_where_no_project_resolves(tmp_path: Path) -> None:
    """A plugin's hooks fire in every session, Kyosei project or not (KYO-591)."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    run = invoke(["--root", str(elsewhere), "discuss", "check", "--hook"], stdin="{}")
    assert silent(run), run


def test_skip_exits_zero_writes_nothing_and_says_the_check_is_retired(kyo, indexed: Path) -> None:
    before = _snapshot(indexed)
    for flags in ((), ("--why", "a report"), ("--at-prompt", PROMPT_1, "--why", "a report")):
        run = kyo("discuss", "skip", *flags)
        assert run.code == 0 and run.err == "", run
        assert run.out.splitlines() == [cmd_discuss.SKIP_RETIRED]
    assert "retired" in cmd_discuss.SKIP_RETIRED
    assert _snapshot(indexed) == before
    assert "skip" not in index_line(indexed, PROMPT_1)


def test_hook_on_another_action_is_refused_naming_check(kyo, project: Path) -> None:
    misplaced = kyo("discuss", "open", "--title", "x", "--hook")
    assert misplaced.code == 64 and "--hook belongs to `kyo discuss check`" in misplaced.err
    assert kyo("discuss", "check", "--hook", stdin="").code == 0
    assert kyo("discuss", "open", "--title", "x").code == 0


def test_skip_says_the_same_in_a_project_with_no_index(kyo, project: Path, session: str) -> None:
    run = kyo("discuss", "skip", "--why", "a report")
    assert run.code == 0 and run.out.splitlines() == [cmd_discuss.SKIP_RETIRED]


# ------------------------------------------------------------------ KYO-899.c1: the hooks


def _packaged_commands() -> list[str]:
    hooks = Path(install_hooks.__file__).parent
    fragments = (hooks / "defaults" / "hooks").glob("*.json")
    texts = [path.read_text(encoding="utf-8") for path in fragments]
    texts.append(
        (hooks / "harness" / "claude-code" / "plugin" / "hooks" / "hooks.json").read_text(
            encoding="utf-8"
        )
    )
    commands: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("command"), str):
                commands.append(node["command"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for text in texts:
        walk(json.loads(text))
    return commands


def test_no_packaged_hook_runs_the_discussion_check() -> None:
    """The plugin's hooks.json and every template under defaults/hooks: the group KYO-662
    and KYO-876 added must not return."""
    commands = _packaged_commands()
    assert any("turns --hook" in command for command in commands)  # the walk reads them
    assert [command for command in commands if "discuss" in command] == []
    under_stop = install_hooks._load_hook_fragment("stop.json", event="Stop")
    under_prompt = install_hooks._load_hook_fragment("prompt-submit.json")
    assert [c["command"].rsplit("; ", 1)[-1] for g in under_prompt for c in g["hooks"]] == [
        "kyo check-build --hook",
        "kyo turns --hook",
    ]
    assert [c["command"].rsplit("; ", 1)[-1] for g in under_stop for c in g["hooks"]] == [
        "kyo sweep --hook && kyo doctor --hook",
        "kyo turns --hook",
    ]


# ------------------------------------------------------------------ KYO-899.c3: installed copies

RETIRED = "kyo discuss check --hook"


def _with_the_retired_line(kyo, project: Path) -> Path:
    """A project whose installed hooks are today's plus the line an earlier build added
    under Stop and UserPromptSubmit."""
    assert kyo("install-hooks", "--copies").code == 0
    path = project / ".claude" / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    for event in ("Stop", "UserPromptSubmit"):
        settings["hooks"][event].append(
            {"hooks": [{"type": "command", "command": install_hooks.guarded(RETIRED)}]}
        )
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return path


def _hook_findings(kyo) -> tuple[list[str], list[str]]:
    result = kyo("doctor", "--json").json
    return (
        [found for found in result["problems"] if "hook_config" in found],
        [found for found in result["notices"] if "hook_config" in found],
    )


def test_a_setup_without_the_check_lacks_no_hook(kyo, project: Path) -> None:
    assert kyo("install-hooks", "--copies").code == 0
    assert _hook_findings(kyo) == ([], [])
    assert RETIRED not in install_hooks.SUPERSEDED_HOOK_COMMANDS
    assert RETIRED not in install_hooks.SUPERSEDED_HOOK_COMMANDS.values()


def test_a_copy_that_still_runs_the_check_is_a_notice_and_install_hooks_removes_it(
    kyo, project: Path
) -> None:
    """Handled as a superseded line is: doctor names it in a notice, not a problem, and
    `kyo install-hooks` removes it by its exact action and adds nothing in its place."""
    path = _with_the_retired_line(kyo, project)
    problems, notices = _hook_findings(kyo)
    assert problems == []
    assert sum(RETIRED in notice and "retired" in notice for notice in notices) >= 1
    assert all("kyo install-hooks" in notice for notice in notices)
    assert "hook_config_superseded" in doctor.NOTICE_CHECKS
    done = kyo("install-hooks", "--copies")
    assert done.code == 0 and "removed retired hook" in done.out
    assert RETIRED not in path.read_text(encoding="utf-8")
    assert _hook_findings(kyo) == ([], [])
    again = path.read_text(encoding="utf-8")
    assert kyo("install-hooks", "--copies").code == 0
    assert path.read_text(encoding="utf-8") == again


def test_a_line_the_project_extended_is_left_alone(kyo, project: Path) -> None:
    path = _with_the_retired_line(kyo, project)
    settings = json.loads(path.read_text(encoding="utf-8"))
    own = f"{RETIRED} && echo mine"
    settings["hooks"]["Stop"][-1]["hooks"][0]["command"] = own
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    assert kyo("install-hooks", "--copies").code == 0
    assert own in path.read_text(encoding="utf-8")


def test_the_removed_parts_stay_removed() -> None:
    """The cue detection, the candidate marks and the turn view that only served the check."""
    for module, names in (
        (cmd_discuss, ("detect", "person_cue", "reply_cue", "check_turn", "next_prompt")),
        (cmd_turns, ("mark_candidate", "next_candidate", "mark_skipped")),
        (transcript, ("read_turn", "turn_view", "split_turns")),
    ):
        for name in names:
            assert not hasattr(module, name), f"{module.__name__}.{name}"
    assert "discussion_shares" not in doctor.CHECKS


# ------------------------------------------------------------------ KYO-899.c6, c7: doctor, brief


def test_doctor_prints_no_weekly_shares_and_proposes_no_discussion_key(
    kyo, indexed: Path, tmp_path: Path
) -> None:
    """A store that before KYO-899 got both lines: a decision filed from a discussion, an
    index line carrying a skip, and a `kinds.yml` without `requires_discussion`."""
    opened = kyo("discuss", "open", "--title", "x", "--at-prompt", PROMPT_1, "--json").json["id"]
    decided = kyo(
        "spawn", opened, "--kind", "decision", "--title", "Keep the cache",
        *_decision_flags(indexed, tmp_path),
    )  # fmt: skip
    assert decided.code == 0, decided
    lines = cmd_turns.read_lines(indexed)
    lines[0]["skip"] = {"why": "a report", "at": "2026-11-06T10:00:31.000Z"}
    cmd_turns._rewrite(indexed, lines)
    kinds = (indexed / "kyosei" / "kinds.yml").read_text(encoding="utf-8")
    assert "requires_discussion: true" not in kinds
    result = kyo("doctor", "--json").json
    said = "\n".join([*result["problems"], *result["notices"]])
    assert "requires_discussion" not in said
    assert "week " not in said and "Stop-check" not in said and "opened live" not in said
    text = kyo("doctor")
    assert "requires_discussion" not in text.out + text.err


def test_the_brief_still_lists_a_discussion_left_open(kyo, indexed: Path) -> None:
    opened = kyo("discuss", "open", "--title", "Which store", "--json").json["id"]
    brief = kyo("brief", "--json").json
    assert [d["id"] for d in brief["left_open_discussions"]] == [opened]
    assert "discussions left open (1):" in kyo("brief").out


# ------------------------------------------------------------------ KYO-662.c3: doctor


def _findings(kyo, name: str) -> tuple[list[str], list[str]]:
    result = kyo("doctor", "--json").json
    return (
        [found for found in result["problems"] if name in found],
        [found for found in result["notices"] if name in found],
    )


def _stops(project: Path, session_id: str, count: int) -> None:
    for _ in range(count):
        cmd_sessions.record_event(
            project, session_id=session_id, event="Stop", cwd=str(project), transcript_path=None
        )


def test_a_session_with_stops_and_no_index_lines_is_a_problem(
    kyo, project: Path, session: str
) -> None:
    opt_in_transcripts(project)
    _stops(project, SESSION, 1)
    assert _findings(kyo, "sessions_unindexed") == ([], [])  # one Stop can precede the turn hook
    _stops(project, SESSION, 1)
    (problem,), notices = _findings(kyo, "sessions_unindexed")
    assert notices == [] and SESSION in problem and "2 stops" in problem
    assert "kyo install-hooks" in problem and "start a new session" in problem
    assert kyo("doctor").code != 0


def test_a_later_indexed_prompt_clears_the_problem(kyo, project: Path, session: str) -> None:
    """The remedy the problem names: with the hooks running, a new session's prompt is
    indexed, and the earlier session is no longer reported."""
    opt_in_transcripts(project)
    _stops(project, SESSION, 2)
    assert _findings(kyo, "sessions_unindexed")[0]
    later = {
        "session_id": "9d9d9d9d-2222-4a2b-9c3d-0123456789ab",
        "hook_event_name": "UserPromptSubmit",
        "prompt_id": PROMPT_2,
        "prompt": "words",
    }
    assert kyo("turns", "--hook", stdin=json.dumps(later)).code == 0
    assert _findings(kyo, "sessions_unindexed") == ([], [])


def test_a_session_with_index_lines_is_not_a_problem(kyo, indexed: Path) -> None:
    _stops(indexed, SESSION, 3)
    assert _findings(kyo, "sessions_unindexed") == ([], [])


def test_a_project_that_keeps_no_index_has_no_such_problem(kyo, project: Path) -> None:
    _stops(project, SESSION, 3)
    assert _findings(kyo, "sessions_unindexed") == ([], [])


def test_a_session_entry_counts_its_stops_and_keeps_the_last_stop_time() -> None:
    lookup = cmd_sessions.find_bridge_session(None)
    entries: list[dict] = []
    for event, stamp in (
        ("SessionStart", "2026-11-06T10:00:00.000Z"),
        ("Stop", "2026-11-06T10:01:00.000Z"),
        ("Stop", "2026-11-06T10:02:00.000Z"),
        ("SessionEnd", "2026-11-06T10:03:00.000Z"),
    ):
        entries, entry = cmd_sessions.apply_event(
            entries, session_id=SESSION, event=event, cwd=None, lookup=lookup, stamp=stamp
        )
    assert entry["stops"] == 2 and entry["stopped_at"] == "2026-11-06T10:02:00.000Z"


