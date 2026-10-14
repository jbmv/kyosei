"""KYO-300: symbol-aware sweep and drift, and the additive touch claim.

``path#symbol`` touches have existed since KYO-66 and exactly one reader was precise
about them — the disjointness check. Everything that asked "has this record's ground
moved" compared FILES, so this store once held 1932 touches, 73 of them naming a symbol,
and 116 unresolved ``touch-changed`` flags: README.md was named by 64 records and cli.py
by 64, and any edit anywhere in either re-flagged every one of them.

The two things built here, and the property that decides every ambiguous case:

* **c1/c2** — ``kyo sweep`` and ``kyo touch-drift`` narrow by symbol where they can,
  against a textual approximation of the region a symbol occupies. Textual because this
  tool sweeps Python, TypeScript, YAML, CSS and prose in one run and a language parser
  would answer for one of them. **Anything the approximation cannot place counts as
  overlapping**: a false "this change does not concern you" is a staleness flag that
  never fires and nothing says so, while a false positive is one line a controller
  dismisses. Half the tests below are about that direction, not about the narrowing.
* **c3/c4** — a touch can claim an ADDITION (``path#+``), for the ordinary case of a
  file that grows by addition where the thing being added has no name yet. It rides the
  existing ``touches.symbol`` column, so there is no stored field and no export change
  and therefore no ``schema_version`` bump; and it is held to, or it would be a claim of
  disjointness with no evidence behind it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from kyosei import sweep, touch_drift

from .conftest import git_init, new_record

# --------------------------------------------------------------------------- helpers


def _git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=project, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(project: Path, message: str) -> str:
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", message)
    return _git(project, "rev-parse", "HEAD")


def _declare(kyo, project: Path, record: str, *lines: str) -> None:
    # Written OUTSIDE the project: a scratch file inside it would be committed by the
    # next `_commit` and then show up as drift in every finding below.
    scope = project.parent / f"{record}-touches.txt"
    scope.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    result = kyo("edit", record, "--touches-file", str(scope))
    assert result.code == 0, result


def _promote(kyo, project: Path, *, name: str, touches: list[str]) -> str:
    idea = project / "ideas" / f"{name}.md"
    idea.parent.mkdir(parents=True, exist_ok=True)
    idea.write_text(f"# {name}\n\nDo the thing.\n", encoding="utf-8")
    criteria = project.parent / f"{name}-criteria.txt"
    criteria.write_text("it works\n", encoding="utf-8")
    scope = project.parent / f"{name}-touches.txt"
    scope.write_text("".join(line + "\n" for line in touches), encoding="utf-8")
    result = kyo(
        "promote",
        str(idea),
        "--kind",
        "chore",
        "--criteria-file",
        str(criteria),
        "--touches-file",
        str(scope),
        "--json",
    )
    assert result.code == 0, result
    return result.json["record"]["id"]


#: Two top-level functions, far enough apart that an edit to one is nowhere near the
#: other — the shape the whole narrowing exists for.
MODULE = """\
def alpha():
    a = 1
    return a


def beta():
    b = 2
    return b


def gamma():
    c = 3
    return c
"""

DOCUMENT = """\
# Title

Opening prose.

## Alpha section

Text under alpha.

## Beta section

Text under beta.
"""


def _edit_function(path: Path, name: str, body: str) -> None:
    """Rewrite one function's body, leaving every other line byte-identical."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith(f"def {name}("):
            lines[index + 1] = f"    {body}\n"
            break
    else:  # pragma: no cover - a test that edits nothing is a broken test
        raise AssertionError(f"no def {name} in {path}")
    path.write_text("".join(lines), encoding="utf-8")


# ------------------------------------------------- the approximation, stated and tested


def test_a_markdown_headings_region_is_its_section_exactly() -> None:
    regions = sweep.symbol_regions(DOCUMENT, "Alpha section")
    assert regions == [sweep.LineRange(5, 8)]
    lines = DOCUMENT.splitlines()
    assert lines[4] == "## Alpha section"
    # Through to the line before the next heading at the same depth, and not one line
    # further: `## Beta section` belongs to Beta.
    assert lines[8] == "## Beta section"


def test_a_shallower_heading_ends_a_deeper_ones_section() -> None:
    text = "# Top\n\n## Inner\n\nbody\n\n# Next top\n\nmore\n"
    assert sweep.symbol_regions(text, "Inner") == [sweep.LineRange(3, 6)]


