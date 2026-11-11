"""The Quick start and the getting-started page run as a test (KYO-803).

KYO-792 corrected the Quick start of ``docs/reference/topics/operating.md`` by running its
commands by hand in two temporary projects, and found four that were refused as written.
This file makes that run a test: the commands of the Quick start, and those of
``docs/guide/04-getting-started.md``, are typed in document order into a temporary
project, once where ``people:`` in ``kyosei/config.yml`` declares nobody and once where it
declares someone, and any command refused where the document means it to work fails the
test, named.

Which lines are not meant to work as typed is declared in the document, in the comment
that follows the command, and the test reads that and keeps no list of its own. A comment
that opens with a tag in square brackets says so:

``[illustration]``
    an id or a value that exists in no project; not run.
``[alternative]``
    one of several forms the block lists side by side, so an earlier line may have left
    the project in a state that refuses it. Run, and a refusal (exit 2) is allowed; a
    usage error or a crash is not.
``[refused]``, ``[refused where nobody is declared]``, ``[refused where someone is declared]``
    refused on purpose, in every project or in the one kind named. Run, and it must exit
    nonzero there and zero in the other kind.

A command holding a placeholder in angle brackets has no typed form and is not run. A word
in square brackets inside a command is optional and the test types it; a trailing bare
``...`` means the part before it may repeat and the test types it once.

What the commands need to exist beforehand (the files they read, an idea to promote, a
branch to check) is the test's own fixture, below, because a document cannot say it in a
command. No ``claude`` is on the PATH, so nothing reaches Claude Code, and the home
directory is a temporary one, so nothing reaches the network or the real store.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
OPERATING = REPO_ROOT / "docs" / "reference" / "topics" / "operating.md"
GETTING_STARTED = REPO_ROOT / "docs" / "guide" / "04-getting-started.md"

#: A comment's opening tag, and what it says.
TAG = re.compile(r"^\[(illustration|alternative|refused)( where (nobody|someone) is declared)?\]")
#: Where a trailing ``# comment`` begins on a code line.
COMMENT = re.compile(r"\s+#(?:\s|$)")
#: An angle-bracket placeholder: the reader fills it in.
PLACEHOLDER = re.compile(r"<[^<>\s][^<>]*>")
#: An optional word, ``[--print]`` or ``[,orchestrator]``: the test types it.
OPTIONAL = re.compile(r"\[([^\[\]]+)\]")


@dataclass
class Step:
    """One command of a document, with the tag its comment carries."""

    source: str
    line: int
    command: str
    tag: str | None
    tag_scope: str | None
    comment: str


def fenced_sh_blocks(text: str) -> list[tuple[int, list[str]]]:
    """The ``sh`` fenced blocks of a document, each as (first line number, its lines)."""
    blocks: list[tuple[int, list[str]]] = []
    inside: list[str] | None = None
    start = 0
    for number, line in enumerate(text.splitlines(), 1):
        if inside is None:
            if line.strip() == "```sh":
                inside, start = [], number + 1
        elif line.strip() == "```":
            blocks.append((start, inside))
            inside = None
        else:
            inside.append(line)
    return blocks


def steps_of_block(source: str, first_line: int, lines: list[str]) -> list[Step]:
    """The commands of one block: a line starting ``kyo ``, its backslash continuations,
    and the comment on any of its lines with the comment-only lines that follow."""
    items: list[dict] = []
    continuing = False
    for offset, raw in enumerate(lines):
        found = COMMENT.search(raw)
        code, comment = (raw[: found.start()], raw[found.end() :]) if found else (raw, "")
        code = code.rstrip()
        if not code.strip():
            if raw.strip().startswith("#") and items:
                items[-1]["comment"].append(raw.strip().lstrip("#").strip())
            elif not raw.strip():
                continuing = False
            continue
        if continuing and items:
            items[-1]["code"] += " " + code.strip()
            items[-1]["comment"].append(comment.strip())
        elif code.startswith("kyo "):
            items.append({"line": first_line + offset, "code": code, "comment": [comment.strip()]})
        else:
            raise AssertionError(f"{source}:{first_line + offset}: not a command: {raw!r}")
        continuing = items[-1]["code"].endswith("\\")
        if continuing:
            items[-1]["code"] = items[-1]["code"][:-1].rstrip()
    steps = []
    for item in items:
        comment = " ".join(part for part in item["comment"] if part).strip()
        tag = TAG.match(comment)
        steps.append(
            Step(
                source=source,
                line=item["line"],
                command=item["code"],
                tag=tag.group(1) if tag else None,
                tag_scope=tag.group(3) if tag else None,
                comment=comment,
            )
        )
    return steps


def short_version_steps(source: str, text: str) -> list[Step]:
    """The ``kyo`` commands in code spans of the getting-started page's "The short version".

    That page keeps its commands in prose rather than in a fenced block, so the numbered
    steps are where its walkthrough lives. A span that does not start ``kyo `` (the
    install line) is not a Kyosei command and is not read.
    """
    base = text[: text.index("## The short version")].count("\n") + 1
    section = text.split("## The short version", 1)[1].split("\n## ", 1)[0]
    out: list[Step] = []
    for offset, line in enumerate(section.splitlines()):
        for span in re.findall(r"`([^`]+)`", line):
            if span.startswith("kyo "):
                out.append(Step(source, base + offset, span, None, None, ""))
    return out


def operating_steps_of(path: Path, text: str | None = None) -> list[Step]:
    """The Quick start section's fenced blocks of operating.md."""
    text = path.read_text(encoding="utf-8") if text is None else text
    head, section = text.split("\n## Quick start", 1)
    section = section.split("\n## ", 1)[0]
    first = head.count("\n") + 1
    steps: list[Step] = []
    for start, lines in fenced_sh_blocks(section):
        steps.extend(steps_of_block("docs/reference/topics/operating.md", first + start, lines))
    return steps


