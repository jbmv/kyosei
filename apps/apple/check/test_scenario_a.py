"""Scenario A of proposal KYO-942: the composer sequence, in the check build (KYO-947).

The maintainer's own hand test of 2026-11-14 (KYO-936, KYO-938, KYO-939), extended as they
asked: a scratchpad of three lines, one anchored to a reply, one tagged and one with an image
becomes the prompt, and each move is undone twice, and redone. The session is the scripted one
(``scripted_session.py``) started by the app's own Start button (KYO-946); the words are typed
with key events and the moves are made with the page's own buttons.

WHAT IS DRIVEN HOW (``composer.py`` says the rest). Command-Z, Command-Shift-Z, Command-X and
Command-V go through the check channel's ``key`` request, which passes through the app's menu
bar and responder chain as a hardware key does. Edit > Undo and Edit > Redo are chosen through
the ``menu`` request. An app that was not brought to the front has no key window, and without
one the Edit menu's items are disabled and Command-X does nothing (measured before the window
request, ``scenario_A_edit_menu_without_a_key_window``); the scenario's first request makes
the window the one that stands as key (``CheckKeyWindow`` in the app's source), after which
the app finds each action's responder, validates the item against it and sends the action,
all with its own code. An assertion of a menu step therefore holds four things apart: the item
was validated enabled, the responder was the app's web view, the action sent was ``undo:``,
and the page's ``window.kyo.undo`` was then called once, by that action, the runner never
calling it. A cut and a paste use the check build's own pasteboard, which stands in the
general one's place in that process (``CheckPasteboard``), and step 6 asserts that the general
pasteboard's change count, the one thing read of it, did not move across the cut and the
paste. What a menu request leaves out of a person's click is said in the app's README.

The marks of a box are read from the draft on the test server, which is what a second device
reads; the page has no read of its marks but the DOM, which shows a mark's title only.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import harness
from composer import Composer

import conftest

ASK = "Describe the tides"
REPLY = (
    "The sea rises and falls twice a day.\n\nThe height of each tide follows the Moon and the Sun."
)
SCRIPT: dict[str, Any] = {"named": {ASK: [{"say": REPLY}]}}

LINES = ["alpha line", "beta line", "gamma line"]
TAG = "review"
#: What a scratchpad of one marked line is made of.
SECOND = "delta line"
SECOND_TAG = "second"


def _open(page: harness.Channel, run: harness.Run, server: harness.Server) -> None:
    """The page loaded, the scripted session started from the Start button, and the chat open."""
    page.wait(
        "the page to load",
        "return typeof window.kyo === 'object' && !!document.querySelector('.view-item');",
    )
    page.wait(
        "the Start panel", "const p = document.getElementById('start'); return p && !p.hidden;"
    )
    page.eval("document.getElementById('start-button').click();")
    page.wait(
        "the chat to follow the started session",
        "return document.getElementById('start').hidden"
        " && !document.body.classList.contains('ended')"
        " && window.kyo.composer.boxes().length > 0;",
        deadline=30,
    )
    harness.poll("the server to list the session as live", server.live_session, deadline=30)


def test_scenario_a_the_composer_sequence(run: harness.Run, check_build: Path, results) -> None:
    project = harness.make_project(run)
    harness.commit_all(run, project, "The check project")
    run.use_script(SCRIPT)
    server = harness.start_server(run, project)
    results.server_version = server.version
    harness.forget_settings()
    scenario = harness.Scenario("A", "The composer sequence", conftest.OUT)
    results.scenarios.append(scenario)
    app = harness.start_app(run, server, project=server.name)
    scenario.app = app
    page = app.channel
    composer = Composer(page, server)
    try:
        _open(page, run, server)
        composer.session = server.live_session()["session_id"]
        _reply(composer, run)
        pad = _step_1(scenario, composer)
        _measure_edit_menu(composer, results)
        made_key = page.request("window", key=True, focus=True)
        results.measurements["scenario_A_window"] = made_key
        composer.count_undo_calls()
        first = _move(scenario, composer, "A.2", "Create prompt", "Create prompt", pad)
        full = _step_5(scenario, composer, first)
        _step_6(scenario, composer, full)
        _step_7(scenario, composer, full)
        _step_8(scenario, composer, run, full)
        scenario.check(
            "A.9",
            "After each step the draft on the test server, read by the runner as a second device, "
            "equalled the composer's boxes: the same boxes by id, kind and text, and as many "
            "marks on each as it draws. A box with neither words nor marks has no draft.",
            all(
                any(word in sync["after"] for sync in composer.syncs)
                for word in (
                    "the anchor",
                    "the tag",
                    "three marks",
                    "Create prompt",
                    "Edit > Undo",
                    "Edit > Redo",
                    "Command-Z",
                    "redone",
                    "Send to prompt",
                    "the paste",
                    "the cut",
                    "the send",
                )
            ),
            {"checks": len(composer.syncs), "after": composer.syncs},
        )
    except Exception as failure:
        scenario.error = f"{type(failure).__name__}: {failure}"
        _keep(scenario, composer, run, server)
        raise
    finally:
        scenario.finish()
        results.measurements["scenario_A_seconds"] = scenario.seconds
        results.measurements["scenario_A_launch_seconds"] = round(app.launch_seconds, 2)
        results.measurements["scenario_A_quit_status"] = app.quit()
        harness.forget_settings()
    failed = [f"{a.id}: {a.text} ({a.detail})" for a in scenario.failed()]
    assert not failed, "\n".join(failed)
    assert harness.calls_reaching_another_socket(run.tmux_log, run.tmux_socket) == []
    assert harness.leftover_check_builds() == []


def _keep(
    scenario: harness.Scenario, composer: Composer, run: harness.Run, server: harness.Server
) -> None:
    """What a failure leaves for a reader: the window, the page, the logs and the pane."""
    try:
        (conftest.OUT / "A-state-at-failure.json").write_text(
            json.dumps(
                {
                    "boxes": composer.boxes(),
                    "channel_state": composer.page.request("state"),
                    "document_has_focus": composer.page.eval("return document.hasFocus();"),
                    "active": composer.page.eval(
                        "return document.activeElement && document.activeElement.className;"
                    ),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        scenario.snapshot("at-failure")
        (conftest.OUT / "A-page-at-failure.html").write_text(
            composer.page.eval("return document.documentElement.outerHTML;"), encoding="utf-8"
        )
    except harness.CheckError:
        pass
    for name, source in (("serve.log", server.log), ("session.jsonl", run.session_log)):
        if source.exists():
            (conftest.OUT / f"A-{name}").write_text(source.read_text("utf-8"), "utf-8")
    pane = run.tmux("capture-pane", "-p", "-t", "=CHK:").stdout
    (conftest.OUT / "A-pane-at-failure.txt").write_text(pane, encoding="utf-8")


def _reply(composer: Composer, run: harness.Run) -> None:
    """A reply on screen, from a prompt typed in the composer and sent with its button."""
    composer.page.eval(
        "const boxes = window.kyo.composer.boxes();"
        "window.kyo.composer.select(boxes.length - 1, boxes[boxes.length - 1].text.length);"
    )
    composer.type(ASK, ASK)
    composer.press("#boxes .box.main .box-send")
    composer.page.wait(
        "the reply in the chat",
        "return document.getElementById('chat').textContent.includes('Moon and the Sun');",
        deadline=30,
    )
    composer.page.wait(
        "the composer empty again",
        "const boxes = window.kyo.composer.boxes();"
        "return boxes.length === 1 && boxes[0].text === '';",
    )


JOINED = "\n".join(LINES)
CREATE = "#boxes .box.scratch .box-create"
SEND = "#boxes .box.main .box-send"
COMMAND = ["command"]
COMMAND_SHIFT = ["command", "shift"]


def _wait_composed(
    c: Composer, what: str, expected: list[list[str]], deadline: float = 10.0
) -> None:
    c.page.wait(
        what,
        "return JSON.stringify(window.kyo.composer.boxes().map((b) => [b.kind, b.text])) === want;",
        want=json.dumps(expected, separators=(",", ":"), ensure_ascii=False),
        deadline=deadline,
    )


def _state(c: Composer, what: str) -> dict[str, Any]:
    """The composer's boxes and the server's draft of them, once the draft has caught up."""
    equal = c.settled(what)
    return {"composed": c.composed(), "held": c.held(), "second_device_equal": equal["equal"]}


def _same(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return left["composed"] == right["composed"] and left["held"] == right["held"]


def _edit_items(c: Composer, undo: bool, redo: bool) -> dict[str, Any]:
    """The Edit menu's Undo and Redo, validated as opening the menu validates them, once they
    are enabled as said or five seconds have passed: the page tells the app what its Undo
    would act on a moment after each change."""
    end = time.monotonic() + 5
    while True:
        menu = c.edit_menu()
        items = {name: menu.get(name, {}).get("enabled") for name in ("Undo", "Redo")}
        if items == {"Undo": undo, "Redo": redo} or time.monotonic() > end:
            return items
        time.sleep(0.1)


def _chosen(answer: dict[str, Any], action: str) -> bool:
    """Whether a ``menu`` request found the item enabled, with the app's web view as the
    responder of ``action``, and sent it."""
    return (
        answer.get("enabled") is True
        and answer.get("performed") is True
        and answer.get("action") == action
        and answer.get("target") == "ChatPage"
    )


def _move(
    scenario: harness.Scenario,
    c: Composer,
    id: str,
    name: str,
    label: str,
    before: dict[str, Any],
) -> dict[str, Any]:
    """One move made with the page's button, then undone and redone twice: by Edit > Undo and
    Edit > Redo through the ``menu`` request, and by Command-Z and Command-Shift-Z through the
    ``key`` request. Each undo leaves the composer what it was before the move, with the marks
    on the lines they were on, and each redo what the move made it."""
    pad_label = c.labels()
    assert pad_label == [label], f"the scratchpad's button says {pad_label}, not {label}"
    c.press(CREATE)
    c.page.wait(
        "the move to leave no scratchpad",
        "return !window.kyo.composer.boxes().some((b) => b.kind === 'scratch');",
    )
    after = _state(c, f"{name}")
    scenario.check(
        f"{id}.moved",
        f"{name}: {label} puts the scratchpad's lines and marks into the prompt, and the draft "
        "on the server equals the composer.",
        len(after["composed"]) == 1 and after["second_device_equal"],
        {"button_said": pad_label, **after},
    )

    # Edit > Undo. The page's undo is called by the item's action alone: the runner makes the
    # menu request and nothing else between the two counts.
    validated = _edit_items(c, undo=True, redo=False)
    calls = c.undo_calls()
    chosen = c.choose("Edit", "Undo")
    # Not a wait that ends the scenario: an action that ran and undid nothing is this
    # assertion's own failure, with what the menu request answered beside it.
    _becomes(c, before["composed"], 10.0)
    undone = _state(c, f"{name} undone by Edit > Undo")
    called = c.undo_calls()
    scenario.check(
        f"{id}.undo-menu",
        f"{name}, Edit > Undo chosen through the menu request: the app validated the item "
        "enabled, and Redo disabled, against its web view; it sent undo: to that view; the "
        "page's undo was then called once, by that action; and the scratchpad is back with its "
        "marks on their lines and the prompt as it was.",
        validated == {"Undo": True, "Redo": False}
        and _chosen(chosen, "undo:")
        and called == {"undo": calls["undo"] + 1, "redo": calls["redo"]}
        and _same(undone, before),
        {
            "validated_before": validated,
            "menu_request": chosen,
            "page_undo_calls": [calls, called],
            "undone": undone,
            "before": before,
        },
    )

    # Edit > Redo.
    validated = _edit_items(c, undo=False, redo=True)
    calls = called
    chosen = c.choose("Edit", "Redo")
    _becomes(c, after["composed"], 10.0)
    again = _state(c, f"{name} redone by Edit > Redo")
    called = c.undo_calls()
    scenario.check(
        f"{id}.redo-menu",
        f"{name}, Edit > Redo chosen through the menu request: validated enabled, redo: sent "
        "to the web view, the page's redo called once by it, and the lines and marks are in the "
        "prompt again.",
        validated["Redo"] is True
        and _chosen(chosen, "redo:")
        and called == {"undo": calls["undo"], "redo": calls["redo"] + 1}
        and _same(again, after),
        {
            "validated_before": validated,
            "menu_request": chosen,
            "page_undo_calls": [calls, called],
            "redone": again,
        },
    )

    # Command-Z. The key is offered to the page before the menu bar, as a hardware key is,
    # so which of the two took it is recorded and not asserted.
    calls = called
    c.page.request("key", key="z", modifiers=COMMAND)
    _wait_composed(c, f"{name} undone by Command-Z", before["composed"])
    undone_key = _state(c, f"{name} undone by Command-Z")
    after_undo = c.undo_calls()
    scenario.check(
        f"{id}.undo-key",
        f"{name}, Command-Z through the key request: the scratchpad is back with its marks on "
        "their lines and the prompt as it was.",
        _same(undone_key, before),
        {"undone": undone_key, "page_undo_calls_through_the_app": [calls, after_undo]},
    )
    c.page.request("key", key="z", modifiers=COMMAND_SHIFT)
    _wait_composed(c, f"{name} redone by Command-Shift-Z", after["composed"])
    redone = _state(c, f"{name} redone by Command-Shift-Z")
    scenario.check(
        f"{id}.redo-key",
        f"{name}, Command-Shift-Z puts the lines into the prompt again.",
        _same(redone, after),
        {"redone": redone, "page_undo_calls_through_the_app": [after_undo, c.undo_calls()]},
    )
    return after


def _step_1(scenario: harness.Scenario, c: Composer) -> dict[str, Any]:
    c.new_scratchpad()
    c.type(JOINED, JOINED, 0)
    c.anchor(0, 2)
    c.settled("the anchor")
    c.tag(0, 1, TAG)
    c.settled("the tag")
    c.image(0, len(LINES[0]) + len(LINES[1]) + 2 + 2, "tide.png")
    harness.poll(
        "the image to reach the draft",
        lambda: any(
            item.get("kind") == "file" and item.get("id")
            for box in c.server_boxes()
            for mark in box.get("marks", [])
            for item in mark["items"]
        ),
        deadline=20,
    )
    pad = _state(c, "the scratchpad's three marks")
    marks = pad["held"][0][2] if pad["held"] else []
    scenario.check(
        "A.1",
        "A scratchpad of three lines holds an anchor on the first, a tag on the second and an "
        "image sent through the app's attachment route on the third.",
        pad["composed"] == [["scratch", JOINED], ["prompt", ""]]
        and [line for line, _ in marks] == [0, 1, 2]
        and marks[0][1][0].startswith("anchor:r:")
        and marks[1][1] == [f"tag:{TAG}"]
        and marks[2][1][0].startswith("file:")
        and pad["second_device_equal"],
        pad,
        look="scratchpad-with-marks",
    )
    return pad


def _measure_edit_menu(c: Composer, results) -> None:
    """What the Edit menu and Command-X do in an app that was not brought to the front and
    has not been given a window that stands as key. A box holds the caret and an anchor
    placed in it is a step its Undo would take back, which the page has told the app; the
    whole scratchpad is selected, so there are words for a cut to take. This is read before
    the scenario's ``window`` request, and nothing here is performed: a disabled item is not
    chosen, and the key is left with no item to reach."""
    state = c.page.request("state")
    c.select(0, 0, len(JOINED))
    menu = c.edit_menu()
    measured: dict[str, Any] = {
        "window_is_key": state["key"],
        "window_stands_as_key": state["stands_as_key"],
        "app_is_active": state["active"],
        "enabled": {name: menu[name].get("enabled") for name in ("Undo", "Redo", "Cut", "Paste")},
    }
    if not menu["Undo"].get("enabled"):
        measured["menu_request"] = c.choose("Edit", "Undo")
    if not menu["Cut"].get("enabled"):
        board = c.pasteboard()
        c.page.request("key", key="x", modifiers=COMMAND)
        time.sleep(0.5)
        measured["command_x_cut_nothing"] = (
            c.composed()[0][1] == JOINED and c.pasteboard()["change_count"] == board["change_count"]
        )
    c.select(0, len(JOINED))
    results.measurements["scenario_A_edit_menu_without_a_key_window"] = measured


def _step_5(scenario: harness.Scenario, c: Composer, first: dict[str, Any]) -> dict[str, Any]:
    c.new_scratchpad()
    c.type(SECOND, SECOND, 0)
    c.tag(0, 0, SECOND_TAG)
    before = _state(c, "the second scratchpad")
    scenario.check(
        "A.5.second",
        "A second scratchpad of one tagged line stands above a prompt that holds the first "
        "move's lines.",
        before["composed"] == [["scratch", SECOND], ["prompt", JOINED]]
        and before["held"][0][2] == [[0, [f"tag:{SECOND_TAG}"]]]
        and before["held"][1] == first["held"][0],
        before,
    )
    after = _move(scenario, c, "A.5", "Send to prompt", "Send to prompt", before)
    text = after["composed"][0][1]
    marks = after["held"][0][2]
    scenario.check(
        "A.5.appended",
        "Send to prompt appends the scratchpad's line to the prompt, with its tag, after the "
        "marks the prompt held.",
        text.startswith(JOINED)
        and text.endswith(SECOND)
        and marks[:3] == first["held"][0][2]
        and marks[3:] == [[text.count("\n"), [f"tag:{SECOND_TAG}"]]],
        after,
    )
    return after


def _becomes(c: Composer, expected: list[list[str]], deadline: float = 5.0) -> bool:
    """Whether the composer's boxes become ``expected`` within the limit. Unlike a wait, this
    does not end the scenario, so that what it finds is recorded as the step's own assertion."""
    try:
        _wait_composed(c, "the boxes", expected, deadline)
    except harness.CheckError:
        return False
    return True


