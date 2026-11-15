"""Scenario J of proposal KYO-950: the Config view, requesting (KYO-959).

A project served with the scripted session live. Ask the session… on a request row places the
row's request header and an empty line in the composer of the session the window follows, in the
prompt box while it is empty and otherwise in a new scratchpad. The control sends nothing, so the
scripted session receives no prompt, and the row reads In the composer while a box holds the
header.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import harness
from lifecycle import Window, keep_failure, seed_project

import conftest

SCRIPT: dict[str, Any] = {"named": {}}

ASK = (
    "const row = document.querySelector(`#config-page .config-row[data-name='${name}']`);"
    "const button = row?.querySelector('.config-ask button');"
    "if (!button) return false; button.click(); return true;"
)

MARK = (
    "const row = document.querySelector(`#config-page .config-row[data-name='${name}']`);"
    "return {mark: row && !row.querySelector('.config-mark').hidden,"
    " text: row?.querySelector('.config-mark').textContent,"
    " ask: [...row.querySelectorAll('.config-ask button')].length};"
)


def _show_row(window: Window, name: str) -> None:
    for key in window.page.eval(
        "return [...document.querySelectorAll('#config-side .config-section-button')]"
        ".map((b) => b.dataset.section).filter((k) => k.startsWith('project:'));"
    ):
        window.page.eval(
            "document.querySelector(`#config-side [data-section='${key}']`).click();", key=key
        )
        if window.page.eval(
            "return !!document.querySelector(`#config-page .config-row[data-name='${name}']`);",
            name=name,
        ):
            return
    raise harness.CheckError(f"no section of the Config view holds the row {name}")


def test_scenario_j_requesting(run: harness.Run, check_build: Path, results) -> None:
    project = seed_project(run, "moor", "MOR", line="Moor reef")
    run.use_script(SCRIPT)
    server = harness.start_server(run, project)
    results.server_version = server.version
    session = server.start_session()["row"]["session_id"]
    harness.forget_settings()
    harness.save_project("moor")
    scenario = harness.Scenario("J", "The Config view, requesting", conftest.OUT)
    results.scenarios.append(scenario)
    app = harness.start_app(run, server)
    scenario.app = app
    window = Window(app)
    try:
        window.loaded()
        window.make_key()
        window.page.wait(
            "the chat to follow the live session",
            "return document.getElementById('start').hidden"
            " && !document.body.classList.contains('ended')"
            " && window.kyo.composer.boxes().length > 0;",
            deadline=30,
        )
        document = server.get("config")
        requests = {
            s["key"]: s["request"]["header"] for s in document["settings"] if s.get("request")
        }
        first, second = "worktree_setup", "trunk"
        window.view("config")
        window.page.wait(
            "the Config view to draw the project",
            "return document.querySelectorAll('#config-side .config-section-button').length > 0;",
            deadline=30,
        )
        # J.1 the prompt box is empty: the request is placed in it.
        _show_row(window, first)
        assert window.page.eval(ASK, name=first)
        window.page.wait(
            "the request in the prompt box",
            "return window.kyo.composer.boxes().at(-1).text === text;",
            text=requests[first] + "\n\n",
        )
        boxes = window.page.eval("return window.kyo.composer.boxes();")
        mark = window.page.eval(MARK, name=first)
        scenario.check(
            "J.prompt",
            "Ask the session… on a request row, with the prompt box empty, places the row's "
            "request header and an empty line in the prompt box, and the row reads In the "
            "composer.",
            len(boxes) == 1
            and boxes[0]["kind"] == "prompt"
            and boxes[0]["text"] == requests[first] + "\n\n"
            and mark["mark"] is True
            and mark["text"] == "In the composer",
            {"boxes": boxes, "mark": mark},
            look="request-in-prompt",
        )
        # J.2 the prompt now holds words: the next request opens a scratchpad.
        _show_row(window, second)
        assert window.page.eval(ASK, name=second)
        window.page.wait(
            "the second request in a scratchpad",
            "return window.kyo.composer.boxes().some((b) => b.kind === 'scratch' && b.text === text);",
            text=requests[second] + "\n\n",
        )
        boxes = window.page.eval("return window.kyo.composer.boxes();")
        prompt = [b for b in boxes if b["kind"] == "prompt"]
        scenario.check(
            "J.scratchpad",
            "With words in the prompt box, the next request is placed in a new scratchpad and "
            "the prompt box keeps its words.",
            len(prompt) == 1
            and prompt[0]["text"] == requests[first] + "\n\n"
            and any(b["kind"] == "scratch" and b["text"] == requests[second] + "\n\n" for b in boxes),
            boxes,
            look="request-in-scratchpad",
        )
        # J.3 nothing was sent, and the control exists for no row without a request header.
        sent = [e for e in run.session_events() if "kyo-config-request" in json.dumps(e)]
        asked = window.page.eval(
            "return [...document.querySelectorAll('#config-page .config-row')]"
            ".filter((row) => row.querySelector('.config-ask')).map((row) => row.dataset.name);"
        )
        scenario.check(
            "J.sends-nothing",
            "The control sends nothing: the scripted session received no request, and the "
            "session id of the window is unchanged.",
            sent == [] and bool(session),
            {"received": sent, "rows_with_the_control": asked},
        )
    except Exception as failure:
        scenario.error = f"{type(failure).__name__}: {failure}"
        keep_failure("J", window, run, server)
        raise
    finally:
        scenario.finish()
        results.measurements["scenario_J_seconds"] = scenario.seconds
        results.measurements["scenario_J_quit_status"] = app.quit()
        harness.forget_settings()
        failed = [f"{a.id}: {a.text} ({a.detail})" for a in scenario.failed()]
        assert not failed, "\n".join(failed)
        assert harness.leftover_check_builds() == []
