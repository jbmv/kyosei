"""KYO-929: `kyo serve`'s idle timer, and what a credit does with the message it sends.

The clock is a fake one the timer is given, the session is the sessions tests' recorded one in a
fake tmux pane, and each look is made by calling :meth:`IdleTimer.tick` directly, so no test
waits for a poll.
"""

# ruff: noqa: F811 - a fixture borrowed from another test module is named by its parameters

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from kyosei import cmd_statusline, cmd_turns, credits, home_settings, serve_idle, serve_send
from kyosei.errors import KyoseiError

from .test_kyo658_discussions import SESSION, append_transcript, person
from .test_kyo900_turn_credits import (  # noqa: F401 - the fixtures, by name
    APPROVAL,
    PROMPT_3,
    PROMPT_4,
    T4,
    _criterion,
    session,
    talked,
)
from .test_kyo_serve_held import HELD, hold
from .test_kyo_serve_send import FRAME_WORKING, IDLE, MESSAGE, issue, keys_sent, log_rows
from .test_kyo_serve_sessions import (  # noqa: F401 - the fixtures, by name
    OLD,
    SESSIONS,
    Bed,
    bed,
    running,
    stream,
)

#: The recorded session's last record is stamped 2026-11-09T14:10:45.553Z.
LAST = datetime(2026, 11, 9, 14, 10, 45, 553000, tzinfo=UTC)
NOW = LAST + timedelta(minutes=40)
EXPIRES = NOW + timedelta(minutes=3)

FIRST_TEXT = (
    "Automated message from kyo serve, not typed by the person: this session has had no "
    "activity for 40 minutes, and its prompt cache expires at 2:53 PM. The person is likely "
    "to clear the session when they return. If you have closeout tasks, such as an interim "
    "shift note, complete them now."
)
SUBAGENT_TEXT = (
    "Automated message from kyo serve, not typed by the person: the main chat has had no "
    "activity for 40 minutes, and subagents are still running. This message refreshed your "
    "prompt cache so that their results are read on a warm one. Nothing else is asked of you."
)


def settings(store_home: Path, **keys: object) -> None:
    store_home.mkdir(parents=True, exist_ok=True)
    (store_home / home_settings.SETTINGS_FILE).write_text(yaml.safe_dump(keys), encoding="utf-8")


def both_on(store_home: Path) -> None:
    settings(store_home, serve_send=True, serve_idle_message=True)


def cache(store_home: Path, expires: datetime | None, session_id: str = OLD) -> None:
    entry = {
        "warm": True,
        "expires_at": None if expires is None else expires.timestamp(),
        "recache_tokens_if_cold": None,
    }
    path = store_home / cmd_statusline.CACHE_FILE
    path.write_text(json.dumps({"sessions": {session_id: entry}}), encoding="utf-8")


def timer(bed: Bed, now: datetime = NOW):
    clock = {"now": now}
    idle = bed.server.idle_timer
    idle.now = lambda: clock["now"]
    idle.tz = UTC
    idle.clock = clock  # type: ignore[attr-defined]
    return idle


def ready(bed: Bed, store_home: Path):
    """Both settings on, a cache about to expire, an idle screen and a session idle 40 minutes."""
    both_on(store_home)
    cache(store_home, EXPIRES)
    bed.show(IDLE)
    return timer(bed)


def idle_rows(store_home: Path) -> list[dict]:
    return [row for row in log_rows(store_home) if row["action"] == serve_send.IDLE_MESSAGE]


def person_record(uuid: str, text: str, stamp: datetime) -> str:
    return json.dumps(
        {
            "type": "user",
            "uuid": uuid,
            "parentUuid": None,
            "isSidechain": False,
            "timestamp": stamp.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "sessionId": OLD,
            "promptSource": "typed",
            "promptId": f"p-{uuid}",
            "origin": {"kind": "human"},
            "message": {"role": "user", "content": text},
        }
    ) + "\n"


