"""KYO-413.c5: three inputs the report validator let through or crashed on.

- ``SHA_RE.match`` accepted ``"abc1234\\n"``: Python's ``$`` matches before a
  trailing newline.
- Two ``criteria_verdicts`` entries for one ``criterion_id`` validated clean.
- ``reason: 5`` on a reason-requiring verdict raised ``AttributeError`` out of
  ``.strip()`` instead of returning a problem line.

Each case is a problem line from :func:`validate_report`, never an exception.
"""

from __future__ import annotations

from kyosei.report_schema import validate_report

from .conftest import valid_report


def _report(**kwargs) -> dict:
    kwargs.setdefault("worked_from_sha", "abc1234")
    return valid_report(dispatch_id="TST-1.d1", record_id="TST-1", **kwargs)


def _verdict(cid: str, verdict: str = "met", **extra) -> dict:
    return {"criterion_id": cid, "verdict": verdict, "method": "reasoned-about", **extra}


def test_the_baseline_report_is_valid() -> None:
    assert validate_report(_report(criteria_verdicts=[_verdict("TST-1.c1")])) == []


def test_a_sha_with_a_trailing_newline_is_refused() -> None:
    problems = validate_report(_report(worked_from_sha="abc1234\n"))
    assert any(p.startswith("worked_from_sha:") for p in problems), problems
    # The same trailing newline on a commit sha is refused by the same check.
    problems = validate_report(_report(commits=[{"sha": "abc1234\n"}]))
    assert any("commits[0].sha" in p for p in problems), problems


def test_a_repeated_criterion_id_is_refused() -> None:
    problems = validate_report(
        _report(
            criteria_verdicts=[
                _verdict("TST-1.c1", "met"),
                _verdict("TST-1.c1", "unmet-unbuilt"),
            ]
        )
    )
    assert problems == [
        "criteria_verdicts[1]: criterion_id 'TST-1.c1' already has a verdict at "
        "criteria_verdicts[0] -- one entry per criterion; merge the two into one "
        "verdict (put the other observation in its notes)"
    ]


def test_distinct_criterion_ids_are_not_refused() -> None:
    doc = _report(criteria_verdicts=[_verdict("TST-1.c1"), _verdict("TST-1.c2")])
    assert validate_report(doc) == []


def test_a_non_string_reason_on_a_reason_requiring_verdict_is_a_problem() -> None:
    doc = _report(
        criteria_verdicts=[_verdict("TST-1.c1", "unmet-unobserved", reason=5)]
    )
    problems = validate_report(doc)  # must not raise AttributeError
    assert "criteria_verdicts[0].reason: must be a string, got int" in problems
    assert any("requires a non-empty 'reason'" in p for p in problems), problems

    doc["criteria_verdicts"][0]["reason"] = "no access to the scheduler host"
    assert validate_report(doc) == []
