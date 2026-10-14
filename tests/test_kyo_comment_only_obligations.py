"""KYO-530: a branch that changes only comments and docstrings raises no docs obligation.

A consumer's APP-58 changed only comments and docstrings and raised 15 obligations, each
settled by hand as not-affected; this repository's KYO-516 raised 12, of which 7 came from
files whose diff moved no code token. The rule (docs/design/docs.md#kyo-530): a changed
file whose code-token sequence is identical on both sides of the diff, read by the
language's own lexer, raises nothing — through ``governs:`` or through a code pointer — and
the preview lists what it quieted. Every other outcome raises exactly as before.

* **c1** a comment-and-docstring-only diff to a governed file leaves no open obligation:
  it is NOT raised (not raised-and-settled), and ``kyo docs preview`` says it was quieted.
* **c2** any code token changed in the same file raises as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kyosei import dispatch_docs, docs_obligations
from kyosei.docs_regions import (
    COMMENT_ONLY_SUFFIXES,
    FileChange,
    code_tokens,
    comment_only_change,
)

from .conftest import new_record
from .test_kyo137_docs_obligations import (
    _POINTER_MODULE,
    _branch_editing,
    _commit,
    _declare_touches,
    _dispatch_and_report,
    _git,
    _pointer_project,
    _pointer_record,
    _preview,
    _write,
)

_APP = '''"""The app's entry point."""

import sys


def main(argv):
    """Run the CLI."""
    # Hand each subcommand a context.
    return len(argv)  # one per argument


class Runner:
    """Holds the state."""

    limit = 3
'''

# Every edit below is to a comment or a docstring, including adding and removing one.
_APP_COMMENTS_ONLY = '''"""The app's entry point, reworded.

Now two paragraphs long."""

import sys


def main(argv):
    # Hand each subcommand a context and an argument namespace.

    return len(argv)


class Runner:
    """Holds the state; see docs/architecture.md#how-the-cli-is-wired."""

    # The limit is three because three is enough.
    limit = 3
