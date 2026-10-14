"""``kyo accept`` — apply a report you have already read, as one act (KYO-331).

Accepting a worker's report was the same seven commands every time: ``kyo report``,
``kyo verify --from-report``, ``kyo docs settle
--from-report``, ``kyo edit --touches-from-drift``, a ``kyo gate`` for each
human-observed criterion, ``kyo move verified``, ``kyo move done``. The judgment in
that sequence is reading ``premise_false[]`` and ``disagreements[]``; everything
after "I accept this" is courier work, and a step is easy to drop — KYO-263's
doc-ref obligation and KYO-314's drift correction each needed a second pass.

**A composition, not a second implementation.** Every step below is the command a
controller types today, reached through :func:`kyosei.cli.build_parser` and the same
handler ``cli.main`` would have called, with the argv this command prints beside the
step. Nothing here re-derives what a verdict means, which obligations a report
matched, or which touches a branch drifted from: a refusal those commands make is
made here, in their words, and this command applies exactly what they apply — which
is what makes KYO-331.c3 true by construction rather than by a second set of tests
agreeing with the first.

**Not one transaction, and this is the honest version of why.**
:meth:`kyosei.store.Store.transaction` opens ``BEGIN IMMEDIATE``, which SQLite does
not nest, and each composed command opens its own. Wrapping them would mean
inlining four write paths here — the reimplementation this record rules out. So the
steps commit one at a time, and a refusal in a later step leaves the earlier ones
applied. **A second run is safe**, which is what makes that acceptable:

* ``kyo verify --from-report`` validates every entry before it writes any of them,
  so it applies all of its batch or none of it — and it skips, and lists as already
  applied, every entry whose criterion's latest verification already came from this
  report (KYO-415), so a second run records no verification and no evidence twice;
  and it skips, and lists as a newer verdict kept, every entry whose criterion's
  latest verification was recorded after the report was ingested (KYO-511), so a
  rerun never puts the report's older verdict back over the controller's newer one.
* ``kyo docs settle --from-report`` names an obligation that already carries a
  verdict and settles nobody over it (law 9: a verdict is never overwritten).
* ``kyo edit --touches-from-drift`` REPLACES the touches list with the correction,
  so applying it twice stores the same lines.
* ``kyo move`` onto the status a record already holds is a no-op that says so
  (KYO-173), so a ladder walk that stopped half way resumes where it stopped.

The drift correction is the one step this command has ever needed to make its own
call about, and it makes two of them now. A report carrying no ``commits[]`` and no
``touched_files[]`` gives it nothing to compare at all — not evidence the branch
answers and not evidence the report answers — so that case is not run and tolerated,
it is not run at all: the step is printed as skipped, with the reason, and the
remaining steps still run (KYO-353). ``kyo report``'s own branch check already has
the words for a report shaped this way (``report_ingest.BRANCH_CHECK_NO_COMMITS``:
"nothing to check ... this report carries no commits"), so this reuses that
distinction rather than inventing a second one. Anything short of that — a
``touched_files[]`` the drift can be computed from, or KYO-344's ``commits[]``
fallback for a branch that is gone — is run for real, and *that* is the case whose
refusal is caught: the existing command refuses with ``touch-drift-apply-needs-a-
finding`` when neither can actually answer (the controller met this on KYO-314).
That refusal is a fact about the dispatch rather than a failure of the acceptance,
so it too is printed as a line in the summary rather than raised, and the remaining
steps still run.

**Partial is said, not inferred** (KYO-415). A tolerated refusal, or a record
``--close`` could not finish, leaves the command exiting 0 as it always has — that
default was kept — so ``--json`` carries ``complete``
(false when any step was refused or, under ``--close``, any covered record was left
short of completion) and the text carries an ``INCOMPLETE`` line under the headline
naming both. A script keyed on the exit status alone reads a partial acceptance as a
whole one; that is why the field exists.

**What stays the controller's.** ``--reviewed`` and ``--close`` are both opt-in and
neither is ever defaulted. Reading a worker's ``premise_false[]`` and
``disagreements[]`` before applying anything is the review order ``procedures.md``
requires, so this command refuses until ``--reviewed`` says it happened; and no gate
is ever written, because a gate carries an instruction to a person and a tool that
minted one out of a criterion's reason would be putting words in somebody's mouth
(KYO-267).
"""

from __future__ import annotations

import json
import shlex
from argparse import Namespace
from dataclasses import dataclass, field

from . import (
    cmd_evidence,
    cmd_next,
    cmd_records,
    cmd_views,
    commands,
    docs_numbered,
    explanations,
)
from .commands import (
    Context,
    Output,
    _criterion_views,
    _load_registry,
    _load_status_ladder,
    _open_docs_obligation_views,
)
from .contracts import (
    ROLE_RETIRED,
    StatusLadder,
    check_archetype_allows_status_flow,
    check_transition,
)
from .errors import ContractViolation, KyoseiError
from .git_queries import run_git
from .report_ingest import verdict_counts_text

