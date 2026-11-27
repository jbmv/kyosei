import Darwin
import Foundation
import KyoseiClient

// KYO-989: a stand-in for `kyo serve` whose connections go silent without closing, as they can
// across a sleep. It is `Fixtures/silent-relay.mjs`, run by Node, which the web tests of this
// app need too; the tests that use it are skipped where Node is not found.

/// Where Node is, from the PATH the tests were started with.
let nodePath: String? = {
    for directory in (ProcessInfo.processInfo.environment["PATH"] ?? "").split(separator: ":") {
        let candidate = "\(directory)/node"
        if FileManager.default.isExecutableFile(atPath: candidate) { return candidate }
    }
    return nil
}()

let opensslPath = ["/usr/bin/openssl", "/opt/homebrew/bin/openssl"].first {
    FileManager.default.isExecutableFile(atPath: $0)
}

let relayCanRun = nodePath != nil && opensslPath != nil

struct RelayFailure: Error, CustomStringConvertible {
    let description: String
}

/// One run of the stand-in, stopped when the test lets it go.
final class SilentRelay: @unchecked Sendable {
    private let process = Process()
    private let input = Pipe()
    private let output = Pipe()
    private let lock = NSLock()
    private var lines: [String] = []
    let directory: URL
    private(set) var port = 0

    var base: URL {
        URL(string: "\(plain ? "http" : "https")://127.0.0.1:\(port)")!
    }

    let plain: Bool

    /// Starts the stand-in: over HTTP/2 with TLS, or over plain HTTP that closes each
    /// connection after its answer when `plain`. After a wake, new connections are closed at
    /// once for `rejoin` seconds.
    init(plain: Bool = false, rejoin: Double = 0) throws {
        self.plain = plain
        directory = FileManager.default.temporaryDirectory.appending(
            path: "kyo989-relay-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        if !plain { try makeCertificate() }
        guard let nodePath else { throw RelayFailure(description: "no node on PATH") }
        let script = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appending(path: "Fixtures/silent-relay.mjs")
        process.executableURL = URL(fileURLWithPath: nodePath)
        process.arguments = [script.path]
        var environment = ProcessInfo.processInfo.environment
        environment["PLAIN"] = plain ? "1" : "0"
        environment["REJOIN_MS"] = String(Int(rejoin * 1000))
        environment["KEY"] = directory.appending(path: "key.pem").path
        environment["CERT"] = directory.appending(path: "cert.pem").path
        process.environment = environment
        process.standardInput = input
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let text = String(decoding: handle.availableData, as: UTF8.self)
            guard let self, !text.isEmpty else { return }
            self.lock.withLock {
                self.lines += text.split(separator: "\n").map(String.init)
            }
        }
        try process.run()
        let deadline = Date().addingTimeInterval(10)
        while Date() < deadline {
            if let line = said.first(where: { $0.hasPrefix("PORT ") }) {
                port = Int(line.dropFirst(5)) ?? 0
                return
            }
            Thread.sleep(forTimeInterval: 0.02)
        }
        stop()
        throw RelayFailure(description: "the stand-in did not say its port")
    }

    private func makeCertificate() throws {
        guard let opensslPath else { throw RelayFailure(description: "no openssl") }
        let openssl = Process()
        openssl.executableURL = URL(fileURLWithPath: opensslPath)
        openssl.arguments = [
            "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=127.0.0.1",
            "-keyout", directory.appending(path: "key.pem").path,
            "-out", directory.appending(path: "cert.pem").path,
        ]
        openssl.standardOutput = FileHandle.nullDevice
        openssl.standardError = FileHandle.nullDevice
        try openssl.run()
        openssl.waitUntilExit()
        guard openssl.terminationStatus == 0 else {
            throw RelayFailure(description: "openssl could not make a certificate")
        }
    }

    /// Everything the stand-in has printed.
    var said: [String] { lock.withLock { lines } }

    /// The paths it has answered, in order.
    var requests: [String] {
        said.filter { $0.hasPrefix("REQ ") }.map { String($0.dropFirst(4)) }
    }

    /// Every connection goes silent and stays open, and new ones are not answered.
    func sleep() { kill(process.processIdentifier, SIGUSR1) }

    /// New connections are answered again (after the rejoin time); the old stay silent.
    func wake() { kill(process.processIdentifier, SIGUSR2) }

    func stop() {
        output.fileHandleForReading.readabilityHandler = nil
        if process.isRunning {
            process.terminate()
            process.waitUntilExit()
        }
        try? FileManager.default.removeItem(at: directory)
    }
}

/// A URL session that trusts the stand-in's certificate, and otherwise is made as
/// `Connections` makes one, from the default configuration.
final class TrustingDelegate: NSObject, URLSessionTaskDelegate, Sendable {
    func urlSession(
        _ session: URLSession, didReceive challenge: URLAuthenticationChallenge
    ) async -> (URLSession.AuthChallengeDisposition, URLCredential?) {
        trust(challenge)
    }

    // A stream's challenge comes to the task's method.
    func urlSession(
        _ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge
    ) async -> (URLSession.AuthChallengeDisposition, URLCredential?) {
        trust(challenge)
    }

    private func trust(
        _ challenge: URLAuthenticationChallenge
    ) -> (URLSession.AuthChallengeDisposition, URLCredential?) {
        guard challenge.protectionSpace.host == "127.0.0.1",
            let trust = challenge.protectionSpace.serverTrust
        else { return (.performDefaultHandling, nil) }
        return (.useCredential, URLCredential(trust: trust))
    }
}

func trustingSession() -> URLSession {
    URLSession(configuration: .default, delegate: TrustingDelegate(), delegateQueue: nil)
}

/// Polls `done` until it holds or `within` has passed; whether it held.
func eventually(
    within: Duration, _ done: @escaping @Sendable () async -> Bool
) async -> Bool {
    let deadline = ContinuousClock.now + within
    while ContinuousClock.now < deadline {
        if await done() { return true }
        try? await Task.sleep(for: .milliseconds(50))
    }
    return await done()
}

/// A temporary file for a connection log.
func temporaryLog() -> URL {
    FileManager.default.temporaryDirectory.appending(path: "kyo989-log-\(UUID().uuidString).log")
}

func lines(of file: URL) -> [String] {
    ((try? String(contentsOf: file, encoding: .utf8)) ?? "").split(separator: "\n").map(String.init)
}
