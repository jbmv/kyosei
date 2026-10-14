"""KYO-456: `kyo serve`'s write boundary and its change events.

The write boundary: every file the HTTP layer reads or writes goes through
``kyosei/serve_files.py``, resolved to its real path inside the project's ``ideas/`` (or
the one scratchpad path), with ETags guarding every replace and delete. The symlink
attack is planted for real: ``ideas/x -> ../kyosei`` in a temp project, and every route
is sent through it.

The change events: ``/events`` streams Server-Sent Events from a stat fingerprint. Every
stream here is read with a socket timeout, so a missing event fails the test instead of hanging it,
and every server is stopped by the ``running`` fixture that started it.
"""

from __future__ import annotations

import ast
import http.client
import json
import os
import time
from pathlib import Path

import pytest

import kyosei
from kyosei import serve, serve_events, serve_files

from .conftest import new_record
from .test_kyo_serve import Client, body_json, register, start, stop

# ----------------------------------------------------------------- fixtures


@pytest.fixture
def running():
    """Start servers; every one is stopped at teardown (test_kyo_serve.py's own)."""
    started: list[Client] = []

    def _start(**kwargs) -> Client:
        client = start(**kwargs)
        started.append(client)
        return client

    yield _start
    for client in started:
        stop(client)


@pytest.fixture
def files(project: Path, store_home: Path, running) -> Client:
    """The fixture project registered and served, with a short event cadence."""
    register(store_home, project)
    client = running()
    client.server.poll_seconds = 0.05
    client.server.keepalive_seconds = 0.4
    return client


BASE = "/v1/projects/proj"


def put(client: Client, path: str, data: bytes, **headers: str):
    return client.request(f"{BASE}/{path}", method="PUT", headers=headers, body=data)


def post(client: Client, route: str, document: dict):
    return client.request(
        f"{BASE}/{route}",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps(document).encode(),
    )


def delete(client: Client, path: str, **headers: str):
    return client.request(f"{BASE}/{path}", method="DELETE", headers=headers)


def plant_store_symlink(project: Path) -> Path:
    """The symlink attack the write boundary must stop: ``ideas/x -> ../kyosei``."""
    link = project / "ideas" / "x"
    link.symlink_to(Path("..") / "kyosei")
    assert (link / "config.yml").is_file()
    return link


# ----------------------------------------------- c1: the routes, through one module


def test_create_read_replace_and_delete_an_idea(files: Client, project: Path) -> None:
    status, headers, body = put(files, "ideas/new.md", b"# New\n", **{"If-None-Match": "*"})
    assert status == 201, body
    created = body_json(body)
    assert created["created"] is True and headers["ETag"] == created["etag"]
    assert (project / "ideas" / "new.md").read_bytes() == b"# New\n"

    status, headers, body = files.get(f"{BASE}/ideas/new.md")
    assert status == 200 and body == b"# New\n"
    assert headers["ETag"] == created["etag"]
    assert headers["Content-Type"].startswith("text/markdown")

    status, headers, body = put(
        files, "ideas/new.md", b"# Newer\n", **{"If-Match": created["etag"]}
    )
    assert status == 200, body
    assert (project / "ideas" / "new.md").read_bytes() == b"# Newer\n"
    newer = body_json(body)["etag"]

    status, _, body = delete(files, "ideas/new.md", **{"If-Match": newer})
    assert status == 200, body
    assert not (project / "ideas" / "new.md").exists()
    # No temporary file was left beside it.
    assert not [p for p in (project / "ideas").iterdir() if p.name.endswith(".tmp")]


def test_a_create_in_a_new_folder_makes_the_folder_inside_ideas(
    files: Client, project: Path
) -> None:
    status, _, body = put(files, "ideas/deep/er/n.md", b"x", **{"If-None-Match": "*"})
    assert status == 201, body
    assert (project / "ideas" / "deep" / "er" / "n.md").read_bytes() == b"x"


def test_a_put_with_no_precondition_is_refused_and_if_match_clears_it(
    files: Client, project: Path
) -> None:
    target = project / "ideas" / "a.md"
    target.write_bytes(b"one")
    status, _, body = put(files, "ideas/a.md", b"two")
    assert status == 428
    assert "If-Match" in body_json(body)["error"]
    assert target.read_bytes() == b"one"
    _, headers, _ = files.get(f"{BASE}/ideas/a.md")
    status, _, body = put(files, "ideas/a.md", b"two", **{"If-Match": headers["ETag"]})
    assert status == 200, body
    assert target.read_bytes() == b"two"


