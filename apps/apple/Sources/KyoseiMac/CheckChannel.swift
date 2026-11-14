// The check channel (KYO-944, proposal KYO-942). Everything in this file is compiled only
// under the `KYOSEI_CHECK` condition, which `package.sh check` passes and the packaging of
// the shipped app does not, so the shipped executable holds none of it. A test reads a build
// made without the condition and fails if this file's marker, the name of the socket
// variable, is in it (`Tests/KyoseiMacTests/CheckBuildTests.swift`).
//
// A check build is driven by a runner (`apps/apple/check/`) that cannot use the accessibility
// tree or take a screenshot from outside without the system asking the person for consent.
// The app therefore acts on itself: while `KYOSEI_CHECK_SOCKET` names a path, it listens
// there on a Unix socket that only its owner can open and answers one line of JSON for each
// line it is sent. A request is `{"op": <name>, ...}`, with an optional `id` that the answer
// repeats; an answer is `{"ok": true, "value": ...}` or `{"ok": false, "error": <sentence>}`.
// The requests are listed at `CheckChannel.answer(to:)`.
#if KYOSEI_CHECK
import AppKit
import Darwin
import Foundation
import WebKit

/// What makes a check build safe to start on a machine that also runs the shipped app.
enum CheckBuild {
    static let serverVariable = "KYOSEI_SERVE_URL"
    static let socketVariable = "KYOSEI_CHECK_SOCKET"
    static let tokenVariable = "KYOSEI_CHECK_TOKEN"
    static let frameVariable = "KYOSEI_CHECK_FRAME"
    static let activateVariable = "KYOSEI_CHECK_ACTIVATE"
    /// The shipped app's identifier, whose defaults a check build must never open.
    static let shippedIdentifier = "dev.kyosei.apple"

    /// Why this process must not start, or nil when it may. A check build has no saved
    /// server and no default one: without `KYOSEI_SERVE_URL` it would have nothing to read,
    /// and with the shipped app's fallback it would read the person's own server. It makes
    /// no keychain call, so `seal-key` is refused, and it does not run under the shipped
    /// app's bundle identifier, where `UserDefaults.standard` is that app's settings.
    static func refusal(
        arguments: [String], environment: [String: String], bundleIdentifier: String?
    ) -> String? {
        if bundleIdentifier == shippedIdentifier {
            return "A check build does not run under the identifier \(shippedIdentifier), "
                + "whose settings are the installed app's. Build it with `package.sh check`."
        }
        if arguments.count >= 2, arguments[1] == "seal-key" {
            return "A check build makes no keychain call, so it has no seal-key command."
        }
        let server = environment[serverVariable] ?? ""
        guard let url = URL(string: server), url.scheme != nil, url.host != nil else {
            return "A check build starts only with \(serverVariable) set to the address of a "
                + "test server, such as http://127.0.0.1:<port>. It has no saved or default server."
        }
        return nil
    }

    /// The device token a check build sends: the one its runner issued, from the environment.
    /// The shipped app reads its token from the keychain with `security`; this build does not.
    static func token(in environment: [String: String]) -> String? {
        let token = (environment[tokenVariable] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return token.isEmpty ? nil : token
    }

    /// `KYOSEI_CHECK_FRAME` as a window frame: `x,y,width,height` in screen points.
    static func frame(in environment: [String: String]) -> NSRect? {
        let parts = (environment[frameVariable] ?? "").split(separator: ",").map {
            Double($0.trimmingCharacters(in: .whitespaces))
        }
        guard parts.count == 4, let x = parts[0], let y = parts[1], let width = parts[2],
            let height = parts[3], width > 0, height > 0
        else { return nil }
        return NSRect(x: x, y: y, width: width, height: height)
    }
}

/// A request the channel could not carry out, said as the sentence its answer holds.
struct CheckFailure: Error {
    let message: String
    init(_ message: String) { self.message = message }
}

/// Listens on the socket and answers requests. The window and the page are asked for at each
/// request, since the app's window is made after the channel starts.
@MainActor
final class CheckChannel {
    private let path: String
    private let window: () -> NSWindow?
    private let page: () -> WKWebView?
    private let terminate: () -> Void
    private var listener: Int32 = -1
    private var accepting: DispatchSourceRead?
    private var connections: [Int32: Connection] = [:]

