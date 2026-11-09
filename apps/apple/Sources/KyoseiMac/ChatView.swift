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
                if let facts = feed.facts {
                    Text(facts.model ?? "model unknown")
                    if let effort = facts.effort { Text("effort \(effort)") }
                    if let percent = facts.context?.percent {
                        Text("context \(percent)%").monospacedDigit()
                    }
                }
                Spacer()
                if feed.status != "connected" { Text(feed.status) }
                Text(feed.project)
                if let branch = feed.facts?.git_branch { Text(branch) }
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
            print("[page] \(message.body)")
        }
    }
}
