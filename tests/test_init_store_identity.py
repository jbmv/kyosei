"""KYO-416: `kyo init` and `kyo install-hooks` stop deciding things about the host
from weak evidence.

* A split store records which code repository it was scaffolded for (``code_repo_id``,
  the root-commit sha set), and adoption compares against it: a store at the default
  path ``$KYOSEI_HOME/<basename>`` is evidence of a shared directory name and nothing
  more, so ``~/oss/api`` must not silently link to ``~/work/api``'s store.
* ``--prefix`` never silently loses to an adopted store's prefix.
* The advisory ``pre-push`` hook is only written inside the repository's own git dir,
  never into a global ``core.hooksPath`` or a tracked ``.husky/``.
* The scaffold commit is pinned against the host's git config (signing, hooks) and
  against an inherited ``GIT_DIR``.
* `kyo init` leads with where the store goes and what moves it.
"""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from kyosei import install_hooks, scaffold
from kyosei.config import SCHEMA_VERSION, config_path_for, load_config
from kyosei.errors import KyoseiError
from kyosei.git_queries import _git_history_identity

from .conftest import git_init, invoke


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _repo(path: Path, message: str) -> Path:
    """A code repository with one commit whose message makes its root sha unique."""
    path.mkdir(parents=True)
    (path / "README").write_text(f"{message}\n", encoding="utf-8")
    git_init(path, commit_message=message)
    return path


def _root_sha(repo: Path) -> str:
    return _git(repo, "rev-list", "--max-parents=0", "HEAD").stdout.strip()


def _commit_count(repo: Path) -> int:
    return int(_git(repo, "rev-list", "--count", "HEAD").stdout.strip())


# ------------------------------------------------------ c1, c5: identity mismatch


def test_a_same_basename_repository_is_refused_the_other_projects_store(
    tmp_path: Path, store_home: Path
) -> None:
    """c5 (and c1's refusal): remove the identity comparison in `init_layout` and the
    second init links to the first project's store and exits 0."""
    work = _repo(tmp_path / "work" / "api", "work api")
    oss = _repo(tmp_path / "oss" / "api", "oss api")
    first = invoke(["init", "--defaults", str(work), "--json"])
    assert first.code == 0, first
    store = Path(first.json["store_root"])
    assert store == store_home / "api"

    refused = invoke(["init", "--defaults", str(oss)])
    assert refused.code == 1, refused
    # Names both repositories (by the only identity the store may carry — KYO-52
    # keeps machine paths out of it), the store path, and both remedies.
    assert str(oss) in refused.err
    assert _root_sha(work) in refused.err and _root_sha(oss) in refused.err
    assert str(store) in refused.err
    assert "--adopt" in refused.err and "--store DIR" in refused.err
    assert not (oss / ".kyosei").exists(), "a refusal links nothing"


def test_adopt_clears_the_identity_refusal_and_links_the_store(
    tmp_path: Path,
) -> None:
    """c1's remedy: `--adopt` links the store, and says it overrode a mismatch."""
    work = _repo(tmp_path / "work" / "api", "work api")
    oss = _repo(tmp_path / "oss" / "api", "oss api")
    store = Path(invoke(["init", "--defaults", str(work), "--json"]).json["store_root"])
    record = invoke(["--root", str(work), "new", "bug", "--title", "t", "--json"])
    record_id = record.json["record"]["id"]
    assert invoke(["init", "--defaults", str(oss)]).code == 1

    adopted = invoke(["init", str(oss), "--adopt", "--json"])
    assert adopted.code == 0, adopted
    assert adopted.json["adopted"] is True
    assert adopted.json["adoption_check"] == "overridden"
    assert Path(adopted.json["store_root"]) == store
    assert "--adopt" in "\n".join(adopted.json["git"])
    assert invoke(["--root", str(oss), "show", record_id]).code == 0


