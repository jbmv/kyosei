"""KYO-226: the changelog, derived from the store rather than written beside it.

A user of an installed build needs to know which build it is and what changed since the
last, and the answer this repository's own rules force is that the record of it may not be
a hand-written ``CHANGELOG.md``. Every consumer-facing document here is generated from its
source of truth with a test that fails when the committed copy falls behind (KYO-138,
``docs/reference/cli.md``). The store already **is** the changelog: every completed
record carries a completion date, a scope, an id and a title, and writing those a second
time by hand is the restatement the charter forbids.

**Where a consumer reads it from.** Not from this repository — they do not have it. The
entries are computed at wheel build time and written into the package as
:data:`CHANGELOG_FILENAME`, by the same hook that writes the build stamp
(:func:`kyosei.buildinfo.regenerate`). So ``kyo changelog`` on an installed copy reads a
file that was current at the moment that copy was built, and *cannot* be older than the
build carrying it — which is the property that makes a bare ``kyo changelog`` after a
reinstall a truthful answer to "what changed since I last installed".

**Where this repository reads it from.** The live store, always. A run out of a source
tree regenerates from ``kyosei/export/`` rather than trusting a packaged file an earlier
build left behind, so ``docs/reference/changelog.md`` and its freshness test
(``tests/test_kyo226_version_and_changelog.py``) are comparing the committed document
against the store as it is now. The two paths are distinguished in ``--json``'s
``source``, never conflated.

**Read through the existing readers.** :func:`kyosei.rebuild.read_record_documents` is
the one door a record document enters through (KYO-179, and an AST test keeps it that
way), and :func:`kyosei.events_ledger.read_events_in` is the events ledger's reader. A second
parser over ``kyosei/export/*.json`` would be a second opinion about what a record *is*,
which is the class of defect this store spends most of its machinery refusing.

**Why the events ledger and not ``updated_at``.** A record document's ``updated_at``
moves whenever anything is attached to it — a note, an evidence artifact, a redaction —
so it answers "when was this last touched", not "when was this finished". The completion
date is the ``at`` of the last event that moved the record into the ladder's
``complete`` status, which is a fact the ledger holds exactly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config as config_module
from . import contracts, events_ledger, rebuild
from .errors import KyoseiError, UsageError

#: The packaged data file, written into the package directory at build time beside the
#: build stamp. Deliberately NOT under ``defaults/``: everything in that directory is an
#: asset something *copies* into a consumer's own tree (``kyo init``,
#: ``kyo install-hooks``), and a copied changelog is a stale changelog. This is served in
#: place from the package, exactly as ``report.schema.json`` is (see the note in
#: ``pyproject.toml``).
CHANGELOG_FILENAME = "_changelog.json"

#: Where this repository keeps its generated markdown twin, root-relative and
#: posix-style — inside ``docs/`` so ``docs_check``'s default globs already reach it,
#: on ``REFERENCE_PATH``'s reasoning exactly.
CHANGELOG_PATH = "docs/reference/changelog.md"

#: The exact command that regenerates the committed copy, named once so the document's
#: own header and the test that fails on a stale copy quote the same string (law 1: an
#: error naming a remedy names one that works).
#:
#: **This checkout's own generator, not `kyo` on PATH** (KYO-372). Every project's `kyo` is an
#: installed build -- the uv tool (KYO-446/KYO-450) -- and that build's `changelog` command
#: reads *its own* snapshot, baked in at
#: build time, not this checkout's live store. Naming `kyo changelog --all --output ...` here used
#: to let the test that names this remedy print "wrote N line(s)" and exit 0 while writing the wrong
#: document -- the packaged build's `changelog` command exists and succeeds, it just answers for a
#: different tree. `.venv/bin/python -m kyosei` bypasses PATH entirely and always runs the code
#: sitting beside this file, so it is the one invocation that cannot be quietly answered by somebody
#: else's build.
CHANGELOG_REGENERATE_COMMAND = (
    f".venv/bin/python -m kyosei changelog --all --output {CHANGELOG_PATH}"
)

#: Where a rendering's entries came from. ``--json`` carries it.
SOURCE_STORE = "store"
SOURCE_PACKAGED = "packaged"

#: What a record with no scope is grouped under. Sorted last by :func:`grouped`, and
#: spelled once here so the text and the markdown cannot disagree about the label.
NO_SCOPE = "(no scope declared)"

#: Which field answered a record's completion date, carried per entry so a reader can
#: tell an exact answer from a fallback.
DATED_BY_EVENT = "event"
DATED_BY_RECORD = "record"


@dataclass(frozen=True)
class Entry:
    """One completed record, as the changelog states it."""

    id: str
    kind: str
    scope: str | None
    title: str
    completed_on: str
    dated_by: str
    #: KYO-398.c2: the full UTC instant ``completed_on`` is the date of. It is what
    #: lets a session be told which records arrived *between two builds*, whose commit
    #: times fall inside a day as often as not — a date alone cannot place a record
    #: completed at 01:00Z against a build committed at 02:34Z the same day. ``None``
    #: on an entry read from packaged data written before this field existed, which
    #: carries the date only; :func:`between` says when it had to fall back to it.
    completed_at: str | None = None

    def as_data(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "scope": self.scope,
            "title": self.title,
            "completed_on": self.completed_on,
            "completed_at": self.completed_at,
            "dated_by": self.dated_by,
        }

    @classmethod
    def from_data(cls, raw: dict) -> Entry:
        return cls(
            id=str(raw.get("id") or ""),
            kind=str(raw.get("kind") or ""),
            scope=raw.get("scope") or None,
            title=str(raw.get("title") or ""),
            completed_on=str(raw.get("completed_on") or ""),
            dated_by=str(raw.get("dated_by") or DATED_BY_RECORD),
            completed_at=raw.get("completed_at") or None,
        )

    @property
    def group(self) -> str:
        return self.scope or NO_SCOPE


# ----------------------------------------------------------------- from the store


def _this_checkouts_config(code_root: Path) -> config_module.Config | None:
    """``code_root``'s own store config, or ``None`` when no project is reachable.

    **This checkout's copy, never the one a redirect resolved through** (KYO-355).
    Since KYO-342 a read run from a linked git worktree whose own ``kyosei/`` carries
    no database resolves :attr:`~kyosei.config.Layout.store_root` to the *main
    checkout's* store — right for ``kyosei.db``, which is gitignored and genuinely
    absent here, and wrong for every file under ``kyosei/`` that git tracks.
    ``kyosei/export/`` is tracked: ``git worktree add`` checks it out, so this branch
    has its own at whatever it holds, and it is the copy this branch would be diffed
    on. Everything in this module renders from that export and is compared against
    this branch's committed ``docs/reference/changelog.md`` — one comparison, both
    sides out of one working tree, or the check is asking two trees one question.

    Measured: in a dispatch worktree cut before the controller filed KYO-354, the
    rendering reached for the main checkout's ``kyosei/export/KYO-354.json``,
    ``tests/test_kyo226_version_and_changelog.py`` failed on records the worker had
    never seen, and a wheel built there would have carried another working tree's
    changelog — the build hook writes :data:`CHANGELOG_FILENAME` through these same
    functions.

    :attr:`~kyosei.config.Layout.store_root_in_this_checkout` is the property KYO-342
    added for exactly this (`kyo install-procedures`, over another tracked file), and
    it *is* ``store_root`` under every layout that was not redirected — so an ordinary
    checkout, and the split layout where the store is its own repository elsewhere,
    are both untouched.
    """
    try:
        layout = config_module.resolve_layout(code_root, required=False)
        if not layout.configured:
            return None
        return config_module.load_config(layout.store_root_in_this_checkout)
    except KyoseiError:
        return None


def store_export_dir(code_root: Path) -> Path | None:
    """The export directory of the store ``code_root`` serves, or ``None``.

    Resolved through :mod:`kyosei.config` rather than assumed to be ``kyosei/export``,
    so this keeps working under the split layout (KYO-56) where the store is a
    different repository entirely.
    """
    config = _this_checkouts_config(code_root)
    return None if config is None else config.export_path


def _kyosei_dir(code_root: Path) -> Path | None:
    """This checkout's registry directory — where the status ladder is read from.

    The same copy :func:`store_export_dir` returns the export of, and necessarily so:
    the ladder answers which status string means *complete*, and the documents that
    answer is applied to are that export's. Reading the ladder out of one working tree
    and the documents it interprets out of another is the pair law 3 forbids.
    """
    config = _this_checkouts_config(code_root)
    return None if config is None else config.kyosei_dir


def _snapshot(value: object) -> dict | None:
    """An event's ``before``/``after`` snapshot as a mapping, however it was stored."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:  # pragma: no cover - a damaged ledger line
            return None
    return value if isinstance(value, dict) else None


