"""KYO-956: the write routes of the Config view and the ``config`` device permission.

The fifth build of proposal KYO-950 (sections 5.3, 5.4 and 9.1). Every request goes over a
real socket to a server whose ``$KYOSEI_HOME`` is the suite's own temporary directory and
whose only project is a temporary one; no file outside it is read or written.

A route calls the writer the CLI calls, so what a writer refuses a route refuses with the
writer's sentence; what only a route adds (the permission, the etag, a stale server, the
two settings a device may not turn on) is tested here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from kyosei import (
    cmd_config,
    config_splice,
    config_view,
    device_tokens,
    home_settings,
    prior_art,
    serve_config,
    serve_files,
)
from kyosei.errors import KyoseiError

from .conftest import invoke
from .test_kyo_serve import Client, body_json, register, start, stop
from .test_kyo_serve_files import file_writes

BASE = "/v1/projects/proj"

SETTINGS = """\
# The machine's settings.
serve_send: true  # sending is on
# serve_idle_message: false
"""

SETTINGS_SEND_OFF = "# The machine's settings.\nserve_send: false\n"


@pytest.fixture
def server(project: Path, store_home: Path):
    register(store_home, project)
    client = start()
    yield client
    stop(client)


def settings_file(text: str | None = SETTINGS) -> Path:
    path = home_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        path.write_text(text, encoding="utf-8")
    return path


def config_file(project: Path) -> Path:
    return project / "kyosei" / "config.yml"


def agent_file(project: Path, name: str = "kyosei-worker") -> Path:
    run = invoke(
        [
            "--root",
            str(project),
            "install-agents",
            "--type",
            "worker=sonnet:medium",
            "--type",
            "designer=opus:high",
        ]
    )
    assert run.code == 0, run
    return project / ".claude" / "agents" / f"{name}.md"


def etag(path: Path) -> str:
    return config_splice.etag_of(path.read_bytes())


class Device:
    def __init__(self, server: Client, name: str, permissions: list[str]) -> None:
        self.server = server
        self.device, secret = device_tokens.issue(name, permissions)
        self.headers = {"Authorization": f"Bearer {secret}"}

    def post(
        self, path: str, document, *, if_match: str | None = None, token: bool = True
    ) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers.update(self.headers)
        if if_match is not None:
            headers["If-Match"] = if_match
        body = document if isinstance(document, bytes) else json.dumps(document).encode()
        status, _, raw = self.server.request(path, method="POST", headers=headers, body=body)
        return status, body_json(raw)

    def get(self, path: str) -> dict:
        status, _, raw = self.server.request(path, headers=self.headers)
        assert status == 200, raw
        return body_json(raw)


@pytest.fixture
def writer(server: Client) -> Device:
    return Device(server, "laptop", [device_tokens.PERMISSION_CONFIG])


def rows() -> list[dict]:
    return config_splice.read_log(__import__("os").environ)


# --------------------------------------------------------------- c1: the permission


def test_config_is_a_permission_and_can_be_granted_and_a_token_issued_before_lacks_it(
    server: Client, project: Path
) -> None:
    assert device_tokens.PERMISSION_CONFIG == "config"
    assert "config" in device_tokens.PERMISSIONS
    old = Device(server, "old-phone", [device_tokens.PERMISSION_SEND])
    assert "config" not in old.device.permissions
    status, refused = old.post(
        f"{BASE}/config/worker_model", {"value": "opus"}, if_match=etag(config_file(project))
    )
    assert status == 403 and refused["reason"] == "no-config-permission"
    assert "kyo devices grant old-phone --permission config" in refused["error"]
    # The remedy it names clears the refusal, and the token stays the one the device holds.
    run = invoke(["devices", "grant", "old-phone", "--permission", "config"])
    assert run.code == 0, run
    status, written = old.post(
        f"{BASE}/config/worker_model", {"value": "opus"}, if_match=etag(config_file(project))
    )
    assert status == 200 and written["written"] is True, written
    # A token with no permission is refused with the remedy for this route's permission.
    nothing = Device(server, "nothing", [])
    status, refused = nothing.post(f"{BASE}/config/worker_model", {"value": "x"}, if_match="x")
    assert status == 403 and "--permission config" in refused["error"]
    status, refused = nothing.post(
        f"{BASE}/config/worker_model", {"value": "x"}, if_match="x", token=False
    )
    assert status == 401 and "--permission config" in refused["error"]


def test_the_permission_is_listed_by_kyo_devices_help() -> None:
    assert "config" in invoke(["devices", "--help"]).out


# ------------------------------------------------------------- c2, c3: each route writes


def test_worker_model_is_written_through_the_cli_writer_and_cleared(
    writer: Device, project: Path
) -> None:
    path = config_file(project)
    status, document = writer.post(
        f"{BASE}/config/worker_model", {"value": "claude-opus-5-5"}, if_match=etag(path)
    )
    assert status == 200, document
    assert document["key"] == "worker_model" and document["value"] == "claude-opus-5-5"
    assert document["written"] is True and document["etag"] == etag(path)
    assert yaml.safe_load(path.read_text())["worker_model"] == "claude-opus-5-5"
    # null clears it, through the writer's `unset`.
    status, cleared = writer.post(
        f"{BASE}/config/worker_model", {"value": None}, if_match=document["etag"]
    )
    assert status == 200 and cleared["previous"] == "claude-opus-5-5"
    assert "worker_model" not in yaml.safe_load(path.read_text())


def test_the_ceiling_route_writes_a_whole_number_and_refuses_what_the_reader_refuses(
    writer: Device, project: Path
) -> None:
    path = config_file(project)
    status, document = writer.post(
        f"{BASE}/config/worker_command_timeout_ceiling_seconds",
        {"value": 240},
        if_match=etag(path),
    )
    assert status == 200 and document["value"] == 240, document
    before = path.read_bytes()
    for bad in (0, -5, 2.5, "240", True):
        status, refused = writer.post(
            f"{BASE}/config/worker_command_timeout_ceiling_seconds",
            {"value": bad},
            if_match=etag(path),
        )
        assert status == 422 and refused["reason"] == "refused", (bad, refused)
        assert path.read_bytes() == before


def test_a_route_refuses_with_the_writers_own_message(writer: Device, project: Path) -> None:
    path = config_file(project)
    cli = invoke(["--root", str(project), "config", "set", "worker_model", "two words"])
    assert cli.code != 0
    status, refused = writer.post(
        f"{BASE}/config/worker_model", {"value": "two words"}, if_match=etag(path)
    )
    assert status == 422 and refused["reason"] == "refused"
    assert refused["error"] in cli.err and "is not a model id or alias" in refused["error"]
    assert "worker_model" not in yaml.safe_load(path.read_text())


def test_prior_art_state_is_written_and_on_is_refused_without_the_extra(
    writer: Device, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = config_file(project)
    monkeypatch.setattr(prior_art, "extra_installed", lambda: False)
    status, refused = writer.post(
        f"{BASE}/config/recx.prior_art.state", {"value": "on"}, if_match=etag(path)
    )
    assert status == 422 and "needs recx-cli, which is not installed" in refused["error"]
    status, written = writer.post(
        f"{BASE}/config/recx.prior_art.state", {"value": "off"}, if_match=etag(path)
    )
    assert status == 200 and written["written"] is True and written["value"] == "off"
    assert prior_art.read_prior_art_config(project).state == "off"
    # The remedy it names (the extra installed) clears the refusal.
    monkeypatch.setattr(prior_art, "extra_installed", lambda: True)
    status, written = writer.post(
        f"{BASE}/config/recx.prior_art.state", {"value": "on"}, if_match=written["etag"]
    )
    assert status == 200 and written["previous"] == "off" and written["value"] == "on"
    status, again = writer.post(
        f"{BASE}/config/recx.prior_art.state", {"value": "on"}, if_match=written["etag"]
    )
    assert status == 200 and again["written"] is False
    status, bad = writer.post(
        f"{BASE}/config/recx.prior_art.state", {"value": "maybe"}, if_match=again["etag"]
    )
    assert status == 400


def test_docs_coverage_level_refuses_problem_without_code_and_the_remedy_clears_it(
    writer: Device, project: Path
) -> None:
    path = config_file(project)
    status, refused = writer.post(
        f"{BASE}/config/docs_coverage.level", {"value": "problem"}, if_match=etag(path)
    )
    assert status == 422 and "code" in refused["error"]
    assert "docs_coverage" not in yaml.safe_load(path.read_text())
    status, written = writer.post(
        f"{BASE}/config/docs_coverage.level", {"value": "notice"}, if_match=etag(path)
    )
    assert status == 200 and written["value"] == "notice" and written["written"] is True
    assert yaml.safe_load(path.read_text())["docs_coverage"]["level"] == "notice"
    status, bad = writer.post(
        f"{BASE}/config/docs_coverage.level", {"value": "loud"}, if_match=written["etag"]
    )
    assert status == 400 and "off, notice, problem" in bad["error"]


def test_an_agent_types_model_and_effort_change_and_nothing_else(
    writer: Device, project: Path
) -> None:
    path = agent_file(project)
    before = path.read_text()
    status, document = writer.post(
        f"{BASE}/agent-types/kyosei-worker",
        {"model": "claude-opus-5-5", "effort": "high"},
        if_match=etag(path),
    )
    assert status == 200, document
    assert document["previous"] == {"model": "sonnet", "effort": "medium"}
    assert document["value"] == {"model": "claude-opus-5-5", "effort": "high"}
    after = path.read_text()
    assert after.split("---", 2)[2] == before.split("---", 2)[2]
    assert "model: claude-opus-5-5\n" in after and "effort: high\n" in after
    # The refusals the writer makes: a model and an effort outside the patterns, a missing file.
    status, refused = writer.post(
        f"{BASE}/agent-types/kyosei-worker",
        {"model": "two words", "effort": "high"},
        if_match=document["etag"],
    )
    assert status == 422 and "is not a model id or alias" in refused["error"]
    status, refused = writer.post(
        f"{BASE}/agent-types/kyosei-worker",
        {"model": "opus", "effort": "loudest"},
        if_match=document["etag"],
    )
    assert status == 422 and "is not an effort level" in refused["error"]
    path.unlink()
    status, refused = writer.post(
        f"{BASE}/agent-types/kyosei-worker", {"model": "opus", "effort": "low"}, if_match="x"
    )
    assert status == 422 and "does not exist" in refused["error"]
    assert not path.exists()
    # Only the two packaged types; a body of anything but the two lines.
    status, _ = writer.post(
        f"{BASE}/agent-types/kyosei-other", {"model": "opus", "effort": "low"}, if_match="x"
    )
    assert status == 404
    status, _ = writer.post(f"{BASE}/agent-types/kyosei-designer", {"model": "opus"}, if_match="x")
    assert status == 400


def test_serve_idle_message_is_written_to_the_machines_file(writer: Device) -> None:
    path = settings_file()
    status, document = writer.post(
        "/v1/settings/serve_idle_message", {"value": True}, if_match=etag(path)
    )
    assert status == 200 and document["value"] is True and document["written"] is True
    assert home_settings.load().serve_idle_message is True
    status, off = writer.post(
        "/v1/settings/serve_idle_message", {"value": False}, if_match=document["etag"]
    )
    assert status == 200 and off["previous"] is True
    assert home_settings.load().serve_idle_message is False
    status, bad = writer.post(
        "/v1/settings/serve_idle_message", {"value": "yes"}, if_match=off["etag"]
    )
    assert status == 400


def test_a_missing_settings_file_is_refused_and_the_remedy_clears_it(writer: Device) -> None:
    path = home_settings.settings_path()
    assert not path.exists()
    status, refused = writer.post(
        "/v1/settings/serve_idle_message", {"value": False}, if_match="sha256:0"
    )
    assert status == 422 and "does not create it" in refused["error"]
    assert not path.exists()
    settings_file("serve_send: true\n")
    status, written = writer.post(
        "/v1/settings/serve_idle_message", {"value": True}, if_match=etag(path)
    )
    assert status == 200 and written["written"] is True


# ------------------------------------------------------------- c4: read_transcripts


def test_read_transcripts_is_accepted_off_and_refused_on_by_request(
    writer: Device, project: Path
) -> None:
    path = config_file(project)
    invoke(["--root", str(project), "config", "set", "read_transcripts", "true"])
    status, off = writer.post(
        f"{BASE}/config/read_transcripts", {"value": False}, if_match=etag(path)
    )
    assert status == 200 and off["previous"] is True and off["value"] is False, off
    before = path.read_bytes()
    status, refused = writer.post(
        f"{BASE}/config/read_transcripts", {"value": True}, if_match=etag(path)
    )
    assert status == 403 and refused["reason"] == "by-request"
    assert "request" in refused["error"] and path.read_bytes() == before
    for other in (None, "false", 0):
        status, _ = writer.post(
            f"{BASE}/config/read_transcripts", {"value": other}, if_match=etag(path)
        )
        assert status == 400
    assert path.read_bytes() == before


# ----------------------------------------------------- c5: serve_idle_message on needs send


def test_idle_message_on_is_refused_while_serve_send_is_not_true(writer: Device) -> None:
    path = settings_file(SETTINGS_SEND_OFF)
    before = path.read_bytes()
    status, refused = writer.post(
        "/v1/settings/serve_idle_message", {"value": True}, if_match=etag(path)
    )
    assert status == 403 and refused["reason"] == "send-disabled"
    assert "serve_send: true" in refused["error"] and path.read_bytes() == before
    # Turning it off is accepted whatever serve_send says.
    status, off = writer.post(
        "/v1/settings/serve_idle_message", {"value": False}, if_match=etag(path)
    )
    assert status == 200, off
    # The remedy it names (the owner sets serve_send at the machine) clears the refusal.
    path.write_text(SETTINGS_SEND_OFF.replace("false", "true"), encoding="utf-8")
    status, on = writer.post(
        "/v1/settings/serve_idle_message", {"value": True}, if_match=etag(path)
    )
    assert status == 200 and on["value"] is True


# ------------------------------------------------------ c6: stale server and the etag


def test_a_stale_server_refuses_every_write_naming_the_restart(
    writer: Device, server: Client, project: Path
) -> None:
    settings = settings_file()
    agent = agent_file(project)
    path = config_file(project)
    server.server.started_version = {**server.server.started_version, "commit": "an-older-build"}
    before = [p.read_bytes() for p in (path, settings, agent)]
    attempts = [
        (f"{BASE}/config/worker_model", {"value": "opus"}, etag(path)),
        (f"{BASE}/config/read_transcripts", {"value": False}, etag(path)),
        (f"{BASE}/agent-types/kyosei-worker", {"model": "opus", "effort": "low"}, etag(agent)),
        ("/v1/settings/serve_idle_message", {"value": False}, etag(settings)),
    ]
    for target, document, tag in attempts:
        status, refused = writer.post(target, document, if_match=tag)
        assert status == 409 and refused["reason"] == "server-stale", (target, refused)
        assert "kyo serve --restart" in refused["error"]
    assert [p.read_bytes() for p in (path, settings, agent)] == before
    # The refusal's remedy: a restarted server (one whose build is the installed one).
    server.server.started_version = server.server.version_payload()
    status, _ = writer.post(f"{BASE}/config/worker_model", {"value": "opus"}, if_match=etag(path))
    assert status == 200


def test_a_different_etag_is_412_with_the_current_value_and_etag(
    writer: Device, project: Path
) -> None:
    path = config_file(project)
    read = writer.get(f"{BASE}/config")["files"]["config"]["etag"]
    assert read == etag(path)
    invoke(["--root", str(project), "config", "set", "worker_model", "sonnet"])
    before = path.read_bytes()
    status, refused = writer.post(f"{BASE}/config/worker_model", {"value": "opus"}, if_match=read)
    assert status == 412 and refused["reason"] == "changed"
    assert refused["current"] == {"value": "sonnet", "etag": etag(path)}
    assert path.read_bytes() == before
    # The remedy it names (read again, send the current etag) clears the refusal.
    status, written = writer.post(
        f"{BASE}/config/worker_model", {"value": "opus"}, if_match=refused["current"]["etag"]
    )
    assert status == 200 and written["previous"] == "sonnet"


def test_a_write_without_if_match_says_what_it_needs(writer: Device, project: Path) -> None:
    path = config_file(project)
    before = path.read_bytes()
    status, refused = writer.post(f"{BASE}/config/worker_model", {"value": "opus"})
    assert status == 428 and refused["reason"] == "if-match-required"
    assert "If-Match" in refused["error"] and path.read_bytes() == before


def test_a_file_changed_after_the_check_is_the_same_412(
    writer: Device, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = config_file(project)
    real = config_splice.apply_change

    def raced(**kwargs):
        invoke(["--root", str(project), "config", "set", "worker_model", "raced"])
        return real(**kwargs)

    monkeypatch.setattr(config_splice, "apply_change", raced)
    status, refused = writer.post(
        f"{BASE}/config/worker_model", {"value": "opus"}, if_match=etag(path)
    )
    assert status == 412 and refused["reason"] == "changed"
    assert refused["current"]["value"] == "raced"


# ------------------------------------------------- c7: no route for the two machine keys


@pytest.mark.parametrize("key", ["serve_send", "orchestrator_command", "controller_command"])
def test_no_route_writes_the_two_machine_keys(writer: Device, key: str) -> None:
    path = settings_file()
    before = path.read_bytes()
    status, refused = writer.post(f"/v1/settings/{key}", {"value": True}, if_match=etag(path))
    assert status == 404 and refused["reason"] == "no-route"
    status, _ = writer.post(f"{BASE}/config/{key}", {"value": True}, if_match="x")
    assert status == 404
    assert path.read_bytes() == before


def test_a_test_fails_if_a_route_for_either_is_added(monkeypatch: pytest.MonkeyPatch) -> None:
    routed = {
        *serve_config.PROJECT_KEY_ROUTES,
        *serve_config.MACHINE_KEY_ROUTES,
        *config_view.AGENT_TYPES,
    }
    assert routed.isdisjoint(serve_config.NEVER_ROUTED)
    assert set(serve_config.NEVER_ROUTED) == {
        "serve_send",
        "orchestrator_command",
        "controller_command",
    }
    # The documents' own route descriptors are the other place a route would be named.
    descriptors = {
        row.write["route"]
        for row in (*config_view.SETTINGS, *config_view.MACHINE_SETTINGS)
        if row.write
    }
    for key in serve_config.NEVER_ROUTED:
        assert not any(route.endswith("/" + key) for route in descriptors)
    # And the check is not vacuous: adding one is seen.
    monkeypatch.setattr(serve_config, "MACHINE_KEY_ROUTES", ("serve_idle_message", "serve_send"))
    assert not {*serve_config.MACHINE_KEY_ROUTES}.isdisjoint(serve_config.NEVER_ROUTED)


def test_the_served_routes_are_exactly_the_documents_write_routes(project: Path) -> None:
    descriptors = {
        row.write["route"]
        for row in (*config_view.SETTINGS, *config_view.MACHINE_SETTINGS)
        if row.write
    } | {f"agent-types/{name}" for name in config_view.AGENT_TYPES}
    served = (
        {f"config/{key}" for key in serve_config.PROJECT_KEY_ROUTES}
        | {f"settings/{key}" for key in serve_config.MACHINE_KEY_ROUTES}
        | {f"agent-types/{name}" for name in config_view.AGENT_TYPES}
    )
    assert served == descriptors


# ------------------------------------------------------------ c8: through serve_files


def test_the_write_lands_through_serve_files_and_no_serve_module_writes_a_file(
    writer: Device, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    real = serve_files.replace_config_text

    def spy(target, shown, write, **kwargs):
        seen.append(shown)
        return real(target, shown, write, **kwargs)

    monkeypatch.setattr(serve_files, "replace_config_text", spy)
    path = config_file(project)
    status, _ = writer.post(f"{BASE}/config/worker_model", {"value": "opus"}, if_match=etag(path))
    assert status == 200 and seen == ["config.yml"]
    source = Path(serve_config.__file__).read_text(encoding="utf-8")
    assert file_writes(source, "serve_config.py") == []


# ----------------------------------------------------------------------- c9: the log


def test_each_attempt_is_a_row_naming_the_device_refusals_included(
    writer: Device, project: Path
) -> None:
    path = config_file(project)
    writer.post(f"{BASE}/config/worker_model", {"value": "opus"}, if_match=etag(path))
    writer.post(f"{BASE}/config/worker_model", {"value": "two words"}, if_match=etag(path))
    writer.post(f"{BASE}/config/read_transcripts", {"value": True}, if_match=etag(path))
    writer.post(f"{BASE}/config/worker_model", {"value": "opus"}, if_match="sha256:stale")
    writer.post(f"{BASE}/config/worker_model", {"value": "opus"})
    writer.post(f"{BASE}/config/serve_send", {"value": True}, if_match="x")
    stranger = Device(writer.server, "tablet", [device_tokens.PERMISSION_SEND])
    stranger.post(f"{BASE}/config/worker_model", {"value": "opus"}, if_match=etag(path))
    logged = rows()
    assert [row["outcome"] for row in logged] == [
        "written",
        "refused",
        "refused",
        "refused",
        "refused",
        "refused",
        "refused",
    ]
    for row in logged[:-1]:
        assert row["device"] == "laptop" and row["device_id"] == writer.device.id
    assert logged[-1]["device"] == "tablet" and logged[-1]["device_id"] == stranger.device.id
    assert logged[0]["previous"] is None and logged[0]["new"] == "opus"
    assert "is not a model id" in logged[1]["reason"]
    assert logged[2]["key"] == "read_transcripts"
    # The document the view reads reports the last write and the device that made it.
    document = writer.get(f"{BASE}/config")
    by_key = {row["key"]: row for row in document["settings"]}
    assert by_key["worker_model"]["last_change"]["device"] == "laptop"


def test_a_write_that_cannot_be_logged_does_not_happen(writer: Device, project: Path) -> None:
    path = config_file(project)
    before = path.read_bytes()
    config_splice.log_path(__import__("os").environ).mkdir(parents=True)  # a directory, not a log
    status, refused = writer.post(
        f"{BASE}/config/worker_model", {"value": "opus"}, if_match=etag(path)
    )
    assert status == 500 and refused["reason"] == "log-unwritable"
    assert path.read_bytes() == before
    status, refused = writer.post("/v1/settings/serve_idle_message", {"value": False}, if_match="x")
    assert status == 500 and refused["reason"] == "log-unwritable"
    # The remedy it names (a log that can be opened) clears it.
    config_splice.log_path(__import__("os").environ).rmdir()
    status, _ = writer.post(f"{BASE}/config/worker_model", {"value": "opus"}, if_match=etag(path))
    assert status == 200


# --------------------------------------------------------- c10: the document, the device


def test_the_documents_state_whether_the_presenting_device_holds_config(
    server: Client, project: Path
) -> None:
    settings_file()
    holder = Device(server, "laptop", [device_tokens.PERMISSION_CONFIG])
    reader = Device(server, "ipad", [device_tokens.PERMISSION_SEND])
    for path in (f"{BASE}/config", "/v1/settings"):
        assert holder.get(path)["device"]["holds_config"] is True
        assert reader.get(path)["device"]["holds_config"] is False
    assert reader.get(f"{BASE}/config")["device"]["permissions"] == ["send"]


# ------------------------------------------------------- the surface of the routes


def test_a_route_takes_no_query_and_only_post_and_a_json_body(
    writer: Device, server: Client, project: Path
) -> None:
    path = config_file(project)
    status, _ = writer.post(f"{BASE}/config/worker_model?x=1", {"value": "o"}, if_match="x")
    assert status == 400
    status, response, _ = server.request(f"{BASE}/config/worker_model", headers=writer.headers)
    assert status == 405 and response["Allow"] == "POST"
    status, refused = writer.post(f"{BASE}/config/worker_model", b"[1]", if_match=etag(path))
    assert status == 400 and refused["reason"] == "bad-request"
    status, refused = writer.post(
        f"{BASE}/config/worker_model", {"value": "opus", "extra": 1}, if_match=etag(path)
    )
    assert status == 400
    status, _, _ = server.request(
        f"{BASE}/config/worker_model",
        method="POST",
        headers={**writer.headers, "Content-Type": "text/plain", "If-Match": etag(path)},
        body=b'{"value": "opus"}',
    )
    assert status == 415
    assert "worker_model" not in yaml.safe_load(path.read_text())


def test_an_unknown_project_is_refused_after_the_token(server: Client, writer: Device) -> None:
    nope = "/v1/projects/nope/config/worker_model"
    assert writer.post(nope, {"value": "o"}, token=False)[0] == 401
    assert writer.post(nope, {"value": "o"}, if_match="x")[0] == 404


def test_the_cli_writers_name_cli_in_their_rows(project: Path) -> None:
    run = invoke(["--root", str(project), "config", "set", "worker_model", "opus"])
    assert run.code == 0, run
    assert [(row["device"], row["device_id"]) for row in rows()] == [("cli", "cli")]
    assert cmd_config.WRITABLE  # the writer the routes share


# ------------------------------------------------- KYO-957: the review's findings F1, F3, F4


def test_a_file_that_is_not_utf8_is_answered_and_logged_and_left_alone(
    writer: Device, project: Path
) -> None:
    """KYO-957 F1: the route closed its connection with no answer and no row."""
    path = config_file(project)
    path.write_bytes(path.read_bytes() + b"# caf\xe9\n")
    held = path.read_bytes()
    before = len(rows())
    status, document = writer.post(
        f"{BASE}/config/worker_model", {"value": "opus"}, if_match=etag(path)
    )
    assert status == 422 and "is not UTF-8 text" in document["error"], document
    assert path.read_bytes() == held
    assert len(rows()) == before + 1 and rows()[-1]["outcome"] == "refused"

    settings = settings_file()
    settings.write_bytes(settings.read_bytes() + b"# caf\xe9\n")
    held = settings.read_bytes()
    for value in (False, True):
        before = len(rows())
        status, document = writer.post(
            "/v1/settings/serve_idle_message", {"value": value}, if_match=etag(settings)
        )
        assert status in (422, 500) and "is not UTF-8 text" in document["error"], document
        assert settings.read_bytes() == held
        assert len(rows()) == before + 1


def test_an_error_no_writer_converted_is_a_logged_500_and_not_a_closed_connection(
    writer: Device, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KYO-957 F1, the backstop in ``serve_config.write``."""

    def broken(*args, **kwargs):
        raise RuntimeError("no such thing")

    monkeypatch.setattr(serve_config, "_write_project_key", broken)
    before = len(rows())
    status, document = writer.post(
        f"{BASE}/config/worker_model", {"value": "opus"}, if_match=etag(config_file(project))
    )
    assert status == 500 and document["reason"] == "failed", document
    assert "RuntimeError" in document["error"]
    assert len(rows()) == before + 1


