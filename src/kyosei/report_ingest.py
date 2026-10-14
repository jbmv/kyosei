"""``kyo report`` / ``kyo report-schema`` / ``kyo split`` — ingesting a worker's report."""

from __future__ import annotations

import json
import sqlite3
from argparse import Namespace
from pathlib import Path

from . import commands, docs_history, docs_recx, explanations, export, report_schema, stop_audit
from . import config as config_module
from .clock import now
from .commands import Context, Output, _load_registry, _load_status_ladder, _touch_record
from .contracts import (
    BRANCH_REACH_CANNOT_TELL,
    BRANCH_REACH_OFF_BRANCH,
    BRANCH_REACH_REACHABLE,
    CONTRACT_DISPATCH_ALREADY_TERMINAL,
    CONTRACT_REPORT_AMENDS_WRONG_REPORT,
    CONTRACT_REPORT_COMMIT_OFF_BRANCH,
    CONTRACT_REPORT_ID_MISMATCH,
    CONTRACT_SPLIT_REQUIRES_UNMET_CRITERIA,
    DISPATCH_STATE_REPORTED,
    DISPATCH_STATE_RUNNING,
    REPORT_AMENDS_KEY,
    amend_remedy,
    check_criterion_state,
    report_amends,
)
from .dispatch_docs import _docs_obligation_lines, _raise_docs_obligations
from .errors import ContractViolation, KyoseiError, UsageError
from .git_queries import _git_commit_reachability, _git_is_ancestor
from .render import commit_label, echo_value
from .touch_drift import _drift_for_dispatch, drift_lines

#: KYO-32: the four states of the whole branch check, distinct from the per-commit
#: :data:`contracts.BRANCH_REACH_OUTCOMES`. ``not-declared`` and ``no-commits`` are
#: their own outcomes rather than a shrug: the first says the controller never named
#: a branch (so nothing *could* be checked), the second says the report carried no
#: commits to check. Neither is ``verified``, and neither is a failure.
BRANCH_CHECK_NOT_DECLARED = "not-declared"


BRANCH_CHECK_NO_COMMITS = "no-commits"


BRANCH_CHECK_VERIFIED = "verified"


BRANCH_CHECK_CANNOT_TELL = "cannot-tell"


#: KYO-411: a report with no ``commits[]`` whose declared branch has
#: NO commits past the report's ``worked_from_sha``. Before this it read "nothing to
#: check", which is the same line a worker who deliberately changed nothing gets — and
#: the same line a worker whose commits landed on a detached HEAD gets, after which the
#: controller's ``git merge`` says "Already up to date" and ships nothing.
BRANCH_CHECK_DID_NOT_ADVANCE = "branch-did-not-advance"


#: KYO-411: a report with no ``commits[]`` whose declared branch DID move past
#: ``worked_from_sha``: the branch carries work the report does not account for.
BRANCH_CHECK_UNREPORTED_COMMITS = "unreported-commits"


#: KYO-411: the warning codes the branch check can attach beside its outcome, in
#: ``check["warnings"]``. A warning never refuses the report; it is named so a
#: ``--json`` consumer can tell it apart from a pass without parsing prose.
BRANCH_WARNING_DID_NOT_ADVANCE = "branch-did-not-advance"
BRANCH_WARNING_UNLISTED_COMMITS = "branch-commits-not-in-report"
#: KYO-544: ``commits[]`` is not oldest first ending on the branch tip -- some entry is
#: not an ancestor of the next, or the tip is not listed at all. A consumer project's
#: APP-58.d1 listed four commits newest-first; every one was reachable, so the check passed, and
#: ``kyo accept`` then compared the tip with ``commits[-1]`` (the oldest) and refused.
BRANCH_WARNING_COMMITS_OUT_OF_ORDER = "branch-commits-out-of-order"


def _resolve_commit(root: Path, rev: str) -> str | None:
    """``rev`` as a full commit sha, or ``None`` when git cannot say. Never raises."""
    from .git_queries import run_git

    try:
        result = run_git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    except OSError:
        return None
    return (result.stdout.strip() or None) if result.returncode == 0 else None


def _commits_order_warning(
    root: Path, branch: str, commits: list[dict], unlisted: tuple[str, ...] | list[str] = ()
) -> dict | None:
    """KYO-544: the out-of-order warning for ``commits[]``, or ``None``.

    A tip missing from ``commits[]`` that ``unlisted`` already carries is left to the
    ``branch-commits-not-in-report`` warning (whose text names the order too), so one
    missing commit is not reported twice under two codes.

    ``None`` too when the order cannot be judged -- the branch or any reported sha does
    not resolve here -- because a warning built on a guess is worse than none, and the
    reachability check beside it already names a sha it could not place.
    """
    tip = _resolve_commit(root, branch)
    if tip is None:
        return None
    resolved = [_resolve_commit(root, str(c.get("sha") or "")) for c in commits]
    if any(r is None for r in resolved):
        return None
    problems = []
    for i in range(len(resolved) - 1):
        older, newer = resolved[i], resolved[i + 1]
        if older == newer or _git_is_ancestor(root, older, newer) is not True:
            problems.append(
                f"commits[{i}] {commits[i]['sha']} is not an ancestor of "
                f"commits[{i + 1}] {commits[i + 1]['sha']}"
            )
    if tip not in resolved:
        if tip not in unlisted:
            problems.append(f"the branch tip {tip} is not listed")
    elif resolved[-1] != tip:
        problems.append(f"commits[] does not end on the branch tip {tip}")
    if not problems:
        return None
    return {
        "code": BRANCH_WARNING_COMMITS_OUT_OF_ORDER,
        "shas": [],
        "detail": (
            "; ".join(problems)
            + f". commits[] goes oldest first, each entry an ancestor of the next, and "
            f"ends on the tip of {branch} ({tip}) -- `git log --reverse --format=%H "
            f"<worked_from_sha>..{branch}` prints that order. `kyo accept` refuses a "
            "report whose commits[] does not include the tip"
        ),
    }


