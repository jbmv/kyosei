"""KYO-974: a session whose Claude Code is logged out is known to be, and nothing else is.

Two readings, each pinned here against the shapes measured on this machine: the transcript's
synthetic reply for a refused request (``error: "authentication_failed"``) and what
``claude auth status --json`` prints. The served half, the field in a session's facts, is in
``tests/test_kyo_serve_sessions.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kyosei import session_layer
from kyosei.harness import claude_code_auth as auth
from kyosei.harness import claude_code_transcript as transcript

NOW = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
TIME_LIMIT = "Login reached its time limit · Please run /login"
SESSION = "55555555-5555-4555-8555-555555555555"


def _row(number: int, kind: str, **fields) -> dict:
    return {
        "isSidechain": False,
        "sessionId": SESSION,
        "parentUuid": f"00000000-0000-4000-8000-{number - 1:012d}" if number > 1 else None,
        "uuid": f"00000000-0000-4000-8000-{number:012d}",
        "timestamp": f"2026-11-20T14:{number:02d}:00.000Z",
        "type": kind,
        **fields,
    }


def prompt(number: int, text: str = "Go on.") -> dict:
    return _row(number, "user", origin={"kind": "human"}, message={"role": "user", "content": text})


def reply(number: int, text: str = "Done.") -> dict:
    usage = {
        "input_tokens": 5,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": 20,
        "output_tokens": 9,
    }
    message = {
        "id": f"msg_{number}",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "usage": usage,
    }
    return _row(number, "assistant", message=message)


def refused(number: int, text: str = TIME_LIMIT) -> dict:
    """The row 2.1.327 wrote for a prompt its login was refused for, with its fields kept."""
    zero = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    message = {
        "id": f"8a74bb0f-0000-4000-8000-{number:012d}",
        "type": "message",
        "role": "assistant",
        "model": "<synthetic>",
        "stop_reason": "stop_sequence",
        "content": [{"type": "text", "text": text}],
        "usage": zero,
    }
    return _row(
        number,
        "assistant",
        message=message,
        error="authentication_failed",
        isApiErrorMessage=True,
        failedResponseHadNoToolCall=True,
    )


def other_error(number: int) -> dict:
    row = refused(number, "API Error: Server error mid-response.")
    row["error"] = "server_error"
    return row


def login(number: int, printed: str = "Login successful", *, system: bool) -> list[dict]:
    """``/login`` and its output: ``system`` rows to 2.1.327, ``user`` rows in 2.1.329."""
    command = (
        "<command-name>/login</command-name>\n"
        "            <command-message>login</command-message>\n"
        "            <command-args></command-args>"
    )
    output = f"<local-command-stdout>{printed}</local-command-stdout>"
    if system:
        return [
            _row(number, "system", subtype="local_command", content=command, level="info"),
            _row(number + 1, "system", subtype="local_command", content=output, level="info"),
        ]
    return [
        _row(number, "user", message={"role": "user", "content": command}),
        _row(number + 1, "user", message={"role": "user", "content": output}),
    ]


def write(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def read(tmp_path: Path, rows: list[dict]) -> transcript.SessionTranscript:
    reader = transcript.SessionTranscript(write(tmp_path / f"{SESSION}.jsonl", rows))
    reader.read()
    return reader


# ------------------------------------------------------------------ the transcript's reading


def test_a_refused_request_is_the_latest_refusal_with_claude_codes_words(tmp_path: Path) -> None:
    reader = read(tmp_path, [prompt(1), reply(2), prompt(3), refused(4)])
    found = reader.latest_login_refusal
    assert found == transcript.LoginRefusal("2026-11-20T14:04:00.000Z", TIME_LIMIT)
    # The prompt the refusal answered is still the person's turn.
    assert [turn.full_text for turn in reader.turns] == ["Go on.", "Go on."]


@pytest.mark.parametrize("system", [True, False], ids=["system-row", "user-row"])
def test_a_login_that_worked_ends_the_refusal(tmp_path: Path, system: bool) -> None:
    reader = read(tmp_path, [prompt(1), refused(2), *login(3, system=system)])
    assert reader.latest_login_refusal is None


def test_a_login_that_did_not_work_leaves_it(tmp_path: Path) -> None:
    reader = read(tmp_path, [prompt(1), refused(2), *login(3, "Login interrupted", system=True)])
    assert reader.latest_login_refusal is not None


def test_a_reply_the_model_wrote_ends_the_refusal_and_another_error_does_not(
    tmp_path: Path,
) -> None:
    assert read(tmp_path, [prompt(1), refused(2), prompt(3), reply(4)]).latest_login_refusal is None
    assert read(tmp_path, [prompt(1), refused(2), other_error(3)]).latest_login_refusal is not None
    # A later prompt alone says nothing about the login.
    assert read(tmp_path, [prompt(1), refused(2), prompt(3)]).latest_login_refusal is not None


def test_a_transcript_with_no_refusal_has_none(tmp_path: Path) -> None:
    assert read(tmp_path, [prompt(1), reply(2), other_error(3)]).latest_login_refusal is None


def test_the_refusal_is_read_across_incremental_reads(tmp_path: Path) -> None:
    path = write(tmp_path / f"{SESSION}.jsonl", [prompt(1), reply(2)])
    reader = transcript.SessionTranscript(path)
    reader.read()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(refused(3)) + "\n")
    reader.read()
    assert reader.latest_login_refusal is not None


# --------------------------------------------------------------- `claude auth status --json`

#: The document 2.1.329 printed for this machine's login, with its account fields removed.
LOGGED_IN_DOCUMENT = {
    "loggedIn": True,
    "authMethod": "claude.ai",
    "apiProvider": "firstParty",
    "analyticsDisabled": False,
    "projectsDirectory": "{config}/projects",
    "configDirectory": "{config}",
    "loginExpiresAt": "2026-12-17T20:18:12.909Z",
    "consoleProfileSignInOffered": True,
    "subscriptionType": "max",
    "storedClaudeAiLogin": True,
}
#: The document it printed, exiting 1, for a configuration directory holding no login.
NO_LOGIN_DOCUMENT = {
    "loggedIn": False,
    "authMethod": "none",
    "apiProvider": "firstParty",
    "analyticsDisabled": False,
    "projectsDirectory": "{config}/projects",
    "configDirectory": "{config}",
    "loginExpiresAt": None,
    "consoleProfileSignInOffered": True,
}


def document(template: dict, config: Path, **changes) -> str:
    filled = {
        key: value.replace("{config}", str(config)) if isinstance(value, str) else value
        for key, value in template.items()
    }
    return json.dumps({**filled, **changes})


def judged(text: str, config: Path, now: datetime = NOW) -> auth.Judgement:
    return auth.judge(auth.parse(text), config, now)


def test_a_configuration_with_no_login_is_logged_out(tmp_path: Path) -> None:
    assert judged(document(NO_LOGIN_DOCUMENT, tmp_path), tmp_path) == auth.Judgement(
        auth.LOGGED_OUT, auth.NOT_LOGGED_IN
    )


def test_a_login_past_its_expiry_is_logged_out(tmp_path: Path) -> None:
    text = document(LOGGED_IN_DOCUMENT, tmp_path)
    after = datetime(2026, 12, 17, 20, 18, 13, tzinfo=UTC)
    assert judged(text, tmp_path, after) == auth.Judgement(auth.LOGGED_OUT, auth.SIGN_IN_ENDED)


def test_a_login_more_than_three_days_from_its_expiry_stands(tmp_path: Path) -> None:
    assert judged(document(LOGGED_IN_DOCUMENT, tmp_path), tmp_path).state == auth.LOGGED_IN


def test_what_cannot_be_told_apart_is_unknown(tmp_path: Path) -> None:
    """KYO-974.c5: none of these is a login known to have ended, so none stops a send."""
    expiry = datetime(2026, 12, 17, 20, 18, 12, tzinfo=UTC)
    cases = {
        # The release says a sign-in can end at any time in its last three days.
        "final days": (document(LOGGED_IN_DOCUMENT, tmp_path), expiry - timedelta(days=2)),
        "no expiry": (document(LOGGED_IN_DOCUMENT, tmp_path, loginExpiresAt=None), NOW),
        "another directory": (document(NO_LOGIN_DOCUMENT, tmp_path / "elsewhere"), NOW),
        "another provider": (document(NO_LOGIN_DOCUMENT, tmp_path, apiProvider="bedrock"), NOW),
        "no loggedIn": (
            json.dumps({"configDirectory": str(tmp_path), "apiProvider": "firstParty"}),
            NOW,
        ),
        "not the document": ("Not logged in. Run claude auth login to authenticate.", NOW),
    }
    for name, (text, now) in cases.items():
        assert judged(text, tmp_path, now).state == auth.UNKNOWN, name
    assert auth.judge(None, tmp_path, NOW).state == auth.UNKNOWN


def fake_claude(tmp_path: Path, printed: str, status: int) -> Path:
    """A `claude` that prints ``printed`` and exits ``status``, and counts its calls."""
    out = tmp_path / "printed.json"
    out.write_text(printed, encoding="utf-8")
    script = tmp_path / "claude"
    script.write_text(
        f'#!/bin/sh\necho "$*" >> "{tmp_path / "calls.log"}"\ncat "{out}"\nexit {status}\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


#: What the fake script's `cat` needs; `find_claude` is replaced, so it finds no `claude` there.
SHELL = {"PATH": "/usr/bin:/bin"}


def calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "calls.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_read_status_asks_claude_once_a_minute_and_reads_exit_status_1(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "config"
    script = fake_claude(tmp_path, document(NO_LOGIN_DOCUMENT, config), 1)
    monkeypatch.setattr(auth, "find_claude", lambda _environ: str(script))
    first = auth.read_status(SHELL)
    assert first is not None and first.logged_in is False
    assert auth.read_status(SHELL) == first
    assert calls(tmp_path) == ["auth status --json"]
    auth.forget()
    auth.read_status(SHELL)
    assert len(calls(tmp_path)) == 2


def test_read_status_is_none_without_claude_or_with_another_failure(
    tmp_path: Path, monkeypatch
) -> None:
    assert auth.read_status({"PATH": ""}) is None  # the suite's resolver finds none
    script = fake_claude(tmp_path, document(NO_LOGIN_DOCUMENT, tmp_path), 2)
    monkeypatch.setattr(auth, "find_claude", lambda _environ: str(script))
    assert auth.read_status(SHELL) is None


# ------------------------------------------------------------------ the two readings together


def live(tmp_path: Path, rows: list[dict]) -> session_layer.LiveSession:
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    path = write(tmp_path / f"{SESSION}.jsonl", rows)
    made = session_layer.LiveSession(
        SESSION, path, config_dir=config, environ={"PATH": ""}, log_dir=tmp_path / "log"
    )
    made.transcript.read()
    return made


def with_status(monkeypatch, tmp_path: Path, printed: str | None) -> None:
    status = None if printed is None else auth.parse(printed)
    monkeypatch.setattr(auth, "read_status", lambda _environ: status)


def test_without_either_reading_a_session_is_not_logged_out(tmp_path: Path) -> None:
    """KYO-974.c5: no `claude` and no refusal is the default, logged in."""
    session = live(tmp_path, [prompt(1), reply(2)])
    assert session.logged_out(NOW) is None
    assert session.facts().logged_out is None


def test_a_refusal_with_no_status_is_logged_out_in_claude_codes_words(tmp_path: Path) -> None:
    session = live(tmp_path, [prompt(1), reply(2), prompt(3), refused(4)])
    assert session.logged_out(NOW) == {
        "source": "transcript",
        "reason": "refused",
        "at": "2026-11-20T14:04:00.000Z",
        "text": TIME_LIMIT,
    }
    assert session.facts().logged_out == session.logged_out(NOW)


def test_an_ended_login_is_logged_out_before_any_request_fails(tmp_path: Path, monkeypatch) -> None:
    session = live(tmp_path, [prompt(1), reply(2)])
    with_status(monkeypatch, tmp_path, document(NO_LOGIN_DOCUMENT, tmp_path / "config"))
    assert session.logged_out(NOW) == {
        "source": "auth-status",
        "reason": "not-logged-in",
        "at": None,
        "text": None,
    }


def test_a_login_that_stands_sets_an_older_refusal_aside(tmp_path: Path, monkeypatch) -> None:
    """Logged in again in another session: this one's transcript still ends on its refusal."""
    session = live(tmp_path, [prompt(1), reply(2), prompt(3), refused(4)])
    with_status(monkeypatch, tmp_path, document(LOGGED_IN_DOCUMENT, tmp_path / "config"))
    assert session.logged_out(NOW) is None


def test_a_status_in_the_final_days_leaves_the_transcript_to_decide(
    tmp_path: Path, monkeypatch
) -> None:
    near = datetime(2026, 12, 16, tzinfo=UTC)
    with_status(monkeypatch, tmp_path, document(LOGGED_IN_DOCUMENT, tmp_path / "config"))
    assert live(tmp_path, [prompt(1), reply(2)]).logged_out(near) is None
    refused_session = live(tmp_path, [prompt(1), reply(2), prompt(3), refused(4)])
    assert refused_session.logged_out(near)["source"] == "transcript"
