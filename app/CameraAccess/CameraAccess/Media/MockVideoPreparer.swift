#if DEBUG

import AVFoundation
import CoreMedia
import CoreVideo
import Foundation
import VideoToolbox

/// The mock SDK fragments each compressed frame into 896-byte packets with
/// UInt8 counters. Large HEVC keyframes can trap inside that closed-source code.
/// Convert camera originals to a bounded HEVC feed without introducing B-frames.
enum MockVideoPreparer {
  // Leave room below the SDK's 256-packet limit for parameter sets and headers.
  static let maximumFrameBytes = 200_000

  enum PreparationError: LocalizedError {
    case noVideo, invalidVideo, exportFailed, frameTooLarge, reorderedFrames

    var errorDescription: String? {
      switch self {
      case .noVideo: return "The selected file has no video track."
      case .invalidVideo: return "The selected video couldn't be read. Choose another video."
      case .exportFailed: return "Couldn't prepare this video for the mock camera. Try another clip."
      case .frameTooLarge: return "This video's frames are too large for the mock camera. Try a lower-detail clip."
      case .reorderedFrames: return "This video requires frame reordering and couldn't be prepared for smooth mock playback."
      }
    }
  }

  /// Conversion and compressed-sample scanning stay off the UI actor. Progress
  /// callbacks run on the worker; callers must hop to MainActor for UI updates.
  /// The returned file is independent of the picker's temporary source URL.
  static func prepare(
    from source: URL, onProgress: @escaping @Sendable (Double) -> Void = { _ in }
  ) async throws -> URL {
    let work = Task.detached(priority: .userInitiated) {
      try Task.checkCancellation()
      onProgress(0)
      let asset = AVURLAsset(url: source)
      guard let track = try await asset.loadTracks(withMediaType: .video).first else {
        throw PreparationError.noVideo
      }
      let duration = try await asset.load(.duration)
      guard duration.seconds.isFinite, duration.seconds > 0 else { throw PreparationError.invalidVideo }

      let (size, transform, frameRate) = try await track.load(.naturalSize, .preferredTransform, .nominalFrameRate)
      let displaySize = CGRect(origin: .zero, size: size).applying(transform).standardized.size
      let formats = try await track.load(.formatDescriptions)
      if !formats.isEmpty,
        formats.allSatisfy({ CMFormatDescriptionGetMediaSubType($0) == kCMVideoCodecType_HEVC }),
        max(size.width, size.height, displaySize.width, displaySize.height) <= 960,
        min(size.width, size.height) <= 540, min(displaySize.width, displaySize.height) <= 540,
        frameRate.isFinite, frameRate > 0, frameRate <= 30
      {
        // Keep suitable HEVC streams unchanged. Oversized frames or B-frames
        // need the same controlled conversion as a 1080p camera original.
        do {
          try await validate(source)
          try Task.checkCancellation()
          let fileExtension = source.pathExtension.isEmpty ? "mp4" : source.pathExtension
          let output = FileManager.default.temporaryDirectory
            .appendingPathComponent("iris-mock-\(UUID().uuidString).\(fileExtension)")
          do {
            try FileManager.default.copyItem(at: source, to: output)
            try Task.checkCancellation()
            onProgress(1)
            return output
          } catch {
            try? FileManager.default.removeItem(at: output)
            throw error
          }
        } catch PreparationError.frameTooLarge {
          // Re-encode with smaller keyframes below.
        } catch PreparationError.reorderedFrames {
          // Re-encode with frame reordering explicitly disabled below.
        }
      }

      // A second, smaller conversion handles unusually detailed keyframes.
      for maximumLongSide in [960.0, 640.0] {
        try Task.checkCancellation()
        let output = FileManager.default.temporaryDirectory
          .appendingPathComponent("iris-mock-\(UUID().uuidString).mp4")
        do {
          let converter = VideoConverter()
          try await withTaskCancellationHandler {
            try await converter.run(from: source, maximumLongSide: maximumLongSide, to: output,
                                    onProgress: onProgress)
          } onCancel: {
            Task { await converter.cancel() }
          }
          onProgress(0.95)
          try await validate(output)
          try Task.checkCancellation()
          onProgress(1)
          return output
        } catch {
          try? FileManager.default.removeItem(at: output)
          if case PreparationError.frameTooLarge = error, maximumLongSide == 960 { continue }
          throw error
        }
      }
      throw PreparationError.frameTooLarge
    }
    return try await withTaskCancellationHandler {
      try await work.value
    } onCancel: {
      work.cancel()
    }
  }

