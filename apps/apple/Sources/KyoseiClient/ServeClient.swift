import Foundation

/// One frame of a Server-Sent Events stream.
public struct ServerEvent: Equatable, Sendable {
    public let name: String
    public let data: String
    public let id: String?

    public init(name: String, data: String, id: String?) {
        self.name = name
        self.data = data
        self.id = id
    }
}

/// Assembles frames from a stream's lines. A blank line ends a frame, and a comment
/// (a line beginning with a colon) is the server's keepalive.
public struct ServerEventParser: Sendable {
    private var name: String?
    private var dataLines: [String] = []
    private var id: String?

    public init() {}

    public mutating func feed(line: String) -> ServerEvent? {
        if line.isEmpty {
            defer {
                name = nil
                dataLines = []
                id = nil
            }
            guard !dataLines.isEmpty else { return nil }
            return ServerEvent(
                name: name ?? "message", data: dataLines.joined(separator: "\n"), id: id)
        }
        if line.hasPrefix(":") { return nil }
        let parts = line.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
        let field = String(parts[0])
        var value = parts.count > 1 ? String(parts[1]) : ""
        if value.hasPrefix(" ") { value.removeFirst() }
        switch field {
        case "event": name = value
        case "data": dataLines.append(value)
        case "id": id = value
        default: break
        }
        return nil
    }
}

public struct SessionRow: Decodable, Sendable {
    public let session_id: String
    public let live: Bool
    public let has_pane: Bool
    public let cwd: String?
    /// The title the session's transcript holds, or none.
    public let title: String?

    public init(
        session_id: String, live: Bool, has_pane: Bool, cwd: String? = nil, title: String? = nil
    ) {
        self.session_id = session_id
        self.live = live
        self.has_pane = has_pane
        self.cwd = cwd
        self.title = title
    }
}

/// One project of the server's registry, as `GET /v1/projects` lists it (KYO-761).
public struct ProjectRow: Decodable, Equatable, Sendable {
    public let name: String
    /// Whether the project's store can be opened; `problem` says why when it cannot.
    public let can_open: Bool
    public let problem: String?

    public init(name: String, can_open: Bool, problem: String? = nil) {
        self.name = name
        self.can_open = can_open
        self.problem = problem
    }
}

private struct ProjectList: Decodable {
    let projects: [ProjectRow]
}

/// What the page's project dropdown and session links are drawn from (KYO-761): the window's
/// project, the server's projects, and each project's live sessions.
public enum ProjectsFrame {
    /// The frame as JSON, with its keys in order so that an unchanged frame is the same text.
    /// `projects` is nil before the server's list has been read, and `problem` says why a
    /// read failed. `live` holds, for each project whose sessions were read, the sessions a
    /// process is running on, in the server's order; a session the server shows no pane for
    /// is carried with `has_pane` false, and the page decides which one a link follows.
    public static func json(
        project: String, projects: [ProjectRow]?, problem: String?, live: [String: [SessionRow]]
    ) -> String {
        var frame: [String: Any] = [
            "project": project,
            "problem": problem ?? NSNull(),
            "projects": NSNull(),
            "live": live.mapValues { rows in
                rows.filter(\.live).map { row -> [String: Any] in
                    [
                        "session_id": row.session_id, "title": row.title ?? NSNull(),
                        "has_pane": row.has_pane,
                    ]
                }
            },
        ]
        if let projects {
            frame["projects"] = projects.map { row -> [String: Any] in
                ["name": row.name, "can_open": row.can_open, "problem": row.problem ?? NSNull()]
            }
        }
        let data = try? JSONSerialization.data(withJSONObject: frame, options: [.sortedKeys])
        return data.map { String(decoding: $0, as: UTF8.self) } ?? "null"
    }
}

/// The session as of its latest reply, which the status strip shows.
public struct SessionFacts: Decodable, Equatable, Sendable {
    public struct Context: Decodable, Equatable, Sendable {
        public let percent: Int?
    }

