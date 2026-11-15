"""What scenarios B and C share: projects served together, the page's project switcher, the
composer's last prompt, and what a failure leaves for a reader (KYO-948).

The check build is started with no ``KYOSEI_PROJECT`` in these scenarios, so that the project
it shows is the one it saved in its own defaults (``dev.kyosei.apple.check``), as an app a
person starts is. ``harness.forget_settings`` clears that domain before and after a scenario,
and ``harness.save_project`` writes the one key a scenario's start needs.
"""

from __future__ import annotations

import contextlib
import json
import signal
import time
from pathlib import Path
from typing import Any

import harness

import conftest

PAGE_LOADED = "return typeof window.kyo === 'object' && !!document.querySelector('.view-item');"


def seed_project(run: harness.Run, name: str, prefix: str, *, line: str) -> Path:
    """A project with a record and a file of its own, so that what the window draws can be
    told from another project's by its words."""
    project = harness.make_project(run, name, prefix)
    run.kyo("new", "task", "--title", f"{line} record", "--json", cwd=project)
    (project / f"{name}-notes.md").write_text(f"# {line}\n\nA file of {name}.\n", encoding="utf-8")
    harness.commit_all(run, project, f"The {name} project")
    return project


class Window:
    """The check build's one window, read and driven through its channel."""

    def __init__(self, app: harness.App) -> None:
        self.app = app
        self.page = app.channel

    def loaded(self) -> None:
        self.page.wait("the page to load", PAGE_LOADED)

    def make_key(self) -> None:
        """The window stands as key and the page holds the focus, so that keys reach it."""
        self.page.request("window", key=True, focus=True)

    # ------------------------------------------------------------------ reading

    def title(self) -> str:
        return self.page.request("state")["title"]

    def name(self) -> str:
        return self.page.eval("return document.getElementById('project-name').textContent;")

    def project_list(self) -> dict[str, Any]:
        """Whether the list of projects stands in place of the chat, and its rows."""
        return self.page.eval(
            "const pane = document.getElementById('projects-pane');"
            "return {shown: getComputedStyle(pane).display !== 'none',"
            " none: document.body.classList.contains('no-project'),"
            " picking: document.body.classList.contains('pick-project'),"
            " rows: [...document.querySelectorAll('#projects-list .project-row')]"
            "   .map((row) => row.dataset.project),"
            " button: document.getElementById('project-name').textContent};"
        )

    def chat(self) -> str:
        return self.page.eval("return document.getElementById('chat').textContent;")

    def prompt(self) -> str:
        """The text of the composer's prompt, which is its last box."""
        return self.page.eval("return window.kyo.composer.boxes().at(-1).text;")

    def note(self) -> str:
        return self.page.eval("return document.getElementById('composer-note').textContent;")

    def links(self) -> list[str]:
        return self.page.eval(
            "return [...document.querySelectorAll('#session-links .session-link')]"
            ".map((link) => link.textContent);"
        )

    def records(self) -> list[str]:
        """The titles of the records the Records view lists, shown first."""
        self.view("records")
        return self.page.wait(
            "the records to be listed",
            "const rows = [...document.querySelectorAll('#records-table [data-open]')];"
            "return rows.length ? rows.map((row) => row.textContent) : null;",
        )

    def files(self) -> list[str]:
        """The files the Files view lists at the project's root."""
        self.view("files")
        return self.page.wait(
            "the files to be listed",
            "const rows = [...document.querySelectorAll('#view-files .files-columns .tree-row')]"
            ".filter((row) => row.dataset.file);"
            "return rows.length ? rows.map((row) => row.dataset.file) : null;",
        )

    def view(self, name: str) -> None:
        self.page.eval(
            "document.querySelector(`.view-item[data-view-item=\"${view}\"]`).click();", view=name
        )
        self.page.wait(
            f"the {name} view",
            "return document.body.classList.contains('view-' + view);",
            view=name,
        )

    # ------------------------------------------------------------------ driving

    def choose(self, name: str) -> None:
        """Choose a project from the top bar's dropdown, and wait until the window is its."""
        self.page.eval("document.getElementById('project-button').click();")
        self.page.wait(
            "the dropdown to open",
            "return !document.getElementById('project-popup').hidden;",
        )
        # The list is read again as the dropdown opens, and its rows are drawn when it answers.
        self.page.wait(
            f"{name}'s row in the dropdown",
            "return !!document.querySelector("
            "`#project-popup .project-row[data-project=\"${name}\"]`);",
            name=name,
        )
        self.page.eval(
            "document.querySelector(`#project-popup .project-row[data-project=\"${name}\"]`)"
            ".click();",
            name=name,
        )
        self.turned(name)

    def pick(self, name: str) -> None:
        """Choose a project from the list shown in place of the chat."""
        self.page.eval(
            "document.querySelector(`#projects-list .project-row[data-project=\"${name}\"]`)"
            ".click();",
            name=name,
        )
        self.turned(name)

    def follow_link(self, name: str) -> None:
        """Press a project's live-session link in the top bar."""
        self.page.eval(
            "[...document.querySelectorAll('#session-links .session-link')]"
            ".find((link) => link.textContent === name).click();",
            name=name,
        )
        self.turned(name)

    def turned(self, name: str) -> None:
        """Wait until the page's dropdown and the window's title both say ``name``."""
        self.page.wait(
            f"the dropdown to name {name}",
            "return document.getElementById('project-name').textContent === name"
            " && !document.body.classList.contains('no-project');",
            name=name,
        )
        harness.poll(f"the window's title to be {name}", lambda: self.title() == name)

    def type_prompt(self, text: str) -> None:
        """Type ``text`` into the prompt with key events, and wait for it to be in the box."""
        self.page.eval(
            "const boxes = window.kyo.composer.boxes();"
            "window.kyo.composer.select(boxes.length - 1, boxes.at(-1).text.length);"
        )
        self.page.request("key", text=text)
        self.page.wait(
            f"{text!r} in the prompt",
            "return window.kyo.composer.boxes().at(-1).text === text;",
            text=text,
        )

    def wait_prompt(self, text: str, what: str, deadline: float = 15.0) -> None:
        self.page.wait(
            f"{text!r} in the prompt {what}",
            "return window.kyo.composer.boxes().at(-1).text === text;",
            text=text,
            deadline=deadline,
        )

    def status_strip(self, directory: Path, name: str) -> dict[str, Any]:
        """The right-hand part of the native status bar in a snapshot, where the state of the
        feed is written ("No session"), as a digest of its pixels and the number of colours.
        The status bar is SwiftUI and nothing in the page reads it, so the strip is compared
        with another project's and not read."""
        import hashlib

        path = directory / f"status-{name}.png"

        def read() -> dict[str, Any]:
            shot = self.page.request("snapshot", path=str(path))
            picture = harness.read_png(path)
            top = int(shot["page"]["y"] + shot["page"]["height"]) + 2
            left, bottom = picture.width * 3 // 4, picture.height - 2
            right = picture.colours(left, top, picture.width - 2, bottom)
            digest = hashlib.sha256(
                b"".join(
                    row[left * picture.channels : (picture.width - 2) * picture.channels]
                    for row in picture.rows[top:bottom]
                )
            ).hexdigest()[:12]
            return {"colours": len(right), "digest": digest}

        # The label changes as the feed connects, so the strip is read until two snapshots
        # in a row agree.
        previous: list[dict[str, Any]] = []

        def steady() -> dict[str, Any] | None:
            now = read()
            if previous and previous[-1]["digest"] == now["digest"]:
                return now
            previous.append(now)
            return None

        return harness.poll(f"the status bar of {name} to be steady", steady, deadline=20)


