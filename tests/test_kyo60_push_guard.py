"""KYO-60 — nothing warned before a push published everything ever written.

The hazard: a project that keeps its store in the code repository publishes, on
its first push, every idea file, record description, note, shift note, evidence
artifact and frozen brief it has ever held — and ``kyo redact`` explicitly cannot
reach committed history, so there is no remedy afterwards. The design is
asymmetric on purpose (an agent asking to push has no standing to publish the
maintainer's private thinking; a human pushing their own repository owns that
call), fires once rather than every time, and states numbers it measured rather
than a description of what a store generally contains.

Each section below is one criterion.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from kyosei import install_hooks, install_prose
from kyosei.cmd_publish import (
    STATE_IN_REPO,
    STATE_PREDATES_LAYOUT_CONFIG,
    STATE_SPLIT,
    STATE_SPLIT_WITH_STORE_STILL_HERE,
    SURFACE_COUNTERS,
    command_in_pre_tool_use_payload,
    publishing_commands,
)
from kyosei.config import STORE_LINK_NAME
from kyosei.errors import EXIT_CONTRACT, EXIT_OK

from .conftest import Run, git_init, invoke, new_record

PAYLOAD = '{{"tool_name": "Bash", "tool_input": {{"command": {command}}}}}'


def _payload(command: str) -> str:
    return PAYLOAD.format(command=json.dumps(command))


def _in(root: Path):
    def run(*args: str, stdin: str | None = None) -> Run:
        return invoke(["--root", str(root), *args], stdin=stdin)

    return run


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@e.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _config(root: Path) -> dict:
    return yaml.safe_load((root / "kyosei" / "config.yml").read_text(encoding="utf-8"))


def _predating_project(tmp_path: Path) -> Path:
    """An in-repo project in the state every project initialised before KYO-54 is
    in: a store inside the code repository, and a ``config.yml`` that carries no
    ``layout_at_init`` key because the key did not exist when it was written.

    Produced by deleting the key rather than by hand-writing a config, so it stays
    a real `kyo init` product in every other respect — and so this fixture keeps
    describing the same state if the rest of the file grows.
    """
    root = tmp_path / "legacy"
    assert invoke(["init", str(root), "--in-repo", "--prefix", "TST"]).code == 0
    path = root / "kyosei" / "config.yml"
    text = "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.startswith(("layout_at_init:", "# layout_at_init:"))
    )
    path.write_text(text + "\n", encoding="utf-8")
    assert "layout_at_init" not in _config(root)
    git_init(root)
    return root


# --------------------------------------------------------------------- KYO-60.c1
# A packaged PreToolUse hook blocks an agent's `git push`, install-hooks
# distributes it, and doctor's hook_config check reports its absence.


def test_a_packaged_pre_tool_use_hook_blocks_an_agents_push(kyo, project: Path) -> None:
    git_init(project)
    blocked = _in(project)(
        "check-push", "--pre-tool-use", stdin=_payload("git push -u origin main")
    )
    assert blocked.code == EXIT_CONTRACT, blocked
    assert "push-publishes-the-store" in blocked.err
    assert "would publish this project's Kyosei store" in blocked.err


def test_install_hooks_distributes_the_pre_tool_use_hook(kyo, project: Path) -> None:
    assert kyo("install-hooks", "--copies").code == 0
    settings = json.loads((project / ".claude" / "settings.json").read_text(encoding="utf-8"))
    groups = settings["hooks"]["PreToolUse"]
    # Every group under this event matches Bash, and this one is here. KYO-298 added a
    # SECOND group to the same packaged fragment (`kyo check-command --pre-tool-use`),
    # so what this asserts is that check-push is wired, not that it is alone — see
    # tests/test_kyo298_command_guard.py for why the second one is its own group rather
    # than a second entry in this one.
    assert {group["matcher"] for group in groups} == {"Bash"}
    assert "kyo check-push --pre-tool-use" in json.dumps(groups)


def test_doctor_reports_the_hook_missing_from_an_older_settings_file(
    kyo, project: Path
) -> None:
    """The upgrade path, which is the only one that matters: hooks are a *copy*
    and never update themselves, so a project that installed hooks before this
    release has settings that will never grow this one unless something says so."""
    assert kyo("install-hooks", "--copies").code == 0
    path = project / ".claude" / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    del settings["hooks"]["PreToolUse"]
    path.write_text(json.dumps(settings), encoding="utf-8")

    result = kyo("doctor", "--json")
    assert result.code != 0
    failures = json.dumps(result.json["checks"]["hook_config"])
    assert "PreToolUse" in failures and "kyo check-push --pre-tool-use" in failures
    assert "kyo install-hooks" in failures

    assert kyo("install-hooks", "--copies").code == 0
    assert kyo("doctor", "--json").json["checks"]["hook_config"]["ok"]


def test_a_bash_call_that_is_not_a_push_is_not_blocked(kyo, project: Path) -> None:
    """Every Bash call the agent makes reaches this hook. If it were not silent on
    the ones that publish nothing it would be a blocking hook that cries wolf,
    which is KYO-33's failure with a worse exit code."""
    for command in ("ls -la", "git status", "git commit -m 'x'", "pytest -q", "grep push ."):
        result = _in(project)("check-push", "--pre-tool-use", stdin=_payload(command))
        assert result.code == EXIT_OK, (command, result)
        assert "nothing checked" in result.out


