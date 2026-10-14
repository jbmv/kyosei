"""KYO-7.c1/c2: ``kyo dispatch`` — frozen brief generation and touch disjointness."""

from __future__ import annotations

import json
from pathlib import Path

from kyosei import explanations
from kyosei.cli import MUTATING_COMMANDS
from kyosei.errors import EXIT_CONTRACT, EXIT_ERROR, EXIT_USAGE

from .conftest import git_worktree, new_record


def test_dispatch_creates_a_running_dispatch_with_a_frozen_brief(kyo, git_project) -> None:
    record = new_record(kyo, criteria=["widgets stop exploding"])
    result = kyo("dispatch", record, "--json", "--full")
    assert result.code == 0, result
    doc = result.json
    assert doc["dispatch_id"].startswith(f"{record}.d")
    assert doc["worked_from_sha"]
    assert doc["record"]["dispatches"][0]["state"] == "running"
    assert doc["record"]["dispatches"][0]["brief"]


def test_brief_contains_every_machine_filled_slot(kyo, git_project) -> None:
    record = new_record(kyo, title="Widget explodes", criteria=["widgets stop exploding"])
    kyo("claim", record, "--level", "asserted", "--method", "read the code", "--file", "-",
        stdin="the cache eviction races the write")
    result = kyo("--full", "dispatch", record, "--json", "--task-file", "-", stdin="Fix the race.")
    assert result.code == 0, result
    brief = result.json["record"]["dispatches"][0]["brief"]

    assert f"Dispatch brief: {record}" in brief
    assert result.json["dispatch_id"] in brief
    assert result.json["worked_from_sha"] in brief
    assert "widgets stop exploding" in brief
    assert "unmet-unbuilt" in brief
    assert "the cache eviction races the write" in brief
    assert "**asserted**" in brief
    assert "write_scope:" in brief
    assert "Standing rules (worker-audience)" in brief
    # a real worker rule from kyosei/procedures.md must actually be there
    assert "Prose enters the store only via stdin or a file" in brief
    assert "Flag conflicts, do not resolve them" in brief
    assert "Fix the race." in brief


def test_dispatch_refuses_off_a_non_git_repo_cleanly(kyo, project: Path) -> None:
    """`project` (no `git_project`) is not a git repo — the error must be clean,
    not a bare traceback (KYO-7.c1)."""
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record)
    assert result.code == EXIT_ERROR
    assert "git" in result.err.lower()
    assert "traceback" not in result.err.lower()


