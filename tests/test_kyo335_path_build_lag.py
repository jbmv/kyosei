"""KYO-335: a store one build can write and the `kyo` on PATH cannot read.

The incident these tests are built from is KYO-325.r1, measured on this project's own
store: the controller added a config key from a working-tree `kyo`, the
`kyo` every hook runs is the desktop app's bundled build, that build refuses "keys no
code path reads", and for 120.5 minutes every hook in the project exited without
checking anything while saying only that it had failed.

**Nothing here depends on how the host running the suite is installed.** The "PATH build" is a stub
script on a `PATH` the test controls, and it refuses the way the real old build
refuses — by reading `kyosei/config.yml` and rejecting a key its own (frozen) key list
does not carry. That is what makes the c4 remedies checkable end to end: removing the
key really does make the stub open the store, and "upgrading" the stub really is
teaching it the key.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from kyosei import config as config_module
from kyosei import install_hooks
from kyosei.cmd_check_build import HOOK_INVOCATIONS, hook_invocation
from kyosei.errors import KyoseiError

from .conftest import REAL_PATH_PROGRAM, invoke


@pytest.fixture(autouse=True)
def real_path_program(monkeypatch):
    """Put the real resolver back: this file's whole subject is the `kyo` on PATH.

    ``tests/conftest.py`` replaces :func:`kyosei.config.path_program` with one that
    always answers ``None``, so no other test in the suite can see the host's
    install. Every test here supplies its own `PATH` holding a stub `kyo`, which is
    what makes these assertions say the same thing on any machine — so here, and
    only here, the resolver is the real one again. Ordering is pytest's: a conftest
    autouse fixture runs before a module's, so this sets the last word.
    """
    monkeypatch.setattr(config_module, "path_program", REAL_PATH_PROGRAM)


#: The key the real incident turned on. Any name this build's `CONFIG_CONSUMERS` does
#: not carry would do; this one is the measured one.
LAGGING_KEY = "worker_command_timeout_ceiling_seconds"

#: What a build that does not know a key says about it — `kyosei.config.load_config`'s
#: own sentence, which is what `kyo where --json` carries verbatim in `store_problem`.
REFUSAL = "carries keys no code path reads"

#: The stub `kyo`'s body. A miniature of the old build: it answers the two questions
#: the probe asks, and its answer to the first is DERIVED from the project's own
#: config file against a frozen key list — so the remedies can be exercised rather
#: than simulated. Its three constants are prepended by :func:`write_stub` as JSON.
STUB_BODY = '''
import json
import sys
from pathlib import Path

import yaml

argv = sys.argv[1:]
if argv[:1] == ["version"]:
    print(json.dumps(VERSION))
    raise SystemExit(0)
if argv[:1] != ["where"] or not HAS_WHERE:
    print("kyo: argument command: invalid choice: " + " ".join(argv[:1]), file=sys.stderr)
    raise SystemExit(64)
root = Path(argv[argv.index("--root") + 1])
config = root / "kyosei" / "config.yml"
raw = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
unknown = sorted(set(raw) - set(KNOWN))
if unknown:
    problem = (
        str(config)
        + " carries keys no code path reads: "
        + ", ".join(unknown)
        + " (law 2 - remove them or ship their consumer)"
    )
    print(json.dumps({"can_open": False, "store_problem": problem}))
else:
    print(json.dumps({"can_open": True, "store_problem": None}))
raise SystemExit(0)
'''


def write_stub(
    directory: Path,
    *,
    known: list[str],
    has_where: bool = True,
    version_line: str = "kyosei 0.1.0 - build deadbee1 (2026-09-01); stamped into the "
    "package when this copy was built",
    kind: str = "bundled",
    release: bool | None = None,
) -> Path:
    """A `kyo` on PATH that knows ``known`` config keys and nothing else.

    Two files, and the wrapper is not ceremony: this checkout's own path carries a
    space, so a ``#!`` line naming the venv's interpreter is truncated at it by the
    kernel and the stub dies as "bad interpreter". The `/bin/sh` wrapper quotes both
    paths, which is also closer to what is being modelled — a `kyo` on PATH is a
    console script, not a python file.
    """
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "old_build.py"
    header = "\n".join(
        [
            f"KNOWN = {known!r}",
            # ``release`` only when given: a build from before KYO-445 carries no such
            # field, and that absence is one of the cases KYO-446 must answer.
            "VERSION = "
            + repr(
                {"line": version_line, "kind": kind}
                | ({} if release is None else {"release": release})
            ),
            f"HAS_WHERE = {has_where!r}",
        ]
    )
    script.write_text(header + STUB_BODY, encoding="utf-8")
    path = directory / "kyo"
    path.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8"
    )
    path.chmod(0o755)
    return path


def known_keys() -> list[str]:
    """Every config key THIS build reads — what an up-to-date PATH build would know."""
    from kyosei.schema import CONFIG_CONSUMERS

    return sorted(CONFIG_CONSUMERS)


def stale_keys() -> list[str]:
    """The same list, minus the key the incident turned on: a build one key behind."""
    return [key for key in known_keys() if key != LAGGING_KEY]


@pytest.fixture
def hooked_project(project: Path) -> Path:
    """The fixture project, with hooks installed and the lagging key declared.

    Both halves matter. The hooks are what make the PATH build this project's
    business at all (:func:`kyosei.config.project_runs_kyo_from_path`), and the key
    is the store-side change a build one release behind refuses.
    """
    result = invoke(["install-hooks", "--copies", "--root", str(project)])
    assert result.code == 0, result
    assert f"{config_module.PATH_PROGRAM} " in (
        project / install_hooks.CLAUDE_SETTINGS_REL
    ).read_text(encoding="utf-8")
    declare_lagging_key(project)
    return project


def declare_lagging_key(project: Path, value: int = 240) -> None:
    config = project / "kyosei" / "config.yml"
    config.write_text(
        config.read_text(encoding="utf-8") + f"\n{LAGGING_KEY}: {value}\n",
        encoding="utf-8",
    )


def remove_lagging_key(project: Path) -> None:
    """The remedy from the store's end: undo the change that build refuses."""
    config = project / "kyosei" / "config.yml"
    kept = [
        line
        for line in config.read_text(encoding="utf-8").splitlines()
        if not line.startswith(f"{LAGGING_KEY}:")
    ]
    config.write_text("\n".join(kept) + "\n", encoding="utf-8")