    public struct Lines: Decodable, Equatable, Sendable {
        public let added: Int
        public let removed: Int
    }

    /// The subscription's usage limits, as Claude Code last reported them (KYO-714).
    public struct Limits: Decodable, Equatable, Sendable {
        public struct Window: Decodable, Equatable, Sendable {
            public let used_percentage: Double
            public let resets_at: String?
        }

        public let five_hour: Window?
        public let seven_day: Window?
    }

    public let model: String?
    public let effort: String?
    public let git_branch: String?
    public let context: Context?
    public let permission_mode: String?
    public let lines: Lines?
    public let limits: Limits?
    /// The weekly figure first read for this session (KYO-717).
    public let weekly_at_start: Limits.Window?

    /// Whole points of the weekly window used since this session's first figure, or nil
    /// when that is unknown, the window has reset since, or the difference rounds to zero.
    public var weeklyUsedThisSession: Int? {
        guard let now = limits?.seven_day, let start = weekly_at_start,
            let reset = now.resets_at, reset == start.resets_at
        else { return nil }
        let points = Int((now.used_percentage - start.used_percentage).rounded())
        return points > 0 ? points : nil
    }
}

/// Any response or frame carrying `facts`.
public struct FactsCarrier: Decodable, Sendable {
    public let facts: SessionFacts?
}

private struct SessionList: Decodable {
    let sessions: [SessionRow]
}

public enum ServeError: Error, CustomStringConvertible {
    case status(Int, String)

    public var description: String {
        switch self {
        case .status(let code, let body): return "kyo serve answered \(code): \(body)"
        }
    }
}

/// A client of one `kyo serve`. Responses a web view will draw are returned as bytes and
/// are not decoded here, so that a client holds no second copy of the server's schema.
public struct ServeClient: Sendable {
    public let base: URL

    public init(base: URL) {
        self.base = base
    }

    private func url(_ path: String, cursor: String? = nil) -> URL {
        var components = URLComponents(
            url: base.appending(path: path), resolvingAgainstBaseURL: false)!
        if let cursor { components.queryItems = [URLQueryItem(name: "cursor", value: cursor)] }
        return components.url!
    }

    private func get(_ path: String) async throws -> Data {
        let (data, response) = try await URLSession.shared.data(from: url(path))
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200 else {
            throw ServeError.status(code, String(decoding: data, as: UTF8.self))
        }
        return data
    }

    /// The projects the server serves, in its registry's order (KYO-761).
    public func projects() async throws -> [ProjectRow] {
        try Self.projects(in: await get("v1/projects"))
    }

    static func projects(in data: Data) throws -> [ProjectRow] {
        try JSONDecoder().decode(ProjectList.self, from: data).projects
    }

    public func sessions(project: String) async throws -> [SessionRow] {
        let data = try await get("v1/projects/\(project)/sessions")
        return try JSONDecoder().decode(SessionList.self, from: data).sessions
    }

    public func sessionsData(project: String) async throws -> Data {
        try await get("v1/projects/\(project)/sessions")
    }

    public func subagent(project: String, session: String, agent: String) async throws -> Data {
        try await get("v1/projects/\(project)/sessions/\(session)/subagents/\(agent)")
    }

    public func changes(project: String, session: String) async throws -> Data {
        try await get("v1/projects/\(project)/sessions/\(session)/changes")
    }

    /// A request a device token authorises. Returns the body and status for the caller to read.
    public func authorised(
        _ method: String, _ path: String, token: String, body: Data? = nil, ifMatch: String? = nil,
        ifNoneMatch: String? = nil, contentType: String = "application/json"
    ) async throws -> (data: Data, status: Int) {
        var request = URLRequest(url: url(path))
        request.httpMethod = method
        request.httpBody = body
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if body != nil { request.setValue(contentType, forHTTPHeaderField: "Content-Type") }
        if let ifMatch { request.setValue(ifMatch, forHTTPHeaderField: "If-Match") }
        if let ifNoneMatch { request.setValue(ifNoneMatch, forHTTPHeaderField: "If-None-Match") }
        let (data, response) = try await URLSession.shared.data(for: request)
        return (data, (response as? HTTPURLResponse)?.statusCode ?? 0)
    }

