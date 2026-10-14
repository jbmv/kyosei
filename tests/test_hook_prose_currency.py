"""KYO-36.c2/c3 — prose that states a packaged hook command is derived, or fails.

We ship consumers two kinds of thing about hooks, and both already have a check:
their *settings* falling behind the packaged fragment is `kyo doctor`'s
``hook_config``; their *installed skill* falling behind the packaged one is
`kyo doctor`'s ``skill`` check. The third kind had none — this project's own
*prose about* the fragment, which fell behind it twice without a word:
``README.md``'s milestone narrative and ``cmd_install_hooks``'s docstring both
still said ``Stop``/``SessionEnd`` → ``kyo doctor && kyo sweep`` after KYO-27
added ``kyo html`` and after KYO-31 reordered the chain. Fixing the strings buys
nothing durable; that is exactly what was done after KYO-27, and these two were
missed anyway. KYO-215 removed ``kyo html`` from the chain again, and this check is
why that removal reaches every sentence describing it in the same run.

So the statement is derived rather than restated (law 3, and the same discipline
as ``tests/test_hook_templates_sync.py`` and the DDL hash pinned to
``schema_version``): every prose sentence of the form

    <event> → <kyo command chain>
    <event> hooks already run <kyo command>, <kyo command>

anywhere in the scanned sources must name, in order, exactly the commands the
packaged fragment installs for that event. Change the fragment and every stale
description of it fails here, in the same run.

Reading is done on flattened prose (:mod:`tests.prose_text`) so a recipe wrapped
across a line break is still one statement — the same wrapping that hid KYO-35's
citation from a fixed-string grep.

Sites deliberately **not** scanned, and why:

* ``docs/design/charter.md`` — the founding design record, written before the
  build and frozen as history; it states what v1 was specified to do, and
  rewriting it to match what shipped would destroy the record of the difference.
* ``docs/reference/*`` — external evidence documents that quote other projects'
  settings files verbatim.
* ``tests/`` and ``src/kyosei/install_hooks.py``'s ``SUPERSEDED_HOOK_COMMANDS`` —
  these name *superseded* commands on purpose, and are already kept honest by
  ``tests/test_install_hooks.py``'s check that every superseded entry names a
  replacement this build installs.
"""

from __future__ import annotations

import json
import re

from kyosei.install_hooks import HOOK_INSTALL_TARGETS, hook_action
from kyosei.scaffold import defaults_dir
from tests.prose_text import REPO_ROOT, flatten_prose, iter_source_files, read_flat

SCAN_GLOBS = (
    "src/kyosei/**/*.py",
    "src/kyosei/**/*.md",
    "templates/**/*.md",
    # The skill (SKILL.md, and KYO-328's second tier REFERENCE.md) is read from its
    # packaged source under the src/kyosei/**/*.md glob above: this repository keeps
    # no .claude/skills/kyosei/ mirror since it moved onto the plugin (KYO-610).
    "README.md",
    # KYO-516: the README's reference sections moved here, and the check moved with them.
    "docs/reference/topics/*.md",
    "CLAUDE.md",
    # The contributor text CLAUDE.md imports with an @ line (KYO-517): moved, not
    # retired, so it keeps the check it had while it lived in CLAUDE.md.
    "AGENTS.md",
)
SCAN_EXCLUDES = ("src/kyosei/install_hooks.py",)

EVENT_ALT = r"SubagentStop|SessionStart|SessionEnd|PreToolUse|Stop"
#: ``kyo check-branch --against main`` — a long flag may take a value, and
#: dropping it would let prose name a *different* invocation of the same command
#: and still pass. The value may not itself start a new command.
COMMAND = r"kyo [a-z][a-z-]*(?:\s+--[a-z][a-z-]*(?:\s+(?!kyo\b)[a-z][\w./-]*)?)*"
#: KYO-298: the word alternatives carry ``\b`` on both sides, and that is a real fix
#: rather than tidying. ``_SPLIT_RE`` below applies this same alternation to a command
#: chain that has ALREADY matched, with no boundary of its own — so an unanchored
#: ``and`` split ``kyo check-command --pre-tool-use`` down the middle of its own
#: subcommand into ``['kyo check-comm', '--pre-tool-use']``, and the check then reported
#: that this build installs a command nobody had written. No `kyo` command spelled
#: ``and``, ``then`` or ``plus`` inside a word existed until `check-command`, which is
#: why a latent bug shipped in a check whose whole job is to be right about command
#: spellings. ``&&`` and ``,`` need no boundary: neither is a word character.
SEPARATOR = r"&&|,\s*\bthen\b|,\s*\band\b|,|\bthen\b|\band\b|\bplus\b"

