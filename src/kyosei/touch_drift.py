"""Touch drift: a dispatch's declared claim checked against what its branch changed.

Also what the disjointness check was worth at dispatch time (declaration provenance),
and ``kyo touch-drift``.
"""

from __future__ import annotations

import json
import shlex
from argparse import Namespace
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from . import commands, explanations
from . import sweep as sweep_module
from .commands import Context, Output
from .contracts import (
    ADDITIVE_SYMBOL,
    GUARDED_BRANCH_DIRS,
    TOUCH_NEW_FILE_PREFIX,
    WORKER_REPORT_PATH,
    claim_covers,
    is_directory_touch,
    is_new_file_touch,
    touch_covers,
    touch_target,
)
from .errors import ContractViolation, KyoseiError
from .git_queries import (
    _git_commit_exists,
    _git_diff_hunks,
    _git_file_at,
    _git_is_ancestor,
    branch_changed_files,
    branch_is_contained_in,
    commit_changed_files,
    run_git,
)

# ------------------------------ KYO-71: the claim, checked against what happened

#: KYO-71, law 6: the outcomes of comparing a dispatch's declared touch claim against
#: the files it actually edited. ``NOT_CHECKED`` is a first-class outcome and not a
#: failure — a dispatch that declared no branch has nothing to diff, and saying "no
#: drift" about a comparison that never ran is the silence this record class exists to
#: end (KYO-48's shape, one level along).
#:
#: KYO-85: ``git diff <against>...<branch>`` is empty **by construction** once the branch
#: is merged, and an empty list must never render as ``none`` ("checked, and you are
#: fine") — the documented workflow runs this after the merge
#: (docs/design/dispatch.md#kyo-85).
DRIFT_NOT_CHECKED = "not-checked"
DRIFT_NONE = "none"
DRIFT_FOUND = "drift"

#: KYO-85: what a finding was computed from, carried on the finding. Two sources
#: answer the same question and one of them survives the merge.
#:
#: ``DRIFT_SOURCE_REPORT`` is preferred wherever it exists, and that preference is the
#: fix. The worker's ``touched_files[]`` needs no ref, no merge base and no surviving
#: branch; it is readable after the branch is deleted, which is the state the workflow
#: leaves behind; and it is the same list a controller was otherwise hand-transcribing
#: out of the report to pass to ``kyo edit --touches-file``. Preferring it *at ingest
#: time too* is what makes KYO-85.c1 structural rather than lucky: the answer given
#: when the report lands and the answer given a week later are one computation over one
#: body of evidence, so they cannot disagree.
#:
#: The cost, stated because KYO-71 exists to say it: ``touched_files[]`` is a
#: *declaration*, and this record class is about the distance between a declaration and
#: what happened. So where git can still answer, its answer is not discarded — it is
#: compared, and a file the branch changed that the report did not name is reported as
#: its own finding (``unreported_by_worker``). The diff is checked against the
#: declaration at the one moment the diff still works.
DRIFT_SOURCE_REPORT = "report-touched-files"
DRIFT_SOURCE_GIT = "git-diff"
#: KYO-344: the third source, tried only once the first two are both unavailable — no
#: ``touched_files[]``, and the branch no longer resolves in this checkout. The
#: report's own ``commits[]`` is evidence the branch itself already can't be, once it's
#: gone: `kyo report` proved every sha reachable from the branch at ingest time
#: (KYO-32), and this asks the one further question that makes them usable here — are
#: they ALSO reachable from the trunk, i.e. did the merge actually land them. See
#: :func:`_drift_from_commits`.
DRIFT_SOURCE_COMMITS = "report-commits"

#: Paths a drift report never names. A worker branch cannot legitimately diff these
#: at all — ``kyo check-branch`` refuses on them — so listing them here would report
#: the same fact twice with the weaker of the two remedies attached.
_DRIFT_IGNORED_PREFIXES = GUARDED_BRANCH_DIRS

#: KYO-206: and the one FILE a drift report never names, for a different reason. The
#: worker's report is the dispatch's OUTPUT, not a file its work edited, and naming it
#: made the suggested `kyo edit --touches-file` line unrunnable once the file was gone.
#: Excluded even though `check-clean` and `.gitignore` should keep it off a branch: drift
#: reads what it is given, including a report's own `touched_files[]`
#: (docs/design/dispatch.md#kyo-206).
_DRIFT_IGNORED_PATHS: frozenset[str] = frozenset({WORKER_REPORT_PATH})


def _considered(changed: list[str]) -> list[str]:
    guarded = tuple(d.rstrip("/") for d in _DRIFT_IGNORED_PREFIXES)
    return [
        path
        for path in changed
        if path not in _DRIFT_IGNORED_PATHS
        and not any(path == g or path.startswith(g + "/") for g in guarded)
    ]


# ------------------------------------------ what the disjointness check was worth
#
# KYO-147.c1. An undeclared scope already refuses as `scope-unknown` (KYO-48); the PASSING
# side must not be silent either, because a controller reads silence as "no collision" —
# a verdict about the work, when what was compared is two predictions
# (docs/design/dispatch.md#kyo-147).
#
# So: state what was compared. Three outcomes per other running dispatch, and the
# first is by far the commonest, because a dispatch that has reported is no longer
# `running` and is therefore never in this set at all.

#: Nothing has ever checked this declaration against work that was actually done —
#: no dispatch on any record in its claim has reported. The comparison was a
#: prediction against a prediction, and this is the honest name for that.
DECLARATION_NEVER_CHECKED = "never-checked"
#: A previous dispatch on a record in this claim reported, and every file that report
#: named is covered by what the record declares today. The drift was fed back
#: (KYO-147.c4's `kyo edit --touches-file` step), so this declaration has survived
#: contact with real work at least once.
DECLARATION_RECONCILED = "reconciled"
#: A previous dispatch reported files this claim still does not cover. The
#: declaration is not merely unverified, it is KNOWN incomplete, by exactly the paths
#: named — the strongest thing this can say, and the one worth acting on.
DECLARATION_UNRECONCILED = "unreconciled"


def _prior_report_files(store, record_ids: list[str]) -> list[str]:
    """Every file the ingested reports on these records named, deduplicated.

    Reads the same ``touched_files[]`` :func:`touch_drift` prefers over a git diff,
    through the same accessor, so "what the work touched" has one meaning here and
    there. A record with no reported dispatch contributes nothing, which is what
    makes :data:`DECLARATION_NEVER_CHECKED` the default rather than a failure.
    """
    seen: set[str] = set()
    for record_id in record_ids:
        for dispatch in store.dispatches(record_id):
            named = _reported_touched_files(store, dispatch)
            if named:
                seen.update(named)
    return sorted(seen)


def declaration_provenance(store, others_running: list) -> list[dict]:
    """How stale each other running dispatch's DECLARED touches are (KYO-147.c1).

    One entry per dispatch the disjointness check compared against, in the order it
    compared them. ``reported`` is always ``False`` and is stated anyway: ingesting a
    report moves a dispatch out of ``running``, so *every* set this check compares is
    work still in flight, and that structural fact is exactly what a controller
    reading "no collision" does not know. The staleness that can vary is the history
    behind the record: whether an earlier dispatch on it ever reported, and whether
    that report's files were fed back into the table this check just read.
    """
    provenance = []
    for row in others_running:
        claim_records = [row["record_id"], *commands.dispatch_covers(row)]
        claim = commands._claim_of(store, row["record_id"], commands.dispatch_covers(row))
        reported_files = _prior_report_files(store, claim_records)
        unreconciled = [
            path for path in reported_files if not claim_covers(claim.files, path)
        ]
        if not reported_files:
            staleness = DECLARATION_NEVER_CHECKED
        elif unreconciled:
            staleness = DECLARATION_UNRECONCILED
        else:
            staleness = DECLARATION_RECONCILED
        provenance.append(
            {
                "dispatch_id": row["id"],
                "record_id": row["record_id"],
                "covers": commands.dispatch_covers(row),
                "declared_files": sorted(claim.files),
                # Always False by construction — see the docstring. Carried in the
                # payload rather than left to be inferred, because "this dispatch has
                # not reported" and "this key is missing" are different things to a
                # consumer.
                "reported": bool(row["report_id"]),
                "staleness": staleness,
                # The two facts `staleness` is derived FROM, carried beside it rather
                # than only as a label. `brief.py` is a pure renderer that imports
                # neither this module nor `commands`, and it branches on these rather
                # than on the label — so the brief and this payload cannot describe
                # different provenance for one comparison (law 3), and neither has to
                # import the other to agree.
                "prior_reported_file_count": len(reported_files),
                "unreconciled_files": unreconciled,
            }
        )
    return provenance


