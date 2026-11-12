import AppKit
import KyoseiClient
import SwiftUI
import WebKit

struct ChatView: View {
    let feed: SessionFeed

    var body: some View {
        VStack(spacing: 0) {
            ChatWebView(feed: feed)
            Divider()
            HStack(spacing: 14) {
                Text(feed.project.isEmpty ? "No project" : feed.project)
                    .fontWeight(.semibold).foregroundStyle(.primary)
                if let percent = feed.facts?.context?.percent {
                    ContextMeter(percent: percent)
                }
                if let limits = feed.facts?.limits {
                    if let window = limits.five_hour { LimitLabel(name: "5-hour", window: window) }
                    if let window = limits.seven_day {
                        LimitLabel(
                            name: "Weekly", window: window,
                            usedThisSession: feed.facts?.weeklyUsedThisSession)
                    }
                }
                if let facts = feed.facts {
                    // The effort follows the model's name, as in `claude-opus-5 high`.
                    Text(
                        [facts.model ?? "model unknown", facts.effort]
                            .compactMap { $0 }.joined(separator: " "))
                }
                if let mode = feed.facts?.permission_mode { Text(mode) }
                if let lines = feed.facts?.lines {
                    HStack(spacing: 4) {
                        Text("+\(lines.added.formatted())")
                        Text("−\(lines.removed.formatted())")
                    }
                    .monospacedDigit()
                }
                RunningAgentsLabel(agents: feed.runningAgents, count: feed.runningSubagents)
                Spacer()
                // With no project chosen there is no session to be live or not.
                if let needed = feed.tokenNeeded {
                    // Said whichever project the window shows, the list of projects included:
                    // without a token the server lists no session at all (KYO-791).
                    TokenNeededLabel(reason: needed)
                } else if !feed.project.isEmpty {
                    FeedStateLabel(state: feed.state)
                }
                if let remote = feed.remote {
                    HStack(spacing: 4) {
                        Text(remote)
                        if let ahead = feed.ahead, ahead > 0 {
                            Text("↑\(ahead)").monospacedDigit()
                                .help("\(ahead) commit\(ahead == 1 ? "" : "s") not on the remote")
                        }
                    }
                }
                if let branch = feed.facts?.git_branch {
                    HStack(spacing: 4) {
                        Image(systemName: "arrow.triangle.branch")
                        Text(branch)
                    }
                }
            }
            .font(.system(size: 11))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 12)
            .frame(height: 24)
        }
    }
}

/// Says when the chat is not following its session, so that a stale chat is not read as a
/// current one (KYO-725), and nothing while it is. Hovering shows why, and that the feed is
/// connecting again.
struct FeedStateLabel: View {
    let state: FeedState
    @State private var shown = false

    private static func moment(_ date: Date) -> String {
        Calendar.current.isDateInToday(date)
            ? date.formatted(date: .omitted, time: .shortened)
            : date.formatted(date: .abbreviated, time: .shortened)
    }

    private static func sentence(_ reason: String) -> String {
        let said = reason.hasSuffix(".") ? reason : reason + "."
        return said + " Connecting again."
    }

    var body: some View {
        switch state {
        case .live:
            EmptyView()
        case .connecting:
            Text("Connecting…")
        case .noSession:
            Text("No session")
        case .notLive(let since, let reason):
            HStack(spacing: 4) {
                Image(systemName: "exclamationmark.triangle.fill")
                Text("Not live since \(Self.moment(since))")
            }
            .foregroundStyle(.orange)
            .contentShape(Rectangle())
            .onHover { shown = $0 }
            .onTapGesture { shown.toggle() }
            .popover(isPresented: $shown, arrowEdge: .top) {
                Text(Self.sentence(reason))
                    .font(.system(size: 12))
                    .frame(maxWidth: 320, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(10)
            }
        }
    }
}

/// Says that the server shows this device no session until it holds a device token it accepts
/// (KYO-791). The words are always shown, and pressing or hovering gives the sentence that
/// says what is wrong with the token and how one is issued.
struct TokenNeededLabel: View {
    let reason: String
    @State private var shown = false

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "key.fill")
            Text("Device token needed")
        }
        .foregroundStyle(.orange)
        .contentShape(Rectangle())
        .onHover { shown = $0 }
        .onTapGesture { shown.toggle() }
        .popover(isPresented: $shown, arrowEdge: .top) {
            Text(reason)
                .font(.system(size: 12))
                .frame(maxWidth: 320, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
                .padding(10)
        }
    }
}

