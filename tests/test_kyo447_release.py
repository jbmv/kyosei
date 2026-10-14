"""KYO-447: `make release` -- tag, build the wheel only, verify the stamp, publish.

Every test runs the real Makefile target in a THROWAWAY git repository holding a copy of
this checkout's build inputs (Makefile, pyproject.toml, README.md, .gitignore,
build_backend/, src/), so the tags it creates never touch this repository and nothing is
pushed anywhere. Publishing goes to a local index (a closed port, or a stub server on
127.0.0.1) through `PUBLISH_URL`; nothing here can reach PyPI.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD_INPUTS = ("Makefile", "pyproject.toml", "README.md", ".gitignore", "build_backend", "src")
TOKEN = "pypi-KYO447-NEVER-PRINT-ME"

pytestmark = pytest.mark.skipif(
    shutil.which("uv") is None or shutil.which("make") is None, reason="needs uv and make"
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MAKE", "MFLAGS"))}
    for name in ("TOKEN_FILE", "DRY_RUN", "PUBLISH_URL", "RELEASE_DIST", "RELEASE_BUILD"):
        env.pop(name, None)
    env.update(
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null",
    )
    return env


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A committed, clean copy of this checkout's build inputs -- its working-tree content."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", *BUILD_INPUTS], cwd=ROOT, check=True, capture_output=True
    ).stdout.decode().split("\0")
    dest = tmp_path / "repo"
    for rel in filter(None, listed):
        source = ROOT / rel
        if source.is_file():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest / rel)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=dest, check=True, env=_env())
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True, env=_env())
    subprocess.run(["git", "commit", "-qm", "init"], cwd=dest, check=True, env=_env())
    return dest


def release(repo: Path, *args: str, env_extra: dict | None = None):
    env = _env()
    env.update(env_extra or {})
    return subprocess.run(
        ["make", "--no-print-directory", "release", *args],
        cwd=repo, env=env, capture_output=True, text=True, timeout=180,
    )


def tags(repo: Path) -> list[str]:
    return _git(repo, "tag").split()


def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "pypi-token"
    path.write_text(TOKEN + "\n")
    return path


def stamp_of(wheel: Path) -> dict:
    with zipfile.ZipFile(wheel) as archive:
        return json.loads(archive.read("kyosei/_build_stamp.json"))


# --- c1: each refusal, named ------------------------------------------------------------


def test_refuses_a_dirty_tree(repo: Path):
    (repo / "stray.txt").write_text("uncommitted\n")
    result = release(repo, "DRY_RUN=1")
    assert result.returncode != 0
    assert "working tree is not clean" in result.stderr
    assert tags(repo) == []


def test_refuses_a_version_already_tagged(repo: Path):
    _git(repo, "tag", "v0.1.0")
    result = release(repo, "DRY_RUN=1")
    assert result.returncode != 0
    assert "v0.1.0 is already tagged" in result.stderr
    assert tags(repo) == ["v0.1.0"]  # the existing tag is left alone


def test_refuses_a_stamp_whose_commit_is_not_head(repo: Path, tmp_path: Path):
    """A fake build writes a wheel whose stamp names another commit; the tag is undone."""
    fake = tmp_path / "fake_build.py"
    fake.write_text(
        "import json, os, sys, zipfile\n"
        "out = sys.argv[1]; os.makedirs(out, exist_ok=True)\n"
        "open(os.path.join(os.path.dirname(out) or '.', '..', 'venv-seen'), 'w')"
        ".write(os.environ.get('VIRTUAL_ENV', 'unset'))\n"
        "stamp = {'commit': '0' * 40, 'dirty': False, 'release': True, 'version': '0.1.0'}\n"
        "with zipfile.ZipFile(os.path.join(out, 'kyosei-0.1.0-py3-none-any.whl'), 'w') as z:\n"
        "    z.writestr('kyosei/_build_stamp.json', json.dumps(stamp))\n"
    )
    result = release(
        repo, "DRY_RUN=1", f"RELEASE_BUILD=python3 {fake}",
        env_extra={"VIRTUAL_ENV": "/some/controller/venv"},
    )
    assert result.returncode != 0
    assert f"not HEAD {_git(repo, 'rev-parse', 'HEAD')}" in result.stderr
    assert "deleted tag v0.1.0" in result.stderr
    assert tags(repo) == []
    # c2: the build runs with VIRTUAL_ENV unset, whatever the caller's environment says.
    assert (repo.parent / "venv-seen").read_text() == "unset"


def test_refuses_without_a_token_file_unless_dry_run(repo: Path):
    result = release(repo)
    assert result.returncode != 0
    assert "no TOKEN_FILE given" in result.stderr
    assert tags(repo) == []


# --- c4: the two version sources must agree ---------------------------------------------


def test_refuses_when_dunder_version_and_pyproject_differ(repo: Path):
    init = repo / "src" / "kyosei" / "__init__.py"
    init.write_text(init.read_text().replace('__version__ = "0.1.0"', '__version__ = "0.1.1"'))
    _git(repo, "commit", "-qam", "bump only __version__")
    result = release(repo, "DRY_RUN=1")
    assert result.returncode != 0
    assert "pyproject.toml's version is 0.1.0 but kyosei.__version__" in result.stderr
    assert "is 0.1.1" in result.stderr
    assert tags(repo) == []


def test_this_checkout_versions_agree():
    """The refusal above is only reachable by drift; this checkout must not be drifted."""
    pyproject = (ROOT / "pyproject.toml").read_text()
    import kyosei

    assert f'\nversion = "{kyosei.__version__}"\n' in pyproject


# --- c2: tag, wheel only, verify, publish; untag on a failed publish --------------------


def test_dry_run_tags_builds_the_wheel_verifies_and_untags(repo: Path):
    result = release(repo, "DRY_RUN=1", env_extra={"VIRTUAL_ENV": "/some/controller/venv"})
    assert result.returncode == 0, result.stderr
    head = _git(repo, "rev-parse", "HEAD")
    assert f"tagged v0.1.0 at {head}" in result.stdout
    assert "stamp verified" in result.stdout
    wheel = repo / "dist" / "kyosei-0.1.0-py3-none-any.whl"
    built = sorted(p.name for p in (repo / "dist").iterdir() if p.suffix in {".whl", ".gz"})
    assert built == [wheel.name]  # the wheel only: no sdist
    stamp = stamp_of(wheel)
    assert (stamp["commit"], stamp["dirty"], stamp["release"]) == (head, False, True)
    assert tags(repo) == []  # a dry run publishes nothing, so it keeps no tag


def test_a_failed_publish_deletes_the_tag_and_never_prints_the_token(repo, tmp_path):
    result = release(
        repo, f"TOKEN_FILE={token_file(tmp_path)}", "PUBLISH_URL=http://127.0.0.1:9/legacy/"
    )
    assert result.returncode != 0
    assert "uv publish failed; deleted tag v0.1.0" in result.stderr
    assert tags(repo) == []
    assert TOKEN not in result.stdout + result.stderr


class _Index(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):  # noqa: N802 -- http.server's name
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        _Index.seen.append(self.headers.get("Authorization", ""))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass


def test_publish_sends_the_token_read_from_the_file_and_keeps_the_tag(repo, tmp_path):
    _Index.seen = []
    server = HTTPServer(("127.0.0.1", 0), _Index)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/legacy/"
        result = release(repo, f"TOKEN_FILE={token_file(tmp_path)}", f"PUBLISH_URL={url}")
    finally:
        server.shutdown()
    assert result.returncode == 0, result.stderr
    assert "published dist/kyosei-0.1.0-py3-none-any.whl" in result.stdout
    assert tags(repo) == ["v0.1.0"]
    expected = "Basic " + base64.b64encode(f"__token__:{TOKEN}".encode()).decode()
    assert _Index.seen == [expected]
    assert TOKEN not in result.stdout + result.stderr


def test_the_recipe_reads_the_token_into_the_environment_not_argv():
    recipe = (ROOT / "Makefile").read_text().split("\nrelease:", 1)[1].split("\nclean:", 1)[0]
    assert 'UV_PUBLISH_TOKEN="$$(cat "$(TOKEN_FILE)")"' in recipe
    assert "--token" not in recipe
    assert "git push" not in recipe


# --- c3: the sdist path fails, saying to use make release -------------------------------


def test_uv_build_sdist_fails_naming_make_release(repo: Path):
    env = _env()
    env.pop("VIRTUAL_ENV", None)
    result = subprocess.run(
        ["uv", "build", "--sdist", "--out-dir", "dist"],
        cwd=repo, env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode != 0
    assert "does not build a source distribution" in result.stderr
    assert "make release" in result.stderr


def test_readme_states_the_sdist_choice():
    from tests.reference_pages import INSTALL

    # The README section, moved to its reference page (KYO-516).
    upgrading = INSTALL.read_text().split("\n## Upgrading\n", 1)[1]
    upgrading = upgrading.split("\n## ", 1)[0]
    assert "make release" in upgrading
    assert "no sdist" in upgrading