    private final class Connection {
        var buffer = Data()
        var source: DispatchSourceRead?
        /// The requests of one connection are answered in the order they were sent.
        var last: Task<Void, Never>?
    }

    init(
        path: String, window: @escaping () -> NSWindow?, page: @escaping () -> WKWebView?,
        terminate: @escaping () -> Void = { NSApp.terminate(nil) }
    ) {
        self.path = path
        self.window = window
        self.page = page
        self.terminate = terminate
    }

    /// The app's channel, when the environment names a socket; otherwise the build is inert.
    static func startFromEnvironment(feed: SessionFeed) -> CheckChannel? {
        let environment = ProcessInfo.processInfo.environment
        guard let path = environment[CheckBuild.socketVariable], !path.isEmpty else { return nil }
        let channel = CheckChannel(
            path: path, window: { feed.webView?.window }, page: { feed.webView })
        do {
            try channel.start()
        } catch {
            let said = (error as? CheckFailure)?.message ?? "\(error)"
            FileHandle.standardError.write(Data("check channel: \(said)\n".utf8))
            exit(65)
        }
        return channel
    }

    // MARK: The socket

    /// Binds the socket and begins accepting. The socket file is made readable and writable
    /// by its owner alone: the mask is narrowed for the bind, so that there is no moment at
    /// which another account could open it, and the mode is set again afterwards.
    func start() throws {
        var address = sockaddr_un()
        let bytes = Array(path.utf8)
        let capacity = MemoryLayout.size(ofValue: address.sun_path)
        guard bytes.count < capacity else {
            throw CheckFailure("the socket path is \(bytes.count) bytes and a Unix socket takes \(capacity - 1)")
        }
        var information = stat()
        if lstat(path, &information) == 0 {
            guard information.st_mode & S_IFMT == S_IFSOCK else {
                throw CheckFailure("\(path) exists and is not a socket")
            }
            unlink(path)
        }
        address.sun_family = sa_family_t(AF_UNIX)
        withUnsafeMutableBytes(of: &address.sun_path) { raw in
            raw.copyBytes(from: bytes)
        }
        let descriptor = socket(AF_UNIX, SOCK_STREAM, 0)
        guard descriptor >= 0 else { throw CheckFailure("no socket: \(String(cString: strerror(errno)))") }
        let mask = umask(0o177)
        let bound = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        umask(mask)
        guard bound == 0, chmod(path, 0o600) == 0, listen(descriptor, 4) == 0 else {
            let why = String(cString: strerror(errno))
            close(descriptor)
            throw CheckFailure("could not listen at \(path): \(why)")
        }
        _ = fcntl(descriptor, F_SETFL, fcntl(descriptor, F_GETFL) | O_NONBLOCK)
        listener = descriptor
        let source = DispatchSource.makeReadSource(fileDescriptor: descriptor, queue: .main)
        source.setEventHandler { [weak self] in
            MainActor.assumeIsolated { self?.accept() }
        }
        source.resume()
        accepting = source
    }

    /// Closes the socket and removes its file.
    func stop() {
        accepting?.cancel()
        accepting = nil
        for (descriptor, connection) in connections {
            connection.source?.cancel()
            close(descriptor)
        }
        connections = [:]
        if listener >= 0 {
            close(listener)
            unlink(path)
        }
        listener = -1
    }

    private func accept() {
        while true {
            let descriptor = Darwin.accept(listener, nil, nil)
            guard descriptor >= 0 else { return }
            var on: Int32 = 1
            setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, &on, socklen_t(MemoryLayout<Int32>.size))
            let connection = Connection()
            let source = DispatchSource.makeReadSource(fileDescriptor: descriptor, queue: .main)
            source.setEventHandler { [weak self] in
                MainActor.assumeIsolated { self?.read(descriptor) }
            }
            source.resume()
            connection.source = source
            connections[descriptor] = connection
        }
    }

