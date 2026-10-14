"""``kyo doctor`` — the tool's own invariant checks, and the registry that runs them.

Law 14: bespoke tooling fails silently in the direction of looking fine. Every check
exists because some invariant is otherwise only *asserted*. Each returns a list of
human-readable findings.

This module is the registry: :data:`CHECK_ORDER`, :data:`CHECKS`, :data:`NOTICE_CHECKS`,
the tiers, :data:`HOOK_FORMS`, :func:`run_checks` and :func:`sort_into_tiers`. The
checks live by subject in ``doctor_install``, ``doctor_consumers``, ``doctor_registry``,
``doctor_exports``, ``doctor_records`` and ``doctor_dispatch``; import a check from the
module that defines it, never through this one. The history behind them is
docs/design/doctor.md.

**Two tiers, and which one a finding lands in is the check's own declaration**
(KYO-214). A check named in :data:`NOTICE_CHECKS` reports *notices*; every other check
reports *problems*, and ``doctor`` fails with exit code 3 when — and only when — the
problem tier is non-empty. Notices are printed in full either way and exit 0 on their
own. The tier is a property of the check, not of the run: a finding that says of
itself "this is not a block and never was" cannot be allowed to fail the ``Stop`` hook
it is printed from, because Claude Code renders a hook exiting nonzero as an error
block (docs/design/doctor.md#kyo-214).

The checks, and the law each one keeps honest:

============================  =====================================================
``check_schema_version``      the db and this build agree
``check_code_repo_identity_mismatch``  KYO-635 — a split store's recorded
                              ``code_repo_id`` shares no root commit with the linked
                              code repository; `kyo init --store <store> --adopt` is
                              the remedy it names
``check_column_consumers``    law 2 — no stored field without a consumer, and no
                              registered consumer that names nothing (KYO-62)
``check_config_consumers``    law 2, for ``kyosei/config.yml``, same both ways
``check_docs_suggestions``    KYO-303 — `kyo docs suggest` has a declaration to draft.
                              A NOTICE, at most one line, and the only check a project
                              can switch off (``docs_suggest_notice: false``): a
                              suggestion that cannot be declined is not a suggestion
``check_kind_registry``       kyosei/kinds.yml validates, and every record's kind exists
``check_kind_grants_missing``  KYO-394 — a kind's ``accepts_from`` lacks an entry the
                              packaged default grants it. A NOTICE naming the exact
                              edit; ``kind_grants_notice: false`` declines it
``check_kind_policy_missing``  KYO-556 — a knowledge kind's ``policy`` lacks a key the
                              packaged default sets. A NOTICE naming the line to add;
                              ``kind_policy_notice: false`` declines it
``check_prior_art_config``    KYO-585 — ``recx.prior_art`` in kyosei/config.yml parses,
                              and its trigger names only kinds kinds.yml defines and
                              commands that can create them
``check_gate_tags``           kyosei/gate-tags.yml validates, and every tag on a gate is
                              still declared in it (KYO-102)
``check_db_export_currency``  KYO-74 — the db and ``kyosei/export/`` are the same
                              generation, or which side moved is *named*, or the
                              tool says it cannot tell
``check_exports``             exports are re-derivable byte-for-byte from the db
``check_export_roundtrip``    ``kyo rebuild`` from export reproduces the export exactly
``check_allocations``         law 4 — ids monotonic and never reissued
``check_id_prefixes``         ids belong to this project
``check_orphans``             no criterion / gate / note / link without its record
``check_criterion_states``    law 10 — states are known, reasons present
``check_ungated_unobserved_criteria``  KYO-267 — a criterion waiting on a person, with
                              no open ``awaiting:*`` gate and nothing running, so the
                              wait is in no Awaiting list. A NOTICE: the state is
                              expected, and the remedy is one `kyo gate` line
``check_criterion_verification_agreement``  law 3/KYO-4.c5 — state agrees with the latest verdict
``check_completed_with_unmet_criteria``  KYO-415 — a record at its complete-role status
                              holding an unmet criterion. A NOTICE naming `kyo split
                              --to-successor` and reopening as the ways out
``check_open_standing_obligations``  KYO-554 — the count of open standing docs
                              obligations (raised by a record, not a dispatch). A
                              NOTICE: open is the expected state until the section
                              catches up, and `kyo brief` lists each one
``check_statuses``            statuses are known; ``wont-do`` carries its reason
``check_plan_membership``     plan pointers resolve and do not loop
``check_links``               law 6 — the dependency graph is acyclic
``check_events``              every record's creation is in the audit log
``check_events_ledger_layout``  KYO-268 — the export still carries the monolithic
                              ``_events.jsonl`` that a commit re-stores whole. A NOTICE:
                              both layouts read, and migrating is a decision about the
                              maintainer's git history
``check_ledger_gaps``         KYO-404 — every events / sweep / brief / shift-note row
                              is in its ledger and every ledger line has its row;
                              `kyo export --refresh` re-appends what is missing
``check_dispatch_state_consistency``   KYO-7 / law 8 — reported has a report; terminated has a why
``check_report_dispatch_agreement``    KYO-7 — a report and its dispatch point back at each other
``check_report_amendment_chain``       KYO-90 — a dispatch's reports form one chain of amendments,
                               ending at the one its report_id names
``check_split_backlink_symmetry``      KYO-7.c6 — a split's void-withdrawn criterion names a real
                               successor criterion, and the produced-by link exists
``check_restate_backlink_symmetry``    KYO-58.c2 — a restated criterion names a real replacement on
                               its own record, and no two name the same one
``check_flag_types``          KYO-8 — every flag names a known flag_type
``check_flag_resolution_consistency``  KYO-8 — resolved_at/resolved_by are set together, never apart
``check_dispatch_covers_exist``        KYO-13.c4 — a dispatch's covers_json names real records
``check_procedures_rules_missing``     KYO-212 — the ledger carries every rule this build
                               ships; `kyo install-procedures` is the remedy it names
``check_procedures_rules_differ``      KYO-212 — a rule both copies carry, worded
                               differently. A NOTICE: `kyo install-procedures` will not
                               rewrite it on its own, and the project's own wording is a
                               legitimate state; `--adopt` rewrites it on request (KYO-151.c2)
``check_installed_build_lag``  KYO-309 — the running build's packaged skill/ledger is
                               older than the source in THIS checkout. A NOTICE, and
                               only ever reachable in the repository this package is
                               built from: the remedy is to reinstall kyo (`make
                               install-tool`, or `uv tool upgrade kyosei`; KYO-466), never
                               an `install-*` command, which would write the older text
                               over the newer source
============================  =====================================================

The direction ``skill``, ``procedures_rules_missing`` and ``procedures_rules_differ``
compare in (KYO-309): the packaged copy is the source and the project's is the copy,
except in the repository the package is built from, where the project's file is what the
next package will be made of and the installed build can only be behind. Where a checkout
carries ``src/kyosei/defaults/`` those three compare against *it*, and the build's own
age is ``installed_build_lag``'s notice (docs/design/doctor.md#kyo-309).
"""

