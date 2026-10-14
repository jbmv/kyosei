"""``kyo close-wave`` — a merged wave's closing sequence as one command (KYO-536).

Before this command, an orchestrator closed each wave by hand, with the same
sequence: the whole suite; ``kyo move <id> verified`` then ``done`` per record;
``kyo changelog --all --output docs/reference/changelog.md``; ``kyo docs index`` and
``kyo reference --output docs/reference/cli.md`` when they had drifted; a commit;
``make install-tool``; ``kyo serve --restart``; ``kyo worktrees --prune``. The
stale-changelog test failed repeatedly for one reason only: the records were closed
AFTER the changelog was regenerated. The order is the content of this command.

**What is Kyosei's and what is the project's.** The moves are Kyosei's, and they are
made through ``kyo move`` itself, the way ``kyo accept --close`` makes them
(:func:`kyosei.cmd_accept._close_blocker` is the same predicate ``kyo move`` meets).
Everything else in the sequence is a fact about one project — which command is its
whole suite, which documents it generates and with what, what it reinstalls or
restarts afterwards — so it is declared in ``kyosei/config.yml`` and never guessed:

.. code-block:: yaml

    close_wave:
      suite:              # optional: each of these must have passed at HEAD
        - make test
      generate:           # run in order, after the moves and before the commit
        - .venv/bin/python -m kyosei changelog --all --output docs/reference/changelog.md
        - run: .venv/bin/python -m kyosei reference --output docs/reference/cli.md
          note: held to the parser by tests/test_kyo138_command_reference.py
      after:              # printed every time; run only with --run-after
        - make install-tool
        - kyo serve --restart
        - kyo worktrees --prune

Each ``generate``/``after`` entry is a command line or ``{run, note}``, the shape
``worktree_setup`` already has. Without ``close_wave.suite`` the suite requirement
falls back to the existing ``orchestrator_only_commands`` (KYO-298): any one of them
passing at HEAD satisfies it, and the output says that is the rule it applied.

**It runs no suite.** The suite is the orchestrator's, run once per wave; what this
command needs is the FACT that it passed, at the code this commit will carry. That
fact is recorded by chaining on the suite's own exit status::

    make test && kyo close-wave --suite-passed "make test"

which writes ``kyosei-suite-passes.json`` in the checkout's git directory (``git
rev-parse --git-path``): never tracked, per checkout, and holding for each declared
suite command the HEAD it passed at and a fingerprint of HEAD's tree OUTSIDE
``kyosei/`` and ``ideas/``. A pass stays good across a store-only commit (an
``accept``, a shift note) and goes stale the moment any code changes. The recording
is refused while anything outside those two directories is uncommitted, because a
suite that ran against uncommitted work is a verdict about no commit. **What this
proves and what it does not**: the ``&&`` is the mechanism, so a pass is only as
honest as the line that recorded it — Kyosei cannot see the suite's exit status, and
a person who types ``--suite-passed`` without running anything has recorded a pass
that never happened. It is the same trust ``kyo verify --method machine-checked``
asks for, and it is stated rather than hidden.

**A completion outside a wave** (KYO-577). A record that reaches completion by another
route leaves the generated documents behind the store, and a suite that checks them
then fails before this command can run. Three pieces close that:

* ``kyo confirm`` regenerates itself: :func:`refresh_after_completion` runs the same
  ``generate`` lines and commits their output with the store, when the tree outside
  the store is clean, and otherwise names the line below. Since KYO-603 the typed
  ``kyo move`` (:func:`cmd_move`, into or out of the completion status) and ``kyo
  accept --close`` do the same.
* A run in which every named record is ALREADY at completion moves nothing and closes
  nothing, so it needs no suite pass: it regenerates and commits, and says why the suite
  was not required. ``kyo close-wave <a done record>`` is therefore the refresh, and
  every ``suite-not-passed`` refusal names it when ``generate`` is declared.
* A pass is carried across a commit this module made from a clean tree
  (:func:`carry_passes`): that commit's paths outside the store are generator output
  and nothing else, which close-wave regenerates after its moves anyway, so they are
  exempt — those paths, across that commit, and only for a pass good just before it.

**Refuses before any write.** Every blocker is collected and printed at once, under
``contract close-wave-not-ready``: a record whose criteria, docs obligations or gates
would refuse its completion move (in that move's own words), a suite with no pass at
this code, and an uncommitted change outside the store. ``--dry-run`` prints the
whole plan — including what it WOULD refuse — and writes nothing.

**Not one transaction**, on ``kyo accept``'s reasoning: the moves, the generators and
the commit are separate acts, and a failure part way stops the sequence there and
says so (exit 1) with what already happened. A rerun is safe: a record already at
completion is left where it is, and the commit stages only what changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import tempfile
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path

from . import cmd_records
from .clock import now
from .cmd_accept import _close_blocker, _run, _rungs_to_completion
from .cmd_check_command import controller_only_matches
from .commands import Context, Output, _load_status_ladder
from .config import (
    CONFIG_NAME,
    KYOSEI_DIR,
    SetupCommand,
    _parse_controller_only_commands,
    config_path_for,
    load_config,
    read_raw_config,
)
from .contracts import GUARDED_BRANCH_DIRS
from .errors import ContractViolation, KyoseiError, UsageError
from .git_queries import run_git

#: The ``kyosei/config.yml`` key this command reads (``schema.CONFIG_CONSUMERS``).
CONFIG_KEY = "close_wave"
#: Its sub-keys, closed: a misspelled ``generte:`` that parsed to nothing would make
#: the command commit a wave without regenerating anything, and say it had.
CONFIG_SUBKEYS = ("suite", "generate", "after")
STEP_KEYS = frozenset({"run", "note"})

#: Every precondition the closing sequence checks before it writes, as one contract —
#: so a wave with three problems is told all three in one run.
CONTRACT_CLOSE_WAVE_NOT_READY = "close-wave-not-ready"
#: ``--suite-passed`` could not record a pass: the command is not a declared suite, or
#: the code it would vouch for is not a commit.
CONTRACT_CLOSE_WAVE_PASS_NOT_RECORDABLE = "close-wave-suite-pass-not-recordable"

#: The labels each blocker line starts with, for a reader (or a test) keying on kind.
BLOCKER_RECORD = "record-not-closable"
BLOCKER_SUITE = "suite-not-passed"
BLOCKER_TREE = "code-not-committed"

#: Where the suite passes live, inside the checkout's git directory.
PASS_FILE_NAME = "kyosei-suite-passes.json"
PASS_FILE_VERSION = 1

#: How many trailing output lines a failed generate or after step prints.
FAILED_STEP_TAIL_LINES = 20
#: The commit can run the repository's own hooks, so it gets more than git's default.
COMMIT_TIMEOUT_SECONDS = 600.0


# ------------------------------------------------------------------ configuration


@dataclass(frozen=True)
class CloseWaveConfig:
    """``close_wave`` as declared, plus the whole-suite commands it falls back to."""

    suite: tuple[str, ...]
    generate: tuple[SetupCommand, ...]
    after: tuple[SetupCommand, ...]
    orchestrator_only: tuple[str, ...]

    @property
    def required_suite(self) -> tuple[str, ...]:
        return self.suite or self.orchestrator_only

    @property
    def needs_every_suite(self) -> bool:
        """``close_wave.suite`` is ALL of; the fallback is ANY ONE of."""
        return bool(self.suite)

    @property
    def recordable(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.suite, *self.orchestrator_only)))


def _parse_steps(value: object, path: Path, key: str) -> tuple[SetupCommand, ...]:
    where = f"{path} key '{CONFIG_KEY}.{key}'"
    if value is None:
        return ()
    if not isinstance(value, list):
        raise KyoseiError(
            f"{where} must be a list of commands, or absent; found "
            f"{type(value).__name__} — hand edit {path}: write it as a list"
        )
    steps: list[SetupCommand] = []
    for index, entry in enumerate(value):
        at = f"{where} entry {index + 1}"
        if isinstance(entry, dict):
            unknown = sorted(set(entry) - STEP_KEYS)
            if unknown or "run" not in entry:
                raise KyoseiError(
                    f"{at} must be a command string or a mapping with 'run' and an "
                    f"optional 'note'; found keys {sorted(entry)} — hand edit {path}"
                )
            run, note = entry["run"], entry.get("note") or ""
        else:
            run, note = entry, ""
        if not isinstance(run, str) or not run.strip() or not isinstance(note, str):
            raise KyoseiError(
                f"{at} must be a non-empty command line (a string); found {run!r} — "
                f"hand edit {path}: give it a command, or remove the entry"
            )
        steps.append(SetupCommand(run.strip(), note.strip()))
    return tuple(steps)


def read_close_wave_config(ctx: Context) -> CloseWaveConfig:
    """``close_wave`` from ``kyosei/config.yml``, validated.

    Parsed here, at the one command that reads it, rather than in ``load_config``: a
    malformed ``close_wave`` must refuse ``kyo close-wave`` and nothing else — not the
    hooks, which load the config on every prompt and every stop.
    """
    path = config_path_for(ctx.root)
    raw = read_raw_config(ctx.root).get(CONFIG_KEY)
    orchestrator_only = tuple(load_config(ctx.root).controller_only_commands)
    if raw is None:
        return CloseWaveConfig((), (), (), orchestrator_only)
    if not isinstance(raw, dict):
        raise KyoseiError(
            f"{path} key '{CONFIG_KEY}' must be a mapping with any of "
            f"{', '.join(CONFIG_SUBKEYS)}; found {type(raw).__name__} — hand edit {path}"
        )
    unknown = sorted(set(raw) - set(CONFIG_SUBKEYS))
    if unknown:
        raise KyoseiError(
            f"{path} key '{CONFIG_KEY}' carries {', '.join(unknown)}, which nothing "
            f"reads — hand edit {path}: the keys are {', '.join(CONFIG_SUBKEYS)}"
        )
    return CloseWaveConfig(
        suite=_parse_controller_only_commands(raw.get("suite"), path, f"{CONFIG_KEY}.suite"),
        generate=_parse_steps(raw.get("generate"), path, "generate"),
        after=_parse_steps(raw.get("after"), path, "after"),
        orchestrator_only=orchestrator_only,
    )


# ------------------------------------------------------------------ the code tree


@dataclass(frozen=True)
class CodeState:
    """The code repository as this command finds it."""

    top: Path
    head: str
    #: sha256 of HEAD's top-level tree entries outside the store directories.
    fingerprint: str
    #: Uncommitted paths (tracked or untracked) outside the store directories.
    dirty: tuple[str, ...]


def _git_out(top: Path, *args: str) -> str:
    try:
        result = run_git(top, *args)
    except OSError as exc:
        raise KyoseiError(f"git could not answer in {top} ({exc})") from exc
    if result.returncode != 0:
        raise KyoseiError(f"`git {' '.join(args)}` failed in {top}: {result.stderr.strip()}")
    return result.stdout


def _guarded_names() -> set[str]:
    return {entry.rstrip("/") for entry in GUARDED_BRANCH_DIRS}


def _dirty_outside_store(top: Path) -> tuple[str, ...]:
    exclusions = [f":(exclude,top){name}" for name in sorted(_guarded_names())]
    out = _git_out(
        top, "status", "--porcelain", "-z", "--untracked-files=all",
        "--ignore-submodules=none", "--", ":(top)", *exclusions,
    )  # fmt: skip
    paths: list[str] = []
    fields = out.split("\0")
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if len(field) < 4:
            continue
        code, path = field[:2], field[3:]
        paths.append(path)
        if ("R" in code or "C" in code) and index < len(fields):
            paths.append(fields[index])
            index += 1
    return tuple(dict.fromkeys(paths))


def code_state(ctx: Context) -> CodeState:
    """HEAD, its code fingerprint, and what is uncommitted, read from git now."""
    try:
        probe = run_git(ctx.code_root, "rev-parse", "--show-toplevel")
    except OSError as exc:
        raise KyoseiError(f"git could not answer in {ctx.code_root} ({exc})") from exc
    if probe.returncode != 0:
        raise KyoseiError(
            f"{ctx.code_root} is not a git repository; closing a wave ends in a commit, "
            "so it runs only where the store's code repository is"
        )
    top = Path(probe.stdout.strip())
    head = _git_out(top, "rev-parse", "HEAD").strip()
    guarded = _guarded_names()
    entries = [
        line
        for line in _git_out(top, "ls-tree", "HEAD").splitlines()
        if line.split("\t", 1)[-1] not in guarded
    ]
    fingerprint = hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()
    return CodeState(top, head, fingerprint, _dirty_outside_store(top))


# ------------------------------------------------------------------ suite passes


def pass_file(top: Path) -> Path:
    """``kyosei-suite-passes.json`` in this checkout's git directory."""
    relative = _git_out(top, "rev-parse", "--git-path", PASS_FILE_NAME).strip()
    return (top / relative).resolve()


