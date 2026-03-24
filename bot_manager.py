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
            _auto_restart_enabled = True
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


def _get_bot_pids() -> list[str]:
    """Find all BotApp process IDs using multiple methods."""
    pids = set()

    # Method 1: pgrep exact name
    for name in ["BotApp", "botapp"]:
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

    # Method 2: pgrep by path pattern
    try:
        result = subprocess.run(
            ["pgrep", "-f", "botapp.app"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            my_pid = str(os.getpid())
            for pid in result.stdout.strip().split("\n"):
                if pid.strip() and pid.strip() != my_pid:
                    pids.add(pid.strip())
    except Exception:
        pass

    return list(pids)


def is_bot_running() -> bool:
    """Check if BotApp is currently running."""
    return len(_get_bot_pids()) > 0


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

        # Start via shell with "exit;" — same as manual terminal launch.
        # This ensures ADB devices are available to the bot.
        subprocess.Popen(
            f"{bot_path} ; exit;",
            shell=True,
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


def _kill_all_python_except_self():
    """Kill all Python processes except our own mactool process."""
    my_pid = str(os.getpid())
    try:
        result = subprocess.run(
            ["pgrep", "-f", "python"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            for pid in result.stdout.strip().split("\n"):
                pid = pid.strip()
                if pid and pid != my_pid:
                    try:
                        subprocess.run(["kill", "-9", pid], capture_output=True, timeout=2)
                        logger.debug(f"Killed Python process {pid}")
                    except Exception:
                        pass
            logger.info("All Python processes killed (except mactool)")
    except Exception as e:
        logger.warning(f"Failed to kill Python processes: {e}")


def stop_bot() -> bool:
    """Stop BotApp, kill all Python scripts, and disable auto-restart."""
    try:
        # Disable auto-restart FIRST so the scheduler doesn't restart it
        set_auto_restart(False)

        # 1. Kill BotApp
        pids = _get_bot_pids()
        if pids:
            logger.info(f"Stopping BotApp (PIDs: {pids})...")
            for pid in pids:
                try:
                    subprocess.run(["kill", pid], capture_output=True, timeout=2)
                except Exception:
                    pass
            time.sleep(2)

            # Force kill if still running
            pids = _get_bot_pids()
            if pids:
                logger.info(f"Force killing BotApp (PIDs: {pids})...")
                for pid in pids:
                    try:
                        subprocess.run(["kill", "-9", pid], capture_output=True, timeout=2)
                    except Exception:
                        pass
                time.sleep(1)

        # 2. Kill all Python scripts (except our own mactool process)
        _kill_all_python_except_self()

        bot_stopped = not is_bot_running()
        if bot_stopped:
            logger.info("BotApp stopped, Python scripts killed, auto-restart disabled")
        else:
            logger.warning("BotApp still running after force kill")

        return bot_stopped

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
