"""Bot.app process management with auto-restart control."""

import subprocess
import json
import logging
import os
import time
from pathlib import Path

from config import get_config

logger = logging.getLogger(__name__)

# Persistent auto-restart state
_STATE_FILE = Path(__file__).parent / "bot_state.json"
_auto_restart_enabled: bool | None = None  # None = not loaded yet


def _load_auto_restart() -> bool:
    """Load auto-restart state from disk."""
    global _auto_restart_enabled
    if _auto_restart_enabled is not None:
        return _auto_restart_enabled
    try:
        if _STATE_FILE.exists():
            data = json.loads(_STATE_FILE.read_text())
            _auto_restart_enabled = data.get("auto_restart", True)
        else:
            _auto_restart_enabled = True  # Default: enabled
    except Exception:
        _auto_restart_enabled = True
    return _auto_restart_enabled


def _save_auto_restart():
    """Persist auto-restart state to disk."""
    try:
        _STATE_FILE.write_text(json.dumps({"auto_restart": _auto_restart_enabled}))
    except Exception as e:
        logger.warning(f"Failed to save bot state: {e}")


def is_auto_restart_enabled() -> bool:
    """Check if auto-restart is enabled."""
    return _load_auto_restart()


def set_auto_restart(enabled: bool):
    """Enable or disable auto-restart."""
    global _auto_restart_enabled
    _auto_restart_enabled = enabled
    _save_auto_restart()
    logger.info(f"Auto-restart {'enabled' if enabled else 'disabled'}")


def is_bot_running() -> bool:
    """Check if BotApp is currently running."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", "BotApp"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        try:
            result = subprocess.run(
                ["ps", "aux"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return "BotApp" in result.stdout
        except Exception as e:
            logger.error(f"Failed to check if BotApp is running: {e}")
            return False


def start_bot() -> bool:
    """Start BotApp and enable auto-restart."""
    try:
        if is_bot_running():
            logger.info("BotApp is already running")
            set_auto_restart(True)
            return True

        config = get_config()
        bot_path = config.bot_app_path

        if not Path(bot_path).exists():
            logger.error(f"BotApp not found at {bot_path}")
            return False

        logger.info(f"Starting BotApp: {bot_path}")

        # Launch via shell so it detaches properly
        subprocess.Popen(
            [bot_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        time.sleep(3)

        if is_bot_running():
            set_auto_restart(True)
            logger.info("BotApp started successfully, auto-restart enabled")
            return True
        else:
            logger.warning("BotApp process not found after start")
            return False

    except Exception as e:
        logger.error(f"Failed to start BotApp: {e}")
        return False


def stop_bot() -> bool:
    """Stop BotApp and disable auto-restart."""
    try:
        # Disable auto-restart FIRST so the scheduler doesn't restart it
        set_auto_restart(False)

        if not is_bot_running():
            logger.info("BotApp is not running")
            return True

        logger.info("Stopping BotApp...")

        # Try graceful kill first
        subprocess.run(
            ["pkill", "-x", "BotApp"],
            capture_output=True,
            timeout=5,
        )
        time.sleep(2)

        # Force kill if still running
        if is_bot_running():
            subprocess.run(
                ["pkill", "-9", "-x", "BotApp"],
                capture_output=True,
                timeout=5,
            )
            time.sleep(1)

        if not is_bot_running():
            logger.info("BotApp stopped, auto-restart disabled")
            return True
        else:
            logger.warning("BotApp still running after kill")
            return False

    except Exception as e:
        logger.error(f"Failed to stop BotApp: {e}")
        return False


def restart_bot() -> bool:
    """Restart BotApp (stop + start). Re-enables auto-restart."""
    stop_bot()
    time.sleep(1)
    return start_bot()


def run_bot_manager_job() -> dict:
    """Periodic job to ensure BotApp is running (only if auto-restart is enabled)."""
    try:
        if not is_auto_restart_enabled():
            logger.debug("Auto-restart disabled, skipping bot check")
            return {"status": "auto_restart_disabled"}

        if is_bot_running():
            return {"status": "running"}
        else:
            logger.warning("BotApp is not running. Auto-restarting...")
            success = start_bot()
            return {"status": "started" if success else "failed_to_start"}

    except Exception as e:
        logger.error(f"Bot manager job failed: {e}")
        return {"status": "error", "error": str(e)}