@pytest.mark.parametrize(
    "command",
    [
        "git push",
        "git push -u origin main",
        "git push --force-with-lease origin kyo-60",
        "git -C /some/dir push",
        "git -c user.name=T push origin main",
        "cd /tmp && git push",
        "git add -A; git push",
        "gh pr create --fill",
        "gh repo create mine --public --source=.",
    ],
)
def test_the_commands_this_build_calls_a_push(command: str) -> None:
    assert publishing_commands(command), command


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git log --oneline",
        "git commit -m 'push the button'",
        "gh pr list",
        "gh pr view 12",
        "pushd /tmp",
        "make release",
        "./scripts/deploy.sh",
    ],
)
def test_the_commands_this_build_deliberately_does_not_match(command: str) -> None:
    """The narrow side of the decision, including its misses.

    ``make release`` and a deploy script that pushes three layers down are real
    publications this cannot see, and that is the accepted cost: on a *blocking*
    hook, matching broadly is the dangerous direction, and a blocking hook that
    fires on commands publishing nothing is trained away (KYO-33's failure with a
    worse exit code). The git ``pre-push`` hook is what stands behind this gap —
    it sees every push git performs, however it was invoked.
    """
    assert publishing_commands(command) == [], command


def test_where_the_shallow_parse_actually_lands() -> None:
    """Stated rather than hidden. The parse splits on shell operators and reads the
    command word, which decides both ends of its accuracy: a `git push` mentioned
    inside another command is correctly not a push, and a `git push` inside a shell
    construct this does not understand is missed. Recorded here so a change that
    makes it smarter changes this test deliberately rather than by accident.
    """
    assert publishing_commands("echo 'run git push later'") == []
    assert publishing_commands("echo git push") == []
    # A miss: `do` is the command word of that segment, so this reads as not a push.
    assert publishing_commands("for x in 1; do git push; done") == []


def test_a_payload_that_is_not_a_bash_call_is_ignored() -> None:
    assert command_in_pre_tool_use_payload('{"tool_name": "Read"}') is None
    assert command_in_pre_tool_use_payload("not json at all") is None
    assert command_in_pre_tool_use_payload("") is None
    assert (
        command_in_pre_tool_use_payload(_payload("git push")) == "git push"
    )


# --------------------------------------------------------------------- KYO-60.c2
# The counts are measured, not described.


def test_every_declared_prose_surface_is_counted() -> None:
    """The derivation, not the numbers. A surface added to
    ``COMMITTED_PROSE_SURFACES`` with no counter would be named in the warning and
    silently reported as zero, which is worse than not naming it (law 3)."""
    declared = {surface.key for surface in install_prose.COMMITTED_PROSE_SURFACES}
    assert set(SURFACE_COUNTERS) == declared


