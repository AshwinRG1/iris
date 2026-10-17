import AVFoundation
import Foundation
import Observation
import UIKit
import os

/// Keep labels and directions open-ended so new server classes do not break decoding.
struct FrameDetection: Codable, Equatable, Sendable {
  let label: String
  let conf: Double
  let side: String
}

struct FrameResponse: Decodable, Equatable, Sendable {
  let detections: [FrameDetection]
  let alert: String?
  let frameID: Int?
  let serverMS: Int?
  let description: String?

  private enum CodingKeys: String, CodingKey {
    case detections, alert, description
    case frameID = "frame_id"
    case serverMS = "server_ms"
  }
}

/// /health is public, including when /frame requires X-Iris-Key. Its readiness
/// describes the model; a successful health check does not authenticate uploads.
struct IrisServerHealth: Decodable, Equatable, Sendable {
  let ready: Bool
  let detector: String
  let device: String?
  let loadError: String?
  let auth: String?
  let fps: Double?
  let serverMSP50: Int?
  let serverMSP95: Int?
  let commit: String?
  let fallback: String?

  var modelStatusText: String {
    if loadError != nil { return "Model failed to load" }
    if !ready { return "Model not ready" }
    if detector.isEmpty { return "No detector reported" }
    return "Ready"
  }

  private enum CodingKeys: String, CodingKey {
    case ready, detector, device, auth, fps, commit, fallback
    case loadError = "load_error"
    case serverMSP50 = "server_ms_p50"
    case serverMSP95 = "server_ms_p95"
  }
}

@MainActor
protocol FrameAlertSpeaking {
  func speak(_ text: String)
  func stop()
}

/// One synthesizer for the lifetime of the uploader; the current audio route also
/// carries speech to connected glasses. Do not override the recording audio session.
@MainActor
final class FrameAlertSpeaker: FrameAlertSpeaking {
  private let synthesizer = AVSpeechSynthesizer()

  func speak(_ text: String) {
    if synthesizer.isSpeaking {
      synthesizer.stopSpeaking(at: .immediate)
    }
    synthesizer.speak(AVSpeechUtterance(string: text))
  }

  func stop() {
    synthesizer.stopSpeaking(at: .immediate)
  }
}

/// Feed decoded camera images into `submit(_:)` as they arrive. Admission happens
/// before JPEG work: frames arriving within 400 ms or during an upload are dropped,
/// so neither encoding nor networking can accumulate a backlog.
@Observable
@MainActor
final class FrameUploader {
  var serverURL: String {
    didSet {
      guard serverURL != oldValue else { return }
      defaults.set(serverURL, forKey: "iris.serverURL")
      invalidateCurrentFrame()
      invalidateServerHealth()
    }
  }

  var serverKey: String {
    didSet {
      guard serverKey != oldValue else { return }
      defaults.set(serverKey, forKey: "iris.serverKey")
      invalidateCurrentFrame()
    }
  }

  /// Start disabled on each launch: sending camera images requires an explicit toggle.
  var isEnabled = false {
    didSet {
      guard isEnabled != oldValue else { return }
      invalidateCurrentFrame()
    }
  }

  private(set) var isStreaming = false
  private(set) var isUploading = false
  private(set) var detections: [FrameDetection] = []
  private(set) var sceneDescription: String?
  private(set) var lastResponseAt: Date?
  private(set) var lastAlert: String?
  private(set) var lastAlertAt: Date?
  private(set) var lastError: String?
  private(set) var lastRoundTripMS: Double?
  private(set) var lastServerMS: Int?
  private(set) var lastFrameID: Int?
  private(set) var serverHealth: IrisServerHealth?
  private(set) var isCheckingHealth = false
  private(set) var lastHealthError: String?
  private(set) var lastHealthCheckedAt: Date?

