"""KYO-408: what `kyo doctor --hook` costs on every stop, and what it still catches.

The ``Stop`` hook runs `kyo sweep && kyo doctor --hook` after every assistant turn. Before
this record the hook form rebuilt the WHOLE store from export into a temporary database
on every run (2.6 s, ~850 MB resident on this repository's own store) and re-parsed the
package's source. Each test below either proves a saving is real — by counting the
expensive call — or proves that a saving never costs a finding the full check makes.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from kyosei import cmd_store as cmd_store_module
from kyosei import doctor as doctor_module
from kyosei import doctor_consumers, doctor_exports
from kyosei import ledgers as ledgers_module
from kyosei import rebuild as rebuild_module
from kyosei.errors import EXIT_DOCTOR

from .conftest import export_of, new_record

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def package_snapshot(tmp_path: Path, monkeypatch) -> Path:
    """KYO-601: the round-trip key reads a private copy of the package's top-level files.

    The key hashes the size and mtime of every file directly in the package directory,
    so that a changed build forces a full pass. In the whole suite other tests rewrite
    files there -- test_kyo226's real wheel build and its ``buildinfo.regenerate`` both
    rewrite ``_build_stamp.json`` and ``_changelog.json`` in this checkout -- and a rewrite
    landing between a test's two hook passes turned the cheaper mode a test expected into
    ``full``. The copy keeps names, sizes and mtimes, so the key is computed as it is in a
    real install, over a directory nothing else in the suite writes to.
    """
    snapshot = tmp_path / "package-snapshot"
    snapshot.mkdir()
    for path in doctor_consumers.PACKAGE_SOURCE_DIR.iterdir():
        try:
            if path.is_file():
                shutil.copy2(path, snapshot / path.name)
        except FileNotFoundError:  # replaced mid-copy by a concurrent writer
            continue
    monkeypatch.setattr(doctor_exports, "PACKAGE_SOURCE_DIR", snapshot)
    return snapshot


@pytest.fixture
def rebuilds(monkeypatch) -> list[dict]:
    """Every `rebuild_from_export` doctor makes, with the ledger flag it passed."""
    calls: list[dict] = []
    real = rebuild_module.rebuild_from_export

    def counting(*args, **kwargs):
        calls.append({"load_ledgers": kwargs.get("load_ledgers", True)})
        return real(*args, **kwargs)

    monkeypatch.setattr(doctor_exports, "rebuild_from_export", counting)
    return calls


def _hook(kyo) -> tuple[object, dict]:
    result = kyo("doctor", "--hook", "--json")
    return result, result.json[cmd_store_module.DOCTOR_HOOK_FORMS_KEY]["export_roundtrip"]


def _shift_note(kyo, write_file, text: str) -> None:
    """A mutation that appends to ledgers and moves the counters, and changes no record
    document — the shape of the `kyo sweep` every stop runs."""
    result = kyo("note", "--shift", "--file", write_file("shift.md", text), "--json")
    assert result.code == 0, result


def _export(project: Path) -> Path:
    return project / "kyosei" / "export"


def _roundtrip_failures(result) -> list[str]:
    return result.json["checks"]["export_roundtrip"]["failures"]


# ------------------------------------------------------------------ c2: the saving


def test_a_second_hook_run_over_an_unchanged_export_does_not_rebuild(
    kyo, rebuilds
) -> None:
    new_record(kyo, title="alpha", criteria=["a"])
    first, how = _hook(kyo)
    assert first.code == 0, first
    assert how["mode"] == doctor_exports.ROUNDTRIP_FULL, how
    assert len(rebuilds) == 1

    second, how = _hook(kyo)
    assert second.code == 0, second
    assert how == {"mode": doctor_exports.ROUNDTRIP_CACHED}
    assert len(rebuilds) == 1, "an unchanged export was rebuilt again"


def test_ledger_appends_are_verified_without_any_rebuild(
    kyo, rebuilds, write_file
) -> None:
    new_record(kyo, title="alpha", criteria=["a"])
    assert _hook(kyo)[0].code == 0
    _shift_note(kyo, write_file, "one")
    _shift_note(kyo, write_file, "two")

    result, how = _hook(kyo)
    assert result.code == 0, result
    assert how == {"mode": doctor_exports.ROUNDTRIP_APPENDS}
    assert len(rebuilds) == 1, "appended ledger lines cost a rebuild"


def test_a_moved_record_document_rebuilds_the_record_half_only(kyo, rebuilds) -> None:
    record = new_record(kyo, title="alpha", criteria=["a"])
    assert _hook(kyo)[0].code == 0
    assert kyo("edit", record, "--title", "beta").code == 0

    result, how = _hook(kyo)
    assert result.code == 0, result
    assert how == {"mode": doctor_exports.ROUNDTRIP_DOCUMENTS}
    assert rebuilds[1:] == [{"load_ledgers": False}], rebuilds


# ----------------------------------------------------- c3: nothing the full check sees


def test_a_hand_edited_document_after_a_passing_hook_fails_the_next_hook(
    kyo, project: Path, rebuilds
) -> None:
    record = new_record(kyo, title="alpha", criteria=["a"])
    assert _hook(kyo)[0].code == 0
    path = export_of(project, record)
    # Same content, different bytes: survives a load, cannot survive a re-export.
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8"))), encoding="utf-8")

    hooked, how = _hook(kyo)
    assert hooked.code == EXIT_DOCTOR, hooked
    assert how["mode"] == doctor_exports.ROUNDTRIP_DOCUMENTS
    assert len(rebuilds) == 2, "the hook did not re-run the round-trip"
    assert any(record in line for line in _roundtrip_failures(hooked))

    bare = kyo("doctor", "--json")
    assert _roundtrip_failures(hooked) == _roundtrip_failures(bare)
    assert hooked.json["problems"] == bare.json["problems"]


def test_an_edit_inside_the_verified_ledger_falls_back_to_the_full_rebuild(
    kyo, project: Path, write_file
) -> None:
    new_record(kyo, title="alpha", criteria=["a"])
    _shift_note(kyo, write_file, "one")
    assert _hook(kyo)[0].code == 0
    ledger = _export(project) / ledgers_module.SHIFT_NOTES_FILENAME
    lines = ledger.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[0] = "{not json\n"
    ledger.write_text("".join(lines), encoding="utf-8")
    _shift_note(kyo, write_file, "two")  # and then append, so the file also grew

    hooked, how = _hook(kyo)
    assert hooked.code == EXIT_DOCTOR, hooked
    assert how["mode"] == doctor_exports.ROUNDTRIP_FULL
    assert "before its last verified line" in how["why"], how
    bare = kyo("doctor", "--json")
    assert _roundtrip_failures(hooked) == _roundtrip_failures(bare)
    assert _roundtrip_failures(hooked), "the fixture broke nothing the full check sees"


def test_an_appended_duplicate_ledger_line_is_caught(kyo, project: Path, write_file) -> None:
    """Appending the last line again is a pure append — and a duplicate id a rebuild
    refuses. The tier must not vouch for it."""
    new_record(kyo, title="alpha", criteria=["a"])
    _shift_note(kyo, write_file, "one")
    assert _hook(kyo)[0].code == 0
    ledger = _export(project) / ledgers_module.SHIFT_NOTES_FILENAME
    last = ledger.read_text(encoding="utf-8").splitlines(keepends=True)[-1]
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(last)

    hooked, how = _hook(kyo)
    assert how["mode"] == doctor_exports.ROUNDTRIP_FULL, how
    assert hooked.code == EXIT_DOCTOR, hooked
    assert _roundtrip_failures(hooked) == _roundtrip_failures(kyo("doctor", "--json"))


def test_a_hand_edited_counter_is_caught_by_the_appends_tier(kyo, project: Path) -> None:
    new_record(kyo, title="alpha", criteria=["a"])
    assert _hook(kyo)[0].code == 0
    path = ledgers_module.allocations_path_in(_export(project))
    counters = json.loads(path.read_text(encoding="utf-8"))
    counters["event"] = str(counters["event"])  # "7" survives JSON, not the load
    path.write_text(json.dumps(counters, indent=2) + "\n", encoding="utf-8")

    hooked, how = _hook(kyo)
    assert hooked.code == EXIT_DOCTOR, hooked
    assert how["mode"] == doctor_exports.ROUNDTRIP_FULL, how
    assert any("allocations differ" in line for line in _roundtrip_failures(hooked))


def test_a_changed_declaration_rebuilds_in_full(kyo, project: Path, rebuilds) -> None:
    new_record(kyo, title="alpha", criteria=["a"])
    assert _hook(kyo)[0].code == 0
    kinds = project / "kyosei" / "kinds.yml"
    kinds.write_text(kinds.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    result, how = _hook(kyo)
    assert how["mode"] == doctor_exports.ROUNDTRIP_FULL
    assert "*.yml" in how["why"], how
    assert rebuilds[-1] == {"load_ledgers": True}


def test_a_changed_build_rebuilds_in_full(kyo, rebuilds, package_snapshot: Path) -> None:
    """KYO-601.c1, and the guarantee the snapshot must not weaken: a package file whose
    mtime moves between two hook passes -- what a rebuild of the wheel does to
    ``_build_stamp.json`` -- turns the documents-only rebuild this edit would earn into a
    full one. It also fails if the key stops reading the directory the fixture provides,
    since touching the snapshot would then change nothing."""
    record = new_record(kyo, title="alpha", criteria=["a"])
    assert _hook(kyo)[0].code == 0
    assert kyo("edit", record, "--title", "beta").code == 0
    stamp = package_snapshot / "_build_stamp.json"
    stamp.write_bytes(stamp.read_bytes() if stamp.exists() else b"{}\n")
    before = stamp.stat()
    os.utime(stamp, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))

    result, how = _hook(kyo)
    assert result.code == 0, result
    assert how["mode"] == doctor_exports.ROUNDTRIP_FULL, how
    assert "the build" in how["why"], how
    assert rebuilds[-1] == {"load_ledgers": True}


def test_the_round_trip_key_never_reads_the_checkout_s_package_directory(
    tmp_path: Path,
) -> None:
    """KYO-601.c2: the guarantee every mode assertion in this file depends on, checked
    without naming the fixture, so it fails if the fixture stops being autouse as well as
    if it is removed. Without it the key reads ``src/kyosei/`` itself, which other tests
    in the suite rewrite, and the flake returns."""
    keyed = doctor_exports.PACKAGE_SOURCE_DIR.resolve()
    assert keyed != doctor_consumers.PACKAGE_SOURCE_DIR.resolve()
    assert not keyed.is_relative_to(ROOT)
    assert keyed.is_relative_to(tmp_path.resolve())


def test_an_unreadable_baseline_rebuilds_rather_than_crashing(kyo, project: Path) -> None:
    new_record(kyo, title="alpha", criteria=["a"])
    assert _hook(kyo)[0].code == 0
    cache = project / "kyosei" / doctor_consumers.DOCTOR_CACHE_DIRNAME
    baseline = cache / doctor_consumers.ROUNDTRIP_CACHE_NAME
    data = json.loads(baseline.read_text(encoding="utf-8"))
    data["files"] = dict.fromkeys(data["files"], "garbage")
    baseline.write_text(json.dumps(data), encoding="utf-8")
    os.utime(export_of(project, "TST-1"), ns=(1, 1))  # force past the cached tier

    result, how = _hook(kyo)
    assert result.code == 0, result
    assert how["mode"] == doctor_exports.ROUNDTRIP_FULL
    assert "unreadable" in how["why"], how


def test_a_failing_round_trip_records_no_baseline(kyo, project: Path, rebuilds) -> None:
    """Only a pass is cached: a failing store pays the full check on every stop until it
    passes, so a finding can never be served from a cache."""
    record = new_record(kyo, title="alpha", criteria=["a"])
    path = export_of(project, record)
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8"))), encoding="utf-8")
    assert _hook(kyo)[0].code == EXIT_DOCTOR
    assert _hook(kyo)[0].code == EXIT_DOCTOR
    assert rebuilds == [{"load_ledgers": True}] * 2


# --------------------------------------------------------- c4: bare doctor is unchanged


def test_bare_doctor_rebuilds_the_whole_store_every_time(kyo, rebuilds) -> None:
    new_record(kyo, title="alpha", criteria=["a"])
    assert kyo("doctor").code == 0
    assert kyo("doctor").code == 0
    assert rebuilds == [{"load_ledgers": True}] * 2
    assert cmd_store_module.DOCTOR_HOOK_FORMS_KEY not in kyo("doctor", "--json").json


# ---------------------------------------------- c5's counterpart: nothing is skipped


def test_every_check_still_runs_in_the_hook_form_and_the_form_is_named(kyo) -> None:
    """No check is skipped, so there is no skipped list to name (c5); what the envelope
    names instead is how each check with a hook form was satisfied."""
    new_record(kyo, title="alpha", criteria=["a"])
    result = kyo("doctor", "--hook", "--json")
    assert set(result.json["checks"]) == set(doctor_module.CHECK_ORDER)
    forms = result.json[cmd_store_module.DOCTOR_HOOK_FORMS_KEY]
    assert set(forms) == set(doctor_module.HOOK_FORMS)
    assert set(doctor_module.HOOK_FORMS) <= set(doctor_module.CHECKS)


def test_every_ledger_rebuild_reads_has_a_table_the_tier_extends() -> None:
    """A ledger added to the reader registry without a table here would silently make
    every hook run a full rebuild (`_ledger_keys` answers None)."""
    assert set(doctor_exports.LEDGER_TABLES) == set(ledgers_module.LEDGER_READERS)


# ------------------------------------------------------------ the cache is local state


def test_the_cache_leaves_the_store_clean_in_git(git_project: Path, kyo) -> None:
    new_record(kyo, title="alpha", criteria=["a"])
    subprocess.run(["git", "add", "-A"], cwd=git_project, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "s"],
        cwd=git_project,
        check=True,
    )
    doctor_consumers._MODULE_SYMBOL_MEMO.clear()  # as in the fresh process a hook is
    assert kyo("doctor", "--hook").code == 0
    cache = git_project / "kyosei" / doctor_consumers.DOCTOR_CACHE_DIRNAME
    assert (cache / doctor_consumers.ROUNDTRIP_CACHE_NAME).is_file()
    assert (cache / doctor_consumers.MODULE_INDEX_CACHE_NAME).is_file()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=git_project,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert doctor_consumers.DOCTOR_CACHE_DIRNAME not in status, status


# ----------------------------------------------------------------- c6: module_index


@pytest.fixture
def parses(monkeypatch) -> list[int]:
    calls: list[int] = []
    real = ast.parse

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(doctor_consumers.ast, "parse", counting)
    return calls


@pytest.fixture
def source_copy(tmp_path: Path) -> Path:
    copy = tmp_path / "src-copy"
    copy.mkdir()
    for path in doctor_consumers.PACKAGE_SOURCE_DIR.glob("*.py"):
        (copy / path.name).write_bytes(path.read_bytes())
    return copy


def test_module_index_is_not_reparsed_by_a_new_process(
    tmp_path: Path, source_copy: Path, parses
) -> None:
    """The ``Stop`` hook is a new process every time, so the in-process memo is cleared
    between runs here: what is left to answer the second run is the disk cache."""
    cache = tmp_path / "cache"
    modules = len(list(source_copy.glob("*.py")))
    first = doctor_consumers.module_index(source_copy, cache)
    assert len(parses) == modules
    doctor_consumers._MODULE_SYMBOL_MEMO.clear()
    second = doctor_consumers.module_index(source_copy, cache)
    assert len(parses) == modules, "an unchanged source tree was parsed again"
    assert second == first


def test_a_touched_source_file_is_reparsed(
    tmp_path: Path, source_copy: Path, parses
) -> None:
    cache = tmp_path / "cache"
    modules = len(list(source_copy.glob("*.py")))
    doctor_consumers.module_index(source_copy, cache)
    touched = source_copy / "ids.py"
    touched.write_text(
        touched.read_text(encoding="utf-8") + "\n\ndef kyo408_added():\n    pass\n",
        encoding="utf-8",
    )
    doctor_consumers._MODULE_SYMBOL_MEMO.clear()
    index = doctor_consumers.module_index(source_copy, cache)
    assert len(parses) == modules + 1, "exactly the touched file is parsed again"
    assert "kyo408_added" in index["ids"][0]
    # ...and the in-process memo sees the edit too, without a new process.
    touched.write_text(
        touched.read_text(encoding="utf-8") + "\n\ndef kyo408_again():\n    pass\n",
        encoding="utf-8",
    )
    assert "kyo408_again" in doctor_consumers.module_index(source_copy, cache)["ids"][0]
    assert len(parses) == modules + 2


def test_doctor_passes_its_cache_to_module_index(kyo, project: Path, parses) -> None:
    doctor_consumers._MODULE_SYMBOL_MEMO.clear()
    assert kyo("doctor", "--hook").code == 0
    assert parses, "the first run parses the source"
    doctor_consumers._MODULE_SYMBOL_MEMO.clear()
    before = len(parses)
    assert kyo("doctor", "--hook").code == 0
    assert len(parses) == before, "the second run re-parsed an unchanged package"


# -------------------------------------------------------------------- c8: the prose


def test_the_readme_and_the_stop_hook_say_what_the_hook_form_runs() -> None:
    from tests.reference_pages import OPERATING

    readme = OPERATING.read_text(encoding="utf-8")  # the README section, moved (KYO-516)
    entry = readme[readme.index("**Close out with hooks, not a checklist**") :]
    entry = entry[: entry.index("\n7. ")]
    for mode in (
        doctor_exports.ROUNDTRIP_CACHED,
        doctor_exports.ROUNDTRIP_APPENDS,
        doctor_exports.ROUNDTRIP_DOCUMENTS,
        doctor_exports.ROUNDTRIP_FULL,
    ):
        assert mode in entry, mode
    assert cmd_store_module.DOCTOR_HOOK_FORMS_KEY in entry
    stop = json.loads(
        (doctor_consumers.PACKAGE_SOURCE_DIR / "defaults" / "hooks" / "stop.json").read_text(
            encoding="utf-8"
        )
    )
    comment = stop["Stop"][0]["hooks"][0]["comment"]
    assert "KYO-408" in comment and "bare `kyo doctor`" in comment
    assert "the same checks run" not in comment.lower() or "export_roundtrip" in comment

