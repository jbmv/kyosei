"""KYO-154: a worker's own helper is not blocked by the worker's dirty worktree.

KYO-128 removed the unclearable stop condition one level up — a worker blocked for
the CONTROLLER's uncommitted files, measured at ~229,000 tokens on a single dispatch
— and it did so by asking "which tree is this stop about?". This record is the same
shape one level in, and it is a case KYO-128 could not have considered, because when
KYO-128 was built no worker had ever spawned a helper.

A helper shares its parent's worktree and is handed its parent's context, so its
first user message names its parent's dispatch. KYO-128's matching therefore
resolves it to the parent's worktree — correct as string matching, wrong as
subject. That tree is dirty because the parent is *still working in it*, and the
helper is then blocked on files it did not write, cannot commit (they are not its
own, and its brief forbids its parent's branch to it as much as anyone's), and
cannot revert without destroying its parent's work. Nobody woken can clear it.

So the guard asks a question before "which tree?": **whose agent is this?** The
answer comes from the harness's own spawn sidecar rather than from prose, and the
answer is a fourth named outcome rather than a silent pass, because "this stop had
no worktree of its own" and "this stop's worktree was clean" are different facts
(KYO-154.c2, and `governs_staleness`'s `cannot-tell` and KYO-129's `no-documents`
before it).

Every test here keeps the CONTROLLER's checkout dirty, as KYO-128's own tests do,
and the parent WORKER's worktree dirty as well — which is the state this record is
about and the one no previous test could produce.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .test_subagent_stop_worktree import (
    CONTROLLER_EDIT,
    _payload,
    _running_dispatch,
)

#: The worker's agent id, and the helper it spawned. Two constants rather than two
#: literals: half of what these tests assert is which of the two the guard is
#: talking about, and a typo'd id would make that assertion vacuous instead of red.
WORKER_AGENT = "a1b2c3d4e5f6a7b8c"
HELPER_AGENT = "a9f0b1c2d3e4f5a6b"


@pytest.fixture
def controller_dirty(git_project: Path) -> Path:
    """The controller's checkout, dirty throughout — as in KYO-128's own tests.

    Re-declared rather than imported: a pytest fixture is registered by the module
    that defines it, and importing the function would give a plain callable.
    """
    (git_project / CONTROLLER_EDIT).write_text("edited by the controller\n", encoding="utf-8")
    return git_project


def _write_agent(
    session_dir: Path,
    agent_id: str,
    prompt: str,
    *,
    spawned_by: str | None = None,
    sidecar: bool = True,
) -> None:
    """One subagent's transcript, plus the spawn sidecar Claude Code writes beside it.

    Both files are shaped from real ones on disk. The sidecar's fields are
    ``agentType``/``description``/``toolUseId``/``spawnDepth``/``model``, plus
    ``parentAgentId`` on exactly those agents another agent spawned — which is the
    field this whole record turns on and the one KYO-128 recorded as absent.

    ``sidecar=False`` writes the transcript alone, which is what the world looked
    like to KYO-128 and is how the canary below shows the bug is real.
    """
    subagents = session_dir / "subagents"
    subagents.mkdir(parents=True, exist_ok=True)
    (subagents / f"agent-{agent_id}.jsonl").write_text(
        json.dumps(
            {
                "type": "user",
                "isSidechain": True,
                "agentId": agent_id,
                # The controller's values, on every record, always. That is why the
                # transcript's own fields cannot answer any of this (KYO-128).
                "cwd": "/the/controllers/cwd",
                "gitBranch": "main",
                "message": {"role": "user", "content": prompt},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    if not sidecar:
        return
    meta = {
        "agentType": "claude",
        "description": f"agent {agent_id}",
        "toolUseId": f"toolu_{agent_id}",
        "spawnDepth": 2 if spawned_by else 1,
        "model": "opus",
    }
    if spawned_by:
        meta["parentAgentId"] = spawned_by
    (subagents / f"agent-{agent_id}.meta.json").write_text(
        json.dumps(meta), encoding="utf-8"
    )


def _session(
    tmp_path: Path,
    dispatch_id: str | None,
    *,
    helper_sidecar: bool = True,
    parent_is_a_worker: bool = True,
) -> Path:
    """A session holding a dispatched worker and the helper that worker spawned.

    The helper's own prompt names the parent's dispatch, deliberately: that is what
    a helper's context actually looks like, and it is the thing that used to resolve
    it to the parent's worktree and block it there.
    """
    session_dir = tmp_path / "transcripts" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_dir.with_suffix(".jsonl").write_text("", encoding="utf-8")
    worker_prompt = (
        f"You are a dispatched Kyosei worker on dispatch **{dispatch_id}**."
        if parent_is_a_worker
        else "You are a research subagent for the controller. Read some files."
    )
    helper_prompt = (
        f"Survey the README's sections for dispatch {dispatch_id} and report back. "
        "Read-only: do not edit anything."
        if parent_is_a_worker
        # A helper of a non-worker inherits a non-worker's context, so it names no
        # dispatch either. Handing it the worker's prompt would test a session that
        # cannot occur and would hide which of the two checks did the work.
        else "Survey the README's sections and report back. Read-only."
    )
    _write_agent(session_dir, WORKER_AGENT, worker_prompt)
    _write_agent(
        session_dir,
        HELPER_AGENT,
        helper_prompt,
        spawned_by=WORKER_AGENT,
        sidecar=helper_sidecar,
    )
    return session_dir.with_suffix(".jsonl")


def _helper_stops(kyo, transcript: Path, controller: Path, *args: str):
    """Run `check-clean` exactly as the ``SubagentStop`` hook runs it, for the helper."""
    return kyo(
        "check-clean",
        "--subagent-stop",
        *args,
        stdin=_payload(transcript, controller, HELPER_AGENT),
    )


def _worker_stops(kyo, transcript: Path, controller: Path, *args: str):
    """The same, for the dispatched worker itself — the c3 control."""
    return kyo(
        "check-clean",
        "--subagent-stop",
        *args,
        stdin=_payload(transcript, controller, WORKER_AGENT),
    )


# ------------------- c1: an agent that is not the worker is not blocked for its tree


def test_a_helper_is_not_blocked_by_its_parent_workers_dirty_worktree(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-154.c1, the substance.

    The parent worker is mid-work, so its worktree holds uncommitted files. Before
    this, the helper resolved to that tree, was blocked on those files, and had no
    action available to it that would clear the block: they are not its files, it
    was spawned read-only, and committing or reverting them would corrupt the work
    of a worker that has not finished. Observed on KYO-144's dispatch.
    """
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-helper")
    (tree / "work-in-progress.py").write_text("# the PARENT is still editing this\n", "utf-8")
    transcript = _session(tmp_path, dispatch_id)

    result = _helper_stops(kyo, transcript, controller_dirty)
    assert result.code == 0, result
    assert "work-in-progress.py" not in result.err, (
        "the helper was blocked on its parent's uncommitted work — the unclearable "
        "condition KYO-128 exists to have removed, one level in"
    )


