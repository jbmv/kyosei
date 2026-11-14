import AppKit
import Testing

@testable import KyoseiMac

// KYO-918.n1: the system adds AutoFill and Services to a box's menu after willOpenMenu runs,
// so the menu's delegate removes them. The menu here is built with those items.
@MainActor
struct MenuTrimTests {
    @Test func removesAutoFillAndServicesAndTheirSeparators() {
        let menu = NSMenu()
        menu.addItem(withTitle: "Cut", action: nil, keyEquivalent: "")
        menu.addItem(.separator())
        let autofill = NSMenuItem(title: "AutoFill", action: nil, keyEquivalent: "")
        autofill.submenu = NSMenu()
        menu.addItem(autofill)
        menu.addItem(.separator())
        let other = NSMenuItem(title: "Anything", action: nil, keyEquivalent: "")
        other.identifier = NSUserInterfaceItemIdentifier("com.apple.menu.Services")
        menu.addItem(other)
        SystemItemTrimmer().menuNeedsUpdate(menu)
        #expect(menu.items.map(\.title) == ["Cut"])
        #expect(menu.allowsContextMenuPlugIns == false)
    }
}
