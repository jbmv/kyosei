"""KYO-143: a fresh dispatch worktree has no tooling, and the brief must not pretend otherwise.

``git worktree add`` checks out tracked files and nothing else. Everything gitignored —
a ``.venv/``, a ``node_modules/``, a compiled binary, a seeded database — is absent from
the tree the instant a worker is sent to it, while the project's own README, CLAUDE.md
and Makefile go on describing a checkout somebody had already built in. KYO-127's
dispatch paid the first fifth of its budget discovering that, and reported it.

Two things this file pins that the record's own framing did not start with, both found
by measurement in the worktree KYO-143.d1 ran in:

*The absence is the cheap half.* A missing ``.venv`` is loud: nothing runs, and the
worker says so. The expensive half is silent — a worktree isolates *files* and inherits
the spawning process's *environment* unchanged, so ``VIRTUAL_ENV`` and ``PATH`` still
point into the checkout the worker was sent away from. Measured at the top of KYO-143.d1,
before anything was installed: the controller's ``.venv/bin`` led the inherited ``PATH``,
so bare ``python``, ``pytest`` and ``kyo`` all resolved to the controller's interpreter
importing the controller's source, from inside the worktree. The same inheritance had
already run the other way earlier — an ``uv pip install -e .`` that followed the
inherited ``VIRTUAL_ENV`` pointed the *controller's* venv at an unmerged worktree's
source, and both parties saw success. No provisioning command fixes that, which is why
the environment note below is rendered on every brief that names a worktree rather than
only on projects that declared setup.

*Only the project knows.* What a worktree needs is a fact Kyosei cannot derive: a venv,
an npm install, a database, nothing at all. So ``worktree_setup`` is declared in
``kyosei/config.yml`` and Kyosei renders it and runs none of it. A store command that ran
a project's package manager on its own initiative would be assuming exactly what the key
exists to have the project state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kyosei.brief import _setup_lines, _worktree_tooling_bullet
from kyosei.config import SetupCommand, load_config
from kyosei.errors import KyoseiError
from kyosei.schema import CONFIG_CONSUMERS, OPTIONAL_CONFIG_KEYS

from .conftest import new_record
from .this_store import store_path


def _declare_setup_replacing(project: Path, block: str) -> None:
    """Replace whatever ``worktree_setup`` the config carries with ``block``.

    Truncates at the key rather than filtering lines, because KYO-164's mapping form has
    continuation lines (``note:``) that a line filter written for the bare-string form
    leaves behind — and a half-deleted block fails with a YAML parse error that looks
    nothing like the refusal a test is asking about.
    """
    path = project / "kyosei" / "config.yml"
    text = path.read_text(encoding="utf-8")
    head = text.split("worktree_setup:", 1)[0]
    path.write_text(head.rstrip("\n") + "\n" + block, encoding="utf-8")


def _declare_setup(project: Path, block: str) -> None:
    """Append a raw ``worktree_setup`` block to the project's generated config."""
    path = project / "kyosei" / "config.yml"
    path.write_text(path.read_text(encoding="utf-8") + block, encoding="utf-8")


def _undeclare_setup(project: Path) -> None:
    """Delete the key — the remedy several of the errors below name."""
    path = project / "kyosei" / "config.yml"
    kept = [
        line
        for line in path.read_text(encoding="utf-8").splitlines(keepends=True)
        if not line.startswith("worktree_setup") and not line.lstrip().startswith("- ")
    ]
    path.write_text("".join(kept), encoding="utf-8")


def _brief_for(kyo, record: str) -> str:
    return kyo("show", record, "--json").json["dispatches"][0]["brief"]


# ----------------------------------------------- KYO-143.c2: declared, not assumed


def test_the_project_declares_what_its_worktrees_need(kyo, git_project: Path) -> None:
    """c2. The commands come from the project's own config and nowhere else."""
    _declare_setup(
        git_project,
        "worktree_setup:\n  - make test\n  - npm --prefix apps/desktop ci\n",
    )
    assert load_config(git_project).worktree_setup == (
        "make test",
        "npm --prefix apps/desktop ci",
    )


