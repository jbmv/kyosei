"""KYO-13.c4 / KYO-12: "a structured report can carry criteria verdicts for
every record its dispatch formally covers, or dispatch refuses to informally
bundle records it cannot report on." This build's design call: formal
multi-record dispatch — `kyo dispatch <id> --also-covers ID [ID ...]` freezes
the covered records' criteria into the brief, and `kyo report` accepts
`criteria_verdicts` naming a criterion on the primary record OR any covered
one, and refuses (as before) anything else.
"""

from __future__ import annotations

import json
from pathlib import Path

from kyosei.errors import EXIT_CONTRACT

from .conftest import export_of, git_worktree, new_record, valid_report


def test_dispatch_also_covers_records_the_covered_ids(kyo, git_project: Path) -> None:
    primary = new_record(kyo, criteria=["primary criterion"])
    covered = new_record(kyo, criteria=["covered criterion"])

    result = kyo("dispatch", primary, "--also-covers", covered, "--json")
    assert result.code == 0, result
    assert result.json["also_covers"] == [covered]

    doc = json.loads(export_of(git_project, primary).read_text(encoding="utf-8"))
    dispatch = doc["dispatches"][0]
    assert dispatch["covers"] == [covered]


def test_dispatch_also_covers_appends_covered_criteria_to_the_brief(kyo, git_project: Path) -> None:
    primary = new_record(kyo, criteria=["primary criterion"])
    covered = new_record(kyo, criteria=["a very distinctive covered criterion text"])

    result = kyo("--full", "dispatch", primary, "--also-covers", covered, "--json")
    assert result.code == 0
    brief = result.json["record"]["dispatches"][0]["brief"]
    assert "Also formally covers" in brief
    assert covered in brief
    assert "a very distinctive covered criterion text" in brief


def test_dispatch_also_covers_refuses_the_primary_record_itself(kyo, git_project: Path) -> None:
    primary = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", primary, "--also-covers", primary)
    assert result.code == 64
    assert "already this dispatch's own record" in result.err


def test_dispatch_also_covers_refuses_an_unknown_record(kyo, git_project: Path) -> None:
    primary = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", primary, "--also-covers", "TST-404")
    assert result.code == 1
    assert "TST-404" in result.err


def test_report_can_carry_verdicts_for_a_covered_record(kyo, git_project: Path) -> None:
    primary = new_record(kyo, criteria=["primary criterion"])
    covered = new_record(kyo, criteria=["covered criterion"])
    dispatch = kyo("dispatch", primary, "--also-covers", covered, "--json")
    dispatch_id = dispatch.json["dispatch_id"]
    sha = dispatch.json["worked_from_sha"]

    covered_criterion_id = f"{covered}.c1"
    report = valid_report(
        dispatch_id=dispatch_id,
        record_id=primary,
        worked_from_sha=sha,
        criteria_verdicts=[
            {
                "criterion_id": f"{primary}.c1",
                "verdict": "met",
                "method": "reasoned-about",
            },
            {
                "criterion_id": covered_criterion_id,
                "verdict": "met",
                "method": "reasoned-about",
            },
        ],
    )
    report_path = git_project / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = kyo("report", dispatch_id, "--file", str(report_path), "--json")
    assert result.code == 0, result
    verdict_ids = {cv["criterion_id"] for cv in result.json["criteria_verdicts"]}
    assert covered_criterion_id in verdict_ids
    assert result.json["covers"] == [covered]


def test_report_refuses_a_record_the_dispatch_did_not_declare_covering(
    kyo, git_project: Path
) -> None:
    """The KYO-12 regression check: without --also-covers, bundling another
    record's criterion into a report must still be refused exactly as before."""
    primary = new_record(kyo, criteria=["primary criterion"])
    bystander = new_record(kyo, criteria=["unrelated criterion"])
    dispatch = kyo("dispatch", primary, "--json")
    dispatch_id = dispatch.json["dispatch_id"]
    sha = dispatch.json["worked_from_sha"]

    report = valid_report(
        dispatch_id=dispatch_id,
        record_id=primary,
        worked_from_sha=sha,
        criteria_verdicts=[
            {"criterion_id": f"{bystander}.c1", "verdict": "met", "method": "reasoned-about"}
        ],
    )
    report_path = git_project / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = kyo("report", dispatch_id, "--file", str(report_path))
    assert result.code == 1
    assert bystander in result.err


