"""KYO-9: ``kyosei.procedures`` must never truncate a rule's justifying prose
mid-sentence, even when the paragraph soft-wraps across several physical lines
in the markdown source (the shape every real rule in ``kyosei/procedures.md``
already uses).

KYO-86 adds the other direction: not "is the rule transmitted intact" but "is the
rule *true*". The prose-as-argument rule claimed, under ``enforced-by: mechanism``,
that no free-text CLI flag existed while four of them shipped — a rule typeset as
enforced when it was a mandate, inside the ledger law 1 exists to keep honest, and
injected verbatim into every worker's brief. The tests at the bottom of this file
compare that rule's flag list against ``kyosei.cli``'s parser, so the next
convenience flag cannot re-break it in silence.

KYO-93 adds the direction KYO-86 could not see. Within a day of that comparison being
written, KYO-91 changed what the tool *does* to those flags — all four checked for shell
residue, refused or warned depending on whether a ``--<flag>-file`` twin exists — and the
rule went on describing the older, weaker behaviour while this file stayed green, because
the *set of flags* had not moved. The last section drives every prose-argument flag
through the real CLI and compares the treatment it actually gets against the treatment
the ``enforced-by`` line claims."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from kyosei import cli
from kyosei.brief import _concurrency_bullet
from kyosei.cli import PROSE_ARGUMENT_METAVAR, build_parser, prose_argument_flags
from kyosei.doctor import TIER_NOTICE, TIER_PROBLEM
from kyosei.errors import EXIT_DOCTOR, KyoseiError
from kyosei.procedures import (
    LEDGER_CHANGED,
    LEDGER_MISSING,
    LEDGER_PREAMBLE,
    PACKAGED_LEDGER_SOURCE_PATH,
    PREAMBLE_TITLE,
    STORE_LEDGER_PATH,
    Rule,
    adopt_preamble,
    adopt_rule,
    compare_ledgers,
    merge_ledger,
    orchestrator_rules,
    parse_procedures,
    preamble,
    rule_bodies,
    worker_rules,
)
from kyosei.procedures import ledger_differences as procedures_ledger_differences
from kyosei.prose import contains_phrase
from kyosei.scaffold import defaults_dir

from .conftest import invoke, new_record
from .test_dispatch_covers import _elsewhere, _scoped
from .test_prose import MANGLED, RESIDUE_CASES
from .this_store import store_path

MULTI_LINE_RULE = """\
### A rule whose prose soft-wraps

- **audience**: worker
- **enforced-by**: mechanism — a made-up check

Shell interpolation silently deleted words mid-sentence three times in one
session of an earlier project, and reported success each time (Law 5).
Never construct a `kyo` command whose prose argument was built by
string-interpolating a shell variable.
"""


def test_first_line_is_the_full_first_sentence_not_a_mid_sentence_cut() -> None:
    rules = parse_procedures(MULTI_LINE_RULE)
    assert len(rules) == 1
    rule = rules[0]
    # The previous behaviour cut at the first physical line, producing exactly
    # this dangler — assert it is gone.
    dangler = "Shell interpolation silently deleted words mid-sentence three times in one"
    assert rule.first_line != dangler
    assert not rule.first_line.endswith(" the")
    # The full first sentence, soft-wrap collapsed to a single line, unbroken.
    assert rule.first_line == (
        "Shell interpolation silently deleted words mid-sentence three times in one "
        "session of an earlier project, and reported success each time (Law 5)."
    )
    # And it stops there — the second sentence is not swept in too.
    assert "Never construct" not in rule.first_line


def test_single_physical_line_paragraph_still_works() -> None:
    text = """\
### Short rule

- **audience**: worker
- **enforced-by**: aspirational

One short sentence. A second sentence that must not appear.
"""
    rules = parse_procedures(text)
    assert rules[0].first_line == "One short sentence."


# ------------------------------------------- KYO-66.c1: the phrase-search rule


def _phrase_rule(text: str):
    matches = [r for r in orchestrator_rules(text) if "phrase search" in r.title]
    assert len(matches) == 1, [r.title for r in orchestrator_rules(text)]
    return matches[0]


def test_the_phrase_search_rule_is_shipped_and_reaches_the_controller(
    kyo, project: Path
) -> None:
    """KYO-66.c1. `kyo brief` injects controller-audience rules and nothing else, so a
    rule that parses but names the wrong audience reaches nobody while looking like it
    reaches someone (KYO-27). Read from the scaffolded project, which is the packaged
    default — the copy every new Kyosei project starts from."""
    text = (project / "kyosei" / "procedures.md").read_text(encoding="utf-8")
    rule = _phrase_rule(text)
    assert "distinctive" in rule.title and "word" in rule.title
    assert "phrase search" in kyo("brief").out


def test_the_phrase_search_rule_says_aspirational_verbatim(kyo, project: Path) -> None:
    """KYO-66.c1, and law 1: nothing can check which search a person reached for, so
    claiming enforcement here would be exactly the overstatement `enforced-by` exists
    to prevent. The word is asserted verbatim because that is the whole content of the
    claim — "mechanism, partial" would be a different and false one."""
    text = (project / "kyosei" / "procedures.md").read_text(encoding="utf-8")
    rule = _phrase_rule(text)
    assert rule.enforced_by.startswith("aspirational")


def test_the_phrase_search_rule_states_all_three_of_its_parts(kyo, project: Path) -> None:
    """A rule that says "be careful with grep" is not followable. This one has to carry
    the asymmetry (a phrase search proves nothing about *absence*), the human-followable
    form (one distinctive word, then read the context), and the programmatic form
    (join the words with `\\s+`) — the third because a controller doing a replace has no
    use for the first two."""
    text = (project / "kyosei" / "procedures.md").read_text(encoding="utf-8")
    body = text.split(_phrase_rule(text).title, 1)[1].split("\n### ", 1)[0]
    flat = " ".join(body.split())
    assert "absent" in flat
    assert "single **word**" in flat
    assert r"`\s+`" in flat
    assert "hard-wrapped" in flat


def test_worker_rules_filters_by_audience_and_still_carries_full_sentences() -> None:
    text = MULTI_LINE_RULE + """

### A controller-only rule

- **audience**: controller
- **enforced-by**: aspirational

