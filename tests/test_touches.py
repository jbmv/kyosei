"""A touches list is authorable at intake and correctable afterwards.

Two reports from the first consumer project outside this repo. Before this module's
mechanisms existed, ``--touches-file`` lived on ``kyo promote`` alone and
``bug.accepts_from`` was ``[intake]``, so **no bug could ever carry touches** — and no
record of any kind
could have a wrong one corrected. The knock-on cost is what makes it a defect
rather than an inconvenience: ``kyo sweep``'s touch-changed/touch-missing checks
key off ``touches``, and ``kyo dispatch``'s disjointness check treats a record
with no touches as disjoint from everything, so an undeclarable scope reads to
both mechanisms as "nothing to watch, collides with nobody".

Each test here fails if its mechanism is removed (CLAUDE.md's standing rule), and
the two that assert an error message name the remedy that clears it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from kyosei import events_ledger

from .conftest import event_lines, export_of, git_init, new_record


def _touch_paths(kyo, record_id: str) -> list[str]:
    shown = kyo("show", record_id, "--json")
    assert shown.code == 0, shown
    return [t["path"] + (f"#{t['symbol']}" if t["symbol"] else "") for t in shown.json["touches"]]


# ---------------------------------------------------------------- touches at `kyo new`


def test_a_bug_filed_at_intake_can_declare_a_file_scope(kyo, project: Path, write_file) -> None:
    """The reported case exactly: `bug.accepts_from` is `[intake]`, so `kyo promote
    --kind bug` is refused by the registry and `kyo new` is the only door. It now
    carries a scope through that door."""
    (project / "widget.py").write_text("def render():\n    return 1\n", encoding="utf-8")
    result = kyo(
        "new",
        "bug",
        "--title",
        "Widget renders twice",
        "--criteria-file",
        write_file("c.txt", "it renders once\n"),
        "--touches-file",
        write_file("t.txt", "widget.py#render\n"),
        "--json",
        "--full",
    )
    assert result.code == 0, result
    record = result.json["record"]
    assert record["kind"] == "bug"
    assert [t["path"] for t in record["touches"]] == ["widget.py"]
    assert [t["symbol"] for t in record["touches"]] == ["render"]


def test_new_still_files_a_bug_in_one_line_without_touches(kyo) -> None:
    """Zero-ceremony intake (law 12/13) is not taxed by the flag existing."""
    record_id = new_record(kyo, kind="bug", title="Something is wrong")
    assert _touch_paths(kyo, record_id) == []


def test_new_validates_touches_against_the_repo_now(kyo, write_file) -> None:
    """`kyo promote`'s rule, unchanged, applied at the other door: intake is
    zero-ceremony about whether a scope is declared, never about whether a
    declared one is real."""
    result = kyo(
        "new",
        "bug",
        "--title",
        "Bad scope",
        "--touches-file",
        write_file("t.txt", "does/not/exist.py\n"),
    )
    assert result.code == 1, result
    assert "touch validation failed" in result.err
    assert "does/not/exist.py" in result.err


def test_new_validates_a_symbol_that_is_absent_from_the_file(
    kyo, project: Path, write_file
) -> None:
    (project / "widget.py").write_text("def render():\n    return 1\n", encoding="utf-8")
    result = kyo(
        "new",
        "bug",
        "--title",
        "Bad symbol",
        "--touches-file",
        write_file("t.txt", "widget.py#teleport\n"),
    )
    assert result.code == 1, result
    assert "'teleport' not found in widget.py" in result.err


# --------------------------------------------------------------- touches at `kyo edit`


def test_edit_replaces_the_whole_touches_list(kyo, project: Path, write_file) -> None:
    """A consumer record's shape: one of the declared files had no role in the work, and the
    file the change actually lived in was never named. Correcting that means
    dropping a path as well as adding one, which is why this replaces rather than
    appends."""
    for name in ("page_host.py", "doh.py", "banner.py"):
        (project / name).write_text("x = 1\n", encoding="utf-8")
    record_id = new_record(kyo, kind="bug", title="Move the banner")
    kyo("edit", record_id, "--touches-file", write_file("t1.txt", "page_host.py\nbanner.py\n"))
    assert _touch_paths(kyo, record_id) == ["page_host.py", "banner.py"]

    result = kyo(
        "edit", record_id, "--touches-file", write_file("t2.txt", "banner.py\ndoh.py\n"), "--json"
    )
    assert result.code == 0, result
    assert _touch_paths(kyo, record_id) == ["banner.py", "doh.py"]


def test_edit_touches_never_reissues_a_touch_id(kyo, project: Path, write_file) -> None:
    """Law 4. Replacing deletes rows, and ``allocations`` counters are monotonic —
    ``rebuild`` reads them from ``_allocations.json`` rather than ``max(seq)``
    precisely so a deleted high-numbered row cannot let the next one collide."""
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")
    (project / "b.py").write_text("x = 1\n", encoding="utf-8")
    record_id = new_record(kyo, kind="bug", title="Scope churn")

    seen: set[str] = set()
    for i, content in enumerate(["a.py\n", "b.py\n", "a.py\nb.py\n"]):
        kyo("edit", record_id, "--touches-file", write_file(f"t{i}.txt", content))
        ids = {t["id"] for t in kyo("show", record_id, "--json").json["touches"]}
        assert not (ids & seen), f"touch id reissued: {ids & seen}"
        seen |= ids


def test_the_prior_touches_list_survives_in_the_events_ledger(
    kyo, project: Path, write_file
) -> None:
    """The correction carries provenance rather than replacing history silently:
    the pre-edit document is the event's ``before``, so what the scope *was* stays
    readable forever without keeping dead rows in the table."""
    (project / "wrong.py").write_text("x = 1\n", encoding="utf-8")
    (project / "right.py").write_text("x = 1\n", encoding="utf-8")
    record_id = new_record(kyo, kind="bug", title="Mis-scoped")
    kyo("edit", record_id, "--touches-file", write_file("t1.txt", "wrong.py\n"))
    kyo("edit", record_id, "--touches-file", write_file("t2.txt", "right.py\n"))

    events = event_lines(project)
    # KYO-418: the prior list is in `changed` on a line written from 25 on; read
    # through the one helper that answers for either format.
    corrections = [
        e
        for e in events
        if e["record_id"] == record_id
        and (prior := events_ledger.event_prior(e)) is not None
        and [t["path"] for t in prior.get("touches") or []] == ["wrong.py"]
    ]
    assert corrections, "the pre-correction touches list is not recoverable from the ledger"
    assert [t["path"] for t in corrections[0]["after"]["touches"]] == ["right.py"]


def test_an_empty_touches_file_clears_the_list(kyo, project: Path, write_file) -> None:
    """Law 6's established reading of an empty file, same as ``--description-file``:
    an empty file means an empty list, not "no change"."""
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")
    record_id = new_record(kyo, kind="bug", title="Scope goes away")
    kyo("edit", record_id, "--touches-file", write_file("t1.txt", "a.py\n"))
    result = kyo("edit", record_id, "--touches-file", write_file("t2.txt", ""), "--json")
    assert result.code == 0, result
    assert _touch_paths(kyo, record_id) == []


def test_edit_touches_alone_is_a_real_change(kyo, project: Path, write_file) -> None:
    """`kyo edit` refuses a call that changes nothing; --touches-file must count,
    or correcting only the scope is impossible."""
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")
    record_id = new_record(kyo, kind="bug", title="Only the scope changes")
    result = kyo("edit", record_id, "--touches-file", write_file("t.txt", "a.py\n"))
    assert result.code == 0, result
    assert "needs something to change" not in result.err


def test_edit_refuses_a_bad_touch_and_leaves_the_old_list_intact(
    kyo, project: Path, write_file
) -> None:
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")
    record_id = new_record(kyo, kind="bug", title="Keeps its scope")
    kyo("edit", record_id, "--touches-file", write_file("t1.txt", "a.py\n"))
    result = kyo("edit", record_id, "--touches-file", write_file("t2.txt", "gone.py\n"))
    assert result.code == 1, result
    assert "touch validation failed" in result.err
    assert _touch_paths(kyo, record_id) == ["a.py"], "a refused edit must not clear the list"


# ------------------------------------------- the knock-on mechanisms of a bug's scope


def test_sweep_now_flags_a_changed_touch_on_a_bug(kyo, project: Path, write_file) -> None:
    """The consequence that made this a defect: ``bug`` is a fast-decaying kind
    (``swept: true``), and before touches were reachable at intake, sweep's touch
    checks could never fire for a single one."""
    target = project / "symptom.py"
    target.write_text("x = 1\n", encoding="utf-8")
    git_init(project)
    record_id = new_record(kyo, kind="bug", title="Fast-decaying symptom")
    assert kyo("edit", record_id, "--touches-file", write_file("t.txt", "symptom.py\n")).code == 0

    baseline = kyo("sweep", "--json")
    assert baseline.json["flags"] == [], "first sweep just records a baseline"

    target.write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "symptom moved"], cwd=project, check=True, capture_output=True
    )
    result = kyo("sweep", "--json")
    assert result.code == 0, result
    flags = [f for f in result.json["flags"] if f["record_id"] == record_id]
    assert flags, "sweep found nothing to flag on a bug whose touched file changed"
    assert flags[0]["flag_type"] == "touch-changed"


def test_dispatch_disjointness_now_sees_a_bugs_touches(kyo, project: Path, write_file) -> None:
    """And the other half: a record with no touches reads as disjoint from
    everything, so two workers could be dispatched onto the same file with the
    tool reporting no collision."""
    (project / "shared.py").write_text("x = 1\n", encoding="utf-8")
    git_init(project)
    first = new_record(kyo, kind="bug", title="First on the file", criteria=["it works"])
    second = new_record(kyo, kind="bug", title="Second on the file", criteria=["it works"])
    for record_id in (first, second):
        scope = write_file(f"{record_id}.txt", "shared.py\n")
        assert kyo("edit", record_id, "--touches-file", scope).code == 0

    assert kyo("dispatch", first, "--json").code == 0
    result = kyo("dispatch", second)
    assert result.code == 2, result
    assert "shared.py" in result.err


# ------------------------------------------------------------------- round-tripping


def test_touches_set_at_intake_survive_a_rebuild(kyo, project: Path, write_file) -> None:
    (project / "widget.py").write_text("x = 1\n", encoding="utf-8")
    record_id = new_record(kyo, kind="bug", title="Round trip")
    kyo("edit", record_id, "--touches-file", write_file("t.txt", "widget.py\n"))
    exported = json.loads(export_of(project, record_id).read_text())
    assert exported["touches"][0]["path"] == "widget.py"

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert _touch_paths(kyo, record_id) == ["widget.py"]


# ===================================================================== KYO-209
#
# The same table, one shape of scope later. `_validate_touches` refused any path that
# did not resolve to a file, which is what makes a stale idea fail at promotion rather
# than three dispatches later — and which left work that CREATES a tree with nothing to
# name. A consumer report hit it filing four desktop-app stage records against an
# `apps/desktop/` that had no files: the orchestrator committed a stub README.md only so
# the records could name it, and every dispatch then reported that the Touches table
# did not cover what the task required.
#
# The refusal cannot simply go away — it is the typo check. So intent is declared in
# the path: a trailing `/` says "directory, existing or not". A mistyped filename does
# not grow one by accident, which is the whole reason this form and not a heuristic.


def _dir_touch(kyo, write_file, record_id: str, *lines: str):
    return kyo(
        "edit", record_id, "--touches-file", write_file("dt.txt", "".join(f"{p}\n" for p in lines))
    )


# ------------------------------------------------------------------------ KYO-209.c1


def test_a_directory_touch_is_accepted_before_the_directory_exists(
    kyo, project: Path, write_file
) -> None:
    """The consumer's case exactly: nothing under `apps/desktop/` exists — the
    directory itself does not exist — and the record can still declare it."""
    assert not (project / "apps").exists()
    result = kyo("--full",
        "new",
        "bug",
        "--title",
        "The desktop app has no window chrome",
        "--criteria-file",
        write_file("c.txt", "it has window chrome\n"),
        "--touches-file",
        write_file("t.txt", "apps/desktop/\n"),
        "--json",
    )
    assert result.code == 0, result
    assert [t["path"] for t in result.json["record"]["touches"]] == ["apps/desktop/"]
    # The slash is part of the STORED value, not decoration stripped after validation:
    # every reader downstream tells a directory from a file by exactly this character.
    assert [t["symbol"] for t in result.json["record"]["touches"]] == [None]


def test_promote_spawn_and_edit_take_the_directory_form_too(
    kyo, project: Path, write_file
) -> None:
    """KYO-209.c1 names four doors. `kyo new` is above; these are the other three,
    because a form one door accepts and another refuses is worse than no form."""
    idea = project / "ideas" / "desktop.md"
    idea.write_text("# Build the desktop app\n\nIt does not exist yet.\n", encoding="utf-8")
    promoted = kyo(
        "promote",
        str(idea),
        "--kind",
        "chore",
        "--criteria-file",
        write_file("pc.txt", "the app builds\n"),
        "--touches-file",
        write_file("pt.txt", "apps/desktop/\n"),
        "--json",
    )
    assert promoted.code == 0, promoted
    record_id = promoted.json["record"]["id"]
    assert _touch_paths(kyo, record_id) == ["apps/desktop/"]

    source = new_record(kyo, kind="bug", title="The desktop app has no packaging")
    spawned = kyo(
        "spawn",
        source,
        "--kind",
        "task",
        "--title",
        "Package the desktop app",
        "--criteria-file",
        write_file("sc.txt", "it packages\n"),
        "--touches-file",
        write_file("st.txt", "apps/desktop/dist/\n"),
        "--json",
    )
    assert spawned.code == 0, spawned
    assert _touch_paths(kyo, spawned.json["record"]["id"]) == ["apps/desktop/dist/"]

    assert _dir_touch(kyo, write_file, record_id, "apps/desktop/", "apps/shared/").code == 0
    assert _touch_paths(kyo, record_id) == ["apps/desktop/", "apps/shared/"]


def test_a_plain_missing_path_is_still_refused_the_way_it_always_was(
    kyo, write_file
) -> None:
    """The check this record is about must not have been weakened into a shrug: the
    typo case is the reason it exists, and its message is unchanged."""
    result = kyo(
        "new",
        "bug",
        "--title",
        "Bad scope",
        "--touches-file",
        write_file("t.txt", "apps/desktop/main.js\n"),
    )
    assert result.code == 1, result
    assert "touch validation failed" in result.err
    assert "apps/desktop/main.js: no such file relative to" in result.err


def test_the_directory_remedy_the_missing_path_names_actually_clears_it(
    kyo, write_file
) -> None:
    """CLAUDE.md: every error message naming a remedy ships with a test proving the
    remedy clears the error. The refusal above now names the directory form, which is
    the whole point — the consumer committed a stub file because nothing told them
    there was another way."""
    refused = kyo(
        "new",
        "bug",
        "--title",
        "Bad scope",
        "--touches-file",
        write_file("t1.txt", "apps/desktop/main.js\n"),
    )
    assert refused.code == 1, refused
    assert "if this work CREATES that path" in refused.err
    assert "apps/desktop/" in refused.err

    cleared = kyo("--full",
        "new",
        "bug",
        "--title",
        "Good scope",
        "--touches-file",
        write_file("t2.txt", "apps/desktop/\n"),
        "--json",
    )
    assert cleared.code == 0, cleared
    assert [t["path"] for t in cleared.json["record"]["touches"]] == ["apps/desktop/"]


def test_a_directory_touch_takes_no_symbol_and_says_how_to_get_one(
    kyo, project: Path, write_file
) -> None:
    """A directory has no symbols to match against, and `contains_phrase` has no file
    to read. Refused rather than silently dropped: `apps/desktop/#render` is somebody
    meaning one of two different things, and guessing which is how a claim ends up
    wider than anybody declared."""
    record_id = new_record(kyo, kind="bug", title="Symbol on a tree")
    refused = _dir_touch(kyo, write_file, record_id, "apps/desktop/#render")
    assert refused.code == 1, refused
    assert "a directory touch claims a whole tree and takes no '#symbol'" in refused.err

    assert _dir_touch(kyo, write_file, record_id, "apps/desktop/").code == 0
    assert _touch_paths(kyo, record_id) == ["apps/desktop/"]

    # And the other half of the remedy, on a file that does exist.
    (project / "widget.py").write_text("def render():\n    return 1\n", encoding="utf-8")
    assert _dir_touch(kyo, write_file, record_id, "widget.py#render").code == 0
    assert _touch_paths(kyo, record_id) == ["widget.py#render"]


def test_a_directory_touch_may_not_be_the_project_root(kyo, write_file) -> None:
    """A claim over everything collides with every other dispatch and tells a worker
    nothing about its boundary. The remedy the message names is the subdirectory."""
    record_id = new_record(kyo, kind="bug", title="Everything")
    refused = _dir_touch(kyo, write_file, record_id, "./")
    assert refused.code == 1, refused
    assert "a touch is a working scope, not the whole project" in refused.err

    assert _dir_touch(kyo, write_file, record_id, "apps/").code == 0
    assert _touch_paths(kyo, record_id) == ["apps/"]


def test_a_directory_touch_outside_the_project_root_is_refused(kyo, write_file) -> None:
    """KYO-52's rule survives the new form: every stored path is root-relative, so a
    directory touch that resolves upward would put `../` in the git-canonical export —
    which existence checking used to make impossible by accident."""
    record_id = new_record(kyo, kind="bug", title="Elsewhere")
    refused = _dir_touch(kyo, write_file, record_id, "../somewhere-else/")
    assert refused.code == 1, refused
    assert "resolves outside the project root" in refused.err


def test_a_directory_form_over_an_existing_file_is_refused(
    kyo, project: Path, write_file
) -> None:
    """The one case where the form is checkable and wrong. Left silent it would store
    `widget.py/` as a tree claim covering `widget.py/anything`, which nothing can ever
    put a file in."""
    (project / "widget.py").write_text("x = 1\n", encoding="utf-8")
    record_id = new_record(kyo, kind="bug", title="Slash on a file")
    refused = _dir_touch(kyo, write_file, record_id, "widget.py/")
    assert refused.code == 1, refused
    assert "names a file, not a directory" in refused.err

    assert _dir_touch(kyo, write_file, record_id, "widget.py").code == 0
    assert _touch_paths(kyo, record_id) == ["widget.py"]


def test_the_trailing_slash_survives_export_and_rebuild(
    kyo, project: Path, write_file
) -> None:
    """The stored form IS the declaration. A rebuild that normalised the slash away
    would turn every directory claim in the project into a file claim on a path that
    does not exist — silently, and only on the machine that ran the rebuild."""
    record_id = new_record(kyo, kind="bug", title="Round trip a tree")
    assert _dir_touch(kyo, write_file, record_id, "apps/desktop/").code == 0
    exported = json.loads(export_of(project, record_id).read_text())
    assert exported["touches"][0]["path"] == "apps/desktop/"

    (project / "kyosei" / "kyosei.db").unlink()
    assert kyo("rebuild").code == 0
    assert _touch_paths(kyo, record_id) == ["apps/desktop/"]


# ------------------------------------------------------------------------ KYO-209.c3


def test_dispatch_disjointness_sees_a_file_under_a_declared_directory(
    kyo, project: Path, write_file
) -> None:
    """Set intersection answers "disjoint" here, which is the one answer that is
    catastrophic: two workers in the same tree, told they were alone."""
    (project / "apps").mkdir()
    (project / "apps" / "main.js").write_text("x = 1\n", encoding="utf-8")
    git_init(project)
    tree = new_record(kyo, kind="bug", title="Owns the tree", criteria=["it works"])
    one_file = new_record(kyo, kind="bug", title="Owns one file in it", criteria=["it works"])
    assert _dir_touch(kyo, write_file, tree, "apps/").code == 0
    assert kyo("edit", one_file, "--touches-file", write_file("f.txt", "apps/main.js\n")).code == 0

    assert kyo("dispatch", tree, "--json").code == 0
    result = kyo("dispatch", one_file)
    assert result.code == 2, result
    # Both sides of the overlap are named: a controller handed only `apps/` cannot
    # find that string in the second record's touches at all (KYO-65.c2's lesson).
    assert "apps/" in result.err
    assert "apps/main.js" in result.err


def test_a_directory_that_does_not_exist_yet_raises_no_sweep_flag(
    kyo, project: Path, write_file
) -> None:
    """KYO-209.c3. `touch-missing` means "the file you declared is gone, so this
    record's premise may be": applied to a tree the work has not built yet it would
    fire on every sweep, for the whole life of the record, on every record filed this
    way — the exact noise that trains a controller to stop reading flags."""
    git_init(project)
    record_id = new_record(kyo, kind="bug", title="Builds a tree")
    assert _dir_touch(kyo, write_file, record_id, "apps/desktop/").code == 0

    result = kyo("sweep", "--json")
    assert result.code == 0, result
    assert [f for f in result.json["flags"] if f["record_id"] == record_id] == []


def test_sweep_flags_a_directory_touch_when_a_file_under_it_changes(
    kyo, project: Path, write_file
) -> None:
    """The other half of c3: not flagging is only right when nothing happened. What
    the flag NAMES is the declared line, because that is what a controller can find in
    the record's touches table."""
    git_init(project)
    record_id = new_record(kyo, kind="bug", title="Owns the tree")
    assert _dir_touch(kyo, write_file, record_id, "apps/").code == 0
    assert kyo("sweep", "--json").json["flags"] == [], "first sweep just records a baseline"

    (project / "apps").mkdir()
    (project / "apps" / "main.js").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "the tree gains a file"],
        cwd=project,
        check=True,
        capture_output=True,
    )

    result = kyo("sweep", "--json")
    assert result.code == 0, result
    flags = [f for f in result.json["flags"] if f["record_id"] == record_id]
    assert flags, "a file appeared under the declared tree and sweep said nothing"
    assert flags[0]["flag_type"] == "touch-changed"
    assert "apps/" in flags[0]["detail"]


