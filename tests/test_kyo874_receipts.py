"""KYO-874: a write read off a terminal prints a receipt.

The fixed script below runs the eight commands the record names, each on an input that
warns. Every test here runs it twice over two identical histories, once as a pipe reads
it and once with ``--full``, and compares the two.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from kyosei import cli, clock, receipt, render
from kyosei.prose import fingerprint

from .conftest import Run, declare_person, git_init, invoke, valid_report
from .test_kyo137_docs_obligations import (
    GOVERNED_DOC,
    _branch_editing,
    _commit,
    _commit_anything,
    _git,
    _write,
)
from .test_output_flags import (
    FIXTURES,
    _FixedDatetime,
    _pinned_text,
    _scenario_in,
    assert_pinned,
    pin_name,
)

#: The eight commands of KYO-874.c1, in the order the script runs them.
COMMANDS = ("new", "attach", "verify", "gate", "dispatch", "report", "accept", "confirm")

DESCRIPTION = """\
The stop check reads the dispatch baseline and names every untracked file that was not
there when the dispatch was frozen. On 2026-11-02 it named 41 files a build had written
under /Users/someone/project/build, none of which the worker had made.

The baseline should record the directories a declared build writes into, and the stop
check should leave those out. The declaration belongs beside `worktree_setup`, since the
same person knows both facts.

