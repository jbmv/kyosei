"""KYO-13.c2: the `/kyosei` skill names only commands and flags that exist.

Extracts every backtick-quoted `kyo ...` invocation from the packaged skill,
`src/kyosei/harness/claude-code/plugin/skills/kyosei/SKILL.md`, and validates it
against the real argparse tree (`kyosei.cli.build_parser`) — an unknown subcommand
or an unknown `--flag` fails this test. Deliberately shallow otherwise (positional values, and
whether a *required* flag was included, are not checked): the point is that
nothing named in the skill can possibly be a typo or a stale command the CLI
no longer has, not that every example is a fully runnable command line.

KYO-34 adds a second dimension. The invocation check verifies what the skill
*calls*; it says nothing about what the skill *claims those calls produce*, and
the skill drifted in exactly that blind spot — it named five `kyo sweep` flag
classes, of which one existed. `test_the_skill_s_sweep_flag_classes_are_real`
below checks the claim against `contracts.FLAG_TYPES`.

KYO-92 adds a third. A readiness class the brief renders is something the
controller must act on, so the skill has to name it and name what clears it —
and `finished but not moved` shipped in KYO-87 without either, while its sibling
`scope unknown` had been explained since KYO-48.
`test_the_skill_names_the_withheld_readiness_class_the_surfaces_render` drives a
record into that class through the real CLI and checks the skill against what
`kyo next` actually printed, so the label and the remedy are read off the tool
rather than restated here.

KYO-131 adds a fourth, and it is the first one that runs in the opposite
direction. Every check above quantifies over what the skill ALREADY SAYS: the
invocation checks validate the commands the skill names, the sweep check reads
the classes the skill lists, and `doctor.check_skill` compares a consumer's copy
to the build's. All three are closed loops — none of them can see a command the
CLI has and the skill omits, because nothing in the skill points at it. So the
CLI grew `kyo edit --note-file`, no controller-facing prose ever named it, and
every one of these checks stayed green while a consumer read
`kyo redact --help`, found `RECORD.nN` in its vocabulary, could find no command
that produced one, and filed the id shape as "a redaction target for an object
nothing can produce". It had been producible the whole time.

`test_the_skill_names_every_prose_write_path_the_cli_exposes` closes that
direction. Its boundary is law 5: prose enters this store only by file or
stdin, so the `--*-file` flags ARE the write paths for stored prose, and the set
is read off the shipped parser rather than listed here. What it deliberately is
not is a command reference — see the honest-scope note on
:func:`_prose_write_paths` for what it cannot see and why generating the
reference (KYO-138) is the better answer to the exhaustive version.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

from kyosei import config, contracts
from kyosei.cli import build_parser
from kyosei.install_skill import installed_skill_text

from .conftest import new_record, valid_report, verify_criterion
from .reference_pages import OPERATING, RULES_AND_EXIT_CODES, STORE

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGED_SKILL_DIR = REPO_ROOT / "src/kyosei/harness/claude-code/plugin/skills/kyosei"
PACKAGED_SKILL_PATH = PACKAGED_SKILL_DIR / "SKILL.md"
#: KYO-328's second tier: the incident histories, installed beside the workflow.
PACKAGED_REFERENCE_PATH = PACKAGED_SKILL_DIR / "REFERENCE.md"

#: KYO-328.c1's measured ceiling for the WORKFLOW tier, in bytes.
#:
#: The skill was 54,823 bytes (`wc -c`, the commit before this record's
#: branch), loaded whole into every controller session and re-read every turn. The
#: record asked for the workflow tier to be at most half of that, and this is the
#: number: 54823 // 2. It is a ceiling on ONE tier — `REFERENCE.md` is read on demand
#: and is deliberately not counted, which is the whole shape of the change.
#:
#: This is not a style rule. A workflow that creeps back over the line is a session
#: cost nobody is billed for at the moment they add the paragraph, which is exactly how
#: it got to 54,823 in the first place. If a future record decides the ceiling is
#: wrong, raise it here deliberately and say why — what must not happen again is the
#: third thing, which is nobody deciding.
#:
#: Raised deliberately by KYO-428 from 27,411 to 27,738 (+327 bytes, the tier was at
#: 27,410): KYO-427 decided the skill must teach the new `--json` defaults (`--full`,
#: `--pretty`) and point agents at `kyo list --unsettled`, and the paragraph that does
#: so is four lines in section 1. The controller may prefer to buy the bytes back by
#: moving history to REFERENCE.md instead; this is the decision made, not a default.
#:
#: Raised deliberately by KYO-528 from 27,738 to 27,811 (+73 bytes): the skill must name
#: the bulk forms of `kyo flag resolve` and `kyo docs settle`, one clause each in the
#: paragraphs that already named those commands.
#:
#: Raised deliberately by KYO-565 from 27,811 to 27,924 (+113 bytes, the tier was at
#: 27,811): `kyo rules add` writes prose into the store (`--reason-file`), and the KYO-131
#: and KYO-302 checks in this file require the skill to name such a path and its flags.
#: One clause, in the paragraph that already named `kyo rules`.
#:
#: Raised deliberately by KYO-566 from 27,924 to 27,981 (+57 bytes, the tier was at
#: 27,924): the Ideas paragraph sent orchestrators to `ideas/scratchpad.md`, which every
#: reader skips, and KYO-566.c2 requires it to name the real scratchpad by its path form
#: and the command that prints its root, and to send a lasting thought to its own file.
#:
#: Raised deliberately by KYO-586 from 27,981 to 28,217 (+236 bytes, the tier was at
#: 27,981): the session-start brief prints a `PRIOR-ART:` line that only the operator can
#: answer, and the workflow must say to ask and how to record either answer. Three lines in
#: section 1 also name `kyo prior-art` and `--no-prior-art`, which retires KYO-585's four
#: debt entries below; the paragraph behind them is in REFERENCE.md.
#: Raised by KYO-602 from 28,217 to 28,218 (+1 byte): the renamed command is one byte longer
#: than its old name, and the tier names it once.
WORKFLOW_TIER_CEILING_BYTES = 28_218

#: One backtick-quoted inline-code span.
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


def _kyo_invocations(text: str) -> list[str]:
    """Every inline-code span that is itself a `kyo ...` (or bare `kyo`)
    invocation — filters out unrelated inline code (file paths, hook names,
    other tool names).

    KYO-362: a span may be a CHAIN. The hook recipes are written
    `kyo sweep && kyo doctor --hook`, which is one span and two invocations, and
    reading it whole made the validator ask `kyo sweep` about a flag belonging to the
    command two words along. Latent until then only because no chain in this prose had
    ever carried a flag; the chains have been written this way since KYO-27.
    """
    invocations: list[str] = []
    for span in INLINE_CODE_RE.findall(text):
        for part in span.split("&&"):
            command = part.strip()
            if re.match(r"^kyo(\s|$)", command):
                invocations.append(command)
    return invocations


def _subcommands():
    parser = build_parser()
    action = next(a for a in parser._actions if a.choices and hasattr(a, "choices"))
    return action.choices  # dict: name -> subparser


def _flags_for(subparser) -> set[str]:
    flags: set[str] = set()
    for action in subparser._actions:
        flags.update(action.option_strings)
    return flags


def test_skill_md_exists() -> None:
    assert PACKAGED_SKILL_PATH.is_file(), f"expected {PACKAGED_SKILL_PATH} to exist"


def test_skill_names_at_least_one_kyo_invocation() -> None:
    """A canary against the extractor itself silently matching nothing (e.g. if
    SKILL.md's formatting changes) and this test passing vacuously."""
    text = PACKAGED_SKILL_PATH.read_text(encoding="utf-8")
    assert len(_kyo_invocations(text)) >= 5


@pytest.mark.parametrize(
    "invocation",
    _kyo_invocations(PACKAGED_SKILL_PATH.read_text(encoding="utf-8")),
)
def test_every_named_kyo_invocation_matches_the_real_cli(invocation: str) -> None:
    tokens = shlex.split(invocation)
    assert tokens[0] == "kyo"
    if len(tokens) == 1:
        return  # bare "kyo" — nothing to validate

    subcommands = _subcommands()
    command = tokens[1]
    assert command in subcommands, (
        f"SKILL.md names `{invocation}`, but {command!r} is not a real `kyo` subcommand "
        f"(known: {sorted(subcommands)})"
    )
    subparser = subcommands[command]
    allowed_flags = _flags_for(subparser)
    for token in tokens[2:]:
        if not token.startswith("--"):
            continue
        flag = token.split("=", 1)[0]
        assert flag in allowed_flags, (
            f"SKILL.md names `{invocation}`, but {flag!r} is not a real flag of "
            f"`kyo {command}` (known: {sorted(allowed_flags)})"
        )


# ------------------------------------------------------------------- KYO-527

#: Bare flags the skill writes in a sentence that names no command, whose owner is NOT
#: the command named in the sentence before. Each maps to the one command the prose
#: means (or None for a flag that belongs to another tool). A flag that is real
#: somewhere else is exactly what KYO-527 shipped wrong -- "A rerun is safe (`--again`
#: re-applies)" followed `kyo accept`, and `--again` is `kyo verify`'s -- so the
#: escape hatch names its owner and the owner is checked, not waved through.
IMPLICIT_FLAG_OWNERS: dict[str, str | None] = {
    "--reviewed": "accept",
    "--close": "accept",
    "--except": "accept",
    "--terminated-no-report": "dispatch",
    "--no-verify": None,  # git's, in the check-push sentence
}


def _sentences(paragraph: str) -> list[str]:
    return re.split(r"(?<=[.;:!?)*])\s+(?=[A-Z*`(])", paragraph)


def _bare_flags_with_no_owner(text: str) -> list[tuple[str, str]]:
    """KYO-527. `(flag, sentence)` for every flag the prose writes as bare inline code
    in a sentence that names no `kyo` command, where the flag is not one of the
    commands named in the nearest earlier sentence of the same paragraph that names
    one, and is not claimed by `IMPLICIT_FLAG_OWNERS`. The invocation check above
    cannot see these: it reads only spans that begin `kyo`."""
    subcommands = _subcommands()
    problems: list[tuple[str, str]] = []
    for paragraph in re.split(r"\n\s*\n", text):
        previous: list[str] = []
        for sentence in _sentences(paragraph):
            commands: list[str] = []
            flags: list[str] = []
            for span in INLINE_CODE_RE.findall(sentence):
                span = span.strip()
                named = re.match(r"^kyo\s+([a-z-]+)", span)
                if named and named.group(1) in subcommands:
                    commands.append(named.group(1))
                elif span.startswith("--"):
                    flags.append(span.split()[0].split("=", 1)[0])
            if flags and not commands and previous:
                for flag in flags:
                    if flag in IMPLICIT_FLAG_OWNERS:
                        owner = IMPLICIT_FLAG_OWNERS[flag]
                        if owner is None or flag in _flags_for(subcommands[owner]):
                            continue
                    elif any(flag in _flags_for(subcommands[c]) for c in previous):
                        continue
                    problems.append((flag, " ".join(sentence.split())))
            if commands:
                previous = commands
    return problems


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(PACKAGED_SKILL_PATH, id="packaged"),
    ],
)
def test_no_bare_flag_is_attributed_to_a_command_that_lacks_it(path: Path) -> None:
    """KYO-527.c2. A consumer project's worker ran `kyo accept --again` because the skill said
    "A rerun is safe (`--again` re-applies)" under `kyo accept`; `--again` is
    `kyo verify --from-report`'s. Remedy: name the command in the sentence
    (`kyo verify --from-report <id> --again`), which the invocation check then
    validates, or add the flag to `IMPLICIT_FLAG_OWNERS` with its real owner."""
    problems = _bare_flags_with_no_owner(path.read_text(encoding="utf-8"))
    assert not problems, (
        f"{path.name} writes a bare flag after a command that does not have it: "
        f"{problems}. Name the command in the sentence, or add the flag to "
        "IMPLICIT_FLAG_OWNERS with the command that really owns it"
    )