def _branch_commits_past(root: Path, base_sha: str, branch: str) -> tuple[list[str] | None, str]:
    """KYO-411: ``git rev-list <base_sha>..<branch>`` — the commits the branch carries
    past the sha the worker says it branched from — or ``None`` with the reason it
    could not be read. Never raises: the branch check must still render an outcome in
    a clone that cannot resolve the refs, and "could not compare" is said, not guessed.
    """
    from .git_queries import run_git

    def run(*git_args: str):
        try:
            return run_git(root, *git_args)
        except OSError:
            return None

    for label, ref in (("worked_from_sha", base_sha), ("branch", branch)):
        probe = run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if probe is None:
            return None, f"git could not be run in {root}"
        if probe.returncode != 0:
            return None, f"{label} {ref!r} does not resolve to a commit in {root}"
    listed = run("rev-list", f"{base_sha}..{branch}")
    if listed is None or listed.returncode != 0:
        stderr = "" if listed is None else listed.stderr.strip()
        return None, f"`git rev-list {base_sha}..{branch}` failed: {stderr}"
    return [line.strip() for line in listed.stdout.splitlines() if line.strip()], ""


def _branch_advance(root: Path, branch: str, base_sha: str | None, commits: list[dict]) -> dict:
    """KYO-411: compare a report's ``commits[]`` against the branch itself.

    Each reported sha being reachable (KYO-32) says nothing about the commits the report
    did NOT list, or about a branch that never moved. This asks git for the whole list
    past ``base_sha`` and returns ``{"worked_from_sha", "outcome", "count",
    "unlisted", "detail"}`` — ``outcome`` one of ``advanced``, ``did-not-advance`` or
    ``cannot-tell``. Reported shas are matched by prefix, so an abbreviated sha in a
    report still accounts for its commit.
    """
    if not base_sha:
        return {
            "worked_from_sha": None,
            "outcome": BRANCH_CHECK_CANNOT_TELL,
            "count": None,
            "unlisted": [],
            "detail": "no worked_from_sha was reported, so the branch had no start to compare from",
        }
    past, why = _branch_commits_past(root, base_sha, branch)
    if past is None:
        return {
            "worked_from_sha": base_sha,
            "outcome": BRANCH_CHECK_CANNOT_TELL,
            "count": None,
            "unlisted": [],
            "detail": why,
        }
    reported = [str(c.get("sha") or "").strip().lower() for c in commits]
    reported = [r for r in reported if r]
    unlisted = [sha for sha in past if not any(sha.startswith(r) for r in reported)]
    return {
        "worked_from_sha": base_sha,
        "outcome": "advanced" if past else "did-not-advance",
        "count": len(past),
        "unlisted": unlisted,
        "detail": f"`git rev-list {base_sha}..{branch}` lists {len(past)} commit(s)",
    }


def _check_report_commits_on_branch(
    root: Path, dispatch, commits: list[dict], worked_from_sha: str | None = None
) -> dict:
    """KYO-32: is every sha in a report's ``commits[]`` reachable from the branch
    its dispatch declared?

    Returns the whole finding as data — ``{"branch", "outcome", "commits": [...]}``
    — so that :func:`cmd_report`'s text output and its ``--json`` payload are two
    renderings of one computation rather than two computations that can disagree
    (law 3). ``outcome`` is never ``verified`` unless every commit came back
    ``reachable``; a single ``cannot-tell`` makes the whole check ``cannot-tell``,
    because a check that could not see one of its inputs did not pass (law 6).

    KYO-411: the reported shas are also compared against the branch as a whole
    (:func:`_branch_advance`, ``git rev-list <worked_from_sha>..<branch>``), carried as
    ``check["advance"]`` with any named ``check["warnings"]``. ``worked_from_sha`` is
    the REPORT's — the sha the worker actually branched from — falling back to the
    dispatch's stamped one; the report's is the right start, because a worktree cut one
    store commit after the stamp (KYO-10) would otherwise count the controller's store
    commit as work the report left out. With no commits reported, the comparison
    decides the outcome: ``branch-did-not-advance`` or ``unreported-commits``, and
    ``no-commits`` only when the comparison itself could not be made.
    """
    branch = dispatch["branch"]
    if not branch:
        return {"branch": None, "outcome": BRANCH_CHECK_NOT_DECLARED, "commits": []}
    base_sha = worked_from_sha or dispatch["worked_from_sha"]
    advance = _branch_advance(root, branch, base_sha, commits)
    warnings: list[dict] = []
    if advance["outcome"] == "did-not-advance":
        warnings.append(
            {
                "code": BRANCH_WARNING_DID_NOT_ADVANCE,
                "shas": [],
                "detail": (
                    f"{branch} has no commits past worked_from_sha {base_sha}, so "
                    f"`git merge {branch}` will ship nothing"
                ),
            }
        )
    if advance["unlisted"]:
        warnings.append(
            {
                "code": BRANCH_WARNING_UNLISTED_COMMITS,
                "shas": list(advance["unlisted"]),
                "detail": (
                    f"{branch} carries {len(advance['unlisted'])} commit(s) past "
                    f"worked_from_sha {base_sha} that this report's commits[] does not list "
                    "(commits[] goes oldest first and ends on the branch tip)"
                ),
            }
        )
    if not commits:
        if advance["outcome"] == "did-not-advance":
            outcome = BRANCH_CHECK_DID_NOT_ADVANCE
        elif advance["outcome"] == "advanced":
            outcome = BRANCH_CHECK_UNREPORTED_COMMITS
        else:
            outcome = BRANCH_CHECK_NO_COMMITS
        return {
            "branch": branch,
            "outcome": outcome,
            "commits": [],
            "advance": advance,
            "warnings": warnings,
        }
    entries = []
    for commit in commits:
        outcome, detail = _git_commit_reachability(root, commit["sha"], branch)
        entries.append({"sha": commit["sha"], "outcome": outcome, "detail": detail})
    if any(e["outcome"] == BRANCH_REACH_OFF_BRANCH for e in entries):
        overall = BRANCH_REACH_OFF_BRANCH
    elif any(e["outcome"] == BRANCH_REACH_CANNOT_TELL for e in entries):
        overall = BRANCH_CHECK_CANNOT_TELL
    else:
        overall = BRANCH_CHECK_VERIFIED
    if overall != BRANCH_REACH_OFF_BRANCH:
        order = _commits_order_warning(root, branch, commits, advance["unlisted"])
        if order is not None:
            warnings.append(order)
    return {
        "branch": branch,
        "outcome": overall,
        "commits": entries,
        "advance": advance,
        "warnings": warnings,
    }


