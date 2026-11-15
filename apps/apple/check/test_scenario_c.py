"""Scenario C of proposal KYO-942: quitting, in the check build (KYO-948; the hand check of
KYO-782).

Two tests. In the first, text is typed through ``key`` requests and ``quit`` is the very next
request, so that the app is quit before the page's pause before a save has passed; the app
started again shows every character. In the second the test server is suspended with SIGSTOP,
text is typed and the app is quit; the server is resumed, and the app started again restores
the text the server never acknowledged, with the line that says so.

The suspended server is the process the harness started, by the pid it recorded, and it is
resumed in a ``finally`` so that a failed test cannot leave it stopped. The check build is
started with no ``KYOSEI_PROJECT``, so the project it shows is the one it saved in its own
defaults, which are cleared at the end.
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path
from typing import Any

import harness
from lifecycle import Window, keep_failure, resume_server, seed_project, stop_server

import conftest

#: Words with capitals, digits, punctuation and a hyphen (the keys of a US keyboard), long
#: enough to be typed over several page events.
TYPED = "Whole, then: 42 words (all-in) and a long line, to the end."
RESTORED = "Restored what had not been saved when the app last quit."
ASK = "Describe the tides"
SCRIPT: dict[str, Any] = {"named": {ASK: [{"say": "The sea rises and falls twice a day."}]}}


def _begin(run: harness.Run, results, scenario_name: str, title: str):
    project = seed_project(run, "delta", "DLT", line="Delta reef")
    run.use_script(SCRIPT)
    server = harness.start_server(run, project)
    results.server_version = server.version
    session = server.start_session()["row"]["session_id"]
    harness.forget_settings()
    harness.save_project("delta")
    scenario = harness.Scenario(scenario_name, title, conftest.OUT)
    results.scenarios.append(scenario)
    return server, session, scenario


def _forget_page_storage(run: harness.Run) -> None:
    """Remove what the page kept in the run's own substitute home (WebKit's storage, which
    `CFFIXED_USER_HOME` moved there), so that the next start has only what the app and the
    server kept. The page keeps each project's prompts there as well, and without this a
    restored prompt could come from either, and a check could not tell which."""
    home = run.directory / "app-home"
    for entry in home.iterdir():
        shutil.rmtree(entry) if entry.is_dir() else entry.unlink()


def _open(run: harness.Run, server: harness.Server, scenario: harness.Scenario) -> Window:
    app = harness.start_app(run, server)
    scenario.app = app
    window = Window(app)
    window.loaded()
    window.make_key()
    window.page.wait(
        "the chat to follow the live session",
        "return document.getElementById('start').hidden"
        " && !document.body.classList.contains('ended')"
        " && window.kyo.composer.boxes().length > 0;",
        deadline=30,
    )
    return window


def _finish(scenario: harness.Scenario, run: harness.Run, window: Window, results, key: str):
    scenario.finish()
    results.measurements[f"scenario_{key}_seconds"] = scenario.seconds
    results.measurements[f"scenario_{key}_quit_status"] = window.app.quit()
    harness.forget_settings()
    failed = [f"{a.id}: {a.text} ({a.detail})" for a in scenario.failed()]
    assert not failed, "\n".join(failed)
    assert harness.calls_reaching_another_socket(run.tmux_log, run.tmux_socket) == []
    assert harness.leftover_check_builds() == []


def test_scenario_c_the_fast_quit(run: harness.Run, check_build: Path, results) -> None:
    server, session, scenario = _begin(run, results, "C", "Quitting: typed text and a fast quit")
    window = None
    try:
        window = _open(run, server, scenario)
        window.page.eval(
            "window.kyo.composer.select(0, window.kyo.composer.boxes()[0].text.length);"
        )
        # The keys are taken by the page after the request returns, so the text is waited for
        # in the box (a key not yet taken is no typed text, and measured: a quit sent before
        # the page had taken the first key found one character of fifty-nine). The quit is
        # then the very next request, which is sent before the page's pause before a save.
        window.page.request("key", text=TYPED)
        window.wait_prompt(TYPED, "before the quit")
        status = window.app.quit(at_once=True)
        results.measurements["scenario_C_fast_quit_status"] = status
        _forget_page_storage(run)
        window = _open(run, server, scenario)
        # What the app shows is the assertion's to judge, so a wait that runs out is not.
        with contextlib.suppress(harness.CheckError):
            window.wait_prompt(TYPED, "after the restart", deadline=10)
        held = [box["text"] for box in server.get(f"sessions/{session}/draft")["boxes"]]
        scenario.check(
            "C.fast-quit",
            "Text typed through `key` requests, once it is in the box, and followed at once by "
            "`quit` is whole when the app starts again, and the server holds it as the session's "
            "draft. The page's own storage was removed between the two runs, so the text came "
            "back from the server.",
            window.prompt() == TYPED and status == 0 and held == [TYPED],
            {"composer": window.prompt(), "server_draft": held, "quit_status": status},
            look="after-the-fast-quit",
        )
    except Exception as failure:
        scenario.error = f"{type(failure).__name__}: {failure}"
        keep_failure("C", window, run, server)
        raise
    finally:
        if window is not None:
            _finish(scenario, run, window, results, "C")


def test_scenario_c_the_unreachable_server(run: harness.Run, check_build: Path, results) -> None:
    server, session, scenario = _begin(
        run, results, "C2", "Quitting: the server does not answer"
    )
    window = None
    try:
        window = _open(run, server, scenario)
        window.page.eval(
            "window.kyo.composer.select(0, window.kyo.composer.boxes()[0].text.length);"
        )
        stop_server(server)
        window.page.request("key", text=TYPED)
        window.wait_prompt(TYPED, "before the quit")
        window.app.quit()
        _forget_page_storage(run)
        resume_server(server)
        server_before = [box["text"] for box in server.get(f"sessions/{session}/draft")["boxes"]]
        window = _open(run, server, scenario)
        with contextlib.suppress(harness.CheckError):
            window.wait_prompt(TYPED, "restored after the restart", deadline=10)
        with contextlib.suppress(harness.CheckError):
            window.page.wait(
                "the line that says what was restored",
                "return document.getElementById('composer-note').textContent === said;",
                said=RESTORED,
                deadline=5,
            )
        held = False
        with contextlib.suppress(harness.CheckError):
            held = harness.poll(
                "the server to hold the restored text",
                lambda: [b["text"] for b in server.get(f"sessions/{session}/draft")["boxes"]]
                == [TYPED]
                or None,
                deadline=10,
            )
        scenario.check(
            "C.unreachable",
            "With the test server suspended, text typed and the app quit is restored on the "
            "next start once the server answers, with the line that says so, and is then saved "
            "to the server. The page's own storage was removed between the two runs, so the text "
            "came back from what the app kept. The server held none of it while it was suspended.",
            window.prompt() == TYPED
            and window.note() == RESTORED
            and server_before == []
            and held is True,
            {
                "composer": window.prompt(),
                "note": window.note(),
                "server_draft_before_the_restart": server_before,
            },
            look="restored-line",
        )
    except Exception as failure:
        scenario.error = f"{type(failure).__name__}: {failure}"
        resume_server(server)
        keep_failure("C2", window, run, server)
        raise
    finally:
        resume_server(server)
        if window is not None:
            _finish(scenario, run, window, results, "C2")
