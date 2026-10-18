@testable import CameraAccess
import AVFoundation
import Foundation
import Testing

@Suite(.serialized)
@MainActor
struct FrameAlertSpeakerTests {
  @Test func speaksEachIDOnceAndAllowsTheSameTextWithANewID() async throws {
    let harness = SpeechHarness()
    harness.speaker.speakDescription("Two people ahead.", id: 7)
    harness.speaker.speakDescription("Two people ahead.", id: 7)
    #expect(harness.synthesizer.spoken == ["Two people ahead."])
    try await harness.synthesizer.complete(0)
    harness.speaker.speakDescription("Two people ahead.", id: 7)
    harness.speaker.speakDescription("An older scene.", id: 6)
    #expect(harness.synthesizer.spoken.count == 1)
    harness.speaker.speakDescription("Two people ahead.", id: 8)
    #expect(harness.synthesizer.spoken == ["Two people ahead.", "Two people ahead."])
    #expect(harness.synthesizer.stopCount == 0)
  }

  @Test func oldCompletionCallbacksDoNotStartWaitingDescriptionsEarly() async throws {
    let harness = SpeechHarness()
    harness.speaker.speakDescription("One person ahead.", id: 1)
    harness.speaker.speakDescription("Two people to your left.", id: 2)
    try await harness.synthesizer.complete(0)
    harness.speaker.speakDescription("Several people ahead.", id: 3)
    try await harness.synthesizer.complete(0, cancelled: true)
    try await harness.synthesizer.complete(0)
    #expect(harness.synthesizer.spoken == ["One person ahead.", "Two people to your left."])
    // Repeated server replies must not restart an older description.
    harness.speaker.speakDescription("One person ahead.", id: 1)
    try await harness.synthesizer.complete(1)
    #expect(harness.synthesizer.spoken == [
      "One person ahead.", "Two people to your left.", "Several people ahead.",
    ])
    #expect(harness.synthesizer.stopCount == 0)
  }

  @Test func descriptionsWaitForOtherDescriptionsWithoutInterrupting() async throws {
    let harness = SpeechHarness()
    harness.speaker.speakDescription("One person ahead.", id: 1)
    harness.speaker.speakDescription("Two people ahead.", id: 2)
    harness.speaker.speakDescription("Several people ahead.", id: 3)
    harness.speaker.speakDescription("Two people ahead.", id: 2)
    #expect(harness.synthesizer.spoken == ["One person ahead."])
    try await harness.synthesizer.complete(0)
    #expect(harness.synthesizer.spoken == ["One person ahead.", "Several people ahead."])
    #expect(harness.synthesizer.stopCount == 0)
  }

  @Test(arguments: [4.999, 5.0, 5.001])
  func dropsDescriptionsWaitingMoreThanFiveSeconds(_ wait: TimeInterval) async throws {
    let harness = SpeechHarness()
    harness.speaker.speakDescription("The current scene.", id: 0)
    let receivedAt = harness.clock.time
    harness.speaker.speakDescription("One person to your left.", id: 1)
    harness.clock.time += 4
    harness.speaker.speakDescription("One person to your left.", id: 1)
    harness.clock.time = receivedAt + wait
    try await harness.synthesizer.complete(0)
    #expect(harness.synthesizer.spoken.count == (wait <= 5 ? 2 : 1))
    harness.speaker.speakDescription("One person to your left.", id: 1)
    #expect(harness.synthesizer.spoken.count == (wait <= 5 ? 2 : 1), "Expired IDs stay consumed")
  }

  @Test func aNewerDescriptionGetsItsOwnWaitingDeadline() async throws {
    let harness = SpeechHarness()
    harness.speaker.speakDescription("The current scene.", id: 0)
    harness.speaker.speakDescription("One person to your left.", id: 1)
    harness.clock.time += 6
    harness.speaker.speakDescription("Two people ahead.", id: 2)
    harness.clock.time += 2
    try await harness.synthesizer.complete(0)
    #expect(harness.synthesizer.spoken == ["The current scene.", "Two people ahead."])
  }

  @Test func blankTextDoesNotInterruptSpeechOrConsumeAnID() {
    let harness = SpeechHarness()
    harness.speaker.speakDescription(" \n\t ", id: 1)
    #expect(harness.synthesizer.spoken.isEmpty)
    harness.speaker.speakDescription(" One person ahead. \n", id: 1)
    harness.speaker.speakDescription("\n", id: 2)
    #expect(harness.synthesizer.spoken == ["One person ahead."])
    #expect(harness.synthesizer.stopCount == 0)
  }

  @Test func stopClearsPendingSpeechAndResetsRememberedIDs() async throws {
    let harness = SpeechHarness()
    harness.speaker.speakDescription("The current scene.", id: 6)
    harness.speaker.speakDescription("An old scene.", id: 7)
    harness.speaker.stop()
    try await harness.synthesizer.complete(0, cancelled: true)
    try await harness.synthesizer.complete(0)
    #expect(harness.synthesizer.spoken == ["The current scene."])
    harness.speaker.speakDescription("A new stream.", id: 1)
    #expect(harness.synthesizer.spoken.last == "A new stream.")
    harness.speaker.stop()
    harness.speaker.speakDescription("A new stream.", id: 1)
    harness.speaker.speakDescription("The next scene.", id: 2)
    try await harness.synthesizer.complete(1, cancelled: true)
    try await harness.synthesizer.complete(1)
    #expect(harness.synthesizer.spoken == ["The current scene.", "A new stream.", "A new stream."])
    try await harness.synthesizer.complete(2)
    #expect(harness.synthesizer.spoken.last == "The next scene.")
    #expect(harness.synthesizer.stopCount == 2)
  }

  @Test func anUnexpectedCancellationStillDrainsTheWaitingDescription() async throws {
    let harness = SpeechHarness()
    harness.speaker.speakDescription("The current scene.", id: 0)
    harness.speaker.speakDescription("One person to your left.", id: 1)
    try await harness.synthesizer.complete(0, cancelled: true)
    #expect(harness.synthesizer.spoken == ["The current scene.", "One person to your left."])
  }
}

@MainActor
private final class SpeechHarness {
  let synthesizer = SynthesizerSpy()
  let clock = SpeechClock()
  let speaker: FrameAlertSpeaker

  init() {
    let clock = clock
    speaker = FrameAlertSpeaker(
      synthesizer: synthesizer,
      now: { clock.time }
    )
  }
}

@MainActor
private final class SpeechClock {
  var time: TimeInterval = 100
}

@MainActor
private final class SynthesizerSpy: FrameSpeechSynthesizing {
  weak var delegate: (any AVSpeechSynthesizerDelegate)?
  private var utterances: [AVSpeechUtterance] = []
  private let callbackSynthesizer = AVSpeechSynthesizer()
  private(set) var stopCount = 0
  var spoken: [String] { utterances.map(\.speechString) }

  func speak(_ utterance: AVSpeechUtterance) { utterances.append(utterance) }

  func stopSpeaking(at boundary: AVSpeechBoundary) -> Bool {
    #expect(boundary == .immediate)
    stopCount += 1
    return true
  }

  func complete(_ index: Int, cancelled: Bool = false) async throws {
    let utterance = try #require(utterances.indices.contains(index) ? utterances[index] : nil)
    if cancelled {
      delegate?.speechSynthesizer?(callbackSynthesizer, didCancel: utterance)
    } else {
      delegate?.speechSynthesizer?(callbackSynthesizer, didFinish: utterance)
    }
    // Let the delegate's main-actor task run, including when it should do nothing.
    try await Task.sleep(for: .milliseconds(10))
  }
}