def test_a_code_symbols_region_runs_to_the_next_definition_at_the_same_depth() -> None:
    assert sweep.symbol_regions(MODULE, "beta") == [sweep.LineRange(6, 10)]
    lines = MODULE.splitlines()
    assert lines[5] == "def beta():"
    # Through the blank lines that separate it from the next definition, and stopping
    # at that definition: the trailing blanks belong to nobody and including them
    # costs nothing, while excluding `def gamma():` is the whole point.
    assert lines[10] == "def gamma():"


def test_a_closing_brace_does_not_end_a_region_early() -> None:
    """The one thing a naive indentation rule gets wrong in every brace language: the
    closing brace sits at the definition's own indentation, so it would end each region
    at its first line and every edit inside every function would read as clean."""
    text = "function a() {\n  one();\n}\n\nfunction b() {\n  two();\n}\n"
    assert sweep.symbol_regions(text, "a") == [sweep.LineRange(1, 4)]


def test_a_decorator_is_part_of_the_region_it_decorates() -> None:
    """Absorbed by the unbroken-run rule rather than by a rule of its own: an edit to
    `@dataclass` is an edit to the class it is attached to."""
    text = "@dataclass\nclass Thing:\n    x = 1\n\n\nclass Other:\n    y = 2\n"
    assert sweep.symbol_regions(text, "Thing") == [sweep.LineRange(1, 5)]


def test_a_mention_inside_a_wrapped_paragraph_covers_the_paragraph() -> None:
    """The silent direction, and the reason a block starts at the top of its run. Every
    line of hard-wrapped prose sits at indentation zero, so "to the next line at the
    same indentation" would give a one-line region and an edit two lines further down
    the same sentence would read as clean."""
    text = (
        "# Title\n\n"
        "Some prose that mentions widget_cache and then\n"
        "keeps going for another line\n"
        "and one more.\n\n"
        "A separate paragraph.\n"
    )
    assert sweep.symbol_regions(text, "widget_cache") == [sweep.LineRange(3, 6)]


def test_a_symbol_that_is_not_there_cannot_be_placed() -> None:
    """``None`` is the "cannot place" answer, and every caller reads it as overlapping.
    It is not the same value as "placed, and the change missed it"."""
    assert sweep.symbol_regions(MODULE, "delta") is None
    assert sweep.symbol_regions(MODULE, "   ") is None


def test_every_occurrence_contributes_a_region() -> None:
    """A superset, deliberately. Picking the occurrence that "looks like the
    definition" needs a per-language notion of one, and guessing wrong there fails
    silently; a union only ever costs a flag somebody dismisses."""
    text = "def alpha():\n    return 1\n\n\ndef caller():\n    return alpha()\n"
    assert sweep.symbol_regions(text, "alpha") == [
        sweep.LineRange(1, 4),
        # The call site pulls in the function containing it, because the run it belongs
        # to starts at `def caller():` — which is the honest reading: an edit there is
        # an edit to a caller of the symbol.
        sweep.LineRange(5, 6),
    ]


def test_hunk_headers_parse_both_sides() -> None:
    diff = (
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -7 +7 @@\n"
        "-old\n"
        "+new\n"
        "@@ -20,0 +21,3 @@\n"
        "+a\n+b\n+c\n"
        "@@ -30,2 +33,0 @@\n"
        "-gone\n-also gone\n"
    )
    hunks = sweep.parse_diff_hunks(diff)
    assert [(h.old_start, h.old_count, h.new_start, h.new_count) for h in hunks] == [
        (7, 1, 7, 1),
        (20, 0, 21, 3),
        (30, 2, 33, 0),
    ]
    # A hunk with no old-side lines added nothing but lines; the other two did not.
    assert [h.removes_existing for h in hunks] == [True, False, True]
    # A pure deletion has no new-side line to point at, so it is widened to the pair
    # of lines it happened between rather than silently covering nothing.
    assert hunks[2].new_range == sweep.LineRange(33, 34)


def test_the_rule_the_output_states_is_the_rule_the_code_applies() -> None:
    """``SYMBOL_REGION_RULE`` is quoted by `kyo touch-drift` and by the README, and c1
    asks for the approximation to be STATED. A rule nobody can find is not stated."""
    assert "counts as CHANGED" in sweep.SYMBOL_REGION_RULE
    assert "shallower indentation" in sweep.SYMBOL_REGION_RULE
    from tests.reference_pages import OPERATING

    readme = OPERATING  # the README section, moved (KYO-516)
    assert "Anything the approximation cannot place counts as" in readme.read_text(
        encoding="utf-8"
    )