def test_dispatch_writes_brief_to_file_with_brief_out(kyo, git_project, tmp_path: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    out = tmp_path / "brief.md"
    result = kyo("dispatch", record, "--brief-out", str(out))
    assert result.code == 0, result
    assert out.is_file()
    assert f"Dispatch brief: {record}" in out.read_text(encoding="utf-8")


# ------------------------------------------------------------------------ KYO-326
#
# `kyo dispatch` printed the whole frozen brief back (about 35 KB for one dispatch), and
# the controller grepped it down to the dispatch id and the worktree line every time.
# The brief is the only thing that stops printing by default — every
# other line `kyo dispatch` ever printed (warnings, the drag-in summary, the
# disjointness lines, the worktree-creation command) is unaffected, and `--print-brief`
# restores the old full print verbatim.


def test_dispatch_no_longer_prints_the_whole_brief_by_default(kyo, git_project) -> None:
    record = new_record(
        kyo, title="Widget explodes", criteria=["widgets stop exploding"]
    )
    result = kyo("dispatch", record, "--task-file", "-", stdin="Fix the race.")
    assert result.code == 0, result

    # The brief's own content never reaches stdout by default...
    assert "Standing rules (worker-audience)" not in result.out
    assert "widgets stop exploding" not in result.out
    assert "Fix the race." not in result.out
    # ...but a controller is told exactly how to read it, and by which command.
    assert "stored brief (" in result.out
    assert "chars, sha256:" in result.out
    assert "kyo show" in result.out
    assert "--print-brief" in result.out


def test_dispatch_still_prints_every_other_line_it_always_did(kyo, git_project) -> None:
    """Warnings, the worktree/branch/model echoes, and the disjointness provenance
    lines are NOT part of the brief and must survive untouched (KYO-326: only the
    brief's own print moves)."""
    record = new_record(kyo, criteria=["a"])
    result = kyo(
        "dispatch", record, "--branch", "kyo-326-example", "--task-file", "-",
        stdin="a task with 12 tests in it",
    )
    assert result.code == 0, result
    assert "stored base sha:" in result.out
    assert "stored worktree:" in result.out
    assert "stored branch: kyo-326-example" in result.out
    assert "stored worker model:" in result.out
    assert "warning: the task prose states" in result.out
    # KYO-489: the headline prints as its short form; the full text is in
    # `kyo reference dispatch`.
    assert explanations.short("disjointness-declared") in result.out


def test_print_brief_restores_the_old_full_print(kyo, git_project) -> None:
    record = new_record(
        kyo, title="Widget explodes", criteria=["widgets stop exploding"]
    )
    result = kyo("dispatch", record, "--print-brief")
    assert result.code == 0, result
    assert "stored brief (" in result.out
    assert "Standing rules (worker-audience)" in result.out
    assert "widgets stop exploding" in result.out


def test_print_brief_does_not_change_brief_out_or_json(kyo, git_project, tmp_path: Path) -> None:
    """`--brief-out` and `--json` were never affected by the default print changing,
    and stay unaffected by `--print-brief` either."""
    record = new_record(kyo, criteria=["a"])
    out = tmp_path / "brief.md"
    default_json = kyo("--full", "dispatch", record, "--json", "--brief-out", str(out))
    assert default_json.code == 0, default_json
    brief_from_json = default_json.json["record"]["dispatches"][0]["brief"]
    assert f"Dispatch brief: {record}" in brief_from_json
    assert out.read_text(encoding="utf-8") == brief_from_json

    # `record` already has a running dispatch in this same (default) worktree, with an
    # undeclared touch scope — irrelevant to what this test checks, so cleared with
    # `--force` rather than given a real scope.
    record2 = new_record(kyo, criteria=["a"])
    out2 = tmp_path / "brief2.md"
    printed_json = kyo(
        "dispatch", record2, "--json", "--full", "--print-brief", "--brief-out", str(out2),
        "--force",
    )
    assert printed_json.code == 0, printed_json
    brief2_from_json = printed_json.json["record"]["dispatches"][0]["brief"]
    assert f"Dispatch brief: {record2}" in brief2_from_json
    assert out2.read_text(encoding="utf-8") == brief2_from_json


def test_dispatch_does_not_move_the_record(kyo, git_project) -> None:
    """Record moves are NOT implied — dispatch state is its own dimension."""
    record = new_record(kyo, criteria=["a"])
    before = kyo("show", record, "--json").json["status"]
    kyo("dispatch", record)
    after = kyo("show", record, "--json").json["status"]
    assert before == after == "open"


def test_dispatch_refuses_a_non_dispatchable_kind(kyo, git_project) -> None:
    """`plan` ships with `dispatchable: false` in the default kinds.yml."""
    plan = new_record(kyo, kind="plan")
    result = kyo("dispatch", plan)
    assert result.code == EXIT_ERROR
    assert "dispatchable" in result.err


def test_dispatch_refuses_touch_collision_with_a_running_dispatch(
    kyo, git_project, tmp_path
) -> None:
    idea_a = git_project / "ideas" / "a.md"
    idea_a.write_text("# Task A\n\nDoes a thing.\n", encoding="utf-8")
    idea_b = git_project / "ideas" / "b.md"
    idea_b.write_text("# Task B\n\nDoes another thing.\n", encoding="utf-8")

    touches_file = tmp_path / "touches.txt"
    touches_file.write_text("kyosei/config.yml\n", encoding="utf-8")
    criteria_file = tmp_path / "criteria.txt"
    criteria_file.write_text("done\n", encoding="utf-8")

    a = kyo(
        "promote", str(idea_a), "--kind", "chore",
        "--criteria-file", str(criteria_file), "--touches-file", str(touches_file), "--json",
    ).json["record"]["id"]
    b = kyo(
        "promote", str(idea_b), "--kind", "chore",
        "--criteria-file", str(criteria_file), "--touches-file", str(touches_file), "--json",
    ).json["record"]["id"]

    first = kyo("dispatch", a, "--json")
    assert first.code == 0, first
    first_dispatch_id = first.json["dispatch_id"]

    collided = kyo("dispatch", b)
    assert collided.code == EXIT_CONTRACT
    assert first_dispatch_id in collided.err
    assert "kyosei/config.yml" in collided.err

    forced = kyo("--full", "dispatch", b, "--force", "--json")
    assert forced.code == 0, forced
    assert forced.json["collisions_overridden"]
    assert forced.json["collisions_overridden"][0]["dispatch_id"] == first_dispatch_id
    dispatch_row = forced.json["record"]["dispatches"][0]
    assert dispatch_row["touch_override"], "the override must be recorded on the dispatch"


def test_dispatch_error_names_the_colliding_dispatch_and_paths(kyo, git_project, tmp_path) -> None:
    idea_a = git_project / "ideas" / "a.md"
    idea_a.write_text("# Task A\n\nDoes a thing.\n", encoding="utf-8")
    idea_b = git_project / "ideas" / "b.md"
    idea_b.write_text("# Task B\n\nDoes another thing.\n", encoding="utf-8")
    touches_file = tmp_path / "touches.txt"
    touches_file.write_text("kyosei/config.yml\nkyosei/kinds.yml\n", encoding="utf-8")
    criteria_file = tmp_path / "criteria.txt"
    criteria_file.write_text("done\n", encoding="utf-8")

    a = kyo(
        "promote", str(idea_a), "--kind", "chore",
        "--criteria-file", str(criteria_file), "--touches-file", str(touches_file), "--json",
    ).json["record"]["id"]
    b = kyo(
        "promote", str(idea_b), "--kind", "chore",
        "--criteria-file", str(criteria_file), "--touches-file", str(touches_file), "--json",
    ).json["record"]["id"]
    kyo("dispatch", a)
    collided = kyo("dispatch", b)
    assert collided.code == EXIT_CONTRACT
    assert "kyosei/config.yml" in collided.err
    assert "kyosei/kinds.yml" in collided.err


def test_touch_disjointness_narrows_on_disjoint_symbols(kyo, git_project, tmp_path) -> None:
    """KYO-147.c5, and a deliberate reversal of KYO-7.c2.

    This test asserted the opposite until now: "`path#symbol` counts as its file —
    two touches naming different symbols in the same file must still collide." That
    was the right default while nothing distinguished a precise declaration from a
    whole-file one, and it cost a real refusal — KYO-214.d1 refused
    against KYO-212.d1 over `doctor.py` and `README.md` while the two edited
    different functions and different paragraphs, with `--force` the only way through
    and a spurious recorded override as the price.

    The file-level rule is unchanged everywhere else; what is new is that when BOTH
    sides named symbols and the sets are disjoint, the tool says so instead of
    refusing. See the sibling below for the half that did not move.
    """
    idea_a = git_project / "ideas" / "a.md"
    idea_a.write_text("# Task A\n", encoding="utf-8")
    idea_b = git_project / "ideas" / "b.md"
    idea_b.write_text("# Task B\n", encoding="utf-8")
    touches_a = tmp_path / "ta.txt"
    touches_a.write_text("kyosei/kinds.yml#archetype\n", encoding="utf-8")
    touches_b = tmp_path / "tb.txt"
    touches_b.write_text("kyosei/kinds.yml#policy\n", encoding="utf-8")
    criteria_file = tmp_path / "criteria.txt"
    criteria_file.write_text("done\n", encoding="utf-8")

    a = kyo(
        "promote", str(idea_a), "--kind", "chore",
        "--criteria-file", str(criteria_file), "--touches-file", str(touches_a), "--json",
    ).json["record"]["id"]
    b = kyo(
        "promote", str(idea_b), "--kind", "chore",
        "--criteria-file", str(criteria_file), "--touches-file", str(touches_b), "--json",
    ).json["record"]["id"]
    # Separate worktrees, because the worktree-collision refusal is a different check
    # and would mask this one — it fired here while this test was being rewritten.
    git_worktree(kyo.project, tmp_path / "wa")
    git_worktree(kyo.project, tmp_path / "wb")
    assert kyo("dispatch", a, "--worktree", str(tmp_path / "wa")).code == 0
    passed = kyo("dispatch", b, "--worktree", str(tmp_path / "wb"), "--json", "--full")

    assert passed.code == 0, passed
    narrowings = passed.json["touch_narrowings"]
    assert [n["path"] for n in narrowings] == ["kyosei/kinds.yml"]
    assert narrowings[0]["candidate_symbols"] == ["policy"]
    assert narrowings[0]["running_symbols"] == ["archetype"]
    # Nothing was forced past: a narrowing is not an override.
    assert passed.json["collisions_overridden"] == []
    # And it is SAID. A guard that quietly narrows is a guard nobody can audit.
    assert "kyosei/kinds.yml" in passed.out
    assert "#archetype" in passed.out and "#policy" in passed.out


def test_a_plain_path_still_collides_with_every_symbol_in_that_file(
    kyo, git_project, tmp_path
) -> None:
    """KYO-147.c5's other half: the narrowing applies only where BOTH sides were
    precise. A touch with no `#symbol` claims the whole file, so it collides with a
    symbol touch in it exactly as it always did — the controller who declared a bare
    path gets the guard they had before, which is what keeps this from being a silent
    weakening of the check."""
    idea_a = git_project / "ideas" / "a.md"
    idea_a.write_text("# Task A\n", encoding="utf-8")
    idea_b = git_project / "ideas" / "b.md"
    idea_b.write_text("# Task B\n", encoding="utf-8")
    touches_a = tmp_path / "ta.txt"
    touches_a.write_text("kyosei/kinds.yml\n", encoding="utf-8")
    touches_b = tmp_path / "tb.txt"
    touches_b.write_text("kyosei/kinds.yml#policy\n", encoding="utf-8")
    criteria_file = tmp_path / "criteria.txt"
    criteria_file.write_text("done\n", encoding="utf-8")

    a = kyo(
        "promote", str(idea_a), "--kind", "chore",
        "--criteria-file", str(criteria_file), "--touches-file", str(touches_a), "--json",
    ).json["record"]["id"]
    b = kyo(
        "promote", str(idea_b), "--kind", "chore",
        "--criteria-file", str(criteria_file), "--touches-file", str(touches_b), "--json",
    ).json["record"]["id"]
    git_worktree(kyo.project, tmp_path / "wa")
    git_worktree(kyo.project, tmp_path / "wb")
    assert kyo("dispatch", a, "--worktree", str(tmp_path / "wa")).code == 0
    collided = kyo("dispatch", b, "--worktree", str(tmp_path / "wb"))
    assert collided.code == EXIT_CONTRACT, collided
    assert "kyosei/kinds.yml" in collided.err


def test_dispatch_does_not_collide_with_a_reported_dispatch(kyo, git_project, tmp_path) -> None:
    """Disjointness only checks *running* dispatches — a reported one is done
    with its worktree."""
    idea_a = git_project / "ideas" / "a.md"
    idea_a.write_text("# Task A\n", encoding="utf-8")
    idea_b = git_project / "ideas" / "b.md"
    idea_b.write_text("# Task B\n", encoding="utf-8")
    touches_file = tmp_path / "touches.txt"
    touches_file.write_text("kyosei/config.yml\n", encoding="utf-8")
    criteria_file = tmp_path / "criteria.txt"
    criteria_file.write_text("done\n", encoding="utf-8")

    a = kyo(
        "promote", str(idea_a), "--kind", "chore",
        "--criteria-file", str(criteria_file), "--touches-file", str(touches_file), "--json",
    ).json["record"]["id"]
    b = kyo(
        "promote", str(idea_b), "--kind", "chore",
        "--criteria-file", str(criteria_file), "--touches-file", str(touches_file), "--json",
    ).json["record"]["id"]

    first = kyo("dispatch", a, "--json")
    dispatch_id = first.json["dispatch_id"]
    sha = first.json["worked_from_sha"]
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "dispatch_id": dispatch_id,
                "record_id": a,
                "worked_from_sha": sha,
                "criteria_verdicts": [],
                "premise_false": [],
                "disagreements": [],
                "corrected_numbers": [],
                "commits": [],
            }
        ),
        encoding="utf-8",
    )
    ingested = kyo("report", dispatch_id, "--file", str(report))
    assert ingested.code == 0, ingested

    second = kyo("dispatch", b)
    assert second.code == 0, second