from __future__ import annotations

from pathlib import Path

from .config import KYOSEI_DIR
from .doctor_consumers import (
    DOCTOR_CACHE_DIRNAME,
    check_column_consumers,
    check_config_consumers,
    read_cache,
    write_cache,
)
from .doctor_dispatch import (
    check_dispatch_covers_exist,
    check_dispatch_state_consistency,
    check_events,
    check_events_ledger_layout,
    check_ledger_gaps,
    check_report_amendment_chain,
    check_report_dispatch_agreement,
    check_restate_backlink_symmetry,
    check_split_backlink_symmetry,
    check_unresolved_stops,
)
from .doctor_exports import (
    check_allocations,
    check_db_export_currency,
    check_export_roundtrip,
    check_export_roundtrip_hook,
    check_exports,
    check_id_prefixes,
    check_orphans,
)
from .doctor_install import (
    check_code_repo_identity,
    check_code_repo_identity_mismatch,
    check_harness,
    check_harness_hook,
    check_hook_config,
    check_hook_config_superseded,
    check_ideas_scratchpad,
    check_installed_build_lag,
    check_orchestrator_only_setup,
    check_path_build_lag,
    check_procedures,
    check_procedures_rules_differ,
    check_procedures_rules_missing,
    check_retired_html_directory,
    check_schema_version,
    check_skill,
    check_skill_superseded,
)
from .doctor_records import (
    check_completed_with_unmet_criteria,
    check_criterion_states,
    check_criterion_verification_agreement,
    check_flag_resolution_consistency,
    check_flag_types,
    check_links,
    check_open_standing_obligations,
    check_plan_membership,
    check_redactions,
    check_statuses,
    check_ungated_unobserved_criteria,
)
from .doctor_registry import (
    check_docs_suggestions,
    check_gate_tags,
    check_kind_grants_missing,
    check_kind_policy_missing,
    check_kind_registry,
    check_scope_registry,
)
from .prior_art import check_prior_art_config
from .store import Store

