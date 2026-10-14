"""``kyo confirm`` — "the maintainer looked and approves", as one act (KYO-494).

The design is KYO-481's. Closing a human gate was three commands every time:
``kyo verify`` a criterion
``human-observed`` with the maintainer's words, ``kyo ungate``, ``kyo move`` (46 human-gate
clearings measured across two stores; 39 followed by a move within fifteen minutes).
Approving a proposal is the same act with no gate in it.

**A composition, the way** :mod:`kyosei.cmd_accept` **is.** Every write goes through the
handler the typed command uses — ``kyo verify``, ``kyo attach``, ``kyo ungate``, ``kyo
move`` — reached through the shipped parser by :func:`cmd_accept._run`, with the argv
printed beside the step, and any refusal is that command's own words. The ladder walk is
:func:`cmd_accept._close`, whose pre-check is :func:`cmd_accept._close_blocker` — which
reads KYO-490's ``settled-requires-no-open-gates`` contract, so "a remaining open gate of
any type stops the completion move" is that contract and not a second copy of it.
Not one transaction, for accept's reason (``BEGIN IMMEDIATE`` does not nest); a second
run is safe instead: a target whose latest verification is ``met``,
``human-observed`` and carries evidence with this quote's sha256 is skipped as already
applied, a cleared gate is out of scope, and a move onto the current status is a no-op.

**Everything is resolved before anything is written.** Which gate the words answer,
which criteria they verify, and every refusal — ambiguity, a retired record, a running
dispatch, a criterion or gate that cannot be confirmed — is decided first; a refusal
stores nothing, exits non-zero under its own contract name, and prints the ready ``kyo
confirm`` line(s) that would resolve it.

**The words go in evidence, verbatim, read once.** The quote is read a single time
(so ``--quote-file -`` works although several steps need it), and each composed step is
handed a temporary file holding exactly those bytes. Nothing is prefixed: a frame the
tool composed would be the tool's words stored as evidence. The gate's clearing reason is
a one-line pointer to that evidence and never the words, because ``gates.cleared_why`` is
a place ``kyo redact`` can only name, not rewrite.

**Approval only**: the verdict is ``met`` and the method ``human-observed``, and
there are no flags to change either. A "no" is ``kyo verify --verdict unmet-unbuilt``.

**A completion regenerates the declared documents** (KYO-577). A record confirmed to
completion outside a wave left every generated document behind the store, so the next
``make test`` failed on the committed changelog and ``kyo close-wave`` refused before its
own regeneration could run. So when this run moved the record to
completion, and only then, it runs the project's ``close_wave: generate:`` lines — in
order, through the shell, from the code root, the same lines ``kyo close-wave`` runs —
and commits their output with the store's export, as close-wave's own commit does. A
project that declares no generate steps runs nothing and commits nothing, exactly as
before. The steps run only when the tree outside ``kyosei/`` and ``ideas/`` is clean;
otherwise, or when a step fails (exit non-zero) or the commit does, the confirmation
stays applied, nothing is committed, and the output is ``INCOMPLETE`` and names the line
that finishes the job — ``kyo close-wave <record>``, which for a record already at
completion moves nothing and needs no suite pass. The exit status stays 0, as for every
other incompleteness here (KYO-415.c3).
"""

from __future__ import annotations

import hashlib
import re
import shlex
import tempfile
from argparse import Namespace
from pathlib import Path

from . import cmd_close_wave, cmd_evidence, cmd_links
from .cmd_accept import _close, _incomplete_line, _incompleteness, _run, _Step
from .commands import Context, Output, _load_status_ladder
from .contracts import ROLE_RETIRED, SETTLED_CRITERION_STATES, VOID_CRITERION_STATES
from .errors import ContractViolation, KyoseiError, NotFound, UsageError
from .prose import STDIN, check_not_shell_mangled, read_prose, single_line

#: The only gates a confirmation answers. ``awaiting:event`` waits for something to
#: happen and ``holding-for`` for a premise to resolve; neither waits for a person to
#: look, and clearing ``holding-for`` raises ``gate-cleared-stale-criteria`` (KYO-59),
#: which needs a deliberate ``kyo ungate``, not the side effect of an approval.
HUMAN_GATE_TYPES: frozenset[str] = frozenset({"awaiting:human-review", "awaiting:human-run"})

