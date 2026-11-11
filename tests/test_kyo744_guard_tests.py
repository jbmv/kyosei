"""KYO-744: a project declares guard tests by the paths that call for them, and a
dispatch's brief names the ones its touches match.

A worker runs the tests that cover its change and leaves the suite to its orchestrator
(KYO-297). A guard test covers no change in particular (it asserts that every command is
classified, or that no flag breaks a naming rule), so the worker never picks it and the
suite finds the failure after the merge. ``guard_tests`` in ``kyosei/config.yml`` maps
globs of touched paths to the test files to run; `kyo dispatch` states the matching ones
in the brief, and `kyo doctor` names a declared test file that does not exist.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from kyosei.config import (
    GuardRule,
    guard_glob_matches,
    matched_guard_tests,
    parse_guard_tests,
    read_guard_tests,
)
from kyosei.errors import KyoseiError
from kyosei.schema import CONFIG_CONSUMERS, OPTIONAL_CONFIG_KEYS

from .conftest import new_record
from .this_store import REPO_ROOT, store_path

RULES = """\
guard_tests:
  - touches:
      - src/app/cli.py
      - src/app/serve*.py
    tests:
      - tests/test_classified.py
      - tests/test_flags.py::test_no_time_flag
    run: .venv/bin/python -m pytest
    note: seconds each
  - touches: apps/desktop/
    tests: apps/desktop/tests/commands.spec.js
    run: npx playwright test
"""


def _config(project: Path) -> Path:
    return project / "kyosei" / "config.yml"


def _declare(project: Path, block: str) -> None:
    """Replace any ``guard_tests`` the config carries with ``block``."""
    path = _config(project)
    head = path.read_text(encoding="utf-8").split("\nguard_tests:", 1)[0]
    path.write_text(head.rstrip("\n") + "\n" + block, encoding="utf-8")


def _write(project: Path, *files: str) -> None:
    for name in files:
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")


def _touching(kyo, tmp_path: Path, *touches: str) -> str:
    record = new_record(kyo, criteria=["a"])
    scope = tmp_path / f"touches-{record}.txt"
    scope.write_text("\n".join(touches) + "\n", encoding="utf-8")
    assert kyo("edit", record, "--touches-file", str(scope)).code == 0
    return record


def _brief_for(kyo, record: str) -> str:
    return kyo("show", record, "--json").json["dispatches"][0]["brief"]


# ------------------------------------------------------------- c1: the declaration


def test_a_project_declares_guard_tests_by_the_paths_that_call_for_them(
    kyo, git_project: Path
) -> None:
    """c1. Globs of touched paths, the test files, and the command they are given to."""
    _declare(git_project, RULES)
    assert read_guard_tests(git_project) == (
        GuardRule(
            touches=("src/app/cli.py", "src/app/serve*.py"),
            tests=("tests/test_classified.py", "tests/test_flags.py::test_no_time_flag"),
            run=".venv/bin/python -m pytest",
            note="seconds each",
        ),
        GuardRule(
            touches=("apps/desktop/",),
            tests=("apps/desktop/tests/commands.spec.js",),
            run="npx playwright test",
        ),
    )
    # The key is one every command accepts: the config still loads.
    assert kyo("brief").code == 0


def test_absent_and_empty_both_declare_nothing(kyo, git_project: Path) -> None:
    assert read_guard_tests(git_project) == ()
    _declare(git_project, "guard_tests: []\n")
    assert read_guard_tests(git_project) == ()


def test_the_key_is_registered_optional_with_both_readers() -> None:
    """Law 2: `kyo doctor`'s config_consumers check resolves these against the source."""
    assert CONFIG_CONSUMERS["guard_tests"] == (
        "cmd_dispatch._cmd_dispatch_create",
        "doctor_install.check_guard_tests",
    )
    assert "guard_tests" in OPTIONAL_CONFIG_KEYS


def test_the_declaration_survives_the_rebuild_that_rewrites_the_config(
    kyo, git_project: Path
) -> None:
    _declare(git_project, RULES)
    before = read_guard_tests(git_project)
    assert kyo("rebuild", "--force").code == 0
    assert read_guard_tests(git_project) == before


@pytest.mark.parametrize(
    ("glob", "touch", "expected"),
    [
        # A file touch matches as its path; `*` stops at a slash.
        ("src/app/cli.py", "src/app/cli.py", True),
        ("src/app/serve*.py", "src/app/serve_reads.py", True),
        ("src/app/serve*.py", "src/app/sub/serve_reads.py", False),
        ("*.md", "docs/README.md", False),
        ("src/app/cl?.py", "src/app/cli.py", True),
        ("src/[a-c]pp/cli.py", "src/app/cli.py", True),
        ("src/[!a-c]pp/cli.py", "src/app/cli.py", False),
        ("src/app/cli.py", "src/app/cliXpy", False),
        # `**` crosses directories, and a trailing slash is the whole tree.
        ("src/**/*.py", "src/app/deep/x.py", True),
        ("src/**/*.py", "src/x.py", True),
        ("src/", "src/app/x.py", True),
        ("src/", "srcs/x.py", False),
        # A new-file touch matches as the path it will create.
        ("src/app/cmd_*.py", "+src/app/cmd_new.py", True),
        # A directory touch claims its tree, so it matches a glob that reaches into it.
        ("src/app/cli.py", "src/app/", True),
        ("src/app/cli.py", "src/", True),
        ("src/**/*.py", "src/app/deep/", True),
        ("apps/desktop/", "apps/", True),
        ("apps/desktop/", "apps/desktop/renderer/", True),
        ("src/app/cli.py", "tests/", False),
        ("src/*.py", "src/app/", False),
        ("apps/desktop/", "apps/apple/", False),
    ],
)
def test_a_glob_reads_each_touch_form_as_what_it_claims(
    glob: str, touch: str, expected: bool
) -> None:
    assert guard_glob_matches(glob, touch) is expected