# ------------------------------------------------------------------------ KYO-209.c2


def test_the_brief_renders_a_directory_touch_as_scope_the_work_creates(
    kyo, project: Path, write_file
) -> None:
    """KYO-209.c2's first half. A row reading `apps/desktop/ | (whole file)` sends a
    worker looking for a file, which is the consumer's failure moved one step later —
    so the symbol column says what the row IS, and a note under the table says the
    tree may not exist yet."""
    git_init(project)
    record_id = new_record(kyo, kind="bug", title="Builds the desktop app", criteria=["it runs"])
    assert _dir_touch(kyo, write_file, record_id, "apps/desktop/").code == 0

    result = kyo("--full", "dispatch", record_id, "--json")
    assert result.code == 0, result
    brief = result.json["record"]["dispatches"][0]["brief"]
    assert "| apps/desktop/ | (the whole tree, including files this work creates) |" in brief
    assert "directory touch" in brief
    assert "It is not a missing file." in brief


def test_the_remedy_names_the_parent_tree_of_the_path_that_was_refused(
    kyo, write_file
) -> None:
    """A remedy the reader has to translate is a remedy half of them get wrong, so the
    example is the refused path's own parent. A path with no parent gets the rule and
    no example — the only tree above it is the project root, and that is refused the
    directory form deliberately, so suggesting it would be a remedy that fails."""
    nested = kyo(
        "new", "bug", "--title", "x", "--touches-file", write_file("t1.txt", "a/b/c.js\n")
    )
    assert nested.code == 1, nested
    assert "(e.g. a/b/)" in nested.err

    bare = kyo("new", "bug", "--title", "x", "--touches-file", write_file("t2.txt", "c.js\n"))
    assert bare.code == 1, bare
    assert "if this work CREATES that path" in bare.err
    assert "(e.g." not in bare.err


