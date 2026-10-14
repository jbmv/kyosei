"""Law 5 — prose enters via file or stdin only, and every write echoes what it stored.

Shell interpolation can silently delete words mid-sentence while the write reports
success. These tests assert the two mechanisms that make that visible:
no prose-shaped flag values, and an echo carrying length and digest.
"""

from __future__ import annotations

import hashlib

import pytest

from kyosei.cli import PROSE_ARGUMENT_METAVAR, build_parser, prose_argument_flags
from kyosei.prose import check_not_shell_mangled, contains_phrase, marked_residue, shell_residue

from .conftest import new_record

PARAGRAPH = "The widget explodes when the cache is cold.\n\nIt only happens after a deploy."


def _prose_flags(parser) -> list[str]:
    """Every option string the parser exposes that takes a value."""
    found: list[str] = []
    stack = [parser]
    while stack:
        current = stack.pop()
        for action in current._actions:
            # Subparsers actions carry a dict of name -> sub-parser in `.choices`; a
            # plain enum-choice positional (e.g. `kyo ideas <review>`) carries a list
            # of strings instead, and must not be mistaken for a nested parser.
            if isinstance(action.choices, dict) and action.choices:
                stack.extend(action.choices.values())
            elif action.nargs != 0 and action.option_strings:
                found.extend(action.option_strings)
    return found


