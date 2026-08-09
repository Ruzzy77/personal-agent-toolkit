// Packaged source for the source-hashed local PDF extraction subprocess.
import AppKit
import Darwin
import Foundation
import PDFKit
import Vision

enum AdapterFailure: Error {
    case invalidRequest
    case invalidInput
    case invalidConfiguration
    case unreadablePDF
    case renderFailed
}

func boundedInt(
    _ value: Any?,
    default defaultValue: Int,
    minimum: Int,
    maximum: Int
) throws -> Int {
    guard let value else {
        return defaultValue
    }
    guard let number = value as? NSNumber else {
        throw AdapterFailure.invalidConfiguration
    }
    let result = number.intValue
    guard result >= minimum && result <= maximum else {
        throw AdapterFailure.invalidConfiguration
    }
    return result
}

func configuredString(
    _ value: Any?,
    default defaultValue: String,
    allowedValues: Set<String>
) throws -> String {
    guard let value else {
        return defaultValue
    }
    guard let result = value as? String, allowedValues.contains(result) else {
        throw AdapterFailure.invalidConfiguration
    }
    return result
}

func render(_ page: PDFPage, maxEdgePixels: Int) throws -> CGImage {
    let box = page.bounds(for: .mediaBox)
    guard box.width > 0, box.height > 0 else {
        throw AdapterFailure.renderFailed
    }
    let scale = min(
        CGFloat(maxEdgePixels) / box.width,
        CGFloat(maxEdgePixels) / box.height
    )
    let target = CGSize(
        width: max(1, floor(box.width * scale)),
        height: max(1, floor(box.height * scale))
    )
    let image = page.thumbnail(of: target, for: .mediaBox)
    var rectangle = CGRect(origin: .zero, size: image.size)
    guard let rendered = image.cgImage(
        forProposedRect: &rectangle,
        context: nil,
        hints: nil
    ) else {
        throw AdapterFailure.renderFailed
    }
    return rendered
}

func isVisuallyBlank(_ image: CGImage) -> Bool {
    let sampleWidth = min(image.width, 128)
    let sampleHeight = min(image.height, 128)
    guard sampleWidth > 0, sampleHeight > 0 else {
        return false
    }
    var pixels = [UInt8](repeating: 255, count: sampleWidth * sampleHeight)
    let rendered = pixels.withUnsafeMutableBytes { buffer in
        guard let context = CGContext(
            data: buffer.baseAddress,
            width: sampleWidth,
            height: sampleHeight,
            bitsPerComponent: 8,
            bytesPerRow: sampleWidth,
            space: CGColorSpaceCreateDeviceGray(),
            bitmapInfo: CGImageAlphaInfo.none.rawValue
        ) else {
            return false
        }
        context.setFillColor(gray: 1, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: sampleWidth, height: sampleHeight))
        context.interpolationQuality = .low
        context.draw(
            image,
            in: CGRect(x: 0, y: 0, width: sampleWidth, height: sampleHeight)
        )
        return true
    }
    guard rendered else {
        return false
    }
    return !pixels.contains { value in value < 250 }
}

func topLeftBoundingBox(_ box: CGRect) -> [Double] {
    let values = [
        box.minX,
        1.0 - box.maxY,
        box.maxX,
        1.0 - box.minY,
    ]
    return values.map { value in
        Double(min(max(value, 0), 1))
    }
}

#if compiler(>=6.2)
    @available(macOS 26.0, *)
    func topLeftBoundingBox(_ box: NormalizedRect) -> [Double] {
        let rectangle = box.verticallyFlipped().cgRect
        return [
            Double(rectangle.minX),
            Double(rectangle.minY),
            Double(rectangle.maxX),
            Double(rectangle.maxY),
        ]
    }
#endif

func normalizedText(_ text: String?) -> String {
    guard let text else {
        return ""
    }
    return text
        .replacingOccurrences(of: "\r\n", with: "\n")
        .replacingOccurrences(of: "\r", with: "\n")
        .trimmingCharacters(in: .whitespacesAndNewlines)
}

func alphanumericCharacterCount(_ text: String) -> Int {
    text.unicodeScalars.reduce(0) { count, scalar in
        count + (CharacterSet.alphanumerics.contains(scalar) ? 1 : 0)
    }
}

