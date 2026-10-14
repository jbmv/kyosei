"""CLI surface: loud errors, exit codes, ``--json`` everywhere, two console scripts."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

from kyosei.cli import COMMAND_TABLE, MUTATING_COMMANDS, build_parser
from kyosei.errors import SCHEMA_MISMATCH_KEY
from kyosei.schema import SCHEMA_VERSION

from .conftest import git_init, invoke, new_record

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_both_console_scripts_share_one_entry_point() -> None:
    """``kyo`` is a blessed alias — identical, never a subset."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert scripts["kyosei"] == scripts["kyo"] == "kyosei.cli:main"


def test_every_command_in_the_table_has_a_subparser() -> None:
    parser = build_parser()
    subparsers = next(a for a in parser._actions if a.choices and hasattr(a, "choices"))
    assert set(subparsers.choices) == set(COMMAND_TABLE)


def test_json_is_accepted_before_and_after_the_subcommand(kyo) -> None:
    record = new_record(kyo)
    before = kyo("--json", "show", record)
    after = kyo("show", record, "--json")
    assert before.code == after.code == 0
    assert json.loads(before.out) == json.loads(after.out)


def test_read_commands_offer_json(kyo) -> None:
    record = new_record(kyo)
    assert kyo("show", record, "--json").json["id"] == record
    assert kyo("doctor", "--json").json["ok"] is True


def test_errors_are_reported_in_json_too(kyo) -> None:
    result = kyo("show", "TST-404", "--json")
    assert result.code == 1
    payload = json.loads(result.out)
    assert payload["ok"] is False
    assert payload["exit_code"] == 1
    assert "TST-404" in payload["error"]


def test_unknown_ids_fail_loudly_in_every_command(kyo) -> None:
    """Law 6: an edit naming a nonexistent id errors — for all of them."""
    missing = "TST-404"
    real = new_record(kyo)
    invocations = [
        ("show", missing),
        ("edit", missing, "--title", "x"),
        ("move", missing, "working"),
        ("wont-do", missing, "--why", "no"),
        ("link", missing, "blocks", real),
        ("link", real, "blocks", missing),
        ("gate", missing, "awaiting:human-review"),
        ("ungate", f"{missing}.g1"),
    ]
    for command in invocations:
        result = kyo(*command)
        assert result.code != 0, command
        assert result.err.strip(), command


def test_error_messages_go_to_stderr(kyo) -> None:
    result = kyo("show", "TST-404")
    assert result.out == ""
    assert "TST-404" in result.err