#: The sentence that turns "no collision" into a statement a controller can audit
#: (KYO-147.c1). Printed whether or not anything was running, because "there was
#: nothing to compare against" is the case in which the silence is most misleading.
#: KYO-489: its full text is explained once, in `kyo reference dispatch`; this is the
#: full text, kept as the name the brief's own rendering and the tests read it by.
PROVENANCE_HEADLINE = explanations.full("disjointness-declared")


def provenance_lines(provenance: list[dict]) -> list[str]:
    """:func:`declaration_provenance`, rendered (law 3: this and the brief's section
    are two renderings of one computation, never two computations).

    KYO-488: each staleness sentence prints once, followed by the
    dispatches in that state, in the order the states first appear. A KNOWN INCOMPLETE
    entry keeps a line of its own, because its file list and its `kyo touch-drift`
    remedy are facts about that one dispatch.

    Printing each once is what removes the 151 bytes per running dispatch that were
    measured repeating (KYO-482). KYO-489 then shortened the
    headline and the never-checked sentence to their deciding words; the full texts are
    in `kyo reference dispatch` (``explanations``). The reconciled sentence was not
    among the clauses KYO-482 shortened and is unchanged.
    """
    staleness_sentences = {
        DECLARATION_RECONCILED: (
            "reconciled — every file an earlier report on this "
            "claim named is covered by what it declares now. Still a prediction "
            "about the work in flight, but one that has survived contact once"
        ),
        DECLARATION_NEVER_CHECKED: explanations.short("never-checked-against-work"),
    }
    headline = explanations.say("disjointness-declared")
    if not provenance:
        return [
            f"{headline}. No other dispatch was running, so nothing was "
            "compared and nothing could have collided — this dispatch is unconstrained "
            "by the check rather than cleared by it."
        ]
    lines = [f"{headline}, against {len(provenance)} running dispatch(es):"]
    grouped: dict[str, list[str]] = {}
    for entry in provenance:
        who = f"{entry['dispatch_id']} (dispatching {entry['record_id']})"
        declared = f"{len(entry['declared_files'])} declared path(s)"
        if entry["staleness"] == DECLARATION_UNRECONCILED:
            lines.append(
                f"  {who}: {declared}, and KNOWN INCOMPLETE — an earlier report on this "
                f"claim named {len(entry['unreconciled_files'])} file(s) it still does "
                "not cover: "
                + ", ".join(entry["unreconciled_files"])
                + ". Reconcile with `kyo touch-drift <dispatch-id> --apply` before "
                "trusting this comparison; until then the check did not see those files"
            )
            continue
        state = (
            DECLARATION_RECONCILED
            if entry["staleness"] == DECLARATION_RECONCILED
            else DECLARATION_NEVER_CHECKED
        )
        grouped.setdefault(state, []).append(f"    {who}: {declared}")
    for state, members in grouped.items():
        if state == DECLARATION_NEVER_CHECKED:
            explanations.say("never-checked-against-work")
        lines.append(f"  {staleness_sentences[state]}:")
        lines += members
    return lines


def _cooccurrence_lines(cooccurrence: dict) -> list[str]:
    """:func:`commands.touch_cooccurrence`, rendered for the controller's terminal.

    KYO-147.c3 in one statement: **no findings means no lines**. Not "0 findings",
    not "not enough history yet" — nothing. A young store, or a declared set nothing
    has enough history behind, produces silence, because the alternative is a line
    that has the shape of a finding and the content of a shrug, and a controller who
    learns to skip that line has learned to skip the real ones too.
    """
    findings = cooccurrence.get("findings") or []
    if not findings:
        return []
    # KYO-489 (KYO-482: the rows stay, only this header shortens).
    return [
        f"{len(findings)} {explanations.say('dragged-in')} (from "
        f"{cooccurrence['reports']} reported dispatch(es)):"
    ] + [
        f"  {f['also_touched']} — {f['hits']} of the {f['support']} dispatch(es) that "
        f"touched {f['declared']} also touched it"
        for f in findings
    ]


def touch_symbol_lines(
    code_root: Path, documents: Iterable[Mapping]
) -> dict[str, dict[str, int]]:
    """``{path: {symbol: line}}`` — where each ``path#symbol`` touch is, right now
    (KYO-327.c2).

    A ``path#symbol`` touch is *already* checked against the working tree, at the
    moment it is declared (``cmd_records._validate_touches``: the file must exist and
    the symbol must be findable in it). The line that check found was thrown away, and
    every worker then re-derived it — the first stretch of turns of a dispatch goes to
    locating what the record already named. This is that same resolution, run once
    more at freeze time, and kept.

    ``sweep.symbol_regions`` and not a second search: it is the reader that already
    decides which lines a ``path#symbol`` claim owns, for touch drift and for the
    disjointness narrowing, and a brief pointing anywhere else would point away from
    the lines the tool itself will judge the work against (law 3). The FIRST line of
    the first region — a place to start reading, never the symbol's extent, which is
    what :data:`brief.TOUCH_SYMBOL_LINE_NOTE` says out loud.

    Silent on everything it cannot place, at every step: a directory or new-file touch
    (no file yet, by construction), the additive claim (``path#+`` names what does not
    exist), a file that has moved since the touch was declared, a binary or undecodable
    one, a symbol the textual search cannot find. Each contributes no entry and the
    brief renders the bare symbol exactly as it did before — the number is an
    affordance, and an affordance that fails must cost nothing.
    """
    found: dict[str, dict[str, int]] = {}
    contents: dict[str, str | None] = {}
    for doc in documents:
        for touch in doc.get("touches") or ():
            path, symbol = touch.get("path"), touch.get("symbol")
            if not path or not symbol or sweep_module.is_additive_symbol(symbol):
                continue
            if is_directory_touch(path) or is_new_file_touch(path):
                continue
            if path not in contents:
                target = code_root / path
                try:
                    contents[path] = target.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError, ValueError):
                    contents[path] = None
            content = contents[path]
            if content is None:
                continue
            regions = sweep_module.symbol_regions(content, symbol)
            if not regions:
                continue
            found.setdefault(path, {})[symbol] = min(region.start for region in regions)
    return found


def _drift_not_checked(store, dispatch, *, detail: str, source: str | None = None) -> dict:
    """A drift finding that examined nothing, shaped exactly like one that did.

    KYO-85.c2: every ``not-checked`` return in this module comes from here, so a new
    reason to be unable to answer cannot accidentally acquire a "none" rendering by
    being written out longhand next to the others. ``detail`` is required — an outcome
    that says "I could not tell" and cannot say what it could not tell is the shrug
    law 6 refuses.
    """
    return {
        "dispatch_id": dispatch["id"],
        "record_id": dispatch["record_id"],
        "covers": commands.dispatch_covers(dispatch),
        "branch": dispatch["branch"],
        "outcome": DRIFT_NOT_CHECKED,
        "source": source,
        "detail": detail,
        "changed_files": [],
        "claimed_files": sorted(commands._claim_of(
            store, dispatch["record_id"], commands.dispatch_covers(dispatch)
        ).files),
        "undeclared_files": [],
        "unused_claims": [],
        "touches_file_lines": [],
        "unreported_by_worker": [],
        "not_yet_in_this_checkout": [],
        # KYO-307: no lines at all, so none of them were corrected from `+path`.
        "landed_new_files": [],
        # KYO-436: no lines, so none were dropped either.
        "gone_paths_dropped": [],
        "apply_command": None,
        # KYO-300: shaped exactly like a finding that did examine something, which is
        # what this function is for. A run that examined no files asked no region
        # question either, and says so rather than carrying two empty lists.
        "symbol_check": (
            f"{SYMBOL_CHECK_NOT_RUN}: nothing was examined at all, so nothing was "
            "examined at symbol level either"
        ),
        "outside_claimed_symbols": [],
        "additive_claims_broken": [],
    }


