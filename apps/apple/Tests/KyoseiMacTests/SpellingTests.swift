import AppKit
import Foundation
import Testing
import WebKit

@testable import KyoseiMac

// KYO-796: the composer underlines misspelled words and neither corrects them nor capitalises
// a sentence. WebKit decides both from two of the app's defaults, read once in the life of
// the process, so the chat's web view registers them as it is built. These tests build that
// web view without a window. Whether an underline is drawn cannot be read from a page, so
// the tests read the two keys as WebKit does, and the typing test shows that the page's text
// is left as typed.
@MainActor
@Suite(.serialized)
struct SpellingTests {
    private static let underline = "WebContinuousSpellCheckingEnabled"
    private static let correction = "WebAutomaticSpellingCorrectionEnabled"
    private static let capitals = "NSAutomaticCapitalizationEnabled"

    /// The test process stores neither key, as an app that has never had a choice made in its
    /// Spelling and Grammar menu stores neither.
    private func page() -> ChatPage {
        ChatPage(
            frame: NSRect(x: 0, y: 0, width: 600, height: 400),
            configuration: WKWebViewConfiguration())
    }

    @Test func buildingThePageTurnsTheUnderlineOnAndTheCorrectionOff() {
        _ = page()
        let defaults = UserDefaults.standard
        // WebKit reads the underline with `boolForKey:`, and takes the system-wide setting for
        // the correction unless `objectForKey:` finds a value.
        #expect(defaults.bool(forKey: Self.underline))
        #expect(defaults.object(forKey: Self.correction) as? Bool == false)
        // A value stored in any domain, the system-wide one included, is read before a
        // registered one, so the capitals key is checked where the app registers it.
        let registered = defaults.volatileDomain(forName: UserDefaults.registrationDomain)
        #expect(registered[Self.capitals] as? Bool == false)
    }

    @Test func theValuesAreRegisteredAndNotStored() {
        _ = page()
        // The Spelling and Grammar menu stores the person's choice under the same keys, and a
        // stored value is read before a registered one. A value the app stored itself would
        // replace that choice at every launch, so the app stores nothing: the keys are in the
        // registration domain and not in the process's own.
        let registered = UserDefaults.standard.volatileDomain(
            forName: UserDefaults.registrationDomain)
        for key in [Self.underline, Self.correction, Self.capitals] {
            #expect(registered[key] != nil)
            #expect(
                CFPreferencesCopyValue(
                    key as CFString, kCFPreferencesCurrentApplication,
                    kCFPreferencesCurrentUser, kCFPreferencesAnyHost) == nil)
        }
    }

    @Test func typingInThePageLeavesTheWordsAsTyped() async throws {
        let view = page()
        view.loadHTMLString("<!doctype html><textarea></textarea>", baseURL: nil)
        let typed = "teh cat. hello agian. i recieve "
        for _ in 0..<100 {
            let ready = try? await view.evaluateJavaScript(
                "document.readyState === 'complete' && !!document.querySelector('textarea')")
            if ready as? Bool == true { break }
            try await Task.sleep(for: .milliseconds(50))
        }
        _ = try await view.evaluateJavaScript("document.querySelector('textarea').focus()")
        for character in typed {
            let literal = String(
                decoding: try JSONEncoder().encode(String(character)), as: UTF8.self)
            _ = try await view.evaluateJavaScript(
                "document.execCommand('insertText', false, \(literal))")
            try await Task.sleep(for: .milliseconds(30))
        }
        // A correction arrives from the app's process after the word is typed.
        try await Task.sleep(for: .seconds(1))
        let value = try #require(
            try await view.evaluateJavaScript("document.querySelector('textarea').value")
                as? String)
        #expect(value == typed)
    }
}
