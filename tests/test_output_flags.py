"""KYO-420: opt-in flags that make kyo's output smaller for an agent reading every byte.

The rule for this work: no DEFAULT output changes. Every
behaviour here is behind a flag, and the golden test at the bottom of this file is what
holds that rule up — it runs every command this work touched, in text mode and under
``--json``, on a fixture store with a fixed clock, and compares each output with a
fixture file captured from the code as it was before this change.

KYO-542: each pinned output is a normalised text file under tests/fixtures/pinned_output/,
not a hash, so a wording change is reviewed as a file diff. ``assert_pinned`` below is the
one comparison every pinned-output test uses; ``KYOSEI_UPDATE_PINNED=1 make test`` (or the
same variable on any one of these test files) rewrites the fixtures.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kyosei import cli, clock, contracts, query

from .conftest import invoke, new_record, valid_report
from .test_kyo137_docs_obligations import (
    _commit_anything,
    _git,
    _governed_doc_and_a_branch_that_only_touches_code,
)

#: The instant every tool-stamped timestamp reads in this file.
FIXED_INSTANT = datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)

#: The size of the evidence artifact attached to the scenario's main record. Large on
#: purpose: KYO-174 is 2.9 MB, mostly evidence, and that is the payload the slim flag
#: exists to keep out of a controller's context.
BIG_ARTIFACT_BYTES = 200_000


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D102 - the one override
        return FIXED_INSTANT if tz is not None else FIXED_INSTANT.replace(tzinfo=None)


@pytest.fixture
def fixed_clock(monkeypatch) -> None:
    """Every ``clock.now()`` reads :data:`FIXED_INSTANT`; every git commit is dated."""
    monkeypatch.setattr(clock, "datetime", _FixedDatetime)
    for name in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(name, "2026-01-02T03:04:05Z")


#: A full commit sha, and the 12-hex brief digest that quotes one. The fixture commits the
#: files `kyo init` writes, so editing any packaged default (documents.yml, statuses.yml,
#: kinds.yml) moved the fixture's base sha and with it every output quoting it: several
#: re-captures in one day, each verified by hand as "identical once shas are
#: normalised". This makes that verification the test's own.
_COMMIT_SHA = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")
_BRIEF_DIGEST = re.compile(r"sha256:[0-9a-f]{12}(?![0-9a-f])")


def _normalise(text: str, tmp_path: Path) -> str:
    """The run's own temporary directory, spelled one way whichever way it was reached, and
    commit shas (with the brief digests quoting them) replaced by placeholders."""
    for spelling in sorted({str(tmp_path), os.path.realpath(tmp_path)}, key=len, reverse=True):
        text = text.replace(spelling, "<TMP>")
    text = _COMMIT_SHA.sub("<SHA>", text)
    return _BRIEF_DIGEST.sub("sha256:<DIGEST>", text)


# ------------------------------------------------------------------------ KYO-542

#: Where every pinned output lives, one normalised text file each.
FIXTURES = Path(__file__).parent / "fixtures" / "pinned_output"
#: Set to rewrite the fixtures instead of comparing: the one documented way to change a pin.
UPDATE_ENV = "KYOSEI_UPDATE_PINNED"


def assert_pinned(name: str, text: str, *, fixtures: Path | None = None) -> None:
    """Compare already-normalised ``text`` with ``fixtures/<name>.txt``.

    A mismatch fails with a unified diff of the fixture against the new output. With
    ``KYOSEI_UPDATE_PINNED`` set the file is rewritten instead, so the change lands in the
    commit as a file diff a reviewer reads. A missing fixture fails naming the variable."""
    path = (fixtures or FIXTURES) / f"{name}.txt"
    if os.environ.get(UPDATE_ENV):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, "utf-8", newline="")
        return
    assert path.exists(), f"no fixture {path}; create it with {UPDATE_ENV}=1"
    expected = path.read_text("utf-8")
    if expected != text:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                text.splitlines(keepends=True),
                fromfile=f"fixture {path.name}",
                tofile="actual output",
            )
        )
        raise AssertionError(f"pinned output {name} changed (rewrite with {UPDATE_ENV}=1):\n{diff}")


def pin_name(*parts: str) -> str:
    """A fixture's relative name: each part reduced to lowercase words joined by ``-``."""
    return "/".join(re.sub(r"[^a-z0-9]+", "-", part.lower()).strip("-") for part in parts)


