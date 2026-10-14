"""``kyo doctor`` — one test per check, each breaking the invariant it guards.

Law 14: the tool's own invariant checks are what stop bespoke tooling from failing
silently in the direction of looking fine. A check nobody proves can fail is not a
check.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

from kyosei import (
    buildinfo,
    doctor_consumers,
    doctor_exports,
    doctor_registry,
    install_gitignore,
    install_procedures,
    install_skill,
)
from kyosei import cmd_store as cmd_store_module
from kyosei import config as config_module
from kyosei import doctor as doctor_module
from kyosei import procedures as procedures_module
from kyosei import scaffold as scaffold_module
from kyosei.doctor import (
    CHECK_ORDER,
    CHECKS,
    NOTICE_CHECKS,
    TIER_NOTICE,
    TIER_PROBLEM,
    tier_of,
)
from kyosei.doctor_consumers import (
    PACKAGE_SOURCE_DIR,
    module_index,
    unresolvable_consumer,
    unresolvable_consumer_failures,
)
from kyosei.errors import EXIT_DOCTOR
from kyosei.schema import COLUMN_CONSUMERS, CONFIG_CONSUMERS, SCHEMA_VERSION

from .conftest import export_of, new_record


def _failing_checks(kyo) -> dict:
    result = kyo("doctor", "--json")
    assert result.code == EXIT_DOCTOR, result
    return {
        name: detail
        for name, detail in result.json["checks"].items()
        if not detail["ok"]
    }


def test_every_registered_check_runs(kyo) -> None:
    assert set(CHECK_ORDER) == set(CHECKS)
    result = kyo("doctor", "--json")
    assert result.code == 0, result
    assert set(result.json["checks"]) == set(CHECK_ORDER)


# ------------------------------------------------------------------------ KYO-214
#
# Two tiers. A check declares its own by membership in `NOTICE_CHECKS`, and the exit
# code answers to the problem tier alone. The behaviour under a live notice is proved
# where the tick-shaped population already lives (tests/test_kyo163_phantom_stops.py);
# what is proved here is the registry itself — that the declaration cannot name a check
# that does not exist, and that every check has exactly one tier.


def test_every_notice_check_is_a_check_and_every_check_has_one_tier(kyo) -> None:
    """KYO-214.c1. A tier declaration that named nothing would be decoration (law 2).

    The failure this forecloses is a rename: `unresolved_stops` renamed in `CHECKS`
    and not in `NOTICE_CHECKS` would silently put the finding back in the problem
    tier — which is the whole bug, arriving by a route no one is watching.
    """
    assert set(CHECKS) >= NOTICE_CHECKS, sorted(NOTICE_CHECKS - set(CHECKS))
    assert "unresolved_stops" in NOTICE_CHECKS
    assert {tier_of(name) for name in CHECKS} <= {TIER_PROBLEM, TIER_NOTICE}
    for name in CHECKS:
        expected = TIER_NOTICE if name in NOTICE_CHECKS else TIER_PROBLEM
        assert tier_of(name) == expected, name

    # ...and the tier travels with every check in `--json`, not only the ones with
    # findings, so a consumer can read the classification off a clean run.
    checks = kyo("doctor", "--json").json["checks"]
    assert {name: detail["tier"] for name, detail in checks.items()} == {
        name: tier_of(name) for name in CHECKS
    }


def test_the_three_remedy_naming_checks_are_deliberately_still_problems() -> None:
    """KYO-214.c3, pinned rather than left to prose.

    `skill`, `hook_config` and `html_freshness` were the candidates considered and
    deliberately not moved: each names a remedy command, so its reader can act now,
    and `skill`/`hook_config` are about to gain a session-start surface (KYO-213) that
    the controller wants in hand before deciding. A later dispatch that moves one
    should change this line and say why — which is the point of it being a line.

    KYO-215 is that later dispatch for the third name, and the change is not a
    reclassification: `html_freshness` was DELETED with `kyo html`. Its successor
    `retired_html_directory` is a notice because it names no remedy command at all —
    deleting `kyosei/html/` is the reader's decision about their own files — so it
    fails this test's premise rather than its assertion, and is asserted below instead.
    """
    for name in ("skill", "hook_config"):
        assert tier_of(name) == TIER_PROBLEM, name
    assert "html_freshness" not in CHECKS, (
        "`kyo html` was retired in KYO-215 and its freshness check went with it; if a "
        "check by this name is back, decide its tier deliberately rather than by "
        "inheriting this line."
    )
    assert tier_of("retired_html_directory") == TIER_NOTICE


# ------------------------------------------------------------------------ KYO-215
#
# `kyo html` is retired, and `html_freshness` -- "is the generated site behind the
# store" -- went with it. What replaces it asks the only question left: is a site
# still sitting there, frozen, being read as if something maintained it.


def test_a_leftover_html_directory_is_reported_as_a_notice(kyo, project: Path) -> None:
    """KYO-215.c3. Told once, as information, with no command claimed to clear it.

    The old pages are indistinguishable from live ones to anyone who opens them, and
    they stopped updating on a date nothing records -- the failure of a viewer with no
    notion of being behind, which reads as authoritative, in its permanent form. Saying
    so is the whole remedy: nothing here deletes the human's
    own directory, on `check_hook_config`'s posture.
    """
    (project / "kyosei" / "html").mkdir()
    (project / "kyosei" / "html" / "index.html").write_text("old board\n", encoding="utf-8")

    result = kyo("doctor", "--json")
    assert result.code == 0, "a notice never fails the hook it is printed from"
    assert result.json["problems"] == []
    (notice,) = result.json["notices"]
    assert notice.startswith("[retired_html_directory] ")
    assert "kyo html" in notice, "the reader is told what used to write it"
    assert "kyo show" in notice and "kyo brief" in notice, (
        "a project without the desktop app is told what reads the store instead"
    )

    human = kyo("doctor")
    assert human.code == 0, human
    assert "[retired_html_directory]" in human.out + human.err, (
        "the notice must reach a human reading the hook's output, not only --json"
    )


def test_the_leftover_notice_is_silent_when_the_directory_is_absent(kyo, project: Path) -> None:
    """Every project that never generated a site, and every one that has since
    deleted theirs. A notice that fires at everyone is one nobody reads."""
    assert not (project / "kyosei" / "html").exists()
    result = kyo("doctor", "--json")
    assert result.code == 0, result
    assert result.json["checks"]["retired_html_directory"]["ok"]
    assert result.json["notices"] == []


def test_deleting_the_directory_clears_the_notice(kyo, project: Path) -> None:
    """The finding names no command, so what clears it is the reader's own `rm`.

    Asserted anyway, because "nothing clears it" and "it never goes away" are
    different claims and only the first one is true here.
    """
    site = project / "kyosei" / "html"
    site.mkdir()
    (site / "index.html").write_text("old board\n", encoding="utf-8")
    assert kyo("doctor", "--json").json["notices"], "the setup did not raise it"

    (site / "index.html").unlink()
    site.rmdir()
    assert kyo("doctor", "--json").json["notices"] == []


def test_a_run_with_problems_still_carries_both_lists(kyo, db) -> None:
    """KYO-214.c1: the two lists are the envelope's shape, not a clean-run extra.

    A consumer branching on `problems` must not have to test for the key's presence:
    `kyo doctor`'s failure carries the same envelope its success returns (KYO-187).
    """
    record = new_record(kyo)
    with db() as conn:
        conn.execute("DELETE FROM events WHERE record_id = ?", (record,))

    result = kyo("doctor", "--json")
    assert result.code == EXIT_DOCTOR, result
    assert result.json["ok"] is False
    assert result.json["notices"] == []
    assert any(line.startswith("[events] ") for line in result.json["problems"]), result.json[
        "problems"
    ]
    # The human-readable failure counts problems, and nothing else.
    assert f"{len(result.json['problems'])} problem(s)" in result.err, result.err


def test_a_healthy_project_passes(kyo) -> None:
    record = new_record(kyo, criteria=["a", "b"])
    other = new_record(kyo, title="two")
    kyo("link", record, "blocks", other)
    kyo("gate", record, "awaiting:event", "--do", "CI goes green")
    assert kyo("doctor").code == 0


def test_hand_edited_export_is_caught(kyo, project: Path) -> None:
    record = new_record(kyo)
    path = export_of(project, record)
    path.write_text(path.read_text().replace('"open"', '"done"'), encoding="utf-8")
    failures = _failing_checks(kyo)
    assert "exports" in failures
    assert "line" in failures["exports"]["failures"][0]


def test_missing_export_is_caught(kyo, project: Path) -> None:
    record = new_record(kyo)
    export_of(project, record).unlink()
    assert "exports" in _failing_checks(kyo)


def test_stale_export_with_no_record_is_caught(kyo, project: Path) -> None:
    new_record(kyo)
    (project / "kyosei" / "export" / "TST-99.json").write_text("{}\n", encoding="utf-8")
    assert "exports" in _failing_checks(kyo)


def test_a_column_without_a_consumer_is_caught(kyo, db) -> None:
    """Law 2 made mechanical: adding a field without a reader fails the checks."""
    new_record(kyo)
    with db() as conn:
        conn.execute("ALTER TABLE records ADD COLUMN governs TEXT")
    failures = _failing_checks(kyo)
    assert "column_consumers" in failures
    assert "records.governs" in failures["column_consumers"]["failures"][0]


def test_a_config_key_without_a_consumer_is_caught(kyo, project: Path) -> None:
    path = project / "kyosei" / "config.yml"
    path.write_text(path.read_text() + "anchor_hour: 9\n", encoding="utf-8")
    result = kyo("doctor")
    # Config loading itself refuses the unknown key before doctor even runs.
    assert result.code != 0
    assert "anchor_hour" in result.err


def test_an_allocation_that_would_reissue_is_caught(kyo, db) -> None:
    new_record(kyo)
    with db() as conn:
        conn.execute("UPDATE allocations SET next_value = 1 WHERE scope = 'record'")
    failures = _failing_checks(kyo)
    assert "allocations" in failures
    assert "reissue" in failures["allocations"]["failures"][0]


def test_an_allocation_for_a_dead_record_is_caught(kyo, db) -> None:
    new_record(kyo)
    with db() as conn:
        conn.execute("INSERT INTO allocations VALUES ('criterion:TST-77', 2)")
    assert "allocations" in _failing_checks(kyo)


def test_a_foreign_id_prefix_is_caught(kyo, db) -> None:
    new_record(kyo)
    with db() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("UPDATE records SET id = 'OTHER-1' WHERE id = 'TST-1'")
    assert "id_prefixes" in _failing_checks(kyo)


def test_an_orphaned_criterion_is_caught(kyo, db) -> None:
    record = new_record(kyo, criteria=["a"])
    with db() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "UPDATE criteria SET record_id = 'TST-404' WHERE record_id = ?", (record,)
        )
    assert "orphans" in _failing_checks(kyo)


def test_an_orphaned_gate_is_caught(kyo, db) -> None:
    record = new_record(kyo)
    kyo("gate", record, "awaiting:human-review")
    with db() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("UPDATE gates SET record_id = 'TST-404'")
    assert "orphans" in _failing_checks(kyo)


def test_a_criterion_missing_its_required_reason_is_caught(kyo, db) -> None:
    record = new_record(kyo, criteria=["a"])
    with db() as conn:
        conn.execute(
            "UPDATE criteria SET state = 'void-premise-gone' WHERE record_id = ?", (record,)
        )
    assert "criterion_states" in _failing_checks(kyo)


def test_a_wont_do_without_a_reason_is_caught(kyo, db) -> None:
    record = new_record(kyo)
    with db() as conn:
        conn.execute("UPDATE records SET status = 'wont-do' WHERE id = ?", (record,))
    assert "statuses" in _failing_checks(kyo)


def test_a_stale_wont_do_reason_is_caught(kyo, db) -> None:
    """Law 3: two fields that must agree, disagreeing."""
    record = new_record(kyo)
    with db() as conn:
        conn.execute("UPDATE records SET wont_do_reason = 'leftover' WHERE id = ?", (record,))
    assert "statuses" in _failing_checks(kyo)


def test_a_plan_cycle_is_caught(kyo, db) -> None:
    first = new_record(kyo, kind="plan", title="one")
    second = new_record(kyo, kind="plan", title="two")
    with db() as conn:
        conn.execute("UPDATE records SET plan_id = ? WHERE id = ?", (second, first))
        conn.execute("UPDATE records SET plan_id = ? WHERE id = ?", (first, second))
    assert "plan_membership" in _failing_checks(kyo)


def test_a_link_cycle_written_behind_the_cli_is_caught(kyo, db) -> None:
    first = new_record(kyo, title="one")
    second = new_record(kyo, title="two")
    kyo("link", first, "blocks", second)
    with db() as conn:
        conn.execute(
            "INSERT INTO links (id, seq, src_id, edge_type, dst_id, created_at)"
            " VALUES ('TST-L9', 9, ?, 'blocks', ?, '2026-01-01T00:00:00.000Z')",
            (second, first),
        )
    failures = _failing_checks(kyo)
    assert "links" in failures
    assert "cycle in blocks" in failures["links"]["failures"][0]


def test_a_record_with_no_events_is_caught(kyo, db) -> None:
    record = new_record(kyo)
    with db() as conn:
        conn.execute("DELETE FROM events WHERE record_id = ?", (record,))
    assert "events" in _failing_checks(kyo)


def test_a_schema_version_mismatch_fails_loudly_at_open_not_as_a_doctor_check(kyo, db) -> None:
    """KYO-3.c1: opening any store — including for ``kyo doctor`` — with a
    mismatched ``schema_version`` fails loudly before any check runs, naming the
    remedy for the direction it is mismatched in. This used to surface as a doctor
    check failure (exit 3); it is now caught earlier, at open, with exit 1 (see
    tests/test_schema_compat.py for the dedicated coverage of that path).

    99 is AHEAD of any schema this build speaks, so since KYO-168 the remedy named
    here is an upgrade of the build rather than ``kyo rebuild`` — which would
    reconstruct a healthy store under the older schema. What this test is about is
    unchanged: the failure arrives at open, as exit 1, carrying a remedy. Which
    remedy belongs to which direction is pinned in
    tests/test_kyo5_error_remedies.py."""
    new_record(kyo)
    with db() as conn:
        conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    result = kyo("doctor", "--json")
    assert result.code == 1
    assert "schema_version 99" in result.err
    assert "install a kyosei" in result.err


def test_check_schema_version_function_directly_flags_a_mismatch(project: Path) -> None:
    """``check_schema_version`` is unreachable via the CLI now (the mismatch is
    caught earlier, at open) but stays as a defensive check for any ``Store`` built
    by a path that bypasses ``open_store`` — proven directly here (law 14: a check
    nobody proves can fail is not a check)."""
    from kyosei.config import load_config
    from kyosei.doctor_install import check_schema_version
    from kyosei.store import Store, connect

    config = load_config(project)
    conn = connect(config.db_path)
    conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    store = Store(config, conn)
    try:
        failures = check_schema_version(store)
        assert failures and "99" in failures[0]
    finally:
        store.close()


def test_a_record_with_an_undefined_kind_is_caught(kyo, db) -> None:
    record = new_record(kyo)
    with db() as conn:
        conn.execute("UPDATE records SET kind = 'ghost-kind' WHERE id = ?", (record,))
    failures = _failing_checks(kyo)
    assert "kind_registry" in failures
    assert "ghost-kind" in failures["kind_registry"]["failures"][0]


def test_a_broken_kinds_yml_is_caught(kyo, project: Path) -> None:
    new_record(kyo)
    path = project / "kyosei" / "kinds.yml"
    path.write_text(path.read_text(encoding="utf-8") + "  not_a_kind_dict: 5\n", encoding="utf-8")
    failures = _failing_checks(kyo)
    assert "kind_registry" in failures


def test_a_hand_edited_criterion_state_breaks_the_rebuild_roundtrip(kyo, project: Path) -> None:
    """The export must still agree with what a fresh rebuild derives from it."""
    record = new_record(kyo, criteria=["a"])
    export_path = project / "kyosei" / "export" / f"{record}.json"
    export_path.write_text(
        export_path.read_text(encoding="utf-8").replace('"unmet-unbuilt"', '"met"'),
        encoding="utf-8",
    )
    failures = _failing_checks(kyo)
    # The hand-edit is caught by `exports` first (db vs. export mismatch); once that's
    # the only difference, a `kind_registry`-clean, `exports`-clean project's rebuild
    # round trip must also agree — proven separately below.
    assert "exports" in failures


def test_rebuild_roundtrip_passes_on_a_healthy_project(kyo) -> None:
    new_record(kyo, criteria=["a", "b"])
    other = new_record(kyo, title="two")
    kyo("link", "TST-1", "blocks", other)
    kyo("gate", "TST-1", "holding-for", "--do", "a measurement finishes")
    assert kyo("doctor").code == 0


# ------------------------------------------------------------------------- KYO-62
#
# The registry's right-hand side — the consumer strings themselves — was never
# resolved by anything, so it drifted: 88 of 360 strings named nothing, across 20
# distinct names, six of which predated the refactor that made the rest visible.
#
# The tests below exist in the shape they do because the *first* attempt at
# measuring this was wrong in the dangerous direction. It resolved module-level
# attributes only, so `store.allocate` and `store.get_meta` — methods on `Store`,
# and perfectly correct entries — were reported broken. A check that fails on
# correct entries is the one people turn off (KYO-33), so the method case is
# pinned first and by name.


def _index():
    return module_index(PACKAGE_SOURCE_DIR)


def test_a_method_of_a_class_resolves_as_a_consumer() -> None:
    """KYO-62.c2: the exact two references a naive resolver called broken."""
    index = _index()
    assert unresolvable_consumer("store.allocate", index) is None
    assert unresolvable_consumer("store.get_meta", index) is None
    # ...spelled out in full, which is the other legal form for the same reader.
    assert unresolvable_consumer("store.Store.allocate", index) is None
    assert unresolvable_consumer("store.Store.get_meta", index) is None
    # ...and the plain module-level form is untouched by any of that.
    assert unresolvable_consumer("export.record_document", index) is None


def test_a_consumer_that_names_nothing_is_reported_and_correcting_it_clears_it() -> None:
    """`store.get_record` is one of the six that were already broken; `store.record`
    is the method it was renamed to, and naming that clears the failure."""
    index = _index()
    why = unresolvable_consumer("store.get_record", index)
    assert why is not None and "get_record" in why
    assert unresolvable_consumer("store.record", index) is None

    assert unresolvable_consumer("no_such_module.thing", index) is not None
    assert unresolvable_consumer("store.NoSuchClass.record", index) is not None
    assert unresolvable_consumer("store.Store.get_record", index) is not None


def test_a_wildcard_is_not_a_consumer_and_an_explicit_list_is() -> None:
    """KYO-62.c4: `commands.*` was legal only because nothing could evaluate it.
    An entry that can never fail declares nothing; the remedy is naming readers."""
    index = _index()
    why = unresolvable_consumer("commands.*", index)
    assert why is not None and "wildcard" in why
    for explicit in ("store.record", "export.record_document", "doctor_exports.check_orphans"):
        assert unresolvable_consumer(explicit, index) is None


def test_every_string_in_both_live_registries_resolves() -> None:
    """The registry is authoritative documentation. This is the assertion that it
    is also true — and the one that fails the next time a reader is renamed."""
    column_sites = [
        (f"{table}.{column}", ref)
        for table, columns in COLUMN_CONSUMERS.items()
        for column, refs in columns.items()
        for ref in refs
    ]
    assert len(column_sites) > 300, "registry unexpectedly small — is this the real one?"
    assert unresolvable_consumer_failures("COLUMN_CONSUMERS", column_sites) == []

    config_sites = [(key, ref) for key, refs in CONFIG_CONSUMERS.items() for ref in refs]
    assert unresolvable_consumer_failures("CONFIG_CONSUMERS", config_sites) == []


def test_doctor_fails_on_a_registered_consumer_that_no_longer_exists(kyo, monkeypatch) -> None:
    """End to end: the check is `kyo doctor`'s, not just a unit test's (KYO-62.c1)."""
    new_record(kyo)
    broken = {
        table: {column: tuple(refs) for column, refs in columns.items()}
        for table, columns in COLUMN_CONSUMERS.items()
    }
    broken["records"]["title"] = ("export.record_document", "render.show_the_old_way")
    monkeypatch.setattr(doctor_consumers, "COLUMN_CONSUMERS", broken)

    failures = _failing_checks(kyo)
    assert "column_consumers" in failures
    assert "render.show_the_old_way" in failures["column_consumers"]["failures"][0]

    # ...and the remedy the message names — a reader that exists — clears it.
    broken["records"]["title"] = ("export.record_document", "render.show_text")
    assert kyo("doctor").code == 0


def test_doctor_fails_on_a_config_consumer_that_no_longer_exists(kyo, monkeypatch) -> None:
    new_record(kyo)
    broken = {key: tuple(refs) for key, refs in CONFIG_CONSUMERS.items()}
    broken["export_dir"] = ("export.export_directory",)
    monkeypatch.setattr(doctor_consumers, "CONFIG_CONSUMERS", broken)

    failures = _failing_checks(kyo)
    assert "config_consumers" in failures
    assert "export.export_directory" in failures["config_consumers"]["failures"][0]

    broken["export_dir"] = ("export.export_dir",)
    assert kyo("doctor").code == 0


def test_a_build_that_cannot_see_its_own_source_reports_nothing(tmp_path: Path) -> None:
    """The one direction this check must never fail in is "everything is broken".
    A sourceless install can resolve nothing, and reporting all 361 strings as
    unresolvable would be the loudest possible false alarm (KYO-33)."""
    assert module_index(tmp_path) is None
    sites = [("records.title", "render.show_the_old_way")]
    assert unresolvable_consumer_failures("COLUMN_CONSUMERS", sites, source_dir=tmp_path) == []


# ------------------------------------------------------------------------ KYO-216.c3
#
# A format-version bump stamps a new `schema_version` into every fresh derivation
# (`export.record_document`) without touching a single other field of any export
# document already on disk. Before this record, `check_exports` and
# `check_export_roundtrip` each reported that one fact once per document — live,
# 198 findings from the first and 198 more from the second for a single bump
# ("doctor found 397 problem(s)").
# What follows proves the collapse: many documents behind by nothing but their
# stamp collapse into one finding naming the count and the remedy, and a document
# that differs in any OTHER way is still reported on its own.


def _stamp_schema_version_only(project: Path, record_id: str, version: int) -> None:
    """Rewrite one record's export document so it differs from the database (or a
    fresh rebuild) in nothing but its ``schema_version`` stamp — exactly what a
    format-version bump leaves behind."""
    path = export_of(project, record_id)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["schema_version"] = version
    path.write_text(json.dumps(doc), encoding="utf-8")


def _stamp_and_break_a_field(project: Path, record_id: str, version: int) -> None:
    """Like :func:`_stamp_schema_version_only`, but also drops the ``touches`` key
    entirely — a document that differs from the database in a REAL way too, and
    must still get its own finding rather than vanishing into the collapsed one."""
    path = export_of(project, record_id)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["schema_version"] = version
    del doc["touches"]
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_exports_collapses_a_schema_version_only_drift_into_one_finding(
    kyo, project: Path
) -> None:
    a = new_record(kyo, title="alpha", criteria=["a"])
    b = new_record(kyo, title="beta", criteria=["a"])
    c = new_record(kyo, title="gamma", criteria=["a"])

    _stamp_schema_version_only(project, a, SCHEMA_VERSION - 1)
    _stamp_schema_version_only(project, b, SCHEMA_VERSION - 1)
    _stamp_and_break_a_field(project, c, SCHEMA_VERSION - 1)

    exports_failures = _failing_checks(kyo)["exports"]["failures"]

    stamp_finding = [f for f in exports_failures if f.startswith("2 export document(s)")]
    assert len(stamp_finding) == 1, exports_failures
    assert a in stamp_finding[0] and b in stamp_finding[0], stamp_finding
    assert "kyo export --refresh" in stamp_finding[0], stamp_finding

    # `c` differs in a real way too (no `touches` key at all): its own finding,
    # not absorbed into the collapsed one.
    own_finding = [f for f in exports_failures if c in f and f not in stamp_finding]
    assert len(own_finding) == 1, exports_failures
    assert "schema_version stamp" not in own_finding[0], own_finding

    assert len(exports_failures) == 2, exports_failures


def test_export_roundtrip_collapses_a_schema_version_only_drift_into_one_finding(
    kyo, project: Path
) -> None:
    """The export -> db -> export twin of the test above."""
    a = new_record(kyo, title="alpha", criteria=["a"])
    b = new_record(kyo, title="beta", criteria=["a"])
    c = new_record(kyo, title="gamma", criteria=["a"])

    _stamp_schema_version_only(project, a, SCHEMA_VERSION - 1)
    _stamp_schema_version_only(project, b, SCHEMA_VERSION - 1)
    _stamp_and_break_a_field(project, c, SCHEMA_VERSION - 1)

    roundtrip_failures = _failing_checks(kyo)["export_roundtrip"]["failures"]

    stamp_finding = [
        f
        for f in roundtrip_failures
        if f.startswith("rebuild round-trip: 2 export document(s)")
    ]
    assert len(stamp_finding) == 1, roundtrip_failures
    assert a in stamp_finding[0] and b in stamp_finding[0], stamp_finding
    assert "kyo export --refresh" in stamp_finding[0], stamp_finding

    own_finding = [f for f in roundtrip_failures if c in f and f not in stamp_finding]
    assert len(own_finding) == 1, roundtrip_failures
    assert "schema_version stamp" not in own_finding[0], own_finding

    assert len(roundtrip_failures) == 2, roundtrip_failures


def test_a_lone_schema_version_only_document_still_names_the_remedy(
    kyo, project: Path
) -> None:
    """The collapsed wording fires — and still names the remedy — even with just one
    document behind, not only when there are many to fold together."""
    a = new_record(kyo, criteria=["a"])
    _stamp_schema_version_only(project, a, SCHEMA_VERSION - 1)

    failures = _failing_checks(kyo)
    assert len(failures["exports"]["failures"]) == 1, failures["exports"]
    assert a in failures["exports"]["failures"][0]
    assert "kyo export --refresh" in failures["exports"]["failures"][0]

    assert len(failures["export_roundtrip"]["failures"]) == 1, failures["export_roundtrip"]
    assert a in failures["export_roundtrip"]["failures"][0]
    assert "kyo export --refresh" in failures["export_roundtrip"]["failures"][0]


def test_export_ahead_schema_version_only_drift_does_not_recommend_a_refresh(
    kyo, project: Path
) -> None:
    """KYO-74: the remedy this collapsed finding names must respect which side of
    the seam moved, exactly like the per-document finding it replaces — an export
    that is itself the newer side must never be told to overwrite itself with a
    stale database, even when the only visible difference is the stamp."""
    a = new_record(kyo, title="alpha", criteria=["a"])
    b = new_record(kyo, title="beta", criteria=["a"])
    _stamp_schema_version_only(project, a, SCHEMA_VERSION - 1)
    _stamp_schema_version_only(project, b, SCHEMA_VERSION - 1)

    # KYO-74: bump every on-disk counter past the database's, the same mechanism
    # `test_split_store.py`'s currency tests use to put the export ahead.
    counters_path = project / "kyosei" / "export" / "_allocations.json"
    counters = json.loads(counters_path.read_text(encoding="utf-8"))
    counters_path.write_text(
        json.dumps({k: v + 1 for k, v in counters.items()}, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    exports_failures = _failing_checks(kyo)["exports"]["failures"]
    stamp_finding = [f for f in exports_failures if f.startswith("2 export document(s)")]
    assert len(stamp_finding) == 1, exports_failures
    assert "kyo export --refresh" not in stamp_finding[0], stamp_finding
    assert "kyo rebuild --force" in stamp_finding[0], stamp_finding


# ------------------------------------------------------------------------- KYO-267
#
# A criterion waiting on a person, with no open `awaiting:*` gate and nothing running
# against the record. `kyo doctor` runs from this project's `Stop` and `SessionEnd`
# hooks, so it is where a controller who forgot the gate is told before the session
# ends rather than by a human later (KYO-264.c3, KYO-265.c3).
#
# A NOTICE, and the argument for the exception to `NOTICE_CHECKS`'s own bar is written
# out on the check. The short version: the condition is one the tool EXPECTS to see,
# so exit 3 would paint every `Stop` between the verdict and the gate red — which is
# the KYO-214 failure, not a stricter version of it.


def _waiting_on_a_person(kyo, *, reason: str = "the maintainer has not looked yet") -> str:
    record = new_record(kyo, criteria=["the panel refreshes"])
    assert (
        kyo(
            "verify",
            f"{record}.c1",
            "--verdict",
            "unmet-unobserved",
            "--method",
            "reasoned-about",
            "--why",
            reason,
        ).code
        == 0
    )
    return record


def _ungated_unobserved_notices(kyo) -> list[str]:
    result = kyo("doctor", "--json")
    assert result.code == 0, result
    return [
        notice
        for notice in result.json["notices"]
        if notice.startswith("[ungated_unobserved_criteria] ")
    ]


def test_an_unobserved_criterion_with_no_gate_is_reported_as_a_notice(kyo) -> None:
    """KYO-267.c3: it warns, names the criterion, names the remedy, and exits 0."""
    record = _waiting_on_a_person(kyo, reason="watch the badge after a cold start")
    (notice,) = _ungated_unobserved_notices(kyo)
    assert record in notice
    assert f"{record}.c1" in notice
    assert "watch the badge after a cold start" in notice
    assert f"kyo gate {record} awaiting:human-run --do " in notice

    human = kyo("doctor")
    assert human.code == 0, human
    assert "[ungated_unobserved_criteria]" in human.out + human.err, (
        "the notice must reach a human reading the hook's output, not only --json"
    )


def test_the_notice_never_fails_the_hook_it_is_printed_from(kyo) -> None:
    """The whole reason it is a notice. `kyo doctor` runs last in the `Stop` chain and
    Claude Code renders a nonzero hook as an error block; a state the tool expects must
    not produce one."""
    _waiting_on_a_person(kyo)
    result = kyo("doctor", "--json")
    assert result.code == 0, result
    assert result.json["problems"] == []
    assert result.json["checks"]["ungated_unobserved_criteria"]["ok"] is False
    assert result.json["checks"]["ungated_unobserved_criteria"]["tier"] == TIER_NOTICE


def test_gating_the_record_clears_the_notice(kyo) -> None:
    """KYO-267.c3's own clause: the test proves the remedy clears the warning. The
    remedy is the line the finding printed, run as printed."""
    record = _waiting_on_a_person(kyo)
    (notice,) = _ungated_unobserved_notices(kyo)
    assert f"kyo gate {record} awaiting:human-run --do " in notice

    assert kyo("gate", record, "awaiting:human-run", "--do", "watch the panel").code == 0
    assert _ungated_unobserved_notices(kyo) == []


def test_settling_the_criterion_clears_the_notice(kyo) -> None:
    """The other way out, and the one that means the person actually looked."""
    record = _waiting_on_a_person(kyo)
    assert _ungated_unobserved_notices(kyo)
    assert (
        kyo(
            "verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed"
        ).code
        == 0
    )
    assert _ungated_unobserved_notices(kyo) == []


def test_a_running_dispatch_raises_no_notice(kyo, git_project) -> None:
    """A worker is out there and is about to observe it; gating now would be
    premature, so the check says nothing."""
    record = _waiting_on_a_person(kyo)
    assert kyo("dispatch", record, "--json").code == 0
    assert _ungated_unobserved_notices(kyo) == []


def test_a_clean_board_raises_nothing(kyo) -> None:
    """A check that fires at everyone is one nobody reads."""
    new_record(kyo, criteria=["the panel refreshes"])
    assert _ungated_unobserved_notices(kyo) == []


def test_doctor_and_the_brief_name_the_same_records(kyo) -> None:
    """One predicate, three surfaces (KYO-267). Two of them, compared: a controller
    told by one and not the other learns to believe neither."""
    record = _waiting_on_a_person(kyo)
    from_brief = {item["id"] for item in kyo("brief", "--json").json["ungated_unobserved"]}
    assert from_brief == {record}
    assert len(_ungated_unobserved_notices(kyo)) == len(from_brief)


# ------------------------------------------------------------------------ KYO-309
#
# Three checks were written from one end of the package and run at both. For a
# CONSUMER the packaged copy is the source and the project's file is the copy, so a
# difference means the project is stale and `kyo install-*` is the remedy. In the
# repository the package is BUILT FROM the direction is reversed: `.claude/skills/
# kyosei/SKILL.md` and `kyosei/procedures.md` are what the next package is made of,
# and the `kyo` on PATH is an installed build that can only be behind. There the old
# shape turned doctor red after every skill edit (met three times in one day) and
# named `kyo install-skill`, which would have overwritten the newer skill with the
# older one.
#
# The detector is the presence of the asset's source (`src/kyosei/defaults/<asset>`, or
# for the skill `src/kyosei/harness/claude-code/plugin/skills/kyosei/`) under the code root —
# a file test, not a project name or an id prefix — so every test below builds a
# source repository by writing that file into the fixture project, and the consumer
# tests are the same project without it.


def _skill_path(project: Path) -> Path:
    return project / install_skill.SKILL_ASSET.target


def _source_path(project: Path, asset) -> Path:
    return project / asset.source_path


def _make_source_repo(project: Path, asset, text: str) -> Path:
    """Write ``text`` as this project's in-repo source for ``asset``."""
    path = _source_path(project, asset)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _findings(kyo, name: str) -> list[str]:
    """One check's findings, whichever tier the run exited in."""
    result = kyo("doctor", "--json")
    assert result.code in (0, EXIT_DOCTOR), result
    return result.json["checks"][name]["failures"]


