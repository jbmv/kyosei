"""What the disjointness check is worth, and what a declared file drags in.

KYO-147, from a consumer report filed explicitly as data rather than as a
complaint: that consumer ran eight dispatches and all eight reported touch drift.
Not one declared set survived contact with the work. The consequence is the part
that matters — **disjointness is computed BEFORE the work, from the set that is
about to be wrong** — and twice in that run workers checked
disjointness by hand against the other running dispatch's declared touches, correctly,
and only because the standing stance told them to flag rather than proceed. The
mechanism did not establish what it appeared to establish; the workers did.

Two mechanisms here, aimed at two different moments:

* **c1**, the cheap one: say what was compared. `kyo dispatch` refuses on
  ``TOUCH_COLLIDES`` and ``TOUCH_SCOPE_UNKNOWN`` and used to say nothing at all about
  the sets it compared, so silence read as "no collision" when what was established
  was a verdict about two predictions. KYO-48 is the precedent — it exists because an
  undeclared scope used to pass silently, so the tool "reported 'not colliding' about
  a comparison it never made" — and a stale declaration is the same defect one degree
  weaker.
* **c2/c3**, the load-bearing one: the store has held every report's
  ``touched_files[]`` since the first dispatch and nothing ever read it. KYO-147.m2
  says why that is where the answer is: the files a change drags in are reachable
  from the type graph and the call graph (a generated mirror, a positionally-keyed
  index, a byte-identical twin), and a controller authoring touches from an idea file
  is searching neither. c3 is the law-6 half — below the thresholds, silence, never a
  weak signal with a percentage attached to make it look like a finding.

Each test here fails if its mechanism is removed; the deletions were run and watched
go red, and the report for KYO-147.d1 names which deletion each one is.
"""

from __future__ import annotations

import json
from pathlib import Path

from kyosei import commands, explanations
from kyosei.errors import EXIT_CONTRACT

from .conftest import git_worktree, new_record, valid_report


def _promote(kyo, project: Path, tmp_path: Path, name: str, touches: list[str]) -> str:
    """A promoted record declaring ``touches``, by the door `kyo promote` uses."""
    for line in touches:
        # `kyo promote` validates every touch against the working tree (charter,
        # Layer 1), so a fixture that declares a path has to create it first.
        target = project / line.split("#", 1)[0]
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("placeholder\n", encoding="utf-8")
    idea = project / "ideas" / f"{name}.md"
    idea.write_text(f"# {name}\n\nDoes a thing.\n", encoding="utf-8")
    criteria = tmp_path / f"{name}-criteria.txt"
    criteria.write_text("done\n", encoding="utf-8")
    touch_file = tmp_path / f"{name}-touches.txt"
    touch_file.write_text("".join(f"{line}\n" for line in touches), encoding="utf-8")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", str(criteria), "--touches-file", str(touch_file), "--json",
    )
    assert result.code == 0, result
    return result.json["record"]["id"]


def _worktree(tmp_path: Path, name: str) -> str:
    # KYO-402: a real linked worktree of the project fixture (``tmp_path / "proj"``);
    # `kyo dispatch --worktree` refuses a bare directory.
    return git_worktree(tmp_path / "proj", tmp_path / name)


# ------------------------------------------------- c1: say what the check was worth


def test_a_lone_dispatch_is_told_it_was_unconstrained_not_cleared(kyo, git_project) -> None:
    """The case where silence is most misleading. With nothing else running, nothing
    was compared — so the honest statement is "unconstrained by the check", not the
    "no collision" a controller reads out of a passing command that says nothing."""
    record = new_record(kyo, criteria=["it works"])
    # Not `--json`: the assertion is about what the controller is TOLD, and `--json`
    # prints the document instead of the lines.
    result = kyo("dispatch", record)

    assert result.code == 0, result
    assert "DECLARED touches" in result.out
    assert "nothing was compared" in result.out
    assert "unconstrained by the check rather than cleared by it" in result.out


