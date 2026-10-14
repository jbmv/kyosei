"""Which dispatch a stopping subagent is: the hook payload and transcript readers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config as config_module
from .commands import Context

# ------------- KYO-128: which worktree is a `SubagentStop` hook actually about?
#
# A `SubagentStop` hook inherits the CONTROLLER session's cwd, never the worktree the
# stopping subagent was dispatched into, so `kyo check-clean` and `kyo check-branch` must be
# told which tree to audit. Neither the static hook template (one string for every
# dispatch) nor the store (it knows every running dispatch, not which one this stop
# belongs to) can say. The hook payload's `agent_id` can: the stopping subagent's own
# transcript opens with the prompt it was spawned with, and that names exactly one running
# dispatch. History: docs/design/dispatch.md#kyo-128.
#
# THE COUPLING THIS TAKES ON, STATED OUT LOUD because it is the only place in this
# codebase that reaches into the harness's private files: resolution reads
# `<session>/subagents/agent-<agent_id>.jsonl` under the directory Claude Code
# derives from `transcript_path`. That path is Claude Code's, not ours, and it can
# change under us without warning. Two things make that survivable and neither is
# optional. First, every step degrades to a NAMED outcome rather than a guess (see
# :class:`StopTarget`), so a layout change turns the guard off loudly instead of
# turning it into a confident answer about the wrong tree — which is the very
# defect being fixed here, and re-introducing it in the fix would be the whole
# failure. Second, what is matched is a token KYOSEI ITSELF minted: the dispatch
# id. We are not parsing the harness's prose, we are looking for our own id in it.
#
# Nothing Kyosei already owns can answer this (the payload's `cwd`, the transcript's
# `cwd`/`gitBranch`, the `.meta.json` sidecar and the store were each checked): the prompt
# text is the only carrier, and the transcript the only copy of it.

#: The stopping agent WAS matched to exactly one running dispatch's worktree.
STOP_TARGET_MATCHED = "matched"
#: No running dispatch has declared a worktree other than the project root, so the
#: hook's own cwd IS the worker's tree. This is the case the old behaviour got
#: right by accident; it is now right on purpose, and says so.
STOP_TARGET_NO_WORKTREES = "no-declared-worktree-in-play"
#: Declared worktrees ARE in play and this stop could not be matched to one. The
#: cwd is then known NOT to be the stopping worker's tree.
STOP_TARGET_UNRESOLVED = "unresolved"
#: KYO-154: the stopping agent is a HELPER spawned by a dispatched worker, not that
#: worker. It shares its parent's worktree, has none of its own, and the tree its
#: prompt would resolve to is the parent's — mid-work, and therefore dirty. This is
#: not "we could not tell": it is a positive identification, and it is a fourth
#: outcome rather than a silent pass because "not a worker" and "clean" are
#: different facts (KYO-154.c2).
STOP_TARGET_NOT_A_WORKER = "helper-of-a-dispatched-worker"
#: KYO-325: the STORE could not be read, so which dispatches declared a worktree is
#: not known and this stop cannot be placed against anything. Its own outcome because
#: the alternative, :data:`STOP_TARGET_NO_WORKTREES`, is a POSITIVE claim ("the project
#: root IS every dispatch's worktree here") that :attr:`StopTarget.resolved` treats as an
#: answer and :func:`record_stop_audit` writes no row for. Law 6: "read it and there was
#: nothing" and "could not read it" are different facts. See
#: docs/design/dispatch.md#kyo-325 for what collapsing them cost.
STOP_TARGET_STORE_UNREADABLE = "store-unreadable"

#: KYO-373: the stopping agent has NO transcript anywhere the resolution looks — no
#: ``subagents/agent-<id>.jsonl``, no spawn sidecar beside it, no sidechain row for it
#: in the session transcript — while the harness HAS written transcripts for the other
#: agents of the same session. An agent this controller dispatched always has one, so
#: this is KYO-154's positive "not a worker" on a second ground, and the guards treat it
#: the same way (exit 0), except that there is no tree to read, so NO directory is
#: checked. These are KYO-163's ticks; :func:`_untranscribed_agent` holds the narrowing
#: that answers KYO-163's refusal to classify them. See docs/design/dispatch.md#kyo-373.
STOP_TARGET_NO_TRANSCRIPT = "no-transcript-not-a-dispatched-worker"

#: KYO-373.c1: WHY a stop could not be placed, as a stored code rather than a prefix of
#: :attr:`StopTarget.why` — the grouping `kyo worktrees` prints reads this, and a count
#: recovered by parsing an English sentence breaks the next time the sentence is
#: reworded (KYO-284's lesson, one field along). Rows written before KYO-373 carry no
#: code and are read by :func:`_legacy_stop_reason` instead, which is the one place
#: that parses prose, and only prose this module wrote.
#: The agent's transcript was not found where the resolution looks.
STOP_REASON_NO_TRANSCRIPT = "no-transcript"
#: A prompt was read and it names none of the dispatches with a declared worktree.
STOP_REASON_PROMPT_NAMES_NONE = "prompt-names-none"
#: A prompt was read and it names several, with no generated-brief header choosing one.
STOP_REASON_PROMPT_NAMES_SEVERAL = "prompt-names-several"
#: Anything else that left a stop unplaced: no ``agent_id``, no ``transcript_path``, a
#: transcript with no readable first message, a brief header naming an absent dispatch.
STOP_REASON_OTHER = "other"
#: The order and the words `kyo worktrees` renders the reasons in.
STOP_REASON_LABELS = {
    STOP_REASON_NO_TRANSCRIPT: "no transcript",
    STOP_REASON_PROMPT_NAMES_NONE: "prompt names none",
    STOP_REASON_PROMPT_NAMES_SEVERAL: "prompt names several",
    STOP_REASON_OTHER: "other",
}

#: A first user message is the dispatch prompt. Nothing below needs the rest of a
#: transcript and some of them are hundreds of megabytes, so the read is capped.
_MAX_TRANSCRIPT_LINES = 4000


@dataclass
class StopTarget:
    """Which directory a ``SubagentStop`` guard should read, and how it knows.

    Law 6, and the reason this is a three-outcome type rather than a path plus a
    boolean: "matched", "there was nothing to match" and "there was something to
    match and we could not" are three different facts with three different correct
    responses, and collapsing the last two into "just check the cwd" is exactly how
    the original defect stayed invisible. :attr:`why` is rendered by every caller
    on every run, passing and failing alike — a verdict that does not name its
    subject is what KYO-128 is about.
    """

    directory: Path
    outcome: str
    why: str
    dispatch_id: str | None = None
    #: The running dispatches with a declared worktree that were considered.
    candidates: list[str] = field(default_factory=list)
    #: KYO-154: for :data:`STOP_TARGET_NOT_A_WORKER`, the dispatch whose WORKER
    #: spawned the agent that stopped. Kept apart from :attr:`dispatch_id`, which
    #: means "the dispatch this stop IS", because a helper's stop is about that
    #: dispatch without being that dispatch's own stop, and a consumer counting
    #: worker stops must not silently count helpers among them.
    helper_of: str | None = None
    #: KYO-284: the transcript file this resolution actually read the stopping
    #: agent's prompt out of, when it read one. Stored beside the resolution rather
    #: than only named inside :attr:`why`, because :func:`worker_tokens` has to
    #: OPEN it later — long after the hook payload that carried it is gone — and a
    #: path recovered by parsing an English sentence is a path that breaks the next
    #: time the sentence is reworded. ``None`` wherever no transcript was read,
    #: which is every fallback outcome and every row written before KYO-284.
    transcript: Path | None = None
    #: KYO-373.c1: one of the ``STOP_REASON_*`` codes on every outcome that did not
    #: place the stop (``unresolved`` and :data:`STOP_TARGET_NO_TRANSCRIPT`), and
    #: ``None`` on the ones that did. Stored with the row; read back by
    #: :attr:`StopAudit.reason` and grouped by :meth:`StopAuditLedger.by_dispatch`.
    reason: str | None = None

    @property
    def resolved(self) -> bool:
        """Is :attr:`directory` believed to be the stopping worker's own tree?"""
        return self.outcome in (STOP_TARGET_MATCHED, STOP_TARGET_NO_WORKTREES)

    def as_json(self) -> dict:
        return {
            "outcome": self.outcome,
            "resolved": self.resolved,
            "directory": str(self.directory),
            "why": self.why,
            "dispatch_id": self.dispatch_id,
            "candidates": list(self.candidates),
            "helper_of": self.helper_of,
            "transcript": None if self.transcript is None else str(self.transcript),
            "reason": self.reason,
        }


