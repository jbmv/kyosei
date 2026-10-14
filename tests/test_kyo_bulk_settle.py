"""KYO-528: bulk forms of two commands that were one call per id.

* ``kyo flag resolve <id> <id> ... --why`` resolves several flags as one act (c1).
* ``kyo docs settle --record <id> --all --verdict --method`` settles every open
  obligation of one record as one act (c2).
"""

from __future__ import annotations

from pathlib import Path

from .test_kyo8_sweep import _promote_with_touches
from .test_kyo137_docs_obligations import _two_obligations


def _three_flags(kyo, project: Path, write_file) -> list[str]:
    for n in range(3):
        (project / f"w{n}.py").write_text("x = 1\n", encoding="utf-8")
        _promote_with_touches(kyo, project, write_file, touch_path=f"w{n}.py", name=f"p{n}")
    for n in range(3):
        (project / f"w{n}.py").unlink()
    flags = kyo("sweep", "--json").json["flags"]
    ids = [f["flag_id"] for f in flags]
    assert len(ids) == 3, ids
    return ids


def _open_flags(kyo) -> int:
    return kyo("flag", "list", "--json").json["open"]


# ------------------------------------------------------------------ c1: flag resolve


def test_flag_resolve_takes_several_ids_in_one_act(kyo, project: Path, write_file) -> None:
    ids = _three_flags(kyo, project, write_file)
    done = kyo("flag", "resolve", *ids, "--why", "all three are refactor noise", "--json")
    assert done.code == 0, done
    assert done.json["flag_ids"] == ids
    assert done.json["resolved"] == 3
    for flag_id in ids:
        assert flag_id in done.out
    assert _open_flags(kyo) == 0
    listed = kyo("flag", "list", "--all", "--json").json["flags"]
    assert {f["resolved_why"] for f in listed} == {"all three are refactor noise"}
    assert len({f["resolved_at"] for f in listed}) == 1


def test_one_unknown_id_refuses_the_whole_call_and_writes_nothing(
    kyo, project: Path, write_file
) -> None:
    ids = _three_flags(kyo, project, write_file)
    refused = kyo("flag", "resolve", ids[0], ids[1], "TST-1.f99", "--why", "x")
    assert refused.code == 1, refused
    assert "TST-1.f99" in refused.err
    assert _open_flags(kyo) == 3
    # The remedy: drop the unknown id and the same call lands.
    assert kyo("flag", "resolve", ids[0], ids[1], "--why", "x").code == 0
    assert _open_flags(kyo) == 1


def test_one_already_resolved_id_refuses_the_whole_call_and_names_the_remedy(
    kyo, project: Path, write_file
) -> None:
    ids = _three_flags(kyo, project, write_file)
    assert kyo("flag", "resolve", ids[0]).code == 0
    refused = kyo("flag", "resolve", *ids, "--why", "x")
    assert refused.code == 2, refused
    assert "already resolved" in refused.err
    assert "Nothing was resolved" in refused.err
    assert _open_flags(kyo) == 2
    assert kyo("flag", "resolve", ids[1], ids[2], "--why", "x").code == 0
    assert _open_flags(kyo) == 0


def test_a_single_id_keeps_its_one_flag_shape(kyo, project: Path, write_file) -> None:
    ids = _three_flags(kyo, project, write_file)
    done = kyo("flag", "resolve", ids[0], "--json")
    assert done.code == 0, done
    assert done.json["flag_id"] == ids[0]
    assert done.json["resolved"] == 1


# ------------------------------------------------------------------ c2: docs settle --all


def test_docs_settle_record_all_settles_every_open_obligation_naming_each(
    kyo, git_project: Path
) -> None:
    record, ids = _two_obligations(kyo, git_project)
    done = kyo(
        "docs", "settle", "--record", record, "--all", "--verdict", "not-affected",
        "--method", "read both", "--why", "neither section describes the internals",
        "--json",
    )
    assert done.code == 0, done
    assert [row["id"] for row in done.json["obligations"]] == ids
    assert done.json["settled"] == 2
    for obligation_id in ids:
        assert obligation_id in done.out
    assert kyo("docs", "obligations", "--json").json["open"] == 0


def test_docs_settle_all_with_nothing_open_says_so_and_writes_nothing(
    kyo, git_project: Path
) -> None:
    record, ids = _two_obligations(kyo, git_project)
    args = ("--record", record, "--all", "--verdict", "updated", "--method", "m", "--why", "w")
    assert kyo("docs", "settle", *args).code == 0
    before = kyo("show", record, "--json").json
    again = kyo("docs", "settle", *args)
    assert again.code == 0, again
    assert "no open docs obligations" in again.out
    assert kyo("show", record, "--json").json == before


def test_docs_settle_all_needs_a_record_and_takes_no_ids(kyo, git_project: Path) -> None:
    record, ids = _two_obligations(kyo, git_project)
    base = ("--verdict", "updated", "--method", "m", "--why", "w")
    no_record = kyo("docs", "settle", "--all", *base)
    assert no_record.code == 64, no_record
    assert "--record" in no_record.err
    with_ids = kyo("docs", "settle", ids[0], "--record", record, "--all", *base)
    assert with_ids.code == 64, with_ids
    assert kyo("docs", "obligations", "--json").json["open"] == 2
    # Remedy: the named form clears the first refusal.
    assert kyo("docs", "settle", "--record", record, "--all", *base).code == 0


def test_docs_settle_all_over_several_needs_a_reason(kyo, git_project: Path) -> None:
    record, _ = _two_obligations(kyo, git_project)
    refused = kyo(
        "docs", "settle", "--record", record, "--all", "--verdict", "updated", "--method", "m"
    )
    assert refused.code == 64, refused
    assert "--why" in refused.err
    assert kyo("docs", "obligations", "--json").json["open"] == 2
