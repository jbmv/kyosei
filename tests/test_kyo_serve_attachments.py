"""KYO-715: `kyo serve`'s attachments, and a composer prompt that attaches them.

Every request goes over a real socket against a project the test built, with tmux faked as in
the send, held and note tests. The store home is the test's own temporary directory. Each
refusal that names a remedy is followed in the same test by that remedy and the request again.
"""

# ruff: noqa: F811 - a fixture borrowed from another test module is named by its parameters

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

from kyosei import serve_attachments

from .conftest import invoke
from .test_kyo_serve import body_json
from .test_kyo_serve_held import wait_until
from .test_kyo_serve_notes import (  # noqa: F401 - the fixtures, by name
    HELD_ROUTE,
    SEND_PROMPT,
    can_send,
    file_note,
    get,
    notes_path,
    token,
    working_screen,
)
from .test_kyo_serve_send import IDLE, enable, issue, keys_sent, post
from .test_kyo_serve_sessions import (  # noqa: F401 - the fixtures, by name
    OLD,
    SESSIONS,
    Bed,
    bed,
    running,
)

ATTACHMENTS = f"{SESSIONS}/{OLD}/attachments"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR a small picture"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF a photograph"
GIF = b"GIF89a a moving picture"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 a web picture"
PDF = b"%PDF-1.7\n a document"


def upload(
    bed: Bed, data: bytes, media_type: str | None, token: str | None
) -> tuple[int, dict]:
    headers = {}
    if media_type is not None:
        headers["Content-Type"] = media_type
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    status, _, body = bed.client.request(ATTACHMENTS, method="POST", headers=headers, body=data)
    return status, body_json(body)


def directory(store_home: Path) -> Path:
    return Path(
        os.path.realpath(store_home / "composer" / "attachments" / "proj" / OLD)
    )


def saved(store_home: Path) -> list[str]:
    found = directory(store_home)
    return sorted(path.name for path in found.iterdir()) if found.is_dir() else []


# ------------------------------------------------------------------- c1: saving a file


def test_an_attachment_is_saved_by_its_digest_and_read_back(
    bed: Bed, store_home: Path, token: str
) -> None:
    status, made = upload(bed, PNG, "image/png", token)
    assert status == 201, made
    digest = hashlib.sha256(PNG).hexdigest()
    path = directory(store_home) / f"{digest}.png"
    assert made == {
        "ok": True,
        "id": digest,
        "path": str(path),
        "media_type": "image/png",
        "bytes": len(PNG),
    }
    assert Path(made["path"]).is_absolute() and path.read_bytes() == PNG
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(directory(store_home).stat().st_mode) == 0o700

    # The same bytes again are the same file and the same id.
    status, again = upload(bed, PNG, "image/png; charset=binary", token)
    assert status == 200 and again == made
    assert saved(store_home) == [f"{digest}.png"]

    status, headers, body = bed.client.request(
        f"{ATTACHMENTS}/{digest}", headers={"Authorization": f"Bearer {token}"}
    )
    assert status == 200 and body == PNG
    assert headers["Content-Type"] == "image/png"
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize(
    ("data", "media_type", "extension"),
    [(JPEG, "image/jpeg", "jpg"), (GIF, "image/gif", "gif"), (WEBP, "image/webp", "webp"),
     (PDF, "application/pdf", "pdf"), (b"GIF87a old", "image/gif", "gif")],
)
def test_each_accepted_type_is_saved_with_its_own_extension(
    bed: Bed, store_home: Path, token: str, data: bytes, media_type: str, extension: str
) -> None:
    status, made = upload(bed, data, media_type, token)
    assert status == 201, made
    assert made["path"].endswith(f"{hashlib.sha256(data).hexdigest()}.{extension}")
    assert made["media_type"] == media_type


