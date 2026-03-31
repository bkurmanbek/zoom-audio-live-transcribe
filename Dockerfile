FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PULSE_RUNTIME_PATH=/tmp/pulse-zoom \
    SKIP_DEPS=1

# System deps: Xvfb, PulseAudio, PortAudio, Python 3.11
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev \
        xvfb x11-utils \
        pulseaudio pulseaudio-utils \
        portaudio19-dev libportaudio2 \
        libasound2 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 \
        libcairo2 libcups2 libdbus-1-3 libdrm2 libegl1 \
        libfontconfig1 libfreetype6 libgbm1 libglib2.0-0 \
        libgtk-3-0 libnspr4 libnss3 libpango-1.0-0 \
        libx11-6 libx11-xcb1 libxcb1 libxcomposite1 \
        libxdamage1 libxext6 libxfixes3 libxrandr2 libxshmfence1 \
        fonts-liberation fonts-noto-color-emoji fonts-ipafont-gothic \
        fonts-tlwg-loma-otf fonts-wqy-zenhei ttf-ubuntu-font-family \
        ttf-unifont xfonts-scalable xfonts-cyrillic \
        procps && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Install Playwright Chromium + its OS deps
RUN python3 -m playwright install chromium --with-deps

# Copy source
COPY src/ src/
COPY start.sh stop.sh ./
COPY .env.example .env.example
RUN chmod +x start.sh stop.sh

# Recordings go here (mount a volume to persist them)
RUN mkdir -p /app/recordings
WORKDIR /app/recordings

# PulseAudio runtime dir
RUN mkdir -p /tmp/pulse-zoom

ENTRYPOINT ["/app/start.sh"]
