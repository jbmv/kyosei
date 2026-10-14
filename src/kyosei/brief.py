"""Generate the frozen dispatch brief, per ``templates/brief-template.md``'s
machine-filled slots (KYO-7.c1).

Every ``[MACHINE-FILLED]`` slot in that template names a real data source in its
own comment; this module is that source, one function per slot family, composed
by :func:`generate_brief`. Nothing here is hand-assembled prose — the only free
text a controller supplies is ``controller_task_prose``, exactly the one section
the template marks ``[CONTROLLER-WRITTEN]``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from . import sweep
from .contracts import (
    CRITERION_STANDING_SUPERSEDED,
    TOUCH_ADDITIVE_BRIEF_NOTE,
    TOUCH_ADDITIVE_SYMBOL,
    TOUCH_DIRECTORY_BRIEF_NOTE,
    TOUCH_DIRECTORY_SYMBOL,
    TOUCH_NEW_FILE_BRIEF_NOTE,
    TOUCH_NEW_FILE_SYMBOL,
    VOID_CRITERION_STATES,
    WORKER_REPORT_PATH,
    is_directory_touch,
    is_new_file_touch,
    replaced_by,
)
from .kinds import Kind
from .procedures import PACKAGED_LEDGER_SOURCE_PATH, STORE_LEDGER_PATH, Rule
from .report_schema import (
    COMMIT_ALLOWED,
    CORRECTED_NUMBER_ALLOWED,
    CRITERION_VERDICT_ALLOWED,
    CRITERION_VERDICT_REASON_REQUIRED,
    DISAGREEMENT_ALLOWED,
    DOCS_VERDICT_ALLOWED,
    PREMISE_FALSE_ALLOWED,
    WORKER_REPORT_METHODS,
)

#: KYO-241.c1: one row per report array, in the order the schema declares them
#: (report.schema.json's own `required` list, minus the scalar fields), each
#: naming the frozenset :mod:`kyosei.report_schema` mirrors from the shipped
#: document. Read here rather than hand-typed so :func:`_report_key_sets_block`
#: can never state a key the schema does not, or omit one it does --
#: ``tests/test_kyo149_report_schema_agrees.py`` already pins every one of these
#: sets against the document itself, so pinning the brief's prose against these
#: same sets closes the whole chain: brief text <-> these constants <-> the
#: shipped schema.
REPORT_ARRAY_KEY_SETS: tuple[tuple[str, frozenset[str]], ...] = (
    ("criteria_verdicts[]", CRITERION_VERDICT_ALLOWED),
    ("premise_false[]", PREMISE_FALSE_ALLOWED),
    ("disagreements[]", DISAGREEMENT_ALLOWED),
    ("corrected_numbers[]", CORRECTED_NUMBER_ALLOWED),
    ("docs_verdicts[]", DOCS_VERDICT_ALLOWED),
    ("commits[]", COMMIT_ALLOWED),
)


def _report_key_sets_block() -> list[str]:
    """KYO-241.c1: the exact key set each report array's entries accept.

    Generated from :data:`REPORT_ARRAY_KEY_SETS` -- itself read from
    :mod:`kyosei.report_schema`'s mirror of the shipped ``report.schema.json`` --
    so this text cannot drift from what `kyo report` actually enforces the way two
    workers' plausible-but-refused keys (`severity` on a disagreement, `message` on
    a commit -- KYO-233.d1, KYO-234.d1) drifted from a brief that named no key set
    at all and sent the worker to go read the whole schema instead.
    """
    lines = [
        "Each array below accepts exactly these keys on every entry (generated "
        "from `report.schema.json`; any other key is refused with "
        "`additionalProperties: false`):",
        "",
    ]
    lines += [
        f"- `{name}`: {', '.join(f'`{key}`' for key in sorted(allowed))}"
        for name, allowed in REPORT_ARRAY_KEY_SETS
    ]
    lines.append("")
    return lines


def report_example(dispatch_id: str, record_id: str, worked_from_sha: str) -> dict:
    """KYO-477.c5: the minimal filled report every brief shows, as a Python object.

    The brief renders exactly this object, and ``tests/test_report_check.py`` passes
    the same object through :func:`kyosei.report_schema.validate_report` — so the
    example cannot drift from the schema `kyo report` enforces. It carries an
    ``unmet-unobserved`` verdict with its ``reason`` because that conditional rule,
    and the three ``method`` values, are what a consumer's workers got wrong when
    the brief listed keys only (a consumer report). Kept minimal: every brief
    pays its length.
    """
    return {
        "dispatch_id": dispatch_id,
        "record_id": record_id,
        "worked_from_sha": worked_from_sha,
        "criteria_verdicts": [
            {
                "criterion_id": f"{record_id}.c1",
                "verdict": "met",
                "method": "machine-checked",
                "evidence": "tests/test_x.py::test_y passes",
            },
            {
                "criterion_id": f"{record_id}.c2",
                "verdict": "unmet-unobserved",
                "method": "reasoned-about",
                "reason": "needs a visible window, which a worker cannot observe",
            },
        ],
        "premise_false": [],
        "disagreements": [],
        "corrected_numbers": [],
        "commits": [{"sha": "0123abc", "subject": "the commit's subject line"}],
    }


def _report_example_block(dispatch_id: str, record_id: str, worked_from_sha: str) -> list[str]:
    """KYO-477.c4/c5: the dry-run instruction and the filled example, as brief lines."""
    return [
        f"Before you hand back, run `kyo report {dispatch_id} --file {WORKER_REPORT_PATH} "
        "--check` in your worktree: it runs every check the ingest runs before it writes "
        "(the schema, the ids, criteria scoping, your commits on the declared branch), "
        "prints any problem, and writes nothing. Fix what it names and run it again until "
        "it exits 0.",
        "",
        "A minimal filled report (`method` is one of "
        f"{', '.join(f'`{m}`' for m in WORKER_REPORT_METHODS)}, never `human-observed`; "
        "a verdict of "
        f"{', '.join(f'`{v}`' for v in sorted(CRITERION_VERDICT_REASON_REQUIRED))} "
        "must carry a `reason`; put in the sha you actually branched from and your real "
        "commits):",
        "",
        "```json",
        *json.dumps(
            report_example(dispatch_id, record_id, worked_from_sha), indent=2
        ).splitlines(),
        "```",
        "",
    ]


WRITE_SCOPE_STATEMENTS: dict[str, str] = {
    "none": (
        "You may read anything. You may not write code, run mutating queries, or "
        "touch production data. Produce findings/prose only."
    ),
    "queries-only": (
        "You may run read-only queries against the systems named in Touches. You "
        "may not write code or mutate any data, production or otherwise."
    ),
    "code": (
        "You may edit code and commit on your dispatch branch, within the "
        "files/symbols named in Touches. You may not touch production data or deploy."
    ),
    "prod-data": (
        "You may mutate the production data named in Touches, and you MUST run "
        "dry-run-by-default: produce and report a dry-run plan first; do not pass "
        "--apply (or equivalent) without it being explicitly requested in this "
        "brief's orchestrator-written section below."
    ),
}

STANDING_STANCE = (
    "**Flag conflicts, do not resolve them. Contradicting this brief must be cheaper "
    "than complying with it.** If a premise above is false, say so in `premise_false[]` "
    "in your report. If you disagree with the approach, say so in `disagreements[]`, "
    "with your rationale — and, if your judgment says the approach is wrong, you may "
    "act on your own judgment and report the disagreement, rather than either silently "
    "complying or silently doing your own thing. Report what you tried and rejected, "
    "not just what you shipped."
)


def _dead_criterion_line(c: dict) -> str:
    """One void criterion, as the summary line names it (KYO-58.c6).

    A superseded criterion names its replacement, so a reader never has to go
    looking for why it was voided — that answer is one of the two the ``reason``
    column holds, and it is the one that is actionable.
    """
    replacement = replaced_by(c["state"], c["reason"])
    if replacement:
        return f"{c['id']} ({CRITERION_STANDING_SUPERSEDED} by {replacement})"
    return f"{c['id']} ({c['state']})"


def _criteria_table(criteria: list[dict]) -> str:
    """KYO-58.c3: the LIVE criteria, and a count of the ones left out.

    Brief length is a measured constraint — an earlier project loaded 2,280 lines per
    worker, 71% of it unactionable — and this is the one document a worker reads in
    full. A dead criterion in it costs reading budget and invites exactly the wrong
    question ("should I do this one?"): measured on this board, KYO-30 would have
    shown a worker five criteria, two of them dead.

    Not silent omission (law 6). The count is always stated, including when nothing
    was left out, so "no void criteria" and "void criteria you were not shown" can
    never render as the same thing — and each omitted one is named, with its
    replacement if it has one, so a worker who needs the history knows it exists and
    where to ask.
    """
    live = [c for c in criteria if c["state"] not in VOID_CRITERION_STATES]
    dead = [c for c in criteria if c["state"] in VOID_CRITERION_STATES]
    if live:
        lines = ["| id | text | state | reason (if void/unobserved) |", "|---|---|---|---|"]
        lines += [
            f"| {c['id']} | {c['text']} | {c['state']} | {c['reason'] or ''} |" for c in live
        ]
    elif criteria:
        lines = ["| (no live criteria) |  |  |  |"]
    else:
        lines = ["| (none) |  |  |  |"]
    if dead:
        lines.append("")
        lines.append(
            f"{len(live)} live criteria · {len(dead)} void, not shown: "
            + ", ".join(_dead_criterion_line(c) for c in dead)
        )
    elif criteria:
        lines.append("")
        lines.append(f"{len(live)} live criteria · 0 void (none withheld from this table)")
    return "\n".join(lines) + "\n"


#: How the touches table labels the record a path was declared by. The primary
#: record and an ``--also-covers`` record contribute to the claim identically —
#: the labels exist so a worker can go back to the right record for the *reason*
#: a path is in scope, not to rank them.
TOUCH_SOURCE_PRIMARY = "this dispatch's own record"
TOUCH_SOURCE_COVERED = "--also-covers"

#: Shared resources (charter, kinds.yml comment) live in the same ``touches``
#: table as files/symbols today; nothing in the schema yet distinguishes a
#: "shared resource" token from a file path, so this slot names that gap
#: explicitly rather than pretending to derive a distinction that isn't there.
SHARED_RESOURCES_LINE = "(not yet distinguished from files in the touches table)"


#: KYO-145.c1. Why the count can only ever be a floor, stated once and carried by both
#: branches of :func:`_concurrency_bullet` — the zero case needs it as badly as the
#: others, and a sentence only one branch carries is a sentence half the workers never
#: receive.
_CONCURRENCY_FLOOR_NOTE = (
    "A floor, never a total. This brief cannot see a dispatch created after it, and a "
    "orchestrator dispatching a wave creates the dispatches one after another and spawns "
    "their workers afterwards — so the count is sampled in the first phase and read in "
    "the third, and the real number when you read this can only be higher. Measured on "
    "this store's own wave 3, generated in one batch: the five briefs said none, 1, 2, "
    "3 and 4, every one accurate at its own freeze instant and all but the last stale "
    "before any worker started."
)

#: The restraint, and deliberately NOT derived from the count (KYO-145.c2). It used to
#: be: a zero count concluded "nothing is contending with you ... a full-suite run here
#: would cost only time", in the present tense, about a condition that had already
#: changed. Every reason below is true at every count, so the advice no longer depends on
#: a number the brief cannot keep current.
_CONCURRENCY_RESTRAINT = (
    "Run the tests that cover your change and leave the whole suite to your orchestrator: "
    "a suite that holds an exclusive resource — a port, a display, a database, a device "
    "— manufactures failures when two runs fight over it, and even where it holds none, "
    "your green would be on this branch before every other worker's merge, which is not "
    "the question anyone needs answered."
)


def _concurrency_bullet(others: Sequence[Mapping]) -> str:
    """KYO-132.c5, as KYO-145 corrected it: the live-dispatch count, stated as a FLOOR.

    The fifth of the header's "conditions of this work" bullets. The worker asked to
    restrain itself is the one party who cannot see the other dispatches; `kyo dispatch`
    can, because it computes exactly this set for the touch-disjointness check a few
    lines earlier, so stating it costs nothing that was not already known.

    WHAT KYO-145 FIXED, and it is a lesson about hedged facts rather than about counts.
    The number was hedged correctly — "at the moment this brief was frozen" — and the
    ADVICE DERIVED FROM IT WAS NOT: a zero count went on to say "You are the only running
    dispatch, so nothing is contending with you ... and a full-suite run here would cost
    only time", in the present tense, about a condition that had changed before the brief
    was read. KYO-131's dispatch reported exactly that, and it was produced by KYO-132's
    own mechanism: the controller creates N dispatches, then N worktrees, then spawns N
    workers, so the count is sampled in phase one and read in phase three.

    So the count is a floor. "At least N, and this brief cannot see any created after it"
    is fully true at every instant, needs nothing `kyo dispatch` does not already know,
    and — this is the part that makes the zero case behave — invites no conclusion.
    "None had been created when this froze" is an absence of evidence; "you are the only
    running dispatch" was an assertion, and it was false about a third of the time.

    Kyosei is not the fix for that in any other direction: it does not spawn workers and
    must not start (KYO-61), so there is no spawn moment it could re-count at, and
    serialising a wave to make one sentence true would give up the batching the
    disjointness check exists to make safe.
    """
    if not others:
        return (
            "- **Other dispatches running**: none had been created when this brief was "
            "frozen. Read that as an absence of evidence, not as a statement that you "
            "are alone — no conclusion about contention follows from it. "
            f"{_CONCURRENCY_FLOOR_NOTE} "
            f"{_CONCURRENCY_RESTRAINT}"
        )
    named = ", ".join(f"{row['dispatch_id']} (dispatching {row['record_id']})" for row in others)
    return (
        f"- **Other dispatches running**: at least {len(others)} had been created when "
        f"this brief was frozen — {named}. This is the same set your touches were "
        "compared against for disjointness, so it is a fact about this dispatch rather "
        f"than a guess. {_CONCURRENCY_FLOOR_NOTE} {_CONCURRENCY_RESTRAINT}"
    )


#: KYO-143. Stated on every brief that names a worktree, whether or not the project
#: declared any setup, because it is a property of the *mechanism* rather than of the
#: project: a git worktree isolates FILES and inherits the spawning process's
#: ENVIRONMENT unchanged, so everything env-directed crosses the boundary worktrees
#: exist to hold. No provisioning command can fix it — an installer that honours
#: ``VIRTUAL_ENV`` will honour the controller's one no matter how correct the command
#: around it is.
#:
#: Observed rather than theorised (KYO-143.d1, measured in the worktree this text
#: now warns about): the worker's inherited ``PATH`` led with the *controller's*
#: ``.venv/bin``, so bare ``python``, ``pytest`` and ``kyo`` all resolved to the
#: controller's interpreter importing the controller's source, from inside the
#: worktree, before anything was installed. The failure this prevents ran backwards
#: once already — an ``uv pip install -e .`` that followed the inherited
#: ``VIRTUAL_ENV`` pointed the CONTROLLER's venv at an unmerged worktree's source,
#: and both parties saw success.
_ENV_INHERITANCE_NOTE = (
    "  **Your environment is inherited, not isolated.** A worktree isolates *files*; "
    "the process that spawned you hands down its *environment* unchanged. `VIRTUAL_ENV`, "
    "`PATH` and their per-ecosystem equivalents can still point into the checkout you "
    "were sent away from, so a bare `python`, `pytest` or `npm` may run another tree's "
    "tooling against another tree's source while reporting cleanly. Two consequences, and "
    "the second is the dangerous one: a result you believe is about your worktree can be "
    "about someone else's, and an installer that honours an inherited `VIRTUAL_ENV` will "
    "install *your* unmerged source into *their* environment — a corruption that points "
    "away from you, that your own tests cannot see, and that reports success on both "
    "sides. Check what you are actually running (`command -v python`, or import the "
    "package and print its `__file__`) before you trust a green, prefer in-tree absolute "
    "paths over whatever `PATH` resolves, and set the environment explicitly for any "
    "installer that reads it."
)


#: How deep a note sits under the command it is about, inside the brief's indented code
#: block. Two spaces past the command, so the pairing is visible at a glance and the
#: note cannot be mistaken for the next command in the list.
_SETUP_NOTE_INDENT = "          # "


def _setup_lines(setup_commands: Sequence[str]) -> list[str]:
    """The ``worktree_setup`` block: each command, and its note beneath it (KYO-164).

    The commands come out at exactly the indentation they always had, and a project that
    declared no notes gets a list byte-identical to the one it got before this existed
    (KYO-164.c2) — which is not a promise this function keeps by being careful, but one
    :class:`kyosei.config.SetupCommand` keeps for it: an entry with no note *is* the
    string it used to be, and ``getattr`` returns ``""`` for anything that never heard
    of notes at all.

    Rendered as a shell comment, inside the code block, beneath its command. That
    placement is the whole of c1 — "beside the command it is about". A block of
    commands followed by a separate list of notes would put the caveat back at the
    distance the YAML comment already had, and a `#` line keeps the block pasteable:
    the reason this note exists is that a worker pasted `npm ci`, watched it exit 0 in
    two seconds, and believed it.
    """
    lines: list[str] = []
    for command in setup_commands:
        lines.append(f"        {command}")
        note = getattr(command, "note", "")
        if note:
            lines.append(f"{_SETUP_NOTE_INDENT}{note}")
    return lines


def _worktree_tooling_bullet(worktree: str, setup_commands: Sequence[str]) -> str:
    """KYO-143. What the worker will actually find in the worktree it was sent to.

    The sixth of the header's "conditions of this work" bullets, and the only one
    that describes the *tree* rather than a declaration about it. It exists because
    a brief that says nothing about tooling is not neutral: the worker reads the
    project's own docs, which name `.venv/bin/python` and `make test` because both
    are true in the checkout those docs were written in — and `git worktree add`
    checks out tracked files and nothing else, so both can be false in the tree the
    worker is standing in. KYO-127's dispatch paid the first fifth of its budget
    finding that out.

    Stated either way, like the branch, worker-model and concurrency bullets above
    it. The empty case is the *designed* case rather than a fallback: a project that
    declares nothing gets a brief that says what a fresh worktree is missing, which
    is the honest floor — this bullet never asserts that a path exists, only that
    the project did or did not declare how to create one (KYO-143.c3, KYO-143.c4).

    Kyosei renders these commands and runs none of them. What a worktree needs is a
    fact only the consuming project holds, so the mechanism declares rather than
    assumes (KYO-143.c2); a store command that ran a project's package manager on
    its own initiative would be guessing precisely what `worktree_setup` exists to
    have the project state.
    """
    if not worktree:
        # No separate worktree was declared, so there is no fresh tree to describe —
        # but the environment hazard is about the process, not the checkout, and
        # survives the absence of a worktree entirely.
        return (
            "- **Worktree tooling**: no worktree was declared for this dispatch, so no "
            "fresh tree was created and nothing here describes one. The environment "
            "caveat below still applies, because it is about the process you are running "
            "in rather than the checkout you are running on.\n" + _ENV_INHERITANCE_NOTE
        )
    if setup_commands:
        listed = "\n".join(_setup_lines(setup_commands))
        notes_line = (
            "  A `#` line under a command is the project's own note about it, carried "
            "through from `worktree_setup` — the part the command does not say about "
            "itself, and the reason the note is there rather than inferred.\n"
            if any(getattr(command, "note", "") for command in setup_commands)
            else ""
        )
        return (
            "- **Worktree tooling**: your worktree was created by `git worktree add`, "
            "which checks out **tracked files only** — everything gitignored (`.venv/`, "
            "`node_modules/`, built binaries, seeded databases) is absent until something "
            "creates it, and nothing has. This project declares what to run "
            "(`worktree_setup` in `kyosei/config.yml`); run it in your worktree before "
            "you trust any test result:\n\n"
            f"{listed}\n\n"
            f"{notes_line}"
            "  Kyosei printed these and ran none of them — what a worktree needs is a "
            "fact only this project holds, so it is declared rather than guessed, and "
            "executing it is yours. If a command here fails or turns out to be wrong, "
            "that is a defect in the declaration and belongs in `premise_false[]`, not "
            "in a workaround you keep to yourself.\n"
            f"{_ENV_INHERITANCE_NOTE}"
        )
    return (
        "- **Worktree tooling**: this project declares none (`worktree_setup` is unset in "
        "`kyosei/config.yml`), so nothing was provisioned for you and this brief promises "
        "nothing about what is installed. Your worktree was created by `git worktree add`, "
        "which checks out **tracked files only**: every gitignored dependency and build "
        "artifact — a `.venv/`, a `node_modules/`, a compiled binary, a seeded database — "
        "is absent, no matter what the project's README, CLAUDE.md or Makefile say, "
        "because those describe a checkout somebody had already built in. Expect to "
        "reconstruct tooling before the project's own test command will run, and say so in "
        "your report: the cost of that reconstruction is the evidence that this project "
        "should declare `worktree_setup`.\n"
        f"{_ENV_INHERITANCE_NOTE}"
    )


def _symbols_by_touch(documents: Sequence[dict]) -> dict[tuple[str, str], list[str]]:
    """``(record_id, path) -> [symbol, ...]`` for the symbol column.

    The *paths* come from ``claimed_paths`` (see :func:`_touches_block`) and never
    from here: symbols are detail hung off a path that is already in the claim, so
    a touch row naming a symbol can never widen the boundary this section states.
    """
    symbols: dict[tuple[str, str], list[str]] = {}
    for doc in documents:
        for touch in doc["touches"]:
            if touch["symbol"]:
                symbols.setdefault((doc["id"], touch["path"]), []).append(touch["symbol"])
    return symbols


#: KYO-327.c2: how the symbol column says where a symbol was, at freeze time. The
#: word "at" and not "on": the region a `path#symbol` touch claims can be several
#: lines (``sweep.symbol_regions``), and this is its first line — a place to jump to,
#: never a span to trust as the symbol's extent.
TOUCH_SYMBOL_LINE_FORMAT = "{symbol} (line {line})"

#: The sentence under the table that says what that number is and is not. A worker who
#: reads it as a current line number will hand-reconcile a file its own branch moved,
#: which is the failure this note exists to pre-empt.
TOUCH_SYMBOL_LINE_NOTE = (
    "A `(line N)` in the symbol column is where `kyo dispatch` found that symbol in "
    "this project's tree **at the moment this brief was frozen**, at the Base SHA "
    "above — resolved by the same textual search the touch was validated by, and the "
    "first line of the region it matched. It is a starting point for a read, not a "
    "current fact and not the symbol's extent: your own edits move it, and a symbol "
    "with no number is one the search could not place (KYO-327)."
)


def _symbol_with_line(symbol: str, line: int | None) -> str:
    if not line:
        return symbol
    return TOUCH_SYMBOL_LINE_FORMAT.format(symbol=symbol, line=line)


def _touches_block(
    *,
    primary_id: str,
    covered_ids: Sequence[str],
    claimed_paths: Mapping[str, set[str]],
    documents: Sequence[dict],
    provenance: Sequence[Mapping] = (),
    narrowings: Sequence[Mapping] = (),
    cooccurrence: Mapping | None = None,
    symbol_lines: Mapping[str, Mapping[str, int]] | None = None,
    test_history: Mapping | None = None,
) -> str:
    """The whole of what this dispatch claims, attributed record by record (KYO-76).

    This section used to render ``record["touches"]`` — the *primary* record's
    touches alone — while ``commands._claim_of`` computed the dispatch's claim over
    the primary record and every ``--also-covers`` record. Same input, two
    consumers, one of them never updated: a worker on a two-record dispatch
    was handed the criteria of both records and the touches of one, and the write
    scope statement below then told it that its capability boundary excluded files
    its own criteria required. KYO-73.d1 hit exactly that and had to file
    `premise_false` to get on with the work.

    So ``claimed_paths`` is passed in from ``commands._claim_of(...).per`` rather
    than recomputed here. The boundary a worker is told and the set the disjointness
    check compared against other running dispatches are then the same object, not
    two derivations that agree until one of them is edited (law 3).

    Attribution is per KYO-76.c1: a worker that cannot see which record put a path
    in scope cannot tell a deliberate claim from controller drift, and
    ``contracts.collision_detail`` already learned this lesson on the refusal side
    (KYO-65.c2).
    """
    lines_of = symbol_lines or {}
    order = [primary_id, *covered_ids]
    labels = {primary_id: TOUCH_SOURCE_PRIMARY}
    labels.update(dict.fromkeys(covered_ids, TOUCH_SOURCE_COVERED))
    symbols = _symbols_by_touch(documents)

    rows: list[str] = []
    undeclared: list[str] = []
    directories = False
    new_files = False
    additive = False
    located = False
    for record_id in order:
        paths = sorted(claimed_paths.get(record_id, set()))
        if not paths:
            undeclared.append(record_id)
            continue
        for path in paths:
            # KYO-209: a directory touch is scope this work creates or owns, and the
            # symbol column says so rather than "(whole file)" about something that is
            # not a file and may not exist yet. A worker that reads such a row as a
            # missing file goes looking for the file — which is the failure the record
            # was filed for, moved one step later.
            if is_directory_touch(path):
                directories = True
                named = TOUCH_DIRECTORY_SYMBOL
            elif is_new_file_touch(path):
                # KYO-225. Same reasoning as the directory row one branch up, one scale
                # smaller: a worker who reads this as an ordinary file touch goes
                # looking for a file that is not there and reports the Touches table as
                # wrong. The row has to say the file is the work's OUTPUT.
                new_files = True
                named = TOUCH_NEW_FILE_SYMBOL
            else:
                # KYO-300.c3: the additive claim is a symbol whose name is not the
                # point — a bare `+` in this column reads as a typo. The column says
                # what the row MEANS, exactly as the two branches above do for the
                # directory and new-file forms, and the note under the table says the
                # rest.
                claimed = symbols.get((record_id, path), [])
                if any(sweep.is_additive_symbol(symbol) for symbol in claimed):
                    additive = True
                placed = {
                    symbol: lines_of.get(path, {}).get(symbol)
                    for symbol in claimed
                    if not sweep.is_additive_symbol(symbol)
                }
                located = located or any(placed.values())
                named = (
                    ", ".join(
                        TOUCH_ADDITIVE_SYMBOL
                        if sweep.is_additive_symbol(symbol)
                        else _symbol_with_line(symbol, placed.get(symbol))
                        for symbol in claimed
                    )
                    or "(whole file)"
                )
            rows.append(f"| {record_id} ({labels[record_id]}) | {path} | {named} |")

    lines: list[str] = []
    if len(order) > 1:
        lines += [
            f"This dispatch covers {len(order)} records ({', '.join(order)}), so the table "
            "below is the union of what all of them declare. Every path in it is inside "
            "your write scope, whichever record put it there.",
            "",
        ]
    if rows:
        lines += ["| declared by | path | symbol |", "|---|---|---|", *rows]
    else:
        lines.append("| (none declared) |  |  |")
    if located:
        lines += ["", TOUCH_SYMBOL_LINE_NOTE]
    if directories:
        lines += ["", TOUCH_DIRECTORY_BRIEF_NOTE]
    if new_files:
        lines += ["", TOUCH_NEW_FILE_BRIEF_NOTE]
    if additive:
        lines += ["", TOUCH_ADDITIVE_BRIEF_NOTE]
    if undeclared:
        lines += [
            "",
            f"{', '.join(undeclared)} declare(s) no touches, so this list is not known to "
            "be complete — `kyo dispatch` reports that as `scope-unknown` rather than as "
            "an empty claim (KYO-48). If you need a file that is not above, that is "
            "ordinary touch drift: report it, do not stop for it.",
        ]
    lines += [
        "",
        "This is also the set `kyo dispatch` checked for disjointness against every other "
        "running dispatch, so it is both what you were checked against and what you may "
        "change (KYO-76). What that check is worth is stated below it (KYO-147.c1): it "
        "compared this DECLARED set against theirs, and a touches table is a prediction "
        "written before the work.",
        "",
        f"Shared resources: {SHARED_RESOURCES_LINE}",
    ]
    lines += _cooccurrence_block(cooccurrence)
    lines += _test_history_block(test_history)
    lines += _provenance_block(provenance, narrowings)
    return "\n".join(lines) + "\n"


def _cooccurrence_block(cooccurrence: Mapping | None) -> list[str]:
    """What this record's declared files have historically dragged in (KYO-147.c2).

    KYO-147.m2 is why this is derived rather than authored: the files a change forces
    are reachable from the type graph and the call graph, and a controller writing a
    touches table from an idea file is searching neither. `docs/reference/cli.md` is
    generated from the parser; `tests/test_kyo5_error_remedies.py` keys error sites
    positionally per function; `templates/hooks/*.json` were byte-identical twins.
    None of the three appears in a grep for the feature's name, and all three have
    broken a dispatch that did not name them.

    KYO-147.c3 is the silence: with no findings this contributes NOTHING — not a
    heading, not a "none". A young store has nothing to say and says nothing, and a
    weak signal is not printed with a percentage next to it to make it look like one.

    KYO-347: one row per DRAGGED file, not one row per pairing. `commands
    .touch_cooccurrence` returns one finding per (declared, also_touched) pair, so
    three declared files that each separately drag in the same fourth file used to
    print that file three times — a record touching App.tsx, StoreMonitor.ts and
    StoreRegistry.ts saw `types.ts` (and `mockData.ts`, and `styles.css`) three times
    each, nine rows for three real findings. What a reader wants from this table is
    "what am I about to also touch", and that question has one answer per file, not
    one per declared file that happens to imply it — so the grouping is here, in the
    rendering, over the --json shape unchanged (KYO-347.c2): a finding's `declared`,
    `also_touched`, `hits` and `support` are read, never recomputed.

    Row order is "the strongest pairing first", and needs no separate sort: `commands
    .touch_cooccurrence` already orders `findings` by rate desc, then hits desc
    (KYO-147.c2's own sort), so the first finding this function sees for a given
    dragged file is that file's strongest pairing, and grouping by first appearance
    preserves the order the query already computed.
    """
    findings = list((cooccurrence or {}).get("findings") or ())
    if not findings:
        return []
    groups: dict[str, list[Mapping]] = {}
    order: list[str] = []
    for finding in findings:
        dragged = finding["also_touched"]
        if dragged not in groups:
            groups[dragged] = []
            order.append(dragged)
        groups[dragged].append(finding)
    lines = [
        "",
        "### What files like these usually drag in",
        "",
        "Derived from this store's own history, not from your record: of the dispatches "
        "that touched each declared file below, this many also touched a file the table "
        "above does not name. **This refuses nothing and predicts nothing about your "
        "work** — it is the class of file that no touches table catches, because the "
        "coupling lives in the dependent file (a generated mirror, a positionally-keyed "
        "index, a byte-identical twin) rather than in the change. Check each one; if you "
        "do edit it, that is ordinary touch drift and you report it.",
        "",
        "| file this drags in | with (declared file, hits of support) |",
        "|---|---|",
    ]
    lines += [
        f"| {dragged} | "
        + ", ".join(f"{f['declared']} {f['hits']}/{f['support']}" for f in groups[dragged])
        + " |"
        for dragged in order
    ]
    return lines


#: KYO-327.c2: the label that keeps this block from being read as a coverage claim.
#: It is one sentence and it is not optional — the difference between "these tests
#: cover your files" (which this store cannot know) and "workers who changed your
#: files changed these tests" (which is all it observed) is the difference between a
#: fact and a guess with a table around it.
TEST_HISTORY_LABEL = (
    "**History, not coverage.** Of the dispatches that reported touching each declared "
    "file below, these are the test files they ALSO changed — where this store's "
    "workers have put the tests for these paths, counted from reported touches and "
    "nothing else. It is not a claim that any of them exercises your change, not a "
    "list of tests to run, and not complete: a test that covers your file and has "
    "never been edited alongside it cannot appear here. Read them, then run the tests "
    "that cover what you actually changed."
)

#: How many test files one declared path contributes. The block exists to save a
#: worker the first search of its session, not to reprint the suite — a path whose
#: every dispatch touched a different test file has no signal to give past the first
#: few, and this brief is paid for by the token.
_TEST_HISTORY_PER_PATH = 5


def _test_history_block(history: Mapping | None) -> list[str]:
    """Which test files past dispatches on these paths also changed (KYO-327.c2).

    Silent on no history, on exactly :func:`_cooccurrence_block`'s reasoning and for
    the same KYO-147.c3 reason: a young store has nothing to say and says nothing. The
    query is not a second one — ``commands.touch_test_history`` and
    ``commands.touch_cooccurrence`` read the same ``_reported_touch_sets`` through the
    same ``contracts.touch_covers``, and differ only in which of the co-touched files
    they keep. Two queries over one history would be two answers that get to disagree
    about what this store did (law 3).
    """
    findings = list((history or {}).get("findings") or ())
    if not findings:
        return []
    lines = [
        "",
        "### Where the tests for these files have been",
        "",
        TEST_HISTORY_LABEL,
        "",
        "| your declared file | test file also changed | how often |",
        "|---|---|---|",
    ]
    for finding in findings:
        for test in list(finding["tests"])[:_TEST_HISTORY_PER_PATH]:
            lines.append(
                f"| {finding['declared']} | {test['path']} | {test['hits']} of "
                f"{finding['support']} dispatches |"
            )
    return lines


def _provenance_block(
    provenance: Sequence[Mapping] | None, narrowings: Sequence[Mapping] | None
) -> list[str]:
    """What the disjointness check established, said to the worker (KYO-147.c1/c5).

    The worker is the one party that cannot see the other dispatches, and the one the
    consumer observed doing the check by hand — correctly, twice, and only because
    the standing stance told them to flag rather than proceed. Telling them what the
    mechanism actually compared is what makes that hand-check either unnecessary or
    obviously necessary, instead of a judgment call made without the facts.

    Written from :func:`touch_drift.declaration_provenance`'s rows rather than
    recomputed, so this and the controller's terminal cannot disagree (law 3).
    """
    rows = list(provenance or ())
    narrowed = list(narrowings or ())
    if not rows and not narrowed:
        return []
    lines = ["", "### What the disjointness check compared", ""]
    if rows:
        lines.append(
            f"Your declared set above was compared against the DECLARED touches of "
            f"{len(rows)} running dispatch(es). Every one of those is a prediction "
            "written before that work started — ingesting a report moves a dispatch out "
            "of `running`, so no set in this comparison has ever been checked against a "
            "diff. How stale each one is:"
        )
        lines.append("")
        for entry in rows:
            # Branches on the two FACTS, never on `staleness`'s label: this module
            # imports neither `cmd_dispatch` nor `commands` (it is a pure renderer,
            # and the dependency would be circular anyway), and re-declaring the
            # label here would be a second spelling of one vocabulary — the drift
            # law 3 exists to prevent. The producer carries both facts for exactly
            # this reason; see `touch_drift.declaration_provenance`.
            if entry.get("unreconciled_files"):
                how = (
                    "**known incomplete** — an earlier report on that claim named "
                    + ", ".join(entry["unreconciled_files"])
                    + ", which it still does not declare, so the check did not see "
                    "those files"
                )
            elif entry.get("prior_reported_file_count"):
                how = (
                    "reconciled once — every file an earlier report on that claim named "
                    "is covered by what it declares now"
                )
            else:
                how = (
                    "never checked against work — neither it nor any earlier dispatch "
                    "on that claim has reported"
                )
            lines.append(
                f"- `{entry['dispatch_id']}` (dispatching {entry['record_id']}), "
                f"{len(entry['declared_files'])} declared path(s): {how}."
            )
    symbol_narrowed = [n for n in narrowed if not n.get("running_directories")]
    if symbol_narrowed:
        lines += [
            "",
            "One or more files are declared by BOTH sides at disjoint `#symbol`s, which "
            "is a shared file rather than a collision (KYO-147.c5). You are in the same "
            "file as another worker; stay inside your symbols, and expect their edits "
            "elsewhere in it:",
            "",
        ]
        lines += [
            f"- `{n['path']}` — you: "
            + ", ".join("`#" + s + "`" for s in n["candidate_symbols"])
            + f"; `{n['dispatch_id']}` ({n['record_id']}): "
            + ", ".join("`#" + s + "`" for s in n["running_symbols"])
            for n in symbol_narrowed
        ]
    # KYO-395: a file this work creates, under a directory another running dispatch
    # claims. Branches on the fact the entry carries (`running_directories`), not on
    # a label, for the reason the provenance rows above do. Rendered as a WARNING:
    # this is the stored record of an overlap the check let through (c3).
    under_directory = [n for n in narrowed if n.get("running_directories")]
    if under_directory:
        lines += [
            "",
            "**WARNING — let through, not cleared** (KYO-395). A file you are declared to "
            "create lies inside a directory another running dispatch claims. It does not "
            "exist yet, so it is not in that dispatch's frozen plan — but a directory "
            "touch claims a tree its work creates or owns, and that worker is free to "
            "create files there, this same name included. Before you create it, check "
            "the other branch has not; if it has, stop and flag it in `premise_false[]`:",
            "",
        ]
        lines += [
            f"- `{n['path'].lstrip('+')}` — you"
            + (f" (`{n['candidate_dispatch_id']}`)" if n.get("candidate_dispatch_id") else "")
            + "; inside "
            + ", ".join(f"`{d}`" for d in n["running_directories"])
            + f", claimed by `{n['dispatch_id']}` ({n['record_id']})"
            for n in under_directory
        ]
    return lines


#: KYO-151.c1. Rendered into the brief of any dispatch whose claim includes the
#: packaged standing-rules ledger, and into no other.
#:
#: The record's whole finding is that this wall is invisible until you walk into it.
#: KYO-132's dispatch and KYO-149's each rediscovered it from a red suite, each spent
#: part of its budget on the diagnosis, and each left the controller a bespoke artifact
#: (a script, then a patch). ``tests/test_procedures.py`` does document the hand-off —
#: inside the file whose failure sends you looking, which is exactly where the knowledge
#: is useless. So it is stated here instead: derivable at freeze time from data the
#: store already holds, and delivered before the surprise rather than after it.
#:
#: Deliberately not a "your suite may go red" warning alone. The interval matters and a
#: controller can miss it: between the merge and the hand-application this repository's
#: own briefs carry the OLD rule text while a build made from that commit ships the new
#: one, so the worker is asked for the replacement text in its report rather than left
#: to assume somebody will re-derive it.
_LEDGER_HANDOFF_BLOCK = (
    "## The packaged ledger is yours; the store copy is an orchestrator hand-off\n"
    "\n"
    f"Your touches include `{PACKAGED_LEDGER_SOURCE_PATH}` — the standing-rules ledger "
    "this build **packages**. Editing it is in your scope. The copy that actually "
    f"reaches a worker is `{STORE_LEDGER_PATH}`, this project's own ledger, and that "
    "one is not yours at any point: `kyo check-branch` fails a dispatch branch that "
    f"diffs anything under `{STORE_LEDGER_PATH.split('/')[0]}/`, and it runs at every "
    "worker stop. So a rule you add or reword here reaches nobody until an orchestrator "
    "applies it on the other side of your merge.\n"
    "\n"
    "The command that performs the hand-off is `kyo install-procedures`, run by the "
    "orchestrator on the project's main checkout after your branch lands. It **inserts** "
    "packaged rules the store copy lacks and rewrites nothing already there — which "
    "means it closes the gap for a rule you ADDED and deliberately does not for a rule "
    "you REWORDED, or for any text outside a rule's body. For those, the exact "
    "replacement text is yours to supply, in `premise_false[]` or `summary`, because "
    "you are the only party who knows what it should say.\n"
    "\n"
    "Two consequences, both of them conditions of this work rather than surprises. "
    "First, this repository's own cross-copy check in "
    "`tests/test_procedures.py` (in Kyosei's own repository) will fail on your branch "
    "for precisely this reason, and that failure is the mechanism "
    "working: do not weaken the check, do not relax it, and do not write the store copy "
    "to buy green — report it. Second, between your merge and the hand-application this "
    "project's briefs carry the old text while a build from that commit ships the new "
    "one, so name in your report every rule whose text moved."
)


def _ledger_handoff_block(claimed_paths: Mapping[str, set[str]]) -> list[str]:
    """KYO-151.c1: the hand-off block, for a dispatch that claims the packaged ledger.

    Keyed off the *claim* (``commands._claim_of(...).per``, the same value the Touches
    table and the disjointness check read) rather than off any record's ``touches``
    directly, so a path that reached the worker's write scope by any route triggers it —
    one value, three readers (law 3).

    Silent when the path is not claimed, and silent for every consumer project always:
    :data:`PACKAGED_LEDGER_SOURCE_PATH` is a path only Kyosei's own repository has.
    """
    claimed = {path for paths in claimed_paths.values() for path in paths}
    if PACKAGED_LEDGER_SOURCE_PATH not in claimed:
        return []
    return [_LEDGER_HANDOFF_BLOCK, ""]


def _doc_fragments_block(
    dispatch_id: str, numbered_documents: Sequence[Mapping]
) -> list[str]:
    """KYO-299.c2: per declared append-only document, where this dispatch's new section
    goes and what it must not do.

    **Generated, and stated in every brief a project with such a document produces** —
    not typed into a task-prose block per dispatch, for the reason KYO-90 and KYO-199
    both give about themselves: an instruction only some briefs carry is an instruction
    most workers never receive, and the workers who do not receive it are exactly the
    ones who will write ``## 205.`` into the file by hand.

    Silent for every project that declares nothing, which is all of them until one does.

    Each row is ``{"path", "set", "fragments", "heading", "after", "placeholder",
    "fragment_path", "placeholder_heading", "amends"}`` — rendered, never derived here.
    ``amends`` (KYO-493) is ``{"field", "status"}`` when the set declares
    ``numbered.amends:`` and absent or ``None`` otherwise; only then does the block say
    how a fragment names the sections it amends. The
    fragment path in particular is computed by
    :meth:`kyosei.docs_numbered.NumberedDocument.fragment_path`, which is the same method
    `kyo check-branch`'s refusal names and `kyo docs assemble` parses back, so what the
    brief tells a worker and what the two commands expect cannot drift (law 3).
    """
    rows = list(numbered_documents)
    if not rows:
        return []
    lines = [
        "## Documents that grow by numbered section — write a fragment, never the document",
        "",
        "This project declares the document(s) below as **numbered and append-only**. "
        "Their section numbers are cited across the repository and their indexes are "
        "generated from them, so two workers who each append \"the next number\" in the "
        "same wave collide three ways at once — on the number, on the tail of the file, "
        "and on every generated index. A touch cannot prevent that: a touch narrows a "
        "claim and does not move a byte.",
        "",
        "So the number is not yours to choose. You write each new section to **a file "
        "of your own**, under the declared fragments directory, named after this "
        "dispatch — a path no other dispatch can write, so there is nothing to collide "
        "on — and your orchestrator runs `kyo docs assemble` on the merged tree, where the "
        "whole wave is visible and the numbering is decidable.",
        "",
    ]
    for row in rows:
        lines.append(f"**{row['path']}** (declared as `{row['set']}` in kyosei/documents.yml)")
        lines.append("")
        lines.append(
            f"- Write your new section to `{row['fragment_path']}` — **one file per new "
            "section**. Pick the slug yourself: a few lowercase words, hyphenated, "
            "naming the section. If you have several sections to add, write several "
            "files, one per section, each with a distinct slug; a fragment receives one "
            "number, so `kyo docs assemble` refuses a file carrying two sections. One "
            "dispatch's files are numbered in file-name order."
        )
        lines.append(
            f"- Head it `{row['placeholder_heading']} <title>`, with `{row['placeholder']}` "
            "written exactly like that where the number goes. Use the **same** "
            f"`{row['placeholder']}` anywhere inside the section that cites this "
            "section's own number — a metadata block, a \"see section N\" of its own. "
            "`kyo docs assemble` replaces every occurrence in the fragment with the one "
            "number it assigns."
        )
        lines.append(
            f"- **Do not append a numbered section to `{row['path']}` itself**, and do "
            "not regenerate its index. `kyo check-branch --subagent-stop` refuses a "
            "branch that adds a numbered heading to it and names this same fragment "
            "path; the index is rebuilt by this project's own command, which your "
            "orchestrator runs after assembling"
            + (f" (`{'`, `'.join(row['after'])}`)." if row["after"] else ".")
        )
        amends = row.get("amends")
        if amends:
            lines.append(
                "- **If your section amends earlier sections, name them on the "
                "fragment's first line** as `<!-- kyosei-fragment amends: 178, 186 -->` "
                "(section numbers of this same document; brackets allowed). **Do not "
                f"edit their `{amends['field']}` or their `status` in place**: "
                "`kyo docs assemble` writes your section's number onto each one's "
                f"back-link and sets its status to `{amends['status']}`, on the merged "
                "tree. `kyo check-branch --subagent-stop` refuses a header it cannot "
                "read or one naming a section the document lacks. Prose pointers in the "
                "old section (\"Amended by <record>\") stay ordinary in-place edits."
            )
        lines.append(
            f"- **Editing an EXISTING section of `{row['path']}` is an ordinary edit** "
            "and is not refused. Declare it the ordinary way, with a "
            "`path#section heading` touch, and edit the document in place. Only the "
            "ADDITION of a new numbered section goes through a fragment, because only "
            "the addition has to agree with every other worker about a number."
        )
        lines.append("")
    lines.append(
        f"Your fragment is part of your branch: commit it like any other file. "
        f"`{dispatch_id}` is already in the file name, which is how `kyo docs assemble` "
        "orders the wave's fragments by dispatch-creation order."
    )
    lines.append("")
    return lines


def _claims_table(claims: list[dict]) -> str:
    if not claims:
        return "| (none) |  |  |  |  |\n"
    lines = ["| claim | level | instrument/method | author | date |", "|---|---|---|---|---|"]
    for c in claims:
        marker = f" [superseded by {c['superseded_by']}]" if c["superseded_by"] else ""
        lines.append(
            f"| {c['statement']}{marker} | **{c['level']}** | {c['method']} | "
            f"{c['author']} | {c['created_at']} |"
        )
    return "\n".join(lines) + "\n"


#: KYO-327.c1: the separator ``procedures.md``'s ``enforced-by`` bullet puts between
#: the enforcement *level* and the argument for it. Every rule in the packaged ledger
#: writes it as a spaced em dash; the en dash and the ASCII double hyphen are here
#: because the ledger is project-editable and a consumer writing one of those must not
#: be punished with a paragraph where everyone else gets a line. A bullet with none of
#: them has no argument to split off, and the whole of it is then the level — which is
#: the honest reading, and is carried whole rather than cut at some byte count.
_ENFORCED_BY_SEPARATORS = (" — ", " – ", " -- ")

#: A backticked token in an ``enforced-by`` bullet that names something a reader can
#: RUN or OPEN: a command, or a file in a repository. Flags (``--why``), field names
#: (``write_scope``) and prose in backticks are not enforcers and are not lifted.
_BACKTICKED = re.compile(r"`([^`\n]+)`")
_ENFORCER_FILE = re.compile(r"^[\w./+-]+\.(?:py|json|md|ya?ml|toml|sh)$")

#: How many enforcers one rule's one-line summary may name. Three is what the longest
#: bullet in the packaged ledger actually cites (``make test LOG=``, ``make lint LOG=``
#: and the test that pins them); a fourth has never been needed and the cap is here so
#: that a future essay cannot grow this line back into a paragraph.
_ENFORCERS_PER_RULE = 3


def _command_key(token: str) -> str:
    """The identity of a cited command, ignoring the flags and arguments it is shown
    with — so ``kyo check-clean`` and ``kyo check-clean --help`` are one citation and
    not two, while ``kyo report`` and ``kyo report-schema`` stay two."""
    words: list[str] = []
    for word in token.split():
        if word.startswith(("-", "<", "[")):
            break
        words.append(word)
    return " ".join(words) or token


def _enforcers(text: str) -> list[str]:
    """Every runnable-or-openable thing ``text`` names in backticks, in order, deduped
    by :func:`_command_key`."""
    found: list[str] = []
    seen: set[str] = set()
    for raw in _BACKTICKED.findall(text):
        token = " ".join(raw.split())
        if not (token.startswith(("kyo ", "make ", "git ")) or _ENFORCER_FILE.match(token)):
            continue
        key = _command_key(token)
        if key in seen:
            continue
        seen.add(key)
        found.append(token)
    return found


#: KYO-531.c1: the two words :func:`enforcement_kind` can return.
ENFORCEMENT_ASPIRATIONAL = "aspirational"
ENFORCEMENT_MECHANISM = "mechanism"


def enforcement_kind(enforced_by: str) -> str:
    """An ``enforced-by`` bullet's kind in one word (KYO-531.c1): ``aspirational`` or
    ``mechanism``.

    The ledger's "Rule format" offers exactly two shapes for the bullet: the mechanism
    that fails when the rule is broken, or ``aspirational``. A level written as
    ``mechanism, partial``, ``mechanism, opt-in`` or as the machinery itself
    (```SubagentStop` hook, `kyo check-clean```) is a mechanism; one that opens with
    ``aspirational`` -- including ``aspirational, with one mechanical edge`` and
    ``aspirational in v1`` -- is aspirational. The qualifier and the argument are one
    `kyo rules "<title>"` away, which is the point of a one-line form."""
    cuts = [at for at in (enforced_by.find(s) for s in _ENFORCED_BY_SEPARATORS) if at != -1]
    level = (enforced_by[: min(cuts)] if cuts else enforced_by).strip().lower()
    if level.startswith(ENFORCEMENT_ASPIRATIONAL):
        return ENFORCEMENT_ASPIRATIONAL
    return ENFORCEMENT_MECHANISM


def enforced_by_summary(enforced_by: str) -> str:
    """One line: the enforcement level, and what enforces it (KYO-327.c1).

    The ledger's ``enforced-by`` bullet is a level (``mechanism``, ``mechanism,
    partial``, ``aspirational``, or the mechanisms themselves) followed by an em dash
    and the argument for that level — history, the measured incident, the admission of
    what is *not* covered. The argument is the reason the bullet is trustworthy and the
    reason it is long: the ten worker-audience rules of this build's own ledger spend
    roughly nine tenths of the Standing-rules section on it, in every brief, whether or
    not the worker ever needs it.

    So the level is kept verbatim, the commands and test files the argument cites are
    lifted out of it (they are the operative half — "which check will fail me"), and
    the argument itself moves behind :data:`WORKER_RULE_FULL_TEXT_COMMAND`. Nothing is
    summarised in the sense of being restated: every word that survives is a word the
    ledger wrote, which is what keeps this from changing a rule's meaning.
    """
    # The EARLIEST separator, not the first spelling that happens to appear: a bullet
    # writing "aspirational -- nothing checks it — and here is why" must cut at the
    # first break, or the "level" grows an argument.
    cuts = [at for at in (enforced_by.find(s) for s in _ENFORCED_BY_SEPARATORS) if at != -1]
    level = (enforced_by[: min(cuts)] if cuts else enforced_by).strip()
    named = [
        token
        for token in _enforcers(enforced_by)
        if _command_key(token) not in {_command_key(t) for t in _enforcers(level)}
    ][:_ENFORCERS_PER_RULE]
    if not named:
        return level
    # A lifted test file loses the sentence that said where it lives. In a consumer
    # project `tests/test_procedures.py` is not a file the reader has, and the ledger
    # says so beside every such citation (KYO-149); the lifted form has to keep saying
    # it, in the words tests/test_kyo149_consumer_paths.py reads.
    cited = [
        f"`{token}`" + (" (in Kyosei's own repository)" if _ENFORCER_FILE.match(token) else "")
        for token in named
    ]
    return f"{level} — {', '.join(cited)}"


#: KYO-327.c1: the read-only command that hands a rule's full text back. Named in the
#: brief rather than the ledger's path, because the path is not reachable from every
#: worker's worktree: under the split layout the store is a different repository from
#: the code, so a worker sent to a `git worktree add` of the code tree has no
#: ``kyosei/procedures.md`` to open, while `kyo rules` reads whichever ledger this
#: build can see and says which one it read.
WORKER_RULE_FULL_TEXT_COMMAND = 'kyo rules "<title>"'

#: The one line that makes the shortening honest: it says what was removed, that
#: nothing was rewritten, and how to get it back. A worker who needs the argument for a
#: rule needs it for ONE rule, once — that is the whole trade.
WORKER_RULES_FULL_TEXT_NOTE = (
    "Each rule above is its title, its one-line enforced-by (the level, and the "
    "commands or tests that hold it up), and the first sentence of the ledger's "
    "reasoning — the ledger's own words, never a paraphrase. The rest of each rule "
    "(the whole enforced-by argument, the incident behind it, what it admits it does "
    f"NOT cover) is one read-only command away: `{WORKER_RULE_FULL_TEXT_COMMAND}` "
    "prints one rule verbatim, and `kyo rules` alone lists every rule this project "
    "carries. Read the full text of any rule you are about to lean on (KYO-327)."
)


def _worker_rules_block(rules: list[Rule]) -> str:
    if not rules:
        return "(no worker-audience rules found in kyosei/procedures.md)\n"
    lines = []
    for r in rules:
        lines.append(f"- **{r.title}** (enforced-by: {enforced_by_summary(r.enforced_by)})")
        if r.first_line:
            lines.append(f"  {r.first_line}")
    lines += ["", WORKER_RULES_FULL_TEXT_NOTE]
    return "\n".join(lines) + "\n"


def _covers_block(covered_records: list[dict]) -> str:
    """KYO-13.c4 / KYO-12: render every record this dispatch formally covers in
    addition to its primary record, each with its own criteria table so a worker
    can act on them with the same authority as the primary record's — and so
    `kyo report` knows exactly which criteria ids a report filed against this
    dispatch is allowed to carry verdicts for."""
    if not covered_records:
        return ""
    parts = [
        "## Also formally covers",
        "",
        "This dispatch's report may carry `criteria_verdicts` for the primary "
        "record above **and** every record below (KYO-12 fix: a dispatch either "
        "formally declares what it covers, or it does not carry verdicts for it — "
        "no informal bundling). Each is listed with its own current criteria.",
        "",
    ]
    for covered in covered_records:
        parts += [
            f"### {covered['id']} — {covered['title']}",
            "",
            covered["description"] or "(no description)",
            "",
            _criteria_table(covered["criteria"]),
        ]
    return "\n".join(parts) + "\n"


def touch_paths_named_in(brief_text: str) -> set[str]:
    """The paths a frozen brief's Touches table actually names.

    The reader for the table :func:`_touches_block` writes, living next to it so
    the format has one definition rather than one in the renderer and a regex in
    whatever wants to check it. A brief is frozen into ``kyosei/export/_briefs.jsonl``
    and is what a worker was *actually* told, so "what boundary did this dispatch
    state" is a question that has to be answerable from the text — KYO-76.c2 is that
    question asked as an equality against ``commands._claim_of``.
    """
    paths: set[str] = set()
    for line in brief_text.splitlines():
        if not line.startswith("| ") or not line.endswith(" |"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 3:
            continue
        declared_by, path, _symbol = cells
        if not (declared_by.endswith(f"({TOUCH_SOURCE_PRIMARY})") or
                declared_by.endswith(f"({TOUCH_SOURCE_COVERED})")):
            continue
        paths.add(path)
    return paths


#: KYO-294.c2: the one sentence in the Base SHA bullet that makes a claim about what
#: the *stop check* will do, pinned as a constant because a test has to read it back
#: and compare it against ``kyo check-branch``'s behaviour on the fixture it describes
#: (``tests/test_kyo294_inherited_store_diff.py``).
#:
#: It exists because the two used to contradict each other in the ordinary case. The
#: bullet has told workers since KYO-105 that a worktree cut one commit ahead of the
#: recorded base is normal and "not something to hand-reconcile", while
#: ``cmd_check_branch.cmd_check_branch`` diffed against that recorded base (KYO-285) and so
#: refused the worker's stop for the controller's own store commit — advice from one
#: surface, a block from the other, for one state. A worker of that wave rebased its
#: branch to clear it, after a hand-back its controller had already merged, and main
#: and the branch then carried different shas for the same tree. Whichever side of the
#: pair someone changes next, this constant is where the claim is written down once,
#: so it cannot be changed on one side alone without a test failing.
BASE_SHA_INHERITED_STORE_COMMITS = (
    "A store commit the orchestrator made between stamping this sha and creating your "
    "worktree is inherited, not yours: `kyo check-branch` judges the commits your "
    "branch added past the point it was cut from the trunk, so an inherited "
    "`kyosei/` or `ideas/` diff does not block your stop -- and a store commit you "
    "make yourself is refused exactly as it always was (KYO-294)."
)


def _recx_trailer_block(recx_manifest: bool) -> list[str]:
    """KYO-614.c1: the ``Recx-Checked`` trailer instruction, beside the ``docs_verdicts[]``
    paragraph, in a project that governs its RECX.yaml (KYO-600) and in no other.

    recx's own drift reminder runs when a worker stops (SubagentStop) and does not read a
    report, so a worker that answered a module obligation in ``docs_verdicts[]`` is asked
    the same question again unless its commit says the entry was checked. `kyo report`
    warns when the branch tip does not say so (``report_ingest``)."""
    if not recx_manifest:
        return []
    from .docs_recx import CHECKED_TRAILER, CHECKED_TRAILER_SINCE, MANIFEST_PATH

    return [
        f"This project governs its {MANIFEST_PATH} as a recx manifest. When you answer a "
        f"`{MANIFEST_PATH} [module <id>]` obligation in `docs_verdicts[]`, whether "
        "`updated` or `not-affected`, put a git trailer naming that module id on your "
        f"branch's last commit, for example `{CHECKED_TRAILER}: core, cli` (one trailer may "
        "name several ids, separated by commas, and the trailer may repeat), and put it "
        "again on any commit you make after that, since recx reads a trailer as covering "
        "its own commit and the earlier ones only. recx's drift reminder, which runs when "
        f"you stop, honours it from recx-cli {CHECKED_TRAILER_SINCE}; `kyo report` warns "
        "for each module you answered that the branch tip's trailers do not name. Write "
        "the trailer only after reading the module entry against the code.",
        "",
    ]


def generate_brief(
    *,
    record: dict,
    kind: Kind,
    dispatch_id: str,
    worked_from_sha: str,
    #: KYO-485: the ``--base`` ref ``worked_from_sha`` was resolved from, or ``None``
    #: when it is the code repository's HEAD (every dispatch before KYO-485).
    base_ref: str | None = None,
    #: KYO-487: the existing branch ``worked_from_sha`` is the tip of, when the
    #: dispatch's worktree checks that branch out as it stands; ``None`` otherwise.
    base_branch: str | None = None,
    generated_at: str,
    worktree: str = "",
    branch: str = "",
    worker_model: str | None = None,
    #: Human text naming *which declaration* produced ``worker_model`` — the caller
    #: renders it from ``cmd_dispatch.MODEL_SOURCE_LABELS`` so the dispatch echo and
    #: the frozen brief say the same sentence about one resolution (law 3).
    worker_model_source: str = "",
    worker_rules: list[Rule],
    controller_task_prose: str,
    covered_records: Sequence[dict] = (),
    #: ``{record_id: {path, ...}}`` — this dispatch's claim, from
    #: ``commands._claim_of(...).per``. Required rather than derived from
    #: ``record``/``covered_records`` so that the boundary the Touches section
    #: states and the set the disjointness check compared are one value (KYO-76).
    claimed_paths: Mapping[str, set[str]],
    #: KYO-132.c5: the *other* dispatches in state ``running`` at the instant this
    #: brief is frozen, as ``{"dispatch_id": ..., "record_id": ...}`` rows. Passed
    #: in rather than queried here for the same reason ``claimed_paths`` is: the
    #: caller has already computed this set for the disjointness check, and one
    #: value read twice is two sources of truth that get to disagree (law 3).
    concurrent_dispatches: Sequence[Mapping] = (),
    #: KYO-143: ``config.worktree_setup`` — the commands this project declares a
    #: fresh worktree needs, empty when it has declared none. Passed in from the
    #: loaded config rather than read here, for the reason every other declaration
    #: in this signature is: the brief renders what the project stated, and a
    #: second reader of the same file is a second source of truth (law 3). Empty
    #: is a rendered state, not a skipped one (KYO-143.c4).
    worktree_setup: Sequence[str] = (),
    #: KYO-147.c1: ``touch_drift.declaration_provenance`` rows — how stale each
    #: other running dispatch's DECLARED touches are. Passed in for the same law-3
    #: reason as everything above it, and with one extra: it is derived from the
    #: identical ``others_running`` list the disjointness check consumed, so a brief
    #: cannot describe a comparison other than the one that was actually made.
    disjointness_provenance: Sequence[Mapping] = (),
    #: KYO-147.c5: file-level overlaps that narrowed away because both sides named
    #: disjoint ``#symbol``\\ s, from ``commands.touch_narrowings``.
    touch_narrowings: Sequence[Mapping] = (),
    #: KYO-147.c2/c3: ``commands.touch_cooccurrence`` — what dispatches touching this
    #: record's declared files have historically also touched. ``None`` and an empty
    #: findings list render identically: as nothing at all.
    touch_cooccurrence: Mapping | None = None,
    #: KYO-327.c2: ``{path: {symbol: line}}`` — where each ``path#symbol`` touch was
    #: found in the tree this brief was frozen from, from
    #: ``touch_drift.touch_symbol_lines``. Resolved there and not here for the reason
    #: every other declaration in this signature is passed in: the brief renders what
    #: it is handed, and a second reader of the working tree is a second source of
    #: truth (law 3). Missing, empty, or missing one symbol all render the same way —
    #: as the bare symbol, exactly as before.
    touch_symbol_lines: Mapping[str, Mapping[str, int]] | None = None,
    #: KYO-327.c2: ``commands.touch_test_history`` — the test files that past reported
    #: dispatches on these declared paths also changed. Empty renders as nothing at
    #: all, like ``touch_cooccurrence``, which it shares its query with.
    touch_test_history: Mapping | None = None,
    #: KYO-299.c2: this project's numbered, append-only documents, one row each, from
    #: ``dispatch_docs.numbered_document_rows``. Passed in rather than read here for the
    #: law-3 reason every declaration in this signature is: the brief renders what the
    #: project stated, and a second reader of ``documents.yml`` is a second source of
    #: truth. Empty for every project that declares none, which renders as nothing.
    numbered_documents: Sequence[Mapping] = (),
    #: KYO-614.c1: whether this project's ``documents.yml`` declares a
    #: ``format: recx-manifest`` set, from ``docs_recx.project_manifest``. Passed in for
    #: the law-3 reason above. ``False`` renders nothing, which is every project that has
    #: not opted in.
    recx_manifest: bool = False,
) -> str:
    """Compose the frozen brief text. ``record`` is an ``export.record_document``
    shape (already carries criteria/touches/claims in their canonical form).
    ``covered_records`` (KYO-13.c4) are additional records this dispatch formally
    covers, in the same shape — see :func:`_covers_block`."""
    gate_line = ""
    # No gate model on `record` here (gates are a M2 concept on the record itself,
    # not surfaced through this slice); templates/brief-template.md's `{{#if
    # gate}}` block is conditional, so simply omitting it when absent is faithful.
    covered_list = list(covered_records)
    touches_block = _touches_block(
        primary_id=record["id"],
        covered_ids=[doc["id"] for doc in covered_list],
        claimed_paths=claimed_paths,
        documents=[record, *covered_list],
        provenance=disjointness_provenance,
        narrowings=touch_narrowings,
        cooccurrence=touch_cooccurrence,
        symbol_lines=touch_symbol_lines,
        test_history=touch_test_history,
    )

    parts = [
        f"# Dispatch brief: {record['id']} — {record['title']}",
        "",
        f"- **Kind**: {kind.name} ({kind.archetype})",
        f"- **Dispatch id**: {dispatch_id}",
        # KYO-105: this bullet used to call the one-commit offset structural ("by
        # construction ... expected to be one commit ahead"), which made the equally
        # normal zero-offset case read as the anomaly the bullet told the worker to
        # watch for — two premise_false reports (KYO-97.r1, KYO-99.r1) in one session
        # said exactly that. The KYO-10 sequencing is real and stays; what the offset
        # actually depends on is when the controller commits the store relative to
        # creating the worktree, so both 0 and 1 are predicted here and neither is
        # described as a deviation.
        f"- **Base SHA**: `{worked_from_sha}` — "
        + (
            # KYO-485: a deliberate starting point, not the repository's HEAD.
            f"the commit `kyo dispatch --base {base_ref}` resolved to when this brief "
            "was generated, deliberately not the code repository's HEAD; the worktree "
            "was cut there. "
            if base_ref
            # KYO-487: an existing branch checked out as it stands starts at its tip.
            else f"the tip of the existing branch `{base_branch}` when this brief was "
            "generated, deliberately not the code repository's HEAD; the worktree "
            "checks that branch out as it stands. "
            if base_branch
            else "HEAD in this repo at the moment this brief was generated. "
        )
        + "KYO-10: dispatch generation stamps this sha and *then* "
        "commits the record of dispatching, so your worktree branches either from "
        "this exact sha or from one commit ahead of it, depending on when the "
        "orchestrator committed the store — both are normal, neither is a premise to "
        "flag, and neither is something to hand-reconcile. Only a branch point "
        "*behind* this sha, or ahead of it by unrelated commits, is worth a look. "
        # KYO-294: the sentence above was true advice and the stop check disagreed
        # with it. Both halves of the pair are now written down in one place, and a
        # test reads this text against what `kyo check-branch` actually does.
        f"{BASE_SHA_INHERITED_STORE_COMMITS} "
        "Branch your worktree from whatever HEAD "
        "actually is when you start, and report the **actual sha you branched from** "
        "(read it yourself, e.g. `git rev-parse HEAD`, never copied from this line) "
        "as `worked_from_sha` in your structured report.",
        f"- **Generated**: {generated_at} UTC",
        # KYO-22: a worker had to *infer* where it was, and two
        # concurrent dispatches inferred the same checkout — one worker's commit
        # landed on the other's branch. This slot is machine-filled from what the
        # controller declared at `kyo dispatch --worktree`, so it is never a guess.
        # KYO-52: stated relative to the project root, because the brief is frozen
        # into `_briefs.jsonl` — git-canonical and permanent — and briefs stating an
        # absolute path were the single largest source of usernames in this repo's
        # own export. The relative form says the same thing to a worker that has
        # been told which project it is working on.
        f"- **Worktree**: `{worktree}` (relative to the project root) — do your work "
        "here, and nowhere else. If this "
        "is not where you actually are, say so in `premise_false[]` rather than "
        "quietly working somewhere else: `kyo dispatch` refuses a second running "
        "dispatch into this same path, and that refusal is worthless if the path is "
        "wrong."
        if worktree
        else "- **Worktree**: (not declared at dispatch time)",
        # KYO-32, the same slot style one line down: a consumer's worker created
        # its own branch instead of the one the controller intended to merge. The
        # merge then targeted an empty branch, `git merge` said "Already up to
        # date" and exited 0, and nothing shipped. Nothing in the brief had ever
        # named a branch, so the worker was not disobeying — it was guessing.
        f"- **Branch**: `{branch}` — commit on this branch and no other. Do not "
        "create your own: the branch named here is the one your orchestrator will "
        "merge, and `git merge` of a branch you never committed to reports "
        '"Already up to date" and exits 0, so nothing would ship and nothing '
        "would say so. Every sha in your report's `commits[]` must be reachable "
        f"from `{branch}`; `kyo report` refuses the report otherwise. If you "
        "believe the wrong branch was declared, say so in `premise_false[]` "
        "rather than quietly branching elsewhere."
        if branch
        else "- **Branch**: (not declared at dispatch time) — no branch was named "
        "for this dispatch, so `kyo report` cannot check that your commits landed "
        "where your orchestrator expects. Commit on the branch your worktree is "
        "already on, and name it in your final message.",
        # KYO-61, the third slot of the same family. Unlike the two above it, this
        # one is stated and never checked, and it says so: Kyosei does not spawn
        # workers — the controller's harness does — so no `kyo` command can make a
        # harness honour this, and a field the tool records but cannot verify has to
        # admit that or become the decoration law 2 exists to prevent. Deliberately
        # NOT a thing to report back: a worker restating the condition its own
        # dispatch declared is a second, weaker source of truth for one fact
        # (KYO-32's lesson), and law 3 says the two then get to disagree.
        f"- **Worker model**: `{worker_model}` — "
        f"{worker_model_source or 'declared for this dispatch'}. Recorded on the "
        "dispatch and stated here so the conditions of this work are on the record; "
        "`enforced-by: aspirational` — nothing verifies it, and you are not asked to "
        "report it back."
        if worker_model
        else "- **Worker model**: (not declared at dispatch time) — nothing in this "
        "project named a model for this work, so the dispatch records none rather "
        "than a default nobody chose. Not something to report back.",
        # KYO-132.c5, the fourth slot of the same family and the only one that is a
        # fact about the *board* rather than about this dispatch's own declarations.
        _concurrency_bullet(list(concurrent_dispatches)),
        # KYO-143, the sixth slot of the same family — and the only one that
        # describes the tree the worker is standing in rather than a declaration
        # about the work. Placed last in the header because it is what the worker
        # acts on first.
        _worktree_tooling_bullet(worktree, list(worktree_setup)),
        "",
        "## Record summary",
        "",
        record["description"] or "(no description)",
        "",
        f"**Status**: {record['status']}{gate_line}",
        f"**Plan membership**: {record['plan'] or '(none)'}",
        "",
        "## Criteria",
        "",
        _criteria_table(record["criteria"]),
        _covers_block(covered_list),
        "## Touches",
        "",
        touches_block,
        # KYO-151.c1, placed here because it is *about* a path in the table above and
        # about the write scope stated below it: the one file a dispatch may edit whose
        # effect does not ship with the merge.
        *_ledger_handoff_block(claimed_paths),
        # KYO-299.c2, placed beside the ledger hand-off for the same reason that one is
        # here: both are about a path in the table above whose ordinary editing rules do
        # not apply to it.
        *_doc_fragments_block(dispatch_id, numbered_documents),
        "## Cited claims and evidence",
        "",
        _claims_table(record["claims"]),
        "## Write scope — your capability boundary, stated mechanically",
        "",
        f"**`write_scope: {kind.policy.get('write_scope', 'none')}`**",
        "",
        WRITE_SCOPE_STATEMENTS.get(kind.policy.get("write_scope", "none"), ""),
        "",
        "## Standing rules (worker-audience)",
        "",
        _worker_rules_block(worker_rules),
        "## Standing stance",
        "",
        STANDING_STANCE,
        "",
        "---",
        "",
        "## Task",
        "",
        controller_task_prose or "(no task-specific instructions given at dispatch time)",
        "",
        "## Report",
        "",
        # KYO-149: this sentence used to send the worker to
        # `templates/report.schema.json` — a source-tree path `pyproject.toml` has
        # never shipped, so in every consumer project that has ever existed it named
        # a file the reader could not open. It cites the command instead, and the
        # command is the one place the schema is read from. `tests/
        # test_kyo149_consumer_paths.py` fails if any path cited in this generated
        # text is one an installed consumer would not have.
        # KYO-206: the paragraph names the PATH. It used to name none, and said "commit
        # your work before you stop" in the same breath — while `kyo check-clean` counts
        # any untracked file, so the only way to satisfy both sentences was to commit the
        # report into the branch. A consumer's first two merges carried `report.json`
        # into main, the third conflicted add/add and the fourth modify/delete, and the
        # controller ended up running `git rm --cached` at every merge. One named path,
        # gitignored by `kyo init`/`kyo install-hooks`, excluded by `check-clean` and
        # never named by touch drift — so "commit your work" no longer means "commit
        # your report".
        "When finished (or when you stop for any reason — including finding the premise "
        "false or the approach wrong), write your structured report per the schema "
        "`kyo report-schema` prints — the same document `kyo report` validates against, "
        f"read straight from the installed package — to **`{WORKER_REPORT_PATH}`** at "
        "the root of your worktree, and note in your final message that it's ready for "
        f"`kyo report {dispatch_id} --file {WORKER_REPORT_PATH}`. Commit your work "
        "before you stop; an uncommitted worktree at stop time is checked and blocked. "
        f"**Do not commit the report itself**: `{WORKER_REPORT_PATH}` is gitignored in "
        "this project, `kyo check-clean` does not count it as uncommitted work, and "
        "`kyo touch-drift` never names it — it is your output, which `kyo report` "
        "ingests and the store keeps, not a file your branch should carry.",
        "",
        # KYO-332: a worker used to close with a 400-to-700-word prose summary
        # restating the structured report a minute before the controller ingested it —
        # paid for twice, and carrying nothing `kyo report` does not already print in a
        # fixed order.
        "Keep your final message to a few lines: the dispatch id, that the report is "
        "written, the branch tip sha, and only what the report cannot carry — say why "
        "in one clause: the report is structured and is about to be read.",
        "",
        # KYO-477.c4/c5: the dry run and a filled example, beside the key sets, so a
        # worker can check its report where it stands instead of by hand.
        *_report_example_block(dispatch_id, record["id"], worked_from_sha),
        *_report_key_sets_block(),
        # KYO-199.c5. Generated rather than typed into a task prose block per dispatch,
        # for the reason KYO-90's paragraph above gives about itself: an instruction only
        # some briefs carry is an instruction most workers never receive. `docs_verdicts[]`
        # is named beside `criteria_verdicts[]` because the two are the same act — a
        # verdict the worker proposes and the controller applies — and the preview command
        # is named in the same breath because it is what makes the reading affordable.
        "Beside `criteria_verdicts[]`, your report may carry **`docs_verdicts[]`**: one "
        "entry per governed document section your branch's diff raises an obligation "
        "against, each with the document path, the section heading verbatim, a verdict "
        "(`updated` if you changed the document to match this work, `not-affected` if you "
        "read it and it needs no change), the method you decided by, and a one-line "
        "reason. Run `kyo docs preview --against main` in your worktree to see exactly "
        "which sections those are, grouped per document — it opens no store, mints no "
        "obligation id and writes nothing, so it is safe to run from here. Read those "
        "sections and answer each one. `kyo report` matches every entry to the obligation "
        "it raised and names any entry that matched none; nothing you write settles "
        "anything, because your orchestrator settles and you propose.",
        "",
        *_recx_trailer_block(recx_manifest),
        # KYO-90. Generated, not controller-typed: every brief dispatched before this
        # existed carried a hand-written sentence telling the worker to get its report
        # right the first time, because a corrected one could not be ingested at all.
        # It can be now, and the worker is the only party who can start that — so the
        # brief is where it has to be said.
        "If you are resumed on this dispatch after your report has already been "
        "ingested, do not start a second one and do not leave the correction in prose: "
        'update your report file and add `"amends": "<the report id the orchestrator '
        'ingested>"` at its top level. It is ingested as the current report, the '
        "earlier one stays readable and marked superseded, and the checks `kyo report` "
        "runs — every commit reachable from the declared branch, touch drift against "
        "`touched_files[]` — run again over the corrected document.",
        "",
    ]
    return "\n".join(parts)


__all__ = [
    "SHARED_RESOURCES_LINE",
    "STANDING_STANCE",
    "TEST_HISTORY_LABEL",
    "TOUCH_SOURCE_COVERED",
    "TOUCH_SOURCE_PRIMARY",
    "TOUCH_SYMBOL_LINE_NOTE",
    "WORKER_RULES_FULL_TEXT_NOTE",
    "WORKER_RULE_FULL_TEXT_COMMAND",
    "WRITE_SCOPE_STATEMENTS",
    "enforced_by_summary",
    "generate_brief",
    "touch_paths_named_in",
]
