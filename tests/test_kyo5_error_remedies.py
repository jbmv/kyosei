"""KYO-5: ``kyo rebuild`` writes the rebuilt schema_version into ``config.yml`` so
db and config agree without hand-editing (c1), and every error message naming a
remedy is proven — by an actual test that runs the remedy — to clear the error it
names (c2), starting with the two schema-version errors.

Found live (KYO-5): after `kyo rebuild` took the store from v1 to v2,
`config.yml` still declared `schema_version: 1` and the very next command failed
with a drift error whose remedy was "run `kyo rebuild`" — the command that had
just run and does not touch `config.yml`. A circular remedy, unblocked only by
hand-editing `config.yml` per the error message's own escape hatch. This file
exercises the fix end to end: hit the error, run the *exact* named remedy (nothing
extra, nothing hand-edited), and assert the error is actually gone afterward.

KYO-68 adds the other half of the guarantee, at the bottom of this file. Everything
above quantifies over the remedies a message **names**; nothing above says anything
about the remedies a message **omits**, so an error can be individually correct in
every line and still steer a reader to the worst available option — or to the only
option that existed when it was written. That is the shape of four filed defects
(KYO-31, KYO-34, KYO-36, KYO-64), none of which asserting harder that a named remedy
works would have caught.

KYO-73 adds the third quantifier, at the very bottom. Neither half above says
anything about whether the remedy a message names APPLIES to the situation that
produced it — and the fifth instance of this defect class is the first where the
named remedy did not merely underperform but actively refused: a fresh clone of a
split store repository was told to run `kyo init`, and `kyo init` turned it away.

KYO-77 turns that third quantifier from a test about one message into a test about
the surface. Everything above KYO-77's section is five defects fixed one at a time;
below it is the general check, worth more than the individual fixes once a
fifth appeared. It walks ``src/kyosei/`` for every error that can name a `kyo`
command, provokes as many of those as it has provokers for, runs what each message
actually printed against the state that printed it, and — because a check that
silently covers part of a surface is the same false assurance it exists to prevent —
reports its own coverage by name on every run. It found a sixth instance of the
defect class on the day it was written (`kinds.py`, in ``FINDINGS``).

KYO-83 changes what names a site. KYO-77 keyed its tables on ``module:lineno``, and
the first branch that met it added three lines of docstring above five sites and made
five considered entries stale at once without changing a word of any message. The key
is now ``module.py:function#ordinal``; the scheme, and what it cannot see, are set out
above :class:`ErrorSite`, and the properties it has to keep are asserted at the bottom
of this file.

KYO-84 changes what the surface IS. KYO-77 defined it as sites whose message can carry
a backticked `kyo` command, so a message whose CORRECT remedy is not a `kyo` command
fell off it entirely — and once off, nothing checked it at all. KYO-82 is the case that
proved it: `kinds.yml` is committed configuration, its recovery is `git checkout` or a
copy of the packaged default, and the site could only stay covered by naming
`kyo rebuild` in order to rule it out. A check whose coverage definition gives a message
a reason to name a command is shaping what it measures. The surface is now every site
that names a remedy of ANY kind, split into the half this check can RUN and the half it
can only RECORD; the predicate, and what it deliberately cannot see, are set out above
:data:`REMEDY_VERBS`.
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import cache, lru_cache
from pathlib import Path

import pytest
import yaml

from kyosei import install_hooks, install_procedures, scaffold
from kyosei.contracts import (
    CONTRACT_KIND_NOT_DIRECTLY_FILEABLE,
    CONTRACT_KIND_NOT_PROMOTABLE_FROM_IDEA,
    CONTRACT_KIND_NOT_SPAWNABLE_FROM,
)
from kyosei.errors import (
    EXIT_CONTRACT,
    EXIT_DOCTOR,
    EXIT_ERROR,
    SCHEMA_BUILD_AHEAD_OF_DB,
    SCHEMA_CONFIG_DRIFT,
    SCHEMA_DB_AHEAD_OF_BUILD,
    SCHEMA_MISMATCH_KEY,
    BrokenInvariants,
    GuardRefusal,
    KyoseiError,
    SchemaMismatch,
)
from kyosei.kinds import KindRegistry, load_kind_registry
from kyosei.schema import SCHEMA_VERSION

from .conftest import Run, git_init, invoke, kinds_yml, new_record, requires_evidence


def _config_path(project: Path) -> Path:
    return project / "kyosei" / "config.yml"


def _read_config_schema_version(project: Path) -> int:
    raw = yaml.safe_load(_config_path(project).read_text(encoding="utf-8"))
    return int(raw["schema_version"])


def _stamp_db_schema_version(project: Path, version: int) -> None:
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(version),))
    conn.commit()
    conn.close()


def _stamp_config_schema_version(project: Path, version: int) -> None:
    raw = yaml.safe_load(_config_path(project).read_text(encoding="utf-8"))
    raw["schema_version"] = version
    _config_path(project).write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


# --------------------------------------------------------------------- KYO-5.c1


def test_rebuild_writes_schema_version_into_config_yml(kyo, project: Path) -> None:
    """The two copies of the version agree after `kyo rebuild --force`, with no
    hand-editing of `config.yml` — the mechanism KYO-5.c1 asks for."""
    new_record(kyo, criteria=["a"])
    _stamp_config_schema_version(project, SCHEMA_VERSION + 5)
    assert _read_config_schema_version(project) != SCHEMA_VERSION

    result = kyo("rebuild", "--force")
    assert result.code == 0, result

    assert _read_config_schema_version(project) == SCHEMA_VERSION


def test_a_fresh_rebuild_from_export_also_writes_config(kyo, project: Path) -> None:
    """Same guarantee when the db is simply missing (no `--force` needed): a plain
    `kyo rebuild` after deleting kyosei.db still syncs config.yml."""
    new_record(kyo, criteria=["a"])
    _stamp_config_schema_version(project, SCHEMA_VERSION + 5)
    (project / "kyosei" / "kyosei.db").unlink()

    result = kyo("rebuild")
    assert result.code == 0, result
    assert _read_config_schema_version(project) == SCHEMA_VERSION


# --------------------------------------------------------------------- KYO-216.c1


def _stamp_export_document_schema_version(project: Path, record: str, version: int) -> Path:
    """Rewrite one record's export document to stamp ``version`` in place (KYO-216).

    Unlike ``tests/test_schema_compat.py``'s ``_bump_document_schema_version``, this
    writes a version OLDER than this build's own — the ordinary state left behind by
    a real schema bump, where every export document still says what it was written
    at and nothing else about it has changed.
    """
    export_path = project / "kyosei" / "export" / f"{record}.json"
    doc = json.loads(export_path.read_text(encoding="utf-8"))
    doc["schema_version"] = version
    export_path.write_text(json.dumps(doc), encoding="utf-8")
    return export_path


def test_rebuild_names_export_refresh_when_it_leaves_export_behind(
    kyo, project: Path
) -> None:
    """KYO-216.c1: the case as it was met live — a schema bump, then `kyo rebuild
    --force` as the open-time refusal instructs, then doctor reporting the same one
    fact (every export document is now behind the database) once per document, with
    no pointer at the moment it mattered.

    `rebuild_from_export` always stamps THIS build's ``SCHEMA_VERSION`` into the
    database it writes and never rewrites the export documents it read, so a
    document stamped at an older version is behind the store the instant rebuild
    finishes. The command's own output must say so and name `kyo export --refresh`
    as the next step — and the remedy it names must actually clear what it names,
    not merely read well.
    """
    record = new_record(kyo, criteria=["a"])
    _stamp_export_document_schema_version(project, record, SCHEMA_VERSION - 1)

    rebuilt = kyo("rebuild", "--force")
    assert rebuilt.code == 0, rebuilt
    assert "kyo export --refresh" in rebuilt.out, rebuilt
    assert str(SCHEMA_VERSION) in rebuilt.out, rebuilt

    payload = kyo("rebuild", "--force", "--json")
    assert payload.code == 0, payload
    assert payload.json["stale_export_documents"] == [record]

    # The drift is real, not merely announced: doctor sees a problem the moment
    # after rebuild leaves the export behind.
    dirty = kyo("doctor")
    assert dirty.code == EXIT_DOCTOR, dirty

    # The remedy named above, run exactly as named, clears it.
    refreshed = kyo("export", "--refresh")
    assert refreshed.code == 0, refreshed

    clean = kyo("doctor")
    assert clean.code == 0, clean


# --------------------------------------------------------------------- KYO-5.c2


def test_db_schema_version_mismatch_remedy_actually_clears_the_error(kyo, project: Path) -> None:
    """The db-predates-this-build error names `kyo rebuild --force` as its remedy;
    prove that running exactly that clears it."""
    record = new_record(kyo, criteria=["a"])
    _stamp_db_schema_version(project, SCHEMA_VERSION - 1)

    broken = kyo("show", record)
    assert broken.code == EXIT_ERROR
    assert "kyo rebuild --force" in broken.err

    remedy = kyo("rebuild", "--force")
    assert remedy.code == 0, remedy

    fixed = kyo("show", record)
    assert fixed.code == 0, fixed
    assert record in fixed.out


def test_config_db_drift_remedy_actually_clears_the_error(kyo, project: Path) -> None:
    """The config/db drift error (the exact bug KYO-5 was filed against) names
    `kyo rebuild --force` as its remedy; prove that running exactly that clears
    it — and that it does so by fixing config.yml, not by luck."""
    record = new_record(kyo, criteria=["a"])
    _stamp_config_schema_version(project, SCHEMA_VERSION + 5)

    broken = kyo("show", record)
    assert broken.code == EXIT_ERROR
    assert "must agree" in broken.err
    assert "kyo rebuild --force" in broken.err

    remedy = kyo("rebuild", "--force")
    assert remedy.code == 0, remedy

    fixed = kyo("show", record)
    assert fixed.code == 0, fixed
    assert record in fixed.out
    assert _read_config_schema_version(project) == SCHEMA_VERSION


def test_missing_meta_schema_version_remedy_actually_clears_the_error(
    kyo, project: Path
) -> None:
    """A db with a `meta` table but no `schema_version` row also names
    `kyo rebuild --force`; prove it too actually works."""
    new_record(kyo, criteria=["a"])
    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    conn.execute("DELETE FROM meta WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    broken = kyo("show", "TST-1")
    assert broken.code == EXIT_ERROR
    assert "kyo rebuild --force" in broken.err

    remedy = kyo("rebuild", "--force")
    assert remedy.code == 0, remedy

    fixed = kyo("show", "TST-1")
    assert fixed.code == 0, fixed


# --------------------------------------------------------------------- KYO-168.c3


def test_the_two_schema_directions_do_not_share_a_message_or_a_remedy(
    kyo, project: Path
) -> None:
    """KYO-168.c1/.c2/.c3: the property that makes the split worth having, asserted on
    the messages the two directions actually produce.

    "Both directions raise" is not this test — it passed on the code KYO-168 was filed
    against, which raised ONE symmetric sentence for `db_version != SCHEMA_VERSION` and
    so handed the holder of a NEWER store the command that reconstructs it under the
    older schema. ``rebuild.py`` stamps this build's ``SCHEMA_VERSION`` into what it
    rebuilds and rewrites ``config.yml`` to match (KYO-5.c1), and its tolerance for
    earlier export formats has no counterpart for a later one — a build cannot know
    what a future schema added — so that advice was a silent, config-blessed downgrade
    of a store that had nothing wrong with it.

    What is pinned is therefore asymmetric, and in both directions at once, so that any
    collapse back to one message or one remedy fails here:

      * BEHIND keeps KYO-5's message whole — the ``--force`` remedy, the reason plain
        `kyo rebuild` refuses, the citation. Watering it down into something the ahead
        case could share is what this half catches.
      * AHEAD names no `kyo` command at all, and in particular not the one that would
        downgrade the store; it says which side is stale and what to do instead. A
        shared message cannot satisfy both halves: it either carries
        `kyo rebuild --force` — and then the ahead reader is told to lose data — or it
        does not, and then the behind reader has lost KYO-5's remedy.

    The wording matters past the split because of who reads it. KYO-175 puts this
    message on screen in the desktop app, which bundles a pinned runtime and will
    routinely be pointed at stores it does not control, so ahead stops being exotic
    the day it ships and its reader is not at a shell.
    """
    record = new_record(kyo, criteria=["a"])

    _stamp_db_schema_version(project, SCHEMA_VERSION - 1)
    behind = kyo("show", record)
    assert behind.code == EXIT_ERROR, behind
    assert behind.out == ""  # never partial command output before the refusal
    assert "kyo rebuild --force" in behind.err
    assert "plain `kyo rebuild` refuses it" in behind.err
    assert "KYO-5" in behind.err

    _stamp_db_schema_version(project, SCHEMA_VERSION + 1)
    ahead = kyo("show", record)
    assert ahead.code == EXIT_ERROR, ahead
    assert ahead.out == ""
    # The whole point of the split: nothing here reconstructs the store under this
    # build's older schema, and no `kyo` command is named at all, because every command
    # that would make this store openable by this build would do exactly that.
    assert "rebuild" not in ahead.err, ahead.err
    assert "--force" not in ahead.err, ahead.err
    assert re.search(r"`kyo(\s|`)", ahead.err) is None, ahead.err
    # And it is specific in the way the behind message is specific: which side is older,
    # that the store is the healthy party, and what upgrading means for a reader who did
    # not install this build.
    assert "install a kyosei" in ahead.err
    assert "intact" in ahead.err
    assert "the desktop app and every project's hooks run" in ahead.err
    # KYO-466: the app carries no runtime of its own any more, so the
    # reader who did not install this build is no longer told it came with the app.
    assert "bundled with the application" not in ahead.err, ahead.err
    assert str(SCHEMA_VERSION) in ahead.err and str(SCHEMA_VERSION + 1) in ahead.err

    # Two directions, two messages — not one message reached twice.
    assert behind.err != ahead.err


# --------------------------------------------------------------------- KYO-216.c2


def test_the_open_time_refusal_names_the_full_upgrade_sequence(kyo, project: Path) -> None:
    """KYO-216.c2: the open-time schema-mismatch refusal named `kyo rebuild --force`
    and nothing after it — so a consumer who followed it, then hit the next `Stop`
    hook, landed on hundreds of doctor findings with no named next step. The refusal
    must state the full sequence: `kyo rebuild --force`, then `kyo export --refresh`,
    then `kyo doctor`.

    KYO-5's original wording (the `--force` remedy, why plain `kyo rebuild` refuses,
    the citation) is asserted unchanged — this adds to that message, it does not
    replace it (the property `test_the_two_schema_directions_do_not_share_a_message_
    or_a_remedy` above already pins for the BEHIND case as a whole).
    """
    record = new_record(kyo, criteria=["a"])
    _stamp_db_schema_version(project, SCHEMA_VERSION - 1)

    behind = kyo("show", record)
    assert behind.code == EXIT_ERROR, behind
    assert "kyo rebuild --force" in behind.err
    assert "plain `kyo rebuild` refuses it" in behind.err
    assert "KYO-5" in behind.err
    # KYO-216: what was missing.
    assert "kyo export --refresh" in behind.err, behind.err
    assert "kyo doctor" in behind.err, behind.err
    assert "KYO-216" in behind.err, behind.err
    # The sequence reads in order: rebuild before refresh, refresh before doctor.
    assert behind.err.index("kyo rebuild --force") < behind.err.index("kyo export --refresh")
    assert behind.err.index("kyo export --refresh") < behind.err.index("kyo doctor")


# --------------------------------------------------------------------- KYO-178
#
# KYO-168 (above) fixed the sentence. It did not give a *program* anything: the
# desktop app has to tell "this store is newer than me" from "this store is older
# than me" — opposite remedies, one of them destructive — and KYO-175.c4 requires it.
# Its only route was to substring-match the prose the test above pins, which makes the
# app's branching a hostage of the next improvement to that wording. So the refusals
# carry a payload as well, by the mechanism this package already uses four times over:
# `cli.main` merges `getattr(exc, "data", None)` into the `--json` error envelope.
#
# Everything in this section reads the ENVELOPE. Nothing in it reads `result.err`, and
# that is the property under test as much as the field values are.


def _schema_payload(result: Run) -> dict:
    """The KYO-178 payload out of a ``--json`` error envelope.

    One function, so a test that wanted the prose would have to reach past it visibly.
    """
    assert result.code == EXIT_ERROR, result
    return result.json[SCHEMA_MISMATCH_KEY]


def test_both_schema_directions_carry_a_payload_naming_the_older_side(
    kyo, project: Path
) -> None:
    """KYO-178.c1/.c2/.c3: which side is older, and all three version numbers, read
    out of the ``--json`` envelope's structured fields.

    Not one word of ``stderr`` is inspected here — not by a substring, not by a
    regex — which is the whole requirement: reword either message and this still
    passes, because nothing it asserts on is derived from the message (proven
    directly, on the class, by
    :func:`test_the_schema_payload_is_not_derived_from_the_message`).

    BOTH directions carry it. The behind case is the one whose sentence was already
    good — it kept KYO-5's remedy word for word through KYO-168 — and leaving it
    prose-only would leave a consumer that can recognise "ahead" and has to infer
    "behind" from the absence of a payload, which is exactly the prose-matching this
    record exists to end (KYO-178.c3).
    """
    record = new_record(kyo, criteria=["a"])
    config_path = project / "kyosei" / "config.yml"
    db_path = project / "kyosei" / "kyosei.db"

    _stamp_db_schema_version(project, SCHEMA_VERSION - 1)
    behind = _schema_payload(kyo("show", record, "--json"))
    assert behind["direction"] == SCHEMA_BUILD_AHEAD_OF_DB
    assert behind["older_side"] == "db"
    assert behind["db_schema_version"] == SCHEMA_VERSION - 1
    assert behind["build_schema_version"] == SCHEMA_VERSION
    assert behind["config_schema_version"] == SCHEMA_VERSION
    assert behind["db_path"] == str(db_path)
    assert behind["config_path"] == str(config_path)

    _stamp_db_schema_version(project, SCHEMA_VERSION + 1)
    ahead = _schema_payload(kyo("show", record, "--json"))
    assert ahead["direction"] == SCHEMA_DB_AHEAD_OF_BUILD
    assert ahead["older_side"] == "build"
    assert ahead["db_schema_version"] == SCHEMA_VERSION + 1
    assert ahead["build_schema_version"] == SCHEMA_VERSION
    assert ahead["config_schema_version"] == SCHEMA_VERSION

    # The point of the payload, stated as the thing a caller actually does with it:
    # the two directions are distinguishable from the structured fields alone.
    assert behind["direction"] != ahead["direction"]
    assert behind["older_side"] != ahead["older_side"]


def test_the_config_db_drift_refusal_carries_the_payload_too(kyo, project: Path) -> None:
    """The third refusal is on the same envelope and says so.

    KYO-178 left this to the worker; carrying it is the decision taken. The consumer
    is KYO-175's app, which receives this refusal from whatever command it was running
    exactly as it receives the other two, and telling "the store and I disagree" from
    "this project's config.yml is mis-stated" by reading English is the same defect one
    site down. ``older_side`` is ``None`` here and that is the answer, not a gap:
    reaching this line means both direction checks passed, so neither the db nor the
    build is the older party — see the message, and KYO-180.
    """
    record = new_record(kyo, criteria=["a"])
    _stamp_config_schema_version(project, SCHEMA_VERSION + 5)

    drift = _schema_payload(kyo("show", record, "--json"))
    assert drift["direction"] == SCHEMA_CONFIG_DRIFT
    assert drift["older_side"] is None
    assert drift["config_schema_version"] == SCHEMA_VERSION + 5
    assert drift["db_schema_version"] == SCHEMA_VERSION
    assert drift["build_schema_version"] == SCHEMA_VERSION
    assert drift["config_path"] == str(project / "kyosei" / "config.yml")


def test_the_schema_payload_is_not_derived_from_the_message() -> None:
    """KYO-178.c2, the half a CLI test cannot show: the same fields under two
    completely different sentences produce byte-identical payloads.

    This is what "would still pass if that message were reworded" means mechanically.
    A payload assembled by parsing the prose — the thing the app does today, moved
    into Python — would fail here.
    """
    fields = {
        "direction": SCHEMA_DB_AHEAD_OF_BUILD,
        "build_schema_version": 3,
        "db_schema_version": 4,
        "config_schema_version": 3,
        "config_path": Path("/tmp/p/kyosei/config.yml"),
        "db_path": Path("/tmp/p/kyosei/kyosei.db"),
    }
    as_written = SchemaMismatch("the store is newer than this build — KYO-168", **fields)
    reworded = SchemaMismatch("something else entirely, with no numbers in it", **fields)

    assert str(as_written) != str(reworded)
    assert as_written.data == reworded.data
    assert as_written.data[SCHEMA_MISMATCH_KEY]["older_side"] == "build"


def _schema_comparison_raises() -> dict[str, set[str | None]]:
    """``{the comparison's source: the classes raised under it}`` for ``open_store``.

    Keyed on the comparison rather than on a line or an ordinal because the comparison
    is what makes a refusal one of these: a branch guarded by a test that mentions
    ``db_version`` is a schema-version verdict, and there is no way to add a fourth one
    that this walk does not see.
    """
    tree = ast.parse((SRC_DIR / "store.py").read_text(encoding="utf-8"))
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "open_store"
    )
    found: dict[str, set[str | None]] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        if not any(
            isinstance(name, ast.Name) and name.id == "db_version"
            for name in ast.walk(node.test)
        ):
            continue
        raised = found.setdefault(ast.unparse(node.test), set())
        for inner in ast.walk(node):
            if isinstance(inner, ast.Raise) and inner.exc is not None:
                called = inner.exc.func if isinstance(inner.exc, ast.Call) else inner.exc
                raised.add(called.id if isinstance(called, ast.Name) else None)
    return found


def test_no_schema_version_refusal_can_be_raised_without_a_payload() -> None:
    """KYO-178.c4, in the two halves that between them close it.

    STATICALLY: every refusal in ``open_store`` guarded by a ``db_version`` comparison
    raises :class:`SchemaMismatch`. Revert one to a bare ``KyoseiError`` — the shape
    all three had before KYO-178 — and this fails, including for a *fourth* branch
    added later, which is the case a test that only provokes the three known states
    would miss.

    BY CONSTRUCTION: :class:`SchemaMismatch` cannot be built without the fields the
    payload is made of. There is no partially-populated form of it to raise, which is
    why the payload lives in a class rather than in a ``.data`` assignment beside each
    ``raise`` — an assignment is a line somebody can forget, and forgetting it fails
    nothing at the moment it happens.
    """
    comparisons = _schema_comparison_raises()
    # Guards the walk itself: rename `db_version` and this test would otherwise pass
    # vacuously, having found no branches at all.
    assert {
        "db_version < SCHEMA_VERSION",
        "db_version > SCHEMA_VERSION",
        "config.schema_version != db_version",
    } <= set(comparisons), sorted(comparisons)
    raised = {name for names in comparisons.values() for name in names}
    assert raised == {"SchemaMismatch"}, comparisons

    complete = {
        "direction": SCHEMA_CONFIG_DRIFT,
        "build_schema_version": 1,
        "db_schema_version": 1,
        "config_schema_version": 2,
        "config_path": "/tmp/p/kyosei/config.yml",
        "db_path": "/tmp/p/kyosei/kyosei.db",
    }
    assert set(SchemaMismatch("m", **complete).data[SCHEMA_MISMATCH_KEY]) == {
        "direction",
        "older_side",
        "build_schema_version",
        "db_schema_version",
        "config_schema_version",
        "config_path",
        "db_path",
    }
    for omitted in complete:
        with pytest.raises(TypeError):
            SchemaMismatch("m", **{k: v for k, v in complete.items() if k != omitted})
    # And a direction the payload has no `older_side` for is refused outright, rather
    # than stored as a value a consumer would have to guess the meaning of.
    with pytest.raises(ValueError, match="unknown schema mismatch direction"):
        SchemaMismatch("m", **{**complete, "direction": "ahead"})


# --------------------------------------------------------------------- KYO-180
#
# The site immediately below the pair, and one sentence's ordering. It fires when
# `config.yml`'s schema_version disagrees with the db's, and it is reached ONLY after
# both direction branches above have passed — so `db_version == SCHEMA_VERSION` is a
# precondition of it raising at all, the db agrees with the build, and `config.yml` is
# by construction the mis-stated party. The old wording put `kyo rebuild --force`
# first and the hand correction second behind "if it alone was mis-edited": a caveat
# on the cheap remedy naming a condition that cannot be false here, in front of a
# remedy that reconstructs the entire store to fix one scalar in a YAML file.


def test_the_config_db_drift_names_the_cheap_remedy_first_and_unhedged(
    kyo, project: Path
) -> None:
    """KYO-180.c1/.c2/.c3: order, hedge, reason, and both remedies still there.

    Asserted on the order the two remedies appear in, not on their presence: presence
    passed before this record too, and being told the destructive option first is the
    whole defect.
    """
    record = new_record(kyo, criteria=["a"])
    _stamp_config_schema_version(project, SCHEMA_VERSION + 5)

    drift = kyo("show", record)
    assert drift.code == EXIT_ERROR, drift
    message = drift.err

    # c1: the hand correction comes first, `kyo rebuild --force` second.
    by_hand = message.index("by hand")
    rebuild = message.index("kyo rebuild --force")
    assert by_hand < rebuild, message

    # c2: no hedge on the cheap remedy, and the message says why config.yml must be
    # the wrong party rather than leaving the reader to work it out.
    assert "if it alone was mis-edited" not in message, message
    assert "config.yml is the mis-stated copy" in message, message
    assert "it matches this build" in message, message
    assert f"schema_version {SCHEMA_VERSION}" in message, message

    # c3: both remedies survive, and so does the citation for what `--force` does to
    # config.yml — which is the reason the destructive option is still worth naming.
    assert "KYO-5.c1" in message, message
    assert "rewrites config.yml's schema_version to match the rebuilt db" in message


# ---------------------------------------------------------------------- KYO-64
#
# The same discipline applied to the remedy KYO-64 added. Note what this half of the
# file can and cannot prove, because KYO-64 was filed about exactly that gap: running
# a named remedy proves it WORKS. Nothing above proves a message names the BEST
# remedy, or that it was revisited when a better one shipped — which is how
# `kyo new`'s refusal went on recommending a registry edit for the whole of
# `kyo spawn`'s life. KYO-68, below, is that missing quantifier.


def _decision_without_intake(project: Path) -> None:
    """The pre-KYO-521 ``decision``: ``accepts_from: [proposal, investigation]``, and
    ``reminder`` no longer producing it. KYO-521 opened ``intake`` on the shipped
    ``decision``, which left no default kind `kyo new` refuses; the tests below that
    read that refusal's wording need one, so they put this one back."""
    path = kinds_yml(project)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["kinds"]["decision"]["accepts_from"] = ["proposal", "investigation"]
    raw["kinds"]["reminder"]["produces"] = [
        k for k in raw["kinds"]["reminder"]["produces"] if k != "decision"
    ]
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def test_the_spawn_remedy_named_by_the_intake_refusal_actually_creates_the_kind(kyo) -> None:
    """`kyo new decision` refuses and names a `kyo spawn` command; run that exact
    command, with nothing hand-edited, and the kind is created."""
    _decision_without_intake(kyo.project)
    source = new_record(kyo, kind="proposal", title="Pick a store", criteria=["a store is chosen"])

    broken = kyo("new", "decision", "--title", "use SQLite")
    assert broken.code != 0
    assert f"`kyo spawn {source} --kind decision" in broken.err, broken

    remedy = kyo("spawn", source, "--kind", "decision", "--title", "use SQLite", "--json")
    assert remedy.code == 0, remedy
    assert remedy.json["record"]["kind"] == "decision"


# ====================================================================== KYO-68
#
# The inverted quantifier. Everything above this line quantifies over the remedies a
# message NAMES: run each one, prove the error clears. It says nothing about the
# remedies a message OMITS, and all four filed instances of this defect are that
# shape — true when written, not revisited when the thing they described changed:
#
#   KYO-31  the Stop hook's `html_freshness` error named `kyo html`, and the hook
#           sequenced `doctor` ahead of it, so the remedy could never run.
#   KYO-34  the `/kyosei` skill named four sweep flag classes that never existed.
#   KYO-36  README and a docstring described a hook recipe the build had not shipped
#           since KYO-27, wrong through two separate changes to the thing described.
#   KYO-64  `kyo new`'s refusal listed every way in EXCEPT `kyo spawn`, the one that
#           works, so its only actionable suggestion was to weaken the registry.
#
# The pattern, stated once so the next instance can be recognised: WHEREVER A MESSAGE
# ENUMERATES OPTIONS FROM A SET THE CODE ALREADY KNOWS, ASSERT OVER THE SET, NOT OVER
# THE MESSAGE. A test that walks the message and checks each option is real catches a
# typo; a test that walks the SET and checks each member is named catches the option
# that shipped after the message was written, which is the one that actually recurs.
#
# KYO-68.c2 applies it to the creation verbs and, deliberately, to nothing else. The
# survey behind that (reasons in the KYO-68.d1 report, in full):
#
#   * statuses, gate types, link types, criterion states, claim levels, verification
#     methods, archetypes, kind names — every message that enumerates one of these
#     renders it as `', '.join(X)` over the code's own constant (contracts.py,
#     cmd_links.py, cmd_evidence.py, cmd_views.py, report_schema.py, kinds.py). The
#     message IS the set; it cannot omit a member, and the inversion would assert
#     that `', '.join(X)` contains every member of X. Left, as tautologies.
#   * flag classes — already inverted, by KYO-59:
#     ``test_skill_cli_sync.py::test_the_skill_names_every_flag_class_somewhere``
#     walks `contracts.FLAG_TYPES` and fails on any member the skill never names.
#     That test is this pattern already, in prose rather than in an error message,
#     and it is the reason the KYO-34 class of defect cannot recur for flag classes.
#   * the disposal verbs (`kyo wont-do` / `kyo void`) — the one other family of
#     hand-written cross-references, and deliberately left. Two members, each
#     unconditionally naming the other (`contracts.check_void_is_knowledge_only`,
#     `check_knowledge_has_no_status_flow`), with no third verb and no store state
#     to make either line conditional. What made the creation verbs decay was a THIRD
#     verb shipping into a message written when there were two; that risk is what
#     the inversion buys, and it is not present here.
#
# The creation verbs were the one place a message answered "which other verbs create
# this kind?" from hand-written branches over a registry that projects edit.

#: The three commands that create a record, one per source an ``accepts_from`` entry
#: may name: ``intake`` -> `kyo new`, ``idea`` -> `kyo promote`, another kind ->
#: `kyo spawn`. Each is the others' remedy when the registry turns a caller away.
CREATION_VERBS: tuple[str, ...] = ("new", "promote", "spawn")


def _packaged_registry() -> KindRegistry:
    """The registry `kyo init` installs — the same file the ``project`` fixture gets.

    Read at collection time so the parametrisation below is (kind x verb) over a real
    registry rather than a list maintained by hand in this file, which is the whole
    point of KYO-68.c1: a project that adds a kind, or adds an edge to one, changes
    what this test asserts with no edit here.
    ``test_the_parametrisation_is_the_projects_own_registry`` is the canary that the
    collection-time copy and the fixture's copy have not drifted apart.
    """
    defaults = scaffold.defaults_dir()
    assert defaults is not None, "this build ships no defaults/ directory to read"
    return load_kind_registry(defaults)


_REGISTRY = _packaged_registry()
_CASES = [(kind, verb) for kind in _REGISTRY.names for verb in CREATION_VERBS]
_CASE_IDS = [f"{kind}-{verb}" for kind, verb in _CASES]


def ways_in(registry: KindRegistry, kind: str) -> set[str]:
    """Which creation verbs the registry admits for ``kind`` — registry truth only.

    Whether a verb *would work right now* also depends on the store (`kyo spawn`
    needs a live source to name); that is deliberately not this question. A refusal
    must name every verb the registry admits, and may only name a runnable command
    line for the ones the store can currently satisfy.
    """
    entry = registry[kind]
    verbs = set()
    if entry.accepts_intake:
        verbs.add("new")
    if "idea" in entry.accepts_from:
        verbs.add("promote")
    if registry.spawn_sources(kind):
        verbs.add("spawn")
    return verbs


#: What "names verb V as a way into kind K" means, in message text. Each pattern is
#: the *invocation* form the refusals use, never the bare verb — because a refusal
#: also names verbs that will NOT work, to say so ("`kyo promote --kind decision`
#: will refuse it too"), and a checker that counted those would pass a build whose
#: only real remedy had been deleted. See ``test_the_matcher_ignores_a_dead_end``.
_WAY_IN_PATTERNS = {
    "new": r"`kyo new {kind}\b",
    "promote": r"`kyo promote <idea-file> --kind {kind}\b",
    "spawn": r"`kyo spawn \S+ --kind {kind}\b",
}


def names_way_in(message: str, verb: str, kind: str) -> bool:
    return re.search(_WAY_IN_PATTERNS[verb].format(kind=re.escape(kind)), message) is not None


def _corpus(kyo) -> list[tuple[str, str]]:
    """One record of every kind the registry lets `kyo new` file directly.

    Built through the registry's own front door on purpose: nothing here edits
    ``kinds.yml`` (which ``conftest.new_record`` does, via ``allow_direct_filing``),
    because a test about what the registry admits cannot also be changing it.
    """
    registry = load_kind_registry(kyo.project / "kyosei")
    corpus = []
    evidence = kyo.project.parent / "corpus-evidence.md"
    evidence.write_text("The test suite, building a corpus:\n\"evidence\"\n", "utf-8")
    for kind in registry.direct_filing_names:
        # KYO-521: a kind requiring evidence (the default `decision`) is filed with it.
        extra = (
            ["--evidence-file", str(evidence)]
            if requires_evidence(kyo.project, kind)
            else []
        )
        result = kyo("new", kind, "--title", f"a {kind}", *extra, "--json")
        assert result.code == 0, result
        corpus.append((result.json["record"]["id"], kind))
    return corpus


def _a_source_the_target_refuses(registry: KindRegistry, corpus, kind: str) -> str | None:
    """A record `kyo spawn` will not accept as a source for ``kind`` — the only way
    to provoke that verb's refusal. ``None`` when the corpus cannot provide one."""
    accepts = registry[kind].accepts_from
    return next((rid for rid, rkind in corpus if rkind not in accepts), None)


def _refusal(kyo, project: Path, registry: KindRegistry, kind: str, verb: str):
    """Provoke ``verb``'s refusal of ``kind``, however that verb is invoked.

    `kyo new` and `kyo promote` are provoked against an EMPTY store deliberately: an
    empty store is the state in which KYO-64's conditional silently deleted the
    `kyo spawn` verb from the message, and it is the state a fresh project is in.
    """
    if verb == "new":
        return kyo("new", kind, "--title", f"a {kind}")
    if verb == "promote":
        idea = project / "ideas" / f"{kind}.md"
        idea.write_text(f"# An idea about a {kind}\n\nBody text.\n", encoding="utf-8")
        return kyo("promote", str(idea), "--kind", kind)
    source = _a_source_the_target_refuses(registry, _corpus(kyo), kind)
    if source is None:  # pragma: no cover - no shipped kind accepts every other kind
        pytest.skip(f"no directly-filable kind is refused as a spawn source for {kind!r}")
    return kyo("spawn", source, "--kind", kind, "--title", f"a {kind}")


# --------------------------------------------------------------------- KYO-68.c1


def test_the_parametrisation_is_the_projects_own_registry(project: Path) -> None:
    """The canary for :func:`_packaged_registry`. The cases below are collected from
    the packaged ``defaults/kinds.yml``; the assertions run against the copy
    `kyo init` wrote into the fixture project. If those two ever stop being the same
    file, this test is parametrised over something the assertions never see."""
    assert kinds_yml(project).read_text(encoding="utf-8") == (
        (scaffold.defaults_dir() / "kinds.yml").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(("kind", "verb"), _CASES, ids=_CASE_IDS)
def test_a_refusing_creation_verb_names_every_other_verb_that_accepts_the_kind(
    kyo, project: Path, kind: str, verb: str
) -> None:
    """KYO-68.c1, over the whole (kind x creation verb) matrix.

    The assertion quantifies over the registry, not over the message: for each verb
    the registry admits for this kind, the refusing verb's message must name it. A
    verb that ships later and is not added to some refusal fails here on the day it
    ships — `kyo spawn` shipped in KYO-50 and went unnamed for three weeks, and this
    is the test that would have said so.
    """
    registry = load_kind_registry(project / "kyosei")
    admitted = ways_in(registry, kind)
    if verb in admitted:
        pytest.skip(f"`kyo {verb}` accepts {kind!r} — this cell is a success, not a refusal")

    result = _refusal(kyo, project, registry, kind, verb)
    assert result.code == EXIT_CONTRACT, result
    for other in sorted(admitted - {verb}):
        assert names_way_in(result.err, other, kind), (
            f"`kyo {verb}`'s refusal of {kind!r} omits `kyo {other}`, which the registry "
            f"admits for it:\n{result.err}"
        )


@pytest.mark.parametrize(("kind", "verb"), _CASES, ids=_CASE_IDS)
def test_a_refusing_creation_verb_offers_no_verb_that_would_refuse_too(
    kyo, project: Path, kind: str, verb: str
) -> None:
    """The same quantifier, other direction — KYO-5's rule stated over the set.

    A refusal may *mention* a verb that will not work, and `kyo new`'s does, to save
    the caller a second refusal. What it may not do is offer one in the invocation
    form that means "run this". Without this half, a refusal could satisfy the test
    above by naming all three verbs unconditionally.
    """
    registry = load_kind_registry(project / "kyosei")
    admitted = ways_in(registry, kind)
    if verb in admitted:
        pytest.skip(f"`kyo {verb}` accepts {kind!r} — this cell is a success, not a refusal")

    result = _refusal(kyo, project, registry, kind, verb)
    for other in CREATION_VERBS:
        if other == verb or other in admitted:
            continue
        assert not names_way_in(result.err, other, kind), (
            f"`kyo {verb}`'s refusal of {kind!r} offers `kyo {other}`, which would refuse "
            f"it too:\n{result.err}"
        )


def test_the_matcher_ignores_a_dead_end(kyo) -> None:
    """The two tests above are only as good as :func:`names_way_in`, and the message
    they read deliberately contains both forms. `kyo new decision` names
    `kyo promote` to say it will refuse too; counting that as "promote is named"
    would let a build pass with every real remedy deleted."""
    _decision_without_intake(kyo.project)
    dead_end = "`kyo promote --kind decision` will refuse it too ('idea' is not in its"
    assert not names_way_in(dead_end, "promote", "decision")
    assert names_way_in("run `kyo promote <idea-file> --kind decision`", "promote", "decision")
    assert not names_way_in("the registry describes it and nothing more", "spawn", "decision")
    assert names_way_in("`kyo spawn TST-1 --kind decision --title ...`", "spawn", "decision")
    assert names_way_in("`kyo spawn <source-id> --kind decision --title ...`", "spawn", "decision")

    # And the real message carries exactly that pair, so the distinction is not
    # hypothetical: `decision` accepts neither intake nor idea, only a kind.
    err = kyo("new", "decision", "--title", "use SQLite").err
    assert dead_end in err, err
    assert not names_way_in(err, "promote", "decision"), err
    assert names_way_in(err, "spawn", "decision"), err


# --------------------------------------------------------------------- KYO-68.c3


def test_the_creation_verbs_agree_on_exit_code_for_a_registry_flow_rejection(
    kyo, project: Path
) -> None:
    """KYO-68.c3: one class of rejection, one exit code.

    `kyo promote`'s refusal was a bare ``KyoseiError`` (exit 1) while `kyo new`'s and
    `kyo spawn`'s were ``ContractViolation`` (exit 2), for the same event: the
    registry's flow rules turning a kind away from a creation verb. Exit codes are a
    documented CLI contract here (``errors.py``: 0/1/2/3/64), so a consumer scripting
    against "a creation verb refused my kind" had to handle two of them, and which
    one it got depended on nothing it could see. The contract *names* stay distinct —
    the three refusals point somewhere different and a caller reading the message
    should be able to tell them apart — but the code a script branches on is one.

    KYO-521 left no default kind `kyo new` refuses, so the pre-KYO-521 `decision` is
    put back to supply one.
    """
    _decision_without_intake(project)
    registry = load_kind_registry(project / "kyosei")
    corpus = _corpus(kyo)

    codes: dict[str, int] = {}
    errors: dict[str, str] = {}
    for verb in CREATION_VERBS:
        kind = next(k for k in registry.names if verb not in ways_in(registry, k))
        if verb == "spawn":
            source = _a_source_the_target_refuses(registry, corpus, kind)
            assert source is not None
            result = kyo("spawn", source, "--kind", kind, "--title", f"a {kind}")
        else:
            result = _refusal(kyo, project, registry, kind, verb)
        codes[verb] = result.code
        errors[verb] = result.err

    assert codes == dict.fromkeys(CREATION_VERBS, EXIT_CONTRACT), codes
    assert CONTRACT_KIND_NOT_DIRECTLY_FILEABLE in errors["new"]
    assert CONTRACT_KIND_NOT_PROMOTABLE_FROM_IDEA in errors["promote"]
    assert CONTRACT_KIND_NOT_SPAWNABLE_FROM in errors["spawn"]
    assert len({errors[v].splitlines()[0] for v in CREATION_VERBS}) == 3, errors


# ====================================================================== KYO-73
#
# The third quantifier, and the one both halves above miss. Everything before this
# line is about remedies a message NAMES (run each, prove it clears the error) and
# remedies a message OMITS (walk the set, prove each member is named). Neither says
# anything about whether the remedy a message names APPLIES TO THE SITUATION THAT
# PRODUCED IT — and the fifth filed instance of this defect class is exactly that:
#
#   a fresh clone of a split store repository has `kyosei/export/` and no database,
#   because `kyosei.db` is gitignored there by design. The first `kyo` command said
#   "run `kyo init` or restore it". `kyo init`, run in that directory, refused —
#   with a message about moving a store out of a code repository, an operation
#   nobody had asked for. Two individually well-formed messages, one dead end.
#
# Nothing in this suite ran a command against a clone and read what it was told.
# tests/test_split_store.py proves recovery WORKS by calling `kyo rebuild` directly,
# which is the one path that never touches the error a human hits first.
#
# The test below is written as the extraction it describes: provoke the failure,
# parse the remedy out of the message itself, run exactly that, and assert the store
# is readable afterwards. It is deliberately not written against the string
# "kyo rebuild" — a test that asserts the message says what it says today cannot fail
# when the message starts naming something that does not work.


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


#: A `kyo` command line as an error message spells one: inside backticks, starting
#: with the verb. Deliberately matches ``kyo ...`` and not ``kyosei.db`` or any other
#: backticked filename the message may also carry.
_NAMED_REMEDY = re.compile(r"`(kyo\s+[^`]+)`")


def named_remedies(message: str) -> list[str]:
    """Every `kyo` command line the message offers as something to run."""
    return [match.group(1).strip() for match in _NAMED_REMEDY.finditer(message)]


def run_named_remedy(remedy: str, root: Path):
    """Run one extracted command line verbatim against ``root``.

    ``--root`` is prepended rather than substituted into the command: the remedy is
    run exactly as written, with nothing added to it but the answer to "which
    project" that an in-process test runner has to supply where a shell would have
    supplied a working directory.
    """
    argv = shlex.split(remedy)
    assert argv[0] == "kyo", remedy
    return invoke(["--root", str(root), *argv[1:]])


def clone_of_a_split_store(split_project, destination: Path) -> Path:
    """A second checkout of a split store: everything committed, no database.

    The exact state KYO-73 was filed against, and an ordinary one for a split store —
    another machine, a fresh clone, "what a second checkout of the same code needs
    when it did not inherit the link" in `kyo init`'s own adoption branch.
    """
    _, store = split_project
    _git(store, "add", "-A")
    _git(store, "-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-q", "-m", "work")
    subprocess.run(
        ["git", "clone", "-q", str(store), str(destination)], check=True, capture_output=True
    )
    assert not (destination / "kyosei" / "kyosei.db").exists()  # gitignored, as designed
    return destination


def exported_record_ids(root: Path) -> set[str]:
    directory = root / "kyosei" / "export"
    return {p.stem for p in directory.glob("*.json") if not p.stem.startswith("_")}


# --------------------------------------------------------------------- KYO-73.c1


def test_a_fresh_clone_of_a_split_store_is_told_a_remedy_that_actually_works(
    split_project, tmp_path: Path
) -> None:
    """KYO-73.c1, as the extraction it describes.

    Run the first command a second checkout runs, take the remedy out of what it
    says, run exactly that and nothing else, and require the store to be readable —
    every record in ``kyosei/export/`` present in `kyo list`. Before the fix this
    named `kyo init`, which refused.
    """
    code, _ = split_project
    kyo_code = lambda *a: invoke(["--root", str(code), *a])  # noqa: E731
    kyo_code("new", "bug", "--title", "a reproduction")
    kyo_code("new", "bug", "--title", "a second reproduction")
    clone = clone_of_a_split_store(split_project, tmp_path / "clone")
    expected = exported_record_ids(clone)
    assert len(expected) == 2

    broken = invoke(["--root", str(clone), "list"])
    assert broken.code == EXIT_ERROR, broken

    remedies = named_remedies(broken.err)
    assert len(remedies) == 1, (
        f"the missing-database failure must name exactly one command to run, not "
        f"{remedies}:\n{broken.err}"
    )

    fixed = run_named_remedy(remedies[0], clone)
    assert fixed.code == 0, fixed

    listed = invoke(["--root", str(clone), "list", "--json"])
    assert listed.code == 0, listed
    assert {item["id"] for item in listed.json["records"]} == expected


def test_the_remedy_the_clone_is_told_is_not_kyo_init(split_project, tmp_path: Path) -> None:
    """The canary for the test above, stated as the defect rather than the fix.

    The extraction test passes whenever the *named* remedy works, which is the
    guarantee that matters — but it would also pass if some future message named a
    working command with `kyo init` sitting beside it, and a reader who ran the wrong
    one would land straight back in the refusal. `kyo init` is not a remedy for a
    store that already exists, and must not be offered as one here.
    """
    code, _ = split_project
    invoke(["--root", str(code), "new", "bug", "--title", "a reproduction"])
    clone = clone_of_a_split_store(split_project, tmp_path / "clone")

    broken = invoke(["--root", str(clone), "list"])
    assert broken.code == EXIT_ERROR, broken
    assert not any(r.startswith("kyo init") for r in named_remedies(broken.err)), broken.err


# --------------------------------------------------------------------- KYO-73.c2


def test_kyo_init_in_a_store_with_no_database_does_not_misdiagnose_it(
    split_project, tmp_path: Path
) -> None:
    """KYO-73.c2: the other half of the dead end.

    `kyo init` in a cloned split store reported that the directory "already carries
    an in-repo store" and pointed at README's section on moving a store *out* of a
    code repository. Both were false of the situation: the consumer had not asked to
    move anything, did not have an in-repo store, and the README section describes a
    different operation. What it says instead has to be true and has to be
    actionable, so the same extraction is applied to it.
    """
    code, _ = split_project
    invoke(["--root", str(code), "new", "bug", "--title", "a reproduction"])
    clone = clone_of_a_split_store(split_project, tmp_path / "clone")

    refused = invoke(["init", str(clone)])
    assert refused.code == EXIT_ERROR, refused
    assert "already carries an in-repo store" not in refused.err, refused.err
    assert "Moving an existing store out of a code repository" not in refused.err, refused.err
    assert "Moving an existing store out of the code repository" not in refused.err, refused.err

    # And what it says instead is actionable: every command it offers as something to
    # run in this directory clears the state.
    offered = named_remedies(refused.err)
    assert offered, refused.err
    for remedy in offered:
        result = run_named_remedy(remedy, clone)
        assert result.code == 0, f"`{remedy}` was offered and failed: {result}"
    assert invoke(["--root", str(clone), "list"]).code == 0


def test_the_readme_pointer_survives_where_it_is_true(tmp_path: Path) -> None:
    """KYO-73.c2 asks for the misdiagnosis to stop, not for the message to be deleted.

    A live in-repo store — database and all — being asked to become a split one is
    exactly the situation that section describes, and it still says so there.
    """
    code = tmp_path / "inrepo"
    code.mkdir()
    assert invoke(["init", str(code), "--in-repo", "--prefix", "TST"]).code == 0
    assert (code / "kyosei" / "kyosei.db").is_file()

    refused = invoke(["init", str(code)])
    assert refused.code == EXIT_ERROR, refused
    assert "already carries an in-repo store" in refused.err, refused.err
    assert "Moving an existing store out of the code repository" in refused.err, refused.err


def test_the_remedy_extractor_reads_commands_and_not_filenames() -> None:
    """The two tests above are only as good as :func:`named_remedies`.

    A message that names no command must extract to nothing rather than to a
    backticked filename that happens to start with the project's name — which the
    missing-database message carries, and which would otherwise be "run" as a remedy.
    """
    assert named_remedies("`kyosei.db` is gitignored, so a fresh clone has this shape") == []
    assert named_remedies("run `kyo rebuild` to reconstruct it") == ["kyo rebuild"]
    assert named_remedies("`kyo rebuild --force` or `kyo export --refresh`") == [
        "kyo rebuild --force",
        "kyo export --refresh",
    ]
    assert named_remedies("see README, \"Moving an existing store out\"") == []


# ====================================================================== KYO-77
#
# The general check, worth more than the individual fixes once a fifth instance
# appeared. Everything above is the three quantifiers applied by hand,
# one filed defect at a time: does a NAMED remedy work (KYO-5), is an OMITTED one
# missing from the set (KYO-68), does the named one APPLY to the state that produced
# the error (KYO-73). Each of those is a test about one message. None of them says
# anything about the other thirty-odd messages in this build that name a remedy.
#
# What follows quantifies the third question over the whole surface, and — because a
# check that silently covers a tenth of the surface is worse than no check at all —
# carries its own coverage figure. Three parts:
#
#   1. THE SURFACE (the denominator). :func:`remedy_naming_error_sites` walks the AST
#      of ``src/kyosei/`` and finds every ``raise`` of a KyoseiError subclass whose
#      message can carry a backticked `kyo ...` command. It resolves one hop through
#      module-level constants and helper functions, because the message that started
#      this — ``store.py``'s missing-database failure — is
#      ``f"... {missing_database_remedy(config)}"`` and carries no remedy text of its
#      own at all. A denominator that missed the very site KYO-73 was filed against
#      would be a fine illustration of the problem and no use as a check.
#
#   2. THE PROVOCATION (the numerator). :data:`PROVOKED` maps a site to a function
#      that builds a store in the state that reaches it. Provocation is the expensive
#      half — the KYO-73.d1 worker said so and they were right — which is why this is
#      a table and not a sweep.
#
#   3. THE ASSERTION. For each provoked site: take the remedies out of the message the
#      run ACTUALLY printed (never out of the source, and never out of a list kept
#      here), and run each one against a FRESH provocation of the same state. Each must
#      exit 0 and the original error must be gone — "gone" meaning the same raise site
#      does not fire again, which is why the site index is built at all.
#
# What this deliberately is not: a message-grammar linter. Nothing here parses a
# message for well-formedness, checks that a named command exists, or asserts a
# message says what it says today. A checker of that shape would have passed all five
# filed instances, including KYO-73, where every word was true and the command was
# real and the consumer was still stuck.
#
# The one hand-maintained judgment is :attr:`Expectation.descriptive`: a message may
# *mention* a command without offering it ("`kyo flag collapse` takes no flag id"),
# and no amount of state-reading distinguishes a mention from an offer. Every such
# mention is classified here by name, with a reason, and the classification is itself
# checked: a mention that stops appearing in the message fails as stale, and a
# mention that starts clearing the error fails as misclassified.

SRC_DIR = Path(scaffold.__file__).resolve().parent

#: Every exception class in ``kyosei.errors`` (plus ``registry.RegistryError``) that the
#: CLI's single handler turns into a message on stderr and an exit code. Named by string
#: because the walk is static; :func:`test_the_site_index_covers_every_error_class` is
#: the canary that this list and the class hierarchy have not drifted apart — and it
#: fired on the day KYO-100 added ``RegistryError``, which is exactly what it is for.
#:
#: KYO-104 removed ``KindRegistryError``, which is now a second name for
#: ``RegistryError`` rather than a class: the kind registry stopped having a loader of
#: its own, so it stopped having a failure of its own to name.
ERROR_CLASS_NAMES = frozenset(
    {
        "KyoseiError",
        "NotFound",
        "ContractViolation",
        "DoctorFailure",
        "UsageError",
        "RegistryError",
        # KYO-101: `kyosei/statuses.yml` declares a ladder the transition contracts
        # cannot bind to — a role missing, or carried by the wrong number of statuses.
        # Its own class because the remedy differs from a shape violation's: the file
        # parses and every entry is well formed, and what is wrong is the arrangement.
        "StatusRegistryError",
        # KYO-178: `store.open_store`'s three schema_version refusals, which carry a
        # structured payload as well as a sentence. A class rather than a `.data`
        # assignment at each raise, so the payload cannot be left off — see
        # `test_no_schema_version_refusal_can_be_raised_without_a_payload`. Listed here
        # because the walk matches raises BY CLASS NAME: without this entry all three
        # sites would silently drop off the surface and the tables below would go stale
        # without a single message changing.
        "SchemaMismatch",
        # KYO-181: `kyo rebuild`'s refusal of an export written by a later build, in
        # either half — record documents (KYO-179) or ledger lines. Same reasoning as
        # `SchemaMismatch` directly above, and this entry is not a formality: the first
        # draft raised a plain KyoseiError with `.data` assigned to a local, and this walk
        # is what noticed that the site had silently dropped off the surface.
        "ExportSchemaAhead",
        # KYO-407: a torn or corrupt export file, named with its path, line and the
        # `git -C <dir> checkout -- <file>` that restores it. Built by one factory
        # (`export.export_file_unreadable`) rather than raised inline, so no raise site
        # carries the class name; its remedy is run end to end, per file and per
        # command, by `tests/test_atomic_writes.py`.
        "ExportFileUnreadable",
        # KYO-187: `kyo check-branch`/`check-clean`/`check-push`, and `kyo doctor`/
        # `docs check`. Those five refusals carry the command's own `--json` envelope,
        # and until KYO-187 each of them built the error, assigned `.data` to a local
        # and raised the variable — a shape this walk matches nothing against, so all
        # five sat off the surface. They are classes now, listed here for the reason
        # `SchemaMismatch` gives above, and the shape they came from is no longer
        # merely unseen but refused: see
        # `test_no_raise_site_hides_its_class_behind_a_variable`.
        "GuardRefusal",
        "BrokenInvariants",
        # KYO-502: `kyo docs index --check` on a stale committed index. Exit 1 rather
        # than BrokenInvariants' 3, carrying its `--json` envelope the same way.
        "IndexStale",
    }
)

#: A `kyo` command line inside backticks — the same shape :func:`named_remedies` reads
#: out of a live message, applied here to source text instead.
_SOURCE_REMEDY = re.compile(r"`(kyo\s[^`]*)`")


# ------------------------------------------- KYO-84: what counts as naming a remedy
#
# KYO-77's surface was "the message can carry a backticked `kyo` command", and that is a
# precise predicate for a reason: it is exactly the set this check has the vocabulary to
# RUN. The defect KYO-84 was filed about is that it was also being used as the whole
# surface, so a message whose correct remedy is a `git checkout`, a re-invocation with a
# different flag, or a hand edit was not merely unverified — it was invisible, and the
# coverage figure was a percentage of a denominator smaller than the real one.
#
# "Names a remedy" is fuzzier than "names a `kyo` command", and got wrong toward
# permissive it fills the denominator with sites that have nothing to verify until the
# percentage stops meaning anything. So the widening is built the way the rest of this
# file already is — OVER-COLLECT MECHANICALLY, CLASSIFY BY HAND, GATE THE
# CLASSIFICATION — rather than by trying to write a predicate clever enough to be right
# on its own. Three recognisers produce a CANDIDATE, and every candidate must land in
# one of the five tables below or the gate fails:
#
#   1. a backticked `kyo ...` command line       -> the RUNNABLE half. Unchanged.
#   2. a backticked non-`kyo` command line       -> `git checkout -- x`, `cp a b`.
#   3. an imperative remedy clause               -> "pass --criteria-file", "delete the
#                                                   line, or give it a value".
#
# 2 over-collects on purpose and in a knowable direction: this package's messages quote
# the command they THEMSELVES ran when it fails (``git rev-parse HEAD`` produced no
# output), and no syntax distinguishes that from a command offered to the reader. Those
# are named in :data:`NOT_A_REMEDY` and excluded from the denominator — the point being
# that they are now named rather than absent. That table is narrow by construction, not
# by good intentions: ``test_the_not_a_remedy_table_cannot_absorb_a_real_remedy``
# refuses an entry that names a `kyo` command or carries an imperative clause, so the
# only thing it can ever hold is "the backticked command is the one kyo ran".
#
# WHAT THIS PREDICATE CANNOT SEE, stated so the next person does not have to derive it:
#
#   * A REMEDY STATED AS A REQUIREMENT rather than as an instruction. ``cmd_links.py``'s
#     `kyo gate` refusal says "gate type requires --do: the exact condition or thing to
#     run must live on the record" — the reader is being told what to pass, in a
#     sentence with no imperative in it. Deliberately left off: recognising declarative
#     statements means recognising "X is not a gate type; known: a, b, c" too, and from
#     there every refusal names a remedy and the denominator is all 178 sites.
#   * A COMMAND SPELLED WITHOUT BACKTICKS. This build has two, and they are the two
#     that matter most: ``kinds.py`` writes "Restore it from version control (git
#     checkout -- x) ... otherwise copy the packaged default: cp a b", bare, while
#     ``cmd_dispatch.py`` backticks `git worktree add`. So the convention is not one
#     this package actually keeps, and recogniser 2 misses half of it. Recogniser 3
#     covers both those sites anyway, because both are introduced by an imperative
#     ("Restore it...", "copy the packaged default:") and the excerpt carries the
#     command along with it — which is why they are on the surface today.
#     REJECTED: scanning un-backticked text for command lines. Without delimiters it
#     needs a closed list of program names to anchor on, and one level is not enough —
#     "does not look like a git repository" would parse as `git repository`. Two levels
#     (program plus subcommand) is a larger hand-kept surface than the one it buys,
#     against a case already covered by the verb that introduces it.
#   * A REMEDY IN A VERB THIS VOCABULARY DOES NOT HOLD. :data:`REMEDY_VERBS` is
#     hand-kept, and a message that says "reinstate the link" is off the surface with
#     nothing to say so. This is the one place the widening can under-collect silently,
#     which is the failure mode this record was filed about, reappearing one level up.
#     It is bounded by being a closed list of ordinary imperatives rather than a
#     heuristic, and it is the honest cost of the alternative being worse.
#   * WHETHER A RECORDED REMEDY IS CORRECT. That is the whole point of the split: the
#     runnable half is run, the recorded half is reported as present-but-unverified.
#     Recording it is better than the status quo, where it was neither.

#: A command line inside backticks whose program is not ``kyo``. Split the way
#: :func:`shell_command_the_message_spells` splits one, and for the same reason: the
#: message spells these FOR a shell, quoting included. Two tokens minimum and a
#: bare-word program name, which is what keeps a backticked identifier
#: (`policy.write_scope`), filename (`kyosei.db`), bare verb (`wont-do`) and flag
#: (`--why-file`) out of the surface — none of those is anything to run.
_BACKTICKED = re.compile(r"`([^`]+)`")
_PROGRAM_NAME = re.compile(r"[a-z][a-z0-9_-]*")


def shell_remedies(message: str) -> tuple[str, ...]:
    """Backticked non-`kyo` command lines, in order, deduplicated."""
    found: list[str] = []
    for span in _BACKTICKED.findall(message):
        text = " ".join(span.split())
        if text == "kyo" or text.startswith("kyo "):
            continue
        try:
            tokens = shlex.split(text)
        except ValueError:  # an unbalanced quote is prose, not a command line
            continue
        if len(tokens) >= 2 and _PROGRAM_NAME.fullmatch(tokens[0]):
            found.append(text)
    return tuple(dict.fromkeys(found))


#: The imperatives this package's error messages actually use to tell a reader what to
#: do, plus their obvious siblings. A CLOSED LIST on purpose: the alternative is a
#: part-of-speech heuristic over message text, which would be unauditable in exactly the
#: place the denominator has to be auditable. Calibrated against all 178 deliberate
#: error sites — see ``test_the_imperative_recogniser_reads_instructions_not_prose`` for
#: both directions of what that calibration bought.
REMEDY_VERBS: tuple[str, ...] = (
    "pass", "give", "set", "run", "rerun", "re-run", "edit", "delete", "remove",
    "restore", "copy", "create", "add", "re-add", "move", "rename", "omit", "point",
    "install", "revert", "supply", "provide", "choose", "pick", "use", "name", "read",
    "check out", "checkout", "ship",
)  # fmt: skip

#: A remedy verb at the START OF A CLAUSE — after a sentence break, a semicolon, a
#: colon, a dash, an open paren, a comma, "or", "then", "otherwise", or "e.g.". Position
#: is what separates the instruction "delete the line" from the description "cannot be
#: deleted", and it is why this is not a bare word search.
_REMEDY_IMPERATIVE = re.compile(
    r"(?:^|(?<=[.;:—()])|(?<=, )|(?<= or )|(?<= then )|(?<=, or )|(?<=otherwise )"
    r"|(?<=e\.g\. ))\s*(" + "|".join(REMEDY_VERBS) + r")\b",
    re.IGNORECASE,
)


def imperative_remedies(message: str) -> tuple[str, ...]:
    """The instruction clauses a message issues, excerpted for reporting.

    The excerpt is what a coverage report and a table entry name the site by; nothing
    runs it, because nothing here can. It is cut at the next sentence or dash so an
    entry reads as the instruction it is rather than as the rest of the message.
    """
    found: list[str] = []
    for match in _REMEDY_IMPERATIVE.finditer(message):
        clause = re.split(r"(?<=[.;])\s|\s—\s", message[match.start(1) :])[0]
        found.append(" ".join(clause.split())[:90])
    return tuple(dict.fromkeys(found))


# ------------------------------------------------------ KYO-83: how a site is named
#
# The key is what the three tables below are indexed by, so what it is made of decides
# what makes an entry go stale. KYO-77 keyed on ``module:lineno`` and that was wrong
# within an afternoon: KYO-80 added three lines of *docstring* to ``store.py``, five
# sites moved down by three, and five entries whose messages had not changed by one
# character were all stale at once. The repair for that is mechanical — add three to
# each number — which means it gets done by whoever is nearest the red suite rather
# than by whoever understands the site, and after two rounds of it the reasons written
# in these tables are decorations attached to numbers nobody re-read. A check that
# breaks on edits unrelated to what it checks is a check people learn to silence
# (KYO-33 is already on file for that shape, about `kyo ideas review`).
#
# So: ``module.py:function#ordinal`` — ``store.py:open_store#0``. Two properties have
# to survive the change, because they are what makes this check worth running at all,
# and KYO-77's line key delivered both:
#
#   * a NEW site must be caught. It is: an unclassified key fails
#     ``test_every_remedy_naming_site_is_classified`` on the day it ships, which is how
#     KYO-81's ``cmd_store.py`` refusal was found.
#   * a site whose MESSAGE changes must be caught. It is, but not by the key and never
#     was: :func:`check_remedies_apply` reads the remedies out of the stderr the run
#     actually produced, so a reworded message is re-run as reworded, a mention that
#     leaves the message fails as a stale classification, and a mention that starts
#     clearing the error fails as misclassified. Both directions are asserted in
#     ``test_a_changed_message_is_caught_though_the_key_does_not_move``.
#
# ORDINALS COUNT EVERY deliberate raise in the function, not only the remedy-naming
# ones, so a message that gains or loses a `kyo` command does not renumber its
# siblings. The enclosing name is qualified and innermost-wins (``outer.inner``,
# ``Class.method``), so a closure's raise is not filed under its host.
#
# WHAT THIS SCHEME CANNOT SEE — every scheme is blind to something and the next person
# should not have to derive it:
#
#   1. A message rewritten at a site whose remedies still run. The key does not move
#      and nothing above notices; the running check only sees the commands. If a
#      message's *reason* for being classified the way it is changes without changing
#      any `kyo` command in it, that judgment goes stale silently.
#   2. Reordering two raises inside one function. The keys swap, the surface set is
#      unchanged, so the classification gate stays green. For a PROVOKED or FINDINGS
#      entry the provoker catches it ("the provoker reached X, not Y"); for an
#      UNREACHED entry, which has no provoker, nothing does — its reason silently
#      moves to the neighbouring site.
#   3. Renaming a function, or moving a raise between functions. Every site in it
#      looks deleted and re-added, so the gate fails and the entries have to be
#      re-keyed by hand. That is churn, and it is the deliberate trade: it happens
#      when the code around the site is genuinely restructured, not when three lines
#      of docstring land above it.
#   4. A raise deleted and a different one added in its place, at the same ordinal in
#      the same function. The entry silently re-points. Same blind spot the line key
#      had, for the same reason: no scheme that names a location can tell a site from
#      its replacement.
#
# Rejected: hashing the message into the key. It makes 1 visible, at the price of
# turning every reworded message into a table edit whose repair is "paste the new
# hash" — the same judgment-free mechanical bump this record was filed about, in a
# form no reader can check by eye. Rejected: keying on the remedy a site names, which
# is stable only until two sites in a module name `kyo rebuild --force`, and four of
# them already do.


@dataclass(frozen=True)
class ErrorSite:
    """One ``raise`` of a deliberate error, located in the source tree."""

    module: str  #: ``"store.py"`` — the file's basename
    function: str  #: qualified, innermost-first-owner: ``"open_store"``, ``"C.m"``
    ordinal: int  #: index among the deliberate raises in that function, in source order
    lineno: int
    end_lineno: int
    remedies: tuple[str, ...]  #: `kyo ...` commands statically visible in its message
    #: KYO-84: what else the message names that a reader could act on — a backticked
    #: non-`kyo` command line, an instruction clause. Populated for EVERY site, not only
    #: for those that name no `kyo` command, because a site can offer both and
    #: ``kinds.py`` is one: it mentions `kyo rebuild` to rule it out and names
    #: `git checkout` and `cp` as the recoveries that work.
    recorded: tuple[str, ...] = ()

    @property
    def names_a_remedy(self) -> bool:
        """Whether this site is a CANDIDATE for the surface — see the KYO-84 notes.

        A candidate, not a verdict: the five tables are what separate a remedy offered
        to the reader from a command this package quoted because it ran it and it
        failed, and no syntax available here tells those apart.
        """
        return bool(self.remedies or self.recorded)

    @property
    def key(self) -> str:
        """``"store.py:open_store#0"`` — what the tables below are indexed by."""
        return f"{self.module}:{self.function}#{self.ordinal}"


class _SourceIndex:
    """The AST of ``src/kyosei/``, with enough name resolution to read a message.

    Resolution matters more than it looks. Of the sites that name a remedy, several
    carry none of that text at the raise itself: ``contracts.py`` keeps
    ``SCOPE_UNKNOWN_REMEDY`` as a module constant so two surfaces cannot remember it
    differently (law 3), and ``store.py``'s missing-database failure delegates the
    entire remedy to ``currency.missing_database_remedy``, which chooses between two
    of them by reading the disk. Both are good structure and both are invisible to a
    walk that only reads string literals at the raise.

    So: ``Name`` resolves to a module-level assignment (in this module or one it
    imports from), and ``Call`` resolves to a module-level function, whose ``return``
    expressions are read in turn. Branch-insensitively and to a bounded depth — this
    is a candidate set, not an evaluator. Over-collecting is safe here (a command the
    message cannot actually print is a site reported as unreached at worst);
    under-collecting is not, because it shrinks the denominator silently.
    """

    MAX_DEPTH = 4

    def __init__(self, directory: Path) -> None:
        self.trees: dict[str, ast.Module] = {}
        self.consts: dict[str, dict[str, ast.expr]] = {}
        self.funcs: dict[str, dict[str, ast.FunctionDef]] = {}
        self.imported: dict[str, dict[str, tuple[str, str]]] = {}
        self.module_aliases: dict[str, dict[str, str]] = {}
        for path in sorted(directory.rglob("*.py")):
            self._load(path.stem, ast.parse(path.read_text(encoding="utf-8")))

    def _load(self, module: str, tree: ast.Module) -> None:
        self.trees[module] = tree
        consts: dict[str, ast.expr] = {}
        funcs: dict[str, ast.FunctionDef] = {}
        imported: dict[str, tuple[str, str]] = {}
        aliases: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        consts[target.id] = node.value
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.value is not None:
                    consts[node.target.id] = node.value
            elif isinstance(node, ast.FunctionDef):
                funcs[node.name] = node
        # Relative imports anywhere, including the function-local ones this package
        # uses to break import cycles — `store.open_store` imports `currency` inside
        # the function body, and that is exactly the edge the resolution needs.
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                for alias in node.names:
                    local = alias.asname or alias.name
                    if node.module is None:
                        aliases[local] = alias.name
                    else:
                        imported[local] = (node.module, alias.name)
        self.consts[module] = consts
        self.funcs[module] = funcs
        self.imported[module] = imported
        self.module_aliases[module] = aliases

    def message_text(self, expr: ast.expr, module: str) -> str:
        return " ".join(self._text(expr, module, 0, frozenset()))

    def _text(
        self, expr: ast.expr | None, module: str, depth: int, seen: frozenset
    ) -> list[str]:
        if expr is None or depth > self.MAX_DEPTH:
            return []
        out: list[str] = []
        for node in ast.walk(expr):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.append(node.value)
            elif isinstance(node, ast.Name):
                out += self._resolve(module, node.id, module, depth, seen)
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                alias = self.module_aliases.get(module, {}).get(node.value.id)
                if alias is not None:
                    out += self._resolve(alias, node.attr, module, depth, seen)
        return out

    def _resolve(
        self, module: str, name: str, referrer: str, depth: int, seen: frozenset
    ) -> list[str]:
        key = (module, name)
        if key in seen:
            return []
        seen = seen | {key}
        if name in self.consts.get(module, {}):
            return self._text(self.consts[module][name], module, depth + 1, seen)
        if name in self.funcs.get(module, {}):
            return self._returns_of(self.funcs[module][name], module, depth + 1, seen)
        target = self.imported.get(referrer, {}).get(name)
        if target is not None and target[0] != module:
            return self._resolve(target[0], target[1], referrer, depth, seen)
        return []

    def _returns_of(
        self, fn: ast.FunctionDef, module: str, depth: int, seen: frozenset
    ) -> list[str]:
        out: list[str] = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Return):
                out += self._text(node.value, module, depth, seen)
            elif isinstance(node, ast.Raise):
                out += self._text(node.exc, module, depth, seen)
        return out


def _enclosing_functions(tree: ast.Module) -> dict[int, str]:
    """Line number -> the qualified name of the INNERMOST scope containing it.

    Innermost rather than outermost (KYO-83): a raise inside a closure belongs to the
    closure, so adding a second closure to the same host function does not renumber
    the first one's sites. Classes are part of the path too, so ``C.m`` and a
    module-level ``m`` cannot collide.
    """
    owner: dict[int, str] = {}

    def visit(node: ast.AST, prefix: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            path = prefix
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                path = (*prefix, child.name)
                # Depth-first and assigning (not setdefault): a nested scope is visited
                # after its host and overwrites it, which is what makes this innermost.
                for line in range(child.lineno, (child.end_lineno or child.lineno) + 1):
                    owner[line] = ".".join(path)
            visit(child, path)

    visit(tree, ())
    return owner


@lru_cache(maxsize=1)
def deliberate_error_sites() -> tuple[ErrorSite, ...]:
    """Every ``raise`` of a deliberate error in ``src/kyosei/``, in source order.

    This is the whole surface a consumer can be shown a message from: ``kyo``'s CLI
    catches :class:`KyoseiError` in exactly one place (``cli.main``), prints it, and
    returns its ``exit_code``. Nothing else reaches stderr as a failure.
    """
    return sites_in(SRC_DIR)


def sites_in(directory: Path) -> tuple[ErrorSite, ...]:
    """The walk itself, against any tree (KYO-83).

    Taking a directory rather than reading :data:`SRC_DIR` is what lets the keying
    tests below make a real edit to a real copy of ``src/kyosei/`` and re-derive the
    keys from what is on disk afterwards, instead of asserting a property of the
    scheme by restating it.

    Source order matters here in a way it did not when the key was a line number:
    :attr:`ErrorSite.ordinal` is a position, so the raises of a module are sorted by
    line before they are numbered rather than taken in ``ast.walk``'s breadth-first
    order.
    """
    index = _SourceIndex(directory)
    sites: list[ErrorSite] = []
    for module, tree in sorted(index.trees.items()):
        owners = _enclosing_functions(tree)
        raises = sorted(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Raise) and node.exc is not None
            ),
            key=lambda node: (node.lineno, node.col_offset),
        )
        seen_in_function: dict[str, int] = {}
        for node in raises:
            called = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            name = called.id if isinstance(called, ast.Name) else getattr(called, "attr", None)
            if name not in ERROR_CLASS_NAMES:
                continue
            function = owners.get(node.lineno, "(module level)")
            ordinal = seen_in_function.get(function, 0)
            seen_in_function[function] = ordinal + 1
            text = index.message_text(node.exc, module)
            sites.append(
                ErrorSite(
                    module=f"{module}.py",
                    function=function,
                    ordinal=ordinal,
                    lineno=node.lineno,
                    end_lineno=node.end_lineno or node.lineno,
                    remedies=tuple(sorted(set(_SOURCE_REMEDY.findall(text)))),
                    recorded=shell_remedies(text) + imperative_remedies(text),
                )
            )
    return tuple(sites)


