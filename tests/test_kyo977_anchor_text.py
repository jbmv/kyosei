"""KYO-977 (decision KYO-976): an anchor to lines of a served file stores the text of its
lines and of two lines either side, as the person saw them when the anchor was placed.

The digest of the lines alone said only whether the lines were the same, and was computed
when the prompt was sent, so an edit between placing an anchor and sending it was invisible,
a blank line matched any other blank line, and an anchor whose text moved went stale. These
tests run a real ``kyo serve`` against a bed project, as tests/test_kyo_serve_notes.py does;
the page's half is apps/apple/web/test/fileanchors.test.ts.
"""

# ruff: noqa: F811 - a fixture borrowed from another test module is named by its parameters

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from kyosei import ledgers, serve_notes
from kyosei.schema import (
    ANCHOR_TEXT_SCHEMA_VERSION,
    PERSON_NOTE_ANCHOR_FIELD_DEFAULTS,
    SCHEMA_VERSION,
)

from .test_kyo_serve_held import wait_until
from .test_kyo_serve_notes import (  # noqa: F401 - the fixtures, by name
    PLAN,
    SEND_PROMPT,
    SOURCE,
    can_send,
    chars_of,
    code,
    filed_by,
    head_of,
    kyo_of,
    marks_of,
    notes_path,
    on_lines,
    one_mark,
    sent_with,
    served,
    token,
    with_files,
    working_screen,
)
from .test_kyo_serve_send import IDLE, post
from .test_kyo_serve_sessions import Bed, bed, running  # noqa: F401 - the fixtures, by name


def seen_of(text: str, first: int, last: int) -> dict:
    """What the page sends as ``seen`` for lines ``first`` to ``last`` of ``text``."""
    lines = serve_notes.file_lines(text)
    context = serve_notes.CONTEXT_LINES
    return {
        "before": lines[max(0, first - 1 - context) : first - 1],
        "lines": lines[first - 1 : last],
        "after": lines[last : last + context],
    }


def only_place(bed: Bed, token: str) -> dict:
    (mark,) = marks_of(bed, token)
    (place,) = mark["anchors"]
    return place


def write(code: Path, path: str, text: str) -> None:
    (code / path).write_text(text, encoding="utf-8")


# ----------------------------------------------------------------- c1: the stored text