# ------------------------------------------------------------------------- KYO-48
#
# `kyo next` and this refusal share one computation, and both used to read an
# undeclared scope as an empty one — so `kyo next` recommended a zero-touch
# record alongside a running dispatch and `kyo dispatch` then permitted it
# silently. Two surfaces agreeing in the permissive direction is worse than two
# surfaces disagreeing: nothing anywhere says the check did not run.


def _elsewhere(tmp_path, name: str) -> list[str]:
    """A worktree of this dispatch's own, so the only thing under test is the
    touch question — two dispatches into one checkout is a separate refusal
    (KYO-22) that would otherwise fire first for its own good reasons."""
    # KYO-402: a real linked worktree of the project fixture (``tmp_path / "proj"``);
    # `kyo dispatch --worktree` refuses a bare directory.
    return ["--worktree", git_worktree(tmp_path / "proj", tmp_path / f"wt-{name}")]


def _scoped_chore(kyo, project, tmp_path, name: str, touches: str) -> str:
    """A dispatchable chore, with or without a declared scope.

    Without one it has to come through `kyo new`: `kyo promote` already requires
    `--touches-file` for a work archetype, so the undeclared-scope record this
    record is about can only be created at the zero-ceremony intake door — which
    is exactly the shape the live case had."""
    if not touches:
        return new_record(kyo, kind="chore", title=name, criteria=["done"])
    idea = project / "ideas" / f"{name}.md"
    idea.write_text(f"# {name}\n", encoding="utf-8")
    criteria = tmp_path / f"{name}-criteria.txt"
    criteria.write_text("done\n", encoding="utf-8")
    touch_file = tmp_path / f"{name}-touches.txt"
    touch_file.write_text(touches, encoding="utf-8")
    result = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", str(criteria), "--touches-file", str(touch_file), "--json",
    )
    assert result.code == 0, result
    return result.json["record"]["id"]


