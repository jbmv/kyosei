"""Docs obligations a dispatch's report raises, and the numbered documents its brief names."""

from __future__ import annotations

from dataclasses import dataclass

from . import commands, docs_numbered, docs_obligations, docs_parse, explanations
from . import config as config_module
from . import registry as registry_module
from .commands import Context
from .errors import KyoseiError
from .git_queries import _git_diff_paths, branch_changed_files, branch_is_contained_in

# ------------------------------------ KYO-137: docs obligations, raised at report time
#
# Raised at report time, not by `kyo sweep`: report time is while
# the controller can still act on what it just read (docs/design/dispatch.md#kyo-137).
#
# The mechanism is a JOIN and not a new declaration. `kyo report` already computes what
# the branch actually changed (KYO-71, `_drift_for_dispatch` below) and a `governs:`
# section already names the paths it speaks for, so intersecting the two answers most of
# this with no syntax anyone has to learn. `doc-refs` covers only the residual: a document
# about the record's subject that shares no file with it.

#: At least one document section has a question against it.
DOCS_OBLIGATIONS_RAISED = "raised"
#: Documents were examined against this branch's diff and none was implicated.
DOCS_OBLIGATIONS_NONE = "none"
#: The diff could not be read, so nothing was examined. Distinct from ``none`` for the
#: reason every other "could not tell" in this module is distinct from its clean twin:
#: reported clean on no evidence is the failure, not the fix (law 6, KYO-85).
DOCS_OBLIGATIONS_NOT_CHECKED = "not-checked"


#: KYO-211: which body of evidence the docs-obligation join read to learn what this
#: dispatch changed. Named constants for the same reason :data:`DRIFT_SOURCE_REPORT`
#: and :data:`DRIFT_SOURCE_GIT` are: a consumer that cannot tell which source answered
#: cannot tell how much the answer is worth, and the three do not answer equally well.
DOCS_CHANGED_SOURCE_BRANCH_DIFF = "branch-three-dot-diff"
DOCS_CHANGED_SOURCE_REPORT = "report-touched-files"
DOCS_CHANGED_SOURCE_BASE_SHA = "two-dot-diff-from-the-recorded-base-sha"
#: KYO-411: a merged branch whose two-dot diff AND the report's
#: ``touched_files[]`` both named paths — the union was read, and ``says`` states which
#: paths came from which.
DOCS_CHANGED_SOURCE_BASE_SHA_AND_REPORT = (
    "two-dot-diff-from-the-recorded-base-sha+report-touched-files"
)


def _normalise_reported_path(path: object) -> str:
    """KYO-411: one spelling for a path a worker typed into ``touched_files[]``.

    ``./src/b.py``, ``src//b.py`` and ``src/./b.py`` are the file git names
    ``src/b.py``, and a ``governs:`` target written the git way never matched the
    other spellings. Leading ``./`` is stripped and the rest normalised as a POSIX path;
    anything that normalises to nothing (``.``, ``./``) is dropped.
    """
    import posixpath

    text = str(path).strip()
    if not text:
        return ""
    normal = posixpath.normpath(text)
    while normal.startswith("./"):
        normal = normal[2:]
    return "" if normal in (".", "") else normal


@dataclass(frozen=True)
class DocsChangedPaths:
    """What the docs-obligation join read, where it read it, and what it could not.

    KYO-211: a branch's three-dot diff is empty **by construction** once it is merged,
    and merge-before-ingest is the ordinary case, so the join must not read that diff
    alone (docs/design/dispatch.md#kyo-211).

    ``detail`` is non-``None`` exactly when nothing could be read, and that is the
    NOT CHECKED outcome (c2). ``says`` is set on every outcome, including that one:
    which source answered is a fact the ingest output states rather than a thing the
    reader infers from a count (law 6).
    """

    paths: tuple[str, ...]
    source: str | None
    #: The rev symbol-precision diffs are read against. ``base_sha...branch`` degenerates
    #: to the two-dot ``base_sha..branch`` whenever the branch descends from that sha,
    #: which is exactly the merged case — so naming the recorded base here is what keeps
    #: a ``governs: path#symbol`` target answered at symbol precision after the merge
    #: rather than falling back to the more-raising file-level answer.
    diff_base: str
    says: str
    detail: str | None = None


