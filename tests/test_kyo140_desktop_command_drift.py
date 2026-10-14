"""KYO-140.c4: the desktop app's hand-kept command lists cannot drift from the CLI's.

``apps/desktop/src/main/records.ts`` keeps two allowlists by hand —
``RECORDS_READ_COMMANDS`` (what records mode may run) and ``KYO_WRITING_COMMANDS``
(what its refusal names as a write). The first is load-bearing for behaviour. The
second is load-bearing for the *message*: a command absent from it is still refused,
but the refusal says "is not known to be read-only" instead of "writes to the store".

It drifted, and by more than the one command the record was filed about. ``retag``
(KYO-130), ``flag`` and ``docs`` were each added to :data:`kyosei.cli.MUTATING_COMMANDS`
without reaching the TypeScript list, because nothing compared them. This module is
that comparison.

**Why the check lives here and not in the desktop suite.** The authority is Python —
``MUTATING_COMMANDS`` and ``COMMAND_TABLE`` are imported directly, so only the
TypeScript side is parsed, and it is parsed as what it is: an array of string
literals. Putting it in ``apps/desktop/tests/`` would invert that (parsing Python from
TypeScript) and, more decisively, would park the check behind ``npm ci`` plus a
separately-fetched Electron binary. ``make test`` is what runs in this repo's ordinary
loop; a check that only runs after a desktop install is a check that does not run.

**Why not generate the TypeScript list from Python.** Codegen would need a generator
someone remembers to run, and then a test that the checked-in file matches what the
generator would emit — which is this comparison plus a build step. The comparison is
the part with the value.

**The invariant is a superset, not an equality**, and that distinction is the whole
design. ``KYO_WRITING_COMMANDS`` legitimately names commands that are not in
``MUTATING_COMMANDS``: ``init``, ``ideas``, ``rebuild``, ``export`` and the
two installers write files without writing an events row. They are writes to a human
and not mutations of the store's own definition, so an equality check could only be
satisfied by lying on one side. What is asserted is the direction that rots silently —
every command the CLI calls a mutation is named as a write — plus, for both lists, that
every name on them is a real subcommand.

The parser is the risk this module carries: a regex that matches nothing would pass
every comparison vacuously, which is the failure mode of a check that reads another
language. :func:`command_array` raises rather than returning empty, and
``test_extractor_*`` below prove it, so a rename or a refactor of the TypeScript fails
loudly instead of going quiet.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kyosei.cli import COMMAND_TABLE, MUTATING_COMMANDS

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDS_TS = REPO_ROOT / "apps" / "desktop" / "src" / "main" / "records.ts"


class ArrayNotFound(AssertionError):
    """The TypeScript no longer holds the array this check reads."""


def command_array(source: str, name: str) -> list[str]:
    """Return the string literals of ``export const <name>: ... = [...]``.

    Raises :class:`ArrayNotFound` rather than returning ``[]`` for either way this can
    stop describing the file — the declaration being gone, or being present and empty.
    A silent empty list would make every assertion in this module pass while comparing
    nothing at all.
    """
    match = re.search(
        rf"export const {re.escape(name)}\s*:[^=]*=\s*\[(?P<body>.*?)\]\s*;",
        source,
        re.DOTALL,
    )
    if match is None:
        raise ArrayNotFound(
            f"{RECORDS_TS.relative_to(REPO_ROOT)} no longer declares `export const {name}` "
            "as an array literal, so the cross-language check in "
            "tests/test_kyo140_desktop_command_drift.py is reading nothing. Restore the "
            f"declaration, or update `command_array`'s pattern to match how {name} is "
            "written now — do not delete this test to make it pass."
        )
    commands = re.findall(r'"([^"]+)"', match.group("body"))
    if not commands:
        raise ArrayNotFound(
            f"`{name}` in {RECORDS_TS.relative_to(REPO_ROOT)} parsed as an EMPTY list. "
            "Either it really is empty, or its members are no longer double-quoted "
            "string literals; both make this check vacuous."
        )
    return commands


def missing_writes(mutating: tuple[str, ...] | list[str], writing: list[str]) -> list[str]:
    """Commands the CLI calls mutations that the desktop app does not name as writes."""
    return [command for command in mutating if command not in writing]


@pytest.fixture(scope="module")
def source() -> str:
    return RECORDS_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def writing(source: str) -> list[str]:
    return command_array(source, "KYO_WRITING_COMMANDS")


@pytest.fixture(scope="module")
def reading(source: str) -> list[str]:
    return command_array(source, "RECORDS_READ_COMMANDS")


def test_every_mutating_command_is_named_a_write(writing: list[str]) -> None:
    """KYO-140.c4 — the check the record exists because nothing was doing.

    This is the assertion that would have caught ``retag`` the day KYO-130 landed.
    """
    missing = missing_writes(MUTATING_COMMANDS, writing)
    assert not missing, (
        "kyosei.cli.MUTATING_COMMANDS names commands that "
        f"{RECORDS_TS.relative_to(REPO_ROOT)}'s KYO_WRITING_COMMANDS does not: "
        f"{', '.join(missing)}. Records mode still refuses them — the allowlist is "
        "RECORDS_READ_COMMANDS and it is unaffected — but the refusal says each `is not "
        "known to be read-only` rather than that it writes to the store. Add "
        f"{', '.join(repr(name) for name in missing)} to KYO_WRITING_COMMANDS."
    )


def test_every_listed_command_is_a_real_subcommand(
    writing: list[str], reading: list[str]
) -> None:
    """Neither hand-kept list may name a command the CLI does not have.

    The other direction of drift: a command renamed or removed in Python leaves a
    dangling name in the TypeScript: in ``RECORDS_READ_COMMANDS`` an allowlist entry
    for nothing, and in ``KYO_WRITING_COMMANDS`` a claim about a command that does not
    exist. Derived from ``COMMAND_TABLE``, so it needs no second hand-kept list to
    check the first.
    """
    unknown = sorted((set(writing) | set(reading)) - set(COMMAND_TABLE))
    assert not unknown, (
        f"{RECORDS_TS.relative_to(REPO_ROOT)} names commands that are not in "
        f"kyosei.cli.COMMAND_TABLE: {', '.join(unknown)}. Either the command was renamed "
        "or removed in Python and the TypeScript still names it, or the entry is a typo — "
        "in which case a real command is silently unclassified as well."
    )


def test_read_allowlist_names_nothing_that_mutates(reading: list[str]) -> None:
    """Records mode's allowlist may not contain a command the CLI calls a mutation.

    Unlike the other two this is about behaviour, not the wording of a refusal: a
    mutating command reaching ``RECORDS_READ_COMMANDS`` would make records mode write
    to the store, against KYO-72's amendment that it is read-only to a human. Nothing
    checked it before, and it is free to check here.
    """
    mutating = sorted(set(reading) & set(MUTATING_COMMANDS))
    assert not mutating, (
        "RECORDS_READ_COMMANDS in "
        f"{RECORDS_TS.relative_to(REPO_ROOT)} allows commands that "
        f"kyosei.cli.MUTATING_COMMANDS says write to the store: {', '.join(mutating)}. "
        "Records mode is read-only to a human (KYO-72's amendment, KYO-112.c6)."
    )


def test_the_three_commands_this_record_was_filed_about_are_named(
    writing: list[str],
) -> None:
    """KYO-140.c3, and the two the record did not know about.

    The record named ``retag``. Comparing the lists for the first time found ``flag``
    and ``docs`` in the same state, which is the argument for the comparison rather
    than for the one-string fix.
    """
    assert {"retag", "flag", "docs"} <= set(writing)


# ----------------------------------------------------------- the parser's own guards
#
# CLAUDE.md: every mechanism ships with a test that fails if the mechanism is removed.
# For a check that reads another language, "removed" includes "still running, against
# nothing" — so these assert the extractor's failure, not the comparison's success.

RENAMED = 'export const SOMETHING_ELSE: readonly string[] = ["gate", "retag"];'
EMPTIED = "export const KYO_WRITING_COMMANDS: readonly string[] = [];"
UNQUOTED = "export const KYO_WRITING_COMMANDS: readonly string[] = [GATE, RETAG];"


@pytest.mark.parametrize("ts_source", [RENAMED, EMPTIED, UNQUOTED])
def test_extractor_refuses_a_source_it_cannot_read(ts_source: str) -> None:
    with pytest.raises(ArrayNotFound):
        command_array(ts_source, "KYO_WRITING_COMMANDS")


def test_extractor_message_names_the_file_and_forbids_deleting_the_test() -> None:
    """The remedy in the message is the remedy, not 'delete this'."""
    with pytest.raises(ArrayNotFound) as caught:
        command_array(RENAMED, "KYO_WRITING_COMMANDS")
    assert "records.ts" in str(caught.value)
    assert "do not delete this test" in str(caught.value)


def test_the_remedy_named_in_the_failure_clears_the_failure() -> None:
    """CLAUDE.md: an error message naming a remedy has a test proving the remedy works.

    Reproduces the exact pre-KYO-140 state — the real list with ``retag`` taken back
    out — proves it fails, then proves that adding the command the message names is
    what clears it.
    """
    real = command_array(RECORDS_TS.read_text(encoding="utf-8"), "KYO_WRITING_COMMANDS")
    drifted = [command for command in real if command != "retag"]
    assert missing_writes(MUTATING_COMMANDS, drifted) == ["retag"]
    assert missing_writes(MUTATING_COMMANDS, [*drifted, "retag"]) == []
