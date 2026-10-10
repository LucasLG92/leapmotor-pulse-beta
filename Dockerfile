# Builders are pinned by multi-platform index digest. Only the web builder has Node.
FROM node:24.19.0-bookworm-slim@sha256:a9f5f7c91a432850b2a8a7797adf5eadb6c733ceed61167806cee7ea7fbc29df AS web-build
WORKDIR /src/apps/web
RUN npm install --global pnpm@11.25.0 --no-audit --no-fund
COPY apps/web/package.json apps/web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/web/ ./
COPY scripts/compress-assets.mjs /src/scripts/compress-assets.mjs
RUN pnpm build

# SQLite's official source is verified before compiling. Debian packages used
# for the toolchain come from one immutable Debian snapshot.
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS sqlite-build
RUN python -c 'from pathlib import Path; stamp="20261007T000000Z"; p=Path("/etc/apt/sources.list.d/debian.sources"); text=p.read_text(); text=text.replace("http://deb.debian.org/debian-security", f"https://snapshot.debian.org/archive/debian-security/{stamp}"); text=text.replace("https://deb.debian.org/debian-security", f"https://snapshot.debian.org/archive/debian-security/{stamp}"); text=text.replace("http://deb.debian.org/debian", f"https://snapshot.debian.org/archive/debian/{stamp}"); text=text.replace("https://deb.debian.org/debian", f"https://snapshot.debian.org/archive/debian/{stamp}"); p.write_text(text)'
RUN apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp/sqlite
RUN curl -fsSLo /tmp/sqlite.tar.gz https://sqlite.org/2026/sqlite-autoconf-3530400.tar.gz \
    && python -c 'from hashlib import sha3_256; from pathlib import Path; actual=sha3_256(Path("/tmp/sqlite.tar.gz").read_bytes()).hexdigest(); expected="454e45f61c6bd75b7420e7190732dea03ce6639c63ada47bbc592f67fc340338"; assert actual == expected, f"SQLite archive SHA3-256 mismatch: {actual}"'
RUN tar -xzf /tmp/sqlite.tar.gz --strip-components=1 -C /tmp/sqlite \
    && ./configure --prefix=/opt/sqlite --enable-shared --disable-static \
    && make -j1 \
    && make install \
    && install -D -m 0755 "$(readlink -f /opt/sqlite/lib/libsqlite3.so.0)" /sqlite-out/libsqlite3.so.0 \
    && test "$(grep -E '^#define SQLITE_VERSION ' sqlite3.h | cut -d '"' -f 2)" = "3.53.4" \
    && rm -rf /tmp/sqlite /tmp/sqlite.tar.gz

# Runtime artifacts remain hashed; only http-ece needs a source build.
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS python-deps
WORKDIR /build
COPY requirements-build-lock.txt ./requirements-build-lock.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    --prefix=/build-tools --only-binary=:all: --require-hashes -r requirements-build-lock.txt
COPY requirements-lock.txt ./requirements-lock.txt
RUN PYTHONPATH=/build-tools/lib/python3.12/site-packages \
    python -m pip install --no-cache-dir --disable-pip-version-check \
    --prefix=/install --only-binary=:all: --no-binary=http-ece \
    --no-build-isolation --require-hashes -r requirements-lock.txt

# Minimal runtime: Python, app files, static PWA, hashed wheels, and patched SQLite.
FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS runtime
# Apply the Trixie OpenSSL security revision from the signed frozen repository.
# APT verifies repository signatures; all three OpenSSL packages are pinned.
RUN python -c 'from pathlib import Path; stamp="20261010T000000Z"; p=Path("/etc/apt/sources.list.d/debian.sources"); text=p.read_text(); text=text.replace("http://deb.debian.org/debian-security", f"https://snapshot.debian.org/archive/debian-security/{stamp}"); text=text.replace("https://deb.debian.org/debian-security", f"https://snapshot.debian.org/archive/debian-security/{stamp}"); text=text.replace("http://deb.debian.org/debian", f"https://snapshot.debian.org/archive/debian/{stamp}"); text=text.replace("https://deb.debian.org/debian", f"https://snapshot.debian.org/archive/debian/{stamp}"); p.write_text(text)'
RUN apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends --only-upgrade \
        libssl3t64=3.5.7-1~deb13u3 openssl=3.5.7-1~deb13u3 \
        openssl-provider-legacy=3.5.7-1~deb13u3 \
    && test "$(dpkg-query -W -f='${Version}' libssl3t64)" = '3.5.7-1~deb13u3' \
    && test "$(dpkg-query -W -f='${Version}' openssl)" = '3.5.7-1~deb13u3' \
    && test "$(dpkg-query -W -f='${Version}' openssl-provider-legacy)" = '3.5.7-1~deb13u3' \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    HOME=/home/pulse \
    LD_LIBRARY_PATH=/usr/local/lib \
    PULSE_DB=/data/pulse.sqlite3 \
    PULSE_BIND=127.0.0.1 \
    PULSE_PORT=8780 \
    PULSE_AUTH_MODE=local \
    PULSE_HOSTS=localhost,127.0.0.1,[::1]
WORKDIR /app
COPY --from=sqlite-build /sqlite-out/libsqlite3.so.0 /usr/local/lib/libsqlite3.so.0
COPY --from=python-deps /install/ /usr/local/
COPY pulse/ ./pulse/
COPY LICENSE THIRD-PARTY-NOTICES.md ./
COPY apps/web/THIRD-PARTY-NOTICES.md ./apps/web/THIRD-PARTY-NOTICES.md
COPY --from=web-build /src/apps/web/dist/ ./apps/web/dist/
RUN groupadd --gid 10001 pulse \
    && useradd --uid 10001 --gid 10001 --home-dir /home/pulse --create-home --shell /usr/sbin/nologin pulse \
    && install -d -o 10001 -g 10001 /data \
    && python -m pip check \
    && python -m pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.12/ensurepip \
    && python -c "import importlib.util; assert importlib.util.find_spec('pip') is None; assert importlib.util.find_spec('ensurepip') is None" \
    && python -c "import sqlite3; assert sqlite3.sqlite_version == '3.53.4', sqlite3.sqlite_version" \
    && test -s /app/apps/web/dist/index.html
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8780
HEALTHCHECK --interval=20s --timeout=3s --start-period=15s --retries=5 \
    CMD ["python", "-m", "pulse.healthcheck"]
CMD ["python", "-m", "pulse"]
