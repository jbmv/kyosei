"""KYO-735: `kyo serve`'s file routes, a project's files as a tree and as bytes.

Every request goes over a real socket against a split-layout project the test built: a code
repository and a store repository, both git repositories under the test's own temporary
directory. Nothing outside that directory is read. Each refusal that names a remedy is
followed in the same test by that remedy and the request again.
"""

# ruff: noqa: F811 - a fixture borrowed from another test module is named by its parameters

from __future__ import annotations

import ast
import os
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import pytest

import kyosei
from kyosei import device_tokens, serve, serve_files, serve_tree

from .conftest import git_init, invoke
from .test_kyo_serve import Client, body_json, register, running  # noqa: F401
from .test_kyo_serve_files import file_writes
from .test_kyo_serve_send import issue

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR a small picture"


@dataclass
class Served:
    client: Client
    code: Path
    store: Path
    base: str
    token: str

    def get(self, path: str, token: str | None = "held", **headers: str):
        sent = dict(headers)
        chosen = self.token if token == "held" else token
        if chosen is not None:
            sent["Authorization"] = f"Bearer {chosen}"
        return self.client.request(f"{self.base}/{path}", headers=sent)

    def json(self, path: str, token: str | None = "held") -> tuple[int, dict]:
        status, _, body = self.get(path, token)
        return status, body_json(body)


def write(root: Path, relpath: str, data: bytes | str) -> Path:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    path.write_bytes(data)
    return path


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


@pytest.fixture
def files(split_project, store_home: Path, running) -> Served:
    """A split project served, with a token holding the files permission.

    The code repository holds a tracked markdown file, a tracked source file, a tracked
    image, an ignored file and an untracked file no rule ignores.
    """
    code, store = split_project
    write(code, ".gitignore", "secret.env\nbuild/\n")
    write(code, "README.md", "# Read me\n")
    write(code, "docs/plan.md", "# Plan\n")
    write(code, "src/pixel.png", PNG)
    git(code, "add", "-A")
    git(code, "commit", "-q", "-m", "files")
    write(code, "secret.env", "TOKEN=1\n")
    write(code, "build/out.txt", "built\n")
    write(code, "src/new.py", "y = 2\n")
    if not (store / ".git").exists():
        git_init(store)
    register(store_home, code)
    name = serve.project_name(str(code))
    return Served(
        running(),
        code,
        store,
        f"/v1/projects/{name}/files",
        issue("ipad", device_tokens.PERMISSION_FILES),
    )


def names(document: dict) -> list[str]:
    return [entry["name"] for entry in document["entries"]]


# --------------------------------------------------------------------- c1: the tree


def test_a_directory_lists_what_git_shows_and_nothing_else(files: Served) -> None:
    status, listed = files.json("code/tree")
    assert status == 200, listed
    assert listed["root"] == "code" and listed["path"] == ""
    shown = names(listed)
    # Tracked files and directories, and a dotfile: the client decides whether to hide it.
    assert {"README.md", "docs", "src", ".gitignore"} <= set(shown)
    # An ignored file, an ignored directory and .git are absent.
    assert "secret.env" not in shown and "build" not in shown and ".git" not in shown
    assert shown == sorted(shown)

    status, src = files.json("code/tree/src")
    assert status == 200 and src["path"] == "src"
    by_name = {entry["name"]: entry for entry in src["entries"]}
    # An untracked file no rule ignores is present beside the tracked ones.
    assert set(by_name) == {"widget.py", "pixel.png", "new.py"}
    assert by_name["new.py"] == {
        "name": "new.py",
        "path": "src/new.py",
        "type": "file",
        "size": len(b"y = 2\n"),
        "media_type": "text/x-python",
        "text": True,
        "mtime_ns": (files.code / "src" / "new.py").stat().st_mtime_ns,
    }
    assert by_name["pixel.png"]["media_type"] == "image/png"
    assert by_name["pixel.png"]["text"] is False and by_name["pixel.png"]["size"] == len(PNG)
    root_entries = {entry["name"]: entry for entry in listed["entries"]}
    assert root_entries["src"]["type"] == "dir" and "size" not in root_entries["src"]


