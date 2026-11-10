"""KYO-719 (decision KYO-718): `kyo serve` starts an orchestrator session on request.

Every request goes over a real socket to a server whose environment names a PRIVATE tmux
server (``KYOSEI_CONTROLLER_SOCKET``), never ``-L kyosei``: the fixtures are
``tests/test_kyo_controller.py``'s, with its stand-in ``claude`` and its ``/bin/sh -c`` login
shell. Each error that names a remedy is followed in the same test by that remedy, and the
request is repeated.
"""

# ruff: noqa: F811 - a fixture borrowed from another test module is named by its parameters

from __future__ import annotations

import ast
import json
import threading
from pathlib import Path

import pytest

from kyosei import cli, cmd_controller, device_tokens, serve, serve_orchestrator, serve_send

from .conftest import invoke
from .test_kyo_controller import (  # noqa: F401 - the fixtures, by name
    MEASURED_TRUST_SCREEN,
    bin_dir,
    env,
    no_real_launchctl,
    registered,
    screen_claude,
    socket_dir,
    tmux,
    write_claude,
)
from .test_kyo_controller import pytestmark as pytestmark
from .test_kyo_serve import Client, body_json, running  # noqa: F401 - the fixture, by name
from .test_kyo_serve_send import issue, log_rows

START = "/v1/projects/proj/orchestrator/start"


@pytest.fixture
def served(env, registered: Path, running) -> Client:
    """A server over the registered project, in the controller tests' environment."""
    return running()


@pytest.fixture
def token(store_home: Path) -> str:
    return issue("ipad", device_tokens.PERMISSION_START)


def post(
    client: Client, path: str, token: str | None, document: object = None
) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps({} if document is None else document).encode("utf-8")
    status, _, raw = client.request(path, method="POST", headers=headers, body=body)
    return status, body_json(raw)


def sessions(env: dict) -> list[str]:
    return tmux(env["socket"], "list-sessions", "-F", "#{session_name}").stdout.split()


# ---------------------------------------------------------------------- c1: the start


def test_the_route_starts_the_session_and_answers_as_the_command_does(
    env, served: Client, token: str, registered: Path
) -> None:
    status, started = post(served, START, token)
    assert status == 200, started
    assert sessions(env) == ["TST"]
    assert started["state"] == "started" and started["waiting_for_trust"] is False
    assert started["session"] == "TST" and started["cwd"] == str(registered.resolve())
    pane_pid = tmux(env["socket"], "list-panes", "-t", "=TST:", "-F", "#{pane_pid}").stdout
    assert int(pane_pid) == started["pid"]
    # The command's own document, key for key, beside the route's two additions.
    assert invoke(["orchestrator", "stop", "proj"]).code == 0
    typed = invoke(["orchestrator", "start", "proj", "--json"]).json
    assert set(started) == set(typed) | {"at", "device"}
    varying = {"pid", "at", "device", "server_was_running"}
    assert {k: v for k, v in started.items() if k not in varying} == {
        k: v for k, v in typed.items() if k not in varying
    }
    assert started["device"]["name"] == "ipad"


def test_the_route_goes_through_cmd_controller_start(
    served: Client, token: str, monkeypatch
) -> None:
    calls: list[tuple] = []

    def fake(project, server, environ) -> dict:
        calls.append((project.prefix, server.socket_args[0], environ is served.server.environ))
        return {"state": "started", "session": project.prefix}

    monkeypatch.setattr(cmd_controller, "start", fake)
    status, started = post(served, START, token)
    assert status == 200 and started["session"] == "TST" and started["action"] == "start"
    # One call, on the socket the server's environment names, with that environment.
    assert calls == [("TST", "-S", True)]


def test_a_start_reports_waiting_for_trust_and_its_folder(
    env, served: Client, token: str, registered: Path
) -> None:
    write_claude(env["bin"], screen_claude(MEASURED_TRUST_SCREEN))
    status, started = post(served, START, token)
    assert status == 200, started
    assert started["state"] == "waiting-for-trust" and started["waiting_for_trust"] is True
    assert started["trust_folder"] == str(registered.resolve())
    # Nothing was typed into the pane: the answer is the person's.
    assert not (env["bin"] / "claude.answered").exists()


def test_the_start_route_answers_post_only_and_takes_no_query(
    env, served: Client, token: str
) -> None:
    status, headers, _ = served.get(START)
    assert status == 405 and headers["Allow"] == "POST"
    status, refused = post(served, f"{START}?token={token}", token)
    assert status == 400 and "Authorization: Bearer" in refused["error"]
    assert sessions(env) == []
    assert post(served, START, token)[0] == 200


