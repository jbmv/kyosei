"""KYO-541: the BUILD CHANGED notice names a few records, then counts the rest and gives
the exact `kyo changelog --since` command, so the session-start brief stays small."""

from __future__ import annotations

from kyosei import render

SINCE = "kyo changelog --since 2026-09-20T10:00:00.000Z"


def _change(n: int) -> dict:
    return {
        "state": "changed",
        "previous": {"commit_short": "aaaaaaaa", "commit_time": "2026-09-20T10:00:00.000Z"},
        "current": {"commit_short": "bbbbbbbb", "commit_time": "2026-09-25T02:34:00.000Z"},
        "previous_brief_id": "B-1",
        "previous_brief_at": "2026-09-20",
        "records_total": n,
        "records_exact": True,
        "since_command": SINCE,
        "records": [
            {"completed_on": "2026-09-2", "id": f"KYO-{i}", "kind": "task", "title": f"t{i}"}
            for i in range(1, n + 1)
        ],
    }


def test_a_short_change_set_lists_every_record_and_no_remainder() -> None:
    text = "\n".join(render.build_change_lines(_change(3)))
    assert all(f"KYO-{i} " in text for i in (1, 2, 3))
    assert "more" not in text


def test_a_long_change_set_lists_a_few_then_counts_the_rest_with_the_command() -> None:
    limit = render.BUILD_NOTICE_RECORD_LIMIT
    text = "\n".join(render.build_change_lines(_change(29)))
    assert text.count("\n    2026") == limit
    assert f"KYO-{limit} " in text and f"KYO-{limit + 1} " not in text
    assert f"… and {29 - limit} more: `{SINCE}`" in text
    assert len(text.encode()) < 1500
