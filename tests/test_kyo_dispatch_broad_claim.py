"""KYO-539.c1: `kyo dispatch` warns, never refuses, when a declared directory touch
contains a touch of another open record."""

from __future__ import annotations

from pathlib import Path

from .conftest import new_record


def _touch(kyo, rec: str, line: str) -> None:
    assert kyo("edit", rec, "--touches-file", "-", stdin=line + "\n").code == 0


def test_a_directory_touch_over_another_open_records_file_warns(kyo, git_project: Path) -> None:
    (git_project / "tests").mkdir()
    (git_project / "tests" / "a.py").write_text("x = 1\n", encoding="utf-8")
    broad = new_record(kyo, criteria=["c"])
    other = new_record(kyo, criteria=["c"])
    _touch(kyo, broad, "tests/")
    _touch(kyo, other, "tests/a.py")
    result = kyo("dispatch", broad, "--json")
    assert result.code == 0, result
    warns = result.json["broad_claim_warnings"]
    assert warns == [{"directory": "tests/", "records": {other: ["tests/a.py"]}}]
    text = kyo("dispatch", broad, "--force")
    assert text.code == 0, text
    assert "claims the directory tests/" in text.out
    assert "+tests/<file>" in text.out


def test_a_disjoint_directory_does_not_warn(kyo, git_project: Path) -> None:
    (git_project / "tests").mkdir()
    (git_project / "docs").mkdir()
    (git_project / "docs" / "d.md").write_text("d\n", encoding="utf-8")
    broad = new_record(kyo, criteria=["c"])
    other = new_record(kyo, criteria=["c"])
    _touch(kyo, broad, "tests/")
    _touch(kyo, other, "docs/d.md")
    result = kyo("dispatch", broad, "--json")
    assert result.code == 0, result
    assert result.json["broad_claim_warnings"] == []
    assert "claims the directory" not in kyo("dispatch", broad, "--force").out


def test_two_broad_directories_print_one_line_each_and_the_advice_once(
    kyo, git_project: Path
) -> None:
    """KYO-547.c1/c2: one line per claimed directory naming each record and its file
    count, the advice once; the per-file listing stays in --json only."""
    files = {
        "src/a.py", "src/b.py", "src/c.py", "tests/t1.py", "tests/t2.py",
    }
    for f in files:
        (git_project / f).parent.mkdir(exist_ok=True)
        (git_project / f).write_text("x = 1\n", encoding="utf-8")
    broad = new_record(kyo, criteria=["c"])
    one = new_record(kyo, criteria=["c"])
    two = new_record(kyo, criteria=["c"])
    three = new_record(kyo, criteria=["c"])
    _touch(kyo, broad, "src/\ntests/")
    _touch(kyo, one, "src/a.py\nsrc/b.py\ntests/t1.py")
    _touch(kyo, two, "src/c.py")
    _touch(kyo, three, "tests/t1.py\ntests/t2.py")

    result = kyo("dispatch", broad, "--json")
    assert result.code == 0, result
    assert result.json["broad_claim_warnings"] == [
        {"directory": "src/", "records": {one: ["src/a.py", "src/b.py"], two: ["src/c.py"]}},
        {"directory": "tests/", "records": {one: ["tests/t1.py"],
                                            three: ["tests/t1.py", "tests/t2.py"]}},
    ]

    text = kyo("dispatch", broad, "--force")
    assert text.code == 0, text
    warning_lines = [ln for ln in text.out.splitlines() if "claims the directory" in ln]
    assert len(warning_lines) == 2, text.out
    src_line, tests_line = warning_lines
    assert "src/" in src_line and "2 other open records" in src_line
    assert f"{one} (2 files)" in src_line and f"{two} (1 file)" in src_line
    assert "tests/" in tests_line
    assert f"{one} (1 file)" in tests_line and f"{three} (2 files)" in tests_line
    assert text.out.count("A directory claim serialises") == 1, text.out
    # the per-file listing must not come back in the human output
    for f in files:
        assert f not in text.out, (f, text.out)
