"""KYO-298: the worker-suite policy as a mechanism rather than a sentence.

WHAT THIS RECORD IS ABOUT, because it decides what these tests are worth pinning.
The rule "run the tests that cover your change, not the whole suite" was in
`kyosei/procedures.md` and in every brief. Two of five dispatched workers in one wave
ran the whole suite anyway — because another line of the *same brief*, this project's
own ``worktree_setup``, told them to run `make test`. Each of those runs was ten to
seventeen minutes of a worker waiting, and each was followed by a full-context re-write
(172,733 and 325,752 tokens of cache creation, 28% of all the prompt cache that wave's
workers wrote) because the harness's prompt cache expired while they sat there. Both
runs also contended with the controller's own suite and failed desktop tests there.

So the thing under test is not "does a string appear in a message". It is that a
rule which lost to a contradiction in its own brief now has a mechanism that does not
lose to it:

* the project DECLARES which commands are the whole suite, because only the project
  knows, and a project that declares nothing gets nothing (``c1``);
* a `PreToolUse` check refuses one of them where it would actually cost something —
  inside a RUNNING dispatch's worktree — and nowhere else (``c2``);
* the way past it is one word, in the refusal, and it asks to be reported (``c3``);
* `kyo doctor` notices the contradiction itself, in the config, where it can be fixed
  (``c5``).

``c4`` (the packaged hook) is in ``tests/test_install_hooks.py`` beside the other
hook-distribution tests, and ``c6`` (the re-write counter) is in
``tests/test_kyo284_worker_tokens.py`` beside the transcript reader it extends —
both deliberately, so a later reader looking at either mechanism finds its tests with
the rest of that mechanism rather than in a record-shaped file.

KYO-312.c2 is here too, in its own section below, and that is the same judgment running
the other way: it is a SECOND refusal raised by the same command from the same payload —
a Bash call whose own declared timeout is longer than the ceiling the project states —
and splitting it into a file of its own would leave two halves of one guard's behaviour
where neither reader finds the other. Its record is the next turn of the same screw: the
worker KYO-298's rule was written for went on to wait 10.1 and then 6.1 minutes in a
single foreground loop, re-writing 250,466 and 255,899 tokens, because the standing rule
that told it not to run the suite also told it to "look once, at about the time it should
be done".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from kyosei import doctor
from kyosei.cmd_check_command import controller_only_matches
from kyosei.config import CONFIG_KEY_EXAMPLES, load_config
from kyosei.contracts import LONG_COMMAND_OVERRIDE_ENV, SUITE_OVERRIDE_ENV
from kyosei.errors import EXIT_CONTRACT, EXIT_ERROR

from .conftest import git_init, invoke, new_record, valid_report
from .test_subagent_stop_worktree import _running_dispatch

# --------------------------------------------------------------------- helpers


def _declare(project: Path, *commands: str) -> None:
    """Write ``controller_only_commands`` into a project's config.yml.

    By hand rather than through a `kyo` command, and that is the design: this key is
    the maintainer's declaration about their own project, and there is deliberately no
    command that edits it — a registry a command could rewrite on request is a
    ``--force`` with extra steps (``registry.Registry.require``'s reasoning).
    """
    path = project / "kyosei" / "config.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["controller_only_commands"] = list(commands)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def _declare_ceiling(project: Path, seconds: object) -> None:
    """KYO-312: write ``worker_command_timeout_ceiling_seconds`` into a project's config.

    By hand, and for `_declare`'s reason exactly: the number is the maintainer's
    statement about their own harness, and there is deliberately no command that edits
    it.
    """
    path = project / "kyosei" / "config.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["worker_command_timeout_ceiling_seconds"] = seconds
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def _running(kyo, tmp_path: Path, name: str = "wt") -> tuple[str, Path]:
    """A running dispatch with a declared worktree, in a project git can answer about.

    `kyo dispatch` stamps a base sha, so the project has to be a git repository before
    one exists at all — the same precondition every real dispatch has.
    """
    if not (kyo.project / ".git").exists():
        git_init(kyo.project)
    return _running_dispatch(kyo, tmp_path, name)


def _payload(command: str, cwd: Path | str | None, *, timeout_ms: int | None = None) -> str:
    """A Claude Code ``PreToolUse`` payload for one Bash call.

    KYO-312: ``timeout`` sits inside ``tool_input`` beside the command, in
    MILLIseconds, and it is present only when the call declares one — which is the
    state the ceiling check treats as "under the harness default, nothing asserted,
    nothing to refuse".
    """
    payload: dict = {
        "session_id": "s1",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    if timeout_ms is not None:
        payload["tool_input"]["timeout"] = timeout_ms
    if cwd is not None:
        payload["cwd"] = str(cwd)
    return json.dumps(payload)


def _check(
    project: Path,
    command: str,
    cwd: Path | str | None,
    *,
    as_json: bool = True,
    timeout_ms: int | None = None,
):
    """Run the guard exactly as the packaged hook runs it.

    ``--json`` by default because most of what these tests assert is the structured
    answer (which segments matched, where, and whether that is inside a running
    worktree); pass ``as_json=False`` for the human lines, which are the other half of
    what a hook actually delivers to an agent.
    """
    args = ["--root", str(project), "check-command", "--pre-tool-use"]
    if as_json:
        args.append("--json")
    return invoke(args, stdin=_payload(command, cwd, timeout_ms=timeout_ms))


# ------------------------------------------------------------------ KYO-298.c1


def test_a_project_that_declares_nothing_refuses_nothing(kyo, tmp_path: Path) -> None:
    """The opt-out, and it is the default.

    This is the property every consumer inherits by upgrading: a build that shipped
    this mechanism must not start refusing commands in a project that never asked for
    it. Asserted against the case that would otherwise fire — a declared-suite command
    typed inside a genuinely running dispatch's worktree — so a regression that made
    the key default to something could not pass this by being tested somewhere
    harmless.
    """
    _dispatch_id, tree = _running(kyo, tmp_path)

    result = _check(kyo.project, f'cd "{tree}" && make test', tree)

    assert result.code == 0, result
    assert result.json["declared"] == []
    said = _check(kyo.project, f'cd "{tree}" && make test', tree, as_json=False)
    assert "declares no `orchestrator_only_commands`" in said.out


def test_an_empty_list_is_the_same_decision_as_no_key_at_all(kyo, tmp_path: Path) -> None:
    """c1's "or empty", and it is a deliberate difference from ``worktree_setup``.

    That key refuses an empty list, because there an empty declaration is
    indistinguishable from the absent one it would silently become. Here the two mean
    the same thing — this project opts out — so a maintainer who tried the key, decided
    against it and emptied the list must not be left with a config every `kyo` command
    refuses to load.
    """
    _dispatch_id, tree = _running(kyo, tmp_path)
    _declare(kyo.project)

    assert load_config(kyo.project).controller_only_commands == ()
    result = _check(kyo.project, f'cd "{tree}" && make test', tree)
    assert result.code == 0, result
    assert result.json["declared"] == []


def test_the_key_is_parsed_and_carried_through_a_rebuild(kyo) -> None:
    """Registered like every other config key: read by the loader, survived by a rewrite.

    `kyo rebuild` rewrites config.yml on every run to keep ``schema_version`` honest,
    and KYO-158 is this project's record of an optional key being silently deleted by
    exactly that. Derived from ``OPTIONAL_CONFIG_KEYS`` now, so this asserts the
    derivation reaches the new key rather than that someone remembered to list it.
    """
    _declare(kyo.project, "make test", "npm run check")

    assert kyo("rebuild", "--force").code == 0
    assert load_config(kyo.project).controller_only_commands == ("make test", "npm run check")


@pytest.mark.parametrize(
    "value",
    [
        "make test",  # a bare string: one command, or a line holding several?
        {"run": "make test"},  # a mapping: this key has no mapping form
        ["make test", ""],  # an entry that is not a command
        ["make test", 7],  # an entry that is not a string
        ['make "test'],  # an entry no shell could parse
    ],
    ids=["bare-string", "mapping", "empty-entry", "non-string-entry", "unparseable"],
)
def test_a_malformed_value_is_a_named_config_error(kyo, value) -> None:
    """c1's "a malformed value is a named config error".

    Every shape that could be MISTAKEN for a declaration is refused, because this list
    decides what a blocking hook refuses: a value that parsed to something other than
    what the maintainer wrote would either block a command they never named or quietly
    stop blocking one they did. Each refusal names the key and the file, which is what
    makes it actionable — the fix is always a text editor, and
    ``tests/test_kyo5_error_remedies.py`` records it as such.
    """
    path = kyo.project / "kyosei" / "config.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["controller_only_commands"] = value
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    result = kyo("list")

    assert result.code == EXIT_ERROR, result
    assert "controller_only_commands" in result.err
    assert "config.yml" in result.err


def test_a_fresh_config_documents_the_key_as_a_commented_example(tmp_path: Path) -> None:
    """c1's "documented in the packaged config template as a commented example".

    A consumer cannot opt into a guard they have never heard of. Every other optional
    key is documented by its absence, because absence is what those keys MEAN; this one
    is an offer, and an offer nobody is shown is not an offer. Asserted as: still not
    declared (the file loads with the key absent), and still mentioned.
    """
    root = tmp_path / "fresh"
    assert invoke(["init", str(root), "--in-repo", "--prefix", "FRS"]).code == 0

    text = (root / "kyosei" / "config.yml").read_text(encoding="utf-8")

    assert "controller_only_commands" in text
    assert SUITE_OVERRIDE_ENV in text
    assert yaml.safe_load(text).get("controller_only_commands") is None
    assert load_config(root).controller_only_commands == ()
    # The example is the one in the registry, not a second copy of it that could drift.
    assert CONFIG_KEY_EXAMPLES["controller_only_commands"].splitlines()[-1] in text


# ------------------------------------------------------------------ KYO-298.c2


def test_the_suite_is_refused_inside_a_running_dispatchs_worktree(kyo, tmp_path: Path) -> None:
    """The case the record was filed for, by the route a worker actually takes.

    ``cd "<worktree>" && make test`` — not `make test` with the payload's cwd already
    inside the tree. A worker's Bash cwd starts as the CONTROLLER's checkout, so a
    guard that read only the payload's cwd would have cleared every real occurrence of
    this while looking like it worked.
    """
    dispatch_id, tree = _running(kyo, tmp_path)
    _declare(kyo.project, "make test")

    result = _check(kyo.project, f'cd "{tree}" && make test', kyo.project)

    assert result.code == EXIT_CONTRACT, result
    assert "suite-is-the-orchestrators" in result.err
    assert dispatch_id in result.err
    assert "make test" in result.err


def test_the_payloads_own_cwd_is_the_other_route(kyo, tmp_path: Path) -> None:
    """c2's "by the payload's cwd OR by the command changing into that worktree".

    Both, because a worker that has already `cd`-ed in an earlier Bash call types the
    bare command, and nothing in the command line then says where it is.
    """
    _dispatch_id, tree = _running(kyo, tmp_path)
    _declare(kyo.project, "make test")

    assert _check(kyo.project, "make test", tree).code == EXIT_CONTRACT
    # And a subdirectory of the worktree is still the worktree.
    assert _check(kyo.project, "make test", tree / "src").code == EXIT_CONTRACT


def test_the_same_command_somewhere_else_is_allowed(kyo, tmp_path: Path) -> None:
    """The controller's own checkout is not a worktree, and this is where the suite RUNS.

    A guard that refused `make test` everywhere would refuse the one run anybody
    actually wants: the controller's, once, after the wave merges.
    """
    _running(kyo, tmp_path)
    _declare(kyo.project, "make test")

    result = _check(kyo.project, "make test", kyo.project)

    assert result.code == 0, result
    assert result.json["matched"] is True
    assert result.json["blocks"] is False


def test_a_dispatch_that_has_reported_no_longer_refuses(kyo, tmp_path: Path) -> None:
    """c2's "in the worktree of a dispatch that has reported, is allowed".

    The predicate is the dispatch's STATE, not the directory's existence: a worktree
    outlives the dispatch that declared it (`kyo worktrees --prune` is a separate,
    deliberate act), and a controller who goes into a finished worker's tree to run
    the suite there is doing exactly what this policy asks for.
    """
    dispatch_id, tree = _running(kyo, tmp_path)
    _declare(kyo.project, "make test")
    assert _check(kyo.project, "make test", tree).code == EXIT_CONTRACT

    stored = kyo("show", dispatch_id, "--json").json
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatch_id,
                record_id=stored["record_id"],
                worked_from_sha=stored["worked_from_sha"],
            )
        ),
        encoding="utf-8",
    )
    assert kyo("report", dispatch_id, "--file", str(report)).code == 0, report.read_text()

    result = _check(kyo.project, "make test", tree)

    assert result.code == 0, result
    assert result.json["running_worktrees"] == []


def test_an_undeclared_command_is_never_refused(kyo, tmp_path: Path) -> None:
    """Every Bash call an agent makes reaches this hook.

    A blocking hook that fires on commands it has no business in gets trained away —
    KYO-33 is this project's own record of routine false positives teaching people to
    stop reading a warning — so anything undeclared exits 0 without a word, and without
    opening a database.
    """
    _running(kyo, tmp_path)
    _declare(kyo.project, "make test")

    result = _check(kyo.project, "git status", tmp_path / "wt")

    assert result.code == 0, result
    assert result.json["matched"] is False


@pytest.mark.parametrize(
    "command",
    [
        "make test-data",
        'echo "make test"',
        "grep -rn 'make test' docs/",
        "make testing",
        "npm run checkout",
    ],
)
def test_the_command_and_not_a_substring_of_an_argument(kyo, tmp_path: Path, command) -> None:
    """c2's hardest clause, and the one a naive implementation gets wrong.

    ``make test-data`` differs from `make test` by four characters and a substring test
    matches it; ``echo "make test"`` contains the command exactly and runs nothing.
    Matching is on TOKENS, from position zero after any env prefix — so an argument
    that spells the command is an argument.
    """
    _running(kyo, tmp_path)
    _declare(kyo.project, "make test", "npm run check")

    result = _check(kyo.project, command, tmp_path / "wt")

    assert result.code == 0, result
    assert result.json["matched"] is False, result.json


@pytest.mark.parametrize(
    "command",
    [
        "make test",
        "make test LOG=/tmp/t.log",
        "FOO=1 make test",
        "/usr/bin/make test",
        "cd src && make test",
        "npm run check",
    ],
)
def test_the_shapes_that_do_run_the_suite_still_match(kyo, tmp_path: Path, command) -> None:
    """The other side of the same clause: narrow matching that misses the real thing
    would be a guard that never fires.

    ``make test LOG=/tmp/t.log`` is the spelling this project's own CLAUDE.md
    recommends, and it is a run of the whole suite; so is an env-var prefix, an
    absolute path to `make`, and a `cd` into a subdirectory of the worktree.
    """
    _running(kyo, tmp_path)
    _declare(kyo.project, "make test", "npm run check")

    result = _check(kyo.project, command, tmp_path / "wt")

    assert result.code == EXIT_CONTRACT, result


def test_a_cd_out_of_the_worktree_is_followed_too(kyo, tmp_path: Path) -> None:
    """Segments in ORDER, with a cwd that moves — not a set of directories in play.

    ``cd <worktree> && cd <elsewhere> && make test`` runs the suite outside the
    worktree, and an implementation that merely asked "does any directory mentioned
    here belong to a running dispatch" would refuse it. That is the over-blocking
    direction, which is the one that gets a hook disabled.
    """
    _running(kyo, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    _declare(kyo.project, "make test")

    result = _check(kyo.project, f'cd "{tmp_path / "wt"}" && cd "{outside}" && make test', None)

    assert result.code == 0, result


def test_a_cd_this_parser_cannot_follow_leaves_the_place_unknown(kyo, tmp_path: Path) -> None:
    """The stated hole, asserted so it stays stated.

    ``cd $TREE`` is not followed — no variable is expanded here — and an unknown
    directory never produces a refusal. That is the honest direction for a BLOCKING
    hook (guessing where a command will run is how a hook gets disabled) and it is the
    accepted cost of a guard whose override is one word anyway. What must not happen is
    this becoming true quietly: the answer is reported as ``null``, not as the
    controller's cwd.
    """
    _running(kyo, tmp_path)
    _declare(kyo.project, "make test")

    result = _check(kyo.project, 'cd "$TREE" && make test', None)

    assert result.code == 0, result
    assert result.json["matches"][0]["cwd"] is None


def test_the_refusal_names_the_rule_the_remedy_and_the_override(kyo, tmp_path: Path) -> None:
    """c2 and c3's message requirements, read off the refusal itself."""
    dispatch_id, tree = _running(kyo, tmp_path)
    _declare(kyo.project, "make test")

    err = _check(kyo.project, f'cd "{tree}" && make test', kyo.project).err

    assert "controller_only_commands" in err  # the rule, and where it is declared
    assert "test FILES that cover your change" in err  # the remedy
    assert "after the wave merges" in err  # whose the suite is
    assert f"{SUITE_OVERRIDE_ENV}=1 " in err  # the override, spelled as typed
    assert "disagreements[]" in err  # and what to do about having used it
    assert dispatch_id in err


def test_the_remedy_the_refusal_names_clears_the_refusal(kyo, tmp_path: Path) -> None:
    """c2's "a test proves the remedy clears it", run rather than asserted about.

    The refusal says: run the test files that cover your change instead. So that is
    what happens here — the same worktree, the same running dispatch, the same guard —
    and it comes back clean. This is the check that would fail if the matcher ever
    became broad enough to catch the remedy along with the thing it is a remedy for,
    which is the failure mode that turns a guard into an obstacle with no way round it.
    """
    _dispatch_id, tree = _running(kyo, tmp_path)
    _declare(kyo.project, "make test")
    assert _check(kyo.project, f'cd "{tree}" && make test', kyo.project).code == EXIT_CONTRACT

    remedy = _check(
        kyo.project,
        f'cd "{tree}" && .venv/bin/python -m pytest tests/test_kyo298_command_guard.py',
        kyo.project,
    )

    assert remedy.code == 0, remedy
    assert remedy.json["matched"] is False


def test_a_project_with_no_store_above_it_is_not_a_failed_tool_call(tmp_path: Path) -> None:
    """PROJECT_OPTIONAL, and it is not a nicety.

    This hook fires on every Bash call the agent makes, including in a checkout that
    configured no Kyosei store at all. The honest answer there is "nothing is
    controller-only here", and it must be exit 0 — a failed tool call at the top of
    every command in an unrelated directory is how a consumer removes the hook.
    """
    elsewhere = tmp_path / "not-a-project"
    elsewhere.mkdir()

    result = invoke(
        ["--root", str(elsewhere), "check-command", "--pre-tool-use", "--json"],
        stdin=_payload("make test", elsewhere),
    )

    assert result.code == 0, result
    assert result.json["declared"] == []


def test_a_payload_that_is_not_a_bash_call_is_not_checked(kyo, tmp_path: Path) -> None:
    """The reader is `kyo check-push`'s, and it returns None for anything it cannot read.

    A guard that guessed at an unfamiliar payload shape would block tool calls it
    cannot understand, which on a blocking hook is the failure that matters.
    """
    _running(kyo, tmp_path)
    _declare(kyo.project, "make test")

    result = invoke(
        ["--root", str(kyo.project), "check-command", "--pre-tool-use", "--json"],
        stdin=json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"}}),
    )

    assert result.code == 0, result
    assert result.json["checked"] is None


# ------------------------------------------------------------------ KYO-298.c3


def test_the_override_lets_the_command_run(kyo, tmp_path: Path) -> None:
    """c3: one word, in front of the command, and it is allowed.

    Cheap on purpose. The standing stance every dispatch of this project runs under
    says contradicting a rule must cost LESS than complying with one that is wrong — a
    refusal with no way past it does not buy agreement, it buys a worker who stops
    saying what it thinks.
    """
    _dispatch_id, tree = _running(kyo, tmp_path)
    _declare(kyo.project, "make test")

    command = f'cd "{tree}" && {SUITE_OVERRIDE_ENV}=1 make test'
    result = _check(kyo.project, command, kyo.project)

    assert result.code == 0, result
    assert result.json["overridden"] is True
    assert "disagreements[]" in _check(kyo.project, command, kyo.project, as_json=False).out


def test_the_override_is_read_from_the_command_line_and_not_the_environment(
    kyo, tmp_path: Path, monkeypatch
) -> None:
    """A PREFIX, and that is the mechanism rather than a style choice.

    The hook is a fresh process: an `export` in an earlier Bash call is invisible to
    it. More to the point, a prefix is in the command line the TRANSCRIPT records,
    which is what makes "visible" true rather than claimed — an environment variable
    set somewhere else would be an override nobody reading the dispatch afterwards
    could see.
    """
    _dispatch_id, tree = _running(kyo, tmp_path)
    _declare(kyo.project, "make test")
    monkeypatch.setenv(SUITE_OVERRIDE_ENV, "1")

    assert _check(kyo.project, "make test", tree).code == EXIT_CONTRACT


def test_the_override_set_to_zero_is_not_an_override(kyo, tmp_path: Path) -> None:
    """``KYOSEI_SUITE_OK=0`` reads to a human as "not ok", and is treated that way."""
    _dispatch_id, tree = _running(kyo, tmp_path)
    _declare(kyo.project, "make test")

    assert _check(kyo.project, f"{SUITE_OVERRIDE_ENV}=0 make test", tree).code == EXIT_CONTRACT


# ------------------------------------------------------------------ KYO-298.c5


def test_doctor_notices_a_worktree_setup_entry_that_is_controller_only(kyo) -> None:
    """c5, and it is the contradiction the whole record exists for.

    Not the guard firing at a worker — that is the symptom. This is the config saying
    two things at once, reported to the one person who can change it, naming both
    lines.
    """
    path = kyo.project / "kyosei" / "config.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["worktree_setup"] = ["make venv", "make test"]
    raw["controller_only_commands"] = ["make test"]
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    result = kyo("doctor", "--json")
    check = result.json["checks"]["orchestrator_only_setup"]
    # KYO-519: the old spelling of the key is noticed too, in its own finding; this
    # test is about the contradiction, so it reads the findings that name it.
    findings = [f for f in check["failures"] if "worktree_setup" in f]

    assert findings, result.json
    text = json.dumps(findings)
    assert "worktree_setup" in text and "controller_only_commands" in text
    assert "make test" in text
    # `make venv`, the entry that is NOT controller-only, is not named. One finding,
    # for the one entry that really is both things at once.
    assert len(findings) == 1


def test_that_notice_never_fails_the_command(kyo) -> None:
    """A notice, never a failure, and the tier is what makes that true.

    `kyo doctor` runs from this project's ``Stop`` and ``SessionEnd`` hooks, and Claude
    Code renders any hook exiting nonzero as an error block. Which of the two config
    lines is the wrong one is an editorial judgment about the consumer's own project —
    Kyosei has no standing to call either wrong — so exit 3 would paint every stop red
    over an ambiguity.
    """
    path = kyo.project / "kyosei" / "config.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["worktree_setup"] = ["make test"]
    raw["controller_only_commands"] = ["make test"]
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    result = kyo("doctor")

    assert result.code == 0, result
    assert "orchestrator_only_setup" in doctor.NOTICE_CHECKS
    assert "make test" in result.out


def test_doctor_is_silent_in_a_project_that_declared_nothing(kyo) -> None:
    """The opt-out reaches the notice too: no declaration, nothing to contradict."""
    path = kyo.project / "kyosei" / "config.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["worktree_setup"] = ["make test"]
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    assert kyo("doctor", "--json").json["checks"]["orchestrator_only_setup"]["failures"] == []


def test_the_notice_and_the_guard_read_the_same_matcher(kyo) -> None:
    """Law 3, where it would otherwise cost the most.

    Two surfaces answer "is this command one of the declared ones?" — the hook and the
    doctor notice — and if they answered differently the notice would tell a maintainer
    their config is fine while the guard went on refusing their workers, or the reverse.
    They share :func:`kyosei.cmd_check_command.controller_only_matches`, and this is the test
    that fails if the notice ever grows a copy of it.
    """
    declared = ("make test",)
    assert [m.declared for m in controller_only_matches("make test", declared, cwd=None)] == [
        "make test"
    ]
    assert controller_only_matches("make test-data", declared, cwd=None) == []


# ------------------------------------------------------------------ KYO-298.c7


def test_the_readme_and_the_skill_name_the_config_key(kyo) -> None:
    """c7's mechanical half. The prose itself is checked by reading.

    What a test can hold down is that the two documents a consumer and a controller
    actually read both NAME the key — a mechanism nobody can find is a mechanism nobody
    turns on, and this project's own history (KYO-131) is of a CLI growing a capability
    that no controller-facing prose ever mentioned while every check stayed green.
    """
    root = Path(__file__).resolve().parent.parent
    from tests.reference_pages import OPERATING

    readme = OPERATING.read_text(encoding="utf-8")  # the README section, moved (KYO-516)
    # The skill is read from its packaged source, the one copy this repository keeps
    # since it moved onto the plugin (KYO-610 removed the .claude/skills/kyosei/ mirror).
    skill = (root / "src/kyosei/harness/claude-code/plugin/skills/kyosei/SKILL.md").read_text(
        encoding="utf-8"
    )

    for text in (readme, skill):
        assert "controller_only_commands" in text
        assert "check-command" in text
        assert SUITE_OVERRIDE_ENV in text
    assert "context_rewrites" in readme


def test_the_readme_carries_the_measurement_rather_than_an_assertion(kyo) -> None:
    """c7 asks for "the measurement behind it", and that is not decoration.

    This record asks consumers to opt into a guard on their own workers. The only
    honest basis for that is the number, and a number that lives in a controller's
    transcript rather than in the document is a number nobody can check.
    """
    from tests.reference_pages import OPERATING

    readme = OPERATING.read_text(encoding="utf-8")  # the README section, moved (KYO-516)

    assert "172,733" in readme and "325,752" in readme


# ------------------------------------------------------------------ KYO-312.c2
#
# The timeout ceiling. KYO-298 refuses a command because of WHAT it runs; this refuses
# one because of HOW LONG the worker declared it would wait in the foreground.
#
# The case it was filed for, and it is the one the prose rule lost: KYO-306.d1 had
# "keep every foreground command under about four minutes" in its brief, detached its
# long measurement run exactly as the rule said to, and then sat in ONE foreground wait
# on it -- `until [ -f ...status ]; do sleep 30; done`, Bash timeout 1,800,000 ms -- for
# 10.1 minutes, and later 6.1. Each outlived the five-minute prompt cache and the next
# message re-wrote the whole context: 250,466 and 255,899 tokens, more than every other
# re-write of that wave combined. The rule's own advice was half the reason: it said to
# look ONCE, at about the time the run should be done, which for an agent with no way to
# be idle except a foreground command IS the thing it exists to prevent.
#
# So what is under test is that the declared intent -- the tool call's own `timeout`,
# which the payload carries before the wait starts -- is refused where it costs
# something, that the remedy the refusal names is a wait this same guard allows, and
# that a project which wants none of this (a consumer whose workers hold a one-hour
# cache) gets none of it by saying nothing.

#: The wait that provoked the record: a single foreground loop with a 30-minute timeout.
_ONE_LONG_WAIT = "until [ -f /tmp/run.status ]; do sleep 30; done"
_HALF_HOUR_MS = 1_800_000

#: The remedy the refusal names, spelled as a worker would type one slice of it.
_ONE_SLICE = "sleep 180; cat /tmp/run.status"


def test_an_over_ceiling_wait_is_refused_inside_a_running_dispatchs_worktree(
    kyo, tmp_path: Path
) -> None:
    """The case the record was filed for, at the moment it can still be prevented.

    Both routes into the worktree, as KYO-298 has them: the payload's own cwd, and a
    `cd` in the command line. A worker's Bash cwd usually starts in the CONTROLLER's
    checkout, so a guard that read only the payload's cwd would clear the real shape.
    """
    dispatch_id, tree = _running(kyo, tmp_path)
    _declare_ceiling(kyo.project, 240)

    result = _check(kyo.project, _ONE_LONG_WAIT, tree, timeout_ms=_HALF_HOUR_MS)

    assert result.code == EXIT_CONTRACT, result
    assert "long-command-outlives-the-cache" in result.err
    assert dispatch_id in result.err

    viacd = _check(
        kyo.project,
        f'cd "{tree}" && {_ONE_LONG_WAIT}',
        kyo.project,
        timeout_ms=_HALF_HOUR_MS,
    )
    assert viacd.code == EXIT_CONTRACT, viacd


def test_a_call_that_declares_no_timeout_is_never_refused_for_being_long(
    kyo, tmp_path: Path
) -> None:
    """A call with no declared timeout is under the harness default, and is not refused.

    The guard reads a DECLARATION and does not predict a duration. Asserted against the
    exact command that would otherwise fire, in the exact place it would fire, so this
    cannot pass by being tested somewhere harmless.
    """
    _dispatch_id, tree = _running(kyo, tmp_path)
    _declare_ceiling(kyo.project, 240)

    result = _check(kyo.project, _ONE_LONG_WAIT, tree)

    assert result.code == 0, result
    assert result.json["timeout_ms"] is None
    assert result.json["over_ceiling"] is False


def test_a_timeout_at_or_under_the_ceiling_is_allowed(kyo, tmp_path: Path) -> None:
    """The ceiling is a ceiling, not a target: equal is under it, and a slice is fine."""
    _dispatch_id, tree = _running(kyo, tmp_path)
    _declare_ceiling(kyo.project, 240)

    assert _check(kyo.project, _ONE_SLICE, tree, timeout_ms=240_000).code == 0
    assert _check(kyo.project, _ONE_SLICE, tree, timeout_ms=190_000).code == 0
    assert _check(kyo.project, _ONE_SLICE, tree, timeout_ms=240_001).code == EXIT_CONTRACT


def test_the_same_long_call_outside_a_running_worktree_is_allowed(
    kyo, tmp_path: Path
) -> None:
    """The controller's own checkout is not a worktree, and the suite really does take
    twenty minutes there.

    The predicate is the same one KYO-298 uses, for the same reason: what makes the wait
    expensive is that a dispatched worker has no way to be idle, and the controller's
    trade is a different one.
    """
    _running(kyo, tmp_path)
    _declare_ceiling(kyo.project, 240)

    result = _check(kyo.project, _ONE_LONG_WAIT, kyo.project, timeout_ms=_HALF_HOUR_MS)

    assert result.code == 0, result
    assert result.json["over_ceiling"] is True
    assert result.json["blocks"] is False


def test_the_two_declarations_are_independent_in_both_directions(
    kyo, tmp_path: Path
) -> None:
    """c2's "a project that declares no ``controller_only_commands`` still gets this
    check only if it opts in by one stated config line".

    Both directions, because either coupling would be a project getting a refusal it
    never asked for: a ceiling alone refuses a long wait and no command, and a
    controller-only list alone refuses the suite and no wait, however long.
    """
    _dispatch_id, tree = _running(kyo, tmp_path)

    _declare_ceiling(kyo.project, 240)
    assert _check(kyo.project, "make test", tree, timeout_ms=1000).code == 0
    assert _check(kyo.project, _ONE_LONG_WAIT, tree, timeout_ms=_HALF_HOUR_MS).code == EXIT_CONTRACT

    _declare(kyo.project, "make test")
    _declare_ceiling(kyo.project, None)
    assert _check(kyo.project, _ONE_LONG_WAIT, tree, timeout_ms=_HALF_HOUR_MS).code == 0
    assert _check(kyo.project, "make test", tree, timeout_ms=1000).code == EXIT_CONTRACT


def test_the_suite_refusal_wins_when_both_apply(kyo, tmp_path: Path) -> None:
    """A `make test` with a two-hour timeout is refused as the SUITE.

    Not a nicety about which message prints. The honest thing to tell a worker is that
    the run was not its to make at all — the wait is the consequence, and a refusal that
    named only the wait would invite it to re-run the same suite in slices.
    """
    _dispatch_id, tree = _running(kyo, tmp_path)
    _declare(kyo.project, "make test")
    _declare_ceiling(kyo.project, 240)

    result = _check(kyo.project, "make test", tree, timeout_ms=7_200_000)

    assert result.code == EXIT_CONTRACT, result
    assert "suite-is-the-orchestrators" in result.err
    assert "long-command-outlives-the-cache" not in result.err


def test_the_refusal_names_the_ceiling_the_measurement_the_remedy_and_the_override(
    kyo, tmp_path: Path
) -> None:
    """What a worker is actually handed, read off the refusal itself.

    The measurement is in the list for KYO-298's reason: this refusal asks a worker to do
    something more awkward than what it typed, and the only honest basis for that is the
    number — which, left in a controller's transcript, is a number the worker cannot
    check.
    """
    dispatch_id, tree = _running(kyo, tmp_path)
    _declare_ceiling(kyo.project, 240)

    err = _check(kyo.project, _ONE_LONG_WAIT, tree, timeout_ms=_HALF_HOUR_MS).err

    assert "worker_command_timeout_ceiling_seconds" in err  # the rule, and where it lives
    assert "240s (4.0 min)" in err  # the ceiling it exceeded
    assert "1800s (30.0 min)" in err  # and by how much
    assert "250,466" in err and "255,899" in err  # the measurement behind the rule
    assert "wait in SLICES" in err  # the remedy
    assert f"{LONG_COMMAND_OVERRIDE_ENV}=1 " in err  # the override, spelled as typed
    assert "disagreements[]" in err  # and what to do about having used it
    assert dispatch_id in err


def test_the_sliced_wait_the_refusal_names_clears_the_refusal(kyo, tmp_path: Path) -> None:
    """c2's "a test proving the remedy clears it", run rather than asserted about.

    The refusal says: detach it and wait in slices, each look under the ceiling. So that
    is what happens here — the same worktree, the same running dispatch, the same guard —
    and both halves come back clean. This is the check that would fail if the ceiling ever
    became low enough, or the matching broad enough, to refuse the remedy along with the
    thing it is a remedy for: a guard whose remedy it also refuses is an obstacle with no
    way round it.
    """
    _dispatch_id, tree = _running(kyo, tmp_path)
    _declare_ceiling(kyo.project, 240)
    assert _check(kyo.project, _ONE_LONG_WAIT, tree, timeout_ms=_HALF_HOUR_MS).code == EXIT_CONTRACT

    detach = _check(
        kyo.project,
        "nohup make test > /tmp/run.log 2>&1 & echo started",
        tree,
        timeout_ms=10_000,
    )
    slice_ = _check(kyo.project, _ONE_SLICE, tree, timeout_ms=190_000)

    assert detach.code == 0, detach
    assert slice_.code == 0, slice_


def test_the_override_lets_the_long_wait_run(kyo, tmp_path: Path) -> None:
    """One word, visible in the transcript, and it is meant to be used.

    Two spellings, and the second is not a nicety: a POSIX shell allows no env prefix in
    front of a compound command, and a compound command (`until ...; do sleep 30; done`)
    is the exact shape a long wait takes. An override that could not be typed in front of
    the command it is about would be an override in name only.
    """
    _dispatch_id, tree = _running(kyo, tmp_path)
    _declare_ceiling(kyo.project, 240)

    prefixed = _check(
        kyo.project, f"{LONG_COMMAND_OVERRIDE_ENV}=1 sleep 900", tree, timeout_ms=_HALF_HOUR_MS
    )
    compound = _check(
        kyo.project,
        f"{LONG_COMMAND_OVERRIDE_ENV}=1; {_ONE_LONG_WAIT}",
        tree,
        timeout_ms=_HALF_HOUR_MS,
    )

    assert prefixed.code == 0, prefixed
    assert prefixed.json["overridden"] is True
    assert compound.code == 0, compound
    assert compound.json["overridden"] is True
    said = _check(
        kyo.project,
        f"{LONG_COMMAND_OVERRIDE_ENV}=1 sleep 900",
        tree,
        as_json=False,
        timeout_ms=_HALF_HOUR_MS,
    )
    assert "disagreements[]" in said.out


def test_the_long_override_set_to_zero_is_not_an_override(kyo, tmp_path: Path) -> None:
    """``KYOSEI_LONG_OK=0`` reads to a human as "not ok", and is treated that way —
    its sibling `KYOSEI_SUITE_OK=0`'s rule, in the one other place a worker meets one."""
    _dispatch_id, tree = _running(kyo, tmp_path)
    _declare_ceiling(kyo.project, 240)

    assert (
        _check(
            kyo.project,
            f"{LONG_COMMAND_OVERRIDE_ENV}=0 sleep 900",
            tree,
            timeout_ms=_HALF_HOUR_MS,
        ).code
        == EXIT_CONTRACT
    )


def test_the_suite_override_does_not_clear_a_long_wait(kyo, tmp_path: Path) -> None:
    """Two refusals, two words, and neither answers the other.

    A worker that said "this suite run is mine deliberately" has said nothing about
    waiting half an hour in the foreground, and a controller reading `disagreements[]`
    needs to be able to tell the two claims apart. One name for both would have made that
    impossible at the moment it is cheapest to keep separate.
    """
    _dispatch_id, tree = _running(kyo, tmp_path)
    _declare_ceiling(kyo.project, 240)

    result = _check(
        kyo.project, f"{SUITE_OVERRIDE_ENV}=1 sleep 900", tree, timeout_ms=_HALF_HOUR_MS
    )

    assert result.code == EXIT_CONTRACT, result


def test_a_dispatch_that_has_reported_no_longer_refuses_a_long_wait(
    kyo, tmp_path: Path
) -> None:
    """The predicate is the dispatch's STATE, exactly as it is for the suite.

    A worktree outlives the dispatch that declared it, and a controller standing in a
    finished worker's tree waiting on a long run is doing nothing this record is about.
    """
    dispatch_id, tree = _running(kyo, tmp_path)
    _declare_ceiling(kyo.project, 240)
    assert _check(kyo.project, _ONE_LONG_WAIT, tree, timeout_ms=_HALF_HOUR_MS).code == EXIT_CONTRACT

    stored = kyo("show", dispatch_id, "--json").json
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatch_id,
                record_id=stored["record_id"],
                worked_from_sha=stored["worked_from_sha"],
            )
        ),
        encoding="utf-8",
    )
    assert kyo("report", dispatch_id, "--file", str(report)).code == 0, report.read_text()

    assert _check(kyo.project, _ONE_LONG_WAIT, tree, timeout_ms=_HALF_HOUR_MS).code == 0


