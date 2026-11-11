"""KYO-814: `kyo serve`'s file search and its recently opened files.

Every request goes over a real socket against a split-layout project built under the test's
temporary directory, as the file routes' own tests do.
"""

# ruff: noqa: F811 - a fixture borrowed from another test module is named by its parameters

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from kyosei import device_tokens, serve, serve_files, serve_tree

from .conftest import git_init
from .test_kyo_serve import body_json, register, running  # noqa: F401
from .test_kyo_serve_send import issue
from .test_kyo_serve_tree import Served, git, write


@pytest.fixture
def served(split_project, store_home: Path, running) -> Served:
    code, store = split_project
    write(code, ".gitignore", "secret.env\n")
    write(code, "notes/Needle-name.md", "nothing here\n")
    write(code, "src/body.py", "a = 1\nthe NEEDLE is here\nb = 2\n")
    write(code, "src/two words.txt", "a needle thread\n")
    write(code, "bin/blob.dat", b"\x00\x01needle\x00")
    git(code, "add", "-A")
    git(code, "commit", "-q", "-m", "files")
    write(code, "secret.env", "needle=1\n")
    write(store, "plans/other.md", "a needle in the store\n")
    write(store, "plans/needle-plan.md", "plain\n")
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


def post(served: Served, path: str, document: dict, token: str | None = "held"):
    headers = {"Content-Type": "application/json"}
    chosen = served.token if token == "held" else token
    if chosen is not None:
        headers["Authorization"] = f"Bearer {chosen}"
    status, _, body = served.client.request(
        f"{served.base}/{path}",
        method="POST",
        headers=headers,
        body=json.dumps(document).encode("utf-8"),
    )
    return status, body_json(body)


def found(document: dict) -> set[tuple]:
    return {
        (item["root"], item["path"], item["match"], item.get("line"))
        for item in document["results"]
    }


# --------------------------------------------------------------- c1, c2, c3: search


def test_search_finds_names_and_lines_in_both_roots_and_root_narrows_it(served: Served) -> None:
    status, both = served.json("search?q=needle")
    assert status == 200, both
    assert found(both) == {
        ("code", "notes/Needle-name.md", "name", None),
        ("code", "src/body.py", "contents", 2),
        ("code", "src/two words.txt", "contents", 1),
        ("store", "plans/needle-plan.md", "name", None),
        ("store", "plans/other.md", "contents", 1),
    }
    line = next(item for item in both["results"] if item["path"] == "src/body.py")
    assert line["text"] == "the NEEDLE is here"
    # grouped by root: every code result before every store result
    roots = [item["root"] for item in both["results"]]
    assert roots == sorted(roots, key=["code", "store"].index)

    # found by name in the store and by contents in the code root
    _, code_only = served.json("search?q=needle&root=code")
    _, store_only = served.json("search?q=needle&root=store")
    assert {item["root"] for item in code_only["results"]} == {"code"}
    assert {item["root"] for item in store_only["results"]} == {"store"}
    assert found(both) == found(code_only) | found(store_only)
    assert {item["match"] for item in store_only["results"]} == {"name", "contents"}


def test_a_file_gives_at_most_five_lines_with_its_count_and_every_result_its_time(
    served: Served,
) -> None:
    # KYO-818: the app draws one row per file, ordered partly by when it was modified.
    write(served.code, "src/many.txt", "".join(f"needle {n}\n" for n in range(1, 9)))
    git(served.code, "add", "src/many.txt")
    _, answer = served.json("search?q=needle&root=code")
    many = [item for item in answer["results"] if item["path"] == "src/many.txt"]
    assert [item["line"] for item in many] == [1, 2, 3, 4, 5]
    assert {item["count"] for item in many} == {8}
    assert all(isinstance(item["mtime_ns"], int) for item in answer["results"])


def test_search_matches_one_phrase_and_names_the_remedy_for_a_bad_request(
    served: Served,
) -> None:
    _, phrase = served.json("search?q=needle+thread")
    assert found(phrase) == {("code", "src/two words.txt", "contents", 1)}
    _, none = served.json("search?q=needle+store+thread")
    assert none["results"] == []
    status, refused = served.json("search")
    assert status == 400 and refused["reason"] == "bad-request"
    status, refused = served.json("search?q=needle&root=scratch")
    assert status == 404
    status, _ = served.json("search?q=needle&root=code")
    assert status == 200