def _branch_check_lines(check: dict, dispatch_id: str) -> list[str]:
    """Render :func:`_check_report_commits_on_branch`'s finding for a human.

    Every outcome gets a line — including the ones where nothing was checked. The
    silent case is what this record exists to end, so "no branch was declared, so
    this was not checked" has to be as visible as "checked, and they are all there".
    """
    branch = check["branch"]
    outcome = check["outcome"]
    if outcome == BRANCH_CHECK_NOT_DECLARED:
        return [
            f"branch check: NOT CHECKED — dispatch {dispatch_id} declared no branch "
            "(`kyo dispatch --branch NAME`), so where these commits landed is unknown "
            "and this command did not verify it"
        ]
    advance = check.get("advance") or {}
    base_sha = advance.get("worked_from_sha")
    if outcome == BRANCH_CHECK_DID_NOT_ADVANCE:
        # KYO-411: never the "nothing to check" line. A worker that deliberately
        # changed nothing and a worker whose commits never reached the branch look the
        # same from here, so the line says both and lets the controller tell which.
        return [
            f"branch check: BRANCH DID NOT ADVANCE — {branch} has no commits past "
            f"worked_from_sha {base_sha}, and this report carries no commits. If this "
            f"dispatch did any work, it never landed on {branch} (a detached HEAD, "
            f"another branch) and `git merge {branch}` will ship nothing; if it "
            "deliberately changed nothing, this is expected"
        ]
    if outcome == BRANCH_CHECK_UNREPORTED_COMMITS:
        return [
            f"branch check: UNREPORTED COMMITS — this report carries no commits, but "
            f"{branch} has {advance.get('count')} commit(s) past worked_from_sha "
            f"{base_sha}. The branch holds work the report does not account for; read "
            f"`git log {base_sha}..{branch}` before merging"
        ] + [f"  - {sha}: on {branch}, not in the report" for sha in advance.get("unlisted", [])]
    if outcome == BRANCH_CHECK_NO_COMMITS:
        line = (
            f"branch check: nothing to check — the declared branch is {branch}, and this "
            "report carries no commits"
        )
        if advance.get("detail"):
            # KYO-411: the only way to reach this outcome now is a comparison that
            # could not be made, so the line says why rather than resting on silence.
            line += (
                f"; whether {branch} moved could not be told either ({advance['detail']})"
            )
        return [line]
    warning_lines = _branch_warning_lines(check)
    if outcome == BRANCH_CHECK_VERIFIED:
        return [
            f"branch check: VERIFIED — all {len(check['commits'])} reported commit(s) are "
            f"reachable from {branch}"
        ] + warning_lines
    # cannot-tell. Named as its own outcome, never softened into the verified line:
    # this is the law-6 case, and a controller reading it must not be able to mistake
    # it for "checked and fine".
    lines = [
        f"branch check: CANNOT TELL — reachability from {branch} could not be determined "
        "for every reported commit. This is NOT a pass: check by hand before merging."
    ]
    for entry in check["commits"]:
        label = {
            BRANCH_REACH_REACHABLE: "reachable",
            BRANCH_REACH_CANNOT_TELL: "cannot tell",
            BRANCH_REACH_OFF_BRANCH: "NOT reachable",
        }[entry["outcome"]]
        lines.append(f"  - {entry['sha']}: {label} — {entry['detail']}")
    return lines + warning_lines


def _branch_warning_lines(check: dict) -> list[str]:
    """KYO-411: the named warnings beside a branch check that had commits to check."""
    lines = []
    for warning in check.get("warnings") or []:
        if warning["code"] == BRANCH_WARNING_UNLISTED_COMMITS:
            lines.append(
                f"  warning ({BRANCH_WARNING_UNLISTED_COMMITS}): {warning['detail']} — "
                "the report does not account for everything the merge will ship:"
            )
            lines += [f"    - {sha}" for sha in warning["shas"]]
        else:
            lines.append(f"  warning ({warning['code']}): {warning['detail']}")
    return lines


def cmd_report_schema(ctx: Context, args: Namespace) -> Output:
    """``kyo report-schema`` — print the schema ``kyo report`` validates against.

    KYO-149.c1: the schema a worker's report is validated against is part of the
    installed tool, so it is served by the tool — ``templates/`` is a directory of the
    source tree and never ships (docs/design/dispatch.md#kyo-149).

    Three properties, each load-bearing:

    * **``PROJECT_NONE``.** This never searches upward for a project, and the
      reader is why. The worker who needs the schema is standing in a dispatch
      worktree — tracked files only, no store, possibly no Kyosei project anywhere
      above it. A command that must find a store before it will print a constant of
      the *installed tool* would fail for precisely the person it was added for,
      which is the same wall in a new place. ``kyo init`` is ``PROJECT_NONE`` for
      the sibling reason.
    * **It prints the file, not a description of it.** :func:`report_schema.schema_text`
      is the only read of the packaged document in shipped code, and
      :func:`cmd_report` validates with the mirror that
      ``tests/test_kyo149_report_schema_agrees.py`` pins to that same document. So
      "what this prints" and "what ingest enforces" are one answer by construction
      and by test, not by two maintainers remembering.
    * **Served, never copied.** The rejected option was to add the schema to
      ``kyo init``'s copied assets, and the reason it stays rejected is the reason
      the reporter gave and this project has acted on twice (KYO-129 put a default
      in code rather than in a packaged file; KYO-138 exists to generate a
      reference rather than maintain one): a copy is a second thing to update, so
      it goes stale, and a stale schema is worse than an unreachable one — it is
      wrong at the moment it is trusted.

    ``--json`` emits the parsed document, so ``kyo report-schema --json | jq`` is a
    query over the real schema. Without it the output is the file's own bytes.
    """
    document = report_schema.schema_document()
    return Output(
        lines=report_schema.schema_text().splitlines(),
        data=document,
    )