Out of scope: a build that writes outside the worktree, which the guard cannot see.
"""

CRITERIA = [
    "The baseline records each directory `worktree_setup` declares as written by a build",
    "The stop check names no file under a recorded directory; a test proves it with a "
    "build that writes one",
    "An undeclared directory is still named, and the line says how to declare it",
    "The operating reference states the key and what it leaves out",
]

GATE_PAYLOAD = """\
Open the app, start a dispatch on a project that declares a build, and stop the worker.
The stop message should list no file under /Users/someone/project/build.
Say what the message listed.
"""

EVIDENCE = "".join(
    f"tests/test_stop_check.py::test_case_{n:03d} PASSED [{n:3d}%]\n" for n in range(1, 61)
) + "60 passed in 4.21s\nexit=0\nlog written to /Users/someone/project/logs/stop.log\n"

WORDS = 'The maintainer: "the stop message lists nothing under build now"'


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name / "proj"
    assert invoke(["init", str(root), "--in-repo", "--prefix", "TST"]).code == 0
    git_init(root)
    declare_person(root)
    return root


PATH = "/Users/someone/project/build"


def script(
    root: Path, scratch: Path, flags: tuple[str, ...] = (), *, warns: bool = True
) -> dict[str, Run]:
    """Run the eight commands in the project at ``root``; ``{command: Run}``.

    Setup commands (a blocking record, the touches, the branch a worker would commit
    on) run under ``--json`` and are not part of what is measured. With ``warns``
    every command is given an input that warns, which is what c2 compares. Without it
    the same eight run on inputs that raise nothing, which is the routine case and is
    measured beside the other.
    """

    def kyo(*args: str, stdin: str | None = None) -> Run:
        return invoke(["--root", str(root), *args], stdin=stdin)

    def setup(*args: str, stdin: str | None = None) -> dict:
        done = kyo(*args, "--json", stdin=stdin)
        assert done.code == 0, done
        return json.loads(done.out)

    runs: dict[str, Run] = {}

    def run(command: str, *args: str, stdin: str | None = None) -> Run:
        runs[command] = kyo(command, *args, *flags, stdin=stdin)
        assert runs[command].code == 0, runs[command]
        return runs[command]

    scratch.mkdir(parents=True, exist_ok=True)
    _write(root, "docs/architecture.md", GOVERNED_DOC)
    _write(root, "src/app.py", "x = 1\n")
    _write(root, "release.sh", "echo release\n")
    _commit(root, "baseline")

    criteria = scratch / "criteria.txt"
    criteria.write_text(
        ("\n".join(CRITERIA) if warns else "\n".join(CRITERIA).replace("; a test", ", and a test"))
        + "\n",
        "utf-8",
    )
    if not warns:
        config = root / "kyosei" / "config.yml"
        config.write_text(
            config.read_text("utf-8") + "worktree_setup:\n  - make venv\n", "utf-8"
        )
    touches = scratch / "touches.txt"
    touches.write_text("src/app.py\n", "utf-8")
    # 1. new: the title is long, a criterion holds a semicolon, and the description
    # names an absolute path, so three warnings.
    run(
        "new", "task",
        "--title",
        "The stop check names files a declared build wrote, which the worker did not "
        "make and cannot remove without breaking the build it was told to run"
        if warns
        else "The stop check leaves out what a declared build wrote",
        "--description-file", "-",
        "--criteria-file", str(criteria),
        "--touches-file", str(touches),
        "--force", "--no-prior-art",
        stdin=DESCRIPTION if warns else DESCRIPTION.replace(PATH, "its build directory"),
    )  # fmt: skip
    record = "TST-1"
    blocker = setup("new", "task", "--title", "A record that blocks", "--force")["record"]["id"]
    if warns:
        setup("link", blocker, "blocks", record)

    def gate() -> None:
        # The payload names an absolute path when the script warns.
        run(
            "gate", record, "awaiting:human-review", "--do-file", "-",
            stdin=GATE_PAYLOAD if warns else GATE_PAYLOAD.replace(PATH, "the build directory"),
        )  # fmt: skip

    if not warns:
        gate()  # before the verdict, so the wait it records is findable

    # 2. attach: a test log that names an absolute path.
    log = scratch / "test-log.txt"
    log.write_text(EVIDENCE if warns else EVIDENCE.rsplit("log written", 1)[0], "utf-8")
    run("attach", record, "--file", str(log), "--supports", f"{record}.c1")

    # 3. verify: unobserved before any gate is open, so nothing makes the wait findable,
    # and the reason names an absolute path.
    run(
        "verify", f"{record}.c4", "--verdict", "unmet-unobserved", "--method",
        "reasoned-about", "--why",
        "needs a person to read the page built at /Users/someone/project/site"
        if warns
        else "needs a person to read the page the build writes",
    )  # fmt: skip

    # 4. gate.
    if warns:
        gate()

    # 5. dispatch: blocked by the other record, so --force; no worktree_setup declared.
    _commit_anything(root, "the store, before anything branches")
    _branch_editing(root, "kyo-874", {"src/app.py": "x = 2\n"})
    base = _git(root, "rev-parse", "HEAD")
    run(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-874",
        *(["--force"] if warns else []),
    )  # fmt: skip
    dispatch_id = f"{record}.d1"

    # 6. report: no commits named and a branch that did not advance past the sha given.
    sha = _git(root, "rev-parse", "kyo-874")
    report = scratch / "report.json"
    if not warns:
        report.write_text(json.dumps(_routine_report(dispatch_id, record, base, sha)), "utf-8")
        run("report", dispatch_id, "--file", str(report))
        run("accept", dispatch_id)
        words = scratch / "words.txt"
        words.write_text(WORDS, "utf-8")
        run("confirm", record, "--quote-file", str(words), "--source-other", "said in the review")
        return runs
    _commit_anything(root, "the store, before the merge")
    _git(root, "merge", "--no-ff", "-m", "merge kyo-874", "kyo-874")
    report.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatch_id,
                record_id=record,
                worked_from_sha=sha,
                criteria_verdicts=[
                    {
                        "criterion_id": f"{record}.c1",
                        "verdict": "met",
                        "method": "machine-checked",
                        "evidence": "tests/test_stop_check.py: 60 passed",
                    },
                    {
                        "criterion_id": f"{record}.c2",
                        "verdict": "unmet-unobserved",
                        "method": "reasoned-about",
                        "reason": "the build was not run here",
                    },
                    {
                        "criterion_id": f"{record}.c3",
                        "verdict": "unmet-unobserved",
                        "method": "reasoned-about",
                        "reason": "the line is printed to a person",
                    },
                ],
                premise_false=[
                    {
                        "claim": "worktree_setup lists directories",
                        "correction": "it lists commands",
                        "impact": "the key is new",
                    }
                ],
                touched_files=["src/app.py", "src/other.py"],
            )
        ),
        "utf-8",
    )
    run("report", dispatch_id, "--file", str(report))

    # 7. accept: two verdicts are unobserved and one docs obligation stays open.
    run("accept", dispatch_id, "--reviewed")

    # 8. confirm: the words answer one of three unobserved criteria, so the gate stays.
    words = scratch / "words.txt"
    words.write_text(WORDS, "utf-8")
    run(
        "confirm", record, "--quote-file", str(words), "--criterion", f"{record}.c2",
        "--source-other", "said in the review",
    )  # fmt: skip
    return runs


def _routine_report(dispatch_id: str, record: str, base: str, tip: str) -> dict:
    """A report with nothing to flag: its commit is on the branch, every file it names
    is claimed, three criteria are met and the one docs obligation is answered."""
    return valid_report(
        dispatch_id=dispatch_id,
        record_id=record,
        worked_from_sha=base,
        criteria_verdicts=[
            {
                "criterion_id": f"{record}.c{n}",
                "verdict": "met",
                "method": "machine-checked",
                "evidence": "tests/test_stop_check.py: 60 passed",
            }
            for n in (1, 2, 3)
        ],
        commits=[{"sha": tip, "subject": "work on kyo-874"}],
        touched_files=["src/app.py"],
        docs_verdicts=[
            {
                "doc_path": "docs/architecture.md",
                "section": "How the CLI is wired",
                "verdict": "not-affected",
                "method": "read the section against the diff",
                "reason": "the wiring is unchanged",
            }
        ],
    )


@pytest.fixture
def fixed_clock(monkeypatch) -> None:
    """tests/test_output_flags.py's clock, which its pinned outputs were captured under."""
    monkeypatch.setattr(clock, "datetime", _FixedDatetime)
    for name in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(name, "2026-01-02T03:04:05Z")