def _rule_titles(text: str) -> list[str]:
    return re.findall(r"^### (.+)$", text, re.MULTILINE)


def _without_the_last_rule(text: str) -> tuple[str, str]:
    title = _rule_titles(text)[-1]
    return text[: text.index(f"### {title}")], title


def _with_the_last_rule_reworded(text: str) -> tuple[str, str]:
    title = _rule_titles(text)[-1]
    head = f"### {title}\n"
    at = text.index(head) + len(head)
    return text[:at] + "\nA sentence this checkout added on purpose.\n" + text[at:], title


def _ledger(project: Path) -> Path:
    return project / "kyosei" / "procedures.md"


def test_the_source_directory_is_spelled_once(kyo) -> None:
    """Law 3. ``procedures.PACKAGED_LEDGER_SOURCE_PATH`` predates KYO-309 and states
    the same path for the ledger's half; two spellings that could drift apart are one
    spelling and a check."""
    assert (
        str(
            scaffold_module.PACKAGED_ASSET_SOURCE_DIR
            / scaffold_module.PROCEDURES_ASSET.source
        )
        == procedures_module.PACKAGED_LEDGER_SOURCE_PATH
    )


def test_the_build_lag_notice_is_a_notice(kyo) -> None:
    """KYO-309.c1: the finding must not fail the command. The whole bug is a `Stop`
    hook painted red over a condition the session cannot clear."""
    assert tier_of("installed_build_lag") == TIER_NOTICE
    assert "installed_build_lag" in CHECK_ORDER