#: ``<event list> [hooks] <arrow-or-verb> <kyo command list>``. The verb set is
#: deliberately narrow — present-tense statements about what the hook runs. A
#: sentence that merely mentions an event ("`kyo install-hooks` wires
#: SessionStart/Stop/SessionEnd/SubagentStop so briefing happens automatically")
#: states no recipe and is not matched.
STATEMENT_RE = re.compile(
    rf"(?P<events>\b(?:{EVENT_ALT})\b(?:\s*(?:/|,|and|or)\s*\b(?:{EVENT_ALT})\b)*)"
    r"\s*(?:hooks?\s*)?"
    r"(?:→|->|already\s+runs?|\bruns?\b)"
    rf"\s*(?P<commands>{COMMAND}(?:\s*(?:{SEPARATOR})\s*{COMMAND})*)"
)

_SPLIT_RE = re.compile(rf"\s*(?:{SEPARATOR})\s*")
_INNER_PAREN_RE = re.compile(r"\(([^()]*)\)")
_EVENT_RE = re.compile(rf"\b(?:{EVENT_ALT})\b")


def strip_asides(flat: str) -> str:
    """Drop parenthetical asides that state neither an event nor a command.

    ``Stop``/``SessionEnd`` already run ``kyo doctor`` (invariant checks) and
    ``kyo sweep`` (staleness flags — ...) is one statement of the recipe with two
    glosses wedged into it; without this it reads as two unrelated fragments and
    the check goes blind to exactly the site it exists for. Asides that *do*
    carry an event or a command are kept, since a whole recipe is sometimes
    written inside parentheses.
    """
    previous = None
    while previous != flat:
        previous = flat

        def drop(m: re.Match[str]) -> str:
            inner = m.group(1)
            return m.group(0) if ("kyo " in inner or _EVENT_RE.search(inner)) else " "

        flat = _INNER_PAREN_RE.sub(drop, flat)
    return re.sub(r"\s+", " ", flat)


def packaged_recipes() -> dict[str, list[list[str]]]:
    """Event -> one command list per packaged fragment that fires on that event.

    KYO-186 made this a list of recipes rather than one recipe, and the reason is
    a change in what an event MEANS rather than a convenience.

    Until then every event had exactly one fragment, so "the commands for this
    event" and "the recipe this event runs" were the same sentence, and prose could
    be held to reciting all of it. That record gives three events a SECOND and
    orthogonal purpose: `SessionStart` both briefs the session and records which
    Remote Control session it is, and `Stop`/`SessionEnd` both run the closeout
    chain and refresh that record. Under the old shape one of two things had to
    happen, and both are worse than this. Assigning per fragment reports whichever
    one `HOOK_INSTALL_TARGETS` happens to list last -- a check reading the wrong
    thing rather than failing. Concatenating them demands that every sentence about
    briefing also recite session recording, which would make a dozen carefully
    written passages across README.md, the skill and `cmd_install_hooks` worse in
    order to keep a check that is no longer asking the right question.

    KYO-298 moves the unit one level in, from the FRAGMENT to the hook GROUP, and
    it is the same argument at a finer grain rather than a new one. That record
    adds a second `PreToolUse` check -- `kyo check-command --pre-tool-use`, which
    refuses the whole suite inside a running dispatch's worktree -- to the fragment
    that already carries `kyo check-push --pre-tool-use`, as a second matcher group
    beside it (one group of two would make `install_hooks.merge_hooks` re-install
    check-push for every consumer who already had it; see
    ``tests/test_install_hooks.py``). The two answer different questions about the
    same tool call and each has its own section of prose, so concatenating them per
    fragment demands that every sentence about publication also recite the suite
    guard -- exactly the worse-prose outcome the paragraph above refused for
    `SessionStart`. A group is what `merge_hooks` installs, what `hook_drift`
    compares and what a reader sees in `.claude/settings.json`, so it is also the
    honest unit for "a recipe this build installs".

    Every existing fragment holds exactly one group, so nothing else changes: the
    `SubagentStop` pair stays ONE recipe (two hooks in one group) and prose must
    still recite both, in order.

    What KYO-36 exists to catch is unchanged and is checked exactly as sharply
    below: prose that describes a recipe THIS BUILD NO LONGER INSTALLS. Reorder
    the stop chain, or change the brief's flags, and every stale description of it
    still matches no recipe for its event and still fails.
    """
    recipes: dict[str, list[list[str]]] = {}
    for event, source_name in HOOK_INSTALL_TARGETS:
        fragment = json.loads((defaults_dir() / "hooks" / source_name).read_text(encoding="utf-8"))
        for groups in fragment.values():
            for group in groups:
                commands: list[str] = []
                for hook in group.get("hooks", []):
                    # KYO-316: the PATH guard is a precondition, not a step of the
                    # recipe -- prose that says what an event RUNS is describing the
                    # commands, and `install_hooks.hook_action` is the one place that
                    # distinction is drawn, so this reads it rather than re-deriving it.
                    action = hook_action(hook["command"])
                    commands += [p.strip() for p in action.split("&&") if p.strip()]
                if commands:
                    recipes.setdefault(event, []).append(commands)
    return recipes