def reply_record(uuid: str, stamp: datetime) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "uuid": uuid,
            "parentUuid": None,
            "isSidechain": False,
            "timestamp": stamp.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "sessionId": OLD,
            "message": {
                "id": f"msg_{uuid}",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [{"type": "text", "text": "noted"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "stop_reason": "end_turn",
            },
        }
    ) + "\n"


# --------------------------------------------------------------- c1: the setting


def test_the_setting_is_off_unless_set_and_reads_true_and_false(store_home: Path) -> None:
    assert home_settings.load({"KYOSEI_HOME": str(store_home)}).serve_idle_message is False
    for value in (True, False):
        settings(store_home, serve_idle_message=value)
        loaded = home_settings.load({"KYOSEI_HOME": str(store_home)})
        assert loaded.serve_idle_message is value
    settings(store_home, serve_send=True)
    assert home_settings.load({"KYOSEI_HOME": str(store_home)}).serve_idle_message is False


def test_a_malformed_setting_is_refused_by_name_and_the_remedy_reads(store_home: Path) -> None:
    settings(store_home, serve_idle_message="yes")
    with pytest.raises(KyoseiError) as refused:
        home_settings.load({"KYOSEI_HOME": str(store_home)})
    assert "serve_idle_message" in str(refused.value) and "'yes'" in str(refused.value)
    assert "serve_idle_message: true" in str(refused.value)
    settings(store_home, serve_idle_message=True)
    assert home_settings.load({"KYOSEI_HOME": str(store_home)}).serve_idle_message is True


def test_no_message_is_sent_with_the_setting_off_and_one_is_sent_when_it_is_on(
    bed: Bed, store_home: Path
) -> None:
    idle = ready(bed, store_home)
    settings(store_home, serve_send=True)  # serve_idle_message absent
    assert idle.tick() == [] and keys_sent(bed) == []
    both_on(store_home)
    assert idle.tick() == [OLD] and keys_sent(bed) != []


def test_the_timer_runs_only_where_sending_is_also_on(bed: Bed, store_home: Path) -> None:
    idle = ready(bed, store_home)
    settings(store_home, serve_idle_message=True)  # serve_send absent
    assert idle.tick() == [] and keys_sent(bed) == []
    settings(store_home, serve_idle_message=True, serve_send=False)
    assert idle.tick() == [] and keys_sent(bed) == []
    both_on(store_home)
    assert idle.tick() == [OLD]


# ------------------------------------------------------------ c2, c3: the message


def test_a_session_idle_with_a_cache_about_to_expire_gets_one_message(
    bed: Bed, store_home: Path
) -> None:
    idle = ready(bed, store_home)

    assert idle.tick() == [OLD]

    sent = keys_sent(bed)
    assert any(FIRST_TEXT in line for line in sent)  # typed as keys, then Enter
    assert not any("Escape" in line or "C-c" in line for line in sent)
    (row,) = idle_rows(store_home)
    assert row["automatic"] is True and row["reason"] == serve_send.IDLE_TIMER
    assert row["outcome"] == "sent" and row["device"] is None and row["session_id"] == OLD
    assert [(s["kind"], s["digest"]) for s in row["segments"]] == [
        ("typed", serve_send.digest_of(FIRST_TEXT))
    ]


def test_the_two_texts_are_pinned(bed: Bed, store_home: Path) -> None:
    expiry = datetime(2026, 11, 9, 14, 53, 45, tzinfo=UTC)
    assert serve_idle.message_text(40, expiry, subagents=False) == FIRST_TEXT
    assert serve_idle.message_text(40, expiry, subagents=True) == SUBAGENT_TEXT
    # Hours before ten and after noon read as a clock does, in the zone given.
    morning = datetime(2026, 11, 9, 0, 5, tzinfo=UTC)
    assert "expires at 12:05 AM." in serve_idle.message_text(31, morning, subagents=False)
    assert "expires at 1:30 PM." in serve_idle.message_text(
        31, datetime(2026, 11, 9, 13, 30, tzinfo=UTC), subagents=False
    )


