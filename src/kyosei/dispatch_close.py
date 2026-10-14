"""``kyo dispatch --close`` — ending a dispatch without (or after) its report."""

from __future__ import annotations

from argparse import Namespace

from . import commands, export
from .clock import now
from .commands import Context, Output, _reason, _touch_record
from .contracts import (
    CONTRACT_DISPATCH_ALREADY_CLOSED,
    CONTRACT_DISPATCH_CLOSE_REQUIRES_REPORT,
    DISPATCH_STATE_REPORTED,
    DISPATCH_STATE_RUNNING,
    DISPATCH_STATE_TERMINATED_NO_REPORT,
    DISPATCH_STATE_WITHDRAWN,
    dispatch_close_reason_label,
)
from .errors import ContractViolation, KyoseiError, UsageError
from .render import echo_block, echo_value


def cmd_dispatch_close(ctx: Context, args: Namespace) -> Output:
    """``kyo dispatch close <dispatch-id> [--terminated-no-report|--withdrawn --why ...]``.

    KYO-7.c5/law 8: refuses (exit 2) to close a dispatch with no ingested report
    unless the caller explicitly names one of the two loud, distinct states that
    mean there is no report. A dispatch never renders as anything that reads like
    "done" without a report — ``state`` is always one of ``running`` / ``reported``
    / ``terminated-no-report`` / ``withdrawn``; ``closed_at`` is a separate,
    orthogonal marker, never a fifth pseudo-state.

    KYO-345 is the second: ``--terminated-no-report`` means the work STOPPED (a worker
    was spawned and died, and the session brief flags every one); ``--withdrawn`` is the
    exit for a dispatch that was never handed to anybody — the same refusals, the same
    required reason, and a state that is terminal and quiet rather than terminal and
    loud (docs/design/dispatch.md#kyo-345).
    """
    store = ctx.store()
    dispatch_id = args.dispatch_id
    if not dispatch_id:
        raise UsageError("kyo dispatch close needs a dispatch id: `kyo dispatch close <id>`")
    # The parser makes these two mutually exclusive (cli.build_parser), so at most one
    # is ever set here and there is no pair to refuse a second time.
    requested_state = _requested_close_state(args)
    reason = _reason(args)

    with store.transaction():
        dispatch = store.dispatch(dispatch_id)
        record_id = dispatch["record_id"]
        before = {record_id: export.record_document(store, record_id)}

        if dispatch["closed_at"]:
            raise ContractViolation(
                CONTRACT_DISPATCH_ALREADY_CLOSED,
                [f"{dispatch_id} was already closed at {dispatch['closed_at']}"],
            )

        state = dispatch["state"]
        if state == DISPATCH_STATE_RUNNING:
            if requested_state is None:
                # KYO-68: name BOTH remedies and what separates them, not just the one
                # that existed first. A message that offers only --terminated-no-report
                # is individually correct and steers every controller who mis-froze a
                # brief into recording a death that did not happen — which is the defect
                # KYO-345 was filed for, arriving through the error message.
                raise ContractViolation(
                    CONTRACT_DISPATCH_CLOSE_REQUIRES_REPORT,
                    [
                        f"{dispatch_id} has no ingested report; pass --terminated-no-report "
                        "--why '...' if a worker was spawned and the work stopped, or "
                        "--withdrawn --why '...' if this dispatch was never handed to one "
                        "(law 8: terminated is not done — and a dispatch nobody ran is not "
                        "a dispatch that died)"
                    ],
                )
            if not (reason or "").strip():
                raise ContractViolation(
                    CONTRACT_DISPATCH_CLOSE_REQUIRES_REPORT,
                    [f"{dispatch_id} entering {requested_state} needs --why (or --why-file)"],
                )
            new_state = requested_state
            terminated_why = reason
        elif state == DISPATCH_STATE_REPORTED:
            if requested_state is not None:
                raise KyoseiError(
                    f"{dispatch_id} already has an ingested report; "
                    f"{CLOSE_STATE_FLAGS[requested_state]} is only for closing a dispatch "
                    "that never got one"
                )
            new_state = state
            terminated_why = dispatch["terminated_why"]
        else:  # already terminated-no-report or withdrawn; closing again records closed_at
            new_state = state
            terminated_why = dispatch["terminated_why"]

        stamp = now()
        store.conn.execute(
            "UPDATE dispatches SET state = ?, terminated_why = ?, closed_at = ?, updated_at = ?"
            " WHERE id = ?",
            (new_state, terminated_why, stamp, stamp, dispatch_id),
        )
        _touch_record(store, record_id)
        after, events = commands._commit_and_export(
            store,
            command=f"dispatch close {dispatch_id}",
            actor=ctx.actor,
            record_ids=[record_id],
            before=before,
        )
    commands._finish_mutation(store, [record_id], events)

    lines = [f"closed {dispatch_id}", echo_value("state", new_state)]
    if terminated_why:
        # KYO-345.c2: the word is derived from the state, never typed here — a
        # withdrawn dispatch echoing "terminated because" would be this command
        # printing the very confusion the state exists to end, in the same breath
        # as the state that ends it.
        lines += echo_block(dispatch_close_reason_label(new_state), terminated_why)
    return Output(
        lines,
        {
            "ok": True,
            "command": "dispatch close",
            "dispatch_id": dispatch_id,
            "record_id": record_id,
            "state": new_state,
            "record": after[record_id],
        },
    )


