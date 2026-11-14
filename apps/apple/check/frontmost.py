"""Which app is in front through a run of the check harness (KYO-945).

A run happens on a machine a person may be using, and it must not take the place of the app
they have in front. This module samples that place through a run and judges the samples.

``lsappinfo`` answers from a process started in tmux without any permission. Two of its
answers are sampled:

- ``front``: the app in front. While the screen is locked this is ``loginwindow`` throughout,
  whatever the harness does.
- ``visibleProcessList``: the visible apps from front to back, ``loginwindow`` left out. Its
  first entry is sampled as the ``head`` of the order.

A sample is judged against the run's own processes: the check build, by its bundle
identifier, and anything whose executable is in a build directory of this checkout or is the
test runner of ``swift test``. The run fails if one of them is in front at any sample, or at
the head of the visible order. A change between two other apps is the person's own, and is
recorded without failing the run.

WHAT A LOCKED SCREEN HIDES. Measured on macOS 27.0 with the screen locked: a check build that
called ``NSApp.activate()`` did not become active, ``front`` stayed ``loginwindow`` and the
visible order did not move. A run under a locked screen therefore passes whatever it would
have done to an unlocked one, and its pass is not evidence. Each sample records whether the
screen was locked, and the judgement says so, so that such a pass is not read as one.

Used three ways::

    python frontmost.py watch <log>          # sample until ended with SIGTERM
    python frontmost.py mark <log> <step>    # say which step of the run begins now
    python frontmost.py judge <log>          # print the judgement; status 1 if the front was taken
"""

from __future__ import annotations

import json
import plistlib
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

LSAPPINFO = "/usr/bin/lsappinfo"
IOREG = "/usr/sbin/ioreg"
CHECK_IDENTIFIER = "dev.kyosei.apple.check"
#: Seconds between two samples.
INTERVAL = 0.1
#: The test runners ``swift test`` starts, which make windows of their own.
TEST_RUNNERS = ("xctest", "swiftpm-testing-helper", "swiftpm-xctest-helper")