def on_path(monkeypatch, directory: Path) -> None:
    monkeypatch.setenv("PATH", str(directory))


def unreadable_to_this_build(project: Path) -> None:
    """Make THIS build unable to open the store, the way the old build could not.

    A key no `CONFIG_CONSUMERS` entry claims is refused by `load_config`, which is
    what `ctx.store()` raises on — the exact failure every hook hit in the incident,
    reproduced against the running build so the hook-side behaviour can be observed.
    """
    config = project / "kyosei" / "config.yml"
    config.write_text(
        config.read_text(encoding="utf-8") + "\na_key_from_a_later_build: 1\n",
        encoding="utf-8",
    )


# ----------------------------------------------------------------- the probe itself


def test_probe_reads_the_path_builds_own_refusal(hooked_project, tmp_path, monkeypatch):
    on_path(monkeypatch, tmp_path / "bin")
    write_stub(tmp_path / "bin", known=stale_keys())

    build = config_module.probe_path_build(hooked_project)

    assert build is not None
    assert build.can_open is False
    assert build.blind is True
    assert REFUSAL in build.problem
    assert LAGGING_KEY in build.problem
    assert build.version_line is not None and "deadbee1" in build.version_line
    # The remedy is that build's, not this one's (KYO-317's defect one step along). The
    # stub answers `bundled`, the kind an older desktop-app build reports; KYO-450
    # retired it, and its remedy is now the install that replaces it, never a re-pack.
    remedy = config_module.path_build_remedy(build)
    assert "uv tool install kyosei" in remedy
    assert "npm run pack" not in remedy


def test_probe_is_silent_when_the_path_build_can_open(hooked_project, tmp_path, monkeypatch):
    on_path(monkeypatch, tmp_path / "bin")
    write_stub(tmp_path / "bin", known=known_keys())

    build = config_module.probe_path_build(hooked_project)

    assert build is not None and build.can_open is True and build.blind is False


def test_probe_is_none_when_nothing_is_on_path(hooked_project, tmp_path, monkeypatch):
    on_path(monkeypatch, tmp_path / "empty")
    (tmp_path / "empty").mkdir()

    assert config_module.probe_path_build(hooked_project) is None