@pytest.fixture
def both(tmp_path: Path, monkeypatch) -> tuple[dict[str, Run], dict[str, Run]]:
    """``(receipts, full)``: the script as a pipe reads it, and with ``--full``."""
    monkeypatch.delenv(cli.OUTPUT_ENV, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "0b0c6a1e-1111-4a2b-9c3d-0123456789ab")
    receipts = script(_project(tmp_path, "piped"), tmp_path / "piped")
    full = script(_project(tmp_path, "whole"), tmp_path / "whole", (cli.FULL_FLAG,))
    return receipts, full


#: A line of stored text as the full output echoes it.
ECHOED_TEXT = re.compile(r"^\s*\| ")
#: The measured line of an echo: a label, a length and a digest.
MEASURED = re.compile(r"stored [\w ]+ \(\d+ chars, sha256:[0-9a-f]{12}\)")
#: What KYO-874.c2 says a receipt must not lose, as it appears in a printed line: a
#: warning, a refusal, an outcome that is not a pass, or a command to run.
MUST_KEEP = re.compile(
    r"warning|REFUSED|refused|INCOMPLETE|NOT COMPLETE|NOT CHECKED|DID NOT|CANNOT|"
    r"overrode|not applied \([1-9]|`(kyo|git) [^`]*`|^\s*(kyo|git|printf) "
)

#: For each command, what its first receipt line says it did and the ids it names.
FIRST_LINE = {
    "new": ("created TST-1 (task", ["TST-1"]),
    "attach": ("attached TST-1.e1 to TST-1", ["TST-1.e1", "TST-1.c1"]),
    "verify": ("verified TST-1.c4: unmet-unbuilt -> unmet-unobserved", ["TST-1.c4"]),
    "gate": ("gated TST-1 with TST-1.g1 (awaiting:human-review)", ["TST-1.g1"]),
    "dispatch": ("dispatched TST-1 as TST-1.d1: branch kyo-874, base sha ", ["TST-1.d1"]),
    "report": ("summary: report TST-1.r1 of TST-1.d1", ["TST-1.r1", "TST-1.d1"]),
    "accept": ("summary: NOT COMPLETE — report TST-1.r1 of TST-1.d1", ["TST-1.r1"]),
    "confirm": ("confirmed TST-1: observed by tester (credit TST-1.p1", ["TST-1.p1"]),
}

