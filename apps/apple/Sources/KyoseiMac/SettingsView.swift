import SwiftUI

/// Where this device's `kyo serve` is, and the token it uses. Read-only views need no token.
struct SettingsView: View {
    let feed: SessionFeed
    @State private var server = SessionFeed.savedServer
    @State private var project = SessionFeed.savedProject
    @State private var token = ""
    @State private var note = ""

    var body: some View {
        Form {
            TextField("Server", text: $server, prompt: Text("https://your-host.ts.net"))
            TextField("Project", text: $project)
            SecureField("Device token", text: $token, prompt: Text("Leave blank to keep the current one"))
            HStack {
                Text(note).foregroundStyle(.secondary)
                Spacer()
                Button("Save") { save() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 460)
    }

    private func save() {
        guard let base = URL(string: server), base.scheme != nil else {
            note = "That is not a server address."
            return
        }
        if !token.isEmpty {
            guard SessionFeed.storeDeviceToken(token) else {
                note = "The token could not be saved."
                return
            }
            token = ""
        }
        feed.configure(server: base, project: project)
        note = "Saved."
    }
}