# --------------------------------------------------------------------- KYO-187
#
# THE SHAPE THE WALK ABOVE CANNOT SEE, AND WHY IT IS REFUSED RATHER THAN FOLLOWED.
#
# :func:`sites_in` matches a raise BY CLASS NAME ON THE RAISE NODE. So
#
#     error = KyoseiError("...")
#     error.data = {...}
#     raise error
#
# matches nothing: the node's ``exc`` is a ``Name``, the name is not in
# :data:`ERROR_CLASS_NAMES`, and the site is skipped. It is not reported as
# unclassified — the classification gate quantifies over what this walk FOUND — so a
# new site written that way ships off the surface entirely, free to name a stale or
# destructive remedy with nothing red. That is a hole in the one check whose entire
# purpose is to make that impossible.
#
# KYO-181 is the case that proves the gap rather than closing it. Its first draft
# raised a `.data`-assigned local, and three tests went red — but they fired because
# ``RECORDED`` still held a key for a site the walk could no longer find. Detection came
# from a STALE TABLE ENTRY LEFT BY A REFACTOR. Had the refusal been a new function
# rather than a rewrite of an already-classified one, nothing would have said a word.
#
# THE FORK, and why this half of it. Either teach the walk to follow ``raise <name>``
# back to its assignment, keeping the pattern legal, or forbid the pattern and require a
# class that carries its payload as required keyword arguments.
#
#   * The codebase had already chosen. KYO-178 built :class:`SchemaMismatch` that way
#     and KYO-181 converted its own site to ``errors.ExportSchemaAhead`` rather than
#     leave a ``raise <name>`` behind. The five sites this record found were the tail of
#     that migration, not a competing convention.
#   * A follower sees the shapes its author imagined. ``raise error`` after
#     ``error = KyoseiError(...)``, yes; ``raise make_refusal(...)``'s result bound in a
#     helper, ``raise errors[0]``, or a payload assigned in one branch of an ``if``, no.
#     The value of this check is that a new site CANNOT leave the surface unseen, and a
#     heuristic whose coverage is "the patterns we thought of" cannot deliver that — the
#     same reasoning KYO-89 recorded against a path-pattern lint.
#   * Refusal is total and needs no vocabulary: every ``raise`` whose exception is not
#     written as a call goes red, whatever the expression is.
#
# NO ALLOWLIST, and that is a measurement rather than an oversight. Two shapes could
# have needed one. A bare ``raise`` inside an ``except`` block re-raises the active
# exception and is legitimate; it has no ``exc`` at all, so it is outside what this
# refuses by construction, not by exemption. ``raise e`` inside ``except ... as e:`` is
# the shape that would have needed a real entry — it names a variable and is a
# legitimate re-raise — and an AST scan of ``src/kyosei/`` found zero of them. So the
# allowlist is empty, and it is empty because nothing is in it.