def _step_6(scenario: harness.Scenario, c: Composer, full: dict[str, Any]) -> None:
    text = full["composed"][0][1]
    first = LINES[0] + "\n"
    rest = text[len(first) :]
    board = c.pasteboard()
    c.cut(0, 0, len(first))
    _wait_composed(c, "the first line cut from the prompt by Command-X", [["prompt", rest]])
    cut_board = c.pasteboard()
    c.new_scratchpad()
    c.paste(0, 0)
    _wait_composed(
        c,
        "the line pasted into a scratchpad by Command-V",
        [["scratch", first], ["prompt", rest]],
    )
    pasted_board = c.pasteboard()
    scenario.check(
        "A.6.pasteboard",
        "Command-X and Command-V went through the check build's own pasteboard, which is what "
        "the app gets when it asks for the general one: it held the cut line after the cut and "
        "its change count rose. The general pasteboard's change count, the one thing read of "
        "it, was the same before the cut and after the paste; it moves if the person at the "
        "machine copies something in those moments.",
        board["is_general"] is True
        and board["name"] != "Apple CFPasteboard general"
        and cut_board["text"] == first
        and cut_board["change_count"] > board["change_count"]
        and board["person_change_count"] is not None
        and board["person_change_count"] == pasted_board["person_change_count"],
        {"before": board, "after_the_cut": cut_board, "after_the_paste": pasted_board},
    )
    pasted = _state(c, "the paste")
    pad_marks = pasted["held"][0][2]
    prompt_marks = pasted["held"][1][2]
    scenario.check(
        "A.6.carried",
        "A line cut from the prompt with Command-X and pasted into a new scratchpad with "
        "Command-V carries its anchor, and the lines the prompt kept hold none.",
        len(pad_marks) == 1
        and pad_marks[0][0] == 0
        and pad_marks[0][1][0].startswith("anchor:r:")
        and not any(item.startswith("anchor") for _, items in prompt_marks for item in items)
        and [line for line, _ in prompt_marks] == [0, 1, 2],
        pasted,
        look="cut-and-pasted",
    )
    c.page.request("key", key="z", modifiers=COMMAND)
    _wait_composed(c, "the paste undone", [["scratch", ""], ["prompt", rest]])
    undone = _state(c, "the paste undone")
    scenario.check(
        "A.6.undo-paste",
        "One Command-Z in the scratchpad takes back the pasted text and its mark together.",
        undone["held"] == [["prompt", rest, prompt_marks]],
        undone,
    )
    # The cut itself, taken back in the prompt: the line returns with what it held. The
    # emptied scratchpad leaves as the caret moves, which is step 7's rule and is asserted
    # there; this step waits for it no longer than three seconds and reads the prompt alone.
    c.select(1, 0)
    left = _becomes(c, [["prompt", rest]], 3.0)
    c.page.request("key", key="z", modifiers=COMMAND)
    c.page.wait(
        "the cut undone",
        "return window.kyo.composer.boxes().at(-1).text === text;",
        text=text,
    )
    back = _state(c, "the cut undone")
    scenario.check(
        "A.6.undo-cut",
        "One Command-Z in the prompt takes back the cut, and the line returns with its anchor.",
        back["held"] == full["held"],
        {"the_emptied_scratchpad_had_left": left, **back},
    )


