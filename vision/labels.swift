// One image per invocation. Keep stdout machine-readable; errors go to stderr.
import Foundation
import Vision
import ImageIO

struct Observation: Codable {
    let text: String
    let confidence: Float
    let box: [Double]  // normalized x,y,width,height; lower-left origin (Vision)
}

do {
    guard CommandLine.arguments.count == 2 else {
        throw NSError(domain: "labels", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "expected one image path"])
    }
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["en-US"]
    request.usesLanguageCorrection = false  // Do not silently correct reagent names.
    request.minimumTextHeight = 0
    try VNImageRequestHandler(url: url, options: [:]).perform([request])
    let rows: [Observation] = (request.results ?? []).compactMap { result in
        guard let candidate = result.topCandidates(1).first else { return nil }
        let b = result.boundingBox
        return Observation(text: candidate.string, confidence: candidate.confidence,
                           box: [Double(b.minX), Double(b.minY), Double(b.width), Double(b.height)])
    }
    let data = try JSONEncoder().encode(rows)
    print(String(decoding: data, as: UTF8.self))
} catch {
    FileHandle.standardError.write(Data("Vision OCR failed: \(error.localizedDescription)\n".utf8))
    exit(1)
}