def test_no_flag_accepts_multi_line_prose(kyo) -> None:
    """Descriptions and notes have no flag-value form at all.

    Only ``--title``, ``--why``, ``--do`` and ``--method`` carry text as arguments,
    and each is single-line-enforced (see the next test) — ``--method`` is a free
    one-line description on ``kyo claim`` (an enum on ``kyo verify``, which is a
    strictly narrower case of "single line").
    """
    value_flags = {f for f in _prose_flags(build_parser()) if not f.startswith("--root")}
    # KYO-494: `kyo confirm --quote` is a fifth, single-line and residue-refused like
    # `--why` because it has a `--quote-file` twin.
    # KYO-565: `kyo rules add --enforced-by` is a sixth, warned rather than refused (no
    # `--enforced-by-file` twin).
    text_bearing = {"--title", "--why", "--do", "--method", "--quote", "--enforced-by"}
    file_bearing = {f for f in value_flags if f.endswith("-file")}
    structural = {
        "--actor",
        "--prefix",
        "--plan",
        "--criterion",
        "--gate",  # a gate id `kyo confirm` answers, not prose (KYO-494)
        "--state",
        "--supersede-note",
        "--kind",
        "--status",
        "--level",
        "--verdict",
        "--supports",
        "--supersedes",
        "--brief-out",  # a file path to write to, not prose content (M5, KYO-7)
        "--to-successor",  # a list of criterion ids, not prose (M5, KYO-7)
        "--out",  # a directory path to write the static viewer to, not prose (M7, KYO-11)
        "--also-covers",  # a list of record ids, not prose (M8, KYO-13.c4)
        "--against",  # a git ref name, not prose (M8, KYO-13.c5)
        "--worktree",  # a directory path the work happens in, not prose (M9, KYO-22)
        "--branch",  # a git branch name the commits must land on, not prose (KYO-32)
        "--store",  # a directory path the split store lives at, not prose (KYO-54)
        "--model",  # a model name the worker runs on, not prose (KYO-61)
        "--since-note",  # a shift-note id (PREFIX-Sn), not prose and not a time (KYO-498)
        # KYO-565: `kyo rules add --audience` is a comma list of registry tokens, refused
        # against the closed `procedures.KNOWN_AUDIENCES` on `--scope`'s terms.
        "--audience",
        # KYO-100: a registry token, not prose — and the most strongly structural entry
        # in this set, because it is the only one checked against a CLOSED list of legal
        # values at write time. A `--scope` carrying the residue a shell substitution
        # leaves is not stored-and-warned-about; it is refused outright by the registry,
        # which is a stronger guarantee than the one-line flags get.
        "--scope",
        # KYO-102: registry tokens on exactly `--scope`'s terms, and refused by the same
        # closed-list check — `--tag` writes a classification onto a gate, `--gate-tag`
        # searches for the gates carrying one. The prose on a gate is `--do`, which is in
        # `text_bearing` above and stays there: the tag did not replace the payload.
        "--tag",
        "--gate-tag",
        # KYO-107: an address into a file, not prose about one. `kyo promote --section`
        # names a heading that has to already be in the idea file, and it is checked
        # against a closed list at write time on exactly `--scope`'s terms — the closed
        # list here is the set of headings THAT file holds, and a value outside it is
        # refused with the list named, never stored. Nothing it carries reaches the store:
        # the record's prose comes from the file, and the heading only says which part.
        "--section",
        # KYO-120: a field name, not prose about one. `kyo base run --group-by` names
        # one of the source's own scalar fields and is refused by
        # `bases.group_by_problem` — the same closed-list check the stored `group-by:`
        # key gets — before the query runs. Structurally the strongest entry in this
        # set: `kyo base run` writes nothing at all, so there is no stored field for a
        # value carrying shell residue to reach even if the closed list admitted it.
        "--group-by",
        # KYO-137: a record id, not prose. `kyo docs obligations --record` filters a
        # read-only listing — it writes nothing at all, so there is no stored field a
        # value carrying shell residue could reach, and a value naming no record
        # simply matches no rows.
        "--record",
        # KYO-138: a file path to write the generated reference to, not prose. Same shape
        # as `--brief-out` and `--out` above, and structurally stronger than either: the
        # reference's CONTENT comes entirely from the argparse tree, so nothing a value
        # carries can reach the output. A path whose directory does not exist is refused
        # with the remedy named, never written.
        "--output",
        # KYO-198: ids, not prose. `kyo verify --from-report` names a dispatch and
        # `--only` names criteria of that dispatch's own stored report — both are looked
        # up, and a value naming nothing is refused (exit 2) with the offender quoted,
        # never stored. Structurally these sit beside `--group-by` at the strong end of
        # this set: everything the command writes is read out of the report, so no
        # character either flag carries reaches a stored field even if the lookups
        # admitted it.
        "--from-report",
        "--only",
        # KYO-343: criterion ids, the inverse of `--only`, looked up against the report's
        # own criteria_verdicts and refused before anything is written if one is absent.
        "--except",
        # KYO-147.c4: a dispatch id, not prose. `kyo edit --touches-from-drift` looks the
        # dispatch up and reads the correction out of the store; a value naming no
        # dispatch is refused before anything is written. Nothing the value carries
        # reaches a stored field — the touches it stores come from the drift finding,
        # never from this argument — which puts it at the strong end of this set beside
        # `--from-report`, whose shape it shares exactly.
        "--touches-from-drift",
        # KYO-420: a comma-separated list of the document's own key names, checked
        # against the keys the object has and refused naming them, on a read that
        # writes nothing; and a row count, which argparse parses as a positive integer.
        "--fields",
        "--limit",
        # KYO-151.c2: a rule title, not prose about one. `kyo install-procedures
        # --adopt` is checked against the CLOSED set of titles the packaged ledger
        # ships — on exactly `--scope`'s terms — and a title matching none of them is
        # refused, naming why, before anything is written. Nothing the value carries
        # reaches a stored field: the text that lands in kyosei/procedures.md is the
        # packaged rule's own, never the argument itself.
        "--adopt",
        # KYO-226: a date or a commit-ish, not prose, on a command that writes nothing
        # at all — `kyo changelog` filters a data file packaged with the build (or, in a
        # Kyosei checkout, the store). Structurally it sits with `--group-by` at the
        # strong end of this set for that reason: there is no stored field for a value
        # carrying shell residue to reach even if the parse admitted it, and the parse
        # does not — a value that is neither a YYYY-MM-DD date nor a commit the checkout
        # resolves is refused, naming both forms that work. Law 16 is untouched: this
        # filters a read, and nothing anywhere stores a timestamp a caller typed.
        "--since",
        # KYO-312: an integer of milliseconds, parsed as one (`type=int`), on a command
        # that writes nothing -- `kyo check-command --timeout-ms` restates the timeout a
        # Bash call declared so the ceiling can be asked about by hand. No prose can
        # survive the parse and there is no stored field for it to reach.
        "--timeout-ms",
        # KYO-330: a file path, not prose, exactly as `--description-file`/
        # `--criteria-file`/`--touches-file` are -- it just does not end in `-file`
        # because it is not one of those three, it is all of them (plus `--title`/
        # `--scope`/`--plan`) in one document. `kyo new/promote/spawn --from FILE`
        # reads the prose OUT of that path; the flag's own value is the path,
        # law 5's boundary, on exactly `--brief-out`/`--out`/`--output`'s terms above.
        "--from",
        # KYO-453: `kyo serve`'s listening options -- a port number, a loopback address
        # (anything else is refused), and the Host names and Origins the server answers.
        # Network identifiers checked when the server starts or a request arrives; none
        # is stored anywhere, and there is no field for prose to reach.
        "--port",
        "--bind",
        "--allow-host",
        "--allow-origin",
        # KYO-454: `kyo activity --finished N` is a count and `kyo gates --type` a gate
        # type from the closed GATE_TYPES list; both narrow a read and store nothing.
        "--finished",
        "--type",
        # KYO-485: `kyo dispatch --base` is a git ref, resolved to a sha before anything
        # is stored; the sha is what lands on the dispatch, never the flag's text.
        "--base",
        # KYO-536: `kyo close-wave --suite-passed` is a command line, matched against the
        # project's declared whole-suite commands and refused unless it is one; it is kept
        # in the checkout's git directory, never in the store.
        "--suite-passed",
    }
    assert value_flags <= text_bearing | file_bearing | structural, value_flags


