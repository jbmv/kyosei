"""KYO-422: per-turn hook costs that grow with the store or the tree.

Three of the four parts the record names ship here, and each test below fails if its
mechanism is taken back out:

* ``Store.description_edited_at`` compares the two snapshots' ``description`` inside
  SQLite rather than ``json.loads``-ing both whole documents in Python (c3, c4);
* ``docs_targets._SymbolIndex`` prunes ``_UNSEARCHED_DIRS`` from the walk rather than
  listing them in full and filtering afterwards (c6, c7);
* ``kyo pre-tool-use`` runs both PreToolUse guards against one read of the payload,
  in one interpreter (c8, c9, c10), opt-in: the packaged hook still names the two
  separate commands (c11).

The fourth, a single ``ps`` snapshot for ``kyo sessions --hook``, was built, measured
slower than the walk it would replace at every depth that walk reaches, and not
shipped; the measurement is on :func:`kyosei.cmd_sessions.session_process`.
"""

from __future__ import annotations

import json
import os
import sys
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from kyosei import cli, cmd_check_build, cmd_check_command, docs_targets
from kyosei.commands import PROJECT_OPTIONAL
from kyosei.errors import EXIT_CONTRACT, KyoseiError
from kyosei.store import open_store

from .conftest import git_init, invoke, new_record
from .test_subagent_stop_worktree import _running_dispatch

REPO = Path(__file__).resolve().parent.parent


# ------------------------------------------------ description_edited_at (c3, c4)


def _reference_description_edited_at(store, record_id: str) -> str:
    """The implementation before KYO-422, as the answer to agree with -- verbatim for a
    row with a whole ``before_json``, and widened by KYO-418 to the ``changed_json`` a
    row written from schema_version 25 carries instead (a present key is a change)."""
    row = store.record(record_id)
    for event in store.conn.execute(
        "SELECT at, before_json, after_json, changed_json FROM events WHERE record_id = ?"
        " ORDER BY seq DESC",
        (record_id,),
    ):
        if event["after_json"] is None:
            continue
        if event["before_json"] is not None:
            before = json.loads(event["before_json"])
            after = json.loads(event["after_json"])
            if before.get("description") != after.get("description"):
                return str(event["at"])
        elif event["changed_json"] is not None:
            if "description" in json.loads(event["changed_json"]):
                return str(event["at"])
    return str(row["created_at"])


def _describe(kyo, project: Path, record: str, text: str) -> None:
    path = project / "description.md"
    path.write_text(text, encoding="utf-8")
    assert kyo("edit", record, "--description-file", str(path)).code == 0


def _latest_seq(project: Path, record: str) -> int:
    store = open_store(project, code_root=project)
    try:
        return store.conn.execute(
            "SELECT MAX(seq) FROM events WHERE record_id = ?", (record,)
        ).fetchone()[0]
    finally:
        store.close()


@pytest.fixture
def described(kyo, git_project: Path) -> tuple[dict[str, str], dict[str, str | None]]:
    """One record per case c3 names, keyed by the case, and for each the ``at`` of the
    event that must be the answer (``None``: the record's ``created_at``).

    Every event's ``at`` is rewritten to a value unique to it afterwards, because a
    whole fixture runs inside one millisecond and "the right event" is otherwise
    indistinguishable from "any event of this record" or from ``created_at``.
    """
    cases = {
        "no events": new_record(kyo, criteria=["a"], title="no events"),
        "never edited": new_record(kyo, criteria=["a"], title="never edited"),
        "edited once": new_record(kyo, criteria=["a"], title="edited once"),
        "edited then unrelated": new_record(kyo, criteria=["a"], title="edited, then not"),
        "missing after": new_record(kyo, criteria=["a"], title="missing after"),
        "missing before": new_record(kyo, criteria=["a"], title="missing before"),
    }
    answer: dict[str, int | None] = {"no events": None, "never edited": None}
    assert kyo("edit", cases["never edited"], "--title", "retitled").code == 0
    _describe(kyo, git_project, cases["edited once"], "The first description.\n")
    answer["edited once"] = _latest_seq(git_project, cases["edited once"])
    _describe(kyo, git_project, cases["edited then unrelated"], "Described.\n")
    answer["edited then unrelated"] = _latest_seq(git_project, cases["edited then unrelated"])
    assert kyo("edit", cases["edited then unrelated"], "--title", "moved on").code == 0
    broken: dict[int, str] = {}
    for case, side in (("missing after", "after_json"), ("missing before", "before_json")):
        _describe(kyo, git_project, cases[case], "Described.\n")
        answer[case] = _latest_seq(git_project, cases[case])
        # A later description change whose event lost one snapshot: both
        # implementations must skip it and find the intact one before it.
        _describe(kyo, git_project, cases[case], "Described again.\n")
        broken[_latest_seq(git_project, cases[case])] = side
    store = open_store(git_project, code_root=git_project)
    try:
        with store.transaction() as conn:
            conn.execute("DELETE FROM events WHERE record_id = ?", (cases["no events"],))
            for seq, side in broken.items():
                conn.execute(f"UPDATE events SET {side} = NULL WHERE seq = ?", (seq,))
                if side == "before_json":
                    # KYO-418: a row written from schema_version 25 holds its prior
                    # value in `changed_json`; "lost its before" loses that too.
                    conn.execute("UPDATE events SET changed_json = NULL WHERE seq = ?", (seq,))
            conn.execute("UPDATE events SET at = printf('2099-01-01T00:00:00.%06dZ', seq)")
    finally:
        store.close()
    expected = {
        case: None if seq is None else f"2099-01-01T00:00:00.{seq:06d}Z"
        for case, seq in answer.items()
    }
    return cases, expected