def _step_7(scenario: harness.Scenario, c: Composer, full: dict[str, Any]) -> None:
    """Each of the step's three rules is one recorded assertion, reached whatever the
    others found: a scratchpad added with + is the box above the prompt, and is named by
    that place, and nothing here ends the scenario on a wait for a scratchpad to leave."""
    text = full["composed"][0][1]
    c.new_scratchpad()
    pad = len(c.boxes()) - 2
    c.type("temporary words", "temporary words", pad)
    c.cut(pad, 0, len("temporary words"))
    c.page.wait(
        "the scratchpad emptied by Command-X",
        "return window.kyo.composer.boxes().at(-2).text === '';",
    )
    c.select(len(c.boxes()) - 1, 0)
    scenario.check(
        "A.7.cut",
        "A scratchpad emptied by a cut leaves when the caret moves to the prompt.",
        _becomes(c, [["prompt", text]]),
        c.composed(),
    )
    standing = len(c.boxes())
    c.new_scratchpad()
    c.page.eval("document.activeElement.blur();")
    outside = c.page.eval(
        "return !document.getElementById('boxes').contains(document.activeElement);"
    )
    end = time.monotonic() + 1.5
    counts = set()
    while time.monotonic() < end:
        counts.add(len(c.boxes()))
        time.sleep(0.1)
    scenario.check(
        "A.7.outside",
        "A scratchpad added with + and left untyped stays while the focus is outside the composer.",
        outside and counts == {standing + 1},
        {
            "focus_outside_the_composer": outside,
            "boxes_before_the_plus": standing,
            "box_counts_over_1.5_s": sorted(counts),
        },
    )
    c.select(len(c.boxes()) - 1, 0)
    scenario.check(
        "A.7.untyped",
        "The scratchpad added with + and left untyped leaves when the caret moves to the prompt.",
        _becomes(c, [["prompt", text]]),
        c.composed(),
    )


