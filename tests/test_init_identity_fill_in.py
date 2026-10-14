"""KYO-559: a store that records no ``code_repo_id`` gets one filled in -- by `kyo
doctor`, the session-start brief or a re-run of `kyo init` -- once the code repository
has a root commit, without ``--adopt``; a recorded identity is never overwritten.

KYO-569: `kyo init` on a directory that does not exist never says git is missing, and a
``$KYOSEI_HOME`` reached through a symlinked parent does not crash it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from kyosei import scaffold
from kyosei.config import load_config, write_config

from .conftest import invoke


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo: Path, message: str = "first") -> str:
    (repo / "README").write_text(f"{message}\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "-m", message)
    return _git(repo, "rev-list", "--max-parents=0", "HEAD").stdout.strip()


def _commitless_project(tmp_path: Path, name: str = "proj") -> tuple[Path, Path]:
    """`git init` then `kyo init --defaults`: the order an agent takes (KYO-559)."""
    code = tmp_path / name
    code.mkdir()
    _git(code, "init", "-q", "-b", "main")
    run = invoke(["init", "--defaults", str(code), "--json"])
    assert run.code == 0, run.err
    store = Path(json.loads(run.out)["store_root"])
    assert load_config(store).code_repo_id is None
    return code, store


# ------------------------------------------------------------------ KYO-559


def test_doctor_records_a_missing_identity_after_the_first_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """c1 and c5: commitless `git init` + `kyo init`, a commit, then `kyo doctor` -- the
    identity is recorded with no --adopt, one line says so, and the store commits it."""
    code, store = _commitless_project(tmp_path)
    root = _commit(code)
    monkeypatch.chdir(code)
    run = invoke(["doctor", "--json"])
    data = json.loads(run.out)
    assert load_config(store).code_repo_id == root
    lines = data["checks"]["code_repo_identity"]["failures"]
    assert len(lines) == 1 and root in lines[0] and "recorded" in lines[0]
    assert data["checks"]["code_repo_identity"]["tier"] == "notice"
    assert "record the code repository's identity" in _git(store, "log", "-1", "--format=%s").stdout
    assert _git(store, "status", "--porcelain").stdout.strip() == ""
    # Done once: the next run has nothing to say.
    again = json.loads(invoke(["doctor", "--json"]).out)
    assert again["checks"]["code_repo_identity"]["failures"] == []


def test_doctor_records_nothing_before_the_first_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, store = _commitless_project(tmp_path)
    monkeypatch.chdir(code)
    data = json.loads(invoke(["doctor", "--json"]).out)
    assert data["checks"]["code_repo_identity"]["failures"] == []
    assert load_config(store).code_repo_id is None


def test_session_start_brief_records_a_missing_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """c6: the same fill-in, from `kyo brief --session-start`, with one line."""
    code, store = _commitless_project(tmp_path)
    root = _commit(code)
    monkeypatch.chdir(code)
    plain = invoke(["brief", "--compact"])
    assert plain.code == 0, plain.err
    assert load_config(store).code_repo_id is None, "only a session-start brief writes"
    run = invoke(["brief", "--compact", "--session-start"])
    assert run.code == 0, run.err
    assert load_config(store).code_repo_id == root
    assert sum(root in line for line in run.out.splitlines()) == 1
    assert "recorded this code repository's identity" in run.out


@pytest.mark.parametrize("surface", ["doctor", "brief", "init", "function"])
def test_no_automatic_path_overwrites_a_recorded_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, surface: str
) -> None:
    """c7: a store that records a DIFFERENT identity keeps it through every automatic
    path -- the mismatch is `kyo init --adopt`'s, never a fill-in."""
    code, store = _commitless_project(tmp_path)
    _commit(code)
    stored = load_config(store)
    write_config(store, project_prefix=stored.project_prefix, code_repo_id="0" * 40)
    monkeypatch.chdir(code)
    if surface == "doctor":
        invoke(["doctor", "--json"])
    elif surface == "brief":
        invoke(["brief", "--compact", "--session-start"])
    elif surface == "init":
        assert invoke(["init"]).code == 1
    else:
        assert scaffold.fill_in_code_repo_id(store, code) == []
    assert load_config(store).code_repo_id == "0" * 40