def test_c1_a_file_anchor_stores_its_lines_and_two_lines_either_side(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    assert serve_notes.CONTEXT_LINES == 2
    note = sent_with(
        bed,
        notes_path,
        with_files,
        served("src/tidy.py", 4, 5),
        # At the file's start and end the context is what there is, and no more.
        served("src/tidy.py", 1, 1),
        served("src/tidy.py", 7, 7, seen=seen_of(SOURCE, 7, 7)),
    )
    middle, start, end = note["anchors"]
    assert middle["lines_text"] == "def reformat(text):\n    return text.strip()\n"
    assert middle["before_text"] == "\n\n" and middle["after_text"] == "\nLIMIT = 3\n"
    assert (start["before_text"], start["lines_text"]) == ("", "import os\n")
    assert start["after_text"] == "\n\n"
    assert (end["before_text"], end["lines_text"], end["after_text"]) == (
        "    return text.strip()\n\n",
        "LIMIT = 3\n",
        "",
    )
    # Each says what the person saw, so kyo checked it against the file: computed.
    assert {anchor["digest_check"] for anchor in note["anchors"]} == {"computed"}
    # The ledger line is the note as it was filed, text and all.
    ledger = ledgers.person_notes_path_in(bed.project / "kyosei" / "export")
    line = next(
        json.loads(raw)
        for raw in ledger.read_text(encoding="utf-8").splitlines()
        if json.loads(raw)["id"] == note["id"]
    )
    assert line["anchors"] == note["anchors"]
    assert line["schema_version"] == SCHEMA_VERSION == ANCHOR_TEXT_SCHEMA_VERSION


def test_c1_the_stored_text_survives_a_rebuild_from_the_export(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    note = sent_with(bed, notes_path, with_files, served("src/tidy.py", 4, 5))
    kyo = kyo_of(bed)
    (bed.project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    shown = kyo("notes", "show", note["id"], "--json").json["note"]
    assert shown["anchors"] == note["anchors"]
    assert shown["anchors"][0]["lines_text"] == "def reformat(text):\n    return text.strip()\n"


# ------------------------------------------------ c2: an edit between placing and sending


def test_c2_an_edit_between_placing_and_sending_files_the_anchor_stale_from_the_start(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    placed = served("src/tidy.py", 4, 5, seen=seen_of(SOURCE, 4, 5))
    # The person anchored lines 4-5 as the page showed them, and the file changed after.
    write(code, "src/tidy.py", SOURCE.replace("strip", "rstrip"))
    status, sent = post(bed, SEND_PROMPT, one_mark(placed), with_files)
    assert status == 200, sent
    # Nothing is refused, and the agent is not quoted words the person never saw.
    assert on_lines(sent) == ["on file src/tidy.py lines 4-5 (words unavailable)"]
    bed.show(IDLE)
    wait_until(lambda: len(filed_by(bed, notes_path, with_files, sent["held"]["id"])) == 1)
    (note,) = filed_by(bed, notes_path, with_files, sent["held"]["id"])
    (stored,) = note["anchors"]
    # Filed with what the person saw, on their word, and stale from the start.
    assert stored["lines_text"] == "def reformat(text):\n    return text.strip()\n"
    assert stored["digest_check"] == "given"
    assert (stored["line_start"], stored["line_end"]) == (4, 5)
    place = only_place(bed, with_files)
    assert place["stale"] is True and place["stale_reason"] == "changed"
    # The file put back as the person saw it, the anchor is fresh.
    write(code, "src/tidy.py", SOURCE)
    assert "stale" not in only_place(bed, with_files)


def test_c2_without_an_edit_the_seen_text_is_the_files_and_the_anchor_is_fresh(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    placed = served("docs/plan.md", 5, 5, seen=seen_of(PLAN, 5, 5))
    status, sent = post(bed, SEND_PROMPT, one_mark(placed), with_files)
    assert status == 200, sent
    assert on_lines(sent) == [
        'on file docs/plan.md line 5 "The second chunk waits on the..." (list item)'
    ]


def test_c2_a_file_deleted_between_placing_and_sending_is_filed_as_absent(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    placed = served("src/tidy.py", 4, 5, seen=seen_of(SOURCE, 4, 5))
    (code / "src" / "tidy.py").unlink()
    status, sent = post(bed, SEND_PROMPT, one_mark(placed), with_files)
    assert status == 200, sent
    assert on_lines(sent) == ["on file src/tidy.py lines 4-5 (words unavailable)"]
    bed.show(IDLE)
    wait_until(lambda: len(filed_by(bed, notes_path, with_files, sent["held"]["id"])) == 1)
    place = only_place(bed, with_files)
    assert place["stale"] is True and place["stale_reason"] == "absent"
    # Without what the person saw there is nothing to file, as before.
    status, refused = post(bed, SEND_PROMPT, one_mark(served("src/tidy.py", 4, 5)), with_files)
    assert status == 404 and "src/tidy.py" in refused["error"]


def test_c2_text_moved_between_placing_and_sending_is_filed_where_it_is(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    selected = chars_of(SOURCE, "return")
    placed = served("src/tidy.py", 5, 5, chars=selected, seen=seen_of(SOURCE, 5, 5))
    write(code, "src/tidy.py", "import sys\n" + SOURCE)
    status, sent = post(bed, SEND_PROMPT, one_mark(placed), with_files)
    assert status == 200, sent
    # The line names the lines the text is at, and quotes them whole: the selection's
    # offsets were counted in the file as it was.
    assert on_lines(sent) == ['on file src/tidy.py line 6 "return text.strip()"']
    bed.show(IDLE)
    wait_until(lambda: len(filed_by(bed, notes_path, with_files, sent["held"]["id"])) == 1)
    (note,) = filed_by(bed, notes_path, with_files, sent["held"]["id"])
    (stored,) = note["anchors"]
    assert (stored["line_start"], stored["line_end"]) == (6, 6)
    assert "char_start" not in stored and stored["digest_check"] == "computed"
    assert "stale" not in only_place(bed, with_files)


def test_c2_seen_text_of_the_wrong_shape_is_refused_and_nothing_is_held(
    bed: Bed, with_files: str, code: Path
) -> None:
    good = seen_of(SOURCE, 4, 5)
    for seen in (
        "def reformat(text):",
        {"lines": good["lines"]},
        {**good, "lines": ["one line"]},
        {**good, "before": []},
        {**good, "after": ["", "LIMIT = 3", "more"]},
        {**good, "lines": ["def reformat(text):\n", "x"]},
    ):
        status, refused = post(
            bed, SEND_PROMPT, one_mark(served("src/tidy.py", 4, 5, seen=seen)), with_files
        )
        assert status == 400 and "'seen'" in refused["error"], (seen, refused)
    status, _ = post(bed, SEND_PROMPT, one_mark(served("src/tidy.py", 4, 5, seen=good)), with_files)
    assert status == 200


# ------------------------------------------------- c3: a blank or repeated line's context


def test_c3_a_blank_line_anchor_goes_stale_when_its_context_no_longer_surrounds_it(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    # Line 2 of the plan is the blank line under its heading.
    note = sent_with(bed, notes_path, with_files, served("docs/plan.md", 2, 2))
    (stored,) = note["anchors"]
    assert stored["lines_text"] == "\n" and stored["before_text"] == "# The plan\n"
    assert "stale" not in only_place(bed, with_files)
    # An edit further down leaves its context, so it stays.
    write(code, "docs/plan.md", PLAN + "- A fourth chunk.\n")
    assert "stale" not in only_place(bed, with_files)
    # The heading above it changed: line 2 is still blank, and the digest of a blank line
    # still matches, but this is not the blank line that was anchored.
    write(code, "docs/plan.md", PLAN.replace("# The plan", "# Another plan"))
    assert cmd_digest(serve_notes.file_lines(PLAN.replace("# The plan", "# Another plan")), 2) == (
        stored["digest"]
    )
    place = only_place(bed, with_files)
    assert place["stale"] is True and place["stale_reason"] == "changed"
    # A file with a blank line 2 and nothing else in common is no match either.
    write(code, "docs/plan.md", "x\n\ny\n")
    assert only_place(bed, with_files)["stale_reason"] == "changed"


def cmd_digest(lines: list[str], line: int) -> str:
    from kyosei import cmd_notes

    return cmd_notes.lines_digest(lines[line - 1 : line])


def test_c3_a_repeated_line_is_told_from_its_twin_by_its_context(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    twice = "a = 1\n\nreturn\n\nb = 2\n\nreturn\n\nc = 3\n"
    write(code, "src/twice.py", twice)
    sent_with(bed, notes_path, with_files, served("src/twice.py", 7, 7))
    assert "stale" not in only_place(bed, with_files)
    # The first `return` is untouched, but the second's context changed: stale, not moved.
    write(code, "src/twice.py", twice.replace("b = 2", "b = 20"))
    assert only_place(bed, with_files)["stale_reason"] == "changed"


# ------------------------------------------------------------ c4: a move, and a doubt


def test_c4_text_found_intact_at_other_lines_is_fresh_there_and_said_to_have_moved(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    selected = chars_of(SOURCE, "reformat")
    sent_with(bed, notes_path, with_files, served("src/tidy.py", 4, 5, chars=selected))
    fresh = only_place(bed, with_files)
    assert fresh["lines"] == [4, 5] and "moved_from" not in fresh
    write(code, "src/tidy.py", "import sys\nimport re\n" + SOURCE)
    moved = only_place(bed, with_files)
    assert moved == {
        "kind": "file",
        "root": "code",
        "path": "src/tidy.py",
        "commit": head_of(code),
        "lines": [6, 7],
        "moved_from": [4, 5],
    }
    # Its text with an edit inside its context is not the same place, and no other is.
    write(code, "src/tidy.py", "import sys\nimport re\n" + SOURCE.replace("LIMIT", "CAP"))
    assert only_place(bed, with_files)["stale_reason"] == "changed"


def test_c4_text_found_intact_at_several_other_places_is_ambiguous_and_stale(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    sent_with(bed, notes_path, with_files, served("src/tidy.py", 4, 5))
    block = "\n\ndef reformat(text):\n    return text.strip()\n\nLIMIT = 3\n"
    # Its own lines still hold it, with a twin elsewhere: its own place wins.
    write(code, "src/tidy.py", SOURCE + block)
    assert "stale" not in only_place(bed, with_files)
    # Gone from its own lines and found twice elsewhere: which one was meant is unknown.
    write(code, "src/tidy.py", "import os\n# moved\n" + block + block)
    place = only_place(bed, with_files)
    assert place["stale"] is True and place["stale_reason"] == "ambiguous"
    assert place["found"] == [[5, 6], [11, 12]] and place["lines"] == [4, 5]
    # One of the two changed, and the other is where it moved.
    write(code, "src/tidy.py", "import os\n# moved\n" + block + block.replace("strip", "lower"))
    moved = only_place(bed, with_files)
    assert moved["lines"] == [5, 6] and moved["moved_from"] == [4, 5] and "stale" not in moved


def test_c4_locate_states_its_rule_on_lines_alone() -> None:
    """The rule, without a server: own lines first, then exactly one other place."""
    lines = serve_notes.file_lines(SOURCE)
    anchor = serve_notes.snapshot(lines, 4, 5) | {"line_start": 4}
    assert serve_notes.locate(anchor, lines) == serve_notes.Located("fresh", (4, 5))
    shifted = ["# new"] + lines
    assert serve_notes.locate(anchor, shifted) == serve_notes.Located("moved", (5, 6))
    # The file's start is part of the context of an anchor within two lines of it.
    first = serve_notes.snapshot(lines, 1, 1) | {"line_start": 1}
    assert serve_notes.locate(first, shifted).state == "changed"


# --------------------------------------------------- c5: a file gone, and a file changed


def test_c5_the_marks_route_tells_a_file_gone_from_a_file_changed(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    sent_with(bed, notes_path, with_files, served("src/tidy.py", 4, 5))
    write(code, "src/tidy.py", SOURCE.replace("strip", "rstrip"))
    changed = only_place(bed, with_files)
    assert changed["stale"] is True and changed["stale_reason"] == "changed"
    (code / "src" / "tidy.py").unlink()
    gone = only_place(bed, with_files)
    assert gone["stale"] is True and gone["stale_reason"] == "absent"


# ------------------------------------------------------ c7: an anchor filed before this


def as_filed_before_this(bed: Bed) -> None:
    """The anchors as a store rebuilt from a schema 36 export holds them: no text."""
    connection = sqlite3.connect(bed.project / "kyosei" / "kyosei.db", isolation_level=None)
    connection.execute(
        "UPDATE person_note_anchors SET lines_text = NULL, before_text = NULL, after_text = NULL"
    )
    connection.close()


def test_c7_an_anchor_holding_no_text_is_compared_by_its_digest_at_its_lines_as_before(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    sent_with(bed, notes_path, with_files, served("src/tidy.py", 4, 5))
    as_filed_before_this(bed)
    assert only_place(bed, with_files) == {
        "kind": "file",
        "root": "code",
        "path": "src/tidy.py",
        "commit": head_of(code),
        "lines": [4, 5],
    }
    # A change to a context line does not touch it, since it holds no context.
    write(code, "src/tidy.py", SOURCE.replace("LIMIT", "CAP"))
    assert "stale" not in only_place(bed, with_files)
    # Its words moved down a line: it is stale and is not followed, as before.
    write(code, "src/tidy.py", "import sys\n" + SOURCE)
    place = only_place(bed, with_files)
    assert place["stale"] is True and place["lines"] == [4, 5] and "moved_from" not in place
    (code / "src" / "tidy.py").unlink()
    assert only_place(bed, with_files)["stale_reason"] == "absent"


def test_c7_a_schema_36_note_line_rebuilds_with_no_text_on_its_anchors(
    bed: Bed, notes_path: str, with_files: str, code: Path
) -> None:
    assert PERSON_NOTE_ANCHOR_FIELD_DEFAULTS[ANCHOR_TEXT_SCHEMA_VERSION] == {
        "lines_text": None,
        "before_text": None,
        "after_text": None,
    }
    note = sent_with(bed, notes_path, with_files, served("src/tidy.py", 4, 5))
    ledger = ledgers.person_notes_path_in(bed.project / "kyosei" / "export")
    rows = []
    for raw in ledger.read_text(encoding="utf-8").splitlines():
        row = json.loads(raw)
        for anchor in row["anchors"]:
            for key in ("lines_text", "before_text", "after_text"):
                anchor.pop(key, None)  # schema 36 had no such keys
        row["schema_version"] = 36
        rows.append(json.dumps(row, sort_keys=True) + "\n")
    ledger.write_text("".join(rows), encoding="utf-8")
    (bed.project / "kyosei" / "kyosei.db").unlink()
    kyo = kyo_of(bed)
    assert kyo("rebuild").code == 0
    (anchor,) = kyo("notes", "show", note["id"], "--json").json["note"]["anchors"]
    assert "lines_text" not in anchor and anchor["digest"] == note["anchors"][0]["digest"]
    assert "stale" not in only_place(bed, with_files)