def test_probe_is_none_when_path_kyo_is_the_running_program(tmp_path, monkeypatch):
    """"The PATH build" and "this build" being one program is not a lag to describe."""
    stub = write_stub(tmp_path / "bin", known=stale_keys())
    on_path(monkeypatch, tmp_path / "bin")

    assert config_module.path_program(argv0=str(stub)) is None


def test_a_probe_that_cannot_answer_is_not_a_pass(hooked_project, tmp_path, monkeypatch):
    """A build too old for `kyo where` is `None`, never `True` (law 6)."""
    on_path(monkeypatch, tmp_path / "bin")
    write_stub(tmp_path / "bin", known=stale_keys(), has_where=False)

    build = config_module.probe_path_build(hooked_project)

    assert build is not None
    assert build.can_open is None
    assert build.blind is False
    assert "not known" in build.problem


# --------------------------------------------------------------------- KYO-335.c2


def doctor_findings(project: Path) -> list[str]:
    result = invoke(["--root", str(project), "doctor", "--json"])
    return result.json["checks"]["path_build_lag"]["failures"]


def test_c2_doctor_reports_the_path_builds_own_error_and_the_remedy(
    hooked_project, tmp_path, monkeypatch
):
    on_path(monkeypatch, tmp_path / "bin")
    stub = write_stub(tmp_path / "bin", known=stale_keys())

    result = invoke(["--root", str(hooked_project), "doctor"])
    findings = doctor_findings(hooked_project)

    assert len(findings) == 1
    finding = findings[0]
    # The PATH build, named; its own words, verbatim; and what it costs.
    assert str(stub) in finding
    assert REFUSAL in finding and LAGGING_KEY in finding
    assert "deadbee1" in finding
    assert "every hook this project installs" in finding
    assert "remedy:" in finding
    # A problem, not a notice: exit 3, and the finding is in the failing set.
    assert result.code == 3, result
    assert any("path_build_lag" in problem for problem in invoke(
        ["--root", str(hooked_project), "doctor", "--json"]
    ).json["problems"])


def test_c2_doctor_is_silent_when_the_path_build_can_open(
    hooked_project, tmp_path, monkeypatch
):
    on_path(monkeypatch, tmp_path / "bin")
    write_stub(tmp_path / "bin", known=known_keys())

    assert doctor_findings(hooked_project) == []


def test_c2_doctor_is_silent_where_no_hook_runs_kyo(project, tmp_path, monkeypatch):
    """No hooks, no PATH build to be wrong about — the gate that keeps this cheap."""
    on_path(monkeypatch, tmp_path / "bin")
    write_stub(tmp_path / "bin", known=stale_keys())
    declare_lagging_key(project)
    settings = project / install_hooks.CLAUDE_SETTINGS_REL
    if settings.exists():
        settings.unlink()

    assert config_module.project_runs_kyo_from_path(project) is False
    assert doctor_findings(project) == []


def test_c2_a_probe_that_could_not_answer_is_reported_as_its_own_thing(
    hooked_project, tmp_path, monkeypatch
):
    on_path(monkeypatch, tmp_path / "bin")
    write_stub(tmp_path / "bin", known=stale_keys(), has_where=False)

    findings = doctor_findings(hooked_project)

    assert len(findings) == 1
    assert "could not be asked" in findings[0]
    assert "not a state to read as healthy" in findings[0]


# ------------------------------------------------- KYO-335.c4, against c2's remedies


def test_c4_removing_the_key_clears_the_doctor_finding(
    hooked_project, tmp_path, monkeypatch
):
    """Remedy one, from the store's end: undo the change that build refuses."""
    on_path(monkeypatch, tmp_path / "bin")
    write_stub(tmp_path / "bin", known=stale_keys())
    assert doctor_findings(hooked_project) != []

    remove_lagging_key(hooked_project)

    assert doctor_findings(hooked_project) == []


def test_c4_upgrading_the_path_build_clears_the_doctor_finding(
    hooked_project, tmp_path, monkeypatch
):
    """Remedy two, from the build's end: move the `kyo` on PATH to a build that knows."""
    on_path(monkeypatch, tmp_path / "bin")
    write_stub(tmp_path / "bin", known=stale_keys())
    assert doctor_findings(hooked_project) != []

    write_stub(tmp_path / "bin", known=known_keys())

    assert doctor_findings(hooked_project) == []