def raises_that_hide_their_class(directory: Path) -> tuple[str, ...]:
    """Every ``raise`` in ``directory`` whose exception is not written as a call.

    Over the same file set :class:`_SourceIndex` reads, deliberately: a module the
    remedy walk never opens cannot hide a site from it, and deriving both from one
    enumerator is what keeps that true if the enumeration ever changes.

    ``raise`` with no expression — the bare re-raise — is not a site at all and is not
    reported. Everything else that is not a ``Call`` is: a ``Name``, an ``Attribute``,
    a subscript. The predicate is "the class is not written here", which is exactly the
    condition under which :func:`sites_in` cannot name it.
    """
    index = _SourceIndex(directory)
    found: list[str] = []
    for module, tree in sorted(index.trees.items()):
        owners = _enclosing_functions(tree)
        for node in sorted(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Raise)
                and n.exc is not None
                and not isinstance(n.exc, ast.Call)
            ),
            key=lambda n: (n.lineno, n.col_offset),
        ):
            function = owners.get(node.lineno, "(module level)")
            found.append(f"{module}.py:{function}: raise {ast.unparse(node.exc)}")
    return tuple(found)


def remedy_naming_error_sites() -> tuple[ErrorSite, ...]:
    """Every site whose message names something a reader could act on (KYO-84).

    The candidate surface: the union of the three recognisers above, before the
    judgment in :data:`NOT_A_REMEDY` takes the quoted-command-that-failed sites back
    out. :func:`sites_that_name_a_remedy` is the denominator; this is what the
    classification gate quantifies over, and it is deliberately the larger of the two
    so that nothing reaches the tables' notice only by the tables' own permission.
    """
    return tuple(site for site in deliberate_error_sites() if site.names_a_remedy)


def runnable_remedy_sites() -> tuple[ErrorSite, ...]:
    """The half of the surface this check has the vocabulary to execute.

    KYO-77's whole surface, and still the only part :func:`check_remedies_apply` can
    say anything about: a `kyo` command can be run in-process against a provoked store
    and the store re-read afterwards. Everything else can be recorded and not run.
    """
    return tuple(site for site in remedy_naming_error_sites() if site.remedies)


# ----------------------------------------------------- KYO-77: which site fired
#
# "The original error is gone" needs a definition sharper than "stderr changed", and
# sharper than "the text no longer appears" — ids and paths make message text a poor
# identity, and a store that fails a *second* way after a remedy runs has not had the
# first failure cleared. The identity used here is the raise site itself: run the
# provoking command again and require that this particular `raise` does not fire.
#
# Getting it needs the construction frame, because `cli.main` catches the exception
# and prints it, discarding the traceback. :class:`KyoseiError` has no ``__init__`` of
# its own, so wrapping it here catches every subclass, including the two that build
# their own message and delegate up (``ContractViolation``, ``DoctorFailure``) — which
# is why the walk skips frames inside ``errors.py`` rather than taking the caller.

#: module STEM -> the line span of each raise site and the key it belongs to. Keyed by
#: stem because that is what a frame's filename gives (``Path(...).stem``); KYO-77 keyed
#: it by ``"store.py"`` and looked it up by ``"store"``, so the span table never
#: matched and every lookup fell through to the line-number fallback below. That was
#: invisible while the key WAS the line number and the fallback reconstructed it
#: exactly. Under KYO-83's keys it would mean no provoked site ever matched, so the
#: spans have to carry their weight now.
_SPANS: dict[str, list[tuple[int, int, str]]] = {}
for _site in deliberate_error_sites():
    _SPANS.setdefault(_site.module.removesuffix(".py"), []).append(
        (_site.lineno, _site.end_lineno, _site.key)
    )


def _site_of_frame() -> str | None:
    """The key of the ``raise`` currently being executed, from the live stack."""
    frame = sys._getframe(1)
    while frame is not None:
        path = Path(frame.f_code.co_filename)
        if path.parent == SRC_DIR and path.name != "errors.py":
            for lo, hi, key in _SPANS.get(path.stem, ()):
                if lo <= frame.f_lineno <= hi:
                    return key
            # Constructed outside any raise this walk found. Deliberately not shaped
            # like a key, so it can only ever be reported, never matched.
            return f"(no known raise site: {path.name} line {frame.f_lineno})"
        frame = frame.f_back
    return None


@contextlib.contextmanager
def watching_error_sites() -> Iterator[list[str]]:
    """Record the site of every deliberate error raised inside the block."""
    reached: list[str] = []
    original = KyoseiError.__init__

    def recording(self, *args, **kwargs):  # noqa: ANN001, ANN202
        original(self, *args, **kwargs)
        key = _site_of_frame()
        if key is not None:
            reached.append(key)

    KyoseiError.__init__ = recording
    try:
        yield reached
    finally:
        KyoseiError.__init__ = original


# --------------------------------------------------- KYO-77: provoking a site


@dataclass
class Provocation:
    """A store in the state that reaches one error site, plus the way back to it."""

    root: Path  #: what a remedy's ``--root`` must point at
    command: tuple[str, ...]  #: the `kyo` invocation that fails here, without `kyo`

    def run(self) -> tuple[Run, list[str]]:
        with watching_error_sites() as reached:
            result = invoke(["--root", str(self.root), *self.command])
        return result, reached


#: A metavariable in a remedy: ``<id>``, ``FILE``, ``DIR``, or the ellipsis a message
#: uses for "and the rest of the flags". A remedy carrying one cannot be run verbatim;
#: the site's :class:`Expectation` has to say what it stands for in *this* state, which
#: is the point — a binding is a claim about the provoking state, and running the bound
#: command is what tests whether the remedy applies to it.
_METAVARIABLE = re.compile(r"^(<[^>]*>|\.\.\.|[A-Z][A-Z_-]*)$")


def bind_remedy(remedy: str, bindings: dict[str, str]) -> tuple[list[str] | None, list[str]]:
    """``(argv, unbound)`` — argv is ``None`` when a metavariable has no binding."""
    try:
        tokens = shlex.split(remedy)
    except ValueError:  # an unbalanced quote in prose is not a command line
        return None, [remedy]
    argv: list[str] = []
    unbound: list[str] = []
    for token in tokens:
        if _METAVARIABLE.match(token):
            if token in bindings:
                argv.extend(shlex.split(bindings[token]))
            else:
                unbound.append(token)
        else:
            argv.append(token)
    return (None, unbound) if unbound else (argv, [])


@dataclass(frozen=True)
class Expectation:
    """How to reach one error site, and how to read what its message names.

    ``provoke`` builds the state from scratch every time it is called: each remedy is
    run against its own fresh provocation, so a message naming two remedies has both
    of them tested from the state that produced the error rather than the second one
    tested from whatever the first left behind.
    """

    provoke: Callable[[Path], Provocation]

    #: What a metavariable in this site's message stands for in the state ``provoke``
    #: builds. ``{"<id>": "TST-1"}``, ``{"FILE": "/tmp/x"}``. A binding is written here
    #: rather than guessed because only the provoker knows the state.
    bindings: dict[str, str] = field(default_factory=dict)

    #: Commands this message *mentions* without offering: the verb it is explaining
    #: ("`kyo flag collapse` takes no flag id"), a place ids appear ("`kyo show
    #: <record>`"), a command that will refuse too and says so. Keyed by the command as
    #: the message spells it, valued by why it is not an offer. Both directions are
    #: checked — see :func:`check_remedies_apply`.
    descriptive: dict[str, str] = field(default_factory=dict)

    #: Whether re-running the provoking command is a fair test of "the error is gone".
    #: True for a failure about STATE — a missing database, a drifted schema version, a
    #: criterion that cannot be restated: the remedy changes the state, the same command
    #: is what the consumer runs next, and it must now succeed. False for a failure about
    #: the INVOCATION — `kyo edit` with no flags, `kyo verify --verdict nonsense`: there
    #: the remedy is a *different* command that reaches the goal, and re-running the
    #: malformed one would fail forever however good the remedy is. Where it is False the
    #: check asserts only that the remedy runs from this state — still the half that
    #: catches KYO-73, whose named remedy exited non-zero.
    clears_by_rerun: bool = True

    #: Files a binding needs to point at (``name -> contents``), written into a scratch
    #: directory before anything runs. A binding value may spell ``{scratch}/name`` to
    #: reach one. Several remedies here take a ``--...-file FILE``, and law 5 is why:
    #: prose enters this store through a file or stdin and never as a shell argument, so
    #: "run the remedy as written" means having a file for it to read.
    scratch_files: dict[str, str] = field(default_factory=dict)


@dataclass
class SiteResult:
    """What the check learned about one site. The unit the coverage figure counts."""

    key: str
    named: tuple[str, ...] = ()
    verified: tuple[str, ...] = ()  #: remedies run from the provoking state that cleared it
    mentions: tuple[str, ...] = ()  #: classified as descriptive, not run
    failures: tuple[str, ...] = ()


def check_remedies_apply(key: str, expectation: Expectation, tmp_path: Path) -> SiteResult:
    """Run every remedy this site's message offers, from the state that produced it.

    The whole of KYO-77.c1, in one function, deliberately returning failures rather
    than asserting them so that KYO-77.c3 can point it at a reconstructed regression
    and read what it says.

    Note where the remedies come from: :func:`named_remedies` reading the stderr the
    run actually produced. Not the source, not ``ErrorSite.remedies`` (which is a
    static over-approximation used only to size the surface), and not a list kept in
    this file. A message that changes what it names changes what this runs.
    """
    result = SiteResult(key=key)
    failures: list[str] = []

    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    for name, contents in expectation.scratch_files.items():
        (scratch / name).write_text(contents, encoding="utf-8")
    bindings = {
        token: value.format(scratch=scratch) for token, value in expectation.bindings.items()
    }

    provocation = expectation.provoke(tmp_path / "provoked")
    failed, reached = provocation.run()
    if failed.code == 0:
        return SiteResult(key, failures=(f"the provoker no longer fails: {failed}",))
    if key not in reached:
        return SiteResult(
            key, failures=(f"the provoker reached {reached or '(no deliberate error)'}, not {key}",)
        )

    named = named_remedies(failed.err)
    result.named = tuple(named)
    if not named:
        # Not a failure. ``ErrorSite.remedies`` unions every branch of every helper the
        # message could reach, so a site can be listed on the surface and still, on this
        # particular state, name nothing to run. Recorded with nothing verified, which
        # is what the coverage report then says about it.
        return result

    stale = set(expectation.descriptive) - set(named)
    if stale:
        failures.append(
            f"classified as mentions but no longer in the message: {sorted(stale)} — "
            f"the classification has gone stale:\n{failed.err}"
        )

    verified: list[str] = []
    mentions: list[str] = []
    for index, remedy in enumerate(named):
        argv, unbound = bind_remedy(remedy, bindings)
        if remedy in expectation.descriptive:
            mentions.append(remedy)
            if argv is None:
                continue
            # The other direction: a mention that starts clearing the error is a remedy
            # this check is declining to verify, and the classification has to be
            # revisited rather than left to hide it.
            if not expectation.clears_by_rerun:
                continue
            fresh = expectation.provoke(tmp_path / f"mention-{index}")
            ran = invoke(["--root", str(fresh.root), *argv[1:]])
            if ran.code == 0 and key not in fresh.run()[1]:
                failures.append(
                    f"`{remedy}` is classified as a mention "
                    f"({expectation.descriptive[remedy]!r}) but clears {key} when run"
                )
            continue
        if argv is None:
            failures.append(
                f"`{remedy}` carries {unbound}, which this check cannot bind — give the site "
                f"a binding for it, or classify it as a mention with a reason"
            )
            continue
        fresh = expectation.provoke(tmp_path / f"remedy-{index}")
        ran = invoke(["--root", str(fresh.root), *argv[1:]])
        if ran.code != 0:
            failures.append(f"`{remedy}` is offered by {key} and exits {ran.code}: {ran.err}")
            continue
        if not expectation.clears_by_rerun:
            verified.append(remedy)
            continue
        after, after_reached = fresh.run()
        if key in after_reached:
            failures.append(
                f"`{remedy}` is offered by {key}, exits 0, and leaves the same error: {after.err}"
            )
            continue
        verified.append(remedy)

    result.verified = tuple(verified)
    result.mentions = tuple(mentions)
    result.failures = tuple(failures)
    return result


# ------------------------------------------------- KYO-77: the provoker table
#
# The expensive half, as the KYO-73.d1 worker said it would be. Each entry is a
# function that builds a store in the state one error site fires from — from scratch,
# every call, because each remedy is tested against its own copy.


def _new_project(at: Path, *, prefix: str = "TST") -> Path:
    at.mkdir(parents=True, exist_ok=True)
    root = at / "proj"
    result = invoke(["init", str(root), "--in-repo", "--prefix", prefix])
    assert result.code == 0, result
    return root


def _runner(root: Path):
    def run(*args: str, stdin: str | None = None) -> Run:
        return invoke(["--root", str(root), *args], stdin=stdin)

    run.project = root
    return run


def _project_with_a_record(at: Path) -> tuple[Path, str]:
    root = _new_project(at)
    record = new_record(_runner(root), criteria=["a claim"])
    return root, record


def _a_finished_record_being_dispatched(at: Path) -> Provocation:
    """KYO-295: `kyo dispatch` on a record whose every criterion is settled — the
    class `kyo next` has withheld since KYO-87 and this command handed out anyway.

    Provoked at the VERIFIED rung rather than at ``open``, which is a statement about
    the remedy and not a convenience. The class names the move that lets it go *from
    where the record stands* (KYO-173), so from ``open`` it names the verified rung
    and the record is finished again one rung higher — the listing's own two-step,
    which the refusal inherits verbatim because it prints the listing's string. At
    the verified rung the named move is the one that clears the refusal outright,
    which is the single-command shape this check has vocabulary for.
    """
    root = _new_project(at)
    run = _runner(root)
    git_init(root)
    record = new_record(run, criteria=["the thing works"])
    verdict = run("verify", f"{record}.c1", "--verdict", "met", "--method", "reasoned-about")
    assert verdict.code == 0, verdict
    for status in ("working", "built", "verified"):
        assert run("move", record, status).code == 0
    return Provocation(root, ("dispatch", record))


def _a_blocked_record_being_dispatched(at: Path) -> Provocation:
    """KYO-570: `kyo dispatch` on a record an unsettled `blocks` source holds back --
    the class `kyo next` withholds under "blocked". The remedy run here is the edge's
    retraction, which the message spells with the real link id."""
    root = _new_project(at)
    run = _runner(root)
    git_init(root)
    blocker = new_record(run, title="The blocker", criteria=["first"])
    blocked = new_record(run, title="The blocked one", criteria=["second"])
    assert run("link", blocker, "blocks", blocked).code == 0
    return Provocation(root, ("dispatch", blocked))


def _a_record_redispatched_into_its_own_running_worktree(at: Path) -> Provocation:
    """KYO-411: a second `kyo dispatch` of a record whose first dispatch is still
    running and holds the same derived worktree (`<worktrees>/kyo-x`)."""
    root = _new_project(at)
    run = _runner(root)
    git_init(root)
    record = new_record(run, criteria=["the thing works"])
    # KYO-485: --no-create-worktree, because every provocation here shares one
    # $KYOSEI_HOME and one project name, so a worktree CREATED by an earlier
    # provocation would sit at this one's derived path and draw the existing-path
    # refusal instead. Creation with this collision is tests/test_dispatch_creates_worktree.py.
    first = run("dispatch", record, "--no-create-worktree", "--branch", "kyo-x")
    assert first.code == 0, first
    return Provocation(root, ("dispatch", record, "--no-create-worktree", "--branch", "kyo-x"))


def _a_brief_out_into_a_missing_directory(at: Path) -> Provocation:
    """KYO-411: `--brief-out` naming a file in a directory that does not exist."""
    root = _new_project(at)
    run = _runner(root)
    git_init(root)
    record = new_record(run, criteria=["the thing works"])
    target = str(root / "no-such-directory" / "brief.md")
    return Provocation(
        root,
        ("dispatch", record, "--no-create-worktree", "--branch", "kyo-x", "--brief-out", target),
    )


def _missing_database(at: Path) -> Provocation:
    root, _ = _project_with_a_record(at)
    (root / "kyosei" / "kyosei.db").unlink()
    return Provocation(root, ("list",))


def _db_without_a_meta_table(at: Path) -> Provocation:
    root, _ = _project_with_a_record(at)
    db = root / "kyosei" / "kyosei.db"
    db.unlink()
    sqlite3.connect(db).close()
    return Provocation(root, ("list",))