def test_a_listing_is_not_stale_when_a_file_is_added_or_ignored(files: Served) -> None:
    assert "later.md" not in names(files.json("code/tree")[1])
    write(files.code, "later.md", "# Later\n")
    assert "later.md" in names(files.json("code/tree")[1])
    write(files.code, ".gitignore", "secret.env\nbuild/\nlater.md\n")
    assert "later.md" not in names(files.json("code/tree")[1])


def test_the_store_repository_is_a_root_of_its_own(files: Served) -> None:
    write(files.store, "docs/proposals/native-apps.md", "# Native apps\n")
    status, listed = files.json("store/tree/docs/proposals")
    assert status == 200 and names(listed) == ["native-apps.md"]
    status, _, body = files.get("store/blob/docs/proposals/native-apps.md")
    assert status == 200 and body == b"# Native apps\n"
    # The code root does not hold it.
    assert files.json("code/tree/docs/proposals")[0] == 404


def test_the_roots_route_names_each_root_and_the_limit(files: Served) -> None:
    status, document = files.json("")
    assert status == 200
    assert document["max_file_bytes"] == serve_tree.MAX_FILE_BYTES
    by_name = {root["name"]: root for root in document["roots"]}
    assert by_name["code"] == {"name": "code", "path": str(files.code), "served": True}
    assert by_name["store"]["served"] is True
    assert os.path.realpath(by_name["store"]["path"]) == os.path.realpath(files.store)
    assert by_name["scratch"] == {
        "name": "scratch",
        "served": False,
        "reason": "scratch-not-recorded",
    }


def test_under_the_in_repo_layout_the_store_root_is_refused_naming_code(
    git_project: Path, store_home: Path, running
) -> None:
    register(store_home, git_project)
    token = issue("ipad", "files")
    served = Served(running(), git_project, git_project, "/v1/projects/proj/files", token)
    status, document = served.json("")
    by_name = {root["name"]: root for root in document["roots"]}
    assert by_name["store"]["served"] is False and by_name["store"]["same_as"] == "code"

    status, refused = served.json("store/tree/kyosei")
    assert status == 404 and refused["reason"] == "store-is-code"
    assert "files/code/" in refused["error"]
    # The remedy it names: the same path under the code root.
    status, listed = served.json("code/tree/kyosei")
    assert status == 200 and "config.yml" in names(listed)


def test_a_root_outside_any_repository_serves_nothing_and_git_init_clears_it(
    project: Path, store_home: Path, running
) -> None:
    register(store_home, project)
    served = Served(
        running(), project, project, "/v1/projects/proj/files", issue("ipad", "files")
    )
    for path in ("code/tree", "code/blob/kyosei/config.yml"):
        status, refused = served.json(path)
        assert status == 409 and refused["reason"] == "not-a-repository", refused
        assert "`git init`" in refused["error"]
    git(project, "init", "-q", "-b", "main")
    assert served.json("code/tree")[0] == 200
    assert served.get("code/blob/kyosei/config.yml")[0] == 200


def test_a_file_on_the_tree_route_and_a_directory_on_the_blob_route_name_the_other(
    files: Served,
) -> None:
    status, refused = files.json("code/tree/README.md")
    assert status == 409 and refused["reason"] == "is-a-file"
    assert "files/code/blob/README.md" in refused["error"]
    assert files.get("code/blob/README.md")[0] == 200

    status, refused = files.json("code/blob/src")
    assert status == 409 and refused["reason"] == "is-a-directory"
    assert "files/code/tree/src" in refused["error"]
    assert files.json("code/tree/src")[0] == 200


# -------------------------------------------------------------------- c2: the bytes


