"""KYO-13.c1 (law 3): the human-facing hook document ``templates/hooks/README.md``
and the packaged fragments ``kyo install-hooks`` actually reads
(``src/kyosei/defaults/hooks/``) must never drift — one is documentation for a
reader, the other is the installed source, and nothing enforces them
independently unless a test does. Until KYO-624 ``templates/hooks/`` also held a
byte-identical copy of every fragment; it was removed, since the packaged files are
as readable in the source tree, and the README's list of fragments is what is
checked against the packaged set now.

KYO-103 adds the third direction. Those files do not only describe themselves;
the ``SubagentStop`` docs *quote* the standing-rules ledger, and a quotation has
a second source that can move under it. KYO-97 corrected the store-write rule —
its ``enforced-by`` line had credited ``kyo doctor`` with a branch check that
command has never had — and all the sites that quote the rule went on
presenting the pre-KYO-97 sentence, misattribution included, as the ledger's
current text.

**Who actually reads each site (KYO-166 correction).** This
docstring said, from the day KYO-103 wrote it, that ``kyo install-hooks``
writes the hook comment into a consumer's ``.claude/settings.json``. It never
did, and the order of the two records shows it:
``install_hooks._strip_comment_fields`` started stripping the ``comment`` key
from every hook entry in **KYO-13**, and this sentence was written in
**KYO-103**, after it — so this was never drift between a true sentence and a
mechanism that moved underneath it; it described behaviour that had already been
deliberately removed and tested against.
``tests/test_install_hooks.py::test_install_hooks_strips_the_comment_field``
proves the stripping through the real ``kyo install-hooks`` CLI path, and
:func:`test_the_comment_field_never_reaches_a_consumers_settings_json` below
proves the same fact directly against the packaged fragment, self-contained in
this file's own write scope, so this sentence cannot go stale a second time
without a check in *this* file failing too. So only one of the two sites
ships anything a consumer's own project ever holds, and it does not ship the
comment:

* ``templates/hooks/README.md`` ships with this repository — read by a
  maintainer or contributor browsing the source tree, never installed, never
  filtered.
* ``src/kyosei/defaults/hooks/subagent-stop.json`` ships inside the installed
  wheel, but only its ``command`` field reaches a consumer's
  ``.claude/settings.json``; its ``comment`` field, carrying the ledger
  quotation, is read only by someone opening the packaged fragment directly —
  in ``site-packages`` after a real install, or in this source tree. A
  smaller, more technical audience than "every consumer looking at their own
  settings.json", which is the direction the old sentence's severity
  judgements were wrong in.

Nothing failed, because nothing compared a quotation of the ledger against the
ledger.

The last section is that comparison, and its reach is exactly two sites.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from kyosei.install_hooks import (
    PATH_GUARD_MESSAGE,
    PATH_GUARD_PREFIX,
    _strip_comment_fields,
    guarded,
    hook_action,
)
from kyosei.procedures import parse_procedures
from tests.prose_text import flatten_prose

REPO_ROOT = Path(__file__).resolve().parents[1]

HOOK_FILES = (
    "session-start.json",
    "sessions.json",
    "stop.json",
    "subagent-stop.json",
    "pre-tool-use.json",
    # KYO-398.c6: the per-prompt build-change check.
    "prompt-submit.json",
)

#: KYO-31: healing steps run before the verification step. `kyo doctor` last is not
#: a preference — sequenced ahead of a remedy in the same `&&` chain it short-circuits
#: that remedy, which is how the doctor-first recipe made `html_freshness` permanent.
#: KYO-215 retired `kyo html` from the middle of this chain; the rule about order is
#: what survives it, and `tests/test_install_hooks.py` holds the behavioural half.
#: KYO-362 added `--hook` to the last command: one line of stderr and the path of the
#: full report, instead of the whole report, which is what a failing doctor used to put
#: in a consumer's terminal on every stop. A change of PRINTING only — the same checks
#: run and the exit status is unchanged (`tests/test_doctor.py` holds that half) — and
#: it reaches an installed chain through `install_hooks.SUPERSEDED_HOOK_COMMANDS`, since
#: element containment can no more see a flag than it can see a removal.
#: KYO-591 added `--hook` to the first command: silent, exit 0, where no Kyosei project
#: resolves, and exactly the bare `kyo sweep` inside one.
STOP_RECIPE = "kyo sweep --hook && kyo doctor --hook"

#: KYO-186: the second fragment on `SessionStart`, `Stop` and `SessionEnd`. One
#: command under three events, which tells them apart by the payload's own
#: `hook_event_name` rather than by three commands that could drift.
SESSIONS_RECIPE = "kyo sessions --hook"


PACKAGED_HOOKS = REPO_ROOT / "src" / "kyosei" / "defaults" / "hooks"
HOOKS_README = REPO_ROOT / "templates" / "hooks" / "README.md"
FRAGMENT_LIST_HEADING = "## The packaged fragments"


def undocumented_fragments(readme: str, packaged: set[str]) -> set[str]:
    """Packaged fragment names the README's fragment list does not name."""
    listed = readme.split(FRAGMENT_LIST_HEADING, 1)
    if len(listed) < 2:
        return set(packaged)
    return {name for name in packaged if f"`{name}`" not in listed[1]}