# -------- state 1: the project's skill matches its source and the build is current


def test_a_source_repo_in_step_with_its_build_is_silent(kyo, project: Path) -> None:
    kyo("install-skill", "--copies")
    # KYO-604: a consumer's install ends with a digest line; a source repository's copy
    # is a byte mirror of its source and carries none.
    packaged, _digest = install_skill.split_skill_digest(
        _skill_path(project).read_text(encoding="utf-8")
    )
    _skill_path(project).write_text(packaged, encoding="utf-8")
    # KYO-593: the source holds the packaged text; the copy is its installed form.
    _make_source_repo(project, install_skill.SKILL_ASSET, install_skill.packaged_skill_text())

    result = kyo("doctor", "--json")
    assert result.code == 0, result
    assert result.json["checks"]["skill"]["failures"] == []
    assert result.json["checks"]["installed_build_lag"]["failures"] == []


# -------- state 2: the project's skill matches its source; only the build is behind


def _a_lagging_build(kyo, project: Path) -> str:
    """The measured case: the skill is edited in this repository and mirrored into its
    packaged source, and the installed `kyo` still carries the previous text."""
    kyo("install-skill", "--copies")
    newer = install_skill.packaged_skill_text() + "\n<!-- edited today -->\n"
    _skill_path(project).write_text(install_skill.installed_skill_text(newer), encoding="utf-8")
    _make_source_repo(project, install_skill.SKILL_ASSET, newer)
    return newer