def test_the_bare_flag_check_catches_the_kyo527_sentence() -> None:
    """The canary: the sentence as shipped, in its paragraph, is refused; the fixed one
    is not; and an IMPLICIT_FLAG_OWNERS entry naming the wrong owner is not a way out."""
    old = (
        "It writes no gate: it only gets the `kyo gate` line printed (section 5).\n"
        "A rerun is safe (`--again` re-applies)."
    )
    assert [f for f, _ in _bare_flags_with_no_owner(old)] == ["--again"]
    fixed = old.replace(
        "(`--again` re-applies)", "; `kyo verify --from-report <id> --again` re-applies"
    )
    assert _bare_flags_with_no_owner(fixed) == []
    for flag, owner in IMPLICIT_FLAG_OWNERS.items():
        if owner is not None:
            assert flag in _flags_for(_subcommands()[owner]), (flag, owner)


# ------------------------------------------------------------------ KYO-328
#
# The skill is two tiers. Everything above is about the workflow tier; these are about
# the reference beside it, and about the split staying a split.


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(PACKAGED_REFERENCE_PATH, id="packaged"),
    ],
)
def test_the_reference_exists_and_is_headed_by_the_workflows_sections(path: Path) -> None:
    """KYO-328.c1's pointer half, from the reference's side. The workflow cites this
    file by section name; a reference whose sections were renamed would leave those
    citations resolving to nothing, which is the failure the split could introduce."""
    assert path.is_file(), f"expected {path} to exist"
    text = path.read_text(encoding="utf-8")
    headings = {line.strip() for line in text.splitlines() if line.startswith("## ")}
    assert len(headings) >= 8, f"{path.name} has only {sorted(headings)}"




@pytest.mark.parametrize(
    "invocation",
    _kyo_invocations(PACKAGED_REFERENCE_PATH.read_text(encoding="utf-8"))
    if PACKAGED_REFERENCE_PATH.is_file()
    else [],
)
def test_every_kyo_invocation_in_the_reference_is_real(invocation: str) -> None:
    """The reference is prose a controller acts on, so it is held to the workflow's
    standard: an unknown command or flag in a story is a story that teaches a command
    that does not exist. KYO-13.c2's check, applied to the tier KYO-328 created."""
    tokens = shlex.split(invocation)
    assert tokens[0] == "kyo"
    if len(tokens) == 1:
        return
    subcommands = _subcommands()
    command = tokens[1]
    assert command in subcommands, (
        f"REFERENCE.md names `{invocation}`, but {command!r} is not a real `kyo` subcommand"
    )
    allowed = _flags_for(subcommands[command])
    for token in tokens[2:]:
        if token.startswith("--"):
            flag = token.split("=", 1)[0]
            assert flag in allowed, (
                f"REFERENCE.md names `{invocation}`, but {flag!r} is not a real flag of "
                f"`kyo {command}`"
            )


def test_the_reference_names_at_least_one_kyo_invocation() -> None:
    """The extractor's canary, as everywhere else in this file: a reference that
    stopped quoting commands would make the check above pass vacuously."""
    assert len(_kyo_invocations(PACKAGED_REFERENCE_PATH.read_text(encoding="utf-8"))) >= 20


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(PACKAGED_SKILL_PATH, id="packaged"),
    ],
)
def test_every_workflow_section_names_the_reference_section_that_carries_its_stories(
    path: Path,
) -> None:
    """KYO-328.c1: the workflow names the reference at the top and at each section
    whose history moved. Stated as "every numbered section", because a section that
    quietly stops pointing is how a reader comes to believe the story is gone rather
    than one read away — and `tests/test_citation_sections_resolve.py` then proves each
    named section exists in the file named. The packaged copy names the reference as
    ``${CLAUDE_SKILL_DIR}/REFERENCE.md`` (KYO-593.c3), so it is read as installed.
    """
    text = installed_skill_text(path.read_text(encoding="utf-8"))
    assert ".claude/skills/kyosei/REFERENCE.md" in text.split("## 0.", 1)[0], (
        f"{path.name}'s preamble must name the reference, or a reader meets eight "
        "citations of a file they were never introduced to"
    )
    sections = re.split(r"(?m)^## ", text)[1:]
    unpointed = [
        section.splitlines()[0]
        for section in sections
        if ".claude/skills/kyosei/REFERENCE.md" not in section
    ]
    assert not unpointed, (
        f"{path.name} sections with no pointer to their stories: {unpointed}"
    )


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(PACKAGED_SKILL_PATH, id="packaged"),
    ],
)
def test_the_workflow_tier_stays_under_its_ceiling(path: Path) -> None:
    """KYO-328.c1's measurement, kept measurable. The workflow is loaded whole into
    every controller session and re-read every turn, so its size is a recurring cost
    paid by every session of every consumer — and the histories that used to make up
    most of it now live in a file nobody pays for until they read it.

    The ceiling is checked in BYTES, which is what the record specified and what the
    em-dashes in this prose make different from a character count.
    """
    size = path.stat().st_size
    assert size <= WORKFLOW_TIER_CEILING_BYTES, (
        f"{path.name} is {size:,} bytes, over the {WORKFLOW_TIER_CEILING_BYTES:,}-byte "
        "ceiling KYO-328 set (half what the skill was before the split). Move the "
        "history to REFERENCE.md and keep the instruction here, or raise the ceiling "
        "deliberately in this file and say why"
    )