def test_dispatch_names_each_compared_declaration_and_how_stale_it_is(
    kyo, git_project, tmp_path
) -> None:
    """c1 proper: per other running dispatch, what was compared and how stale it is.

    A running dispatch has never reported *by construction* — ingesting a report
    moves it out of ``running`` — so "never checked against work" is the commonest
    answer and the one a controller most needs said out loud."""
    a = _promote(kyo, git_project, tmp_path, "a", ["kyosei/config.yml"])
    b = _promote(kyo, git_project, tmp_path, "b", ["kyosei/kinds.yml"])
    first = kyo("dispatch", a, "--worktree", _worktree(tmp_path, "wa"), "--json")
    assert first.code == 0, first

    second = kyo("--full", "dispatch", b, "--worktree", _worktree(tmp_path, "wb"), "--json")
    assert second.code == 0, second

    provenance = second.json["disjointness_provenance"]
    assert [p["dispatch_id"] for p in provenance] == [first.json["dispatch_id"]]
    assert provenance[0]["staleness"] == "never-checked"
    assert provenance[0]["declared_files"] == ["kyosei/config.yml"]
    # It has not reported; a dispatch that has is not in `running` at all.
    assert provenance[0]["reported"] is False
    # The controller is told, in the terminal, on a run that passed.
    spoken = kyo("dispatch", b, "--force", "--worktree", _worktree(tmp_path, "wc"))
    assert spoken.code == 0, spoken
    assert "DECLARED touches" in spoken.out
    # KYO-489: "a prediction on both sides" is the full text, in `kyo reference dispatch`.
    assert "prediction on both sides" in explanations.full("disjointness-declared")
    assert "prediction on both sides" not in spoken.out
    assert "never checked against work" in spoken.out
    # And the worker, who is the one party that cannot see the other dispatches,
    # is told the same thing in the frozen brief.
    brief = second.json["record"]["dispatches"][0]["brief"]
    assert "What the disjointness check compared" in brief
    assert first.json["dispatch_id"] in brief
    assert "never checked against work" in brief


def test_a_declaration_a_report_already_disproved_is_named_as_known_incomplete(
    kyo, git_project, tmp_path
) -> None:
    """The strongest thing c1 can say, and the only one worth acting on.

    A record on its second dispatch whose FIRST dispatch reported files it still does
    not declare has a table that is not merely unverified — it is known incomplete, by
    exactly those paths, and the disjointness check just compared against it without
    seeing them. That is the consumer's eight-of-eight finding turned into a sentence
    about a specific dispatch."""
    a = _promote(kyo, git_project, tmp_path, "a", ["kyosei/config.yml"])
    b = _promote(kyo, git_project, tmp_path, "b", ["kyosei/kinds.yml"])

    first = kyo("dispatch", a, "--worktree", _worktree(tmp_path, "wa"), "--json")
    assert first.code == 0, first
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            valid_report(
                dispatch_id=first.json["dispatch_id"],
                record_id=a,
                worked_from_sha=first.json["worked_from_sha"],
                # It edited a file the record never declared, and nobody fed it back.
                touched_files=["kyosei/config.yml", "kyosei/procedures.md"],
            )
        ),
        encoding="utf-8",
    )
    assert kyo("report", first.json["dispatch_id"], "--file", str(report)).code == 0
    # Second dispatch on the same record: running again, still declaring the old set.
    again = kyo("dispatch", a, "--worktree", _worktree(tmp_path, "wa2"), "--json")
    assert again.code == 0, again

    second = kyo("--full", "dispatch", b, "--worktree", _worktree(tmp_path, "wb"), "--json")
    assert second.code == 0, second
    provenance = second.json["disjointness_provenance"]
    assert [p["staleness"] for p in provenance] == ["unreconciled"]
    assert provenance[0]["unreconciled_files"] == ["kyosei/procedures.md"]
    assert "known incomplete" in second.json["record"]["dispatches"][0]["brief"]
    spoken = kyo("dispatch", b, "--force", "--worktree", _worktree(tmp_path, "wc"))
    assert spoken.code == 0, spoken
    assert "KNOWN INCOMPLETE" in spoken.out
    assert "kyosei/procedures.md" in spoken.out
    assert "kyo touch-drift <dispatch-id> --apply" in spoken.out


def test_a_reconciled_declaration_says_it_survived_contact_once(
    kyo, git_project, tmp_path
) -> None:
    """The other side of the same fact, and the reason c4's feed-back is worth doing
    even though it does not improve the guard: a table whose drift WAS fed back reads
    differently here from one that never was. Still a prediction about work in flight
    — that part is structural — but one that has been checked against a diff once."""
    a = _promote(kyo, git_project, tmp_path, "a", ["kyosei/config.yml"])
    b = _promote(kyo, git_project, tmp_path, "b", ["kyosei/kinds.yml"])

    first = kyo("dispatch", a, "--worktree", _worktree(tmp_path, "wa"), "--json")
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            valid_report(
                dispatch_id=first.json["dispatch_id"],
                record_id=a,
                worked_from_sha=first.json["worked_from_sha"],
                touched_files=["kyosei/config.yml", "kyosei/procedures.md"],
            )
        ),
        encoding="utf-8",
    )
    assert kyo("report", first.json["dispatch_id"], "--file", str(report)).code == 0
    corrected = tmp_path / "corrected.txt"
    corrected.write_text("kyosei/config.yml\nkyosei/procedures.md\n", encoding="utf-8")
    assert kyo("edit", a, "--touches-file", str(corrected)).code == 0
    assert kyo("dispatch", a, "--worktree", _worktree(tmp_path, "wa2"), "--json").code == 0

    second = kyo("dispatch", b, "--worktree", _worktree(tmp_path, "wb"), "--json")
    assert second.code == 0, second
    provenance = second.json["disjointness_provenance"]
    assert [p["staleness"] for p in provenance] == ["reconciled"]
    assert provenance[0]["unreconciled_files"] == []
    spoken = kyo("dispatch", b, "--force", "--worktree", _worktree(tmp_path, "wc"))
    assert "survived contact once" in spoken.out


