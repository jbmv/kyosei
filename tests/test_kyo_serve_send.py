"""KYO-699: `kyo serve`'s send routes, device tokens and the send log (chunk E4's send half).

A fake ``tmux`` on the server's ``PATH`` records every call and answers ``capture-pane`` with a
fixture screen, so a test reads exactly which keys a route sent. Every request goes over a
real socket. Each error that names a remedy is followed in the same test by that remedy,
and the request is repeated.
"""

# ruff: noqa: F811 - a fixture borrowed from another test module is named by its parameters

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
import yaml

import kyosei
from kyosei import device_tokens, home_settings, serve_send

from .conftest import invoke
from .test_kyo_serve import body_json
from .test_kyo_serve_sessions import (  # noqa: F401 - the fixtures, by name
    ENDED,
    OLD,
    SESSIONS,
    Bed,
    bed,
    reaches_send,
    running,
)

SEND = f"{SESSIONS}/{OLD}/send"
SLASH = f"{SESSIONS}/{OLD}/slash"
INTERRUPT = f"{SESSIONS}/{OLD}/interrupt"
FRAME_WORKING = "frame-04.txt"
#: An idle screen with an empty box. ``idle.txt`` was recorded without colour, so its dim
#: suggestion reads as typed text and clearing the box would never finish.
IDLE = "idle-suggestion.reconstructed.txt"

MESSAGE = {"segments": [{"kind": "typed", "text": "hello there"}]}


def enable(store_home: Path, value: object = True) -> None:
    store_home.mkdir(parents=True, exist_ok=True)
    (store_home / home_settings.SETTINGS_FILE).write_text(
        yaml.safe_dump({home_settings.SERVE_SEND_KEY: value}), encoding="utf-8"
    )


def issue(name: str = "ipad", *permissions: str) -> str:
    """A token, by way of the command a person runs."""
    flags = [arg for permission in permissions for arg in ("--permission", permission)]
    result = invoke(["devices", "issue", name, *flags, "--json"])
    assert result.code == 0, result
    return json.loads(result.out)["token"]