# --------------------------------------------------------------------- KYO-335.c1


def test_c1_rebuild_says_the_path_build_will_refuse_and_proceeds(
    hooked_project, tmp_path, monkeypatch
):
    """`kyo rebuild` is the one command that rewrites config.yml and stamps a schema."""
    on_path(monkeypatch, tmp_path / "bin")
    stub = write_stub(tmp_path / "bin", known=stale_keys())

    result = invoke(["--root", str(hooked_project), "rebuild", "--force"])

    assert result.code == 0, result
    # It proceeded: the store was rebuilt.
    assert "rebuilt" in result.out
    assert (hooked_project / "kyosei" / "kyosei.db").is_file()
    # And it said what the PATH build will now make of it.
    assert str(stub) in result.out
    assert "cannot open the store this rebuild just wrote" in result.out
    assert REFUSAL in result.out
    assert "deadbee1" in result.out
    assert "remedy:" in result.out


def test_c1_rebuild_is_silent_when_the_path_build_can_open(
    hooked_project, tmp_path, monkeypatch
):
    on_path(monkeypatch, tmp_path / "bin")
    write_stub(tmp_path / "bin", known=known_keys())

    result = invoke(["--root", str(hooked_project), "rebuild", "--force"])

    assert result.code == 0, result
    assert "cannot open" not in result.out


def test_c4_removing_the_key_clears_the_rebuild_note(
    hooked_project, tmp_path, monkeypatch
):
    on_path(monkeypatch, tmp_path / "bin")
    write_stub(tmp_path / "bin", known=stale_keys())
    assert "cannot open" in invoke(
        ["--root", str(hooked_project), "rebuild", "--force"]
    ).out

    remove_lagging_key(hooked_project)

    assert "cannot open" not in invoke(
        ["--root", str(hooked_project), "rebuild", "--force"]
    ).out


# --------------------------------------------------------------------- KYO-335.c3


#: The hook invocations that reach the store, and how each is spelled on the command
#: line. Kept beside the assertions rather than derived, because what is being checked
#: is that EACH of these says the sentence — a table derived from the same source the
#: code reads could not fail if the code's table were wrong.
STORE_READING_HOOKS = (
    ("brief", ["brief", "--compact", "--session-start"]),
    ("sweep", ["sweep"]),
    ("doctor", ["doctor"]),
)


@pytest.mark.parametrize(("name", "argv"), STORE_READING_HOOKS, ids=lambda v: str(v))
def test_c3_a_hook_that_cannot_read_the_store_says_nothing_was_checked(
    hooked_project, name, argv
):
    unreadable_to_this_build(hooked_project)

    result = invoke(["--root", str(hooked_project), *argv])

    first = result.err.strip().splitlines()[0]
    # One line, and it is the FIRST line of stderr — the only thing a hook error
    # notice renders (KYO-316's measurement).
    assert result.err.strip().count("\n") == 0, result
    assert first.startswith(f"kyo {name}: NOTHING WAS CHECKED")
    # It names the cause verbatim rather than replacing it.
    assert "a_key_from_a_later_build" in first
    assert "Remedy:" in first
    # Non-blocking on every event this project installs into: never exit 2.
    assert result.code != 2, result
    assert result.code != 0, result


def test_c3_check_command_does_not_claim_the_project_declares_nothing(hooked_project):
    """The false claim the busiest hook made for two hours, on every Bash call."""
    unreadable_to_this_build(hooked_project)
    payload = json.dumps({"tool_input": {"command": "make test"}, "cwd": str(hooked_project)})

    result = invoke(
        ["--root", str(hooked_project), "check-command", "--pre-tool-use"], stdin=payload
    )

    assert "NOTHING WAS CHECKED" in result.out
    assert "declares no `controller_only_commands`" not in result.out
    assert "a_key_from_a_later_build" in result.out
    # Refuses nothing and blocks nothing: this is the hook on every Bash call.
    assert result.code == 0, result