/// How full the context is: the number, and a bar coloured by how near the limit it is.
struct ContextMeter: View {
    let percent: Int

    private var colour: Color {
        switch percent {
        case ..<40: .green
        case ..<60: .blue
        case ..<85: .orange
        default: .red
        }
    }

    var body: some View {
        HStack(spacing: 6) {
            Text("context \(percent)%").monospacedDigit()
            Capsule().fill(.quaternary)
                .frame(width: 64, height: 6)
                .overlay(alignment: .leading) {
                    Capsule().fill(colour)
                        .frame(width: 64 * CGFloat(min(max(percent, 0), 100)) / 100)
                }
        }
    }
}

/// How much of one usage window is spent. Hovering shows when the window resets.
struct LimitLabel: View {
    let name: String
    let window: SessionFacts.Limits.Window
    /// Points of this window used since the session's first reading, when known (KYO-717).
    var usedThisSession: Int?

    private var resets: String {
        let stamps = ISO8601DateFormatter()
        let plain = stamps.date(from: window.resets_at ?? "")
        stamps.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = plain ?? stamps.date(from: window.resets_at ?? "") else {
            return "Reset time unknown"
        }
        return "Resets \(date.formatted(date: .abbreviated, time: .shortened))"
    }

    @State private var shown = false

    var body: some View {
        Text(
            "\(name): \(Int(window.used_percentage.rounded()))%"
                + (usedThisSession.map { " ↓\($0)" } ?? "")
        )
            .monospacedDigit()
            .foregroundStyle(window.used_percentage >= 90 ? Color.red : window.used_percentage >= 75 ? Color.orange : Color.secondary)
            .contentShape(Rectangle())
            // A tooltip did not appear over the status bar, so the reset time opens on its own.
            .onHover { shown = $0 }
            .onTapGesture { shown.toggle() }
            .popover(isPresented: $shown, arrowEdge: .top) {
                Text(resets).font(.system(size: 12)).padding(10)
            }
    }
}

/// `Subagents: N`, which opens a list of the running ones when clicked.
struct RunningAgentsLabel: View {
    let agents: [RunningAgent]
    let count: Int
    @State private var shown = false
    /// The glow's phase, which only moves while a subagent runs and Reduce Motion is off.
    @State private var glowing = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var running: Bool { count > 0 }

    var body: some View {
        Text("Subagents: \(count)")
            .monospacedDigit()
            // Green with a soft glow that pulses slowly while any subagent runs; with
            // Reduce Motion on the glow holds at its middle strength.
            .foregroundStyle(running ? Color.green : Color.secondary)
            .shadow(
                color: running ? Color.green.opacity(reduceMotion ? 0.6 : (glowing ? 0.9 : 0.25)) : .clear,
                radius: reduceMotion ? 3 : (glowing ? 5 : 2)
            )
            .animation(
                running && !reduceMotion
                    ? .easeInOut(duration: 1.8).repeatForever(autoreverses: true) : .default,
                value: glowing
            )
            .onAppear { glowing = running }
            .onChange(of: running) { _, now in glowing = now }
            .contentShape(Rectangle())
            .onTapGesture { if !agents.isEmpty { shown.toggle() } }
            .help(
                agents.isEmpty
                    ? "No subagent is running"
                    : agents.map { "\($0.name): \($0.kind)" }.joined(separator: "\n")
            )
            .popover(isPresented: $shown, arrowEdge: .top) {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(agents) { agent in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(agent.name).fontWeight(.semibold)
                            HStack(spacing: 8) {
                                if let record = agent.record { Text(record) }
                                Text(agent.kind)
                                if let since = agent.since {
                                    Text(since, style: .timer).monospacedDigit()
                                }
                                if !agent.tokens.isEmpty { Text("\(agent.tokens) tokens").monospacedDigit() }
                            }
                            .foregroundStyle(.secondary)
                        }
                    }
                }
                .font(.system(size: 12))
                .padding(12)
                .frame(minWidth: 220, alignment: .leading)
            }
    }
}

