FROM debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

# ── System deps (heaviest / most-cached layer) ──────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential \
        python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js 22 LTS via NodeSource ───────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/nodesource_setup.sh \
    && echo "575583bbac2fccc0b5edd0dbc03e222d9f9dc8d724da996d22754d6411104fd1  /tmp/nodesource_setup.sh" | sha256sum -c - \
    && bash /tmp/nodesource_setup.sh \
    && rm /tmp/nodesource_setup.sh \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Go 1.23 ─────────────────────────────────────────────────────────────
ARG GO_VERSION=1.23.6
RUN ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-${ARCH}.tar.gz" \
    | tar -xz -C /usr/local
ENV PATH="/usr/local/go/bin:/home/user/go/bin:${PATH}"

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
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o /tmp/rustup-init.sh \
    && echo "6c30b75a75b28a96fd913a037c8581b580080b6ee9b8169a3c0feb1af7fe8caf  /tmp/rustup-init.sh" | sha256sum -c - \
    && sh /tmp/rustup-init.sh -y --default-toolchain stable --profile minimal \
    && rm /tmp/rustup-init.sh

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