def test_the_readme_lists_every_packaged_fragment() -> None:
    """KYO-624: the README describes the packaged fragments rather than holding copies
    of them, so a fragment added to the package without a line in that list is a hook
    the document does not mention."""
    packaged = {path.name for path in PACKAGED_HOOKS.glob("*.json")}
    assert packaged == set(HOOK_FILES), sorted(packaged)
    missing = undocumented_fragments(HOOKS_README.read_text(encoding="utf-8"), packaged)
    assert not missing, f"templates/hooks/README.md does not list {sorted(missing)}"


def test_a_fragment_missing_from_the_readme_list_is_reported() -> None:
    readme = HOOKS_README.read_text(encoding="utf-8").replace("`stop.json`", "`stop`")
    assert undocumented_fragments(readme, set(HOOK_FILES)) == {"stop.json"}


def test_templates_hooks_holds_no_copy_of_a_fragment() -> None:
    """KYO-624: one copy of each fragment, the packaged one. A copy re-added under
    templates/hooks/ would be a second source with no check holding it equal."""
    assert not list((REPO_ROOT / "templates" / "hooks").glob("*.json"))


def test_the_comment_field_never_reaches_a_consumers_settings_json() -> None:
    """KYO-166.c2. The module docstring above claims the packaged ``comment``
    field never survives into a consumer's ``.claude/settings.json``; this ties
    that claim to the mechanism that makes it true (``install_hooks._strip_comment_fields``),
    read directly off the real packaged fragment, so the docstring cannot state
    the opposite again without this failing too. This is the check
    ``tests/test_install_hooks.py::test_install_hooks_strips_the_comment_field``
    cannot be, since it is outside this file's write scope and this file must not
    depend on a claim about itself staying true only because another file's test
    happens to still exist.
    """
    packaged = json.loads(
        (REPO_ROOT / "src" / "kyosei" / "defaults" / "hooks" / "subagent-stop.json").read_text(
            encoding="utf-8"
        )
    )
    (group,) = packaged["SubagentStop"]
    hooks = group["hooks"]
    assert hooks and all("comment" in hook for hook in hooks), (
        "the packaged fixture no longer carries a comment field to strip; this test "
        "would pass vacuously"
    )
    stripped = _strip_comment_fields(hooks)
    assert all("comment" not in hook for hook in stripped)
    # And the field that DOES ship is untouched -- the mechanism drops exactly one key.
    assert [h["command"] for h in stripped] == [h["command"] for h in hooks]


