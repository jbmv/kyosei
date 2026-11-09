"""KYO-703: `kyo serve`'s held messages.

A fake ``tmux`` on the server's ``PATH`` records every call and answers ``capture-pane`` with a
fixture screen. Each error that names a remedy is followed in the same test by that remedy and
the repeated request.
"""

# ruff: noqa: F811 - a fixture borrowed from another test module is named by its parameters

from __future__ import annotations

import json
import os
import stat
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from kyosei import config, device_tokens, serve_files, serve_send, session_layer

from .conftest import invoke
from .test_kyo_serve import body_json, stop
from .test_kyo_serve_send import (
    FRAME_WORKING,
    IDLE,
    MESSAGE,
    enable,
    issue,
    keys_sent,
    log_rows,
    post,
)
from .test_kyo_serve_sessions import (  # noqa: F401 - the fixtures, by name
    ENDED,
    OLD,
    SESSIONS,
    Bed,
    bed,
    running,
    stream,
)

HELD = f"{SESSIONS}/{OLD}/held"
SECRET = "hello there"
WITHIN = 10.0

PROMPTS = [
    "permission-bash.txt",
    "ask.txt",
    "usage.txt",
    "trust-dialog.synthetic.txt",
    "unrecognised-help.txt",
]


@pytest.fixture(autouse=True)
def working_screen(request: pytest.FixtureRequest) -> None:
    """A session mid-turn, looked at often; a test moves the screen to what it is about."""
    if "bed" in request.fixturenames:
        made = request.getfixturevalue("bed")
        made.show_frame(FRAME_WORKING)
        made.server.held_poll_seconds = 0.03


@pytest.fixture
def on(store_home: Path) -> str:
    enable(store_home)
    return issue("ipad", device_tokens.PERMISSION_SEND)


def wait_until(check: Callable[[], object], within: float = WITHIN) -> None:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError("the condition was not reached in time")


def call(
    bed: Bed, method: str, token: str | None, *, if_match: str | None = None
) -> tuple[int, dict]:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    if if_match is not None:
        headers["If-Match"] = if_match
    status, _, raw = bed.client.request(HELD, method=method, headers=headers)
    return status, body_json(raw)


def hold(bed: Bed, token: str, document: dict = MESSAGE) -> dict:
    status, made = post(bed, HELD, document, token)
    assert status == 200, made
    return made["held"]


def held_now(bed: Bed, token: str) -> dict | None:
    status, read = call(bed, "GET", token)
    assert status == 200, read
    return read["held"]


def waits_with(bed: Bed, token: str, reason: str | None) -> dict:
    """The held message, once its reason is the one given."""
    wait_until(lambda: (held_now(bed, token) or {}).get("reason") == reason)
    found = held_now(bed, token)
    assert found is not None
    return found


def looks(bed: Bed) -> None:
    """Wait until the watcher has looked at the pane twice more."""
    seen = bed.pane_reads()
    wait_until(lambda: bed.pane_reads() >= seen + 2)


def held_file(store_home: Path) -> Path:
    return store_home / config.COMPOSER_DIRNAME / serve_files.HELD / f"{OLD}.json"


def retract_of(bed: Bed, token: str, held: dict) -> tuple[int, dict]:
    return call(bed, "DELETE", token, if_match=held["id"])


# ------------------------------------------------------------------------- c2: holding one


def test_a_message_sent_while_working_is_held_instead_of_sent(
    bed: Bed, store_home: Path, on: str
) -> None:
    held = hold(bed, on)
    looks(bed)

    assert keys_sent(bed) == []
    assert held["state"] == "held" and held["reason"] is None and held["retrying"] is True
    assert held["device"]["name"] == "ipad" and held["automatic"] is False

    read = held_now(bed, on)
    assert read["id"] == held["id"]
    assert read["segments"] == [{"kind": "typed", "text": SECRET}]


def test_the_response_to_a_hold_and_its_log_row_hold_no_text(
    bed: Bed, store_home: Path, on: str
) -> None:
    status, made = post(bed, HELD, MESSAGE, on)
    assert status == 200 and SECRET not in json.dumps(made["held"])
    assert made["action"] == "hold"
    (row,) = log_rows(store_home)
    assert row["action"] == "hold" and row["outcome"] == "held" and row["status"] == 200
    assert SECRET not in (store_home / serve_send.LOG_FILE).read_text(encoding="utf-8")