def test_the_reference_carries_what_the_workflow_no_longer_does() -> None:
    """The canary on the split itself: a reference that was never filled, or was
    emptied by a later edit, would let the ceiling above be met by DELETING the
    histories rather than moving them — which is the one outcome KYO-328 rules out
    ("nothing a controller is told to DO is lost", and nothing a controller may need
    to understand is either).
    """
    reference = PACKAGED_REFERENCE_PATH.read_text(encoding="utf-8")
    assert PACKAGED_REFERENCE_PATH.stat().st_size > PACKAGED_SKILL_PATH.stat().st_size, (
        "the reference is smaller than the workflow — the stories were cut, not moved"
    )
    # Three incidents the workflow used to tell in full, sampled across its sections
    # (KYO-620 kept each finding and dropped its dated counts).
    for marker in (
        "tens of kilobytes",
        "recovered its uncommitted files",
        "KYO-87's defect reached by the other road",
    ):
        assert marker in reference, f"the reference no longer carries {marker!r}"


def test_the_packaged_skill_names_only_real_commands() -> None:
    """KYO-15.c1: the sync check covers the packaged copy, not just this repo's.
    A consumer runs the packaged one, so that is the copy whose staleness costs
    somebody something."""
    packaged = PACKAGED_SKILL_PATH.read_text(encoding="utf-8")
    invocations = _kyo_invocations(packaged)
    assert invocations, "found no `kyo ...` invocations in the packaged skill"
    subcommands = _subcommands()
    for invocation in invocations:
        tokens = shlex.split(invocation)
        if len(tokens) == 1:
            continue
        command = tokens[1]
        assert command in subcommands, (
            f"the packaged skill names `{invocation}`, but {command!r} is not a real "
            "`kyo` subcommand"
        )
        allowed = _flags_for(subcommands[command])
        for token in tokens[2:]:
            if token.startswith("--"):
                flag = token.split("=", 1)[0]
                assert flag in allowed, (
                    f"the packaged skill names `{invocation}`, but {flag!r} is not a "
                    f"real flag of `kyo {command}`"
                )


# ------------------------------------------------------------------- KYO-34

#: The skill's enumeration of what `kyo sweep` flags: the parenthetical that
#: opens with the literal "staleness flags", read up to its closing paren.
#:
#: Scope, stated honestly (KYO-34.c2): this recognises a flag class as "named"
#: only inside that one parenthetical. It is deliberately not a whole-file scan
#: for flag-class-shaped words — hyphenated lowercase tokens are ordinary
#: English in this file ("in-flight", "zero-ceremony", "near-duplicate"), so a
#: whole-file matcher would either need an allowlist that rots or would fire on
#: prose. The narrow version provably catches every name in the list that
#: introduces itself as a sweep flag class, which is the shape the drift took
#: and the shape a future edit to this sentence would take. A flag class
#: invented in some *other* sentence of the skill is not covered; if the skill
#: ever grows a second such list, anchor it the same way and add it here.
SWEEP_FLAG_CLASSES_RE = re.compile(
    r"staleness flags\s*(?:--|—|–|:)?\s*(?P<body>[^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)

#: Split the enumeration into items. Commas only: the sentence is a prose list,
#: and bounding on the paren already keeps the surrounding sentence out.
_ITEM_SPLIT_RE = re.compile(r",")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def _slugify_flag_class(item: str) -> str:
    """Normalise one enumerated item to the shape a `FLAG_TYPES` entry has.

    `` `doc-stale `governs:` sections` `` → ``doc-stale-governs-sections``;
    ``aging asserted claims`` → ``aging-asserted-claims``; a name already
    written as a flag type (``touch-changed``) is returned unchanged. Leading
    conjunctions and markdown emphasis are dropped so that "and near-duplicate
    criteria" does not read as a different class from "near-duplicate criteria".
    """
    text = item.strip().strip("`*_").lower()
    text = re.sub(r"^(?:and|or)\s+", "", text)
    return _SLUG_STRIP_RE.sub("-", text).strip("-")


def _sweep_flag_classes(text: str) -> list[str]:
    match = SWEEP_FLAG_CLASSES_RE.search(text)
    assert match is not None, (
        "the skill no longer contains a 'staleness flags -- ...' parenthetical. "
        "That sentence is what this test checks against contracts.FLAG_TYPES; if it "
        "moved or was reworded, re-anchor SWEEP_FLAG_CLASSES_RE rather than deleting "
        "the check — otherwise the skill can claim sweep produces anything at all"
    )
    return [
        slug
        for slug in (_slugify_flag_class(item) for item in _ITEM_SPLIT_RE.split(match["body"]))
        if slug
    ]


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(PACKAGED_SKILL_PATH, id="packaged"),
    ],
)
def test_the_skill_s_sweep_flag_classes_are_real(path: Path) -> None:
    """KYO-34.c1/c2: the skill's sweep sentence names exactly `FLAG_TYPES`.

    The invocation checks above prove every `kyo sweep` the skill writes is a
    real command. They do not read the clause that says what it produces, and
    that clause named four flag classes (`moved-but-inverse`,
    `moved-but-not-merged`, aging asserted claims, doc-stale `governs:`
    sections) that were specified in the charter and never built. Both
    directions fail here: an invented class, and a shipped class the skill
    forgot to mention.
    """
    text = path.read_text(encoding="utf-8")
    named = _sweep_flag_classes(text)
    # KYO-59: `SWEEP_FLAG_TYPES`, not `FLAG_TYPES`. The two were the same tuple until
    # `kyo ungate` gained a flag class of its own, and this sentence is specifically
    # about what *sweep* produces — checked against the whole of FLAG_TYPES it would
    # demand the skill credit sweep with a class sweep does not raise, which is the
    # same untrue-claim failure in the opposite direction.
    real = set(contracts.SWEEP_FLAG_TYPES)
    invented = sorted(set(named) - real)
    missing = sorted(real - set(named))
    assert not invented and not missing, (
        f"{path.name}'s sweep sentence names {named}, but contracts.SWEEP_FLAG_TYPES holds "
        f"{sorted(real)}"
        + (
            f" — named but not built: {invented} (a specified-but-unbuilt flag class "
            "stated in the same voice as a shipped one is the drift KYO-34 fixed; say "
            "it in docs/design/charter.md, not in the file a controller reads every "
            "session)"
            if invented
            else ""
        )
        + (f" — built but unmentioned: {missing}" if missing else "")
    )


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(PACKAGED_SKILL_PATH, id="packaged"),
    ],
)
def test_the_skill_names_every_flag_class_somewhere(path: Path) -> None:
    """KYO-59: the sentence above is scoped to sweep, so on its own it would let a
    non-sweep flag class ship unmentioned — a controller would meet
    `gate-cleared-stale-criteria` in a brief with nothing in the workflow file
    saying what raises it or how it clears. Every member of `FLAG_TYPES` must appear
    somewhere in the skill; which sentence is the author's business."""
    text = path.read_text(encoding="utf-8")
    unmentioned = [name for name in contracts.FLAG_TYPES if name not in text]
    assert not unmentioned, (
        f"{path.name} never names {unmentioned} — a flag class a controller can be "
        "shown and the workflow file has never heard of"
    )