#: What a ``symbol_check`` says when nothing asked the region question. Law 6 again: a
#: drift finding that carried empty ``outside_claimed_symbols`` and ``additive_claims``
#: with no way to tell "checked and clean" from "never asked" would be the absence bug
#: this whole command exists to fix, one level down.
SYMBOL_CHECK_RUN = "run"
SYMBOL_CHECK_NOT_RUN = "not-run"


def _touch_lines_of(store, record_id: str) -> list[str]:
    """One record's touches as ``--touches-file`` lines — ``path``, ``path#symbol``.

    KYO-300.c2 found this: ``touches_file_lines`` was built from ``_claim_of(...).per``,
    which is the FILE-level set, so the correction `kyo touch-drift` printed (and
    `kyo edit --touches-from-drift` applied) silently flattened every ``path#symbol``
    touch to its bare path. A command that has just told a controller which symbol to
    add must not, in its next breath, hand them a list that deletes the symbols they
    already had.
    """
    return sorted(
        row["path"] + (f"#{row['symbol']}" if row["symbol"] else "")
        for row in store.touches(record_id)
    )


def _line_path(line: str) -> str:
    """The path half of a ``--touches-file`` line, with any ``#symbol`` taken off."""
    return line.partition("#")[0]


def _symbol_drift(
    store,
    record_id: str,
    covers: list[str],
    considered: list[str],
    *,
    hunks_for: Callable[[str], list[sweep_module.DiffHunk] | None] | None,
    content_for: Callable[[str], str | None] | None,
) -> tuple[list[dict], list[dict], str]:
    """KYO-300.c2/c4: the two findings that live INSIDE a claimed file.

    File-level drift asks "did this branch edit a file nothing claimed". These ask the
    two questions that are invisible to it:

    * **c2** — the branch edited a file this record claims *by symbol*, outside every
      symbol it named. Not undeclared, so the drift list above has nothing to say about
      it, and yet the record's claim is exactly as wrong as if the file were undeclared:
      the next dispatch that narrows on those symbols will be told it is out of the way
      when it is not. The finding names the symbols to add.
    * **c4** — the branch changed or removed lines of a file it claimed only additively
      (``path#+``). An additive claim is a *promise*, and a promise nothing checks is a
      claim of disjointness with no evidence behind it.

    Returns ``(outside, additive, check)``. ``check`` is ``SYMBOL_CHECK_NOT_RUN`` with a
    reason whenever the hunks could not be had at all — from a report's
    ``touched_files[]``, which names files and knows nothing about lines, or from a
    branch git cannot diff. Empty lists then mean "not asked", never "clean".
    """
    if hunks_for is None or content_for is None:
        return (
            [],
            [],
            f"{SYMBOL_CHECK_NOT_RUN}: the evidence for this finding is a list of FILES, "
            "so which lines of them changed — and therefore which symbol's region — "
            "cannot be asked of it",
        )
    symbols: dict[str, set[str | None]] = {}
    for rid in (record_id, *covers):
        for path, named in commands._touch_symbols(store, rid).items():
            symbols.setdefault(path, set()).update(named)
    outside: list[dict] = []
    additive: list[dict] = []
    # KYO-406.c4: paths whose region question could not be asked -- the diff or the
    # branch tip's copy of the file could not be read. Skipping them silently made
    # "could not look" read exactly like "looked and it was clean".
    unread: list[str] = []
    for path in considered:
        named = symbols.get(path)
        # A path claimed through a directory or new-file touch has no entry here, and a
        # claim that named the plain path carries `None` — either way the whole file is
        # claimed and there is nothing more precise to check.
        if not named or None in named:
            continue
        hunks = hunks_for(path)
        if hunks is None:
            unread.append(path)
            continue
        is_additive = ADDITIVE_SYMBOL in named
        real = sorted(s for s in named if s and not sweep_module.is_additive_symbol(s))
        if is_additive:
            # The additive claim covers every hunk that only ADDS. What it says nothing
            # about — and what c4 holds it to — is a hunk that rewrote or deleted lines
            # that were already there.
            broken = [hunk for hunk in hunks if hunk.removes_existing]
            if broken and not real:
                additive.append(
                    {
                        "path": path,
                        "hunks": len(broken),
                        "lines_removed": sum(hunk.old_count for hunk in broken),
                    }
                )
            hunks = broken
        if not real or not hunks:
            continue
        content = content_for(path)
        if content is None:
            unread.append(path)
            continue
        suggest = sweep_module.changed_outside_claimed_symbols(
            real,
            hunks,
            content=content,
            markdown=path.lower().endswith((".md", ".markdown")),
            regions_for=lambda symbol, text=content: sweep_module.symbol_regions(
                text, symbol
            ),
        )
        if suggest is None:
            continue
        outside.append(
            {
                "path": path,
                "claimed": real,
                "suggested_touches": [f"{path}#{name}" for name in suggest] or [path],
            }
        )
    if unread:
        return (
            outside,
            additive,
            f"{SYMBOL_CHECK_NOT_RUN} for {', '.join(sorted(unread))}: the branch's diff "
            "or its tip's copy of the file could not be read, so which claimed symbol's "
            "region those edits fell in was not asked — that is not a clean answer",
        )
    return outside, additive, SYMBOL_CHECK_RUN


def touch_drift(
    store,
    dispatch,
    changed: list[str],
    *,
    source: str,
    examined_nothing: str,
    hunks_for: Callable[[str], list[sweep_module.DiffHunk] | None] | None = None,
    content_for: Callable[[str], str | None] | None = None,
) -> dict:
    """KYO-71: which of ``changed`` no record this dispatch covers ever claimed.

    The touches table is a **prediction**, written before the work (KYO-48 was the
    undeclared claim, KYO-65 the unconsulted one; this is the declared but incomplete
    one). So this **reports**; nothing here refuses. A worker cannot declare a file it
    has not yet discovered it needs; the goal is that the RECORD ends up true
    (docs/design/dispatch.md#kyo-71).

    KYO-85.c2, and the reason ``examined_nothing`` is a required argument: an empty
    ``changed`` is never a clean bill. Whatever the source, nothing was examined, and
    the one place that could render "none" for that is here — so it is here that it is
    refused, structurally, rather than at each of the callers that would have to
    remember. The caller supplies the sentence saying *why* its evidence was empty,
    because only the caller knows.
    """
    if not changed:
        return _drift_not_checked(store, dispatch, detail=examined_nothing, source=source)
    covers = commands.dispatch_covers(dispatch)
    record_id = dispatch["record_id"]
    claim = commands._claim_of(store, record_id, covers)
    considered = _considered(changed)
    # KYO-209: neither of these is set arithmetic any more, because a directory touch
    # claims what is under it and set difference cannot see that. A branch that creates
    # `apps/desktop/main.js` under a declared `apps/desktop/` has done exactly what its
    # record said it would, and the whole value of this command is that it distinguishes
    # that from a branch that wandered.
    undeclared = sorted(path for path in considered if not claim_covers(claim.files, path))
    unused = sorted(
        declared
        for declared in claim.files
        if not any(touch_covers(declared, path) for path in considered)
    )
    # KYO-300.c2: the record's OWN lines, symbols and all — see :func:`_touch_lines_of`
    # for why this is no longer `claim.per[record_id]`.
    touches_file_lines = sorted(set(_touch_lines_of(store, record_id)) | set(undeclared))
    outside, additive, symbol_check = _symbol_drift(
        store,
        record_id,
        covers,
        considered,
        hunks_for=hunks_for,
        content_for=content_for,
    )
    return {
        "dispatch_id": dispatch["id"],
        "record_id": record_id,
        "covers": covers,
        "branch": dispatch["branch"],
        "outcome": DRIFT_FOUND if undeclared else DRIFT_NONE,
        # KYO-85: which body of evidence this is a finding about. A consumer that
        # cannot tell a report-sourced answer from a git-sourced one cannot tell how
        # much the answer is worth.
        "source": source,
        "detail": None,
        "changed_files": considered,
        "claimed_files": sorted(claim.files),
        # Files edited outside anything this dispatch claimed. The actionable half.
        "undeclared_files": undeclared,
        # Claimed and never touched. Reported but never treated as an error: a
        # prediction that was cautious is not a prediction that was wrong, and a
        # record whose work is not finished has claims it has not reached yet.
        "unused_claims": unused,
        # KYO-71.c4: exactly what `kyo edit <record> --touches-file FILE` would need,
        # as lines. --touches-file REPLACES the list, so this is the whole corrected
        # claim (existing paths plus the drift), not a diff a controller would have
        # to merge by hand. Attributed to the PRIMARY record deliberately: dividing
        # new files among covered records would be the tool guessing at authorship,
        # and the primary record is the one whose dispatch this is and the one a
        # controller opens.
        "touches_file_lines": touches_file_lines,
        # KYO-85.c3: the same lines as one runnable command, so the correction is
        # applied rather than retyped. See :func:`_apply_command`.
        "apply_command": _apply_command(record_id, touches_file_lines),
        # KYO-85: filled in by the caller when a report was preferred AND git could
        # still answer — files the branch changed that the report did not name.
        "unreported_by_worker": [],
        # Filled in by the caller, which knows the code root. See
        # :func:`_drift_for_dispatch`.
        "not_yet_in_this_checkout": [],
        # KYO-307: same — which `+path` lines above were corrected to the plain path
        # because the file they claimed now exists. See :func:`_land_new_file_touches`.
        "landed_new_files": [],
        # KYO-436: same — which lines were dropped because the file they name is gone
        # from this checkout. See :func:`_drop_gone_paths`.
        "gone_paths_dropped": [],
        # KYO-300.c2/c4: the two findings inside a file the claim already covers. See
        # :func:`_symbol_drift`; `symbol_check` says whether they were asked at all.
        "symbol_check": symbol_check,
        "outside_claimed_symbols": outside,
        "additive_claims_broken": additive,
    }