def test_a_held_message_is_private_and_outside_git(bed: Bed, store_home: Path, on: str) -> None:
    hold(bed, on)
    path = held_file(store_home)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_a_second_message_is_refused_naming_the_remedy_and_the_remedy_clears_it(
    bed: Bed, store_home: Path, on: str
) -> None:
    first = hold(bed, on)

    status, refused = post(bed, HELD, {"segments": [{"kind": "typed", "text": "again"}]}, on)

    assert status == 409 and refused["reason"] == "already-held"
    assert refused["held_id"] == first["id"]
    assert f"If-Match: {first['id']}" in refused["error"] and "DELETE" in refused["error"]
    assert held_now(bed, on)["segments"][0]["text"] == SECRET
    assert log_rows(store_home)[-1]["reason"] == "already-held"

    assert retract_of(bed, on, first)[0] == 200  # the remedy the refusal named
    assert post(bed, HELD, {"segments": [{"kind": "typed", "text": "again"}]}, on)[0] == 200


def test_the_state_is_announced_on_the_session_stream_without_its_text(
    bed: Bed, on: str, stream
) -> None:
    opened = stream()
    assert opened.event("ready")["held"] is None

    held = hold(bed, on)
    event = opened.event("held")
    assert event["outcome"] == "held" and event["id"] == held["id"]
    assert event["held"]["segments"] == [{"kind": "typed", "length": len(SECRET)}]
    assert SECRET not in json.dumps(event)

    joined = stream()
    assert joined.event("ready")["held"]["id"] == held["id"]

    bed.show("permission-bash.txt")
    changed = opened.event("held")
    assert changed["held"]["reason"] == "screen-not-ready"

    bed.show(IDLE)
    # The first look at an idle screen clears the reason; the second delivers.
    delivered = opened.event("held")
    while delivered["outcome"] != "delivered":
        assert delivered["held"]["reason"] is None
        delivered = opened.event("held")
    assert delivered["id"] == held["id"] and delivered["held"] is None


def test_a_retraction_is_announced(bed: Bed, on: str, stream) -> None:
    opened = stream()
    held = hold(bed, on)
    opened.event("held")
    assert retract_of(bed, on, held)[0] == 200
    event = opened.event("held")
    assert event["outcome"] == "retracted" and event["id"] == held["id"]


def test_a_held_message_survives_a_server_restart_and_is_then_delivered(
    bed: Bed, project: Path, tmp_path: Path, store_home: Path, on: str, running
) -> None:
    held = hold(bed, on)
    stop(bed.client)

    again = Bed(project, tmp_path, running(environ=bed.server.environ))
    again.server.held_poll_seconds = 0.03
    assert held_now(again, on)["id"] == held["id"]
    assert keys_sent(again) == []

    again.show(IDLE)
    wait_until(lambda: held_now(again, on) is None)
    assert any("send-keys -t %1 -l -- hello there" in line for line in keys_sent(again))


def test_a_message_interrupted_during_delivery_is_not_sent_twice_after_a_restart(
    bed: Bed, project: Path, tmp_path: Path, store_home: Path, on: str, running
) -> None:
    hold(bed, on)
    stop(bed.client)
    path = held_file(store_home)
    message = json.loads(path.read_text(encoding="utf-8"))
    message["delivering"] = True
    path.write_text(json.dumps(message), encoding="utf-8")

    again = Bed(project, tmp_path, running(environ=bed.server.environ))
    again.server.held_poll_seconds = 0.03
    again.show(IDLE)
    time.sleep(0.2)

    read = held_now(again, on)
    assert read["reason"] == "delivery-interrupted" and read["retrying"] is False
    assert read["detail"] and "retract" in read["detail"]
    assert keys_sent(again) == []

    assert retract_of(again, on, read)[0] == 200  # the remedy the reason named
    assert post(again, HELD, MESSAGE, on)[0] == 200


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_a_route_with_no_token_is_refused_and_a_token_clears_it(
    bed: Bed, store_home: Path, method: str
) -> None:
    enable(store_home)
    if method == "POST":
        status, refused = post(bed, HELD, MESSAGE, None)
    else:
        status, refused = call(bed, method, None)
    assert status == 401 and refused["reason"] == "no-token"
    assert "kyo devices issue" in refused["error"]

    token = issue("ipad", device_tokens.PERMISSION_SEND)
    if method == "POST":
        assert post(bed, HELD, MESSAGE, token)[0] == 200
    elif method == "GET":
        assert call(bed, method, token)[0] == 200
    else:
        # A valid token reaches the next refusal, which has nothing to take back.
        assert call(bed, method, token)[1]["reason"] == "not-held"