# ------------------------------------------------------------------------- KYO-65
#
# The second hole in the same computation KYO-48 fixed. `--also-covers` is a
# formal declaration that a dispatch is doing another record's work too, and the
# disjointness check read only the PRIMARY record's touches — so the covered
# record's files were claimed by nobody as far as the check could see, and a
# second dispatch into exactly those files was permitted with no refusal.
#
# The live instance is on this board: KYO-43.d1 covered KYO-44. KYO-43's touches
# named `cmd_new`-adjacent files; KYO-44's named `render.py`, `html_site.py` and
# `brief.py`. Those three were edited for the whole run while the tool believed
# they were unclaimed. Nothing collided — that was luck, not the check working.


def _scoped(kyo, project: Path, write_file, name: str, touches: str) -> str:
    """A dispatchable chore with a declared scope. `kyo promote` requires
    `--touches-file` for a work archetype, so this is the door a scoped record
    comes through; an UNSCOPED one can only come from `kyo new` (`new_record`).

    The touched files are created first because `kyo promote` validates that a
    touch names something real (KYO-3)."""
    for line in touches.splitlines():
        path_part = line.partition("#")[0].strip()
        if path_part and not (project / path_part).exists():
            (project / path_part).parent.mkdir(parents=True, exist_ok=True)
            (project / path_part).write_text("placeholder\n", encoding="utf-8")
    idea = project / "ideas" / f"{name}.md"
    idea.write_text(f"# {name}\n", encoding="utf-8")
    result = kyo(
        "promote",
        str(idea),
        "--kind",
        "chore",
        "--criteria-file",
        write_file(f"{name}-criteria.txt", "done\n"),
        "--touches-file",
        write_file(f"{name}-touches.txt", touches),
        "--json",
    )
    assert result.code == 0, result
    return result.json["record"]["id"]


def _elsewhere(tmp_path: Path, name: str) -> list[str]:
    """Its own worktree — otherwise the *worktree* collision refusal fires and a
    test that means to exercise touch disjointness proves nothing about it."""
    # KYO-402: a real linked worktree of the project fixture (``tmp_path / "proj"``);
    # `kyo dispatch --worktree` refuses a bare directory.
    return ["--worktree", git_worktree(tmp_path / "proj", tmp_path / f"wt-{name}")]


def test_a_covered_records_touches_are_claimed_by_the_dispatch_covering_it(
    kyo, git_project: Path, tmp_path: Path, write_file
) -> None:
    """KYO-65.c1/c4, the live instance reproduced: the covered record names a file
    the primary's touches do not, and a second dispatch into that file must be
    refused. Against the pre-fix build this dispatch is permitted — which is
    exactly how KYO-43.d1 and KYO-44 ran."""
    primary = _scoped(kyo, git_project, write_file, "primary", "src/kyosei/cmd_new.py\n")
    covered = _scoped(kyo, git_project, write_file, "covered", "src/kyosei/render.py\n")
    bystander = _scoped(kyo, git_project, write_file, "bystander", "src/kyosei/render.py\n")

    first = kyo("dispatch", primary, "--also-covers", covered, "--json")
    assert first.code == 0, first

    refused = kyo("dispatch", bystander, *_elsewhere(tmp_path, "bystander"))
    assert refused.code == EXIT_CONTRACT, (
        "the covered record's files are claimed by the dispatch covering it"
    )
    assert "dispatch-touch-disjointness" in refused.err
    assert "src/kyosei/render.py" in refused.err