def test_a_project_that_declares_nothing_parses_to_nothing(kyo, git_project: Path) -> None:
    """c4's precondition: absence is a real, reachable state — not an error, and not a
    default borrowed from a project Kyosei has never seen."""
    assert load_config(git_project).worktree_setup == ()


def test_the_key_is_registered_with_consumers_that_exist(kyo) -> None:
    """Law 2, in both directions. `kyo doctor`'s config_consumers check resolves each
    string below against real code, so this test failing means the key went undeclared
    — and the doctor check failing means a consumer was renamed out from under it."""
    assert CONFIG_CONSUMERS["worktree_setup"] == (
        "brief.generate_brief",
        "cmd_dispatch._cmd_dispatch_create",
    )
    # Optional, because config.yml is generated per project and never copied from a
    # packaged template: every project initialised before this key has no line for it.
    assert "worktree_setup" in OPTIONAL_CONFIG_KEYS


# ------------------------------------- KYO-143.c1: the worker is told what to run


def test_the_declared_commands_reach_the_frozen_brief(kyo, git_project: Path) -> None:
    """c1. The worker arrives knowing the command instead of reconstructing tooling no
    record asked it to build."""
    _declare_setup(git_project, "worktree_setup:\n  - make test\n")
    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record, "--branch", "kyo-143-x").code == 0

    brief = _brief_for(kyo, record)
    assert "Worktree tooling" in brief
    assert "make test" in brief
    # And it says who runs it, because the whole point of c2 is that Kyosei does not.
    assert "printed" in brief.lower() or "ran none of them" in brief


def test_the_declared_commands_are_printed_beside_git_worktree_add(
    kyo, git_project: Path
) -> None:
    """c1/c2 at the seam the record named: the one moment where what the worker will
    find is being decided is the moment the controller is told how to create it."""
    _declare_setup(git_project, "worktree_setup:\n  - make test\n")
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--branch", "kyo-143-y")
    assert result.code == 0, result

    lines = result.out.splitlines()
    add_index = next(i for i, line in enumerate(lines) if "git worktree add" in line)
    after = "\n".join(lines[add_index:])
    assert "make test" in after, result.out
    assert "worktree_setup" in after, result.out


def test_kyosei_never_runs_the_declared_commands(kyo, git_project: Path) -> None:
    """c2's boundary, stated as a test because it is the thing most likely to be
    'helpfully' added later. The command below would create a file if it ran."""
    marker = git_project / "kyosei-ran-this"
    _declare_setup(git_project, f"worktree_setup:\n  - touch {marker}\n")
    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record, "--branch", "kyo-143-z").code == 0
    assert not marker.exists(), (
        "`kyo dispatch` executed a project's declared setup command; it must render "
        "them and run none of them (KYO-143.c2)"
    )


# --------------------- KYO-143.c3: the brief asserts no path it has not checked


def test_the_brief_never_claims_a_gitignored_path_is_present(kyo, git_project: Path) -> None:
    """c3. The generated logistics state what the *project declared*, never what the
    tree *contains* — `kyo dispatch` normally runs in a different checkout from the
    worker's, so a claim about the worker's tree would be a claim about a tree this
    command has not looked at. That is the defect class KYO-128 shipped about."""
    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record, "--branch", "kyo-143-c3").code == 0
    brief = _brief_for(kyo, record)

    assert "tracked files only" in brief
    # The brief may *name* gitignored paths as things that are absent; what it must
    # never do is describe one as available. The sentence that carries the naming is
    # the one that calls them missing.
    assert ".venv/" in brief and "absent" in brief


def test_the_brief_warns_that_the_environment_crosses_the_boundary(
    kyo, git_project: Path
) -> None:
    """The NOTE on the record, and the finding that inverted its severity. Rendered
    whether or not the project declared setup, because it is a property of the
    mechanism rather than of the project — and because no setup command can fix it."""
    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record, "--branch", "kyo-143-env").code == 0
    brief = _brief_for(kyo, record)

    assert "inherited, not isolated" in brief
    assert "VIRTUAL_ENV" in brief
    assert "PATH" in brief
    # The backwards case is the one a worker will not think of on its own.
    assert "unmerged source" in brief


