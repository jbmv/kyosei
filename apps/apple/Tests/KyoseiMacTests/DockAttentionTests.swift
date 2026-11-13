import AppKit
import Foundation
import Testing

@testable import KyoseiMac

// KYO-922: while the session waits on the person and another app is in front, the Dock
// request is made again on each tick, so the icon goes on bouncing until the app comes to the
// front or the session works again. The ticks are called by hand here; the timer only calls
// `renew` on its interval.
@MainActor
@Suite(.serialized)
struct DockAttentionTests {
    final class Dock {
        var active = false
        var next = 0
        var open: Set<Int> = []
        var made = 0
    }

    private func attention(_ dock: Dock, center: NotificationCenter) -> DockAttention {
        DockAttention(
            isActive: { dock.active },
            request: {
                dock.next += 1
                dock.made += 1
                dock.open.insert(dock.next)
                return dock.next
            },
            cancel: { dock.open.remove($0) },
            center: center)
    }

    @Test func aWaitWithAnotherAppInFrontIsAskedAgainOnEachTickUntilTheAppComesToTheFront() {
        let dock = Dock()
        let center = NotificationCenter()
        let subject = attention(dock, center: center)
        subject.waiting(true)
        #expect(subject.bouncing)
        #expect(dock.made == 1)
        subject.renew()
        subject.renew()
        // Each tick replaces the request it made before, so one is open at a time.
        #expect(dock.made == 3)
        #expect(dock.open.count == 1)
        center.post(name: NSApplication.didBecomeActiveNotification, object: nil)
        #expect(!subject.bouncing)
        #expect(dock.open.isEmpty)
        subject.renew()
        #expect(dock.made == 3)
    }

    @Test func aTickFindingTheAppInFrontEndsTheBounce() {
        let dock = Dock()
        let subject = attention(dock, center: NotificationCenter())
        subject.waiting(true)
        dock.active = true
        subject.renew()
        #expect(!subject.bouncing)
        #expect(dock.open.isEmpty)
        #expect(dock.made == 1)
    }

    @Test func workBegunAgainEndsTheBounce() {
        let dock = Dock()
        let subject = attention(dock, center: NotificationCenter())
        subject.waiting(true)
        subject.waiting(false)
        #expect(!subject.bouncing)
        #expect(dock.open.isEmpty)
    }

    @Test func aWaitWhileTheAppIsInFrontAsksNothing() {
        let dock = Dock()
        dock.active = true
        let subject = attention(dock, center: NotificationCenter())
        subject.waiting(true)
        #expect(!subject.bouncing)
        #expect(dock.made == 0)
    }

    @Test func theTimerRenewsTheRequestByItself() async throws {
        let dock = Dock()
        let subject = attention(dock, center: NotificationCenter())
        subject.waiting(true)
        try await Task.sleep(for: .seconds(DockAttention.interval + 0.5))
        #expect(dock.made >= 2)
        subject.stop()
    }
}