def test_a_stale_etag_is_a_412_carrying_the_current_one(files: Client, project: Path) -> None:
    target = project / "ideas" / "a.md"
    target.write_bytes(b"one")
    _, headers, _ = files.get(f"{BASE}/ideas/a.md")
    stale = headers["ETag"]
    target.write_bytes(b"someone else's edit")  # the other viewer saved first
    status, _, body = put(files, "ideas/a.md", b"mine", **{"If-Match": stale})
    assert status == 412
    refusal = body_json(body)
    assert refusal["etag"] == serve_files.etag_of(b"someone else's edit")
    assert target.read_bytes() == b"someone else's edit"
    # The remedy it names -- PUT with the current etag -- clears it.
    status, _, _ = put(files, "ideas/a.md", b"mine", **{"If-Match": refusal["etag"]})
    assert status == 200
    assert target.read_bytes() == b"mine"


def test_a_create_never_overwrites(files: Client, project: Path) -> None:
    target = project / "ideas" / "a.md"
    target.write_bytes(b"there already")
    status, _, body = put(files, "ideas/a.md", b"new", **{"If-None-Match": "*"})
    assert status == 412
    assert body_json(body)["etag"] == serve_files.etag_of(b"there already")
    assert target.read_bytes() == b"there already"


def test_if_match_on_a_missing_file_names_the_create(files: Client) -> None:
    status, _, body = put(files, "ideas/none.md", b"x", **{"If-Match": '"abc"'})
    assert status == 412
    assert "If-None-Match: *" in body_json(body)["error"]


def test_a_missing_file_is_a_404_naming_the_create(files: Client) -> None:
    status, _, body = files.get(f"{BASE}/ideas/none.md")
    assert status == 404
    assert "If-None-Match: *" in body_json(body)["error"]
    status, _, _ = put(files, "ideas/none.md", b"x", **{"If-None-Match": "*"})
    assert status == 201
    status, _, _ = files.get(f"{BASE}/ideas/none.md")
    assert status == 200


def test_a_delete_needs_if_match_and_the_etag_clears_it(files: Client, project: Path) -> None:
    target = project / "ideas" / "a.md"
    target.write_bytes(b"one")
    status, _, body = delete(files, "ideas/a.md")
    assert status == 428 and target.exists()
    status, _, _ = delete(files, "ideas/a.md", **{"If-Match": '"stale"'})
    assert status == 412 and target.exists()
    status, _, _ = delete(files, "ideas/a.md", **{"If-Match": serve_files.etag_of(b"one")})
    assert status == 200 and not target.exists()


def test_a_folder_is_deleted_only_when_empty(files: Client, project: Path) -> None:
    folder = project / "ideas" / "f"
    folder.mkdir()
    (folder / "a.md").write_bytes(b"a")
    status, _, body = delete(files, "ideas/f")
    assert status == 409 and "delete each file" in body_json(body)["error"]
    (folder / "a.md").unlink()
    status, _, _ = delete(files, "ideas/f")
    assert status == 200 and not folder.exists()


def test_the_tree_lists_every_path_with_size_mtime_and_etag(
    files: Client, project: Path
) -> None:
    (project / "ideas" / "sub").mkdir()
    (project / "ideas" / "sub" / "b.md").write_bytes(b"bee")
    (project / "ideas" / "pic.png").write_bytes(b"\x89PNG")
    status, _, body = files.get(f"{BASE}/ideas/")
    assert status == 200
    tree = body_json(body)
    by_path = {entry["path"]: entry for entry in tree["entries"]}
    assert by_path["sub"]["type"] == "dir"
    assert by_path["sub/b.md"] == {
        "path": "sub/b.md",
        "type": "file",
        "size": 3,
        "mtime_ns": (project / "ideas" / "sub" / "b.md").stat().st_mtime_ns,
        "created_ns": serve_files.created_ns((project / "ideas" / "sub" / "b.md").stat()),
        "etag": serve_files.etag_of(b"bee"),
    }
    assert by_path["pic.png"]["type"] == "file"
    # The same tree without the trailing slash.
    assert body_json(files.get(f"{BASE}/ideas")[2])["entries"] == tree["entries"]


def test_move_renames_inside_ideas_and_never_overwrites(files: Client, project: Path) -> None:
    ideas = project / "ideas"
    (ideas / "a.md").write_bytes(b"a")
    (ideas / "b.md").write_bytes(b"b")
    status, _, body = post(files, "ideas-move", {"from": "a.md", "to": "b.md"})
    assert status == 412 and (ideas / "b.md").read_bytes() == b"b"
    status, _, body = post(files, "ideas-move", {"from": "a.md", "to": "moved/c.md"})
    assert status == 200, body
    assert (ideas / "moved" / "c.md").read_bytes() == b"a" and not (ideas / "a.md").exists()


