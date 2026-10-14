"""KYO-421: the CLI's error contract on three paths that were left open.

1. An exception a handler did not mean to raise (c1-c3): one line on stderr, the
   ``--json`` envelope on stdout, exit 1, the context still closed, and the traceback
   one environment variable away.
2. Lock contention (c4-c5): an explicit ``busy_timeout`` on every store connection,
   and a write that loses the race refused in words that name the database and the
   remedy — proved by running the remedy.
3. A mistyped subcommand (c6): the nearest real one, before the full list.

Plus c7, from KYO-409.r1: a push guard that refuses keeps its blocking exit when the
store is also unreadable to this build.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from kyosei import cli, commands
from kyosei import store as store_module
from kyosei.cli import TRACEBACK_ENV, build_parser
from kyosei.commands import PROJECT_REQUIRED
from kyosei.errors import EXIT_CONTRACT, EXIT_ERROR, EXIT_USAGE

from .conftest import allow_direct_filing, git_init, invoke

# ------------------------------------------------------------ c1-c3: the outer handler


def _explode(ctx, args):
    ctx.store()  # an open store, so c3's close has something to close
    raise PermissionError(13, "Permission denied", "/somewhere/kyosei.db")


@pytest.fixture
def exploding_list(monkeypatch):
    """`kyo list` whose handler raises something that is not a KyoseiError."""
    monkeypatch.setitem(cli.COMMAND_TABLE, "list", (_explode, PROJECT_REQUIRED))


def test_c1_json_envelope_for_an_unexpected_exception(project, exploding_list, monkeypatch):
    monkeypatch.delenv(TRACEBACK_ENV, raising=False)

    result = invoke(["--root", str(project), "--json", "list"])

    assert result.code == EXIT_ERROR, result
    payload = json.loads(result.out)
    assert payload["ok"] is False
    assert payload["command"] == "list"
    assert payload["exception_type"] == "PermissionError"
    assert "Permission denied" in payload["error"]
    assert payload["exit_code"] == EXIT_ERROR


def test_c2_text_mode_is_one_line_and_no_traceback(project, exploding_list, monkeypatch):
    monkeypatch.delenv(TRACEBACK_ENV, raising=False)

    result = invoke(["--root", str(project), "list"])

    assert result.code == EXIT_ERROR, result
    assert result.out == ""
    lines = result.err.strip().splitlines()
    assert len(lines) == 1, result
    assert lines[0].startswith("kyo: PermissionError: ")
    assert "Permission denied" in lines[0]
    assert "Traceback" not in result.err
    # The one line names the remedy for "I need the traceback".
    assert f"{TRACEBACK_ENV}=1" in lines[0]


def test_c2_the_named_variable_brings_the_traceback_back(project, exploding_list, monkeypatch):
    """The remedy the one-line message names, run: set the variable, get the trace."""
    monkeypatch.delenv(TRACEBACK_ENV, raising=False)
    before = invoke(["--root", str(project), "list"])
    named = re.search(r"set (\w+)=1", before.err)
    assert named, before
    monkeypatch.setenv(named.group(1), "1")

    after = invoke(["--root", str(project), "list"])

    assert after.code == EXIT_ERROR, after
    assert "Traceback (most recent call last)" in after.err
    assert "_explode" in after.err
    # And the one-line report is still its last line.
    assert after.err.strip().splitlines()[-1].startswith("kyo: PermissionError: ")


def test_c3_the_context_is_closed_when_an_unexpected_exception_escapes(
    project, exploding_list, monkeypatch
):
    closed: list[bool] = []
    real_close = commands.Context.close

    def spy(self):
        closed.append(self._store is not None)
        real_close(self)

    monkeypatch.setattr(commands.Context, "close", spy)

    result = invoke(["--root", str(project), "list"])

    assert result.code == EXIT_ERROR, result
    assert closed == [True], "ctx.close() did not run with the store open"


# ------------------------------------------------------------- c4-c5: lock contention


def test_c4_connect_sets_an_explicit_busy_timeout(tmp_path: Path):
    conn = store_module.connect(tmp_path / "x.db")
    try:
        value = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()
    assert value == store_module.BUSY_TIMEOUT_MS
    # Not Python's implicit 5 s: a value somebody chose.
    assert value != 5000


def test_c4_the_timeout_is_read_at_connect_time(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(store_module, "BUSY_TIMEOUT_MS", 1234)
    conn = store_module.connect(tmp_path / "x.db")
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 1234
    finally:
        conn.close()


def _write_args(project: Path, *extra: str) -> list[str]:
    allow_direct_filing(project, "bug")
    return ["--root", str(project), *extra, "new", "bug", "--title", "Raced"]


@pytest.fixture
def held_lock(project, monkeypatch):
    """A second connection holding the store's write lock, and a short wait."""
    monkeypatch.setattr(store_module, "BUSY_TIMEOUT_MS", 50)
    holder = sqlite3.connect(project / "kyosei" / "kyosei.db", isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    yield holder
    if holder.in_transaction:
        holder.execute("ROLLBACK")
    holder.close()


def test_c5_a_write_that_loses_the_lock_race_is_refused_in_words(project, held_lock):
    db_path = project / "kyosei" / "kyosei.db"

    result = invoke(_write_args(project))

    assert result.code == EXIT_ERROR, result
    lines = result.err.strip().splitlines()
    assert len(lines) == 1, result
    assert "Traceback" not in result.err
    assert str(db_path) in lines[0]
    assert "another process is writing" in lines[0]
    # The writer is not knowable from SQLite, and the message says so rather than guess.
    assert "SQLite does not say which process holds a lock" in lines[0]
    assert "run this command again once the other kyo command finishes" in lines[0]


def test_c5_the_json_envelope_carries_the_refusal(project, held_lock):
    result = invoke(_write_args(project, "--json"))

    assert result.code == EXIT_ERROR, result
    payload = json.loads(result.out)
    assert payload["ok"] is False
    assert payload["command"] == "new"
    assert payload["exit_code"] == EXIT_ERROR
    assert "is locked" in payload["error"]
    assert "exception_type" not in payload  # a deliberate refusal, not an unexpected one


def test_c5_retrying_after_the_other_writer_releases_clears_it(project, held_lock):
    """The remedy the refusal names, run exactly: the other writer finishes, retry."""
    refused = invoke(_write_args(project))
    assert "run this command again once the other kyo command finishes" in refused.err

    held_lock.execute("COMMIT")
    retried = invoke(_write_args(project, "--json"))

    assert retried.code == 0, retried
    assert retried.json["record"]["id"].startswith("TST-")


def test_c5_lock_contention_is_recognised_by_code_not_only_by_text():
    assert store_module.is_lock_contention(sqlite3.OperationalError("database is locked"))
    assert not store_module.is_lock_contention(sqlite3.OperationalError("no such table: x"))
    assert not store_module.is_lock_contention(OSError("database is locked"))


# ------------------------------------------------------------ c6: unknown subcommand


def _suggested(err: str) -> list[str]:
    line = next((ln for ln in err.splitlines() if "did you mean" in ln), "")
    return re.findall(r"`kyo ([\w-]+)`", line)


@pytest.mark.parametrize(("typo", "meant"), [("lsit", "list"), ("flags", "flag")])
def test_c6_a_near_miss_names_the_command_before_the_choices(typo, meant):
    result = invoke([typo])

    assert result.code == EXIT_USAGE, result
    assert f"did you mean `kyo {meant}`" in result.err
    assert result.err.index("did you mean") < result.err.index("choose from")
    assert meant in _suggested(result.err)


def test_c6_no_close_match_prints_no_suggestion():
    result = invoke(["xyzzy"])

    assert result.code == EXIT_USAGE, result
    assert "did you mean" not in result.err
    assert "choose from" in result.err


def test_c6_the_suggested_command_is_one_the_parser_accepts():
    """Running what the line suggests gets past the parser (the remedy, proved)."""
    suggested = _suggested(invoke(["lsit"]).err)
    assert suggested

    args = build_parser().parse_args([suggested[0]])

    assert args.command == "list"


# ---------------------------------------------- c7: a refusing guard keeps its block

PUSH_PAYLOAD = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push"}})


def test_c7_check_push_still_blocks_when_the_store_is_unreadable(project):
    git_init(project)
    blocked = invoke(
        ["--root", str(project), "check-push", "--pre-tool-use"], stdin=PUSH_PAYLOAD
    )
    assert blocked.code == EXIT_CONTRACT, blocked

    # The KYO-335 condition: this build cannot open the store (a config key it does
    # not read), and the invocation is hook-shaped.
    config = project / "kyosei" / "config.yml"
    config.write_text(
        config.read_text(encoding="utf-8") + "\na_key_from_a_later_build: 1\n",
        encoding="utf-8",
    )
    assert invoke(["--root", str(project), "list"]).code != 0  # really unreadable

    result = invoke(
        ["--root", str(project), "check-push", "--pre-tool-use"], stdin=PUSH_PAYLOAD
    )

    assert "NOTHING WAS CHECKED" not in result.out + result.err, result
    assert result.code == EXIT_CONTRACT, result
    assert "push-publishes-the-store" in result.err
