"""Dependency checker and installer for Zoom audio capture."""
import os
import sys
import shutil
import logging
import subprocess

log = logging.getLogger(__name__)

# (binary_to_check, apt_package)
SYSTEM_DEPS = [
    ("Xvfb",       "xvfb"),
    ("xdpyinfo",   "x11-utils"),
    ("pactl",      "pulseaudio-utils"),
    ("pulseaudio", "pulseaudio"),
    ("pip3",       "python3-pip"),
]

# (pip_package, import_name)  — import_name differs from pip name for some packages
PYTHON_DEPS = [
    ("sounddevice",  "sounddevice"),
    ("numpy",        "numpy"),
    ("pulsectl",     "pulsectl"),
    ("playwright",   "playwright"),
    ("soniox",       "soniox"),
    ("python-dotenv","dotenv"),
]


def _apt_install(*packages: str) -> None:
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    subprocess.run(
        ["apt-get", "install", "-y", "--no-install-recommends", *packages],
        check=True, env=env, stdout=subprocess.DEVNULL,
    )


def _apt_update() -> None:
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    subprocess.run(
        ["apt-get", "update", "-qq"],
        check=True, env=env, stdout=subprocess.DEVNULL,
    )


def ensure_system_deps() -> None:
    missing_pkgs = [pkg for cmd, pkg in SYSTEM_DEPS if not shutil.which(cmd)]

    # portaudio: no direct binary, detect via sounddevice import
    try:
        import sounddevice  # noqa: F401
    except Exception:
        missing_pkgs.extend(["portaudio19-dev", "libportaudio2"])

    if missing_pkgs:
        log.info("Installing system packages: %s", missing_pkgs)
        _apt_update()
        _apt_install(*dict.fromkeys(missing_pkgs))


def ensure_python_deps() -> None:
    missing = []
    for pip_name, import_name in PYTHON_DEPS:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)

    if missing:
        log.info("Installing Python packages: %s", missing)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", *missing],
            check=True,
        )


def ensure_playwright() -> None:
    """Install Playwright's bundled Chromium if not already present."""
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
        capture_output=True, text=True,
    )
    # dry-run exits non-zero and prints what would be installed when missing
    if result.returncode != 0 or "chromium" in result.stdout.lower():
        log.info("Installing Playwright Chromium browser…")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium",
             "--with-deps"],
            check=True,
        )
        log.info("Playwright Chromium ready.")


def ensure_all() -> None:
    """Check and install every dependency. Requires apt (Debian/Ubuntu)."""
    if not shutil.which("apt-get"):
        raise EnvironmentError("apt-get not found; this tool requires Debian/Ubuntu.")

    ensure_system_deps()
    ensure_python_deps()
    ensure_playwright()
    log.info("All dependencies satisfied.")
