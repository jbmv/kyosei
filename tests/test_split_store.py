"""KYO-54: the store may live outside the code repository.

The defect this file is the mechanism for: `kyo init` used to scaffold `ideas/`
and `kyosei/` inside the project repository and README told the consumer to
commit them, so following the documentation committed the intake layer, every
record description, shift notes, evidence stored verbatim and the frozen briefs
quoting all of them to the same repository as the code — by default, and
discovered only by reading README carefully or by reviewing a diff that already
contained them.

Gitignoring the store was rejected on KYO-54 because it destroys the store
instead of relocating it (`kyosei.db` is already gitignored, so the export is the
only durable copy). The fix is *where* the store is committed, not whether. So
every test below asks one of two questions: does the store still work when it
lives somewhere else, and does the tool still say true things about what it is
protecting when it does.

KYO-74, at the bottom of this file, is the second question asked of the seam this
record created rather than of the store it moved. A split store is pulled, so its
database can be legitimately BEHIND its export — a direction that could not arise
while one process owned both files, and which every read surface and every
`kyo doctor` message about exports was written on the assumption of.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from kyosei import config as config_module
from kyosei import install_hooks, install_prose
from kyosei.config import (
    LAYOUT_IN_REPO,
    LAYOUT_SPLIT,
    LAYOUT_UNCONFIGURED,
    STORE_LINK_NAME,
)
from kyosei.contracts import WORKER_REPORT_PATH

from .conftest import export_of, git_init, invoke, new_record


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


def _in_code(code: Path):
    def run(*args: str, stdin: str | None = None):
        return invoke(["--root", str(code), *args], stdin=stdin)

    return run


# --------------------------------------------------------------------- KYO-54.c5
# The default layout, and the alternative offered rather than hidden.


def test_init_defaults_to_a_split_store_under_the_store_home(tmp_path: Path) -> None:
    code = tmp_path / "my-app"
    code.mkdir()
    result = invoke(["init", "--defaults", str(code), "--json"])
    assert result.code == 0, result

    store = Path(result.json["store_root"])
    assert result.json["layout"] == LAYOUT_SPLIT
    assert store == config_module.store_home() / "my-app"
    assert (store / "kyosei" / "config.yml").is_file()
    assert (store / "ideas" / ".gitignore").is_file()

    # The point of the whole record: none of it is in the code repository.
    assert not (code / "kyosei").exists()
    assert not (code / "ideas").exists()
    # Two files, and the second is not store content: KYO-206's `.gitignore` entry for
    # the worker report path belongs to the CODE repository under both layouts, because
    # that is the repository a dispatch worktree is a checkout of.
    # KYO-507: a directory in no repository is made one, so `.git` is there too.
    # KYO-558.c4: and CLAUDE.md, whose marked section says setup is incomplete -- a
    # pointer, not store content.
    assert sorted(p.name for p in code.iterdir()) == sorted(
        [STORE_LINK_NAME, ".gitignore", ".git", "CLAUDE.md"]
    )
    assert WORKER_REPORT_PATH in (code / ".gitignore").read_text(encoding="utf-8")


def test_the_split_store_is_its_own_git_repository_with_the_scaffold_committed(
    tmp_path: Path,
) -> None:
    """KYO-54.c5. A store that is not committed is the durability loss this
    record refused, so `kyo init` does not leave that to the consumer."""
    code = tmp_path / "proj"
    code.mkdir()
    result = invoke(["init", "--defaults", str(code), "--json"])
    store = Path(result.json["store_root"])

    assert (store / ".git").is_dir()
    tracked = _git(store, "ls-files").stdout.split()
    assert "kyosei/config.yml" in tracked
    assert "ideas/.gitignore" in tracked
    # The database is derived, and stays gitignored in the store repo too.
    assert "kyosei/kyosei.db" not in tracked


def test_init_in_repo_is_an_explicit_alternative_that_still_works(tmp_path: Path) -> None:
    """KYO-54.c5's other half: choosing the in-repo layout is one documented flag,
    not a hidden one, and it produces exactly the pre-KYO-54 layout."""
    code = tmp_path / "proj"
    result = invoke(["init", str(code), "--in-repo", "--json"])
    assert result.code == 0, result
    assert result.json["layout"] == LAYOUT_IN_REPO
    assert result.json["store_root"] == str(code)
    assert (code / "kyosei" / "config.yml").is_file()
    assert (code / "ideas" / ".gitignore").is_file()
    assert not (code / STORE_LINK_NAME).exists()


def test_in_repo_is_named_in_init_help_and_in_the_default_run(tmp_path: Path) -> None:
    """"An explicit alternative rather than a hidden flag" is a claim about what a
    consumer is *told*, so it is checked where they would find out: `--help`, and
    the output of the run that chose the other way for them."""
    help_text = invoke(["init", "--help"]).out
    assert "--in-repo" in help_text
    assert "~/kyosei/<project>/" in help_text

    # KYO-558.c7: the run that used to choose the other way for them now chooses
    # nothing, and names the alternative before anything is written.
    code = tmp_path / "proj"
    code.mkdir()
    result = invoke(["init", str(code)])
    assert result.code != 0, result
    assert "kyo init --in-repo" in result.err
    # And the run that does choose the default still points at it, via --explain.
    assert invoke(["init", "--defaults", str(code)]).code == 0
    assert "kyo init --in-repo" in invoke(["init", str(code), "--explain"]).out


def test_in_repo_and_store_contradict_each_other(tmp_path: Path) -> None:
    result = invoke(["init", str(tmp_path / "p"), "--in-repo", "--store", str(tmp_path / "s")])
    assert result.code == 64, result
    assert "--in-repo and --store" in result.err


# --------------------------------------------------------------------- KYO-54.c6
# Visible, never a dotfolder, and overridable.


def test_the_default_store_home_is_visible_and_under_the_home_directory(monkeypatch) -> None:
    """KYO-54.c6: `~/kyosei`, not `~/.kyosei`. `ideas/` is a folder of
    documents a human opens by hand; a dotfolder is conventionally machine-managed
    config a user is not expected to browse."""
    monkeypatch.delenv(config_module.STORE_HOME_ENV, raising=False)
    assert config_module.store_home() == Path.home() / "kyosei"
    assert config_module.default_store_dir("my-app") == Path.home() / "kyosei" / "my-app"
    for part in config_module.default_store_dir("my-app").parts[-2:]:
        assert not part.startswith("."), "the store directory must never be hidden"


def test_the_store_home_is_overridable_by_configuration(tmp_path: Path, monkeypatch) -> None:
    elsewhere = tmp_path / "somewhere-else"
    monkeypatch.setenv(config_module.STORE_HOME_ENV, str(elsewhere))
    assert config_module.store_home() == elsewhere

    code = tmp_path / "proj"
    code.mkdir()
    result = invoke(["init", "--defaults", str(code), "--json"])
    assert Path(result.json["store_root"]) == elsewhere / "proj"


def test_store_flag_overrides_the_location_for_one_project(tmp_path: Path) -> None:
    code = tmp_path / "proj"
    code.mkdir()
    target = tmp_path / "vaults" / "proj-store"
    result = invoke(["init", str(code), "--store", str(target), "--json"])
    assert result.code == 0, result
    assert Path(result.json["store_root"]) == target
    assert (target / "kyosei" / "config.yml").is_file()
    # And the link is what carries that choice to every later command.
    assert invoke(["--root", str(code), "list", "--json"]).code == 0


def test_the_store_link_names_the_store_portably(tmp_path: Path, monkeypatch) -> None:
    """The link file is committed to the code repository, so it must not pin one
    machine's absolute path into a repository everyone clones — the same defect
    KYO-52 names inside the store."""
    home_store = Path.home() / "kyosei-test-portability"
    monkeypatch.setenv(config_module.STORE_HOME_ENV, str(home_store))
    text = config_module.render_store_link(home_store / "proj")
    assert "store: ~/kyosei-test-portability/proj" in text
    assert str(Path.home()) not in text.split("store:")[-1]


# --------------------------------------------------------------------- KYO-54.c7
# One repository per project.


def test_a_second_init_does_not_reuse_the_first_project_s_repository(tmp_path: Path) -> None:
    """KYO-54.c7. Per-project repositories were chosen for a concrete reason:
    this project's own store already discloses an unrelated project's filesystem
    location because a relay quoted a path. One repository spanning every project
    would make that structural rather than accidental."""
    for name in ("alpha", "beta"):
        (tmp_path / name).mkdir()
        result = invoke(
            ["init", "--defaults", str(tmp_path / name), "--prefix", name[:3].upper(), "--json"]
        )
        assert result.code == 0, result

    alpha = config_module.store_home() / "alpha"
    beta = config_module.store_home() / "beta"
    assert (alpha / ".git").is_dir() and (beta / ".git").is_dir()

    alpha_top = _git(alpha, "rev-parse", "--show-toplevel").stdout.strip()
    beta_top = _git(beta, "rev-parse", "--show-toplevel").stdout.strip()
    assert alpha_top != beta_top

    # Neither repository can see the other's records, now or later.
    assert _git(alpha, "ls-files").stdout == _git(beta, "ls-files").stdout
    invoke(["--root", str(tmp_path / "alpha"), "new", "bug", "--title", "only alpha's"])
    assert "only alpha's" not in _git(beta, "log", "-p").stdout
    assert not any(p.name == "beta" for p in alpha.rglob("*"))


def test_init_refuses_a_store_that_would_land_inside_another_repository(
    tmp_path: Path, monkeypatch
) -> None:
    """The mechanism behind c7: nesting is refused at `kyo init`, not discovered
    later in somebody's diff. Removing it makes the test above pass by luck."""
    shared = tmp_path / "one-big-repo"
    shared.mkdir()
    (shared / "README.md").write_text("not a store\n", encoding="utf-8")
    git_init(shared)
    monkeypatch.setenv(config_module.STORE_HOME_ENV, str(shared))

    code = tmp_path / "proj"
    code.mkdir()
    result = invoke(["init", "--defaults", str(code)])
    assert result.code == 1, result
    assert "its own git repository" in result.err
    assert "--store DIR" in result.err
    assert not (code / STORE_LINK_NAME).exists()