def _load_report_document(file: str, dispatch_id: str) -> dict:
    """KYO-477: the half of ingest that needs no store — read ``--file``, parse it,
    validate it against the schema, and refuse a ``dispatch_id`` that is not the one
    named on the command line. One function, called by both the ingest and
    ``--check``, so the two cannot disagree about what a well-formed report is.
    """
    path = Path(file)
    if not path.is_file():
        raise KyoseiError(f"cannot read --file: {file} is not a file")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise KyoseiError(f"{file} is not valid JSON: {exc}") from exc

    problems = report_schema.validate_report(parsed)
    if problems:
        detail = "\n".join(f"  - {p}" for p in problems)
        # KYO-149: the remedy this message names has to be reachable from where the
        # reader is standing. It used to name a source-tree path no consumer has;
        # it now names the command that prints the same document from the installed
        # package, which is the thing they can actually run next.
        raise KyoseiError(
            f"{file} does not conform to the report schema "
            f"(print it with `kyo report-schema`):\n{detail}"
        )

    if parsed["dispatch_id"] != dispatch_id:
        raise ContractViolation(
            CONTRACT_REPORT_ID_MISMATCH,
            [
                f"report names dispatch_id {parsed['dispatch_id']!r} but was ingested as "
                f"`kyo report {dispatch_id}` — refusing rather than silently reconciling (law 6)"
            ],
        )
    return parsed


def _check_report_against_store(ctx: Context, store, dispatch_id: str, parsed: dict) -> dict:
    """KYO-477: every check ingest runs against the store BEFORE it writes anything —
    the record_id cross-check, the terminal/amends contracts, the branch check over
    ``commits[]`` and the record scoping of ``criteria_verdicts`` — and nothing else.

    It only reads. :func:`cmd_report` calls it inside its transaction, so a refusal
    leaves no half-ingested report behind; ``kyo report --check`` calls it with no
    transaction open at all, which is what lets a worker run it from a dispatch
    worktree whose store is the main checkout's, opened read-only (KYO-342). Returns
    what the write phase needs: ``dispatch``, ``record_id``, ``amends``, ``amending``,
    ``covers`` and ``branch_check``.
    """
    dispatch = store.dispatch(dispatch_id)
    record_id = dispatch["record_id"]
    if parsed["record_id"] != record_id:
        raise ContractViolation(
            CONTRACT_REPORT_ID_MISMATCH,
            [
                f"report names record_id {parsed['record_id']!r} but dispatch {dispatch_id} "
                f"was generated from {record_id!r} — refusing"
            ],
        )
    # KYO-90: the resumed-worker case. `dispatch-already-terminal` is not
    # relaxed — what changes is that a report may *declare* which report it
    # supersedes, and only that declaration gets it past the refusal. A second
    # report that says nothing is still refused, so a different account can
    # never land silently over the first; and an amendment names the report it
    # replaces, so it cannot silently land over an account it never read.
    amends = report_amends(parsed)
    current_report_id = dispatch["report_id"]
    amending = bool(amends) and dispatch["state"] == DISPATCH_STATE_REPORTED
    if not amending and dispatch["state"] != DISPATCH_STATE_RUNNING:
        detail = [
            f"{dispatch_id} is already {dispatch['state']}; only a running dispatch "
            "can receive a report"
        ]
        if dispatch["state"] == DISPATCH_STATE_REPORTED and current_report_id:
            detail.append(amend_remedy(dispatch_id, current_report_id))
        raise ContractViolation(CONTRACT_DISPATCH_ALREADY_TERMINAL, detail)
    if amends and not amending:
        # An `amends` on a dispatch that is running (nothing to supersede) or
        # terminated-no-report (it never had a report at all). Both are the
        # same mistake — a correction aimed at an account that does not exist —
        # and both are refused rather than quietly ingested as a first report,
        # because ingesting it would file a claim about supersession that the
        # store would then have no way to make true.
        raise ContractViolation(
            CONTRACT_REPORT_AMENDS_WRONG_REPORT,
            [
                f'report declares "{REPORT_AMENDS_KEY}": {amends!r}, but dispatch '
                f"{dispatch_id} is {dispatch['state']} and carries no ingested report to "
                "supersede",
                f"drop the {REPORT_AMENDS_KEY!r} key and file this as the dispatch's report",
            ],
        )
    if amending and amends != current_report_id:
        raise ContractViolation(
            CONTRACT_REPORT_AMENDS_WRONG_REPORT,
            [
                f'report declares "{REPORT_AMENDS_KEY}": {amends!r}, but the current report '
                f"for {dispatch_id} is {current_report_id!r}",
                "a correction written against an account that has itself already been "
                "superseded was written without reading what it replaces — refusing rather "
                "than reconciling (law 6)",
                f"read {current_report_id} on {record_id} (`kyo show <record>` renders "
                "every report and marks which one is current), fold anything it says "
                f'that this one does not into your document, then set "{REPORT_AMENDS_KEY}": '
                f'"{current_report_id}" and file it again',
            ],
        )
    # KYO-32: before anything is written, every reported commit must be on the
    # branch this dispatch declared. Raised inside the transaction like the
    # cross-checks above, so a refusal leaves no half-ingested report behind.
    branch_check = _check_report_commits_on_branch(
        ctx.code_root, dispatch, parsed["commits"], parsed.get("worked_from_sha")
    )
    off_branch = [
        entry
        for entry in branch_check["commits"]
        if entry["outcome"] == BRANCH_REACH_OFF_BRANCH
    ]
    if off_branch:
        declared = branch_check["branch"]
        raise ContractViolation(
            CONTRACT_REPORT_COMMIT_OFF_BRANCH,
            [
                f"{entry['sha']} is not reachable from {declared!r}, the branch dispatch "
                f"{dispatch_id} declared — a commit on some other branch is a commit the "
                f"orchestrator's `git merge {declared}` will not pick up, and that merge "
                'reports "Already up to date" and exits 0 rather than saying so. Move the '
                f"commit onto {declared} (e.g. `git cherry-pick`/`git rebase`) and re-file "
                "the report, or re-dispatch declaring the branch the work is actually on"
                for entry in off_branch
            ],
        )

    # KYO-13.c4 / KYO-12: a report may carry verdicts for its dispatch's primary
    # record AND every record the dispatch formally covers via `--also-covers`
    # at dispatch time (dispatch["covers_json"]) — never any other record. This
    # is the fix for "a report cannot carry verdicts for co-dispatched records":
    # formal coverage, declared and frozen at dispatch time, not informal
    # bundling discovered after the fact.
    covers = json.loads(dispatch["covers_json"]) if dispatch["covers_json"] else []
    allowed_record_ids = {record_id, *covers}
    for cv in parsed["criteria_verdicts"]:
        criterion = store.criterion(cv["criterion_id"])
        if criterion["record_id"] not in allowed_record_ids:
            raise KyoseiError(
                f"criteria_verdicts names {cv['criterion_id']!r}, which belongs to "
                f"{criterion['record_id']}, but dispatch {dispatch_id} only formally "
                f"covers {sorted(allowed_record_ids)} (this report's own record_id plus "
                "whatever `kyo dispatch --also-covers` declared) — refusing to informally "
                "bundle a record this dispatch never declared covering it (KYO-12)"
            )
    return {
        "dispatch": dispatch,
        "record_id": record_id,
        "amends": amends,
        "amending": amending,
        "covers": covers,
        "branch_check": branch_check,
    }


