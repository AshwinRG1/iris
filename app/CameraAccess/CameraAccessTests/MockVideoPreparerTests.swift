#if DEBUG

@testable import CameraAccess
import AVFoundation
import CoreGraphics
import CoreMedia
import CoreVideo
import Foundation
import Testing

@Suite(.serialized)
struct MockVideoPreparerTests {
  @Test func preservesHEVCWithoutReencoding() async throws {
    let source = try #require(Bundle.main.url(forResource: "plant", withExtension: "mp4"))
    let prepared = try await MockVideoPreparer.prepare(from: source)
    defer { try? FileManager.default.removeItem(at: prepared) }
    #expect(prepared != source)
    #expect(try Data(contentsOf: prepared) == Data(contentsOf: source))
    try await MockVideoPreparer.validate(prepared)
    let track = try #require(try await AVURLAsset(url: prepared).loadTracks(withMediaType: .video).first)
    let (size, rate) = try await track.load(.naturalSize, .nominalFrameRate)
    #expect(size.width == 540)
    #expect(size.height == 960)
    #expect(abs(rate - 30) < 0.1)
  }

  @Test func preparedHEVCSurvivesRemovalOfPickerSource() async throws {
    let bundled = try #require(Bundle.main.url(forResource: "plant", withExtension: "mp4"))
    let source = FileManager.default.temporaryDirectory.appendingPathComponent("picker-\(UUID()).mp4")
    try FileManager.default.copyItem(at: bundled, to: source)
    defer { try? FileManager.default.removeItem(at: source) }
    let prepared = try await MockVideoPreparer.prepare(from: source)
    defer { try? FileManager.default.removeItem(at: prepared) }

    try FileManager.default.removeItem(at: source)
    try await MockVideoPreparer.validate(prepared)
    #expect(try Data(contentsOf: prepared) == Data(contentsOf: bundled))
  }

  @Test(arguments: [false, true])
  func converts1080pHEVCToMockFeedWithoutBFrames(_ rotated: Bool) async throws {
    let source = try await makeImportVideo(rotated: rotated)
    defer { try? FileManager.default.removeItem(at: source) }
    let prepared = try await MockVideoPreparer.prepare(from: source)
    defer { try? FileManager.default.removeItem(at: prepared) }

    try await expectConvertedFeed(prepared, size: rotated ? CGSize(width: 540, height: 960) : CGSize(width: 960, height: 540))
    #expect(prepared.pathExtension == "mp4")
    #expect(FileManager.default.fileExists(atPath: source.path))
  }

  @Test func convertsH264ImportsToHEVC() async throws {
    let source = try await makeImportVideo(codec: .h264, size: CGSize(width: 640, height: 360))
    defer { try? FileManager.default.removeItem(at: source) }
    let prepared = try await MockVideoPreparer.prepare(from: source)
    defer { try? FileManager.default.removeItem(at: prepared) }
    try await expectConvertedFeed(prepared, size: CGSize(width: 640, height: 360))
  }

  @Test func conversionCanBeCancelledAfterFramesStart() async throws {
    let source = try await makeImportVideo()
    defer { try? FileManager.default.removeItem(at: source) }
    let (updates, continuation) = AsyncStream<Double>.makeStream(bufferingPolicy: .bufferingNewest(1))
    let task = Task {
      defer { continuation.finish() }
      return try await MockVideoPreparer.prepare(from: source) { continuation.yield($0) }
    }
    var started = false
    for await progress in updates where progress > 0 {
      started = true
      task.cancel()
      break
    }
    #expect(started)
    await #expect(throws: CancellationError.self) { _ = try await task.value }
  }

  @Test func convertsSmallHEVCThatContainsBFrames() async throws {
    // One second of 320×180 red HEVC, encoded with libx265 and bframes=2.
    // A fixed fixture makes this independent of device encoder B-frame choices.
    let encoded = """
      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
      ABgAAeLCL34IFJlKsJM9gAVUB1QKWA2IEbA=
      """
    let data = try #require(Data(base64Encoded: encoded, options: .ignoreUnknownCharacters))
    let source = FileManager.default.temporaryDirectory.appendingPathComponent("iris-bframes-\(UUID()).mp4")
    try data.write(to: source)
    defer { try? FileManager.default.removeItem(at: source) }
    do {
      try await MockVideoPreparer.validate(source)
      Issue.record("The source fixture must require frame reordering")
    } catch MockVideoPreparer.PreparationError.reorderedFrames {}

    let prepared = try await MockVideoPreparer.prepare(from: source)
    defer { try? FileManager.default.removeItem(at: prepared) }
    try await expectConvertedFeed(prepared, size: CGSize(width: 320, height: 180))
  }