def test_both_copies_of_the_stop_fragment_run_the_ordered_recipe() -> None:
    """KYO-31.c1. The whole defect was an ordering inside one string that every other
    comparison in the codebase is blind to."""
    for directory in (PACKAGED_HOOKS,):
        fragment = json.loads((directory / "stop.json").read_text(encoding="utf-8"))
        commands = [
            hook["command"] for group in fragment["Stop"] for hook in group["hooks"]
        ]
        # KYO-316 put the PATH guard ahead of the recipe. The ordering this test
        # exists for is the ordering of the RECIPE, so the guard is stripped and the
        # fact that it is there is asserted below, where it is the subject.
        assert commands == [guarded(STOP_RECIPE)], f"{directory}: {commands}"
        assert [hook_action(c) for c in commands] == [STOP_RECIPE], f"{directory}: {commands}"


# ------------------------------------------------- KYO-316: the missing-`kyo` guard
#
# What a hook says when `kyo` is not on this shell's PATH. Measured first, per event,
# against Claude Code 2.1.278: a not-found hook command exits 127 and
# every event this project installs into (SessionStart, PreToolUse, Stop, SubagentStop,
# SessionEnd) treats that as a NON-blocking error -- the action proceeds and the only
# thing anyone sees is a `<hook name> hook error` notice carrying the FIRST LINE of
# stderr, which was `/bin/sh: kyo: command not found`. So the guards silently did not
# run and the notice named no remedy. The fragments now say one line instead.
#
# These are behavioural checks against a real `/bin/sh`, not a spelling check on the
# constant: the guard's whole job is done by the shell, and the shape that matters --
# does it print, what status does it exit with, does the guarded command still run and
# still decide the exit status when `kyo` IS there -- is invisible to string equality.


def packaged_hook_commands() -> list[tuple[str, str]]:
    """``(fragment name, command)`` for every hook in every packaged fragment."""
    found: list[tuple[str, str]] = []
    for name in HOOK_FILES:
        fragment = json.loads(
            (REPO_ROOT / "src" / "kyosei" / "defaults" / "hooks" / name).read_text(
                encoding="utf-8"
            )
        )
        for groups in fragment.values():
            for group in groups:
                for hook in group["hooks"]:
                    found.append((name, hook["command"]))
    return found