def read_passes(top: Path) -> dict[str, dict]:
    path = pass_file(top)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    passes = data.get("passes") if isinstance(data, dict) else None
    return passes if isinstance(passes, dict) else {}


def write_passes(top: Path, passes: dict[str, dict]) -> None:
    path = pass_file(top)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": PASS_FILE_VERSION, "passes": passes}, indent=2) + "\n",
        encoding="utf-8",
    )


def carry_passes(before: CodeState, after: CodeState, paths: list[str], by: str) -> list[str]:
    """KYO-577: carry each pass that was good at ``before`` across a regeneration commit.

    Called only after a commit this module made itself, from a tree that was clean
    outside the store before the generate steps ran — so every path that commit changed
    outside the store (``paths``) is generate-step output and nothing else. Those paths
    are exempt, and only those, and only across that one commit: a pass is carried when
    its fingerprint matched the tree just before the commit, so code nobody ran the suite
    on can never ride along. Why it is safe: what the pass would otherwise be withheld
    for is generator output, which ``kyo close-wave`` regenerates after its moves and
    commits without a suite run anyway — the same exposure, no more. What it costs to
    refuse instead is measured: a whole suite (about 5.5 minutes here) to re-vouch for
    code that did not change.
    """
    if not paths or after.fingerprint == before.fingerprint:
        return []
    passes = read_passes(before.top)
    carried: list[str] = []
    for command, entry in passes.items():
        if not isinstance(entry, dict) or entry.get("code_fingerprint") != before.fingerprint:
            continue
        hops = [hop for hop in entry.get("carried") or [] if isinstance(hop, dict)]
        hops.append({"to": after.head, "paths": list(paths), "by": by, "at": now()})
        passes[command] = {**entry, "code_fingerprint": after.fingerprint, "carried": hops}
        carried.append(command)
    if carried:
        write_passes(before.top, passes)
    return carried