def test_init_refuses_to_relink_a_project_that_already_has_a_store(tmp_path: Path) -> None:
    code = tmp_path / "proj"
    code.mkdir()
    assert invoke(["init", "--defaults", str(code)]).code == 0
    again = invoke(["init", str(code)])
    assert again.code == 1
    assert STORE_LINK_NAME in again.err


# --------------------------------------------------------------------- KYO-54.c1
# Every command works unchanged under the split layout.


def test_every_store_command_works_from_the_code_repository(split_project) -> None:
    """KYO-54.c1. Driven through `--root <code repo>` — a consumer never says
    "the store is over there" twice; the link says it once."""
    code, store = split_project
    kyo = _in_code(code)

    created = kyo(
        "new", "bug", "--title", "Widget explodes", "--criteria-file", "-", "--json",
        stdin="it stops exploding\n",
    )
    assert created.code == 0, created
    record_id = created.json["record"]["id"]

    assert kyo("show", record_id).code == 0
    assert kyo("list", "--json").code == 0
    assert kyo("edit", record_id, "--note-file", "-", stdin="a note\n").code == 0
    assert kyo("attach", record_id, "--file", "-", stdin="console output\n").code == 0
    assert kyo("note", "--shift", "--file", "-", stdin="a shift note\n").code == 0
    assert kyo("sweep").code == 0
    assert kyo("brief").code == 0
    assert kyo("next").code == 0
    assert kyo("map").code == 0
    assert kyo("export", "--refresh").code == 0
    assert kyo("doctor").code == 0

    # Everything the store wrote landed in the store, and nothing in the code repo.
    assert (store / "kyosei" / "export" / f"{record_id}.json").is_file()
    # KYO-268.c2: the events ledger is a directory of bounded shards, and what this
    # test is about is WHERE it landed, not how many files it is.
    assert list((store / "kyosei" / "export" / "_events").glob("*.jsonl"))
    assert not (code / "kyosei").exists()
    assert not (code / "ideas").exists()


def test_promotion_reads_the_ideas_layer_from_the_store(split_project) -> None:
    """`ideas/` moved with the store, so promotion must follow it there — and the
    promoted record's provenance line must name the idea's path in the store.

    ``--keep``: this is specifically pinning the frontmatter stamp's shape, which
    KYO-228 made opt-in; it also proves the split-layout path resolution holds for the
    flag, not only for the (now default) consuming path.
    """
    code, store = split_project
    idea = store / "ideas" / "widget.md"
    idea.write_text("# A widget idea\n\nsomething worth doing\n", encoding="utf-8")
    touches = store / "touches.txt"
    touches.write_text("src/widget.py\n", encoding="utf-8")
    (code / "src" / "widget.py").write_text("x = 1\n", encoding="utf-8")

    result = _in_code(code)(
        "promote", str(idea), "--kind", "chore", "--touches-file", str(touches),
        "--criteria-file", "-", "--keep", "--json", stdin="the widget exists\n",
    )
    assert result.code == 0, result
    assert "ideas/widget.md" in result.json["record"]["description"]
    # KYO-30: the stamp is top-of-file frontmatter, and `promoted` is a list.
    assert idea.read_text(encoding="utf-8").startswith("---\npromoted:\n  - TST-")


def test_touches_resolve_against_the_code_repository_not_the_store(split_project) -> None:
    """The store/code distinction, at its sharpest. A record's `touches` name code
    files; validating them against the store would reject every real scope and
    accept nothing but store paths."""
    code, store = split_project
    kyo = _in_code(code)
    touches = store / "t.txt"

    touches.write_text("src/widget.py\n", encoding="utf-8")
    ok = kyo("new", "bug", "--title", "real scope", "--touches-file", str(touches), "--json")
    assert ok.code == 0, ok

    touches.write_text("kyosei/config.yml\n", encoding="utf-8")
    bad = kyo("new", "bug", "--title", "store path", "--touches-file", str(touches))
    assert bad.code != 0, bad


def test_documents_are_declared_in_the_store_and_resolved_in_the_code(
    split_project,
) -> None:
    """KYO-129 at the store/code seam, which it straddles rather than sits on one side
    of. ``documents.yml`` is a registry, so it lives beside the other four in the STORE;
    the files it names are documents, so they are globbed against the CODE. Resolving
    either one in the other's repository would be confidently wrong and quiet about it —
    the same failure as validating ``touches`` against the store, one directory up.
    """
    code, store = split_project
    kyo = _in_code(code)

    # Nothing declared: the default reaches the code repository's root markdown.
    (code / "README.md").write_text("# Ops\n\ngoverns: src/vanished.py\n", encoding="utf-8")
    default = kyo("docs", "check", "--json")
    assert default.code == 3, default
    assert default.json["scanned"] == ["README.md"]

    # Declared in the store, matched in the code.
    (store / "kyosei" / "documents.yml").write_text(
        "documents:\n"
        "  handbook:\n"
        "    summary: The operating handbook.\n"
        "    globs:\n"
        "      - handbook/*.md\n",
        encoding="utf-8",
    )
    (code / "handbook").mkdir()
    (code / "handbook" / "ops.md").write_text(
        "# Ops\n\ngoverns: src/widget.py\n", encoding="utf-8"
    )

    declared = kyo("docs", "check", "--json")
    assert declared.code == 0, declared
    assert declared.json["document_paths"] == ["handbook/*.md"]
    assert declared.json["document_paths_declared"] is True
    assert declared.json["scanned"] == ["handbook/ops.md"]


def test_dispatch_stamps_the_code_repository_s_head(split_project) -> None:
    """A worker branches from the *code*, so a dispatch that stamped the store
    repository's HEAD would be confidently wrong in every later ancestry check."""
    code, store = split_project
    kyo = _in_code(code)
    touches = store / "t.txt"
    touches.write_text("src/widget.py\n", encoding="utf-8")
    created = kyo(
        "new", "bug", "--title", "dispatchable", "--criteria-file", "-",
        "--touches-file", str(touches), "--json", stdin="it works\n",
    )
    record_id = created.json["record"]["id"]

    dispatched = kyo("dispatch", record_id, "--json")
    assert dispatched.code == 0, dispatched
    code_head = _git(code, "rev-parse", "HEAD").stdout.strip()
    store_head = _git(store, "rev-parse", "HEAD").stdout.strip()
    assert dispatched.json["worked_from_sha"] == code_head
    assert dispatched.json["worked_from_sha"] != store_head


# --------------------------------------------------------------------- KYO-54.c2
# The guards say what they are guarding — under both layouts and under neither.