def test_the_helpers_own_prompt_would_have_matched_its_parents_dispatch(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """The canary the whole record rests on: without the sidecar, the bug is here.

    Remove the one file that says who spawned this agent and everything else is
    identical — same prompt, same dirty parent tree — and the guard resolves the
    helper to its parent's worktree and blocks it there, exit 2, citing files the
    helper did not write. A fix whose test cannot produce the defect it fixes is
    not evidence of anything.
    """
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-canary")
    (tree / "work-in-progress.py").write_text("# the PARENT is still editing this\n", "utf-8")
    transcript = _session(tmp_path, dispatch_id, helper_sidecar=False)

    result = _helper_stops(kyo, transcript, controller_dirty, "--json")
    assert result.code == 2, (
        "the fixture no longer reproduces KYO-154 — this test is now vacuous and the "
        "one above proves nothing"
    )
    assert result.json["subject"]["outcome"] == "matched"
    assert "work-in-progress.py" in result.err


# --------------------------------- c2: a named outcome, not a silent pass


def test_the_helper_outcome_is_named_and_distinguishable_in_json(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-154.c2.

    Three things must be told apart in `--json`, and a bare exit 0 tells apart none
    of them: a clean worker worktree, a helper with no worktree of its own, and a
    stop the guard could not place at all. The outcome token is what separates them,
    and ``helper_of`` names the dispatch the stop belongs to without claiming the
    stop IS that dispatch's.
    """
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-named")
    (tree / "work-in-progress.py").write_text("# mid-work\n", "utf-8")
    transcript = _session(tmp_path, dispatch_id)

    result = _helper_stops(kyo, transcript, controller_dirty, "--json")
    assert result.code == 0, result
    data = result.json
    assert data["subject"]["outcome"] == "helper-of-a-dispatched-worker"
    assert data["subject"]["helper_of"] == dispatch_id
    assert data["subject"]["dispatch_id"] is None, (
        "a helper's stop was recorded as the dispatch's OWN stop; a consumer counting "
        "worker stops would count it"
    )
    assert data["checked_the_worker_tree"] is False, (
        "the guard claimed to have audited a worker's tree on a stop that was not a "
        "worker's — the false negative KYO-128 calls the half that matters"
    )
    assert data["subject"]["outcome"] != "unresolved", (
        "'not a worker' collapsed into 'could not tell'; they are different facts"
    )


def test_the_output_says_no_verdict_rather_than_nothing_was_checked(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-128's ``unresolved`` shouts NOTHING WAS CHECKED because a tree that should
    have been audited was not. Here nothing needed auditing, and reusing that sentence
    would report a healthy stop as a failure — which is how a warning stops being read.
    """
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-wording")
    (tree / "work-in-progress.py").write_text("# mid-work\n", "utf-8")
    transcript = _session(tmp_path, dispatch_id)

    result = _helper_stops(kyo, transcript, controller_dirty)
    combined = result.out + result.err
    assert "NOTHING WAS CHECKED" not in combined
    assert "HELPER" in combined
    assert dispatch_id in combined, "the output did not name the dispatch this stop belongs to"
    assert CONTROLLER_EDIT not in combined, (
        "the controller's own edits were cited at a helper — KYO-128's false positive"
    )


def test_a_helpers_stop_is_recorded_but_is_not_an_unaudited_stop(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-134's ledger keeps the row; `kyo doctor` must not call it a finding.

    Recorded, because KYO-134.c5's lesson is that an empty list renders "nothing
    happened" and "nothing found" identically, and a discriminator with no evidence
    it ever ran is the same trap. Not a finding, because the remedy `kyo doctor`
    prints — "check that worktree before merging its branch" — is advice about a
    tree that WAS audited, at its own worker's stop.
    """
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-ledger")
    (tree / "work-in-progress.py").write_text("# mid-work\n", "utf-8")
    transcript = _session(tmp_path, dispatch_id)

    result = _helper_stops(kyo, transcript, controller_dirty, "--json")
    assert result.json["stop_audit_event"], "the helper's stop was not recorded at all"

    doctored = kyo("doctor", "--json")
    assert doctored.json["checks"]["unresolved_stops"]["failures"] == [], (
        "a helper's stop was reported as a stop that audited nothing"
    )
    listing = kyo("worktrees", "--json")
    assert listing.json["stop_audits"]["helpers"] == 1, listing.json["stop_audits"]
    assert listing.json["stop_audits"]["unresolved"] == []
    assert dispatch_id in listing.json["stop_audits"]["sentence"] or "HELPER" in (
        listing.json["stop_audits"]["sentence"]
    ), "the ledger's own sentence does not mention the helper rows it is counting"


# ----------------- c3: the guard KYO-128 fixed still behaves exactly as it did


def test_the_parent_worker_itself_is_still_blocked_by_its_own_dirty_worktree(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-154.c3, the boundary, on the very tree the helper was let off.

    Same session, same worktree, same uncommitted file. The agent that owns that
    work is still stopped for it — otherwise this record would have bought the
    helper's freedom with the false negative KYO-128 calls the half that matters.
    """
    dispatch_id, tree = _running_dispatch(kyo, tmp_path, "wt-parent")
    (tree / "work-in-progress.py").write_text("# the worker's OWN unfinished work\n", "utf-8")
    transcript = _session(tmp_path, dispatch_id)

    result = _worker_stops(kyo, transcript, controller_dirty)
    assert result.code == 2, "the dispatched worker stopped clean on its own dirty worktree"
    assert "work-in-progress.py" in result.err
    assert CONTROLLER_EDIT not in result.err


def test_a_clean_parent_worker_still_passes_and_names_its_own_worktree(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """KYO-128.c1/c3 unchanged for a worker in a session that also contains a helper.

    The sidecar the helper introduced is present in this session; a worker's own has
    no ``parentAgentId``, and reading one must not change what a worker gets.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-parent-clean")
    transcript = _session(tmp_path, dispatch_id)

    result = _worker_stops(kyo, transcript, controller_dirty, "--json")
    assert result.code == 0, result
    assert result.json["subject"]["outcome"] == "matched"
    assert result.json["subject"]["dispatch_id"] == dispatch_id
    assert result.json["checked_the_worker_tree"] is True


def test_a_parent_that_is_not_a_dispatched_worker_changes_nothing(
    kyo, controller_dirty: Path, tmp_path: Path
) -> None:
    """What keeps the new outcome narrow, and it is the half that could have been a guess.

    A sidecar naming a parent says only "some agent spawned this one". It is the
    PARENT's own prompt resolving to exactly one running dispatch that says the
    parent is a worker — and where it does not resolve, nothing fires and the stop
    keeps whatever outcome it had before. Declining to answer already has a name
    here (`unresolved`); a fourth outcome that fired on a guess would be worth less
    than it.
    """
    dispatch_id, _tree = _running_dispatch(kyo, tmp_path, "wt-not-a-worker")
    transcript = _session(tmp_path, dispatch_id, parent_is_a_worker=False)

    result = _helper_stops(kyo, transcript, controller_dirty, "--json")
    assert result.code == 0, result
    assert result.json["subject"]["outcome"] == "unresolved", (
        "the helper outcome fired for an agent whose parent could not be placed as a "
        "worker — that is a guess, not an identification"
    )
