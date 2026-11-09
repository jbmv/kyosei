import Foundation
import KyoseiClient
import Observation
import WebKit

/// Reads one session from `kyo serve` and hands each response to the web view unchanged.
@MainActor
@Observable
final class SessionFeed {
    private static let menuRefresh = Duration.seconds(5)

    private let client: ServeClient
    private var selected: String?
    private var current: String?
    private var task: Task<Void, Never>?
    private var menuTask: Task<Void, Never>?
    @ObservationIgnored weak var webView: WKWebView?

    let project: String
    private(set) var status = "connecting"
    private(set) var facts: SessionFacts?
    private(set) var remote: String?
    var runningSubagents = 0
    private var draftSession: String?
    private var draftEtag: String?
    private var draftTask: Task<Void, Never>?

    init(client: ServeClient, project: String) {
        self.client = client
        self.project = project
    }

    static func fromEnvironment() -> SessionFeed {
        let environment = ProcessInfo.processInfo.environment
        let base = URL(string: environment["KYOSEI_SERVE_URL"] ?? "http://127.0.0.1:47370")!
        return SessionFeed(
            client: ServeClient(base: base), project: environment["KYOSEI_PROJECT"] ?? "kyosei")
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
            "find-generic-password", "-s", "dev.kyosei.device-token", "-a", "studio-mac", "-w",
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

    /// Hands a message to the server, which holds it until the turn has ended.
    func sendMessage(body: [String: Any]) {
        guard let session = current, let token = Self.deviceToken(),
            let data = try? JSONSerialization.data(withJSONObject: body)
        else {
            send("sent", #"{"error": "No session or device token to send with."}"#)
            return
        }
        Task {
            guard
                let reply = try? await client.authorised(
                    "POST", sessionPath(session, "held"), token: token, body: data)
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

    /// Brings a session's saved draft into the composer, once for each session opened.
    private func loadDraft(session: String) {
        guard draftSession != session, let token = Self.deviceToken() else { return }
        draftSession = session
        draftEtag = nil
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
            let segments = held["segments"] as? [[String: Any]] ?? []
            let text = segments.compactMap { $0["text"] as? String }.joined()
            send("retracted", #"{"text": \#(quoted(text))}"#)
        }
    }

    /// A slash command goes straight to the session, which takes one only when idle.
    func slash(command: String) {
        guard let session = current, let token = Self.deviceToken(),
            let data = try? JSONSerialization.data(withJSONObject: ["command": command])
        else { return }
        Task {
            guard
                let reply = try? await client.authorised(
                    "POST", sessionPath(session, "slash"), token: token, body: data)
            else {
                send("sent", #"{"error": "The server could not be reached."}"#)
                return
            }
            if reply.status == 200 {
                send("sent", "{}")
            } else {
                send("sent", #"{"error": \#(quoted(refusal(reply.data)))}"#)
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
                    send("clear", "null")
                    status = "no session"
                    try await Task.sleep(for: .seconds(3))
                    continue
                }
                let turns = try await client.turns(project: project, session: row.session_id)
                try Task.checkCancellation()
                current = row.session_id
                send("clear", "null")
                send("apply", String(decoding: turns, as: UTF8.self))
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
            case "reset", "session":
                return  // Read the session again from the top.
            default:
                break
            }
        }
    }
}