@pytest.mark.parametrize("command", ["check-clean", "check-branch"])
def test_c3_the_subagent_stop_guards_already_say_it(hooked_project, command, tmp_path):
    """KYO-325's outcome, kept honest here so c3's claim covers all seven hooks."""
    unreadable_to_this_build(hooked_project)
    payload = json.dumps({"agent_id": "a1", "cwd": str(hooked_project)})

    args = ["--root", str(hooked_project), command, "--subagent-stop"]
    if command == "check-branch":
        args += ["--against", "main"]
    result = invoke(args, stdin=payload)

    assert "NOTHING WAS CHECKED" in result.out + result.err, result
    assert result.code != 2, result


@pytest.mark.parametrize(
    ("command", "argv", "stdin"),
    [
        ("sessions", ["sessions", "--hook"], json.dumps({"session_id": "s1"})),
        (
            "check-push",
            ["check-push", "--pre-tool-use"],
            json.dumps({"tool_input": {"command": "ls"}}),
        ),
    ],
)
def test_c3_the_two_hooks_that_read_no_store_still_do_their_job(
    hooked_project, command, argv, stdin
):
    """Law 6 the other way: these two never open the store, so nothing went unchecked.

    Worth pinning rather than assuming, because KYO-325's own correction note lists
    "no push ... guard" among the casualties of the incident window and that half is
    not so: `kyo check-push` reads `kyosei/config.yml` raw (never the database) and
    `kyo sessions` reads its own JSON file beside the store. Both kept working. A
    NOTHING WAS CHECKED line from either would be this record's own defect inverted —
    a guard claiming it did nothing when it did its whole job.
    """
    unreadable_to_this_build(hooked_project)

    result = invoke(["--root", str(hooked_project), *argv], stdin=stdin)

    assert "NOTHING WAS CHECKED" not in result.out + result.err, result
    assert result.code == 0, result


def test_c3_the_table_covers_every_hook_this_project_installs():
    """Every `kyo` subcommand the packaged hook fragments invoke is in the table."""
    invoked: set[str] = set()
    for _event, fragment in install_hooks.HOOK_INSTALL_TARGETS:
        for group in install_hooks._load_hook_fragment(fragment):
            for hook in group.get("hooks", []):
                command = install_hooks.hook_action(hook.get("command"))
                for piece in str(command).split("&&"):
                    words = piece.strip().split()
                    if words[:1] == [config_module.PATH_PROGRAM] and len(words) > 1:
                        invoked.add(words[1])

    assert invoked, "no hook fragment invoked `kyo` — the parse above is wrong"
    declared = {shape.command for shape in HOOK_INVOCATIONS}
    assert invoked <= declared, sorted(invoked - declared)


def test_c3_a_hand_run_command_that_is_not_a_hook_keeps_its_own_error(hooked_project):
    """The sentence is for hook-shaped invocations; `kyo list` keeps its own refusal."""
    unreadable_to_this_build(hooked_project)

    result = invoke(["--root", str(hooked_project), "list"])

    assert "NOTHING WAS CHECKED" not in result.err
    assert "a_key_from_a_later_build" in result.err


def test_c3_hook_invocation_needs_the_flag_that_makes_it_a_hook():
    from argparse import Namespace

    assert hook_invocation(Namespace(command="brief", session_start=True)) is not None
    assert hook_invocation(Namespace(command="brief", session_start=False)) is None
    # `sweep` and `doctor` are run by the hook exactly as a person runs them.
    assert hook_invocation(Namespace(command="sweep")) is not None
    assert hook_invocation(Namespace(command="list")) is None


# ------------------------------------------------- KYO-335.c4, against c3's remedy


def test_c4_removing_the_key_clears_the_hook_line_and_the_hook_runs(hooked_project):
    """The remedy the hook line names, proved against the hook line itself."""
    unreadable_to_this_build(hooked_project)
    before = invoke(["--root", str(hooked_project), "sweep"])
    assert "NOTHING WAS CHECKED" in before.err

    config = hooked_project / "kyosei" / "config.yml"
    config.write_text(
        "\n".join(
            line
            for line in config.read_text(encoding="utf-8").splitlines()
            if not line.startswith("a_key_from_a_later_build:")
        )
        + "\n",
        encoding="utf-8",
    )

    after = invoke(["--root", str(hooked_project), "sweep"])
    assert "NOTHING WAS CHECKED" not in after.err
    assert after.code == 0, after
    # And the config really is loadable again, which is what the remedy claimed.
    assert yaml.safe_load(config.read_text(encoding="utf-8")) is not None


