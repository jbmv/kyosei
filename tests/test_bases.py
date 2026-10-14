"""``kyo base`` — saved view definitions over one shared engine (KYO-108).

The board, the status view, the bubbles and the typed-edge graph are designed as
four *renderings* of one saved query. The load-bearing property is not that a Base
runs — it is that a Base and ``kyo list`` are the SAME query, so that a filter cannot
exist on one surface and not the other, and a fix to one cannot leave the other behind.
That property is what most of this file is about.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kyosei import bases as bases_module
from kyosei import query as query_module
from kyosei.cli import build_parser
from kyosei.contracts import STANDING_CURRENT, STANDING_VOID, default_ladder

from .conftest import events_text, new_record
from .test_gates import write_gate_tags
from .test_scopes import write_scopes
from .test_statuses import RENAMED_RETIREMENT, write_statuses

TWO_SCOPES = """\
scopes:
  server:
    summary: The API service.
  web-app:
    summary: The browser client.
"""

ONE_TAG = """\
gate-tags:
  ui-review:
    summary: A human has to look at a UI element.
"""


def bases_yml(project: Path) -> Path:
    return project / "kyosei" / "bases.yml"


def write_bases(project: Path, text: str) -> Path:
    path = bases_yml(project)
    path.write_text(text, encoding="utf-8")
    return path


def base_of(**fields: object) -> str:
    """One base named ``only``, from a mapping of its declared fields."""
    lines = ["bases:", "  only:", "    summary: A saved view.", "    source: records"]
    for key, value in fields.items():
        lines.append(f"    {key}: {json.dumps(value)}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------- KYO-108.c1
# A definition lives in reviewable config, is validated on load, and a value the
# project's own registries do not declare is refused naming the registry.


def test_the_packaged_default_ships_the_shape_and_declares_no_bases(project: Path) -> None:
    """`kyo init` writes bases.yml beside the other registries, empty.

    Empty on purpose: a seeded base would have to name a status or a kind out of the
    project's own registries, so a project that renamed its ladder would hold a
    bases.yml that fails to load — taking every base it wrote itself down with it.
    """
    text = bases_yml(project).read_text(encoding="utf-8")
    assert "bases: {}" in text
    assert "group-by" in text  # the shape is documented in the file it configures


def test_an_absent_bases_file_is_an_empty_set_and_says_where_to_write_one(
    kyo, project: Path
) -> None:
    """The same rule scopes.yml and gate-tags.yml follow: absence is not an error.

    Law 6 all the same — "(none)" naming no path is a dead end, so the empty listing
    names the file that would hold one.
    """
    bases_yml(project).unlink()
    result = kyo("base", "list", "--json")
    assert result.code == 0
    assert result.json["bases"] == []
    assert result.json["exists"] is False
    assert result.json["path"].endswith("bases.yml")
    assert "bases.yml" in kyo("base", "list").out


def test_a_saved_base_is_read_from_reviewable_config(kyo, project: Path) -> None:
    write_bases(
        project,
        "bases:\n"
        "  open-work:\n"
        "    summary: Everything open.\n"
        "    source: records\n"
        "    filters:\n"
        "      status: open\n",
    )
    listed = kyo("base", "list", "--json").json["bases"]
    assert [b["name"] for b in listed] == ["open-work"]
    assert listed[0]["summary"] == "Everything open."
    assert listed[0]["filters"] == {"status": "open"}


@pytest.mark.parametrize(
    ("filters", "bad_value", "registry_file"),
    [
        ({"kind": "ghost-kind"}, "ghost-kind", "kinds.yml"),
        ({"scope": "nowhere"}, "nowhere", "scopes.yml"),
        ({"gate-tag": "unheard-of"}, "unheard-of", "gate-tags.yml"),
    ],
)
def test_a_filter_value_no_registry_declares_is_refused_naming_the_registry(
    kyo, project: Path, filters: dict, bad_value: str, registry_file: str
) -> None:
    """KYO-108.c1, and the synergy step 1 was built for: a Base cannot express a filter
    the project's own registries do not declare. The sentence is the registry's own
    (``registry.unknown_name_refusal``), so a base is refused in the same words a write
    is."""
    write_bases(project, base_of(filters=filters))
    result = kyo("base", "list")
    assert result.code == 1
    assert bad_value in result.err
    assert registry_file in result.err


def test_an_undeclared_status_is_refused_naming_the_status_registry(
    kyo, project: Path
) -> None:
    write_bases(project, base_of(filters={"status": "shipped"}))
    result = kyo("base", "list")
    assert result.code == 1
    assert "statuses.yml" in result.err
    for name in default_ladder().names:
        assert name in result.err
    assert STANDING_CURRENT in result.err


@pytest.mark.parametrize(
    "declaration",
    [
        {"columns": ["id", "nonsense"]},
        {"sort": ["-nonsense"]},
        {"group-by": "nonsense"},
    ],
)
def test_an_unknown_field_is_refused_naming_the_field_set(
    kyo, project: Path, declaration: dict
) -> None:
    """The other half of c1: a column/sort/grouping name that is not a field names the
    field set the engine produces, for the same reason the registry refusal names the
    registry — a closed set the caller cannot see is a guessing game."""
    write_bases(project, base_of(**declaration))
    result = kyo("base", "list")
    assert result.code == 1
    assert "nonsense" in result.err
    for field_name in ("id", "kind", "status", "title"):
        assert field_name in result.err


@pytest.mark.parametrize("field", ["gate_tags", "gate_types"])
def test_a_list_valued_field_cannot_be_grouped_by(kyo, project: Path, field: str) -> None:
    """``gate_tags`` and ``gate_types`` are shown and filtered on and never grouped by:
    a row with two tags (or two open-gate types) belongs to two groups, and silently
    stringifying the list would put ``[ui, human]`` in a different bucket from
    ``[human, ui]``."""
    write_bases(project, base_of(**{"group-by": field}))
    result = kyo("base", "list")
    assert result.code == 1
    assert field in result.err
    assert field not in result.err.split("the fields the engine produces are:")[1]


def test_a_source_this_build_cannot_run_is_refused_on_load(kyo, project: Path) -> None:
    """A base naming a source no engine can run is refused when the file is READ,
    never answered with an empty view (law 6).

    KYO-109 landed the two sources this test used to name as unrunnable (``ideas`` and
    ``docs``, now in :data:`kyosei.query.SOURCES`), so it names one that is not — the
    mechanism is the refusal and the fact that it lists what this build does run, not
    the particular word that fails it.
    """
    write_bases(
        project,
        "bases:\n  emails:\n    summary: Over the mail spool.\n    source: mailbox\n",
    )
    result = kyo("base", "list")
    assert result.code == 1
    assert "mailbox" in result.err
    for runnable in ("records", "ideas", "docs"):
        assert runnable in result.err


def test_every_problem_in_the_file_arrives_at_once(kyo, project: Path) -> None:
    """Law 6, inherited from the shared loader rather than re-implemented: a bases.yml
    with a bad kind, a bad status, a bad column and a missing summary is fixed in one
    pass, not four runs."""
    write_bases(
        project,
        "bases:\n"
        "  broken:\n"
        "    summary: Four mistakes.\n"
        "    source: records\n"
        "    filters:\n"
        "      kind: ghost-kind\n"
        "      status: shipped\n"
        "    columns: [id, nonsense]\n"
        "  nameless:\n"
        "    source: records\n",
    )
    result = kyo("base", "list")
    assert result.code == 1
    for fragment in ("ghost-kind", "shipped", "nonsense", "summary"):
        assert fragment in result.err, fragment


def test_a_plan_filter_is_checked_when_the_base_runs_not_when_it_loads(
    kyo, project: Path
) -> None:
    """A ``plan:`` filter names a record id, not a registry entry. Checked by the engine
    at run time — the record set is not a vocabulary the project declares, and a
    redacted record must not take every other base in the file down with it."""
    write_bases(project, base_of(filters={"plan": "TST-999"}))
    assert kyo("base", "list").code == 0
    result = kyo("base", "run", "only")
    assert result.code != 0
    assert "TST-999" in result.err


def test_an_unknown_base_name_is_refused_with_the_declared_ones(kyo, project: Path) -> None:
    write_bases(project, base_of())
    result = kyo("base", "run", "no-such-base")
    assert result.code == 1
    assert "no-such-base" in result.err
    assert "only" in result.err
    assert "bases.yml" in result.err


# --------------------------------------------------------------------- KYO-108.c2
# `kyo base` lists the bases and runs one read-only; --json carries rows, applied
# columns and sort, the grouping, and each row's typed links.


def test_a_run_carries_rows_columns_sort_and_grouping(kyo, project: Path) -> None:
    first = new_record(kyo, kind="bug", title="widget explodes")
    second = new_record(kyo, kind="bug", title="gadget melts")
    write_bases(
        project,
        base_of(columns=["id", "title"], sort=["-seq"], **{"group-by": "kind"}),
    )
    data = kyo("base", "run", "only", "--json").json
    assert [row["id"] for row in data["rows"]] == [second, first]
    assert data["applied"]["columns"] == ["id", "title"]
    assert data["applied"]["sort"] == ["-seq"]
    assert data["applied"]["group-by"] == "kind"
    assert data["groups"] == [{"value": "bug", "count": 2, "ids": [second, first]}]
    assert data["summary"] == {"total": 2, "counted": 2, "not_counted": 0}


def test_no_grouping_binding_means_null_not_an_empty_list(kyo, project: Path) -> None:
    """``None`` and ``[]`` are different facts: "this base does not group" and "this
    base groups and matched nothing". A renderer with a group-by dropdown reads them
    differently."""
    new_record(kyo, kind="bug", title="one")
    write_bases(project, base_of())
    assert kyo("base", "run", "only", "--json").json["groups"] is None


# --------------------------------------------------------------------- KYO-120
# `kyo base run --group-by FIELD` — the one binding a caller may change, because it
# rearranges rows a run already produced rather than deciding which rows exist.
# `bases.py`'s module docstring has called group-by "the binding a renderer may
# change" since KYO-108; until this flag, nothing let a renderer change it.


def test_the_flag_replaces_the_declared_grouping_and_applied_says_which(
    kyo, project: Path
) -> None:
    """KYO-120.c1. ``applied.group-by`` is the field the run ACTUALLY grouped by.

    ``base`` is the definition the reviewable file declares and keeps saying so; a
    document that echoed the declared key over rows the flag had regrouped would be
    worse than no flag at all, because a renderer painting "grouped by kind" over
    status buckets is a rendering that cannot be trusted about anything.
    """
    bug = new_record(kyo, kind="bug", title="widget explodes")
    task = new_record(kyo, kind="task", title="replace the widget")
    write_bases(project, base_of(sort=["seq"], **{"group-by": "kind"}))

    data = kyo("base", "run", "only", "--group-by", "status", "--json").json
    assert data["applied"]["group-by"] == "status"
    assert data["groups"] == [{"value": "open", "count": 2, "ids": [bug, task]}]
    # The file's own account of itself is untouched: `base` is the declaration.
    assert data["base"]["group-by"] == "kind"


def test_the_flag_rearranges_and_selects_nothing(kyo, project: Path) -> None:
    """The whole reason this override is allowed where a `--source` or a `--filter`
    is not: grouping decides how already-produced rows are arranged, so the run's rows,
    columns, order and counts are identical with the flag and without it."""
    new_record(kyo, kind="bug", title="widget explodes")
    new_record(kyo, kind="task", title="replace the widget")
    write_bases(project, base_of(columns=["id", "title"], sort=["-seq"], **{"group-by": "kind"}))

    declared = kyo("base", "run", "only", "--json").json
    overridden = kyo("base", "run", "only", "--group-by", "status", "--json").json
    for shared in ("rows", "summary", "base"):
        assert overridden[shared] == declared[shared], shared
    for unchanged in ("source", "filters", "columns", "sort"):
        assert overridden["applied"][unchanged] == declared["applied"][unchanged], unchanged
    # Only the arrangement moved.
    assert overridden["applied"]["group-by"] != declared["applied"]["group-by"]


def test_the_flag_can_group_a_base_that_declares_no_binding(kyo, project: Path) -> None:
    """``None`` means "this base does not group", and the flag is how a renderer asks
    it to — which is the case the docstring's "a renderer may change it" is mostly
    about, since a base with no binding is the shape `kyo init` ships."""
    bug = new_record(kyo, kind="bug", title="widget explodes")
    write_bases(project, base_of())
    assert kyo("base", "run", "only", "--json").json["groups"] is None
    data = kyo("base", "run", "only", "--group-by", "kind", "--json").json
    assert data["applied"]["group-by"] == "kind"
    assert data["groups"] == [{"value": "bug", "count": 1, "ids": [bug]}]


def test_the_terminal_states_the_grouping_it_actually_applied(kyo, project: Path) -> None:
    """The text surface and ``--json`` are two renderings of one run. A terminal that
    printed the file's declared key over regrouped rows would be the same lie in a
    different font, so it reads the applied field and names the key it displaced."""
    new_record(kyo, kind="bug", title="widget explodes")
    write_bases(project, base_of(**{"group-by": "kind"}))
    result = kyo("base", "run", "only", "--group-by", "status")
    assert result.code == 0
    assert "grouped by status" in result.out
    assert "overriding the base's kind" in result.out
    assert "grouped by kind" not in result.out


@pytest.mark.parametrize("field", ["nonsense", "gate_tags"])
def test_the_flag_is_validated_by_the_rule_the_stored_key_gets(
    kyo, project: Path, field: str
) -> None:
    """KYO-120.c2. One rule (:func:`bases.group_by_problem`), asked twice.

    ``nonsense`` is not a field and ``gate_tags`` is a list-valued one — a row with two
    tags belongs to two groups — and the file's ``group-by:`` key is refused for both.
    The flag earns the same refusal, naming the same field set, from the same function:
    a second validator written for the flag is exactly how a surface acquires a
    grouping the saved form is not allowed to declare.
    """
    new_record(kyo, kind="bug", title="widget explodes")
    write_bases(project, base_of())

    flag = kyo("base", "run", "only", "--group-by", field)
    assert flag.code != 0
    assert field in flag.err
    # Naming the field set is the half that makes the refusal actionable (law 6).
    for legal in ("id", "kind", "status", "title"):
        assert legal in flag.err, legal
    # Nothing rendered: the refusal fires before the query runs, so there are no rows
    # on stdout for a caller to mistake for a partial answer.
    assert flag.out == ""

    # The same field in the file earns the same sentence about the same field set.
    write_bases(project, base_of(**{"group-by": field}))
    stored = kyo("base", "list")
    assert stored.code != 0
    assert flag.err.split("is not a field")[-1] == stored.err.split("is not a field")[-1]


@pytest.mark.parametrize("field", ["gate_tags", "gate_types"])
def test_a_multi_valued_field_is_refused_by_the_flag_naming_the_scalar_set(
    kyo, project: Path, field: str
) -> None:
    """``gate_tags`` and ``gate_types`` are shown and filtered on and never grouped by,
    and the refusal proves it by not listing the field it just refused among the ones
    it offers."""
    new_record(kyo, kind="bug", title="widget explodes")
    write_bases(project, base_of())
    result = kyo("base", "run", "only", "--group-by", field)
    assert result.code != 0
    offered = result.err.split("the fields the engine produces are:")[1]
    assert field not in offered
    assert "kind" in offered


def test_the_flag_is_refused_on_the_action_that_runs_nothing(kyo, project: Path) -> None:
    """Law 6. ``kyo base list`` renders declarations; there is no arrangement to
    override, and a flag that silently did nothing would teach a caller it had been
    honoured. The refusal names the invocation that would have worked."""
    write_bases(project, base_of())
    result = kyo("base", "list", "--group-by", "kind")
    assert result.code != 0
    assert "kyo base run" in result.err
    assert "--group-by" in result.err


def test_every_row_carries_its_typed_links(kyo, project: Path) -> None:
    """The design makes the typed-edge graph the fourth rendering, conditional
    on edge-type FILTERING — so the type has to reach the renderer, and from the run
    that produced the rows rather than from a second query per node."""
    blocker = new_record(kyo, kind="bug", title="the blocker")
    blocked = new_record(kyo, kind="bug", title="the blocked")
    assert kyo("link", blocker, "blocks", blocked).code == 0
    write_bases(project, base_of())
    rows = {row["id"]: row["links"] for row in kyo("base", "run", "only", "--json").json["rows"]}
    assert rows[blocker] == [{"direction": "outgoing", "type": "blocks", "other": blocked}]
    assert rows[blocked] == [{"direction": "incoming", "type": "blocks", "other": blocker}]


def test_every_row_carries_its_open_gate_types(kyo, project: Path) -> None:
    """KYO-203.c1: ``gate_types`` beside ``gate_tags`` and ``gates_open`` — the distinct
    types of a record's OPEN gates, in the order those gates were created, so a
    consumer can tell ``awaiting:event`` from ``awaiting:human-review`` without
    opening the record. A cleared gate's type must not linger, the way a cleared
    gate's tag already does not."""
    gated = new_record(kyo, kind="bug", title="gated twice")
    assert kyo("gate", gated, "awaiting:event", "--do", "CI goes green").code == 0
    review_gate = kyo("gate", gated, "awaiting:human-review", "--json").json["gate_id"]
    ungated = new_record(kyo, kind="bug", title="never gated")
    cleared = new_record(kyo, kind="bug", title="gate cleared")
    clear_id = kyo(
        "gate", cleared, "holding-for", "--do", "the measurement finishes", "--json"
    ).json["gate_id"]
    assert kyo("ungate", clear_id, "--why", "measured").code == 0

    write_bases(project, base_of())
    rows = {
        row["id"]: row["gate_types"]
        for row in kyo("base", "run", "only", "--json").json["rows"]
    }
    assert rows[gated] == ["awaiting:event", "awaiting:human-review"]
    assert rows[ungated] == []
    assert rows[cleared] == []

    assert kyo("ungate", review_gate, "--why", "reviewed").code == 0
    rows = {
        row["id"]: row["gate_types"]
        for row in kyo("base", "run", "only", "--json").json["rows"]
    }
    assert rows[gated] == ["awaiting:event"]