def test_single_line_flags_refuse_multi_line_values(kyo) -> None:
    record = new_record(kyo)
    result = kyo("edit", record, "--title", "first line\nsecond line")
    assert result.code == 64
    assert "single line" in result.err
    assert kyo("show", record, "--json").json["title"] == "A record"


def test_description_arrives_from_a_file_and_is_echoed_back(kyo, write_file) -> None:
    path = write_file("desc.md", PARAGRAPH + "\n")
    record = new_record(kyo)
    result = kyo("edit", record, "--description-file", path)
    assert result.code == 0

    digest = hashlib.sha256(PARAGRAPH.encode()).hexdigest()[:12]
    assert f"({len(PARAGRAPH)} chars, sha256:{digest})" in result.out
    for line in PARAGRAPH.split("\n"):
        assert f"  | {line}" in result.out
    assert kyo("show", record, "--json").json["description"] == PARAGRAPH


def test_description_arrives_from_stdin(kyo) -> None:
    record = new_record(kyo)
    result = kyo("edit", record, "--description-file", "-", stdin=PARAGRAPH + "\n")
    assert result.code == 0
    assert kyo("show", record, "--json").json["description"] == PARAGRAPH


def test_an_empty_file_means_an_empty_value(kyo, write_file) -> None:
    """Law 6: an empty list means empty — never "argument not given"."""
    record = new_record(kyo)
    assert kyo("edit", record, "--description-file", write_file("d.md", "text")).code == 0
    result = kyo("edit", record, "--description-file", write_file("empty.md", ""))
    assert result.code == 0
    assert "stored description (0 chars" in result.out
    assert kyo("show", record, "--json").json["description"] == ""


def test_an_empty_criteria_file_adds_zero_criteria_and_says_so(kyo, write_file) -> None:
    record = new_record(kyo)
    result = kyo("edit", record, "--add-criteria-file", write_file("none.txt", "\n\n"))
    assert result.code == 0
    assert "stored criteria: 0 added" in result.out
    assert kyo("show", record, "--json").json["criteria"] == []


def test_a_missing_prose_file_fails_loudly(kyo) -> None:
    record = new_record(kyo)
    result = kyo("edit", record, "--note-file", "/nonexistent/note.md")
    assert result.code == 1
    assert "not a file" in result.err


