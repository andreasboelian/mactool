#!/usr/bin/env python3
"""EBM Mactool - macOS backend automation tool."""

import logging
import signal
import sys
import argparse
from pathlib import Path
from logging.handlers import RotatingFileHandler

import uvicorn

from config import get_config
from scheduler import get_scheduler
from bot_manager import run_bot_manager_job
from api import app

# Setup logging
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / "mactool.log"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Create rotating file handler
file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=3,
)
file_handler.setFormatter(logging.Formatter(LOG_FORMAT))

# Create console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(LOG_FORMAT))

# Configure root logger
logger = logging.getLogger()
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Global state
_scheduler = None
_api_server = None


def setup_signal_handlers():
    """Setup graceful shutdown handlers."""

    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}. Initiating graceful shutdown...")
        shutdown()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


def shutdown():
    """Graceful shutdown."""
    logger.info("Shutting down mactool...")

    # Stop scheduler
    global _scheduler
    if _scheduler:
        try:
            _scheduler.stop()
            logger.info("Scheduler stopped")
        except Exception as e:
            logger.error(f"Error stopping scheduler: {e}")

    logger.info("Shutdown complete")
    sys.exit(0)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="EBM Mactool - macOS automation tool")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--web-ui", action="store_true", help="Start FastAPI web UI")
    parser.add_argument("--web-port", type=int, default=8000, help="Web UI port (default: 8000)")
    parser.add_argument("--sync", action="store_true", help="Run sync once and exit")
    parser.add_argument("--check-devices", action="store_true", help="Check devices once and exit")
    parser.add_argument("--bot-restart", action="store_true", help="Restart bot and exit")

    args = parser.parse_args()

    # Set log level
    log_level = logging.DEBUG if args.debug else logging.INFO
    logger.setLevel(log_level)
    for handler in logger.handlers:
        handler.setLevel(log_level)

    logger.info("=" * 80)
    logger.info("EBM Mactool started")
    logger.info("=" * 80)

    try:
        # Load config
        config = get_config()
        logger.info(f"Configuration loaded: server={config.server_name}")

        # One-off commands
        if args.sync:
            logger.info("Running one-time sync...")
            from sync import trigger_sync
            # CLI is a manual trigger like the Sync Now button → upload all logs
            result = trigger_sync(upload_all_logs=True)
            logger.info(f"Sync result: {result}")
            return

        if args.check_devices:
            logger.info("Checking devices once...")
            from device_monitor import run_device_monitor_job
            result = run_device_monitor_job()
            logger.info(f"Device check result: {result}")
            return

        if args.bot_restart:
            logger.info("Restarting bot...")
            from bot_manager import restart_bot
            success = restart_bot()
            logger.info(f"Bot restart: {'success' if success else 'failed'}")
            return

        # Setup signal handlers for graceful shutdown
        setup_signal_handlers()

        # Initialize scheduler
        global _scheduler
        _scheduler = get_scheduler()
        _scheduler.start()
        logger.info("Scheduler started")

        # Run initial bot check
        logger.info("Running initial bot manager check...")
        result = run_bot_manager_job()
        logger.info(f"Bot check result: {result}")

        # Start web UI if requested
        if args.web_ui:
            logger.info(f"Starting FastAPI web UI on http://localhost:{args.web_port}")
            uvicorn.run(
                app,
                host="0.0.0.0",
                port=args.web_port,
                log_level="info",
                access_log=True,
            )
        else:
            # Keep running in foreground
            logger.info("Mactool running in background. Press Ctrl+C to stop.")
            try:
                signal.pause()  # Keep the main thread alive
            except KeyboardInterrupt:
                shutdown()

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        shutdown()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