#: KYO-345: which report-less terminal state each `kyo dispatch close` flag asks
#: for, and — read the other way round — which flag a refusal has to spell when it
#: talks about a state. Named once so the flag, the state and the word in the message
#: are one mapping rather than three retyped agreements (law 3); keyed by the contract
#: constants so a fifth state that reaches close without a flag is a ``KeyError`` here
#: rather than a message naming a flag that does not exist.
CLOSE_STATE_FLAGS: dict[str, str] = {
    DISPATCH_STATE_TERMINATED_NO_REPORT: "--terminated-no-report",
    DISPATCH_STATE_WITHDRAWN: "--withdrawn",
}

#: The argparse destination each of those flags lands on, in the order a refusal
#: should offer them: the death first, because it is the older and commoner case.
_CLOSE_STATE_FLAG_ATTRS: tuple[tuple[str, str], ...] = (
    ("terminated_no_report", DISPATCH_STATE_TERMINATED_NO_REPORT),
    ("withdrawn", DISPATCH_STATE_WITHDRAWN),
)


def _requested_close_state(args: Namespace) -> str | None:
    """Which report-less state this invocation asked for, or ``None`` (KYO-345).

    ``None`` is a first-class answer, not a default: it means the caller named
    neither state, which is exactly the refusal law 8 requires on a dispatch with no
    ingested report. The parser makes the two flags mutually exclusive, so this never
    has to choose between them.
    """
    for attr, state in _CLOSE_STATE_FLAG_ATTRS:
        if getattr(args, attr, False):
            return state
    return None


#: KYO-133: the flags that mean something only on `kyo dispatch close
#: <dispatch-id>`, each documented "with 'close'" already. Passing any of them
#: beside a record id is refused before the brief freezes: accepting it silently would
#: dispatch a brand-new record when the controller meant to END one. Named once so the
#: check below and any future reader see the same attribute/flag names (law 3). Every
#: close-only flag must be listed here, or a mistyped closure with it freezes a second
#: brief (docs/design/dispatch.md#kyo-133).
_CLOSE_ONLY_FLAGS: tuple[tuple[str, str], ...] = (
    ("terminated_no_report", "--terminated-no-report"),
    ("withdrawn", "--withdrawn"),
    ("why", "--why"),
    ("why_file", "--why-file"),
)


def _refuse_close_only_flags(ctx: Context, args: Namespace) -> None:
    """KYO-133.c1/c2/c3: refuse *before* anything is frozen, not after.

    Cheapest fix available, per the record: the flags in :data:`_CLOSE_ONLY_FLAGS`
    are meaningful only with ``close``, so passing any of them with a record id is a
    usage error naming the correct form, rather than a silent no-op that dispatches
    the wrong thing.

    c3: if the record already has a running dispatch, name its id — the whole
    reason a controller reaches for one of these flags is to end a dispatch whose
    id is a lookup away, and the refusal can already see it from here.
    """
    offending = [flag for attr, flag in _CLOSE_ONLY_FLAGS if getattr(args, attr, None)]
    if not offending:
        return
    record_id = args.target
    store = ctx.store()
    running = [
        row["id"] for row in store.dispatches(record_id) if row["state"] == DISPATCH_STATE_RUNNING
    ]
    verb = "is" if len(offending) == 1 else "are"
    # KYO-83: the whole message lives in this one raise, in one f-string, rather than
    # built up across statements and handed to `raise` as a local variable —
    # `tests/test_kyo5_error_remedies.py` (in Kyosei's own repository) reads a site's
    # message by walking the AST of the `raise` itself, and a local variable is invisible
    # to that walk the same way a module-level constant or function is not.
    raise UsageError(
        f"{', '.join(offending)} {verb} meaningful only with `kyo dispatch close "
        f"<dispatch-id>`, not with a record id ({record_id!r}); nothing was dispatched."
        + (
            f" {record_id} already has a running dispatch: {', '.join(running)} — that is "
            "probably the one you meant to close."
            if running
            else ""
        )
    )
