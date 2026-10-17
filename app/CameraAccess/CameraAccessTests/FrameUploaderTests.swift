@testable import CameraAccess
import CoreMedia
import Foundation
import Testing
import UIKit
import os

@Suite(.serialized)
@MainActor
struct FrameUploaderTests {
  @Test(arguments: ["frame-alert", "frame-quiet", "frame-extended"])
  func decodesContractFixtures(_ name: String) throws {
    let response = try JSONDecoder().decode(FrameResponse.self, from: fixture(name))
    switch name {
    case "frame-alert":
      #expect(response.detections.count == 2)
      #expect(response.alert == "possible person, left")
      #expect(response.frameID == nil)
      #expect(response.serverMS == nil)
      #expect(response.description == nil)
    case "frame-quiet":
      #expect(response.detections.isEmpty)
      #expect(response.alert == nil)
    default:
      #expect(response.detections.first?.label == "limb")
      #expect(response.frameID == 1842)
      #expect(response.serverMS == 96)
      #expect(response.description != nil)
    }
  }

  @Test func acceptsFutureLabelsAndDirections() throws {
    let data = Data(#"{"detections":[{"label":"new-class","conf":0.6,"side":"above"}],"alert":null,"future":true}"#.utf8)
    let response = try JSONDecoder().decode(FrameResponse.self, from: data)
    #expect(response.detections.first?.label == "new-class")
    #expect(response.detections.first?.side == "above")
  }

  @Test(arguments: ["http://10.138.20.97:8000", "https://203-0-113-10.nip.io"],
    ["", "/", "///", "/frame", "/frame/", "///frame///"])
  func normalizesSavedAndEditedURLsForHealthAndUploads(_ base: String, _ suffix: String) async throws {
    let input = " \n\(base)\(suffix)\t"
    let harness = Harness(replies: [
      .response(200, healthReply()), .response(200, try fixture("frame-quiet")),
    ], serverURL: input)
    let uploader = harness.uploader
    #expect(uploader.serverURL == base, "Previously saved endpoint URLs are migrated on launch")
    #expect(harness.defaults.string(forKey: "iris.serverURL") == base)

    uploader.serverURL = input
    #expect(harness.defaults.string(forKey: "iris.serverURL") == base)
    uploader.refreshServerHealth()
    try await waitUntil { !uploader.isCheckingHealth }
    #expect(uploader.serverHealth?.ready == true)
    #expect(uploader.lastHealthError == nil)

    uploader.isEnabled = true
    uploader.setStreaming(true)
    uploader.submit(testImage())
    try await waitUntil { !uploader.isUploading }
    #expect(UploadProtocol.requests.map { $0.url?.absoluteString } == ["\(base)/health", "\(base)/frame"])
    #expect(uploader.lastError == nil)
  }

  @Test func uploadsOneResizedJPEGWithTheExpectedMultipartContract() async throws {
    let harness = Harness(replies: [.response(200, try fixture("frame-alert"))])
    let uploader = harness.uploader
    uploader.setStreaming(true)
    let image = testImage()
    uploader.submit(image)
    #expect(UploadProtocol.requests.isEmpty, "Uploading starts disabled")

    uploader.isEnabled = true
    uploader.submit(image)
    try await waitUntil { !uploader.isUploading }

    let request = try #require(UploadProtocol.requests.first)
    #expect(UploadProtocol.requests.count == 1)
    #expect(request.httpMethod == "POST")
    #expect(request.url?.absoluteString == "https://iris.example.test/frame")
    #expect(request.timeoutInterval == 1.5)
    #expect(request.value(forHTTPHeaderField: "X-Iris-Key") == nil)
    let contentType = try #require(request.value(forHTTPHeaderField: "Content-Type"))
    let boundary = try #require(contentType.components(separatedBy: "boundary=").last)
    #expect(contentType.hasPrefix("multipart/form-data; boundary="))
    let body = try #require(request.httpBody)
    #expect(body.count <= 2_000_000)
    let header = Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"frame.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".utf8)
    let trailer = Data("\r\n--\(boundary)--\r\n".utf8)
    #expect(body.starts(with: header))
    #expect(body.suffix(trailer.count) == trailer)
    let jpeg = body.subdata(in: header.count..<(body.count - trailer.count))
    let uploaded = try #require(UIImage(data: jpeg)?.cgImage)
    #expect(uploaded.width == 640, "A Retina source must still upload exactly 640 pixels wide")
    #expect(uploaded.height == 360)
    #expect(harness.speaker.spoken == ["possible person, left"])
    #expect(uploader.lastAlertAt != nil)
    #expect(uploader.lastRoundTripMS != nil)
    #expect(uploader.lastError == nil)
  }