def test_dispatch_refuses_a_zero_touch_record_beside_a_running_dispatch(
    kyo, git_project, tmp_path
) -> None:
    scoped = _scoped_chore(kyo, git_project, tmp_path, "scoped", "kyosei/config.yml\n")
    unscoped = _scoped_chore(kyo, git_project, tmp_path, "unscoped", "")
    first = kyo("dispatch", scoped, "--json")
    assert first.code == 0, first

    refused = kyo("dispatch", unscoped, *_elsewhere(tmp_path, "unscoped"))
    assert refused.code == EXIT_CONTRACT, refused
    assert "dispatch-scope-unknown" in refused.err
    assert first.json["dispatch_id"] in refused.err
    assert unscoped in refused.err


def test_declaring_a_disjoint_scope_clears_the_scope_unknown_refusal(
    kyo, git_project, tmp_path
) -> None:
    """The remedy the error names must actually clear it (CLAUDE.md's standing
    rule) — and it is the remedy that adds information, not the one that
    suppresses the question."""
    scoped = _scoped_chore(kyo, git_project, tmp_path, "scoped", "kyosei/config.yml\n")
    unscoped = _scoped_chore(kyo, git_project, tmp_path, "unscoped", "")
    kyo("dispatch", scoped, "--json")

    elsewhere = _elsewhere(tmp_path, "unscoped")
    refused = kyo("dispatch", unscoped, *elsewhere)
    assert "--touches-file" in refused.err

    touches = tmp_path / "late-touches.txt"
    touches.write_text("kyosei/kinds.yml\n", encoding="utf-8")
    assert kyo("edit", unscoped, "--touches-file", str(touches)).code == 0
    cleared = kyo("dispatch", unscoped, *elsewhere, "--json")
    assert cleared.code == 0, cleared