def getting_started_steps_of(path: Path, text: str | None = None) -> list[Step]:
    text = path.read_text(encoding="utf-8") if text is None else text
    source = "docs/guide/04-getting-started.md"
    steps: list[Step] = []
    for start, lines in fenced_sh_blocks(text):
        steps.extend(steps_of_block(source, start, lines))
    return steps + short_version_steps(source, text)


def typed_form(command: str) -> str | None:
    """What a reader types for the command as the document writes it, or None when the
    command holds a placeholder that has no typed form."""
    if PLACEHOLDER.search(command):
        return None
    command = re.sub(r"\s+\.\.\.\s*$", "", command)
    return OPTIONAL.sub(r"\1", command)


# -- the temporary project -----------------------------------------------------------

FILES = {
    "desc.md": "The widget explodes when the cache is cold.\n",
    "criteria.txt": "A cold start no longer explodes\nA regression test covers it\nThe log names the cache\nThe docs say so\n",  # noqa: E501
    "record.md": "---\ntitle: Cache warms on start\ntouches:\n  - src/server/cache.py\n---\n\nWarm the cache.\n\n## Criteria\n- The cache is warm after start\n",  # noqa: E501
    "words.md": "Use SQLite; we do not need a server.\n",
    "decision.md": "outcome: unprompted\n",
    "q.txt": "I ran it and it works.\n",
    "capture.txt": "1 passed\n",
    "claim.md": "The cache holds 3 rows.\n",
    "claim2.md": "The cache holds 4 rows.\n",
    "console.log": "boom\n",
    "thought.md": "Suspect the warm-up.\n",
    "revised.md": "Suspect the eviction.\n",
    "why.md": "It carried a consumer id.\n",
    "touches.txt": "src/widget.py\n",
    "t2.txt": "src/server/cache.py\nsrc/widget.py\n",
    "fixed.txt": "A regression test covers the cold start\n",
    "new.txt": "The log names the cache and its size\n",
    "task.md": "Fix the cold start.\n",
    "payload.json": json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "."}),
    "src/widget.py": "x = 1\n",
    "src/server/cache.py": "def rebuild():\n    return 1\n",
}
IDEA = "# Widget ideas\n\nA stray thought about widgets.\n\n## The heading\n\nOne section's idea.\n\n## Another\n\nKept.\n"  # noqa: E501
SESSION_ID = "11111111-2222-3333-4444-555555555555"