#: The one verdict and the one method a confirmation records.
CONFIRM_VERDICT = "met"
CONFIRM_METHOD = "human-observed"

#: KYO-577: the refresh outcomes that leave this confirmation incomplete.
_REFRESH_NOT_DONE = frozenset({cmd_close_wave.REFRESH_SKIPPED, cmd_close_wave.REFRESH_FAILED})

CONTRACT_CONFIRM_AMBIGUOUS_GATE = "confirm-ambiguous-gate"
CONTRACT_CONFIRM_AMBIGUOUS_CRITERIA = "confirm-ambiguous-criteria"
CONTRACT_CONFIRM_NOTHING_TO_CONFIRM = "confirm-nothing-to-confirm"
CONTRACT_CONFIRM_RECORD_RETIRED = "confirm-record-retired"
CONTRACT_CONFIRM_DISPATCH_RUNNING = "confirm-dispatch-running"
CONTRACT_CONFIRM_CRITERION_NOT_CONFIRMABLE = "confirm-criterion-not-confirmable"
CONTRACT_CONFIRM_GATE_NOT_CONFIRMABLE = "confirm-gate-not-confirmable"
CONTRACT_CONFIRM_GATE_NAMES_A_MISSING_CRITERION = "confirm-gate-names-a-missing-criterion"

#: A criterion id in full form (``KYO-449.c4``). Short forms (``c3``) are not
#: read — 3 of 63 human gates measured used one, and a bare ``c3`` also matches prose.
_FULL_CRITERION_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,15}-[1-9][0-9]*)\.c([1-9][0-9]*)\b")

#: The rule that chose the targets, as ``--json``'s ``rule`` carries it.
RULE_CRITERION_FLAG = "criterion-flag"
RULE_GATE_PAYLOAD = "gate-payload"
RULE_SOLE_UNOBSERVED = "sole-unobserved"
RULE_NO_TARGET = "no-target"


# ------------------------------------------------------------------------ the quote


class _Quote:
    """The maintainer's words, read once, and how to name them again in a remedy line."""

    def __init__(self, args: Namespace) -> None:
        quote = getattr(args, "quote", None)
        quote_file = getattr(args, "quote_file", None)
        if quote is not None and quote_file is not None:
            raise UsageError("pass --quote or --quote-file, not both")
        if quote is None and quote_file is None:
            raise UsageError(
                "kyo confirm needs the maintainer's words: --quote TEXT for one line, or "
                "--quote-file FILE ('-' for stdin) for anything longer"
            )
        if quote_file is not None:
            self.text = read_prose(quote_file, what="--quote-file")
            self.flag = ["--quote-file", quote_file]
            self.source = "stdin" if quote_file == STDIN else quote_file
        else:
            # cli.check_prose_arguments already refused residue before the store opened;
            # repeated for a caller that hands this handler a Namespace of its own.
            check_not_shell_mangled(quote, what="--quote")
            # The same single-line rule --why, --do and --title are held to, in its words.
            self.text = single_line(quote, what="--quote") if quote.strip() else ""
            self.flag = ["--quote", quote]
            self.source = "--quote"
        if not self.text.strip():
            raise UsageError(
                "the quote is empty: kyo confirm stores the maintainer's words as "
                "evidence, and there are none to store"
            )
        self.sha256 = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        self.chars = len(self.text)


def _line(record_id: str, quote: _Quote, *extra: str) -> str:
    """A ready ``kyo confirm`` line naming the same words the refused run was given."""
    return "kyo " + shlex.join(["confirm", record_id, *extra, *quote.flag])


def _criterion_flag(ids: list[str]) -> list[str]:
    return ["--criterion", *ids] if ids else []


# ----------------------------------------------------------------------- resolution


def _human_gates(store, record_id: str) -> list:
    return [
        gate
        for gate in store.gates(record_id)
        if not gate["cleared_at"] and gate["gate_type"] in HUMAN_GATE_TYPES
    ]


def _named_ids(detail: str | None) -> list[str]:
    """Every full-form criterion id in a gate's payload, in order, once each."""
    seen: list[str] = []
    for match in _FULL_CRITERION_RE.finditer(detail or ""):
        cid = f"{match.group(1)}.c{match.group(2)}"
        if cid not in seen:
            seen.append(cid)
    return seen