#: KYO-331.c2. A report whose worker said a premise was false, or that they disagree
#: with the approach, is not one to apply on the strength of having been ingested.
#: Its own contract name rather than a bare error, because the refusal is about the
#: ORDER of two controller acts — read, then apply — which is the shape every other
#: transition contract here has.
CONTRACT_ACCEPT_NEEDS_REVIEW = "accept-needs-review"

#: KYO-343.c2. ``--except`` names a criterion to hold back, not one to select in — but
#: an id the report carries no verdict for is a typo or a stale id either way, and
#: applying the complement of a name the report never had would silently apply
#: everything. Its own contract, worded around ``--except`` rather than reusing
#: ``kyo verify --only``'s (:data:`cmd_evidence.CONTRACT_VERIFY_ONLY_NAMES_AN_ABSENT_CRITERION`):
#: the two refusals answer different flags and a controller reading one must not have
#: to guess which command it came from.
CONTRACT_ACCEPT_EXCEPT_NAMES_AN_ABSENT_CRITERION = "accept-except-names-an-absent-criterion"

#: KYO-353. Not a :class:`~kyosei.errors.ContractViolation` name — nothing here is
#: refused — but the same kind of label, for the same reason: a controller reading
#: `--json` needs a stable string to key on rather than parsing prose out of a line.
#: Set on the touches step whenever a report's ``commits[]`` and ``touched_files[]``
#: are BOTH empty, which is not a refusal by any composed command because the step is
#: never run to be refused — see the module docstring.
ACCEPT_TOUCHES_NOTHING_TO_COMPARE = "accept-touches-nothing-to-compare"


#: KYO-540.c1. The dispatch's branch has a commit past the last one the report lists.
#: Three workers stopped with background work still running and the
#: orchestrator compared the branch tip to the report by hand each time; accepting such a
#: report applies an account of a branch that no longer matches it, and `kyo accept`'s
#: touches-from-drift would then be computed over commits the worker never described.
CONTRACT_ACCEPT_BRANCH_MOVED_PAST_REPORT = "accept-branch-moved-past-report"


def _resolve(root, rev: str) -> str | None:
    try:
        result = run_git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    except OSError:
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def _refuse_if_branch_moved(ctx: Context, dispatch, report_id: str, body: dict) -> None:
    """KYO-540.c1: refuse when the branch tip is not a commit the report lists.

    KYO-544: the question is whether the tip is among ``commits[]`` (each resolved), not
    whether it is ``commits[-1]``: a complete report listed newest-first ends on its
    oldest commit, and comparing the tip with that refused a branch that never moved.
    ``kyo report`` warns about the order (``branch-commits-out-of-order``); this check
    does not depend on it.

    Compared only when it can be: no declared branch, a report with no ``commits[]``
    (``kyo report``'s branch check already says what that means), or a branch this
    clone cannot resolve (a merged-and-deleted branch) leaves nothing to compare, and
    refusing then would block every accept after a branch is cleaned up; nor is it
    compared when no reported sha resolves here. The remedy is the amending report
    KYO-90 built for exactly this: the worker (or the orchestrator with ``git log``)
    adds the missing commit(s) and files it with ``amends``.
    """
    branch = dispatch["branch"]
    commits = body.get("commits") or []
    if not branch or not commits:
        return
    tip = _resolve(ctx.code_root, branch)
    if tip is None:
        return
    resolved = [_resolve(ctx.code_root, c["sha"]) for c in commits]
    if all(r is None for r in resolved) or tip in resolved:
        return
    listed = ", ".join(c["sha"] for c in commits)
    raise ContractViolation(
        CONTRACT_ACCEPT_BRANCH_MOVED_PAST_REPORT,
        [
            f"branch {branch} is at {tip}, which is not among the {len(commits)} "
            f"commit(s) in report {report_id}'s commits[] ({listed}) — the branch "
            "moved after the report was written (a background task that kept "
            "committing, or a resumed worker), so the report does not describe what "
            "would be merged",
            "nothing was applied. Have the worker file an amending report: add the "
            f"missing commit(s), oldest first, end commits[] on {tip}, set "
            f"\"amends\": \"{report_id}\" and ingest it against this dispatch; then "
            "accept again",
        ],
    )


@dataclass
class _Step:
    """One composed command: what was run, and what it said.

    ``argv`` is kept so the summary can print the command a controller would have
    typed. That is not decoration: it is what makes the act auditable against the
    seven-command sequence it replaces, and what a reader re-runs by hand when they
    want one step on its own.
    """

    argv: list[str]
    lines: list[str] = field(default_factory=list)
    data: dict | None = None
    refused: str | None = None
    #: KYO-353. Set instead of ``refused`` — never both — when the step was not run
    #: at all because there was nothing for it to compare (see
    #: :data:`ACCEPT_TOUCHES_NOTHING_TO_COMPARE`); ``None`` for a step that ran.
    skipped: str | None = None

    @property
    def command(self) -> str:
        return "kyo " + " ".join(self.argv)


