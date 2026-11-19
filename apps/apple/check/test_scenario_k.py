"""Scenario K: a new project's session in the app, from the trust prompt to a session that
changes its id and one that starts later (KYO-964, KYO-965, KYO-966).

The session is the scripted one, started with the trust dialog on its screen
(``scripted_session.py``, THE START), from the app's Start button through the test server's
Start route. The steps, each an assertion:

- K.trust: the Start panel says that Claude Code asks whether to trust the folder and offers
  the terminal; Open the terminal attaches to the orchestrator's session by its name, while
  the project records no session, and nothing is typed into the dialog until the page types
  (KYO-964.c1, c2).
- K.follows: Return typed in the app's terminal answers the dialog, and the chat follows the
  session that then begins, without a project switch, and the terminal turns to its pane
  (KYO-964.c3's mechanism; the look is the maintainer's).
- K.first: the first prompt, typed into the composer and sent, is delivered with no act in
  the terminal, in a pane of tmux's default 80x24, where its 794 characters scroll the input
  box (KYO-965.c2).
- K.resume: ``/resume`` typed in the app's terminal changes the session's id in that pane; the
  chat follows the new session, its terminal opens on it and a prompt sent goes to it
  (KYO-966.c2).
- K.later: after ``/exit`` the followed row is not live, and a session started by another
  device through the Start route is followed within seconds (KYO-966.c3).
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any

import harness
from test_session_start import FIRST_PROMPT, pane_size

import conftest

AFTER_RESUME = "Said after the resume"
SCRIPT: dict[str, Any] = {"named": {AFTER_RESUME: [{"say": "Heard after the resume."}]}}

READY = (
    "return !document.getElementById('terminal-screen').hidden"
    " && document.getElementById('terminal-note').hidden;"
)


def send_log(run: harness.Run) -> list[dict[str, Any]]:
    return [json.loads(line) for line in harness.each_line(run.kyosei_home / "send-log.jsonl")]


def terminal_rows(run: harness.Run, action: str, outcome: str) -> list[dict[str, Any]]:
    return [r for r in send_log(run) if r["action"] == action and r["outcome"] == outcome]


def type_in_terminal(page: harness.Channel, data: str) -> None:
    """What the drawer's emulator posts for keys typed into it."""
    page.eval(
        "window.webkit.messageHandlers.terminal.postMessage({action: 'input', data});", data=data
    )


def acts(run: harness.Run) -> list[str]:
    return [row["event"] for row in run.session_events() if row["event"] != "screen"]


def type_and_send(page: harness.Channel, text: str) -> None:
    page.eval(
        "const boxes = window.kyo.composer.boxes();"
        "window.kyo.composer.select(boxes.length - 1, boxes.at(-1).text.length);"
    )
    page.request("key", text=text, timeout=120)
    page.wait(
        "the typed prompt in the composer",
        "return window.kyo.composer.boxes().at(-1).text === text;",
        text=text,
        deadline=60,
    )
    page.eval("document.querySelector('#boxes .box.main .box-send').click();")


