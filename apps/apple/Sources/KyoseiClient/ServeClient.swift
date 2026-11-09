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

    public let model: String?
    public let effort: String?
    public let git_branch: String?
    public let context: Context?
    public let permission_mode: String?
    public let lines: Lines?
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
        ifNoneMatch: String? = nil
    ) async throws -> (data: Data, status: Int) {
        var request = URLRequest(url: url(path))
        request.httpMethod = method
        request.httpBody = body
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
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

    /// The session's events until the connection ends, which the caller answers by
    /// reconnecting with the last `transcript` frame's cursor.
    public func events(
        project: String, session: String, cursor: String?
    ) -> AsyncThrowingStream<ServerEvent, Error> {
        var request = URLRequest(
            url: url("v1/projects/\(project)/sessions/\(session)/events", cursor: cursor))
        request.timeoutInterval = 120
        let finalRequest = request
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (bytes, response) = try await URLSession.shared.bytes(for: finalRequest)
                    let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                    guard code == 200 else { throw ServeError.status(code, "") }
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