def test_check_branch_still_fails_on_a_store_diff_under_the_in_repo_layout(
    kyo, git_project: Path
) -> None:
    _git(git_project, "checkout", "-b", "bad-branch")
    (git_project / "kyosei" / "kinds.yml").write_text("kinds: {}\n", encoding="utf-8")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "sneaky store edit")

    result = kyo("check-branch", "bad-branch", "--against", "main", "--json")
    assert result.code == 2, result
    assert result.json["layout"] == LAYOUT_IN_REPO
    assert result.json["guarding"] is True
    assert any("kyosei/kinds.yml" in f for f in result.json["guarded_files"])


def test_check_branch_says_the_in_repo_guarantee_out_loud(kyo, git_project: Path) -> None:
    _git(git_project, "checkout", "-b", "feature")
    (git_project / "widget.py").write_text("x = 1\n", encoding="utf-8")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "code")

    result = kyo("check-branch", "feature", "--against", "main")
    assert result.code == 0, result
    assert "store layout: in-repo" in result.out
    assert "what stands between a worker branch and the store" in result.out


def test_check_branch_under_the_split_layout_reports_a_structural_guarantee(
    split_project,
) -> None:
    """KYO-54.c2, the criterion most likely to be met in appearance only. Under
    the split layout the guarded diff is not expressible at all — a *stronger*
    guarantee than the in-repo check, and indistinguishable from a check that has
    quietly stopped working unless it says which one it is."""
    code, store = split_project
    _git(code, "checkout", "-b", "feature")
    (code / "src" / "widget.py").write_text("x = 2\n", encoding="utf-8")
    _git(code, "add", "-A")
    _git(code, "commit", "-q", "-m", "code change")

    result = invoke(["--root", str(code), "check-branch", "feature", "--against", "main"])
    assert result.code == 0, result
    assert result.out.count("store layout: split") == 1
    assert "cannot diff the store at all" in result.out
    assert "physical property rather than a check that could fail" in result.out
    assert str(store) in result.out

    as_json = invoke(
        ["--root", str(code), "check-branch", "feature", "--against", "main", "--json"]
    )
    assert as_json.json["layout"] == LAYOUT_SPLIT
    assert as_json.json["guarding"] is True
    assert as_json.json["repo"] == str(code)
    assert as_json.json["store_root"] == str(store)


def test_check_branch_under_the_split_layout_still_catches_a_stray_store_copy(
    split_project,
) -> None:
    """The backstop the split layout does not remove: a `kyosei/` directory
    committed inside the code repository is a second copy of a store nothing
    reads, and is exactly as much of a mistake as it was before."""
    code, _store = split_project
    _git(code, "checkout", "-b", "stray")
    (code / "kyosei").mkdir()
    (code / "kyosei" / "kinds.yml").write_text("kinds: {}\n", encoding="utf-8")
    _git(code, "add", "-A")
    _git(code, "commit", "-q", "-m", "stray store copy")

    result = invoke(["--root", str(code), "check-branch", "stray", "--against", "main", "--json"])
    assert result.code == 2, result
    assert any("kyosei/kinds.yml" in f for f in result.json["guarded_files"])


def test_check_branch_with_no_project_configured_says_it_is_guarding_nothing(
    tmp_path: Path,
) -> None:
    """The third case. A repository that configured neither layout used to get
    "no Kyosei project found ... run `kyo init` first" at exit 1 from inside a
    SubagentStop hook, which blocks a worker's stop over a store nobody asked
    for. It now exits 0 and says plainly that exit 0 proves nothing."""
    plain = tmp_path / "plain-repo"
    plain.mkdir()
    (plain / "README.md").write_text("no kyosei here\n", encoding="utf-8")
    git_init(plain)

    result = invoke(["--root", str(plain), "check-branch", "--against", "main", "--json"])
    assert result.code == 0, result
    assert result.json["layout"] == LAYOUT_UNCONFIGURED
    assert result.json["guarding"] is False

    human = invoke(["--root", str(plain), "check-branch", "--against", "main"])
    assert "store layout: unconfigured" in human.out
    assert "guarding nothing" in human.out
    assert "NOT evidence" in human.out


def test_check_clean_reads_the_code_repository_under_both_layouts(
    split_project, kyo, git_project: Path
) -> None:
    """`kyo check-clean` is a worker-stop guard, so its subject is the worktree the
    worker is in — the code repository — never the store, whose dirt is the
    controller's business."""
    code, store = split_project
    (code / "src" / "unsaved.py").write_text("half a thought\n", encoding="utf-8")
    dirty = invoke(["--root", str(code), "check-clean", "--json"])
    assert dirty.code == 2, dirty
    assert dirty.json["root"] == str(code)
    assert dirty.json["layout"] == LAYOUT_SPLIT

    # A dirty store does not fail a worker's stop under the split layout.
    _git(code, "add", "-A")
    _git(code, "commit", "-q", "-m", "save it")
    (store / "ideas" / "stray.md").write_text("controller's business\n", encoding="utf-8")
    clean = invoke(["--root", str(code), "check-clean"])
    assert clean.code == 0, clean
    assert "cannot block a worker's stop" in clean.out

    # And the in-repo layout keeps behaving exactly as it did.
    assert kyo("check-clean", "--json").json["layout"] == LAYOUT_IN_REPO


def test_check_clean_with_no_project_configured_still_checks_the_worktree(
    tmp_path: Path,
) -> None:
    plain = tmp_path / "plain-repo"
    plain.mkdir()
    (plain / "README.md").write_text("no kyosei here\n", encoding="utf-8")
    git_init(plain)

    clean = invoke(["--root", str(plain), "check-clean"])
    assert clean.code == 0, clean
    assert "store layout: unconfigured" in clean.out
    assert "excluded by name only" in clean.out

    (plain / "work.py").write_text("uncommitted\n", encoding="utf-8")
    assert invoke(["--root", str(plain), "check-clean"]).code == 2


def test_the_subagent_stop_hook_wires_both_guards_under_either_layout() -> None:
    """The hook is the only place these two run in production. Whatever the
    fragment installs is what a consumer's worker actually gets, so the layout
    story is only true if both commands are still wired there."""
    repo_root = Path(__file__).resolve().parents[1]
    fragment = json.loads(
        (repo_root / "src" / "kyosei" / "defaults" / "hooks" / "subagent-stop.json").read_text(
            encoding="utf-8"
        )
    )
    # KYO-316 put a PATH guard ahead of each command. What this test is about is
    # which commands are wired, so it asks `install_hooks.hook_action` for the command a
    # hook runs rather than re-deciding here what counts as one.
    commands = [
        install_hooks.hook_action(hook["command"])
        for group in fragment["SubagentStop"]
        for hook in group["hooks"]
    ]
    # KYO-128 added `--subagent-stop` to both. The flag is what tells them WHICH
    # worktree to read; without it they resolve upward from the cwd a SubagentStop
    # hook inherits, which is the controller's project root and never the stopping
    # worker's worktree.
    assert commands == [
        "kyo check-clean --subagent-stop",
        "kyo check-branch --subagent-stop --against main",
    ]


# --------------------------------------------------------------------- KYO-54.c3
# What `kyo init` says, at the moment it scaffolds.


@pytest.mark.parametrize("flags", [[], ["--in-repo"]])
def test_init_names_every_committed_prose_surface_under_both_layouts(
    tmp_path: Path, flags: list[str]
) -> None:
    """KYO-54.c3. The only warning a consumer gets at the moment the decision is
    actually made, so it names ideas, shift notes, evidence stored verbatim and
    frozen briefs rather than gesturing at "the store"."""
    code = tmp_path / "proj"
    code.mkdir()
    result = invoke(["init", "--defaults", str(code), *flags])
    assert result.code == 0, result
    # KYO-558.c6: the list moved behind one line naming `kyo init --explain`, which
    # prints it for this project at any time and writes nothing.
    assert "kyo init --explain" in result.out
    result = invoke(["init", str(code), "--explain"])
    assert result.code == 0, result

    for surface in install_prose.COMMITTED_PROSE_SURFACES:
        assert surface.path in result.out, f"{surface.path} is not named by `kyo init`"
    for named in ("ideas/", "shift notes", "verbatim", "frozen dispatch briefs"):
        assert named in result.out
    assert "kyo redact" in result.out and "already been pushed" in result.out


def test_the_in_repo_warning_says_the_prose_becomes_committed(tmp_path: Path) -> None:
    code = tmp_path / "proj"
    result = invoke(["init", str(code), "--in-repo", "--explain"])
    assert "INSIDE the code repository" in result.out
    assert "becomes committed" in result.out