def test_mkdir_creates_a_folder_inside_ideas(files: Client, project: Path) -> None:
    status, _, body = post(files, "ideas-mkdir", {"path": "one/two"})
    assert status == 201, body
    assert (project / "ideas" / "one" / "two").is_dir()
    status, _, body = post(files, "ideas-mkdir", {"path": "one/two"})
    assert status == 200 and body_json(body)["created"] is False


def test_the_scratchpad_is_the_one_path_in_the_per_project_home(
    files: Client, store_home: Path
) -> None:
    pad = store_home / "proj" / serve_files.SCRATCHPAD_NAME
    status, _, _ = files.get(f"{BASE}/scratchpad")
    assert status == 404
    status, headers, body = put(files, "scratchpad", b"today\n", **{"If-None-Match": "*"})
    assert status == 201, body
    assert pad.read_bytes() == b"today\n"
    status, got, body = files.get(f"{BASE}/scratchpad")
    assert status == 200 and body == b"today\n" and got["ETag"] == headers["ETag"]
    status, _, _ = put(files, "scratchpad", b"later\n", **{"If-Match": got["ETag"]})
    assert status == 200 and pad.read_bytes() == b"later\n"


def test_the_scratchpad_takes_no_path_and_no_delete(files: Client) -> None:
    status, _, _ = files.get(f"{BASE}/scratchpad/other.md")
    assert status == 404
    status, headers, _ = delete(files, "scratchpad")
    assert status == 405 and headers["Allow"] == "GET, PUT"


def test_a_scratchpad_symlink_is_refused(files: Client, store_home: Path, project: Path) -> None:
    home = store_home / "proj"
    home.mkdir(parents=True)
    (home / serve_files.SCRATCHPAD_NAME).symlink_to(project / "kyosei" / "config.yml")
    before = (project / "kyosei" / "config.yml").read_bytes()
    status, _, body = put(files, "scratchpad", b"pwned", **{"If-Match": "*"})
    assert status == 403, body
    status, _, _ = files.get(f"{BASE}/scratchpad")
    assert status == 403
    assert (project / "kyosei" / "config.yml").read_bytes() == before


def test_file_routes_name_a_registered_project(files: Client) -> None:
    status, _, body = files.get("/v1/projects/nobody/ideas/")
    assert status == 404 and "registry" in body_json(body)["error"]


def test_a_post_to_the_file_routes_still_needs_json(files: Client) -> None:
    status, _, _ = files.request(
        f"{BASE}/ideas-mkdir", method="POST", headers={"Content-Type": "text/plain"}, body=b"{}"
    )
    assert status == 415


# ------------------------------------------------------- c2: the planted attacks


def test_the_symlink_into_kyosei_is_refused_on_every_route(
    files: Client, project: Path
) -> None:
    plant_store_symlink(project)
    config = project / "kyosei" / "config.yml"
    before = config.read_bytes()
    etag = serve_files.etag_of(before)

    status, _, body = files.get(f"{BASE}/ideas/x/config.yml")
    assert status == 403
    error = body_json(body)["error"]
    assert "kyosei/ directory" in error and "law 12" in error

    status, _, _ = put(files, "ideas/x/config.yml", b"pwned", **{"If-Match": etag})
    assert status == 403
    status, _, _ = put(files, "ideas/x/new.md", b"pwned", **{"If-None-Match": "*"})
    assert status == 403
    status, _, _ = delete(files, "ideas/x/config.yml", **{"If-Match": etag})
    assert status == 403
    status, _, _ = post(files, "ideas-move", {"from": "x/config.yml", "to": "stolen.yml"})
    assert status == 403
    (project / "ideas" / "idea.md").write_bytes(b"i")
    status, _, _ = post(files, "ideas-move", {"from": "idea.md", "to": "x/idea.md"})
    assert status == 403
    status, _, _ = post(files, "ideas-mkdir", {"path": "x/dir"})
    assert status == 403

    assert config.read_bytes() == before
    assert not (project / "kyosei" / "new.md").exists()
    assert not (project / "kyosei" / "idea.md").exists()
    assert not (project / "kyosei" / "dir").exists()
    assert (project / "ideas" / "idea.md").exists()


def test_a_file_symlink_into_kyosei_is_refused(files: Client, project: Path) -> None:
    (project / "ideas" / "c.md").symlink_to(project / "kyosei" / "config.yml")
    status, _, body = files.get(f"{BASE}/ideas/c.md")
    assert status == 403 and "kyosei/ directory" in body_json(body)["error"]