def start_live_session(server: harness.Server, run: harness.Run) -> str:
    """Start the scripted session of ``server``'s project through the server's Start route,
    and return its id."""
    started = server.start_session()
    return started["row"]["session_id"]


def end_session(run: harness.Run, server: harness.Server, session: str) -> None:
    """End the scripted session as a person ends Claude Code: ``/exit`` typed into its pane
    of the run's private tmux server. Waits until the server no longer lists it as live."""
    pane = run.tmux("list-panes", "-a", "-F", "#{pane_id}").stdout.split()
    assert len(pane) == 1, f"the private tmux server holds {len(pane)} panes"
    run.tmux("send-keys", "-t", pane[0], "-l", "/exit")
    run.tmux("send-keys", "-t", pane[0], "Enter")
    harness.poll(
        "the server to stop listing the session as live",
        lambda: all(
            row["session_id"] != session or not row.get("live")
            for row in server.get("sessions")["sessions"]
        ),
        deadline=30,
    )


def stop_server(server: harness.Server) -> None:
    """Suspend the test server: SIGSTOP to the pid the harness recorded at its start, and to
    nothing else."""
    assert server.process.poll() is None, "the test server is not running"
    server.process.send_signal(signal.SIGSTOP)


def resume_server(server: harness.Server) -> None:
    """Resume the test server. Safe to call when it is not stopped, and when it has ended."""
    if server.process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            server.process.send_signal(signal.SIGCONT)


def keep_failure(
    name: str, window: Window | None, run: harness.Run, server: harness.Server | None
) -> None:
    """What a failure leaves for a reader: the window, the page, the logs and the pane."""
    if window is not None:
        try:
            (conftest.OUT / f"{name}-state-at-failure.json").write_text(
                json.dumps(
                    {
                        "channel_state": window.page.request("state"),
                        "project_list": window.project_list(),
                        "name": window.name(),
                        "prompt": window.prompt(),
                        "note": window.note(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            window.app.channel.request(
                "snapshot", path=str(conftest.OUT / f"{name}-at-failure.png")
            )
            (conftest.OUT / f"{name}-page-at-failure.html").write_text(
                window.page.eval("return document.documentElement.outerHTML;"), encoding="utf-8"
            )
        except (harness.CheckError, OSError):
            pass
    if server is not None and server.log.exists():
        (conftest.OUT / f"{name}-serve.log").write_text(server.log.read_text("utf-8"), "utf-8")
    if run.session_log.exists():
        (conftest.OUT / f"{name}-session.jsonl").write_text(
            run.session_log.read_text("utf-8"), "utf-8"
        )


def timed(fn: Any) -> float:
    began = time.monotonic()
    fn()
    return round(time.monotonic() - began, 2)
