"""An authorized credit may name verifications and gates, not only records (KYO-668).

The maintainer's rule (KYO-664's evidence): a one-word acceptance only authorizes the
go-ahead. The agents recorded some of those go-aheads as a criterion's verdict or a gate's
clearing, so the role must reach those rows too; a decided credit still names a record.
"""

from __future__ import annotations

from pathlib import Path

from .conftest import CREDIT_ELSEWHERE, declare_person, new_record
from .test_kyo644_credits import _approval, _credits, _file, _legacy_verdict, _subjects


def _batch(tmp_path: Path, *lines: str) -> str:
    return _file(tmp_path / "batch.tsv", "".join(line + "\n" for line in lines))


def test_kyo_credit_writes_an_authorized_credit_on_a_verdict(kyo, project: Path, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    declare_person(project)
    vid = _legacy_verdict(kyo, project, record)
    basis = _approval(kyo, project, tmp_path)
    ran = kyo("credit", vid, "--role", "authorized", "--quote-file", _file(tmp_path / "w", "yes"),
              *CREDIT_ELSEWHERE, "--basis", basis, "--json")  # fmt: skip
    assert ran.code == 0, ran
    credit = next(c for c in _credits(project) if c["id"] == ran.json["credit_id"])
    assert credit["role"] == "authorized"
    assert _subjects(project, credit["id"]) == [("verification", vid)]


def test_a_batch_writes_an_authorized_credit_on_a_verdict(kyo, project: Path, tmp_path) -> None:
    record = new_record(kyo, criteria=["a"])
    declare_person(project)
    vid = _legacy_verdict(kyo, project, record)
    basis = _approval(kyo, project, tmp_path)
    line = "\t".join([vid, "authorized", "other:the test suite", "", "", "", "approved"])
    ran = kyo("credit", "--batch", _batch(tmp_path, line), "--basis", basis)
    assert ran.code == 0, ran
    (credit,) = [c for c in _credits(project) if c["basis_id"] == basis]
    assert credit["role"] == "authorized"
    assert _subjects(project, credit["id"]) == [("verification", vid)]


def test_decided_on_a_verdict_and_observed_on_a_record_stay_refused(
    kyo, project: Path, tmp_path
) -> None:
    record = new_record(kyo, criteria=["a"])
    declare_person(project)
    vid = _legacy_verdict(kyo, project, record)
    basis = _approval(kyo, project, tmp_path)
    words = _file(tmp_path / "w", "yes")
    decided = kyo("credit", vid, "--role", "decided", "--quote-file", words, *CREDIT_ELSEWHERE,
                  "--basis", basis)  # fmt: skip
    assert decided.code == 64 and "decided credit names a record" in decided.err, decided
    observed = kyo("credit", record, "--role", "observed", "--quote-file", words,
                   *CREDIT_ELSEWHERE, "--basis", basis)  # fmt: skip
    assert observed.code == 64 and "not a record" in observed.err, observed
    mixed = kyo("credit", record, vid, "--role", "authorized", "--quote-file", words,
                *CREDIT_ELSEWHERE, "--basis", basis)  # fmt: skip
    assert mixed.code == 64 and "not both" in mixed.err, mixed
