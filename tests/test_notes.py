"""Notes are append-only with supersede markers (law 4).

The maintainer must be able to read the *current* understanding without losing the
contested history — so a correction appends and marks, it never overwrites.

KYO-94 adds the shift log's half of that guarantee, below. A record note has always
been readable by `kyo show <record>`; a *shift* note had no read path at all. It was
surfaced by `kyo brief` in exactly one brief — the brief stamps the maximum shift-note
seq as the new watermark — and after that no command shipped could reach it. `kyo show`
refused the id, `kyo html` rendered none, and `kyo redact` accepted the id in order to
DESTROY the note. Found while answering "is everything good to go for a fresh session?",
which is the question the shift log exists to answer yes to: the handoff note written
for the next session was consumed thirty seconds later by a `kyo brief` run checking
whether the handoff would work.

Two mechanisms answer it, and they are separate on purpose. `kyo show PREFIX-SN` is the
durable read path for a note whose id you have. The brief's handoff block is what
arrives when you have no id to ask for — the last shift note authored before your
session began, in full, however many briefs have run since.
"""

from __future__ import annotations

import json

from .conftest import new_record


def test_notes_append_and_keep_their_order(kyo, write_file) -> None:
    record = new_record(kyo)
    kyo("edit", record, "--note-file", write_file("a.md", "first thought"))
    kyo("edit", record, "--note-file", write_file("b.md", "second thought"))
    notes = kyo("show", record, "--json").json["notes"]
    assert [n["id"] for n in notes] == [f"{record}.n1", f"{record}.n2"]
    assert [n["text"] for n in notes] == ["first thought", "second thought"]


def test_supersede_marks_the_old_note_and_keeps_it(kyo, write_file) -> None:
    record = new_record(kyo)
    kyo("edit", record, "--note-file", write_file("a.md", "it is the cache warmer"))
    result = kyo(
        "edit", record,
        "--supersede-note", f"{record}.n1",
        "--note-file", write_file("b.md", "correction: it is the serialiser"),
    )
    assert result.code == 0
    notes = kyo("show", record, "--json").json["notes"]
    assert len(notes) == 2
    assert notes[0]["superseded_by"] == f"{record}.n2"
    assert notes[0]["superseded_at"] is not None
    assert notes[0]["text"] == "it is the cache warmer"
    assert notes[1]["superseded_by"] is None


def test_a_note_cannot_be_superseded_twice(kyo, write_file) -> None:
    record = new_record(kyo)
    kyo("edit", record, "--note-file", write_file("a.md", "first"))
    kyo("edit", record, "--supersede-note", f"{record}.n1", "--note-file", write_file("b.md", "b"))
    result = kyo(
        "edit", record, "--supersede-note", f"{record}.n1", "--note-file", write_file("c.md", "c")
    )
    assert result.code == 1
    assert "already superseded" in result.err


def test_supersede_requires_the_replacement(kyo) -> None:
    record = new_record(kyo)
    result = kyo("edit", record, "--supersede-note", f"{record}.n1")
    assert result.code == 64
    assert "requires --note-file" in result.err


def test_superseding_an_unknown_note_fails_loudly(kyo, write_file) -> None:
    record = new_record(kyo)
    result = kyo(
        "edit", record, "--supersede-note", f"{record}.n9", "--note-file", write_file("a.md", "x")
    )
    assert result.code == 1
    assert "no note" in result.err


def test_a_note_cannot_supersede_across_records(kyo, write_file) -> None:
    first = new_record(kyo, title="one")
    second = new_record(kyo, title="two")
    kyo("edit", first, "--note-file", write_file("a.md", "first"))
    result = kyo(
        "edit", second, "--supersede-note", f"{first}.n1", "--note-file", write_file("b.md", "b")
    )
    assert result.code == 1
    assert first in result.err


def test_an_empty_note_is_refused(kyo, write_file) -> None:
    record = new_record(kyo)
    result = kyo("edit", record, "--note-file", write_file("empty.md", "\n\n"))
    assert result.code == 64
    assert "must not be empty" in result.err


# ------------------------------------------------------- KYO-94.c1/c2/c3: the read path


def _write_shift_note(kyo, write_file, name: str, text: str) -> str:
    result = kyo("note", "--shift", "--file", write_file(name, text), "--json")
    assert result.code == 0, result
    return result.json["shift_note_id"]