def test_the_sweep_flag_class_check_can_fail() -> None:
    """A canary on the checker itself: the extractor and the comparison really
    do reject a sentence that names a class `FLAG_TYPES` does not have. Without
    this, a regex that silently matched nothing would make the test above pass
    for the wrong reason — the exact failure mode that let the original drift
    survive `tests/test_skill_cli_sync.py` for a whole milestone."""
    prefix_drift = (
        "`kyo sweep` (staleness flags -- moved-but-inverse, moved-but-not-merged, "
        "aging asserted claims, doc-stale `governs:` sections, near-duplicate "
        "criteria). You do not need to remember to run either."
    )
    named = _sweep_flag_classes(prefix_drift)
    assert set(named) - set(contracts.SWEEP_FLAG_TYPES) == {
        "moved-but-inverse",
        "moved-but-not-merged",
        "aging-asserted-claims",
        "doc-stale-governs-sections",
    }
    assert "near-duplicate-criteria" in named  # the one of the five that exists


# ------------------------------------------------------------------- KYO-92

#: One `kyo next` heading for a class of records it is deliberately *withholding*
#: from the ready list. Both such classes print through this shape, which is what
#: lets the test below discover which one is holding a record without naming it.
WITHHELD_CLASS_HEADING_RE = re.compile(r"^\d+ record\(s\) (?P<label>.+?) \(NOT ready\):$")


def _withheld_class_holding(next_out: str, record: str) -> str:
    """The label `kyo next` printed over the withheld section that holds ``record``.

    Read off the output rather than asserted against a literal: the point of this
    check is that the skill agrees with whatever the surface calls the class, so
    the surface has to be the one that says it. Returns "" if the record appears
    in no withheld section (it is ready, or absent) — the caller asserts on that.
    """
    label = ""
    for line in next_out.splitlines():
        heading = WITHHELD_CLASS_HEADING_RE.match(line)
        if heading:
            label = heading["label"]
            continue
        if label and record in line:
            return label
    return ""


def _finished_but_not_moved(kyo, git_project) -> tuple[str, str]:
    """A record in the class KYO-87 added: dispatched, reported, every criterion
    settled, and never moved. Returns ``(record_id, kyo-next output)``.

    Built through the CLI in the consumer's own sequence — the dispatch reports,
    the controller verifies by hand, and the status move is the step nobody
    performs — because the class is *derived* from those facts and cannot be
    declared. Deliberately leaves the record `open`: reading the class off
    `status = built` is precisely what KYO-87 rejected.
    """
    record = new_record(kyo, kind="bug", title="Merged, and still open", criteria=["it works"])
    dispatched = kyo("dispatch", record, "--json")
    assert dispatched.code == 0, dispatched
    report_path = git_project / "report.json"
    report_path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatched.json["dispatch_id"],
                record_id=record,
                worked_from_sha=dispatched.json["worked_from_sha"],
            )
        ),
        encoding="utf-8",
    )
    ingested = kyo("report", dispatched.json["dispatch_id"], "--file", str(report_path))
    assert ingested.code == 0, ingested
    verify_criterion(kyo, f"{record}.c1")
    assert kyo("show", record, "--json").json["status"] == "open"

    listed = kyo("next")
    assert listed.code == 0, listed
    return record, listed.out


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(PACKAGED_SKILL_PATH, id="packaged"),
    ],
)
def test_the_skill_names_the_withheld_readiness_class_the_surfaces_render(
    path: Path, kyo, git_project
) -> None:
    """KYO-92.c1: the class, and the command that clears it, in the workflow file.

    The asymmetry this closes: `scope unknown` was explained to the controller and
    `finished but not moved` — same property, same surfaces, same audience, and a
    remedy that is a controller act nothing else will perform — was not. A class a
    controller is shown with nothing in the workflow saying what puts a record
    there or what takes it out is the KYO-34 failure in a new place.

    Scope, stated honestly: this checks the two halves that *are* derivable — the
    label the surface prints and the remedy it names, both read out of a real run.
    "What puts a record in it" is prose and is checked by a human; what cannot
    happen is the label or the remedy changing under the skill in silence.
    """
    record, listed = _finished_but_not_moved(kyo, git_project)
    text = path.read_text(encoding="utf-8")

    label = _withheld_class_holding(listed, record)
    assert label, (
        f"`kyo next` no longer withholds {record} from the ready list, or renamed the "
        "heading shape this test reads — re-anchor WITHHELD_CLASS_HEADING_RE rather "
        "than deleting the check"
    )
    assert label in text, (
        f"`kyo next` withholds records under {label!r} and {path.name} never says the "
        "words — a controller meets the class in the brief with nothing in the "
        "workflow file explaining it"
    )

    needs = kyo("brief", "--json").json["settled_not_moved"][0]["needs"]
    remedy = needs.replace(record, "<id>")
    assert remedy in text, (
        f"the surfaces tell the controller to run `{needs}` and {path.name} does not "
        f"name `{remedy}` — the remedy for this class is a controller act, so "
        "the workflow file is where it has to be written down"
    )


# ------------------------------------------------------------------ KYO-131


def _prose_write_paths() -> list[tuple[str, str]]:
    """Every ``(subcommand, flag)`` by which prose enters the store, read off the
    shipped parser.

    The boundary is law 5, not a taste call: multi-line prose has no flag-value
    form in this CLI at all, so a flag that takes a FILE is exactly a flag that
    writes stored text. That makes the set derivable — a command that grows a new
    way to write prose joins it without anyone remembering to add it here, which
    is the property the checks above lack and the reason KYO-131 got through them.

    Two exclusions, both to keep the check about write paths rather than about
    flags in general:

    * ``--why-file`` and its kind — any ``--X-file`` whose one-line twin ``--X``
      exists on the same command. Those are the uniform reason flags
      ``_add_reason_flags`` puts on nearly every mutating command; they record why
      a write happened, they do not create the object, and requiring the skill to
      name all eighteen of them would bury the six that matter in noise.
    * Nothing else. ``--file`` itself counts: it is how `kyo claim`, `kyo attach`
      and `kyo note` write their objects, and those are three of the four id
      shapes `kyo redact` names.

    Honest scope, in the tradition of the notes above. This asserts that the flag
    string and the command both appear SOMEWHERE in the skill — "names nowhere" is
    the failure KYO-131 was, and it is the one this catches. It does not check that
    the flag was named *for that command*, and it deliberately does not demand a
    runnable invocation per pair: that would make the skill a hand-written command
    reference, which is a document that should be GENERATED from this same parser
    (KYO-138) rather than maintained by a test that punishes the author for every
    flag they did not write a sentence about.

    **KYO-138 shipped, KYO-152 then met this note's own stated condition, and the
    check is still deliberately NOT deleted.** The history is kept rather than
    compressed, because each round of it narrowed what the check is actually for.

    The original note said this check would become redundant once the generated
    reference existed. KYO-138's dispatch found that wrong — a reference nothing
    points at is undiscoverable in practice — and named the missing step: the skill
    naming the reference. KYO-152 made exactly that change. `SKILL.md` and its
    packaged twin now tell a controller, in the preamble, that the file is the
    workflow and not the command reference, and that `kyo reference` prints the
    surface this document cannot promise to cover.

    So the stated condition is met, and on inspection the condition was still the
    wrong test. It asks whether the complete surface is *discoverable*. This check
    asks something strictly stronger: whether the workflow document itself names
    each prose write path, in the prose where a controller would use it. KYO-131
    was never a discoverability failure — `kyo edit --help` had listed `--note-file`
    the whole time. It was a reader who never learned they had a question: they
    reached a confident wrong answer from the surface they were taught to read, and
    filed a defect on it. A pointer to a generated reference lowers the odds of
    that. It cannot remove it, because acting on the pointer requires already
    suspecting you are wrong, which is the one thing that reader was not.

    Concretely, what deleting this check would leave unguarded: the skill could drop
    the record-note table in section 6 and the writer-naming sentence in section 5
    and stay green — the exact document KYO-131 was filed against, with only "run
    `kyo reference`" standing between a controller and the same wrong conclusion.
    Nothing else would notice. `doctor.check_skill` compares the two copies to each
    other, the invocation checks above quantify only over what the skill already
    says, and `tests/test_kyo138_command_reference.py` compares the reference to the
    parser and never reads the skill at all.
    """
    parser = build_parser()
    sub = next(a for a in parser._actions if a.choices)
    paths: list[tuple[str, str]] = []
    for command, subparser in sorted(sub.choices.items()):
        options = _flags_for(subparser)
        for flag in sorted(options):
            if flag != "--file" and not flag.endswith("-file"):
                continue
            if flag != "--file" and flag[: -len("-file")] in options:
                continue  # the file twin of a one-line text flag
            paths.append((command, flag))
    return paths


