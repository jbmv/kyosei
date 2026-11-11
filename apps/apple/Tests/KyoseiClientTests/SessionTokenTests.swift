import Foundation
import Network
import Testing

@testable import KyoseiClient

// KYO-791: `kyo serve` answers a session read only to a device token. These tests stand a
// server on loopback that does the same, and read what each of the client's session reads
// sent it.

/// A stand-in for `kyo serve` that records each request's path and `Authorization` header,
/// and refuses a session read that carries none as the server does.
private final class TokenServe: @unchecked Sendable {
    struct Request: Equatable {
        let path: String
        let authorization: String?
    }

    private let queue = DispatchQueue(label: "token-serve")
    private let lock = NSLock()
    private var seen: [Request] = []
    private var listener: NWListener?
    private var port: NWEndpoint.Port?
    /// The `reason` of the refusal a request without an accepted token is given.
    private let reason: String
    private let accepted: String?

    init(accepting token: String?, reason: String = "no-token") {
        accepted = token.map { "Bearer \($0)" }
        self.reason = reason
    }

    var requests: [Request] { lock.withLock { seen } }
    var base: URL { URL(string: "http://127.0.0.1:\(lock.withLock { port!.rawValue })")! }

    func start() async throws {
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: .any)
        let listener = try NWListener(using: parameters)
        listener.newConnectionHandler = { [self] connection in
            connection.start(queue: queue)
            receive(connection, Data())
        }
        let resumed = Once()
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            listener.stateUpdateHandler = { state in
                switch state {
                case .ready, .failed, .cancelled: resumed.run { continuation.resume() }
                default: break
                }
            }
            listener.start(queue: queue)
        }
        lock.withLock {
            self.listener = listener
            port = listener.port
        }
    }

    func stop() {
        lock.withLock { listener }?.cancel()
    }

    private func receive(_ connection: NWConnection, _ buffer: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) {
            [self] data, _, complete, error in
            var buffer = buffer
            if let data { buffer.append(data) }
            let text = String(decoding: buffer, as: UTF8.self)
            guard let end = text.range(of: "\r\n\r\n") else {
                if !complete && error == nil { receive(connection, buffer) }
                return
            }
            let lines = text[..<end.lowerBound].components(separatedBy: "\r\n")
            let target = String(lines[0].split(separator: " ").dropFirst().first ?? "")
            let path = String(target.split(separator: "?").first ?? "")
            let authorization = lines.dropFirst().first {
                $0.lowercased().hasPrefix("authorization:")
            }.map { $0.dropFirst("authorization:".count).trimmingCharacters(in: .whitespaces) }
            lock.withLock { seen.append(Request(path: path, authorization: authorization)) }
            answer(connection, path, admitted: accepted != nil && authorization == accepted)
        }
    }

    private func answer(_ connection: NWConnection, _ path: String, admitted: Bool) {
        var head = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        var body = #"{"ok": true, "sessions": []}"#
        if !admitted {
            head =
                "HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json\r\n"
                + "WWW-Authenticate: Bearer\r\n"
            body = #"{"ok": false, "error": "the server's own sentence", "reason": "\#(reason)"}"#
        } else if path.hasSuffix("/events") {
            head = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream; charset=utf-8\r\n"
            body = "event: ready\ndata: {}\n\n"
        }
        let text = head + "Content-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n" + body
        connection.send(
            content: Data(text.utf8), contentContext: .finalMessage, isComplete: true,
            completion: .contentProcessed { _ in connection.cancel() })
    }
}

private final class Once: @unchecked Sendable {
    private let lock = NSLock()
    private var done = false

    func run(_ body: () -> Void) {
        let first = lock.withLock {
            defer { done = true }
            return !done
        }
        if first { body() }
    }
}

private let sessions = "/v1/projects/p/sessions"

/// Every session read the client has, each made once. The paths are in the order made.
private let everyRead = [
    sessions,
    sessions,
    "\(sessions)/s/subagents/a1",
    "\(sessions)/s/changes",
    "\(sessions)/s/images/t1/0",
    "\(sessions)/s/turns",
    "\(sessions)/s/screen",
    "\(sessions)/s/events",
]