#: For each command, a piece of every warning the script's input raises.
WARNINGS = {
    "new": [
        "warning: title is 144 characters",
        "warning: TST-1.c2: criterion is 2 semicolon-separated clauses",
        "warning: this prose names 1 absolute path(s)",
    ],
    "attach": ["warning: this prose names 1 absolute path(s)"],
    "verify": [
        "nothing makes this wait findable",
        "kyo gate TST-1 awaiting:human-run --do",
        "warning: this prose names 1 absolute path(s)",
    ],
    "gate": ["warning: this prose names 1 absolute path(s)"],
    "dispatch": [
        "this project declares no `worktree_setup`",
        "--force overrode the blocked refusal (1 blocker(s)):",
        "TST-2 (status open, not settled) through link TST-L1",
        "create it with: git worktree add",
    ],
    "report": [
        "branch check: BRANCH DID NOT ADVANCE",
        "premise_false (1):",
        "worktree_setup lists directories",
        "TST-1.c2: unmet-unobserved",
        "`kyo verify --from-report TST-1.d1`",
        "kyo docs settle TST-1.o1 --verdict",
        "touch drift: 1 file(s)",
        "+ src/other.py",
    ],
    "accept": [
        "INCOMPLETE: 1 step(s) refused",
        "review: --reviewed, over 1 premise_false",
        "kyo edit TST-1 --touches-from-drift TST-1.d1  — REFUSED",
        "touch validation failed:",
    ],
    "confirm": [
        "INCOMPLETE: 1 covered record(s) left open",
        "still unobserved: TST-1.c3, TST-1.c4",
        "left open: TST-1.g1 (awaiting:human-review)",
        "TST-1 stays open:",
        "`kyo split TST-1 --to-successor TST-1.c3 TST-1.c4`",
    ],
}


def _lines(run: Run) -> list[str]:
    """The printed lines, one per newline, as a reader of the pipe sees them."""
    return run.out.splitlines()


def _same_but_for_the_project(receipt_line: str, full_lines: list[str]) -> bool:
    """Whether ``receipt_line`` is in the full output. The two histories live in two
    directories and their commits differ, so a path or a sha is compared by shape."""

    def shape(line: str) -> str:
        line = re.sub(r"/[^ ']*/(piped|whole)\b", "<ROOT>", line)
        line = re.sub(r"\b[0-9a-f]{40}\b", "<SHA>", line)
        return re.sub(r"sha256:[0-9a-f]{12,64}", "sha256:<D>", line)

    return shape(receipt_line) in {shape(line) for line in full_lines}


# ------------------------------------------------------------------------ KYO-874.c1


@pytest.mark.parametrize("command", COMMANDS)
def test_a_write_read_off_a_terminal_prints_a_receipt(both, command: str) -> None:
    """c1. One line saying what was done, with the ids; the measure of each stored text
    and none of the text; and nothing of the full output but what warns."""
    receipts, full = both
    receipt, whole = _lines(receipts[command]), _lines(full[command])
    says, ids = FIRST_LINE[command]
    assert receipt[0].startswith(says), receipt[0]
    for made in ids:
        assert made in receipt[0], (made, receipt[0])
    assert len(receipt) < len(whole)
    assert len(receipts[command].out.encode()) < len(full[command].out.encode())
    # Law 5: every text the full output measures is measured in the receipt, and no
    # line of any text is printed.
    assert not [line for line in receipt if ECHOED_TEXT.match(line)]
    measured = [m.group(0) for line in whole if (m := MEASURED.match(line))]
    for measure in measured:
        assert _same_but_for_the_project(measure, [m.group(0) for line in receipt
                                                   if (m := MEASURED.match(line))]), measure
    # No line is truncated: every receipt line but the composed first lines is a whole
    # line of the full output (a measured line loses only its trailing colon).
    composed = 0
    for line in receipt:
        if _same_but_for_the_project(line, whole) or _same_but_for_the_project(
            line + ":", whole
        ):
            continue
        composed += 1
        assert any(
            _same_but_for_the_project(line[: len(prefix)], [prefix]) for prefix in whole
        ), f"{line!r} is neither a line of the full output nor one extended"
    assert composed <= 2, receipt


def test_new_measures_the_criteria_it_no_longer_prints(both) -> None:
    """c1 / law 5: the criteria texts are left out, and one length and digest over them
    (joined by newlines, in the order stored) stands for them."""
    receipts, _full = both
    text = "\n".join(CRITERIA)
    assert (
        f"stored criteria: 4, TST-1.c1 to TST-1.c4 ({len(text)} chars, "
        f"sha256:{fingerprint(text)})"
        in _lines(receipts["new"])
    )
    assert "stored description (" + str(len(DESCRIPTION.rstrip("\n"))) + " chars" in receipts[
        "new"
    ].out


# ------------------------------------------------------------------------ KYO-874.c2


