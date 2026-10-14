"""KYO-241.c1/c2 — the brief states the report's array key sets, generated; the
refusal for an unknown key names what the entry does accept.

Two reports in a row (KYO-233.d1, KYO-234.d1) were refused at ingest for a key that
is not in ``report.schema.json`` (``additionalProperties: false``): a `severity` on
a `disagreements[]` entry and a `message` on a `commits[]` entry. Both were the
obvious word for the object — a commit has a message, a disagreement has a weight —
and nothing in the brief or the refusal told the worker which keys the schema
actually wanted, so each cost a resume just to delete one key.

This file is a sibling to ``test_kyo149_report_schema_agrees.py`` rather than an
addition to it: KYO-149 pins :mod:`kyosei.report_schema`'s hand-mirrored constants
against the shipped ``report.schema.json`` document; this pins :mod:`kyosei.brief`'s
generated prose against those same constants, so a worker reading the frozen brief
and a worker reading `kyo report-schema` are reading two renderings of one set, not
two lists that happen to agree today.
"""

from __future__ import annotations

from kyosei import brief as brief_module
from kyosei import render
from kyosei import report_schema as rs
from kyosei.contracts import REPORT_AMENDS_KEY

from .conftest import new_record, valid_report

# ------------------------------------------------------------- KYO-241.c1: the brief


def test_report_array_key_sets_are_report_schemas_own_constants() -> None:
    """:data:`brief.REPORT_ARRAY_KEY_SETS` names each array with the very frozenset
    :mod:`kyosei.report_schema` mirrors from the shipped document -- not a second,
    separately-typed copy of the same key names. ``test_kyo149...`` already proves
    those frozensets agree with ``report.schema.json`` itself, so this closes the
    chain: brief text <- these frozensets <- the shipped schema."""
    expected = {
        "criteria_verdicts[]": rs.CRITERION_VERDICT_ALLOWED,
        "premise_false[]": rs.PREMISE_FALSE_ALLOWED,
        "disagreements[]": rs.DISAGREEMENT_ALLOWED,
        "corrected_numbers[]": rs.CORRECTED_NUMBER_ALLOWED,
        "docs_verdicts[]": rs.DOCS_VERDICT_ALLOWED,
        "commits[]": rs.COMMIT_ALLOWED,
    }
    assert dict(brief_module.REPORT_ARRAY_KEY_SETS) == expected
    # Every report array KYO-149 mirrors is named here, and nothing else is --
    # an array added to the schema without a row here would state no key set at
    # all in the brief, which is exactly the silence KYO-241 exists to close.
    # `docs_verdicts` is optional (rs.TOP_LEVEL_OPTIONAL), not required, so it is
    # checked against the union of both rather than against required alone.
    array_top_level_keys = (set(rs.TOP_LEVEL_REQUIRED) | set(rs.TOP_LEVEL_OPTIONAL)) - {
        "dispatch_id",
        "record_id",
        "worked_from_sha",
        "summary",
        "touched_files",
        REPORT_AMENDS_KEY,
    }
    assert {name.rstrip("[]") for name in expected} == array_top_level_keys


def test_the_rendered_block_states_every_key_the_constants_hold() -> None:
    """Parses :func:`brief._report_key_sets_block`'s own output back into
    ``{array_name: {keys}}`` and compares it to the live constants -- not to a
    second hand-typed expectation -- so a rendering bug (wrong sort, a dropped key,
    a stale copy-paste) is caught the same way a schema drift is."""
    block = "\n".join(brief_module._report_key_sets_block())
    parsed: dict[str, set[str]] = {}
    for name, allowed in brief_module.REPORT_ARRAY_KEY_SETS:
        line = next(line for line in block.splitlines() if line.startswith(f"- `{name}`"))
        keys = {tok.strip("`") for tok in line.split(": ", 1)[1].split(", ")}
        parsed[name] = keys
        assert keys == set(allowed), f"{name}: rendered {keys} but constants say {set(allowed)}"
    assert set(parsed) == {name for name, _ in brief_module.REPORT_ARRAY_KEY_SETS}


