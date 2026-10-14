"""tools/pure_move.py: the check every module split's move commit must pass."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "pure_move.py"

ORIGINAL = '''"""A module about to be split."""

import os

LIMIT = 3


def alpha(x):
    """Alpha's rule."""
    return x + LIMIT


class Beta:
    def run(self):
        return os.sep
'''


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    pkg = tmp_path / "src" / "kyosei"
    pkg.mkdir(parents=True)
    (pkg / "big.py").write_text(ORIGINAL)
    (pkg / "other.py").write_text("def untouched():\n    return 1\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    return pkg


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), "--root", str(root), "--base", "HEAD",
         "--old", "src/kyosei/big.py"],
        capture_output=True, text=True,
    )


def _split(pkg: Path, alpha_body: str = "return x + LIMIT") -> None:
    (pkg / "big.py").unlink()
    (pkg / "limits.py").write_text("LIMIT = 3\n")
    (pkg / "alpha_mod.py").write_text(
        "from .limits import LIMIT\n\n\n"
        f'def alpha(x):\n    """Alpha\'s rule."""\n    {alpha_body}\n'
    )
    (pkg / "beta_mod.py").write_text(
        "import os\n\n\nclass Beta:\n    def run(self):\n        return os.sep\n"
    )


def test_a_pure_split_passes(tmp_path: Path) -> None:
    pkg = _repo(tmp_path)
    _split(pkg)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "0 finding(s)" in result.stdout


def test_a_changed_body_is_named(tmp_path: Path) -> None:
    pkg = _repo(tmp_path)
    _split(pkg, alpha_body="return x + LIMIT + 1")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "DIFFERS  alpha" in result.stdout


def test_a_dropped_definition_is_named(tmp_path: Path) -> None:
    pkg = _repo(tmp_path)
    _split(pkg)
    (pkg / "beta_mod.py").write_text("import os\n")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "MISSING  Beta" in result.stdout


def test_a_new_helper_in_a_new_module_is_named(tmp_path: Path) -> None:
    pkg = _repo(tmp_path)
    _split(pkg)
    (pkg / "limits.py").write_text("LIMIT = 3\n\n\ndef helper():\n    return 0\n")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "ADDED    helper" in result.stdout


def test_a_reexport_shim_in_the_old_module_is_named(tmp_path: Path) -> None:
    pkg = _repo(tmp_path)
    _split(pkg)
    (pkg / "big.py").write_text("from .alpha_mod import alpha  # noqa: F401\n")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "SHIM     alpha" in result.stdout


def test_a_moved_docstring_edit_is_a_difference(tmp_path: Path) -> None:
    """The docstring pass is a separate commit; the move commit may not start it."""
    pkg = _repo(tmp_path)
    _split(pkg)
    source = (pkg / "alpha_mod.py").read_text().replace("Alpha's rule.", "Alpha.")
    (pkg / "alpha_mod.py").write_text(source)
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "DIFFERS  alpha" in result.stdout


def test_an_import_the_old_module_uses_is_not_a_shim(tmp_path: Path) -> None:
    """A registry left in the old module calls what moved out; that import is not a re-export."""
    pkg = _repo(tmp_path)
    _split(pkg)
    (pkg / "big.py").write_text("from .alpha_mod import alpha\n\nREGISTRY = {'alpha': alpha}\n")
    result = _run(tmp_path)
    assert "SHIM" not in result.stdout
    assert "ADDED" not in result.stdout  # big.py existed at base, so REGISTRY is not "new"


def _with_all(pkg: Path, names: list[str]) -> None:
    header = f"import os\n\n__all__ = {names!r}\n"
    (pkg / "big.py").write_text(ORIGINAL.replace("import os\n", header))


def test_an_all_list_that_only_shrank_passes(tmp_path: Path) -> None:
    pkg = _repo(tmp_path)
    _with_all(pkg, ["alpha", "Beta"])
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "all")
    _split(pkg)
    remaining = "__all__ = ['alpha']\n\nfrom .alpha_mod import alpha\n\nX = alpha\n"
    (pkg / "big.py").write_text(remaining)
    result = _run(tmp_path)
    assert "__all__" not in result.stdout


def test_an_all_list_that_gained_a_name_is_a_difference(tmp_path: Path) -> None:
    pkg = _repo(tmp_path)
    _with_all(pkg, ["alpha"])
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "all")
    _split(pkg)
    (pkg / "big.py").write_text("__all__ = ['alpha', 'gamma']\n")
    result = _run(tmp_path)
    assert "DIFFERS  __all__" in result.stdout