def test_a_running_subagent_chooses_the_second_text(bed: Bed, store_home: Path) -> None:
    idle = ready(bed, store_home)
    shown = bed.screen_file.read_text(encoding="utf-8")
    bed.screen_file.write_text(
        shown + "  ◯ general-purpose  Run sleep and reply finished\n", encoding="utf-8"
    )

    assert idle.tick() == [OLD]

    assert any(SUBAGENT_TEXT in line for line in keys_sent(bed))
    assert not any(FIRST_TEXT in line for line in keys_sent(bed))


def test_the_measured_minutes_are_filled_in(bed: Bed, store_home: Path) -> None:
    idle = ready(bed, store_home)
    idle.clock["now"] = LAST + timedelta(minutes=47, seconds=30)
    cache(store_home, idle.clock["now"] + timedelta(minutes=1))

    assert idle.tick() == [OLD]

    assert any("no activity for 47 minutes" in line for line in keys_sent(bed))


# ------------------------------------------------------------ c4: once per period


def test_a_second_message_waits_for_a_turn_of_the_persons(bed: Bed, store_home: Path) -> None:
    idle = ready(bed, store_home)
    assert idle.tick() == [OLD]
    sent_at = NOW
    # Claude Code records the message as the person's turn and replies; the cache is
    # refreshed, and forty minutes on it nears expiry again.
    bed.append(
        [
            person_record("auto-1", FIRST_TEXT, sent_at + timedelta(seconds=2)),
            reply_record("r-1", sent_at + timedelta(seconds=20)),
        ]
    )
    later = sent_at + timedelta(minutes=41)
    idle.clock["now"] = later
    cache(store_home, later + timedelta(minutes=2))

    assert idle.tick() == []
    assert len(idle_rows(store_home)) == 1

    # The person returns, says something, and leaves again; the next period is messaged.
    bed.append(
        [
            person_record("real-1", "carry on with the next task", later + timedelta(minutes=1)),
            reply_record("r-2", later + timedelta(minutes=2)),
        ]
    )
    again = later + timedelta(minutes=50)
    idle.clock["now"] = again
    cache(store_home, again + timedelta(minutes=2))

    assert idle.tick() == [OLD]
    assert len(idle_rows(store_home)) == 2


def test_the_period_is_remembered_by_the_log_and_the_transcript_not_by_the_server(
    bed: Bed, store_home: Path
) -> None:
    idle = ready(bed, store_home)
    assert idle.tick() == [OLD]
    bed.append([reply_record("r-1", NOW + timedelta(seconds=20))])
    later = NOW + timedelta(minutes=41)
    cache(store_home, later + timedelta(minutes=2))
    fresh = serve_idle.IdleTimer(bed.server, now=lambda: later, tz=UTC)  # a restarted server

    assert fresh.tick() == []


# -------------------------------------------------------- c5: what prevents a message


def test_a_cache_with_no_expiry_or_a_past_one_sends_nothing(bed: Bed, store_home: Path) -> None:
    idle = ready(bed, store_home)
    cache(store_home, None)
    assert idle.tick() == []
    cache(store_home, NOW - timedelta(seconds=1))
    assert idle.tick() == []
    cache(store_home, NOW + timedelta(minutes=6))  # not yet near
    assert idle.tick() == []
    (store_home / cmd_statusline.CACHE_FILE).unlink()  # no cache saved at all
    assert idle.tick() == []
    assert keys_sent(bed) == []
    cache(store_home, EXPIRES)  # the remedy: a cache near expiry
    assert idle.tick() == [OLD]


def test_an_entry_under_thirty_minutes_old_sends_nothing(bed: Bed, store_home: Path) -> None:
    idle = ready(bed, store_home)
    idle.clock["now"] = LAST + timedelta(minutes=29, seconds=59)
    cache(store_home, idle.clock["now"] + timedelta(minutes=3))
    assert idle.tick() == [] and keys_sent(bed) == []
    # A cache that lives five minutes expires, and so is near expiry, five minutes after the
    # last entry: that never reaches thirty.
    idle.clock["now"] = LAST + timedelta(minutes=4)
    cache(store_home, LAST + timedelta(minutes=5))
    assert idle.tick() == [] and keys_sent(bed) == []
    idle.clock["now"] = LAST + timedelta(minutes=30)
    cache(store_home, idle.clock["now"] + timedelta(minutes=3))
    assert idle.tick() == [OLD]