def post(bed: Bed, path: str, document: dict, token: str | None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    status, _, body = bed.client.request(
        path, method="POST", headers=headers, body=json.dumps(document).encode("utf-8")
    )
    return status, body_json(body)


def tmux_lines(bed: Bed) -> list[str]:
    if not bed.tmux_log.exists():
        return []
    return bed.tmux_log.read_text(encoding="utf-8").splitlines()


def keys_sent(bed: Bed) -> list[str]:
    words = ("send-keys", "load-buffer", "paste-buffer")
    return [line for line in tmux_lines(bed) if any(word in line for word in words)]


def log_rows(store_home: Path) -> list[dict]:
    path = store_home / serve_send.LOG_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(autouse=True)
def empty_box(request: pytest.FixtureRequest) -> None:
    if "bed" in request.fixturenames:
        request.getfixturevalue("bed").show(IDLE)


@pytest.fixture
def token(store_home: Path) -> str:
    return issue("ipad", device_tokens.PERMISSION_SEND)


@pytest.fixture
def on(store_home: Path, token: str) -> str:
    """Sending enabled, and a token with send permission."""
    enable(store_home)
    return token


# --------------------------------------------------------------------- c1: off until enabled


@pytest.mark.parametrize(
    ("path", "document", "screen"),
    [
        (SEND, MESSAGE, IDLE),
        (SLASH, {"command": "/usage"}, IDLE),
        (INTERRUPT, {}, None),
    ],
)
def test_every_route_refuses_until_the_setting_is_on_and_the_remedy_clears_it(
    bed: Bed, store_home: Path, token: str, path: str, document: dict, screen: str | None
) -> None:
    if screen is None:
        bed.show_frame(FRAME_WORKING)
    else:
        bed.show(screen)

    status, refused = post(bed, path, document, token)

    assert status == 403
    assert refused["setting"] == home_settings.SERVE_SEND_KEY
    assert f"{home_settings.SERVE_SEND_KEY}: true" in refused["error"]
    assert str(store_home / home_settings.SETTINGS_FILE) in refused["error"]
    assert keys_sent(bed) == []

    enable(store_home)  # the remedy the refusal named, with no restart of the server

    status, sent = post(bed, path, document, token)
    assert status == 200, sent
    assert keys_sent(bed)


def test_clearing_the_setting_stops_sending_without_a_restart(
    bed: Bed, store_home: Path, on: str
) -> None:
    assert post(bed, SEND, MESSAGE, on)[0] == 200
    enable(store_home, False)
    assert post(bed, SEND, MESSAGE, on)[0] == 403


def test_a_malformed_setting_refuses_by_name_and_correcting_it_clears_it(
    bed: Bed, store_home: Path, token: str
) -> None:
    enable(store_home, "yes")
    status, refused = post(bed, SEND, MESSAGE, token)
    assert status == 500 and home_settings.SERVE_SEND_KEY in refused["error"]
    assert keys_sent(bed) == []

    enable(store_home, True)
    assert post(bed, SEND, MESSAGE, token)[0] == 200


def test_a_send_route_answers_post_only(bed: Bed, on: str) -> None:
    status, headers, _ = bed.get(SEND)
    assert status == 405 and headers["Allow"] == "POST"


def test_a_query_string_is_refused_so_a_token_cannot_reach_the_request_log(
    bed: Bed, on: str
) -> None:
    status, refused = post(bed, f"{SEND}?token={on}", MESSAGE, on)
    assert status == 400 and "Authorization" in refused["error"]
    assert keys_sent(bed) == []


# --------------------------------------------------------------------------- c2: the tokens


def test_a_token_is_printed_once_and_only_its_digest_is_stored(store_home: Path) -> None:
    made = invoke(["devices", "issue", "ipad", "--permission", "send", "--json"])
    assert made.code == 0, made
    document = json.loads(made.out)
    token = document["token"]
    assert token.startswith(device_tokens.TOKEN_PREFIX) and "digest" not in document["device"]

    path = store_home / device_tokens.TOKENS_FILE
    text = path.read_text(encoding="utf-8")
    assert token not in text
    assert device_tokens.token_digest(token) in text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    listed = invoke(["devices", "list", "--json"])
    assert listed.code == 0, listed
    assert token not in listed.out and "sha256:" not in listed.out
    (row,) = json.loads(listed.out)["devices"]
    assert row["name"] == "ipad" and row["permissions"] == ["send"]
    assert row["revoked_at"] is None


def test_the_shape_is_a_list_of_permissions_and_a_token_may_hold_none(store_home: Path) -> None:
    issue("reader")
    (device,) = device_tokens.read_devices(store_home / device_tokens.TOKENS_FILE)
    assert device.permissions == ()
    assert isinstance(device.stored()["permissions"], list)


def test_a_send_with_no_token_is_refused_and_the_remedy_clears_it(bed: Bed, store_home) -> None:
    enable(store_home)
    status, refused = post(bed, SEND, MESSAGE, None)
    assert status == 401 and refused["reason"] == "no-token"
    assert "kyo devices issue" in refused["error"]
    assert keys_sent(bed) == []

    token = issue("ipad", "send")
    assert post(bed, SEND, MESSAGE, token)[0] == 200


def test_a_refusal_for_a_missing_token_asks_for_bearer(bed: Bed, store_home: Path) -> None:
    enable(store_home)
    status, headers, _ = bed.client.request(
        SEND,
        method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps(MESSAGE).encode("utf-8"),
    )
    assert status == 401 and headers["WWW-Authenticate"] == "Bearer"


def test_a_send_with_an_unknown_token_is_refused_and_the_remedy_clears_it(
    bed: Bed, store_home: Path
) -> None:
    enable(store_home)
    status, refused = post(bed, SEND, MESSAGE, "kyo_not-a-token")
    assert status == 401 and refused["reason"] == "unknown-token"
    assert keys_sent(bed) == []

    assert post(bed, SEND, MESSAGE, issue("ipad", "send"))[0] == 200


def test_a_revoked_token_is_refused_at_once_and_the_remedy_clears_it(
    bed: Bed, store_home: Path, on: str
) -> None:
    assert post(bed, SEND, MESSAGE, on)[0] == 200

    done = invoke(["devices", "revoke", "ipad", "--json"])
    assert done.code == 0, done
    assert json.loads(done.out)["revoked"] is True

    before = len(keys_sent(bed))
    status, refused = post(bed, SEND, MESSAGE, on)
    assert status == 401 and refused["reason"] == "revoked-token"
    assert len(keys_sent(bed)) == before
    assert log_rows(store_home)[-1]["device"]["name"] == "ipad"

    # The name is free again once its token is revoked.
    assert post(bed, SEND, MESSAGE, issue("ipad", "send"))[0] == 200


def test_a_token_without_send_permission_is_refused_and_the_remedy_clears_it(
    bed: Bed, store_home: Path
) -> None:
    enable(store_home)
    bare = issue("ipad")
    status, refused = post(bed, SEND, MESSAGE, bare)
    assert status == 403 and refused["reason"] == "no-send-permission"
    assert "--permission send" in refused["error"]
    assert "kyo devices grant ipad --permission send" in refused["error"]  # KYO-747.c3
    assert keys_sent(bed) == []

    # The remedy it names runs, and the token the device holds works as it was.
    assert invoke(["devices", "grant", "ipad", "--permission", "send"]).code == 0
    assert post(bed, SEND, MESSAGE, bare)[0] == 200


def test_an_unreadable_token_file_refuses_every_token_and_replacing_it_clears_it(
    bed: Bed, store_home: Path, on: str
) -> None:
    path = store_home / device_tokens.TOKENS_FILE
    good = path.read_text(encoding="utf-8")
    path.write_text("not json", encoding="utf-8")
    status, refused = post(bed, SEND, MESSAGE, on)
    assert status == 500 and str(path) in refused["error"]
    assert keys_sent(bed) == []

    path.write_text(good, encoding="utf-8")
    assert post(bed, SEND, MESSAGE, on)[0] == 200


def test_a_second_live_device_of_one_name_is_refused_and_revoking_clears_it(
    store_home: Path,
) -> None:
    issue("ipad", "send")
    again = invoke(["devices", "issue", "ipad"])
    assert again.code != 0 and "kyo devices revoke ipad" in again.err
    assert invoke(["devices", "revoke", "ipad"]).code == 0
    assert invoke(["devices", "issue", "ipad"]).code == 0


def test_revoking_what_is_not_there_names_the_listing(store_home: Path) -> None:
    missing = invoke(["devices", "revoke", "phone"])
    assert missing.code != 0 and "kyo devices list" in missing.err
    assert invoke(["devices", "list"]).code == 0


def test_list_takes_no_argument_and_the_bare_form_clears_it(store_home: Path) -> None:
    refused = invoke(["devices", "list", "extra"])
    assert refused.code != 0 and "kyo devices list" in refused.err
    assert invoke(["devices", "list"]).code == 0


# ------------------------------------------------------------------------------ KYO-747


def log_changes(store_home: Path) -> list[dict]:
    path = store_home / device_tokens.CHANGE_LOG_FILE
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_token_only_prints_the_token_alone_with_no_trailing_newline(store_home: Path) -> None:
    plain = invoke(["devices", "issue", "ipad", "--permission", "send"])
    assert plain.code == 0
    assert "--token-only" in plain.out.splitlines()[-1]
    alone = invoke(["devices", "issue", "phone", "--token-only"])
    assert alone.code == 0 and alone.out.startswith("kyo_")
    assert "\n" not in alone.out and " " not in alone.out
    token = alone.out
    digest = device_tokens.token_digest(token)
    devices = device_tokens.read_devices(device_tokens.tokens_path({"KYOSEI_HOME": str(store_home)}))
    assert [d.name for d in devices if d.digest == digest] == ["phone"]
    assert invoke(["devices", "revoke", "ipad", "--token-only"]).code != 0
    assert invoke(["devices", "revoke", "ipad"]).code == 0, "dropping the flag clears it"


def test_grant_adds_a_permission_to_a_live_token_without_changing_it_and_is_logged(
    bed: Bed, store_home: Path
) -> None:
    enable(store_home)
    token = issue("ipad", "send")
    before = (store_home / device_tokens.TOKENS_FILE).read_text(encoding="utf-8")
    done = invoke(["devices", "grant", "ipad", "--permission", "files", "--json"])
    assert done.code == 0, done
    document = json.loads(done.out)
    assert document["changed"] == ["files"]
    assert document["device"]["permissions"] == ["files", "send"]
    after = json.loads((store_home / device_tokens.TOKENS_FILE).read_text(encoding="utf-8"))
    assert after["devices"][0]["digest"] == json.loads(before)["devices"][0]["digest"]
    assert post(bed, SEND, MESSAGE, token)[0] == 200, "the same token still works"
    [row] = log_changes(store_home)
    assert row["action"] == "grant" and row["permissions"] == ["files"] and row["name"] == "ipad"
    assert token not in json.dumps(row)


def test_revoke_with_a_permission_removes_it_and_leaves_the_token_live(
    bed: Bed, store_home: Path
) -> None:
    enable(store_home)
    token = issue("ipad", "send", "notes")
    done = invoke(["devices", "revoke", "ipad", "--permission", "send", "--json"])
    assert done.code == 0, done
    assert json.loads(done.out)["device"]["permissions"] == ["notes"]
    status, refused = post(bed, SEND, MESSAGE, token)
    assert status == 403 and refused["reason"] == "no-send-permission"
    missing = invoke(["devices", "revoke", "ipad", "--permission", "send"])
    assert missing.code != 0 and "does not carry send" in missing.err
    assert [r["action"] for r in log_changes(store_home)] == ["revoke-permission"]
    assert invoke(["devices", "revoke", "ipad"]).code == 0, "bare revoke still revokes"


def test_grant_and_permission_revoke_refuse_a_revoked_device_naming_issue(
    store_home: Path,
) -> None:
    issue("ipad", "send")
    assert invoke(["devices", "revoke", "ipad"]).code == 0
    for args in (
        ["devices", "grant", "ipad", "--permission", "files"],
        ["devices", "revoke", "ipad", "--permission", "send"],
    ):
        refused = invoke(args)
        assert refused.code != 0 and "kyo devices issue ipad --permission" in refused.err
    assert invoke(["devices", "issue", "ipad", "--permission", "files"]).code == 0
    assert invoke(["devices", "grant", "ipad", "--permission", "send"]).code == 0


def test_grant_needs_a_permission_and_naming_one_clears_it(store_home: Path) -> None:
    issue("ipad")
    refused = invoke(["devices", "grant", "ipad"])
    assert refused.code != 0 and "kyo devices grant ipad --permission" in refused.err
    assert invoke(["devices", "grant", "ipad", "--permission", "send"]).code == 0


def test_a_bad_device_name_is_refused_and_a_valid_one_clears_it(store_home: Path) -> None:
    bad = invoke(["devices", "issue", "my ipad"])
    assert bad.code != 0 and "device name" in bad.err
    assert invoke(["devices", "issue", "my-ipad"]).code == 0


def test_issue_and_revoke_need_a_device_and_naming_one_clears_it(store_home: Path) -> None:
    for action in ("issue", "revoke", "grant"):
        missing = invoke(["devices", action])
        assert missing.code != 0 and f"kyo devices {action}" in missing.err
    assert invoke(["devices", "issue", "my-ipad", "--permission", "send"]).code == 0
    assert invoke(["devices", "revoke", "my-ipad"]).code == 0


def test_the_command_is_not_served(bed: Bed, on: str) -> None:
    status, _, body = bed.get("/v1/projects/proj/cmd/devices/list")
    assert status == 403 and b"this machine only" in body


# --------------------------------------------------------------------------- c3: a message


def test_a_message_goes_as_ordered_typed_and_pasted_segments(bed: Bed, on: str) -> None:
    document = {
        "segments": [
            {"kind": "typed", "text": "before"},
            {"kind": "pasted", "text": "the pasted\nlines"},
            {"kind": "typed", "text": "after\nmore"},
        ]
    }
    status, sent = post(bed, SEND, document, on)
    assert status == 200, sent
    keys = keys_sent(bed)
    assert [line.split(" ", 2)[2] for line in keys] == [
        "send-keys -t %1 -l -- before",
        "load-buffer -b kyosei-paste-1 -",
        "paste-buffer -p -d -b kyosei-paste-1 -t %1",
        "send-keys -t %1 -l -- after",
        "send-keys -t %1 M-Enter",
        "send-keys -t %1 -l -- more",
        "send-keys -t %1 Enter",
    ]


def test_a_message_is_sent_to_a_working_screen(bed: Bed, on: str) -> None:
    bed.show_frame(FRAME_WORKING)
    assert post(bed, SEND, MESSAGE, on)[0] == 200
    assert keys_sent(bed)


@pytest.mark.parametrize(
    "screen",
    [
        "permission-bash.txt",
        "ask.txt",
        "usage.txt",
        "trust-dialog.synthetic.txt",
        "unrecognised-help.txt",
    ],
)
def test_a_message_is_refused_on_any_other_screen(bed: Bed, on: str, screen: str) -> None:
    bed.show(screen)
    status, refused = post(bed, SEND, MESSAGE, on)
    assert status == 409 and refused["reason"] == "screen-not-ready"
    assert keys_sent(bed) == []


def test_a_session_with_no_pane_is_refused(bed: Bed, on: str) -> None:
    registry = bed.claude / "sessions" / f"{os.getpid()}.json"
    registry.write_text(json.dumps({"pid": os.getpid(), "sessionId": ENDED}), encoding="utf-8")
    status, refused = post(bed, SEND, MESSAGE, on)
    assert status == 409 and refused["reason"] == "no-pane"
    assert keys_sent(bed) == []


def test_a_session_this_project_does_not_record_is_refused(bed: Bed, on: str) -> None:
    other = "55555555-5555-4555-8555-555555555555"
    status, refused = post(bed, f"{SESSIONS}/{other}/send", MESSAGE, on)
    assert status == 404 and "not recorded" in refused["error"]
    assert keys_sent(bed) == []


def test_a_project_not_opted_in_is_refused(bed: Bed, on: str, project: Path) -> None:
    path = project / "kyosei" / "config.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["read_transcripts"] = False
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    status, refused = post(bed, SEND, MESSAGE, on)
    assert status == 403 and refused["setting"] == "read_transcripts"
    assert keys_sent(bed) == []


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"segments": []},
        {"segments": [{"kind": "typed"}]},
        {"segments": [{"kind": "spoken", "text": "x"}]},
        {"segments": [{"kind": "typed", "text": ""}]},
        {"segments": [{"kind": "typed", "text": "   "}]},
        {"segments": [{"kind": "typed", "text": "x"}], "automatic": "yes"},
    ],
)
def test_a_malformed_message_is_refused(bed: Bed, on: str, document: dict) -> None:
    status, refused = post(bed, SEND, document, on)
    assert status == 400 and refused["reason"] == "bad-request"
    assert keys_sent(bed) == []


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        ("typed", "stop\x1b[201~now"),
        ("typed", "line\rsubmit"),
        ("typed", "a\ttab"),
        ("pasted", "paste\x1bout"),
        ("pasted", "c1\x9bcontrol"),
    ],
)
def test_a_control_character_that_would_act_as_a_key_is_refused(
    bed: Bed, on: str, kind: str, text: str
) -> None:
    status, refused = post(bed, SEND, {"segments": [{"kind": kind, "text": text}]}, on)
    assert status == 400 and "control character" in refused["error"]
    assert keys_sent(bed) == []