def test_a_default_base_renders_exactly_what_kyo_list_renders(kyo, project: Path) -> None:
    """The visible half of "one engine, two surfaces": a base declaring no columns
    prints the listing's own lines, through the listing's own function."""
    new_record(kyo, kind="bug", title="widget explodes")
    write_bases(project, base_of())
    listed = [line for line in kyo("list").out.splitlines() if line.startswith("TST-")]
    ran = [line for line in kyo("base", "run", "only").out.splitlines() if line.startswith("TST-")]
    assert ran == listed


def test_running_a_base_never_writes(kyo, project: Path) -> None:
    """Read-only, and shown rather than asserted: the events log is the store's record
    of every mutation, and a base run adds nothing to it."""
    new_record(kyo, kind="bug", title="one")
    write_bases(project, base_of())
    before = events_text(project)
    assert kyo("base", "run", "only", "--json").code == 0
    assert kyo("base", "list", "--json").code == 0
    assert events_text(project) == before


# --------------------------------------------------------------------- KYO-108.c3
# Every run states the currency direction, so a renderer can distinguish "nothing
# stale" from "could not tell".


def test_a_run_states_the_currency_direction_when_nothing_is_stale(
    kyo, project: Path
) -> None:
    new_record(kyo, kind="bug", title="one")
    write_bases(project, base_of())
    seam = kyo("base", "run", "only", "--json").json["db_export_currency"]
    assert seam["direction"] == "in-step"
    assert seam["db_is_behind"] is False