def test_force_overrides_the_scope_unknown_refusal_and_records_it(
    kyo, git_project, tmp_path
) -> None:
    """The affordance the record asked to keep: a controller who knows the work is
    independent can still dispatch it — but the fact that nothing checked is then
    a durable part of the dispatch, not a decision that leaves no trace."""
    scoped = _scoped_chore(kyo, git_project, tmp_path, "scoped", "kyosei/config.yml\n")
    unscoped = _scoped_chore(kyo, git_project, tmp_path, "unscoped", "")
    first = kyo("dispatch", scoped, "--json")

    forced = kyo(
        "--full", "dispatch", unscoped, *_elsewhere(tmp_path, "unscoped"), "--force", "--json"
    )
    assert forced.code == 0, forced
    assert forced.json["collisions_overridden"] == [], "nothing was known to collide"
    overridden = forced.json["scope_unknown_overridden"]
    assert overridden and overridden[0]["dispatch_id"] == first.json["dispatch_id"]
    assert unscoped in overridden[0]["undeclared"]
    assert forced.json["record"]["dispatches"][0]["touch_override"], (
        "forcing past an unknown scope must be recorded on the dispatch, like a collision"
    )


def test_next_and_dispatch_cannot_disagree_about_what_is_safe(
    kyo, git_project, tmp_path
) -> None:
    """KYO-48.c3, pinned: whatever `kyo next` offers, `kyo dispatch` accepts;
    whatever `kyo next` holds back as unknown, `kyo dispatch` refuses. Run against
    a store holding one of every case at once."""
    running = _scoped_chore(kyo, git_project, tmp_path, "running", "kyosei/config.yml\n")
    colliding = _scoped_chore(kyo, git_project, tmp_path, "colliding", "kyosei/config.yml\n")
    disjoint = _scoped_chore(kyo, git_project, tmp_path, "disjoint", "kyosei/kinds.yml\n")
    unscoped = _scoped_chore(kyo, git_project, tmp_path, "unscoped", "")
    assert kyo("dispatch", running, "--json").code == 0

    listing = kyo("next", "--json").json
    ready_ids = [r["id"] for r in listing["ready"]]
    unknown_ids = [r["id"] for r in listing["scope_unknown"]]
    assert ready_ids == [disjoint]
    assert unknown_ids == [unscoped]
    assert colliding not in ready_ids + unknown_ids

    for record_id in unknown_ids:
        refused = kyo("dispatch", record_id, *_elsewhere(tmp_path, record_id))
        assert refused.code == EXIT_CONTRACT, (
            f"`kyo next` withheld {record_id} as unknown but `kyo dispatch` permitted it"
        )
    for record_id in ready_ids:
        accepted = kyo("dispatch", record_id, *_elsewhere(tmp_path, record_id), "--json")
        assert accepted.code == 0, (
            f"`kyo next` offered {record_id} as ready but `kyo dispatch` refused it: {accepted}"
        )


