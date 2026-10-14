"""KYO-423: six human-output lines that misdescribed or repeated what they report.

Text output only. Each test below pins one line's wording against the specific way it
went wrong, and every JSON assertion in this file is that the JSON did NOT change:
this record excluded output-shape changes.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from kyosei import changelog, docs_check
from kyosei.cmd_worktrees import EXPAND_STOPS_FLAG
from kyosei.schema import SCHEMA_VERSION

from .conftest import git_worktree, legacy_unresolved_rows, new_record
from .test_kyo134_unresolved_stop_record import _unresolved_stop
from .test_subagent_stop_worktree import CONTROLLER_EDIT


def _git(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=project, check=True, capture_output=True, text=True)


# --------------------------------------------------------- c1: changelog dates are UTC


def _empty(source: str = changelog.SOURCE_STORE) -> changelog.Changelog:
    return changelog.Changelog([], source)


def test_the_unfiltered_changelog_header_says_its_dates_are_utc() -> None:
    """`completed_on` is the UTC day (KYO-398); a reader in EDT sees anything finished
    after 20:00 dated tomorrow. The bare-date window said "(UTC)"; the unfiltered header
    said nothing, and that is the header a bare `--all` prints."""
    for source in (changelog.SOURCE_STORE, changelog.SOURCE_PACKAGED):
        summary = changelog.text_lines(_empty(source), [], None)[1]
        assert "UTC" in summary, summary
        assert changelog.DATES_ARE_UTC in summary


def test_an_instant_window_header_says_so_too() -> None:
    summary = changelog.text_lines(_empty(), [], "2026-09-25T12:00:00Z")[1]
    assert changelog.DATES_ARE_UTC in summary, summary


def test_the_generated_markdown_header_says_its_dates_are_utc() -> None:
    document = changelog.markdown(_empty(), [], None)
    header = document.split("\n## ", 1)[0]
    assert f"{changelog.DATES_ARE_UTC}." in header, header
    assert "UTC" in changelog.DATES_ARE_UTC


# ---------------------------------------- c2: a hand-run check-branch is the default


def test_a_hand_run_check_branch_says_default_and_not_an_unresolved_stop(
    kyo, git_project: Path
) -> None:
    """No `--subagent-stop`, so there was no stop to resolve: the KYO-285 sentence
    described a hook stop that never happened."""
    result = kyo("check-branch")
    assert result.code == 0, result
    text = result.out + result.err
    assert "against: main (default)" in text, text
    assert "did not resolve" not in text, text


def test_an_unmatched_subagent_stop_keeps_the_unresolved_stop_wording(
    kyo, git_project: Path
) -> None:
    """The other half: the hook path still says why it fell back, and the two paths
    stay two messages. JSON is unchanged on both (`main-unmatched`)."""
    payload = json.dumps(
        {
            "session_id": "session",
            "cwd": str(git_project),
            "hook_event_name": "SubagentStop",
            "stop_hook_active": False,
        }
    )
    stopped = kyo("check-branch", "--subagent-stop", stdin=payload)
    text = stopped.out + stopped.err
    assert "this stop did not resolve to a specific running dispatch" in text, text
    assert "(default)" not in text, text

    as_json = kyo("check-branch", "--subagent-stop", "--json", stdin=payload)
    assert as_json.json["against_source"] == "main-unmatched"
    by_hand = kyo("check-branch", "--json")
    assert by_hand.json["against_source"] == "main-unmatched", "a JSON value changed"


# --------------------------------------------- c3: GOVERNS_FORMAT printed once in text


def test_docs_check_text_prints_the_governs_format_once(kyo, project: Path) -> None:
    doc = project / "docs" / "PRD.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(
        "## One\n\ngoverns: src/nope.py\n\n## Two\n\ngoverns: src/gone.py\n", encoding="utf-8"
    )
    result = kyo("docs", "check")
    assert result.code == 3, result
    text = result.out + result.err
    assert text.count(docs_check.GOVERNS_FORMAT) == 1, text


# ------------------------------------------ c4: rebuild counts legacy paths, no list


def test_rebuild_counts_legacy_absolute_paths_without_listing_them(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    tree = tmp_path / "wt-legacy-polish"
    git_worktree(kyo.project, tree)
    record = new_record(kyo, title="widget", criteria=["a"])
    assert kyo("dispatch", record, "--worktree", str(tree)).code == 0

    export_path = git_project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    doc["schema_version"] = SCHEMA_VERSION - 1
    doc["dispatches"][0]["worktree"] = str(tree.resolve())
    export_path.write_text(json.dumps(doc), encoding="utf-8")

    (git_project / "kyosei" / "kyosei.db").unlink()
    result = kyo("rebuild")
    assert result.code == 0, result
    assert "1 absolute path(s) loaded verbatim" in result.out, result.out
    assert "`kyo rebuild --json`" in result.out, result.out
    assert str(tree.resolve()) not in result.out, "the per-path lines are back in text"

    (git_project / "kyosei" / "kyosei.db").unlink()
    listed = kyo("rebuild", "--json").json["legacy_absolute_paths"]
    assert len(listed) == 1 and str(tree.resolve()) in listed[0], listed


# ------------------------------------ c5: `kyo worktrees` folds the stop census away


def test_worktrees_default_output_folds_the_stop_census_to_one_line(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    (git_project / CONTROLLER_EDIT).write_text("edited by the controller\n", encoding="utf-8")
    # The fold renders the per-stop rows a store's history holds (KYO-418.c6 counts
    # new unresolved stops instead), so this writes one the way earlier builds did.
    with legacy_unresolved_rows():
        dispatch_id = _unresolved_stop(kyo, tmp_path, git_project, "wt-polish-census")

    default = kyo("worktrees")
    assert default.code == 0, default
    assert "per dispatch those stops named" not in default.out, default.out
    assert "it could have been any of" not in default.out, default.out
    census_lines = [line for line in default.out.splitlines() if line.startswith("stop audits:")]
    assert len(census_lines) == 1, default.out
    assert "1 of 1 recorded stop(s) could NOT be matched" in census_lines[0], census_lines
    assert f"kyo worktrees {EXPAND_STOPS_FLAG}" in census_lines[0], census_lines

    expanded = kyo("worktrees", EXPAND_STOPS_FLAG)
    assert expanded.code == 0, expanded
    assert "per dispatch those stops named" in expanded.out, expanded.out
    assert f"    {dispatch_id} (running): 1 unmatched" in expanded.out, expanded.out
    assert f"it could have been any of: {dispatch_id}" in expanded.out, expanded.out

    as_json = kyo("worktrees", "--json").json["stop_audits"]
    assert as_json == kyo("worktrees", EXPAND_STOPS_FLAG, "--json").json["stop_audits"]
    assert [u["candidates"] for u in as_json["unresolved"]] == [[dispatch_id]]


def test_worktrees_default_output_still_says_when_nothing_was_recorded(kyo) -> None:
    """The one line is the ledger's sentence, so the KYO-134.c5 three-way answer
    survives the fold: an empty ledger is not reported as "all checked"."""
    listed = kyo("worktrees")
    assert listed.code == 0, listed
    assert "stop audits: none recorded in this store yet" in listed.out, listed.out


# -------------------------------------------- c6: counts are not "stored" values


def test_redact_prints_its_counts_without_the_stored_prefix(
    kyo, project: Path, write_file
) -> None:
    record = new_record(kyo, kind="bug", title="Saw an id")
    attached = kyo("attach", record, "--file", write_file("ev.txt", "an id 4429117"), "--json")
    assert attached.code == 0, attached
    result = kyo("redact", attached.json["evidence_id"], "--why", "carries an id")
    assert result.code == 0, result
    for label in (
        "scrubbed from prior ledger lines",
        "scrubbed from the database's events table",
        "embedded copies removed (briefs, reports)",
    ):
        lines = [line for line in result.out.splitlines() if label in line]
        assert len(lines) == 1, result.out
        assert lines[0].startswith(f"{label}: "), lines[0]
    # A real stored-value echo keeps its word.
    assert "stored reason (" in result.out, result.out
