"""KYO-837: `kyo serve`'s starred files, the server half of the stars of KYO-835.

Every request goes over a real socket against a split-layout project, as the recent route's
tests (KYO-814) do.
"""

# ruff: noqa: F811 - a fixture borrowed from another test module is named by its parameters

from __future__ import annotations

import json
import os
import stat

import pytest

from kyosei import device_tokens, serve, serve_files, serve_tree

from .test_kyo_serve import body_json, running  # noqa: F401
from .test_kyo_serve_recent import served  # noqa: F401
from .test_kyo_serve_send import issue
from .test_kyo_serve_tree import Served


def call(served: Served, method: str, document: dict | None = None, token: str | None = "held"):
    headers = {"Content-Type": "application/json"}
    chosen = served.token if token == "held" else token
    if chosen is not None:
        headers["Authorization"] = f"Bearer {chosen}"
    status, _, body = served.client.request(
        f"{served.base}/starred",
        method=method,
        headers=headers,
        body=None if document is None else json.dumps(document).encode("utf-8"),
    )
    return status, body_json(body)


def paths_of(document: dict) -> list[tuple[str, str]]:
    return [(e["root"], e["path"]) for e in document["entries"]]


def test_stars_list_newest_first_each_once_and_are_shared_between_devices(
    served: Served,
) -> None:
    status, empty = call(served, "GET")
    assert status == 200 and empty["entries"] == []
    assert empty["limit"] == serve_tree.STARRED_LIMIT
    for root, path in [
        ("code", "src/widget.py"),
        ("store", "plans/other.md"),
        ("code", "src/widget.py"),  # a repeat
    ]:
        status, listed = call(served, "POST", {"root": root, "path": path})
        assert status == 200, listed
    other = issue("phone", device_tokens.PERMISSION_FILES)
    status, listed = call(served, "GET", token=other)
    assert status == 200
    assert paths_of(listed) == [("store", "plans/other.md"), ("code", "src/widget.py")]
    assert all(e["starred_at"] for e in listed["entries"])


def test_unstar_removes_one_answers_the_list_and_forgives_what_is_not_starred(
    served: Served,
) -> None:
    for path in ("src/widget.py", "src/body.py"):
        call(served, "POST", {"root": "code", "path": path})
    status, listed = call(served, "DELETE", {"root": "code", "path": "src/widget.py"})
    assert status == 200 and paths_of(listed) == [("code", "src/body.py")]
    status, again = call(served, "DELETE", {"root": "code", "path": "src/widget.py"})
    assert status == 200 and paths_of(again) == [("code", "src/body.py")]
    assert paths_of(call(served, "GET")[1]) == [("code", "src/body.py")]


def test_a_star_of_a_file_since_deleted_can_still_be_removed(served: Served) -> None:
    (served.code / "src" / "body.py").unlink()
    # the file is still shown by git until the removal is committed; take it from the list
    # through the one route that does not need the file
    call(served, "POST", {"root": "code", "path": "src/widget.py"})
    path = serve_files.project_starred_path(dict(os.environ), serve.project_name(str(served.code)))
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["entries"].append(
        {"root": "code", "path": "gone/away.py", "starred_at": "2026-01-01T00:00:00.000000Z"}
    )
    path.write_text(json.dumps(stored), encoding="utf-8")
    assert ("code", "gone/away.py") in paths_of(call(served, "GET")[1])
    status, listed = call(served, "DELETE", {"root": "code", "path": "gone/away.py"})
    assert status == 200 and ("code", "gone/away.py") not in paths_of(listed)


def test_the_list_is_kept_beside_the_recent_files_private_and_outside_a_repository(
    served: Served,
) -> None:
    call(served, "POST", {"root": "code", "path": "src/widget.py"})
    environ = dict(os.environ)
    kept = serve_files.project_starred_path(environ, serve.project_name(str(served.code)))
    assert kept.exists() and stat.S_IMODE(kept.stat().st_mode) == 0o600
    assert kept.parent.parent == serve_files.composer_kind_directory(
        environ, serve_files.PROJECT_RECENT
    ).parent
    assert kept.parent != serve_files.composer_kind_directory(environ, serve_files.PROJECT_RECENT)
    assert serve_files.enclosing_git_repository(kept.parent) is None


def test_a_full_list_refuses_another_star_and_unstarring_clears_it(
    served: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(serve_tree, "STARRED_LIMIT", 2)
    for path in ("src/widget.py", "src/body.py"):
        assert call(served, "POST", {"root": "code", "path": path})[0] == 200
    status, refused = call(served, "POST", {"root": "code", "path": "notes/Needle-name.md"})
    assert status == 409 and refused["reason"] == "starred-full"
    assert len(call(served, "GET")[1]["entries"]) == 2
    assert call(served, "DELETE", {"root": "code", "path": "src/body.py"})[0] == 200
    assert call(served, "POST", {"root": "code", "path": "notes/Needle-name.md"})[0] == 200


def test_a_path_the_tree_does_not_serve_is_refused_and_the_remedy_clears_it(
    served: Served,
) -> None:
    for root, path in [
        ("code", "secret.env"),  # ignored
        ("code", "../escape"),
        ("code", ".git/config"),
        ("code", "nothing.md"),
        ("code", "src"),
    ]:
        status, refused = call(served, "POST", {"root": root, "path": path})
        assert status in (400, 403, 404, 409), (path, status, refused)
        assert refused["ok"] is False
    for method in ("POST", "DELETE"):
        assert call(served, method, {"root": "scratch", "path": "x"})[0] == 404
        status, refused = call(served, method, {"path": "src/widget.py"})
        assert status == 400 and refused["reason"] == "bad-request"
    assert call(served, "DELETE", {"root": "code", "path": "../escape"})[0] in (400, 403, 404)
    assert call(served, "GET")[1]["entries"] == []
    assert call(served, "POST", {"root": "code", "path": "src/body.py"})[0] == 200


def test_every_method_needs_a_token_holding_the_files_permission(served: Served) -> None:
    paperless = issue("typist", device_tokens.PERMISSION_SEND)
    body = {"root": "code", "path": "src/widget.py"}
    for token, expected in [(None, 401), (paperless, 403)]:
        for method in ("GET", "POST", "DELETE"):
            document = None if method == "GET" else body
            assert call(served, method, document, token)[0] == expected, method
    assert call(served, "GET")[1]["entries"] == []
