"""Human-readable output: the write-echo (law 5) and ``kyo show``."""

from __future__ import annotations

from . import docs_obligations
from . import sweep as sweep_module
from .brief import WORKER_RULE_FULL_TEXT_COMMAND
from .buildinfo import SOURCE_GIT
from .contracts import (
    BLOCKED_REMEDY,
    CRITERION_STANDING_SUPERSEDED,
    FLAG_TYPE_TOUCH_CHANGED,
    REPORT_STANDING_SUPERSEDED,
    SCOPE_UNKNOWN_REMEDY,
    TOUCH_ADDITIVE_SYMBOL,
    TOUCH_NEW_FILE_SYMBOL,
    amended_by,
    dispatch_close_reason_label,
    replaced_by,
    report_amends,
    report_standing,
    touch_target,
)
from .prose import fingerprint


def echo_block(label: str, text: str) -> list[str]:
    """Render stored prose back to the caller, verbatim and measured.

    Length and digest are printed because "looks right" is not a check: a dropped
    word changes the digest even when the sentence still reads fine.
    """
    lines = [f"stored {label} ({len(text)} chars, sha256:{fingerprint(text)}):"]
    lines.extend(f"  | {line}" for line in text.split("\n"))
    return lines


def echo_value(label: str, value: str) -> str:
    return f"stored {label}: {value}"


def result_value(label: str, value: str) -> str:
    """One ``label: value`` line for a count or a result of work done — the sibling
    of :func:`echo_value` for what was NOT stored.

    KYO-423.c6: ``echo_value`` says "stored" because it echoes a value just written
    back to its writer (a base sha, a worktree, a title). ``kyo redact`` printed its
    scrub counts through it too, so "stored scrubbed from prior ledger lines: 2"
    described a count of work as a stored value. Counts and results use this instead.
    """
    return f"{label}: {value}"


def elided_first_line(text: str, *, more_at: str) -> tuple[str, str]:
    """``(text's first line, a note saying how much was left off and where to read it)``.

    The second element is ``""`` when there was nothing to leave off, so a caller can
    interpolate it unconditionally and a single-line value renders exactly as it did
    before this function existed.

    KYO-148. Three renderings put one stored value on one line: the claims indexed
    under an open gate (KYO-88), the gate list in `kyo show`, and the board-wide gated
    list in `kyo brief`. A raw newline in any of them does not merely look untidy — the
    continuation starts at column 0, so it escapes its own indented block and whatever
    is printed next appears to belong to nothing. Under ``kyo brief --compact``, which
    is what the SessionStart hook runs into every session, it would run every gate on
    the board together.

    That became reachable the moment a gate's payload stopped being single-line. The
    payload was single-line only because ``--do`` was the sole way to write one, which
    is the defect KYO-148 was filed about rather than a property anything relied on
    deliberately — but three renderings did rely on it, silently, which is why the
    eliding ships in the same commit as ``--do-file`` (law 2).

    One function rather than the shape written out at each surface, on
    ``cmd_links._validated_tags``'s reasoning (KYO-130): three copies of an eliding
    rule agree only until someone edits one of them, and the surface that forgot would
    be discovered as a corrupted brief. ``more_at`` is the one thing that legitimately
    differs — each caller names where the full text can actually be read, because a
    note that says text was omitted without saying where it went is the same failure
    one step smaller.
    """
    first, *rest = text.split("\n")
    if not rest:
        return first, ""
    return first, f"  (+{len(rest)} more line(s) {more_at})"


#: What a title reads as when the renderer has no title for that id (KYO-47).
#: A renderer reading a document assembled from ``kyosei/export/`` can be missing
#: the far endpoint of an edge, so this case is real rather than defensive. Never
#: rendered as an empty string: a blank where a title belongs reads as "this record
#: has no title", not "this renderer could not find one".
MISSING_TITLE = "(title unavailable)"


def commit_label(commit: dict) -> str:
    """The sha, plus `subject` if the entry has one.

    KYO-241.c3: `subject` is the field this project asks a worker to write; an
    entry that omits it renders exactly as every commit entry rendered before
    this field existed (the byte-for-byte "unchanged" the criterion asks for).

    KYO-270: `summary` (KYO-224) meant the same thing under an older name and is
    retired -- a *new* report can no longer write it (refused at ingest, see
    `report_schema.COMMIT_ALLOWED`), but an already-ingested report that carries
    only `summary` is history and is not rewritten, so this function still has to
    read it *tolerantly*: `.get("subject")` never raises on such an entry, it
    just renders as if the entry carried no first-line field at all, the same as
    any other commit that omits `subject`. This function reads only `subject` --
    `test_commit_label_does_not_fall_back_to_the_retired_summary_key` in
    ``tests/test_kyo241_report_key_sets.py`` proves it does not fall back to
    `summary`. One function so `kyo report`'s own output and `kyo show
    <dispatch-id>` cannot pick different commits' worth of prose to trust (law 3).
    """
    label = commit.get("subject")
    return f"{commit['sha']}  {label}" if label else str(commit["sha"])


def report_standings(doc: dict) -> dict[str, dict]:
    """KYO-90.c3: per report id, ``{standing, amends, amended_by}``.

    One derivation, so every surface that renders a report's standing agrees about
    which report is the account of what happened (law 3).
    Everything here comes from facts the export already carries: a report is
    ``current`` exactly when its dispatch's ``report_id`` names it, and the
    forward link is the ``amends`` the worker wrote into the document — the
    reverse link is a lookup over that same fact rather than a second copy of it,
    which is the shape :func:`contracts.replaced_by` already uses for restated
    criteria.

    A document whose ``dispatches`` list does not contain a report's dispatch (an
    export read in isolation, or a partial one) leaves that report ``superseded``
    rather than guessing it is current: claiming an unread dispatch's report is
    the live one is exactly the mistake worth refusing to make silently.
    """
    current_by_dispatch = {d["id"]: d.get("report_id") for d in doc.get("dispatches", [])}
    reports = doc.get("reports", [])
    pairs_by_dispatch: dict[str, list[tuple[str, str | None]]] = {}
    for report in reports:
        pairs_by_dispatch.setdefault(report["dispatch_id"], []).append(
            (report["id"], report_amends(report.get("body")))
        )
    standings: dict[str, dict] = {}
    for report in reports:
        pairs = pairs_by_dispatch.get(report["dispatch_id"], [])
        standings[report["id"]] = {
            "standing": report_standing(
                report["id"], current_by_dispatch.get(report["dispatch_id"])
            ),
            "amends": report_amends(report.get("body")),
            "amended_by": amended_by(report["id"], pairs),
        }
    return standings


def edge_sentence(
    src_id: str,
    src_title: str | None,
    edge_type: str,
    dst_id: str,
    dst_title: str | None,
    *,
    indent: str = "  ",
) -> list[str]:
    """One edge as a directional sentence: subject, verb, object (KYO-47).

    ``KYO-43 blocks KYO-39`` and ``KYO-39 blocks KYO-43`` are both well-formed and
    read almost identically at a glance, which is how four wrong edges were written
    in this project in a single day -- three backwards or mistyped, and one whose
    misreading destroyed a *correct* edge. Ids are the thing the operator gets
    wrong; titles are the thing the operator knows. So every place a human is shown
    an edge shows it the same way, top to bottom, in the direction it means:

        KYO-43 "kyo new files any kind directly, without a note file"
          BLOCKS
        KYO-39 "Decision: the seam, authorship, and the shape of v2"

    Titles are never truncated. In this repo's own store the longest record title
    is 98 characters and the median is 59 (measured, not remembered), so a
    truncation short enough to fit two of them on one line would cut exactly the
    distinguishing tail -- "Decision: the seam..." and "Decision: the store..." are
    a real pair here. One endpoint per line means the worst case is a wrapped line,
    not a hidden one.

    The edge type is upper-cased for the same reason it is on its own line: it is
    the verb, and it must not read as part of either title.
    """
    return [
        f"{indent}{src_id} {_quoted(src_title)}",
        f"{indent}  {edge_type.upper()}",
        f"{indent}{dst_id} {_quoted(dst_title)}",
    ]


def _quoted(title: str | None) -> str:
    return MISSING_TITLE if title is None else f'"{title}"'


def _standing_of(doc: dict, standings: dict[str, str] | None) -> str:
    """The word this record's status field is rendered as (KYO-44.c2).

    ``standings`` is built by the caller from the kind registry
    (``contracts.standing``); falling back to the raw status keeps every renderer
    that has no registry to hand honest rather than guessing an archetype.
    """
    return (standings or {}).get(doc["id"], doc["status"])


def _members_block(members: list[dict], standings: dict[str, str] | None = None) -> list[str]:
    """A plan's members and their status distribution (KYO-27).

    KYO-44.c2: a plan may hold knowledge members, and this distribution counted a
    permanently-``open`` decision alongside a genuinely-open, dispatchable task.
    """
    if not members:
        return []
    counts: dict[str, int] = {}
    for member in members:
        word = _standing_of(member, standings)
        counts[word] = counts.get(word, 0) + 1
    spread = ", ".join(f"{status}:{n}" for status, n in sorted(counts.items()))
    out = ["", f"members ({len(members)}) — {spread}:"]
    out += [
        f"  {m['id']:<10} {m['kind']:<14} {_standing_of(m, standings):<9} {m['title']}"
        for m in members
    ]
    return out


def _claim_marker(claim: dict) -> str:
    """The supersession markers a claim carries, rendered the same way everywhere."""
    marker = f" [superseded by {claim['superseded_by']}]" if claim["superseded_by"] else ""
    via = f" (supersedes {claim['supersedes']})" if claim["supersedes"] else ""
    return f"{marker}{via}"


def claims_beside_gate(claims: list[dict]) -> list[str]:
    """This record's claims, rendered under one OPEN gate (KYO-88).

    A gate is an open question about a record; a claim is an answer recorded about
    the same record, with a verification level. They live on the same record and
    nothing related them, so a consumer's record sat blocked for three days on a
    question its own ``verified`` claim had already answered — found only because
    someone happened to read both sections. Adjacency at the section level was
    already there (``gates`` and ``claims`` are neighbours) and was not enough: the
    reader still had to hold one block in their head while reading the other.

    Nothing here decides that a claim answers a gate. That is a judgment about
    meaning, and getting it wrong silently unblocks work, which is worse than the
    current failure of work merely sitting stuck (KYO-88.c3). No matching, no
    scoring, no "possibly related" — the reader does the comparison, the tool only
    makes it possible to do without scrolling.

    **One line per claim, and never a truncated list.** The full claim, with its
    method and its whole statement, is a few lines below in the ``claims`` section;
    what is inlined here is an index — id, level, and the statement's first line —
    so a record with many claims costs *lines* under each open gate, not
    paragraphs. A cap ("first five claims") was rejected rather than tuned: the
    claim a cap hides is exactly the claim this rendering exists to surface, and a
    list that silently omits it would read as complete while reproducing the
    original failure. Whether the first line is enough to judge on is the reader's
    call, and the full text is one screen away either way.

    A record with no claims says so. An empty block would read as "compared and
    found nothing", which is a different fact from "there was nothing to compare".
    """
    if not claims:
        return ["      claims on this record (0) — nothing recorded to compare this gate against"]
    out = [
        f"      claims on this record ({len(claims)}) — compare before treating this gate"
        " as unanswered:"
    ]
    for claim in claims:
        # KYO-148 extracted the eliding into `elided_first_line`; this call renders
        # byte-for-byte what the inlined version did, and the gate payload below now
        # goes through the same rule rather than a second copy of it.
        first, more = elided_first_line(claim["statement"], more_at="under `claims` below")
        out.append(
            f"        {claim['id']}  {claim['level']:<9} {first}"
            f"{_claim_marker(claim)}{more}"
        )
    return out