def _completion_dates(export_dir: Path, complete_status: str) -> dict[str, str]:
    """``record id -> the ``at`` of the last event that *moved* it into ``complete``.

    **The transition, not the state.** An event's ``after`` is a full snapshot of the
    record, so every note, evidence artifact and edit landing on a finished record
    carries ``status: done`` in it. Matching on the snapshot alone therefore dates the
    record by its last write of any kind — which is ``updated_at`` by a longer route,
    and is exactly the reading this function exists to avoid. What is matched is a row
    whose ``before`` says something else and whose ``after`` says ``complete``.

    Measured, not reasoned: written the other way first, this dated all 199 of this
    store's completed records identically to their ``updated_at``, and the test that
    asserts the two sources differ is what said so.

    The ledger is in sequence order, so the last such transition wins — a record moved
    out of ``done`` and back in is dated by the move that stuck.
    """
    dates: dict[str, str] = {}
    for event in events_ledger.read_events_in(export_dir):
        record_id = event.get("record_id")
        if not record_id:
            continue
        after = _snapshot(event.get("after"))
        if after is None or after.get("status") != complete_status:
            continue
        # KYO-418.c3: "whose before says something else" asked of either line format.
        # An old line's whole `before` and a new line's `changed` map both reduce to
        # the prior value of each key that moved; `status` among them is a move INTO
        # complete, and no prior document at all is a record created there.
        prior = events_ledger.event_prior(
            {
                **event,
                "before": _snapshot(event.get("before")),
                "after": after,
                events_ledger.EVENT_CHANGED_KEY: _snapshot(
                    event.get(events_ledger.EVENT_CHANGED_KEY)
                ),
            }
        )
        if prior is not None and "status" not in prior:
            continue
        dates[str(record_id)] = str(event.get("at") or "")
    return dates