def test_a_lagging_build_does_not_fail_the_doctor(kyo, project: Path) -> None:
    """KYO-309.c1. This is the bug as it was met: every edit to the skill turned the
    Stop hook red. The project's copy is not stale — nothing here is, except `kyo`."""
    _a_lagging_build(kyo, project)

    result = kyo("doctor", "--json")
    assert result.code == 0, result
    assert result.json["checks"]["skill"]["failures"] == []


def test_a_lagging_build_is_reported_as_a_notice_naming_reinstalling(
    kyo, project: Path
) -> None:
    """KYO-309.c1: it still has to be SAID — a controller following a skill the
    installed build does not implement is the drift this family exists for."""
    _a_lagging_build(kyo, project)

    notices = kyo("doctor", "--json").json["notices"]
    lag = [line for line in notices if line.startswith("[installed_build_lag]")]
    assert len(lag) == 1, notices
    assert "installed build" in lag[0]
    assert "older than" in lag[0]
    # KYO-450: the remedy is this build's own upgrade route, release flag and all --
    # never the desktop app's re-pack, which ships no `kyo` any more.
    assert buildinfo.upgrade_route() in lag[0]
    assert "re-pack" not in lag[0]
    # ...and the one command it must never be read as recommending appears only as
    # the thing not to do. `kyo install-skill` here writes the OLDER packaged text
    # over the newer source, which is the edit that produced this finding.
    assert "Never `kyo install-skill`" in lag[0]
    assert "run `kyo install-skill`" not in lag[0]


def test_the_reinstall_the_notice_names_clears_it(kyo, project: Path) -> None:
    """KYO-309.c1's "every remedy the check can name is proved to clear its finding
    without losing the newer text". A reinstall is a build rather than a command, so
    what it does is modelled: the packaged copy becomes this checkout's source. Here
    that is the installed build's skill catching up with the edit."""
    newer = _a_lagging_build(kyo, project)
    assert _findings(kyo, "installed_build_lag") != []

    # What reinstalling does, and the only thing it does, to the two files involved.
    _make_source_repo(
        project,
        install_skill.SKILL_ASSET,
        install_skill.packaged_skill_text(),
    )
    _skill_path(project).write_text(
        install_skill.installed_skill_text(install_skill.packaged_skill_text()),
        encoding="utf-8",
    )

    result = kyo("doctor", "--json")
    assert result.code == 0, result
    assert result.json["checks"]["installed_build_lag"]["failures"] == []
    # The text is not lost by the remedy — it is lost only if you skip the mirror,
    # which is the finding below, not this one.
    assert newer != install_skill.packaged_skill_text()


# -------- state 3: the project's skill and its own source have come apart


def test_a_broken_mirror_is_a_real_finding_that_never_names_install_skill(
    kyo, project: Path
) -> None:
    """KYO-309.c1's third state. ``tests/test_skill_cli_sync.py`` requires these two
    files to be byte-identical, so a difference is a real problem here — but the
    remedy cannot be the command that resolves the consumer's version of it."""
    kyo("install-skill", "--copies")
    _make_source_repo(project, install_skill.SKILL_ASSET, install_skill.packaged_skill_text())
    _skill_path(project).write_text("# the newer skill\n", encoding="utf-8")

    result = kyo("doctor", "--json")
    assert result.code == EXIT_DOCTOR, result
    finding = result.json["checks"]["skill"]["failures"]
    assert len(finding) == 1, finding
    assert install_skill.SKILL_ASSET.target in finding[0]
    assert str(install_skill.SKILL_ASSET.source_path) in finding[0]
    assert "Do NOT run `kyo install-skill`" in finding[0]