#: What `kyo show` prints in place of a full obligation listing, and the flag that
#: turns the listing back on. Named once because the renderer prints it and
#: ``tests/test_kyo137_docs_obligations.py`` asserts it (law 3).
EXPAND_OBLIGATIONS_FLAG = "--expand-obligations"


def standing_obligation_lines(obligations: list[dict]) -> list[str]:
    """The ``standing docs obligations`` block of ``kyo show`` (KYO-554.c8).

    A standing obligation is one this record raised when it was filed or edited, so it
    has no dispatch (``dispatch_id`` null) and no report that would ever list it. Each is
    printed on one line with its state, uncollapsed, because the collapsed block above
    groups by settlement and names no section. Nothing is printed for a record that holds
    none, which is every record whose kind does not set
    ``policy.raises_docs_obligations``.
    """
    standing = [o for o in obligations if not o.get("dispatch_id")]
    if not standing:
        return []
    open_count = sum(1 for o in standing if not o["verdict"])
    out = ["", f"standing docs obligations ({open_count} open of {len(standing)}):"]
    for obligation in standing:
        state = obligation["verdict"] or docs_obligations.OBLIGATION_STATE_OPEN
        out.append(
            f"  {obligation['id']}  [{state}]  {obligation['doc_path']} "
            f"[{obligation['section']}]  ({obligation['cause']}, raised "
            f"{obligation['created_at']})"
        )
        if obligation["verdict"]:
            out.append(
                f"      {obligation['method']}  {obligation['author']}  "
                f"{obligation['settled_at']}"
            )
    if open_count:
        out.append(
            "  (settle one: `kyo docs settle <id> --verdict updated|not-affected "
            "--method <how>`)"
        )
    return out


def obligation_lines(obligations: list[dict], *, expand: bool = False) -> list[str]:
    """The ``docs obligations`` block of ``kyo show`` (KYO-219.c1/c2).

    Collapsed by default and, with ``expand``, the full per-obligation listing this
    block printed before KYO-219 — same fields, same order, one entry per obligation.
    Both readings are of the same rows, and the grouping is
    :func:`kyosei.docs_obligations.collapse_obligations` rather than a rule written out here:
    ``kyo show --json`` carries that function's output under ``docs_obligation_groups``
    (c3), so the desktop viewer renders the collapse the terminal renders instead of
    re-deriving one that could disagree with it.

    The counts-by-state line goes ahead of the list rather than after it, because at 59
    obligations the reader who needs "how many are still open" has already scrolled past
    the answer by the time a footer arrives — the same reason `kyo list`'s warning goes
    after its count and this one goes before its list: each sits where the number it
    qualifies is actually read.
    """
    summary = docs_obligations.collapse_obligations(obligations)
    out = [f"docs obligations ({summary['open']} open of {summary['total']}):"]
    if not obligations:
        out.append("  (none)")
        return out
    out.append(
        "  by state: "
        + ", ".join(f"{item['state']} {item['count']}" for item in summary["counts_by_state"])
    )
    if expand:
        for obligation in obligations:
            # The verdict IS the state: a settled obligation shows
            # what was asserted, by whom and how, and an open one shows the word "open"
            # rather than a blank a reader could take for either answer.
            state = obligation["verdict"] or docs_obligations.OBLIGATION_STATE_OPEN
            out.append(
                f"  {obligation['id']}  [{state}]  {obligation['doc_path']} "
                f"[{obligation['section']}]  ({obligation['cause']}, "
                f"{obligation['dispatch_id'] or 'standing'})"
            )
            out.append(f"      {obligation['detail']}")
            if obligation["verdict"]:
                out.append(
                    f"      {obligation['method']}  {obligation['author']}  "
                    f"{obligation['settled_at']}"
                )
            if obligation["settled_why"]:
                out.append(f"      why: {obligation['settled_why']}")
        return out

    for group in summary["groups"]:
        # KYO-554: a standing obligation has no dispatch; the group says how many it has.
        raised_by = [*group["dispatch_ids"]]
        if group.get("standing"):
            raised_by.append(f"{group['standing']} standing")
        raised = f"{', '.join(group['causes'])}, {', '.join(raised_by)}"
        out.append(f"  [{group['state']}]  {group['count']} obligation(s)  ({raised})")
        if group["settled_at"]:
            out.append(
                f"      {group['method']}  {group['author']}  {group['settled_at']}"
            )
        # The line this record was filed about: one `kyo docs settle --why` over a
        # governed-file set stamps one note onto every obligation it named, and it is
        # printed here once for the whole group instead of once per member.
        if group["settled_why"]:
            out.append(f"      why: {group['settled_why']}")
        for entry in group["details"]:
            ids = ", ".join(entry["obligation_ids"])
            times = f"(x{entry['count']}) " if entry["count"] > 1 else ""
            out.append(f"      {times}{ids}  —  {entry['detail']}")
    out.append(
        f"  (collapsed by settlement and detail; `kyo show {EXPAND_OBLIGATIONS_FLAG}` "
        "lists every obligation, `--json` always carries them all)"
    )
    return out


