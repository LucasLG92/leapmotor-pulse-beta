import AuthenticationServices
import Capacitor
import CoreFoundation
import CryptoKit
import Foundation
import PulseCore
import Security
import UIKit
import WidgetKit

@objc(PulseNativePlugin)
public final class PulseNativePlugin: CAPPlugin, CAPBridgedPlugin, ASWebAuthenticationPresentationContextProviding {
    public let identifier = "PulseNativePlugin"
    public let jsName = "PulseNative"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "status", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "login", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "logout", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "request", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "cancelRequest", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "exportJourneyReport", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "linkWidget", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "setWidgetCache", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "clearWidget", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "pinWidget", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "startEvents", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "stopEvents", returnType: CAPPluginReturnPromise)
    ]

    private enum AuthorizationPurpose: Equatable {
        case app
        case widget

        var scopes: String {
            switch self {
            case .app: return "openid profile pulse.app.access"
            case .widget: return "openid pulse.widget.read"
            }
        }
    }

    private final class PendingAuthorization {
        let call: CAPPluginCall
        let purpose: AuthorizationPurpose
        var transaction: NativeOAuthTransaction
        let sessionGeneration: UInt64
        let previousSession: NativeSessionState.Snapshot?
        let widgetGeneration: UInt64
        let pairingCode: String?
        let deviceName: String?
        var webSession: ASWebAuthenticationSession?
        var request: NativeHTTPRequestHandle?
        var exchangeStarted = false

        init(call: CAPPluginCall, purpose: AuthorizationPurpose, transaction: NativeOAuthTransaction,
             sessionGeneration: UInt64, previousSession: NativeSessionState.Snapshot?,
             widgetGeneration: UInt64, pairingCode: String?, deviceName: String?) {
            self.call = call
            self.purpose = purpose
            self.transaction = transaction
            self.sessionGeneration = sessionGeneration
            self.previousSession = previousSession
            self.widgetGeneration = widgetGeneration
            self.pairingCode = pairingCode
            self.deviceName = deviceName
        }
    }

    private let sessions = NativeSessionState()
    private let credentials = NativeWidgetCredentialStore()
    private let journeyReportStore = NativeJourneyReportTemporaryStore()
    private var runtimeConfig: NativeRuntimeConfig?
    private var http: NativeHTTPClient?
    private var widgetCache: PulseWidgetCache?
    private var observers: [NSObjectProtocol] = []
    private var pendingAuthorization: PendingAuthorization?
    private var activeJourneyReportExport: PendingJourneyReportExport?
    private var journeyReportStoreReady = false
    private var activeEvents: NativeEventStream?
    private var latestSubscriptionNumber: Int64 = 0
    private var activeSubscriptionID: String?
    private var eventGeneration: UInt64 = 0
    private var eventsDesired = false
    private var eventsPaused = false
    private var widgetGeneration: UInt64 = 0
    private var didReconcileWidgetStatus = false
    private var lastWidgetLinked: Bool?
    private var cancellableNativeReads: [String: PendingNativeRead] = [:]
    private var cancelledBeforeDispatch: [String: TimeInterval] = [:]

    public override func load() {
        do {
            try journeyReportStore.removeStaleExports()
            journeyReportStoreReady = true
        } catch {
            journeyReportStoreReady = false
        }
        runtimeConfig = Self.readRuntimeConfig()
        if let runtimeConfig {
            http = NativeHTTPClient(config: runtimeConfig)
        }
        if let groupID = Self.readAppGroup() {
            widgetCache = PulseWidgetCache(groupID: groupID)
        }
        if let runtimeConfig, credentials.credential(apiOrigin: runtimeConfig.apiOrigin) == nil {
            try? widgetCache?.clear()
            reloadWidgetTimelines()
        }
        observers.append(NotificationCenter.default.addObserver(
            forName: UIApplication.willResignActiveNotification, object: nil, queue: .main
        ) { [weak self] _ in self?.pauseEvents() })
        observers.append(NotificationCenter.default.addObserver(
            forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main
        ) { [weak self] _ in self?.resumeEventsAndRefreshWidget() })
    }

    deinit {
        for observer in observers { NotificationCenter.default.removeObserver(observer) }
        activeEvents?.cancel()
        cancellableNativeReads.values.forEach { $0.cancel() }
        cancellableNativeReads.removeAll()
        cancelledBeforeDispatch.removeAll()
        pendingAuthorization?.webSession?.cancel()
        pendingAuthorization?.request?.cancel()
        if let export = activeJourneyReportExport {
            export.request?.cancel()
            export.pickerDelegate?.clearCallbacks()
            if let fileURL = export.temporaryFileURL {
                try? journeyReportStore.removeExport(at: fileURL)
            }
            let call = export.call
            DispatchQueue.main.async {
                export.picker?.delegate = nil
                export.picker?.dismiss(animated: false)
                call.reject("No se pudo confirmar el resultado de la exportación.",
                            "NATIVE_REPORT_EXPORT_OUTCOME_UNKNOWN")
            }
        }
    }

    @objc public func status(_ call: CAPPluginCall) {
        onMain { [weak self] in
            guard let self else { return }
            let config = self.runtimeConfig
            let credential = config.flatMap { self.credentials.credential(apiOrigin: $0.apiOrigin) }
            let enabled = config.map { self.credentials.cacheEnabled(apiOrigin: $0.apiOrigin) } ?? false
            let linked = credential != nil
            if !self.didReconcileWidgetStatus || (self.lastWidgetLinked == true && !linked) {
                if !linked {
                    self.clearWidgetSnapshot()
                    NativeWidgetBackgroundRefresh.scheduleIfEnabled()
                }
            }
            self.didReconcileWidgetStatus = true
            self.lastWidgetLinked = linked
            call.resolve([
                "configured": config != nil,
                "authenticated": self.sessions.isAuthenticated,
                "widgetLinked": linked,
                "widgetCacheEnabled": enabled && self.widgetCache != nil,
                "supportsRequestCancellation": true
            ])
        }
    }

    @objc public func login(_ call: CAPPluginCall) {
        onMain { [weak self] in self?.beginAuthorization(call, purpose: .app, pairingCode: nil, deviceName: nil) }
    }

    @objc public func logout(_ call: CAPPluginCall) {
        onMain { [weak self] in self?.performLogout(call, responseMode: false) }
    }

    @objc public func request(_ call: CAPPluginCall) {
        onMain { [weak self] in self?.performRequest(call) }
    }

    @objc public func cancelRequest(_ call: CAPPluginCall) {
        onMain { [weak self] in self?.performCancelRequest(call) }
    }

    @objc public func exportJourneyReport(_ call: CAPPluginCall) {
        onMain { [weak self] in self?.performJourneyReportExport(call) }
    }

    @objc public func linkWidget(_ call: CAPPluginCall) {
        onMain { [weak self] in
            guard let self else { return }
            let pairingCode = call.getString("pairingCode")
            let deviceName = call.getString("deviceName")
            guard let pairingCode,
                  pairingCode.range(of: "^[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil,
                  let deviceName, Self.validDeviceName(deviceName) else {
                call.reject("Los datos del widget no son válidos.", "NATIVE_WIDGET_INPUT")
                return
            }
            // No conservar el código de un solo uso dentro de las opciones del bridge.
            var sanitizedOptions = call.options ?? [:]
            sanitizedOptions.removeValue(forKey: "pairingCode")
            call.options = sanitizedOptions
            self.beginAuthorization(call, purpose: .widget,
                                    pairingCode: pairingCode, deviceName: deviceName.trimmingCharacters(in: .whitespacesAndNewlines))
        }
    }

    @objc public func setWidgetCache(_ call: CAPPluginCall) {
        onMain { [weak self] in
            guard let self else { return }
            guard let raw = call.options["enabled"] as? NSNumber,
                  CFGetTypeID(raw) == CFBooleanGetTypeID() else {
                call.reject("La preferencia de caché no es válida.", "NATIVE_WIDGET_INPUT")
                return
            }
            let enabled = raw.boolValue
            do {
                guard let config = self.runtimeConfig else {
                    guard !enabled else { throw NativeWidgetCredentialError.notLinked }
                    try self.credentials.disableCache()
                    self.advanceWidgetGeneration()
                    try self.widgetCache?.clear()
                    self.reloadWidgetTimelines()
                    NativeWidgetBackgroundRefresh.scheduleIfEnabled()
                    call.resolve()
                    return
                }
                if enabled && self.widgetCache == nil { throw NativeWidgetCredentialError.notLinked }
                if enabled && self.credentials.credential(apiOrigin: config.apiOrigin) == nil {
                    self.clearWidgetSnapshot()
                    throw NativeWidgetCredentialError.notLinked
                }
                try self.credentials.setCacheEnabled(enabled, apiOrigin: config.apiOrigin)
                self.advanceWidgetGeneration()
                if !enabled { try self.widgetCache?.clear() }
                self.reloadWidgetTimelines()
                NativeWidgetBackgroundRefresh.scheduleIfEnabled()
                call.resolve()
                if enabled { self.refreshWidgetSnapshot() }
            } catch {
                if enabled {
                    if let config = self.runtimeConfig {
                        try? self.credentials.setCacheEnabled(false, apiOrigin: config.apiOrigin)
                    } else {
                        try? self.credentials.disableCache()
                    }
                }
                try? self.widgetCache?.clear()
                self.reloadWidgetTimelines()
                NativeWidgetBackgroundRefresh.scheduleIfEnabled()
                call.reject("No se pudo guardar la preferencia de caché.", "NATIVE_WIDGET_CACHE")
            }
        }
    }

    @objc public func clearWidget(_ call: CAPPluginCall) {
        onMain { [weak self] in self?.performClearWidget(call) }
    }

    @objc public func pinWidget(_ call: CAPPluginCall) {
        call.resolve(["supported": false])
    }

    @objc public func startEvents(_ call: CAPPluginCall) {
        onMain { [weak self] in
            guard let self, let subscriptionID = call.getString("subscriptionId"),
                  let number = Self.parseSubscriptionID(subscriptionID) else {
                call.reject("La suscripción de eventos no es válida.", "NATIVE_EVENT_ID")
                return
            }
            guard let config = self.runtimeConfig, let session = self.sessions.snapshot() else {
                call.reject("Inicia sesión para recibir actualizaciones.", "NATIVE_UNAUTHENTICATED")
                return
            }
            guard number > self.latestSubscriptionNumber else {
                call.reject("La suscripción de eventos ya ha caducado.", "NATIVE_EVENT_STALE")
                return
            }
            self.latestSubscriptionNumber = number
            self.activeSubscriptionID = subscriptionID
            self.eventsDesired = true
            self.eventsPaused = false
            self.stopActiveEvents()
            self.eventGeneration = Self.nextGeneration(self.eventGeneration)
            self.openEvents(config: config, session: session,
                            subscriptionID: subscriptionID, generation: self.eventGeneration)
            call.resolve()
        }
    }

    @objc public func stopEvents(_ call: CAPPluginCall) {
        onMain { [weak self] in
            guard let self else { return }
            if let subscriptionID = call.getString("subscriptionId"),
               subscriptionID == self.activeSubscriptionID {
                self.eventsDesired = false
                self.eventsPaused = false
                self.activeSubscriptionID = nil
                self.eventGeneration = Self.nextGeneration(self.eventGeneration)
                self.stopActiveEvents()
            }
            call.resolve()
        }
    }

    public func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        if let window = bridge?.viewController?.viewIfLoaded?.window { return window }
        if let window = UIApplication.shared.connectedScenes
            .compactMap({ $0 as? UIWindowScene }).flatMap(\.windows).first(where: \.isKeyWindow) {
            return window
        }
        return UIWindow()
    }

    private func performRequest(_ call: CAPPluginCall) {
        guard let path = call.getString("path"), let method = call.getString("method"),
              NativeAPIPolicy.allows(path: path, method: method) else {
            Self.resolveResponse(call, status: 400, contentType: "application/json",
                                 body: "{\"detail\":\"La solicitud nativa no es válida.\"}")
            return
        }
        let requestID = call.getString("request_id")
        if call.options?.keys.contains("request_id") == true && requestID == nil {
            Self.resolveResponse(call, status: 400, contentType: "application/json",
                                 body: "{\"detail\":\"La solicitud nativa no es válida.\"}")
            return
        }
        if let requestID {
            guard Self.validNativeRequestID(requestID),
                  NativeAPIPolicy.isCancellableRead(path: path, method: method) else {
                Self.resolveResponse(call, status: 400, contentType: "application/json",
                                     body: "{\"detail\":\"La solicitud nativa no es válida.\"}")
                return
            }
        }
        if method == "POST", path == "/api/auth/logout" {
            if requestID != nil {
                Self.resolveResponse(call, status: 400, contentType: "application/json",
                                     body: "{\"detail\":\"La solicitud nativa no es válida.\"}")
                return
            }
            performLogout(call, responseMode: true)
            return
        }
        guard let session = sessions.snapshot() else {
            Self.resolveResponse(call, status: 401, contentType: "application/json",
                                 body: "{\"detail\":\"Inicia sesión para continuar.\"}")
            return
        }
        guard let http else {
            Self.resolveResponse(call, status: 503, contentType: "application/json",
                                 body: "{\"detail\":\"La app nativa no está configurada.\"}")
            return
        }
        let body: Data?
        if method == "GET" {
            guard call.getString("body")?.isEmpty != false else {
                Self.resolveResponse(call, status: 400, contentType: "application/json",
                                     body: "{\"detail\":\"La solicitud nativa no es válida.\"}")
                return
            }
            body = nil
        } else {
            guard let bodyString = call.getString("body"),
                  let data = bodyString.data(using: .utf8), data.count <= NativeHTTPClient.maximumRequestBytes,
                  let json = try? JSONSerialization.jsonObject(with: data), json is [String: Any] else {
                Self.resolveResponse(call, status: 400, contentType: "application/json",
                                     body: "{\"detail\":\"La solicitud nativa no es válida.\"}")
                return
            }
            body = data
        }
        let pendingRead: PendingNativeRead?
        if let requestID {
            pruneCancelledBeforeDispatch()
            if cancelledBeforeDispatch.removeValue(forKey: requestID) != nil {
                call.reject("La solicitud se canceló.", "NATIVE_CANCELLED")
                return
            }
            guard cancellableNativeReads[requestID] == nil,
                  cancellableNativeReads.count < 16 else {
                call.reject("Hay demasiadas solicitudes. Reintenta.", "NATIVE_BUSY")
                return
            }
            let registered = PendingNativeRead()
            cancellableNativeReads[requestID] = registered
            pendingRead = registered
        } else {
            pendingRead = nil
        }
        let methodForPolicy = method
        let handle = http.sendAPI(method: method, path: path, body: body, session: session) { [weak self] result in
            self?.onMain {
                guard let self else { return }
                if let requestID, let pendingRead,
                   self.cancellableNativeReads[requestID] === pendingRead {
                    self.cancellableNativeReads.removeValue(forKey: requestID)
                }
                if pendingRead?.isCancelled == true {
                    call.reject("La solicitud se canceló.", "NATIVE_CANCELLED")
                    return
                }
                let current = self.sessions.isCurrent(session.generation)
                switch result {
                case .failure:
                    if NativeRequestPolicy.mutationOutcomeUnknown(method: methodForPolicy,
                                                                  path: path,
                                                                  responseReceived: false,
                                                                  sessionCurrent: current) {
                        call.reject("No se pudo confirmar el resultado; la operación puede haberse guardado.",
                                    "NATIVE_MUTATION_OUTCOME_UNKNOWN")
                    } else {
                        call.reject("No se pudo contactar con Pulse.", "NATIVE_TRANSPORT")
                    }
                case .success(let response):
                    guard current else {
                        if NativeRequestPolicy.mutationOutcomeUnknown(method: methodForPolicy,
                                                                      path: path,
                                                                      responseReceived: true,
                                                                      sessionCurrent: false) {
                            call.reject("La sesión cambió mientras se completaba la operación.",
                                        "NATIVE_MUTATION_OUTCOME_UNKNOWN")
                        } else {
                            Self.resolveResponse(call, status: 401, contentType: "application/json",
                                                 body: "{\"detail\":\"La sesión ha caducado. Vuelve a iniciar sesión.\"}")
                        }
                        return
                    }
                    self.deliver(response, method: methodForPolicy, path: path,
                                 session: session, call: call)
                }
            }
        }
        if let requestID, let pendingRead,
           cancellableNativeReads[requestID] === pendingRead {
            pendingRead.handle = handle
            if pendingRead.isCancelled { handle?.cancel() }
        }
    }

    private func performCancelRequest(_ call: CAPPluginCall) {
        guard let requestID = call.getString("request_id"), Self.validNativeRequestID(requestID) else {
            call.resolve()
            return
        }
        pruneCancelledBeforeDispatch()
        if let pending = cancellableNativeReads[requestID] {
            pending.cancel()
        } else if cancelledBeforeDispatch[requestID] == nil, cancelledBeforeDispatch.count < 64 {
            cancelledBeforeDispatch[requestID] = ProcessInfo.processInfo.systemUptime + 15
        }
        call.resolve()
    }

    private func pruneCancelledBeforeDispatch() {
        let now = ProcessInfo.processInfo.systemUptime
        cancelledBeforeDispatch = cancelledBeforeDispatch.filter { $0.value > now }
    }

    private static func validNativeRequestID(_ value: String) -> Bool {
        value.range(of: "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                    options: .regularExpression) != nil
    }

    private func performJourneyReportExport(_ call: CAPPluginCall) {
        guard let days = Self.journeyReportDays(from: call) else {
            call.reject("Los datos de exportación no son válidos.", "NATIVE_REPORT_EXPORT_INVALID")
            return
        }
        guard activeJourneyReportExport == nil else {
            call.reject("Ya hay una exportación en curso.", "NATIVE_REPORT_EXPORT_BUSY")
            return
        }
        guard let session = sessions.snapshot() else {
            call.reject("La sesión ha caducado. Vuelve a iniciar sesión.",
                        "NATIVE_REPORT_EXPORT_UNAUTHENTICATED")
            return
        }
        guard journeyReportStoreReady, let http else {
            call.reject("No se pudo preparar el informe para exportarlo.", "NATIVE_REPORT_EXPORT_UNAVAILABLE")
            return
        }

        let export = PendingJourneyReportExport(call: call, days: days, session: session)
        activeJourneyReportExport = export
        let path = "/api/journeys/report?days=\(days)"
        let request = http.sendAPI(method: "GET", path: path, body: nil, session: session,
                                   limit: NativeJourneyReportPolicy.maximumBodyBytes) { [weak self, weak export] result in
            guard let self, let export else { return }
            self.onMain { [weak self, weak export] in
                guard let self, let export, self.activeJourneyReportExport === export else { return }
                export.request = nil
                guard self.sessions.isCurrent(export.session.generation) else {
                    self.failJourneyReportExport(export, code: "NATIVE_REPORT_EXPORT_UNAUTHENTICATED")
                    return
                }
                switch result {
                case .failure:
                    self.failJourneyReportExport(export, code: "NATIVE_REPORT_EXPORT_UNAVAILABLE")
                case .success(let response):
                    guard response.status == 200 else {
                        let code = [401, 403].contains(response.status)
                            ? "NATIVE_REPORT_EXPORT_UNAUTHENTICATED"
                            : "NATIVE_REPORT_EXPORT_UNAVAILABLE"
                        self.failJourneyReportExport(export, code: code)
                        return
                    }
                    guard response.contentType == "application/json",
                          let safeJSON = try? NativeJourneyReportPolicy.sanitizedJSON(
                            data: response.body, expectedDays: export.days
                          ) else {
                        self.failJourneyReportExport(export, code: "NATIVE_REPORT_EXPORT_INVALID")
                        return
                    }
                    guard self.sessions.isCurrent(export.session.generation) else {
                        self.failJourneyReportExport(export, code: "NATIVE_REPORT_EXPORT_UNAUTHENTICATED")
                        return
                    }
                    self.presentJourneyReportPicker(export, safeJSON: safeJSON)
                }
            }
        }
        if activeJourneyReportExport === export {
            guard let request else {
                failJourneyReportExport(export, code: "NATIVE_REPORT_EXPORT_UNAVAILABLE")
                return
            }
            export.request = request
        }
    }

    private func presentJourneyReportPicker(_ export: PendingJourneyReportExport, safeJSON: Data) {
        guard activeJourneyReportExport === export,
              sessions.isCurrent(export.session.generation) else {
            failJourneyReportExport(export, code: "NATIVE_REPORT_EXPORT_UNAUTHENTICATED")
            return
        }
        do {
            let fileURL = try journeyReportStore.createExport(data: safeJSON, days: export.days)
            export.temporaryFileURL = fileURL
            guard let viewController = bridge?.viewController,
                  viewController.viewIfLoaded?.window != nil,
                  viewController.presentedViewController == nil,
                  !viewController.isBeingDismissed else {
                failJourneyReportExport(export, code: "NATIVE_REPORT_EXPORT_UNAVAILABLE")
                return
            }

            let pickerDelegate = NativeJourneyReportPickerDelegate()
            pickerDelegate.onPicked = { [weak self, weak export] urls in
                guard let self, let export else { return }
                self.onMain { [weak self, weak export] in
                    guard let self, let export else { return }
                    self.finishJourneyReportPick(export, pickedURLs: urls)
                }
            }
            pickerDelegate.onCancelled = { [weak self, weak export] in
                guard let self, let export else { return }
                self.onMain { [weak self, weak export] in
                    guard let self, let export else { return }
                    self.finishJourneyReportPick(export, pickedURLs: nil)
                }
            }
            let picker = UIDocumentPickerViewController(forExporting: [fileURL], asCopy: true)
            picker.delegate = pickerDelegate
            export.pickerDelegate = pickerDelegate
            export.picker = picker
            viewController.present(picker, animated: true)
        } catch {
            failJourneyReportExport(export, code: "NATIVE_REPORT_EXPORT_UNAVAILABLE")
        }
    }

    private func finishJourneyReportPick(_ export: PendingJourneyReportExport, pickedURLs: [URL]?) {
        guard activeJourneyReportExport === export else { return }
        guard let pickedURLs else {
            resolveJourneyReportExport(export, status: "cancelled")
            return
        }
        guard sessions.isCurrent(export.session.generation) else {
            failJourneyReportExport(export, code: "NATIVE_REPORT_EXPORT_UNAUTHENTICATED")
            return
        }
        guard !pickedURLs.isEmpty else {
            failJourneyReportExport(export, code: "NATIVE_REPORT_EXPORT_OUTCOME_UNKNOWN")
            return
        }
        resolveJourneyReportExport(export, status: "completed")
    }

    private func resolveJourneyReportExport(_ export: PendingJourneyReportExport, status: String) {
        guard activeJourneyReportExport === export else { return }
        let cleaned = retireJourneyReportExport(export, dismissPicker: false)
        guard cleaned else {
            journeyReportStoreReady = false
            let code = status == "completed"
                ? "NATIVE_REPORT_EXPORT_OUTCOME_UNKNOWN"
                : "NATIVE_REPORT_EXPORT_UNAVAILABLE"
            let message = code == "NATIVE_REPORT_EXPORT_OUTCOME_UNKNOWN"
                ? "El resultado de la exportación no se pudo confirmar. Comprueba el destino antes de reintentar."
                : "No se pudo preparar el informe para exportarlo."
            export.call.reject(message, code)
            return
        }
        export.call.resolve(["status": status])
    }

    private func failJourneyReportExport(_ export: PendingJourneyReportExport, code: String) {
        guard activeJourneyReportExport === export else { return }
        let resultCode = code == "NATIVE_REPORT_EXPORT_UNAUTHENTICATED" && export.picker != nil
            ? "NATIVE_REPORT_EXPORT_OUTCOME_UNKNOWN" : code
        if !retireJourneyReportExport(export) { journeyReportStoreReady = false }
        switch resultCode {
        case "NATIVE_REPORT_EXPORT_UNAUTHENTICATED":
            export.call.reject("La sesión ha caducado. Vuelve a iniciar sesión.", resultCode)
        case "NATIVE_REPORT_EXPORT_INVALID":
            export.call.reject("Los datos de exportación no son válidos.", resultCode)
        case "NATIVE_REPORT_EXPORT_BUSY":
            export.call.reject("Ya hay una exportación en curso.", resultCode)
        case "NATIVE_REPORT_EXPORT_OUTCOME_UNKNOWN":
            export.call.reject("El resultado de la exportación no se pudo confirmar. Comprueba el destino antes de reintentar.", resultCode)
        default:
            export.call.reject("No se pudo preparar el informe para exportarlo.", "NATIVE_REPORT_EXPORT_UNAVAILABLE")
        }
    }

    @discardableResult
    private func retireJourneyReportExport(_ export: PendingJourneyReportExport,
                                           dismissPicker: Bool = true) -> Bool {
        guard activeJourneyReportExport === export else { return true }
        activeJourneyReportExport = nil
        export.request?.cancel()
        export.request = nil
        if let picker = export.picker {
            picker.delegate = nil
            if dismissPicker, picker.presentingViewController != nil {
                picker.dismiss(animated: false)
            }
        }
        export.picker = nil
        export.pickerDelegate?.clearCallbacks()
        export.pickerDelegate = nil
        var cleaned = true
        if let fileURL = export.temporaryFileURL {
            do {
                try journeyReportStore.removeExport(at: fileURL)
            } catch {
                cleaned = false
            }
        }
        export.temporaryFileURL = nil
        return cleaned
    }

    private static func journeyReportDays(from call: CAPPluginCall) -> Int? {
        guard let options = call.options, Set(options.keys) == ["days"],
              let days = options["days"] else { return nil }
        return NativeJourneyReportPolicy.requestedDays(arguments: ["days": days])
    }

    private func deliver(_ response: NativeHTTPResponse, method: String, path: String,
                         session: NativeSessionState.Snapshot, call: CAPPluginCall) {
        if response.status >= 300 && response.status < 400 {
            Self.resolveResponse(call, status: response.status, contentType: "application/json",
                                 body: "{\"detail\":\"Recurso no disponible.\"}")
            return
        }
        guard response.contentType.isEmpty || response.contentType == "application/json",
              let responseBody = String(data: response.body, encoding: .utf8) else {
            Self.resolveResponse(call, status: 502, contentType: "application/json",
                                 body: "{\"detail\":\"Pulse devolvió una respuesta no válida.\"}")
            return
        }
        if method == "GET", path == "/api/session", response.status == 200 {
            guard var object = try? JSONSerialization.jsonObject(with: response.body) as? [String: Any] else {
                Self.resolveResponse(call, status: 502, contentType: "application/json",
                                     body: "{\"detail\":\"Pulse devolvió una sesión no válida.\"}")
                return
            }
            guard let csrf = object["csrf_token"] as? String, NativeSessionState.validCSRF(csrf) else {
                Self.resolveResponse(call, status: 502, contentType: "application/json",
                                     body: "{\"detail\":\"Pulse devolvió una sesión no válida.\"}")
                return
            }
            guard sessions.updateCSRF(csrf, expectedGeneration: session.generation) else {
                Self.resolveResponse(call, status: 401, contentType: "application/json",
                                     body: "{\"detail\":\"La sesión ha caducado. Vuelve a iniciar sesión.\"}")
                return
            }
            object["csrf_token"] = NativeRequestPolicy.csrfMarker
            let bodyData = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data("{}".utf8)
            let body = String(data: bodyData, encoding: .utf8) ?? "{}"
            Self.resolveResponse(call, status: response.status,
                                 contentType: response.contentType.isEmpty ? "application/json" : response.contentType,
                                 body: body)
            return
        }
        Self.resolveResponse(call, status: response.status,
                             contentType: response.contentType.isEmpty ? "application/json" : response.contentType,
                             body: responseBody)
    }

    private func beginAuthorization(_ call: CAPPluginCall, purpose: AuthorizationPurpose,
                                    pairingCode: String?, deviceName: String?) {
        guard let config = runtimeConfig, http != nil else {
            call.reject("La app nativa no está configurada.", "NATIVE_NOT_CONFIGURED")
            return
        }
        let previous = sessions.snapshot()
        if purpose == .widget, previous == nil {
            call.reject("Inicia sesión antes de enlazar el widget.", "NATIVE_UNAUTHENTICATED")
            return
        }
        guard pendingAuthorization == nil else {
            call.reject("Ya hay una autenticación en curso.", "NATIVE_AUTH_BUSY")
            return
        }
        let generation = sessions.generation
        let transaction: NativeOAuthTransaction
        do { transaction = try NativeOAuthTransaction.begin(expectedGeneration: generation) }
        catch {
            call.reject("La autenticación segura no está disponible.", "NATIVE_PKCE_UNAVAILABLE")
            return
        }
        guard let authorizationURL = Self.authorizationURL(config: config, purpose: purpose,
                                                           transaction: transaction),
              let callbackHost = config.callback.host,
              !callbackHost.isEmpty else {
            call.reject("No se pudo iniciar la autenticación segura.", "NATIVE_AUTH_FAILED")
            return
        }
        let pending = PendingAuthorization(call: call, purpose: purpose, transaction: transaction,
                                           sessionGeneration: generation,
                                           previousSession: purpose == .app ? previous : nil,
                                           widgetGeneration: widgetGeneration,
                                           pairingCode: pairingCode, deviceName: deviceName)
        let callback = ASWebAuthenticationSession.Callback.https(host: callbackHost, path: config.callback.path)
        let webSession = ASWebAuthenticationSession(url: authorizationURL, callback: callback) { [weak self, weak pending] url, error in
            guard let self, let pending else { return }
            self.onMain { self.handleAuthorizationCallback(pending, callbackURL: url, error: error) }
        }
        webSession.presentationContextProvider = self
        webSession.prefersEphemeralWebBrowserSession = false
        pending.webSession = webSession
        pendingAuthorization = pending
        guard webSession.start() else {
            finishAuthorization(pending, message: "No se pudo abrir el acceso seguro.", code: "NATIVE_AUTH_BROWSER")
            return
        }
    }

    private func handleAuthorizationCallback(_ pending: PendingAuthorization, callbackURL: URL?, error: Error?) {
        guard pendingAuthorization === pending, !pending.exchangeStarted else { return }
        guard error == nil, let callbackURL,
              let codeAndState = Self.callbackValues(callbackURL, config: runtimeConfig),
              pending.transaction.consume(state: codeAndState.state,
                                          now: ProcessInfo.processInfo.systemUptime,
                                          currentGeneration: sessions.generation) else {
            finishAuthorization(pending, message: "No se pudo completar la autenticación.", code: "NATIVE_AUTH_FAILED")
            return
        }
        guard let config = runtimeConfig, let http else {
            finishAuthorization(pending, message: "La app nativa no está configurada.", code: "NATIVE_NOT_CONFIGURED")
            return
        }
        pending.exchangeStarted = true
        pending.request = http.sendTokenRequest(code: codeAndState.code,
                                                redirectURI: config.callback,
                                                verifier: pending.transaction.verifier) { [weak self, weak pending] result in
            guard let self, let pending else { return }
            self.onMain {
                guard self.pendingAuthorization === pending else { return }
                switch result {
                case .failure:
                    self.finishAuthorization(pending, message: "No se pudo completar la autenticación.", code: "NATIVE_AUTH_FAILED")
                case .success(let response):
                    guard response.status == 200, response.contentType == "application/json",
                          let object = try? JSONSerialization.jsonObject(with: response.body) as? [String: Any],
                          let accessToken = object["access_token"] as? String,
                          Self.validAccessToken(accessToken) else {
                        self.finishAuthorization(pending, message: "No se pudo completar la autenticación.", code: "NATIVE_AUTH_FAILED")
                        return
                    }
                    switch pending.purpose {
                    case .app: self.completeAppLogin(pending, accessToken: accessToken)
                    case .widget: self.completeWidgetLink(pending, accessToken: accessToken)
                    }
                }
            }
        }
    }

    private func completeAppLogin(_ pending: PendingAuthorization, accessToken: String) {
        guard sessions.generation == pending.sessionGeneration, let http else {
            finishAuthorization(pending, message: "La autenticación ya no está activa.", code: "NATIVE_AUTH_STALE")
            return
        }
        pending.request = http.sendBootstrap(accessToken: accessToken) { [weak self, weak pending] result in
            guard let self, let pending else { return }
            self.onMain {
                guard self.pendingAuthorization === pending else { return }
                guard case .success(let response) = result,
                      response.status == 200, response.contentType == "application/json",
                      let body = try? JSONSerialization.jsonObject(with: response.body) as? [String: Any],
                      (body["ok"] as? Bool) == true,
                      let ttl = Self.integer(body["expires_in"]), (1...1_800).contains(ttl),
                      let cookie = Self.sessionCookie(response.headers, expectedTTL: ttl) else {
                    self.finishAuthorization(pending, message: "No se pudo iniciar sesión en Pulse.", code: "NATIVE_BOOTSTRAP_FAILED")
                    return
                }
                let provisional = NativeSessionState.Snapshot(cookie: cookie, csrf: "",
                                                              expiresAtUptime: ProcessInfo.processInfo.systemUptime + Double(ttl),
                                                              generation: pending.sessionGeneration)
                guard let http = self.http else {
                    self.finishAuthorization(pending, message: "No se pudo iniciar sesión en Pulse.", code: "NATIVE_BOOTSTRAP_FAILED")
                    return
                }
                pending.request = http.sendAPI(method: "GET", path: "/api/session", body: nil,
                                               session: provisional, limit: NativeHTTPClient.maximumOAuthBytes) { [weak self, weak pending] result in
                    guard let self, let pending else { return }
                    self.onMain {
                        guard self.pendingAuthorization === pending else { return }
                        guard case .success(let sessionResponse) = result,
                              sessionResponse.status == 200,
                              sessionResponse.contentType == "application/json",
                              let sessionBody = try? JSONSerialization.jsonObject(with: sessionResponse.body) as? [String: Any],
                              (sessionBody["authenticated"] as? Bool) == true,
                              let csrf = sessionBody["csrf_token"] as? String,
                              NativeSessionState.validCSRF(csrf),
                              let user = sessionBody["user"] as? [String: Any],
                              let name = user["name"] as? String,
                              !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                            self.finishAuthorization(pending, message: "No se pudo iniciar sesión en Pulse.", code: "NATIVE_BOOTSTRAP_FAILED")
                            return
                        }
                        guard self.sessions.install(cookie: cookie, csrf: csrf, ttlSeconds: ttl,
                                                    expectedGeneration: pending.sessionGeneration) else {
                            let bootstrap = NativeSessionState.Snapshot(cookie: cookie, csrf: csrf,
                                                                        expiresAtUptime: provisional.expiresAtUptime,
                                                                        generation: pending.sessionGeneration)
                            http.logout(bootstrap)
                            self.finishAuthorization(pending, message: "La autenticación ya no está activa.", code: "NATIVE_AUTH_STALE")
                            return
                        }
                        if let previous = pending.previousSession { http.logout(previous) }
                        self.finishAuthorization(pending, message: nil, code: nil)
                    }
                }
            }
        }
    }

    private func completeWidgetLink(_ pending: PendingAuthorization, accessToken: String) {
        guard sessions.isCurrent(pending.sessionGeneration),
              let config = runtimeConfig, let http,
              let pairingCode = pending.pairingCode,
              let deviceName = pending.deviceName,
              pending.widgetGeneration == widgetGeneration else {
            finishAuthorization(pending, message: "El enlace del widget ya no está activo.", code: "NATIVE_AUTH_STALE")
            return
        }

        let installationID: String
        let previousCredential: NativeWidgetCredentialStore.Credential?
        let cacheWasEnabled: Bool
        do {
            installationID = try credentials.installationID()
            let existingCredential = credentials.credential(apiOrigin: config.apiOrigin)
            cacheWasEnabled = credentials.cacheEnabled(apiOrigin: config.apiOrigin)
            previousCredential = try credentials.advanceGeneration(apiOrigin: config.apiOrigin) ?? existingCredential
        } catch {
            finishAuthorization(pending, message: "No se pudo enlazar el widget.", code: "NATIVE_WIDGET_LINK_FAILED")
            return
        }
        advanceWidgetGeneration()
        let operationGeneration = widgetGeneration
        if cacheWasEnabled {
            do { try widgetCache?.clear() }
            catch {
                finishAuthorization(pending, message: "No se pudo enlazar el widget.", code: "NATIVE_WIDGET_LINK_FAILED")
                return
            }
            reloadWidgetTimelines()
        }

        pending.request = http.sendWidgetRegistration(accessToken: accessToken,
                                                       pairingCode: pairingCode,
                                                       installationID: installationID,
                                                       deviceName: deviceName) { [weak self, weak pending] result in
            guard let self, let pending else { return }
            self.onMain {
                guard self.pendingAuthorization === pending else { return }
                guard case .success(let response) = result,
                      response.status == 200, response.contentType == "application/json",
                      let object = try? JSONSerialization.jsonObject(with: response.body) as? [String: Any],
                      let grant = object["grant"] as? [String: Any],
                      let grantID = grant["id"] as? String,
                      let profileID = grant["profile_id"] as? String,
                      let token = object["widget_token"] as? String,
                      let expiration = grant["expires_at"] as? String,
                      let expiresAt = Self.parseDate(expiration),
                      Self.validGrant(grantID: grantID, profileID: profileID,
                                      token: token, expiresAt: expiresAt) else {
                    self.finishAuthorization(pending, message: "No se pudo enlazar el widget. Comprueba el código.",
                                             code: "NATIVE_WIDGET_LINK_FAILED")
                    return
                }
                guard self.sessions.isCurrent(pending.sessionGeneration),
                      self.widgetGeneration == operationGeneration else {
                    _ = http.sendWidgetRevocation(grantID: grantID, token: token) { _ in }
                    self.finishAuthorization(pending, message: "El enlace del widget ya no está activo.", code: "NATIVE_AUTH_STALE")
                    return
                }
                do {
                    _ = try self.credentials.storeGrant(grantID: grantID, profileID: profileID,
                                                        widgetToken: token, apiOrigin: config.apiOrigin,
                                                        expiresAt: expiresAt)
                } catch {
                    _ = http.sendWidgetRevocation(grantID: grantID, token: token) { _ in }
                    self.finishAuthorization(pending, message: "No se pudo enlazar el widget.", code: "NATIVE_WIDGET_LINK_FAILED")
                    return
                }
                self.advanceWidgetGeneration()
                if let old = previousCredential, old.grantID != grantID {
                    _ = http.sendWidgetRevocation(grantID: old.grantID, token: old.widgetToken) { _ in }
                }
                self.reloadWidgetTimelines()
                NativeWidgetBackgroundRefresh.scheduleIfEnabled()
                self.finishAuthorization(pending, message: nil, code: nil)
                if cacheWasEnabled { self.refreshWidgetSnapshot() }
            }
        }
    }

    private func finishAuthorization(_ pending: PendingAuthorization, message: String?, code: String?) {
        guard pendingAuthorization === pending else { return }
        pendingAuthorization = nil
        pending.webSession = nil
        pending.request = nil
        if let message {
            pending.call.reject(message, code)
            if pending.purpose == .widget { refreshWidgetSnapshot() }
        } else {
            pending.call.resolve()
        }
    }

    private func performLogout(_ call: CAPPluginCall, responseMode: Bool) {
        let session = sessions.snapshot()
        let config = runtimeConfig
        let credential = config.flatMap { credentials.credential(apiOrigin: $0.apiOrigin) }
        sessions.invalidate()
        if let export = activeJourneyReportExport {
            failJourneyReportExport(export, code: "NATIVE_REPORT_EXPORT_UNAUTHENTICATED")
        }
        cancelPendingAuthorization(message: "La autenticación se canceló.", code: "NATIVE_AUTH_CANCELLED")
        stopAllEvents()
        advanceWidgetGeneration()

        var localClearSucceeded = true
        do { try credentials.clearAll() } catch { localClearSucceeded = false }
        do { try widgetCache?.clear() } catch { localClearSucceeded = false }
        reloadWidgetTimelines()
        NativeWidgetBackgroundRefresh.scheduleIfEnabled()

        if let session, let http { http.logout(session) }
        if let credential, let http {
            _ = http.sendWidgetRevocation(grantID: credential.grantID, token: credential.widgetToken) { _ in }
        }
        if localClearSucceeded {
            if responseMode {
                Self.resolveResponse(call, status: 200, contentType: "application/json", body: "{\"ok\":true}")
            } else {
                call.resolve()
            }
        } else if responseMode {
            Self.resolveResponse(call, status: 500, contentType: "application/json",
                                 body: "{\"detail\":\"No se pudo borrar la caché del widget.\"}")
        } else {
            call.reject("No se pudo borrar la caché del widget.", "NATIVE_WIDGET_CACHE")
        }
    }

    private func performClearWidget(_ call: CAPPluginCall) {
        let credential = runtimeConfig.flatMap { credentials.credential(apiOrigin: $0.apiOrigin) }
        advanceWidgetGeneration()
        var cleared = true
        do { try credentials.clearAll() } catch { cleared = false }
        do { try widgetCache?.clear() } catch { cleared = false }
        reloadWidgetTimelines()
        NativeWidgetBackgroundRefresh.scheduleIfEnabled()
        if let credential, let http {
            _ = http.sendWidgetRevocation(grantID: credential.grantID, token: credential.widgetToken) { _ in }
        }
        if cleared { call.resolve() }
        else { call.reject("No se pudo borrar la caché del widget.", "NATIVE_WIDGET_CACHE") }
    }

    private func cancelPendingAuthorization(message: String, code: String) {
        guard let pending = pendingAuthorization else { return }
        pendingAuthorization = nil
        pending.webSession?.cancel()
        pending.request?.cancel()
        pending.call.reject(message, code)
    }

    private func openEvents(config: NativeRuntimeConfig, session: NativeSessionState.Snapshot,
                            subscriptionID: String, generation: UInt64) {
        activeEvents?.cancel()
        let stream = NativeEventStream(config: config, session: session, subscriptionID: subscriptionID,
                                       isCurrent: { [weak self] in
                                           self?.sessions.isCurrent(session.generation) ?? false
                                       },
                                       onRevision: { [weak self] revision, id in
                                           self?.onMain {
                                               guard let self, self.isCurrentEvent(id, generation: generation) else { return }
                                               self.notifyListeners("pulseRevision", data: [
                                                "revision": revision,
                                                "subscriptionId": id
                                               ])
                                           }
                                       },
                                       onError: { [weak self] id in
                                           self?.onMain {
                                               guard let self, self.isCurrentEvent(id, generation: generation) else { return }
                                               self.eventsDesired = false
                                               self.activeSubscriptionID = nil
                                               self.activeEvents = nil
                                               self.notifyListeners("pulseStreamError", data: ["subscriptionId": id])
                                           }
                                       })
        activeEvents = stream
        stream.start()
    }

    private func isCurrentEvent(_ subscriptionID: String, generation: UInt64) -> Bool {
        eventsDesired && !eventsPaused && activeSubscriptionID == subscriptionID && eventGeneration == generation
    }

    private func pauseEvents() {
        guard eventsDesired, activeSubscriptionID != nil else { return }
        eventsPaused = true
        eventGeneration = Self.nextGeneration(eventGeneration)
        stopActiveEvents()
    }

    private func resumeEventsAndRefreshWidget() {
        if eventsDesired, eventsPaused, let subscriptionID = activeSubscriptionID,
           let config = runtimeConfig, let session = sessions.snapshot() {
            eventsPaused = false
            eventGeneration = Self.nextGeneration(eventGeneration)
            openEvents(config: config, session: session,
                       subscriptionID: subscriptionID, generation: eventGeneration)
        } else if eventsDesired, eventsPaused, let subscriptionID = activeSubscriptionID {
            eventsDesired = false
            eventsPaused = false
            activeSubscriptionID = nil
            notifyListeners("pulseStreamError", data: ["subscriptionId": subscriptionID])
        }
        refreshWidgetSnapshot()
    }

    private func stopActiveEvents() {
        activeEvents?.cancel()
        activeEvents = nil
    }

    private func stopAllEvents() {
        eventsDesired = false
        eventsPaused = false
        activeSubscriptionID = nil
        eventGeneration = Self.nextGeneration(eventGeneration)
        stopActiveEvents()
    }

    private func refreshWidgetSnapshot() {
        guard let config = runtimeConfig, let http, let cache = widgetCache,
              credentials.cacheEnabled(apiOrigin: config.apiOrigin),
              // A new request retires older foreground/background responses under the same lock.
              let credential = try? credentials.advanceGeneration(apiOrigin: config.apiOrigin) else {
            if let config = runtimeConfig, let cache = widgetCache {
                let cleared = try? credentials.clearSnapshotIfUnavailable(apiOrigin: config.apiOrigin) {
                    try cache.clear()
                }
                if cleared == true { reloadWidgetTimelines() }
            }
            NativeWidgetBackgroundRefresh.scheduleIfEnabled()
            return
        }
        let operationGeneration = widgetGeneration
        _ = http.sendWidgetSnapshot(grantID: credential.grantID, token: credential.widgetToken) { [weak self] result in
            self?.onMain {
                guard let self, self.widgetGeneration == operationGeneration else { return }
                guard case .success(let response) = result else { return }
                guard self.credentials.isCurrent(credential, apiOrigin: config.apiOrigin) else { return }
                if [401, 403, 404].contains(response.status) {
                    do {
                        guard try self.credentials.clearGrant(
                            ifCurrent: credential, apiOrigin: config.apiOrigin,
                            afterClear: {
                                do { try cache.clear() } catch { }
                            }
                        ) else { return }
                    } catch { return }
                    self.advanceWidgetGeneration()
                    self.reloadWidgetTimelines()
                    NativeWidgetBackgroundRefresh.scheduleIfEnabled()
                    return
                }
                guard response.status == 200, response.contentType == "application/json" else {
                    if response.status == 200 {
                        do {
                            let cleared = try self.credentials.withCurrent(
                                credential, apiOrigin: config.apiOrigin, requireCacheEnabled: true
                            ) { try cache.clear() }
                            if cleared { self.reloadWidgetTimelines() }
                        } catch { }
                    }
                    return
                }
                let now = Date()
                guard let sanitized = try? WidgetSnapshotPolicy.sanitize(
                    data: response.body, expectedProfileID: credential.profileID, now: now
                ), let visible = WidgetSnapshotPolicy.visibleSnapshot(sanitized, now: now) else {
                    do {
                        let cleared = try self.credentials.withCurrent(
                            credential, apiOrigin: config.apiOrigin, requireCacheEnabled: true
                        ) { try cache.clear() }
                        if cleared { self.reloadWidgetTimelines() }
                    } catch { }
                    return
                }
                guard self.widgetGeneration == operationGeneration else { return }
                do {
                    let wrote = try self.credentials.withCurrent(
                        credential, apiOrigin: config.apiOrigin, requireCacheEnabled: true
                    ) { try cache.write(snapshot: visible) }
                    if wrote { self.reloadWidgetTimelines() }
                } catch {
                    do {
                        let disabled = try self.credentials.disableCache(
                            ifCurrent: credential, apiOrigin: config.apiOrigin,
                            afterDisable: {
                                do { try cache.clear() } catch { }
                            }
                        )
                        if disabled {
                            self.advanceWidgetGeneration()
                            self.reloadWidgetTimelines()
                        }
                    } catch { }
                }
            }
        }
    }

    private func clearWidgetSnapshot() {
        guard let cache = widgetCache else { return }
        try? cache.clear()
        reloadWidgetTimelines()
    }

    private func reloadWidgetTimelines() {
        WidgetCenter.shared.reloadTimelines(ofKind: "PulseWidget")
    }

    private func advanceWidgetGeneration() {
        widgetGeneration = Self.nextGeneration(widgetGeneration)
    }

    static func readRuntimeConfig() -> NativeRuntimeConfig? {
        guard let apiOrigin = bundleString("PulseAPIOrigin"),
              let issuer = bundleString("PulseOIDCIssuer"),
              let authorizationEndpoint = bundleString("PulseAuthorizationEndpoint"),
              let tokenEndpoint = bundleString("PulseTokenEndpoint"),
              let clientID = bundleString("PulseClientID"),
              let audience = bundleString("PulseAudience"),
              let callback = bundleString("PulseCallback") else { return nil }
        return NativeRuntimeConfig(apiOrigin: apiOrigin, issuer: issuer,
                                   authorizationEndpoint: authorizationEndpoint,
                                   tokenEndpoint: tokenEndpoint, clientID: clientID,
                                   audience: audience, callback: callback)
    }

    static func readAppGroup() -> String? {
        bundleString("PulseAppGroup")
    }

    private static func bundleString(_ key: String) -> String? {
        guard let value = Bundle.main.object(forInfoDictionaryKey: key) as? String,
              !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return value
    }

    private static func authorizationURL(config: NativeRuntimeConfig, purpose: AuthorizationPurpose,
                                         transaction: NativeOAuthTransaction) -> URL? {
        guard var components = URLComponents(url: config.authorizationEndpoint, resolvingAgainstBaseURL: false) else {
            return nil
        }
        components.queryItems = [
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "client_id", value: config.clientID),
            URLQueryItem(name: "redirect_uri", value: config.callback.absoluteString),
            URLQueryItem(name: "scope", value: purpose.scopes),
            URLQueryItem(name: "state", value: transaction.state),
            URLQueryItem(name: "code_challenge", value: transaction.challenge),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "audience", value: config.audience)
        ]
        return components.url
    }

    private static func callbackValues(_ callback: URL, config: NativeRuntimeConfig?) -> (code: String, state: String)? {
        guard let config,
              callback.scheme?.lowercased() == "https",
              callback.host?.lowercased() == config.callback.host?.lowercased(),
              callback.port == config.callback.port,
              callback.path == config.callback.path,
              callback.user == nil, callback.password == nil, callback.fragment == nil,
              let items = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems else { return nil }
        let codes = items.filter { $0.name == "code" }
        let states = items.filter { $0.name == "state" }
        guard codes.count == 1, states.count == 1,
              let code = codes[0].value, !code.isEmpty, code.utf8.count <= 4_096,
              let state = states[0].value,
              !items.contains(where: { $0.name == "error" }) else { return nil }
        return (code, state)
    }

    private static func sessionCookie(_ headers: [AnyHashable: Any], expectedTTL: Int) -> String? {
        let values = headers.filter { String(describing: $0.key).lowercased() == "set-cookie" }.map(\.value)
        guard values.count == 1 else { return nil }
        let rawValues: [String]
        if let value = values[0] as? String { rawValues = [value] }
        else if let value = values[0] as? [String] { rawValues = value }
        else { return nil }
        guard rawValues.count == 1, let raw = rawValues.first, raw.utf8.count <= 512,
              !raw.contains(",") else { return nil }
        let parts = raw.split(separator: ";", omittingEmptySubsequences: false).map {
            $0.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        guard parts.count >= 4,
              let cookieMatch = parts[0].range(of: "^__Host-pulse_session=([A-Za-z0-9_-]{43})$", options: .regularExpression) else {
            return nil
        }
        let cookieValue = String(parts[0][cookieMatch]).components(separatedBy: "=").last ?? ""
        var secure = false
        var httpOnly = false
        var path = false
        var sameSite = false
        var maxAge: Int?
        for attribute in parts.dropFirst() {
            let lower = attribute.lowercased()
            if lower == "secure", !secure { secure = true }
            else if lower == "httponly", !httpOnly { httpOnly = true }
            else if lower == "path=/", !path { path = true }
            else if lower == "samesite=lax", !sameSite { sameSite = true }
            else if lower.hasPrefix("max-age="), maxAge == nil,
                    let value = Int(attribute.dropFirst("max-age=".count)), value == expectedTTL {
                maxAge = value
            } else {
                return nil
            }
        }
        guard secure, httpOnly, path, sameSite, maxAge == expectedTTL,
              NativeSessionState.validCookie(cookieValue) else { return nil }
        return cookieValue
    }

    private static func integer(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        return Int(number.stringValue)
    }

    private static func validAccessToken(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 4_096
            && !value.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains)
    }

    private static func validDeviceName(_ value: String) -> Bool {
        let name = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name.count <= 60 else { return false }
        return name.unicodeScalars.allSatisfy {
            !CharacterSet.controlCharacters.contains($0) && $0.properties.generalCategory != .format
        }
    }

    private static func validGrant(grantID: String, profileID: String, token: String, expiresAt: Date) -> Bool {
        grantID.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil
            && profileID.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil
            && token.range(of: "^pw_[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil
            && expiresAt > Date()
            && expiresAt.timeIntervalSinceNow <= 30 * 24 * 60 * 60
    }

    private static func parseDate(_ value: String) -> Date? {
        let regular = ISO8601DateFormatter()
        regular.formatOptions = [.withInternetDateTime]
        if let date = regular.date(from: value) { return date }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: value)
    }

    private static func parseSubscriptionID(_ value: String) -> Int64? {
        guard value.range(of: "^[1-9][0-9]{0,15}$", options: .regularExpression) != nil,
              let number = Int64(value), number <= 9_007_199_254_740_991 else { return nil }
        return number
    }

    private static func nextGeneration(_ value: UInt64) -> UInt64 {
        value == UInt64.max ? 1 : value + 1
    }

    private static func resolveResponse(_ call: CAPPluginCall, status: Int, contentType: String, body: String) {
        call.resolve(["status": status, "contentType": contentType, "body": body])
    }

    private func onMain(_ work: @escaping () -> Void) {
        if Thread.isMainThread { work() }
        else { DispatchQueue.main.async(execute: work) }
    }
}