def test_a_body_that_is_not_an_object_is_refused_and_an_object_clears_it(
    env, served: Client, token: str
) -> None:
    status, refused = post(served, START, token, ["start"])
    assert status == 400 and "JSON object" in refused["error"]
    assert sessions(env) == []
    assert post(served, START, token, {})[0] == 200


def test_a_project_the_registry_does_not_name_is_refused(env, served: Client, token: str) -> None:
    status, _ = post(served, "/v1/projects/elsewhere/orchestrator/start", token)
    assert status == 404 and sessions(env) == []


# ------------------------------------------------------------------ c2: the permission


def test_a_start_with_no_token_is_refused_and_the_remedy_clears_it(
    env, served: Client, store_home: Path
) -> None:
    status, refused = post(served, START, None)
    assert status == 401 and refused["reason"] == "no-token"
    assert "kyo devices issue <name> --permission start" in refused["error"]
    assert sessions(env) == []
    assert post(served, START, issue("ipad", "start"))[0] == 200


def test_a_token_with_only_send_is_refused_and_the_remedy_clears_it(
    env, served: Client, store_home: Path
) -> None:
    only_send = issue("ipad", device_tokens.PERMISSION_SEND)
    status, refused = post(served, START, only_send)
    assert status == 403 and refused["reason"] == "no-start-permission"
    assert "kyo devices revoke ipad" in refused["error"]
    assert "kyo devices issue <name> --permission start" in refused["error"]
    assert sessions(env) == []

    # The remedy, as the sentence gives it: revoke, and issue with each permission needed.
    assert invoke(["devices", "revoke", "ipad"]).code == 0
    status, started = post(served, START, issue("ipad", "send", "start"))
    assert status == 200 and sessions(env) == ["TST"], started


def test_a_revoked_token_is_refused_at_once(env, served: Client, token: str) -> None:
    assert invoke(["devices", "revoke", "ipad"]).code == 0
    status, refused = post(served, START, token)
    assert status == 401 and refused["reason"] == "revoked-token"
    assert sessions(env) == []


def test_devices_grants_and_lists_the_start_permission(store_home: Path) -> None:
    assert device_tokens.PERMISSION_START in device_tokens.PERMISSIONS
    issue("ipad", "start", "notes")
    listed = invoke(["devices", "list", "--json"]).json["devices"]
    assert [(d["name"], d["permissions"]) for d in listed] == [("ipad", ["notes", "start"])]
    assert "permissions: notes, start" in invoke(["devices", "list"]).out
    unknown = invoke(["devices", "issue", "other", "--permission", "stop"])
    assert unknown.code != 0 and "start" in unknown.err


# ------------------------------------------------------------- c3: already running


def test_a_start_while_one_runs_starts_no_second_session_and_says_so(
    env, served: Client, token: str
) -> None:
    first = post(served, START, token)[1]
    status, refused = post(served, START, token)
    assert status == 409 and refused["reason"] == "already-running"
    assert "no second session was started" in refused["error"]
    assert "orchestrator session TST is already running" in refused["error"]
    assert sessions(env) == ["TST"]
    panes = tmux(env["socket"], "list-panes", "-a", "-F", "#{pane_pid}").stdout.split()
    assert panes == [str(first["pid"])]
    # The sentence's remedy: end it, and a start succeeds.
    assert "`kyo orchestrator stop proj`" in refused["error"]
    assert invoke(["orchestrator", "stop", "proj"]).code == 0
    assert post(served, START, token)[0] == 200


