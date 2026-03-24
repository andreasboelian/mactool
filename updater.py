"""Self-update from GitHub repository."""

import logging
import subprocess
import sys
from pathlib import Path

from config import get_config

logger = logging.getLogger(__name__)

# Files to update (only Python scripts, never config.json)
UPDATE_FILES = [
    "api.py",
    "bot_manager.py",
    "config.py",
    "device_monitor.py",
    "diagnose_columns.py",
    "main.py",
    "scheduler.py",
    "sync.py",
    "test_sync.py",
    "updater.py",
    "requirements.txt",
    "install.sh",
]

APP_DIR = Path(__file__).parent


def check_for_updates() -> dict:
    """Check if updates are available by comparing local and remote commits."""
    config = get_config()
    if not config.github_repo:
        return {"status": "error", "error": "github_repo not configured"}

    try:
        # Check if git is available in APP_DIR
        git_dir = APP_DIR / ".git"
        if not git_dir.exists():
            return {
                "status": "not_initialized",
                "message": "Run initial setup first: git clone into app directory",
            }

        # Fetch remote
        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=APP_DIR,
            capture_output=True,
            timeout=15,
        )

        # Compare local vs remote
        local = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=APP_DIR,
            capture_output=True,
            text=True,
            timeout=5,
        )
        remote = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=APP_DIR,
            capture_output=True,
            text=True,
            timeout=5,
        )

        local_sha = local.stdout.strip()[:8]
        remote_sha = remote.stdout.strip()[:8]

        if local_sha == remote_sha:
            return {"status": "up_to_date", "version": local_sha}

        # Count commits behind
        behind = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/main"],
            cwd=APP_DIR,
            capture_output=True,
            text=True,
            timeout=5,
        )
        count = behind.stdout.strip()

        return {
            "status": "update_available",
            "current": local_sha,
            "latest": remote_sha,
            "commits_behind": int(count) if count.isdigit() else 0,
        }

    except Exception as e:
        logger.error(f"Update check failed: {e}")
        return {"status": "error", "error": str(e)}


def perform_update() -> dict:
    """Pull latest code from GitHub and restart the service."""
    config = get_config()
    if not config.github_repo:
        return {"status": "error", "error": "github_repo not configured"}

    git_dir = APP_DIR / ".git"

    try:
        if not git_dir.exists():
            # First time: clone the repo
            logger.info(f"Cloning {config.github_repo} into {APP_DIR}...")
            result = subprocess.run(
                [
                    "git", "clone",
                    f"https://github.com/{config.github_repo}.git",
                    "--branch", "main",
                    "--single-branch",
                    ".",
                ],
                cwd=APP_DIR,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                # If dir not empty, init and pull instead
                subprocess.run(["git", "init"], cwd=APP_DIR, capture_output=True, timeout=10)
                subprocess.run(
                    ["git", "remote", "add", "origin",
                     f"https://github.com/{config.github_repo}.git"],
                    cwd=APP_DIR, capture_output=True, timeout=10,
                )
                subprocess.run(
                    ["git", "fetch", "origin"],
                    cwd=APP_DIR, capture_output=True, timeout=30,
                )
                subprocess.run(
                    ["git", "reset", "--hard", "origin/main"],
                    cwd=APP_DIR, capture_output=True, timeout=15,
                )
            action = "cloned"
        else:
            # Pull latest changes (force-reset to remote, keeps config.json safe via .gitignore)
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=APP_DIR, capture_output=True, timeout=30,
            )
            result = subprocess.run(
                ["git", "reset", "--hard", "origin/main"],
                cwd=APP_DIR, capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return {"status": "error", "error": f"git reset failed: {result.stderr}"}
            action = "updated"

        # Get current version
        version_result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=APP_DIR, capture_output=True, text=True, timeout=5,
        )
        version = version_result.stdout.strip() if version_result.returncode == 0 else "unknown"

        # Install any new dependencies
        venv_pip = APP_DIR / "venv" / "bin" / "pip"
        req_file = APP_DIR / "requirements.txt"
        if venv_pip.exists() and req_file.exists():
            subprocess.run(
                [str(venv_pip), "install", "-r", str(req_file), "-q"],
                cwd=APP_DIR, capture_output=True, timeout=120,
            )

        logger.info(f"Update complete: {action}, version {version}")

        # Schedule service restart (non-blocking)
        _schedule_restart()

        return {
            "status": "success",
            "action": action,
            "version": version,
            "message": "Update applied. Service restarting...",
        }

    except Exception as e:
        logger.error(f"Update failed: {e}")
        return {"status": "error", "error": str(e)}


def _schedule_restart():
    """Restart the service after a short delay (so the API response gets sent)."""
    import threading

    def _do_restart():
        import time
        time.sleep(2)  # Let the HTTP response finish
        plist = Path.home() / "Library/LaunchAgents/com.ebm.mactool.plist"
        if plist.exists():
            subprocess.run(
                ["launchctl", "unload", str(plist)],
                capture_output=True, timeout=10,
            )
            subprocess.run(
                ["launchctl", "load", str(plist)],
                capture_output=True, timeout=10,
            )
        else:
            # Fallback: restart python process
            logger.warning("No LaunchAgent plist found, restarting process directly")
            import os
            os.execv(sys.executable, [sys.executable] + sys.argv)

    thread = threading.Thread(target=_do_restart, daemon=True)
    thread.start()
