"""Entry point: ZoomSession + AudioCapture + Soniox transcription + VU meter."""
import os
import sys
import time
import signal
import logging
import argparse
from datetime import datetime
from typing import Optional

PID_FILE = "/tmp/zoom-audio-capture.pid"

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Terminal display — 4-line in-place update
# ---------------------------------------------------------------------------

BAR_WIDTH = 36
_display_initialized = False
_start_time: Optional[float] = None
_DIVIDER = "  " + "─" * 74

# ANSI helpers
_GRN  = "\033[32m"
_YLW  = "\033[33m"
_RED  = "\033[31m"
_DIM  = "\033[2m"
_RST  = "\033[0m"


def _elapsed() -> str:
    if _start_time is None:
        return "0:00:00"
    s = int(time.time() - _start_time)
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _vu_bar(level: float) -> str:
    filled = int(level * BAR_WIDTH)
    color  = _RED if level > 0.8 else (_YLW if level > 0.5 else _GRN)
    bar    = color + "█" * filled + _RST + "░" * (BAR_WIDTH - filled)
    pct    = int(level * 100)
    status = f"{_GRN}LIVE{_RST}" if level > 0.01 else f"{_DIM}····{_RST}"
    return f"  ● REC  [{bar}] {pct:3d}%  {status}   {_elapsed()}"


def _committed_line(text: str, width: int = 74) -> str:
    if not text:
        return f"  {_DIM}(waiting for speech…){_RST}"
    display = text[-width:].lstrip() if len(text) > width else text
    prefix  = "…" if len(text) > width else " "
    return f"  {prefix}{display}"


def _interim_line(text: str, width: int = 70) -> str:
    if not text:
        return f"  {_DIM}▶{_RST}"
    display = text[:width]
    suffix  = "…" if len(text) > width else ""
    return f"  {_DIM}▶ {display}{suffix}{_RST}"


def _render(level: float, committed: str, interim: str) -> None:
    global _display_initialized, _start_time
    if _start_time is None:
        _start_time = time.time()
    vu   = _vu_bar(level)
    comm = _committed_line(committed)
    live = _interim_line(interim)
    if not _display_initialized:
        sys.stdout.write("\n" + vu + "\n" + _DIVIDER + "\n" + comm + "\n" + live)
        _display_initialized = True
    else:
        # Move up 4 lines, rewrite all
        sys.stdout.write(
            "\033[4A\r\033[2K" + vu
            + "\n\r\033[2K" + _DIVIDER
            + "\n\r\033[2K" + comm
            + "\n\r\033[2K" + live
        )
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _validate_url(url: str) -> str:
    if "zoom.us" in url:
        return url
    raise ValueError(
        f"Does not look like a Zoom URL: {url!r}\n"
        "Expected format: https://zoom.us/j/<ID>?pwd=<PWD>"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture real-time audio from a Zoom meeting.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="  Example:\n    python main.py 'https://zoom.us/j/123456789?pwd=abc'",
    )
    parser.add_argument("url",            help="Zoom meeting URL")
    parser.add_argument("--verbose",      action="store_true", help="Debug logging")
    parser.add_argument("--no-record",    action="store_true", help="Skip WAV recording")
    parser.add_argument("--no-transcribe",action="store_true", help="Skip Soniox transcription")
    parser.add_argument("--language",     default="en", help="BCP-47 language code (default: en)")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        _validate_url(args.url)
    except ValueError as e:
        log.error("%s", e)
        sys.exit(1)

    # Load .env so SONIOX_API_KEY is available
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    except ImportError:
        pass

    # Write PID for stop.sh
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    _shutdown = {"requested": False}

    def _on_signal(sig, _frame):
        log.info("Signal %s received — shutting down…", sig)
        _shutdown["requested"] = True

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT,  _on_signal)

    from session    import ZoomSession
    from audio      import AudioCapture
    from transcribe import SonioxTranscriber, get_soniox_config

    # --- Recording ---
    record_path: Optional[str] = None
    if not args.no_record:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        record_path = os.path.join(os.getcwd(), f"recording_{ts}.wav")

    # --- Transcription ---
    transcriber: Optional[SonioxTranscriber] = None
    if not args.no_transcribe:
        api_key = os.environ.get("SONIOX_API_KEY", "").strip()
        if api_key:
            transcriber = SonioxTranscriber(api_key, get_soniox_config(language=args.language))
        else:
            log.warning("SONIOX_API_KEY not set — transcription disabled. "
                        "Set it in .env or run with --no-transcribe.")

    session = ZoomSession(args.url)
    capture: Optional[AudioCapture] = None

    try:
        session.start()

        def _on_audio(data):
            if transcriber is not None:
                transcriber.send_audio(data)

        capture = AudioCapture(session.monitor_source, record_path=record_path)
        capture.start(on_audio=_on_audio)

        if transcriber:
            transcriber.start()

        sys.stderr.write(
            "\nCapturing audio"
            + (f" → recording to {record_path}" if record_path else "")
            + (" + live transcript" if transcriber else "")
            + "\nPress Ctrl+C or run ./stop.sh to stop.\n"
        )
        sys.stderr.flush()

        while not _shutdown["requested"]:
            if transcriber:
                committed, interim = transcriber.get_transcript_parts()
            else:
                committed, interim = "", ""
            _render(capture.get_level(), committed, interim)
            time.sleep(0.1)

    except Exception as e:
        log.error("Fatal: %s", e)
        raise
    finally:
        sys.stdout.write("\n" * 4)
        sys.stdout.flush()
        if transcriber:
            transcriber.stop()
        if capture:
            capture.stop()
        session.stop()
        try:
            os.remove(PID_FILE)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