def test_two_starts_at_once_start_one_session(env, served: Client, token: str) -> None:
    answers: list[int] = []

    def start() -> None:
        answers.append(post(served, START, token)[0])

    threads = [threading.Thread(target=start) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert sorted(answers) == [200, 409]
    assert sessions(env) == ["TST"]


def test_a_start_that_fails_says_why_and_its_remedy_clears_it(
    env, served: Client, token: str
) -> None:
    write_claude(env["bin"], "#!/bin/sh\nexit 3\n")
    status, refused = post(served, START, token)
    assert status == 502 and refused["reason"] == "start-failed"
    assert "exited at once" in refused["error"]
    write_claude(env["bin"], "#!/bin/sh\nexec sleep 300\n")
    assert post(served, START, token)[0] == 200


def test_a_project_with_no_usable_prefix_is_refused_and_setting_one_clears_it(
    env, served: Client, token: str, registered: Path
) -> None:
    config = registered / "kyosei" / "config.yml"
    original = config.read_text(encoding="utf-8")
    assert "project_prefix: TST" in original
    config.write_text(original.replace("project_prefix: TST", "project_prefix: tst"))
    status, refused = post(served, START, token)
    assert status == 502 and "project_prefix" in refused["error"]
    assert sessions(env) == []
    config.write_text(original, encoding="utf-8")
    assert post(served, START, token)[0] == 200


# ---------------------------------------------------------------------- c4: the log


def test_a_start_is_logged_with_its_device(
    served: Client, token: str, store_home: Path
) -> None:
    assert post(served, START, token)[0] == 200
    (row,) = log_rows(store_home)
    assert row["action"] == serve_orchestrator.LOG_ACTION == "orchestrator-start"
    assert row["project"] == "proj" and row["device"]["name"] == "ipad"
    assert row["outcome"] == "started" and row["status"] == 200 and row["reason"] == "started"
    assert row["segments"] is None and row["session_id"] == ""
    assert token not in (store_home / serve_send.LOG_FILE).read_text(encoding="utf-8")


def test_a_refusal_for_want_of_the_permission_is_logged_with_its_device(
    served: Client, store_home: Path
) -> None:
    assert post(served, START, issue("ipad", "send", "notes"))[0] == 403
    (row,) = log_rows(store_home)
    assert row["action"] == "orchestrator-start" and row["device"]["name"] == "ipad"
    assert row["outcome"] == "refused" and row["reason"] == "no-start-permission"
    assert row["status"] == 403


def test_every_other_outcome_is_logged_too(
    env, served: Client, token: str, store_home: Path
) -> None:
    assert post(served, START, None)[0] == 401
    assert post(served, START, token)[0] == 200
    assert post(served, START, token)[0] == 409
    assert invoke(["orchestrator", "stop", "proj"]).code == 0
    write_claude(env["bin"], "#!/bin/sh\nexit 3\n")
    assert post(served, START, token)[0] == 502
    rows = log_rows(store_home)
    assert [(r["outcome"], r["reason"]) for r in rows] == [
        ("refused", "no-token"),
        ("started", "started"),
        ("refused", "already-running"),
        ("failed", "start-failed"),
    ]
    assert rows[0]["device"] is None and all(r["device"] for r in rows[1:])


def test_a_log_that_cannot_be_used_stops_the_start_and_moving_it_clears_it(
    env, served: Client, token: str, store_home: Path
) -> None:
    (store_home / ".git").mkdir()
    status, refused = post(served, START, token)
    assert status == 500 and refused["reason"] == "send-log-unusable"
    assert "no orchestrator session was started" in refused["error"]
    assert sessions(env) == []
    (store_home / ".git").rmdir()
    assert post(served, START, token)[0] == 200


# ---------------------------------------------------------- c5: a start and nothing else


def test_no_route_runs_any_other_orchestrator_subcommand(
    env, served: Client, token: str
) -> None:
    assert post(served, START, token)[0] == 200
    orchestrator = "/v1/projects/proj/orchestrator"
    parser = cli.build_parser()
    (action,) = [
        a
        for a in parser._subparsers._group_actions[0].choices["orchestrator"]._actions
        if a.dest == "action"
    ]
    others = sorted(set(action.choices) - {"start"})
    assert {"stop", "attach", "list"} <= set(others)
    for name in [*others, "install", "uninstall", "start/proj", ""]:
        path = f"{orchestrator}/{name}".rstrip("/")
        for status, refused in (
            post(served, path, token),
            (lambda got: (got[0], body_json(got[2])))(served.get(path)),
        ):
            assert status == 404, (name, refused)
            assert refused["reason"] == "not-served" and "KYO-718" in refused["error"]
            assert "/exit" in refused["error"]
    # The session the route started is still the only one, and still running.
    assert sessions(env) == ["TST"]
    assert serve_orchestrator.ROUTE == serve.ORCHESTRATOR_ROUTE
    assert serve_orchestrator.START == serve.ORCHESTRATOR_START


def test_the_orchestrator_command_stays_refused_by_name(
    env, served: Client, token: str
) -> None:
    for command in ("orchestrator", "controller"):
        assert command in serve.NOT_SERVED and command not in serve.SERVED_COMMANDS
        for action in ("start/proj", "stop/proj", "list"):
            status, _, body = served.get(f"/v1/projects/proj/cmd/{command}/{action}")
            assert status == 403, (command, action, body)
            assert serve.NOT_SERVED[command] in body_json(body)["error"]
    assert sessions(env) == []


def test_the_start_module_cannot_stop_a_session() -> None:
    tree = ast.parse(Path(serve_orchestrator.__file__).read_text(encoding="utf-8"))
    # The module reaches cmd_controller by two names, and neither ends a session.
    reached = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "cmd_controller"
    }
    assert reached == {"AlreadyRunning", "start_registered"}, reached
    imported = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    ]
    assert "stop" not in imported and "subprocess" not in imported
