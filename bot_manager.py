"""Bot.app process management."""

import subprocess
import logging
import time
from pathlib import Path

from config import get_config

logger = logging.getLogger(__name__)


def is_bot_running() -> bool:
    """Check if BotApp is currently running."""
    try:
        # Use pgrep to find process
        result = subprocess.run(
            ["pgrep", "-x", "BotApp"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        is_running = result.returncode == 0
        logger.debug(f"BotApp running status: {is_running}")
        return is_running

    except FileNotFoundError:
        # Fallback: use ps if pgrep not available
        try:
            result = subprocess.run(
                ["ps", "aux"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            is_running = "BotApp" in result.stdout
            logger.debug(f"BotApp running status (via ps): {is_running}")
            return is_running
        except Exception as e:
            logger.error(f"Failed to check if BotApp is running: {e}")
            return False

    except Exception as e:
        logger.error(f"Failed to check if BotApp is running: {e}")
        return False


def start_bot() -> bool:
    """Start BotApp."""
    try:
        if is_bot_running():
            logger.info("BotApp is already running")
            return True

        config = get_config()
        bot_path = config.bot_app_path

        # Verify the app exists
        if not Path(bot_path).exists():
            logger.error(f"BotApp not found at {bot_path}")
            return False

        # Start the app
        logger.info(f"Starting BotApp from {bot_path}...")

        # Use 'open' command on macOS to launch the app
        result = subprocess.run(
            ["open", "-a", "botapp"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            time.sleep(2)  # Give app time to start
            if is_bot_running():
                logger.info("BotApp started successfully")
                return True
            else:
                logger.warning("BotApp start command returned 0 but process not found")
                return False
        else:
            logger.error(f"Failed to start BotApp: {result.stderr}")
            return False

    except Exception as e:
        logger.error(f"Failed to start BotApp: {e}")
        return False


def stop_bot() -> bool:
    """Stop BotApp."""
    try:
        if not is_bot_running():
            logger.info("BotApp is not running")
            return True

        logger.info("Stopping BotApp...")

        result = subprocess.run(
            ["pkill", "-f", "BotApp"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0 or result.returncode == 1:  # 1 = no process found
            time.sleep(1)
            if not is_bot_running():
                logger.info("BotApp stopped successfully")
                return True
            else:
                logger.warning("BotApp still running after stop command")
                return False
        else:
            logger.error(f"Failed to stop BotApp: {result.stderr}")
            return False

    except Exception as e:
        logger.error(f"Failed to stop BotApp: {e}")
        return False


def stop_all_python_processes() -> bool:
    """Stop all Python processes (except main)."""
    try:
        logger.info("Stopping all Python processes...")

        # Get current process ID
        import os

        current_pid = os.getpid()

        result = subprocess.run(
            ["pgrep", "-f", "python"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.stdout:
            pids = result.stdout.strip().split("\n")
            for pid in pids:
                if pid.strip() and pid.strip() != str(current_pid):
                    try:
                        subprocess.run(
                            ["kill", "-9", pid.strip()],
                            capture_output=True,
                            timeout=2,
                        )
                        logger.debug(f"Killed Python process {pid}")
                    except Exception as e:
                        logger.warning(f"Failed to kill process {pid}: {e}")

            logger.info("Python processes stopped")
            return True
        else:
            logger.info("No other Python processes found")
            return True

    except Exception as e:
        logger.error(f"Failed to stop Python processes: {e}")
        return False


def restart_bot() -> bool:
    """Restart BotApp (stop + start)."""
    try:
        logger.info("Restarting BotApp...")

        # Stop all Python processes
        stop_all_python_processes()

        # Stop bot
        stop_bot()
        time.sleep(1)

        # Start bot
        success = start_bot()

        if success:
            logger.info("BotApp restarted successfully")
        else:
            logger.error("Failed to restart BotApp")

        return success

    except Exception as e:
        logger.error(f"Failed to restart BotApp: {e}")
        return False


def run_bot_manager_job() -> dict:
    """Periodic job to ensure BotApp is running."""
    try:
        logger.info("Running bot manager job...")

        if is_bot_running():
            logger.info("BotApp is running. No action needed.")
            return {"status": "running"}
        else:
            logger.warning("BotApp is not running. Attempting to start...")
            success = start_bot()

            if success:
                return {"status": "started"}
            else:
                return {"status": "failed_to_start"}

    except Exception as e:
        logger.error(f"Bot manager job failed: {e}")
        return {"status": "error", "error": str(e)}