def show_text(
    doc: dict,
    *,
    members: list[dict] | None = None,
    standings: dict[str, str] | None = None,
    link_titles: dict[str, str] | None = None,
    expand_obligations: bool = False,
    pending_touches: frozenset[str] | set[str] | None = None,
    covered_by: list[dict] | None = None,
    carried: dict | None = None,
) -> str:
    """Full record rendering for ``kyo show`` without ``--json``.

    ``link_titles`` maps a linked record's id to its title (KYO-47.c2). Like
    ``standings`` and a plan's ``members``, it is handed in by the caller rather
    than stored on the document: a title copied into the *other* endpoint's export
    would go stale the moment either record is renamed, and this store has no
    other field that can silently disagree with itself.

    ``expand_obligations`` is KYO-219.c2's flag reaching the one block it changes; see
    :func:`obligation_lines`.

    ``pending_touches`` is KYO-225.c3's: the stored paths of the new-file touches whose
    file is not in this checkout yet, so the ``touches`` block can say which are still
    to be created without this renderer learning where the project lives.

    ``covered_by`` is KYO-291's: the dispatches on OTHER records that formally cover
    this one (``cmd_views._covered_by_dispatches``). Handed in for the same reason
    ``members`` is — it is a backwards walk over another record's ``covers_json``, not
    a field of this record's document — and it is rendered in its own block, never
    folded into ``dispatches``.

    ``carried`` is KYO-497's (``cmd_views._carried_standing``): on a record of a
    ``policy.carried`` kind, when it was filed and how many whole days it has been
    carried; ``None`` on every other record, which prints no line for it.
    """
    out: list[str] = []
    pending = set(pending_touches or ())
    out.append(f"{doc['id']}  [{doc['kind']}]  {_standing_of(doc, standings)}")
    out.append(f"  title:   {doc['title']}")
    if carried is not None:
        age = "settled" if carried["age_days"] is None else f"{carried['age_days']}d"
        basis = carried.get("since_note") or "filed"
        out.append(f"  carried: since {carried['since']} ({basis}), {age}")
    if doc["plan"]:
        out.append(f"  plan:    {doc['plan']}")
    # KYO-100. Printed unconditionally, unlike `plan` above: "no scope was declared" is
    # a fact a reader has to be able to see, and a line that appears only when a value
    # exists makes its absence indistinguishable from a build that does not have the
    # field at all (law 6 — an unknown must not read as either answer).
    out.append(f"  scope:   {doc['scope'] or '(none declared)'}")
    out.append(f"  created: {doc['created_at']}")
    out.append(f"  updated: {doc['updated_at']}")
    if doc["wont_do_reason"]:
        # The column is one column; the label is the archetype's own word for what
        # happened to the record (KYO-44).
        out.append(f"  {_standing_of(doc, standings)}: {doc['wont_do_reason']}")

    out.append("")
    out.append("description:")
    if doc["description"] == "":
        out.append("  (empty)")
    else:
        out.extend(f"  | {line}" for line in doc["description"].split("\n"))

    out.append("")
    out.append(f"criteria ({len(doc['criteria'])}):")
    if not doc["criteria"]:
        out.append("  (none)")
    for criterion in doc["criteria"]:
        # KYO-58.c5/c6: `void-withdrawn` covers two different things, and the one
        # with a replacement names it here -- beside the stored state, never instead
        # of it (the KYO-44 rule for standings, applied one level down).
        replacement = replaced_by(criterion["state"], criterion["reason"])
        superseded = (
            f"  ({CRITERION_STANDING_SUPERSEDED} by {replacement})" if replacement else ""
        )
        out.append(
            f"  {criterion['id']}  {criterion['state']}{superseded}  {criterion['text']}"
        )
        if criterion["reason"]:
            out.append(f"      reason: {criterion['reason']}")
        for verification in criterion["verifications"]:
            evidence = (
                f"  evidence: {verification['evidence_id']}"
                if verification["evidence_id"]
                else ""
            )
            out.append(
                f"      {verification['id']}  {verification['verdict']}  "
                f"{verification['method']}  {verification['author']}  "
                f"{verification['created_at']}{evidence}"
            )

    out.append("")
    out.append(f"touches ({len(doc['touches'])}):")
    if not doc["touches"]:
        out.append("  (none)")
    for touch in doc["touches"]:
        # KYO-225.c3. The path column is the PATH — a new-file touch's `+` is a
        # statement about the touch, not part of what it claims, so it never appears
        # here. What appears instead is the note, and only while the file is still
        # absent: once the work has landed, the touch is an ordinary file touch and
        # this renders it as exactly that, with nobody having edited anything.
        # `pending_touches` is the caller's answer to "which of these are still to be
        # created" — handed in rather than computed here for the same reason
        # `standings` and `link_titles` are (this renderer has no project root, and
        # should not grow one).
        target = touch_target(touch["path"]) + (
            f"#{touch['symbol']}" if touch["symbol"] else ""
        )
        note = f"  {TOUCH_NEW_FILE_SYMBOL}" if touch["path"] in pending else ""
        # KYO-300.c3. The stored form is printed as stored — `path#+` is what a
        # controller would type back into `kyo edit --touches-file`, so replacing it
        # with the sentence would make the display unusable as input (the mistake the
        # `+` marker was deliberately kept out of `touch_target` to avoid). The
        # sentence is added BESIDE it, the way the pending-file note is.
        if sweep_module.is_additive_symbol(touch["symbol"]):
            note = f"  {TOUCH_ADDITIVE_SYMBOL}"
        out.append(f"  {touch['id']}  {target}{note}")

    # KYO-137.c1. Rendered next to `touches` because it is the same kind of statement
    # pointed the other way, and a reader comparing "what code does this record move"
    # against "what documents does that affect" is asking one question.
    out.append("")
    out.append(f"doc-refs ({len(doc['doc_refs'])}):")
    if not doc["doc_refs"]:
        out.append("  (none)")
    for ref in doc["doc_refs"]:
        target = ref["path"] + (f"#{ref['section']}" if ref["section"] else "")
        out.append(f"  {ref['id']}  {target}")

    out.append("")
    out.extend(obligation_lines(doc["docs_obligations"], expand=expand_obligations))
    out.extend(standing_obligation_lines(doc["docs_obligations"]))

    active_gates = [g for g in doc["gates"] if g["cleared_at"] is None]
    out.append("")
    out.append(f"gates ({len(active_gates)} open of {len(doc['gates'])}):")
    if not doc["gates"]:
        out.append("  (none)")
    for gate in doc["gates"]:
        state = "open" if gate["cleared_at"] is None else "cleared"
        # KYO-148: the payload may now be a whole procedure. One line per gate is what
        # keeps this block readable, so a multi-line payload is indexed by its first
        # line and the reader is told where to read the rest — `--json`, because this
        # rendering is the only place `kyo show` prints a gate's payload at all, and a
        # pointer at a surface that does not exist would be worse than none.
        if gate["do"]:
            first, more = elided_first_line(
                gate["do"], more_at=f"in `kyo show {doc['id']} --json`"
            )
            detail = f"  do: {first}{more}"
        else:
            detail = ""
        # KYO-102: the tags before the payload, because the tag is what this gate has in
        # common with every other gate someone might be searching for, and the payload
        # is what is true of this one alone.
        tags = f"  tags: {', '.join(gate['tags'])}" if gate["tags"] else ""
        out.append(f"  {gate['id']}  {gate['type']}  [{state}]{tags}{detail}")
        if gate["cleared_why"]:
            out.append(f"      cleared because: {gate['cleared_why']}")
        # KYO-88: the claims go under the OPEN gates only. A cleared gate is no
        # longer a question, so putting the record's answers beside it would be
        # noise in the one rendering this record exists to keep readable.
        if state == "open":
            out.extend(claims_beside_gate(doc["claims"]))

    out.append("")
    out.append(f"claims ({len(doc['claims'])}):")
    if not doc["claims"]:
        out.append("  (none)")
    for claim in doc["claims"]:
        out.append(
            f"  {claim['id']}  {claim['level']}  {claim['author']}  {claim['created_at']}"
            f"{_claim_marker(claim)}"
        )
        out.append(f"      method: {claim['method']}")
        out.extend(f"    | {line}" for line in claim["statement"].split("\n"))

    out.append("")
    out.append(f"evidence artifacts ({len(doc['evidence_artifacts'])}):")
    if not doc["evidence_artifacts"]:
        out.append("  (none)")
    for artifact in doc["evidence_artifacts"]:
        supports = f"  supports: {artifact['supports_id']}" if artifact["supports_id"] else ""
        out.append(
            f"  {artifact['id']}  sha256:{artifact['sha256'][:12]}  "
            f"{artifact['author']}  {artifact['created_at']}{supports}"
        )

    out.append("")
    out.append(f"dispatches ({len(doc['dispatches'])}):")
    if not doc["dispatches"]:
        out.append("  (none)")
    for dispatch in doc["dispatches"]:
        # KYO-7.c5: `state` alone is always one of running/reported/terminated-no-
        # report -- never rendered as anything that reads like "done". `closed`
        # is shown as a separate, orthogonal fact, never folded into `state`.
        closed = f"  closed: {dispatch['closed_at']}" if dispatch["closed_at"] else ""
        out.append(
            f"  {dispatch['id']}  state: {dispatch['state']}  "
            f"base sha: {dispatch['worked_from_sha']}{closed}"
        )
        # KYO-61: always a line, never only when set. "no model was declared for
        # this dispatch" is the fact a reader most needs and the one an omission
        # hides — it is what every dispatch on this board said before the field
        # existed, and silence would render it identically to a chosen default.
        out.append(
            "      worker model: "
            + (dispatch["worker_model"] or "(none declared — nothing said what ran this)")
        )
        if dispatch["report_id"]:
            # KYO-90.c3: when a dispatch carries more than one report, the line that
            # names "the report" must say which one it means and that the others
            # exist. A reader who sees only `report: KYO-83.r1` on a dispatch whose
            # account was later corrected is reading the superseded one as the
            # account of what happened, which is the failure this record is about.
            superseded = [
                r["id"]
                for r in doc["reports"]
                if r["dispatch_id"] == dispatch["id"] and r["id"] != dispatch["report_id"]
            ]
            trail = f"  (current; supersedes {', '.join(superseded)})" if superseded else ""
            out.append(f"      report: {dispatch['report_id']}{trail}")
        if dispatch["terminated_why"]:
            # KYO-345.c2: one column holds the reason a dispatch ended with no report,
            # and the WORD in front of it is derived from the state. `kyo show
            # <record-id>` is the one surface that prints a withdrawn dispatch at all
            # (it is in no brief list), so it is also the one surface where printing
            # "terminated because" would tell a reader the opposite of what happened.
            out.append(
                f"      {dispatch_close_reason_label(dispatch['state'])}: "
                f"{dispatch['terminated_why']}"
            )
        if dispatch["touch_override"]:
            # KYO-48: not only collisions any more — a `--force` past a
            # disjointness question the tool could not answer is recorded here too,
            # and each entry says which it was in its `why`.
            out.append(f"      touch checks overridden: {dispatch['touch_override']}")
        if dispatch["covers"]:
            out.append(f"      also formally covers: {', '.join(dispatch['covers'])}")

    # KYO-291: the other side of the `also formally covers` line just above. On the
    # primary record a dispatch says which records it spoke for; on each of those
    # records, until now, nothing said anything at all — `dispatches (0)`, `reports (0,
    # 0 superseded)`, and no way to reach the report whose verdicts settled this
    # record's criteria.
    #
    # Its own heading, never folded into `dispatches (N)`: that count is this record's
    # OWN dispatches and stays one, because "who was sent to work on this record" and
    # "who spoke for it" are different questions and a single number cannot answer both.
    #
    # Printed only when there is something to print, which is the one place this block
    # departs from the always-a-section rule the sections around it follow. The reason
    # is that this is not a child object of the record — there is no "this record has no
    # covering dispatches" fact to state, only the ordinary case of a record nobody's
    # dispatch covered, which is nearly every record. `--json` carries the key
    # unconditionally (`covered_by_dispatches`, `[]` when empty), so a machine reader
    # loses nothing by the silence here.
    covering = list(covered_by or [])
    if covering:
        out.append("")
        out.append(f"covered by dispatches on other records ({len(covering)}):")
        for entry in covering:
            report_of = (
                f"report: {entry['report_id']}"
                if entry["report_id"]
                else "report: (none ingested yet)"
            )
            out.append(
                f"  {entry['id']}  state: {entry['state']}  "
                f"dispatched against: {entry['primary_record_id']}  {report_of}"
            )

    out.append("")
    # Named apart from the record ``standings`` parameter this function already
    # carries: one is "what does this record's status mean", the other is "which
    # of these reports is the account of what happened", and a shadowed name here
    # silently retargets the plan-members block below.
    report_standing_by_id = report_standings(doc)
    superseded_total = sum(
        1
        for s in report_standing_by_id.values()
        if s["standing"] == REPORT_STANDING_SUPERSEDED
    )
    # The count is stated the way `flags` states its unresolved count: always, so
    # "none of these was ever corrected" is a printed fact and not an absence.
    out.append(f"reports ({len(doc['reports'])}, {superseded_total} superseded):")
    if not doc["reports"]:
        out.append("  (none)")
    for report in doc["reports"]:
        body = report["body"]
        standing_of = report_standing_by_id[report["id"]]
        out.append(
            f"  {report['id']}  dispatch: {report['dispatch_id']}  {report['author']}  "
            f"{report['created_at']}  [{standing_of['standing']}]"
        )
        if standing_of["amended_by"]:
            out.append(
                f"      SUPERSEDED by {standing_of['amended_by']} — this is not the account of "
                f"what happened on {report['dispatch_id']}; it is what was said before the "
                "correction, kept because the verdicts and findings below were recorded "
                "against it"
            )
        if standing_of["amends"]:
            out.append(
                f"      amends {standing_of['amends']}, which stays readable above; the checks "
                "`kyo report` runs (branch reachability, touch drift) ran against this document"
            )
        out.append(
            f"      premise_false: {len(body['premise_false'])}  "
            f"disagreements: {len(body['disagreements'])}  "
            f"corrected_numbers: {len(body['corrected_numbers'])}  "
            f"criteria_verdicts: {len(body['criteria_verdicts'])}  "
            f"commits: {len(body['commits'])}"
        )
        # KYO-241.c3: the count above already said how many; this says which ones,
        # and beside each sha `subject` if the worker wrote one (KYO-270 retired
        # the older `summary` -- `commit_label` reads only `subject`).
        for commit in body["commits"]:
            out.append(f"        - {commit_label(commit)}")

    unresolved_count = sum(1 for f in doc["flags"] if not f["resolved_at"])
    out.append("")
    out.append(f"flags ({len(doc['flags'])}, {unresolved_count} unresolved):")
    if not doc["flags"]:
        out.append("  (none)")
    for flag in doc["flags"]:
        state = "resolved" if flag["resolved_at"] else "unresolved"
        other = f"  vs {flag['other_criterion_id']}" if flag["other_criterion_id"] else ""
        out.append(f"  {flag['id']}  {flag['flag_type']}  [{state}]{other}")
        out.append(f"      {flag['detail']}")
        if flag["resolved_at"]:
            out.append(
                f"      resolved {flag['resolved_at']} by {flag['resolved_by']}"
                + (f": {flag['resolved_why']}" if flag["resolved_why"] else "")
            )

    out.append("")
    out.append(f"links ({len(doc['links'])}):")
    if not doc["links"]:
        out.append("  (none)")
    for link in doc["links"]:
        # KYO-47.c2: the same directional sentence `kyo link` echoes. The old
        # rendering flipped an arrow (`->` / `<-`) around a constant subject, so
        # both endpoints of a backwards edge read as plausible; here the edge is
        # always written from its own source to its own destination, whichever end
        # of it this record happens to be.
        out.append(f"  {link['id']}  {link['direction']}")
        if link["direction"] == "outgoing":
            src_id, dst_id = doc["id"], link["other"]
            src_title, dst_title = doc["title"], (link_titles or {}).get(link["other"])
        else:
            src_id, dst_id = link["other"], doc["id"]
            src_title, dst_title = (link_titles or {}).get(link["other"]), doc["title"]
        out += edge_sentence(src_id, src_title, link["type"], dst_id, dst_title, indent="    ")

    out.append("")
    out.append(f"notes ({len(doc['notes'])}, append-only):")
    if not doc["notes"]:
        out.append("  (none)")
    for note in doc["notes"]:
        marker = f" [superseded by {note['superseded_by']}]" if note["superseded_by"] else ""
        out.append(f"  {note['id']}  {note['created_at']}{marker}")
        out.extend(f"    | {line}" for line in note["text"].split("\n"))

    out += _members_block(members or [], standings)
    return "\n".join(out)


