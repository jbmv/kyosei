"""KYO-856 and KYO-853: `kyo attach` given an image references a composer attachment.

Decision KYO-855: a record holds a screenshot by reference to the composer's attachment and
never as bytes. The evidence's stored text is one line,
``attachment <sha256>.<extension> of session <session id>``, which existing evidence carries
with no stored field. `kyo show` resolves the line to the file's path on this machine and
says when the file is gone. Before this, `kyo attach --file` read every file as text and
ended in an uncaught ``UnicodeDecodeError`` on a PNG (KYO-853).
"""

# ruff: noqa: F811 - a fixture borrowed from another test module is named by its parameters

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from kyosei import serve_attachments

from .conftest import export_of, invoke, new_record
from .test_kyo_serve import body_json
from .test_kyo_serve_notes import can_send, token  # noqa: F401 - the fixtures, by name
from .test_kyo_serve_sessions import SESSIONS, Bed, bed, running  # noqa: F401 - the fixtures

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR a screenshot \xff\xfe"
PDF = b"%PDF-1.7\n a document \xff"
SESSION = "11111111-2222-4333-8444-555555555555"


def attachments(store_home: Path, session: str) -> Path:
    return store_home / "composer" / "attachments" / "proj" / session


def files_holding(root: Path, needle: bytes) -> list[str]:
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and needle in path.read_bytes()
    )


def evidence_of(kyo, record_id: str) -> list[dict]:
    shown = kyo("show", record_id, "--json")
    assert shown.code == 0, shown
    return shown.json["evidence_artifacts"]


# ------------------------------------------------- KYO-856.c1: one of the composer's


def test_a_composer_attachment_is_referenced_by_sha256_and_session_and_no_bytes_are_stored(
    kyo, project: Path, store_home: Path
) -> None:
    record_id = new_record(kyo)
    digest = hashlib.sha256(PNG).hexdigest()
    directory = attachments(store_home, SESSION)
    directory.mkdir(parents=True)
    sent = directory / f"{digest}.png"
    sent.write_bytes(PNG)

    result = kyo("attach", record_id, "--file", str(sent), "--json")
    assert result.code == 0, result
    line = f"attachment {digest}.png of session {SESSION}"
    assert result.json["attachment"]["copied"] is False
    assert result.json["attachment"]["session"] == SESSION

    [artifact] = evidence_of(kyo, record_id)
    assert artifact["text"] == line
    assert artifact["sha256"] == hashlib.sha256(line.encode()).hexdigest()
    # The file stays where the composer kept it, and nothing is filed under `records`.
    assert sorted(path.name for path in directory.iterdir()) == [sent.name]
    assert not attachments(store_home, serve_attachments.RECORDS_SESSION).exists()

    # Neither the store (database, ledger) nor the export holds the image's bytes.
    marker = b"IHDR a screenshot"
    assert files_holding(project, marker) == []
    exported = json.loads(export_of(project, record_id).read_text(encoding="utf-8"))
    assert [e["text"] for e in exported["evidence_artifacts"]] == [line]
    assert "attachment" not in exported["evidence_artifacts"][0]


def test_a_file_named_as_an_attachment_whose_bytes_differ_is_kept_anew(
    kyo, store_home: Path
) -> None:
    """A name is trusted only when it is the digest of the bytes under it."""
    record_id = new_record(kyo)
    directory = attachments(store_home, SESSION)
    directory.mkdir(parents=True)
    wrong = directory / f"{'0' * 64}.png"
    wrong.write_bytes(PNG)

    result = kyo("attach", record_id, "--file", str(wrong), "--json")
    assert result.code == 0, result
    digest = hashlib.sha256(PNG).hexdigest()
    [artifact] = evidence_of(kyo, record_id)
    assert artifact["text"] == f"attachment {digest}.png of session records"


# ------------------------------------------------- KYO-856.c2: from elsewhere on disk


def test_an_image_from_elsewhere_is_kept_under_its_sha256_and_then_referenced(
    kyo, project: Path, store_home: Path, tmp_path: Path
) -> None:
    record_id = new_record(kyo)
    shot = tmp_path / "Screenshot 2026-11-11.png"
    shot.write_bytes(PNG)
    digest = hashlib.sha256(PNG).hexdigest()

    result = kyo("attach", record_id, "--file", str(shot), "--json")
    assert result.code == 0, result
    kept = attachments(store_home, "records") / f"{digest}.png"
    assert kept.read_bytes() == PNG
    assert result.json["attachment"]["copied"] is True
    assert os.path.realpath(result.json["attachment"]["path"]) == os.path.realpath(kept)

    [artifact] = evidence_of(kyo, record_id)
    assert artifact["text"] == f"attachment {digest}.png of session records"
    assert files_holding(project, b"IHDR a screenshot") == []

    # The same file again is the same kept file, referenced by a second piece of evidence.
    again = kyo("attach", record_id, "--file", str(shot), "--json")
    assert again.code == 0 and again.json["attachment"]["copied"] is False
    assert sorted(path.name for path in kept.parent.iterdir()) == [kept.name]
    assert [e["text"] for e in evidence_of(kyo, record_id)] == [artifact["text"]] * 2


def test_a_pdf_is_referenced_as_an_image_is(kyo, store_home: Path, tmp_path: Path) -> None:
    record_id = new_record(kyo)
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(PDF)
    result = kyo("attach", record_id, "--file", str(paper), "--json")
    assert result.code == 0, result
    digest = hashlib.sha256(PDF).hexdigest()
    assert (attachments(store_home, "records") / f"{digest}.pdf").read_bytes() == PDF
    assert result.json["attachment"]["media_type"] == "application/pdf"