def test_the_tree_does_not_walk_through_the_symlink(files: Client, project: Path) -> None:
    plant_store_symlink(project)
    tree = body_json(files.get(f"{BASE}/ideas/")[2])
    by_path = {entry["path"]: entry for entry in tree["entries"]}
    assert by_path["x"] == {
        "path": "x",
        "type": "symlink",
        "mtime_ns": by_path["x"]["mtime_ns"],
        "created_ns": by_path["x"]["created_ns"],
    }
    assert not [p for p in by_path if p.startswith("x/")]


def test_a_symlink_that_stays_inside_ideas_is_followed(files: Client, project: Path) -> None:
    (project / "ideas" / "real").mkdir()
    (project / "ideas" / "real" / "r.md").write_bytes(b"r")
    (project / "ideas" / "alias").symlink_to("real")
    status, _, body = files.get(f"{BASE}/ideas/alias/r.md")
    assert status == 200 and body == b"r"


@pytest.mark.parametrize(
    "raw",
    [
        "../kyosei/config.yml",  # a .. escape, sent as-is
        "sub/../../kyosei/config.yml",
        "%2E%2E/kyosei/config.yml",  # encoded dots
        "..%2Fkyosei%2Fconfig.yml",  # an encoded slash inside one segment
        "a%00.md",  # a NUL
        "%2Fetc%2Fpasswd",  # an absolute path
        "a//b.md",  # an empty segment
        "./a.md",
    ],
)
def test_a_path_that_is_not_inside_ideas_is_refused_before_the_disk(
    files: Client, project: Path, raw: str
) -> None:
    config = project / "kyosei" / "config.yml"
    before = config.read_bytes()
    for method in ("GET", "PUT", "DELETE"):
        status, _, body = files.request(
            f"{BASE}/ideas/{raw}",
            method=method,
            headers={"If-Match": serve_files.etag_of(before)},
            body=b"pwned" if method == "PUT" else None,
        )
        assert status == 400, (method, raw, body)
        assert "is not a path inside ideas/" in body_json(body)["error"]
    assert config.read_bytes() == before


@pytest.mark.parametrize("bad", ["/etc/passwd", "../kyosei", "a\x00b", "", "a//b"])
def test_move_and_mkdir_refuse_the_same_paths(files: Client, bad: str) -> None:
    status, _, _ = post(files, "ideas-mkdir", {"path": bad})
    assert status == 400
    status, _, _ = post(files, "ideas-move", {"from": bad, "to": "ok.md"})
    assert status == 400
    status, _, _ = post(files, "ideas-move", {"from": "ok.md", "to": bad})
    assert status == 400


@pytest.mark.parametrize(
    "relpath",
    ["/abs", "C:/win", "\\share", "a\x00", "", "a/", "a//b", "..", "a/../b", "./a"],
)
def test_check_relpath_refuses_as_text(relpath: str) -> None:
    with pytest.raises(serve.Refusal) as caught:
        serve_files.check_relpath(relpath)
    assert caught.value.status == 400


def test_ideas_itself_is_not_a_target(project: Path, store_home: Path) -> None:
    paths = serve_files.project_paths(str(project), {"KYOSEI_HOME": str(store_home)})
    assert serve_files.ideas_target(paths, "a/b.md") == Path(
        os.path.realpath(project / "ideas" / "a" / "b.md")
    )
    with pytest.raises(serve.Refusal):
        serve_files.ideas_target(paths, "..")


# ------------------------------------------- c3: no other serve module writes a file

#: Called on anything, these write (or create, move or remove) a file or directory.
_WRITES_ANYWHERE = {
    "write_text",
    "write_bytes",
    "rename",
    "unlink",
    "rmtree",
    "mkdir",
    "rmdir",
    "touch",
    "symlink_to",
    "hardlink_to",
    "link_to",
    "mkstemp",
    "mkdtemp",
    "NamedTemporaryFile",
    "TemporaryFile",
    "SpooledTemporaryFile",
    "fdopen",
}
#: These names mean something else on other types (``list.remove``, ``dict.copy``),
#: so they count only on the module that writes with them.
_WRITES_ON = {
    "os": {"open", "remove", "removedirs", "makedirs", "link", "symlink", "replace",
           "truncate", "chmod", "mkfifo", "mknod", "rename", "renames", "unlink", "rmdir"},
    "shutil": {"move", "copy", "copy2", "copyfile", "copytree", "copymode", "copystat",
               "rmtree", "chown"},
}


