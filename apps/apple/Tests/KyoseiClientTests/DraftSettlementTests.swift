import Foundation
import Testing

@testable import KyoseiClient

/// What a test's stand-in for the page and the server saw.
private actor Server {
    private(set) var received: [String] = []
    func receive(_ text: String) { received.append(text) }
}

@Test func aPendingSaveIsSentAndAcknowledgedBeforeTheAppMayQuit() async {
    let server = Server()
    let outcome = await settleDraft(limit: .seconds(5)) {
        // The page hands over the save it was holding, and the server takes a moment.
        try? await Task.sleep(for: .milliseconds(50))
        await server.receive("the last characters typed")
        return true
    }
    // The settlement is what lets the app quit, and it returned only after the save landed.
    #expect(outcome == .acknowledged)
    #expect(await server.received == ["the last characters typed"])
}

@Test func withNothingPendingTheAppQuitsAtOnce() async {
    let began = ContinuousClock.now
    let outcome = await settleDraft(limit: .seconds(5)) { nil }
    #expect(outcome == .nothingPending)
    #expect(ContinuousClock.now - began < .seconds(1))
}

@Test func aSaveTheServerRefusesIsNotTakenAsSaved() async {
    #expect(await settleDraft(limit: .seconds(5)) { false } == .unsaved)
}

@Test func aServerThatDoesNotAnswerDelaysTheQuitByTheLimitAndNoLonger() async {
    let began = ContinuousClock.now
    let outcome = await settleDraft(limit: .milliseconds(100)) {
        // A request that ends only at its own time-out, long after the limit.
        try? await Task.sleep(for: .seconds(30))
        return true
    }
    let took = ContinuousClock.now - began
    #expect(outcome == .unsaved)
    #expect(took >= .milliseconds(100))
    #expect(took < .seconds(5))
}

@Test func unsavedTextIsKeptAndGivenBackAtTheNextLaunchUntilTheServerHasIt() async throws {
    let suite = "kyosei-tests-\(UUID().uuidString)"
    let defaults = try #require(UserDefaults(suiteName: suite))
    defer { defaults.removePersistentDomain(forName: suite) }
    let body = #"{"boxes":[{"text":"a prompt, and the last characters typed"}]}"#

    // The quit: the server does not answer within the limit, so the text is kept here.
    let outcome = await settleDraft(limit: .milliseconds(50)) {
        try? await Task.sleep(for: .seconds(30))
        return true
    }
    #expect(outcome == .unsaved)
    KeptDraft(server: "http://studio:47370", project: "kyosei", body: body).keep(in: defaults)

    // The next launch reads it for the same server and project, and for no other.
    let again = try #require(UserDefaults(suiteName: suite))
    #expect(KeptDraft.kept(in: again, server: "http://studio:47370", project: "kyosei")?.body == body)
    #expect(KeptDraft.kept(in: again, server: "http://studio:47370", project: "other") == nil)
    #expect(KeptDraft.kept(in: again, server: "http://laptop:47370", project: "kyosei") == nil)
    // It stays kept until the server has acknowledged it, and is then forgotten.
    #expect(KeptDraft.kept(in: again, server: "http://studio:47370", project: "kyosei") != nil)
    KeptDraft.forget(in: again)
    #expect(KeptDraft.kept(in: again, server: "http://studio:47370", project: "kyosei") == nil)
}
