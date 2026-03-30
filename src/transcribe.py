"""SonioxTranscriber: streams PCM audio to Soniox v2 realtime STT."""
import asyncio
import logging
import queue
import threading
from typing import Optional

import numpy as np

from audio import CHANNELS, SAMPLE_RATE

log = logging.getLogger(__name__)


def get_soniox_config(language='en', diarization=False, language_id=False):
    """
    Build a Soniox v2 RealtimeSTTConfig.

    Soniox v2 accepts ISO 639-1 codes directly (e.g. 'en', 'ru', 'es').
    language_hints is omitted when language_id is enabled so the API can
    auto-detect without conflicting with a fixed hint.
    """
    from soniox.types.realtime import RealtimeSTTConfig

    hints = None if language_id else [language]

    return RealtimeSTTConfig(
        model="stt-rt-v4",
        audio_format="pcm_s16le",
        sample_rate=SAMPLE_RATE,
        num_channels=CHANNELS,
        language_hints=hints,
        enable_speaker_diarization=diarization or None,
        enable_language_identification=language_id or None,
    )


class SonioxTranscriber:
    """
    Streams float32 audio chunks → PCM int16 → Soniox realtime STT.

    Usage:
        t = SonioxTranscriber(api_key, get_soniox_config())
        t.start()
        # from audio callback:
        t.send_audio(float32_ndarray)
        # from display loop:
        print(t.get_transcript())
        t.stop()
    """

    def __init__(self, api_key: str, config) -> None:
        self._api_key   = api_key
        self._config    = config
        self._queue: queue.Queue[Optional[bytes]] = queue.Queue()
        self._committed = ""   # finalized tokens
        self._interim   = ""   # non-final tokens (pending)
        self._lock      = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running   = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> "SonioxTranscriber":
        self._running = True
        self._thread  = threading.Thread(
            target=self._run, daemon=True, name="soniox"
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._running = False
        self._queue.put(None)          # unblock the sender coroutine
        if self._thread:
            self._thread.join(timeout=8)

    def send_audio(self, float32_data: np.ndarray) -> None:
        """Convert float32 PCM to int16 bytes and enqueue for streaming."""
        if not self._running:
            return
        pcm = (float32_data * 32767).clip(-32768, 32767).astype(np.int16)
        self._queue.put(pcm.tobytes())

    def get_transcript(self) -> str:
        """Return the current transcript (committed + interim tokens)."""
        with self._lock:
            return self._committed + self._interim

    def get_transcript_parts(self) -> tuple:
        """Return (committed, interim) separately for richer display."""
        with self._lock:
            return self._committed, self._interim

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self) -> None:
        asyncio.run(self._stream())

    async def _stream(self) -> None:
        from soniox.client import AsyncSonioxClient

        try:
            async with AsyncSonioxClient(api_key=self._api_key) as client:
                session = client.realtime.stt.connect(config=self._config)
                async with session:
                    send = asyncio.create_task(self._send_loop(session))
                    recv = asyncio.create_task(self._recv_loop(session))
                    await asyncio.gather(send, recv, return_exceptions=True)
        except Exception as e:
            log.error("Soniox stream error: %s", e)

    async def _send_loop(self, session) -> None:
        loop = asyncio.get_running_loop()
        while True:
            chunk = await loop.run_in_executor(None, self._queue.get)
            if chunk is None:
                break
            await session.send_byte_chunk(chunk)
        await session.finish()

    async def _recv_loop(self, session) -> None:
        async for event in session.receive_events():
            if event.error_code:
                log.error("Soniox error %s: %s", event.error_code, event.error_message)
                continue
            final   = "".join(t.text for t in event.tokens if t.is_final)
            interim = "".join(t.text for t in event.tokens if not t.is_final)
            with self._lock:
                if final:
                    self._committed += final
                    # Keep last 300 chars so the terminal line stays readable
                    if len(self._committed) > 300:
                        self._committed = self._committed[-300:]
                self._interim = interim
