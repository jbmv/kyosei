"""KYO-455: `kyo projects list|add|remove`, the registry's one writer.

Every registry here is under the test's own ``$KYOSEI_HOME`` (conftest's autouse
``store_home``), never the real ``~/kyosei``. Any server started here binds loopback on
an ephemeral port and is stopped by the test.
"""

from __future__ import annotations

import http.client
import io
import json
import os
import threading
import urllib.parse
from pathlib import Path

import pytest

from kyosei import cli, cmd_projects, serve
from kyosei.config import project_home_name, resolve_layout
from kyosei.errors import KyoseiError

from .conftest import invoke


def init_in_repo(root: Path, prefix: str = "TST") -> Path:
    result = invoke(["init", str(root), "--in-repo", "--prefix", prefix])
    assert result.code == 0, result
    return root.resolve()


def registry(store_home: Path) -> dict:
    return json.loads((store_home / "projects.json").read_text(encoding="utf-8"))


def listed(args: list[str] | None = None) -> dict:
    result = invoke(["projects", "list", "--json", *(args or [])])
    assert result.code == 0, result
    return result.json


# ------------------------------------------------ c1: list, add, remove, atomic


def test_a_missing_registry_is_the_ordinary_empty_list(store_home: Path) -> None:
    assert not (store_home / "projects.json").exists()
    data = listed()
    assert data["projects"] == []
    assert data["registry"] == str(store_home / "projects.json")


def test_add_list_remove_round_trip(tmp_path: Path, store_home: Path) -> None:
    a = init_in_repo(tmp_path / "alpha", "ALP")
    b = init_in_repo(tmp_path / "beta", "BET")
    # init registered both (c2); remove them and add them back by hand.
    for name in ("alpha", "beta"):
        assert invoke(["projects", "remove", name]).code == 0
    assert registry(store_home) == {"projects": []}

    added = invoke(["projects", "add", str(a), "--json"])
    assert added.code == 0, added
    assert added.json["added"] is True
    assert added.json["project"] == {
        "name": "alpha",
        "root": str(a),
        "prefix": "ALP",
        "problem": None,
    }
    assert invoke(["projects", "add", str(b)]).code == 0

    rows = listed()["projects"]
    assert [(r["name"], r["root"], r["prefix"]) for r in rows] == [
        ("alpha", str(a), "ALP"),
        ("beta", str(b), "BET"),
    ]
    human = invoke(["projects", "list"])
    assert "alpha" in human.out and "BET" in human.out

    # Remove by root path as well as by name.
    gone = invoke(["projects", "remove", str(a), "--json"])
    assert gone.code == 0, gone
    assert gone.json["project"]["name"] == "alpha"
    assert registry(store_home) == {"projects": [{"root": str(b)}]}


def test_re_adding_a_listed_root_writes_nothing(tmp_path: Path, store_home: Path) -> None:
    root = init_in_repo(tmp_path / "proj")
    before = (store_home / "projects.json").stat().st_mtime_ns
    again = invoke(["projects", "add", str(root), "--json"])
    assert again.code == 0, again
    assert again.json["added"] is False
    assert "already registered" in invoke(["projects", "add", str(root)]).out
    assert (store_home / "projects.json").stat().st_mtime_ns == before
    assert registry(store_home) == {"projects": [{"root": str(root)}]}


def test_every_entry_a_person_wrote_is_kept_as_written(tmp_path: Path, store_home: Path) -> None:
    root = init_in_repo(tmp_path / "proj")
    other = init_in_repo(tmp_path / "other")
    (store_home / "projects.json").write_text(
        json.dumps({"projects": [{"root": str(other), "note": "mine"}]}), encoding="utf-8"
    )
    assert invoke(["projects", "add", str(root)]).code == 0
    assert registry(store_home)["projects"] == [
        {"root": str(other), "note": "mine"},
        {"root": str(root)},
    ]
    assert invoke(["projects", "remove", "proj"]).code == 0
    assert registry(store_home)["projects"] == [{"root": str(other), "note": "mine"}]


BAD_REGISTRIES = [
    ("{", "it is not JSON"),
    ("[]", 'is not an object with a "projects" list'),
    ('{"projects": 3}', 'it has no "projects" list'),
    ('{"projects": [{"path": "/x"}]}', 'projects[0] has no "root" path'),
    ('{"projects": [{"root": "  "}]}', 'projects[0] has no "root" path'),
]


