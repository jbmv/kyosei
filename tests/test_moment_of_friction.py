"""Four places the tool knew the remedy and did not say it.

Each was reported by a consumer project. Grouped
because they are one class, and because this repo's own standing rule — every
error message naming a remedy ships with a test proving the remedy clears it —
had nothing to say about messages that name *no* remedy at all.

- `kyo new` on a `requires_criteria` kind succeeded silently and produced a
  record `kyo move` refuses to take to `working` and `kyo next` filters out of
  ready work. The zero-ceremony intake path is intended; the silence was not.
- `kyo move <id> done` listed each unmet criterion and never mentioned
  `kyo split`, the command built for that exact moment. A consumer hand-rolled the
  split twice in one day without reaching for it.
- `--criterion`'s help read "criterion to restate" while `--state` accepts
  only the two void states and there is no `--text`. Criterion text being
  immutable is right — it is a claim — but the help promised otherwise.
- `--why` is prose as an argument, and a reason containing backticks lost a
  word to shell command substitution before `kyo` saw it.
"""

from __future__ import annotations

from pathlib import Path

from .conftest import new_record

# ----------------------------------------------------------- a parked record


def test_a_parked_record_says_it_is_parked(kyo) -> None:
    result = kyo("new", "bug", "--title", "Filed in a hurry")
    assert result.code == 0, result
    assert "parked" in result.out
    assert "--add-criteria-file" in result.out


def test_the_notice_names_what_actually_blocks(kyo) -> None:
    """It claims `move working` will refuse and `next` will not list it. Both
    halves are asserted here, so the notice cannot start lying."""
    record = kyo("new", "bug", "--title", "Filed in a hurry", "--json").json["record"]["id"]

    blocked = kyo("move", record, "working")
    assert blocked.code == 2, blocked
    assert record not in [row["id"] for row in kyo("next", "--json").json["ready"]]


def test_the_remedy_the_notice_names_clears_it(kyo, write_file) -> None:
    record = kyo("new", "bug", "--title", "Filed in a hurry", "--json").json["record"]["id"]
    assert kyo("move", record, "working").code == 2

    assert kyo(
        "edit", record, "--add-criteria-file", write_file("c.txt", "it stops happening\n")
    ).code == 0
    assert kyo("move", record, "working").code == 0
    assert record in [row["id"] for row in kyo("next", "--json").json["ready"]]


def test_a_kind_that_needs_no_criteria_says_nothing(kyo, project: Path, write_file) -> None:
    """`plan` has requires_criteria: false, so a plan with no criteria is not
    parked and must not be told it is."""
    idea = project / "ideas" / "a-plan.md"
    idea.write_text("# A plan\n\nOwns work.\n", encoding="utf-8")
    result = kyo(
        "promote", str(idea), "--kind", "plan",
        "--touches-file", write_file("t.txt", "kyosei/kinds.yml\n"),
    )
    assert result.code == 0, result
    assert "parked" not in result.out


# ------------------------------------------------- move done names kyo split


def test_move_done_names_kyo_split(kyo, write_file) -> None:
    record = new_record(kyo, kind="bug", title="Half done", criteria=["the first", "the second"])
    kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed")

    result = kyo("move", record, "done")
    assert result.code == 2, result
    assert f"{record}.c2" in result.err
    assert "kyo split" in result.err
    assert "--to-successor" in result.err


def test_the_split_the_message_names_actually_clears_it(kyo) -> None:
    """AGENTS.md's standing rule, applied to a remedy that was missing rather than
    wrong: run exactly what the message suggests and the transition succeeds."""
    record = new_record(kyo, kind="bug", title="Half done", criteria=["the first", "the second"])
    kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed")
    assert kyo("move", record, "done").code == 2

    assert kyo("split", record, "--to-successor", f"{record}.c2").code == 0
    assert kyo("move", record, "done").code == 0