  @Test func dropsBusyFramesAndThrottlesWithoutQueuing() async throws {
    let quiet = try fixture("frame-quiet")
    let harness = Harness(replies: [.hold, .response(200, quiet), .response(200, quiet)])
    let uploader = harness.uploader
    let image = testImage()
    uploader.isEnabled = true
    uploader.setStreaming(true)
    uploader.submit(image)
    try await waitUntil { UploadProtocol.requests.count == 1 }

    harness.clock.time += 1
    for _ in 0..<100 { uploader.submit(image) }
    #expect(UploadProtocol.requests.count == 1)

    // Re-enabling immediately must not open another slot while cancellation unwinds.
    uploader.isEnabled = false
    uploader.isEnabled = true
    uploader.submit(image)
    #expect(UploadProtocol.requests.count == 1)
    try await waitUntil { !uploader.isUploading }
    #expect(UploadProtocol.cancellations == 1)
    #expect(harness.speaker.spoken.isEmpty)

    uploader.submit(image)
    try await waitUntil { !uploader.isUploading }
    #expect(UploadProtocol.requests.count == 2, "Skipped frames must never drain from a queue")
    harness.clock.time += 0.39
    uploader.submit(image)
    #expect(!uploader.isUploading)
    #expect(UploadProtocol.requests.count == 2)
    harness.clock.time += 0.02
    uploader.submit(image)
    try await waitUntil { !uploader.isUploading }
    #expect(UploadProtocol.requests.count == 3)
  }