def _step_8(
    scenario: harness.Scenario, c: Composer, run: harness.Run, full: dict[str, Any]
) -> None:
    text = full["composed"][0][1]
    c.press(SEND)
    taken = harness.poll(
        "the session to take the prompt",
        lambda: [row for row in run.session_events("prompt") if row["text"] != ASK],
        deadline=30,
    )[0]
    pane = taken["text"]
    lines = pane.split("\n")
    image = next(line.removeprefix("attached ") for line in lines if line.startswith("attached "))
    anchor = re.fullmatch(
        r'on your reply [0-9a-f-]{36} "The sea rises and falls twice[^"]*"', lines[0]
    )
    in_order = (
        anchor is not None
        and lines[1:3] == LINES[0:2]
        and lines[3] == f"attached {image}"
        and lines[4:] == [LINES[2], SECOND]
    )
    sent_marks = Path(image)
    rows = [json.loads(line) for line in _transcript(run).read_text("utf-8").splitlines()]
    person = [row for row in rows if row.get("uuid") == taken["uuid"]]
    c.page.wait(
        "the composer to be empty",
        "const boxes = window.kyo.composer.boxes();"
        "return boxes.length === 1 && boxes[0].text === ''"
        " && !document.querySelector('#boxes .mark.held');",
        deadline=15,
    )
    emptied = _state(c, "the send")
    scenario.check(
        "A.8.pane",
        "After Send the scripted session's pane received the anchor's line, the image's path and "
        "the text in order.",
        in_order,
        {"pane": pane, "sent_prompt_was": text},
    )
    scenario.check(
        "A.8.image",
        "The image file is on the test server.",
        sent_marks.is_file() and sent_marks.stat().st_size > 0,
        {"path": image, "bytes": sent_marks.stat().st_size if sent_marks.exists() else None},
    )
    scenario.check(
        "A.8.record",
        "The person's record is in the transcript, and the composer is empty, with no box on the "
        "server's draft.",
        len(person) == 1
        and person[0].get("type") == "user"
        and emptied["composed"] == [["prompt", ""]]
        and emptied["held"] == [],
        {"records": person, **emptied},
        look="after-send",
    )