def test_c1_init_into_a_repo_that_already_runs_kyo_hooks_says_so(
    project: Path, tmp_path: Path, monkeypatch
):
    """The second command that writes config.yml, in the case where it is not silent.

    `kyo init` installs no hooks (`kyo install-hooks` does), so a fresh repository has
    nothing running the PATH build yet and this says nothing. A repository that
    already carries `kyo` hooks and is being given a store is the real situation, and
    it is the one where a maintainer most needs to hear it: the guards are already
    wired up and about to check nothing.
    """
    assert invoke(["install-hooks", "--copies", "--root", str(project)]).code == 0
    settings = (project / install_hooks.CLAUDE_SETTINGS_REL).read_text(encoding="utf-8")

    # A code repository that already runs `kyo` from its hooks and has no store yet.
    elsewhere = tmp_path / "already-hooked"
    (elsewhere / install_hooks.CLAUDE_SETTINGS_REL).parent.mkdir(parents=True)
    (elsewhere / install_hooks.CLAUDE_SETTINGS_REL).write_text(settings, encoding="utf-8")
    on_path(monkeypatch, tmp_path / "bin")
    # One key behind what `kyo init` is about to write — for a fresh store that is
    # `schema_version`, which is the measured incident's shape one field along.
    stub = write_stub(
        tmp_path / "bin", known=[k for k in known_keys() if k != "schema_version"]
    )

    result = invoke(["init", str(elsewhere), "--in-repo", "--prefix", "AHK"])

    assert result.code == 0, result
    assert (elsewhere / "kyosei" / "config.yml").is_file()
    assert str(stub) in result.out
    assert REFUSAL in result.out
    assert "remedy:" in result.out


def test_the_suite_cannot_see_this_machines_own_kyo(monkeypatch):
    """The conftest fixture that makes every OTHER test machine-independent.

    Undoes this module's own restore first, which is the state every other test file
    in the suite runs in: `kyosei.config.path_program` answers None whatever this
    machine has installed, so no probe runs and no subprocess starts.
    """
    from kyosei import config as live

    monkeypatch.setattr(live, "path_program", lambda *_a, **_k: None)

    assert live.path_program() is None
    assert live.probe_path_build(Path("/nonexistent")) is None


def test_c3_the_line_survives_a_refusal_that_wraps(hooked_project, monkeypatch):
    """A multi-line refusal still yields ONE line — only the first reaches a reader."""
    from kyosei import commands

    def raises(self):
        raise KyoseiError("line one of the refusal\nline two, carrying the remedy")

    monkeypatch.setattr(commands.Context, "store", raises)

    result = invoke(["--root", str(hooked_project), "sweep"])

    body = result.err.strip()
    assert body.count("\n") == 0, body
    assert "NOTHING WAS CHECKED" in body
    assert "line two, carrying the remedy" in body


# --------------------------------------------------------------------- KYO-446
#
# KYO-440's distribution decision: this repository dogfoods on the uv tool -- one
# `kyo` per machine, a non-editable snapshot of `main`. KYO-335's hazard and
# machinery are unchanged; the remedy sentence for a uv-tool PATH build names the
# re-snapshot (`make install-tool`) for an unreleased build and `uv tool upgrade kyosei`
# for a release, never the app's re-pack.

REPO = Path(__file__).resolve().parent.parent

UV_TOOL = "uv-tool"


def test_kyo446_c2_doctor_names_the_resnapshot_for_an_unreleased_uv_tool(
    hooked_project, tmp_path, monkeypatch
):
    from kyosei import buildinfo

    on_path(monkeypatch, tmp_path / "bin")
    write_stub(tmp_path / "bin", known=stale_keys(), kind=UV_TOOL, release=False)

    findings = doctor_findings(hooked_project)

    assert len(findings) == 1
    remedy = next(line for line in findings[0].splitlines() if "remedy:" in line)
    assert buildinfo.RESNAPSHOT_TARGET in remedy
    assert "npm run pack" not in remedy
    assert "re-pack" not in remedy


