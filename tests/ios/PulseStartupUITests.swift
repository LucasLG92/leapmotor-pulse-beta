import CoreGraphics
import UIKit
import XCTest

private struct PixelContrastResult {
    let passed: Bool
    let reason: String
    let contrastingPixels: Int
    let p95MinusP5Luma: Int
}

@MainActor
final class PulseStartupUITests: XCTestCase {
    private let headingText = "Tu espacio privado de movilidad."
    private let noticeText = "El acceso de esta app aún no está configurado."
    private let renderSize = 32
    private let minimumContrastingPixels = 12
    private let minimumLumaDelta = 12

    func testGenericLoginRenders() {
        continueAfterFailure = false

        let app = XCUIApplication(bundleIdentifier: "com.lucas.pulse")
        let heading = app.staticTexts[headingText]
        let notice = app.staticTexts[noticeText]
        let loginButton = app.buttons["Iniciar sesión"]
        app.launch()
        let readinessDeadline = ProcessInfo.processInfo.systemUptime + 90

        while ProcessInfo.processInfo.systemUptime < readinessDeadline {
            if hasSemanticReadiness(app: app, heading: heading, notice: notice,
                                    loginButton: loginButton) {
                break
            }
            RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.25))
        }

        func attachGenericAppScreenshot(_ screenshot: XCUIScreenshot) {
            let attachment = XCTAttachment(screenshot: screenshot)
            attachment.name = "Pulse generic startup"
            attachment.lifetime = .keepAlways
            add(attachment)
        }

        guard hasSemanticReadiness(app: app, heading: heading, notice: notice,
                                  loginButton: loginButton) else {
            attachGenericAppScreenshot(app.screenshot())
            XCTFail("The exact generic startup accessibility state did not become ready.")
            return
        }

        let checkDeadline = ProcessInfo.processInfo.systemUptime + 15
        var lastScreenshot: XCUIScreenshot?
        var verifiedScreenshot: XCUIScreenshot?
        var visualChecks = visualMetrics(
            passed: false, reason: "not-observed", heading: nil, notice: nil,
            framesStable: false, foregroundRevalidated: false,
            accessibilityRevalidated: false
        )

        while ProcessInfo.processInfo.systemUptime < checkDeadline {
            let appFrameBefore = app.frame
            let headingFrameBefore = heading.frame
            let noticeFrameBefore = notice.frame
            let candidate = app.screenshot()
            lastScreenshot = candidate

            let screenshotReturnedAt = ProcessInfo.processInfo.systemUptime
            let appFrameAfter = app.frame
            let headingFrameAfter = heading.frame
            let noticeFrameAfter = notice.frame
            let framesStable = appFrameBefore == appFrameAfter
                && headingFrameBefore == headingFrameAfter
                && noticeFrameBefore == noticeFrameAfter
            let foregroundRevalidated = app.state == .runningForeground
            let accessibilityRevalidated = hasSemanticReadiness(
                app: app, heading: heading, notice: notice, loginButton: loginButton
            )

            let image: CGImage? = candidate.image.imageOrientation == .up
                ? candidate.image.cgImage : nil
            let headingCheck = image.map {
                pixelContrast(frame: headingFrameBefore, appFrame: appFrameBefore, image: $0)
            }
            let noticeCheck = image.map {
                pixelContrast(frame: noticeFrameBefore, appFrame: appFrameBefore, image: $0)
            }
            let checksPass = framesStable && foregroundRevalidated
                && accessibilityRevalidated
                && headingCheck?.passed == true && noticeCheck?.passed == true

            var reason = "none"
            if !foregroundRevalidated {
                reason = "foreground-changed"
            } else if !accessibilityRevalidated {
                reason = "accessibility-changed"
            } else if !framesStable {
                reason = "frames-changed"
            } else if image == nil {
                reason = "invalid-image"
            } else if headingCheck?.passed != true {
                reason = "heading-\(headingCheck?.reason ?? "invalid-image")"
            } else if noticeCheck?.passed != true {
                reason = "notice-\(noticeCheck?.reason ?? "invalid-image")"
            }
            visualChecks = visualMetrics(
                passed: checksPass, reason: reason, heading: headingCheck,
                notice: noticeCheck, framesStable: framesStable,
                foregroundRevalidated: foregroundRevalidated,
                accessibilityRevalidated: accessibilityRevalidated
            )

            let checkCompletedAt = ProcessInfo.processInfo.systemUptime
            if screenshotReturnedAt > checkDeadline || checkCompletedAt > checkDeadline {
                visualChecks = visualMetrics(
                    passed: false, reason: "deadline", heading: headingCheck,
                    notice: noticeCheck, framesStable: framesStable,
                    foregroundRevalidated: foregroundRevalidated,
                    accessibilityRevalidated: accessibilityRevalidated
                )
                break
            }
            if checksPass {
                verifiedScreenshot = candidate
                break
            }
            RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.2))
        }

        if verifiedScreenshot == nil {
            emitVisualMetrics(visualChecks)
            if let lastScreenshot {
                attachGenericAppScreenshot(lastScreenshot)
            }
            XCTFail("The exact startup screenshot did not pass the bounded pixel contrast check.")
            return
        }

        guard let verifiedScreenshot else {
            XCTFail("The verified startup screenshot was lost before attachment.")
            return
        }
        attachGenericAppScreenshot(verifiedScreenshot)
        let png = verifiedScreenshot.pngRepresentation
        guard !png.isEmpty, png.count <= 10 * 1024 * 1024 else {
            XCTFail("The verified startup screenshot must be a non-empty PNG no larger than 10 MiB.")
            return
        }
        guard let metricLine = visualMetricsJSON(visualChecks) else {
            XCTFail("The startup pixel-check receipt could not be encoded.")
            return
        }

        print("PULSE_CI_UI_VISUAL_PIXEL_CHECKS_V2:" + metricLine)
        print("PULSE_CI_UI_PNG_V2:" + png.base64EncodedString())
        print("PULSE_CI_UI_READY_V2")
    }

    private func hasSemanticReadiness(
        app: XCUIApplication,
        heading: XCUIElement,
        notice: XCUIElement,
        loginButton: XCUIElement
    ) -> Bool {
        app.state == .runningForeground
            && heading.exists && heading.label == headingText && heading.isHittable
            && notice.exists && notice.label == noticeText && notice.isHittable
            && loginButton.exists && loginButton.label == "Iniciar sesión"
            && !loginButton.isEnabled
    }

    private func pixelContrast(frame: CGRect, appFrame: CGRect, image: CGImage) -> PixelContrastResult {
        guard finiteRect(frame), finiteRect(appFrame), appFrame.width > 0, appFrame.height > 0,
              frame.width > 0, frame.height > 0 else {
            return PixelContrastResult(passed: false, reason: "invalid-frame",
                                       contrastingPixels: 0, p95MinusP5Luma: 0)
        }
        let scaleX = CGFloat(image.width) / appFrame.width
        let scaleY = CGFloat(image.height) / appFrame.height
        guard scaleX.isFinite, scaleY.isFinite, scaleX > 0, scaleY > 0,
              abs(scaleX - scaleY) / max(scaleX, scaleY) <= 0.01 else {
            return PixelContrastResult(passed: false, reason: "incoherent-scale",
                                       contrastingPixels: 0, p95MinusP5Luma: 0)
        }
        guard frame.minX >= appFrame.minX, frame.minY >= appFrame.minY,
              frame.maxX <= appFrame.maxX, frame.maxY <= appFrame.maxY else {
            return PixelContrastResult(passed: false, reason: "frame-out-of-bounds",
                                       contrastingPixels: 0, p95MinusP5Luma: 0)
        }

        let x0 = floor((frame.minX - appFrame.minX) * scaleX)
        let y0 = floor((frame.minY - appFrame.minY) * scaleY)
        let x1 = ceil((frame.maxX - appFrame.minX) * scaleX)
        let y1 = ceil((frame.maxY - appFrame.minY) * scaleY)
        guard x0.isFinite, y0.isFinite, x1.isFinite, y1.isFinite,
              x0 >= 0, y0 >= 0, x1 <= CGFloat(image.width), y1 <= CGFloat(image.height),
              x1 > x0, y1 > y0 else {
            return PixelContrastResult(passed: false, reason: "crop-out-of-bounds",
                                       contrastingPixels: 0, p95MinusP5Luma: 0)
        }
        guard let crop = image.cropping(to: CGRect(x: x0, y: y0, width: x1 - x0, height: y1 - y0)) else {
            return PixelContrastResult(passed: false, reason: "crop-unavailable",
                                       contrastingPixels: 0, p95MinusP5Luma: 0)
        }

        var rgba = [UInt8](repeating: 0, count: renderSize * renderSize * 4)
        let bitmapInfo = CGBitmapInfo.byteOrder32Big.union(
            CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue)
        ).rawValue
        let rendered = rgba.withUnsafeMutableBytes { bytes -> Bool in
            guard let context = CGContext(
                data: bytes.baseAddress,
                width: renderSize,
                height: renderSize,
                bitsPerComponent: 8,
                bytesPerRow: renderSize * 4,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: bitmapInfo
            ) else {
                return false
            }
            context.interpolationQuality = .high
            context.draw(crop, in: CGRect(x: 0, y: 0, width: renderSize, height: renderSize))
            return true
        }
        guard rendered else {
            return PixelContrastResult(passed: false, reason: "render-unavailable",
                                       contrastingPixels: 0, p95MinusP5Luma: 0)
        }
        guard stride(from: 3, to: rgba.count, by: 4).allSatisfy({ rgba[$0] == 255 }) else {
            return PixelContrastResult(passed: false, reason: "transparent-pixels",
                                       contrastingPixels: 0, p95MinusP5Luma: 0)
        }

        var luma = [Double]()
        luma.reserveCapacity(renderSize * renderSize)
        for index in stride(from: 0, to: rgba.count, by: 4) {
            let red = Double(rgba[index])
            let green = Double(rgba[index + 1])
            let blue = Double(rgba[index + 2])
            luma.append(0.2126 * red + 0.7152 * green + 0.0722 * blue)
        }
        let sortedLuma = luma.sorted()
        let median = sortedLuma[sortedLuma.count / 2]
        let p5 = sortedLuma[Int(Double(sortedLuma.count - 1) * 0.05)]
        let p95 = sortedLuma[Int(Double(sortedLuma.count - 1) * 0.95)]
        let spread = Int((p95 - p5).rounded(.down))
        let contrastingPixels = luma.filter {
            abs($0 - median) >= Double(minimumLumaDelta)
        }.count
        let passed = spread >= minimumLumaDelta
            && contrastingPixels >= minimumContrastingPixels
        return PixelContrastResult(passed: passed,
                                   reason: passed ? "none" : "low-distributed-contrast",
                                   contrastingPixels: contrastingPixels,
                                   p95MinusP5Luma: spread)
    }

    private func finiteRect(_ rect: CGRect) -> Bool {
        [rect.minX, rect.minY, rect.width, rect.height].allSatisfy(\.isFinite)
    }

    private func visualMetrics(
        passed: Bool,
        reason: String,
        heading: PixelContrastResult?,
        notice: PixelContrastResult?,
        framesStable: Bool,
        foregroundRevalidated: Bool,
        accessibilityRevalidated: Bool
    ) -> [String: Any] {
        [
            "passed": passed,
            "reason": reason,
            "sample_size": renderSize * renderSize,
            "minimum_contrasting_pixels": minimumContrastingPixels,
            "minimum_luma_delta": minimumLumaDelta,
            "heading_contrasting_pixels": heading?.contrastingPixels ?? 0,
            "heading_p95_minus_p5_luma": heading?.p95MinusP5Luma ?? 0,
            "notice_contrasting_pixels": notice?.contrastingPixels ?? 0,
            "notice_p95_minus_p5_luma": notice?.p95MinusP5Luma ?? 0,
            "frames_stable": framesStable,
            "foreground_revalidated": foregroundRevalidated,
            "accessibility_revalidated": accessibilityRevalidated
        ]
    }

    private func visualMetricsJSON(_ metrics: [String: Any]) -> String? {
        guard JSONSerialization.isValidJSONObject(metrics),
              let data = try? JSONSerialization.data(withJSONObject: metrics, options: [.sortedKeys]),
              let encoded = String(data: data, encoding: .utf8) else {
            return nil
        }
        return encoded
    }

    private func emitVisualMetrics(_ metrics: [String: Any]) {
        guard let encoded = visualMetricsJSON(metrics) else {
            print("PULSE_CI_UI_VISUAL_PIXEL_CHECKS_V2:invalid")
            return
        }
        print("PULSE_CI_UI_VISUAL_PIXEL_CHECKS_V2:" + encoded)
    }
}