def test_a_changed_key_set_changes_the_rendered_text(monkeypatch) -> None:
    """Proof the block is generated from the data and not copy-pasted prose: swap
    the module-level constant and the rendered text follows it."""
    monkeypatch.setattr(
        brief_module,
        "REPORT_ARRAY_KEY_SETS",
        (("commits[]", frozenset({"sha", "a_marker_key_nobody_typed_by_hand"})),),
    )
    block = "\n".join(brief_module._report_key_sets_block())
    assert "a_marker_key_nobody_typed_by_hand" in block


def test_generated_brief_states_the_commit_and_disagreement_key_sets(kyo, git_project) -> None:
    """The actual frozen brief a worker reads (KYO-241's own failure mode: two
    workers reached for `severity` and `message` because nothing on screen named
    the accepted keys) now states them, for the two entries that were burned."""
    record = new_record(kyo, criteria=["a"])
    dispatched = kyo("--full", "dispatch", record, "--json")
    brief = dispatched.json["record"]["dispatches"][0]["brief"]
    assert "`commits[]`" in brief
    assert "`sha`" in brief and "`subject`" in brief
    assert "`disagreements[]`" in brief
    for key in ("topic", "brief_said", "worker_did", "rationale"):
        assert f"`{key}`" in brief
    # The two keys that started KYO-241 must never be advertised as accepted.
    assert "`severity`" not in brief
    assert "`message`" not in brief
    # KYO-270: `summary` was retired from `commits[]` -- the brief's own generated
    # key-set line for it must name only the survivor.
    commits_line = next(
        line for line in brief.splitlines() if line.strip().startswith("- `commits[]`")
    )
    assert "`summary`" not in commits_line


# KYO-241.c2 (the unknown-key refusal names the entry's accepted keys) is covered
# in ``tests/test_report.py`` per the dispatch brief's Task section, beside every
# other ``kyo report`` refusal test -- see
# ``test_report_refuses_an_unknown_commit_key_and_names_what_it_accepts`` and
# ``test_report_refuses_an_unknown_disagreement_key_and_names_what_it_accepts``.

# ------------------------------------------------------------- KYO-241.c3: `subject`