def test_reading_an_unknown_attachment_is_refused(bed: Bed, token: str) -> None:
    for unknown in ("0" * 64, "..%2F..%2Fdevice-tokens.json", "not-an-id"):
        status, _, body = bed.client.request(
            f"{ATTACHMENTS}/{unknown}", headers={"Authorization": f"Bearer {token}"}
        )
        refused = body_json(body)
        assert status == 404 and refused["reason"] == "attachment-not-found", refused
        assert "POST the file" in refused["error"]


# ---------------------------------------------------------------- c2: what is refused


@pytest.mark.parametrize(
    ("data", "media_type", "status", "reason"),
    [
        (b"plain words", "text/plain", 415, "attachment-type-not-accepted"),
        (b'{"a": 1}', "application/json", 415, "attachment-type-not-accepted"),
        (PNG, "image/svg+xml", 415, "attachment-type-not-accepted"),
        (PNG, None, 415, "attachment-type-not-accepted"),
        (JPEG, "image/png", 415, "attachment-content-mismatch"),
        (b"not a picture at all", "image/png", 415, "attachment-content-mismatch"),
        (b"", "image/png", 400, "attachment-empty"),
    ],
)
def test_a_bad_attachment_is_refused_with_a_reason_and_nothing_is_saved(
    bed: Bed,
    store_home: Path,
    token: str,
    data: bytes,
    media_type: str | None,
    status: int,
    reason: str,
) -> None:
    got, refused = upload(bed, data, media_type, token)
    assert (got, refused["reason"]) == (status, reason), refused
    assert saved(store_home) == []
    # The remedy each names: the file's own bytes with the type they have.
    assert upload(bed, PNG, "image/png", token)[0] == 201


def test_a_mismatch_names_the_type_the_bytes_have(bed: Bed, token: str) -> None:
    _, refused = upload(bed, JPEG, "image/png", token)
    assert refused["declared"] == "image/png" and refused["found"] == "image/jpeg"
    assert "the bytes are those of image/jpeg" in refused["error"]
    assert upload(bed, JPEG, "image/jpeg", token)[0] == 201