# ------------------------- c2/c3: what this record's declared files usually drag in


def _report_touching(kyo, tmp_path, record_id, worktree, files: list[str]) -> None:
    """One dispatch on ``record_id`` that reports having touched ``files``."""
    dispatched = kyo("dispatch", record_id, "--worktree", worktree, "--json")
    assert dispatched.code == 0, dispatched
    report = tmp_path / f"{dispatched.json['dispatch_id']}.json"
    report.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatched.json["dispatch_id"],
                record_id=record_id,
                worked_from_sha=dispatched.json["worked_from_sha"],
                touched_files=files,
            )
        ),
        encoding="utf-8",
    )
    assert kyo("report", dispatched.json["dispatch_id"], "--file", str(report)).code == 0


def _ingest(kyo, tmp_path, dispatched, record_id, files: list[str]) -> None:
    """Ingest a report for ``dispatched`` naming ``files`` as what it touched."""
    report = tmp_path / f"{dispatched.json['dispatch_id']}-r.json"
    report.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatched.json["dispatch_id"],
                record_id=record_id,
                worked_from_sha=dispatched.json["worked_from_sha"],
                touched_files=files,
            )
        ),
        encoding="utf-8",
    )
    assert kyo("report", dispatched.json["dispatch_id"], "--file", str(report)).code == 0


def test_a_store_with_no_history_says_nothing_at_all(kyo, git_project, tmp_path) -> None:
    """KYO-147.c3, the half that matters most. A young store has nothing to say and
    says NOTHING — not "0 findings", not "not enough history yet". A line with the
    shape of a finding and the content of a shrug teaches a controller to skip the
    line, and then to skip the real ones."""
    record = _promote(kyo, git_project, tmp_path, "a", ["kyosei/config.yml"])
    result = kyo("--full", "dispatch", record, "--json")

    assert result.code == 0, result
    # Nothing else is running, so the provenance list is empty and the line above it
    # is the only thing said about the comparison.
    assert result.json["disjointness_provenance"] == []
    assert result.json["touch_cooccurrence"]["findings"] == []
    assert result.json["touch_cooccurrence"]["reports"] == 0
    spoken = kyo("dispatch", record, "--force", "--worktree", _worktree(tmp_path, "w2"))
    assert "usually drag in" not in spoken.out
    assert "historically" not in spoken.out
    brief = result.json["record"]["dispatches"][0]["brief"]
    assert "drag in" not in brief
    # ...but `--json` can still say WHY it was silent, which is not a finding.
    assert result.json["touch_cooccurrence"]["min_reports"] == commands.COOCCURRENCE_MIN_REPORTS


def test_a_record_is_told_which_files_its_declared_set_has_dragged_in(
    kyo, git_project, tmp_path
) -> None:
    """KYO-147.c2, computed from rows the store already had.

    Every past dispatch here touched `kyosei/config.yml` and also `README.md` — the
    shape of a generated mirror or a positionally-keyed index, which is what m2 says
    no touches table predicts. The new record declares the first and not the second,
    and is told so at generation time, which is the only moment that can improve the
    prediction the guard consumes."""
    history = _promote(kyo, git_project, tmp_path, "hist", ["kyosei/config.yml"])
    for n in range(commands.COOCCURRENCE_MIN_REPORTS):
        _report_touching(
            kyo, tmp_path, history, _worktree(tmp_path, f"wh{n}"),
            ["kyosei/config.yml", "README.md"],
        )

    fresh = _promote(kyo, git_project, tmp_path, "fresh", ["kyosei/config.yml"])
    result = kyo("--full", "dispatch", fresh, "--worktree", _worktree(tmp_path, "wf"), "--json")

    assert result.code == 0, result
    findings = result.json["touch_cooccurrence"]["findings"]
    assert [(f["declared"], f["also_touched"]) for f in findings] == [
        ("kyosei/config.yml", "README.md")
    ]
    assert findings[0]["hits"] == findings[0]["support"] == commands.COOCCURRENCE_MIN_REPORTS
    # Counts, not just names: a controller has to be able to weigh it.
    spoken = kyo("dispatch", fresh, "--force", "--worktree", _worktree(tmp_path, "wf2"))
    assert f"{findings[0]['hits']} of the {findings[0]['support']}" in spoken.out
    brief = result.json["record"]["dispatches"][0]["brief"]
    assert "What files like these usually drag in" in brief
    assert "README.md" in brief
    # It refuses nothing.
    assert result.json["record"]["dispatches"][0]["state"] == "running"