def _docs_changed_paths(
    ctx: Context,
    store,
    dispatch,
    *,
    against: str = "main",
    touched_files: list[str] | None = None,
) -> DocsChangedPaths:
    """Which paths this dispatch changed, in KYO-85's order of evidence (KYO-211.c1).

    The order is deliberately the one ``_drift_for_dispatch`` already uses, and asking
    "is this branch already contained in the base?" **before** trusting the diff is the
    whole of the fix: an empty three-dot diff is what a merged, rebased or empty branch
    produces, and nothing in the diff can tell which.

    1. A live branch answers from its own three-dot diff, exactly as before.
    2. A branch already contained in ``against`` answers from the UNION of the two-dot
       diff between the dispatch's recorded base sha and the branch (which survives the
       merge because it names both ends itself) and the report's ``touched_files[]``,
       normalised (KYO-411). Either alone answers when the other names nothing, and
       ``says``/``source`` state which did.
    3. Failing both, NOT CHECKED, saying which of them could not be had.
    """
    branch = dispatch["branch"]
    base_sha = dispatch["worked_from_sha"]
    if not branch:
        return DocsChangedPaths(
            (),
            None,
            against,
            says="no branch was declared, so there was no diff to read",
            detail=(
                f"{dispatch['id']} declares no branch, so there is no diff to intersect "
                f"with any document's governs: targets"
            ),
        )

    merged = branch_is_contained_in(ctx.code_root, branch, against)
    if not merged:
        # Includes `None` (git could not tell): the three-dot diff is still the best
        # evidence available, and `branch_changed_files` raises by name when a ref is
        # unknown, which the caller turns into its own NOT CHECKED.
        try:
            changed = branch_changed_files(ctx.code_root, against, branch)
        except KyoseiError as error:
            return DocsChangedPaths(
                (),
                None,
                against,
                says=f"the diff of {branch} could not be read",
                detail=(
                    f"the branch diff could not be read ({error}), so no document was "
                    f"compared against it"
                ),
            )
        return DocsChangedPaths(
            tuple(changed),
            DOCS_CHANGED_SOURCE_BRANCH_DIFF,
            against,
            says=f"read from the diff of {branch} (`git diff {against}...{branch}`)",
        )

    # Merged. Everything below exists because `git diff against...branch` now compares
    # the branch against itself.
    why_the_diff_is_empty = (
        f"{branch} is already merged into {against}, so the three-dot diff "
        f"`git diff {against}...{branch}` compares {branch} against itself and examines "
        "nothing — an empty answer there is the absence of a finding, not a clean one"
    )
    # A base sha that still resolves makes `base...branch` the two-dot `base..branch`,
    # which is what keeps symbol precision working after the merge.
    diff_base = base_sha if base_sha else against
    # KYO-411: the report's list used to win outright here, so a
    # path the worker left out of touched_files[] raised no obligation, and a path it
    # spelled `./src/b.py` matched no `governs:` target. Git's two-dot diff from the
    # recorded base sha is now read whenever it resolves and UNIONED with the report's
    # (normalised) list, and `says` names which paths came from which source — the
    # report still answers alone when git cannot, which is what it was added for.
    named: list[str] = []
    for path in touched_files or []:
        normal = _normalise_reported_path(path)
        if normal and normal not in named:
            named.append(normal)
    two_dot = _git_diff_paths(ctx.code_root, base_sha, branch) if base_sha else None
    from_git = set(two_dot or ())
    if from_git and named:
        only_git = sorted(from_git - set(named))
        only_report = sorted(set(named) - from_git)
        both = len(from_git & set(named))

        def sample(paths: list[str]) -> str:
            shown = ", ".join(paths[:5])
            return shown + (f" and {len(paths) - 5} more" if len(paths) > 5 else "")

        parts = [f"{both} named by both"]
        if only_git:
            parts.append(
                f"{len(only_git)} only by the git diff (missing from touched_files[]: "
                f"{sample(only_git)})"
            )
        if only_report:
            parts.append(
                f"{len(only_report)} only by the report's touched_files[] "
                f"({sample(only_report)})"
            )
        return DocsChangedPaths(
            tuple(sorted(from_git | set(named))),
            DOCS_CHANGED_SOURCE_BASE_SHA_AND_REPORT,
            diff_base,
            says=(
                f"read from the union of `git diff {base_sha}..{branch}` (the two-dot diff "
                f"from {dispatch['id']}'s recorded base sha) and the report's "
                f"touched_files[] — {'; '.join(parts)} — because {why_the_diff_is_empty}"
            ),
        )
    if named:
        return DocsChangedPaths(
            tuple(named),
            DOCS_CHANGED_SOURCE_REPORT,
            diff_base,
            says=(
                f"read from the report's touched_files[] ({len(named)} path(s)) alone — "
                + (
                    f"`git diff {base_sha}..{branch}` named no path"
                    if two_dot is not None
                    else "the two-dot diff from the recorded base sha "
                    f"({base_sha or 'none recorded'}) could not be read"
                )
                + f" — because {why_the_diff_is_empty}"
            ),
        )
    if from_git:
        return DocsChangedPaths(
            tuple(sorted(from_git)),
            DOCS_CHANGED_SOURCE_BASE_SHA,
            diff_base,
            says=(
                f"read from `git diff {base_sha}..{branch}`, the two-dot diff from "
                f"{dispatch['id']}'s recorded base sha, because {why_the_diff_is_empty}"
            ),
        )
    return DocsChangedPaths(
        (),
        None,
        diff_base,
        says="neither the report nor the recorded base sha could name a changed path",
        detail=(
            f"{why_the_diff_is_empty}. The report named no touched_files[], and the "
            f"two-dot diff from {dispatch['id']}'s recorded base sha "
            f"({base_sha or 'none recorded'}) could not be read either, so NOTHING was "
            f"compared against any document. Run `kyo docs preview --against "
            f"{base_sha or against} --branch {branch}` to see what this branch raises"
        ),
    )


