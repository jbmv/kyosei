"""KYO-828: a governs target can name a file whose name has no suffix.

``Makefile`` and ``LICENSE`` carry no slash and no suffix, which is the spelling of a bare
symbol, and read as one they governed every code file containing the word. A base holding
a file under exactly that name now makes the target that file, and ``./Makefile`` names
the file by spelling alone.
"""

from __future__ import annotations

from pathlib import Path

from kyosei import docs_parse, docs_targets
from kyosei.docs_targets import SHAPE_PATH, SHAPE_SYMBOL, resolve_target


def _tree(root: Path) -> Path:
    """A Makefile, a LICENSE, and code files that mention both words."""
    (root / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
    (root / "LICENSE").write_text("Apache License\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "build.py").write_text(
        "# reads the Makefile and the LICENSE\nMakefile = 'Makefile'\n", encoding="utf-8"
    )
    (root / "src" / "other.py").write_text("# Makefile targets\n", encoding="utf-8")
    return root


def test_makefile_governs_only_the_makefile(tmp_path: Path) -> None:
    target = resolve_target(_tree(tmp_path), "Makefile")
    assert target.reason is None
    assert target.shape == SHAPE_PATH
    assert target.paths == ("Makefile",)
    assert target.symbol is None and target.defined_in == ()


def test_license_governs_only_the_license(tmp_path: Path) -> None:
    assert resolve_target(_tree(tmp_path), "LICENSE").paths == ("LICENSE",)


def test_word_with_no_file_of_that_name_is_still_a_symbol(tmp_path: Path) -> None:
    """The rule is the file's presence: without it the same word is a symbol search."""
    root = _tree(tmp_path)
    (root / "Makefile").unlink()
    target = resolve_target(root, "Makefile")
    assert target.shape == SHAPE_SYMBOL
    assert target.paths == ("src/build.py", "src/other.py")


def test_name_differing_in_case_is_not_the_file(tmp_path: Path) -> None:
    """Exact name, on every filesystem: ``makefile`` is not ``Makefile``."""
    root = _tree(tmp_path)
    (root / "src" / "lower.py").write_text("makefile = 1\n", encoding="utf-8")
    target = resolve_target(root, "makefile")
    assert target.shape == SHAPE_SYMBOL
    assert target.paths == ("src/lower.py",)


def test_directory_of_that_name_is_not_a_file(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "ImportRun").mkdir()
    (root / "src" / "runs.py").write_text("class ImportRun: ...\n", encoding="utf-8")
    target = resolve_target(root, "ImportRun")
    assert target.shape == SHAPE_SYMBOL
    assert target.paths == ("src/runs.py",)


def test_file_is_found_under_a_declared_base(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "deploy").mkdir()
    (root / "deploy" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (root / "src" / "image.py").write_text("# builds the Dockerfile\n", encoding="utf-8")
    target = resolve_target(root, "Dockerfile", ("src", "deploy"))
    assert target.shape == SHAPE_PATH
    assert target.paths == ("deploy/Dockerfile",)


def test_dot_slash_names_the_file_by_spelling(tmp_path: Path) -> None:
    """``./Makefile`` is a path with no file consulted, so a missing file is reported."""
    root = _tree(tmp_path)
    present = resolve_target(root, "./Makefile")
    assert present.shape == SHAPE_PATH and present.paths == ("Makefile",)
    (root / "Makefile").unlink()
    missing = resolve_target(root, "./Makefile")
    assert missing.shape == SHAPE_PATH and missing.paths == ()
    assert missing.reason is not None and "no such file" in missing.reason


def test_suffixless_file_takes_a_symbol_half(tmp_path: Path) -> None:
    target = resolve_target(_tree(tmp_path), "Makefile#test")
    assert target.reason is None and target.paths == ("Makefile",)


def test_a_section_governing_makefile_resolves_to_it_alone(tmp_path: Path) -> None:
    """Through the document reader, which is what the check and the join both use."""
    root = _tree(tmp_path)
    (root / "docs").mkdir()
    (root / "docs" / "build.md").write_text(
        "# Build\n\n## Targets\n\ngoverns: Makefile\n\nThe targets.\n", encoding="utf-8"
    )
    [document] = docs_targets.documents(root, documents_in=docs_parse.DEFAULT)
    governed = [path for section in document.sections for path in section.governed_paths]
    assert governed == ["Makefile"]