def test_the_split_disclosure_says_what_it_keeps_out(tmp_path: Path) -> None:
    code = tmp_path / "proj"
    code.mkdir()
    result = invoke(["init", "--defaults", str(code), "--explain"])
    assert "OUTSIDE the code repository" in result.out
    assert "keeps out of the code repository" in result.out


def test_the_disclosure_is_generated_from_the_surface_table(monkeypatch, tmp_path: Path) -> None:
    """The mechanism, which the test above would pass without: the warning is
    derived from :data:`COMMITTED_PROSE_SURFACES`, so a surface added to the store
    later cannot be missing from what `kyo init` discloses (law 3). A hand-typed
    paragraph is exactly how this list would go quietly out of date."""
    monkeypatch.setattr(
        install_prose,
        "COMMITTED_PROSE_SURFACES",
        (
            install_prose.ProseSurface(
                "seances",
                "kyosei/export/_seances.jsonl",
                "seance",
                "messages from the beyond",
            ),
        ),
    )
    lines = install_prose.prose_disclosure(
        LAYOUT_IN_REPO, code_root=tmp_path, store_root=tmp_path
    )
    assert any("_seances.jsonl" in line and "beyond" in line for line in lines)
    assert not any("_events.jsonl" in line for line in lines)


# --------------------------------------------------------------------- KYO-54.c4
# Rebuild and the round-trip, under the split layout.


def test_rebuild_works_under_the_split_layout(split_project) -> None:
    code, store = split_project
    kyo = _in_code(code)
    created = kyo("new", "bug", "--title", "survives a rebuild", "--json")
    record_id = created.json["record"]["id"]

    (store / "kyosei" / "kyosei.db").unlink()
    rebuilt = invoke(["--root", str(code), "rebuild", "--json"])
    assert rebuilt.code == 0, rebuilt
    assert kyo("show", record_id).code == 0
    # The round-trip check itself: doctor re-derives every export document.
    assert kyo("doctor").code == 0


def test_a_store_committed_to_its_own_repository_is_recoverable_from_that_repository(
    split_project, tmp_path: Path
) -> None:
    """KYO-54.c4, and the reason "just gitignore the store" was refused. The split
    store gives up nothing that mattered: it is still committed, so it is still
    recoverable — from a clone of the store repository *alone*, with the code
    repository nowhere in sight."""
    code, store = split_project
    kyo = _in_code(code)
    record_id = kyo("new", "bug", "--title", "recoverable", "--json").json["record"]["id"]
    kyo("edit", record_id, "--note-file", "-", stdin="a private thought\n")
    _git(store, "add", "-A")
    _git(store, "-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-q", "-m", "work")

    clone = tmp_path / "restored"
    subprocess.run(
        ["git", "clone", "-q", str(store), str(clone)], check=True, capture_output=True
    )
    assert not (clone / "kyosei" / "kyosei.db").exists()  # gitignored, as designed

    rebuilt = invoke(["--root", str(clone), "rebuild", "--json"])
    assert rebuilt.code == 0, rebuilt
    shown = invoke(["--root", str(clone), "show", record_id, "--json"])
    assert shown.code == 0, shown
    assert shown.json["title"] == "recoverable"
    assert "a private thought" in json.dumps(shown.json)


# ------------------------------------------------------------------ the resolver
# Discovery is the only thing the split layout changed, so it gets its own tests.


def test_discovery_walks_up_to_the_store_link_like_git(split_project) -> None:
    code, store = split_project
    deep = code / "src" / "a" / "b"
    deep.mkdir(parents=True)
    layout = config_module.resolve_layout(deep)
    assert layout.kind == LAYOUT_SPLIT
    assert layout.store_root == store
    assert layout.code_root == code


def test_a_store_link_pointing_nowhere_fails_loudly(split_project) -> None:
    """Law 6. A consumer whose store is missing — never cloned, or moved — must be
    told that, not told they have no project."""
    code, store = split_project
    (code / STORE_LINK_NAME).write_text("store: ./no-such-store\n", encoding="utf-8")
    result = invoke(["--root", str(code), "list"])
    assert result.code == 1, result
    assert "not a Kyosei store" in result.err
    assert "clone it to that path" in result.err
    assert store.name  # the store itself is untouched by the broken link


def test_a_relative_store_link_resolves_against_the_code_repository(
    split_project, tmp_path: Path
) -> None:
    code, store = split_project
    sibling = code.parent / "sibling-store"
    store.rename(sibling)
    (code / STORE_LINK_NAME).write_text("store: ../sibling-store\n", encoding="utf-8")
    result = invoke(["--root", str(code), "list", "--json"])
    assert result.code == 0, result


def test_an_unreadable_store_link_names_the_remedy(split_project) -> None:
    code, _store = split_project
    (code / STORE_LINK_NAME).write_text("# nothing but a comment\n", encoding="utf-8")
    result = invoke(["--root", str(code), "list"])
    assert result.code == 1
    assert "does not name a store" in result.err
    assert "kyo init --store DIR" in result.err


# ------------------------------------ moving an existing store out of the repo
# There is deliberately no `kyo` command for this (KYO-54's brief: the migration
# belongs to KYO-60, which is not this dispatch's to build). What IS built here
# is the half a migration cannot work without — `kyo init --store` adopting a
# store that already exists — and a test that walks the documented steps, so the
# README's instructions are executed by the suite rather than trusted.


def test_init_store_adopts_a_store_that_already_exists(tmp_path: Path) -> None:
    """Adoption, on its own: link a code repository to an existing store without
    scaffolding over it. Also the case of a second checkout of the same code that
    did not inherit the link."""
    first = tmp_path / "proj"
    first.mkdir()
    initialised = invoke(["init", "--defaults", str(first), "--prefix", "TST", "--json"])
    store = Path(initialised.json["store_root"])
    record_id = invoke(["--root", str(first), "new", "bug", "--title", "kept", "--json"]).json[
        "record"
    ]["id"]

    second = tmp_path / "proj-checkout-two"
    second.mkdir()
    result = invoke(["init", str(second), "--store", str(store), "--json"])
    assert result.code == 0, result
    assert result.json["adopted"] is True
    assert result.json["written"] == []
    assert result.json["project_prefix"] == "TST"
    assert "adopted the existing store" in "\n".join(result.json["git"])
    # The records are the same records, reached from the second checkout.
    assert invoke(["--root", str(second), "show", record_id]).code == 0


def test_the_documented_migration_moves_an_in_repo_store_out(tmp_path: Path) -> None:
    """The steps README's "Moving an existing store out of the code repository"
    prescribes, run end to end. An in-repo project with real records becomes a
    split one, and every record survives.

    This is what makes the refusal in `init_layout` honest: that error tells a
    consumer with an existing in-repo store to read that section, and law 5's
    discipline is that an error naming a remedy has a test proving the remedy
    clears it.
    """
    code = tmp_path / "proj"
    assert invoke(["init", str(code), "--in-repo", "--prefix", "TST"]).code == 0
    git_init(code)
    kyo = _in_code(code)
    record_id = kyo("new", "bug", "--title", "predates the move", "--json").json["record"]["id"]
    kyo("edit", record_id, "--note-file", "-", stdin="a private thought\n")
    (code / "ideas" / "thought.md").write_text("# unfiltered\n", encoding="utf-8")

    # `kyo init` refuses to do this for you, and names where the steps are.
    refused = invoke(["init", "--defaults", str(code)])
    assert refused.code == 1
    assert "Moving an existing store out of the code repository" in refused.err

    # Step 1-3: move both layers out, commit the removal, make the store a repo.
    store = tmp_path / "kyosei-home" / "proj-moved"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.mkdir()
    for layer in ("kyosei", "ideas"):
        (code / layer).rename(store / layer)
    _git(code, "add", "-A")
    _git(code, "-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-q", "-m", "move out")
    _git(store, "init", "-q", "-b", "main")
    _git(store, "add", "-A")
    _git(store, "-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-q", "-m", "store")

    # Step 4: link the code repository to the store that now exists.
    linked = invoke(["init", str(code), "--store", str(store), "--json"])
    assert linked.code == 0, linked
    assert linked.json["adopted"] is True

    # Everything survived, and the code repository now holds only the pointer.
    shown = kyo("show", record_id, "--json")
    assert shown.code == 0, shown
    assert shown.json["title"] == "predates the move"
    assert "a private thought" in json.dumps(shown.json)
    assert kyo("doctor").code == 0
    assert not (code / "kyosei").exists() and not (code / "ideas").exists()
    assert (code / STORE_LINK_NAME).is_file()

    # And the guards now report the structural guarantee rather than the checked one.
    guard = kyo("check-branch", "--against", "main")
    assert guard.code == 0, guard
    assert "cannot diff the store at all" in guard.out