def test_holding_is_refused_until_sending_is_on_and_the_remedy_clears_it(
    bed: Bed, store_home: Path
) -> None:
    token = issue("ipad", device_tokens.PERMISSION_SEND)
    status, refused = post(bed, HELD, MESSAGE, token)
    assert status == 403 and refused["reason"] == "send-disabled"
    enable(store_home)
    assert post(bed, HELD, MESSAGE, token)[0] == 200


def test_a_held_message_is_checked_as_a_message_is(bed: Bed, on: str) -> None:
    command = {"segments": [{"kind": "typed", "text": "/usage"}]}
    status, refused = post(bed, HELD, command, on)
    assert status == 400 and refused["reason"] == "message-is-a-command"
    assert held_now(bed, on) is None


def test_a_session_with_no_pane_is_refused_at_holding(bed: Bed, on: str) -> None:
    registry = bed.claude / "sessions" / f"{os.getpid()}.json"
    registry.write_text(json.dumps({"pid": os.getpid(), "sessionId": ENDED}), encoding="utf-8")
    status, refused = post(bed, HELD, MESSAGE, on)
    assert status == 409 and refused["reason"] == "no-pane"
    assert held_now(bed, on) is None


def test_a_query_string_is_refused_so_a_token_cannot_reach_the_request_log(
    bed: Bed, on: str
) -> None:
    status, refused = post(bed, f"{HELD}?token={on}", MESSAGE, on)
    assert status == 400 and "Authorization" in refused["error"]
    assert held_now(bed, on) is None


def test_the_held_route_refuses_other_methods(bed: Bed, on: str) -> None:
    status, headers, _ = bed.client.request(HELD, method="PUT", body=b"{}")
    assert status == 405 and headers["Allow"] == "GET, POST, DELETE"


# -------------------------------------------------------------------------- c3: delivering


def test_a_held_message_is_delivered_when_the_screen_is_idle_with_no_client_subscribed(
    bed: Bed, store_home: Path, on: str
) -> None:
    assert bed.server.session_hub.samplers() == []
    hold(bed, on)
    looks(bed)
    assert keys_sent(bed) == []

    bed.show(IDLE)
    wait_until(lambda: held_now(bed, on) is None)

    assert bed.server.session_hub.samplers() == []
    assert [line.split(" ", 2)[2] for line in keys_sent(bed)] == [
        "send-keys -t %1 -l -- hello there",
        "send-keys -t %1 Enter",
    ]
    assert not held_file(store_home).exists()


def test_a_delivery_is_recorded_in_the_send_log(bed: Bed, store_home: Path, on: str) -> None:
    hold(bed, on)
    bed.show(IDLE)
    wait_until(lambda: held_now(bed, on) is None)

    rows = log_rows(store_home)
    assert [row["action"] for row in rows] == ["hold", "held-delivery"]
    delivered = rows[-1]
    assert delivered["outcome"] == "sent" and delivered["status"] == 200
    assert delivered["device"]["name"] == "ipad"
    assert delivered["segments"] == [
        {"kind": "typed", "length": len(SECRET), "digest": delivered["segments"][0]["digest"]}
    ]
    assert SECRET not in (store_home / serve_send.LOG_FILE).read_text(encoding="utf-8")


def test_an_automatic_message_is_delivered_as_automatic(
    bed: Bed, store_home: Path, on: str
) -> None:
    hold(bed, on, {**MESSAGE, "automatic": True})
    bed.show(IDLE)
    wait_until(lambda: held_now(bed, on) is None)
    assert log_rows(store_home)[-1]["automatic"] is True


def test_a_message_held_on_an_idle_screen_is_delivered(bed: Bed, on: str) -> None:
    bed.show(IDLE)
    hold(bed, on)
    wait_until(lambda: held_now(bed, on) is None)
    assert keys_sent(bed)


