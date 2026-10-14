"""Explain once: the fixed reasoning write commands used to print on every run (KYO-489).

KYO-482 classified every line `kyo dispatch`, `kyo report` and `kyo accept` print, and
found twenty explanatory clauses that read the same on every run. Nineteen of them are
here; #17 is a
remedy printed twice, which KYO-488 already prints once, and a remedy never belongs in
this registry.

Each entry carries the clause's full text, carried over word for word from the renderer
that printed it, the KYO id that introduced it, and the short form a command now prints
inline in its place: the words that decide the controller's next act ("DECLARED",
"never checked against work", "reported, not refused", "not enforced", "gate each
yourself"). Two surfaces render from this one source (law 3):

1. the command's own output, through :func:`say`, which ends with one
   ``explained in `kyo reference <command>``` line (:func:`footer`) whenever any short
   form was printed;
2. ``kyo reference <command>``'s "Output notes" subsection (:func:`reference_lines`),
   which lists each entry's short form beside its full text.

**What never comes here** (the boundary set on KYO-482):
refusals, remedies, receipts and report review content. A remedy names a command to
run or an edit to make, and ``tests/test_kyo482_explanations.py`` fails if any entry's
text carries a backticked `kyo ` command or a ``governs:`` edit — so a remedy cannot be
shortened away by being filed here. Refusals never consult :func:`say`.

Short and full texts are :meth:`str.format` templates. A field is a per-run value the
renderer passes (``{branch}``, ``{gate_type}``) and the reference prints as ``<branch>``;
or a constant of this build (:func:`_constants`), filled in both places, so the
reference states the build's own suffix list rather than a copy of it.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass


@dataclass(frozen=True)
class Explanation:
    """One explanatory clause, explained once."""

    #: Stable key, the renderer's handle on this entry.
    key: str
    #: Every top-level command whose text output can print this entry's short form —
    #: the `kyo reference <command>` sections that list it. A command composing another
    #: (`accept` runs `verify`, `docs settle` and `edit`; `confirm` runs `ungate`) is
    #: listed for what its steps print.
    commands: tuple[str, ...]
    #: What the command prints inline, in place of the full text.
    short: str
    #: The clause as it was printed before KYO-489, word for word.
    full: str
    #: The record that introduced the clause, kept as provenance.
    kyo_id: str
    #: Its number in KYO-482's classification of the twenty clauses.
    clause: int


REGISTRY: tuple[Explanation, ...] = (
    Explanation(
        key="worktree-setup-not-run",
        commands=("dispatch",),
        short="`worktree_setup`, printed, not run",
        full="declared by this project as `worktree_setup`; printed, not run",
        kyo_id="KYO-143",
        clause=1,
    ),
    Explanation(
        key="worker-model-not-enforced",
        commands=("dispatch",),
        short="not enforced",
        full=(
            "recorded and stated in the brief, never enforced — Kyosei does not spawn "
            "workers"
        ),
        kyo_id="KYO-61",
        clause=2,
    ),
    Explanation(
        key="disjointness-declared",
        commands=("dispatch",),
        short="disjointness: computed from DECLARED touches",
        full=(
            "disjointness was computed against DECLARED touches — a prediction on both "
            "sides, written before the work, and reported drift says predictions are "
            "routinely wrong"
        ),
        kyo_id="KYO-147.c1",
        clause=3,
    ),
    Explanation(
        key="never-checked-against-work",
        commands=("dispatch",),
        short="never checked against work",
        full=(
            "never checked against work — it has not reported, and no earlier dispatch "
            "on this claim has either, so nothing has ever compared this table to a diff"
        ),
        kyo_id="KYO-147.c1",
        clause=4,
    ),
    Explanation(
        key="dragged-in",
        commands=("dispatch",),
        short="file(s) historically dragged in and not declared",
        full=(
            "file(s) that this record's declared files have historically dragged in and "
            "this record does not declare (from {reports} reported dispatch(es); "
            "reported, never refused)"
        ),
        kyo_id="KYO-147.c2",
        clause=5,
    ),
    Explanation(
        key="print-brief",
        commands=("dispatch",),
        short="or --print-brief",
        full="pass --print-brief to print it here instead",
        kyo_id="KYO-326",
        clause=6,
    ),
    Explanation(
        key="verdicts-not-applied",
        commands=("report",),
        short="verdicts above are NOT applied; apply them via",
        full=(
            "note: verdicts above are NOT applied to any criterion; applying them is a "
            "orchestrator act via"
        ),
        kyo_id="KYO-7.c4",
        clause=7,
    ),
    Explanation(
        key="obligations-need-verdicts",
        commands=("report",),
        short="each needs a verdict before completion",
        full=(
            "this record cannot reach its completion status until each carries a "
            "verdict, and a document counts as updated when you say so, not when its "
            "timestamp moves"
        ),
        kyo_id="KYO-137.c2",
        clause=8,
    ),
    Explanation(
        key="whole-file-narrowing",
        commands=("report", "docs"),
        short="narrows to a symbol's own lines, for {region_suffixes} targets only",
        full=(
            "raises a section only when the diff touches that symbol's own lines, where "
            "the section is about a symbol rather than the file. That narrowing holds "
            "for {region_suffixes} targets and is offered for no others: a `#symbol` on "
            "any other suffix is accepted and still raises like the whole file"
        ),
        kyo_id="KYO-199.c2, KYO-351",
        clause=9,
    ),
    Explanation(
        key="changed-paths-branch-diff",
        commands=("report",),
        short="the diff of {branch}",
        full="read from the diff of {branch} (`git diff {against}...{branch}`)",
        kyo_id="KYO-211.c1",
        clause=10,
    ),
    Explanation(
        key="drift-reported-not-refused",
        commands=("report", "touch-drift"),
        short="reported, not refused",
        full=(
            "This is REPORTED, not refused — a touches table is a prediction and is "
            "routinely, defensibly wrong. What it costs is a later dispatch colliding "
            "with a file nothing knew was claimed."
        ),
        kyo_id="KYO-71",
        clause=11,
    ),
    Explanation(
        key="review-entries-unsettled",
        commands=("accept",),
        short="unsettled",
        full="they stay readable in the record, and nothing here settles them",
        kyo_id="KYO-331",
        clause=12,
    ),
    Explanation(
        key="docs-nothing-blocks",
        commands=("accept", "docs"),
        short="none blocks completion",
        full="nothing here blocks its completion status any longer",
        kyo_id="KYO-137",
        clause=13,
    ),
    Explanation(
        key="drift-fed-back",
        commands=("accept", "edit"),
        short="corrects this record; the guard reads the next record's table",
        full=(
            "This makes {record_id} TRUE; it does not improve the disjointness guard, "
            "which reads the next record's table and not this one's — measured on one "
            "consumer project, where feeding drift back after every merge left four of "
            "five following declarations still insufficient"
        ),
        kyo_id="KYO-147.c4",
        clause=14,
    ),
    Explanation(
        key="scope-derivation-never-overrides",
        commands=("accept", "edit", "new", "promote", "spawn"),
        short="The stored scope is unchanged.",
        full="The stored scope is unchanged — derivation never overrides a declaration.",
        kyo_id="KYO-100.c3",
        clause=15,
    ),
    Explanation(
        key="accept-wrote-no-gate",
        commands=("accept",),
        short="not gated; gate each yourself:",
        full=(
            "no open `awaiting:*` gate and no running dispatch. This command wrote no "
            "gate — a gate carries an instruction to a person. Gate each yourself:"
        ),
        kyo_id="KYO-331.c4",
        clause=16,
    ),
    Explanation(
        key="accept-not-closed",
        commands=("accept",),
        short="records not moved",
        full="every covered record is left where this act found it",
        kyo_id="KYO-331.c5",
        clause=18,
    ),
    Explanation(
        key="gates-orthogonal",
        commands=("gate",),
        short="orthogonal",
        full="gates are orthogonal to status",
        # Milestone 1 wrote this before records had ids; the charter section is its
        # provenance.
        kyo_id="milestone 1; charter, \"Status and gates are orthogonal dimensions\"",
        clause=19,
    ),
    Explanation(
        key="ungate-no-stale-flag",
        commands=("ungate", "confirm"),
        short="only {flagging_gate_types} does",
        full=(
            "clearing it reports that something happened, not that something was decided "
            "(only {flagging_gate_types} does — KYO-59)"
        ),
        kyo_id="KYO-59",
        clause=20,
    ),
)

BY_KEY: dict[str, Explanation] = {entry.key: entry for entry in REGISTRY}

#: The short forms :func:`say` rendered during this invocation, as printed. Reset by
#: :func:`reset` at the top of every `kyo` run (``cli.run``), the way
#: ``prose.reset_path_warnings`` resets its collector, so one invocation's short forms
#: can never put a pointer line under the next one's output.
_SAID: list[str] = []


def _constants() -> dict[str, str]:
    """The fields that are constants of this build, read from where the code reads them.

    Imported here rather than at module top: this module is imported by the renderers
    those constants live beside, and must not import them back at load time.
    """
    from .contracts import GATE_TYPES_FLAGGING_STALE_CRITERIA
    from .docs_regions import region_suffixes_says

    return {
        "region_suffixes": region_suffixes_says(),
        "flagging_gate_types": ", ".join(sorted(GATE_TYPES_FLAGGING_STALE_CRITERIA)),
    }


def fields(template: str) -> list[str]:
    """The ``{name}`` fields a template uses, in order."""
    return [name for _, name, _, _ in string.Formatter().parse(template) if name]


def _fill(template: str, values: dict[str, str]) -> str:
    """``template`` with every field filled: a constant of this build, a value the
    caller passed, and otherwise ``<name>`` (the reference's spelling of a per-run
    value)."""
    names = fields(template)
    if not names:
        return template
    known = {**_constants(), **values}
    return template.format(**{name: known.get(name, f"<{name}>") for name in names})


def match_full(key: str, text: str) -> dict[str, str] | None:
    """The per-run values ``text`` carries, when it is exactly this entry's full text,
    else ``None`` — so a renderer holding only the printed sentence (a stored finding)
    can print the short form without a second copy of the template."""
    template = BY_KEY[key].full
    constants = _constants()
    pattern = ""
    seen: set[str] = set()
    for literal, name, _spec, _conv in string.Formatter().parse(template):
        pattern += re.escape(literal)
        if not name:
            continue
        if name in constants:
            pattern += re.escape(constants[name])
        elif name in seen:
            pattern += f"(?P={name})"
        else:
            seen.add(name)
            pattern += f"(?P<{name}>.+?)"
    found = re.fullmatch(pattern, text)
    return found.groupdict() if found else None


def short(key: str, **values: object) -> str:
    """The entry's short form, filled, WITHOUT recording it as printed."""
    return _fill(BY_KEY[key].short, {k: str(v) for k, v in values.items()})


