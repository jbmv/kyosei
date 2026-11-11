"""KYO-826: the transcript search route's role and context, and the project's opened results.

Every request goes over a real socket against the session-route test bed.
"""

# ruff: noqa: F811 - a fixture borrowed from another test module is named by its parameters

from __future__ import annotations

import json
import os
import stat
import urllib.parse

import pytest

from kyosei import device_tokens, serve_files, serve_sessions

from .test_kyo_serve import body_json
from .test_kyo_serve_sessions import (  # noqa: F401 - the fixtures, by name
    BASE,
    ELSEWHERE,
    ENDED,
    OLD,
    SESSIONS,
    Bed,
    bed,
    issue_reader,
    running,
)

HISTORY = f"{BASE}/history/recent"
SEARCH = f"{BASE}/search"


def search(bed: Bed, phrase: str, **more: str) -> tuple[int, dict]:
    status, _, body = bed.get(f"{SEARCH}?{urllib.parse.urlencode({'q': phrase, **more})}")
    return status, body_json(body)


def post(bed: Bed, document: dict, headers: dict[str, str] | None = None):
    chosen = bed.client.reader if headers is None else headers
    status, _, body = bed.client.request(
        HISTORY,
        method="POST",
        headers={**chosen, "Content-Type": "application/json"},
        body=json.dumps(document).encode("utf-8"),
    )
    return status, body_json(body)


def listed(bed: Bed, headers: dict[str, str] | None = None):
    chosen = bed.client.reader if headers is None else headers
    status, _, body = bed.client.request(HISTORY, headers=chosen)
    return status, body_json(body)


# ------------------------------------------------------------- c1, c2: the search route


def test_the_search_route_takes_a_role_and_refuses_an_unknown_one(bed: Bed) -> None:
    _, both = search(bed, "ok", limit="100")
    assert {r["role"] for r in both["results"]} == {"person", "assistant"}
    for role in ("person", "assistant"):
        status, only = search(bed, "ok", limit="100", role=role)
        assert status == 200
        assert only["results"] == [r for r in both["results"] if r["role"] == role]
    status, refused = search(bed, "ok", role="user")
    assert status == 400 and "`person`" in refused["error"] and "`assistant`" in refused["error"]


def test_the_search_route_takes_a_context_and_refuses_one_over_the_limit(bed: Bed) -> None:
    def kept(document: dict) -> int:
        pieces = document["results"][0]["excerpt"]
        return len("".join(p["text"] for p in pieces if p["text"] != "…"))

    phrase = "a fixed structure"
    _, default = search(bed, phrase)
    _, narrow = search(bed, phrase, context="3")
    _, wide = search(bed, phrase, context="400")
    assert kept(narrow) == len(phrase) + 6
    assert kept(narrow) < kept(default) <= kept(wide)
    status, refused = search(bed, phrase, context="401")
    assert status == 400 and "between 0 and 400" in refused["error"]
    status, refused = search(bed, phrase, context="many")
    assert status == 400 and "?context is a number" in refused["error"]
    assert search(bed, phrase, context="400")[0] == 200  # the remedy


# ---------------------------------------------------------------- c3: the history list


def test_the_history_lists_newest_first_each_message_once_and_states_its_maximum(
    bed: Bed,
) -> None:
    status, empty = listed(bed)
    assert status == 200 and empty["entries"] == []
    assert empty["limit"] == serve_sessions.HISTORY_LIMIT
    assert empty["words_limit"] == serve_sessions.HISTORY_WORDS_LIMIT
    for message, words in (("a", "first"), ("b", "second"), ("a", "first again")):
        status, kept = post(bed, {"session": OLD, "message": message, "words": words})
        assert status == 200, kept
    _, now = listed(bed)
    assert [(e["message"], e["words"]) for e in now["entries"]] == [
        ("a", "first again"),
        ("b", "second"),
    ]
    assert all(e["session"] == OLD and e["opened_at"] for e in now["entries"])
    assert kept["entries"] == now["entries"]


