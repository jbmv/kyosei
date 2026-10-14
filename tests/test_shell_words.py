"""KYO-409.c6: the one shell reading both blocking `PreToolUse` guards share.

`kyo check-push` and `kyo check-command` each carried a private tokeniser with the same
separator tuple, and both missed the same shapes. These tests pin the shared module's
own behaviour — segmenting, never-raising tokenising, the env prefix, wrapper skipping,
directory following — and the stated holes, so a change that closes one changes a test
deliberately rather than by accident. The guards' own behaviour over this reading is
tested beside each guard (tests/test_kyo60_push_guard.py,
tests/test_kyo298_command_guard.py).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from kyosei import shell_words
from kyosei.shell_words import invocation, segments, tokens, walk

SRC = Path(__file__).resolve().parents[1] / "src" / "kyosei"

# ------------------------------------------------------------------ segmenting


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("a && b || c; d | e", ["a", "b", "c", "d", "e"]),
        ("sleep 1 & git status", ["sleep 1", "git status"]),
        ("a |& b", ["a", "b"]),
        ("a\nb", ["a", "b"]),
        ("make test > log 2>&1 &", ["make test > log 2>&1"]),
        ("cmd &> /dev/null; b", ["cmd &> /dev/null", "b"]),
        ('echo "a && b; c | d & e"', ['echo "a && b; c | d & e"']),
        ("echo 'a;b' ; c", ["echo 'a;b'", "c"]),
        ("echo $(a && b); c", ["echo $(a && b)", "c"]),
        ("echo `a; b`; c", ["echo `a; b`", "c"]),
        ("(a && b) || c", ["a", "b", "c"]),
        ("a # b && c\nd", ["a", "d"]),
        ("echo a\\\nb", ["echo ab"]),
        ("cat <<'EOF'\nx && y\nEOF\nz", ["cat <<'EOF'", "z"]),
        ("cat <<-EOF\n\tx\n\tEOF\nz", ["cat <<-EOF", "z"]),
        ("echo x<<<'y'; z", ["echo x<<<'y'", "z"]),
    ],
)
def test_segments_in_order(command: str, expected: list[str]) -> None:
    assert segments(command) == expected


@pytest.mark.parametrize("command", ['echo "unclosed', "echo 'unclosed", "a $(b", "`a", "(", ")"])
def test_nothing_raises(command: str) -> None:
    """A hook that died on what a worker really types would block the tool call with a
    traceback; every one of these returns something."""
    segments(command)
    walk(command, None)
    assert isinstance(tokens(command), list)


# ------------------------------------------------------------------ invocations


@pytest.mark.parametrize(
    ("words", "argv", "env"),
    [
        ("FOO=1 BAR=2 make test", ("make", "test"), {"FOO": "1", "BAR": "2"}),
        ("env make test", ("make", "test"), {}),
        ("env -i FOO=1 make test", ("make", "test"), {"FOO": "1"}),
        ("env -u HOME make test", ("make", "test"), {}),
        ("env -S 'make test'", ("make", "test"), {}),
        ("command make test", ("make", "test"), {}),
        ("command -p make test", ("make", "test"), {}),
        ("nohup make test", ("make", "test"), {}),
        ("time -p make test", ("make", "test"), {}),
        ("timeout 900 make test", ("make", "test"), {}),
        ("timeout -s KILL -k 5 900 make test", ("make", "test"), {}),
        ("timeout --signal=KILL 900 make test", ("make", "test"), {}),
        ("nice -n 5 make test", ("make", "test"), {}),
        ("exec make test", ("make", "test"), {}),
        ("A=1 nohup env B=2 time make test", ("make", "test"), {"A": "1", "B": "2"}),
        ("make test > /tmp/r.log 2>&1", ("make", "test"), {}),
        ("> /tmp/r.log make test", ("make", "test"), {}),
        ("/usr/bin/env git push", ("git", "push"), {}),
    ],
)
def test_the_command_word_past_wrappers_env_and_redirections(
    words: str, argv: tuple[str, ...], env: dict[str, str]
) -> None:
    call = invocation(tokens(words))
    assert call.argv == argv
    assert call.env == env


def test_a_lookup_runs_nothing() -> None:
    assert invocation(tokens("command -v git")).argv == ()
    assert invocation(tokens("command -V make")).argv == ()


def test_a_bare_assignment_keeps_its_environment() -> None:
    """``KYOSEI_LONG_OK=1; until …`` — the override written as its own segment, which
    `kyo check-command`'s timeout override reads (KYO-312)."""
    (step,) = walk("KYOSEI_LONG_OK=1", None)
    assert step.invocation.argv == ()
    assert step.invocation.env == {"KYOSEI_LONG_OK": "1"}