This must never reach worker_rules().
"""
    rules = worker_rules(text)
    assert len(rules) == 1
    assert rules[0].audience == "worker"
    assert rules[0].first_line.endswith("(Law 5).")


# ------------------------------- KYO-86: the prose-as-argument rule and the parser agree
#
# `kyosei/procedures.md` asserted "there is no free-text CLI flag for prose" under
# `enforced-by: mechanism`, while ten subparsers offered `--why` and four more offered
# `--title`/`--do`/`--method`. A consumer's controller found it the way these are always
# found: a `--why` whose backticks the shell ate before `kyo` ran, stored mangled, no
# complaint.
#
# c1 is satisfiable by editing that sentence, and a sentence drifts — which is the whole
# subject of the record. So c2 asks for the two halves to be *compared*: the rule names its
# flags, `kyosei.cli.prose_argument_flags` reads the same set off the shipped parser (each
# such flag declares itself with `metavar=PROSE_ARGUMENT_METAVAR`), and these tests fail if
# they disagree in either direction.
#
# Read from the scaffolded project rather than this repo's own store, for the same reason
# the KYO-66 tests above do: that file *is* `kyosei/procedures.md` for every project `kyo
# init` creates, and it is the copy the build actually ships.

#: The rule's own title prefix. Matched as a phrase (`kyosei.prose.contains_phrase`)
#: because this file is hard-wrapped and a fixed-string `in` would answer a narrower
#: question than the one being asked — the rule two sections down in procedures.md.
PROSE_RULE_TITLE = "Prose enters the store only via stdin or a file"


def _procedures(project: Path) -> str:
    return (project / "kyosei" / "procedures.md").read_text(encoding="utf-8")


def _prose_rule(text: str) -> Rule:
    matches = [r for r in worker_rules(text) if contains_phrase(r.title, PROSE_RULE_TITLE)]
    assert len(matches) == 1, [r.title for r in worker_rules(text)]
    return matches[0]


def _flags_named_by(rule: Rule) -> set[str]:
    """The prose-as-argument flags the rule's `enforced-by` line lists.

    Every backtick-quoted `--flag` in the line, minus the `--*-file` family: those are
    the *safe* path the rule points at, not the hole it is admitting to.
    """
    named = re.findall(r"`(--[^`\s]+)`", rule.enforced_by)
    return {flag for flag in named if not flag.endswith("-file")}


def test_the_rule_lists_exactly_the_flags_that_take_prose_as_an_argument(project: Path) -> None:
    """KYO-86.c2. The rule and the parser are compared, not both hand-maintained: add a
    `--why`-style flag and this fails until the rule admits it exists."""
    named = _flags_named_by(_prose_rule(_procedures(project)))
    assert named == set(prose_argument_flags()), named


def test_the_comparison_is_not_vacuous(project: Path) -> None:
    """The canary the whole of c2 rests on. A comparison that reads an empty set from one
    side passes forever and says nothing — so prove both sides are populated, and prove
    that a parser carrying one extra prose flag would fail the test above."""
    assert prose_argument_flags()
    assert _flags_named_by(_prose_rule(_procedures(project)))

    parser = build_parser()
    subcommands = next(a for a in parser._actions if isinstance(a.choices, dict)).choices
    subcommands["wont-do"].add_argument("--reason", metavar=PROSE_ARGUMENT_METAVAR)

    widened = set(prose_argument_flags(parser))
    assert "--reason" in widened
    assert widened != _flags_named_by(_prose_rule(_procedures(project)))


def test_the_enforced_by_line_no_longer_claims_no_such_flag_exists(project: Path) -> None:
    """KYO-86.c1, both halves. The claim that killed the rule is gone, and what is left
    is typeset as the partial mechanism it is — a bare `mechanism` here would be law 1's
    exact failure, in the ledger written to prevent it."""
    rule = _prose_rule(_procedures(project))
    assert not contains_phrase(rule.enforced_by, "there is no free-text CLI flag for prose")
    assert rule.enforced_by.startswith("mechanism, partial")
    # And the part that *is* enforced is still stated, not thrown out with the false half.
    assert contains_phrase(rule.enforced_by, "multi-line prose has no flag-value form")


def test_the_rewritten_rule_still_parses_and_still_carries_its_reason(project: Path) -> None:
    """A rule rewritten into a shape `kyosei.procedures` cannot parse reaches nobody while
    looking like it reaches someone (KYO-27) — and `_prose_rule` selects out of
    `worker_rules`, so a rule that lost its audience would fail this file everywhere at
    once. The justifying sentence is the only part of the "why" a worker ever sees, and it
    is the sentence the whole rule exists for."""
    rule = _prose_rule(_procedures(project))
    assert rule.audience == "worker"
    assert rule.first_line.endswith("(Law 5).")
    assert contains_phrase(rule.first_line, "delete words mid-sentence")


# --------------------------------------------------- KYO-86.c3: what the operator wrote

#: Balanced backticks and quotes, plus the metacharacters a shell would act on if one were
#: between the operator and `kyo`. `check_not_shell_mangled` deliberately leaves a balanced
#: pair alone — quoting a phrase is ordinary English, and a check that fires on ordinary
#: input is one people learn to route around.
SHELL_ISH_REASON = 'superseded by `make test`; $PATH & *.py globbing, see "the note"'


def test_a_reason_full_of_shell_metacharacters_is_stored_exactly_as_written(kyo) -> None:
    """KYO-86.c3. Whichever path survives, the reason in the store is the reason the
    operator wrote — byte for byte, and echoed back with the digest that proves it."""
    record = new_record(kyo, criteria=["a"])
    result = kyo(
        "edit", record, "--criterion", f"{record}.c1", "--state", "void-premise-gone",
        "--why", SHELL_ISH_REASON,
    )
    assert result.code == 0, result

    stored = kyo("show", record, "--json").json["criteria"][0]["reason"]
    assert stored == SHELL_ISH_REASON
    digest = hashlib.sha256(SHELL_ISH_REASON.encode("utf-8")).hexdigest()[:12]
    assert f"sha256:{digest}" in result.out


def test_the_same_reason_arrives_intact_through_the_file_path_too(kyo, write_file) -> None:
    """The two paths must agree, or "use --why-file instead" is advice that changes the
    text it was supposed to preserve."""
    record = new_record(kyo, criteria=["a"])
    path = write_file("why.md", SHELL_ISH_REASON + "\n")
    result = kyo(
        "edit", record, "--criterion", f"{record}.c1", "--state", "void-premise-gone",
        "--why-file", path,
    )
    assert result.code == 0, result
    assert kyo("show", record, "--json").json["criteria"][0]["reason"] == SHELL_ISH_REASON


def test_a_reason_the_shell_ate_half_of_is_refused_and_nothing_is_stored(kyo) -> None:
    """The reported harm, which is silence rather than error: backticks ran a command and
    `kyo` was handed the remainder. An unbalanced pair is the wreckage, and the refusal
    names the flag that would have survived it."""
    record = new_record(kyo, criteria=["a"])
    result = kyo(
        "edit", record, "--criterion", f"{record}.c1", "--state", "void-premise-gone",
        "--why", "superseded by the `make test run",
    )
    assert result.code == 64, result
    assert "unbalanced" in result.err
    assert "--why-file" in result.err
    assert kyo("show", record, "--json").json["criteria"][0]["state"] == "unmet-unbuilt"


# ----------------- KYO-93: the rule's ENFORCEMENT claim, not only its flag list
#
# KYO-86 compared two sets, and the sets agreed. KYO-91 then changed the *treatment*
# — every prose-argument flag checked for shell residue, refused where a
# `--<flag>-file` twin exists and warned about loudly where none does — while the rule
# went on saying `--why` alone was checked and that a balanced substitution left
# nothing behind at all. Membership had not moved, so nothing above fired, and a rule
# injected verbatim into every dispatch brief spent a day understating its own tool.
#
# So the `enforced-by` line's claims are checked against *behaviour*, not against the
# parser's shape. A shape-based check would still pass with KYO-91 reverted: `--why`
# had a `--why-file` twin before that record and has one after it, and what changed was
# which flags anything looked at. Each flag is driven through the real CLI instead, and
# classified by what comes back.
#
# WHAT THIS CANNOT CATCH — stated here because a check that oversells its own reach is
# precisely the defect this record is about (KYO-86.c2 read as a broader guarantee than
# it was, which is how the drift below went unseen):
#
#   * Paraphrase. Only the two derivation clauses are pinned as text. Reword any other
#     sentence in the line and nothing here notices — including the sentence describing
#     *how* residue is detected, which is sampled by two values, never pinned. Widen
#     `shell_residue` to a shape neither sample has and the rule's description of it can
#     go stale exactly the way this record's did.
#   * A branch the code has but no flag currently reaches. If every prose flag grew a
#     file twin, the warn branch would be unexercised, and a true statement about it and
#     a false one would look the same from here. What is required unconditionally is
#     that the rule say exactly one thing per case; the comparison against behaviour
#     runs only where an instance exists.
#   * The invisible substitution itself, which is invisible by construction (KYO-91).
#     What is checked is that the rule keeps *admitting* it — see the test at the end.

#: The two things the tool can do about residue, spelled the way the rule spells them:
#: these strings are substituted into the clauses the rule is required to contain.
REFUSED = "refused"
WARNED = "warned about"

#: Two residues, because the `enforced-by` line names two detections. `MANGLED` is
#: KYO-91's gap (that record's reproduction, written post-substitution); the second is
#: KYO-26's odd delimiter, kept as weak evidence. Driving only one would let the other
#: be deleted in silence while the rule went on claiming both.
RESIDUE_SAMPLES = (MANGLED, "half a `quotation")

#: The phrase in which the rule admits what nothing can see. KYO-91 is emphatic that its
#: detection closes nothing, and a rule that quietly upgraded "open hole" to a guarantee
#: would be the same overstatement in the opposite direction to the one being fixed.
THE_HOLE = "an open hole, not a closed one"


def _derivation_phrase(has_twin: bool, treatment: str) -> str:
    """The clause the rule must contain, for one half of the derivation.

    Built rather than written out, so the *direction* is code-derived: invert the
    parser's rule and the expected clause inverts with it, and the rule as written
    stops matching. A pair of hand-typed literals would agree with an inverted tool.
    """
    subject = "a flag with a `--<flag>-file` twin" if has_twin else "a flag without one"
    return f"{subject} is {treatment}"


def _flags_with_a_file_twin() -> set[str]:
    """Every option some subparser offers a ``<flag>-file`` form of alongside it.

    The predicate the rule states, read off the shipped parser — the test below checks
    that the tool's actual behaviour follows it, rather than assuming it does.
    """
    found: set[str] = set()
    stack = [build_parser()]
    while stack:
        current = stack.pop()
        offered = {opt for action in current._actions for opt in action.option_strings}
        found |= {flag for flag in offered if f"{flag}-file" in offered}
        for action in current._actions:
            if isinstance(action.choices, dict) and action.choices:
                stack.extend(action.choices.values())
    return found


def _treatment(kyo, write_file, flag: str, value: str) -> str | None:
    """What the shipped tool actually does about ``value`` passed to ``flag``.

    :data:`REFUSED`, :data:`WARNED`, or ``None`` for "took it without a word" — the
    state the rule asserts no one-line text flag is in. The command lines come from
    ``tests/test_prose.py``'s ``RESIDUE_CASES``, which is itself pinned there to equal
    ``prose_argument_flags()``; a second driver map here would be a second hand-kept
    list, which is the shape KYO-86 was filed about.
    """
    record = new_record(kyo, criteria=["a criterion"])
    result = RESIDUE_CASES[flag](kyo, record, value, write_file=write_file)
    if result.code != 0:
        assert flag in result.err, result
        assert f"{flag}-file" in result.err, "the rule says a refusal names that twin"
        return REFUSED
    if "warning" in result.out and flag in result.out:
        return WARNED
    return None


def _the_rule_describes_the_enforcement(kyo, write_file, project: Path) -> None:
    """KYO-93.c2 as a helper, so that c3 can assert it *fails* on a reverted tool."""
    rule = _prose_rule(_procedures(project))
    twinned = _flags_with_a_file_twin()
    flags = prose_argument_flags()
    assert flags, "nothing to compare: the derived side of the comparison went empty"

    observed: dict[str, str] = {}
    for flag in flags:
        seen = {_treatment(kyo, write_file, flag, value) for value in RESIDUE_SAMPLES}
        assert seen != {None}, (
            f"{flag} is checked for shell residue by nothing, and the rule says every "
            f"one-line text flag is"
        )
        assert len(seen) == 1, f"{flag}: the two residues are treated differently: {seen}"
        treatment = seen.pop()
        expected = REFUSED if flag in twinned else WARNED
        assert treatment == expected, (
            f"{flag} is {treatment}, but the rule derives {expected} from whether a "
            f"{flag}-file twin exists"
        )
        observed[flag] = treatment

    for has_twin in (True, False):
        stated = {
            candidate
            for candidate in (REFUSED, WARNED)
            if contains_phrase(rule.enforced_by, _derivation_phrase(has_twin, candidate))
        }
        case = _derivation_phrase(has_twin, "<refused|warned about>")
        assert len(stated) == 1, (
            f"the enforced-by line must say exactly one thing about the case "
            f"{case!r}; it says {stated or 'nothing'}"
        )
        instances = {t for flag, t in observed.items() if (flag in twinned) is has_twin}
        assert not instances or instances == stated, (
            f"for {case!r} the rule claims {stated} and the tool does {instances}"
        )


def test_the_rule_describes_the_enforcement_the_tool_actually_has(
    kyo, write_file, project: Path
) -> None:
    """KYO-93.c2. Every prose-argument flag is driven with both residues the rule names,
    and what comes back is compared with what the rule claims comes back — so a change
    to the *treatment* fails here the way a change to the *set of flags* already fails
    above. Read from the scaffolded project, the copy the build ships."""
    _the_rule_describes_the_enforcement(kyo, write_file, project)


def test_that_check_bites_when_kyo91s_enforcement_is_reverted(
    kyo, write_file, project: Path, monkeypatch
) -> None:
    """KYO-93.c3. KYO-91's change is one wire: `cli.main` running
    `check_prose_arguments` over every prose flag the invocation gave. Cut that wire and
    the tool is back where it was — `--why` still refused, because `commands._reason`
    calls the check directly, and `--do`, `--title`, `--method` looked at by nothing.
    The rule text is untouched. c2's check must fail on that, or it is another test that
    is green for reasons unrelated to what it claims."""
    monkeypatch.setattr(cli, "check_prose_arguments", lambda parser, args: [])

    # The reverted asymmetry itself, so this fails loudly if "revert" stops meaning this.
    assert _treatment(kyo, write_file, "--why", MANGLED) == REFUSED
    assert _treatment(kyo, write_file, "--do", MANGLED) is None

    with pytest.raises(AssertionError) as raised:
        _the_rule_describes_the_enforcement(kyo, write_file, project)
    assert "checked for shell residue by nothing" in str(raised.value)


@pytest.mark.parametrize("flag", sorted(RESIDUE_CASES))
def test_every_flag_the_rule_names_does_refuse_a_newline(
    kyo, write_file, flag: str
) -> None:
    """The line's other enforcement claim, and the older one: "each refuses a newline".
    It was true when KYO-86 wrote it and nothing since has checked it, which is the
    condition every part of this rule was in until now."""
    record = new_record(kyo, criteria=["a criterion"])
    result = RESIDUE_CASES[flag](kyo, record, "first line\nsecond line", write_file=write_file)
    assert result.code == 64, result
    assert "single line" in result.err, result


def test_the_rule_still_admits_the_hole_and_the_hole_is_still_open(
    kyo, write_file, project: Path
) -> None:
    """The claim that cannot be derived and must not be dropped. A substitution whose
    command printed output leaves no residue, so the tool takes it in silence — driven
    here rather than asserted, because the rule's "open hole, not a closed one" is a
    statement about that silence. If some later check ever did see it, this fails, and
    the sentence gets to be rewritten by someone who knows what replaced it."""
    rule = _prose_rule(_procedures(project))
    assert contains_phrase(rule.enforced_by, THE_HOLE), rule.enforced_by

    # `dead because `kyo rebuild` already handles it` where the command printed a word:
    # ordinary text in an ordinary argument, indistinguishable from prose someone typed.
    for flag in sorted(RESIDUE_CASES):
        assert _treatment(kyo, write_file, flag, "dead because rebuild handles it") is None


# ------------- KYO-95: this repo's live ledger and the packaged default it came from
#
# Two copies of the standing-rules ledger exist here, and every test above reads only
# one of them. `src/kyosei/defaults/procedures.md` is the packaged default — what `kyo
# init` scaffolds, and therefore what the `project` fixture hands the tests above.
# `kyosei/procedures.md` is this repo's own live store copy, the one `kyo dispatch`
# injects into every worker's brief. Nothing loaded the second one, and nothing compared
# the two.
#
# That gap is structural rather than an oversight: `kyo check-branch` forbids a dispatch
# branch to write anything under `kyosei/`, so every rule change here is necessarily
# split in two — a worker edits the packaged default, and the controller applies the
# store copy by hand afterwards, at the end of a merge, with nothing behind it but care.
# That hand step ran twice in one day (KYO-86's rule and KYO-93's) with this file green
# either way, which is the same as saying the suite could not tell whether this
# project's own workers were being handed a stale rule.
#
# WHY THIS WAS A REPO TEST AND NOT A `kyo doctor` CHECK (KYO-95.c2), AND WHAT KYO-212
# CHANGED. KYO-95 declined to ship this comparison to consumers: a store copy is
# *supposed* to be able to diverge from the packaged default — `kyo init` copies the
# default in precisely so the project can then own it — and a check that failed every
# consumer who had edited their own rules would be worse than the drift it caught.
#
# What that reasoning missed, and KYO-212 found by asking how a rule added to the
# packaged default reaches a project that already has a store, is that "diverge" is two
# things. A store copy going BEYOND the default (a rule of its own, an edited rule) is
# the feature. A store copy falling BEHIND it is nothing but a rule that reaches nobody:
# KYO-199 added a worker-audience rule and neither consumer project could ever see it,
# because nothing read the packaged copy a second time. So KYO-212's consumer-facing
# checks (`kyo doctor`'s `procedures_rules_missing` and `procedures_rules_differ`)
# report exactly the two halves that are not the feature — a packaged rule the ledger
# lacks, and a rule whose text the two copies disagree about, the second with both
# sides quoted, nothing rewritten and, since KYO-214, in the notice tier rather than
# the failing one — and stay silent about a rule only the project carries.
# KYO-95.c2's own demonstration below is rewritten to that line rather than deleted,
# and still drives it rather than claiming it.
#
# What stays repo-only is the direction no consumer owes anything about: this
# repository's store copy carrying a rule the packaged default does not ship, which is
# either a deliberate local rule (named in `STORE_ONLY_RULES`) or a rule that was meant
# for every project and rotted here.
#
# WHAT "AGREE" MEANS HERE — decided, because the record left it open. Not "the two files
# are identical": this repo already carries two controller-audience rules the packaged
# default deliberately does not ship, earned during dogfooding and meaningless to a
# consumer who dispatches no workers. "Identical" would forbid a local rule forever, and
# the local rules already exist. So the comparison is rule-scoped: every rule the
# packaged default ships must appear in the store copy with the same content, and every
# rule the store copy carries beyond that must be named in `STORE_ONLY_RULES` below.
#
# A THIRD store-local rule appearing later is LISTED, not passed silently. Passing
# silently would let a rule that was meant for every project rot in this repo's copy
# alone — the exact mirror image of the drift this record is about, and invisible in
# exactly the same way. Listing it costs one line in `STORE_ONLY_RULES`, added by the
# same controller in the same commit as the hand-applied rule, and buys a moment where
# somebody has to say out loud that the new rule is deliberately local.
#
# THE COMPARISON IS OF RULES, NOT OF FILE LAYOUT. `## ...` section headings and `---`
# breaks sit *between* rules, so a naive "### to the next ###" split attributes them to
# whichever rule they happen to follow — and the two copies here do not agree about
# where they sit, because inserting a rule moves them. Today that alone would report two
# rules as different whose text is identical to the byte. A check that cries wolf on its
# first run is a check that gets ignored, so `procedures.rule_bodies` drops that scaffolding.
# Anything about *heading structure* is therefore out of scope here by choice, not by
# omission: it is a property of one file, not a disagreement between two.

# KYO-151: the two paths, spelled once in `kyosei.procedures` and bound here. The
# brief block that warns a dispatch about the hand-off is keyed off the first of them,
# so a rename that missed this file would leave the warning and the check disagreeing
# about which files they are talking about (law 3).
PACKAGED_REL = PACKAGED_LEDGER_SOURCE_PATH
STORE_REL = STORE_LEDGER_PATH

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGED_LEDGER = REPO_ROOT / PACKAGED_REL
STORE_LEDGER = store_path("procedures.md")

#: The store copy exists only in the maintainer's checkout, which carries this
#: repository's store; a public checkout has the packaged default alone. The tests that
#: compare the two copies run where both are present.
needs_store_ledger = pytest.mark.skipif(
    not STORE_LEDGER.is_file(),
    reason="this repository's own store copy of procedures.md is not in this checkout",
)

#: The rules this repo's store copy carries that the packaged default does not ship, and
#: is not meant to. The first two are controller-audience and about running a dispatch —
#: a consumer who never dispatches a worker has no use for either, and `kyo init` should
#: not scaffold rules that describe this project's own operating habits. The third
#: (KYO-354) is about filing criteria that edit `src/kyosei/defaults/skill.md`, a file
#: only this repository has. Adding to this set is how a *new* store-local rule stops
#: failing `ledger_differences`.
STORE_ONLY_RULES = frozenset(
    {
        # KYO-524: reworded controller -> orchestrator with the rest of the store copy.
        "A criterion asks for a skill edit only when the change alters what an orchestrator "
        "does at a step",
        "Declare the dispatch's branch with `kyo dispatch --branch`",
        "Quote the maintainer verbatim; never file their report as your own paraphrase",
    }
)

# KYO-212.c4 moved the comparison itself into `src/kyosei/procedures.py`, because
# `kyo doctor`'s two ledger checks and `kyo install-procedures` now ask the
# same question of a consumer's ledger that this file asks of ours, and three
# implementations of "what is a difference" would eventually be three answers. What
# stays here is this repository's *binding* of it: our two file paths, the allowlist of
# rules our store copy carries alone, and the sentence naming where that allowlist
# lives — which belongs here rather than in the tool, because `tests/test_procedures.py`
# is a path no installed consumer has (KYO-149/KYO-156).

STORE_ONLY_HINT = " Name it in STORE_ONLY_RULES in tests/test_procedures.py."


def ledger_differences(
    packaged: str, store: str, store_only: frozenset[str] = STORE_ONLY_RULES
) -> list[str]:
    """:func:`kyosei.procedures.ledger_differences`, bound to this repo's two copies."""
    return procedures_ledger_differences(
        packaged,
        store,
        store_only,
        packaged_label=PACKAGED_REL,
        store_label=STORE_REL,
        store_only_hint=STORE_ONLY_HINT,
    )