def test_a_tab_is_accepted_in_a_paste(bed: Bed, on: str) -> None:
    document = {"segments": [{"kind": "pasted", "text": "a\tb"}]}
    assert post(bed, SEND, document, on)[0] == 200


def test_a_message_shaped_like_a_command_is_refused_and_the_slash_route_clears_it(
    bed: Bed, on: str
) -> None:
    command = {"segments": [{"kind": "typed", "text": "/usage"}]}
    status, refused = post(bed, SEND, command, on)
    assert status == 400 and refused["reason"] == "message-is-a-command"
    assert keys_sent(bed) == []

    assert post(bed, SLASH, {"command": "/usage"}, on)[0] == 200


def test_a_message_beginning_with_a_file_path_is_not_a_command(bed: Bed, on: str) -> None:
    document = {"segments": [{"kind": "typed", "text": "/Users/me/x.py is failing"}]}
    assert post(bed, SEND, document, on)[0] == 200


# ------------------------------------------------------------- c4: a command and an interrupt


def test_a_slash_command_is_sent_on_an_idle_screen(bed: Bed, on: str) -> None:
    status, sent = post(bed, SLASH, {"command": "/usage"}, on)
    assert status == 200, sent
    assert [line.split(" ", 2)[2] for line in keys_sent(bed)] == [
        "send-keys -t %1 -l -- /usage",
        "send-keys -t %1 Enter",
    ]