# ------------------------------------------------------ KYO-300.c1: sweep, narrowed


def test_sweep_does_not_flag_a_symbol_claim_the_change_never_reached(
    kyo, project: Path
) -> None:
    (project / "module.py").write_text(MODULE, encoding="utf-8")
    record = _promote(kyo, project, name="alpha-only", touches=["module.py#alpha"])
    git_init(project, commit_message="baseline")
    assert kyo("sweep", "--json").json["flags"] == []

    _edit_function(project / "module.py", "gamma", "c = 99")
    _commit(project, "edit gamma")

    swept = kyo("sweep", "--json")
    assert swept.code == 0, swept
    assert swept.json["coverage"] == "swept", swept.json
    assert [f for f in swept.json["flags"] if f["record_id"] == record] == [], (
        "the change was in gamma and this record claims only alpha"
    )


def test_sweep_flags_the_symbol_whose_region_changed_and_names_it(
    kyo, project: Path
) -> None:
    (project / "module.py").write_text(MODULE, encoding="utf-8")
    record = _promote(
        kyo, project, name="two-symbols", touches=["module.py#alpha", "module.py#beta"]
    )
    git_init(project, commit_message="baseline")
    kyo("sweep", "--json")

    _edit_function(project / "module.py", "beta", "b = 99")
    _commit(project, "edit beta")

    flags = [f for f in kyo("sweep", "--json").json["flags"] if f["record_id"] == record]
    assert len(flags) == 1, flags
    detail = flags[0]["detail"]
    assert "module.py#beta" in detail
    assert "module.py#alpha" not in detail


def test_a_plain_path_touch_is_flagged_exactly_as_before(kyo, project: Path) -> None:
    """The asymmetry c1 asks for: narrowing happens only where the record was precise.
    A bare path claims the whole file and gets exactly the flag it always got."""
    (project / "module.py").write_text(MODULE, encoding="utf-8")
    record = _promote(kyo, project, name="whole-file", touches=["module.py"])
    git_init(project, commit_message="baseline")
    kyo("sweep", "--json")

    _edit_function(project / "module.py", "gamma", "c = 99")
    _commit(project, "edit gamma")

    flags = [f for f in kyo("sweep", "--json").json["flags"] if f["record_id"] == record]
    assert len(flags) == 1, flags
    assert "module.py" in flags[0]["detail"]
    assert "#" not in flags[0]["detail"].split(": ", 1)[1]


def test_a_section_claim_in_a_document_narrows_the_same_way(kyo, project: Path) -> None:
    (project / "NOTES.md").write_text(DOCUMENT, encoding="utf-8")
    record = _promote(
        kyo, project, name="section", touches=["NOTES.md#Alpha section"]
    )
    git_init(project, commit_message="baseline")
    kyo("sweep", "--json")

    text = (project / "NOTES.md").read_text(encoding="utf-8")
    (project / "NOTES.md").write_text(
        text.replace("Text under beta.", "Rewritten text under beta."), encoding="utf-8"
    )
    _commit(project, "edit the beta section")

    assert [
        f for f in kyo("sweep", "--json").json["flags"] if f["record_id"] == record
    ] == []


def test_a_symbol_that_cannot_be_placed_still_flags(kyo, project: Path) -> None:
    """The inviolable direction. The symbol was valid when the touch was written and the
    branch has since deleted it, so the region cannot be found — which is exactly when a
    controller most needs to hear that the ground moved."""
    (project / "module.py").write_text(MODULE, encoding="utf-8")
    record = _promote(kyo, project, name="vanishing", touches=["module.py#beta"])
    git_init(project, commit_message="baseline")
    kyo("sweep", "--json")

    (project / "module.py").write_text(
        MODULE.replace("def beta():\n    b = 2\n    return b\n", ""), encoding="utf-8"
    )
    _commit(project, "delete beta")

    flags = [f for f in kyo("sweep", "--json").json["flags"] if f["record_id"] == record]
    assert len(flags) == 1, flags
    assert "module.py#beta" in flags[0]["detail"]


def test_narrowing_never_widens_the_file_level_answer() -> None:
    """``narrow_changed_by_symbol`` is handed the file-level hit and never recomputes
    it — the same discipline ``commands._narrow_by_symbol`` keeps with
    ``shared_touch_paths``. Nothing it returns can name a path the caller did not."""
    entries, dropped = sweep.narrow_changed_by_symbol(
        ["a.py"],
        {"a.py": ["alpha"], "b.py": ["beta"]},
        regions_for=lambda path, symbol: [sweep.LineRange(1, 2)],
        hunks_for=lambda path: [sweep.DiffHunk(1, 1, 1, 1)],
    )
    assert entries == ["a.py#alpha"]
    assert dropped == []