#: The phrase a KYO-167 preamble finding is rendered with. Pinned by
#: :func:`test_a_preamble_edit_in_one_copy_alone_is_reported`, so the filter below cannot
#: quietly stop matching and start hiding rule findings instead.
PREAMBLE_FINDING = "disagree ABOVE the first rule"


def rule_differences(
    packaged: str, store: str, store_only: frozenset[str] = STORE_ONLY_RULES
) -> list[str]:
    """:func:`ledger_differences`, restricted to the findings about RULES.

    KYO-167 gave the comparison a second dimension: the two copies are now also held
    together above the first `###`, where they were free to drift. Each test below is
    about exactly one rule moving and asserts that nothing ELSE is reported — so a
    live, unrelated preamble finding in the same list would turn every one of them
    into an assertion about the state of the preamble as a side effect, and they would
    all go red together for one reason none of them is about.

    The whole-file question stays exactly as strict as it was: it is
    :func:`test_this_repos_store_ledger_agrees_with_the_packaged_default`'s, it uses
    :func:`ledger_differences`, and a preamble that has come apart fails it.
    """
    return [
        difference
        for difference in ledger_differences(packaged, store, store_only)
        if PREAMBLE_FINDING not in difference
    ]


@needs_store_ledger
def test_this_repos_store_ledger_agrees_with_the_packaged_default() -> None:
    """KYO-95.c1. The check the split between worker and controller left missing: the
    rule text this project's own briefs are built from, compared against the rule text
    this build ships, so a hand-application that was skipped or mistyped fails here
    instead of reaching a worker."""
    differences = ledger_differences(
        PACKAGED_LEDGER.read_text(encoding="utf-8"),
        STORE_LEDGER.read_text(encoding="utf-8"),
    )
    assert not differences, "\n".join(differences)


@needs_store_ledger
def test_the_ledger_comparison_reads_real_rules_out_of_both_files() -> None:
    """The canary the whole of c1 rests on. Normalization that ate too much would leave
    every body empty, every comparison trivially true, and the check green forever — so
    prove both sides parse to the same rule set the shipped parser sees (which also
    rules out two rules sharing a title, where the dict would keep only the last), and
    prove each body still carries its bullets and its justifying prose."""
    for path in (PACKAGED_LEDGER, STORE_LEDGER):
        text = path.read_text(encoding="utf-8")
        bodies = rule_bodies(text)
        parsed = parse_procedures(text)
        assert len(bodies) == len(parsed), path
        assert set(bodies) == {rule.title for rule in parsed}, path
        for title, body in bodies.items():
            assert "**audience**" in body, (path, title)
            assert "**enforced-by**" in body, (path, title)
            # The bullets, then at least one paragraph of justifying prose — the
            # part law 15 keeps in this file for whoever wants the "why", and the
            # part a too-eager normalization would be likeliest to eat.
            assert len(body.splitlines()) >= 2, (path, title)
            assert not body.splitlines()[-1].startswith("-"), (path, title)


@needs_store_ledger
def test_the_store_only_rules_are_exactly_the_ones_declared() -> None:
    """The other half of c1, and the answer to the question the record left open. The two
    copies are compared rule-by-rule rather than byte-for-byte *because* this repo keeps
    local rules; the price of that is that the set of them is declared here rather than
    inferred, so it cannot quietly grow."""
    packaged = rule_bodies(PACKAGED_LEDGER.read_text(encoding="utf-8"))
    store = rule_bodies(STORE_LEDGER.read_text(encoding="utf-8"))
    assert set(store) - set(packaged) == set(STORE_ONLY_RULES)
    assert set(packaged) < set(store), "the packaged default ships a rule the store lacks"


@needs_store_ledger
def test_a_third_store_local_rule_is_listed_rather_than_passing_silently() -> None:
    """The decision above, demonstrated. A rule added to this repo's store copy and to
    nothing else fails until somebody names it — because the alternative is a rule that
    was meant for every project sitting in one repo's copy, unnoticed, which is this
    record's own defect pointing the other way."""
    (difference,) = rule_differences(
        PACKAGED_LEDGER.read_text(encoding="utf-8"),
        STORE_LEDGER.read_text(encoding="utf-8") + "\n" + MULTI_LINE_RULE,
    )
    assert "A rule whose prose soft-wraps" in difference
    assert "STORE_ONLY_RULES" in difference


@needs_store_ledger
def test_an_allowlisted_rule_that_left_the_store_copy_is_reported() -> None:
    """Law 2 applied to the allowlist itself: an entry naming a rule nothing carries any
    more is a permission granted to nobody, and it would go on granting it to the next
    rule that happened to reuse the title."""
    (difference,) = rule_differences(
        PACKAGED_LEDGER.read_text(encoding="utf-8"),
        STORE_LEDGER.read_text(encoding="utf-8"),
        store_only=STORE_ONLY_RULES | {"A rule this repo stopped carrying"},
    )
    assert "A rule this repo stopped carrying" in difference
    assert "no longer carries" in difference


# ------------------------------------------------- KYO-95.c3: the check, demonstrated

#: The prose-as-argument rule as it stood *before* KYO-93 rewrote its `enforced-by` line,
#: copied byte for byte from the packaged procedures.md of that time; its digest is in
#: :data:`kyosei.procedures.SUPERSEDED_RULE_WORDINGS`, so not one word of it may change.
#: Frozen history rather than a claim about the present, so it cannot rot the way a
#: restatement of current text would, and c3 asks for the check to be shown biting on a
#: real revert of a real rule rather than on a fabricated one.
PRE_KYO93_PROSE_RULE = """\
### Prose enters the store only via stdin or a file; the one-line text flags are the exception

- **audience**: worker
- **enforced-by**: mechanism, partial — multi-line prose has no flag-value form at all: body
  text (`edit`, `claim`, `verify`, `note`, `report`, ...) enters only through a `--*-file`
  flag or stdin. The exception is the one-line text flags, which do take prose as a shell
  argument — `--do`, `--method`, `--title`, `--why` — and each refuses a value containing a
  newline; `--why` also refuses an unbalanced backtick or quote and names `--why-file`
  instead (KYO-26). A *balanced* substitution the shell ate before `kyo` was invoked is
  still invisible, so this is an open hole, not a closed one. What is mechanical is that it
  cannot widen unannounced: `tests/test_procedures.py` compares the flags listed here
  against the ones the parser actually offers (KYO-86).

Shell interpolation silently deleted words mid-sentence three times in one session of the
predecessor project, and reported success each time (Law 5). Never construct a `kyo` command
whose prose argument was built by string-interpolating a shell variable, and when a reason
runs longer than a phrase, write it to a file and pass `--why-file` — the path the shell
does not touch.

"""

#: One distinctive phrase from each side of KYO-93's rewrite, used to prove the failure
#: message points at the copy that actually moved. Neither appears in the other version.
PRE_KYO93_WORDING = "unbalanced backtick or quote and names"
POST_KYO93_WORDING = "residue a shell substitution leaves"


def _with_the_prose_rule_reverted(text: str) -> str:
    """``text`` with KYO-93's rewrite of the prose-as-argument rule undone."""
    heading = PRE_KYO93_PROSE_RULE.split("\n", 1)[0]
    start = text.index(heading)
    end = text.index("\n### ", start) + 1
    reverted = text[:start] + PRE_KYO93_PROSE_RULE + text[end:]
    assert reverted != text, "the revert changed nothing; KYO-93's rule is already reverted"
    return reverted


def _with_the_prose_rule_reworded(text: str) -> str:
    """``text`` with the prose rule on its pre-KYO-93 wording AND edited by hand.

    KYO-515: the bare revert is a wording an earlier build really shipped, which a
    plain `kyo install-procedures` now moves to the packaged wording because nobody in
    the project wrote it. A test about a rule the PROJECT reworded needs text no build
    ever shipped, so this adds a project's own words to the old wording.
    """
    reverted = _with_the_prose_rule_reverted(text)
    edited = reverted.replace(
        "reported success each time (Law 5).",
        "reported success each time (Law 5), as this project reads it.",
        1,
    )
    assert edited != reverted, "the hand edit changed nothing"
    return edited


@needs_store_ledger
@pytest.mark.parametrize("reverted_copy", ["packaged", "store"])
def test_reverting_kyo93s_rule_in_one_copy_alone_fails_the_check(reverted_copy: str) -> None:
    """KYO-95.c3, and on the real files rather than a fixture — the check has to bite on
    the shape the drift actually takes here, which is one of two hand-maintained copies
    moving. Both directions, because the hand step can be skipped (the store copy stays
    behind) or applied to the wrong text (the store copy moves alone), and a comparison
    that only looked one way would catch one of those."""
    packaged = PACKAGED_LEDGER.read_text(encoding="utf-8")
    store = STORE_LEDGER.read_text(encoding="utf-8")
    if reverted_copy == "packaged":
        packaged = _with_the_prose_rule_reverted(packaged)
    else:
        store = _with_the_prose_rule_reverted(store)

    (difference,) = rule_differences(packaged, store)
    assert PROSE_RULE_TITLE in difference

    lines = difference.splitlines()
    quoted = {
        PACKAGED_REL: next(ln for ln in lines[1:] if ln.strip().startswith(PACKAGED_REL)),
        STORE_REL: next(ln for ln in lines[1:] if ln.strip().startswith(STORE_REL)),
    }
    moved = PACKAGED_REL if reverted_copy == "packaged" else STORE_REL
    intact = STORE_REL if reverted_copy == "packaged" else PACKAGED_REL

    # The direction is shown, not merely claimed: the copy that moved is quoted saying
    # the old thing, and the copy that did not is quoted saying KYO-93's.
    assert PRE_KYO93_WORDING in quoted[moved], quoted[moved]
    assert POST_KYO93_WORDING in quoted[intact], quoted[intact]


def test_a_consumers_own_extra_rule_is_never_a_doctor_finding(kyo, project: Path) -> None:
    """KYO-95.c2's surviving half, demonstrated rather than asserted, and now the line
    KYO-212 draws instead of the one KYO-95 drew. A consumer's `kyosei/procedures.md` is
    theirs to go BEYOND the packaged default — that is what a project-editable ledger is
    for — so a rule only they carry is not a finding, and nothing this repo says about
    its own local rules (`STORE_ONLY_RULES`, and the repo-only direction of the
    comparison) is imposed on them as a public contract."""
    ledger = project / "kyosei" / "procedures.md"
    ledger.write_text(ledger.read_text(encoding="utf-8") + "\n" + MULTI_LINE_RULE, encoding="utf-8")

    # This repo's own binding does report it — it has an allowlist, and a consumer has
    # none — which is exactly the asymmetry being demonstrated.
    (difference,) = ledger_differences(
        PACKAGED_LEDGER.read_text(encoding="utf-8"),
        ledger.read_text(encoding="utf-8"),
        store_only=frozenset(),
    )
    assert "A rule whose prose soft-wraps" in difference

    result = kyo("doctor")
    assert result.code == 0, result
    assert "A rule whose prose soft-wraps" not in result.out + result.err
    assert PACKAGED_REL not in result.out + result.err


def test_a_consumers_edited_rule_is_reported_and_never_rewritten(kyo, project: Path) -> None:
    """The half KYO-212 reversed, and the reversal's whole content: a rule whose text the
    two copies disagree about IS reported now — because the alternative was KYO-199's
    rule reaching nobody — and is still never rewritten by anything. The consumer's own
    words survive `kyo install-procedures` verbatim, and doctor goes on saying so until a
    person settles it, which is KYO-212.c3.

    KYO-214's tier is what makes "goes on saying so" survivable: this is a NOTICE, so it
    is printed in full on every run and exits 0. Failing here would have asked a project
    to undo its own considered wording or read a red doctor forever — the state this
    dispatch's report flagged and that record answered."""
    ledger = project / "kyosei" / "procedures.md"
    edited = _with_the_prose_rule_reworded(ledger.read_text(encoding="utf-8"))
    ledger.write_text(edited, encoding="utf-8")

    result = kyo("doctor")
    assert result.code == 0, "a rewording is a notice, not a failure"
    printed = result.out
    assert PROSE_RULE_TITLE in printed
    assert PRE_KYO93_WORDING in printed, "the consumer's own text is quoted back to them"
    assert POST_KYO93_WORDING in printed, "and so is the packaged text it disagrees with"
    assert "information, not a failure" in printed

    assert kyo("install-procedures").code == 0
    assert ledger.read_text(encoding="utf-8") == edited, "an edited rule is never rewritten"
    again = kyo("doctor", "--json")
    assert again.code == 0, "and it never starts failing either"
    assert any(
        PROSE_RULE_TITLE in notice for notice in again.json["notices"]
    ), "it stays reported until a person edits one copy"


# ---------- KYO-97: the store-write rule names the command that actually guards
#
# The widest-blast-radius rule in the ledger — the one that keeps a worker out of the
# store, injected verbatim into every dispatch brief — said under `enforced-by:
# mechanism` that "`kyo doctor` and a merge guard fail a branch that diffs anything
# under `kyosei/`". `kyo doctor` has never had a branch check: `src/kyosei/doctor.py`
# imports nothing from `git_queries` and invokes git nowhere. The guard that does this
# is `kyo check-branch` (KYO-13.c5), whose own docstring says it was written to make
# *this sentence* mechanical — and the sentence was never updated to name it. Third
# instance in one day of an `enforced-by` line describing a mechanism that does not
# exist as stated (KYO-86, KYO-93, both on the prose-argument rule).
#
# So the attribution is driven rather than proof-read: every `kyo` command the line
# names is run against a branch that really does diff the store, and has to fail it.
# Naming `kyo doctor` there fails this file, which is the state the record was filed in.
#
# WHAT THIS DOES NOT COVER — stated because a check that oversells its reach is the
# defect this record is about:
#
#   * ONE RULE. Only "Never write to the structured store directly". Every other
#     `enforced-by` line in the ledger may name any command it likes and nothing here
#     looks. That is deliberate and it is KYO-93's precedent: a general `enforced-by`
#     verifier is a real idea and a different, larger record. This one is a single
#     false attribution in the highest-traffic rule in the file.
#   * ONE CHECK. The commands are driven against the branch-diff claim only. A line
#     naming a command that exists and guards branches, while attributing some *other*
#     property to it, passes here.
#   * THE PROSE. Only the `enforced-by` bullet is scanned for command names, not the
#     justifying paragraph — which is where this rule now records that `kyo doctor`
#     never had the check, and a paragraph that said the opposite would not fail here.
#   * THE OTHER HALF OF THE CLAIM. "A worker's worktree holds no copy of
#     kyosei/kyosei.db" is true by construction under the split layout and true in an
#     in-repo project only because `kyo init` gitignores the database. Neither is
#     driven from here; `tests/test_check_branch.py` and `tests/test_split_store.py`
#     own those. What is checked below is that the line still *distinguishes* them.
#   * THE STORE COPY. Like every rule-content test above, this reads the packaged
#     default. That it also holds for `kyosei/procedures.md` — the copy this repo's
#     own briefs are built from — is carried by KYO-95's rule-by-rule comparison in
#     the section above, not asserted a second time here.

