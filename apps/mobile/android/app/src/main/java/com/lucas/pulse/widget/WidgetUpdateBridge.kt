package com.lucas.pulse.widget

import android.content.Context
import androidx.glance.appwidget.updateAll
import kotlinx.coroutines.CoroutineExceptionHandler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/** Java-callable, best-effort bridge for the native app-session plugin. */
object WidgetUpdateBridge {
    private val scope = CoroutineScope(
        SupervisorJob() + Dispatchers.IO + CoroutineExceptionHandler { _, _ -> },
    )

    @JvmStatic
    fun updateAll(context: Context) {
        val applicationContext = context.applicationContext
        scope.launch {
            try {
                PulseWidget().updateAll(applicationContext)
            } catch (_: Exception) {
                // A widget refresh is best-effort; never log snapshot or launcher state.
            }
        }
    }
}