def test_search_never_finds_an_ignored_file_or_reads_a_binary_or_oversize_one(
    served: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = served.json("search?q=needle")
    paths = {item["path"] for item in result["results"]}
    assert "secret.env" not in paths  # ignored, holds the text
    assert "bin/blob.dat" not in paths  # binary, holds the text
    # a file past the limit is not read
    monkeypatch.setattr(serve_tree, "MAX_FILE_BYTES", 5)
    _, small = served.json("search?q=needle")
    assert "src/body.py" not in {item["path"] for item in small["results"]}
    # ... but its name is still a name match
    _, named = served.json("search?q=body")
    assert found(named) == {("code", "src/body.py", "name", None)}


def test_search_does_not_follow_a_link_out_of_the_root(served: Served, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("needle outside\n", encoding="utf-8")
    (served.code / "link.txt").symlink_to(outside)
    _, result = served.json("search?q=needle")
    assert "link.txt" not in {item["path"] for item in result["results"]}


# ------------------------------------------------------------------------ c4: cap


def test_search_is_capped_and_says_whether_more_were_found(
    served: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, all_of_them = served.json("search?q=needle")
    assert all_of_them["more"] is False and all_of_them["limit"] == serve_tree.SEARCH_LIMIT
    total = len(all_of_them["results"])
    monkeypatch.setattr(serve_tree, "SEARCH_LIMIT", total - 1)
    _, capped = served.json("search?q=needle")
    assert len(capped["results"]) == total - 1
    assert capped["more"] is True and capped["limit"] == total - 1
    monkeypatch.setattr(serve_tree, "SEARCH_LIMIT", total)
    _, exact = served.json("search?q=needle")
    assert len(exact["results"]) == total and exact["more"] is False


# ----------------------------------------------------------- c5, c6, c7: recent


def test_recent_lists_newest_first_each_once_and_is_shared_between_devices(
    served: Served, store_home: Path
) -> None:
    status, empty = served.json("recent")
    assert status == 200 and empty["entries"] == []
    assert empty["limit"] == serve_tree.RECENT_LIMIT
    opens = [("code", "src/widget.py"), ("store", "plans/other.md"), ("code", "src/widget.py")]
    for root, path in opens:
        status, listed = post(served, "recent", {"root": root, "path": path})
        assert status == 200, listed
    other = issue("phone", device_tokens.PERMISSION_FILES)
    status, listed = served.json("recent", token=other)
    assert status == 200
    assert [(e["root"], e["path"]) for e in listed["entries"]] == [
        ("code", "src/widget.py"),
        ("store", "plans/other.md"),
    ]
    assert all(e["opened_at"] for e in listed["entries"])

    kept = serve_files.project_recent_path(
        {"KYOSEI_HOME": str(store_home)} if False else _environ(store_home),
        serve.project_name(str(served.code)),
    )
    assert kept.exists() and stat.S_IMODE(kept.stat().st_mode) == 0o600
    assert serve_files.composer_kind_directory(
        _environ(store_home), serve_files.PROJECT_RECENT
    ).parent == serve_files.composer_kind_directory(
        _environ(store_home), serve_files.PROJECT_DRAFTS
    ).parent


def _environ(store_home: Path) -> dict[str, str]:
    import os

    return dict(os.environ)


def test_recent_drops_the_oldest_past_its_maximum(
    served: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(serve_tree, "RECENT_LIMIT", 2)
    for path in ("src/widget.py", "src/body.py", "notes/Needle-name.md"):
        assert post(served, "recent", {"root": "code", "path": path})[0] == 200
    _, listed = served.json("recent")
    assert [e["path"] for e in listed["entries"]] == ["notes/Needle-name.md", "src/body.py"]
    assert listed["limit"] == 2


def test_recent_refuses_a_path_the_tree_does_not_serve_and_the_remedy_clears_it(
    served: Served,
) -> None:
    for root, path in [
        ("code", "secret.env"),  # ignored
        ("code", "../escape"),
        ("code", ".git/config"),
        ("code", "nothing.md"),
        ("code", "src"),
    ]:
        status, refused = post(served, "recent", {"root": root, "path": path})
        assert status in (403, 404, 409, 400), (path, status, refused)
        assert refused["ok"] is False
    status, refused = post(served, "recent", {"root": "scratch", "path": "x"})
    assert status == 404
    status, refused = post(served, "recent", {"path": "src/widget.py"})
    assert status == 400 and refused["reason"] == "bad-request"
    assert served.json("recent")[1]["entries"] == []
    # the remedy: name a file the tree lists
    assert post(served, "recent", {"root": "code", "path": "src/body.py"})[0] == 200


def test_both_routes_need_a_token_holding_the_files_permission(served: Served) -> None:
    paperless = issue("typist", device_tokens.PERMISSION_SEND)
    for token, status_expected in [(None, 401), (paperless, 403)]:
        assert served.json("search?q=needle", token=token)[0] == status_expected
        assert served.json("recent", token=token)[0] == status_expected
        assert (
            post(served, "recent", {"root": "code", "path": "src/widget.py"}, token)[0]
            == status_expected
        )
    assert served.json("search?q=needle")[0] == 200
    assert served.json("recent")[0] == 200