    private func read(_ descriptor: Int32) {
        guard let connection = connections[descriptor] else { return }
        var bytes = [UInt8](repeating: 0, count: 65536)
        let count = Darwin.read(descriptor, &bytes, bytes.count)
        guard count > 0 else {
            // The runner closed its end. The descriptor is closed once the answers that
            // were already asked for have been written.
            connection.source?.cancel()
            connections[descriptor] = nil
            let last = connection.last
            Task {
                await last?.value
                close(descriptor)
            }
            return
        }
        connection.buffer.append(contentsOf: bytes[0..<count])
        while let end = connection.buffer.firstIndex(of: 0x0A) {
            let line = Data(connection.buffer[connection.buffer.startIndex..<end])
            connection.buffer = Data(connection.buffer[connection.buffer.index(after: end)...])
            guard !line.isEmpty else { continue }
            let before = connection.last
            connection.last = Task { [weak self] in
                await before?.value
                guard let self else { return }
                let (answer, then) = await self.answer(to: line)
                Self.write(answer + [0x0A], to: descriptor)
                then?()
            }
        }
    }

    private static func write(_ data: Data, to descriptor: Int32) {
        data.withUnsafeBytes { raw in
            var sent = 0
            while sent < raw.count {
                let wrote = Darwin.write(descriptor, raw.baseAddress! + sent, raw.count - sent)
                if wrote <= 0 { return }
                sent += wrote
            }
        }
    }

    // MARK: Requests

