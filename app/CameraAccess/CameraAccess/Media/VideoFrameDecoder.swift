/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

import AVFoundation
import CoreMedia
import UIKit
import VideoToolbox
import os

// MARK: - VideoFrameDecoder

/// Decompresses HEVC `CMSampleBuffer`s into `UIImage`s for on-screen preview. Not
/// required for recording (passthrough writes compressed frames directly) — included
/// to show how to display hvc1 frames. On decode failure (e.g. session invalidation
/// while backgrounded) it returns the last good frame until a keyframe arrives, so no
/// corrupt frames reach the UI. Thread-safe — call `decode(_:)` from any thread.
final class VideoFrameDecoder: Sendable {
  private struct State {
    var session: VTDecompressionSession?
    var formatDescription: CMFormatDescription?
    var consecutiveFailures: Int = 0
    var lastGoodImage: UIImage?
    var awaitingKeyframe: Bool = false
  }

  private let state: OSAllocatedUnfairLock<State>
  private let ciContext: CIContext

  init() {
    self.state = OSAllocatedUnfairLock(uncheckedState: State())
    self.ciContext = CIContext()
  }

  /// Decodes an HEVC sample buffer into a `UIImage`. Creates the decompression session
  /// lazily and recreates it if the format changes or it goes invalid. On failure,
  /// returns the last good frame until a keyframe arrives.
  func decode(_ sampleBuffer: CMSampleBuffer) -> UIImage? {
    guard CMSampleBufferGetDataBuffer(sampleBuffer) != nil,
      let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer)
    else {
      return state.withLockUnchecked { $0.lastGoodImage }
    }

    let isKeyframe = sampleBuffer.isHEVCKeyframe()

    let (session, awaitingKeyframe) = state.withLockUnchecked { state -> (VTDecompressionSession?, Bool) in
      // Recreate if format description changed (e.g. resolution switch mid-stream)
      if let currentFormat = state.formatDescription,
        let existingSession = state.session,
        !CMFormatDescriptionEqual(currentFormat, otherFormatDescription: formatDescription)
      {
        VTDecompressionSessionInvalidate(existingSession)
        state.session = nil
        state.formatDescription = nil
        state.consecutiveFailures = 0
        state.awaitingKeyframe = true
      }

      if let session = state.session {
        // Catch an invalidated session before a decode attempt fails.
        if VTDecompressionSessionCanAcceptFormatDescription(session, formatDescription: formatDescription) {
          return (session, state.awaitingKeyframe)
        }
        VTDecompressionSessionInvalidate(session)
        state.session = nil
        state.formatDescription = nil
        state.consecutiveFailures = 0
        state.awaitingKeyframe = true
      }

      // Force software decoding so the session survives backgrounding. iOS tears down
      // hardware sessions when backgrounded, and a fresh one stalls until the next
      // keyframe — visible stutter.
      var decoderSpec: CFDictionary?
      if #available(iOS 17.0, *) {
        decoderSpec =
          [
            kVTVideoDecoderSpecification_EnableHardwareAcceleratedVideoDecoder as String: false
          ] as CFDictionary
      }

      let outputAttrs: [CFString: Any] = [
        kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_32BGRA
      ]

      var newSession: VTDecompressionSession?
      VTDecompressionSessionCreate(
        allocator: kCFAllocatorDefault,
        formatDescription: formatDescription,
        decoderSpecification: decoderSpec,
        imageBufferAttributes: outputAttrs as CFDictionary,
        outputCallback: nil,
        decompressionSessionOut: &newSession)
      state.session = newSession
      state.formatDescription = formatDescription
      state.consecutiveFailures = 0
      state.awaitingKeyframe = true
      return (newSession, true)
    }

    // Awaiting a keyframe after (re)creating the session: hold the last good frame.
    if awaitingKeyframe && !isKeyframe {
      return state.withLockUnchecked { $0.lastGoodImage }
    }

    guard let session else {
      return state.withLockUnchecked { $0.lastGoodImage }
    }

    // The handler runs synchronously on this thread because flags is empty, so capturing
    // this local by reference is safe despite the @Sendable warning.
    // WARNING: do not add ._EnableAsynchronousDecompression (or any async flag) — the
    // handler would then run off-thread and this capture would be a data race.
    nonisolated(unsafe) var outputPixelBuffer: CVPixelBuffer?
    let decodeStatus = VTDecompressionSessionDecodeFrame(
      session,
      sampleBuffer: sampleBuffer,
      flags: [],
      infoFlagsOut: nil
    ) { status, _, imageBuffer, _, _ in
      if status == noErr, let imageBuffer {
        outputPixelBuffer = imageBuffer
      }
    }

    // After 3 consecutive failures, invalidate so the next frame builds a fresh session
    // — tolerates transient errors while still recovering from a persistent one
    // (e.g. a pre-iOS 17 session torn down after backgrounding).
    if decodeStatus != noErr {
      return state.withLockUnchecked { state in
        state.consecutiveFailures += 1
        if state.consecutiveFailures >= 3 {
          if let session = state.session {
            VTDecompressionSessionInvalidate(session)
          }
          state.session = nil
          state.formatDescription = nil
          state.consecutiveFailures = 0
          state.awaitingKeyframe = true
        }
        return state.lastGoodImage
      }
    }

    state.withLockUnchecked {
      $0.consecutiveFailures = 0
      if isKeyframe {
        $0.awaitingKeyframe = false
      }
    }

    guard let pixelBuffer = outputPixelBuffer else {
      return state.withLockUnchecked { $0.lastGoodImage }
    }

    let cgImage = ciContext.createCGImage(
      CIImage(cvPixelBuffer: pixelBuffer),
      from: CGRect(
        x: 0, y: 0,
        width: CVPixelBufferGetWidth(pixelBuffer),
        height: CVPixelBufferGetHeight(pixelBuffer)))
    guard let cgImage else {
      return state.withLockUnchecked { $0.lastGoodImage }
    }

    let image = UIImage(cgImage: cgImage)
    state.withLockUnchecked { $0.lastGoodImage = image }
    return image
  }
}