    /// One image the person sent, and its media type.
    public func image(
        project: String, session: String, turn: String, index: Int
    ) async throws -> (data: Data, mediaType: String) {
        let path = "v1/projects/\(project)/sessions/\(session)/images/\(turn)/\(index)"
        let (data, response) = try await URLSession.shared.data(from: url(path))
        let http = response as? HTTPURLResponse
        guard http?.statusCode == 200 else {
            throw ServeError.status(http?.statusCode ?? 0, String(decoding: data, as: UTF8.self))
        }
        return (data, http?.value(forHTTPHeaderField: "Content-Type") ?? "image/png")
    }

    public func turns(project: String, session: String) async throws -> Data {
        try await get("v1/projects/\(project)/sessions/\(session)/turns")
    }

    /// What the session's pane shows now, read afresh by the server's screen route and apart
    /// from any stream. Nil when the route cannot answer, which says nothing about a stream.
    public func screen(project: String, session: String) async -> PaneSnapshot? {
        var request = URLRequest(url: url("v1/projects/\(project)/sessions/\(session)/screen"))
        request.timeoutInterval = 10
        guard let (data, response) = try? await URLSession.shared.data(for: request),
            (response as? HTTPURLResponse)?.statusCode == 200
        else { return nil }
        var snapshot = PaneSnapshot()
        return snapshot.update(from: data) ? snapshot : nil
    }

    /// How long an open stream may carry no bytes at all before it is given up. The server
    /// sends a keepalive after 15 seconds without an event, so three missed keepalives mean
    /// the connection is gone though the socket has not said so (KYO-725).
    public static let streamIdleLimit: TimeInterval = 45