def test_kyo446_c2_the_named_resnapshot_clears_the_doctor_finding(
    hooked_project, tmp_path, monkeypatch
):
    """The remedy the finding names, proved: a re-snapshot is a uv-tool build of the
    newer source, which knows the key -- and the finding is gone."""
    on_path(monkeypatch, tmp_path / "bin")
    write_stub(tmp_path / "bin", known=stale_keys(), kind=UV_TOOL, release=False)
    assert doctor_findings(hooked_project) != []

    # `make install-tool` replaces the PATH build with a snapshot of the source that
    # wrote the key: same kind, still unreleased, now knowing every key this build reads.
    write_stub(tmp_path / "bin", known=known_keys(), kind=UV_TOOL, release=False)

    assert doctor_findings(hooked_project) == []


def test_kyo446_c2_the_write_time_warning_names_the_resnapshot_and_it_clears(
    hooked_project, tmp_path, monkeypatch
):
    """KYO-335.c1's warning (`kyo rebuild`, the command that rewrites config.yml)."""
    from kyosei import buildinfo

    on_path(monkeypatch, tmp_path / "bin")
    write_stub(tmp_path / "bin", known=stale_keys(), kind=UV_TOOL, release=False)

    before = invoke(["--root", str(hooked_project), "rebuild", "--force"])
    assert before.code == 0, before
    assert "cannot open the store this rebuild just wrote" in before.out
    remedy = next(line for line in before.out.splitlines() if "remedy:" in line)
    assert buildinfo.RESNAPSHOT_TARGET in remedy
    assert "npm run pack" not in remedy

    write_stub(tmp_path / "bin", known=known_keys(), kind=UV_TOOL, release=False)

    after = invoke(["--root", str(hooked_project), "rebuild", "--force"])
    assert after.code == 0, after
    assert "cannot open" not in after.out


def test_kyo446_c3_a_released_uv_tool_path_build_is_told_to_upgrade(
    hooked_project, tmp_path, monkeypatch
):
    from kyosei import buildinfo

    on_path(monkeypatch, tmp_path / "bin")
    write_stub(tmp_path / "bin", known=stale_keys(), kind=UV_TOOL, release=True)

    build = config_module.probe_path_build(hooked_project)

    assert build is not None and build.release is True
    remedy = config_module.path_build_remedy(build)
    assert buildinfo.UV_TOOL_UPGRADE_COMMAND in remedy
    assert buildinfo.RESNAPSHOT_TARGET not in remedy

    # And the upgrade clears it: the newer release knows the key.
    write_stub(tmp_path / "bin", known=known_keys(), kind=UV_TOOL, release=True)
    assert doctor_findings(hooked_project) == []


def test_kyo446_c3_a_path_build_that_predates_the_release_field_is_a_snapshot(
    hooked_project, tmp_path, monkeypatch
):
    """No `release` in its `version --json`: it predates KYO-445 and so every release."""
    from kyosei import buildinfo

    on_path(monkeypatch, tmp_path / "bin")
    write_stub(tmp_path / "bin", known=stale_keys(), kind=UV_TOOL)

    build = config_module.probe_path_build(hooked_project)

    assert build is not None and build.release is None
    assert buildinfo.RESNAPSHOT_TARGET in config_module.path_build_remedy(build)


def test_kyo446_the_remedy_never_borrows_the_running_copys_release(
    hooked_project, tmp_path, monkeypatch
):
    """The PATH build's facts decide its remedy, even when this copy is a release."""
    from kyosei import buildinfo

    on_path(monkeypatch, tmp_path / "bin")
    write_stub(tmp_path / "bin", known=stale_keys(), kind=UV_TOOL, release=False)
    monkeypatch.setattr(buildinfo, "install_kind", lambda *_a, **_k: UV_TOOL)
    real = buildinfo.build_identity()
    monkeypatch.setattr(
        buildinfo,
        "build_identity",
        lambda *_a, **_k: buildinfo.BuildIdentity(**{**real.__dict__, "release": True}),
    )

    build = config_module.probe_path_build(hooked_project)

    assert buildinfo.RESNAPSHOT_TARGET in config_module.path_build_remedy(build)


# ------------------------------------------------------ KYO-446.c1, make install-tool


