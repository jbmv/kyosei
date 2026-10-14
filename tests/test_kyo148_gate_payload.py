"""KYO-148: a gate's payload can be written as prose and corrected without a false clearing.

Two halves, separable, and the first is the one that bit a real operator. `kyo gate`
took ``--do TEXT`` and nothing else — no ``--do-file``, no stdin — while every other
prose field in the tool takes a file precisely so its content can be readable. An
operator went to answer a ``ui-review`` gate whose payload held a query fragment and
half a command rather than a URL, because a procedure with a command and several links
had to be written on one line, and could not reach the view at all.

The second half is that gates were absent from `kyo edit`, so correcting a ``do:``
needed `ungate` + `gate`: a ``cleared`` written into a git-canonical ledger for a
condition that did not clear, and a new gate id for what is textually one continuing
obligation. The reporting consumer paid that three times.

What each test group covers:

* ``c1`` — the file and stdin routes, and that what arrives is stored byte-for-byte.
* ``c2`` — the correction changes the payload and NOTHING else: same gate id, still
  open, no new gate, no ``cleared`` anywhere.
* ``c3`` — the correction is its own named event, and the previous text stays readable
  in both places that matter: this command's echo, and ``_events.jsonl``.
* ``c4`` — ``--do TEXT`` still works, and is still the whole story for a short payload.

Plus the group the record did not ask for and the change made necessary (law 2 — every
mechanism ships in the commit that needs it): three renderings put one gate on one line,
and a multi-line payload would have broken out of its own block in all three, including
the `kyo brief --compact` the SessionStart hook injects into every session.
"""

from __future__ import annotations

import json

import pytest

from kyosei.render import elided_first_line

from .conftest import new_record

PROCEDURE = """\
Open the staging app and check the read-only banner in both modes:

1. http://localhost:5173/records?previewBanner=readonly
   the banner must say "read only" and the Save button must be absent
2. http://localhost:5173/ideas?previewBanner=readonly
   the same banner, and the composer must be disabled"""

CORRECTED = PROCEDURE + "\n\nStart it with `npm --prefix apps/desktop run dev` first."


def _gate_with(kyo, record, *args, gate_type="awaiting:human-review"):
    result = kyo("gate", record, gate_type, *args, "--json")
    assert result.code == 0, result
    return result.json["gate_id"]


def _gate_doc(kyo, record: str, gate_id: str) -> dict:
    gates = kyo("show", record, "--json").json["gates"]
    return next(g for g in gates if g["id"] == gate_id)