def _mode_writes(node: ast.expr | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return any(flag in node.value for flag in "wax+")
    return True  # a mode this check cannot read is counted as a write


def file_writes(source: str, filename: str = "<source>") -> list[str]:
    """Every call in ``source`` that writes a file, as ``line: call``."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source, filename)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        mode_kw = next((k.value for k in node.keywords if k.arg == "mode"), None)
        if isinstance(func, ast.Name):
            name = func.id
            if name == "open" and _mode_writes(
                node.args[1] if len(node.args) > 1 else mode_kw
            ):
                found.append(f"{node.lineno}: open(… writing)")
            elif name in _WRITES_ANYWHERE:
                found.append(f"{node.lineno}: {name}()")
            continue
        if not isinstance(func, ast.Attribute):
            continue
        name = func.attr
        owner = func.value.id if isinstance(func.value, ast.Name) else None
        if owner in _WRITES_ON and name in _WRITES_ON[owner]:
            found.append(f"{node.lineno}: {owner}.{name}()")
        elif name in _WRITES_ANYWHERE:
            found.append(f"{node.lineno}: .{name}()")
        elif name == "replace" and len(node.args) == 1 and not node.keywords:
            # Path.replace(target); str.replace always takes two arguments.
            found.append(f"{node.lineno}: .replace(target)")
        elif name == "open":
            if owner == "io" and _mode_writes(node.args[1] if len(node.args) > 1 else mode_kw):
                found.append(f"{node.lineno}: io.open(… writing)")
            elif owner != "io" and _mode_writes(node.args[0] if node.args else mode_kw):
                found.append(f"{node.lineno}: .open(… writing)")
    return found


def serve_modules() -> list[Path]:
    return sorted(Path(kyosei.__file__).parent.glob("serve*.py"))


def test_no_serve_module_but_serve_files_writes_a_file() -> None:
    modules = {path.name: path for path in serve_modules()}
    assert {"serve.py", "serve_files.py", "serve_events.py"} <= set(modules)
    offenders = {
        name: writes
        for name, path in modules.items()
        if name != "serve_files.py"
        and (writes := file_writes(path.read_text(encoding="utf-8"), name))
    }
    assert offenders == {}, (
        "only kyosei/serve_files.py may write a file (KYO-456); move these calls "
        f"behind it: {offenders}"
    )


def test_the_boundary_module_is_where_the_writes_are() -> None:
    # Non-vacuous: the detector sees serve_files.py's own writes, so a check that
    # passed only because it could see nothing would fail here.
    source = (Path(kyosei.__file__).parent / "serve_files.py").read_text(encoding="utf-8")
    writes = " ".join(file_writes(source))
    for call in ("mkstemp", "fdopen", "os.link", "os.replace", "os.rename", ".unlink", ".mkdir"):
        assert call in writes, call


@pytest.mark.parametrize(
    "call",
    [
        "open(p, 'w')",
        "open(p, mode='a')",
        "open(p, 'r+b')",
        "open(p, m)",
        "io.open(p, 'x')",
        "Path(p).open('w')",
        "p.write_text('x')",
        "p.write_bytes(b'x')",
        "p.rename(q)",
        "p.replace(q)",
        "os.replace(p, q)",
        "p.unlink()",
        "shutil.rmtree(p)",
        "p.mkdir()",
        "os.makedirs(p)",
        "os.remove(p)",
        "shutil.copy(p, q)",
        "tempfile.mkstemp()",
    ],
)
def test_the_check_catches_each_kind_of_write(call: str) -> None:
    assert file_writes(f"def f(p, q, m):\n    {call}\n"), call


@pytest.mark.parametrize(
    "call",
    ["open(p)", "open(p, 'rb')", "p.read_text()", "s.replace('_', '-')", "xs.remove(1)",
     "d.copy()", "self.wfile.write(b)", "Path(p).open()"],
)
def test_the_check_passes_reads_and_lookalikes(call: str) -> None:
    assert file_writes(f"def f(p, s, xs, d, self, b):\n    {call}\n") == [], call


# ------------------------------------------------------------------ c4: events


class EventStream:
    """One open ``/events`` response, read line by line under a socket timeout."""

    def __init__(self, client: Client, *, timeout: float = 10.0) -> None:
        self.conn = http.client.HTTPConnection("127.0.0.1", client.port, timeout=timeout)
        self.conn.putrequest("GET", f"{BASE}/events", skip_host=True)
        self.conn.putheader("Host", f"127.0.0.1:{client.port}")
        self.conn.endheaders()
        self.response = self.conn.getresponse()

    def next_block(self) -> list[str]:
        """The lines up to the next blank line; raises on the timeout."""
        lines: list[str] = []
        while True:
            line = self.response.fp.readline()
            if line == b"":
                raise EOFError("the stream ended")
            text = line.decode("utf-8").rstrip("\n")
            if text == "":
                if lines:
                    return lines
                continue
            lines.append(text)

    def next_change(self, *, within: float = 10.0) -> str:
        """The ``kind`` of the next ``changed`` event, skipping keepalives."""
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            block = self.next_block()
            if block[0] == "event: changed":
                return json.loads(block[1].removeprefix("data: "))["kind"]
        raise TimeoutError("no change event arrived")

    def close(self) -> None:
        self.conn.close()


@pytest.fixture
def events(files: Client):
    opened: list[EventStream] = []

    def _open() -> EventStream:
        stream = EventStream(files)
        opened.append(stream)
        assert stream.response.status == 200
        assert stream.response.headers["Content-Type"].startswith("text/event-stream")
        assert stream.next_block() == ["retry: 1000", ": kyo serve events"]
        return stream

    yield _open
    for stream in opened:
        stream.close()


def test_an_idea_write_is_an_ideas_event(files: Client, events, project: Path) -> None:
    stream = events()
    (project / "ideas" / "fresh.md").write_text("x", encoding="utf-8")
    assert stream.next_change() == "ideas"


def test_a_store_write_is_a_store_event(files: Client, events, kyo) -> None:
    stream = events()
    new_record(kyo, kind="bug", title="Evented", criteria=["it moves the store"])
    assert stream.next_change() == "store"


def test_a_sessions_write_is_a_sessions_event(files: Client, events, project: Path) -> None:
    stream = events()
    (project / "kyosei" / "sessions.json").write_text("{}", encoding="utf-8")
    assert stream.next_change() == "sessions"


def test_a_scratchpad_write_is_a_scratchpad_event(files: Client, events, store_home: Path) -> None:
    """KYO-575.c2: the scratchpad lives in the per-project home, outside ``ideas/``, so
    without its own kind no window hears another window or machine save it."""
    stream = events()
    status, headers, body = put(files, "scratchpad", b"first\n", **{"If-None-Match": "*"})
    assert status == 201, body
    assert stream.next_change() == "scratchpad"
    # A replace through the route (an atomic rename) is heard too, and so is a write by
    # anything else on the server's machine: the fingerprint reads the file, not the route.
    status, _, body = put(files, "scratchpad", b"second\n", **{"If-Match": headers["ETag"]})
    assert status == 200, body
    assert stream.next_change() == "scratchpad"
    (store_home / "proj" / serve_files.SCRATCHPAD_NAME).write_text("third\n", encoding="utf-8")
    assert stream.next_change() == "scratchpad"


def test_a_scratchpad_replaced_within_one_tick_still_moves_the_stamp(
    project: Path, store_home: Path
) -> None:
    """Same length, same mtime, a new file by rename: the inode is what differs."""
    paths = serve_files.project_paths(str(project), {"KYOSEI_HOME": str(store_home)})
    paths.scratchpad.parent.mkdir(parents=True, exist_ok=True)
    paths.scratchpad.write_text("aaaa\n", encoding="utf-8")
    before = serve_events.fingerprint(paths)["scratchpad"]
    stamp = os.stat(paths.scratchpad).st_mtime_ns
    replacement = paths.scratchpad.with_name(".scratchpad.md.tmp")
    replacement.write_text("bbbb\n", encoding="utf-8")
    os.utime(replacement, ns=(stamp, stamp))
    os.replace(replacement, paths.scratchpad)
    assert os.stat(paths.scratchpad).st_mtime_ns == stamp
    assert serve_events.fingerprint(paths)["scratchpad"] != before


def test_a_read_moves_nothing(files: Client, events, kyo) -> None:
    stream = events()
    # A read-only command opens the database; the fingerprint must not move for it.
    assert kyo("list", "--json").code == 0
    block = stream.next_block()  # the next thing sent is a keepalive, not a change
    assert block == [": keepalive"], block


def test_an_idle_stream_sends_a_keepalive(files: Client, events) -> None:
    assert events().next_block() == [": keepalive"]


def test_stopping_the_server_ends_the_stream(files: Client, events) -> None:
    stream = events()
    files.server.shutdown()
    with pytest.raises(EOFError):
        while True:
            stream.next_block()


def test_the_fingerprint_is_stat_only_and_names_each_kind(project: Path, store_home: Path) -> None:
    paths = serve_files.project_paths(str(project), {"KYOSEI_HOME": str(store_home)})
    first = serve_events.fingerprint(paths)
    assert set(first) == set(serve_events.KINDS)
    assert first["sessions"] == "gone"
    (project / "kyosei" / "sessions.json").write_text("{}", encoding="utf-8")
    second = serve_events.fingerprint(paths)
    assert second["sessions"] != "gone" and second["store"] == first["store"]
    # KYO-575.c2: the scratchpad is a kind of its own, "gone" until its first save.
    assert first["scratchpad"] == "gone" and second["scratchpad"] == "gone"
    paths.scratchpad.parent.mkdir(parents=True, exist_ok=True)
    paths.scratchpad.write_text("x\n", encoding="utf-8")
    third = serve_events.fingerprint(paths)
    assert third["scratchpad"] != "gone"
    assert {k: v for k, v in third.items() if k != "scratchpad"} == {
        k: v for k, v in second.items() if k != "scratchpad"
    }


def test_the_events_route_is_get_only(files: Client) -> None:
    status, headers, _ = files.request(
        f"{BASE}/events", method="POST", headers={"Content-Type": "application/json"}, body=b"{}"
    )
    assert status == 405 and headers["Allow"] == "GET"


def test_the_default_cadence_is_a_one_second_poll_and_a_fifteen_second_keepalive(
    running,
) -> None:
    client = running()
    assert (client.server.poll_seconds, client.server.keepalive_seconds) == (1.0, 15.0)
    assert (serve_events.POLL_SECONDS, serve_events.KEEPALIVE_SECONDS) == (1.0, 15.0)


# ------------------------------------------------------- KYO-467: document text


def write_doc(project: Path, relpath: str, text: str) -> Path:
    path = project / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_governed_document_is_served_read_only_with_its_etag(
    files: Client, project: Path
) -> None:
    """KYO-467.c1: a file `kyo docs check` lists is served as its bytes, at a path
    relative to the code root -- the default set's docs/**/*.md and root *.md alike."""
    guide = write_doc(project, "docs/design/guide.md", "# Guide\n\nThe text.\n")
    write_doc(project, "NOTES.md", "# Notes\n")
    status, headers, body = files.get(f"{BASE}/docs/docs/design/guide.md")
    assert status == 200, body
    assert body == guide.read_bytes()
    assert headers["ETag"] == serve_files.etag_of(guide.read_bytes())
    assert headers["Content-Type"].startswith("text/markdown")
    status, _, body = files.get(f"{BASE}/docs/NOTES.md")
    assert (status, body) == (200, b"# Notes\n")
    # Read-only: the route answers GET and nothing else, and the file is untouched.
    for method in ("PUT", "DELETE", "POST"):
        status, headers, _ = files.request(
            f"{BASE}/docs/docs/design/guide.md",
            method=method,
            headers={"If-Match": "*", "Content-Type": "application/json"},
            body=b"{}" if method != "DELETE" else None,
        )
        assert status == 405 and headers["Allow"] == "GET", method
    assert guide.read_text(encoding="utf-8") == "# Guide\n\nThe text.\n"


def test_the_document_route_serves_what_kyo_docs_check_lists_and_nothing_else(
    files: Client, project: Path, kyo
) -> None:
    """The route's set IS the check's: every path `kyo docs check --json` lists reads,
    and a real file outside that set -- the store's config, a source file, a markdown
    file the globs do not reach -- is a 404 naming the set, exactly like a missing one."""
    write_doc(project, "docs/a.md", "# A\n")
    write_doc(project, "docs/deep/b.md", "# B\n")
    write_doc(project, "src/app.py", "x = 1\n")
    write_doc(project, "src/readme.md", "# not a document by the default globs\n")
    listed = kyo("docs", "check", "--json").json["document_paths"]
    documents = [row["id"] for row in kyo("docs", "check", "--json").json["documents"]]
    assert {"docs/a.md", "docs/deep/b.md"} <= set(documents), listed
    for path in documents:
        status, _, body = files.get(f"{BASE}/docs/{path}")
        assert status == 200, (path, body)
        assert body == (project / path).read_bytes()
    for path in ("kyosei/config.yml", "src/app.py", "src/readme.md", "docs/missing.md"):
        status, _, body = files.get(f"{BASE}/docs/{path}")
        assert status == 404, (path, body)
        error = body_json(body)["error"]
        assert "is not one of this project's documents" in error, error
        assert "docs/**/*.md" in error, error  # the set it was measured against


def test_a_declared_document_set_is_the_one_served(files: Client, project: Path) -> None:
    """`kyosei/documents.yml` declaring a set REPLACES the default for the route as it
    does for the check (KYO-129.c2): the declared tree reads, the default's does not."""
    (project / "kyosei" / "documents.yml").write_text(
        "documents:\n  handbook:\n    summary: The handbook.\n    globs:\n"
        "      - handbook/**/*.md\n",
        encoding="utf-8",
    )
    write_doc(project, "handbook/ops/runbook.md", "# Run\n")
    write_doc(project, "docs/a.md", "# A\n")
    assert files.get(f"{BASE}/docs/handbook/ops/runbook.md")[0] == 200
    status, _, body = files.get(f"{BASE}/docs/docs/a.md")
    assert status == 404 and "handbook/**/*.md" in body_json(body)["error"]


@pytest.mark.parametrize(
    "raw",
    [
        "../kyosei/config.yml",
        "docs/../kyosei/config.yml",
        "..%2Fkyosei%2Fconfig.yml",  # an encoded slash inside one segment
        "docs%2F..%2F..%2F..%2Fetc%2Fpasswd",
        "%2Fetc%2Fpasswd",  # an absolute path
        "a%00.md",  # a NUL
        "docs//a.md",
        "./docs/a.md",
    ],
)
def test_a_traversal_to_the_document_route_is_refused_before_the_disk(
    files: Client, project: Path, raw: str
) -> None:
    """The planted traversal: every spelling is a 400 as text, whatever it names."""
    write_doc(project, "docs/a.md", "# A\n")
    status, _, body = files.get(f"{BASE}/docs/{raw}")
    assert status == 400, (raw, body)
    assert "is not a path inside the project's code root" in body_json(body)["error"]


def test_a_document_symlink_that_leaves_the_project_is_refused(
    files: Client, project: Path, tmp_path: Path
) -> None:
    """A link inside docs/ matches the globs, and the check reads through it -- but the
    route serves only what resolves inside the code root, so it is no way out."""
    outside = tmp_path / "secret.md"
    outside.write_text("# not this project's\n", encoding="utf-8")
    (project / "docs").mkdir(exist_ok=True)
    (project / "docs" / "leak.md").symlink_to(outside)
    status, _, body = files.get(f"{BASE}/docs/docs/leak.md")
    assert status == 403, body
    assert "outside the code root" in body_json(body)["error"]
    assert b"not this project's" not in body


def test_the_document_route_needs_a_path(files: Client) -> None:
    status, _, body = files.get(f"{BASE}/docs/")
    assert status == 404 and "cmd/docs/check" in body_json(body)["error"]


def test_the_document_route_reads_the_code_root_under_the_split_layout(
    split_project, store_home: Path, running
) -> None:
    """Under the split layout the documents are in the CODE repository, not beside the
    store; the route resolves them there, as `kyo docs check` does."""
    code, store = split_project
    write_doc(code, "docs/split.md", "# Split\n")
    paths = serve_files.project_paths(str(code), {"KYOSEI_HOME": str(store_home)})
    assert paths.code_root.resolve() == code.resolve()
    assert paths.store_root.resolve() == store.resolve()
    assert serve_files.document_target(paths, "docs/split.md") == Path(
        os.path.realpath(code / "docs" / "split.md")
    )


# ------------------------------------------------- KYO-467: an idea's created time


def test_every_tree_entry_carries_a_created_time_no_later_than_now(
    files: Client, project: Path
) -> None:
    """KYO-467.c3: the tree names when each path came to be. A file whose mtime is
    pushed into the future keeps a created time from when it was written."""
    before = time.time_ns()
    idea = project / "ideas" / "later.md"
    idea.write_bytes(b"later")
    future = time.time() + 10 * 365 * 24 * 3600
    os.utime(idea, (future, future))
    after = time.time_ns()
    entries = {e["path"]: e for e in body_json(files.get(f"{BASE}/ideas/")[2])["entries"]}
    entry = entries["later.md"]
    assert entry["mtime_ns"] > after
    # Filesystem stamps can be coarser than the clock; a second's slack either side.
    assert before - 1_000_000_000 <= entry["created_ns"] <= after + 1_000_000_000
    assert all(isinstance(e["created_ns"], int) for e in entries.values())


class _Stat:
    def __init__(self, **fields: float | int) -> None:
        self.__dict__.update(fields)


def test_created_is_the_birth_time_else_the_earliest_known() -> None:
    """Birth time where the filesystem keeps one; otherwise the smaller of mtime and
    ctime (never later than the birth); a zero birth time is a filesystem with none."""
    stamps = {"st_mtime_ns": 5_000, "st_ctime_ns": 3_000}
    assert serve_files.created_ns(_Stat(st_birthtime_ns=1_000, **stamps)) == 1_000
    assert serve_files.created_ns(_Stat(st_birthtime=2.0, **stamps)) == 2_000_000_000
    assert serve_files.created_ns(_Stat(**stamps)) == 3_000
    assert serve_files.created_ns(_Stat(st_birthtime=0.0, **stamps)) == 3_000
    assert serve_files.created_ns(_Stat(st_mtime_ns=2_000, st_ctime_ns=9_000)) == 2_000