@pytest.mark.parametrize(
    ("relpath", "data", "content_type"),
    [
        ("docs/plan.md", b"# Plan\n", "text/markdown; charset=utf-8"),
        ("src/widget.py", b"x = 1\n", "text/x-python; charset=utf-8"),
        ("src/pixel.png", PNG, "image/png"),
    ],
)
def test_a_file_is_returned_with_its_type_and_etag_and_revalidates(
    files: Served, relpath: str, data: bytes, content_type: str
) -> None:
    status, headers, body = files.get(f"code/blob/{relpath}")
    assert status == 200 and body == data
    assert headers["Content-Type"] == content_type
    assert headers["ETag"] == serve_files.etag_of(data)
    assert headers["X-Content-Type-Options"] == "nosniff"

    status, again, body = files.get(
        f"code/blob/{relpath}", **{"If-None-Match": headers["ETag"]}
    )
    assert status == 304 and body == b"" and again["ETag"] == headers["ETag"]

    # A changed file no longer matches the tag, and its new bytes are returned.
    write(files.code, relpath, data + b"more")
    status, changed, body = files.get(
        f"code/blob/{relpath}", **{"If-None-Match": headers["ETag"]}
    )
    assert status == 200 and body == data + b"more" and changed["ETag"] != headers["ETag"]


@pytest.mark.parametrize(
    ("name", "kind", "text"),
    [
        ("notes.md", "text/markdown", True),
        ("main.ts", "text/typescript", True),
        ("App.swift", "text/x-swift", True),
        ("lib.rs", "text/x-rust", True),
        ("config.yml", "application/yaml", True),
        ("pyproject.toml", "application/toml", True),
        ("data.json", "application/json", True),
        ("Makefile", "text/x-makefile", True),
        (".gitignore", "text/plain", True),
        ("photo.jpg", "image/jpeg", False),
        ("paper.pdf", "application/pdf", False),
        ("blob.unknownsuffix", "application/octet-stream", False),
        ("no-suffix-at-all", "application/octet-stream", False),
    ],
)
def test_media_types_are_by_name_and_text_is_recognisable(
    name: str, kind: str, text: bool
) -> None:
    assert serve_tree.media_type(name) == kind
    assert serve_tree.is_text(kind) is text
    assert serve_tree.content_type(kind).endswith("; charset=utf-8") is text


# ----------------------------------------------------------- c3: what is refused


@pytest.mark.parametrize("route", ["tree", "blob"])
@pytest.mark.parametrize(
    ("relpath", "status"),
    [
        ("/etc/passwd", 400),
        ("../code/README.md", 400),
        ("src/../README.md", 400),
        ("src/./widget.py", 400),
        ("src/a\x00b", 400),
        (".git/config", 403),
        (".GIT/config", 403),
        ("src/.git/config", 403),
    ],
)
def test_a_path_refused_as_text_never_reaches_the_disk(
    files: Served, monkeypatch: pytest.MonkeyPatch, route: str, relpath: str, status: int
) -> None:
    def touched(*args, **kwargs):
        raise AssertionError("the path was refused as text and must not be resolved")

    monkeypatch.setattr(serve_tree, "_real_inside", touched)
    monkeypatch.setattr(serve_tree, "_git_shows", touched)
    quoted = urllib.parse.quote(relpath, safe="/")
    # A leading slash is written as its own (empty) segment, as a client would send it.
    got, refused = files.json(f"code/{route}/{quoted}")
    assert got == status, refused
    assert refused["ok"] is False


@pytest.mark.parametrize("route", ["tree", "blob"])
def test_a_symlink_inside_the_root_that_points_outside_it_is_refused(
    files: Served, tmp_path: Path, route: str
) -> None:
    outside = tmp_path / "outside"
    write(outside, "secret.md", "outside the root\n")
    write(outside, "folder/inner.md", "inner\n")
    (files.code / "leak.md").symlink_to(outside / "secret.md")
    (files.code / "leakdir").symlink_to(outside / "folder")
    # git shows both links (untracked, not ignored), so only the real path refuses them.
    listed = {entry["name"]: entry for entry in files.json("code/tree")[1]["entries"]}
    assert listed["leak.md"]["type"] == "symlink" and "size" not in listed["leak.md"]

    for relpath in ("leak.md", "leakdir", "leakdir/inner.md"):
        status, _, body = files.get(f"code/{route}/{relpath}")
        refused = body_json(body)
        assert status == 403 and refused["reason"] == "outside-root", refused
        assert b"outside the root" not in body and b"inner" not in body.replace(
            b"inner.md", b""
        )