def entries_from_store(code_root: Path) -> list[Entry]:
    """Every completed record in the store ``code_root`` serves, newest first."""
    export_dir = store_export_dir(code_root)
    if export_dir is None or not export_dir.is_dir():
        raise KyoseiError(
            f"no Kyosei store is reachable from {code_root}, so there is no changelog to "
            "generate from it"
        )
    kyosei_dir = _kyosei_dir(code_root)
    ladder = (
        contracts.load_status_ladder(kyosei_dir)
        if kyosei_dir is not None
        else contracts.default_ladder()
    )
    completed = _completion_dates(export_dir, ladder.complete)

    entries: list[Entry] = []
    for document in rebuild.read_record_documents(export_dir):
        if document.get("status") != ladder.complete:
            continue
        record_id = str(document.get("id") or "")
        at = completed.get(record_id)
        dated_by = DATED_BY_EVENT
        if not at:
            # A record completed before the events ledger carried the move — or one
            # whose move event was redacted. `updated_at` is the honest remaining
            # answer, and the entry says which field answered rather than presenting
            # the two as the same fact.
            at = str(document.get("updated_at") or "")
            dated_by = DATED_BY_RECORD
        entries.append(
            Entry(
                id=record_id,
                kind=str(document.get("kind") or ""),
                scope=document.get("scope") or None,
                title=str(document.get("title") or ""),
                completed_on=at[:10],
                dated_by=dated_by,
                # KYO-398.c2: both sources are tool-stamped UTC instants (law 16), so
                # the date above is this value's date and the two cannot disagree.
                completed_at=at or None,
            )
        )
    return sort_entries(entries)


