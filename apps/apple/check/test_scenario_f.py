"""Scenario F of proposal KYO-942: Records and Files, in the check build (KYO-944.c6).

Each assertion is one of the looks the maintainer made by hand for KYO-883 c4 to c8. What
can be read from the page is asserted from it, and a screenshot of each state is put on the
review sheet for what is judged by eye. The store is made with ordinary ``kyo`` commands and
served by the real ``kyo serve``; nothing here stands in for the server or the app.
"""

from __future__ import annotations

import json
from pathlib import Path

import harness

import conftest

#: The two reds the page's ``--apple-red`` has, in the light and the dark appearance.
REDS = {"rgb(255, 59, 48)", "rgb(255, 69, 58)"}


def _seed(run: harness.Run) -> tuple[Path, dict[str, str]]:
    """A project with two open plans, members in three statuses, a shift note and files."""
    project = harness.make_project(run)
    (project / "src").mkdir()
    (project / "src" / "demo.py").write_text(
        'print("a file of the check project")\n', encoding="utf-8"
    )
    (project / "notes.md").write_text(
        "# Notes\n\nA file the check opens in Files.\n\n## A second heading\n\nMore text.\n",
        encoding="utf-8",
    )

    def new(kind: str, title: str, *more: str, stdin: str | None = None) -> str:
        made = run.kyo("new", kind, "--title", title, *more, "--json", cwd=project, stdin=stdin)
        return json.loads(made)["id"]

    def member(title: str, plan: str) -> str:
        return new("bug", title, "--plan", plan, "--criteria-file", "-", stdin="it works\n")

    ids = {"plan": new("plan", "Harness plan"), "other_plan": new("plan", "A second plan")}
    ids["open"] = member("A member still open", ids["plan"])
    ids["working"] = member("A member being worked on", ids["plan"])
    ids["retired"] = member("A member that was retired", ids["plan"])
    ids["elsewhere"] = member("A member of the second plan", ids["other_plan"])
    run.kyo("move", ids["working"], "working", cwd=project)
    run.kyo(
        "wont-do",
        ids["retired"],
        "--why-file",
        "-",
        cwd=project,
        stdin="Retired so that the plan has a member that is not outstanding.\n",
    )
    run.kyo(
        "note", "--shift", "--file", "-", cwd=project, stdin="A shift note of the check project.\n"
    )
    harness.commit_all(run, project, "The check project's records and files")
    return project, ids


def test_scenario_f_records_and_files(run: harness.Run, check_build: Path, results) -> None:
    project, ids = _seed(run)
    server = harness.start_server(run, project)
    results.server_version = server.version
    harness.forget_settings()
    scenario = harness.Scenario("F", "Records and Files", conftest.OUT)
    results.scenarios.append(scenario)
    app = harness.start_app(run, server, project=server.name)
    scenario.app = app
    page = app.channel
    try:
        # The page takes the keys the scenario sends once it is the window's first responder.
        page.request("window", focus=True)
        _records(scenario, page, ids)
        _files(scenario, page)
    except Exception as failure:
        # What the page and the server said at the failure, beside the result.
        scenario.error = f"{type(failure).__name__}: {failure}"
        try:
            scenario.snapshot("at-failure")
            (conftest.OUT / "F-page-at-failure.html").write_text(
                page.eval("return document.documentElement.outerHTML;"), encoding="utf-8"
            )
        except harness.CheckError:
            pass
        (conftest.OUT / "F-serve.log").write_text(
            server.log.read_text(encoding="utf-8"), encoding="utf-8"
        )
        raise
    finally:
        scenario.finish()
        results.measurements["scenario_F_seconds"] = scenario.seconds
        results.measurements["scenario_F_launch_seconds"] = round(app.launch_seconds, 2)
        results.measurements["scenario_F_tmux_calls_reaching_another_socket"] = len(
            harness.calls_reaching_another_socket(run.tmux_log, run.tmux_socket)
        )
        status = app.quit()
        harness.forget_settings()
        results.measurements["scenario_F_quit_status"] = status
    failed = [
        f"{assertion.id}: {assertion.text} ({assertion.detail})" for assertion in scenario.failed()
    ]
    assert not failed, "\n".join(failed)
    assert harness.calls_reaching_another_socket(run.tmux_log, run.tmux_socket) == []
    assert harness.leftover_check_builds() == []


