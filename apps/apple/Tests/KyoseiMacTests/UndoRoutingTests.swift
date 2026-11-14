import AppKit
import CoreGraphics
import Foundation
import Testing
import WebKit

@testable import KyoseiMac

// KYO-915: Edit > Undo and Edit > Redo act on a composer box's own history while the box has
// the focus, and on the page's other text fields as before. These tests put the built renderer
// in a `ChatPage` in a window and use a menu of two items that carry the actions of the Edit
// menu's own, `undo:` and `redo:`, which a SwiftUI app's Edit menu was read to hold with no
// target. AppKit offers such an action to the key window's first responder and then to each
// next responder. A test process is not the active app, and its window was measured never to
// become the key window, with `NSApp.activate` too, so `NSApp.target(forAction:)` finds
// nothing here. The tests therefore walk the window's responder chain themselves, as `aim`
// says, and give each item the responder found as its target. The rest is AppKit's: `update()`
// validates the items against that target as opening the menu would, and
// `performActionForItem(at:)` validates an item and sends its action as choosing it would.
// The menu is never opened, and the app's own Edit menu is not seen here. A window is on the
// screen while a test runs, so they run only in a session that has the console.

/// Whether this process is in a login session that has the screen.
private var hasConsole: Bool {
    guard let session = CGSessionCopyCurrentDictionary() as? [String: Any] else { return false }
    return session["kCGSSessionOnConsoleKey"] as? Bool ?? false
}

@MainActor
@Suite(.serialized, .enabled(if: hasConsole))
struct UndoRoutingTests {
    private let undo = #selector(ChatPage.undo(_:))
    private let redo = #selector(ChatPage.redo(_:))