def test_the_prose_write_path_set_is_non_empty_and_holds_the_flag_kyo131_was_about() -> None:
    """A canary on the extractor, the same shape `test_skill_names_at_least_one_kyo
    _invocation` is: a parser walk that silently returned nothing would make the
    check below pass for the wrong reason. `edit --note-file` is asserted by name
    because it is the specific write path a consumer concluded did not exist — if a
    refactor drops it from the derived set, this check has stopped covering the case
    it was built for and should fail rather than quietly narrow."""
    paths = _prose_write_paths()
    assert len(paths) >= 10, f"the parser walk found only {paths} — it has stopped seeing flags"
    assert ("edit", "--note-file") in paths, (
        "`kyo edit --note-file` is no longer in the derived write-path set. That is the "
        "flag KYO-131 was filed about: it wrote RECORD.nN notes that no controller-facing "
        "surface named, and a consumer filed the id shape as unproducible"
    )
    assert ("note", "--file") in paths and ("claim", "--file") in paths


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(PACKAGED_SKILL_PATH, id="packaged"),
    ],
)
def test_the_skill_names_every_prose_write_path_the_cli_exposes(path: Path) -> None:
    """KYO-131.c4: the check in the direction the other skill checks cannot see.

    A controller learns this tool from the skill. A way of writing to the store
    that the skill names nowhere is, to them, a way that does not exist — and the
    cost is measured, not hypothetical: one consumer wrote up a real object as an
    unproducible one, and proposed deleting it from the redaction vocabulary,
    which would have removed the documented target for something the store holds.
    """
    text = path.read_text(encoding="utf-8")
    unnamed = [
        f"kyo {command} {flag}"
        for command, flag in _prose_write_paths()
        if flag not in text or f"kyo {command}" not in text
    ]
    assert not unnamed, (
        f"{path.name} never names {unnamed} — each is a way to write prose into the store "
        "that a controller reading this file end to end would never learn exists. That is "
        "the KYO-131 failure: `kyo edit --note-file` wrote RECORD.nN notes for milestones "
        "while `kyo redact --help` advertised the id shape, and a consumer reasonably "
        "concluded the object could not be created. Name the write path in the skill, or "
        "if the flag genuinely is not controller-facing, say why in _prose_write_paths"
    )


def test_the_write_path_check_can_fail() -> None:
    """A canary on the comparison itself (KYO-34's lesson: a checker that cannot be
    shown to fail is not known to check anything). A skill with a write path missing
    is detected as missing — and, specifically, the KYO-131 text is."""
    paths = _prose_write_paths()
    pretend_skill = "kyo edit is a command. kyo note --shift --file f appends to the log."
    unnamed = [
        f"kyo {command} {flag}"
        for command, flag in paths
        if flag not in pretend_skill or f"kyo {command}" not in pretend_skill
    ]
    assert "kyo edit --note-file" in unnamed, (
        "a skill that names `kyo edit` and never names `--note-file` is exactly the "
        "document KYO-131 was filed against, and this check must call it unnamed"
    )
    assert "kyo claim --file" in unnamed


# ------------------------------------------------------------------ KYO-197

#: The store home every project has, as the prose surfaces spell it. What
#: follows it -- when a directory segment follows it at all -- is the dispatch
#: worktree home, which is :data:`kyosei.config.WORKTREES_DIRNAME` and nothing else.
HOME_MARKER = "~/kyosei/<project>/"

#: Every prose surface that quotes a path under that home. The skill is read from its
#: packaged source, the one copy this repository keeps since it moved onto the plugin
#: (KYO-610 removed the ``.claude/skills/kyosei/`` mirror); the reference pages under
#: ``docs/reference/topics/`` quote the same home in the command cheatsheet, the store
#: layout and the guarantees table (README sections until KYO-516 moved them).
#:
#: The gap this closes: ``test_every_named_kyo_invocation_matches_the_real_cli``
#: validates every command and flag these documents name against the parser, and a
#: *path* a document quotes is neither, so KYO-89's rename of ``worktrees`` to
#: ``dispatch worktrees`` changed the constant and left all three documents saying
#: the old name, with nothing to notice. A consumer followed the skill, met the
#: tool's spelling, and filed the space as a bug.
HOME_QUOTING_DOCS = (
    pytest.param(PACKAGED_SKILL_PATH, id="packaged-skill"),
    # KYO-516: the README became a one-screen front page that quotes no dispatch path,
    # and the three passages that quoted the home moved, text intact, to these pages --
    # the Quick start cheatsheet, the store layout, and the guarantees table.
    pytest.param(OPERATING, id="operating"),
    pytest.param(STORE, id="store"),
    pytest.param(RULES_AND_EXIT_CODES, id="rules-and-exit-codes"),
)


def _quoted_home_segments(text: str) -> list[tuple[int, str]]:
    """Every directory segment a document names directly under :data:`HOME_MARKER`.

    Scope, stated honestly. A candidate is the rest of the *line* after the marker,
    truncated at the next backtick -- inline code spans end there, and the README's
    fenced cheatsheet has no backtick to stop at, so it runs to end of line. A
    candidate containing no ``/`` names no directory segment: it is the store home
    itself (``the store at ~/kyosei/<project>/,``) or a leaf file under it
    (``~/kyosei/<project>/scratchpad.md``), and neither is what this check is about,
    so both are skipped. Whatever precedes the first ``/`` of a candidate that has
    one IS a directory segment -- spaces included, which is the entire point, since
    the constant's value has one.
    """
    found: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        start = 0
        while (hit := line.find(HOME_MARKER, start)) != -1:
            start = hit + len(HOME_MARKER)
            candidate = line[start:].split("`", 1)[0]
            if "/" not in candidate:
                continue
            found.append((lineno, candidate.split("/", 1)[0]))
    return found


@pytest.mark.parametrize("path", HOME_QUOTING_DOCS)
def test_every_quoted_worktree_home_spells_the_constant(path: Path) -> None:
    """KYO-197.c2: the skill and the README name the directory the tool derives.

    Both halves matter and they fail in opposite directions. The canary fails when a
    document stops quoting the home at all (a check that reads nothing passes
    vacuously, KYO-34's lesson); the comparison fails when a document quotes a
    segment the constant does not spell -- which is what shipped, and which is also
    what a *future* rename of the constant produces, since the documents would then
    still say ``dispatch worktrees`` while the constant said something else.
    """
    segments = _quoted_home_segments(path.read_text(encoding="utf-8"))
    assert segments, (
        f"{path.relative_to(REPO_ROOT)} quotes no directory under {HOME_MARKER!r} any "
        "more. If the document genuinely stopped naming the dispatch worktree home, "
        "drop it from HOME_QUOTING_DOCS deliberately; otherwise this check just went "
        "blind"
    )
    for lineno, segment in segments:
        assert segment == config.WORKTREES_DIRNAME, (
            f"{path.relative_to(REPO_ROOT)}:{lineno} names "
            f"`~/kyosei/<project>/{segment}/`, but `kyo dispatch --branch` derives "
            f"`~/kyosei/<project>/{config.WORKTREES_DIRNAME}/` "
            "(kyosei.config.WORKTREES_DIRNAME). Change the document, not the "
            "constant: the space is KYO-89's, and the reasoning is written above it"
        )


def test_the_quoted_home_check_reads_the_real_documents() -> None:
    """The extractor's own canary: it must skip the store home and leaf files under
    it, and it must keep a segment that contains a space. A version that dropped
    spaced segments would have passed on the broken documents and on the fixed ones
    alike."""
    sample = "\n".join(
        (
            "the store at `~/kyosei/<project>/` is a visible directory",
            "a scratchpad at `~/kyosei/<project>/scratchpad.md` per session",
            "mv kyosei ideas ~/kyosei/<project>/          # the whole store",
            "derives `~/kyosei/<project>/dispatch worktrees/<name>` and prints",
            "# worktree it derives: ~/kyosei/<project>/two words/",
        )
    )
    assert _quoted_home_segments(sample) == [(4, "dispatch worktrees"), (5, "two words")]