#: KYO-362: the longest any one doctor finding may be, and the promise
#: ``tests/test_doctor.py`` holds every check to. It lives here, with the registry,
#: because it bounds every registered check's findings rather than any one module's (KYO-438).
#:
#: A number rather than a principle, because a principle ("do not put a whole collection in
#: a message") is what checks violated without anything noticing
#: (docs/design/doctor.md#kyo-362). Seven hundred characters is about eight terminal lines:
#: enough for the several-clause findings this tool legitimately writes (a registry listing
#: its declared values, a seam naming its remedy and what running it would destroy) and far
#: short of a screenful. What it rules out is precisely the shape that grows with the STORE
#: — an id per record, a counter per scope, a document quoted whole — which is the only
#: shape that turns one finding into a terminal full of them.
MAX_FINDING_CHARS = 700

CHECK_ORDER = (
    "schema_version",
    # KYO-559: first after the schema check, because it writes -- the store's config gains
    # the code repository's identity -- and every later check then reads that config.
    "code_repo_identity",
    # KYO-635: beside the fill-in and after it -- the one identity question that check
    # will not answer (a recorded root commit this repository no longer has), a problem.
    "code_repo_identity_mismatch",
    # KYO-596: the Claude Code plugin, for a project on it; the copy checks below fall
    # silent there, so it sits ahead of them.
    "harness",
    "hook_config",
    # KYO-604: the superseded half of `hook_config`'s comparison, a notice.
    "hook_config_superseded",
    "procedures",
    # KYO-212: beside the audience check and after it, on `scope_registry`'s
    # reasoning — a ledger whose audience tokens are broken has a bigger problem
    # than a ledger that is one rule behind, and the two read better in that order.
    # Two of them, because a tier is a property of a check name (KYO-214): the rules
    # the ledger is missing are a problem with a one-command remedy, the rules whose
    # wording has come apart are a notice nothing can clear. Missing first — a rule
    # that is absent is the one the reader can do something about.
    "procedures_rules_missing",
    "procedures_rules_differ",
    "skill",
    # KYO-604: the superseded half of `skill`'s comparison, a notice.
    "skill_superseded",
    # KYO-309: beside `skill` and after it, because it is the finding `skill` used to
    # make wrongly in this repository — the project's copy first, the age of the build
    # reading it second. Silent in every consumer project.
    "installed_build_lag",
    # KYO-335: beside `installed_build_lag` and after it, because the two ask the same
    # question of two different copies and only in that order do they read as one
    # thought — first "is the build running this behind the checkout", then "is the
    # build every HOOK runs so far behind that it cannot open the store at all". The
    # second is the one that costs a project every guard it has.
    "path_build_lag",
    # KYO-215: where `html_freshness` used to sit. The static viewer is retired, so
    # the question is no longer "is the site behind" but "is a site still lying here".
    "retired_html_directory",
    # KYO-566: beside `retired_html_directory` and after it -- both are a file the tool
    # once scaffolded or wrote and nothing reads any more, left on disk for the human.
    "ideas_scratchpad",
    "column_consumers",
    "config_consumers",
    # KYO-298: beside `config_consumers` and after it, on `scope_registry`'s
    # reasoning — a config key nobody reads is a bigger problem than two keys that
    # read consistently and contradict each other, and the two read better in that
    # order.
    "orchestrator_only_setup",
    # KYO-303: after the two config checks and before the registry ones, which is where
    # a reader meets it in the right frame — everything above this line is about a file
    # being WRONG, everything below is about a registry being wrong, and this one is
    # about a registry that is merely quiet. It is also the only check whose finding the
    # project can switch off, and putting it beside the key that switches it off is how
    # the two get read together.
    "docs_suggestions",
    "kind_registry",
    # KYO-394: beside `kind_registry` and after it — a kinds.yml that does not load has
    # a bigger problem than one that loads and grants less than the default, and this
    # check is silent on the first so the two never speak about one file at once.
    "kind_grants_missing",
    # KYO-556: beside its sibling, after it -- the same comparison for `policy`.
    "kind_policy_missing",
    # KYO-585: after the kind checks, because it reads the kind registry: a
    # `recx.prior_art` trigger naming a kind or command that does not exist.
    "prior_art_config",
    # KYO-100: beside its sibling, and after it — a store whose kinds.yml is broken has
    # a bigger problem than a scope, and the two messages read better in that order.
    "scope_registry",
    # KYO-102: beside its two siblings, for the same reason `scope_registry` sits beside
    # `kind_registry` — the three ask one question of three registries, and reading them
    # together is how a broken file is told apart from a stranded reference.
    "gate_tags",
    # KYO-74: ahead of `exports`, because it is the check that says which side of the
    # seam moved and `exports` phrases its own findings in those terms.
    "db_export_currency",
    "exports",
    "export_roundtrip",
    "allocations",
    "id_prefixes",
    "orphans",
    "criterion_states",
    # KYO-267: beside its two siblings and after them, on `scope_registry`'s reasoning
    # — a store whose criterion states are unknown has a bigger problem than one whose
    # wait is merely invisible, and the three read better in that order.
    "ungated_unobserved_criteria",
    "criterion_verification_agreement",
    # KYO-415: after the criterion checks, because it reads criterion states as facts —
    # a store whose states are unknown or disagree with their verdicts has the bigger
    # problem, and this one's finding would be built on it.
    "completed_with_unmet_criteria",
    # KYO-554: after the criterion checks and beside them -- another wait a record
    # carries, here on a document rather than on a person.
    "open_standing_obligations",
    "redactions",
    "statuses",
    "plan_membership",
    "links",
    "events",
    # KYO-268: beside `events` and after it, on `scope_registry`'s reasoning — a store
    # whose audit log has a record missing from it has a bigger problem than one whose
    # ledger is merely in the layout that costs git too much, and the two read better in
    # that order.
    "events_ledger_layout",
    # KYO-404: after the two checks about the events ledger's shape, because this one
    # is about its contents — and about the other three ledgers' too.
    "ledger_gaps",
    "dispatch_state_consistency",
    # KYO-134: beside `dispatch_state_consistency`, because both are about a dispatch
    # whose state on the board and state in the world have come apart — there, one the
    # store still calls running; here, one whose worker stopped without anything
    # checking the tree it stopped in.
    "unresolved_stops",
    "report_dispatch_agreement",
    "report_amendment_chain",
    "split_backlink_symmetry",
    "restate_backlink_symmetry",
    "dispatch_covers_exist",
    "flag_types",
    "flag_resolution_consistency",
)


