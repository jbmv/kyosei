import Foundation

/// The app's record of its connection to `kyo serve` (KYO-989), for a person to hand over
/// after the chat stopped following its session: the system's sleep and wake, each change of
/// network path, each read or stream that failed with its error and the wait before the next
/// attempt, each renewal of the connections, and each return to live.
///
/// Each line begins with its time, in ISO 8601 with the local offset. The file is appended
/// to across launches; when a line would take it past `limit` bytes it is moved to the same
/// name with `.1` after it, replacing the one there, and a new file is begun, so the two
/// together hold no more than twice `limit`. A log with no file writes nothing.
public final class ConnectionLog: @unchecked Sendable {
    /// The app's own log, which writes nothing until `open(at:)` names its file.
    public static let shared = ConnectionLog()
    /// The size at which the file is moved aside.
    public static let defaultLimit = 512 * 1024

    private let lock = NSLock()
    private var file: URL?
    private let limit: Int
    private let stamps: ISO8601DateFormatter = {
        let stamps = ISO8601DateFormatter()
        stamps.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        stamps.timeZone = .current
        return stamps
    }()

    public init(file: URL? = nil, limit: Int = ConnectionLog.defaultLimit) {
        self.file = file
        self.limit = limit
    }

    /// The file lines are written to, or nil when the log writes nothing.
    public var location: URL? { lock.withLock { file } }

    /// The file moved aside when the log reached its limit.
    public var previous: URL? { location.map { $0.appendingPathExtension("1") } }

    /// Begins writing to `file`, making its folder when there is none.
    public func open(at file: URL) {
        try? FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        lock.withLock { self.file = file }
    }

    /// Appends one line, which is one line however `words` is broken.
    public func write(_ words: String, at date: Date = Date()) {
        let flat = words.split(whereSeparator: \.isNewline).joined(separator: " ")
        lock.withLock {
            guard let file else { return }
            let line = Data("\(stamps.string(from: date)) \(flat)\n".utf8)
            let manager = FileManager.default
            let size = (try? manager.attributesOfItem(atPath: file.path)[.size] as? Int) ?? 0
            if size > 0, size + line.count > limit {
                let aside = file.appendingPathExtension("1")
                try? manager.removeItem(at: aside)
                try? manager.moveItem(at: file, to: aside)
            }
            if let handle = try? FileHandle(forWritingTo: file) {
                defer { try? handle.close() }
                _ = try? handle.seekToEnd()
                try? handle.write(contentsOf: line)
            } else {
                try? line.write(to: file)
            }
        }
    }

    /// An error in the log's words: the status bar's sentence, and the system's code for a
    /// failure of the network, which the sentence does not always carry.
    public static func words(_ error: Error) -> String {
        let said = ServeClient.describe(error)
        if let failure = error as? URLError {
            return "\(said) (URLError \(failure.code.rawValue))"
        }
        return said
    }

    /// A wait in seconds, as the log states it.
    public static func words(_ wait: Duration) -> String {
        let seconds = Double(wait.components.seconds)
            + Double(wait.components.attoseconds) / 1e18
        return seconds == seconds.rounded()
            ? "\(Int(seconds))s" : String(format: "%.2fs", seconds)
    }
}