def test_a_mismatched_identity_is_still_refused_without_adopt(tmp_path: Path) -> None:
    """c2: adoption of a store recording another repository's identity is refused
    without --adopt and allowed with it."""
    first = tmp_path / "a" / "api"
    first.mkdir(parents=True)
    _git(first, "init", "-q", "-b", "main")
    _commit(first, "one")
    assert invoke(["init", "--defaults", str(first)]).code == 0
    other = tmp_path / "b" / "api"
    other.mkdir(parents=True)
    _git(other, "init", "-q", "-b", "main")
    _commit(other, "two")
    refused = invoke(["init", "--defaults", str(other)])
    assert refused.code == 1
    assert "--adopt" in refused.err and "different code repository" in refused.err
    assert not (other / ".kyosei").exists()
    assert invoke(["init", "--defaults", str(other), "--adopt"]).code == 0


def test_the_no_commit_message_names_the_automatic_path(tmp_path: Path) -> None:
    """c3."""
    code = tmp_path / "proj"
    code.mkdir()
    _git(code, "init", "-q", "-b", "main")
    run = invoke(["init", "--defaults", str(code), "--json"])
    note = next(n for n in json.loads(run.out)["git"] if "no commits yet" in n)
    assert "kyo doctor" in note and "session-start brief" in note
    assert "kyo init --adopt" not in note
    assert scaffold.IDENTITY_FILL_IN_PATH in note


def test_rerunning_init_prints_what_changed_and_no_layout_explanation(
    tmp_path: Path,
) -> None:
    """c4 (and c5 by the re-run route): bare `kyo init` again, after the first commit,
    records the identity and says only that."""
    code, store = _commitless_project(tmp_path)
    root = _commit(code)
    run = invoke(["init", str(code)])
    assert run.code == 0, run.err
    assert load_config(store).code_repo_id == root
    assert "this run changed" in run.out and root in run.out
    for layout_text in ("--explain", "record ids will look like", "store layout", "store: "):
        assert layout_text not in run.out
    # With nothing left to change, the re-run keeps its old answer.
    assert "already links to a store" in invoke(["init", str(code)]).err


# ------------------------------------------------------------------ KYO-569


def test_init_on_a_missing_directory_creates_it_and_never_says_git_is_missing(
    tmp_path: Path,
) -> None:
    """KYO-569.c1."""
    target = tmp_path / "not" / "yet"
    run = invoke(["init", "--defaults", str(target), "--json"])
    assert run.code == 0, run.err
    assert "git is not on PATH" not in run.out + run.err
    assert (target / ".git").is_dir()
    assert json.loads(run.out)["code_repo_id"] is not None


def test_git_in_a_missing_directory_names_the_directory() -> None:
    """KYO-569.c1, the class: `_git` itself never reads a missing cwd as missing git."""
    missing = Path("/nonexistent-kyo569/dir")
    with pytest.raises(Exception) as caught:
        scaffold._git(missing, "status")
    assert str(missing) in str(caught.value)
    assert "PATH" not in str(caught.value)


def test_init_defaults_under_a_symlinked_store_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KYO-569.c2: $KYOSEI_HOME reached through a symlinked parent (macOS /var)."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    monkeypatch.setenv("KYOSEI_HOME", str(link / "home"))
    code = link / "proj"
    code.mkdir()
    _git(code, "init", "-q", "-b", "main")
    _commit(code)
    run = invoke(["init", "--defaults", str(code), "--json"])
    assert run.code == 0, run.err
    data = json.loads(run.out)
    assert (real / "home" / "proj" / "kyosei" / "config.yml").is_file()
    assert "kyosei/config.yml" in data["written"]
    # And the text form, whose file list is where the crash was.
    code2 = link / "proj2"
    code2.mkdir()
    text = invoke(["init", "--defaults", str(code2)])
    assert text.code == 0, text.err
    assert "files in the store" in text.out