def test_rules_sharing_a_runner_merge_into_one_command() -> None:
    rules = (
        GuardRule(("src/app/cli.py",), ("tests/test_a.py", "tests/test_b.py"), "pytest -q"),
        GuardRule(("src/app/*.py",), ("tests/test_b.py", "tests/test c.py"), "pytest -q", "n"),
        GuardRule(("src/app/",), ("spec.js",), "npx playwright test"),
        GuardRule(("docs/",), ("tests/test_docs.py",), "pytest -q"),
    )
    pytest_cmd, playwright_cmd = matched_guard_tests(rules, {"src/app/cli.py"})
    assert pytest_cmd.command == "pytest -q tests/test_a.py tests/test_b.py 'tests/test c.py'"
    assert pytest_cmd.notes == ("n",)
    assert ("src/app/cli.py", "src/app/*.py") in pytest_cmd.reasons
    assert playwright_cmd.command == "npx playwright test spec.js"
    assert matched_guard_tests(rules, {"README.md"}) == ()
    assert matched_guard_tests(rules, set()) == ()


MALFORMED = [
    "guard_tests: tests/test_a.py\n",
    "guard_tests:\n  - tests/test_a.py\n",
    "guard_tests:\n  - touches: a.py\n    tests: t.py\n    run: pytest\n    when: always\n",
    "guard_tests:\n  - tests: t.py\n    run: pytest\n",
    "guard_tests:\n  - touches: []\n    tests: t.py\n    run: pytest\n",
    "guard_tests:\n  - touches: a.py\n    run: pytest\n",
    "guard_tests:\n  - touches: a.py\n    tests: t.py\n",
    "guard_tests:\n  - touches: a.py\n    tests: t.py\n    run: ''\n",
    "guard_tests:\n  - touches: /abs/a.py\n    tests: t.py\n    run: pytest\n",
    "guard_tests:\n  - touches: ../a.py\n    tests: t.py\n    run: pytest\n",
    "guard_tests:\n  - touches: 'a.py#main'\n    tests: t.py\n    run: pytest\n",
    "guard_tests:\n  - touches: +a.py\n    tests: t.py\n    run: pytest\n",
    "guard_tests:\n  - touches: a.py\n    tests: '::test_x'\n    run: pytest\n",
    "guard_tests:\n  - touches: a.py\n    tests: t.py\n    run: pytest\n    note: ''\n",
]


@pytest.mark.parametrize("block", MALFORMED)
def test_a_malformed_declaration_is_refused_and_each_named_remedy_clears_it(
    kyo, git_project: Path, block: str
) -> None:
    """The refusal names two remedies: write the printed shape, or delete the key."""
    _declare(git_project, block)
    with pytest.raises(KyoseiError) as refusal:
        read_guard_tests(git_project)
    message = str(refusal.value)
    assert "guard_tests" in message and "hand edit" in message
    # It refuses `kyo dispatch` and nothing else: the config still loads for a hook.
    record = new_record(kyo, criteria=["a"])
    refused = kyo("dispatch", record, "--branch", "kyo-744-x")
    assert refused.code != 0 and "guard_tests" in refused.err
    assert kyo("show", record, "--json").json["dispatches"] == []
    assert kyo("brief").code == 0

    # Remedy one: the shape the message prints, written as printed.
    shape = message.split("write each rule as\n", 1)[1]
    _declare(git_project, textwrap.dedent(shape) + "\n")
    assert len(read_guard_tests(git_project)) == 1
    # Remedy two: delete the key.
    _declare(git_project, "")
    assert read_guard_tests(git_project) == ()
    assert kyo("dispatch", record, "--branch", "kyo-744-x").code == 0


def test_parse_reports_the_rule_by_number() -> None:
    value = [{"touches": "a.py", "tests": "t.py", "run": "pytest"}, {"touches": "a.py"}]
    with pytest.raises(KyoseiError, match="rule 2"):
        parse_guard_tests(value, Path("kyosei/config.yml"))


# ------------------------------------------------------------------- c2: the brief


