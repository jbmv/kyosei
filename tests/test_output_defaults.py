"""KYO-428: kyo's output defaults as KYO-427 decided them.

KYO-420 built `--slim` and `--compact-json` as opt-ins; KYO-427 made both the default:

1. a write's ``--json`` embeds each record as its id, kind, status, updated_at and
   changed fields, and ``--full`` restores the whole document (``--slim`` is a no-op);
2. ``--json`` prints on one line when stdout is not a terminal and indented when it is,
   ``--compact-json`` and ``--pretty`` forcing either — error envelopes included;
3. ``kyo check-push --json`` never states a ``publishes`` it did not compute;
4. ``kyo redact --json`` carries no alias keys.

And what it deliberately left alone: ``kyo show --json`` is the whole record, and bare
``kyo list`` lists every record.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from kyosei import cli

from .conftest import new_record, verify_criterion
from .test_kyo60_push_guard import _payload_in
from .test_kyo137_docs_obligations import (
    _governed_doc_and_a_branch_that_only_touches_code,
    _merged_ingest,
)
from .test_output_flags import _record_documents, _slim_records

# ------------------------------------------------------------------------ c1 / c6


def _edited(kyo, tmp_path: Path, *flags: str):
    record = new_record(kyo, criteria=["it holds"])
    body = tmp_path / "description.md"
    body.write_text("A rewritten description.\n", encoding="utf-8")
    result = kyo("edit", record, "--description-file", str(body), "--json", *flags)
    assert result.code == 0, result
    return record, result


def test_edit_json_embeds_no_whole_record_by_default(kyo, tmp_path: Path) -> None:
    """Plain ``--json`` on a write is slim: no whole document anywhere in the payload,
    and the record it embeds says what this invocation changed."""
    record, result = _edited(kyo, tmp_path)
    payload = result.json
    assert _record_documents(payload) == [], payload
    embedded = payload["record"]
    assert embedded["slim"] is True
    assert embedded["id"] == record
    assert set(cli.SLIM_RECORD_FIELDS) <= embedded.keys()
    assert "description" in embedded["changed_fields"]
    assert "criteria" not in embedded


def test_edit_json_full_restores_the_whole_record(kyo, tmp_path: Path) -> None:
    record, result = _edited(kyo, tmp_path, "--full")
    embedded = result.json["record"]
    assert embedded.keys() >= cli.RECORD_DOCUMENT_KEYS, embedded
    assert "slim" not in embedded
    assert embedded["description"].strip() == "A rewritten description."
    # The same document `kyo show` reads, not a narrowing of it.
    shown = kyo("show", record, "--json").json
    assert embedded["criteria"] == shown["criteria"]


def test_slim_flag_is_a_no_op_alias_for_plain_json(kyo, tmp_path: Path) -> None:
    """c6. ``--slim`` is still accepted, and the payload is the one plain ``--json``
    prints. Two fresh records edited the same way, so only the id and timestamp
    differ between them."""
    first, plain = _edited(kyo, tmp_path)
    second, slim = _edited(kyo, tmp_path, "--slim")

    def comparable(payload: dict, record: str) -> str:
        text = json.dumps(payload, sort_keys=True).replace(record, "<ID>")
        return text.replace(payload["record"]["updated_at"], "<T>")

    assert comparable(plain.json, first) == comparable(slim.json, second)


def _accepted(kyo, git_project: Path, *flags: str) -> dict:
    record, _base = _governed_doc_and_a_branch_that_only_touches_code(kyo, git_project)
    dispatch_id, reported = _merged_ingest(
        kyo,
        git_project,
        record,
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
    assert reported.code == 0, reported
    accepted = kyo("accept", dispatch_id, "--json", *flags)
    assert accepted.code == 0, accepted
    return accepted.json


def test_accept_json_steps_embed_no_whole_record_by_default(kyo, git_project: Path) -> None:
    payload = _accepted(kyo, git_project)
    steps = payload["steps"]
    assert steps
    assert _record_documents(payload) == []
    assert all(_record_documents(step["data"]) == [] for step in steps)
    assert _slim_records(payload), "accept's steps embed no slimmed record at all"


def test_accept_json_full_restores_the_whole_records(kyo, git_project: Path) -> None:
    payload = _accepted(kyo, git_project, "--full")
    assert any(_record_documents(step["data"]) for step in payload["steps"]), payload
    assert _slim_records(payload) == []


# ------------------------------------------------------------------------ c2


class _Tty:
    """``sys.stdout`` as a terminal: what it is written is kept, and it says it is a tty."""

    def __init__(self) -> None:
        self.text = ""

    def write(self, text: str) -> int:
        self.text += text
        return len(text)

    def flush(self) -> None:  # noqa: D102 - the stream protocol
        pass

    def isatty(self) -> bool:
        return True


def _run(kyo, monkeypatch: pytest.MonkeyPatch, *args: str, tty: bool) -> str:
    """The stdout of one invocation, with stdout a terminal or not.

    The conftest runner redirects stdout to a ``StringIO``, which is not a tty; for the
    terminal half, ``isatty`` on that same redirected stream is forced true — the one
    fact ``cli.main`` reads — rather than allocating a pty."""
    if not tty:
        return kyo(*args).out
    real = cli._stdout_is_tty
    monkeypatch.setattr(cli, "_stdout_is_tty", lambda: True)
    try:
        return kyo(*args).out
    finally:
        monkeypatch.setattr(cli, "_stdout_is_tty", real)


#: A success envelope and an error envelope (an id that does not exist).
ENVELOPES = (("list", "--json"), ("show", "TST-99", "--json"))


def _is_compact(out: str) -> bool:
    return out.count("\n") == 1 and out.endswith("\n") and "\n  " not in out


def _is_indented(out: str) -> bool:
    return out.count("\n") > 1 and "\n  " in out


@pytest.mark.parametrize("args", ENVELOPES, ids=["success", "error"])
def test_json_is_compact_when_stdout_is_not_a_tty(kyo, monkeypatch, args) -> None:
    new_record(kyo, criteria=["a"])
    out = _run(kyo, monkeypatch, *args, tty=False)
    assert _is_compact(out), out
    json.loads(out)


@pytest.mark.parametrize("args", ENVELOPES, ids=["success", "error"])
def test_json_is_indented_when_stdout_is_a_tty(kyo, monkeypatch, args) -> None:
    new_record(kyo, criteria=["a"])
    out = _run(kyo, monkeypatch, *args, tty=True)
    assert _is_indented(out), out
    assert json.loads(out) == json.loads(_run(kyo, monkeypatch, *args, tty=False))


@pytest.mark.parametrize("args", ENVELOPES, ids=["success", "error"])
def test_pretty_forces_indentation_off_a_tty(kyo, monkeypatch, args) -> None:
    new_record(kyo, criteria=["a"])
    out = _run(kyo, monkeypatch, *args, "--pretty", tty=False)
    assert _is_indented(out), out
    # A common flag: before the command too.
    assert _run(kyo, monkeypatch, "--pretty", *args, tty=False) == out


@pytest.mark.parametrize("args", ENVELOPES, ids=["success", "error"])
def test_compact_json_forces_one_line_on_a_tty(kyo, monkeypatch, args) -> None:
    new_record(kyo, criteria=["a"])
    out = _run(kyo, monkeypatch, *args, "--compact-json", tty=True)
    assert _is_compact(out), out


def test_the_tty_is_read_off_stdout() -> None:
    """``_stdout_is_tty`` asks ``sys.stdout`` itself, so the tests above force the same
    fact the real process reads rather than a parallel one."""
    real = sys.stdout
    try:
        sys.stdout = _Tty()
        assert cli._stdout_is_tty() is True
        sys.stdout = object()  # a replaced stdout with no isatty at all
        assert cli._stdout_is_tty() is False
    finally:
        sys.stdout = real


# ------------------------------------------------------------------------ c3


def test_check_push_never_states_a_publishes_it_did_not_compute(kyo, git_project: Path) -> None:
    """In a repository whose store IS published by a push, the not-a-push path of
    ``kyo check-push --pre-tool-use --json`` must not say ``publishes: false``.

    KYO-427 chose "the real value" because it believed the value was already computed
    there. It is not: KYO-409.c1 returns before measuring anything on that path (a
    measurement costs 0.25 s on every Bash call an agent makes), and since KYO-409 the
    payload carries no ``publishes`` key at all there. The honest statement this pins is
    that whatever the payload says about ``publishes`` is the computed value — which on
    the not-a-push path means saying nothing, and on the judged path means the real one.
    """
    judged = kyo("check-push", "--json", "--advisory")
    computed = judged.json["publishes"]
    assert computed is True, judged  # the fixture repository publishes its store

    not_a_push = kyo(
        "check-push", "--pre-tool-use", "--json", stdin=_payload_in("ls", git_project)
    )
    assert not_a_push.code == 0, not_a_push
    assert not_a_push.json["matched"] is False
    assert not_a_push.json.get("publishes", computed) == computed, not_a_push.json


# ------------------------------------------------------------------------ c4


def test_redact_json_carries_no_alias_keys(kyo, tmp_path: Path) -> None:
    record = new_record(kyo, criteria=["a"])
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("an identifier to redact\n", encoding="utf-8")
    attached = kyo("attach", record, "--file", str(evidence), "--json")
    assert attached.code == 0, attached
    target = attached.json["evidence_id"]

    redacted = kyo("redact", target, "--why", "carries an identifier", "--json")
    assert redacted.code == 0, redacted
    payload = redacted.json
    assert "evidence_id" not in payload
    assert "events_scrubbed" not in payload
    # The surviving keys carry what the aliases did.
    assert payload["target_id"] == target
    assert isinstance(payload["ledger_lines_scrubbed"], int)


# ------------------------------------------------------------------------ c5


def test_show_json_is_still_the_whole_record(kyo) -> None:
    record = new_record(kyo, criteria=["a"])
    shown = kyo("show", record, "--json")
    assert shown.code == 0, shown
    assert shown.json.keys() >= cli.RECORD_DOCUMENT_KEYS
    assert "slim" not in shown.json
    assert shown.json["criteria"][0]["text"] == "a"
    assert kyo("show", record, "--json", "--full").json == shown.json


def test_bare_list_still_lists_done_and_void_records(kyo) -> None:
    kept = new_record(kyo, criteria=["a"])
    finished = new_record(kyo, kind="task", criteria=["a"])
    dropped = new_record(kyo, criteria=["a"])
    verify_criterion(kyo, f"{finished}.c1")
    moved = kyo("move", finished, "done")
    assert moved.code == 0, moved
    assert kyo("move", dropped, "wont-do", "--why", "not needed").code in (0, 2)
    listed = kyo("list", "--json")
    assert listed.code == 0, listed
    rows = {row["id"]: row["status"] for row in listed.json["records"]}
    assert set(rows) >= {kept, finished, dropped}, rows
    assert rows[finished] == "done"
    assert rows[dropped] == "wont-do"
    # The short listing is the opt-in, and it is shorter.
    unsettled = {row["id"] for row in kyo("list", "--unsettled", "--json").json["records"]}
    assert kept in unsettled and finished not in unsettled
