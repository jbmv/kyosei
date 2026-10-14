"""KYO-413.c6: a registry or ``config.yml`` that repeats a mapping key is refused.

``yaml.safe_load`` keeps the LAST of two equal keys, so a second ``bug:`` in
``kinds.yml`` or a second ``done:`` in ``statuses.yml`` replaced the first with exit 0
-- against the registry's collect-every-problem refusal style. Each test below writes
the duplicate, asserts the refusal names the file, the key and the remedy, then deletes
the duplicate and asserts the same command runs clean.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kyosei.errors import EXIT_ERROR
from kyosei.registry import RegistryError, load_yaml_refusing_duplicates

DEFAULT_STATUSES = (
    Path(__file__).resolve().parent.parent / "src" / "kyosei" / "defaults" / "statuses.yml"
)


def _duplicate_block(text: str, header: str) -> str:
    """``text`` with the two-space-indented block starting at ``header`` repeated
    directly after itself -- the copy-paste a person makes when adding an entry."""
    lines = text.splitlines(keepends=True)
    start = lines.index(header)
    end = start + 1
    while end < len(lines) and (lines[end].startswith("    ") or not lines[end].strip()):
        end += 1
    return "".join(lines[:end] + lines[start:end] + lines[end:])


def _assert_refused(result, path: Path, key: str) -> None:
    assert result.code == EXIT_ERROR, result
    assert str(path) in result.err
    assert f"repeats the key {key!r}" in result.err
    assert "delete one of them" in result.err


def test_the_loader_refuses_a_repeated_key_and_names_both_lines() -> None:
    with pytest.raises(RegistryError) as exc:
        load_yaml_refusing_duplicates("a: 1\nb: 2\na: 3\n", Path("x.yml"))
    assert "x.yml line 3 repeats the key 'a' (first at line 1)" in str(exc.value)


def test_the_loader_leaves_distinct_keys_and_merge_overrides_alone() -> None:
    text = "base: &b {x: 1, y: 2}\nderived:\n  <<: *b\n  x: 3\n"
    assert load_yaml_refusing_duplicates(text, Path("x.yml")) == {
        "base": {"x": 1, "y": 2},
        "derived": {"x": 3, "y": 2},
    }


def test_a_repeated_kind_in_kinds_yml_is_refused_until_deleted(kyo, project: Path) -> None:
    kinds = project / "kyosei" / "kinds.yml"
    original = kinds.read_text(encoding="utf-8")
    kinds.write_text(_duplicate_block(original, "  bug:\n"), encoding="utf-8")
    _assert_refused(kyo("list"), kinds, "bug")

    kinds.write_text(original, encoding="utf-8")
    assert kyo("list").code == 0


def test_a_repeated_status_in_statuses_yml_is_refused_until_deleted(
    kyo, project: Path
) -> None:
    statuses = project / "kyosei" / "statuses.yml"
    original = DEFAULT_STATUSES.read_text(encoding="utf-8")
    statuses.write_text(_duplicate_block(original, "  done:\n"), encoding="utf-8")
    _assert_refused(kyo("list"), statuses, "done")

    statuses.write_text(original, encoding="utf-8")
    assert kyo("list").code == 0


def test_a_repeated_key_in_config_yml_is_refused_until_deleted(kyo, project: Path) -> None:
    config = project / "kyosei" / "config.yml"
    original = config.read_text(encoding="utf-8")
    config.write_text(original + "project_prefix: OTHER\n", encoding="utf-8")
    _assert_refused(kyo("list"), config, "project_prefix")

    config.write_text(original, encoding="utf-8")
    assert kyo("list").code == 0