def test_a_diff_that_cannot_be_read_leaves_the_path_flagged() -> None:
    entries, dropped = sweep.narrow_changed_by_symbol(
        ["a.py"],
        {"a.py": ["alpha"]},
        regions_for=lambda path, symbol: [sweep.LineRange(500, 501)],
        hunks_for=lambda path: None,
    )
    assert (entries, dropped) == (["a.py"], [])


def test_a_directory_or_new_file_touch_is_untouched_by_the_narrowing() -> None:
    entries, dropped = sweep.narrow_changed_by_symbol(
        ["apps/desktop/", "+src/new.py"],
        {},
        regions_for=lambda path, symbol: None,
        hunks_for=lambda path: [],
    )
    assert entries == ["+src/new.py", "apps/desktop/"]
    assert dropped == []


# --------------------------------------------- KYO-300.c3: a touch can claim an addition


def test_an_additive_claim_is_accepted_on_an_existing_file(kyo, project: Path) -> None:
    (project / "protocol.ts").write_text("export type Old = 1;\n", encoding="utf-8")
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, project, record, "protocol.ts#+")

    shown = kyo("show", record, "--json").json
    assert [(t["path"], t["symbol"]) for t in shown["touches"]] == [("protocol.ts", "+")]


def test_an_additive_claim_needs_no_symbol_to_be_found(kyo, project: Path) -> None:
    """The whole point: the thing being added has no name yet, so validation refusing it
    as "not found" left the whole file as the only honest claim."""
    (project / "shell.css").write_text(".a { color: red }\n", encoding="utf-8")
    record = new_record(kyo, criteria=["a"])
    refused = kyo(
        "edit",
        record,
        "--touches-file",
        str(_written(project, "bad.txt", "shell.css#not-there-yet\n")),
    )
    assert refused.code != 0
    assert "not found" in refused.err + refused.out
    _declare(kyo, project, record, "shell.css#+")


def test_an_additive_claim_still_needs_the_file_to_exist(kyo, project: Path) -> None:
    """``path#+`` claims additions to a file that IS there; ``+path`` is the form for a
    file that is not. The existence check that keeps a typo a typo still runs."""
    record = new_record(kyo, criteria=["a"])
    refused = kyo(
        "edit",
        record,
        "--touches-file",
        str(_written(project, "bad.txt", "nope.ts#+\n")),
    )
    assert refused.code != 0
    assert "no such file" in refused.err + refused.out


def test_kyo_show_says_what_an_additive_claim_means(kyo, project: Path) -> None:
    (project / "protocol.ts").write_text("export type Old = 1;\n", encoding="utf-8")
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, project, record, "protocol.ts#+")

    shown = kyo("show", record)
    assert "protocol.ts#+" in shown.out
    assert sweep.TOUCH_ADDITIVE_SYMBOL in shown.out


def test_the_brief_says_what_an_additive_claim_means(kyo, git_project: Path) -> None:
    (git_project / "protocol.ts").write_text("export type Old = 1;\n", encoding="utf-8")
    _commit(git_project, "protocol")
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "protocol.ts#+")

    dispatched = kyo("--full", "dispatch", record, "--branch", "kyo-300-brief", "--json")
    assert dispatched.code == 0, dispatched
    brief = dispatched.json["record"]["dispatches"][0]["brief"]
    assert sweep.TOUCH_ADDITIVE_SYMBOL in brief
    assert sweep.TOUCH_ADDITIVE_BRIEF_NOTE in brief


def test_kyo_reference_says_what_an_additive_claim_means(kyo) -> None:
    reference = kyo("reference", "new")
    assert reference.code == 0, reference
    assert "'path#+'" in reference.out


def test_two_additive_claims_on_one_file_do_not_collide(kyo, git_project: Path) -> None:
    (git_project / "protocol.ts").write_text("export type Old = 1;\n", encoding="utf-8")
    _commit(git_project, "protocol")
    first = new_record(kyo, criteria=["a"])
    second = new_record(kyo, criteria=["b"])
    _declare(kyo, git_project, first, "protocol.ts#+")
    _declare(kyo, git_project, second, "protocol.ts#+")

    assert kyo("dispatch", first, "--branch", "one", "--json").code == 0
    other = kyo("dispatch", second, "--branch", "two", "--json")
    assert other.code == 0, other
    narrowed = other.json["touch_narrowings"]
    assert [entry["path"] for entry in narrowed] == ["protocol.ts"]