def test_a_model_yaml_reads_as_a_number_is_refused_and_not_written(
    writer: Device, project: Path
) -> None:
    """KYO-957 F4: `model: 123` read back as an integer and passed a str() comparison."""
    path = agent_file(project)
    before = path.read_bytes()
    status, document = writer.post(
        f"{BASE}/agent-types/kyosei-worker",
        {"model": "123", "effort": "high"},
        if_match=etag(path),
    )
    assert status == 422 and "hand edit" in document["error"], document
    assert path.read_bytes() == before


def test_a_failed_check_restores_only_the_bytes_this_write_put_there(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KYO-957 F3: the restore after a refused probe overwrote a change made meanwhile."""
    path = config_file(project)
    text = "\n".join(
        line for line in path.read_text().splitlines()
        if not line.lstrip("# ").startswith("worker_command_timeout_ceiling_seconds")
    ) + "\n"
    path.write_text(text)

    def refuse_and_let_another_writer_in(*args, **kwargs):
        with path.open("a", encoding="utf-8") as handle:
            handle.write("# written by someone else during the check\n")
        return "the older build refuses it"

    monkeypatch.setattr(config_splice, "_probe_refusal", refuse_and_let_another_writer_in)
    run = invoke(
        ["--root", str(project), "config", "set", "worker_command_timeout_ceiling_seconds", "90"]
    )
    assert run.code != 0 and "nothing was restored" in run.err, run
    assert "# written by someone else during the check\n" in path.read_text()


def test_a_settings_file_that_is_not_utf8_is_refused_and_removing_it_clears_it(
    store_home: Path,
) -> None:
    """KYO-957 F1: `home_settings.load` raised a UnicodeDecodeError, which no caller expects."""
    path = settings_file()
    path.write_bytes(path.read_bytes() + b"# caf\xe9\n")
    with pytest.raises(KyoseiError, match="is not UTF-8 text"):
        home_settings.load()
    path.unlink()
    assert home_settings.load().source == "default"