def test_a_renderer_can_tell_cannot_tell_apart_from_nothing_stale(
    kyo, project: Path
) -> None:
    """A view that can only paint 'stale' and 'not stale' has nowhere to put 'could
    not tell'. The two states have to be
    DISTINGUISHABLE in the payload, not merely both non-fatal — so this asserts the
    direction differs and that the undecidable one carries its reason.
    """
    new_record(kyo, kind="bug", title="one")
    write_bases(project, base_of())
    clean = kyo("base", "run", "only", "--json").json["db_export_currency"]

    # The counters agree and yet the record sets differ: a record document added by
    # hand, or a rebuild interrupted. `kyosei.currency` refuses to let the record sets
    # outvote the counters and says so instead of guessing.
    (project / "kyosei" / "export" / "TST-404.json").write_text("{}\n", encoding="utf-8")
    murky = kyo("base", "run", "only", "--json").json["db_export_currency"]

    assert clean["direction"] == "in-step"
    assert murky["direction"] == "cannot-tell"
    assert clean["direction"] != murky["direction"]
    assert clean["undecidable_because"] is None
    assert murky["undecidable_because"]
    assert "TST-404" in murky["says"]


def test_the_run_warns_on_the_terminal_from_the_same_comparison(kyo, project: Path) -> None:
    """The human line and the JSON block are one comparison, not two."""
    new_record(kyo, kind="bug", title="one")
    write_bases(project, base_of())
    (project / "kyosei" / "export" / "TST-404.json").write_text("{}\n", encoding="utf-8")
    out = kyo("base", "run", "only").out
    assert "WARNING" in out
    assert "TST-404" in out