def test_a_symlink_to_an_ignored_file_inside_the_root_is_refused(files: Served) -> None:
    (files.code / "alias.env").symlink_to(files.code / "secret.env")
    status, refused = files.json("code/blob/alias.env")
    assert status == 404 and refused["reason"] == "not-shown"
    # A link to a file git shows is read.
    (files.code / "alias.md").symlink_to(files.code / "README.md")
    status, _, body = files.get("code/blob/alias.md")
    assert status == 200 and body == b"# Read me\n"


@pytest.mark.parametrize(
    "relpath", ["secret.env", "build/out.txt", "absent.md", "SECRET.env"]
)
def test_a_file_git_ignores_is_refused_like_an_absent_one(files: Served, relpath: str) -> None:
    status, _, body = files.get(f"code/blob/{relpath}")
    refused = body_json(body)
    assert status == 404 and refused["reason"] == "not-shown", refused
    assert b"TOKEN=1" not in body and b"built" not in body


def test_an_ignored_directory_is_refused(files: Served) -> None:
    status, refused = files.json("code/tree/build")
    assert status == 404 and refused["reason"] == "not-shown"
    assert "files/code/tree" in refused["error"]
    assert files.json("code/tree")[0] == 200


def test_a_tracked_file_is_shown_though_a_rule_would_ignore_it(files: Served) -> None:
    write(files.code, "kept.env", "KEPT=1\n")
    write(files.code, ".gitignore", "secret.env\nbuild/\n*.env\n")
    assert files.json("code/blob/kept.env")[0] == 404
    git(files.code, "add", "-f", "kept.env")
    status, _, body = files.get("code/blob/kept.env")
    assert status == 200 and body == b"KEPT=1\n"


def test_a_name_git_would_read_as_a_pattern_is_read_literally(files: Served) -> None:
    write(files.code, "docs/star*.md", "# Star\n")
    status, _, body = files.get("code/blob/docs/" + urllib.parse.quote("star*.md"))
    assert status == 200 and body == b"# Star\n"
    status, refused = files.json("code/blob/docs/" + urllib.parse.quote("*.md"))
    assert status == 404 and refused["reason"] == "not-shown"


# -------------------------------------------------------------- c4: the permission


@pytest.mark.parametrize(
    "path",
    ["", "code/tree", "code/blob/README.md", "resolve?path=README.md", "scratch/tree"],
)
def test_every_route_needs_a_token_with_the_files_permission(
    files: Served, path: str
) -> None:
    status, headers, body = files.get(path, token=None)
    refused = body_json(body)
    assert status == 401 and refused["reason"] == "no-token"
    assert headers["WWW-Authenticate"] == "Bearer"
    assert "--permission files" in refused["error"]

    status, refused = files.json(path, token="kyo_not-a-token-this-machine-issued")
    assert status == 401 and refused["reason"] == "unknown-token"


def test_a_token_without_the_permission_is_refused_and_the_remedy_clears_it(
    files: Served,
) -> None:
    other = issue("phone", "send", "notes", "start", "terminal")
    for path in ("code/tree", "code/blob/README.md", "resolve?path=README.md", ""):
        status, _, body = files.get(path, token=other)
        refused = body_json(body)
        assert status == 403 and refused["reason"] == "no-files-permission", refused
        assert b"# Read me" not in body
    assert "kyo devices revoke phone" in refused["error"]
    assert "`kyo devices issue <name> --permission files`" in refused["error"]

    # The remedy, as the sentence spells it: revoke, and issue with the permission.
    assert invoke(["devices", "revoke", "phone"]).code == 0
    again = issue("phone", "files")
    assert files.get("code/blob/README.md", token=again)[0] == 200
    assert files.json("code/tree", token=again)[0] == 200
    # The revoked token is refused from then on.
    status, refused = files.json("code/tree", token=other)
    assert status == 401 and refused["reason"] == "revoked-token"


