"""``kyo doctor``'s export diagnostics say which side is which, correctly.

Reported by a consumer project immediately after the v6 migration: doctor's
``export_roundtrip`` message read ``on disk "schema_version": 6, derived
"schema_version": 5`` when the file on disk said 5 and the database was 6. The
substance was right and the direction was backwards — the worst shape a
diagnostic can take, because it is confidently wrong rather than absent, and the
reporter only caught it by opening the file to see which half to believe.

The cause was two interchangeable string arguments whose meaning came entirely
from their position: ``check_exports`` passed them one way, ``check_export_roundtrip``
the other. ``_first_difference`` is keyword-only now, so no call site can repeat
it — but these tests assert the *output*, not the signature, because a future
refactor could reintroduce the inversion without touching the keywords.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .conftest import export_of, new_record


def _doctor_line(kyo, check: str) -> str:
    result = kyo("doctor")
    assert result.code != 0, "expected doctor to fail"
    lines = [line for line in result.err.splitlines() if f"[{check}]" in line]
    assert lines, f"no [{check}] failure in:\n{result.err}"
    return lines[0]


def _labels(line: str) -> tuple[str, str]:
    match = re.search(r"on disk '(.*?)', derived '(.*?)'", line)
    assert match, f"message does not carry both labels: {line!r}"
    return match.group(1), match.group(2)


def test_export_roundtrip_labels_are_not_inverted(kyo, project: Path) -> None:
    """The reported case: an export document that does not round-trip does not get
    its "on disk" and "derived" halves swapped.

    Before KYO-216 the vehicle here was a `schema_version` behind the build's own
    — a rebuild always re-exports at the current version, so that alone was enough
    to break the round-trip. KYO-216 folded exactly that shape into its own
    collapsed finding (one line naming the count and the remedy, carrying neither
    label at all — see ``tests/test_doctor.py``'s KYO-216.c3 tests), so this now
    uses a document missing a field a fresh rebuild fills back in: present (and
    empty) once rebuilt, absent on the file as hand-edited. Still a real,
    uncollapsed round-trip mismatch, and still the same direction to get right.
    """
    record = new_record(kyo, kind="bug", title="Mislabelled")
    path = export_of(project, record)
    document = json.loads(path.read_text(encoding="utf-8"))
    del document["touches"]
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    on_disk, derived = _labels(_doctor_line(kyo, "export_roundtrip"))
    assert '"touches"' not in on_disk, f"'on disk' should not show the filled-in field: {on_disk!r}"
    assert '"touches"' in derived, f"'derived' does not show what rebuild fills in: {derived!r}"


def test_exports_labels_are_not_inverted(kyo, project: Path) -> None:
    """The check that was already right, pinned so the fix to its neighbour cannot
    break it on the way past."""
    record = new_record(kyo, kind="bug", title="Original title")
    path = export_of(project, record)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["title"] = "Hand-edited title"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    on_disk, derived = _labels(_doctor_line(kyo, "exports"))
    assert "Hand-edited title" in on_disk
    assert "Original title" in derived


def test_both_checks_agree_about_which_side_is_the_file(kyo, project: Path) -> None:
    """The actual defect was the two checks disagreeing. One hand-edit trips both,
    so they can be compared directly."""
    record = new_record(kyo, kind="bug", title="Original title")
    path = export_of(project, record)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["title"] = "Hand-edited title"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = kyo("doctor")
    assert result.code != 0
    for check in ("exports", "export_roundtrip"):
        lines = [line for line in result.err.splitlines() if f"[{check}]" in line]
        if not lines:
            continue
        on_disk, _derived = _labels(lines[0])
        assert "Hand-edited title" in on_disk, f"[{check}] mislabels the file: {on_disk!r}"