def test_kyo446_c1_make_install_tool_runs_uv_tool_install_force_with_virtual_env_unset(
    tmp_path,
):
    """Run the real target against a `uv` stub that records what it was handed.

    The stub stands in for uv so nothing is installed: what is checked is the argv the
    Makefile passes and whether an inherited VIRTUAL_ENV reached it (KYO-297's lesson --
    an installer that honours it installs this tree into somebody else's environment).
    """
    import os
    import shutil
    import subprocess

    make = shutil.which("make")
    if make is None:  # pragma: no cover - every machine this suite runs on has make
        pytest.skip("no make on PATH")
    record = tmp_path / "uv-called.json"
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "uv"
    stub.write_text(
        "#!/bin/sh\n"
        f'"{sys.executable}" -c \'import json, os, sys; '
        f'json.dump({{"argv": sys.argv[1:], "VIRTUAL_ENV": os.environ.get("VIRTUAL_ENV")}}, '
        f'open({json.dumps(str(record))}, "w"))\' "$@"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "VIRTUAL_ENV": str(tmp_path / "somebody-elses-venv"),
    }

    result = subprocess.run(
        [make, "-s", "install-tool"], cwd=REPO, env=env, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    called = json.loads(record.read_text(encoding="utf-8"))
    assert called["argv"] == ["tool", "install", "--force", "."]
    assert called["VIRTUAL_ENV"] is None
    # A snapshot, never an editable install (docs/reference/topics/install.md, "Install").
    assert "-e" not in called["argv"] and "--editable" not in called["argv"]


# ------------------------------------------- KYO-549.c1, make install-tool retries once


def _failing_uv(tmp_path, failures: int):
    """A `uv` stub that fails its first ``failures`` calls and records every call."""
    import os

    record = tmp_path / "uv-calls.jsonl"
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "uv"
    stub.write_text(
        "#!/bin/sh\n"
        f'"{sys.executable}" -c \'import json, os, sys; '
        f"path = {json.dumps(str(record))}; "
        "n = len(open(path).readlines()) if os.path.exists(path) else 0; "
        'open(path, "a").write(json.dumps({"argv": sys.argv[1:], '
        '"VIRTUAL_ENV": os.environ.get("VIRTUAL_ENV")}) + "\\n"); '
        f'sys.exit(1 if n < {failures} else 0)\' "$@"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "VIRTUAL_ENV": str(tmp_path / "somebody-elses-venv"),
    }
    return record, env


def _run_install_tool(env):
    import shutil
    import subprocess

    make = shutil.which("make")
    if make is None:  # pragma: no cover - every machine this suite runs on has make
        pytest.skip("no make on PATH")
    return subprocess.run(
        [make, "-s", "install-tool"], cwd=REPO, env=env, capture_output=True, text=True
    )


def _calls(record):
    return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]


def test_kyo549_c1_install_tool_retries_a_failed_install_once(tmp_path):
    record, env = _failing_uv(tmp_path, failures=1)
    result = _run_install_tool(env)
    assert result.returncode == 0, result.stderr
    assert "retrying once" in result.stderr
    calls = _calls(record)
    assert [call["argv"] for call in calls] == [["tool", "install", "--force", "."]] * 2
    # The retry keeps KYO-297's prefix: an inherited VIRTUAL_ENV reaches neither attempt.
    assert [call["VIRTUAL_ENV"] for call in calls] == [None, None]


def test_kyo549_c1_two_failures_exit_nonzero_saying_kyo_may_be_missing_and_what_to_rerun(
    tmp_path,
):
    record, env = _failing_uv(tmp_path, failures=2)
    result = _run_install_tool(env)
    assert result.returncode != 0
    assert len(_calls(record)) == 2  # one retry, not a loop
    assert "kyo may now be missing from PATH" in result.stderr
    assert "rerun `make install-tool`" in result.stderr
    # The remedy: rerun it once the install can succeed (here, the stub's third call).
    rerun = _run_install_tool(env)
    assert rerun.returncode == 0, rerun.stderr
    assert len(_calls(record)) == 3


def test_kyo446_c1_readme_says_when_to_run_make_install_tool():
    from kyosei import buildinfo
    from tests.reference_pages import INSTALL

    readme = INSTALL.read_text(encoding="utf-8")  # the README section, moved (KYO-516)
    install = readme.split("## Install", 1)[1].split("\n## ", 1)[0]
    assert buildinfo.RESNAPSHOT_TARGET in install
    assert "schema_version" in install
    assert buildinfo.UV_TOOL_UPGRADE_COMMAND in install