CHECKS = {
    "schema_version": check_schema_version,
    "code_repo_identity": check_code_repo_identity,
    "code_repo_identity_mismatch": check_code_repo_identity_mismatch,
    "harness": check_harness,
    "hook_config": check_hook_config,
    "hook_config_superseded": check_hook_config_superseded,
    "procedures": check_procedures,
    "procedures_rules_missing": check_procedures_rules_missing,
    "procedures_rules_differ": check_procedures_rules_differ,
    "skill": check_skill,
    "skill_superseded": check_skill_superseded,
    "installed_build_lag": check_installed_build_lag,
    "path_build_lag": check_path_build_lag,
    "retired_html_directory": check_retired_html_directory,
    "ideas_scratchpad": check_ideas_scratchpad,
    "column_consumers": check_column_consumers,
    "config_consumers": check_config_consumers,
    "orchestrator_only_setup": check_orchestrator_only_setup,
    "docs_suggestions": check_docs_suggestions,
    "kind_registry": check_kind_registry,
    "kind_grants_missing": check_kind_grants_missing,
    "kind_policy_missing": check_kind_policy_missing,
    "prior_art_config": check_prior_art_config,
    "scope_registry": check_scope_registry,
    "gate_tags": check_gate_tags,
    "db_export_currency": check_db_export_currency,
    "exports": check_exports,
    "export_roundtrip": check_export_roundtrip,
    "allocations": check_allocations,
    "id_prefixes": check_id_prefixes,
    "orphans": check_orphans,
    "criterion_states": check_criterion_states,
    "ungated_unobserved_criteria": check_ungated_unobserved_criteria,
    "criterion_verification_agreement": check_criterion_verification_agreement,
    "completed_with_unmet_criteria": check_completed_with_unmet_criteria,
    "open_standing_obligations": check_open_standing_obligations,
    "redactions": check_redactions,
    "statuses": check_statuses,
    "plan_membership": check_plan_membership,
    "links": check_links,
    "events": check_events,
    "events_ledger_layout": check_events_ledger_layout,
    "ledger_gaps": check_ledger_gaps,
    "dispatch_state_consistency": check_dispatch_state_consistency,
    "unresolved_stops": check_unresolved_stops,
    "report_dispatch_agreement": check_report_dispatch_agreement,
    "report_amendment_chain": check_report_amendment_chain,
    "split_backlink_symmetry": check_split_backlink_symmetry,
    "restate_backlink_symmetry": check_restate_backlink_symmetry,
    "dispatch_covers_exist": check_dispatch_covers_exist,
    "flag_types": check_flag_types,
    "flag_resolution_consistency": check_flag_resolution_consistency,
}