/// The web view of the chat. Command-F reaches the page's own handler as a key press when the
/// view is first responder, but a key equivalent is offered to the menu first, so the view
/// takes it here and asks the page to open its search field (KYO-741).
///
/// Spelling (KYO-796). WebKit on macOS underlines misspelled words only when the app's
/// `WebContinuousSpellCheckingEnabled` default is true, and a missing key reads as false. It
/// asks the system's checker for corrections, which include the capital at the start of a
/// sentence, when words are being marked and `WebAutomaticSpellingCorrectionEnabled` is true,
/// and a missing key there reads as the system-wide setting. The page is therefore built over
/// registered defaults that turn the underline on and the correction off. They are registered
/// rather than set because the Spelling and Grammar menu stores the person's choice under the
/// same keys, and a stored value is read before a registered one. They are registered before
/// the first web view exists because WebKit reads the keys once in the life of the process.
///
/// The capital at the start of a sentence still came back with the correction turned off
/// whenever the underline was on (KYO-796.e3, the maintainer, 2026-11-11). It is taken to come
/// from the system's own capitalisation setting, `NSAutomaticCapitalizationEnabled`, which the
/// checker reads from the app's domain before the global one, so that key is registered off too.
/// That this is the source is a hypothesis the maintainer's next look decides.
final class ChatPage: WKWebView {
    static let spellingDefaults: [String: Any] = [
        "WebContinuousSpellCheckingEnabled": true,
        "WebAutomaticSpellingCorrectionEnabled": false,
        "NSAutomaticCapitalizationEnabled": false,
    ]

    override init(frame: CGRect, configuration: WKWebViewConfiguration) {
        UserDefaults.standard.register(defaults: Self.spellingDefaults)
        super.init(frame: frame, configuration: configuration)
    }

    required init?(coder: NSCoder) {
        fatalError("ChatPage is built in code")
    }

    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        if flags == .command, event.charactersIgnoringModifiers == "f" {
            evaluateJavaScript("window.kyo.find()", completionHandler: nil)
            return true
        }
        // Command-Shift-F opens History (KYO-825), offered to the menu first like Command-F.
        if flags == [.command, .shift], event.charactersIgnoringModifiers?.lowercased() == "f" {
            evaluateJavaScript("window.kyo.history()", completionHandler: nil)
            return true
        }
        return super.performKeyEquivalent(with: event)
    }
}

/// The read-only chat: a page drawn from the renderer in the app's resources. It loads
/// nothing else, and a link in a message opens in the person's browser.
struct ChatWebView: NSViewRepresentable {
    let feed: SessionFeed