def test_the_numbers_come_from_the_store_and_move_when_it_does(kyo, project: Path) -> None:
    git_init(project)
    check = _in(project)

    before = check("check-push", "--json").json["counts"]
    assert before["records"] == 0

    first = new_record(kyo, title="a record with a private description")
    kyo("edit", first, "--note-file", "-", stdin="a private thought\n")
    (project / "ideas" / "one.md").write_text("# unfiltered\n", encoding="utf-8")
    (project / "ideas" / "two.md").write_text("# also unfiltered\n", encoding="utf-8")
    kyo("note", "--shift", "--file", "-", stdin="what I suspect is going wrong\n")

    after = check("check-push", "--json").json["counts"]
    assert after["records"] == 1
    # scratchpad.md is counted here even though `kyo ideas review` excludes it:
    # unreviewed intake is the most exposed prose in the project, not the least.
    assert after["ideas"] == before["ideas"] + 2
    assert after["shift_notes"] == before["shift_notes"] + 1
    assert after["events"] > before["events"]

    blocked = check("check-push")
    assert blocked.code == EXIT_CONTRACT
    assert "1 record" in blocked.err
    assert f"{after['ideas']:,} idea files" in blocked.err
    assert "1 shift note" in blocked.err
    assert f"{after['events']:,} events" in blocked.err


def test_the_message_is_not_fixed_text(kyo, project: Path) -> None:
    """The mechanism this test would fail if removed: two projects with different
    contents must produce different messages. A hand-written paragraph about "your
    ideas and records" would pass every assertion above and this one is what it
    could not pass."""
    git_init(project)
    first = _in(project)("check-push").err
    for index in range(3):
        (project / "ideas" / f"idea-{index}.md").write_text("# x\n", encoding="utf-8")
    second = _in(project)("check-push").err
    assert first != second


# --------------------------------------------------------------------- KYO-60.c3
# It fires once. Acknowledging records who and when.


def test_acknowledging_records_who_and_when_and_silences_the_next_push(
    kyo, project: Path
) -> None:
    git_init(project)
    check = _in(project)

    first = check("check-push", "--pre-tool-use", stdin=_payload("git push"))
    assert first.code == EXIT_CONTRACT, "an unacknowledged push must not be silent"

    acknowledged = invoke(
        ["--root", str(project), "--actor", "the-maintainer", "check-push", "--acknowledge",
         "--json"]
    )
    assert acknowledged.code == EXIT_OK, acknowledged
    stamp = acknowledged.json["acknowledged"]
    assert stamp["actor"] == "the-maintainer"
    assert stamp["at"].endswith("Z")

    stored = _config(project)["publication_acknowledged"]
    assert stored == stamp, "the acknowledgement lives in config.yml, per CONFIG_CONSUMERS"

    second = check("check-push", "--pre-tool-use", stdin=_payload("git push"))
    assert second.code == EXIT_OK, second
    assert "already acknowledged by the-maintainer" in second.out


def test_the_acknowledgement_survives_a_rebuild(kyo, project: Path) -> None:
    """`kyo rebuild` rewrites config.yml to keep ``schema_version`` in step with
    the database (KYO-5.c1) and passes only the required keys. If that rewrite
    dropped the acknowledgement, "fires once" would hold only until the next
    rebuild — and rebuilds are routine."""
    git_init(project)
    assert _in(project)("check-push", "--acknowledge").code == EXIT_OK
    (project / "kyosei" / "kyosei.db").unlink()
    assert _in(project)("rebuild").code == 0
    assert _config(project)["publication_acknowledged"]["actor"]
    assert _in(project)("check-push").code == EXIT_OK


def test_acknowledging_does_not_quietly_bump_the_schema_version(kyo, project: Path) -> None:
    """Acknowledging a publication says nothing about the store's schema. Writing
    it through `write_config` would stamp the build's ``SCHEMA_VERSION`` and erase
    exactly the db/config drift KYO-5's errors exist to report — a fix for one
    thing silencing the alarm for another."""
    git_init(project)
    path = project / "kyosei" / "config.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    stale = int(raw["schema_version"]) - 1
    raw["schema_version"] = stale
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    assert _in(project)("check-push", "--acknowledge").code == EXIT_OK
    after = _config(project)
    assert after["schema_version"] == stale, "the drift must survive being acknowledged past"
    assert after["publication_acknowledged"]["actor"]