def _run(*arguments: str) -> str:
    try:
        return subprocess.run(  # noqa: S603
            arguments, capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def screen_is_locked() -> bool | None:
    """Whether the console session's screen is locked, or None where that cannot be read.

    Read from the I/O registry's list of console users, which needs no permission.
    """
    listed = _run(IOREG, "-n", "Root", "-d1", "-a")
    try:
        users = plistlib.loads(listed.encode("utf-8")).get("IOConsoleUsers", [])
    except Exception:  # noqa: BLE001 - any unreadable answer is "not known"
        return None
    if not users:
        return None
    return any(bool(user.get("CGSSessionScreenIsLocked")) for user in users)


def describe(asn: str) -> dict[str, Any]:
    """The name, bundle identifier, pid and executable of the app with this ASN."""
    said = _run(LSAPPINFO, "info", "-only", "bundleid,pid,name", asn)
    name = re.search(r'^"([^"]*)"', said, re.MULTILINE)
    bundle = re.search(r'bundleID="([^"]*)"', said)
    executable = re.search(r'executable path="([^"]*)"', said)
    pid = re.search(r"pid = (\d+)", said)
    return {
        "asn": asn,
        "name": name.group(1) if name else "",
        "bundle": bundle.group(1) if bundle else "",
        "pid": int(pid.group(1)) if pid else None,
        "executable": executable.group(1) if executable else "",
    }


def sample(known: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One sample: the app in front, and the head of the visible order."""

    def app(asn: str) -> dict[str, Any]:
        if asn not in known:
            known[asn] = describe(asn)
        return known[asn]

    front = _run(LSAPPINFO, "front").strip().rstrip(":")
    order = re.findall(r"(ASN:0x[0-9a-f]+-0x[0-9a-f]+)", _run(LSAPPINFO, "visibleProcessList"))
    return {
        "front": app(front) if front.startswith("ASN:") else None,
        "head": app(order[0]) if order else None,
        "order": [app(asn)["name"] for asn in order],
    }


def watch(log: Path) -> None:
    """Sample until SIGTERM or SIGINT, writing a line whenever a sample differs from the last."""
    going = True

    def stop(*_: object) -> None:
        nonlocal going
        going = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    known: dict[str, dict[str, Any]] = {}
    last: str | None = None
    count = 0
    locked: bool | None = None
    with log.open("a", encoding="utf-8") as stream:
        while going:
            if count % 10 == 0:
                locked = screen_is_locked()
            taken = {**sample(known), "locked": locked}
            count += 1
            text = json.dumps(taken, sort_keys=True)
            # A line for each change, and one now and then to say the sampling goes on.
            if text != last or count % 50 == 0:
                stream.write(json.dumps({"t": time.time(), "n": count, "sample": taken}) + "\n")
                stream.flush()
                last = text
            time.sleep(INTERVAL)


def mark(log: Path, step: str) -> None:
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"t": time.time(), "step": step}) + "\n")


def is_the_runs_own(app: dict[str, Any] | None, build_directories: tuple[str, ...]) -> bool:
    """Whether an app is a process of the run: the check build or a test runner."""
    if not app:
        return False
    executable = str(app.get("executable") or "")
    name = str(app.get("name") or "")
    return (
        app.get("bundle") == CHECK_IDENTIFIER
        or any(executable.startswith(directory) for directory in build_directories)
        or Path(executable).name in TEST_RUNNERS
        or name in TEST_RUNNERS
        or name in ("KyoseiCheck", "KyoseiMac")
    )


def judge(log: Path, *, build_directories: tuple[str, ...] = ()) -> dict[str, Any]:
    """Read a log and say what the run did to the front.

    ``taken`` lists each sample at which a process of the run was in front or at the head of
    the visible order, with the step in force; ``passed`` is whether there was none and there
    was at least one sample. ``other_changes`` lists the changes among other apps.
    """
    step = "before the first step"
    taken: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []
    steps: list[str] = []
    samples = 0
    written = 0
    locked: set[bool | None] = set()
    before: dict[str, str | None] = {}
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if "step" in row:
                step = row["step"]
                steps.append(step)
                continue
            taken_now = row.get("sample") or {}
            written += 1
            samples = max(samples, int(row.get("n") or 0))
            locked.add(taken_now.get("locked"))
            for place in ("front", "head"):
                app = taken_now.get(place)
                label = (app or {}).get("bundle") or (app or {}).get("name") or None
                if place in before and before[place] != label:
                    change = {
                        "step": step,
                        "place": place,
                        "from": before[place],
                        "to": label,
                        "t": row.get("t"),
                    }
                    if is_the_runs_own(app, build_directories):
                        taken.append(change)
                    else:
                        others.append(change)
                elif place not in before and is_the_runs_own(app, build_directories):
                    taken.append(
                        {"step": step, "place": place, "from": None, "to": label, "t": row.get("t")}
                    )
                before[place] = label
    return {
        "passed": written > 0 and not taken,
        "samples": samples,
        "lines": written,
        "interval_seconds": INTERVAL,
        "steps": steps,
        "screen_locked": sorted(locked, key=str),
        "taken": taken,
        "other_changes": others,
        "ends_with": before,
    }


def sentence(judged: dict[str, Any]) -> str:
    if not judged["lines"]:
        return "the front was not sampled"
    locked = judged["screen_locked"]
    lock = (
        "the screen was locked throughout, which hides what a run does to the front, so this "
        "is not evidence that an unlocked screen would have been left alone"
        if locked == [True]
        else "the screen was unlocked throughout"
        if locked == [False]
        else f"the screen's lock was read as {locked}"
    )
    if judged["taken"]:
        where = "; ".join(
            f"{change['to']} came to the {change['place']} during {change['step']}"
            for change in judged["taken"]
        )
        return f"the run took the front: {where} ({lock})"
    return (
        f"no process of the run was in front in {judged['samples']} samples "
        f"over {len(judged['steps'])} steps; {lock}"
    )


def main(arguments: list[str]) -> int:
    if len(arguments) >= 2 and arguments[0] == "watch":
        watch(Path(arguments[1]))
        return 0
    if len(arguments) >= 3 and arguments[0] == "mark":
        mark(Path(arguments[1]), " ".join(arguments[2:]))
        return 0
    if len(arguments) >= 2 and arguments[0] == "judge":
        here = Path(__file__).resolve().parent.parent
        judged = judge(Path(arguments[1]), build_directories=(str(here / ".build"),))
        print(f"frontmost: {sentence(judged)}")
        return 0 if judged["passed"] else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
