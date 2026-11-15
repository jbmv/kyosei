"""Scenario A of proposal KYO-942: the composer sequence, in the check build (KYO-947).

The maintainer's own hand test of 2026-11-14 (KYO-936, KYO-938, KYO-939), extended as they
asked: a scratchpad of three lines, one anchored to a reply, one tagged and one with an image
becomes the prompt, and each move is undone twice, and redone. The session is the scripted one
(``scripted_session.py``) started by the app's own Start button (KYO-946); the words are typed
with key events and the moves are made with the page's own buttons.

WHAT IS DRIVEN HOW (``composer.py`` says the rest). Command-Z and Command-Shift-Z go through
the check channel's ``key`` request, which passes through the app's menu bar and responder
chain as a hardware key does. Edit > Undo through the ``menu`` request cannot be driven: an
app that was not brought to the front has no key window, so the item is disabled and the
request performs nothing (measured, ``scenario_A_edit_menu_without_a_key_window``). The
scenario calls ``window.kyo.undo()`` and ``window.kyo.redo()``, which the app's two items call
when they are performed (KYO-915), and says so in the assertion. Command-X and Command-V
through the ``key`` request would go through the system pasteboard, which is the person's, and
the check build has none of its own, so a cut and a paste are events of the page's own
carrying a ``DataTransfer`` of the page's making.

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
        made_key = page.request("window", key=True, focus=True)
        results.measurements["scenario_A_window"] = made_key
        _open(page, run, server)
        composer.session = server.live_session()["session_id"]
        _reply(composer, run)
        pad = _step_1(scenario, composer)
        first = _move(scenario, composer, "A.2", "Create prompt", "Create prompt", pad)
        _measure_edit_menu(composer, results)
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
                    "kyo.undo()",
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


def _undo(c: Composer, how: str) -> Any:
    """``call`` is ``window.kyo.undo()``, which Edit > Undo calls when it is performed (KYO-915);
    ``key`` is Command-Z through the check channel's ``key`` request."""
    if how == "call":
        return c.page.eval("return window.kyo.undo();")
    return c.page.request("key", key="z", modifiers=COMMAND)


def _redo(c: Composer, how: str) -> Any:
    if how == "call":
        return c.page.eval("return window.kyo.redo();")
    return c.page.request("key", key="z", modifiers=COMMAND_SHIFT)