# ------------------------------------------------------------------ KYO-198


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(PACKAGED_SKILL_PATH, id="packaged"),
    ],
)
def test_the_skill_s_report_section_names_verify_from_report(path: Path) -> None:
    """KYO-198.c4, the skill half. `kyo report` prints the verdicts and says they are
    NOT applied, correctly (KYO-7.c4) — and the controller then extracted each
    criterion's evidence from the report JSON by hand and ran one
    `kyo verify --evidence-file` per criterion, many times over.
    The mechanism has to be reachable from the section that creates the need for it,
    which is this one; a controller who reads only the workflow document is the exact
    reader KYO-131 measured concluding the tool could not do something it could.

    Anchored on the section rather than the whole file for KYO-34's reason: a
    whole-file search would pass on a mention buried in a section about something
    else, which is the same staleness wearing a different shape.
    """
    text = path.read_text(encoding="utf-8")
    heading = "## 4. Ingesting reports"
    assert heading in text, (
        f"{path.name} no longer has a section starting {heading!r}; re-anchor this check "
        "rather than deleting it"
    )
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    assert "kyo verify --from-report" in section, (
        "the report-ingestion section must name `kyo verify --from-report <dispatch-id>`: "
        "it is the section that tells a controller the verdicts are not applied, so it is "
        "the section where the way to apply them belongs (KYO-198.c4)"
    )
    assert "--only" in section, (
        "the section must also name `--only`, which is what lets a controller apply the "
        "verdicts they have checked and leave the rest — the half that keeps the "
        "deliberate step deliberate"
    )


# ------------------------------------------------------------------ KYO-302

# The check in this section is the FOURTH direction, and the first that quantifies over
# the whole parser rather than over what the skill already says.
#
# Every check above is closed over the document: the invocation checks validate the
# commands the skill names, the sweep check reads the classes the skill lists, and even
# KYO-131's write-path check — which does walk the parser — walks one derived slice of it
# (the flags that write prose). None of them can see a command the CLI grew and the skill
# never mentioned. A walk of `build_parser()` against the skill once found 15 of
# 51 commands named nowhere in it, including `kyo edit --touches-from-drift`, which this
# store's own controller had hand-substituted with `git diff --name-only` six times in one
# session, and the four-command upgrade sequence a consumer's controller needs and would
# only ever meet in the README (KYO-302).
#
# The check is deliberately NOT "the skill must name everything". The skill says of itself
# that it is the workflow and not the command reference, and a document naming all 51
# commands stops being a workflow — `kyo reference` is complete by construction and is
# where exhaustiveness belongs (KYO-138). So each direction has an escape hatch that costs
# a sentence: a command or a flag is either named in the workflow, where a controller would
# reach for it, or listed below with a REASON a future reader can disagree with. What
# cannot happen any more is the third thing, which is what happened: nobody deciding at all.

#: Commands the workflow deliberately leaves to `kyo reference`, each with the reason.
#: Disagree with one of these by deleting its entry and writing the sentence in the skill
#: — that is the whole intended workflow for this dict.
LEAVE_TO_REFERENCE_COMMANDS: dict[str, str] = {
    # KYO-331's entry for `accept` was deleted by KYO-328, which is the debt collecting
    # itself exactly as that entry said it would: the record rewriting the skill's report
    # section named `kyo accept` there, and this check failed until the now-false reason
    # went. Nothing replaces it — the command is in the workflow, where a controller
    # reaches for it.
    # KYO-494's entry for `confirm` was deleted by KYO-495, which named the command in
    # section 5's human-gate paragraph and in the proposal-approval sentence of section 2.
    "base": (
        "saved views are an arrangement a project declares once in kyosei/bases.yml and "
        "then reads back; the workflow's own instruction is to read the board with "
        "`kyo brief` and `kyo list`, and a project that has authored bases knows it has"
    ),
    "export": (
        "`kyo export --refresh` repairs export documents a schema bump left behind — store "
        "maintenance, named by `kyo doctor` at the moment it is needed and carried in the "
        "upgrade sequence in docs/reference/topics/install.md, not a step in the controller "
        "loop"
    ),
    # KYO-585's debt entry for `prior-art` was deleted by KYO-586, which named the command,
    # `kyo prior-art-setting` and `--no-prior-art` in section 1 beside the brief line.
    "map": (
        "renders the kind registry as a diagram; section 2 already sends the controller to "
        "kyosei/kinds.yml itself, which is the source of truth the diagram is drawn from"
    ),
    # KYO-514. Debt, like KYO-331's was: the skill's session-start section is where a
    # controller would offer `kyo onboard --check`'s fixes, and the skill was outside that
    # dispatch's touches.
    "onboard": (
        "prints the setup prompt an agent follows before the skill exists in a project, and "
        "`--check` audits a project's setup; the prompt, the README and "
        "docs/reference/topics/install.md's Install section name it, "
        "and a running controller reaches it through `kyo doctor`'s neighbours, not the loop"
    ),
    "rebuild": (
        "rebuilds kyosei.db from kyosei/export/ — a checkout-level act (a fresh clone, a "
        "schema bump, a corrupted db) that the tool's own refusal message names on the "
        "spot, and that section 0 reaches through `kyo where` for the store that will not "
        "open. A controller does not choose to rebuild; it is told to"
    ),
    "sessions": (
        "reads and writes kyosei/sessions.json: `--hook` is how the packaged session hooks "
        "record a session, and the plain listing answers an operator question (which Claude "
        "Code sessions this project has had, and their Remote Control links) that nothing "
        "in the dispatch loop consults. docs/reference/topics/store.md, 'Reading the store', "
        "names it for the "
        "human who does ask it (KYO-302.c3)"
    ),
    "pre-tool-use": (
        "a hook entry point, not a controller step: it runs check-push and check-command "
        "against one PreToolUse payload, and is opt-in until a re-pack ships it and the "
        "packaged hook switches to it. docs/reference/topics/operating.md's command list "
        "names it (KYO-422)"
    ),
    "serve": (
        "a long-running server for viewers (the desktop app, a browser), not a controller "
        "step: nothing in the dispatch loop talks HTTP, and a controller reads the same "
        "documents with the commands it serves (KYO-453)"
    ),
    "orchestrator": (
        "starts the maintainer's own orchestrator session, from a terminal or over SSH; an "
        "orchestrator does not start itself, and docs/reference/topics/serve-and-desktop.md, "
        "'Starting an orchestrator from another machine', says how to run it (KYO-458; "
        "`kyo controller` is its alias, KYO-519)"
    ),
    "controller": (
        "the alias `kyo orchestrator` is also accepted under, kept for the desktop app's "
        "wire and for the commands people have typed (KYO-519, KYO-513); the skill "
        "names the command by its canonical name"
    ),
    "projects": (
        "manages the machine's project registry, which `kyo init` already fills; a "
        "controller's loop never adds or removes a served project (KYO-455)"
    ),
    "harness": (
        "a setup step run once per machine and once per project, before the skill it "
        "installs is loaded; the operator's guide and the install topic give it (KYO-594)"
    ),
    "install-agents": (
        "a setup step `kyo onboard`'s prompt runs for setup decision C4, writing the "
        "operator's worker agent types once; the setup-decisions guide gives it (KYO-607)"
    ),
    # KYO-454: the four read-only views a viewer (the desktop app, through `kyo serve`)
    # reads; the controller's own reads are `kyo brief`, `kyo show` and `kyo worktrees`.
    "briefs": (
        "a viewer's read of past session briefs; a controller reads the current one with "
        "`kyo brief` and a past shift note by id with `kyo show` (KYO-454)"
    ),
    "shift-notes": (
        "a viewer's read of the whole shift log; a controller gets the notes it needs in "
        "`kyo brief`'s window and writes them with `kyo note --shift` (KYO-454)"
    ),
    "gates": (
        "a viewer's census of open gates across records; a controller meets gates in "
        "`kyo brief` and `kyo show`, and adds and clears them with `kyo gate`/`kyo ungate` "
        "(KYO-454)"
    ),
    "activity": (
        "the desktop activity view's rows, read through `kyo serve`; a controller reads "
        "the same dispatches with `kyo worktrees` and `kyo show <dispatch-id>` (KYO-454)"
    ),
}