# --------------------------------------------------------------------- KYO-108.c4
# The records source IS the `kyo list` engine.


def _list_filter_flags() -> set[str]:
    """The filter flags ``kyo list`` actually offers, read off the shipped parser."""
    parser = build_parser()
    [subparsers] = [
        action for action in parser._actions if hasattr(action, "choices") and action.choices
    ]
    list_parser = subparsers.choices["list"]
    # The flags every command accepts, read off the root parser rather than listed —
    # KYO-420 added two, and a hand-kept set here would have called them filters.
    common = {"--help", "-h"} | {
        option for action in parser._actions for option in action.option_strings
    }
    # KYO-420: the listing's own row-count flags, which a Base deliberately cannot
    # declare; the constant carries the reason.
    list_only = set(query_module.LIST_ONLY_FLAGS)
    return {
        option
        for action in list_parser._actions
        for option in action.option_strings
        if option.startswith("--") and option not in common and option not in list_only
    }


def test_every_kyo_list_filter_is_expressible_in_a_base(kyo) -> None:
    """KYO-108.c4's mechanism, and the reason the filter set is a dataclass.

    ``kyo list``'s flags are compared against :data:`kyosei.query.FILTER_NAMES`, which
    is also what a Base's ``filters:`` keys are derived from. Adding a filter to the
    listing and not to Bases — or the reverse — fails here rather than shipping as a
    surface that can ask a question the other cannot.
    """
    expected = {query_module.filter_flag(name) for name in query_module.FILTER_NAMES}
    assert _list_filter_flags() == expected
    assert set(query_module.FILTER_KEYS) == {
        flag[len("--") :] for flag in _list_filter_flags()
    }