def _scenario(
    kyo, project: Path, tmp_path: Path, flags: list[str], *, big_everywhere: bool = False
) -> list[tuple[str, object]]:
    """Every command KYO-420 touched, run once each with ``flags`` appended.

    Returns ``[(label, Run)]``. A fresh project per call, so the text and ``--json``
    captures come from two identical histories rather than one command run twice.
    ``big_everywhere`` also gives the second record the large artifact, so every
    mutation in the slim test runs against one (off for the golden, whose history is
    fixed).
    """
    runs: list[tuple[str, object]] = []

    def run(label: str, *args: str, stdin: str | None = None):
        result = kyo(*args, *flags, stdin=stdin)
        runs.append((label, result))
        return result

    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, project)
    big = tmp_path / "big-evidence.txt"
    big.write_text(("0123456789abcdef" * 64 + "\n") * (BIG_ARTIFACT_BYTES // 1025), "utf-8")
    assert run("attach", "attach", record, "--file", str(big)).code == 0

    other = new_record(kyo, kind="task", title="A second record", criteria=["it holds"])
    if big_everywhere:
        assert kyo("attach", other, "--file", str(big)).code == 0
    assert run("link", "link", other, "blocks", record).code == 0
    assert run("move", "move", other, "working").code == 0
    assert (
        run(
            "verify", "verify", f"{other}.c1", "--verdict", "met", "--method", "reasoned-about"
        ).code
        == 0
    )
    small = tmp_path / "small-evidence.txt"
    small.write_text("a line to redact\n", "utf-8")
    attached = kyo("attach", other, "--file", str(small), "--json")
    assert attached.code == 0, attached
    assert run("redact", "redact", attached.json["evidence_id"], "--why", "a test").code == 0

    third = new_record(kyo, kind="task", title="A withdrawn dispatch", criteria=["x"])
    third_dispatch = kyo("dispatch", third, "--no-create-worktree", "--branch", "kyo-137", "--json")
    assert third_dispatch.code == 0, third_dispatch
    run(
        "dispatch close",
        "dispatch",
        "close",
        third_dispatch.json["dispatch_id"],
        "--withdrawn",
        "--why",
        "frozen by mistake",
    )

    # The base: a saved view whose filters declare a single-string status (c5).
    (project / "kyosei" / "bases.yml").write_text(
        "bases:\n  open-work:\n    summary: every open record\n    source: records\n"
        "    filters:\n      status: open\n",
        "utf-8",
    )
    run("base run", "base", "run", "open-work")

    run("show", "show", record)
    run("show other", "show", other)
    run("list", "list")
    run("list status", "list", "--status", "open")
    run("list kind", "list", "--kind", "task")
    run("list unknown status", "list", "--status", "nonsense")
    run("show missing", "show", "TST-99")

    _commit_anything(project, "the store, before the guards")
    run("check-branch", "check-branch", "kyo-137", "--against", "main")
    run("check-clean", "check-clean")

    # KYO-570: `other` blocks `record` and is unsettled, so the dispatch needs --force.
    dispatched = run(
        "dispatch", "dispatch", record, "--no-create-worktree", "--branch", "kyo-137", "--force"
    )
    assert dispatched.code == 0, dispatched
    dispatch_id = f"{record}.d1"
    sha = _git(project, "rev-parse", "kyo-137")
    _commit_anything(project, "the store, before the merge")
    _git(project, "merge", "--no-ff", "-m", "merge kyo-137", "kyo-137")
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            valid_report(
                dispatch_id=dispatch_id,
                record_id=record,
                worked_from_sha=sha,
                criteria_verdicts=[
                    {
                        "criterion_id": f"{record}.c1",
                        "verdict": "met",
                        "method": "machine-checked",
                        "evidence": "make test: exit=0",
                    }
                ],
                touched_files=["src/app.py"],
            )
        ),
        "utf-8",
    )
    assert run("report", "report", dispatch_id, "--file", str(report_path)).code == 0
    run("accept", "accept", dispatch_id)

    # The failing guards, which --quiet must leave alone.
    (project / "stray.txt").write_text("uncommitted\n", "utf-8")
    run("check-clean failing", "check-clean")
    _git(project, "checkout", "-q", "-b", "touches-the-store")
    (project / "ideas" / "sneaky.md").write_text("# sneaky\n", "utf-8")
    _git(project, "add", "ideas/sneaky.md")
    _git(project, "commit", "-q", "-m", "a worker writing the ideas layer")
    run("check-branch failing", "check-branch", "touches-the-store", "--against", "main")
    return runs