def test_a_slash_command_is_refused_on_a_working_screen(bed: Bed, on: str) -> None:
    bed.show_frame(FRAME_WORKING)
    status, refused = post(bed, SLASH, {"command": "/usage"}, on)
    assert status == 409 and refused["reason"] == "screen-not-ready"
    assert keys_sent(bed) == []


@pytest.mark.parametrize("command", ["usage", "/a\nb", "/a\x1b", 7])
def test_a_malformed_command_is_refused(bed: Bed, on: str, command: object) -> None:
    status, refused = post(bed, SLASH, {"command": command}, on)
    assert status == 400 and refused["reason"] == "bad-request"
    assert keys_sent(bed) == []


def test_an_interrupt_presses_escape_on_a_working_screen(bed: Bed, on: str) -> None:
    bed.show_frame(FRAME_WORKING)
    status, sent = post(bed, INTERRUPT, {}, on)
    assert status == 200, sent
    assert [line.split(" ", 2)[2] for line in keys_sent(bed)] == ["send-keys -t %1 Escape"]


@pytest.mark.parametrize("screen", [IDLE, "permission-bash.txt", "usage.txt"])
def test_an_interrupt_is_refused_on_any_other_screen(bed: Bed, on: str, screen: str) -> None:
    bed.show(screen)
    status, refused = post(bed, INTERRUPT, {}, on)
    assert status == 409 and refused["reason"] == "screen-not-ready"
    assert keys_sent(bed) == []


