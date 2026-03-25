"""Self-update from GitHub repository with tag-based versioning."""

import logging
import os
import subprocess
import sys
from pathlib import Path

from config import get_config

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).parent


def _git(args: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    """Run a git command in APP_DIR."""
    return subprocess.run(
        ["git"] + args,
        cwd=APP_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def get_current_version() -> str:
    """Get current version from git tags.

    Returns tag name (e.g. 'v1.0.100') or SHA fallback.
    """
    try:
        # Exact tag on HEAD
        result = _git(["describe", "--tags", "--exact-match", "HEAD"])
        if result.returncode == 0:
            return result.stdout.strip()

        # Nearest tag + distance
        result = _git(["describe", "--tags"])
        if result.returncode == 0:
            return result.stdout.strip()

        # Fallback: short SHA
        result = _git(["rev-parse", "--short", "HEAD"])
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def get_available_versions() -> list[str]:
    """Get all available version tags from remote, sorted newest first."""
    try:
        _git(["fetch", "origin", "--tags"], timeout=15)
        result = _git(["tag", "--list", "v*", "--sort=-version:refname"])
        if result.returncode == 0 and result.stdout.strip():
            return [t.strip() for t in result.stdout.strip().split("\n") if t.strip()]
        return []
    except Exception as e:
        logger.error(f"Failed to get versions: {e}")
        return []


def check_for_updates() -> dict:
    """Check if a newer version tag is available."""
    config = get_config()
    if not config.github_repo:
        return {"status": "error", "error": "github_repo not configured"}

    try:
        git_dir = APP_DIR / ".git"
        if not git_dir.exists():
            return {"status": "not_initialized", "message": "Git not initialized"}

        # Fetch tags and branches
        _git(["fetch", "origin", "--tags"], timeout=15)

        current = get_current_version()
        versions = get_available_versions()

        if not versions:
            return {"status": "up_to_date", "version": current}

        latest = versions[0]

        if current == latest:
            return {
                "status": "up_to_date",
                "version": current,
                "versions": versions,
            }

        return {
            "status": "update_available",
            "current": current,
            "latest": latest,
            "versions": versions,
        }

    except Exception as e:
        logger.error(f"Update check failed: {e}")
        return {"status": "error", "error": str(e)}


def perform_update(version: str | None = None) -> dict:
    """Update to a specific version tag (or latest if None).

    Args:
        version: Tag name like 'v1.0.101'. None = latest tag.
    """
    config = get_config()
    if not config.github_repo:
        return {"status": "error", "error": "github_repo not configured"}

    git_dir = APP_DIR / ".git"

    try:
        if not git_dir.exists():
            # First time: init and fetch
            logger.info(f"Initializing git for {config.github_repo}...")
            _git(["init"])
            _git(["remote", "add", "origin", f"https://github.com/{config.github_repo}.git"])
            _git(["fetch", "origin", "--tags"], timeout=30)
        else:
            _git(["fetch", "origin", "--tags"], timeout=30)

        # Determine target version
        if version:
            target = version
        else:
            versions = get_available_versions()
            if not versions:
                # No tags yet — fall back to origin/main
                target = None
            else:
                target = versions[0]

        if target:
            # Checkout specific tag (detached HEAD is fine for deployment)
            result = _git(["checkout", target, "--force"])
            if result.returncode != 0:
                return {"status": "error", "error": f"git checkout {target} failed: {result.stderr}"}
            logger.info(f"Checked out version {target}")
        else:
            # Fallback: no tags, use origin/main
            result = _git(["reset", "--hard", "origin/main"])
            if result.returncode != 0:
                return {"status": "error", "error": f"git reset failed: {result.stderr}"}
            target = get_current_version()

        # Install dependencies
        venv_pip = APP_DIR / "venv" / "bin" / "pip"
        req_file = APP_DIR / "requirements.txt"
        if venv_pip.exists() and req_file.exists():
            subprocess.run(
                [str(venv_pip), "install", "-r", str(req_file), "-q"],
                cwd=APP_DIR, capture_output=True, timeout=120,
            )

        logger.info(f"Update complete: version {target}")

        # Schedule service restart
        _schedule_restart()

        return {
            "status": "success",
            "version": target,
            "message": f"Updated to {target}. Service restarting...",
        }

    except Exception as e:
        logger.error(f"Update failed: {e}")
        return {"status": "error", "error": str(e)}


def _schedule_restart():
    """Restart by exiting the process — launchd (KeepAlive=true) restarts it."""
    import threading

    def _do_exit():
        import time
        time.sleep(2)  # Let the HTTP response finish
        logger.info("Exiting for restart (launchd KeepAlive will restart us)...")
        os._exit(0)

    thread = threading.Thread(target=_do_exit, daemon=False)
    thread.start()