@pytest.mark.parametrize(("text", "why"), BAD_REGISTRIES)
def test_an_unparseable_registry_is_refused_by_name_never_read_as_empty(
    tmp_path: Path, store_home: Path, text: str, why: str
) -> None:
    root = init_in_repo(tmp_path / "proj")
    path = store_home / "projects.json"
    path.write_text(text, encoding="utf-8")
    for args in (["list"], ["add", str(root)], ["remove", "proj"]):
        result = invoke(["projects", *args])
        assert result.code == 1, (args, result)
        assert str(path) in result.err and why in result.err
        assert "not read as empty" in result.err
        # Nothing was written over the person's file.
        assert path.read_text(encoding="utf-8") == text
    # The remedy the refusal names -- correct it by hand, in the shape it quotes --
    # clears it.
    path.write_text(
        cmd_projects.REGISTRY_SHAPE.replace("/path/to/project", str(root)), encoding="utf-8"
    )
    assert [r["root"] for r in listed()["projects"]] == [str(root)]


@pytest.mark.parametrize(("text", "_why"), BAD_REGISTRIES)
def test_the_writer_and_kyo_serve_refuse_the_same_files(
    tmp_path: Path, text: str, _why: str
) -> None:
    """Two readers of one file (serve's predates this command): they must agree."""
    path = tmp_path / "projects.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(KyoseiError, match="not read as empty"):
        cmd_projects.read_entries(path)
    with pytest.raises(serve.Refusal):
        serve.read_registry(path)


def test_the_remedy_moving_it_aside_also_clears_the_refusal(store_home: Path) -> None:
    path = store_home / "projects.json"
    store_home.mkdir(parents=True, exist_ok=True)
    path.write_text("{", encoding="utf-8")
    assert invoke(["projects", "list"]).code == 1
    path.rename(store_home / "projects.json.aside")
    assert listed()["projects"] == []


