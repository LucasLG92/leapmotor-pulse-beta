package com.lucas.pulse.nativebridge;

import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;

/** Fails closed if the pinned AppAuth logger cannot be silenced before OAuth starts. */
final class NativeAppAuthLogging {
    private static boolean initialized;

    private NativeAppAuthLogging() {}

    static synchronized void ensureSilent() {
        if (initialized) return;
        try {
            Class<?> logger = Class.forName("net.openid.appauth.internal.Logger");
            Class<?> wrapper = Class.forName("net.openid.appauth.internal.Logger$LogWrapper");
            Object noLog = Proxy.newProxyInstance(wrapper.getClassLoader(), new Class<?>[] {wrapper},
                    (proxy, method, args) -> {
                        if ("isLoggable".equals(method.getName())) return false;
                        if ("getStackTraceString".equals(method.getName())) return "";
                        return null;
                    });
            Constructor<?> constructor = logger.getDeclaredConstructor(wrapper);
            constructor.setAccessible(true);
            Object instance = constructor.newInstance(noLog);
            Method setInstance = logger.getMethod("setInstance", logger);
            setInstance.invoke(null, instance);
            initialized = true;
        } catch (Throwable ignored) {
            // AppAuth's implementation is pinned; do not begin OAuth if its logger is not controlled.
            throw new IllegalStateException("Native authentication is unavailable.");
        }
    }
}
