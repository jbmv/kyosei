#if KYOSEI_CHECK
import AppKit
import CoreGraphics
import Darwin
import Foundation
import Testing
import WebKit

@testable import KyoseiMac

// KYO-944: the check channel's requests, each asked over the socket as the runner asks it.
// The tests exist only in a build made with the `KYOSEI_CHECK` condition
// (`swift test -Xswiftc -DKYOSEI_CHECK`), since the channel exists in no other. A window
// holding a small page is on the screen while a test runs, so they run only in a session
// that has the console.

/// Whether this process is in a login session that has the screen.
private var hasConsole: Bool {
    guard let session = CGSessionCopyCurrentDictionary() as? [String: Any] else { return false }
    return session["kCGSSessionOnConsoleKey"] as? Bool ?? false
}

/// The runner's end of the socket. Its calls block, so they are made off the main thread,
/// which the channel answers on.
private final class Client: @unchecked Sendable {
    private let descriptor: Int32
    private var buffer = Data()

    init(path: String) throws {
        descriptor = socket(AF_UNIX, SOCK_STREAM, 0)
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        withUnsafeMutableBytes(of: &address.sun_path) { $0.copyBytes(from: Array(path.utf8)) }
        let connected = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connected == 0 else { throw CheckFailure("the test could not connect to \(path)") }
    }

    deinit { close(descriptor) }

    private func exchange(_ line: Data) -> Data {
        var out = line
        out.append(0x0A)
        out.withUnsafeBytes { _ = write(descriptor, $0.baseAddress, $0.count) }
        while true {
            if let end = buffer.firstIndex(of: 0x0A) {
                let answer = Data(buffer[buffer.startIndex..<end])
                buffer = Data(buffer[buffer.index(after: end)...])
                return answer
            }
            var bytes = [UInt8](repeating: 0, count: 65536)
            let count = read(descriptor, &bytes, bytes.count)
            if count <= 0 { return Data() }
            buffer.append(contentsOf: bytes[0..<count])
        }
    }

    /// Sends one line as it is and returns the answer's line.
    func send(line: Data) async -> Data {
        await withCheckedContinuation { continuation in
            DispatchQueue.global().async { continuation.resume(returning: self.exchange(line)) }
        }
    }
}

/// The target of the test menu's items.
@MainActor
private final class MenuTarget: NSObject, NSMenuItemValidation {
    var marked = 0
    var refused = 0
    var capitals = 0

    @objc func mark(_ sender: Any?) { marked += 1 }
    @objc func refuse(_ sender: Any?) { refused += 1 }
    @objc func capital(_ sender: Any?) { capitals += 1 }

    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        item.action != #selector(refuse(_:))
    }
}

@MainActor
@Suite(.serialized, .enabled(if: hasConsole))
struct CheckChannelTests {
    /// A page with a coloured ground, a button that counts its presses and an input that
    /// keeps the keys it is given.
    private static let html = """
        <!doctype html><body style="margin:0;background:rgb(0,128,255)">
        <button id="b" style="position:absolute;left:20px;top:20px;width:120px;height:60px"
          onclick="window.presses = (window.presses || 0) + 1">press</button>
        <input id="i" style="position:absolute;left:20px;top:120px;width:300px;height:40px;font-size:24px">
        <script>
          window.keys = [];
          document.addEventListener("keydown", (event) => window.keys.push(event.key));
          document.addEventListener("contextmenu", (event) => {
            event.preventDefault();
            window.menus = (window.menus || 0) + 1;
          });
        </script>
        """

    private struct Opened {
        let window: NSWindow
        let page: ChatPage
        let channel: CheckChannel
        let client: Client
        let path: String
        let target: MenuTarget
        let quits: Counter
    }

    @MainActor
    private final class Counter {
        var count = 0
    }

