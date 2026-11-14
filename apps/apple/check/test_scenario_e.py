"""Scenario E of proposal KYO-942: Chat while a session works, in the check build (KYO-946.c6).

The looks are those the maintainer made by hand for KYO-883 c1 to c3: the working line, the
arrow back to the end of the chat, and a prompt drawn dimmed until the server confirms it.
The session is the scripted one (``scripted_session.py``), started from the app's own Start
button through the test server's Start route (KYO-946.c1), and the prompts are typed into
the app's composer and sent with its Send button, so the server types them into the session's
pane (KYO-946.c2). The app is given its device token in its environment and makes no
keychain call (KYO-946.c5).

A second, short scenario follows in the same app, Q: a question the session asks reaches the
chat while it is open, from the session's hook, before the transcript holds the call
(KYO-946.c4). The proposal's scenario D covers the question card in full.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import harness

import conftest

TIDES = "Tell me about tides"
COUNT = "Count to three"
COLOUR = "Ask me a colour"

#: A working row as wide as an 80-column pane takes on one line.
WORKING = "Hatching… (1m 12s · ↓ 3.6k tokens · thought for 3s · reading four tide tables)"
assert len(WORKING) < 80

LONG = "\n\n".join(
    f"Paragraph {number} of a long reply about tides. The sea rises and falls twice a day, "
    "and the height of each tide follows the Moon and the Sun."
    for number in range(1, 31)
)

QUESTION = {
    "question": "Which colour do you prefer?",
    "header": "Colour",
    "multiSelect": False,
    "options": [
        {"label": "Red", "description": "Choose red."},
        {"label": "Blue", "description": "Choose blue."},
    ],
}

SCRIPT: dict[str, Any] = {
    "named": {
        TIDES: [
            {"working": WORKING},
            {"background": {"shells": 2, "agents": [["general-purpose", "Read the tide tables"]]}},
            {"wait": 5.0},
            {"background": {"shells": 0, "agents": []}},
            {"say": LONG},
        ],
        COUNT: [
            {"say": "One.", "after": 0.5},
            {"say": "Two.", "after": 4.0},
            {"say": "Three.", "after": 1.0},
        ],
        COLOUR: [{"ask": {"questions": [QUESTION], "hook": "held"}}, {"say": "Thanks."}],
    }
}

CHAT = "document.getElementById('chat').textContent"


def _type_and_send(page: harness.Channel, text: str) -> dict[str, Any]:
    """Type ``text`` into the prompt's box with key events, press Send, and return what the
    chat drew at the press. The keys are taken one after another by the page, so the box is
    waited for before the button is pressed."""
    page.eval(
        "const boxes = window.kyo.composer.boxes();"
        "window.kyo.composer.select(boxes.length - 1, boxes[boxes.length - 1].text.length);"
    )
    page.request("key", text=text)
    page.wait(
        "the typed prompt in the composer",
        "return window.kyo.composer.boxes().at(-1).text === text;",
        text=text,
    )
    return page.eval(
        "document.querySelector('#boxes .box.main .box-send').click();"
        "const drawn = document.querySelector('#chat .turn.sending');"
        "return drawn ? {text: drawn.textContent, opacity: getComputedStyle(drawn).opacity} : null;"
    )


def test_scenario_e_chat_while_a_session_works(
    run: harness.Run, check_build: Path, results
) -> None:
    project = harness.make_project(run)
    harness.commit_all(run, project, "The check project")
    run.use_script(SCRIPT)
    server = harness.start_server(run, project)
    results.server_version = server.version
    harness.forget_settings()
    scenario = harness.Scenario("E", "Chat while a session works", conftest.OUT)
    question = harness.Scenario("Q", "A question reaches the chat while it is open", conftest.OUT)
    results.scenarios += [scenario, question]
    app = harness.start_app(run, server, project=server.name)
    scenario.app = question.app = app
    page = app.channel
    current = scenario
    try:
        page.request("window", focus=True)
        _start(scenario, page, run, server)
        _dimmed_prompt(scenario, page, run)
        _working_line(scenario, page)
        _arrow(scenario, page)
        scenario.finish()
        current = question
        question.began = time.monotonic()
        _question(question, page, run)
        results.measurements["unactivated"] = {
            **page.eval(
                "return {document_has_focus: document.hasFocus(), "
                "visibility: document.visibilityState, "
                "composer_box_says_focused: window.kyo.composer.boxes().at(-1).focused};"
            ),
            **{
                key: value
                for key, value in page.request("state").items()
                if key in ("key", "main", "active", "accessory", "became_active", "menus_on_screen")
            },
        }
    except Exception as failure:
        current.error = f"{type(failure).__name__}: {failure}"
        try:
            current.snapshot("at-failure")
            (conftest.OUT / "E-page-at-failure.html").write_text(
                page.eval("return document.documentElement.outerHTML;"), encoding="utf-8"
            )
        except harness.CheckError:
            pass
        for name, source in (("serve.log", server.log), ("session.jsonl", run.session_log)):
            if source.exists():
                (conftest.OUT / f"E-{name}").write_text(source.read_text("utf-8"), "utf-8")
        pane = run.tmux("capture-pane", "-p", "-t", "=CHK:").stdout
        (conftest.OUT / "E-pane-at-failure.txt").write_text(pane, encoding="utf-8")
        raise
    finally:
        if scenario.seconds is None:
            scenario.finish()
        question.finish()
        results.measurements["scenario_E_seconds"] = scenario.seconds
        results.measurements["scenario_Q_seconds"] = question.seconds
        results.measurements["scenario_E_launch_seconds"] = round(app.launch_seconds, 2)
        status = app.quit()
        harness.forget_settings()
        results.measurements["scenario_E_quit_status"] = status
    failed = [
        f"{assertion.id}: {assertion.text} ({assertion.detail})"
        for assertion in scenario.failed() + question.failed()
    ]
    assert not failed, "\n".join(failed)
    assert harness.calls_reaching_another_socket(run.tmux_log, run.tmux_socket) == []
    assert harness.leftover_check_builds() == []


def _start(
    scenario: harness.Scenario, page: harness.Channel, run: harness.Run, server: harness.Server
) -> None:
    page.wait(
        "the page to load",
        "return typeof window.kyo === 'object' && !!document.querySelector('.view-item');",
    )
    offered = page.wait(
        "the Start panel",
        "const panel = document.getElementById('start');"
        "return panel && !panel.hidden ? document.getElementById('start-text').textContent : '';",
    )
    began = time.monotonic()
    page.eval("document.getElementById('start-button').click();")
    page.wait(
        "the chat to follow the started session",
        "return document.getElementById('start').hidden"
        " && !document.body.classList.contains('ended')"
        " && !document.getElementById('empty-chat').hidden"
        " && window.kyo.composer.boxes().length > 0;",
        deadline=30,
    )
    seconds = round(time.monotonic() - began, 2)
    row = server.live_session()
    session = run.session_events("started")
    panes = run.tmux("list-panes", "-a", "-F", "#{session_name} #{pane_pid}").stdout.split()
    scenario.check(
        "E.start",
        "The app's Start button starts the scripted session through the test server's Start "
        "route, in the private tmux server, and the server lists it as live with a pane.",
        row is not None
        and len(session) == 1
        and panes == ["CHK", str(session[0]["pid"])]
        and row["session_id"] == session[0]["session_id"],
        {"panel_said": offered, "seconds_from_press_to_chat": seconds, "panes": panes},
    )
    loaded = page.eval(
        "return {boxes: window.kyo.composer.boxes().map((box) => box.kind),"
        " empty: document.getElementById('empty-chat').textContent,"
        " waiting: document.getElementById('working-text').textContent,"
        " note: document.getElementById('composer-note').textContent};"
    )
    executable = harness.CHECK_EXECUTABLE.read_bytes()
    scenario.check(
        "E.loads",
        "The chat and the composer load against the test server with the device token the "
        "run gave the check build, which holds no call of the keychain's tool.",
        loaded["boxes"] == ["prompt"]
        and "waiting for first input" in loaded["empty"]
        and loaded["waiting"] == "Waiting for you…"
        and b"/usr/bin/security" not in executable
        and b"KYOSEI_CHECK_TOKEN" in executable,
        {**loaded, "executable_names_the_keychain_tool": b"/usr/bin/security" in executable},
        look="chat-loaded",
    )


def _dimmed_prompt(scenario: harness.Scenario, page: harness.Channel, run: harness.Run) -> None:
    pressed = time.monotonic()
    drawn = _type_and_send(page, TIDES)
    scenario.check(
        "E.dimmed",
        "A prompt sent directly appears in the chat at the press, with the dimmed class.",
        drawn is not None and drawn["text"].strip() == TIDES and drawn["opacity"] == "0.5",
        drawn,
        look="dimmed-prompt",
    )
    still = page.eval("return !!document.querySelector('#chat .turn.sending');")
    scenario.check(
        "E.dimmed-in-the-look",
        "The prompt was still dimmed when its screenshot had been taken.",
        still,
        {"seconds_since_the_press": round(time.monotonic() - pressed, 2)},
    )
    page.wait(
        "the prompt to lose the dimmed class", "return !document.querySelector('.turn.sending');"
    )
    confirmed = round(time.monotonic() - pressed, 2)
    taken = harness.poll("the session to take the prompt", lambda: run.session_events("prompt"))
    shown = page.wait(
        "the delivered prompt in the chat",
        "const turns = [...document.querySelectorAll('#chat .turn')];"
        "return turns.length === 1 && turns[0].textContent.includes(text)"
        " ? {classes: turns[0].className, composer: window.kyo.composer.boxes().at(-1).text,"
        "    held: !document.getElementById('held').hidden} : null;",
        text=TIDES,
    )
    scenario.check(
        "E.delivered",
        "The prompt is typed into the session's pane, submitted and recorded, and the app "
        "shows it as delivered: it loses the dimmed class, is drawn once, is not held, and "
        "the composer is empty.",
        taken[0]["text"] == TIDES
        and "sending" not in shown["classes"]
        and shown["composer"] == ""
        and shown["held"] is False,
        {**shown, "pane_received": taken[0]["text"], "seconds_from_press_to_confirmed": confirmed},
    )


def _working_line(scenario: harness.Scenario, page: harness.Channel) -> None:
    page.wait(
        "the working line with the background work",
        "const words = document.getElementById('working-text').textContent;"
        "return words.includes('Hatching') && words.includes('shell');",
    )
    line = page.eval(
        "const words = document.getElementById('working-text');"
        "const style = getComputedStyle(words);"
        "const one = document.createElement('span'); one.textContent = 'X';"
        "words.parentElement.append(one); const row = one.getBoundingClientRect().height; one.remove();"
        f"return {{text: words.textContent, title: words.title, hidden: words.parentElement.hidden,"
        " height: words.getBoundingClientRect().height, one_row: row,"
        " clipped: words.scrollWidth > words.clientWidth, white_space: style.whiteSpace,"
        f" in_chat: {CHAT}.includes('Hatching') || {CHAT}.includes('tide tables')}};"
    )
    scenario.check(
        "E.working-line",
        "The working line is one row, with its whole text in the tooltip and nothing of it "
        "in the chat.",
        line["text"].startswith(WORKING)
        and line["title"] == line["text"]
        and not line["hidden"]
        and line["height"] <= line["one_row"] + 1
        and line["clipped"]
        and line["white_space"] == "nowrap"
        and not line["in_chat"],
        line,
        look="working-line",
    )
    page.wait(
        "the long reply",
        f"return {CHAT}.includes('Paragraph 30 of a long reply')"
        " && document.getElementById('working-text').textContent === 'Waiting for you…';",
        deadline=20,
    )


def _arrow(scenario: harness.Scenario, page: harness.Channel) -> None:
    _type_and_send(page, COUNT)
    page.wait("the first of three replies", f"return {CHAT}.includes('One.');")
    # From the end of the chat, with nothing counted, up to a place in the long reply.
    page.eval("const page = document.getElementById('page'); page.scrollTop = page.scrollHeight;")
    page.wait(
        "the count to clear at the end",
        "return document.getElementById('jump-count').textContent === '';",
    )
    place = page.eval(
        "const page = document.getElementById('page');"
        "page.scrollTop = Math.floor((page.scrollHeight - page.clientHeight) / 2);"
        "return page.scrollTop;"
    )
    page.wait(
        "the arrow, away from the end",
        "const arrow = document.getElementById('jump-bottom');"
        "return arrow.classList.contains('shown') && !arrow.classList.contains('counted');",
    )
    page.wait("the third reply", f"return {CHAT}.includes('Three.');", deadline=20)
    after = page.eval(
        "const page = document.getElementById('page'); const arrow = document.getElementById('jump-bottom');"
        "return {place: page.scrollTop, count: document.getElementById('jump-count').textContent,"
        " classes: arrow.className, display: getComputedStyle(arrow).display,"
        " from_the_end: page.scrollHeight - page.scrollTop - page.clientHeight};"
    )
    scenario.check(
        "E.arrow",
        "With the chat scrolled up, new output leaves the place unchanged and the arrow reads "
        '"New messages: N", here the two replies that arrived.',
        place > 0
        and after["place"] == place
        and after["count"] == "New messages: 2"
        and "shown" in after["classes"]
        and "counted" in after["classes"]
        and after["display"] != "none",
        {"place_before": place, **after},
        look="arrow",
    )
    page.eval("const page = document.getElementById('page'); page.scrollTop = page.scrollHeight;")
    cleared = page.wait(
        "the count to clear at the end",
        "const arrow = document.getElementById('jump-bottom');"
        "return document.getElementById('jump-count').textContent === ''"
        " && !arrow.classList.contains('shown') ? {classes: arrow.className} : null;",
    )
    scenario.check(
        "E.arrow-clears",
        "At the end of the chat the count clears and the arrow goes.",
        "counted" not in cleared["classes"],
        cleared,
    )
    page.wait(
        "the session to be idle again",
        "return document.getElementById('working-text').textContent === 'Waiting for you…';",
    )


def _question(scenario: harness.Scenario, page: harness.Channel, run: harness.Run) -> None:
    _type_and_send(page, COLOUR)
    card = page.wait(
        "the question in the chat",
        "const cards = [...document.querySelectorAll('#chat .question')];"
        "return cards.length ? {cards: cards.length, text: cards[0].textContent,"
        " line: document.getElementById('working-text').textContent} : null;",
        deadline=10,
    )
    prompt = [row for row in run.session_events("prompt") if row["text"] == COLOUR][0]
    rows = [json.loads(line) for line in _transcript(run).read_text("utf-8").splitlines()]
    after = rows[[row["uuid"] for row in rows].index(prompt["uuid"]) + 1 :]
    hooks = [row for row in run.session_events("hook") if row["hook_event"] == "PreToolUse"]
    scenario.check(
        "Q.open",
        "With the session's hook, the question reaches the chat while its dialog is open and "
        "before the transcript holds the call.",
        card["cards"] == 1
        and QUESTION["question"] in card["text"]
        and after == []
        and len(hooks) == 1
        and not run.session_events("answered"),
        {**card, "transcript_rows_after_the_prompt": len(after)},
        look="question-open",
    )
    scenario.check(
        "Q.waiting",
        "The line under the composer says the session waits for an answer in the terminal.",
        card["line"] == "Waiting for your answer in the terminal",
        card["line"],
    )
    # Answered as a person does, in the terminal: Return takes the first option.
    run.tmux("send-keys", "-t", "=CHK:", "Enter")
    page.wait("the reply after the answer", f"return {CHAT}.includes('Thanks.');", deadline=15)
    settled = page.eval(
        "const cards = [...document.querySelectorAll('#chat .question')];"
        "return {cards: cards.length, text: cards.map((card) => card.textContent).join(' | ')};"
    )
    answered = run.session_events("answered")
    writes = [row for row in run.session_events("write") if row["t"] >= answered[0]["t"]]
    scenario.check(
        "Q.settled",
        "When the call and its answer reach the transcript together, the same card shows the "
        "outcome and no second card is drawn.",
        settled["cards"] == 1
        and answered[0]["answers"] == {QUESTION["question"]: "Red"}
        and [record["content"] for record in writes[0]["records"]]
        == [["tool_use"], ["tool_result"]],
        settled,
        look="question-answered",
    )


def _transcript(run: harness.Run) -> Path:
    return Path(run.session_events("started")[0]["transcript"])