# ===================================================================== KYO-225
#
# KYO-209.c1 named two forms -- "a directory with a trailing slash, OR AN EXPLICIT
# MARKER" -- and shipped the first. A consumer report found every worker of a wave
# reporting touch drift, because a new file could not be declared as a touch until it
# existed. The directory form is not the answer for a file that
# lands beside a dozen existing ones: claiming the whole directory overstates the scope
# and collides with every other dispatch under it.


def _new_file_touch(kyo, write_file, record_id: str, *lines: str):
    return kyo(
        "edit", record_id, "--touches-file", write_file("nt.txt", "".join(f"{p}\n" for p in lines))
    )


# ------------------------------------------------------------------------ KYO-225.c1


def test_a_new_file_touch_is_accepted_before_the_file_exists(
    kyo, project: Path, write_file
) -> None:
    """The consumer's case: the work adds one module beside existing ones, and the
    record can say so without claiming the whole directory."""
    (project / "src").mkdir()
    (project / "src" / "existing.py").write_text("x = 1\n", encoding="utf-8")
    assert not (project / "src" / "new_module.py").exists()
    result = kyo("--full",
        "new", "bug", "--title", "The widget needs its own module",
        "--criteria-file", write_file("c.txt", "it has one\n"),
        "--touches-file", write_file("t.txt", "+src/new_module.py\nsrc/existing.py\n"),
        "--json",
    )
    assert result.code == 0, result
    # The marker is part of the STORED value, like the directory suffix and for the
    # same reason: every reader downstream tells the two apart by exactly this.
    assert [t["path"] for t in result.json["record"]["touches"]] == [
        "+src/new_module.py", "src/existing.py",
    ]
    assert [t["symbol"] for t in result.json["record"]["touches"]] == [None, None]