@pytest.mark.parametrize(
    ("filter_key", "value"),
    [
        ("kind", "bug"),
        ("scope", "server"),
        ("status", "open"),
        ("gate-tag", "ui-review"),
    ],
)
def test_each_filter_selects_the_same_records_through_both_surfaces(
    kyo, project: Path, filter_key: str, value: str
) -> None:
    """Not merely "expressible": the same rows. One engine means one answer."""
    write_scopes(project, TWO_SCOPES)
    write_gate_tags(project, ONE_TAG)
    wanted = new_record(kyo, kind="bug", title="wanted")
    assert kyo("edit", wanted, "--scope", "server").code == 0
    assert kyo("gate", wanted, "awaiting:human-review", "--tag", "ui-review").code == 0
    other = new_record(kyo, kind="chore", title="other", criteria=["a"])
    assert kyo("edit", other, "--scope", "web-app").code == 0
    # Off `open`, un-gated, a different kind and a different scope: excluded by every
    # one of the four filters, so each parametrisation is a real narrowing.
    assert kyo("move", other, "working").code == 0

    write_bases(project, base_of(filters={filter_key: value}))
    through_a_base = [row["id"] for row in kyo("base", "run", "only", "--json").json["rows"]]
    through_the_list = [
        row["id"] for row in kyo("list", f"--{filter_key}", value, "--json").json["records"]
    ]
    assert through_a_base == through_the_list
    assert wanted in through_a_base
    assert other not in through_a_base