  var statusText: String {
    if !isEnabled { return "Off" }
    if Self.endpoint(serverURL, path: "frame") == nil { return "Enter a valid server URL" }
    if Self.normalizedKey(serverKey) == nil { return "Check server key" }
    if !isStreaming { return "Waiting for live preview" }
    if lastError != nil { return "Retrying" }
    return lastResponseAt == nil ? "Waiting for server" : "Uploading frames"
  }

  @ObservationIgnored private let defaults: UserDefaults
  @ObservationIgnored private let session: URLSession
  @ObservationIgnored private let speaker: any FrameAlertSpeaking
  @ObservationIgnored private let now: () -> TimeInterval
  @ObservationIgnored private var uploadTask: Task<Void, Never>?
  @ObservationIgnored private var lastAttempt: TimeInterval?
  @ObservationIgnored private var generation = 0
  @ObservationIgnored private var healthTask: Task<Void, Never>?
  @ObservationIgnored private var healthGeneration = 0

  private static let interval: TimeInterval = 0.4
  private static let timeout: TimeInterval = 1.5
  // server/config.py caps the whole multipart body, not just the JPEG.
  nonisolated private static let maximumBodyBytes = 2_000_000
  // server/frame_routes.py refuses an image with either side above this size.
  nonisolated private static let maximumImageSide = 4096
  private static let logger = Logger(subsystem: "com.iris.CameraAccess", category: "FrameUploader")