def test_promote_spawn_and_edit_take_the_new_file_form_too(
    kyo, project: Path, write_file
) -> None:
    """KYO-225.c1 names the same four doors KYO-209.c1 did, for the same reason: a
    form one door accepts and another refuses is worse than no form."""
    idea = project / "ideas" / "module.md"
    idea.write_text("# Add a module\n\nIt does not exist yet.\n", encoding="utf-8")
    promoted = kyo(
        "promote", str(idea), "--kind", "chore",
        "--criteria-file", write_file("pc.txt", "it imports\n"),
        "--touches-file", write_file("pt.txt", "+src/new_module.py\n"),
        "--json",
    )
    assert promoted.code == 0, promoted
    record_id = promoted.json["record"]["id"]
    assert _touch_paths(kyo, record_id) == ["+src/new_module.py"]

    source = new_record(kyo, kind="bug", title="The module has no tests")
    spawned = kyo(
        "spawn", source, "--kind", "task", "--title", "Test the module",
        "--criteria-file", write_file("sc.txt", "it is tested\n"),
        "--touches-file", write_file("st.txt", "+tests/test_new_module.py\n"),
        "--json",
    )
    assert spawned.code == 0, spawned
    assert _touch_paths(kyo, spawned.json["record"]["id"]) == ["+tests/test_new_module.py"]

    assert _new_file_touch(kyo, write_file, record_id, "+src/a.py", "+src/b.py").code == 0
    assert _touch_paths(kyo, record_id) == ["+src/a.py", "+src/b.py"]


