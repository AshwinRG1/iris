import AVFoundation
import Foundation

@MainActor
protocol FrameAlertSpeaking {
  func speakDescription(_ text: String, id: Int)
  func stop()
}

/// A small transport boundary lets speech ordering be tested without playing audio.
@MainActor
protocol FrameSpeechSynthesizing: AnyObject {
  var delegate: (any AVSpeechSynthesizerDelegate)? { get set }
  func speak(_ utterance: AVSpeechUtterance)
  @discardableResult func stopSpeaking(at boundary: AVSpeechBoundary) -> Bool
}

extension AVSpeechSynthesizer: FrameSpeechSynthesizing {}

/// One synthesizer uses the existing audio route, including connected glasses.
/// Only Gemini descriptions are spoken; server alerts remain display-only.
/// Descriptions wait for current speech and only the newest pending one survives.
@MainActor
final class FrameAlertSpeaker: NSObject, FrameAlertSpeaking, AVSpeechSynthesizerDelegate {
  private struct PendingDescription {
    let text: String
    let receivedAt: TimeInterval
  }

  private let synthesizer: any FrameSpeechSynthesizing
  private let now: () -> TimeInterval
  private var currentUtterance: AVSpeechUtterance?
  private var pendingDescription: PendingDescription?
  // IDs increase within a stream. Remember accepted IDs too, so repeated replies
  // cannot revive a replaced/expired description or refresh its waiting deadline.
  private var latestDescriptionID: Int?
  private static let maximumDescriptionWait: TimeInterval = 5

  init(
    synthesizer: any FrameSpeechSynthesizing = AVSpeechSynthesizer(),
    now: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }
  ) {
    self.synthesizer = synthesizer
    self.now = now
    super.init()
    synthesizer.delegate = self
  }

  func speakDescription(_ text: String, id: Int) {
    let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !text.isEmpty else { return }
    if let latestDescriptionID, id <= latestDescriptionID { return }
    latestDescriptionID = id
    pendingDescription = PendingDescription(text: text, receivedAt: now())
    speakPendingDescription()
  }

  func stop() {
    currentUtterance = nil
    pendingDescription = nil
    latestDescriptionID = nil
    synthesizer.stopSpeaking(at: .immediate)
  }

  private func speakPendingDescription() {
    guard currentUtterance == nil, let pending = pendingDescription else { return }
    pendingDescription = nil
    guard now() - pending.receivedAt <= Self.maximumDescriptionWait else { return }
    let utterance = AVSpeechUtterance(string: pending.text)
    currentUtterance = utterance
    synthesizer.speak(utterance)
  }

  nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
    speechEnded(ObjectIdentifier(utterance))
  }

  nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
    speechEnded(ObjectIdentifier(utterance))
  }

  nonisolated private func speechEnded(_ id: ObjectIdentifier) {
    // Delegate callbacks may arrive off the main actor. Only pass identity across
    // that boundary; AVSpeechUtterance itself is not Sendable.
    Task { @MainActor [weak self] in
      guard let self, let current = currentUtterance, ObjectIdentifier(current) == id else { return }
      currentUtterance = nil
      speakPendingDescription()
    }
  }
}