def test_the_refusal_names_the_covered_record_not_only_the_dispatch(
    kyo, git_project: Path, tmp_path: Path, write_file
) -> None:
    """KYO-65.c2: a controller who sees only the dispatch id will open the PRIMARY
    record, find no overlap in its touches, and conclude the tool is wrong. The
    path belongs to the covered record, so the covered record is named."""
    primary = _scoped(kyo, git_project, write_file, "primary", "src/kyosei/cmd_new.py\n")
    covered = _scoped(kyo, git_project, write_file, "covered", "src/kyosei/render.py\n")
    bystander = _scoped(kyo, git_project, write_file, "bystander", "src/kyosei/render.py\n")
    first = kyo("dispatch", primary, "--also-covers", covered, "--json")

    refused = kyo("dispatch", bystander, *_elsewhere(tmp_path, "bystander"))
    assert covered in refused.err, "the record that actually declares the path is unnamed"
    assert first.json["dispatch_id"] in refused.err
    assert "primary record's own touches" in refused.err

    forced = kyo("dispatch", bystander, *_elsewhere(tmp_path, "bystander"), "--force", "--json")
    assert forced.code == 0, forced
    overridden = forced.json["collisions_overridden"]
    assert overridden and overridden[0]["via_running"] == [covered], (
        "what was overridden must record which covered record owned the overlap"
    )
    assert covered in forced.out, "forcing past it must still say which record it was"


def test_a_dispatchs_own_also_covers_is_part_of_what_it_claims(
    kyo, git_project: Path, tmp_path: Path, write_file
) -> None:
    """KYO-65.c1, the other direction: the record being dispatched is disjoint, and
    the record it is about to cover is not. The claim is the union, so this is a
    collision — and the sentence says which side it came from."""
    running = _scoped(kyo, git_project, write_file, "running", "src/kyosei/render.py\n")
    primary = _scoped(kyo, git_project, write_file, "primary", "src/kyosei/cmd_new.py\n")
    covered = _scoped(kyo, git_project, write_file, "covered", "src/kyosei/render.py\n")
    assert kyo("dispatch", running, "--json").code == 0

    refused = kyo("dispatch", primary, "--also-covers", covered, *_elsewhere(tmp_path, "p"))
    assert refused.code == EXIT_CONTRACT, refused
    assert covered in refused.err
    assert "also covered by this dispatch" in refused.err

    # Nothing was written by the refusal, so the same dispatch WITHOUT the covers
    # declaration is the control: the delta is caused by `--also-covers` alone.
    alone = kyo("dispatch", primary, *_elsewhere(tmp_path, "p"), "--json")
    assert alone.code == 0, "the primary record really is disjoint on its own"
    assert alone.json["collisions_overridden"] == []


def test_a_covered_record_with_no_touches_makes_the_whole_claim_unknown(
    kyo, git_project: Path, tmp_path: Path, write_file
) -> None:
    """KYO-65.c3: KYO-48's third outcome propagates through the union. A union
    that treated a missing set as an empty one would be the original bug in a new
    place — a scoped primary papering over an unscoped covered record."""
    primary = _scoped(kyo, git_project, write_file, "primary", "src/kyosei/cmd_new.py\n")
    unscoped = new_record(kyo, kind="chore", title="Covered, unscoped", criteria=["done"])
    candidate = _scoped(kyo, git_project, write_file, "candidate", "src/kyosei/render.py\n")

    first = kyo("dispatch", primary, "--also-covers", unscoped, "--json")
    assert first.code == 0, "nothing was running, so nothing was unknowable yet"

    refused = kyo("dispatch", candidate, *_elsewhere(tmp_path, "candidate"))
    assert refused.code == EXIT_CONTRACT, (
        "the running dispatch's claim includes a record that declares nothing"
    )
    assert "dispatch-scope-unknown" in refused.err
    assert unscoped in refused.err, "the unscoped record in the claim must be named"
    assert "--touches-file" in refused.err

    (git_project / "src" / "kyosei" / "prose.py").write_text("x\n", encoding="utf-8")
    assert (
        kyo(
            "edit", unscoped, "--touches-file", write_file("late.txt", "src/kyosei/prose.py\n")
        ).code
        == 0
    )
    cleared = kyo("dispatch", candidate, *_elsewhere(tmp_path, "candidate"), "--json")
    assert cleared.code == 0, "the remedy the refusal names must clear it"


