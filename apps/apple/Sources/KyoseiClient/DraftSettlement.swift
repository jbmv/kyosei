import Foundation

/// How the composer's draft stood when the app was let go (KYO-782).
public enum DraftSettlement: Equatable, Sendable {
    /// The page held no save, and none was on its way.
    case nothingPending
    /// The server answered that it holds what was typed.
    case acknowledged
    /// The server refused the save, or did not answer within the limit.
    case unsaved
}

/// Settles the composer's draft before the app quits: `save` asks the page for a save it
/// is holding, sends it, and answers with the server's acknowledgement, or nil when there
/// was nothing to send. It is given `limit` to finish. The app quits when this returns, so
/// a server that does not answer delays the quit by the limit and no longer; the caller
/// then keeps the text on this machine.
///
/// The wait is not a task group's, which would itself wait for `save` to end: a request
/// to a server that has stopped answering ends only at its own time-out.
public func settleDraft(
    limit: Duration, save: @escaping @Sendable () async -> Bool?
) async -> DraftSettlement {
    let (outcomes, continuation) = AsyncStream.makeStream(of: DraftSettlement.self)
    let work = Task {
        let acknowledged = await save()
        continuation.yield(
            acknowledged == nil ? .nothingPending : acknowledged == true ? .acknowledged : .unsaved)
    }
    let timer = Task {
        do {
            try await Task.sleep(for: limit)
            continuation.yield(.unsaved)
        } catch {}
    }
    var first: DraftSettlement = .unsaved
    for await outcome in outcomes {
        first = outcome
        break
    }
    timer.cancel()
    work.cancel()
    continuation.finish()
    return first
}

/// The composer's prompts that the server had not acknowledged when the app quit (KYO-782),
/// kept on this machine and given back to the composer at the next launch. One is kept at
/// a time: the composer of the one window.
public struct KeptDraft: Codable, Equatable, Sendable {
    /// The server the prompts were being saved to, and the project they were written in.
    public let server: String
    public let project: String
    /// The body of the save that was not acknowledged: the page's `{"boxes": [...]}`, as JSON.
    public let body: String

    public init(server: String, project: String, body: String) {
        self.server = server
        self.project = project
        self.body = body
    }

    public static let key = "unsavedDraft"

    /// Keeps these prompts in place of any kept before.
    public func keep(in defaults: UserDefaults) {
        guard let data = try? JSONEncoder().encode(self) else { return }
        defaults.set(data, forKey: Self.key)
    }

    /// The prompts kept for this server and project, or nil. They stay kept until `forget`,
    /// which is called once the server has acknowledged them.
    public static func kept(in defaults: UserDefaults, server: String, project: String) -> KeptDraft? {
        guard
            let data = defaults.data(forKey: key),
            let draft = try? JSONDecoder().decode(KeptDraft.self, from: data),
            draft.server == server, draft.project == project
        else { return nil }
        return draft
    }

    public static func forget(in defaults: UserDefaults) {
        defaults.removeObject(forKey: key)
    }
}
