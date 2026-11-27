import Foundation
import KyoseiClient
import Testing

@testable import KyoseiMac

// KYO-989: the chat stayed not live after the laptop woke, until the app was quit. These drive
// the app's requests against a stand-in whose connections go silent without closing and whose
// new connections fail for a while after the wake, as when the network is being joined again,
// and then succeed.

/// The status a read was answered with, or the code of the `URLError` it failed with.
private func read(_ session: URLSession, _ url: URL, timeout: TimeInterval) async -> Int {
    var request = URLRequest(url: url)
    request.timeoutInterval = timeout
    do {
        let (_, response) = try await session.data(for: request)
        return (response as? HTTPURLResponse)?.statusCode ?? 0
    } catch {
        return (error as? URLError)?.code.rawValue ?? 0
    }
}

private let timedOut = URLError.Code.timedOut.rawValue

/// Why the app did not recover before KYO-989, which sent every request on
/// `URLSession.shared`: a session keeps the HTTP/2 connection that went silent and puts each
/// later request on it, though new connections are answered, and only a new session opens one.
@Test(.enabled(if: relayCanRun), .timeLimit(.minutes(1)))
func aSessionKeepsAnHTTP2ConnectionThatWentSilent() async throws {
    let relay = try SilentRelay()
    defer { relay.stop() }
    let session = trustingSession()
    defer { session.invalidateAndCancel() }
    let version = relay.base.appending(path: "v1/projects")
    #expect(await read(session, version, timeout: 5) == 200)
    relay.sleep()
    #expect(await read(session, version, timeout: 1) == timedOut)
    relay.wake()
    for _ in 0..<3 {
        #expect(await read(session, version, timeout: 1) == timedOut)
    }
    await withCheckedContinuation { done in session.reset { done.resume() } }
    #expect(await read(session, version, timeout: 1) == timedOut)
    let fresh = trustingSession()
    defer { fresh.invalidateAndCancel() }
    #expect(await read(fresh, version, timeout: 5) == 200)
}

/// Over plain HTTP, as `kyo serve` answers on its own port, each request has a connection of
/// its own, and the first after the wake is answered.
@Test(.enabled(if: relayCanRun), .timeLimit(.minutes(1)))
func overPlainHTTPTheFirstReadAfterTheWakeIsAnswered() async throws {
    let relay = try SilentRelay(plain: true)
    defer { relay.stop() }
    let session = URLSession(configuration: .default)
    defer { session.invalidateAndCancel() }
    let version = relay.base.appending(path: "v1/projects")
    #expect(await read(session, version, timeout: 5) == 200)
    relay.sleep()
    #expect(await read(session, version, timeout: 1) == timedOut)
    relay.wake()
    #expect(await read(session, version, timeout: 5) == 200)
}

/// What the app does since KYO-989, without a wake to tell it: the stream that went silent
/// times out, the client's connections are renewed, the attempts made while the network is
/// still being joined fail, and a later one makes the chat live.
@Test(.enabled(if: relayCanRun), .timeLimit(.minutes(1)))
func theFollowerIsLiveAgainOnNewConnectionsAfterAConnectionWentSilent() async throws {
    let relay = try SilentRelay(rejoin: 3)
    defer { relay.stop() }
    let file = temporaryLog()
    defer { try? FileManager.default.removeItem(at: file) }
    let log = ConnectionLog(file: file)
    let connections = Connections(log: log, make: trustingSession)
    var follower = SessionFollower(
        client: ServeClient(base: relay.base, connections: connections), project: "p", session: "s")
    follower.idleLimit = 2
    follower.quiet = .seconds(30)
    follower.backoff = Backoff(first: .milliseconds(500), longest: .seconds(1))
    let seen = Seen()
    let reader = Task {
        for await update in follower.updates(cursor: "c0") { seen.append(update) }
    }
    defer { reader.cancel() }
    #expect(await eventually(within: .seconds(10)) { seen.lives == 1 })
    relay.sleep()
    try await Task.sleep(for: .milliseconds(300))
    relay.wake()
    #expect(await eventually(within: .seconds(20)) { seen.lives == 2 })
    #expect(connections.renewed >= 1)
    // The attempts made in the three seconds after the wake failed, and one after them did not.
    #expect(seen.reasons.contains("kyo serve sent nothing for too long."))
    #expect(seen.reasons.contains { $0 != "kyo serve sent nothing for too long." })
    let written = lines(of: file)
    #expect(written.contains { $0.hasSuffix("new connections: a request timed out") })
    #expect(
        written.contains {
            $0.contains("stream of p s lost: kyo serve sent nothing for too long. (URLError -1001);"
                + " next attempt in ")
        })
}