def sort_entries(entries: list[Entry]) -> list[Entry]:
    """Newest first, and deterministic — the committed markdown is compared by bytes.

    The secondary key is the numeric part of the id, descending, so several records
    completed on the same day read in the order they were filed rather than in whatever
    order the filesystem listed them.
    """

    def key(entry: Entry) -> tuple:
        return (entry.completed_on, _id_sort_key(entry.id))

    return sorted(entries, key=key, reverse=True)


def _id_sort_key(record_id: str) -> tuple:
    prefix, _, number = record_id.rpartition("-")
    return (prefix, int(number)) if number.isdigit() else (record_id, 0)


# ------------------------------------------------------------ the packaged data file


def data_document(code_root: Path) -> dict:
    """The packaged data file's content, generated from the store."""
    from .buildinfo import build_identity

    identity = build_identity()
    return {
        "generated_from_commit": identity.commit,
        "entries": [entry.as_data() for entry in entries_from_store(code_root)],
    }


def write_packaged_data(directory: Path, code_root: Path) -> dict:
    """Write :data:`CHANGELOG_FILENAME` into ``directory``. **Called by the build hook.**

    A store that cannot be reached is written down as an *absence with a reason* rather
    than skipped. A build from a checkout with no store (an sdist, say) would otherwise
    produce a package whose ``kyo changelog`` printed nothing and could not say why —
    the silent-success failure this repository names in its own ``pyproject.toml``.
    """
    try:
        document = data_document(code_root)
    except KyoseiError as exc:
        document = {"generated_from_commit": None, "entries": [], "unavailable": str(exc)}
    (directory / CHANGELOG_FILENAME).write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return document


def read_packaged_data(directory: Path | None = None) -> dict | None:
    """The packaged data file this copy carries, or ``None`` if it carries none."""
    from .buildinfo import package_dir

    path = (directory if directory is not None else package_dir()) / CHANGELOG_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


@dataclass(frozen=True)
class Changelog:
    """The entries, and the honest account of where they came from."""

    entries: list[Entry]
    source: str
    unavailable: str | None = None
    #: Set only when ``source`` is :data:`SOURCE_PACKAGED` and
    #: :func:`_nearby_source_checkout` found one — the checkout whose live store this
    #: build is not running out of (KYO-372.c2). ``None`` otherwise, including on every
    #: :data:`SOURCE_STORE` reading, where the question does not apply.
    nearby_checkout: Path | None = None


def _nearby_source_checkout(start: Path | None = None) -> Path | None:
    """A Kyosei source checkout reachable by walking up from ``start`` (default: the
    working directory), or ``None`` (KYO-372.c2).

    Distinct from :func:`kyosei.buildinfo.source_tree_root`, which is anchored on
    *this running copy's own* location and answers "what checkout is the code
    currently executing imported out of" — a packaged build (a uv tool install, most
    often) answers ``None`` there wherever it is invoked from,
    by design; that is the whole condition this record is about. This instead asks
    about the directory the command was RUN from, which is what tells an installed `kyo`
    apart from a developer standing inside the very checkout it is failing to read: the
    shape this project's own repository has, ``src/kyosei/`` beside a store -- a live
    ``kyosei/export/`` in the in-repo layout, or the ``.kyosei`` link file in the split
    layout this repository has used since KYO-630.
    """
    current = (start if start is not None else Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "src" / "kyosei").is_dir() and (
            (candidate / "kyosei" / "export").is_dir() or (candidate / ".kyosei").is_file()
        ):
            return candidate
    return None


def live_store_hint(changelog: Changelog, destination: str | Path) -> str | None:
    """KYO-372.c2: ``None`` unless this reading came from the packaged snapshot AND a
    live source checkout is reachable from the working directory — in which case `kyo`
    on PATH cannot be trusted to answer for that checkout (see
    :func:`_nearby_source_checkout`), and this names the command that reads it
    directly, for the caller's own ``destination`` (KYO-156's rule again: the reader's
    own path, not Kyosei's).

    "Present and newer" is read the cheap way: a live store being reachable at all,
    while this build did not read out of it, is grounds enough to say so. Comparing the
    two rendered documents byte for byte to add "and different" would cost this hint an
    entire extra render of the live side on every packaged run, for a case (the
    checkout's own snapshot happens to still be current) a reader can rule out
    themselves in the second it takes to glance at the file this names.
    """
    if changelog.source != SOURCE_PACKAGED or changelog.nearby_checkout is None:
        return None
    return (
        f"a live Kyosei store is present in {changelog.nearby_checkout} and this "
        "build is not running out of it -- `kyo` on PATH read the snapshot packaged "
        "with its own build instead, which cannot see anything completed since. From "
        f"inside that checkout: .venv/bin/python -m kyosei changelog --all --output "
        f"{destination}"
    )