STORE_RULE_TITLE = "Never write to the structured store directly"

#: The rule exactly as it stood before this record, copied from the packaged
#: procedures.md of that time. Frozen history, so the canary below bites on the
#: real misattribution rather than a fabricated one — the same discipline as
#: :data:`PRE_KYO93_PROSE_RULE`.
PRE_KYO97_STORE_RULE = """\
### Never write to the structured store directly

- **audience**: worker
- **enforced-by**: mechanism — a worker's worktree holds no copy of `kyosei/kyosei.db`; `kyo
  doctor` and a merge guard fail a branch that diffs anything under `kyosei/`

You cannot move a record, check a criterion, or edit `kinds.yml`/`procedures.md` from a
dispatch.
"""

#: What a worker writing to the store looks like as a diff: a file under `kyosei/` that
#: no `kyo` command would have produced. Deliberately not one of the store's own
#: artifacts — an edited `kinds.yml` or a hand-written export would give `kyo doctor` an
#: unrelated reason to exit non-zero, and the whole question here is whether a command
#: fails *because of the branch*.
INTRUDING_PATH = "kyosei/a-worker-should-not-have-written-this.md"


def _store_rule(text: str) -> Rule:
    matches = [r for r in worker_rules(text) if contains_phrase(r.title, STORE_RULE_TITLE)]
    assert len(matches) == 1, [r.title for r in worker_rules(text)]
    return matches[0]


def _kyo_commands_named_by(rule: Rule) -> set[str]:
    """Every ``kyo <subcommand>`` the rule's ``enforced-by`` line names.

    ``Rule.enforced_by`` arrives whitespace-collapsed, which is what makes this safe:
    the bullet soft-wraps, and the pre-KYO-97 line wrapped *inside* the backticks
    (```kyo\\n  doctor```). A pattern read off the raw markdown would have found no
    command at all in the very text this check exists to catch (KYO-66).
    """
    return set(re.findall(r"`kyo ([a-z][a-z-]*)`", rule.enforced_by))


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project, check=True, capture_output=True, text=True)


def _every_named_command_guards_the_branch(kyo, git_project: Path, text: str) -> None:
    """KYO-97.c3 as a helper, so the canary can assert it *fails* on the old wording."""
    named = _kyo_commands_named_by(_store_rule(text))
    assert named, "the enforced-by line names no `kyo` command at all; nothing to check"

    for command in sorted(named):
        clean = kyo(command)
        assert clean.code == 0, (
            f"the rule names `kyo {command}`, which does not even succeed on a branch "
            f"that leaves the store alone: {clean}"
        )

    _git(git_project, "checkout", "-q", "-b", "worker-touched-the-store")
    (git_project / INTRUDING_PATH).write_text("a worker's note, in the store\n", encoding="utf-8")
    _git(git_project, "add", "-A")
    _git(git_project, "commit", "-q", "-m", "a worker writing to the structured store")

    for command in sorted(named):
        result = kyo(command)
        assert result.code != 0, (
            f"the rule's enforced-by line says `kyo {command}` fails a branch that diffs "
            f"anything under kyosei/; this branch diffs {INTRUDING_PATH} and `kyo "
            f"{command}` exited 0. Name the command that does it, or drop the claim"
        )
        assert INTRUDING_PATH in result.out + result.err, (
            f"`kyo {command}` failed, but not about {INTRUDING_PATH} — it is failing for "
            f"some other reason, which is not evidence that it guards the store"
        )


def test_every_kyo_command_the_store_rule_names_fails_a_store_touching_branch(
    kyo, git_project: Path, project: Path
) -> None:
    """KYO-97.c1 and c3. The line's attribution is driven, not read: each command it
    names is run against a branch that really diffs `kyosei/`, and must fail it and say
    which path. `kyo doctor` cannot pass this, which is the whole point — it is what the
    line named for a day while `kyo check-branch` did the work."""
    _every_named_command_guards_the_branch(kyo, git_project, _procedures(project))


def test_that_check_bites_on_the_misattribution_it_was_filed_about(
    kyo, git_project: Path
) -> None:
    """KYO-97.c3's other half. A check whose failing case is never demonstrated is a
    check nobody knows the reach of — so the pre-KYO-97 rule, quoted from history, goes
    through the same helper and has to fail, naming the command it wrongly credited."""
    with pytest.raises(AssertionError) as raised:
        _every_named_command_guards_the_branch(kyo, git_project, PRE_KYO97_STORE_RULE)
    assert "`kyo doctor` exited 0" in str(raised.value), raised.value


def test_the_store_rule_names_check_branch_and_the_hook_that_invokes_it(project: Path) -> None:
    """KYO-97.c1. The command is half the answer: a worker reasoning about what actually
    stops it needs to know *when* the guard runs, and a guard that runs at stop time is a
    materially different promise from one that runs at write time. The hook name is
    checked against the packaged fragment rather than against itself, so renaming the
    event breaks this rather than leaving the rule quietly wrong."""
    rule = _store_rule(_procedures(project))
    assert contains_phrase(rule.enforced_by, "`kyo check-branch`")
    assert not contains_phrase(rule.enforced_by, "`kyo doctor`")

    events = json.loads(
        (defaults_dir() / "hooks" / "subagent-stop.json").read_text(encoding="utf-8")
    )
    (event,) = events
    assert contains_phrase(rule.enforced_by, f"`{event}` hook")
    installed = [
        hook["command"]
        for group in events[event]
        for hook in group.get("hooks", [])
    ]
    assert any("kyo check-branch" in command for command in installed), installed


def test_the_store_rule_separates_construction_from_enforcement(project: Path) -> None:
    """KYO-97.c2. The old line conflated two guarantees into one `mechanism` claim: "a
    worker's worktree holds no copy of kyosei/kyosei.db" (true by construction under the
    split layout, and true in an in-repo project only because the database is gitignored)
    and "fails a branch that diffs anything under kyosei/" (a command). The split
    layout made those two different guarantees for two consumer layouts, so the line has to say
    which is which and for whom — and say `partial`, because neither covers a write made
    outside git."""
    rule = _store_rule(_procedures(project))
    assert rule.enforced_by.startswith("mechanism, partial")
    for claim in (
        "By construction, under the split layout",
        "By a command, under the in-repo layout",
        "holds no copy of `kyosei/kyosei.db`",
        "gitignored",
    ):
        assert contains_phrase(rule.enforced_by, claim), (claim, rule.enforced_by)


def test_the_rewritten_store_rule_still_parses_and_still_carries_its_reason(
    project: Path,
) -> None:
    """The KYO-86 lesson, applied to this rewrite: a rule reshaped into something
    `kyosei.procedures` cannot parse reaches nobody while looking like it reaches
    someone (KYO-27), and the first sentence of the prose is the only "why" a worker
    ever sees."""
    rule = _store_rule(_procedures(project))
    assert rule.audience == "worker"
    assert contains_phrase(rule.first_line, "You cannot move a record, check a criterion")


# ------------- KYO-132: the dispatch-economy defaults, and the one that has a mechanism
#
# Three rules ship here together because they are one policy: a worker runs the tests
# covering its own change, the controller runs the suite once on the merged tree, and
# records whose reading is the same reading go to one worker. A consumer project
# reported that the DEFAULT was wrong rather
# than the tool — the mechanisms were all already built, and a fresh consumer was told
# nothing about any of them, so every controller independently chose the expensive
# option and every worker independently ran the whole suite.
#
# WHAT IS DRIVEN HERE AND WHAT IS NOT, said plainly because a rule about test economy
# that oversold its own enforcement would be the joke it deserves to be:
#
#   * PRESENCE AND AUDIENCE are driven — each rule is located in the packaged default,
#     its audience is asserted, and it is watched arriving on the surface that audience
#     actually reads (a frozen dispatch brief for the worker rule, `kyo brief` for the
#     two controller rules). A rule that parses but names the wrong audience reaches
#     nobody while looking like it reaches someone (KYO-27).
#   * THE `enforced-by` REGISTER is driven, one rule at a time, against what the tool
#     can actually do: `mechanism, partial` for the two that have machinery behind
#     them, `aspirational` verbatim for the one that does not. Kyosei never runs a
#     project's tests and never will, so the controller-suite rule cannot claim more.
#   * THE COUNT IN THE BRIEF is driven end to end (KYO-132.c5) — the fact that turns
#     "run only your own tests" from a standing exhortation into something true of this
#     dispatch. This is the mechanism the worker rule's `enforced-by` line names, so it
#     is checked from the rule rather than only from `tests/test_dispatch.py`, the same
#     way KYO-86/KYO-93/KYO-97 check the claims their rules make.
#   * WHAT NOBODY CHECKS: whether a worker ran the right tests, whether a controller
#     ran the suite, and whether a grouping was the right grouping. None of the three
#     is observable from inside the store, and each rule says so in its own line.

TESTS_RULE_TITLE = "Run the tests that cover your change, not the whole suite"
SUITE_RULE_TITLE = "Run the full suite yourself, once, on the merged tree"
GROUPING_RULE_TITLE = (
    "Group records into one dispatch when a worker would otherwise ingest the same "
    "context twice"
)

#: ``(title, audience)`` for the three rules KYO-132 ships. Parametrized over rather
#: than written out three times, so a fourth rule added to this policy costs one line
#: and cannot be added without an audience.
DISPATCH_ECONOMY_RULES = [
    (TESTS_RULE_TITLE, "worker"),
    (SUITE_RULE_TITLE, "orchestrator"),
    (GROUPING_RULE_TITLE, "orchestrator"),
]


def _rule_titled(text: str, title: str) -> Rule:
    matches = [r for r in parse_procedures(text) if contains_phrase(r.title, title)]
    assert len(matches) == 1, (title, [r.title for r in parse_procedures(text)])
    return matches[0]


def _rule_body(text: str, title: str) -> str:
    """One rule's whole section, heading excluded — bullets and justifying prose.

    ``Rule.first_line`` is only the first sentence (it is all a brief carries), and the
    content these rules are judged on lives further down: the three grouping modes, the
    reason a pre-merge green answers nothing, the hedge on the one unmeasured claim.
    """
    start = text.index(f"### {title}") + len(f"### {title}")
    rest = text[start:]
    ends = [i for i in (rest.find("\n### "), rest.find("\n---\n")) if i != -1]
    return rest[: min(ends)] if ends else rest


def _without_rule(text: str, title: str) -> str:
    """``text`` with one ``### `` rule section cut out, for the canary below.

    The cut runs to the next rule heading or the next section break, whichever comes
    first — a rule that happens to be last in its section is followed by ``---``, not by
    another ``###``, and cutting to end-of-file there would delete every rule after it
    and pass this file for entirely the wrong reason.
    """
    start = text.index(f"### {title}")
    rest = text[start:]
    ends = [i for i in (rest.find("\n### ", 1), rest.find("\n---\n", 1)) if i != -1]
    end = start + (min(ends) + 1 if ends else len(rest))
    stripped = text[:start] + text[end:]
    assert stripped != text, title
    assert f"### {title}" not in stripped, title
    return stripped


def _the_rule_is_shipped_and_reaches_its_audience(
    kyo, git_project: Path, title: str, audience: str
) -> None:
    """KYO-132.c6 as a helper, so the canary can assert it *fails* on a stripped ledger.

    Read from the scaffolded project's ledger, which is `kyo init`'s copy of
    `src/kyosei/defaults/procedures.md` — the packaged default the criteria name, and the
    same file every rule-content test above reads.
    """
    text = (git_project / "kyosei" / "procedures.md").read_text(encoding="utf-8")
    rule = _rule_titled(text, title)
    assert audience in rule.audience, (title, rule.audience)
    assert rule.first_line, f"{title!r} has no justifying prose, so its brief line is empty"

    if audience == "worker":
        record = new_record(kyo, criteria=["the suite is somebody's job"])
        result = kyo("--full", "dispatch", record, "--json")
        assert result.code == 0, result
        surface = result.json["record"]["dispatches"][0]["brief"]
    else:
        result = kyo("brief")
        assert result.code == 0, result
        surface = result.out
    assert contains_phrase(surface, title), (title, audience)


@pytest.mark.parametrize(("title", "audience"), DISPATCH_ECONOMY_RULES)
def test_each_dispatch_economy_rule_ships_and_reaches_its_audience(
    kyo, git_project: Path, title: str, audience: str
) -> None:
    """KYO-132.c1/c2/c3/c6. Presence is half of it; the other half is that the rule
    lands where its audience looks, which is the only thing law 15 promises and the
    thing a wrong `audience:` token silently breaks."""
    _the_rule_is_shipped_and_reaches_its_audience(kyo, git_project, title, audience)


@pytest.mark.parametrize(("title", "audience"), DISPATCH_ECONOMY_RULES)
def test_removing_a_dispatch_economy_rule_fails_this_file(
    kyo, git_project: Path, title: str, audience: str
) -> None:
    """KYO-132.c6, demonstrated rather than claimed. Cut the rule out of the ledger the
    project actually dispatches from and watch the check above fail — a test whose
    failing case is never shown is a test nobody knows the reach of (KYO-97's lesson)."""
    ledger = git_project / "kyosei" / "procedures.md"
    ledger.write_text(_without_rule(ledger.read_text(encoding="utf-8"), title), encoding="utf-8")
    with pytest.raises(AssertionError):
        _the_rule_is_shipped_and_reaches_its_audience(kyo, git_project, title, audience)


def test_the_worker_test_rule_claims_the_mechanism_it_actually_has(project: Path) -> None:
    """KYO-132.c1/c4. `mechanism, partial` and not `mechanism`: `kyo dispatch` really
    does state the live-dispatch count in the frozen brief, which is what makes the rule
    a fact about this dispatch — and nothing whatsoever observes which tests a worker
    then ran, which is the half that keeps it `partial`."""
    rule = _rule_titled(_procedures(project), TESTS_RULE_TITLE)
    assert rule.enforced_by.startswith("mechanism, partial")
    assert contains_phrase(rule.enforced_by, "`store.running_dispatches()`")
    assert contains_phrase(rule.enforced_by, "tests/test_procedures.py")
    # The admission, in the same breath as the claim (law 1).
    assert contains_phrase(rule.enforced_by, "nothing observes which tests you actually ran")