  @Test func failedVideoDecodesSkipUploadsAndRecoverWithoutRestarting() async throws {
    let frames = try hevcFrames()
    let decoder = VideoFrameDecoder()
    #expect(decoder.decode(frames.predicted) == nil, "A new session waits for a keyframe")
    let harness = Harness(replies: [
      .response(200, try fixture("frame-alert")),
      .response(200, try fixture("frame-quiet")),
      .response(200, try fixture("frame-quiet")),
    ])
    let uploader = harness.uploader
    uploader.isEnabled = true
    uploader.setStreaming(true)
    uploader.submit(try #require(decoder.decode(frames.keyframe)))
    try await waitUntil { !uploader.isUploading }
    #expect(UploadProtocol.requests.count == 1)

    // Match the camera listener: only a non-nil decode is submitted. Advancing
    // beyond 400 ms ensures throttling cannot hide an accidentally reused image.
    for _ in 0..<3 {
      harness.clock.time += 1
      let image = decoder.decode(frames.corrupt)
      #expect(image == nil)
      if let image { uploader.submit(image) }
      try await waitUntil { !uploader.isUploading }
      #expect(UploadProtocol.requests.count == 1)
    }
    // Three failures recreate the session; predicted frames must stay skipped.
    for _ in 0..<3 {
      harness.clock.time += 1
      let image = decoder.decode(frames.predicted)
      #expect(image == nil)
      if let image { uploader.submit(image) }
      try await waitUntil { !uploader.isUploading }
      #expect(UploadProtocol.requests.count == 1)
    }
    #expect(harness.speaker.spoken == ["possible person, left"])

    for sample in [frames.keyframe, frames.predicted] {
      harness.clock.time += 1
      uploader.submit(try #require(decoder.decode(sample)))
      try await waitUntil { !uploader.isUploading }
    }
    #expect(UploadProtocol.requests.count == 3)
    #expect(uploader.isEnabled && uploader.isStreaming)
    #expect(uploader.lastError == nil)
    #expect(harness.speaker.spoken == ["possible person, left"])
  }

  @Test func recoversFromHTTPErrorsTimeoutsAndInvalidJSONWithoutSpeakingThem() async throws {
    let extended = try fixture("frame-extended")
    let harness = Harness(replies: [
      .response(401, Data(#"{"detail":"Unauthorized"}"#.utf8)),
      .failure(URLError(.timedOut)),
      .response(503, Data(#"{"detail":"Loading"}"#.utf8)),
      .response(200, Data("invalid JSON".utf8)),
      .response(200, extended),
      .response(200, try fixture("frame-quiet")),
      .response(200, extended),
    ])
    let uploader = harness.uploader
    uploader.serverKey = "test-key"
    uploader.isEnabled = true
    uploader.setStreaming(true)
    let image = testImage()

    for expectedError in ["Check server key", "timed out", "not ready", "decode"] {
      harness.clock.time += 1
      uploader.submit(image)
      try await waitUntil { !uploader.isUploading }
      #expect(uploader.lastError?.localizedCaseInsensitiveContains(expectedError) == true)
      #expect(uploader.isEnabled && uploader.isStreaming)
      #expect(uploader.detections.isEmpty)
      #expect(harness.speaker.spoken.isEmpty)
    }

    harness.clock.time += 1
    uploader.submit(image)
    try await waitUntil { !uploader.isUploading }
    #expect(uploader.lastError == nil)
    #expect(uploader.detections.first?.label == "limb")
    #expect(uploader.lastFrameID == 1842)
    #expect(uploader.lastServerMS == 96)
    #expect(uploader.sceneDescription != nil)
    #expect(harness.speaker.spoken == ["possible person, right"], "Scene descriptions are never spoken")
    let alertTime = uploader.lastAlertAt

    harness.clock.time += 1
    uploader.submit(image)
    try await waitUntil { !uploader.isUploading }
    #expect(uploader.detections.isEmpty)
    #expect(uploader.sceneDescription == nil)
    #expect(uploader.lastAlertAt == alertTime)
    #expect(harness.speaker.spoken.count == 1, "A quiet frame does not speak or erase alert history")

    harness.clock.time += 1
    uploader.submit(image)
    try await waitUntil { !uploader.isUploading }
    #expect(harness.speaker.spoken == ["possible person, right", "possible person, right"], "The server alone decides alert cooldowns")
    #expect(UploadProtocol.requests.allSatisfy { $0.value(forHTTPHeaderField: "X-Iris-Key") == "test-key" })
  }

  @Test func streamStopsAndSettingsChangesCancelPendingFrames() async throws {
    let harness = Harness(replies: [.hold, .response(200, try fixture("frame-alert")), .hold])
    let uploader = harness.uploader
    let image = testImage()
    uploader.isEnabled = true
    uploader.setStreaming(true)
    uploader.submit(image)
    try await waitUntil { UploadProtocol.requests.count == 1 }
    uploader.setStreaming(false)
    uploader.submit(image)
    try await waitUntil { !uploader.isUploading }
    #expect(uploader.detections.isEmpty)
    #expect(uploader.lastError == nil, "Intentional cancellation is not a network failure")
    #expect(harness.speaker.spoken.isEmpty)

    uploader.setStreaming(true)
    uploader.submit(image)
    try await waitUntil { !uploader.isUploading }
    #expect(uploader.detections.count == 2)
    harness.clock.time += 1
    uploader.submit(image)
    try await waitUntil { UploadProtocol.requests.count == 3 }
    uploader.serverURL = "https://replacement.example.test"
    #expect(uploader.detections.isEmpty)
    #expect(uploader.lastResponseAt == nil)
    try await waitUntil { !uploader.isUploading }
    #expect(UploadProtocol.cancellations == 2)
    #expect(harness.speaker.spoken == ["possible person, left"])
  }

  @Test func invalidConfigurationAndInvalidFramesRecoverLocally() async throws {
    let harness = Harness(replies: [.response(200, try fixture("frame-quiet"))])
    let uploader = harness.uploader
    uploader.isEnabled = true
    uploader.setStreaming(true)
    uploader.serverURL = "file:///tmp/not-a-server"
    uploader.submit(testImage())
    #expect(uploader.lastError != nil)
    #expect(UploadProtocol.requests.isEmpty)

    uploader.serverURL = "http://localhost:8000/"
    uploader.submit(UIImage())
    try await waitUntil { !uploader.isUploading }
    #expect(uploader.lastError?.contains("resize") == true)
    #expect(UploadProtocol.requests.isEmpty)
    harness.clock.time += 1
    uploader.submit(testImage())
    try await waitUntil { !uploader.isUploading }
    #expect(uploader.lastError == nil)
    #expect(UploadProtocol.requests.first?.url?.absoluteString == "http://localhost:8000/frame")
  }

  @Test func resizedImageSideLimitIsEnforcedAndTheNextFrameSucceeds() async throws {
    let harness = Harness(replies: [.response(200, try fixture("frame-quiet"))])
    let uploader = harness.uploader
    uploader.isEnabled = true
    uploader.setStreaming(true)
    // Even a highly compressible JPEG is invalid when a resized side is 4097 px.
    let pixels = Data(count: 640 * 4097 * 3)
    let provider = try #require(CGDataProvider(data: pixels as CFData))
    let cgImage = try #require(CGImage(
      width: 640, height: 4097, bitsPerComponent: 8, bitsPerPixel: 24,
      bytesPerRow: 640 * 3, space: CGColorSpaceCreateDeviceRGB(),
      bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
      provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent
    ))
    uploader.submit(UIImage(cgImage: cgImage))
    try await waitUntil { !uploader.isUploading }
    #expect(uploader.lastError?.contains("4096 px") == true)
    #expect(UploadProtocol.requests.isEmpty)
    #expect(harness.speaker.spoken.isEmpty)

    harness.clock.time += 1
    // The server accepts the boundary itself, not just ordinary landscape frames.
    let format = UIGraphicsImageRendererFormat()
    format.scale = 1
    let boundaryImage = UIGraphicsImageRenderer(size: CGSize(width: 640, height: 4096), format: format).image { context in
      UIColor.blue.setFill()
      context.fill(CGRect(x: 0, y: 0, width: 640, height: 4096))
    }
    uploader.submit(boundaryImage)
    try await waitUntil { !uploader.isUploading }
    #expect(UploadProtocol.requests.count == 1)
    #expect(uploader.lastError == nil)
  }

  @Test(arguments: ["  test-key\n", " \n\t"])
  func trimsKeyAndOmitsBlankHeaders(_ key: String) async throws {
    let harness = Harness(replies: [.response(200, try fixture("frame-quiet"))])
    harness.uploader.serverKey = key
    harness.uploader.isEnabled = true
    harness.uploader.setStreaming(true)
    harness.uploader.submit(testImage())
    try await waitUntil { !harness.uploader.isUploading }
    let request = try #require(UploadProtocol.requests.first)
    #expect(request.value(forHTTPHeaderField: "X-Iris-Key") == (key.contains("test-key") ? "test-key" : nil))
    #expect(request.url?.query == nil)
    #expect(harness.uploader.lastError == nil)
  }

  @Test(arguments: ["ab\u{0}cd", "ab\u{7f}cd", "ab\ncd", "cafékey"])
  func rejectsInvalidKeyBeforeAnyRequestAndRecovers(_ key: String) async throws {
    let harness = Harness(replies: [.response(200, try fixture("frame-quiet"))])
    let uploader = harness.uploader
    uploader.serverKey = key
    uploader.isEnabled = true
    uploader.setStreaming(true)
    uploader.submit(testImage())
    #expect(!uploader.isUploading)
    #expect(UploadProtocol.requests.isEmpty)
    #expect(uploader.lastError?.contains("invalid characters") == true)
    #expect(uploader.lastError?.contains(key) == false)

    uploader.serverKey = "valid-key"
    uploader.submit(testImage())
    try await waitUntil { !uploader.isUploading }
    #expect(UploadProtocol.requests.count == 1)
    #expect(uploader.lastError == nil)
  }

  @Test(arguments: [400, 401, 413, 422, 500, 503])
  func httpErrorsDoNotExposeResponseBodiesOrInventModelReadiness(_ status: Int) async throws {
    let secret = "private-api-key"
    let body = try JSONSerialization.data(withJSONObject: ["detail": "bad key \(secret)"])
    let harness = Harness(replies: [.response(status, body)])
    let uploader = harness.uploader
    uploader.serverKey = secret
    uploader.isEnabled = true
    uploader.setStreaming(true)
    uploader.submit(testImage())
    try await waitUntil { !uploader.isUploading }
    let error = try #require(uploader.lastError)
    #expect(!error.contains(secret))
    #expect(error.contains(String(status)))
    #expect(uploader.lastRoundTripMS == nil)
    #expect(uploader.lastFrameID == nil)
    #expect(uploader.isEnabled && uploader.isStreaming)
    #expect(harness.speaker.spoken.isEmpty)
    if status == 503 {
      #expect(error.contains("not ready"))
      #expect(!error.contains("loading"))
    }
  }

  @Test func transportErrorsNeverExposeRequestCredentials() async throws {
    let secret = "private-api-key"
    let error = URLError(.badURL, userInfo: [NSLocalizedDescriptionKey: "Illegal header value \(secret)"])
    let harness = Harness(replies: [.failure(error)])
    harness.uploader.serverKey = secret
    harness.uploader.isEnabled = true
    harness.uploader.setStreaming(true)
    harness.uploader.submit(testImage())
    try await waitUntil { !harness.uploader.isUploading }
    #expect(harness.uploader.lastError?.contains(secret) == false)
    #expect(harness.uploader.lastError?.contains("Server unreachable") == true)
  }

  @Test func healthIsPublicAndDoesNotAffectFrameStateOrSpeech() async throws {
    let harness = Harness(replies: [.response(200, healthReply())])
    let uploader = harness.uploader
    // Even an invalid upload key must not be attached to or block public /health.
    uploader.serverKey = "bad\u{0}key"
    uploader.refreshServerHealth()
    try await waitUntil { !uploader.isCheckingHealth }
    let request = try #require(UploadProtocol.requests.first)
    #expect(request.httpMethod == "GET")
    #expect(request.url?.absoluteString == "https://iris.example.test/health")
    #expect(request.value(forHTTPHeaderField: "X-Iris-Key") == nil)
    #expect(request.url?.query == nil)
    #expect(request.timeoutInterval == 1.5)
    #expect(request.cachePolicy == .reloadIgnoringLocalCacheData)
    #expect(uploader.serverHealth?.modelStatusText == "Ready")
    #expect(uploader.serverHealth?.auth == "on")
    #expect(uploader.serverHealth?.fps == 2.5)
    #expect(uploader.serverHealth?.serverMSP50 == 10)
    #expect(uploader.serverHealth?.serverMSP95 == 20)
    #expect(uploader.lastHealthCheckedAt != nil)
    #expect(uploader.lastHealthError == nil)
    #expect(!uploader.isEnabled && !uploader.isStreaming)
    #expect(uploader.lastResponseAt == nil)
    #expect(uploader.lastFrameID == nil)
    #expect(harness.speaker.spoken.isEmpty)
  }

  @Test func healthUsesItsOwnSessionDuringAnUpload() async throws {
    let harness = Harness(replies: [.hold, .response(200, healthReply())])
    let uploader = harness.uploader
    uploader.isEnabled = true
    uploader.setStreaming(true)
    uploader.submit(testImage())
    try await waitUntil { UploadProtocol.requests.count == 1 }

    uploader.refreshServerHealth()
    try await waitUntil { !uploader.isCheckingHealth }
    #expect(uploader.serverHealth?.ready == true)
    #expect(uploader.lastHealthError == nil)
    #expect(uploader.isUploading, "Health completes while the upload is still pending")
    #expect(UploadProtocol.requests.map { $0.value(forHTTPHeaderField: "X-Iris-Test-Session") } == ["upload", "health"])

    uploader.isEnabled = false
    try await waitUntil { !uploader.isUploading }
  }

  @Test func publicHealthDoesNotAuthenticateTheUploadKey() async throws {
    let harness = Harness(replies: [
      .response(401, Data(#"{"detail":"missing or wrong key"}"#.utf8)),
      .response(200, healthReply()),
      .response(200, try fixture("frame-alert")),
    ])
    let uploader = harness.uploader
    uploader.isEnabled = true
    uploader.setStreaming(true)
    uploader.submit(testImage())
    try await waitUntil { !uploader.isUploading }
    let authError = uploader.lastError
    uploader.refreshServerHealth()
    try await waitUntil { !uploader.isCheckingHealth }
    #expect(uploader.lastError == authError)
    #expect(uploader.lastResponseAt == nil)
    #expect(harness.speaker.spoken.isEmpty)

    harness.clock.time += 1
    uploader.submit(testImage())
    try await waitUntil { !uploader.isUploading }
    #expect(uploader.lastError == nil)
    #expect(harness.speaker.spoken == ["possible person, left"])
  }

  @Test func failedModelHealthDoesNotPreventFrameRecovery() async throws {
    let harness = Harness(replies: [
      .response(200, Data(#"{"ready":false,"detector":"yolo","load_error":"RuntimeError","auth":"off"}"#.utf8)),
      .response(200, try fixture("frame-quiet")),
      .response(200, healthReply()),
    ])
    let uploader = harness.uploader
    uploader.refreshServerHealth()
    try await waitUntil { !uploader.isCheckingHealth }
    #expect(uploader.serverHealth?.modelStatusText == "Model failed to load")
    #expect(uploader.lastHealthError == nil, "HTTP succeeded even though model loading failed")

    uploader.isEnabled = true
    uploader.setStreaming(true)
    uploader.submit(testImage())
    try await waitUntil { !uploader.isUploading }
    #expect(uploader.lastResponseAt != nil, "Readiness snapshots must not gate frame retries")
    uploader.refreshServerHealth()
    try await waitUntil { !uploader.isCheckingHealth }
    #expect(uploader.serverHealth?.modelStatusText == "Ready")
  }

  @Test func healthErrorsAreRedactedAndRecoverWithoutChangingUploadErrors() async throws {
    let secret = "private-api-key"
    let harness = Harness(replies: [
      .response(500, Data("bad key \(secret)".utf8)),
      .response(200, Data("invalid JSON".utf8)),
      .failure(URLError(.badURL, userInfo: [NSLocalizedDescriptionKey: secret])),
      .response(200, healthReply()),
    ])
    let uploader = harness.uploader
    for _ in 0..<3 {
      uploader.refreshServerHealth()
      try await waitUntil { !uploader.isCheckingHealth }
      #expect(uploader.serverHealth == nil)
      #expect(uploader.lastHealthCheckedAt == nil)
      #expect(uploader.lastHealthError != nil)
      #expect(uploader.lastHealthError?.contains(secret) == false)
      #expect(uploader.lastError == nil)
    }
    uploader.refreshServerHealth()
    try await waitUntil { !uploader.isCheckingHealth }
    #expect(uploader.serverHealth?.ready == true)
    #expect(uploader.lastHealthError == nil)
    #expect(harness.speaker.spoken.isEmpty)
  }

  @Test func changingServerCancelsHealthAndIgnoresStaleResults() async throws {
    let harness = Harness(replies: [.hold, .response(200, healthReply())])
    let uploader = harness.uploader
    uploader.refreshServerHealth()
    uploader.refreshServerHealth()
    try await waitUntil { UploadProtocol.requests.count == 1 }
    uploader.serverURL = "https://replacement.example.test"
    try await waitUntil { !uploader.isCheckingHealth }
    #expect(UploadProtocol.cancellations == 1)
    #expect(uploader.serverHealth == nil)
    #expect(uploader.lastHealthError == nil)
    uploader.refreshServerHealth()
    try await waitUntil { !uploader.isCheckingHealth }
    #expect(UploadProtocol.requests.last?.url?.host == "replacement.example.test")
    #expect(uploader.serverHealth?.ready == true)
  }

  @Test func serverRestartCanResetFrameIDsWithoutSuppressingAlerts() async throws {
    let replies = [98, 1].map { id in
      UploadProtocol.Reply.response(200, Data("{\"detections\":[],\"alert\":\"possible person, left\",\"frame_id\":\(id),\"server_ms\":10}".utf8))
    }
    let harness = Harness(replies: replies)
    harness.uploader.isEnabled = true
    harness.uploader.setStreaming(true)
    for _ in 0..<2 {
      harness.clock.time += 1
      harness.uploader.submit(testImage())
      try await waitUntil { !harness.uploader.isUploading }
    }
    #expect(harness.uploader.lastFrameID == 1)
    #expect(harness.speaker.spoken.count == 2)
  }

  @Test func healthChecksDoNotRefreshExpiredHUDDetections() async throws {
    let harness = Harness(replies: [.response(200, try fixture("frame-alert")), .response(200, healthReply())])
    let uploader = harness.uploader
    uploader.isEnabled = true
    uploader.setStreaming(true)
    uploader.submit(testImage())
    try await waitUntil { !uploader.isUploading }
    let receivedAt = try #require(uploader.lastResponseAt)
    #expect(uploader.hasFreshDetections(at: receivedAt.addingTimeInterval(1)))
    #expect(!uploader.hasFreshDetections(at: receivedAt.addingTimeInterval(2)))
    #expect(!uploader.hasFreshDetections(at: receivedAt.addingTimeInterval(-1)))
    uploader.refreshServerHealth()
    try await waitUntil { !uploader.isCheckingHealth }
    #expect(uploader.lastResponseAt == receivedAt)
    #expect(!uploader.hasFreshDetections(at: receivedAt.addingTimeInterval(3)))
    #expect(harness.speaker.spoken == ["possible person, left"])
  }
}

/// Shape from frame_routes.Health. Includes unknown/model metadata to exercise
/// the same tolerant decoding as /frame; /events alone owns boot_id and event IDs.
private func healthReply() -> Data {
  Data(#"{"ready":true,"detector":"fake","device":"cpu","load_error":null,"auth":"on","model_file":null,"model_sha256":null,"class_names":null,"fallback":null,"commit":"abc1234def","fps":2.5,"server_ms_p50":10,"server_ms_p95":20,"future_field":true}"#.utf8)
}

private final class FixtureBundle: NSObject {}

private func fixture(_ name: String) throws -> Data {
  let url = try #require(Bundle(for: FixtureBundle.self).url(forResource: name, withExtension: "json", subdirectory: "contract"))
  return try Data(contentsOf: url)
}

/// Two real 64x64 blue HEVC frames (IDR then predicted), encoded with libx265.
/// Embedded parameter sets avoid a device encoder or large video fixture in tests.
private func hevcFrames() throws -> (keyframe: CMSampleBuffer, predicted: CMSampleBuffer, corrupt: CMSampleBuffer) {
  let parameterSets = [
    "QAEMAf//AWAAAAMAkAAAAwAAAwAekoCQ",
    "QgEBAWAAAAMAkAAAAwAAAwAeoCCBBZZKkkyvAWgIAAADAAgAAAMA8EA=",
    "RAHBcrQiQA==",
  ].map { [UInt8](Data(base64Encoded: $0)!) }
  var format: CMFormatDescription?
  let status = parameterSets[0].withUnsafeBufferPointer { vps in
    parameterSets[1].withUnsafeBufferPointer { sps in
      parameterSets[2].withUnsafeBufferPointer { pps in
        var pointers = [vps.baseAddress!, sps.baseAddress!, pps.baseAddress!]
        var sizes = parameterSets.map { $0.count }
        return CMVideoFormatDescriptionCreateFromHEVCParameterSets(
          allocator: kCFAllocatorDefault, parameterSetCount: 3,
          parameterSetPointers: &pointers, parameterSetSizes: &sizes,
          nalUnitHeaderLength: 4, extensions: nil, formatDescriptionOut: &format)
      }
    }
  }
  try #require(status == noErr)
  let description = try #require(format)

  func sample(_ bytes: Data) throws -> CMSampleBuffer {
    var block: CMBlockBuffer?
    try #require(CMBlockBufferCreateWithMemoryBlock(
      allocator: kCFAllocatorDefault, memoryBlock: nil, blockLength: bytes.count,
      blockAllocator: kCFAllocatorDefault, customBlockSource: nil, offsetToData: 0,
      dataLength: bytes.count, flags: 0, blockBufferOut: &block) == noErr)
    let buffer = try #require(block)
    try #require(bytes.withUnsafeBytes {
      CMBlockBufferReplaceDataBytes(with: $0.baseAddress!, blockBuffer: buffer,
        offsetIntoDestination: 0, dataLength: bytes.count)
    } == noErr)
    var timing = CMSampleTimingInfo(duration: CMTime(value: 1, timescale: 30),
      presentationTimeStamp: .zero, decodeTimeStamp: .invalid)
    var size = bytes.count
    var result: CMSampleBuffer?
    try #require(CMSampleBufferCreateReady(
      allocator: kCFAllocatorDefault, dataBuffer: buffer, formatDescription: description,
      sampleCount: 1, sampleTimingEntryCount: 1, sampleTimingArray: &timing,
      sampleSizeEntryCount: 1, sampleSizeArray: &size, sampleBufferOut: &result) == noErr)
    return try #require(result)
  }

  return (
    try sample(Data(base64Encoded: "AAAAFCgBrwhg+e6tX/7jM+9R6fLS/rmA")!),
    try sample(Data(base64Encoded: "AAAACwIB0Al+EMYQwPuM")!),
    // A truncated NAL unit with a length prefix larger than its payload.
    try sample(Data([0, 0, 0, 5, 0x02]))
  )
}

@MainActor
private func testImage() -> UIImage {
  let format = UIGraphicsImageRendererFormat()
  format.scale = 2
  return UIGraphicsImageRenderer(size: CGSize(width: 1600, height: 900), format: format).image { context in
    UIColor.blue.setFill()
    context.fill(CGRect(x: 0, y: 0, width: 1600, height: 900))
  }
}

@MainActor
private func waitUntil(_ condition: () -> Bool) async throws {
  let deadline = ContinuousClock.now + .seconds(10)
  while !condition(), ContinuousClock.now < deadline {
    try await Task.sleep(for: .milliseconds(10))
  }
  try #require(condition(), "Upload did not finish within ten seconds")
}

@MainActor
private final class SpeechSpy: FrameAlertSpeaking {
  var spoken: [String] = []
  func speak(_ text: String) { spoken.append(text) }
  func stop() {}
}

@MainActor
private final class TestClock {
  var time: TimeInterval = 100
}

@MainActor
private final class Harness {
  let uploader: FrameUploader
  let speaker = SpeechSpy()
  let clock = TestClock()
  private let session: URLSession
  private let healthSession: URLSession
  let defaults: UserDefaults
  private let suiteName = "FrameUploaderTests.\(UUID().uuidString)"

  init(replies: [UploadProtocol.Reply], serverURL: String = "https://iris.example.test") {
    UploadProtocol.reset(replies: replies)
    defaults = UserDefaults(suiteName: suiteName)!
    defaults.set(serverURL, forKey: "iris.serverURL")
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [UploadProtocol.self]
    configuration.httpMaximumConnectionsPerHost = 1
    configuration.httpAdditionalHeaders = ["X-Iris-Test-Session": "upload"]
    session = URLSession(configuration: configuration)
    let healthConfiguration = URLSessionConfiguration.ephemeral
    healthConfiguration.protocolClasses = [UploadProtocol.self]
    healthConfiguration.httpMaximumConnectionsPerHost = 1
    healthConfiguration.httpAdditionalHeaders = ["X-Iris-Test-Session": "health"]
    healthSession = URLSession(configuration: healthConfiguration)
    let clock = clock
    uploader = FrameUploader(defaults: defaults, session: session, healthSession: healthSession, speaker: speaker, now: { clock.time })
  }

  isolated deinit {
    uploader.isEnabled = false
    session.invalidateAndCancel()
    healthSession.invalidateAndCancel()
    defaults.removePersistentDomain(forName: suiteName)
  }
}

/// A controlled URL loading transport. Held replies only finish on cancellation,
/// making overlap and stale-frame behavior observable without a running Iris server.
private final class UploadProtocol: URLProtocol, @unchecked Sendable {
  enum Reply: Sendable {
    case response(Int, Data)
    case failure(URLError)
    case hold
  }

  private struct State {
    var replies: [Reply] = []
    var requests: [URLRequest] = []
    var cancellations = 0
  }

  private static let state = OSAllocatedUnfairLock(uncheckedState: State())
  private let isHolding = OSAllocatedUnfairLock(initialState: false)

  static var requests: [URLRequest] { state.withLockUnchecked { $0.requests } }
  static var cancellations: Int { state.withLockUnchecked { $0.cancellations } }

  static func reset(replies: [Reply]) {
    state.withLockUnchecked { $0 = State(replies: replies) }
  }

  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

  override func startLoading() {
    var captured = request
    if captured.httpBody == nil, let stream = request.httpBodyStream {
      stream.open()
      defer { stream.close() }
      var data = Data()
      var buffer = [UInt8](repeating: 0, count: 8192)
      while true {
        let count = stream.read(&buffer, maxLength: buffer.count)
        if count <= 0 { break }
        data.append(contentsOf: buffer.prefix(count))
      }
      captured.httpBody = data
    }
    let reply = Self.state.withLockUnchecked { state in
      state.requests.append(captured)
      return state.replies.isEmpty ? Reply.failure(URLError(.badServerResponse)) : state.replies.removeFirst()
    }
    switch reply {
    case .response(let status, let data):
      let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
      client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
      client?.urlProtocol(self, didLoad: data)
      client?.urlProtocolDidFinishLoading(self)
    case .failure(let error):
      client?.urlProtocol(self, didFailWithError: error)
    case .hold:
      isHolding.withLock { $0 = true }
    }
  }

  override func stopLoading() {
    // URLSession can also call stopLoading after a successful response. Count
    // cancellation only when a deliberately held request was still pending.
    let wasHolding = isHolding.withLock { holding in
      defer { holding = false }
      return holding
    }
    if wasHolding {
      Self.state.withLockUnchecked { $0.cancellations += 1 }
    }
  }
}