# ------------------------------------------------------------------------ KYO-105


def test_base_sha_bullet_predicts_both_normal_offsets_and_calls_neither_a_deviation(
    kyo, git_project
) -> None:
    """KYO-105.c1/c3: the Base SHA bullet once said the worktree HEAD "is expected
    to be one commit ahead ... by construction", so a worker branching from the very
    sha in the brief read equality as the anomaly the bullet told it to watch for --
    twice in one session (KYO-97.r1, KYO-99.r1). Zero and one are both normal; only
    behind, or ahead by unrelated commits, is worth a look. Pinned so the wording
    cannot silently revert."""
    record = new_record(kyo, criteria=["a"])
    brief = kyo("--full", "dispatch", record, "--json").json["record"]["dispatches"][0]["brief"]
    bullet = next(line for line in brief.splitlines() if line.startswith("- **Base SHA**:"))

    # both normal cases are predicted, and neither is framed as the deviation
    assert "this exact sha or from one commit ahead" in bullet
    assert "both are normal" in bullet
    assert "expected to be one commit ahead" not in bullet
    assert "by construction" not in bullet
    # what IS worth flagging is named, and it is neither of the normal cases
    assert "*behind* this sha, or ahead of it by unrelated commits" in bullet
    # KYO-10's reason an offset can exist at all survives
    assert "KYO-10" in bullet
    assert "stamps this sha and *then* commits the record of dispatching" in bullet


def test_base_sha_bullet_still_says_read_the_sha_yourself_and_never_copy_it(
    kyo, git_project
) -> None:
    """KYO-105.c2: correcting the offset prediction must not cost the instruction
    that made the two reports measurable in the first place -- `worked_from_sha` is
    read from the worktree, never copied from the brief."""
    record = new_record(kyo, criteria=["a"])
    brief = kyo("--full", "dispatch", record, "--json").json["record"]["dispatches"][0]["brief"]
    bullet = next(line for line in brief.splitlines() if line.startswith("- **Base SHA**:"))

    assert "report the **actual sha you branched from**" in bullet
    assert "read it yourself, e.g. `git rev-parse HEAD`, never copied from this line" in bullet
    assert "as `worked_from_sha` in your structured report" in bullet


# ------------------------------------------------------------------------ KYO-133