def _parse(argv: list[str]) -> Namespace:
    """The Namespace ``cli.main`` would have built for ``argv``.

    Built by the shipped parser rather than by hand-filling a Namespace, and that is
    load-bearing twice over: a handler reads flags this command has never heard of
    (``kyo edit`` alone reads fourteen), so a hand-built Namespace would raise
    ``AttributeError`` the day one is added; and a default written here could drift
    from the parser's, which would make this command quietly do something the typed
    command does not.
    """
    from . import cli  # local: `cli` imports this module to put it in COMMAND_TABLE

    return cli.build_parser().parse_args(argv)


def _run(ctx: Context, argv: list[str], handler, *, tolerate: tuple[type, ...] = ()) -> _Step:
    """Run one composed command, capturing its own output verbatim.

    ``tolerate`` is empty for every step but the drift correction: a refusal is the
    composed command's to make and this one has no standing to swallow it.
    """
    step = _Step(argv=list(argv))
    try:
        output = handler(ctx, _parse(argv))
    except tolerate as refusal:  # type: ignore[misc]
        step.refused = str(refusal)
        step.lines = str(refusal).splitlines()
        return step
    step.lines = list(output.lines)
    step.data = output.data
    return step


def _step_text_lines(step: _Step, *, summarised: set[str]) -> list[str]:
    """A step's lines as this command's TEXT prints them (KYO-488).

    The verify step prints the unobserved-criteria gate remedy, and this command's
    summary prints the same remedy for every covered record. When the summary prints
    it for every record the step printed it for (``summarised``: the ids it names), the
    step's copy is left out of the text, so the remedy prints once; otherwise the step
    keeps its lines, and nothing is lost. The lines left out are the ones
    :func:`cmd_evidence.gate_prompt_lines` renders from the step's own
    ``ungated_unobserved`` — the function that printed them, not a second spelling of
    them. ``--json`` carries ``step.lines`` whole.
    """
    prompts = (step.data or {}).get("ungated_unobserved")
    if (
        not prompts
        or step.argv[:1] != ["verify"]
        or not {found["id"] for found in prompts} <= summarised
    ):
        return step.lines
    block = cmd_evidence.gate_prompt_lines(list(prompts))
    lines = list(step.lines)
    for at in range(len(lines) - len(block) + 1):
        if lines[at : at + len(block)] == block:
            return lines[:at] + lines[at + len(block) :]
    return lines


def _reviewed_entries(body: dict) -> tuple[list[dict], list[dict]]:
    return list(body.get("premise_false") or []), list(body.get("disagreements") or [])


def _premise_false_lines(entries: list[dict]) -> list[str]:
    """Each ``premise_false[]`` entry, whole. Law 5: printed as the worker wrote it."""
    lines: list[str] = []
    for n, entry in enumerate(entries, start=1):
        lines.append(f"premise_false[{n}] claim: {entry.get('claim', '')}")
        lines.append(f"  correction: {entry.get('correction', '')}")
        lines.append(f"  impact: {entry.get('impact', '')}")
    return lines


def _disagreement_lines(entries: list[dict]) -> list[str]:
    lines: list[str] = []
    for n, entry in enumerate(entries, start=1):
        lines.append(f"disagreements[{n}] topic: {entry.get('topic', '')}")
        lines.append(f"  the brief said: {entry.get('brief_said', '')}")
        lines.append(f"  the worker did: {entry.get('worker_did', '')}")
        lines.append(f"  rationale: {entry.get('rationale', '')}")
    return lines


def _refuse_until_reviewed(
    dispatch_id: str,
    report_id: str,
    premise_false: list[dict],
    disagreements: list[dict],
    *,
    reviewed: bool,
) -> None:
    """KYO-331.c2, and the whole reason this command has a flag at all.

    The standing stance a worker writes under is that contradicting the brief must be
    cheaper than complying with it, which is worth nothing if the entries they wrote
    are applied past. So the refusal PRINTS them — every field of every entry, in the
    worker's own words — and then stops. ``--reviewed`` is a statement by the
    controller that they have read what is above it; nothing verifies that, and
    nothing could, which is exactly why the tool must not supply it by default.
    """
    if reviewed or not (premise_false or disagreements):
        return
    raise ContractViolation(
        CONTRACT_ACCEPT_NEEDS_REVIEW,
        [
            f"report {report_id} (dispatch {dispatch_id}) carries "
            f"{len(premise_false)} premise_false and {len(disagreements)} "
            f"disagreements entry(s), and reading them is the act this command "
            f"cannot do for you",
            *_premise_false_lines(premise_false),
            *_disagreement_lines(disagreements),
            "nothing was applied. Read the entries above; then pass --reviewed to say "
            "you have, and this runs every step",
        ],
    )