def run_hook(
    command: str, *, path: str, cwd: str | Path = "/tmp"
) -> subprocess.CompletedProcess[str]:
    """Run a packaged hook command exactly as Claude Code does — one string, one
    ``/bin/sh`` — with ``PATH`` set to ``path`` and nothing else inherited that could
    put a `kyo` back in reach. ``cwd`` is the hook's working directory: since KYO-591
    the guard speaks only where a Kyosei project marker is found from there."""
    env = {"PATH": path, "HOME": os.environ.get("HOME", "/tmp")}
    return subprocess.run(
        ["/bin/sh", "-c", command],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


def test_every_packaged_hook_carries_the_path_guard() -> None:
    """The mechanism itself: a fragment that lost the guard is a hook that goes back
    to `/bin/sh: kyo: command not found` on a machine without the CLI."""
    commands = packaged_hook_commands()
    # KYO-398 added the eighth: UserPromptSubmit's `kyo check-build --hook`.
    assert len(commands) == 8, commands
    for name, command in commands:
        assert command.startswith(PATH_GUARD_PREFIX), f"{name}: {command!r}"
        # ...and the guard is a prefix, never the whole thing: a hook that guards and
        # then runs nothing would pass the assertion above and do no work at all.
        assert hook_action(command).startswith("kyo "), f"{name}: {command!r}"


def test_the_guard_says_one_actionable_line_when_kyo_is_not_on_path(tmp_path: Path) -> None:
    """KYO-316.c2. Run for real, with a PATH that has no `kyo` on it: each packaged
    hook must print the one line naming both remedies and exit non-blocking, instead of
    the shell's bare `command not found`. In a Kyosei project: KYO-591 made the guard
    silent everywhere else (``tests/test_hooks_silent_outside_project.py``)."""
    (tmp_path / "kyosei").mkdir()
    (tmp_path / "kyosei" / "config.yml").write_text("prefix: TST\n", encoding="utf-8")
    for name, command in packaged_hook_commands():
        result = run_hook(command, path="/nonexistent-for-this-test", cwd=tmp_path)
        assert result.stdout == "", f"{name}: the message must not go to stdout"
        printed = result.stderr.strip().splitlines()
        # One line, because Claude Code renders the FIRST line of stderr and nothing else.
        assert printed == [PATH_GUARD_MESSAGE], f"{name}: {result.stderr!r}"
        assert "command not found" not in result.stderr, f"{name}: {result.stderr!r}"
        # 127, the code the shell already returned for this condition -- a non-blocking
        # error on every event this project installs into. Never 2, which on PreToolUse
        # would refuse every Bash call in the project and on Stop/SubagentStop would
        # prevent a stop nobody woken can unblock.
        assert result.returncode == 127, f"{name}: {result.returncode}"
        assert result.returncode != 2


def test_the_message_names_the_install_and_no_retired_app_action() -> None:
    """A line that says only that something is missing is the shell's line with extra
    steps. KYO-316.c2's bar was the remedies; since KYO-450 there is one route, the
    installed CLI -- the desktop app ships no `kyo` of its own, so its "Install command
    line tools" action (KYO-315) is gone and a line still offering it would name a menu
    item that does not exist."""
    assert "uv tool install kyosei" in PATH_GUARD_MESSAGE
    assert "Install command line tools" not in PATH_GUARD_MESSAGE
    assert "Kyosei app" not in PATH_GUARD_MESSAGE
    assert "\n" not in PATH_GUARD_MESSAGE
    # One quote-free line: it sits inside `echo '...'` in every packaged command.
    assert "'" not in PATH_GUARD_MESSAGE


def test_installing_kyo_clears_the_message_and_the_hook_runs(tmp_path: Path) -> None:
    """CLAUDE.md's standing rule: an error naming a remedy ships with a test proving
    the remedy clears it. The remedy here is a `kyo` on PATH, whichever of the two ways
    put it there, so the test is a `kyo` on PATH — and what must then be true is not
    only that the message is gone but that the hook's own command ran and its own exit
    status survived the guard."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "kyo"
    # Echoes its arguments so the test can see WHICH command ran, and exits 3 so a
    # guard that swallowed the status would be caught: 3 is neither 0 nor 127.
    stub.write_text('#!/bin/sh\necho "kyo $*"\nexit 3\n', encoding="utf-8")
    stub.chmod(0o755)

    for name, command in packaged_hook_commands():
        result = run_hook(command, path=f"{bin_dir}:/usr/bin:/bin")
        assert PATH_GUARD_MESSAGE not in result.stderr, f"{name}: {result.stderr!r}"
        # The stub echoes what it was called as, so this says the hook's OWN first
        # command ran -- the guard fell through to it rather than standing in for it.
        first_command = hook_action(command).split("&&")[0].strip()
        assert result.stdout.strip() == first_command, f"{name}: {result.stdout!r}"
        assert result.returncode == 3, f"{name}: the hook's own exit status was not kept"


# ------------------- KYO-103: the SubagentStop docs quote the ledger, and stayed behind it
#
# WHAT THIS COVERS, AND WHAT IT DELIBERATELY DOES NOT — stated first, because a check
# that oversells its reach is the same defect this record is about:
#
#   * TWO SITES. :data:`QUOTE_SITES` and no others. Every other file in this repo may
#     quote `procedures.md` however it likes and nothing here looks. That is KYO-93's and
#     KYO-97's precedent applied again: a general "is this quotation of the ledger still
#     current" verifier is a real idea and a different, larger record. This one pins the
#     quotations that actually went stale, one of which ships to consumers.
#   * ONE RULE, TWO FRAGMENTS. Only the fragments in :data:`PINNED_QUOTATIONS`, and only
#     against the `enforced-by` bullet of "Never write to the structured store directly".
#     A site that quotes some *other* sentence of that rule, or any other rule, passes.
#   * THE PACKAGED LEDGER. The comparison reads `src/kyosei/defaults/procedures.md`. That
#     the store copy `kyosei/procedures.md` says the same thing is carried by KYO-95's
#     rule-by-rule comparison in `tests/test_procedures.py`, not asserted a second time.
#   * NOT A SPELLING CHECK ON THE PROSE AROUND THE QUOTE. The surrounding sentences may
#     describe the guard wrongly and still pass; what is pinned is the quoted text.
#
# The pin runs both ways on purpose. Each fragment must be in the ledger *and* in both
# sites, so the ledger cannot move without the quotations failing, and a quotation cannot
# be reworded without failing against the ledger. Neither side can drift alone.

LEDGER = REPO_ROOT / "src" / "kyosei" / "defaults" / "procedures.md"
LEDGER_REL = "src/kyosei/defaults/procedures.md"

STORE_RULE_TITLE = "Never write to the structured store directly"

#: The site paths, relative to the repo root. Until KYO-624 a third site, the templates/
#: copy of the JSON fragment, was listed here; it was removed with that copy.
QUOTE_SITES = (
    "templates/hooks/README.md",
    "src/kyosei/defaults/hooks/subagent-stop.json",
)

#: The two fragments of the store-write rule's ``enforced-by`` line those two sites
#: quote, written the way the ledger writes them. Every comparison below runs both sides
#: through :func:`~tests.prose_text.flatten_prose` first — soft wrapping collapsed and
#: markup dropped — so the ledger's marked-up, hard-wrapped bullet, the README's own
#: wrapping and a plain-text JSON comment all compare equal, and neither has to
#: reproduce the others' punctuation. (Underscores are markup to that reader, so
#: ``cmd_guard.py`` flattens to ``cmdguard.py`` on both sides at once; writing the
#: flattened form here instead would read as a typo and invite a "fix" that breaks it.)
#: Both fragments are asserted to be substrings of the ledger, not assumed to be, which is
#: what makes this constant a pin rather than a fourth copy of the sentence.
PINNED_QUOTATIONS = (
    "`kyo check-branch` (`src/kyosei/cmd_guard.py`), which the `SubagentStop` hook "
    "invokes at every worker stop",
    "exit 2 on a branch that diffs anything under `kyosei/` or `ideas/` relative to its "
    "merge-base with `main`",
)

#: The sentence all the sites presented as current until KYO-103, quoted from the
#: packaged procedures.md as it stood before that record rather than restated, so
#: it cannot rot — the same discipline as `tests/test_procedures.py`'s PRE_KYO97_STORE_RULE.
#: `kyo doctor` opens the store and validates its contents; it imports no git query and has
#: never looked at a branch. `kyo check-branch` (KYO-13.c5) is the guard that does this.
PRE_KYO97_CLAIM = (
    "a worker's worktree holds no copy of `kyosei/kyosei.db`; `kyo doctor` and a merge "
    "guard fail a branch that diffs anything under `kyosei/`"
)


def store_rule_enforced_by(ledger_text: str) -> str:
    """The store-write rule's ``enforced-by`` bullet, flattened for comparison."""
    matches = [r for r in parse_procedures(ledger_text) if r.title == STORE_RULE_TITLE]
    assert len(matches) == 1, [r.title for r in parse_procedures(ledger_text)]
    return flatten_prose(matches[0].enforced_by)


def quotes(haystack: str, quotation: str) -> bool:
    """Is ``quotation`` present in ``haystack``, both read as flattened prose?"""
    return flatten_prose(quotation) in flatten_prose(haystack)


def stale_ledger_quotations(ledger_text: str, sites: dict[str, str]) -> list[str]:
    """Every way the pinned sites and the ledger disagree about the quoted text."""
    problems: list[str] = []

    rule = store_rule_enforced_by(ledger_text)
    for quotation in PINNED_QUOTATIONS:
        if not quotes(rule, quotation):
            problems.append(
                f"{LEDGER_REL}: the store-write rule's enforced-by line no longer says "
                f"{quotation!r}, but {', '.join(QUOTE_SITES)} quote it as its current "
                f"text — update the quotations in the same commit as the rule"
            )
    if quotes(rule, PRE_KYO97_CLAIM):
        problems.append(
            f"{LEDGER_REL}: the store-write rule is back to its pre-KYO-97 wording, which "
            f"credits `kyo doctor` with a branch check that command has never had"
        )

    for rel, text in sites.items():
        for quotation in PINNED_QUOTATIONS:
            if not quotes(text, quotation):
                problems.append(
                    f"{rel}: no longer quotes {quotation!r} from the store-write rule in "
                    f"{LEDGER_REL} — quote the rule's current text, or point at the rule "
                    f"instead of quoting it and drop this site from QUOTE_SITES"
                )
        if quotes(text, PRE_KYO97_CLAIM):
            problems.append(
                f"{rel}: presents the pre-KYO-97 store-write sentence — `kyo doctor` and a "
                f"merge guard fail a branch that diffs anything under kyosei/ — as the "
                f"ledger's text. KYO-97 removed it; `kyo doctor` has never had that check"
            )
    return problems


def _sites() -> dict[str, str]:
    return {rel: (REPO_ROOT / rel).read_text(encoding="utf-8") for rel in QUOTE_SITES}


def test_the_subagent_stop_docs_quote_the_ledgers_current_store_write_rule() -> None:
    """KYO-103.c1/c2. The two shipped quotations and the README's are read off the ledger
    rather than proof-read, so KYO-97's correction cannot land in the rule alone again."""
    problems = stale_ledger_quotations(LEDGER.read_text(encoding="utf-8"), _sites())
    assert not problems, "\n".join(
        ["hook docs quoting a version of the store-write rule the ledger does not have:", *problems]
    )


def test_a_site_that_still_quotes_the_pre_kyo97_sentence_fails() -> None:
    """KYO-103.c2's failing case, demonstrated on the real defect rather than a fabricated
    one: the README as it stood before this record, quoting the misattribution."""
    sites = _sites()
    sites["templates/hooks/README.md"] = (
        "the merge guard procedures.md already advertises (\"a worker's worktree holds no "
        "copy of `kyosei/kyosei.db`; `kyo doctor` and a merge guard fail a branch that "
        'diffs anything under `kyosei/`"), made mechanical instead of aspirational.'
    )
    problems = stale_ledger_quotations(LEDGER.read_text(encoding="utf-8"), sites)
    assert any("presents the pre-KYO-97 store-write sentence" in p for p in problems), problems
    assert all(p.startswith("templates/hooks/README.md") for p in problems), problems


def test_the_ledger_moving_alone_fails_even_with_every_site_untouched() -> None:
    """The other direction, which is the one that has no other net. A rewrite of the rule
    that left both quotations in place would otherwise be silent."""
    ledger = LEDGER.read_text(encoding="utf-8")
    # Matched across soft wrapping (KYO-35's lesson): this repo hard-wraps, so a phrase in
    # the bullet is as likely to span a line break as not, and a fixed-string replace would
    # quietly change nothing and prove nothing.
    weakened = r"its\s+merge-base\s+with\s+`main`"
    reverted, count = re.subn(weakened, "`main`", ledger)
    assert count == 1, (
        f"{weakened!r} matched {count} times in the ledger — this test is no longer "
        "weakening the sentence it means to weaken, so it is proving nothing"
    )
    problems = stale_ledger_quotations(reverted, _sites())
    assert any(p.startswith(LEDGER_REL) for p in problems), problems


def test_the_pinned_quotations_are_not_vacuous() -> None:
    """A pin made of text that appears nowhere would pass every assertion above by being
    absent from both sides at once — except that both sides are required to contain it.
    This states the trap explicitly: each fragment is substantial and distinct."""
    assert len(PINNED_QUOTATIONS) == 2
    for quotation in PINNED_QUOTATIONS:
        assert len(quotation.split()) >= 8, quotation
    assert not quotes(store_rule_enforced_by(LEDGER.read_text(encoding="utf-8")), PRE_KYO97_CLAIM)
