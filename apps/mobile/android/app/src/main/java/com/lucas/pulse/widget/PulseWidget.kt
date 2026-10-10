package com.lucas.pulse.widget

import android.content.ComponentName
import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.LocalSize
import androidx.glance.action.actionStartActivity
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.SizeMode
import androidx.glance.appwidget.provideContent
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.text.FontWeight as GlanceFontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider
import com.lucas.pulse.MainActivity
import java.text.DateFormat
import java.text.NumberFormat
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Stateless renderer. It reads only the local, encrypted, opt-in cache. */
class PulseWidget : GlanceAppWidget() {
    override val sizeMode = SizeMode.Responsive(
        setOf(
            DpSize(180.dp, 100.dp),
            DpSize(280.dp, 170.dp),
        ),
    )

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val display = withContext(Dispatchers.IO) { WidgetSnapshotCache(context).display() }
        val mainActivity = ComponentName(context, MainActivity::class.java)
        provideContent { PulseWidgetContent(display, mainActivity) }
    }
}

@Composable
private fun PulseWidgetContent(
    display: WidgetDisplay,
    mainActivity: ComponentName,
) {
    val palette = widgetPalette(display.appearance)
    val size = LocalSize.current
    val compact = size.width < 240.dp || size.height < 132.dp
    val sourceLabel = when {
        display.kind == "energy" -> "ENERGÍA"
        display.demo -> "DEMO"
        display.source == "telemetry" -> "TELEMETRÍA"
        display.source == "unavailable" -> "SIN DATOS"
        else -> null
    }

    Column(
        modifier = GlanceModifier
            .fillMaxSize()
            .background(palette.background)
            .clickable(actionStartActivity(mainActivity))
            .padding(horizontal = if (compact) 14.dp else 18.dp, vertical = 12.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Row(modifier = GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = GlanceModifier.defaultWeight()) {
                Text(
                    text = "PULSE",
                    style = TextStyle(
                        color = palette.primary,
                        fontSize = 13.sp,
                        fontWeight = GlanceFontWeight.Bold,
                    ),
                )
                display.profileName?.let { name ->
                    Text(
                        text = name,
                        style = TextStyle(
                            color = palette.secondary,
                            fontSize = 11.sp,
                        ),
                    )
                }
            }
            sourceLabel?.let { label ->
                Text(
                    text = label,
                    style = TextStyle(
                        color = if (display.demo) palette.accent else palette.secondary,
                        fontSize = 10.sp,
                        fontWeight = GlanceFontWeight.Bold,
                    ),
                )
            }
        }

        Spacer(modifier = GlanceModifier.height(if (compact) 8.dp else 14.dp))

        when (display.status) {
            "setup" -> Text("Abre Pulse para configurar", style = bodyStyle(palette))
            "disabled" -> Text("Widget desactivado en Pulse", style = bodyStyle(palette))
            "locked" -> Text("Desbloquea el teléfono para ver los datos", style = bodyStyle(palette))
            "masked" -> Text("Perfil enmascarado; valores ocultos", style = bodyStyle(palette))
            "unknown" -> Text("Antigüedad desconocida; valores ocultos", style = bodyStyle(palette))
            "unavailable" -> Text(
                if (display.kind == "energy") "Sin registros de energía disponibles" else "Sin datos disponibles",
                style = bodyStyle(palette),
            )
            else -> Metrics(display = display, compact = compact, palette = palette)
        }

        Spacer(modifier = GlanceModifier.defaultWeight())
        if (display.status == "fresh" || display.status == "stale") {
            if (display.kind == "energy") {
                display.observedAtMillis?.let { calculatedAt ->
                    Text(
                        text = "Calculado · ${formatUtcDateTime(calculatedAt)}",
                        style = TextStyle(color = palette.secondary, fontSize = 10.sp),
                    )
                }
                val start = display.energyWindowStartMillis
                val end = display.energyWindowEndMillis
                if (start != null && end != null) {
                    Text(
                        text = "Periodo UTC · ${formatUtcDate(start)} – ${formatUtcDate(end)}",
                        style = TextStyle(color = palette.secondary, fontSize = 10.sp),
                    )
                }
            } else {
                Text(
                    text = if (compact && display.status == "stale") {
                        "Antiguos · frescura física desconocida"
                    } else if (compact) {
                        "Recientes · frescura física desconocida"
                    } else if (display.status == "stale") {
                        "Datos antiguos · frescura física desconocida"
                    } else {
                        "Datos recientes · frescura física desconocida"
                    },
                    style = TextStyle(
                        color = palette.secondary,
                        fontSize = 10.sp,
                    ),
                )
                display.observedAtMillis?.let { observedAt ->
                    val observedText = DateFormat.getDateTimeInstance(
                        DateFormat.SHORT,
                        DateFormat.SHORT,
                        Locale.getDefault(),
                    ).format(Date(observedAt))
                    Text(
                        text = "Observado $observedText",
                        style = TextStyle(
                            color = palette.secondary,
                            fontSize = 10.sp,
                        ),
                    )
                }
            }
        }
    }
}