def _review_line(premise_false: list[dict], disagreements: list[dict]) -> str:
    """What the review step did, said once at the top.

    Reached with entries present only after :func:`_refuse_until_reviewed` let the run
    through, which is the only way ``--reviewed`` can be true here — so this says what
    the flag covered rather than whether it was passed.
    """
    if not (premise_false or disagreements):
        return "review: the report carries no premise_false and no disagreements entry"
    return (
        f"review: --reviewed, over {len(premise_false)} premise_false and "
        f"{len(disagreements)} disagreements entry(s) — "
        f"{explanations.say('review-entries-unsettled')}, readable in `kyo show`"
    )


# --------------------------------------------------------------- KYO-343: --except


def _except_complement(
    report_id: str, verdicts: list[dict], except_ids: list[str]
) -> list[str]:
    """The ids ``kyo verify --from-report --only`` should apply: every criterion the
    report carries a verdict for, minus what ``--except`` named (KYO-343.c1).

    Refuses first — before anything is written, naming the ids the report DOES carry
    — on the shape ``kyo verify --only`` already refuses an absent name with
    (:data:`cmd_evidence.CONTRACT_VERIFY_ONLY_NAMES_AN_ABSENT_CRITERION`), because a
    typo in ``--except`` is exactly as silent a way to apply everything as a typo in
    ``--only`` is to apply nothing (KYO-343.c2).

    Order is the report's own — the order its ``criteria_verdicts[]`` already lists —
    so the ``--only`` this hands to the composed command reads the same whichever ids
    were named, and so does the excepted list printed beside it.
    """
    present = [entry["criterion_id"] for entry in verdicts]
    present_set = set(present)
    absent = [criterion_id for criterion_id in except_ids if criterion_id not in present_set]
    if absent:
        raise ContractViolation(
            CONTRACT_ACCEPT_EXCEPT_NAMES_AN_ABSENT_CRITERION,
            [
                f"--except names {criterion_id}, which report {report_id} carries no "
                f"verdict for (it carries: {', '.join(sorted(present_set)) or '(none)'})"
                for criterion_id in absent
            ]
            + [
                f"nothing was applied. Drop {', '.join(absent)} from --except, or check "
                f"{report_id}'s criteria_verdicts for the ids it actually carries"
            ],
        )
    except_set = set(except_ids)
    return [criterion_id for criterion_id in present if criterion_id not in except_set]


def _except_gate_remedy(record_id: str, entries: list[dict]) -> str:
    """The ``kyo gate ... awaiting:event`` line for a record carrying an excepted verdict
    (KYO-343.c4) — never run, only printed, on
    :func:`cmd_next.ungated_unobserved_remedy`'s own reasoning: a gate carries an
    instruction to a person, and a tool that minted one out of a held-back verdict
    would be putting words in somebody's mouth. ``awaiting:event`` rather than
    ``holding-for``: what an excepted criterion is typically waiting on is another
    record landing (a consumer's case, APP-58.c1), and a hold until a merge
    lands is an event (KYO-210). Clearing a ``holding-for`` gate raises
    ``gate-cleared-stale-criteria`` against every criterion older than the clearing
    (KYO-59) — here, the ones this same acceptance just settled.
    """
    payload = "; ".join(
        " ".join(
            f"{entry['criterion_id']}: {entry['verdict']} proposed, held back by "
            "--except".split()
        )
        for entry in entries
    )
    quoted = shlex.quote(payload) if ('"' in payload or "\\" in payload) else f'"{payload}"'
    return f"kyo gate {record_id} awaiting:event --do {quoted}"


# ------------------------------------------------------------------ KYO-331.c5: --close


def _rungs_to_completion(ladder: StatusLadder, current: str) -> list[str]:
    """The statuses between ``current`` (exclusive) and completion (inclusive).

    Read off the ladder's declared order with the retired role removed — the same
    derivation :attr:`StatusLadder.flow` prints as ``open → working → built →
    verified → done`` — so a project that renamed its rungs, or declared five of
    them, is walked under its own names. No status name appears in this module.

    Nothing in :func:`contracts.check_transition` requires the intermediate rungs: a
    single move from ``open`` to ``done`` is permitted, and a controller does not make
    one. The rungs are the record's own history of having been built and then
    verified, and each is an events row somebody can read afterwards, so walking them
    is what "the way a controller does it by hand" means here.

    A status the registry no longer declares is not a rung of anything, so the walk
    is the completion status alone rather than a guess about where the record stands.
    """
    rungs = [name for name in ladder.names if ladder.role(name) != ROLE_RETIRED]
    if current not in rungs or ladder.complete not in rungs:  # pragma: no cover - roles
        return [ladder.complete]
    return rungs[rungs.index(current) + 1 : rungs.index(ladder.complete) + 1]


