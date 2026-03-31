# zoom-audio-capture

Capture real-time audio from a Zoom meeting on Linux and transcribe it live — no Zoom API, no screen capture.

Uses a virtual display (Xvfb) and a PulseAudio null sink so Zoom routes its audio output to a capturable monitor source. [Soniox](https://soniox.com) provides the real-time speech-to-text.

```
https://zoom.us/j/<ID> → Chromium (Playwright, Xvfb :99) → PulseAudio null sink
                                                                      ↓
                                      Python sounddevice ← monitor source
                                                  ↓
                                    live VU meter + transcript in terminal
```

## Requirements

- Ubuntu / Debian-based Linux (or Docker)
- `sudo` access (first run installs system packages via apt)
- Python 3.8+
- A [Soniox](https://soniox.com) API key (free tier available)

## Setup

```bash
git clone https://github.com/<you>/zoom-audio-capture
cd zoom-audio-capture

cp .env.example .env
# Edit .env and set SONIOX_API_KEY=<your key>
```

## Usage

```bash
# Join a meeting — installs all deps on first run
./start.sh 'https://zoom.us/j/<ID>?pwd=<PWD>'

# Stop
./stop.sh

# Skip transcription (audio + VU meter only)
python3 src/main.py 'https://zoom.us/j/<ID>' --no-transcribe

# Skip WAV recording
python3 src/main.py 'https://zoom.us/j/<ID>' --no-record

# Verbose / debug output
python3 src/main.py 'https://zoom.us/j/<ID>' --verbose

# Different language (BCP-47 code)
python3 src/main.py 'https://zoom.us/j/<ID>' --language es
```

The recording is saved as `recording_<timestamp>.wav` in the working directory.

## Docker

```bash
# Build
docker build -t zoom-audio-capture .

# Run (pass your Soniox key and mount a volume for recordings)
docker run --rm -it \
  -e SONIOX_API_KEY=your_key_here \
  -v "$(pwd)/recordings:/app/recordings" \
  zoom-audio-capture 'https://zoom.us/j/<ID>?pwd=<PWD>'

# Or use an env file
docker run --rm -it \
  --env-file .env \
  -v "$(pwd)/recordings:/app/recordings" \
  zoom-audio-capture 'https://zoom.us/j/<ID>?pwd=<PWD>'

# Without transcription
docker run --rm -it \
  -v "$(pwd)/recordings:/app/recordings" \
  zoom-audio-capture 'https://zoom.us/j/<ID>?pwd=<PWD>' --no-transcribe
```

Recordings are written to `/app/recordings` inside the container — mount a volume to persist them.

Convert to MP3:
```bash
ffmpeg -i recording_<timestamp>.wav recording.mp3
```

## How it works

| File | Role |
|------|------|
| `start.sh` | Entry point — validates URL, installs deps, runs `main.py` |
| `stop.sh` | Graceful shutdown via PID file |
| `src/deps.py` | Installs apt packages, Python deps, Playwright Chromium |
| `src/session.py` | Manages Xvfb + PulseAudio null sink + Chromium/Zoom lifecycle |
| `src/audio.py` | Reads PCM from monitor source, computes RMS, writes WAV |
| `src/transcribe.py` | Streams PCM to Soniox realtime STT, exposes committed/interim text |
| `src/main.py` | Arg parsing, signal handling, 4-line live terminal display |

## Terminal display

```
  ● REC  [████████████░░░░░░░░░░░░░░░░░░░░░░]  33%  LIVE   0:02:14
  ──────────────────────────────────────────────────────────────────
   Hello everyone, welcome to the meeting. We're going to review…
  ▶ and I think we should look at the numbers (live, dim)
```

- VU bar color: green → yellow → red
- Committed (final) text on line 3
- Live/interim text dimmed with `▶` on line 4

## Known limitations

- Meetings with waiting rooms require a host-approved account.
- Meetings set to "Only authenticated users can join" require signing in first.
- Hardcoded to Xvfb display `:99` and sink name `zoom_capture` — parallel meetings need separate instances.
- Runs fine as root; PulseAudio uses `/tmp/pulse-zoom` as runtime path.
