/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// CameraView.swift
//
// The capture screen: a full-bleed camera preview with controls overlaid on a
// scrim. Walks the SDK's camera lifecycle as explicit steps (Start Session →
// Start Streaming → Capture / Record → Stop Streaming → End Session) and shows the
// live DeviceSessionState / StreamState so the state machine is legible.
//

import MWDATCore
import SwiftUI
import UIKit

private let updateRequiredTitle = "Update required"

struct CameraView: View {
  @State private var viewModel: CameraViewModel
  @Bindable var wearablesVM: WearablesViewModel
  @State private var showSettingsMenu: Bool = false
  @State private var isLaunchingUpdate: Bool = false

  init(wearables: WearablesInterface, wearablesVM: WearablesViewModel) {
    self._viewModel = State(wrappedValue: CameraViewModel(wearables: wearables))
    self._wearablesVM = Bindable(wearablesVM)
  }

  private var isUpdateRequired: Bool {
    wearablesVM.requiresFirmwareUpdate
  }

  /// Whether the preview is live or tearing down — drives the in-row Preview
  /// toggle's icon, id, and action. (Recording implies a live preview.)
  private var previewIsActive: Bool {
    viewModel.isStreaming || viewModel.isRecording || viewModel.streamState == .stopping
  }

  var body: some View {
    ZStack {
      // Full-bleed preview (or a dark placeholder) fills the screen so overlaying
      // the controls never reflows it.
      previewBackground
        .edgesIgnoringSafeArea(.all)

      if let preview = viewModel.activePreview {
        CapturePreviewView(
          preview: preview,
          onDismiss: { viewModel.dismissCapturePreview() }
        )
        .transition(.opacity)
        .zIndex(1)
      }

      // Tap outside the open top-bar settings menu dismisses it.
      if showSettingsMenu {
        Color.clear
          .contentShape(Rectangle())
          .onTapGesture {
            withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
              showSettingsMenu = false
            }
          }
          .edgesIgnoringSafeArea(.all)
      }

      VStack(spacing: 0) {
        topBar
          .padding(.horizontal, 20)
          .padding(.top, 16)
          .padding(.bottom, 40)
          .background(
            LinearGradient(colors: [.black.opacity(0.6), .clear], startPoint: .top, endPoint: .bottom)
              .edgesIgnoringSafeArea(.top)
          )

        Spacer(minLength: 0)

        bottomBar
      }
    }
    .animation(.spring(response: 0.42, dampingFraction: 0.86), value: viewModel.hasSession)
    .animation(.easeInOut(duration: 0.2), value: previewIsActive)
    .animation(.easeInOut(duration: 0.2), value: viewModel.isStreaming)
    .animation(.easeInOut(duration: 0.2), value: viewModel.isPaused)
    .animation(.easeInOut(duration: 0.2), value: viewModel.includeAudioInStream)
    .animation(.easeInOut(duration: 0.2), value: viewModel.isRecording)
    .alert("Something went wrong", isPresented: $viewModel.showError) {
      Button("OK") { viewModel.dismissError() }
    } message: {
      Text(viewModel.errorMessage)
    }
    .alert("Allow camera access", isPresented: $viewModel.showCameraPermissionRedirectConfirm) {
      Button("Continue") { Task { await viewModel.confirmCameraPermissionRedirect() } }
      Button("Cancel", role: .cancel) {}
    } message: {
      Text("To preview your glasses camera, you'll be taken to the Meta AI app to grant access, then returned here.")
    }
    .navigationBarHidden(true)
  }

  // MARK: - Preview background

  @ViewBuilder
  private var previewBackground: some View {
    ZStack {
      Color.black
      if viewModel.showsLivePreview {
        // Live preview, the frozen last frame while paused, or the last frame held
        // while the stream tears down — so the paused badge / stop loader overlays
        // the preview rather than the start-streaming screen.
        LivePreviewView(viewModel: viewModel)
        if viewModel.isPaused {
          // Dim the frozen frame so the badge reads as intentionally paused.
          Color.black.opacity(0.35)
          pausedOverlay
        }
      } else if !viewModel.isBusy {
        statusPlaceholder
      }

      // Loader overlays whatever is behind it during any in-flight transition.
      if viewModel.isBusy {
        loadingOverlay
      }
    }
  }

  /// Label over the frozen preview while paused, so a held frame reads as
  /// intentionally paused (with how to resume) rather than a stalled feed. A
  /// `pause.fill` status glyph (not a circled/button shape) — resume is a glasses
  /// gesture, not an on-screen control.
  private var pausedOverlay: some View {
    VStack(spacing: 8) {
      Image(systemName: "pause.fill")
        .font(.system(size: 36))
        .foregroundStyle(.white)
      Text("Paused")
        .font(.system(size: 20, weight: .semibold))
        .foregroundStyle(.white)
      Text("Tap your glasses to resume.")
        .font(.system(size: 15))
        .multilineTextAlignment(.center)
        .foregroundStyle(Color.white.opacity(0.7))
    }
    .padding(.horizontal, 24)
    .accessibilityIdentifier("paused_overlay")
  }

  /// Dimmed spinner shown over the preview (or placeholder) while a session/stream
  /// start or stop is in flight.
  private var loadingOverlay: some View {
    ZStack {
      Color.black.opacity(0.35)
      ProgressView()
        .scaleEffect(1.5)
        .tint(.white)
    }
  }

  @ViewBuilder
  private var statusPlaceholder: some View {
    if !viewModel.hasActiveDevice {
      placeholder(title: "Put on your glasses", subtitle: nil, showWaitingRow: true)
    } else if isUpdateRequired {
      placeholder(title: updateRequiredTitle, subtitle: "Apply the update to start your camera.", showWaitingRow: false)
    } else if !viewModel.hasSession {
      placeholder(title: "Ready", subtitle: "Start a session to connect to your glasses.", showWaitingRow: false)
    } else if viewModel.isPaused {
      placeholder(title: "Paused", subtitle: "Tap your glasses to resume.", showWaitingRow: false)
    } else {
      placeholder(title: "Session started", subtitle: "Start the preview to see the live camera feed.", showWaitingRow: false)
    }
  }

  private func placeholder(title: String, subtitle: String?, showWaitingRow: Bool) -> some View {
    VStack(spacing: 12) {
      Image(.cameraAccessIcon)
        .resizable()
        .renderingMode(.template)
        .foregroundStyle(.white)
        .aspectRatio(contentMode: .fit)
        .frame(width: 88)

      Text(title)
        .font(.system(size: 20, weight: .semibold))
        .foregroundStyle(.white)

      if let subtitle {
        Text(subtitle)
          .font(.system(size: 15))
          .multilineTextAlignment(.center)
          .foregroundStyle(Color.white.opacity(0.7))
      }

      if showWaitingRow {
        HStack(spacing: 8) {
          Image(systemName: "hourglass")
            .resizable()
            .aspectRatio(contentMode: .fit)
            .foregroundStyle(Color.white.opacity(0.7))
            .frame(width: 16, height: 16)
          Text("Waiting for an active device")
            .font(.system(size: 14))
            .foregroundStyle(Color.white.opacity(0.7))
        }
      }
    }
    .padding(.horizontal, 24)
  }

  // MARK: - Top bar

  /// Top bar: compact live SDK state stacked on the left so the lifecycle is
  /// legible without heavy chrome, settings/disconnect on the right.
  private var topBar: some View {
    HStack(alignment: .top, spacing: 12) {
      VStack(alignment: .leading, spacing: 6) {
        statusChip(label: "Session", value: viewModel.sessionStateText, active: viewModel.isSessionActive, present: viewModel.hasSession)
        statusChip(label: "Stream", value: viewModel.streamStateText, active: viewModel.isStreaming, present: viewModel.hasStream)
      }

      Spacer()

      // Top bar carries only the app-level settings/Disconnect control.
      iconButton("gearshape", id: "settings_button") {
        withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
          showSettingsMenu.toggle()
        }
      }
      .overlay(alignment: .trailing) {
        if showSettingsMenu {
          CustomButton(
            title: "Disconnect",
            style: .destructive,
            isDisabled: wearablesVM.registrationState != .registered
          ) {
            wearablesVM.disconnectGlasses()
            withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) { showSettingsMenu = false }
          }
          .frame(width: 120)
          .offset(y: 44)
          .transition(.scale(scale: 0.01, anchor: .topTrailing).combined(with: .opacity))
        }
      }
    }
  }

  private func statusChip(label: String, value: String, active: Bool, present: Bool) -> some View {
    HStack(spacing: 6) {
      Circle()
        .fill(active ? Color.green : (present ? Color.yellow : Color.gray))
        .frame(width: 7, height: 7)
      Text("\(label): \(value)")
        .font(.system(size: 12, weight: .medium))
        .monospaced()
        .foregroundStyle(Color.white.opacity(0.85))
    }
  }

  private func iconButton(_ systemName: String, id: String, action: @escaping () -> Void) -> some View {
    Button(action: action) {
      Image(systemName: systemName)
        .resizable()
        .aspectRatio(contentMode: .fit)
        .foregroundStyle(.white)
        .frame(width: 22, height: 22)
        .padding(.leading, 16)
    }
    .accessibilityIdentifier(id)
  }

  // MARK: - Bottom bar

  private var bottomBar: some View {
    VStack(spacing: 14) {
      if isUpdateRequired {
        updateControls
      } else {
        // Reserved capture-row space + a single persistent button hold the button at
        // a fixed Y across every state.
        captureRow
        anchoredPrimaryButton
      }
    }
    .frame(maxWidth: .infinity)
    .padding(.horizontal, 24)
    .padding(.top, 48)
    .padding(.bottom, 28)
    .background(
      LinearGradient(colors: [.clear, .black.opacity(0.75)], startPoint: .top, endPoint: .bottom)
        .edgesIgnoringSafeArea(.bottom)
    )
  }

  /// In-session toolbar (Preview / Photo / Record / Mic), kept laid out so the
  /// session button below holds a fixed Y. Hidden without a session; Preview leads as
  /// the gateway and is the lone enabled control in session-ready, the rest enabling
  /// once streaming.
  private var captureRow: some View {
    HStack(spacing: 10) {
      previewPill
      photoButton
      recordPill
      micToggle
    }
    .opacity(viewModel.hasSession ? 1 : 0)
  }

  /// Photo and Record need a live stream.
  private var captureControlsEnabled: Bool { viewModel.isStreaming }
  /// Record/Stop stays available while recording even if the stream is paused, so a
  /// pause can't trap an in-progress recording; starting still needs a live stream.
  private var recordControlEnabled: Bool { viewModel.isStreaming || viewModel.isRecording }
  /// Mic additionally locks once recording starts — the audio setting can't change
  /// mid-take.
  private var micEnabled: Bool { viewModel.isStreaming && !viewModel.isRecording }

  /// Photo capture as a compact icon (no label) so the Preview and Record pills keep
  /// room for text. Enabled while streaming, including during a recording.
  private var photoButton: some View {
    Button {
      viewModel.capturePhoto()
    } label: {
      Image(systemName: "camera.fill")
        .font(.system(size: 16, weight: .semibold))
        .foregroundStyle(.white)
        .frame(width: 50, height: 50)
        .background(.ultraThinMaterial)
        .clipShape(Circle())
    }
    .accessibilityIdentifier("capture_button")
    .disabled(!captureControlsEnabled)
    .opacity(captureControlsEnabled ? 1 : 0.35)
  }

  /// Preview on/off — the gateway for capture. The eye icon flips and dims when off;
  /// locked mid-recording so a recording can't lose its feed.
  private var previewPill: some View {
    capturePill(
      icon: previewIsActive ? "eye.fill" : "eye.slash.fill",
      title: "Preview",
      contentTint: .white,
      id: previewIsActive ? "stop_preview_button" : "start_preview_button"
    ) {
      if previewIsActive {
        viewModel.stopStreaming()
      } else {
        Task { await viewModel.startStreaming() }
      }
    }
    .disabled(previewToggleDisabled)
    .opacity(previewToggleDisabled ? 0.35 : 1)
  }

  private var previewToggleDisabled: Bool {
    if previewIsActive {
      return viewModel.isRecording || viewModel.isBusy
    }
    return !viewModel.isSessionActive || viewModel.isBusy
  }

  /// The single full-width session button: Start Session, or End Session
  /// (destructive) once a session exists. One instance, never branched, so it never
  /// remounts and holds a fixed Y — only its title / style / id / action change.
  private var anchoredPrimaryButton: some View {
    CustomButton(
      title: viewModel.hasSession ? "End session" : "Start session",
      style: viewModel.hasSession ? .destructive : .primary,
      isDisabled: primaryDisabled
    ) {
      if viewModel.hasSession {
        viewModel.endSession()
      } else {
        viewModel.startSession()
      }
    }
    .accessibilityIdentifier(viewModel.hasSession ? "end_session_button" : "start_session_button")
  }

  /// Start needs an active device; End stays available mid-stream/record — the SDK
  /// cascades the stop, so only an in-flight transition disables it.
  private var primaryDisabled: Bool {
    if viewModel.hasSession {
      return viewModel.isBusy
    }
    return viewModel.isBusy || !viewModel.hasActiveDevice
  }

  private func primaryButton(_ title: String, id: String, disabled: Bool = false, action: @escaping () -> Void) -> some View {
    CustomButton(title: title, style: .primary, isDisabled: disabled, action: action)
      .accessibilityIdentifier(id)
  }

  /// Frosted icon+label capsule for the capture actions. `contentTint` colors both
  /// the icon and label so on/off dims them together (see `previewPill`).
  private func capturePill(icon: String, title: String, contentTint: Color = .white, id: String, action: @escaping () -> Void) -> some View {
    Button(action: action) {
      HStack(spacing: 8) {
        Image(systemName: icon)
          .font(.system(size: 15, weight: .semibold))
          .foregroundStyle(contentTint)
        Text(title)
          .font(.system(size: 15, weight: .semibold))
          .foregroundStyle(contentTint)
          .lineLimit(1)
          .minimumScaleFactor(0.85)
      }
      .frame(maxWidth: .infinity)
      .frame(height: 50)
      .background(.ultraThinMaterial)
      .clipShape(Capsule())
    }
    .accessibilityIdentifier(id)
  }

  /// Record/Stop capsule. Morphs in place while recording (red fill + the live timer
  /// in the isolated `RecordingTimerLabel`) so the bar keeps its shape and the
  /// per-frame timer doesn't re-render the surrounding controls.
  private var recordPill: some View {
    Button {
      viewModel.toggleRecording()
    } label: {
      Group {
        if viewModel.isRecording {
          RecordingTimerLabel(viewModel: viewModel)
        } else {
          HStack(spacing: 8) {
            Image(systemName: "video.fill")
              .font(.system(size: 15, weight: .semibold))
              .foregroundStyle(Color.recordAccent)
            Text("Record")
              .font(.system(size: 15, weight: .semibold))
              .foregroundStyle(.white)
          }
        }
      }
      .frame(maxWidth: .infinity)
      .frame(height: 50)
      .background {
        if viewModel.isRecording {
          ZStack {
            Capsule().fill(.ultraThinMaterial)
            Capsule().fill(Color.recordAccent.opacity(0.50))
          }
        } else {
          Capsule().fill(.ultraThinMaterial)
        }
      }
    }
    .accessibilityIdentifier("record_button")
    .disabled(!recordControlEnabled)
    .opacity(recordControlEnabled ? 1 : 0.35)
  }

  /// Sound-in-video toggle. Brightens on, dims/slashes when muted; locked until
  /// streaming and during recording. Denied reads as off and, since iOS won't
  /// re-prompt, tapping it opens Settings instead of toggling.
  private var micToggle: some View {
    let isOn = !viewModel.micDenied && viewModel.includeAudioInStream
    return Button {
      if viewModel.micDenied {
        if let url = URL(string: UIApplication.openSettingsURLString) {
          UIApplication.shared.open(url)
        }
      } else {
        viewModel.includeAudioInStream.toggle()
      }
    } label: {
      Image(systemName: isOn ? "mic.fill" : "mic.slash.fill")
        .font(.system(size: 16, weight: .semibold))
        .foregroundStyle(isOn ? .white : Color.white.opacity(0.45))
        .frame(width: 50, height: 50)
        .background(.ultraThinMaterial)
        .clipShape(Circle())
    }
    .disabled(!micEnabled)
    .opacity(micEnabled ? 1 : 0.35)
    .accessibilityIdentifier("mic_toggle")
  }

  // MARK: - Update controls

  @ViewBuilder
  private var updateControls: some View {
    UpdateRequiredMessage()
    // Gate the button while the external update flow launches so a double-tap can't
    // fire it twice; re-enabled once the await returns.
    primaryButton("Update firmware", id: "update_firmware_button", disabled: isLaunchingUpdate) {
      isLaunchingUpdate = true
      Task {
        await wearablesVM.openFirmwareUpdate()
        isLaunchingUpdate = false
      }
    }
  }
}

