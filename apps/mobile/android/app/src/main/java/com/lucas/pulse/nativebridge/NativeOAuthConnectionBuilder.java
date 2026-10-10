package com.lucas.pulse.nativebridge;

import android.net.Uri;

import net.openid.appauth.connectivity.ConnectionBuilder;

import java.io.FilterInputStream;
import java.io.FilterOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.Proxy;
import java.net.URI;
import java.net.URL;
import java.security.Principal;
import java.security.cert.Certificate;
import java.util.List;
import java.util.Map;

import javax.net.ssl.HttpsURLConnection;

/** Pins AppAuth network traffic to the two configured HTTPS endpoints with bounded IO. */
final class NativeOAuthConnectionBuilder implements ConnectionBuilder {
    private static final int MAX_REQUEST_BYTES = 16 * 1024;
    private static final int MAX_RESPONSE_BYTES = 64 * 1024;
    private static final int CONNECT_TIMEOUT_MS = 3_000;
    private static final int READ_TIMEOUT_MS = 8_000;
    private final NativeRuntimeConfig config;

    NativeOAuthConnectionBuilder(NativeRuntimeConfig config) {
        this.config = config;
    }

    @Override public HttpURLConnection openConnection(Uri uri) throws IOException {
        if (uri == null || !config.isOAuthEndpoint(uri.toString())) throw new IOException("OAuth endpoint rejected");
        try {
            URL url = new URI(uri.toString()).toURL();
            HttpsURLConnection delegate = (HttpsURLConnection) url.openConnection(Proxy.NO_PROXY);
            delegate.setInstanceFollowRedirects(false);
            delegate.setConnectTimeout(CONNECT_TIMEOUT_MS);
            delegate.setReadTimeout(READ_TIMEOUT_MS);
            delegate.setUseCaches(false);
            return new BoundedHttpsConnection(url, delegate);
        } catch (Exception ignored) {
            throw new IOException("OAuth connection unavailable");
        }
    }

    private static final class BoundedHttpsConnection extends HttpsURLConnection {
        private final HttpsURLConnection delegate;

        BoundedHttpsConnection(URL url, HttpsURLConnection delegate) {
            super(url);
            this.delegate = delegate;
        }

        @Override public void connect() throws IOException { delegate.connect(); }
        @Override public void disconnect() { delegate.disconnect(); }
        @Override public boolean usingProxy() { return delegate.usingProxy(); }
        @Override public String getCipherSuite() { return delegate.getCipherSuite(); }
        @Override public Certificate[] getLocalCertificates() { return delegate.getLocalCertificates(); }
        @Override public Certificate[] getServerCertificates() throws javax.net.ssl.SSLPeerUnverifiedException {
            return delegate.getServerCertificates();
        }
        @Override public Principal getPeerPrincipal() throws javax.net.ssl.SSLPeerUnverifiedException {
            return delegate.getPeerPrincipal();
        }
        @Override public Principal getLocalPrincipal() { return delegate.getLocalPrincipal(); }
        @Override public void setRequestMethod(String method) throws java.net.ProtocolException { delegate.setRequestMethod(method); }
        @Override public String getRequestMethod() { return delegate.getRequestMethod(); }
        @Override public void setInstanceFollowRedirects(boolean follow) { delegate.setInstanceFollowRedirects(false); }
        @Override public boolean getInstanceFollowRedirects() { return false; }
        @Override public void setConnectTimeout(int timeout) { delegate.setConnectTimeout(Math.min(timeout, CONNECT_TIMEOUT_MS)); }
        @Override public int getConnectTimeout() { return delegate.getConnectTimeout(); }
        @Override public void setReadTimeout(int timeout) { delegate.setReadTimeout(Math.min(timeout, READ_TIMEOUT_MS)); }
        @Override public int getReadTimeout() { return delegate.getReadTimeout(); }
        @Override public void setUseCaches(boolean useCaches) { delegate.setUseCaches(false); }
        @Override public boolean getUseCaches() { return false; }
        @Override public void setDoInput(boolean doInput) { delegate.setDoInput(doInput); }
        @Override public boolean getDoInput() { return delegate.getDoInput(); }
        @Override public void setDoOutput(boolean doOutput) { delegate.setDoOutput(doOutput); }
        @Override public boolean getDoOutput() { return delegate.getDoOutput(); }
        @Override public void setRequestProperty(String key, String value) { delegate.setRequestProperty(key, value); }
        @Override public void addRequestProperty(String key, String value) { delegate.addRequestProperty(key, value); }
        @Override public String getRequestProperty(String key) { return delegate.getRequestProperty(key); }
        @Override public Map<String, List<String>> getRequestProperties() { return delegate.getRequestProperties(); }
        @Override public int getResponseCode() throws IOException { return delegate.getResponseCode(); }
        @Override public String getResponseMessage() throws IOException { return delegate.getResponseMessage(); }
        @Override public String getHeaderField(String name) { return delegate.getHeaderField(name); }
        @Override public String getHeaderField(int n) { return delegate.getHeaderField(n); }
        @Override public String getHeaderFieldKey(int n) { return delegate.getHeaderFieldKey(n); }
        @Override public Map<String, List<String>> getHeaderFields() { return delegate.getHeaderFields(); }
        @Override public String getContentType() { return delegate.getContentType(); }
        @Override public int getContentLength() { return delegate.getContentLength(); }
        @Override public long getContentLengthLong() { return delegate.getContentLengthLong(); }
        @Override public InputStream getInputStream() throws IOException {
            enforceDeclaredResponseSize();
            return bounded(delegate.getInputStream(), MAX_RESPONSE_BYTES);
        }
        @Override public InputStream getErrorStream() {
            if (delegate.getContentLengthLong() > MAX_RESPONSE_BYTES) return failingStream();
            InputStream stream = delegate.getErrorStream();
            return stream == null ? null : bounded(stream, MAX_RESPONSE_BYTES);
        }
        @Override public OutputStream getOutputStream() throws IOException {
            return bounded(delegate.getOutputStream(), MAX_REQUEST_BYTES);
        }

        private void enforceDeclaredResponseSize() throws IOException {
            long size = delegate.getContentLengthLong();
            if (size < -1 || size > MAX_RESPONSE_BYTES) throw new IOException("OAuth response too large");
        }

        private static InputStream bounded(InputStream input, int maxBytes) {
            return new FilterInputStream(input) {
                private int count;
                @Override public int read() throws IOException {
                    int value = super.read();
                    if (value >= 0 && ++count > maxBytes) throw new IOException("OAuth response too large");
                    return value;
                }
                @Override public int read(byte[] buffer, int offset, int length) throws IOException {
                    int allowed = Math.min(length, maxBytes + 1 - count);
                    if (allowed <= 0) throw new IOException("OAuth response too large");
                    int read = super.read(buffer, offset, allowed);
                    if (read > 0 && (count += read) > maxBytes) throw new IOException("OAuth response too large");
                    return read;
                }
            };
        }

        private static InputStream failingStream() {
            return new InputStream() {
                @Override public int read() throws IOException { throw new IOException("OAuth response too large"); }
            };
        }

        private static OutputStream bounded(OutputStream output, int maxBytes) {
            return new FilterOutputStream(output) {
                private int count;
                @Override public void write(int value) throws IOException {
                    if (++count > maxBytes) throw new IOException("OAuth request too large");
                    out.write(value);
                }
                @Override public void write(byte[] buffer, int offset, int length) throws IOException {
                    if (length < 0 || length > maxBytes - count) throw new IOException("OAuth request too large");
                    count += length;
                    out.write(buffer, offset, length);
                }
            };
        }
    }
}