    func makeCoordinator() -> Coordinator {
        Coordinator(feed: feed)
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.userContentController.add(context.coordinator, name: "log")
        configuration.userContentController.add(context.coordinator, name: "select")
        configuration.userContentController.add(context.coordinator, name: "image")
        configuration.userContentController.add(context.coordinator, name: "agent")
        configuration.userContentController.add(context.coordinator, name: "strip")
        for name in [
            "send", "retract", "interrupt", "saveDraft", "saveAgenda", "attach", "start",
            "sendNow", "copy",
            "terminal", "records", "files", "history", "project", "projects",
        ] {
            configuration.userContentController.add(context.coordinator, name: name)
        }
        let view = ChatPage(frame: .zero, configuration: configuration)
        view.navigationDelegate = context.coordinator
        view.isInspectable = true
        feed.webView = view
        // A packaged app holds the renderer in its own resources.
        let packaged = Bundle.main.url(
            forResource: "KyoseiApple_KyoseiMac", withExtension: "bundle"
        ).flatMap(Bundle.init(url:))
        let directory = (packaged ?? Bundle.module).resourceURL!.appending(path: "renderer")
        view.loadFileURL(
            directory.appending(path: "chat.html"), allowingReadAccessTo: directory)
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        let feed: SessionFeed

        init(feed: SessionFeed) {
            self.feed = feed
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            feed.start()
        }

        func webView(
            _ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
            decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void
        ) {
            guard let url = action.request.url, action.navigationType == .linkActivated else {
                decisionHandler(action.request.url?.isFileURL == true ? .allow : .cancel)
                return
            }
            if ["http", "https"].contains(url.scheme) { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }

        func userContentController(
            _ controller: WKUserContentController, didReceive message: WKScriptMessage
        ) {
            if message.name == "select", let body = message.body as? [String: Any],
                let session = body["session"] as? String
            {
                feed.select(session: session)
                return
            }
            if message.name == "send", let body = message.body as? [String: Any] {
                feed.sendMessage(body: body)
                return
            }
            if message.name == "retract", let body = message.body as? [String: Any],
                let id = body["id"] as? String
            {
                feed.retract(id: id)
                return
            }
            if message.name == "sendNow", let body = message.body as? [String: Any],
                let id = body["id"] as? String
            {
                feed.sendNow(id: id)
                return
            }
            if message.name == "copy", let body = message.body as? [String: Any],
                let text = body["text"] as? String
            {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
                return
            }
            if message.name == "attach", let body = message.body as? [String: Any] {
                feed.attach(body: body)
                return
            }
            if message.name == "saveDraft", let body = message.body as? [String: Any] {
                feed.saveDraft(body: body)
                return
            }
            if message.name == "saveAgenda", let body = message.body as? [String: Any] {
                feed.saveAgenda(body: body)
                return
            }
            if message.name == "interrupt" {
                feed.interrupt()
                return
            }
            if message.name == "terminal", let body = message.body as? [String: Any] {
                feed.terminal(body: body)
                return
            }
            if message.name == "records", let body = message.body as? [String: Any] {
                feed.records(body: body)
                return
            }
            if message.name == "files", let body = message.body as? [String: Any] {
                feed.files(body: body)
                return
            }
            if message.name == "history", let body = message.body as? [String: Any] {
                feed.history(body: body)
                return
            }
            if message.name == "start" {
                feed.startOrchestrator()
                return
            }
            if message.name == "project", let body = message.body as? [String: Any],
                let name = body["name"] as? String
            {
                feed.choose(project: name, session: body["session"] as? String)
                return
            }
            if message.name == "projects", let body = message.body as? [String: Any] {
                feed.projects(body: body)
                return
            }
            if message.name == "strip", let body = message.body as? [String: Any],
                let running = body["subagents"] as? Int
            {
                feed.runningSubagents = running
                let stamps = ISO8601DateFormatter()
                stamps.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
                let agents = body["agents"] as? [[String: Any]] ?? []
                feed.runningAgents = agents.enumerated().map { index, agent in
                    RunningAgent(
                        id: index,
                        name: agent["name"] as? String ?? "",
                        type: agent["type"] as? String ?? "",
                        model: agent["model"] as? String ?? "",
                        effort: agent["effort"] as? String ?? "",
                        since: stamps.date(from: agent["since"] as? String ?? ""),
                        tokens: agent["tokens"] as? String ?? "")
                }
                return
            }
            if message.name == "agent", let body = message.body as? [String: Any],
                let agent = body["agent"] as? String
            {
                feed.loadAgent(agent)
                return
            }
            if message.name == "image", let body = message.body as? [String: Any],
                let attachment = body["attachment"] as? String
            {
                feed.loadAttachment(attachment, session: body["session"] as? String)
                return
            }
            if message.name == "image", let body = message.body as? [String: Any],
                let turn = body["turn"] as? String, let index = body["index"] as? Int
            {
                feed.loadImage(turn: turn, index: index, session: body["session"] as? String)
                return
            }
            print("[page] \(message.body)")
        }
    }
}
