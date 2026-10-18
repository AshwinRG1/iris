@testable import CameraAccess
import AVFoundation
import Foundation
import Testing

@Suite(.serialized)
@MainActor
struct FrameAlertSpeakerTests {
  @Test(arguments: [0.0, 3.999])
  func newDescriptionCancelsAlertBeforeFourSeconds(_ delay: TimeInterval) {
    let harness = SpeechHarness()
    harness.speaker.scheduleAlert("Possible person, left.")
    #expect(harness.synthesizer.spoken.isEmpty)
    harness.clock.advance(by: delay)
    harness.speaker.speakDescription("Two people to your left.", id: 7)
    #expect(harness.synthesizer.spoken == ["Two people to your left."])
    #expect(harness.clock.pendingTimerCount == 0)
    // Even a callback queued just before cancellation must not speak the alert.
    harness.clock.fireEvenIfCancelled(0)
    harness.clock.advance(by: 10)
    #expect(harness.synthesizer.spoken == ["Two people to your left."])
    #expect(harness.synthesizer.stopCount == 0)
  }

  @Test func alertIsSpokenAtFourSecondsWhenNoDescriptionArrives() {
    let harness = SpeechHarness()
    let deadline = harness.clock.time + 4
    harness.speaker.scheduleAlert("Possible person, left.")
    #expect(harness.synthesizer.spoken.isEmpty)
    harness.clock.advance(to: deadline - 0.001)
    #expect(harness.synthesizer.spoken.isEmpty)
    harness.clock.advance(to: deadline)
    #expect(harness.synthesizer.spoken == ["Possible person, left."])
    #expect(harness.clock.pendingTimerCount == 0)
    harness.clock.advance(by: 10)
    #expect(harness.synthesizer.spoken.count == 1)
  }

  @Test(arguments: ["Possible person, right.", "Possible person, left."])
  func twoAlertsReplaceTheTimerAndSpeakOnlyTheLatestBackup(_ secondAlert: String) {
    let harness = SpeechHarness()
    let start = harness.clock.time
    harness.speaker.scheduleAlert("Possible person, left.")
    harness.clock.advance(by: 2)
    harness.speaker.scheduleAlert(secondAlert)
    #expect(harness.clock.pendingTimerCount == 1)
    harness.clock.advance(to: start + 4)
    harness.clock.fireEvenIfCancelled(0)
    #expect(harness.synthesizer.spoken.isEmpty)
    harness.clock.advance(to: start + 6 - 0.001)
    #expect(harness.synthesizer.spoken.isEmpty)
    harness.clock.advance(to: start + 6)
    #expect(harness.synthesizer.spoken == [secondAlert])
    harness.clock.fireEvenIfCancelled(1)
    harness.clock.advance(by: 10)
    #expect(harness.synthesizer.spoken == [secondAlert])
    #expect(harness.synthesizer.stopCount == 0)
  }

  @Test func repeatedAndOlderDescriptionIDsDoNotCancelTheBackup() async throws {
    let harness = SpeechHarness()
    harness.speaker.speakDescription("One person ahead.", id: 7)
    try await harness.synthesizer.complete(0)
    harness.speaker.scheduleAlert("Possible person, left.")
    harness.clock.advance(by: 3)
    harness.speaker.speakDescription("One person ahead.", id: 7)
    harness.speaker.speakDescription("An older scene.", id: 6)
    harness.clock.advance(by: 1)
    #expect(harness.synthesizer.spoken == ["One person ahead.", "Possible person, left."])
  }

  @Test func alertWaitsWithoutInterruptingCurrentSpeech() async throws {
    let harness = SpeechHarness()
    harness.speaker.speakDescription("One person ahead.", id: 1)
    harness.speaker.scheduleAlert("Possible person, left.")
    harness.clock.advance(by: 3)
    #expect(harness.synthesizer.spoken == ["One person ahead."])
    #expect(harness.synthesizer.stopCount == 0)
    harness.speaker.speakDescription("Two people to your left.", id: 2)
    try await harness.synthesizer.complete(0)
    harness.clock.advance(by: 10)
    #expect(harness.synthesizer.spoken == ["One person ahead.", "Two people to your left."])
    #expect(harness.synthesizer.stopCount == 0)
  }

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