def test_the_plan_filter_selects_the_same_records_through_both_surfaces(
    kyo, project: Path
) -> None:
    plan = new_record(kyo, kind="plan", title="a plan")
    member = new_record(kyo, kind="bug", title="a member")
    assert kyo("edit", member, "--plan", plan).code == 0
    new_record(kyo, kind="bug", title="not a member")

    write_bases(project, base_of(filters={"plan": plan}))
    through_a_base = [row["id"] for row in kyo("base", "run", "only", "--json").json["rows"]]
    through_the_list = [
        row["id"] for row in kyo("list", "--plan", plan, "--json").json["records"]
    ]
    assert through_a_base == through_the_list == [member]


def test_a_base_inherits_the_standing_aware_status_filter(kyo, project: Path) -> None:
    """KYO-99, through the shared engine and not re-derived. ``status: open`` in a base
    means what it means in the listing: real, dispatchable work — never a knowledge
    record that merely stores the initial status name."""
    decision = new_record(kyo, kind="decision", title="how statuses are stored")
    work = new_record(kyo, kind="bug", title="a real open")

    write_bases(project, base_of(filters={"status": default_ladder().initial}))
    assert [row["id"] for row in kyo("base", "run", "only", "--json").json["rows"]] == [work]

    write_bases(project, base_of(filters={"status": STANDING_CURRENT}))
    assert [row["id"] for row in kyo("base", "run", "only", "--json").json["rows"]] == [decision]


