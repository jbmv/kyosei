"""Error types and exit codes.

Law 6 (*fail loudly*) needs distinguishable failures: a caller must be able to tell
"you asked for something that does not exist" from "the store refused this transition".

Exit codes are part of the CLI contract:

===  ==========================================================================
0    success
1    runtime error — unknown id, illegal argument combination, IO failure
2    transition-contract violation (law 7); the unmet item is named in the message
3    ``kyo doctor`` found a broken invariant
64   usage error (argparse). Deliberately *not* 2, so that 2 means exactly one thing.
===  ==========================================================================
"""

from __future__ import annotations

from pathlib import Path

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONTRACT = 2
EXIT_DOCTOR = 3
EXIT_USAGE = 64


class KyoseiError(Exception):
    """Base class for every error the CLI reports deliberately."""

    exit_code = EXIT_ERROR


class NotFound(KyoseiError):
    """A named id does not exist. Law 6: an edit naming a nonexistent id errors."""

    exit_code = EXIT_ERROR


class ContractViolation(KyoseiError):
    """A transition contract was not met (law 7).

    ``unmet`` names the specific items that failed, so the message can never
    degrade to "invalid transition".
    """

    exit_code = EXIT_CONTRACT

    def __init__(self, contract: str, unmet: list[str]) -> None:
        self.contract = contract
        self.unmet = unmet
        detail = "\n".join(f"  - {item}" for item in unmet)
        super().__init__(f"contract {contract} not met:\n{detail}")


#: The ``--json`` error-envelope key :class:`SchemaMismatch`'s payload arrives under
#: (KYO-178). One key, so a consumer tests for the payload by presence rather than by
#: recognising which of several fields turned up.
SCHEMA_MISMATCH_KEY = "schema_mismatch"

#: The db's ``meta`` table is stamped with a schema this build does not speak yet: the
#: BUILD is the older party, and the remedy is a newer build. Nothing here reconstructs
#: the store (KYO-168).
SCHEMA_DB_AHEAD_OF_BUILD = "db-ahead-of-build"
#: The db predates this build: the DB is the older party, and `kyo rebuild --force`
#: reconstructs it under the current schema (KYO-5).
SCHEMA_BUILD_AHEAD_OF_DB = "build-ahead-of-db"
#: The db and the build agree and ``config.yml`` does not. Neither party to the
#: db/build comparison is older — reaching this state means both directions above
#: were checked and passed — so ``config.yml`` is the mis-stated copy (KYO-180).
SCHEMA_CONFIG_DRIFT = "config-drift"

#: Which party is the older one, per direction — derived here rather than passed in,
#: so the two fields cannot be made to disagree at a call site (law 3).
_OLDER_SIDE: dict[str, str | None] = {
    SCHEMA_DB_AHEAD_OF_BUILD: "build",
    SCHEMA_BUILD_AHEAD_OF_DB: "db",
    SCHEMA_CONFIG_DRIFT: None,
}