def test_scenario_k_a_new_sessions_start_its_first_prompt_and_its_id_changing(
    run: harness.Run, check_build: Path, results
) -> None:
    project = harness.make_project(run, "fresh", "NEW")
    harness.commit_all(run, project, "A new project")
    run.use_script(SCRIPT, trust_prompt=True)
    server = harness.start_server(run, project)
    results.server_version = server.version
    harness.forget_settings()
    scenario = harness.Scenario("K", "A new session in the app, and its id changing", conftest.OUT)
    results.scenarios.append(scenario)
    app = harness.start_app(run, server, project=server.name)
    scenario.app = app
    page = app.channel
    try:
        page.request("window", focus=True)
        page.wait(
            "the Start panel",
            "const panel = document.getElementById('start');"
            "return typeof window.kyo === 'object' && panel && !panel.hidden;",
        )
        page.eval("document.getElementById('start-button').click();")
        said = page.wait(
            "the panel to say the session waits on the trust prompt",
            "const offered = !document.getElementById('start-terminal').hidden;"
            "const text = document.getElementById('start-text').textContent;"
            "return offered && text.includes('asking whether to trust') ? text : '';",
            deadline=30,
        )
        listed_before = server.get("sessions")["sessions"]
        page.eval("document.getElementById('start-terminal').click();")
        page.wait("the terminal to show the orchestrator's pane", READY, deadline=20)
        opened = terminal_rows(run, "orchestrator-terminal", "opened")
        time.sleep(1.0)
        before_typing = acts(run)
        scenario.check(
            "K.trust",
            "While the started session waits on the trust prompt, the project records no "
            "session, the panel offers the terminal, and Open the terminal attaches to the "
            "orchestrator's session by its name; opening it types nothing into the dialog.",
            listed_before == []
            and len(opened) == 1
            and before_typing == ["trust-prompt"]
            and "Open the terminal" in said,
            {"panel": said, "sessions": listed_before, "rows": opened, "acts": before_typing},
            look="trust-terminal",
        )

        type_in_terminal(page, "\r")
        began = time.monotonic()
        page.wait(
            "the chat to follow the session that began",
            "return document.getElementById('start').hidden"
            " && !document.getElementById('empty-chat').hidden"
            " && window.kyo.composer.boxes().length > 0;",
            deadline=30,
        )
        followed_in = round(time.monotonic() - began, 2)
        first = harness.poll("the listed session", server.live_session)["session_id"]
        harness.poll(
            "the terminal to open on the session's pane",
            lambda: [
                r for r in terminal_rows(run, "terminal", "opened") if r["session_id"] == first
            ],
            deadline=20,
        )
        scenario.check(
            "K.follows",
            "Return typed in the app's terminal answers the dialog; the chat follows the "
            "session that begins, and the terminal turns to that session's pane, with no "
            "project switch.",
            acts(run)[:3] == ["trust-prompt", "trusted", "started"]
            and run.session_events("started")[0]["session_id"] == first,
            {"seconds_from_answer_to_chat": followed_in, "session": first},
            look="followed",
        )

        # KYO-965.c2: with the drawer closed nothing sizes the pane, which is put at tmux's
        # default, the size of the session the maintainer's first prompt went to.
        page.eval("document.getElementById('terminal-toggle').click();")
        run.tmux("resize-window", "-t", "=NEW:", "-x", "80", "-y", "24")
        harness.poll("the pane at 80x24", lambda: pane_size(run, "NEW") == "80x24")
        type_and_send(page, FIRST_PROMPT)
        pressed = time.time()
        taken = harness.poll(
            "the session to take the first prompt",
            lambda: run.session_events("prompt"),
            deadline=60,
        )
        page.wait(
            "the reply in the chat",
            "return document.getElementById('chat').textContent.includes('Scripted reply 1.');",
            deadline=30,
        )
        rows = [
            (r["action"], r["outcome"], r["reason"], r["session_id"] == first)
            for r in send_log(run)
            if r["action"] in ("hold", "held-delivery")
        ]
        held = page.eval("return document.getElementById('held').hidden;")
        scenario.check(
            "K.first",
            "The first prompt sent from the app to the session it started is delivered once "
            "the session is idle, with no act in the terminal, though at 80x24 it scrolls the "
            "input box; the send log holds its hold and its delivery and no failure.",
            taken[0]["text"] == FIRST_PROMPT
            and rows == [("hold", "held", None, True), ("held-delivery", "sent", None, True)]
            and held is True,
            {
                "seconds_from_send_to_prompt": round(taken[0]["t"] - pressed, 2),
                "rows": rows,
                "pane": pane_size(run, "NEW"),
            },
            look="first-prompt",
        )

        # KYO-966.c2: /resume in the session's own terminal, the app's drawer.
        # The drawer's elements stand as they were left, so the reopened stream is waited
        # for by its log row, and the app's connection by a moment after it.
        opened_before = len(terminal_rows(run, "terminal", "opened"))
        page.eval("document.getElementById('terminal-toggle').click();")
        harness.poll(
            "the terminal to open on the session again",
            lambda: len(terminal_rows(run, "terminal", "opened")) > opened_before,
            deadline=20,
        )
        page.wait("the terminal on the session", READY, deadline=20)
        time.sleep(1.0)
        type_in_terminal(page, "/resume\r")
        resumed = harness.poll("the session to resume", lambda: run.session_events("resumed"),
                               deadline=20)  # fmt: skip
        second = resumed[0]["session_id"]
        began = time.monotonic()
        harness.poll(
            "the app's terminal to open on the resumed session",
            lambda: [
                r for r in terminal_rows(run, "terminal", "opened") if r["session_id"] == second
            ],
            deadline=30,
        )
        moved_in = round(time.monotonic() - began, 2)
        page.wait(
            "the chat to show the resumed session",
            "return !document.getElementById('empty-chat').hidden;",
            deadline=20,
        )
        type_and_send(page, AFTER_RESUME)
        page.wait(
            "the reply in the resumed session",
            "return document.getElementById('chat').textContent.includes(reply);",
            reply="Heard after the resume.",
            deadline=30,
        )
        holds = [r["session_id"] for r in send_log(run) if r["action"] == "hold"]
        scenario.check(
            "K.resume",
            "After /resume in the app's terminal changes the session's id in that pane, the "
            "chat follows the new session and its terminal opens on it, with no project "
            "switch, and a prompt sent then goes to the new session.",
            second != first and holds[-1] == second,
            {"first": first, "second": second, "terminal_reopened_in": moved_in, "holds": holds},
            look="resumed",
        )

        # KYO-966.c3: the followed session ends, and another starts without the app.
        type_in_terminal(page, "/exit\r")
        harness.poll(
            "the server to stop listing the session as live",
            lambda: server.live_session() is None,
            deadline=30,
        )
        page.wait(
            "the Start panel for a project with no live session",
            "return !document.getElementById('start').hidden;",
            deadline=20,
        )
        run.use_script(SCRIPT)
        status, started = server.ask("POST", "orchestrator/start", {})
        assert status == 200, started
        began = time.monotonic()
        third = harness.poll("the server to list the new session", server.live_session,
                             deadline=30)["session_id"]  # fmt: skip
        page.wait(
            "the chat to follow the session started elsewhere",
            "return document.getElementById('start').hidden"
            " && !document.getElementById('empty-chat').hidden;",
            deadline=30,
        )
        later_in = round(time.monotonic() - began, 2)
        scenario.check(
            "K.later",
            "With the followed session ended, a session started by another device through "
            "the Start route is followed within seconds, with no project switch.",
            third not in (first, second) and later_in < 15,
            {"third": third, "seconds_to_follow": later_in},
            look="later",
        )
        scenario.finish()
    except Exception as failure:
        scenario.error = f"{type(failure).__name__}: {failure}"
        with contextlib.suppress(harness.CheckError):
            scenario.snapshot("at-failure")
        for name, source in (("serve.log", server.log), ("session.jsonl", run.session_log),
                             ("app.log", app.log)):  # fmt: skip
            if source.exists():
                (conftest.OUT / f"K-{name}").write_text(source.read_text("utf-8"), "utf-8")
        (conftest.OUT / "K-send-log.jsonl").write_text(
            "\n".join(json.dumps(row) for row in send_log(run)), encoding="utf-8"
        )
        raise
    finally:
        if scenario.seconds is None:
            scenario.finish()
        results.measurements["scenario_K_seconds"] = scenario.seconds
        app.quit()
        harness.forget_settings()
    failed = [f"{a.id}: {a.text} ({a.detail})" for a in scenario.failed()]
    assert not failed, "\n".join(failed)
    assert harness.calls_reaching_another_socket(run.tmux_log, run.tmux_socket) == []
    assert harness.leftover_check_builds() == []