    /// Answers one request, and returns what is to be done once the answer has been written.
    ///
    /// - `eval`: `script` is the body of an async function run in the page, with the members
    ///   of `args` as its variables; the value it returns is the answer, as JSON.
    /// - `menus`: the main menu as a tree, each menu validated as opening it would.
    /// - `menu`: `path` is the titles from the menu bar to one item, which is validated and
    ///   performed as choosing it would.
    /// - `key`: `key` is one character or a named key, with `modifiers`; or `text` is typed
    ///   one key at a time. The events go to `NSApp.sendEvent`.
    /// - `mouse`: a press, a release or both at `x`, `y` in the page's own coordinates.
    /// - `snapshot`: writes a PNG of the window to `path`.
    /// - `state`: the window's title, whether it is key, its first responder, whether the
    ///   app is active and which app is in front.
    /// - `window`: places the window, or makes it key.
    /// - `quit`: ends the app as its Quit item does.
    func answer(to line: Data) async -> (Data, (() -> Void)?) {
        var id: Any?
        var then: (() -> Void)?
        var body: [String: Any]
        do {
            guard let request = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else {
                throw CheckFailure("a request is one JSON object on one line")
            }
            id = request["id"]
            guard let op = request["op"] as? String else { throw CheckFailure("a request names its op") }
            let value: Any
            switch op {
            case "eval": value = try await evaluate(request)
            case "menus": value = try menus()
            case "menu": value = try choose(request)
            case "key": value = try key(request)
            case "mouse": value = try mouse(request)
            case "snapshot": value = try await snapshot(request)
            case "state": value = try state()
            case "window": value = try place(request)
            case "quit":
                value = NSNull()
                then = terminate
            default: throw CheckFailure("there is no request named \(op)")
            }
            body = ["ok": true, "value": value]
        } catch {
            body = ["ok": false, "error": (error as? CheckFailure)?.message ?? "\(error)"]
        }
        if let id { body["id"] = id }
        let data = (try? JSONSerialization.data(withJSONObject: body, options: [.sortedKeys]))
            ?? Data(#"{"ok":false,"error":"the answer could not be written as JSON"}"#.utf8)
        return (data, then)
    }

    private func theWindow() throws -> NSWindow {
        guard let window = window() else { throw CheckFailure("the app has no window yet") }
        return window
    }

    private func thePage() throws -> WKWebView {
        guard let page = page() else { throw CheckFailure("the app has no page yet") }
        return page
    }

    // MARK: eval

    /// The script is wrapped so that what it returns crosses to the app as JSON text: a value
    /// the bridge would convert itself, such as a date or a DOM node, is then either written
    /// the way the page's own `JSON.stringify` writes it or refused there, and never guessed.
    private func evaluate(_ request: [String: Any]) async throws -> Any {
        guard let script = request["script"] as? String else { throw CheckFailure("eval takes a script") }
        let page = try thePage()
        let wrapped = """
            const __body = async () => {
            \(script)
            };
            const __value = await __body();
            return JSON.stringify(__value === undefined ? null : __value);
            """
        let result: Any?
        do {
            result = try await page.callAsyncJavaScript(
                wrapped, arguments: request["args"] as? [String: Any] ?? [:], in: nil,
                contentWorld: .page)
        } catch {
            let details = (error as NSError).userInfo
            let said = details["WKJavaScriptExceptionMessage"] as? String ?? error.localizedDescription
            throw CheckFailure("the script failed: \(said)")
        }
        guard let text = result as? String,
            let value = try? JSONSerialization.jsonObject(with: Data(text.utf8), options: [.fragmentsAllowed])
        else { throw CheckFailure("the script returned a value that is not JSON") }
        return value
    }

    // MARK: menus and menu

    private static func names(of flags: NSEvent.ModifierFlags) -> [String] {
        var names: [String] = []
        if flags.contains(.command) { names.append("command") }
        if flags.contains(.shift) { names.append("shift") }
        if flags.contains(.option) { names.append("option") }
        if flags.contains(.control) { names.append("control") }
        return names
    }

    /// Brings a menu to the state it has when it opens: its delegate fills it, and each item
    /// is validated against the responder that would take its action.
    private func refresh(_ menu: NSMenu) {
        menu.delegate?.menuNeedsUpdate?(menu)
        menu.update()
    }

    private func describe(_ menu: NSMenu) -> [[String: Any]] {
        refresh(menu)
        return menu.items.map { item in
            if item.isSeparatorItem { return ["separator": true] }
            var row: [String: Any] = [
                "title": item.title,
                "enabled": item.isEnabled,
                "hidden": item.isHidden,
                "key": item.keyEquivalent,
                "modifiers": Self.names(of: item.keyEquivalentModifierMask),
                "state": item.state == .on ? "on" : item.state == .mixed ? "mixed" : "off",
            ]
            if let submenu = item.submenu { row["items"] = describe(submenu) }
            return row
        }
    }

    private func mainMenu() throws -> NSMenu {
        guard let menu = NSApp.mainMenu else { throw CheckFailure("the app has no main menu") }
        return menu
    }

    private func menus() throws -> Any {
        describe(try mainMenu())
    }

    private func choose(_ request: [String: Any]) throws -> Any {
        guard let path = request["path"] as? [String], !path.isEmpty else {
            throw CheckFailure("menu takes a path of titles, such as [\"Edit\", \"Undo\"]")
        }
        var menu = try mainMenu()
        for (depth, title) in path.enumerated() {
            refresh(menu)
            guard let item = menu.items.first(where: { $0.title == title && !$0.isSeparatorItem }) else {
                let offered = menu.items.filter { !$0.isSeparatorItem }.map(\.title)
                throw CheckFailure("no item titled \(title) among \(offered)")
            }
            if depth < path.count - 1 {
                guard let submenu = item.submenu else { throw CheckFailure("\(title) has no submenu") }
                menu = submenu
                continue
            }
            guard item.isEnabled else { return ["performed": false, "enabled": false, "title": item.title] }
            menu.performActionForItem(at: menu.index(of: item))
            return ["performed": true, "enabled": true, "title": item.title]
        }
        throw CheckFailure("the path named no item")
    }

    // MARK: key

    private static let named: [String: (code: UInt16, characters: String, function: Bool)] = [
        "return": (36, "\r", false), "tab": (48, "\t", false), "space": (49, " ", false),
        "delete": (51, "\u{7F}", false), "escape": (53, "\u{1B}", false),
        "left": (123, "\u{F702}", true), "right": (124, "\u{F703}", true),
        "down": (125, "\u{F701}", true), "up": (126, "\u{F700}", true),
    ]

    /// The keys of a US keyboard: the character each types alone, the one it types with
    /// Shift, and its key code.
    private static let keys: [(plain: Character, shifted: Character, code: UInt16)] = [
        ("a", "A", 0), ("s", "S", 1), ("d", "D", 2), ("f", "F", 3), ("h", "H", 4), ("g", "G", 5),
        ("z", "Z", 6), ("x", "X", 7), ("c", "C", 8), ("v", "V", 9), ("b", "B", 11), ("q", "Q", 12),
        ("w", "W", 13), ("e", "E", 14), ("r", "R", 15), ("y", "Y", 16), ("t", "T", 17),
        ("1", "!", 18), ("2", "@", 19), ("3", "#", 20), ("4", "$", 21), ("6", "^", 22),
        ("5", "%", 23), ("=", "+", 24), ("9", "(", 25), ("7", "&", 26), ("-", "_", 27),
        ("8", "*", 28), ("0", ")", 29), ("]", "}", 30), ("o", "O", 31), ("u", "U", 32),
        ("[", "{", 33), ("i", "I", 34), ("p", "P", 35), ("l", "L", 37), ("j", "J", 38),
        ("'", "\"", 39), ("k", "K", 40), (";", ":", 41), ("\\", "|", 42), (",", "<", 43),
        ("/", "?", 44), ("n", "N", 45), ("m", "M", 46), (".", ">", 47), ("`", "~", 50),
    ]
    private static let codes: [Character: UInt16] = Dictionary(
        keys.flatMap { [($0.plain, $0.code), ($0.shifted, $0.code)] }, uniquingKeysWith: { first, _ in first })
    private static let plain: [UInt16: Character] = Dictionary(
        keys.map { ($0.code, $0.plain) }, uniquingKeysWith: { first, _ in first })
    private static let shifted: [UInt16: Character] = Dictionary(
        keys.map { ($0.code, $0.shifted) }, uniquingKeysWith: { first, _ in first })

    private static func flags(_ names: [String]) throws -> NSEvent.ModifierFlags {
        var flags: NSEvent.ModifierFlags = []
        for name in names {
            switch name {
            case "command": flags.insert(.command)
            case "shift": flags.insert(.shift)
            case "option": flags.insert(.option)
            case "control": flags.insert(.control)
            default: throw CheckFailure("there is no modifier named \(name)")
            }
        }
        return flags
    }

    /// The field of a Quartz event that holds the number of the window it is for. It has
    /// no public name; an `NSEvent` made from a Quartz event was measured to take its
    /// `windowNumber` from it on macOS 27, and `press` checks that it did.
    private static let windowField = CGEventField(rawValue: 51)

    /// One key: a press and a release, handed to the app as the window server hands a key.
    /// The app then offers it to the window's key equivalents and the menu bar's, and gives
    /// it to the first responder if neither takes it.
    ///
    /// The event is made from a Quartz keyboard event, as a hardware key's is, because the
    /// menu bar matches a key equivalent differently against an event made by
    /// `NSEvent.keyEvent`. Measured: an item with the equivalent `k` and the modifiers Command
    /// and Shift took such an event only when its unmodified character was `k`, an item with
    /// the equivalent `K` and Command only when it was `K`, and a hardware key reaches both.
    /// An event made from a Quartz event reached both. Its characters are those the key
    /// types on the keyboard layout in use, unless `typed` states them, which typing text
    /// does so that the text does not depend on the layout. Where the Quartz event cannot
    /// be made for the window, the event is made by `NSEvent.keyEvent` with `plain` as its
    /// unmodified character.
    private func press(
        _ characters: String, plain: String? = nil, typed: Bool = false, code: UInt16,
        flags: NSEvent.ModifierFlags, in window: NSWindow
    ) throws {
        for (type, down) in [(NSEvent.EventType.keyDown, true), (.keyUp, false)] {
            var event: NSEvent?
            if let field = Self.windowField,
                let quartz = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down)
            {
                var quartzFlags: CGEventFlags = []
                if flags.contains(.command) { quartzFlags.insert(.maskCommand) }
                if flags.contains(.shift) { quartzFlags.insert(.maskShift) }
                if flags.contains(.option) { quartzFlags.insert(.maskAlternate) }
                if flags.contains(.control) { quartzFlags.insert(.maskControl) }
                if flags.contains(.function) { quartzFlags.insert(.maskSecondaryFn) }
                quartz.flags = quartzFlags
                quartz.setIntegerValueField(field, value: Int64(window.windowNumber))
                if typed {
                    let units = Array(characters.utf16)
                    quartz.keyboardSetUnicodeString(stringLength: units.count, unicodeString: units)
                }
                if let made = NSEvent(cgEvent: quartz), made.windowNumber == window.windowNumber {
                    event = made
                }
            }
            event = event
                ?? NSEvent.keyEvent(
                    with: type, location: .zero, modifierFlags: flags,
                    timestamp: ProcessInfo.processInfo.systemUptime,
                    windowNumber: window.windowNumber, context: nil, characters: characters,
                    charactersIgnoringModifiers: plain ?? characters, isARepeat: false, keyCode: code)
            guard let event else { throw CheckFailure("no key event could be made for \(characters)") }
            NSApp.sendEvent(event)
        }
    }

