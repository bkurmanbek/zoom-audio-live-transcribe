"""ZoomSession: manages Xvfb virtual display + Playwright browser lifecycle."""
import os
import time
import logging
import subprocess
from urllib.parse import urlparse, parse_qs
from typing import Optional

log = logging.getLogger(__name__)

DISPLAY_NUM  = ":99"
SINK_NAME    = "zoom_capture"
GUEST_NAME   = "Listener"

# Selectors used to navigate Zoom web client
_SEL_JOIN_FROM_BROWSER = 'a:has-text("join from your browser"), a:has-text("Join from Your Browser")'
_SEL_NAME_INPUT        = '#inputname, input[placeholder*="name" i], input[id*="name" i]'
_SEL_JOIN_BTN          = 'button.preview-join-button, button:has-text("Join")'
_SEL_AUDIO_BTN         = 'button:has-text("Join Audio by Computer"), button:has-text("Join Audio")'


def _build_webclient_url(url: str) -> str:
    """Convert any Zoom meeting URL to the browser web-client join URL."""
    parsed  = urlparse(url)
    meeting_id = parsed.path.rstrip("/").split("/")[-1]
    params  = parse_qs(parsed.query)
    pwd     = params.get("pwd", [""])[0]
    wc_url  = f"https://zoom.us/wc/{meeting_id}/join"
    if pwd:
        wc_url += f"?pwd={pwd}"
    return wc_url