def load() -> Changelog:
    """This build's changelog: the live store where there is one, else the package.

    Order matters and is not arbitrary. A source-tree run has a store that is current by
    definition and a packaged file left by whenever the tree was last installed; reading
    the packaged one there would report a changelog older than the code beside it.
    """
    from .buildinfo import build_identity, install_kind, source_tree_root, upgrade_route

    root = source_tree_root()
    if root is not None:
        try:
            return Changelog(entries_from_store(root), SOURCE_STORE)
        except KyoseiError:
            pass  # fall through to the packaged copy, which is what an install has
    packaged = read_packaged_data()
    nearby = _nearby_source_checkout()
    if packaged is None:
        # KYO-317: this used to name `uv tool install --force .` unconditionally, which
        # was wrong for the reader who reached this branch from a build of another kind
        # -- `install_kind()` (and not `root`, which is already known `None` here) is
        # what tells them apart. KYO-450 (left by KYO-446.d1): the build's own release
        # flag travels with its kind, so a uv tool is sent to `uv tool upgrade kyosei`
        # for a release and to the re-snapshot for an unreleased snapshot -- never to a
        # re-pack of the desktop app, which ships no `kyo` any more.
        return Changelog(
            [],
            SOURCE_PACKAGED,
            unavailable=(
                "this copy carries no packaged changelog and is not running out of a "
                "Kyosei checkout. To fix that: "
                f"{upgrade_route(install_kind(), build_identity().release)}, which "
                "regenerates the changelog as it builds"
            ),
            nearby_checkout=nearby,
        )
    entries = sort_entries([Entry.from_data(raw) for raw in packaged.get("entries") or []])
    return Changelog(
        entries,
        SOURCE_PACKAGED,
        unavailable=packaged.get("unavailable"),
        nearby_checkout=nearby,
    )


# ------------------------------------------------------------------------ filtering

#: ``--since`` takes a date in this form, or a commit-ish resolved against the checkout.
DATE_FORMAT = "YYYY-MM-DD"


def is_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")  # noqa: DTZ007 — a calendar date, not an instant
    except ValueError:
        return False
    return True


def is_instant(value: str) -> bool:
    """``True`` for a full UTC-comparable timestamp (KYO-398): anything longer than a
    date. :func:`resolve_since` only ever returns a date or a normalised instant, so
    the length is the whole test."""
    return len(value) > len("YYYY-MM-DD")


def resolve_since(value: str, code_root: Path | None = None) -> str:
    """``--since``'s value as a ``YYYY-MM-DD`` date, or as a UTC instant.

    A date is taken as written. An ISO-8601 instant with an offset — a build's
    ``commit_time`` from ``kyo version --json``, which is what the per-prompt build
    check (KYO-398.c6) names — is normalised to UTC and filters *strictly after* it.
    This is a read filter and nothing about it is stored, so law 16 is untouched (the
    parser comment on ``--since`` says the same). Anything else is treated as a
    commit-ish and resolved against the checkout this copy runs from — which an
    installed copy does not have, so the refusal names the form that always works
    rather than failing obscurely.
    """
    if is_date(value):
        return value
    from .buildinfo import _git, source_tree_root, utc_timestamp

    if "T" in value:
        instant = utc_timestamp(value)
        if instant is not None:
            return instant

    root = code_root if code_root is not None else source_tree_root()
    resolved = _git(root, "show", "-s", "--format=%cI", value) if root is not None else None
    if resolved:
        # KYO-398.c1: the commit's UTC date, on the store's clock — not `%cI[:10]`,
        # which is the committer's local date and disagrees with it every evening.
        instant = utc_timestamp(resolved.strip().splitlines()[-1])
        if instant is not None:
            return instant[:10]
    # One refusal and not two, though the two readers differ (a source tree that does
    # not have this commit; an installed build with no checkout at all). What differs
    # between them is the *diagnosis*, which is stated below; the remedies are the same
    # two either way, and splitting the message would have given each reader one of
    # them. Both are offered rather than one chosen for them: which is meant depends on
    # whether they wanted a window or the history.
    where = (
        f"a commit `git show` resolves in {root}"
        if root is not None
        else "a commit — this is an installed build, with no git checkout to resolve one in"
    )
    raise UsageError(
        f"`--since {value}` is neither a {DATE_FORMAT} date, a UTC timestamp, nor {where}. "
        "Give it a date "
        f"— `kyo version` prints the date of the build you are running — or run "
        f"`kyo changelog --all` for the whole history"
    )