    /// A window with the page, a channel on a socket of its own, and the runner's end of it.
    private func open() async throws -> Opened {
        NSApplication.shared.setActivationPolicy(.accessory)
        let page = ChatPage(
            frame: NSRect(x: 0, y: 0, width: 600, height: 400),
            configuration: WKWebViewConfiguration())
        let window = NSWindow(
            contentRect: NSRect(x: 200, y: 200, width: 600, height: 400),
            styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.title = "Check"
        window.contentView = page
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(page)
        page.loadHTMLString(Self.html, baseURL: nil)
        for _ in 0..<100 {
            let ready = try? await page.evaluateJavaScript(
                "document.readyState === 'complete' && Array.isArray(window.keys)")
            if ready as? Bool == true { break }
            try await Task.sleep(for: .milliseconds(50))
        }
        // A Unix socket's path is short, and the temporary directory's is not always.
        let path = "/tmp/kyo-check-test-\(getpid())-\(UInt32.random(in: 0..<UInt32.max)).sock"
        let quits = Counter()
        let channel = CheckChannel(path: path, window: { window }, page: { page }) { quits.count += 1 }
        try channel.start()
        let target = MenuTarget()
        return Opened(
            window: window, page: page, channel: channel, client: try Client(path: path),
            path: path, target: target, quits: quits)
    }

    private func close(_ bed: Opened) {
        bed.channel.stop()
        bed.window.orderOut(nil)
    }

    /// Asks one request and returns its answer, which must be an object.
    private func ask(_ bed: Opened, _ request: [String: Any]) async throws -> [String: Any] {
        let line = try JSONSerialization.data(withJSONObject: request)
        let answer = await bed.client.send(line: line)
        return try #require(try JSONSerialization.jsonObject(with: answer) as? [String: Any])
    }

    /// The value of a request that must succeed.
    private func value(_ bed: Opened, _ request: [String: Any]) async throws -> Any {
        let answer = try await ask(bed, request)
        #expect(answer["ok"] as? Bool == true, "\(request["op"] ?? "") answered \(answer)")
        return answer["value"] ?? NSNull()
    }

    /// A main menu of one menu, Check, with an item that validates, one that does not, and
    /// a submenu. The app's own main menu is put back afterwards.
    private func menu(for target: MenuTarget) -> NSMenu {
        let main = NSMenu(title: "Main")
        let check = NSMenu(title: "Check")
        let mark = NSMenuItem(title: "Mark", action: #selector(MenuTarget.mark(_:)), keyEquivalent: "k")
        mark.keyEquivalentModifierMask = [.command, .shift]
        let refuse = NSMenuItem(title: "Refuse", action: #selector(MenuTarget.refuse(_:)), keyEquivalent: "")
        let more = NSMenuItem(title: "More", action: nil, keyEquivalent: "")
        more.submenu = NSMenu(title: "More")
        let inner = NSMenuItem(title: "Inner", action: #selector(MenuTarget.mark(_:)), keyEquivalent: "")
        more.submenu?.addItem(inner)
        // The other way an item states Shift: in the capital of its equivalent.
        let capital = NSMenuItem(title: "Capital", action: #selector(MenuTarget.capital(_:)), keyEquivalent: "J")
        capital.keyEquivalentModifierMask = [.command]
        [mark, refuse, inner, capital].forEach { $0.target = target }
        [mark, refuse, .separator(), more, capital].forEach { check.addItem($0) }
        let holder = NSMenuItem(title: "Check", action: nil, keyEquivalent: "")
        holder.submenu = check
        main.addItem(holder)
        return main
    }

    @Test func theSocketIsItsOwnersAloneAndGoesWhenTheChannelStops() async throws {
        let bed = try await open()
        var information = stat()
        #expect(lstat(bed.path, &information) == 0)
        #expect(information.st_mode & S_IFMT == S_IFSOCK)
        #expect(information.st_mode & 0o777 == 0o600)
        #expect(information.st_uid == getuid())
        close(bed)
        #expect(lstat(bed.path, &information) != 0)
    }

    @Test func aRequestThatIsNotOneIsAnsweredWithItsReason() async throws {
        let bed = try await open()
        defer { close(bed) }
        let notJSON = await bed.client.send(line: Data("not json".utf8))
        let first = try #require(try JSONSerialization.jsonObject(with: notJSON) as? [String: Any])
        #expect(first["ok"] as? Bool == false)
        let unknown = try await ask(bed, ["op": "nothing", "id": 7])
        #expect(unknown["ok"] as? Bool == false)
        #expect(unknown["id"] as? Int == 7)
        #expect((unknown["error"] as? String)?.contains("nothing") == true)
    }

    @Test func evalRunsAScriptInThePageAndReturnsItsValue() async throws {
        let bed = try await open()
        defer { close(bed) }
        let sum = try await value(bed, ["op": "eval", "script": "return a + b;", "args": ["a": 2, "b": 3]])
        #expect(sum as? Int == 5)
        // The script is an async function's body, and a promise it returns is waited for.
        let later = try await value(
            bed,
            [
                "op": "eval",
                "script": "await new Promise((done) => setTimeout(done, 20)); return {id: document.getElementById('b').id, list: [1, null]};",
            ])
        let object = try #require(later as? [String: Any])
        #expect(object["id"] as? String == "b")
        #expect((object["list"] as? [Any])?.count == 2)
        #expect(try await value(bed, ["op": "eval", "script": "window.keys.length;"]) is NSNull)
        // An answer longer than the socket's buffer arrives whole.
        let long = try await value(bed, ["op": "eval", "script": "return 'x'.repeat(2000000);"])
        #expect((long as? String)?.count == 2_000_000)
        let thrown = try await ask(bed, ["op": "eval", "script": "throw new Error('said so');"])
        #expect(thrown["ok"] as? Bool == false)
        #expect((thrown["error"] as? String)?.contains("said so") == true)
    }

    @Test func menusReturnsTheMainMenuValidated() async throws {
        let bed = try await open()
        defer { close(bed) }
        let before = NSApp.mainMenu
        NSApp.mainMenu = menu(for: bed.target)
        defer { NSApp.mainMenu = before }
        let tree = try #require(try await value(bed, ["op": "menus"]) as? [[String: Any]])
        #expect(tree.map { $0["title"] as? String } == ["Check"])
        let items = try #require(tree[0]["items"] as? [[String: Any]])
        #expect(items.map { $0["title"] as? String } == ["Mark", "Refuse", nil, "More", "Capital"])
        #expect(items[0]["enabled"] as? Bool == true)
        #expect(items[0]["key"] as? String == "k")
        #expect(items[0]["modifiers"] as? [String] == ["command", "shift"])
        // The item its target does not validate is disabled, as it is when the menu opens.
        #expect(items[1]["enabled"] as? Bool == false)
        #expect(items[2]["separator"] as? Bool == true)
        let inner = try #require(items[3]["items"] as? [[String: Any]])
        #expect(inner.map { $0["title"] as? String } == ["Inner"])
    }

    @Test func menuPerformsAnItemByItsPathAndNotADisabledOne() async throws {
        let bed = try await open()
        defer { close(bed) }
        let before = NSApp.mainMenu
        NSApp.mainMenu = menu(for: bed.target)
        defer { NSApp.mainMenu = before }
        let done = try #require(try await value(bed, ["op": "menu", "path": ["Check", "Mark"]]) as? [String: Any])
        #expect(done["performed"] as? Bool == true)
        #expect(bed.target.marked == 1)
        _ = try await value(bed, ["op": "menu", "path": ["Check", "More", "Inner"]])
        #expect(bed.target.marked == 2)
        let refused = try #require(
            try await value(bed, ["op": "menu", "path": ["Check", "Refuse"]]) as? [String: Any])
        #expect(refused["performed"] as? Bool == false)
        #expect(bed.target.refused == 0)
        let missing = try await ask(bed, ["op": "menu", "path": ["Check", "Absent"]])
        #expect(missing["ok"] as? Bool == false)
        #expect((missing["error"] as? String)?.contains("Mark") == true)
    }

    @Test func keySendsAKeyThroughTheMenuBarsEquivalentsAndToThePage() async throws {
        let bed = try await open()
        defer { close(bed) }
        let before = NSApp.mainMenu
        NSApp.mainMenu = menu(for: bed.target)
        defer { NSApp.mainMenu = before }
        // A key equivalent of the menu bar is taken there, as a hardware key's is.
        // The page is offered it first and answers from its own process that it has no use
        // for it, so the item's action arrives a moment after the request is answered.
        _ = try await value(bed, ["op": "key", "key": "k", "modifiers": ["command", "shift"]])
        for _ in 0..<40 where bed.target.marked == 0 {
            try await Task.sleep(for: .milliseconds(50))
        }
        #expect(bed.target.marked == 1)
        _ = try await value(bed, ["op": "key", "key": "j", "modifiers": ["command", "shift"]])
        for _ in 0..<40 where bed.target.capitals == 0 {
            try await Task.sleep(for: .milliseconds(50))
        }
        #expect(bed.target.capitals == 1)
        #expect(bed.target.marked == 1)
        _ = try await value(bed, ["op": "eval", "script": "window.keys = [];"])
        // A key no menu takes goes on to the window's first responder, which is the page.
        _ = try await value(bed, ["op": "eval", "script": "document.getElementById('i').focus();"])
        _ = try await value(bed, ["op": "key", "text": "aB"])
        _ = try await value(bed, ["op": "key", "key": "left"])
        var keys: [String] = []
        for _ in 0..<40 {
            keys = try await value(bed, ["op": "eval", "script": "return window.keys;"]) as? [String] ?? []
            if keys.count >= 3 { break }
            try await Task.sleep(for: .milliseconds(50))
        }
        #expect(keys == ["a", "B", "ArrowLeft"])
        let unknown = try await ask(bed, ["op": "key", "key": "nokey"])
        #expect(unknown["ok"] as? Bool == false)
    }

    @Test func mouseClicksAPointOfThePage() async throws {
        let bed = try await open()
        defer { close(bed) }
        _ = try await value(bed, ["op": "mouse", "x": 80, "y": 50])
        var presses = 0
        for _ in 0..<40 {
            presses = try await value(bed, ["op": "eval", "script": "return window.presses || 0;"]) as? Int ?? 0
            if presses > 0 { break }
            try await Task.sleep(for: .milliseconds(50))
        }
        #expect(presses == 1)
        let outside = try await ask(bed, ["op": "mouse", "x": 5000, "y": 50])
        #expect(outside["ok"] as? Bool == false)
    }

    /// A right press reaches the page, which here draws no native menu, as the app's page
    /// draws its own for most of what it offers. That a native menu is read and closed is
    /// checked against the running check build (`apps/apple/check/test_channel_in_app.py`)
    /// and not here. Waiting for a menu takes the app's events, and once a process has done
    /// that, each later event from the window server stops the run loop the process waits
    /// in. Under `swift test` nothing fetches events between requests, so that stop ends
    /// the test process with no verdict. This was measured: every run of this suite that
    /// asked for a menu ended within the two tests after it.
    @Test func aRightPressReachesThePage() async throws {
        let bed = try await open()
        defer { close(bed) }
        let answer = try #require(
            try await value(bed, ["op": "mouse", "x": 400, "y": 300, "button": "right"]) as? [String: Any])
        #expect(answer["events"] as? Int == 2)
        #expect(try await value(bed, ["op": "eval", "script": "return window.menus;"]) as? Int == 1)
    }

    @Test func snapshotWritesAPNGOfTheWindowWithThePageInIt() async throws {
        let bed = try await open()
        defer { close(bed) }
        let file = FileManager.default.temporaryDirectory.appending(path: "kyo-check-\(getpid()).png")
        defer { try? FileManager.default.removeItem(at: file) }
        let shot = try #require(try await value(bed, ["op": "snapshot", "path": file.path]) as? [String: Any])
        let image = try #require(NSBitmapImageRep(data: try Data(contentsOf: file)))
        #expect(shot["width"] as? Int == image.pixelsWide)
        #expect(shot["height"] as? Int == image.pixelsHigh)
        let scale = try #require(shot["scale"] as? Double)
        // The image is the window's frame, title bar included, so it is taller than the page.
        #expect(Double(image.pixelsWide) == 600 * scale)
        #expect(Double(image.pixelsHigh) > 400 * scale)
        let place = try #require(shot["page"] as? [String: Double])
        #expect(place["width"] == 600 * scale)
        #expect(place["height"] == 400 * scale)
        // A point of the page's ground, away from the button and the input, is its colour.
        let x = Int(place["x"]! + 450 * scale)
        let y = Int(place["y"]! + 300 * scale)
        // The ground is rgb(0, 128, 255). The image is in the display's colour space, so the
        // components are near those values and not equal to them.
        func isGround(_ image: NSBitmapImageRep) -> Bool {
            guard let colour = image.colorAt(x: x, y: y)?.usingColorSpace(.sRGB) else { return false }
            return colour.redComponent < 0.25 && colour.blueComponent > 0.85
                && colour.greenComponent > 0.3 && colour.greenComponent < 0.7
        }
        #expect(isGround(image), "\(String(describing: image.colorAt(x: x, y: y)))")
        // The title bar above the page is not the page's ground.
        #expect(image.colorAt(x: x, y: Int(place["y"]! / 2)) != image.colorAt(x: x, y: y))
        // The page's part alone holds the page, from `takeSnapshot`.
        _ = try await value(bed, ["op": "snapshot", "path": file.path, "parts": ["web"]])
        #expect(isGround(try #require(NSBitmapImageRep(data: try Data(contentsOf: file)))))
    }

    @Test func stateSaysWhatTheWindowIs() async throws {
        let bed = try await open()
        defer { close(bed) }
        let state = try #require(try await value(bed, ["op": "state"]) as? [String: Any])
        #expect(state["title"] as? String == "Check")
        #expect(state["first_responder"] as? String == "ChatPage")
        #expect(state["visible"] as? Bool == true)
        #expect(state["key"] as? Bool == bed.window.isKeyWindow)
        #expect(state["active"] as? Bool == NSApp.isActive)
        #expect(state["frame"] as? [Double] == [200, 200, 600, bed.window.frame.height].map(Double.init))
    }

    @Test func windowPlacesTheWindow() async throws {
        let bed = try await open()
        defer { close(bed) }
        let state = try #require(
            try await value(bed, ["op": "window", "frame": [240, 260, 640, 480], "focus": true]) as? [String: Any])
        #expect(state["frame"] as? [Double] == [240, 260, 640, 480])
        #expect(bed.window.frame == NSRect(x: 240, y: 260, width: 640, height: 480))
    }

    @Test func quitAnswersAndThenEndsTheApp() async throws {
        let bed = try await open()
        defer { close(bed) }
        let answer = try await ask(bed, ["op": "quit"])
        #expect(answer["ok"] as? Bool == true)
        for _ in 0..<40 where bed.quits.count == 0 {
            try await Task.sleep(for: .milliseconds(25))
        }
        #expect(bed.quits.count == 1)
    }
}
#endif