def test_a_cd_out_of_the_worktree_does_not_clear_a_long_wait(kyo, tmp_path: Path) -> None:
    """The deliberate difference from the suite matcher, asserted so it stays deliberate.

    `cd <worktree> && cd <elsewhere> && make test` runs the SUITE outside the worktree and
    is allowed — the contention it would cause is about where the command runs. A timeout
    belongs to the whole tool call, and the cache it burns is the worker's wherever its
    shell is standing, so a worker that starts inside its worktree and steps out is still
    the worker this refusal is about.
    """
    _dispatch_id, tree = _running(kyo, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    _declare(kyo.project, "make test")
    _declare_ceiling(kyo.project, 240)

    command = f'cd "{outside}" && {_ONE_LONG_WAIT}'

    assert _check(kyo.project, f'cd "{outside}" && make test', tree).code == 0
    assert _check(kyo.project, command, tree, timeout_ms=_HALF_HOUR_MS).code == EXIT_CONTRACT
    # ...and a call that neither starts in nor enters a worktree is still allowed.
    assert _check(kyo.project, command, outside, timeout_ms=_HALF_HOUR_MS).code == 0


def test_the_ceiling_key_is_parsed_and_carried_through_a_rebuild(kyo) -> None:
    """Registered like every other config key: read by the loader, survived by a rewrite.

    `kyo rebuild` rewrites config.yml on every run to keep ``schema_version`` honest, and
    KYO-158 is this project's record of an optional key being silently deleted by exactly
    that. Derived from ``OPTIONAL_CONFIG_KEYS``, so this asserts the derivation reaches the
    new key rather than that someone remembered to list it.
    """
    _declare_ceiling(kyo.project, 240)

    assert kyo("rebuild", "--force").code == 0
    assert load_config(kyo.project).worker_command_timeout_ceiling_seconds == 240


@pytest.mark.parametrize(
    "value",
    [
        "240",  # seconds as a string
        "4m",  # the spelling a person is most likely to reach for
        240.0,  # a float, which is a number this key cannot mean
        True,  # a bool, which Python would otherwise read as a one-second ceiling
        0,  # a ceiling that refuses every timed command in the worktree
        -1,
    ],
    ids=["string", "duration", "float", "bool", "zero", "negative"],
)
def test_a_malformed_ceiling_is_a_named_config_error(kyo, value) -> None:
    """Every shape that could be MISTAKEN for a ceiling is refused, naming the key and
    the file.

    ``True`` is the one worth spelling out: Python's ``bool`` is an ``int``, so a
    ``worker_command_timeout_ceiling_seconds: true`` that was merely range-checked would
    parse to a ONE-SECOND ceiling and refuse very nearly every command a worker types —
    a blocking hook firing on everything, which is how a hook gets removed (KYO-33).
    """
    _declare_ceiling(kyo.project, value)

    result = kyo("list")

    assert result.code == EXIT_ERROR, result
    assert "worker_command_timeout_ceiling_seconds" in result.err
    assert "config.yml" in result.err


def test_the_ceiling_refusal_remedy_actually_loads(kyo) -> None:
    """KYO-5.c2 for the two config refusals above: the remedy each one names clears it.

    Both are a text editor — write whole seconds, or delete the line — and both endings
    are legitimate, which is exactly what no command could decide on a maintainer's
    behalf.
    """
    _declare_ceiling(kyo.project, "4m")
    assert kyo("list").code == EXIT_ERROR

    _declare_ceiling(kyo.project, 240)
    assert kyo("list").code == 0
    assert load_config(kyo.project).worker_command_timeout_ceiling_seconds == 240

    path = kyo.project / "kyosei" / "config.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    del raw["worker_command_timeout_ceiling_seconds"]
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert kyo("list").code == 0
    assert load_config(kyo.project).worker_command_timeout_ceiling_seconds is None


def test_a_fresh_config_documents_the_ceiling_as_a_commented_example(tmp_path: Path) -> None:
    """A consumer cannot opt into a guard they have never heard of.

    ``controller_only_commands``'s reasoning, and one step further: that key at least
    names commands a maintainer already thinks about, where this one is a number nobody
    would think to look for. Asserted as: still not declared (the file loads with the key
    absent, so upgrading refuses nothing), and still mentioned.
    """
    root = tmp_path / "fresh-ceiling"
    assert invoke(["init", str(root), "--in-repo", "--prefix", "FRC"]).code == 0

    text = (root / "kyosei" / "config.yml").read_text(encoding="utf-8")

    assert "worker_command_timeout_ceiling_seconds" in text
    assert LONG_COMMAND_OVERRIDE_ENV in text
    assert yaml.safe_load(text).get("worker_command_timeout_ceiling_seconds") is None
    assert load_config(root).worker_command_timeout_ceiling_seconds is None
    # The example is the one in the registry, not a second copy of it that could drift.
    assert CONFIG_KEY_EXAMPLES["worker_command_timeout_ceiling_seconds"].splitlines()[-1] in text


def test_a_project_that_declares_neither_key_opens_no_database(
    kyo, tmp_path: Path, monkeypatch
) -> None:
    """The opt-out reaches the new check, and so does the cost argument behind it.

    A half-hour wait inside a real running worktree, in a project that declared nothing:
    exit 0, and the store is never opened. A guard that opened a database on every
    command an agent typed would be a cost every consumer pays for a feature most of them
    have not turned on.
    """
    _dispatch_id, tree = _running(kyo, tmp_path)
    opened: list[str] = []
    from kyosei import commands as commands_module

    original = commands_module.Context.store

    def spy(self):
        opened.append(str(self.root))
        return original(self)

    monkeypatch.setattr(commands_module.Context, "store", spy)

    result = _check(kyo.project, _ONE_LONG_WAIT, tree, timeout_ms=_HALF_HOUR_MS)

    assert result.code == 0, result
    assert opened == []


# -------------------------------------------------- the cost of being on every call


def test_an_undeclared_project_opens_no_database(kyo, tmp_path: Path, monkeypatch) -> None:
    """The hook runs before EVERY Bash call, so the undeclared path has to be cheap.

    Asserted structurally rather than by timing: the store is never opened. A guard
    that opened a database on every command an agent typed would be a cost every
    consumer pays for a feature most of them have not turned on.
    """
    opened: list[str] = []
    real = type(kyo).__mro__  # noqa: F841 - documents that `kyo` is a plain function
    from kyosei import commands as commands_module

    original = commands_module.Context.store

    def spy(self):
        opened.append(str(self.root))
        return original(self)

    monkeypatch.setattr(commands_module.Context, "store", spy)

    assert _check(kyo.project, "make test", kyo.project).code == 0
    assert opened == []


def test_a_declared_match_does_open_it(kyo, tmp_path: Path, monkeypatch) -> None:
    """...and the other side, so the test above cannot pass by the guard doing nothing."""
    _running(kyo, tmp_path)
    _declare(kyo.project, "make test")
    opened: list[str] = []
    from kyosei import commands as commands_module

    original = commands_module.Context.store

    def spy(self):
        opened.append(str(self.root))
        return original(self)

    monkeypatch.setattr(commands_module.Context, "store", spy)

    _check(kyo.project, "make test", kyo.project)

    assert opened


def test_a_record_exists_for_this_work(kyo) -> None:
    """A smoke test that the fixture project still works the way these tests assume."""
    assert new_record(kyo, criteria=["a"], title="a record")


# ------------------------------------------------------------------- KYO-409
# The suite guard reads the shared shell parse (kyosei.shell_words): wrappers in front
# of the command word, a single `&`, and make's own options no longer hide the suite.


@pytest.mark.parametrize(
    "form",
    [
        "nohup make test > /tmp/r.log 2>&1 &",
        "time make test",
        "env make test",
        "command make test",
        "timeout 900 make test",
        "make -j4 test",
        "make -j 4 test",
        "nice -n 5 make test",
        "sleep 1 & make test",
        "(make test)",
    ],
)
def test_kyo409_wrapped_suite_runs_are_the_suite(tmp_path: Path, form: str) -> None:
    """KYO-409.c4, on the matcher itself, after a ``cd`` into the would-be worktree.
    Each form fails if kyosei.shell_words stops stepping over wrappers (or splitting on
    a single ``&``), or if make's targets stop being read past its options."""
    tree = tmp_path / "wt"
    tree.mkdir()
    matches = controller_only_matches(f'cd "{tree}" && {form}', ("make test",), cwd=Path("/"))
    assert len(matches) == 1, (form, matches)
    assert matches[0].cwd == tree.resolve()


@pytest.mark.parametrize(
    "form",
    ["make test-data", 'echo "make test"', "command -v make test", "make -j4 test-data"],
)
def test_kyo409_what_still_is_not_the_suite(tmp_path: Path, form: str) -> None:
    """The narrow side, held while the match widened."""
    matches = controller_only_matches(f'cd "{tmp_path}" && {form}', ("make test",), cwd=None)
    assert matches == [], (form, matches)


def test_kyo409_the_wrapped_forms_are_refused_inside_a_running_worktree(
    kyo, tmp_path: Path
) -> None:
    """KYO-409.c4 end to end: the hook, a running dispatch, a ``cd`` into its worktree."""
    _dispatch_id, tree = _running(kyo, tmp_path)
    _declare(kyo.project, "make test")
    for form in (
        "time make test",
        "env make test",
        "command make test",
        "timeout 900 make test",
        "make -j4 test",
    ):
        result = _check(kyo.project, f'cd "{tree}" && {form}', kyo.project)
        assert result.code == EXIT_CONTRACT, (form, result)


def test_kyo409_the_timeout_remedys_detached_form_is_refused_and_the_override_clears_it(
    kyo, tmp_path: Path
) -> None:
    """KYO-409.c5. The KYO-312 timeout refusal tells a worker to detach a long run as
    ``nohup <command> > /tmp/run.log 2>&1 &``. Taken from that refusal's own text and
    filled in with the suite, it used to walk straight past the suite guard; it is now
    refused inside a running worktree, and ``KYOSEI_SUITE_OK=1`` in front of it — the
    override the suite refusal names — still clears the refusal."""
    import re

    from kyosei.cmd_check_command import RunningWorktree, timeout_refusal_lines

    dispatch_id, tree = _running(kyo, tmp_path)
    _declare(kyo.project, "make test")
    remedy_text = "\n".join(
        timeout_refusal_lines(
            "x",
            timeout_ms=1_000_000,
            ceiling_seconds=240,
            place=tree,
            worktree=RunningWorktree(dispatch_id, "TST-1", str(tree), tree),
        )
    )
    detached = re.search(r"`(nohup <command>[^`]*)`", remedy_text)
    assert detached, "the timeout remedy no longer spells a nohup form"
    form = detached.group(1).replace("<command>", "make test")

    refused = _check(kyo.project, f'cd "{tree}" && {form}', kyo.project)
    assert refused.code == EXIT_CONTRACT, (form, refused)
    assert f"{SUITE_OVERRIDE_ENV}=1 " in refused.err

    cleared = _check(kyo.project, f'cd "{tree}" && {SUITE_OVERRIDE_ENV}=1 {form}', kyo.project)
    assert cleared.code == 0, (form, cleared)
    assert cleared.json["matches"][0]["overridden"] is True