def _db_without_a_schema_version_row(at: Path) -> Provocation:
    root, _ = _project_with_a_record(at)
    conn = sqlite3.connect(root / "kyosei" / "kyosei.db")
    conn.execute("DELETE FROM meta WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    return Provocation(root, ("list",))


def _db_from_an_earlier_schema(at: Path) -> Provocation:
    root, _ = _project_with_a_record(at)
    _stamp_db_schema_version(root, SCHEMA_VERSION - 1)
    return Provocation(root, ("list",))


def _config_and_db_disagree(at: Path) -> Provocation:
    root, _ = _project_with_a_record(at)
    _stamp_config_schema_version(root, SCHEMA_VERSION + 5)
    return Provocation(root, ("list",))


def _kinds_yml_is_gone(at: Path) -> Provocation:
    root, _ = _project_with_a_record(at)
    kinds_yml(root).unlink()
    return Provocation(root, ("new", "bug", "--title", "a bug"))


def _not_a_git_repository(at: Path) -> Provocation:
    root, record = _project_with_a_record(at)
    return Provocation(root, ("dispatch", record))


def _detached_head(at: Path) -> Provocation:
    root, _ = _project_with_a_record(at)
    git_init(root)
    subprocess.run(
        ["git", "checkout", "-q", "--detach", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return Provocation(root, ("check-branch",))


# KYO-148 adds four sites, three of which name flags rather than commands and are in
# RECORDED. Only this one is provoked, and one of the four is worth calling out: the
# `kyo gate` payload refusal is the worked example, in the note above
# :data:`REMEDY_VERBS`, of a remedy stated as a REQUIREMENT and therefore invisible to
# this predicate. Its raise did not move; the sentence gained an imperative clause
# ("Pass --do TEXT ... or --do-file FILE") naming the file form that now exists, so
# recogniser 3 sees it and it is classified rather than staying silently uncounted.
# What put it on the surface was giving the reader a way to succeed, which is the thing
# this whole check exists to be able to see.


def _redo_on_a_gate_that_already_cleared(at: Path) -> Provocation:
    root, record = _project_with_a_record(at)
    kyo = _runner(root)
    gated = kyo("gate", record, "awaiting:human-review", "--do", "look at the banner", "--json")
    gate_id = gated.json["gate_id"]
    assert kyo("ungate", gate_id, "--why", "looked at it").code == 0
    return Provocation(root, ("redo", gate_id, "--do", "look at something else"))


def _install_agents_with_no_type(at: Path) -> Provocation:
    """KYO-607: `kyo install-agents` naming no agent type."""
    return Provocation(_new_project(at), ("install-agents",))


def _edit_with_nothing_to_change(at: Path) -> Provocation:
    root, record = _project_with_a_record(at)
    return Provocation(root, ("edit", record))


def _since_note_naming_an_unknown_note(at: Path) -> Provocation:
    """KYO-498: `kyo new reminder --since-note` naming a shift note this store lacks."""
    root = _new_project(at)
    return Provocation(root, ("new", "reminder", "--title", "an item", "--since-note", "TST-S99"))


def _since_note_naming_a_later_note(at: Path) -> Provocation:
    """KYO-498: a carried record filed before the note its `--since-note` names."""
    root = _new_project(at)
    run = _runner(root)
    filed = run("new", "reminder", "--title", "an old item", "--json")
    assert filed.code == 0, filed
    record = filed.json["record"]["id"]
    conn = sqlite3.connect(root / "kyosei" / "kyosei.db")
    conn.execute(
        "UPDATE records SET created_at = '2000-01-01T00:00:00.000Z' WHERE id = ?", (record,)
    )
    conn.commit()
    conn.close()
    noted = run("note", "--shift", "--file", "-", "--json", stdin="written after it")
    assert noted.code == 0, noted
    return Provocation(root, ("edit", record, "--since-note", noted.json["shift_note_id"]))


def _decision_filed_without_evidence(at: Path) -> Provocation:
    """KYO-521: `kyo new decision` with no `--evidence-file`, in a store holding a
    proposal, so the spawn route the refusal names has a real source to bind."""
    root = _new_project(at)
    run = _runner(root)
    new_record(run, kind="proposal", title="Pick a store", criteria=["a store is chosen"])
    return Provocation(root, ("new", "decision", "--title", "use SQLite"))


def _decision_spawned_from_a_reminder_without_evidence(at: Path) -> Provocation:
    """KYO-522: `kyo spawn <reminder> --kind decision` with no `--evidence-file`."""
    root = _new_project(at)
    run = _runner(root)
    reminder = new_record(run, kind="reminder", title="Ask about the cache")
    return Provocation(
        root, ("spawn", reminder, "--kind", "decision", "--title", "keep the cache")
    )


def _rekinding_a_plan_that_has_members(at: Path) -> Provocation:
    """KYO-509: `kyo edit <plan> --kind task` while a record still names it as its plan."""
    root = _new_project(at)
    run = _runner(root)
    plan = new_record(run, kind="plan")
    member = new_record(run, kind="task", criteria=["a claim"])
    joined = run("edit", member, "--plan", plan)
    assert joined.code == 0, joined
    return Provocation(root, ("edit", plan, "--kind", "task"))


def _edit_setting_a_verified_state_directly(at: Path) -> Provocation:
    root, record = _project_with_a_record(at)
    return Provocation(root, ("edit", record, "--criterion", f"{record}.c1", "--state", "met"))


def _verify_with_an_unknown_verdict(at: Path) -> Provocation:
    root, record = _project_with_a_record(at)
    return Provocation(
        root, ("verify", f"{record}.c1", "--verdict", "probably", "--method", "reasoned-about")
    )


# KYO-198. Three sites arrived with `kyo verify --from-report`; all three are
# invocation failures rather than state failures, except the middle one, which is the
# only genuinely stateful refusal of the three and the one KYO-198.c3 is about.


def _verify_missing_one_of_its_three_arguments(at: Path) -> Provocation:
    root, record = _project_with_a_record(at)
    return Provocation(root, ("verify", f"{record}.c1", "--verdict", "met"))


def _a_dispatch_that_has_not_reported(at: Path) -> Provocation:
    root, record = _project_with_a_record(at)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-q", "-m", "work")
    dispatched = _runner(root)("dispatch", record, "--json")
    assert dispatched.code == 0, dispatched
    return Provocation(root, ("verify", "--from-report", dispatched.json["dispatch_id"]))


def _from_report_beside_a_typed_verdict(at: Path) -> Provocation:
    root, record = _project_with_a_record(at)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-q", "-m", "work")
    kyo = _runner(root)
    dispatched = kyo("dispatch", record, "--json")
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    report = at / "worker-report.json"
    report.write_text(
        json.dumps(
            {
                "dispatch_id": dispatch_id,
                "record_id": record,
                "worked_from_sha": dispatched.json["worked_from_sha"],
                "criteria_verdicts": [
                    {
                        "criterion_id": f"{record}.c1",
                        "verdict": "met",
                        "method": "reasoned-about",
                    }
                ],
                "premise_false": [],
                "disagreements": [],
                "corrected_numbers": [],
                "commits": [],
            }
        ),
        encoding="utf-8",
    )
    ingested = kyo("report", dispatch_id, "--file", str(report))
    assert ingested.code == 0, ingested
    return Provocation(
        root, ("verify", "--from-report", dispatch_id, "--verdict", "met")
    )


def _restating_a_criterion_that_is_already_void(at: Path) -> Provocation:
    root, record = _project_with_a_record(at)
    kyo = _runner(root)
    voided = kyo(
        "edit", record, "--criterion", f"{record}.c1", "--state", "void-withdrawn", "--why", "moot"
    )
    assert voided.code == 0, voided
    text = at / "restatement.txt"
    text.write_text("a sharper claim\n", encoding="utf-8")
    return Provocation(root, ("restate", f"{record}.c1", "--text-file", str(text)))


def _correcting_an_attested_criterion(at: Path) -> Provocation:
    root, record = _project_with_a_record(at)
    kyo = _runner(root)
    verified = kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "reasoned-about", "--why", "yes"
    )
    assert verified.code == 0, verified
    text = at / "correction.txt"
    text.write_text("a sharper claim\n", encoding="utf-8")
    return Provocation(
        root, ("edit", record, "--criterion", f"{record}.c1", "--text-file", str(text))
    )


def _voiding_a_work_record(at: Path) -> Provocation:
    root, record = _project_with_a_record(at)
    return Provocation(root, ("void", record, "--why", "filed in error"))


def _redacting_something_that_is_not_redactable(at: Path) -> Provocation:
    root, record = _project_with_a_record(at)
    return Provocation(root, ("redact", record, "--why", "a secret slipped in"))


def _redacting_again_with_a_different_reason(at: Path) -> Provocation:
    """KYO-405: a rerun of `kyo redact` is a retry only under the reason already
    recorded; a new reason is refused, and the refusal names the retry."""
    root, record = _project_with_a_record(at)
    evidence = at / "ev.txt"
    evidence.write_text("consumer id 4429117 seen in the queue\n")
    runner = _runner(root)
    evidence_id = runner("attach", record, "--file", str(evidence), "--json").json[
        "evidence_id"
    ]
    assert runner("redact", evidence_id, "--why", "first").code == 0
    return Provocation(root, ("redact", evidence_id, "--why", "second"))


def _noting_against_a_record_id(at: Path) -> Provocation:
    # KYO-131. The invocation a controller writes after meeting `RECORD.nN` in
    # `kyo redact`'s vocabulary: `kyo note <record> --file ...`, on the reasonable theory
    # that the command called `note` is the one that writes notes. It is not — `kyo note`
    # writes the shift log — and until KYO-131 argparse answered with "the following
    # arguments are required: --shift", which names no way forward and does not so much
    # as acknowledge the id it was handed. A consumer took that at face value and filed
    # RECORD.nN as an id shape for an object nothing could produce. The record has to be
    # real here: the remedy the message now names is `kyo edit <that same id>
    # --note-file`, and this check runs it.
    root, record = _project_with_a_record(at)
    return Provocation(root, ("note", record, "--file", "-"))


def _noting_without_saying_which_log(at: Path) -> Provocation:
    # KYO-131, the same refusal reached without a record id: `--shift` omitted and
    # nothing to say what the caller meant instead. The message names the record-scoped
    # write path with a metavariable, because at this point the command has not been told
    # which record — if any — the caller had in mind.
    root, _ = _project_with_a_record(at)
    return Provocation(root, ("note", "--file", "-"))


def _showing_a_shift_note_that_does_not_exist(at: Path) -> Provocation:
    # KYO-94: `kyo show` dispatches on the PREFIX-SN id shape, so this store — which has a
    # record and no shift notes — reaches "no shift note", never "no record".
    root, _ = _project_with_a_record(at)
    return Provocation(root, ("show", "TST-S9"))


def _showing_a_brief_that_does_not_exist(at: Path) -> Provocation:
    # KYO-454: `kyo briefs <id>` for a brief run this store never recorded.
    root, _ = _project_with_a_record(at)
    return Provocation(root, ("briefs", "TST-B9"))


def _running_a_base_without_naming_one(at: Path) -> Provocation:
    # KYO-108: `kyo base run` with its name positional omitted. What this store's
    # bases.yml declares is irrelevant — the refusal is about the invocation and fires
    # before any base is looked up.
    root, _ = _project_with_a_record(at)
    return Provocation(root, ("base", "run"))


def _grouping_a_listing_instead_of_a_run(at: Path) -> Provocation:
    # KYO-120: `--group-by` aimed at `kyo base list`, which runs nothing and so has no
    # arrangement to override. A base has to be declared here, unlike the refusal above,
    # because the remedy this message names is a *run* and it has to have something to
    # run from this exact state.
    root, _ = _project_with_a_record(at)
    (root / "kyosei" / "bases.yml").write_text(
        "bases:\n"
        "  only:\n"
        "    summary: Every record.\n"
        "    source: records\n",
        encoding="utf-8",
    )
    return Provocation(root, ("base", "list", "--group-by", "kind"))


def _flag_resolve_without_a_flag_id(at: Path) -> Provocation:
    root, _ = _project_with_a_record(at)
    return Provocation(root, ("flag", "resolve"))


def _flag_collapse_with_a_flag_id(at: Path) -> Provocation:
    root, _ = _project_with_a_record(at)
    return Provocation(root, ("flag", "collapse", "TST-1.f1"))


def _reference_of_one_command_with_output(at: Path) -> Provocation:
    """KYO-221.c2. `--output` writes the whole reference; a command name narrows what is
    printed. Combined they would write one section over a file that holds the document,
    so they are refused together, and the refusal offers both of the things the caller
    might have meant — each a real invocation, which is what this check runs."""
    root = _new_project(at)
    return Provocation(root, ("reference", "show", "--output", str(at / "cli.md")))


def _changelog_since_neither_a_date_nor_a_commit(at: Path) -> Provocation:
    """KYO-226. `--since` takes a YYYY-MM-DD date or a commit the checkout can resolve.

    The refusal offers two things of different kinds, which is why this site carries a
    `descriptive` entry: `kyo changelog --all` is a real invocation that reaches the
    whole history the caller was asking a slice of, and `kyo version` is where they find
    a date to give it — informative, and no answer on its own to the invocation refused.
    """
    root = _new_project(at)
    return Provocation(root, ("changelog", "--since", "last-tuesday"))


def _flag_list_with_a_flag_id(at: Path) -> Provocation:
    # KYO-220. A foreign project's id deliberately, so the `kyo show <id>` the message
    # offers back is the caller's own spelling and classifiable as descriptive rather
    # than as a command this check should run against a store that has no such flag.
    root, _ = _project_with_a_record(at)
    return Provocation(root, ("flag", "list", "KYO-1.f1"))


def _flag_with_an_unknown_action(at: Path) -> Provocation:
    root, _ = _project_with_a_record(at)
    return Provocation(root, ("flag", "reopen", "TST-1.f1"))


def _flag_collapse_with_settled(at: Path) -> Provocation:
    # KYO-346: --settled belongs to `resolve`, not `collapse` — this names both
    # commands to say which is which, offering neither as something to run.
    root, _ = _project_with_a_record(at)
    return Provocation(root, ("flag", "collapse", "--settled"))


def _flag_list_with_settled(at: Path) -> Provocation:
    # KYO-346: same refusal, the other sibling action.
    root, _ = _project_with_a_record(at)
    return Provocation(root, ("flag", "list", "--settled"))


def _flag_resolve_settled_with_a_flag_id(at: Path) -> Provocation:
    # KYO-346.c3: `--settled` operates on the whole store, like `collapse`; a flag id
    # would read as "resolve just this one settled flag", which is not what it does.
    root, _ = _project_with_a_record(at)
    return Provocation(root, ("flag", "resolve", "TST-1.f1", "--settled"))


def _flag_resolve_settled_without_a_reason(at: Path) -> Provocation:
    # KYO-346.c3: `--settled` closes flags nobody judged individually, so unlike a
    # single `flag resolve` (where `--why` is optional) it demands one.
    root, _ = _project_with_a_record(at)
    return Provocation(root, ("flag", "resolve", "--settled"))


def _dispatch_close_without_an_id(at: Path) -> Provocation:
    root, _ = _project_with_a_record(at)
    return Provocation(root, ("dispatch", "close"))


def _moving_a_knowledge_record(at: Path) -> Provocation:
    root = _new_project(at)
    kyo = _runner(root)
    record = new_record(kyo, kind="decision", title="use SQLite", criteria=["a store is chosen"])
    return Provocation(root, ("move", record, "working"))


def _done_with_unsettled_criteria(at: Path) -> Provocation:
    root, record = _project_with_a_record(at)
    return Provocation(root, ("move", record, "done"))


def _built_with_no_criteria(at: Path) -> Provocation:
    """KYO-413.c1: a requires_criteria record with none, moved straight to a
    verification rung without passing through an active status."""
    root = _new_project(at)
    record = new_record(_runner(root))
    return Provocation(root, ("move", record, "built"))


def _done_over_an_open_gate(at: Path) -> Provocation:
    """KYO-490: a record whose one criterion is met, moved to `done` with a gate still
    open. The remedy named is `kyo ungate`, which clears it on a re-run; `--force` is
    named as a flag on the caller's own command and so is not on this surface (its
    test is tests/test_settle_past_open_gate.py)."""
    root, record = _project_with_a_record(at)
    kyo = _runner(root)
    assert kyo("move", record, "working").code == 0
    verified = kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed")
    assert verified.code == 0, verified
    gated = kyo("gate", record, "awaiting:human-review")
    assert gated.code == 0, gated
    return Provocation(root, ("move", record, "done"))


def _verified_without_a_verdict(at: Path) -> Provocation:
    """KYO-546: a record whose one criterion has never been verified, moved straight to
    `verified`. No report is waiting, so `kyo verify` is the one
    remedy printed; the `kyo accept` line is tests/test_verified_requires_a_verdict.py's."""
    root, record = _project_with_a_record(at)
    return Provocation(root, ("move", record, "verified"))


def _done_with_no_met_criterion(at: Path) -> Provocation:
    root, record = _project_with_a_record(at)
    kyo = _runner(root)
    voided = kyo(
        "edit", record, "--criterion", f"{record}.c1", "--state", "void-withdrawn", "--why", "moot"
    )
    assert voided.code == 0, voided
    return Provocation(root, ("move", record, "done"))


def _project_with_an_open_docs_obligation(at: Path) -> tuple[Path, str, str]:
    """A store whose one record carries an open docs obligation (KYO-137)."""
    root, record, obligations = _project_with_open_docs_obligations(at)
    return root, record, obligations[0]


#: One document section per obligation wanted, all governing the same file, so a single
#: branch change raises exactly ``sections`` of them (KYO-153.c4: two sections governing
#: one file raise two obligations, deliberately).
_GOVERNING_SECTION = "## How the CLI is wired {n}\n\ngoverns: src/app.py\n\nProse.\n"


def _project_with_open_docs_obligations(
    at: Path, *, sections: int = 1
) -> tuple[Path, str, list[str]]:
    """A store whose one record carries ``sections`` open docs obligations (KYO-137).

    Built through the real path — a document that governs a file, a branch that changes
    it, a dispatch and a report — because the sites below are the docs layer's blocking
    gate, its verdict vocabulary and its set-settle refusals, and a hand-inserted row
    would prove the remedy against a state the mechanism cannot actually produce.

    ``sections`` is the KYO-159 half: the set-settle refusals need a set, and a set needs
    more than one obligation to be one. The extra sections govern the same file, which is
    exactly how a real multi-obligation document arises — the charter explains a mechanism
    where the README documents it, and one changed file puts a question to both.
    """
    root = _new_project(at)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "architecture.md").write_text(
        "# Architecture\n\n"
        + "\n".join(_GOVERNING_SECTION.format(n=n) for n in range(1, sections + 1)),
        encoding="utf-8",
    )
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    git_init(root)
    kyo = _runner(root)
    record = new_record(kyo, criteria=["the CLI is wired"])
    touches = root / "touches.txt"
    touches.write_text("src/app.py\n", encoding="utf-8")
    assert kyo("edit", record, "--touches-file", str(touches)).code == 0

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()

    base = git("rev-parse", "--abbrev-ref", "HEAD")
    git("checkout", "-q", "-b", "kyo-137")
    (root / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "work")
    sha = git("rev-parse", "HEAD")
    git("checkout", "-q", base)

    # KYO-485: the branch is made by hand above, so the dispatch must not create it.
    dispatch_id = kyo(
        "dispatch", record, "--no-create-worktree", "--branch", "kyo-137", "--json"
    ).json["dispatch_id"]
    report = root / "report.json"
    report.write_text(
        json.dumps(
            {
                "dispatch_id": dispatch_id,
                "record_id": record,
                "worked_from_sha": sha,
                "criteria_verdicts": [],
                "premise_false": [],
                "disagreements": [],
                "corrected_numbers": [],
                "commits": [],
            }
        ),
        encoding="utf-8",
    )
    ingested = kyo("report", dispatch_id, "--file", str(report), "--json")
    assert ingested.code == 0, ingested
    raised = ingested.json["docs_obligations"]["raised"]
    assert len(raised) == sections, ingested
    verify = kyo(
        "verify", f"{record}.c1", "--verdict", "met", "--method", "reasoned-about"
    )
    assert verify.code == 0, verify
    return root, record, [row["id"] for row in raised]


def _done_with_an_open_docs_obligation(at: Path) -> Provocation:
    root, record, _ = _project_with_an_open_docs_obligation(at)
    return Provocation(root, ("move", record, "done"))


def _settling_no_docs_obligation_at_all(at: Path) -> Provocation:
    """KYO-159: `kyo docs settle` with a verdict and no id to attach it to."""
    root, _ = _project_with_a_record(at)
    return Provocation(root, ("docs", "settle", "--verdict", "updated", "--method", "x"))


def _settling_all_with_no_record(at: Path) -> Provocation:
    """KYO-528.c2: `kyo docs settle --all` names no record to settle the open ones of."""
    root, _ = _project_with_a_record(at)
    return Provocation(
        root,
        ("docs", "settle", "--all", "--verdict", "updated", "--method", "x", "--why", "w"),
    )


def _settling_all_beside_an_obligation_id(at: Path) -> Provocation:
    root, record, obligation = _project_with_an_open_docs_obligation(at)
    return Provocation(
        root,
        ("docs", "settle", obligation, "--record", record, "--all", "--verdict", "updated",
         "--method", "x", "--why", "w"),
    )


def _settling_a_docs_obligation_with_an_unknown_verdict(at: Path) -> Provocation:
    root, _, obligation = _project_with_an_open_docs_obligation(at)
    return Provocation(
        root, ("docs", "settle", obligation, "--verdict", "probably-fine", "--method", "x")
    )


def _showing_fields_without_json(at: Path) -> Provocation:
    """KYO-420.c3: `kyo show --fields` narrows the --json document, and in text mode
    it is refused rather than silently ignored."""
    root, record = _project_with_a_record(at)
    return Provocation(root, ("show", record, "--fields", "status"))


def _previewing_docs_against_an_unknown_base(at: Path) -> Provocation:
    """KYO-199.c6: the one failure a worker actually hits — it was branched from
    something other than `main`, so the base it diffs against is not a ref here."""
    root, _, _ = _project_with_open_docs_obligations(at)
    return Provocation(root, ("docs", "preview", "--against", "no-such-base"))


def _a_stale_section_index(at: Path) -> Provocation:
    """KYO-502.c1: a set declares `section_status.index` and the committed file is stale
    (here, absent). The named remedy, `kyo docs index`, writes it; `--check` then passes."""
    root = _new_project(at)
    (root / "kyosei" / "documents.yml").write_text(
        "documents:\n  design:\n    summary: The design record.\n    globs:\n"
        "      - docs/design/**/*.md\n    section_status:\n"
        "      statuses: [current, amended, superseded]\n"
        "      index: docs/reference/design-sections.md\n",
        encoding="utf-8",
    )
    doc = root / "docs" / "design" / "a.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("# A\n\n## KYO-1\n\nWhy one.\n", encoding="utf-8")
    return Provocation(root, ("docs", "index", "--check"))


def _settling_docs_from_a_dispatch_that_has_not_reported(at: Path) -> Provocation:
    """KYO-199.c4: `kyo docs settle --from-report` before the report it reads exists.

    The docs half of `cmd_evidence.py:_report_verdicts#0`, and about STATE rather than
    the invocation for the same reason: `kyo report` gives the dispatch a report, and
    the same command then succeeds.
    """
    root, record = _project_with_a_record(at)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-q", "-m", "work")
    dispatched = _runner(root)("dispatch", record, "--json")
    assert dispatched.code == 0, dispatched
    return Provocation(
        root, ("docs", "settle", "--from-report", dispatched.json["dispatch_id"])
    )


def _settling_docs_from_a_report_beside_a_typed_verdict(at: Path) -> Provocation:
    """KYO-199.c4: a verdict typed beside --from-report is the retyping it removes."""
    root, record, _ = _project_with_open_docs_obligations(at)
    dispatch_id = _runner(root)("show", record, "--json").json["dispatches"][0]["id"]
    return Provocation(
        root,
        (
            "docs",
            "settle",
            "--from-report",
            dispatch_id,
            "--verdict",
            "updated",
            "--method",
            "x",
        ),
    )


def _settling_a_set_without_saying_what_it_covered(at: Path) -> Provocation:
    """KYO-159: two obligations, one act, and no --why saying what the review covered."""
    root, _, obligations = _project_with_open_docs_obligations(at, sections=2)
    return Provocation(
        root, ("docs", "settle", *obligations, "--verdict", "updated", "--method", "x")
    )


def _settling_a_set_one_of_whose_members_is_already_settled(at: Path) -> Provocation:
    """KYO-159: the all-or-nothing refusal, which is what keeps a set to one verdict."""
    root, _, obligations = _project_with_open_docs_obligations(at, sections=2)
    kyo = _runner(root)
    first = kyo("docs", "settle", obligations[0], "--verdict", "updated", "--method", "x")
    assert first.code == 0, first
    return Provocation(
        root,
        (
            "docs",
            "settle",
            *obligations,
            "--verdict",
            "not-affected",
            "--method",
            "x",
            "--why",
            "read both sections",
        ),
    )


def _init_over_an_existing_in_repo_store(at: Path) -> Provocation:
    root, _ = _project_with_a_record(at)
    return Provocation(root, ("init", str(root), "--in-repo"))


def _init_in_a_new_project_with_no_layout_chosen(at: Path) -> Provocation:
    """KYO-558: bare `kyo init` in a folder with no store, which writes nothing."""
    root = at / "fresh"
    root.mkdir(parents=True)
    return Provocation(root, ("init",))


def _init_split_over_an_existing_in_repo_store(at: Path) -> Provocation:
    root, _ = _project_with_a_record(at)
    return Provocation(root, ("init", str(root)))


def _dispatch_with_an_extra_argument(at: Path) -> Provocation:
    root, record = _project_with_a_record(at)
    git_init(root)
    opened = invoke(["--root", str(root), "dispatch", record])
    assert opened.code == 0, opened
    return Provocation(root, ("dispatch", record, f"{record}.d1"))


def _refreshing_over_an_export_ahead_database(at: Path) -> Provocation:
    """KYO-81's refusal: a counter the export holds and the database has not reached.

    Manufactured by hand rather than by two checkouts, the same way
    ``tests/test_export_refresh.py`` builds it — a pull is what produces this in life,
    and ``tests/test_split_store.py`` asserts the same refusal over the genuine
    article; here the state has to be exact and cheap.
    """
    root, _ = _project_with_a_record(at)
    allocations = root / "kyosei" / "export" / "_allocations.json"
    counters = json.loads(allocations.read_text(encoding="utf-8"))
    counters["event"] = int(counters["event"]) + 3
    allocations.write_text(
        json.dumps(counters, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return Provocation(root, ("export", "--refresh"))


def _dispatch_close_with_a_dispatch_open(at: Path) -> Provocation:
    root, record = _project_with_a_record(at)
    git_init(root)
    opened = invoke(["--root", str(root), "dispatch", record])
    assert opened.code == 0, opened
    return Provocation(root, ("dispatch", "close"))


def _terminated_no_report_with_a_record_id(at: Path) -> Provocation:
    """KYO-133: the flag vector of the spurious-dispatch trap. A controller wanting to
    close a running dispatch types the record id plus `--terminated-no-report --why
    ...` instead of `dispatch close <dispatch-id>` -- before the fix this silently
    froze a second dispatch (KYO-128.d2) instead of refusing."""
    root, record = _project_with_a_record(at)
    git_init(root)
    opened = invoke(["--root", str(root), "dispatch", record])
    assert opened.code == 0, opened
    return Provocation(root, ("dispatch", record, "--terminated-no-report", "--why", "oops"))


def _applying_a_touch_drift_that_was_never_checked(at: Path) -> Provocation:
    """KYO-147.c4. A dispatch with no declared branch and no ingested report has no
    body of evidence to compare its touches against, so `kyo edit --touches-from-drift`
    has no correction to store — and storing the record's existing touches back over
    themselves would report success about a comparison that never happened."""
    root, record = _project_with_a_record(at)
    git_init(root)
    dispatched = invoke(["--root", str(root), "dispatch", record, "--json"])
    assert dispatched.code == 0, dispatched
    return Provocation(
        root, ("edit", record, "--touches-from-drift", dispatched.json["dispatch_id"])
    )


def _report_amending_an_already_superseded_report(at: Path) -> Provocation:
    """KYO-90: r1 is ingested, r2 amends it, and a third report arrives still
    naming r1 — a correction written against an account that has itself already
    been replaced. Building the state end to end through the CLI is the point:
    the refusal only means anything if the two ingests before it really worked."""
    root, record = _project_with_a_record(at)
    git_init(root)
    dispatched = invoke(["--root", str(root), "dispatch", record, "--json"])
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]
    sha = dispatched.json["worked_from_sha"]

    def _file(name: str, **extra) -> str:
        path = root / name
        path.write_text(
            json.dumps(
                {
                    "dispatch_id": dispatch_id,
                    "record_id": record,
                    "worked_from_sha": sha,
                    "criteria_verdicts": [],
                    "premise_false": [],
                    "disagreements": [],
                    "corrected_numbers": [],
                    "commits": [],
                    **extra,
                }
            ),
            encoding="utf-8",
        )
        return str(path)

    first = invoke(
        ["--root", str(root), "report", dispatch_id, "--file", _file("r1.json"), "--json"]
    )
    assert first.code == 0, first
    amended = invoke(
        [
            "--root",
            str(root),
            "report",
            dispatch_id,
            "--file",
            _file("r2.json", amends=first.json["report_id"]),
        ]
    )
    assert amended.code == 0, amended
    stale = _file("r3.json", amends=first.json["report_id"])
    return Provocation(root, ("report", dispatch_id, "--file", stale))


def _report_that_does_not_conform_to_the_schema(at: Path) -> Provocation:
    """KYO-149: the ingest refusal a worker actually meets, and the one whose remedy
    used to be unreachable. Its message named ``templates/report.schema.json`` — a
    path present only in Kyosei's own source tree — so the reader was told to consult
    a document their machine did not have, by an error whose whole job is to say what
    to do next. It now names ``kyo report-schema``, which is a command they can run,
    and this entry is what proves the command runs from the state the error was
    raised in rather than merely appearing in the sentence."""
    root, record = _project_with_a_record(at)
    git_init(root)
    dispatched = invoke(["--root", str(root), "dispatch", record, "--json"])
    assert dispatched.code == 0, dispatched
    dispatch_id = dispatched.json["dispatch_id"]

    # Valid JSON, invalid report: every required key missing. The failure has to come
    # from schema validation and not from the JSON parser above it, or this provokes
    # a different site than the one being classified.
    path = root / "malformed.json"
    path.write_text(json.dumps({"dispatch_id": dispatch_id}), encoding="utf-8")
    return Provocation(root, ("report", dispatch_id, "--file", str(path)))


# ---------------------------------------------------------- KYO-77: the table
#
# Site -> how to reach it and how to read what it says. The keys are what
# :func:`deliberate_error_sites` produces, and
# ``test_every_remedy_naming_site_is_classified`` is the gate that keeps this table,
# :data:`UNREACHED` and :data:`FINDINGS` between them accounting for every site on the
# surface — a new remedy-naming error fails that test on the day it ships rather than
# quietly lowering a coverage figure nobody recomputed.

PROVOKED: dict[str, Expectation] = {
    # KYO-295. The one remedy here is the status move, and it is the listing's own
    # string — `kyo next` is mentioned to say WHERE this record already is, not
    # offered to run, and running it against this state prints the record under
    # "finished but not moved" rather than clearing anything.
    "cmd_dispatch.py:_refuse_a_finished_record#0": Expectation(
        _a_finished_record_being_dispatched,
        descriptive={
            "kyo next": (
                "named to say which listing already withholds this record, and off "
                "whose computation the refusal is raised; it reports, it does not move"
            )
        },
    ),
    # KYO-570. The offered remedy is the edge's retraction, by its real link id; `kyo
    # next` is named to say where the record is already withheld, as KYO-295's is.
    "cmd_dispatch.py:_refuse_a_blocked_record#0": Expectation(
        _a_blocked_record_being_dispatched,
        bindings={"TEXT": "'not a real dependency'"},
        descriptive={
            "kyo next": (
                "named to say which listing already withholds this record, and off "
                "whose computation the refusal is raised; it reports, it does not move"
            )
        },
    ),
    # KYO-411. `kyo show <id>` in both messages is where the running dispatch or the
    # brief is READ; it clears nothing. The remedies that do clear them (a different
    # --worktree, a writable --brief-out path) are the caller's re-invocations, proven
    # in tests/test_dispatch_lifecycle_gaps.py.
    "cmd_dispatch.py:_cmd_dispatch_create#7": Expectation(
        _a_record_redispatched_into_its_own_running_worktree,
        descriptive={
            "kyo show TST-1.d1": (
                "names where to read the dispatch that is already running; reading it "
                "clears nothing"
            )
        },
    ),
    "cmd_dispatch.py:_cmd_dispatch_create#8": Expectation(
        _a_brief_out_into_a_missing_directory,
        descriptive={
            "kyo show <dispatch-id>": (
                "the read-back path offered instead of --brief-out; it reads a brief, it "
                "does not make a path writable"
            )
        },
    ),
    "store.py:open_store#0": Expectation(_missing_database),
    "store.py:open_store#1": Expectation(
        _db_without_a_meta_table,
        descriptive={"kyo rebuild": "named to say it refuses here; --force is the offer"},
    ),
    "store.py:open_store#2": Expectation(
        _db_without_a_schema_version_row,
        descriptive={"kyo rebuild": "named to say it refuses here; --force is the offer"},
    ),
    "store.py:open_store#3": Expectation(
        _db_from_an_earlier_schema,
        descriptive={
            "kyo rebuild": "named to say it refuses here; --force is the offer",
            # KYO-216: the message now names the FULL sequence a schema bump asks
            # for, and these two are the second and third steps of it — offered to
            # run only after `kyo rebuild --force` has already fixed the database,
            # not against the state that raised this refusal. Run here, both open
            # the same still-mismatched store and fail for the identical reason
            # `kyo rebuild --force` exists to fix; that is not evidence the remedy
            # is wrong, it is evidence the ordering matters and this check has no
            # vocabulary for a sequence, only for one command per site.
            "kyo export --refresh": (
                "the third step of the sequence, after `kyo rebuild --force`; "
                "offered against the state that raised this refusal it fails the "
                "same way, because it opens the same mismatched store first"
            ),
            "kyo doctor": (
                "the fourth step of the sequence, confirming the first three; "
                "offered against the state that raised this refusal it fails the "
                "same way, because it opens the same mismatched store first"
            ),
        },
    ),
    # KYO-168 split the old symmetric `!=` mismatch into two raises. #3 is the BEHIND
    # direction and keeps KYO-5's message and the entry above it word for word; #4 is
    # the new AHEAD direction, which names no `kyo` command at all and so lives in
    # RECORDED below; the config/db drift that was #4 is #5 from here on. The
    # renumbering was predicted in KYO-168.n1 rather than discovered by the worker —
    # the KYO-147.m1 class: a file any change here drags in by a property of the file.
    "store.py:open_store#5": Expectation(_config_and_db_disagree),
    # KYO-82. Moved here out of FINDINGS, where KYO-77 put it on the day it found it:
    # this was the sixth instance of the KYO-73 class, and a word-for-word copy of the
    # fifth ("run `kyo init` or restore it", the sentence store.py raised for a missing
    # database). The message now offers nothing runnable, because nothing runnable
    # applies: kinds.yml is committed configuration, so the recovery is `git checkout`
    # of a tracked file or a copy of the packaged default, and the message says that
    # instead of reaching for the nearest command that exists. `kyo rebuild` is named
    # only to be ruled out, and the classification below is checked in both directions
    # — if the message stops saying it this goes stale and fails; if it ever starts
    # clearing the error, that is a remedy this check is declining to verify and it
    # fails too. What the message names INSTEAD of a command is checked by
    # ``test_the_recovery_named_for_a_missing_kinds_yml_actually_restores_it``, because
    # this machinery has vocabulary only for `kyo` commands.
    #
    # KYO-104 re-keyed it from ``kinds.py:load_kind_registry#0``: the kind registry moved
    # onto the shared loader, so the raise moved with it and the message generalised from
    # ``kinds.yml`` to whichever required registry is missing (there is one). This is the
    # third blind spot named above :class:`ErrorSite` — a raise moved between functions
    # looks deleted and re-added, the gate fails, and the entry is re-keyed by hand. The
    # judgment below is re-read and unchanged: the message is the same message.
    "registry.py:load#0": Expectation(
        _kinds_yml_is_gone,
        descriptive={
            "kyo rebuild": "named to be ruled out: it reconstructs the database from "
            "kyosei/export/ and has no source for this file"
        },
    ),
    "git_queries.py:_git_rev_parse_head#1": Expectation(
        _not_a_git_repository,
        descriptive={"kyo dispatch": "names the command being refused; the remedy is `git init`"},
    ),
    "git_queries.py:_git_current_branch#2": Expectation(
        _detached_head,
        descriptive={
            "kyo check-branch": "names the command being refused, with the flag it needs; no "
            "runnable line is spelled"
        },
    ),
    # KYO-148. The one payload site whose message names a `kyo` command; its three
    # siblings name flags instead and are in RECORDED. `clears_by_rerun=False`: this is
    # a failure about the INVOCATION, not about state — the gate stays cleared, so
    # re-running the same `redo` would fail forever however good the remedy is.
    "cmd_links.py:cmd_redo#1": Expectation(
        _redo_on_a_gate_that_already_cleared,
        clears_by_rerun=False,
        descriptive={
            "kyo gate": "names the verb that files a fresh obligation, not a runnable "
            "line: what the new gate must say is exactly what this command could not "
            "guess, which is why it refuses rather than rewriting the cleared one"
        },
    ),
    "cmd_records.py:cmd_edit#0": Expectation(
        _edit_with_nothing_to_change,
        clears_by_rerun=False,
        descriptive={"kyo edit --help": "a pointer at the flag list, not a command that edits"},
    ),
    # KYO-498. `kyo shift-notes` is where the ids are read, not a command that clears
    # anything: the remedy is the re-invocation naming a listed (or an earlier) note,
    # proved by tests/test_carried_since_note.py's refusal tests.
    "cmd_records.py:_checked_since_note#1": Expectation(
        _since_note_naming_an_unknown_note,
        clears_by_rerun=False,
        descriptive={
            "kyo shift-notes": "the read-only listing the note ids come from; the "
            "remedy is re-running with one of them"
        },
    ),
    "cmd_records.py:_checked_since_note#2": Expectation(
        _since_note_naming_a_later_note,
        clears_by_rerun=False,
        descriptive={
            "kyo shift-notes": "the read-only listing of notes with their times; the "
            "remedy is re-running with an earlier one, or without --since-note"
        },
    ),
    # KYO-521. The refusal's first remedy is the re-invocation with --evidence-file,
    # which tests/test_kyo521_reminder_and_decision.py runs; the spawn route it names is
    # run here, from a store whose proposal is TST-1.
    "cmd_records.py:_evidence_at_filing#0": Expectation(
        _decision_filed_without_evidence,
        bindings={"<proposal-id>": "TST-1", "...": "'use SQLite'"},
        clears_by_rerun=False,
        descriptive={
            "kyo new decision": "the command being refused, named to say what it must "
            "do; the remedy is re-running it with --evidence-file"
        },
    ),
    # KYO-522. The spawn from a reminder is refused the same way; the message names the
    # command being refused, and the remedy is re-running it with --evidence-file.
    "cmd_records.py:_evidence_at_filing#1": Expectation(
        _decision_spawned_from_a_reminder_without_evidence,
        clears_by_rerun=False,
        descriptive={
            "kyo spawn TST-1 --kind decision": "the command being refused, named to say "
            "what it must do; the remedy is re-running it with --evidence-file"
        },
    ),
    # KYO-509. The member id is the one the provoker filed second.
    "cmd_records.py:_rekind#3": Expectation(
        _rekinding_a_plan_that_has_members,
        bindings={"<member>": "TST-2"},
    ),
    "cmd_records.py:cmd_edit#6": Expectation(
        _edit_setting_a_verified_state_directly,
        clears_by_rerun=False,
        bindings={"...": "reasoned-about --why 'the criterion holds'"},
    ),
    "cmd_evidence.py:_verify_one#1": Expectation(
        _verify_with_an_unknown_verdict,
        clears_by_rerun=False,
        descriptive={
            "kyo verify": "names the command being refused, to say which verdicts it takes",
            "kyo edit --criterion --state ... --why": "spelled as a flag shape, not an "
            "invocation: it carries no criterion id",
        },
    ),
    # KYO-198. `kyo verify` grew a second form, so its "you are missing an argument"
    # message names the other one. That is an explanation of the alternative, not an
    # offer: `--from-report` needs a dispatch that has reported, which the provoking
    # state does not have and which no amount of running this line would create.
    "cmd_evidence.py:_verify_one#0": Expectation(
        _verify_missing_one_of_its_three_arguments,
        clears_by_rerun=False,
        descriptive={
            "kyo verify --from-report <dispatch-id>": "names the command's OTHER form, "
            "to say why the three arguments are not argparse-required; it is not a way "
            "to finish the invocation that was actually typed"
        },
    ),
    # KYO-198.c3, and the only one of the three that is about STATE rather than about
    # the invocation: the dispatch has no report, `kyo report` gives it one, and the
    # same `kyo verify --from-report` then succeeds. `clears_by_rerun=True` is the
    # whole point of the entry.
    "cmd_evidence.py:_report_verdicts#0": Expectation(
        _a_dispatch_that_has_not_reported,
        bindings={"<report-path>": "{scratch}/worker-report.json"},
        scratch_files={
            "worker-report.json": json.dumps(
                {
                    "dispatch_id": "TST-1.d1",
                    "record_id": "TST-1",
                    # A sha of the right SHAPE and not this repository's: `kyo report`
                    # compares it to the dispatch's recorded base and *reports* the
                    # difference (KYO-10) rather than refusing, so a fixed value keeps
                    # this file static while the provoked git history is not.
                    "worked_from_sha": "0123456789abcdef0123456789abcdef01234567",
                    "criteria_verdicts": [
                        {
                            "criterion_id": "TST-1.c1",
                            "verdict": "met",
                            "method": "reasoned-about",
                        }
                    ],
                    "premise_false": [],
                    "disagreements": [],
                    "corrected_numbers": [],
                    "commits": [],
                }
            )
            + "\n"
        },
    ),
    # KYO-198. A verdict typed beside `--from-report`: the remedy is the one-criterion
    # form, which runs from this state and records the verdict the caller was trying to
    # type. `clears_by_rerun=False` — the malformed invocation stays malformed.
    "cmd_evidence.py:_refuse_flags_that_from_report_supplies#0": Expectation(
        _from_report_beside_a_typed_verdict,
        clears_by_rerun=False,
        bindings={
            "<criterion-id>": "TST-1.c1",
            "<verdict>": "met",
            "<method>": "reasoned-about",
        },
    ),
    # KYO-199.c6. The message names the command being refused in order to say WHICH two
    # refs it diffs; the fix is a different `--against`, and the ref is the caller's
    # worktree's, not this check's to invent.
    # KYO-420.c3. About the INVOCATION: the remedy is the same read with --json added,
    # and the text-mode call stays refused however often it is re-run.
    "cmd_views.py:cmd_show#0": Expectation(
        _showing_fields_without_json,
        bindings={"<id>": "TST-1"},
        clears_by_rerun=False,
        descriptive={"kyo show --fields": "names the flag being refused, not a command to run"},
    ),
    # KYO-502. About STATE: `kyo docs index` writes the file the check compares against,
    # and the same `--check` then exits 0.
    "cmd_views.py:_cmd_docs_index#0": Expectation(_a_stale_section_index),
    "cmd_views.py:_cmd_docs_preview#0": Expectation(
        _previewing_docs_against_an_unknown_base,
        clears_by_rerun=False,
        descriptive={
            "kyo docs preview": "names the command being refused, to say which two refs "
            "it diffs and which flags name them"
        },
    ),
    # KYO-199.c4, the docs half of `cmd_evidence.py:_report_verdicts#0` and about STATE
    # for the same reason: `kyo report` gives the dispatch a report and the same command
    # then succeeds. The report file is static — see that entry for why a fixed sha is
    # correct here.
    "cmd_views.py:_cmd_docs_settle_from_report#0": Expectation(
        _settling_docs_from_a_dispatch_that_has_not_reported,
        bindings={"<report-path>": "{scratch}/worker-report.json"},
        scratch_files={
            "worker-report.json": json.dumps(
                {
                    "dispatch_id": "TST-1.d1",
                    "record_id": "TST-1",
                    "worked_from_sha": "0123456789abcdef0123456789abcdef01234567",
                    "criteria_verdicts": [],
                    "premise_false": [],
                    "disagreements": [],
                    "corrected_numbers": [],
                    "commits": [],
                }
            )
            + "\n"
        },
    ),
    # KYO-199.c4, and the exact shape of `cmd_evidence.py:_refuse_flags_that_from_report
    # _supplies#0`: a verdict typed beside `--from-report`. The remedy is the
    # one-obligation form, which runs from this state and records the verdict the caller
    # was trying to type; the malformed invocation stays malformed.
    "cmd_views.py:_refuse_flags_that_from_report_supplies#0": Expectation(
        _settling_docs_from_a_report_beside_a_typed_verdict,
        clears_by_rerun=False,
        bindings={
            "<obligation-id>": "TST-1.o1",
            "<verdict>": "updated",
            "<how>": "re-read",
        },
    ),
    "cmd_records.py:_correct_criterion_text#1": Expectation(
        _correcting_an_attested_criterion,
        # `kyo restate` is a different act, not a way to make `kyo edit --text-file`
        # start working: it leaves the attested criterion attested (and void), so the
        # original command still refuses — correctly. What the remedy has to do is run
        # and carry the new wording, and that is what is checked.
        clears_by_rerun=False,
        bindings={"FILE": "{scratch}/replacement.txt"},
        scratch_files={"replacement.txt": "a sharper wording of the same claim\n"},
    ),
    "cmd_records.py:cmd_restate#1": Expectation(
        _restating_a_criterion_that_is_already_void,
        clears_by_rerun=False,
        bindings={"FILE": "{scratch}/another-claim.txt"},
        scratch_files={"another-claim.txt": "a claim the record still needs\n"},
    ),
    "contracts.py:check_archetype_allows_status_flow#0": Expectation(
        _moving_a_knowledge_record,
        clears_by_rerun=False,
        bindings={"<f>": "{scratch}/amended.txt", "<reason>": "'filed in error'"},
        scratch_files={"amended.txt": "The decision, amended.\n"},
        descriptive={
            "kyo wont-do": "named as the work archetype's counterpart, to explain what `void` is"
        },
    ),
    "contracts.py:check_void_is_knowledge_only#0": Expectation(
        _voiding_a_work_record,
        clears_by_rerun=False,
        bindings={"<reason>": "'filed in error'"},
        descriptive={"kyo void": "names the command being refused"},
    ),
    "contracts.py:check_transition#4": Expectation(_done_with_unsettled_criteria),
    "contracts.py:check_transition#5": Expectation(
        _done_with_no_met_criterion,
        clears_by_rerun=False,
        descriptive={
            "kyo wont-do": "named as a verb without the record id; not runnable as spelled"
        },
    ),
    "contracts.py:check_transition#6": Expectation(
        _done_with_an_open_docs_obligation,
        bindings={"<id>": "TST-1.o1", "<how>": "re-read"},
    ),
    # KYO-413.c1: last in `check_transition` so #0-#6 keep their keys.
    "contracts.py:check_transition#7": Expectation(
        _built_with_no_criteria,
        bindings={"<file>": "{scratch}/criteria.txt"},
        scratch_files={"criteria.txt": "the fix ships\n"},
    ),
    # KYO-490: appended after KYO-413.c1's raise so #0-#7 keep their keys.
    "contracts.py:check_transition#8": Expectation(
        _done_over_an_open_gate,
        bindings={"<reason>": "'reviewed on the call'"},
    ),
    # KYO-546: appended after KYO-490's raise so #0-#8 keep their keys.
    "contracts.py:check_transition#9": Expectation(
        _verified_without_a_verdict,
        bindings={"<verdict>": "met", "<how>": "reasoned-about"},
    ),
    # KYO-159 put three more refusals in this function, and this one moved from #0 to #1
    # behind the first of them; KYO-199 put a fifth at the head (`--only` without
    # `--from-report`, which names no `kyo` command and so is not on this surface) and
    # moved every one of them down again. The keys are positional by design (see
    # `_SPANS`), so the entry moves with the site rather than the site being kept where
    # the entry is.
    # KYO-528.c2: the two `--all` refusals live in their own function so the ordinals
    # above and below keep their keys.
    "cmd_views.py:_check_settle_all_shape#0": Expectation(
        _settling_all_beside_an_obligation_id,
        clears_by_rerun=False,
        descriptive={
            "kyo docs settle --all": "names the command being refused, to say what it takes"
        },
    ),
    "cmd_views.py:_check_settle_all_shape#1": Expectation(
        _settling_all_with_no_record,
        clears_by_rerun=False,
        bindings={"<id>": "TST-1", "<how>": "re-read", "<reason>": "'read it'"},
        descriptive={
            "kyo docs settle --all": "names the command being refused, to say what it takes",
            "kyo docs settle --record KYO-1 --all --verdict not-affected --method <how> "
            "--why <reason>": "an example spelled with a foreign project's id",
        },
    ),
    "cmd_views.py:_cmd_docs_settle#1": Expectation(
        _settling_no_docs_obligation_at_all,
        clears_by_rerun=False,
        descriptive={
            "kyo docs settle": "names the command being refused, to say what it takes"
        },
    ),
    "cmd_views.py:_cmd_docs_settle#2": Expectation(
        _settling_a_docs_obligation_with_an_unknown_verdict,
        # The failure is about the INVOCATION, not the state: re-running the malformed
        # command would go on failing however good the remedy is. What is asserted is
        # that the remedy runs from the state that printed it.
        clears_by_rerun=False,
        bindings={"<id>": "TST-1.o1", "<how>": "re-read"},
    ),
    # KYO-131 widened this message: each of the four redactable shapes now names the
    # command that WRITES one. The writers are mentions in the strictest sense — the
    # caller is redacting, not creating, and running `kyo attach` here would be the
    # opposite of what they asked for. They are in the message because a vocabulary that
    # lists id shapes without their write paths is what taught a consumer that RECORD.nN
    # was unproducible, and this is one of the three surfaces where that reader arrives.
    "cmd_redact.py:_redaction_target#0": Expectation(
        _redacting_something_that_is_not_redactable,
        clears_by_rerun=False,
        descriptive={
            "kyo redact": "names the command being refused, to say what it takes",
            "kyo show <record>": "named as where ids are found, not as a fix",
            "kyo brief": "named as where shift-note ids are found, not as a fix",
            "kyo attach": "named as what WRITES a RECORD.eN, not as a fix for this id",
            "kyo claim": "named as what writes a RECORD.mN, not as a fix for this id",
            "kyo edit --note-file": (
                "named as what writes a RECORD.nN — the write path a consumer could not "
                "find, which is why the shape is attributed here at all. Not an offer: "
                "the caller is redacting an id, not creating a note"
            ),
            "kyo note --shift": "named as what writes a PREFIX-SN, not as a fix for this id",
        },
    ),
    # KYO-405: the refusal of a redaction rerun under a different reason names the rerun
    # that IS accepted — the recorded reason, from a file. About the invocation: the
    # different-reason command goes on being refused, correctly, so what is checked is
    # that the named retry runs from this state.
    "cmd_redact.py:cmd_redact#2": Expectation(
        _redacting_again_with_a_different_reason,
        clears_by_rerun=False,
        bindings={"FILE": "{scratch}/reason.txt"},
        scratch_files={"reason.txt": "first\n"},
    ),
    # KYO-131: the two refusals `kyo note` makes when the caller wanted a record note.
    # Both name `kyo edit --note-file`, and for #0 the check RUNS it — against the very
    # id the caller passed, which is the point. The remedy this message names is not a
    # suggestion to read something; it is the command that does what they asked for, and
    # if it ever stops working from this state the message becomes the circular remedy
    # KYO-5 was filed about.
    "cmd_brief.py:cmd_note#0": Expectation(
        _noting_against_a_record_id,
        clears_by_rerun=False,
        bindings={"FILE": "{scratch}/note.md"},
        scratch_files={"note.md": "an observation that belongs to the record\n"},
    ),
    "cmd_brief.py:cmd_note#1": Expectation(
        _noting_without_saying_which_log,
        clears_by_rerun=False,
        bindings={"<RECORD>": "TST-1", "FILE": "{scratch}/note.md"},
        scratch_files={"note.md": "an observation that belongs to the record\n"},
    ),
    # KYO-94: the read path that closes the shift log's one-shot delivery. Its message
    # names two commands and offers neither: both are places a shift-note id is PRINTED,
    # and neither conjures the note this caller asked for. `kyo brief` would exit 0 and
    # change nothing about `kyo show TST-S9`, which is exactly why it is classified here
    # rather than run — "the remedy ran fine" would be a false assurance.
    "cmd_views.py:_show_shift_note#0": Expectation(
        _showing_a_shift_note_that_does_not_exist,
        clears_by_rerun=False,
        descriptive={
            "kyo note --shift": "named as what prints a shift-note id when it appends "
            "one, not as a way to reach the note that was asked for",
            "kyo brief": "named as where shift-note ids appear (the shift log and the "
            "handoff block), not as a fix",
        },
    ),
    # KYO-454: about the INVOCATION, so `clears_by_rerun=False`. `kyo briefs` is the
    # runnable offer — the listing a caller learns a real brief id from.
    "cmd_views.py:cmd_briefs#1": Expectation(
        _showing_a_brief_that_does_not_exist,
        clears_by_rerun=False,
    ),
    # KYO-108: `kyo base run` with no name. The offer is real and runnable — `kyo base
    # list` is how a caller learns the name they failed to supply, and it exits 0 from
    # exactly this state — but it does not clear the error, because the error is about
    # the invocation rather than the store's condition. `clears_by_rerun=False` is that
    # distinction, and it still catches the KYO-73 shape: a named remedy that itself
    # fails from the state that named it.
    "cmd_views.py:_cmd_base_run#0": Expectation(
        _running_a_base_without_naming_one,
        clears_by_rerun=False,
        descriptive={"kyo base run": "names the command being refused, to say what it takes"},
    ),
    # KYO-120: `--group-by` on the action that runs nothing. The offer is a real run,
    # bound here to the base the provoker declares, so the check actually executes the
    # arrangement the caller was reaching for. `clears_by_rerun=False` for the usual
    # reason: the error is about the invocation, and `kyo base list --group-by kind`
    # will keep failing however good the remedy is.
    "cmd_views.py:cmd_base#0": Expectation(
        _grouping_a_listing_instead_of_a_run,
        bindings={"<name>": "only", "<field>": "kind"},
        clears_by_rerun=False,
        descriptive={
            "kyo base list": "names the command being refused, to say what it does not do"
        },
    ),
    # KYO-221. Both remedies are real invocations of a command that needs no project,
    # so both are run as written; re-running the refused one would fail forever, which
    # is what `clears_by_rerun=False` says.
    "cmd_views.py:cmd_reference#0": Expectation(
        _reference_of_one_command_with_output,
        clears_by_rerun=False,
    ),
    # KYO-226. `kyo changelog --all` is the runnable half — a command that needs no
    # project, reaching the whole history the caller asked a slice of. `kyo version` is
    # the mention: it is where a date comes from, and running it leaves the refused
    # invocation exactly as refused, which is what makes it descriptive rather than an
    # offer. `clears_by_rerun=False` for the usual reason — the error is about the
    # invocation, and `--since last-tuesday` will keep failing however good the remedy.
    "changelog.py:resolve_since#0": Expectation(
        _changelog_since_neither_a_date_nor_a_commit,
        clears_by_rerun=False,
        descriptive={
            "kyo version": "names where the reader finds a date to give --since; it "
            "prints one and changes nothing about the invocation being refused"
        },
    ),
    "cmd_flags.py:cmd_flag#0": Expectation(_flag_collapse_with_a_flag_id, clears_by_rerun=False),
    # KYO-220 inserted `kyo flag list` ahead of the resolve branch, so every site below
    # it in this function moved up one ordinal. Exactly the renumbering the scheme's own
    # note calls its deliberate trade ("an edit inside the same function, ahead of an
    # existing site"), re-keyed by hand here because that is what the trade costs.
    #
    # KYO-346 does the same thing twice more: `--settled` is checked right after each
    # action's own flag-id refusal (so the two read together at the call site), which
    # pushed `#1`/`#2`/`#3` to `#2`/`#4`/`#6` below. Re-keyed by hand again, on the note
    # above's own reasoning.
    "cmd_flags.py:cmd_flag#1": Expectation(
        _flag_collapse_with_settled,
        clears_by_rerun=False,
        descriptive={
            "kyo flag resolve": "the command --settled belongs to; not run here, "
            "since running it alone leaves this invocation exactly as refused",
            "kyo flag collapse": "the command actually invoked, named to say why it "
            "does not take --settled",
        },
    ),
    "cmd_flags.py:cmd_flag#2": Expectation(
        _flag_list_with_a_flag_id,
        clears_by_rerun=False,
        descriptive={
            "kyo flag list": "names the command being refused",
            "kyo show KYO-1.f1": "an example spelled with a foreign project's id",
        },
    ),
    "cmd_flags.py:cmd_flag#3": Expectation(
        _flag_list_with_settled,
        clears_by_rerun=False,
        descriptive={
            "kyo flag resolve": "the command --settled belongs to; not run here, "
            "since running it alone leaves this invocation exactly as refused",
            "kyo flag list": "the command actually invoked, named to say why it does "
            "not take --settled",
        },
    ),
    "cmd_flags.py:cmd_flag#5": Expectation(
        _flag_resolve_settled_with_a_flag_id,
        clears_by_rerun=False,
        descriptive={
            "kyo flag resolve --settled": "names the command being refused, to say "
            "what it does not take"
        },
    ),
    "cmd_flags.py:cmd_flag#6": Expectation(
        _flag_resolve_without_a_flag_id,
        clears_by_rerun=False,
        descriptive={
            "kyo flag resolve": "names the command being refused",
            "kyo flag resolve KYO-1.f1": "an example spelled with a foreign project's id",
        },
    ),
    "cmd_flags.py:cmd_flag_settle#0": Expectation(
        _flag_resolve_settled_without_a_reason,
        clears_by_rerun=False,
        descriptive={
            "kyo flag resolve --settled": "names the command being refused, to say "
            "what it is still missing"
        },
    ),
    # All three `kyo dispatch` refusals below spell the shape of the invocation rather
    # than offering a command: the id in them is the caller's own (or, for KYO-133,
    # named separately in plain prose beside the backtick span rather than substituted
    # into it), and the check has no standing to decide which dispatch they meant.
    # Filling `<id>`/`<dispatch-id>` in with a real one and requiring exit 0 would be a
    # test of `kyo dispatch close`'s own contracts (it refuses a dispatch with no
    # ingested report, law 8), not of whether this message applies. Classified, so the
    # coverage figure counts them as reached and verifying nothing rather than as
    # verified.
    "dispatch_close.py:cmd_dispatch_close#0": Expectation(
        _dispatch_close_with_a_dispatch_open,
        clears_by_rerun=False,
        descriptive={
            "kyo dispatch close <id>": "the shape of the invocation, naming the missing "
            "positional; the id is the caller's"
        },
    ),
    # KYO-133: `--terminated-no-report`, `--why` and `--why-file` are meaningful only
    # with `close`, and used to be silently accepted and silently ignored beside a
    # record id -- the command dispatched a brand-new record anyway (KYO-128.d2). Now
    # a usage error, naming the correct form and, separately in plain prose (not this
    # backtick span), the record's own running dispatch when it has one.
    "dispatch_close.py:_refuse_close_only_flags#0": Expectation(
        _terminated_no_report_with_a_record_id,
        clears_by_rerun=False,
        descriptive={
            "kyo dispatch close <dispatch-id>": "the shape of the invocation the "
            "caller probably meant; the record's actual running dispatch id is named "
            "separately in the message, not substituted into this span"
        },
    ),
    # KYO-90. The refusal that keeps an amended report from silently landing over an
    # account it never read. Its message names one `kyo` command and does not offer it:
    # the correction is an edit to the report document, which nothing here can make for
    # the caller.
    # KYO-149. The remedy this message names is the schema itself, and until KYO-149 it
    # was named as a source-tree path no installed consumer has ever had. `kyo
    # report-schema` is offered, not merely mentioned: a report refused for its shape
    # is fixed by reading the shape, and the command prints it from the package.
    # `clears_by_rerun=False` because the correction is an edit to the caller's report
    # file — printing the schema tells them what to write, it does not write it.
    "report_ingest.py:_load_report_document#2": Expectation(
        _report_that_does_not_conform_to_the_schema,
        clears_by_rerun=False,
    ),
    "report_ingest.py:_check_report_against_store#3": Expectation(
        _report_amending_an_already_superseded_report,
        clears_by_rerun=False,
        descriptive={
            "kyo show <record>": "where the reports and their standings are read; the "
            "remedy itself is an edit to the report file, which this check cannot make"
        },
    ),
    # KYO-147.c4. `kyo touch-drift <dispatch-id>` is a real offer and runs from the
    # provoking state (it reports "not checked", which is the reading this refusal is
    # telling the caller to go and look at). `kyo report ... --file <report.json>` is
    # named but not offered: the report is the worker's own document, and no check here
    # can author one — the same reading `_check_report_against_store#3` records.
    "touch_drift.py:touch_drift_correction#0": Expectation(
        _applying_a_touch_drift_that_was_never_checked,
        clears_by_rerun=False,
        descriptive={
            # Keyed as the message SPELLS it: the dispatch id is interpolated, and the
            # provoker above always builds TST-1.d1.
            "kyo report TST-1.d1 --file <report.json>": "the shape of the ingest, not a "
            "runnable line: the report is a document the worker writes and this check "
            "cannot author one — the same reading _check_report_against_store#3 records",
            # Reached from `_drift_not_checked`'s detail rather than from this refusal's
            # own lines, and it is a flag on the command that CREATES a dispatch, not
            # something to run against one that already exists: the branch had to be
            # declared at dispatch time, and this message names it to say what was
            # missing then.
            "kyo dispatch --branch NAME": "a flag shape on `kyo dispatch`, naming the "
            "declaration that was not made when this dispatch was created; there is "
            "nothing to run against an existing one",
        },
    ),
    "cmd_dispatch.py:cmd_dispatch#0": Expectation(
        _dispatch_with_an_extra_argument,
        clears_by_rerun=False,
        descriptive={
            "kyo dispatch close <dispatch-id>": "the shape of the invocation the caller "
            "probably meant; the id is theirs"
        },
    ),
    # KYO-81's refusal, added to the surface after KYO-77 was written and left in
    # UNREACHED by the merge that found it. Its remedy is the interesting kind: not a
    # refusal to be re-worded around but a command that moves the store to the state
    # where the refused one is safe, so `clears_by_rerun` is the whole assertion.
    "cmd_store.py:cmd_export#1": Expectation(
        _refreshing_over_an_export_ahead_database,
        descriptive={
            "kyo export --refresh": "names the command being refused, to say what it "
            "would overwrite"
        },
    ),
    "scaffold.py:init_project#0": Expectation(
        _init_over_an_existing_in_repo_store, clears_by_rerun=False
    ),
    # KYO-558: each layout route is run from a fresh folder, and bare `kyo init` run again
    # afterwards no longer reaches this refusal (it meets the existing store instead).
    "commands.py:cmd_init#0": Expectation(
        _init_in_a_new_project_with_no_layout_chosen,
        descriptive={
            "kyo init": "names the command being refused, and the step the guided "
            "setup runs",
            "kyo onboard": "the guided route: it prints steps for an agent and writes "
            "nothing itself, so running it alone cannot clear a refusal about a choice "
            "not yet made",
            "kyo init --explain": "describes the layouts and writes nothing by design",
        },
    ),
    # KYO-416 put the `--in-repo`/`--adopt` contradiction ahead of it, so this site
    # moved from #1 to #2; the provocation is unchanged.
    "scaffold.py:init_layout#2": Expectation(
        _init_split_over_an_existing_in_repo_store,
        clears_by_rerun=False,
        descriptive={"kyo init": "names the command being refused, to say what it does not do"},
    ),
    # KYO-607. A failure about the INVOCATION: the remedy is the same command naming
    # the types, and re-running the bare one would fail forever.
    "cmd_install_agents.py:cmd_install_agents#0": Expectation(
        _install_agents_with_no_type,
        clears_by_rerun=False,
    ),
}


#: Sites this check does not reach, each with the reason — KYO-77.c2. These are named,
#: not omitted: a coverage figure whose denominator quietly excluded the awkward cases
#: would be the same false assurance this whole file exists against. Each entry is a
#: claim about why provoking it is out of reach here, and each is a candidate for the
#: next pass rather than a decision that it does not matter.
UNREACHED: dict[str, str] = {
    # KYO-493.c7: a worker branch whose fragment's `amends:` header `kyo docs assemble`
    # would refuse. The `kyo docs assemble` the message quotes names the command that
    # WOULD refuse, not the remedy; each refusal line carries its own.
    "cmd_check_branch.py:cmd_check_branch#2": (
        "not provoked by this walk: the refusal needs a git repository with a worker "
        "branch carrying a fragment, and its remedy is an edit committed on that branch "
        "(correct the fragment's `kyosei-fragment amends:` header, or the amended "
        "section's `status:` / back-link line). Provoked, and its remedy run to exit 0, "
        "by tests/test_doc_fragments.py::"
        "test_check_branch_refuses_a_fragment_assemble_would_refuse"
    ),
    # KYO-499.c4: a worker branch whose changed documents or src/ pointers break the
    # section-status rules. The `kyo docs check` the message quotes names the command
    # that refuses the same thing, not the remedy; each problem line carries its own.
    "cmd_check_branch.py:cmd_check_branch#3": (
        "not provoked by this walk: the refusal needs a git repository with a worker "
        "branch changing a document or a src/ pointer, and its remedy is an edit "
        "committed on that branch (the section's status or link, or the pointer's "
        "comment). Provoked, and its remedy run to exit 0, by tests/test_docs_check.py::"
        "test_check_branch_refuses_a_changed_document_whose_pair_disagrees and "
        "test_check_branch_runs_the_set_rules_and_the_pointer_rule"
    ),
    # KYO-555.c5: a worker branch newly introducing a block-grade history phrase into a
    # document of a `history_lint: block` ssot set. The `kyo report` the message quotes
    # is where an override is reviewed, not the remedy.
    "cmd_check_branch.py:cmd_check_branch#4": (
        "not provoked by this walk: the refusal needs a git repository with a worker "
        "branch adding history prose to an ssot document, and its remedy is a commit on "
        "that branch (rewriting the sentence as the current fact, or a "
        "`Kyosei-History-OK` trailer naming the file and pattern). Provoked, and both "
        "remedies run to exit 0, by tests/test_history_lint.py::"
        "test_a_block_set_blocks_a_block_grade_match_and_the_trailer_clears_it and "
        "test_rewriting_the_sentence_as_the_current_fact_clears_the_block"
    ),
    # KYO-494: `kyo confirm`'s resolution refusals. Each writes nothing and prints the
    # ready line(s) that resolve it, built from the refused invocation's own words.
    "cmd_confirm.py:_refuse_retired#0": (
        "the remedy is `kyo ungate <gate> --why-file FILE`, whose FILE is the caller's "
        "reason to write; a confirmation of retired work is deliberately offered "
        "nowhere. Provoked, and the export proved unchanged, by "
        "tests/test_kyo_confirm.py::test_a_retired_record_is_refused"
    ),
    "cmd_confirm.py:_refuse_running_dispatch#0": (
        "the remedy is the same `kyo confirm` line once the dispatch is no longer "
        "running, which needs a dispatch closed in between. Provoked, the dispatch "
        "closed, and the printed line run to exit 0 by "
        "tests/test_kyo_confirm.py::test_a_running_dispatch_is_refused_and_the_same_line_clears_it_once_closed"
    ),
    "cmd_confirm.py:_resolve#0": (
        "each printed `kyo confirm` line carries the refused invocation's own quote "
        "source. Provoked, and the printed line run to exit 0, by "
        "tests/test_kyo_confirm.py::test_a_gate_naming_a_criterion_the_record_lacks_is_refused"
    ),
    "cmd_confirm.py:_resolve#2": (
        "each printed `kyo confirm` line carries the refused invocation's own quote "
        "source. Provoked, and the printed line run to exit 0, by "
        "tests/test_kyo_confirm.py::test_nothing_to_confirm_is_refused_and_its_line_clears_it"
    ),
    # KYO-458: `kyo controller`'s refusals, each proved end to end on a private tmux
    # server in tests/test_kyo_controller.py.
    "cmd_controller.py:cmd_controller#0": (
        "not provoked by this walk: every `kyo controller` site runs tmux, and this "
        "walk has no private tmux server to run it against (the real `tmux -L kyosei` "
        "is the user's and must not be touched). Provoked, and its remedy run to exit "
        "0, by "
        "tests/test_kyo_controller.py::test_usage_refusals_name_the_invocation_that_works"
    ),
    "cmd_controller.py:cmd_controller#1": (
        "not provoked by this walk: every `kyo controller` site runs tmux, and this "
        "walk has no private tmux server to run it against (the real `tmux -L kyosei` "
        "is the user's and must not be touched). Provoked, and its remedy run to exit "
        "0, by "
        "tests/test_kyo_controller.py::test_usage_refusals_name_the_invocation_that_works"
    ),
    "cmd_controller.py:cmd_controller#2": (
        "not provoked by this walk: every `kyo controller` site runs tmux, and this "
        "walk has no private tmux server to run it against (the real `tmux -L kyosei` "
        "is the user's and must not be touched). Provoked, and its remedy run to exit "
        "0, by "
        "tests/test_kyo_controller.py::test_usage_refusals_name_the_invocation_that_works"
    ),
    "cmd_controller.py:cmd_controller#3": (
        "not provoked by this walk: every `kyo controller` site runs tmux, and this "
        "walk has no private tmux server to run it against (the real `tmux -L kyosei` "
        "is the user's and must not be touched). Provoked, and its remedy run to exit "
        "0, by "
        "tests/test_kyo_controller.py::test_usage_refusals_name_the_invocation_that_works"
    ),
    "cmd_controller.py:resolve_project#0": (
        "not provoked by this walk: needs two registry entries answering to one name, "
        "which the resolver's name-or-prefix matching makes a fixture of two projects; "
        "the remedy it names (start by directory path) is run by "
        "tests/test_kyo_controller.py::test_start_by_prefix_and_by_directory_path"
    ),
    "cmd_controller.py:resolve_project#1": (
        "not provoked by this walk: every `kyo controller` site runs tmux, and this "
        "walk has no private tmux server to run it against (the real `tmux -L kyosei` "
        "is the user's and must not be touched). Provoked, and its remedy run to exit "
        "0, by "
        "tests/test_kyo_controller.py::test_an_unknown_project_is_refused_and_registering_it_clears_it"
    ),
    "cmd_controller.py:start#0": (
        "not provoked by this walk: every `kyo controller` site runs tmux, and this "
        "walk has no private tmux server to run it against (the real `tmux -L kyosei` "
        "is the user's and must not be touched). Provoked, and its remedy run to exit "
        "0, by "
        "tests/test_kyo_controller.py::test_start_refuses_a_session_already_running_and_its_remedy_clears_it"
    ),
    "cmd_controller.py:start#1": (
        "not provoked by this walk: every `kyo controller` site runs tmux, and this "
        "walk has no private tmux server to run it against (the real `tmux -L kyosei` "
        "is the user's and must not be touched). Provoked, and its remedy run to exit "
        "0, by "
        "tests/test_kyo_controller.py::test_over_ssh_with_no_server_start_refuses_naming_install_and_install_clears_it"
    ),
    "cmd_controller.py:stop#0": (
        "not provoked by this walk: every `kyo controller` site runs tmux, and this "
        "walk has no private tmux server to run it against (the real `tmux -L kyosei` "
        "is the user's and must not be touched). Provoked, and its remedy run to exit "
        "0, by "
        "tests/test_kyo_controller.py::test_stop_refuses_when_nothing_runs_naming_list"
    ),
    # KYO-474: `kyo controller attach`'s two refusals, each proved end to end on a private
    # tmux server in tests/test_kyo_controller.py, with the exec replaced by a recorder.
    "cmd_controller.py:attach#0": (
        "not provoked by this walk: every `kyo controller` site runs tmux, and this "
        "walk has no private tmux server to run it against (the real `tmux -L kyosei` "
        "is the user's and must not be touched). Provoked, and both remedies it names "
        "(`kyo controller start`, `kyo controller --install --apply`) run until the same "
        "attach resolves, by "
        "tests/test_kyo_controller.py::test_attach_with_no_server_names_start_and_starting_clears_it"
        " and "
        "tests/test_kyo_controller.py::test_attach_over_ssh_with_no_server_names_install_and_both_remedies_clear_it"
    ),
    "cmd_controller.py:attach#1": (
        "not provoked by this walk: every `kyo controller` site runs tmux, and this "
        "walk has no private tmux server to run it against (the real `tmux -L kyosei` "
        "is the user's and must not be touched). Provoked, and its remedy run until the "
        "same attach resolves, by "
        "tests/test_kyo_controller.py::test_attach_to_a_project_with_no_session_names_start_and_starting_clears_it"
    ),
    # KYO-457: `kyo serve --install`'s refusals and the start-time port check, each proved
    # in tests/test_kyo_serve_install.py with injected service managers and listeners.
    "serve_install.py:refuse_if_held#0": (
        "not provoked by this walk: every `kyo serve --install/--uninstall/--restart` s"
        "ite runs launchctl or systemctl, and the port check needs a live listener; this "
        "walk has neither, and the real service managers are the user's. Provoked, and it"
        "s remedy run to exit 0, by tests/test_kyo_serve_install.py::test_start_refuses_w"
        "hen_another_kyo_serve_answers_naming_its_pid_and_build"
    ),
    "serve_install.py:install#0": (
        "not provoked by this walk: every `kyo serve --install/--uninstall/--restart` s"
        "ite runs launchctl or systemctl, and the port check needs a live listener; this "
        "walk has neither, and the real service managers are the user's. Provoked, and it"
        "s remedy run to exit 0, by tests/test_kyo_serve_install.py::test_a_failed_bootst"
        "rap_names_the_desktop_terminal_and_rerunning_clears_it"
    ),
    "serve_install.py:install#1": (
        "not provoked by this walk: every `kyo serve --install/--uninstall/--restart` s"
        "ite runs launchctl or systemctl, and the port check needs a live listener; this "
        "walk has neither, and the real service managers are the user's. Provoked, and it"
        "s remedy run to exit 0, by tests/test_kyo_serve_install.py::test_a_failed_system"
        "d_enable_names_the_user_instance_and_rerunning_clears_it"
    ),
    "serve_install.py:uninstall#0": (
        "not provoked by this walk: every `kyo serve --install/--uninstall/--restart` s"
        "ite runs launchctl or systemctl, and the port check needs a live listener; this "
        "walk has neither, and the real service managers are the user's. Provoked, and it"
        "s remedy run to exit 0, by tests/test_kyo_serve_install.py::test_a_failed_bootou"
        "t_leaves_the_plist_and_rerunning_uninstall_clears_it"
    ),
    "serve_install.py:restart#0": (
        "not provoked by this walk: every `kyo serve --install/--uninstall/--restart` s"
        "ite runs launchctl or systemctl, and the port check needs a live listener; this "
        "walk has neither, and the real service managers are the user's. Provoked, and it"
        "s remedy run to exit 0, by tests/test_kyo_serve_install.py::test_restart_kicksta"
        "rts_the_agent_and_refuses_naming_install_when_none_is_loaded"
    ),
    "serve_install.py:restart#1": (
        "not provoked by this walk: every `kyo serve --install/--uninstall/--restart` s"
        "ite runs launchctl or systemctl, and the port check needs a live listener; this "
        "walk has neither, and the real service managers are the user's. Provoked, and it"
        "s remedy run to exit 0, by tests/test_kyo_serve_install.py::test_a_failed_restar"
        "t_names_install_apply_and_running_it_clears_it"
    ),
    "serve_install.py:requested_mode#1": (
        "not provoked by this walk: every `kyo serve --install/--uninstall/--restart` s"
        "ite runs launchctl or systemctl, and the port check needs a live listener; this "
        "walk has neither, and the real service managers are the user's. Provoked, and it"
        "s remedy run to exit 0, by tests/test_kyo_serve_install.py::test_apply_alone_or_"
        "two_modes_are_refused_and_naming_one_clears_it"
    ),
    # KYO-455: `kyo projects`'s refusals, each proved in tests/test_kyo_projects.py.
    "cmd_projects.py:add#0": (
        "not provoked by this walk: `kyo projects` is PROJECT_NONE and its refusals "
        "are about $KYOSEI_HOME/projects.json and directories beside the walk's "
        "project, which its --root provocations do not build. Provoked, and its "
        "remedy run to exit 0, by "
        "tests/test_kyo_projects.py::test_add_refuses_a_path_that_does_not_exist"
    ),
    "cmd_projects.py:add#1": (
        "not provoked by this walk: `kyo projects` is PROJECT_NONE and its refusals "
        "are about $KYOSEI_HOME/projects.json and directories beside the walk's "
        "project, which its --root provocations do not build. Provoked, and its "
        "remedy run to exit 0, by "
        "tests/test_kyo_projects.py::test_add_refuses_a_directory_that_holds_no_project"
    ),
    "cmd_projects.py:add#2": (
        "not provoked by this walk: `kyo projects` is PROJECT_NONE and its refusals "
        "are about $KYOSEI_HOME/projects.json and directories beside the walk's "
        "project, which its --root provocations do not build. Provoked, and its "
        "remedy run to exit 0, by "
        "tests/test_kyo_projects.py::test_add_refuses_a_duplicate_project_name"
    ),
    "cmd_projects.py:remove#0": (
        "not provoked by this walk: `kyo projects` is PROJECT_NONE and its refusals "
        "are about $KYOSEI_HOME/projects.json and directories beside the walk's "
        "project, which its --root provocations do not build. Provoked, and its "
        "remedy run to exit 0, by "
        "tests/test_kyo_projects.py::test_remove_refuses_an_unregistered_name_and_list_shows_the_names"
    ),
    "cmd_projects.py:remove#1": (
        "not provoked by this walk: `kyo projects` is PROJECT_NONE and its refusals "
        "are about $KYOSEI_HOME/projects.json and directories beside the walk's "
        "project, which its --root provocations do not build. Provoked, and its "
        "remedy run to exit 0, by "
        "tests/test_kyo_projects.py::test_a_hand_written_duplicate_name_is_flagged_and_removed_by_root"
    ),
    "cmd_projects.py:cmd_projects#0": (
        "not provoked by this walk: `kyo projects` is PROJECT_NONE and its refusals "
        "are about $KYOSEI_HOME/projects.json and directories beside the walk's "
        "project, which its --root provocations do not build. Provoked, and its "
        "remedy run to exit 0, by "
        "tests/test_kyo_projects.py::test_add_and_remove_need_a_target_and_list_takes_none"
    ),
    "cmd_projects.py:cmd_projects#1": (
        "not provoked by this walk: `kyo projects` is PROJECT_NONE and its refusals "
        "are about $KYOSEI_HOME/projects.json and directories beside the walk's "
        "project, which its --root provocations do not build. Provoked, and its "
        "remedy run to exit 0, by "
        "tests/test_kyo_projects.py::test_add_and_remove_need_a_target_and_list_takes_none"
    ),
    # KYO-346 re-keyed this from `#2` to `#4` — see the note above `cmd_flag#1` in
    # PROVOKED for why.
    "cmd_flags.py:cmd_flag#4": (
        "unreachable through the CLI: argparse rejects an unknown `kyo flag` action against "
        "choices={list,resolve,collapse} before the handler runs, so this branch is dead code "
        "and its message can never be printed"
    ),
    # KYO-151.c2. `procedures.adopt_rule` refuses this way when the STORE copy does not
    # carry the named rule at all -- a real state for the pure function, and the reason
    # the guard exists rather than a bare KeyError. But `install_procedures.install_procedures`
    # (the only caller `kyo install-procedures` has) runs the plain merge FIRST, which
    # inserts every packaged rule the store lacks before a single `--adopt` is
    # processed -- so by the time `adopt_rule` sees the store text, the named rule is
    # never missing: it was either already there, or the merge just put it there. A
    # rule genuinely absent from the packaged default is caught one branch earlier
    # (`#0`, "names no rule ... ships"), which IS on the runnable surface. Reaching #1
    # through `kyo install-procedures` needs a state this build's own call order makes
    # impossible; only a caller that invokes `procedures.adopt_rule` directly, bypassing
    # `install_procedures`'s merge step, can print this message, and nothing in this
    # package's CLI does that.
    "procedures.py:adopt_rule#1": (
        "unreachable through `kyo install-procedures`: the command always merges every "
        "missing packaged rule in before applying `--adopt`, so the named rule is never "
        "absent from the store copy when this check runs -- only a direct call to "
        "`procedures.adopt_rule` bypassing that merge could reach it, which nothing in "
        "this CLI does"
    ),
    # KYO-143, classified by the controller reconciling the wave-3 merge. The message
    # backticks `kyo dispatch` DESCRIPTIVELY -- naming where a worktree_setup entry gets
    # rendered, not offering a command to run -- so the recogniser reads it as a runnable
    # remedy and this table is where a runnable-looking site that cannot be provoked
    # belongs. Provoking it needs a config.yml carrying a multi-line worktree_setup
    # entry, which is a fixture this file does not build. The alternative was to
    # un-backtick the mention in config.py; that removes the site from the surface
    # entirely rather than reclassifying it, which is a worse answer than recording why.
    # KYO-164 moved the per-entry refusals out of `_parse_worktree_setup` and into
    # `_parse_setup_entry`, which is what the key now names; nothing about the site or
    # its message changed in kind.
    "config.py:_parse_setup_entry#5": (
        "needs a config.yml whose worktree_setup holds an entry whose command or note "
        "contains a newline -- reachable, not yet built here; and the `kyo dispatch` its "
        "message names is descriptive, so running it would clear nothing"
    ),
    "config.py:read_store_link#1": (
        "needs a .kyosei-store link file whose YAML parses but names no store — reachable, "
        "not yet built here"
    ),
    "config.py:_layout_at#0": (
        "needs a .kyosei-store link pointing at a directory that is not a store — reachable, "
        "not yet built here"
    ),
    "config.py:resolve_layout#0": (
        "fires from `resolve_layout` walking up from the process's cwd, which the in-process "
        "runner shares with the whole suite; provoking it safely needs a chdir this file does "
        "not do"
    ),
    "config.py:load_config#5": (
        "needs a config.yml whose `publication_acknowledged` key is a scalar; the remedy names "
        "a hand edit followed by a command, and this check has no vocabulary for a remedy whose "
        "first step is not a command"
    ),
    "config.py:record_publication_acknowledgement#0": (
        "`load_config` refuses a config with missing keys (config.py:508) before any command "
        "reaches this second check in `record_publication_acknowledgement`"
    ),
    "cmd_dispatch.py:_cmd_dispatch_create#9": (
        "needs --brief-out to pass the up-front writability check (#8) and then fail to "
        "write anyway (a full disk, a race); no store state reaches that through the CLI. "
        "tests/test_dispatch_lifecycle_gaps.py provokes it by patching Path.write_text and "
        "asserts the message names the created dispatch and says not to re-run"
    ),
    "cmd_dispatch.py:_cmd_dispatch_create#6": (
        "needs two records with undeclared touches and a dispatch comparison between them; "
        "reachable, and the highest-value one left — its message offers `kyo dispatch --force`, "
        "which is a remedy that changes what gets recorded, not just what gets refused"
    ),
    "report_ingest.py:_load_report_document#3": (
        "needs a schema-valid report file whose dispatch_id names a different dispatch; the "
        "backticked `kyo report <id>` there restates how the report was ingested and is not "
        "offered as anything to run"
    ),
    "report_ingest.py:_check_report_against_store#5": (
        "needs a report whose criteria_verdicts name a record the dispatch does not cover; "
        "reachable, not yet built here"
    ),
    "cmd_records.py:cmd_spawn#0": (
        "needs a retired record used as a spawn source, which needs a kind whose accepts_from "
        "names it; reachable, not yet built here"
    ),
    "cmd_redact.py:cmd_redact#1": (
        "needs a well-formed child id (RECORD.eN) that no row carries; reachable, not yet "
        "built here"
    ),
    # KYO-308.c1: `kyo move <plan> <a completion status>` refuses over an unsettled
    # member and backticks `kyo edit <MEMBER-ID> --no-plan` as one of its two named
    # remedies (the other, `--force`, is an imperative clause — RECORDED's shape, not
    # this one — and is what makes this site runnable at all). Genuinely reachable and
    # genuinely clears the refusal; `tests/test_plan_members.py` provokes it and runs
    # both named remedies (settle, detach) plus `--force` directly, in the ordinary
    # style every other test in this project's suite uses. Not wired into this file's
    # own provoke-and-recheck apparatus, on the same call `cmd_dispatch.py:_cmd_dispatch
    # _create#6` makes a few entries up: a real, runnable remedy this check has not yet
    # built a `Provocation`/`Expectation` for.
    # KYO-327: `kyo rules "<title>"` refuses a title the ledger does not carry and names
    # bare `kyo rules`, which lists them. Read-only on both sides -- the command opens no
    # database -- and proved where the command's own tests live:
    # tests/test_kyo323_brief_size.py provokes the refusal, runs the listing, and shows a
    # title taken from it is printed rather than refused. Classified on the merged tree by
    # the controller; the dispatch that added the command never ran this file.
    # KYO-565: `kyo rules add`'s refusals. Each needs only a tmp project and a reason
    # file, and each is provoked and its named remedy run to exit 0 in
    # tests/test_kyo_rules_add.py, where the command's own tests live -- the same
    # arrangement as `cmd_rules#1` below.
    "commands.py:cmd_rules#0": (
        "an add-form flag without `add`; reachable and genuinely clears -- see "
        "tests/test_kyo_rules_add.py::"
        "test_the_stray_flag_refusal_names_the_add_form_and_running_it_clears, "
        "which runs the `kyo rules add` form the refusal names -- reachable, not yet "
        "built here"
    ),
    "commands.py:_rules_add#0": (
        "`kyo rules add` missing a flag; reachable and genuinely clears -- see "
        "tests/test_kyo_rules_add.py::"
        "test_the_missing_flags_refusal_names_the_full_form_and_running_it_clears "
        "and ::test_a_rule_titled_add_stays_printable_by_the_spelling_the_help_names "
        "(`kyo rules Add`) -- reachable, not yet built here"
    ),
    "commands.py:_rules_add#1": (
        "no project store readable; reachable and genuinely clears -- see "
        "tests/test_kyo_rules_add.py::test_no_project_is_refused_and_root_clears_it, "
        "which reruns with "
        "--root <project> -- reachable, not yet built here"
    ),
    "commands.py:_rules_add#2": (
        "a project whose kyosei/procedures.md is gone; reachable and genuinely clears -- "
        "see tests/test_kyo_rules_add.py::"
        "test_a_missing_ledger_is_refused_and_install_procedures_clears_it, "
        "which runs `kyo install-procedures` and then the add -- reachable, not yet "
        "built here"
    ),
    "procedures.py:add_project_rule#7": (
        "a title an existing rule already has; reachable and the named command prints "
        "the rule -- see tests/test_kyo_rules_add.py::"
        "test_the_duplicate_title_refusal_names_the_command_that_"
        "prints_the_rule -- reachable, not yet built here"
    ),
    "commands.py:cmd_rules#1": (
        "needs only a title no rule carries; reachable and genuinely clears -- see "
        "tests/test_kyo323_brief_size.py, which follows the listing the refusal names to "
        "a title the command then prints -- reachable, not yet built here"
    ),
    "contracts.py:check_plan_close_over_open_members#0": (
        "needs a plan with an unsettled member; reachable and genuinely clears — see "
        "tests/test_plan_members.py, which provokes this refusal and runs `kyo edit "
        "<id> --no-plan`, settling every member, and `--force` — reachable, not yet "
        "built here"
    ),
    # KYO-594: `kyo harness install claude-code` writes a link under $KYOSEI_HOME and runs
    # `claude`; this walk's sandbox has no stub `claude` and no prepared link directory.
    "cmd_harness.py:write_link#0": (
        "not provoked by this walk: it needs a directory standing where the plugin link "
        "goes. Provoked, and its remedy run to exit 0, by tests/test_harness_install.py::"
        "test_a_directory_in_the_links_place_is_refused_and_moving_it_aside_clears_it"
    ),
    "cmd_harness.py:copied_hook_commands#0": (
        "not provoked by this walk: it needs a project whose .claude/settings.json is not "
        "JSON. Provoked, and its remedy run to exit 0, by tests/test_harness_install.py::"
        "test_an_unreadable_settings_file_refuses_before_writing_and_fixing_it_clears_it"
    ),
    "cmd_harness.py:copied_hook_commands#1": (
        "the same refusal for a settings file holding JSON that is not an object; the "
        "remedy, correcting the file and running again, is the one tests/"
        "test_harness_install.py::test_an_unreadable_settings_file_refuses_before_writing_"
        "and_fixing_it_clears_it runs to exit 0"
    ),
    # KYO-595: `kyo harness migrate` runs `claude` and writes the plugin link, which this
    # walk's sandbox has neither a stub `claude` nor a prepared home for.
    "cmd_harness.py:cmd_harness#0": (
        "not provoked by this walk, which names a harness wherever it runs this command. "
        "Provoked, and its remedy run to exit 0, by tests/test_harness_migrate.py::"
        "test_install_without_a_harness_is_refused_and_naming_it_clears_it"
    ),
    "cmd_harness_migrate.py:_read_settings#0": (
        "not provoked by this walk: it needs a project on the copies whose .claude/"
        "settings.json is not JSON. Provoked, and its remedy run to exit 0, by tests/"
        "test_harness_migrate.py::test_an_unreadable_settings_file_refuses_before_anything_"
        "and_fixing_it_clears_it"
    ),
    "cmd_harness_migrate.py:migrate_claude_code#0": (
        "not provoked by this walk, which always runs inside a project; its remedy (run it "
        "in a project) is what every other test in tests/test_harness_migrate.py does, and "
        "test_outside_a_project_it_refuses provokes it"
    ),
    "cmd_harness_migrate.py:migrate_claude_code#1": (
        "not provoked by this walk: it needs PATH without `claude`. Provoked, and its "
        "remedy run to exit 0, by tests/test_harness_migrate.py::test_without_claude_it_"
        "refuses_and_changes_nothing_and_claude_on_path_clears_it"
    ),
}


#: Sites this check DOES reach and where it finds the defect KYO-77 was filed about.
#: An entry lands here rather than in :data:`PROVOKED` when the fix is a change to a
#: message in ``src/kyosei/`` that the dispatch which found it may not make. Each is
#: asserted to still be broken, so the day the message is fixed this table fails and
#: the entry has to move — a finding cannot rot into a permanently tolerated xfail.
#:
#: EMPTY, and it earned that. Its one entry was ``kinds.py:load_kind_registry#0``, the
#: sixth instance of the KYO-73 class: a store whose ``kyosei/kinds.yml`` was gone was
#: told to ``run `kyo init` or restore it`` — the fifth instance's sentence, copied
#: word for word into another module — and `kyo init` refused that state. KYO-82 fixed
#: the message and the entry moved into :data:`PROVOKED`, which is the transition this
#: table exists to force. The mechanism is kept for the next finding, and
#: ``test_a_finding_is_wired_to_a_provoker`` is what stops it rotting while unused.
FINDINGS: dict[str, str] = {}


#: KYO-84.c2, the half of the surface this check can only RECORD. Each of these names a
#: real remedy and none of them names a `kyo` command, so :func:`check_remedies_apply`
#: has nothing to run: the value says what the message offers and why running it is out
#: of this check's reach. Three shapes recur, and the distinction is worth keeping in
#: view because it is what a later pass would attack:
#:
#:   * A RE-INVOCATION — "pass --criteria-file", "pass --force to override". The remedy
#:     is the refused command again with a different argument, and the argument is the
#:     caller's: a criteria file is prose only they can write (law 5), a `--force` is a
#:     decision only they can make. Runnable in principle, which makes these the
#:     candidates for the next pass; not runnable by a check with no standing to invent
#:     the argument.
#:   * A SHELL COMMAND IN SOMEONE ELSE'S REPOSITORY — `git worktree add`, `git rebase`.
#:     Named as exemplars, without paths, because the paths are the caller's checkout.
#:   * A HAND EDIT — "delete the line, or give it a value". No command exists, which is
#:     the case this record was filed about: before KYO-84 these were not unverified,
#:     they were absent, and the coverage figure was a percentage of a surface that had
#:     had them quietly removed from it.
#:
#: An entry here is a claim that the remedy is real, and it is checked in the direction
#: that matters: if the message ever starts naming a `kyo` command, the site moves into
#: the runnable half and ``test_every_remedy_naming_site_is_classified`` fails until it
#: is provoked or excused. What is NOT checked is whether the remedy works — that is the
#: honest limit, and reporting it as present-but-unverified is the improvement.
RECORDED: dict[str, str] = {
    # KYO-607: `kyo install-agents` refusals whose remedy is a flag or a re-invocation.
    "cmd_install_agents.py:cmd_install_agents#1": (
        "re-invoking with each template named once; proved by tests/test_install_agents.py"
        "::test_a_type_named_twice_is_refused, which runs the corrected call to exit 0"
    ),
    "cmd_install_agents.py:cmd_install_agents#2": (
        "re-invoking with `--force` to replace the differing file (or `--print` to read "
        "the text first); proved by tests/test_install_agents.py::"
        "test_an_existing_differing_file_is_refused_and_force_clears_it"
    ),
    # KYO-585: `recx.prior_art` refusals whose remedy is a hand edit of kyosei/config.yml
    # (add the missing `state:` line; correct or delete `limit:`), and `kyo prior-art`'s
    # refusal without the optional extra, whose remedy is installing it -- a package
    # manager command against the caller's own tool environment.
    "prior_art.py:parse_prior_art#5": (
        "a hand edit of kyosei/config.yml adding `state: on` or `state: off` under "
        "recx.prior_art; the parse of the edited block is tested by "
        "tests/test_prior_art.py::test_c2_three_states"
    ),
    "prior_art.py:parse_prior_art#6": (
        "a hand edit of kyosei/config.yml giving recx.prior_art.limit a whole number from 1 "
        "to 100, or deleting it for the default; tested by "
        "tests/test_prior_art.py::test_c2_declared_trigger_limit_and_index_url"
    ),
    # KYO-586: `kyo prior-art-setting on` without the extra; the same remedy as the site
    # below, proved by its own test the same way.
    "prior_art.py:set_prior_art_state#0": (
        "installing the optional extra (`uv tool install 'kyosei[recx]'`), which changes "
        "the caller's tool environment. Proved by tests/test_prior_art_opt_in.py::"
        "test_c1_on_without_the_extra_is_refused_and_installing_it_clears_it, which makes "
        "recx_cli unimportable, sees the refusal, restores it and runs the command to exit 0"
    ),
    "prior_art.py:cmd_prior_art#0": (
        "installing the optional extra (`uv tool install 'kyosei[recx]'`), which changes "
        "the caller's tool environment. Proved by tests/test_prior_art.py::"
        "test_c1_absent_extra_refuses_prior_art_and_installing_clears_it, which makes "
        "recx_cli unimportable, sees the refusal, restores it and runs the command to exit 0"
    ),
    # KYO-535.c2: `kyo init` / `kyo projects add` from inside a running dispatch's
    # worktree against the real home. The remedy is a re-invocation of the same command
    # with KYOSEI_HOME set to a temporary directory (or the explicit override), not a
    # fixed `kyo` command: the message's `kyo init ...` elides the caller's own
    # arguments, and provoking it needs a running dispatch whose worktree is the cwd.
    # Proved by tests/test_kyo_worker_leak_guard.py::
    # test_the_named_remedy_a_temporary_home_clears_the_refusal and
    # test_the_named_override_clears_the_refusal.
    "config.py:refuse_real_home_from_a_dispatch_worktree#0": (
        "the same command re-run with KYOSEI_HOME set to a temporary directory, or with "
        "KYOSEI_ALLOW_REAL_HOME=1. Proved by tests/test_kyo_worker_leak_guard.py::"
        "test_the_named_remedy_a_temporary_home_clears_the_refusal and "
        "test_the_named_override_clears_the_refusal"
    ),
    # KYO-492: `kyo ungate`'s argument-shape refusals. Each remedy is a re-invocation
    # (drop the ids or the --tag; name an id or a --tag), not a `kyo` command the
    # message spells. Proved by
    # tests/test_ungate_bulk.py::test_each_shape_refusal_clears_by_the_reinvocation_it_names.
    "cmd_links.py:cmd_ungate#0": (
        "the same `kyo ungate` naming gate ids or passing --tag, not both. Proved by "
        "tests/test_ungate_bulk.py::test_each_shape_refusal_clears_by_the_reinvocation_it_names"
    ),
    "cmd_links.py:cmd_ungate#2": (
        "the same `kyo ungate` naming a gate id or a --tag. Proved by "
        "tests/test_ungate_bulk.py::test_each_shape_refusal_clears_by_the_reinvocation_it_names"
    ),
    # KYO-494: `kyo confirm` sites whose remedy is a line built from the caller's own
    # invocation (its quote source and flags), so no fixed `kyo` command is named.
    "cmd_confirm.py:_Quote.__init__#0": (
        "a usage refusal of the invocation itself (both --quote and --quote-file "
        "given); the remedy is to drop one, which this walk has no invocation to drop "
        "it from. Proved by "
        "tests/test_kyo_confirm.py::test_confirm_needs_exactly_one_quote_source"
    ),
    "cmd_confirm.py:_resolve#1": (
        "each printed `kyo confirm` line carries the refused invocation's own quote "
        "source. Provoked, and the printed line run to exit 0, by "
        "tests/test_kyo_confirm.py::test_ambiguous_criteria_is_refused_and_its_line_clears_it"
    ),
    "cmd_confirm.py:_scope#0": (
        "each printed `kyo confirm` line carries the refused invocation's own quote "
        "source. Provoked for a non-human and a cleared --gate, and the printed line "
        "run to exit 0, by "
        "tests/test_kyo_confirm.py::test_a_non_human_or_cleared_gate_is_refused_and_its_line_clears_it"
    ),
    # KYO-536: `kyo close-wave`'s two remedies that are not `kyo` commands.
    "cmd_close_wave.py:cmd_close_wave#0": (
        "the same `kyo close-wave` naming records or passing --suite-passed; the remedy is "
        "a re-invocation with arguments this walk has none of. Proved by "
        "tests/test_kyo_close_wave.py::test_an_empty_invocation_is_refused_and_naming_work_clears_it"
    ),
    "cmd_close_wave.py:_parse_steps#2": (
        "a hand edit of kyosei/config.yml giving the empty `close_wave` entry a command. "
        "Proved by tests/test_kyo_close_wave.py::"
        "test_an_empty_close_wave_step_is_refused_and_giving_it_a_command_clears_it"
    ),
    # KYO-454: `kyo briefs` given a brief id AND --latest. The remedy is a re-invocation
    # with one of the two dropped, not a `kyo` command the message spells. Proved by
    # tests/test_kyo_serve_reads.py::test_a_brief_id_and_latest_together_are_refused,
    # which runs the refused call and then `kyo briefs TST-B1` to exit 0.
    "cmd_views.py:cmd_briefs#0": (
        "the same `kyo briefs` naming a brief id or passing --latest, not both. Proved by "
        "tests/test_kyo_serve_reads.py::test_a_brief_id_and_latest_together_are_refused"
    ),
    # KYO-458: `kyo controller`'s remedies that are not `kyo` commands.
    "cmd_controller.py:resolve_tmux#0": (
        "installing tmux (`brew install tmux`) or running through a login shell; "
        "installing software is out of reach here. Proved by "
        "tests/test_kyo_controller.py::test_no_tmux_is_refused_and_installing_it_clears_it, "
        "which puts tmux back on PATH and starts"
    ),
    "cmd_controller.py:start#3": (
        "a login shell on which claude resolves (`zsh -lc 'command -v claude'`); the "
        "user's shell profile is out of reach here. Proved by "
        "tests/test_kyo_controller.py::test_claude_that_exits_at_once_is_reported_and_a_working_claude_clears_it"
    ),
    "cmd_controller.py:install#0": (
        "running the printed `launchctl bootstrap` line from a terminal in the desktop "
        "session; a real bootstrap is out of reach of any test. Proved with an injected "
        "launchctl, failing twice and then loading on the printed line, by "
        "tests/test_kyo_controller.py::test_kyo472_c2_two_failures_say_not_loaded_and_the_printed_line_loads_it"
    ),
    "cmd_controller.py:uninstall#0": (
        "re-running the same `launchctl bootout` from a terminal in the desktop "
        "session; a real bootout is out of reach of any test. Proved with an injected "
        "launchctl by "
        "tests/test_kyo_controller.py::test_a_failed_bootout_is_reported_and_leaves_the_plist"
    ),
    # KYO-457: two `kyo serve` modes at once; the remedy is running them one after another.
    "serve_install.py:requested_mode#0": (
        "running the two modes one after another, not a single `kyo` command the message "
        "spells. Proved by "
        "tests/test_kyo_serve_install.py::test_apply_alone_or_two_modes_are_refused_and_naming_one_clears_it"
    ),
    # KYO-453, three sites and one remedy shape: A RE-INVOCATION SOMEWHERE ELSE. A
    # command served by `kyo serve` runs on a store opened read-only, and a write is
    # refused -- by `Store.transaction` for every write that opens one, and by
    # `cli.run` for one that reached SQLite's `query_only` without. The remedy is the
    # same command in a terminal on the machine that holds the store, which is what
    # tests/test_kyo_serve.py runs: the same `kyo sweep` without `read_only` writes.
    "store.py:Store.transaction#0": (
        "re-invocation outside kyo serve: run the command in a terminal on the machine "
        "that holds the store. Proved by tests/test_kyo_serve.py::"
        "test_a_write_through_a_transaction_is_refused_naming_kyo_serve, which runs the "
        "same command without read_only and sees it write"
    ),
    "cli.py:run#1": (
        "re-invocation outside kyo serve, as store.py:Store.transaction#0: the same "
        "refusal for a write that skipped the transaction. Provoked by "
        "tests/test_kyo_serve.py::test_a_write_that_skips_the_transaction_is_refused_by_query_only"
    ),
    # A re-invocation with a different argument: `--bind 127.0.0.1` (the default) or
    # `::1`. Proved by tests/test_kyo_serve.py::
    # test_the_loopback_bind_the_refusal_names_is_accepted.
    "serve.py:check_bind#0": (
        "re-invocation of kyo serve with a loopback --bind (127.0.0.1, the default, or "
        "::1); the Tailscale Serve path it also names is the user's network, out of "
        "reach here. Proved by tests/test_kyo_serve.py::"
        "test_the_loopback_bind_the_refusal_names_is_accepted"
    ),
    # KYO-421.c5. A write that lost a lock race to another kyo process. The remedy is
    # WAITING — the other writer has to finish first — which this walk cannot stage: its
    # provocation is a state rebuilt from scratch and a command re-run against it, and a
    # held lock is a second live process, not a state. It is proved where it can be,
    # end to end: tests/test_cli_error_envelope.py holds the lock from a second
    # connection, runs the write, releases, and runs the same write again to exit 0.
    # KYO-453 re-keyed it: `cli.main`'s body became `cli.run`, which `main` wraps.
    "cli.py:run#0": (
        "re-invocation after another process releases the store's write lock: run the "
        "same command again once the other kyo command finishes. Out of reach here "
        "because the provoking state is a concurrent writer, which this walk's "
        "rebuild-then-rerun provocations cannot hold open; proved instead by "
        "tests/test_cli_error_envelope.py::test_c5_retrying_after_the_other_writer_releases_clears_it"
    ),
    # KYO-168, and a fourth shape the three above do not name: AN UPGRADE OF THE BUILD
    # THIS CHECK IS RUNNING INSIDE. The store's schema is ahead of this kyosei, so the
    # remedy is a newer kyosei — an installation, which is out of reach here not because
    # the argument belongs to the caller but because the process would have to replace
    # itself to run it. It is also the one site on this surface where naming a `kyo`
    # command would be the DEFECT rather than the coverage: every command that could
    # make this store openable by this build writes the older schema over the newer one.
    # ``test_the_two_schema_directions_do_not_share_a_message_or_a_remedy`` is what holds
    # that down, and it is checked in the direction that matters here too — if this
    # message ever starts naming a `kyo` command the site moves into the runnable half
    # and ``test_every_remedy_naming_site_is_classified`` fails until someone looks.
    "store.py:open_store#4": (
        "an upgrade of this build: install a kyosei that speaks the store's schema_version "
        "or later. Not runnable here in the strongest sense on this surface — the remedy "
        "replaces the interpreter running the check — and deliberately not a `kyo` command, "
        "because every command that would make this store openable by this build "
        "reconstructs it under the OLDER schema and loses what the newer one added"
    ),
    # KYO-179, the same shape one boundary over. `kyo rebuild` now refuses an export
    # whose documents were written by a later build, and `kyosei/export/` is the
    # serialization that actually travels between builds, so this is the pair's more
    # reachable half rather than a second copy of it. Classified by the controller at
    # merge rather than by KYO-179.d1: this file was another running dispatch's, its
    # worker declined to edit it and reported the exact entries instead, and the two
    # raises live in NEW functions so no existing ordinal here moves.
    #
    # KYO-181 renamed the first of the two rather than adding a third. `kyo rebuild` now
    # refuses a later-build export in either half — record documents OR ledger lines — and
    # the two refusals deliberately share ONE raise site so their wording cannot drift
    # apart (law 3), which is why the key moved from
    # `_refuse_documents_from_a_later_build#0` to the shared
    # `_refuse_an_export_from_a_later_build#0`. The remedy is unchanged and now covers
    # both, which is the point: a reader has the same thing to do either way.
    "rebuild.py:_refuse_an_export_from_a_later_build#0": (
        "an upgrade of this build: install a kyosei that speaks the export's "
        "schema_version or later. Out of reach in the same strong sense as "
        "store.py:open_store#4 - the remedy replaces the interpreter running the check - "
        "one boundary over, at the serialization that actually travels between builds. "
        "One site for both halves of the export since KYO-181: a document written by a "
        "later build and a ledger line written by one call for the same installation"
    ),
    "export.py:document_schema_version#0": (
        "a hand edit, or a restore from git: correct the one schema_version value in the "
        "named export document, or check that file out of the store repository's history. "
        "kyosei/export/ is git-canonical, so the version the document was written at still "
        "exists even though the file no longer states it. No `kyo` command edits an export "
        "document and there must not be one - the database is authoritative and every "
        "document is derived from it"
    ),
    # KYO-181: the ledger twin of the site above, and the same remedy for the same reason
    # — a stamp that is not a version number means the line cannot say what format it is
    # in, and no `kyo` command edits an export artifact by hand. It differs from its twin
    # in one respect worth stating: a ledger is append-only, so restoring the file from
    # git history is the safer of the two halves of this remedy rather than merely the
    # tidier one, because the historical lines are not derivable from the database the way
    # a record document is.
    "export.py:ledger_schema_version#0": (
        "a hand edit, or a restore from git: correct the one schema_version value on the "
        "named line, or check that ledger out of the store repository's history. "
        "kyosei/export/ is git-canonical, so the version that line was written at still "
        "exists even though the file no longer states it. No `kyo` command edits a ledger "
        "and there must not be one - the ledgers are append-only history, not a derived "
        "artifact a command may regenerate"
    ),
    "cmd_dispatch.py:_cmd_dispatch_create#1": (
        "re-invocation: `kyo dispatch` again with a non-empty --branch, or without the "
        "flag; which of the two is meant is the caller's, not this check's"
    ),
    "cmd_dispatch.py:_cmd_dispatch_create#2": (
        "shell command: `git worktree add`, named as an exemplar with no path, because "
        "the path is the caller's checkout and the branch is theirs to choose"
    ),
    # KYO-402. Both remedies are git's, not kyo's, and both are proven to clear in
    # tests/test_worktree_prune_safety.py (a valid name dispatches; `git worktree add`
    # followed by the same dispatch succeeds).
    "cmd_dispatch.py:_refuse_an_invalid_branch_name#0": (
        "re-invocation: `kyo dispatch` again with a --branch name `git check-ref-format "
        "--branch` accepts; which name is the caller's"
    ),
    "cmd_dispatch.py:_refuse_a_foreign_worktree#1": (
        "shell command: `git worktree add <path>` from the code repository, then the same "
        "dispatch; the path and branch are the caller's"
    ),
    # KYO-485. Every one is a re-invocation or git's, and each is proven to clear in
    # tests/test_dispatch_creates_worktree.py.
    "cmd_dispatch.py:_resolve_base#0": (
        "re-invocation: `kyo dispatch` again with a non-empty --base, or without it"
    ),
    "cmd_dispatch.py:_resolve_base#1": (
        "re-invocation: `kyo dispatch` again with --branch beside --base; the branch "
        "name is the caller's"
    ),
    "cmd_dispatch.py:_resolve_base#2": (
        "re-invocation: `kyo dispatch` again with a --base ref that resolves in the code "
        "repository (or after `git fetch`); which ref is the caller's"
    ),
    "cmd_dispatch.py:_plan_the_worktree#0": (
        "re-invocation: `kyo dispatch` again with --worktree DIR or a different --branch; "
        "which is the caller's. The test file runs the --branch one"
    ),
    "cmd_dispatch.py:_plan_the_worktree#1": (
        "re-invocation: `kyo dispatch` again without --base, or with a new --branch"
    ),
    "cmd_dispatch.py:_WorktreeCreation.run#0": (
        "fix what git names (a branch checked out elsewhere), or --worktree DIR; git's "
        "message decides which, so the test file frees the branch and re-runs"
    ),
    "cmd_dispatch.py:_WorktreeCreation.__exit__#0": (
        "shell command: `git worktree remove <path>`; reached only when a dispatch failed "
        "after its worktree was created AND git refused the cleanup, which needs a fault "
        "injected at both points"
    ),
    "cmd_dispatch.py:_cmd_dispatch_create#3": (
        "re-invocation: `kyo dispatch` again with a non-empty --model, or without the "
        "flag; the model name belongs to the harness the controller spawns with"
    ),
    "cmd_dispatch.py:_cmd_dispatch_create#5": (
        "re-invocation: --force over a touch-disjointness refusal. Running it would be "
        "asserting the two dispatches are safe to overlap, which is the judgment the "
        "refusal exists to ask for"
    ),
    # KYO-345 widened this message from one flag to two, and the reason it stays
    # RECORDED is unchanged: whichever the caller means, the --why is theirs.
    "dispatch_close.py:cmd_dispatch_close#2": (
        "re-invocation carrying prose: --terminated-no-report --why '...' or "
        "--withdrawn --why '...', and the why is the controller's account of why no "
        "report came (law 8). This check can supply a placeholder, not a reason — and "
        "it could not choose between the two flags either: which one applies is the "
        "fact only the controller has, since it is whether they ever spawned a worker"
    ),
    "cmd_views.py:_cmd_docs_settle#3": (
        "re-invocation carrying prose: `kyo docs settle` again with --why, or one id at "
        "a time. The reason is the controller's account of what made these sections one "
        "editorial act (KYO-159.c4) — it is the evidence the review covered every member, "
        "and a placeholder supplied by this check would be exactly the rubber stamp the "
        "requirement exists to make expensive"
    ),
    "cmd_views.py:_cmd_docs_settle#4": (
        "re-invocation naming a different set: the same command over only the members "
        "that are still open. Which of the named ids those are is in the message, but "
        "choosing to settle a smaller set is the caller's — and re-running as spelled "
        "would go on being refused, because a standing verdict is provenance and this "
        "command never overwrites one (law 9)"
    ),
    "report_ingest.py:_check_report_against_store#4": (
        "shell commands in the worker's repository: `git cherry-pick`/`git rebase` to "
        "move a commit onto the declared branch. Named as exemplars; the check owns "
        "neither the commit nor the checkout"
    ),
    # KYO-330: ordinals #5/#6 here (and #3/#4 on cmd_spawn below) moved up from #4/#5
    # and #1/#2 -- `--from`/`--check` added two/three raises earlier in the same
    # functions (`--check` without `--from`, and, on `cmd_spawn`, `--title` required
    # with neither given), and ordinals count every deliberate raise in the function,
    # not only the remedy-naming ones. The messages themselves are unchanged on the
    # flags path these entries are about; a `--from` file gets an added clause naming
    # `'## Criteria'`/`'touches:'` as the same kind of remedy, one clause longer.
    "cmd_records.py:cmd_promote#5": (
        "re-invocation with a file of prose: --criteria-file. The criteria are the "
        "record's substance and only the caller has them"
    ),
    "cmd_records.py:cmd_promote#6": (
        "re-invocation with a file of prose: --touches-file, whose lines are paths in a "
        "project this check is not looking at"
    ),
    "cmd_records.py:cmd_spawn#3": (
        "re-invocation with a file of prose: --criteria-file, as cmd_promote#5"
    ),
    "cmd_records.py:cmd_spawn#4": (
        "re-invocation with a file of prose: --touches-file, as cmd_promote#6"
    ),
    "cmd_records.py:cmd_edit#5": (
        "re-invocation, twice: the refusal is that --state and --text-file arrived "
        "together, and the remedy is to run the call the caller meant. Which one that "
        "is, is the thing the refusal says it cannot tell"
    ),
    "cmd_records.py:_checked_since_note#0": (
        "KYO-498: re-invocation as a carried kind, or a kyosei/kinds.yml edit adding "
        "`carried: true`. Proved by tests/test_carried_since_note.py::"
        "test_a_non_carried_kind_is_refused_and_filing_as_carry_clears_it"
    ),
    # KYO-509: `kyo edit --kind`'s refusal of an undefined target, whose remedy is a
    # re-invocation with another kind or a hand edit of kyosei/kinds.yml -- no `kyo`
    # command the message spells.
    "cmd_records.py:_rekind#0": (
        "re-invocation naming a kind kyosei/kinds.yml defines (the message lists them), "
        "or a kinds.yml edit defining the target. Proved by tests/test_kyo_rekind.py::"
        "test_edit_kind_refuses_a_kind_that_does_not_exist and the re-kinds that follow "
        "in that file"
    ),
    # KYO-538: the refusal's remedy is `--force` on the same invocation (or using the
    # record it names), no `kyo` command the message spells. Proved by
    # tests/test_kyo_uniform_receipts.py::test_force_clears_the_refusal
    "cmd_records.py:_refuse_identical_recent#0": (
        "re-invocation with --force. Proved by tests/test_kyo_uniform_receipts.py::"
        "test_force_clears_the_refusal"
    ),
    "cmd_records.py:cmd_edit#7": (
        "re-invocation: --plan or --no-plan, not both. Same shape as cmd_edit#5"
    ),
    "cmd_records.py:cmd_edit#9": (
        "re-invocation: --touches-file or --touches-from-drift, not both (KYO-147.c4). "
        "The third of exactly this shape in one function, and the reason is the same "
        "each time: both flags REPLACE the whole list, so which one the caller meant is "
        "the thing the refusal says it cannot tell"
    ),
    "cmd_records.py:cmd_edit#8": (
        "re-invocation: --scope or --no-scope, not both (KYO-100). Same shape as "
        "cmd_edit#7 one flag pair over, and the same reason: which of the two the "
        "caller meant is the thing the refusal says it cannot tell"
    ),
    # KYO-100. The one place any of the four registries refuses a name it does
    # not hold, for every dimension and every write path — see registry.Registry.require
    # on why it is one raise and not four. Its remedy is a hand edit of a reviewable
    # config file, which is deliberate rather than incidental: the registry IS the rule,
    # so there is no `kyo` command that adds a scope and there must not be one, or the
    # rule becomes a flag. The same shape as kinds.py's, one dimension over.
    "registry.py:Registry.require#0": (
        "hand edit: add the entry to kyosei/<dimension>.yml, or create that file if the "
        "project has none yet. No `kyo` command writes a registry — a registry a command "
        "could edit on request is a --force with extra steps"
    ),
    "cmd_store.py:cmd_export#0": (
        "re-invocation: `kyo export --refresh`, spelled as the flag rather than as a "
        "command line. The runnable spelling of it IS on the surface and provoked, at "
        "cmd_store.py:cmd_export#1"
    ),
    "commands.py:_reason#0": (
        "re-invocation: --why or --why-file, not both. The reason text is the caller's "
        "and law 5 says it arrives by file or stdin, never as an argument"
    ),
    # KYO-148. The gate payload's three flag-level refusals, all the same shape as
    # `commands.py:_reason#0` directly above and for the same reason: the payload is the
    # caller's own prose, so no command this check could run knows what it should say.
    # What is new is only that `--do` finally has a `--do-file` twin for these messages
    # to point at — before KYO-148 the one field whose job is "the exact thing to run"
    # was the only prose field in the tool with no file form at all.
    "cmd_links.py:_payload#0": (
        "re-invocation: --do or --do-file, not both. Exactly commands.py:_reason#0 one "
        "field over, and law 5 is the same half of the answer — a payload longer than a "
        "line arrives by file or stdin, never as an argument"
    ),
    "cmd_links.py:_payload#1": (
        "re-invocation, or a hand edit: put the instruction in the file, or omit --do "
        "and --do-file entirely for a gate that carries no payload. Which of the two "
        "was meant is the thing an empty file cannot say, and the payload itself is "
        "prose no command here could write"
    ),
    # KYO-349. Inserted above cmd_gate's own `requires --do` raise, which is why it
    # is #1 and that one moved to #2 below — the ordinal shift KYO-83 built this
    # whole mechanism to survive. kyo verify, kyo brief, kyo next, kyo accept and kyo
    # doctor all suggest a ready `kyo gate ... --do "<placeholder>"` line
    # (cmd_next.ungated_unobserved_remedy); this is what stops pasting it unedited
    # from storing the placeholder as if it were an instruction.
    "cmd_links.py:cmd_gate#1": (
        "re-invocation: pass a real --do naming what a person should actually look "
        "at or run — 'watch the badge after a cold start', not the placeholder text "
        "itself. Same shape as _payload#0/#1 just above: the replacement is prose "
        "only the caller can write, so nothing here can supply it"
    ),
    "cmd_links.py:cmd_gate#2": (
        "re-invocation: --do TEXT for a one-line payload, or --do-file FILE (or "
        "`--do-file -`) for a procedure that needs more. The payload is the caller's — "
        "it is the exact condition or thing to run, which is precisely what nothing but "
        "the caller knows. This site reached the surface at KYO-148 without its raise "
        "moving: the sentence was purely declarative ('gate type X requires --do', the "
        "worked example above REMEDY_VERBS) until it gained an imperative clause naming "
        "the new file form. KYO-349 then inserted a new raise above it in source, "
        "shifting this one from #1 to #2 without its own message moving at all"
    ),
    # KYO-143, reconciled by the controller at the wave-3 merge. `worktree_setup` is a
    # list of shell commands a project declares for its own worktrees; every refusal
    # below is about the SHAPE of that declaration, so the remedy is always a text
    # editor. No `kyo` command writes config.yml, for registry.py:Registry.require#0's
    # reason one file over.
    "config.py:_parse_worktree_setup#2": (
        "hand edit: delete the empty worktree_setup list from config.yml, or give it "
        "entries. Declaring nothing is the designed case and absence is how it is "
        "spelled, so an empty list is a half-written declaration rather than a choice"
    ),
    # KYO-164 moved this one into `_parse_setup_entry` alongside the mapping form's own
    # refusals, and added the note's. Same reasoning throughout: the declaration is the
    # project's own text and only a person knows what it was meant to say.
    "config.py:_parse_setup_entry#4": (
        "hand edit: remove the offending entry from config.yml's worktree_setup, or "
        "replace it with a command. The entry is the project's own shell text and "
        "nothing but a person knows what it was meant to say"
    ),
    "config.py:_parse_setup_entry#2": (
        "hand edit: delete the empty `note:` from the worktree_setup entry, or write "
        "what the command does not say about itself. A blank note is a half-written "
        "declaration -- the caveat is the whole reason the mapping form exists, and only "
        "the project knows what it is"
    ),
    # KYO-142, same merge. Both sites refuse a DIMENSION whose path-shaped field is
    # spelled against its dialect, which is a definition in kyosei/<dimension>.yml or in
    # the packaged registry — a hand edit either way, and the same shape as
    # registry.py:Registry.require#0 one level up: the registry is the rule.
    "registry.py:Dimension.__post_init__#0": (
        "hand edit: rename the field to the dialect's own name, or validate it as "
        "something that is not path-shaped. Which of the two is meant is the dimension "
        "author's, and the refusal says so rather than guessing"
    ),
    "registry.py:Dimension.__post_init__#1": (
        "hand edit: give the reserved name its dialect's validator, or pick another "
        "name. Reserved-name-with-the-wrong-validator is the copied-line mistake KYO-142 "
        "exists to catch, so the remedy is the correction a person makes deliberately"
    ),
    # KYO-138, classified by the controller reconciling the wave-B merge. `kyo reference
    # --output` refuses a destination whose parent directory is absent. The remedy is a
    # hand edit of the filesystem (`mkdir`) or a re-invocation without the flag, and which
    # of the two is meant is the caller's — they know whether they wanted a file.
    # KYO-221 put a refusal ahead of it in the same function (a command name with
    # --output), so it is #1 from here on — the renumbering this scheme's own note
    # names as its deliberate trade.
    "cmd_views.py:cmd_reference#1": (
        "hand edit plus a re-invocation: create the missing directory, or drop --output "
        "and take the reference on stdout. No `kyo` command makes a directory, and which "
        "of the two the caller wanted is the thing the refusal cannot tell"
    ),
    # KYO-226: `kyo changelog --output` refuses a destination whose parent directory is
    # absent — byte for byte the same situation as `kyo reference --output` directly
    # above, and classified the same way for the same reason.
    "cmd_views.py:cmd_changelog#1": (
        "hand edit plus a re-invocation: create the missing directory, or drop --output "
        "and take the changelog on stdout. No `kyo` command makes a directory, and which "
        "of the two the caller wanted is the thing the refusal cannot tell"
    ),
    "config.py:load_config#3": (
        "hand edit, or a code change: remove the unread keys from config.yml, or ship "
        "their consumer (law 2). No command does either, and the second is a commit"
    ),
    # KYO-413.c7: a hand-edited non-integer `schema_version`. The remedy is a text
    # editor; the backticked `git log` only shows the value to restore.
    # `tests/test_config_load_validation.py` writes the bad value, asserts the refusal,
    # restores the number and asserts the command runs clean.
    "config.py:load_config#9": (
        "hand edit: write the store's schema_version back into config.yml as a whole "
        "number. `git log -p` is named only to show what that number was; no command "
        "writes it, because every command that could is blocked by this refusal"
    ),
    # KYO-413.c6: a repeated mapping key in a registry or config.yml. Which copy to keep
    # is the author's decision, so the remedy is a text editor.
    # `tests/test_yaml_duplicate_keys.py` writes the duplicate into kinds.yml,
    # statuses.yml and config.yml, asserts the refusal, deletes it and asserts the
    # command runs clean.
    "registry.py:load_yaml_refusing_duplicates#0": (
        "hand edit: delete one of the two entries, or rename one if both were meant. "
        "Which of them is right is the author's call and no command can make it"
    ),
    "config.py:load_config#6": (
        "hand edit: delete the worker_model line from config.yml, or give it a value. "
        "The whole remedy is a text editor — the case KYO-84 was filed about, and the "
        "one site on this surface that names no command of any kind"
    ),
    # KYO-303, and it is the site directly above in every respect this table cares
    # about: a value in config.yml that is neither a boolean nor absent, whose remedy is
    # the two things a person does in a text editor. Recorded rather than provoked for
    # that reason and not for want of a provoker — `tests/test_docs_suggest.py` writes
    # the bad value, asserts the refusal, writes `false`, and asserts the refusal is
    # gone, which is the KYO-5.c2 guarantee run against the exact remedy this message
    # names.
    "config.py:load_config#7": (
        "hand edit: write docs_suggest_notice: false in config.yml, or delete the line. "
        "Both are a text editor, and which of the two is meant is the project's own "
        "decision about whether it wants the notice — the thing the refusal must not "
        "guess. It names no command deliberately: the two a reader might expect (`kyo "
        "doctor`, `kyo docs suggest`) are both blocked by this very refusal until the "
        "line is fixed, so naming either would be the KYO-77 defect itself"
    ),
    # KYO-394, the site directly above in every respect: `kind_grants_notice` is parsed
    # exactly as `docs_suggest_notice` is. `tests/test_doctor.py` writes the bad value,
    # asserts the refusal, writes `false`, and asserts `kyo doctor` runs clean again.
    # KYO-416: a `code_repo_id` that is not a string of root shas. The remedy is a text
    # editor — delete the line. `tests/test_init_store_identity.py` writes the bad
    # value, asserts the refusal, deletes the line and asserts the config loads.
    "config.py:load_config#12": (
        "hand edit: delete the code_repo_id line from config.yml. No command writes it "
        "back, because every command that reads this store is blocked by this refusal "
        "until the line is fixed"
    ),
    "config.py:load_config#8": (
        "hand edit: write kind_grants_notice: false in config.yml, or delete the line. "
        "A text editor either way, and which is meant is the project's decision about "
        "whether it wants the notice; no command is named because `kyo doctor`, the one "
        "a reader would reach for, is blocked by this refusal until the line is fixed"
    ),
    # KYO-556.c5: `kind_policy_notice`, parsed exactly as `kind_grants_notice` above.
    # `tests/test_kind_policy_notice.py` writes the bad value, asserts the refusal,
    # writes `false`, and asserts `kyo doctor` runs clean again.
    "config.py:load_config#13": (
        "hand edit: write kind_policy_notice: false in config.yml, or delete the line. "
        "A text editor either way, and which is meant is the project's decision about "
        "whether it wants the notice; no command is named because `kyo doctor`, the one "
        "a reader would reach for, is blocked by this refusal until the line is fixed"
    ),
    # KYO-410: a blank or non-string `trunk:`, parsed as `worker_model` is. The remedy is a
    # text editor. `tests/test_guard_git_config.py` writes each bad value, asserts the
    # refusal, deletes the line and asserts the config loads.
    "config.py:load_config#11": (
        "hand edit: delete the trunk line from config.yml, or give it a branch name. A "
        "text editor either way, and which branch is the trunk is the project's own fact"
    ),
    # KYO-410: a declared `trunk:` naming no ref, refused where check-branch reads it.
    # `tests/test_guard_git_config.py` provokes it through `check-branch --subagent-stop`,
    # corrects the key by hand and asserts the refusal is gone and the real verdict runs.
    "cmd_check_branch.py:resolve_trunk#0": (
        "hand edit: set trunk: in config.yml to the branch worker branches are cut from, "
        "or delete the line. The backticked `git rev-parse` only shows what was tried; "
        "which branch is the trunk is the project's fact and no command writes it"
    ),
    # KYO-410: no rung of the trunk order answered. Same remedy, provoked and cleared the
    # same way in `tests/test_guard_git_config.py`.
    "cmd_check_branch.py:resolve_trunk#1": (
        "hand edit: add trunk: <branch> to config.yml, or re-run with --against <ref>. "
        "Which branch is the trunk is the project's fact, and no command can derive what "
        "git itself could not"
    ),
    # KYO-342, and the RECORDED shape it fits is the first one this table's docstring
    # names: a re-invocation, in a different working tree. The message tells a reader to
    # run whatever they just ran in the main checkout instead, and "whatever they just
    # ran" is every store-writing command there is — the refusal is raised from one
    # helper that `Store.transaction`, `kyo rebuild` and `kyo export --refresh` all call,
    # so the remedy cannot be a fixed command line and deliberately is not spelled as
    # one. What running it would take is a second checkout of the same repository, which
    # this check's `--root`-based runner has no vocabulary for: `bind_remedy` builds an
    # argv, not a working directory. The remedy IS exercised end to end, in
    # `tests/test_kyo342_worktree_store.py` (in kyosei's own repository): a real `git
    # worktree add`, the write refused from the worktree, the same write run in the main
    # checkout, and both stores' files hashed before and after to prove which one moved.
    "config.py:refuse_store_write#0": (
        "a re-invocation somewhere else: run the same command in the main checkout the "
        "message names, which is where the store it would have written actually lives. "
        "Out of reach here because the remedy is a directory rather than a command line "
        "— this runner passes `--root` and cannot change which working tree it is in — "
        "and because the refusing helper serves every store writer, so there is no one "
        "command line to bind"
    ),
    "prose.py:check_not_shell_mangled#0": (
        "re-invocation through a file: pass the same prose with the --...-file form the "
        "shell does not touch (law 5). The prose is the caller's and this check has no "
        "copy of what they meant to say"
    ),
    "rebuild.py:rebuild_from_export#1": (
        "re-invocation: --force over an existing database. Running it destroys the "
        "database the refusal is protecting, which is why it is a refusal"
    ),
    # KYO-569.c1: git asked to run in a directory that does not exist. Every init path
    # creates or checks the directory first (`kyo init --defaults DIR` makes DIR), so
    # this is the guard for the class rather than a reachable refusal;
    # `tests/test_init_identity_fill_in.py` raises it by calling `_git` directly.
    "scaffold.py:_git#0": (
        "a filesystem action: create the directory, or name one that exists. Neither "
        "is a `kyo` command, and which directory was meant is the caller's"
    ),
    "scaffold.py:check_store_location#0": (
        "re-invocation with a location, or a manual deletion: --store DIR, or clear the "
        "non-empty directory 'yourself first'. The message says yourself because "
        "nothing here should delete it for you"
    ),
    "scaffold.py:check_store_location#1": (
        "re-invocation with a location, or an environment change: --store DIR outside "
        "the repository, or KYOSEI_HOME set somewhere that is not inside one. The "
        "second is a change to the caller's shell, outside this process entirely"
    ),
    "scaffold.py:init_layout#3": (
        "a read and a hand deletion: read .kyosei-store to see which store it names, "
        "and delete it if that store is gone. Deciding it is gone is the judgment"
    ),
    # KYO-416.c2: `--prefix` contradicting a store being adopted. The remedy is a
    # re-invocation with the caller's own arguments changed (drop --prefix, give the
    # store's, or name another --store DIR). `tests/test_init_store_identity.py`
    # provokes the refusal, re-runs without --prefix and with the store's own prefix,
    # and asserts each links the store.
    "scaffold.py:init_layout#4": (
        "re-invocation with different arguments: drop --prefix, pass the store's own "
        "prefix, or --store DIR for a new store. Which of them is meant is the caller's "
        "decision about which store this repository should have"
    ),
    # KYO-298, three of the four in one new function. `controller_only_commands` is a
    # list of command lines a person types into config.yml, so every way it can be
    # malformed is a text editor away from correct and none of them is a `kyo` command
    # — the same shape, and for the same reason, as its sibling `worktree_setup`.
    "config.py:_parse_controller_only_commands#1": (
        "hand edit: write controller_only_commands as a list in config.yml, or delete "
        "the key. Deleting it is the designed opt-out and is as legitimate as fixing "
        "it, which is exactly what no command could decide on the maintainer's behalf"
    ),
    "config.py:_parse_controller_only_commands#2": (
        "hand edit: replace the offending controller_only_commands entry with a "
        "command line, or remove it. The entry is the project's own shell text and "
        "only a person knows which command it was meant to be"
    ),
    "config.py:_parse_controller_only_commands#4": (
        "hand edit: give the controller_only_commands entry a command, or remove it. "
        "An entry that parses to no tokens at all (a lone quote, whitespace) is a "
        "half-typed line, and what it was meant to say is not derivable from it"
    ),
    # KYO-298. The first refusal on this surface whose remedy is neither a `kyo`
    # command nor an edit: it is "run the tests that cover your change instead", which
    # is a command only the worker's own project can spell — the same boundary
    # `worktree_setup` exists at, one guard over. The message's other half, the
    # KYOSEI_SUITE_OK=1 re-invocation, IS runnable in principle and is deliberately not
    # run here: running it would be asserting that this dispatch's suite is safe to run
    # beside the controller's, which is the judgment the refusal exists to ask a human
    # for (cmd_dispatch.py:_cmd_dispatch_create#5's reasoning). Both halves ARE run
    # against a real store in tests/test_kyo298_command_guard.py: the remedy comes back
    # clean at this same site, and the override lets the command through.
    "cmd_check_command.py:cmd_check_command#0": (
        "the project's own test command, plus a re-invocation: run the test files that "
        "cover your change (only the worker's project knows how to spell that), or "
        "re-run the refused command prefixed with KYOSEI_SUITE_OK=1, which is a "
        "deliberate disagreement rather than a repair"
    ),
    # KYO-312, and the same boundary as the site above it in every respect: the remedy is
    # a shell command in the worker's own project ("detach it, then `sleep 180; cat
    # <status file>`" -- the run, its log and its status file are the worker's to spell),
    # and the KYOSEI_LONG_OK=1 half is a deliberate disagreement rather than a repair,
    # which running here would be asserting on a worker's behalf. Both halves ARE run
    # against a real store in tests/test_kyo298_command_guard.py: the sliced wait this
    # message spells out comes back clean through this same guard in the same worktree,
    # and the override lets the long call through.
    "cmd_check_command.py:cmd_check_command#1": (
        "the worker's own sliced wait, plus a re-invocation: detach the run and look in "
        "bounded slices under the ceiling (the command, its log and its status file are "
        "the worker's to spell), or re-run the call with KYOSEI_LONG_OK=1 in front of it, "
        "which is a deliberate disagreement rather than a repair"
    ),
    # KYO-312, two of two in one parser, and `_parse_controller_only_commands`'s shape:
    # `worker_command_timeout_ceiling_seconds` is a number a person types into config.yml,
    # so every way it can be malformed is a text editor away from correct and none of them
    # is a `kyo` command. Recorded rather than provoked for `config.py:load_config#7`'s
    # reason and not for want of a provoker -- tests/test_kyo298_command_guard.py writes
    # `4m`, asserts the refusal, writes `240`, asserts it loads, then deletes the line and
    # asserts the ceiling is gone: the KYO-5.c2 guarantee run against both endings these
    # messages name.
    "config.py:_parse_timeout_ceiling_seconds#0": (
        "hand edit: write worker_command_timeout_ceiling_seconds as a whole number of "
        "seconds in config.yml, or delete the line. Deleting it is the designed opt-out "
        "and is as legitimate as fixing it -- there is no default ceiling, because the "
        "number describes the consumer's harness rather than their project"
    ),
    "config.py:_parse_timeout_ceiling_seconds#1": (
        "hand edit: give worker_command_timeout_ceiling_seconds a positive number of "
        "seconds, or delete the line. A zero or negative ceiling would refuse every timed "
        "command in a running worktree, which is the shape of a half-finished edit rather "
        "than a decision a command could complete on the maintainer's behalf"
    ),
    # KYO-331, and the RE-INVOCATION shape the note above names first — with the sharpest
    # version of why the argument is the caller's. `--reviewed` is not a value this check
    # could supply: it is a statement that a human read the worker's premise_false[] and
    # disagreements[] entries, which is the one judgment `kyo accept` exists NOT to
    # automate. A check that ran the remedy would be manufacturing that reading.
    # The verified half is in tests/test_kyo329_accept.py, where the refusal is provoked,
    # every entry is asserted present in its text, and `--reviewed` is then run and shown
    # to apply the report — the KYO-5.c2 guarantee, just not from this file's machinery.
    # KYO-540.c1: the remedy is an amending REPORT FILE (the worker's own document, ending
    # commits[] on the branch tip and carrying "amends"), then the same accept again. The file
    # is not something this check can write, so it is out of reach here; the clearing is
    # proved in tests/test_kyo_accept_tip_and_summary.py::
    # test_an_amending_report_ending_on_the_tip_clears_the_refusal.
    "cmd_accept.py:_refuse_if_branch_moved#0": (
        "a hand-written amending report: the worker (or the orchestrator) adds the missing "
        "commit(s) to commits[], sets amends, and files it; this check "
        "cannot author a report's contents. Proved by tests/test_kyo_accept_tip_and_summary.py"
        "::test_an_amending_report_ending_on_the_tip_clears_the_refusal"
    ),
    "cmd_accept.py:_refuse_until_reviewed#0": (
        "a re-invocation whose argument is the controller's own statement: read the "
        "entries the refusal printed, then pass --reviewed. Out of reach in the strongest "
        "sense this shape has - the flag asserts that a person read something, so running "
        "it here would assert the act the refusal exists to require. It names no `kyo` "
        "command deliberately, because a pasteable line is what a rubber stamp is made of"
    ),
    # KYO-330: `kyo new/promote/spawn --from FILE` reads title/scope/plan/touches and a
    # criteria list out of one markdown file. Both sites below are a hand edit of THAT
    # file -- the caller's, not this store's -- and neither message names a `kyo`
    # command: manually verified (this dispatch's own report) by provoking each,
    # applying the named edit, and re-running unchanged, which cleared it both times.
    "cmd_records.py:_parse_criteria_items#0": (
        "hand edit of the --from file: a line under '## Criteria' that is not a list "
        "item needs a '- ' (or '1. ') marker, or has to be joined onto the item above "
        "it. No `kyo` command edits a file --from reads -- it is the caller's markdown, "
        "the same way a --criteria-file's contents are"
    ),
    "cmd_records.py:_parse_record_file#8": (
        "hand edit of the --from file: `kyo promote` has no --description-file for "
        "--from to stand in for (its description is always the idea being promoted), "
        "so prose outside the front matter and '## Criteria' has to come out of the "
        "file by hand. No `kyo` command edits it -- same reason as "
        "_parse_criteria_items#0 just above"
    ),
    # KYO-333.c5: a third site in the same --from file, same shape as the two above --
    # manually verified by this dispatch's own test
    # (tests/test_kyo329_length_warnings.py::test_quoting_the_touches_line_clears_the_refusal):
    # provoke it, quote the offending 'touches:' line, re-run --from unchanged, and the
    # "not a string" refusal is gone (a later refusal can still fire if the quoted path
    # itself does not exist -- that is touch validation, a different site, doing its own
    # job correctly).
    "cmd_records.py:_reject_non_string_touch_lines#0": (
        "hand edit of the --from file: quote the whole 'touches:' line that a colon "
        "and a space turned into a YAML mapping (e.g. \"path#4. Section: subsection\") "
        "so it reads back as one string. No `kyo` command edits it -- same reason as "
        "_parse_criteria_items#0 above: it is the caller's markdown, not anything kyo "
        "stores"
    ),
    # KYO-403's git remedy, run by tests/test_split_worktree_layout.py (its c3 refusal,
    # `dispatch-needs-a-committed-store-link`, was removed by KYO-580).
    # c4: a worker branch that adds, deletes or re-points `.kyosei`
    # (test_check_branch_refuses_a_branch_that_deletes_or_repoints_the_link and
    # test_check_branch_refuses_a_branch_that_adds_the_link run `git checkout main --
    # .kyosei` / `git rm .kyosei` on the branch, commit, and re-run check-branch clean).
    "cmd_check_branch.py:cmd_check_branch#0": (
        "a git commit on the worker's branch restoring `.kyosei` to the base "
        "(`git checkout <against> -- .kyosei`, or `git rm .kyosei` when the base has "
        "none). Not a `kyo` command -- the file is the code repository's, and only the "
        "branch's own history can undo the change"
    ),
}


#: KYO-84: candidates the recognisers pull in that are not remedies at all — the
#: command this package itself ran, quoted so the reader can see what was attempted.
#: ``git status --porcelain`` in `kyo check-clean`'s failure is not something to run; it
#: is what was run.
#:
#: Excluded from the denominator, and NAMED, which is the whole difference from before:
#: a site that carries a backticked command was previously either on the surface for
#: naming a `kyo` one or invisible, and there was no third thing to be. The escape hatch
#: this table opens is closed again mechanically rather than by intent — see
#: ``test_the_not_a_remedy_table_cannot_absorb_a_real_remedy``: an entry may not name a
#: `kyo` command and may not carry an instruction clause, so the only site that can ever
#: sit here is one whose sole candidate signal is a quoted non-`kyo` command.
NOT_A_REMEDY: dict[str, str] = {
    "cmd_check_clean.py:cmd_check_clean#0": (
        "`git status --porcelain` is the command kyo ran to answer the question, quoted "
        "because it failed"
    ),
    "git_queries.py:_git_rev_parse_head#2": (
        "`git rev-parse HEAD` is the command kyo ran, quoted because it returned nothing"
    ),
    "git_queries.py:_git_current_branch#1": (
        "`git rev-parse --abbrev-ref HEAD` is the command kyo ran, quoted with its "
        "stderr as the evidence that this is not a git repository"
    ),
    "git_queries.py:branch_changed_files#0": (
        "`git rev-parse --verify` is the command kyo ran to resolve the ref, quoted "
        "because the ref is unknown"
    ),
    "git_queries.py:branch_changed_files#1": (
        "`git diff --name-only` is the command kyo ran, quoted with its stderr"
    ),
}


def sites_that_name_a_remedy() -> tuple[ErrorSite, ...]:
    """THE DENOMINATOR (KYO-84.c2): candidates, less the ones that are not remedies.

    Defined here rather than beside the walk because it is not a property of the source
    tree: it is the walk's over-collection with one hand-written judgment subtracted,
    and that judgment is :data:`NOT_A_REMEDY`, three inches up, where a reader who wants
    to argue with the number can see every entry that shrank it.
    """
    return tuple(site for site in remedy_naming_error_sites() if site.key not in NOT_A_REMEDY)


# ---------------------------------------------------------- KYO-77: the tests


ALL_SITE_KEYS = tuple(site.key for site in remedy_naming_error_sites())


def test_the_site_index_finds_the_error_site_kyo73_was_filed_against() -> None:
    """The canary for the whole denominator, and the reason it resolves names.

    ``store.py``'s missing-database failure is the site the fifth instance of this
    defect class was filed against, and its message carries no remedy text at all: it
    is ``f"{path} is missing; {missing_database_remedy(config)}"``, and the helper
    lives in another module and picks between two remedies by reading the disk. A
    surface walk that only read literals at the raise would miss it — which is to say
    it would report full coverage of a surface with the motivating case cut out of it.
    """
    missing_db = next(
        site
        for site in remedy_naming_error_sites()
        if site.module == "store.py"
        and site.function == "open_store"
        and "kyo init" in site.remedies
    )
    assert "kyo rebuild" in missing_db.remedies, missing_db
    # And the resolution is not accidentally reading the whole file: a site that names
    # nothing stays out of the denominator.
    assert len(remedy_naming_error_sites()) < len(deliberate_error_sites())


def test_the_site_index_covers_every_error_class_the_cli_reports() -> None:
    """:data:`ERROR_CLASS_NAMES` is matched by name against the AST, so it has to be
    kept honest against the classes themselves: a new subclass of :class:`KyoseiError`
    that this list does not know about would silently shrink the surface."""
    live = {cls.__name__ for cls in _all_subclasses(KyoseiError)} | {"KyoseiError"}
    assert live == set(ERROR_CLASS_NAMES), (
        f"kyosei's deliberate-error classes are {sorted(live)}; this check walks the AST "
        f"for {sorted(ERROR_CLASS_NAMES)} and would not see the difference"
    )


def _all_subclasses(cls: type) -> set[type]:
    # A class exists for __subclasses__() only once its module is imported; run alone,
    # nothing else imports docs_index (IndexStale), so import every module first.
    import importlib
    import pkgutil

    import kyosei

    for module in pkgutil.iter_modules(kyosei.__path__):
        if module.name != "__main__":
            importlib.import_module(f"kyosei.{module.name}")
    found = set(cls.__subclasses__())
    for sub in list(found):
        found |= _all_subclasses(sub)
    return found


# --------------------------------------------------------------------- KYO-187
#
# The refusal itself. Its reasoning is above :func:`raises_that_hide_their_class`.


def test_no_raise_site_hides_its_class_behind_a_variable() -> None:
    """KYO-187.c1: nothing in ``src/kyosei/`` raises a name.

    This is the whole rule, and it is what makes the surface above a surface rather
    than a sample: a site the walk cannot name is not reported as unclassified, it is
    not reported at all, so the only place the omission can be made to show is here.
    """
    hidden = raises_that_hide_their_class(SRC_DIR)
    assert not hidden, (
        f"{len(hidden)} raise site(s) name a variable instead of constructing the error "
        f"at the raise: {list(hidden)}. The remedy-coverage walk matches BY CLASS NAME on "
        f"the raise node, so each of these is off the surface entirely — no table entry "
        f"goes stale and nothing goes red when its message does. Build the error at the "
        f"raise; if it carries a payload, give it a class in `errors.py` that takes that "
        f"payload as a required keyword argument, the way `errors.GuardRefusal` and "
        f"`errors.ExportSchemaAhead` do."
    )


def test_a_newly_added_raise_of_a_variable_is_caught(tmp_path: Path) -> None:
    """KYO-187.c4, against a real copy of the tree rather than against the rule.

    The test above passes today because the five sites KYO-187 found were converted.
    What has to hold tomorrow is that ADDING a sixth fails — and this makes the edit
    and re-derives the answer from what is on disk, instead of asserting the property
    by restating it.

    The second half is the part worth reading: the walk still cannot see the added
    site. The refusal is not a redundant second opinion about something ``sites_in``
    would have caught; it is the only thing between that shape and the surface.
    """
    copy = _copy_of_the_source_tree(tmp_path)
    _insert_lines_above(
        copy / "cmd_store.py",
        "def cmd_doctor(",
        [
            "def _a_refusal_added_later(data: dict) -> None:",
            '    error = KyoseiError("something broke; run `kyo rebuild --force`")',
            "    error.data = data",
            "    raise error",
            "",
            "",
        ],
    )
    hidden = raises_that_hide_their_class(copy)
    assert "cmd_store.py:_a_refusal_added_later: raise error" in hidden, hidden

    # And the reason this check has to exist: nothing else notices. The added site
    # names `kyo rebuild --force`, which would land it in the classification gate as
    # unclassified — if the walk could see it at all, which it cannot.
    keys = [site.key for site in sites_in(copy)]
    assert not [key for key in keys if "_a_refusal_added_later" in key]


def test_the_refusal_reads_the_raise_shape_and_not_a_name(tmp_path: Path) -> None:
    """What the predicate is, in both directions, on a module written to be read.

    A bare ``raise`` re-raises the active exception and carries no class to hide; it is
    outside this by construction. ``raise exc`` inside ``except ... as exc:`` DOES name
    a variable, and it is caught — deliberately, and not because nobody thought of it:
    ``src/kyosei/`` contains zero of them, so exempting the shape would be writing an
    allowlist for a case that does not exist and leaving it there for one that does.
    """
    module = tmp_path / "synthetic.py"
    module.write_text(
        "\n".join(
            [
                "from .errors import KyoseiError",
                "",
                "",
                "def visible() -> None:",
                '    raise KyoseiError("visible: run `kyo rebuild` to clear it")',
                "",
                "",
                "def hidden() -> None:",
                '    error = KyoseiError("hidden: run `kyo rebuild --force` to clear it")',
                '    error.data = {"ok": False}',
                "    raise error",
                "",
                "",
                "def bare_reraise() -> None:",
                "    try:",
                "        visible()",
                "    except KyoseiError:",
                "        raise",
                "",
                "",
                "def rebound_reraise() -> None:",
                "    try:",
                "        visible()",
                "    except KyoseiError as exc:",
                "        raise exc",
                "",
            ]
        ),
        encoding="utf-8",
    )
    assert raises_that_hide_their_class(tmp_path) == (
        "synthetic.py:hidden: raise error",
        "synthetic.py:rebound_reraise: raise exc",
    )
    # The gap this closes, demonstrated rather than described: of the four raises above,
    # the walk sees exactly the one that constructs its error at the raise.
    assert [site.key for site in sites_in(tmp_path)] == ["synthetic.py:visible#0"]


def test_neither_envelope_carrying_refusal_can_be_built_without_its_payload() -> None:
    """KYO-187.c3, by construction rather than by inspection.

    The five converted sites cannot regress to a payload-free refusal by someone
    deleting a line, because there is no payload-free form of either class to raise.
    That is the same guarantee :class:`SchemaMismatch` gives at the store boundary and
    the reason both of these are classes at all.
    """
    with pytest.raises(TypeError):
        GuardRefusal("contract", ["unmet"])  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        BrokenInvariants(["broken"])  # type: ignore[call-arg]

    envelope = {"ok": False, "command": "check-branch", "branch": "b"}
    assert GuardRefusal("contract", ["unmet"], data=envelope).data == envelope
    assert BrokenInvariants(["broken"], data=envelope).data == envelope
    # Copied, not aliased: the envelope a command hands over is the same dict it would
    # have returned on the clean path, and a refusal must not be able to report a later
    # edit of it.
    refusal = GuardRefusal("contract", ["unmet"], data=envelope)
    envelope["ok"] = True
    assert refusal.data["ok"] is False

    # Both keep their parent's exit code, which is what `cli.main` returns: converting
    # the sites must not have changed what a caller's shell sees.
    assert GuardRefusal("contract", ["unmet"], data={}).exit_code == EXIT_CONTRACT
    assert BrokenInvariants(["broken"], data={}).exit_code == EXIT_DOCTOR


# --------------------------------------------------------------------- KYO-77.c2


def test_every_remedy_naming_site_is_classified() -> None:
    """KYO-77.c2's gate: no site falls off the edge of the table.

    The five tables between them must account for every site the surface walk finds.
    A message that starts naming a remedy — a new one, or an old one that gains a
    ``kyo`` command — lands here as unclassified and fails, which is the only thing
    that keeps the coverage figure below from decaying into a number about a
    denominator someone forgot to recompute.

    KYO-84 widened what "the surface walk finds" and added two tables to account for
    the new half, so this gate now covers a message whose remedy is a `git checkout`, a
    re-invocation or a hand edit. It is the same gate: the change is what it quantifies
    over. Before it, such a message was not merely unclassified — it never arrived.
    """
    surface = set(ALL_SITE_KEYS)
    classified = _classified()
    unclassified = surface - classified
    assert not unclassified, (
        f"{len(unclassified)} error site(s) name a remedy and are in none of PROVOKED, "
        f"UNREACHED, FINDINGS, RECORDED or NOT_A_REMEDY: {sorted(unclassified)}. Provoke "
        f"it; or say in UNREACHED why this check cannot; or, if its remedy is not a `kyo` "
        f"command, say in RECORDED what the remedy is and why running it is out of reach."
    )
    stale = classified - surface
    assert not stale, (
        f"{sorted(stale)} are classified here but the surface walk no longer finds them — "
        f"the line moved, or the message stopped naming a remedy."
    )
    # Pairwise, not n-way: a key in two of the tables is already a contradiction
    # (provoked and unreached at once), and an n-way intersection is empty whenever any
    # one table is — which, since KYO-82 emptied FINDINGS, is always.
    tables = {
        "PROVOKED": set(PROVOKED),
        "UNREACHED": set(UNREACHED),
        "FINDINGS": set(FINDINGS),
        "RECORDED": set(RECORDED),
        "NOT_A_REMEDY": set(NOT_A_REMEDY),
    }
    for left, right in ((a, b) for a in tables for b in tables if a < b):
        assert not tables[left] & tables[right], (
            f"{sorted(tables[left] & tables[right])} are in both {left} and {right}"
        )

    # KYO-84: the two halves are a property of the MESSAGE, not of which table someone
    # filed the site in, so they have to agree. A site whose message names a `kyo`
    # command is runnable and belongs in the KYO-77 tables; one whose message does not
    # cannot be provoked by machinery that has vocabulary only for `kyo` commands.
    runnable = {site.key for site in runnable_remedy_sites()}
    misfiled = (set(PROVOKED) | set(FINDINGS) | set(UNREACHED)) - runnable
    assert not misfiled, (
        f"{sorted(misfiled)} are filed as sites this check runs commands at, but their "
        f"messages name no `kyo` command — they belong in RECORDED"
    )
    assert not set(RECORDED) & runnable, (
        f"{sorted(set(RECORDED) & runnable)} are recorded as unrunnable but their messages "
        f"name a `kyo` command; provoke them, or say in UNREACHED why not"
    )


def test_the_not_a_remedy_table_cannot_absorb_a_real_remedy() -> None:
    """KYO-84: the escape hatch, closed mechanically rather than by good intentions.

    :data:`NOT_A_REMEDY` exists for one thing — this package quotes the command it ran
    when that command fails, and no syntax tells that apart from a command offered to
    the reader. It also removes sites from the denominator, which makes it the one place
    in this file where a hand-written entry can flatter the coverage figure, and "we
    only put honest things there" is not a mechanism.

    So it is bounded by what it may contain: a site whose message names a `kyo` command,
    or issues an instruction, is not a quoted failure and cannot sit here whatever the
    reason says. That leaves exactly one shape it can hold.
    """
    sites = {site.key: site for site in remedy_naming_error_sites()}
    for key in NOT_A_REMEDY:
        site = sites[key]
        assert not site.remedies, (
            f"{key} is excluded from the remedy surface as 'not a remedy', but its message "
            f"names {list(site.remedies)} — a `kyo` command is a remedy by construction"
        )
        assert not imperative_remedies(_message_of(site)), (
            f"{key} is excluded from the remedy surface as 'not a remedy', but its message "
            f"instructs the reader: {list(imperative_remedies(_message_of(site)))}"
        )


@cache
def _messages() -> dict[str, str]:
    """Site key -> the message text the surface walk read, for tests that re-read it."""
    index = _SourceIndex(SRC_DIR)
    out: dict[str, str] = {}
    for module, tree in sorted(index.trees.items()):
        owners = _enclosing_functions(tree)
        raises = sorted(
            (n for n in ast.walk(tree) if isinstance(n, ast.Raise) and n.exc is not None),
            key=lambda n: (n.lineno, n.col_offset),
        )
        seen: dict[str, int] = {}
        for node in raises:
            called = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            name = called.id if isinstance(called, ast.Name) else getattr(called, "attr", None)
            if name not in ERROR_CLASS_NAMES:
                continue
            function = owners.get(node.lineno, "(module level)")
            ordinal = seen.get(function, 0)
            seen[function] = ordinal + 1
            out[f"{module}.py:{function}#{ordinal}"] = index.message_text(node.exc, module)
    return out


def _message_of(site: ErrorSite) -> str:
    return _messages()[site.key]


class RemedyCoverage(UserWarning):
    """KYO-77.c2, made visible on every run rather than only on failure.

    pytest prints its warnings summary under ``-q``, which is what this suite runs
    with, so the figure and the list of sites this check does not reach are in the
    output of `make test` whether or not anything failed. A skipped test would say
    "something here did not run"; this says which error messages are unchecked, by
    name, which is the difference KYO-77.c2 asks for.
    """


def test_the_coverage_of_this_check_is_reported_by_name(tmp_path: Path) -> None:
    """The coverage figure itself: how much of the remedy surface is exercised.

    This is the deliverable, not the count. A check that reached four sites of forty
    and reported "all remedies verified" would be worse than no check, because it
    converts an unknown into a false assurance — the exact failure KYO-31, KYO-34,
    KYO-36, KYO-64 and KYO-73 were each filed against. So the number is emitted every
    run, alongside the name of every site it does not cover and why.

    KYO-84.c2 is what the shape below is: the denominator is every site that names a
    remedy OF ANY KIND, and the report separates the remedies this check can RUN from
    the ones it can only RECORD. It used to say "N sites name a `kyo` command" and
    divide by that, which quietly made the surface the same shape as the tool — the
    percentage went UP when a message stopped naming a command, and the site went
    invisible in the same movement. There are now two percentages because there are two
    honest questions, and the one that used to be reported alone is the smaller claim.
    """
    surface = len(sites_that_name_a_remedy())
    runnable = len(runnable_remedy_sites())
    recorded = surface - runnable
    lines = [
        "KYO-84 — remedy applicability, coverage of the error-message remedy surface:",
        f"  {len(deliberate_error_sites())} deliberate error sites in src/kyosei/",
        f"  {len(ALL_SITE_KEYS)} of them name something a reader could act on, of which",
        f"      {len(NOT_A_REMEDY)} are the command kyo itself ran, not a remedy (named below)",
        f"  {surface} name a remedy — THE DENOMINATOR, and of those",
        f"      {runnable} name a `kyo` command, which this check can RUN",
        f"      {recorded} name a remedy it can only RECORD: a shell command in someone",
        "        else's repository, a re-invocation whose argument is the caller's, a hand edit",
        "",
        f"  {len(PROVOKED)} provoked and checked   "
        f"({len(PROVOKED) * 100 // runnable}% of the runnable half, "
        f"{len(PROVOKED) * 100 // surface}% of the whole remedy surface)",
        f"  {len(FINDINGS)} provoked and FOUND BROKEN (see below)",
        f"  {len(UNREACHED)} runnable and not provoked (see below)",
        f"  {len(RECORDED)} recorded, and unverifiable by this check (see below)",
        "",
        "  not provoked:",
        *(f"    {key} — {why}" for key, why in sorted(UNREACHED.items())),
        "",
        "  provoked and found broken:",
        *(f"    {key} — {why}" for key, why in sorted(FINDINGS.items())),
        "",
        "  present but unverifiable — the remedy is real and this check cannot run it:",
        *(f"    {key} — {why}" for key, why in sorted(RECORDED.items())),
        "",
        "  on no surface — the backticked command is the one kyo ran:",
        *(f"    {key} — {why}" for key, why in sorted(NOT_A_REMEDY.items())),
    ]
    warnings.warn(RemedyCoverage("\n".join(lines)), stacklevel=1)

    # A floor, so the check cannot quietly rot into a formality. Deliberately below
    # what it covers today: it is a ratchet against deletion, not a target.
    assert len(PROVOKED) >= 20, f"only {len(PROVOKED)} sites provoked of {runnable} runnable"
    assert len(PROVOKED) + len(FINDINGS) + len(UNREACHED) == runnable
    assert len(RECORDED) == recorded
    assert len(RECORDED) + len(NOT_A_REMEDY) + runnable == len(ALL_SITE_KEYS)
    # The widening is not a relabelling: there are sites on this surface that KYO-77's
    # would not have carried at all. If this ever stops being true the record's premise
    # has gone with it, and the extra machinery should go too.
    assert recorded > 0, "the widened surface contains nothing the `kyo`-only one did not"


# --------------------------------------------------------------------- KYO-77.c1


@pytest.mark.parametrize("key", ALL_SITE_KEYS, ids=ALL_SITE_KEYS)
def test_the_remedy_a_message_names_applies_where_it_was_raised(key: str, tmp_path: Path) -> None:
    """KYO-77.c1, once per error site that names a remedy.

    Parametrised over the SURFACE rather than over the provoker table, on purpose: a
    site nobody has provoked appears here as a skip with its reason attached, so
    ``pytest -rs`` names it and the headline skip count moves when coverage changes.
    Parametrising over :data:`PROVOKED` instead would make an unreached site invisible,
    which is the failure mode KYO-77.c2 exists to prevent.

    KYO-84 keeps that argument and applies it to the widened surface: a site whose
    remedy this check cannot run appears here too, as a skip carrying what the remedy is
    and why. ``pytest -rs`` therefore names every message on the surface, runnable or
    not, which is the difference between a remedy that is unverified and one that was
    never on the list.
    """
    if key in NOT_A_REMEDY:
        pytest.skip(f"KYO-84: {key} names no remedy: {NOT_A_REMEDY[key]}")
    if key in RECORDED:
        pytest.skip(f"KYO-84 records but cannot run the remedy at {key}: {RECORDED[key]}")
    if key in UNREACHED:
        pytest.skip(f"KYO-77 does not reach {key}: {UNREACHED[key]}")
    if key in FINDINGS:
        result = check_remedies_apply(key, _FINDING_EXPECTATIONS[key], tmp_path)
        assert result.failures, (
            f"{key} is recorded in FINDINGS as a message whose named remedy does not apply, "
            f"and this check no longer finds anything wrong with it. If it was fixed, move it "
            f"into PROVOKED."
        )
        pytest.xfail(f"KYO-77 finding at {key}: {FINDINGS[key]}\n" + "\n".join(result.failures))

    result = check_remedies_apply(key, PROVOKED[key], tmp_path)
    assert not result.failures, (
        f"{key} names a remedy that does not apply to the state that produced it:\n  "
        + "\n  ".join(result.failures)
    )


#: The findings are provoked the same way anything else is; they are only separated
#: because their fix is a message change the dispatch that found them may not make.
#: Empty because :data:`FINDINGS` is — KYO-82 fixed the one entry and it moved into
#: :data:`PROVOKED`, taking its provoker (``_kinds_yml_is_gone``) with it.
_FINDING_EXPECTATIONS: dict[str, Expectation] = {}


def test_a_finding_is_wired_to_a_provoker() -> None:
    """The invariant :data:`FINDINGS` would otherwise only announce as a ``KeyError``.

    Both tables emptied when KYO-82 fixed the site KYO-77 found, so the branch above
    that reads them has no live user. It is kept because the next finding needs it,
    and this is what stops it rotting while unused: a finding with no provoker cannot
    be asserted still-broken, so it would silently become a finding nobody checks —
    which is the shape of the whole defect class this file is about.
    """
    assert set(FINDINGS) == set(_FINDING_EXPECTATIONS), (
        f"findings with no provoker: {sorted(set(FINDINGS) - set(_FINDING_EXPECTATIONS))}; "
        f"provokers with no finding: {sorted(set(_FINDING_EXPECTATIONS) - set(FINDINGS))}"
    )


# --------------------------------------------------------------------- KYO-77.c3
#
# The check, pointed at the tree as it stood before KYO-73's fix. Reconstructed rather
# than checked out: `store.open_store` now delegates the whole remedy to
# `currency.missing_database_remedy`, so restoring the historic message is one
# substitution of that helper's return value —
#
#     raise KyoseiError(f"{config.db_path} is missing; run `kyo init` or restore it")
#
# — and the message the check then reads is that line, byte for byte.


def _historic_remedy(text: str, monkeypatch) -> None:
    """Make ``store.open_store``'s missing-database message read ``text``.

    ``open_store`` imports the helper inside the function body (to break an import
    cycle), so it is looked up on the module at every call and this substitution
    reaches it.
    """
    from kyosei import currency

    monkeypatch.setattr(currency, "missing_database_remedy", lambda config: text)


def test_the_check_fails_on_the_message_kyo73_was_filed_against(tmp_path, monkeypatch) -> None:
    """KYO-77.c3: run the check against the pre-KYO-73 missing-database message.

    Every word of that message was true and `kyo init` was a real command that really
    scaffolds stores. It was wrong because of the state it was printed in — a store
    that already exists, whose database alone is gitignored — and `kyo init` refuses
    that state. Nothing that reads the message can tell; only running the command from
    the state that produced the error can, and that is what fails here.
    """
    _historic_remedy("run `kyo init` or restore it", monkeypatch)

    result = check_remedies_apply("store.py:open_store#0", Expectation(_missing_database), tmp_path)

    assert result.named == ("kyo init",), result
    assert result.verified == (), result
    assert result.failures, "the check passed the message KYO-73 was filed against"
    assert any("kyo init" in f and "exits" in f for f in result.failures), result.failures


def test_the_check_also_fails_a_remedy_that_runs_but_leaves_the_error(
    tmp_path, monkeypatch
) -> None:
    """The other arm of KYO-77.c1, which the KYO-73 instance does not exercise.

    KYO-73's dead end announced itself with a non-zero exit. The worse shape — and the
    one a checker that only looked at exit codes would wave through — is a named remedy
    that runs perfectly and changes nothing about the situation. `kyo docs check` is a
    real command that succeeds against a store with no database (it reads the docs
    skeleton and never opens one), and it does not reconstruct one.
    """
    _historic_remedy("run `kyo docs check` or restore it", monkeypatch)

    result = check_remedies_apply("store.py:open_store#0", Expectation(_missing_database), tmp_path)

    assert result.named == ("kyo docs check",), result
    assert result.verified == (), result
    assert any("leaves the same error" in f for f in result.failures), result.failures


def test_the_check_passes_the_message_kyo73_left_behind(tmp_path) -> None:
    """The other direction, so the two tests above are not just a broken check.

    Unpatched, the same site's real message names `kyo rebuild`, and running exactly
    that against a fresh clone's state clears it. This is the assertion that says the
    failures above are about the remedy and not about the harness.
    """
    key = "store.py:open_store#0"
    result = check_remedies_apply(key, PROVOKED[key], tmp_path)

    assert result.failures == (), result
    assert result.verified == ("kyo rebuild",), result


# ====================================================================== KYO-82
#
# The sixth instance of the class, and the first where the honest answer was that no
# `kyo` command applies. KYO-73's was the easy shape — a remedy existed and was merely
# unnamed, and correcting the message was the whole fix. Here `kinds.yml` is committed
# configuration rather than an export artifact: `kyo rebuild` has no source to write it
# from, and scaffolding refuses a directory that already holds a store, which is the
# state this error is raised in. So the message names version control and the packaged
# default, and says in as many words that no command does this.
#
# That leaves KYO-77's machinery with nothing to verify at the site, because it has
# vocabulary only for `kyo` commands: it can check that the one command the message
# mentions is a mention and not an offer, and no more. The remedies that are NOT
# commands are checked here instead, the same way everything else in this file is —
# take what the message actually printed, run exactly that, assert the error is gone.


def _store_with_no_kinds_yml(at: Path) -> tuple[Path, Run]:
    """A store missing ``kinds.yml``, and the failure a command gets from it."""
    root, _ = _project_with_a_record(at)
    kinds_yml(root).unlink()
    failed = invoke(["--root", str(root), "list"])
    assert failed.code != 0, failed
    return root, failed


def shell_command_the_message_spells(err: str, pattern: str) -> list[str]:
    """One command line lifted out of a message and split the way a shell would.

    The message is spelled FOR a shell: its paths go through ``shlex.quote`` so the
    lines are runnable as printed. So it has to be read back with ``shlex.split``, and
    the pattern's only job is to find where the command starts and ends — never to
    tokenise it.

    This shipped as ``(cp \\S+ \\S+)``: a whitespace-hostile pattern reading a
    deliberately whitespace-safe message. It matched a bare path and could not match a
    quoted one, so it passed everywhere a dispatched worker can look and failed the
    moment it merged. :func:`test_a_quoted_path_in_a_named_recovery_is_read_back` is
    the regression; the KYO-83.d1 report says why no worker could have seen it.
    """
    spelled = re.search(pattern, err)
    assert spelled, f"no command matching {pattern!r} in:\n{err}"
    return shlex.split(spelled.group(1))


#: Where each recovery sits in the message. Anchored on the prose around the command
#: rather than on the command's own shape, because the shape is a shell's business and
#: :func:`shlex.split` is what knows it.
_PACKAGED_DEFAULT_RECOVERY = r"copy the packaged default: (cp .+?) — then re-add"
_VERSION_CONTROL_RECOVERY = r"\((git checkout -- .+?)\), where the store is tracked"


@pytest.fixture(params=["plain", "with a space"], ids=["plain-path", "spaced-path"])
def kinds_recovery_ground(request, tmp_path: Path, monkeypatch) -> Path:
    """A directory to build the store under — and, in one of the two runs, a space.

    Both paths the message names are the test's to control and both can carry one: the
    store's own (the ``cp`` destination, and the ``git checkout`` argument) and the
    packaged default's (the ``cp`` source, which is wherever the kyosei package was
    imported from). The spaced parametrisation moves both.

    It is a fixture rather than the one-off manual check that found the bug because a
    manual check is not evidence anyone gets to keep. Dispatch worktrees live under a
    spaceless home by design (KYO-70) while the repository they branch from may not,
    so this whole class of bug is invisible from where the work
    is done and visible the moment it merges. Two parametrised runs are the cheapest
    way to make a worker's `make test` and the controller's the same run.
    """
    ground = tmp_path / request.param
    ground.mkdir(parents=True, exist_ok=True)
    if " " in request.param:
        packaged = scaffold.defaults_dir()
        assert packaged is not None, "this build ships no defaults/ directory to relocate"
        # The whole directory, not just kinds.yml: `kyo init` copies every packaged
        # asset through this same locator, so relocating half of it would fail the
        # scaffold rather than exercise the assertion under test.
        spaced = ground / "packaged assets"
        shutil.copytree(packaged, spaced)
        # KYO-593: install_skill reads the skill from the plugin's tree, not defaults/.
        for module in (scaffold, install_hooks, install_procedures):
            monkeypatch.setattr(module, "defaults_dir", lambda: spaced)
    return ground


def test_a_quoted_path_in_a_named_recovery_is_read_back() -> None:
    """The regression, at the unit, with no store and no filesystem in the way.

    The bug was not in the message — the message was right, and right for exactly the
    reason it was written that way. It was in the reading. Both directions are pinned
    here so neither the quoting nor the parse can drift alone: the path is quoted
    because it holds a space, and it comes back out whole.
    """
    original = "/Users/someone/My Projects/kyosei/src/kyosei/defaults/kinds.yml"
    quoted = shlex.quote(original)
    assert quoted != original, "shlex.quote left this path bare; the case is not exercised"
    message = (
        f"otherwise copy the packaged default: cp {quoted} /tmp/store/kinds.yml — then "
        f"re-add any kinds this project defined beyond the shipped set"
    )

    argv = shell_command_the_message_spells(message, _PACKAGED_DEFAULT_RECOVERY)

    assert argv == ["cp", original, "/tmp/store/kinds.yml"], argv


def test_the_missing_kinds_yml_message_does_not_name_kyo_init(tmp_path: Path) -> None:
    """KYO-82.c1 and c2, on the face of the message.

    The old text was ``run `kyo init` or restore it``, word for word the sentence
    ``store.py`` raised for a missing database until KYO-73 — the same defect copied
    into another module, and never found because KYO-73 was filed from the database
    symptom. `kyo init` scaffolds a project and refuses a directory that already holds
    a store, so it could not apply to the state that printed it.

    Asserted as an absence AND as a presence: an absence alone would be satisfied by a
    message that simply stopped saying anything useful.
    """
    _, failed = _store_with_no_kinds_yml(tmp_path)

    assert "kyo init" not in failed.err, failed.err
    assert "no `kyo` command restores it" in failed.err, failed.err
    # The one command it does name, it names to rule out — and says why.
    assert named_remedies(failed.err) == ["kyo rebuild"], failed.err


def test_the_recovery_named_for_a_missing_kinds_yml_actually_restores_it(
    kinds_recovery_ground: Path,
) -> None:
    """KYO-82.c1: run the packaged-default recovery exactly as the message spells it.

    Parsed out of the stderr the run produced, not written here: a test that asserted
    the message says what it says today cannot fail when the message starts naming
    something that does not work, which is the whole discipline of this file.

    Run twice — once where every path is bare, once where the store's path and the
    packaged default's both contain a space, which is the case that ships quoted.
    """
    root, failed = _store_with_no_kinds_yml(kinds_recovery_ground)

    argv = shell_command_the_message_spells(failed.err, _PACKAGED_DEFAULT_RECOVERY)
    subprocess.run(argv, check=True, capture_output=True)

    cleared = invoke(["--root", str(root), "list"])
    assert cleared.code == 0, cleared


def test_the_version_control_recovery_named_for_a_missing_kinds_yml_works(
    kinds_recovery_ground: Path,
) -> None:
    """KYO-82.c1, the other recovery the message names, run as spelled.

    This is the one that restores the project's own registry rather than the shipped
    default, so it is the one a real store wants — and the reason the message puts it
    first. It is only available where the store is tracked, which the message says.
    """
    root, _ = _project_with_a_record(kinds_recovery_ground)
    git_init(root)  # one commit of everything, kinds.yml included
    assert kinds_yml(root).is_file()
    kinds_yml(root).unlink()

    failed = invoke(["--root", str(root), "list"])
    assert failed.code != 0, failed
    argv = shell_command_the_message_spells(failed.err, _VERSION_CONTROL_RECOVERY)
    subprocess.run(argv, cwd=root, check=True, capture_output=True)

    cleared = invoke(["--root", str(root), "list"])
    assert cleared.code == 0, cleared


def test_no_error_message_still_carries_the_sentence_kyo82_was_filed_against() -> None:
    """KYO-82 was KYO-73's message copied word for word into a second module, and it
    survived KYO-73 because that fix was made from the database symptom and nobody
    looked for the other copy. This is that look, kept: it reads the MESSAGES (through
    the same resolution the surface walk uses), not the file text, so a module that
    merely discusses the old sentence in prose — as ``kinds.py`` now does — is not
    mistaken for one that still prints it."""
    index = _SourceIndex(SRC_DIR)
    carriers = []
    for module, tree in sorted(index.trees.items()):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Raise)
                and node.exc is not None
                and "run `kyo init` or restore it" in index.message_text(node.exc, module)
            ):
                carriers.append(f"{module}.py:{node.lineno}")
    assert carriers == [], carriers