private final class PendingNativeRead {
    var handle: NativeHTTPRequestHandle?
    private(set) var isCancelled = false

    func cancel() {
        guard !isCancelled else { return }
        isCancelled = true
        handle?.cancel()
    }
}

private final class PendingJourneyReportExport {
    let call: CAPPluginCall
    let days: Int
    let session: NativeSessionState.Snapshot
    var request: NativeHTTPRequestHandle?
    var temporaryFileURL: URL?
    var picker: UIDocumentPickerViewController?
    var pickerDelegate: NativeJourneyReportPickerDelegate?

    init(call: CAPPluginCall, days: Int, session: NativeSessionState.Snapshot) {
        self.call = call
        self.days = days
        self.session = session
    }
}

private final class NativeJourneyReportPickerDelegate: NSObject, UIDocumentPickerDelegate {
    var onPicked: (([URL]) -> Void)?
    var onCancelled: (() -> Void)?

    func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
        onPicked?(urls)
    }

    func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
        onCancelled?()
    }

    func clearCallbacks() {
        onPicked = nil
        onCancelled = nil
    }
}

private struct NativeOAuthTransaction {
    let state: String
    let verifier: String
    let challenge: String
    private let expectedGeneration: UInt64
    private let startedAt: TimeInterval
    private var consumed = false

