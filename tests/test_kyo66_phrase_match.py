"""KYO-66: a phrase search is not proof of absence in hard-wrapped prose.

The record was filed by the controller against its own mistake — four absence claims
in one day, made from a phrase search that could not have found what it was looking
for. Instance 1 grepped ``founding charter``, found nothing, and wrote "already fixed"
into a record; the phrase was there, spanning a line break as ``founding\\ncharter``.

The human half of that is a ``procedures.md`` rule and can only ever be aspirational.
The half tested here is the tool's own: ``kyosei`` matched stored phrases literally in
four places, and every one of them under-matched *in silence* —

* ``kyo ideas review``'s mention scan, whose code-span pattern could not cross a line
  break at all, so it reported no broken mentions exactly the way it reports none;
* touch symbol validation (``kyo new/promote --touches-file``) and
* ``governs:`` target validation (``kyo docs check``), which both said
  "``'X'`` not found in ``path``" about text that was right there — an absence claim in
  its costliest direction, a refusal;
* ``kyo redact``'s embedded-copy scrub, where finding nothing means the value stays.

Every test below asserts a match **succeeds** across a line break where a fixed-string
match fails, so each one fails against the pre-fix build, and each pairs with its
opposite: the wrap-tolerant form must not become one that matches anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kyosei.ledger_scrub import _scrub_every_form
from kyosei.prose import (
    contains_phrase,
    flatten,
    phrase_pattern,
    replace_phrase,
)

from .conftest import new_record, valid_report

#: The phrase and its wrapped form, from instance 1 of the record.
PHRASE = "founding charter"
WRAPPED = "…a claim this repo inherited from the founding\ncharter, which is stale.\n"


# --------------------------------------------------------------- the matcher itself


def test_a_fixed_string_match_misses_the_phrase_that_wrapped() -> None:
    """The defect, stated as a test: this is what `grep` answered, and it is a true
    answer to a narrower question than the one being asked."""
    assert PHRASE not in WRAPPED


def test_the_wrap_tolerant_match_finds_it() -> None:
    assert contains_phrase(WRAPPED, PHRASE)


def test_the_pattern_is_the_form_the_record_prescribes() -> None:
    """Split on whitespace, join the escaped words with a whitespace gap. Pinned as a
    string because the rule in `procedures.md` tells a reader to build exactly this,
    and a reader who does must get the same thing the tool got."""
    assert phrase_pattern("founding charter") == r"founding(?:\s|\\[nrt])+charter"
    assert phrase_pattern("  spaced   out  ") == r"spaced(?:\s|\\[nrt])+out"


def test_a_single_word_pattern_is_the_word_itself() -> None:
    """The change is additive: a one-word needle — every symbol this repo actually
    resolves — matches exactly what it matched before."""
    assert phrase_pattern("generate_brief") == "generate_brief"


def test_a_phrase_with_regex_metacharacters_is_matched_literally() -> None:
    """A stored phrase is text, never a pattern. `re.escape` per word is what keeps a
    redaction target containing `.*` from redacting the whole column."""
    haystack = "the value was a.*b (really)\nand nothing else"
    assert contains_phrase(haystack, "a.*b (really) and")
    assert not contains_phrase(haystack, "axxb (really) and")


def test_a_wordless_phrase_is_refused_rather_than_matching_everywhere() -> None:
    """An empty pattern matches at every position. Silently "finding" a blank needle
    in every row is a worse failure than the one this function fixes, so building the
    pattern raises and the caller-facing predicate answers False."""
    with pytest.raises(ValueError):
        phrase_pattern("   \n  ")
    assert contains_phrase("anything at all", "   ") is False
    assert replace_phrase("anything at all", "", "[gone]") == ("anything at all", 0)


def test_whole_word_still_refuses_a_substring_of_a_longer_identifier() -> None:
    """Symbol resolution means "contains that symbol", not "contains those letters".
    Wrap tolerance must not quietly widen that."""
    assert contains_phrase("def render():", "render", whole_word=True)
    assert not contains_phrase("def rerender():", "render", whole_word=True)
    assert contains_phrase("def rerender():", "render")


def test_replace_phrase_replaces_across_the_break_and_counts_it() -> None:
    text, count = replace_phrase(WRAPPED, PHRASE, "[redacted]")
    assert count == 1
    assert "founding" not in text
    assert "[redacted]" in text
    assert WRAPPED.replace(PHRASE, "[redacted]") == WRAPPED, "precondition: literal fails"


def test_a_replacement_containing_a_backreference_is_inserted_literally() -> None:
    """A redaction marker is text. If it were expanded as a template, a marker
    containing `\\1` would rewrite the very text it is removing."""
    text, count = replace_phrase("keep the founding\ncharter out", PHRASE, r"\1[x]")
    assert count == 1
    assert r"\1[x]" in text


def test_flatten_collapses_the_wrapping_and_nothing_else() -> None:
    assert flatten("  founding\n  charter  ") == "founding charter"
    assert flatten("already flat") == "already flat"


def test_the_gap_also_spans_a_json_escaped_newline() -> None:
    """Some of this store's prose is matched while it is still JSON-encoded
    (`reports.body_json`, the `_events.jsonl` ledgers), where a newline is the two
    characters `\\n` and no whitespace at all."""
    encoded = json.dumps("the founding\ncharter")[1:-1]
    assert "\n" not in encoded
    assert contains_phrase(encoded, PHRASE)


# ------------------------------------------------- touch symbols (kyo new/promote)


def _wrapped_heading_doc() -> str:
    """A file whose prose wraps mid-phrase, the shape every doc in this repo has."""
    return (
        "# Notes\n\n"
        "The registry is the single place a kind is declared, and the audience is a\n"
        "first-class property of every rule.\n"
    )


def test_a_touch_symbol_that_wrapped_in_the_target_file_resolves(
    kyo, project: Path, write_file
) -> None:
    """A symbol is not always an identifier — `path.md#some phrase` is matched against
    prose. Before KYO-66 this was refused with "not found" about text sitting in the
    file, which is the record's own failure made by the tool, in the direction that
    costs most: a refusal a caller cannot argue with."""
    (project / "notes.md").write_text(_wrapped_heading_doc(), encoding="utf-8")
    assert "audience is a first-class property" not in _wrapped_heading_doc()

    result = kyo("--full",
        "new", "bug", "--title", "Wrapped touch",
        "--criteria-file", write_file("c.txt", "a\n"),
        "--touches-file", write_file("t.txt", "notes.md#audience is a first-class property\n"),
        "--json",
    )
    assert result.code == 0, result
    touches = result.json["record"]["touches"]
    assert [t["symbol"] for t in touches] == ["audience is a first-class property"]


def test_a_touch_symbol_that_is_genuinely_absent_is_still_refused(
    kyo, project: Path, write_file
) -> None:
    """The other direction. A validator that accepts everything is indistinguishable
    from one that was removed."""
    (project / "notes.md").write_text(_wrapped_heading_doc(), encoding="utf-8")
    result = kyo(
        "new", "bug", "--title", "Absent touch",
        "--criteria-file", write_file("c2.txt", "a\n"),
        "--touches-file", write_file("t2.txt", "notes.md#audience is a second-class property\n"),
    )
    assert result.code != 0
    assert "not found in notes.md" in result.err


# ------------------------------------------------------ governs: targets (kyo docs check)


def test_a_governs_symbol_that_wrapped_resolves(kyo, project: Path) -> None:
    """`governs:` deliberately shares touch validation's resolution rule; KYO-66 keeps
    them sharing one implementation rather than one intention."""
    (project / "notes.md").write_text(_wrapped_heading_doc(), encoding="utf-8")
    docs = project / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "PRD.md").write_text(
        "## Rules\n\ngoverns: notes.md#audience is a first-class property\n\nProse.\n",
        encoding="utf-8",
    )
    result = kyo("docs", "check", "--json")
    assert result.code == 0, result
    assert result.json["problems"] == []


def test_a_governs_symbol_that_is_genuinely_absent_still_fails(kyo, project: Path) -> None:
    (project / "notes.md").write_text(_wrapped_heading_doc(), encoding="utf-8")
    docs = project / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "PRD.md").write_text(
        "## Rules\n\ngoverns: notes.md#audience is a second-class property\n",
        encoding="utf-8",
    )
    result = kyo("docs", "check", "--json")
    assert result.code == 3
    assert result.json["problems"][0]["target"].endswith("second-class property")


# --------------------------------------------------------- kyo redact's embedded scrub


MULTILINE_SECRET = "consumer id 4429117 was seen in\nthe queue on the second run"


def test_the_embedded_scrub_finds_a_copy_whose_line_breaks_moved() -> None:
    """Unit-level, because it is the property that matters and it is one line of
    difference: the stored blob and the copy agree on every word and disagree on
    where the breaks fall. A fixed-string scrub finds nothing there and reports
    nothing found, which reads exactly like having nothing to find."""
    reflowed = "before " + MULTILINE_SECRET.replace("\n", "\n  ") + " after"
    assert MULTILINE_SECRET not in reflowed, "precondition: a literal scrub misses it"
    scrubbed, hits = _scrub_every_form(reflowed, MULTILINE_SECRET, "[redacted]")
    assert hits == 1
    assert "4429117" not in scrubbed
    assert scrubbed.startswith("before ") and scrubbed.endswith(" after")


def test_the_embedded_scrub_finds_a_json_escaped_copy() -> None:
    """`reports.body_json` holds a worker's report exactly as it was ingested, so a
    multi-line blob is in there with `\\n` where its newline was — and never matched
    the raw blob at all. Not a wrapping failure; the same silent under-match."""
    embedded = json.dumps({"summary": f"quoting: {MULTILINE_SECRET}"})
    assert MULTILINE_SECRET not in embedded, "precondition: a literal scrub misses it"
    scrubbed, hits = _scrub_every_form(embedded, MULTILINE_SECRET, "[redacted]")
    assert hits == 1
    assert "4429117" not in scrubbed
    assert json.loads(scrubbed)["summary"] == "quoting: [redacted]"


def test_the_scrub_leaves_text_that_merely_shares_words_alone() -> None:
    """Wrap tolerance is not word-bag matching: the words have to be adjacent, in
    order, separated by nothing but the wrapping."""
    other = "consumer id 4429117 was seen in a different place in\nthe queue"
    scrubbed, hits = _scrub_every_form(other, MULTILINE_SECRET, "[redacted]")
    assert hits == 0
    assert scrubbed == other


def test_redacting_a_multi_line_claim_clears_it_from_an_ingested_report(
    kyo, git_project: Path, write_file, tmp_path: Path
) -> None:
    """End to end, on the surface the old scrub could not reach at all. The claim is
    stored with the newline the file gave it; the report that quoted it is stored
    JSON-encoded; `kyo redact` has to clear both."""
    record = new_record(kyo, kind="task", title="Cited", criteria=["a"])
    claim_id = kyo(
        "claim", record, "--level", "asserted", "--method", "read it",
        "--file", write_file("cl.txt", MULTILINE_SECRET), "--json",
    ).json["claim_id"]
    dispatch = kyo("dispatch", record, "--json").json
    report_path = tmp_path / "r.json"
    report_path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatch["dispatch_id"],
                record_id=record,
                worked_from_sha=dispatch["worked_from_sha"],
                summary=f"the claim said: {MULTILINE_SECRET}",
            )
        ),
        encoding="utf-8",
    )
    assert kyo("report", dispatch["dispatch_id"], "--file", str(report_path)).code == 0

    body = _report_bodies(git_project)
    assert any("4429117" in text for text in body), "precondition: the report quotes it"

    assert kyo("redact", claim_id, "--why", "carried an id").code == 0

    assert not any("4429117" in text for text in _report_bodies(git_project))
    assert "4429117" not in (git_project / "kyosei" / "export" / f"{record}.json").read_text()
    assert kyo("doctor").code == 0, "the rewritten export must still match the database"


def _report_bodies(project: Path) -> list[str]:
    import sqlite3

    conn = sqlite3.connect(project / "kyosei" / "kyosei.db")
    try:
        return [str(row[0]) for row in conn.execute("SELECT body_json FROM reports")]
    finally:
        conn.close()