def since_entries(entries: list[Entry], since: str | None) -> list[Entry]:
    """Entries completed on or after ``since`` (a date), or after it (an instant).

    Inclusive for a date, deliberately. The question this answers is "what does the
    build I am upgrading to have that mine does not", and the two failure directions are
    not symmetric: listing one entry the reader already had costs a line, and hiding one
    they do not have is the defect this record was filed about. An instant is a build's
    commit time (KYO-398), where "after" is exact; an entry with no instant of its own
    (packaged data written before ``completed_at``) is compared by its date, inclusively,
    for the same asymmetry.
    """
    if since is None:
        return list(entries)
    if not is_instant(since):
        return [entry for entry in entries if entry.completed_on >= since]
    return [entry for entry in entries if _after(entry, since)]


def _after(entry: Entry, instant: str) -> bool:
    if entry.completed_at:
        return entry.completed_at > instant
    return entry.completed_on >= instant[:10]


def _at_or_before(entry: Entry, instant: str) -> bool:
    if entry.completed_at:
        return entry.completed_at <= instant
    return entry.completed_on <= instant[:10]


@dataclass(frozen=True)
class Between:
    """The records one build carries that an older one did not (KYO-398.c4)."""

    entries: list[Entry]
    #: ``True`` when every entry was placed by its full instant. ``False`` when at least
    #: one had only a date — packaged data written before ``completed_at`` existed — and
    #: was kept on the inclusive, day-granular test, so the list may name a record
    #: completed earlier on the old build's own day that the old build already had.
    exact: bool


def between(entries: list[Entry], after: str, upto: str) -> Between:
    """Entries completed after ``after`` and at or before ``upto`` — two builds' UTC
    commit times — from the NEWER build's changelog (KYO-398.c4).

    The upper bound is not decoration. A store-sourced changelog (a working tree) can
    hold records completed after its HEAD's commit; a packaged one cannot, by
    construction, but the bound costs nothing and states the question exactly: what is
    in this build that was not in that one.
    """
    shown = [e for e in entries if _after(e, after) and _at_or_before(e, upto)]
    return Between(shown, exact=all(e.completed_at for e in shown))


def grouped(entries: list[Entry]) -> list[tuple[str, list[Entry]]]:
    """``(scope, entries)`` pairs, scopes alphabetical and ``NO_SCOPE`` last.

    Alphabetical rather than "busiest first" so that the committed markdown's diff shows
    the records that were added and nothing else — a group order that moved with the
    data would rewrite the whole document on every completed record.
    """
    groups: dict[str, list[Entry]] = {}
    for entry in entries:
        groups.setdefault(entry.group, []).append(entry)
    ordered = sorted(groups, key=lambda name: (name == NO_SCOPE, name))
    return [(name, groups[name]) for name in ordered]


# ------------------------------------------------------------------------ rendering


def _summary_line(changelog: Changelog, shown: list[Entry], since: str | None) -> str:
    count = len(shown)
    records = "record" if count == 1 else "records"
    if since is None:
        window = ""
    elif is_instant(since):
        window = f" completed after {since}"
    else:
        window = f" completed on or after {since} (UTC)"
    where = (
        "generated from this checkout's store"
        if changelog.source == SOURCE_STORE
        else "read from the data file packaged with this build"
    )
    # KYO-423.c1: every date below is ``at[:10]`` of a UTC instant (KYO-398), and a
    # reader west of Greenwich sees an evening's work dated tomorrow. The bare-date
    # window already said "(UTC)"; the unfiltered and instant headers said nothing.
    return (
        f"{count} {records}{window} — {where}. {containment_sentence(changelog)}. "
        f"{DATES_ARE_UTC}"
    )