def _close_blocker(store, record_id: str, ladder: StatusLadder) -> list[str] | None:
    """Why ``record_id`` cannot reach completion, in the contracts' own words.

    The predicate is the contract itself, called with this record's criteria and open
    obligations exactly as ``kyo move`` calls it — not a second reading of "all
    criteria settled" that could come to disagree with the refusal a controller would
    actually meet. Asked before the walk starts so a record that cannot finish is not
    moved part way up the ladder on its way to being told so.
    """
    row = store.record(record_id)
    kind_def = _load_registry(store).require(row["kind"])
    try:
        check_archetype_allows_status_flow(record_id, row["kind"], kind_def.archetype, ladder)
        check_transition(
            record_id=record_id,
            kind=row["kind"],
            from_status=row["status"],
            to_status=ladder.complete,
            criteria=_criterion_views(store, record_id),
            reason=None,
            requires_criteria=kind_def.requires_criteria,
            ladder=ladder,
            open_docs_obligations=_open_docs_obligation_views(store, record_id),
            # KYO-490: the same open-gate refusal `kyo move` meets, read here so a
            # gated record is left at its status rather than walked part way up.
            open_gates=cmd_records._open_gate_views(store, record_id),
        )
    except ContractViolation as refusal:
        return str(refusal).splitlines()
    return None


def _unmatched_on_open_documents(
    store, record_id: str, unmatched: list[dict]
) -> list[dict]:
    """The unmatched docs verdicts that name a document ``record_id`` still has an open
    obligation on (KYO-393.c4).

    A refusal over open docs obligations used to be the whole story the controller got,
    when ``kyo accept``'s own settle step had just printed the verdicts that were meant
    to settle them — sixty lines earlier, as "unmatched". Which unmatched entry was meant
    for which open obligation is exactly what the matcher could not decide, so this does
    not decide it either: it names every unmatched entry on a document that still has an
    open obligation, and the controller reads the pair.
    """
    open_documents = {
        row["doc_path"] for row in store.open_docs_obligations(record_id)
    }
    # A duplicate named a section another entry already answered, so it cannot be the
    # answer to an obligation that is still open.
    return [
        row
        for row in unmatched
        if row["doc_path"] in open_documents
        and row["why"] == docs_numbered.VERDICT_UNMATCHED_NO_OBLIGATION
    ]


def _close(
    ctx: Context, store, covered: list[str], unmatched: list[dict] | None = None
) -> tuple[list[_Step], list[dict], list[dict]]:
    """``--close``: walk each covered record it can to its completion status.

    Returns the steps run, the records closed and the records left with the reason
    each was left — law 6, which is the half of this that matters: a close that
    silently skipped two of five records would look exactly like one that closed all
    five if only the closures were printed.

    ``unmatched`` is the settle step's own unmatched docs verdicts (KYO-393.c4): a record
    left over open docs obligations names those on the same documents beside the
    contract's refusal, so the refusal and the verdicts that might have answered it are
    read together.
    """
    ladder = _load_status_ladder(store)
    steps: list[_Step] = []
    closed: list[dict] = []
    left: list[dict] = []
    for record_id in covered:
        was = store.record(record_id)["status"]
        if was == ladder.complete:
            left.append(
                {
                    "record_id": record_id,
                    "status": was,
                    "why": [f"already {ladder.complete}"],
                }
            )
            continue
        blocker = _close_blocker(store, record_id, ladder)
        if blocker is not None:
            row = {"record_id": record_id, "status": was, "why": blocker}
            near = _unmatched_on_open_documents(store, record_id, list(unmatched or ()))
            if near:
                row["unmatched_docs_verdicts"] = near
                row["why"] = [
                    *blocker,
                    f"{len(near)} docs verdict(s) in this report matched no obligation "
                    "on these documents and were not applied — they may be the answers "
                    "to the open obligations above:",
                    *(f"  {entry['line']}" for entry in near),
                ]
            left.append(row)
            continue
        walked: list[str] = []
        stopped: list[str] | None = None
        for rung in _rungs_to_completion(ladder, was):
            step = _run(
                ctx, ["move", record_id, rung], cmd_records.cmd_move, tolerate=(ContractViolation,)
            )
            steps.append(step)
            if step.refused is not None:
                # The pre-check passed and a rung refused anyway — a plan closing over
                # open members (KYO-308) is the live case. Reported rather than raised:
                # the other covered records' closures are not this one's to abort.
                stopped = step.lines
                break
            walked.append(rung)
        now = store.record(record_id)["status"]
        if stopped is None:
            closed.append({"record_id": record_id, "was": was, "status": now, "walked": walked})
        else:
            left.append({"record_id": record_id, "status": now, "why": stopped})
    return steps, closed, left


# ------------------------------------------------------------- KYO-353: the touches step