    private func key(_ request: [String: Any]) throws -> Any {
        let window = try theWindow()
        var flags = try Self.flags(request["modifiers"] as? [String] ?? [])
        if let text = request["text"] as? String {
            for character in text {
                if character == "\n" {
                    try press("\r", typed: true, code: 36, flags: [], in: window)
                    continue
                }
                guard let code = Self.codes[character] ?? (character == " " ? 49 : nil) else {
                    throw CheckFailure("no key of a US keyboard types \(character)")
                }
                let shifted = Self.shifted[code] == character
                let plain = Self.plain[code].map(String.init)
                try press(
                    String(character), plain: plain, typed: true, code: code,
                    flags: shifted ? [.shift] : [], in: window)
            }
            return ["keys": text.count]
        }
        guard let name = request["key"] as? String, !name.isEmpty else {
            throw CheckFailure("key takes a key, such as \"z\" or \"return\", or a text to type")
        }
        if let known = Self.named[name] {
            if known.function { flags.insert(.function) }
            try press(known.characters, code: known.code, flags: flags, in: window)
            return ["keys": 1]
        }
        guard name.count == 1, let code = Self.codes[Character(name)] else {
            throw CheckFailure("there is no key named \(name)")
        }
        // With Shift held a key's character is its shifted one, as the keyboard reports it.
        let plain = Self.plain[code].map(String.init) ?? name
        let characters = flags.contains(.shift) ? (Self.shifted[code].map(String.init) ?? name) : plain
        try press(characters, plain: plain, code: code, flags: flags, in: window)
        return ["keys": 1]
    }

