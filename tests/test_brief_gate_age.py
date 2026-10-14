"""KYO-496: `kyo brief` prints how long each open gate has been waiting.

`gates.created_at` was stored and never printed, so a two-week wait in a consumer
project read exactly like one opened that morning. The age is
derived at read time from the gate's own stamp against the brief's stamp, by the one
helper in `clock.py`, and is never stored.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from kyosei import cmd_brief
from kyosei.clock import elapsed_days

from .conftest import new_record


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _gate_backdated(kyo, db, *, ago: timedelta, title: str) -> tuple[str, str]:
    record = new_record(kyo, title=title)
    gate_id = kyo("gate", record, "awaiting:human-review", "--json").json["gate_id"]
    opened = _stamp(datetime.now(UTC) - ago)
    conn = db()
    conn.execute("UPDATE gates SET created_at = ? WHERE id = ?", (opened, gate_id))
    conn.close()
    return record, opened


def _gated_lines(text: str) -> list[str]:
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("gated work ("))
    out = []
    for line in lines[start + 1 :]:
        if not line.startswith("  "):
            break
        out.append(line)
    return out


@pytest.mark.parametrize("compact", [False, True])
def test_every_gated_line_ends_its_gate_part_with_its_age(kyo, db, compact) -> None:
    """c1: 13 days and 23 hours is 13 whole days (rounded down, not to nearest); a gate
    opened a moment ago is 0d."""
    old, _ = _gate_backdated(kyo, db, ago=timedelta(days=13, hours=23), title="Old wait")
    new, _ = _gate_backdated(kyo, db, ago=timedelta(minutes=5), title="New wait")
    args = ["brief", "--compact"] if compact else ["brief"]
    result = kyo(*args)
    assert result.code == 0, result
    lines = _gated_lines(result.out)
    assert len(lines) == 2, lines
    by_record = {re.match(r"\s+(\S+)", line).group(1): line for line in lines}
    # The age is the last thing in the gate part: nothing but the `do:` text may follow.
    assert re.search(r"awaiting:human-review  open 13d$", by_record[old]), by_record[old]
    assert re.search(r"awaiting:human-review  open 0d$", by_record[new]), by_record[new]


def test_the_age_sits_before_the_do_text(kyo, db) -> None:
    record = new_record(kyo, title="With a do")
    gate_id = kyo("gate", record, "awaiting:event", "--do", "CI goes green", "--json").json[
        "gate_id"
    ]
    conn = db()
    conn.execute(
        "UPDATE gates SET created_at = ? WHERE id = ?",
        (_stamp(datetime.now(UTC) - timedelta(days=2, hours=1)), gate_id),
    )
    conn.close()
    for args in (["brief"], ["brief", "--compact"]):
        (line,) = _gated_lines(kyo(*args).out)
        assert line.endswith("awaiting:event  open 2d  do: CI goes green"), line


def test_brief_json_carries_opened_at_and_age_days(kyo, db) -> None:
    """c2."""
    record, opened = _gate_backdated(kyo, db, ago=timedelta(days=7, hours=2), title="Json")
    payload = kyo("brief", "--json", "--full").json
    (item,) = payload["gated"]
    assert item["record_id"] == record
    assert item["opened_at"] == opened
    assert item["age_days"] == 7
    # Aged against the brief's own stamp, the clock the document already reports.
    assert item["age_days"] == elapsed_days(opened, payload["generated_at"])


def test_the_brief_ages_gates_only_through_the_clock_helper(kyo, db, monkeypatch) -> None:
    """c3: replace the helper and the printed age follows it -- so neither cmd_brief
    nor the renderer computes an age of its own."""
    _gate_backdated(kyo, db, ago=timedelta(days=3), title="Patched")
    calls = []

    def fake(since: str, until: str) -> int:
        calls.append((since, until))
        return 4242

    monkeypatch.setattr(cmd_brief, "elapsed_days", fake)
    result = kyo("brief", "--compact")
    (line,) = _gated_lines(result.out)
    assert line.endswith("open 4242d"), line
    assert len(calls) == 1


def test_elapsed_days_counts_whole_24_hour_periods() -> None:
    base = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)
    at = _stamp(base)
    assert at == "2026-09-19T12:00:00.000Z"
    assert elapsed_days(at, _stamp(base + timedelta(hours=23, minutes=59))) == 0
    assert elapsed_days(at, _stamp(base + timedelta(days=1))) == 1
    # Across a calendar boundary but under 24 hours is still 0: periods, not dates.
    assert elapsed_days(at, _stamp(base + timedelta(hours=13))) == 0
    assert elapsed_days(at, _stamp(base + timedelta(days=14, hours=23))) == 14
    # A clock that moved backwards reads as no wait, never as a negative one.
    assert elapsed_days(at, _stamp(base - timedelta(hours=3))) == 0