def test_the_environment_warning_survives_a_declared_setup(kyo, git_project: Path) -> None:
    """A project that declares provisioning has fixed the loud half and none of the
    silent half, so the warning must not disappear when the commands appear."""
    _declare_setup(git_project, "worktree_setup:\n  - make test\n")
    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record, "--branch", "kyo-143-env2").code == 0
    assert "inherited, not isolated" in _brief_for(kyo, record)


# ---------------------------------------------- KYO-143.c4: silence is not an option


def test_a_project_declaring_nothing_gets_a_brief_that_says_so(kyo, git_project: Path) -> None:
    """c4. The empty case is the designed case, not a fallback: the brief states what a
    fresh worktree is missing rather than leaving the worker to infer it from a failure."""
    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record, "--branch", "kyo-143-c4").code == 0
    brief = _brief_for(kyo, record)

    assert "declares none" in brief
    assert "tracked files only" in brief
    # Naming the documents that will contradict the tree is the specific rescue: this
    # is what KYO-127's worker needed and did not have.
    assert "CLAUDE.md" in brief or "README" in brief


def test_dispatch_output_warns_the_controller_when_nothing_is_declared(
    kyo, git_project: Path
) -> None:
    """c4 at the other surface. The controller is the party holding a shell and the
    only one who can end the warning by declaring the key, so it is named."""
    record = new_record(kyo, criteria=["a"])
    result = kyo("dispatch", record, "--branch", "kyo-143-c4b")
    assert result.code == 0, result
    assert "declares no `worktree_setup`" in result.out
    assert "config.yml" in result.out


# --------------------------------------- refusals, each with the remedy it names


def test_a_bare_string_is_refused_and_the_named_remedy_clears_it(
    kyo, git_project: Path
) -> None:
    """One command or a shell script? Genuinely ambiguous, so it is refused rather than
    guessed — and the message prints the list form that fixes it."""
    _declare_setup(git_project, "worktree_setup: make test\n")
    with pytest.raises(KyoseiError) as excinfo:
        load_config(git_project)
    assert "must be a LIST" in str(excinfo.value)

    _undeclare_setup(git_project)
    _declare_setup(git_project, "worktree_setup:\n  - make test\n")
    assert load_config(git_project).worktree_setup == ("make test",)


def test_an_empty_list_is_refused_and_deleting_the_key_clears_it(
    kyo, git_project: Path
) -> None:
    """An empty list reads as a declaration while declaring nothing, and is
    indistinguishable downstream from the absent case it would silently become."""
    _declare_setup(git_project, "worktree_setup: []\n")
    with pytest.raises(KyoseiError) as excinfo:
        load_config(git_project)
    message = str(excinfo.value)
    assert "declares nothing" in message
    assert "delete the key" in message

    _undeclare_setup(git_project)
    assert load_config(git_project).worktree_setup == ()


def test_a_non_string_entry_is_refused_and_removing_it_clears_it(
    kyo, git_project: Path
) -> None:
    _declare_setup(git_project, "worktree_setup:\n  - make test\n  - 17\n")
    with pytest.raises(KyoseiError) as excinfo:
        load_config(git_project)
    assert "entry 2" in str(excinfo.value)

    _undeclare_setup(git_project)
    _declare_setup(git_project, "worktree_setup:\n  - make test\n")
    assert load_config(git_project).worktree_setup == ("make test",)


def test_a_newline_inside_one_entry_is_refused_and_splitting_clears_it(
    kyo, git_project: Path
) -> None:
    """Each entry is rendered as one line in two line-oriented surfaces; a newline
    inside one would silently become two commands in the brief and one string here."""
    _declare_setup(git_project, 'worktree_setup:\n  - "make test\\nmake lint"\n')
    with pytest.raises(KyoseiError) as excinfo:
        load_config(git_project)
    assert "newline" in str(excinfo.value)

    _undeclare_setup(git_project)
    _declare_setup(git_project, "worktree_setup:\n  - make test\n  - make lint\n")
    assert load_config(git_project).worktree_setup == ("make test", "make lint")


