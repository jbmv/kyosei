import AppKit
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
                if let facts = feed.facts {
                    Text(facts.model ?? "model unknown")
                    if let effort = facts.effort { Text("effort \(effort)") }
                    if let percent = facts.context?.percent {
                        Text("context \(percent)%").monospacedDigit()
                    }
                }
                Text(
                    "\(feed.runningSubagents) subagent\(feed.runningSubagents == 1 ? "" : "s") running"
                )
                .monospacedDigit()
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
        let directory = Bundle.module.resourceURL!.appending(path: "renderer")
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
