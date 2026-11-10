import Foundation
import KyoseiClient
import Observation
import WebKit

/// A subagent still at work, as the status bar lists it.
struct RunningAgent: Identifiable, Equatable {
    let id: Int
    let name: String
    let type: String
    let since: Date?
    /// Its tokens so far, in words, or empty before the server has been asked.
    var tokens = ""

    /// The record a dispatched worker is on, read from its description.
    var record: String? {
        name.firstMatch(of: /[A-Z][A-Z0-9]*-\d+/).map { String($0.output) }
    }
}

/// Reads one session from `kyo serve` and hands each response to the web view unchanged.
@MainActor
@Observable
final class SessionFeed {
    private static let menuRefresh = Duration.seconds(5)

    private var client: ServeClient
    private var selected: String?
    private var current: String?
    private var task: Task<Void, Never>?
    private var menuTask: Task<Void, Never>?
    @ObservationIgnored weak var webView: WKWebView?

    private(set) var project: String
    private(set) var status = "connecting"
    private(set) var facts: SessionFacts?
    private(set) var remote: String?
    /// Commits on the branch that its upstream lacks; nil without an upstream (KYO-717).
    private(set) var ahead: Int?
    var runningSubagents = 0
    var runningAgents: [RunningAgent] = []
    private var draftSession: String?
    private var draftEtag: String?
    private var draftTask: Task<Void, Never>?
    private var startWatch: Task<Void, Never>?
    /// Whether the terminal drawer is open, and the size of its view once the page has
    /// measured one (KYO-721).
    private var terminalOpen = false
    private var terminalSize: (cols: Int, rows: Int)?
    /// The session whose terminal was last asked for, so that it is asked for once.
    private var terminalSession: String?
    private var terminalTask: Task<Void, Never>?
    /// Read when the drawer opens, since reading it starts a process and a key must not.
    private var terminalToken: String?
    /// The open stream's connection, which an input or a resize names.
    private var terminalConnection: String?
    private var typed = ""
    private var typing = false
    private var resizing: Task<Void, Never>?
    private var currentLive = false

    init(client: ServeClient, project: String) {
        self.client = client
        self.project = project
    }

    private static let serverKey = "serverURL"
    private static let projectKey = "project"
    private static let localServer = "http://127.0.0.1:47370"
    private static let tokenService = "dev.kyosei.device-token"
    private static var environment: [String: String] { ProcessInfo.processInfo.environment }

    static var savedServer: String {
        environment["KYOSEI_SERVE_URL"] ?? UserDefaults.standard.string(forKey: serverKey)
            ?? localServer
    }

    static var savedProject: String {
        environment["KYOSEI_PROJECT"] ?? UserDefaults.standard.string(forKey: projectKey)
            ?? "kyosei"
    }

    static func fromEnvironment() -> SessionFeed {
        SessionFeed(client: ServeClient(base: URL(string: savedServer)!), project: savedProject)
    }

    /// Points this window at another server or project, and reads it afresh.
    func configure(server: URL, project: String) {
        UserDefaults.standard.set(server.absoluteString, forKey: Self.serverKey)
        UserDefaults.standard.set(project, forKey: Self.projectKey)
        task?.cancel()
        menuTask?.cancel()
        startWatch?.cancel()
        task = nil
        menuTask = nil
        startWatch = nil
        closeTerminal()
        terminalSession = nil
        client = ServeClient(base: server)
        self.project = project
        selected = nil
        current = nil
        currentLive = false
        draftSession = nil
        draftEtag = nil
        facts = nil
        remote = nil
        ahead = nil
        status = "connecting"
        send("clear", "null")
        start()
    }