# ============================================================ KYO-164
#
# A project can explain its setup commands, and the brief showed only the commands.
#
# This repository's own config declared:
#
#     worktree_setup:
#       - make test          # provisions .venv via the Makefile's stamp target
#       - cd apps/desktop && npm ci   # may leave electron/dist absent
#
# and the brief rendered the two commands and neither comment. The caveat is the part
# that matters: `npm ci` exits 0 in about two seconds while SKIPPING install scripts
# under npm's allow-scripts policy, so a worker who runs it and believes it has a working
# tree does not — `node node_modules/electron/install.js` is the missing step, and that is
# not inferable from the command. It cost three separate dispatches time, and was
# survivable only because a human kept repeating it in spawn prompts out of band.
#
# Same shape as KYO-158: YAML comments are not values, so anything
# that round-trips through a parsed structure loses them. KYO-158 could edit the config
# FILE in place; the brief cannot, because it renders into a different document. So the
# note becomes data — and the bare form keeps rendering exactly what it rendered before,
# which `SetupCommand` makes true by construction rather than by care.


def test_a_note_a_project_attaches_reaches_the_worker_beside_its_command(
    kyo, git_project: Path
) -> None:
    """KYO-164.c1, and c3: the brief carries the note, in the block, under the command it
    is about. Delete the note's rendering and this is what goes red."""
    _declare_setup(
        git_project,
        "worktree_setup:\n"
        "  - make test\n"
        "  - run: cd apps/desktop && npm ci\n"
        "    note: exits 0 in ~2s while SKIPPING install scripts; run"
        " `node node_modules/electron/install.js` after it\n",
    )
    record = new_record(kyo, criteria=["a"])
    assert kyo("dispatch", record, "--branch", "kyo-164-a").code == 0

    brief = _brief_for(kyo, record)
    lines = brief.splitlines()
    npm = "cd apps/desktop && npm ci"
    command_at = next(i for i, line in enumerate(lines) if line.strip() == npm)
    # Beside the command it is about, and beneath it — not in a separate list further
    # down, which is the distance the YAML comment already had.
    assert "SKIPPING install scripts" in lines[command_at + 1]
    assert lines[command_at + 1].lstrip().startswith("# ")
    assert "node node_modules/electron/install.js" in lines[command_at + 1]
    # And the brief says what a `#` line under a command is, so it is not read as one.
    assert "is the project's own note about it" in brief

    # The entry that declared no note gets none: a note is not invented for a command
    # that did not ask for one.
    bare_at = next(i for i, line in enumerate(lines) if line.strip() == "make test")
    assert not lines[bare_at + 1].lstrip().startswith("# ")


def test_the_note_is_parsed_off_the_entry_and_not_off_a_yaml_comment(
    kyo, git_project: Path
) -> None:
    """The reason the shape had to change at all, stated as a test. A trailing `#`
    comment is invisible to `yaml.safe_load`, so it cannot be the carrier however much
    it looks like one — and a project that writes one gets a bare command, silently,
    which is the state this record was filed from."""
    _declare_setup(git_project, "worktree_setup:\n  - make test  # provisions .venv\n")
    (command,) = load_config(git_project).worktree_setup
    assert command == "make test"
    assert command.note == "", "a YAML comment is not a value and never reaches anything"

    _declare_setup_replacing(
        git_project, "worktree_setup:\n  - run: make test\n    note: provisions .venv\n"
    )
    (command,) = load_config(git_project).worktree_setup
    assert command == "make test"
    assert command.note == "provisions .venv"


def test_a_project_that_declares_bare_commands_gets_exactly_what_it_got_before(
    kyo, git_project: Path
) -> None:
    """KYO-164.c2. Not "looks the same": the same bytes. A bare entry is a `str` that
    carries an empty note, so the block a note-less project gets cannot be changed by a
    renderer that now knows about notes — there is nothing for it to render."""
    assert _setup_lines(["make test", "npm ci"]) == ["        make test", "        npm ci"]
    assert _setup_lines([SetupCommand("make test"), SetupCommand("npm ci")]) == [
        "        make test",
        "        npm ci",
    ]
    # The whole bullet, which is what the worker actually reads: identical whether the
    # entries came through the mapping form with no note or as bare strings.
    plain = _worktree_tooling_bullet("wt", ["make test", "npm ci"])
    parsed = _worktree_tooling_bullet("wt", [SetupCommand("make test"), SetupCommand("npm ci")])
    assert plain == parsed
    assert "#" not in plain.split("worktree before")[-1], "no comment line appears"
    assert "is the project's own note about it" not in plain