def _show(page: harness.Channel, view: str) -> None:
    page.wait(
        "the page to load",
        "return typeof window.kyo === 'object' && !!document.querySelector('.view-item');",
    )
    page.eval('document.querySelector(`.view-item[data-view-item="${view}"]`).click();', view=view)
    page.wait(
        f"the {view} view", "return document.body.classList.contains('view-' + view);", view=view
    )


def _records(scenario: harness.Scenario, page: harness.Channel, ids: dict[str, str]) -> None:
    _show(page, "records")
    page.wait(
        "the records to be listed",
        "return document.querySelectorAll('#records-table [data-open]').length > 0"
        " && document.querySelectorAll('#records-plans [data-plan]').length === 2;",
    )

    # c5: a plan is named by its id and then its title.
    plans = page.eval(
        "return [...document.querySelectorAll('#records-plans [data-plan]')].map((row) => ({"
        " id: row.dataset.plan, name: row.querySelector('.tree-name').textContent,"
        " first: row.querySelector('.tree-name').firstElementChild?.className,"
        " firstText: row.querySelector('.tree-name').firstElementChild?.textContent }));"
    )
    expected = {
        ids["plan"]: f"{ids['plan']} Harness plan",
        ids["other_plan"]: f"{ids['other_plan']} A second plan",
    }
    scenario.check(
        "F.plans-named",
        "Plans are named by id and then title (KYO-883 c5).",
        {row["id"]: row["name"] for row in plans} == expected
        and all(row["first"] == "rec-id" and row["firstText"] == row["id"] for row in plans),
        plans,
        look="records-home",
    )

    # c6: the side bar has no Latest shift note, though the project has a shift note.
    side = page.eval(
        "return { latest: document.getElementById('records-home-latest-note') !== null,"
        " headings: [...document.querySelectorAll('#records-side .records-home-side h5')]"
        "   .map((h) => h.textContent),"
        " everyHeading: [...document.querySelectorAll('#records-side h4, #records-side h5')]"
        "   .map((h) => h.textContent),"
        " shiftNotes: document.querySelector("
        "   '#records-locations [data-location=\"Shift notes\"] .location-count')?.textContent };"
    )
    scenario.check(
        "F.no-latest-shift-note",
        "The Records side bar has no Latest shift note (KYO-883 c6); the project's shift note "
        "is counted under the Shift notes location.",
        not side["latest"]
        and side["headings"] == ["Locations", "Plans", "Recents", "Mentioned"]
        and not any("shift note" in heading.lower() for heading in side["everyHeading"])
        and side["shiftNotes"] == "1",
        side,
    )

    # c4: a chosen plan's outstanding members carry the red status class.
    page.eval(
        'document.querySelector(`#records-plans [data-plan="${plan}"]`).click();', plan=ids["plan"]
    )
    page.wait(
        "the plan's members",
        "return document.getElementById('records-landing').classList.contains('plan-chosen')"
        " && document.querySelectorAll('#records-table [data-open]').length === 3;",
    )
    members = page.eval(
        "return [...document.querySelectorAll('#records-table [data-open]')].map((row) => {"
        " const cell = row.querySelector('.rec-status');"
        " return { id: row.dataset.open, status: cell.textContent,"
        " outstanding: cell.classList.contains('outstanding'),"
        " colour: getComputedStyle(cell).color }; });"
    )
    by_id = {row["id"]: row for row in members}
    scenario.check(
        "F.outstanding-red",
        "A chosen plan's outstanding members carry the red status class, and a member that is "
        "not outstanding does not (KYO-883 c4).",
        set(by_id) == {ids["open"], ids["working"], ids["retired"]}
        and by_id[ids["open"]]["status"] == "open"
        and by_id[ids["working"]]["status"] == "working"
        and all(
            by_id[ids[name]]["outstanding"] and by_id[ids[name]]["colour"] in REDS
            for name in ("open", "working")
        )
        and not by_id[ids["retired"]]["outstanding"]
        and by_id[ids["retired"]]["colour"] not in REDS,
        members,
        look="plan-chosen",
    )

    _dock(scenario, page, "records")


