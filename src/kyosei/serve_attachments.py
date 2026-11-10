"""``kyo serve``'s attachments: files a composer prompt attaches, kept on this machine (KYO-715).

Under ``/v1/projects/{project}/sessions/{id}/attachments``:

- ``POST`` -- the file itself as the body, with its own ``Content-Type``, saves it and answers
  ``{"id", "path", "media_type", "bytes"}``, ``path`` being absolute;
- ``GET .../attachments/{id}`` -- the file's bytes with their type, so that a draft opened on
  another device can show it.

Both need a device token holding the ``send`` permission, as a draft does
(:mod:`kyosei.serve_drafts`): attaching is part of composing. The machine's ``serve_send``
setting is not consulted, because saving a file does not touch a session.

WHY A PATH. The session runs on this machine, and a client on another one has the file. So
the file is saved here and the agent is given its absolute path, in a line of the prompt
(:func:`kyosei.serve_notes.send_prompt`), and reads it as it reads any other file.

WHAT IS ACCEPTED. :data:`MEDIA_TYPES`: PNG, JPEG, GIF, WebP and PDF, at most
:data:`MAX_ATTACHMENT_BYTES`. The declared type must be one of them, and the bytes must begin
as that type's files begin; a body whose bytes say another type, an empty body and a larger
one are each refused with a ``reason``, and nothing is saved. None of these types is one an
HTML form can send, so taking them on this route, as an exception to the server's rule that a
``POST`` is JSON (:mod:`kyosei.serve`), opens no cross-site path: a page's ``fetch`` with one
of them needs a preflight, which the server does not answer.

WHERE. ``$KYOSEI_HOME/composer/attachments/<project>/<session id>/<sha256>.<extension>``,
private to this account and refused inside a git repository, through
:func:`kyosei.serve_files.write_attachment`. The name is the hex digest of the bytes and the
extension is the checked type's, and nothing the client names becomes part of a path, so the
same file sent twice is one file with one id. Files are kept: nothing here deletes one.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import serve_files, serve_send
from .serve import Refusal
from .serve_sessions import ProjectSessions

#: The largest attachment accepted. A PDF of a few hundred pages or a large screenshot fits.
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class MediaType:
    extension: str
    #: How this type's files begin: each entry is (offset, bytes found there).
    signature: tuple[tuple[int, bytes], ...]
    #: Another opening this type's files may have instead (GIF's two versions).
    alternative: tuple[tuple[int, bytes], ...] = ()

    def matches(self, data: bytes) -> bool:
        return any(
            signature and all(data[at : at + len(magic)] == magic for at, magic in signature)
            for signature in (self.signature, self.alternative)
        )


#: The types accepted, by the name a ``Content-Type`` gives them.
MEDIA_TYPES: dict[str, MediaType] = {
    "image/png": MediaType("png", ((0, b"\x89PNG\r\n\x1a\n"),)),
    "image/jpeg": MediaType("jpg", ((0, b"\xff\xd8\xff"),)),
    "image/gif": MediaType("gif", ((0, b"GIF87a"),), ((0, b"GIF89a"),)),
    "image/webp": MediaType("webp", ((0, b"RIFF"), (8, b"WEBP"))),
    "application/pdf": MediaType("pdf", ((0, b"%PDF-"),)),
}

_ID = re.compile(r"^[0-9a-f]{64}$")

_ACCEPTED = ", ".join(MEDIA_TYPES)


def declared_type(header: str | None) -> str:
    """The media type a ``Content-Type`` header names, if it is accepted; else a 415."""
    named = (header or "").split(";")[0].strip().lower()
    if named not in MEDIA_TYPES:
        raise Refusal(
            415,
            f"an attachment is sent as the file's own bytes with its Content-Type, which is "
            f"one of {_ACCEPTED}; this one was {named or 'not given'!r}. Convert the file to "
            "one of those types, or leave it out",
            reason="attachment-type-not-accepted",
            accepted=list(MEDIA_TYPES),
        )
    return named


def _sniffed(data: bytes) -> str | None:
    return next((name for name, kind in MEDIA_TYPES.items() if kind.matches(data)), None)


def _document(attachment_id: str, path: Path, media_type: str, size: int) -> dict[str, Any]:
    return {"id": attachment_id, "path": str(path), "media_type": media_type, "bytes": size}


def _authorized(
    environ: dict[str, str],
    root_of: Callable[[], str],
    session_id: str,
    authorization: str | None,
) -> None:
    serve_send.require_device(environ, authorization)
    ProjectSessions(root_of(), environ).require(session_id)


def upload(
    environ: dict[str, str],
    project: str,
    root_of: Callable[[], str],
    session_id: str,
    authorization: str | None,
    content_type: str | None,
    content_length: str | None,
    read: Callable[[int], bytes],
) -> tuple[int, dict[str, Any]]:
    """Save one attachment; 201 when it is new, 200 when the same bytes were saved before.

    A body over the limit is refused unread (:func:`too_large`); every other refusal reads
    and discards the body first, so the client receives it.
    """
    media_type = declared_type(content_type)
    if content_length is None or not content_length.strip().isdigit():
        raise Refusal(411, "send the attachment with a Content-Length header")
    size = int(content_length)
    if size > MAX_ATTACHMENT_BYTES:
        raise Refusal(
            413,
            f"the attachment is {size} bytes, and kyo serve takes at most "
            f"{MAX_ATTACHMENT_BYTES} ({MAX_ATTACHMENT_BYTES // (1024 * 1024)} MiB); send a "
            "smaller file, or a smaller copy of this one",
            reason="attachment-too-large",
            limit=MAX_ATTACHMENT_BYTES,
        )
    body = serve_send.Body(lambda: read(size))
    try:
        _authorized(environ, root_of, session_id, authorization)
    except Refusal:
        body.drain()
        raise
    data = body.read()
    if not data:
        raise Refusal(
            400,
            "the body is empty, so there is no file to attach; send the file's bytes",
            reason="attachment-empty",
        )
    kind = MEDIA_TYPES[media_type]
    if not kind.matches(data):
        found = _sniffed(data)
        says = f"the bytes are those of {found}" if found else "the bytes are of no type accepted"
        raise Refusal(
            415,
            f"the body was declared {media_type}, but {says}; send the file with the "
            f"Content-Type its contents have (one of {_ACCEPTED})",
            reason="attachment-content-mismatch",
            declared=media_type,
            found=found,
        )
    attachment_id = hashlib.sha256(data).hexdigest()
    directory = serve_files.attachment_directory(environ, project, session_id, create=True)
    name = f"{attachment_id}.{kind.extension}"
    created = serve_files.write_attachment(directory, name, data)
    document = {"ok": True, **_document(attachment_id, directory / name, media_type, len(data))}
    return (201 if created else 200), document


def too_large(refusal: Refusal) -> bool:
    """Whether a refusal left the body unread, so the connection cannot be reused."""
    return refusal.extra.get("reason") == "attachment-too-large"


def find(
    environ: dict[str, str], project: str, session_id: str, attachment_id: str
) -> dict[str, Any] | None:
    """One saved attachment's id, path, type and size; None when there is none."""
    if not _ID.match(attachment_id):
        return None
    directory = serve_files.attachment_directory(environ, project, session_id, create=False)
    for media_type, kind in MEDIA_TYPES.items():
        path = directory / f"{attachment_id}.{kind.extension}"
        if path.is_file() and not path.is_symlink():
            return _document(attachment_id, path, media_type, path.stat().st_size)
    return None


