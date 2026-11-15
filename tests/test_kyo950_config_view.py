"""KYO-952: `kyo config` and the read routes for a project's and the machine's settings.

The read side of proposal KYO-950. The class registry (``config_view.SETTINGS``,
``MACHINE_SETTINGS``, ``DECISIONS``) must place every registered key; the command prints the
documents; ``GET /v1/projects/{project}/config`` and ``GET /v1/settings`` serve them to a
device token of any permission; the event stream reports a ``config`` change.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kyosei import cli, config_view, device_tokens, home_settings, schema, serve

from .conftest import invoke
from .test_kyo_serve import Client, body_json, register, start, stop
from .test_kyo_serve_files import EventStream

BASE = "/v1/projects/proj"

#: The setup decisions of the proposal's section 3.4, each of which holds no key of its own.
SECTION_3_4 = [
    "A1", "A2", "A3", "B1", "B2", "C1", "C2", "C3", "D2", "D6", "D7", "D8", "D9", "D10",
    "D11", "E1", "E2", "E3", "E4", "E5",
]  # fmt: skip

GUIDE = Path(__file__).resolve().parent.parent / "docs" / "guide" / "setup-decisions.md"


def machine_file(text: str) -> Path:
    path = home_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def config(project: Path, *args: str) -> dict:
    result = invoke(["--root", str(project), "config", "--json", *args])
    assert result.code == 0, result
    return json.loads(result.out)


def token(*permissions: str) -> dict[str, str]:
    _, secret = device_tokens.issue("laptop", list(permissions))
    return {"Authorization": f"Bearer {secret}"}


@pytest.fixture
def server(project: Path, store_home: Path):
    register(store_home, project)
    client = start()
    client.server.poll_seconds = 0.05
    client.server.keepalive_seconds = 0.4
    yield client
    stop(client)


def get(client: Client, path: str, headers: dict[str, str] | None = None):
    status, response_headers, body = client.request(path, headers=headers or {})
    return status, response_headers, body_json(body)


# ------------------------------------------------------------- c1, c2: the registry


def test_every_registered_key_and_decision_is_placed() -> None:
    assert config_view.unclassified() == {"config": [], "machine": []}
    ids = config_view.classified_decisions()
    assert sorted(ids) == sorted(set(ids)), "a decision is placed twice"
    assert set(SECTION_3_4) <= set(ids)
    keyed = {row.decision for row in config_view.SETTINGS if row.decision}
    keyed |= {row.decision for row in config_view.MACHINE_SETTINGS if row.decision}
    guide_ids = {
        line.split(".")[0].removeprefix("### ")
        for line in GUIDE.read_text(encoding="utf-8").splitlines()
        if line.startswith("### ") and line[4] in "ABCDE" and line[5].isdigit()
    }
    assert guide_ids <= set(ids) | keyed | {config_view.AGENT_TYPE_DECISION}
    for row in (*config_view.SETTINGS, *config_view.MACHINE_SETTINGS):
        assert row.klass in config_view.CLASS_VALUES
        assert row.why.strip()


def test_a_key_registered_without_a_class_is_reported(monkeypatch) -> None:
    monkeypatch.setitem(schema.CONFIG_CONSUMERS, "brand_new", ("nowhere",))
    monkeypatch.setattr(home_settings, "KNOWN_KEYS", (*home_settings.KNOWN_KEYS, "new_machine"))
    assert config_view.unclassified() == {"config": ["brand_new"], "machine": ["new_machine"]}


def test_removing_the_class_registry_is_reported(monkeypatch) -> None:
    monkeypatch.setattr(config_view, "SETTINGS", ())
    monkeypatch.setattr(config_view, "MACHINE_SETTINGS", ())
    missing = config_view.unclassified()
    assert set(missing["config"]) == set(schema.CONFIG_CONSUMERS)
    assert set(missing["machine"]) == set(home_settings.KNOWN_KEYS)


def test_the_project_document_lists_every_key_once_with_its_class(project: Path) -> None:
    document = config(project)
    keys = [row["key"] for row in document["settings"]]
    assert len(keys) == len(set(keys))
    covered = {top for row in config_view.SETTINGS for top in row.top_keys}
    assert covered == set(schema.CONFIG_CONSUMERS)
    by_key = {row["key"]: row for row in document["settings"]}
    assert by_key["worker_model"]["class"] == "editable"
    assert by_key["worktree_setup"]["class"] == "request"
    assert by_key["project_prefix"]["class"] == "shown"
    assert by_key["project_prefix"]["value"] == "TST"
    assert by_key["worktree_setup"]["request"]["header"].startswith(
        "[kyo-config-request] worktree_setup, "
    )
    assert [d["id"] for d in document["decisions"]] == [
        d for d in config_view.classified_decisions()
        if d in {x.id for x in config_view.DECISIONS if x.document == "project"}
    ]  # fmt: skip
    assert document["files"]["config"]["readable"] is True
    assert document["files"]["config"]["etag"].startswith("sha256:")


def test_the_machine_document_lists_every_machine_key_and_its_decisions(project: Path) -> None:
    document = config(project, "--machine")
    assert document["machine"] is True
    assert [row["key"] for row in document["settings"]] == [
        "orchestrator_command",
        "serve_send",
        "serve_idle_message",
    ]
    ids = {d["id"] for d in document["decisions"]}
    assert ids == {"E1", "E2", "E3", "E5"}
    assert {row["class"] for row in document["settings"]} == {"shown", "editable"}


def test_the_text_form_names_each_setting_and_its_class(project: Path) -> None:
    result = invoke(["--root", str(project), "config"])
    assert result.code == 0, result
    assert "worker_model [editable]" in result.out
    assert "kyo config: read only, nothing was written." in result.out


def test_a_comment_and_lines_are_read_from_the_file(project: Path) -> None:
    path = project / "kyosei" / "config.yml"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text + "\n# why: chosen for cost, KYO-61\nworker_model: sonnet  # per the brief\n",
        encoding="utf-8",
    )
    row = {r["key"]: r for r in config(project)["settings"]}["worker_model"]
    assert row["value"] == "sonnet"
    assert row["comment"] == ["# why: chosen for cost, KYO-61"]
    assert row["trailing"] == "# per the brief"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[row["lines"]["first"] - 1].startswith("worker_model:")


# ------------------------------------------------------------- c4: withheld values


COMMAND = 'orchestrator_command: ["env", "API_KEY=hunter2", "claude", "--flag=x", "{prefix}"]\n'


def test_the_cli_prints_the_command_in_full(project: Path) -> None:
    machine_file(COMMAND)
    row = config(project, "--machine")["settings"][0]
    assert row["value"] == ["env", "API_KEY=hunter2", "claude", "--flag=x", "{prefix}"]
    assert "withheld" not in row


def test_the_served_document_withholds_assigned_values(server: Client) -> None:
    machine_file(COMMAND)
    headers = token()
    status, _, document = get(server, "/v1/settings", headers)
    assert status == 200, document
    row = document["settings"][0]
    assert row["value"] == ["env", "API_KEY=(withheld)", "claude", "--flag=x", "{prefix}"]
    assert row["withheld"] == [1]
    assert "hunter2" not in json.dumps(document)
    project_document = get(server, f"{BASE}/config", headers)[2]
    assert "hunter2" not in json.dumps(project_document)


# ------------------------------------------------------------- c3: the routes


@pytest.mark.parametrize("permission", [None, *device_tokens.PERMISSIONS])
def test_a_token_of_any_permission_reads_both_documents(server: Client, permission) -> None:
    headers = token(*([permission] if permission else []))
    status, response, document = get(server, f"{BASE}/config", headers)
    assert status == 200, document
    assert document["device"] == {
        "name": "laptop",
        "permissions": [permission] if permission else [],
    }
    assert document["project"] == "proj"
    assert document["server"]["stale"] is False
    assert response["Cache-Control"] == "private, no-store"
    assert response["ETag"] == document["files"]["config"]["etag"]
    status, response, machine = get(server, "/v1/settings", headers)
    assert status == 200, machine
    assert machine["machine"] is True
    assert response["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("path", [f"{BASE}/config", "/v1/settings"])
def test_a_request_without_a_token_is_refused_as_a_session_read_is(
    server: Client, path: str
) -> None:
    refused = get(server, path)
    session = get(server, f"{BASE}/sessions")
    assert refused[0] == session[0] == 401
    assert refused[2] == session[2]
    assert refused[1]["WWW-Authenticate"] == "Bearer"
    bad = get(server, path, {"Authorization": "Bearer kyo_not-one-of-ours"})
    assert bad[0] == 401 and bad[2]["reason"] == "unknown-token"


def test_the_configuration_routes_take_no_query_and_only_get(server: Client) -> None:
    headers = token()
    assert get(server, f"{BASE}/config?x=1", headers)[0] == 400
    status, response, _ = server.request(
        f"{BASE}/config",
        method="POST",
        headers={**headers, "Content-Type": "application/json"},
        body=b"{}",
    )
    assert status == 405 and response["Allow"] == "GET"


def test_an_unknown_project_is_refused_after_the_token(server: Client) -> None:
    assert get(server, "/v1/projects/nope/config")[0] == 401
    assert get(server, "/v1/projects/nope/config", token())[0] == 404


# ------------------------------------------------------------- c5: NOT_SERVED


def test_the_command_is_not_served_with_its_reason() -> None:
    assert "config" in serve.NOT_SERVED
    assert "GET /v1/projects/{project}/config" in serve.NOT_SERVED["config"]
    assert "config" not in serve.SERVED_COMMANDS
    assert "config" in cli.COMMAND_TABLE
    served, refused = set(serve.SERVED_COMMANDS), set(serve.NOT_SERVED)
    assert set(cli.COMMAND_TABLE) - served - refused == set()


# ------------------------------------------------------------- c6: a refused file


def test_a_file_load_config_refuses_is_still_reported(project: Path, server: Client) -> None:
    path = project / "kyosei" / "config.yml"
    path.write_text(
        path.read_text(encoding="utf-8") + "\nunheard_of_key: 1\nworker_model: sonnet\n",
        encoding="utf-8",
    )
    for document in (
        config(project),
        get(server, f"{BASE}/config", token())[2],
    ):
        report = document["files"]["config"]
        assert report["readable"] is False
        assert "unheard_of_key" in report["problem"]
        row = {r["key"]: r for r in document["settings"]}["worker_model"]
        assert row["value"] == "sonnet"


def test_the_refusal_is_the_one_load_config_gives(project: Path) -> None:
    from kyosei.config import load_config
    from kyosei.errors import KyoseiError

    path = project / "kyosei" / "config.yml"
    path.write_text(path.read_text(encoding="utf-8") + "\nunheard_of_key: 1\n", encoding="utf-8")
    with pytest.raises(KyoseiError) as raised:
        load_config(project)
    assert config(project)["files"]["config"]["problem"] == str(raised.value)


def test_an_unreadable_machine_file_is_reported(project: Path) -> None:
    machine_file("serve_send: maybe\n")
    document = config(project, "--machine")
    assert document["files"]["settings"]["readable"] is False
    assert "serve_send" in document["files"]["settings"]["problem"]
    assert {r["key"]: r for r in document["settings"]}["serve_send"]["value"] == "maybe"


# ------------------------------------------------------------- c7: one-way writes


def test_each_editable_setting_states_whether_it_can_be_undone(project: Path) -> None:
    document = config(project)
    editable = [row for row in document["settings"] if row["class"] == "editable"]
    assert {row["key"] for row in editable} == {
        "worker_model",
        "worker_command_timeout_ceiling_seconds",
        "recx.prior_art.state",
        "read_transcripts",
        "docs_coverage.level",
    }
    for row in editable:
        assert set(row["write"]) == {"route", "value", "clearable", "permission", "one_way"}
        assert row["write"]["permission"] == "config"
    one_way = {row["key"]: row["write"]["one_way"] for row in editable}
    assert one_way.pop("read_transcripts") == config_view.READ_TRANSCRIPTS_ONE_WAY
    assert set(one_way.values()) == {None}
    machine = config(project, "--machine")
    idle = {r["key"]: r for r in machine["settings"]}["serve_idle_message"]
    assert idle["write"]["one_way"] is None
    assert "write" not in {r["key"]: r for r in document["settings"]}["worktree_setup"]


# ------------------------------------------------------------- c8: the config event


def open_events(client: Client) -> EventStream:
    stream = EventStream(client)
    assert stream.next_block() == ["retry: 1000", ": kyo serve events"]
    return stream


@pytest.mark.parametrize(
    "relative",
    ["kyosei/config.yml", "kyosei/scopes.yml", "kyosei/procedures.md", "kyosei/kinds.yml"],
)
def test_a_project_file_is_a_config_event(server: Client, project: Path, relative: str) -> None:
    stream = open_events(server)
    try:
        path = project / relative
        path.write_text(
            (path.read_text(encoding="utf-8") if path.is_file() else "") + "\n# edited\n",
            encoding="utf-8",
        )
        assert stream.next_change() == "config"
    finally:
        stream.close()


def test_an_agent_type_file_and_the_machine_file_are_config_events(
    server: Client, project: Path
) -> None:
    agents = project / ".claude" / "agents"
    agents.mkdir(parents=True)
    stream = open_events(server)
    try:
        (agents / "kyosei-worker.md").write_text("---\nmodel: sonnet\n---\n", encoding="utf-8")
        assert stream.next_change() == "config"
        machine_file("serve_idle_message: false\n")
        assert stream.next_change() == "config"
    finally:
        stream.close()