@pytest.mark.parametrize(
    "screen",
    ["permission-bash.txt", "ask.txt", "usage.txt", "trust-dialog.synthetic.txt"],
)
def test_a_prompt_or_a_dialog_on_the_screen_sends_nothing(
    bed: Bed, store_home: Path, screen: str
) -> None:
    idle = ready(bed, store_home)
    bed.show(screen)
    assert idle.tick() == [] and keys_sent(bed) == []
    bed.show(IDLE)
    assert idle.tick() == [OLD]


def test_a_working_turn_sends_nothing(bed: Bed, store_home: Path) -> None:
    idle = ready(bed, store_home)
    bed.show_frame(FRAME_WORKING)
    assert idle.tick() == [] and keys_sent(bed) == []
    bed.show(IDLE)
    assert idle.tick() == [OLD]


def test_text_in_the_input_box_sends_nothing(bed: Bed, store_home: Path) -> None:
    idle = ready(bed, store_home)
    # idle.txt was recorded without colour, so its dim suggestion reads as typed text.
    bed.show("idle.txt")
    assert idle.tick() == [] and keys_sent(bed) == []
    bed.show(IDLE)
    assert idle.tick() == [OLD]


def test_a_held_message_prevents_it_and_retracting_it_clears_that(
    bed: Bed, store_home: Path
) -> None:
    idle = ready(bed, store_home)
    bed.server.held_poll_seconds = 1000.0  # the held message's own watcher looks once only
    token = issue("ipad", "send")
    held = hold(bed, token, MESSAGE)
    time.sleep(0.2)

    assert idle.tick() == []
    assert not any(FIRST_TEXT in line for line in keys_sent(bed))

    status, _, _ = bed.client.request(
        HELD,
        method="DELETE",
        headers={"Authorization": f"Bearer {token}", "If-Match": held["id"]},
    )
    assert status == 200
    assert idle.tick() == [OLD]


def test_a_session_without_a_pane_or_an_ended_one_is_left_alone(
    bed: Bed, store_home: Path
) -> None:
    idle = ready(bed, store_home)
    cache(store_home, EXPIRES, session_id="not-recorded-here")
    assert idle.tick() == []


def test_the_timer_never_sends_a_slash_command_or_an_interrupt(bed: Bed, store_home: Path) -> None:
    idle = ready(bed, store_home)
    assert idle.tick() == [OLD]
    assert not FIRST_TEXT.startswith("/")
    assert [row["action"] for row in log_rows(store_home)] == [serve_send.IDLE_MESSAGE]
    assert not any(
        word in line for line in keys_sent(bed) for word in ("Escape", "C-c", "C-[", "/usage")
    )


# --------------------------------------------- c6, c7: credit, and what a client reads