def _apply_command(record_id: str, lines: list[str]) -> str | None:
    """KYO-85.c3: the corrected touch list as one command that applies it.

    The cheaper half of the same defect: the tool printed the right list and then
    required it to be written into a file by hand and passed to ``--touches-file``,
    once per dispatch, although the tool already held the data.

    ``--touches-file`` already reads stdin when handed ``-`` (law 5's write path:
    prose enters by file or stdin, never as an argument), so no new flag and no
    temporary file are needed — only a line that is runnable as printed. Each path
    goes through ``shlex.quote`` for the same reason KYO-82's message does, and KYO-89
    is why that branch is now reachable from a worker's own checkout.
    """
    if not lines:
        return None
    quoted = " ".join(shlex.quote(line) for line in lines)
    return f"printf '%s\\n' {quoted} | kyo edit {record_id} --touches-file -"


def _reported_touched_files(store, dispatch) -> list[str] | None:
    """The ``touched_files[]`` of this dispatch's ingested report, or ``None``.

    ``None`` for a dispatch with no report, a report that omitted the (optional) key,
    and a report that carried it **empty**. The last is deliberate: an empty list is a
    statement, but it is not evidence about which files were edited, and preferring it
    over a git diff that can still answer would replace a working check with a shrug.
    An empty declaration therefore falls through to git, and if git cannot answer
    either the outcome is ``not-checked`` — never "none".
    """
    report_id = dispatch["report_id"]
    if not report_id:
        return None
    try:
        report = store.report(report_id)
    except KyoseiError:
        return None
    try:
        body = json.loads(report["body_json"])
    except (TypeError, ValueError):  # pragma: no cover - a report is validated at ingest
        return None
    files = body.get("touched_files") if isinstance(body, dict) else None
    if not isinstance(files, list):
        return None
    named = [str(path).strip() for path in files if str(path).strip()]
    return named or None


def _reported_commits(store, dispatch) -> list[str] | None:
    """The shas of this dispatch's ingested report's ``commits[]``, or ``None``.

    KYO-344's evidence, read the same defensive way :func:`_reported_touched_files`
    reads ``touched_files[]``: ``None`` for a dispatch with no report, an unreadable
    body, or a report that carried no commit with a sha. This only reads what the
    report SAYS — whether those shas are actually reachable from the trunk is asked
    where they are used (:func:`_drift_from_commits`), the same division
    :func:`_reported_touched_files` keeps from the comparison its own list is put to.
    """
    report_id = dispatch["report_id"]
    if not report_id:
        return None
    try:
        report = store.report(report_id)
    except KyoseiError:
        return None
    try:
        body = json.loads(report["body_json"])
    except (TypeError, ValueError):  # pragma: no cover - a report is validated at ingest
        return None
    commits = body.get("commits") if isinstance(body, dict) else None
    if not isinstance(commits, list):
        return None
    shas = [
        str(commit.get("sha", "")).strip()
        for commit in commits
        if isinstance(commit, dict) and str(commit.get("sha", "")).strip()
    ]
    return shas or None


def _drift_from_commits(ctx: Context, store, dispatch, against: str) -> dict | None:
    """KYO-344: the fallback tried only once the branch no longer resolves and the
    report named no ``touched_files[]`` — the exact gap the consumer's report is
    about: "the report's ``commits[]`` were already verified reachable and are on
    main after the merge -- drift could be computed from them rather than from the
    branch name."

    Returns ``None`` whenever the commits cannot answer either — no report, no
    commits, a commit not (or not yet) reachable from ``against``, or a diff git could
    not read — which the caller folds into the not-checked detail rather than reading
    as "examined and found nothing" (law 6). A single unreachable sha refuses the
    whole fallback rather than silently computing drift from a partial set: the point
    of asking reachability first is that a commit git cannot place on the trunk is not
    evidence about what landed there.
    """
    shas = _reported_commits(store, dispatch)
    if not shas:
        return None
    for sha in shas:
        if _git_is_ancestor(ctx.code_root, sha, against) is not True:
            return None
    changed: set[str] = set()
    for sha in shas:
        files = commit_changed_files(ctx.code_root, sha)
        if files is None:
            return None
        changed.update(files)
    drift = touch_drift(
        store,
        dispatch,
        sorted(changed),
        source=DRIFT_SOURCE_COMMITS,
        examined_nothing=(
            f"report {dispatch['report_id']}'s commits[] ({len(shas)}) named no files "
            "that changed at all"
        ),
        # No branch ref survives to ask the region question with — see
        # DRIFT_SOURCE_COMMITS's own note.
        hunks_for=None,
        content_for=None,
    )
    return _mark_absent_paths(ctx, _drop_gone_paths(ctx, _land_new_file_touches(ctx, drift)))