# --------------------------------------------------------------------------------- c5: the log


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_a_send_is_logged_with_its_segments_and_never_their_text(
    bed: Bed, store_home: Path, on: str
) -> None:
    secret_typed = "the words typed by hand"
    secret_pasted = "the words pasted in\nfrom somewhere"
    document = {
        "segments": [
            {"kind": "typed", "text": secret_typed},
            {"kind": "pasted", "text": secret_pasted},
        ]
    }
    status, sent = post(bed, SEND, document, on)
    assert status == 200, sent

    path = store_home / serve_send.LOG_FILE
    raw = path.read_text(encoding="utf-8")
    assert secret_typed not in raw and "pasted in" not in raw
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    (row,) = log_rows(store_home)
    assert row["outcome"] == "sent" and row["status"] == 200 and row["reason"] is None
    assert row["action"] == "send" and row["session_id"] == OLD
    assert row["at"].endswith("Z")
    assert row["device"]["name"] == "ipad" and row["device"]["id"]
    assert row["automatic"] is False
    assert row["segments"] == [
        {"kind": "typed", "length": len(secret_typed), "digest": _digest(secret_typed)},
        {"kind": "pasted", "length": len(secret_pasted), "digest": _digest(secret_pasted)},
    ]
    assert sent["segments"] == row["segments"]


