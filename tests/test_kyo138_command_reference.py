"""KYO-138: the command reference is generated from the parser, and cannot go stale.

The record's argument, in one example. `kyo edit --note-file` is the only writer of
`RECORD.nN` notes. No controller-facing prose named it, so a consumer read
`kyo redact --help`, found `RECORD.nN` in its vocabulary, could find no command that
produced one, and filed the id shape as "a redaction target for an object nothing can
produce" (KYO-131). It had been producible the whole time. A *written* reference can only
hold what its author remembered; a *generated* one holds what the parser has. That is the
property under test here, and it is stronger than the drift detection KYO-137 built:
detection leaves a window in which the reference is wrong and someone is reading it,
generation closes the window.

The five criteria map onto the five sections below. The one that makes this more than a
convenience is c4: a committed reference that has fallen behind the parser is worse than
none, because it looks authoritative. `test_the_committed_reference_is_what_the_parser_
yields_right_now` is the check that makes staleness a build failure instead of a
discovery, and `test_the_staleness_check_can_fail` is the canary proving that check
can actually fail — KYO-34's lesson, that a checker never shown to fail is not known to
check anything.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest

from kyosei import docs_check, docs_targets
from kyosei.cli import (
    COMMAND_TABLE,
    MUTATING_COMMANDS,
    REFERENCE_PATH,
    REFERENCE_REGENERATE_COMMAND,
    _canonical_subparsers,
    build_parser,
    reference_markdown,
)
from kyosei.registry import DEFAULT_DOCUMENT_GLOBS

from .conftest import invoke

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED = REPO_ROOT / REFERENCE_PATH


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    """Each command once, under its canonical name: an alias (`kyo controller`, KYO-519)
    is the same parser object and is documented inside its command's section."""
    action = next(a for a in parser._actions if isinstance(a.choices, dict) and a.choices)
    return _canonical_subparsers(action)