def test_the_worker_test_rule_gives_the_reason_that_holds_without_an_exclusive_resource(
    project: Path,
) -> None:
    """KYO-132.c1. The consumer's Electron/keyboard-focus case is vivid and is the
    narrower half: a project with no port, display, database or device still must not
    have every worker run the suite, because a pre-merge green on one branch cannot
    predict what a merge produces. A rule that led with the exclusive resource would
    read as inapplicable to most projects, which is the opposite of generic."""
    body = " ".join(_rule_body(_procedures(project), TESTS_RULE_TITLE).split())
    assert "before every other worker's merge" in body
    assert "merge to red" in body
    assert "exclusive resource" in body
    # Generic: the rule says who runs what and why, and leaves the command alone —
    # what a suite costs and what it holds are things only the consumer knows.
    assert "only your project knows" in body


def test_the_controller_suite_rule_says_aspirational_verbatim(project: Path) -> None:
    """KYO-132.c2/c4, and law 1. Kyosei does not run tests, does not know what this
    project's suite is, and cannot tell whether a controller ran it — so `mechanism,
    partial` here would be exactly the overstatement `enforced-by` exists to prevent.
    The word is asserted verbatim because it is the whole content of the claim."""
    rule = _rule_titled(_procedures(project), SUITE_RULE_TITLE)
    assert rule.enforced_by.startswith("aspirational")
    assert contains_phrase(rule.enforced_by, "never runs a project's tests")


def test_the_controller_suite_rule_states_why_a_pre_merge_green_cannot_answer_it(
    project: Path,
) -> None:
    """KYO-132.c2. "Run the suite" without the reason is a chore; with the reason it is
    the only run that predicts what ships. The reason is also what stops a controller
    reading the worker rule as a lowering of the standard rather than a relocation."""
    body = " ".join(_rule_body(_procedures(project), SUITE_RULE_TITLE).split())
    assert "two green branches can merge to red" in body
    assert "only you ever hold" in body
    assert "relocation" in body


def test_the_grouping_rule_keys_on_context_ingest_and_not_on_touch_overlap(
    project: Path,
) -> None:
    """KYO-132.c3, and the correction made to this record's first
    reading of it. "Related records" is unfalsifiable and is NOT the test; whether a
    worker would otherwise ingest the same context twice is, and it is an observable
    cost. Touch overlap is one signal of it and the narrower one — two records in the
    same subsystem can need identical reading with no file in common at all — so a rule
    keyed on file overlap would miss most of what this is for."""
    body = " ".join(_rule_body(_procedures(project), GROUPING_RULE_TITLE).split())
    assert "ingest the same context twice" in body
    assert "no file in common" in body
    # Named and rejected in the rule itself, because the wrong test is the tempting one.
    assert "unfalsifiable" in body
    assert "never by topic" in body


def test_the_grouping_rule_distinguishes_all_three_modes(project: Path) -> None:
    """KYO-132.c3. A rule naming only `--also-covers` sends a controller to the wrong
    instrument for two of the three cases: an informal grouping changes no store object,
    and the serial case is not a grouping at all but the shape the disjointness guard
    leaves available. The three are asserted by their labels, so collapsing two of them
    back into one fails here."""
    body = " ".join(_rule_body(_procedures(project), GROUPING_RULE_TITLE).split())
    assert "**Formal**" in body and "`kyo dispatch --also-covers <id>`" in body
    assert "**Informal**" in body and "No store object changes" in body
    assert "**Serial**" in body and "cannot be dispatched concurrently" in body


def test_the_grouping_rule_makes_no_efficiency_claim_for_the_serial_case(
    project: Path,
) -> None:
    """KYO-132.c3/c4, and the hardest constraint on this rule. Nobody has measured
    whether serialising same-file records into one worker is efficient, and the
    impression that grouping "seemed" to make development faster was about the practice
    as a whole and does not transfer. So the serial bullet may say what the guard leaves
    available and nothing more — and the provenance of the claim that IS made keeps its
    hedge, rather than being promoted into a finding by the act of writing it down."""
    body = " ".join(_rule_body(_procedures(project), GROUPING_RULE_TITLE).split())
    assert "not a claim that serialising is cheaper" in body
    assert "no measurement of its efficiency" in body
    # The hedge, kept verbatim in the register it arrived in.
    assert "*seemed*" in body
    assert "no measurement behind the combined effect" in body


def test_the_grouping_rule_names_a_flag_the_cli_actually_offers(kyo, project: Path) -> None:
    """KYO-132.c4. `--also-covers` is the one piece of machinery this rule credits, and
    a rule crediting a flag that does not exist is the KYO-86 defect in a new place — so
    the flag is read off the real command rather than proof-read out of the rule."""
    rule = _rule_titled(_procedures(project), GROUPING_RULE_TITLE)
    assert rule.enforced_by.startswith("mechanism, partial")
    assert contains_phrase(rule.enforced_by, "`kyo dispatch --also-covers <id>`")
    helped = kyo("dispatch", "--help")
    assert helped.code == 0, helped
    assert "--also-covers" in helped.out, helped.out


def test_the_grouping_rule_admits_what_no_mechanism_reaches(project: Path) -> None:
    """KYO-132.c4's other half. The instruments exist; the judgment they serve is not
    checkable by anything, and the line has to say so or it claims more than it has."""
    rule = _rule_titled(_procedures(project), GROUPING_RULE_TITLE)
    assert contains_phrase(rule.enforced_by, "nothing measures context-ingest cost")


# ---------------------------------- KYO-132.c5: the count, driven end to end


def test_a_brief_frozen_beside_other_dispatches_states_how_many(
    kyo, git_project: Path, tmp_path: Path, write_file
) -> None:
    """KYO-132.c5. The worker asked to restrain itself is the one party who cannot see
    the other dispatches; `kyo dispatch` can, because it computes exactly this set for
    the disjointness check. Naming the live dispatches is what makes the standing rule a
    fact about this dispatch rather than an exhortation."""
    first_record = _scoped(kyo, git_project, write_file, "first", "src/one.py\n")
    second_record = _scoped(kyo, git_project, write_file, "second", "src/two.py\n")

    first = kyo("dispatch", first_record, "--json")
    assert first.code == 0, first
    second = kyo("--full", "dispatch", second_record, *_elsewhere(tmp_path, "second"), "--json")
    assert second.code == 0, second

    brief = second.json["record"]["dispatches"][0]["brief"]
    assert "**Other dispatches running**: at least 1 had been created when this brief" in brief
    assert first.json["dispatch_id"] in brief
    assert first_record in brief
    # The consequence, not just the number — the count exists to be acted on.
    assert "leave the whole suite to your orchestrator" in brief


def test_a_lone_dispatchs_brief_reads_as_an_absence_of_evidence(
    kyo, git_project: Path, write_file
) -> None:
    """KYO-132.c5's other half, as KYO-145 corrected it (c3). The bullet is still stated
    either way — silence would be worse — but "none" no longer asserts that the worker is
    alone. It could not: this brief was frozen before the rest of its own wave existed,
    and on this store's wave 3 the first of five briefs said "none" while four siblings
    were about to be spawned."""
    record = _scoped(kyo, git_project, write_file, "lonely", "src/lonely.py\n")
    result = kyo("--full", "dispatch", record, "--json")
    assert result.code == 0, result

    brief = result.json["record"]["dispatches"][0]["brief"]
    assert "**Other dispatches running**: none had been created when this brief" in brief
    assert "absence of evidence" in brief
    assert "no conclusion about contention follows from it" in brief
    # And the restraint still arrives, because it never depended on the count.
    assert "leave the whole suite to your orchestrator" in brief


def test_the_count_is_of_running_dispatches_and_drops_when_one_closes(
    kyo, git_project: Path, tmp_path: Path, write_file
) -> None:
    """The canary on the number itself. A count that only ever grew — every dispatch
    this project ever made, say — would still read as a plausible sentence in every
    brief while telling a lone worker it was contending with a shift that ended days
    ago, and the assertions above cannot tell the two apart."""
    first_record = _scoped(kyo, git_project, write_file, "first", "src/one.py\n")
    second_record = _scoped(kyo, git_project, write_file, "second", "src/two.py\n")

    first = kyo("dispatch", first_record, "--json")
    assert first.code == 0, first
    closed = kyo(
        "dispatch", "close", first.json["dispatch_id"],
        "--terminated-no-report", "--why", "the shift ended",
    )
    assert closed.code == 0, closed

    second = kyo("--full", "dispatch", second_record, *_elsewhere(tmp_path, "second"), "--json")
    assert second.code == 0, second
    brief = second.json["record"]["dispatches"][0]["brief"]
    assert "**Other dispatches running**: none had been created when this brief" in brief
    assert first.json["dispatch_id"] not in brief


# ---- KYO-212: a rule added to the packaged default reaching a project that already has a store
#
# The gap KYO-95 left open, found by asking a question KYO-199 made
# urgent: `kyo init` scaffolds `kyosei/procedures.md` from the packaged default ONCE,
# and nothing ever read the packaged copy for that project again. So KYO-199's
# worker-audience rule — read each governed section your branch raises — reached
# neither consumer project, and no surface said so. `kyo doctor`'s
# `procedures_rules_missing` (a problem: one command clears it) and
# `procedures_rules_differ` (a notice: nothing can) are the report, and
# `kyo install-procedures` is the remedy the first one names. All three run the one
# comparison that lives in `kyosei/procedures.py`, which the KYO-95 section above binds
# to this repo's own two copies (KYO-212.c4), so the test and the tool cannot disagree
# about what a difference is.
#
# WHY MERGE AND NOT OVERWRITE, which is the whole shape of the record: the store copy
# is project-editable by design. Overwriting would have been a one-line addition to
# `kyo install-skill` and would have silently deleted this repository's own two local
# rules, and every rule a consumer wrote. So packaged rules the copy lacks are
# inserted, rules whose text differs are reported and left, store-only rules are not
# touched.
#
# HOW "A PROJECT BUILT FROM AN OLDER PACKAGED DEFAULT" IS BUILT HERE (KYO-212.c5). By
# deleting a rule from the scaffolded project's ledger, which is by construction the
# file an older default would have produced — the alternative, editing the packaged
# copy under `src/`, would have the suite write to the tree it is testing.


def _without_rule(text: str, title: str) -> str:
    """``text`` with one ``### `` rule removed, as an older default would not have had it.

    Deliberately not reusing the tool's own block-boundary code: a bug in that code
    would then be invisible here, and this file's whole job in the KYO-95 section above
    is to be an independent reader of the same two files.
    """
    start = text.index(f"### {title}\n")
    next_rule = text.find("\n### ", start + 1)
    section_end = min(
        (pos for pos in (text.find("\n---\n", start), text.find("\n## ", start)) if pos != -1),
        default=len(text),
    )
    if next_rule != -1 and next_rule < section_end:
        return text[:start] + text[next_rule + 1 :]
    # Last rule of its section: take the blank line that separated it from the rule
    # above with it, or the section would keep a hole where it used to be.
    return text[: start - 1] + text[section_end:]


def _titles(text: str) -> list[str]:
    return [rule.title for rule in parse_procedures(text)]


def _packaged() -> str:
    return PACKAGED_LEDGER.read_text(encoding="utf-8")


#: `PROSE_RULE_TITLE` above is a prefix, matched with `in`; the comparison names rules
#: by their whole `###` heading, and this is that heading — read off the frozen
#: pre-KYO93 copy of the rule, so it cannot drift from the text these tests revert to.
PROSE_RULE_HEADING = PRE_KYO93_PROSE_RULE.splitlines()[0].removeprefix("### ")