def _pinned_text(result, tmp_path: Path) -> str:
    text = f"{result.code}\n--out--\n{result.out}\n--err--\n{result.err}"
    return _normalise(text, tmp_path)


def _scenario_in(tmp_path: Path, name: str, flags: list[str], **options):
    root = tmp_path / name / "proj"
    created = invoke(["init", str(root), "--in-repo", "--prefix", "TST"])
    assert created.code == 0, created
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)

    def kyo(*args: str, stdin: str | None = None):
        return invoke(["--root", str(root), *args], stdin=stdin)

    kyo.project = root
    # Normalised against the TEST's tmp_path, not this scenario's: $KYOSEI_HOME (where
    # dispatch worktrees resolve) is the conftest's, one level up.
    return _scenario(kyo, root, tmp_path / name, flags, **options), tmp_path


# ------------------------------------------------------------------------ KYO-420.c7


@pytest.mark.parametrize("mode", ["text", "json"])
#: Fixture history (KYO-542 dropped the digests; each fixture's own git log is the record
#: now). Every re-capture before it is in git history of this file. A wording change is
#: reviewed as the fixture diff `KYOSEI_UPDATE_PINNED=1` produces.
def test_every_touched_commands_default_output_is_unchanged(
    tmp_path: Path, fixed_clock, mode: str
) -> None:
    # KYO-427 moved the `--json` default on purpose (slim, and compact off a terminal).
    # What this golden now holds is that the two opt-outs restore the output as it was
    # before KYO-420, byte for byte: `--json --full --pretty` is the old `--json`, and
    # the digests below are unchanged. The new default is pinned in
    # tests/test_output_defaults.py.
    flags = ["--json", "--full", "--pretty"] if mode == "json" else []
    runs, where = _scenario_in(tmp_path, mode, flags)
    for label, result in runs:
        text = _pinned_text(result, where)
        dump = os.environ.get("KYO420_DUMP_DIR")
        if dump:
            (Path(dump) / f"{mode}-{label}.txt").write_text(text, "utf-8")
        assert_pinned(pin_name("output_flags", mode, label), text)


# ------------------------------------------------------------------------ KYO-420.c1

#: The mutations c1 names, by the scenario's labels.
SLIMMED_MUTATIONS = (
    "attach",
    "link",
    "move",
    "verify",
    "redact",
    "dispatch close",
    "dispatch",
    "report",
    "accept",
)


def _record_documents(value) -> list[dict]:
    """Every whole record document anywhere inside ``value``."""
    found: list[dict] = []
    if isinstance(value, dict):
        if value.keys() >= cli.RECORD_DOCUMENT_KEYS:
            found.append(value)
        for item in value.values():
            found += _record_documents(item)
    elif isinstance(value, list):
        for item in value:
            found += _record_documents(item)
    return found