def _already_applied(store, criterion_id: str, quote: _Quote) -> bool:
    """This criterion's latest verification is this quote's ``met``, ``human-observed``."""
    latest = store.latest_verification(criterion_id)
    if latest is None or latest["verdict"] != CONFIRM_VERDICT:
        return False
    if latest["method"] != CONFIRM_METHOD or not latest["evidence_id"]:
        return False
    return store.evidence_artifact(latest["evidence_id"])["sha256"] == quote.sha256


def _holds_quote(store, record_id: str, quote: _Quote) -> list[str]:
    """The evidence ids on ``record_id`` carrying this quote's sha256."""
    return [
        row["id"] for row in store.evidence_artifacts(record_id) if row["sha256"] == quote.sha256
    ]


def _refuse_retired(store, record_id: str, status: str, ladder) -> None:
    if ladder.role(status) != ROLE_RETIRED:
        return
    gates = [gate for gate in store.gates(record_id) if not gate["cleared_at"]]
    raise ContractViolation(
        CONTRACT_CONFIRM_RECORD_RETIRED,
        [
            f"{record_id} is {status} (retired): confirming it would record approval of "
            "work that was retired, so nothing was written",
            *(
                f"clear its open gate on its own: `kyo ungate {gate['id']} --why-file FILE`"
                for gate in gates
            ),
        ],
    )


def _refuse_running_dispatch(store, record_id: str, quote: _Quote, args: Namespace) -> None:
    """The clause :func:`cmd_next.ungated_unobserved` uses: while a worker is changing the
    thing, a look at it is a look at a build that is about to change."""
    from .commands import dispatch_covers

    running = [
        d for d in store.running_dispatches() if record_id in (d["record_id"], *dispatch_covers(d))
    ]
    if not running:
        return
    raise ContractViolation(
        CONTRACT_CONFIRM_DISPATCH_RUNNING,
        [
            *(
                f"dispatch {d['id']} is running on {d['record_id']}"
                + (f" and covers {record_id}" if d["record_id"] != record_id else "")
                for d in running
            ),
            "a look taken while a worker is changing the thing is a look at a build about "
            "to change, so nothing was written. Once the dispatch is no longer running "
            "(`kyo report <dispatch-id> --file FILE` ingests its report), run the same line "
            "again:",
            f"  {_rerun_line(record_id, quote, args)}",
        ],
    )


def _rerun_line(record_id: str, quote: _Quote, args: Namespace) -> str:
    extra: list[str] = list(_criterion_flag(list(args.criterion or [])))
    if args.gate:
        extra += ["--gate", args.gate]
    if args.no_close:
        extra.append("--no-close")
    return _line(record_id, quote, *extra)


def _scope(store, record_id: str, quote: _Quote, args: Namespace) -> list:
    """The in-scope human gates: zero or one, or a refusal."""
    human = _human_gates(store, record_id)
    if args.gate:
        found = next((g for g in store.gates(record_id) if g["id"] == args.gate), None)
        why: str | None = None
        if found is None:
            try:
                other = store.gate(args.gate)
            except NotFound:
                why = f"--gate {args.gate}: no such gate"
            else:
                why = f"--gate {args.gate} is on {other['record_id']}, not {record_id}"
        elif found["cleared_at"]:
            why = f"--gate {args.gate} was already cleared at {found['cleared_at']}"
        elif found["gate_type"] not in HUMAN_GATE_TYPES:
            why = (
                f"--gate {args.gate} is {found['gate_type']}, which a confirmation never "
                f"clears (only {', '.join(sorted(HUMAN_GATE_TYPES))}); clear it on its own "
                f"with `kyo ungate {args.gate} --why-file FILE`"
            )
        if why is None:
            return [found]
        criteria = _criterion_flag(list(args.criterion or []))
        remedies = [_line(record_id, quote, *criteria, "--gate", gate["id"]) for gate in human] or [
            _line(record_id, quote, *criteria)
        ]
        raise ContractViolation(
            CONTRACT_CONFIRM_GATE_NOT_CONFIRMABLE,
            [
                why,
                f"nothing was written. {record_id}'s open human gates: "
                + (", ".join(f"{g['id']} ({g['gate_type']})" for g in human) or "(none)")
                + ". Run one of:",
                *(f"  {line}" for line in remedies),
            ],
        )
    if len(human) > 1:
        criteria = _criterion_flag(list(args.criterion or []))
        raise ContractViolation(
            CONTRACT_CONFIRM_AMBIGUOUS_GATE,
            [
                f"{record_id} has {len(human)} open human gates, and the words answer one "
                "instruction — only a person knows which. Nothing was written:",
                *(
                    f"  {gate['id']} ({gate['gate_type']}): "
                    + (gate["detail"] or "(no payload)").splitlines()[0]
                    for gate in human
                ),
                "run the line for the gate the words answer:",
                *(
                    f"  {_line(record_id, quote, *criteria, '--gate', gate['id'])}"
                    for gate in human
                ),
            ],
        )
    return human