def test_the_missing_path_refusal_names_both_remedies_and_both_clear_it(
    kyo, write_file
) -> None:
    """c1. The unmarked path is still refused — that is the typo case, and the whole
    value of the check — and the refusal now names the two forms that declare intent.
    CLAUDE.md: every message naming a remedy ships with a test proving it clears."""
    refused = kyo(
        "new", "bug", "--title", "Bad scope",
        "--touches-file", write_file("t1.txt", "apps/desktop/main.js\n"),
    )
    assert refused.code == 1, refused
    assert "touch validation failed" in refused.err
    assert "apps/desktop/main.js: no such file relative to" in refused.err
    assert "+apps/desktop/main.js" in refused.err, "the new-file remedy is not named"
    assert "(e.g. apps/desktop/)" in refused.err, "the directory remedy is not named"

    by_file = kyo("--full",
        "new", "bug", "--title", "One file",
        "--touches-file", write_file("t2.txt", "+apps/desktop/main.js\n"),
        "--json",
    )
    assert by_file.code == 0, by_file
    assert [t["path"] for t in by_file.json["record"]["touches"]] == ["+apps/desktop/main.js"]

    by_tree = kyo(
        "new", "bug", "--title", "One tree",
        "--touches-file", write_file("t3.txt", "apps/desktop/\n"),
        "--json",
    )
    assert by_tree.code == 0, by_tree