def _transcript(run: harness.Run) -> Path:
    return Path(run.session_events("started")[0]["transcript"])


# ------------------------------------------------------------------------------ scenario G

LONG_TURN = "Work for a while"
LATER = "Please add this afterwards"
TYPED_SINCE = "typed while it was held"
#: How long the scripted turn runs. The last activity reads "1m ago" from a minute after the
#: turn's first record, so a prompt delivered when the turn ends finds it so, and the turn
#: writes nothing that would reset it before then. The minute is what the last assertion
#: depends on and cannot be shortened; the four seconds over it are for the page, which
#: draws the activity once a second, to have shown "1m ago" before the turn ends. Holding
#: the prompt and returning it happen within the turn and do not depend on its length.
TURN_SECONDS = 64

G_SCRIPT: dict[str, Any] = {
    "named": {LONG_TURN: [{"wait": TURN_SECONDS}], LATER: [{"say": "Added."}]}
}

ACTIVITY = "document.getElementById('top-activity').textContent"


def test_scenario_g_a_prompt_sent_during_a_turn_is_held(
    run: harness.Run, check_build: Path, results
) -> None:
    """The held-prompt part of scenario G (KYO-883 c9 and c10): a prompt sent during a turn is
    held; Edit returns it as the prompt, and what was typed since becomes a scratchpad; and the
    last activity resets when a prompt is delivered. The line under the composer and the Chat
    item taking their waiting classes are the question card's, not this test's."""
    project = harness.make_project(run)
    harness.commit_all(run, project, "The check project")
    run.use_script(G_SCRIPT)
    server = harness.start_server(run, project)
    results.server_version = server.version
    harness.forget_settings()
    scenario = harness.Scenario("G", "A prompt sent during a turn is held", conftest.OUT)
    results.scenarios.append(scenario)
    app = harness.start_app(run, server, project=server.name)
    scenario.app = app
    page = app.channel
    composer = Composer(page, server)
    try:
        page.request("window", key=True, focus=True)
        _open(page, run, server)
        composer.session = server.live_session()["session_id"]
        _held(scenario, composer, run)
    except Exception as failure:
        scenario.error = f"{type(failure).__name__}: {failure}"
        _keep(scenario, composer, run, server)
        raise
    finally:
        scenario.finish()
        results.measurements["scenario_G_seconds"] = scenario.seconds
        results.measurements["scenario_G_quit_status"] = app.quit()
        harness.forget_settings()
    failed = [f"{a.id}: {a.text} ({a.detail})" for a in scenario.failed()]
    assert not failed, "\n".join(failed)
    assert harness.calls_reaching_another_socket(run.tmux_log, run.tmux_socket) == []
    assert harness.leftover_check_builds() == []