def test_a_shift_note_is_readable_by_id_after_two_briefs_have_run(kyo, write_file) -> None:
    """KYO-94.c3, and the whole defect in one test.

    A brief surfaces the note and stamps the watermark past it; a second brief shows an
    empty window. The note's text must still come back by id — the surfacing window and
    the read path are different questions, and only the first one has a watermark."""
    note_id = _write_shift_note(kyo, write_file, "s.md", "the deploy key rotates on Friday")

    first = kyo("brief")
    assert note_id in first.out
    second = kyo("brief")
    assert "shift log since last brief (0)" in second.out

    shown = kyo("show", note_id)
    assert shown.code == 0, shown
    assert "the deploy key rotates on Friday" in shown.out
    assert kyo("show", note_id, "--json").json["text"] == "the deploy key rotates on Friday"


def test_a_shift_note_read_by_id_says_it_is_append_only_and_not_a_record(
    kyo, write_file
) -> None:
    """A shift note is not a record and `kyo show` must not make it look like one: no
    criteria, no gates, no amendment path. The separate PREFIX-SN id space is the
    mechanism that keeps the log unamendable, so the rendering says so out loud."""
    note_id = _write_shift_note(kyo, write_file, "s.md", "a judgement call, recorded")
    shown = kyo("show", note_id)
    assert "[shift note]" in shown.out
    assert "append-only" in shown.out
    assert "criteria (" not in shown.out
    assert kyo("show", note_id, "--json").json["object"] == "shift-note"


def test_a_multi_line_shift_note_comes_back_whole(kyo, write_file) -> None:
    note_id = _write_shift_note(kyo, write_file, "s.md", "line one\nline two\nline three")
    shown = kyo("show", note_id)
    for line in ("line one", "line two", "line three"):
        assert line in shown.out


def test_an_unknown_shift_note_id_is_refused_as_a_shift_note_not_as_a_record(kyo) -> None:
    """Ids are typed (law 6). `kyo show TST-S9` on a store with no shift notes must say
    *no shift note* — "no record 'TST-S9'" is what taught a reader this id space did not
    exist, and it was the sentence the defect was found through."""
    result = kyo("show", "TST-S9")
    assert result.code == 1
    assert "no shift note" in result.err
    assert "no record" not in result.err


def test_a_redacted_shift_note_reads_back_as_the_redaction_never_as_its_text(
    kyo, write_file
) -> None:
    """`kyo redact` could always find a shift note by id in order to remove its text.
    The read path added beside it must not undo that: it prints the redaction — when, and
    why — and no prose."""
    note_id = _write_shift_note(kyo, write_file, "s.md", "customer 4417 hit the bug")
    assert kyo("redact", note_id, "--why", "a customer id slipped in").code == 0

    shown = kyo("show", note_id)
    assert shown.code == 0, shown
    assert "4417" not in shown.out
    assert "a customer id slipped in" in shown.out
    assert "kyo redact" in shown.out


# ------------------------------------------------------------- KYO-94.c4: the handoff


def _handoff(kyo, *args: str) -> dict:
    return kyo("brief", "--json", *args).json["handoff"]


def test_the_last_note_before_a_session_survives_every_brief_that_session_runs(
    kyo, write_file
) -> None:
    """KYO-94.c4, and the live failure it was filed from.

    A handoff note is written at the end of one session and consumed by whichever brief
    happens to run next — in the reported case, one run thirty seconds later to check
    that the handoff would work. Anchored on the session instead of on the previous
    brief, the answer does not move however many briefs run inside the session."""
    _write_shift_note(kyo, write_file, "s.md", "v2 starts at the plan record, not the board")
    assert kyo("brief").code == 0  # the outgoing session consumes it, as it always did

    opening = kyo("brief", "--session-start")
    assert "v2 starts at the plan record, not the board" in opening.out

    for _ in range(3):
        later = kyo("brief")
        assert "v2 starts at the plan record, not the board" in later.out
    assert _handoff(kyo)["note"]["text"] == "v2 starts at the plan record, not the board"


def test_the_handoff_is_printed_in_full_under_compact(kyo, write_file) -> None:
    """`--compact` is what the packaged SessionStart hook runs, and it drops the body of
    every note in the shift-log window. A handoff abbreviated to its id is not a handoff,
    so this block is the one exception."""
    _write_shift_note(kyo, write_file, "s.md", "the migration is half-applied on staging")
    assert kyo("brief").code == 0
    out = kyo("brief", "--compact", "--session-start").out
    assert "the migration is half-applied on staging" in out


