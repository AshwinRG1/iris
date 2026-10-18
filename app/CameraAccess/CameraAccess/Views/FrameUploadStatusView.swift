import SwiftUI

struct FrameUploadStatusView: View {
  @Bindable var uploader: FrameUploader
  let display: GlassesDisplayController
  let canRetryDisplay: Bool
  let retryDisplay: () -> Void
  @Environment(\.dismiss) private var dismiss

  var body: some View {
    NavigationStack {
      Form {
        Section("Meta Ray-Ban Display") {
          LabeledContent("Display", value: display.statusText)
            .accessibilityIdentifier("glasses_display_status")
          LabeledContent("Detections", value: display.snapshot.heading.capitalized)
          if let error = display.errorMessage {
            Text(error)
              .foregroundStyle(.red)
            Button("Retry display", action: retryDisplay)
              .disabled(!canRetryDisplay)
              .accessibilityIdentifier("retry_glasses_display")
          }
        }

        Section("Iris server") {
          TextField("Server base URL", text: $uploader.serverURL)
            .keyboardType(.URL)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .accessibilityIdentifier("iris_server_url")
          SecureField("Server key", text: $uploader.serverKey)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .accessibilityIdentifier("iris_server_key")
          Toggle("Send frames and speak alerts", isOn: $uploader.isEnabled)
            .accessibilityIdentifier("iris_upload_toggle")
        }

        Section {
          Button {
            uploader.refreshServerHealth()
          } label: {
            HStack {
              Label("Check server", systemImage: "arrow.clockwise")
              Spacer()
              if uploader.isCheckingHealth { ProgressView() }
            }
          }
          .disabled(uploader.isCheckingHealth)
          .accessibilityIdentifier("iris_check_server")

          if let error = uploader.lastHealthError {
            Text(error)
              .foregroundStyle(.red)
              .accessibilityIdentifier("iris_health_error")
          }
          if let health = uploader.serverHealth {
            LabeledContent("Model", value: health.modelStatusText)
            LabeledContent("Detector", value: health.detector.isEmpty ? "Unknown" : health.detector)
            if let device = health.device { LabeledContent("Device", value: device) }
            LabeledContent("Server key", value: authenticationText(health.auth))
            if let loadError = health.loadError {
              LabeledContent("Model load error", value: loadError)
                .foregroundStyle(.red)
            }
            if let fps = health.fps {
              LabeledContent("Server frame rate", value: "\(fps.formatted(.number.precision(.fractionLength(1)))) fps")
            }
            if let p50 = health.serverMSP50 { LabeledContent("Processing p50", value: "\(p50) ms") }
            if let p95 = health.serverMSP95 { LabeledContent("Processing p95", value: "\(p95) ms") }
            if let fallback = health.fallback { LabeledContent("Fallback", value: fallback) }
            if let commit = health.commit { LabeledContent("Server version", value: String(commit.prefix(12))) }
          }
          if let checkedAt = uploader.lastHealthCheckedAt {
            LabeledContent("Checked") {
              Text(checkedAt, format: .dateTime.hour().minute().second())
            }
          }
        } header: {
          Text("Server health")
        } footer: {
          Text("Enter a key when the server requires one. The next uploaded frame verifies your key.")
        }

        Section("Status") {
          LabeledContent("Uploads", value: uploader.statusText)
          if let error = uploader.lastError {
            Text(error)
              .foregroundStyle(.red)
              .accessibilityIdentifier("iris_upload_error")
          }
          if let roundTrip = uploader.lastRoundTripMS {
            LabeledContent("Round trip", value: "\(roundTrip.formatted(.number.precision(.fractionLength(0)))) ms")
          }
          if let serverMS = uploader.lastServerMS {
            LabeledContent("Server processing", value: "\(serverMS) ms")
          }
          if let frameID = uploader.lastFrameID {
            LabeledContent("Frame", value: "\(frameID)")
          }
        }

        Section("Last alert") {
          Text(uploader.lastAlert ?? "No alerts received")
          if let receivedAt = uploader.lastAlertAt {
            LabeledContent("Received") {
              Text(receivedAt, format: .dateTime.hour().minute().second())
            }
          }
        }

        if let description = uploader.sceneDescription, !description.isEmpty {
          Section("Scene description") {
            Text(description)
          }
        }
      }
      .navigationTitle("Iris status")
      .toolbar {
        ToolbarItem(placement: .confirmationAction) {
          Button("Done") { dismiss() }
        }
      }
    }
    .preferredColorScheme(.dark)
  }

  private func authenticationText(_ auth: String?) -> String {
    switch auth {
    case "on": return "Required"
    case "off": return "Not required"
    default: return "Unknown"
    }
  }
}
