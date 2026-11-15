"""KYO-944.c4 and c7, against the running check build.

The channel's requests each have a test in ``Tests/KyoseiMacTests/CheckChannelTests.swift``,
which asks them of a channel in the test process. These ask the app itself, for what a test
process cannot show: the app's own main menu, a native menu opened and closed, the native
status bar in a snapshot, and a quit that runs the app's own quit path.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import harness
import pytest


@pytest.fixture
def app(run: harness.Run, check_build: Path, results) -> Iterator[harness.App]:
    project = harness.make_project(run)
    harness.commit_all(run, project, "A project for the check build to show")
    server = harness.start_server(run, project)
    results.server_version = server.version
    harness.forget_settings()
    started = harness.start_app(run, server, project=server.name)
    results.measurements["launch_seconds"] = round(started.launch_seconds, 2)
    try:
        started.channel.wait(
            "the page to load",
            "return document.readyState === 'complete' && typeof window.kyo === 'object'",
        )
        yield started
    finally:
        started.quit()
        harness.forget_settings()


def test_the_app_started_is_the_check_build_and_shows_the_test_servers_project(
    app: harness.App, results
) -> None:
    state = app.channel.request("state")
    assert state["bundle"] == harness.CHECK_IDENTIFIER
    assert state["title"] == "checked"
    assert state["visible"] is True
    # KYO-945: started as an accessory app, which nothing brings in front of another app.
    assert state["accessory"] is True
    assert state["active"] is False and state["became_active"] == 0
    project = app.channel.wait(
        "the project's name in the top bar",
        "return document.querySelector('.view-item[aria-current=\"page\"]')?.dataset.viewItem",
    )
    assert project == "chat"
    # What the window is when the app was not asked to come to the front (c7).
    made_key = app.channel.request("window", key=True, focus=True)
    results.measurements["window_without_activation"] = {
        "asked": "window.makeKey() and makeFirstResponder(page), with no NSApp.activate()",
        "key": made_key["key"],
        "stands_as_key": made_key["stands_as_key"],
        "main": made_key["main"],
        "app_active": made_key["active"],
        "frontmost_app_is_the_check_build": made_key["frontmost"] == harness.CHECK_IDENTIFIER,
        "first_responder": made_key["first_responder"],
        "page_has_focus": app.channel.eval("return document.hasFocus();"),
    }
    assert made_key["first_responder"] == "ChatPage"


def test_the_apps_own_menu_is_read_and_an_item_of_it_is_performed(app: harness.App) -> None:
    menus = {menu["title"]: menu.get("items", []) for menu in app.channel.request("menus")}
    assert {"File", "Edit", "Window"} <= set(menus)
    file_items = {item.get("title"): item for item in menus["File"]}
    assert file_items["Projects…"]["key"] == "o"
    assert file_items["Projects…"]["modifiers"] == ["command"]
    edit = {item.get("title"): item for item in menus["Edit"]}
    assert edit["Redo"]["modifiers"] == ["command", "shift"]

    # File > Projects… shows the list of projects in place of the chat (KYO-766).
    before = app.channel.eval("return document.body.className;")
    done = app.channel.request("menu", path=["File", "Projects…"])
    # The item is SwiftUI's own, whose target is an object of its making.
    assert done == {
        "performed": True,
        "enabled": True,
        "title": "Projects…",
        "action": "menuAction:",
        "target": "MenuItemCallback",
    }
    after = app.channel.wait(
        "the page to change for File > Projects…",
        "return document.body.className !== before ? document.body.className : '';",
        before=before,
    )
    assert after != before


def test_a_right_press_reads_the_native_menu_and_closes_it(app: harness.App) -> None:
    box = app.channel.eval(
        "const at = document.getElementById('view-items').getBoundingClientRect();"
        "return {x: at.right + 40, y: at.top + at.height / 2};"
    )
    answer = app.channel.request("mouse", x=box["x"], y=box["y"], button="right", menu=True)
    assert answer["menu"], "no native menu opened on the right press"
    assert "Reload" in answer["menu"]
    # KYO-945: the menu was read without being put on the screen, where the window server
    # would have tracked it and taken the keyboard and pointer from the app in front.
    assert answer["on_screen"] is False
    assert app.channel.request("state")["menus_on_screen"] == 0
    # The app answers again, so it is not waiting in the menu.
    assert app.channel.eval("return 1 + 1;") == 2


def test_a_snapshot_holds_the_native_status_bar(app: harness.App, tmp_path: Path, results) -> None:
    """The status bar is SwiftUI, below the page. With no live session it reads "No
    session", so its strip of the snapshot holds the text's colours as well as its ground;
    the page's part alone leaves the strip one colour."""
    app.channel.wait(
        "the chat to say there is no session",
        "return document.body.textContent.includes('no live session');",
    )
    whole = app.channel.request("snapshot", path=str(tmp_path / "whole.png"))
    page = whole["page"]
    picture = harness.read_png(tmp_path / "whole.png")
    assert (picture.width, picture.height) == (whole["width"], whole["height"])
    top = int(page["y"] + page["height"]) + 2
    strip = picture.colours(0, top, picture.width, picture.height - 2)
    title_bar = picture.colours(0, 2, picture.width, int(page["y"]) - 2)

    app.channel.request("snapshot", path=str(tmp_path / "web.png"), parts=["web"])
    web_only = harness.read_png(tmp_path / "web.png")
    bare = web_only.colours(0, top, web_only.width, web_only.height - 2)

    results.measurements["snapshot_native_parts"] = {
        "status_bar_height_pixels": picture.height - top,
        "status_bar_distinct_colours": len(strip),
        "status_bar_distinct_colours_without_the_native_part": len(bare),
        "title_bar_distinct_colours": len(title_bar),
    }
    assert picture.height - top > 20, "there is no strip below the page"
    assert len(bare) == 1
    assert len(strip) > 8, f"the status bar's strip holds {len(strip)} colours"
    assert len(title_bar) > 8, f"the title bar holds {len(title_bar)} colours"


def test_quit_ends_the_app_through_its_own_quit_path(app: harness.App, results) -> None:
    import time

    began = time.monotonic()
    status = app.quit(deadline=20)
    results.measurements["quit_seconds"] = round(time.monotonic() - began, 2)
    # Status 0 is the app ending itself; a signal from the harness would be negative.
    assert status == 0
    assert harness.leftover_check_builds() == []