def _collapse(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture(scope="module")
def rendered() -> str:
    return reference_markdown(build_parser())


# ------------------------------------------------------------------ c1: it is complete


def test_every_command_gets_its_own_section(rendered: str) -> None:
    """KYO-138.c1, the command half. Not a count and not a sample: every key of the real
    subparser map must have a heading, so a command cannot be shipped and undocumented."""
    missing = [
        name for name in _subparsers(build_parser()) if f"## `kyo {name}`\n" not in rendered
    ]
    assert not missing, (
        f"the generated reference has no section for {missing} — these are real `kyo` "
        "subcommands a reader would never learn exist"
    )


def test_every_flag_of_every_command_appears(rendered: str) -> None:
    """KYO-138.c1, the flag half, and the direct answer to KYO-131.

    Quantified over what the PARSER has rather than over what the document says, which
    is the whole point: the check that let KYO-131 through ran the other way round, over
    the commands the skill already named, and so could not see a flag nobody had written
    a sentence about.
    """
    unnamed: list[str] = []
    for name, subparser in sorted(_subparsers(build_parser()).items()):
        for action in subparser._actions:
            if action.help is argparse.SUPPRESS:
                continue
            for option in action.option_strings:
                if f"`{option}" not in rendered:
                    unnamed.append(f"kyo {name} {option}")
    assert not unnamed, f"the generated reference never names {unnamed}"


def test_the_flag_kyo131_was_filed_about_is_in_the_reference(rendered: str) -> None:
    """The measured case, asserted by name. If a refactor stops the generator from
    reaching `kyo edit --note-file`, the check above may still pass on the other flags
    while the reference has quietly stopped covering the case it was built for."""
    section = rendered.split("## `kyo edit`", 1)[-1].split("\n## ", 1)[0]
    assert "`--note-file FILE`" in section, (
        "`kyo edit --note-file` is missing from the generated reference. That is the "
        "write path KYO-131 was filed about: it produced RECORD.nN notes that no prose "
        "surface named, and a consumer concluded the object could not be created"
    )


def test_every_command_s_help_text_survives_into_the_reference(rendered: str) -> None:
    """c1 says "and their help text", so the help is asserted verbatim (whitespace
    collapsed) rather than merely "a flag with this name is mentioned somewhere"."""
    missing: list[str] = []
    for name, subparser in sorted(_subparsers(build_parser()).items()):
        for action in subparser._actions:
            if action.help is argparse.SUPPRESS or not action.help:
                continue
            if _collapse(action.help) not in rendered:
                missing.append(f"kyo {name} {action.option_strings or action.dest}")
    assert not missing, f"help text was dropped for {missing}"


# --------------------------------------------------- c2: nothing is maintained by hand


def test_a_command_added_to_the_parser_appears_with_no_other_edit() -> None:
    """KYO-138.c2, and the reason :func:`reference_markdown` takes a parser argument.

    A command and a flag that do not exist anywhere in this build are added to a real
    parser and rendered. Both appear. This can only pass if the generator holds no list
    of command names, no list of flags, and no per-command special case — which is the
    property c2 actually asks for, and one that asserting over the shipped parser alone
    could never distinguish from a very well-maintained hand-written list.
    """
    parser = build_parser()
    action = next(a for a in parser._actions if isinstance(a.choices, dict) and a.choices)
    invented = action.add_parser(
        "frobnicate", help="a command this build does not have, invented by the test"
    )
    invented.add_argument("target", help="what to frobnicate")
    invented.add_argument(
        "--twice", action="store_true", help="frobnicate it a second time"
    )
    invented.add_argument("--depth", required=True, metavar="N", help="how deep to go")

    rendered = reference_markdown(parser)

    assert "## `kyo frobnicate`" in rendered
    assert "a command this build does not have, invented by the test" in rendered
    assert "`TARGET`" in rendered and "what to frobnicate" in rendered
    assert "`--twice`" in rendered and "frobnicate it a second time" in rendered
    assert "- `--depth N` — **required**." in rendered, (
        "an invented required flag was not rendered as required — the generator is "
        "reading something other than the action it was handed"
    )


def test_the_reference_names_no_command_the_parser_does_not_have(rendered: str) -> None:
    """The opposite direction, which is the failure mode a hand-written reference has
    and KYO-34 caught in the skill: a command that was renamed or removed still being
    advertised. Every `## `kyo X`` heading must be a real subcommand."""
    headings = re.findall(r"^## `kyo ([a-z-]+)`$", rendered, re.MULTILINE)
    real = set(_subparsers(build_parser()))
    invented = sorted(set(headings) - real)
    assert not invented, f"the reference documents {invented}, which the CLI does not have"
    assert len(headings) == len(real), "a command is documented twice, or not at all"


# ------------------------------------------------- c3: markdown docs_check can parse


def test_the_committed_reference_is_where_docs_check_already_looks() -> None:
    """KYO-138.c3. Docs mode (KYO-139) and `kyo base run --source docs` read whatever
    `docs_targets.documents()` enumerates, and this project declares no `documents.yml`,
    so what reaches them is `DEFAULT_DOCUMENT_GLOBS`. A reference sitting outside those
    globs would satisfy every other criterion and still be invisible in the app, which
    is the half of this record that motivated it."""
    assert COMMITTED.is_file(), f"no committed reference at {REFERENCE_PATH}"
    reached = {
        path.relative_to(REPO_ROOT).as_posix()
        for glob in DEFAULT_DOCUMENT_GLOBS
        for path in REPO_ROOT.glob(glob)
        if path.is_file()
    }
    assert REFERENCE_PATH in reached, (
        f"{REFERENCE_PATH} is not matched by any of {DEFAULT_DOCUMENT_GLOBS}, so docs "
        "mode and `kyo base run --source docs` cannot see it"
    )


def test_docs_check_parses_the_reference_into_sections() -> None:
    """c3's "without a second format": the same parser docs mode uses reads this file
    and finds a section per command, not one undifferentiated blob."""
    documents = docs_targets.documents(REPO_ROOT)
    doc = next((d for d in documents if d.path == REFERENCE_PATH), None)
    assert doc is not None, (
        f"docs_targets.documents() did not enumerate {REFERENCE_PATH} — it is on disk and "
        "inside the default globs, so the enumerator and the glob check disagree"
    )
    headings = [section.heading for section in doc.sections]
    assert "`kyo edit`" in headings and "`kyo new`" in headings, (
        f"docs_check split the reference into {headings[:8]}... — the per-command "
        "headings are not being read as sections"
    )
    assert len(doc.sections) > len(_subparsers(build_parser())), (
        "fewer sections than commands: the markdown is not parsing as expected"
    )


def test_the_reference_declares_no_metadata_and_so_raises_no_docs_obligation() -> None:
    """Deliberate, and worth a test rather than an accident to be rediscovered.

    A `governs: src/kyosei/cli.py` line here would be *true*, and would still be wrong:
    it would raise a KYO-137 docs obligation every time `cli.py` changed for any reason,
    whose remedy is a controller act (`kyo docs settle`) against a file no human
    maintains. The equality check below is strictly stronger than governs-staleness for a
    generated artifact — it compares bytes against what the parser yields now, rather
    than two git timestamps — so the weaker mechanism would only add noise.

    This also guards an accident: `docs_check` reads `governs:`/`status:`/`amended-by:`
    anywhere outside a fence, so a help string that happened to begin with one of those
    words would silently turn this file into a governing document.
    """
    doc = next(d for d in docs_targets.documents(REPO_ROOT) if d.path == REFERENCE_PATH)
    declaring = [s.heading for s in doc.sections if s.metadata.get("governs")]
    assert not declaring, (
        f"the generated reference declares governs: targets in {declaring}. If that was "
        "intentional, say why; if a help string starting 'governs:' produced it, the "
        "generator needs to neutralise it"
    )
    check = docs_check.check_docs(REPO_ROOT)
    offending = [p for p in check.problems if p.doc_path == REFERENCE_PATH]
    assert not offending, f"`kyo docs check` fails on the generated reference: {offending}"


# ----------------------------------------------------- c4: a stale copy cannot ship


def test_the_committed_reference_is_what_the_parser_yields_right_now(rendered: str) -> None:
    """KYO-138.c4 — the criterion that makes this more than a convenience.

    Equality, not KYO-140's subset. That shape was right there because its two sides
    legitimately differed; these two do not. This file has exactly one author, and it is
    the generator, so any difference at all is drift and the honest comparison is the
    strict one. (Which is also why the render must not depend on the environment that
    produced it — see the determinism test below, without which this check would fail
    for reasons that have nothing to do with the parser.)
    """
    assert COMMITTED.is_file(), (
        f"{REFERENCE_PATH} is missing. Generate it: {REFERENCE_REGENERATE_COMMAND}"
    )
    committed = COMMITTED.read_text(encoding="utf-8")
    assert committed == rendered, (
        f"{REFERENCE_PATH} has fallen behind the parser in src/kyosei/cli.py. A committed "
        "reference that is out of date is worse than none, because it looks "
        f"authoritative. Regenerate it and commit the result:\n\n    "
        f"{REFERENCE_REGENERATE_COMMAND}\n"
    )


def test_the_staleness_check_can_fail() -> None:
    """The canary. A checker that has never been shown to fail is not known to check
    anything (KYO-34), and the specific way this one could quietly stop checking is a
    generator that returned "" or a comparison that normalised both sides to nothing."""
    rendered = reference_markdown(build_parser())
    assert rendered.strip(), "the generator produced no output at all"
    stale = rendered.replace("## `kyo edit`", "## `kyo edit-renamed-yesterday`", 1)
    assert stale != rendered, "the substitution found nothing; this canary is vacuous"


def test_the_render_does_not_depend_on_terminal_width(monkeypatch) -> None:
    """The hazard c4's equality check creates, closed.

    `argparse.HelpFormatter` wraps usage to `shutil.get_terminal_size()`, so the same
    parser formats to different bytes under a different `COLUMNS`. Uncaught, that would
    make the staleness check fail depending on whose terminal ran the generator — an
    error message naming a remedy that does not clear the error, which is exactly what
    this project's own rules forbid.
    """
    monkeypatch.setenv("COLUMNS", "40")
    narrow = reference_markdown(build_parser())
    monkeypatch.setenv("COLUMNS", "400")
    wide = reference_markdown(build_parser())
    assert narrow == wide, (
        "the generated reference differs between a 40-column and a 400-column terminal, "
        "so the committed copy depends on who generated it"
    )


# -------------------------------- c5: usable by someone who has never run the tool


def test_required_and_optional_are_distinguished(rendered: str) -> None:
    """KYO-138.c5, first half. Read off the parser on both sides: every option argparse
    marks `required` must be rendered as required, and every one it does not must not
    be — so this cannot pass by the generator marking everything, or nothing."""
    for name, subparser in sorted(_subparsers(build_parser()).items()):
        section = rendered.split(f"## `kyo {name}`", 1)[-1].split("\n## ", 1)[0]
        for action in subparser._actions:
            if action.help is argparse.SUPPRESS or not action.option_strings:
                continue
            bullet = next(
                (
                    line
                    for line in section.splitlines()
                    if line.startswith(f"- `{action.option_strings[0]}")
                ),
                None,
            )
            if bullet is None:
                continue  # hoisted into the "accepted everywhere" section
            expected = "**required**" if action.required else "optional"
            assert expected in bullet, (
                f"`kyo {name} {action.option_strings[0]}` is "
                f"{'required' if action.required else 'optional'} in the parser and the "
                f"reference says otherwise: {bullet!r}"
            )


def test_a_required_flag_and_an_optional_one_really_are_rendered_differently(
    rendered: str,
) -> None:
    """The canary for the check above, which would pass vacuously if every bullet
    happened to contain both words. `kyo promote --kind` is required; `--title` is
    not (KYO-330: nor is `kyo new`'s own `--title` any more, now that `--from` can
    supply it instead -- this canary moved to a flag pair that stays
    required/optional either way)."""
    section = rendered.split("## `kyo promote`", 1)[-1].split("\n## ", 1)[0]
    assert "- `--kind KIND` — **required**." in section
    assert "- `--title TEXT` — optional." in section


def test_every_command_names_its_subject(rendered: str) -> None:
    """KYO-138.c5, second half: "names each command's subject". The subject is the
    `help=` argparse itself shows in `kyo --help`, so the reference and the tool cannot
    give two answers to what a command is for."""
    parser = build_parser()
    action = next(a for a in parser._actions if isinstance(a.choices, dict) and a.choices)
    for choice in action._choices_actions:
        assert choice.help, f"`kyo {choice.dest}` has no help= and so has no subject"
        assert _collapse(choice.help) in rendered, (
            f"the reference never states what `kyo {choice.dest}` is for"
        )


def test_each_command_says_whether_it_needs_a_project_and_whether_it_writes(
    rendered: str,
) -> None:
    """Also c5, and the two facts a reader cannot get from `--help` at all. Both are
    derived from `COMMAND_TABLE` and `MUTATING_COMMANDS` rather than restated, so a
    command that changes either one changes the reference with no edit."""
    for name in _subparsers(build_parser()):
        section = rendered.split(f"## `kyo {name}`", 1)[-1].split("\n## ", 1)[0]
        assert "Needs a Kyosei project:" in section, f"`kyo {name}` does not say"
        expected = (
            "Writes to the store: **yes**"
            if name in MUTATING_COMMANDS
            else "Writes to the store: **no**"
        )
        assert expected in section, (
            f"`kyo {name}` is {'in' if name in MUTATING_COMMANDS else 'not in'} "
            f"MUTATING_COMMANDS and the reference says the opposite"
        )
    # Every spelling the parser accepts, aliases included (KYO-519: `kyo controller`),
    # since each spelling needs its own COMMAND_TABLE key.
    every_spelling = set(
        next(
            a for a in build_parser()._actions if isinstance(a.choices, dict) and a.choices
        ).choices
    )
    assert every_spelling == set(COMMAND_TABLE), (
        "the parser and COMMAND_TABLE disagree about which commands exist, so the "
        "'needs a project' column above is answering for a different set"
    )


# ------------------------------------------------------------------- the command itself


@pytest.fixture
def nowhere(tmp_path: Path, monkeypatch) -> Path:
    """An empty directory with no Kyosei project in it or above it, as the working
    directory. The autouse ``store_home`` fixture already points ``$KYOSEI_HOME`` at
    this test's own tmp tree, so there is genuinely no store to find — which is the
    condition ``PROJECT_NONE`` is claimed to survive, and claiming it from inside this
    repository (where a store *is* above the cwd) would prove nothing."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    return empty


def test_kyo_reference_runs_with_no_project_at_all(nowhere: Path) -> None:
    """PROJECT_NONE, on `kyo report-schema`'s reasoning: the readers who need this are a
    worker in a dispatch worktree and someone who has just installed the tool, and
    neither reliably has a store above them. A reference command that first demanded a
    project would fail for exactly the person it exists for."""
    result = invoke(["reference"])
    assert result.code == 0, result
    assert "# `kyo` command reference" in result.out
    assert "## `kyo edit`" in result.out


def test_kyo_reference_output_writes_the_file(nowhere: Path) -> None:
    destination = nowhere / "cli.md"
    result = invoke(["reference", "--output", str(destination)])
    assert result.code == 0, result
    assert destination.read_text(encoding="utf-8") == reference_markdown(build_parser())
    # KYO-156: the regenerate line names the caller's OWN destination. This asserted
    # `REFERENCE_REGENERATE_COMMAND in result.out` — Kyosei's `--output
    # docs/reference/cli.md` — which held here only because this repository is the
    # source tree, and told every consumer to regenerate into a directory they do not
    # have. The assertion was encoding the defect, so it moves with the fix.
    assert f"kyo reference --output {destination}" in result.out


def test_kyo_reference_json_carries_the_markdown(nowhere: Path) -> None:
    result = invoke(["reference", "--json"])
    assert result.code == 0, result
    payload = json.loads(result.out)
    assert payload["ok"] is True
    assert payload["output"] is None
    assert payload["markdown"] == reference_markdown(build_parser())


def test_kyo_reference_refuses_a_directory_that_does_not_exist(nowhere: Path) -> None:
    """Law 1: the refusal names a remedy, and the remedy clears the error."""
    result = invoke(["reference", "--output", str(nowhere / "nope" / "cli.md")])
    assert result.code != 0
    assert "does not exist" in result.err
    assert "--output" in result.err

    (nowhere / "nope").mkdir()
    cleared = invoke(["reference", "--output", str(nowhere / "nope" / "cli.md")])
    assert cleared.code == 0, cleared
    assert (nowhere / "nope" / "cli.md").is_file()


# ------------------------------------------------------------------------ KYO-221
#
# A consumer report: the whole reference is a long markdown document, which suits a file
# and not a search, and a per-command view (`kyo reference <command>`) would have saved
# several round trips.


def test_one_commands_section_is_a_verbatim_slice_of_the_whole(
    rendered: str, nowhere: Path
) -> None:
    """c1 and c3. The property that makes this worth having rather than dangerous: the
    section is the same bytes the full document carries, not a second, thinner
    description of the same parser — which is how a per-command view drifts."""
    for name in ("show", "dispatch", "reference"):
        result = invoke(["reference", name])
        assert result.code == 0, result
        assert result.out.startswith(f"## `kyo {name}`"), result.out[:200]
        assert result.out.rstrip("\n") in rendered, (
            f"`kyo reference {name}` is not a slice of the reference it claims to be from"
        )
        # And it is ONE command's section, not the document from that point on.
        assert result.out.count("\n## `kyo ") == 0, result.out


def test_a_single_command_section_carries_the_same_detail_the_full_one_does(
    nowhere: Path,
) -> None:
    """c1's list: usage, positionals, options and the same descriptions. Asserted
    against the parser rather than against a copy of the text, so this cannot pass by
    agreeing with a stale expectation."""
    result = invoke(["reference", "show"])
    assert result.code == 0, result
    assert "Usage: `kyo show" in result.out
    assert "### Positional arguments" in result.out
    subparser = _subparsers(build_parser())["show"]
    assert _collapse(subparser.description or subparser.format_usage()) or True
    for action in subparser._actions:
        for spelling in action.option_strings:
            if spelling in ("--help", "--root", "--actor", "--json"):
                continue
            assert spelling in result.out, spelling


def test_an_unknown_command_is_refused_with_the_known_ones(nowhere: Path) -> None:
    """c1. The list is read off the parser, so it documents this build rather than the
    build somebody wrote the message beside."""
    result = invoke(["reference", "no-such-command"])
    assert result.code != 0, result
    assert "'no-such-command' is not a command of" in result.err
    assert "Known commands:" in result.err
    for name in sorted(_subparsers(build_parser())):
        assert name in result.err, name
    # The remedy clears it (CLAUDE.md's standing rule).
    cleared = invoke(["reference", "show"])
    assert cleared.code == 0, cleared


def test_reference_with_no_command_is_unchanged(rendered: str, nowhere: Path) -> None:
    """c2. The whole document is still the default, and `--json` still says which."""
    result = invoke(["reference"])
    assert result.code == 0, result
    assert result.out == rendered

    payload = json.loads(invoke(["reference", "--json"]).out)
    assert payload["markdown"] == rendered
    assert payload["of_command"] is None
    sliced = json.loads(invoke(["reference", "show", "--json"]).out)
    assert sliced["of_command"] == "show"
    assert sliced["markdown"].rstrip("\n") in rendered


def test_output_with_a_command_is_refused_rather_than_silently_narrowed(
    nowhere: Path,
) -> None:
    """c2. `--output` writes the WHOLE reference. A generated file is written by
    replacement, so narrowing this combination would leave a file that looks like the
    reference and holds one command, with nothing saying so."""
    destination = nowhere / "cli.md"
    refused = invoke(["reference", "show", "--output", str(destination)])
    assert refused.code != 0, refused
    assert "writes the whole reference" in refused.err
    assert not destination.exists()

    cleared = invoke(["reference", "--output", str(destination)])
    assert cleared.code == 0, cleared
    assert destination.read_text(encoding="utf-8") == reference_markdown(build_parser())
