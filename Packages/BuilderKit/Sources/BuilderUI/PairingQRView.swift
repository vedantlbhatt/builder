import SwiftUI

#if canImport(AppKit)
    import AppKit
#endif
#if canImport(CoreImage)
    import CoreImage
#endif

/// The Mac half of "Log in with Claude Code": a QR the phone scans, and the same code in
/// type large enough to read across a desk.
///
/// The QR encodes `builder://pair?code=XXXX-XXXX`; the phone's scanner accepts either a
/// bare code or a URL carrying `?code=` (see `mobile/src/pairing/parse.ts`), so the two
/// presentations here are interchangeable and the code below the image is not merely a
/// caption — it is the fallback when the camera is not to hand.
///
/// Rendering: CoreImage's generator emits one pixel per module. It is scaled by an INTEGER
/// factor and drawn with `.interpolation(.none)`, because a fractional scale or bilinear
/// filtering blurs module edges, and a blurred edge is exactly what a phone camera in a
/// dim room fails on. If the generator returns nil the code is shown on its own.
public struct PairingQRView: View {

    public let userCode: String
    public let payload: String
    let dark: Bool
    let side: CGFloat

    @State private var copied = false

    public init(userCode: String, payload: String, dark: Bool = true, side: CGFloat = 220) {
        self.userCode = userCode
        self.payload = payload
        self.dark = dark
        self.side = side
    }

    public var body: some View {
        VStack(spacing: 14) {
            #if canImport(AppKit)
                if let image = Self.qrImage(for: payload, targetSide: side) {
                    Image(nsImage: image)
                        .interpolation(.none)
                        .resizable()
                        .frame(width: image.size.width, height: image.size.height)
                        .padding(10)
                        .background(RoundedRectangle(cornerRadius: 8).fill(Color.white))
                        .accessibilityLabel("Pairing QR code")
                }
            #endif

            Text(userCode)
                .font(.system(size: 28, weight: .semibold, design: .monospaced))
                .kerning(2)
                .foregroundStyle(StripPalette.text(dark: dark))
                .textSelection(.enabled)

            Text("Open Builder on your phone → Settings → Scan code")
                .font(.system(size: 12))
                .foregroundStyle(StripPalette.textDim(dark: dark))
                .multilineTextAlignment(.center)

            Button {
                copyCode()
            } label: {
                Text(copied ? "Copied" : "Copy code")
                    .font(.system(size: 11, weight: .medium))
            }
            .buttonStyle(.plain)
            .foregroundStyle(StripPalette.accent(dark: dark))
        }
    }

    private func copyCode() {
        #if canImport(AppKit)
            let pb = NSPasteboard.general
            pb.clearContents()
            pb.setString(userCode, forType: .string)
        #endif
        copied = true
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            copied = false
        }
    }

    #if canImport(AppKit)
        /// One module per pixel from the generator, scaled by the largest integer factor
        /// that fits `targetSide`, so the result is a little under the target rather than
        /// fractionally over it with uneven modules.
        static func qrImage(for text: String, targetSide: CGFloat) -> NSImage? {
            guard let filter = CIFilter(name: "CIQRCodeGenerator") else { return nil }
            filter.setValue(Data(text.utf8), forKey: "inputMessage")
            filter.setValue("M", forKey: "inputCorrectionLevel")
            guard let output = filter.outputImage else { return nil }

            let extent = output.extent.integral
            guard extent.width > 0, extent.height > 0 else { return nil }

            let scale = max(1, (targetSide / extent.width).rounded(.down))
            let scaled = output.transformed(by: CGAffineTransform(scaleX: scale, y: scale))

            let rep = NSCIImageRep(ciImage: scaled)
            let image = NSImage(size: rep.size)
            image.addRepresentation(rep)
            return image
        }
    #endif
}