def test_a_base_follows_a_renamed_status_ladder(kyo, project: Path) -> None:
    """KYO-99's role half, inherited: nothing in the shared engine compares against a
    status name frozen into the code, so a project that renames its retirement terminal
    can save a base on the new name and still find its voided knowledge under ``void``.
    """
    write_statuses(project, RENAMED_RETIREMENT)
    knowledge = new_record(kyo, kind="evidence", title="p95 measured at 40ms")
    work = new_record(kyo, kind="bug", title="a real open")
    assert kyo("void", knowledge, "--why", "measured the wrong thing").code == 0
    assert kyo("wont-do", work, "--why", "not worth doing").code == 0

    write_bases(project, base_of(filters={"status": "shelved"}))
    assert [row["id"] for row in kyo("base", "run", "only", "--json").json["rows"]] == [work]

    write_bases(project, base_of(filters={"status": STANDING_VOID}))
    assert [row["id"] for row in kyo("base", "run", "only", "--json").json["rows"]] == [knowledge]


def test_the_row_shape_is_one_shape(kyo, project: Path) -> None:
    """The rows a base returns and the rows the listing returns are the same dict, plus
    the base's per-row edge list — which is what makes a rendering built on one of them
    a rendering of the other."""
    new_record(kyo, kind="bug", title="one")
    write_bases(project, base_of())
    [listed] = kyo("list", "--json").json["records"]
    [ran] = kyo("base", "run", "only", "--json").json["rows"]
    assert set(ran) - set(listed) == {"links"}
    assert {key: ran[key] for key in listed} == listed
    assert set(listed) == set(query_module.ROW_FIELDS)


def test_the_engine_lives_in_one_module(kyo) -> None:
    """The structural half of "no duplicated query logic".

    The listing's scan over ``records`` and its open-gate pass exist once, in
    ``kyosei.query``, and neither command surface holds a copy. Deliberately not "no
    SQL in cmd_views": ``_plan_members`` is a different question (KYO-27, membership
    walked from the plan's side) and belongs to ``kyo show``. What must not recur is
    *this* query, which is why the assertion names it.
    """
    from kyosei import bases, cmd_views

    engine = Path(query_module.__file__).read_text(encoding="utf-8")
    assert engine.count("SELECT * FROM records") == 1
    assert "FROM gates WHERE cleared_at IS NULL" in engine
    for module in (cmd_views, bases):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "SELECT * FROM records" not in source, module.__name__
        assert "FROM gates" not in source, module.__name__


# --------------------------------------------------------------------- KYO-108.c5
# Base definitions are config, not store data.


def test_bases_add_no_stored_field_and_move_no_schema_version(kyo, project: Path) -> None:
    """A Base is a saved question about the store, never a thing in it. Shown at the
    store's own boundary: the exported schema_version and the record document's field
    set are untouched by a store that has bases and runs them."""
    from kyosei.schema import SCHEMA_VERSION

    record = new_record(kyo, kind="bug", title="one")
    before = json.loads((project / "kyosei" / "export" / f"{record}.json").read_text("utf-8"))
    write_bases(project, base_of(columns=["id", "title"], **{"group-by": "kind"}))
    assert kyo("base", "run", "only", "--json").code == 0

    after = json.loads((project / "kyosei" / "export" / f"{record}.json").read_text("utf-8"))
    assert after == before
    assert after["schema_version"] == SCHEMA_VERSION
    assert not {key for key in after if "base" in key}
    assert kyo("doctor", "--json").code == 0


def test_a_base_definition_is_not_reachable_through_the_store(kyo, project: Path) -> None:
    """The definitions live in a file the tool reads and never writes — the same
    relationship ``kinds.yml`` has. Nothing in ``kyo`` creates or edits one, which is
    why the refusal for an unknown base name tells the reader to edit the file."""
    write_bases(project, base_of())
    assert bases_module.bases_path(project / "kyosei").name == "bases.yml"
    result = kyo("base", "run", "nope")
    assert "add a `nope:` entry" in result.err
