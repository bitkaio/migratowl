# Official Python image on Debian bookworm: Python 3.13 (+ pip, venv) in /usr/local.
# Keep in sync with MIGRATOWL_SANDBOX_PYTHON_VERSION (migratowl/config.py).
FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# ── System deps (heaviest / most-cached layer) ──────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential unzip \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js 22 LTS via NodeSource ───────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/nodesource_setup.sh \
    && echo "575583bbac2fccc0b5edd0dbc03e222d9f9dc8d724da996d22754d6411104fd1  /tmp/nodesource_setup.sh" | sha256sum -c - \
    && bash /tmp/nodesource_setup.sh \
    && rm /tmp/nodesource_setup.sh \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Go 1.27 ─────────────────────────────────────────────────────────────
# Checksums: https://go.dev/dl/?mode=json
ARG GO_VERSION=1.27.1
ARG GO_SHA256_AMD64=63d339f0da5ab53635a56f2490a7984dfe12dfcff22ad749f63edaf590168445
ARG GO_SHA256_ARM64=3450b45a3f9ee8568792736a5c5e70a1f2e9b36c35a8f74958c03e51d7d92bec
RUN ARCH="$(dpkg --print-architecture)" \
    && case "$ARCH" in \
         amd64) GO_SHA256="$GO_SHA256_AMD64" ;; \
         arm64) GO_SHA256="$GO_SHA256_ARM64" ;; \
         *) echo "Unsupported arch: $ARCH" >&2; exit 1 ;; \
       esac \
    && curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-${ARCH}.tar.gz" -o /tmp/go.tar.gz \
    && echo "${GO_SHA256}  /tmp/go.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/go.tar.gz -C /usr/local \
    && rm /tmp/go.tar.gz
ENV PATH="/usr/local/go/bin:/home/user/go/bin:${PATH}"

# ── Java 21 (Eclipse Temurin), Maven, Gradle ────────────────────────────
# Checksums: https://api.adoptium.net/v3/assets/latest/21/hotspot,
# apache-maven-*-bin.tar.gz.sha512, gradle-*-bin.zip.sha256
# Release tag URL-encoded (+ → %2B): jdk-21.0.12.1+1
ARG JDK_RELEASE=jdk-21.0.12.1%2B1
ARG JDK_FILE_VERSION=21.0.12.1_1
ARG JDK_SHA256_AMD64=ce79869e1307ed8ee1e2baa86a412b1eb5b75d10a01006d788a6f968bcfaee94
ARG JDK_SHA256_ARM64=23e37e026f12f3e706f18938ff611db3032d075b09d0879a25d06718c773e223
RUN ARCH="$(dpkg --print-architecture)" \
    && case "$ARCH" in \
         amd64) JDK_ARCH=x64; JDK_SHA256="$JDK_SHA256_AMD64" ;; \
         arm64) JDK_ARCH=aarch64; JDK_SHA256="$JDK_SHA256_ARM64" ;; \
         *) echo "Unsupported arch: $ARCH" >&2; exit 1 ;; \
       esac \
    && curl -fsSL "https://github.com/adoptium/temurin21-binaries/releases/download/${JDK_RELEASE}/OpenJDK21U-jdk_${JDK_ARCH}_linux_hotspot_${JDK_FILE_VERSION}.tar.gz" -o /tmp/jdk.tar.gz \
    && echo "${JDK_SHA256}  /tmp/jdk.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/java \
    && tar -xzf /tmp/jdk.tar.gz -C /opt/java --strip-components=1 \
    && rm /tmp/jdk.tar.gz

ARG MAVEN_VERSION=3.9.16
ARG MAVEN_SHA512=831a8591fe20c8243b1dbe7d71e3244f31d1665b0804b2e825e38cbbe5ce0cafb8338851f90780735568773e0a6cd07bbec107cda0b896b008b861075358b6f6
RUN curl -fsSL "https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/${MAVEN_VERSION}/apache-maven-${MAVEN_VERSION}-bin.tar.gz" -o /tmp/maven.tar.gz \
    && echo "${MAVEN_SHA512}  /tmp/maven.tar.gz" | sha512sum -c - \
    && mkdir -p /opt/maven \
    && tar -xzf /tmp/maven.tar.gz -C /opt/maven --strip-components=1 \
    && rm /tmp/maven.tar.gz

ARG GRADLE_VERSION=9.8.0
ARG GRADLE_SHA256=bafd5ce9cfaea0fbccfdc8439a1ac42fbd4cd9c89dc9a988228d8a2639a58e6c
RUN curl -fsSL "https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip" -o /tmp/gradle.zip \
    && echo "${GRADLE_SHA256}  /tmp/gradle.zip" | sha256sum -c - \
    && unzip -q /tmp/gradle.zip -d /opt \
    && mv "/opt/gradle-${GRADLE_VERSION}" /opt/gradle \
    && rm /tmp/gradle.zip
ENV JAVA_HOME="/opt/java" \
    PATH="/opt/java/bin:/opt/maven/bin:/opt/gradle/bin:${PATH}"

# ── Workspace + non-root user dirs ──────────────────────────────────────
RUN groupadd -g 1000 user \
    && useradd -m -u 1000 -g 1000 user \
    && mkdir -p /home/user/workspace /home/user/go \
    && chown -R 1000:1000 /home/user

# ── Rust (installed as uid 1000) ────────────────────────────────────────
USER 1000
ENV CARGO_HOME="/home/user/.cargo"
ENV RUSTUP_HOME="/home/user/.rustup"
ENV PATH="${CARGO_HOME}/bin:${PATH}"
# Pinned rustup-init binary; checksums from .../rustup-init.sha256
ARG RUSTUP_VERSION=1.29.1
ARG RUSTUP_SHA256_AMD64=dda7234360b7f578ca8b0ddcb80145646fa61a67c1720a5abc7051b35c9fcb71
ARG RUSTUP_SHA256_ARM64=15f6e4ce9f583b929c996c91562bad6d4454f3281de858b02cdfdef615fac433
RUN case "$(dpkg --print-architecture)" in \
         amd64) RUST_TARGET=x86_64-unknown-linux-gnu; RUSTUP_SHA256="$RUSTUP_SHA256_AMD64" ;; \
         arm64) RUST_TARGET=aarch64-unknown-linux-gnu; RUSTUP_SHA256="$RUSTUP_SHA256_ARM64" ;; \
         *) echo "Unsupported arch" >&2; exit 1 ;; \
       esac \
    && curl --proto '=https' --tlsv1.2 -fsSL "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/${RUST_TARGET}/rustup-init" -o /tmp/rustup-init \
    && echo "${RUSTUP_SHA256}  /tmp/rustup-init" | sha256sum -c - \
    && chmod +x /tmp/rustup-init \
    && /tmp/rustup-init -y --default-toolchain stable --profile minimal \
    && rm /tmp/rustup-init

# ── Runtime server (FastAPI) ────────────────────────────────────────────
USER root
WORKDIR /app
COPY requirements.txt requirements.lock.txt ./
RUN pip install --no-cache-dir --break-system-packages --require-hashes -r requirements.lock.txt

COPY main.py .
RUN chown -R 1000:1000 /app

USER 1000
ENV GOPATH="/home/user/go"

EXPOSE 8888

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888"]
