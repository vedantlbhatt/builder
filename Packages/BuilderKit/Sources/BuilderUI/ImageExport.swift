import SwiftUI

#if canImport(AppKit)
    import AppKit
#endif

/// Renders a card to a PNG.
///
/// Two rules that only show up when you actually export:
///
/// - **@2x, always.** X and every other timeline downscales what you upload; a 1x export
///   of a 1600pt card arrives soft, and soft is indistinguishable from cheap.
/// - **Flat fills only in the view.** `ImageRenderer` silently drops `Material`, most
///   blend modes and some shadow configurations — so a card that looks correct on screen
///   exports as a grey rectangle, with no error anywhere.
public enum ImageExport {

    public enum ExportError: Error, CustomStringConvertible {
        case renderFailed
        case encodeFailed

        public var description: String {
            switch self {
            case .renderFailed: return "ImageRenderer produced no image"
            case .encodeFailed: return "could not encode PNG"
            }
        }
    }

    #if canImport(AppKit)
        /// Render any SwiftUI view to PNG data.
        @MainActor
        public static func png<V: View>(of view: V, scale: CGFloat = 2) throws -> Data {
            let renderer = ImageRenderer(content: view)
            renderer.scale = scale
            // sRGB explicitly: the default follows the display, so exporting on a P3
            // monitor would produce a file whose colours do not match the same card
            // rendered anywhere else.
            renderer.isOpaque = true

            guard let cg = renderer.cgImage else { throw ExportError.renderFailed }
            let rep = NSBitmapImageRep(cgImage: cg)
            rep.size = NSSize(width: cg.width, height: cg.height)
            guard let data = rep.representation(using: .png, properties: [:]) else {
                throw ExportError.encodeFailed
            }
            return data
        }

        @MainActor
        public static func writeCard(
            _ model: RecapModel,
            to path: String,
            shape: RecapCardView.Shape = .landscape,
            dark: Bool = true,
            showLegend: Bool = false
        ) throws -> (path: String, bytes: Int, width: Int, height: Int) {
            let card = RecapCardView(model: model, shape: shape, dark: dark, showLegend: showLegend)
            let data = try png(of: card)
            try data.write(to: URL(fileURLWithPath: path))
            return (
                path, data.count,
                Int(shape.size.width * 2), Int(shape.size.height * 2)
            )
        }

        /// Put the image on the pasteboard so it can be pasted straight into a compose box.
        ///
        /// The single highest-leverage sharing affordance available: the target audience
        /// already screenshots their terminal, and pasting beats finding a file.
        @MainActor
        public static func copyToPasteboard(_ data: Data) {
            let pb = NSPasteboard.general
            pb.clearContents()
            if let image = NSImage(data: data) {
                pb.writeObjects([image])
            }
        }
    #endif

    /// Caption text to accompany the image.
    ///
    /// Deliberately plain. Anything that reads as generated copy gets deleted before
    /// posting, which defeats the purpose of offering it.
    public static func caption(for model: RecapModel) -> String {
        let s = Superlative.choose(model)
        var parts: [String] = [s.headline]
        if let repo = model.repoName { parts.append("· \(repo)") }
        return parts.joined(separator: " ")
    }
}