    /// Stores the device token through the system's own `security` tool.
    static func storeDeviceToken(_ token: String) -> Bool {
        // A token saved under another account name, as one added by hand is, would be found
        // first and sent in place of this one. Every item for the service goes before the add.
        for _ in 0..<8 {
            let old = Process()
            old.executableURL = URL(fileURLWithPath: "/usr/bin/security")
            old.arguments = ["delete-generic-password", "-s", tokenService]
            old.standardOutput = Pipe()
            old.standardError = Pipe()
            guard (try? old.run()) != nil else { break }
            old.waitUntilExit()
            if old.terminationStatus != 0 { break }
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        process.arguments = [
            "add-generic-password", "-U", "-s", tokenService, "-a", "device", "-w", token,
        ]
        guard (try? process.run()) != nil else { return false }
        process.waitUntilExit()
        return process.terminationStatus == 0
    }

    func start() {
        guard task == nil else { return }
        task = Task { await run() }
        menuTask = Task {
            while !Task.isCancelled {
                await sendMenu()
                try? await Task.sleep(for: Self.menuRefresh)
            }
        }
    }

    /// Show another session of this window's project. A window stays on one project, so that
    /// one project's session is never opened from another's.
    func select(session: String) {
        selected = session
        facts = nil
        status = "connecting"
        task?.cancel()
        task = Task { await run() }
    }

    /// The device token, read through the system's own `security` tool, which created it.
    private static func deviceToken() -> String? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        process.arguments = [
            "find-generic-password", "-s", tokenService, "-w",
        ]
        let output = Pipe()
        process.standardOutput = output
        process.standardError = Pipe()
        guard (try? process.run()) != nil else { return nil }
        process.waitUntilExit()
        guard process.terminationStatus == 0 else { return nil }
        let text = String(decoding: output.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func sessionPath(_ session: String, _ route: String) -> String {
        "v1/projects/\(project)/sessions/\(session)/\(route)"
    }

    private func refusal(_ data: Data) -> String {
        let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        return object?["error"] as? String ?? "The server refused that."
    }

    /// Hands a message to the server, which holds it until the turn has ended. A prompt with
    /// marks goes by the route that puts each mark's anchors above its paragraph and files it.
    func sendMessage(body: [String: Any]) {
        let marked = !((body["marks"] as? [Any]) ?? []).isEmpty
        var body = body
        if !marked { body["marks"] = nil }
        guard let session = current, let token = Self.deviceToken(),
            let data = try? JSONSerialization.data(withJSONObject: body)
        else {
            send("sent", #"{"error": "No session or device token to send with."}"#)
            return
        }
        Task {
            guard
                let reply = try? await client.authorised(
                    "POST", sessionPath(session, marked ? "send-prompt" : "held"), token: token,
                    body: data)
            else {
                send("sent", #"{"error": "The server could not be reached."}"#)
                return
            }
            let object = try? JSONSerialization.jsonObject(with: reply.data) as? [String: Any]
            if reply.status == 200 || reply.status == 201 {
                let held = object?["held"] as? [String: Any] ?? object
                send("sent", #"{"id": \#(quoted(held?["id"] as? String))}"#)
            } else {
                send("sent", #"{"error": \#(quoted(refusal(reply.data)))}"#)
            }
        }
    }

    /// Reads what the store holds of this session's sent prompts: the places their paragraphs
    /// were anchored to, which the chat keeps showing, and the tags in use, which the tag
    /// field suggests. A token without the `notes` permission is refused, and nothing is shown.
    private func loadMarks(session: String, after delay: Duration = .zero) {
        guard let token = Self.deviceToken() else { return }
        Task {
            try? await Task.sleep(for: delay)
            guard session == current else { return }
            if let reply = try? await client.authorised(
                "GET", sessionPath(session, "marks"), token: token), reply.status == 200
            {
                send("marks", String(decoding: reply.data, as: UTF8.self))
            }
            if let reply = try? await client.authorised(
                "GET", "v1/projects/\(project)/tags", token: token), reply.status == 200
            {
                send("tags", String(decoding: reply.data, as: UTF8.self))
            }
        }
    }

    /// Brings a session's saved draft into the composer, once for each session opened.
    private func loadDraft(session: String) {
        guard draftSession != session, let token = Self.deviceToken() else { return }
        draftSession = session
        draftEtag = nil
        loadMarks(session: session)
        Task {
            guard
                let reply = try? await client.authorised(
                    "GET", sessionPath(session, "draft"), token: token),
                reply.status == 200, session == current
            else { return }
            draftEtag = etag(in: reply.data)
            send("draft", String(decoding: reply.data, as: UTF8.self))
        }
    }

    private func etag(in data: Data) -> String? {
        (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["etag"] as? String
    }

    /// Replaces the session's draft. Saves run one at a time, so each uses the last etag.
    func saveDraft(body: [String: Any]) {
        guard let session = current, session == draftSession, let token = Self.deviceToken(),
            let data = try? JSONSerialization.data(withJSONObject: body)
        else { return }
        let previous = draftTask
        draftTask = Task {
            await previous?.value
            let etag = draftEtag
            guard
                let reply = try? await client.authorised(
                    "PUT", sessionPath(session, "draft"), token: token, body: data, ifMatch: etag,
                    ifNoneMatch: etag == nil ? "*" : nil)
            else { return }
            if reply.status == 200 || reply.status == 201 {
                draftEtag = self.etag(in: reply.data)
            } else if reply.status == 412 {
                // Another device saved first. Keep this text, and let the next edit replace it.
                draftEtag = self.etag(in: reply.data)
                send("note", #"{"text": "This draft also changed on another device."}"#)
            }
        }
    }

    /// The file types the server keeps as they are. Anything else an image can be made from,
    /// such as HEIC from an Apple camera, is sent as JPEG.
    private static let keptTypes: Set<String> = [
        "image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf",
    ]

    private static let largeImageBytes = 3 * 1024 * 1024
    private static let longestEdge = 4096

    /// An image as a JPEG no longer than `longestEdge` on a side, turned the right way up.
    private static func jpeg(from data: Data) -> Data? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: longestEdge,
        ]
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
        else { return nil }
        let output = NSMutableData()
        guard
            let destination = CGImageDestinationCreateWithData(
                output, "public.jpeg" as CFString, 1, nil)
        else { return nil }
        CGImageDestinationAddImage(
            destination, image,
            [kCGImageDestinationLossyCompressionQuality: 0.85] as CFDictionary)
        return CGImageDestinationFinalize(destination) ? output as Data : nil
    }

    /// Uploads a file pasted into a prompt to the machine the session runs on, and tells the
    /// composer the id and path the server gave it.
    func attach(body: [String: Any]) {
        let ref = body["ref"] as? String ?? ""
        func fail(_ why: String) {
            send("attached", #"{"ref": \#(quoted(ref)), "error": \#(quoted(why))}"#)
        }
        guard let session = current, let token = Self.deviceToken() else {
            return fail("No session or device token to attach with.")
        }
        guard var data = Data(base64Encoded: body["data"] as? String ?? ""), !data.isEmpty else {
            return fail("That file could not be read.")
        }
        var type = body["type"] as? String ?? ""
        // A photo pasted from the clipboard arrives as a PNG many times the size of the file
        // it came from, so a large image is sent as JPEG, as is any type the server does not keep.
        let large = type.hasPrefix("image/") && data.count > Self.largeImageBytes
        if !Self.keptTypes.contains(type) || large {
            guard let jpeg = Self.jpeg(from: data) else {
                return fail("That kind of file cannot be attached.")
            }
            data = jpeg
            type = "image/jpeg"
        }
        Task {
            guard
                let reply = try? await client.authorised(
                    "POST", sessionPath(session, "attachments"), token: token, body: data,
                    contentType: type)
            else { return fail("The server could not be reached.") }
            let object = try? JSONSerialization.jsonObject(with: reply.data) as? [String: Any]
            let file = object?["attachment"] as? [String: Any] ?? object
            guard reply.status == 200 || reply.status == 201, let id = file?["id"] as? String else {
                return fail(refusal(reply.data))
            }
            send(
                "attached",
                #"{"ref": \#(quoted(ref)), "id": \#(quoted(id)), "path": \#(quoted(file?["path"] as? String))}"#)
        }
    }

    /// Takes a held message back, returning its text to the composer.
    func retract(id: String) {
        guard let session = current, let token = Self.deviceToken() else { return }
        Task {
            guard
                let reply = try? await client.authorised(
                    "DELETE", sessionPath(session, "held"), token: token, ifMatch: id),
                reply.status == 200
            else {
                send("note", #"{"text": "It could not be taken back; it may have been sent."}"#)
                return
            }
            let object = try? JSONSerialization.jsonObject(with: reply.data) as? [String: Any]
            let held = object?["held"] as? [String: Any] ?? object ?? [:]
            // A prompt sent with marks was delivered with lines added above its paragraphs,
            // so what returns to the composer is the prompt as it was typed.
            let typed = (held["prompt"] as? [String: Any])?["segments"] as? [[String: Any]]
            let segments = typed ?? held["segments"] as? [[String: Any]] ?? []
            let text = segments.compactMap { $0["text"] as? String }.joined()
            send("retracted", #"{"text": \#(quoted(text))}"#)
        }
    }

    /// Delivers the held message during the turn (KYO-722). Claude Code queues it and gives it to
    /// the agent at its next tool call. The server's `held` event then clears the held box; a
    /// refusal is said in words and the message stays held.
    func sendNow(id: String) {
        guard let session = current, let token = Self.deviceToken() else { return }
        Task {
            guard
                let reply = try? await client.authorised(
                    "POST", sessionPath(session, "held-now"), token: token,
                    body: Data("{}".utf8), ifMatch: id)
            else {
                send("note", #"{"text": "The server could not be reached."}"#)
                return
            }
            if reply.status != 200 {
                send("note", #"{"text": \#(quoted(refusal(reply.data)))}"#)
            }
        }
    }

    func interrupt() {
        guard let session = current, let token = Self.deviceToken() else { return }
        Task {
            guard
                let reply = try? await client.authorised(
                    "POST", sessionPath(session, "interrupt"), token: token, body: Data("{}".utf8)),
                reply.status == 200
            else {
                send("note", #"{"text": "The interrupt was refused."}"#)
                return
            }
        }
    }

    // MARK: The terminal drawer (KYO-721)

    private static let terminalTokenHelp =
        "The terminal needs a device token holding the terminal permission. Issue one on the "
        + "server's machine with `kyo devices issue <name> --permission terminal`, repeating "
        + "--permission for send, notes and start, and enter it in Settings."
    /// How much one input request carries. The server takes 65536 bytes, and a scalar is at
    /// most four.
    private static let inputScalars = 8000

    /// What the page's terminal drawer asks: to open or close the stream, to type, or to
    /// state the size of its view.
    func terminal(body: [String: Any]) {
        if let cols = body["cols"] as? Int, let rows = body["rows"] as? Int {
            terminalSize = (cols, rows)
        }
        switch body["action"] as? String {
        case "open":
            terminalOpen = true
            terminalToken = Self.deviceToken()
            openTerminal()
        case "close":
            terminalOpen = false
            terminalSession = nil
            closeTerminal()
        case "input":
            if let data = body["data"] as? String { type(data) }
        case "resize":
            resizeTerminal()
        default:
            break
        }
    }

    private func terminalFrame(_ name: String, _ event: String) {
        send("terminalEvent", #"{"name": \#(quoted(name)), "event": \#(event)}"#)
    }

    private func closeTerminal() {
        terminalTask?.cancel()
        terminalTask = nil
        terminalConnection = nil
        typed = ""
    }

    /// Follows the current session's terminal while the drawer is open. A session with no
    /// live process has none, and the page then shows the pane's last text.
    private func openTerminal() {
        closeTerminal()
        guard terminalOpen else { return }
        terminalSession = current
        guard let session = current, currentLive else {
            terminalFrame("refused", #"{"error": "This session has no terminal to show."}"#)
            return
        }
        guard let token = terminalToken else {
            terminalFrame("refused", #"{"error": \#(quoted(Self.terminalTokenHelp))}"#)
            return
        }
        terminalTask = Task { await followTerminal(session: session, token: token) }
    }

    private func followTerminal(session: String, token: String) async {
        while !Task.isCancelled {
            var components = URLComponents(
                url: client.base.appending(path: sessionPath(session, "terminal")),
                resolvingAgainstBaseURL: false)!
            if let size = terminalSize {
                components.queryItems = [
                    URLQueryItem(name: "cols", value: String(size.cols)),
                    URLQueryItem(name: "rows", value: String(size.rows)),
                ]
            }
            var request = URLRequest(url: components.url!)
            request.timeoutInterval = 120
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            do {
                for try await event in Self.terminalEvents(request) {
                    if event.name == "ready" {
                        let object =
                            try? JSONSerialization.jsonObject(with: Data(event.data.utf8))
                            as? [String: Any]
                        terminalConnection = object?["connection"] as? String
                    }
                    terminalFrame(event.name, event.data)
                    if event.name == "closed" {
                        terminalConnection = nil
                        return
                    }
                }
            } catch ServeError.status(_, let body) {
                // A refusal is the server's own sentence, which names the remedy. It is not
                // asked again until the drawer is opened again.
                terminalConnection = nil
                terminalFrame("refused", #"{"error": \#(quoted(refusal(Data(body.utf8))))}"#)
                return
            } catch {
                if Task.isCancelled { return }
            }
            terminalConnection = nil
            guard !Task.isCancelled else { return }
            terminalFrame(
                "note", #"{"text": "The terminal's connection was lost. Connecting again."}"#)
            try? await Task.sleep(for: .seconds(1))
        }
    }

    /// The terminal stream's events. It carries the device token, which the session's event
    /// stream does not, and a refusal's body, which says why.
    private nonisolated static func terminalEvents(
        _ request: URLRequest
    ) -> AsyncThrowingStream<ServerEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                    guard code == 200 else {
                        var body: [UInt8] = []
                        for try await byte in bytes where body.count < 65536 { body.append(byte) }
                        throw ServeError.status(code, String(decoding: body, as: UTF8.self))
                    }
                    var parser = ServerEventParser()
                    var line: [UInt8] = []
                    for try await byte in bytes {
                        guard byte == UInt8(ascii: "\n") else {
                            line.append(byte)
                            continue
                        }
                        if let event = parser.feed(line: String(decoding: line, as: UTF8.self)) {
                            continuation.yield(event)
                        }
                        line.removeAll(keepingCapacity: true)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func terminalPost(_ route: String, _ body: [String: Any]) async -> String? {
        guard let session = terminalSession, let connection = terminalConnection,
            let token = terminalToken
        else { return "The terminal is not connected." }
        var body = body
        body["connection"] = connection
        guard let data = try? JSONSerialization.data(withJSONObject: body),
            let reply = try? await client.authorised(
                "POST", sessionPath(session, "terminal/\(route)"), token: token, body: data)
        else { return "The server could not be reached." }
        return reply.status == 200 ? nil : refusal(reply.data)
    }

    /// Sends what was typed, one request at a time and in order. Keys typed while a request
    /// is out go together in the next.
    private func type(_ data: String) {
        typed += data
        guard !typing else { return }
        typing = true
        Task {
            while !typed.isEmpty {
                let part = String(typed.unicodeScalars.prefix(Self.inputScalars))
                typed = String(typed.unicodeScalars.dropFirst(part.unicodeScalars.count))
                if let problem = await terminalPost("input", ["data": part]) {
                    typed = ""
                    terminalFrame("note", #"{"text": \#(quoted(problem))}"#)
                }
            }
            typing = false
        }
    }

    /// States the view's size. The requests run one after another, so the last one stands.
    private func resizeTerminal() {
        let previous = resizing
        resizing = Task {
            await previous?.value
            guard let size = terminalSize, terminalConnection != nil else { return }
            _ = await terminalPost("resize", ["cols": size.cols, "rows": size.rows])
        }
    }

    private static let startWatchPoll = Duration.seconds(2)
    private static let startWatchLimit = Duration.seconds(600)

    /// Asks the server to start this project's orchestrator session (KYO-719), and tells the
    /// page what came of it. The page puts the reply into words; a refusal is the server's
    /// own sentence, which names the remedy.
    func startOrchestrator() {
        guard let token = Self.deviceToken() else {
            let why =
                "Starting a session needs a device token holding the start permission. Issue "
                + "one on the server's machine with `kyo devices issue <name> --permission "
                + "start`, repeating --permission for send and notes, and enter it in Settings."
            send("start", #"{"phase": "failed", "text": \#(quoted(why))}"#)
            return
        }
        send("start", #"{"phase": "starting"}"#)
        let project = project
        Task {
            guard
                let reply = try? await client.authorised(
                    "POST", "v1/projects/\(project)/orchestrator/start", token: token,
                    body: Data("{}".utf8))
            else {
                send("start", #"{"phase": "failed", "text": "The server could not be reached."}"#)
                return
            }
            guard project == self.project else { return }
            let object = try? JSONSerialization.jsonObject(with: reply.data) as? [String: Any]
            let document = object == nil ? "null" : String(decoding: reply.data, as: UTF8.self)
            send(
                "start",
                #"{"phase": "answered", "status": \#(reply.status), "reply": \#(document)}"#)
            // A session already running may not be listed yet, so it is watched for as well.
            let running = object?["reason"] as? String == "already-running"
            if reply.status == 200 || running { followStartedSession() }
        }
    }

    /// Shows the project's live session once the server lists one. A session is listed when
    /// Claude Code has begun in it, which waits on the trust prompt when there is one, so the
    /// watch is long and ends by itself.
    private func followStartedSession() {
        startWatch?.cancel()
        let project = project
        startWatch = Task {
            let deadline = ContinuousClock.now + Self.startWatchLimit
            while ContinuousClock.now < deadline {
                try? await Task.sleep(for: Self.startWatchPoll)
                guard !Task.isCancelled, project == self.project else { return }
                guard let rows = try? await client.sessions(project: project),
                    let row = rows.first(where: { $0.live && $0.has_pane })
                        ?? rows.first(where: { $0.live })
                else { continue }
                guard !Task.isCancelled else { return }
                if current != row.session_id { select(session: row.session_id) }
                await sendMenu()
                return
            }
            // The button is given back, so that a session that never began can be started.
            let why =
                "The session was started, but the server has not listed it after ten minutes. "
                + "`kyo orchestrator list` on the server's machine says what is running."
            send("start", #"{"phase": "failed", "text": \#(quoted(why))}"#)
        }
    }

    /// Fetches one of the person's images for the page, which may load only its own files.
    func loadImage(turn: String, index: Int) {
        guard let session = current else { return }
        Task {
            guard
                let image = try? await client.image(
                    project: project, session: session, turn: turn, index: index),
                session == current
            else { return }
            let data = "data:\(image.mediaType);base64,\(image.data.base64EncodedString())"
            let key = "\(turn)/\(index)"
            send("image", #"{"key": \#(quoted(key)), "data": \#(quoted(data))}"#)
        }
    }

    /// A sent attachment's image, by its id, for the prompt it was sent in (KYO-720).
    func loadAttachment(_ id: String) {
        guard let session = current, let token = Self.deviceToken() else { return }
        Task {
            guard
                let reply = try? await client.authorised(
                    "GET", sessionPath(session, "attachments/\(id)"), token: token),
                reply.status == 200
            else {
                send("image", #"{"key": \#(quoted("a:" + id)), "data": null}"#)
                return
            }
            guard session == current else { return }
            let bytes = [UInt8](reply.data.prefix(4))
            let type =
                bytes.starts(with: [0x89, 0x50]) ? "image/png"
                : bytes.starts(with: [0xFF, 0xD8]) ? "image/jpeg"
                : bytes.starts(with: [0x47, 0x49]) ? "image/gif" : "image/webp"
            let data = "data:\(type);base64,\(reply.data.base64EncodedString())"
            send("image", #"{"key": \#(quoted("a:" + id)), "data": \#(quoted(data))}"#)
        }
    }

    private func send(_ function: String, _ json: String) {
        webView?.evaluateJavaScript("window.kyo.\(function)(\(json))") { _, error in
            if let error { print("[feed] \(function): \(error)") }
        }
    }

    private func quoted(_ text: String?) -> String {
        guard let text, let data = try? JSONEncoder().encode(text) else { return "null" }
        return String(decoding: data, as: UTF8.self)
    }

    private func sendMenu() async {
        guard let sessions = try? await client.sessionsData(project: project) else { return }
        var changes: Data?
        if let current { changes = try? await client.changes(project: project, session: current) }
        takeRemote(from: changes)
        let json =
            #"{"project": \#(quoted(project)), "current": \#(quoted(current)), "changes": "#
            + (changes.map { String(decoding: $0, as: UTF8.self) } ?? "null")
            + #", "sessions": "# + String(decoding: sessions, as: UTF8.self) + "}"
        send("menu", json)
    }

    /// Fetches one subagent's tool calls for the panel.
    func loadAgent(_ agent: String) {
        guard let session = current else { return }
        Task {
            guard
                let data = try? await client.subagent(
                    project: project, session: session, agent: agent),
                session == current
            else { return }
            send("agent", String(decoding: data, as: UTF8.self))
        }
    }

    /// The remote's name, or "No Remote" for a repository the server says has none.
    private func takeRemote(from json: Data?) {
        guard let json,
            let object = try? JSONSerialization.jsonObject(with: json) as? [String: Any],
            object.keys.contains("remote"), object["root"] is String
        else { return }
        remote = object["remote"] as? String ?? "No Remote"
        ahead = object["ahead"] as? Int
    }

    private func takeFacts(from json: Data) {
        if let carried = try? JSONDecoder().decode(FactsCarrier.self, from: json).facts {
            facts = carried
        }
    }

    private func run() async {
        while !Task.isCancelled {
            do {
                let sessions = try await client.sessions(project: project)
                guard
                    let row = sessions.first(where: { $0.session_id == selected })
                        ?? sessions.first(where: { $0.live && $0.has_pane }) ?? sessions.first
                else {
                    current = nil
                    currentLive = false
                    if terminalOpen, terminalSession != nil { openTerminal() }
                    send("clear", "null")
                    status = "no session"
                    try await Task.sleep(for: .seconds(3))
                    continue
                }
                let turns = try await client.turns(project: project, session: row.session_id)
                try Task.checkCancellation()
                current = row.session_id
                currentLive = row.live
                send("clear", "null")
                send("apply", String(decoding: turns, as: UTF8.self))
                if terminalOpen, terminalSession != row.session_id { openTerminal() }
                takeFacts(from: turns)
                loadDraft(session: row.session_id)
                status = "connected"
                await sendMenu()
                guard row.live else {
                    // An ended session has nothing to follow.
                    send("screen", #"{"screen": null}"#)
                    send("terminal", #"{"terminal": null}"#)
                    try await Task.sleep(for: .seconds(3600))
                    continue
                }
                try await follow(session: row.session_id, turns: turns)
            } catch is CancellationError {
                return
            } catch {
                if Task.isCancelled { return }
                status = "disconnected"
                print("[feed] \(error)")
            }
            try? await Task.sleep(for: .seconds(1))
        }
    }

    private func follow(session: String, turns: Data) async throws {
        var cursor =
            (try? JSONSerialization.jsonObject(with: turns) as? [String: Any])?["cursor"] as? String
        for try await event in client.events(project: project, session: session, cursor: cursor) {
            switch event.name {
            case "transcript":
                send("apply", event.data)
                cursor = event.id ?? cursor
            case "reply":
                send("live", event.data)
            case "ready":
                send("ready", event.data)
                takeFacts(from: Data(event.data.utf8))
            case "facts":
                send("facts", event.data)
                takeFacts(from: Data(event.data.utf8))
            case "screen":
                send("screen", event.data)
            case "terminal":
                send("terminal", event.data)
            case "held":
                send("held", event.data)
                // A delivered prompt's paragraphs are filed just after its keys are sent.
                if event.data.contains("delivered"), let session = current {
                    loadMarks(session: session, after: .seconds(2))
                }
            case "reset", "session":
                return  // Read the session again from the top.
            default:
                break
            }
        }
    }
}