def _report_check_output(
    ctx: Context, store, dispatch_id: str, parsed: dict, file: str
) -> Output:
    """KYO-477: ``kyo report <dispatch-id> --file F --check`` — the ingest's checks,
    and nothing it writes.

    The schema validation and the dispatch_id cross-check have already run (in
    :func:`_load_report_document`, the same call the ingest makes); this runs
    :func:`_check_report_against_store` with no transaction open, and stops. A
    refusal is the same exception, with the same message and exit code, that
    ingesting this file would raise. A pass names what was checked and what was
    not: the docs obligations and touch drift are computed by the ingest's write
    phase and are not part of this check.
    """
    checked = _check_report_against_store(ctx, store, dispatch_id, parsed)
    lines = [
        f"report check: {file} passes every check `kyo report {dispatch_id}` runs before "
        "it writes (schema, dispatch and record ids, dispatch state, criteria_verdicts "
        "scoping, commits on the declared branch); nothing was written",
    ]
    lines += _branch_check_lines(checked["branch_check"], dispatch_id)
    lines.append(
        "not checked here: docs_verdicts matching and touch drift, which the ingest "
        "computes as it writes and which never refuse a report"
    )
    return Output(
        lines,
        {
            "ok": True,
            "command": "report",
            "check": True,
            "dispatch_id": dispatch_id,
            "record_id": checked["record_id"],
            "amends": checked["amends"] if checked["amending"] else None,
            "branch_check": checked["branch_check"],
        },
    )


def verdict_counts_text(verdicts: list[dict]) -> str:
    """KYO-540.c2: the report's proposed verdicts counted by state, e.g. ``2 met, 1
    unmet-unobserved``; ``none`` for a report that proposes no verdict. Shared by
    ``kyo report`` and ``kyo accept`` so the two summary lines count one way."""
    counts: dict[str, int] = {}
    for entry in verdicts:
        counts[entry["verdict"]] = counts.get(entry["verdict"], 0) + 1
    return ", ".join(f"{n} {state}" for state, n in sorted(counts.items())) or "none"