def test_terminated_no_report_with_a_record_id_is_refused_not_silently_dispatched(
    kyo, git_project
) -> None:
    """KYO-133.c1: the flag is documented "with 'close'". Passing it beside a record
    id used to be silently ignored, and the command dispatched a brand-new record
    anyway — the maximally unhelpful outcome for a mistyped closure (KYO-128.d2)."""
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--terminated-no-report", "--why", "oops")
    assert result.code == EXIT_USAGE, result
    assert "kyo dispatch close <dispatch-id>" in result.err
    assert "--terminated-no-report" in result.err
    # Nothing was frozen: no second dispatch exists on the record.
    assert kyo("show", record, "--json").json["dispatches"] == []


def test_why_and_why_file_with_a_record_id_are_refused_on_the_same_terms(
    kyo, git_project, tmp_path: Path
) -> None:
    """KYO-133.c2: `--why` and `--why-file` are meaningful only with `close`, same as
    `--terminated-no-report`."""
    record = new_record(kyo, criteria=["a"])
    why_result = kyo("dispatch", record, "--why", "oops")
    assert why_result.code == EXIT_USAGE, why_result
    assert "kyo dispatch close <dispatch-id>" in why_result.err
    assert "--why" in why_result.err
    assert kyo("show", record, "--json").json["dispatches"] == []

    why_file = tmp_path / "why.txt"
    why_file.write_text("oops\n", encoding="utf-8")
    why_file_result = kyo("dispatch", record, "--why-file", str(why_file))
    assert why_file_result.code == EXIT_USAGE, why_file_result
    assert "kyo dispatch close <dispatch-id>" in why_file_result.err
    assert "--why-file" in why_file_result.err
    assert kyo("show", record, "--json").json["dispatches"] == []


def test_all_three_close_only_flags_together_are_named_in_one_refusal(
    kyo, git_project, tmp_path: Path
) -> None:
    """KYO-133.c4: pins all three flags at once, so any of the three becoming
    silently accepted again — individually or together — turns this red."""
    record = new_record(kyo, criteria=["a"])
    why_file = tmp_path / "why.txt"
    why_file.write_text("oops\n", encoding="utf-8")
    result = kyo(
        "dispatch", record, "--terminated-no-report", "--why", "oops",
        "--why-file", str(why_file),
    )
    assert result.code == EXIT_USAGE, result
    for flag in ("--terminated-no-report", "--why", "--why-file"):
        assert flag in result.err
    assert kyo("show", record, "--json").json["dispatches"] == []


def test_refusal_names_the_records_own_running_dispatch(kyo, git_project) -> None:
    """KYO-133.c3: the refusal names the record's currently running dispatch, so the
    controller does not have to look up the id `kyo dispatch close` actually needs."""
    record = new_record(kyo, criteria=["a"])
    opened = kyo("dispatch", record, "--json")
    assert opened.code == 0, opened
    running_id = opened.json["dispatch_id"]

    result = kyo("dispatch", record, "--terminated-no-report", "--why", "oops")
    assert result.code == EXIT_USAGE, result
    assert running_id in result.err
    # still exactly one dispatch: nothing new was frozen
    assert len(kyo("show", record, "--json").json["dispatches"]) == 1


def test_refusal_names_no_dispatch_when_the_record_has_none_running(
    kyo, git_project
) -> None:
    """The flip side of c3: nothing to name is stated as nothing, not guessed at."""
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--terminated-no-report", "--why", "oops")
    assert result.code == EXIT_USAGE, result
    assert "running dispatch" not in result.err


def test_dispatch_close_itself_is_unaffected_by_the_close_only_flag_refusal(
    kyo, git_project
) -> None:
    """The fix must not touch the `close` path at all: `kyo dispatch close <id>
    --terminated-no-report --why ...` is exactly what these flags are for."""
    record = new_record(kyo, criteria=["a"])
    opened = kyo("dispatch", record, "--json")
    dispatch_id = opened.json["dispatch_id"]
    closed = kyo(
        "dispatch", "close", dispatch_id, "--terminated-no-report", "--why", "abandoned", "--json"
    )
    assert closed.code == 0, closed
    assert closed.json["state"] == "terminated-no-report"


# ------------------------------------------------------------------------ KYO-177