def test_the_readme_documents_the_migration_the_error_message_names() -> None:
    """The remedy is prose, so the prose is what has to exist. Named here rather
    than trusted, because an error message pointing at a section nobody wrote is
    the same defect as one naming a flag nobody shipped."""
    from tests.reference_pages import STORE

    readme = STORE.read_text(encoding="utf-8")  # the README section, moved (KYO-516)
    assert "### Moving an existing store out of the code repository" in readme
    section = readme.split("### Moving an existing store out of the code repository")[1]
    assert "kyo init --store" in section
    assert "git filter-repo" in section, "the history caveat must not be quietly dropped"


# --------------------------------------------------------------------- KYO-63
# Sweep's bookkeeping and the history it describes are two repositories here.
#
# `kyo sweep` records "last swept at code sha X" in the STORE repository, and X is a
# commit in the CODE repository. Under the in-repo layout those are the same repo, so a
# clone of the store is by construction a clone of the history the bookkeeping refers
# to. The split layout — the default since KYO-54 — breaks that: nothing guarantees a
# given clone of the code contains X. The failure is at restore time and it is quiet,
# because a sweep that cannot diff creates no touch-changed flags, which is byte-for-byte
# what a project with nothing stale looks like.


def _foreign_code_repo(where: Path, *, linked_to: Path) -> Path:
    """A code repository that is NOT the one the store swept: its own root commit, and
    the same declared touch path present so `touch-missing` cannot fire and provide the
    visibility by accident. The store link is copied in because that is what a restore
    looks like — the store is found, the history it recorded is not."""
    (where / "src").mkdir(parents=True)
    (where / "src" / "widget.py").write_text("x = 999\n", encoding="utf-8")
    git_init(where, commit_message="an unrelated history")
    (where / STORE_LINK_NAME).write_text(
        (linked_to / STORE_LINK_NAME).read_text(encoding="utf-8"), encoding="utf-8"
    )
    return where


def _record_touching_widget(code: Path, store: Path) -> str:
    touches = store / "kyo63-touches.txt"
    touches.write_text("src/widget.py\n", encoding="utf-8")
    created = _in_code(code)(
        "new", "bug", "--title", "widget work", "--touches-file", str(touches), "--json"
    )
    assert created.code == 0, created
    return created.json["record"]["id"]


def test_a_store_beside_a_foreign_code_repository_reports_staleness_as_unanswered(
    split_project, tmp_path: Path
) -> None:
    """KYO-63.c1/c2/c3, the failure itself. The store is restored beside a code
    repository that does not contain the sha it recorded; the touch it watches is
    different there and the sweep still creates nothing. What must be true is that the
    run says which question it could not answer, in the payload the v2 board reads and in
    the text a person reads — and that the next run recovers, which is the only reason
    "flag nothing" is a defensible choice rather than a shrug."""
    code, store = split_project
    _record_touching_widget(code, store)

    baseline = _in_code(code)("sweep", "--json")
    assert baseline.code == 0, baseline
    assert baseline.json["coverage"] == "baseline"
    assert baseline.json["code_repo_id"], "the run must record the history it swept"

    other = _foreign_code_repo(tmp_path / "restored-code", linked_to=code)
    swept = invoke(["--root", str(other), "sweep", "--json"])
    assert swept.code == 0, swept

    # The silence, exactly as it would have been before this record.
    assert swept.json["flags"] == []
    # ...and the outcome that stops it reading as a clean board.
    assert swept.json["coverage"] == "foreign-history"
    assert baseline.json["code_repo_id"][:12] in swept.json["coverage_detail"]

    brief = invoke(["--root", str(other), "brief", "--json"])
    assert brief.json["unresolved_flags"] == []
    assert brief.json["staleness"]["coverage"] == "foreign-history"
    assert brief.json["staleness"]["sweep_run_id"] == swept.json["sweep_run_id"]

    human = invoke(["--root", str(other), "brief"])
    assert "staleness coverage (foreign-history):" in human.out
    assert "not a clean bill" in human.out

    # The remedy the message names: this run re-baselined in the history it can see, so
    # the next sweep answers staleness again rather than repeating the warning forever.
    recovered = invoke(["--root", str(other), "sweep", "--json"])
    assert recovered.json["coverage"] == "swept"
    # KYO-635.c3: the store still records the other repository's root commit, which
    # doctor reports as a problem naming `kyo init --store <store> --adopt`; that
    # command, the operator's word that the store is this repository's, clears it.
    found = invoke(["--root", str(other), "doctor"])
    assert found.code == 3 and "[code_repo_identity_mismatch]" in found.err
    assert found.err.count("\n  - ") == 1, found.err
    adopted = invoke(["init", str(other), "--store", str(store), "--adopt"])
    assert adopted.code == 0, adopted.err
    assert invoke(["--root", str(other), "doctor"]).code == 0


def test_the_unanswered_sweep_says_so_in_its_own_output_too(
    split_project, tmp_path: Path
) -> None:
    """The same run, read the way a controller reads it at a session stop hook: text,
    not `--json`. The flag count is the line that lies on its own, so the correction has
    to be beside it."""
    code, store = split_project
    _record_touching_widget(code, store)
    assert _in_code(code)("sweep").code == 0

    other = _foreign_code_repo(tmp_path / "restored-code-text", linked_to=code)
    human = invoke(["--root", str(other), "sweep"])
    assert human.code == 0, human
    assert "created 0 flag(s)" in human.out
    assert "NOT ANSWERED" in human.out
    assert "not a clean bill" in human.out


def test_a_shallow_clone_missing_the_recorded_base_is_named_as_unresolvable(
    split_project, tmp_path: Path
) -> None:
    """The other half of KYO-63.c1: the same history, legitimately, but not all of it.

    A shallow clone has no root commit to identify itself with — its oldest commit is a
    graft boundary — so the identity check must abstain rather than shout "different
    history" at a `--depth 1`. What is left is the sha itself, which genuinely is not
    here, and that is what the run reports."""
    code, _store = split_project
    kyo = _in_code(code)
    assert kyo("sweep").code == 0  # baseline recorded at the first commit

    (code / "src" / "widget.py").write_text("x = 2\n", encoding="utf-8")
    _git(code, "add", "-A")
    _git(code, "commit", "-q", "-m", "second commit")

    shallow = tmp_path / "shallow-clone"
    subprocess.run(
        ["git", "clone", "--depth", "1", f"file://{code}", str(shallow)],
        check=True,
        capture_output=True,
        text=True,
    )
    (shallow / STORE_LINK_NAME).write_text(
        (code / STORE_LINK_NAME).read_text(encoding="utf-8"), encoding="utf-8"
    )

    swept = invoke(["--root", str(shallow), "sweep", "--json"])
    assert swept.code == 0, swept
    assert swept.json["coverage"] == "base-unresolvable"
    assert "shallow" in swept.json["coverage_detail"]
    # Identity is not established in a shallow clone, and is recorded as unknown rather
    # than as the graft boundary that would look like a different repository later.
    assert swept.json["code_repo_id"] is None


# ====================================================================== KYO-74
# The db/export seam, which the split store made two-directional.
#
# Under the in-repo layout one process owned `kyosei.db` and `kyosei/export/` and the
# database could only ever be the newer of the two. The split store is its own git
# repository, `kyosei.db` is gitignored there, and a second checkout pulls record
# changes the way it pulls code — so after a pull the export is legitimately AHEAD of
# the database, a state that could not previously arise.
#
# Two symptoms came out of one gap: nothing compared the two, in a direction-aware
# way. Reads presented a database missing records as a complete board. `kyo doctor`
# named a direction it had no input for, and named the wrong one — telling a consumer
# that "the database is authoritative" and that a freshly pulled record was a "stale
# export", either of which, acted on, destroys the pulled work.
#
# The tests below construct the pulled state end to end rather than by hand, because
# what makes it real is that every part of it is what git legitimately produces.


def _second_checkout(store: Path, destination: Path) -> Path:
    """A clone of the store repository, with its database rebuilt — i.e. a working
    second checkout, in step with the export at the moment it was cloned."""
    _git(store, "add", "-A")
    _git(store, "-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-q", "-m", "work")
    subprocess.run(
        ["git", "clone", "-q", str(store), str(destination)], check=True, capture_output=True
    )
    assert invoke(["--root", str(destination), "rebuild"]).code == 0
    return destination