    // MARK: mouse

    /// Watches for a menu that a right press opens, reads its items when its tracking has
    /// begun, chooses one if asked, and ends the tracking, so that a press on something
    /// that opens a native menu does not leave the app waiting in the menu.
    private final class MenuWatch: NSObject {
        var menu: NSMenu?
        var titles: [String]?
        var choose: String?
        var chosen = false

        @objc func began(_ note: Notification) {
            guard let menu = note.object as? NSMenu, self.menu == nil else { return }
            self.menu = menu
            let timer = Timer(
                timeInterval: 0.25, target: self, selector: #selector(read(_:)), userInfo: nil,
                repeats: false)
            // A menu's tracking runs the loop in a mode of its own, which the common modes include.
            RunLoop.main.add(timer, forMode: .common)
        }

        @objc func read(_ timer: Timer) {
            guard let menu else { return }
            titles = menu.items.filter { !$0.isSeparatorItem && !$0.isHidden }.map(\.title)
            let item = choose.flatMap { title in menu.items.first { $0.title == title && $0.isEnabled } }
            menu.cancelTrackingWithoutAnimation()
            if let item {
                chosen = true
                menu.performActionForItem(at: menu.index(of: item))
            }
        }
    }

    /// Hands a mouse event to the view under it. A window that is not the key window keeps
    /// a left press for itself, to become key, unless the view takes a first press, and a
    /// check build's window is not always key; so a left press goes to the view the window
    /// would have chosen. A right press goes through the window, which opens the view's menu.
    private func deliver(_ event: NSEvent, in window: NSWindow) {
        let frame = window.contentView?.superview ?? window.contentView
        guard event.type != .rightMouseDown, event.type != .rightMouseUp,
            let view = frame?.hitTest(event.locationInWindow)
        else {
            window.sendEvent(event)
            return
        }
        switch event.type {
        case .leftMouseDown: view.mouseDown(with: event)
        case .leftMouseUp: view.mouseUp(with: event)
        case .mouseMoved: view.mouseMoved(with: event)
        default: window.sendEvent(event)
        }
    }