def automatic_turn(kyo, talked: Path, store_home: Path) -> None:
    """The session's latest turn of the person's is the idle timer's message, recorded in
    the send log as an automatic send."""
    append_transcript([person("p-4", PROMPT_4, FIRST_TEXT, T4)])
    cmd_turns.confirm_from_transcript(talked, SESSION, _transcript())
    sent = datetime.fromisoformat(T4.replace("Z", "+00:00")) - timedelta(seconds=1)
    row = {
        "at": sent.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "action": serve_send.IDLE_MESSAGE,
        "project": "talked",
        "session_id": SESSION,
        "device": None,
        "segments": [
            {"kind": "typed", "length": len(FIRST_TEXT), "digest": serve_send.digest_of(FIRST_TEXT)}
        ],
        "automatic": True,
        "outcome": "sent",
        "status": 200,
        "reason": serve_send.IDLE_TIMER,
    }
    store_home.mkdir(parents=True, exist_ok=True)
    with (store_home / serve_send.LOG_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def _transcript() -> Path:
    from .test_kyo658_discussions import transcript_path

    return transcript_path()


def test_a_bare_credit_takes_the_persons_previous_turn_not_the_automated_message(
    kyo, talked: Path, store_home: Path
) -> None:
    automatic_turn(kyo, talked, store_home)
    record, cid = _criterion(kyo)

    done = kyo("verify", cid, "--verdict", "met", "--method", "human-observed")

    assert done.code == 0, done
    credit = kyo("show", record, "--json").json["credits"][-1]
    assert credit["source_turn"] == "p-3"
    words = next(
        e["text"]
        for e in kyo("show", record, "--json").json["evidence_artifacts"]
        if e["id"] == credit["quote_id"]
    )
    assert words == APPROVAL


def test_confirm_skips_the_automated_message_too(kyo, talked: Path, store_home: Path) -> None:
    automatic_turn(kyo, talked, store_home)
    record, cid = _criterion(kyo)

    done = kyo("confirm", record, "--criterion", cid, "--no-close")

    assert done.code == 0, done
    assert kyo("show", record, "--json").json["credits"][-1]["source_turn"] == "p-3"


def test_a_turn_naming_the_automated_message_is_refused_and_the_remedy_clears_it(
    kyo, talked: Path, store_home: Path
) -> None:
    automatic_turn(kyo, talked, store_home)
    record, cid = _criterion(kyo)
    line = ("verify", cid, "--verdict", "met", "--method", "human-observed")

    for flag in ("--turn", "--context-turn"):
        args = (flag, PROMPT_4) if flag == "--turn" else ("--turn", PROMPT_3, flag, PROMPT_4)
        refused = kyo(*line, *args)
        assert refused.code == 2, refused
        assert credits.CONTRACT_CREDIT_TURN_NOT_THE_PERSONS in refused.err
        assert "automatic send from kyo serve" in refused.err
        assert "nothing was written" in refused.err and PROMPT_3 in refused.err
        assert kyo("show", record, "--json").json["credits"] == []
    assert kyo(*line, "--turn", PROMPT_3).code == 0  # the person's own turn


def test_said_does_not_list_the_automated_message(kyo, talked: Path, store_home: Path) -> None:
    assert FIRST_TEXT[:20] not in kyo("turns", "--said").out
    automatic_turn(kyo, talked, store_home)

    listed = kyo("turns", "--said")

    assert listed.code == 0, listed
    assert FIRST_TEXT[:20] not in listed.out and PROMPT_3 in listed.out
    assert [t["turn"] for t in kyo("turns", "--said", "--json").json["said"]] == ["p-1", "p-3"]


def test_a_person_turn_with_the_same_words_and_no_log_row_is_still_the_persons(
    kyo, talked: Path, store_home: Path
) -> None:
    """The mark is the log's. Words that merely look like the message are the person's own
    when no automatic send of them was logged."""
    append_transcript([person("p-4", PROMPT_4, FIRST_TEXT, T4)])
    assert "p-4" in [t["turn"] for t in kyo("turns", "--said", "--json").json["said"]]


def test_the_turns_route_marks_the_automated_turn(bed: Bed, store_home: Path) -> None:
    idle = ready(bed, store_home)
    assert idle.tick() == [OLD]
    bed.append(
        [
            person_record("auto-1", FIRST_TEXT, NOW + timedelta(seconds=2)),
            person_record("real-1", "a message the person typed", NOW + timedelta(minutes=5)),
        ]
    )

    status, _, raw = bed.get(f"{SESSIONS}/{OLD}/turns")
    turns = json.loads(raw)["turns"]
    by_text = {turn["text"]: turn for turn in turns}
    assert by_text[FIRST_TEXT]["automatic"] is True
    assert by_text["a message the person typed"]["automatic"] is False
    assert all(turn["automatic"] is False for turn in turns if turn["text"] != FIRST_TEXT)
    assert status == 200