def _store_names() -> str:
    return " or ".join(sorted(GUARDED_BRANCH_DIRS))


def _matched_suites(typed: str, cfg: CloseWaveConfig) -> list[str]:
    matches = controller_only_matches(typed, cfg.recordable, cwd=None)
    return list(dict.fromkeys(match.declared for match in matches))


def suite_pass_entry(typed: str, state: CodeState, actor: str) -> dict:
    return {
        "typed": typed,
        "head": state.head,
        "code_fingerprint": state.fingerprint,
        "at": now(),
        "actor": actor,
    }


def record_suite_pass(
    ctx: Context, typed: str, cfg: CloseWaveConfig, state: CodeState, *, write: bool
) -> tuple[list[str], dict[str, dict]]:
    """``--suite-passed``: the lines it prints and the passes it (would) write."""
    matched = _matched_suites(typed, cfg)
    unmet: list[str] = []
    if not matched:
        declared = ", ".join(f"`{c}`" for c in cfg.recordable) or "none"
        unmet.append(
            f"`{typed}` is not a whole-suite command this project declares (declared: "
            f"{declared}). Hand edit {config_path_for(ctx.root)}: list it under "
            f"`orchestrator_only_commands` or `{CONFIG_KEY}: suite:`, or record the "
            "command the project declares"
        )
    if state.dirty:
        unmet.append(
            f"uncommitted changes outside {_store_names()}, so the suite ran against "
            f"code no commit holds: {', '.join(state.dirty)}. Commit them (or discard "
            "them), run the suite again, and record that run"
        )
    if unmet:
        raise ContractViolation(CONTRACT_CLOSE_WAVE_PASS_NOT_RECORDABLE, unmet)
    passes = read_passes(state.top)
    entry = suite_pass_entry(typed, state, ctx.actor)
    for declared in matched:
        passes[declared] = entry
    verb = "would record" if not write else "recorded"
    if write:
        write_passes(state.top, passes)
    lines = [
        f"suite pass {verb}: {', '.join(f'`{c}`' for c in matched)} at "
        f"{state.head[:12]} ({pass_file(state.top)})"
    ]
    return lines, passes