def test_every_mutation_echoes_what_it_stored(kyo, write_file) -> None:
    record = new_record(kyo, criteria=["a criterion"])
    checks = [
        (kyo("edit", record, "--title", "New title"), "stored title: New title"),
        (kyo("gate", record, "awaiting:human-run", "--do", "run make test"), "stored do ("),
        (kyo("move", record, "working"), "stored status: open -> working"),
        (
            kyo("edit", record, "--note-file", write_file("n.md", "a note")),
            "stored note",
        ),
        # KYO-490: the gate above is still open, and settling over it takes --force.
        (kyo("wont-do", record, "--why", "no longer needed", "--force"), "stored why ("),
    ]
    for result, expected in checks:
        assert result.code == 0, result
        assert expected in result.out, result


# ------------------------------------------------------------------------- KYO-52
#
# Prose is not path-typed and never will be. The column-level fix
# (`contracts.store_path`) reaches `dispatches.worktree` and `touches.path`; it
# cannot reach a path quoted inside an idea, an evidence blob, or a `kyo doctor`
# error pasted into a note — and those are where most of this repo's own stored
# usernames came from. So the write path warns, in the same non-refusing style as
# `kyo dispatch`'s bare-number warning (KYO-27).

HOME_PATH = "/Users/someone/WebstormProjects/other-project/src/app.ts"


def test_a_path_in_stored_prose_is_warned_about_and_not_refused(kyo) -> None:
    """KYO-52.c3: the warning fires, the write still happens, the exit code is 0 and
    the prose is stored exactly as given."""
    record = new_record(kyo, criteria=["a"])
    result = kyo(
        "edit", record, "--note-file", "-", stdin=f"doctor said {HOME_PATH} is missing"
    )
    assert result.code == 0, result
    assert "warning" in result.out
    assert HOME_PATH in result.out
    assert "git-canonical" in result.out

    notes = kyo("show", record, "--json").json["notes"]
    assert notes[-1]["text"] == f"doctor said {HOME_PATH} is missing"