def test_the_setup_entry_refusals_name_the_shape_they_want(git_project: Path) -> None:
    """Every refusal names the fix, because a config this key cannot parse is a project
    whose workers get a brief describing a tree nobody looked at (KYO-143's own reason,
    applied to the new form)."""
    cases = {
        "worktree_setup:\n  - notes: provisions .venv\n": ["no consumer reads", "notes"],
        "worktree_setup:\n  - note: provisions .venv\n": ["no 'run' key", "run: <the command>"],
        "worktree_setup:\n  - run: make test\n    note: ''\n": ["empty 'note'"],
        # Double-quoted, because single-quoted YAML would keep the backslash literal
        # and the entry would parse cleanly — a test that proved nothing.
        'worktree_setup:\n  - run: "make test\\nnpm ci"\n': ["contains a newline"],
        'worktree_setup:\n  - run: make test\n    note: "a\\nb"\n': ["note contains a newline"],
        "worktree_setup:\n  - 17\n": ["must be a command (a string) or a mapping"],
    }
    for block, expected in cases.items():
        _declare_setup_replacing(git_project, block)
        with pytest.raises(KyoseiError) as caught:
            load_config(git_project)
        for phrase in expected:
            assert phrase in str(caught.value), (block, str(caught.value))


def test_a_note_survives_the_rebuild_that_rewrites_the_config(kyo, git_project: Path) -> None:
    """KYO-158's lesson, which is the same lesson: a key that round-trips through a
    parsed structure loses whatever the round trip does not carry. The note is a value
    now, so `kyo rebuild` — which rewrites config.yml to stamp schema_version — carries
    it the way it carries every other optional key."""
    _declare_setup_replacing(
        git_project,
        "worktree_setup:\n  - run: cd apps/desktop && npm ci\n    note: skips install scripts\n",
    )
    rebuilt = kyo("rebuild", "--force")
    assert rebuilt.code == 0, rebuilt
    (command,) = load_config(git_project).worktree_setup
    assert command == "cd apps/desktop && npm ci"
    assert command.note == "skips install scripts"


# ------------------------------------------------ KYO-297: provisioning runs no suite

#: This repository's own checkout: its Makefile and the config its briefs are frozen from.
REPO = Path(__file__).resolve().parents[1]


def _make_targets(name: str) -> list[str]:
    """The recipe lines of one Makefile target, empty when the target only has
    prerequisites -- which is the whole property `venv` has to keep."""
    lines = (REPO / "Makefile").read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"{name}:"):
            recipe = []
            for body in lines[index + 1 :]:
                if not body.startswith("\t"):
                    break
                recipe.append(body.strip())
            return recipe
    raise AssertionError(f"the Makefile has no `{name}` target")


@pytest.mark.skipif(
    not store_path("config.yml").is_file(),
    reason="this repository's own store (and its config.yml) is not in this checkout",
)
def test_this_repos_worktree_setup_provisions_without_running_the_suite():
    """KYO-297.c1. `worktree_setup` once said `make test` (from KYO-143 on), so every
    frozen brief told its worker to run the whole suite while the same brief's standing rule
    told it not to. Two workers of one wave sat through it and each re-wrote its whole
    context when the five-minute prompt cache it outlived expired. The config is read the
    way a brief reads it, so a YAML edit that stops parsing fails here too."""
    store_root = store_path("config.yml").parent.parent
    commands = [step.run for step in load_config(store_root).worktree_setup]
    assert commands, "this repository declares worktree_setup; an empty one is a regression"
    suite_runners = [
        run
        for run in commands
        if "make test" in run or "make all" in run or "pytest" in run and "::" not in run
    ]
    assert suite_runners == [], (
        "worktree_setup runs the whole Python suite as provisioning again: "
        f"{suite_runners}. `make venv` provisions and runs nothing (KYO-297)."
    )
    assert any(run.split()[:2] == ["make", "venv"] for run in commands)


def test_make_venv_is_the_stamp_target_and_nothing_else():
    """The target the config names has to stay a name for the stamp: a recipe line added
    to it later is how it quietly becomes `make test` again."""
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "\nvenv: $(STAMP)\n" in makefile
    assert _make_targets("venv") == []