    /// A window holding the renderer, with a chat of one reply and one box of the words
    /// "abc" that has the caret.
    private func open() async throws -> (NSWindow, ChatPage) {
        NSApplication.shared.setActivationPolicy(.accessory)
        let page = ChatPage(
            frame: NSRect(x: 0, y: 0, width: 900, height: 700),
            configuration: WKWebViewConfiguration())
        let window = NSWindow(
            contentRect: NSRect(x: 200, y: 200, width: 900, height: 700),
            styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = page
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(page)
        let directory = Bundle.module.resourceURL!.appending(path: "renderer")
        page.loadFileURL(directory.appending(path: "chat.html"), allowingReadAccessTo: directory)
        try await wait("the renderer to load") {
            try await page.evaluateJavaScript(
                "document.readyState === 'complete' && typeof window.kyo === 'object'") as? Bool == true
        }
        _ = try await page.evaluateJavaScript(
            """
            window.kyo.project({ project: "p", scope: "server/p" });
            window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
              { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
            ] } });
            window.kyo.apply({ reset: true,
              turns: [{ id: "u1", at: "2026-01-01T00:00:00Z", text: "A prompt of several words." }],
              replies: [{ message_id: "m1", uuid: "x1", at: "2026-01-01T00:00:01Z", text: "First paragraph of the reply here." }] });
            window.kyo.draft({ boxes: [{ id: "a", text: "abc" }], etag: "e" });
            window.kyo.composer.select(0, 3);
            true
            """)
        return (window, page)
    }

    private func wait(_ what: String, until done: () async throws -> Bool) async throws {
        for _ in 0..<100 {
            if try await done() { return }
            try await Task.sleep(for: .milliseconds(50))
        }
        Issue.record("waited five seconds for \(what)")
    }

    /// The two items, titled as the window titles them after typing in a text field.
    private func menu() -> NSMenu {
        let menu = NSMenu(title: "Edit")
        menu.addItem(NSMenuItem(title: "Undo Typing", action: undo, keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Redo Typing", action: redo, keyEquivalent: ""))
        return menu
    }

    /// The responder of the window that takes an action sent with no target: the first
    /// responder, or the nearest of its next responders, that responds to it.
    private func taker(of action: Selector, in window: NSWindow) -> NSResponder? {
        var responder = window.firstResponder
        while let candidate = responder {
            if candidate.responds(to: action) { return candidate }
            responder = candidate.nextResponder
        }
        return nil
    }

    /// Gives each item the responder that would take its action were the window the key one.
    private func aim(_ menu: NSMenu, at window: NSWindow) {
        for item in menu.items { item.target = item.action.flatMap { taker(of: $0, in: window) } }
    }

    /// Whether each item is enabled once validated, as it is when the menu opens.
    private func enabled(_ menu: NSMenu, in window: NSWindow) -> [Bool] {
        aim(menu, at: window)
        menu.update()
        return menu.items.map(\.isEnabled)
    }

    /// Chooses an item: AppKit validates it and sends its action to its target.
    private func choose(_ index: Int, of menu: NSMenu, in window: NSWindow) {
        aim(menu, at: window)
        menu.performActionForItem(at: index)
    }

    /// The box's words and the anchors drawn in its gutter.
    private func box(_ page: ChatPage) async throws -> String {
        try await page.evaluateJavaScript(
            """
            JSON.stringify([window.kyo.composer.boxes().map((box) => box.text),
              [...document.querySelectorAll("#boxes .box .mark.held")].flatMap((mark) => mark.title.split("\\n"))])
            """) as? String ?? ""
    }

    private let plain = #"[["abc"],[]]"#
    private let anchored = #"[["abc"],["anchor: First paragraph of the reply here."]]"#

    @Test func undoAndRedoInABoxRunTheBoxsOwnHistory() async throws {
        let (window, page) = try await open()
        defer { window.orderOut(nil) }
        let menu = menu()

        // A box with the caret and nothing to undo: the view takes both actions and enables neither.
        try await wait("the page to say a box has the focus") { page.undoState.box }
        #expect(page.undoState == ChatPage.UndoState(message: ["box": true, "undo": false, "redo": false]))
        #expect(window.firstResponder === page)
        #expect(taker(of: undo, in: window) === page)
        #expect(taker(of: redo, in: window) === page)
        #expect(enabled(menu, in: window) == [false, false])
        #expect(menu.items.map(\.title) == ["Undo", "Redo"])

        // An anchor placed from the chat's menu is a step of the box's history and no edit of
        // its text, so WebKit's undo manager holds nothing for it.
        _ = try await page.evaluateJavaScript(
            """
            (() => {
              const target = document.querySelector("#chat .reply p");
              const at = target.getBoundingClientRect();
              target.dispatchEvent(new MouseEvent("contextmenu", {
                bubbles: true, cancelable: true, clientX: at.left + 30, clientY: at.top + at.height / 2,
              }));
              document.querySelector("#anchor-menu .anchor-new").click();
              return true;
            })()
            """)
        #expect(try await box(page) == anchored)
        #expect(page.undoManager?.canUndo != true)
        try await wait("the page to say there is a step to undo") { page.undoState.undo }
        #expect(enabled(menu, in: window) == [true, false])

        // Edit > Undo removes the anchor and leaves the words.
        choose(0, of: menu, in: window)
        try await wait("the anchor to go") { try await box(page) == plain }
        #expect(try await box(page) == plain)
        try await wait("the page to say there is a step to redo") { page.undoState.redo }
        #expect(enabled(menu, in: window) == [false, true])

        // Edit > Redo puts it back.
        choose(1, of: menu, in: window)
        try await wait("the anchor to come back") { try await box(page) == anchored }
        #expect(try await box(page) == anchored)
        try await wait("the page to say there is a step to undo again") { page.undoState.undo }
        #expect(enabled(menu, in: window) == [true, false])
    }

    @Test func aTextFieldOutsideTheBoxesKeepsWebKitsUndo() async throws {
        let (window, page) = try await open()
        defer { window.orderOut(nil) }
        let menu = menu()
        try await wait("the page to say a box has the focus") { page.undoState.box }

        // A text field of the page that is not a box, with words typed in it.
        _ = try await page.evaluateJavaScript(
            """
            (() => {
              const field = document.createElement("input");
              field.id = "probe";
              document.body.append(field);
              field.focus();
              document.execCommand("insertText", false, "typed");
              return true;
            })()
            """)
        try await wait("the page to say no box has the focus") { !page.undoState.box }
        #expect(!page.responds(to: undo))
        #expect(!page.responds(to: redo))
        // The action passes the view to the window, as it did before the view took any.
        #expect(taker(of: undo, in: window) === window)
        #expect(taker(of: redo, in: window) === window)
        // The window validates and performs the item from the web view's undo manager.
        #expect(page.undoManager?.canUndo == true)
        #expect(enabled(menu, in: window) == [true, false])
        choose(0, of: menu, in: window)
        try await wait("the typed words to go") {
            try await page.evaluateJavaScript("document.getElementById('probe').value") as? String == ""
        }
        #expect(try await box(page) == plain)
        #expect(enabled(menu, in: window) == [false, true])
    }

    @Test func aMessageThatIsNotTheStateReadsAsNoBox() {
        #expect(ChatPage.UndoState(message: "nothing") == ChatPage.UndoState())
        // Nothing is undone or redone in a box that does not have the focus.
        let state = ChatPage.UndoState(message: ["box": false, "undo": true, "redo": true])
        #expect(state == ChatPage.UndoState())
    }
}