# ====================================================================== KYO-83
#
# The keying scheme itself, checked. The scheme is described where
# :class:`ErrorSite` is defined; what is below is the evidence for the three claims
# that description makes, each demonstrated against a real edit to a real copy of
# ``src/kyosei/`` rather than asserted about the idea of one:
#
#   * line drift above a site does not move its key, so an entry does not go stale
#     for a reason that has nothing to do with what it says (KYO-83.c1)
#   * a newly added site still lands as unclassified (KYO-83.c2)
#   * a message that changes is still caught, by running rather than by keying, and
#     the key deliberately does NOT move when it does (KYO-83.c2)


def _copy_of_the_source_tree(tmp_path: Path) -> Path:
    """``src/kyosei/`` on disk, editable, walkable by :func:`sites_in`."""
    copy = tmp_path / "src_kyosei"
    shutil.copytree(SRC_DIR, copy)
    return copy


def _insert_lines_above(path: Path, marker: str, lines: list[str]) -> int:
    """Put ``lines`` immediately above the one line starting with ``marker``.

    The marker must match exactly once. These tests turn on which site an edit lands
    above, so a marker that quietly took the first of several matches would make them
    assert something other than what they say.
    """
    text = path.read_text(encoding="utf-8").splitlines(keepends=True)
    matches = [i for i, line in enumerate(text) if line.startswith(marker)]
    assert len(matches) == 1, f"{marker!r} matches {len(matches)} lines of {path.name}"
    index = matches[0]
    path.write_text(
        "".join(text[:index] + [f"{line}\n" for line in lines] + text[index:]), encoding="utf-8"
    )
    return len(lines)