def _dock(scenario: harness.Scenario, page: harness.Channel, view: str) -> None:
    """c7: one drawer, present in this view, and it docks."""
    read = (
        "const panel = document.getElementById('menu-panel');"
        "const side = document.getElementById(view + '-side');"
        "return { panels: document.querySelectorAll('#menu-panel').length,"
        " holdsSide: !!side && panel.contains(side),"
        " sideShown: !!side && getComputedStyle(side).display !== 'none' && !side.hidden,"
        " menuShown: getComputedStyle(document.getElementById('menu')).display !== 'none',"
        " docked: document.body.classList.contains('dock-left'),"
        " panelLeft: Math.round(panel.getBoundingClientRect().left),"
        " panelWidth: Math.round(panel.getBoundingClientRect().width),"
        " paneLeft: Math.round("
        "   document.getElementById('view-' + view).getBoundingClientRect().left) };"
    )
    before = page.eval(read, view=view)
    page.eval("document.getElementById('menu-icon').click();")
    page.wait("the drawer to dock", "return document.body.classList.contains('dock-left');")
    # The drawer slides and the pane moves over a transition; the wait is for their places.
    docked = page.wait(
        "the docked drawer to be in place",
        f"const now = (() => {{ {read} }})();"
        " return now.panelLeft === 0 && now.paneLeft === now.panelWidth ? now : null;",
        view=view,
    )
    scenario.check(
        f"F.drawer-{view}",
        f"One drawer is present in {view.capitalize()} and docks (KYO-883 c7): undocked it is "
        "off the page's edge, and docked it stands at the left with the view beside it.",
        before["panels"] == 1
        and before["holdsSide"]
        and before["sideShown"]
        and before["menuShown"]
        and not before["docked"]
        and before["panelLeft"] < 0
        and before["paneLeft"] == 0
        and docked["docked"]
        and docked["sideShown"]
        and docked["paneLeft"] == docked["panelWidth"] > 0,
        {"undocked": before, "docked": docked},
        look=f"{view}-docked",
    )


def _files(scenario: harness.Scenario, page: harness.Channel) -> None:
    _show(page, "files")
    page.wait(
        "the files to be listed",
        "return !!document.querySelector("
        "  '#view-files .files-columns .tree-row[data-file=\"notes.md\"]');",
    )
    _dock(scenario, page, "files")

    # c8: an open file's root and path are separate elements.
    page.eval(
        "document.querySelector("
        "  '#view-files .files-columns .tree-row[data-file=\"notes.md\"]').click();"
    )
    page.wait(
        "the file to open",
        "const file = document.getElementById('file');"
        "return !file.hidden && !!file.querySelector('.file-body, .file-code')"
        " && document.querySelectorAll('#files-path > span').length === 2;",
    )
    path = page.eval(
        "return { parts: [...document.querySelectorAll('#files-path > span')]"
        "   .map((part) => [part.className, part.textContent]),"
        " text: document.getElementById('files-path').textContent,"
        " title: document.getElementById('files-path').title,"
        " heading: document.querySelector('#file h1')?.textContent ?? null };"
    )
    scenario.check(
        "F.root-and-path",
        "An open file's root and path are separate elements (KYO-883 c8).",
        path["parts"] == [["files-path-root", "Code"], ["files-path-file", "notes.md"]]
        and "/" not in path["text"]
        and path["title"].endswith("/notes.md"),
        path,
        look="file-open",
    )

    # c8: a right-click on the path offers Copy path. The press is a mouse event sent to the
    # window, and the menu is the page's own. Copy path is not chosen: choosing it writes
    # the general pasteboard, which is the person's.
    place = page.eval(
        "const at = document.querySelector('#files-path .files-path-file').getBoundingClientRect();"
        "return { x: at.left + at.width / 2, y: at.top + at.height / 2 };"
    )
    page.request("mouse", x=place["x"], y=place["y"], button="right")
    page.wait("the path's menu", "return !!document.getElementById('path-menu');")
    menu = page.eval(
        "const menu = document.getElementById('path-menu');"
        "return { heading: menu.querySelector('.path-menu-path')?.textContent ?? null,"
        " buttons: [...menu.querySelectorAll('button')].map((button) => button.textContent) };"
    )
    scenario.check(
        "F.copy-path",
        "A right-click on the open file's path offers Copy path (KYO-883 c8).",
        "Copy path" in menu["buttons"] and (menu["heading"] or "").endswith("/notes.md"),
        menu,
        look="path-menu",
    )
    # Escape, sent as a key, closes the page's menu.
    page.request("key", key="escape")
    page.wait(
        "the path's menu to close",
        "return document.getElementById('path-menu') === null;",
        deadline=5,
    )