def test_an_automatic_send_is_marked(bed: Bed, store_home: Path, on: str) -> None:
    assert post(bed, SEND, {**MESSAGE, "automatic": True}, on)[0] == 200
    (row,) = log_rows(store_home)
    assert row["automatic"] is True


def test_a_slash_command_is_logged_by_digest_alone(bed: Bed, store_home: Path, on: str) -> None:
    assert post(bed, SLASH, {"command": "/model haiku"}, on)[0] == 200
    (row,) = log_rows(store_home)
    assert row["action"] == "slash"
    assert row["segments"] == [
        {"kind": "command", "length": 12, "digest": _digest("/model haiku")}
    ]
    assert "haiku" not in (store_home / serve_send.LOG_FILE).read_text(encoding="utf-8")


def test_an_interrupt_is_logged_with_no_segments(bed: Bed, store_home: Path, on: str) -> None:
    bed.show_frame(FRAME_WORKING)
    assert post(bed, INTERRUPT, {}, on)[0] == 200
    (row,) = log_rows(store_home)
    assert row["action"] == "interrupt" and row["segments"] == [] and row["outcome"] == "sent"


def test_a_refused_send_logs_its_refusal_with_its_segments(
    bed: Bed, store_home: Path, on: str
) -> None:
    bed.show("permission-bash.txt")
    status, _ = post(bed, SEND, MESSAGE, on)
    assert status == 409
    (row,) = log_rows(store_home)
    assert row["outcome"] == "refused" and row["status"] == 409
    assert row["reason"] == "screen-not-ready"
    assert row["device"]["name"] == "ipad"
    assert row["segments"] == [
        {"kind": "typed", "length": 11, "digest": _digest("hello there")}
    ]