def cmd_report(ctx: Context, args: Namespace) -> Output:
    """``kyo report <dispatch-id> --file .kyosei-report.json`` — structured ingest.

    KYO-7.c3/c4: validates against ``report.schema.json`` (the subset
    implemented in :mod:`kyosei.report_schema`, and printable with
    ``kyo report-schema``), refuses loudly on a mismatched
    ``dispatch_id``/``record_id``, stores every field structurally, and never
    applies any verdict to a criterion — that stays a controller act via
    ``kyo verify``.

    **KYO-90: a resumed worker's corrected report.** Workers are resumed on the same
    branch after a merge finds something wrong, so the corrected report needs somewhere
    to go (docs/design/dispatch.md#kyo-90). A report may carry
    ``"amends": "<report-id>"``, and then it is ingested as a NEW report row that
    becomes the dispatch's current one while the
    named report stays exactly where it was, marked superseded by every reader.

    Three properties, each deliberate:

    * ``dispatch-already-terminal`` still refuses a second report that declares
      nothing. That contract is not the bug — a report is the record of what a
      dispatch concluded, and a second one landing silently over the first would
      make the frozen-brief-and-report pairing worthless.
    * The declaration is in the **document**, not in a flag on this command. The
      party who knows the earlier report was wrong is the worker, and a worker
      cannot type the controller's flags; a field in the file it rewrites anyway
      is reachable from where it stands. It also names *which* report is being
      superseded, which a bare flag cannot, so a stale correction is refused
      (:data:`CONTRACT_REPORT_AMENDS_WRONG_REPORT`) instead of silently landing
      over an account it never read.
    * **Every consumer below re-runs over the amended document** — the schema
      validation, the ``criteria_verdicts`` record-scoping, the branch check over
      ``commits[]`` (which still *refuses* an off-branch commit), and touch drift,
      which reads ``touched_files[]`` through ``dispatch["report_id"]`` and so
      follows the current report by construction. That is KYO-90.c2, and it is the
      half the evidence-artifact workaround could not do at all: an amended report
      that nothing checks is prose in a different filing cabinet.

    Nothing here reopens anything. ``state`` stays ``reported``, ``closed_at``
    stays whatever it was, and the dispatch remains the frozen record of one
    attempt — an amendment corrects the *account* of that attempt, not the number
    of attempts.
    """
    store = ctx.store()
    dispatch_id = args.dispatch_id
    parsed = _load_report_document(args.file, dispatch_id)

    if getattr(args, "check", False):
        return _report_check_output(ctx, store, dispatch_id, parsed, args.file)

    with store.transaction():
        checked = _check_report_against_store(ctx, store, dispatch_id, parsed)
        dispatch = checked["dispatch"]
        record_id = checked["record_id"]
        amends = checked["amends"]
        amending = checked["amending"]
        covers = checked["covers"]
        branch_check = checked["branch_check"]

        before = {record_id: export.record_document(store, record_id)}
        report_id, seq = store.new_child_id(record_id, "report")
        stamp = now()
        body_json = json.dumps(parsed, sort_keys=True)
        store.conn.execute(
            "INSERT INTO reports (id, record_id, dispatch_id, seq, worked_from_sha, body_json,"
            " author, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                report_id,
                record_id,
                dispatch_id,
                seq,
                parsed["worked_from_sha"],
                body_json,
                ctx.actor,
                stamp,
            ),
        )
        store.conn.execute(
            "UPDATE dispatches SET state = ?, report_id = ?, updated_at = ? WHERE id = ?",
            (DISPATCH_STATE_REPORTED, report_id, stamp, dispatch_id),
        )
        # KYO-137.c2. Inside the same transaction as the report, deliberately: the
        # obligations a report raises are part of what ingesting it means, and a report
        # that landed while its obligations did not would be a record that reached `done`
        # through a gate nobody had opened.
        # KYO-211: the report's own `touched_files[]` goes in, because the dispatch row
        # in hand still carries the previous report's id (it was read before the UPDATE
        # above) — so `_reported_touched_files` would answer about the wrong document on
        # a first ingest and about the superseded one on an amendment. The parsed
        # document is the only place this report's list exists at this moment.
        obligations = _raise_docs_obligations(
            ctx,
            store,
            dispatch,
            stamp,
            parsed.get("docs_verdicts"),
            touched_files=parsed.get("touched_files"),
            against=getattr(args, "against", None) or "main",
        )
        _touch_record(store, record_id)
        after, events = commands._commit_and_export(
            store,
            command=(
                f"report {dispatch_id} (amends {amends})" if amending else f"report {dispatch_id}"
            ),
            actor=ctx.actor,
            record_ids=[record_id],
            before=before,
        )
    commands._finish_mutation(store, [record_id], events)

    sha_note = ""
    if parsed["worked_from_sha"] != dispatch["worked_from_sha"]:
        # KYO-10: the dispatch's recorded base sha is stamped *before* the commit
        # that records the dispatch itself lands, so a reported sha that is a
        # descendant of it is the expected case — not noise worth surfacing every
        # time. KYO-105: how far ahead depends on when the controller commits the
        # store relative to creating the worktree, so equal (which does not reach
        # this branch at all) and one ahead are both normal. Only a sha that is NOT
        # reachable from the recorded base (unrelated history, or behind it) is
        # actually worth a controller's attention.
        ancestor = _git_is_ancestor(
            ctx.code_root, dispatch["worked_from_sha"], parsed["worked_from_sha"]
        )
        if ancestor:
            sha_note = (
                f" (worked_from_sha {parsed['worked_from_sha']} is a descendant of the "
                f"dispatch's recorded base sha {dispatch['worked_from_sha']} — expected by "
                "construction, KYO-10; not itself worth reconciling)"
            )
        else:
            unknown = " (git could not determine ancestry)" if ancestor is None else ""
            sha_note = (
                f" (worked_from_sha {parsed['worked_from_sha']} differs from the dispatch's "
                f"recorded base sha {dispatch['worked_from_sha']} and is not a known "
                f"descendant of it{unknown} — worth checking what branched from where)"
            )

    # KYO-71.c3: computed here, AFTER the transaction, because it refuses nothing —
    # a drift finding must never be able to fail an ingest, and running it outside
    # the transaction makes that structural rather than a promise. This is the
    # chosen firing point: complete branch, store in hand, and the exact moment the
    # controller would otherwise correct the record's touches by hand.
    #
    # KYO-85: re-read, because the row in hand was fetched before the ingest and does
    # not know about the report it just accepted. The dispatch that reaches the drift
    # computation has to be the one a `kyo touch-drift` run a week later would read,
    # or the answer printed at ingest and the answer printed afterwards are two
    # computations over two bodies of evidence — which is exactly what KYO-85.c1 asks
    # not to be the case.
    drift = _drift_for_dispatch(
        ctx, store, store.dispatch(dispatch_id), getattr(args, "against", None) or "main"
    )

    # KYO-555.c6: every history-lint override on the branch, read from its commits and
    # printed with the disagreements -- an override is the worker disagreeing with the
    # lint, and the orchestrator reviews it before accepting. Not stored.
    overrides = _history_overrides(ctx, dispatch, getattr(args, "against", None) or "main")

    # KYO-614.c2: the module obligations this report answers that the branch tip's
    # `Recx-Checked` trailers do not name, so recx's own reminder will ask again. A
    # warning and never a refusal, computed after the transaction like the drift above;
    # `None` in a project that declares no recx-manifest set, whose output is unchanged.
    recx_trailers = _recx_trailer_finding(ctx, dispatch, parsed.get("docs_verdicts"))

    # KYO-7.c4: premise_false and disagreements print BEFORE any verdict.
    #
    # KYO-208.c1: and before the docs obligations and the touch drift, which used to
    # print here — between the branch check and `premise_false` — and pushed the whole
    # review block below however many obligation lines this dispatch happened to raise.
    # The skill tells a controller to review `premise_false[]` FIRST, so a consumer
    # grepped the two apart by hand: exactly the courier step this command exists to
    # remove. The order below IS the review order the skill mandates; the `--json`
    # payload's keys are unchanged, because a machine consumer reads keys and never
    # line order (law 3: two renderings of one computation).
    if amending:
        lines = [
            f"ingested AMENDED report {report_id} for dispatch {dispatch_id}{sha_note}",
            f"  {report_id} is now the current account of {dispatch_id}; {amends} is superseded",
            f"  {amends} keeps its id, its ingest time and every word it said — nothing was "
            "overwritten and nothing was deleted (`kyo show ...` renders both)",
        ]
        # KYO-90.c2's other half, stated where the controller reads it: everything
        # below this line was recomputed over the amended document, not carried
        # over from the report it replaces.
        lines.append(
            f"  the checks below ran against {report_id}, the report that is now current"
        )
        if dispatch["closed_at"]:
            # An amendment does not reopen anything. The KYO-83 case arrived after
            # the dispatch was closed, and a mechanism that refused it there would
            # have refused the only instance it was built for.
            lines.append(
                f"  note: {dispatch_id} was closed at {dispatch['closed_at']} and stays closed; "
                "state is still `reported` and this did not reopen it"
            )
    else:
        lines = [f"ingested report {report_id} for dispatch {dispatch_id}{sha_note}"]
    lines += _branch_check_lines(branch_check, dispatch_id)
    # KYO-540.c2: the one-line summary goes FIRST. The block below is long enough that a
    # reader filtered it, and filtering is how an outcome was misread; the counts that
    # decide what to do next are the first thing printed.
    raised = obligations.get("raised") or []
    lines.insert(
        0,
        f"summary: report {report_id} of {dispatch_id} — "
        f"premise_false {len(parsed['premise_false'])}, "
        f"disagreements {len(parsed['disagreements'])}, "
        f"verdicts proposed: {verdict_counts_text(parsed['criteria_verdicts'])}, "
        f"open docs obligations {len(raised)}",
    )
    if covers:
        lines.append(f"dispatch formally covers (KYO-13.c4): {', '.join(sorted(covers))}")
    lines.append(f"premise_false ({len(parsed['premise_false'])}):")
    lines += (
        [f"  - {pf['claim']}\n    -> {pf['correction']}" for pf in parsed["premise_false"]]
        or ["  (none)"]
    )
    lines.append(f"disagreements ({len(parsed['disagreements'])}):")
    lines += (
        [
            f"  - {d['topic']}: brief said {d['brief_said']!r}; worker did "
            f"{d['worker_did']!r}\n    because: {d['rationale']}"
            for d in parsed["disagreements"]
        ]
        or ["  (none)"]
    )
    if overrides:
        lines.append(
            f"  history-lint overrides on {dispatch['branch']} ({len(overrides)}), each a "
            f"`{docs_history.TRAILER}` trailer letting newly introduced history prose into "
            "an ssot document — review each before accepting:"
        )
        lines += [f"  - {override.line()}" for override in overrides]
    lines.append(f"corrected_numbers ({len(parsed['corrected_numbers'])}):")
    lines += (
        [
            f"  - {cn['claim']}: {cn['brief_value']} -> {cn['actual_value']} ({cn['method']})"
            for cn in parsed["corrected_numbers"]
        ]
        or ["  (none)"]
    )
    lines.append(f"criteria_verdicts ({len(parsed['criteria_verdicts'])}):")
    lines += (
        [
            f"  - {cv['criterion_id']}: {cv['verdict']} ({cv['method']})"
            for cv in parsed["criteria_verdicts"]
        ]
        or ["  (none)"]
    )
    lines.append(f"commits ({len(parsed['commits'])}):")
    lines += ([f"  - {commit_label(c)}" for c in parsed["commits"]] or ["  (none)"])
    # KYO-198.c4: the note that creates the need names the mechanism that meets it.
    # "Applying them is a controller act via `kyo verify`" was correct and left the
    # controller extracting each criterion's evidence from this JSON by hand and running
    # one `kyo verify --evidence-file` per criterion, for every report. The deliberate
    # step is unchanged; what it costs is not.
    # KYO-489: the reasoning is explained once, in `kyo reference report`; the
    # commands after it are the remedy and are unchanged.
    lines.append(
        f"note: {explanations.say('verdicts-not-applied')} "
        f"`kyo verify --from-report {dispatch_id}` (all of them, or "
        "`--only <criterion-id> ...` for the ones you have checked), or one at a time with "
        "`kyo verify <criterion-id> --verdict ... --method ...` (KYO-7.c4)"
    )
    # KYO-137.c2, moved here by KYO-208.c1. Still the finding the controller must act
    # on before the record can move — what changed is that it no longer stands between
    # the reader and the review block the skill sends them to first. It is the last
    # thing on the screen, which is where the next action is read from.
    lines += _docs_obligation_lines(obligations)
    lines += _recx_trailer_lines(recx_trailers)
    lines += drift_lines(drift)
    # KYO-592: every stop of this dispatch's worker that a stop guard reported without
    # blocking (stop_hook_active), so the finding it did not enforce reaches the review.
    unblocked = stop_audit.unblocked_stops(store, dispatch_id)
    lines += stop_audit.unblocked_stop_lines(unblocked)
    return Output(
        lines,
        {
            "ok": True,
            "command": "report",
            "report_id": report_id,
            "dispatch_id": dispatch_id,
            "record_id": record_id,
            # KYO-90: the report this one superseded, or None for a first report.
            # Present either way, so "this was not an amendment" is a value a
            # machine consumer reads rather than a key it fails to find (law 6).
            "amends": amends if amending else None,
            "covers": covers,
            # KYO-32: the same finding the text output renders, as data. A machine
            # consumer reads `branch_check["outcome"]`, which is `verified` only when
            # every commit was actually confirmed reachable.
            # KYO-137.c2: what this report raised against the project's documents,
            # in three named states so "no document is implicated" and "no document
            # was examined" are never the same JSON.
            "docs_obligations": obligations,
            "branch_check": branch_check,
            # KYO-71: the same finding the text renders. `outcome` is `drift` only
            # when files really were edited outside the claim, and `not-checked`
            # whenever the comparison could not be made — never silently `none`.
            "touch_drift": drift,
            "premise_false": parsed["premise_false"],
            "disagreements": parsed["disagreements"],
            "history_overrides": [override.as_json() for override in overrides],
            "corrected_numbers": parsed["corrected_numbers"],
            "criteria_verdicts": parsed["criteria_verdicts"],
            "commits": parsed["commits"],
            "record": after[record_id],
            # KYO-614.c2: present only in a project that declares a recx-manifest set.
            **({"recx_trailers": recx_trailers} if recx_trailers is not None else {}),
            # KYO-592: present only when there are any, so a report whose worker never
            # stopped past a finding prints the envelope it always printed.
            **(
                {
                    "unblocked_stops": [
                        {
                            "event_id": u.event_id,
                            "at": u.at,
                            "guard": u.guard,
                            "contract": u.contract,
                        }
                        for u in unblocked
                    ]
                }
                if unblocked
                else {}
            ),
        },
    )