def test_an_additive_claim_does_not_collide_with_a_symbol_claim(
    kyo, git_project: Path
) -> None:
    (git_project / "module.py").write_text(MODULE, encoding="utf-8")
    _commit(git_project, "module")
    first = new_record(kyo, criteria=["a"])
    second = new_record(kyo, criteria=["b"])
    _declare(kyo, git_project, first, "module.py#alpha")
    _declare(kyo, git_project, second, "module.py#+")

    assert kyo("dispatch", first, "--branch", "one", "--json").code == 0
    other = kyo("dispatch", second, "--branch", "two", "--json")
    assert other.code == 0, other


def test_an_additive_claim_collides_with_a_whole_file_claim(
    kyo, git_project: Path
) -> None:
    """The one thing it must still collide with, and the asymmetry the whole symbol
    refinement rests on: a plain path claims everything in the file, including the
    lines that are not there yet."""
    (git_project / "module.py").write_text(MODULE, encoding="utf-8")
    _commit(git_project, "module")
    first = new_record(kyo, criteria=["a"])
    second = new_record(kyo, criteria=["b"])
    _declare(kyo, git_project, first, "module.py")
    _declare(kyo, git_project, second, "module.py#+")

    assert kyo("dispatch", first, "--branch", "one", "--json").code == 0
    refused = kyo("dispatch", second, "--branch", "two", "--json")
    assert refused.code == 2, refused
    assert "module.py" in refused.err + refused.out


def test_the_additive_claim_needed_no_schema_bump(kyo, project: Path) -> None:
    """It rides ``touches.symbol``, which has held a string since the first migration.
    If this ever stops being true the compat machinery has to be used — the record says
    so, and this test is the thing that notices."""
    (project / "protocol.ts").write_text("export type Old = 1;\n", encoding="utf-8")
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, project, record, "protocol.ts#+")
    export = (project / "kyosei" / "export" / f"{record}.json").read_text(
        encoding="utf-8"
    )
    assert '"symbol": "+"' in export


# ------------------------------------------ KYO-300.c2/c4: drift, inside a claimed file


def _written(project: Path, name: str, text: str) -> Path:
    path = project.parent / name
    path.write_text(text, encoding="utf-8")
    return path


def _branch_editing(project: Path, branch: str, edit) -> str:
    base = _git(project, "rev-parse", "--abbrev-ref", "HEAD")
    _git(project, "checkout", "-q", "-b", branch)
    edit()
    _commit(project, f"work on {branch}")
    _git(project, "checkout", "-q", base)
    return base


def test_drift_distinguishes_editing_outside_a_symbol_from_an_unclaimed_file(
    kyo, git_project: Path
) -> None:
    (git_project / "module.py").write_text(MODULE, encoding="utf-8")
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "module.py#alpha")
    against = _branch_editing(
        git_project,
        "kyo-300-outside",
        lambda: _edit_function(git_project / "module.py", "gamma", "c = 99"),
    )

    dispatch_id = kyo(
        "dispatch", record, "--branch", "kyo-300-outside", "--json"
    ).json["dispatch_id"]
    result = kyo("touch-drift", dispatch_id, "--against", against, "--json")
    assert result.code == 0, result
    drift = result.json["touch_drift"]
    # Not an unclaimed file — the path IS in the claim, which is what makes this a
    # finding the file-level list cannot carry.
    assert drift["undeclared_files"] == []
    assert drift["symbol_check"] == touch_drift.SYMBOL_CHECK_RUN
    outside = drift["outside_claimed_symbols"]
    assert [entry["path"] for entry in outside] == ["module.py"]
    assert outside[0]["claimed"] == ["alpha"]
    assert outside[0]["suggested_touches"] == ["module.py#gamma"]
    printed = kyo("touch-drift", dispatch_id, "--against", against)
    assert "claimed by symbol, edited outside it" in printed.out
    assert "module.py#gamma" in printed.out
    assert sweep.SYMBOL_REGION_RULE in printed.out