#: Flags of commands the skill DOES name, deliberately left to `kyo reference`. Same rule,
#: same escape hatch. Keyed by ``(command, flag)`` so that a flag left out on one command
#: and explained on another cannot be confused for one decision.
LEAVE_TO_REFERENCE_FLAGS: dict[tuple[str, str], str] = {
    # KYO-595: the copies mode is chosen once, at setup, and a project already on the
    # copies runs both commands without the flag; the skill names them for that case.
    ("install-hooks", "--copies"): (
        "a setup choice made once; a project already on the copies, the "
        "only one whose workflow reruns this command, needs no flag, and the refusal "
        "names it where it is needed"
    ),
    ("install-skill", "--copies"): (
        "a setup choice made once; a project already on the copies, the "
        "only one whose workflow reruns this command, needs no flag, and the refusal "
        "names it where it is needed"
    ),
    # KYO-330's six entries — `--from` and `--check` on new/promote/spawn — were deleted
    # by KYO-328, which wrote them into section 2 of the workflow. Their reasons said in
    # so many words that the paragraph's next author should decide, and the check below
    # turned the decision into a failing test the moment the skill named the flags.
    ("confirm", "--quote"): (
        "the one-line twin of --quote-file, which the skill names: the maintainer's words "
        "are rarely one line once written in the who/when/where shape, and the "
        "workflow teaches the file form for prose (law 5); `kyo reference` carries both"
    ),
    ("new", "--since-note"): (
        "KYO-498: used once per already-old carried item when a project adopts the carry "
        "kind; docs/reference/topics/registries.md's kind-registry section teaches it, and "
        "whether the workflow should "
        "is the controller's call, not this dispatch's"
    ),
    ("edit", "--since-note"): (
        "KYO-498: the later-set form of `kyo new --since-note`, same reason; "
        "docs/reference/topics/registries.md's kind-registry section teaches it"
    ),
    ("check-branch", "--quiet"): (
        "opt-in output narrowing (KYO-420): every default is byte-identical without it, "
        "so the workflow the skill teaches is unchanged; whether the skill should teach "
        "it is the controller's call, not this dispatch's"
    ),
    ("check-clean", "--quiet"): (
        "opt-in output narrowing (KYO-420): every default is byte-identical without it, "
        "so the workflow the skill teaches is unchanged; whether the skill should teach "
        "it is the controller's call, not this dispatch's"
    ),
    ("show", "--fields"): (
        "opt-in output narrowing (KYO-420): every default is byte-identical without it, "
        "so the workflow the skill teaches is unchanged; whether the skill should teach "
        "it is the controller's call, not this dispatch's"
    ),
    ("brief", "--compact"): (
        "a hook's own flag: written into .claude/settings.json by `kyo install-hooks` and "
        "never typed by a controller, so section 0 names the hook by what it does and "
        "REFERENCE.md carries the exact command (KYO-339's editing pass)"
    ),
    ("brief", "--session-start"): (
        "a hook's own flag: written into .claude/settings.json by `kyo install-hooks` and "
        "never typed by a controller, so section 0 names the hook by what it does and "
        "REFERENCE.md carries the exact command (KYO-339's editing pass)"
    ),
    ("check-branch", "--subagent-stop"): (
        "a hook's own flag: written into .claude/settings.json by `kyo install-hooks` and "
        "never typed by a controller, so section 0 names the hook by what it does and "
        "REFERENCE.md carries the exact command (KYO-339's editing pass)"
    ),
    ("check-clean", "--subagent-stop"): (
        "a hook's own flag: written into .claude/settings.json by `kyo install-hooks` and "
        "never typed by a controller, so section 0 names the hook by what it does and "
        "REFERENCE.md carries the exact command (KYO-339's editing pass)"
    ),
    ("attach", "--supports"): (
        "points an evidence artifact at the claim or criterion it supports; the workflow's "
        "route from evidence to a criterion is `kyo verify --evidence-file`, and section 5 "
        "names `kyo attach` for what it is there for — minting the REC.eN a redaction can "
        "target"
    ),
    ("check-command", "--timeout-ms"): (
        "states by hand the timeout a Bash call declares, so KYO-312's ceiling is "
        "answerable without a PreToolUse payload; what the workflow reaches is the hook "
        "itself and the one config line that turns it on, and a controller typing this "
        "is debugging the guard rather than running the loop"
    ),
    ("check-push", "--advisory"): (
        "what the git pre-push hook runs so a human pushing their own repository is warned "
        "rather than blocked; the controller-facing half is the PreToolUse block and "
        "`--acknowledge`, and both are in section 0"
    ),
    ("docs", "--suppressed"): (
        "lists what a declared `#symbol` stopped `kyo docs preview` from raising "
        "(KYO-352) — an audit a WORKER runs in its own worktree, beside the preview it "
        "writes `docs_verdicts[]` from, while every docs paragraph in this workflow is "
        "the controller's settle path over the obligations that did raise"
    ),
    ("init", "--in-repo"): (
        "`kyo init` runs once, before there is a controller session to read this file, and "
        "its layout flags are a maintainer's decision documented in "
        "docs/reference/topics/store.md ('Where the store lives'); section 0 begins after init"
    ),
    ("init", "--defaults"): (
        "same as --in-repo: the scripted route through the init-time layout choice "
        "(KYO-558), made before any orchestrator session exists; `kyo onboard`'s prompt "
        "and docs/guide/04-getting-started.md name it"
    ),
    ("init", "--explain"): (
        "same as --in-repo: it explains the init-time layout choice and writes nothing "
        "(KYO-558); `kyo where` is what an orchestrator reads its own layout back with"
    ),
    ("init", "--no-git-init"): (
        "same as --in-repo: an init-time choice (KYO-507) made before any controller "
        "session exists; docs/reference/topics/store.md, 'What `kyo init` creates', names it"
    ),
    ("init", "--prefix"): (
        "same as --in-repo: an init-time decision the maintainer makes once, and one a "
        "controller cannot revisit — record ids are permanent (law 4)"
    ),
    ("init", "--store"): (
        "same as --in-repo: where the store goes is a maintainer's choice at init time, "
        "and `kyo where` is what a controller reads it back with"
    ),
    ("show", "--expand-obligations"): (
        "expands a collapsed obligations block in `kyo show`; the collapsing is the "
        "readable default and --json carries every obligation either way (KYO-219), so "
        "nothing is hidden from a controller who needs them"
    ),
    ("worktrees", "--expand-stops"): (
        "names the unaudited stop events on finished-and-gone rows instead of counting "
        "them; section 3's subject is what a prune would remove, and --json carries the "
        "stops either way"
    ),
}


def _subcommand_choices(parser=None):
    """``{name: subparser}`` for a parser — the shipped one by default, or a copy a
    canary has grown a throwaway command on."""
    parser = parser if parser is not None else build_parser()
    return next(a for a in parser._actions if a.choices).choices


def _universal_flags() -> set[str]:
    """The flags every single subcommand carries (`--help`, `--root`, `--actor`,
    `--json`). Derived by intersection rather than listed, so a new global joins it
    without anyone remembering — and deliberately read off a PRISTINE parser, so a
    canary that adds a throwaway command does not shrink the set and light up every
    other command's globals as unnamed."""
    sets = [_flags_for(sp) for sp in _subcommand_choices().values()]
    return set.intersection(*sets) if sets else set()


def _names(text: str, token: str) -> bool:
    """Does the document name this command or flag anywhere?

    Boundary-aware, unlike the whole-file substring tests above, and that is load-bearing
    in both directions: plain `in` would count `kyo report-schema` as naming `kyo report`,
    and `--do-file` as naming `--do`.

    Honest scope, in this file's tradition. This asks whether the token appears SOMEWHERE
    in the document, not whether it appears in a sentence about the right command — the
    same boundary :func:`_prose_write_paths` draws, and for the same reason: demanding a
    runnable invocation per flag would turn the skill into the hand-written command
    reference `kyo reference` already generates. So a flag named for one command counts as
    named for another that shares its spelling (`--output`, `--all`, `--file`). What that
    leaves uncovered is a flag mentioned in the wrong paragraph; what it catches is the
    failure KYO-302 measured, which is a flag mentioned in no paragraph at all.
    """
    return re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", text) is not None


def _unnamed_commands(text: str, choices) -> list[str]:
    return [name for name in sorted(choices) if not _names(text, f"kyo {name}")]