def test_covering_an_unscoped_record_makes_this_dispatchs_own_claim_unknown(
    kyo, git_project: Path, tmp_path: Path, write_file
) -> None:
    """KYO-65.c3, candidate side: `--also-covers` an unscoped record and this
    dispatch can no longer say what it touches either."""
    running = _scoped(kyo, git_project, write_file, "running", "src/kyosei/render.py\n")
    primary = _scoped(kyo, git_project, write_file, "primary", "src/kyosei/cmd_new.py\n")
    unscoped = new_record(kyo, kind="chore", title="Covered, unscoped", criteria=["done"])
    assert kyo("dispatch", running, "--json").code == 0

    refused = kyo("dispatch", primary, "--also-covers", unscoped, *_elsewhere(tmp_path, "p"))
    assert refused.code == EXIT_CONTRACT, refused
    assert "dispatch-scope-unknown" in refused.err
    assert unscoped in refused.err


def test_a_covered_records_touches_are_re_read_not_frozen_at_dispatch_time(
    kyo, git_project: Path, tmp_path: Path, write_file
) -> None:
    """KYO-65.c1's stored-vs-recomputed decision, pinned. This controller corrected
    a covered record's touches four separate times in one session, each after the
    dispatch covering it had already started. A union computed once and stored
    would still be answering with the pre-correction scope."""
    primary = _scoped(kyo, git_project, write_file, "primary", "src/kyosei/cmd_new.py\n")
    covered = _scoped(kyo, git_project, write_file, "covered", "src/kyosei/kinds.py\n")
    bystander = _scoped(kyo, git_project, write_file, "bystander", "src/kyosei/render.py\n")
    assert kyo("dispatch", primary, "--also-covers", covered, "--json").code == 0

    allowed = kyo("dispatch", bystander, *_elsewhere(tmp_path, "b1"), "--json")
    assert allowed.code == 0, "nothing in the claim named render.py yet"
    kyo(
        "dispatch",
        "close",
        allowed.json["dispatch_id"],
        "--terminated-no-report",
        "--why-file",
        write_file("why2.txt", "undoing the probe\n"),
    )

    assert (
        kyo(
            "edit", covered, "--touches-file", write_file("corrected.txt", "src/kyosei/render.py\n")
        ).code
        == 0
    )
    refused = kyo("dispatch", bystander, *_elsewhere(tmp_path, "b2"))
    assert refused.code == EXIT_CONTRACT, (
        "correcting a covered record's scope mid-flight must change the answer"
    )
    assert covered in refused.err


def test_doctor_catches_a_covers_json_naming_a_missing_record(kyo, db, git_project: Path) -> None:
    primary = new_record(kyo, criteria=["a"])
    covered = new_record(kyo, criteria=["b"])
    kyo("dispatch", primary, "--also-covers", covered)

    conn = db()
    conn.execute(
        "UPDATE dispatches SET covers_json = ? WHERE record_id = ?",
        (json.dumps(["TST-999"]), primary),
    )
    conn.commit()
    conn.close()

    result = kyo("doctor")
    assert result.code == 3
    assert "TST-999" in result.err


# ------------------------------------------------------------------------- KYO-76
#
# The third hole in the same computation, and the first one on the *other* side of
# the dispatch. KYO-48 fixed the check's inputs; KYO-65 fixed them again for
# `--also-covers`. Both are about what the tool compares. This is about what the
# WORKER IS TOLD: the brief's Touches section rendered `record["touches"]` — the
# primary record's touches alone — while `commands._claim_of` computed the union.
#
# Same input, two consumers, one of them never updated. The write-scope
# section states the boundary as "within the files/symbols named in Touches", so a
# worker on a two-record dispatch was handed the criteria of both records and a
# capability boundary that excluded the files its own criteria required.
#
# The live instance: KYO-73.d1 covered KYO-74. The brief's Touches named KYO-73's
# four files; all four of KYO-74's criteria lived outside them. That worker filed
# `premise_false` and proceeded — correctly, and only because the standing stance
# makes contradicting the brief cheaper than complying with it. A worker that
# complied would have delivered nothing and reported the dispatch impossible.


def _brief_of(result) -> str:
    """The frozen brief off a `kyo dispatch --json --full` run: the brief lives in the
    whole record document, which a plain `--json` no longer embeds (KYO-427)."""
    return result.json["record"]["dispatches"][0]["brief"]