'''

_DOC = """# The architecture

## How the CLI is wired

governs: src/app.py

The CLI hands each subcommand a context.

## How the runner limits work

governs: src/app.py#Runner

Three at a time.
"""


def _governed_project(kyo, project: Path) -> tuple[str, str]:
    _write(project, "docs/architecture.md", _DOC)
    _write(project, "src/app.py", _APP)
    _commit(project, "baseline")
    base = _git(project, "rev-parse", "--abbrev-ref", "HEAD")
    record = new_record(kyo, criteria=["the CLI is wired"])
    _declare_touches(kyo, project, record, "src/app.py")
    return record, base


def _change(before: str, after: str, path: str = "src/app.py") -> FileChange:
    return FileChange(path=path, before_text=before, after_text=after)


# --------------------------------------------------------------------------- c1, end to end


def test_a_comment_only_branch_raises_no_obligation_at_report(kyo, git_project: Path) -> None:
    """c1: the governed file changed in comments and docstrings only, so `kyo report`
    raises nothing for either section governing it — not raised, rather than raised and
    settled, so there is no row to settle and the completion gate has nothing to hold."""
    record, _ = _governed_project(kyo, git_project)
    _branch_editing(git_project, "kyo-530", {"src/app.py": _APP_COMMENTS_ONLY})
    _, result = _dispatch_and_report(kyo, git_project, record, branch="kyo-530")

    obligations = result.json["docs_obligations"]
    assert obligations["outcome"] == dispatch_docs.DOCS_OBLIGATIONS_NONE, obligations
    assert obligations["raised"] == []


def test_the_preview_lists_what_the_comment_only_rule_quieted(kyo, git_project: Path) -> None:
    """c1, the audit half: a quieted section is an absence, so the preview counts it under
    its own reason and `--suppressed` names it — never as a #symbol's doing, although one
    of the two sections narrows to `Runner` and the docstring edit is inside it."""
    _, base = _governed_project(kyo, git_project)
    _branch_editing(git_project, "kyo-530", {"src/app.py": _APP_COMMENTS_ONLY})

    preview = _preview(kyo, base, "kyo-530", "--json")
    assert preview.code == 0, preview
    assert preview.json["obligations"] == []
    quieted = {row["section"]: row for row in preview.json["suppressed"]}
    assert set(quieted) == {"How the CLI is wired", "How the runner limits work"}
    assert all(row["comment_only"] for row in quieted.values())
    assert "only comments and docstrings in src/app.py" in quieted["How the CLI is wired"][
        "detail"
    ]

    text = _preview(kyo, base, "kyo-530", "--suppressed")
    assert "quieted as comment-only: 2 section(s)" in text.out, text.out
    assert "suppressed by a declared #symbol: 0 section(s)" in text.out
    assert "[How the runner limits work]" in text.out


def test_re_pointing_a_citation_raises_nothing(kyo, git_project: Path) -> None:
    """c1 for the `pointer` cause, the KYO-516 shape: a branch that only rewrites the
    pointers in a docstring and a comment changes no code, so neither the section it
    stopped citing nor the one it now cites is asked about."""
    base = _pointer_project(kyo, git_project)
    repointed = _POINTER_MODULE.replace(
        "Formerly docs/design/a.md#kyo-3.", "See also docs/design/a.md#kyo-2."
    ).replace("# The limit's reason: docs/design/a.md#kyo-2.", "# Why: docs/design/a.md#kyo-1.")
    assert repointed != _POINTER_MODULE
    _branch_editing(git_project, "kyo-530", {"src/pkg/mod.py": repointed})

    preview = _preview(kyo, base, "kyo-530", "--json")
    assert preview.json["obligations"] == [], preview.json["obligations"]
    quieted = [row for row in preview.json["suppressed"] if row["comment_only"]]
    assert {row["section"] for row in quieted} == {"KYO-1", "KYO-2"}
    assert {row["cause"] for row in quieted} == {docs_obligations.OBLIGATION_POINTER}


def test_a_report_on_a_re_pointed_citation_raises_nothing(kyo, git_project: Path) -> None:
    """c1 for the pointer cause through `kyo report` itself, not only the preview."""
    _pointer_project(kyo, git_project)
    record = _pointer_record(kyo, git_project)
    _branch_editing(
        git_project,
        "kyo-530",
        {"src/pkg/mod.py": _POINTER_MODULE.replace("says why", "says why alpha is 1")},
    )
    _, result = _dispatch_and_report(kyo, git_project, record, branch="kyo-530")
    assert result.json["docs_obligations"]["raised"] == []


# --------------------------------------------------------------------------- c2, end to end


def test_one_code_token_beside_the_comment_edits_raises_as_before(
    kyo, git_project: Path
) -> None:
    """c2: the same comment and docstring edits plus one changed literal raise both
    sections exactly as before. The `Runner` section is raised too although its own lines
    changed only a docstring and a comment: the rule is per file, and per file this diff
    moved a code token, so the symbol's region answers as KYO-153 always has."""
    record, _ = _governed_project(kyo, git_project)
    _branch_editing(
        git_project,
        "kyo-530",
        {"src/app.py": _APP_COMMENTS_ONLY.replace("return len(argv)", "return len(argv) + 1")},
    )
    _, result = _dispatch_and_report(kyo, git_project, record, branch="kyo-530")
    raised = result.json["docs_obligations"]["raised"]
    assert [row["section"] for row in raised] == [
        "How the CLI is wired",
        "How the runner limits work",
    ], raised
    assert {row["cause"] for row in raised} == {docs_obligations.OBLIGATION_GOVERNS}


def test_a_code_change_in_a_pointer_region_still_raises(kyo, git_project: Path) -> None:
    """c2 for the pointer cause: the docstring edit rides along with a code change in the
    same region, and the section is raised as KYO-500 raises it."""
    base = _pointer_project(kyo, git_project)
    edited = _POINTER_MODULE.replace("says why", "says why alpha is 2").replace(
        "    return 1\n", "    return 2\n"
    )
    _branch_editing(git_project, "kyo-530", {"src/pkg/mod.py": edited})
    preview = _preview(kyo, base, "kyo-530", "--json")
    assert [row["section"] for row in preview.json["obligations"]] == ["KYO-1"]
    assert not any(row["comment_only"] for row in preview.json["suppressed"])


def test_a_comment_only_file_does_not_quiet_another_file_that_changed_code(
    kyo, git_project: Path
) -> None:
    """The rule is per file: a section governing two files is raised when either one
    changed code, however many other files changed only comments."""
    _write(
        git_project,
        "docs/two.md",
        "# Two\n\n## Both files\n\ngoverns: src/a.py, src/b.py\n\nProse.\n",
    )
    _write(git_project, "src/a.py", "A = 1  # one\n")
    _write(git_project, "src/b.py", "B = 1\n")
    base = _commit(git_project, "baseline")
    _branch_editing(
        git_project, "kyo-530", {"src/a.py": "A = 1  # uno\n", "src/b.py": "B = 2\n"}
    )
    preview = _preview(kyo, base, "kyo-530", "--json")
    rows = preview.json["obligations"]
    assert [row["section"] for row in rows] == ["Both files"]
    assert rows[0]["changed"] == ["src/b.py"]


# ----------------------------------------------------------- the instrument, per language


_NO_DOCSTRINGS = _APP.replace('"""The app\'s entry point."""\n\n', "").replace(
    '    """Run the CLI."""\n', ""
)


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        pytest.param(_APP, _APP_COMMENTS_ONLY, True, id="comments-and-docstrings"),
        pytest.param(_APP, _NO_DOCSTRINGS, True, id="docstrings-removed"),
        pytest.param(_NO_DOCSTRINGS, _APP, True, id="docstrings-added"),
        pytest.param(
            _APP, _APP.replace('"""Holds the state."""', "'''Holds it.'''"), True,
            id="a-docstring-in-other-quotes",
        ),
        pytest.param(
            _APP, _APP.replace("\n\nclass", "\n\n\n# a section\n\nclass"), True,
            id="blank-lines-and-a-comment",
        ),
        pytest.param(
            _APP, _APP.replace("def main", "async def main").replace('"""Run', '"""Run it'),
            False, id="a-docstring-edit-beside-a-keyword",
        ),
    ],
)
def test_python_comment_only_is_comments_docstrings_and_blank_lines(
    before: str, after: str, expected: bool
) -> None:
    assert comment_only_change(_change(before, after)) is expected


@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param("x = 'a'\n", 'x = "a"\n', id="a-literal-spelled-differently"),
        pytest.param("x = 'a'\n", "x = 'b'\n", id="a-literal"),
        pytest.param("x = 1\n", "y = 1\n", id="a-name"),
        pytest.param("if x:\n    y = 1\n", "if x:\n  y = 1\n", id="re-indented"),
        pytest.param('"""doc"""\nx = 1\n', 'x = 1\n"""not a docstring now"""\n', id="moved-string"),
        pytest.param(
            "def f():\n    x = 1\n    'not first'\n",
            "def f():\n    x = 1\n    'edited'\n",
            id="a-string-statement-that-is-not-a-docstring",
        ),
        pytest.param(
            "#!/usr/bin/env python\nx = 1\n", "#!/usr/bin/env python3\nx = 1\n", id="shebang"
        ),
        pytest.param(
            "# -*- coding: utf-8 -*-\nx = 1\n",
            "# -*- coding: latin-1 -*-\nx = 1\n",
            id="coding-cookie",
        ),
        pytest.param("x = f'{a}'  # c\n", "x = f'{b}'  # c\n", id="an-f-string"),
    ],
)
def test_python_any_code_token_is_a_change(before: str, after: str) -> None:
    """c2 at the instrument: a token's text counts, not only its meaning — quote style
    and re-indentation raise, so the rule never quiets a diff that changed a token."""
    assert comment_only_change(_change(before, after, "m.py")) is False


def test_a_shebang_below_line_one_is_just_a_comment() -> None:
    assert comment_only_change(_change("x = 1\n#!not a shebang\n", "x = 1\n#!edited\n", "m.py"))


def test_a_non_ascii_docstring_is_matched_to_its_tokens() -> None:
    """ast reports byte columns and tokenize character columns; a docstring with é on its
    last line, followed by code on the same line, must still be told apart from the
    code."""
    before = 'def f():\n    """é"""; return 1\n'
    assert code_tokens("m.py", before) is not None
    assert comment_only_change(_change(before, 'def f():\n    """éé"""; return 1\n', "m.py"))
    assert not comment_only_change(_change(before, 'def f():\n    """é"""; return 2\n', "m.py"))


@pytest.mark.parametrize(
    ("before", "after", "path"),
    [
        pytest.param(None, "x = 1\n", "m.py", id="added-file"),
        pytest.param("x = 1\n", None, "m.py", id="deleted-file"),
        pytest.param("x = (\n", "x = (  # c\n", "m.py", id="will-not-parse"),
        pytest.param("// a\nx = 1;\n", "// b\nx = 1;\n", "m.ts", id="no-lexer-for-ts"),
        pytest.param("/* a */\n", "/* b */\n", "m.css", id="no-lexer-for-css"),
        pytest.param("a: [1\n", "a: [1  # c\n", "c.yml", id="yaml-will-not-scan"),
    ],
)
def test_cannot_tell_is_never_comment_only(before, after, path) -> None:
    """Decision 9: every outcome but positive evidence raises."""
    assert comment_only_change(FileChange(path, before, after)) is False


def test_yaml_comments_are_dropped_by_the_scanner_and_values_are_not() -> None:
    before = "# header\nworktree_setup:\n  - make venv  # provisions\nkey: 'a # not a comment'\n"
    assert comment_only_change(
        _change(before, before.replace("# header", "# new header\n# two"), "c.yml")
    )
    assert comment_only_change(
        _change(before, before.replace("# provisions", "# provisions .venv"), "c.yaml")
    )
    assert not comment_only_change(
        _change(before, before.replace("not a comment", "still not one"), "c.yml")
    )
    assert not comment_only_change(_change(before, before.replace("venv", "test"), "c.yml"))


def test_the_lexable_suffixes_are_python_and_yaml() -> None:
    """The scope docs/design/docs.md#kyo-530 argues: the languages whose own lexer this
    project can run with what it already ships."""
    assert COMMENT_ONLY_SUFFIXES == (".py", ".yml", ".yaml")