def full(key: str, **values: object) -> str:
    """The entry's full text, filled — what the command printed before KYO-489."""
    return _fill(BY_KEY[key].full, {k: str(v) for k, v in values.items()})


def say(key: str, **values: object) -> str:
    """The short form a command prints inline, recorded so :func:`footer` can point at
    the full text."""
    text = short(key, **values)
    _SAID.append(text)
    return text


def reset() -> None:
    """Start one invocation with nothing said."""
    _SAID.clear()


def footer_line(command: str) -> str:
    """The one pointer line, naming the command that was run."""
    return f"explained in `kyo reference {command}`"


def footer(command: str, lines: list[str]) -> list[str]:
    """``[footer_line(command)]`` when ``lines`` carry a short form :func:`say` rendered
    in this invocation, and ``[]`` otherwise.

    Checked against the lines actually printed, not only against what was said: a
    composing command (`accept`) may drop a step's line from its text, and a pointer
    under output that shows no short form would be a pointer to nothing.
    """
    if not _SAID:
        return []
    text = "\n".join(lines)
    if not any(said in text for said in _SAID):
        return []
    return [footer_line(command)]


def for_command(command: str) -> list[Explanation]:
    """The entries `kyo reference <command>` lists, in clause order."""
    return [entry for entry in REGISTRY if command in entry.commands]


def reference_lines(command: str) -> list[str]:
    """``kyo reference <command>``'s "Output notes" subsection, or ``[]`` when the
    command prints none of these short forms."""
    entries = for_command(command)
    if not entries:
        return []
    out = [
        "### Output notes",
        "",
        "Text output prints the quoted short form in place of the full text after it, "
        "which is what it printed there before KYO-489. When it printed any, its last "
        f"line is: explained in `kyo reference {command}`. `<name>` stands for a value "
        "of that run.",
        "",
    ]
    for entry in entries:
        out.append(
            f"- \"{_fill(entry.short, {})}\" — {_fill(entry.full, {})} ({entry.kyo_id})"
        )
    out.append("")
    return out


__all__ = [
    "BY_KEY",
    "REGISTRY",
    "Explanation",
    "fields",
    "footer",
    "footer_line",
    "for_command",
    "full",
    "match_full",
    "reference_lines",
    "reset",
    "say",
    "short",
]
