"""KYO-558: bare `kyo init` in a new project writes nothing and points to onboarding.

A consumer report found that an agent asked to "run kyo init" ran it without a terminal,
and every round-one setup decision -- the store layout among them, the one that is costly
to undo -- was made silently. Hooks, the skill and the CLAUDE.md section were not
installed and nothing said so. The shape chosen: no interactive prompt, the same text on
and off a terminal, and `--defaults` as the scripted route. Every test here runs under
the suite's temporary ``$KYOSEI_HOME`` (``conftest.store_home``).
"""

from __future__ import annotations

import hashlib
import os
import pty
import re
import subprocess
import sys
from pathlib import Path

import pytest

from kyosei import cmd_onboard, scaffold
from kyosei.cmd_onboard import Item
from kyosei.config import load_config
from kyosei.install_skill import (
    CLAUDE_MD_BEGIN,
    CLAUDE_MD_END,
    CLAUDE_MD_SECTION,
    CLAUDE_MD_SETUP_INCOMPLETE_SECTION,
    claude_md_drift,
)

from .conftest import git_init, invoke

REPO_ROOT = Path(__file__).resolve().parents[1]


def tree_state(*roots: Path) -> dict[str, str]:
    """Every path under ``roots`` with its content hash, so "wrote nothing" is a diff."""
    state = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file():
                state[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                state[str(path)] = "dir"
    return state


def flat(text: str) -> str:
    """``text`` with every run of whitespace one space: argparse wraps help text."""
    return " ".join(text.split())


def check_items(code: Path) -> list[dict]:
    result = invoke(["--root", str(code), "onboard", "--check", "--json"])
    assert result.code == 0, result
    (project,) = result.json["projects"]
    return project["items"]


@pytest.fixture
def fresh(tmp_path: Path) -> Path:
    """A new project folder: one source file, no git, no store."""
    code = tmp_path / "my-app"
    code.mkdir()
    (code / "main.py").write_text("print('hi')\n", encoding="utf-8")
    return code


# ------------------------------------------------------------------ c1: writes nothing


@pytest.mark.parametrize(
    "extra",
    [[], ["--prefix", "MYAP"], ["--no-git-init"], ["--json"]],
    ids=["bare", "prefix-only", "no-git-init-only", "json"],
)
def test_bare_init_in_a_new_project_writes_nothing_and_exits_nonzero(
    fresh: Path, tmp_path: Path, store_home: Path, extra: list[str]
) -> None:
    """c1. None of --defaults, --in-repo, --store or --adopt: no store, no `.kyosei`, no
    `git init`, no registry entry, no CLAUDE.md -- the whole tmp tree is unchanged."""
    before = tree_state(tmp_path)
    result = invoke(["init", str(fresh), *extra])
    assert result.code != 0, result
    assert tree_state(tmp_path) == before
    assert not (fresh / ".git").exists()
    assert not (fresh / ".kyosei").exists()
    assert not (fresh / "CLAUDE.md").exists()
    assert not store_home.exists()


def test_bare_init_of_a_directory_that_does_not_exist_creates_nothing(
    tmp_path: Path, store_home: Path
) -> None:
    before = tree_state(tmp_path)
    assert invoke(["init", str(tmp_path / "not-yet")]).code != 0
    assert tree_state(tmp_path) == before
    assert not (tmp_path / "not-yet").exists()


def test_rerunning_init_where_a_store_link_exists_behaves_as_before(fresh: Path) -> None:
    """The refusal is for a project with no store; a re-run keeps its old answer."""
    assert invoke(["init", "--defaults", str(fresh)]).code == 0
    again = invoke(["init", str(fresh)])
    assert again.code == 1, again
    assert "already links to a store" in again.err
    assert "kyo onboard" not in again.err


# ------------------------------------------------------------------ c2: --defaults


@pytest.mark.parametrize("prefix", [None, "APP"])
def test_defaults_does_what_bare_init_used_to(
    fresh: Path, tmp_path: Path, store_home: Path, prefix: str | None
) -> None:
    """c2. The pre-KYO-558 bare run: the split store at $KYOSEI_HOME/<dir>, its own git
    repository with the scaffold committed, the prefix from the directory name (or
    --prefix), the code repository made and its identity recorded, the project
    registered -- and the same scaffold `init_layout`'s defaults write."""
    argv = ["init", "--defaults", str(fresh), "--json"]
    if prefix:
        argv += ["--prefix", prefix]
    result = invoke(argv)
    assert result.code == 0, result
    data = result.json
    expected_prefix = prefix or "MYAP"
    assert data["layout"] == "split"
    assert Path(data["store_root"]) == store_home / "my-app"
    assert data["project_prefix"] == expected_prefix
    assert (fresh / ".kyosei").is_file()
    assert (fresh / ".git").is_dir()
    assert data["code_repo_id"] is not None
    assert load_config(Path(data["store_root"])).code_repo_id == data["code_repo_id"]
    assert data["registry"]["registered"] is True
    store_log = subprocess.run(
        ["git", "-C", data["store_root"], "log", "--oneline"], capture_output=True, text=True
    )
    assert store_log.stdout.strip(), "the scaffold is committed in the store"

    twin = tmp_path / "twin" / "my-app"
    twin.mkdir(parents=True)
    direct = scaffold.init_layout(
        twin, prefix=prefix, store_dir=tmp_path / "twin-store", environ=dict(os.environ)
    )
    assert sorted(data["written"]) == sorted(
        str(p.relative_to(direct.store_root)) for p in direct.written
    )
    assert direct.prefix == expected_prefix


# ------------------------------------------------------------------ c7, c8: the refusal


def test_the_refusal_names_three_routes_with_the_derived_path_and_prefix(
    fresh: Path, store_home: Path
) -> None:
    """c7, and written as the start of setup: onboard first and recommended, then
    --defaults with what it would make, then --in-repo."""
    result = invoke(["init", str(fresh)])
    err = result.err
    onboard = err.index("kyo onboard`")
    defaults = err.index(f"`kyo init --defaults {fresh}`")
    in_repo = err.index(f"`kyo init --in-repo {fresh}`")
    assert onboard < defaults < in_repo
    assert "recommended" in err[onboard:defaults]
    assert str(store_home / "my-app") in err[defaults:in_repo]
    assert "MYAP-1" in err[defaults:in_repo]
    assert "nothing" in err.splitlines()[0]


def test_the_routes_run_as_printed_from_where_they_were_printed(
    fresh: Path, monkeypatch
) -> None:
    """With no directory argument the routes carry none, and a carried --prefix stays."""
    monkeypatch.chdir(fresh)
    err = invoke(["init", "--prefix", "APP"]).err
    assert "  `kyo onboard`" in err
    assert "  `kyo init --defaults --prefix APP`" in err
    assert "APP-1" in err
    assert invoke(["init", "--defaults", "--prefix", "APP"]).code == 0


def test_the_refusal_says_defaults_would_link_a_store_that_already_exists(
    tmp_path: Path, store_home: Path
) -> None:
    origin = tmp_path / "one" / "proj"
    origin.mkdir(parents=True)
    (origin / "main.py").write_text("x = 1\n", encoding="utf-8")
    git_init(origin)
    assert invoke(["init", "--defaults", str(origin)]).code == 0
    clone = tmp_path / "two" / "proj"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    err = invoke(["init", str(clone)]).err
    assert f"link the store that already exists at {store_home / 'proj'}" in err
    linked = invoke(["init", "--defaults", str(clone), "--json"])
    assert linked.code == 0, linked
    assert linked.json["adopted"] is True


def _run_kyo(argv: list[str], *, terminal: bool, env: dict[str, str]) -> tuple[int, str]:
    """Run `kyo` as a separate process, with every standard stream on a pseudo-terminal
    or on pipes, and return its exit code and everything it printed."""
    command = [sys.executable, "-m", "kyosei", *argv]
    if not terminal:
        done = subprocess.run(command, capture_output=True, text=True, env=env, timeout=60)
        return done.returncode, done.stdout + done.stderr
    leader, follower = pty.openpty()
    probe = (
        "import os, sys; assert all(os.isatty(n) for n in (0, 1, 2)), 'not a terminal'; "
        "from kyosei.cli import main; sys.exit(main(sys.argv[1:]))"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", probe, *argv],
        stdin=follower,
        stdout=follower,
        stderr=follower,
        env=env,
        close_fds=True,
    )
    os.close(follower)
    chunks = []
    while True:
        try:
            chunk = os.read(leader, 65536)
        except OSError:  # EIO: the follower side closed
            break
        if not chunk:
            break
        chunks.append(chunk)
    os.close(leader)
    code = process.wait(timeout=60)
    return code, b"".join(chunks).decode("utf-8").replace("\r\n", "\n")


def test_the_refusal_is_the_same_text_with_and_without_a_terminal(
    fresh: Path, tmp_path: Path, store_home: Path
) -> None:
    """c8. No prompt in Python, so an agent (no terminal) and a person (a
    terminal) read the same routes. The terminal run asserts it really had one."""
    env = {**os.environ, "KYOSEI_HOME": str(store_home)}
    env.pop("KYOSEI_ROOT", None)
    before = tree_state(tmp_path)
    piped_code, piped = _run_kyo(["init", str(fresh)], terminal=False, env=env)
    tty_code, on_tty = _run_kyo(["init", str(fresh)], terminal=True, env=env)
    assert "not a terminal" not in on_tty
    assert piped_code == tty_code == 64
    assert "kyo onboard" in piped
    assert on_tty == piped
    assert tree_state(tmp_path) == before


# ------------------------------------------------------------------ c3, c6, c10: output


@pytest.mark.parametrize("layout", [["--defaults"], ["--in-repo"]])
def test_init_ends_with_onboard_checks_missing_items_and_kyo_onboard(
    fresh: Path, layout: list[str]
) -> None:
    """c3. Every project item `kyo onboard --check` reads as missing or behind, each with
    its fix, in the check's own order, then `kyo onboard` -- the last lines printed."""
    result = invoke(["init", *layout, str(fresh)])
    assert result.code == 0, result
    expected = [i for i in check_items(fresh) if i["status"] in ("missing", "behind")]
    assert expected, "a fresh project has setup left to do"
    lines = result.out.rstrip("\n").splitlines()
    start = next(n for n, line in enumerate(lines) if line.startswith("Setup incomplete"))
    block = lines[start:]
    rendered = []
    for item in expected:
        rendered.append(f"  {item['label']}: {item['status']} -- {item['detail']}")
        rendered.append(f"      fix: {item['fix']}")
    assert block[1:-1] == rendered
    assert "`kyo onboard`" in block[-1]
    as_json = invoke(["init", "--defaults", "--json", str(fresh.parent / "other")])
    assert as_json.code == 0, as_json
    labels = [i["label"] for i in as_json.json["setup_incomplete"]]
    assert labels == [
        i["label"]
        for i in check_items(fresh.parent / "other")
        if i["status"] in ("missing", "behind")
    ]


def test_init_that_links_an_existing_store_ends_with_the_list_too(
    fresh: Path, tmp_path: Path
) -> None:
    """c3's "or links": a second checkout adopting the store by --store."""
    first = invoke(["init", "--defaults", str(fresh), "--json"])
    store = first.json["store_root"]
    other = tmp_path / "elsewhere" / "my-app"
    other.mkdir(parents=True)
    linked = invoke(["init", str(other), "--store", store])
    assert linked.code == 0, linked
    assert linked.out.rstrip("\n").splitlines()[-1].startswith("Next: `kyo onboard`")
    assert "Setup incomplete" in linked.out


def test_no_list_when_every_project_item_is_present(fresh: Path, monkeypatch) -> None:
    """c10. The list is the check's, so a project the check finds complete gets none."""
    present = [Item(label, "present", "fine") for label in cmd_onboard.PROJECT_LABELS]
    monkeypatch.setattr(cmd_onboard, "project_items", lambda layout: present)
    result = invoke(["init", "--defaults", str(fresh)])
    assert result.code == 0, result
    assert "Setup incomplete" not in result.out
    assert "kyo onboard`, the guided setup" not in result.out
    data = invoke(["init", "--defaults", "--json", str(fresh.parent / "b")]).json
    assert data["setup_incomplete"] == []


def test_not_needed_and_not_run_items_are_not_setup_left_to_do(monkeypatch) -> None:
    """c10's other edge: only missing and behind are listed."""
    items = [
        Item("kyo doctor", "not-run", "never run by --check", "`kyo doctor`"),
        Item("scopes", "not-needed", "one component"),
        Item("hooks", "present", "current"),
    ]
    monkeypatch.setattr(cmd_onboard, "project_items", lambda layout: items)
    assert cmd_onboard.setup_incomplete(None) == ([], [])


def test_init_output_leads_with_what_it_did_and_points_at_the_explanation(
    fresh: Path, tmp_path: Path
) -> None:
    """c6. The committed-prose list and the worktree paragraph moved behind one line
    naming `kyo init --explain`; that prints them for this project and writes nothing."""
    result = invoke(["init", "--defaults", str(fresh)])
    assert result.code == 0, result
    lines = result.out.splitlines()
    assert lines[0].startswith("store: ")
    assert lines[1].startswith("initialised Kyosei project for ")
    for explanation in (
        "OUTSIDE the code repository",
        "keeps out of the code repository, none of which",
        "Dispatch worktrees will be created",
        "kyo redact",
    ):
        assert explanation not in result.out
    pointers = [line for line in lines if "kyo init --explain" in line]
    assert len(pointers) == 1
    end_of_done = next(n for n, line in enumerate(lines) if line.startswith("Setup incomplete"))
    assert lines.index(pointers[0]) < end_of_done
    # The whole output was 41 lines for this case before KYO-558, the
    # explanation ~15 of them; what is left before the next steps is the done block.
    assert len(lines[:end_of_done]) <= 20, lines[:end_of_done]

    before = tree_state(tmp_path)
    explained = invoke(["init", str(fresh), "--explain"])
    assert explained.code == 0, explained
    assert tree_state(tmp_path) == before
    assert "OUTSIDE the code repository" in explained.out
    assert "Dispatch worktrees will be created" in explained.out


def test_explain_in_a_new_project_describes_both_layouts_and_writes_nothing(
    fresh: Path, tmp_path: Path, store_home: Path
) -> None:
    before = tree_state(tmp_path)
    result = invoke(["init", str(fresh), "--explain", "--json"])
    assert result.code == 0, result
    assert tree_state(tmp_path) == before
    assert [entry["layout"] for entry in result.json["layouts"]] == ["split", "in-repo"]
    text = invoke(["init", str(fresh), "--explain"]).out
    assert "OUTSIDE the code repository" in text and "INSIDE the code repository" in text
    only = invoke(["init", str(fresh), "--in-repo", "--explain"]).out
    assert "INSIDE" in only and "OUTSIDE" not in only


# ------------------------------------------------------------------ c4, c11: CLAUDE.md


def test_a_fresh_defaults_init_writes_the_marked_setup_incomplete_section(fresh: Path) -> None:
    """c4. A later session in this repository is told it uses Kyosei and that setup
    continues at `kyo onboard --check`, even though nothing else was installed."""
    result = invoke(["init", "--defaults", str(fresh), "--json"])
    assert result.code == 0, result
    text = (fresh / "CLAUDE.md").read_text(encoding="utf-8")
    assert text == CLAUDE_MD_SETUP_INCOMPLETE_SECTION + "\n"
    section = text[text.index(CLAUDE_MD_BEGIN) : text.index(CLAUDE_MD_END)]
    assert "This project uses Kyosei" in section
    assert "`kyo onboard --check`" in section
    assert result.json["claude_md"] == str(fresh / "CLAUDE.md")
    tracked = subprocess.run(
        ["git", "-C", str(fresh), "ls-files"], capture_output=True, text=True
    ).stdout.split()
    assert "CLAUDE.md" in tracked, "committed with the other files init wrote here"


def test_inits_section_names_the_plugin_route_and_the_copies_only_with_their_flag() -> None:
    """KYO-613.c1. The section's advice holds on both routes: it names `kyo harness
    install claude-code`, which removes it on the plugin route, and every copies command
    it names carries `--copies`, since without it they refuse outside a project on the
    copies. It must not name `kyo install-skill` as the only way to replace it."""
    from kyosei.install_skill import (
        CLAUDE_MD_SETUP_INCOMPLETE_BODY,
        SUPERSEDED_SETUP_INCOMPLETE_BODIES,
        claude_md_carries_init_section,
    )

    body = CLAUDE_MD_SETUP_INCOMPLETE_BODY
    assert "`kyo harness install claude-code`" in body
    named = re.findall(r"`(kyo install-(?:skill|hooks)[^`]*)`", body)
    assert named, "the copies route is named too"
    assert all("--copies" in command for command in named), named
    assert "`kyo install-skill` replaces" not in body
    # A project kyo init made under an earlier build is still recognised as carrying it.
    for old in SUPERSEDED_SETUP_INCOMPLETE_BODIES:
        assert claude_md_carries_init_section(f"{CLAUDE_MD_BEGIN}\n{old}\n{CLAUDE_MD_END}\n")


def test_init_keeps_an_existing_claude_md_and_an_existing_section(fresh: Path) -> None:
    (fresh / "CLAUDE.md").write_text("# Mine\n\nKeep this.\n", encoding="utf-8")
    assert invoke(["init", "--in-repo", str(fresh)]).code == 0
    text = (fresh / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith("# Mine\n\nKeep this.\n")
    assert CLAUDE_MD_SETUP_INCOMPLETE_SECTION in text

    other = fresh.parent / "other"
    other.mkdir()
    full = f"# Theirs\n\n{CLAUDE_MD_SECTION}\n"
    (other / "CLAUDE.md").write_text(full, encoding="utf-8")
    result = invoke(["init", "--in-repo", "--json", str(other)])
    assert result.json["claude_md"] is None
    assert (other / "CLAUDE.md").read_text(encoding="utf-8") == full


def _enable_plugin(code: Path, rel: str = ".claude/settings.local.json") -> None:
    """Enable ``kyosei@kyosei`` for ``code`` the way `kyo harness install` leaves it."""
    import json

    from kyosei.cmd_harness import PLUGIN_ID

    path = code / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabledPlugins": {PLUGIN_ID: True}}), encoding="utf-8")


@pytest.mark.parametrize(
    "rel", [".claude/settings.local.json", ".claude/settings.json"], ids=["local", "project"]
)
def test_init_writes_no_section_where_the_plugin_is_enabled(fresh: Path, rel: str) -> None:
    """KYO-634.c1, fresh init: the section's advice would be false where the plugin is on."""
    (fresh / "CLAUDE.md").write_text("# Mine\n", encoding="utf-8")
    _enable_plugin(fresh, rel)
    result = invoke(["init", "--defaults", str(fresh), "--json"])
    assert result.code == 0, result
    assert result.json["claude_md"] is None
    assert (fresh / "CLAUDE.md").read_text(encoding="utf-8") == "# Mine\n"


def test_init_adopting_a_store_writes_no_section_where_the_plugin_is_enabled(
    fresh: Path, tmp_path: Path
) -> None:
    """KYO-634.c1, the case seen: `kyo init --store` adopting an existing store in a
    checkout whose plugin is enabled at local scope, with and without a CLAUDE.md."""
    first = invoke(["init", "--defaults", str(fresh), "--json"])
    store = first.json["store_root"]
    for name, existing in (("with-md", "# Theirs\n"), ("without-md", None)):
        other = tmp_path / name / "my-app"
        other.mkdir(parents=True)
        if existing is not None:
            (other / "CLAUDE.md").write_text(existing, encoding="utf-8")
        _enable_plugin(other)
        linked = invoke(["init", str(other), "--store", store, "--json"])
        assert linked.code == 0, linked
        assert linked.json["adopted"] is True
        assert linked.json["claude_md"] is None
        md = other / "CLAUDE.md"
        if existing is None:
            assert not md.exists()
        else:
            assert md.read_text(encoding="utf-8") == existing


def test_init_adopting_a_store_without_the_plugin_still_writes_the_section(
    fresh: Path, tmp_path: Path
) -> None:
    """KYO-634.c2: no plugin enabled (a disabled entry counts as none), so the section
    is written on the adopt path as before."""
    first = invoke(["init", "--defaults", str(fresh), "--json"])
    assert CLAUDE_MD_SETUP_INCOMPLETE_SECTION in (fresh / "CLAUDE.md").read_text("utf-8")
    other = tmp_path / "elsewhere" / "my-app"
    (other / ".claude").mkdir(parents=True)
    (other / ".claude" / "settings.local.json").write_text(
        '{"enabledPlugins": {"kyosei@kyosei": false}}', encoding="utf-8"
    )
    linked = invoke(["init", str(other), "--store", first.json["store_root"], "--json"])
    assert linked.code == 0, linked
    assert linked.json["claude_md"] == str(other / "CLAUDE.md")
    assert (other / "CLAUDE.md").read_text(encoding="utf-8") == (
        CLAUDE_MD_SETUP_INCOMPLETE_SECTION + "\n"
    )


def test_install_skill_replaces_inits_section_and_doctor_calls_neither_drift(
    fresh: Path,
) -> None:
    """c11. The section sits inside install-skill's own markers, so install-skill
    replaces it like any older one; and doctor's CLAUDE.md check does not report it."""
    (fresh / "CLAUDE.md").write_text("# Mine\n", encoding="utf-8")
    assert invoke(["init", "--defaults", str(fresh)]).code == 0
    assert claude_md_drift(fresh) == []
    doctor = invoke(["--root", str(fresh), "doctor", "--json"])
    assert "CLAUDE.md" not in doctor.out + doctor.err, doctor
    # KYO-596: with nothing copied and no plugin, the readback is the harness item, on
    # the plugin and missing; the section item belongs to a project on the copies.
    items = {i["label"]: i for i in check_items(fresh)}
    assert "CLAUDE.md section" not in items
    assert items["harness"]["status"] == "missing"

    assert invoke(["--root", str(fresh), "install-skill", "--copies"]).code == 0
    text = (fresh / "CLAUDE.md").read_text(encoding="utf-8")
    assert text == f"# Mine\n\n{CLAUDE_MD_SECTION}\n"
    assert CLAUDE_MD_SETUP_INCOMPLETE_SECTION not in text
    assert claude_md_drift(fresh) == []
    doctor = invoke(["--root", str(fresh), "doctor", "--json"])
    assert "CLAUDE.md" not in doctor.out + doctor.err, doctor


# ------------------------------------------------------------------ c5, c12: help


def test_init_help_says_setup_starts_at_kyo_onboard() -> None:
    """c5: the text an agent asked to "run kyo init" reads first."""
    text = flat(invoke(["init", "--help"]).out)
    assert "Setup starts at `kyo onboard`" in text
    assert "--defaults" in text


def test_top_level_help_describes_onboard_as_where_setup_starts() -> None:
    """c12."""
    text = flat(invoke(["--help"]).out)
    assert re.search(r"onboard guided project setup, where a new project starts", text), text


# ------------------------------------------------------------------ c9, c13: prose


def test_the_onboarding_prompt_passes_a_layout_flag_according_to_b1() -> None:
    """c9. Step 4's init runs `--defaults` or `--in-repo` by [B1]'s answer, and no
    `kyo init` the prompt tells the agent to run is the bare one that now refuses."""
    prompt = cmd_onboard.render_prompt()
    step = prompt[prompt.index("## Step 4") : prompt.index("## Step 5")]
    init_line = next(line for line in step.splitlines() if line.startswith("2. `kyo init"))
    assert "`kyo init --defaults --prefix <PREFIX>`" in init_line
    assert "**[B1]**" in init_line
    assert "`kyo init --in-repo --prefix <PREFIX>`" in step
    for command in re.findall(r"`(kyo init[^`]*)`", prompt):
        flags = command.split()[2:]
        assert not flags or {"--defaults", "--in-repo", "--adopt", "--no-git-init"} & set(
            flags
        ), command


@pytest.mark.parametrize(
    "doc",
    ["README.md", "docs/guide/04-getting-started.md", "docs/guide/setup-decisions.md"],
)
def test_the_docs_describe_bare_inits_new_behaviour(doc: str) -> None:
    """c13."""
    text = flat((REPO_ROOT / doc).read_text(encoding="utf-8"))
    assert "Bare `kyo init`" in text or "bare `kyo init`" in text
    assert "writes nothing" in text
    assert "`kyo init --defaults`" in text
    assert "`kyo onboard`" in text