def test_a_second_checkout_of_the_same_history_adopts_silently_and_verified(
    tmp_path: Path,
) -> None:
    """The case adoption exists for still works with no flag: a clone shares the root."""
    origin = _repo(tmp_path / "one" / "proj", "proj")
    assert invoke(["init", "--defaults", str(origin)]).code == 0
    clone = tmp_path / "two" / "proj"
    clone.parent.mkdir()
    _git(clone.parent, "clone", "-q", str(origin), "proj")
    linked = invoke(["init", "--defaults", str(clone), "--json"])
    assert linked.code == 0, linked
    assert linked.json["adoption_check"] == "verified"


def test_the_identity_is_the_one_sweeps_record(tmp_path: Path) -> None:
    """scaffold.code_repo_identity must stay the same value as the sweep's
    `code_repo_id` — two derivations of one fact, held equal here."""
    repo = _repo(tmp_path / "r", "r")
    assert scaffold.code_repo_identity(repo) == _git_history_identity(repo)
    assert scaffold.code_repo_identity(repo) == _root_sha(repo)
    empty = tmp_path / "empty"
    empty.mkdir()
    _git(empty, "init", "-q")
    assert scaffold.code_repo_identity(empty) is None
    assert _git_history_identity(empty) is None


# ------------------------------------------------------------------ c2: --prefix


def test_a_prefix_that_contradicts_the_adopted_store_is_refused(tmp_path: Path) -> None:
    origin = _repo(tmp_path / "one" / "proj", "proj")
    assert invoke(["init", "--defaults", str(origin), "--prefix", "AAA"]).code == 0
    clones = []
    for name in ("two", "three"):
        (tmp_path / name).mkdir()
        _git(tmp_path / name, "clone", "-q", str(origin), "proj")
        clones.append(tmp_path / name / "proj")

    refused = invoke(["init", "--defaults", str(clones[0]), "--prefix", "ZZZ"])
    assert refused.code == 1, refused
    assert "ZZZ" in refused.err and "AAA" in refused.err

    # Remedies: re-run without --prefix, or with the store's own.
    plain = invoke(["init", "--defaults", str(clones[0]), "--json"])
    assert plain.code == 0, plain
    assert plain.json["project_prefix"] == "AAA"
    same = invoke(["init", "--defaults", str(clones[1]), "--prefix", "aaa", "--json"])
    assert same.code == 0, same
    assert same.json["project_prefix"] == "AAA"


# --------------------------------------------------------- c3: unverifiable cases
#
# The choice (recorded in the report): on the implicit default path an unverifiable
# adoption is REFUSED, because a shared basename is the only evidence; on an explicit
# `--store DIR` (README's migration flow ends there) it is ALLOWED and stated as
# UNVERIFIED in the output and the JSON. `--adopt` allows it on either route.


def _strip_identity(store: Path) -> None:
    config = config_path_for(store)
    text = config.read_text(encoding="utf-8")
    kept = [ln for ln in text.splitlines() if not ln.startswith("code_repo_id:")]
    config.write_text("\n".join(kept) + "\n", encoding="utf-8")
    assert load_config(store).code_repo_id is None


def test_a_store_with_no_recorded_identity_is_never_adopted_silently(
    tmp_path: Path,
) -> None:
    first = _repo(tmp_path / "a" / "proj", "a")
    store = Path(invoke(["init", "--defaults", str(first), "--json"]).json["store_root"])
    _strip_identity(store)  # every store scaffolded before KYO-416
    second = _repo(tmp_path / "b" / "proj", "b")

    refused = invoke(["init", "--defaults", str(second)])
    assert refused.code == 1, refused
    assert "unverified" in refused.err and "records no code repository" in refused.err
    assert "--adopt" in refused.err and "--store DIR" in refused.err

    named = invoke(["init", str(second), "--store", str(store), "--json"])
    assert named.code == 0, named
    assert named.json["adoption_check"] == "unverified"
    assert any("UNVERIFIED" in note for note in named.json["git"])