  @Test func bundledSampleHasNoReorderedFrames() async throws {
    let url = try #require(Bundle.main.url(forResource: "iris_nobframes_720p", withExtension: "mp4"))
    try await MockVideoPreparer.validate(url)
    let asset = AVURLAsset(url: url)
    let track = try #require(try await asset.loadTracks(withMediaType: .video).first)
    let (size, rate) = try await track.load(.naturalSize, .nominalFrameRate)
    #expect(size == CGSize(width: 720, height: 1280))
    #expect(abs(rate - 24) < 0.1)

    let reader = try AVAssetReader(asset: asset)
    let output = AVAssetReaderTrackOutput(track: track, outputSettings: nil)
    reader.add(output)
    try #require(reader.startReading())
    defer { reader.cancelReading() }
    var previousTime: CMTime?
    var frameCount = 0
    while let sample = output.copyNextSampleBuffer() {
      guard CMSampleBufferGetTotalSampleSize(sample) > 0 else { continue }
      let presentationTime = CMSampleBufferGetPresentationTimeStamp(sample)
      let decodeTime = CMSampleBufferGetDecodeTimeStamp(sample)
      #expect(presentationTime.isNumeric)
      if let previousTime { #expect(CMTimeCompare(presentationTime, previousTime) > 0) }
      if decodeTime.isNumeric { #expect(CMTimeCompare(presentationTime, decodeTime) == 0) }
      previousTime = presentationTime
      frameCount += 1
    }
    #expect(reader.status == .completed)
    #expect(frameCount > 0)
  }

  @Test func unreadableSelectionReturnsAnError() async throws {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("missing-\(UUID()).mp4")
    await #expect(throws: (any Error).self) {
      _ = try await MockVideoPreparer.prepare(from: url)
    }
  }

  @Test func cancelledImportDoesNotReturnAFeed() async throws {
    let source = try #require(Bundle.main.url(forResource: "plant", withExtension: "mp4"))
    let task = Task { try await MockVideoPreparer.prepare(from: source) }
    task.cancel()
    await #expect(throws: CancellationError.self) { _ = try await task.value }
  }
}

/// Real camera-sized sources exercise decoding, rotation, resampling and encoding.
/// Reusing one immutable pixel buffer keeps these fixtures small and quick to make.
private func makeImportVideo(
  codec: AVVideoCodecType = .hevc, size: CGSize = CGSize(width: 1920, height: 1080),
  rotated: Bool = false
) async throws -> URL {
  let url = FileManager.default.temporaryDirectory.appendingPathComponent("iris-source-\(UUID()).mov")
  let writer = try AVAssetWriter(outputURL: url, fileType: .mov)
  let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: codec, AVVideoWidthKey: Int(size.width), AVVideoHeightKey: Int(size.height)
  ])
  input.expectsMediaDataInRealTime = false
  if rotated { input.transform = CGAffineTransform(a: 0, b: 1, c: -1, d: 0, tx: size.height, ty: 0) }
  let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
    kCVPixelBufferWidthKey as String: Int(size.width), kCVPixelBufferHeightKey as String: Int(size.height),
    kCVPixelBufferCGImageCompatibilityKey as String: true,
    kCVPixelBufferCGBitmapContextCompatibilityKey as String: true
  ])
  writer.add(input)
  do {
    try #require(writer.startWriting())
    writer.startSession(atSourceTime: .zero)
    let pool = try #require(adaptor.pixelBufferPool)
    var createdBuffer: CVPixelBuffer?
    try #require(CVPixelBufferPoolCreatePixelBuffer(kCFAllocatorDefault, pool, &createdBuffer) == kCVReturnSuccess)
    let buffer = try #require(createdBuffer)
    CVPixelBufferLockBaseAddress(buffer, [])
    let context = CGContext(data: CVPixelBufferGetBaseAddress(buffer), width: Int(size.width), height: Int(size.height),
      bitsPerComponent: 8, bytesPerRow: CVPixelBufferGetBytesPerRow(buffer), space: CGColorSpaceCreateDeviceRGB(),
      bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)
    context?.setFillColor(CGColor(red: 0.8, green: 0.1, blue: 0.2, alpha: 1))
    context?.fill(CGRect(origin: .zero, size: size))
    CVPixelBufferUnlockBaseAddress(buffer, [])
    try #require(context != nil)
    for frame in 0..<60 {
      while !input.isReadyForMoreMediaData {
        try #require(writer.status == .writing)
        try await Task.sleep(for: .milliseconds(5))
      }
      try #require(adaptor.append(buffer, withPresentationTime: CMTime(value: Int64(frame), timescale: 60)))
    }
    input.markAsFinished()
    await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
      writer.finishWriting { continuation.resume() }
    }
    try #require(writer.status == .completed)
    return url
  } catch {
    if writer.status == .writing { writer.cancelWriting() }
    try? FileManager.default.removeItem(at: url)
    throw error
  }
}

private func expectConvertedFeed(_ url: URL, size expectedSize: CGSize) async throws {
  try await MockVideoPreparer.validate(url)
  let asset = AVURLAsset(url: url)
  let track = try #require(try await asset.loadTracks(withMediaType: .video).first)
  let (size, rate, transform, formats) = try await track.load(.naturalSize, .nominalFrameRate, .preferredTransform, .formatDescriptions)
  #expect(size == expectedSize)
  #expect(abs(rate - 24) < 0.1)
  #expect(transform == .identity, "Orientation must be baked into the converted pixels")
  #expect(formats.allSatisfy { CMFormatDescriptionGetMediaSubType($0) == kCMVideoCodecType_HEVC })
  let duration = try await asset.load(.duration)
  #expect(abs(duration.seconds - 1) < 0.05)

  let reader = try AVAssetReader(asset: asset)
  let output = AVAssetReaderTrackOutput(track: track, outputSettings: nil)
  reader.add(output)
  try #require(reader.startReading())
  defer { reader.cancelReading() }
  var count = 0
  while let sample = output.copyNextSampleBuffer() {
    guard CMSampleBufferGetTotalSampleSize(sample) > 0 else { continue }
    let pts = CMSampleBufferGetPresentationTimeStamp(sample)
    let dts = CMSampleBufferGetDecodeTimeStamp(sample)
    if dts.isNumeric { #expect(CMTimeCompare(pts, dts) == 0, "Converted frames must not need reordering") }
    count += 1
  }
  #expect(reader.status == .completed)
  #expect(count == 24)
}

#endif