    private func mouse(_ request: [String: Any]) throws -> Any {
        guard let x = (request["x"] as? NSNumber)?.doubleValue,
            let y = (request["y"] as? NSNumber)?.doubleValue
        else { throw CheckFailure("mouse takes x and y, in the page's coordinates") }
        let window = try theWindow()
        let page = try thePage()
        let right = request["button"] as? String == "right"
        let types: [NSEvent.EventType]
        switch request["type"] as? String ?? "click" {
        case "click": types = right ? [.rightMouseDown, .rightMouseUp] : [.leftMouseDown, .leftMouseUp]
        case "down": types = [right ? .rightMouseDown : .leftMouseDown]
        case "up": types = [right ? .rightMouseUp : .leftMouseUp]
        case "move": types = [.mouseMoved]
        default: throw CheckFailure("a mouse request's type is click, down, up or move")
        }
        // The page's coordinates start at its top left corner and are in its own pixels,
        // which the page's zoom scales; the window's start at its bottom left.
        let zoom = page.pageZoom > 0 ? page.pageZoom : 1
        let inPage = NSPoint(x: x * zoom, y: page.isFlipped ? y * zoom : page.bounds.height - y * zoom)
        guard page.bounds.contains(inPage) else {
            throw CheckFailure("the point \(x), \(y) is outside the page, which is \(page.bounds.size)")
        }
        let point = page.convert(inPage, to: nil)
        let wantsMenu = request["menu"] as? Bool ?? false || request["choose"] != nil
        let watch = MenuWatch()
        watch.choose = request["choose"] as? String
        if wantsMenu {
            NotificationCenter.default.addObserver(
                watch, selector: #selector(MenuWatch.began(_:)),
                name: NSMenu.didBeginTrackingNotification, object: nil)
        }
        defer { NotificationCenter.default.removeObserver(watch) }
        for type in types {
            guard
                let event = NSEvent.mouseEvent(
                    with: type, location: point, modifierFlags: [],
                    timestamp: ProcessInfo.processInfo.systemUptime,
                    windowNumber: window.windowNumber, context: nil, eventNumber: 0,
                    clickCount: (request["count"] as? NSNumber)?.intValue ?? 1, pressure: 1)
            else { throw CheckFailure("no mouse event could be made") }
            deliver(event, in: window)
        }
        var answer: [String: Any] = ["events": types.count]
        if wantsMenu {
            // WebKit opens its menu after the page has answered, which is after the press
            // returns. The wait ends when a menu has been read, or at the limit, and then
            // goes on for a moment. It takes the app's events as the app's own loop does:
            // the window server sends the process events as a menu closes, each arrival
            // stops the run loop that is waiting so that the event is fetched, and where
            // nothing fetches events, as under `swift test`, that stop ends the loop the
            // process waits in and with it the process. This was read from the stop's caller,
            // `PushToCGEventQueue`, in a debugger.
            let limit = (request["wait"] as? NSNumber)?.doubleValue ?? 3
            func pump(until end: Date, while going: () -> Bool) {
                while going(), Date() < end {
                    let next = NSApp.nextEvent(
                        matching: .any, until: Date().addingTimeInterval(0.05), inMode: .default,
                        dequeue: true)
                    if let next { NSApp.sendEvent(next) }
                }
            }
            pump(until: Date().addingTimeInterval(limit)) { watch.titles == nil }
            if watch.titles != nil { pump(until: Date().addingTimeInterval(0.3)) { true } }
            answer["menu"] = watch.titles.map { $0 as Any } ?? NSNull()
            answer["chosen"] = watch.chosen
        }
        return answer
    }

    // MARK: snapshot