// MARK: - Live preview

/// The streaming preview image. Isolated from `CameraView` so frequent
/// (~24fps) `currentVideoFrame` updates re-render only this view, not the
/// surrounding controls.
private struct LivePreviewView: View {
  var viewModel: CameraViewModel

  var body: some View {
    if let videoFrame = viewModel.currentVideoFrame, viewModel.hasReceivedFirstFrame {
      GeometryReader { geometry in
        Image(uiImage: videoFrame)
          .resizable()
          .aspectRatio(contentMode: .fill)
          .frame(width: geometry.size.width, height: geometry.size.height)
          .clipped()
      }
    } else {
      ProgressView()
        .scaleEffect(1.5)
        .tint(.white)
    }
  }
}

// MARK: - Recording timer

/// Elapsed-time label shown inside the record button while recording. Self-drives
/// off the recording start via `TimelineView`, so it counts continuously — including
/// through a stream pause — and re-renders only this isolated label.
private struct RecordingTimerLabel: View {
  var viewModel: CameraViewModel

  var body: some View {
    HStack(spacing: 8) {
      Image(systemName: "stop.fill")
        .font(.system(size: 15, weight: .semibold))
        .foregroundStyle(Color.recordAccent)
      // Counts from the actual recording start (first written frame), so it tracks the
      // saved clip and keeps advancing through a pause; shows 00:00 until that frame.
      if let start = viewModel.recordingStartDate {
        TimelineView(.periodic(from: start, by: 1)) { context in
          timerText(context.date.timeIntervalSince(start))
        }
      } else {
        timerText(0)
      }
    }
  }