def map_text(nodes: list[dict], edges: list[dict]) -> str:
    """Indented human rendering of ``kyo map`` — the circles-and-arrows kind registry.

    ``--json`` is the real contract (a future viewer's input); this is a legible
    fallback, not a second source of truth — it is derived from the same ``nodes``/
    ``edges`` the JSON output carries.
    """
    out: list[str] = []
    kinds_by_id = {n["id"]: n for n in nodes if n["type"] == "kind"}
    incoming: dict[str, list[str]] = {}
    for edge in edges:
        incoming.setdefault(edge["to"], []).append(edge["from"])

    out.append(f"kinds ({len(kinds_by_id)}):")
    for name in sorted(kinds_by_id):
        node = kinds_by_id[name]
        out.append(f"  {name}  [{node['archetype']}]  {node['summary']}")
        sources = ", ".join(sorted(incoming.get(name, []))) or "(none)"
        out.append(f"    accepts from: {sources}")
        targets = sorted(e["to"] for e in edges if e["from"] == name)
        out.append(f"    produces:     {', '.join(targets) or '(none)'}")
        policy = ", ".join(f"{k}={v}" for k, v in sorted(node["policy"].items()))
        out.append(f"    policy:       {policy}")

    pseudo = sorted(n["id"] for n in nodes if n["type"] == "pseudo")
    out.append(f"pseudo-sources: {', '.join(pseudo)}")
    return "\n".join(out)


# ------------------------------------------------------------------------- M6


def sweep_text(data: dict) -> str:
    """``kyo sweep``'s human rendering (KYO-8.c1/c2/c3).

    KYO-63: the base-sha commentary is driven by the run's recorded ``coverage`` rather
    than re-derived from the two shas here. The old form described what it *assumed* had
    happened ("diffed X..Y") and said it identically whether or not the diff ran — and
    the "(no flags)" line is what makes that dangerous, because it reads as "nothing is
    stale" in either case.
    """
    widened = data.get("widened_flags") or []
    if data.get("recorded") is False:
        # KYO-418.c8: a no-op run writes nothing, so it has no id of its own to lead
        # with -- it names the recorded run whose answer it repeats instead.
        out = [
            f"(not recorded — same base as {data['unchanged_since']}, nothing new): checked "
            f"{data['checked']} swept record(s), created 0 flag(s), widened 0"
        ]
    else:
        out = [
            f"{data['sweep_run_id']}: checked {data['checked']} swept record(s), "
            f"created {len(data['flags'])} flag(s), widened {len(widened)}"
        ]
    out.append(
        "  staleness: "
        + sweep_module.coverage_sentence(data["coverage"], data["coverage_detail"])
    )
    if not data["flags"] and not widened:
        out.append(
            "  (no flags)"
            if sweep_module.staleness_answered(data["coverage"])
            else "  (no flags — but staleness was not answered, so this is not a clean bill)"
        )
    for flag in data["flags"]:
        out.append(f"  {flag['flag_id']}  {flag['record_id']}  {flag['flag_type']}")
        out.append(f"      {flag['detail']}")
    # KYO-69: a re-observed finding is reported as widened, never as new. Printing it in
    # the "created" list is how the same finding came to be filed nine times against one
    # record and read, each time, like nine separate things going wrong.
    for flag in widened:
        out.append(f"  {flag['flag_id']}  {flag['record_id']}  {flag['flag_type']}  [widened]")
        out.append(f"      {flag['detail']}")
    return "\n".join(out)


def flag_collapse_text(data: dict) -> str:
    """``kyo flag collapse``'s human rendering (KYO-69.c3).

    Says the before/after count in both modes, and says which mode it is in on its first
    line: a migration whose dry run and whose apply look alike is one people run twice
    believing the first was the rehearsal.
    """
    mode = "APPLIED" if data["applied"] else "dry run — nothing written"
    out = [
        f"flag collapse ({mode}): {len(data['plan'])} group(s) across "
        f"{data['records_affected']} record(s); "
        f"{data['flags_superseded']} duplicate flag(s) closed as superseded"
    ]
    out.append(
        f"  unresolved flags: {data['unresolved_before']} -> {data['unresolved_after']}"
    )
    if not data["plan"]:
        out.append("  (nothing to collapse — every record has at most one open finding)")
    for item in data["plan"]:
        out.append(
            f"  {item['record_id']}  keep {item['survivor_flag_id']}, supersede "
            + ", ".join(item["superseded_flag_ids"])
        )
        out.append(f"      was:  {item['survivor_detail_before']}")
        out.append(f"      now:  {item['survivor_detail_after']}")
    if data.get("dry_run_note"):
        out.append(f"  {data['dry_run_note']}")
    return "\n".join(out)


def flag_settle_text(data: dict) -> str:
    """``kyo flag resolve --settled``'s human rendering (KYO-346.c3).

    Same shape as :func:`flag_collapse_text` on purpose — dry run/apply stated on
    the first line, before/after unresolved counts, and per-record detail — this is
    the same migration pattern KYO-69 built, run against a different accumulation.
    """
    mode = "APPLIED" if data["applied"] else "dry run — nothing written"
    out = [
        f"flag resolve --settled ({mode}): {data['flags_closed']} staleness flag(s) "
        f"across {data['records_affected']} record(s) already settled"
    ]
    out.append(
        f"  unresolved flags: {data['unresolved_before']} -> {data['unresolved_after']}"
    )
    if data["by_type"]:
        spread = ", ".join(f"{t}:{n}" for t, n in sorted(data["by_type"].items()))
        out.append(f"  by type: {spread}")
    if not data["plan"]:
        out.append("  (nothing to settle — no open staleness flag sits under a settled record)")
    for item in data["plan"]:
        spread = ", ".join(f"{t}:{n}" for t, n in sorted(item["by_type"].items()))
        out.append(f"  {item['record_id']} ({item['status']}): {spread}")
    if data.get("dry_run_note"):
        out.append(f"  {data['dry_run_note']}")
    return "\n".join(out)


def _readiness_row(item: dict) -> str:
    return f"  {item['id']:<10} {item['kind']:<14} {item['status']:<9} {item['title']}"


def _blocked_detail(item: dict) -> str:
    """KYO-570: each unsettled blocker and its status, on one line."""
    return "blocked by " + ", ".join(f"{b['id']} ({b['status']})" for b in item["blockers"])


#: KYO-267: what the ungated-unobserved section says about itself, in both renderings
#: and at zero. It names the remedy because the whole defect is that nothing did: a
#: controller who records `unmet-unobserved` for a person's observation has to be told,
#: somewhere they are already looking, that the gate is what makes the wait findable.
#:
#: Under ``--compact`` (KYO-419) it prints only when the section has an item: an empty
#: section collapses into the one "empty:" line, and a remedy for nothing is only
#: length. With an item waiting it still prints, as KYO-267 decided:
#: the compact form is what the ``SessionStart`` hook injects, and a remedy visible
#: only in the long form is one the session never sees.
UNGATED_UNOBSERVED_SENTENCE = (
    "a criterion is waiting on somebody to look, nothing is running against the record, "
    'and no `awaiting:*` gate says so — `kyo gate <id> awaiting:human-run --do "..."` is '
    "what puts it in the Awaiting list; nothing else does"
)


#: KYO-289: the parenthetical that DEFINES "finished but not moved", rendered once so
#: `kyo brief` and `kyo next` cannot describe the same class differently. Until KYO-289
#: both said "a dispatch reported and every criterion is settled", which was the
#: condition the class was computed from — and that condition was the defect: work done
#: in-session, or verified by the maintainer by hand, settles its criteria without any
#: dispatch to report, and 23 such records on this store's own board were being offered
#: as dispatchable. The class is the criteria; how the work was done is not part of it.
SETTLED_NOT_MOVED_SENTENCE = (
    "every criterion is settled, however the work was done — nothing is left to "
    "build and only the status move is outstanding, which is the orchestrator's"
)


def _settled_not_moved_detail(item: dict) -> str:
    """The per-record line under "finished but not moved" (KYO-87, KYO-289).

    One function for both surfaces, for KYO-87.c3's reason. ``reported_by`` is always a
    list and is routinely empty now that the class no longer requires a dispatch, so the
    reporters are named only when there are any — a bare "reported and every criterion
    is settled" with nothing before it was the first thing this change would have
    printed, and it asserts a dispatch that does not exist.
    """
    reported = list(item.get("reported_by") or [])
    settled = "every criterion is settled"
    if reported:
        settled = f"{', '.join(reported)} reported; {settled}"
    return f"{settled} — needs `{item['needs']}`"


def _ungated_unobserved_lines(items: list[dict], *, heading: str, compact: bool) -> list[str]:
    """The ungated-unobserved section, rendered once for `kyo brief` and `kyo next`
    (KYO-267).

    Both surfaces read one computation (``cmd_next.ungated_unobserved_records``); this
    is the other half of that — one renderer, so they cannot phrase the same rows
    differently either. The criterion id and its stored reason go on the line beneath
    the record, because the reason is what the person is being asked to observe and the
    controller composing the gate needs it in front of them, not one `kyo show` away.
    """
    out = ["", heading]
    # KYO-419.c6: the compact form (the SessionStart hook's) drops it when there is
    # nothing waiting -- an empty section collapses to one line anyway. With an item
    # waiting it stays, because that is the moment KYO-267 printed it for: the remedy
    # is what makes the wait findable, and the session start is where it is read.
    if not compact or items:
        out.append(f"  ({UNGATED_UNOBSERVED_SENTENCE})")
    if not items:
        out.append("  (none)")
    for item in items:
        out.append(_readiness_row(item))
        if compact:
            continue
        for criterion in item["criteria"]:
            reason = criterion.get("reason") or "(no reason recorded)"
            out.append(f"      {criterion['id']}  {criterion['state']}  {reason}")
        out.append(f"      remedy: {item['remedy']}")
    return out