def _classified() -> set[str]:
    """Every site any table accounts for — the five of them, since KYO-84."""
    return set(PROVOKED) | set(UNREACHED) | set(FINDINGS) | set(RECORDED) | set(NOT_A_REMEDY)


def test_lines_added_above_a_site_do_not_invalidate_its_entry(tmp_path: Path) -> None:
    """KYO-83.c1, reconstructing the edit that broke this check the day it landed.

    KYO-80 added three lines of docstring to ``store.py``. Five error sites moved down
    by three, no message changed by one character, and all five entries in the tables
    above were stale at once — on a branch that had touched nothing this check is
    about. That is the defect: the repair is arithmetic, so it gets done by whoever is
    nearest the red suite rather than by whoever understands the site.

    Here the same edit is made to a copy of the tree and the keys are re-derived from
    what is on disk afterwards. Nothing moves, the tables still account for the whole
    surface, and — the control, without which this proves nothing — the line numbers
    demonstrably DID move, so the edit was real and KYO-77's key would have broken.
    """
    copy = _copy_of_the_source_tree(tmp_path)
    before = sites_in(copy)
    assert {s.key for s in before} == {s.key for s in deliberate_error_sites()}, (
        "the copy is not the tree this suite checks"
    )

    shifted = _insert_lines_above(
        copy / "store.py",
        "def open_store",
        [
            "# Three lines of comment above a function that holds five error sites —",
            "# the shape of the KYO-80 edit that broke this check's keys, and which",
            "# changed no error message anywhere.",
        ],
    )
    after = sites_in(copy)

    # 1. The edit was real: every site at or below it moved by exactly three lines.
    moved = {s.key: s.lineno for s in after}
    store_sites = [s for s in before if s.module == "store.py" and s.lineno > 0]
    drifted = [s for s in store_sites if moved[s.key] == s.lineno + shifted]
    assert len(drifted) >= 5, (
        f"the edit moved {len(drifted)} store.py sites; it was supposed to move all of them"
    )

    # 2. No key moved, so no entry in the tables went stale.
    assert {s.key for s in after} == {s.key for s in before}

    # 3. And no message changed, which is the whole reason nothing should have.
    assert {s.key: s.remedies for s in after} == {s.key: s.remedies for s in before}

    # 4. The gate this suite actually runs still passes against the edited tree.
    surface = {s.key for s in after if s.names_a_remedy}
    assert not surface - _classified(), sorted(surface - _classified())
    assert not _classified() - surface, sorted(_classified() - surface)

    # 5. The control: KYO-77's key was `module:lineno`, and under it five entries
    #    would now name sites that no longer exist.
    def line_keys(sites: tuple[ErrorSite, ...]) -> set[str]:
        return {f"{s.module}:{s.lineno}" for s in sites if s.names_a_remedy}

    assert len(line_keys(before) - line_keys(after)) >= 5, (
        "the old keying is being credited with a failure it would not have had"
    )