def test_a_code_repository_with_no_commits_is_never_adopted_silently(
    tmp_path: Path,
) -> None:
    """The verifier's repro: `git init` in a scratch `.../q/p`, then `kyo init`, with a
    leftover store at `$KYOSEI_HOME/p`."""
    leftover = _repo(tmp_path / "old" / "p", "old")
    assert invoke(["init", "--defaults", str(leftover), "--prefix", "PKY"]).code == 0
    fresh = tmp_path / "q" / "p"
    fresh.mkdir(parents=True)
    _git(fresh, "init", "-q")

    refused = invoke(["init", "--defaults", str(fresh)])
    assert refused.code == 1, refused
    assert "no commits yet" in refused.err
    assert "--adopt" in refused.err

    adopted = invoke(["init", str(fresh), "--adopt", "--json"])
    assert adopted.code == 0, adopted
    assert adopted.json["adoption_check"] == "unverified"
    assert any("UNVERIFIED" in note and "no commits" in note for note in adopted.json["git"])


# --------------------------------------------------------------- c4: the config key


def test_split_init_records_the_code_repository_identity(tmp_path: Path) -> None:
    code = _repo(tmp_path / "proj", "proj")
    result = invoke(["init", "--defaults", str(code), "--json"])
    assert result.code == 0, result
    store = Path(result.json["store_root"])
    config = load_config(store)
    assert config.code_repo_id == _git_history_identity(code)
    assert config.schema_version == SCHEMA_VERSION
    # Recorded before the first commit: the store's history carries it.
    assert _git(store, "status", "--porcelain").stdout == ""
    assert "code_repo_id" in _git(store, "show", "HEAD:kyosei/config.yml").stdout
    doctor = invoke(["--root", str(code), "doctor", "--json"])
    assert doctor.json["checks"]["config_consumers"]["failures"] == []


def test_the_key_survives_a_rewrite_of_the_config(tmp_path: Path) -> None:
    """Optional keys are carried by write_config; a rebuild must not drop this one."""
    code = _repo(tmp_path / "proj", "proj")
    store = Path(invoke(["init", "--defaults", str(code), "--json"]).json["store_root"])
    before = load_config(store).code_repo_id
    assert invoke(["--root", str(code), "rebuild", "--force"]).code == 0
    assert load_config(store).code_repo_id == before