def _recx_trailer_finding(
    ctx: Context, dispatch: sqlite3.Row | dict, docs_verdicts: list[dict] | None
) -> dict | None:
    """KYO-614.c2: :func:`docs_recx.unchecked_modules` for this dispatch's branch, or
    ``None`` when the project declares no recx-manifest set."""
    if docs_recx.project_manifest(ctx.root / config_module.KYOSEI_DIR) is None:
        return None
    return docs_recx.unchecked_modules(ctx.code_root, dispatch["branch"], docs_verdicts)


def _recx_trailer_lines(finding: dict | None) -> list[str]:
    """One warning per answered module id the branch tip's trailers do not name."""
    if not finding or not finding["unchecked"]:
        return []
    key = docs_recx.CHECKED_TRAILER
    why = (
        f" ({finding['detail']})"
        if finding["detail"]
        else f" (read from the tip commit of {finding['branch']}; a trailer on an earlier "
        "commit does not cover the commits after it)"
    )
    lines = [
        f"recx trailer: {len(finding['unchecked'])} module(s) answered in docs_verdicts[] "
        f"are not named by a `{key}` trailer on the branch tip{why}, so recx's drift "
        f"reminder (recx-cli {docs_recx.CHECKED_TRAILER_SINCE} or later) will ask about "
        "them again; a warning, not a refusal:"
    ]
    lines += [f"  warning: module {module_id} — no `{key}: {module_id}` on the tip"
              for module_id in finding["unchecked"]]
    return lines