#: A finding that fails the command: exit 3, and the hook chain it runs from stops.
TIER_PROBLEM = "problem"
#: A finding that is reported and exits 0.
TIER_NOTICE = "notice"

#: KYO-214: the checks whose findings are NOTICES. A set of names beside :data:`CHECKS`
#: rather than a wrapper around each check function, because the tier is one word about
#: a whole check and nothing in the check body needs to read it — the cheapest shape
#: that still puts the declaration next to the registry it qualifies, where a check
#: added later is visibly either in it or not.
#:
#: The bar for membership, stated so the next addition is a judgment against something:
#: a notice reports a condition that is real, that the reader may need, and that
#: **the reader cannot clear by acting on it now**. ``unresolved_stops`` is the
#: founding case — its own text says "this is not a block and never was" and "it clears
#: on its own once those dispatches are no longer running", so failing on it asked a
#: controller to fix a condition whose only remedy is waiting. Everything else stays a
#: problem, including the checks whose findings name a remedy command (``skill``,
#: ``hook_config``, ``procedures_rules_missing``): a named remedy is precisely the case
#: where the reader CAN act (docs/design/doctor.md#kyo-214).
#:
#: A remedy-naming check may join only by arguing two things, on the check itself where
#: its next reader will find them: that the condition is EXPECTED rather than broken,
#: and that failing on it — exit 3 from the ``Stop`` and ``SessionEnd`` hooks, rendered
#: as a hook error on every stop — would cost the reader more than the finding is worth
#: (docs/design/doctor.md#kyo-267). The members, and the argument each makes:
#:
#: * ``unresolved_stops`` (KYO-214): it clears on its own; the only remedy is waiting.
#: * ``procedures_rules_differ`` (KYO-212): the ledger is the project's to edit, so a
#:   different wording may be deliberate, and nothing in the tool rewrites a rule that
#:   is already there.
#: * ``retired_html_directory`` (KYO-215): it names no remedy command at all.
#: * ``ideas_scratchpad`` (KYO-566): every store scaffolded before KYO-566 holds the
#:   file, and where each thought in it belongs is the reader's judgement, not a command.
#: * ``ungated_unobserved_criteria`` (KYO-267): recording a wait for a person is correct
#:   behaviour, and the interval before it is gated is a legitimate state.
#: * ``events_ledger_layout`` (KYO-268.c3): every store written before schema_version 20
#:   has the old layout, the reader accepts it by design, and migrating is a decision
#:   about the maintainer's git history rather than a repair.
#: * ``orchestrator_only_setup`` (KYO-298.c5): which of two config lines is wrong is an
#:   editorial judgment about the project's own file.
#: * ``docs_suggestions`` (KYO-303.c2): a suggestion must never be a problem, and the
#:   project can switch it off (``docs_suggest_notice: false``).
#: * ``installed_build_lag`` (KYO-309.c1): in the repository this package is built from
#:   the build is older than the checkout after every edit until someone reinstalls kyo
#:   (`make install-tool`, or `uv tool upgrade kyosei` for a release; KYO-466), and the
#:   finding is unreachable from a consumer project.
#: * ``kind_grants_missing`` (KYO-394): a narrowed kind is a legitimate registry, and
#:   the project can switch it off (``kind_grants_notice: false``).
#: * ``kind_policy_missing`` (KYO-556): a key left out of a kind's policy reads as false,
#:   which may be the project's choice, and it can switch the notice off
#:   (``kind_policy_notice: false``).
#: * ``completed_with_unmet_criteria`` (KYO-415): recording a regression on a closed
#:   record is correct, and split-or-reopen is the controller's judgment.
#: * ``open_standing_obligations`` (KYO-554): an open standing obligation is the expected
#:   state between filing a decision and editing the section it names.
#: * ``hook_config_superseded`` (KYO-604): every release that changes a packaged hook's
#:   text leaves each project that has not rerun install-hooks on the earlier text, which
#:   still runs; failing on it painted every stop red in every such project. A hook that
#:   is missing or edited stays ``hook_config``, a problem.
#: * ``skill_superseded`` (KYO-604): the same argument for a `/kyosei` skill copy whose
#:   recorded digest says it is still exactly what an earlier build wrote. An edited
#:   copy, or one with no digest, stays ``skill``, a problem.
#: * ``code_repo_identity`` (KYO-559): its finding reports a fill-in it has already made
#:   (a missing ``code_repo_id`` recorded), so there is nothing left for the reader to do.
NOTICE_CHECKS = frozenset(
    {
        "code_repo_identity",
        "completed_with_unmet_criteria",
        "hook_config_superseded",
        "skill_superseded",
        "unresolved_stops",
        "procedures_rules_differ",
        "retired_html_directory",
        "ideas_scratchpad",
        "ungated_unobserved_criteria",
        "events_ledger_layout",
        "orchestrator_only_setup",
        "docs_suggestions",
        "installed_build_lag",
        "kind_grants_missing",
        "kind_policy_missing",
        "open_standing_obligations",
    }
)


