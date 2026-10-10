import Foundation

public struct WidgetEnergyWindow: Codable, Sendable, Equatable {
    public let startedAt: Date
    public let endedAt: Date

    public init(startedAt: Date, endedAt: Date) {
        self.startedAt = startedAt
        self.endedAt = endedAt
    }
}

/// Minimal allowlisted data that a native widget may render from local storage.
///
/// Cache format v2 adds `kind`, `energyWindow`, and `generatedAt`. A pre-v2
/// snapshot is migrated only when its fields and source belong to the original
/// vehicle contract; old cache records can never be interpreted as energy.
public struct CachedWidgetSnapshot: Codable, Sendable, Equatable {
    private static let legacyVehicleFields: Set<String> = ["battery_pct", "range_km", "charging"]
    private static let legacyVehicleSources: Set<String> = ["demo", "telemetry", "unavailable"]

    public let profileID: String
    public let profileName: String
    public let revision: Int
    public let fields: [String]
    public let appearance: String
    public let privacyMode: String
    public let source: String
    public let maxAgeMinutes: Int
    public let observedAt: Date?
    public let validUntil: Date?
    public let batteryPercent: Double?
    public let rangeKilometers: Double?
    public let charging: Bool?
    public let locked: Bool?
    public let energyKilowattHours: Double?
    public let energyCostEuros: Double?
    public let kind: String
    public let energyWindow: WidgetEnergyWindow?
    public let generatedAt: Date?

    public init(
        profileID: String,
        profileName: String,
        revision: Int,
        fields: [String],
        appearance: String,
        privacyMode: String,
        source: String,
        observedAt: Date?,
        validUntil: Date?,
        batteryPercent: Double?,
        rangeKilometers: Double?,
        charging: Bool?,
        maxAgeMinutes: Int = 120,
        locked: Bool? = nil,
        energyKilowattHours: Double? = nil,
        energyCostEuros: Double? = nil,
        kind: String = "vehicle",
        energyWindow: WidgetEnergyWindow? = nil,
        generatedAt: Date? = nil
    ) {
        self.profileID = profileID
        self.profileName = profileName
        self.revision = revision
        self.fields = fields
        self.appearance = appearance
        self.privacyMode = privacyMode
        self.source = source
        self.maxAgeMinutes = maxAgeMinutes
        self.observedAt = observedAt
        self.validUntil = validUntil
        self.batteryPercent = batteryPercent
        self.rangeKilometers = rangeKilometers
        self.charging = charging
        self.locked = locked
        self.energyKilowattHours = energyKilowattHours
        self.energyCostEuros = energyCostEuros
        self.kind = kind
        self.energyWindow = energyWindow
        self.generatedAt = generatedAt
    }

    private enum CodingKeys: String, CodingKey {
        case profileID
        case profileName
        case revision
        case fields
        case appearance
        case privacyMode
        case source
        case maxAgeMinutes
        case observedAt
        case validUntil
        case batteryPercent
        case rangeKilometers
        case charging
        case locked
        case energyKilowattHours
        case energyCostEuros
        case kind
        case energyWindow
        case generatedAt
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        profileID = try container.decode(String.self, forKey: .profileID)
        profileName = try container.decode(String.self, forKey: .profileName)
        revision = try container.decode(Int.self, forKey: .revision)
        fields = try container.decode([String].self, forKey: .fields)
        appearance = try container.decode(String.self, forKey: .appearance)
        privacyMode = try container.decode(String.self, forKey: .privacyMode)
        source = try container.decode(String.self, forKey: .source)
        maxAgeMinutes = try container.decode(Int.self, forKey: .maxAgeMinutes)
        observedAt = try container.decodeIfPresent(Date.self, forKey: .observedAt)
        validUntil = try container.decodeIfPresent(Date.self, forKey: .validUntil)
        batteryPercent = try container.decodeIfPresent(Double.self, forKey: .batteryPercent)
        rangeKilometers = try container.decodeIfPresent(Double.self, forKey: .rangeKilometers)
        charging = try container.decodeIfPresent(Bool.self, forKey: .charging)
        locked = try container.decodeIfPresent(Bool.self, forKey: .locked)
        energyKilowattHours = try container.decodeIfPresent(Double.self, forKey: .energyKilowattHours)
        energyCostEuros = try container.decodeIfPresent(Double.self, forKey: .energyCostEuros)

        if container.contains(.kind) {
            kind = try container.decode(String.self, forKey: .kind)
            energyWindow = try container.decodeIfPresent(WidgetEnergyWindow.self, forKey: .energyWindow)
            generatedAt = try container.decodeIfPresent(Date.self, forKey: .generatedAt)
        } else {
            let isLegacyVehicle = !container.contains(.energyWindow)
                && !container.contains(.generatedAt)
                && (1...3).contains(fields.count)
                && Set(fields).count == fields.count
                && fields.allSatisfy(Self.legacyVehicleFields.contains)
                && Self.legacyVehicleSources.contains(source)
                && locked == nil
                && energyKilowattHours == nil
                && energyCostEuros == nil
            guard isLegacyVehicle else {
                throw DecodingError.dataCorruptedError(
                    forKey: .fields,
                    in: container,
                    debugDescription: "Only pre-v2 vehicle snapshots may omit the family metadata."
                )
            }
            kind = "vehicle"
            energyWindow = nil
            generatedAt = nil
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(profileID, forKey: .profileID)
        try container.encode(profileName, forKey: .profileName)
        try container.encode(revision, forKey: .revision)
        try container.encode(fields, forKey: .fields)
        try container.encode(appearance, forKey: .appearance)
        try container.encode(privacyMode, forKey: .privacyMode)
        try container.encode(source, forKey: .source)
        try container.encode(maxAgeMinutes, forKey: .maxAgeMinutes)
        try container.encodeIfPresent(observedAt, forKey: .observedAt)
        try container.encodeIfPresent(validUntil, forKey: .validUntil)
        try container.encodeIfPresent(batteryPercent, forKey: .batteryPercent)
        try container.encodeIfPresent(rangeKilometers, forKey: .rangeKilometers)
        try container.encodeIfPresent(charging, forKey: .charging)
        try container.encodeIfPresent(locked, forKey: .locked)
        try container.encodeIfPresent(energyKilowattHours, forKey: .energyKilowattHours)
        try container.encodeIfPresent(energyCostEuros, forKey: .energyCostEuros)
        try container.encode(kind, forKey: .kind)
        try container.encode(energyWindow, forKey: .energyWindow)
        try container.encode(generatedAt, forKey: .generatedAt)
    }
}
