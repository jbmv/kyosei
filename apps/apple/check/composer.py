"""The page's composer as the composer scenarios read and drive it (KYO-947).

``Composer`` wraps the check channel and the test server for scenarios A and G: it reads the
composer's boxes through ``window.kyo.composer``, reads the marks from the draft on the test
server, which is what a second device reads, and makes the moves a person makes. Keys go
through the channel's ``key`` request, Command-X and Command-V among them: the check build's
general pasteboard is one of its own (``CheckPasteboard``), which the ``pasteboard`` request
reads. The Edit menu's items are chosen through the ``menu`` request. Both need the window to
stand as the key window, which a scenario asks for once with ``window``. The events a web
view takes from the window server (a context menu on the chat, a gutter's mark panel) and a
paste of a file are dispatched in the page, as the page's own tests dispatch them.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import harness

#: A PNG of one pixel, sent as the image of a line.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _vacant(kind: str, text: str, marks: int) -> bool:
    """The draft keeps no box with neither words nor marks: the prompt every composer has, and
    a scratchpad that has just been emptied or added."""
    return text == "" and marks == 0


def _describe(item: dict[str, Any]) -> str:
    """A mark's item as the kind and what names it: an anchor by the reply's message and the
    range it covers, a tag by its name, a file by its id."""
    if item["kind"] == "anchor":
        return f"anchor:{item['key']}:{item['start']}-{item['end']}"
    if item["kind"] == "tag":
        return f"tag:{item['name']}"
    if item["kind"] == "file":
        return f"file:{item['id']}"
    return item["kind"]


class Composer:
    """The page's composer, read and driven through the check channel."""

    def __init__(self, page: harness.Channel, server: harness.Server) -> None:
        self.page = page
        self.server = server
        self.session = ""
        #: Each check that the draft on the server equalled the composer: after what, and how
        #: long the page took to save it.
        self.syncs: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ reading

    def boxes(self) -> list[dict[str, Any]]:
        return self.page.eval(
            "return window.kyo.composer.boxes().map((box) => ({kind: box.kind, id: box.id,"
            " text: box.text, from: box.from, to: box.to, focused: box.focused}));"
        )

    def shown_marks(self) -> list[int]:
        """For each box, how many marks its gutter draws as held."""
        return self.page.eval(
            "return [...document.querySelectorAll('#boxes .box')]"
            ".map((box) => box.querySelectorAll('.mark.held').length);"
        )

    def labels(self) -> list[str]:
        return self.page.eval(
            "return [...document.querySelectorAll('#boxes .box-create')].map((b) => b.textContent);"
        )

    def server_boxes(self) -> list[dict[str, Any]]:
        return self.server.get(f"sessions/{self.session}/draft").get("boxes", [])

    def held(self) -> list[list[Any]]:
        """The server's draft as ``[kind, text, [[line, [item kinds]], ...]]`` for each box."""
        return [
            [
                "scratch" if box.get("scratch") is not None else "prompt",
                box["text"],
                [
                    [mark["line"], [_describe(item) for item in mark["items"]]]
                    for mark in box.get("marks", [])
                ],
            ]
            for box in self.server_boxes()
        ]

    def composed(self) -> list[list[Any]]:
        """The composer's boxes as ``[kind, text]``."""
        return [[box["kind"], box["text"]] for box in self.boxes()]

    def second_device(self) -> dict[str, Any]:
        """Whether the draft on the server equals the composer's boxes: the same boxes by id,
        kind and text, and as many marks on each as the box draws."""
        boxes = self.boxes()
        shown = self.shown_marks()
        stored = self.server_boxes()
        ours = [
            [box["id"], box["kind"], box["text"], count]
            for box, count in zip(boxes, shown, strict=True)
            if not _vacant(box["kind"], box["text"], count)
        ]
        theirs = [
            [
                box["id"],
                "scratch" if box.get("scratch") is not None else "prompt",
                box["text"],
                len(box.get("marks", [])),
            ]
            for box in stored
            if not _vacant(
                "scratch" if box.get("scratch") is not None else "prompt",
                box["text"],
                len(box.get("marks", [])),
            )
        ]
        return {"equal": ours == theirs, "composer": ours, "server": theirs}

    def settled(self, what: str, deadline: float = 12.0) -> dict[str, Any]:
        """Wait for the draft on the server to equal the composer's boxes; the page saves a
        moment after an edit, so this is a condition asked of the server and not a sleep."""
        began = time.monotonic()
        end = began + deadline
        last: dict[str, Any] = {}
        while time.monotonic() < end:
            last = self.second_device()
            if last["equal"]:
                self.syncs.append({"after": what, "seconds": round(time.monotonic() - began, 2)})
                return last
            time.sleep(0.15)
        raise harness.CheckError(
            f"the server's draft never equalled the composer after {what}: {last}"
        )

    # ------------------------------------------------------------------ driving

    def type(self, text: str, expect: str, index: int = -1) -> None:
        """Keys are taken by the page one after another after the request returns, so the
        text is waited for in the box before anything else is done. The box is named by its
        place and not by its focus: an app that was not brought to the front has a page that
        reports no focus (KYO-946), while the keys still reach the box the caret is in."""
        self.page.request("key", text=text)
        self.page.wait(
            f"{expect!r} in box {index}",
            "return window.kyo.composer.boxes().at(index).text === text;",
            text=expect,
            index=index,
        )

    def select(self, index: int, at: int, to: int | None = None) -> None:
        self.page.eval(
            "window.kyo.composer.select(index, at, to);",
            index=index,
            at=at,
            to=at if to is None else to,
        )

    def new_scratchpad(self) -> None:
        before = len(self.boxes())
        self.page.eval("document.getElementById('new-scratch').click();")
        self.page.wait(
            "a new scratchpad with the caret in it",
            "const boxes = window.kyo.composer.boxes();"
            "return boxes.length === count + 1 && boxes[0].kind === 'scratch';",
            count=before,
        )

    def anchor(self, index: int, at: int) -> None:
        """An anchor on the line with the caret, through the page's context menu on the first
        paragraph of the reply."""
        self.select(index, at)
        self.page.eval(
            "const target = document.querySelector('#chat .reply p');"
            "const at = target.getBoundingClientRect();"
            "target.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true,"
            " clientX: at.left + 30, clientY: at.top + at.height / 2}));"
            "document.querySelector('#anchor-menu .anchor-new').click();"
        )

    def tag(self, index: int, line: int, name: str) -> None:
        """A tag on a line, through the mark panel its gutter opens, the name typed with keys."""
        self.page.eval(
            "const box = document.querySelectorAll('#boxes .box')[index];"
            "const gutter = box.querySelector('.cm-gutter.kyo-marks');"
            "const row = box.querySelectorAll('.cm-line')[line].getBoundingClientRect();"
            "gutter.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true,"
            " clientX: gutter.getBoundingClientRect().left + 5, clientY: row.top + 4}));"
            "document.querySelector('.mark-panel input').focus();",
            index=index,
            line=line,
        )
        self.page.wait(
            "the tag field to hold the focus",
            "const field = document.querySelector('.mark-panel input');"
            "return !!field && document.activeElement === field;",
        )
        self.page.request("key", text=name)
        self.page.wait(
            "the tag typed into the panel",
            "return document.querySelector('.mark-panel input')?.value === name;",
            name=name,
        )
        self.page.request("key", key="return")
        self.page.wait(
            "the tag on its line",
            "return [...document.querySelectorAll('.mark-panel')].some((panel) =>"
            " panel.textContent.includes('#' + name));",
            name=name,
        )
        self.page.request("key", key="escape")

    def image(self, index: int, at: int, name: str) -> None:
        """An image on the line with the caret, as a paste of a file, which the page sends
        through the app's attach route to the test server."""
        self.select(index, at)
        self.page.eval(
            "const bytes = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));"
            "const data_transfer = new DataTransfer();"
            "data_transfer.items.add(new File([bytes], name, {type: 'image/png'}));"
            "document.activeElement.dispatchEvent(new ClipboardEvent('paste',"
            " {clipboardData: data_transfer, bubbles: true, cancelable: true}));",
            data=base64.b64encode(PNG).decode(),
            name=name,
        )

    def press(self, selector: str) -> None:
        self.page.eval("document.querySelector(selector).click();", selector=selector)

    def edit_menu(self) -> dict[str, dict[str, Any]]:
        menus = {menu["title"]: menu.get("items", []) for menu in self.page.request("menus")}
        return {item["title"]: item for item in menus["Edit"] if item.get("title")}

    def choose(self, *path: str) -> dict[str, Any]:
        """Choose an item of the menu bar by its titles. The app validates the item against
        the responder its action goes to and, if that enables it, sends the action; the
        answer says which of those happened, the action's name and the responder's class."""
        return self.page.request("menu", path=list(path))

    def count_undo_calls(self) -> None:
        """Count the calls of the page's ``window.kyo.undo`` and ``window.kyo.redo`` from now
        on. The app's ``undo:`` and ``redo:`` call them (KYO-915) and the runner never does,
        so a count that rises by one at a menu request is the item's action having run."""
        self.page.eval(
            "if (!window.checkUndoCalls) {"
            "  window.checkUndoCalls = {undo: 0, redo: 0};"
            "  for (const name of ['undo', 'redo']) {"
            "    const own = window.kyo[name];"
            "    window.kyo[name] = (...given) => {"
            "      window.checkUndoCalls[name] += 1; return own(...given); };"
            "  }"
            "}"
        )

    def undo_calls(self) -> dict[str, int]:
        return self.page.eval("return window.checkUndoCalls;")

    def pasteboard(self) -> dict[str, Any]:
        """The check build's own pasteboard: its name, change count and text, whether it is
        what the app gets when it asks for the general pasteboard, and the change count of
        the general pasteboard itself, the one thing read of it."""
        return self.page.request("pasteboard")

    def cut(self, index: int, start: int, end: int) -> None:
        """Command-X on a selection, through the ``key`` request and the Edit menu's Cut."""
        self.select(index, start, end)
        self.page.request("key", key="x", modifiers=["command"])

    def paste(self, index: int, at: int) -> None:
        """Command-V at a place, through the ``key`` request and the Edit menu's Paste."""
        self.select(index, at)
        self.page.request("key", key="v", modifiers=["command"])