def _slim_records(value) -> list[dict]:
    found: list[dict] = []
    if isinstance(value, dict):
        if value.get("slim") is True and "changed_fields" in value:
            found.append(value)
        for item in value.values():
            found += _slim_records(item)
    elif isinstance(value, list):
        for item in value:
            found += _slim_records(item)
    return found


def test_slim_mutation_payloads_carry_ids_and_changes_not_documents(tmp_path: Path) -> None:
    """c1. Every mutation, against records carrying a 200 KB artifact, under
    ``--json --slim``: smaller than the artifact, no whole document anywhere, and each
    embedded record reduced to its id, status and the fields this invocation changed.
    Remove the post-processing in ``cli.main`` and every payload is back over 200 KB."""
    runs, _ = _scenario_in(tmp_path, "slim", ["--json", "--slim"], big_everywhere=True)
    by_label = dict(runs)
    for label in SLIMMED_MUTATIONS:
        result = by_label[label]
        assert result.code == 0, (label, result)
        payload = result.json
        assert len(result.out) < BIG_ARTIFACT_BYTES, (label, len(result.out))
        assert _record_documents(payload) == [], label
        slimmed = _slim_records(payload)
        assert slimmed, f"{label}: no slimmed record at all"
        for record in slimmed:
            assert set(cli.SLIM_RECORD_FIELDS) <= record.keys(), (label, record)
            assert record["id"].startswith("TST-"), (label, record)
            assert isinstance(record["changed_fields"], list), (label, record)

    def changed(label: str, record_id: str) -> set[str]:
        # `accept` embeds the same record once per composed step.
        records = [r for r in _slim_records(by_label[label].json) if r["id"] == record_id]
        assert records, (label, record_id)
        return {name for record in records for name in record["changed_fields"]}

    assert "evidence_artifacts" in changed("attach", "TST-1")
    assert {"status", "updated_at"} <= changed("move", "TST-2")
    assert _slim_records(by_label["move"].json)[0]["status"] == "working"
    assert "criteria" in changed("verify", "TST-2")
    assert "links" in changed("link", "TST-1") and "links" in changed("link", "TST-2")
    assert "dispatches" in changed("dispatch", "TST-1")
    assert "reports" in changed("report", "TST-1")
    # accept: the nested steps[].data are slimmed too, and the changes are this
    # invocation's (the verify step moved the criterion).
    steps = by_label["accept"].json["steps"]
    assert steps and all(_record_documents(step["data"]) == [] for step in steps)
    assert "criteria" in changed("accept", "TST-1")


def test_slim_leaves_text_output_and_kyo_show_alone(kyo) -> None:
    """``--slim`` is a ``--json`` switch: text is untouched, and ``kyo show``'s own
    document is the record itself, not an embedded copy, so it is not narrowed."""
    record = new_record(kyo, criteria=["a"])
    assert kyo("show", record, "--slim").out == kyo("show", record).out
    assert kyo("show", record, "--json", "--slim").json == kyo("show", record, "--json").json


def test_slim_changed_fields_are_null_when_nothing_was_diffed() -> None:
    """A read-only command has no events to diff; ``changed_fields`` says ``None``
    rather than ``[]``, which would read as "nothing changed"."""
    document = dict.fromkeys(cli.RECORD_DOCUMENT_KEYS, [])
    document.update(id="TST-1", kind="bug", status="open", updated_at="t")
    slimmed = cli.slim_payload({"record": document}, None)
    assert slimmed["record"]["changed_fields"] is None
    assert slimmed["record"]["id"] == "TST-1"


# ------------------------------------------------------------------------ KYO-420.c2