def _send(c: Composer, text: str) -> None:
    """Type ``text`` into the prompt and press Send."""
    c.page.eval(
        "const boxes = window.kyo.composer.boxes();"
        "window.kyo.composer.select(boxes.length - 1, boxes[boxes.length - 1].text.length);"
    )
    c.type(text, text)
    c.press(SEND)


def _held(scenario: harness.Scenario, c: Composer, run: harness.Run) -> None:
    page = c.page
    _send(c, LONG_TURN)
    harness.poll(
        "the session to take the first prompt",
        lambda: [row for row in run.session_events("prompt") if row["text"] == LONG_TURN],
        deadline=30,
    )
    page.wait(
        "the session to be working",
        "return document.getElementById('working-text').textContent !== 'Waiting for you…'"
        " && !document.body.classList.contains('ended');",
        deadline=20,
    )
    _send(c, LATER)
    held = page.wait(
        "the held prompt to be shown",
        "const box = document.getElementById('held');"
        "return box && !box.hidden ? {words: document.getElementById('held-text').textContent,"
        " edit: !document.getElementById('held-edit').hidden,"
        " composer: window.kyo.composer.boxes().map((b) => [b.kind, b.text])} : null;",
        deadline=20,
    )
    taken = [row["text"] for row in run.session_events("prompt")]
    scenario.check(
        "G.held",
        "A prompt sent during a turn is held: the app says it waits for the turn, the composer "
        "is empty, and the session's pane has received only the first prompt.",
        "turn" in held["words"] and held["composer"] == [["prompt", ""]] and taken == [LONG_TURN],
        {**held, "pane_has_received": taken},
        look="held-prompt",
    )
    page.eval("window.kyo.composer.select(window.kyo.composer.boxes().length - 1, 0);")
    c.type(TYPED_SINCE, TYPED_SINCE)
    page.eval("document.getElementById('held-edit').click();")
    back = page.wait(
        "the held prompt to be returned as the prompt",
        "const boxes = window.kyo.composer.boxes().map((b) => [b.kind, b.text]);"
        "return document.getElementById('held').hidden && boxes.length === 2"
        " && boxes[1][1] === text ? boxes : null;",
        text=LATER,
        deadline=20,
    )
    still = [row["text"] for row in run.session_events("prompt")]
    scenario.check(
        "G.edit",
        "Edit returns the held prompt as the prompt, what was typed since becomes a scratchpad "
        "above it, and nothing is delivered to the pane.",
        back == [["scratch", TYPED_SINCE], ["prompt", LATER]] and still == [LONG_TURN],
        {"boxes": back, "pane_has_received": still},
        look="held-prompt-edited",
    )
    # Sent again, it is held again, and the turn's end delivers it.
    c.press(SEND)
    page.wait(
        "the returned prompt to be held again",
        "return !document.getElementById('held').hidden;",
        deadline=20,
    )
    ages: list[tuple[float, str]] = []
    began = time.monotonic()
    while time.monotonic() < began + TURN_SECONDS + 30:
        words = page.eval(f"return {ACTIVITY};")
        if not ages or ages[-1][1] != words:
            ages.append((round(time.monotonic() - began, 1), words))
        if run.session_events("prompt")[1:]:
            break
        time.sleep(0.2)
    taken = [row["text"] for row in run.session_events("prompt")]
    page.wait(
        "the delivered prompt to be shown as sent",
        "return document.getElementById('held').hidden;",
        deadline=30,
    )
    after = page.wait(
        "the last activity to reset",
        f"const words = {ACTIVITY}; return words === '< 1m ago' ? words : null;",
        deadline=15,
    )
    scenario.check(
        "G.activity",
        "The last activity read 1m ago when the turn ended and the held prompt was delivered, "
        "and reads under a minute once it was.",
        taken == [LONG_TURN, LATER]
        and "1m ago" in [words for _, words in ages]
        and after == "< 1m ago",
        {"activity_seen_at_seconds": ages, "after_delivery": after, "pane_received": taken},
        look="activity-reset",
    )
