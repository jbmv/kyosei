import AppKit
import KyoseiClient
import Network

/// Watches what the system says of the machine's sleep and of its network (KYO-989), writes
/// each to the connection log, and asks for the feed's reads to start again when the machine
/// wakes and when a network path returns after there was none.
///
/// A wake is not left to the timers of the reads that were out. Those timers may not run
/// while the machine sleeps, and a connection that went silent across the sleep is kept by
/// the system's URL session, which sends the next requests on it as well (see `Connections`).
@MainActor
final class SystemWatch {
    private let log: ConnectionLog
    private let reconnect: @MainActor (String) -> Void
    private var observers: [NSObjectProtocol] = []
    private let monitor = NWPathMonitor()
    /// Whether the last path was satisfied, nil before the first.
    private var satisfied: Bool?
    private var lastPath = ""

    init(log: ConnectionLog, reconnect: @escaping @MainActor (String) -> Void) {
        self.log = log
        self.reconnect = reconnect
        let center = NSWorkspace.shared.notificationCenter
        observers.append(
            center.addObserver(
                forName: NSWorkspace.willSleepNotification, object: nil, queue: .main
            ) { [weak self] _ in
                MainActor.assumeIsolated { self?.log.write("sleep") }
            })
        observers.append(
            center.addObserver(
                forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
            ) { [weak self] _ in
                MainActor.assumeIsolated { self?.woke() }
            })
        monitor.pathUpdateHandler = { [weak self] path in
            let words = Self.words(path)
            let good = path.status == .satisfied
            Task { @MainActor in self?.took(words, satisfied: good) }
        }
        monitor.start(queue: DispatchQueue(label: "kyosei.network-path"))
    }

    func stop() {
        monitor.cancel()
        for observer in observers { NSWorkspace.shared.notificationCenter.removeObserver(observer) }
        observers = []
    }

    private func woke() {
        log.write("wake")
        reconnect("the machine woke")
    }

    /// A path in the log's words: its status, and the interfaces it may use with their kinds.
    nonisolated static func words(_ path: NWPath) -> String {
        let status =
            switch path.status {
            case .satisfied: "satisfied"
            case .unsatisfied: "unsatisfied"
            case .requiresConnection: "requires a connection"
            @unknown default: "of unknown status"
            }
        let interfaces = path.availableInterfaces.map { interface -> String in
            let kind =
                switch interface.type {
                case .wifi: "wifi"
                case .wiredEthernet: "ethernet"
                case .cellular: "cellular"
                case .loopback: "loopback"
                case .other: "other"
                @unknown default: "unknown"
                }
            return "\(interface.name) (\(kind))"
        }
        return "network path \(status)"
            + (interfaces.isEmpty ? ", no interface" : ": " + interfaces.joined(separator: ", "))
    }

    /// Takes a path update: a change is written, and a path that is satisfied again after one
    /// that was not starts the reads again, which would otherwise wait out their backoff.
    func took(_ words: String, satisfied good: Bool) {
        if words != lastPath { log.write(words) }
        lastPath = words
        let was = satisfied
        satisfied = good
        if was == false, good { reconnect("the network path is satisfied again") }
    }
}