def statements_in(flat: str) -> list[tuple[list[str], list[str]]]:
    """``(events, commands)`` for every hook-recipe statement in flattened prose."""
    found = []
    for m in STATEMENT_RE.finditer(strip_asides(flat)):
        events = _EVENT_RE.findall(m.group("events"))
        commands = [c.strip() for c in _SPLIT_RE.split(m.group("commands")) if c.strip()]
        found.append((events, commands))
    return found


def stale_hook_prose() -> list[str]:
    recipes = packaged_recipes()
    problems: list[str] = []
    for path in iter_source_files(SCAN_GLOBS, SCAN_EXCLUDES):
        rel = path.relative_to(REPO_ROOT).as_posix()
        for events, commands in statements_in(read_flat(path)):
            for event in events:
                expected = recipes.get(event)
                # A statement is current if it recites SOME fragment this build
                # installs for the event -- not if it recites all of them at once.
                # See `packaged_recipes` for why that is the right question now.
                if expected is None or commands in expected:
                    continue
                installed = " or ".join(" && ".join(recipe) for recipe in expected)
                problems.append(
                    f"{rel}: says {event} runs {' && '.join(commands)!r}; "
                    f"this build installs {installed!r} on that event"
                )
    return problems


def test_every_prose_statement_of_a_hook_recipe_matches_the_packaged_fragment() -> None:
    """KYO-36.c2. Change a packaged fragment and every prose copy that still
    describes the old one fails here — the check that did not exist when KYO-27
    and KYO-31 each left two descriptions behind."""
    problems = stale_hook_prose()
    assert not problems, "\n".join(
        ["prose describing a hook this build does not install:", *problems]
    )


def test_the_scan_actually_reaches_the_sites_this_record_was_filed_about() -> None:
    """KYO-36.c3. A check that quietly matched nothing would pass forever. These
    are the sites that state a recipe today; if a rewrite makes one of them stop
    parsing as a statement, this fails rather than silently losing coverage."""
    expected_sites = {
        "docs/reference/topics/operating.md",  # the README's operating guide (KYO-516)
        "src/kyosei/commands.py",
        "templates/hooks/README.md",
        "src/kyosei/harness/claude-code/plugin/skills/kyosei/SKILL.md",
        "src/kyosei/harness/claude-code/plugin/skills/kyosei/REFERENCE.md",
    }
    seen = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in iter_source_files(SCAN_GLOBS, SCAN_EXCLUDES)
        if statements_in(read_flat(path))
    }
    assert expected_sites <= seen, f"no hook-recipe statement found in {expected_sites - seen}"


# --------------------------------------------------------------- the check's own mechanism


def test_a_stale_recipe_statement_is_detected() -> None:
    packaged = packaged_recipes()["Stop"]
    stale = "`Stop`/`SessionEnd` → `kyo doctor && kyo sweep`"
    assert statements_in(flatten_prose(stale)) == [
        (["Stop", "SessionEnd"], ["kyo doctor", "kyo sweep"])
    ]
    assert ["kyo doctor", "kyo sweep"] not in packaged