def test_a_file_the_record_already_declares_is_never_reported_as_a_drag(
    kyo, git_project, tmp_path
) -> None:
    """The question is "what does this record NOT declare". A co-occurrence the table
    already names is a prediction that was right, and printing it back would be the
    tool congratulating itself in the one section a worker is meant to act on."""
    history = _promote(kyo, git_project, tmp_path, "hist", ["kyosei/config.yml"])
    for n in range(commands.COOCCURRENCE_MIN_REPORTS):
        _report_touching(
            kyo, tmp_path, history, _worktree(tmp_path, f"wh{n}"),
            ["kyosei/config.yml", "README.md"],
        )

    fresh = _promote(kyo, git_project, tmp_path, "fresh", ["kyosei/config.yml", "README.md"])
    result = kyo("dispatch", fresh, "--worktree", _worktree(tmp_path, "wf"), "--json")

    assert result.code == 0, result
    assert result.json["touch_cooccurrence"]["findings"] == []
    spoken = kyo("dispatch", fresh, "--force", "--worktree", _worktree(tmp_path, "wf2"))
    assert "usually drag in" not in spoken.out


def test_a_file_dragged_by_several_declared_files_gets_one_row_naming_all_of_them(
    kyo, git_project, tmp_path
) -> None:
    """KYO-347.c1. The consumer report this record was filed on: a record
    touching App.tsx, Monitor.ts and Registry.ts saw `types.ts` printed
    three times, once per declared file that dragged it in — nine rows for three
    real findings. Three declared files here each separately drag in one dragged
    file, with three different hits-of-support figures, and the table groups by the
    dragged file: one row, naming all three declared files, each with its own
    figure."""
    hist_a = _promote(kyo, git_project, tmp_path, "hist-a", ["a.txt"])
    for n in range(6):
        _report_touching(
            kyo, tmp_path, hist_a, _worktree(tmp_path, f"wa{n}"), ["a.txt", "dragged.txt"],
        )
    hist_b = _promote(kyo, git_project, tmp_path, "hist-b", ["b.txt"])
    for n in range(5):
        _report_touching(
            kyo, tmp_path, hist_b, _worktree(tmp_path, f"wb{n}"), ["b.txt", "dragged.txt"],
        )
    _report_touching(kyo, tmp_path, hist_b, _worktree(tmp_path, "wb-miss"), ["b.txt"])
    hist_c = _promote(kyo, git_project, tmp_path, "hist-c", ["c.txt"])
    for n in range(6):
        _report_touching(
            kyo, tmp_path, hist_c, _worktree(tmp_path, f"wc{n}"), ["c.txt", "dragged.txt"],
        )
    for n in range(2):
        _report_touching(
            kyo, tmp_path, hist_c, _worktree(tmp_path, f"wc-miss{n}"), ["c.txt"],
        )

    fresh = _promote(kyo, git_project, tmp_path, "fresh", ["a.txt", "b.txt", "c.txt"])
    result = kyo("--full", "dispatch", fresh, "--worktree", _worktree(tmp_path, "wf"), "--json")

    assert result.code == 0, result
    # --json findings are unchanged: one entry per (declared, also_touched) pairing,
    # untouched by the grouping this record adds to the brief's rendering (KYO-347.c2).
    findings = result.json["touch_cooccurrence"]["findings"]
    pairs = {(f["declared"], f["also_touched"]): (f["hits"], f["support"]) for f in findings}
    assert pairs == {
        ("a.txt", "dragged.txt"): (6, 6),
        ("b.txt", "dragged.txt"): (5, 6),
        ("c.txt", "dragged.txt"): (6, 8),
    }

    brief = result.json["record"]["dispatches"][0]["brief"]
    rows = [line for line in brief.splitlines() if line.startswith("| dragged.txt ")]
    assert len(rows) == 1, brief
    row = rows[0]
    assert "a.txt 6/6" in row
    assert "b.txt 5/6" in row
    assert "c.txt 6/8" in row
    # Ordered by the strongest pairing: a.txt (rate 1.0) before c.txt (0.75) before
    # b.txt (0.83)... rate order is a.txt, b.txt, c.txt (1.0, 0.83, 0.75), so within
    # the one row the declared files appear in that order too.
    assert row.index("a.txt") < row.index("b.txt") < row.index("c.txt")