def _same(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return left["composed"] == right["composed"] and left["held"] == right["held"]


def _move(
    scenario: harness.Scenario,
    c: Composer,
    id: str,
    name: str,
    label: str,
    before: dict[str, Any],
) -> dict[str, Any]:
    """One move made with the page's button and undone twice: once through Edit > Undo, once
    through Command-Z, each with Command-Shift-Z or Edit > Redo after. Each time the composer
    is what it was before the move, with the marks on the lines they were on."""
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
    # The page's call that Edit > Undo makes. The item itself is disabled in an app that was
    # not brought to the front, which has no key window to validate it (measured below).
    done = _undo(c, "call")
    _wait_composed(c, f"{name} undone by kyo.undo()", before["composed"])
    undone = _state(c, f"{name} undone by kyo.undo()")
    scenario.check(
        f"{id}.undo-call",
        f"{name}, window.kyo.undo(), which Edit > Undo calls: the scratchpad is back with its "
        "marks on their lines and the prompt is as it was. Not the menu item: see "
        "scenario_A_edit_menu_without_a_key_window in the measurements.",
        _same(undone, before) and done is True,
        {"returned": done, "undone": undone, "before": before},
    )
    # Again, and Command-Z.
    c.press(CREATE)
    _wait_composed(c, f"{name} again", after["composed"])
    again = _state(c, f"{name} again")
    _undo(c, "key")
    _wait_composed(c, f"{name} undone by Command-Z", before["composed"])
    undone_key = _state(c, f"{name} undone by Command-Z")
    scenario.check(
        f"{id}.undo-key",
        f"{name}, Command-Z through the key request: the same as the call.",
        _same(again, after) and _same(undone_key, before),
        {"again": again, "undone": undone_key},
    )
    _redo(c, "key")
    _wait_composed(c, f"{name} redone by Command-Shift-Z", after["composed"])
    redone = _state(c, f"{name} redone")
    scenario.check(
        f"{id}.redo-key",
        f"{name}, Command-Shift-Z puts the lines into the prompt again.",
        _same(redone, after),
        redone,
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
    """Whether the Edit menu's items can be chosen in an app that was not brought to the front.
    The prompt holds a move that can be undone and a box holds the caret, so the page has
    told the app that Undo has something to act on."""
    menu = c.edit_menu()
    state = c.page.request("state")
    measured: dict[str, Any] = {
        "window_is_key": state["key"],
        "app_is_active": state["active"],
        "undo_enabled": menu["Undo"].get("enabled"),
        "redo_enabled": menu["Redo"].get("enabled"),
        "a_move_could_be_undone": c.page.eval(
            "return window.kyo.composer.boxes().some((b) => b.text !== '');"
        ),
    }
    if not menu["Undo"].get("enabled"):
        # A disabled item is not performed, so this changes nothing.
        measured["menu_request"] = c.page.request("menu", path=["Edit", "Undo"])
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


def _step_6(scenario: harness.Scenario, c: Composer, full: dict[str, Any]) -> None:
    text = full["composed"][0][1]
    first = LINES[0] + "\n"
    rest = text[len(first) :]
    c.cut(0, 0, len(first))
    _wait_composed(c, "the first line cut from the prompt", [["prompt", rest]])
    c.new_scratchpad()
    c.paste(0, 0)
    _wait_composed(c, "the line pasted into a scratchpad", [["scratch", first], ["prompt", rest]])
    pasted = _state(c, "the paste")
    pad_marks = pasted["held"][0][2]
    prompt_marks = pasted["held"][1][2]
    scenario.check(
        "A.6.carried",
        "A line cut from the prompt and pasted into a new scratchpad carries its anchor, and "
        "the lines the prompt kept hold none.",
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
        undone["held"] == [["prompt", rest, prompt_marks]]
        or undone["held"][-1][2] == prompt_marks
        and not any(
            item.startswith("anchor")
            for box in undone["held"][:-1]
            for _, items in box[2]
            for item in items
        ),
        undone,
    )
    # The cut itself, taken back in the prompt: the line returns with what it held.
    c.select(1, 0)
    _wait_composed(c, "the empty scratchpad to leave", [["prompt", rest]])
    c.page.request("key", key="z", modifiers=COMMAND)
    _wait_composed(c, "the cut undone", [["prompt", text]])
    back = _state(c, "the cut undone")
    scenario.check(
        "A.6.undo-cut",
        "One Command-Z in the prompt takes back the cut, and the line returns with its anchor.",
        back["held"] == full["held"],
        back,
    )


def _step_7(scenario: harness.Scenario, c: Composer, full: dict[str, Any]) -> None:
    text = full["composed"][0][1]
    c.new_scratchpad()
    c.type("temporary words", "temporary words", 0)
    c.cut(0, 0, len("temporary words"))
    _wait_composed(c, "the scratchpad emptied by a cut", [["scratch", ""], ["prompt", text]])
    c.select(1, 0)
    _wait_composed(c, "the emptied scratchpad to leave", [["prompt", text]])
    scenario.check(
        "A.7.cut",
        "A scratchpad emptied by a cut leaves when the caret moves to the prompt.",
        c.composed() == [["prompt", text]],
        c.composed(),
    )
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
        outside and counts == {2},
        {"focus_outside_the_composer": outside, "box_counts_over_1.5_s": sorted(counts)},
    )
    c.select(1, 0)
    _wait_composed(c, "the untyped scratchpad to leave", [["prompt", text]])
    scenario.check(
        "A.7.untyped",
        "The scratchpad added with + and left untyped leaves when the caret moves to the prompt.",
        c.composed() == [["prompt", text]],
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
#: writes nothing that would reset it before then.
TURN_SECONDS = 80

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