    /// A PNG of the window: its native parts drawn by `cacheDisplay` from the frame view,
    /// which holds the title bar and the content, and the page drawn over them from
    /// `WKWebView.takeSnapshot`, since a web view's content is drawn in another process and
    /// `cacheDisplay` leaves its place empty. `parts` chooses one of the two for measuring.
    private func snapshot(_ request: [String: Any]) async throws -> Any {
        guard let path = request["path"] as? String else { throw CheckFailure("snapshot takes a path") }
        let parts = request["parts"] as? [String] ?? ["native", "web"]
        let window = try theWindow()
        let page = try thePage()
        guard let frame = window.contentView?.superview ?? window.contentView else {
            throw CheckFailure("the window has no view")
        }
        guard let image = frame.bitmapImageRepForCachingDisplay(in: frame.bounds) else {
            throw CheckFailure("no bitmap could be made for the window")
        }
        if parts.contains("native") { frame.cacheDisplay(in: frame.bounds, to: image) }
        var place = page.convert(page.bounds, to: frame)
        if frame.isFlipped { place.origin.y = frame.bounds.height - place.maxY }
        if parts.contains("web") {
            let web: NSImage
            do {
                web = try await page.takeSnapshot(configuration: nil)
            } catch {
                throw CheckFailure("the page gave no snapshot: \(error.localizedDescription)")
            }
            guard let context = NSGraphicsContext(bitmapImageRep: image) else {
                throw CheckFailure("the bitmap could not be drawn into")
            }
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.current = context
            web.draw(in: place, from: .zero, operation: .copy, fraction: 1)
            context.flushGraphics()
            NSGraphicsContext.restoreGraphicsState()
        }
        guard let data = image.representation(using: .png, properties: [:]) else {
            throw CheckFailure("the snapshot could not be written as PNG")
        }
        do {
            try data.write(to: URL(fileURLWithPath: path), options: .atomic)
        } catch {
            throw CheckFailure("the snapshot could not be written to \(path): \(error.localizedDescription)")
        }
        // The scale from points to the image's pixels, and the page's place in the image
        // from its top left corner, so that a reader can find the native parts around it.
        let scale = Double(image.pixelsWide) / Double(frame.bounds.width)
        return [
            "path": path,
            "width": image.pixelsWide,
            "height": image.pixelsHigh,
            "scale": scale,
            "page": [
                "x": Double(place.minX) * scale,
                "y": Double(frame.bounds.height - place.maxY) * scale,
                "width": Double(place.width) * scale,
                "height": Double(place.height) * scale,
            ],
        ]
    }

    // MARK: state, window, quit

    private func state() throws -> Any {
        let window = try theWindow()
        let frame = window.frame
        let responder: Any = window.firstResponder.map { String(describing: type(of: $0)) } ?? NSNull()
        let frontmost: Any = NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? NSNull()
        let bundle: Any = Bundle.main.bundleIdentifier ?? NSNull()
        return [
            "title": window.title,
            "key": window.isKeyWindow,
            "main": window.isMainWindow,
            "visible": window.isVisible,
            "on_screen": window.occlusionState.contains(.visible),
            "first_responder": responder,
            "active": NSApp.isActive,
            "frontmost": frontmost,
            "bundle": bundle,
            "frame": [frame.minX, frame.minY, frame.width, frame.height],
        ]
    }

    /// Places the window (`frame`, as `[x, y, width, height]` in screen points), makes it the
    /// key window (`key`), makes the page its first responder (`focus`), or activates the
    /// app (`activate`), which brings it in front of the person's other apps and is asked
    /// for only by a run that says so. The answer is the state afterwards.
    private func place(_ request: [String: Any]) throws -> Any {
        let window = try theWindow()
        if let frame = request["frame"] as? [NSNumber], frame.count == 4 {
            window.setFrame(
                NSRect(
                    x: frame[0].doubleValue, y: frame[1].doubleValue, width: frame[2].doubleValue,
                    height: frame[3].doubleValue), display: true)
        }
        if request["activate"] as? Bool == true { NSApp.activate() }
        if request["key"] as? Bool == true { window.makeKey() }
        if request["focus"] as? Bool == true { window.makeFirstResponder(try thePage()) }
        return try state()
    }
}
#endif