@pytest.mark.parametrize(
    "position",
    ["first of its section", "middle of its section", "last of its section", "a controller rule"],
)
def test_merging_a_removed_rule_back_restores_the_file_byte_for_byte(position: str) -> None:
    """KYO-212.c2's "in the packaged order within their audience section, and leaves
    every other line of the file untouched", driven the only way that is checkable
    without restating the algorithm: take the packaged ledger, remove one rule, merge —
    and require the result to be the original file, byte for byte. Position matters
    because each one takes a different branch: the first rule of a section has no
    earlier sibling to anchor to, the last has no later one, and the file's `---` breaks
    and `## ` headings sit between sections where a careless insertion lands.
    """
    packaged = _packaged()
    worker = [r.title for r in worker_rules(packaged)]
    controller = [r.title for r in orchestrator_rules(packaged)]
    title = {
        "first of its section": worker[0],
        "middle of its section": worker[len(worker) // 2],
        "last of its section": worker[-1],
        "a controller rule": controller[0],
    }[position]

    older = _without_rule(packaged, title)
    assert title not in _titles(older), "the removal did not remove the rule"
    assert len(_titles(older)) == len(_titles(packaged)) - 1, "the removal took something else too"

    merged, added = merge_ledger(packaged, older)
    assert added == [title]
    assert merged == packaged


def test_two_missing_rules_come_back_in_packaged_order() -> None:
    """The single-rule case above cannot see an ordering bug between two insertions:
    each one would land beside a rule that is already there either way. Remove two
    adjacent rules and the second has to anchor to the first one restored."""
    packaged = _packaged()
    worker = [r.title for r in worker_rules(packaged)]
    first, second = worker[1], worker[2]

    older = _without_rule(_without_rule(packaged, second), first)
    merged, added = merge_ledger(packaged, older)
    assert added == [first, second]
    assert merged == packaged


def test_a_project_that_never_edited_anything_is_silent(kyo, project: Path) -> None:
    """The canary on every assertion below: a freshly scaffolded project must produce
    no finding and no addition, or "doctor went silent" would mean nothing."""
    assert kyo("doctor").code == 0
    result = kyo("install-procedures", "--json")
    assert result.code == 0, result
    assert result.json["added"] == []
    assert result.json["changed"] is False


def test_doctor_reports_the_rule_a_project_never_received(kyo, project: Path) -> None:
    """The live case, in the shape it actually took: KYO-199 added a worker-audience
    rule to the packaged default, two consumer projects already had a store, and the
    rule reached neither of them with nothing anywhere saying so."""
    ledger = project / "kyosei" / "procedures.md"
    title = [r.title for r in worker_rules(_packaged())][-1]
    ledger.write_text(_without_rule(ledger.read_text(encoding="utf-8"), title), encoding="utf-8")

    result = kyo("doctor")
    assert result.code != 0
    assert "procedures_rules_missing" in result.err
    assert title in result.err
    assert "kyo install-procedures" in result.err


def test_a_missing_rule_is_a_problem_because_one_command_clears_it(
    kyo, project: Path
) -> None:
    """KYO-214's tiers, applied. This half stays in the failing tier and has to: every
    finding names `kyo install-procedures`, and a named remedy is exactly the case where
    the reader can act now — the bar `NOTICE_CHECKS` states. Moving this check into
    `NOTICE_CHECKS` turns this test red, which is the point of writing it."""
    ledger = project / "kyosei" / "procedures.md"
    title = [r.title for r in worker_rules(_packaged())][-1]
    ledger.write_text(_without_rule(ledger.read_text(encoding="utf-8"), title), encoding="utf-8")

    result = kyo("doctor", "--json")
    assert result.code == EXIT_DOCTOR, result
    assert result.json["ok"] is False
    assert result.json["checks"]["procedures_rules_missing"]["tier"] == TIER_PROBLEM
    assert any(
        line.startswith("[procedures_rules_missing] ") and title in line
        for line in result.json["problems"]
    ), result.json["problems"]
    assert result.json["notices"] == []


def test_a_reworded_rule_is_a_notice_and_never_fails_the_run(kyo, project: Path) -> None:
    """The other half, and the disagreement this dispatch filed: a rule the project
    reworded on purpose cannot be cleared by anything — `kyo install-procedures` will not
    touch a rule already there, and the packaged copy lives inside the installed package
    — so failing on it asks a consumer to undo their own edit or read a red doctor
    forever. Declaring it in `NOTICE_CHECKS` is what makes "stays reported" bearable.
    Dropping it from that set turns this test red."""
    ledger = project / "kyosei" / "procedures.md"
    ledger.write_text(
        _with_the_prose_rule_reverted(ledger.read_text(encoding="utf-8")), encoding="utf-8"
    )

    result = kyo("doctor", "--json")
    assert result.code == 0, result
    assert result.json["ok"] is True, "a notice never fails the run"
    assert result.json["checks"]["procedures_rules_differ"]["tier"] == TIER_NOTICE
    assert result.json["problems"] == []
    (notice,) = result.json["notices"]
    assert notice.startswith("[procedures_rules_differ] ")
    assert PROSE_RULE_TITLE in notice
    assert "information, not a failure" in notice, "it says which tier it is in, in words"


def test_the_older_project_upgraded_one_rule_at_a_time(kyo, project: Path) -> None:
    """KYO-212.c5, end to end. A project built from an older packaged default (one rule
    behind), which has since edited one packaged rule and written one rule of its own:
    doctor reports the missing rule and the edited one, `--print` previews without
    writing, the command adds only what is missing, doctor goes silent about that rule,
    and both of the project's own edits survive untouched — the merge never rewrites a
    rule somebody chose (KYO-212.c3)."""
    ledger = project / "kyosei" / "procedures.md"
    missing = [r.title for r in worker_rules(_packaged())][-1]

    older = _without_rule(ledger.read_text(encoding="utf-8"), missing)
    older = _with_the_prose_rule_reworded(older) + "\n" + MULTI_LINE_RULE
    ledger.write_text(older, encoding="utf-8")

    # `--json`, because with a problem outstanding the human-readable failure carries the
    # problem tier alone (KYO-214); the envelope carries both lists on either path.
    reported = kyo("doctor", "--json")
    assert reported.code == EXIT_DOCTOR, reported
    problems, notices = reported.json["problems"], reported.json["notices"]
    assert any(missing in line and "kyo install-procedures" in line for line in problems)
    assert any(PROSE_RULE_TITLE in line for line in notices), "the edited rule is a notice"
    assert not any(
        "A rule whose prose soft-wraps" in line for line in problems + notices
    ), "the project's own rule is neither"

    preview = kyo("install-procedures", "--print", "--json")
    assert preview.code == 0, preview
    assert preview.json["added"] == [missing]
    assert ledger.read_text(encoding="utf-8") == older, "--print wrote to the file"

    applied = kyo("install-procedures", "--json")
    assert applied.code == 0, applied
    assert applied.json["added"] == [missing]
    assert applied.json["differing"] == [PROSE_RULE_HEADING], "it says what it declined to"

    upgraded = ledger.read_text(encoding="utf-8")
    assert missing in _titles(upgraded)
    # Everything the project wrote is byte-identical, and the new rule is the only
    # thing that arrived: strip it back out and the file is exactly what it was.
    assert _without_rule(upgraded, missing) == older
    assert PRE_KYO93_WORDING in upgraded, "the hand-edited rule survived the merge verbatim"

    after = kyo("doctor", "--json")
    assert after.code == 0, "the problem is gone, and what remains is a notice"
    assert after.json["problems"] == [], "the missing rule is no longer reported"
    assert any(
        PROSE_RULE_TITLE in notice for notice in after.json["notices"]
    ), "the edited one still is, until a person settles it"


def test_a_project_whose_ledger_is_gone_gets_the_whole_packaged_default(
    kyo, project: Path
) -> None:
    """A copy deleted rather than edited has nothing to preserve, and the command must
    not read "no rules are missing" off a file that is not there."""
    ledger = project / "kyosei" / "procedures.md"
    ledger.unlink()
    result = kyo("install-procedures", "--json")
    assert result.code == 0, result
    assert ledger.read_text(encoding="utf-8") == _packaged()
    assert kyo("doctor").code == 0


def test_doctor_and_the_command_agree_about_which_rules_differ(kyo, project: Path) -> None:
    """KYO-212.c4's point, driven rather than asserted by the fact that one function is
    imported twice: doctor's findings, the command's `differing` list and this file's own
    binding of the comparison name the same rules for the same pair of files."""
    ledger = project / "kyosei" / "procedures.md"
    diverged = _with_the_prose_rule_reworded(ledger.read_text(encoding="utf-8"))
    missing = [r.title for r in orchestrator_rules(_packaged())][0]
    diverged = _without_rule(diverged, missing)
    ledger.write_text(diverged, encoding="utf-8")

    named_by_the_comparison = {
        difference.title
        for difference in compare_ledgers(_packaged(), diverged)
        if difference.kind in (LEDGER_MISSING, LEDGER_CHANGED)
    }
    assert named_by_the_comparison == {PROSE_RULE_HEADING, missing}

    reported = kyo("doctor", "--json")
    said = "\n".join(reported.json["problems"] + reported.json["notices"])
    assert all(title in said for title in named_by_the_comparison)
    result = kyo("install-procedures", "--json")
    assert set(result.json["added"]) | set(result.json["differing"]) == named_by_the_comparison


# ================================================================= KYO-151
#
# A dispatch that changes a standing rule cannot leave this repository green, and
# nothing said so. Two dispatches (KYO-132, KYO-149) each diagnosed the same structural
# fact from a red suite and each left the controller a bespoke artifact; the knowledge
# lived in a comment inside the file whose failure sends you looking, which is exactly
# where it is useless.
#
# The three parts pinned here are the three the record asked for and no more. c1: the
# brief says it, before the surprise rather than after it. c3: the command that performs
# the hand-off refuses to become a route around the store-write ban. c4: the check that
# made the gap visible still bites — which is the one thing that must NOT be traded for
# a green suite, and is why the section above this one is left exactly as it was.


def _brief_of(kyo, record: str) -> str:
    return kyo("show", record, "--json").json["dispatches"][0]["brief"]


def test_a_dispatch_that_claims_the_packaged_ledger_is_told_about_the_hand_off(
    kyo, git_project: Path, tmp_path: Path, write_file
) -> None:
    """KYO-151.c1. The worker is told, in the frozen brief, that the copy its edit has to
    reach is one it cannot write — and which command applies it, and what the interval
    between the two costs. Derivable at freeze time from the claim the dispatch already
    computed, which is why it can be stated rather than rediscovered."""
    record = _scoped(
        kyo, git_project, write_file, "ledger", f"{PACKAGED_LEDGER_SOURCE_PATH}\n"
    )
    assert kyo("dispatch", record, "--branch", "kyo-151-c1", *_elsewhere(tmp_path, "c1")).code == 0

    brief = _brief_of(kyo, record)
    # The two files, named apart: the one in scope and the one that is not.
    assert PACKAGED_LEDGER_SOURCE_PATH in brief
    assert STORE_LEDGER_PATH in brief
    # The guard that makes the second unwritable, and the command that applies it.
    assert "kyo check-branch" in brief
    assert "kyo install-procedures" in brief
    # The part `kyo install-procedures` does NOT do, which is the half both earlier
    # dispatches had to supply by hand.
    assert "rewrites nothing already there" in brief
    # The interval a controller can miss, and the red suite stated as a condition of the
    # work rather than met as a surprise.
    assert "tests/test_procedures.py" in brief
    assert "do not weaken the check" in brief


def test_a_dispatch_that_does_not_claim_the_ledger_is_told_none_of_it(
    kyo, git_project: Path, tmp_path: Path, write_file
) -> None:
    """The other half of c1, and the reason it is keyed off the claim rather than
    printed on every brief: a block that arrives unconditionally is a block nobody reads
    by the third dispatch. This one appears exactly when it is about the work."""
    record = _scoped(kyo, git_project, write_file, "other", "src/kyosei/cmd_new.py\n")
    sent = kyo("dispatch", record, "--branch", "kyo-151-c1b", *_elsewhere(tmp_path, "c1b"))
    assert sent.code == 0, sent

    brief = _brief_of(kyo, record)
    assert PACKAGED_LEDGER_SOURCE_PATH not in brief
    assert "kyo install-procedures" not in brief


def _on_branch(root: Path, branch: str) -> None:
    subprocess.run(
        ["git", "checkout", "-q", "-b", branch], cwd=root, check=True, capture_output=True
    )


def _rename_default_branch_to_main(root: Path) -> None:
    """`git init` names the first branch from the developer's own `init.defaultBranch`,
    so the tests below say which branch they mean instead of inheriting one."""
    subprocess.run(["git", "branch", "-M", "main"], cwd=root, check=True, capture_output=True)


def test_install_procedures_refuses_to_write_from_a_dispatch_worktree(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """KYO-151.c3, on the signal that actually identifies a dispatch: `git worktree add`
    produces a LINKED worktree, whose `--git-common-dir` points back at the main
    checkout's admin directory. That is git's own fact, so the refusal holds whatever the
    worktree and its branch happen to be named — which a path convention would not."""
    _rename_default_branch_to_main(git_project)
    worktree = tmp_path / "wt-151"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "kyo-151-work", str(worktree)],
        cwd=git_project,
        check=True,
        capture_output=True,
    )
    ledger = worktree / "kyosei" / "procedures.md"
    before = ledger.read_text(encoding="utf-8")
    ledger.write_text(_without_rule(before, [r.title for r in worker_rules(_packaged())][-1]))
    behind = ledger.read_text(encoding="utf-8")

    result = invoke(["--root", str(worktree), "install-procedures"])
    assert result.code != 0, result
    assert ledger.read_text(encoding="utf-8") == behind, "the refusal came before the write"
    # Refused, and told where to go instead — a refusal without a destination is this
    # record's own defect one step later.
    assert "linked git worktree" in result.err
    assert str(git_project) in result.err
    assert "`main`" in result.err
    assert "kyo check-branch" in result.err

    # `--print` writes nothing, so it is not refused: the ban is on writing the store
    # copy, and a worker that cannot preview the hand-off cannot report it accurately.
    preview = invoke(["--root", str(worktree), "install-procedures", "--print", "--json"])
    assert preview.code == 0, preview
    assert preview.json["added"], "the preview still says what the hand-off would add"
    assert ledger.read_text(encoding="utf-8") == behind


def test_install_procedures_refuses_to_write_from_a_branch_that_is_not_main(
    kyo, git_project: Path
) -> None:
    """The second half of c3. A worktree is the shape a dispatch usually takes, but not
    the only one: a controller who dispatched into a branch of the main checkout, or who
    simply never switched back, would otherwise write the store copy from a branch whose
    diff `kyo check-branch` forbids."""
    _rename_default_branch_to_main(git_project)
    _on_branch(git_project, "kyo-151-feature")
    ledger = git_project / "kyosei" / "procedures.md"
    behind = _without_rule(
        ledger.read_text(encoding="utf-8"), [r.title for r in worker_rules(_packaged())][-1]
    )
    ledger.write_text(behind, encoding="utf-8")

    result = kyo("install-procedures")
    assert result.code != 0, result
    assert ledger.read_text(encoding="utf-8") == behind
    assert "kyo-151-feature" in result.err
    assert "`main`" in result.err

    # And it goes through on the branch it named, which is what makes the refusal a
    # direction rather than a wall (KYO-5: an error naming a remedy proves the remedy).
    subprocess.run(
        ["git", "checkout", "-q", "main"], cwd=git_project, check=True, capture_output=True
    )
    assert kyo("install-procedures", "--json").json["added"]


def test_a_project_with_no_main_branch_is_not_refused_for_a_naming_convention(
    kyo, git_project: Path
) -> None:
    """The boundary of the branch signal. `main` is Kyosei's own convention and the
    branch `kyo check-branch` defaults to; a project on `master` has not done anything
    wrong, and refusing it would make this command unrunnable there forever."""
    subprocess.run(
        ["git", "branch", "-M", "master"], cwd=git_project, check=True, capture_output=True
    )
    ledger = git_project / "kyosei" / "procedures.md"
    ledger.write_text(
        _without_rule(
            ledger.read_text(encoding="utf-8"), [r.title for r in worker_rules(_packaged())][-1]
        ),
        encoding="utf-8",
    )
    result = kyo("install-procedures", "--json")
    assert result.code == 0, result
    assert result.json["added"]


def test_a_store_outside_git_is_not_refused_on_a_question_nothing_could_ask(
    kyo, project: Path
) -> None:
    """The other boundary, and the reason the refusal is written as three git facts
    rather than one guess: `kyo init` scaffolds stores outside git, and a command that
    refused there would be refusing on the strength of a question it could not ask."""
    ledger = project / "kyosei" / "procedures.md"
    ledger.write_text(
        _without_rule(
            ledger.read_text(encoding="utf-8"), [r.title for r in worker_rules(_packaged())][-1]
        ),
        encoding="utf-8",
    )
    assert kyo("install-procedures", "--json").json["added"]


# ----------------------------------------------------------------- KYO-151.c2
#
# c2's own criterion: the hand-off KYO-132 and KYO-149 each improvised (an apply
# script, then a patch) is a REWORD of a packaged rule, or of the preamble -- exactly
# what `merge_ledger` refuses to touch by design (KYO-212.c3). `--adopt`/
# `--adopt-preamble` are the opt-in override: naming a rule (or the preamble) replaces
# its text with the packaged wording, deliberately, and never as a side effect of the
# plain merge -- so a bare `kyo install-procedures` still never rewrites a rule already
# there, which is exactly what the tests above this section keep proving.