def test_a_weak_co_occurrence_is_silence_not_a_percentage(kyo, git_project, tmp_path) -> None:
    """KYO-147.c3's other half, and the reason the thresholds are two numbers rather
    than one. Measured over this repository's own 128 reports: of the 4445 ordered
    pairs with support >= 5 the median rate is 0.14 and the 90th percentile 0.42, so
    a rate threshold below half prints coincidences. A rate ALONE is not enough
    either — 3-of-5 is 0.6 and still three dispatches, and on the real store that
    surfaced `query.py => tests/test_bases.py`, a pair with no mechanism behind it.

    Here, enough dispatches to speak from, and a file that co-occurred in a minority
    of them: nothing is printed at all."""
    history = _promote(kyo, git_project, tmp_path, "hist", ["kyosei/config.yml"])
    below = commands.COOCCURRENCE_MIN_HITS - 1
    for n in range(commands.COOCCURRENCE_MIN_REPORTS):
        also = ["README.md"] if n < below else []
        _report_touching(
            kyo, tmp_path, history, _worktree(tmp_path, f"wh{n}"),
            ["kyosei/config.yml", *also],
        )

    fresh = _promote(kyo, git_project, tmp_path, "fresh", ["kyosei/config.yml"])
    result = kyo("--full", "dispatch", fresh, "--worktree", _worktree(tmp_path, "wf"), "--json")

    assert result.code == 0, result
    assert result.json["touch_cooccurrence"]["reports"] == commands.COOCCURRENCE_MIN_REPORTS
    assert result.json["touch_cooccurrence"]["findings"] == []
    assert "usually drag in" not in result.json["record"]["dispatches"][0]["brief"]
    spoken = kyo("dispatch", fresh, "--force", "--worktree", _worktree(tmp_path, "wf2"))
    assert "README.md" not in spoken.out


def test_a_handful_of_reports_is_too_little_history_to_speak_from(
    kyo, git_project, tmp_path
) -> None:
    """KYO-147.c3's store-level floor, which nothing else here would catch.

    These few dispatches co-occurred PERFECTLY — every one touched both files, so the
    rate is 1.0 and the hit count clears :data:`commands.COOCCURRENCE_MIN_HITS`. Every
    per-pair threshold passes and the tool still says nothing, because a store this
    young has not yet shown that its pairs mean anything: with a handful of dispatches
    the strongest possible signal and an accident are the same measurement. "It
    degrades gracefully (a young store says nothing)" is the record's own wording, and
    this is the case where graceful and silent have to be the same thing."""
    assert commands.COOCCURRENCE_MIN_HITS < commands.COOCCURRENCE_MIN_REPORTS, (
        "this test is only meaningful while a perfect pair can clear the per-pair "
        "thresholds without clearing the store-level floor"
    )
    history = _promote(kyo, git_project, tmp_path, "hist", ["kyosei/config.yml"])
    for n in range(commands.COOCCURRENCE_MIN_HITS):
        _report_touching(
            kyo, tmp_path, history, _worktree(tmp_path, f"wh{n}"),
            ["kyosei/config.yml", "README.md"],
        )

    fresh = _promote(kyo, git_project, tmp_path, "fresh", ["kyosei/config.yml"])
    result = kyo("--full", "dispatch", fresh, "--worktree", _worktree(tmp_path, "wf"), "--json")

    assert result.code == 0, result
    assert result.json["touch_cooccurrence"]["reports"] == commands.COOCCURRENCE_MIN_HITS
    assert result.json["touch_cooccurrence"]["findings"] == []
    assert "usually drag in" not in result.json["record"]["dispatches"][0]["brief"]


# ------------------------------ c4: apply the correction instead of retyping the list