def _docs_obligations_for_dispatch(
    ctx: Context,
    store,
    dispatch,
    *,
    against: str = "main",
    touched_files: list[str] | None = None,
) -> tuple[list[docs_obligations.DocsObligation], DocsChangedPaths]:
    """The obligations this dispatch's work raises, computed and not yet stored.

    The record's own ``doc-refs`` and every covered record's are read as one declaration,
    the same way ``_claim_of`` reads a dispatch's touches: ``--also-covers`` means the
    dispatch is doing that record's work too, and a document its work affects is affected.

    Two roots, two repositories under the split layout: the document globs are declared in
    the STORE (``ctx.root``) and resolved against the CODE (``ctx.code_root``), which is
    where the documents and the changed files both are.

    **KYO-153**: the branch's diff is read as well as its file list, so a
    ``governs: path#symbol`` target is answered at the precision it was written with
    rather than at file level. Only paths some target actually narrows are read
    (:func:`docs_obligations.governed_symbol_paths`), which is what keeps this to a couple of
    ``git show`` calls; a branch git cannot diff yields an empty map, and an empty map
    raises everything the old file-level join would have raised rather than quietly less.

    **KYO-211**: the changed paths are computed HERE, by :func:`_docs_changed_paths`,
    rather than handed in from a single three-dot diff, which is empty for a merged
    branch. The evidence is returned beside the obligations so the ingest output can say
    which source answered (c1) and an unanswerable one is NOT CHECKED, never "none" (c2).
    """
    record_ids = [dispatch["record_id"], *commands.dispatch_covers(dispatch)]
    doc_refs: list[tuple[str, str | None]] = []
    for record_id in record_ids:
        for row in store.doc_refs(record_id):
            doc_refs.append((row["path"], row["symbol"]))
    documents_in = docs_parse.document_set(store.config.kyosei_dir)
    evidence = _docs_changed_paths(
        ctx, store, dispatch, against=against, touched_files=touched_files
    )
    if evidence.detail is not None:
        return [], evidence
    changed = list(evidence.paths)
    branch = dispatch["branch"]
    if not branch:  # pragma: no cover - `_docs_changed_paths` already returned a detail
        # No branch, no diff to read at symbol precision. The file-level join still runs,
        # which is the conservative answer `_target_raises` documents: an absent `changes`
        # map raises everything rather than quietly less.
        return (
            docs_obligations.obligations_for_changed_files(
                ctx.code_root, changed, documents_in=documents_in, doc_refs=doc_refs
            ),
            evidence,
        )
    # KYO-199.c6: assembling the two halves of this join lives in `docs_obligations` now, so
    # `kyo docs preview` asks the same function the same question about a worktree that
    # has no dispatch row to name. Two assemblies could disagree, and the preview exists
    # to show a worker what its controller will see.
    #
    # KYO-211: `evidence.diff_base` rather than `against`. For a live branch the two are
    # the same string; for a merged one it is the recorded base sha, and
    # `base_sha...branch` is the two-dot `base_sha..branch` whenever the branch descends
    # from that sha — so a `governs: path#symbol` target keeps being answered at symbol
    # precision after the merge instead of degrading to the file-level answer.
    return (
        docs_obligations.obligations_for_branch(
            ctx.code_root,
            evidence.diff_base,
            branch,
            changed,
            documents_in=documents_in,
            doc_refs=doc_refs,
        ),
        evidence,
    )