  static func outputSize(for size: CGSize, maximumLongSide: CGFloat) throws -> CGSize {
    guard size.width.isFinite, size.height.isFinite, size.width > 0, size.height > 0 else {
      throw PreparationError.invalidVideo
    }
    let scale = min(1, maximumLongSide / max(size.width, size.height),
                    maximumLongSide * 9 / 16 / min(size.width, size.height))
    // HEVC 4:2:0 needs even dimensions. Never enlarge a small source.
    return CGSize(width: max(2, floor(size.width * scale / 2) * 2),
                  height: max(2, floor(size.height * scale / 2) * 2))
  }

  /// Read compressed samples rather than decoding/loading the whole movie into RAM.
  static func validate(_ url: URL) async throws {
    let asset = AVURLAsset(url: url)
    guard let track = try await asset.loadTracks(withMediaType: .video).first else {
      throw PreparationError.noVideo
    }
    let reader = try AVAssetReader(asset: asset)
    let output = AVAssetReaderTrackOutput(track: track, outputSettings: nil)
    output.alwaysCopiesSampleData = false
    guard reader.canAdd(output) else { throw PreparationError.invalidVideo }
    reader.add(output)
    guard reader.startReading() else { throw PreparationError.invalidVideo }
    defer { reader.cancelReading() }

    var frameCount = 0
    var previousTime: CMTime?
    while let sample = output.copyNextSampleBuffer() {
      try Task.checkCancellation()
      // Asset readers can emit empty timing/edit samples around real frames.
      let bytes = CMSampleBufferGetTotalSampleSize(sample)
      guard bytes > 0 else { continue }
      guard let format = CMSampleBufferGetFormatDescription(sample),
        CMFormatDescriptionGetMediaSubType(format) == kCMVideoCodecType_HEVC
      else { throw PreparationError.invalidVideo }
      guard bytes <= maximumFrameBytes else {
        throw PreparationError.frameTooLarge
      }
      let time = CMSampleBufferGetPresentationTimeStamp(sample)
      guard time.isNumeric else { throw PreparationError.invalidVideo }
      if let previousTime, CMTimeCompare(time, previousTime) <= 0 {
        throw PreparationError.reorderedFrames
      }
      previousTime = time
      frameCount += 1
    }
    guard reader.status == .completed, frameCount > 0 else { throw PreparationError.invalidVideo }
  }

