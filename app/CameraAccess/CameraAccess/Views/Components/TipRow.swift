/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

//
// TipRow.swift
//
// Reusable icon + text row used by the onboarding tips on the home screen and
// the getting-started sheet. Pass a `title` for the two-line (title + body)
// variant; omit it for the single-line body-only variant.
//

import SwiftUI

struct TipRow: View {
  let resource: ImageResource
  var title: String?
  let text: String

  var body: some View {
    HStack(alignment: .top, spacing: 12) {
      Image(resource)
        .resizable()
        .renderingMode(.template)
        .foregroundStyle(Color.black)
        .aspectRatio(contentMode: .fit)
        .frame(width: 24)
        .padding(.leading, 4)
        .padding(.top, 4)

      if let title {
        VStack(alignment: .leading, spacing: 6) {
          Text(title)
            .font(.system(size: 18, weight: .semibold))
            .foregroundStyle(Color.black)
          Text(text)
            .font(.system(size: 15))
            .foregroundStyle(Color.gray)
            .fixedSize(horizontal: false, vertical: true)
        }
      } else {
        Text(text)
          .font(.system(size: 15))
          .foregroundStyle(Color.black)
          .fixedSize(horizontal: false, vertical: true)
      }

      Spacer(minLength: 0)
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }
}
