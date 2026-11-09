"""KYO-669: a private term in a commit being pushed refuses the push.

The terms come from the machine's project registry and the store's word file, so every
name here is generated at run time and nothing private is written down. Each section is
one criterion; the real pushes go through the hook ``kyo install-hooks`` writes.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from kyosei import cmd_publish, private_terms
from kyosei.errors import EXIT_CONTRACT, EXIT_OK, EXIT_PRIVATE_TERM

from . import test_defaults_name_no_consumer as public_tree_guard
from .conftest import Run, git_init, invoke

ZERO = "0" * 40


def _made_up(length: int = 9) -> str:
    return "q" + "".join(secrets.choice("bcdfghjklmnpqrstvwxz") for _ in range(length - 1))


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@e.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def _commit(root: Path, message: str, *, author: str = "T") -> str:
    _git(root, "commit", "--allow-empty", "-q", f"--author={author} <t@e.com>", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _register(root: Path, sibling: Path, prefix: str) -> None:
    """A registry at the test's ``$KYOSEI_HOME`` listing ``root`` and one other project."""
    (sibling / "kyosei").mkdir(parents=True)
    (sibling / "kyosei" / "config.yml").write_text(
        f"project_prefix: {prefix}\n", encoding="utf-8"
    )
    home = Path(os.environ["KYOSEI_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "projects.json").write_text(
        json.dumps({"projects": [{"root": str(root)}, {"root": str(sibling)}]}),
        encoding="utf-8",
    )


class Pushable:
    """An in-repo project with a bare remote, a registered sibling and a pushed first
    commit. Its store is unacknowledged, so the store warning is live throughout."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "work"
        assert invoke(["init", str(self.root), "--in-repo", "--prefix", "TST"]).code == 0
        git_init(self.root)
        self.remote = tmp_path / "remote.git"
        _git(tmp_path, "init", "-q", "--bare", str(self.remote))
        _git(self.root, "remote", "add", "origin", str(self.remote))
        self.private = _made_up()
        self.prefix = _made_up(5).upper()
        _register(self.root, tmp_path / "projects" / self.private, self.prefix)
        self.base = _commit(self.root, "a clean first commit")
        self.branch = _git(self.root, "symbolic-ref", "--short", "HEAD")
        _git(self.root, "push", "-q", "--no-verify", "origin", self.branch)

    def pre_push(self, *ref_lines: str, remote: str = "origin") -> Run:
        return invoke(
            ["--root", str(self.root), "check-push", "--pre-push", remote],
            stdin="".join(f"{line}\n" for line in ref_lines),
        )

    def line(self, sha: str, remote_sha: str | None = None) -> str:
        return f"refs/heads/x {sha} refs/heads/x {remote_sha or ZERO}"

    def pre_tool_use(self, command: str) -> Run:
        return invoke(
            ["--root", str(self.root), "check-push", "--pre-tool-use"],
            stdin=json.dumps(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": command},
                    "cwd": str(self.root),
                }
            ),
        )

    def acknowledge(self) -> None:
        assert invoke(["--root", str(self.root), "check-push", "--acknowledge"]).code == 0


@pytest.fixture
def pushable(tmp_path: Path) -> Pushable:
    return Pushable(tmp_path)


# ------------------------------------------------------------------------ KYO-669.c1


def test_the_guard_and_check_push_share_one_matcher() -> None:
    """Moved, not copied: the test's names are the package's objects, and the refusal
    is built from the same module."""
    for name in ("compile_terms", "common_words", "read_word_file", "Terms", "HOME_PATH"):
        assert getattr(public_tree_guard, name) is getattr(private_terms, name), name
    assert cmd_publish.project_terms is private_terms.project_terms


# ------------------------------------------------------------------------ KYO-669.c2


def test_a_private_name_in_a_message_refuses_and_amending_clears_it(
    pushable: Pushable,
) -> None:
    pushable.acknowledge()
    sha = _commit(pushable.root, f"subject line\n\nabout {pushable.private} here")
    refused = pushable.pre_push(pushable.line(sha, pushable.base))
    assert refused.code == EXIT_PRIVATE_TERM, refused
    assert "push-carries-a-private-term" in refused.err
    assert f"{sha[:12]} message line 3 (project name)" in refused.err
    assert pushable.private not in refused.err + refused.out
    assert "`git commit --amend`" in refused.err

    # The remedy the refusal names, carried out.
    _git(pushable.root, "commit", "--amend", "-q", "--allow-empty", "-m", "subject line")
    amended = _git(pushable.root, "rev-parse", "HEAD")
    cleared = pushable.pre_push(pushable.line(amended, pushable.base))
    assert cleared.code == EXIT_OK, cleared


def test_a_record_id_a_listed_word_and_a_home_path_each_refuse(
    pushable: Pushable,
) -> None:
    pushable.acknowledge()
    word = _made_up(8)
    words = pushable.root / "kyosei" / private_terms.WORD_FILE
    words.write_text(f"{word}\n", encoding="utf-8")
    cases = {
        f"fixes {pushable.prefix}-12": "record prefix",
        f"the {word.upper()} menu": "listed word",
        "from /Users/someone/x": "home path",
        "from /home/someone/x": "home path",
    }
    for message, kind in cases.items():
        sha = _commit(pushable.root, message)
        refused = pushable.pre_push(pushable.line(sha, pushable.base))
        assert refused.code == EXIT_PRIVATE_TERM, (message, refused)
        assert f"message line 1 ({kind})" in refused.err
        assert message not in refused.err
        _git(pushable.root, "reset", "-q", "--hard", pushable.base)


def test_a_private_author_name_refuses_and_resetting_it_clears(pushable: Pushable) -> None:
    pushable.acknowledge()
    sha = _commit(pushable.root, "clean message", author=pushable.private)
    refused = pushable.pre_push(pushable.line(sha, pushable.base))
    assert refused.code == EXIT_PRIVATE_TERM, refused
    assert f"{sha[:12]} author name (project name)" in refused.err
    assert pushable.private not in refused.err

    _git(pushable.root, "commit", "--amend", "-q", "--allow-empty", "--reset-author", "-C", "HEAD")
    amended = _git(pushable.root, "rev-parse", "HEAD")
    assert pushable.pre_push(pushable.line(amended, pushable.base)).code == EXIT_OK


def test_a_project_with_neither_source_has_nothing_to_refuse(pushable: Pushable) -> None:
    (Path(os.environ["KYOSEI_HOME"]) / "projects.json").unlink()
    pushable.acknowledge()
    sha = _commit(pushable.root, f"{pushable.private} from /Users/someone/x")
    assert pushable.pre_push(pushable.line(sha, pushable.base)).code == EXIT_OK


def test_an_unreadable_registry_says_so_and_refuses_nothing(pushable: Pushable) -> None:
    (Path(os.environ["KYOSEI_HOME"]) / "projects.json").write_text("{", encoding="utf-8")
    sha = _commit(pushable.root, pushable.private)
    result = pushable.pre_push(pushable.line(sha, pushable.base))
    assert result.code == EXIT_OK, result
    assert "no commit was scanned" in result.out


# ------------------------------------------------------------------------ KYO-669.c3


def test_a_public_projects_own_name_does_not_trip_it(tmp_path: Path) -> None:
    pushable = Pushable(tmp_path)
    pushable.acknowledge()
    # A registry entry named for a name on the never-private list, with its prefix.
    public = tmp_path / "public" / "recx"
    (public / "kyosei").mkdir(parents=True)
    (public / "kyosei" / "config.yml").write_text("project_prefix: RECX\n", encoding="utf-8")
    registry = Path(os.environ["KYOSEI_HOME"]) / "projects.json"
    document = json.loads(registry.read_text(encoding="utf-8"))
    document["projects"].append({"root": str(public)})
    registry.write_text(json.dumps(document), encoding="utf-8")

    sha = _commit(pushable.root, "bump recx, see RECX-3, for kyosei and TST-9")
    assert pushable.pre_push(pushable.line(sha, pushable.base)).code == EXIT_OK


# ------------------------------------------------------------------------ KYO-669.c4


def _hooked(pushable: Pushable, tmp_path: Path, kyo_body: str | None = None) -> dict[str, str]:
    """Install the generated hook, and an environment whose ``kyo`` is this build."""
    assert invoke(["--root", str(pushable.root), "install-hooks", "--copies"]).code == 0
    shim = tmp_path / "shim"
    shim.mkdir(exist_ok=True)
    kyo = shim / "kyo"
    kyo.write_text(
        kyo_body or f'#!/bin/sh\nexec "{sys.executable}" -m kyosei "$@"\n', encoding="utf-8"
    )
    kyo.chmod(0o755)
    return {**os.environ, "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"}


def _push(root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "push", *args], cwd=root, env=env, capture_output=True, text=True, timeout=120
    )


def _remote_tip(pushable: Pushable, branch: str) -> str:
    return _git(pushable.remote, "rev-parse", f"refs/heads/{branch}")


def test_a_real_push_of_a_private_term_is_refused_and_no_verify_passes(
    pushable: Pushable, tmp_path: Path
) -> None:
    env = _hooked(pushable, tmp_path)
    sha = _commit(pushable.root, f"about {pushable.private}")
    refused = _push(pushable.root, env, "origin", pushable.branch)
    assert refused.returncode != 0, refused
    assert "push-carries-a-private-term" in refused.stderr
    assert pushable.private not in refused.stderr + refused.stdout
    assert _remote_tip(pushable, pushable.branch) == pushable.base

    forced = _push(pushable.root, env, "--no-verify", "origin", pushable.branch)
    assert forced.returncode == 0, forced
    assert _remote_tip(pushable, pushable.branch) == sha


def test_a_real_push_past_an_unacknowledged_store_only_warns(
    pushable: Pushable, tmp_path: Path
) -> None:
    env = _hooked(pushable, tmp_path)
    sha = _commit(pushable.root, "a clean change")
    pushed = _push(pushable.root, env, "origin", pushable.branch)
    assert pushed.returncode == 0, pushed
    assert "does not block your push" in pushed.stdout + pushed.stderr
    assert "would publish this project's Kyosei store" in pushed.stdout + pushed.stderr
    assert _remote_tip(pushable, pushable.branch) == sha


@pytest.mark.parametrize(
    "body",
    ["#!/bin/sh\nexit 1\n", "#!/bin/sh\nexit 2\n", "#!/bin/sh\nexit 64\n", "#!/bin/sh\nexit 127\n"],
)
def test_a_broken_kyo_cannot_stop_a_push(
    pushable: Pushable, tmp_path: Path, body: str
) -> None:
    env = _hooked(pushable, tmp_path, body)
    sha = _commit(pushable.root, f"about {pushable.private}")
    assert _push(pushable.root, env, "origin", pushable.branch).returncode == 0
    assert _remote_tip(pushable, pushable.branch) == sha


def test_the_manual_line_refuses_and_passes_on_the_same_terms(
    pushable: Pushable, tmp_path: Path
) -> None:
    """The line named to a consumer who keeps their own hook, under ``set -e``, with
    something of theirs after it that must still run."""
    from kyosei.install_hooks import PRE_PUSH_MANUAL_LINE

    env = _hooked(pushable, tmp_path)
    marker = tmp_path / "theirs-ran"
    hook = pushable.root / ".git" / "hooks" / "pre-push"
    hook.write_text(
        f'#!/bin/sh\nset -e\n{PRE_PUSH_MANUAL_LINE}\ntouch "{marker}"\n', encoding="utf-8"
    )
    _commit(pushable.root, f"about {pushable.private}")
    assert _push(pushable.root, env, "origin", pushable.branch).returncode != 0
    assert not marker.exists()

    _git(pushable.root, "commit", "--amend", "-q", "--allow-empty", "-m", "clean")
    assert _push(pushable.root, env, "origin", pushable.branch).returncode == 0
    assert marker.exists()


# ------------------------------------------------------------------------ KYO-669.c5


def _published_private_commit(pushable: Pushable) -> str:
    """A private term already on the remote: no later push sends it."""
    pushable.acknowledge()
    sha = _commit(pushable.root, f"old {pushable.private}")
    _git(pushable.root, "push", "-q", "--no-verify", "origin", pushable.branch)
    return sha


def test_commits_the_remote_already_has_are_not_read(pushable: Pushable) -> None:
    old = _published_private_commit(pushable)
    new = _commit(pushable.root, "a clean change")
    assert pushable.pre_push(pushable.line(new, old)).code == EXIT_OK


def test_only_the_commits_a_ref_line_sends_are_named(pushable: Pushable) -> None:
    pushable.acknowledge()
    first = _commit(pushable.root, "clean")
    bad = _commit(pushable.root, pushable.private)
    refused = pushable.pre_push(pushable.line(bad, first))
    assert refused.code == EXIT_PRIVATE_TERM
    assert bad[:12] in refused.err and first[:12] not in refused.err


def test_a_branch_with_no_counterpart_on_the_remote(pushable: Pushable) -> None:
    """The remote sha is zeros. Only what is new is read: the same branch is clean when
    it adds nothing private, whatever its published history holds, and is refused when
    it adds something private."""
    old = _published_private_commit(pushable)
    _git(pushable.root, "switch", "-q", "-c", "feature")
    assert pushable.pre_push(pushable.line(old)).code == EXIT_OK
    clean = _commit(pushable.root, "a clean change")
    assert pushable.pre_push(pushable.line(clean)).code == EXIT_OK
    bad = _commit(pushable.root, f"new {pushable.private}")
    refused = pushable.pre_push(pushable.line(bad))
    assert refused.code == EXIT_PRIVATE_TERM
    assert bad[:12] in refused.err and old[:12] not in refused.err


def test_a_first_push_to_a_remote_with_nothing_reads_the_whole_branch(
    pushable: Pushable, tmp_path: Path
) -> None:
    old = _published_private_commit(pushable)
    fresh = tmp_path / "fresh.git"
    _git(tmp_path, "init", "-q", "--bare", str(fresh))
    _git(pushable.root, "remote", "add", "fresh", str(fresh))
    refused = pushable.pre_push(pushable.line(old), remote="fresh")
    assert refused.code == EXIT_PRIVATE_TERM
    assert old[:12] in refused.err


def test_a_deletion_sends_nothing_and_a_remote_sha_never_fetched_is_tolerated(
    pushable: Pushable,
) -> None:
    pushable.acknowledge()
    bad = _commit(pushable.root, pushable.private)
    deleting = pushable.pre_push(f"(delete) {ZERO} refs/heads/x {bad}")
    assert deleting.code == EXIT_OK, deleting
    unfetched = pushable.pre_push(pushable.line(bad, "a" * 40))
    assert unfetched.code == EXIT_PRIVATE_TERM, unfetched


def test_a_real_push_of_a_new_branch_goes_through_the_hook(
    pushable: Pushable, tmp_path: Path
) -> None:
    env = _hooked(pushable, tmp_path)
    _published_private_commit(pushable)
    _git(pushable.root, "switch", "-q", "-c", "feature")
    clean = _commit(pushable.root, "a clean change")
    assert _push(pushable.root, env, "-u", "origin", "feature").returncode == 0
    assert _remote_tip(pushable, "feature") == clean

    _git(pushable.root, "switch", "-q", "-c", "other")
    _commit(pushable.root, f"new {pushable.private}")
    assert _push(pushable.root, env, "origin", "other").returncode != 0


@pytest.mark.parametrize(
    "command",
    [
        "git push",
        "git push origin {branch}",
        "git push -u origin HEAD",
        "git push origin +{branch}:refs/heads/elsewhere",
        "git push --force-with-lease origin {branch}",
        "git push --all origin",
        "git push -o ci.skip origin {branch}",
        "git -C {root} push origin {branch}",
    ],
)
def test_the_agents_push_is_refused_from_the_range_it_would_send(
    pushable: Pushable, command: str
) -> None:
    pushable.acknowledge()
    bad = _commit(pushable.root, f"about {pushable.private}")
    refused = pushable.pre_tool_use(
        command.format(branch=pushable.branch, root=pushable.root)
    )
    assert refused.code == EXIT_CONTRACT, refused
    assert "push-carries-a-private-term" in refused.err
    assert bad[:12] in refused.err
    assert pushable.private not in refused.err
    assert "--no-verify" not in refused.err


@pytest.mark.parametrize(
    "command",
    [
        "git push --dry-run",
        "git push -n origin {branch}",
        "git push --delete origin other",
        "git push origin :other",
        "git push origin {base}:refs/heads/older",
    ],
)
def test_an_agents_push_that_sends_no_such_commit_is_allowed(
    pushable: Pushable, command: str
) -> None:
    pushable.acknowledge()
    _commit(pushable.root, f"about {pushable.private}")
    result = pushable.pre_tool_use(command.format(branch=pushable.branch, base=pushable.base))
    assert result.code == EXIT_OK, result


def test_the_agents_new_branch_is_read_without_its_published_history(
    pushable: Pushable,
) -> None:
    _published_private_commit(pushable)
    _git(pushable.root, "switch", "-q", "-c", "feature")
    _commit(pushable.root, "a clean change")
    assert pushable.pre_tool_use("git push -u origin feature").code == EXIT_OK
    bad = _commit(pushable.root, f"new {pushable.private}")
    refused = pushable.pre_tool_use("git push -u origin feature")
    assert refused.code == EXIT_CONTRACT
    assert bad[:12] in refused.err


def test_the_store_refusal_still_comes_first_for_an_agent(pushable: Pushable) -> None:
    _commit(pushable.root, f"about {pushable.private}")
    refused = pushable.pre_tool_use("git push")
    assert refused.code == EXIT_CONTRACT
    assert "push-publishes-the-store" in refused.err