func writeResult(_ result: [String: Any]) throws {
    let data = try JSONSerialization.data(
        withJSONObject: result,
        options: [.sortedKeys, .withoutEscapingSlashes]
    )
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([0x0A]))
}

func appendWithinBudget(
    _ candidates: [[String: Any]],
    to units: inout [[String: Any]],
    totalContentCharacters: inout Int,
    maxUnits: Int,
    maxUnitContentCharacters: Int,
    maxTotalContentCharacters: Int
) -> Bool {
    for candidate in candidates {
        guard
            let content = candidate["content"] as? String,
            content.count <= maxUnitContentCharacters,
            units.count < maxUnits,
            totalContentCharacters <= maxTotalContentCharacters - content.count
        else {
            return false
        }
        units.append(candidate)
        totalContentCharacters += content.count
    }
    return true
}

func averageConfidence(_ lines: [RecognizedTextObservation]) -> Double? {
    let values = lines.compactMap { line in
        line.topCandidates(1).first.map { Double($0.confidence) }
    }
    guard !values.isEmpty else {
        return nil
    }
    let average = values.reduce(0, +) / Double(values.count)
    return min(max(average, 0), 1)
}

#if compiler(>=6.2)
    @available(macOS 26.0, *)
    func recognizeStructuredPage(
        _ image: CGImage,
        page: Int,
        languages: [String]
    ) async throws -> [[String: Any]] {
        var recognition = RecognizeDocumentsRequest()
        recognition.textRecognitionOptions.recognitionLanguages = languages.map {
            Locale.Language(identifier: $0)
        }
        recognition.textRecognitionOptions.useLanguageCorrection = true
        recognition.textRecognitionOptions.automaticallyDetectLanguage = true
        guard let document = try await recognition.perform(on: image).first?.document else {
            return []
        }

        var units: [[String: Any]] = []
        var structurallyOwnedLines: Set<UUID> = []
        for (tableIndex, table) in document.tables.enumerated() {
            for (rowIndex, row) in table.rows.enumerated() {
                for (cellIndex, cell) in row.enumerated() {
                    let text = normalizedText(cell.content.text.transcript)
                    guard !text.isEmpty else {
                        continue
                    }
                    let lines = cell.content.text.lines
                    structurallyOwnedLines.formUnion(lines.map(\.uuid))
                    var unit: [String: Any] = [
                        "unit_type": "table_cell",
                        "structure_path": [
                            "page": page,
                            "table": tableIndex + 1,
                            "row": rowIndex + 1,
                            "cell": cellIndex + 1,
                            "row_start": cell.rowRange.lowerBound + 1,
                            "row_end": cell.rowRange.upperBound,
                            "column_start": cell.columnRange.lowerBound + 1,
                            "column_end": cell.columnRange.upperBound,
                        ],
                        "content": text,
                        "derivation_method": "ocr",
                        "geometry": [
                            "coordinate_system": "top_left_normalized",
                            "bbox": topLeftBoundingBox(cell.content.boundingRegion.boundingBox),
                        ],
                        "quality_flags": [
                            "ocr",
                            "structured_ocr",
                            "table_cell",
                            "reading_order_unverified",
                        ],
                        "issues": [],
                    ]
                    if let confidence = averageConfidence(lines) {
                        unit["confidence"] = confidence
                    }
                    units.append(unit)
                }
            }
        }

        for (paragraphIndex, paragraph) in document.paragraphs.enumerated() {
            let lineIdentifiers = Set(paragraph.lines.map(\.uuid))
            if !lineIdentifiers.isEmpty && lineIdentifiers.isSubset(of: structurallyOwnedLines) {
                continue
            }
            let text = normalizedText(paragraph.transcript)
            guard !text.isEmpty else {
                continue
            }
            var unit: [String: Any] = [
                "unit_type": "paragraph",
                "structure_path": [
                    "page": page,
                    "paragraph": paragraphIndex + 1,
                ],
                "content": text,
                "derivation_method": "ocr",
                "geometry": [
                    "coordinate_system": "top_left_normalized",
                    "bbox": topLeftBoundingBox(paragraph.boundingRegion.boundingBox),
                ],
                "quality_flags": ["ocr", "structured_ocr", "reading_order_unverified"],
                "issues": [],
            ]
            if let confidence = averageConfidence(paragraph.lines) {
                unit["confidence"] = confidence
            }
            units.append(unit)
        }
        return units
    }