def test_exit_codes_are_distinguishable(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    assert kyo("show", record).code == 0
    assert kyo("show", "TST-404").code == 1  # unknown id
    assert kyo("move", record, "done").code == 2  # contract violation
    assert kyo("move", record, "sideways").code == 64  # usage
    assert invoke(["--root", "/nonexistent", "doctor"]).code == 1


def test_argparse_usage_errors_do_not_collide_with_contract_violations() -> None:
    """Exit 2 means "contract", never "you typed it wrong"."""
    assert invoke(["new"]).code == 64
    assert invoke(["nonsense-command"]).code == 64


def test_edit_needs_something_to_change(kyo) -> None:
    record = new_record(kyo)
    result = kyo("edit", record)
    assert result.code == 64
    assert "something to change" in result.err


def test_python_dash_m_entry_point_exists() -> None:
    assert (REPO_ROOT / "src" / "kyosei" / "__main__.py").is_file()
    assert sys.version_info >= (3, 12)


# --------------------------------------------------- KYO-184: `kyo where`
#
# The command whose subject is "where is this project, and can this build open its
# store". Every test below is about a state in which one of the two commands the
# desktop app used to borrow could not answer, or answered only by doing work the
# question did not need.


def test_where_answers_both_questions_in_one_invocation(git_project) -> None:
    """KYO-184.c1. One call carries the location AND the openability verdict."""
    payload = invoke(["--root", str(git_project), "where", "--json"]).json
    assert payload["ok"] is True
    assert payload["layout"] == "in-repo"
    assert payload["configured"] is True
    assert payload["store_root"] == str(git_project)
    assert payload["code_root"] == str(git_project)
    assert payload["project_prefix"] == "TST"
    assert payload["branch"] and payload["branch_problem"] is None
    assert payload["can_open"] is True
    assert payload["store_problem"] is None
    assert payload["build_schema_version"] == SCHEMA_VERSION
    assert payload["db_path"] == str(git_project / "kyosei" / "kyosei.db")


def test_where_answers_where_there_is_no_project_at_all(tmp_path, monkeypatch) -> None:
    """KYO-184.c1 — "needs no store to be openable to answer", the strongest case:
    there is no store.

    Asked from inside the directory with no ``--root``, deliberately: an explicit
    ``--root`` is taken at its word by ``resolve_layout`` whatever the command's
    project mode is, so a test that passed one would pass with this command declared
    ``PROJECT_REQUIRED`` and would be checking nothing. The upward search is where
    that declaration bites, and ``PROJECT_OPTIONAL`` is what turns "no project found"
    from a refusal into the answer below.
    """
    nowhere = tmp_path / "nothing here"
    nowhere.mkdir()
    monkeypatch.chdir(nowhere)
    result = invoke(["where", "--json"])
    assert result.code == 0
    payload = result.json
    assert payload["layout"] == "unconfigured"
    assert payload["configured"] is False
    assert payload["store_root"] is None
    assert payload["can_open"] is False
    assert "unconfigured" in payload["store_problem"]


def test_the_remedy_the_unconfigured_answer_names_clears_it(tmp_path) -> None:
    """This repository's standing rule, applied to the one remedy `where` names of
    its own: `kyo init` is offered where nothing is configured, so `kyo init` is run
    and the same command is asked again."""
    nowhere = tmp_path / "not yet a project"
    nowhere.mkdir()
    before = invoke(["--root", str(nowhere), "where"])
    assert "`kyo init` creates a project" in before.out
    assert invoke(["init", str(nowhere), "--in-repo", "--prefix", "NEW"]).code == 0
    after = invoke(["--root", str(nowhere), "where", "--json"]).json
    assert after["configured"] is True
    assert after["can_open"] is True
    assert after["project_prefix"] == "NEW"


def test_where_answers_for_a_project_that_is_not_a_git_repository(project) -> None:
    """KYO-184.c4. `kyo check-branch` asks git for the branch before it reports
    anything, so it fails outright here — which is why the app carried a second
    command as a fallback locator. This one places the project and says why there is
    no branch, rather than failing or inventing one."""
    payload = invoke(["--root", str(project), "where", "--json"]).json
    assert payload["ok"] is True
    assert payload["store_root"] == str(project)
    assert payload["can_open"] is True
    assert payload["branch"] is None
    assert payload["branch_problem"] == "no-git"
    # "not a git repository" stays distinguishable from "detached HEAD": git's own
    # sentence is carried, not a paraphrase composed here.
    assert "git repository" in payload["branch_problem_detail"]
    # And the guard this replaces genuinely cannot answer in this state.
    assert invoke(["--root", str(project), "check-branch", "--json"]).code != 0


def test_where_reports_a_detached_head_as_such_rather_than_as_no_git(project) -> None:
    """The other half of the same distinction: a project that IS in git but has no
    branch name to report. Folding this into "no git" would tell a reader their
    project is not versioned when it is."""
    git_init(project)
    head = invoke(["--root", str(project), "where", "--json"]).json["branch"]
    assert head
    subprocess.run(
        ["git", "checkout", "-q", "--detach", "HEAD"], cwd=project, check=True
    )
    payload = invoke(["--root", str(project), "where", "--json"]).json
    assert payload["branch"] is None
    assert payload["branch_problem"] == "no-branch"
    assert payload["store_root"] == str(project)
    assert payload["can_open"] is True


def test_where_carries_the_stored_schema_mismatch_payload_unchanged(project, db) -> None:
    """KYO-184.c2. On a mismatch the envelope carries KYO-178's payload *as stored*.

    Proved by comparison rather than by inspection: the same payload is read out of
    a command that raises the refusal (`kyo list`, whose error envelope `cli.main`
    merges `exc.data` into) and out of `kyo where`, which catches it. Byte-identical
    dictionaries can only happen if neither side re-derived it.
    """
    conn = db()
    conn.execute(
        "UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION + 1),)
    )
    conn.close()

    listed = invoke(["--root", str(project), "list", "--json"])
    assert listed.code != 0
    raised = listed.json[SCHEMA_MISMATCH_KEY]

    where = invoke(["--root", str(project), "where", "--json"])
    # The answer arrives on a clean exit: this command's subject is the question, so
    # "no, this build cannot open it" is a question answered, not a command that failed.
    assert where.code == 0
    payload = where.json
    assert payload["can_open"] is False
    assert payload[SCHEMA_MISMATCH_KEY] == raised
    assert payload[SCHEMA_MISMATCH_KEY]["direction"] == "db-ahead-of-build"
    assert payload[SCHEMA_MISMATCH_KEY]["older_side"] == "build"
    # The location is still answered — that is the whole point of asking both
    # questions in one command.
    assert payload["store_root"] == str(project)
    assert payload["project_prefix"] == "TST"
    # And the refusal's own sentence is carried whole, for a reader.
    assert payload["store_problem"] == listed.json["error"]


def test_where_places_a_store_whose_database_is_missing(project) -> None:
    """A fresh clone of a store repository: `kyosei.db` is gitignored, so it is not
    there. The location is known and `can_open` is false with the CLI's own remedy —
    which is exactly the state this worktree's own `kyo where` reports."""
    (project / "kyosei" / "kyosei.db").unlink()
    payload = invoke(["--root", str(project), "where", "--json"]).json
    assert payload["store_root"] == str(project)
    assert payload["project_prefix"] == "TST"
    assert payload["can_open"] is False
    assert "kyo rebuild" in payload["store_problem"]


def test_where_places_a_split_store_and_names_the_link(split_project) -> None:
    """Under the split layout the store is not the code repository, and a locator
    that answered with one directory would be wrong about the other."""
    code, store = split_project
    payload = invoke(["--root", str(code), "where", "--json"]).json
    assert payload["layout"] == "split"
    assert payload["store_root"] == str(store)
    assert payload["code_root"] == str(code)
    assert payload["store_link"] == str(code / ".kyosei")
    assert payload["can_open"] is True
    assert payload["branch"]


def _export_snapshot(project: Path) -> list[tuple[str, bytes]]:
    return sorted(
        (str(path.relative_to(project)), path.read_bytes())
        for path in (project / "kyosei" / "export").rglob("*")
        if path.is_file()
    )


def test_where_is_declared_read_only_and_writes_nothing(kyo, project, db) -> None:
    """The write-capability declaration this project keeps in ``MUTATING_COMMANDS``
    — what ``kyo reference`` renders "Writes to the store: **no**" from — and the
    behaviour behind it: ``where`` opens the store in order to answer whether the
    store opens, and leaves no events row and no export byte behind."""
    assert "where" not in MUTATING_COMMANDS
    new_record(kyo)
    conn = db()
    events_before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    export_before = _export_snapshot(project)

    assert invoke(["--root", str(project), "where", "--json"]).code == 0

    conn = db()
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events_before
    conn.close()
    assert _export_snapshot(project) == export_before