def test_c3_description_edited_at_agrees_with_the_python_walk_on_every_case(
    described, git_project: Path
) -> None:
    cases, expected = described
    store = open_store(git_project, code_root=git_project)
    try:
        for case, record in cases.items():
            new = store.description_edited_at(record)
            assert new == _reference_description_edited_at(store, record), case
            # And each case is the case it claims to be, not six records that all
            # fall back to `created_at`.
            assert new == (expected[case] or store.record(record)["created_at"]), case
    finally:
        store.close()


def test_c4_the_lookup_parses_no_event_snapshot_in_python(
    described, git_project: Path, monkeypatch
) -> None:
    """The whole point: the snapshots are up to 2.9 MB each, and every one of them was
    parsed for a record never edited. Put the Python parse back and this goes red."""
    store = open_store(git_project, code_root=git_project)
    try:
        records = list(described[0].values())
        expected = {r: _reference_description_edited_at(store, r) for r in records}

        def refuse(*args, **kwargs):
            raise AssertionError("description_edited_at called json.loads on a snapshot")

        monkeypatch.setattr(json, "loads", refuse)
        got = {r: store.description_edited_at(r) for r in records}
    finally:
        monkeypatch.undo()
        store.close()
    assert got == expected


# ---------------------------------------------------------- _SymbolIndex (c6, c7)