def test_both_remedies_the_broken_mirror_names_clear_it_and_keep_their_text(
    kyo, project: Path
) -> None:
    """KYO-309.c1. Two copy directions, each labelled in the finding with the text it
    keeps, and each proved here to clear the finding while keeping that text. Neither
    is guessed at for the reader: which file they edited is the one fact the tool
    cannot read, and naming one direction only is how the newer text gets lost."""
    kyo("install-skill", "--copies")
    original = install_skill.packaged_skill_text()
    newer_skill = "# the newer skill, citing ${CLAUDE_SKILL_DIR}/REFERENCE.md\n"

    for keep_the_skill in (True, False):
        source = _make_source_repo(project, install_skill.SKILL_ASSET, original)
        _skill_path(project).write_text(
            install_skill.installed_skill_text(newer_skill), encoding="utf-8"
        )
        finding = _findings(kyo, "skill")
        assert len(finding) == 1

        # KYO-593.c3: the two files differ by one path substitution, so the remedies
        # are `sed` lines rather than `cp`; run the printed line itself, from the
        # project root, as the reader would.
        label = "keeps the skill's text" if keep_the_skill else "keeps the packaged source's text"
        (line,) = [ln for ln in finding[0].splitlines() if ln.endswith(label)]
        command = line.split("   # ")[0].strip()
        subprocess.run(["/bin/sh", "-c", command], cwd=project, check=True)
        kept = newer_skill if keep_the_skill else original

        assert _findings(kyo, "skill") == []
        assert source.read_text(encoding="utf-8") == kept
        assert _skill_path(project).read_text(
            encoding="utf-8"
        ) == install_skill.installed_skill_text(kept)


# -------- the consumer, whose every path through these checks is unchanged


def test_a_consumer_project_is_checked_exactly_as_before(kyo, project: Path) -> None:
    """KYO-309.c1's last clause. No ``src/kyosei/defaults/`` — so the packaged copy is
    the source, a difference is the project's staleness, and `kyo install-skill` is
    both named and correct."""
    assert not _source_path(project, install_skill.SKILL_ASSET).exists()
    kyo("install-skill", "--copies")
    _skill_path(project).write_text("# an old copy\n", encoding="utf-8")

    result = kyo("doctor", "--json")
    assert result.code == EXIT_DOCTOR, result
    finding = result.json["checks"]["skill"]["failures"]
    assert len(finding) == 1
    assert "run `kyo install-skill`" in finding[0]
    assert result.json["checks"]["installed_build_lag"]["failures"] == []

    assert kyo("install-skill", "--copies").code == 0
    assert kyo("doctor").code == 0


def test_a_consumer_never_sees_the_build_lag_notice(kyo, project: Path) -> None:
    """Structurally silent, not merely quiet: a consumer has no source file to lag
    behind, and a notice they could never act on is one more line in every brief."""
    assert _findings(kyo, "installed_build_lag") == []
    kyo("install-skill", "--copies")
    assert _findings(kyo, "installed_build_lag") == []


# -------- KYO-309.c2: the same question asked of the ledger checks
#
# The answer is that the ledger checks DO have the reversal, in both halves and for
# the same reason, and that the fix is the same one. `kyosei/procedures.md` is a
# merged, project-editable file rather than a byte mirror, which changes what a
# difference MEANS but not which file is the source: in this repository the ledger is
# the hand-written copy and `src/kyosei/defaults/procedures.md` is what the next build
# packages, so an older installed build is behind both.
#
# Two live findings came out of it, each proved below against the same fixture:
#   * a rule deleted from this repository's source and its ledger is reported by an
#     older build as one the ledger is MISSING — red, with `kyo install-procedures`
#     named, which would put the deleted rule back;
#   * a rule reworded in this repository's source and its ledger is reported as the
#     project's own drift, with `--adopt` named beside it, which would take the older
#     packaged wording.


def test_a_rule_this_checkout_deleted_is_not_reported_as_missing(kyo, project: Path) -> None:
    """KYO-309.c2, the red half. The build predates the deletion; nothing is missing."""
    packaged = install_procedures.packaged_procedures_text()
    trimmed, title = _without_the_last_rule(packaged)
    _make_source_repo(project, scaffold_module.PROCEDURES_ASSET, trimmed)
    _ledger(project).write_text(trimmed, encoding="utf-8")

    # The comparison the old code made, shown failing, so this test says which of the
    # two answers KYO-309.c2 asks for is the one it found.
    against_the_build = procedures_module.compare_ledgers(packaged, trimmed)
    assert [
        difference.title
        for difference in against_the_build
        if difference.kind == procedures_module.LEDGER_MISSING
    ] == [title]

    result = kyo("doctor", "--json")
    assert result.code == 0, result
    assert result.json["checks"]["procedures_rules_missing"]["failures"] == []


def test_a_rule_this_checkout_reworded_is_not_reported_as_the_projects_drift(
    kyo, project: Path
) -> None:
    """KYO-309.c2, the notice half. Its remedy was the destructive one: `--adopt`
    takes the wording out of the installed build."""
    packaged = install_procedures.packaged_procedures_text()
    reworded, title = _with_the_last_rule_reworded(packaged)
    _make_source_repo(project, scaffold_module.PROCEDURES_ASSET, reworded)
    _ledger(project).write_text(reworded, encoding="utf-8")

    against_the_build = procedures_module.compare_ledgers(packaged, reworded)
    assert [
        difference.title
        for difference in against_the_build
        if difference.kind == procedures_module.LEDGER_CHANGED
    ] == [title]

    result = kyo("doctor", "--json")
    assert result.code == 0, result
    assert result.json["checks"]["procedures_rules_differ"]["failures"] == []
    # ...and the age of the build, which is what is actually true, is said once.
    assert len(result.json["checks"]["installed_build_lag"]["failures"]) == 1


def test_a_ledger_behind_this_repositorys_own_source_is_still_a_finding(
    kyo, project: Path
) -> None:
    """The fix must not silence the check here, only turn it round. A rule added to
    this repository's packaged source and not to its ledger is the real KYO-212 drift,
    arriving one step earlier than a consumer meets it."""
    packaged = install_procedures.packaged_procedures_text()
    trimmed, title = _without_the_last_rule(packaged)
    _make_source_repo(project, scaffold_module.PROCEDURES_ASSET, packaged)
    _ledger(project).write_text(trimmed, encoding="utf-8")

    result = kyo("doctor", "--json")
    assert result.code == EXIT_DOCTOR, result
    finding = result.json["checks"]["procedures_rules_missing"]["failures"]
    assert len(finding) == 1, finding
    assert title in finding[0]
    assert procedures_module.PACKAGED_LEDGER_SOURCE_PATH in finding[0]
    # The command that clears a consumer's version of this reads the INSTALLED build's
    # copy, which this checkout is the source of; it is named only to say why not.
    assert "run `kyo install-procedures`" not in finding[0]

    # The remedy it does name — copy the rule across from the source — clears it, and
    # loses nothing: the ledger keeps everything it already had.
    _ledger(project).write_text(packaged, encoding="utf-8")
    assert _findings(kyo, "procedures_rules_missing") == []


def test_a_reworded_ledger_rule_here_names_the_source_file_not_adopt(
    kyo, project: Path
) -> None:
    """The notice still fires between this repository's own two copies — and `--adopt`
    is the one remedy it must not offer, because it would fetch a third text."""
    packaged = install_procedures.packaged_procedures_text()
    reworded, title = _with_the_last_rule_reworded(packaged)
    _make_source_repo(project, scaffold_module.PROCEDURES_ASSET, packaged)
    _ledger(project).write_text(reworded, encoding="utf-8")

    result = kyo("doctor", "--json")
    assert result.code == 0, result
    finding = result.json["checks"]["procedures_rules_differ"]["failures"]
    assert len(finding) == 1, finding
    assert title in finding[0]
    assert procedures_module.PACKAGED_LEDGER_SOURCE_PATH in finding[0]
    # `--adopt <title>` is a runnable command that would fetch the installed build's
    # older wording; here it appears only as a bare warning with nothing to copy.
    assert f"--adopt {title!r}" not in finding[0]
    assert "Never `kyo install-procedures --adopt` here" in finding[0]

    _ledger(project).write_text(packaged, encoding="utf-8")
    assert _findings(kyo, "procedures_rules_differ") == []


def test_a_consumers_ledger_checks_are_unchanged(kyo, project: Path) -> None:
    """KYO-309.c2's other side. No source file: the packaged copy is the default, and
    both messages keep the commands that are correct there."""
    packaged = install_procedures.packaged_procedures_text()
    trimmed, title = _without_the_last_rule(packaged)
    _ledger(project).write_text(trimmed, encoding="utf-8")

    result = kyo("doctor", "--json")
    assert result.code == EXIT_DOCTOR, result
    missing = result.json["checks"]["procedures_rules_missing"]["failures"]
    assert len(missing) == 1 and title in missing[0]
    assert "run `kyo install-procedures`" in missing[0]

    reworded, title = _with_the_last_rule_reworded(packaged)
    _ledger(project).write_text(reworded, encoding="utf-8")
    differ = _findings(kyo, "procedures_rules_differ")
    assert len(differ) == 1 and title in differ[0]
    assert f"--adopt {title!r}" in differ[0]


# ------------------------------------------------------------------------- KYO-362
#
# What a finding may cost a reader. Both halves of this record are about the same
# thing measured in characters: a check that formats a whole collection into one
# message, and a hook that prints every message it was given. The case that motivated
# the record is the two of them together -- a failing `Stop` hook whose first finding
# was two store-sized allocation maps, filling the terminal on a store where, it turned
# out, one counter was wrong.


def _finding_of(kyo, check: str) -> str:
    findings = _failing_checks(kyo)[check]["failures"]
    assert len(findings) == 1, findings
    return findings[0]


def _counters_path(project: Path) -> Path:
    return project / "kyosei" / "export" / "_allocations.json"


#: What the snapshot said before a test broke it, so :func:`_heal_the_roundtrip` can
#: put the exact bytes back. Keyed by project, since each test gets its own tmp tree.
_ORIGINAL_COUNTERS: dict[Path, str] = {}