def _unnamed_flags(text: str, choices, universal: set[str] | None = None) -> list[tuple[str, str]]:
    """Every ``(command, flag)`` the document leaves unnamed, for commands it DOES name.

    A flag of a command the skill never mentions is not this check's business: the command
    itself is already answered for, above, and demanding flag sentences for a command
    deliberately left to the reference would be the same demand twice.
    """
    universal = _universal_flags() if universal is None else universal
    unnamed: list[tuple[str, str]] = []
    for name in sorted(choices):
        if not _names(text, f"kyo {name}"):
            continue
        for flag in sorted(_flags_for(choices[name]) - universal):
            if not flag.startswith("--"):
                continue
            if not _names(text, flag):
                unnamed.append((name, flag))
    return unnamed


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(PACKAGED_SKILL_PATH, id="packaged"),
    ],
)
def test_every_command_is_named_or_deliberately_left_to_the_reference(path: Path) -> None:
    """KYO-302.c2, first half: adding a command to the parser fails this suite until
    somebody either writes it into the workflow or says, in a sentence, why it stays out."""
    text = path.read_text(encoding="utf-8")
    undecided = [
        name
        for name in _unnamed_commands(text, _subcommand_choices())
        if name not in LEAVE_TO_REFERENCE_COMMANDS
    ]
    assert not undecided, (
        f"{path.name} never names {undecided}, and LEAVE_TO_REFERENCE_COMMANDS in "
        f"{Path(__file__).name} does not say why. Do one of the two: name the command in "
        "the workflow paragraph where a controller would reach for it, or add it to that "
        "dict with a one-line reason. `kyo reference` is complete by construction, so "
        "'leave it to the reference' is a legitimate answer — it is just not one that "
        "gets to be silent (KYO-302)"
    )


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(PACKAGED_SKILL_PATH, id="packaged"),
    ],
)
def test_every_flag_of_a_named_command_is_named_or_deliberately_left(path: Path) -> None:
    """KYO-302.c2, second half. The command half alone would let `kyo dispatch` grow a
    flag that changes what dispatching does while the skill's dispatch section says
    nothing — which is the KYO-131 shape one level down, and is how `--touches-from-drift`
    lived unnamed on a command the skill names in three separate paragraphs."""
    text = path.read_text(encoding="utf-8")
    undecided = [
        pair
        for pair in _unnamed_flags(text, _subcommand_choices())
        if pair not in LEAVE_TO_REFERENCE_FLAGS
    ]
    assert not undecided, (
        f"{path.name} names these commands but never their flags: "
        f"{[f'kyo {c} {f}' for c, f in undecided]}. Name each where the workflow reaches "
        "it, or add it to LEAVE_TO_REFERENCE_FLAGS with a one-line reason (KYO-302)"
    )


def test_the_leave_to_reference_sets_carry_no_stale_or_empty_entry() -> None:
    """The escape hatch's own upkeep, and the half that keeps it honest.

    An entry for a command that no longer exists is a reason nobody can act on; an entry
    for a command the skill has since started naming is a reason that is now FALSE, and a
    false reason in this dict is worse than no dict, because the next reader takes it as a
    decision somebody made. Both fail here, and so does an empty reason.
    """
    choices = _subcommand_choices()
    text = PACKAGED_SKILL_PATH.read_text(encoding="utf-8")

    unreal = sorted(set(LEAVE_TO_REFERENCE_COMMANDS) - set(choices))
    assert not unreal, f"LEAVE_TO_REFERENCE_COMMANDS names {unreal}, which the parser does not have"

    now_named = sorted(name for name in LEAVE_TO_REFERENCE_COMMANDS if _names(text, f"kyo {name}"))
    assert not now_named, (
        f"the skill now names {now_named}, so its 'deliberately left out' reason is false — "
        "delete the entry"
    )

    universal = _universal_flags()
    for (command, flag), reason in LEAVE_TO_REFERENCE_FLAGS.items():
        assert command in choices, (
            f"LEAVE_TO_REFERENCE_FLAGS names `kyo {command}`, which does not exist"
        )
        assert flag in _flags_for(choices[command]) - universal, (
            f"`kyo {command} {flag}` is not a real non-global flag of that command any more"
        )
        assert not _names(text, flag), (
            f"the skill now names {flag}, so the reason recorded for `kyo {command} {flag}` "
            "is false — delete the entry"
        )
        assert reason.strip(), f"`kyo {command} {flag}` is listed with no reason"

    for name, reason in LEAVE_TO_REFERENCE_COMMANDS.items():
        assert reason.strip(), f"`kyo {name}` is listed with no reason"


def test_the_two_way_check_catches_a_new_command_and_a_new_flag() -> None:
    """KYO-302.c2's canary, and the reason to trust either half: both directions are shown
    failing on a parser that grew something the skill cannot possibly name.

    `build_parser()` builds a fresh tree on every call, so the throwaway command and the
    throwaway flag added here exist only inside this test — nothing else in the suite, and
    nothing in the shipped CLI, ever sees them. KYO-34's lesson is the whole point: a
    checker that has never been shown to fail is not known to check anything, and a parser
    walk that silently returned an empty list would make both tests above pass for the
    wrong reason.
    """
    parser = build_parser()
    choices = _subcommand_choices(parser)
    assert "frobnicate" not in choices, "pick a throwaway name the CLI does not have"

    subparsers = next(a for a in parser._actions if a.choices)
    grown = subparsers.add_parser("frobnicate", help="a command that exists only in this test")
    grown.add_argument("--harder", action="store_true")
    # ...and a flag on a command the skill DOES name, which is the second half: the command
    # check cannot see this one, because `kyo edit` is named in four paragraphs already.
    choices["edit"].add_argument("--newly-grown", action="store_true")

    text = PACKAGED_SKILL_PATH.read_text(encoding="utf-8")
    assert "frobnicate" in _unnamed_commands(text, choices), (
        "a command the skill has never heard of must come back unnamed"
    )
    assert ("edit", "--newly-grown") in _unnamed_flags(text, choices), (
        "a new flag on a command the skill names must come back unnamed — this is the "
        "`--touches-from-drift` case, and the command-level check is blind to it"
    )
    # And neither is excused by the escape hatch, which is what makes the suite fail.
    assert "frobnicate" not in LEAVE_TO_REFERENCE_COMMANDS
    assert ("edit", "--newly-grown") not in LEAVE_TO_REFERENCE_FLAGS
    # The real tree is untouched: a fresh parser still has neither.
    fresh = _subcommand_choices()
    assert "frobnicate" not in fresh
    assert "--newly-grown" not in _flags_for(fresh["edit"])


# ------------------------------------------------------------------ KYO-466
#
# The desktop app stopped bundling a runtime and KYO-450 retired its install kind, but
# the reference kept telling a controller to re-pack a bundled copy and to use the app's
# Install Command Line Tools action. The reinstall routes it names are the ones
# `buildinfo.upgrade_route` answers with, read from the same constants rather than
# restated, so the reference cannot drift from what `kyo version` prints.

_RETIRED_REINSTALL_WORDING = (
    "re-pack",
    "Install Command Line Tools",
    "bundled",
)


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(PACKAGED_REFERENCE_PATH, id="packaged"),
    ],
)
def test_the_reference_names_the_uv_tool_routes_and_no_retired_one(path: Path) -> None:
    """KYO-466.c1: a release moves with `uv tool upgrade kyosei`, a snapshot of a checkout
    with `make install-tool`, and nothing re-packs an app or links its bundled `kyo`."""
    from kyosei.buildinfo import RESNAPSHOT_TARGET, UV_TOOL_UPGRADE_COMMAND

    # Whitespace folded, because the reference wraps prose at a column and an inline
    # command can break across a line like any other words.
    text = " ".join(path.read_text(encoding="utf-8").split())
    section = text.split("### The reinstall, per install kind", 1)[1].split("### ", 1)[0]
    assert f"`{UV_TOOL_UPGRADE_COMMAND}`" in section, section
    assert f"`{RESNAPSHOT_TARGET}`" in section, section
    for retired in _RETIRED_REINSTALL_WORDING:
        assert retired.lower() not in text.lower(), (
            f"{path.name} still says {retired!r}; the desktop app ships no runtime, "
            "so no route through it exists"
        )