def tier_of(name: str) -> str:
    """Which tier ``name``'s findings land in — :data:`TIER_PROBLEM` or notice.

    Derived from :data:`NOTICE_CHECKS` at every call rather than stored beside each
    finding (law 3): one declaration, one reader, nothing that can disagree with it.
    """
    return TIER_NOTICE if name in NOTICE_CHECKS else TIER_PROBLEM


#: KYO-408: the checks whose `kyo doctor --hook` form differs from bare `kyo doctor`'s,
#: and the function that form runs. Every other check runs identically in both. A hook
#: form takes ``(store, detail)`` and writes how it satisfied the check into ``detail``
#: under its own name, which `--hook --json` reports as ``hook_forms``.
HOOK_FORMS = {
    "export_roundtrip": check_export_roundtrip_hook,
    # KYO-596: Claude Code's files rather than a `claude` subprocess on every stop.
    "harness": check_harness_hook,
}


def run_checks(
    store: Store, *, hook: bool = False, detail: dict | None = None
) -> dict[str, list[str]]:
    """Every check's findings, in :data:`CHECK_ORDER` — both tiers, unsorted.

    The tier is not applied here on purpose: this is the whole of what the checks
    found, and :func:`sort_into_tiers` is the one place that splits it. A `--json`
    consumer therefore reads every check's findings under ``checks`` whichever tier
    they are in, and the two lists it also gets are a rendering of the same data.

    KYO-408: ``hook=True`` runs each check's :data:`HOOK_FORMS` entry where it has one.
    Every check still runs and still reports under its own name; what differs is how
    much a hook form re-verifies of what an earlier passing run already verified.
    """
    results: dict[str, list[str]] = {}
    for name in CHECK_ORDER:
        if hook and name in HOOK_FORMS:
            results[name] = HOOK_FORMS[name](store, detail)
        else:
            results[name] = CHECKS[name](store)
    return results