def _raise_docs_obligations(
    ctx: Context,
    store,
    dispatch,
    stamp: str,
    docs_verdicts: list[dict] | None = None,
    *,
    touched_files: list[str] | None = None,
    against: str = "main",
) -> dict:
    """Store this dispatch's docs obligations, and say what was examined (KYO-137.c2).

    Called inside `kyo report`'s transaction. What it returns is shaped like a drift
    finding — an ``outcome`` that is one of three named states, with ``detail`` filled in
    whenever nothing was examined — because the two answers are read side by side in the
    same output and the reader must be able to tell "no document is implicated" from "no
    document was looked at".

    **Already-raised obligations are not raised twice.** A worker resumed on a dispatch
    files an amending report (KYO-90) over the same branch, and the join would produce the
    same rows a second time; an obligation the controller had already settled would
    reappear open, which would make the amendment path punish itself. The key is
    (document, section), which is c3's unit and therefore the right identity for this.

    **KYO-199.c4**: ``docs_verdicts`` is the report's own proposed verdicts, matched here
    to the obligations by (document, section) — see
    :func:`docs_numbered.match_docs_verdicts` for why that pair and not an id. Matching runs
    against every obligation this DISPATCH carries, not only the ones this call just
    raised, so an amending report can answer obligations its first report raised. Nothing
    is applied: the match is a rendering and a finding, and settling it is a separate
    controller act (``kyo docs settle --from-report``).

    **KYO-211**: ``touched_files`` is the report's own declaration, passed in because at
    this point in `kyo report`'s transaction the *row* in hand still carries the previous
    report's id — the parsed document is the only place this dispatch's list exists. It
    is what answers the join when the branch has already been merged, and which source
    answered is carried out on the finding (``changed_source``/``changed_says``) so the
    ingest output states it rather than leaving a reader to infer it from a count.
    """
    record_id = dispatch["record_id"]
    proposals = list(docs_verdicts or [])

    def unexamined(evidence: DocsChangedPaths) -> dict:
        # An entry proposing a verdict is still reported when nothing was examined —
        # otherwise a worker's whole reading would vanish on the one outcome where the
        # controller most needs to know something was claimed about it (law 6).
        _, unmatched = docs_numbered.match_docs_verdicts([], proposals)
        return {
            "outcome": DOCS_OBLIGATIONS_NOT_CHECKED,
            "detail": evidence.detail,
            "raised": [],
            "verdicts_matched": 0,
            "verdicts_unmatched": unmatched,
            "verdicts_answered": [],
            "dispatch_id": dispatch["id"],
            "changed_source": evidence.source,
            "changed_says": evidence.says,
            "changed_files": list(evidence.paths),
        }

    found, evidence = _docs_obligations_for_dispatch(
        ctx, store, dispatch, against=against, touched_files=touched_files
    )
    if evidence.detail is not None:
        return unexamined(evidence)
    changed = list(evidence.paths)
    existing = {
        (row["doc_path"], row["section"])
        for row in store.docs_obligations(record_id)
        if row["dispatch_id"] == dispatch["id"]
    }
    stored: list[dict] = []
    for obligation in found:
        if (obligation.doc_path, obligation.section) in existing:
            continue
        obligation_id, seq = store.new_child_id(record_id, "docs-obligation")
        store.conn.execute(
            "INSERT INTO docs_obligations (id, record_id, seq, dispatch_id, doc_path,"
            " section, cause, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                obligation_id,
                record_id,
                seq,
                dispatch["id"],
                obligation.doc_path,
                obligation.section,
                obligation.cause,
                obligation.says,
                stamp,
            ),
        )
        stored.append(
            {
                "id": obligation_id,
                "doc_path": obligation.doc_path,
                "section": obligation.section,
                "cause": obligation.cause,
                "detail": obligation.says,
                # KYO-199.c2: the matched `governs:` targets, as the document wrote them.
                # Not a stored column — the schema's unit is unchanged (c1) and this is
                # derived from the same join that produced the row, carried on the finding
                # so the rendering can offer the narrower `path#symbol` form once per
                # document without re-reading the document to find out whether it could.
                "targets": list(obligation.targets),
            }
        )
    # Every obligation this dispatch carries, whichever report raised it, so an amending
    # report's entries reach the rows its first report created (KYO-90 + c4).
    carried = [
        {"id": row["id"], "doc_path": row["doc_path"], "section": row["section"],
         "verdict": row["verdict"]}
        for row in store.docs_obligations(record_id)
        if row["dispatch_id"] == dispatch["id"]
    ]
    # KYO-393: the same numbered-document reading `kyo docs settle --from-report` passes,
    # so ingest and settle agree about which obligation each verdict answers.
    matched, unmatched = docs_numbered.match_docs_verdicts(
        carried,
        proposals,
        docs_numbered.numbered_templates(ctx.root / config_module.KYOSEI_DIR, ctx.code_root),
    )
    proposed_by_id = {pair["obligation"]["id"]: pair["entry"] for pair in matched}
    for row in stored:
        if row["id"] in proposed_by_id:
            row["proposed"] = proposed_by_id[row["id"]]
    return {
        "outcome": DOCS_OBLIGATIONS_RAISED if stored else DOCS_OBLIGATIONS_NONE,
        "detail": None
        if stored
        else (
            f"{len(changed)} changed path(s) intersected no governs: section and no "
            f"doc-ref on {record_id}"
        ),
        "raised": stored,
        # The count and the named list, never one without the other: a controller reading
        # "3 of 5 applied" cannot tell a typo from a verdict on an untouched section.
        "verdicts_matched": len(matched),
        "verdicts_unmatched": unmatched,
        "verdicts_answered": [pair["obligation"]["id"] for pair in matched],
        # KYO-393.c1: matched by section number with a heading that differs — applied
        # when settled, and named here so the difference is read before it is.
        "verdicts_matched_by_number": [
            pair for pair in matched if pair.get("by_number") is not None
        ],
        "dispatch_id": dispatch["id"],
        # KYO-211.c1: which body of evidence named the changed paths, and the sentence
        # saying so. Carried on every outcome — including the two that raised nothing —
        # because "none" computed from a merged branch's empty diff and "none" computed
        # from a real diff are the two facts this record exists to keep apart.
        "changed_source": evidence.source,
        "changed_says": evidence.says,
        "changed_files": changed,
    }