def test_the_history_drops_the_oldest_past_its_maximum(
    bed: Bed, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(serve_sessions, "HISTORY_LIMIT", 2)
    for message in ("a", "b", "c"):
        assert post(bed, {"session": OLD, "message": message, "words": message})[0] == 200
    _, now = listed(bed)
    assert now["limit"] == 2 and [e["message"] for e in now["entries"]] == ["c", "b"]


def test_a_message_of_another_session_is_listed_apart_and_a_session_of_another_project_refused(
    bed: Bed,
) -> None:
    assert post(bed, {"session": ENDED, "message": "a", "words": "x"})[0] == 200
    assert post(bed, {"session": OLD, "message": "a", "words": "y"})[0] == 200
    _, now = listed(bed)
    assert [(e["session"], e["words"]) for e in now["entries"]] == [(OLD, "y"), (ENDED, "x")]
    status, refused = post(bed, {"session": ELSEWHERE, "message": "a", "words": "z"})
    assert status == 404 and "is not recorded for this project" in refused["error"]
    assert len(listed(bed)[1]["entries"]) == 2
    # the remedy named: a session the sessions route lists is accepted
    assert post(bed, {"session": OLD, "message": "b", "words": "z"})[0] == 200
    listing = body_json(bed.get(SESSIONS)[2])
    assert OLD in json.dumps(listing)


def test_a_body_without_its_fields_is_refused_and_one_with_them_is_taken(bed: Bed) -> None:
    for document in (
        {},
        {"session": OLD, "message": "a"},
        {"session": OLD, "message": "", "words": "w"},
        {"session": OLD, "message": 3, "words": "w"},
        {"session": OLD, "message": "m" * 129, "words": "w"},
    ):
        status, refused = post(bed, document)
        assert status == 400 and refused["reason"] == "bad-request", (document, refused)
    assert post(bed, {"session": OLD, "message": "a", "words": "w"})[0] == 200
    assert listed(bed)[1]["entries"][0]["words"] == "w"


def test_words_over_the_stated_length_are_cut_not_refused(bed: Bed) -> None:
    longest = serve_sessions.HISTORY_WORDS_LIMIT
    status, kept = post(bed, {"session": OLD, "message": "a", "words": "w" * (longest + 50)})
    assert status == 200
    assert kept["entries"][0]["words"] == "w" * longest


# ------------------------------------------------------- c4: where it is kept, who sees it


def test_the_history_is_shared_between_devices_and_kept_private_beside_the_recent_files(
    bed: Bed,
) -> None:
    assert post(bed, {"session": OLD, "message": "a", "words": "from one"})[0] == 200
    other = issue_reader("other-device")
    assert other != bed.client.reader
    status, theirs = listed(bed, other)
    assert status == 200 and [e["words"] for e in theirs["entries"]] == ["from one"]

    environ = dict(os.environ)
    kept = serve_files.project_history_path(environ, "proj")
    assert kept.exists() and stat.S_IMODE(kept.stat().st_mode) == 0o600
    recent = serve_files.project_recent_path(environ, "proj")
    assert kept.parent.parent == recent.parent.parent  # both under $KYOSEI_HOME/composer
    assert kept.parent != recent.parent
    assert serve_files.composer_kind_directory(environ, serve_files.PROJECT_HISTORY) == kept.parent
    assert serve_files.enclosing_git_repository(kept.parent) is None


# ------------------------------------------------------------------- c5: the token


def test_the_history_routes_need_a_device_token_and_answer_with_one(bed: Bed) -> None:
    for headers in ({}, {"Authorization": "Bearer kyo_dev_not-one-this-machine-issued"}):
        status, refused = listed(bed, headers)
        assert status == 401, refused
        status, refused = post(bed, {"session": OLD, "message": "a", "words": "w"}, headers)
        assert status == 401, refused
    assert listed(bed)[1]["entries"] == []  # the refused POST wrote nothing
    _, token = device_tokens.issue("history-laptop", [])  # the remedy, as it is issued
    header = {"Authorization": f"Bearer {token}"}
    assert listed(bed, header)[0] == 200
    assert post(bed, {"session": OLD, "message": "a", "words": "w"}, header)[0] == 200


def test_the_history_route_takes_no_query_and_no_other_method_or_path(bed: Bed) -> None:
    status, _, body = bed.get(f"{HISTORY}?x=1")
    assert status == 400, body
    status, _, _ = bed.client.request(HISTORY, method="DELETE", headers=bed.client.reader)
    assert status == 405
    status, _, _ = bed.get(f"{BASE}/history")
    assert status == 404
