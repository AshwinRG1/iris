/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// MockDeviceKitViewModel.swift
//
// View model for managing mock devices during development and testing of DAT SDK features.
// Mock devices simulate real Meta wearable device behavior, allowing developers to test
// streaming, photo capture, and device management workflows without physical hardware.
//

#if DEBUG

import Foundation
import MWDATMockDevice
import Observation

@Observable
@MainActor
final class MockDeviceKitViewModel {
  private let mockDeviceKit: MockDeviceKitInterface
  var cardViewModels: [MockDeviceCardViewModel] = []
  var isEnabled: Bool
  var showError: Bool = false
  var errorMessage: String = ""

  init(mockDeviceKit: MockDeviceKitInterface) {
    self.mockDeviceKit = mockDeviceKit
    self.isEnabled = mockDeviceKit.isEnabled
    self.cardViewModels = mockDeviceKit.pairedDevices.compactMap { $0 as? MockGlasses }.map { MockDeviceCardViewModel(device: $0) }
  }

  func enable() {
    mockDeviceKit.enable()
    isEnabled = true
  }

  func disable() {
    mockDeviceKit.disable()
    cardViewModels = []
    isEnabled = false
  }

  // Add a new mock Ray-Ban Meta device
  func pairGlasses() {
    let mockDevice: MockGlasses
    do {
      mockDevice = try mockDeviceKit.pairGlasses(model: .rayBanMeta)
    } catch {
      showError(error.localizedDescription)
      return
    }
    cardViewModels.append(MockDeviceCardViewModel(device: mockDevice))
  }

  func unpairDevice(_ device: MockDevice) {
    if let idx = cardViewModels.firstIndex(where: { $0.id == device.deviceIdentifier }) {
      cardViewModels.remove(at: idx)
      mockDeviceKit.unpairDevice(device)
    }
  }

  func showError(_ message: String) {
    errorMessage = message
    showError = true
  }

  func dismissError() {
    showError = false
  }
}

#endif