@pytest.mark.parametrize("command", COMMANDS)
def test_a_receipt_drops_no_warning_refusal_or_remedy(both, command: str) -> None:
    """c2. Each warning the input raises is in both outputs, and no line the receipt
    leaves out is a warning, a refusal, a failed outcome or a command to run."""
    receipts, full = both
    receipt, whole = _lines(receipts[command]), _lines(full[command])
    for warning in WARNINGS[command]:
        assert any(warning in line for line in whole), f"the input did not warn: {warning}"
        assert any(warning in line for line in receipt), f"the receipt lost: {warning}"
    footer = f"explained in `kyo reference {command}`"
    kept = receipt
    dropped = [
        line
        for line in whole
        if line != footer
        and not _same_but_for_the_project(line, kept)
        and not any(_same_but_for_the_project(line, [k[: len(line)]]) for k in kept)
        and not (line.endswith(":") and _same_but_for_the_project(line[:-1], kept))
    ]
    assert dropped, "the receipt left nothing out"
    lost = [line for line in dropped if MUST_KEEP.search(line)]
    # The one line a receipt replaces whole: dispatch's brief line is kept verbatim, so
    # nothing here needs an exception.
    assert not lost, lost


def test_an_unmarked_line_is_in_the_receipt() -> None:
    """c2's mechanism: a line is left out only when the command marked it as detail. A
    warning added to a command later is a plain string, and a plain string is kept."""
    lines = [
        receipt.receipt_form("did it", "did it: X-1"),
        *render.echo_block("description", "one\ntwo"),
        receipt.detail("stored kind: task"),
        "  warning: added next year",
    ]
    assert receipt.receipt_lines(lines) == [
        "did it: X-1",
        f"stored description (7 chars, sha256:{fingerprint('one' + chr(10) + 'two')})",
        "  warning: added next year",
    ]
    # A composing command indents a step's lines; the marks survive it.
    indented = receipt.receipt_lines(receipt.indented("  ", lines))
    assert indented[-1] == "    warning: added next year"
    assert len(indented) == 3
    # The full form is the string itself, so every other reader is unchanged.
    assert [str(line) for line in lines][:2] == ["did it", lines[1]]
    assert json.dumps(lines[:1]) == '["did it"]'


# ------------------------------------------------------------------------ KYO-874.c3


def _pinned(tmp_path: Path, mode: str, flags: list[str], labels=None) -> None:
    runs, where = _scenario_in(tmp_path, mode, flags)
    for label, result in runs:
        if labels is None or label in labels:
            assert_pinned(pin_name("output_flags", mode, label), _pinned_text(result, where))


#: The scenario's commands that print a receipt.
SCENARIO_RECEIPTS = {"attach", "move", "verify", "dispatch", "report", "accept"}


def test_the_flag_restores_the_full_output(tmp_path: Path, fixed_clock, monkeypatch) -> None:
    """c3. Off a terminal, `--full` prints what every command printed before receipts
    existed: the pinned outputs of tests/test_output_flags.py, byte for byte."""
    monkeypatch.delenv(cli.OUTPUT_ENV, raising=False)
    _pinned(tmp_path, "text", [cli.FULL_FLAG])


def test_a_terminal_prints_as_it_did(tmp_path: Path, fixed_clock, monkeypatch) -> None:
    """c3. With stdout a terminal and no flag, the same pinned outputs."""
    monkeypatch.delenv(cli.OUTPUT_ENV, raising=False)
    monkeypatch.setattr(cli, "_stdout_is_tty", lambda: True)
    _pinned(tmp_path, "text", [])


def test_json_is_unchanged(tmp_path: Path, fixed_clock, monkeypatch) -> None:
    """c3. `--json` off a terminal prints the pinned envelopes, `steps[].lines` whole."""
    monkeypatch.delenv(cli.OUTPUT_ENV, raising=False)
    _pinned(tmp_path, "json", ["--json", "--full", "--pretty"])


def test_reads_and_unconverted_writes_print_the_same_to_a_pipe(
    tmp_path: Path, fixed_clock, monkeypatch
) -> None:
    """Off a terminal with no flag, every command of the scenario that is not one of the
    receipts (`show`, `list`, `check-branch`, `check-clean`, `base run`, and the writes
    this record did not convert) prints its pinned output, and each receipt differs."""
    monkeypatch.delenv(cli.OUTPUT_ENV, raising=False)
    runs, where = _scenario_in(tmp_path, "text", [])
    for label, result in runs:
        name = pin_name("output_flags", "text", label)
        if label in SCENARIO_RECEIPTS:
            pinned = (FIXTURES / f"{name}.txt").read_text("utf-8")
            assert _pinned_text(result, where) != pinned, label
            assert len(result.out) < len(pinned), label
        else:
            assert_pinned(name, _pinned_text(result, where))