def test_show_prints_a_dispatchs_frozen_brief_by_its_dispatch_id(kyo, git_project) -> None:
    """KYO-177.c1/c2: before this, `kyo show <dispatch-id>` answered "no record",
    true of the record table and useless as an answer — the brief was reachable only
    by reading the export JSON by hand. Now the id's own shape dispatches it to a
    real render."""
    record = new_record(kyo, criteria=["a"])
    dispatched = kyo("--full", "dispatch", record, "--json")
    dispatch_id = dispatched.json["dispatch_id"]
    brief = dispatched.json["record"]["dispatches"][0]["brief"]

    shown = kyo("show", dispatch_id)
    assert shown.code == 0, shown
    assert "no record" not in shown.out
    assert "stored brief" in shown.out
    assert f"Dispatch brief: {record}" in shown.out

    shown_json = kyo("show", dispatch_id, "--json")
    assert shown_json.code == 0, shown_json
    assert shown_json.json["object"] == "dispatch"
    assert shown_json.json["id"] == dispatch_id
    assert shown_json.json["record_id"] == record
    assert shown_json.json["state"] == "running"
    assert shown_json.json["brief"] == brief


def test_show_on_an_unknown_dispatch_id_says_no_dispatch_not_no_record(
    kyo, git_project
) -> None:
    """KYO-177.c2: ids are typed (law 6) — an id shaped like a dispatch but not on
    file must read *no dispatch*, never *no record*, which would teach a reader that
    this id space does not exist at all."""
    record = new_record(kyo, criteria=["a"])
    result = kyo("show", f"{record}.d99")
    assert result.code == EXIT_ERROR, result
    assert "no dispatch" in result.err
    assert "no record" not in result.err


def test_show_of_a_dispatch_is_read_only_and_idempotent(kyo, git_project) -> None:
    """KYO-177.c1/c4: running it twice changes nothing, and it is not, and cannot
    quietly become, a store-writing command."""
    assert "show" not in MUTATING_COMMANDS

    record = new_record(kyo, criteria=["a"])
    dispatched = kyo("dispatch", record, "--json")
    dispatch_id = dispatched.json["dispatch_id"]

    before = kyo("show", record, "--json").json
    kyo("show", dispatch_id)
    kyo("show", dispatch_id, "--json")
    after = kyo("show", record, "--json").json
    assert before == after


# ------------------------------------------- KYO-332: a short hand-back request


def test_report_section_asks_for_a_short_final_message(kyo, git_project) -> None:
    """KYO-332.c1/c2: the Report section asks for a final message of a few lines --
    the dispatch id, that the report is written, the branch tip sha, and only what
    the report cannot carry -- and gives its reason in one clause: the report is
    structured and is about to be read. The section's existing sentences (commit
    before stopping, do not commit the report itself, where to send it) stay in
    place beside the new one."""
    record = new_record(kyo, criteria=["a"])
    dispatched = kyo("--full", "dispatch", record, "--json")
    brief = dispatched.json["record"]["dispatches"][0]["brief"]
    report_section = brief.split("## Report")[1]

    assert "Keep your final message to a few lines" in report_section
    assert "the dispatch id" in report_section
    assert "the branch tip sha" in report_section
    assert "only what the report cannot carry" in report_section
    assert "the report is structured and is about to be read" in report_section

    # every existing sentence of the section is still there, unchanged
    assert "Commit your work before you stop" in report_section
    assert "**Do not commit the report itself**" in report_section
    assert "note in your final message that it's ready for" in report_section


def test_the_end_or_wait_out_instruction_is_unchanged(kyo, git_project) -> None:
    """KYO-332.c3: this is a separate worker-audience standing rule ("Keep every
    foreground command under about four minutes"), not part of the Report section
    KYO-332 edits -- it must still reach the brief, worded exactly as the ledger
    wrote it.

    KYO-337 moved that wording, and this assertion with it: the three workers woken
    after hand-back had each ended every process they started, and were
    woken anyway by the notifications of monitors whose runs they had stopped. The
    ledger's sentence now says task rather than process and names the watcher. What
    this test holds is unchanged -- the ledger's own first sentence, whatever it says,
    reaching the brief rather than being summarised away.
    """
    record = new_record(kyo, criteria=["a"])
    dispatched = kyo("--full", "dispatch", record, "--json")
    brief = dispatched.json["record"]["dispatches"][0]["brief"]
    assert (
        "before you hand back, end or stop every background task you started -- "
        "monitors included" in brief
    )