def _with_symbol(project: Path, relative: str, symbol: str) -> None:
    """`kyo promote` validates that a `path#symbol` touch names something real
    (KYO-3), and `_scoped`'s placeholder file contains no symbols. Written before
    `_scoped` runs, which only creates a touched file that does not already exist."""
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"def {symbol.rpartition('.')[2]}():\n    pass\n", encoding="utf-8")


def test_the_brief_states_the_union_of_the_touches_it_covers_not_the_primarys(
    kyo, git_project: Path, write_file
) -> None:
    """KYO-76.c1/c3, the live instance reproduced. The covered record names a file
    the primary's touches do not; the brief must name it, and must say which record
    put it there.

    Against the pre-fix build the covered path is simply absent from the brief —
    which is how KYO-73.d1 was told its boundary excluded `currency.py`."""
    primary = _scoped(kyo, git_project, write_file, "primary", "src/kyosei/cmd_new.py\n")
    covered = _scoped(kyo, git_project, write_file, "covered", "src/kyosei/currency.py\n")

    result = kyo("dispatch", primary, "--also-covers", covered, "--json", "--full")
    assert result.code == 0, result
    brief = _brief_of(result)

    touches = brief.split("## Touches", 1)[1].split("## Cited claims", 1)[0]
    assert "src/kyosei/cmd_new.py" in touches, touches
    assert "src/kyosei/currency.py" in touches, touches

    # KYO-76.c1's second half: attribution. A worker that cannot tell which record
    # put a path in scope cannot tell a deliberate claim from controller drift.
    assert f"| {primary} (this dispatch's own record) | src/kyosei/cmd_new.py |" in touches
    assert f"| {covered} (--also-covers) | src/kyosei/currency.py |" in touches


def test_the_brief_names_a_symbol_scoped_touch_under_the_record_that_declared_it(
    kyo, git_project: Path, write_file
) -> None:
    """A `path#symbol` touch keeps its symbol. The claim is file-level by design
    (`commands._touch_files`), so the path column is the boundary and the symbol is
    detail hung off it — the symbol must survive the change that made the path column
    authoritative, or the brief lost information it used to carry."""
    primary = _scoped(kyo, git_project, write_file, "primary", "src/kyosei/cmd_new.py\n")
    _with_symbol(git_project, "src/kyosei/currency.py", "db_export_currency")
    covered = _scoped(
        kyo, git_project, write_file, "covered", "src/kyosei/currency.py#db_export_currency\n"
    )

    brief = _brief_of(kyo("dispatch", primary, "--also-covers", covered, "--json", "--full"))
    assert (
        # KYO-327: the symbol cell also carries the line it was found at when frozen.
        f"| {covered} (--also-covers) | src/kyosei/currency.py | db_export_currency (line " in brief
    ), brief


def test_the_brief_and_the_disjointness_check_name_the_same_paths(
    kyo, git_project: Path, write_file, tmp_path: Path
) -> None:
    """KYO-76.c2. The boundary the worker is told and the set `kyo dispatch` compared
    against every other running dispatch are read back out of the two places they
    actually live — the frozen brief text, and `commands._claim_of` — and must be
    equal.

    This is the assertion that would have failed on KYO-73.d1's brief, and the reason
    the claim is passed into `generate_brief` rather than recomputed there: two
    derivations agree until one of them is edited."""
    from kyosei import brief as brief_module
    from kyosei import commands
    from kyosei.store import open_store

    primary = _scoped(
        kyo,
        git_project,
        write_file,
        "primary",
        "src/kyosei/cmd_new.py\nsrc/kyosei/commands.py\n",
    )
    _with_symbol(git_project, "src/kyosei/store.py", "open_store")
    covered = _scoped(
        kyo,
        git_project,
        write_file,
        "covered",
        "src/kyosei/currency.py\nsrc/kyosei/store.py#open_store\n",
    )

    result = kyo("dispatch", primary, "--also-covers", covered, "--json", "--full")
    assert result.code == 0, result

    store = open_store(git_project)
    try:
        claim = commands._claim_of(store, primary, [covered])
    finally:
        store.close()

    stated = brief_module.touch_paths_named_in(_brief_of(result))
    assert stated == claim.files, (sorted(stated), sorted(claim.files))
    # Not vacuous in either direction: an empty brief table and an empty claim would
    # also be equal.
    assert len(stated) == 4, sorted(stated)