def test_a_newly_added_site_is_still_caught(tmp_path: Path) -> None:
    """KYO-83.c2, first half: the property that found KYO-81's ``cmd_store.py`` site.

    A key that is stable is worth nothing if it is stable by being blind. An error
    that starts naming a `kyo` command has to arrive in these tables as unclassified
    and fail ``test_every_remedy_naming_site_is_classified`` on the day it ships, and
    that is checked here by adding one to a copy of the tree and running the gate's
    own set difference against it.
    """
    copy = _copy_of_the_source_tree(tmp_path)
    before = {s.key for s in sites_in(copy) if s.names_a_remedy}

    _insert_lines_above(
        copy / "store.py",
        "    return Store(config, conn, read_only=read_only)",
        [
            "    if config.db_path.name == 'never':",
            '        raise KyoseiError("a new refusal; run `kyo rebuild --force` to clear it")',
        ],
    )
    after = {s.key for s in sites_in(copy) if s.names_a_remedy}

    assert after - before == {"store.py:open_store#6"}, sorted(after - before)
    assert not (after - before) <= _classified(), (
        "a brand-new remedy-naming site is already classified, which cannot be right"
    )


def test_a_site_inserted_between_two_others_still_fails_the_gate(tmp_path: Path) -> None:
    """The bounded edge of ordinals, stated as a test rather than as a footnote.

    Adding a raise in the MIDDLE of a function renumbers the ones after it, so the
    entries for those sites silently re-point at their neighbours — blind spot 2 in
    the scheme's notes. What this pins down is that the blindness is bounded: the
    count still changes, so an unclassified key still appears and the gate still
    fails. Someone is still made to look. For a PROVOKED or FINDINGS entry the
    provoker then catches the re-pointing outright ("the provoker reached X, not Y");
    for an UNREACHED entry, which has no provoker, nothing does, and that is the
    honest cost of this scheme.
    """
    copy = _copy_of_the_source_tree(tmp_path)
    before = {s.key: s.remedies for s in sites_in(copy) if s.names_a_remedy}

    _insert_lines_above(
        copy / "store.py",
        '    db_version = int(row["value"])',
        [
            "    if config.db_path.name == 'never':",
            '        raise KyoseiError("wedged between two sites; run `kyo rebuild --force`")',
        ],
    )
    after = {s.key: s.remedies for s in sites_in(copy) if s.names_a_remedy}

    # The gate still fires: one more site than there are entries for it.
    assert set(after) - set(before) == {"store.py:open_store#6"}, sorted(set(after) - set(before))
    assert not set(after) - set(before) <= _classified()

    # And the cost, made concrete rather than described: `open_store#3` is now a
    # different error from the one the tables have an entry for.
    assert after["store.py:open_store#3"] != before["store.py:open_store#3"]


