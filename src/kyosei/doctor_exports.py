"""Doctor checks that the database and ``kyosei/export/`` agree: currency, byte-for-byte
exports, the rebuild round-trip and its hook form, allocations, id prefixes, orphans.
Registered in :data:`kyosei.doctor.CHECKS`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sqlite3
import tempfile
from pathlib import Path

from . import currency, events_ledger, export, ids
from . import ledgers as ledgers_module
from . import rebuild as rebuild_module
from .doctor_consumers import (
    PACKAGE_SOURCE_DIR,
    ROUNDTRIP_CACHE_NAME,
    doctor_cache_dir,
    read_cache,
    write_cache,
)
from .errors import KyoseiError
from .rebuild import rebuild_from_export
from .schema import DDL, SCHEMA_VERSION
from .store import (
    SCOPE_BRIEF,
    SCOPE_EVENT,
    SCOPE_LINK,
    SCOPE_RECORD,
    SCOPE_SHIFT_NOTE,
    SCOPE_SWEEP,
    Store,
    connect,
)

#: How much of a quoted line one finding shows. A finding that quotes an export
#: document quotes something a consumer wrote, so its length is theirs and not this
#: tool's: a 4,000-character description field is a legitimate record and was one
#: unbounded finding away from being the next screenful.
QUOTED_LINE_CHARS = 120


def _clipped(line: str, limit: int = QUOTED_LINE_CHARS) -> str:
    """One quoted line of a file, shortened to something a message can carry."""
    text = line.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _first_difference(*, on_disk: str, derived: str) -> str:
    """Name the first line where a file and its derivation disagree.

    Keyword-only, and that is the whole point (KYO-23). These two arguments are
    interchangeable strings whose meaning comes entirely from which is which, and a
    positional call site that swaps them reports the right difference in the wrong
    direction — the worst shape a diagnostic can take
    (docs/design/doctor.md#kyo-23).
    """
    on_disk_lines, derived_lines = on_disk.split("\n"), derived.split("\n")
    for number, (got, want) in enumerate(zip(on_disk_lines, derived_lines, strict=False), start=1):
        if got != want:
            return (
                f"line {number}: on disk {_clipped(got)!r}, derived {_clipped(want)!r}"
            )
    return f"length: {len(on_disk_lines)} lines on disk, {len(derived_lines)} derived"


def _schema_version_only_diff(on_disk: str, derived: str) -> bool:
    """KYO-216: does ``on_disk`` differ from ``derived`` in nothing but the top-level
    ``schema_version`` stamp?

    A format-version bump changes what every export document *says about itself*
    without changing anything else it says (``kyo rebuild`` stamps the running
    build's ``SCHEMA_VERSION`` into whatever it reconstructs; a live re-export
    stamps the same). That is one fact about the whole store, not a per-document
    drift, and this is the predicate that lets the two checks below report it once.

    Parses both sides as JSON rather than comparing lines, because the stamp's own
    line moves if a bump also reorders or adds a field — the case this predicate
    must say "no" to, since a document that changed in ANY other way is still its
    own finding. ``json.loads`` failing (a hand-corrupted file, `check_exports`'s
    own territory) answers ``False`` rather than raising: a file this broken gets
    its ordinary per-document finding, not a crash inside a doctor check.
    """
    try:
        on_disk_doc = json.loads(on_disk)
        derived_doc = json.loads(derived)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(on_disk_doc, dict) or not isinstance(derived_doc, dict):
        return False
    version_key = export.DOCUMENT_VERSION_KEY
    if on_disk_doc.get(version_key) == derived_doc.get(version_key):
        # Not a version difference at all — some other field is what differs, and
        # this predicate leaves it to the caller's ordinary per-document finding.
        return False
    on_disk_doc.pop(version_key, None)
    derived_doc.pop(version_key, None)
    return on_disk_doc == derived_doc


def _schema_version_stamp_finding(
    record_ids: list[str], *, remedy: str, roundtrip: bool = False
) -> str:
    """KYO-216.c3: one finding for every document a format-version bump left behind,
    naming the count and the remedy — not one finding per document repeating the
    same fact (docs/design/doctor.md#kyo-216).
    """
    listed = ", ".join(sorted(record_ids)[:3])
    if len(record_ids) > 3:
        listed += f", and {len(record_ids) - 3} more"
    prefix = "rebuild round-trip: " if roundtrip else ""
    return (
        f"{prefix}{len(record_ids)} export document(s) ({listed}) differ from "
        + ("a fresh rebuild" if roundtrip else "the database")
        + " only by their schema_version stamp — a format-version bump left every "
        f"one of them behind; this is one fact, not {len(record_ids)} separate "
        f"drifts. {remedy}"
    )


def check_db_export_currency(store: Store) -> list[str]:
    """KYO-74.c2: which side of the db/export seam moved — or that it cannot be told.

    Its own check rather than a clause inside ``check_exports`` because the two ask
    different questions. ``check_exports`` asks "does every export document match its
    record", which is silent when the seam has moved in a way that changes no
    document — a database ahead only on the event counter, say. This one asks
    "are these two artifacts the same generation of the same store", and answers with
    one of three states, never with a guess (:mod:`kyosei.currency`).
    """
    result = currency.db_export_currency(store)
    if result.in_step:
        return []
    return [f"{result.sentence()} — {result.remedy()}"]


def check_exports(store: Store) -> list[str]:
    """Re-derive every export from the database and fail on any mismatch.

    KYO-74: what a mismatch *means* depends on which side moved. "The database is
    authoritative" and "stale export" are stated only when the database is the newer
    side: a consumer who acts on either when the export is ahead regenerates it from a
    database that is behind and destroys the pulled work
    (docs/design/doctor.md#kyo-74).
    """
    failures: list[str] = []
    seam = currency.db_export_currency(store)
    expected = export.expected_exports(store)
    stamp_only: list[str] = []
    for record_id, content in expected.items():
        path = export.export_path(store, record_id)
        if not path.is_file():
            failures.append(f"export missing for {record_id} (expected {path})")
            continue
        actual = path.read_text(encoding="utf-8")
        if actual != content:
            if _schema_version_only_diff(actual, content):
                # KYO-216.c3: collected rather than reported here — one line below,
                # once, for the whole set (see `_schema_version_stamp_finding`).
                stamp_only.append(record_id)
                continue
            failures.append(
                f"export for {record_id} does not match the database at "
                f"{_first_difference(on_disk=actual, derived=content)}; "
                f"{_mismatch_direction(seam)}"
            )
    if stamp_only:
        # The remedy this collapsed finding names follows the same direction logic
        # as an ordinary mismatch: normally the database is current and
        # `kyo export --refresh` is right, but an export that is itself the newer
        # side must not be told to overwrite itself with a stale database.
        remedy = (
            _mismatch_direction(seam)
            if seam.direction == currency.CURRENCY_EXPORT_AHEAD
            else "Run `kyo export --refresh` to rewrite them from the database and "
            "clear this (KYO-216)."
        )
        failures.append(_schema_version_stamp_finding(stamp_only, remedy=remedy))
    known = {export.export_path(store, rid) for rid in expected}
    for path in export.existing_export_files(store):
        if path not in known:
            failures.append(f"{path} has no record in the database ({_orphan_export(seam)})")
    return failures


#: Where the full comparison is stated. Both helpers below point at it rather than
#: restating it per record: `check_db_export_currency` runs first and says it once,
#: and a store with fifty drifted documents should not carry fifty copies of the
#: same sentence about the same seam.
_SEE_CURRENCY = "see the [db_export_currency] finding above"


def _mismatch_direction(seam: currency.Currency) -> str:
    """What an export document that differs from its record means, given the seam."""
    if seam.direction == currency.CURRENCY_EXPORT_AHEAD:
        return (
            f"the EXPORT is the newer side here ({_SEE_CURRENCY}), so the on-disk text is "
            "current and the derived text is stale. Do NOT regenerate exports from the "
            f"database — {seam.remedy()}"
        )
    if seam.direction == currency.CURRENCY_UNDECIDABLE:
        return f"which side is current cannot be determined ({_SEE_CURRENCY})"
    return "the database is authoritative — exports are generated, never hand-edited"


def _orphan_export(seam: currency.Currency) -> str:
    """What an export file with no record behind it means, given the seam."""
    if seam.direction == currency.CURRENCY_EXPORT_AHEAD:
        return (
            "NOT a stale export — the database is behind its export and has not loaded "
            f"this record yet; {_SEE_CURRENCY}"
        )
    if seam.direction == currency.CURRENCY_UNDECIDABLE:
        return (
            "stale export, or a record the database has not loaded — which cannot be "
            f"determined; {_SEE_CURRENCY}"
        )
    return "stale export"


#: KYO-362: how many differing counters one allocations finding names before it stops
#: and says how many are left. Ten, on :func:`_schema_version_stamp_finding`'s
#: precedent directly above (three, there, because those entries are record ids a
#: reader recognises; ten here, because a counter name plus its two values is one
#: short clause and the reader is usually looking for a pattern across several).
ALLOCATION_DIFF_LIMIT = 10


def _allocation_differences(on_disk: dict[str, int], rebuilt: dict[str, int]) -> str:
    """The counters two allocation maps disagree about — and nothing else.

    KYO-362: a whole map in a finding grows with the store and is read on every
    ``Stop`` (docs/design/doctor.md#kyo-362). This is :func:`_first_difference`'s
    treatment, for the allocations half of the round-trip.

    A counter missing from one side is a difference too, and is named ``absent``
    rather than silently compared against a zero it does not have — the two are not
    the same fault and the reader has to be able to tell them apart.
    """
    scopes = sorted(set(on_disk) | set(rebuilt))
    differing = [
        scope for scope in scopes if on_disk.get(scope) != rebuilt.get(scope)
    ]

    def value(side: dict[str, int], scope: str) -> str:
        return "absent" if scope not in side else str(side[scope])

    listed = "; ".join(
        f"{scope}: on disk {value(on_disk, scope)}, rebuilt {value(rebuilt, scope)}"
        for scope in differing[:ALLOCATION_DIFF_LIMIT]
    )
    if len(differing) > ALLOCATION_DIFF_LIMIT:
        listed += f"; and {len(differing) - ALLOCATION_DIFF_LIMIT} more differing"
    return (
        f"{len(differing)} counter(s) differ ({len(scopes) - len(differing)} agree) — "
        f"{listed}"
    )


def check_export_roundtrip(store: Store) -> list[str]:
    """Rebuilding from the live export and re-exporting reproduces it byte-for-byte.

    Charter, Layer 2: "Once ``kyo rebuild`` reconstructs a byte-identical store from
    export and ``kyo doctor`` verifies the round-trip in both directions, ``kyosei.db``
    is gitignored." This checks the export -> db -> export direction specifically
    (``check_exports`` already checks db -> export on the live database), by
    comparing what is on disk *now* against what a fresh rebuild, re-exported, would
    produce — deliberately not against the live db's own documents, so this check
    stays meaningful even when the live db and its export have already diverged
    (``check_exports`` names that failure; this one must not also crash on it).

    KYO-408: this, the form bare `kyo doctor` runs, always rebuilds the whole store.
    A pass also records what it verified (:func:`_full_roundtrip`), which is
    what lets :func:`check_export_roundtrip_hook` avoid doing it again.
    """
    export_directory = export.export_dir(store)
    record_files = export.record_export_files_in(export_directory)
    if not export_directory.is_dir() or not record_files:
        return []  # nothing exported yet (e.g. a brand-new project); nothing to check
    return _full_roundtrip(store, export_directory, record_files)


def _rebuild_and_compare(
    store: Store,
    export_directory: Path,
    record_files: list[Path],
    *,
    load_ledgers: bool,
) -> tuple[list[str], dict[str, dict[str, int]] | None]:
    """Rebuild into a temporary directory and compare; ``(findings, ledger keys)``.

    The ledger keys -- the highest seq each ledger table holds after the rebuild
    (:func:`_ledger_keys`) -- are returned only for a whole rebuild that passed, because
    they are what the hook form's incremental tier checks an appended line against.
    """
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="kyosei-doctor-rebuild-") as tmp:
        tmp_config = dataclasses.replace(store.config, root=Path(tmp))
        try:
            rebuild_from_export(
                export_directory, tmp_config, force=False, load_ledgers=load_ledgers
            )
        except Exception as exc:  # noqa: BLE001 - report, don't crash doctor
            return [f"rebuild round-trip failed: {exc}"], None

        tmp_conn = connect(tmp_config.db_path)
        tmp_store = Store(tmp_config, tmp_conn)
        try:
            stamp_only: list[str] = []
            for path in record_files:
                record_id = path.stem
                on_disk = path.read_text(encoding="utf-8")
                try:
                    rebuilt = export.serialise(export.record_document(tmp_store, record_id))
                except Exception as exc:  # noqa: BLE001 - report, don't crash doctor
                    failures.append(f"rebuild round-trip: {record_id}: {exc}")
                    continue
                if on_disk != rebuilt:
                    if _schema_version_only_diff(on_disk, rebuilt):
                        # KYO-216.c3: rebuild always stamps the running build's
                        # SCHEMA_VERSION, so an export written at an older one
                        # differs here on every single record the same one way —
                        # collected and reported once below, not once each.
                        stamp_only.append(record_id)
                        continue
                    failures.append(
                        f"rebuild round-trip: {record_id} differs at "
                        f"{_first_difference(on_disk=on_disk, derived=rebuilt)}"
                    )
            if stamp_only:
                # Unlike `check_exports`, this comparison is export -> db -> export:
                # `rebuild_from_export` above already refused an export ahead of this
                # build (`ExportSchemaAhead`), so every document that reaches this
                # loop is at or below SCHEMA_VERSION and the remedy is unconditional.
                failures.append(
                    _schema_version_stamp_finding(
                        stamp_only,
                        remedy="Run `kyo export --refresh` to rewrite them from the "
                        "database and clear this (KYO-216).",
                        roundtrip=True,
                    )
                )
            on_disk_allocations = ledgers_module.read_allocations_in(export_directory)
            rebuilt_allocations = ledgers_module.expected_allocations(tmp_store)
            if on_disk_allocations != rebuilt_allocations:
                failures.append(
                    "rebuild round-trip: allocations differ — "
                    + _allocation_differences(on_disk_allocations, rebuilt_allocations)
                )
            keys = None
            if load_ledgers and not failures:
                keys = _ledger_keys(tmp_conn)
        finally:
            tmp_store.close()
    return failures, keys


# ------------------------------------------------ KYO-408: the hook form's round-trip

#: The table each append-only ledger loads into. The incremental tier below extends a
#: ledger only if it is named here; a ledger added to ``ledgers.LEDGER_READERS`` without
#: an entry makes :func:`_ledger_keys` answer ``None``, which records no baseline, which
#: makes every hook run rebuild in full — slow, never wrong
#: (``tests/test_doctor_hook_cost.py`` holds the two registries together).
LEDGER_TABLES = {
    events_ledger.EVENTS_FILENAME: "events",
    ledgers_module.SWEEPS_FILENAME: "sweep_runs",
    ledgers_module.BRIEFS_FILENAME: "brief_runs",
    ledgers_module.SHIFT_NOTES_FILENAME: "shift_notes",
}

#: How the hook form satisfied ``export_roundtrip``, reported under ``hook_forms`` in
#: ``kyo doctor --hook --json``.
ROUNDTRIP_CACHED = "cached"
ROUNDTRIP_APPENDS = "appends-verified"
ROUNDTRIP_DOCUMENTS = "documents-rebuilt"
ROUNDTRIP_FULL = "full"


#: The shape every ledger id has had in every store measured
#: (docs/design/doctor.md#kyo-408): the project prefix, a letter, and the row's own
#: ``seq`` -- ``KYO-E13656`` is seq 13656. It is what lets a baseline be one
#: number per ledger rather than every id ever written: a line appended with a seq above
#: every verified seq cannot reuse a verified id, because its id ends in a number none
#: of theirs does.
LEDGER_ID_RE = re.compile(r"^.+-[A-Z]+(\d+)$")


def _ledger_keys(conn: sqlite3.Connection) -> dict[str, dict[str, int]] | None:
    """The highest seq in each ledger table — or ``None`` when no baseline can be kept.

    ``None`` when a registered ledger has no table here, or when any row's id does not
    carry its own seq (:data:`LEDGER_ID_RE`): then a new line's id could collide with an
    old one that a seq comparison cannot see, and the hook form rebuilds in full.
    """
    if set(ledgers_module.LEDGER_READERS) - set(LEDGER_TABLES):
        return None
    keys: dict[str, dict[str, int]] = {}
    for ledger, table in LEDGER_TABLES.items():
        highest = 0
        for row_id, row_seq in conn.execute(f"SELECT id, seq FROM {table}"):
            match = LEDGER_ID_RE.match(str(row_id))
            if match is None or int(match.group(1)) != row_seq:
                return None
            highest = max(highest, row_seq)
        keys[ledger] = {"max_seq": highest}
    return keys


def _ledger_of(rel: str) -> str | None:
    """The ledger an export-relative path belongs to, or ``None`` if it is not a ledger."""
    if rel == events_ledger.EVENTS_FILENAME:
        return events_ledger.EVENTS_FILENAME
    head, _, tail = rel.partition("/")
    if head == events_ledger.EVENTS_DIRNAME and tail and "/" not in tail:
        return (
            events_ledger.EVENTS_FILENAME
            if tail.endswith(events_ledger.EVENTS_SHARD_SUFFIX)
            else None
        )
    return rel if rel in LEDGER_TABLES else None


def _is_shard(rel: str) -> bool:
    return rel.startswith(f"{events_ledger.EVENTS_DIRNAME}/")


def _export_file_stamps(export_directory: Path) -> dict[str, list[int]]:
    """``{export-relative path: [size, mtime_ns]}`` for every file under the export."""
    stamps: dict[str, list[int]] = {}
    for path in sorted(export_directory.rglob("*")):
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        stamps[path.relative_to(export_directory).as_posix()] = [
            stat.st_size,
            stat.st_mtime_ns,
        ]
    return stamps


def _appendable(stamps: dict[str, list[int]]) -> set[str]:
    """The ledger files an ordinary command appends to: the newest events shard and the
    three single-file ledgers. Only these carry a content hash in the baseline, so only
    these can be extended without a rebuild; a change anywhere else rebuilds."""
    shards = sorted(rel for rel in stamps if _is_shard(rel) and _ledger_of(rel))
    single = {
        rel for rel in stamps if rel in LEDGER_TABLES and rel != events_ledger.EVENTS_FILENAME
    }
    return single | set(shards[-1:])


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _roundtrip_key(store: Store, export_directory: Path) -> str:
    """What the round-trip's verdict depends on besides the export itself: this build
    (its schema version and every file of the package, by size and mtime), the store's
    own ``*.yml`` declarations, and where the export is. Any change re-runs it in full.
    """
    package: dict[str, list[int]] = {}
    for path in sorted(PACKAGE_SOURCE_DIR.iterdir()):
        try:
            if path.is_file():
                stat = path.stat()
                package[path.name] = [stat.st_size, stat.st_mtime_ns]
        except OSError:
            continue
    declarations: dict[str, list[int]] = {}
    for path in sorted(store.config.kyosei_dir.glob("*.yml")):
        try:
            stat = path.stat()
        except OSError:
            continue
        declarations[path.name] = [stat.st_size, stat.st_mtime_ns]
    material = {
        "schema_version": SCHEMA_VERSION,
        "package": package,
        "declarations": declarations,
        "export": str(export_directory),
        "prefix": store.config.project_prefix,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


def _full_roundtrip(store: Store, export_directory: Path, record_files: list[Path]) -> list[str]:
    """The whole rebuild, and — when it passes over an export that did not move while
    it ran — the baseline the hook form extends from."""
    key = _roundtrip_key(store, export_directory)
    before = _export_file_stamps(export_directory)
    hashes = {}
    for rel in _appendable(before):
        try:
            hashes[rel] = _sha256_of(export_directory / rel)
        except OSError:
            keys_usable = False
            break
    else:
        keys_usable = True
    failures, keys = _rebuild_and_compare(
        store, export_directory, record_files, load_ledgers=True
    )
    if (
        keys_usable
        and not failures
        and keys is not None
        and _export_file_stamps(export_directory) == before
    ):
        write_cache(
            doctor_cache_dir(store) / ROUNDTRIP_CACHE_NAME,
            {
                "key": key,
                "files": {
                    rel: [*stamp, hashes.get(rel)] for rel, stamp in before.items()
                },
                "ledgers": keys,
            },
        )
    return failures


class _Rebuild(Exception):
    """The incremental tier cannot vouch for this export; rebuild it in full.

    Never a finding: whatever made the tier give up, the full check that runs instead
    is the one that reports it, in its own words.
    """


def _verify_appends(
    export_directory: Path,
    appended: dict[str, tuple[int, str | None]],
    ledgers: dict[str, dict[str, int]],
) -> tuple[dict[str, list[dict]], dict[str, tuple[int, str]]]:
    """Prove each changed ledger file only GREW, and read what it grew by.

    ``appended`` maps a file to the (size, sha256) the baseline verified. The bytes up
    to that size must hash to the same digest -- so nothing already verified was edited,
    reordered or truncated -- and must end at a line boundary; everything after it must
    be whole lines, each a JSON object at or below this build's schema version, whose
    ``seq`` is above every seq its ledger already holds and whose id carries that seq
    (:data:`LEDGER_ID_RE`). Returns the new rows per ledger and each file's new
    (size, sha256).
    """
    rows: dict[str, list[dict]] = {name: [] for name in ledgers_module.LEDGER_READERS}
    highest: dict[str, int] = {}
    for name, keys in ledgers.items():
        value = keys.get("max_seq") if isinstance(keys, dict) else None
        if isinstance(value, bool) or not isinstance(value, int):
            raise _Rebuild("the recorded baseline is unreadable")
        highest[name] = value
    grown: dict[str, tuple[int, str]] = {}
    # Name order is append order for the shards (events_ledger.event_shard_name).
    for rel in sorted(appended):
        verified_size, verified_hash = appended[rel]
        ledger = _ledger_of(rel)
        if ledger is None or ledger not in highest:
            raise _Rebuild(f"{rel} is not a ledger this tier can extend")
        digest = hashlib.sha256()
        try:
            with (export_directory / rel).open("rb") as handle:
                prefix = handle.read(verified_size)
                tail = handle.read()
        except OSError as exc:
            raise _Rebuild(f"{rel} could not be read ({exc})") from exc
        if len(prefix) != verified_size:
            raise _Rebuild(f"{rel} is shorter than when it was last verified")
        digest.update(prefix)
        if verified_size and (
            digest.hexdigest() != verified_hash or not prefix.endswith(b"\n")
        ):
            raise _Rebuild(f"{rel} changed before its last verified line, not only after it")
        if tail and not tail.endswith(b"\n"):
            raise _Rebuild(f"{rel} ends in a partial line")
        digest.update(tail)
        grown[rel] = (verified_size + len(tail), digest.hexdigest())
        for raw in tail.decode("utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except ValueError as exc:
                raise _Rebuild(f"{rel} gained a line that is not JSON") from exc
            if not isinstance(row, dict):
                raise _Rebuild(f"{rel} gained a line that is not an object")
            try:
                version = export.ledger_schema_version(row, where=rel)
            except KyoseiError as exc:
                raise _Rebuild(str(exc)) from exc
            if version > SCHEMA_VERSION:
                raise _Rebuild(f"{rel} gained a line from a later schema_version")
            seq = row.get("seq")
            match = LEDGER_ID_RE.match(str(row.get("id")))
            if (
                isinstance(seq, bool)
                or not isinstance(seq, int)
                or seq <= highest[ledger]
                or match is None
                or int(match.group(1)) != seq
            ):
                raise _Rebuild(
                    f"{rel} gained a line whose seq is not above every seq its ledger "
                    "holds, or whose id does not carry its seq"
                )
            highest[ledger] = seq
            rows[ledger].append(row)
    ledgers.update({name: {"max_seq": value} for name, value in highest.items()})
    return rows, grown


def _load_appends(export_directory: Path, rows: dict[str, list[dict]]) -> None:
    """Load the appended rows and the counters through rebuild's own loader, into an
    in-memory database with the real schema: a line missing a column the load needs, or
    a counter snapshot that would not survive the load, fails here exactly as it would
    fail the whole rebuild. The counters are then compared the way the full check does.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(DDL)
        rebuild_module._load(conn, export_directory, [], rows, [])
        loaded = {
            scope: int(value)
            for scope, value in conn.execute(
                "SELECT scope, next_value FROM allocations ORDER BY scope"
            )
        }
    except Exception as exc:  # noqa: BLE001 - the full check will name it
        raise _Rebuild(f"the appended lines or counters do not load ({exc})") from exc
    finally:
        conn.close()
    if ledgers_module.read_allocations_in(export_directory) != loaded:
        raise _Rebuild("the counters do not survive a load")


def _incremental_roundtrip(
    store: Store, export_directory: Path, record_files: list[Path], baseline: dict
) -> tuple[str, list[str]]:
    """The hook form's tiers, cheapest first; raises :class:`_Rebuild` to fall back."""
    files = baseline.get("files")
    ledgers = baseline.get("ledgers")
    if (
        not isinstance(files, dict)
        or not isinstance(ledgers, dict)
        or not all(
            isinstance(entry, list)
            and len(entry) == 3
            and all(isinstance(n, int) and not isinstance(n, bool) for n in entry[:2])
            and (entry[2] is None or isinstance(entry[2], str))
            for entry in files.values()
        )
    ):
        raise _Rebuild("the recorded baseline is unreadable")
    now = _export_file_stamps(export_directory)
    removed = sorted(set(files) - set(now))
    if removed:
        raise _Rebuild(f"{removed[0]} was removed")
    record_rels = {
        path.relative_to(export_directory).as_posix() for path in record_files
    }
    allocations_rel = ledgers_module.allocations_path_in(export_directory).relative_to(
        export_directory
    ).as_posix()
    newest_verified_shard = max((rel for rel in files if _is_shard(rel)), default="")
    documents_moved = False
    changed = False
    appended: dict[str, tuple[int, str | None]] = {}
    for rel, stamp in now.items():
        verified = files.get(rel)
        if verified is not None and list(verified[:2]) == stamp:
            continue
        changed = True
        if rel in record_rels or rel == allocations_rel:
            documents_moved = documents_moved or rel in record_rels
            continue
        if _ledger_of(rel) is None:
            raise _Rebuild(f"{rel} changed")
        if verified is None:
            if rel == events_ledger.EVENTS_FILENAME or (
                _is_shard(rel) and rel <= newest_verified_shard
            ):
                raise _Rebuild(f"{rel} appeared out of append order")
            appended[rel] = (0, None)
        elif verified[2] is None:
            raise _Rebuild(f"{rel} is a closed ledger file and changed")
        else:
            appended[rel] = (verified[0], verified[2])
    if not changed:
        return ROUNDTRIP_CACHED, []

    advanced = {name: dict(keys) for name, keys in ledgers.items() if isinstance(keys, dict)}
    rows, grown = _verify_appends(export_directory, appended, advanced)
    _load_appends(export_directory, rows)
    failures: list[str] = []
    mode = ROUNDTRIP_APPENDS
    if documents_moved:
        mode = ROUNDTRIP_DOCUMENTS
        failures, _ = _rebuild_and_compare(
            store, export_directory, record_files, load_ledgers=False
        )
    if not failures and _export_file_stamps(export_directory) == now:
        # A file that is still appendable keeps a hash (the one just computed, or the
        # one it was verified with); the shard a new one just closed loses its hash, so
        # any later change to it rebuilds rather than being read as an append.
        appendable = _appendable(now)
        hashes = {rel: files[rel][2] for rel in files if rel in appendable}
        hashes.update({rel: digest for rel, (_, digest) in grown.items()})
        write_cache(
            doctor_cache_dir(store) / ROUNDTRIP_CACHE_NAME,
            {
                "key": baseline["key"],
                "files": {
                    rel: [*stamp, hashes.get(rel) if rel in appendable else None]
                    for rel, stamp in now.items()
                },
                "ledgers": advanced,
            },
        )
    return mode, failures


def check_export_roundtrip_hook(store: Store, detail: dict | None = None) -> list[str]:
    """``export_roundtrip`` as `kyo doctor --hook` runs it (KYO-408).

    The same findings as :func:`check_export_roundtrip` whenever there are any, at a
    cost that follows what CHANGED since the last verified pass rather than the size of
    the ledger. Against the baseline a passing round-trip recorded, cheapest first:

    * **cached** — no export file's (size, mtime) moved: the verdict is the one already
      reached, and nothing is read.
    * **appends-verified** — only ledgers grew and the counters moved, which is what
      `kyo sweep` does on every stop: each grown file is proved to have only grown
      (the verified bytes hash the same) and its new lines are loaded, with the
      counters, through rebuild's own loader. No record document can have changed, so
      none is rebuilt.
    * **documents-rebuilt** — a record document moved too: the appended lines are
      verified as above, and the record half of the store is rebuilt and compared
      (:func:`rebuild_from_export` with ``load_ledgers=False``) — no ledger is held in
      memory.
    * **full** — anything else: no baseline, a different build or ``*.yml``, a file
      removed, a closed shard or anything outside the ledgers edited, a ledger rewritten
      rather than appended to, or any appended line the tiers above cannot vouch for.

    What the tiers cannot see, stated rather than hidden: an edit to a record document
    or a closed shard that restores its size AND its mtime to the nanosecond.
    """
    export_directory = export.export_dir(store)
    record_files = export.record_export_files_in(export_directory)
    if not export_directory.is_dir() or not record_files:
        return []
    baseline = read_cache(doctor_cache_dir(store) / ROUNDTRIP_CACHE_NAME)
    why = "no verified round-trip is recorded yet"
    if baseline is not None and baseline.get("key") != _roundtrip_key(store, export_directory):
        why = "the build, the store's *.yml or the export location changed since the last pass"
    elif baseline is not None:
        try:
            mode, failures = _incremental_roundtrip(
                store, export_directory, record_files, baseline
            )
        except _Rebuild as reason:
            why = str(reason)
        except (KeyError, IndexError, TypeError, ValueError, OSError) as exc:
            # A baseline this build cannot read (hand-edited, truncated, another
            # shape) is a reason to rebuild, never a finding and never a crash.
            why = f"the recorded baseline is unreadable ({type(exc).__name__})"
        else:
            if detail is not None:
                detail["export_roundtrip"] = {"mode": mode}
            return failures
    if detail is not None:
        detail["export_roundtrip"] = {"mode": ROUNDTRIP_FULL, "why": why}
    return _full_roundtrip(store, export_directory, record_files)


def check_allocations(store: Store) -> list[str]:
    """Law 4: every counter is ahead of every id it has handed out."""
    failures: list[str] = []
    allocations = {
        row["scope"]: int(row["next_value"])
        for row in store.conn.execute("SELECT scope, next_value FROM allocations")
    }

    # KYO-414: the scopes compared are rebuild's own list, the one its seeding reads,
    # never a second hand-written copy here, which drifts from it without a word
    # (docs/design/doctor.md#kyo-414).
    for scope, highest, what in rebuild_module.loaded_allocation_highs(store.conn):
        nxt = allocations.get(scope)
        if nxt is None:
            failures.append(f"allocation scope {scope!r} is missing but {what} exist")
        elif nxt <= highest:
            failures.append(
                f"allocation scope {scope!r} would reissue: next_value={nxt} "
                f"but {what} already uses {highest} (law 4: ids are never reused)"
            )

    known_records = set(store.all_record_ids())
    for scope in sorted(allocations):
        if ":" in scope:
            _, _, owner = scope.partition(":")
            if owner not in known_records:
                failures.append(f"allocation scope {scope!r} names a record that does not exist")
        elif scope not in (
            SCOPE_RECORD,
            SCOPE_LINK,
            SCOPE_EVENT,
            SCOPE_SWEEP,
            SCOPE_BRIEF,
            SCOPE_SHIFT_NOTE,
        ):
            failures.append(f"allocation scope {scope!r} is not a scope this build allocates")
    return failures


def check_id_prefixes(store: Store) -> list[str]:
    prefix = store.prefix
    return [
        f"{row['id']} does not carry this project's prefix {prefix!r}"
        for row in store.conn.execute("SELECT id FROM records ORDER BY seq")
        if ids.prefix_of(row["id"]) != prefix
    ]


def check_orphans(store: Store) -> list[str]:
    failures: list[str] = []
    known = set(store.all_record_ids())
    for table in (
        "criteria",
        "notes",
        "gates",
        "touches",
        "claims",
        "verifications",
        "evidence_artifacts",
        "dispatches",
        "reports",
        "flags",
    ):
        for row in store.conn.execute(f"SELECT id, record_id FROM {table}"):
            if row["record_id"] not in known:
                failures.append(
                    f"{table[:-1]} {row['id']} points at missing record {row['record_id']}"
                )
    for row in store.conn.execute("SELECT id, src_id, dst_id FROM links"):
        for side in ("src_id", "dst_id"):
            if row[side] not in known:
                failures.append(f"link {row['id']} points at missing record {row[side]}")
    for row in store.conn.execute(
        "SELECT id, superseded_by FROM notes WHERE superseded_by IS NOT NULL"
    ):
        target = store.conn.execute(
            "SELECT 1 FROM notes WHERE id = ?", (row["superseded_by"],)
        ).fetchone()
        if target is None:
            failures.append(
                f"note {row['id']} is superseded by missing note {row['superseded_by']}"
            )

    known_criteria = {
        r["id"] for r in store.conn.execute("SELECT id FROM criteria")
    }
    for row in store.conn.execute("SELECT id, criterion_id FROM verifications"):
        if row["criterion_id"] not in known_criteria:
            failures.append(
                f"verification {row['id']} points at missing criterion {row['criterion_id']}"
            )

    known_evidence = {r["id"] for r in store.conn.execute("SELECT id FROM evidence_artifacts")}
    for row in store.conn.execute(
        "SELECT id, evidence_id FROM verifications WHERE evidence_id IS NOT NULL"
    ):
        if row["evidence_id"] not in known_evidence:
            failures.append(
                f"verification {row['id']} points at missing evidence artifact "
                f"{row['evidence_id']}"
            )

    known_claims = {r["id"] for r in store.conn.execute("SELECT id FROM claims")}
    for row in store.conn.execute(
        "SELECT id, supersedes FROM claims WHERE supersedes IS NOT NULL"
    ):
        if row["supersedes"] not in known_claims:
            failures.append(f"claim {row['id']} supersedes missing claim {row['supersedes']}")
    for row in store.conn.execute(
        "SELECT id, superseded_by FROM claims WHERE superseded_by IS NOT NULL"
    ):
        if row["superseded_by"] not in known_claims:
            failures.append(
                f"claim {row['id']} is superseded by missing claim {row['superseded_by']}"
            )

    # `supports_id` on an evidence artifact is a soft pointer into any of three
    # tables (claim, criterion, or record) — resolve against whichever it names.
    known_supports = known | known_criteria | known_claims
    for row in store.conn.execute(
        "SELECT id, supports_id FROM evidence_artifacts WHERE supports_id IS NOT NULL"
    ):
        if row["supports_id"] not in known_supports:
            failures.append(
                f"evidence artifact {row['id']} supports missing id {row['supports_id']}"
            )

    # Milestone 6 (KYO-8): a flag's cross-record pointers, present only on the
    # near-duplicate-criteria class, must resolve.
    for row in store.conn.execute(
        "SELECT id, other_record_id FROM flags WHERE other_record_id IS NOT NULL"
    ):
        if row["other_record_id"] not in known:
            failures.append(f"flag {row['id']} points at missing record {row['other_record_id']}")
    for row in store.conn.execute(
        "SELECT id, other_criterion_id FROM flags WHERE other_criterion_id IS NOT NULL"
    ):
        if row["other_criterion_id"] not in known_criteria:
            failures.append(
                f"flag {row['id']} points at missing criterion {row['other_criterion_id']}"
            )
    return failures