def next_text(
    ready: list[dict],
    scope_unknown: list[dict],
    settled_not_moved: list[dict] | None = None,
    ungated_unobserved: list[dict] | None = None,
    *,
    blocked: list[dict] | None = None,
) -> str:
    """``kyo next``'s human rendering (KYO-8.c5).

    KYO-48: the unknown-scope count is printed even when it is zero. The class
    exists whether or not it is occupied, and a heading that appears only when
    something is in it teaches a reader that its absence means nothing was
    checked — which is the habit that let a zero-touch record read as ready.

    KYO-87 adds the third class on the same reasoning, and to the same standard:
    `kyo next` and `kyo brief` read one computation, so neither can call a record
    ready while the other calls it finished (KYO-87.c3).
    """
    out = [f"{len(ready)} ready record(s):"]
    if not ready:
        out.append("  (none)")
    for item in ready:
        out.append(_readiness_row(item))
        # KYO-87: worked before, and still genuinely dispatchable. The row is not
        # moved out of this list — something is unsettled and re-dispatching is a
        # real answer — but "somebody already built against this" is not a fact to
        # make the reader go and find.
        if item.get("prior_dispatch"):
            out.append(f"      {item['prior_dispatch']}")
    out.append("")
    out.append(f"{len(scope_unknown)} record(s) with unknown scope (NOT ready):")
    if not scope_unknown:
        out.append("  (none)")
    for item in scope_unknown:
        out.append(_readiness_row(item))
        out.append(f"      {item['reason']}")
    if scope_unknown:
        out.append(f"  remedy: {SCOPE_UNKNOWN_REMEDY}")

    # KYO-570. Printed at zero for KYO-48's reason; the brief prints the same rows.
    held = list(blocked or [])
    out.append("")
    out.append(f"{len(held)} record(s) blocked by an unsettled record (NOT ready):")
    if not held:
        out.append("  (none)")
    for item in held:
        out.append(_readiness_row(item))
        out.append(f"      {_blocked_detail(item)}")
    if held:
        out.append(f"  remedy: {BLOCKED_REMEDY}")

    settled = list(settled_not_moved or [])
    out.append("")
    out.append(f"{len(settled)} record(s) finished but not moved (NOT ready):")
    # KYO-289: the same definition `kyo brief` prints, from the same constant. The
    # class stopped being "a dispatch reported", and a surface that still said so
    # would be telling a controller the 23 records it just started withholding are
    # in some other class than the one they are in.
    out.append(f"  ({SETTLED_NOT_MOVED_SENTENCE})")
    if not settled:
        out.append("  (none)")
    for item in settled:
        out.append(_readiness_row(item))
        out.append(f"      {_settled_not_moved_detail(item)}")

    # KYO-267. Not a readiness class: these records may well still be dispatchable and
    # are listed above if they are. What they also are is *waiting on a person*, with
    # nothing on the board that says so — which is why the section is here rather than
    # only in `kyo brief`. The two surfaces read one computation (KYO-87.c3).
    unobserved = list(ungated_unobserved or [])
    out += _ungated_unobserved_lines(
        unobserved,
        heading=f"{len(unobserved)} record(s) with an unobserved criterion and no gate:",
        # `kyo next` has no --compact; the long form is the only form it has.
        compact=False,
    )
    return "\n".join(out)


#: KYO-94: the two ``handoff["basis"]`` values, restated here only so the renderer does
#: not import ``cmd_brief`` (which imports ``render``). ``tests/test_notes.py`` checks
#: they still match the values the command produces.
HANDOFF_BASIS_SESSION = "session-start"
HANDOFF_BASIS_NO_SESSION = "no-session-recorded"

#: KYO-94: what a redacted shift note prints where its prose would go. `kyo redact`
#: removed that text deliberately, and neither the read path nor the guaranteed handoff
#: may become the way it comes back.
SHIFT_NOTE_REDACTED_LINE = "(text removed by `kyo redact`; there is nothing here to read)"


def _shift_note_body(note: dict, indent: str) -> list[str]:
    """A shift note's prose, or the fact that it was redacted — the one computation both
    renderings below go through (law 3), so a redacted note cannot print its text in one
    surface and its redaction in the other."""
    if note.get("redacted_at"):
        return [f"{indent}{SHIFT_NOTE_REDACTED_LINE}"]
    return [f"{indent}| {line}" for line in note["text"].split("\n")]


def shift_note_lines(note: dict, *, indent: str = "  ") -> list[str]:
    """One shift note as the brief lists it: an id/time/author line, then the body."""
    head = f"{indent}{note['id']}  {note['created_at']}  {note['author']}"
    if note.get("redacted_at"):
        head += f"  [redacted {note['redacted_at']} because: {note['redacted_why']}]"
    return [head, *_shift_note_body(note, f"{indent}  ")]


def shift_note_text(note: dict) -> str:
    """``kyo show PREFIX-SN`` (KYO-94.c1).

    A shift note could be *written* by id and *deleted* by id — `kyo redact` parses this
    same ``PREFIX-SN`` shape — and read exactly once, by whoever happened to run the next
    `kyo brief`. This is the missing third verb. It is deliberately not a record
    rendering: a shift note is append-only and never amended, which is the property that
    makes the shift log worth reading at all, and this says so rather than leaving a
    reader to infer it from the absence of an edit path.
    """
    out = [f"{note['id']}  [shift note]  append-only; never amended"]
    out.append(f"  author:  {note['author']}")
    out.append(f"  written: {note['created_at']}")
    if note.get("redacted_at"):
        out.append(f"  redacted: {note['redacted_at']} because: {note['redacted_why']}")
    out.append("")
    out.append("text:")
    out += _shift_note_body(note, "  ")
    return "\n".join(out)


#: What a flag's ``resolved_at`` reads as in the ``[...]`` slot, both ways round. Named
#: because `kyo show <flag-id>` and `kyo flag list` both print it and they must agree
#: (law 3) — a listing that calls a flag open beside a detail view that calls it
#: resolved is the failure KYO-220 exists to end, one screen smaller.
FLAG_STATE_OPEN = "open"
FLAG_STATE_RESOLVED = "resolved"


def flag_state(flag: dict) -> str:
    return FLAG_STATE_OPEN if flag.get("resolved_at") is None else FLAG_STATE_RESOLVED


def flag_line(flag: dict) -> str:
    """One flag, as ``kyo flag list`` prints it: id, type, record, state (KYO-220.c1)."""
    return (
        f"  {flag['id']:<14} {flag['flag_type']:<26} {flag['record_id']:<10} "
        f"[{flag_state(flag)}]"
    )


def flag_text(flag: dict) -> str:
    """``kyo show <flag-id>`` — one flag in full (KYO-220.c1).

    A flag could be raised by `kyo sweep`, ranked by `kyo brief`, resolved by
    `kyo flag resolve` and collapsed by `kyo flag collapse`, and read back by nothing:
    a consumer wanting to know what one of their flags actually said had to run
    `kyo brief --json` and pick the flag out of the brief document by hand. Four verbs
    and no reader, which is the same hole KYO-94 closed for shift notes and KYO-177 for
    dispatches — this is the third of them, rendered the way those two are.

    ``sweep_run_id`` and the sha range are printed unconditionally for a
    ``touch-changed`` flag, including when they are absent: "no sweep recorded this"
    and "this flag's detail did not parse as a range" are facts a reader has to be able
    to see, and a line that appears only when a value exists makes its absence
    indistinguishable from a build that has no such field (law 6, the KYO-100 rule).
    """
    state = flag_state(flag)
    out = [
        f"flag {flag['id']}  [{state}]  {flag['flag_type']} on {flag['record_id']}",
        echo_value("raised", flag["created_at"]),
        echo_value("raised by sweep", flag["sweep_run_id"] or "(no sweep run recorded)"),
    ]
    finding = None
    if flag["flag_type"] == FLAG_TYPE_TOUCH_CHANGED:
        # The two facts the consumer report says they went to `kyo brief --json` for.
        # Read back out of the detail through the sweep module's own parser rather than
        # re-split here: `format_touch_changed_detail` is the one place the string is
        # written, and this is its documented inverse (law 3).
        finding = sweep_module.parse_touch_changed_detail(flag["detail"])
    if finding is None:
        # Either not a touch-changed flag, or one whose detail this build cannot parse.
        # Both print the stored prose verbatim; the second says so first, because a
        # detail block with no range above it would otherwise read as a flag type that
        # simply has no range (law 6 — an unknown must not read as either answer).
        if flag["flag_type"] == FLAG_TYPE_TOUCH_CHANGED:
            out.append(
                "  (this flag's detail is not in the form `kyo sweep` writes, so no "
                "range or path list could be read out of it — the stored text follows)"
            )
        if flag["other_record_id"]:
            out.append(echo_value("other record", flag["other_record_id"]))
        if flag["other_criterion_id"]:
            out.append(echo_value("other criterion", flag["other_criterion_id"]))
        out += echo_block("detail", flag["detail"])
    else:
        # The detail IS the range, the paths and the sweep count, joined into one
        # sentence by `format_touch_changed_detail`. Printed decomposed and not also
        # verbatim: an echo block underneath would be the same facts a second time, and
        # on this store's own flags that is a 495-character repeat of the line above it.
        out.append(echo_value("sha range", f"{finding.from_sha}..{finding.to_sha}"))
        out.append(echo_value("paths", ", ".join(finding.paths)))
        out.append(echo_value("sweeps that have observed it", str(finding.sweeps)))
    if state == FLAG_STATE_OPEN:
        out.append(
            f"not resolved — `kyo flag resolve {flag['id']} --why-file <file>` records "
            "who judged it and why"
        )
        return "\n".join(out)
    out.append(echo_value("resolved", flag["resolved_at"]))
    out.append(echo_value("resolved by", flag["resolved_by"] or "(not recorded)"))
    if flag["resolved_why"]:
        out += echo_block("resolved because", flag["resolved_why"])
        # KYO-69's collapse closes duplicates without anyone judging them, and the
        # schema has one resolution path for both. The distinction lives in the reason,
        # so the reader of a single flag is told which of the two this was rather than
        # being left to recognise a prefix.
        if sweep_module.is_superseded(flag["resolved_why"]):
            out.append(
                "  (closed as a duplicate by `kyo flag collapse`, NOT judged and "
                "dismissed — its range and paths were merged into the surviving flag)"
            )
    else:
        out.append("resolved with no reason recorded")
    return "\n".join(out)


