#!/usr/bin/env python3
"""Measure the Stop check's detector against transcripts and known discussion spans (KYO-662.c1).

Usage:
    python tests/fixtures/kyo662_measure_detector.py --spans SPANS.tsv DIR [DIR ...] [--json]

The transcripts and the spans are private and are never part of this repository. This script
takes their location at run time, reads them, and prints counts only: no transcript text, no
session id, no record uuid. ``tests/test_kyo662_stop_check.py`` runs it when the environment
names the files (``KYOSEI_KYO662_TRANSCRIPTS``, directories separated by the path separator,
and ``KYOSEI_KYO662_SPANS``) and is skipped otherwise.

Inputs
    DIR         directories of Claude Code session transcripts (``<session>.jsonl``). A session
                present in several directories is read once, from its largest file.
    SPANS.tsv   one row per discussion span, tab separated with a header, carrying at least
                ``session``, ``opening_uuid`` (the agent's message the discussion answers, or
                empty), ``first_uuid`` (the person's first turn in it) and ``last_uuid`` (the
                turn that decided).

What is measured, with the detector the hook ships (``kyosei.cmd_discuss.detect``)
    turns       A turn is what ``kyosei.cmd_discuss.split_turns`` yields; an agent turn is one a
                prompt opened and the agent answered with text. Each agent turn's end is one
                Stop.
    recall      Over the spans whose first turn is a prompt the person typed (a span that
                starts with a message sent mid-turn or with a multiple-choice answer has no
                Stop of its own to be caught at): the share for which the detector fires at
                the end of the turn holding that prompt, or at the end of the turn holding the
                span's opening message. A span whose turn the agent never answered with text
                had no Stop and counts as missed unless its opening message was caught.
    firing      The number of agent turn ends at which the detector fires, split into those
                inside a span (from the turn holding its opening message, or its first turn, to
                the turn holding its last), where a discussion would already be open and the
                check says nothing, and those outside every span, each of which would cost the
                agent one ``open`` or ``skip``.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

from kyosei.cmd_discuss import CUE_ASKED, CUE_PERSON, CUE_REPLY, detect, split_turns, turn_view


def _records(path: Path):
    with path.open(encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            try:
                record = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(record, dict):
                yield record


def _sessions(directories: list[Path]) -> dict[str, Path]:
    best: dict[str, Path] = {}
    for directory in directories:
        for path in sorted(directory.glob("*.jsonl")):
            if path.name == "history.jsonl":
                continue
            held = best.get(path.stem)
            if held is None or path.stat().st_size > held.stat().st_size:
                best[path.stem] = path
    return best


def measure(directories: list[Path], spans_path: Path) -> dict:
    sessions = _sessions(directories)
    turn_of: dict[str, dict[str, int]] = {}  # session -> record uuid -> agent-turn number
    typed_by: dict[str, set[str]] = {}  # session -> uuids of prompts the person typed
    fired: dict[str, list[list[str] | None]] = {}  # session -> cues per turn; None: no Stop
    typed = 0
    for session, path in sessions.items():
        uuids: dict[str, int] = {}
        prompts: set[str] = set()
        cues: list[list[str] | None] = []
        for records in split_turns(_records(path)):
            view = turn_view(records)
            number = len(cues)
            for record in records:
                if not isinstance(record.get("uuid"), str):
                    continue
                uuids[record["uuid"]] = number
                origin = record.get("origin")
                if (
                    record.get("type") == "user"
                    and record.get("toolUseResult") is None
                    and isinstance(origin, dict)
                    and origin.get("kind") == "human"
                ):
                    prompts.add(record["uuid"])
            if view.prompt_id is None or view.reply is None:
                cues.append(None)
                continue
            if view.origin == "human" and view.person:
                typed += 1
            cues.append(detect(view.person, view.reply, view.asked))
        turn_of[session], typed_by[session], fired[session] = uuids, prompts, cues

    with spans_path.open(encoding="utf-8", newline="") as handle:
        spans = list(csv.DictReader(handle, delimiter="\t"))
    inside: dict[str, set[int]] = {session: set() for session in sessions}
    eligible = caught = unresolved = mid_turn = 0
    by_cue = {"person": 0, "reply": 0, "asked": 0, "opening": 0}
    for span in spans:
        session = span["session"]
        uuids = turn_of.get(session)
        if uuids is None:
            unresolved += 1
            continue
        first = uuids.get(span["first_uuid"])
        last = uuids.get(span["last_uuid"])
        before = uuids.get(span["opening_uuid"]) if span.get("opening_uuid") else None
        if first is None:
            unresolved += 1
            continue
        start = before if before is not None else first
        inside[session].update(range(start, (last if last is not None else first) + 1))
        if span["first_uuid"] not in typed_by[session]:
            mid_turn += 1
            continue
        eligible += 1
        cues = fired[session][first] or []
        at_opening = before is not None and bool(fired[session][before])
        by_cue["person"] += CUE_PERSON in cues
        by_cue["reply"] += CUE_REPLY in cues
        by_cue["asked"] += CUE_ASKED in cues
        by_cue["opening"] += at_opening
        caught += bool(cues) or at_opening

    turns = sum(found is not None for cues in fired.values() for found in cues)
    fires = fires_inside = 0
    outside_per_session: list[int] = []
    for session, cues in fired.items():
        outside = 0
        for number, found in enumerate(cues):
            if not found:
                continue
            fires += 1
            if number in inside[session]:
                fires_inside += 1
            else:
                outside += 1
        if any(found is not None for found in cues):
            outside_per_session.append(outside)
    return {
        "transcripts": len(sessions),
        "agent_turns": turns,
        "agent_turns_opened_by_the_person": typed,
        "spans": len(spans),
        "spans_unresolved": unresolved,
        "spans_not_starting_at_a_typed_prompt": mid_turn,
        "spans_eligible": eligible,
        "spans_caught": caught,
        "recall": round(caught / eligible, 4) if eligible else None,
        "caught_by": by_cue,
        "fires": fires,
        "fires_inside_a_span": fires_inside,
        "fires_outside_every_span": fires - fires_inside,
        "sessions_with_agent_turns": len(outside_per_session),
        "outside_per_session_median": (
            statistics.median(outside_per_session) if outside_per_session else None
        ),
        "outside_per_session_max": max(outside_per_session, default=None),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directories", nargs="+", type=Path, metavar="DIR")
    parser.add_argument("--spans", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = measure(args.directories, args.spans)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for key, value in result.items():
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