#endif

func recognizeTextPage(
    _ image: CGImage,
    page: Int,
    languages: [String]
) throws -> [[String: Any]] {
    let recognition = VNRecognizeTextRequest()
    recognition.recognitionLevel = .accurate
    recognition.recognitionLanguages = languages
    recognition.usesLanguageCorrection = true
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    try handler.perform([recognition])
    var units: [[String: Any]] = []
    for (visionIndex, observation) in (recognition.results ?? []).enumerated() {
        guard let candidate = observation.topCandidates(1).first else {
            continue
        }
        let text = normalizedText(candidate.string)
        guard !text.isEmpty else {
            continue
        }
        units.append([
            "unit_type": "page_region",
            "structure_path": [
                "page": page,
                "vision_index": visionIndex,
            ],
            "content": text,
            "derivation_method": "ocr",
            "geometry": [
                "coordinate_system": "top_left_normalized",
                "bbox": topLeftBoundingBox(observation.boundingBox),
            ],
            "confidence": Double(candidate.confidence),
            "quality_flags": ["ocr", "reading_order_unverified"],
            "issues": [],
        ])
    }
    return units
}

func runAdapter() async throws {
    guard
        let requestLine = readLine(),
        let requestData = requestLine.data(using: .utf8),
        let request = try JSONSerialization.jsonObject(with: requestData) as? [String: Any],
        request["schema_version"] as? String == "corpus.extraction-request.v1",
        request["operation"] as? String == "extract",
        let input = request["input"] as? [String: Any],
        input["kind"] as? String == "read_only_file_descriptor",
        input["format_id"] as? String == "pdf",
        let fileDescriptor = input["file_descriptor"] as? Int,
        fileDescriptor >= 0,
        fileDescriptor <= Int(Int32.max),
        let path = input["path"] as? String,
        path == "/dev/fd/\(fileDescriptor)",
        fcntl(Int32(fileDescriptor), F_GETFD) != -1
    else {
        throw AdapterFailure.invalidRequest
    }

    let config = request["config"] as? [String: Any] ?? [:]
    let maxPages = try boundedInt(
        config["max_pages"],
        default: 200,
        minimum: 1,
        maximum: 2_000
    )
    let maxEdgePixels = try boundedInt(
        config["max_edge_pixels"],
        default: 3_000,
        minimum: 512,
        maximum: 8_192
    )
    let languages = config["recognition_languages"] as? [String] ?? ["ko-KR", "en-US"]
    guard !languages.isEmpty, languages.count <= 8 else {
        throw AdapterFailure.invalidConfiguration
    }
    let ocrScope = try configuredString(
        config["ocr_scope"],
        default: "hybrid",
        allowedValues: ["hybrid", "all_pages", "textless_pages"]
    )
    let nativeTextMinAlphanumericCharacters = try boundedInt(
        config["native_text_min_alphanumeric_characters"],
        default: 32,
        minimum: 1,
        maximum: 10_000
    )
    let budgets = request["budgets"] as? [String: Any] ?? [:]
    let maxUnits = try boundedInt(
        budgets["max_units"],
        default: 250_000,
        minimum: 1,
        maximum: 1_000_000
    )
    let maxUnitContentCharacters = try boundedInt(
        budgets["max_unit_content_chars"],
        default: 5_000_000,
        minimum: 1,
        maximum: 50_000_000
    )
    let maxTotalContentCharacters = try boundedInt(
        budgets["max_total_content_chars"],
        default: 150_000_000,
        minimum: 1,
        maximum: 500_000_000
    )

    let inputURL = URL(fileURLWithPath: path)
    guard let document = PDFDocument(url: inputURL) else {
        throw AdapterFailure.unreadablePDF
    }

    var units: [[String: Any]] = []
    var totalContentCharacters = 0
    var issues: [[String: Any]] = []
    let pageCount = min(document.pageCount, maxPages)
    if document.pageCount > maxPages {
        issues.append([
            "code": "pdf_page_limit_reached",
            "message": "PDF extraction stopped at the configured page limit.",
            "severity": "warning",
            "details": ["processed_pages": maxPages, "document_pages": document.pageCount],
        ])
    }

    for pageIndex in 0..<pageCount {
        guard let page = document.page(at: pageIndex) else {
            issues.append([
                "code": "pdf_page_unavailable",
                "message": "PDFKit could not open one page.",
                "severity": "warning",
                "details": ["page": pageIndex + 1],
            ])
            continue
        }

        let nativeText = normalizedText(page.string)
        if !nativeText.isEmpty {
            if !appendWithinBudget(
                [[
                    "unit_type": "page",
                    "structure_path": ["page": pageIndex + 1],
                    "content": nativeText,
                    "derivation_method": "native_text",
                    "quality_flags": ["reading_order_unverified"],
                    "issues": [],
                ]],
                to: &units,
                totalContentCharacters: &totalContentCharacters,
                maxUnits: maxUnits,
                maxUnitContentCharacters: maxUnitContentCharacters,
                maxTotalContentCharacters: maxTotalContentCharacters
            ) {
                issues.append([
                    "code": "adapter_budget_reached",
                    "message": "PDF extraction stopped at the configured result budget.",
                    "severity": "warning",
                    "details": ["page": pageIndex + 1],
                ])
                break
            }
        }

        let shouldRunOCR: Bool
        switch ocrScope {
        case "all_pages":
            shouldRunOCR = true
        case "textless_pages":
            shouldRunOCR = nativeText.isEmpty
        default:
            shouldRunOCR = (
                alphanumericCharacterCount(nativeText)
                    < nativeTextMinAlphanumericCharacters
            )
        }
        if !shouldRunOCR {
            continue
        }

        do {
            let image = try autoreleasepool {
                try render(page, maxEdgePixels: maxEdgePixels)
            }
            if nativeText.isEmpty && isVisuallyBlank(image) {
                continue
            }
            var recognized: [[String: Any]] = []
#if compiler(>=6.2)
                if #available(macOS 26.0, *) {
                    do {
                        recognized = try await recognizeStructuredPage(
                            image,
                            page: pageIndex + 1,
                            languages: languages
                        )
                    } catch {
                        issues.append([
                            "code": "pdf_structured_ocr_fallback",
                            "message": (
                                "Structured Vision recognition failed; text-region OCR was used "
                                    + "for one page."
                            ),
                            "severity": "warning",
                            "details": ["page": pageIndex + 1],
                        ])
                        recognized = try recognizeTextPage(
                            image,
                            page: pageIndex + 1,
                            languages: languages
                        )
                    }
                } else {
                    recognized = try recognizeTextPage(
                        image,
                        page: pageIndex + 1,
                        languages: languages
                    )
                }
#else
                recognized = try recognizeTextPage(
                    image,
                    page: pageIndex + 1,
                    languages: languages
                )
#endif
            if !appendWithinBudget(
                recognized,
                to: &units,
                totalContentCharacters: &totalContentCharacters,
                maxUnits: maxUnits,
                maxUnitContentCharacters: maxUnitContentCharacters,
                maxTotalContentCharacters: maxTotalContentCharacters
            ) {
                issues.append([
                    "code": "adapter_budget_reached",
                    "message": "PDF extraction stopped at the configured result budget.",
                    "severity": "warning",
                    "details": ["page": pageIndex + 1],
                ])
                break
            }
            if nativeText.isEmpty && recognized.isEmpty {
                issues.append([
                    "code": "pdf_page_without_text",
                    "message": "Neither PDFKit nor Vision found text on one page.",
                    "severity": "warning",
                    "details": ["page": pageIndex + 1],
                ])
            }
        } catch {
            issues.append([
                "code": "pdf_ocr_page_failed",
                "message": "Vision OCR could not process one PDF page.",
                "severity": "warning",
                "details": ["page": pageIndex + 1],
            ])
        }
    }

    let hasIncompleteIssue = issues.contains { issue in
        guard let severity = issue["severity"] as? String else {
            return false
        }
        return severity == "warning" || severity == "error"
    }

    try writeResult([
        "schema_version": "corpus.extraction-result.v1",
        "completeness": units.isEmpty || hasIncompleteIssue ? "partial" : "complete",
        "units": units,
        "issues": issues,
    ])
}

@main
struct CorpusPDFVisionAdapter {
    static func main() async {
        do {
            try await runAdapter()
        } catch {
            FileHandle.standardError.write(Data("PDF extraction adapter failed.\n".utf8))
            exit(2)
        }
    }
}