def drift_lines(drift: dict) -> list[str]:
    """One drift finding, rendered (law 3: the text output and ``--json`` are two
    renderings of :func:`touch_drift`'s single computation, never two computations).
    """
    if drift["outcome"] == DRIFT_NOT_CHECKED:
        return [
            "touch drift: NOT CHECKED — "
            + (drift.get("detail") or "no branch was declared for this dispatch, so there "
               "is no diff to compare a claim against")
        ]
    # KYO-85: every sentence below says which evidence it read. "none" meant two
    # different things depending on what was examined, and the whole defect was that
    # the output did not distinguish them.
    if drift["source"] == DRIFT_SOURCE_REPORT:
        where = "the report's touched_files[]"
    elif drift["source"] == DRIFT_SOURCE_COMMITS:
        # KYO-344.c1: says IN THE OUTPUT that it fell back because the branch is gone,
        # not only in the finding's `source` field a text reader may never look at.
        where = f"the report's commits[] (branch {drift['branch']} no longer exists)"
    else:
        where = f"the diff of {drift['branch']}"
    if drift["outcome"] == DRIFT_NONE:
        lines = [
            f"touch drift: none — all {len(drift['changed_files'])} file(s) "
            f"{where} names are claimed by "
            + ", ".join([drift["record_id"], *drift["covers"]])
        ]
        if drift.get("landed_new_files") or drift.get("gone_paths_dropped"):
            # KYO-307: the claim is right about WHICH files and stale in the FORM of
            # one line, which is the commonest outcome of all — a dispatch that adds
            # exactly the file its record said it would drifts nowhere. `kyo edit
            # --touches-from-drift` still rewrites those lines, so the correction is
            # printed here too; a "none" that hid it would be a controller watching
            # their record change after being told nothing had.
            lines.append(
                f"every file is claimed, and one form of claim has gone stale. Correct "
                f"{drift['record_id']} with `kyo edit {drift['record_id']} "
                f"--touches-from-drift {drift['dispatch_id']}`, which stores exactly "
                "these lines:"
            )
            lines += [f"    {path}" for path in drift["touches_file_lines"]]
            lines += _landed_new_file_lines(drift["landed_new_files"])
            # KYO-436: a claimed file the branch deleted drifts nowhere either, and the
            # correction still changes the record, so it is printed for the same reason.
            lines += _gone_path_lines(drift.get("gone_paths_dropped") or [])
        return lines + _symbol_drift_lines(drift) + _cross_check_lines(drift)
    who = ", ".join([drift["record_id"], *drift["covers"]])
    lines = [
        f"touch drift: {len(drift['undeclared_files'])} file(s) named by {where} "
        f"that no record this dispatch covers ({who}) declared "
        f"({explanations.say('drift-reported-not-refused')}):",
    ]
    lines += [f"  + {path}" for path in drift["undeclared_files"]]
    lines.append(
        f"correct {drift['record_id']} with `kyo edit {drift['record_id']} "
        "--touches-file FILE`, where FILE holds exactly these lines:"
    )
    lines += [f"    {path}" for path in drift["touches_file_lines"]]
    # KYO-307.c2: printed beside the lines it changed, so what a controller reads here
    # and what `kyo edit --touches-from-drift` stores are the same list AND the same
    # explanation of it.
    lines += _landed_new_file_lines(drift.get("landed_new_files") or [])
    lines += _gone_path_lines(drift.get("gone_paths_dropped") or [])
    lines += _unlanded_new_file_lines(drift)
    if drift.get("apply_command"):
        # KYO-85.c3: the list above, as something to run. Printed beside the lines
        # rather than instead of them — a controller reviewing a correction wants to
        # read it before applying it, and the whole point is that reading it is no
        # longer the step before retyping it.
        lines.append("or apply it without writing a file, exactly as printed:")
        lines.append(f"    {drift['apply_command']}")
    if drift.get("not_yet_in_this_checkout"):
        # Found while building this: `kyo edit --touches-file` validates every path
        # against the working tree (charter, Layer 1 — "staleness is guarded at the
        # boundary"), and a file a worker CREATED exists only on its branch until the
        # merge. So the correction this command hands over is refused if applied too
        # early, and the honest thing is to say when rather than to weaken the
        # boundary check that catches real typos.
        lines.append(
            f"  ({len(drift['not_yet_in_this_checkout'])} of those path(s) do not exist "
            "in this checkout yet — "
            + ", ".join(drift["not_yet_in_this_checkout"])
            + f" — because the branch created them. Merge {drift['branch']} first; "
            "`kyo edit --touches-file` validates paths against the working tree and "
            "will refuse them until then)"
        )
    # KYO-436: a claim dropped because its file is gone is named by the line above,
    # and is not "kept above", so it is not repeated here as if it were.
    gone = {_line_path(e["line"]) for e in drift.get("gone_paths_dropped") or []}
    unused = [path for path in drift["unused_claims"] if path not in gone]
    if unused:
        lines.append(
            f"  ({len(unused)} claimed path(s) were not touched: "
            + ", ".join(unused)
            + " — kept above, since an unfinished record has claims it has not reached)"
        )
    return lines + _symbol_drift_lines(drift) + _cross_check_lines(drift)


def _symbol_drift_lines(drift: dict) -> list[str]:
    """KYO-300.c2/c4, rendered — the findings a file-level drift list cannot carry.

    Printed under both outcomes, including ``none``: "every file you touched is claimed"
    and "you edited one of them outside the symbols you claimed" are both true at once,
    and the second is the one a controller has to act on. A file-level clean bill that
    swallowed it would be the same false reassurance KYO-85 was filed about.
    """
    lines: list[str] = []
    for entry in drift.get("outside_claimed_symbols") or []:
        claimed = ", ".join("#" + name for name in entry["claimed"])
        lines.append(
            f"  claimed by symbol, edited outside it: {entry['path']} is claimed at "
            f"{claimed}, and this branch changed lines in it that no claimed symbol's "
            "region covers. This is NOT an unclaimed file — the path is in the claim — "
            "so the correction is a touch to ADD, not a path to add:"
        )
        lines += [f"    {line}" for line in entry["suggested_touches"]]
    for entry in drift.get("additive_claims_broken") or []:
        lines.append(
            f"  additive claim not kept: {entry['path']} is claimed `#"
            f"{ADDITIVE_SYMBOL}` — additions only — and this branch changed "
            f"or removed {entry['lines_removed']} existing line(s) of it across "
            f"{entry['hunks']} hunk(s). Either name the symbols it edited, or claim the "
            "plain path, which claims the whole file"
        )
    if lines:
        lines.append(f"  ({sweep_module.SYMBOL_REGION_RULE})")
    # KYO-406.c4: printed beside findings too, since a check that ran for some paths
    # and not for others must still name the ones it did not run for.
    if str(drift.get("symbol_check") or "").startswith(SYMBOL_CHECK_NOT_RUN):
        lines.append(f"  (symbol-level drift {drift['symbol_check']})")
    return lines


def _cross_check_lines(drift: dict) -> list[str]:
    """KYO-85: the declaration, checked against the diff while the diff still works.

    Preferring ``touched_files[]`` answers the merged-branch case at the cost of
    comparing one declaration against another — which is the exact failure mode KYO-71
    was filed about. This is what keeps that honest: where a report was preferred and
    git could *also* answer, the two are compared and the difference is named. It is
    not a refusal and not a drift finding; it is a fact about the report.
    """
    missing = drift.get("unreported_by_worker") or []
    if not missing:
        return []
    return [
        f"  (note: {len(missing)} file(s) the branch changed are not in the report's "
        "touched_files[]: "
        + ", ".join(missing)
        + " — the finding above is computed from the report, which is the list that "
        "survives the merge; this line is the diff disagreeing with it while it still "
        "can)"
    ]


