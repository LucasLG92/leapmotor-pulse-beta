package com.lucas.pulse;

import com.getcapacitor.BridgeActivity;
import android.os.Bundle;
import com.lucas.pulse.nativebridge.PulseNativePlugin;

public class MainActivity extends BridgeActivity {
    @Override public void onCreate(Bundle state) {
        registerPlugin(PulseNativePlugin.class);
        super.onCreate(state);
    }
}