def test_edit_stores_the_correction_touch_drift_printed(kyo, git_project, tmp_path) -> None:
    """KYO-147.c4. `kyo report` has printed the corrected list since KYO-71 and the
    runnable `printf ... | kyo edit --touches-file -` one-liner since KYO-85.c3, so
    "without hand-retyping the file list" was arguably already met. What was not met
    is applying it without leaving the tool: a line to paste into another shell is a
    step the consumer measured themselves skipping, and every skipped one leaves the
    store wrong about the past.

    So: a flag on `kyo edit`, which is the command KYO-71's output has always named as
    the remedy, storing exactly the lines `kyo touch-drift` prints -- read from that
    one computation rather than recomputed."""
    # Not paths under the store directory: `kyo touch-drift` ignores those by design.
    record = _promote(kyo, git_project, tmp_path, "a", ["src/widget.py"])
    (git_project / "src" / "helper.py").write_text("x = 1\n", encoding="utf-8")
    dispatched = kyo("dispatch", record, "--worktree", _worktree(tmp_path, "wa"), "--json")
    assert dispatched.code == 0, dispatched
    _ingest(kyo, tmp_path, dispatched, record, ["src/widget.py", "src/helper.py"])

    assert [t["path"] for t in kyo("show", record, "--json").json["touches"]] == [
        "src/widget.py"
    ]
    printed = kyo("touch-drift", dispatched.json["dispatch_id"], "--json")
    assert printed.code == 0, printed

    applied = kyo("edit", record, "--touches-from-drift", dispatched.json["dispatch_id"])
    assert applied.code == 0, applied
    after = [t["path"] for t in kyo("show", record, "--json").json["touches"]]
    # Exactly the lines the read-only command prints, not a diff to merge by hand.
    assert after == printed.json["touch_drift"]["touches_file_lines"]
    assert after == ["src/helper.py", "src/widget.py"]
    # And it says, where it is easiest to believe otherwise, what this did not buy --
    # KYO-489: as its short form, with the measurement in `kyo reference edit`.
    assert explanations.short("drift-fed-back") in applied.out
    assert applied.out.rstrip("\n").splitlines()[-1] == explanations.footer_line("edit")


def test_touch_drift_itself_still_writes_nothing(kyo, git_project, tmp_path) -> None:
    """`kyo touch-drift` stays a pure read, and three separate write-capability
    declarations in this project depend on that being true: `cli.MUTATING_COMMANDS`,
    records mode's `RECORDS_READ_COMMANDS` allowlist, and the desktop app's
    `KYO_WRITING_COMMANDS` (which `tests/test_kyo140_desktop_command_drift.py` keeps
    in step with the first). Putting the write here would have made all three wrong at
    once, which is why c4's flag went on `kyo edit` instead."""
    from kyosei.cli import MUTATING_COMMANDS

    assert "touch-drift" not in MUTATING_COMMANDS
    record = _promote(kyo, git_project, tmp_path, "a", ["src/widget.py"])
    (git_project / "src" / "helper.py").write_text("x = 1\n", encoding="utf-8")
    dispatched = kyo("dispatch", record, "--worktree", _worktree(tmp_path, "wa"), "--json")
    _ingest(kyo, tmp_path, dispatched, record, ["src/widget.py", "src/helper.py"])

    plain = kyo("touch-drift", dispatched.json["dispatch_id"], "--json")
    assert plain.code == 0, plain
    assert plain.json["touch_drift"]["undeclared_files"] == ["src/helper.py"]
    assert [t["path"] for t in kyo("show", record, "--json").json["touches"]] == [
        "src/widget.py"
    ]


def test_the_correction_is_refused_whole_when_the_merge_has_not_landed_its_paths(
    kyo, git_project, tmp_path
) -> None:
    """The caveat KYO-71's own output already named, and the reason c4 needed no new
    check for it: `--touches-from-drift` runs the identical `_validate_touches` that
    `--touches-file` runs, and that validates every path against the working tree
    (charter, Layer 1). A file the branch CREATED exists only on that branch until the
    merge, so the correction is refused as a whole rather than trimmed to the paths
    that happen to exist -- a trimmed one would store a touches table wrong in a NEW
    way and report success, which is the failure shape this record is about."""
    record = _promote(kyo, git_project, tmp_path, "a", ["src/widget.py"])
    dispatched = kyo("dispatch", record, "--worktree", _worktree(tmp_path, "wa"),
                     "--branch", "feature", "--json")
    _ingest(kyo, tmp_path, dispatched, record,
            ["src/widget.py", "src/created_on_the_branch.py"])

    refused = kyo("edit", record, "--touches-from-drift", dispatched.json["dispatch_id"])
    assert refused.code != 0, refused
    assert "src/created_on_the_branch.py" in refused.err
    # Nothing was half-stored.
    assert [t["path"] for t in kyo("show", record, "--json").json["touches"]] == [
        "src/widget.py"
    ]


