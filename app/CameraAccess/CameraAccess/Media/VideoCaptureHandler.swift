/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

import AVFoundation
import CoreMedia
import os

// MARK: - VideoCaptureHandler

/// Writes the video track for a recording: appends compressed HEVC frames in
/// passthrough mode with correct sync-sample flags and timestamps so the MOV is
/// Photos-compatible. Thread-safe via `OSAllocatedUnfairLock`.
final class VideoCaptureHandler: Sendable {
  /// Fallback per-frame duration (frames/sec) when the SDK sample carries none.
  private static let fallbackFrameRate: Int32 = 24
  /// A track is "streaming" if it wrote within this many seconds.
  private static let streamingStalenessSeconds = 2.0

  private static let logger = Logger(subsystem: "com.meta.wearables.CameraAccess", category: "VideoCapture")

  private struct State {
    var videoInput: AVAssetWriterInput
    // Weak: VideoRecorder owns the writer for the recording's life.
    weak var assetWriter: AVAssetWriter?
    var streamStartTimestamp: CMTime?
    // DTS of the last appended sample; keeps the output timeline strictly increasing.
    var lastOutputDTS: CMTime?
    var lastVideoWriteTime: Date?
    var isCapturing: Bool = false
  }

  private let state: OSAllocatedUnfairLock<State>

  var isVideoStreaming: Bool {
    let lastWrite = state.withLockUnchecked { $0.lastVideoWriteTime }
    guard let lastWrite else { return false }
    return Date().timeIntervalSince(lastWrite) < Self.streamingStalenessSeconds
  }

  init(writer: AVAssetWriter, width: Int, height: Int, sourceFormatHint: CMFormatDescription?) {
    let videoInput = AVAssetWriterInput(mediaType: .video, outputSettings: nil, sourceFormatHint: sourceFormatHint)
    videoInput.expectsMediaDataInRealTime = true

    self.state = OSAllocatedUnfairLock(
      uncheckedState: State(
        videoInput: videoInput,
        assetWriter: writer
      ))

    if writer.canAdd(videoInput) {
      writer.add(videoInput)
    } else {
      Self.logger.error("Couldn't add video input to asset writer")
    }
  }

  func start() {
    state.withLockUnchecked { state in
      state.isCapturing = true
    }
  }

  func stop() {
    state.withLockUnchecked { state in
      state.isCapturing = false
      state.videoInput.markAsFinished()
    }
  }

  func appendVideoFrame(_ sampleBuffer: CMSampleBuffer) {
    guard let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer),
      CMFormatDescriptionGetMediaType(formatDescription) == kCMMediaType_Video
    else {
      return
    }
    writeCompressedFrame(sampleBuffer)
  }

  // MARK: - Video Frame Writing

  /// Rebases the frame onto a strictly monotonic timeline and appends it. Readiness
  /// check, timeline update, and append run under the same lock as markAsFinished(),
  /// so no frame appends after the input is finished (which would fail the writer).
  private func writeCompressedFrame(_ sampleBuffer: CMSampleBuffer) {
    let sourcePTS = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
    let sourceDTS = CMSampleBufferGetDecodeTimeStamp(sampleBuffer)

    // SDK frames may carry no per-sample duration; without one the stts atom is
    // zero-length and the timeline isn't seekable. Fall back to the frame rate.
    let originalDuration = CMSampleBufferGetDuration(sampleBuffer)
    let frameDuration =
      (originalDuration.isValid && originalDuration > .zero)
      ? originalDuration
      : CMTimeMake(value: 1, timescale: Self.fallbackFrameRate)

    // Set sync-sample flags so AVAssetWriter builds the stss (keyframe index). SDK
    // buffers may omit them, leaving a MOV that AVPlayer plays but Photos rejects.
    let isSync = sampleBuffer.isHEVCKeyframe()

    state.withLockUnchecked { state in
      guard state.isCapturing, state.assetWriter?.status != .failed, state.videoInput.isReadyForMoreMediaData else {
        return
      }

      let timelineStart = state.streamStartTimestamp ?? sourcePTS
      if state.streamStartTimestamp == nil {
        state.streamStartTimestamp = sourcePTS
      }

      // Rebase onto the recording origin, then clamp monotonic. A looping source
      // (e.g. the mock feed restarting plant.mp4) sends timestamps backwards, and a
      // backwards DTS/PTS fails the writer — the clamp keeps every sample increasing.
      var dts = sourceDTS.isValid ? CMTimeSubtract(sourceDTS, timelineStart) : CMTimeSubtract(sourcePTS, timelineStart)
      if let lastDTS = state.lastOutputDTS {
        let floor = CMTimeAdd(lastDTS, frameDuration)
        if dts < floor { dts = floor }
      }
      var pts = CMTimeSubtract(sourcePTS, timelineStart)
      if pts < dts { pts = dts }

      var timingInfo = CMSampleTimingInfo(duration: frameDuration, presentationTimeStamp: pts, decodeTimeStamp: dts)
      var adjustedSampleBuffer: CMSampleBuffer?
      guard
        CMSampleBufferCreateCopyWithNewTiming(
          allocator: kCFAllocatorDefault,
          sampleBuffer: sampleBuffer,
          sampleTimingEntryCount: 1,
          sampleTimingArray: &timingInfo,
          sampleBufferOut: &adjustedSampleBuffer
        ) == noErr, let adjustedSampleBuffer
      else {
        return
      }

      if let attachments = CMSampleBufferGetSampleAttachmentsArray(adjustedSampleBuffer, createIfNecessary: true) as? [NSMutableDictionary],
        let dict = attachments.first
      {
        dict[kCMSampleAttachmentKey_NotSync] = !isSync
        dict[kCMSampleAttachmentKey_DependsOnOthers] = !isSync
      }

      if state.videoInput.append(adjustedSampleBuffer) {
        state.lastOutputDTS = dts
        state.lastVideoWriteTime = Date()
      }
    }
  }

}

// MARK: - HEVC Keyframe Detection

extension CMSampleBuffer {
  /// Returns true if any NAL unit in the buffer is an HEVC keyframe type.
  /// Keyframe NAL types: BLA (16-18), IDR (19-20), CRA (21).
  func isHEVCKeyframe() -> Bool {
    guard let dataBuffer = CMSampleBufferGetDataBuffer(self) else { return false }
    let totalLength = CMBlockBufferGetDataLength(dataBuffer)
    var offset = 0

    // NAL units are in HVCC format: 4-byte big-endian length prefix followed by NAL data.
    while offset + 4 < totalLength {
      var nalLengthBE: UInt32 = 0
      guard CMBlockBufferCopyDataBytes(dataBuffer, atOffset: offset, dataLength: 4, destination: &nalLengthBE) == kCMBlockBufferNoErr
      else { break }
      let nalLength = Int(UInt32(bigEndian: nalLengthBE))
      offset += 4

      guard nalLength > 0, offset + nalLength <= totalLength else { break }

      // HEVC NAL header byte 0: forbidden_zero_bit(1) | nal_unit_type(6) | nuh_layer_id MSB(1)
      var nalHeader: UInt8 = 0
      guard CMBlockBufferCopyDataBytes(dataBuffer, atOffset: offset, dataLength: 1, destination: &nalHeader) == kCMBlockBufferNoErr
      else { break }
      let nalUnitType = (nalHeader >> 1) & 0x3F

      if nalUnitType >= 16 && nalUnitType <= 21 {
        return true
      }

      offset += nalLength
    }

    return false
  }
}
