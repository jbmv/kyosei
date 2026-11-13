"""KYO-920: a box's attached files still send after the box has passed to another session.

A draft's boxes pass from a session that ended to the next one (KYO-763), and a box names its
files by id while the files stay under the session they were attached in. The server finds
such an id under the project's other sessions, and a prompt sent with it names a copy kept in
its own session. Every request goes over a real socket, as in the attachment tests.
"""

# ruff: noqa: F811 - a fixture borrowed from another test module is named by its parameters

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .test_kyo_serve import body_json
from .test_kyo_serve_notes import (  # noqa: F401 - the fixtures, by name
    HELD_ROUTE,
    SEND_PROMPT,
    can_send,
    get,
    token,
)
from .test_kyo_serve_send import post
from .test_kyo_serve_sessions import (  # noqa: F401 - the fixtures, by name
    ENDED,
    OLD,
    SESSIONS,
    Bed,
    bed,
    running,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR a picture pasted in the ended session"
SEGMENTS = [{"kind": "typed", "text": "look at this\nand this one"}]


def attachments(store_home: Path, session: str) -> Path:
    return Path(os.path.realpath(store_home / "composer" / "attachments" / "proj" / session))


def attach(bed: Bed, session: str, token: str, data: bytes = PNG) -> dict:
    status, _, body = bed.client.request(
        f"{SESSIONS}/{session}/attachments",
        method="POST",
        headers={"Content-Type": "image/png", "Authorization": f"Bearer {token}"},
        body=data,
    )
    made = body_json(body)
    assert status == 201, made
    return made


def draft(bed: Bed, session: str, token: str, boxes: list[dict] | None = None) -> dict:
    """Read a session's draft, or create it with ``boxes``."""
    headers = {"Authorization": f"Bearer {token}"}
    body = None
    method = "GET"
    if boxes is not None:
        method = "PUT"
        headers["If-None-Match"] = "*"
        body = json.dumps({"boxes": boxes}).encode("utf-8")
    status, _, raw = bed.client.request(
        f"{SESSIONS}/{session}/draft", method=method, headers=headers, body=body
    )
    answered = body_json(raw)
    assert status in (200, 201), answered
    return answered


def moved_box(bed: Bed, can_send: str, image: dict) -> dict:
    """A box with the image on its second line, written in the ended session and taken by
    the live one, as the composer would read it there."""
    mark = {"line": 1, "items": [{"kind": "file", "id": image["id"], "name": "shot.png"}]}
    draft(bed, ENDED, can_send, [{"id": "b1", "text": "look at this\nand this one",
                                  "marks": [mark]}])
    boxes = draft(bed, OLD, can_send)["boxes"]
    assert [box["id"] for box in boxes] == ["b1"], "the live session took the ended one's box"
    return boxes[0]


def test_a_file_attached_in_an_ended_session_sends_from_the_next(
    bed: Bed, store_home: Path, can_send: str
) -> None:
    image = attach(bed, ENDED, can_send)
    box = moved_box(bed, can_send, image)
    ids = [item["id"] for item in box["marks"][0]["items"]]
    document = {"segments": SEGMENTS, "marks": [{"line": 1, "attachments": ids}]}

    status, sent = post(bed, SEND_PROMPT, document, can_send)

    assert status == 200, sent
    copy = attachments(store_home, OLD) / f"{image['id']}.png"
    assert sent["message"] == f"look at this\nattached {copy}\nand this one"
    assert copy.read_bytes() == PNG, "the prompt names a copy in the session it was sent in"
    assert hashlib.sha256(copy.read_bytes()).hexdigest() == image["id"]
    assert Path(image["path"]).read_bytes() == PNG, "the file stays where it was attached"
    assert get(bed, HELD_ROUTE, can_send)[1]["held"]["prompt"] == document


def test_the_file_of_a_box_from_an_ended_session_is_read_through_the_live_one(
    bed: Bed, store_home: Path, can_send: str
) -> None:
    # Quick Look on the chip asks the session the composer follows for the file's bytes.
    image = attach(bed, ENDED, can_send)
    moved_box(bed, can_send, image)
    status, headers, body = bed.client.request(
        f"{SESSIONS}/{OLD}/attachments/{image['id']}",
        headers={"Authorization": f"Bearer {can_send}"},
    )
    assert status == 200 and body == PNG
    assert headers["Content-Type"] == "image/png"
    assert not attachments(store_home, OLD).exists(), "reading the file copies nothing"


def test_a_file_no_session_of_the_project_holds_is_still_refused_and_attaching_clears_it(
    bed: Bed, store_home: Path, can_send: str
) -> None:
    # A file of the same name under another project is not this project's.
    elsewhere = store_home / "composer" / "attachments" / "other" / ENDED
    elsewhere.mkdir(parents=True)
    digest = hashlib.sha256(PNG).hexdigest()
    (elsewhere / f"{digest}.png").write_bytes(PNG)
    document = {"segments": SEGMENTS, "marks": [{"line": 0, "attachments": [digest]}]}

    status, refused = post(bed, SEND_PROMPT, document, can_send)

    assert status == 400 and refused["reason"] == "bad-request", refused
    assert "marks[0].attachments[0]" in refused["error"]
    assert "another session of its project" in refused["error"]
    assert "POST the file" in refused["error"]
    # The remedy the refusal names: attach the file to this session and send its id.
    image = attach(bed, OLD, can_send)
    assert image["id"] == digest
    assert post(bed, SEND_PROMPT, document, can_send)[0] == 200