def test_a_recipe_split_across_a_line_break_is_still_one_statement() -> None:
    """The KYO-35 lesson applied here: this repo hard-wraps, so a recipe is as
    likely to span a line break as not, and a line-at-a-time check would miss it."""
    wrapped = "    hook templates into settings (SessionStart ->\n    `kyo brief --compact`).\n"
    assert statements_in(flatten_prose(wrapped)) == [(["SessionStart"], ["kyo brief --compact"])]


def test_a_gloss_in_parentheses_does_not_break_the_statement() -> None:
    prose = (
        "`Stop`/`SessionEnd` already run `kyo doctor` (invariant checks) and `kyo sweep` "
        "(staleness flags -- `touch-changed`)."
    )
    assert statements_in(flatten_prose(prose)) == [
        (["Stop", "SessionEnd"], ["kyo doctor", "kyo sweep"])
    ]


def test_a_mention_that_states_no_recipe_is_not_a_statement() -> None:
    prose = "`kyo install-hooks` wires `SessionStart`/`Stop`/`SessionEnd`/`SubagentStop` so that"
    assert statements_in(flatten_prose(prose)) == []


def test_every_event_this_build_installs_has_a_derived_recipe() -> None:
    """If a new event is added to :data:`HOOK_INSTALL_TARGETS` and no fragment
    backs it, the comparison above would silently skip every statement about it."""
    recipes = packaged_recipes()
    assert set(recipes) == {event for event, _ in HOOK_INSTALL_TARGETS}
    assert all(recipe for recipes_for_event in recipes.values() for recipe in recipes_for_event)
    assert all(recipes_for_event for recipes_for_event in recipes.values())


def test_a_command_whose_own_name_contains_a_separator_word_is_not_split(
) -> None:
    """KYO-298: ``check-command`` contains ``and``, and that used to split it in half.

    :data:`_SPLIT_RE` applies the separator alternation to a chain that has already
    matched, so an unanchored ``and`` cut ``kyo check-command --pre-tool-use`` into
    ``['kyo check-comm', '--pre-tool-use']`` -- and the failure this check then printed
    named a command no one had written, about a fragment that was correct. No `kyo`
    command spelled a separator word inside a word until this one, which is exactly how
    a bug hides in a check whose job is to be right about command spellings. The
    separator words carry ``\\b`` on both sides now; this is what says so.
    """
    prose = "`PreToolUse` → `kyo check-command --pre-tool-use`"

    assert statements_in(flatten_prose(prose)) == [
        (["PreToolUse"], ["kyo check-command --pre-tool-use"])
    ]
    # ...and a genuine two-command chain still splits, so the fix did not buy this by
    # turning the separator off. Spelled `, then`, which is how this repository's prose
    # writes a chain: a bare `and` between two commands is swallowed by COMMAND's own
    # optional flag-VALUE group, which predates this record and is not what it is about.
    both = (
        "`SubagentStop` → `kyo check-clean --subagent-stop`, then "
        "`kyo check-branch --subagent-stop --against main`"
    )
    assert statements_in(flatten_prose(both)) == [
        (
            ["SubagentStop"],
            ["kyo check-clean --subagent-stop", "kyo check-branch --subagent-stop --against main"],
        )
    ]


def test_two_groups_on_one_event_are_two_recipes_not_one() -> None:
    """KYO-298: the unit is the hook GROUP, so each `PreToolUse` check stands alone.

    `merge_hooks` installs a group, `hook_drift` compares a group, and a reader sees a
    group in `.claude/settings.json`. Concatenating the two would demand that every
    sentence about the publication guard also recite the suite guard -- the worse-prose
    outcome KYO-186 already refused one level up, at the fragment.
    """
    recipes = packaged_recipes()["PreToolUse"]

    assert ["kyo check-push --pre-tool-use"] in recipes
    assert ["kyo check-command --pre-tool-use"] in recipes
    # The SubagentStop pair is two hooks in ONE group and stays one recipe, so prose
    # about it must still recite both in order.
    assert packaged_recipes()["SubagentStop"] == [
        ["kyo check-clean --subagent-stop", "kyo check-branch --subagent-stop --against main"]
    ]