def criterion_text(criterion: dict, *, record_id: str) -> str:
    """``kyo show <criterion-id>`` — one criterion and its whole verdict history (c2).

    The history, not the current state alone: a criterion's ``state`` is DERIVED from
    its latest verification (schema.py, the ``verifications`` table), so a rendering
    that printed only the state would be printing a conclusion whose evidence this
    store keeps and this command declined to show.
    """
    replacement = replaced_by(criterion["state"], criterion["reason"])
    superseded = (
        f"  ({CRITERION_STANDING_SUPERSEDED} by {replacement})" if replacement else ""
    )
    out = [
        f"criterion {criterion['id']}  [{criterion['state']}]{superseded}  on {record_id}",
        echo_value("created", criterion["created_at"]),
        echo_value("updated", criterion["updated_at"]),
    ]
    if criterion["reason"]:
        out += echo_block("reason", criterion["reason"])
    out += echo_block("text", criterion["text"])
    verifications = criterion["verifications"]
    out.append("")
    out.append(f"verifications ({len(verifications)}):")
    if not verifications:
        out.append("  (none) — this criterion has never been verified")
    for verification in verifications:
        evidence = (
            f"  evidence: {verification['evidence_id']}"
            if verification["evidence_id"]
            else ""
        )
        out.append(
            f"  {verification['id']}  {verification['verdict']}  "
            f"{verification['method']}  {verification['author']}  "
            f"{verification['created_at']}{evidence}"
        )
        if verification["reason"]:
            out.append(f"      reason: {verification['reason']}")
    return "\n".join(out)


def evidence_text(evidence: dict, *, record_id: str) -> str:
    """``kyo show <evidence-id>`` — one evidence artifact, or its redaction (c2).

    A redacted artifact renders the redaction and not the text, because after
    `kyo redact` there IS no text and no digest of it (schema.py, ``evidence_artifacts``:
    the sha256 of a short identifier is brute-forceable, so the digest goes with the
    text). Saying so explicitly is the point — an empty ``text:`` block would read as an
    artifact someone attached nothing to.
    """
    out = [f"evidence {evidence['id']}  on {record_id}"]
    out.append(echo_value("author", evidence["author"]))
    out.append(echo_value("attached", evidence["created_at"]))
    out.append(
        echo_value("supports", evidence["supports_id"] or "(no criterion named)")
    )
    if evidence["redacted_at"]:
        out.append(echo_value("redacted", evidence["redacted_at"]))
        out += echo_block("redacted because", evidence["redacted_why"] or "")
        out.append(
            "text: (redacted — the artifact's text and its sha256 were both removed; "
            "the digest went with the text deliberately)"
        )
        return "\n".join(out)
    out.append(echo_value("sha256", evidence["sha256"]))
    out.append("")
    out += echo_block("text", evidence["text"])
    return "\n".join(out)


def handoff_lines(handoff: dict | None, *, in_window: set[str] | None = None) -> list[str]:
    """KYO-94.c4 — the shift note that arrives whatever the watermark says.

    Always a block, never only when occupied, and never dropped by ``--compact``:
    ``--compact`` is what the packaged ``SessionStart`` hook runs, and a handoff
    summarised to its id is not a handoff. The block also states *which* definition of
    "this session" produced the note, because the fallback ("no brief has ever declared a
    session start") answers a weaker question and must not read like the strong one.
    """
    if handoff is None:  # a brief document from before this field existed
        return []
    note = handoff["note"]
    anchor = handoff["session_brief_id"]
    when = handoff["session_started_at"]
    if handoff["basis"] == HANDOFF_BASIS_SESSION:
        where = f" (this session opened at {anchor}, {when})" if anchor else ""
        out = [f"handoff — last shift note authored before this session began{where}:"]
    else:
        out = ["handoff — most recent shift note:"]
    if note is None:
        out.append("  (none — no shift note was written before this session began)")
    else:
        out += shift_note_lines(note)
        if in_window and note["id"] in in_window:
            out.append("    ^ also listed in the shift log above; one note, shown twice")
        out.append(f"    (readable at any time with `kyo show {note['id']}`)")
    if handoff["basis"] == HANDOFF_BASIS_NO_SESSION:
        out.append(
            "  ^ no brief run has ever declared a session start, so this is the most "
            "recent note rather than the last one before YOUR session — install the "
            "SessionStart hook (`kyo harness install claude-code`, or `kyo install-hooks "
            "--copies`) to make the boundary real"
        )
    return out


def _build_label(side: dict) -> str:
    short = side.get("commit_short") or "(unknown)"
    when = side.get("commit_time")
    return f"build {short} (committed {when})" if when else f"build {short}"


#: KYO-541: how many records the BUILD CHANGED notice names. The notice is for noticing;
#: `kyo changelog --since` lists them all. A 29-record notice once pushed the SessionStart
#: brief to 14 KB, which Claude Code saved to a file instead of showing inline.
BUILD_NOTICE_RECORD_LIMIT = 5


def build_change_lines(change: dict | None) -> list[str]:
    """The BUILD notice, and under BUILD CHANGED every kind-registry notice (KYO-556.c6).

    The notices come after the build's own lines and before the blank line that ends
    the notice: an upgrade that added a grant or a policy key to the packaged kinds.yml
    states what it asks of this store in the one place a session is sure to read.
    """
    out = _build_change_lines(change)
    notices = (change or {}).get("registry_notices") or []
    if change and change.get("state") == "changed" and notices:
        end = len(out) - 1 if out and out[-1] == "" else len(out)
        extra = [
            "  This build's packaged kinds.yml asks something of this store's copy "
            f"({len(notices)}; `kyo doctor` prints the same notices):"
        ]
        extra.extend(f"    - {notice}" for notice in notices)
        out[end:end] = extra
    return out


def _build_change_lines(change: dict | None) -> list[str]:
    """KYO-398.c4/c5: the notice a session-start brief opens with when the `kyo` under
    the project is not the one its previous session-start brief ran.

    Empty for a brief that is not opening a session, for a project's first session, and
    for an unchanged build — c4's "a brief with an unchanged build prints no notice".
    Rendered in full under ``--compact``: the SessionStart hook runs the compact form,
    and that is the one place a session learns this.
    """
    if not change:
        return []
    state = change.get("state")
    anchor = (
        f"this project's previous session-start brief ({change.get('previous_brief_id')}, "
        f"{change.get('previous_brief_at')})"
    )
    if state == "cannot-tell":
        current = change.get("current") or {}
        recorded = (
            f" This brief records {_build_label(current)}, so the next session can tell."
            if current.get("commit")
            else ""
        )
        return [
            f"BUILD: cannot tell whether the `kyo` under this project changed since {anchor}: "
            f"{change.get('reason')}.{recorded}",
            "",
        ]
    if state != "changed":
        return []
    before, after = change.get("previous") or {}, change.get("current") or {}
    out = [
        f"BUILD CHANGED: the `kyo` running this brief is {_build_label(after)}; {anchor} "
        f"ran {_build_label(before)}."
    ]
    if after.get("source") == SOURCE_GIT:
        out.append(
            "  This copy runs live from a working tree, so its build is that checkout's "
            f"HEAD and moves with every commit: `git log {before.get('commit_short')}.."
            f"{after.get('commit_short')}` is what changed."
        )
        out.append("")
        return out
    out.append(
        "  Every project on this machine that runs this copy moved with it — reinstalling kyo "
        "does that, and nothing in this project did."
    )
    if change.get("older"):
        out.append(
            "  This build is OLDER than that one: records completed between the two are no "
            "longer in the tool, and its own changelog cannot name them."
        )
        out.append("")
        return out
    if not (before.get("commit_time") and after.get("commit_time")):
        missing = "the previous build" if not before.get("commit_time") else "this build"
        out.append(
            f"  {missing.capitalize()} carries no commit time (its stamp predates "
            "KYO-398), so the records between the two cannot be placed; "
            "`kyo changelog --all` lists everything this build carries."
        )
        out.append("")
        return out
    total = int(change.get("records_total") or 0)
    records = (change.get("records") or [])[:BUILD_NOTICE_RECORD_LIMIT]
    out.append(
        f"  Records this build carries that the previous one did not ({total}) — from this "
        f"build's own changelog, completed after {before.get('commit_time')} and at or "
        f"before {after.get('commit_time')}:"
    )
    if not records:
        out.append("    (none — the two builds differ by commits that completed no record)")
    for entry in records:
        out.append(
            f"    {entry.get('completed_on')}  {entry.get('id', ''):<10} "
            f"{entry.get('kind', ''):<14} {entry.get('title', '')}"
        )
    if total > len(records):
        out.append(f"    … and {total - len(records)} more: `{change.get('since_command')}`")
    if not change.get("records_exact", True):
        out.append(
            "  Some of these were dated to the day only (this build's packaged changelog "
            "predates completion times), so the list may include a record completed earlier "
            "on the previous build's own day that it already had."
        )
    out.append("")
    return out


#: KYO-419: how the compact brief's one line of empty count-sections begins.
COMPACT_EMPTY_SECTIONS_LEAD = "  empty (0 each): "


