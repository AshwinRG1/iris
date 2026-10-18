import Foundation
import MWDATCore
import MWDATDisplay
import Observation
import UIKit

#if DEBUG
import MWDATMockDevice
#endif

/// Shares the camera's DeviceSession; never opens a competing session or enables
/// MockDeviceKit while real hardware is connected.
@Observable
@MainActor
final class GlassesDisplayController {
  enum Connection: Equatable { case disconnected, starting, connected, failed }

  private(set) var snapshot = DetectionHUDSnapshot.off
  private(set) var previewImage = DetectionHUDRenderer.render(.off)
  private(set) var connection = Connection.disconnected
  private(set) var errorMessage: String?
  private(set) var isDelivered = false
  private(set) var isMockDisplay = false
  private(set) var isSessionActive = false

  @ObservationIgnored private var display: MWDATDisplay.Display?
  @ObservationIgnored private var stateToken: (any AnyListenerToken)?
  @ObservationIgnored private var sendTask: Task<Void, Never>?
  @ObservationIgnored private var readinessTask: Task<Void, Never>?
  @ObservationIgnored private var sendTimeoutTask: Task<Void, Never>?
  @ObservationIgnored private var delivery = HUDDeliveryBuffer(DetectionHUDSnapshot.off)
  @ObservationIgnored private var generation = 0
  @ObservationIgnored private var nextRetryAt: TimeInterval = 0

  var statusText: String {
    switch connection {
    case .disconnected: return "Display not connected"
    case .starting: return "Connecting glasses display…"
    case .failed: return "Glasses display unavailable"
    case .connected:
      if !isSessionActive { return "Glasses display paused" }
      if errorMessage != nil { return "Glasses update failed · retrying" }
      if !isDelivered { return "Updating glasses display…" }
      return isMockDisplay ? "HUD sent to mock display" : "HUD sent to glasses"
    }
  }

  func update(_ value: DetectionHUDSnapshot) {
    if value != snapshot {
      snapshot = value
      previewImage = DetectionHUDRenderer.render(value)
      delivery.update(value)
      isDelivered = delivery.isDelivered
    }
    sendIfNeeded()
  }

  func attach(to session: DeviceSession) {
    guard display == nil, session.state == .started else { return }
    stop()
    isSessionActive = true
    guard session.device?.deviceType() == .metaRayBanDisplay else {
      failAndStop("Connect Meta Ray-Ban Display glasses to use Iris.")
      return
    }
    #if DEBUG
    isMockDisplay = MockDeviceKit.shared.pairedDevices.contains { $0.deviceIdentifier == session.deviceId }
    #endif
    do {
      let capability = try session.addDisplay()
      display = capability
      connection = .starting
      let epoch = generation
      stateToken = capability.statePublisher.listen { [weak self] state in
        Task { @MainActor [weak self] in
          guard let self, generation == epoch else { return }
          handleState(state)
        }
      }
      capability.start()
      readinessTask = Task { @MainActor [weak self] in
        do { try await Task.sleep(for: .seconds(10)) } catch { return }
        guard let self, generation == epoch, connection == .starting else { return }
        failAndStop("The glasses display did not become ready. Retry the display connection.")
      }
    } catch {
      connection = .failed
      errorMessage = error.localizedDescription
    }
  }

  func setSessionActive(_ active: Bool) {
    isSessionActive = active
    if active { sendIfNeeded() }
  }

  func stop() {
    // Invalidate before stopping: the SDK may synchronously announce .stopped.
    generation += 1
    sendTask?.cancel()
    sendTask = nil
    readinessTask?.cancel()
    readinessTask = nil
    sendTimeoutTask?.cancel()
    sendTimeoutTask = nil
    stateToken = nil
    display?.stop()
    display = nil
    delivery.reset()
    isDelivered = false
    isSessionActive = false
    isMockDisplay = false
    nextRetryAt = 0
    errorMessage = nil
    connection = .disconnected
  }

  isolated deinit {
    sendTask?.cancel()
    readinessTask?.cancel()
    sendTimeoutTask?.cancel()
    display?.stop()
  }

  private func handleState(_ state: DisplayState) {
    switch state {
    case .starting: connection = .starting
    case .started:
      readinessTask?.cancel()
      readinessTask = nil
      connection = .connected
      sendIfNeeded()
    case .stopping, .stopped:
      failAndStop("The glasses display stopped. Retry the display connection.")
    }
  }

  private func failAndStop(_ message: String) {
    stop()
    connection = .failed
    errorMessage = message
  }

  private func sendIfNeeded() {
    guard connection == .connected, isSessionActive, sendTask == nil,
          ProcessInfo.processInfo.systemUptime >= nextRetryAt,
          let display, let ticket = delivery.begin() else { return }
    let image = DetectionHUDRenderer.displayImage(from: previewImage)
    let epoch = generation
    isDelivered = false
    sendTimeoutTask = Task { @MainActor [weak self] in
      do { try await Task.sleep(for: .seconds(3)) } catch { return }
      guard let self, generation == epoch, delivery.inFlight == ticket else { return }
      failAndStop("The glasses display did not respond. Retry the display connection.")
    }
    sendTask = Task { @MainActor [weak self] in
      do {
        // A single root view is required by DAT. The square image contains only
        // HUD graphics; camera video is never sent back to the glasses display.
        try await display.send(
          FlexBox(direction: .column, alignment: .center, crossAlignment: .stretch) {
            MWDATDisplay.Image(image: image, sizePreset: .fill)
          }
        )
        guard let self, !Task.isCancelled, generation == epoch else { return }
        delivery.complete(ticket, succeeded: true)
        errorMessage = nil
        finishSend()
        // If detections changed during the transfer, skip intermediate states.
        sendIfNeeded()
      } catch {
        guard let self, !Task.isCancelled, generation == epoch else { return }
        delivery.complete(ticket, succeeded: false)
        errorMessage = error.localizedDescription
        nextRetryAt = ProcessInfo.processInfo.systemUptime + 1
        finishSend()
      }
    }
  }

  private func finishSend() {
    sendTimeoutTask?.cancel()
    sendTimeoutTask = nil
    sendTask = nil
    isDelivered = delivery.isDelivered
  }
}