def _not_found(attachment_id: str, session_id: str) -> Refusal:
    return Refusal(
        404,
        f"no attachment {attachment_id!r} is saved for session {session_id}; POST the file to "
        "this session's attachments route, and use the id it answers",
        reason="attachment-not-found",
        attachment=attachment_id,
    )


def read(
    environ: dict[str, str],
    project: str,
    root_of: Callable[[], str],
    session_id: str,
    authorization: str | None,
    attachment_id: str,
) -> tuple[str, bytes]:
    """One attachment's type and bytes."""
    _authorized(environ, root_of, session_id, authorization)
    found = find(environ, project, session_id, attachment_id)
    if found is None:
        raise _not_found(attachment_id, session_id)
    return found["media_type"], Path(found["path"]).read_bytes()


def resolve(
    environ: dict[str, str], project: str, session_id: str, ids: list[str], where: str
) -> list[dict[str, Any]]:
    """The saved attachments these ids name, in order; an unknown one is a 400 naming it."""
    found: list[dict[str, Any]] = []
    for index, attachment_id in enumerate(ids):
        attachment = find(environ, project, session_id, attachment_id)
        if attachment is None:
            refusal = _not_found(attachment_id, session_id)
            raise serve_send.bad_request(f"{where}[{index}]: {refusal}")
        found.append(attachment)
    return found