def test_doctor_accepts_a_config_with_and_without_the_optional_keys(
    kyo, project: Path, tmp_path: Path
) -> None:
    """Both keys are optional, and `kyo doctor`'s law-2 check has to agree — a
    project that predates them is not broken, it is just older."""
    assert kyo("doctor").code == 0
    assert _in(project)("check-push", "--acknowledge").code == EXIT_OK
    assert kyo("doctor").code == 0

    legacy = _predating_project(tmp_path)
    assert _in(legacy)("doctor").code == 0


# --------------------------------------------------------------------- KYO-60.c4
# The remedy the message names actually clears it.


def test_the_migration_the_warning_names_clears_the_warning(tmp_path: Path) -> None:
    """This project's rule: an error naming a remedy has a test running that remedy
    and proving it clears the error. The remedy here is KYO-54's documented
    migration — there is no `kyo migrate` verb and this must not pretend there is.
    """
    code = tmp_path / "proj"
    assert invoke(["init", str(code), "--in-repo", "--prefix", "TST"]).code == 0
    git_init(code)
    kyo = _in(code)
    record = kyo("new", "bug", "--title", "predates the move", "--json").json["record"]["id"]
    kyo("edit", record, "--note-file", "-", stdin="a private thought\n")
    (code / "ideas" / "thought.md").write_text("# unfiltered\n", encoding="utf-8")
    _git(code, "add", "-A")
    _git(code, "commit", "-q", "-m", "the store, committed")

    blocked = kyo("check-push")
    assert blocked.code == EXIT_CONTRACT
    assert "Moving an existing store out of the code repository" in blocked.err

    # The remedy, exactly as the message states it.
    store = tmp_path / "kyosei-home" / "proj-moved"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.mkdir()
    for layer in ("kyosei", "ideas"):
        (code / layer).rename(store / layer)
    _git(code, "add", "-A")
    _git(code, "commit", "-q", "-m", "move the Kyosei store out of this repository")
    _git(store, "init", "-q", "-b", "main")
    _git(store, "add", "-A")
    _git(store, "commit", "-q", "-m", "Kyosei store")
    assert invoke(["init", str(code), "--store", str(store), "--json"]).code == 0

    cleared = _in(code)("check-push", "--pre-tool-use", stdin=_payload("git push"))
    assert cleared.code == EXIT_OK, cleared
    # And nothing was acknowledged to get there: the hazard is gone, not accepted.
    assert _config(store).get("publication_acknowledged") is None


def test_the_readme_still_documents_the_migration_the_warning_names() -> None:
    """The remedy is prose, so the prose has to exist. Named rather than trusted:
    a message pointing at a section nobody wrote is the same defect as one naming
    a flag nobody shipped."""
    from tests.reference_pages import STORE

    readme = STORE.read_text(encoding="utf-8")  # the README section, moved (KYO-516)
    assert "### Moving an existing store out of the code repository" in readme


# --------------------------------------------------------------------- KYO-60.c5
# Split is silent; split-with-a-store-still-here is not.


def test_a_split_project_is_never_warned(split_project) -> None:
    code, store = split_project
    result = _in(code)("check-push", "--pre-tool-use", stdin=_payload("git push"))
    assert result.code == EXIT_OK, result
    assert (code / STORE_LINK_NAME).is_file()
    assert _in(code)("check-push", "--json").json["state"] == STATE_SPLIT


def test_a_split_project_that_still_commits_a_store_here_is_warned(
    split_project, tmp_path: Path
) -> None:
    """The case that needs this most, and the one the split default does not cover:
    the store was moved out but the old copy was never removed from the code
    repository, so a push still publishes it. The live store is elsewhere; the
    thing being published is the residue, and that is what gets counted."""
    code, store = split_project
    (code / "ideas").mkdir()
    (code / "ideas" / "left-behind.md").write_text("# still here\n", encoding="utf-8")
    (code / "kyosei" / "export").mkdir(parents=True)
    (code / "kyosei" / "export" / "_shift_notes.jsonl").write_text(
        '{"text": "what I suspect"}\n', encoding="utf-8"
    )
    _git(code, "add", "-A")
    _git(code, "commit", "-q", "-m", "the copy nobody removed")

    result = _in(code)("check-push", "--json")
    assert result.code == EXIT_CONTRACT
    assert result.json["state"] == STATE_SPLIT_WITH_STORE_STILL_HERE
    assert result.json["measured_in"] == str(code)
    assert result.json["counts"]["ideas"] == 1
    assert result.json["counts"]["shift_notes"] == 1
    assert any("ideas/left-behind.md" in path for path in result.json["residue"])