def test_applying_a_drift_that_was_never_checked_is_refused_not_a_no_op(
    kyo, git_project, tmp_path
) -> None:
    """"Stored 0 path(s)" is the shape of a success, and a dispatch whose drift was
    NOT CHECKED has no correction at all -- the same reason KYO-198 refuses `kyo verify
    --from-report` on a dispatch with no report rather than answering "applied 0 of 0".
    Storing the record's existing touches back over themselves would be worse than a
    no-op: it would report success about a comparison that never happened. The remedy
    named is the thing that makes an answer possible."""
    record = _promote(kyo, git_project, tmp_path, "a", ["src/widget.py"])
    dispatched = kyo("dispatch", record, "--worktree", _worktree(tmp_path, "wa"), "--json")
    # No branch declared and no report ingested: there is nothing to compare.
    refused = kyo("edit", record, "--touches-from-drift", dispatched.json["dispatch_id"])
    assert refused.code == EXIT_CONTRACT, refused
    assert "no correction to store" in refused.err
    assert "kyo report" in refused.err


def test_the_two_touches_flags_refuse_each_other(kyo, git_project, tmp_path) -> None:
    """Each REPLACES the whole list, so together they mean "whichever this build reads
    second" -- the class of silent ambiguity law 6 refuses rather than resolves."""
    record = _promote(kyo, git_project, tmp_path, "a", ["src/widget.py"])
    dispatched = kyo("dispatch", record, "--worktree", _worktree(tmp_path, "wa"), "--json")
    scope = tmp_path / "t.txt"
    scope.write_text("src/widget.py\n", encoding="utf-8")
    refused = kyo(
        "edit", record,
        "--touches-file", str(scope),
        "--touches-from-drift", dispatched.json["dispatch_id"],
    )
    assert refused.code != 0, refused
    assert "not both" in refused.err


# ------------------------- KYO-395: a new file under another's directory touch warns


def _promote_new(kyo, project: Path, tmp_path: Path, name: str, touches: list[str]) -> str:
    """:func:`_promote` for touches that include the ``+path`` and ``dir/`` forms,
    neither of which may be created on disk first: ``+path`` claims a file that does
    not exist yet, and a directory touch is valid whether or not its tree does."""
    plain = [t for t in touches if not t.startswith("+") and not t.endswith("/")]
    _promote_files = [project / line.split("#", 1)[0] for line in plain]
    for target in _promote_files:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("placeholder\n", encoding="utf-8")
    idea = project / "ideas" / f"{name}.md"
    idea.write_text(f"# {name}\n\nDoes a thing.\n", encoding="utf-8")
    criteria = tmp_path / f"{name}-criteria.txt"
    criteria.write_text("done\n", encoding="utf-8")
    touch_file = tmp_path / f"{name}-touches.txt"
    touch_file.write_text("".join(f"{line}\n" for line in touches), encoding="utf-8")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", str(criteria), "--touches-file", str(touch_file), "--json",
    )
    assert result.code == 0, result
    return result.json["record"]["id"]


def test_a_new_file_under_a_running_directory_touch_warns_and_is_not_refused(
    kyo, git_project, tmp_path
) -> None:
    """KYO-395.c1/c3, from a consumer report: APP-61 declared `+apps/desktop/test/
    enroll.test.js` while APP-45 held `apps/desktop/test/`, and `--force` was the only
    way on. The file cannot be in the other dispatch's frozen plan, so it is let
    through — and SAID, as a warning naming both dispatches and the file, because the
    other worker owns that tree and may create the same name."""
    owner = _promote_new(kyo, git_project, tmp_path, "owner", ["apps/desktop/test/"])
    creator = _promote_new(
        kyo, git_project, tmp_path, "creator", ["+apps/desktop/test/enroll.test.js"]
    )
    first = kyo("dispatch", owner, "--worktree", _worktree(tmp_path, "wa"), "--json")
    assert first.code == 0, first
    owner_dispatch = first.json["dispatch_id"]

    # `kyo next` reads the same computation and must agree: offered, not withheld.
    listing = kyo("next", "--json").json
    assert creator in [r["id"] for r in listing["ready"]]

    passed = kyo("dispatch", creator, "--worktree", _worktree(tmp_path, "wb"), "--json")
    assert passed.code == 0, passed
    creator_dispatch = passed.json["dispatch_id"]
    assert passed.json["collisions_overridden"] == []  # nothing was forced past
    [warning] = commands.narrowing_warnings(passed.json["touch_narrowings"])
    assert warning["dispatch_id"] == owner_dispatch
    assert warning["path"] == "+apps/desktop/test/enroll.test.js"
    assert warning["running_directories"] == ["apps/desktop/test/"]
    assert warning["candidate_dispatch_id"] == creator_dispatch
    sentence = commands.narrowing_detail(warning)
    for fact in ("WARNING", owner_dispatch, creator_dispatch, "apps/desktop/test/enroll.test.js"):
        assert fact in sentence
    assert "free to create files there" in sentence

    # c1: the controller is told in the terminal. A re-dispatch under --force into a
    # third worktree is the cheapest way to read the text rendering of the same pass.
    spoken = kyo(
        "dispatch", creator, "--force", "--worktree", _worktree(tmp_path, "wc")
    )
    assert spoken.code == 0, spoken
    assert "WARNING" in spoken.out and owner_dispatch in spoken.out
    assert "apps/desktop/test/enroll.test.js" in spoken.out

    # c3: the stored record of the dispatch reads it back, via `kyo show`.
    shown = kyo("show", creator_dispatch)
    assert shown.code == 0, shown
    assert "WARNING — let through, not cleared" in shown.out
    assert "apps/desktop/test/enroll.test.js" in shown.out
    assert owner_dispatch in shown.out and creator_dispatch in shown.out