def _history_overrides(
    ctx: Context, dispatch: sqlite3.Row | dict, against: str
) -> list[docs_history.HistoryOverride]:
    """The ``Kyosei-History-OK`` trailers on the dispatch's branch (KYO-555.c6), or none
    when it has no branch git can read."""
    branch = dispatch["branch"]
    if not branch:
        return []
    return docs_history.branch_overrides(ctx.code_root, against, branch)


def cmd_split(ctx: Context, args: Namespace) -> Output:
    """``kyo split <record-id> --to-successor <criterion-id> [...]``.

    KYO-7.c6: creates a successor record of the same kind carrying the named
    UNMET criteria (new ids, text copied, state preserved); the original keeps
    its met criteria and their verifications untouched, and its split-away
    criteria become ``void-withdrawn`` with an auto reason naming the successor
    (law 4: supersede, never delete). Splitting away a met (or already-void)
    criterion is refused.
    """
    store = ctx.store()
    record_id = args.id
    requested = list(args.to_successor)
    if len(set(requested)) != len(requested):
        raise UsageError("--to-successor lists the same criterion id more than once")

    record = store.record(record_id)
    registry = _load_registry(store)
    kind_def = registry.require(record["kind"])
    if kind_def.archetype != "work":
        raise KyoseiError(
            f"{record_id} is kind {record['kind']!r} (archetype: {kind_def.archetype}); only "
            "archetype: work records have criteria to split"
        )

    named_criteria = []
    for cid in requested:
        criterion = store.criterion(cid)
        if criterion["record_id"] != record_id:
            raise KyoseiError(f"{cid} belongs to {criterion['record_id']}, not {record_id}")
        named_criteria.append(criterion)

    unmet = {"unmet-unbuilt", "unmet-unobserved"}
    not_unmet = [c for c in named_criteria if c["state"] not in unmet]
    if not_unmet:
        raise ContractViolation(
            CONTRACT_SPLIT_REQUIRES_UNMET_CRITERIA,
            [
                f"{c['id']} is {c['state']}, not unmet — only unmet-unbuilt/unmet-unobserved "
                "criteria may split to a successor (a met criterion stays on the original; a "
                "void one is already settled)"
                for c in not_unmet
            ],
        )

    with store.transaction():
        before = {record_id: export.record_document(store, record_id)}

        successor_id = store.new_record_id()
        seq = int(successor_id.rsplit("-", 1)[1])
        stamp = now()
        title = f"{record['title']} (split from {record_id})"
        description = f"Split from {record_id}.\n\n---\nSplit from: {record_id}\n"
        store.conn.execute(
            "INSERT INTO records (id, seq, kind, title, status, plan_id, scope, description,"
            " wont_do_reason, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                successor_id,
                seq,
                record["kind"],
                title,
                # KYO-101: a split successor is filed in whatever this project
                # declares `role: initial`, exactly like `kyo new`.
                _load_status_ladder(store).initial,
                record["plan_id"],
                # KYO-100: inherited, like `kind` and `plan_id` above and unlike
                # `kyo spawn`, which does not inherit it. A split successor is the same
                # work carrying some of the same criteria forward, so the part of the
                # system it belongs to is the same part by construction; a spawn crosses
                # a kind boundary and makes no such claim.
                record["scope"],
                description,
                stamp,
                stamp,
            ),
        )

        moved: list[tuple[sqlite3.Row, str]] = []
        for original in named_criteria:
            new_cid, cseq = store.new_child_id(successor_id, "criterion")
            store.conn.execute(
                "INSERT INTO criteria (id, record_id, seq, text, state, reason, created_at,"
                " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_cid,
                    successor_id,
                    cseq,
                    original["text"],
                    original["state"],
                    original["reason"],
                    stamp,
                    stamp,
                ),
            )
            moved.append((original, new_cid))

        for touch in store.touches(record_id):
            new_tid, tseq = store.new_child_id(successor_id, "touch")
            store.conn.execute(
                "INSERT INTO touches (id, record_id, seq, path, symbol, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (new_tid, successor_id, tseq, touch["path"], touch["symbol"], stamp),
            )

        for original, new_cid in moved:
            reason = f"split off to successor {successor_id} as {new_cid}"
            check_criterion_state(original["id"], "void-withdrawn", reason)
            store.conn.execute(
                "UPDATE criteria SET state = 'void-withdrawn', reason = ?, updated_at = ?"
                " WHERE id = ?",
                (reason, stamp, original["id"]),
            )

        link_id, lseq = store.new_link_id()
        store.conn.execute(
            "INSERT INTO links (id, seq, src_id, edge_type, dst_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (link_id, lseq, successor_id, "produced-by", record_id, stamp),
        )

        _touch_record(store, record_id)
        after, events = commands._commit_and_export(
            store,
            command=f"split {record_id} -> {successor_id}",
            actor=ctx.actor,
            record_ids=[record_id, successor_id],
            before={**before, successor_id: None},
        )
    commands._finish_mutation(store, [record_id, successor_id], events)

    lines = [
        f"split {record_id} -> {successor_id}",
        echo_value("successor title", title),
        f"moved {len(moved)} criterion/criteria to the successor:",
    ]
    lines += [f"  {orig['id']} -> {new_cid}  ({orig['text']})" for orig, new_cid in moved]
    lines.append(f"link: {link_id}  {successor_id} produced-by {record_id}")
    return Output(
        lines,
        {
            "ok": True,
            "command": "split",
            "record_id": record_id,
            "successor_id": successor_id,
            "moved_criteria": [{"from": o["id"], "to": n} for o, n in moved],
            "record": after[record_id],
            "successor": after[successor_id],
        },
    )