def test_an_untracked_leftover_directory_is_not_a_publication(split_project) -> None:
    """What a push publishes is what git *tracks*. An untracked directory beside
    the code is not about to become public, and blocking on it would be the
    blocking hook firing on a non-event."""
    code, store = split_project
    (code / "ideas").mkdir()
    (code / "ideas" / "untracked.md").write_text("# not committed\n", encoding="utf-8")
    result = _in(code)("check-push", "--json")
    assert result.code == EXIT_OK, result
    assert result.json["state"] == STATE_SPLIT


# --------------------------------------------------------------------- KYO-60.c6
# A project that predates the layout config is treated as in-repo, and says so.


def test_a_project_that_predates_the_layout_config_is_warned(tmp_path: Path) -> None:
    """This repository's own state at the time this was written: a store inside the
    code repository and a ``config.yml`` with no ``layout_at_init`` key, because
    the key did not exist when it was written. Treating "unconfigured" as
    "not in-repo" would silently exempt every project initialised before KYO-54 —
    which is all of them, and they are the ones with the most prose to lose."""
    legacy = _predating_project(tmp_path)
    result = _in(legacy)("check-push", "--json")
    assert result.code == EXIT_CONTRACT, result
    assert result.json["state"] == STATE_PREDATES_LAYOUT_CONFIG
    assert "predates the layout config" in result.err
    assert "no one ever chose this" in result.err


def test_a_project_that_chose_in_repo_explicitly_says_so_instead(
    kyo, project: Path
) -> None:
    """Separately from the case above, because they are different situations that
    happen to publish the same files: one consumer weighed the choice and took it,
    the other never saw a choice. Telling them apart is the whole reason
    ``layout_at_init`` is stored."""
    git_init(project)
    assert _config(project)["layout_at_init"] == "in-repo"
    result = _in(project)("check-push", "--json")
    assert result.code == EXIT_CONTRACT, result
    assert result.json["state"] == STATE_IN_REPO
    assert "chose the in-repo layout at `kyo init`" in result.err
    assert "predates" not in result.err


def test_a_repository_with_no_kyosei_project_at_all_is_silent(tmp_path: Path) -> None:
    """`PreToolUse` fires on every Bash call in every repository the agent works
    in, most of which have no store. Failing there would make `kyo` unusable as a
    hook rather than protective."""
    bare = tmp_path / "unrelated"
    bare.mkdir()
    (bare / "main.py").write_text("print('hi')\n", encoding="utf-8")
    git_init(bare)
    result = invoke(["--root", str(bare), "check-push", "--pre-tool-use"],
                    stdin=_payload("git push"))
    assert result.code == EXIT_OK, result


# ------------------------------------------------- the human half: git pre-push


def test_install_hooks_writes_an_advisory_pre_push_hook(kyo, project: Path) -> None:
    git_init(project)
    assert kyo("install-hooks", "--copies").code == 0
    hook = project / ".git" / "hooks" / "pre-push"
    assert hook.is_file()
    text = hook.read_text(encoding="utf-8")
    assert "kyo check-push --advisory" in text
    assert install_hooks.PRE_PUSH_MARKER in text
    assert hook.stat().st_mode & 0o111, "a git hook that is not executable never runs"
    # The limitation, stated in the artifact itself rather than only in a doc:
    # .git/hooks is not versioned, so a fresh clone does not have this.
    assert "not versioned" in text