def _remedy(command: str) -> str:
    return f"`{command} && kyo close-wave --suite-passed {shlex.quote(command)}`"


def suite_status(
    ctx: Context, cfg: CloseWaveConfig, state: CodeState, passes: dict[str, dict]
) -> tuple[list[str], list[str], dict]:
    """``(lines, blockers, data)`` for the suite precondition."""
    required = cfg.required_suite
    if not required:
        blocker = (
            f"[{BLOCKER_SUITE}] this project declares no whole-suite command, so there is "
            f"nothing whose pass could be checked. Hand edit {config_path_for(ctx.root)}: "
            f"declare it under `{CONFIG_KEY}: suite:` (or `orchestrator_only_commands`), "
            f"then run it and record the pass: {_remedy('<suite command>')}"
        )
        return [], [blocker], {"required": [], "rule": None, "passed": [], "missing": []}
    good: list[str] = []
    missing: list[str] = []
    lines: list[str] = []
    for command in required:
        entry = passes.get(command)
        if entry and entry.get("code_fingerprint") == state.fingerprint:
            good.append(command)
            hops = [hop for hop in entry.get("carried") or [] if isinstance(hop, dict)]
            last = hops[-1].get("to") if hops else entry.get("head")
            same = "".join(
                f"; carried across {str(hop.get('to'))[:12]} ({hop.get('by')}), which "
                f"changed only generated documents ({', '.join(hop.get('paths') or [])})"
                for hop in hops
            ) + (
                ""
                if last == state.head
                else (f"; HEAD is now {state.head[:12]}, which differs only under {_store_names()}")
            )
            lines.append(
                f"suite: `{command}` passed at {str(entry.get('head'))[:12]} "
                f"({entry.get('at')}){same}"
            )
        else:
            missing.append(command)
    rule = "all" if cfg.needs_every_suite else "any"
    data = {"required": list(required), "rule": rule, "passed": good, "missing": missing}
    if rule == "all" and missing:
        blockers = [
            f"[{BLOCKER_SUITE}] `{command}` has no recorded pass at this code "
            f"(HEAD {state.head[:12]}). Run it and record it: {_remedy(command)}"
            for command in missing
        ]
        return lines, blockers, data
    if rule == "any" and not good:
        blockers = [
            f"[{BLOCKER_SUITE}] none of this project's whole-suite commands "
            f"({', '.join(f'`{c}`' for c in required)}) has a recorded pass at this code "
            f"(HEAD {state.head[:12]}); without `{CONFIG_KEY}: suite:` any one of them "
            f"will do. Run one and record it: {_remedy(required[0])}"
        ]
        return lines, blockers, data
    if rule == "any":
        lines.append(
            f"suite rule: any one of {', '.join(f'`{c}`' for c in required)} (no "
            f"`{CONFIG_KEY}: suite:` declared)"
        )
    return lines, [], data


# ------------------------------------------------------------------ steps


@dataclass
class StepResult:
    command: str
    exit_code: int
    tail: list[str]

    def as_json(self) -> dict:
        return {"run": self.command, "exit_code": self.exit_code, "tail": self.tail}


