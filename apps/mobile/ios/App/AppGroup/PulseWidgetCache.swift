import Foundation
import PulseCore

/// El App Group contiene únicamente la proyección seleccionada. Ningún token.
public final class PulseWidgetCache {
    private let directory: URL
    private let file: URL
    private let manager = FileManager.default

    public init?(groupID: String) {
        guard !groupID.isEmpty, !groupID.contains("$("),
              let container = FileManager.default.containerURL(
                forSecurityApplicationGroupIdentifier: groupID) else { return nil }
        directory = container.appendingPathComponent("Library/PulseWidget", isDirectory: true)
        file = directory.appendingPathComponent("snapshot.json", isDirectory: false)
    }

    public func write(snapshot: CachedWidgetSnapshot) throws {
        let now = Date()
        guard let safe = WidgetSnapshotPolicy.visibleSnapshot(snapshot, now: now) else {
            try clear()
            return
        }
        try manager.createDirectory(at: directory, withIntermediateDirectories: true,
            attributes: [.protectionKey: FileProtectionType.complete])
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let data = try encoder.encode(Envelope(snapshot: safe, receivedAt: now,
            receivedUptime: ProcessInfo.processInfo.systemUptime))
        guard data.count <= 64 * 1024 else { throw CacheFailure.invalid }
        try data.write(to: file, options: [.atomic, .completeFileProtection])
        // También se excluye la carpeta: la copia no debe sobrevivir a reinstalaciones.
        var protectedDirectory = directory
        var protectedFile = file
        var properties = URLResourceValues()
        properties.isExcludedFromBackup = true
        try protectedDirectory.setResourceValues(properties)
        try protectedFile.setResourceValues(properties)
    }

    public func read(now: Date = Date()) -> CachedWidgetSnapshot? {
        // La protección completa produce un placeholder mientras iOS está bloqueado.
        guard let size = try? manager.attributesOfItem(atPath: file.path)[.size] as? NSNumber,
              size.intValue <= 64 * 1024,
              let data = try? Data(contentsOf: file) else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        guard let decoded = try? decoder.decode(Envelope.self, from: data) else { return nil }
        let elapsed = ProcessInfo.processInfo.systemUptime - decoded.receivedUptime
        let wallElapsed = now.timeIntervalSince(decoded.receivedAt)
        guard elapsed.isFinite, elapsed >= 0, elapsed <= 2 * 60 * 60,
              wallElapsed.isFinite, abs(wallElapsed - elapsed) <= 5 else { return nil }
        return WidgetSnapshotPolicy.visibleSnapshot(decoded.snapshot, now: now)
    }

    public func clear() throws {
        if manager.fileExists(atPath: file.path) { try manager.removeItem(at: file) }
    }

    private enum CacheFailure: Error { case invalid }

    // CachedWidgetSnapshot's Codable shape is cache v2. Its decoder accepts an
    // old record only when its fields and source prove it is a legacy vehicle
    // snapshot; missing family metadata can never become an energy snapshot.
    private struct Envelope: Codable {
        let snapshot: CachedWidgetSnapshot
        let receivedAt: Date
        let receivedUptime: TimeInterval
    }
}
