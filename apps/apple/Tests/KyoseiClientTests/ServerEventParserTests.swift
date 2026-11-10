import Foundation
import Testing

@testable import KyoseiClient

private func frames(_ lines: [String]) -> [ServerEvent] {
    var parser = ServerEventParser()
    return lines.compactMap { parser.feed(line: $0) }
}

@Test func aFrameEndsAtTheBlankLine() {
    let result = frames(["id: 7", "event: transcript", #"data: {"a": 1}"#, ""])
    #expect(result == [ServerEvent(name: "transcript", data: #"{"a": 1}"#, id: "7")])
}

@Test func aKeepaliveIsNotAFrame() {
    #expect(frames([": keepalive", ""]).isEmpty)
}

@Test func theFieldsOfOneFrameDoNotCarryIntoTheNext() {
    let result = frames(["id: 7", "event: ready", "data: 1", "", "event: reply", "data: 2", ""])
    #expect(result.map(\.id) == ["7", nil])
    #expect(result.map(\.name) == ["ready", "reply"])
}

@Test func aColonInTheDataIsKept() {
    #expect(frames(["data: a: b", ""]).first?.data == "a: b")
}

@Test func severalDataLinesAreJoined() {
    #expect(frames(["data: a", "data: b", ""]).first?.data == "a\nb")
}

private func weeklyFacts(_ weekly: String, start: String?) throws -> SessionFacts {
    let first = start.map { #","weekly_at_start":\#($0)"# } ?? ""
    let json = #"{"limits":{"seven_day":\#(weekly)}\#(first)}"#
    return try JSONDecoder().decode(SessionFacts.self, from: Data(json.utf8))
}

@Test func weeklyPointsUsedAreCountedFromTheSessionsFirstFigure() throws {
    let now = #"{"used_percentage":15.2,"resets_at":"T1"}"#
    let start = #"{"used_percentage":12.0,"resets_at":"T1"}"#
    #expect(try weeklyFacts(now, start: start).weeklyUsedThisSession == 3)
}

@Test func weeklyUseIsNotShownWithoutAFirstFigureAfterAResetOrAtZero() throws {
    let now = #"{"used_percentage":15.2,"resets_at":"T1"}"#
    #expect(try weeklyFacts(now, start: nil).weeklyUsedThisSession == nil)
    let other = #"{"used_percentage":12.0,"resets_at":"T0"}"#
    #expect(try weeklyFacts(now, start: other).weeklyUsedThisSession == nil)
    let same = #"{"used_percentage":15.0,"resets_at":"T1"}"#
    #expect(try weeklyFacts(now, start: same).weeklyUsedThisSession == nil)
}

// MARK: Whether a stream still carries its session (KYO-725)

private func readyFrame(state: String?, text: String?) -> ServerEvent {
    let screen = state.map { #"{"state": "\#($0)"}"# } ?? "null"
    let terminal = text.map { #"{"text": "\#($0)", "working_line": null}"# } ?? "null"
    return ServerEvent(
        name: "ready", data: #"{"screen": \#(screen), "terminal": \#(terminal), "held": null}"#,
        id: "c0")
}

@Test func aPaneSnapshotTakesOnlyThePartsAFrameStates() {
    var snapshot = PaneSnapshot()
    snapshot.update(from: Data(#"{"screen": {"state": "working"}, "terminal": {"text": "a", "working_line": "w"}}"#.utf8))
    #expect(snapshot == PaneSnapshot(state: "working", text: "a", workingLine: "w"))
    snapshot.update(from: Data(#"{"session_id": "s", "terminal": {"text": "b", "working_line": null}}"#.utf8))
    #expect(snapshot == PaneSnapshot(state: "working", text: "b", workingLine: nil))
    snapshot.update(from: Data(#"{"session_id": "s", "screen": null}"#.utf8))
    #expect(snapshot.state == nil)
    do {
        let answer = snapshot.update(from: Data(#"{"session_id": "s", "text": "reply"}"#.utf8))
        #expect(!answer)
    }
}

@Test func aQuietStreamWhosePaneDiffersOnTwoReadsInARowIsStale() {
    let start = ContinuousClock.now
    var watch = StreamWatch(quiet: .seconds(10))
    watch.received(readyFrame(state: "idle", text: "before"), at: start)
    #expect(!watch.wantsRead(at: start + .seconds(9)))
    #expect(watch.wantsRead(at: start + .seconds(10)))
    let now = PaneSnapshot(state: "working", text: "after")
    do {
        let answer = watch.read(now, at: start + .seconds(10))
        #expect(!answer)
    }
    #expect(!watch.wantsRead(at: start + .seconds(15)))
    do {
        let answer = watch.read(now, at: start + .seconds(20))
        #expect(answer)
    }
}

@Test func aFrameFromTheReaderBetweenReadsKeepsTheStreamLive() {
    let start = ContinuousClock.now
    var watch = StreamWatch(quiet: .seconds(10))
    watch.received(readyFrame(state: "idle", text: "before"), at: start)
    let now = PaneSnapshot(state: "working", text: "after")
    do {
        let answer = watch.read(now, at: start + .seconds(10))
        #expect(!answer)
    }
    watch.received(
        ServerEvent(name: "screen", data: #"{"screen": {"state": "working"}}"#, id: nil),
        at: start + .seconds(11))
    #expect(watch.confirmed)
    do {
        let answer = watch.read(now, at: start + .seconds(21))
        #expect(!answer)
    }
}

@Test func aHeldAnnouncementIsNotTheReaderAndKeepsNoStreamLive() {
    let start = ContinuousClock.now
    var watch = StreamWatch(quiet: .seconds(10))
    watch.received(readyFrame(state: "idle", text: "before"), at: start)
    watch.received(
        ServerEvent(name: "held", data: #"{"outcome": "delivered", "id": "h1"}"#, id: nil),
        at: start + .seconds(9))
    #expect(!watch.confirmed)
    #expect(watch.wantsRead(at: start + .seconds(10)))
}

@Test func aReadyFrameAloneIsNotProofAndAMatchingReadIs() {
    let start = ContinuousClock.now
    var watch = StreamWatch(quiet: .seconds(10))
    watch.received(readyFrame(state: "idle", text: "same"), at: start)
    #expect(watch.ready && !watch.confirmed)
    do {
        let answer = watch.read(PaneSnapshot(state: "idle", text: "same"), at: start + .seconds(10))
        #expect(!answer)
    }
    #expect(watch.confirmed)
}

/// A read that failed is not a read: it neither counts towards the two nor breaks them.
@Test func aPaneThatCannotBeReadSaysNothingAboutTheStream() {
    let start = ContinuousClock.now
    var watch = StreamWatch(quiet: .seconds(10))
    watch.received(readyFrame(state: "idle", text: "before"), at: start)
    let now = PaneSnapshot(state: "working", text: "after")
    do {
        let answer = watch.read(now, at: start + .seconds(10))
        #expect(!answer)
    }
    do {
        let answer = watch.read(nil, at: start + .seconds(20))
        #expect(!answer)
        #expect(!watch.confirmed)
    }
    do {
        let answer = watch.read(now, at: start + .seconds(30))
        #expect(answer)
    }
}

@Test func backoffDoublesToItsLongestAndStartsAgainWhenReset() {
    var backoff = Backoff(first: .seconds(1), longest: .seconds(5))
    #expect((0..<5).map { _ in backoff.next() } == [1, 2, 4, 5, 5].map { Duration.seconds($0) })
    backoff.reset()
    #expect(backoff.next() == .seconds(1))
}

// MARK: A local stand-in for `kyo serve`

import Network

/// Serves scripted answers on a loopback port, and can stop and start again on that port.
private final class FakeServe: @unchecked Sendable {
    /// One request's connection, answered by the test's handler.
    final class Reply: @unchecked Sendable {
        let connection: NWConnection
        let queue: DispatchQueue
        private let lock = NSLock()
        private var closed = false

        init(_ connection: NWConnection, _ queue: DispatchQueue) {
            self.connection = connection
            self.queue = queue
        }

        var isClosed: Bool { lock.withLock { closed } }

        func respond(_ status: Int, _ body: String) {
            write(
                "HTTP/1.1 \(status) Answer\r\nContent-Type: application/json\r\n"
                    + "Content-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n\(body)")
            close()
        }

        func open() {
            write(
                "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream; charset=utf-8\r\n"
                    + "Cache-Control: no-cache\r\nConnection: close\r\n\r\nretry: 1000\n: fake\n\n")
        }

        func frame(_ name: String, _ data: String, id: String? = nil) {
            write((id.map { "id: \($0)\n" } ?? "") + "event: \(name)\ndata: \(data)\n\n")
        }

        /// A keepalive every `interval` until the connection closes, as the server sends on
        /// a stream with nothing else to say.
        func keepalive(every interval: Double) {
            queue.asyncAfter(deadline: .now() + interval) { [self] in
                guard !isClosed else { return }
                write(": keepalive\n\n")
                keepalive(every: interval)
            }
        }

        func write(_ text: String) {
            guard !isClosed else { return }
            connection.send(content: Data(text.utf8), completion: .contentProcessed { _ in })
        }

        func close() {
            lock.withLock { closed = true }
            connection.send(
                content: nil, contentContext: .finalMessage, isComplete: true,
                completion: .contentProcessed { [connection] _ in connection.cancel() })
        }
    }

    typealias Handler = @Sendable (_ target: String, _ count: Int, _ reply: Reply) -> Void

    private let queue = DispatchQueue(label: "fake-serve")
    private let lock = NSLock()
    private let handler: Handler
    private var listener: NWListener?
    private var replies: [Reply] = []
    private var targets: [String] = []
    private var port: NWEndpoint.Port?

    init(_ handler: @escaping Handler) {
        self.handler = handler
    }

    var requests: [String] { lock.withLock { targets } }
    var base: URL { URL(string: "http://127.0.0.1:\(lock.withLock { port!.rawValue })")! }

    func requests(to route: String) -> [String] {
        requests.filter { $0.split(separator: "?").first?.hasSuffix(route) == true }
    }

    func start() async throws {
        let parameters = NWParameters.tcp
        parameters.allowLocalEndpointReuse = true
        parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: lock.withLock { port } ?? .any)
        let listener = try NWListener(using: parameters)
        listener.newConnectionHandler = { [self] connection in accept(connection) }
        let started = Started()
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            listener.stateUpdateHandler = { state in
                switch state {
                case .ready, .failed, .cancelled: started.once { continuation.resume() }
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

    /// Closes the listener and every connection, as a server that exits does.
    func stop() {
        let (listener, replies) = lock.withLock { (self.listener, self.replies) }
        listener?.cancel()
        for reply in replies { reply.connection.cancel() }
        lock.withLock {
            self.listener = nil
            self.replies = []
        }
    }

    private func accept(_ connection: NWConnection) {
        connection.start(queue: queue)
        receive(connection, Data())
    }

    private func receive(_ connection: NWConnection, _ buffer: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) {
            [self] data, _, complete, error in
            var buffer = buffer
            if let data { buffer.append(data) }
            let text = String(decoding: buffer, as: UTF8.self)
            guard text.contains("\r\n\r\n") else {
                if !complete && error == nil { receive(connection, buffer) }
                return
            }
            let target = String(text.split(separator: " ").dropFirst().first ?? "")
            let reply = Reply(connection, queue)
            let count = lock.withLock {
                targets.append(target)
                replies.append(reply)
                let route = target.split(separator: "?").first ?? ""
                return targets.filter { $0.split(separator: "?").first == route }.count
            }
            handler(target, count, reply)
        }
    }
}

private final class Started: @unchecked Sendable {
    private let lock = NSLock()
    private var done = false

    func once(_ body: () -> Void) {
        let first = lock.withLock {
            defer { done = true }
            return !done
        }
        if first { body() }
    }
}

/// What a follower handed over, read until `until` holds or `within` has passed.
private func collect(
    _ follower: SessionFollower, cursor: String? = "c0", within: Duration = .seconds(8),
    until: @escaping @Sendable ([FollowUpdate]) -> Bool
) async -> [FollowUpdate] {
    let seen = Collected()
    let reader = Task {
        for await update in follower.updates(cursor: cursor) {
            seen.append(update)
            if until(seen.all) { return }
        }
    }
    let deadline = ContinuousClock.now + within
    while ContinuousClock.now < deadline, !until(seen.all) {
        try? await Task.sleep(for: .milliseconds(20))
    }
    reader.cancel()
    _ = await reader.value
    return seen.all
}

private final class Collected: @unchecked Sendable {
    private let lock = NSLock()
    private var items: [FollowUpdate] = []
    var all: [FollowUpdate] { lock.withLock { items } }
    func append(_ item: FollowUpdate) { lock.withLock { items.append(item) } }
}

private func follower(_ server: FakeServe) -> SessionFollower {
    var follower = SessionFollower(
        client: ServeClient(base: server.base), project: "p", session: "s")
    follower.idleLimit = 3
    follower.quiet = .milliseconds(200)
    follower.backoff = Backoff(first: .milliseconds(50), longest: .milliseconds(200))
    return follower
}

private func names(_ updates: [FollowUpdate]) -> [String] {
    updates.map {
        switch $0 {
        case .event(let event): event.name
        case .live: "LIVE"
        case .notLive: "NOT LIVE"
        }
    }
}

private func reasons(_ updates: [FollowUpdate]) -> [String] {
    updates.compactMap {
        if case .notLive(_, let reason) = $0 { reason } else { nil }
    }
}

private let readyIdle = #"{"screen": {"state": "idle"}, "terminal": {"text": "A", "working_line": null}, "held": null}"#
private let screenIdle = #"{"ok": true, "screen": {"state": "idle"}, "terminal": {"text": "A", "working_line": null}}"#

// MARK: The stream, against the stand-in

@Test func aStreamThatSendsNothingIsGivenUpAfterItsIdleLimit() async throws {
    let server = FakeServe { _, _, reply in
        reply.open()
        reply.frame("ready", readyIdle)
    }
    try await server.start()
    defer { server.stop() }
    let started = ContinuousClock.now
    var failure: Error?
    do {
        for try await _ in ServeClient(base: server.base).events(
            project: "p", session: "s", cursor: nil, idleLimit: 1)
        {}
    } catch {
        failure = error
    }
    #expect((failure as? URLError)?.code == .timedOut)
    #expect(ContinuousClock.now - started < .seconds(5))
}

@Test func aRefusedStreamCarriesTheServersSentence() async throws {
    let server = FakeServe { _, _, reply in
        reply.respond(403, #"{"ok": false, "error": "this project has not opted in"}"#)
    }
    try await server.start()
    defer { server.stop() }
    var failure: Error?
    do {
        for try await _ in ServeClient(base: server.base).events(
            project: "p", session: "s", cursor: nil)
        {}
    } catch {
        failure = error
    }
    #expect(ServeClient.describe(failure!) == "kyo serve answered 403: this project has not opted in")
}

@Test func theFollowerReconnectsWithItsCursorWhenTheServerClosesTheStream() async throws {
    let server = FakeServe { target, count, reply in
        guard target.contains("/events") else { return reply.respond(200, screenIdle) }
        reply.open()
        reply.frame("ready", readyIdle)
        if count == 1 {
            reply.frame("transcript", #"{"turns": []}"#, id: "c1")
            reply.close()
        } else {
            reply.keepalive(every: 0.1)
        }
    }
    try await server.start()
    defer { server.stop() }
    let updates = await collect(follower(server)) { names($0).filter { $0 == "LIVE" }.count == 2 }
    #expect(names(updates) == ["ready", "LIVE", "transcript", "NOT LIVE", "ready", "LIVE"])
    #expect(reasons(updates) == [SessionFollower.closed])
    let streams = server.requests(to: "/events")
    #expect(streams.count == 2)
    #expect(streams.first?.hasSuffix("cursor=c0") == true)
    #expect(streams.last?.hasSuffix("cursor=c1") == true)
}

@Test func theFollowerResumesAfterTheServerIsDownForATime() async throws {
    let server = FakeServe { target, _, reply in
        guard target.contains("/events") else { return reply.respond(200, screenIdle) }
        reply.open()
        reply.frame("ready", readyIdle)
        reply.keepalive(every: 0.1)
    }
    try await server.start()
    defer { server.stop() }
    let follower = follower(server)
    let restart = Task {
        try? await Task.sleep(for: .milliseconds(400))
        server.stop()
        try? await Task.sleep(for: .milliseconds(800))
        try? await server.start()
    }
    let updates = await collect(follower) { names($0).filter { $0 == "LIVE" }.count == 2 }
    _ = await restart.value
    let seen = names(updates)
    #expect(seen.first == "ready" && seen.last == "LIVE")
    #expect(seen.filter { $0 == "LIVE" }.count == 2)
    // While the server was down, each attempt said the chat was not live, all since one moment.
    let since = Set(updates.compactMap { if case .notLive(let at, _) = $0 { at } else { nil } })
    #expect(since.count == 1)
    #expect(server.requests(to: "/events").count >= 2)
}

@Test func theFollowerResumesAfterRequestsAreRefusedForATime() async throws {
    let server = FakeServe { target, count, reply in
        guard target.contains("/events") else { return reply.respond(200, screenIdle) }
        guard count > 3 else {
            return reply.respond(503, #"{"ok": false, "error": "the server is starting"}"#)
        }
        reply.open()
        reply.frame("ready", readyIdle)
        reply.keepalive(every: 0.1)
    }
    try await server.start()
    defer { server.stop() }
    let updates = await collect(follower(server)) { names($0).contains("LIVE") }
    #expect(names(updates) == ["NOT LIVE", "NOT LIVE", "NOT LIVE", "ready", "LIVE"])
    #expect(reasons(updates).allSatisfy { $0 == "kyo serve answered 503: the server is starting" })
}

/// The stall of KYO-725: the connection stays up and keepalives keep coming, while nothing
/// the reader of the session would send arrives, and the pane has moved on.
@Test func aStreamWhoseReaderHasStoppedIsReconnected() async throws {
    let server = FakeServe { target, count, reply in
        guard target.contains("/events") else {
            return reply.respond(
                200,
                #"{"ok": true, "screen": {"state": "working"}, "terminal": {"text": "B", "working_line": "w"}}"#)
        }
        reply.open()
        if count == 1 {
            reply.frame("ready", readyIdle)
            reply.frame("held", #"{"outcome": "delivered", "id": "h1"}"#)
        } else {
            reply.frame(
                "ready",
                #"{"screen": {"state": "working"}, "terminal": {"text": "B", "working_line": "w"}, "held": null}"#)
        }
        reply.keepalive(every: 0.05)
    }
    try await server.start()
    defer { server.stop() }
    let updates = await collect(follower(server)) { names($0).filter { $0 == "LIVE" }.count == 2 }
    #expect(names(updates) == ["ready", "LIVE", "held", "NOT LIVE", "ready", "LIVE"])
    #expect(reasons(updates) == [SessionFollower.stopped])
    #expect(server.requests(to: "/events").count == 2)
    #expect(server.requests(to: "/screen").count >= 3)
}

@Test func aQuietStreamWhosePaneIsUnchangedIsKeptOpen() async throws {
    let server = FakeServe { target, _, reply in
        guard target.contains("/events") else { return reply.respond(200, screenIdle) }
        reply.open()
        reply.frame("ready", readyIdle)
        reply.keepalive(every: 0.05)
    }
    try await server.start()
    defer { server.stop() }
    let updates = await collect(follower(server), within: .milliseconds(1500)) { _ in false }
    #expect(names(updates) == ["ready", "LIVE"])
    #expect(server.requests(to: "/events").count == 1)
    #expect(server.requests(to: "/screen").count >= 3)
}

@Test func anErrorFrameReconnectsAndCatchesUpFromTheCursor() async throws {
    let server = FakeServe { target, count, reply in
        guard target.contains("/events") else { return reply.respond(200, screenIdle) }
        reply.open()
        if count == 1 {
            reply.frame("ready", readyIdle)
            reply.frame("transcript", #"{"turns": []}"#, id: "c1")
            reply.frame("error", #"{"error": "session s could not be sampled: tmux timed out"}"#)
        } else {
            reply.frame("transcript", #"{"turns": []}"#, id: "c2")
            reply.frame("ready", readyIdle)
        }
        reply.keepalive(every: 0.1)
    }
    try await server.start()
    defer { server.stop() }
    let updates = await collect(follower(server)) { names($0).filter { $0 == "LIVE" }.count == 2 }
    #expect(names(updates) == ["ready", "LIVE", "transcript", "NOT LIVE", "transcript", "ready", "LIVE"])
    #expect(reasons(updates) == ["session s could not be sampled: tmux timed out"])
    #expect(server.requests(to: "/events").last?.hasSuffix("cursor=c1") == true)
}

@Test func aResetFrameEndsTheFollow() async throws {
    let server = FakeServe { target, _, reply in
        guard target.contains("/events") else { return reply.respond(200, screenIdle) }
        reply.open()
        reply.frame("ready", readyIdle)
        reply.frame("reset", #"{"session_id": "s"}"#)
        reply.keepalive(every: 0.1)
    }
    try await server.start()
    defer { server.stop() }
    let updates = await collect(follower(server), within: .seconds(2)) { _ in false }
    #expect(names(updates) == ["ready", "LIVE", "reset"])
}

@Test func aRefusalEndsTheFollowSayingWhy() async throws {
    let server = FakeServe { _, _, reply in
        reply.respond(404, #"{"ok": false, "error": "no session s in this project"}"#)
    }
    try await server.start()
    defer { server.stop() }
    let updates = await collect(follower(server), within: .seconds(2)) { _ in false }
    #expect(reasons(updates) == ["kyo serve answered 404: no session s in this project"])
    #expect(server.requests.count == 1)
}

// MARK: The `resumed` frame (KYO-728)

@Test func aResumedFrameShowsTheReaderIsReading() {
    let start = ContinuousClock.now
    var watch = StreamWatch(quiet: .seconds(10))
    watch.received(readyFrame(state: "idle", text: "before"), at: start)
    #expect(!watch.confirmed)
    watch.received(
        ServerEvent(name: "resumed", data: #"{"session_id": "s"}"#, id: nil), at: start + .seconds(9))
    #expect(watch.confirmed)
    // It counts as heard from the reader, and leaves what the stream said of the pane.
    #expect(!watch.wantsRead(at: start + .seconds(18)))
    #expect(watch.stated == PaneSnapshot(state: "idle", text: "before"))
}

@Test func aResumedFrameIsTakenWithoutReconnecting() async throws {
    let server = FakeServe { target, _, reply in
        guard target.contains("/events") else { return reply.respond(200, screenIdle) }
        reply.open()
        reply.frame("ready", readyIdle)
        reply.frame("resumed", #"{"session_id": "s"}"#)
        reply.frame("transcript", #"{"turns": []}"#, id: "c1")
        reply.keepalive(every: 0.05)
    }
    try await server.start()
    defer { server.stop() }
    // Read on past the frames, so that a reconnection made because of one would be seen.
    let updates = await collect(follower(server), within: .seconds(1)) { _ in false }
    #expect(names(updates) == ["ready", "LIVE", "resumed", "transcript"])
    #expect(server.requests(to: "/events").count == 1)
}

@Test func anErrorSentAfterReadyKeepsTheChatNotLiveUntilTheReaderResumes() async throws {
    // A reader whose passes fail answers each new connection with `ready` and then `error`.
    let server = FakeServe { target, count, reply in
        guard target.contains("/events") else { return reply.respond(200, screenIdle) }
        reply.open()
        reply.frame("ready", readyIdle)
        if count < 3 {
            reply.frame("error", #"{"error": "session s could not be sampled: tmux timed out"}"#)
        } else {
            reply.frame("resumed", #"{"session_id": "s"}"#)
        }
        reply.keepalive(every: 0.05)
    }
    try await server.start()
    defer { server.stop() }
    let updates = await collect(follower(server)) { names($0).filter { $0 == "LIVE" }.count == 2 }
    // The second connection's `ready` is not taken as live, and the third's is once
    // `resumed` has arrived.
    #expect(
        names(updates) == [
            "ready", "LIVE", "NOT LIVE", "ready", "NOT LIVE", "ready", "resumed", "LIVE",
        ])
    #expect(reasons(updates).allSatisfy { $0 == "session s could not be sampled: tmux timed out" })
    #expect(server.requests(to: "/events").count == 3)
}

@Test func aStoppedReadersErrorReconnectsToAReadyThatIsLive() async throws {
    // `stopped` says the reader has ended, so the next connection's `ready` is a new
    // reader's fresh read and needs no further proof.
    let server = FakeServe { target, count, reply in
        guard target.contains("/events") else { return reply.respond(200, screenIdle) }
        reply.open()
        reply.frame("ready", readyIdle)
        if count == 1 {
            reply.frame("error", #"{"error": "the reader of session s stopped", "stopped": true}"#)
            reply.close()
        } else {
            reply.keepalive(every: 0.05)
        }
    }
    try await server.start()
    defer { server.stop() }
    let started = ContinuousClock.now
    let updates = await collect(follower(server)) { names($0).filter { $0 == "LIVE" }.count == 2 }
    #expect(names(updates) == ["ready", "LIVE", "NOT LIVE", "ready", "LIVE"])
    #expect(reasons(updates) == ["the reader of session s stopped"])
    #expect(server.requests(to: "/screen").isEmpty)
    #expect(ContinuousClock.now - started < .seconds(2))
}
