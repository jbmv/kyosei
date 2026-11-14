import AppKit
import CoreGraphics
import Foundation
import Testing
import WebKit

@testable import KyoseiMac

// KYO-918: on a box's words the right-click menu shows a misspelled word's suggestions with
// Ignore and Learn, Indent and Outdent, and Cut, Copy and Paste. The rows the system adds
// (AutoFill, the Continuity items and Services on macOS 27) are not in the menu's `items`, so
// a test over a menu built by hand cannot see them. These tests open the real menu: the web
// view is put in a window, a right mouse press is sent to an editable element holding a
// misspelled word, and when the menu's tracking has begun the titles in the menu's window
// are read and tracking is cancelled. A menu is on the screen for a quarter of a second in
// each test, so they run only in a session that has the console.

/// Reads a menu once its tracking has begun, then cancels the tracking.
@MainActor
private final class MenuReader: NSObject {
    var menu: NSMenu?
    /// The titles drawn in the menu's window, top to bottom.
    var shown: [String]?
    /// The titles of the menu's own items, separators left out.
    var own: [String] = []
    /// The titles of the system's items that are not hidden, separators left out.
    var system: [String] = []

    @objc func began(_ note: Notification) {
        guard let menu = note.object as? NSMenu, self.menu == nil else { return }
        self.menu = menu
        let timer = Timer(
            timeInterval: 0.25, target: self, selector: #selector(read(_:)), userInfo: nil,
            repeats: false)
        // Tracking runs the loop in its own mode, which the common modes include.
        RunLoop.main.add(timer, forMode: .common)
    }

    @objc func read(_ timer: Timer) {
        guard let menu else { return }
        var titles: [String] = []
        func collect(_ view: NSView) {
            if let field = view as? NSTextField { titles.append(field.stringValue) }
            view.subviews.forEach(collect)
        }
        for window in NSApp.windows where window.isVisible && window.className.contains("Menu") {
            window.contentView.map(collect)
        }
        own = menu.items.filter { !$0.isSeparatorItem }.map(\.title)
        system = ChatPage.systemItems(shownWith: menu)
            .filter { !$0.isSeparatorItem && !$0.isHidden }.map(\.title)
        shown = titles
        menu.cancelTrackingWithoutAnimation()
    }
}

/// Whether this process is in a login session that has the screen: a menu opens in no other.
private var hasConsole: Bool {
    guard let session = CGSessionCopyCurrentDictionary() as? [String: Any] else { return false }
    return session["kCGSSessionOnConsoleKey"] as? Bool ?? false
}

@MainActor
@Suite(.serialized, .enabled(if: hasConsole))
struct MenuTrimTests {
    /// Runs the main run loop from here for a while. A menu's tracking is a run loop nested
    /// in whichever one is running, and the stop that ends it can arrive after it has
    /// returned. Under `swift test` the outermost loop is the one the process's `main` waits
    /// in, and a stop that reaches it ends the process with status 0 and no verdict, which
    /// was measured in about half the runs of this harness's first form. Running a loop here
    /// puts one between the menu's and that one.
    private func spin(_ seconds: TimeInterval, until done: () -> Bool = { false }) {
        let end = Date().addingTimeInterval(seconds)
        while Date() < end, !done() {
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
        }
    }

    private func window(with page: ChatPage) async throws -> NSWindow {
        NSApplication.shared.setActivationPolicy(.accessory)
        let window = NSWindow(
            contentRect: NSRect(x: 200, y: 200, width: 600, height: 400),
            styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = page
        window.makeKeyAndOrderFront(nil)
        // The attributes CodeMirror's content element carries in the composer.
        page.loadHTMLString(
            """
            <!doctype html><body style="margin:0"><div id="box" contenteditable="true" \
            spellcheck="true" style="font-size:40px;padding:8px">teh cat agian</div>
            """, baseURL: nil)
        for _ in 0..<100 {
            let ready = try? await page.evaluateJavaScript(
                "document.readyState === 'complete' && !!document.getElementById('box')")
            if ready as? Bool == true { break }
            try await Task.sleep(for: .milliseconds(50))
        }
        _ = try await page.evaluateJavaScript("document.getElementById('box').focus()")
        // The checker marks the word after the element has focus.
        try await Task.sleep(for: .milliseconds(500))
        return window
    }

    /// Right-clicks the box at `x`, where 40 is on the misspelled first word and 110 is past
    /// it, and returns what the menu showed.
    private func rightClick(
        _ page: ChatPage, in window: NSWindow, at x: CGFloat, composer: Bool
    ) throws -> MenuReader {
        let reader = MenuReader()
        NotificationCenter.default.addObserver(
            reader, selector: #selector(MenuReader.began(_:)),
            name: NSMenu.didBeginTrackingNotification, object: nil)
        defer { NotificationCenter.default.removeObserver(reader) }
        // The page posts this from its `contextmenu` listener in the app.
        page.composerMenu = composer
        let point = NSPoint(x: x, y: page.bounds.height - 30)
        for type in [NSEvent.EventType.rightMouseDown, .rightMouseUp] {
            let event = try #require(
                NSEvent.mouseEvent(
                    with: type, location: point, modifierFlags: [],
                    timestamp: ProcessInfo.processInfo.systemUptime,
                    windowNumber: window.windowNumber, context: nil, eventNumber: 0,
                    clickCount: 1, pressure: 1))
            window.sendEvent(event)
        }
        spin(6) { reader.shown != nil }
        spin(0.3)
        return reader
    }

    @Test func aBoxsMenuShowsOnlyTheSuggestionsTheTwoItemsAndTheClipboard() async throws {
        let page = ChatPage(
            frame: NSRect(x: 0, y: 0, width: 600, height: 400),
            configuration: WKWebViewConfiguration())
        let window = try await window(with: page)
        defer { window.orderOut(nil) }

        let box = try rightClick(page, in: window, at: 40, composer: true)
        let shown = try #require(box.shown, "no menu opened on the right mouse press")
        let tail = ["Ignore Spelling", "Learn Spelling", "Indent", "Outdent", "Cut", "Copy", "Paste"]
        #expect(Array(shown.suffix(tail.count)) == tail)
        // Everything above them is a suggestion: the menu's own items and nothing else.
        #expect(shown == box.own)
        #expect(shown.count > tail.count)

        #expect(box.system.isEmpty)

        // A press the page does not report as on a box's words keeps WebKit's menu and the
        // system's rows. This is what shows that the reading above would have seen such a
        // row, and that the rows hidden for a box's menu are in the next menu. It is made
        // off the misspelled word, where the menu has no suggestions and is short enough to
        // be drawn whole: a menu draws only the rows that fit the screen.
        let other = try rightClick(page, in: window, at: 110, composer: false)
        let all = try #require(other.shown, "no menu opened on the second press")
        #expect(all.contains("Spelling and Grammar"))
        #expect(!other.system.isEmpty, "the system added no row, so the first check proved nothing")
        #expect(other.system.allSatisfy(all.contains), "\(other.system) not all drawn in \(all)")
    }
}