def _events(project) -> list[dict]:
    """Every line of the git-canonical events ledger, in order.

    KYO-268.c2: the ledger is a directory of bounded shards, so this globs for the
    export directory and reads it through `events_ledger.events_files_in` rather than opening
    one named file."""
    from kyosei.events_ledger import events_files_in

    export_dir = next(p for p in project.glob("**/export") if p.is_dir())
    return [
        json.loads(line)
        for path in events_files_in(export_dir)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --------------------------------------------------------------------------------- c1


def test_a_multi_line_payload_arrives_from_a_file_and_is_stored_verbatim(
    kyo, write_file
) -> None:
    """c1. The incident's own payload: a procedure with a command and several links,
    each with what to look at. Before this it could not be written at all."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record, "--do-file", write_file("do.md", PROCEDURE + "\n"))
    assert _gate_doc(kyo, record, gate_id)["do"] == PROCEDURE


def test_a_multi_line_payload_arrives_from_stdin(kyo) -> None:
    """c1's other half. `--do-file -` is the same route the rest of the tool spells
    the same way, and it is what makes law 5's "by file OR STDIN" true here."""
    record = new_record(kyo)
    result = kyo(
        "gate", record, "awaiting:human-review", "--do-file", "-", "--json", stdin=PROCEDURE
    )
    assert result.code == 0, result
    assert _gate_doc(kyo, record, result.json["gate_id"])["do"] == PROCEDURE


def test_the_payload_is_echoed_back_whole_with_its_length_and_digest(kyo, write_file) -> None:
    """Law 5: the caller sees what landed. A multi-line payload is echoed line by line,
    not summarised — this is the one surface that prints all of it."""
    record = new_record(kyo)
    result = kyo(
        "gate", record, "awaiting:human-review", "--do-file", write_file("do.md", PROCEDURE)
    )
    assert result.code == 0, result
    assert f"stored do ({len(PROCEDURE)} chars" in result.out
    for line in PROCEDURE.split("\n"):
        assert f"  | {line}" in result.out


def test_a_gate_type_that_requires_a_payload_accepts_the_file_form(kyo, write_file) -> None:
    """The three payload-requiring types are the ones whose payload is most likely to be
    a procedure. `awaiting:human-run` is literally "the exact thing to run"."""
    record = new_record(kyo)
    gate_id = _gate_with(
        kyo,
        record,
        "--do-file",
        write_file("run.md", PROCEDURE),
        gate_type="awaiting:human-run",
    )
    assert _gate_doc(kyo, record, gate_id)["do"] == PROCEDURE


def test_the_refusal_for_a_missing_payload_names_both_routes(kyo) -> None:
    """CLAUDE.md: an error naming a remedy owes a test that the remedy clears it. The
    refusal named only `--do` when only `--do` existed; now it names both, and both
    work — the file half is proved by the test above, the flag half here."""
    record = new_record(kyo)
    refused = kyo("gate", record, "awaiting:human-run")
    assert refused.code == 64, refused
    assert "--do TEXT" in refused.err and "--do-file" in refused.err
    assert kyo("gate", record, "awaiting:human-run", "--do", "run make test").code == 0


def test_an_empty_payload_file_is_refused_out_loud(kyo, write_file) -> None:
    """Law 6: an empty file is never silently read as "argument not given". A payload is
    not a description — an empty one is a gate nobody can clear — so this refuses and
    names the way to actually say "no payload", which the next test drives."""
    record = new_record(kyo)
    result = kyo("gate", record, "awaiting:human-review", "--do-file", write_file("e.md", ""))
    assert result.code == 64, result
    assert "empty payload" in result.err
    assert "omit --do and --do-file entirely" in result.err


def test_the_remedy_the_empty_file_refusal_names_actually_works(kyo) -> None:
    """The remedy named above, driven. `awaiting:human-review` is the one gate type that
    may carry no payload, which is what makes "omit them entirely" a real route."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record)
    assert _gate_doc(kyo, record, gate_id)["do"] is None


def test_passing_both_forms_at_once_is_refused(kyo, write_file) -> None:
    """The `--why` / `--why-file` precedent: two sources for one field is a question the
    tool must not answer by picking one."""
    record = new_record(kyo)
    result = kyo(
        "gate", record, "awaiting:human-review",
        "--do", "one line", "--do-file", write_file("do.md", PROCEDURE),
    )
    assert result.code == 64, result
    assert "not both" in result.err


# --------------------------------------------------------------------------------- c4


def test_the_one_line_form_still_works_and_still_refuses_a_newline(kyo) -> None:
    """c4 is a boundary, not a feature: a project must not be forced into a file for a
    single command. `--do` is unchanged — it takes one line and refuses two."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record, "--do", "run make test")
    assert _gate_doc(kyo, record, gate_id)["do"] == "run make test"

    refused = kyo("gate", record, "awaiting:human-review", "--do", "first\nsecond")
    assert refused.code == 64, refused
    assert "single line" in refused.err


def test_do_now_refuses_shell_residue_and_names_the_twin_it_just_acquired(kyo) -> None:
    """The consequence of c1 that nobody has to remember: KYO-91 derives the response to
    shell residue from whether the flag has a `--<flag>-file` twin on the same
    subparser — refuse and name the twin, or warn and store. `--do` had no twin and was
    only warned about. It has one now, so it refuses, with no edit to that machinery
    ("ship a `--title-file` one day and `--title` starts refusing, here, with no edit").
    """
    record = new_record(kyo)
    result = kyo("gate", record, "awaiting:human-review", "--do", "dead because  it is")
    assert result.code == 64, result
    assert "--do-file" in result.err, "a refusal must name a way to succeed"
    assert kyo("show", record, "--json").json["gates"] == [], "nothing was stored"


def test_the_route_that_refusal_names_accepts_the_very_same_text(kyo, write_file) -> None:
    """The other half of the rule above: the gap may genuinely be what someone meant,
    and `--do-file` is how they say so. It stores the text rather than tidying it."""
    record = new_record(kyo)
    text = "dead because  it is"
    gate_id = _gate_with(kyo, record, "--do-file", write_file("do.md", text))
    assert _gate_doc(kyo, record, gate_id)["do"] == text


# --------------------------------------------------------------------------------- c2


def test_correcting_a_payload_changes_the_payload_and_nothing_else(kyo, write_file) -> None:
    """c2, as a whole-object comparison rather than a list of fields anyone could forget
    to extend: everything about the gate except `do` must be identical afterwards."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record, "--do-file", write_file("do.md", PROCEDURE))
    before = _gate_doc(kyo, record, gate_id)

    result = kyo("redo", gate_id, "--do-file", write_file("fix.md", CORRECTED))
    assert result.code == 0, result

    after = _gate_doc(kyo, record, gate_id)
    assert after["do"] == CORRECTED
    assert {k: v for k, v in after.items() if k != "do"} == {
        k: v for k, v in before.items() if k != "do"
    }


def test_correcting_a_payload_mints_no_new_gate_and_clears_nothing(kyo) -> None:
    """The two costs of the `ungate` + `gate` round trip this replaces, asserted
    directly: the record still has exactly one gate, it is the same one, and it is
    open."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record, "--do", "the wrong instruction")
    assert kyo("redo", gate_id, "--do", "the right instruction").code == 0

    gates = kyo("show", record, "--json").json["gates"]
    assert [g["id"] for g in gates] == [gate_id]
    assert gates[0]["cleared_at"] is None and gates[0]["cleared_why"] is None


def test_no_event_in_the_whole_ledger_says_this_gate_cleared(kyo, project) -> None:
    """The claim c2 actually makes is about the LEDGER, which is git-canonical and
    permanent — so it is checked there rather than only on the current document. The
    reporting consumer's complaint was not that the gate looked wrong afterwards; it was
    that `cleared` was written for a condition that did not clear."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record, "--do", "the wrong instruction")
    assert kyo("redo", gate_id, "--do", "the right instruction").code == 0

    commands = [e["command"] for e in _events(project)]
    assert f"redo {gate_id}" in commands
    assert not [c for c in commands if c.startswith("ungate")]
    for event in _events(project):
        for gate in (event["after"] or {}).get("gates", []):
            assert gate["cleared_at"] is None


def test_the_echo_says_out_loud_that_nothing_was_cleared_or_created(kyo) -> None:
    """`retag`'s precedent. The whole point of the command is that it is NOT the round
    trip it replaces, and a property held silently is one a reader cannot check."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record, "--do", "before")
    result = kyo("redo", gate_id, "--do", "after")
    assert "no gate was cleared and no gate was created" in result.out
    assert f"still open — {gate_id}" in result.out


def test_a_cleared_gate_refuses_correction_and_says_why(kyo) -> None:
    """Where this parts company with `kyo retag`, deliberately. Retagging a cleared gate
    is allowed because a tag told nobody anything a correction would falsify. A payload
    did: somebody was told to do this, did it, and `cleared_why` is recorded against
    these words. That is `_correct_criterion_text`'s hazard (KYO-58) exactly, and this
    follows that precedent rather than `retag`'s."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record, "--do", "look at the banner")
    assert kyo("ungate", gate_id, "--why", "looked").code == 0

    result = kyo("redo", gate_id, "--do", "look at something else")
    assert result.code != 0, result
    assert "no longer correctable" in result.err
    assert "`kyo gate`" in result.err, "a refusal names what to do instead"
    assert _gate_doc(kyo, record, gate_id)["do"] == "look at the banner"


def test_the_remedy_the_cleared_refusal_names_works(kyo) -> None:
    """The remedy above, driven: a still-live obligation becomes a new gate, which is an
    honest new obligation rather than an edit to a finished one."""
    record = new_record(kyo)
    first = _gate_with(kyo, record, "--do", "look at the banner")
    assert kyo("ungate", first, "--why", "looked").code == 0
    second = _gate_with(kyo, record, "--do", "look at the other banner too")
    assert second != first
    assert _gate_doc(kyo, record, second)["do"] == "look at the other banner too"


def test_redo_needs_a_payload_to_replace_the_old_one_with(kyo) -> None:
    """One of the two forms is required: an absent flag cannot mean "clear the payload"
    on a command whose only purpose is to set one."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record, "--do", "before")
    result = kyo("redo", gate_id)
    assert result.code == 64, result


def test_redo_takes_a_multi_line_payload_from_stdin_too(kyo) -> None:
    """The correction path is the one most likely to need the file form — the payload is
    being corrected precisely because the first attempt could not say enough."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record, "--do", "half a command")
    result = kyo("redo", gate_id, "--do-file", "-", stdin=PROCEDURE)
    assert result.code == 0, result
    assert _gate_doc(kyo, record, gate_id)["do"] == PROCEDURE


# --------------------------------------------------------------------------------- c3


def test_the_correction_is_its_own_named_event(kyo, project) -> None:
    """c3. `retag`'s reasoning: its own command string, so the change is a named act in
    `_events.jsonl` rather than an unexplained difference between two exports — and one
    that reads as neither a gating nor a clearing."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record, "--do", "before")
    assert kyo("redo", gate_id, "--do", "after").code == 0

    matching = [e for e in _events(project) if e["command"] == f"redo {gate_id}"]
    assert len(matching) == 1, [e["command"] for e in _events(project)]
    assert matching[0]["record_id"] == record


def test_the_previous_payload_stays_readable_in_the_ledger(kyo, project, write_file) -> None:
    """c3's substance, and why this needs no new column: every mutation writes the
    record's whole document before and after, so the ledger holds the old payload
    verbatim under the correction's own name. That is the same place a criterion
    corrected in place leaves its old wording."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record, "--do-file", write_file("do.md", PROCEDURE))
    assert kyo("redo", gate_id, "--do-file", write_file("fix.md", CORRECTED)).code == 0

    event = next(e for e in _events(project) if e["command"] == f"redo {gate_id}")
    # KYO-418: the prior `gates` list is in `changed` on a line written from 25 on.
    was = next(g for g in event["changed"]["gates"] if g["id"] == gate_id)
    now = next(g for g in event["after"]["gates"] if g["id"] == gate_id)
    assert was["do"] == PROCEDURE
    assert now["do"] == CORRECTED


def test_the_echo_prints_the_previous_payload_beside_the_new_one(kyo, write_file) -> None:
    """Law 5, and the reason replacement is safe to offer at all: a caller who meant to
    APPEND can see that they replaced. Both texts are measured, so a correction that
    silently lost a line is visible as a digest that does not match."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record, "--do-file", write_file("do.md", PROCEDURE))
    result = kyo("redo", gate_id, "--do-file", write_file("fix.md", CORRECTED))
    assert result.code == 0, result
    assert f"stored do ({len(CORRECTED)} chars" in result.out
    assert f"it previously said ({len(PROCEDURE)} chars" in result.out
    for line in PROCEDURE.split("\n"):
        assert f"  | {line}" in result.out


def test_correcting_to_the_same_text_says_nothing_changed(kyo) -> None:
    """Law 6: "ran and changed nothing" is a different statement from "did not run", and
    the caller who expected a change is the one who needs to hear it."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record, "--do", "the same words")
    result = kyo("redo", gate_id, "--do", "the same words")
    assert result.code == 0, result
    assert "nothing changed" in result.out


def test_the_json_document_carries_both_payloads(kyo) -> None:
    """A `--json` caller is the one least able to read a line of prose off stdout, and
    is the likeliest to be a controller correcting a payload on someone's behalf."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record, "--do", "before")
    result = kyo("redo", gate_id, "--do", "after", "--json")
    assert result.json["do"] == "after"
    assert result.json["previous_do"] == "before"


# ------------------------------------------------- what a multi-line payload would break
#
# Law 2's other direction: allowing the field to hold newlines put three existing
# renderings at risk in the same commit, so the eliding ships here rather than being
# discovered as a corrupted brief.


def test_a_multi_line_payload_stays_on_one_line_in_kyo_show(kyo, write_file) -> None:
    """The gate list is one line per gate, and the claims block is indented UNDER it.
    A raw newline starts at column 0, so it escapes the block and the claims below it
    appear to belong to nothing."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record, "--do-file", write_file("do.md", PROCEDURE))
    gate_lines = [line for line in kyo("show", record).out.split("\n") if gate_id in line]
    assert len(gate_lines) == 1, gate_lines
    assert "the composer must be disabled" not in gate_lines[0]
    assert "+5 more line(s)" in gate_lines[0]


def test_a_multi_line_payload_stays_on_one_line_in_the_compact_brief(
    kyo, write_file
) -> None:
    """The highest-consequence of the three: the gated list is board-wide and
    `--compact` is what the SessionStart hook injects into every session, so one
    multi-line payload would run every gate on the board together."""
    record = new_record(kyo)
    _gate_with(kyo, record, "--do-file", write_file("do.md", PROCEDURE))
    brief = kyo("brief", "--compact").out
    gated = [line for line in brief.split("\n") if line.startswith(f"  {record}  ")]
    assert len(gated) == 1, gated
    assert "the composer must be disabled" not in gated[0]


def test_what_the_elision_points_at_really_does_print_the_whole_payload(
    kyo, write_file
) -> None:
    """CLAUDE.md again: the note names `kyo show <record> --json` as where the rest is,
    and a pointer at a surface that did not carry the text would be worse than none."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record, "--do-file", write_file("do.md", PROCEDURE))
    line = next(x for x in kyo("show", record).out.split("\n") if gate_id in x)
    assert f"`kyo show {record} --json`" in line
    assert _gate_doc(kyo, record, gate_id)["do"] == PROCEDURE


def test_a_single_line_payload_renders_exactly_as_it_always_did(kyo) -> None:
    """The elision must be invisible when there is nothing to elide — otherwise every
    existing gate's rendering changes to buy a case none of them are in."""
    record = new_record(kyo)
    gate_id = _gate_with(kyo, record, "--do", "run make test")
    line = next(x for x in kyo("show", record).out.split("\n") if gate_id in x)
    assert line.endswith("do: run make test")


def test_a_multi_line_payload_cannot_corrupt_the_stale_criteria_flag(
    kyo, write_file
) -> None:
    """The one place a payload is copied into a STORED field rather than printed. The
    KYO-59 flag quotes the gate inside a parenthetical and writes the result to
    `flags.detail`, which is permanent, has no correction command, and is rendered by
    four one-line surfaces. A newline reaching it would be unfixable."""
    record = new_record(kyo, criteria=["a criterion written before the gate cleared"])
    gate_id = _gate_with(
        kyo, record, "--do-file", write_file("do.md", PROCEDURE), gate_type="holding-for"
    )
    result = kyo("ungate", gate_id, "--why", "decided")
    assert result.code == 0, result

    flags = kyo("show", record, "--json").json["flags"]
    stale = [f for f in flags if "stale" in f["flag_type"]]
    assert stale, flags
    assert "\n" not in stale[0]["detail"]
    assert "the composer must be disabled" in stale[0]["detail"], "flattened, not truncated"


# ------------------------------------------------------- the eliding rule's own guards


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("one line", ("one line", "")),
        ("first\nsecond", ("first", "  (+1 more line(s) over there)")),
        ("first\nsecond\nthird", ("first", "  (+2 more line(s) over there)")),
        ("", ("", "")),
        ("trailing\n", ("trailing", "  (+1 more line(s) over there)")),
    ],
)
def test_the_eliding_rule_itself(text: str, expected: tuple[str, str]) -> None:
    """Directly, because three surfaces now depend on it agreeing with itself. The empty
    string is included deliberately: it must not claim lines were omitted."""
    assert elided_first_line(text, more_at="over there") == expected


def test_the_eliding_rule_is_shared_rather_than_copied() -> None:
    """CLAUDE.md: a mechanism ships with a test that fails if the mechanism is removed.
    What is removable here is the SHARING — three surfaces each growing their own copy
    would pass every test above while being exactly the drift the extraction prevents.
    So this asserts the single definition is the one all three reach for."""
    from pathlib import Path

    from kyosei import render

    text = Path(render.__file__).read_text(encoding="utf-8")
    assert text.count("def elided_first_line(") == 1, "one definition"
    # The definition plus `claims_beside_gate`, `show_text`, `brief_text`'s gated section
    # and its carried section (KYO-497). If another surface starts eliding, raise this
    # number — do not inline the rule there.
    assert text.count("elided_first_line(") == 5, "one definition, four call sites"