def test_a_note_written_during_a_session_does_not_displace_the_handoff(
    kyo, write_file
) -> None:
    """"Before this session began" is a boundary, not "the note before the latest one":
    a controller who writes its own notes must still be able to re-read what it was
    handed, which is exactly the state a context compaction leaves it in."""
    _write_shift_note(kyo, write_file, "old.md", "from the shift before")
    assert kyo("brief", "--session-start").code == 0
    _write_shift_note(kyo, write_file, "new.md", "something I noticed just now")

    handoff = _handoff(kyo)
    assert handoff["note"]["text"] == "from the shift before"
    assert handoff["basis"] == "session-start"


def test_the_next_session_is_handed_the_previous_session_s_last_note(
    kyo, write_file
) -> None:
    """The guarantee end to end: session one writes and consumes its own log, session two
    opens and is handed the last thing session one said, without knowing any id."""
    assert kyo("brief", "--session-start").code == 0
    _write_shift_note(kyo, write_file, "a.md", "started the refactor")
    _write_shift_note(kyo, write_file, "b.md", "handoff: the seam is in export.py")
    assert kyo("brief").code == 0  # session one reads its own log and stamps the watermark

    opening = kyo("brief", "--session-start")
    assert "handoff: the seam is in export.py" in opening.out
    assert _handoff(kyo)["note"]["text"] == "handoff: the seam is in export.py"


def test_with_no_session_ever_declared_the_brief_says_which_question_it_answered(
    kyo, write_file
) -> None:
    """The fallback for a project whose SessionStart hook is not installed. The weaker
    guarantee — "the most recent note" rather than "the last one before YOUR session" —
    still holds, and the brief must not let the two read alike (law 6)."""
    _write_shift_note(kyo, write_file, "s.md", "whatever the watermark says")
    assert kyo("brief").code == 0
    assert kyo("brief").code == 0

    handoff = _handoff(kyo)
    assert handoff["basis"] == "no-session-recorded"
    assert handoff["note"]["text"] == "whatever the watermark says"
    out = kyo("brief").out
    assert "no brief run has ever declared a session start" in out
    assert "kyo install-hooks" in out


def test_a_brief_with_no_shift_notes_at_all_still_prints_the_handoff_block(kyo) -> None:
    """Always a block, never only when occupied: a heading that appears only when it has
    content teaches a reader that its absence means nothing was checked (the KYO-48
    reasoning, applied here)."""
    out = kyo("brief", "--session-start").out
    assert "handoff" in out
    assert "(none" in out
    assert _handoff(kyo, "--session-start")["note"] is None


def test_the_guaranteed_handoff_never_resurrects_a_redacted_note(kyo, write_file) -> None:
    """A guarantee that always surfaces the last note must not become a way for redacted
    prose to come back, and must not quietly walk BACK to the note before it either —
    that would republish, as "your predecessor's closing judgment", something nobody
    chose to hand over."""
    _write_shift_note(kyo, write_file, "a.md", "an earlier thought nobody handed over")
    note_id = _write_shift_note(kyo, write_file, "b.md", "token ghp_secretvalue leaked")
    assert kyo("redact", note_id, "--why", "a token slipped in").code == 0
    # Consume the window, so what is left in the brief is the handoff block alone.
    assert kyo("brief", "--session-start").code == 0

    out = kyo("brief").out
    assert "shift log since last brief (0)" in out
    assert "ghp_secretvalue" not in out
    assert "an earlier thought nobody handed over" not in out
    assert "a token slipped in" in out
    assert "nothing here to read" in out
    handoff = _handoff(kyo)
    assert handoff["note"]["id"] == note_id
    assert handoff["note"]["redacted_why"] == "a token slipped in"


def test_the_session_anchor_is_recorded_in_the_git_canonical_brief_ledger(
    kyo, project, write_file
) -> None:
    """The session concept is a stored fact, not an in-memory one: it survives into
    `_briefs.jsonl`, which is what `kyo rebuild` reads."""
    assert kyo("brief", "--session-start").code == 0
    assert kyo("brief").code == 0
    rows = [
        json.loads(line)
        for line in (project / "kyosei" / "export" / "_briefs.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [row["session_start"] for row in rows] == [1, 0]


def test_the_handoff_basis_values_the_renderer_knows_are_the_ones_the_command_emits(
    kyo, write_file
) -> None:
    """`render.py` restates the two basis values rather than importing `cmd_brief`
    (which imports `render`). Law 3 says one of the two copies has to be checked against
    the other, or the block silently stops matching what produced it."""
    from kyosei import cmd_brief, render

    assert render.HANDOFF_BASIS_SESSION == cmd_brief.HANDOFF_BASIS_SESSION
    assert render.HANDOFF_BASIS_NO_SESSION == cmd_brief.HANDOFF_BASIS_NO_SESSION