def test_a_new_file_touch_takes_no_symbol_and_says_why(kyo, write_file) -> None:
    """There is no file to find the symbol in — which is the whole premise of the
    form — so the refusal says that rather than reporting the symbol as absent."""
    result = kyo(
        "new", "bug", "--title", "x",
        "--touches-file", write_file("t.txt", "+src/new_module.py#render\n"),
    )
    assert result.code == 1, result
    assert "nothing to find 'render' in" in result.err
    assert "drop the '#render'" in result.err


def test_the_marker_on_a_path_that_is_already_there_is_refused(
    kyo, project: Path, write_file
) -> None:
    """A `+` on an existing file claims to create something that exists. The two say
    different things, and a controller reading the record back deserves the true one."""
    (project / "widget.py").write_text("x = 1\n", encoding="utf-8")
    result = kyo(
        "new", "bug", "--title", "x", "--touches-file", write_file("t.txt", "+widget.py\n")
    )
    assert result.code == 1, result
    assert "already there, so this work does not create it" in result.err
    cleared = kyo(
        "new", "bug", "--title", "x", "--touches-file", write_file("t2.txt", "widget.py\n"),
        "--json",
    )
    assert cleared.code == 0, cleared


def test_the_marker_on_a_directory_form_is_refused_as_redundant(kyo, write_file) -> None:
    """`+apps/desktop/` is two markers for one intent, and the directory form already
    carries it. The refusal names both ways out rather than picking one."""
    result = kyo(
        "new", "bug", "--title", "x", "--touches-file", write_file("t.txt", "+apps/desktop/\n")
    )
    assert result.code == 1, result
    assert "a directory touch is already accepted" in result.err
    assert "`apps/desktop/`" in result.err