def _drift_for_dispatch(ctx: Context, store, dispatch, against: str) -> dict:
    """:func:`touch_drift` against the best evidence this dispatch has, or a stated
    "not checked" when it has none.

    KYO-71.c3, decided rather than assumed: **this fires at report-ingest time**, and
    on demand via `kyo touch-drift`. The trade, both halves of it:

    * **Report time (chosen).** The branch is complete, every commit is in, and the
      store is right there — which matters, because a worker's worktree holds no
      ``kyosei.db`` under the in-repo layout, so the comparison is literally not
      computable from inside a worker. It is also the exact moment a controller would
      correct the record: this project's controller corrected touches by hand after
      nearly every dispatch, and every one of those was a diff the tool could have
      shown. It is too late to prevent a concurrent collision.
    * **Worker stop time (not chosen).** `kyo check-clean`/`kyo check-branch` run
      earlier and could warn sooner, but they run in the worker's worktree where the
      store may be unreachable, and a worker's scope is incomplete mid-work — it
      cannot declare a file it has not yet discovered it needs. Preventing the
      concurrent collision entirely is probably not achievable at all, which is why
      the goal is that the RECORD ends up true rather than that the prediction was.

    KYO-85 adds the order of evidence, which is the whole fix:

    1. the ingested report's ``touched_files[]``, whenever there is one — no ref, no
       merge base, no surviving branch, and identical whenever it is asked;
    2. otherwise the three-dot diff, for a dispatch that has not reported yet — but
       only after asking whether the branch is already merged, because a merged branch
       makes that diff empty by construction and an empty diff is not a clean bill;
    3. otherwise ``not-checked``, saying which of the two could not be had.
    """
    branch = dispatch["branch"]
    # KYO-300.c2/c4: which lines of a claimed file this branch changed. Asked of the
    # SAME three-dot range the file list comes from, and `None` whenever that range is
    # not available (no branch, merged branch, git missing) — which the symbol checks
    # then report as "not run" rather than as "clean".
    hunks_for, content_for = _line_evidence(ctx, branch, against)

    reported = _reported_touched_files(store, dispatch)
    if reported is not None:
        drift = touch_drift(
            store,
            dispatch,
            reported,
            source=DRIFT_SOURCE_REPORT,
            examined_nothing=(
                f"the report ingested for {dispatch['id']} named no files in "
                "touched_files[], so there is nothing to compare its claim against"
            ),
            # A report names files and the region question is about lines, so the
            # evidence for THIS half is the diff whenever the diff can still be had —
            # the same asymmetry `_cross_check_lines` already rests on.
            hunks_for=hunks_for,
            content_for=content_for,
        )
        drift["unreported_by_worker"] = _git_disagreement(ctx, branch, against, reported)
        return _mark_absent_paths(
            ctx, _drop_gone_paths(ctx, _land_new_file_touches(ctx, drift))
        )

    if not branch:
        return _drift_not_checked(
            store,
            dispatch,
            detail=(
                f"dispatch {dispatch['id']} declared no branch (`kyo dispatch --branch "
                "NAME`) and has ingested no report, so there is neither a diff nor a "
                "touched_files[] list to compare its touch claim against"
            ),
        )

    # KYO-85: asked BEFORE the diff, because the diff cannot tell. `git diff
    # against...branch` is three-dot, so a merged branch's merge base is its own tip
    # and the answer is an empty list for a branch that changed a hundred files.
    merged = branch_is_contained_in(ctx.code_root, branch, against)
    if merged:
        return _drift_not_checked(
            store,
            dispatch,
            detail=(
                f"{branch} is already merged into {against}, so the three-dot diff "
                f"`git diff {against}...{branch}` compares {branch} against itself and "
                "examines nothing — an empty answer here is the absence of a finding, "
                f"not a clean one. File {dispatch['id']}'s report with a touched_files[] "
                "list (`kyo report`) and this command answers from that instead, with no "
                "ref of any kind"
            ),
        )

    if merged is None and not _git_commit_exists(ctx.code_root, branch):
        # KYO-344: not "no branch declared" (above) -- one was, and it is simply gone
        # from this checkout now (the ordinary shape of "the merged branch was
        # deleted"). Before giving up, try the report's own commits[]: `kyo report`
        # already proved them reachable from the branch at ingest time (KYO-32), and
        # once they are ALSO reachable from `against` they answer the same question
        # the branch diff no longer can.
        from_commits = _drift_from_commits(ctx, store, dispatch, against)
        if from_commits is not None:
            return from_commits
        return _drift_not_checked(
            store,
            dispatch,
            detail=(
                f"{branch} no longer resolves in {ctx.code_root} (it looks deleted, "
                f"most likely after the merge), and {dispatch['id']}'s report's "
                "commits[] could not answer either (missing, empty, not every sha "
                f"reachable from {against}, or the diff could not be read) — so "
                "neither the branch, a reachable commits[], nor the report's "
                f"touched_files[] can say what changed. Restore {branch}, or file "
                "(or amend) a report whose commits[] are every one reachable from "
                f"{against}, or whose touched_files[] names what it changed"
            ),
        )

    try:
        changed = branch_changed_files(ctx.code_root, against, branch)
    except KyoseiError as exc:
        return _drift_not_checked(
            store,
            dispatch,
            detail=(
                f"the diff could not be computed: {exc}. A dispatch whose report has been "
                "ingested is answered from its touched_files[] and needs no ref at all"
            ),
        )
    drift = touch_drift(
        store,
        dispatch,
        changed,
        source=DRIFT_SOURCE_GIT,
        examined_nothing=(
            f"`git diff {against}...{branch}` reported no changed files at all. That is "
            "not a claim that nothing drifted: an empty three-dot diff is what a merged, "
            "rebased or empty branch produces, and nothing here can tell which"
        ),
        hunks_for=hunks_for,
        content_for=content_for,
    )
    return _mark_absent_paths(ctx, _drop_gone_paths(ctx, _land_new_file_touches(ctx, drift)))


def _line_evidence(
    ctx: Context, branch: str | None, against: str
) -> tuple[
    Callable[[str], list[sweep_module.DiffHunk] | None] | None,
    Callable[[str], str | None] | None,
]:
    """The two closures :func:`_symbol_drift` needs, or ``(None, None)`` (KYO-300).

    Memoised per call, because a branch that edits README.md is asked about README.md
    once per record the dispatch covers, and the file's text is asked for again per
    symbol claimed on it.

    ``(None, None)`` — never a pair of closures that quietly answer "nothing" — for a
    dispatch with no branch and for a branch already merged into ``against``, which is
    the same condition that makes the three-dot diff lie (KYO-85). The caller renders
    that as "not run", so nothing here can turn "could not look" into "looked and it
    was clean".
    """
    if not branch or branch_is_contained_in(ctx.code_root, branch, against) is not False:
        return None, None
    revs = [f"{against}...{branch}"]
    tip: str = branch
    hunks: dict[str, list[sweep_module.DiffHunk] | None] = {}
    text: dict[str, str | None] = {}

    def hunks_for(path: str) -> list[sweep_module.DiffHunk] | None:
        if path not in hunks:
            hunks[path] = _git_diff_hunks(ctx.code_root, revs, path)
        return hunks[path]

    def content_for(path: str) -> str | None:
        # KYO-406.c3: the hunks are `against...branch`, so their new-side line numbers
        # belong to the branch TIP, and the regions must be placed in the tip's copy of
        # the file. The controller's checkout has none of the branch's edits (and may
        # have its own), so reading it there reported edits inside a claimed symbol as
        # outside it.
        if path not in text:
            text[path] = _git_file_at(ctx.code_root, tip, path)
        return text[path]

    return hunks_for, content_for


def _git_disagreement(
    ctx: Context, branch: str | None, against: str, reported: list[str]
) -> list[str]:
    """Files the branch changed that the report never named — empty whenever git
    cannot answer, which is most of the time this runs. See :func:`_cross_check_lines`.
    """
    if not branch or branch_is_contained_in(ctx.code_root, branch, against) is not False:
        return []
    try:
        changed = branch_changed_files(ctx.code_root, against, branch)
    except KyoseiError:
        return []
    return sorted(set(_considered(changed)) - set(reported))


def _mark_absent_paths(ctx: Context, drift: dict) -> dict:
    """KYO-71.c4's one real limit, stated rather than hidden: a file the worker
    *created* exists only on its branch, so the correction this command hands over
    cannot be applied until the merge — `kyo edit --touches-file` validates paths
    against the working tree on purpose.

    KYO-209: a directory touch is exempt, and getting that wrong would be a lie rather
    than a nuisance. This list is a statement about what `kyo edit --touches-file`
    would REFUSE right now, and it refuses nothing about a directory touch — that form
    is accepted whether or not the tree exists. Listing an absent `apps/desktop/` here
    would tell a controller to wait for a merge it does not need.

    KYO-543.c2: the limit is no longer left standing for a file the branch provably
    created. An undeclared plain line that is absent here, present at the branch tip,
    and whose tip is NOT an ancestor of HEAD (the merge has not landed) is rewritten
    as ``+path`` — the form `kyo edit --touches-file` accepts for a file this work
    creates and that does not exist yet — so `kyo accept` run before merging stores
    the correction instead of refusing it (a consumer project's APP-58.d1 and
    APP-61.d1: a migration and a notes fragment). After the merge,
    :func:`_land_new_file_touches`
    (KYO-307) turns it back into the plain path. Only that evidence converts a line:
    with no branch to ask, or a merged tip (whose absent file is
    :func:`_drop_gone_paths`' business), the line stays plain and is listed below as
    waiting on a merge, exactly as before.
    """
    _mark_unlanded_new_files(ctx, drift)
    drift["not_yet_in_this_checkout"] = [
        line
        for line in drift["touches_file_lines"]
        # KYO-300.c2: a line may now carry `#symbol`, which is a statement about the
        # touch and not part of the path — the same relation `touch_target` holds for
        # the `+` marker. Asking the filesystem about `src/x.py#foo` would report every
        # symbol touch as a file waiting on a merge.
        if not is_directory_touch(_line_path(line))
        and not is_new_file_touch(_line_path(line))
        and not (ctx.code_root / _line_path(line)).exists()
    ]
    return drift


def _mark_unlanded_new_files(ctx: Context, drift: dict) -> None:
    """KYO-543.c2, the conversion :func:`_mark_absent_paths` describes. In place."""
    branch = drift.get("branch")
    undeclared = set(drift.get("undeclared_files") or [])
    candidates = [
        line
        for line in drift["touches_file_lines"]
        if line in undeclared
        and not is_directory_touch(line)
        and not is_new_file_touch(line)
        and not (ctx.code_root / line).exists()
    ]
    if not candidates or not branch or not _git_commit_exists(ctx.code_root, branch):
        return
    if _git_is_ancestor(ctx.code_root, branch, "HEAD") is not False:
        return
    marked = {
        line
        for line in candidates
        if _git_file_at(ctx.code_root, branch, line) is not None
    }
    if not marked:
        return
    # In place, not re-sorted: the list is ordered by path, and the marker is not
    # part of the path (the same order :func:`_land_new_file_touches` restores).
    drift["touches_file_lines"] = [
        f"{TOUCH_NEW_FILE_PREFIX}{line}" if line in marked else line
        for line in drift["touches_file_lines"]
    ]
    drift["apply_command"] = _apply_command(drift["record_id"], drift["touches_file_lines"])


