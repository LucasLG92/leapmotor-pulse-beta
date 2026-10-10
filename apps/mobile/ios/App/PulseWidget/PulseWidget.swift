import Foundation
import SwiftUI
import WidgetKit
import PulseCore

private struct PulseEntry: TimelineEntry {
    let date: Date
    let snapshot: CachedWidgetSnapshot?
}

private struct PulseProvider: TimelineProvider {
    private func stored(at date: Date) -> CachedWidgetSnapshot? {
        guard let group = Bundle.main.object(forInfoDictionaryKey: "PulseAppGroup") as? String,
              let cache = PulseWidgetCache(groupID: group) else { return nil }
        return cache.read(now: date)
    }

    func placeholder(in context: Context) -> PulseEntry {
        PulseEntry(date: Date(), snapshot: nil)
    }

    func getSnapshot(in context: Context, completion: @escaping (PulseEntry) -> Void) {
        let now = Date()
        completion(PulseEntry(date: now, snapshot: context.isPreview ? nil : stored(at: now)))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<PulseEntry>) -> Void) {
        let now = Date()
        let snapshot = stored(at: now)
        var entries = [PulseEntry(date: now, snapshot: snapshot)]
        let next = min(now.addingTimeInterval(30 * 60), snapshot?.validUntil ?? now.addingTimeInterval(30 * 60))
        if let expiry = snapshot?.validUntil, expiry > now {
            // Se pide un render sin valores al vencer. WidgetKit decide cuándo ejecutarlo.
            entries.append(PulseEntry(date: expiry, snapshot: nil))
        }
        completion(Timeline(entries: entries, policy: .after(next)))
    }
}

private struct PulseWidgetView: View {
    let entry: PulseEntry
    @Environment(\.widgetFamily) private var family
    @Environment(\.colorScheme) private var colorScheme