@Composable
private fun Metrics(display: WidgetDisplay, compact: Boolean, palette: WidgetPalette) {
    val fields = display.selectedFieldOrder.filter { it in display.selectedFields }
        .take(WidgetPolicy.MAX_SELECTED_FIELDS)
    if (fields.isEmpty()) {
        Text("No hay campos seleccionados", style = bodyStyle(palette))
        return
    }

    if (display.kind == "energy") {
        Text("Energía registrada · 30 días", style = bodyStyle(palette))
        if (compact) {
            val field = fields.first()
            Metric(energyLabel(field), energyValue(field, display), palette, compact = true)
        } else {
            Row(verticalAlignment = Alignment.CenterVertically) {
                fields.forEach { field ->
                    Metric(
                        energyLabel(field),
                        energyValue(field, display),
                        palette = palette,
                        compact = true,
                        modifier = GlanceModifier.defaultWeight(),
                    )
                }
            }
        }
        return
    }

    if (compact) {
        val field = fields.first()
        Metric(vehicleLabel(field), vehicleValue(field, display), palette, compact = true)
    } else {
        Row(verticalAlignment = Alignment.CenterVertically) {
            fields.forEach { field ->
                Metric(
                    vehicleLabel(field),
                    vehicleValue(field, display),
                    palette = palette,
                    compact = true,
                    modifier = GlanceModifier.defaultWeight(),
                )
            }
        }
    }
}

private fun vehicleLabel(field: String): String = when (field) {
    "battery_pct" -> "Batería"
    "range_km" -> "Autonomía"
    "charging" -> "Carga"
    "locked" -> "Cierre"
    else -> "—"
}

private fun vehicleValue(field: String, display: WidgetDisplay): String = when (field) {
    "battery_pct" -> display.batteryPct?.let { "${formatNumber(it, 1)}%" } ?: "—"
    "range_km" -> display.rangeKm?.let { "${formatNumber(it, 1)} km" } ?: "—"
    "charging" -> display.charging?.let(::chargingLabel) ?: "—"
    "locked" -> display.locked?.let { if (it) "Cerrado" else "Abierto" } ?: "—"
    else -> "—"
}

private fun energyLabel(field: String): String = when (field) {
    "energy_kwh" -> "Energía"
    "energy_cost_eur" -> "Coste registrado"
    else -> "—"
}

private fun energyValue(field: String, display: WidgetDisplay): String = when (field) {
    "energy_kwh" -> display.energyKwh?.let { "${formatNumber(it, 3)} kWh" } ?: "—"
    "energy_cost_eur" -> display.energyCostEur?.let { "${formatNumber(it, 2)} €" } ?: "—"
    else -> "—"
}

@Composable
private fun Metric(
    label: String,
    value: String,
    palette: WidgetPalette,
    compact: Boolean = false,
    modifier: GlanceModifier = GlanceModifier,
) {
    Column(
        modifier = modifier.padding(bottom = if (compact) 0.dp else 5.dp),
    ) {
        Text(
            text = label,
            style = TextStyle(
                color = palette.secondary,
                fontSize = 10.sp,
            ),
        )
        Text(
            text = value,
            style = TextStyle(
                color = palette.primary,
                fontSize = when {
                    value.length > 14 -> 11.sp
                    value.length > 11 -> 13.sp
                    compact -> 16.sp
                    else -> 18.sp
                },
                fontWeight = GlanceFontWeight.Bold,
            ),
        )
    }
}

@Composable
private fun bodyStyle(palette: WidgetPalette) = TextStyle(
    color = palette.primary,
    fontSize = 13.sp,
    fontWeight = GlanceFontWeight.Bold,
)

private data class WidgetPalette(
    val background: ColorProvider,
    val primary: ColorProvider,
    val secondary: ColorProvider,
    val accent: ColorProvider,
)

private fun widgetPalette(appearance: String): WidgetPalette {
    // API31+ keeps day/night alternatives in RemoteViews for host theme changes.
    // Older Android resolves a color when the widget is rendered again.
    fun adaptiveColor(day: Color, night: Color): ColorProvider = when (appearance) {
        "light" -> ColorProvider(day)
        "dark" -> ColorProvider(night)
        else -> androidx.glance.color.ColorProvider(day, night)
    }
    return WidgetPalette(
        background = adaptiveColor(Color(0xFFF6F7F2), Color(0xFF17211D)),
        primary = adaptiveColor(Color(0xFF18352B), Color(0xFFF1F5EF)),
        secondary = adaptiveColor(Color(0xFF53665C), Color(0xFFB9C8BF)),
        accent = adaptiveColor(Color(0xFF245841), Color(0xFF95D1AA)),
    )
}

private fun formatNumber(value: Double, maximumFractionDigits: Int): String =
    NumberFormat.getNumberInstance(Locale.getDefault()).apply {
        isGroupingUsed = true
        minimumFractionDigits = 0
        this.maximumFractionDigits = maximumFractionDigits
    }.format(value)

private fun chargingLabel(charging: Boolean): String = if (charging) "Cargando" else "Sin carga"

private fun formatUtcDateTime(epochMillis: Long): String =
    SimpleDateFormat("yyyy-MM-dd HH:mm 'UTC'", Locale.ROOT).apply {
        timeZone = TimeZone.getTimeZone("UTC")
    }.format(Date(epochMillis))

private fun formatUtcDate(epochMillis: Long): String =
    SimpleDateFormat("yyyy-MM-dd", Locale.ROOT).apply {
        timeZone = TimeZone.getTimeZone("UTC")
    }.format(Date(epochMillis))

class PulseWidgetReceiver : androidx.glance.appwidget.GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = PulseWidget()
}