def test_it_goes_on_the_second_of_two_idle_looks(bed: Bed, on: str) -> None:
    bed.server.held_poll_seconds = 60.0  # the watcher makes its first look and then waits
    bed.show(IDLE)
    hold(bed, on)
    wait_until(lambda: OLD in bed.server.held_messages._idle_once)
    assert keys_sent(bed) == []

    bed.server.held_messages.attend(OLD)

    assert keys_sent(bed)
    assert held_now(bed, on) is None


def test_a_working_screen_between_two_idle_looks_starts_the_count_again(bed: Bed, on: str) -> None:
    bed.server.held_poll_seconds = 60.0
    bed.show(IDLE)
    hold(bed, on)
    held_messages = bed.server.held_messages
    wait_until(lambda: OLD in held_messages._idle_once)

    bed.show_frame(FRAME_WORKING)
    held_messages.attend(OLD)
    assert OLD not in held_messages._idle_once and keys_sent(bed) == []

    bed.show(IDLE)
    held_messages.attend(OLD)
    assert keys_sent(bed) == [] and held_now(bed, on) is not None


def test_the_screen_is_checked_again_immediately_before_sending(
    bed: Bed, monkeypatch: pytest.MonkeyPatch, on: str
) -> None:
    """The screen goes back to working after the first look; only the second can catch it."""
    bed.server.held_poll_seconds = 60.0
    real = session_layer.LiveSession.screen
    states: list[str] = []

    def screen(self):
        found = real(self)
        states.append(found.state)
        if len(states) == 1:
            bed.show_frame(FRAME_WORKING)
        return found

    monkeypatch.setattr(session_layer.LiveSession, "screen", screen)
    bed.server.held_messages._idle_once.add(OLD)  # already idle once, so the first look is the send
    bed.show(IDLE)
    hold(bed, on)
    wait_until(lambda: len(states) >= 2)

    assert states == ["idle", "working"]
    assert keys_sent(bed) == []
    assert held_now(bed, on) is not None


def test_a_pane_is_not_read_while_nothing_is_held(bed: Bed, on: str) -> None:
    before = bed.pane_reads()
    time.sleep(0.2)
    assert bed.pane_reads() == before
    assert bed.server.held_messages._thread is None


def test_the_watching_ends_with_the_held_message(bed: Bed, on: str) -> None:
    bed.show(IDLE)
    hold(bed, on)
    wait_until(lambda: held_now(bed, on) is None)
    wait_until(lambda: bed.server.held_messages._thread is None)
    settled = bed.pane_reads()
    time.sleep(0.2)
    assert bed.pane_reads() == settled


def test_a_pane_is_read_no_more_than_the_poll_allows(bed: Bed, on: str) -> None:
    bed.server.held_poll_seconds = 0.1
    hold(bed, on)
    time.sleep(0.5)
    # One look reads the pane once; a tick on every 0.1 s is at most six in half a second.
    assert 1 <= bed.pane_reads() <= 7


# ----------------------------------------------------------------- c4: what cannot be delivered


@pytest.mark.parametrize("screen", PROMPTS)
def test_a_prompt_or_dialog_never_receives_it_and_it_stays_held_with_its_reason(
    bed: Bed, on: str, screen: str
) -> None:
    hold(bed, on)
    bed.show(screen)

    found = waits_with(bed, on, "screen-not-ready")
    looks(bed)
    assert keys_sent(bed) == []
    assert found["state"] == "held" and found["retrying"] is True and found["detail"]

    bed.show(IDLE)
    wait_until(lambda: held_now(bed, on) is None)
    assert keys_sent(bed)


def test_text_in_the_terminals_input_box_is_not_erased(bed: Bed, on: str) -> None:
    hold(bed, on)
    bed.show("idle.txt")  # this fixture's box reads as holding typed text

    found = waits_with(bed, on, "input-box-in-use")
    looks(bed)
    assert keys_sent(bed) == [] and found["retrying"] is True

    bed.show(IDLE)
    wait_until(lambda: held_now(bed, on) is None)


def test_a_session_that_loses_its_pane_stays_held_and_is_delivered_when_it_returns(
    bed: Bed, on: str
) -> None:
    hold(bed, on)
    registry = bed.claude / "sessions" / f"{os.getpid()}.json"
    live = registry.read_text(encoding="utf-8")
    registry.write_text(json.dumps({"pid": os.getpid(), "sessionId": ENDED}), encoding="utf-8")
    bed.show(IDLE)

    waits_with(bed, on, "no-pane")
    assert keys_sent(bed) == []

    registry.write_text(live, encoding="utf-8")
    wait_until(lambda: held_now(bed, on) is None)
    assert keys_sent(bed)