def _flag_targets(store, record_id: str, quote: _Quote, args: Namespace, by_id: dict) -> list:
    """Exactly the named criteria, each this record's own and not void."""
    named: list[str] = []
    for cid in args.criterion:
        if cid not in named:
            named.append(cid)
    problems: list[str] = []
    elsewhere: dict[str, list[str]] = {}
    for cid in named:
        row = by_id.get(cid)
        if row is None:
            try:
                other = store.criterion(cid)
            except NotFound:
                problems.append(f"--criterion {cid}: no such criterion")
            else:
                problems.append(f"--criterion {cid} is {other['record_id']}'s, not {record_id}'s")
                elsewhere.setdefault(other["record_id"], []).append(cid)
        elif row["state"] in VOID_CRITERION_STATES:
            problems.append(
                f"--criterion {cid} is {row['state']}: a void criterion directs nobody, "
                "and there is nothing to confirm on it"
            )
    if not problems:
        return [by_id[cid] for cid in named]
    good = [
        cid for cid in named if cid in by_id and by_id[cid]["state"] not in VOID_CRITERION_STATES
    ]
    gate = ["--gate", args.gate] if args.gate else []
    remedies = [_line(record_id, quote, *_criterion_flag(good), *gate)]
    remedies += [_line(other, quote, *_criterion_flag(ids)) for other, ids in elsewhere.items()]
    raise ContractViolation(
        CONTRACT_CONFIRM_CRITERION_NOT_CONFIRMABLE,
        [
            *problems,
            "nothing was written. Without "
            + ("those" if len(problems) > 1 else "it")
            + ", or on the record that carries it:",
            *(f"  {line}" for line in remedies),
        ],
    )