def test_the_brief_states_the_matching_guard_tests_as_commands(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """c2. Under worktree setup, as commands, with what called for each."""
    _declare(git_project, RULES)
    (git_project / "src" / "app").mkdir(parents=True)
    (git_project / "src/app/serve_reads.py").write_text("def handler():\n    pass\n")
    record = _touching(kyo, tmp_path, "src/app/serve_reads.py#handler")
    dispatched = kyo("dispatch", record, "--branch", "kyo-744-a")
    assert dispatched.code == 0, dispatched

    brief = _brief_for(kyo, record)
    command = (
        ".venv/bin/python -m pytest tests/test_classified.py "
        "tests/test_flags.py::test_no_time_flag"
    )
    assert f"        {command}\n" in brief
    assert "called for by your touch of src/app/serve_reads.py" in brief
    assert "# seconds each" in brief
    assert "before you report" in brief
    # Under the worktree setup and above the record: the header's last bullet.
    assert (
        brief.index("**Worktree tooling**")
        < brief.index("**Guard tests**")
        < brief.index("## Record summary")
    )
    # The rule the touch does not match is not stated.
    assert "playwright" not in brief
    # The orchestrator is shown the same command.
    assert command in dispatched.out


def test_the_brief_says_nothing_when_no_rule_matches(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """c2. A dispatch whose touches match no rule gets no guard-tests bullet at all."""
    _declare(git_project, RULES)
    _write(git_project, "README.md")
    record = _touching(kyo, tmp_path, "README.md")
    dispatched = kyo("dispatch", record, "--branch", "kyo-744-b")
    assert dispatched.code == 0, dispatched
    assert "Guard tests" not in _brief_for(kyo, record)
    assert "guard tests" not in dispatched.out


def test_the_brief_says_nothing_when_the_project_declares_none(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    _write(git_project, "src/app/cli.py")
    record = _touching(kyo, tmp_path, "src/app/cli.py")
    assert kyo("dispatch", record, "--branch", "kyo-744-c").code == 0
    assert "Guard tests" not in _brief_for(kyo, record)


def test_a_covered_records_touches_call_for_guard_tests_too(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """One branch carries the covered record's change, so its touches are matched."""
    _declare(git_project, RULES)
    _write(git_project, "README.md")
    primary = _touching(kyo, tmp_path, "README.md")
    covered = _touching(kyo, tmp_path, "apps/desktop/")
    dispatched = kyo("dispatch", primary, "--also-covers", covered, "--branch", "kyo-744-d")
    assert dispatched.code == 0, dispatched
    brief = _brief_for(kyo, primary)
    assert "        npx playwright test apps/desktop/tests/commands.spec.js\n" in brief
    assert "called for by your touch of apps/desktop/" in brief
    assert "pytest" not in brief.split("**Guard tests**", 1)[1].split("## Record summary")[0]


def test_a_new_file_touch_calls_for_the_guard_of_the_path_it_creates(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    _declare(
        git_project,
        "guard_tests:\n  - touches: src/app/cmd_*.py\n    tests: tests/test_classified.py\n"
        "    run: pytest\n",
    )
    new_file = _touching(kyo, tmp_path, "+src/app/cmd_new.py")
    assert kyo("dispatch", new_file, "--branch", "kyo-744-e").code == 0
    assert "        pytest tests/test_classified.py\n" in _brief_for(kyo, new_file)


# ------------------------------------------------------------------ c3: kyo doctor


def test_doctor_names_a_declared_guard_test_file_that_does_not_exist(
    kyo, git_project: Path
) -> None:
    """c3, and the remedy the finding names: correct the path, or remove the entry."""
    _declare(git_project, RULES)
    _write(git_project, "tests/test_classified.py", "apps/desktop/tests/commands.spec.js")
    result = kyo("doctor", "--json")
    check = result.json["checks"]["guard_tests"]
    assert not check["ok"] and result.code != 0
    (finding,) = check["failures"]
    # The selector is taken off before the file is looked for.
    assert "'tests/test_flags.py'" in finding and "rule 1" in finding
    assert "does not exist" in finding and "hand edit" in finding

    _write(git_project, "tests/test_flags.py")
    assert kyo("doctor", "--json").json["checks"]["guard_tests"]["ok"]


def test_doctor_names_a_malformed_declaration_and_is_silent_without_one(
    kyo, git_project: Path
) -> None:
    assert kyo("doctor", "--json").json["checks"]["guard_tests"]["ok"]
    _declare(git_project, "guard_tests: tests/test_a.py\n")
    (finding,) = kyo("doctor", "--json").json["checks"]["guard_tests"]["failures"]
    assert "must be a list of rules" in finding


# ------------------------------------------------------ c4: this repository's own


@pytest.mark.skipif(
    not store_path("config.yml").is_file(),
    reason="this repository's own store (and its config.yml) is not in this checkout",
)
def test_every_guard_test_this_repository_declares_exists() -> None:
    """Whatever this repository's store declares, the files are in this checkout. It
    passes on a store that declares none: the declaration is the store's to carry."""
    store_root = store_path("config.yml").parent.parent
    missing = [
        test
        for rule in read_guard_tests(store_root)
        for test in rule.tests
        if not (REPO_ROOT / test.split("::", 1)[0]).is_file()
    ]
    assert missing == []