def run_shell_step(command: str, cwd: Path) -> StepResult:
    """Run one declared command line through the shell, from the code root.

    Output goes to a temporary FILE, never a pipe: an after-step such as a server
    restart leaves a child holding its stdout, and a pipe would keep this command
    waiting for that child to exit.
    """
    with tempfile.TemporaryFile() as log:
        completed = subprocess.run(  # noqa: S602 - the project's own declared command
            command,
            shell=True,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=dict(os.environ),
            check=False,
        )
        log.seek(0)
        text = log.read().decode("utf-8", errors="replace")
    tail = text.splitlines()[-FAILED_STEP_TAIL_LINES:]
    return StepResult(command, completed.returncode, tail)


def _step_line(step: SetupCommand) -> str:
    return f"    $ {step.run}" + (f"    # {step.note}" if step.note else "")


def _commit_message(ids: list[str], complete: str, generated: bool) -> str:
    tail = "; generated documents regenerated" if generated else ""
    return f"store: {', '.join(ids)} {complete}{tail} (kyo close-wave)"


def _repo_top(directory: Path) -> Path | None:
    try:
        probe = run_git(directory, "rev-parse", "--show-toplevel")
    except OSError:
        return None
    return Path(probe.stdout.strip()) if probe.returncode == 0 else None


def _commit(top: Path, paths: list[str], message: str) -> str | None:
    """Stage ``paths`` and commit exactly them; the new sha, or ``None`` if unchanged."""
    _git_out(top, "add", "-A", "--", *paths)
    staged = run_git(top, "diff", "--cached", "--quiet", "--", *paths)
    if staged.returncode == 0:
        return None
    result = run_git(
        top, "commit", "-q", "-m", message, "--", *paths, timeout=COMMIT_TIMEOUT_SECONDS
    )
    if result.returncode != 0:
        raise KyoseiError(f"git commit failed in {top}: {(result.stderr or result.stdout).strip()}")
    return _git_out(top, "rev-parse", "HEAD").strip()


# ------------------------------------------------------------------ the command


def cmd_close_wave(ctx: Context, args: Namespace) -> Output:
    """``kyo close-wave [RECORD_ID ...] [--suite-passed CMD] [--dry-run] [--run-after]``.

    Order: record the suite pass (when asked); check every precondition and refuse
    before any write; move each record to completion through the ladder; run the
    declared generators; commit the store's export and what the generators changed;
    print the after-steps, running them only under ``--run-after``.
    """
    ids = list(dict.fromkeys(args.record_ids or []))
    if not ids and not args.suite_passed:
        raise UsageError(
            "name the records to close, or pass --suite-passed with the whole-suite "
            "command that just passed, or both"
        )
    dry = bool(args.dry_run)
    cfg = read_close_wave_config(ctx)
    state = code_state(ctx)
    lines: list[str] = []
    data: dict = {"ok": True, "command": "close-wave", "dry_run": dry, "head": state.head}

    passes = read_passes(state.top)
    if args.suite_passed:
        recorded, passes = record_suite_pass(ctx, args.suite_passed, cfg, state, write=not dry)
        lines += recorded
        data["suite_pass_recorded"] = not dry
    if not ids:
        data["complete"] = True
        return Output(lines, data)

    store = ctx.store()
    ladder = _load_status_ladder(store)
    blockers: list[str] = []
    plan: list[dict] = []
    for record_id in ids:
        status = store.record(record_id)["status"]
        if status == ladder.complete:
            plan.append({"record_id": record_id, "status": status, "rungs": []})
            continue
        refusal = _close_blocker(store, record_id, ladder)
        if refusal is not None:
            blockers.append(
                f"[{BLOCKER_RECORD}] {record_id} (at {status}) cannot reach "
                f"{ladder.complete}: " + " ".join(part.strip() for part in refusal)
            )
            continue
        plan.append(
            {"record_id": record_id, "status": status,
             "rungs": _rungs_to_completion(ladder, status)}
        )  # fmt: skip
    if state.dirty:
        blockers.append(
            f"[{BLOCKER_TREE}] uncommitted changes outside {_store_names()}: "
            f"{', '.join(state.dirty)}. Commit them, or discard them (a generated file an "
            "earlier close-wave wrote is regenerated), then run the suite again if code "
            "changed"
        )
    suite_lines, suite_blockers, suite_data = suite_status(ctx, cfg, state, passes)
    # KYO-577: a run that moves nothing closes nothing, so it needs no suite pass —
    # it is the refresh `kyo confirm` names when it could not regenerate itself.
    refresh_only = len(plan) == len(ids) and not any(item["rungs"] for item in plan)
    data["refresh_only"] = refresh_only
    hints: list[str] = []
    if refresh_only:
        suite_data["waived"] = True
        suite_lines.append(_refresh_only_line(ladder.complete))
        suite_blockers = []
    elif suite_blockers:
        hints = _stale_generated_hint(store, cfg, ladder.complete)
    blockers += suite_blockers
    data.update({"suite": suite_data, "blockers": blockers, "ready": not blockers})
    if hints:
        data["hints"] = hints

    message = _commit_message(ids, ladder.complete, bool(cfg.generate))
    if dry:
        data.update(
            records=plan,
            generate=[step.run for step in cfg.generate],
            after=[step.run for step in cfg.after],
            commit_message=message,
        )
        plan_lines = _plan_lines(ctx, cfg, state, plan, blockers, suite_lines, message, args)
        hint_lines = [f"  {hint}" for hint in hints]
        return Output([*plan_lines[:1], *lines, *hint_lines, *plan_lines[1:]], data)
    if blockers:
        blockers += hints
        if args.suite_passed:
            blockers.append(
                "(not a blocker: the suite pass was recorded before this refusal — once "
                "the above clear, rerun without --suite-passed)"
            )
        raise ContractViolation(CONTRACT_CLOSE_WAVE_NOT_READY, blockers)

    lines += suite_lines
    # 1. the moves, through `kyo move` itself
    moved: list[dict] = []
    for item in plan:
        if not item["rungs"]:
            lines.append(f"{item['record_id']}: already {ladder.complete}")
            continue
        for rung in item["rungs"]:
            step = _run(ctx, ["move", item["record_id"], rung], cmd_records.cmd_move,
                        tolerate=(ContractViolation,))  # fmt: skip
            if step.refused is not None:
                lines += [f"REFUSED: {step.command}", *(f"  {x}" for x in step.lines)]
                _incomplete(lines, "a move refused; nothing was generated or committed")
        moved.append(item)
        lines.append(f"{item['record_id']}: {item['status']} → {' → '.join(item['rungs'])}")
    data["records"] = plan

    # 2. the generators
    generated: list[dict] = []
    for step in cfg.generate:
        result = run_shell_step(step.run, state.top)
        generated.append(result.as_json())
        if result.exit_code != 0:
            lines += [f"generate FAILED (exit {result.exit_code}): {step.run}",
                      *(f"  {x}" for x in result.tail)]  # fmt: skip
            _incomplete(lines, "the records were moved; nothing was committed")
        lines.append(f"generated: {step.run}")
    data["generate"] = generated

    # 3. the commit: the store's export and what the generators changed
    data["commits"] = _commit_and_carry(ctx, state, message, lines, by="kyo close-wave")

    # 4. the after-steps
    data["after"] = _after(cfg, state, args, lines)
    data["complete"] = True
    return Output(lines, data)