  private func timerText(_ elapsed: TimeInterval) -> some View {
    Text(elapsed.formattedRecordingTime)
      .font(.system(size: 15, weight: .semibold))
      .monospacedDigit()
      .foregroundStyle(.white)
      // Identifier on the Text so UI tests find it regardless of the time value.
      .accessibilityIdentifier("recording_indicator")
  }
}

// MARK: - Update banner

struct UpdateRequiredMessage: View {
  private let message = "Your glasses need a firmware update before Camera Access can start."

  var body: some View {
    HStack(alignment: .top, spacing: 12) {
      Image(systemName: "exclamationmark.triangle.fill")
        .resizable()
        .aspectRatio(contentMode: .fit)
        .foregroundStyle(Color.updateRequiredForeground)
        .frame(width: 24, height: 24)
        .accessibilityHidden(true)

      VStack(alignment: .leading, spacing: 4) {
        Text(updateRequiredTitle)
          .font(.system(size: 16, weight: .semibold))
          .foregroundStyle(Color.updateRequiredForeground)

        Text(message)
          .font(.system(size: 15))
          .foregroundStyle(Color.updateRequiredForeground)
          .fixedSize(horizontal: false, vertical: true)
      }

      Spacer(minLength: 0)
    }
    .padding(.all, 16)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(Color.updateRequiredBackground)
    .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
  }
}