def test_the_message_lists_every_unmet_criterion_in_the_command(kyo) -> None:
    record = new_record(kyo, kind="bug", title="Three left", criteria=["a", "b", "c"])
    kyo("verify", f"{record}.c1", "--verdict", "met", "--method", "human-observed")

    result = kyo("move", record, "done")
    assert f"{record}.c2 {record}.c3" in result.err, result.err


# ------------------------------------------------- the criterion flag's help


def test_the_criterion_flag_describes_the_correction_window(kyo) -> None:
    """KYO-26 wrote this against help text that said "criterion to restate" while
    restating text was impossible. KYO-58 made the promise keepable instead of
    deleting it: the flag now says which of the two acts it does, and the window in
    which a correction is allowed at all.
    """
    help_text = kyo("edit", "--help").out
    assert "criterion to restate" not in help_text
    assert "--text-file" in help_text
    assert "kyo restate" in help_text
    # the window itself, not just the verb — the thing a reader has to know
    assert "attested" in help_text


def test_the_documented_reword_procedure_works(kyo, write_file) -> None:
    """The help names a procedure; this is that procedure, run — now the one
    command `kyo restate` rather than the two-step void-then-add it replaced."""
    record = new_record(kyo, kind="bug", title="Badly worded", criteria=["amber AND red"])
    assert kyo(
        "restate", f"{record}.c1", "--text-file", write_file("c.txt", "amber at one minute\n")
    ).code == 0

    criteria = kyo("show", record, "--json").json["criteria"]
    assert [c["state"] for c in criteria] == ["void-withdrawn", "unmet-unbuilt"]
    assert criteria[1]["id"] != criteria[0]["id"], "the replacement gets its own id"
    assert criteria[0]["reason"] == f"restated as {criteria[1]['id']}", (
        "the connection between the two is mechanical, not prose the controller "
        "happened to write"
    )


# ------------------------------------------------------- shell-mangled --why


def test_an_unbalanced_backtick_in_why_is_refused(kyo) -> None:
    """The consumer's case: a reason containing backticks lost a word to command
    substitution, and the stored reason was wrong and uncorrectable."""
    record = new_record(kyo, kind="bug", title="Doomed", criteria=["a"])
    result = kyo("wont-do", record, "--why", "the value in `foo was wrong")
    assert result.code != 0
    assert "unbalanced" in result.err
    assert "--why-file" in result.err


def test_an_unbalanced_quote_in_why_is_refused(kyo) -> None:
    record = new_record(kyo, kind="bug", title="Doomed", criteria=["a"])
    result = kyo("wont-do", record, "--why", 'it said "half a quotation')
    assert result.code != 0
    assert "unbalanced" in result.err


def test_a_balanced_pair_is_left_alone(kyo) -> None:
    """The check must not cry wolf at correct usage — quoting a phrase is normal,
    and a rule that fires on ordinary input is one people route around."""
    record = new_record(kyo, kind="bug", title="Fine", criteria=["a"])
    result = kyo("wont-do", record, "--why", 'superseded by `kyo split`, per the "new" plan')
    assert result.code == 0, result


def test_an_apostrophe_is_not_treated_as_an_unbalanced_quote(kyo) -> None:
    """The false positive this repo's own suite caught within a minute of the check
    existing: ordinary English contracts words, and refusing that would have made
    the check worse than the mangling it catches."""
    record = new_record(kyo, kind="bug", title="Fine", criteria=["a"])
    result = kyo("wont-do", record, "--why", "the premise died and this wasn't worth pursuing")
    assert result.code == 0, result


def test_the_file_route_the_message_names_accepts_it(kyo, write_file) -> None:
    """The remedy: --why-file is not touched by the shell, so it takes exactly the
    text a mangled --why could not."""
    record = new_record(kyo, kind="bug", title="Doomed", criteria=["a"])
    assert kyo("wont-do", record, "--why", "the value in `foo was wrong").code != 0
    assert kyo(
        "wont-do", record, "--why-file", write_file("w.txt", "the value in `foo was wrong")
    ).code == 0