def _refresh_only_line(complete: str) -> str:
    return (
        f"suite: not required — every named record is already {complete}, so this run "
        "moves nothing and closes nothing; it regenerates the declared documents and "
        f"commits them with the store (the tree outside {_store_names()} is clean, so "
        "that commit holds generator output and nothing else)"
    )


def refresh_command(record_ids: list[str]) -> str:
    """The line that regenerates and commits the declared documents for records that
    are already at completion — :func:`cmd_close_wave` with nothing to move."""
    return "kyo " + shlex.join(["close-wave", *record_ids])


def _latest_completed(store, complete: str) -> str | None:
    rows = [store.record(record_id) for record_id in store.all_record_ids()]
    done = [row for row in rows if row["status"] == complete]
    if not done:
        return None
    return max(done, key=lambda row: (row["updated_at"] or "", row["id"]))["id"]


def _stale_generated_hint(store, cfg: CloseWaveConfig, complete: str) -> list[str]:
    """KYO-577.c2: the one manual step that can remain, named in full.

    A suite that checks a committed generated document fails when a record reached
    completion (or was restated or rescoped) by a route that did not regenerate it, and
    then this refusal is all the orchestrator sees. ``kyo confirm``, ``kyo move`` and
    ``kyo accept --close`` regenerate on their own from a clean tree (KYO-577, KYO-603);
    one run over uncommitted work, and an edit, do not.
    """
    if not cfg.generate:
        return []
    done = _latest_completed(store, complete)
    if done is None:
        return []
    return [
        "(not a blocker on its own: if the suite FAILED on a generated document that has "
        f"fallen behind the store — a record moved to {complete}, restated or rescoped "
        "since the last close-wave by a route that did not regenerate it, such as a move "
        "made over uncommitted work — refresh the "
        f"documents first, on their own: `{refresh_command([done])}` (any record already "
        f"at {complete} will do; it moves nothing, needs no suite pass, and commits the "
        f"`{CONFIG_KEY}: generate:` output with the store), then run the suite again and "
        "record its pass)"
    ]


#: KYO-577: what a completion outside a wave did about the declared generated documents.
REFRESH_NONE_DECLARED = "none-declared"
REFRESH_COMMITTED = "committed"
REFRESH_UNCHANGED = "unchanged"
REFRESH_SKIPPED = "skipped"
REFRESH_FAILED = "failed"