    private var snapshot: CachedWidgetSnapshot? {
        entry.snapshot.flatMap { WidgetSnapshotPolicy.visibleSnapshot($0, now: Date()) }
    }
    private var dark: Bool {
        snapshot?.appearance == "dark" || (snapshot?.appearance != "light" && colorScheme == .dark)
    }
    private var ink: Color { dark ? Color(red: 0.91, green: 0.95, blue: 0.92) : Color(red: 0.10, green: 0.18, blue: 0.13) }
    private var secondary: Color { dark ? Color(red: 0.69, green: 0.77, blue: 0.71) : Color(red: 0.33, green: 0.41, blue: 0.36) }
    private var accent: Color { dark ? Color(red: 0.71, green: 0.88, blue: 0.75) : Color(red: 0.14, green: 0.35, blue: 0.25) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 7) {
                Image(systemName: "waveform.path.ecg").foregroundStyle(accent).accessibilityHidden(true)
                Text("Pulse").font(.headline)
                Spacer(minLength: 0)
                if snapshot?.source == "demo" {
                    Text("Demo").font(.caption2.weight(.semibold)).foregroundStyle(secondary)
                }
            }
            if let snapshot {
                if snapshot.privacyMode == "masked" {
                    placeholder(icon: "eye.slash", title: "Privacidad activada", detail: "Abre Pulse para consultar los datos.")
                } else {
                    values(snapshot).privacySensitive()
                    Spacer(minLength: 0)
                    if snapshot.kind == "energy" {
                        if let calculated = snapshot.observedAt {
                            HStack(spacing: 4) {
                                Text("Calculado ·")
                                Text(calculated, style: .time)
                            }.font(.caption2).foregroundStyle(secondary).privacySensitive()
                        }
                        if let period = energyPeriodUTC(snapshot.energyWindow) {
                            Text("Periodo · \(period)")
                                .font(.system(size: 9)).foregroundStyle(secondary)
                                .lineLimit(2).minimumScaleFactor(0.8).privacySensitive()
                        }
                    } else if let observed = snapshot.observedAt {
                        HStack(spacing: 4) {
                            Text(entry.date.timeIntervalSince(observed) > 300 ? "Datos antiguos ·" : "Lectura ·")
                            Text(observed, style: .time)
                        }.font(.caption2).foregroundStyle(secondary).privacySensitive()
                    }
                }
            } else {
                placeholder(icon: "car.side", title: "Tu coche, a mano", detail: "Activa el widget en Ajustes de Pulse.")
            }
        }
        .foregroundStyle(ink)
        .containerBackground(for: .widget) {
            dark ? Color(red: 0.08, green: 0.14, blue: 0.10) : Color(red: 0.96, green: 0.97, blue: 0.94)
        }
        .widgetURL(URL(string: "pulse://open"))
        .accessibilityElement(children: .contain)
    }

    @ViewBuilder private func values(_ value: CachedWidgetSnapshot) -> some View {
        if family == .systemSmall {
            if let field = value.fields.first { metric(field, snapshot: value, compact: true) }
        } else {
            HStack(alignment: .top, spacing: 8) {
                ForEach(value.fields, id: \.self) { field in
                    metric(field, snapshot: value, compact: false).frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }

    private func metric(_ field: String, snapshot: CachedWidgetSnapshot, compact: Bool) -> some View {
        let label: String
        switch field {
        case "battery_pct": label = "Batería"
        case "range_km": label = "Autonomía"
        case "charging": label = "Carga"
        case "locked": label = "Cierre"
        case "energy_kwh": label = "Energía registrada · 30 días"
        case "energy_cost_eur": label = "Coste registrado · 30 días"
        default: label = "—"
        }
        let value: String = {
            if field == "battery_pct", let number = snapshot.batteryPercent { return "\(Int(number.rounded())) %" }
            if field == "range_km", let number = snapshot.rangeKilometers { return "\(Int(number.rounded())) km" }
            if field == "charging", let state = snapshot.charging { return state ? "En curso" : "Inactiva" }
            if field == "locked", let state = snapshot.locked { return state ? "Cerrado" : "Abierto" }
            if field == "energy_kwh", let number = snapshot.energyKilowattHours {
                return "\(number.formatted(.number.precision(.fractionLength(0...3)))) kWh"
            }
            if field == "energy_cost_eur", let number = snapshot.energyCostEuros {
                return number.formatted(.currency(code: "EUR").precision(.fractionLength(2)))
            }
            return "—"
        }()
        return VStack(alignment: .leading, spacing: 5) {
            Text(label).font(snapshot.kind == "energy" ? .caption2 : .caption).foregroundStyle(secondary)
                .lineLimit(2).fixedSize(horizontal: false, vertical: true)
            Text(value).font(.system(compact ? .largeTitle : .title2, design: .rounded).weight(.semibold))
                .monospacedDigit().minimumScaleFactor(0.7).lineLimit(1)
        }.accessibilityElement(children: .combine)
    }

    private func energyPeriodUTC(_ window: WidgetEnergyWindow?) -> String? {
        guard let window else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.formatOptions = [.withFullDate]
        return "\(formatter.string(from: window.startedAt)) – \(formatter.string(from: window.endedAt)) UTC"
    }

    private func placeholder(icon: String, title: String, detail: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Image(systemName: icon).foregroundStyle(secondary).accessibilityHidden(true)
            Text(title).font(.subheadline.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
            Text(detail).font(.caption).foregroundStyle(secondary).fixedSize(horizontal: false, vertical: true)
        }
    }
}

@main
struct PulseWidget: Widget {
    let kind = "PulseWidget"
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: PulseProvider()) { entry in PulseWidgetView(entry: entry) }
            .configurationDisplayName("Leapmotor Pulse")
            .description("Campos seleccionados del vehículo o energía registrada, con privacidad y fechas.")
            .supportedFamilies([.systemSmall, .systemMedium])
    }
}