def test_a_body_over_the_limit_is_refused_and_nothing_is_saved(
    bed: Bed, store_home: Path, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(serve_attachments, "MAX_ATTACHMENT_BYTES", len(PNG) - 1)
    status, refused = upload(bed, PNG, "image/png", token)
    assert status == 413 and refused["reason"] == "attachment-too-large", refused
    assert refused["limit"] == len(PNG) - 1 and "send a smaller file" in refused["error"]
    assert saved(store_home) == []
    small = PNG[: len(PNG) - 1]
    assert upload(bed, small, "image/png", token)[0] == 201


def test_attaching_needs_a_token_with_the_send_permission(
    bed: Bed, store_home: Path
) -> None:
    status, refused = upload(bed, PNG, "image/png", None)
    assert status == 401, refused
    notes_only = issue("phone", "notes")
    status, refused = upload(bed, PNG, "image/png", notes_only)
    assert status == 403 and refused["reason"] == "no-send-permission"
    assert saved(store_home) == []
    assert invoke(["devices", "revoke", "phone"]).code == 0
    assert upload(bed, PNG, "image/png", issue("phone", "send"))[0] == 201


def test_the_routes_answer_only_their_methods(bed: Bed, token: str) -> None:
    auth = {"Authorization": f"Bearer {token}"}
    assert bed.client.request(ATTACHMENTS, headers=auth)[0] == 405
    status, headers, _ = bed.client.request(
        f"{ATTACHMENTS}/{'0' * 64}",
        method="POST",
        headers={**auth, "Content-Type": "application/json"},
        body=b"{}",
    )
    assert status == 405 and headers["Allow"] == "GET"


# ------------------------------------------------------ c3: a prompt that attaches them

SEGMENTS = [{"kind": "typed", "text": "look at this\nand this one"}]


def test_a_marks_attachments_become_attached_lines_after_its_on_lines(
    bed: Bed, notes_path: str, can_send: str
) -> None:
    first = file_note(bed, notes_path, can_send)
    _, png = upload(bed, PNG, "image/png", can_send)
    _, pdf = upload(bed, PDF, "application/pdf", can_send)
    document = {
        "segments": SEGMENTS,
        "marks": [
            {"line": 1, "anchors": [{"kind": "note", "id": first["id"]}],
             "attachments": [png["id"], pdf["id"]]},
        ],
    }
    status, sent = post(bed, SEND_PROMPT, document, can_send)
    assert status == 200, sent
    assert sent["message"] == (
        "look at this\n"
        f"on note {first['id']}\n"
        f"attached {png['path']}\n"
        f"attached {pdf['path']}\n"
        "and this one"
    )


def test_an_unknown_attachment_is_refused_and_nothing_is_held(
    bed: Bed, can_send: str
) -> None:
    document = {"segments": SEGMENTS, "marks": [{"line": 0, "attachments": ["f" * 64]}]}
    status, refused = post(bed, SEND_PROMPT, document, can_send)
    assert status == 400 and refused["reason"] == "bad-request", refused
    assert "marks[0].attachments[0]" in refused["error"]
    assert "POST the file" in refused["error"]
    assert get(bed, HELD_ROUTE, can_send)[1]["held"] is None
    # The remedy: attach the file first and send its id.
    _, png = upload(bed, PNG, "image/png", can_send)
    document["marks"][0]["attachments"] = [png["id"]]
    assert post(bed, SEND_PROMPT, document, can_send)[0] == 200


@pytest.mark.parametrize(
    ("attachments", "words"),
    [("x", '"attachments" must be a list'), ([1], '"attachments" must be a list'),
     (["a", "a"], "names an attachment twice")],
)
def test_malformed_attachments_are_refused(
    bed: Bed, can_send: str, attachments: object, words: str
) -> None:
    document = {"segments": SEGMENTS, "marks": [{"line": 0, "attachments": attachments}]}
    status, refused = post(bed, SEND_PROMPT, document, can_send)
    assert status == 400 and words in refused["error"], refused
    assert get(bed, HELD_ROUTE, can_send)[1]["held"] is None


# ------------------------------------------------- c4: attachments alone file no note


def test_attachments_alone_need_only_send_and_file_no_note(
    bed: Bed, notes_path: str, store_home: Path
) -> None:
    enable(store_home)
    send_only = issue("laptop", "send")
    _, png = upload(bed, PNG, "image/png", send_only)
    marks = [{"line": 0, "attachments": [png["id"]]}]
    status, sent = post(bed, SEND_PROMPT, {"segments": SEGMENTS, "marks": marks}, send_only)
    assert status == 200, sent
    assert sent["message"] == f"attached {png['path']}\nlook at this\nand this one"
    held = get(bed, HELD_ROUTE, send_only)[1]["held"]
    assert held["prompt"] == {"segments": SEGMENTS, "marks": marks}
    bed.show(IDLE)
    wait_until(lambda: get(bed, HELD_ROUTE, send_only)[1]["held"] is None)
    assert any("attached" in key for key in keys_sent(bed))
    reader = issue("reader", "notes")
    assert get(bed, notes_path, reader)[1]["count"] == 0
    assert Path(png["path"]).is_file(), "an attachment is kept once it is sent"


def test_a_retracted_prompt_gives_back_its_attachments_with_its_marks(
    bed: Bed, can_send: str
) -> None:
    _, png = upload(bed, PNG, "image/png", can_send)
    marks = [{"line": 1, "tags": ["look"], "attachments": [png["id"]]}]
    _, sent = post(bed, SEND_PROMPT, {"segments": SEGMENTS, "marks": marks}, can_send)
    status, _, body = bed.client.request(
        HELD_ROUTE,
        method="DELETE",
        headers={"Authorization": f"Bearer {can_send}", "If-Match": sent["held"]["id"]},
    )
    retracted = body_json(body)
    assert status == 200, retracted
    assert retracted["prompt"] == {"segments": SEGMENTS, "marks": marks}
    assert retracted["prompt"]["marks"][0]["attachments"] == [png["id"]]