def _resolve(store, record_id: str, quote: _Quote, args: Namespace, scope: list) -> dict:
    """The targets, by the first rule that applies. Writes nothing."""
    criteria = list(store.criteria(record_id))
    by_id = {row["id"]: row for row in criteria}
    resolved: dict = {"ignored": [], "named": []}
    gate = scope[0] if scope else None

    if args.criterion:
        resolved["rule"] = RULE_CRITERION_FLAG
        resolved["targets"] = _flag_targets(store, record_id, quote, args, by_id)
        if gate is not None:
            # What decides whether the gate is answered. An id the record does not carry
            # cannot hold it: naming the criteria is the statement of what was looked at.
            resolved["named"] = [cid for cid in _named_ids(gate["detail"]) if cid in by_id]
        return resolved

    if gate is not None:
        named = _named_ids(gate["detail"])
        own = [cid for cid in named if cid.rsplit(".", 1)[0] == record_id]
        resolved["ignored"] = [cid for cid in named if cid not in own]
        if own:
            missing = [cid for cid in own if cid not in by_id]
            if missing:
                carried = [
                    row["id"] for row in criteria if row["state"] not in SETTLED_CRITERION_STATES
                ]
                raise ContractViolation(
                    CONTRACT_CONFIRM_GATE_NAMES_A_MISSING_CRITERION,
                    [
                        f"{gate['id']}'s payload names {', '.join(missing)}, which "
                        f"{record_id} does not carry — nothing was written",
                        f"correct the payload with `kyo redo {gate['id']} --do-file FILE`, "
                        "or say which criteria the words answer:",
                        "  "
                        + _line(record_id, quote, *_criterion_flag(carried), "--gate", gate["id"]),
                    ],
                )
            resolved["rule"] = RULE_GATE_PAYLOAD
            resolved["named"] = own
            resolved["targets"] = [
                by_id[cid] for cid in own if by_id[cid]["state"] not in SETTLED_CRITERION_STATES
            ]
            return resolved

    unobserved = [row for row in criteria if row["state"] == "unmet-unobserved"]
    if len(unobserved) == 1:
        resolved["rule"] = RULE_SOLE_UNOBSERVED
        resolved["targets"] = unobserved
        return resolved
    if len(unobserved) > 1:
        ids = [row["id"] for row in unobserved]
        gate_flag = ["--gate", gate["id"]] if gate is not None else []
        raise ContractViolation(
            CONTRACT_CONFIRM_AMBIGUOUS_CRITERIA,
            [
                f"{record_id} has {len(ids)} unmet-unobserved criteria and "
                + (
                    f"{gate['id']} names none of them"
                    if gate is not None
                    else "no open human gate names any"
                )
                + ": one approval applied to observations nobody named is a rubber stamp. "
                "Nothing was written. Name the criteria the words answer:",
                *(f"  {_line(record_id, quote, '--criterion', cid, *gate_flag)}" for cid in ids),
                f"  {_line(record_id, quote, *_criterion_flag(ids), *gate_flag)}",
            ],
        )
    resolved["rule"] = RULE_NO_TARGET
    resolved["targets"] = []
    if gate is None and not _holds_quote(store, record_id, quote):
        unsettled = [row["id"] for row in criteria if row["state"] not in SETTLED_CRITERION_STATES]
        remedies = [_line(record_id, quote, "--criterion", cid) for cid in unsettled]
        raise ContractViolation(
            CONTRACT_CONFIRM_NOTHING_TO_CONFIRM,
            [
                f"{record_id} has no open human gate and no unmet-unobserved criterion, so "
                "the words would land nowhere — nothing was written",
                *(
                    [
                        "name the criterion the words answer (an unmet-unbuilt one is allowed "
                        "when named):",
                        *(f"  {line}" for line in remedies),
                    ]
                    if remedies
                    else [
                        "every criterion is settled; to keep the words as record evidence, "
                        f"`kyo attach {record_id} --file FILE --supports {record_id}`"
                    ]
                ),
            ],
        )
    return resolved


# ---------------------------------------------------------------------- the command


def _composed(ctx: Context, argv: list[str], display: list[str], handler) -> _Step:
    """One composed step, run with the real temp-file argv and printed with ``display``."""
    step = _run(ctx, argv, handler, tolerate=(KyoseiError,))
    step.argv = display
    return step


def _settled_now(store, criterion_id: str) -> bool:
    return store.criterion(criterion_id)["state"] in SETTLED_CRITERION_STATES


def _pointer(
    store, record_id: str, quote: _Quote, verified: list[str], attached: str | None
) -> str:
    """The gate's clearing reason — a pointer to the evidence, never the words."""
    if verified:
        evidence = [store.latest_verification(cid)["evidence_id"] for cid in verified]
        return (
            f"confirmed by kyo confirm: {', '.join(verified)} {CONFIRM_VERDICT} "
            f"({CONFIRM_METHOD}), evidence {', '.join(e for e in evidence if e)}, "
            f"sha256:{quote.sha256}"
        )
    if attached:
        return (
            f"confirmed by kyo confirm: no criterion to verify, evidence {attached} "
            f"attached to {record_id}, sha256:{quote.sha256}"
        )
    return (
        f"confirmed by kyo confirm: the criteria it names were already settled, "
        f"sha256:{quote.sha256}"
    )