def test_the_environment_variable_restores_the_full_output(
    tmp_path: Path, fixed_clock, monkeypatch
) -> None:
    """``KYOSEI_OUTPUT=full`` is `--full` for every invocation, which is how this suite
    reads the whole output of a write."""
    monkeypatch.setenv(cli.OUTPUT_ENV, cli.OUTPUT_FULL)
    _pinned(tmp_path, "text", [], labels=SCENARIO_RECEIPTS)


def test_what_a_hook_reads_is_never_a_receipt(kyo, git_project: Path, monkeypatch) -> None:
    """The hooks call `kyo` with stdout piped and act on what it prints. None of those
    commands marks its output as a receipt, so the reduction is never applied to them."""
    monkeypatch.delenv(cli.OUTPUT_ENV, raising=False)
    assert kyo("new", "task", "--title", "Something to list", "--force").code == 0

    def never(lines):
        raise AssertionError(f"a receipt was made of {lines!r}")

    monkeypatch.setattr(cli.receipt_module, "receipt_lines", never)
    for argv in (
        ["brief"],
        ["brief", "--compact"],
        ["doctor", "--hook"],
        ["sweep", "--hook"],
        ["check-clean"],
        ["check-clean", "--subagent-stop"],
        ["check-branch", "main", "--against", "main"],
        ["check-build"],
        ["show", "TST-1"],
        ["list"],
    ):
        ran = kyo(*argv)
        assert "a receipt was made" not in ran.out + ran.err, (argv, ran)
    # The patch is live: a write does reach it.
    with pytest.raises(AssertionError, match="a receipt was made"):
        kyo("new", "task", "--title", "A second thing", "--force")


# ------------------------------------------------------------------------ KYO-874.c4


def _sizes(runs: dict[str, Run]) -> tuple[int, int]:
    return (
        sum(len(runs[c].out.encode()) for c in COMMANDS),
        sum(runs[c].out.count("\n") for c in COMMANDS),
    )


@pytest.mark.parametrize("warns", [True, False], ids=["warning", "routine"])
def test_the_script_prints_fewer_bytes_as_receipts(
    tmp_path: Path, monkeypatch, capsys, warns: bool
) -> None:
    """c4. The bytes and lines the eight commands print, before (`--full`, which is the
    output before this change byte for byte) and after. ``pytest -s`` prints the table;
    ``KYO874_DUMP_DIR`` writes each output to a file."""
    monkeypatch.delenv(cli.OUTPUT_ENV, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "0b0c6a1e-1111-4a2b-9c3d-0123456789ab")
    receipts = script(_project(tmp_path, "piped"), tmp_path / "piped", warns=warns)
    full = script(_project(tmp_path, "whole"), tmp_path / "whole", (cli.FULL_FLAG,), warns=warns)
    dump = os.environ.get("KYO874_DUMP_DIR")
    kind = "warning" if warns else "routine"
    with capsys.disabled():
        print(f"\n{kind} inputs      before (--full)        after (receipt)")
        for command in COMMANDS:
            r, f = receipts[command].out, full[command].out
            print(
                f"  {command:9} {len(f.encode()):6} bytes {f.count(chr(10)):3} lines   "
                f"{len(r.encode()):6} bytes {r.count(chr(10)):3} lines"
            )
            if dump:
                (Path(dump) / f"{kind}.{command}.full.txt").write_text(f, "utf-8")
                (Path(dump) / f"{kind}.{command}.receipt.txt").write_text(r, "utf-8")
        (fb, fl), (rb, rl) = _sizes(full), _sizes(receipts)
        print(f"  {'total':9} {fb:6} bytes {fl:3} lines   {rb:6} bytes {rl:3} lines")
    for command in COMMANDS:
        assert len(receipts[command].out.encode()) < len(full[command].out.encode()), command
    if not warns:
        # With nothing to warn about a receipt is a few lines. A report always leaves
        # something to do (its verdicts and docs verdicts are proposals), so it is longer.
        for command in COMMANDS:
            most = {"report": 9, "dispatch": 6, "confirm": 14}.get(command, 5)
            assert receipts[command].out.count("\n") <= most, receipts[command].out
