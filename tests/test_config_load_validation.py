"""KYO-413.c7: ``load_config`` refuses a non-integer ``schema_version`` and a
null/non-string/empty ``project_prefix`` with a named remedy.

Before: ``int(raw["schema_version"])`` on ``abc`` was a raw ValueError traceback, and
``str(raw["project_prefix"])`` turned ``project_prefix: null`` into the prefix
``"None"``. Each test writes the bad value, asserts the refusal, restores the value the
refusal asks for, and asserts the same command runs clean.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kyosei.config import load_config
from kyosei.errors import EXIT_ERROR, KyoseiError


def _set(config: Path, key: str, value: str) -> str:
    original = config.read_text(encoding="utf-8")
    patched, n = re.subn(rf"(?m)^{key}:.*$", f"{key}: {value}", original)
    assert n == 1
    config.write_text(patched, encoding="utf-8")
    return original


@pytest.mark.parametrize("value", ["abc", "1.5", "true", "null"])
def test_a_non_integer_schema_version_is_refused_by_name(kyo, project: Path, value) -> None:
    config = project / "kyosei" / "config.yml"
    original = _set(config, "schema_version", value)
    result = kyo("list")
    assert result.code == EXIT_ERROR, result
    assert "key 'schema_version' must be a whole number" in result.err
    assert "Traceback" not in result.err
    assert "write back the number this store was built at" in result.err

    config.write_text(original, encoding="utf-8")
    assert kyo("list").code == 0


def test_a_quoted_integer_schema_version_still_loads(project: Path) -> None:
    """``int("24")`` always worked; the check must not take that away."""
    config = project / "kyosei" / "config.yml"
    current = load_config(project).schema_version
    _set(config, "schema_version", f'"{current}"')
    assert load_config(project).schema_version == current


@pytest.mark.parametrize("value", ["null", "''", "'   '", "[TST]", "42"])
def test_a_null_empty_or_non_string_prefix_is_refused_by_name(
    kyo, project: Path, value
) -> None:
    config = project / "kyosei" / "config.yml"
    original = _set(config, "project_prefix", value)
    with pytest.raises(KyoseiError) as exc:
        load_config(project)
    assert "key 'project_prefix' must be the non-empty id prefix" in str(exc.value)
    assert "write it back as it was" in str(exc.value)
    assert "'None'" not in str(exc.value)

    config.write_text(original, encoding="utf-8")
    assert load_config(project).project_prefix == "TST"
    assert kyo("list").code == 0