  /// A separate actor owns the reader/writer so decoding, orientation, scaling
  /// and encoding never use MainActor. Backpressure bounds memory to a few frames.
  private actor VideoConverter {
    private var reader: AVAssetReader?
    private var writer: AVAssetWriter?
    private var isCancelled = false

    func cancel() {
      isCancelled = true
      reader?.cancelReading()
      if writer?.status == .writing { writer?.cancelWriting() }
    }

    func run(
      from source: URL, maximumLongSide: CGFloat, to destination: URL,
      onProgress: @escaping @Sendable (Double) -> Void
    ) async throws {
      try Task.checkCancellation()
      guard !isCancelled else { throw CancellationError() }
      let asset = AVURLAsset(url: source)
      guard let track = try await asset.loadTracks(withMediaType: .video).first else {
        throw PreparationError.noVideo
      }
      let (size, transform) = try await track.load(.naturalSize, .preferredTransform)
      let duration = try await asset.load(.duration)
      let bounds = CGRect(origin: .zero, size: size).applying(transform).standardized
      let target = try outputSize(for: bounds.size, maximumLongSide: maximumLongSide)
      let layer = AVMutableVideoCompositionLayerInstruction(assetTrack: track)
      layer.setTransform(
        transform.concatenating(CGAffineTransform(translationX: -bounds.minX, y: -bounds.minY))
          .concatenating(CGAffineTransform(scaleX: target.width / bounds.width,
                                          y: target.height / bounds.height)), at: .zero)
      let instruction = AVMutableVideoCompositionInstruction()
      instruction.timeRange = CMTimeRange(start: .zero, duration: duration)
      instruction.layerInstructions = [layer]
      let composition = AVMutableVideoComposition()
      composition.instructions = [instruction]
      composition.renderSize = target
      composition.frameDuration = CMTime(value: 1, timescale: 24)
      // SDR, 8-bit HEVC Main also handles HDR phone originals consistently.
      composition.colorPrimaries = AVVideoColorPrimaries_ITU_R_709_2
      composition.colorTransferFunction = AVVideoTransferFunction_ITU_R_709_2
      composition.colorYCbCrMatrix = AVVideoYCbCrMatrix_ITU_R_709_2

      let reader = try AVAssetReader(asset: asset)
      let output = AVAssetReaderVideoCompositionOutput(videoTracks: [track], videoSettings: [
        kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
      ])
      output.videoComposition = composition
      output.alwaysCopiesSampleData = false
      guard reader.canAdd(output) else { throw PreparationError.invalidVideo }
      reader.add(output)

      let writer = try AVAssetWriter(outputURL: destination, fileType: .mp4)
      let settings: [String: Any] = [
        AVVideoCodecKey: AVVideoCodecType.hevc,
        AVVideoWidthKey: Int(target.width), AVVideoHeightKey: Int(target.height),
        AVVideoColorPropertiesKey: [
          AVVideoColorPrimariesKey: AVVideoColorPrimaries_ITU_R_709_2,
          AVVideoTransferFunctionKey: AVVideoTransferFunction_ITU_R_709_2,
          AVVideoYCbCrMatrixKey: AVVideoYCbCrMatrix_ITU_R_709_2
        ],
        AVVideoCompressionPropertiesKey: [
          AVVideoProfileLevelKey: kVTProfileLevel_HEVC_Main_AutoLevel,
          AVVideoAllowFrameReorderingKey: false,
          AVVideoAverageBitRateKey: maximumLongSide == 960 ? 1_200_000 : 600_000,
          AVVideoExpectedSourceFrameRateKey: 24,
          AVVideoMaxKeyFrameIntervalKey: 24
        ]
      ]
      guard writer.canApply(outputSettings: settings, forMediaType: .video) else {
        throw PreparationError.exportFailed
      }
      let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
      input.expectsMediaDataInRealTime = false
      guard writer.canAdd(input) else { throw PreparationError.exportFailed }
      writer.add(input)
      self.reader = reader
      self.writer = writer
      defer {
        self.reader = nil
        self.writer = nil
      }

      do {
        try Task.checkCancellation()
        guard !isCancelled else { throw CancellationError() }
        guard writer.startWriting(), reader.startReading() else { throw PreparationError.exportFailed }
        writer.startSession(atSourceTime: .zero)
        var lastProgress = 0.0
        while true {
          try Task.checkCancellation()
          guard !isCancelled else { throw CancellationError() }
          while !input.isReadyForMoreMediaData {
            guard writer.status == .writing else { throw PreparationError.exportFailed }
            try await Task.sleep(for: .milliseconds(5))
          }
          let appended = try autoreleasepool {
            guard let sample = output.copyNextSampleBuffer() else { return false }
            guard input.append(sample) else { throw PreparationError.exportFailed }
            let seconds = CMSampleBufferGetPresentationTimeStamp(sample).seconds
            let progress = min(0.9, max(0, seconds / duration.seconds * 0.9))
            if progress >= lastProgress + 0.01 {
              lastProgress = progress
              onProgress(progress)
            }
            return true
          }
          if !appended { break }
        }
        guard reader.status == .completed else { throw PreparationError.invalidVideo }
        input.markAsFinished()
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
          writer.finishWriting { continuation.resume() }
        }
        try Task.checkCancellation()
        guard !isCancelled else { throw CancellationError() }
        guard writer.status == .completed else { throw PreparationError.exportFailed }
      } catch {
        reader.cancelReading()
        if writer.status == .writing { writer.cancelWriting() }
        throw error
      }
    }
  }
}

#endif