def _payload_of(raw: str) -> dict:
    """A hook payload, or an empty dict for anything unreadable.

    Same stance as :func:`kyosei.cmd_publish.command_in_pre_tool_use_payload`
    (KYO-60): a guard that guessed at an unfamiliar payload shape would act
    confidently on something it cannot read, and on a blocking hook that is the
    failure mode that matters. An unreadable payload is a named outcome below,
    never an exception.
    """
    try:
        payload = json.loads(raw) if raw and raw.strip() else {}
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _message_text(record: dict) -> str:
    """The text of one transcript record, whichever shape its content is in."""
    message = record.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        return "\n".join(parts)
    return ""


def _first_user_message(path: Path, agent_id: str | None = None) -> str | None:
    """The first user message in a transcript, optionally for one agent only."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index >= _MAX_TRANSCRIPT_LINES:
                    return None
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict) or record.get("type") != "user":
                    continue
                if agent_id is not None and record.get("agentId") != agent_id:
                    continue
                text = _message_text(record)
                if text:
                    return text
    except OSError:
        return None
    return None


def _dispatch_prompt(payload: dict, agent_id: str) -> tuple[str | None, str, Path | None]:
    """The stopping subagent's spawn prompt, how it was found (or not), and where.

    The second element is returned on failure too, and is the whole point: "no
    transcript for agent X under Y" is an answer a controller can act on, where a
    bare ``None`` would become a silent fallback.

    KYO-284 adds the third: the transcript FILE the prompt was read out of, as a
    path rather than as a sentence. The sentence has always named it — `why` in
    every stored `matched` row reads "first user message of <path>" — and prose is
    exactly the wrong carrier for a path another reader has to open. The one
    consumer is :func:`worker_tokens`, which sums that file's `usage` objects; it
    reads what THIS resolution matched instead of going looking for a transcript
    itself, which is the whole point of putting the path here.
    """
    transcript = payload.get("transcript_path")
    if not isinstance(transcript, str) or not transcript:
        return None, "the hook payload carried no transcript_path", None
    parent = Path(transcript)
    # `<project>/<session-id>.jsonl` sits beside `<project>/<session-id>/`.
    session_dir = parent.with_suffix("")
    bare = agent_id[len("agent-") :] if agent_id.startswith("agent-") else agent_id
    for candidate in (
        session_dir / "subagents" / f"agent-{bare}.jsonl",
        session_dir / "subagents" / f"{bare}.jsonl",
    ):
        if candidate.is_file():
            text = _first_user_message(candidate)
            if text:
                return text, f"first user message of {candidate}", candidate
    text = _first_user_message(parent, agent_id=bare)
    if text:
        return text, f"first sidechain message for agent {bare} in {parent}", parent
    return (
        None,
        (
            f"no readable transcript for agent {bare} under {session_dir / 'subagents'} "
            f"or {parent}"
        ),
        None,
    )


def _spawn_sidecar(payload: dict, agent_id: str) -> tuple[dict, str]:
    """The harness's own ``agent-<id>.meta.json``, and where it was looked for.

    KYO-154: every sidecar carries ``spawnDepth``, and every one written for an agent
    another agent spawned also carries ``parentAgentId`` — whose agent this is, without
    inferring it from prose (docs/design/dispatch.md#kyo-154).

    An empty dict for anything unreadable, never an exception: this runs inside a
    ``SubagentStop`` hook, and the whole point of the outcome vocabulary is that a
    missing input degrades to a named answer rather than a crash or a guess.
    """
    transcript = payload.get("transcript_path")
    if not isinstance(transcript, str) or not transcript:
        return {}, "the hook payload carried no transcript_path"
    session_dir = Path(transcript).with_suffix("")
    bare = agent_id[len("agent-") :] if agent_id.startswith("agent-") else agent_id
    subagents = session_dir / "subagents"
    for candidate in (subagents / f"agent-{bare}.meta.json", subagents / f"{bare}.meta.json"):
        try:
            if candidate.is_file():
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data, str(candidate)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    return {}, f"no readable spawn sidecar for agent {bare} under {subagents}"


def _untranscribed_agent(payload: dict, agent_id: str) -> tuple[bool, str, bool]:
    """KYO-373.c2: is the stopping agent one the harness keeps NO trace of at all?

    Returns ``(untranscribed, detail, absent)``. ``untranscribed`` is ``True`` only
    when every one of these holds, and the detail says which of them failed otherwise,
    because the unresolved stop that results has to be able to say why it was not
    classified. ``absent`` is the narrower fact the reason code needs: no transcript
    FILE exists for the agent, whether or not the rest held.

    * the hook payload names a session transcript, and that file exists — so the
      session directory below is the real one and not a guess;
    * neither ``subagents/agent-<id>.jsonl`` nor ``subagents/<id>.jsonl`` exists (a
      file that exists but yielded no prompt is NOT this case: something was written
      for that agent, so it stays an ordinary unresolved stop);
    * no spawn sidecar exists for it either — the record measured "no ``.meta.json``"
      as part of the signature, and a sidecar is the harness saying it spawned this
      agent;
    * the capped sidechain scan of the session transcript found no first message for
      it (the caller already ran that scan; it is why this is asked at all); and
    * the SAME ``subagents/`` directory holds at least one other agent's transcript.

    The last one is the narrowing, and it is what answers KYO-163's refusal rather than
    overruling it silently. KYO-163 declined to classify on the absence of a trace
    because a real worker whose transcript the harness failed to write would then go
    unaudited, and because a harness LAYOUT change — transcripts moving somewhere this
    module does not look — would turn every real worker into "not a worker" at once,
    which is the dangerous half of KYO-128 returning by a new route. A session whose
    ``subagents/`` directory holds other agents' transcripts is a session in which the
    harness demonstrably writes them where this looks; one with none is a session in
    which an absent file cannot be told from a moved one, and there the stop stays
    ``unresolved`` and is audited exactly as before. What remains is the residual
    KYO-163 named — one agent's write failing while its siblings' succeed — and it is
    stated here rather than hidden.
    """
    transcript = payload.get("transcript_path")
    if not isinstance(transcript, str) or not transcript:
        return False, "the hook payload carried no transcript_path", False
    parent = Path(transcript)
    session_dir = parent.with_suffix("")
    subagents = session_dir / "subagents"
    bare = agent_id[len("agent-") :] if agent_id.startswith("agent-") else agent_id
    try:
        if not parent.is_file():
            return False, f"the session transcript {parent} does not exist", False
        for name in (f"agent-{bare}.jsonl", f"{bare}.jsonl"):
            if (subagents / name).exists():
                return False, (
                    f"{subagents / name} exists but yielded no readable first message"
                ), False
        for name in (f"agent-{bare}.meta.json", f"{bare}.meta.json"):
            if (subagents / name).exists():
                return False, f"a spawn sidecar {subagents / name} exists for it", True
        witnessed = subagents.is_dir() and any(subagents.glob("*.jsonl"))
    except OSError as exc:
        return False, f"the session directory could not be read ({exc})", False
    if not witnessed:
        return False, (
            f"the harness has written no agent transcript under {subagents} in this "
            "session at all, so an absent file cannot be told from a layout change and "
            "the stop is not classified as not-a-worker (KYO-373)"
        ), True
    return True, (
        f"no transcript for agent {bare} exists anywhere the resolution looks — no "
        f"{subagents / f'agent-{bare}.jsonl'}, no spawn sidecar beside it, no sidechain "
        f"row for it in {parent} — while the harness HAS written transcripts under "
        f"{subagents} for the agents it spawned in this session. An agent this "
        "orchestrator dispatched always has one, so this is not a dispatched worker (the "
        "~32s SubagentStop tick mints a fresh id like this one, KYO-163), and no "
        "directory was checked for it (KYO-373)"
    ), True


def _spawning_agent(meta: dict) -> str | None:
    """The id of the agent that spawned this one, per the harness's sidecar.

    ``parentAgentId`` is the whole signal, and ``spawnDepth`` is deliberately NOT
    used as a substitute for it. Depth answers "how far from the top-level session"
    — which is only the same question as "was this spawned by a worker" while the
    controller *is* the top-level session. That is true of every controller this
    project has run, and it is a convention rather than a guarantee, so leaning on
    it would put the dangerous half of KYO-128's defect back: a genuine worker
    misread as a helper is a worker whose dirty tree is never audited. A named
    parent is a fact about this agent; a depth is a fact about a topology.
    """
    parent = meta.get("parentAgentId")
    if isinstance(parent, str) and parent:
        return parent
    return None


def _names_token(prompt: str, token: str) -> bool:
    """Does ``prompt`` name ``token`` as a whole id rather than as a fragment?

    ``KYO-12.d1`` must not match inside ``KYO-12.d11``, and a bare substring test
    would. The dispatch id is the thing being matched precisely because Kyosei
    minted it; matching it loosely would give that back.
    """
    pattern = rf"(?<![0-9A-Za-z_.-]){re.escape(token)}(?![0-9A-Za-z_-])"
    return re.search(pattern, prompt) is not None


def _dispatches_named_by(prompt: str, declared: list[dict]) -> tuple[list[dict], bool]:
    """The candidate dispatches one prompt names, and whether it named them by id.

    The dispatch id first, and on its own: it is Kyosei's own token, it appears in
    every brief this tool generates, and it is specific enough that a match is a
    match. The resolved directory is the second route, for a prompt that names the
    worktree without naming the dispatch.

    KYO-154 extracted this from :func:`resolve_stop_target` unchanged, because the
    same question now gets asked of a second prompt — the parent's — and two copies
    of it would be free to disagree about what counts as naming a dispatch.

    What this deliberately does NOT do is rank the hits. Every id in the prompt is
    treated alike here, because "named" and "named as its own" are two different
    questions and KYO-201 is what happens when the second one is never asked:
    :func:`_brief_own_dispatch` asks it, over this function's output.
    """
    by_id = [row for row in declared if _names_token(prompt, row["dispatch_id"])]
    if by_id:
        return by_id, True
    return [row for row in declared if str(row["worktree_resolved"]) in prompt], False


#: KYO-201.c2. The ``- **Dispatch id**: <id>`` bullet that `brief.generate_brief`
#: writes as the FIRST machine-filled slot of every brief it generates — before the
#: base sha, before the declared worktree, and (this is the point) before the
#: "Other dispatches running" bullet that names everybody else. A prompt carrying a
#: generated brief therefore says which dispatch it IS, in a shape Kyosei minted,
#: and not merely which dispatch ids it mentions. Matched as a whole line rather
#: than as a bare id anywhere in the prompt: the whole defect being fixed here is a
#: bare-id match treating a mention of somebody else as a claim about yourself.
#: `tests/test_subagent_stop_worktree.py` runs this pattern over a brief that
#: `generate_brief` actually produced, so the two cannot drift apart in silence.
_BRIEF_OWN_DISPATCH_RE = re.compile(
    r"^[ \t]*[-*][ \t]+\*\*Dispatch id\*\*:[ \t]*([A-Za-z0-9][0-9A-Za-z_.-]*)[ \t\r]*$",
    re.MULTILINE,
)


def _brief_own_dispatch(prompt: str) -> str | None:
    """The dispatch a prompt's generated-brief header claims as its own, if any.

    ``None`` for a prompt that carries no such header — which is a real and common
    shape (a controller may hand the worker a path to the frozen brief rather than
    paste it), and is why this is a PREFERENCE applied over the existing match
    rather than a replacement for it.
    """
    match = _BRIEF_OWN_DISPATCH_RE.search(prompt)
    return match.group(1) if match else None


def _named_with_states(rows: list[dict]) -> str:
    """Every dispatch a prompt named, each with its state (KYO-201.c2).

    Deliberately no directories: this string is rendered into the hook's message AND
    stored in the git-canonical stop-audit ledger, and an absolute worktree path
    there is permanent, meaningless on any other checkout, and identifying (KYO-52).
    The state is the fact a reader needs — it is what says why the losing candidate
    was in the running at all.
    """
    if not rows:
        return "none"
    return ", ".join(f"{row['dispatch_id']} ({row['state']})" for row in rows)


def _declared_worktree_rows(ctx: Context) -> tuple[list[dict], str | None]:
    """Every dispatch that declared a worktree that still exists, whatever its state.

    Returns ``(rows, unreadable)``: "the store said there are none" and "the store could
    not be asked" must not arrive at the caller identical (KYO-325,
    :data:`STOP_TARGET_STORE_UNREADABLE`).

    State is not consulted (KYO-201.c1): a dispatch leaves ``running`` the instant its
    report is ingested, and a worker may stop after that, so a running-only candidate set
    matches a stopping worker against some other dispatch.

    THE ONE NARROWING: the directory has to be there. What this feeds runs `git status` in
    the path it returns, and a pruned worktree cannot hold uncommitted work. It also keeps
    :data:`STOP_TARGET_NO_WORKTREES` meaningful — without it every research subagent's stop
    in a project with any history would report "declared worktrees are in play". `kyo
    worktrees` computes the same fact the same way (`WORKTREE_STATE_GONE`). History:
    docs/design/dispatch.md#kyo-201.

    Read-only and failure-tolerant: these guards are ``PROJECT_OPTIONAL`` and run
    from a hook, so a repository with no store, or a store this build cannot open,
    must produce "nothing to match against" rather than an exception inside a
    ``SubagentStop``.
    """
    layout_kind = ctx.layout.kind if ctx.layout is not None else config_module.LAYOUT_IN_REPO
    if layout_kind == config_module.LAYOUT_UNCONFIGURED:
        # No store is configured here at all. That is not a store this build failed
        # to read — there is nothing to read — so it stays an empty answer and the
        # caller's `no-declared-worktree-in-play` is the honest one.
        return [], None
    try:
        store = ctx.store()
        rows = store.all_dispatches()
    except Exception as exc:  # noqa: BLE001 - a hook must never die on a store it cannot read
        # KYO-325: the exception text is carried out rather than swallowed; it names the
        # defect and usually the remedy in one line (docs/design/dispatch.md#kyo-325).
        return [], str(exc)
    out: list[dict] = []
    for row in rows:
        stored = row["worktree"]
        if stored is None:
            continue
        try:
            resolved = ctx.resolve_worktree(stored)
            if not resolved.exists():
                continue
        except Exception:  # noqa: BLE001 - a malformed stored path is not fatal here
            continue
        out.append(
            {
                "dispatch_id": row["id"],
                "record_id": row["record_id"],
                "state": row["state"],
                "seq": row["seq"],
                "worktree": stored,
                "worktree_resolved": resolved,
            }
        )
    return out, None


def resolve_stop_target(ctx: Context, raw_payload: str) -> StopTarget:
    """Which tree a ``SubagentStop`` guard should read, given the hook's payload.

    The fallback is always the hook's own cwd — today's behaviour — but WHICH of
    the three outcomes produced it is recorded, because the caller's decision about
    whether to block differs between them and a reader has to be able to see which
    one they got.
    """
    fallback = ctx.code_root
    payload = _payload_of(raw_payload)
    candidates, store_unreadable = _declared_worktree_rows(ctx)
    # KYO-325, and it has to come FIRST, because the outcome it replaces is the one
    # below: an unreadable store produced an empty candidate list, which read as
    # `no-declared-worktree-in-play` — a positive claim that this cwd is the worker's
    # tree, which `StopTarget.resolved` believes and `record_stop_audit` writes no row
    # for. Declining to answer is the right answer here, and it has to be a LOUD one:
    # the store is exactly what this stop would have been written to, so a stop that
    # gets here leaves no durable trace by construction and the sentence the guard
    # prints is the only copy of it.
    if store_unreadable is not None:
        return StopTarget(
            fallback,
            STOP_TARGET_STORE_UNREADABLE,
            "this project's store could not be read, so which dispatches declared a "
            "worktree is not known and this stop was matched against nothing "
            f"({store_unreadable}). "
            "This cwd is the hook's own and is NOT known to be the stopping worker's "
            "tree — and because the store is what a stop is recorded IN, this one "
            "cannot be written down either: if the `kyo` running this hook is an "
            "older build than the store it is reading (an installed build lags the "
            "source tree until it is reinstalled), upgrade it and the whole mechanism comes "
            "back on its own (KYO-325)",
            candidates=[],
        )
    # Declared worktrees that are not simply the project root. A dispatch that
    # declared no worktree defaults to the project root (KYO-22), and "matching"
    # the stop to that tells us nothing the fallback does not already say.
    declared = [
        row for row in candidates if row["worktree_resolved"] != fallback.resolve()
    ]
    candidate_ids = [row["dispatch_id"] for row in declared]

    if not declared:
        return StopTarget(
            fallback,
            STOP_TARGET_NO_WORKTREES,
            "no dispatch has declared a worktree that exists outside the project root, "
            "so the project root IS every dispatch's worktree here and this cwd is "
            "the right tree to read",
            candidates=candidate_ids,
        )

    agent_id = payload.get("agent_id") or payload.get("agentId")
    if not isinstance(agent_id, str) or not agent_id:
        return StopTarget(
            fallback,
            STOP_TARGET_UNRESOLVED,
            "the hook payload carried no agent_id, so Claude Code did not name the agent "
            f"that stopped, while {len(declared)} dispatch(es) DO have a declared "
            "worktree — this cwd is therefore probably not the stopping worker's tree",
            candidates=candidate_ids,
            reason=STOP_REASON_OTHER,
        )

    # KYO-154, and it has to come BEFORE the prompt match rather than after it. A
    # helper spawned by a worker is handed the worker's own context, so its prompt
    # names the worker's dispatch and matches it — correctly as a matter of string
    # matching, and wrongly as a matter of subject. The tree that match resolves to
    # is the PARENT's, the parent is mid-work, and the helper is then blocked on
    # uncommitted files it did not write, cannot commit and cannot revert. That is
    # the unclearable condition KYO-128 exists to have removed, arriving one level
    # in. Asking whose agent this is first is what stops it being asked at all.
    sidecar, _sidecar_where = _spawn_sidecar(payload, agent_id)
    parent_agent = _spawning_agent(sidecar)
    if parent_agent is not None:
        parent_prompt, parent_how, _parent_transcript = _dispatch_prompt(payload, parent_agent)
        parent_hits, _parent_by_id = _dispatches_named_by(parent_prompt or "", declared)
        # KYO-201.c2 reaches this path too, and it had to. A worker's own prompt is
        # the brief, and a brief names every OTHER dispatch that was running when it
        # was frozen ("Other dispatches running") — so the moment the candidate set
        # stopped being "running only", this parent prompt began matching two and
        # three dispatches and `len(...) == 1` began failing, which would have
        # silently retired KYO-154's helper outcome. The header preference is what
        # keeps it firing, and it is also what makes it CORRECT: under the old narrow
        # set a parent whose brief named one still-running other dispatch was placed
        # as that other dispatch, which is the same misattribution one level up.
        parent_own = _brief_own_dispatch(parent_prompt or "")
        narrowed = [row for row in parent_hits if row["dispatch_id"] == parent_own]
        parent_hits = narrowed or parent_hits
        parent_dispatches = {row["dispatch_id"] for row in parent_hits}
        # Both halves must be positive, and the second is what keeps this narrow. A
        # sidecar naming a parent says only "some agent spawned this one"; it is the
        # parent's own prompt resolving to exactly one running dispatch that says the
        # parent is a WORKER, and therefore that this stop is that worker's helper.
        # Where the parent cannot be placed, nothing here fires and the outcome is
        # whatever it was before — declining to answer is the existing `unresolved`,
        # and a fourth outcome that fired on a guess would be worth less than it.
        if len(parent_dispatches) == 1:
            row = parent_hits[0]
            return StopTarget(
                row["worktree_resolved"],
                STOP_TARGET_NOT_A_WORKER,
                f"the stopping agent was spawned by agent {parent_agent}, whose own "
                f"prompt names {row['dispatch_id']} (dispatching {row['record_id']}) "
                f"({parent_how}) — so this stop is that worker's HELPER and not that "
                "worker. It has no worktree of its own; the tree it shared is its "
                "parent's, and the parent is still working in it",
                candidates=candidate_ids,
                helper_of=row["dispatch_id"],
            )

    prompt, how, transcript = _dispatch_prompt(payload, agent_id)
    if not prompt:
        # KYO-373.c2. Asked only once the prompt search has come back empty, and
        # after KYO-154's helper test, so a helper with a sidecar is still a helper.
        untranscribed, detail, absent = _untranscribed_agent(payload, agent_id)
        if untranscribed:
            return StopTarget(
                fallback,
                STOP_TARGET_NO_TRANSCRIPT,
                detail,
                candidates=candidate_ids,
                reason=STOP_REASON_NO_TRANSCRIPT,
            )
        return StopTarget(
            fallback,
            STOP_TARGET_UNRESOLVED,
            f"{how}, so the stopping agent could not be matched to any of the "
            f"{len(declared)} running dispatch(es) with a declared worktree ({detail})",
            candidates=candidate_ids,
            transcript=transcript,
            reason=STOP_REASON_NO_TRANSCRIPT if absent else STOP_REASON_OTHER,
        )

    hits, by_id = _dispatches_named_by(prompt, declared)
    directories = {str(row["worktree_resolved"]) for row in hits}

    # KYO-201.c2. "Named" and "named as its own" are different claims, and the whole
    # of this record is what happens when only the first is asked: a prompt that
    # mentions another dispatch — which is the RIGHT thing for a controller to tell a
    # worker about a concurrent edit, and which every generated brief does by itself
    # in its "Other dispatches running" bullet — was as good as a prompt about that
    # dispatch. Where the prompt carries a generated brief, the header settles it,
    # and it settles it in BOTH directions: the header's dispatch wins if it is a
    # candidate, and if it is not, this stop is refused rather than handed the
    # nearest other dispatch the prompt happened to name. The second half is the
    # one that matters — being wrong about which tree is the harm, not being unsure.
    header_id = _brief_own_dispatch(prompt)
    if header_id is not None:
        own = next((row for row in hits if row["dispatch_id"] == header_id), None)
        if own is not None:
            return StopTarget(
                own["worktree_resolved"],
                STOP_TARGET_MATCHED,
                f"the stopping agent's prompt carries a generated brief whose header "
                f"names {header_id} as its OWN dispatch (dispatching "
                f"{own['record_id']}, state {own['state']}), and that dispatch declared "
                f"this directory. Dispatches with a declared worktree that the prompt "
                f"names, with each one's state: {_named_with_states(hits)} ({how})",
                dispatch_id=own["dispatch_id"],
                candidates=candidate_ids,
                transcript=transcript,
            )
        return StopTarget(
            fallback,
            STOP_TARGET_UNRESOLVED,
            f"the stopping agent's prompt carries a generated brief whose header names "
            f"{header_id} as its OWN dispatch, and no dispatch with a declared worktree "
            f"on disk here is {header_id}. The other dispatch(es) the prompt names, with "
            f"each one's state — {_named_with_states(hits)} — are ones it merely "
            f"mentions, and matching a stop to one of those is how a check ends up "
            f"confident about another worker's tree ({how})",
            candidates=candidate_ids,
            transcript=transcript,
            reason=STOP_REASON_OTHER,
        )

    if len(directories) == 1:
        # Several dispatches can resolve to one directory — a record re-dispatched
        # into the same worktree, which the widened set now sees where the running-
        # only one saw at most one of them. The directory is the answer either way;
        # the most recent of them is the one this stop is likelier to BE, and naming
        # the oldest would put a stale dispatch id in a message about a live tree.
        row = max(hits, key=lambda candidate: candidate["seq"])
        return StopTarget(
            row["worktree_resolved"],
            STOP_TARGET_MATCHED,
            f"{row['dispatch_id']} (dispatching {row['record_id']}, state "
            f"{row['state']}) declared this directory and the stopping agent's own "
            f"prompt names {'that dispatch id' if by_id else 'that directory'} ({how})",
            dispatch_id=row["dispatch_id"],
            candidates=candidate_ids,
            transcript=transcript,
        )
    if not directories:
        return StopTarget(
            fallback,
            STOP_TARGET_UNRESOLVED,
            f"the stopping agent's prompt names none of the {len(declared)} "
            f"dispatch(es) with a declared worktree ({how})",
            candidates=candidate_ids,
            transcript=transcript,
            reason=STOP_REASON_PROMPT_NAMES_NONE,
        )
    return StopTarget(
        fallback,
        STOP_TARGET_UNRESOLVED,
        f"the stopping agent's prompt names {len(directories)} different dispatch "
        f"worktrees ({how}) and carries no generated-brief header saying which of them "
        f"is its own — {_named_with_states(hits)} — so which one this stop belongs to is "
        "genuinely unknown, and guessing between them is how a check ends up confident "
        "about the wrong tree",
        candidates=candidate_ids,
        transcript=transcript,
        reason=STOP_REASON_PROMPT_NAMES_SEVERAL,
    )