def _publish_and_pull(store: Path, clone: Path) -> None:
    """Commit the store's current state and pull it into the second checkout."""
    _git(store, "add", "-A")
    _git(store, "-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-q", "-m", "more")
    subprocess.run(
        ["git", "-C", str(clone), "pull", "-q", "origin", "HEAD"],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def pulled_behind(split_project, tmp_path: Path) -> tuple[Path, str, str]:
    """The reproduction, built by git: ``(clone, modified_record, added_record)``.

    One checkout retitles a record and files another; the second checkout pulls both.
    Its database is now behind its export by one modified record and one added one —
    the case KYO-74.c3 names, and the one every message below has to be true of.
    """
    code, store = split_project
    kyo = _in_code(code)
    modified = kyo("new", "bug", "--title", "a reproduction", "--json").json["record"]["id"]
    clone = _second_checkout(store, tmp_path / "second-checkout")

    kyo("edit", modified, "--title", "retitled on the other machine")
    added = kyo("new", "bug", "--title", "filed on the other machine", "--json").json["record"][
        "id"
    ]
    _publish_and_pull(store, clone)
    return clone, modified, added


# --------------------------------------------------------------------- KYO-74.c1


def test_list_does_not_present_a_database_behind_its_export_as_the_whole_board(
    pulled_behind,
) -> None:
    """The count said "1 record(s)" over a store holding two, with no warning — law 6,
    and KYO-31's stale viewer one layer down, at the db/export seam instead of the
    html/store seam. It must now say the database is behind, and name what fixes it."""
    clone, _modified, added = pulled_behind
    listed = invoke(["--root", str(clone), "list"])
    assert listed.code == 0, listed

    # The database genuinely does not have the pulled record yet: it is on no listing
    # line. The warning below names it, which is the entire point.
    assert not any(line.startswith(added) for line in listed.out.splitlines()), listed.out
    assert "database is behind its export" in listed.out, listed.out
    assert "kyo rebuild --force" in listed.out, listed.out

    seam = invoke(["--root", str(clone), "list", "--json"]).json["db_export_currency"]
    assert seam["direction"] == "export-ahead"
    assert seam["db_is_behind"] is True
    assert seam["records_only_in_export"] == [added]


def test_brief_refuses_over_a_database_behind_its_export_rather_than_writing(
    pulled_behind,
) -> None:
    """The same guarantee on the session brief, and one step further, because `kyo
    brief` is a read surface that WRITES.

    It allocates a brief-run id and an event from the database's counters and then
    overwrites the git-canonical `_allocations.json` with them. Over a database that
    is behind its export that replaces the pulled counters with lower ones and
    reissues an id the export has already handed out — law 4's reissued id, arriving
    through the command a session-start hook runs first. So it refuses, and the
    snapshot it would have clobbered is untouched afterwards.

    KYO-80 changed where that refusal comes from and not whether it happens: `cmd_brief`
    no longer carries a copy of the guard, and the write below is refused by the one on
    `Store.transaction()` (KYO-75). The assertions are deliberately on what a consumer
    sees — a non-zero exit, the sentence, the remedy, an untouched snapshot — so they
    hold across that move and would fail if either guard were removed.
    """
    clone, _modified, _added = pulled_behind
    snapshot = (clone / "kyosei" / "export" / "_allocations.json").read_text(encoding="utf-8")

    for extra in ([], ["--compact"], ["--json"]):
        briefed = invoke(["--root", str(clone), "brief", *extra])
        assert briefed.code != 0, briefed
        said = briefed.out + briefed.err
        assert "database is behind its export" in said, said
        assert "kyo rebuild --force" in said, said

    assert (clone / "kyosei" / "export" / "_allocations.json").read_text(
        encoding="utf-8"
    ) == snapshot


def test_the_named_remedy_makes_the_read_surfaces_complete_and_quiet(pulled_behind) -> None:
    """KYO-5's discipline applied to the remedy KYO-74 adds: run exactly what the
    warning names and the board is whole, the warning is gone, and doctor passes."""
    clone, modified, added = pulled_behind
    assert invoke(["--root", str(clone), "rebuild", "--force"]).code == 0

    listed = invoke(["--root", str(clone), "list", "--json"])
    assert listed.code == 0, listed
    ids = {item["id"] for item in listed.json["records"]}
    assert {modified, added} <= ids
    assert listed.json["db_export_currency"]["direction"] == "in-step"
    assert "database is behind its export" not in invoke(["--root", str(clone), "list"]).out
    assert invoke(["--root", str(clone), "doctor"]).code == 0


def test_a_store_in_step_says_nothing_about_the_seam(split_project) -> None:
    """The cost of the check is a comparison; the cost of its OUTPUT must be zero in
    the ordinary case, or `kyo list` and `kyo brief` grow a line nobody reads."""
    code, _store = split_project
    kyo = _in_code(code)
    kyo("new", "bug", "--title", "ordinary")
    for command in ("list", "brief"):
        result = kyo(command)
        assert result.code == 0, result
        assert "behind its export" not in result.out, result.out
        assert "WARNING" not in result.out, result.out


# --------------------------------------------------------------------- KYO-74.c3


def test_doctor_does_not_assert_the_database_is_authoritative_after_a_pull(
    pulled_behind,
) -> None:
    """KYO-74.c3. Both sentences doctor used to state unconditionally are inverted in
    this case: the on-disk text is the pulled, current one and the derived text is the
    stale one, and the "stale export" is a record that arrived thirty seconds ago. A
    consumer who acts on either regenerates the export from a database that is behind
    it — and "exports are generated, never hand-edited" is what recommends exactly
    that."""
    clone, _modified, added = pulled_behind
    doctored = invoke(["--root", str(clone), "doctor"])
    assert doctored.code != 0, doctored  # the seam IS a problem; it must still fail

    said = doctored.out + doctored.err
    assert "the database is authoritative" not in said, said
    assert "(stale export)" not in said, said
    assert f"{added}.json has no record in the database (NOT a stale export" in said, said
    assert "database is behind its export" in said, said
    assert "kyo rebuild --force" in said, said


def test_doctor_still_says_the_database_is_authoritative_where_that_is_true(
    kyo, project: Path
) -> None:
    """KYO-74.c3 asks for the sentence to stop being asserted where it is false, not
    for it to be deleted. A hand-edited export document in a store whose counters agree
    is exactly what it was written for, and it still says so."""
    record = new_record(kyo, criteria=["a"])
    document = export_of(project, record)
    document.write_text(
        document.read_text(encoding="utf-8").replace("A record", "edited by hand"),
        encoding="utf-8",
    )

    doctored = kyo("doctor")
    assert doctored.code != 0, doctored
    said = doctored.out + doctored.err
    assert "the database is authoritative" in said, said


# --------------------------------------------------------------------- KYO-74.c2


def _write_allocations(root: Path, allocations: dict) -> None:
    (root / "kyosei" / "export" / "_allocations.json").write_text(
        json.dumps(allocations, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def _read_allocations(root: Path) -> dict:
    return json.loads(
        (root / "kyosei" / "export" / "_allocations.json").read_text(encoding="utf-8")
    )


def test_doctor_distinguishes_the_export_being_behind_from_the_database_being_behind(
    kyo, project: Path
) -> None:
    """The first two of KYO-74.c2's three states, in one store, one after the other.

    The id counters are the input the direction question never had: law 4 makes them
    monotonic and every mutating command moves at least one inside its own
    transaction, so a side that has recorded an operation the other has not cannot
    hide it. Ahead on disk means the export moved; ahead in the database means the
    database did.
    """
    new_record(kyo, criteria=["a"])
    counters = _read_allocations(project)

    _write_allocations(project, {k: v + 1 for k, v in counters.items()})
    said = (lambda r: r.out + r.err)(kyo("doctor"))
    assert "the database is behind its export" in said, said
    assert "kyo rebuild --force" in said, said

    _write_allocations(project, {k: v - 1 for k, v in counters.items()})
    said = (lambda r: r.out + r.err)(kyo("doctor"))
    assert "the export is behind its database" in said, said
    assert "kyo export --refresh" in said, said


def test_doctor_says_it_cannot_tell_rather_than_picking_a_direction(
    kyo, project: Path
) -> None:
    """The third state, which is the one KYO-74 is actually about: the tool had no
    input that could tell it which side moved and named one anyway. Where the evidence
    is a genuine fork — each side ahead on a different counter — the honest answer is
    that it cannot be determined, and the remedy is to run neither command, because
    each overwrites the side the other keeps (KYO-32's branch check)."""
    new_record(kyo, criteria=["a"])
    counters = _read_allocations(project)
    forked = dict(counters)
    forked["event"] = counters["event"] + 1
    forked["record"] = counters["record"] - 1
    _write_allocations(project, forked)

    doctored = kyo("doctor")
    assert doctored.code != 0, doctored
    said = doctored.out + doctored.err
    assert "cannot tell which side" in said, said
    assert "both sides have moved" in said, said
    assert "do NOT run" in said, said
    # And it does not quietly pick one anyway.
    assert "the database is behind its export" not in said, said
    assert "the export is behind its database" not in said, said


def test_a_missing_allocation_snapshot_is_cannot_tell_not_a_direction(
    kyo, project: Path
) -> None:
    """The other way the comparison loses its input. `_allocations.json` is rewritten
    by every mutating command, so a store that has ever been written to has one;
    missing, what is gone is the counter, not a side of the seam — and there is
    nothing to compare rather than something to conclude."""
    new_record(kyo, criteria=["a"])
    (project / "kyosei" / "export" / "_allocations.json").unlink()

    said = (lambda r: r.out + r.err)(kyo("doctor"))
    assert "cannot tell which side" in said, said
    assert "not there to compare" in said, said


# ====================================================================== KYO-75
# The half of the same gap KYO-74's criteria did not reach, raised by the worker
# that satisfied them.
#
# KYO-74 made `kyo brief` REFUSE over a database behind its export, and the
# rationale it gave was not specific to briefs: a brief allocates ids from the
# database and rewrites `kyosei/export/_allocations.json` with them, which lowers
# the pulled counters and reissues an id the export has already handed out. Every
# mutating command does exactly that. `kyo brief` was guarded because a
# session-start hook runs it first, not because it does the most damage — a
# reissued id from `kyo new` puts two different records under one identifier in a
# git-canonical file, which is what law 4 exists to prevent.
#
# So the guard is on `Store.transaction()`, once, and these tests are about the
# two things that placement has to get right: everything that writes is caught,
# and `kyo rebuild` — the remedy every message names, which necessarily runs
# against a database behind its export — is not.


def _counters(clone: Path) -> dict:
    return json.loads(
        (clone / "kyosei" / "export" / "_allocations.json").read_text(encoding="utf-8")
    )


def test_no_mutating_command_lowers_a_counter_over_a_database_behind_its_export(
    pulled_behind, tmp_path: Path
) -> None:
    """KYO-75.c1 and KYO-75.c3 together, driven through the CLI over the pulled state.

    Four distinct mutating commands, one from each family that allocates: a record
    id, a criterion child id, a link id, a project-wide shift-note id. Each must
    either refuse or leave every counter where it was — and each must name the
    remedy, because a refusal a consumer cannot clear is worse than the write.
    """
    clone, modified, added = pulled_behind
    before = _counters(clone)
    # Not a vacuous fixture: the export really is holding counters this database has
    # not reached, which is the whole precondition.
    assert before["record"] > 0

    note_file = tmp_path / "shift.txt"
    note_file.write_text("a shift note written against a behind database\n", encoding="utf-8")

    attempts = [
        ["new", "bug", "--title", "filed against a database behind its export"],
        ["edit", modified, "--title", "retitled against a database behind its export"],
        ["link", modified, "blocks", added],
        ["note", "--shift", "--file", str(note_file)],
    ]
    for argv in attempts:
        result = invoke(["--root", str(clone), *argv])
        assert result.code != 0, (argv, result)
        said = result.out + result.err
        assert "database is behind its export" in said, (argv, said)
        assert "kyo rebuild --force" in said, (argv, said)
        # KYO-75.c1's second clause, checked after every single attempt rather than
        # once at the end: a command that lowered a counter and then failed would be
        # invisible to a check that only looked at the final state.
        after = _counters(clone)
        assert all(after[scope] >= value for scope, value in before.items()), (argv, after)
        assert after == before, (argv, after)


def test_a_record_the_export_already_holds_is_not_reissued_to_a_new_record(
    pulled_behind,
) -> None:
    """The damage law 4 exists to prevent, stated as the outcome rather than as the
    counter that produces it.

    The pulled record's id is the next one this database would hand out. Against the
    pre-fix build, `kyo new` therefore writes a DIFFERENT record into that same
    git-canonical document — two records under one identifier, in a committed file,
    with nothing saying so."""
    clone, _modified, added = pulled_behind
    document = clone / "kyosei" / "export" / f"{added}.json"
    original = json.loads(document.read_text(encoding="utf-8"))

    refused = invoke(["--root", str(clone), "new", "bug", "--title", "the reissued one"])
    assert refused.code != 0, refused
    assert json.loads(document.read_text(encoding="utf-8")) == original

    # And the remedy makes the write land where it belongs: the next id AFTER the
    # pulled one, with the pulled record still its own record (KYO-5's discipline).
    assert invoke(["--root", str(clone), "rebuild", "--force"]).code == 0
    made = invoke(["--root", str(clone), "new", "bug", "--title", "the next one", "--json"])
    assert made.code == 0, made
    assert made.json["record"]["id"] != added
    assert json.loads(document.read_text(encoding="utf-8"))["title"] == original["title"]


def test_the_guard_is_on_the_shared_transaction_seam_not_repeated_per_command(
    pulled_behind,
) -> None:
    """KYO-75.c2. `kyo brief` was the fourth per-command instance of one fix; the
    point of this record is that there is no fifth.

    Asserted where a new mutating command would actually inherit it — every writer in
    this codebase opens `Store.transaction()`, so a command written tomorrow is
    covered by its author doing nothing at all. Testing the four commands above
    instead would pass just as well against four hand-placed guards, which is the
    design this criterion exists to rule out.
    """
    from kyosei.errors import KyoseiError
    from kyosei.store import open_store

    store = open_store(clone := pulled_behind[0])
    try:
        with pytest.raises(KyoseiError) as raised, store.transaction():
            store.new_record_id()  # pragma: no cover - the guard fires before this
        assert "database is behind its export" in str(raised.value)
        assert "kyo rebuild --force" in str(raised.value)
    finally:
        store.close()
    assert (clone / "kyosei" / "export" / "_allocations.json").is_file()


def test_rebuild_is_not_caught_by_the_guard_it_is_the_remedy_for(pulled_behind) -> None:
    """The trap this placement has to avoid. `kyo rebuild --force` is the command
    every one of the refusals above names, and it necessarily runs against a database
    that is behind its export — a guard that caught it would leave a consumer with a
    refusal whose only remedy is refused.

    It is exempt structurally rather than by name: `cmd_rebuild` deliberately does not
    go through `ctx.store()` (it must run when there is no database at all), and
    `rebuild._load` opens its own connection over a fresh file, so it never passes
    through `Store.transaction()`.
    """
    clone, modified, added = pulled_behind
    before = _counters(clone)

    rebuilt = invoke(["--root", str(clone), "rebuild", "--force"])
    assert rebuilt.code == 0, rebuilt

    # Law 4 held across the remedy: rebuild seeds `allocations` from the snapshot and
    # from nothing else, so no counter moved and the board is now whole.
    assert _counters(clone) == before
    listed = invoke(["--root", str(clone), "list", "--json"])
    assert {item["id"] for item in listed.json["records"]} >= {modified, added}
    assert listed.json["db_export_currency"]["direction"] == "in-step"

    # And writing is unblocked by exactly the command that was named.
    assert invoke(["--root", str(clone), "new", "bug", "--title", "after the remedy"]).code == 0


def test_a_fork_the_read_surfaces_call_complete_still_refuses_to_write(
    pulled_behind,
) -> None:
    """Where the write question and the read question genuinely come apart.

    Both sides have moved: the export is ahead on some scopes and the database on
    others. `Currency.db_is_behind` is False — no export document lacks a row, so a
    listing is not under-reporting and the read surfaces are right to stay quiet. A
    write is still a law-4 violation, because `write_allocations` rewrites the whole
    snapshot from the database and the scopes the export is ahead on are lowered.

    This is the case a guard built on the read predicate would wave straight through,
    and the reason `would_reissue_an_id` is its own property.
    """
    clone, _modified, _added = pulled_behind
    assert invoke(["--root", str(clone), "rebuild", "--force"]).code == 0
    assert invoke(["--root", str(clone), "new", "bug", "--title", "local work"]).code == 0

    # Now make each side ahead of the other on a different scope, with every export
    # document still backed by a row: links the export has issued and this database
    # has not, and events this database has recorded and the export has not.
    allocations = clone / "kyosei" / "export" / "_allocations.json"
    counters = json.loads(allocations.read_text(encoding="utf-8"))
    counters["link"] = int(counters.get("link", 1)) + 5
    counters["event"] = int(counters["event"]) - 1
    allocations.write_text(json.dumps(counters, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    seam = invoke(["--root", str(clone), "list", "--json"]).json["db_export_currency"]
    assert seam["direction"] == "cannot-tell"
    assert seam["db_is_behind"] is False, "the read surfaces have nothing to warn about here"
    assert seam["export_ahead_scopes"] == ["link"]

    refused = invoke(["--root", str(clone), "new", "bug", "--title", "over a fork"])
    assert refused.code != 0, refused
    said = refused.out + refused.err
    # And it says the true thing about a fork rather than the pulled-behind sentence:
    # neither remedy is safe here, because which side is newer is exactly what could
    # not be determined (`Currency.remedy`).
    assert "cannot tell which side" in said, said
    assert "do NOT run" in said, said
    assert json.loads(allocations.read_text(encoding="utf-8")) == counters


def test_an_orphaned_export_document_over_agreeing_counters_does_not_block_writes(
    pulled_behind,
) -> None:
    """The other direction the two predicates come apart, and the one where refusing
    would be the wrong call.

    A record document with no row behind it sets `db_is_behind` — a listing really is
    under-reporting, and `kyo list` says so. But the counters agree, so no write
    lowers anything and no id is reissued: there is no law-4 exposure to guard
    against. Refusing here would stop a consumer in the one state whose own remedy
    text is "do NOT run either command until you know which side you mean to keep" —
    a refusal with no action behind it. `kyo doctor` reports the orphan; the write
    guard stays out of it.
    """
    clone, _modified, added = pulled_behind
    assert invoke(["--root", str(clone), "rebuild", "--force"]).code == 0
    before = _counters(clone)

    # An export document nothing in the database backs, with every counter untouched.
    orphan = clone / "kyosei" / "export" / f"{added}.json"
    document = json.loads(orphan.read_text(encoding="utf-8"))
    document["id"] = f"{document['id']}0"
    (clone / "kyosei" / "export" / f"{document['id']}.json").write_text(
        json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    seam = invoke(["--root", str(clone), "list", "--json"]).json["db_export_currency"]
    assert seam["db_is_behind"] is True, "the read surfaces DO have something to warn about"
    assert seam["export_ahead_scopes"] == []

    made = invoke(["--root", str(clone), "new", "bug", "--title", "still workable", "--json"])
    assert made.code == 0, made
    after = _counters(clone)
    assert all(after[scope] >= value for scope, value in before.items()), after


# --------------------------------------------------------------- KYO-80 / KYO-81
#
# KYO-75 narrowed the write question to `Currency.would_reissue_an_id` and moved it
# onto `Store.transaction()`. These two records are the same question asked twice
# about that change — when a predicate or a seam moves, who else was reading the old
# one — and the answers were: `kyo brief`, which kept refusing on the *read*
# predicate, and `kyo export --refresh`, which writes to `kyosei/export/` without
# ever opening a transaction and so was never in front of the guard at all.


def test_brief_and_new_do_not_disagree_over_an_orphaned_export_document(
    pulled_behind,
) -> None:
    """KYO-80.c1/.c3, pinned as one test so the two can never again be changed apart.

    The state is the one where the read predicate and the write predicate come apart
    in the direction that does no damage: an export document with no row behind it,
    over counters that agree. `kyo new` proceeds here — nothing is lowered, nothing is
    reissued, and that is pinned one test above
    (`test_an_orphaned_export_document_over_agreeing_counters_does_not_block_writes`).
    `kyo brief` refused, in the same state, one line apart, over a message asserting
    the write "would ... reissue an id the export has already handed out" — the one
    thing the tool had just established would not happen (KYO-80).

    It now proceeds too, and says what a read surface says: the board is incomplete,
    stated first, ahead of every count, the way `kyo list` states it. The reading
    harm is real — a brief is read as authoritative — but the state it arises in is
    the one whose own remedy text is "do NOT run either command until you know which
    side you mean to keep", so a refusal at session start leaves a controller with no
    session and no move. A warning leaves them the board they can see.
    """
    clone, _modified, added = pulled_behind
    assert invoke(["--root", str(clone), "rebuild", "--force"]).code == 0
    orphan = json.loads(
        (clone / "kyosei" / "export" / f"{added}.json").read_text(encoding="utf-8")
    )
    orphan["id"] = f"{orphan['id']}0"
    (clone / "kyosei" / "export" / f"{orphan['id']}.json").write_text(
        json.dumps(orphan, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    seam = invoke(["--root", str(clone), "list", "--json"]).json["db_export_currency"]
    assert seam["db_is_behind"] is True and seam["export_ahead_scopes"] == []

    assert invoke(["--root", str(clone), "new", "bug", "--title", "still workable"]).code == 0
    briefed = invoke(["--root", str(clone), "brief", "--compact"])
    assert briefed.code == 0, briefed
    assert "WARNING" in briefed.out, briefed.out
    assert "incomplete" in briefed.out, briefed.out
    # And it says nothing about reissuing an id, because nothing here would.
    assert "reissue" not in (briefed.out + briefed.err)


def test_no_refusal_claims_a_reissued_id_where_no_id_would_be_reissued(
    pulled_behind,
) -> None:
    """KYO-80.c2, asked of every command that can refuse over this seam rather than of
    `kyo brief` alone — the record is about a second reader of a predicate, so pinning
    only the reader that was found repeats the mistake.

    In the orphaned-export state, `would_reissue_an_id` is False. Whatever each
    command below decides to do, none of them may assert that consequence.
    """
    clone, _modified, added = pulled_behind
    assert invoke(["--root", str(clone), "rebuild", "--force"]).code == 0
    document = json.loads(
        (clone / "kyosei" / "export" / f"{added}.json").read_text(encoding="utf-8")
    )
    document["id"] = f"{document['id']}0"
    (clone / "kyosei" / "export" / f"{document['id']}.json").write_text(
        json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    for command in (
        ["brief"],
        ["brief", "--json"],
        ["new", "bug", "--title", "a write"],
        ["export", "--refresh"],
        ["list"],
    ):
        result = invoke(["--root", str(clone), *command])
        said = result.out + result.err
        assert "reissue" not in said, (command, said)
        assert "already handed out" not in said, (command, said)
        assert "already issued" not in said, (command, said)


def test_export_refresh_does_not_overwrite_a_pulled_export_from_an_older_database(
    pulled_behind,
) -> None:
    """KYO-81.c1/.c3 over the genuine article: a state git produced, not one built by
    hand. `tests/test_export_refresh.py` asserts the same refusal over a hand-built
    export-ahead store; this one asserts it over an actual pull, because the record
    is about what happens to a second checkout.

    `cmd_export` allocates nothing, so it opens no `Store.transaction()`, so KYO-75's
    guard — placed on that seam so that no future writer has to remember it — never
    sees it. It reached `refresh_exports` and `write_allocations` directly and did the
    guard's whole damage: every pulled record document rewritten from the older
    database, every pulled counter lowered, exit code 0, no warning.
    """
    clone, modified, _added = pulled_behind
    allocations = clone / "kyosei" / "export" / "_allocations.json"
    counters_before = allocations.read_text(encoding="utf-8")
    document_before = (clone / "kyosei" / "export" / f"{modified}.json").read_text(
        encoding="utf-8"
    )

    refused = invoke(["--root", str(clone), "export", "--refresh"])
    assert refused.code != 0, refused
    said = refused.out + refused.err
    assert "database is behind its export" in said, said
    assert "kyo rebuild --force" in said, said
    assert "--force" in said, said

    assert allocations.read_text(encoding="utf-8") == counters_before
    assert (clone / "kyosei" / "export" / f"{modified}.json").read_text(
        encoding="utf-8"
    ) == document_before

    # The remedy the message names clears it, and clears it in the direction that
    # keeps the pulled work rather than the direction `--force` would take (KYO-5).
    assert invoke(["--root", str(clone), "rebuild", "--force"]).code == 0
    refreshed = invoke(["--root", str(clone), "export", "--refresh", "--json"])
    assert refreshed.code == 0, refreshed
    assert refreshed.json["forced_over_export_ahead"] is False