def refresh_after_completion(
    ctx: Context,
    record_ids: list[str],
    *,
    by: str,
    status: str | None = None,
    remedy_ids: list[str] | None = None,
) -> dict:
    """Regenerate and commit the declared documents after records reached completion
    outside ``kyo close-wave`` (KYO-577) — close-wave's steps 2 and 3, without the moves
    (already made) and without a suite (nothing closes). ``kyo confirm``, ``kyo move``
    and ``kyo accept --close`` call it (KYO-603).

    ``status`` is the status the commit message names (the completion status unless
    given: a move OUT of completion changes the changelog too). ``remedy_ids`` are the
    records the remedy line names (``record_ids`` unless given), which must be at
    completion for that line to move nothing; ``[]`` names none.

    Runs exactly the ``close_wave: generate:`` lines, in order, from the code root, and
    only when the tree outside the store is clean, so the commit it makes holds the
    store's export and generator output and nothing else. A project declaring no
    generate steps gets nothing: no command runs, no commit is made, no line printed.

    Never raises for the refresh itself: the records have already moved, and that stands
    (the composition rule of :mod:`kyosei.cmd_accept`). A step that cannot run, or
    fails, is returned as ``skipped`` or ``failed`` with the reason and ``remedy`` — the
    line that finishes the job once its cause is cleared — for the caller to report as
    incomplete.
    """
    result: dict = {"status": REFRESH_NONE_DECLARED, "lines": [], "generate": [],
                    "commits": [], "remedy": None}  # fmt: skip
    remedy_targets = record_ids if remedy_ids is None else remedy_ids
    remedy = refresh_command(remedy_targets) if remedy_targets else None
    try:
        cfg = read_close_wave_config(ctx)
    except KyoseiError as exc:
        return _refresh_not_done(result, REFRESH_SKIPPED, str(exc), remedy)
    if not cfg.generate:
        return result
    try:
        state = code_state(ctx)
    except KyoseiError as exc:
        return _refresh_not_done(result, REFRESH_SKIPPED, str(exc), None)
    if state.dirty:
        return _refresh_not_done(
            result,
            REFRESH_SKIPPED,
            f"uncommitted changes outside {_store_names()} ({', '.join(state.dirty)}), "
            "which a commit of generator output could not be told apart from. Commit or "
            "discard them",
            remedy,
        )
    lines: list[str] = result["lines"]
    for step in cfg.generate:
        ran = run_shell_step(step.run, state.top)
        result["generate"].append(ran.as_json())
        if ran.exit_code != 0:
            lines += [f"generate FAILED (exit {ran.exit_code}): {step.run}",
                      *(f"  {x}" for x in ran.tail)]  # fmt: skip
            return _refresh_not_done(
                result,
                REFRESH_FAILED,
                "a generate step failed, so nothing was committed and whatever it wrote is "
                "left in the working tree. Fix the step, discard its partial output",
                remedy,
            )
        lines.append(f"generated: {step.run}")
    if not _dirty_outside_store(state.top):
        result["status"] = REFRESH_UNCHANGED
        lines.append("generated documents: unchanged, so nothing was committed")
        return result
    ladder = _load_status_ladder(ctx.store())
    message = (
        f"store: {', '.join(record_ids)} {status or ladder.complete}; generated documents "
        f"regenerated ({by})"
    )
    try:
        result["commits"] = _commit_and_carry(ctx, state, message, lines, by=by)
    except KyoseiError as exc:
        return _refresh_not_done(result, REFRESH_FAILED, str(exc), remedy)
    result["status"] = REFRESH_COMMITTED
    return result


def cmd_move(ctx: Context, args: Namespace) -> Output:
    """``kyo move`` as typed: :func:`kyosei.cmd_records.cmd_move`, then the refresh.

    KYO-603. A plan moved to done with ``kyo move`` after the wave's close-wave had
    regenerated the changelog, and every whole-suite run from that commit on failed the
    committed changelog's freshness test until someone regenerated it by hand.
    So a typed move that enters or leaves the completion status does what ``kyo
    confirm`` does (KYO-577): :func:`refresh_after_completion`, which runs the declared
    ``close_wave: generate:`` lines and commits their output with the store's export
    when the tree outside the store is clean, and otherwise prints the line that does.

    Only the typed command is wrapped (``cli.COMMAND_TABLE``). ``kyo close-wave``,
    ``kyo accept --close`` and ``kyo confirm`` compose ``cmd_records.cmd_move`` directly
    and regenerate once for the whole act. The move's own output, exit status and
    ``--json`` are unchanged; ``refresh`` is added to the payload only when the project
    declares generate steps, so a project that declares none sees nothing new.
    """
    try:
        before: str | None = ctx.store().record(args.id)["status"]
    except KyoseiError:
        before = None  # the move refuses below, in its own words
    output = cmd_records.cmd_move(ctx, args)
    if not output.data.get("changed"):
        return output
    store = ctx.store()
    complete = _load_status_ladder(store).complete
    after = output.data["record"]["status"]
    if complete not in (before, after):
        return output
    if after == complete:
        remedy_ids = [args.id]
    else:
        latest = _latest_completed(store, complete)
        remedy_ids = [latest] if latest else []
    refresh = refresh_after_completion(
        ctx, [args.id], by="kyo move", status=after, remedy_ids=remedy_ids
    )
    if refresh["status"] == REFRESH_NONE_DECLARED:
        return output
    output.lines += refresh["lines"]
    output.data["refresh"] = {key: value for key, value in refresh.items() if key != "lines"}
    return output