def test_the_advisory_mode_warns_without_blocking(kyo, project: Path) -> None:
    """The asymmetry, which is the design: a human pushing their own repository
    owns that decision, so this half tells them and gets out of the way."""
    git_init(project)
    result = _in(project)("check-push", "--advisory")
    assert result.code == EXIT_OK, result
    assert "does not block your push" in result.out
    assert "would publish this project's Kyosei store" in result.out


def test_a_consumers_own_pre_push_hook_is_never_overwritten(kyo, project: Path) -> None:
    git_init(project)
    hook = project / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    result = kyo("install-hooks", "--copies", "--json")
    assert result.code == 0
    assert hook.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"
    assert any("left alone" in note for note in result.json["notes"])


def test_install_hooks_stays_idempotent_with_the_pre_push_hook(kyo, project: Path) -> None:
    git_init(project)
    assert kyo("install-hooks", "--copies", "--json").json["changes"]
    assert kyo("install-hooks", "--copies", "--json").json["changes"] == []


# ------------------------------------------------------------------- KYO-409
# The PreToolUse push guard reads the shared shell parse (kyosei.shell_words), pays
# nothing on a Bash call that is not a push, and judges the project the push's own
# directory belongs to.


def _payload_in(command: str, cwd: Path | None) -> str:
    payload: dict = {"tool_name": "Bash", "tool_input": {"command": command}}
    if cwd is not None:
        payload["cwd"] = str(cwd)
    return json.dumps(payload)