def test_sending_switched_off_keeps_it_held_and_switching_it_on_delivers_it(
    bed: Bed, store_home: Path, on: str
) -> None:
    hold(bed, on)
    enable(store_home, False)
    bed.show(IDLE)

    found = waits_with(bed, on, "send-disabled")
    assert keys_sent(bed) == [] and found["retrying"] is True

    enable(store_home, True)  # the remedy the reason's detail named
    wait_until(lambda: held_now(bed, on) is None)
    assert keys_sent(bed)


def test_a_device_revoked_while_holding_is_not_delivered_for_and_retracting_clears_it(
    bed: Bed, store_home: Path, on: str
) -> None:
    held = hold(bed, on)
    assert invoke(["devices", "revoke", "ipad"]).code == 0
    bed.show(IDLE)

    laptop = issue("laptop", device_tokens.PERMISSION_SEND)
    found = waits_with(bed, laptop, "revoked-token")
    assert found["id"] == held["id"]
    time.sleep(0.15)
    assert keys_sent(bed) == []

    status, retracted = retract_of(bed, laptop, held)  # another device takes it back
    assert status == 200 and retracted["segments"] == [{"kind": "typed", "text": SECRET}]


def test_an_unusable_log_keeps_it_held_and_clearing_the_log_delivers_it(
    bed: Bed, store_home: Path, on: str
) -> None:
    hold(bed, on)
    log = store_home / serve_send.LOG_FILE
    log.rename(log.with_suffix(".moved"))
    log.mkdir()
    bed.show(IDLE)

    found = waits_with(bed, on, "send-log-unusable")
    assert found["retrying"] is True and keys_sent(bed) == []

    log.rmdir()  # the remedy: a usable log
    wait_until(lambda: held_now(bed, on) is None)
    assert keys_sent(bed)


def test_a_delivery_that_fails_midway_stays_held_is_not_retried_and_retracting_clears_it(
    bed: Bed, store_home: Path, monkeypatch: pytest.MonkeyPatch, on: str
) -> None:
    def failing(self, segments) -> None:
        raise OSError("tmux went away")

    monkeypatch.setattr(session_layer.LiveSession, "send_message", failing)
    held = hold(bed, on)
    bed.show(IDLE)

    found = waits_with(bed, on, "send-failed")
    assert found["retrying"] is False and found["state"] == "held"
    assert "keys may have reached" in found["detail"] and "Retract" in found["detail"]
    rows = [row for row in log_rows(store_home) if row["action"] == "held-delivery"]
    assert [(row["outcome"], row["reason"]) for row in rows] == [("failed", "send-failed")]

    # It is not tried again.
    wait_until(lambda: bed.server.held_messages._thread is None)
    assert len([r for r in log_rows(store_home) if r["action"] == "held-delivery"]) == 1

    assert retract_of(bed, on, held)[0] == 200
    assert held_now(bed, on) is None


def test_a_screen_that_will_not_take_the_keys_keeps_it_held(
    bed: Bed, monkeypatch: pytest.MonkeyPatch, store_home: Path, on: str
) -> None:
    def refusing(self, segments) -> None:
        raise session_layer.PaneNotReady("the input box would not clear")

    monkeypatch.setattr(session_layer.LiveSession, "send_message", refusing)
    hold(bed, on)
    bed.show(IDLE)

    found = waits_with(bed, on, "screen-not-ready")
    assert found["retrying"] is True and found["state"] == "held"
    assert any(
        row["action"] == "held-delivery" and row["outcome"] == "refused"
        for row in log_rows(store_home)
    )

    monkeypatch.undo()
    wait_until(lambda: held_now(bed, on) is None)


# -------------------------------------------------------------------------- c5: retracting


def test_retracting_returns_its_segments_and_prevents_its_delivery(
    bed: Bed, store_home: Path, on: str
) -> None:
    document = {
        "segments": [
            {"kind": "typed", "text": "before"},
            {"kind": "pasted", "text": "the pasted\nlines"},
        ],
    }
    held = hold(bed, on, document)

    status, retracted = retract_of(bed, on, held)

    assert status == 200 and retracted["segments"] == document["segments"]
    assert retracted["id"] == held["id"]
    assert not held_file(store_home).exists()

    bed.show(IDLE)
    wait_until(lambda: bed.server.held_messages._thread is None)
    assert keys_sent(bed) == []
    rows = log_rows(store_home)
    assert [(row["action"], row["outcome"]) for row in rows] == [
        ("hold", "held"),
        ("retract", "retracted"),
    ]