def test_an_argument_that_spells_a_command_is_an_argument() -> None:
    assert invocation(tokens('echo "make test"')).argv == ("echo", "make test")
    assert invocation(tokens("grep -rn 'git push' .")).name == "grep"


# ------------------------------------------------------------------ directories


def test_cd_is_followed_in_order(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    steps = walk(f'x; cd "{a}" && y; cd ../b && z', tmp_path)
    assert [(s.invocation.argv[0], s.cwd) for s in steps] == [
        ("x", tmp_path.resolve()),
        ("y", a.resolve()),
        ("z", b.resolve()),
    ]


def test_a_subshell_cd_is_undone_at_its_close(tmp_path: Path) -> None:
    steps = walk(f'(cd "{tmp_path}" && inner); outer', Path("/"))
    assert [(s.invocation.argv[0], s.cwd) for s in steps] == [
        ("inner", tmp_path.resolve()),
        ("outer", Path("/")),
    ]


def test_a_cd_in_a_pipeline_or_the_background_moves_nobody(tmp_path: Path) -> None:
    for command in (f'cd "{tmp_path}" | after', f'cd "{tmp_path}" & after'):
        (step,) = walk(command, Path("/"))
        assert step.cwd == Path("/"), command


def test_pushd_popd_and_env_c(tmp_path: Path) -> None:
    steps = walk(f'pushd "{tmp_path}" && a; popd && b; env -C "{tmp_path}" c', Path("/"))
    assert [(s.invocation.argv[0], s.cwd) for s in steps] == [
        ("a", tmp_path.resolve()),
        ("b", Path("/")),
        ("c", tmp_path.resolve()),
    ]


@pytest.mark.parametrize(
    "command",
    ['cd "$TREE" && x', "cd `pwd` && x", "cd - && x", "cd a b && x", "popd && x", "pushd +1 && x"],
)
def test_what_cannot_be_followed_is_unknown_not_borrowed(command: str) -> None:
    """Law 6: an unknown directory is ``None``, never the previous one — a blocking hook
    that guessed where a command runs is the one that gets disabled."""
    (step,) = walk(command, Path("/"))
    assert step.cwd is None, command


def test_a_relative_cd_from_an_unknown_place_stays_unknown() -> None:
    (step,) = walk("cd sub && x", None)
    assert step.cwd is None


def test_cd_alone_is_home() -> None:
    (step,) = walk("cd && x", Path("/"))
    assert step.cwd == Path.home().resolve()


# ------------------------------------------------------------------ the stated holes


def test_the_stated_holes_stay_stated() -> None:
    """Recorded so closing one is a deliberate change here, and matching the README's
    list: shell keywords are read as command words, ``$( … )`` bodies are not searched,
    and ``sudo``/``bash -c`` are not wrappers."""
    assert walk("for x in 1; do make test; done", None)[1].invocation.name == "do"
    assert [s.invocation.name for s in walk("echo $(make test)", None)] == ["echo"]
    assert invocation(tokens("sudo make test")).name == "sudo"
    assert invocation(tokens("bash -c 'make test'")).name == "bash"


# ------------------------------------------------------------------ one reading, not two


def test_both_guards_import_the_shared_reading_and_carry_no_copy_of_their_own() -> None:
    """KYO-409.c6. Neither guard module defines a segmenter, tokeniser, env-prefix
    splitter or cd-follower, nor calls :mod:`shlex` itself; both import this module."""
    private_copies = {
        "_segments",
        "_shell_segments",
        "_tokens",
        "_split_env_prefix",
        "_followed_cd",
        "_SEPARATORS",
        "_COMMAND_SEPARATORS",
    }
    for name in ("cmd_publish.py", "cmd_check_command.py"):
        tree = ast.parse((SRC / name).read_text(encoding="utf-8"))
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.ClassDef)
        } | {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        assert not defined & private_copies, (name, defined & private_copies)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom | ast.Import)
            for alias in node.names
        }
        assert "shlex" not in imported, name
        assert "shell_words" in imported, name
    assert shell_words.SEPARATORS  # the module this test is about exists