  @Test func descriptionWaitsForABackupThatHasAlreadyStarted() async throws {
    let harness = SpeechHarness()
    harness.speaker.scheduleAlert("Possible person, left.")
    harness.clock.advance(by: 4)
    harness.speaker.speakDescription("Two people to your left.", id: 1)
    #expect(harness.synthesizer.spoken == ["Possible person, left."])
    #expect(harness.synthesizer.stopCount == 0)
    try await harness.synthesizer.complete(0)
    #expect(harness.synthesizer.spoken == ["Possible person, left.", "Two people to your left."])
  }

  @Test func keepsOnlyTheNewestWaitingDescription() async throws {
    let harness = SpeechHarness()
    harness.speaker.scheduleAlert("Possible person, left.")
    harness.clock.advance(by: 4)
    harness.speaker.speakDescription("One person to your left.", id: 1)
    harness.speaker.speakDescription("Two people ahead.", id: 2)
    harness.speaker.speakDescription("One person to your left.", id: 1)
    try await harness.synthesizer.complete(0)
    #expect(harness.synthesizer.spoken == ["Possible person, left.", "Two people ahead."])
  }

  @Test func expiredBackupsInterruptSpeechAndIgnoreOldCompletionCallbacks() async throws {
    let harness = SpeechHarness()
    harness.speaker.speakDescription("One person ahead.", id: 1)
    harness.speaker.scheduleAlert("Possible person, left.")
    harness.clock.advance(by: 4)
    harness.speaker.speakDescription("Two people to your left.", id: 2)
    harness.speaker.scheduleAlert("Possible person, right.")
    harness.clock.advance(by: 4)
    #expect(harness.synthesizer.stopCount == 2)
    try await harness.synthesizer.complete(0, cancelled: true)
    try await harness.synthesizer.complete(0)
    try await harness.synthesizer.complete(1, cancelled: true)
    #expect(harness.synthesizer.spoken == [
      "One person ahead.", "Possible person, left.", "Possible person, right.",
    ])
    // Repeated server replies must not restart the interrupted description.
    harness.speaker.speakDescription("One person ahead.", id: 1)
    try await harness.synthesizer.complete(2)
    #expect(harness.synthesizer.spoken.last == "Two people to your left.")
    #expect(harness.synthesizer.spoken.count == 4)
  }

  @Test func descriptionsWaitForOtherDescriptionsWithoutInterrupting() async throws {
    let harness = SpeechHarness()
    harness.speaker.speakDescription("One person ahead.", id: 1)
    harness.speaker.speakDescription("Two people ahead.", id: 2)
    harness.speaker.speakDescription("Several people ahead.", id: 3)
    #expect(harness.synthesizer.spoken == ["One person ahead."])
    try await harness.synthesizer.complete(0)
    #expect(harness.synthesizer.spoken == ["One person ahead.", "Several people ahead."])
    #expect(harness.synthesizer.stopCount == 0)
  }

  @Test(arguments: [4.999, 5.0, 5.001])
  func dropsDescriptionsWaitingMoreThanFiveSeconds(_ wait: TimeInterval) async throws {
    let harness = SpeechHarness()
    harness.speaker.scheduleAlert("Possible person, left.")
    harness.clock.advance(by: 4)
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
    harness.speaker.scheduleAlert("Possible person, left.")
    harness.clock.advance(by: 4)
    harness.speaker.speakDescription("One person to your left.", id: 1)
    harness.clock.time += 6
    harness.speaker.speakDescription("Two people ahead.", id: 2)
    harness.clock.time += 2
    try await harness.synthesizer.complete(0)
    #expect(harness.synthesizer.spoken == ["Possible person, left.", "Two people ahead."])
  }

  @Test func blankTextDoesNotInterruptSpeechOrConsumeAnID() {
    let harness = SpeechHarness()
    harness.speaker.scheduleAlert(" \n\t ")
    harness.speaker.speakDescription(" \n\t ", id: 1)
    #expect(harness.synthesizer.spoken.isEmpty)
    harness.speaker.speakDescription(" One person ahead. \n", id: 1)
    harness.speaker.scheduleAlert("\n")
    #expect(harness.clock.pendingTimerCount == 0)
    #expect(harness.synthesizer.spoken == ["One person ahead."])
    #expect(harness.synthesizer.stopCount == 0)
  }

  @Test func stopClearsPendingSpeechAndResetsRememberedIDs() async throws {
    let harness = SpeechHarness()
    harness.speaker.scheduleAlert("Possible person, left.")
    harness.clock.advance(by: 4)
    harness.speaker.speakDescription("An old scene.", id: 7)
    harness.speaker.stop()
    try await harness.synthesizer.complete(0, cancelled: true)
    try await harness.synthesizer.complete(0)
    #expect(harness.synthesizer.spoken == ["Possible person, left."])
    harness.speaker.speakDescription("A new stream.", id: 1)
    #expect(harness.synthesizer.spoken.last == "A new stream.")
    harness.speaker.stop()
    harness.speaker.speakDescription("A new stream.", id: 1)
    try await harness.synthesizer.complete(1, cancelled: true)
    #expect(harness.synthesizer.spoken == ["Possible person, left.", "A new stream.", "A new stream."])
    #expect(harness.synthesizer.stopCount == 2)
  }

  @Test func stopCancelsTheBackupAndIgnoresItsCallbackAfterRestart() {
    let harness = SpeechHarness()
    harness.speaker.scheduleAlert("An old alert.")
    harness.clock.advance(by: 2)
    harness.speaker.stop()
    #expect(harness.clock.pendingTimerCount == 0)
    harness.speaker.scheduleAlert("A new alert.")
    harness.clock.fireEvenIfCancelled(0)
    harness.clock.advance(by: 2)
    #expect(harness.synthesizer.spoken.isEmpty)
    harness.clock.advance(by: 2)
    #expect(harness.synthesizer.spoken == ["A new alert."])
  }

  @Test func blankDescriptionDoesNotCancelTheBackupOrConsumeAnID() async throws {
    let harness = SpeechHarness()
    harness.speaker.scheduleAlert("Possible person, left.")
    harness.speaker.speakDescription(" \n\t ", id: 1)
    harness.clock.advance(by: 4)
    #expect(harness.synthesizer.spoken == ["Possible person, left."])
    try await harness.synthesizer.complete(0)
    harness.speaker.speakDescription("Two people ahead.", id: 1)
    #expect(harness.synthesizer.spoken == ["Possible person, left.", "Two people ahead."])
  }

  @Test func anUnexpectedCancellationStillDrainsTheWaitingDescription() async throws {
    let harness = SpeechHarness()
    harness.speaker.scheduleAlert("Possible person, left.")
    harness.clock.advance(by: 4)
    harness.speaker.speakDescription("One person to your left.", id: 1)
    try await harness.synthesizer.complete(0, cancelled: true)
    #expect(harness.synthesizer.spoken == ["Possible person, left.", "One person to your left."])
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
      now: { clock.time },
      scheduleFallback: { delay, fire in clock.schedule(after: delay, fire: fire) }
    )
  }
}

@MainActor
private final class SpeechClock {
  private final class ScheduledAlert {
    let deadline: TimeInterval
    let fire: @MainActor () -> Void
    var isCancelled = false
    var hasFired = false

    init(deadline: TimeInterval, fire: @escaping @MainActor () -> Void) {
      self.deadline = deadline
      self.fire = fire
    }
  }

  var time: TimeInterval = 100
  private var timers: [ScheduledAlert] = []
  var pendingTimerCount: Int { timers.filter { !$0.isCancelled && !$0.hasFired }.count }

  func schedule(after delay: TimeInterval, fire: @escaping @MainActor () -> Void) -> (@MainActor () -> Void) {
    let timer = ScheduledAlert(deadline: time + delay, fire: fire)
    timers.append(timer)
    return { timer.isCancelled = true }
  }

  func advance(by interval: TimeInterval) { advance(to: time + interval) }

  func advance(to target: TimeInterval) {
    while let timer = timers.filter({ !$0.isCancelled && !$0.hasFired && $0.deadline <= target })
      .min(by: { $0.deadline < $1.deadline }) {
      time = timer.deadline
      timer.hasFired = true
      timer.fire()
    }
    time = target
  }

  func fireEvenIfCancelled(_ index: Int) { timers[index].fire() }
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
