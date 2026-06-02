"""RustDesk.app watchdog.

RustDesk must always be running so the Macs stay remotely reachable. This module
checks whether RustDesk is alive and (re)launches it if it was closed. A persistent
watch flag lets the dashboard pause/resume the watchdog.
"""

import subprocess
import json
import logging
import time
from pathlib import Path

from config import get_config

logger = logging.getLogger(__name__)

# Persistent watch state (whether the watchdog re-launches RustDesk)
_STATE_FILE = Path(__file__).parent / "rustdesk_state.json"
_watch_enabled: bool | None = None  # None = not loaded yet


def _load_watch() -> bool:
    """Load watch state from disk (default: enabled)."""
    global _watch_enabled
    if _watch_enabled is not None:
        return _watch_enabled
    try:
        if _STATE_FILE.exists():
            data = json.loads(_STATE_FILE.read_text())
            _watch_enabled = data.get("watch_enabled", True)
        else:
            _watch_enabled = True
    except Exception:
        _watch_enabled = True
    return _watch_enabled


def _save_watch():
    """Persist watch state to disk."""
    try:
        _STATE_FILE.write_text(json.dumps({"watch_enabled": _watch_enabled}))
    except Exception as e:
        logger.warning(f"Failed to save RustDesk state: {e}")


def is_watch_enabled() -> bool:
    """Check if the RustDesk watchdog is enabled."""
    return _load_watch()


def set_watch_enabled(enabled: bool):
    """Enable or disable the RustDesk watchdog."""
    global _watch_enabled
    _watch_enabled = enabled
    _save_watch()
    logger.info(f"RustDesk watchdog {'enabled' if enabled else 'disabled'}")


def _get_rustdesk_pids() -> list[str]:
    """Find all RustDesk process IDs using multiple methods."""
    pids = set()

    # Method 1: pgrep exact binary name
    for name in ["RustDesk", "rustdesk"]:
        try:
            result = subprocess.run(
                ["pgrep", "-x", name],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                for pid in result.stdout.strip().split("\n"):
                    if pid.strip():
                        pids.add(pid.strip())
        except Exception:
            pass

    # Method 2: pgrep by app bundle path (catches GUI + background service)
    try:
        result = subprocess.run(
            ["pgrep", "-f", "RustDesk.app"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            for pid in result.stdout.strip().split("\n"):
                if pid.strip():
                    pids.add(pid.strip())
    except Exception:
        pass

    return list(pids)


def is_rustdesk_running() -> bool:
    """Check if RustDesk is currently running."""
    return len(_get_rustdesk_pids()) > 0


def start_rustdesk() -> bool:
    """Launch RustDesk.app via macOS `open` (idempotent for GUI apps)."""
    try:
        if is_rustdesk_running():
            logger.info("RustDesk is already running")
            return True

        config = get_config()
        app_path = config.rustdesk_app_path

        if not Path(app_path).exists():
            logger.error(f"RustDesk.app not found at {app_path}")
            return False

        logger.info(f"Starting RustDesk: {app_path}")

        # `open` launches the app via LaunchServices. It won't spawn a second
        # instance if one is already running, so this stays idempotent.
        subprocess.run(
            ["open", app_path],
            capture_output=True, timeout=15,
        )

        time.sleep(3)

        if is_rustdesk_running():
            logger.info("RustDesk started successfully")
            return True
        else:
            logger.warning("RustDesk process not found after start")
            return False

    except Exception as e:
        logger.error(f"Failed to start RustDesk: {e}")
        return False


def run_rustdesk_manager_job() -> dict:
    """Periodic job to ensure RustDesk is running (only if watch is enabled)."""
    try:
        if not is_watch_enabled():
            logger.debug("RustDesk watchdog disabled, skipping check")
            return {"status": "watch_disabled"}

        if is_rustdesk_running():
            return {"status": "running"}
        else:
            logger.warning("RustDesk is not running. Auto-restarting...")
            success = start_rustdesk()
            return {"status": "started" if success else "failed_to_start"}

    except Exception as e:
        logger.error(f"RustDesk manager job failed: {e}")
        return {"status": "error", "error": str(e)}