def test_a_new_file_that_already_exists_is_still_refused(kyo, git_project, tmp_path) -> None:
    """KYO-395.c2, first half: the let-through rests on "the file does not exist yet".
    Once it does, the `+` claim is stale and the other worker may already be editing a
    real file in its tree — the old refusal comes back."""
    owner = _promote_new(kyo, git_project, tmp_path, "owner", ["apps/desktop/test/"])
    creator = _promote_new(
        kyo, git_project, tmp_path, "creator", ["+apps/desktop/test/enroll.test.js"]
    )
    assert kyo("dispatch", owner, "--worktree", _worktree(tmp_path, "wa")).code == 0
    made = git_project / "apps" / "desktop" / "test" / "enroll.test.js"
    made.parent.mkdir(parents=True, exist_ok=True)
    made.write_text("// exists now\n", encoding="utf-8")

    refused = kyo("dispatch", creator, "--worktree", _worktree(tmp_path, "wb"))
    assert refused.code == EXIT_CONTRACT, refused
    assert "enroll.test.js" in refused.err


def test_a_new_file_the_other_dispatch_also_names_is_still_refused(
    kyo, git_project, tmp_path
) -> None:
    """KYO-395.c2, second half: the other dispatch naming that same path is a
    file-level collision, whatever directory it also claims."""
    owner = _promote_new(
        kyo, git_project, tmp_path, "owner",
        ["apps/desktop/test/", "+apps/desktop/test/enroll.test.js"],
    )
    creator = _promote_new(
        kyo, git_project, tmp_path, "creator", ["+apps/desktop/test/enroll.test.js"]
    )
    assert kyo("dispatch", owner, "--worktree", _worktree(tmp_path, "wa")).code == 0

    refused = kyo("dispatch", creator, "--worktree", _worktree(tmp_path, "wb"))
    assert refused.code == EXIT_CONTRACT, refused
    assert "enroll.test.js" in refused.err


def test_a_directory_claimed_over_a_running_new_file_still_collides(
    kyo, git_project, tmp_path
) -> None:
    """KYO-395, the direction deliberately left alone: a candidate claiming `dir/`
    while a running dispatch is creating a file in it would own the tree that worker
    is already writing into. The consumer's argument ("not in the frozen plan") is
    about the candidate's `+path`, and does not apply the other way round."""
    creator = _promote_new(
        kyo, git_project, tmp_path, "creator", ["+apps/desktop/test/enroll.test.js"]
    )
    owner = _promote_new(kyo, git_project, tmp_path, "owner", ["apps/desktop/test/"])
    assert kyo("dispatch", creator, "--worktree", _worktree(tmp_path, "wa")).code == 0

    refused = kyo("dispatch", owner, "--worktree", _worktree(tmp_path, "wb"))
    assert refused.code == EXIT_CONTRACT, refused


def test_narrowing_the_directory_claim_clears_the_warning(kyo, git_project, tmp_path) -> None:
    """The warning's remedy — narrow the other dispatch's claim to the files it will
    actually create — clears it, because the claim is re-read on every comparison."""
    owner = _promote_new(kyo, git_project, tmp_path, "owner", ["apps/desktop/test/"])
    creator = _promote_new(
        kyo, git_project, tmp_path, "creator", ["+apps/desktop/test/enroll.test.js"]
    )
    assert kyo("dispatch", owner, "--worktree", _worktree(tmp_path, "wa")).code == 0
    narrowed = tmp_path / "owner-narrowed.txt"
    narrowed.write_text("+apps/desktop/test/other.test.js\n", encoding="utf-8")
    edited = kyo("edit", owner, "--touches-file", str(narrowed))
    assert edited.code == 0, edited

    passed = kyo("dispatch", creator, "--worktree", _worktree(tmp_path, "wb"), "--json")
    assert passed.code == 0, passed
    assert commands.narrowing_warnings(passed.json["touch_narrowings"]) == []