def brief_text(data: dict, *, compact: bool) -> str:
    """``kyo brief``'s human rendering (KYO-8.c6). ``compact`` drops the
    per-item detail a hook injects into a session start (ids and counts only).

    KYO-94 carves out one exception to that: :func:`handoff_lines` renders in full
    under ``--compact`` too. The hook runs ``--compact``, so a section abbreviated
    there is abbreviated in the one place it exists for."""
    # KYO-226.c2: the build, beside the brief id, on the first line a session sees.
    # `--compact` does not drop it — the compact form is what the SessionStart hook
    # injects, which is the one place this has to arrive, and "which build is this" was
    # a question no session surface answered at all (a consumer reported a defect their
    # own installed build had already fixed). Read off the payload rather than computed
    # here, so this line and `kyo brief --json` cannot give two answers; `.get` rather
    # than `[]` because a brief document written before this field existed is still a
    # brief document and must still render.
    build = (data.get("build") or {}).get("line")
    header = f"{data['brief_run_id']}  {data['generated_at']}"
    # KYO-398.c4: FIRST, ahead of the header — the code producing every line below is
    # not the code that produced the last session's, and a reader has to know that
    # before trusting what it says about the tool. `.get`: a document from before the
    # field is still a brief.
    out = build_change_lines(data.get("build_change"))
    out.append(f"{header}  {build}" if build else header)

    # KYO-74.c1, first and unconditional when it fires: every count below reads as the
    # whole board, and under the split layout a second checkout's database can be
    # legitimately behind its export — in which case the board this brief describes is
    # not the board the store holds. `--compact` does not drop it; the compact form is
    # what a session-start hook injects, which is the one place this has to arrive.
    seam = data.get("db_export_currency") or {}
    if seam.get("db_is_behind"):
        out.append("")
        out.append(f"WARNING: {seam['says']}.")
        out.append(f"  Every count below is incomplete. To make it current, {seam['remedy']}.")

    # KYO-586.c3: a choice only the operator can make, so it is put to the orchestrator at
    # session start, under `--compact` too, until either answer is recorded.
    if data.get("prior_art_unchosen"):
        out.append("")
        out.append(data["prior_art_unchosen"])

    out.append("")
    out.append("counts by kind:")
    for entry in data["counts_by_kind"]:
        by_status = ", ".join(f"{s}:{n}" for s, n in sorted(entry["by_status"].items()))
        counted = "counted" if entry["counted"] else "not counted"
        out.append(f"  {entry['kind']:<14} total:{entry['total']:<3} ({counted})  {by_status}")

    # KYO-18: `status` was computed on every row and dropped
    # only by this formatter, so a `verified` bug and an `open` one rendered
    # identically under a heading reading "ready work" — and this is the output the
    # skill tells a controller to trust *instead of* reading records. A controller
    # read three fixed bugs as outstanding and argued a priority call on it. The
    # heading gives up the word "ready" for the same reason: `kyo next`'s definition
    # is mechanical (dispatchable, ungated, disjoint) and says nothing about whether
    # any work remains, which is what "ready work" reads as.
    #
    # KYO-87: and the heading still counted finished work. A consumer read
    # "dispatchable now (31)" with 16 records in it that had been dispatched,
    # reported, verified and merged, on the surface a controller is told to read
    # instead of the records, which is where noise costs most. Those rows now have
    # their own section below; the
    # parenthetical here no longer has to hedge that a listed record "may already be
    # finished", because the ones the store can prove are finished are not listed.
    out.append("")
    out.append(f"dispatchable now ({len(data['ready'])}):")
    if not compact:
        out.append("  (ungated, criteria present, disjoint from running dispatches, and still")
        out.append("   unfinished — a record with every criterion settled is below, not here)")
    if not data["ready"]:
        out.append("  (none)")
    for item in data["ready"]:
        out.append(
            f"  {item['id']:<10} {item['kind']:<14} {item.get('status', ''):<9} {item['title']}"
        )
        # The boundary KYO-87 refuses to flatten: reported, but something is still
        # unsettled, so this is genuinely dispatchable and says why it is not below.
        if item.get("prior_dispatch") and not compact:
            out.append(f"      {item['prior_dispatch']}")

    # KYO-48: the class of records that pass every other readiness test and whose
    # disjointness could not be computed at all. They are NOT in the count above —
    # a controller reading this section is reading it to pick something to hand
    # out, and "nobody declared what this touches" has to reach them at that
    # moment or it never will.
    # KYO-419.c1: under `--compact`, a count-section with nothing in it is not given a
    # heading, a "(none)" and a blank line of its own. It is named on ONE line instead
    # (emitted after the last of them), because KYO-48's rule still holds: a heading
    # that appears only when occupied teaches a reader that its absence means nothing
    # was checked. Naming it keeps "checked, and empty" on the page at a line per brief.
    empty: list[str] = []
    unknown = data.get("scope_unknown", [])
    if compact and not unknown:
        empty.append("scope unknown")
    else:
        out.append("")
        out.append(f"scope unknown, NOT dispatchable now ({len(unknown)}):")
    if not compact:
        out.append("  (a record in the comparison declares no touches, so disjointness from the")
        out.append("   running dispatches is unknown — not empty. `kyo dispatch` refuses these)")
        if not unknown:
            out.append("  (none)")
    for item in unknown:
        out.append(
            f"  {item['id']:<10} {item['kind']:<14} {item.get('status', ''):<9} {item['title']}"
        )
        if not compact:
            out.append(f"      {item['reason']}")
    if unknown and not compact:
        out.append(f"  remedy: {SCOPE_UNKNOWN_REMEDY}")

    # KYO-570.c2: the records an unsettled `blocks` source holds back, off the same
    # `_ready_records` call `kyo next` renders. Printed at zero for KYO-48's reason.
    held = data.get("blocked", [])
    if compact and not held:
        empty.append("blocked")
    else:
        out.append("")
        out.append(f"blocked, NOT dispatchable now ({len(held)}):")
    if not compact:
        out.append("  (an incoming `blocks` link's source is not settled yet; `kyo dispatch`")
        out.append("   refuses these without --force)")
        if not held:
            out.append("  (none)")
    for item in held:
        out.append(
            f"  {item['id']:<10} {item['kind']:<14} {item.get('status', ''):<9} {item['title']}"
        )
        out.append(f"      {_blocked_detail(item)}")
    if held and not compact:
        out.append(f"  remedy: {BLOCKED_REMEDY}")

    # KYO-87.c1. Finished, and still sitting where nobody moved it. The class is
    # derived — no unsettled criterion — rather than read off `status = built`,
    # which is the state a controller has to remember to declare and the one they
    # forgot 16 times. KYO-289 dropped the "a dispatch reported" half of that
    # derivation: it excluded exactly the work nobody dispatched, which on this
    # store was 23 of the 28 records the same list was calling dispatchable.
    # Nothing here moves anything: a status change fires transition contracts and
    # is the controller's act, so the section names the move instead of
    # making it. Printed at zero for KYO-48's reason.
    settled = data.get("settled_not_moved", [])
    if compact and not settled:
        empty.append("finished but not moved")
    else:
        out.append("")
        out.append(f"finished but not moved ({len(settled)}):")
    if not compact:
        out.append(f"  ({SETTLED_NOT_MOVED_SENTENCE})")
        if not settled:
            out.append("  (none)")
    for item in settled:
        out.append(
            f"  {item['id']:<10} {item['kind']:<14} {item.get('status', ''):<9} {item['title']}"
        )
        if compact:
            continue
        out.append(f"      {_settled_not_moved_detail(item)}")

    # KYO-18: `built` -> `verified` is a real transition the
    # status flow already models, and its queue is the one whose items need a human
    # for thirty seconds and nobody else at all. It appeared in no section: not
    # dispatchable (nothing to hand out), not gated (no condition to clear), not
    # done.
    awaiting = data.get("awaiting_verification", [])
    if compact and not awaiting:
        empty.append("built, awaiting verification")
    else:
        out.append("")
        out.append(f"built, awaiting verification ({len(awaiting)}):")
        if not awaiting:
            out.append("  (none)")
    for item in awaiting:
        out.append(f"  {item['id']:<10} {item['kind']:<14} {item['title']}")
        if compact:
            continue
        if not item["unsettled_criteria"]:
            out.append(f"      all criteria settled — needs `kyo move {item['id']} verified`")
        for criterion in item["unsettled_criteria"]:
            out.append(f"      {criterion['id']}  {criterion['state']}  {criterion['text']}")

    # KYO-267, and it sits here on purpose: beside "built, awaiting verification",
    # which is the section for "worked, and somebody still has to look". This is the
    # same shape one step further along — the looking has been *asked for*, in a
    # criterion's reason, and nothing on the board carries the ask. KYO-264.c3 and
    # KYO-265.c3 sat in exactly this state and the only surface that held them was a
    # shift note.
    unobserved = data.get("ungated_unobserved", [])
    if compact and not unobserved:
        empty.append("unobserved, waiting on a person, ungated")
    else:
        out += _ungated_unobserved_lines(
            unobserved,
            heading=f"unobserved, waiting on a person, ungated ({len(unobserved)}):",
            compact=compact,
        )

    out.append("")
    out.append(f"gated work ({len(data['gated'])}):")
    if not data["gated"]:
        out.append("  (none)")
    for gate in data["gated"]:
        # KYO-148, and this is the highest-consequence of the three eliding sites: the
        # gated list is board-wide and `--compact` is what the SessionStart hook injects
        # into every session, so one multi-line payload here would run every gate on the
        # board together in the first thing a controller reads.
        if gate["detail"]:
            first, more = elided_first_line(
                gate["detail"], more_at=f"in `kyo show {gate['record_id']} --json`"
            )
            detail = f"  do: {first}{more}"
        else:
            detail = ""
        # KYO-102: the brief's gated list is where a controller reads what the board is
        # waiting on, so the classification belongs here and not only on `kyo show`.
        # Kept even under --compact, which is what the SessionStart hook runs: the tag
        # is the shorter of the two and the one that groups.
        tags = f"  tags: {', '.join(gate['tags'])}" if gate["tags"] else ""
        # KYO-496: how long the board has been waiting, in both forms -- the stored
        # `created_at` was never printed, so a two-week wait read like a new one.
        # `age_days` is computed by cmd_brief against the brief's own stamp.
        age = f"  open {gate['age_days']}d"
        out.append(f"  {gate['record_id']}  {gate['gate_type']}{tags}{age}{detail}" if compact
                   else f"  {gate['record_id']} ({gate['title']})  {gate['gate_id']}  "
                   f"{gate['gate_type']}{tags}{age}{detail}")

    # KYO-497 (KYO-483's C3): the carried items -- decisions waiting on the operator and
    # reminders tied to a date or an event -- placed directly after "gated work", with
    # ages the tool computed rather than day counts a closing note re-typed. Kept under
    # --compact (what the SessionStart hook injects): being seen at session start is the
    # point of it. KYO-521: the heading is the shipped kind's name, `reminder`; the
    # `--json` keys keep the policy's word, `carried`, which is what selects the rows.
    carried = data.get("carried", [])
    if compact and not carried:
        empty.append("reminders")
    else:
        out.append("")
        if not data.get("carried_kinds"):
            out.append(
                "reminders: no kind in kyosei/kinds.yml carries policy.carried, so nothing"
                " is listed here"
            )
        else:
            out.append(
                f"reminders ({len(carried)}; age is whole days since first recorded, oldest"
                " first):"
            )
            if not carried:
                out.append("  (none)")
        for item in carried:
            first, more = elided_first_line(
                item["title"], more_at=f"in `kyo show {item['id']}`"
            )
            waits = "; ".join(
                f"{g['gate_id']} {g['gate_type']}, open {g['age_days']}d"
                for g in item["open_gates"]
            )
            waiting = f"  [waiting: {waits}]" if waits else ""
            status = "" if compact else f"  ({item['status']})"
            out.append(f"  {item['id']}  {item['age_days']}d  {first}{more}{status}{waiting}")

    # KYO-554.c7: the open standing docs obligations -- raised by a record (a decision
    # naming a section) rather than a dispatch, so no report will ever list them and this
    # is where they are seen. Placed after the reminders because both are waits the board
    # carries with no branch behind them. Kept under --compact, which is what the
    # SessionStart hook runs. `.get`: a brief document from before the field.
    standing = data.get("standing_obligations", [])
    if compact and not standing:
        empty.append("standing docs obligations")
    else:
        out.append("")
        out.append(
            f"standing docs obligations ({len(standing)}; raised by a record, not a "
            "dispatch; oldest first):"
        )
        if not standing:
            out.append("  (none)")
        for item in standing:
            title = "" if compact else f" ({item['record_title']})"
            out.append(
                f"  {item['id']}  {item['record_id']}{title}  {item['doc_path']} "
                f"[{item['section']}]  {item['age_days']}d"
            )
        if standing:
            out.append(
                "  (settle each: `kyo docs settle <id> --verdict updated|not-affected "
                "--method <how>`)"
            )

    if compact and not data["running_dispatches"]:
        empty.append("running dispatches")
    else:
        out.append("")
        out.append(f"running dispatches ({len(data['running_dispatches'])}):")
        if not data["running_dispatches"]:
            out.append("  (none)")
    for d in data["running_dispatches"]:
        out.append(f"  {d['id']}  {d['record_id']}  since {d['created_at']}")

    # KYO-314.c1: this list is unanswered ones only — `_open_dispatches` drops a
    # terminated-no-report dispatch once its record is settled or a later dispatch
    # spoke for it and reported. `terminated_no_report_answered_count` is the count
    # this same call found and withheld for that reason; `.get` because a brief
    # document written before this field existed is still a brief document.
    #
    # KYO-345.c2: a `withdrawn` dispatch is not in this list and has no list of its
    # own here, which is the whole point of the state. The heading is a literal word
    # and the rows come from one query over `terminated-no-report` alone, so there is
    # no path by which a withdrawal reaches this section — and nothing about a brief
    # nobody was ever handed is a thing the next controller has to act on. The record
    # keeps it: `kyo show <record-id>` prints the dispatch history unfiltered.
    answered = data.get("terminated_no_report_answered_count", 0)
    if compact and not data["terminated_no_report"]:
        # The withheld-as-answered count keeps a clause on the shared line rather than
        # the two-line explanation the full form prints under a "(none)".
        suffix = f" ({answered} answered, not shown)" if answered else ""
        empty.append(f"terminated-no-report dispatches{suffix}")
    else:
        out.append("")
        out.append(f"terminated-no-report dispatches ({len(data['terminated_no_report'])}):")
        if not data["terminated_no_report"]:
            out.append("  (none)")
    for d in data["terminated_no_report"]:
        out.append(f"  {d['id']}  {d['record_id']}  because: {d['terminated_why']}")
    if answered and not (compact and not data["terminated_no_report"]):
        out.append(
            f"  {answered} more answered (its record settled, or a later dispatch "
            "reported for it) and not shown — `kyo show <record-id>` prints a "
            "record's whole dispatch history, unfiltered"
        )

    if empty:
        out.append("")
        out.append(COMPACT_EMPTY_SECTIONS_LEAD + "; ".join(empty))

    # KYO-63: the staleness question's own answer, immediately above the flag list that
    # would otherwise be the only staleness signal here. An empty flag list means both
    # "nothing is stale" and "the check could not run", and under the split layout the
    # second is one restore away — the store's recorded base sha belongs to a repository
    # the store does not contain. The desktop board and the docs-mode banner show the
    # same third state; this is where the controller's own reading of it lives.
    staleness = data.get("staleness")
    answered = staleness is None or sweep_module.staleness_answered(staleness["coverage"])
    if staleness is not None:
        out.append("")
        out.append(f"staleness coverage ({staleness['coverage']}):")
        out.append(f"  {sweep_module.coverage_sentence(staleness['coverage'])}")
        if not compact and staleness["detail"]:
            out.append(f"      {staleness['detail']}")

    out.append("")
    out.append(f"unresolved sweep flags ({len(data['unresolved_flags'])}):")
    if not data["unresolved_flags"]:
        out.append("  (none)")
        if not answered:
            out.append("  ^ not a clean bill: staleness was not answered (see above)")
    # KYO-69.c2: which half of the touch-changed flags is a staleness warning about work
    # that was actually done, and which half is a record that has merely sat open while
    # the codebase advanced. Both counts print even at zero, on KYO-48's reasoning in
    # `next_text`: a heading that appears only when occupied teaches a reader that its
    # absence means nothing was checked. `cmd_brief._ranked_unresolved_flags` has already
    # ordered the list so the first group is the one worth reading first.
    for scope in sweep_module.TOUCH_CHANGED_SCOPES:
        count = sum(1 for f in data["unresolved_flags"] if f.get("touch_changed_scope") == scope)
        out.append(f"  {sweep_module.TOUCH_CHANGED_SCOPE_HEADINGS[scope]}: {count}")
    if compact:
        # KYO-326.c2: the SessionStart hook runs `kyo brief --compact --session-start`,
        # and nothing at session start acts on one flag rather than on the counts —
        # the per-flag, per-group listing below (the `else` branch, still what the full
        # brief prints)
        # was costing 95 lines of a 15,547-byte compact brief for that reason alone.
        # Counted here by flag type, beside the worked/open-only split above; `kyo flag
        # list` is where the ids actually live, and `--json` still carries every flag
        # under `unresolved_flags` regardless of `--compact`.
        if data["unresolved_flags"]:
            by_flag_type: dict[str, int] = {}
            for flag in data["unresolved_flags"]:
                by_flag_type[flag["flag_type"]] = by_flag_type.get(flag["flag_type"], 0) + 1
            for flag_type in sorted(by_flag_type):
                out.append(f"  {flag_type}: {by_flag_type[flag_type]}")
            out.append("  `kyo flag list` lists them")
    else:
        # KYO-222. Four records whose touches all include a shared file get four flags from
        # ONE commit that appended to it, and the list ranked them as four findings under
        # four records — the reader had to open each to learn they were one event. Grouped
        # on the `from_sha..to_sha` each flag already stores (never by re-running git), so a
        # commit range reads as one line. A flag whose range nothing else shares, and every
        # flag class that has no range at all, renders exactly as it did.
        for group in sweep_module.group_flags_by_range(data["unresolved_flags"]):
            if len(group["flags"]) == 1:
                flag = group["flags"][0]
                scope = flag.get("touch_changed_scope")
                tag = f"  [{scope}]" if scope else ""
                out.append(f"  {flag['id']}  {flag['record_id']}  {flag['flag_type']}{tag}")
                out.append(f"      {flag['detail']}")
                continue
            split = f"{group['worked']} worked, {group['open_only']} open-only"
            out.append(
                f"  {group['range']}  {len(group['flags'])} touch-changed flag(s) from one "
                f"commit range  [{split}]  {', '.join(group['record_ids'])}"
            )
            # `kyo flag resolve` is still per flag (c2), so the ids stay on the page:
            # a grouping that made the members unreachable would have replaced a
            # reading problem with an acting one.
            out.append(
                "      resolve individually: "
                + ", ".join(flag["id"] for flag in group["flags"])
            )
            out.append(f"      {group['flags'][0]['detail']}")

    out.append("")
    out.append(f"shift log since last brief ({len(data['shift_notes'])}):")
    if not data["shift_notes"]:
        out.append("  (none)")
    for note in data["shift_notes"]:
        out.append(f"  {note['id']}  {note['created_at']}  {note['author']}")
        if not compact:
            out.extend(f"    | {line}" for line in note["text"].split("\n"))

    out.append("")
    out += handoff_lines(
        data.get("handoff"),
        in_window={n["id"] for n in data["shift_notes"]},
    )

    # KYO-213: only when there is something to say — the controller's finding was
    # that a consumer upgrading the tool hears about a stale skill/hooks/ledger only
    # from `kyo doctor` at the Stop hook, by which point the session already ran on
    # the old copy. Present in full under `--compact` too, same posture as the
    # handoff block above: `--compact` is what a SessionStart hook injects, which is
    # the one place this has to arrive before anything else gets read.
    asset_drift = data.get("asset_drift", [])
    if asset_drift:
        out.append("")
        out.append("this build vs this project's copies:")
        # ``detail`` already names its own remedy (or, for a reworded rule, says
        # there isn't one) — see `_asset_drift` — so nothing is appended here that
        # would print it a second time.
        for finding in asset_drift:
            out.append(f"  {finding['asset']}: {finding['detail']}")

    out.append("")
    out.append(f"events since previous brief ({len(data['deltas'])}):")
    if data["previous_brief_at"] is None:
        out.append("  (no previous brief; showing every event so far)")
    if not data["deltas"]:
        out.append("  (none)")
    elif compact:
        out.append(f"  ({len(data['deltas'])} event(s); see --json or kyo show for detail)")
    else:
        for event in data["deltas"]:
            record = f" {event['record_id']}" if event["record_id"] else ""
            out.append(f"  {event['at']}  {event['actor']}{record}  {event['command']}")

    if data["orchestrator_rules"]:
        out.append("")
        out.append("orchestrator-audience rules:")
        # KYO-356.c2: printed once, ahead of the rules themselves, so the form a
        # controller copies carries this store's own prefix -- not the skill's
        # `KYO-263`, a different project's id, read once at session start rather
        # than every session `kyo brief` runs.
        # KYO-561: absent when onboarding's [E4] is recorded as declined.
        if data.get("link_example"):
            out.append(f"  cite a record to the maintainer like this: {data['link_example']}")
        # KYO-531: one line per rule in both forms -- the title (the rule's own
        # imperative) and its enforced-by kind in a word. KYO-336 had cut the compact
        # form to a line and KYO-419.c4 to the title; the full form still carried the
        # whole enforced-by argument and the first sentence, up to ~780 characters a
        # line, re-read every session by an orchestrator who has read the rules
        # before. What a returning reader has NOT read is a rule that changed, so
        # that one -- and only that one -- prints in full, marked, measured against
        # the previous brief that recorded rule digests (cmd_brief).
        #
        # KYO-548: but only a NEW rule prints in full. A CHANGED one prints its title
        # and one line saying what changed, and names `kyo rules "<title>"` for the
        # rest: an upstream rename (controller -> orchestrator) rewords most rules at
        # once, and reprinting every one turned a session-start brief into 29 KB of
        # rules the reader had already read -- what KYO-531 set out to stop.
        compared_to = data.get("orchestrator_rules_compared_to")
        for rule in data["orchestrator_rules"]:
            kind = rule.get("enforcement") or rule["enforced_by"]
            change = rule.get("change")
            if not change:
                out.append(f"  - {rule['title']} ({kind})")
                continue
            if change != "changed":
                out.append(
                    f"  - {rule['title']} ({kind}) -- {change.upper()} since the previous "
                    f"brief ({compared_to}); in full:"
                )
                for line in (rule.get("text") or "").splitlines():
                    out.append(f"      {line}" if line.strip() else "")
                continue
            out.append(
                f"  - {rule['title']} ({kind}) -- CHANGED since the previous "
                f"brief ({compared_to})"
            )
            summary = rule.get("change_summary")
            what = (
                f"what changed: {summary}"
                if summary
                else "what changed is not recorded (the previous brief kept only a "
                "digest of this rule)"
            )
            out.append(f"      {what}; full text: `kyo rules \"{rule['title']}\"`")
        out.append(f"  `{WORKER_RULE_FULL_TEXT_COMMAND}` prints one rule in full.")
        if compared_to is None:
            out.append(
                "  (no earlier brief recorded these rules' text, so none is marked as "
                "changed; the next brief compares against this one)"
            )

    return "\n".join(out)
