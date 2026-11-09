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
            case "reset", "session":
                return  // Read the session again from the top.
            default:
                break
            }
        }
    }
}