def test_a_covered_record_that_declares_nothing_is_said_so_in_the_brief(
    kyo, git_project: Path, write_file, tmp_path: Path
) -> None:
    """KYO-48's distinction, carried onto the statement side: an undeclared scope is
    unknown, not empty. A brief that silently rendered nothing for such a record would
    tell the worker its boundary was the primary record's files — a confident answer to
    a question nobody could answer (law 6)."""
    primary = _scoped(kyo, git_project, write_file, "primary", "src/kyosei/cmd_new.py\n")
    unscoped = new_record(kyo, kind="chore", title="Covered, unscoped", criteria=["done"])

    # --force: an unscoped covered record makes the whole claim `scope-unknown`, which
    # is a refusal in its own right (KYO-65). Overriding it is what puts a worker in
    # front of exactly this brief.
    result = kyo("dispatch", primary, "--also-covers", unscoped, "--force", "--json", "--full")
    assert result.code == 0, result
    touches = _brief_of(result).split("## Touches", 1)[1].split("## Cited claims", 1)[0]
    assert f"{unscoped} declare(s) no touches" in touches, touches


def test_a_single_record_dispatch_still_states_its_own_touches(
    kyo, git_project: Path, write_file
) -> None:
    """The ordinary case is the majority of dispatches and must not have grown a
    two-record framing it does not need."""
    solo = _scoped(kyo, git_project, write_file, "solo", "src/kyosei/cmd_new.py\n")
    brief = _brief_of(kyo("dispatch", solo, "--json", "--full"))
    touches = brief.split("## Touches", 1)[1].split("## Cited claims", 1)[0]
    assert f"| {solo} (this dispatch's own record) | src/kyosei/cmd_new.py |" in touches
    assert "This dispatch covers" not in touches, touches
    assert "--also-covers" not in touches, touches


def test_a_repeated_also_covers_flag_covers_every_occurrence(kyo, git_project: Path) -> None:
    """KYO-476.c1 + c2, from a consumer project's report: argparse's default `store`
    action kept only the last `--also-covers`. Repeating the flag means the same as one
    flag with every id, in order, and an id named in two occurrences is covered once."""
    primary = new_record(kyo, criteria=["primary"])
    first = new_record(kyo, criteria=["first"])
    second = new_record(kyo, criteria=["second"])

    repeated = kyo(
        "dispatch", primary, "--also-covers", first, "--also-covers", second, first,
        "--json",
    )
    assert repeated.code == 0, repeated
    assert repeated.json["also_covers"] == [first, second]
    doc = json.loads(export_of(git_project, primary).read_text(encoding="utf-8"))
    assert doc["dispatches"][0]["covers"] == [first, second]


def test_the_own_record_refusal_fires_from_any_occurrence(kyo, git_project: Path) -> None:
    """KYO-476.c2: the refusal for naming the dispatch's own record is not lost when the
    own id sits in an earlier occurrence that `store` would have overwritten."""
    primary = new_record(kyo, criteria=["a"])
    covered = new_record(kyo, criteria=["b"])
    refused = kyo("dispatch", primary, "--also-covers", primary, "--also-covers", covered)
    assert refused.code == 64, refused
    assert "already this dispatch's own record" in refused.err


def test_dispatch_help_says_also_covers_may_be_repeated(kyo, git_project: Path) -> None:
    """KYO-476.c3: the help (and so `kyo reference dispatch`) says the flag repeats."""
    import contextlib
    import io

    from kyosei.cli import build_parser

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.suppress(SystemExit):
        build_parser().parse_args(["dispatch", "--help"])
    help_text = " ".join(buffer.getvalue().split())
    assert "May be repeated" in help_text
    reference = kyo("reference", "dispatch")
    assert "May be repeated" in " ".join(reference.out.split()), reference