def test_a_mistyped_record_id_keeps_no_file(kyo, store_home: Path, tmp_path: Path) -> None:
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG)
    result = kyo("attach", "TST-999", "--file", str(shot))
    assert result.code != 0 and "TST-999" in result.err
    assert not (store_home / "composer").exists()


# ------------------------------------------------- KYO-856.c3: kyo show


def test_show_prints_the_files_path_and_says_when_it_is_gone(
    kyo, store_home: Path, tmp_path: Path
) -> None:
    record_id = new_record(kyo)
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG)
    attached = kyo("attach", record_id, "--file", str(shot), "--json")
    assert attached.code == 0, attached
    evidence_id = attached.json["evidence_id"]
    kept = Path(attached.json["attachment"]["path"])
    assert kept.is_file() and str(kept) in kyo("attach", record_id, "--file", str(shot)).out

    for shown in (kyo("show", record_id), kyo("show", evidence_id)):
        assert shown.code == 0, shown
        assert f"attachment: {kept}" in shown.out
        assert "no longer there" not in shown.out
    described = kyo("show", evidence_id, "--json").json["attachment"]
    assert described["path"] == str(kept) and described["present"] is True

    kept.unlink()
    for shown in (kyo("show", record_id), kyo("show", evidence_id)):
        assert shown.code == 0, shown
        assert f"attachment: {kept} (the file is no longer there" in shown.out
    assert evidence_of(kyo, record_id)[0]["attachment"]["present"] is False


def test_text_evidence_carries_no_attachment(kyo) -> None:
    record_id = new_record(kyo)
    assert kyo("attach", record_id, "--file", "-", stdin="attachment of a kind\n").code == 0
    [artifact] = evidence_of(kyo, record_id)
    assert "attachment" not in artifact
    assert "attachment:" not in kyo("show", record_id).out


# ------------------------------------------------- KYO-853: a file that is not text


@pytest.mark.parametrize(
    "data",
    [b"\x89PN not a picture after all", b"\xff\xfe\x00binary", b"PK\x03\x04\x14\x00\xb5\xe9"],
)
def test_bytes_that_are_neither_text_nor_an_accepted_file_are_refused_in_a_sentence(
    kyo, store_home: Path, tmp_path: Path, data: bytes
) -> None:
    record_id = new_record(kyo)
    blob = tmp_path / "capture.bin"
    blob.write_bytes(data)
    result = kyo("attach", record_id, "--file", str(blob))
    assert result.code != 0
    assert "UnicodeDecodeError" not in result.err and "Traceback" not in result.err
    assert "neither text in UTF-8 nor a file of a type the attachment store keeps" in result.err
    assert "PNG, JPEG, GIF, WebP or PDF" in result.err
    assert evidence_of(kyo, record_id) == [] and not (store_home / "composer").exists()

    # The remedy the sentence names: the converted file is attached.
    blob.write_bytes(PNG)
    assert kyo("attach", record_id, "--file", str(blob)).code == 0
    assert len(evidence_of(kyo, record_id)) == 1


def test_a_file_over_the_limit_is_refused_and_a_smaller_copy_is_taken(
    kyo, store_home: Path, tmp_path: Path, monkeypatch
) -> None:
    record_id = new_record(kyo)
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG + b"\x00" * 64)
    monkeypatch.setattr(serve_attachments, "MAX_ATTACHMENT_BYTES", len(PNG))
    result = kyo("attach", record_id, "--file", str(shot))
    assert result.code != 0 and "attach a smaller copy of it" in result.err
    assert not (store_home / "composer").exists()
    shot.write_bytes(PNG)
    assert kyo("attach", record_id, "--file", str(shot)).code == 0


def test_text_is_still_stored_verbatim(kyo, tmp_path: Path) -> None:
    record_id = new_record(kyo)
    capture = tmp_path / "capture.txt"
    capture.write_text("a capture — with non-ASCII text\n", encoding="utf-8")
    assert kyo("attach", record_id, "--file", str(capture)).code == 0
    assert [e["text"] for e in evidence_of(kyo, record_id)] == ["a capture — with non-ASCII text"]


# ------------------------------------------------- the route the Mac app fetches by


def test_a_file_kept_for_a_record_is_read_through_the_attachment_route(
    bed: Bed, store_home: Path, tmp_path: Path, token: str
) -> None:
    """`records` names no session, so the route asks for the device token and no session."""
    record = invoke(["--root", str(bed.project), "new", "bug", "--title", "A defect", "--json"])
    assert record.code == 0, record
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG)
    record_id = record.json["record"]["id"]
    attached = invoke(
        ["--root", str(bed.project), "attach", record_id, "--file", str(shot), "--json"]
    )
    assert attached.code == 0, attached
    reference = attached.json["attachment"]
    assert reference["session"] == "records"

    route = f"{SESSIONS}/records/attachments"
    status, headers, body = bed.client.request(
        f"{route}/{reference['id']}", headers={"Authorization": f"Bearer {token}"}
    )
    assert status == 200 and body == PNG and headers["Content-Type"] == "image/png"

    # Without a token it is refused, as any attachment is.
    status, _, _ = bed.client.request(f"{route}/{reference['id']}")
    assert status == 401
    # The exemption is the read's alone: nothing is uploaded to `records` over the route.
    status, _, body = bed.client.request(
        route,
        method="POST",
        headers={"Content-Type": "image/png", "Authorization": f"Bearer {token}"},
        body=PNG + b"another",
    )
    assert status == 404, body_json(body)
    kept = attachments(store_home, "records")
    assert sorted(path.name for path in kept.iterdir()) == [reference["name"]]