# ------------------------------------------------------------------ KYO-635


def _replaced_history(tmp_path: Path) -> tuple[Path, Path, str, str]:
    """A project whose store records its first root commit, after which the code
    repository's history is replaced by a fresh one-commit history (a new root)."""
    code = tmp_path / "proj"
    code.mkdir()
    _git(code, "init", "-q", "-b", "main")
    old_root = _commit(code, "old")
    run = invoke(["init", "--defaults", str(code), "--json"])
    assert run.code == 0, run.err
    store = Path(json.loads(run.out)["store_root"])
    assert load_config(store).code_repo_id == old_root
    _git(code, "checkout", "-q", "--orphan", "fresh")
    _git(code, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "-m", "fresh")
    _git(code, "branch", "-q", "-D", "main")
    _git(code, "branch", "-q", "-m", "main")
    new_root = _git(code, "rev-list", "--max-parents=0", "HEAD").stdout.strip()
    assert new_root != old_root
    return code, store, old_root, new_root


def test_adopt_re_records_a_differing_identity(tmp_path: Path) -> None:
    """KYO-635.c1: `kyo init --store <store> --adopt` in a repository whose root commit
    differs from the recorded one records the new root and says it replaced the old,
    both from a fresh checkout (no link) and from the checkout already linked."""
    code, store, old_root, new_root = _replaced_history(tmp_path)
    (code / ".kyosei").unlink()
    run = invoke(["init", str(code), "--store", str(store), "--adopt"])
    assert run.code == 0, run.err
    assert load_config(store).code_repo_id == new_root
    line = next(ln for ln in run.out.splitlines() if "replacing" in ln)
    assert new_root in line and old_root in line
    assert "re-records" in _git(store, "log", "-1", "--format=%s").stdout
    assert _git(store, "status", "--porcelain").stdout.strip() == ""


def test_adopt_re_records_through_an_existing_link(tmp_path: Path) -> None:
    code, store, old_root, new_root = _replaced_history(tmp_path)
    assert (code / ".kyosei").is_file()
    run = invoke(["init", str(code), "--store", str(store), "--adopt"])
    assert run.code == 0, run.err
    assert load_config(store).code_repo_id == new_root
    assert any(old_root in ln and new_root in ln for ln in run.out.splitlines())


def test_differing_identity_without_adopt_is_still_refused(tmp_path: Path) -> None:
    """KYO-635.c2: without --adopt the differing identity is refused, linked or not,
    and nothing is re-recorded."""
    code, store, old_root, _ = _replaced_history(tmp_path)
    linked = invoke(["init", str(code), "--store", str(store)])
    assert linked.code == 1 and "already links to a store" in linked.err
    (code / ".kyosei").unlink()
    refused = invoke(["init", str(code), "--store", str(store)])
    assert refused.code == 1
    assert "different code repository" in refused.err and "--adopt" in refused.err
    assert not (code / ".kyosei").exists()
    assert load_config(store).code_repo_id == old_root


def test_doctor_reports_a_differing_identity_and_the_remedy_clears_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KYO-635.c3: doctor reports the mismatch as a problem naming the exact command,
    never rewrites the identity itself, and running that command clears the finding."""
    code, store, old_root, new_root = _replaced_history(tmp_path)
    monkeypatch.chdir(code)
    run = invoke(["doctor", "--json"])
    assert run.code == 3
    check = json.loads(run.out)["checks"]["code_repo_identity_mismatch"]
    assert check["tier"] == "problem"
    [finding] = check["failures"]
    assert old_root in finding and new_root in finding
    remedy = f"kyo init --store {store} --adopt"
    assert f"`{remedy}`" in finding
    assert load_config(store).code_repo_id == old_root, "doctor never rewrites it"
    argv = remedy.split()[1:]
    assert invoke(argv).code == 0
    after = json.loads(invoke(["doctor", "--json"]).out)
    assert after["checks"]["code_repo_identity_mismatch"]["failures"] == []
    assert load_config(store).code_repo_id == new_root
