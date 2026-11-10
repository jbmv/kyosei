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
                Text(feed.project).fontWeight(.semibold).foregroundStyle(.primary)
                if let percent = feed.facts?.context?.percent {
                    ContextMeter(percent: percent)
                }
                if let limits = feed.facts?.limits {
                    if let window = limits.five_hour { LimitLabel(name: "5-hour", window: window) }
                    if let window = limits.seven_day { LimitLabel(name: "Weekly", window: window) }
                }
                if let facts = feed.facts {
                    Text(facts.model ?? "model unknown")
                    if let effort = facts.effort { Text("effort \(effort)") }
                }
                RunningAgentsLabel(agents: feed.runningAgents, count: feed.runningSubagents)
                if let mode = feed.facts?.permission_mode { Text(mode) }
                if let lines = feed.facts?.lines {
                    HStack(spacing: 4) {
                        Text("+\(lines.added.formatted())").foregroundStyle(.green)
                        Text("−\(lines.removed.formatted())").foregroundStyle(.red)
                    }
                    .monospacedDigit()
                }
                Spacer()
                if feed.status != "connected" { Text(feed.status) }
                if let remote = feed.remote { Text(remote) }
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
        Text("\(name): \(Int(window.used_percentage.rounded()))%")
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

/// The count of running subagents, which opens a list of them when clicked.
struct RunningAgentsLabel: View {
    let agents: [RunningAgent]
    let count: Int
    @State private var shown = false

    var body: some View {
        Text("\(count) subagent\(count == 1 ? "" : "s") running")
            .monospacedDigit()
            .contentShape(Rectangle())
            .onTapGesture { if !agents.isEmpty { shown.toggle() } }
            .help(agents.isEmpty ? "No subagent is running" : agents.map(\.name).joined(separator: "\n"))
            .popover(isPresented: $shown, arrowEdge: .top) {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(agents) { agent in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(agent.name).fontWeight(.semibold)
                            HStack(spacing: 8) {
                                if let record = agent.record { Text(record) }
                                Text(agent.type)
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
        for name in ["send", "retract", "interrupt", "saveDraft"] {
            configuration.userContentController.add(context.coordinator, name: name)
        }
        let view = WKWebView(frame: .zero, configuration: configuration)
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
            if message.name == "saveDraft", let body = message.body as? [String: Any] {
                feed.saveDraft(body: body)
                return
            }
            if message.name == "interrupt" {
                feed.interrupt()
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
                let turn = body["turn"] as? String, let index = body["index"] as? Int
            {
                feed.loadImage(turn: turn, index: index)
                return
            }
            print("[page] \(message.body)")
        }
    }
}