def test_retracting_works_while_sending_is_off(bed: Bed, store_home: Path, on: str) -> None:
    held = hold(bed, on)
    enable(store_home, False)
    assert retract_of(bed, on, held)[0] == 200


def test_retracting_with_no_or_the_wrong_id_is_refused_and_the_right_id_clears_it(
    bed: Bed, on: str
) -> None:
    held = hold(bed, on)

    status, refused = call(bed, "DELETE", on)
    assert status == 428 and refused["held_id"] == held["id"]
    assert f"If-Match: {held['id']}" in refused["error"]

    status, refused = call(bed, "DELETE", on, if_match="someone-elses")
    assert status == 412 and refused["reason"] == "wrong-held-id"
    assert held_now(bed, on) is not None

    assert call(bed, "DELETE", on, if_match=f'"{held["id"]}"')[0] == 200  # quoted as an etag


def test_retracting_what_is_no_longer_held_is_refused_and_the_turns_route_answers(
    bed: Bed, on: str
) -> None:
    status, refused = call(bed, "DELETE", on, if_match="a-message-now-gone")
    assert status == 404 and refused["reason"] == "not-held"
    assert "turns" in refused["error"]
    assert bed.get(f"{SESSIONS}/{OLD}/turns")[0] == 200  # the remedy the refusal named


def test_a_retraction_during_a_delivery_waits_and_finds_it_delivered(
    bed: Bed, tmp_path: Path, on: str
) -> None:
    fake = tmp_path / "bin" / "tmux"
    script = fake.read_text(encoding="utf-8")
    fake.write_text(script.replace('case "$*" in\n', 'case "$*" in\n  *send-keys*) sleep 0.4 ;;\n'))
    held = hold(bed, on)
    bed.show(IDLE)
    wait_until(lambda: (held_now(bed, on) or {}).get("state") == "delivering")

    status, refused = retract_of(bed, on, held)

    assert status == 404 and refused["reason"] == "not-held"
    assert keys_sent(bed)  # delivered, and the retraction found it so


def _race_round(bed: Bed, on: str, round_number: int) -> str:
    """One hold, then a retraction and a delivery at once; which one happened."""
    held_messages = bed.server.held_messages
    bed.show(IDLE)
    held = hold(bed, on)
    held_messages._idle_once.add(OLD)
    before = len(keys_sent(bed))
    barrier = threading.Barrier(2)
    results: list[tuple[int, dict]] = []
    # In turn each side starts first, by a varying lead that covers the moment the other takes
    # the lock, so both outcomes occur and most rounds are genuine races.
    lead = 0.003 * (round_number % 10)
    retract_after, deliver_after = (lead, 0.0) if round_number % 2 else (0.0, lead)

    def retract() -> None:
        barrier.wait()
        time.sleep(retract_after)
        results.append(retract_of(bed, on, held))

    def deliver() -> None:
        barrier.wait()
        time.sleep(deliver_after)
        held_messages.attend(OLD)

    threads = [threading.Thread(target=retract), threading.Thread(target=deliver)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=WITHIN)
        assert not thread.is_alive()

    status, document = results[0]
    sent = len(keys_sent(bed)) > before
    assert held_now(bed, on) is None
    if status == 200:
        assert not sent, "retracted and also delivered"
        assert document["segments"] == [{"kind": "typed", "text": SECRET}]
        return "retracted"
    assert status == 404 and document["reason"] == "not-held", document
    assert sent, "neither retracted nor delivered"
    return "delivered"


def test_a_retraction_racing_a_delivery_has_exactly_one_outcome(
    bed: Bed, monkeypatch: pytest.MonkeyPatch, store_home: Path, on: str
) -> None:
    monkeypatch.setattr(bed.server.held_messages, "_ensure_watching", lambda: None)
    outcomes = [_race_round(bed, on, number) for number in range(40)]
    actions = [row["action"] for row in log_rows(store_home)]
    assert actions.count("retract") == outcomes.count("retracted")
    assert actions.count("held-delivery") == outcomes.count("delivered")
    assert set(outcomes) == {"retracted", "delivered"}, outcomes
