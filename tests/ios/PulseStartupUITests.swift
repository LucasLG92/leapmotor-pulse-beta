import XCTest

@MainActor
final class PulseStartupUITests: XCTestCase {
    func testGenericLoginRenders() {
        continueAfterFailure = false

        let app = XCUIApplication(bundleIdentifier: "com.lucas.pulse")
        let heading = app.staticTexts["Tu espacio privado de movilidad."]
        let notice = app.staticTexts["El acceso de esta app aún no está configurado."]
        let loginButton = app.buttons["Iniciar sesión"]
        app.launch()
        let deadline = ProcessInfo.processInfo.systemUptime + 90

        while ProcessInfo.processInfo.systemUptime < deadline {
            if app.state == .runningForeground,
               heading.exists, heading.isHittable,
               notice.exists, notice.isHittable,
               loginButton.exists, !loginButton.isEnabled {
                break
            }
            RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.25))
        }

        let foreground = app.state == .runningForeground
        let headingVisibleAndHittable = heading.exists && heading.isHittable
        let noticeVisibleAndHittable = notice.exists && notice.isHittable
        let loginButtonExists = loginButton.exists
        let loginButtonDisabled = loginButton.exists && !loginButton.isEnabled

        func attachGenericAppScreenshot(_ screenshot: XCUIScreenshot) {
            let attachment = XCTAttachment(screenshot: screenshot)
            attachment.name = "Pulse generic startup"
            attachment.lifetime = .keepAlways
            add(attachment)
        }

        let checks: [(Bool, String)] = [
            (foreground, "Pulse must be running in the foreground."),
            (headingVisibleAndHittable, "The exact generic mobility heading must be visible and hittable."),
            (noticeVisibleAndHittable, "The exact unconfigured-access notice must be visible and hittable."),
            (loginButtonExists, "The generic login button must exist."),
            (loginButtonDisabled, "The generic login button must be disabled.")
        ]

        for (passed, message) in checks {
            guard passed else {
                attachGenericAppScreenshot(app.screenshot())
                XCTFail(message)
                return
            }
        }

        XCTAssertTrue(foreground, "Pulse must be running in the foreground.")
        XCTAssertTrue(headingVisibleAndHittable, "The exact generic mobility heading must be visible and hittable.")
        XCTAssertTrue(noticeVisibleAndHittable, "The exact unconfigured-access notice must be visible and hittable.")
        XCTAssertTrue(loginButtonExists, "The generic login button must exist.")
        XCTAssertTrue(loginButtonDisabled, "The generic login button must be disabled.")

        let snapshot = app.screenshot()
        attachGenericAppScreenshot(snapshot)
        let png = snapshot.pngRepresentation
        guard !png.isEmpty, png.count <= 10 * 1024 * 1024 else {
            XCTFail("The verified startup screenshot must be a non-empty PNG no larger than 10 MiB.")
            return
        }

        print("PULSE_CI_UI_PNG_V1:" + png.base64EncodedString())
        print("PULSE_CI_UI_READY_V1")
    }
}