def test_a_malformed_identity_is_refused_and_deleting_the_line_clears_it(
    tmp_path: Path,
) -> None:
    code = _repo(tmp_path / "proj", "proj")
    store = Path(invoke(["init", "--defaults", str(code), "--json"]).json["store_root"])
    config = config_path_for(store)
    text = config.read_text(encoding="utf-8")
    config.write_text(
        "\n".join(
            "code_repo_id: 5" if ln.startswith("code_repo_id:") else ln
            for ln in text.splitlines()
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(KyoseiError, match="delete the line"):
        load_config(store)
    _strip_identity(store)  # the remedy the message names


# ------------------------------------------------------------ c6: shared hooks dir


def _pre_push_note(result) -> str:
    return "\n".join(result.json.get("notes", []) + [result.err, result.out])


def test_install_hooks_does_not_write_into_a_hooks_path_outside_the_repository(
    tmp_path: Path, monkeypatch
) -> None:
    shared = tmp_path / "dotfiles-githooks"
    shared.mkdir()
    global_config = tmp_path / "gitconfig"
    global_config.write_text(f"[core]\n\thooksPath = {shared}\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    code = _repo(tmp_path / "proj", "proj")
    assert invoke(["init", str(code), "--in-repo"]).code == 0

    result = invoke(["--root", str(code), "install-hooks", "--copies", "--json"])
    assert result.code == 0, result
    assert not (shared / "pre-push").exists(), "a global hooks directory is not ours"
    note = _pre_push_note(result)
    assert str(shared) in note
    assert install_hooks.PRE_PUSH_MANUAL_LINE in note


def test_install_hooks_does_not_add_a_tracked_file_to_a_husky_directory(
    tmp_path: Path,
) -> None:
    code = _repo(tmp_path / "proj", "proj")
    (code / ".husky").mkdir()
    _git(code, "config", "core.hooksPath", ".husky")
    assert invoke(["init", str(code), "--in-repo"]).code == 0
    result = invoke(["--root", str(code), "install-hooks", "--copies", "--json"])
    assert result.code == 0, result
    assert not (code / ".husky" / "pre-push").exists()
    assert ".husky" in _pre_push_note(result)


def test_install_hooks_still_writes_the_repositorys_own_hooks_directory(
    tmp_path: Path,
) -> None:
    code = _repo(tmp_path / "proj", "proj")
    assert invoke(["init", str(code), "--in-repo"]).code == 0
    assert invoke(["--root", str(code), "install-hooks", "--copies"]).code == 0
    assert (code / ".git" / "hooks" / "pre-push").is_file()


# ------------------------------------------------ c7, c8: the scaffold commit is ours


def _hostile_global_config(tmp_path: Path) -> Path:
    hooks = tmp_path / "global-hooks"
    hooks.mkdir()
    pre_commit = hooks / "pre-commit"
    pre_commit.write_text("#!/bin/sh\necho refused by a global hook >&2\nexit 1\n")
    pre_commit.chmod(pre_commit.stat().st_mode | stat.S_IEXEC)
    config = tmp_path / "hostile-gitconfig"
    config.write_text(
        "[commit]\n\tgpgsign = true\n"
        "[user]\n\tsigningkey = KYO416-NO-SUCH-KEY\n"
        "[gpg]\n\tprogram = false\n"
        f"[core]\n\thooksPath = {hooks}\n",
        encoding="utf-8",
    )
    return config


def test_the_scaffold_commit_survives_signing_and_a_failing_global_hook(
    tmp_path: Path, monkeypatch
) -> None:
    code = tmp_path / "proj"
    code.mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(_hostile_global_config(tmp_path)))
    result = invoke(["init", "--defaults", str(code), "--json"])
    assert result.code == 0, result
    store = Path(result.json["store_root"])
    assert any("committed the scaffold" in n for n in result.json["git"]), result.json["git"]
    assert _commit_count(store) == 1


def test_the_hostile_config_really_does_block_an_unpinned_commit(
    tmp_path: Path, monkeypatch
) -> None:
    """Control for the test above: without the pins, this config fails a commit —
    so the test above is measuring the pins, not a config git ignores."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(_hostile_global_config(tmp_path)))
    repo = tmp_path / "control"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "f").write_text("x\n")
    _git(repo, "add", "-A")
    base = ["git", "-c", "user.name=T", "-c", "user.email=t@e"]
    signed = subprocess.run(
        [*base, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "m"],
        cwd=repo, capture_output=True, text=True,
    )
    assert signed.returncode != 0, "gpgsign with no usable key must fail unpinned"
    hooked = subprocess.run(
        [*base, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "m"],
        cwd=repo, capture_output=True, text=True,
    )
    assert hooked.returncode != 0 and "refused by a global hook" in hooked.stderr


def test_an_inherited_git_dir_does_not_redirect_the_scaffold(
    tmp_path: Path, monkeypatch
) -> None:
    other = _repo(tmp_path / "other", "other")
    commits_before = _commit_count(other)
    code = _repo(tmp_path / "proj", "proj")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    result = invoke(["init", "--defaults", str(code), "--json"])
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_WORK_TREE")
    monkeypatch.delenv("GIT_INDEX_FILE")
    assert result.code == 0, result
    store = Path(result.json["store_root"])
    assert _commit_count(other) == commits_before, "the other repository gained a commit"
    assert (store / ".git").is_dir() and _commit_count(store) == 1
    # And the identity recorded is this repository's, not the one GIT_DIR named.
    assert load_config(store).code_repo_id == _root_sha(code)


def test_a_git_call_that_hangs_is_stopped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scaffold, "GIT_TIMEOUT_SECONDS", 0.001)
    result = scaffold._git(tmp_path, "--version")
    assert result is not None and result.returncode == 124
    assert "did not finish" in result.stderr


# --------------------------------------------------------- c9, c10: host side effects


def test_in_repo_init_creates_nothing_under_the_store_home(
    tmp_path: Path, store_home: Path
) -> None:
    """c9 did not reproduce (see the report); this keeps it that way."""
    code = _repo(tmp_path / "proj", "proj")
    result = invoke(["init", str(code), "--in-repo"])
    assert result.code == 0, result
    # KYO-455: the one thing init writes there is the registry entry,
    # which lists the in-repo project where it is; no store and no project directory.
    assert sorted(p.name for p in store_home.iterdir()) == ["projects.json"]
    assert json.loads((store_home / "projects.json").read_text()) == {
        "projects": [{"root": str(code.resolve())}]
    }
    # KYO-558.c6: the worktree disclosure moved behind `kyo init --explain`.
    explained = invoke(["init", str(code), "--explain"])
    assert explained.code == 0, explained
    assert "creates nothing there" in explained.out


def test_init_leads_with_the_store_path_and_what_moves_it(
    tmp_path: Path, store_home: Path
) -> None:
    code = _repo(tmp_path / "proj", "proj")
    result = invoke(["init", "--defaults", str(code)])
    assert result.code == 0, result
    lines = result.out.splitlines()
    first_status = next(
        i for i, ln in enumerate(lines) if ln.startswith(("initialised", "linked"))
    )
    lead = "\n".join(lines[:first_status])
    assert str(store_home / "proj") in lead
    assert "$KYOSEI_HOME" in lead and "--store DIR" in lead


def test_adopt_contradicts_in_repo(tmp_path: Path) -> None:
    code = tmp_path / "proj"
    code.mkdir()
    result = invoke(["init", str(code), "--in-repo", "--adopt"])
    assert result.code != 0
    assert "--adopt" in result.err


# ------------------------------------------------------------------ KYO-507


def _plain_dir(tmp_path: Path, name: str = "fresh") -> Path:
    path = tmp_path / name
    path.mkdir()
    return path


def test_init_in_a_non_repository_makes_one_and_records_the_identity(tmp_path: Path) -> None:
    """KYO-507.c1: remove `_bootstrap_code_repository` and this has no repository."""
    code = _plain_dir(tmp_path)
    run = invoke(["init", "--defaults", str(code), "--json"])
    assert run.code == 0, run.err
    files = _git(code, "show", "--name-only", "--format=%s", "HEAD").stdout.split()
    assert "kyo" in _git(code, "log", "-1", "--format=%s").stdout
    assert sorted(f for f in files if f.startswith(".")) == [".gitignore", ".kyosei"]
    # KYO-558.c4: and init's setup-incomplete CLAUDE.md, in the same first commit.
    assert _git(code, "ls-files").stdout.split() == [".gitignore", ".kyosei", "CLAUDE.md"]
    assert _git(code, "status", "--porcelain").stdout.strip() == ""
    root = _root_sha(code)
    assert json.loads(run.out)["code_repo_id"] == root
    assert load_config(Path(json.loads(run.out)["store_root"])).code_repo_id == root


def test_no_git_init_keeps_the_old_behaviour(tmp_path: Path) -> None:
    """KYO-507.c2."""
    code = _plain_dir(tmp_path)
    run = invoke(["init", "--defaults", str(code), "--no-git-init", "--json"])
    assert run.code == 0, run.err
    assert not (code / ".git").exists()
    data = json.loads(run.out)
    assert data["code_repo_id"] is None
    assert any("no commits yet, so no code-repository identity" in n for n in data["git"])


def test_a_repository_without_commits_gets_no_commit_and_a_working_remedy(
    tmp_path: Path,
) -> None:
    """KYO-507.c3: the line names the way the identity gets recorded -- since KYO-559.c3
    the automatic fill-in, not `kyo init --adopt` -- and `--adopt` after a first commit
    still records it."""
    code = _plain_dir(tmp_path)
    _git(code, "init", "-q")
    run = invoke(["init", "--defaults", str(code), "--json"])
    assert run.code == 0, run.err
    data = json.loads(run.out)
    store = Path(data["store_root"])
    assert data["code_repo_id"] is None
    note = next(n for n in data["git"] if "no commits yet" in n)
    assert scaffold.IDENTITY_FILL_IN_PATH in note
    assert _git(code, "rev-list", "--all", "--count").stdout.strip() == "0"
    assert load_config(store).code_repo_id is None

    (code / "README").write_text("x\n", encoding="utf-8")
    git_init(code, commit_message="first")
    fixed = invoke(["init", str(code), "--adopt", "--json"])
    assert fixed.code == 0, fixed.err
    assert load_config(store).code_repo_id == _root_sha(code)
    assert json.loads(fixed.out)["code_repo_id"] == _root_sha(code)