def test_adopt_rewrites_exactly_the_named_rule_from_the_packaged_default(
    kyo, project: Path
) -> None:
    """The direction KYO-132/KYO-149 each had to hand the controller by hand: a
    packaged rule was reworded, the store copy still carries the old text, and
    `--adopt` is the one reviewable command that takes the new wording on request."""
    ledger = project / "kyosei" / "procedures.md"
    edited = _with_the_prose_rule_reverted(ledger.read_text(encoding="utf-8"))
    ledger.write_text(edited, encoding="utf-8")

    result = kyo("install-procedures", "--adopt", PROSE_RULE_HEADING, "--json")
    assert result.code == 0, result
    (adopted,) = result.json["adopted"]
    assert adopted["title"] == PROSE_RULE_HEADING
    assert PRE_KYO93_WORDING in adopted["before"], "it prints what was there"
    assert POST_KYO93_WORDING in adopted["after"], "and what replaced it"
    assert result.json["changed"] is True
    assert result.json["differing"] == [], "the adopted rule no longer differs"

    upgraded = ledger.read_text(encoding="utf-8")
    assert PRE_KYO93_WORDING not in upgraded, "the store copy now carries the packaged text"
    assert POST_KYO93_WORDING in upgraded

    # Nothing else moved: the rule this test never touched still reads exactly as the
    # freshly scaffolded project wrote it.
    other_title = [r.title for r in worker_rules(_packaged())][-1]
    assert other_title != PROSE_RULE_HEADING
    assert rule_bodies(upgraded)[other_title] == rule_bodies(_packaged())[other_title]

    assert kyo("doctor", "--json").json["notices"] == [], "the notice cleared"


def test_adopting_the_preamble_rewrites_it_from_the_packaged_default(
    kyo, project: Path
) -> None:
    """The other half c2 asks for: KYO-167 gave the comparison a way to SAY the two
    preambles disagree and no way to act on it except a person editing one copy by
    hand. `--adopt-preamble` is that act, named."""
    ledger = project / "kyosei" / "procedures.md"
    edited = ledger.read_text(encoding="utf-8").replace(
        "This is Law 15", "This is Law 15, as this project reads it", 1
    )
    assert edited != ledger.read_text(encoding="utf-8")
    ledger.write_text(edited, encoding="utf-8")
    assert preamble(edited) != preamble(_packaged())

    result = kyo("install-procedures", "--adopt-preamble", "--json")
    assert result.code == 0, result
    assert result.json["preamble_adopted"] is not None
    assert "as this project reads it" in result.json["preamble_adopted"]["before"]
    assert "as this project reads it" not in result.json["preamble_adopted"]["after"]
    assert result.json["changed"] is True

    upgraded = ledger.read_text(encoding="utf-8")
    assert preamble(upgraded) == preamble(_packaged())
    # Rule content is untouched; only what sits above the first rule moved.
    assert {r.title: r for r in parse_procedures(upgraded)}.keys() == {
        r.title for r in parse_procedures(edited)
    }


def test_adopt_refuses_a_rule_the_packaged_default_does_not_ship(kyo, project: Path) -> None:
    """The first of c2's two refusals. A rule only the store copy carries
    (:data:`LEDGER_STORE_ONLY`) has no packaged wording to reconcile against —
    `--adopt` must say that rather than silently doing nothing, or leaving the store
    untouched while claiming success."""
    ledger = project / "kyosei" / "procedures.md"
    ledger.write_text(ledger.read_text(encoding="utf-8") + "\n" + MULTI_LINE_RULE, encoding="utf-8")
    local_title = MULTI_LINE_RULE.splitlines()[0].removeprefix("### ")
    before = ledger.read_text(encoding="utf-8")

    result = kyo("install-procedures", "--adopt", local_title)
    assert result.code != 0, result
    assert local_title in result.err
    assert "does not ship" in result.err or "no packaged wording" in result.err
    assert ledger.read_text(encoding="utf-8") == before, "the refusal came before any write"


def test_adopt_refuses_a_rule_already_identical(kyo, project: Path) -> None:
    """The second of c2's two refusals. A fresh project's rules all read the same in
    both copies; adopting one would be a no-op that still claimed to have changed
    something, so it is refused and named instead."""
    title = [r.title for r in worker_rules(_packaged())][0]
    result = kyo("install-procedures", "--adopt", title)
    assert result.code != 0, result
    assert title in result.err
    assert "identical" in result.err or "already" in result.err


def test_adopt_preamble_refuses_when_already_identical(kyo, project: Path) -> None:
    """The preamble's own version of the same refusal: a freshly scaffolded project's
    preamble already matches the packaged one, so there is nothing to adopt."""
    result = kyo("install-procedures", "--adopt-preamble")
    assert result.code != 0, result
    assert "identical" in result.err or "already" in result.err


def test_adopt_is_refused_from_a_dispatch_worktree_the_same_way_as_the_plain_merge(
    kyo, git_project: Path, tmp_path: Path
) -> None:
    """KYO-151.c3's refusal applies to `--adopt`/`--adopt-preamble` too: the hand-off is
    a controller act however it is invoked, and a worktree that could write the store
    copy through `--adopt` alone would be a route around the same ban `kyo check-branch`
    enforces."""
    _rename_default_branch_to_main(git_project)
    worktree = tmp_path / "wt-151c2"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "kyo-151c2-work", str(worktree)],
        cwd=git_project,
        check=True,
        capture_output=True,
    )
    ledger = worktree / "kyosei" / "procedures.md"
    edited = _with_the_prose_rule_reverted(ledger.read_text(encoding="utf-8"))
    ledger.write_text(edited, encoding="utf-8")

    result = invoke(
        ["--root", str(worktree), "install-procedures", "--adopt", PROSE_RULE_HEADING]
    )
    assert result.code != 0, result
    assert ledger.read_text(encoding="utf-8") == edited, "the refusal came before the write"
    assert "linked git worktree" in result.err

    preview = invoke(
        [
            "--root",
            str(worktree),
            "install-procedures",
            "--adopt",
            PROSE_RULE_HEADING,
            "--print",
            "--json",
        ]
    )
    assert preview.code == 0, preview
    assert preview.json["adopted"], "the preview still says what --adopt would do"
    assert ledger.read_text(encoding="utf-8") == edited, "--print wrote nothing"


def test_the_doctor_notice_names_the_adopt_flag_beside_the_rule(kyo, project: Path) -> None:
    """KYO-151.c2's last requirement: the notice `procedures_rules_differ` prints must
    read as a remedy, not only a finding — the controller should not have to already
    know `--adopt` exists to find it."""
    ledger = project / "kyosei" / "procedures.md"
    ledger.write_text(
        _with_the_prose_rule_reworded(ledger.read_text(encoding="utf-8")), encoding="utf-8"
    )

    result = kyo("doctor", "--json")
    assert result.code == 0, result
    (notice,) = result.json["notices"]
    assert f"--adopt {PROSE_RULE_HEADING!r}" in notice, notice


def test_adopt_rule_and_adopt_preamble_are_pure_functions_and_never_silent(project: Path) -> None:
    """The unit-level half of the same guarantee: :func:`adopt_rule` and
    :func:`adopt_preamble` raise :class:`KyoseiError` rather than doing nothing, so a
    caller that reaches them directly (as `install_procedures` does) cannot mistake a
    no-op for success."""
    packaged = _packaged()
    reverted = _with_the_prose_rule_reverted(packaged)

    merged, before, after = adopt_rule(packaged, reverted, PROSE_RULE_HEADING)
    assert merged == packaged
    assert PRE_KYO93_WORDING in before
    assert POST_KYO93_WORDING in after

    with pytest.raises(KyoseiError):
        adopt_rule(packaged, reverted, "A rule whose prose soft-wraps")
    with pytest.raises(KyoseiError):
        adopt_rule(packaged, packaged, PROSE_RULE_HEADING)

    edited_preamble = packaged.replace("This is Law 15", "This is Law 15 (as amended)", 1)
    merged_preamble, before_p, after_p = adopt_preamble(packaged, edited_preamble)
    assert preamble(merged_preamble) == preamble(packaged)
    assert "(as amended)" in before_p
    assert "(as amended)" not in after_p

    with pytest.raises(KyoseiError):
        adopt_preamble(packaged, packaged)


# ================================================================= KYO-167
#
# The two copies were compared RULE BY RULE, so everything above the first `###` was
# outside the comparison entirely — the Law 15 paragraph that says what the ledger is and
# who reads it, and the "Rule format" section that specifies the shape every rule takes.
# The two could disagree there forever and nothing would notice.
#
# Found the way this kind of gap is usually found: by a prediction that failed. KYO-162's
# brief told its worker to expect KYO-151's structural failure and leave the repo red. No
# such failure occurred, because its only edit sat in the preamble — and *why not* was the
# finding. It also sharpens KYO-151: the wall bites when a RULE is edited, not when the
# file is.
#
# The gap is closed by comparing more, never by saying less: the tests below pin that the
# Law 15 paragraph is still in both copies saying what it said, so a future green cannot
# be bought by deleting the text that made the two copies worth comparing.


def _preambles() -> tuple[str, str]:
    return (
        preamble(PACKAGED_LEDGER.read_text(encoding="utf-8")),
        preamble(STORE_LEDGER.read_text(encoding="utf-8")),
    )


@needs_store_ledger
def test_the_preamble_reader_returns_the_real_preamble_of_both_files() -> None:
    """The canary the rest of this section rests on, and the same one KYO-95 wrote for
    the rule comparison: a reader that returned "" would make every comparison below
    trivially true and green forever. So prove it captures the Law 15 paragraph and the
    Rule format section — including the ``### `` example *inside* the fence, which is why
    the cut is made in masked text — and stops before the first real rule."""
    for text, path in (
        (PACKAGED_LEDGER.read_text(encoding="utf-8"), PACKAGED_REL),
        (STORE_LEDGER.read_text(encoding="utf-8"), STORE_REL),
    ):
        head = preamble(text)
        assert "This is Law 15" in head, path
        assert "## Rule format" in head, path
        # The fenced example is inside it, which is why the cut is made in masked text.
        assert "### <short imperative title>" in head, path
        # And it ends where the rules begin: no real rule's title is in it.
        for rule in parse_procedures(text):
            assert f"### {rule.title}" not in head, (path, rule.title)


@needs_store_ledger
def test_the_law_15_paragraph_still_says_what_it_said_in_both_copies() -> None:
    """KYO-167.c3, and the guard on the cheap way out. This gap can be "closed" by
    deleting the preamble content or reducing both copies to the same placeholder, and
    both would leave the two files agreeing about nothing worth agreeing on. Every clause
    the paragraph is *for* is named here, so a green bought that way is not available."""
    for head, path in zip(_preambles(), (PACKAGED_REL, STORE_REL), strict=True):
        assert "standing-rules ledger" in head, path
        assert "`audience: worker` rules into the frozen dispatch" in head, path
        assert "`audience: orchestrator`" in head, path
        assert "`audience: human`" in head, path
        assert "This is Law 15 — audience is a first-class property" in head, path
        assert "the reading budget is the binding constraint" in head, path
        assert "reads mostly text it cannot act on" in head, path
        assert "A rule that lives where its audience does not look is not a rule." in head, path


def test_a_preamble_edit_in_one_copy_alone_is_reported(tmp_path: Path) -> None:
    """KYO-167.c2. The whole of the gap: a divergence with no rule involved, which the
    rule-by-rule comparison saw nothing of. Driven against a synthesised pair rather than
    the two live files, so it keeps testing the mechanism whatever state the hand-off
    leaves those in."""
    packaged = PACKAGED_LEDGER.read_text(encoding="utf-8")
    edited = packaged.replace("This is Law 15", "This is Law 15 (as amended)", 1)
    assert edited != packaged, "the edit changed nothing"

    (difference,) = ledger_differences(packaged, edited, store_only=frozenset())
    assert PREAMBLE_FINDING in difference
    # The direction is shown, not merely claimed — the copy that moved is quoted saying
    # the new thing and the copy that did not is quoted saying the old.
    lines = difference.splitlines()
    store_line = next(ln for ln in lines[1:] if ln.strip().startswith(STORE_REL))
    packaged_line = next(ln for ln in lines[1:] if ln.strip().startswith(PACKAGED_REL))
    assert "(as amended)" in store_line
    assert "(as amended)" not in packaged_line

    # And no rule is implicated, because none moved.
    assert not [
        d for d in compare_ledgers(packaged, edited) if d.kind in (LEDGER_MISSING, LEDGER_CHANGED)
    ]


def test_a_rewrapped_preamble_is_not_a_divergence() -> None:
    """The boundary, and the reason the comparison is of words rather than of bytes. The
    hand application this whole record is about is a person pasting prose into a second
    file; a check that cried wolf on a re-wrap is a check that gets an allowlist entry
    and then gets ignored."""
    packaged = PACKAGED_LEDGER.read_text(encoding="utf-8")
    rewrapped = packaged.replace(
        "brief, and the session brief (`kyo brief`) injects",
        "brief, and the session brief\n(`kyo brief`) injects",
        1,
    )
    assert rewrapped != packaged, "the re-wrap changed nothing"
    assert ledger_differences(packaged, rewrapped, store_only=frozenset()) == []


def test_a_preamble_divergence_is_never_filed_as_a_rule(kyo, project: Path) -> None:
    """It is not a rule and must never be handed to anything that reads rule titles.
    `kyo install-procedures` merges BY RULE and has no merge for this — so it must not
    claim one, must not name a rule that does not exist, and above all must not rewrite
    the preamble on a consumer who edited it."""
    ledger = project / "kyosei" / "procedures.md"
    edited = ledger.read_text(encoding="utf-8").replace(
        "This is Law 15", "This is Law 15, as this project reads it", 1
    )
    assert edited != ledger.read_text(encoding="utf-8")
    ledger.write_text(edited, encoding="utf-8")

    (difference,) = compare_ledgers(_packaged(), edited)
    assert difference.kind == LEDGER_PREAMBLE
    assert difference.title == PREAMBLE_TITLE

    result = kyo("install-procedures", "--json")
    assert result.code == 0, result
    assert result.json["added"] == []
    assert result.json["differing"] == [], "a preamble is not a rule that differs"
    assert ledger.read_text(encoding="utf-8") == edited, "the consumer's preamble survives"
    assert PREAMBLE_TITLE not in result.out