class SchemaMismatch(KyoseiError):
    """Two of the three places a ``schema_version`` is written disagree (KYO-178).

    Raised only by :func:`kyosei.store.open_store`, which is where all three copies
    are in hand at once: ``kyosei/config.yml``, the db's ``meta`` table, and the
    :data:`kyosei.schema.SCHEMA_VERSION` compiled into the running build.

    KYO-168 gave each direction its own sentence, which fixed what a human reads in a
    terminal and gave a *program* nothing: the desktop app (KYO-175.c4) has to tell
    "this store is newer than me" from "this store is older than me" — opposite
    remedies, one of them destructive — and its only route was to substring-match
    prose, which makes its branching a hostage of the next improvement to the wording.
    So the refusal carries a payload alongside its message, the way ``cmd_guard``,
    ``cmd_publish``, ``cmd_views`` and ``cmd_store`` already attach one: ``cli.main``
    merges ``exc.data`` into the ``--json`` error envelope, so this arrives out of
    whatever command the app happened to be running when the store refused to open,
    with no new command and no second call.

    The payload cannot be omitted, which is the point of the class existing at all
    rather than a ``.data`` assignment at each raise site: every field it is made of
    is a required keyword argument, so a refusal without a payload does not construct.
    ``tests/test_kyo5_error_remedies.py`` holds the other half — every refusal in
    ``open_store`` guarded by a ``db_version`` comparison raises *this* class.

    :attr:`direction` is the field to branch on and it names both parties in each of
    its values (``db-ahead-of-build``, not ``db-ahead``) deliberately: KYO-74's
    ``db_export_currency`` envelope already carries a ``direction`` about a different
    seam entirely, with values like ``db-ahead``, and the same consumer maps both.
    """

    exit_code = EXIT_ERROR

    def __init__(
        self,
        message: str,
        *,
        direction: str,
        build_schema_version: int,
        db_schema_version: int,
        config_schema_version: int,
        config_path: Path | str,
        db_path: Path | str,
    ) -> None:
        if direction not in _OLDER_SIDE:
            raise ValueError(
                f"unknown schema mismatch direction {direction!r}; expected one of "
                f"{sorted(_OLDER_SIDE)}"
            )
        super().__init__(message)
        self.direction = direction
        #: ``"build"``, ``"db"``, or ``None`` where neither is older — see
        #: :data:`SCHEMA_CONFIG_DRIFT`.
        self.older_side = _OLDER_SIDE[direction]
        #: Merged into the ``--json`` error envelope by ``cli.main``. Every value is
        #: computed from the arguments above; none of it is parsed back out of
        #: ``message``, so rewording the sentence cannot move it.
        self.data = {
            SCHEMA_MISMATCH_KEY: {
                "direction": self.direction,
                "older_side": self.older_side,
                # All three copies, always, whichever pair disagreed: a consumer
                # showing the reader what each party says has no second way to ask,
                # because the store it would ask did not open.
                "build_schema_version": int(build_schema_version),
                "db_schema_version": int(db_schema_version),
                "config_schema_version": int(config_schema_version),
                # The files those copies live in. The app renders a path and, for
                # KYO-180's hand correction, points at the file to edit — without
                # re-deriving where the store is, which facts.ts declines to do.
                "config_path": str(config_path),
                "db_path": str(db_path),
            }
        }


#: The ``--json`` error-envelope key :class:`ExportSchemaAhead`'s payload arrives under
#: (KYO-181). One key, for the reason :data:`SCHEMA_MISMATCH_KEY` gives at the db
#: boundary: a consumer tests for the payload by presence rather than by recognising
#: which of several fields turned up.
EXPORT_SCHEMA_AHEAD_KEY = "export_schema_ahead"

#: Which half of ``kyosei/export/`` was written by the later build. The two halves are
#: guarded by two different mechanisms — a record document's stamp (KYO-179) and a ledger
#: line's (KYO-181) — and they fail for different reasons, so a payload that could not
#: tell them apart would make the two indistinguishable to the consumer that has to
#: report which. Neither value changes what the reader must DO; both remedies are the
#: same installation.
EXPORT_AHEAD_RECORD_DOCUMENTS = "record-documents"
EXPORT_AHEAD_LEDGERS = "ledgers"


class ExportSchemaAhead(KyoseiError):
    """``kyosei/export/`` was written by a build later than this one (KYO-181).

    Raised only by ``kyosei.rebuild``, at the boundary that actually travels between
    builds: the export is the git-canonical serialization and ``kyosei.db`` is
    gitignored. KYO-179 built the refusal for record documents and KYO-181 extended it to
    the ledgers; both raise this, through one shared message, because a reader has the
    same thing to do either way and two wordings could only drift apart.

    A class rather than a ``.data`` assignment at the raise site, for the two reasons
    :class:`SchemaMismatch` gives one boundary over and both of which bit here first:

    * **The payload cannot be omitted.** Every field it is made of is a required keyword
      argument, so a refusal without a payload does not construct.
    * **The remedy-coverage walk matches raises BY CLASS NAME**
      (``tests/test_kyo5_error_remedies.py``, ``ERROR_CLASS_NAMES`` — this class must be
      listed there). Building an error, assigning ``.data`` and raising the variable is
      invisible to that walk: the site drops silently off the surface, and the message
      could then start naming a destructive `kyo` command with nothing to notice. That is
      not a hypothetical — it is what the first draft of KYO-181 did, and the check
      caught it.

    ``source`` is the field to branch on where a consumer wants to say WHICH half was
    ahead; it is not the field to branch on for what to do, because there is only one
    thing to do. See :data:`EXPORT_AHEAD_RECORD_DOCUMENTS` / :data:`EXPORT_AHEAD_LEDGERS`.
    """

    exit_code = EXIT_ERROR

    def __init__(
        self,
        message: str,
        *,
        source: str,
        build_schema_version: int,
        export_schema_version: int,
        files: list[dict],
    ) -> None:
        if source not in (EXPORT_AHEAD_RECORD_DOCUMENTS, EXPORT_AHEAD_LEDGERS):
            raise ValueError(
                f"unknown export-ahead source {source!r}; expected one of "
                f"{[EXPORT_AHEAD_RECORD_DOCUMENTS, EXPORT_AHEAD_LEDGERS]}"
            )
        super().__init__(message)
        self.source = source
        #: Merged into the ``--json`` error envelope by ``cli.main``. Every value is
        #: computed from the arguments above; none of it is parsed back out of
        #: ``message``, so rewording the sentence cannot move it.
        self.data = {
            EXPORT_SCHEMA_AHEAD_KEY: {
                "source": self.source,
                "build_schema_version": int(build_schema_version),
                "export_schema_version": int(export_schema_version),
                # EVERY offending file, not the three the sentence had room for: prose
                # truncates for a human, a payload must not truncate for a program.
                "files": list(files),
            }
        }