  init(
    defaults: UserDefaults = .standard,
    session: URLSession? = nil,
    speaker: any FrameAlertSpeaking = FrameAlertSpeaker(),
    now: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }
  ) {
    self.defaults = defaults
    self.serverURL = defaults.string(forKey: "iris.serverURL") ?? ""
    self.serverKey = defaults.string(forKey: "iris.serverKey") ?? ""
    self.speaker = speaker
    self.now = now

    if let session {
      self.session = session
    } else {
      let configuration = URLSessionConfiguration.ephemeral
      configuration.timeoutIntervalForRequest = Self.timeout
      configuration.timeoutIntervalForResource = Self.timeout
      configuration.waitsForConnectivity = false
      configuration.httpMaximumConnectionsPerHost = 1
      configuration.urlCache = nil
      self.session = URLSession(configuration: configuration)
    }
  }

  isolated deinit {
    uploadTask?.cancel()
    healthTask?.cancel()
    speaker.stop()
  }

  /// Call on every stream transition, including pause, stop and background teardown.
  func setStreaming(_ streaming: Bool) {
    guard isStreaming != streaming else { return }
    isStreaming = streaming
    invalidateCurrentFrame()
  }

  func submit(_ image: UIImage) {
    guard isEnabled, isStreaming, !isUploading else { return }
    let instant = now()
    if let lastAttempt, instant - lastAttempt < Self.interval { return }
    lastAttempt = instant

    guard let endpoint = Self.endpoint(serverURL, path: "frame") else {
      recordFailure("Enter a valid HTTP or HTTPS server URL.")
      return
    }

    // Match scripts/replay.py: trim surrounding whitespace, then reject control
    // characters and non-ASCII keys before encoding or asking URLSession to send.
    guard let key = Self.normalizedKey(serverKey) else {
      recordFailure("Server key has invalid characters. Use printable ASCII characters.")
      return
    }
    let requestGeneration = generation
    let session = session
    isUploading = true
    uploadTask = Task { [weak self] in
      // This slot stays occupied until cancellation finishes, even if the user
      // rapidly switches off/on or changes servers. A second upload cannot overlap.
      defer {
        self?.isUploading = false
        self?.uploadTask = nil
      }

      do {
        let boundary = "Iris-\(UUID().uuidString)"
        let preparation = Task.detached(priority: .utility) {
          try Self.multipartBody(for: image, boundary: boundary)
        }
        let body = try await withTaskCancellationHandler {
          try await preparation.value
        } onCancel: {
          preparation.cancel()
        }
        try Task.checkCancellation()

        var request = URLRequest(url: endpoint, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: Self.timeout)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if !key.isEmpty { request.setValue(key, forHTTPHeaderField: "X-Iris-Key") }

        let start = ContinuousClock.now
        let (data, response) = try await session.upload(for: request, from: body)
        let elapsed = start.duration(to: .now).components
        let roundTripMS = Double(elapsed.seconds) * 1_000 + Double(elapsed.attoseconds) / 1e15
        try Task.checkCancellation()
        guard let self, generation == requestGeneration, isEnabled, isStreaming else { return }

        guard let http = response as? HTTPURLResponse else {
          recordFailure("The server returned an invalid HTTP response.")
          return
        }
        guard http.statusCode == 200 else {
          recordFailure(Self.httpError(status: http.statusCode))
          return
        }

        let reply = try JSONDecoder().decode(FrameResponse.self, from: data)
        detections = reply.detections
        sceneDescription = reply.description
        lastResponseAt = Date()
        lastRoundTripMS = roundTripMS
        lastServerMS = reply.serverMS
        lastFrameID = reply.frameID
        lastError = nil

        if let alert = reply.alert, !alert.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
          lastAlert = alert
          lastAlertAt = lastResponseAt
          speaker.speak(alert)
        }
      } catch {
        guard !Task.isCancelled, let self, generation == requestGeneration else { return }
        if error is DecodingError {
          recordFailure("Couldn't decode the server reply.")
        } else if let error = error as? PreparationError {
          recordFailure(error.localizedDescription)
        } else {
          recordFailure(Self.networkError(error))
        }
      }
    }
  }

  /// Explicit status-screen action. Never sends the key, changes frame detections,
  /// speaks alerts, or gates retries on a possibly stale readiness snapshot.
  func refreshServerHealth() {
    guard !isCheckingHealth else { return }
    guard let endpoint = Self.endpoint(serverURL, path: "health") else {
      serverHealth = nil
      lastHealthError = "Enter a valid HTTP or HTTPS server URL."
      return
    }

    let requestGeneration = healthGeneration
    let session = session
    isCheckingHealth = true
    lastHealthError = nil
    healthTask = Task { [weak self] in
      defer {
        self?.isCheckingHealth = false
        self?.healthTask = nil
      }
      do {
        var request = URLRequest(url: endpoint, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: Self.timeout)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await session.data(for: request)
        try Task.checkCancellation()
        guard let self, healthGeneration == requestGeneration else { return }
        guard let http = response as? HTTPURLResponse else {
          recordHealthFailure("The server returned an invalid HTTP response.")
          return
        }
        guard http.statusCode == 200 else {
          recordHealthFailure("Health check returned HTTP \(http.statusCode).")
          return
        }
        let health = try JSONDecoder().decode(IrisServerHealth.self, from: data)
        serverHealth = health
        lastHealthCheckedAt = Date()
        lastHealthError = nil
      } catch {
        guard !Task.isCancelled, let self, healthGeneration == requestGeneration else { return }
        recordHealthFailure(error is DecodingError ? "Couldn't decode server health." : Self.networkError(error))
      }
    }
  }

  /// /frame has only directions. /latest boxes describe a saved server JPEG and
  /// must not be assigned to whichever local camera frame happens to be visible.
  func hasFreshDetections(at date: Date) -> Bool {
    guard isEnabled, isStreaming, let receivedAt = lastResponseAt else { return false }
    return (0..<2).contains(date.timeIntervalSince(receivedAt))
  }

  private func invalidateCurrentFrame() {
    generation += 1
    uploadTask?.cancel()
    lastAttempt = nil
    detections = []
    sceneDescription = nil
    lastResponseAt = nil
    lastRoundTripMS = nil
    lastServerMS = nil
    lastFrameID = nil
    lastError = nil
    speaker.stop()
  }

  private func recordFailure(_ message: String) {
    // A failed/quiet frame has no current detections. Keep the last alert as history.
    detections = []
    sceneDescription = nil
    lastResponseAt = nil
    lastRoundTripMS = nil
    lastServerMS = nil
    lastFrameID = nil
    let changed = lastError != message
    lastError = message
    if changed { Self.logger.error("\(message, privacy: .public)") }
  }

  private func invalidateServerHealth() {
    healthGeneration += 1
    healthTask?.cancel()
    serverHealth = nil
    lastHealthError = nil
    lastHealthCheckedAt = nil
  }

  private func recordHealthFailure(_ message: String) {
    serverHealth = nil
    lastHealthCheckedAt = nil
    let changed = lastHealthError != message
    lastHealthError = message
    if changed { Self.logger.error("\(message, privacy: .public)") }
  }

  private static func normalizedKey(_ value: String) -> String? {
    let key = value.trimmingCharacters(in: .whitespacesAndNewlines)
    return key.unicodeScalars.allSatisfy { (0x20...0x7E).contains($0.value) } ? key : nil
  }

  private static func endpoint(_ value: String, path: String) -> URL? {
    guard let components = URLComponents(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
      let scheme = components.scheme?.lowercased(), ["http", "https"].contains(scheme),
      let host = components.host, !host.isEmpty,
      components.user == nil, components.password == nil,
      components.query == nil, components.fragment == nil,
      let baseURL = components.url
    else { return nil }
    return baseURL.appendingPathComponent(path)
  }

  private static func httpError(status: Int) -> String {
    // Like the replay harness, use fixed messages. An error body or a transport's
    // localizedDescription can echo request credentials and must not enter logs/UI.
    switch status {
    case 400: return "Server rejected the JPEG image (400)."
    case 401: return "Check server key (401)."
    case 413: return "Frame exceeds the server's upload limit (413)."
    case 422: return "Server rejected the frame form data (422)."
    case 503: return "Server model is not ready (503). Check server health."
    default: return "Server returned HTTP \(status)."
    }
  }

  private static func networkError(_ error: Error) -> String {
    if let error = error as? URLError, error.code == .timedOut {
      return "Server unreachable: request timed out."
    }
    return "Server unreachable. Check the server URL and connection."
  }

  private enum PreparationError: LocalizedError {
    case invalidImage, imageSideTooLarge, jpegEncoding, bodyTooLarge

    var errorDescription: String? {
      switch self {
      case .invalidImage: return "Couldn't resize the camera frame."
      case .imageSideTooLarge: return "Skipped a frame whose resized height exceeds the server's 4096 px limit."
      case .jpegEncoding: return "Couldn't encode the camera frame as JPEG."
      case .bodyTooLarge: return "Skipped a frame larger than 2 MB."
      }
    }
  }

  /// Runs off the main actor, only for admitted frames. Drawing normalizes UIImage
  /// orientation, and scale=1 ensures 640 *pixels* even on Retina displays.
  nonisolated private static func multipartBody(for image: UIImage, boundary: String) throws -> Data {
    try Task.checkCancellation()
    let size = image.size
    guard size.width > 0, size.height > 0, size.width.isFinite, size.height.isFinite else {
      throw PreparationError.invalidImage
    }
    let height = max(1, (640 * size.height / size.width).rounded())
    guard height.isFinite, height <= CGFloat(maximumImageSide) else { throw PreparationError.imageSideTooLarge }
    let target = CGSize(width: 640, height: height)
    let format = UIGraphicsImageRendererFormat()
    format.scale = 1
    format.opaque = true
    format.preferredRange = .standard
    let resized = UIGraphicsImageRenderer(size: target, format: format).image { _ in
      image.draw(in: CGRect(origin: .zero, size: target))
    }
    guard let jpeg = resized.jpegData(compressionQuality: 0.7) else { throw PreparationError.jpegEncoding }
    try Task.checkCancellation()

    var body = Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"frame.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".utf8)
    // Check before appending a potentially large payload to a second allocation.
    let trailer = Data("\r\n--\(boundary)--\r\n".utf8)
    guard body.count + jpeg.count + trailer.count <= maximumBodyBytes else { throw PreparationError.bodyTooLarge }
    body.append(jpeg)
    body.append(trailer)
    return body
  }
}
