import Foundation
import KyoseiClient
import Observation
import WebKit

/// Reads one session from `kyo serve` and hands each response to the web view unchanged.
@MainActor
@Observable
final class SessionFeed {
    private let client: ServeClient
    private let project: String
    private var task: Task<Void, Never>?
    @ObservationIgnored weak var webView: WKWebView?

    private(set) var status = "connecting"
    private(set) var facts: SessionFacts?
    let projectName: String

    private func takeFacts(from json: Data) {
        if let carried = try? JSONDecoder().decode(FactsCarrier.self, from: json).facts {
            facts = carried
        }
    }

    init(client: ServeClient, project: String) {
        self.client = client
        self.project = project
        self.projectName = project
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
    }

    private func send(_ function: String, _ json: String) {
        webView?.evaluateJavaScript("window.kyo.\(function)(\(json))") { _, error in
            if let error { print("[feed] \(function): \(error)") }
        }
    }

    private func run() async {
        while !Task.isCancelled {
            do {
                let sessions = try await client.sessions(project: project)
                guard let row = sessions.first(where: { $0.live && $0.has_pane }) ?? sessions.first
                else {
                    status = "no session"
                    try await Task.sleep(for: .seconds(3))
                    continue
                }
                let turns = try await client.turns(project: project, session: row.session_id)
                send("clear", "null")
                send("apply", String(decoding: turns, as: UTF8.self))
                takeFacts(from: turns)
                status = "connected"
                try await follow(session: row.session_id, turns: turns)
            } catch is CancellationError {
                return
            } catch {
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
                takeFacts(from: Data(event.data.utf8))
            case "screen":
                send("screen", event.data)
            case "reset", "session":
                return  // Read the session again from the top.
            default:
                break
            }
        }
    }
}
