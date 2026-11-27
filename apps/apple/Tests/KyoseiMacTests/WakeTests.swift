#if KYOSEI_CHECK
import AppKit
import Foundation
import KyoseiClient
import Testing

@testable import KyoseiMac

// KYO-989: the feed after the machine wakes. These exist only in a build made with the
// `KYOSEI_CHECK` condition, as the check channel's tests do, since there the feed reads its
// device token from the run's environment and never from the person's keychain:
//
//   swift test --scratch-path .build/check -Xswiftc -DKYOSEI_CHECK --filter WakeTests
//
// They put no window on the screen.

@MainActor
private func feed(_ relay: SilentRelay, log file: URL) -> SessionFeed {
    let connections = Connections(log: ConnectionLog(file: file), make: trustingSession)
    return SessionFeed(
        client: ServeClient(base: relay.base, connections: connections), project: "p")
}

private func postWake() async {
    await MainActor.run {
        NSWorkspace.shared.notificationCenter.post(
            name: NSWorkspace.didWakeNotification, object: NSWorkspace.shared)
    }
}

@Suite(.serialized) struct WakeTests {
    /// The stream and the reads went out on a connection that went silent across the sleep.
    /// The wake starts them again on new connections at once, well within the stream's idle
    /// limit, so no timer that the sleep may have stopped is waited on.
    @Test(.enabled(if: relayCanRun), .timeLimit(.minutes(1)))
    @MainActor
    func aWakeStartsTheFeedsReadsAndStreamsAgainOnNewConnections() async throws {
        let relay = try SilentRelay()
        defer { relay.stop() }
        let file = temporaryLog()
        defer { try? FileManager.default.removeItem(at: file) }
        let feed = feed(relay, log: file)
        feed.start()
        #expect(await eventually(within: .seconds(10)) { await feed.state == .live })
        #expect(await eventually(within: .seconds(10)) {
            relay.requests.contains("/v1/projects/p/events")
        })
        relay.sleep()
        try await Task.sleep(for: .milliseconds(300))
        let before = relay.requests.count
        relay.wake()
        let woke = ContinuousClock.now
        await postWake()
        let wanted = [
            "/v1/projects", "/v1/projects/p/sessions", "/v1/projects/p/sessions/s/turns",
            "/v1/projects/p/sessions/s/events", "/v1/projects/p/events",
        ]
        #expect(await eventually(within: .seconds(5)) {
            let after = Set(relay.requests.dropFirst(before))
            return wanted.allSatisfy(after.contains)
        })
        #expect(await eventually(within: .seconds(5)) { await feed.state == .live })
        #expect(ContinuousClock.now - woke < .seconds(ServeClient.streamIdleLimit / 4))
        let written = lines(of: file)
        #expect(written.contains { $0.hasSuffix(" wake") })
        #expect(written.contains { $0.hasSuffix(" new connections: the machine woke") })
        #expect(written.contains { $0.hasSuffix(" live: p s") })
    }

    /// A read that fails writes its error and the wait before the next attempt.
    @Test(.timeLimit(.minutes(1)))
    @MainActor
    func aFailedReadWritesALineWithItsErrorAndTheNextWait() async throws {
        let file = temporaryLog()
        defer { try? FileManager.default.removeItem(at: file) }
        // Nothing listens on port 1.
        let feed = SessionFeed(
            client: ServeClient(
                base: URL(string: "http://127.0.0.1:1")!,
                connections: Connections(log: ConnectionLog(file: file))),
            project: "p")
        feed.start()
        #expect(await eventually(within: .seconds(5)) {
            lines(of: file).contains {
                $0.contains(" session read of p failed: kyo serve cannot be reached. (URLError ")
                    && $0.hasSuffix("; next attempt in 1s")
            }
        })
        await postWake()
        #expect(await eventually(within: .seconds(2)) {
            lines(of: file).contains { $0.hasSuffix(" wake") }
        })
    }
}
#endif