def test_the_path_warning_is_carried_in_the_json_document_too(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    result = kyo(
        "edit", record, "--note-file", "-", "--json", stdin=f"see {HOME_PATH}"
    )
    assert result.code == 0, result
    assert result.json["path_warnings"] == [HOME_PATH]


def test_a_single_line_reason_flag_is_warned_about_the_same_way(kyo) -> None:
    """`--why` is stored prose too, and "a doctor error pasted into a reason" is
    exactly the `--why` case."""
    record = new_record(kyo, criteria=["a"])
    result = kyo("wont-do", record, "--why", f"superseded by the work in {HOME_PATH}")
    assert result.code == 0, result
    assert HOME_PATH in result.out
    assert "warning" in result.out


def test_an_ordinary_note_produces_no_path_warning(kyo) -> None:
    """A warning that fires on ordinary input is one people learn to route around —
    the same reasoning that keeps the apostrophe out of `check_not_shell_mangled`.
    Machine-generic paths disclose nothing about who ran the command."""
    record = new_record(kyo, criteria=["a"])
    result = kyo(
        "edit",
        record,
        "--note-file",
        "-",
        stdin="the traceback points at /usr/lib/python3.12/json/decoder.py and /etc/hosts",
    )
    assert result.code == 0, result
    assert "warning" not in result.out


def test_the_warning_does_not_leak_from_one_invocation_into_the_next(kyo) -> None:
    """The collector is per-invocation. A note stored an hour ago must not warn
    again on an unrelated command, or the warning becomes noise and stops meaning
    anything."""
    record = new_record(kyo, criteria=["a"])
    assert kyo("edit", record, "--note-file", "-", stdin=f"see {HOME_PATH}").code == 0

    later = kyo("show", record)
    assert later.code == 0, later
    assert "warning" not in later.out


def test_home_paths_finds_both_shapes_and_ignores_system_paths() -> None:
    from kyosei.prose import home_paths

    assert home_paths("at /Users/jane/proj/x.py:12") == ["/Users/jane/proj/x.py:12"]
    assert home_paths("at /home/jane/proj") == ["/home/jane/proj"]
    assert home_paths(r"at C:\Users\jane\proj") == [r"C:\Users\jane\proj"]
    assert home_paths("/usr/local/bin/kyo and /tmp/x and src/kyosei/prose.py") == []
    # Deduplicated: one path named three times is one warning, not three.
    assert home_paths("/Users/j/a /Users/j/a /Users/j/a") == ["/Users/j/a"]


# ------------------------------------------------------------------------- KYO-91
#
# The KYO-26 check refused an *odd* number of backticks or quotes and called that
# "the signature of a substitution that swallowed the text between them". It is not.
# A shell doing command substitution removes BOTH delimiters and everything between
# them; the argument that arrives holds zero backticks, zero is even, and the check
# passed it. The controller reproduced it on main and the store took
# `dead because  already handles it` without a word.
#
# What replaced it is not a better guess at the same thing — there is no better guess.
# A substitution whose command printed output is invisible from inside this process
# and always will be. What is detectable is the residue left when it printed nothing:
# the gap where the words used to be. That gap is also a gap people sometimes type,
# so these tests pin the false positive as deliberately as they pin the catch.

#: The reproduction attached to KYO-91, as it arrives after the shell has run
#: `kyo rebuild` and put its (empty) output in place of the backticked command.
#: Written here as the *post-substitution* string on purpose: a test that spelled the
#: backticks would be testing pytest's own quoting, not the tool's behaviour.
MANGLED = "dead because  already handles it"

#: The same sentence with nothing eaten out of it.
INTACT = "dead because kyo rebuild already handles it"


def _gate(kyo, record, value, **_):
    return kyo("gate", record, "awaiting:human-run", "--do", value)


def _title(kyo, record, value, **_):
    return kyo("edit", record, "--title", value)


def _why(kyo, record, value, **_):
    return kyo("wont-do", record, "--why", value)


def _method(kyo, record, value, *, write_file):
    return kyo(
        "claim", record, "--level", "asserted", "--method", value,
        "--file", write_file("claim.md", "the widget is fixed\n"),
    )


def _quote(kyo, record, value, **_):
    # KYO-494: named and --no-close, so an intact quote verifies the criterion and stops.
    return kyo("confirm", record, "--criterion", f"{record}.c1", "--no-close", "--quote", value)


def _enforced_by(kyo, record, value, *, write_file):
    # KYO-565: `kyo rules add`'s one-line mechanism; the title is unique per record so
    # the duplicate-title refusal never answers in the residue check's place.
    return kyo(
        "rules", "add", "--title", f"Rule for {record}", "--audience", "worker",
        "--enforced-by", value, "--reason-file", write_file("reason.md", "A reason.\n"),
    )


#: flag -> a way to actually give that flag a value on a real command line.
#:
#: KYO-91.c2. Not a second list of flags beside `cli.prose_argument_flags()`: the test
#: below asserts these keys ARE that function's output, so a fifth prose-argument flag
#: makes this file fail until someone writes down how to drive it and what it then
#: does. That is the property a list maintained by hand cannot have, and the property
#: whose absence let `--do`, `--title` and `--method` ship with no check at all.
RESIDUE_CASES = {
    "--do": _gate,
    "--enforced-by": _enforced_by,
    "--title": _title,
    "--why": _why,
    "--method": _method,
    "--quote": _quote,
}


def test_every_prose_argument_flag_is_covered_by_a_residue_case() -> None:
    """KYO-91.c2. The set is read off the shipped parser, never typed here."""
    assert set(RESIDUE_CASES) == set(prose_argument_flags())


def test_the_coverage_comparison_is_not_vacuous() -> None:
    """The canary c2 rests on. A comparison whose derived side is empty passes forever
    and says nothing — so prove both sides are populated, and prove that a parser
    carrying one more prose flag would fail the test above. Same shape as
    `tests/test_procedures.py::test_the_comparison_is_not_vacuous`, which guards the
    other half of the same declaration."""
    assert prose_argument_flags()
    assert len(RESIDUE_CASES) > 1, "c4 needs at least two flags driven for real"

    parser = build_parser()
    subcommands = next(a for a in parser._actions if isinstance(a.choices, dict)).choices
    subcommands["wont-do"].add_argument("--reason", metavar=PROSE_ARGUMENT_METAVAR)

    widened = set(prose_argument_flags(parser))
    assert "--reason" in widened
    assert set(RESIDUE_CASES) != widened, "a fifth prose flag must fail the test above"


@pytest.mark.parametrize("flag", sorted(RESIDUE_CASES))
def test_a_value_the_shell_ate_a_word_out_of_is_never_stored_silently(
    kyo, write_file, flag: str
) -> None:
    """KYO-91.c1 and c4, for every flag rather than for `--why` alone.

    Both outcomes c1 allows are accepted here, because they are the right outcome for
    different flags: a refusal naming the file form for a flag that has one, and a
    loud warning for a flag that does not (a refused `--title` would make `kyo new`
    impossible, and there is no `--title-file`). What is not accepted, and what this
    test fails on, is silence.
    """
    record = new_record(kyo, criteria=["a criterion"])
    result = RESIDUE_CASES[flag](kyo, record, MANGLED, write_file=write_file)

    said = result.err if result.code != 0 else result.out
    assert flag in said, result
    assert "the shell removed part of it" in said, result
    if result.code == 0:
        assert "warning" in said, result
    else:
        assert f"{flag}-file" in said, "a refusal must name a way to succeed"


@pytest.mark.parametrize("flag", sorted(RESIDUE_CASES))
def test_the_same_sentence_with_nothing_eaten_out_of_it_passes_quietly(
    kyo, write_file, flag: str
) -> None:
    """The other half of every check in this file. One that fires on ordinary input is
    one people learn to route around — so the sentence the operator MEANT to write must
    go through with no refusal and no warning."""
    record = new_record(kyo, criteria=["a criterion"])
    result = RESIDUE_CASES[flag](kyo, record, INTACT, write_file=write_file)
    assert result.code == 0, result
    assert "warning" not in result.out, result


def test_the_reproduction_on_the_record_is_refused_and_nothing_is_stored(kyo) -> None:
    """KYO-91.c1, on the exact command the record was filed with. Before this, the
    store took it and said `stored why (32 chars, ...)`."""
    record = new_record(kyo, criteria=["a criterion"])
    result = kyo("wont-do", record, "--why", MANGLED)
    assert result.code == 64, result
    assert "--why-file" in result.err
    assert repr(MANGLED) in result.err, "the caller is shown what actually arrived"

    shown = kyo("show", record, "--json").json
    assert shown["status"] == "open"
    assert shown["wont_do_reason"] is None


def test_the_file_route_the_refusal_names_accepts_the_very_same_text(kyo, write_file) -> None:
    """Every error message naming a remedy owes a test that the remedy clears it. The
    doubled space may genuinely be what someone meant; `--why-file` is how they say so,
    and it stores the text unchanged rather than tidying it."""
    record = new_record(kyo, criteria=["a criterion"])
    assert kyo("wont-do", record, "--why", MANGLED).code == 64
    result = kyo("wont-do", record, "--why-file", write_file("why.md", MANGLED + "\n"))
    assert result.code == 0, result
    assert kyo("show", record, "--json").json["wont_do_reason"] == MANGLED


def test_a_warned_flag_still_stores_the_value_exactly_as_given(kyo) -> None:
    """The warning branch is a warning: the write happened, the exit code is 0, and the
    bytes in the store are the bytes that arrived — including the gap, which is the
    evidence. Tidying it away would destroy the only trace of what went wrong."""
    record = new_record(kyo, criteria=["a criterion"])
    result = kyo("edit", record, "--title", MANGLED)
    assert result.code == 0, result
    assert kyo("show", record, "--json").json["title"] == MANGLED


def test_the_residue_warning_is_carried_in_the_json_document_too(kyo) -> None:
    """`--json` callers are the ones least able to read a line of prose off stdout, and
    the most likely to be a controller storing a title on someone's behalf."""
    record = new_record(kyo, criteria=["a criterion"])
    result = kyo("edit", record, "--title", MANGLED, "--json")
    assert result.code == 0, result
    assert len(result.json["shell_residue_warnings"]) == 1
    assert "--title" in result.json["shell_residue_warnings"][0]


def test_the_residue_warning_does_not_leak_into_the_next_invocation(kyo) -> None:
    """Per-invocation, like the KYO-52 path collector. A warning that repeats itself on
    an unrelated command is noise, and noise is how a check stops being read."""
    record = new_record(kyo, criteria=["a criterion"])
    assert kyo("edit", record, "--title", MANGLED).code == 0
    later = kyo("show", record)
    assert later.code == 0, later
    assert "warning" not in later.out


def test_the_check_runs_before_the_store_is_touched(kyo) -> None:
    """A refusal after a partial write would be worse than no refusal: `kyo gate` and
    `kyo wont-do` both mutate, and the check is the reason neither of them got to."""
    record = new_record(kyo, criteria=["a criterion"])
    before = kyo("show", record, "--json").json
    assert kyo("wont-do", record, "--why", MANGLED).code == 64
    assert kyo("show", record, "--json").json == before


# ---------------------------------------------------- the detection, stated plainly


def test_the_findings_are_the_residue_and_not_the_substitution() -> None:
    """What `shell_residue` sees, and — the whole of KYO-91 — what it cannot.

    The last two cases are the point of the record: a substitution that printed
    something leaves an argument indistinguishable from prose, and this function
    correctly reports nothing, because there is nothing to report.
    """
    assert shell_residue("dead because  already handles it")  # the reproduction
    assert shell_residue(" leading") and shell_residue("trailing ")
    assert shell_residue("half a `quotation")
    assert shell_residue('half a "quotation')

    assert shell_residue("an ordinary single-spaced sentence") == []
    assert shell_residue("superseded by `kyo split`, per the \"new\" plan") == []
    assert shell_residue("the premise died and this wasn't worth pursuing") == []
    # A substitution whose command printed output. Nothing here can see this, ever.
    assert shell_residue("dead because 2026-08-26 already handles it") == []


def test_the_false_positive_is_real_and_is_not_pretended_away() -> None:
    """The honest cost, pinned rather than described. A doubled space someone typed on
    purpose is refused exactly like a doubled space a substitution left, because the
    two are the same string. The remedy in the message is what makes that survivable,
    and `test_the_file_route_the_refusal_names_accepts_the_very_same_text` proves it
    works."""
    innocent = "one sentence.  And the next, typewriter-style."
    assert shell_residue(innocent) == [
        # The offset is read off the string, not typed: a hand-counted one in a test
        # is the same class of fact as a hand-counted one in prose (this repo's
        # standing rule about tool-derivable facts), and it was wrong the first time.
        f"a 2-character gap at offset {innocent.index('  ')}, wide enough to have held a word"
    ]


def test_the_marked_form_makes_the_gap_visible() -> None:
    """Two spaces and one space are the same picture in a terminal, and the entire
    finding is about which one it is — so the message shows the difference rather than
    asserting it."""
    assert marked_residue(MANGLED) == "dead because··already handles it"
    assert marked_residue(INTACT) == INTACT
    assert marked_residue(" edged ") == "·edged·"


def test_the_docstring_no_longer_claims_an_odd_count_is_the_signature() -> None:
    """KYO-91.c3, as a test rather than as a promise. The sentence is what let a reader
    auditing law 5's enforcement find a mechanism and stop looking.

    Matched as a phrase (`kyosei.prose.contains_phrase`) because a docstring is
    hard-wrapped and a fixed-string `in` would answer a narrower question than the one
    being asked — it would pass the moment the claim wrapped onto a second line.
    """
    doc = check_not_shell_mangled.__doc__ or ""
    assert not contains_phrase(doc, "which is the signature of a substitution")
    assert not contains_phrase(doc, "an odd number of backticks or quotes means one of a pair")
    # And the half c3 asks to be added: what the check cannot see.
    assert contains_phrase(doc, "it cannot *see* one either")
    assert contains_phrase(doc, "takes its own delimiters with it")