class GuardRefusal(ContractViolation):
    """A guard command refused, and its ``--json`` envelope refuses with it (KYO-187).

    ``kyo check-branch``, ``kyo check-clean`` and ``kyo check-push`` each build one
    ``data`` dict describing what they looked at, and each returns it on the clean
    path. On the refusing path a consumer needs the *same* dict — which branch, which
    files, which trigger — because the refusal is the only output it gets.

    Those three sites used to assign ``error.data = data`` to a local and
    ``raise error``, which cost them two things and one of them silently:

    * **The payload was a line somebody could forget.** Nothing failed at the moment
      it was left off; the ``--json`` envelope just came back with the message and
      nothing else. Here it is a required keyword argument, so a refusal without its
      envelope does not construct.
    * **The site was invisible to the remedy-coverage walk.**
      ``tests/test_kyo5_error_remedies.py`` matches raises BY CLASS NAME on the raise
      node, so ``raise error`` matched nothing and the site dropped off the surface
      that exists to notice a message going stale. That walk now *refuses* the shape
      outright (``test_no_raise_site_hides_its_class_behind_a_variable``) rather than
      trying to follow a variable back to its assignment, so this class is the only
      way these three refusals can be written at all.

    ONE required keyword argument rather than a field per envelope key, which is where
    this departs from :class:`SchemaMismatch` and :class:`ExportSchemaAhead` above.
    Those two assemble a payload that exists only on the failing path, so naming each
    field is what makes it impossible to build half of one. Here the envelope is
    computed once and shared with the command's success return; taking it apart into
    keyword arguments would mean building it twice, and two copies of one envelope is
    exactly the disagreement law 3 says not to create. What the class has to guarantee
    is that the refusal cannot travel *without* the envelope, and that is what a
    required argument guarantees.

    The envelope is merged into the ``--json`` payload at top level by ``cli.main``,
    not nested under a key of its own: these keys (``ok``, ``command``, ``branch``,
    ``guarded_files``, ...) are the command's own output shape, and burying them under
    an envelope key would move every one of them for every existing consumer.
    """

    def __init__(self, contract: str, unmet: list[str], *, data: dict) -> None:
        super().__init__(contract, unmet)
        #: Merged into the ``--json`` error envelope by ``cli.main``. Copied rather
        #: than aliased so the raise cannot be made to report a dict the caller went
        #: on to mutate.
        self.data = dict(data)


class DoctorFailure(KyoseiError):
    """``kyo doctor`` found at least one broken invariant."""

    exit_code = EXIT_DOCTOR

    def __init__(self, failures: list[str]) -> None:
        self.failures = failures
        detail = "\n".join(f"  - {f}" for f in failures)
        super().__init__(f"doctor found {len(failures)} problem(s):\n{detail}")


class BrokenInvariants(DoctorFailure):
    """A check found broken invariants, and reports which checks ran (KYO-187).

    ``kyo doctor`` and ``kyo docs check`` are the two commands whose whole output is a
    verdict over a set of checks, and a consumer told only "3 problem(s)" cannot say
    which check failed or which ones passed. Both build that breakdown for their clean
    path already; this carries the same dict out through the failure.

    A class rather than a ``.data`` assignment beside each ``raise``, for the two
    reasons :class:`GuardRefusal` gives directly above and in the same words: the
    payload cannot be omitted, and the raise stays visible to the remedy-coverage
    walk, which now refuses the ``raise <variable>`` shape rather than trying to see
    through it.
    """

    def __init__(self, failures: list[str], *, data: dict) -> None:
        super().__init__(failures)
        #: Merged into the ``--json`` error envelope by ``cli.main``. See
        #: :attr:`GuardRefusal.data` for why it is not nested under a key.
        self.data = dict(data)


class UsageError(KyoseiError):
    """The invocation itself was malformed."""

    exit_code = EXIT_USAGE
