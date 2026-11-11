import Foundation
import Testing

@testable import KyoseiMac

// KYO-809: the frame the page rebuilds a taken-back prompt from carries the prompt's own marks
// as the server returned them, so that the page restores anchors and tags on any device.
struct RetractionTests {
    private func frame(_ json: String) throws -> [String: Any] {
        let text = SessionFeed.retractedFrame(Data(json.utf8))
        return try #require(
            try JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any])
    }

    @Test func aMarkedPromptReturnsItsMarksAndItsTypedText() throws {
        let reply = """
            {"ok": true, "segments": [{"kind": "typed", "text": "sent with lines added"}],
             "prompt": {"segments": [{"kind": "typed", "text": "as typed"}],
                        "marks": [{"line": 0, "anchors": [{"kind": "record", "id": "KYO-1"}],
                                   "tags": ["x"]}]}}
            """
        let back = try frame(reply)
        #expect(back["text"] as? String == "as typed")
        let marks = (back["prompt"] as? [String: Any])?["marks"] as? [[String: Any]]
        #expect(marks?.count == 1)
        #expect(marks?.first?["tags"] as? [String] == ["x"])
    }

    @Test func aPlainPromptReturnsItsTextAlone() throws {
        let back = try frame(#"{"ok": true, "segments": [{"kind": "typed", "text": "hi"}], "prompt": null}"#)
        #expect(back["text"] as? String == "hi")
        #expect(back["prompt"] == nil)
    }
}