def test_kyo409_a_non_push_never_measures_the_store(
    kyo, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KYO-409.c1. Every Bash call an agent makes reaches this hook, and measuring the
    store counts every event line — 0.25 s and 123 MB on this repository, per call, to
    answer "not a push". With ``publication_state`` made to explode, a non-push still
    exits 0: the early return comes before the measurement. The push beside it proves
    the patch is live, so this cannot pass by the patch missing its target."""
    from kyosei import cmd_publish

    git_init(project)

    def explode(*_args, **_kwargs):
        raise AssertionError("publication_state measured the store for a non-push")

    monkeypatch.setattr(cmd_publish, "publication_state", explode)
    for command in ("ls", "git status", "echo 'git push'"):
        result = _in(project)(
            "check-push", "--pre-tool-use", "--json", stdin=_payload_in(command, project)
        )
        assert result.code == EXIT_OK, (command, result)
        assert result.json["matched"] is False
        assert "state" not in result.json and "publishes" not in result.json

    # KYO-421: an exception a handler did not mean to raise no longer escapes `main`;
    # it is reported on one line, exit 1, naming its type and message — which is what
    # proves the patched measurement was reached for the push.
    pushed = _in(project)(
        "check-push", "--pre-tool-use", stdin=_payload_in("git push", project)
    )
    assert pushed.code == 1, pushed
    assert "AssertionError: publication_state measured the store" in pushed.err, pushed


@pytest.mark.parametrize(
    "command",
    [
        "GIT_TERMINAL_PROMPT=0 git push",
        "env git push",
        "env GIT_TERMINAL_PROMPT=0 git push origin main",
        "command git push",
        "sleep 1 & git push",
        "(git push origin main)",
        "nohup git push > /tmp/p.log 2>&1 &",
        "timeout 60 git push",
    ],
)
def test_kyo409_wrapped_and_backgrounded_pushes_are_pushes(command: str) -> None:
    """KYO-409.c2: the forms `publishing_commands()` used to miss.
    Each one fails if the shared wrapper/env skipping or the single-``&`` separator is
    removed from kyosei.shell_words."""
    assert publishing_commands(command), command


@pytest.mark.parametrize(
    "command",
    [
        'echo "git push"',
        "git push-foo",
        "git pushy origin",
        "command -v git push",
        "cat <<'EOF'\ngit push\nEOF",
        "git log # then git push",
    ],
)
def test_kyo409_what_still_is_not_a_push(command: str) -> None:
    """The narrow side, held while the match widened: an argument, a longer
    subcommand, a lookup, a heredoc body and a comment all mention a push and run
    none. The heredoc case is not academic: the packed hook this was written under
    refused the worker's own `cat >> tests/... <<'EOF'` because a test body inside it
    mentioned a push."""
    assert publishing_commands(command) == [], command


def _scratch_repo(where: Path) -> Path:
    where.mkdir(parents=True)
    (where / "README").write_text("scratch\n", encoding="utf-8")
    git_init(where)
    return where


def test_kyo409_a_scratch_push_from_inside_a_project_is_not_blocked(
    project: Path, tmp_path: Path
) -> None:
    """KYO-409.c3, a case observed live: a push of an unrelated scratch
    repository, typed from inside an unacknowledged in-repo project, was blocked and
    shown THIS project's counts. Every route to the scratch directory — ``cd``, ``git
    -C``, the payload's own cwd — and a scratch repository nested INSIDE the project's
    tree (its git top level is below the project's root) is now judged as what it is.
    Fails if check-push goes back to judging the hook's layout alone."""
    git_init(project)
    assert _in(project)("check-push").code == EXIT_CONTRACT  # the project does block

    outside = _scratch_repo(tmp_path / "scratch")
    nested = _scratch_repo(project / "work" / "nested-scratch")
    for scratch in (outside, nested):
        for command, cwd in (
            (f'cd "{scratch}" && git push -q "{scratch}/remote.git" main', project),
            (f'git -C "{scratch}" push -q origin main', project),
            ("git push -q origin main", scratch),
        ):
            result = _in(project)(
                "check-push", "--pre-tool-use", "--json", stdin=_payload_in(command, cwd)
            )
            assert result.code == EXIT_OK, (scratch, command, result)
            assert result.json["matched"] is True
            assert result.json["state"] == "no-project", result.json
            assert result.json["directory"] == str(scratch.resolve())


def test_kyo409_a_push_into_a_project_from_outside_it_is_blocked_with_its_counts(
    kyo, project: Path, tmp_path: Path
) -> None:
    """KYO-409.c3's other direction: the hook belongs to ANOTHER project — one whose
    maintainer has acknowledged publishing its own store — and the push names the
    unacknowledged in-repo project by ``git -C`` or a ``cd``. It is that project's store
    the push publishes, so it is that project's state and counts the refusal states, and
    the hook project's acknowledgement is not borrowed for it.

    The hook's project is a real one because that is the only place the packaged hook
    runs (``kyo install-hooks`` writes a project's own ``.claude/settings.json``); a
    hook context with no readable store at all turns any refusal into
    ``cli.main``'s NOTHING WAS CHECKED line, which is KYO-335's business, not this."""
    git_init(project)
    kyo("note", "--shift", "--file", "-", stdin="a private suspicion\n")
    elsewhere = tmp_path / "elsewhere"
    assert invoke(["init", str(elsewhere), "--in-repo", "--prefix", "ELS"]).code == 0
    git_init(elsewhere)
    assert _in(elsewhere)("check-push", "--acknowledge").code == EXIT_OK
    assert _in(elsewhere)("check-push").code == EXIT_OK
    counts = _in(project)("check-push", "--json").json["counts"]

    for command in (f'git -C "{project}" push origin main', f'cd "{project}" && git push'):
        result = invoke(
            ["--root", str(elsewhere), "check-push", "--pre-tool-use", "--json"],
            stdin=_payload_in(command, elsewhere),
        )
        assert result.code == EXIT_CONTRACT, (command, result)
        assert result.json["state"] == STATE_IN_REPO
        assert result.json["measured_in"] == str(project.resolve())
        assert result.json["counts"] == counts
        assert result.json["counts"]["shift_notes"] == 1


def test_kyo409_a_push_from_a_directory_that_cannot_be_followed_fails_closed(
    project: Path,
) -> None:
    """``cd "$DIR"`` is not expanded, so where the push runs is unknown. The guard
    fails closed: it judges the hook's own project, as before KYO-409, and says the
    directory was not followed. Letting it through would publish an unacknowledged
    store whenever the directory comes from a variable."""
    git_init(project)
    result = _in(project)(
        "check-push",
        "--pre-tool-use",
        "--json",
        stdin=_payload_in('cd "$SOMEWHERE" && git push', project),
    )
    assert result.code == EXIT_CONTRACT, result
    assert result.json["state"] == STATE_IN_REPO
    assert result.json["directory"] is None
    assert result.json["directory_followed"] is False