def cmd_confirm(ctx: Context, args: Namespace) -> Output:
    """``kyo confirm <record> (--quote TEXT | --quote-file FILE) [--criterion ...]
    [--gate GID] [--no-close]`` — KYO-494.

    1. Resolve, writing nothing: retired record, running dispatch, the in-scope
       human gate, the targets — each refusal its own contract, with ready lines.
    2. ``kyo verify <cid> --verdict met --method human-observed --evidence-file <words>``
       per target not already applied; or, with no target and a gate in scope, ``kyo
       attach <record> --file <words> --supports <record>``.
    3. ``kyo ungate <gate> --why-file <pointer>`` once the gate is answered.
    4. Unless ``--no-close``: :func:`cmd_accept._close` over this record.
    5. When step 4 moved the record to completion and the project declares
       ``close_wave: generate:``, those lines run and their output is committed with
       the store: :func:`cmd_close_wave.refresh_after_completion` (KYO-577).
    """
    quote = _Quote(args)
    store = ctx.store()
    record_id = args.record_id
    row = store.record(record_id)
    ladder = _load_status_ladder(store)
    _refuse_retired(store, record_id, row["status"], ladder)
    _refuse_running_dispatch(store, record_id, quote, args)
    scope = _scope(store, record_id, quote, args)
    resolved = _resolve(store, record_id, quote, args, scope)
    gate = scope[0] if scope else None

    targets: list[dict] = []
    skipped: list[dict] = []
    for criterion in resolved["targets"]:
        cid = criterion["id"]
        if _already_applied(store, cid, quote):
            skipped.append({"criterion_id": cid, "reason": "already applied: this quote's met"})
        elif criterion["state"] == CONFIRM_VERDICT:
            skipped.append({"criterion_id": cid, "reason": "already met"})
        else:
            targets.append({"criterion_id": cid, "prior_state": criterion["state"]})

    steps: list[_Step] = []
    verified: list[str] = []
    attached: str | None = None
    with tempfile.TemporaryDirectory(prefix="kyo-confirm-") as scratch:
        words = Path(scratch) / "quote.txt"
        words.write_text(quote.text, encoding="utf-8", newline="")
        label = f"<{quote.source}>"
        for target in targets:
            cid = target["criterion_id"]
            tail = ["--verdict", CONFIRM_VERDICT, "--method", CONFIRM_METHOD, "--evidence-file"]
            step = _composed(
                ctx,
                ["verify", cid, *tail, str(words)],
                ["verify", cid, *tail, label],
                cmd_evidence.cmd_verify,
            )
            steps.append(step)
            if step.refused is None:
                verified.append(cid)
        if not resolved["targets"] and gate is not None:
            held = [
                e
                for e in _holds_quote(store, record_id, quote)
                if store.evidence_artifact(e)["supports_id"] == record_id
            ]
            if held:
                attached = held[0]
                skipped.append({"evidence_id": attached, "reason": "already attached"})
            else:
                tail = ["--supports", record_id]
                step = _composed(
                    ctx,
                    ["attach", record_id, "--file", str(words), *tail],
                    ["attach", record_id, "--file", label, *tail],
                    cmd_evidence.cmd_attach,
                )
                steps.append(step)
                if step.refused is None:
                    attached = (step.data or {}).get("evidence_id")

        gates_cleared: list[dict] = []
        gates_left: list[dict] = []
        if gate is not None:
            named = resolved["named"]
            holding = [cid for cid in named if not _settled_now(store, cid)]
            refused = [step for step in steps if step.refused]
            if holding or refused:
                gates_left.append(
                    {
                        "gate_id": gate["id"],
                        "gate_type": gate["gate_type"],
                        "holding": holding,
                        "why": (
                            f"still waiting on {', '.join(holding)}"
                            if holding
                            else "a composed step was refused"
                        ),
                    }
                )
            else:
                pointer = Path(scratch) / "pointer.txt"
                pointer.write_text(
                    _pointer(store, record_id, quote, verified, attached),
                    encoding="utf-8",
                    newline="",
                )
                step = _composed(
                    ctx,
                    ["ungate", gate["id"], "--why-file", str(pointer)],
                    ["ungate", gate["id"], "--why-file", "<pointer>"],
                    cmd_links.cmd_ungate,
                )
                steps.append(step)
                if step.refused is None:
                    gates_cleared.append({"gate_id": gate["id"], "gate_type": gate["gate_type"]})
                else:
                    gates_left.append(
                        {
                            "gate_id": gate["id"],
                            "gate_type": gate["gate_type"],
                            "holding": [],
                            "why": "kyo ungate refused",
                        }
                    )

    close = not args.no_close
    closed: list[dict] = []
    left: list[dict] = []
    if close:
        close_steps, closed, left = _close(ctx, store, [record_id])
        steps += close_steps
    # 5. KYO-577: a completion outside a wave regenerates the declared documents itself.
    refresh = (
        cmd_close_wave.refresh_after_completion(ctx, [record_id], by="kyo confirm")
        if closed
        else None
    )
    refresh_failed = refresh is not None and refresh["status"] in _REFRESH_NOT_DONE
    refused_steps, still_open = _incompleteness(store, steps, left, close=close)
    complete = not refused_steps and not still_open and not gates_left and not refresh_failed

    lines = [
        f"confirmed {record_id} as {ctx.actor}, with the words in {quote.source} "
        f"({quote.chars} chars, sha256:{quote.sha256})"
    ]
    if not complete:
        lines.append(
            _incomplete_line(
                refused_steps,
                still_open,
                also=(
                    [
                        f"{len(gates_left)} in-scope gate(s) left open ("
                        + ", ".join(f"{g['gate_id']}: {g['why']}" for g in gates_left)
                        + ")"
                    ]
                    if gates_left
                    else []
                )
                + (
                    [f"the generated documents were not refreshed ({refresh['status']})"]
                    if refresh_failed
                    else []
                ),
            )
        )
    lines.append(_resolution_line(record_id, resolved, gate))
    for cid in resolved["ignored"]:
        lines.append(f"ignored: {cid} in {gate['id']}'s payload is not {record_id}'s")
    for target in targets:
        lines.append(f"target: {target['criterion_id']} (was {target['prior_state']})")
    for item in skipped:
        lines.append(
            f"skipped: {item.get('criterion_id') or item.get('evidence_id')} — {item['reason']}"
        )
    if len(verified) > 1:
        lines.append(
            f"the same {quote.chars} chars (sha256:{quote.sha256[:12]}…) stored once per "
            "criterion, as "
            + ", ".join(store.latest_verification(c)["evidence_id"] for c in verified)
        )
    for n, step in enumerate(steps, start=1):
        suffix = "  — REFUSED" if step.refused else ""
        lines.append(f"[{n}/{len(steps)}] {step.command}" + suffix)
        lines += [f"  {line}" for line in step.lines]
    for item in gates_left:
        lines.append(f"left open: {item['gate_id']} ({item['gate_type']}) — {item['why']}")
    if close:
        for row_ in closed:
            lines.append(f"{record_id}: {row_['was']} -> {' -> '.join(row_['walked'])}")
        for row_ in left:
            lines.append(f"{record_id} stays {row_['status']}:")
            lines += [f"    {line.strip()}" for line in row_["why"]]
    else:
        lines.append(f"--no-close: {record_id} is left at {store.record(record_id)['status']}")
    if refresh is not None:
        lines += refresh["lines"]

    return Output(
        lines,
        {
            "ok": True,
            "command": "confirm",
            "complete": complete,
            "record_id": record_id,
            "quote": {"chars": quote.chars, "sha256": quote.sha256},
            "rule": resolved["rule"],
            "ignored": resolved["ignored"],
            "targets": targets,
            "skipped": skipped,
            "gates_cleared": gates_cleared,
            "gates_left": gates_left,
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
            "closed": closed,
            "left_open": left,
            "refresh": (
                {key: value for key, value in refresh.items() if key != "lines"}
                if refresh is not None
                else None
            ),
        },
    )


def _resolution_line(record_id: str, resolved: dict, gate) -> str:
    gate_part = f"{gate['id']} ({gate['gate_type']})" if gate is not None else "no open human gate"
    rule = resolved["rule"]
    count = len(resolved["targets"])
    if rule == RULE_CRITERION_FLAG:
        return f"resolved: --criterion names {count} criterion/criteria; gate in scope: {gate_part}"
    if rule == RULE_GATE_PAYLOAD:
        return (
            f"resolved: {gate_part} names {', '.join(resolved['named'])} -> {count} "
            "unsettled criterion/criteria to verify"
        )
    if rule == RULE_SOLE_UNOBSERVED:
        return (
            f"resolved: {record_id}'s sole unmet-unobserved criterion; gate in scope: {gate_part}"
        )
    return f"resolved: no criterion to verify; gate in scope: {gate_part}"