    static func begin(expectedGeneration: UInt64) throws -> NativeOAuthTransaction {
        let state = try randomURLToken()
        let verifier = try randomURLToken()
        let digest = SHA256.hash(data: Data(verifier.utf8))
        return NativeOAuthTransaction(state: state, verifier: verifier,
                                      challenge: Data(digest).base64URL,
                                      expectedGeneration: expectedGeneration,
                                      startedAt: ProcessInfo.processInfo.systemUptime)
    }

    mutating func consume(state returnedState: String, now: TimeInterval,
                          currentGeneration: UInt64) -> Bool {
        guard !consumed, now >= startedAt, now - startedAt <= 5 * 60,
              currentGeneration == expectedGeneration, Self.constantTimeEqual(state, returnedState) else { return false }
        consumed = true
        return true
    }

    private static func constantTimeEqual(_ left: String, _ right: String) -> Bool {
        let lhs = Array(left.utf8)
        let rhs = Array(right.utf8)
        guard lhs.count == rhs.count else { return false }
        var difference: UInt8 = 0
        for index in lhs.indices { difference |= lhs[index] ^ rhs[index] }
        return difference == 0
    }

    private static func randomURLToken() throws -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        let status = bytes.withUnsafeMutableBytes { buffer in
            SecRandomCopyBytes(kSecRandomDefault, buffer.count, buffer.baseAddress!)
        }
        guard status == errSecSuccess else { throw NativeWidgetCredentialError.randomUnavailable }
        return Data(bytes).base64URL
    }
}

private extension Data {
    var base64URL: String {
        base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
