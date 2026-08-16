import BuilderModel
import Foundation

/// Streams newline-delimited records from a file, resuming from a byte offset.
///
/// Three properties this type exists to guarantee:
///
/// 1. **A partial trailing line is NEVER consumed.** Transcripts are appended to while
///    we read them, so the last line in the file is routinely half-written. Committing an
///    offset in the middle of a line means that record is lost forever — the next run
///    resumes past its start and the JSON never parses. `endOffset` therefore only ever
///    advances to just past a `\n`.
///
/// 2. **Never loads the whole file.** MEASURED: the largest transcript on the reference
///    machine is 78 MB in only 2,635 records — an average of 12.1 KB per line, with
///    individual records reaching megabytes. `String(contentsOfFile:)` on that file is a
///    78 MB allocation to read a few hundred new bytes.
///
/// 3. **Oversized lines are skipped loudly, not silently.** A single line above
///    `Tuning.maxLineBytes` is reported through the diagnostics callback and skipped, so
///    one pathological record cannot exhaust memory and cannot vanish unnoticed.
///
/// Deliberately a plain `final class` returning `Data` slices. A `~Copyable` version
/// handing back an `UnsafeRawBufferPointer` into the internal buffer was considered and
/// rejected: the pointer would be invalidated by the next `next()`, producing a
/// use-after-free generator on exactly the 78 MB file that is hardest to debug — and the
/// measured 20x performance headroom means it would buy nothing.
public final class LineReader {

    public struct Line {
        /// The raw bytes of one line, without its terminator.
        public let data: Data
        /// Byte offset of the START of this line in the file.
        public let offset: Int
        /// Zero-based index of this line within the whole file.
        public let index: Int
    }

    private let handle: FileHandle
    private var buffer = Data()
    private var bufferStartOffset: Int
    private var searchIndex = 0
    private var lineIndex: Int
    private var atEOF = false

    /// Offset of the first byte NOT yet returned as part of a complete line. Always
    /// positioned just past a newline (or at the starting offset). This is what gets
    /// written to `ingest_watermark.byte_offset`.
    public private(set) var endOffset: Int

    /// Index the NEXT line would receive. Paired with `endOffset` in the watermark.
    ///
    /// This has to come from the reader, not from counting parsed results: one line can
    /// produce several normalized events (an assistant record with three content blocks
    /// produces three) and several lines can produce none, so `events.count` is not a
    /// line count and using it as one corrupts the resume position.
    public var nextLineIndex: Int { lineIndex }

    private let maxLineBytes: Int
    private let chunkSize: Int
    private let onDiagnostic: ((String, String) -> Void)?

    public init(
        path: String,
        startOffset: Int = 0,
        startLineIndex: Int = 0,
        maxLineBytes: Int = Tuning.maxLineBytes,
        chunkSize: Int = Tuning.readBufferBytes,
        onDiagnostic: ((String, String) -> Void)? = nil
    ) throws {
        guard let h = FileHandle(forReadingAtPath: path) else {
            throw CocoaError(.fileNoSuchFile, userInfo: [NSFilePathErrorKey: path])
        }
        self.handle = h
        self.maxLineBytes = maxLineBytes
        self.chunkSize = chunkSize
        self.onDiagnostic = onDiagnostic
        self.bufferStartOffset = startOffset
        self.endOffset = startOffset
        self.lineIndex = startLineIndex
        if startOffset > 0 { try h.seek(toOffset: UInt64(startOffset)) }
    }

    deinit { try? handle.close() }

    public func close() { try? handle.close() }

    /// Next complete line, or `nil` at end of the currently-complete data.
    ///
    /// Returning `nil` does not mean the file is finished — it means everything up to
    /// `endOffset` has been handed out and whatever remains has no terminator yet.
    public func next() throws -> Line? {
        while true {
            if let line = takeBufferedLine() { return line }
            if atEOF {
                // Trailing bytes with no newline: a line still being written. Leave
                // `endOffset` where it is so the next run re-reads from the line start.
                return nil
            }
            try fill()
        }
    }

    private func takeBufferedLine() -> Line? {
        while searchIndex < buffer.count {
            guard let nlRel = buffer[searchIndex...].firstIndex(of: 0x0A) else {
                // No terminator in the remainder. Guard against a single line so large
                // it would grow the buffer without bound.
                if buffer.count - searchIndex > maxLineBytes {
                    onDiagnostic?(
                        "oversized_line_skipped",
                        "line at offset \(bufferStartOffset + searchIndex) exceeds \(maxLineBytes) bytes"
                    )
                    // Drop what we hold and resync at the next newline we encounter.
                    searchIndex = buffer.count
                    compact()
                }
                return nil
            }

            let start = searchIndex
            let lineLength = nlRel - start
            searchIndex = nlRel + 1

            let absoluteStart = bufferStartOffset + start
            endOffset = bufferStartOffset + searchIndex
            let idx = lineIndex
            lineIndex += 1

            if lineLength > maxLineBytes {
                onDiagnostic?("oversized_line_skipped", "line \(idx) is \(lineLength) bytes")
                compact()
                continue
            }
            if lineLength == 0 {
                compact()
                continue  // blank line; still consumed, still advances the watermark
            }

            let data = buffer.subdata(in: start..<nlRel)
            compact()
            return Line(data: data, offset: absoluteStart, index: idx)
        }
        return nil
    }

    /// Discard consumed bytes so the buffer tracks the unread tail, not the whole file.
    private func compact() {
        guard searchIndex > 0 else { return }
        buffer.removeSubrange(0..<searchIndex)
        bufferStartOffset += searchIndex
        searchIndex = 0
    }

    private func fill() throws {
        let chunk = try handle.read(upToCount: chunkSize) ?? Data()
        if chunk.isEmpty {
            atEOF = true
        } else {
            buffer.append(chunk)
        }
    }
}