class Project:
    """A folder with one commit, a home directory of its own, and a PATH with no claude."""

    def __init__(self, root: Path, people: bool) -> None:
        self.root = root
        self.people = people
        self.home = root / "home"
        self.proj = root / "kyo"
        shim = root / "bin"
        for d in (self.home, self.proj, shim):
            d.mkdir(parents=True)
        launcher = shim / "kyo"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m kyosei "$@"\n')
        launcher.chmod(0o755)
        git = shutil.which("git")
        assert git, "git is required"
        os.symlink(git, shim / "git")
        self.env = {
            "HOME": str(self.home),
            "KYOSEI_HOME": str(self.home / "kyosei"),
            "PATH": f"{shim}:/usr/bin:/bin",
            "PYTHONPATH": str(REPO_ROOT / "src"),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "LANG": "en_US.UTF-8",
            "TMPDIR": str(root),
            # the commands are an orchestrator's, typed in a Claude Code shell, which sets this
            "CLAUDE_CODE_SESSION_ID": SESSION_ID,
        }
        assert shutil.which("claude", path=self.env["PATH"]) is None
        self.fixtures_declared = False
        for name, body in FILES.items():
            path = self.proj / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        self.sh("git init -q -b main . && git add -A && git commit -q -m first")

    @property
    def store(self) -> Path:
        return self.home / "kyosei" / "kyo"

    def sh(self, command: str, cwd: Path | None = None) -> tuple[int, str]:
        try:
            done = subprocess.run(
                ["sh", "-c", command],
                cwd=cwd or self.proj,
                env=self.env,
                text=True,
                capture_output=True,
                timeout=120,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return 124, "timed out"
        return done.returncode, (done.stdout + done.stderr)[-1200:]

    def declare_fixtures(self) -> None:
        """Once a store exists: a gate tag and a base for the commands that name them, a
        link from the project's ``ideas/`` to the store's, and the declared person."""
        k = self.store / "kyosei"
        (k / "gate-tags.yml").write_text(
            "gate-tags:\n  ui-review:\n    summary: Waiting on a person to look at the interface.\n"
        )
        (k / "bases.yml").write_text(
            "bases:\n  open-work:\n    summary: Everything open, oldest first.\n    source: records\n"  # noqa: E501
            "    filters:\n      status: open\n    columns: [id, kind, standing, gates_open, title]\n    sort: [seq]\n"  # noqa: E501
        )
        (self.store / "ideas").mkdir(exist_ok=True)
        os.symlink(self.store / "ideas", self.proj / "ideas")
        # a tracked ideas/ in the code repository would be an unacknowledged store
        with (self.proj / ".git" / "info" / "exclude").open("a") as handle:
            handle.write("ideas\n")
        if self.people:
            config = yaml.safe_load((k / "config.yml").read_text())
            config["people"] = {"tester": None}
            (k / "config.yml").write_text(yaml.safe_dump(config, sort_keys=False))
        self.fixtures_declared = True

    def before(self, command: str) -> None:
        """The state a command reads, made just before it, keyed on the command word."""
        words = shlex.split(command)
        name = words[1] if len(words) > 1 else ""
        if name == "promote":
            (self.store / "ideas" / "widget.md").write_text(IDEA)
        elif name == "report":
            sha = self.sh("git rev-parse HEAD")[1].strip()
            (self.proj / ".kyosei-report.json").write_text(
                json.dumps(
                    {
                        "dispatch_id": "KYO-1.d1",
                        "record_id": "KYO-1",
                        "worked_from_sha": sha,
                        "criteria_verdicts": [],
                        "premise_false": [],
                        "disagreements": [],
                        "corrected_numbers": [],
                        "commits": [],
                        "touched_files": ["src/widget.py"],
                    }
                )
            )
        elif name == "confirm":
            self.sh('kyo gate KYO-1 awaiting:human-review --do "look at it"')
        elif name == "check-clean":
            self.sh("git add -A && git commit -q -m 'what kyo init wrote'")
        elif name == "check-branch" and len(words) > 2:
            self.sh(f"git branch {shlex.quote(words[2])}")
        elif name == "dispatch" and "--worktree" in words:
            path = words[words.index("--worktree") + 1]
            self.sh(f"git worktree add -q {shlex.quote(path)} -b wt-kyo-1")
        elif name == "dispatch" and "--no-create-worktree" in words:
            # the line before it created the worktree and a dispatch on this branch
            self.sh(
                'kyo dispatch close KYO-1.d2 --withdrawn --why "the alternative on the next line"'
            )

    def run(self, step: Step) -> tuple[int, str] | None:
        typed = typed_form(step.command)
        if typed is None or step.tag == "illustration":
            return None
        self.before(typed)
        cwd = self.proj
        if typed.startswith(("kyo init --in-repo", "kyo init --store")):
            # a second store belongs to a folder of its own
            cwd = self.root / ("inrepo" if "--in-repo" in typed else "named")
            cwd.mkdir(exist_ok=True)
            self.sh("git init -q -b main . && git commit -q --allow-empty -m first", cwd=cwd)
        result = self.sh(typed, cwd=cwd)
        if not self.fixtures_declared and (self.store / "kyosei" / "config.yml").exists():
            self.declare_fixtures()
        return result


def judge(step: Step, result: tuple[int, str], people: bool) -> str | None:
    """What is wrong with this step's exit status given its tag, or None."""
    code, output = result
    where = "someone is declared" if people else "nobody is declared"
    tail = " | ".join(output.strip().splitlines()[-2:])
    if step.tag == "refused":
        mine = step.tag_scope is None or (step.tag_scope == "someone") == people
        if mine and code == 0:
            return f"exit 0, documented as refused where {where}"
        if not mine and code != 0:
            return f"exit {code} where {where}, documented as refused only where {step.tag_scope} is declared: {tail}"  # noqa: E501
        return None
    if step.tag == "alternative":
        if code in (0, 2):
            return None
        return f"exit {code}, an alternative may be refused (2) but not misused or crash: {tail}"
    if code != 0:
        return f"exit {code} where it is meant to work ({where}): {tail}"
    return None


def run_walkthrough(steps: list[Step], root: Path, people: bool) -> list[str]:
    """Type every step into a fresh temporary project; return one line per failure."""
    project = Project(root, people)
    failures = []
    for step in steps:
        result = project.run(step)
        if result is None:
            continue
        problem = judge(step, result, people)
        if problem:
            failures.append(f"{step.source}:{step.line}: `{step.command}`: {problem}")
    return failures


# -- the tests -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def operating_steps() -> list[Step]:
    return operating_steps_of(OPERATING)


@pytest.mark.parametrize("people", [False, True], ids=["nobody-declared", "someone-declared"])
def test_quick_start_commands_run(tmp_path, operating_steps, people):
    """Every command of operating.md's Quick start works as the document says."""
    assert len(operating_steps) > 100, "the Quick start block was not read"
    failures = run_walkthrough(operating_steps, tmp_path, people)
    assert not failures, "commands refused where they are meant to work:\n" + "\n".join(failures)


@pytest.mark.parametrize("people", [False, True], ids=["nobody-declared", "someone-declared"])
def test_getting_started_commands_run(tmp_path, people):
    """Every command of the getting-started page's walkthrough works as the page says."""
    steps = getting_started_steps_of(GETTING_STARTED)
    assert steps, "no command was read from the getting-started page"
    failures = run_walkthrough(steps, tmp_path, people)
    assert not failures, "commands refused where they are meant to work:\n" + "\n".join(failures)


def test_tags_are_read_from_the_document_not_a_list():
    """A tag in a comment, and nothing else, decides how a line is judged (KYO-803.c2)."""
    block = [
        "kyo show KYO-1                  # plain",
        "kyo move KYO-1 done  \\",
        '      --why "x"      # [alternative] state may refuse it',
        "                     # on a second line",
        "kyo list --plan KYO-20   # [illustration] an id that exists nowhere",
        "kyo discuss skip         # [refused where nobody is declared] a credit form",
        "kyo new decision        # [refused] always",
    ]
    steps = steps_of_block("synthetic", 1, block)
    assert [(s.command, s.tag, s.tag_scope) for s in steps] == [
        ("kyo show KYO-1", None, None),
        ('kyo move KYO-1 done --why "x"', "alternative", None),
        ("kyo list --plan KYO-20", "illustration", None),
        ("kyo discuss skip", "refused", "nobody"),
        ("kyo new decision", "refused", None),
    ]
    plain, alt, _, scoped, always = steps
    assert judge(plain, (2, "refused"), people=False)
    assert not judge(plain, (0, ""), people=False)
    assert not judge(alt, (2, ""), people=False)
    assert judge(alt, (64, "usage"), people=False)
    assert not judge(scoped, (2, ""), people=False)
    assert judge(scoped, (0, ""), people=False)
    assert not judge(scoped, (0, ""), people=True)
    assert judge(scoped, (2, ""), people=True)
    assert not judge(always, (2, ""), people=True)
    assert judge(always, (0, ""), people=True)


def test_optional_words_and_placeholders():
    assert typed_form("kyo docs index [--check]") == "kyo docs index --check"
    assert (
        typed_form('kyo promote a.md --section "The heading" ...')
        == 'kyo promote a.md --section "The heading"'
    )
    assert typed_form('kyo rules "<title>"') is None
    assert typed_form('kyo dispatch KYO-1 --why "..."') == 'kyo dispatch KYO-1 --why "..."'


def test_every_quick_start_tag_is_known(operating_steps):
    """A bracketed word that opens a comment and is not a tag is a typo of one."""
    for step in operating_steps:
        if step.comment.startswith("[") and not TAG.match(step.comment):
            pytest.fail(
                f"{step.source}:{step.line}: `{step.command}` opens its comment with an unknown tag"
            )


def test_a_command_changed_to_a_refused_form_fails_the_walkthrough(tmp_path, operating_steps):
    """KYO-803.c3: one walkthrough command changed to a form the CLI refuses is named."""
    target = next(
        (
            i
            for i, s in enumerate(operating_steps)
            if s.command.startswith("kyo list --kind bug --status working")
        ),
        None,
    )
    assert target is not None, "the command this test changes is gone from the Quick start"
    step = operating_steps[target]
    # a status the registry does not have: refused where the document means it to work
    changed = Step(
        step.source,
        step.line,
        step.command.replace("--status working", "--status nonesuch"),
        None,
        None,
        "",
    )
    failures = run_walkthrough([*operating_steps[:target], changed], tmp_path, people=False)
    assert (
        len(failures) == 1
        and f"{step.source}:{step.line}" in failures[0]
        and "nonesuch" in failures[0]
    ), failures