class ZoomSession:
    def __init__(self, meeting_url: str) -> None:
        self.meeting_url    = meeting_url
        self.webclient_url  = _build_webclient_url(meeting_url)
        self.monitor_source = f"{SINK_NAME}.monitor"
        self._xvfb: Optional[subprocess.Popen] = None
        self._pa_module_id: Optional[str]       = None
        self._playwright    = None
        self._browser       = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> "ZoomSession":
        self._start_pulseaudio()
        self._setup_audio_sink()
        self._start_xvfb()
        self._launch_browser()
        log.info("Zoom session active. Monitor: %s", self.monitor_source)
        return self

    def stop(self) -> None:
        log.info("Stopping Zoom session…")
        self._close_browser()
        self._kill(self._xvfb, name="Xvfb")
        self._unload_pa_module()
        log.info("Zoom session stopped.")

    def __enter__(self) -> "ZoomSession":
        return self.start()

    def __exit__(self, *_) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Internal steps
    # ------------------------------------------------------------------

    def _start_pulseaudio(self) -> None:
        result = subprocess.run(["pulseaudio", "--check"], capture_output=True)
        if result.returncode == 0:
            return  # already running at the default system socket

        # Not running — start our own instance with a custom runtime path.
        # Set PULSE_RUNTIME_PATH in os.environ so every subsequent pactl/
        # pulseaudio call in this process uses the same socket.
        log.info("Starting PulseAudio…")
        os.makedirs("/tmp/pulse-zoom", exist_ok=True)
        os.environ["PULSE_RUNTIME_PATH"] = "/tmp/pulse-zoom"
        subprocess.run(
            ["pulseaudio", "--start", "--exit-idle-time=-1", "--log-level=error"],
            check=True, capture_output=True,
        )
        time.sleep(1.5)
        if subprocess.run(["pulseaudio", "--check"], capture_output=True).returncode != 0:
            raise RuntimeError("PulseAudio failed to start.")

    def _setup_audio_sink(self) -> None:
        result = subprocess.run(
            ["pactl", "load-module", "module-null-sink",
             f"sink_name={SINK_NAME}",
             f"sink_properties=device.description={SINK_NAME}"],
            capture_output=True, text=True, check=True,
        )
        self._pa_module_id = result.stdout.strip()

        # PulseAudio may suffix the name (e.g. zoom_capture.2) if the name
        # is already taken — discover the actual sink name from the module.
        sinks_out = subprocess.run(
            ["pactl", "list", "sinks", "short"],
            capture_output=True, text=True,
        ).stdout
        actual_sink = SINK_NAME  # default
        for line in sinks_out.splitlines():
            parts = line.split()
            # parts: [index, name, driver, sample_spec, state]
            # Keep overwriting so we end up with the last (newest) match,
            # because PulseAudio appends .2/.3/… when the name is taken.
            if len(parts) >= 2 and "module-null-sink" in line and \
               parts[1].startswith(SINK_NAME):
                actual_sink = parts[1]

        self.monitor_source = f"{actual_sink}.monitor"
        subprocess.run(["pactl", "set-default-sink", actual_sink],
                       check=True, capture_output=True)
        log.info("PulseAudio null sink created: %s (module %s), monitor: %s",
                 actual_sink, self._pa_module_id, self.monitor_source)

    def _start_xvfb(self) -> None:
        # Reuse if already running (e.g. system-level Xvfb on :99)
        test = subprocess.run(
            ["xdpyinfo", "-display", DISPLAY_NUM],
            capture_output=True,
        )
        if test.returncode == 0:
            log.info("Xvfb already running on display %s — reusing.", DISPLAY_NUM)
            self._xvfb = None  # not ours to kill
            return

        self._xvfb = subprocess.Popen(
            ["Xvfb", DISPLAY_NUM, "-screen", "0", "1280x720x24", "-ac"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(1)
        if self._xvfb.poll() is not None:
            raise RuntimeError("Xvfb exited immediately — check xvfb installation.")
        log.info("Xvfb started on display %s", DISPLAY_NUM)

    def _launch_browser(self) -> None:
        from playwright.sync_api import sync_playwright

        env = {
            **os.environ,
            "DISPLAY":  DISPLAY_NUM,
            "PULSE_SINK": SINK_NAME,
        }

        log.info("Launching Chromium → %s", self.webclient_url)
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=False,
            env=env,
            args=[
                "--use-fake-ui-for-media-stream",   # auto-allow mic/camera prompts
                "--autoplay-policy=no-user-gesture-required",
                "--disable-infobars",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = self._browser.new_context(
            permissions=["microphone", "camera"],
        )
        page = context.new_page()
        page.goto(self.webclient_url, wait_until="domcontentloaded", timeout=30000)
        self._join_meeting(page)

    def _join_meeting(self, page) -> None:
        # Some entry points show a "join from your browser" link first
        try:
            link = page.locator(_SEL_JOIN_FROM_BROWSER).first
            link.wait_for(timeout=6000, state="visible")
            link.click()
            log.info("Clicked 'join from your browser' link.")
        except Exception:
            pass  # already on the join form

        # Fill display name
        name_input = page.locator(_SEL_NAME_INPUT).first
        name_input.wait_for(timeout=15000, state="visible")
        name_input.fill(GUEST_NAME)
        log.info("Filled name: %s", GUEST_NAME)

        # Click the Join button
        join_btn = page.locator(_SEL_JOIN_BTN).first
        join_btn.wait_for(timeout=10000, state="visible")
        join_btn.click()
        log.info("Clicked Join.")

        # Dismiss the audio dialog
        audio_btn = page.locator(_SEL_AUDIO_BTN).first
        try:
            audio_btn.wait_for(timeout=25000, state="visible")
        except Exception:
            page.screenshot(path="/tmp/zoom_debug.png")
            raise
        audio_btn.click()
        log.info("Joined audio — now in meeting.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _close_browser(self) -> None:
        try:
            if self._browser:
                self._browser.close()
        except Exception as e:
            log.debug("Error closing browser: %s", e)
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception as e:
            log.debug("Error stopping playwright: %s", e)

    def _kill(self, proc: Optional[subprocess.Popen], name: str = "process") -> None:
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("%s did not terminate; force-killing.", name)
            proc.kill()
        except Exception as e:
            log.debug("Error stopping %s: %s", name, e)

    def _unload_pa_module(self) -> None:
        if not self._pa_module_id:
            return
        try:
            subprocess.run(
                ["pactl", "unload-module", self._pa_module_id],
                capture_output=True, check=True,
            )
            log.info("PulseAudio null sink removed.")
        except Exception as e:
            log.debug("Could not unload PA module: %s", e)