def _docs_obligation_lines(obligations: dict) -> list[str]:
    """How `kyo report` says what it raised. Never silent in any of the three states.

    **KYO-199.c1: grouped per document, with the settle line for each.** The flat list
    this printed before was read document by document anyway — that is how the reading is
    actually done, because a document is opened once and answers every section of it a
    dispatch disturbed — so the rendering says so, and the ready-to-run `kyo docs settle`
    line naming every id in the block sits at the foot of the block it settles.

    **KYO-199.c4: the worker's proposed verdict prints beside the obligation it answers**,
    and any entry that answered none is named at the end. Both halves print on every
    outcome, the two that examined nothing included — a worker's whole reading must not
    vanish on the run where the controller most needs to know something was claimed.
    """
    outcome = obligations["outcome"]
    if outcome == DOCS_OBLIGATIONS_NOT_CHECKED:
        lines = [f"docs obligations: NOT CHECKED — {obligations['detail']}"]
    elif outcome == DOCS_OBLIGATIONS_NONE:
        lines = [f"docs obligations: none — {obligations['detail']}"]
    else:
        groups = docs_obligations.group_obligations_per_document(obligations["raised"])
        lines = [
            f"docs obligations: {len(obligations['raised'])} raised across {len(groups)} "
            f"document(s) — {explanations.say('obligations-need-verdicts')}"
        ]
        lines += docs_obligations.grouped_obligation_lines(groups)
    # KYO-211.c1: which evidence answered, on every outcome. `none` used to be printed
    # for a merged branch whose three-dot diff was empty by construction, and the line
    # that would have told a controller so did not exist.
    if obligations.get("changed_says"):
        lines.append(f"  changed paths: {_changed_paths_says(obligations)}")
    return lines + _docs_verdict_lines(obligations)