def _touches_step(
    ctx: Context, record_id: str, report_id: str, dispatch_id: str, body: dict
) -> _Step:
    """Step 3, ``kyo edit --touches-from-drift`` — or the reason it was not run.

    A report carrying no ``commits[]`` and no ``touched_files[]`` gives the drift
    correction nothing to compare against, which is a fact about the report itself —
    knowable here, before the composed command is even asked, and true independent of
    whatever the branch or the git history can or cannot answer. So this does not run
    the step and tolerate its refusal (KYO-353.c1): a step never run cannot be
    REFUSED, and printing it that way would be exactly the "nothing to diff" read as
    a refusal the record was filed to stop. ``kyo report``'s own branch check already
    has the words for a report shaped this way
    (:data:`report_ingest.BRANCH_CHECK_NO_COMMITS`: "nothing to check ... this report
    carries no commits") — reused here as :data:`ACCEPT_TOUCHES_NOTHING_TO_COMPARE`
    rather than invented a second time.

    Anything short of both being empty — a ``touched_files[]`` the drift can be
    computed from, or KYO-344's ``commits[]`` fallback for a branch that is gone — is
    run for real, and a refusal from THAT run is still the composed command's own to
    make and still tolerated and printed as ``REFUSED`` (KYO-353.c2): the difference
    from before is only that the step which had nothing to run on no longer reaches
    that code path at all.
    """
    argv = ["edit", record_id, "--touches-from-drift", dispatch_id]
    reported_commits = body.get("commits") or []
    reported_touched_files = body.get("touched_files") or []
    if not reported_commits and not reported_touched_files:
        return _Step(
            argv=argv,
            skipped=ACCEPT_TOUCHES_NOTHING_TO_COMPARE,
            lines=[
                f"nothing to compare — report {report_id} carries no commits[] and no "
                "touched_files[], so `kyo edit --touches-from-drift` was not run"
            ],
        )
    return _run(ctx, argv, cmd_records.cmd_edit, tolerate=(KyoseiError,))


# ------------------------------------------------------------- KYO-415: completeness


def _incompleteness(
    store, steps: list[_Step], left: list[dict], *, close: bool
) -> tuple[list[_Step], list[dict]]:
    """What stops this acceptance being complete: the steps a composed command refused,
    and — under ``--close`` only — the covered records it left short of completion.

    Three things deliberately do not count. A step skipped for nothing to compare
    (KYO-353) was never run, so nothing was refused. A record ``--close`` found already
    at its completion status is left where it is because it is finished, which is what
    a second run of ``--close`` meets on every record (the rerun this module promises is
    safe). And without ``--close`` no record was asked to move, so none was left open.

    The exit status is unchanged by this (KYO-415.c3: that plan changes no default
    behaviour); ``complete`` in ``--json`` and the ``INCOMPLETE``
    line in the text are the signal.
    """
    refused = [step for step in steps if step.refused]
    if not close:
        return refused, []
    complete_status = _load_status_ladder(store).complete
    return refused, [row for row in left if row["status"] != complete_status]


def _incomplete_line(
    refused: list[_Step], still_open: list[dict], *, also: list[str] | None = None
) -> str:
    """KYO-415.c2: one line, near the top, naming what did not land.

    ``also`` is for a composing command with a reason of its own to be incomplete —
    ``kyo confirm``'s in-scope gate left unanswered (KYO-494) — so the line keeps one
    shape whichever command prints it.
    """
    parts: list[str] = []
    if refused:
        parts.append(
            f"{len(refused)} step(s) refused ({'; '.join(step.command for step in refused)})"
        )
    if still_open:
        parts.append(
            f"{len(still_open)} covered record(s) left open ("
            + ", ".join(f"{row['record_id']} at {row['status']}" for row in still_open)
            + ")"
        )
    parts += list(also or ())
    return (
        "INCOMPLETE: "
        + " and ".join(parts)
        + " — the steps that ran stayed applied, and each reason is printed below"
    )


# ---------------------------------------------------------------------- the command