# ================================================================= KYO-145
#
# The count is true when frozen and false when read, and the wording did not save it.
#
# KYO-132 shipped the concurrency bullet and hedged the NUMBER honestly ("at the moment
# this brief was frozen"). It did not hedge the ADVICE: a zero count went on, in the
# present tense, to say "You are the only running dispatch, so nothing is contending with
# you ... and a full-suite run here would cost only time". KYO-131's dispatch reported
# that its brief said exactly that while four siblings were live -- its own branch point
# is titled "Wave 3 dispatched: KYO-131, 139, 141, 142, 143". Measured across that wave's
# five briefs, generated in one batch: none, 1, 2, 3, 4. Every one accurate at its own
# freeze instant; all but the last stale before any worker started. The controller creates
# N dispatches, then N worktrees, then spawns N workers -- sampled in phase one, read in
# phase three.
#
# What it is NOT: an argument to dispatch one at a time (that serialises a wave to fix a
# wording problem, and batching is what the disjointness check makes safe), and not an
# argument for Kyosei to re-count at spawn time (it does not spawn workers and must not
# start, KYO-61 -- there is no spawn moment it can observe).
#
# So: a floor. "At least N, and this brief cannot see any created after it" is fully true
# at every instant, and the zero case then behaves -- an absence of evidence invites no
# conclusion where "you are the only running dispatch" asserted one.
#
# WHY THIS LIVES HERE. `tests/test_procedures.py` is where the brief's standing-rule
# sentences are pinned, and the rule this bullet is the mechanism for -- "run the tests
# that cover your change, not the whole suite" -- names the bullet in its own
# `enforced-by` line. Keeping the wording test beside the rule test is what stops the two
# from drifting: the last two assertions below read the rule out of the ledger and check
# that it still describes what the bullet actually does. A file of its own would test the
# bullet and leave the rule free to go on describing the version KYO-145 deleted.


def _both_bullets() -> tuple[str, str]:
    """The bullet in both of its states, rendered directly. Driven end to end elsewhere
    in this file; here it is called straight so a wording assertion names the renderer
    rather than depending on the state of a fixture's board."""
    return (
        _concurrency_bullet([]),
        _concurrency_bullet([{"dispatch_id": "TST-1.d1", "record_id": "TST-1"}]),
    )


#: Wordings the bullet must never regain. Each is a present-tense claim about *now* built
#: out of a number sampled before the worker existed — the shape KYO-145 is about, not the
#: particular sentence, so a rewrite that reintroduces the shape under new words is caught
#: by the positive assertions below rather than by this list alone.
FORBIDDEN_CONCURRENCY_WORDINGS = (
    "You are the only running dispatch",
    "nothing is contending with you",
    "a full-suite run here would cost only time",
)


def test_neither_bullet_asserts_what_was_true_only_at_freeze_time() -> None:
    """KYO-145.c4, and c2. No sentence in either state draws a present-tense conclusion
    about contention from a count the brief cannot keep current."""
    for bullet in _both_bullets():
        for forbidden in FORBIDDEN_CONCURRENCY_WORDINGS:
            assert forbidden not in bullet, bullet


def test_the_count_is_presented_as_a_floor_in_both_states() -> None:
    """KYO-145.c1 and c3. The number is a floor that later dispatches can only raise —
    said in both states, because the zero case is the one that misled a worker and the
    one a reader is likeliest to treat as a total."""
    empty, populated = _both_bullets()
    for bullet in (empty, populated):
        assert "A floor, never a total" in bullet
        assert "cannot see a dispatch created after it" in bullet
        # The mechanism that makes it a floor, so the claim is not asked for on trust.
        assert "creates the dispatches one after another and spawns" in bullet

    assert "at least 1 had been created when this brief was frozen" in populated
    assert "none had been created when this brief was frozen" in empty
    assert "absence of evidence" in empty
    assert "no conclusion about contention follows from it" in empty


def test_the_restraint_is_stated_without_being_derived_from_the_count() -> None:
    """The other half of c2, and the design decision the record left open ("worth deciding
    separately whether the derived advice belongs in the brief at all"). It does — it is
    the reason the count is stated — but it is now the same advice at every count, so
    there is no number the brief could be wrong about that changes what it says."""
    empty, populated = _both_bullets()
    restraint = "Run the tests that cover your change and leave the whole suite to your"
    assert restraint in empty
    assert restraint in populated
    assert empty.split(restraint)[1] == populated.split(restraint)[1], (
        "the advice differs by count, which is the derivation KYO-145 removed"
    )


def test_the_standing_rule_describes_the_bullet_this_build_actually_renders(
    project: Path,
) -> None:
    """The drift KYO-86/KYO-93/KYO-97 each caught once: an `enforced-by` line describing a
    mechanism that no longer works as stated, injected verbatim into every worker's brief.
    KYO-145 made the old line false in exactly that way — it claimed a lone worker is told
    it is alone, "which is what makes the rule self-cancel". Nothing does, and nothing can."""
    title = "Run the tests that cover your change, not the whole suite"
    rule = _rule_titled(_procedures(project), title)
    assert contains_phrase(rule.enforced_by, "as a FLOOR")
    assert contains_phrase(rule.enforced_by, "the rule never self-cancels")
    assert not contains_phrase(rule.enforced_by, "a lone worker is told that too")


# ================================================================= KYO-324
#
# Every turn a worker takes re-reads its whole context, and a test run's full output,
# once printed, is re-read on every later turn after that. The controller already has a
# rule for the second half (write a long run to a file, read the file) — this is the
# worker's own pair: batch independent tool calls into one turn instead of spending one
# turn per call, and once a run's output is in a file, read back only its failures rather
# than the whole log. Neither half has a mechanism yet — nothing counts turns, and nothing
# counts how much of a written-to-file run gets read back — so the rule is aspirational,
# stated with what a check would actually look at rather than a bare word.

TURN_ECONOMY_RULE_TITLE = "Batch independent calls into one turn; read a test run's failures only"


def _turn_economy_rule(text: str) -> Rule:
    matches = [r for r in worker_rules(text) if contains_phrase(r.title, TURN_ECONOMY_RULE_TITLE)]
    assert len(matches) == 1, [r.title for r in worker_rules(text)]
    return matches[0]


def test_the_turn_economy_rule_is_worker_audience_and_short(project: Path) -> None:
    """The rule is itself standing context every worker pays for on every turn — the
    thing it is about — so it is checked for brevity as well as presence: one rule, one
    short paragraph, not a restatement of its neighbour's four-minute wait advice."""
    rule = _turn_economy_rule(_procedures(project))
    assert rule.audience == "worker"
    body = _rule_body(_procedures(project), TURN_ECONOMY_RULE_TITLE)
    paragraphs = [p for p in body.split("\n\n") if p.strip() and not p.strip().startswith("-")]
    assert len(paragraphs) == 1, "more than one paragraph of justifying prose"
    assert "four minute" not in " ".join(body.split()).lower(), (
        "restates the neighbouring foreground-command rule instead of leaving it alone"
    )


def test_the_turn_economy_rule_says_batch_calls_and_read_only_failures(project: Path) -> None:
    """KYO-324.c1's content half: both instructions, each with its one-sentence reason —
    a turn re-reads the whole context, and printed output is re-read on every later turn."""
    rule = _turn_economy_rule(_procedures(project))
    flat = " ".join(rule.first_line.split())
    assert "independent reads, searches, and status commands together in one turn" in flat
    assert "quietly" in flat
    assert "reading back only its failures" in flat
    assert "a turn re-reads your whole context" in flat
    assert "printed output is re-read on every turn after" in flat


def test_the_turn_economy_rule_says_aspirational_and_names_what_would_measure_it(
    project: Path,
) -> None:
    """KYO-324.c1's honesty half: `aspirational` verbatim (nothing counts a worker's
    turns or its re-reads of a file), and — the discipline this whole ledger is about
    (Law 1) — what a check would actually look at rather than a bare claim of enforcement."""
    rule = _turn_economy_rule(_procedures(project))
    assert rule.enforced_by.startswith("aspirational")
    assert contains_phrase(rule.enforced_by, "nothing counts how many tool calls a turn batched")
    assert contains_phrase(rule.enforced_by, "sum_transcript_usage")
    assert contains_phrase(rule.enforced_by, "but nothing reads either signal today")


def test_the_turn_economy_rules_first_sentence_reaches_a_generated_brief(
    kyo, git_project: Path
) -> None:
    """KYO-324.c1's checked half. Only the title, `enforced-by`, and the rule's first
    *sentence* are brief-injected (procedures.md's own "Rule format" section) — so this
    drives a real `kyo dispatch` and reads the frozen brief back, rather than trusting
    that the packaged ledger and the injection pipeline agree."""
    rule = _turn_economy_rule(_procedures(git_project))
    record = new_record(kyo, criteria=["a"])
    result = kyo("--full", "dispatch", record, "--json")
    assert result.code == 0, result
    brief = result.json["record"]["dispatches"][0]["brief"]
    assert TURN_ECONOMY_RULE_TITLE in brief
    assert rule.first_line in brief


def test_removing_the_turn_economy_rule_fails_the_checked_test(kyo, git_project: Path) -> None:
    """The canary on the test above: cut the rule from the ledger the project actually
    dispatches from and the brief-injection check has to fail, or it is checking nothing
    (KYO-97's lesson, applied here)."""
    ledger = git_project / "kyosei" / "procedures.md"
    ledger.write_text(
        _without_rule(ledger.read_text(encoding="utf-8"), TURN_ECONOMY_RULE_TITLE),
        encoding="utf-8",
    )
    record = new_record(kyo, criteria=["a"])
    result = kyo("--full", "dispatch", record, "--json")
    assert result.code == 0, result
    brief = result.json["record"]["dispatches"][0]["brief"]
    assert TURN_ECONOMY_RULE_TITLE not in brief


# --------------------------------------------- KYO-356.c1: the kyosei:// link-form rule

LINK_FORM_RULE_TITLE = (
    "Cite every record to the maintainer as a markdown link with a `kyosei://` target"
)


def _link_form_rule(text: str) -> Rule:
    return _rule_titled(text, LINK_FORM_RULE_TITLE)


def test_the_link_form_rule_is_controller_audience_and_kyo_rules_lists_it(
    kyo, project: Path
) -> None:
    """KYO-356.c1. A rule that parses but names the wrong audience reaches nobody
    while looking like it reaches someone (KYO-27) — read from the scaffolded
    project, the packaged default every new Kyosei project starts from."""
    text = _procedures(project)
    rule = _link_form_rule(text)
    assert rule.audience == "orchestrator"

    listed = kyo("rules", "--json")
    assert listed.code == 0, listed
    assert LINK_FORM_RULE_TITLE in [r["title"] for r in listed.json["rules"]]
    assert LINK_FORM_RULE_TITLE in kyo("brief").out


def test_the_link_form_rule_says_aspirational_verbatim(project: Path) -> None:
    """Law 1: nothing checks that a citation actually got typeset as a link, so
    `mechanism` here would be exactly the overstatement `enforced-by` exists to
    prevent — the word is asserted verbatim because it is the whole content of the
    claim."""
    rule = _link_form_rule(_procedures(project))
    assert rule.enforced_by.startswith("aspirational")


def test_the_link_form_rule_gives_the_form_and_says_where_it_applies(project: Path) -> None:
    """KYO-356.c1's content half. The rule states the actual shape of the link — so a
    controller does not have to reconstruct it from the skill's own example — and
    says it applies to every message of a session, closing summaries and tables
    included, which is exactly where it had been drifting."""
    body = " ".join(_rule_body(_procedures(project), LINK_FORM_RULE_TITLE).split())
    assert "[KYO-263](kyosei://KYO/KYO-263)" in body
    assert "every message of a session, closing summaries and tables included" in body


def test_the_link_form_rule_reaches_kyo_brief_not_the_worker_brief(
    kyo, git_project: Path
) -> None:
    """Controller-audience, and only controller-audience: the drift was in the
    controller's own prose, not a worker's, so this must not cost a
    dispatched worker any brief bytes."""
    text = _procedures(git_project)
    assert LINK_FORM_RULE_TITLE not in {r.title for r in worker_rules(text)}

    record = new_record(kyo, criteria=["a"])
    dispatched = kyo("--full", "dispatch", record, "--json")
    assert dispatched.code == 0, dispatched
    assert LINK_FORM_RULE_TITLE not in dispatched.json["record"]["dispatches"][0]["brief"]


# ---------------------------------------------- KYO-413.c3/c4: malformed rule bullets
#
# `_AUDIENCE_RE` matched only a lowercase `**audience**` and
# `_ENFORCED_BY_RE` only `**enforced-by**`, and `parse_procedures` skips a section
# missing either -- so a rule written `**Audience**` or `**enforced by**` vanished from
# every brief while `kyo doctor`'s procedures check stayed green.

_KYO413_LEDGER = """\
### Rule A

- **audience**: worker
- **enforced-by**: aspirational

Well formed.

### Rule B

- **Audience**: worker, controller
- **Enforced-By**: aspirational

Capitalised keys.

### Rule C

- **audience**: worker
- **enforced by**: aspirational

Spaced key.
"""


def test_kyo413_rule_bullet_keys_match_any_case_and_both_spellings() -> None:
    from kyosei.procedures import orchestrator_rules, worker_rules

    assert [r.title for r in parse_procedures(_KYO413_LEDGER)] == ["Rule A", "Rule B", "Rule C"]
    assert [r.title for r in worker_rules(_KYO413_LEDGER)] == ["Rule A", "Rule B", "Rule C"]
    assert [r.title for r in orchestrator_rules(_KYO413_LEDGER)] == ["Rule B"]
    rule_c = parse_procedures(_KYO413_LEDGER)[2]
    assert rule_c.enforced_by == "aspirational"
    assert rule_c.first_line == "Spaced key."


_KYO413_NO_ENFORCED_BY = """
### A rule missing its enforcement line

- **audience**: worker

This reaches nobody.
"""


def test_kyo413_doctor_names_a_rule_section_missing_a_bullet(kyo, project: Path) -> None:
    from kyosei.procedures import incomplete_rules

    assert incomplete_rules(_KYO413_NO_ENFORCED_BY) == [
        ("A rule missing its enforcement line", ["enforced-by"])
    ]
    ledger = project / "kyosei" / "procedures.md"
    original = ledger.read_text(encoding="utf-8")
    assert kyo("doctor").code == 0
    ledger.write_text(original + _KYO413_NO_ENFORCED_BY, encoding="utf-8")
    result = kyo("doctor")
    assert result.code == EXIT_DOCTOR
    assert "'A rule missing its enforcement line' has no enforced-by bullet" in result.err
    assert "reaches nobody" in result.err

    # The remedy the finding names -- add the missing bullet -- clears it.
    fixed = _KYO413_NO_ENFORCED_BY.replace(
        "- **audience**: worker\n", "- **audience**: worker\n- **enforced-by**: aspirational\n"
    )
    ledger.write_text(original + fixed, encoding="utf-8")
    assert "A rule missing its enforcement line" not in kyo("doctor").err