#: KYO-423.c1: the one sentence both renderings state about their dates.
DATES_ARE_UTC = (
    "Dates are UTC, so a record completed in the evening west of Greenwich can carry "
    "the next day's date"
)


def containment_sentence(changelog: Changelog) -> str:
    """KYO-398.c2: what a listed record's relation to the running build IS.

    Measured: a consumer session read this command's list, dated after
    the build's own date, as "neither fix is in the version you have" — about two
    records the build carried, byte for byte. A window header alone reads as "newer
    than you"; nothing said the opposite, which is what is true.
    """
    if changelog.source == SOURCE_PACKAGED:
        return (
            "Every record listed is IN the build you are running: this list was packaged "
            "when that build was made and can name nothing newer than it. What a newer "
            "build adds is not here — a newer build's own `kyo changelog` lists it"
        )
    return (
        "Every record listed is in this checkout's store, beside the code this copy runs "
        "live from the same working tree — none is from a build you do not have"
    )


def text_lines(changelog: Changelog, shown: list[Entry], since: str | None) -> list[str]:
    """``kyo changelog``'s human output."""
    from .buildinfo import version_line

    out = [version_line(), _summary_line(changelog, shown, since)]
    if changelog.unavailable:
        out.append(f"WARNING: {changelog.unavailable}.")
    if not shown:
        out.append("")
        out.append("  (nothing)")
        return out
    for scope, entries in grouped(shown):
        out.append("")
        out.append(f"{scope} ({len(entries)}):")
        for entry in entries:
            out.append(
                f"  {entry.completed_on}  {entry.id:<10} {entry.kind:<14} {entry.title}"
            )
    return out


def markdown(changelog: Changelog, shown: list[Entry], since: str | None) -> str:
    """The generated document ``docs/reference/changelog.md`` holds (KYO-226.c4).

    Plain ATX headings, no fenced blocks and no line ``docs_check`` would read as
    metadata, on ``reference_markdown``'s terms: every ``##`` becomes a section, docs
    mode renders it, and ``kyo base run --source docs`` queries it with no second format.
    Deliberately declares no ``governs:`` — see :data:`kyosei.cli.REFERENCE_DECLARES_GOVERNS`
    for why a generated file must not, and the equality test that replaces it.
    """
    out = ["# `kyo` changelog", ""]
    out.append(
        "**This file is generated from Kyosei's own store — one line per completed "
        "record, grouped by scope, newest first. Do not edit it by hand.** Kyosei's own "
        f"copy is `{CHANGELOG_PATH}` (in Kyosei's own repository), regenerated with "
        f"`{CHANGELOG_REGENERATE_COMMAND}` — this checkout's own generator, not `kyo` "
        "on PATH, which is commonly an installed build whose "
        "`changelog` command reads a snapshot baked in at build time rather than this "
        "store (KYO-372) — and kept honest by "
        "`tests/test_kyo226_version_and_changelog.py` (in Kyosei's own repository), "
        "which fails if that copy and the store disagree."
    )
    out.append("")
    out.append(
        "The same entries ship inside the installed package and are readable without "
        "this repository: `kyo changelog` prints what changed since the build you are "
        "running, and `kyo version` says which build that is. Both the stamp and the "
        "packaged entries are written at wheel build time, so neither can be older than "
        "the build carrying it."
    )
    out.append("")
    out.append(f"{DATES_ARE_UTC}.")
    out.append("")
    if since:
        out.append(f"Records completed on or after {since}.")
        out.append("")
    if not shown:
        out.append("No completed records.")
        out.append("")
        return "\n".join(out)
    for scope, entries in grouped(shown):
        out.append(f"## {scope}")
        out.append("")
        for entry in entries:
            out.append(
                f"- **{entry.completed_on}** — `{entry.id}` ({entry.kind}) — {entry.title}"
            )
        out.append("")
    return "\n".join(out)