def test_commits_accept_an_optional_subject_string(kyo, git_project, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    d = kyo("dispatch", record, "--json").json
    report = valid_report(
        dispatch_id=d["dispatch_id"],
        record_id=record,
        worked_from_sha=d["worked_from_sha"],
        commits=[{"sha": "0123abcdef0", "subject": "KYO-241: add subject to commits[]"}],
    )
    path = tmp_path / "r.json"
    path.write_text(__import__("json").dumps(report), encoding="utf-8")
    result = kyo("report", d["dispatch_id"], "--file", str(path))
    assert result.code == 0, result


def test_a_report_without_subject_renders_exactly_as_before(kyo, git_project, tmp_path) -> None:
    """KYO-241.c3: a report that omits `subject` is unchanged -- same commit line
    `kyo report` printed before this field existed, bare sha and nothing else.

    Before KYO-270, this same case was driven by the older `summary` field
    instead -- retired now (`test_report_refuses_the_retired_commits_summary_key_
    and_names_the_survivor` in ``tests/test_report.py`` covers a *new* report
    still writing it), so the "no first-line field at all" case is exercised by
    omitting both, not by falling back to the retired key."""
    record = new_record(kyo, criteria=["a"])
    d = kyo("dispatch", record, "--json").json
    report = valid_report(
        dispatch_id=d["dispatch_id"],
        record_id=record,
        worked_from_sha=d["worked_from_sha"],
        commits=[{"sha": "0123abcdef0"}],
    )
    path = tmp_path / "r.json"
    path.write_text(__import__("json").dumps(report), encoding="utf-8")
    result = kyo("report", d["dispatch_id"], "--file", str(path))
    assert result.code == 0, result
    # No first-line field: rendered as the bare sha on its own line, no trailing
    # text after it (the exact line `report_ingest.cmd_report` prints per commit).
    assert "  - 0123abcdef0\n" in result.out or result.out.rstrip("\n").endswith(
        "  - 0123abcdef0"
    )


def test_subject_renders_beside_the_sha_in_kyo_report_and_kyo_show(
    kyo, git_project, tmp_path
) -> None:
    record = new_record(kyo, criteria=["a"])
    d = kyo("dispatch", record, "--json").json
    report = valid_report(
        dispatch_id=d["dispatch_id"],
        record_id=record,
        worked_from_sha=d["worked_from_sha"],
        commits=[{"sha": "0123abcdef0", "subject": "the commit's first line"}],
    )
    path = tmp_path / "r.json"
    path.write_text(__import__("json").dumps(report), encoding="utf-8")
    result = kyo("report", d["dispatch_id"], "--file", str(path))
    assert result.code == 0, result
    assert "0123abcdef0  the commit's first line" in result.out

    shown = kyo("show", record)
    assert shown.code == 0, shown
    assert "0123abcdef0  the commit's first line" in shown.out


# ------------------------------------------------------------- KYO-270: retire `summary`


def test_commit_allowed_no_longer_carries_the_retired_summary_key() -> None:
    """KYO-270.c1: `commits[]` accepts exactly one optional first-line field now
    -- `subject` survives, `summary` (KYO-224) is gone from the accepted set
    itself, not merely unused by convention."""
    assert frozenset({"sha", "subject"}) == rs.COMMIT_ALLOWED
    assert "summary" not in rs.COMMIT_OPTIONAL


def test_commit_label_does_not_fall_back_to_the_retired_summary_key() -> None:
    """KYO-270.c2: `render.commit_label` reads only `subject` -- an entry that
    carries `summary` but not `subject` (exactly the shape an already-ingested,
    pre-KYO-270 report stores) renders as if it carried no first-line field at
    all, tolerantly (no exception), rather than echoing the retired key's text.
    This is the "no code path still reads the retired key" proof for `render`;
    the ingest side of the same proof is
    `test_report_refuses_the_retired_commits_summary_key_and_names_the_survivor`
    in ``tests/test_report.py``."""
    legacy = {"sha": "0123abcdef0", "summary": "an old-style echoed subject line"}
    assert render.commit_label(legacy) == "0123abcdef0"
    assert "an old-style echoed subject line" not in render.commit_label(legacy)

    both = {"sha": "0123abcdef0", "subject": "the real subject", "summary": "the old one"}
    assert render.commit_label(both) == "0123abcdef0  the real subject"


def test_a_stored_report_carrying_the_retired_summary_key_still_renders_via_kyo_show(
    kyo, git_project, tmp_path, db
) -> None:
    """KYO-270's Task section: 'existing stored reports that carry `summary` are
    history and are not rewritten; `kyo show <dispatch-id>` must still render
    them (read the stored JSON tolerantly, validate only on ingest)'.

    A *new* report carrying `summary` is refused at ingest now (KYO-270), so the
    only way this shape reaches the store is a report ingested before KYO-270
    existed. This reproduces exactly that: ingest a valid report through `kyo
    report` (as any already-stored report was, under the rules of its own day),
    then rewrite its stored ``body_json`` in place -- the same bytes an old
    report already has on disk, not a document `kyo report` was ever asked to
    accept -- and prove `kyo show` still renders it without raising."""
    import json as _json

    record = new_record(kyo, criteria=["a"])
    d = kyo("dispatch", record, "--json").json
    report = valid_report(
        dispatch_id=d["dispatch_id"],
        record_id=record,
        worked_from_sha=d["worked_from_sha"],
        commits=[{"sha": "0123abcdef0", "subject": "placeholder, rewritten below"}],
    )
    path = tmp_path / "r.json"
    path.write_text(_json.dumps(report), encoding="utf-8")
    ingested = kyo("report", d["dispatch_id"], "--file", str(path), "--json")
    assert ingested.code == 0, ingested
    report_id = ingested.json["report_id"]

    conn = db()
    row = conn.execute("SELECT body_json FROM reports WHERE id = ?", (report_id,)).fetchone()
    body = _json.loads(row["body_json"])
    body["commits"] = [{"sha": "0123abcdef0", "summary": "an old-style echoed subject line"}]
    conn.execute(
        "UPDATE reports SET body_json = ? WHERE id = ?", (_json.dumps(body), report_id)
    )
    conn.close()

    shown = kyo("show", record)
    assert shown.code == 0, shown
    assert "0123abcdef0" in shown.out
    # The retired key's text is not read or rendered -- only the sha shows.
    assert "an old-style echoed subject line" not in shown.out