def _refresh_not_done(result: dict, status: str, why: str, remedy: str | None) -> dict:
    result["status"] = status
    result["remedy"] = remedy
    then = (
        f", then regenerate and commit them: `{remedy}` (it moves nothing and needs no "
        "suite pass)"
        if remedy
        else ""
    )
    result["why"] = why
    result["lines"].append(
        f"generated documents NOT refreshed ({status}): {why}{then}. Until then a suite "
        "that checks them fails on the stale copies"
    )
    return result


def _incomplete(lines: list[str], what: str) -> None:
    raise KyoseiError(
        "\n".join(
            [
                *lines,
                f"INCOMPLETE: {what} — rerunning the same command is safe (a record "
                "already at completion is left where it is)",
            ]
        )
    )


def _commit_and_carry(
    ctx: Context, state: CodeState, message: str, lines: list[str], *, by: str
) -> list[dict]:
    """:func:`_commit_all`, then carry the suite passes across the code commit it made.

    ``state`` must be the tree as it was BEFORE the generate steps ran, and clean
    outside the store — the condition :func:`carry_passes` rests on.
    """
    commits = _commit_all(ctx, state, message, lines)
    code = next((c for c in commits if c["repo"] == str(state.top) and c["sha"]), None)
    if code is not None:
        after = code_state(ctx)
        paths = [p for p in code["paths"] if not _in_store(p)]
        for command in carry_passes(state, after, paths, by):
            lines.append(
                f"suite pass for `{command}` carried to {after.head[:12]}: that commit "
                f"changed only generated documents outside {_store_names()}"
            )
    return commits


def _in_store(path: str) -> bool:
    return path.split("/", 1)[0] in _guarded_names()


def _commit_all(ctx: Context, state: CodeState, message: str, lines: list[str]) -> list[dict]:
    export = ctx.store().config.export_path
    store_top = _repo_top(export)
    generated_paths = list(_dirty_outside_store(state.top))
    groups: dict[Path, list[str]] = {}
    if generated_paths:
        groups.setdefault(state.top, []).extend(generated_paths)
    if store_top is not None:
        groups.setdefault(store_top, []).append(
            os.path.relpath(export.resolve(), store_top.resolve())
        )
    else:
        lines.append(f"store export {export} is in no git repository; not committed")
    commits: list[dict] = []
    for top, paths in groups.items():
        sha = _commit(top, paths, message)
        commits.append({"repo": str(top), "paths": paths, "sha": sha})
        lines.append(
            f"committed {sha[:12]} in {top}: {', '.join(paths)}"
            if sha
            else f"nothing to commit in {top} ({', '.join(paths)} unchanged)"
        )
    return commits


def _after(cfg: CloseWaveConfig, state: CodeState, args: Namespace, lines: list[str]) -> list:
    if not cfg.after:
        lines.append(f"after-steps: none declared ({CONFIG_KEY}: after: in {CONFIG_NAME})")
        return []
    if not args.run_after:
        lines.append("after-steps (not run; pass --run-after to run them):")
        lines += [_step_line(step) for step in cfg.after]
        return [{"run": step.run, "exit_code": None} for step in cfg.after]
    results: list[dict] = []
    for step in cfg.after:
        result = run_shell_step(step.run, state.top)
        results.append(result.as_json())
        if result.exit_code != 0:
            lines += [f"after-step FAILED (exit {result.exit_code}): {step.run}",
                      *(f"  {x}" for x in result.tail)]  # fmt: skip
            _incomplete(lines, "the wave is committed; the remaining after-steps did not run")
        lines.append(f"ran: {step.run}")
    return results


def _plan_lines(
    ctx: Context,
    cfg: CloseWaveConfig,
    state: CodeState,
    plan: list[dict],
    blockers: list[str],
    suite_lines: list[str],
    message: str,
    args: Namespace,
) -> list[str]:
    """``--dry-run``: everything the real run would do, and nothing done."""
    head = (
        f"kyo close-wave --dry-run: WOULD REFUSE ({len(blockers)} blocker(s), "
        f"contract {CONTRACT_CLOSE_WAVE_NOT_READY}) — nothing written"
        if blockers
        else "kyo close-wave --dry-run: ready — nothing written"
    )
    out = [head]
    out += [f"  {line}" for line in blockers]
    out += suite_lines
    out.append("1. move (through `kyo move`):")
    for item in plan:
        rungs = item["rungs"]
        out.append(
            f"    {item['record_id']}: " + (
                f"{item['status']} → {' → '.join(rungs)}" if rungs else "already at completion"
            )
        )  # fmt: skip
    out.append(f"2. generate (from {state.top}):")
    out += [_step_line(step) for step in cfg.generate] or [
        f"    none declared ({CONFIG_KEY}: generate: in {KYOSEI_DIR}/{CONFIG_NAME})"
    ]
    out.append(
        f"3. commit {ctx.store().config.export_path} and every path the generate steps "
        f'change outside {_store_names()}: "{message}"'
    )
    run = "run (--run-after)" if args.run_after else "printed, not run (pass --run-after)"
    out.append(f"4. after-steps, {run}:")
    out += [_step_line(step) for step in cfg.after] or ["    none declared"]
    return out