def _unlanded_new_file_lines(drift: dict) -> list[str]:
    """KYO-543.c2, rendered from the correction itself rather than a second list: an
    undeclared path that appears in it as ``+path`` is one the conversion marked."""
    undeclared = set(drift.get("undeclared_files") or [])
    marked = [
        line
        for line in drift["touches_file_lines"]
        if is_new_file_touch(line) and line[len(TOUCH_NEW_FILE_PREFIX) :] in undeclared
    ]
    if not marked:
        return []
    return [
        f"  ({len(marked)} line(s) name a file this branch created that is not in this "
        f"checkout yet, because {drift.get('branch')} is not merged, so the correction "
        f"claims it as a new file: " + ", ".join(marked)
        + f" — `kyo edit` accepts '{TOUCH_NEW_FILE_PREFIX}path' for a file that does "
        "not exist yet, and after the merge this command corrects it to the plain path)"
    ]


def _land_new_file_touches(ctx: Context, drift: dict) -> dict:
    """KYO-307: a ``+path`` whose file now EXISTS is corrected to the plain path.

    The drift correction is applied AFTER the merge, when a file the branch created now
    exists, and :func:`cmd_records._new_file_touch_problem` refuses ``+path`` for an
    existing file (KYO-225) — so without this, the correction for any dispatch that adds
    a file is refused (docs/design/dispatch.md#kyo-307).

    **The conversion belongs here and not in validation.** Two reasons, and the second
    is the one that decides it:

    * A validation that accepted `+path` for an existing file *when it came from a
      correction* would make the rule depend on who is calling, which is the second
      write path ``cmd_edit`` refuses to have (KYO-147.c4) — and a blanket acceptance
      would delete KYO-225.c1's honesty check, the only thing that keeps a `+` on a
      record from meaning nothing.
    * KYO-307.c2: what `kyo touch-drift` PRINTS must be what the flag applies, and must
      still paste into `kyo edit --touches-file`. Relaxing validation for one caller
      leaves the printed lines refused by the other. Correcting the lines themselves,
      in the single computation both renderings read (law 3), is the only place that
      holds for both at once.

    Only a marked path that is now a **file** is converted: a `+path` that has become a
    directory is left marked and meets the existing refusal, which is the honest answer
    for a claim that no longer describes anything. The directory form needs nothing
    here and gets nothing — `dir/` is accepted whether or not the tree exists, which is
    the same reason :func:`_mark_absent_paths` exempts it.
    """
    landed: list[dict] = []
    corrected: list[str] = []
    for line in drift["touches_file_lines"]:
        path = _line_path(line)
        if is_new_file_touch(path) and (ctx.code_root / touch_target(path)).is_file():
            plain = line[len(TOUCH_NEW_FILE_PREFIX) :]
            landed.append({"was": line, "now": plain})
            corrected.append(plain)
        else:
            corrected.append(line)
    if landed:
        drift["touches_file_lines"] = sorted(set(corrected))
        drift["apply_command"] = _apply_command(
            drift["record_id"], drift["touches_file_lines"]
        )
    drift["landed_new_files"] = landed
    return drift


def _landed_new_file_lines(landed: list[dict]) -> list[str]:
    """KYO-307.c1's "the command says which lines it converted", as one sentence.

    Rendered by both consumers — `kyo touch-drift`'s finding and `kyo edit
    --touches-from-drift`'s echo — from the same list, so a controller who reads the
    correction and a controller who applies it are told the same thing.
    """
    if not landed:
        return []
    return [
        f"  ({len(landed)} line(s) claimed a file this work would create "
        f"('{TOUCH_NEW_FILE_PREFIX}path') and that file now exists, so the correction "
        "names it as it stands: "
        + ", ".join(f"{entry['was']} -> {entry['now']}" for entry in landed)
        + f" — a '{TOUCH_NEW_FILE_PREFIX}' on a path that is already there is refused, "
        "and the merge is what made it already there)"
    ]


#: KYO-436: why a line left the correction. Named from the evidence the finding holds:
#: the path is in ``changed_files`` (this branch touched it and it is gone, so the touch
#: was its deletion) or it is not (it left the trunk by some other change).
#: KYO-543 adds the third: the branch tip still HAS the file, the tip is merged into
#: HEAD, and a later trunk commit deleted it — a notes fragment the branch created and
#: `kyo docs assemble` consumed on the trunk is the common shape of this. The branch
#: did not delete it, and "some other change" would hide that the
#: file was this branch's own.
GONE_DELETED_BY_THIS_BRANCH = "deleted-by-this-branch"
GONE_FROM_THE_TRUNK = "gone-from-the-trunk"
GONE_AFTER_THE_MERGE = "deleted-on-the-trunk-after-the-merge"


def _drop_gone_paths(ctx: Context, drift: dict) -> dict:
    """KYO-436: a line whose file no longer exists is dropped from the correction.

    Found accepting KYO-431.d1: the branch deleted ``src/kyosei/cmd_review.py``, which
    the record claimed; the correction kept it (every claimed line is kept, since "an
    unfinished record has claims it has not reached") and ``_validate_touches`` then
    refused the correction's own list, because a plain path must exist. A path the
    branch DELETED is not an unreached claim — the work reached it and removed it — and
    a claimed path another merge removed is not one this record can still touch. Both
    cases occur.

    Dropped here, in the single computation both renderings read, for KYO-307's reason:
    what `kyo touch-drift` prints must be what `kyo edit --touches-from-drift` stores
    and must still paste into ``--touches-file``. Validation stays exactly as strict.

    Which absent lines go, and the one that must NOT:

    * a line the record already claimed (not in ``undeclared_files``) — a plain claim
      was validated as existing when it was written, so if it is absent now something
      removed it, and no merge still to come brings it back;
    * an undeclared line only on positive evidence that it was deleted: the checkout's
      own history records the deletion (the branch deleted it and is merged, or another
      change did), and either the branch tip, where it still resolves, does not have
      it, or the tip has it and is an ancestor of HEAD (KYO-543). The second is a file
      the branch created that the trunk deleted *after* the merge landed — a notes
      fragment `kyo docs assemble` consumed — and is dropped as
      :data:`GONE_AFTER_THE_MERGE`;
    * **kept**: any other absent undeclared line — above all a file the branch CREATED
      that the merge has not landed (the tip has it and is NOT an ancestor of HEAD),
      which is absent here for the opposite reason. That is KYO-71's caveat, stated by
      :func:`_mark_absent_paths`; it must go on being refused, and dropping it would
      erase real drift. Absence alone cannot tell the two apart (a report-only finding
      may have no branch to ask at all), so the deletion itself is what is asked for,
      and whether the tip is merged is what separates "created, not landed" from
      "landed, then deleted" — not whether the tip has the file (KYO-543: the tip of a
      merged branch keeps every file the trunk later removed). Ancestry git cannot
      answer counts as not merged, so the line is kept.

    Directory and ``+path`` lines are never dropped: neither form requires the path to
    exist (the same exemptions :func:`_mark_absent_paths` makes).
    """
    undeclared = set(drift.get("undeclared_files") or [])
    changed = set(drift.get("changed_files") or [])
    branch = drift.get("branch")
    tip = branch if branch and _git_commit_exists(ctx.code_root, branch) else None
    tip_merged: list[bool] = []  # asked once, and only if a tip has an absent file

    def merged() -> bool:
        if not tip_merged:
            tip_merged.append(_git_is_ancestor(ctx.code_root, tip, "HEAD") is True)
        return tip_merged[0]

    def deleted_here(path: str) -> str | None:
        """Why ``path`` left, or None when it must be kept."""
        at_tip = tip is not None and _git_file_at(ctx.code_root, tip, path) is not None
        if at_tip and not merged():
            return None
        try:
            found = run_git(
                ctx.code_root, "log", "-1", "--diff-filter=D", "--format=%H", "HEAD",
                "--", path,
            )
        except OSError:
            return None
        if found.returncode != 0 or not found.stdout.strip():
            return None
        if at_tip:
            return GONE_AFTER_THE_MERGE
        return GONE_DELETED_BY_THIS_BRANCH if path in changed else GONE_FROM_THE_TRUNK

    kept: list[str] = []
    dropped: list[dict] = []
    for line in drift["touches_file_lines"]:
        path = _line_path(line)
        if (
            is_directory_touch(path)
            or is_new_file_touch(path)
            or (ctx.code_root / path).exists()
        ):
            kept.append(line)
            continue
        if path in undeclared:
            why = deleted_here(path)
            if why is None:
                kept.append(line)
                continue
        else:
            why = GONE_DELETED_BY_THIS_BRANCH if path in changed else GONE_FROM_THE_TRUNK
        dropped.append({"line": line, "why": why})
    if dropped:
        drift["touches_file_lines"] = kept
        drift["apply_command"] = _apply_command(drift["record_id"], kept)
    drift["gone_paths_dropped"] = dropped
    return drift