private final class Seen: @unchecked Sendable {
    private let lock = NSLock()
    private var updates: [FollowUpdate] = []
    func append(_ update: FollowUpdate) { lock.withLock { updates.append(update) } }
    var lives: Int { lock.withLock { updates.filter { $0 == .live }.count } }
    var reasons: [String] {
        lock.withLock {
            updates.compactMap { if case .notLive(_, let reason) = $0 { reason } else { nil } }
        }
    }
}

// MARK: The connection log

@Test func theConnectionLogTimesEachLineAndIsKeptAcrossALaunch() throws {
    let file = temporaryLog()
    defer { try? FileManager.default.removeItem(at: file) }
    ConnectionLog(file: file).write("wake")
    // A second log on the same file is the next launch's.
    ConnectionLog(file: file).write("a read failed\nacross lines")
    let written = lines(of: file)
    #expect(written.count == 2)
    let stamps = ISO8601DateFormatter()
    stamps.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    for line in written {
        let stamp = String(line.prefix { $0 != " " })
        #expect(stamps.date(from: stamp) != nil, "\(line)")
    }
    #expect(written[0].hasSuffix(" wake"))
    #expect(written[1].hasSuffix(" a read failed across lines"))
}

@Test func theConnectionLogIsBoundedByMovingTheFileAside() throws {
    let file = temporaryLog()
    let aside = file.appendingPathExtension("1")
    defer {
        try? FileManager.default.removeItem(at: file)
        try? FileManager.default.removeItem(at: aside)
    }
    let log = ConnectionLog(file: file, limit: 1000)
    for index in 0..<200 { log.write("line \(index) of the connection log") }
    let size = { (url: URL) in
        (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? 0
    }
    #expect(size(file) <= 1000)
    #expect(size(aside) <= 1000 && size(aside) > 0)
    #expect(lines(of: file).last?.hasSuffix("line 199 of the connection log") == true)
    #expect(log.previous == aside)
}

@Test func aLogWithNoFileWritesNothing() {
    let log = ConnectionLog()
    log.write("wake")
    #expect(log.location == nil)
}

/// A path is written when it changes, and one that is satisfied again after one that was not
/// starts the reads again; the first path the monitor states does not.
@Test @MainActor func aReturningNetworkPathIsWrittenAndStartsTheReadsAgain() throws {
    let file = temporaryLog()
    defer { try? FileManager.default.removeItem(at: file) }
    var asked: [String] = []
    let watch = SystemWatch(log: ConnectionLog(file: file)) { asked.append($0) }
    defer { watch.stop() }
    let before = lines(of: file).count
    watch.took("network path satisfied: zz9 (wifi)", satisfied: true)
    watch.took("network path satisfied: zz9 (wifi)", satisfied: true)
    watch.took("network path unsatisfied: zz9 (wifi)", satisfied: false)
    watch.took("network path satisfied: zz9 (wifi)", satisfied: true)
    // The monitor's own updates may land too, naming the machine's real interfaces.
    let written = lines(of: file).dropFirst(before).filter { $0.contains("zz9") }
    #expect(written.map { String($0.drop { $0 != " " }.dropFirst()) } == [
        "network path satisfied: zz9 (wifi)", "network path unsatisfied: zz9 (wifi)",
        "network path satisfied: zz9 (wifi)",
    ])
    #expect(asked == ["the network path is satisfied again"])
}