def sort_into_tiers(results: dict[str, list[str]]) -> dict[str, list[str]]:
    """``{"problem": [...], "notice": [...]}`` — every finding, tagged with its check.

    The ``[check_name] message`` prefixing lives here rather than in ``cmd_doctor`` so
    that the two lists, the failure message and the ``--json`` envelope are all the
    same strings (law 3).
    """
    tiers: dict[str, list[str]] = {TIER_PROBLEM: [], TIER_NOTICE: []}
    for name, messages in results.items():
        tiers[tier_of(name)].extend(f"[{name}] {message}" for message in messages)
    return tiers


# ----------------------------------------------------------------------------- KYO-564
#
# `kyo onboard --check` never opens the database (opening a WAL database writes its index
# even under ``query_only``), so it cannot run these checks -- and before KYO-564 it said
# ``kyo doctor: not-run`` even straight after a clean `kyo doctor`, so a finished setup read
# back as unfinished. Every `kyo doctor` run, bare or ``--hook``, now leaves its verdict in
# one small file the readback can read without the database.
#
# Where: :data:`DOCTOR_CACHE_DIRNAME` beside ``kyosei.db``, the self-ignoring directory the
# export round-trip cache already lives in (KYO-408). Machine-local and never under
# ``kyosei/export/``: a verdict is about this machine's database and this build, and a
# fresh clone carrying another machine's "clean" would be a false readback. The
# directory's own ``.gitignore`` of ``*`` keeps it out of git without a new line in
# ``kyosei/.gitignore`` (which an existing project only gains via `kyo
# install-procedures`).

#: The file, inside :data:`DOCTOR_CACHE_DIRNAME`.
LAST_RESULT_NAME = "last-result.json"


def last_result_path(store_root: Path) -> Path:
    """Where `kyo doctor` records its last result, from the STORE root alone -- so a
    reader holding only a resolved layout (`kyo onboard --check`) finds the same file
    :func:`record_last_result` wrote without constructing a :class:`Store`. Under the
    split layout ``store_root`` is the store's checkout, where ``kyosei.db`` is."""
    return store_root / KYOSEI_DIR / DOCTOR_CACHE_DIRNAME / LAST_RESULT_NAME


def record_last_result(
    store: Store, results: dict[str, list[str]], *, hook: bool = False
) -> None:
    """Record this run's verdict: clean, or the names of the problem-tier checks that
    found something, with the instant and the build that ran them. Never raises (it goes
    through :func:`kyosei.doctor_consumers.write_cache`): a doctor that could not write
    its verdict must still report the checks it ran.

    The build is the commit alone, read without a dirty check -- this runs on every
    session stop under ``--hook``, and a `git status` per stop is a cost the readback's
    "which build said so" does not need."""
    from . import clock
    from .buildinfo import build_identity

    identity = build_identity(with_dirty=False)
    failing = [
        name for name, msgs in results.items() if msgs and tier_of(name) == TIER_PROBLEM
    ]
    write_cache(
        last_result_path(store.config.root),
        {
            "at": clock.now(),
            "clean": not failing,
            "failing": failing,
            "notices": sum(
                len(msgs) for name, msgs in results.items() if tier_of(name) == TIER_NOTICE
            ),
            "hook": hook,
            "version": identity.version,
            "commit": identity.short_commit,
        },
    )


def read_last_result(store_root: Path) -> dict | None:
    """The last recorded result, or ``None`` when none is recorded or it cannot be read
    (another format, truncated). The caller tells those two apart by whether
    :func:`last_result_path` exists."""
    data = read_cache(last_result_path(store_root))
    if data is None or not isinstance(data.get("at"), str):
        return None
    if not isinstance(data.get("failing"), list):
        return None
    return data
