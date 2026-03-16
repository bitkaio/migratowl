FROM debian:bookworm-slim

# ── System deps (heaviest / most-cached layer) ──────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential \
        python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js 22 LTS via NodeSource ───────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
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
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --default-toolchain stable --profile minimal

# ── Runtime server (FastAPI) ────────────────────────────────────────────
USER root
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY main.py .
RUN chown -R 1000:1000 /app

USER 1000
ENV GOPATH="/home/user/go"

EXPOSE 8888

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888"]
