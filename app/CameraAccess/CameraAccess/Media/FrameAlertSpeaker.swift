import AVFoundation
import Foundation

@MainActor
protocol FrameAlertSpeaking {
  func scheduleAlert(_ text: String)
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
/// A new description cancels the alert backup; otherwise it speaks after four seconds.
/// Descriptions wait for current speech and only the newest pending one survives.
@MainActor
final class FrameAlertSpeaker: NSObject, FrameAlertSpeaking, AVSpeechSynthesizerDelegate {
  typealias AlertFallbackScheduler = @MainActor (
    TimeInterval, @escaping @MainActor () -> Void
  ) -> (@MainActor () -> Void)

  static let alertFallbackDelay: TimeInterval = 4

  private struct PendingDescription {
    let text: String
    let receivedAt: TimeInterval
  }

  private let synthesizer: any FrameSpeechSynthesizing
  private let now: () -> TimeInterval
  private let scheduleFallback: AlertFallbackScheduler
  private var cancelFallback: (@MainActor () -> Void)?
  private var fallbackGeneration = 0
  private var currentUtterance: AVSpeechUtterance?
  private var pendingDescription: PendingDescription?
  // IDs increase within a stream. Remember accepted IDs too, so repeated replies
  // cannot revive a replaced/expired description or refresh its waiting deadline.
  private var latestDescriptionID: Int?
  private static let maximumDescriptionWait: TimeInterval = 5

  init(
    synthesizer: any FrameSpeechSynthesizing = AVSpeechSynthesizer(),
    now: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime },
    scheduleFallback: @escaping AlertFallbackScheduler = { delay, fire in
      let deadline = ContinuousClock.now + .seconds(delay)
      let task = Task { @MainActor in
        do {
          try await Task.sleep(until: deadline, clock: .continuous)
        } catch {
          return
        }
        guard !Task.isCancelled else { return }
        fire()
      }
      return { task.cancel() }
    }
  ) {
    self.synthesizer = synthesizer
    self.now = now
    self.scheduleFallback = scheduleFallback
    super.init()
    synthesizer.delegate = self
  }

  func scheduleAlert(_ text: String) {
    guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
    cancelPendingAlert()
    let generation = fallbackGeneration
    cancelFallback = scheduleFallback(Self.alertFallbackDelay) { [weak self] in
      // Ignore callbacks already queued when a description, replacement or reset arrived.
      guard let self, fallbackGeneration == generation else { return }
      cancelPendingAlert()
      speakAlert(text)
    }
  }

  private func speakAlert(_ text: String) {
    let previous = currentUtterance
    let utterance = AVSpeechUtterance(string: text)
    // Update first: a cancellation callback for the old utterance must not start
    // a waiting description ahead of the new alert.
    currentUtterance = utterance
    if previous != nil {
      synthesizer.stopSpeaking(at: .immediate)
    }
    synthesizer.speak(utterance)
  }

  func speakDescription(_ text: String, id: Int) {
    let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !text.isEmpty else { return }
    if let latestDescriptionID, id <= latestDescriptionID { return }
    cancelPendingAlert()
    latestDescriptionID = id
    pendingDescription = PendingDescription(text: text, receivedAt: now())
    speakPendingDescription()
  }

  func stop() {
    cancelPendingAlert()
    currentUtterance = nil
    pendingDescription = nil
    latestDescriptionID = nil
    synthesizer.stopSpeaking(at: .immediate)
  }

  private func cancelPendingAlert() {
    fallbackGeneration += 1
    cancelFallback?()
    cancelFallback = nil
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
