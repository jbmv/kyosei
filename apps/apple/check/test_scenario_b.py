"""Scenario B of proposal KYO-942: projects and drafts, in the check build (KYO-948).

Two tests, for the two halves of the hand checks of KYO-761 and KYO-763.

``test_scenario_b_switching`` is KYO-761. One test server serves three projects, one with a live
session (the scripted one, KYO-946). The app starts with no saved project and so shows the list
of projects; a prompt is left unsent in each project, the window is switched among them with the
dropdown and with a live-session link, the app is quit and started again, and a project the
server no longer lists is saved as the last one shown.

``test_scenario_b_the_projects_draft`` is KYO-763. With no live session, the runner saves a
tagged prompt to the project's draft, as another device would; the app shows it; a session is
started and has it in its composer once; a session ended with an unsent prompt leaves it for
the next session.

The check build is started without ``KYOSEI_PROJECT`` in both, so that the project it shows is
the one it saved in its own defaults, as an app a person starts. That domain is set or cleared
only through ``harness.forget_settings`` and ``harness.save_project``, and is left cleared.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import harness
from lifecycle import Window, end_session, keep_failure, seed_project

import conftest

ASK = "Describe the tides"
REPLY = "The sea rises and falls twice a day."
SCRIPT: dict[str, Any] = {"named": {ASK: [{"say": REPLY}]}}

#: The three projects, by name, with the prefix of their records and the words that tell their
#: records and files from the others'.
PROJECTS = {
    "alpha": ("ALP", "Alpha harbour"),
    "bravo": ("BRV", "Bravo quarry"),
    "charlie": ("CHL", "Charlie meadow"),
}
LIVE = "alpha"
UNSENT = {name: f"The unsent prompt of {name}" for name in PROJECTS}


def _others(name: str) -> list[str]:
    return [other for other in PROJECTS if other != name]


def _draw(window: Window, directory: Path, name: str) -> dict[str, Any]:
    """Everything the window draws of a project, in each of its views."""
    window.view("chat")
    window.page.wait(
        "the chat to show the project's state",
        "const text = document.getElementById('chat').textContent;"
        "const start = document.getElementById('start');"
        "return name === live ? text.includes(reply)"
        " : (!!start && !start.hidden) || document.body.textContent.includes('no live session');",
        name=name,
        live=LIVE,
        reply=REPLY,
    )
    chat = window.chat()
    records = window.records()
    files = window.files()
    window.view("chat")
    return {
        "title": window.title(),
        "dropdown": window.name(),
        "chat": chat,
        "prompt": window.prompt(),
        "records": records,
        "files": files,
        "links": window.links(),
    }


def test_scenario_b_switching(run: harness.Run, check_build: Path, results) -> None:
    projects = {
        name: seed_project(run, name, prefix, line=line)
        for name, (prefix, line) in PROJECTS.items()
    }
    run.use_script(SCRIPT)
    server = harness.start_server(run, projects[LIVE])
    results.server_version = server.version
    live = server.of(projects[LIVE])
    session = live.start_session()["row"]["session_id"]
    harness.forget_settings()
    scenario = harness.Scenario("B", "Projects: switching, links and a first start", conftest.OUT)
    results.scenarios.append(scenario)
    app = harness.start_app(run, server)
    scenario.app = app
    window = Window(app)
    saw: dict[str, Any] = {}
    try:
        window.loaded()
        window.make_key()

        # B.1 first start: with no saved project the list of projects is shown.
        window.page.wait(
            "the list of projects",
            "return document.getElementById('projects-list').querySelectorAll('.project-row')"
            ".length >= 3;",
        )
        first = window.project_list()
        first["title"] = window.title()
        first["saved"] = harness.saved_project()
        scenario.check(
            "B.first-start",
            "Started with no saved project, the app shows the list of projects, with every "
            "project the server serves, and no project's chat.",
            first["shown"]
            and first["none"]
            and sorted(first["rows"]) == sorted(PROJECTS)
            and first["button"] == "Choose a project"
            and first["title"] == "Kyosei"
            and first["saved"] is None,
            first,
            look="first-start-list",
        )

        # B.2 the live project is chosen from the list; its chat follows the session.
        window.pick(LIVE)
        window.page.wait(
            "the chat to follow the live session",
            "return document.getElementById('start').hidden"
            " && !document.body.classList.contains('ended')"
            " && window.kyo.composer.boxes().length > 0;",
            deadline=30,
        )
        window.page.eval("window.kyo.composer.select(0, 0);")
        window.type_prompt(ASK)
        window.page.eval("document.querySelector('#boxes .box.main .box-send').click();")
        window.page.wait(
            "the reply in the chat",
            "return document.getElementById('chat').textContent.includes(reply);",
            reply=REPLY,
            deadline=30,
        )
        window.page.wait(
            "the composer empty again",
            "const boxes = window.kyo.composer.boxes();"
            "return boxes.length === 1 && boxes[0].text === '';",
        )
        window.type_prompt(UNSENT[LIVE])

        # B.3 a prompt left unsent in each project, and what the window draws of each.
        for name in ("bravo", "charlie"):
            window.choose(name)
            window.type_prompt(UNSENT[name])
        # The list of projects is read again each time the dropdown is opened, so that the
        # links it draws are the server's live sessions.
        window.page.wait(
            "the live link", "return !!document.querySelector('#session-links .session-link');"
        )

        saw["order"] = []
        for name in ("alpha", "charlie", "bravo", "alpha"):
            window.choose(name)
            window.wait_prompt(UNSENT[name], f"of {name}")
            drawn = _draw(window, conftest.OUT, name)
            drawn["name"] = name
            saw["order"].append(drawn)
            ours = PROJECTS[name][1]
            theirs = [PROJECTS[other][1] for other in _others(name)]
            scenario.check(
                f"B.follows-{len(saw['order'])}-{name}",
                f"After switching to {name} the dropdown and the window's title name it, the "
                "chat, Records and Files are its own, the composer holds its unsent prompt, and "
                "nothing of the project left is drawn.",
                drawn["title"] == name
                and drawn["dropdown"] == name
                and (REPLY in drawn["chat"]) == (name == LIVE)
                and drawn["prompt"] == UNSENT[name]
                and any(ours in title for title in drawn["records"])
                and not any(other in title for title in drawn["records"] for other in theirs)
                and f"{name}-notes.md" in drawn["files"]
                and not any(f"{other}-notes.md" in drawn["files"] for other in _others(name))
                and drawn["links"] == [LIVE],
                drawn,
                look=f"switched-to-{name}-{len(saw['order'])}",
            )

        # The status bar is the app's, and is read from a snapshot: the right-hand label of a
        # project with no session is the same words in each, and the live project's differs.
        window.choose("bravo")
        bravo = window.status_strip(conftest.OUT, "bravo")
        window.choose("charlie")
        charlie = window.status_strip(conftest.OUT, "charlie")
        window.choose(LIVE)
        live_strip = window.status_strip(conftest.OUT, LIVE)
        saw["status"] = {"bravo": bravo, "charlie": charlie, "alpha": live_strip}
        scenario.check(
            "B.status-bar",
            "The status bar follows the project: two projects with no session draw the same "
            "label at its right, and the live project draws another.",
            bravo["colours"] > 3
            and bravo["digest"] == charlie["digest"]
            and live_strip["digest"] != bravo["digest"],
            saw["status"],
        )

        # B.4 a live-session link switches to its project and follows its session.
        window.choose("charlie")
        window.follow_link(LIVE)
        window.page.wait(
            "the chat to follow the linked session",
            "return document.getElementById('chat').textContent.includes(reply)"
            " && !document.body.classList.contains('ended');",
            reply=REPLY,
            deadline=30,
        )
        linked = {
            "title": window.title(),
            "dropdown": window.name(),
            "prompt": window.prompt(),
            "current_link": window.page.eval(
                "return document.querySelector('#session-links [aria-current=true]')"
                "?.textContent ?? null;"
            ),
        }
        scenario.check(
            "B.link",
            "A live-session link switches the window to its project and follows its session: "
            "the title and the dropdown name the project, the chat is the session's, and the "
            "link is the current one.",
            linked["title"] == LIVE
            and linked["dropdown"] == LIVE
            and linked["current_link"] == LIVE
            and linked["prompt"] == UNSENT[LIVE],
            linked,
            look="after-the-link",
        )
        window.choose("charlie")
        window.wait_prompt(UNSENT["charlie"], "of charlie")

        # B.5 quit and start again: each project's unsent prompt is still there.
        saved_before = harness.saved_project()
        results.measurements["scenario_B_quit_status"] = app.quit()
        app = harness.start_app(run, server)
        scenario.app = app
        window = Window(app)
        window.loaded()
        window.make_key()
        window.turned("charlie")
        again: dict[str, Any] = {"saved_before_the_quit": saved_before}
        for name in ("charlie", "alpha", "bravo"):
            if window.name() != name:
                window.choose(name)
            window.wait_prompt(UNSENT[name], f"of {name} after the restart")
            again[name] = window.prompt()
        scenario.check(
            "B.restart",
            "After the app is quit and started again it shows the project it last showed, and "
            "each project's unsent prompt, the live project's among them, is in its composer.",
            saved_before == "charlie"
            and again == {"saved_before_the_quit": "charlie", **UNSENT},
            again,
        )

        # B.6 a project the server no longer lists, saved as the last one shown.
        window.choose("charlie")
        results.measurements["scenario_B_quit_status_2"] = app.quit()
        harness.save_project("retired")
        app = harness.start_app(run, server)
        scenario.app = app
        window = Window(app)
        window.loaded()
        window.page.wait(
            "the list of projects",
            "return document.getElementById('projects-list').querySelectorAll('.project-row')"
            ".length >= 3;",
        )
        gone = window.project_list()
        gone["title"] = window.title()
        gone["saved"] = harness.saved_project()
        scenario.check(
            "B.not-served",
            "Started with a saved project the server no longer lists, the app shows the list of "
            "projects and no chat, and the saved project is left as it was.",
            gone["shown"]
            and gone["none"]
            and sorted(gone["rows"]) == sorted(PROJECTS)
            and gone["title"] == "Kyosei"
            and gone["saved"] == "retired",
            gone,
            look="not-served-list",
        )
        scenario.check(
            "B.session",
            "The scripted session of the live project was the one every part of this scenario "
            "followed, and is still live.",
            live.live_session() is not None and live.live_session()["session_id"] == session,
            {"session": session},
        )
    except Exception as failure:
        scenario.error = f"{type(failure).__name__}: {failure}"
        keep_failure("B", window, run, server)
        (conftest.OUT / "B-saw.json").write_text(json.dumps(saw, indent=2), encoding="utf-8")
        raise
    finally:
        scenario.finish()
        results.measurements["scenario_B_seconds"] = scenario.seconds
        results.measurements["scenario_B_launch_seconds"] = round(app.launch_seconds, 2)
        results.measurements["scenario_B_final_quit_status"] = app.quit()
        harness.forget_settings()
    failed = [f"{a.id}: {a.text} ({a.detail})" for a in scenario.failed()]
    assert not failed, "\n".join(failed)
    assert harness.calls_reaching_another_socket(run.tmux_log, run.tmux_socket) == []
    assert harness.leftover_check_builds() == []


def _boxes(server: harness.Server, path: str) -> list[dict[str, Any]]:
    return server.get(path).get("boxes", [])


def test_scenario_b_the_projects_draft(run: harness.Run, check_build: Path, results) -> None:
    project = seed_project(run, "bravo", "BRV", line="Bravo quarry")
    run.use_script(SCRIPT)
    server = harness.start_server(run, project)
    results.server_version = server.version
    harness.forget_settings()
    scenario = harness.Scenario("B2", "Projects: the project's draft", conftest.OUT)
    results.scenarios.append(scenario)
    harness.save_project("bravo")
    app = harness.start_app(run, server)
    scenario.app = app
    window = Window(app)
    tagged = "A tagged line written between sessions"
    box_id = "device-two-box"
    try:
        window.loaded()
        window.make_key()
        window.page.wait(
            "the Start panel", "const p = document.getElementById('start'); return p && !p.hidden;"
        )

        # B.4a the runner saves the project's draft as another device, a tagged prompt.
        status, _ = server.ask(
            "PUT",
            "draft",
            {
                "boxes": [
                    {
                        "id": box_id,
                        "text": tagged,
                        "marks": [{"line": 0, "items": [{"kind": "tag", "name": "review"}]}],
                    }
                ]
            },
            headers={"If-None-Match": "*"},
        )
        assert status in (200, 201), status
        window.wait_prompt(tagged, "from the project's draft")
        shown = window.page.eval(
            "return {boxes: window.kyo.composer.boxes().map((b) => [b.kind, b.text]),"
            " marks: [...document.querySelectorAll('#boxes .box')]"
            "  .map((box) => box.querySelectorAll('.mark.held').length)};"
        )
        scenario.check(
            "B.draft-shown",
            "With no live session, a tagged prompt the runner saved to the project's draft as "
            "another device is shown by the app, with its tag on its line.",
            shown["boxes"] == [["prompt", tagged]] and shown["marks"] == [1],
            shown,
            look="project-draft-shown",
        )

        # B.4b a session started has it in its composer, once.
        window.page.eval("document.getElementById('start-button').click();")
        window.page.wait(
            "the chat to follow the started session",
            "return document.getElementById('start').hidden"
            " && !document.body.classList.contains('ended')"
            " && window.kyo.composer.boxes().length > 0;",
            deadline=30,
        )
        row = harness.poll("the server to list the session", server.live_session, deadline=30)
        first = row["session_id"]
        harness.poll(
            "the server to hold the prompt as the session's draft",
            lambda: [b["text"] for b in _boxes(server, f"sessions/{first}/draft")] == [tagged],
        )
        window.wait_prompt(tagged, "of the started session")
        started = window.page.eval(
            "return {boxes: window.kyo.composer.boxes().map((b) => [b.kind, b.text]),"
            " marks: [...document.querySelectorAll('#boxes .box')]"
            "  .map((box) => box.querySelectorAll('.mark.held').length)};"
        )
        started["session_draft"] = [b["text"] for b in _boxes(server, f"sessions/{first}/draft")]
        started["project_draft"] = _boxes(server, "draft")
        scenario.check(
            "B.draft-taken",
            "A session started afterwards has the prompt in its composer once, with its tag, "
            "and the project's draft no longer holds it.",
            started["boxes"] == [["prompt", tagged]]
            and started["marks"] == [1]
            and started["session_draft"] == [tagged]
            and started["project_draft"] == [],
            started,
            look="session-has-the-draft",
        )

        # B.4c a session ended with an unsent prompt leaves it for the next session.
        more = tagged + "\nand a second line typed in the session"
        window.page.eval(
            "const boxes = window.kyo.composer.boxes();"
            "window.kyo.composer.select(boxes.length - 1, boxes.at(-1).text.length);"
        )
        window.page.request("key", text="\nand a second line typed in the session")
        window.wait_prompt(more, "with the second line")
        harness.poll(
            "the server to hold the second line",
            lambda: [b["text"] for b in _boxes(server, f"sessions/{first}/draft")] == [more],
        )
        end_session(run, server, first)
        window.page.wait(
            "the app to show the session ended",
            "return document.body.classList.contains('ended')"
            " || !document.getElementById('start').hidden;",
            deadline=30,
        )
        left = [b["text"] for b in _boxes(server, "draft")]
        scenario.check(
            "B.draft-left",
            "A session ended with an unsent prompt leaves it to the project: the project's "
            "draft holds it, and the ended session's draft does not.",
            left == [more] and _boxes(server, f"sessions/{first}/draft") == [],
            {
                "project_draft": left,
                "ended_session_draft": _boxes(server, f"sessions/{first}/draft"),
            },
            look="session-ended",
        )
        window.page.wait(
            "the Start panel after the end",
            "const p = document.getElementById('start'); return p && !p.hidden;",
            deadline=30,
        )
        window.page.eval("document.getElementById('start-button').click();")
        window.page.wait(
            "the chat to follow the next session",
            "return document.getElementById('start').hidden"
            " && !document.body.classList.contains('ended');",
            deadline=30,
        )
        second = harness.poll(
            "the server to list the next session",
            lambda: next(
                (
                    row
                    for row in server.get("sessions")["sessions"]
                    if row.get("live") and row.get("has_pane") and row["session_id"] != first
                ),
                None,
            ),
            deadline=30,
        )["session_id"]
        window.wait_prompt(more, "of the next session")
        nxt = {
            "composer": window.page.eval(
                "return window.kyo.composer.boxes().map((b) => [b.kind, b.text]);"
            ),
            "session_draft": [b["text"] for b in _boxes(server, f"sessions/{second}/draft")],
            "project_draft": _boxes(server, "draft"),
        }
        scenario.check(
            "B.draft-next-session",
            "The next session has the unsent prompt in its composer, once.",
            nxt["composer"] == [["prompt", more]]
            and nxt["session_draft"] == [more]
            and nxt["project_draft"] == [],
            nxt,
            look="next-session-has-the-draft",
        )
    except Exception as failure:
        scenario.error = f"{type(failure).__name__}: {failure}"
        keep_failure("B2", window, run, server)
        raise
    finally:
        scenario.finish()
        results.measurements["scenario_B2_seconds"] = scenario.seconds
        results.measurements["scenario_B2_launch_seconds"] = round(app.launch_seconds, 2)
        results.measurements["scenario_B2_quit_status"] = app.quit()
        harness.forget_settings()
    failed = [f"{a.id}: {a.text} ({a.detail})" for a in scenario.failed()]
    assert not failed, "\n".join(failed)
    assert harness.calls_reaching_another_socket(run.tmux_log, run.tmux_socket) == []
    assert harness.leftover_check_builds() == []