def test_a_new_file_touch_outside_the_project_root_is_refused(kyo, write_file) -> None:
    result = kyo(
        "new", "bug", "--title", "x", "--touches-file", write_file("t.txt", "+../outside.py\n")
    )
    assert result.code == 1, result
    assert "resolves outside the project root" in result.err


def test_the_marker_survives_export_and_rebuild(kyo, project: Path, write_file) -> None:
    """The stored form is what every reader keys off, so it has to survive the round
    trip through the git-canonical export the database is rebuilt from."""
    record_id = new_record(kyo, kind="bug", title="Adds a module")
    assert _new_file_touch(kyo, write_file, record_id, "+src/new_module.py").code == 0
    document = json.loads(export_of(project, record_id).read_text(encoding="utf-8"))
    assert [t["path"] for t in document["touches"]] == ["+src/new_module.py"]

    assert kyo("rebuild", "--force").code == 0
    assert _touch_paths(kyo, record_id) == ["+src/new_module.py"]


# ------------------------------------------------------------------------ KYO-225.c2


def test_the_brief_renders_a_new_file_touch_as_a_file_this_work_creates(
    kyo, project: Path, write_file
) -> None:
    """c2's first half. A row reading `src/new_module.py | (whole file)` sends a worker
    looking for a file that is not there — the consumer's failure moved one step on."""
    git_init(project)
    record_id = new_record(kyo, kind="bug", title="Adds a module", criteria=["it imports"])
    assert _new_file_touch(kyo, write_file, record_id, "+src/new_module.py").code == 0

    result = kyo("--full", "dispatch", record_id, "--json")
    assert result.code == 0, result
    brief = result.json["record"]["dispatches"][0]["brief"]
    assert "| +src/new_module.py | (a file this work creates) |" in brief
    assert "new-file touch" in brief
    assert "not drift and not a missing file" in brief


