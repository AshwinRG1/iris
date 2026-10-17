/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// MockDeviceKitView.swift
//
// Debug-only interface for managing mock Meta wearable devices during development.
// This view allows developers to create, configure, and test with simulated devices
// without requiring physical Meta hardware.
//

#if DEBUG

import Foundation
import SwiftUI

struct MockDeviceKitView: View {
  @Bindable var viewModel: MockDeviceKitViewModel

  var body: some View {
    NavigationView {
      ScrollView {
        VStack(spacing: 12) {
          CardView {
            VStack(spacing: 6) {
              HStack {
                Text("MockDeviceKit")
                  .font(.headline)
                  .fontWeight(.bold)
                  .foregroundStyle(.primary)
                Spacer()

                if viewModel.isEnabled {
                  Text("\(viewModel.cardViewModels.count) device(s) paired")
                    .font(.subheadline)
                    .foregroundStyle(.green)
                }
              }

              Text("This screen simulates devices and mocks their capabilities and states.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)

              Divider()

              if viewModel.isEnabled {
                MockDeviceKitButton("Disable MockDeviceKit", style: .destructive) {
                  viewModel.disable()
                }

                MockDeviceKitButton("Pair Ray-Ban Meta", disabled: viewModel.cardViewModels.count >= 3) {
                  viewModel.pairGlasses()
                }
              } else {
                MockDeviceKitButton("Enable MockDeviceKit") {
                  viewModel.enable()
                }
              }
            }
            .padding(12)
          }

          if viewModel.isEnabled {
            ForEach(viewModel.cardViewModels, id: \.id) { cardViewModel in
              MockDeviceCardView(
                viewModel: cardViewModel,
                onUnpairDevice: {
                  viewModel.unpairDevice(cardViewModel.device)
                }
              )
            }
          }

          Spacer()
        }
        .padding()
      }
      .background(Color(.systemGroupedBackground))
      .alert("Error", isPresented: $viewModel.showError) {
        Button("OK") {
          viewModel.dismissError()
        }
      } message: {
        Text(viewModel.errorMessage)
      }
    }
  }
}

#endif