/// Makes every session read, and returns the errors of those that threw.
private func readEverything(_ client: ServeClient) async -> [Error] {
    var errors: [Error] = []
    func attempt(_ read: () async throws -> Void) async {
        do { try await read() } catch { errors.append(error) }
    }
    await attempt { _ = try await client.sessions(project: "p") }
    await attempt { _ = try await client.sessionsData(project: "p") }
    await attempt { _ = try await client.subagent(project: "p", session: "s", agent: "a1") }
    await attempt { _ = try await client.changes(project: "p", session: "s") }
    await attempt { _ = try await client.image(project: "p", session: "s", turn: "t1", index: 0) }
    await attempt { _ = try await client.turns(project: "p", session: "s") }
    _ = await client.screen(project: "p", session: "s")
    await attempt {
        for try await _ in client.events(project: "p", session: "s", cursor: nil, idleLimit: 5) {}
    }
    return errors
}

@Test func everySessionReadCarriesTheDeviceToken() async throws {
    let server = TokenServe(accepting: "kyo_held")
    try await server.start()
    defer { server.stop() }
    let errors = await readEverything(ServeClient(base: server.base, token: "kyo_held"))
    #expect(errors.isEmpty)
    #expect(server.requests.map(\.path) == everyRead)
    #expect(server.requests.allSatisfy { $0.authorization == "Bearer kyo_held" })
}

@Test func theStreamCarriesTheTokenBesideItsCursor() {
    let client = ServeClient(base: URL(string: "http://127.0.0.1:1")!, token: "kyo_held")
    let request = client.sessionRequest("v1/projects/p/sessions/s/events", cursor: "c7")
    #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer kyo_held")
    // The token is a header and never part of the address, which the server logs.
    #expect(request.url?.query == "cursor=c7")
}

@Test func aDeviceWithNoTokenSendsNoneAndIsToldThatOneIsNeeded() async throws {
    let server = TokenServe(accepting: "kyo_held")
    try await server.start()
    defer { server.stop() }
    let errors = await readEverything(ServeClient(base: server.base))
    #expect(server.requests.map(\.path) == everyRead)
    #expect(server.requests.allSatisfy { $0.authorization == nil })
    // Every read that reports a failure reports this one; the screen read answers nil.
    #expect(errors.count == everyRead.count - 1)
    for error in errors {
        let said = ServeClient.tokenNeeded(error)
        #expect(
            said
                == "A device token is needed: kyo serve shows a session only to a device that "
                + "holds one. Issue one on the server's machine with `kyo devices issue <name>` "
                + "and enter it in Settings.")
        // The status bar's sentence is the same one, and not the bare status.
        #expect(ServeClient.describe(error) == said)
    }
}

@Test func aFollowerRefusedForWantOfATokenSaysSoAndStops() async throws {
    let server = TokenServe(accepting: "kyo_held")
    try await server.start()
    defer { server.stop() }
    let follower = SessionFollower(
        client: ServeClient(base: server.base), project: "p", session: "s")
    var reasons: [String] = []
    for await update in follower.updates(cursor: nil) {
        if case .notLive(_, let reason) = update { reasons.append(reason) }
    }
    #expect(reasons.count == 1)
    #expect(reasons.first?.hasPrefix("A device token is needed") == true)
}

@Test func aTokenTheServerRefusesIsNamedForWhatIsWrongWithIt() async throws {
    for (reason, begins) in [
        ("unknown-token", "The server did not issue the device token saved in Settings"),
        ("revoked-token", "The device token saved in Settings was revoked"),
    ] {
        let server = TokenServe(accepting: "kyo_other", reason: reason)
        try await server.start()
        defer { server.stop() }
        var failure: Error?
        do {
            _ = try await ServeClient(base: server.base, token: "kyo_held").sessions(project: "p")
        } catch {
            failure = error
        }
        let refused = try #require(failure)
        let said = try #require(ServeClient.tokenNeeded(refused))
        #expect(said.hasPrefix(begins))
        #expect(said.hasSuffix("`kyo devices issue <name>` and enter it in Settings."))
    }
}

@Test func aRefusalThatIsNotAboutTheTokenIsNotSaidToBe() {
    let optIn = ServeError.status(403, #"{"ok": false, "error": "this project has not opted in"}"#)
    #expect(ServeClient.tokenNeeded(optIn) == nil)
    #expect(ServeClient.describe(optIn) == "kyo serve answered 403: this project has not opted in")
    #expect(ServeClient.tokenNeeded(URLError(.timedOut)) == nil)
}