def test_kyo_devices_grants_and_lists_the_files_permission(store_home: Path) -> None:
    assert "files" in device_tokens.PERMISSIONS
    made = invoke(["devices", "issue", "ipad", "--permission", "files", "--json"])
    assert made.code == 0 and made.json["device"]["permissions"] == ["files"]
    listed = invoke(["devices", "list"])
    assert listed.code == 0 and "permissions: files" in listed.out
    assert "files" in invoke(["devices", "--help"]).out


def test_the_routes_answer_get_only(files: Served) -> None:
    for path in ("code/tree", "code/blob/README.md", ""):
        status, headers, _ = files.client.request(
            f"{files.base}/{path}",
            method="PUT",
            headers={"Authorization": f"Bearer {files.token}", "If-None-Match": "*"},
            body=b"overwritten",
        )
        assert status == 405 and headers["Allow"] == "GET"
    assert (files.code / "README.md").read_bytes() == b"# Read me\n"


def test_the_module_writes_no_file() -> None:
    source = (Path(kyosei.__file__).parent / "serve_tree.py").read_text(encoding="utf-8")
    assert file_writes(source, "serve_tree.py") == []
    # And it opens nothing by a mode: its one read is `read_bytes`.
    calls = {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "read_bytes" in calls and "open" not in calls


# ----------------------------------------------------------- c5: the scratch root


@pytest.mark.parametrize(
    "path",
    [
        "scratch/tree",
        "scratch/blob/notes.md",
        "scratch/tree?session=11111111-2222-3333-4444-555555555555",
    ],
)
def test_the_scratch_root_is_refused_because_no_directory_is_recorded(
    files: Served, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    def touched(*args, **kwargs):
        raise AssertionError("no directory is known, so none may be looked at")

    monkeypatch.setattr(serve_tree, "_real_inside", touched)
    monkeypatch.setattr(serve_tree, "_git_shows", touched)
    status, refused = files.json(path)
    assert status in (400, 404), refused
    if status == 404:
        assert refused["reason"] == "scratch-not-recorded"
        assert "`code` and `store`" in refused["error"]


def test_an_unknown_root_and_an_unknown_route_are_refused(files: Served) -> None:
    status, refused = files.json("home/tree")
    assert status == 404 and refused["reason"] == "no-such-root"
    status, refused = files.json("code/raw/README.md")
    assert status == 404 and refused["reason"] == "not-served"
    status, refused = files.json("code")
    assert status == 404 and refused["reason"] == "not-served"


# ------------------------------------------------- c6: a path as an agent writes one


def resolved(files: Served, text: str) -> dict:
    status, document = files.json("resolve?path=" + urllib.parse.quote(text, safe=""))
    assert status == 200, document
    assert document["given"] == text
    return document


def test_an_absolute_path_resolves_to_its_root_and_relative_path(files: Served) -> None:
    found = resolved(files, str(files.code / "src" / "widget.py"))
    assert found["found"] is True
    assert (found["root"], found["path"], found["line"]) == ("code", "src/widget.py", None)
    assert found["type"] == "file" and found["media_type"] == "text/x-python"

    # Written by its real path (a temporary directory is a symlink on macOS).
    real = os.path.realpath(files.code / "docs" / "plan.md")
    assert resolved(files, real)["path"] == "docs/plan.md"

    write(files.store, "docs/proposals/native-apps.md", "# Native apps\n")
    found = resolved(files, str(files.store / "docs" / "proposals" / "native-apps.md"))
    assert (found["root"], found["path"]) == ("store", "docs/proposals/native-apps.md")


def test_a_relative_path_is_tried_against_each_repository(files: Served) -> None:
    found = resolved(files, "src/widget.py")
    assert (found["root"], found["path"]) == ("code", "src/widget.py")
    assert resolved(files, "./docs/plan.md")["path"] == "docs/plan.md"

    write(files.store, "docs/proposals/native-apps.md", "# Native apps\n")
    found = resolved(files, "docs/proposals/native-apps.md")
    assert (found["root"], found["path"]) == ("store", "docs/proposals/native-apps.md")

    found = resolved(files, "src")
    assert (found["root"], found["path"], found["type"]) == ("code", "src", "dir")


@pytest.mark.parametrize(
    ("suffix", "line"), [(":120", 120), (":120:7", 120), (":12-20", 12)]
)
def test_a_line_after_the_path_is_returned(files: Served, suffix: str, line: int) -> None:
    found = resolved(files, "src/widget.py" + suffix)
    assert (found["root"], found["path"], found["line"]) == ("code", "src/widget.py", line)
    found = resolved(files, str(files.code / "src" / "widget.py") + suffix)
    assert (found["root"], found["path"], found["line"]) == ("code", "src/widget.py", line)


def test_a_file_whose_name_ends_in_a_number_is_found_whole(files: Served) -> None:
    write(files.code, "notes:12", "odd name\n")
    found = resolved(files, "notes:12")
    assert (found["path"], found["line"]) == ("notes:12", None)


def test_a_path_outside_every_root_is_said_to_be_outside(
    files: Served, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = write(tmp_path / "outside", "secret.md", "outside\n")
    lstat = Path.lstat

    def watched(self: Path, *args, **kwargs):
        assert not str(self).startswith(str(outside.parent)), "looked outside every root"
        return lstat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", watched)
    texts = (
        str(outside),
        "/etc/passwd",
        "~/.ssh/id_ed25519",
        f"{files.code}/../outside/secret.md",
    )
    for text in texts:
        answer = resolved(files, text)
        assert answer["found"] is False and answer["root"] is None and answer["path"] is None
        assert answer["reason"] == "outside-every-root", answer
        assert answer["roots"] == ["code", "store"]


@pytest.mark.parametrize(
    "text", ["absent/file.md", "secret.env", "build/out.txt", "../outside/secret.md"]
)
def test_a_path_no_root_holds_or_git_does_not_show_is_not_found(
    files: Served, text: str
) -> None:
    answer = resolved(files, text)
    assert answer["found"] is False and answer["reason"] == "not-found"
    absolute = resolved(files, str(files.code / "secret.env"))
    assert absolute["found"] is False and absolute["reason"] == "not-found"


def test_a_link_that_leaves_the_root_does_not_resolve(files: Served, tmp_path: Path) -> None:
    outside = write(tmp_path / "outside", "secret.md", "outside\n")
    (files.code / "leak.md").symlink_to(outside)
    assert resolved(files, "leak.md")["found"] is False


def test_the_resolve_route_takes_exactly_one_path(files: Served) -> None:
    for query in ("", "?path=a&path=b", "?file=a", "?path=", "?path=a%00b"):
        status, refused = files.json("resolve" + query)
        assert status == 400 and refused["reason"] == "bad-request", (query, refused)
    assert files.json("resolve?path=README.md")[0] == 200


# --------------------------------------------------------------- c7: the limits


def test_a_file_over_the_limit_is_refused_with_its_size(
    files: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(serve_tree, "MAX_FILE_BYTES", 64)
    write(files.code, "big.bin", b"x" * 65)
    write(files.code, "fits.bin", b"x" * 64)
    status, _, body = files.get("code/blob/big.bin")
    refused = body_json(body)
    assert status == 413 and refused["reason"] == "file-too-large"
    assert refused["size"] == 65 and refused["limit"] == 64
    assert "65 bytes" in refused["error"] and b"xxxx" not in body
    status, _, body = files.get("code/blob/fits.bin")
    assert status == 200 and len(body) == 64
    # The listing still names the large file and its size.
    by_name = {entry["name"]: entry for entry in files.json("code/tree")[1]["entries"]}
    assert by_name["big.bin"]["size"] == 65


def test_the_limit_is_the_one_the_reference_states() -> None:
    assert serve_tree.MAX_FILE_BYTES == 32 * 1024 * 1024
    topic = Path(kyosei.__file__).parents[2] / "docs/reference/topics/serve-and-desktop.md"
    assert "32 MiB" in topic.read_text(encoding="utf-8")


def test_the_listing_of_a_large_directory_is_complete(files: Served) -> None:
    count = 3000
    many = files.code / "many"
    many.mkdir()
    for index in range(count):
        (many / f"file-{index:05d}.txt").write_bytes(b"")
    status, listed = files.json("code/tree/many")
    assert status == 200
    assert names(listed) == [f"file-{index:05d}.txt" for index in range(count)]