    /// The session's events until the connection ends, which the caller answers by
    /// reconnecting with the last `transcript` frame's cursor. A connection that carries
    /// nothing for `idleLimit` seconds ends with an error.
    public func events(
        project: String, session: String, cursor: String?,
        idleLimit: TimeInterval = ServeClient.streamIdleLimit
    ) -> AsyncThrowingStream<ServerEvent, Error> {
        var request = URLRequest(
            url: url("v1/projects/\(project)/sessions/\(session)/events", cursor: cursor))
        request.timeoutInterval = idleLimit
        let finalRequest = request
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (bytes, response) = try await URLSession.shared.bytes(for: finalRequest)
                    let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                    guard code == 200 else {
                        // The refusal's body is the server's own sentence, which says why.
                        var body: [UInt8] = []
                        for try await byte in bytes where body.count < 65536 { body.append(byte) }
                        throw ServeError.status(code, String(decoding: body, as: UTF8.self))
                    }
                    var parser = ServerEventParser()
                    var line: [UInt8] = []
                    // Read by byte: `bytes.lines` drops the blank line that ends a frame.
                    for try await byte in bytes {
                        guard byte == UInt8(ascii: "\n") else {
                            line.append(byte)
                            continue
                        }
                        if let event = parser.feed(line: String(decoding: line, as: UTF8.self)) {
                            continuation.yield(event)
                        }
                        line.removeAll(keepingCapacity: true)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

extension ServeClient {
    /// An error from a request or a stream, in words for the status bar.
    public static func describe(_ error: Error) -> String {
        if case ServeError.status(let code, let body) = error {
            let object = try? JSONSerialization.jsonObject(with: Data(body.utf8)) as? [String: Any]
            guard let said = object?["error"] as? String else { return "kyo serve answered \(code)." }
            return "kyo serve answered \(code): \(said)"
        }
        if let failure = error as? URLError {
            switch failure.code {
            case .cannotConnectToHost, .cannotFindHost, .networkConnectionLost,
                .notConnectedToInternet, .dnsLookupFailed:
                return "kyo serve cannot be reached."
            case .timedOut:
                return "kyo serve sent nothing for too long."
            default:
                return failure.localizedDescription
            }
        }
        return String(describing: error)
    }
}

/// What a session's pane shows, as both the event stream and the screen route state it: the
/// screen's classification and the terminal's words, which `kyo serve` derives from one capture
/// in the same way for each.
public struct PaneSnapshot: Equatable, Sendable {
    public var state: String?
    public var text: String?
    public var workingLine: String?

    public init(state: String? = nil, text: String? = nil, workingLine: String? = nil) {
        self.state = state
        self.text = text
        self.workingLine = workingLine
    }

    /// Takes what a document states of the pane: its `screen`, its `terminal`, or both, as a
    /// `ready`, `screen` or `terminal` frame or the screen route carries them. A key that is
    /// absent leaves its part as it was. Returns whether the document stated either.
    @discardableResult
    public mutating func update(from json: Data) -> Bool {
        guard let object = try? JSONSerialization.jsonObject(with: json) as? [String: Any] else {
            return false
        }
        var stated = false
        if let screen = object["screen"] {
            state = (screen as? [String: Any])?["state"] as? String
            stated = true
        }
        if let terminal = object["terminal"] {
            let words = terminal as? [String: Any]
            text = words?["text"] as? String
            workingLine = words?["working_line"] as? String
            stated = true
        }
        return stated
    }
}

/// The waits between attempts at something that keeps failing, doubling from `first` up to
/// `longest`, so that a server that is down is not asked for a whole transcript every second.
public struct Backoff: Sendable {
    public let first: Duration
    public let longest: Duration
    private var failures = 0

    public init(first: Duration = .seconds(1), longest: Duration = .seconds(30)) {
        self.first = first
        self.longest = longest
    }

    public mutating func next() -> Duration {
        let wait = first * (1 << min(failures, 16))
        failures += 1
        return min(wait, longest)
    }

    public mutating func reset() {
        failures = 0
    }
}

/// Whether an open stream still carries its session (KYO-725).
///
/// `kyo serve` sends a frame when the transcript, the pane or the session's facts change,
/// and a keepalive when it has sent nothing for a while. The keepalive comes from the
/// connection and not from the server's reader of the session, so a stream whose reader has
/// stopped goes on looking open: the chat stops while the socket stays up. When the stream
/// has been quiet for `quiet`, the pane is read afresh through the screen route and compared
/// with what the stream last said of it. The reader samples the pane twice a second and sends
/// any change, so a pane that differs on two fresh reads in a row, with no frame from the
/// reader between them, has changed without the stream saying so.
public struct StreamWatch: Sendable {
    /// The frames the server's reader of the session sends. `held` is announced by the
    /// route that holds the message, and keeps arriving when the reader has stopped.
    /// `resumed` says a pass has succeeded after an `error` frame (KYO-728), which only a
    /// reader that is reading can say. `agenda` is the reader's too (KYO-770): it looks for a
    /// change to the session's task list on each pass, whoever saved it.
    public static let readerFrames: Set<String> = [
        "transcript", "reply", "facts", "screen", "terminal", "resumed", "agenda",
    ]
    /// Fresh reads that must differ in a row before the stream is held to be stale.
    public static let differingReads = 2

    public let quiet: Duration
    /// What the stream last said the pane shows.
    public private(set) var stated = PaneSnapshot()
    /// The stream has sent its `ready` frame.
    public private(set) var ready = false
    /// A frame from the reader has arrived since `ready`, or a fresh read matched the stream:
    /// the stream is known to carry the session, which `ready` alone does not show, since it
    /// is built from what the reader last saw.
    public private(set) var confirmed = false
    private var heard: ContinuousClock.Instant?
    private var differing = 0

    public init(quiet: Duration = .seconds(10)) {
        self.quiet = quiet
    }

    public mutating func received(_ event: ServerEvent, at now: ContinuousClock.Instant) {
        if event.name == "ready" {
            ready = true
        } else if ready && Self.readerFrames.contains(event.name) {
            // The catch-up frame comes before `ready` and is the route's own read.
            confirmed = true
        } else {
            return
        }
        stated.update(from: Data(event.data.utf8))
        heard = now
        differing = 0
    }

    /// The stream has been quiet long enough that the pane should be read afresh.
    public func wantsRead(at now: ContinuousClock.Instant) -> Bool {
        guard ready, let heard else { return false }
        return now - heard >= quiet
    }

    /// Takes a fresh read of the pane, nil when it could not be read; true when the stream is
    /// stale.
    public mutating func read(_ fresh: PaneSnapshot?, at now: ContinuousClock.Instant) -> Bool {
        heard = now
        guard let fresh else { return false }
        if fresh == stated {
            differing = 0
            confirmed = true
            return false
        }
        differing += 1
        return differing >= Self.differingReads
    }
}

/// What a `SessionFollower` hands its reader.
public enum FollowUpdate: Sendable, Equatable {
    case event(ServerEvent)
    /// The stream carries the session as it is now.
    case live
    /// The chat is not live: since when, and why, in words.
    case notLive(since: Date, reason: String)
}

/// Follows one session's events across lost connections, a restarted server and refusals,
/// reconnecting with the last transcript cursor so that nothing is missed and nothing is
/// drawn twice (KYO-725). It ends after a `reset` or `session` frame, which ask the reader to
/// read the session again from the top, and after a refusal (a 4xx answer), which reading
/// again may answer differently.
public struct SessionFollower: Sendable {
    public let client: ServeClient
    public let project: String
    public let session: String
    public var idleLimit = ServeClient.streamIdleLimit
    public var quiet = Duration.seconds(10)
    public var backoff = Backoff()

    public static let stopped = "The stream stopped carrying the session's changes."
    public static let closed = "kyo serve closed the stream."

    public init(client: ServeClient, project: String, session: String) {
        self.client = client
        self.project = project
        self.session = session
    }

    public func updates(cursor: String?) -> AsyncStream<FollowUpdate> {
        AsyncStream { continuation in
            let task = Task {
                await run(cursor: cursor, continuation)
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private enum Outcome: Sendable {
        case ended
        case refused(String)
        case lost(String, stale: Bool)
    }

    private func run(cursor: String?, _ out: AsyncStream<FollowUpdate>.Continuation) async {
        let progress = FollowProgress(cursor: cursor)
        var backoff = backoff
        var since: Date?
        while !Task.isCancelled {
            let outcome = await connect(progress, out)
            if progress.takeWasLive() {
                backoff.reset()
                since = nil
            }
            if Task.isCancelled { return }
            let start = since ?? Date()
            switch outcome {
            case .ended:
                return
            case .refused(let reason):
                out.yield(.notLive(since: start, reason: reason))
                return
            case .lost(let reason, let stale):
                since = start
                if stale { progress.requireProof() }
                out.yield(.notLive(since: start, reason: reason))
            }
            try? await Task.sleep(for: backoff.next())
        }
    }

    private func connect(
        _ progress: FollowProgress, _ out: AsyncStream<FollowUpdate>.Continuation
    ) async -> Outcome {
        let cursor = progress.beginConnection(quiet: quiet)
        do {
            return try await withThrowingTaskGroup(of: Outcome.self) { group in
                group.addTask {
                    let events = client.events(
                        project: project, session: session, cursor: cursor, idleLimit: idleLimit)
                    for try await event in events {
                        if event.name == "error" {
                            // The server could not read the session (KYO-728). With
                            // `stopped` its reader has ended and the next connection is
                            // given a new one, whose `ready` is a fresh read. Without it
                            // the same reader goes on trying, and its `ready` is what it
                            // read before failing: the next connection is live only once
                            // a reader frame (`resumed` among them) or a fresh read of the
                            // pane shows it, so the chat does not turn live and back on
                            // every attempt while the failure lasts.
                            let object =
                                try? JSONSerialization.jsonObject(with: Data(event.data.utf8))
                                as? [String: Any]
                            let said = object?["error"] as? String
                            let stopped = object?["stopped"] as? Bool ?? false
                            return .lost(
                                said ?? "kyo serve could not read the session.", stale: !stopped)
                        }
                        let becameLive = progress.received(event, at: .now)
                        out.yield(.event(event))
                        if becameLive { out.yield(.live) }
                        if event.name == "reset" || event.name == "session" { return .ended }
                    }
                    return .lost(Self.closed, stale: false)
                }
                group.addTask {
                    while true {
                        try await Task.sleep(for: quiet / 4)
                        guard progress.wantsRead(at: .now) else { continue }
                        let fresh = await client.screen(project: project, session: session)
                        let (stale, becameLive) = progress.read(fresh, at: .now)
                        if becameLive { out.yield(.live) }
                        if stale { return .lost(Self.stopped, stale: true) }
                    }
                }
                defer { group.cancelAll() }
                return try await group.next() ?? .ended
            }
        } catch {
            if Task.isCancelled { return .ended }
            if case ServeError.status(let code, _) = error, (400..<500).contains(code) {
                return .refused(ServeClient.describe(error))
            }
            return .lost(ServeClient.describe(error), stale: false)
        }
    }
}

/// A follower's state across its connections, shared by the task reading the stream and the
/// task reading the pane afresh.
private final class FollowProgress: @unchecked Sendable {
    private let lock = NSLock()
    private var cursor: String?
    private var watch = StreamWatch()
    private var live = false
    private var wasLive = false
    /// The last connection was found stale or carried an `error`, so the next one's `ready`
    /// is not taken as proof: a reader that is stuck or failing answers a new subscriber
    /// with what it last saw.
    private var needsProof = false

    init(cursor: String?) {
        self.cursor = cursor
    }

    func beginConnection(quiet: Duration) -> String? {
        lock.withLock {
            watch = StreamWatch(quiet: quiet)
            live = false
            return cursor
        }
    }

    func requireProof() {
        lock.withLock { needsProof = true }
    }

    /// Whether this connection was live at some point since the last call.
    func takeWasLive() -> Bool {
        lock.withLock {
            defer { wasLive = false }
            return wasLive
        }
    }

    /// Takes a frame; true when the stream has just become live.
    func received(_ event: ServerEvent, at now: ContinuousClock.Instant) -> Bool {
        lock.withLock {
            if event.name == "transcript", let id = event.id { cursor = id }
            watch.received(event, at: now)
            return declareLiveIfDue()
        }
    }

    func wantsRead(at now: ContinuousClock.Instant) -> Bool {
        lock.withLock { watch.wantsRead(at: now) }
    }

    /// Takes a fresh read of the pane: whether the stream is stale, and whether it has just
    /// become live.
    func read(_ fresh: PaneSnapshot?, at now: ContinuousClock.Instant) -> (Bool, Bool) {
        lock.withLock {
            let stale = watch.read(fresh, at: now)
            return (stale, stale ? false : declareLiveIfDue())
        }
    }

    private func declareLiveIfDue() -> Bool {
        guard !live, needsProof ? watch.confirmed : watch.ready else { return false }
        live = true
        wasLive = true
        needsProof = false
        return true
    }
}

// MARK: The Records view (KYO-732)

extension ServeClient {
    /// The bytes `kyo <command> <positionals> --<flag>=<value> --json` prints, and the status
    /// the server gave them. A refusal's body is the server's own document, so it is returned
    /// rather than thrown. A flag given twice is sent twice, as `kyo gates --type` takes it.
    public func command(
        project: String, _ command: String, positionals: [String] = [],
        flags: [(name: String, value: String)] = []
    ) async throws -> (data: Data, status: Int) {
        var address = base.appending(path: "v1/projects/\(project)/cmd/\(command)")
        for positional in positionals { address.append(path: positional) }
        var components = URLComponents(url: address, resolvingAgainstBaseURL: false)!
        if !flags.isEmpty {
            components.queryItems = flags.map { URLQueryItem(name: $0.name, value: $0.value) }
        }
        let (data, response) = try await URLSession.shared.data(from: components.url!)
        return (data, (response as? HTTPURLResponse)?.statusCode ?? 0)
    }

    /// The project's change events until the connection ends: a `changed` frame names the
    /// kind of thing that changed (`store`, `ideas`, `sessions` or `scratchpad`). A connection
    /// that carries nothing for `idleLimit` seconds, keepalives included, ends with an error.
    public func projectEvents(
        project: String, idleLimit: TimeInterval = ServeClient.streamIdleLimit
    ) -> AsyncThrowingStream<ServerEvent, Error> {
        var request = URLRequest(url: base.appending(path: "v1/projects/\(project)/events"))
        request.timeoutInterval = idleLimit
        let finalRequest = request
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (bytes, response) = try await URLSession.shared.bytes(for: finalRequest)
                    let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                    guard code == 200 else {
                        var body: [UInt8] = []
                        for try await byte in bytes where body.count < 65536 { body.append(byte) }
                        throw ServeError.status(code, String(decoding: body, as: UTF8.self))
                    }
                    var parser = ServerEventParser()
                    var line: [UInt8] = []
                    for try await byte in bytes {
                        guard byte == UInt8(ascii: "\n") else {
                            line.append(byte)
                            continue
                        }
                        if let event = parser.feed(line: String(decoding: line, as: UTF8.self)) {
                            continuation.yield(event)
                        }
                        line.removeAll(keepingCapacity: true)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

// MARK: The Files view (KYO-736)

extension ServeClient {
    /// What a file route answered: the bytes, the status, and the two headers a client reads.
    public struct FileReply: Sendable {
        public let data: Data
        public let status: Int
        public let etag: String?
        public let contentType: String?
    }

    /// The characters a path segment or a query value is sent as itself. Everything else is
    /// percent-encoded, so that a `?`, a `#`, a `%` or a `+` in a file's name is part of the
    /// name: the resolve route reads its query as a form, where a bare `+` is a space.
    private static let unreserved = CharacterSet(
        charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")

    /// The address of a file route: `segments` under the project's `files`, each encoded as
    /// one segment, and `query` after them.
    public func filesURL(
        project: String, segments: [String], query: [(name: String, value: String)] = []
    ) -> URL {
        func encoded(_ text: String) -> String {
            text.addingPercentEncoding(withAllowedCharacters: Self.unreserved) ?? text
        }
        var components = URLComponents(
            url: base.appending(path: "v1/projects/\(project)/files"),
            resolvingAgainstBaseURL: false)!
        if !segments.isEmpty {
            components.percentEncodedPath += "/" + segments.map(encoded).joined(separator: "/")
        }
        if !query.isEmpty {
            components.percentEncodedQuery =
                query.map { "\(encoded($0.name))=\(encoded($0.value))" }.joined(separator: "&")
        }
        return components.url!
    }

    /// Reads one file route (see "Reading a project's files" in the reference). A refusal's
    /// body is the server's own document, so it is returned rather than thrown. With
    /// `ifNoneMatch` the blob route answers 304 and no bytes when the file is unchanged; the
    /// system's own cache is not consulted, so that the status is the server's.
    public func files(
        project: String, segments: [String], query: [(name: String, value: String)] = [],
        token: String?, ifNoneMatch: String? = nil
    ) async throws -> FileReply {
        var request = URLRequest(url: filesURL(project: project, segments: segments, query: query))
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let ifNoneMatch { request.setValue(ifNoneMatch, forHTTPHeaderField: "If-None-Match") }
        let (data, response) = try await URLSession.shared.data(for: request)
        let http = response as? HTTPURLResponse
        return FileReply(
            data: data, status: http?.statusCode ?? 0,
            etag: http?.value(forHTTPHeaderField: "ETag"),
            contentType: http?.value(forHTTPHeaderField: "Content-Type"))
    }
}