def test_drift_says_nothing_when_the_branch_stayed_inside_its_symbols(
    kyo, git_project: Path
) -> None:
    (git_project / "module.py").write_text(MODULE, encoding="utf-8")
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "module.py#alpha")
    against = _branch_editing(
        git_project,
        "kyo-300-inside",
        lambda: _edit_function(git_project / "module.py", "alpha", "a = 99"),
    )

    dispatch_id = kyo(
        "dispatch", record, "--branch", "kyo-300-inside", "--json"
    ).json["dispatch_id"]
    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json[
        "touch_drift"
    ]
    assert drift["outside_claimed_symbols"] == []
    assert drift["symbol_check"] == touch_drift.SYMBOL_CHECK_RUN


def test_the_correction_no_longer_flattens_the_symbols_it_just_told_you_to_add(
    kyo, git_project: Path
) -> None:
    """Found while building c2. ``touches_file_lines`` was the FILE-level claim, so the
    list `kyo touch-drift` prints — and `kyo edit --touches-from-drift` applies —
    deleted every ``#symbol`` the record had. A command that has just named the symbol
    to add must not hand over a correction that removes the ones already there."""
    (git_project / "module.py").write_text(MODULE, encoding="utf-8")
    (git_project / "other.py").write_text("x = 1\n", encoding="utf-8")
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "module.py#alpha")

    def edit() -> None:
        _edit_function(git_project / "module.py", "alpha", "a = 99")
        (git_project / "other.py").write_text("x = 2\n", encoding="utf-8")

    against = _branch_editing(git_project, "kyo-300-flatten", edit)
    dispatch_id = kyo(
        "dispatch", record, "--branch", "kyo-300-flatten", "--json"
    ).json["dispatch_id"]
    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json[
        "touch_drift"
    ]
    assert drift["touches_file_lines"] == ["module.py#alpha", "other.py"]
    assert "module.py#alpha" in drift["apply_command"]


def test_an_additive_claim_is_held_to(kyo, git_project: Path) -> None:
    """KYO-300.c4, the branch that breaks the promise."""
    (git_project / "protocol.ts").write_text(
        "export type Old = 1;\nexport type Kept = 2;\n", encoding="utf-8"
    )
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "protocol.ts#+")
    against = _branch_editing(
        git_project,
        "kyo-300-broken",
        lambda: (git_project / "protocol.ts").write_text(
            "export type Old = 999;\nexport type Kept = 2;\nexport type New = 3;\n",
            encoding="utf-8",
        ),
    )

    dispatch_id = kyo(
        "dispatch", record, "--branch", "kyo-300-broken", "--json"
    ).json["dispatch_id"]
    result = kyo("touch-drift", dispatch_id, "--against", against, "--json")
    broken = result.json["touch_drift"]["additive_claims_broken"]
    assert [entry["path"] for entry in broken] == ["protocol.ts"]
    assert broken[0]["lines_removed"] >= 1
    assert result.code == 0, result
    printed = kyo("touch-drift", dispatch_id, "--against", against)
    assert "additive claim not kept" in printed.out


def test_an_additive_claim_kept_raises_nothing(kyo, git_project: Path) -> None:
    """KYO-300.c4, the branch that keeps it — the half that proves the check is not
    simply "you touched the file"."""
    (git_project / "protocol.ts").write_text(
        "export type Old = 1;\nexport type Kept = 2;\n", encoding="utf-8"
    )
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "protocol.ts#+")
    against = _branch_editing(
        git_project,
        "kyo-300-kept",
        lambda: (git_project / "protocol.ts").write_text(
            "export type Old = 1;\nexport type Kept = 2;\nexport type New = 3;\n",
            encoding="utf-8",
        ),
    )

    dispatch_id = kyo(
        "dispatch", record, "--branch", "kyo-300-kept", "--json"
    ).json["dispatch_id"]
    drift = kyo("touch-drift", dispatch_id, "--against", against, "--json").json[
        "touch_drift"
    ]
    assert drift["additive_claims_broken"] == []
    assert drift["outside_claimed_symbols"] == []


def test_a_finding_that_could_not_be_asked_says_so(kyo, git_project: Path) -> None:
    """Law 6, one level down: two empty lists with no way to tell "checked and clean"
    from "never asked" is the absence bug this whole command exists to fix."""
    (git_project / "module.py").write_text(MODULE, encoding="utf-8")
    _commit(git_project, "baseline")
    record = new_record(kyo, criteria=["a"])
    _declare(kyo, git_project, record, "module.py#alpha")
    dispatch_id = kyo("dispatch", record, "--json").json["dispatch_id"]

    drift = kyo("touch-drift", dispatch_id, "--json").json["touch_drift"]
    assert drift["symbol_check"].startswith(touch_drift.SYMBOL_CHECK_NOT_RUN)
    assert drift["outside_claimed_symbols"] == []