def test_a_changed_message_is_caught_though_its_key_does_not_move(tmp_path, monkeypatch) -> None:
    """KYO-83.c2, second half — and the trade this scheme makes, made explicit.

    Under ``module:function#ordinal`` a message can be rewritten from end to end and
    the key does not move. That is deliberate: a key that moved on every reword would
    have to be re-derived on every reword, and the repair would be "paste the new
    hash", which is the same judgment-free bump KYO-83 was filed about, in a form no
    reader can check by eye.

    So the catching is done where it always actually was — by running. Both directions
    of it are asserted here at one site whose key stays fixed throughout:

      * the message changes what it OFFERS, and the offer is run from the state that
        produced the error and found not to clear it;
      * the message stops naming something an entry carries a judgment about, and that
        judgment fails as stale rather than sitting there describing nothing.
    """
    key = "store.py:open_store#0"
    assert key in {site.key for site in remedy_naming_error_sites()}

    # A message that changed to offer a real command that does not clear this state.
    _historic_remedy("run `kyo docs check` or restore it", monkeypatch)
    changed = check_remedies_apply(key, Expectation(_missing_database), tmp_path / "offered")
    assert changed.named == ("kyo docs check",), changed
    assert any("leaves the same error" in f for f in changed.failures), changed.failures
    assert changed.key == key, "the failure is reported against the same site it was raised at"
    assert key in {site.key for site in remedy_naming_error_sites()}, "and the key did not move"

    # A message that stopped naming a command the table classifies. Standing in for the
    # reword, because the classification is the judgment an entry exists to carry: when
    # the message no longer says the thing the reason is about, the reason has to fail.
    monkeypatch.undo()
    stale = check_remedies_apply(
        key,
        Expectation(_missing_database, descriptive={"kyo doctor": "not in this message at all"}),
        tmp_path / "classified",
    )
    assert any("no longer in the message" in f for f in stale.failures), stale.failures


# ====================================================================== KYO-84
#
# The surface itself, checked. Everything above KYO-84's line takes "the surface" as
# given and asks whether the messages on it are right; this record is the claim that the
# surface was the wrong set — sites whose message can carry a `kyo` command, which is
# the set the CHECK can act on rather than the set the READER is being told something
# by. The three properties that make the widening worth its machinery:
#
#   * a message naming a real remedy that is not a `kyo` command is on the surface and
#     classified, rather than invisible (c1)
#   * the denominator is sites that name a remedy of any kind, and the report separates
#     what the check can run from what it can only record (c2)
#   * ``kinds.py``'s message no longer needs to name a command to stay covered (c3)
#
# The last is the reason the record exists, so it is asserted as a counterfactual rather
# than described: strip the `kyo rebuild` sentence out of that message and the site is
# STILL on the surface. Before this change it would have dropped off entirely.


def _site(key: str) -> ErrorSite:
    sites = {site.key: site for site in deliberate_error_sites()}
    assert key in sites, f"{key} is not a site this walk finds; the code moved"
    return sites[key]


# --------------------------------------------------------------------- KYO-84.c1


def test_a_remedy_that_is_not_a_kyo_command_is_on_the_surface() -> None:
    """KYO-84.c1, at the two sites that make the point in different ways.

    ``config.py:load_config#6`` is the strong case: its whole remedy is "delete the
    line, or give it a value", a text editor and nothing else. It carries no backticked
    command of any kind, so under KYO-77's surface there was no way for it to be seen at
    all — not unverified, absent.

    ``cmd_dispatch.py:_cmd_dispatch_create#2`` is the other shape: a real command,
    `git worktree add`, which this check cannot run because the path is the caller's.

    Both are now on the surface, in the denominator, and classified with a reason. That
    is the whole of c1 — being checked is not what was asked for, being visible is.
    """
    surface = {site.key for site in sites_that_name_a_remedy()}
    for key in ("config.py:load_config#6", "cmd_dispatch.py:_cmd_dispatch_create#2"):
        site = _site(key)
        assert not site.remedies, f"{key} names a `kyo` command now; pick another witness"
        assert site.recorded, f"{key} names no remedy this walk can see: {_message_of(site)}"
        assert key in surface, f"{key} names a remedy and is not in the denominator"
        assert key in _classified(), f"{key} is on the surface and in no table"
    # And the first of them names no command at all, which is what makes it the case
    # KYO-77's surface could not have carried under any reading.
    assert not shell_remedies(_message_of(_site("config.py:load_config#6")))


def test_the_widened_surface_strictly_contains_the_kyo_only_one() -> None:
    """The widening adds and does not move: every site KYO-77 checked is still checked.

    Worth asserting rather than assuming, because the cheap way to make a coverage
    percentage survive a widened denominator is to quietly let something out of the
    numerator. Nothing here may leave.
    """
    kyo_only = {site.key for site in deliberate_error_sites() if site.remedies}
    widened = {site.key for site in sites_that_name_a_remedy()}
    assert kyo_only < widened, "the widened surface does not strictly contain KYO-77's"
    assert kyo_only == {site.key for site in runnable_remedy_sites()}
    assert kyo_only <= set(PROVOKED) | set(UNREACHED) | set(FINDINGS)


def test_the_imperative_recogniser_reads_instructions_not_prose() -> None:
    """:data:`REMEDY_VERBS` is a closed list read at clause starts, and both halves of
    that matter. The list is what stops the predicate being a heuristic; the position is
    what stops "cannot move to done" being read as an instruction to move something.

    Held down in both directions, because a recogniser that says yes to everything makes
    the denominator meaningless and one that says yes to nothing puts the record's own
    defect back where it was.
    """
    # Instructions, in the shapes this package's messages actually use.
    assert imperative_remedies("kind has policy.requires_criteria: true; pass --criteria-file")
    assert imperative_remedies("found None — delete the line, or give it a value")
    assert imperative_remedies("Pass --store DIR pointing outside the repository")
    assert imperative_remedies("create the worktree first (e.g. `git worktree add`), then dispatch")

    # Not instructions: the verb is mid-clause, describing rather than telling.
    assert not imperative_remedies("its id is retired and cannot move to done")
    assert not imperative_remedies("the record does not name a plan")
    assert not imperative_remedies("`git rev-parse HEAD` in /x produced no output")


def test_the_shell_recogniser_reads_command_lines_not_backticked_words() -> None:
    """The other recogniser, held down the same way — and against the exact strings this
    package puts in backticks for reasons that have nothing to do with remedies.

    A permissive version of this is the failure the record warns about: every backticked
    identifier in every validation message would join the denominator, the percentage
    would collapse, and the figure would stop being about anything.
    """
    assert shell_remedies("Restore it from version control (git checkout -- /x/kinds.yml)") == ()
    assert shell_remedies("(`git checkout -- /x/kinds.yml`), where the store is tracked") == (
        "git checkout -- /x/kinds.yml",
    )
    assert shell_remedies("copy the packaged default: `cp /a/kinds.yml /b/kinds.yml`") == (
        "cp /a/kinds.yml /b/kinds.yml",
    )
    # `kyo` lines belong to the runnable half and are not double-counted here.
    assert shell_remedies("run `kyo rebuild --force` to clear it") == ()
    # And the things this package backticks that are not commands.
    for not_a_command in ("`kyosei.db`", "`--why-file`", "`wont-do`", "`policy.write_scope`"):
        assert shell_remedies(f"a message carrying {not_a_command} in it") == (), not_a_command


def test_a_newly_added_site_whose_remedy_is_not_a_kyo_command_is_caught(tmp_path: Path) -> None:
    """KYO-84.c1 as the property, not as two witnesses: the gate catches the NEXT one.

    ``test_a_newly_added_site_is_still_caught`` proves this for a site naming a `kyo`
    command, which is what KYO-77's surface could see. This is the same edit to the same
    copied tree with a message of the shape KYO-84 was filed about — a hand edit, no
    command anywhere in it — and it has to arrive unclassified and fail the gate. Under
    the old surface it would have arrived nowhere.
    """
    copy = _copy_of_the_source_tree(tmp_path)
    before = {s.key for s in sites_in(copy) if s.names_a_remedy}

    _insert_lines_above(
        copy / "store.py",
        "    return Store(config, conn, read_only=read_only)",
        [
            "    if config.db_path.name == 'never':",
            '        raise KyoseiError("a new refusal; delete the stray line from config.yml")',
        ],
    )
    added = {s.key for s in sites_in(copy) if s.names_a_remedy} - before

    assert added == {"store.py:open_store#6"}, sorted(added)
    new_site = next(s for s in sites_in(copy) if s.key == "store.py:open_store#6")
    assert new_site.remedies == (), "the witness names a `kyo` command; it proves the old surface"
    assert new_site.recorded, new_site
    assert not added <= _classified(), (
        "a brand-new site naming a hand edit is already classified, which cannot be right"
    )


# --------------------------------------------------------------------- KYO-84.c2


def test_the_denominator_is_every_site_that_names_a_remedy() -> None:
    """KYO-84.c2, as the arithmetic the coverage report prints.

    The denominator is the surface minus the sites that are not remedies, and every one
    of those subtractions is an entry someone wrote a reason for. What this pins is that
    there is no third source of shrinkage: the candidate set, the two classes, and the
    five tables all reconcile, so a number in that report cannot be arrived at by any
    route but the tables above it.
    """
    candidates = {site.key for site in remedy_naming_error_sites()}
    denominator = {site.key for site in sites_that_name_a_remedy()}
    runnable = {site.key for site in runnable_remedy_sites()}

    assert candidates - denominator == set(NOT_A_REMEDY)
    assert denominator == runnable | set(RECORDED)
    assert not runnable & set(RECORDED)
    assert candidates == _classified()
    # The two classes c2 asks the report to distinguish, distinguished by the message
    # rather than by which table the site happens to sit in.
    for site in sites_that_name_a_remedy():
        assert (site.key in runnable) == bool(site.remedies), site


# --------------------------------------------------------------------- KYO-84.c3


def test_the_kinds_yml_message_stays_on_the_surface_without_naming_kyo_rebuild(
    tmp_path: Path,
) -> None:
    """KYO-84.c3: the incentive the record was filed about, removed and shown removed.

    KYO-82's correct fix was to name no `kyo` remedy — `kinds.yml` is committed
    configuration and the recovery is version control or the packaged default. But
    KYO-82.c3 required the site to stay covered, and under KYO-77's surface a message
    with no `kyo` command in it was not on the surface at all, so `kyo rebuild` had to
    appear in order to be ruled out. Good prose with a reason for existing that was
    partly the check's.

    That reason is gone, and here is the evidence rather than the assertion: take the
    real message, delete every `kyo` command from it, and the site is still recognised
    as naming a remedy — by the two recoveries that actually restore the file, which it
    introduces as instructions ("Restore it from version control (git checkout -- x)",
    "otherwise copy the packaged default: cp a b"). The mention is now kept on its own
    merits (it is the reader's next guess and it is wrong), which
    ``missing_registry_remedy``'s docstring records and
    ``test_the_missing_kinds_yml_message_does_not_name_kyo_init`` still holds in place.
    """
    root, failed = _store_with_no_kinds_yml(tmp_path)
    assert named_remedies(failed.err) == ["kyo rebuild"], failed.err

    without_the_mention = re.sub(r"`kyo[^`]*`", "that command", failed.err)
    assert named_remedies(without_the_mention) == [], without_the_mention

    # Still a remedy-naming message, by the two recoveries that work. Note WHICH
    # recogniser sees them: these commands are spelled without backticks, so it is the
    # imperative introducing each one — not the command's own shape — that puts this
    # site on the surface. That limit is written down above :data:`REMEDY_VERBS`.
    still_named = shell_remedies(without_the_mention) + imperative_remedies(without_the_mention)
    assert still_named, without_the_mention
    assert any("git checkout" in r for r in still_named), still_named
    assert any("cp " in r for r in still_named), still_named

    # And the site as it actually ships is on the surface twice over: in the runnable
    # half for the mention, and carrying a recorded remedy that is the real recovery.
    site = _site("registry.py:load#0")
    assert site.remedies == ("kyo rebuild",), site
    assert any("git checkout" in r for r in site.recorded), site
    assert root.is_dir()


def test_the_kept_mention_is_still_ruled_out_rather_than_offered() -> None:
    """c3's other half: kept does not mean unexamined.

    The mention survives KYO-84 because it helps a reader, not because a table needed
    it, and the thing that makes it help is that it is ruled out WITH A REASON. So the
    reason has to be in the message, not only in the classification — a bare
    "`kyo rebuild` does not do this" would be the mention without the thing it was kept
    for. The classification itself is checked in both directions by
    ``check_remedies_apply`` at every run, through ``PROVOKED``.
    """
    message = _message_of(_site("registry.py:load#0"))
    assert "no `kyo` command restores it" in message
    assert "reconstructs the database" in message, (
        "`kyo rebuild` is named without saying why it does not apply, which is the only "
        "thing that made keeping it worth a sentence"
    )
    assert PROVOKED["registry.py:load#0"].descriptive.get("kyo rebuild"), (
        "the mention is no longer classified as a mention"
    )


# ------------------------------------------------------------------------- KYO-182
#
# The two stale-build refusals -- `store.open_store`'s ahead direction (KYO-168) and
# `rebuild`'s later-export one (KYO-179/KYO-181) -- stated three clauses verbatim in two
# files with no shared source. They stay two messages, for the reason KYO-179's worker
# argued and this file's KYO-168 test already pins: one names a DATABASE and is raised at
# open, the other names an EXPORT DIRECTORY and specific files and says nothing was
# written, and which artifact is meant is what a reader in the desktop app most needs.
# Only the shared clauses moved, to `contracts` (KYO-182.c1).
#
# Two assertions are needed and neither alone is enough. That both messages CONTAIN the
# clauses would stay green if one site quietly went back to stating them literally --
# which is the exact failure mode this record exists to end, because a literal copy reads
# perfectly right up until somebody improves the other one. So the second test asserts the
# absence of the literals from the two source files. Together they say: the text is in both
# messages, and it is in neither file.

_SHARED_CLAUSE_FRAGMENTS = (
    "this build is the stale side of the pair",
    "a build has no way to know what a later schema added",
    "the desktop app and every project's hooks run",
)


def _both_stale_build_refusals(kyo, project: Path) -> tuple[str, str, str]:
    """The two messages, provoked rather than constructed: a db from a later schema, and
    an export document from one. Returns them with the record the export names."""
    record = new_record(kyo, criteria=["a"])

    _stamp_db_schema_version(project, SCHEMA_VERSION + 1)
    db_refusal = kyo("show", record)
    assert db_refusal.code == EXIT_ERROR, db_refusal

    _stamp_db_schema_version(project, SCHEMA_VERSION)
    export_path = project / "kyosei" / "export" / f"{record}.json"
    document = json.loads(export_path.read_text(encoding="utf-8"))
    document["schema_version"] = SCHEMA_VERSION + 1
    export_path.write_text(json.dumps(document), encoding="utf-8")
    (project / "kyosei" / "kyosei.db").unlink()
    export_refusal = kyo("rebuild")
    assert export_refusal.code == EXIT_ERROR, export_refusal

    return db_refusal.err, export_refusal.err, record


def test_both_stale_build_refusals_say_the_shared_clauses_from_the_one_shared_source(
    kyo, project: Path
) -> None:
    """KYO-182.c1 and .c3: both messages carry all three clauses, AS ``contracts`` renders
    them.

    Rendered rather than quoted: a literal expectation here would be a fourth copy of the
    text, and would pass the day a site stopped using the shared source and happened to
    still say the same words. Asking ``contracts`` for the string is what makes this test
    fail when a site is bypassed and pass when the shared wording is improved.
    """
    from kyosei.contracts import (
        installed_build_clause,
        stale_build_clause,
        upgrade_the_build_clause,
    )

    db_err, export_err, _ = _both_stale_build_refusals(kyo, project)

    assert stale_build_clause(artifact="store", cannot="open a store") in db_err, db_err
    assert stale_build_clause(artifact="export", cannot="read an export") in export_err, export_err
    for message in (db_err, export_err):
        assert upgrade_the_build_clause(SCHEMA_VERSION + 1) in message, message
        assert installed_build_clause() in message, message


def test_the_installed_build_clause_asks_upgrade_route_rather_than_restating_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KYO-466.c2: the reader-who-did-not-install-this sentence names the route that moves
    THIS copy, by asking :func:`kyosei.buildinfo.upgrade_route` at the moment it is
    rendered. A sentence that restated one route would be the KYO-317 failure again -- a
    line printed by one install kind naming another kind's remedy -- and the one it used
    to state, "updating that application", names a route the app no longer has."""
    import kyosei.buildinfo as buildinfo_module
    from kyosei.contracts import installed_build_clause

    monkeypatch.setattr(buildinfo_module, "upgrade_route", lambda: "ROUTE-SENTINEL")
    clause = installed_build_clause()
    assert clause.endswith("ROUTE-SENTINEL"), clause
    assert "bundled" not in clause, clause
    assert "application" not in clause, clause
    monkeypatch.undo()
    assert installed_build_clause().endswith(buildinfo_module.upgrade_route())


def test_neither_refusal_site_states_the_shared_clauses_literally(
    kyo, project: Path
) -> None:
    """KYO-182.c1's other half. The clauses are in both messages and in neither file, so
    "improved in one place and left stale in the other" has nowhere to happen.

    The messages are provoked first, and the fragments checked against them, so this
    cannot pass by the clauses having quietly disappeared from the messages altogether —
    which is the one way "absent from both files" could be satisfied for the wrong reason.
    """
    import kyosei.rebuild as rebuild_module
    import kyosei.store as store_module

    db_err, export_err, _ = _both_stale_build_refusals(kyo, project)
    for fragment in _SHARED_CLAUSE_FRAGMENTS:
        assert fragment in db_err, fragment
        assert fragment in export_err, fragment

    for module in (store_module, rebuild_module):
        source = Path(module.__file__).resolve().read_text(encoding="utf-8")
        # Comments in these files legitimately discuss the situation; what must not
        # appear is the clause itself. Strip the prose that is not code first.
        code_only = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        for fragment in _SHARED_CLAUSE_FRAGMENTS:
            assert fragment not in code_only, (
                f"{module.__name__} states {fragment!r} itself; KYO-182 moved it to "
                "contracts so the two refusals cannot drift apart"
            )


def test_the_two_refusals_are_still_told_apart_by_what_is_their_own(
    kyo, project: Path
) -> None:
    """KYO-182.c2: sharing the clauses did not make one message out of two.

    KYO-179's worker argued that a single parameterised message would be vague about
    which artifact it means, and that this is the thing a desktop reader most needs. So:
    the db one names the database file and says it cannot OPEN it; the export one names
    the export directory and the offending file and says nothing was written. Neither
    sentence appears in the other.
    """
    db_err, export_err, record_id = _both_stale_build_refusals(kyo, project)

    assert "kyosei.db" in db_err, db_err
    assert "open a store" in db_err, db_err
    assert "kyosei.db" not in export_err, export_err

    assert str(project / "kyosei" / "export") in export_err, export_err
    assert f"{record_id}.json" in export_err, export_err
    assert "Nothing has been written" in export_err, export_err
    assert "Nothing has been written" not in db_err, db_err

    assert db_err != export_err


# ------------------------------------------------------- KYO-478: a second state for #4
#
# PROVOKED holds one state per site, and `check_transition#4` already has one: a
# record with no report, whose refusal names `kyo split`. KYO-478 gave the same raise a
# second remedy for a different state — a criterion an ingested, unapplied report
# already met — so that state is provoked here and run through the same check.


def _done_over_a_criterion_an_unapplied_report_met(at: Path) -> Provocation:
    """KYO-478: two dispatches on one record; the first one's report carries `met`
    for c1 and was ingested but never applied."""
    root, record = _project_with_a_record(at)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-q", "-m", "work")
    kyo = _runner(root)
    for index, verdicts in enumerate(
        ([{"criterion_id": f"{record}.c1", "verdict": "met", "method": "reasoned-about"}], [])
    ):
        dispatched = kyo("dispatch", record, "--json")
        assert dispatched.code == 0, dispatched
        dispatch_id = dispatched.json["dispatch_id"]
        report = at / f"worker-report-{index}.json"
        report.write_text(
            json.dumps(
                {
                    "dispatch_id": dispatch_id,
                    "record_id": record,
                    "worked_from_sha": dispatched.json["worked_from_sha"],
                    "criteria_verdicts": verdicts,
                    "premise_false": [],
                    "disagreements": [],
                    "corrected_numbers": [],
                    "commits": [],
                }
            ),
            encoding="utf-8",
        )
        ingested = kyo("report", dispatch_id, "--file", str(report))
        assert ingested.code == 0, ingested
    return Provocation(root, ("move", record, "done"))


def test_kyo478_the_unapplied_report_remedy_clears_the_settled_criteria_refusal(
    tmp_path: Path,
) -> None:
    """KYO-478.c2: the `kyo verify --from-report <d> --only <c>` the refusal names,
    run from the state that produced it, lets the same move through — and `kyo split`
    is not among what it names, because every unsettled criterion is covered."""
    key = "contracts.py:check_transition#4"
    result = check_remedies_apply(
        key, Expectation(_done_over_a_criterion_an_unapplied_report_met), tmp_path
    )
    assert not result.failures, "\n".join(result.failures)
    assert len(result.verified) == 1, result
    assert result.verified[0].startswith("kyo verify --from-report "), result
    assert not any("kyo split" in remedy for remedy in result.named), result


# ====================================================================== KYO-504
#
# The walk's fixture project, ``tmpXXXXXXXX/provoked/proj``, turned up in the real
# ~/kyosei/projects.json past KYO-464's session guard. ``tmpXXXXXXXX`` is what
# ``tempfile.mkdtemp()`` names a directory and never what pytest's ``tmp_path`` is
# (``pytest-of-<user>/pytest-N/[popen-gwK/]<test>0``), so the run that wrote it was not a
# test at all: it was this module imported and a provoker run by hand, where no
# ``store_home`` fixture redirects ``$KYOSEI_HOME`` and no session exists to guard.
# :func:`conftest.refuse_the_real_store_home` closes it where every in-process `kyo` call
# passes, and these prove it does -- each against a stand-in for the account's home, so
# a broken guard writes into ``tmp_path`` rather than the developer's registry.


def _stand_in_for_the_account_home(tmp_path: Path, monkeypatch) -> Path:
    """Make ``tmp_path/account`` the home both the guard and `kyo` take to be real, with
    ``$KYOSEI_HOME`` unset -- the environment a hand-run probe has. Returns its registry."""
    from . import conftest as conftest_module

    account = tmp_path / "account"
    account.mkdir()
    registry = account / "kyosei" / "projects.json"
    monkeypatch.setattr(conftest_module, "_real_registry", lambda: registry)
    monkeypatch.setenv("HOME", str(account))
    monkeypatch.delenv("KYOSEI_HOME", raising=False)
    return registry


def test_kyo504_the_walk_refuses_an_unredirected_store_home_before_writing(
    tmp_path: Path, monkeypatch
) -> None:
    """KYO-504.c2: with ``$KYOSEI_HOME`` not redirected, the walk's own entry point
    refuses before `kyo init` runs -- no project directory, no registry -- and the remedy
    its refusal names, a temporary ``$KYOSEI_HOME``, lets the same walk through with the
    project registered there and nowhere else. Remove the call in ``invoke`` and the
    first half fails: the walk registers ``provoked/proj`` in the stand-in registry."""
    from .conftest import RealStoreHomeRefused

    registry = _stand_in_for_the_account_home(tmp_path, monkeypatch)
    key = "store.py:open_store#0"

    with pytest.raises(RealStoreHomeRefused) as refused:
        check_remedies_apply(key, Expectation(_missing_database), tmp_path / "walk")
    assert not registry.exists(), registry.read_text(encoding="utf-8")
    assert not (tmp_path / "walk" / "provoked" / "proj").exists()
    assert str(registry) in str(refused.value), refused.value
    assert 'KYOSEI_HOME="$(mktemp -d)"' in str(refused.value), refused.value

    redirected = tmp_path / "redirected"
    monkeypatch.setenv("KYOSEI_HOME", str(redirected))
    result = check_remedies_apply(key, Expectation(_missing_database), tmp_path / "walk")
    assert not result.failures, result.failures
    assert not registry.exists(), registry.read_text(encoding="utf-8")
    roots = [p["root"] for p in json.loads((redirected / "projects.json").read_text())["projects"]]
    assert str(tmp_path / "walk" / "provoked" / "proj") in roots, roots


def test_kyo504_a_home_that_only_looks_like_the_default_is_not_refused(
    tmp_path: Path, monkeypatch
) -> None:
    """The guard compares against the ACCOUNT's home, not ``$HOME``: a test that unsets
    ``$KYOSEI_HOME`` and points ``$HOME`` somewhere temporary to exercise the default
    (``tests/test_kyo379_where_store_home.py``) is not what KYO-504 is about, and stays
    runnable."""
    from . import conftest as conftest_module

    monkeypatch.setattr(
        conftest_module, "_real_registry", lambda: tmp_path / "account" / "kyosei" / "projects.json"
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("HOME", str(elsewhere))
    monkeypatch.delenv("KYOSEI_HOME", raising=False)
    root = _new_project(tmp_path / "provoked")
    registry = json.loads((elsewhere / "kyosei" / "projects.json").read_text())
    roots = [p["root"] for p in registry["projects"]]
    assert roots == [str(root)], roots


#: What a hand-run probe of the walk does, which is KYO-504's leak: import this module,
#: run a provoker under ``tempfile.mkdtemp()``. Outside pytest, so nothing but the guard
#: in ``invoke`` stands between it and the registry -- here a stand-in, by argv.
_PROBE_OUTSIDE_PYTEST = """
import sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tests import conftest
registry = Path(sys.argv[2])
conftest._real_registry = lambda: registry
from tests import test_kyo5_error_remedies as walk
try:
    walk.check_remedies_apply(
        "store.py:open_store#0", walk.Expectation(walk._missing_database),
        Path(tempfile.mkdtemp()),
    )
except conftest.RealStoreHomeRefused:
    print("refused")
else:
    print("ran")
"""


def test_kyo504_a_probe_outside_pytest_is_refused_too(tmp_path: Path) -> None:
    """KYO-504.c1/c2: the leak's own path, reproduced as it happened -- no pytest
    session, no ``store_home`` fixture, ``$KYOSEI_HOME`` unset, a ``tempfile`` directory --
    against a stand-in account home. Without the guard this prints ``ran`` and the
    stand-in registry holds ``.../tmpXXXXXXXX/provoked/proj``, which is the entry the
    controller found in the real one."""
    account = tmp_path / "account"
    account.mkdir()
    registry = account / "kyosei" / "projects.json"
    env = {k: v for k, v in os.environ.items() if k != "KYOSEI_HOME"}
    env["HOME"] = str(account)
    repo = Path(__file__).resolve().parent.parent
    probe = subprocess.run(
        [sys.executable, "-I", "-c", _PROBE_OUTSIDE_PYTEST, str(repo), str(registry)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip().splitlines()[-1] == "refused", probe.stdout
    assert not registry.exists(), registry.read_text(encoding="utf-8")