def _reference_symbol_files(root: Path, bases: list[str]) -> list[tuple[str, str]]:
    """``_SymbolIndex._read`` before KYO-422, verbatim: the ``rglob`` walk."""
    seen: dict[str, str] = {}
    for base in bases:
        start = docs_targets._under_root(root, base)
        if start is None or not start.is_dir():
            continue
        for path in sorted(start.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in docs_targets.SEARCHABLE_SUFFIXES:
                continue
            if docs_targets._UNSEARCHED_DIRS & set(path.relative_to(start).parts[:-1]):
                continue
            rel = path.relative_to(root).as_posix()
            if rel in seen:
                continue
            seen[rel] = path.read_text(encoding="utf-8", errors="replace")
    return sorted(seen.items())


@pytest.fixture
def symbol_tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    files = {
        "src/pkg/alpha.py": "def frobnicate_widgets():\n    pass\n",
        "src/pkg/deeper/beta.py": "frobnicate_widgets()\n",
        "src/zeta.ts": "export const x = frobnicate_widgets();\n",
        "docs/guide.md": "Call frobnicate_widgets to begin (not a searchable suffix).\n",
        "scripts/run.sh": "frobnicate_widgets\n",
        "scripts/other.sh": "nothing relevant\n",
        # Excluded wherever they sit below a base, never the base itself.
        "node_modules/lib/index.js": "frobnicate_widgets()\n",
        "src/node_modules/x.js": "frobnicate_widgets()\n",
        ".git/hooks/frob.py": "frobnicate_widgets()\n",
        "src/pkg/__pycache__/alpha.py": "frobnicate_widgets()\n",
        "build/out.py": "frobnicate_widgets()\n",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_c7_find_returns_the_same_files_in_the_same_order(symbol_tree: Path) -> None:
    # "." and "src" both reach src/pkg/alpha.py: the duplicate through two bases.
    bases = [".", "src"]
    index = docs_targets._SymbolIndex(symbol_tree, bases)

    assert index._read() == _reference_symbol_files(symbol_tree, bases)
    reference = docs_targets._SymbolIndex(symbol_tree, bases)
    reference._files = _reference_symbol_files(symbol_tree, bases)
    found = index.find("frobnicate_widgets")
    assert found == reference.find("frobnicate_widgets")
    assert found == (
        "scripts/run.sh",
        "src/pkg/alpha.py",
        "src/pkg/deeper/beta.py",
        "src/zeta.ts",
    )


def test_c6_an_unsearched_directory_is_never_entered(symbol_tree: Path, monkeypatch) -> None:
    """Asserted on the listing itself: every directory the walk opens is recorded, and
    none of them may be inside an excluded one. `rglob` opened every one of them."""
    opened: list[Path] = []
    real_scandir = os.scandir

    def recording(path=".", *args, **kwargs):
        opened.append(Path(os.fsdecode(path)))
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", recording)
    docs_targets._SymbolIndex(symbol_tree, [".", "src"])._read()
    monkeypatch.undo()

    assert opened, "the walk opened no directory at all -- this test measures nothing"
    entered = [
        path
        for path in opened
        if docs_targets._UNSEARCHED_DIRS & set(path.resolve().relative_to(symbol_tree).parts)
    ]
    assert not entered, entered


# ------------------------------------------------------ kyo pre-tool-use (c8-c10)


def _payload(command: str | None, cwd: Path) -> str:
    return json.dumps(
        {
            "session_id": "s1",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "cwd": str(cwd),
            "tool_input": {} if command is None else {"command": command},
        }
    )


def _separately(project: Path, stdin: str):
    push = invoke(["--root", str(project), "check-push", "--pre-tool-use"], stdin=stdin)
    command = invoke(["--root", str(project), "check-command", "--pre-tool-use"], stdin=stdin)
    return push, command


def _together(project: Path, stdin: str):
    return invoke(["--root", str(project), "pre-tool-use"], stdin=stdin)


@pytest.fixture
def guarded(kyo, tmp_path: Path) -> tuple[Path, Path]:
    """An in-repo project (so an agent's push publishes it) that declares `make test`
    controller-only, with a running dispatch whose worktree is ``tree``."""
    project = kyo.project
    git_init(project)
    _dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt")
    config = project / "kyosei" / "config.yml"
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["controller_only_commands"] = ["make test"]
    config.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return project, tree


def test_c8_a_command_neither_refuses_is_silent(guarded) -> None:
    project, _tree = guarded
    stdin = _payload("ls", project)
    push, command = _separately(project, stdin)
    together = _together(project, stdin)

    assert (push.code, command.code) == (0, 0)
    assert together.code == 0, together
    assert together.out == "" and together.err == "", together


@pytest.mark.parametrize("stdin", ["", "   ", "not json", "[1, 2]", _payload(None, Path("/"))])
def test_c8_an_empty_or_malformed_payload_is_silent(guarded, stdin: str) -> None:
    project, _tree = guarded
    push, command = _separately(project, stdin)
    together = _together(project, stdin)

    assert (push.code, command.code) == (0, 0)
    assert together.code == 0, together
    assert together.out == "" and together.err == "", together


def test_c8_c9_a_push_check_push_refuses_is_refused_with_its_own_words(guarded) -> None:
    project, _tree = guarded
    stdin = _payload("git push origin main", project)
    push, command = _separately(project, stdin)
    together = _together(project, stdin)

    assert push.code == EXIT_CONTRACT and command.code == 0
    assert together.code == EXIT_CONTRACT, together
    assert together.err == push.err


def test_c8_c9_the_suite_check_command_refuses_is_refused_with_its_own_words(guarded) -> None:
    project, tree = guarded
    stdin = _payload(f'cd "{tree}" && make test', project)
    push, command = _separately(project, stdin)
    together = _together(project, stdin)

    assert push.code == 0 and command.code == EXIT_CONTRACT
    assert together.code == EXIT_CONTRACT, together
    assert together.err == command.err


def test_c8_a_command_both_refuse_carries_both_refusals(guarded) -> None:
    project, tree = guarded
    stdin = _payload(f'cd "{tree}" && make test && cd "{project}" && git push', project)
    push, command = _separately(project, stdin)
    together = _together(project, stdin)

    assert push.code == EXIT_CONTRACT and command.code == EXIT_CONTRACT, (push, command)
    assert together.code == EXIT_CONTRACT, together
    for separate in (push, command):
        for line in separate.err.splitlines()[1:]:
            assert line in together.err, line
    # Publication first, as the packaged groups run.
    assert together.err.index("push-publishes-the-store") < together.err.index(
        "suite-is-the-orchestrators"
    )
    envelope = invoke(["--root", str(project), "pre-tool-use", "--json"], stdin=stdin).json
    assert envelope["refused_by"] == ["check-push", "check-command"]


def test_c8_stdin_is_read_once(guarded, monkeypatch) -> None:
    """Both checks see the payload although the stream can only be read once: a second
    read returns "", which would make the second check silently check nothing."""
    project, tree = guarded
    stdin = _payload(f'cd "{tree}" && make test && cd "{project}" && git push', project)
    reads: list[int] = []

    class OnceOnly:
        closed = False

        def __init__(self, text: str) -> None:
            self.text = text

        def read(self) -> str:
            reads.append(1)
            text, self.text = self.text, ""
            return text

        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(sys, "stdin", OnceOnly(stdin))
    result = invoke(["--root", str(project), "pre-tool-use", "--json"])

    assert len(reads) == 1
    assert result.code == EXIT_CONTRACT
    assert json.loads(result.out)["refused_by"] == ["check-push", "check-command"]


def test_c10_it_is_project_optional_and_writes_nothing(tmp_path: Path) -> None:
    assert cli.COMMAND_TABLE["pre-tool-use"] == (
        cmd_check_command.cmd_pre_tool_use,
        PROJECT_OPTIONAL,
    )
    assert "pre-tool-use" not in cli.MUTATING_COMMANDS
    elsewhere = tmp_path / "not-a-project"
    elsewhere.mkdir()

    result = invoke(
        ["--root", str(elsewhere), "pre-tool-use"], stdin=_payload("make test", elsewhere)
    )

    assert result.code == 0, result
    assert result.out == "" and result.err == ""


def test_c10_it_is_a_hook_invocation(tmp_path: Path) -> None:
    assert "pre-tool-use" in {shape.command for shape in cmd_check_build.HOOK_INVOCATIONS}
    assert cmd_check_build.hook_invocation(Namespace(command="pre-tool-use")) is not None


def _unreadable(project: Path) -> None:
    config = project / "kyosei" / "config.yml"
    config.write_text(
        config.read_text(encoding="utf-8") + "\na_key_from_a_later_build: 1\n",
        encoding="utf-8",
    )


def test_c10_an_unreadable_store_says_nothing_was_checked_and_blocks_nothing(guarded) -> None:
    """check-command's own sentence, which it says at exit 0 rather than raising: it
    is not silence, because silence would read as "checked, nothing to refuse"."""
    project, tree = guarded
    _unreadable(project)

    result = _together(project, _payload(f'cd "{tree}" && make test', project))

    assert result.code == 0, result
    assert "NOTHING WAS CHECKED" in result.out
    assert "a_key_from_a_later_build" in result.out


def test_c10_a_failure_that_escapes_prints_the_hook_line_non_blocking(
    guarded, monkeypatch
) -> None:
    """When a check RAISES on an unreadable store, `cli.main`'s KYO-335 path names this
    command, and the exit is neither 0 nor the blocking 2."""
    project, _tree = guarded
    _unreadable(project)

    def cannot_measure(ctx, args, raw=None):
        raise KyoseiError("the store could not be measured")

    monkeypatch.setattr(cmd_check_command, "cmd_check_push", cannot_measure)
    result = _together(project, _payload("git push", project))

    first = result.err.strip().splitlines()[0]
    assert first.startswith("kyo pre-tool-use: NOTHING WAS CHECKED"), result
    assert result.code not in (0, 2), result


def test_c10_the_remedy_the_hook_line_names_clears_it(guarded, monkeypatch) -> None:
    """The line's remedy is "undo the store-side change this build refuses"."""
    project, _tree = guarded
    _unreadable(project)
    before = _together(project, _payload("ls", project))
    assert before.code == 0, before
    assert "NOTHING WAS CHECKED" in before.out, before

    config = project / "kyosei" / "config.yml"
    config.write_text(
        "\n".join(
            line
            for line in config.read_text(encoding="utf-8").splitlines()
            if not line.startswith("a_key_from_a_later_build:")
        )
        + "\n",
        encoding="utf-8",
    )
    after = _together(project, _payload("ls", project))
    assert after.code == 0 and after.out == "" and after.err == "", after


def test_c12_the_readme_documents_the_opt_in_entry_point() -> None:
    from tests.reference_pages import OPERATING

    readme = OPERATING.read_text(encoding="utf-8")  # the README section, moved (KYO-516)
    assert "kyo pre-tool-use" in readme
    line = next(line for line in readme.splitlines() if line.startswith("kyo pre-tool-use"))
    assert "opt-in" in line


def test_c11_the_packaged_hook_still_names_the_two_commands() -> None:
    """This run leaves the default alone (see the module docstring and
    `cmd_check_command.cmd_pre_tool_use`); switching it is its own change, after a re-pack."""
    fragment = json.loads(
        (REPO / "src" / "kyosei" / "defaults" / "hooks" / "pre-tool-use.json").read_text(
            encoding="utf-8"
        )
    )
    commands = [hook["command"] for group in fragment["PreToolUse"] for hook in group["hooks"]]
    assert any(c.endswith("kyo check-push --pre-tool-use") for c in commands)
    assert any(c.endswith("kyo check-command --pre-tool-use") for c in commands)
