"""A store is not one place, and the list of places must not be able to go quiet.

The ``REDACTABLE`` table has the property that a missing surface shows up as a kind
with no entry rather than as silence. The first fix for frozen briefs did not, because
its surface list was a bare tuple inside a function body where nothing could see it.
This module is the missing half: every column on a prose-embedding table must be
either scrubbed or explicitly exempted, so the *next* surface is a failing test
rather than a value quietly left in git.

The surfaces found so far are evidence artifacts, the event ledger, claims and shift
notes, source-code comments and frozen dispatch briefs. Every surface holding
controller-written prose is a new place an identifier can land, and the list grows as
the tool grows.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from kyosei.contracts import EMBEDDED_PROSE, EMBEDDED_PROSE_EXEMPT, REDACTABLE


def _columns(project: Path, table: str) -> list[str]:
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def test_every_column_on_a_prose_embedding_table_is_classified(kyo, project: Path) -> None:
    """The mechanism. Add a column to `dispatches` or `reports` and this fails until
    you say whether a redaction has to reach it — which is the decision that got
    skipped when `dispatches.brief` was added."""
    scrubbed = {(table, column) for table, column in EMBEDDED_PROSE}
    for table in sorted({t for t, _ in EMBEDDED_PROSE}):
        for column in _columns(project, table):
            key = (table, column)
            assert key in scrubbed or key in EMBEDDED_PROSE_EXEMPT, (
                f"{table}.{column} is neither scrubbed by `kyo redact` nor listed in "
                "EMBEDDED_PROSE_EXEMPT — decide which, and say why in the exemption"
            )


def test_no_exemption_names_a_column_that_does_not_exist(kyo, project: Path) -> None:
    """An exemption for a renamed column is a lie that reads as diligence."""
    for table in sorted({t for t, _ in EMBEDDED_PROSE}):
        live = set(_columns(project, table))
        for exempt_table, column in EMBEDDED_PROSE_EXEMPT:
            if exempt_table == table:
                assert column in live, f"{table}.{column} is exempted but no longer exists"


def test_every_embedded_surface_exists_in_the_live_schema(kyo, project: Path) -> None:
    """A scrub target that no longer exists would fail loudly at redaction time,
    which is the worst moment to find out."""
    for table, column in EMBEDDED_PROSE:
        assert column in _columns(project, table), f"{table}.{column} is gone"


def test_no_column_is_both_scrubbed_and_exempt() -> None:
    overlap = {(t, c) for t, c in EMBEDDED_PROSE} & set(EMBEDDED_PROSE_EXEMPT)
    assert not overlap, f"contradictory classification: {overlap}"


def test_every_redactable_store_names_a_real_table_and_column(kyo, project: Path) -> None:
    """The same guarantee for the other table — REDACTABLE has had it by convention
    and by its CLI-reachability test; this pins the schema half."""
    for kind, (table, text_column, record_column) in REDACTABLE.items():
        live = _columns(project, table)
        assert text_column in live, f"{kind}: {table}.{text_column} is gone"
        assert "redacted_at" in live and "redacted_why" in live, f"{kind}: no redaction columns"
        if record_column:
            assert record_column in live, f"{kind}: {table}.{record_column} is gone"


def test_the_two_tables_do_not_overlap() -> None:
    """A store is either addressable by id (REDACTABLE) or holds a copy of prose
    from elsewhere (EMBEDDED_PROSE). Listing one in both would mean redacting it
    twice by different rules."""
    redactable = {(table, column) for table, column, _ in REDACTABLE.values()}
    assert not redactable & set(EMBEDDED_PROSE)
