"""KYO-453: `kyo serve`, the loopback read server over the CLI's own --json.

Every server here is started in-process on an ephemeral loopback port and stopped by
the test that started it. The requests go over a real socket (``http.client``), so
the guards are exercised on the headers a client actually sends.
"""

from __future__ import annotations

import http.client
import io
import json
import socket
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from kyosei import cli, serve
from kyosei.commands import PROJECT_REQUIRED, Output
from kyosei.errors import KyoseiError
from kyosei.store import open_store

from .conftest import invoke, new_record

# ----------------------------------------------------------------- fixtures


class Client:
    """A running :class:`serve.KyoServer` and a way to ask it things."""

    def __init__(self, server: serve.KyoServer) -> None:
        self.server = server
        self.port = server.port

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        host: str | None = "default",
    ) -> tuple[int, http.client.HTTPMessage, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            if host == "default":
                conn.putheader("Host", f"127.0.0.1:{self.port}")
            elif host is not None:
                conn.putheader("Host", host)
            for key, value in (headers or {}).items():
                conn.putheader(key, value)
            if body is not None:
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            response = conn.getresponse()
            return response.status, response.headers, response.read()
        finally:
            conn.close()

    def get(self, path: str, **kwargs) -> tuple[int, dict, bytes]:
        return self.request(path, **kwargs)


def start(**kwargs) -> Client:
    server = serve.KyoServer(port=0, log_stream=io.StringIO(), **kwargs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return Client(server)


def stop(client: Client) -> None:
    client.server.shutdown()
    client.server.server_close()


@pytest.fixture
def running():
    """Start servers with the given options; every one is stopped at teardown."""
    started: list[Client] = []

    def _start(**kwargs) -> Client:
        client = start(**kwargs)
        started.append(client)
        return client

    yield _start
    for client in started:
        stop(client)


def register(store_home: Path, *roots: Path | str) -> Path:
    store_home.mkdir(parents=True, exist_ok=True)
    path = store_home / serve.REGISTRY_FILE
    path.write_text(
        json.dumps({"projects": [{"root": str(root)} for root in roots]}), encoding="utf-8"
    )
    return path


@pytest.fixture
def served(project: Path, store_home: Path, running, kyo):
    """The fixture project, with one record, registered and served."""
    new_record(kyo, kind="bug", title="Served record", criteria=["it is served"])
    register(store_home, project)
    return running()


def body_json(body: bytes) -> dict:
    return json.loads(body.decode("utf-8"))


# ------------------------------------------------------------ c1: the bind


@pytest.mark.parametrize("address", ["0.0.0.0", "192.168.1.5", "example.com", "::"])
def test_a_non_loopback_bind_is_refused_naming_tailscale_serve(address: str) -> None:
    result = invoke(["serve", "--bind", address, "--port", "0"])
    assert result.code == 1, result
    assert "loopback" in result.err and "Tailscale Serve" in result.err
    assert "tailscale funnel" in result.err
    with pytest.raises(KyoseiError, match="Tailscale Serve"):
        serve.check_bind(address)


def test_the_loopback_bind_the_refusal_names_is_accepted(running) -> None:
    # The remedy the refusal names ("Bind 127.0.0.1 (the default) or ::1") clears it.
    for address in ("127.0.0.1", "localhost"):
        client = running(bind=address)
        assert client.server.server_address[0] == "127.0.0.1"
        status, _, _ = client.get("/v1/version")
        assert status == 200


def test_ipv6_loopback_is_accepted_where_the_host_has_it(running) -> None:
    if not socket.has_ipv6:
        pytest.skip("no IPv6 on this host")
    try:
        client = running(bind="::1")
    except KyoseiError:
        pytest.skip("::1 is not configured on this host")
    conn = http.client.HTTPConnection("::1", client.port, timeout=30)
    conn.request("GET", "/v1/version", headers={"Host": f"[::1]:{client.port}"})
    assert conn.getresponse().status == 200
    conn.close()


def test_every_command_the_parser_builds_is_the_same_command_serve_runs() -> None:
    args = cli.build_parser().parse_args(["serve"])
    assert args.port == serve.DEFAULT_PORT == 47370
    assert args.bind == serve.DEFAULT_BIND == "127.0.0.1"
    assert cli.COMMAND_TABLE["serve"][0] is serve.cmd_serve


# ------------------------------------------------------- c1: /v1/version


def test_version_carries_kyo_version_and_the_header(served: Client) -> None:
    status, headers, body = served.get("/v1/version")
    assert status == 200
    document = body_json(body)
    assert document["kyosei"] == invoke(["version", "--json"]).json
    assert document["schema_version"] == document["kyosei"]["schema_version"]
    assert document["pid"] > 0 and document["started_at"].endswith("Z")
    assert document["stale"] is False
    assert headers[serve.VERSION_HEADER] == serve.version_header(document["kyosei"])


def test_version_header_forms() -> None:
    assert serve.version_header({"version": "0.2.0", "release": True, "commit_short": "a"}) == (
        "0.2.0"
    )
    assert (
        serve.version_header({"version": "0.2.0", "release": False, "commit_short": "c99aab56"})
        == "0.2.0+c99aab56"
    )
    assert serve.version_header({"version": "0.2.0", "commit_short": None}) == "0.2.0"


def test_stale_says_the_installed_build_is_not_the_running_one(served: Client) -> None:
    served.server.started_version = {
        **served.server.started_version,
        "commit": "0" * 40,
        "line": "an older build",
    }
    document = body_json(served.get("/v1/version")[2])
    assert document["stale"] is True
    assert "an older build" in document["stale_detail"]
    assert "start it again" in document["stale_detail"]


# ------------------------------------------------------ c1: /v1/projects


def test_projects_lists_each_registry_entry_through_kyo_where(
    served: Client, project: Path
) -> None:
    status, _, body = served.get("/v1/projects")
    assert status == 200
    document = body_json(body)
    where = invoke(["where", "--root", str(project), "--json"]).json
    assert document["projects"] == [
        {
            "name": "proj",
            "root": str(project),
            "store_root": where["store_root"],
            "prefix": "TST",
            "can_open": True,
            "problem": None,
        }
    ]


def test_an_entry_that_cannot_open_is_listed_with_its_problem(
    served: Client, project: Path, store_home: Path, tmp_path: Path
) -> None:
    register(store_home, project, tmp_path / "gone")
    listed = body_json(served.get("/v1/projects")[2])["projects"]
    assert [p["name"] for p in listed] == ["proj", "gone"]
    assert listed[1]["can_open"] is False and listed[1]["problem"]


def test_a_missing_registry_is_the_empty_list(running) -> None:
    document = body_json(running().get("/v1/projects")[2])
    assert document["projects"] == []


def test_an_unreadable_registry_is_refused_and_fixing_it_clears_that(
    served: Client, project: Path, store_home: Path
) -> None:
    path = store_home / serve.REGISTRY_FILE
    path.write_text("{not json", encoding="utf-8")
    for route in ("/v1/projects", "/v1/projects/proj/cmd/next"):
        status, headers, body = served.get(route)
        assert status == 500
        document = body_json(body)
        assert document["refused_by"] == "kyo serve"
        assert str(path) in document["error"] and "correct it by hand" in document["error"]
        assert serve.VERSION_HEADER in headers
    register(store_home, project)  # the remedy: correct the file
    assert served.get("/v1/projects/proj/cmd/next")[0] == 200


def test_an_unknown_project_names_the_registry_and_adding_it_clears_that(
    running, project: Path, store_home: Path
) -> None:
    register(store_home)  # KYO-455: `kyo init` registered it; start unregistered
    client = running()
    status, _, body = client.get("/v1/projects/proj/cmd/next")
    assert status == 404
    assert "/v1/projects" in body_json(body)["error"]
    register(store_home, project)  # the remedy: its root added to the file
    assert client.get("/v1/projects/proj/cmd/next")[0] == 200


# ------------------------------------------------ c1: byte for byte --json


SERVED_ROUTES = [
    ("show/TST-1", ["show", "TST-1"]),
    ("show/TST-1?full", ["show", "TST-1", "--full"]),
    ("list", ["list"]),
    ("list?status=open&status=done", ["list", "--status", "open", "--status", "done"]),
    ("map", ["map"]),
    ("next", ["next"]),
    ("where", ["where"]),
    ("version", ["version"]),
    ("rules", ["rules"]),
    ("reference/show", ["reference", "show"]),
    ("report-schema", ["report-schema"]),
    ("base/list", ["base", "list"]),
    ("docs/check", ["docs", "check"]),
    ("docs/obligations", ["docs", "obligations"]),
    ("worktrees", ["worktrees"]),
    ("sessions", ["sessions"]),
]


@pytest.mark.parametrize(("route", "argv"), SERVED_ROUTES, ids=[r for r, _ in SERVED_ROUTES])
def test_a_served_body_is_byte_for_byte_the_clis_json(
    served: Client, project: Path, route: str, argv: list[str]
) -> None:
    status, headers, body = served.get(f"/v1/projects/proj/cmd/{route}")
    expected = invoke([*argv, "--root", str(project), "--json"])
    assert body.decode("utf-8") == expected.out
    assert status == (200 if expected.code == 0 else 422)
    assert headers["Content-Type"].startswith("application/json")
    assert headers[serve.VERSION_HEADER] == serve.version_header(served.server.started_version)


def test_the_clis_error_envelope_is_served_verbatim_as_422(
    served: Client, project: Path
) -> None:
    status, _, body = served.get("/v1/projects/proj/cmd/show/TST-999")
    expected = invoke(["show", "TST-999", "--root", str(project), "--json"])
    assert expected.code == 1
    assert status == 422
    assert body.decode("utf-8") == expected.out


def test_a_usage_error_is_a_400_carrying_the_parsers_words(served: Client) -> None:
    status, _, body = served.get("/v1/projects/proj/cmd/list?no-such-flag")
    assert status == 400
    document = body_json(body)
    assert document["exit_code"] == 64 and "--no-such-flag" in document["error"]


def test_every_response_carries_the_version_header(served: Client) -> None:
    for path in (
        "/v1/version",
        "/nowhere",
        "/v1/projects/nope/cmd/next",
        "/v1/projects/proj/cmd/brief",
        "/v1/projects/proj/cmd/list?bogus",
    ):
        _, headers, _ = served.get(path)
        assert headers[serve.VERSION_HEADER], path
    _, headers, _ = served.get("/v1/version", host="evil.example")
    assert headers[serve.VERSION_HEADER]


def test_an_unknown_route_is_a_404_naming_the_routes(served: Client) -> None:
    status, _, body = served.get("/v2/version")
    assert status == 404
    assert "/v1/version" in body_json(body)["error"]


# ---------------------------------------- c2: in-process, locked, read-only


def test_run_returns_what_main_prints(project: Path) -> None:
    argv = ["--root", str(project), "where", "--json"]
    printed = invoke(argv)
    code, payload = _quiet_run(argv)
    assert code == printed.code == 0
    assert payload == printed.json
    code, payload = _quiet_run(["--root", str(project), "where"])
    assert code == 0 and payload is None


def _quiet_run(argv: list[str], **kwargs):
    import contextlib

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return cli.run(argv, **kwargs)


def test_served_commands_run_through_cli_run_read_only(served: Client, monkeypatch) -> None:
    calls = []
    real = cli.run

    def spy(argv, environ=None, *, read_only=False):
        calls.append((list(argv), read_only))
        return real(argv, environ, read_only=read_only)

    monkeypatch.setattr(cli, "run", spy)
    assert served.get("/v1/projects/proj/cmd/next")[0] == 200
    argv, read_only = calls[-1]
    assert argv[2] == "next" and argv[-1] == "--json" and read_only is True


def test_a_write_through_a_transaction_is_refused_naming_kyo_serve(project: Path, db) -> None:
    before = db().execute("SELECT COUNT(*) FROM events").fetchone()[0]
    code, payload = _quiet_run(["--root", str(project), "sweep", "--json"], read_only=True)
    assert code == 1
    assert "kyo serve" in payload["error"] and "read-only" in payload["error"]
    assert db().execute("SELECT COUNT(*) FROM events").fetchone()[0] == before
    # Without read_only the same command writes, so the refusal is the flag's doing.
    assert _quiet_run(["--root", str(project), "sweep", "--json"])[0] == 0


def test_the_transaction_refusal_is_the_stores_own(project: Path) -> None:
    store = open_store(project, read_only=True)
    try:
        with pytest.raises(KyoseiError, match="kyo serve"), store.transaction():
            pass
    finally:
        store.close()


def test_a_write_that_skips_the_transaction_is_refused_by_query_only(
    project: Path, monkeypatch
) -> None:
    def writes_directly(ctx, args):
        ctx.store().conn.execute("INSERT INTO meta (key, value) VALUES ('x', 'y')")
        return Output(["wrote"], {"ok": True, "command": "map"})

    monkeypatch.setitem(cli.COMMAND_TABLE, "map", (writes_directly, PROJECT_REQUIRED))
    code, payload = _quiet_run(["--root", str(project), "map", "--json"], read_only=True)
    assert code == 1
    assert "kyo serve" in payload["error"] and "kyo map tried" in payload["error"]
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM meta WHERE key = 'x'").fetchone()[0] == 0
    finally:
        conn.close()


def test_commands_wait_on_the_one_lock(served: Client) -> None:
    results: list[int] = []
    served.server.lock.acquire()
    try:
        thread = threading.Thread(
            target=lambda: results.append(served.get("/v1/projects/proj/cmd/next")[0])
        )
        thread.start()
        time.sleep(0.5)
        assert results == [], "a command ran while the lock was held"
    finally:
        served.server.lock.release()
    thread.join(timeout=60)
    assert results == [200]


def test_concurrent_requests_each_get_their_own_output(served: Client, project: Path) -> None:
    expected = {
        "next": invoke(["next", "--root", str(project), "--json"]).out,
        "show/TST-1": invoke(["show", "TST-1", "--root", str(project), "--json"]).out,
    }
    got: list[tuple[str, str]] = []

    def fetch(route: str) -> None:
        got.append((route, served.get(f"/v1/projects/proj/cmd/{route}")[2].decode("utf-8")))

    threads = [threading.Thread(target=fetch, args=(r,)) for r in list(expected) * 4]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert len(got) == 8
    for route, body in got:
        assert body == expected[route]


# ----------------------------------------------- c3: the classification


def test_every_command_is_classified_exactly_once() -> None:
    served, refused = set(serve.SERVED_COMMANDS), set(serve.NOT_SERVED)
    table = set(cli.COMMAND_TABLE)
    assert not served & refused, served & refused
    assert table - served - refused == set(), (
        "unclassified: add each to serve.SERVED_COMMANDS or serve.NOT_SERVED"
    )
    assert (served | refused) - table == set(), "classified but no longer a command"


def test_no_mutating_command_is_served_except_by_its_read_actions() -> None:
    for name in set(serve.SERVED_COMMANDS) & set(cli.MUTATING_COMMANDS):
        actions = serve.SERVED_COMMANDS[name].actions
        assert actions is not None, f"{name} writes and is served without an action limit"
    assert serve.SERVED_COMMANDS["docs"].actions == {"check", "obligations", "preview"}
    assert set(serve.SERVED_COMMANDS) & set(cli.MUTATING_COMMANDS) == {"docs"}


def test_the_classification_test_fails_on_an_unclassified_command(monkeypatch) -> None:
    monkeypatch.setitem(cli.COMMAND_TABLE, "brand-new", cli.COMMAND_TABLE["show"])
    with pytest.raises(AssertionError, match="unclassified"):
        test_every_command_is_classified_exactly_once()


def test_the_classification_test_fails_on_a_served_mutating_command(monkeypatch) -> None:
    monkeypatch.setitem(serve.SERVED_COMMANDS, "new", serve.Served())
    monkeypatch.delitem(serve.NOT_SERVED, "new")
    with pytest.raises(AssertionError, match="new writes"):
        test_no_mutating_command_is_served_except_by_its_read_actions()


REFUSED_REQUESTS = [
    "brief",
    "sweep",
    "new/bug",
    "docs/settle",
    "docs/assemble",
    "docs/suggest",
    "worktrees?prune",
    "worktrees?pru",
    "worktrees?prune&include-unmerged",
    "sessions?hook",
    "reference?output=written.md",
    "changelog?output=written.md",
    "show/TST-1?root=/elsewhere",
    "show/TST-1?roo=/elsewhere",
    "show/TST-1?actor=someone",
    "show/TST-1?act=someone",
    "show/TST-1?json",
    "show/TST-1?help",
    "show/TST-1?he",
    "worktrees/--prune",
]


@pytest.mark.parametrize("route", REFUSED_REQUESTS)
def test_a_refused_command_action_or_flag_never_runs(
    served: Client, route: str, monkeypatch, project: Path, tmp_path: Path, db
) -> None:
    ran = []
    real = cli.run
    monkeypatch.setattr(cli, "run", lambda *a, **k: ran.append(a) or real(*a, **k))
    runs_before = db().execute("SELECT COUNT(*) FROM brief_runs").fetchone()[0]
    status, _, body = served.get(f"/v1/projects/proj/cmd/{route}")
    assert status in (400, 403), (status, body)
    document = body_json(body)
    assert document["ok"] is False and document["refused_by"] == "kyo serve"
    assert ran == [], f"{route} reached cli.run"
    assert db().execute("SELECT COUNT(*) FROM brief_runs").fetchone()[0] == runs_before
    assert not list(tmp_path.rglob("written.md"))


def test_a_refused_command_names_running_it_in_a_terminal(served: Client, project: Path) -> None:
    body = body_json(served.get("/v1/projects/proj/cmd/brief")[2])
    assert "`kyo brief`" in body["error"] and "terminal" in body["error"]
    # The remedy it names: the same command, run in a terminal, runs.
    assert invoke(["--root", str(project), "brief", "--json"]).code == 0


def test_an_unknown_command_names_the_served_ones(served: Client) -> None:
    status, _, body = served.get("/v1/projects/proj/cmd/lsit")
    assert status == 404
    assert "show" in body_json(body)["error"]


# --------------------------------------------- c4: Host, Origin, Content-Type


def test_a_foreign_host_is_refused_and_allow_host_clears_it(served: Client, running) -> None:
    status, _, body = served.get("/v1/version", host="evil.example")
    assert status == 403
    error = body_json(body)["error"]
    assert "DNS rebinding" in error and "--allow-host evil.example" in error
    allowed = running(allow_hosts=("evil.example",))
    assert allowed.get("/v1/version", host="evil.example")[0] == 200
    assert allowed.get("/v1/version", host=f"evil.example:{allowed.port}")[0] == 200
    assert allowed.get("/v1/version", host="other.example")[0] == 403


def test_a_missing_host_is_refused(served: Client) -> None:
    assert served.get("/v1/version", host=None)[0] == 403


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_the_loopback_names_are_answered(served: Client, host: str) -> None:
    assert served.get("/v1/version", host=f"{host}:{served.port}")[0] == 200
    assert served.get("/v1/version", host=host)[0] == 200


def test_a_foreign_origin_is_refused_and_allow_origin_clears_it(
    served: Client, running
) -> None:
    origin = "https://evil.example"
    status, headers, body = served.get("/v1/version", headers={"Origin": origin})
    assert status == 403
    assert f"--allow-origin {origin}" in body_json(body)["error"]
    assert "Access-Control-Allow-Origin" not in headers
    allowed = running(allow_origins=(origin,))
    status, headers, _ = allowed.get("/v1/version", headers={"Origin": origin})
    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == origin
    assert allowed.get("/v1/version", headers={"Origin": "https://other.example"})[0] == 403


def test_no_origin_is_the_desktop_clients_case_and_is_answered(served: Client) -> None:
    status, headers, _ = served.get("/v1/version")
    assert status == 200
    assert "Access-Control-Allow-Origin" not in headers


@pytest.mark.parametrize(
    "content_type", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data", None]
)
def test_a_post_without_json_is_refused_and_json_clears_that(
    served: Client, content_type: str | None
) -> None:
    headers = {"Content-Type": content_type} if content_type else {}
    status, _, body = served.request(
        "/v1/version", method="POST", headers=headers, body=b"{}"
    )
    assert status == 415
    assert "Content-Type: application/json" in body_json(body)["error"]
    # The remedy clears the Content-Type refusal; what answers next is the route's own
    # word, because every route the read server serves is a GET.
    status, _, body = served.request(
        "/v1/version",
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
        body=b"{}",
    )
    assert status == 405
    assert "GET" in body_json(body)["error"]
