"""Law 16 — timestamps are stamped by the tool, never typed by a caller."""

from __future__ import annotations

import re
from datetime import datetime

from kyosei.cli import build_parser
from kyosei.clock import now

from .conftest import new_record

ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

#: Any flag whose name suggests it takes a time. There must be none.
TIME_SHAPED = re.compile(r"date|time|stamp|--at$|--when")

#: Flags the pattern catches by spelling that take a DURATION, not a moment. Law 16 is
#: about a caller typing *when* something happened; `kyo check-command --timeout-ms`
#: (KYO-312) restates how long a Bash call declared it may run, read from the hook
#: payload, and nothing stores it. Named one by one so a new time-shaped flag still fails.
DURATIONS_NOT_MOMENTS = {"--timeout-ms"}


def test_timestamps_are_utc_iso_8601_with_an_explicit_z() -> None:
    stamp = now()
    assert ISO_Z.match(stamp), stamp
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    assert parsed.utcoffset().total_seconds() == 0


def test_no_flag_anywhere_accepts_a_timestamp() -> None:
    """The mechanism behind "an agent never types a time"."""
    offenders: list[str] = []
    stack = [build_parser()]
    while stack:
        current = stack.pop()
        for action in current._actions:
            # Subparsers actions carry a dict of name -> sub-parser in `.choices`; a
            # plain enum-choice positional (e.g. `kyo ideas <review>`) carries a list
            # of strings instead, and must not be mistaken for a nested parser.
            if isinstance(action.choices, dict) and action.choices:
                stack.extend(action.choices.values())
                continue
            for option in action.option_strings:
                if TIME_SHAPED.search(option) and option not in DURATIONS_NOT_MOMENTS:
                    offenders.append(option)
    assert offenders == []


def test_stored_timestamps_are_tool_stamped(kyo) -> None:
    record = new_record(kyo)
    document = kyo("show", record, "--json").json
    assert ISO_Z.match(document["created_at"])
    assert ISO_Z.match(document["updated_at"])


def test_updated_at_moves_on_mutation_and_created_at_does_not(kyo) -> None:
    record = new_record(kyo)
    first = kyo("show", record, "--json").json
    assert kyo("edit", record, "--title", "renamed").code == 0
    second = kyo("show", record, "--json").json
    assert second["created_at"] == first["created_at"]
    assert second["updated_at"] >= first["updated_at"]