def test_two_dispatches_declaring_the_same_new_file_collide(
    kyo, project: Path, write_file
) -> None:
    """c2's last clause. The marker says something about the touch and nothing about
    what it covers, so two records claiming to create one file are claiming one file."""
    git_init(project)
    first = new_record(kyo, kind="bug", title="Adds the module", criteria=["a"])
    second = new_record(kyo, kind="bug", title="Also adds the module", criteria=["a"])
    assert kyo(
        "edit", first, "--touches-file", write_file("t1.txt", "+src/new_module.py\n")
    ).code == 0
    assert kyo(
        "edit", second, "--touches-file", write_file("t2.txt", "+src/new_module.py\n")
    ).code == 0

    assert kyo("dispatch", first, "--json").code == 0
    clash = kyo("dispatch", second)
    assert clash.code == 2, clash
    assert "src/new_module.py" in clash.err

    # And the marked and unmarked forms of one path are one path: `contracts.touch_covers`
    # compares targets, so a record that will create the file and a record that edits it
    # are not two unrelated strings.
    from kyosei import contracts

    assert contracts.touch_covers("+src/new_module.py", "src/new_module.py")
    assert contracts.touch_covers("src/new_module.py", "+src/new_module.py")
    assert contracts.shared_touch_paths({"+src/x.py"}, {"src/x.py"})


def test_a_new_file_that_does_not_exist_yet_raises_no_sweep_flag(
    kyo, project: Path, write_file
) -> None:
    """c2. `touch-missing` means "the file you declared is gone"; applied to a file the
    work has not written yet it would fire on every sweep for the life of the record."""
    git_init(project)
    record_id = new_record(kyo, kind="bug", title="Adds a module")
    assert _new_file_touch(kyo, write_file, record_id, "+src/new_module.py").code == 0

    result = kyo("sweep", "--json")
    assert result.code == 0, result
    assert [f for f in result.json["flags"] if f["record_id"] == record_id] == []


# ------------------------------------------------------------------------ KYO-225.c3


def test_sweep_does_not_flag_the_commit_that_creates_the_declared_file(
    kyo, project: Path, write_file
) -> None:
    """c3. The commit that creates the file is the record's declared output arriving,
    not the ground moving under it. A LATER edit to the same file still flags."""
    git_init(project)
    record_id = new_record(kyo, kind="bug", title="Adds a module")
    assert _new_file_touch(kyo, write_file, record_id, "+src/new_module.py").code == 0
    assert kyo("sweep", "--json").json["flags"] == [], "first sweep just records a baseline"

    (project / "src").mkdir(exist_ok=True)
    (project / "src" / "new_module.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "the work lands"],
        cwd=project, check=True, capture_output=True,
    )
    created = kyo("sweep", "--json")
    assert created.code == 0, created
    assert [f for f in created.json["flags"] if f["record_id"] == record_id] == [], (
        "the record was flagged for its own declared output arriving"
    )

    (project / "src" / "new_module.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "somebody else edits it"],
        cwd=project, check=True, capture_output=True,
    )
    edited = kyo("sweep", "--json")
    flags = [f for f in edited.json["flags"] if f["record_id"] == record_id]
    assert flags, "an edit to the declared file after it landed is real staleness"
    assert flags[0]["flag_type"] == "touch-changed"
    assert "+src/new_module.py" in flags[0]["detail"]


def test_show_renders_a_landed_new_file_touch_as_an_ordinary_file_touch(
    kyo, project: Path, write_file
) -> None:
    """c3. "Reads as an ordinary file touch WITHOUT AN EDIT": the path column is the
    path in both states, and the note is there only while the file is still to come."""
    record_id = new_record(kyo, kind="bug", title="Adds a module")
    assert _new_file_touch(kyo, write_file, record_id, "+src/new_module.py").code == 0

    pending = kyo("show", record_id).out
    assert "src/new_module.py  (a file this work creates)" in pending
    assert "+src/new_module.py" not in pending, "the marker is not part of the path"

    (project / "src").mkdir(exist_ok=True)
    (project / "src" / "new_module.py").write_text("x = 1\n", encoding="utf-8")
    landed = kyo("show", record_id).out
    landed_line = next(
        line for line in landed.splitlines() if "src/new_module.py" in line and ".t" in line
    )
    assert landed_line.strip().endswith("src/new_module.py"), landed_line
    # Nothing was edited to make that happen.
    assert _touch_paths(kyo, record_id) == ["+src/new_module.py"]