def _changed_paths_says(obligations: dict) -> str:
    """The ``changed paths:`` sentence as printed (KYO-489).

    The live-branch case, the one every ordinary report takes, reads the same on every
    run, so it prints its short form and its full text is in `kyo reference report`.
    The stored ``changed_says`` (and so ``--json``) keeps the full sentence: only the
    print shortens. Every other source (a merged branch, an unreadable diff, no branch)
    is the finding itself and prints whole.
    """
    says = obligations["changed_says"]
    if obligations.get("changed_source") != DOCS_CHANGED_SOURCE_BRANCH_DIFF:
        return says
    # Read back out of the stored sentence rather than carried as a new key: the
    # finding's keys are `--json`'s, and KYO-489 changes no JSON key.
    values = explanations.match_full("changed-paths-branch-diff", says)
    if values is None:
        return says
    return explanations.say("changed-paths-branch-diff", branch=values["branch"])


def _docs_verdict_lines(obligations: dict) -> list[str]:
    """What the report's own ``docs_verdicts[]`` answered, and what it did not (c4).

    Applies nothing. The verdicts sit beside their obligations as *proposals* with the
    worker's method and reason attached, and the line that would apply them is printed
    the way `kyo report` already prints `kyo verify --from-report` — the controller
    reads first and acts second, which is the whole of the standing stance.
    """
    matched = obligations.get("verdicts_matched", 0)
    unmatched = list(obligations.get("verdicts_unmatched") or ())
    if not matched and not unmatched:
        return []
    lines = [
        f"  docs_verdicts: {matched} matched an obligation this dispatch raised, "
        f"{len(unmatched)} matched none. NOT applied — `kyo docs settle --from-report "
        f"{obligations.get('dispatch_id', '<dispatch-id>')}` applies them as one "
        "orchestrator act (`--only <obligation-id> ...` for the ones you have checked)"
    ]
    for pair in obligations.get("verdicts_matched_by_number") or ():
        lines.append(f"    matched by number: {docs_numbered.number_matched_line(pair)}")
    for row in unmatched:
        lines.append(f"    unmatched: {docs_numbered.unmatched_verdict_line(row)}")
    return lines


def numbered_document_rows(ctx: Context, dispatch_id: str) -> list[dict]:
    """This project's numbered, append-only documents, ready for the brief (KYO-299.c2).

    Every string the brief renders is computed HERE, from the declaration and from
    :mod:`kyosei.docs_numbered`'s own constants and methods, rather than assembled in
    ``brief.py`` from parts: ``fragment_path`` is
    :meth:`kyosei.docs_numbered.NumberedDocument.fragment_path` — the same method
    `kyo check-branch`'s refusal names and `kyo docs assemble` parses back — and the
    placeholder is the module constant. What a worker is told and what the two commands
    expect are therefore one value with three readers (law 3), which is the only way an
    instruction this specific stays true.

    Empty, never an exception, for a project that declares none — which is every project
    until one declares one, and is the state `kyo dispatch` must not become slower or
    more fragile for.
    """
    try:
        documents = docs_numbered.numbered_documents(
            ctx.root / config_module.KYOSEI_DIR, ctx.code_root
        )
    except KyoseiError:
        # A `documents.yml` this build refuses is a real problem and `kyo docs check`
        # is where it is reported. Failing `kyo dispatch` over it would block the work
        # on a file the work has nothing to do with.
        return []
    placeholder = docs_numbered.FRAGMENT_PLACEHOLDER
    return [
        {
            "path": document.path,
            "set": document.set_name,
            "fragments": document.fragments_dir,
            "heading": document.heading,
            "after": list(document.after),
            "placeholder": placeholder,
            "placeholder_heading": document.heading.replace(
                registry_module.HEADING_NUMBER, placeholder
            ),
            "fragment_path": document.fragment_path(dispatch_id, "<short-slug>"),
            # KYO-493: the set's `numbered.amends:` declaration, or None. The brief
            # states the header syntax only for a document that declares it.
            "amends": (
                {"field": document.amends_field, "status": document.amends_status}
                if document.amends_field
                else None
            ),
        }
        for document in documents
    ]
