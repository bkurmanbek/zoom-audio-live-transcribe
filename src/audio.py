"""AudioCapture: reads PCM frames from a PulseAudio monitor source via PULSE_SOURCE."""
import os
import time
import wave
import threading
import logging
from typing import Callable, Optional

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE  = 48000
CHANNELS     = 2
BLOCK_SIZE   = 4096
FIND_TIMEOUT = 20   # seconds to wait for the monitor source to appear in pactl


class AudioCapture:
    def __init__(
        self,
        source_name: str,
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
        block_size: int = BLOCK_SIZE,
        record_path: Optional[str] = None,
    ) -> None:
        self.source_name = source_name
        self.sample_rate = sample_rate
        self.channels    = channels
        self.block_size  = block_size
        self._record_path = record_path
        self._wav: Optional[wave.Wave_write] = None
        self._stream     = None
        self._level      = 0.0
        self._lock       = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, on_audio: Optional[Callable[[np.ndarray], None]] = None) -> "AudioCapture":
        import sounddevice as sd

        self._wait_for_pa_source()

        # Point PortAudio's PulseAudio backend at our specific monitor source
        os.environ["PULSE_SOURCE"] = self.source_name
        log.info("PULSE_SOURCE set to: %s", self.source_name)

        if self._record_path:
            self._wav = wave.open(self._record_path, "wb")
            self._wav.setnchannels(self.channels)
            self._wav.setsampwidth(2)  # int16 = 2 bytes
            self._wav.setframerate(self.sample_rate)
            log.info("Recording to: %s", self._record_path)

        def _cb(indata: np.ndarray, frames: int, t, status) -> None:
            if status:
                log.debug("Stream status: %s", status)
            rms = float(np.sqrt(np.mean(indata ** 2)))
            with self._lock:
                # Scale: typical speech RMS ~0.02–0.1 → map to 0–1 visually
                self._level = min(rms * 15.0, 1.0)
            if self._wav is not None:
                pcm = (indata * 32767).clip(-32768, 32767).astype(np.int16)
                self._wav.writeframes(pcm.tobytes())
            if on_audio is not None:
                on_audio(indata.copy())

        self._stream = sd.InputStream(
            device="pulse",          # PulseAudio virtual device; honours PULSE_SOURCE
            channels=self.channels,
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            dtype="float32",
            callback=_cb,
        )
        self._stream.start()
        log.info(
            "Audio capture started: %s @ %dHz %dch",
            self.source_name, self.sample_rate, self.channels,
        )
        return self

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                log.debug("Error stopping audio stream: %s", e)
            finally:
                self._stream = None
        if self._wav is not None:
            try:
                self._wav.close()
                log.info("WAV recording saved: %s", self._record_path)
            except Exception as e:
                log.debug("Error closing WAV file: %s", e)
            finally:
                self._wav = None

    def get_level(self) -> float:
        with self._lock:
            return self._level

    def __enter__(self) -> "AudioCapture":
        return self.start()

    def __exit__(self, *_) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _wait_for_pa_source(self) -> None:
        """Block until the PulseAudio source is visible via pactl (or timeout)."""
        import subprocess
        deadline = time.monotonic() + FIND_TIMEOUT
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["pactl", "list", "sources", "short"],
                capture_output=True, text=True,
            )
            if self.source_name in result.stdout:
                log.info("PulseAudio source ready: %s", self.source_name)
                return
            log.debug("Waiting for PulseAudio source '%s'…", self.source_name)
            time.sleep(1.5)

        raise RuntimeError(
            f"PulseAudio source '{self.source_name}' did not appear after {FIND_TIMEOUT}s. "
            "Run 'pactl list sources short' to inspect available sources."
        )