def test_a_write_is_a_temporary_file_renamed_over_the_registry(
    tmp_path: Path, store_home: Path, monkeypatch
) -> None:
    root = init_in_repo(tmp_path / "proj")
    other = init_in_repo(tmp_path / "other")
    assert invoke(["projects", "remove", "other"]).code == 0
    path = store_home / "projects.json"
    before = path.read_text(encoding="utf-8")

    renames: list[tuple[str, str]] = []
    real_replace = os.replace

    def failing_replace(src, dst):
        renames.append((str(src), str(dst)))
        raise OSError("disk full")

    monkeypatch.setattr(cmd_projects.os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        cmd_projects.add(other)
    # The rename was the write: the target was untouched, the temporary sat beside it
    # and is gone.
    assert renames and renames[0][1] == str(path)
    assert Path(renames[0][0]).parent == path.parent
    assert path.read_text(encoding="utf-8") == before
    assert sorted(p.name for p in store_home.iterdir() if p.is_file()) == ["projects.json"]

    monkeypatch.setattr(cmd_projects.os, "replace", real_replace)
    cmd_projects.add(other)
    assert [e["root"] for e in registry(store_home)["projects"]] == [str(root), str(other)]


def test_remove_refuses_an_unregistered_name_and_list_shows_the_names(
    tmp_path: Path,
) -> None:
    init_in_repo(tmp_path / "proj")
    result = invoke(["projects", "remove", "nope"])
    assert result.code == 1
    assert "kyo projects list" in result.err
    names = [r["name"] for r in listed()["projects"]]
    assert names == ["proj"]
    assert invoke(["projects", "remove", names[0]]).code == 0


def test_add_and_remove_need_a_target_and_list_takes_none(tmp_path: Path) -> None:
    root = init_in_repo(tmp_path / "proj")
    for action in ("add", "remove"):
        result = invoke(["projects", action])
        assert result.code == 1 and f"kyo projects {action}" in result.err
    # The invocations the refusals name run.
    assert invoke(["projects", "remove", "proj"]).code == 0
    assert invoke(["projects", "add", str(root)]).code == 0
    result = invoke(["projects", "list", str(root)])
    assert result.code == 1 and "takes no argument" in result.err
    assert invoke(["projects", "list"]).code == 0


def test_an_entry_that_cannot_be_opened_is_listed_with_its_problem(
    tmp_path: Path, store_home: Path
) -> None:
    store_home.mkdir(parents=True, exist_ok=True)
    gone = tmp_path / "gone"
    (store_home / "projects.json").write_text(
        json.dumps({"projects": [{"root": str(gone)}]}), encoding="utf-8"
    )
    (row,) = listed()["projects"]
    assert row["name"] == "gone" and "no directory" in row["problem"]
    # Removable by name even though it cannot be opened.
    assert invoke(["projects", "remove", "gone"]).code == 0


# ----------------------------------------- c2: init registers; add's refusals


def test_init_registers_the_in_repo_project_it_creates(tmp_path: Path, store_home: Path) -> None:
    result = invoke(["init", str(tmp_path / "proj"), "--in-repo", "--prefix", "TST", "--json"])
    assert result.code == 0, result
    root = (tmp_path / "proj").resolve()
    assert registry(store_home) == {"projects": [{"root": str(root)}]}
    assert result.json["registry"] == {
        "registered": True,
        "registry": str(store_home / "projects.json"),
        "problem": None,
        "name": "proj",
    }
    human = invoke(["projects", "list"])
    assert "proj" in human.out and "TST" in human.out


def test_init_registers_the_split_project_by_its_code_root(
    split_project: tuple[Path, Path], store_home: Path
) -> None:
    code, store = split_project
    roots = [e["root"] for e in registry(store_home)["projects"]]
    assert roots == [str(code.resolve())]
    (row,) = listed()["projects"]
    assert row["name"] == store.name


def test_init_with_an_unreadable_registry_still_inits_and_says_how_to_register(
    tmp_path: Path, store_home: Path
) -> None:
    store_home.mkdir(parents=True, exist_ok=True)
    path = store_home / "projects.json"
    path.write_text("{", encoding="utf-8")
    result = invoke(["init", str(tmp_path / "proj"), "--in-repo", "--json"])
    assert result.code == 0, result
    data = result.json["registry"]
    assert data["registered"] is False and str(path) in data["problem"]
    assert path.read_text(encoding="utf-8") == "{"
    human_root = (tmp_path / "proj").resolve()
    # The remedy it names: fix the file, then `kyo projects add <root>`.
    path.unlink()
    assert invoke(["projects", "add", str(human_root)]).code == 0
    assert registry(store_home) == {"projects": [{"root": str(human_root)}]}


def test_init_human_output_names_the_registration(tmp_path: Path) -> None:
    result = invoke(["init", str(tmp_path / "proj"), "--in-repo"])
    assert result.code == 0, result
    assert "registered in this machine's project registry" in result.out
    assert "as 'proj'" in result.out


def test_add_refuses_a_directory_that_holds_no_project(tmp_path: Path, store_home: Path) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    result = invoke(["projects", "add", str(bare)])
    assert result.code == 1
    assert "holds no initialised Kyosei project" in result.err
    assert f"kyo init {bare.resolve()}" in result.err
    assert not (store_home / "projects.json").exists()
    # A subdirectory of a project is not the project: add does not search upward.
    root = init_in_repo(tmp_path / "proj")
    (root / "sub").mkdir()
    assert invoke(["projects", "add", str(root / "sub")]).code == 1
    # The remedy it names -- `kyo init` there -- clears it.
    assert invoke(["projects", "remove", "proj"]).code == 0
    assert invoke(["init", str(bare), "--in-repo"]).code == 0
    assert invoke(["projects", "add", str(bare)]).code == 0


def test_add_refuses_a_path_that_does_not_exist(tmp_path: Path) -> None:
    result = invoke(["projects", "add", str(tmp_path / "nowhere")])
    assert result.code == 1 and "there is no directory" in result.err
    # The remedy it names: the directory of a Kyosei project.
    root = init_in_repo(tmp_path / "proj")
    assert invoke(["projects", "remove", "proj"]).code == 0
    assert invoke(["projects", "add", str(root)]).code == 0


def test_add_refuses_a_duplicate_project_name(tmp_path: Path, store_home: Path) -> None:
    first = init_in_repo(tmp_path / "one" / "proj", "ONE")
    second = tmp_path / "two" / "proj"
    result = invoke(["init", str(second), "--in-repo", "--prefix", "TWO", "--json"])
    assert result.code == 0, result
    # init itself was refused registration, and said so.
    reg = result.json["registry"]
    assert reg["registered"] is False and "already registered" in reg["problem"]
    assert registry(store_home) == {"projects": [{"root": str(first)}]}

    refused = invoke(["projects", "add", str(second)])
    assert refused.code == 1
    assert "a project named 'proj' is already registered" in refused.err
    assert f"kyo projects remove {first}" in refused.err
    # The remedy it names clears it.
    assert invoke(["projects", "remove", str(first)]).code == 0
    assert invoke(["projects", "add", str(second)]).code == 0
    assert registry(store_home) == {"projects": [{"root": str(second.resolve())}]}


def test_a_hand_written_duplicate_name_is_flagged_and_removed_by_root(
    tmp_path: Path, store_home: Path
) -> None:
    a = init_in_repo(tmp_path / "one" / "proj")
    b = tmp_path / "two" / "proj"
    invoke(["init", str(b), "--in-repo"])
    (store_home / "projects.json").write_text(
        json.dumps({"projects": [{"root": str(a)}, {"root": str(b.resolve())}]}),
        encoding="utf-8",
    )
    assert "shares its name" in invoke(["projects", "list"]).out
    ambiguous = invoke(["projects", "remove", "proj"])
    assert ambiguous.code == 1 and "by its root path" in ambiguous.err
    assert invoke(["projects", "remove", str(a)]).code == 0


# ------------------------------------- c3: the served name, and no absolute path


def _request(port: int, path: str) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
        response = conn.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        conn.close()


@pytest.fixture
def server():
    started = serve.KyoServer(port=0, log_stream=io.StringIO())
    threading.Thread(target=started.serve_forever, daemon=True).start()
    yield started
    started.shutdown()
    started.server_close()


def test_the_listed_name_is_project_home_name_and_the_name_kyo_serve_serves(
    tmp_path: Path, split_project: tuple[Path, Path], server
) -> None:
    code, store = split_project
    in_repo = init_in_repo(tmp_path / "inrepo", "INR")
    rows = {r["root"]: r["name"] for r in listed()["projects"]}
    for root in (code.resolve(), in_repo):
        assert rows[str(root)] == project_home_name(resolve_layout(explicit_root=root))
    assert rows[str(code.resolve())] == store.name  # split: the store directory's name
    assert rows[str(in_repo)] == "inrepo"  # in-repo: the code root's name

    status, body = _request(server.port, "/v1/projects")
    assert status == 200
    assert {p["root"]: p["name"] for p in body["projects"]} == rows
    for name in rows.values():
        status, body = _request(server.port, f"/v1/projects/{name}/cmd/where")
        assert status == 200, body


def test_no_route_takes_an_absolute_path_for_a_project(tmp_path: Path, server) -> None:
    root = init_in_repo(tmp_path / "proj")
    for spelled in (urllib.parse.quote(str(root), safe=""), str(root).lstrip("/")):
        status, body = _request(server.port, f"/v1/projects/{spelled}/cmd/where")
        assert status == 404, (spelled, body)
    status, _ = _request(server.port, "/v1/projects/proj/cmd/where")
    assert status == 200


def test_kyo_serve_does_not_run_kyo_projects(tmp_path: Path, server) -> None:
    init_in_repo(tmp_path / "proj")
    assert "projects" in serve.NOT_SERVED and "projects" not in serve.SERVED_COMMANDS
    assert cli.COMMAND_TABLE["projects"][0] is cmd_projects.cmd_projects
    status, body = _request(server.port, "/v1/projects/proj/cmd/projects/list")
    assert status == 403 and body["refused_by"] == "kyo serve"


def test_describe_names_an_unopenable_entry_as_kyo_serve_does(tmp_path: Path) -> None:
    gone = tmp_path / "gone"
    assert cmd_projects.describe(str(gone)).name == serve.project_name(str(gone))


def test_register_on_init_never_raises(tmp_path: Path, store_home: Path) -> None:
    store_home.mkdir(parents=True, exist_ok=True)
    (store_home / "projects.json").write_text("{", encoding="utf-8")
    lines, data = cmd_projects.register_on_init(tmp_path)
    assert data["registered"] is False and lines
    with pytest.raises(KyoseiError):
        cmd_projects.add(tmp_path)