def cmd_accept(ctx: Context, args: Namespace) -> Output:
    """``kyo accept <dispatch-id> [--reviewed] [--close] [--except CRITERION_ID ...]``
    — KYO-331, KYO-343.

    The module docstring says what this is and why it is a composition; this is the
    order and what each step is for.

    1. ``kyo verify --from-report`` — the report's ``criteria_verdicts[]``, with the
       method and evidence the worker recorded, attributed to whoever runs this.
       ``--except`` narrows this one step: the complement of the named ids goes to
       ``kyo verify --from-report``'s own ``--only``, so the excepted verdicts are
       never selected rather than applied and reverted (KYO-343.c1).
    2. ``kyo docs settle --from-report`` — the report's ``docs_verdicts[]``, matched
       to the obligations this dispatch's diff raised.
    3. ``kyo edit --touches-from-drift`` — the record's touches, corrected to what
       the branch actually changed. Skipped, not run, when the report carries no
       ``commits[]`` and no ``touched_files[]`` — see :func:`_touches_step`.
    4. ``--close`` only: each covered record that can finish, walked up the ladder.
       A record holding an excepted criterion needs no special case here: the
       criterion was never selected in step 1, so it stays unsettled, and the
       ladder's own ``complete-requires-settled-criteria`` refusal leaves the record
       where it is and names that criterion as the reason (KYO-343.c3) — the same
       refusal :func:`_close_blocker` already reads for every other unsettled
       criterion.
    5. ``--close`` only, when it moved a record to completion and the project declares
       ``close_wave: generate:``: the declared documents regenerated and committed with
       the store, as ``kyo confirm`` does (KYO-603, KYO-577) —
       :func:`cmd_close_wave.refresh_after_completion`. When it cannot run, the
       acceptance stays applied, is reported incomplete, and names the line that
       finishes it.

    Between 3 and 4 it prints the ``kyo gate`` line for every covered record left
    with an unobserved criterion that nothing makes findable, and writes no gate
    (KYO-331.c4). The line comes from :func:`cmd_next.ungated_unobserved_remedy`,
    which is where ``kyo verify``, ``kyo brief``, ``kyo next`` and ``kyo doctor``
    already get theirs, so this cannot come to suggest a different gate from the one
    the board asks for. A record whose wait is already gated gets no line: the gate
    is what "findable" means, and a second one would be the duplicate that predicate
    exists to prevent. ``--except`` gets its own such line, for every record holding
    an excepted criterion, from :func:`_except_gate_remedy` (KYO-343.c4) — printed
    whether or not the record is also in the unobserved class, because the two waits
    are different facts about the record even when they share a criterion.
    """
    store = ctx.store()
    dispatch_id = args.dispatch_id
    dispatch = store.dispatch(dispatch_id)
    record_id = dispatch["record_id"]
    # The refusal for a dispatch with no ingested report is `kyo verify
    # --from-report`'s own (`verify-from-report-needs-a-report`), raised from the same
    # function that raises it there — one refusal, one wording, and it already names
    # `kyo report` as the remedy.
    report_id, verdicts = cmd_evidence._report_verdicts(store, dispatch_id)
    body = json.loads(store.report(report_id)["body_json"])
    premise_false, disagreements = _reviewed_entries(body)
    _refuse_until_reviewed(
        dispatch_id, report_id, premise_false, disagreements, reviewed=bool(args.reviewed)
    )

    _refuse_if_branch_moved(ctx, dispatch, report_id, body)

    except_ids = list(getattr(args, "except_ids", None) or [])
    only = _except_complement(report_id, verdicts, except_ids) if except_ids else None
    excepted = (
        [entry for entry in verdicts if entry["criterion_id"] in set(except_ids)]
        if except_ids
        else []
    )

    covered = [record_id, *commands.dispatch_covers(dispatch)]
    steps: list[_Step] = []
    # `--only` needs at least one id; an `--except` that names every reported
    # criterion leaves nothing to select, and the honest act is to run no verify step
    # at all rather than pass `--only` nothing or fall back to applying everything.
    if only is None or only:
        verify_argv = ["verify", "--from-report", dispatch_id]
        if only is not None:
            verify_argv += ["--only", *only]
        steps.append(_run(ctx, verify_argv, cmd_evidence.cmd_verify))
    settle = _run(ctx, ["docs", "settle", "--from-report", dispatch_id], cmd_views.cmd_docs)
    steps.append(settle)
    # KYO-393: what the settle step could not match, read from its own payload rather
    # than re-matched here — one matcher, one answer.
    unmatched_docs = list((settle.data or {}).get("unmatched") or [])
    steps.append(_touches_step(ctx, record_id, report_id, dispatch_id, body))
    waiting = [
        found
        for found in (cmd_next.ungated_unobserved(store, rid) for rid in covered)
        if found is not None
    ]
    except_by_record: dict[str, list[dict]] = {}
    excepted_record_of: dict[str, str] = {}
    for entry in excepted:
        criterion = store.criterion(entry["criterion_id"])
        excepted_record_of[entry["criterion_id"]] = criterion["record_id"]
        except_by_record.setdefault(criterion["record_id"], []).append(entry)
    closed: list[dict] = []
    left: list[dict] = []
    refresh: dict | None = None
    refresh_not_done = False
    if args.close:
        close_steps, closed, left = _close(ctx, store, covered, unmatched_docs)
        steps += close_steps
        # KYO-603: a completion outside a wave regenerates the declared documents, once
        # for every record this acceptance closed. Local import: `cmd_close_wave` imports
        # this module.
        finished = [row["record_id"] for row in closed]
        if finished:
            from . import cmd_close_wave

            refresh = cmd_close_wave.refresh_after_completion(
                ctx, finished, by="kyo accept --close"
            )
            if refresh["status"] == cmd_close_wave.REFRESH_NONE_DECLARED:
                refresh = None
            else:
                refresh_not_done = refresh["status"] in (
                    cmd_close_wave.REFRESH_SKIPPED,
                    cmd_close_wave.REFRESH_FAILED,
                )

    refused_steps, still_open = _incompleteness(store, steps, left, close=bool(args.close))
    complete = not refused_steps and not still_open and not refresh_not_done

    open_obligations = sum(len(store.open_docs_obligations(rid)) for rid in covered)
    lines = [
        # KYO-540.c2: the summary is the first line, so a filtered read still sees
        # whether the acceptance was complete and what is left to act on.
        f"summary: {'COMPLETE' if complete else 'NOT COMPLETE'} — report {report_id} of "
        f"{dispatch_id}: premise_false {len(premise_false)}, "
        f"disagreements {len(disagreements)}, "
        f"verdicts: {verdict_counts_text(verdicts)}, "
        f"open docs obligations {open_obligations}",
        f"accepted report {report_id} of dispatch {dispatch_id} on {record_id}, as {ctx.actor}"
        + (f" (covering {', '.join(covered)})" if len(covered) > 1 else ""),
    ]
    if not complete:
        lines.append(
            _incomplete_line(
                refused_steps,
                still_open,
                also=(
                    [f"the generated documents were not refreshed ({refresh['status']})"]
                    if refresh_not_done and refresh is not None
                    else []
                ),
            )
        )
    lines.append(_review_line(premise_false, disagreements))
    summarised = {found["id"] for found in waiting}
    for n, step in enumerate(steps, start=1):
        suffix = (
            "  — REFUSED"
            if step.refused
            else "  — skipped, nothing to compare"
            if step.skipped
            else ""
        )
        lines.append(f"[{n}/{len(steps)}] {step.command}" + suffix)
        lines += [f"  {line}" for line in _step_text_lines(step, summarised=summarised)]
    if excepted:
        lines.append(
            f"--except: {len(excepted)} of {len(verdicts)} reported verdict(s) held back — "
            "unapplied and readable in the report:"
        )
        for entry in excepted:
            lines.append(
                f"  {entry['criterion_id']}  proposed {entry['verdict']} "
                f"({entry.get('method', '')})"
            )
        lines.append(
            "this command wrote no gate for them either — a gate carries an "
            "instruction to a person. Gate each record holding one yourself:"
        )
        for held_record, entries in except_by_record.items():
            lines.append(
                f"  {held_record}  " + ", ".join(entry["criterion_id"] for entry in entries)
            )
            lines.append(f"  {_except_gate_remedy(held_record, entries)}")
    if waiting:
        lines.append(
            f"unobserved criteria nothing makes findable ({len(waiting)} record(s)): "
            + explanations.say("accept-wrote-no-gate")
        )
        for found in waiting:
            lines.append(
                f"  {found['id']}  "
                + ", ".join(criterion["id"] for criterion in found["criteria"])
            )
            lines.append(f"  {found['remedy']}")
    if args.close:
        lines.append(f"--close: {len(closed)} of {len(covered)} covered record(s) moved")
        for row in closed:
            lines.append(f"  {row['record_id']}  {row['was']} -> {' -> '.join(row['walked'])}")
        lines.append(f"left ({len(left)}):")
        for row in left:
            lines.append(f"  - {row['record_id']} is {row['status']}:")
            lines += [f"      {line.strip()}" for line in row["why"]]
        if not left:
            lines.append("  (none)")
        if refresh is not None:
            lines += refresh["lines"]
    else:
        lines.append(f"--close was not passed: {explanations.say('accept-not-closed')}")
    if unmatched_docs:
        # KYO-393.c3: LAST, because it is the line the consumer's sixty did not end on.
        # The settle step printed each entry above; this is the count and the command
        # that prints them again (re-running it applies nothing already applied — law 9).
        lines.append(
            f"{len(unmatched_docs)} docs verdict(s) from report {report_id} matched no "
            "obligation and were NOT applied — `kyo docs settle --from-report "
            f"{dispatch_id}` lists them"
        )

    return Output(
        lines,
        {
            "ok": True,
            "command": "accept",
            # KYO-415.c1: additive. `ok` keeps its meaning (the command ran and raised
            # nothing); `complete` says whether everything it set out to apply landed.
            "complete": complete,
            "dispatch_id": dispatch_id,
            "report_id": report_id,
            "record_id": record_id,
            "covers": covered,
            "reviewed": bool(args.reviewed),
            "premise_false": premise_false,
            "disagreements": disagreements,
            "except_ids": except_ids,
            "excepted": [
                {
                    "criterion_id": entry["criterion_id"],
                    "record_id": excepted_record_of[entry["criterion_id"]],
                    "verdict": entry["verdict"],
                    "method": entry.get("method"),
                    "gate_remedy": _except_gate_remedy(
                        excepted_record_of[entry["criterion_id"]],
                        except_by_record[excepted_record_of[entry["criterion_id"]]],
                    ),
                }
                for entry in excepted
            ],
            "steps": [
                {
                    "command": step.command,
                    "argv": step.argv,
                    "refused": step.refused,
                    "skipped": step.skipped,
                    "lines": step.lines,
                    "data": step.data,
                }
                for step in steps
            ],
            "ungated_unobserved": waiting,
            "unmatched_docs_verdicts": unmatched_docs,
            "closed": closed,
            "left_open": left,
            # KYO-603: present only when --close moved a record to completion in a
            # project declaring generate steps, so every other payload is unchanged.
            **(
                {"refresh": {key: value for key, value in refresh.items() if key != "lines"}}
                if refresh is not None
                else {}
            ),
        },
    )
