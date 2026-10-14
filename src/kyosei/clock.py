"""The only source of time in Kyosei.

Law 16: *all timestamps are stamped by the tool itself* — an agent never types a time.
No CLI flag anywhere accepts a timestamp; ``tests/test_law16_time.py`` asserts that
by walking the argument parser.
"""

from __future__ import annotations

from datetime import UTC, datetime

#: ISO-8601, UTC, milliseconds, explicit ``Z``. Stable width keeps exports diffable.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def now() -> str:
    """Return the current instant as an ISO-8601 UTC string ending in ``Z``."""
    stamp = datetime.now(UTC)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}Z"


def elapsed_days(since: str, until: str) -> int:
    """Whole 24-hour periods from ``since`` to ``until``, rounded down (KYO-496).

    Both are timestamps this tool stamped (:func:`now`), so ``until`` is a reader's own
    clock — the brief passes the stamp it records itself under — and an age is always
    derived at read time from two stored instants, never stored. An age that would be
    negative (``since`` after ``until``: a clock that moved backwards between two
    machines) reads as 0 rather than as a negative wait.
    """
    start = datetime.fromisoformat(since.replace("Z", "+00:00"))
    end = datetime.fromisoformat(until.replace("Z", "+00:00"))
    return max(int((end - start).total_seconds() // 86400), 0)
