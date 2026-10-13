package com.lucas.pulse.nativebridge;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.io.IOException;
import java.io.InputStream;
import java.net.URL;
import java.security.Principal;
import java.security.cert.Certificate;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import javax.net.ssl.HttpsURLConnection;

import org.junit.Test;

public final class NativeReadCancellationTest {
    private static final String COOKIE = "a".repeat(43);
    private static final String CSRF = "b".repeat(32);

    @Test public void cancellationBeforeTransportAttachPreventsHttpDispatch() throws Exception {
        NativeHttpClient.RequestCancellation cancellation = new NativeHttpClient.RequestCancellation();
        cancellation.cancel();
        AtomicInteger opened = new AtomicInteger();
        NativeHttpClient client = client(url -> {
            opened.incrementAndGet();
            return new BlockingConnection(url, new CountDownLatch(0), new CountDownLatch(0));
        });

        assertCancelled(client, cancellation);
        assertEquals(0, opened.get());
    }

    @Test public void transportRejectsCancellationHandlesForMutations() throws Exception {
        AtomicInteger opened = new AtomicInteger();
        NativeHttpClient client = client(url -> {
            opened.incrementAndGet();
            return new BlockingConnection(url, new CountDownLatch(0), new CountDownLatch(0));
        });
        try {
            client.request("POST", "/api/charges", "{}", session().snapshot(1),
                    new NativeHttpClient.RequestCancellation());
            fail("La cancelación no debe poder asociarse a una mutación.");
        } catch (NativeHttpClient.NativeTransportException expected) {
            assertEquals(400, expected.status);
        }
        assertEquals(0, opened.get());
    }

    @Test public void cancellationDisconnectsAnInFlightResponseRead() throws Exception {
        CountDownLatch reading = new CountDownLatch(1);
        CountDownLatch disconnected = new CountDownLatch(1);
        AtomicInteger opens = new AtomicInteger();
        AtomicInteger disconnects = new AtomicInteger();
        NativeHttpClient client = client(url -> {
            opens.incrementAndGet();
            return new BlockingConnection(url, reading, disconnected, disconnects);
        });
        NativeHttpClient.RequestCancellation cancellation = new NativeHttpClient.RequestCancellation();
        NativeSessionState state = session();
        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<?> request = executor.submit(() -> {
                try {
                    client.request("GET", "/api/state", null, state.snapshot(1), cancellation);
                    fail("La lectura cancelada no debe entregar una respuesta.");
                } catch (NativeHttpClient.NativeTransportException expected) {
                    assertEquals(0, expected.status);
                }
            });
            assertTrue("La lectura debe alcanzar el cuerpo pendiente.", reading.await(2, TimeUnit.SECONDS));
            cancellation.cancel();
            cancellation.cancel();
            request.get(2, TimeUnit.SECONDS);
            assertTrue(cancellation.isCancelled());
            assertEquals(1, opens.get());
            assertTrue(disconnected.await(1, TimeUnit.SECONDS));
            assertTrue(disconnects.get() >= 1);
        } finally {
            executor.shutdownNow();
        }
    }

    private static NativeHttpClient client(NativeHttpClient.ConnectionOpener opener) {
        NativeRuntimeConfig config = NativeRuntimeConfig.fromValues(
                "https://pulse.example.test", "https://issuer.example.test",
                "https://issuer.example.test/authorize", "https://issuer.example.test/token",
                "pulse-mobile", "pulse-api", "https://pulse.example.test/native/oauth/callback");
        if (config == null) throw new AssertionError("La configuración de prueba debe ser válida.");
        return new NativeHttpClient(config, opener);
    }

    private static NativeSessionState session() {
        NativeSessionState state = new NativeSessionState();
        assertTrue(state.install(COOKIE, CSRF, 60, 0, 0));
        return state;
    }

    private static void assertCancelled(NativeHttpClient client,
            NativeHttpClient.RequestCancellation cancellation) throws Exception {
        try {
            client.request("GET", "/api/state", null, session().snapshot(1), cancellation);
            fail("La solicitud cancelada no debe alcanzar el transporte.");
        } catch (NativeHttpClient.NativeTransportException expected) {
            assertEquals(0, expected.status);
            assertFalse(expected.safeMessage.isEmpty());
        }
    }

    private static final class BlockingConnection extends HttpsURLConnection {
        private final CountDownLatch reading;
        private final CountDownLatch disconnected;
        private final AtomicInteger disconnects;

        BlockingConnection(URL url, CountDownLatch reading, CountDownLatch disconnected) {
            this(url, reading, disconnected, new AtomicInteger());
        }

        BlockingConnection(URL url, CountDownLatch reading, CountDownLatch disconnected,
                AtomicInteger disconnects) {
            super(url);
            this.reading = reading;
            this.disconnected = disconnected;
            this.disconnects = disconnects;
        }

        @Override public void disconnect() {
            disconnects.incrementAndGet();
            disconnected.countDown();
        }

        @Override public boolean usingProxy() { return false; }
        @Override public void connect() { connected = true; }
        @Override public int getResponseCode() { return 200; }
        @Override public String getContentType() { return "application/json"; }
        @Override public Map<String, List<String>> getHeaderFields() { return Collections.emptyMap(); }
        @Override public String getCipherSuite() { return "TLS_FAKE"; }
        @Override public Certificate[] getLocalCertificates() { return null; }
        @Override public Certificate[] getServerCertificates() { return new Certificate[0]; }
        @Override public Principal getPeerPrincipal() { return null; }
        @Override public Principal getLocalPrincipal() { return null; }

        @Override public InputStream getInputStream() {
            return new InputStream() {
                @Override public int read() throws IOException {
                    reading.countDown();
                    try {
                        if (!disconnected.await(2, TimeUnit.SECONDS)) throw new IOException("read timed out");
                    } catch (InterruptedException error) {
                        Thread.currentThread().interrupt();
                        throw new IOException(error);
                    }
                    throw new IOException("connection disconnected");
                }

                @Override public int read(byte[] bytes, int offset, int length) throws IOException {
                    return read();
                }
            };
        }
    }
}