def _explode_the_counters(project: Path, *, how_many: int = 300) -> dict:
    """A store of the motivating size: several hundred counters in the export's
    snapshot, which is what ``kyo rebuild`` seeds ``allocations`` from.

    Every consumer store looks like this — a counter per record for its criteria, its
    notes and its gates — and a consumer project's store had several hundred of them,
    more than a screen.
    """
    path = _counters_path(project)
    _ORIGINAL_COUNTERS[project] = path.read_text(encoding="utf-8")
    counters = json.loads(path.read_text(encoding="utf-8"))
    for index in range(how_many):
        counters[f"criterion:TST-{index}"] = 2
    path.write_text(
        json.dumps(counters, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return counters


def _write_counters(project: Path, counters: dict) -> None:
    _counters_path(project).write_text(
        json.dumps(counters, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def test_the_allocations_finding_names_only_the_counters_that_differ(
    kyo, project: Path
) -> None:
    """KYO-362.c1. Several hundred counters, exactly one of them wrong.

    The counter is made wrong the way a snapshot actually goes wrong — written as a
    JSON *string* by a hand edit, where `kyo rebuild` seeds the table with it and the
    re-read comes back an integer. What matters to this test is only that the two maps
    disagree about one key out of several hundred, which is the shape of the case that
    motivated the record.
    """
    new_record(kyo, title="alpha", criteria=["a"])
    counters = _explode_the_counters(project)
    counters["criterion:TST-42"] = "0007"
    _write_counters(project, counters)

    finding = _finding_of(kyo, "export_roundtrip")
    assert "criterion:TST-42" in finding, finding
    assert "0007" in finding and "7" in finding, finding
    assert "301 agree" in finding, finding
    assert len(finding) < 400, (len(finding), finding)
    # And the thing that made it unreadable is gone: no counter that AGREES is named.
    assert "criterion:TST-41" not in finding, finding


def test_more_than_ten_differing_counters_print_ten_and_a_count(kyo) -> None:
    """KYO-362.c1's other half, on the helper, because provoking two hundred genuinely
    differing counters through a real rebuild would be a test about how to corrupt a
    snapshot rather than about what the finding says."""
    on_disk = {f"criterion:TST-{i}": 2 for i in range(200)}
    rebuilt = {f"criterion:TST-{i}": 3 for i in range(200)}
    rebuilt["criterion:TST-199"] = 2  # one that agrees, so the count is not a constant
    message = doctor_exports._allocation_differences(on_disk, rebuilt)
    assert "199 counter(s) differ (1 agree)" in message, message
    assert "and 189 more differing" in message, message
    assert len(message) < 600, (len(message), message)
    named = [key for key in on_disk if f"{key}:" in message]
    assert len(named) == doctor_exports.ALLOCATION_DIFF_LIMIT, named


def test_a_counter_on_one_side_only_is_a_difference_and_says_so() -> None:
    """Absent is not zero, and the two faults are not the same one."""
    message = doctor_exports._allocation_differences({"record": 4}, {})
    assert "record: on disk 4, rebuilt absent" in message, message
    message = doctor_exports._allocation_differences({}, {"record": 4})
    assert "record: on disk absent, rebuilt 4" in message, message


# --------------------------------------------------- KYO-362: `kyo doctor --hook`
#
# One line and a pointer. The flag changes what is PRINTED and nothing else -- the
# same checks run, in the same order, and the exit status is the one bare `kyo doctor`
# would have given -- so every test below asserts the unchanged half too.


def _report_path(project: Path) -> Path:
    return project / "kyosei" / config_module.DOCTOR_REPORT_NAME


def _break_the_roundtrip(kyo, project: Path) -> str:
    """Make the round-trip check fail, and return the counter it will name.

    Everything a test wants in the store has to be there BEFORE this runs: a snapshot
    ahead of the database is a state every mutating command refuses to write over
    (KYO-74), which is correct and is why healing is a matter of putting the file back
    rather than of `kyo export --refresh`.
    """
    new_record(kyo, title="alpha", criteria=["a"])
    counters = _explode_the_counters(project)
    counters["criterion:TST-42"] = "0007"
    _write_counters(project, counters)
    return "criterion:TST-42"


def _heal_the_roundtrip(project: Path) -> None:
    """Put the snapshot back, byte for byte, to what the store itself last wrote.

    Restoring the bytes rather than regenerating them, because an export ahead of its
    database is refused by every mutating command and by `kyo export --refresh` alike
    (KYO-74) — correctly, and this test is about what `--hook` does once a store is
    clean again, not about how a corrupted one is repaired.
    """
    _counters_path(project).write_text(_ORIGINAL_COUNTERS.pop(project), encoding="utf-8")


def test_doctor_hook_prints_one_line_and_leaves_the_report_in_a_file(
    kyo, project: Path
) -> None:
    """KYO-362.c2. The line carries the problem count, the failing check's name and
    the path; the findings themselves are in the file, and nowhere else."""
    counter = _break_the_roundtrip(kyo, project)
    bare = kyo("doctor")
    assert bare.code == EXIT_DOCTOR, bare

    result = kyo("doctor", "--hook")
    assert result.code == bare.code, (result, bare)
    assert result.out == "", result.out

    assert result.err.count("\n") == 1 and result.err.endswith("\n"), repr(result.err)
    line = result.err.strip()
    failing = sorted(_failing_checks(kyo))
    problems = kyo("doctor", "--json").json["problems"]
    assert f"{len(problems)} problem(s)" in line, (line, problems)
    for name in failing:
        assert name in line, (name, line)
    assert "export_roundtrip" in failing, failing
    report = _report_path(project)
    assert str(report) in line, line
    # The whole defect, stated as an assertion: the finding is not in the line.
    assert counter not in line, line
    assert len(line) < 400, (len(line), line)

    detail = report.read_text(encoding="utf-8")
    assert counter in detail, detail
    assert "export_roundtrip" in detail


def test_doctor_hook_is_silent_and_removes_a_report_an_earlier_run_left(
    kyo, project: Path
) -> None:
    """KYO-362.c2's other half. A store that has started passing must not go on
    pointing a reader at findings that are gone -- the self-healing rule KYO-31 wrote
    into this hook's ordering, applied to the file it now writes."""
    counter = _break_the_roundtrip(kyo, project)
    assert kyo("doctor", "--hook").code == EXIT_DOCTOR
    report = _report_path(project)
    assert counter in report.read_text(encoding="utf-8")

    _heal_the_roundtrip(project)
    result = kyo("doctor", "--hook")
    assert result.code == 0, result
    assert result.out == "" and result.err == "", (result.out, result.err)
    assert not report.exists(), report.read_text(encoding="utf-8")


def test_doctor_hook_changes_nothing_but_the_printing(kyo, project: Path) -> None:
    """KYO-362.c2's promise, held to the envelope: the same checks run and report the
    same findings, whichever way the command was invoked."""
    _break_the_roundtrip(kyo, project)
    bare = kyo("doctor", "--json")
    hooked = kyo("doctor", "--hook", "--json")
    assert bare.code == hooked.code == EXIT_DOCTOR
    assert hooked.json["checks"] == bare.json["checks"]
    assert hooked.json["problems"] == bare.json["problems"]
    assert hooked.json["notices"] == bare.json["notices"]
    # ...plus exactly three keys: two are how a `--json` consumer finds the file, and
    # KYO-408's third says how each check with a hook form was satisfied.
    assert set(hooked.json) - set(bare.json) == {
        cmd_store_module.DOCTOR_HOOK_LINE_KEY,
        cmd_store_module.DOCTOR_REPORT_PATH_KEY,
        cmd_store_module.DOCTOR_HOOK_FORMS_KEY,
    }
    assert hooked.json[cmd_store_module.DOCTOR_REPORT_PATH_KEY] == str(
        _report_path(project)
    )


def test_the_hook_line_names_kyo_doctor_and_that_command_prints_what_the_file_holds(
    kyo, project: Path
) -> None:
    """KYO-362.c3, and CLAUDE.md's standing rule about remedies: the line names a way
    to read the detail in the terminal, and running it prints the findings the file
    holds. Not "a similar report" -- the same strings, because `sort_into_tiers` is
    the one place a finding is formatted (law 3)."""
    _break_the_roundtrip(kyo, project)
    hooked = kyo("doctor", "--hook")
    assert "`kyo doctor`" in hooked.err, hooked.err

    findings = [
        line.strip()[2:]
        for line in _report_path(project).read_text(encoding="utf-8").splitlines()
        if line.startswith("  - ")
    ]
    assert findings, "the report holds no findings to compare against"

    bare = kyo("doctor")
    assert bare.code == EXIT_DOCTOR
    for finding in findings:
        assert finding in bare.err, (finding, bare.err)


def test_a_notice_never_reaches_the_hook_line(kyo, project: Path) -> None:
    """Notices exit 0 on their own, so they are never why this line is printing, and
    a line that carried them would grow with conditions nobody is being asked to fix.
    They stay in the file, which is where detail lives."""
    _waiting_on_a_person(kyo)
    _break_the_roundtrip(kyo, project)
    result = kyo("doctor", "--hook", "--json")
    notices = result.json["notices"]
    assert notices, "this fixture produces no notice, so the test proves nothing"
    line = result.json[cmd_store_module.DOCTOR_HOOK_LINE_KEY]
    for notice in notices:
        assert notice not in line, (notice, line)
    detail = _report_path(project).read_text(encoding="utf-8")
    for notice in notices:
        assert notice in detail, (notice, detail)


def test_the_report_file_is_gitignored_by_the_line_install_procedures_tops_up(
    project: Path,
) -> None:
    """KYO-362.c2's "gitignored the way `kyosei.db` and `sessions.json` are", held to
    the mechanism rather than to the scaffolded file: an existing project gets the
    line from `kyo install-procedures`' top-up, which is how KYO-322's two sidecars
    reached every project that predated them."""
    assert config_module.DOCTOR_REPORT_NAME in scaffold_module.KYOSEI_GITIGNORE
    store_ignore = project / "kyosei" / ".gitignore"
    assert config_module.DOCTOR_REPORT_NAME in store_ignore.read_text(encoding="utf-8")

    # ...and a project scaffolded before this line existed is topped up, not rewritten.
    store_ignore.write_text("kyosei.db\n", encoding="utf-8")
    assert config_module.DOCTOR_REPORT_NAME in install_gitignore.missing_store_gitignore_lines(
        project
    )
    changes, _ = install_gitignore.install_store_gitignore(project)
    assert changes and config_module.DOCTOR_REPORT_NAME in changes[0]
    after = store_ignore.read_text(encoding="utf-8")
    assert after.startswith("kyosei.db\n"), after
    assert config_module.DOCTOR_REPORT_NAME in after
    assert not install_gitignore.missing_store_gitignore_lines(project)


# ----------------------------------------------------------------------- KYO-362.c5
#
# WHAT A FINDING MAY COST A READER, AS A CHECK RATHER THAN AS A HABIT.
#
# The defect this record was filed for is one check formatting two whole collections
# into one message. Nothing was wrong with the check's substance and no check would
# have gone red over it, because "how long is this message" was nobody's question. It
# is this file's now, in the two
# halves the criterion asks for and for the reason it asks for both.
#
# THE RUNTIME HALF runs every registered check against a large store broken where
# breaking it is practical, and holds every finding it gets to
# `doctor.MAX_FINDING_CHARS`. It is the stronger half where it reaches, and it names
# what it did not reach rather than implying it reached everything -- KYO-77's shape,
# and for KYO-77's reason: a check that silently covers part of a surface is the same
# false assurance it exists to prevent. It found the SECOND instance of the defect as
# soon as it was written (`db_export_currency`, at 5,989 characters, naming every
# drifted counter twice).
#
# THE STATIC HALF is what covers the checks the first cannot provoke. It refuses the
# SHAPE rather than measuring the output: a name bound to a collection, interpolated
# whole into a finding. That is what both instances were, it is what nothing about a
# check's own source otherwise makes visible, and it is the only half that can say
# anything about a check no fixture in this repository knows how to break.


def _a_large_broken_store(kyo, project: Path, db) -> list[str]:
    """A store with several hundred counters and a dozen records, broken eight ways.

    Deliberately broken in ways that make the checks quote the STORE back: a record
    id per drifted counter, a whole export document's line, a registry's declared
    values. A fixture that broke one small thing would pass this test without
    exercising the thing it is about.
    """
    ids = [new_record(kyo, title=f"record {i}", criteria=["a", "b"]) for i in range(12)]

    document = json.loads(export_of(project, ids[0]).read_text(encoding="utf-8"))
    document["title"] = "a title long enough to be worth clipping, " * 30
    export_of(project, ids[0]).write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8"
    )
    (project / "kyosei" / "export" / "TST-999.json").write_text("{}\n", encoding="utf-8")

    counters = _explode_the_counters(project)
    counters["criterion:TST-42"] = "0007"
    _write_counters(project, counters)

    with db() as conn:
        conn.execute(
            "UPDATE criteria SET state = 'made-up' WHERE id = ?", (f"{ids[1]}.c1",)
        )
        conn.execute("UPDATE records SET status = 'invented' WHERE id = ?", (ids[2],))
        conn.execute("UPDATE records SET kind = 'nonesuch' WHERE id = ?", (ids[3],))
        conn.execute(
            "UPDATE criteria SET record_id = 'TST-404' WHERE id = ?", (f"{ids[5]}.c2",)
        )
        conn.execute("DELETE FROM events WHERE record_id = ?", (ids[4],))
    return ids


#: The checks the fixture above actually breaks. A floor, asserted so that a fixture
#: that stops provoking something fails here instead of quietly measuring nothing --
#: not a ceiling, and not a claim about the checks it does not name.
_PROVOKED_BY_THE_FIXTURE = frozenset(
    {
        "allocations",
        "criterion_states",
        "db_export_currency",
        "events",
        "export_roundtrip",
        "exports",
        "kind_registry",
        "orphans",
        "statuses",
    }
)


def test_no_doctor_finding_is_longer_than_the_stated_limit(kyo, project: Path, db) -> None:
    """KYO-362.c5, the runtime half."""
    _a_large_broken_store(kyo, project, db)
    result = kyo("doctor", "--json")
    assert result.code == EXIT_DOCTOR, result

    findings = [
        (name, finding)
        for name, detail in result.json["checks"].items()
        for finding in detail["failures"]
    ]
    assert findings, "the fixture broke nothing, so this measures nothing"
    provoked = {name for name, _ in findings}
    assert provoked >= _PROVOKED_BY_THE_FIXTURE, sorted(
        _PROVOKED_BY_THE_FIXTURE - provoked
    )

    too_long = [
        (name, len(finding), finding[:200])
        for name, finding in findings
        if len(finding) > doctor_module.MAX_FINDING_CHARS
    ]
    assert not too_long, (
        f"{len(too_long)} finding(s) over {doctor_module.MAX_FINDING_CHARS} characters: "
        f"{too_long}. `kyo doctor` runs from the Stop hook and the harness renders all "
        "of a failing hook's stderr, so a finding that grows with the store is a "
        "terminal full of one message (KYO-362). Name a few, count the rest."
    )


def test_the_length_limit_would_catch_the_finding_this_record_was_filed_for() -> None:
    """The measurement, so the limit above is not a number nobody checked. Two whole
    allocation maps, at the size of the motivating case, against what the finding says
    now."""
    on_disk = {f"criterion:APP-{i}": 2 for i in range(300)}
    rebuilt = dict(on_disk, **{"criterion:APP-7": 3})
    before = f"on disk {on_disk} vs rebuilt {rebuilt}"
    assert len(before) > 20 * doctor_module.MAX_FINDING_CHARS, len(before)
    after = doctor_exports._allocation_differences(on_disk, rebuilt)
    assert len(after) < doctor_module.MAX_FINDING_CHARS, (len(after), after)


# --------------------------------------------- KYO-362.c5, the static half


_COLLECTION_CALLS = frozenset({"dict", "set", "list", "sorted", "tuple", "frozenset"})
_COLLECTION_ANNOTATIONS = frozenset({"dict", "set", "list", "tuple", "frozenset", "Counter"})


def _returns_a_collection() -> set[str]:
    """Every function in ``src/kyosei/`` whose return annotation is a collection.

    Read from the annotations rather than guessed from the name, because the finding
    this record was filed for interpolated ``ledgers.read_allocations_in(...)``'s
    result — a name that says nothing about being a mapping, in a call whose
    annotation says exactly that.
    """
    found: set[str] = set()
    for path in sorted(PACKAGE_SOURCE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.returns is None:
                continue
            returns = node.returns
            base = returns.value if isinstance(returns, ast.Subscript) else returns
            name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
            if name in _COLLECTION_ANNOTATIONS:
                found.add(node.name)
    return found


def _collection_names_in(function: ast.FunctionDef, returning: set[str]) -> set[str]:
    """Names this function binds to something that is a whole collection."""
    names: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.AnnAssign):
            targets, value, annotation = [node.target], node.value, node.annotation
        elif isinstance(node, ast.Assign):
            targets, value, annotation = node.targets, node.value, None
        else:
            continue
        collection = isinstance(
            value,
            (ast.Dict, ast.Set, ast.DictComp, ast.SetComp, ast.ListComp, ast.List),
        )
        if isinstance(value, ast.Call):
            called = value.func
            called_name = (
                called.id if isinstance(called, ast.Name) else getattr(called, "attr", None)
            )
            collection = collection or called_name in _COLLECTION_CALLS
            collection = collection or called_name in returning
        if annotation is not None:
            base = (
                annotation.value if isinstance(annotation, ast.Subscript) else annotation
            )
            annotation_name = (
                base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
            )
            collection = collection or annotation_name in _COLLECTION_ANNOTATIONS
        if not collection:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def collections_formatted_into_messages(source: Path) -> list[str]:
    """Every place a module interpolates a whole collection into an f-string.

    The predicate, and what it cannot see, stated rather than left to be derived. It
    sees a BARE name: ``f"{allocations}"``. It does not see ``f"{obj.allocations}"``,
    ``f"{lookup()}"`` or a collection handed to a helper that formats it — three
    shapes that would be the same defect, and three shapes no simple reading of the
    source can tell from a legitimate one. So this is a floor under the runtime half
    above, not a substitute for it; a check the fixture cannot provoke is covered by
    this and by nothing else, and that is stated where the coverage is reported.
    """
    returning = _returns_a_collection()
    tree = ast.parse(source.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for function in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        names = _collection_names_in(function, returning)
        if not names:
            continue
        for node in ast.walk(function):
            if not isinstance(node, ast.FormattedValue):
                continue
            if isinstance(node.value, ast.Name) and node.value.id in names:
                offenders.append(f"{source.name}:{function.name}: {{{node.value.id}}}")
    return offenders


def test_no_check_formats_a_whole_collection_into_a_finding() -> None:
    """KYO-362.c5, the static half — the shape, refused where the runtime half above
    cannot reach. This is what the round-trip check was doing, verbatim:
    ``f"on disk {on_disk_allocations} vs rebuilt {rebuilt_allocations}"``."""
    doctor_modules = sorted(PACKAGE_SOURCE_DIR.glob("doctor*.py"))
    assert len(doctor_modules) > 1, doctor_modules
    offenders = [
        offender
        for module in doctor_modules
        for offender in collections_formatted_into_messages(module)
    ]
    assert not offenders, (
        f"{offenders} put a whole collection in a message. A finding's length then "
        "grows with the store, which is how one of them came to fill a maintainer's "
        "terminal on every stop (KYO-362). Name the items that differ, and count the "
        "rest — `doctor_exports._allocation_differences` and `currency.some_of` are the two "
        "shapes already here."
    )


def test_the_static_half_sees_the_shape_it_exists_for(tmp_path: Path) -> None:
    """KYO-187's discipline, one record on: a rule asserted against the tree it was
    written for passes on the day it is written whatever it does. This makes the edit
    and re-derives the answer from what is on disk."""
    module = tmp_path / "doctor.py"
    module.write_text(
        "\n".join(
            [
                "def check_something(store) -> list[str]:",
                "    on_disk = read_allocations_in(store)",
                "    rebuilt = {row: 1 for row in store}",
                "    if on_disk != rebuilt:",
                '        return [f"differ: on disk {on_disk} vs rebuilt {rebuilt}"]',
                "    return []",
                "",
                "",
                "def check_a_counted_one(store) -> list[str]:",
                "    differing = [row for row in store]",
                '    return [f"{len(differing)} differ: {differing[0]}"]',
            ]
        ),
        encoding="utf-8",
    )
    offenders = collections_formatted_into_messages(module)
    assert offenders == [
        "doctor.py:check_something: {on_disk}",
        "doctor.py:check_something: {rebuilt}",
    ], offenders


# ------------------------------------------------------------------------- KYO-394
#
# A store scaffolded before KYO-228 kept a kinds.yml without `intake` on its work
# kinds, and `kyo new task` refused with nothing having said so first: no command
# merges kinds.yml. `kind_grants_missing` is the notice that says so, naming the exact
# edit that takes the default's grant — and a project that narrowed a kind on purpose
# declines it with one config line, on `docs_suggest_notice`'s convention.


def _kinds_path(project: Path) -> Path:
    return project / "kyosei" / "kinds.yml"


def _set_kind_line(project: Path, kind: str, field: str, value: str) -> None:
    """Rewrite ``field``'s line inside ``kind``'s block of the project's kinds.yml —
    the hand edit the notice tells a reader to make, done the way a reader does it."""
    lines = _kinds_path(project).read_text(encoding="utf-8").splitlines(keepends=True)
    start = lines.index(f"  {kind}:\n")
    for index in range(start + 1, len(lines)):
        if re.match(rf"^    {field}:", lines[index]):
            lines[index] = f"    {field}: {value}\n"
            break
    else:
        raise AssertionError(f"no {field} line under {kind}")
    _kinds_path(project).write_text("".join(lines), encoding="utf-8")


def _apply_printed_edits(project: Path, finding: str) -> int:
    """Make every edit a finding names, parsed out of the finding itself — so what is
    proved is that the TEXT a reader gets is the remedy, not a helper's idea of it."""
    edits = re.findall(
        r"under `(\w+):` write `(accepts_from|produces): (\[[^\]]*\])`", finding
    )
    for kind, field, value in edits:
        _set_kind_line(project, kind, field, value)
    return len(edits)


def _set_config_key(project: Path, key: str, value: object) -> None:
    import yaml

    path = project / "kyosei" / "config.yml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw[key] = value
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def test_kind_grants_missing_is_a_registered_notice_and_silent_on_a_fresh_store(kyo) -> None:
    assert CHECKS["kind_grants_missing"] is doctor_registry.check_kind_grants_missing
    assert "kind_grants_missing" in NOTICE_CHECKS
    assert tier_of("kind_grants_missing") == TIER_NOTICE
    # `kyo init` copies the default, so a fresh store grants exactly what it grants.
    assert _findings(kyo, "kind_grants_missing") == []


def test_a_pre_kyo228_task_is_a_notice_naming_the_kind_the_entry_and_the_edit(
    kyo, project: Path
) -> None:
    """c1 and c2: a consumer store's shape, the notice, and its remedy clearing it."""
    _set_kind_line(project, "task", "accepts_from", "[idea, bug, investigation, reminder]")
    refused = kyo("new", "task", "--title", "t", "--json")
    assert refused.code != 0, refused  # the refusal the notice exists to foretell
    assert "kind-does-not-accept-intake" in refused.err, refused

    result = kyo("doctor", "--json")
    assert result.code == 0, result  # a notice, not a failure
    findings = result.json["checks"]["kind_grants_missing"]["failures"]
    assert len(findings) == 1, findings
    finding = findings[0]
    assert "'task'" in finding and "'intake'" in finding, finding
    assert "`kyo new task` is refused" in finding, finding
    assert (
        "under `task:` write `accepts_from: [idea, bug, investigation, reminder, intake]`"
    ) in finding
    assert "kind_grants_notice: false" in finding, finding
    assert len(finding) < doctor_module.MAX_FINDING_CHARS, len(finding)
    assert "[kind_grants_missing]" in kyo("doctor").out

    # The printed edit is the remedy, and it clears the notice and the refusal both.
    assert _apply_printed_edits(project, finding) == 1
    assert _findings(kyo, "kind_grants_missing") == []
    assert kyo("new", "task", "--title", "t", "--json").code == 0


def test_every_narrowed_kind_is_named_once_with_only_what_it_lacks(
    kyo, project: Path
) -> None:
    _set_kind_line(project, "task", "accepts_from", "[idea, bug, investigation, reminder]")
    _set_kind_line(project, "chore", "accepts_from", "[]")
    findings = _findings(kyo, "kind_grants_missing")
    assert len(findings) == 2, findings
    chore = next(finding for finding in findings if "'chore'" in finding)
    assert "'intake', 'idea'" in chore, chore
    assert "under `chore:` write `accepts_from: [intake, idea]`" in chore, chore
    assert not any("'bug'" in finding.split(" does not accept")[0] for finding in findings)


def test_a_missing_kind_grant_prints_the_reciprocal_produces_edit_too(
    kyo, project: Path
) -> None:
    """c2's "exact": a pre-KYO-341 store lacks `investigation` on bug.accepts_from AND
    `bug` on investigation.produces, and the loader refuses the first edit without the
    second. The notice prints both, and making both loads and clears it."""
    _set_kind_line(project, "bug", "accepts_from", "[intake, idea, reminder]")
    _set_kind_line(project, "investigation", "produces", "[evidence, decision, task, experiment]")
    finding = _findings(kyo, "kind_grants_missing")
    assert len(finding) == 1, finding
    assert "'investigation'" in finding[0]
    assert "`kyo spawn <investigation-id> --kind bug` is refused" in finding[0]
    assert (
        "under `investigation:` write `produces: [evidence, decision, task, experiment, bug]`"
    ) in finding[0]

    assert _apply_printed_edits(project, finding[0]) == 2
    assert kyo("doctor", "--json").json["checks"]["kind_registry"]["failures"] == []
    assert _findings(kyo, "kind_grants_missing") == []


def test_a_project_can_decline_the_notice_with_one_config_line(kyo, project: Path) -> None:
    """c3: a kind narrowed on purpose is silenced by `kind_grants_notice: false`, and
    stays silenced — the key is read on every run, so the notice does not recur."""
    _set_kind_line(project, "task", "accepts_from", "[idea, bug, investigation, reminder]")
    assert _findings(kyo, "kind_grants_missing") != []

    _set_config_key(project, "kind_grants_notice", False)
    assert config_module.load_config(project).kind_grants_notice is False
    assert _findings(kyo, "kind_grants_missing") == []
    assert _findings(kyo, "kind_grants_missing") == []  # a second session, same answer
    assert kyo("doctor", "--json").json["checks"]["config_consumers"]["failures"] == []

    # `true` is the default answer, stated: the notice is back.
    _set_config_key(project, "kind_grants_notice", True)
    assert _findings(kyo, "kind_grants_missing") != []


def test_the_config_key_is_registered_and_refuses_a_non_boolean(kyo, project: Path) -> None:
    from kyosei.schema import OPTIONAL_CONFIG_KEYS

    assert CONFIG_CONSUMERS["kind_grants_notice"] == ("doctor_registry.check_kind_grants_missing",)
    assert "kind_grants_notice" in OPTIONAL_CONFIG_KEYS

    _set_config_key(project, "kind_grants_notice", "no")
    refused = kyo("doctor")
    assert refused.code != 0, refused
    assert "kind_grants_notice: false" in refused.err, refused
    # The remedy it names clears it.
    _set_config_key(project, "kind_grants_notice", False)
    assert kyo("doctor").code == 0


def test_kinds_only_one_side_defines_are_not_compared(kyo, project: Path) -> None:
    """A grant naming a kind this project does not define could not be written (the
    loader would refuse it), and a project's own kind has no default to lag."""
    text = _kinds_path(project).read_text(encoding="utf-8")
    text = text.replace(
        "kinds:\n",
        "kinds:\n  spike:\n    archetype: work\n    summary: Ours alone.\n"
        "    accepts_from: []\n    produces: []\n    policy:\n"
        "      requires_criteria: true\n      dispatchable: true\n"
        "      unattended_ok: true\n      write_scope: code\n"
        "      swept: true\n      counted: true\n\n",
        1,
    )
    _kinds_path(project).write_text(text, encoding="utf-8")
    assert kyo("doctor", "--json").json["checks"]["kind_registry"]["failures"] == []
    assert _findings(kyo, "kind_grants_missing") == []


def test_in_the_package_source_repo_the_source_default_is_the_comparison(
    kyo, project: Path
) -> None:
    """KYO-309's direction rule: where the checkout carries src/kyosei/defaults/kinds.yml
    that file is the default, not the installed build's copy of it."""
    source = project / scaffold_module.PACKAGED_ASSET_SOURCE_DIR / "kinds.yml"
    source.parent.mkdir(parents=True, exist_ok=True)
    packaged = _kinds_path(project).read_text(encoding="utf-8")
    source.write_text(packaged, encoding="utf-8")
    _set_kind_line(project, "task", "accepts_from", "[idea, bug, investigation, reminder]")
    # Now make the SOURCE default agree with the narrowed project: no notice.
    source.write_text(_kinds_path(project).read_text(encoding="utf-8"), encoding="utf-8")
    assert _findings(kyo, "kind_grants_missing") == []


# KYO-438.c1: the doctor split (KYO-433) left doctor_exports reaching into doctor_consumers'
# private cache helpers. A name one doctor module shares with another is public, and this
# holds it: no doctor_* module imports an underscore-prefixed name from another.
def test_no_doctor_module_imports_another_doctor_modules_private_name():
    package = Path(doctor_module.__file__).parent
    reaches = []
    for path in sorted(package.glob("doctor*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith(
                "doctor"
            ):
                continue
            reaches += [
                f"{path.name}: {node.module}.{alias.name}"
                for alias in node.names
                if alias.name.startswith("_")
            ]
    assert reaches == [], reaches


# KYO-438.c2: the bound every check's findings are held to lives with the registry.
def test_max_finding_chars_lives_in_the_registry_module():
    assert doctor_module.MAX_FINDING_CHARS == 700
    for module in (doctor_consumers, doctor_exports, doctor_registry):
        assert not hasattr(module, "MAX_FINDING_CHARS"), module.__name__