def test_compact_json_is_one_line_and_the_same_object(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    for args in (["list"], ["show", record], ["move", record, "working"]):
        default = kyo(*args, "--json") if args[0] != "move" else None
        compact = kyo(*args, "--json", "--compact-json")
        assert compact.code == 0, compact
        assert compact.out.count("\n") == 1 and compact.out.endswith("\n"), args
        assert "\n  " not in compact.out
        if default is not None:
            assert compact.json == default.json, args
    # Before the command too, like every common flag.
    before = kyo("--compact-json", "list", "--json").out
    assert before == kyo("list", "--json", "--compact-json").out


def test_compact_json_covers_the_error_envelope(kyo) -> None:
    """c2: the KyoseiError handler's envelope is printed through the same switch.
    KYO-427: off a terminal the default is already compact, so the indented side is
    asked for with ``--pretty``."""
    default = kyo("show", "TST-99", "--json", "--pretty")
    compact = kyo("show", "TST-99", "--json", "--compact-json")
    assert default.code == compact.code != 0
    assert default.out.count("\n") > 1
    assert compact.out.count("\n") == 1
    assert json.loads(compact.out) == json.loads(default.out)
    assert compact.err == default.err


# ------------------------------------------------------------------------ KYO-420.c3


def test_show_fields_returns_only_the_named_keys(kyo) -> None:
    record = new_record(kyo, title="Narrow me", criteria=["a"])
    shown = kyo("show", record, "--json", "--fields", "status,title")
    assert shown.code == 0, shown
    assert shown.json == {"ok": True, "command": "show", "status": "open", "title": "Narrow me"}
    # A dispatch-shaped or other id carries its own envelope; the rule is the same.
    assert set(kyo("show", record, "--json", "--fields", "id").json) == {"ok", "command", "id"}


def test_show_fields_refuses_an_unknown_field_naming_the_valid_ones(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    refused = kyo("show", record, "--json", "--fields", "status,nonsense")
    assert refused.code == 64
    assert "'nonsense'" in refused.err
    assert "Valid fields:" in refused.err and "status" in refused.err and "title" in refused.err
    # The remedy the refusal names clears it.
    assert kyo("show", record, "--json", "--fields", "status").code == 0


def test_show_fields_without_json_is_refused_not_ignored(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    refused = kyo("show", record, "--fields", "status")
    assert refused.code == 64
    assert "--json" in refused.err
    assert kyo("show", record, "--json", "--fields", "status").code == 0


# ------------------------------------------------------------------------ KYO-420.c4


def _ids(run) -> list[str]:
    return [row["id"] for row in run.json["records"]]


def test_list_status_repeats_as_either(kyo) -> None:
    first = new_record(kyo, criteria=["a"])
    second = new_record(kyo, criteria=["a"])
    third = new_record(kyo, criteria=["a"])
    assert kyo("move", second, "working").code == 0
    assert kyo("move", third, "wont-do", "--why", "not needed").code in (0, 2)
    either = kyo("list", "--status", "open", "--status", "working", "--json")
    assert either.code == 0, either
    assert _ids(either) == [first, second]
    assert _ids(kyo("list", "--status", "open", "--json")) == [first]


def test_list_status_refuses_any_undeclared_value(kyo) -> None:
    ladder = contracts.default_ladder()
    refused = kyo("list", "--status", "open", "--status", "nonsense")
    assert refused.code == 64
    # The engine's own sentence. The packaged ladder's path differs from the project's
    # copy, so the comparison is from "declares:" on, which is the ladder itself.
    expected = query.unknown_status_refusal(ladder, "nonsense")
    assert "'nonsense' is not a status;" in refused.err
    assert expected.split(" declares: ", 1)[1] in refused.err


def test_list_limit_caps_rows_and_says_so(kyo) -> None:
    made = [new_record(kyo, criteria=["a"]) for _ in range(3)]
    limited = kyo("list", "--limit", "2")
    assert limited.code == 0
    rows = limited.out.splitlines()
    assert len(rows) == 3  # two rows and the summary line
    assert "limited to 2 by --limit: showing 2 of 3" in rows[-1]
    as_json = kyo("list", "--limit", "2", "--json").json
    assert _ids(kyo("list", "--limit", "2", "--json")) == made[:2]
    assert as_json["summary"]["total"] == 3
    assert as_json["summary"]["limited"] is True and as_json["summary"]["shown"] == 2
    assert kyo("list", "--limit", "0").code == 64


def test_list_unsettled_leaves_out_complete_and_retired(kyo, project: Path) -> None:
    open_one = new_record(kyo, criteria=["a"])
    working = new_record(kyo, criteria=["a"])
    retired = new_record(kyo, criteria=["a"])
    assert kyo("move", working, "working").code == 0
    wont = kyo("wont-do", retired, "--why", "not needed")
    assert wont.code == 0, wont
    ladder = contracts.default_ladder()
    listed = kyo("list", "--unsettled", "--json")
    assert listed.code == 0, listed
    assert _ids(listed) == [open_one, working]
    assert all(not ladder.is_settled(row["status"]) for row in listed.json["records"])
    # Everything is still there without the flag.
    assert retired in _ids(kyo("list", "--json"))
    # And it combines with --status: both must hold.
    assert _ids(kyo("list", "--unsettled", "--status", "wont-do", "--json")) == []


# ------------------------------------------------------------------------ KYO-420.c5


def test_a_base_declaring_a_single_string_status_runs_as_before(kyo, project: Path) -> None:
    first = new_record(kyo, criteria=["a"])
    second = new_record(kyo, criteria=["a"])
    assert kyo("move", second, "working").code == 0
    (project / "kyosei" / "bases.yml").write_text(
        "bases:\n  open-work:\n    summary: every open record\n    source: records\n"
        "    filters:\n      status: open\n",
        "utf-8",
    )
    ran = kyo("base", "run", "open-work", "--json")
    assert ran.code == 0, ran
    assert [row["id"] for row in ran.json["rows"]] == [first]
    assert ran.json["base"]["filters"] == {"status": "open"}
    assert query.Filters.from_mapping({"status": "open"}).status == "open"


# ------------------------------------------------------------------------ KYO-420.c6


def test_quiet_check_clean_passes_in_one_line(kyo, git_project: Path) -> None:
    _commit_anything(git_project, "settle the store")
    loud = kyo("check-clean")
    quiet = kyo("check-clean", "--quiet")
    assert loud.code == quiet.code == 0
    assert len(loud.out.splitlines()) > 1
    assert len(quiet.out.splitlines()) == 1
    assert str(git_project) in quiet.out and "clean" in quiet.out
    assert kyo("check-clean", "--quiet", "--json").json == kyo("check-clean", "--json").json


def test_quiet_check_clean_failing_prints_in_full(kyo, git_project: Path) -> None:
    _commit_anything(git_project, "settle the store")
    (git_project / "stray.py").write_text("x = 1\n", "utf-8")
    loud = kyo("check-clean")
    quiet = kyo("check-clean", "--quiet")
    assert loud.code == 2
    assert (quiet.code, quiet.out, quiet.err) == (loud.code, loud.out, loud.err)


def test_quiet_check_branch_passes_in_one_line_and_fails_in_full(kyo, git_project: Path) -> None:
    _commit_anything(git_project, "settle the store")
    _git(git_project, "checkout", "-q", "-b", "clean-branch")
    (git_project / "code.py").write_text("x = 1\n", "utf-8")
    _commit_anything(git_project, "code only")
    loud = kyo("check-branch", "--against", "main")
    quiet = kyo("check-branch", "--against", "main", "--quiet")
    assert loud.code == quiet.code == 0, (loud, quiet)
    assert len(loud.out.splitlines()) > 1
    assert len(quiet.out.splitlines()) == 1
    assert "clean-branch" in quiet.out and "clean" in quiet.out and str(git_project) in quiet.out

    (git_project / "ideas" / "sneaky.md").write_text("# sneaky\n", "utf-8")
    _commit_anything(git_project, "a worker writing the ideas layer")
    loud = kyo("check-branch", "--against", "main")
    quiet = kyo("check-branch", "--against", "main", "--quiet")
    assert loud.code == 2
    assert (quiet.code, quiet.out, quiet.err) == (loud.code, loud.out, loud.err)


# ------------------------------------------------------------------------ KYO-420.c9


def test_every_new_flag_is_in_its_commands_help() -> None:
    def help_of(*command: str) -> str:
        return invoke([*command, "--help"]).out

    assert "--fields" in help_of("show")
    listing = help_of("list")
    for flag in ("--status", "--limit", query.UNSETTLED_FLAG):
        assert flag in listing, flag
    assert "--quiet" in help_of("check-clean")
    assert "--quiet" in help_of("check-branch")
    for command in ((), ("list",), ("move",), ("accept",)):
        text = help_of(*command)
        assert cli.SLIM_FLAG in text and cli.COMPACT_JSON_FLAG in text, command


def test_the_reference_lists_the_global_flags_as_accepted_everywhere() -> None:
    """The reference's "accepted everywhere" section is derived from the parser; the
    two global flags reach it because they are common flags, not per-command ones."""
    rendered = cli.reference_markdown()
    everywhere = rendered.split("## Options accepted everywhere", 1)[1].split("\n## ", 1)[0]
    assert cli.SLIM_FLAG in everywhere and cli.COMPACT_JSON_FLAG in everywhere


def test_readme_shows_the_new_list_flags() -> None:
    from tests.reference_pages import OPERATING

    readme = OPERATING.read_text("utf-8")  # the README section, moved (KYO-516)
    for example in (
        "kyo list --status open --status working",
        "kyo list --unsettled",
        "kyo list --unsettled --limit 20",
        "kyo show KYO-1 --json --fields status,title",
    ):
        assert example in readme, example


# ------------------------------------------------------------------------ KYO-542


def test_a_changed_output_fails_with_a_diff_of_the_fixture(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("one\ntwo\n", "utf-8")
    assert_pinned("x", "one\ntwo\n", fixtures=tmp_path)
    with pytest.raises(AssertionError) as caught:
        assert_pinned("x", "one\nTWO\n", fixtures=tmp_path)
    message = str(caught.value)
    assert "-two" in message and "+TWO" in message and UPDATE_ENV in message


def test_an_altered_fixture_fails_the_pin(tmp_path: Path) -> None:
    # The real fixtures: copy one, alter it, and the same output no longer passes.
    fixture = next(FIXTURES.rglob("*.txt"))
    name = str(fixture.relative_to(FIXTURES).with_suffix(""))
    text = fixture.read_text("utf-8")
    assert_pinned(name, text)
    copy = tmp_path / fixture.relative_to(FIXTURES)
    copy.parent.mkdir(parents=True)
    copy.write_text(text + "tampered\n", "utf-8")
    with pytest.raises(AssertionError):
        assert_pinned(name, text, fixtures=tmp_path)


def test_the_update_variable_rewrites_the_fixture(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(AssertionError, match=UPDATE_ENV):
        assert_pinned("sub/new", "a\n", fixtures=tmp_path)
    monkeypatch.setenv(UPDATE_ENV, "1")
    assert_pinned("sub/new", "a\n", fixtures=tmp_path)
    monkeypatch.delenv(UPDATE_ENV)
    assert (tmp_path / "sub" / "new.txt").read_text("utf-8") == "a\n"
    assert_pinned("sub/new", "a\n", fixtures=tmp_path)


def test_no_pin_constant_survives_in_the_pinned_output_tests() -> None:
    here = Path(__file__).parent
    names = ["GOLD" + "EN", "REFUSALS_" + "BEFORE", "KEYS_" + "BEFORE", "REVIEW_BLOCK_" + "BEFORE"]
    for name in (
        "test_output_flags.py",
        "test_kyo482_explanations.py",
        "test_kyo482_once_per_invocation.py",
    ):
        source = (here / name).read_text("utf-8")
        assert "import hash" + "lib" not in source, name
        for constant in names:
            assert constant not in source, (name, constant)