@pytest.mark.parametrize(
    ("enabled", "presented", "status", "reason"),
    [
        (False, "valid", 403, "send-disabled"),
        (True, None, 401, "no-token"),
        (True, "kyo_unknown", 401, "unknown-token"),
    ],
)
def test_a_request_refused_before_its_token_is_accepted_is_logged_without_a_device(
    bed: Bed,
    store_home: Path,
    token: str,
    enabled: bool,
    presented: str | None,
    status: int,
    reason: str,
) -> None:
    if enabled:
        enable(store_home)
    used = token if presented == "valid" else presented
    assert post(bed, SEND, MESSAGE, used)[0] == status
    (row,) = log_rows(store_home)
    assert row["outcome"] == "refused" and row["reason"] == reason and row["status"] == status
    assert row["device"] is None and row["segments"] is None
    raw = (store_home / serve_send.LOG_FILE).read_text(encoding="utf-8")
    assert "hello there" not in raw and token not in raw


def test_a_token_refused_for_permission_is_logged_with_its_device(
    bed: Bed, store_home: Path
) -> None:
    enable(store_home)
    assert post(bed, SEND, MESSAGE, issue("ipad"))[0] == 403
    (row,) = log_rows(store_home)
    # The token names its device, and no body is parsed until the token is accepted.
    assert row["reason"] == "no-send-permission" and row["device"]["name"] == "ipad"
    assert row["segments"] is None


def test_a_log_that_would_lie_in_a_repository_refuses_the_send_and_moving_it_clears_it(
    bed: Bed, store_home: Path, on: str, tmp_path: Path
) -> None:
    repository = tmp_path / "a-repo"
    (repository / ".git").mkdir(parents=True)
    moved = repository / "home"
    moved.mkdir()
    shutil_copy_settings(store_home, moved)

    bed.server.environ["KYOSEI_HOME"] = str(moved)
    status, refused = post(bed, SEND, MESSAGE, on)
    assert status == 500 and refused["reason"] == "send-log-unusable"
    assert str(repository.resolve()) in refused["error"]
    assert keys_sent(bed) == []
    assert not (moved / serve_send.LOG_FILE).exists()

    bed.server.environ["KYOSEI_HOME"] = str(store_home)  # the remedy: a home outside any repository
    assert post(bed, SEND, MESSAGE, on)[0] == 200


def shutil_copy_settings(source: Path, target: Path) -> None:
    name = home_settings.SETTINGS_FILE
    (target / name).write_text((source / name).read_text(encoding="utf-8"), encoding="utf-8")


def test_an_unusable_log_stops_the_send_before_any_key(
    bed: Bed, store_home: Path, on: str
) -> None:
    (store_home / serve_send.LOG_FILE).mkdir()
    status, refused = post(bed, SEND, MESSAGE, on)
    assert status == 500 and refused["reason"] == "send-log-unusable"
    assert keys_sent(bed) == []


# ---------------------------------------------------------------------------- c6: the one route


def test_only_the_send_module_can_reach_the_sending_functions() -> None:
    package = Path(kyosei.__file__).parent
    # device_tokens.py is read by the server and is outside the serve* names.
    modules = [*sorted(package.glob("serve*.py")), package / "device_tokens.py"]
    assert {
        "serve.py",
        "serve_events.py",
        "serve_files.py",
        "serve_sessions.py",
        "serve_send.py",
        "serve_held.py",
        "serve_drafts.py",
        "device_tokens.py",
    } <= {p.name for p in modules}
    offenders = {
        path.name: found
        for path in modules
        if (found := reaches_send(path.read_text(encoding="utf-8"), path.name))
        # serve_terminal.py is the deliberate exception (KYO-721): it types what a person
        # types, through a client of its own, and tests/test_kyo_serve_terminal.py pins
        # what it reaches.
        and path.name not in ("serve_send.py", "serve_terminal.py")
    }
    assert offenders == {}, f"only kyosei.serve_send may reach the sending half: {offenders}"


def test_the_send_module_reaches_only_the_session_layers_three_methods() -> None:
    path = Path(kyosei.__file__).parent / "serve_send.py"
    found = reaches_send(path.read_text(encoding="utf-8"), path.name)
    assert sorted(entry.split(": ", 1)[1] for entry in found) == [
        ".send_interrupt",
        ".send_message",
        ".send_slash_command",
    ]