def _gone_path_lines(dropped: list[dict]) -> list[str]:
    """KYO-436, rendered by both consumers from one list — `kyo touch-drift`'s finding
    and `kyo edit --touches-from-drift`'s echo — so the correction says what it left
    out, rather than a controller discovering a claim vanished by diffing the record.
    """
    if not dropped:
        return []
    by_branch = [e["line"] for e in dropped if e["why"] == GONE_DELETED_BY_THIS_BRANCH]
    after_merge = [e["line"] for e in dropped if e["why"] == GONE_AFTER_THE_MERGE]
    elsewhere = [
        e["line"]
        for e in dropped
        if e["why"] not in (GONE_DELETED_BY_THIS_BRANCH, GONE_AFTER_THE_MERGE)
    ]
    reasons = []
    if by_branch:
        reasons.append("deleted by this branch: " + ", ".join(by_branch))
    if after_merge:
        reasons.append(
            "this branch's own file, deleted on the trunk after the branch merged: "
            + ", ".join(after_merge)
        )
    if elsewhere:
        reasons.append(
            "removed from this checkout by some other change: " + ", ".join(elsewhere)
        )
    return [
        f"  ({len(dropped)} line(s) left out of the correction because the file no "
        "longer exists — " + "; ".join(reasons) + " — a plain path must exist to be "
        "claimed, and work that removed a file has reached that claim, not left it "
        "unfinished)"
    ]


def cmd_touch_drift(ctx: Context, args: Namespace) -> Output:
    """``kyo touch-drift <dispatch-id> [--against main]`` — KYO-71.

    Reports and exits 0 whatever it finds. There is deliberately no ``--strict``: a
    flag that made this refuse would be a flag somebody eventually turns on, and the
    record's whole finding is that the branch which edits outside its claim is
    usually the branch that was right.

    KYO-147.c4 considered putting the "apply it" flag here and did not: this is a
    pure read, and three separate write-capability declarations in this project agree
    that it is (``cli.MUTATING_COMMANDS``, records mode's ``RECORDS_READ_COMMANDS``
    allowlist, and the desktop app's ``KYO_WRITING_COMMANDS``, which
    ``tests/test_kyo140_desktop_command_drift.py`` keeps in step). The correction is
    applied by ``kyo edit --touches-from-drift <dispatch-id>`` instead, which is the
    command KYO-71's own output has always named as the remedy and which every one of
    those declarations already calls a write.
    """
    store = ctx.store()
    dispatch = store.dispatch(args.dispatch_id)
    against = args.against or "main"
    drift = _drift_for_dispatch(ctx, store, dispatch, against)
    return Output(
        drift_lines(drift),
        {"ok": True, "command": "touch-drift", "against": against, "touch_drift": drift},
    )


#: KYO-147.c4: ``kyo edit --touches-from-drift`` on a dispatch whose drift was NOT
#: CHECKED. There is no correction to store, and "stored 0 path(s)" would be the shape
#: of a success — the same reason KYO-198 refuses ``kyo verify --from-report`` on a
#: dispatch with no report rather than answering "applied 0 of 0".
CONTRACT_TOUCH_DRIFT_APPLY_NEEDS_A_FINDING = "touch-drift-apply-needs-a-finding"


@dataclass(frozen=True)
class DriftCorrection:
    """What :func:`touch_drift_correction` hands ``kyo edit --touches-from-drift``.

    KYO-307: the lines alone were enough while they were a verbatim copy of what the
    record already said. Now that one form of line is corrected on the way out
    (``+path`` -> ``path``, once the merge has made the file exist), the caller has to
    be able to *say so* — c1 asks the command to name what it converted, and a caller
    handed only the final lines could not tell a converted line from a declared one.
    """

    lines: list[str]
    #: ``[{"was": "+src/x.py", "now": "src/x.py"}, ...]`` — see
    #: :func:`_land_new_file_touches`. Empty for every correction that changed nothing.
    landed_new_files: list[dict]
    #: KYO-436: ``[{"line": "src/x.py", "why": GONE_...}, ...]`` — see
    #: :func:`_drop_gone_paths`. Empty whenever every claimed file still exists.
    gone_paths_dropped: list[dict]
    #: KYO-543.c2: the sentence naming the lines marked ``+path`` because the branch
    #: created them and is not merged — see :func:`_mark_unlanded_new_files`.
    unlanded_lines: tuple[str, ...] = ()

    def landed_lines(self) -> list[str]:
        """The conversion, in the words `kyo touch-drift` uses for it — one sentence
        from one source, so the command that applies the correction cannot come to
        describe it differently from the command that offered it (law 3). KYO-436's
        dropped lines are said in the same place and the same way.
        """
        return (
            _landed_new_file_lines(self.landed_new_files)
            + _gone_path_lines(self.gone_paths_dropped)
            + list(self.unlanded_lines)
        )


def touch_drift_correction(ctx: Context, store, dispatch_id: str, against: str) -> DriftCorrection:
    """The corrected touches list for ``dispatch_id``, as ``--touches-file`` lines.

    KYO-147.c4: applied without leaving the tool (``kyo edit --touches-from-drift``),
    because a line to paste into another shell is a step controllers skip, and every
    skipped one leaves the store wrong about the past (docs/design/dispatch.md#kyo-147).

    It is deliberately the SAME list :func:`touch_drift` prints, read from the same
    computation rather than recomputed, so what a controller reviews and what gets
    stored cannot differ (law 3). Nothing here writes — the caller (``cmd_edit``) runs
    the identical validate-and-replace it runs for ``--touches-file``, so a path this
    would refuse is one that command refuses. KYO-71's caveat — a file the branch
    CREATED does not exist in this checkout until the merge — is met before it reaches
    validation when the branch can prove it (KYO-543.c2: the line is corrected to
    ``+path``, see :func:`_mark_absent_paths`), and stated as before when it cannot.

    KYO-307 is the far side of that same caveat, and is fixed rather than stated: once
    the merge HAS landed, a ``+path`` line names a file that is now there, which
    ``_validate_touches`` refuses just as flatly. :func:`_land_new_file_touches` has
    already corrected those lines inside the drift finding, so both renderings carry
    the corrected list and this function still returns exactly what `kyo touch-drift`
    printed.
    """
    dispatch = store.dispatch(dispatch_id)
    drift = _drift_for_dispatch(ctx, store, dispatch, against)
    if drift["outcome"] == DRIFT_NOT_CHECKED:
        raise ContractViolation(
            CONTRACT_TOUCH_DRIFT_APPLY_NEEDS_A_FINDING,
            [
                f"{dispatch_id}'s touch drift was not checked — "
                + (drift.get("detail") or "nothing was examined"),
                "so there is no correction to store, and storing the record's existing "
                "touches back over themselves would report success about a comparison "
                "that never happened",
                "make the comparison possible first — declare the dispatch's branch, or "
                f"ingest its report with `kyo report {dispatch_id} --file <report.json>` "
                f"— then run `kyo touch-drift {dispatch_id}` to read the correction and "
                "this flag to store it",
            ],
        )
    return DriftCorrection(
        list(drift["touches_file_lines"]),
        list(drift.get("landed_new_files") or []),
        list(drift.get("gone_paths_dropped") or []),
        tuple(_unlanded_new_file_lines(drift)),
    )
