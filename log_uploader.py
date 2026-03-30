"""Upload bot log files to Supabase Storage bucket."""

import re
import logging
from pathlib import Path
from datetime import datetime, timedelta

from supabase import Client

logger = logging.getLogger(__name__)

BUCKET_NAME = "bot-logs"
RETENTION_DAYS = 90
LOG_TIMESTAMP_RE = re.compile(r"^\[(\d{2})/(\d{2})\s+(\d{2}):(\d{2}):\d{2}\]")


def _ensure_bucket(client: Client) -> bool:
    """Create the storage bucket if it doesn't exist."""
    try:
        client.storage.create_bucket(
            BUCKET_NAME, options={"public": False}
        )
        logger.info(f"Created storage bucket '{BUCKET_NAME}'")
        return True
    except Exception as e:
        error_msg = str(e).lower()
        if "already exists" in error_msg or "duplicate" in error_msg or "409" in error_msg:
            logger.debug(f"Bucket '{BUCKET_NAME}' already exists")
            return True
        logger.error(f"Failed to create bucket '{BUCKET_NAME}': {e}")
        return False


def _parse_log_timestamp(file_path: Path) -> tuple[str, str] | None:
    """Extract date and time from the first line of a log file.

    Log format: [MM/DD HH:MM:SS] ...
    Returns: ("YYYY-MM-DD", "HHMM") or None on failure.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            first_line = f.readline()

        match = LOG_TIMESTAMP_RE.match(first_line)
        if not match:
            logger.warning(f"Could not parse timestamp from {file_path.name}: {first_line[:60]}")
            return None

        month = int(match.group(1))
        day = int(match.group(2))
        hour = int(match.group(3))
        minute = int(match.group(4))

        # Infer year: if log month is Dec and current month is Jan, use last year
        now = datetime.now()
        year = now.year
        if month == 12 and now.month == 1:
            year -= 1

        date_str = f"{year}-{month:02d}-{day:02d}"
        time_str = f"{hour:02d}{minute:02d}"
        return date_str, time_str

    except Exception as e:
        logger.warning(f"Failed to parse timestamp from {file_path.name}: {e}")
        return None


def _build_upload_path(server_name: str, username: str, date_str: str, time_str: str) -> str:
    """Build the storage path: server_name/YYYY-MM-DD_HHMM_username.log"""
    return f"{server_name}/{date_str}_{time_str}_{username}.log"


def _discover_log_files(db_parent_dir: Path) -> list[Path]:
    """Find all .log files in the logs subdirectory."""
    logs_dir = db_parent_dir / "logs"
    if not logs_dir.exists():
        logger.info(f"Logs directory not found: {logs_dir}")
        return []

    log_files = sorted(logs_dir.glob("*.log"))
    logger.info(f"Found {len(log_files)} log files in {logs_dir}")
    return log_files


def _upload_log_file(client: Client, file_path: Path, storage_path: str) -> bool:
    """Upload a single log file to Supabase Storage."""
    try:
        content = file_path.read_bytes()
        client.storage.from_(BUCKET_NAME).upload(
            storage_path,
            content,
            file_options={"content-type": "text/plain", "x-upsert": "true"},
        )
        logger.info(f"Uploaded {file_path.name} → {storage_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to upload {file_path.name}: {e}")
        return False


def _cleanup_old_logs(client: Client, server_name: str, retention_days: int = RETENTION_DAYS) -> int:
    """Delete log files older than retention_days from the bucket."""
    try:
        files = client.storage.from_(BUCKET_NAME).list(
            server_name, options={"limit": 1000}
        )
        if not files:
            return 0

        cutoff = datetime.now() - timedelta(days=retention_days)
        to_delete = []

        for f in files:
            name = f.get("name", "")
            # Filename format: YYYY-MM-DD_HHMM_username.log
            if len(name) < 10:
                continue
            try:
                file_date = datetime.strptime(name[:10], "%Y-%m-%d")
                if file_date < cutoff:
                    to_delete.append(f"{server_name}/{name}")
            except ValueError:
                continue

        if to_delete:
            client.storage.from_(BUCKET_NAME).remove(to_delete)
            logger.info(f"Cleaned up {len(to_delete)} log files older than {retention_days} days")

        return len(to_delete)

    except Exception as e:
        logger.warning(f"Log cleanup failed (non-fatal): {e}")
        return 0


def upload_bot_logs(sb_client: Client, server_name: str, db_parent_dir: Path) -> dict:
    """Upload all bot log files to Supabase Storage and clean up old files.

    Returns: {"status", "uploaded", "failed", "cleaned"}
    """
    result = {"status": "success", "uploaded": 0, "failed": 0, "cleaned": 0}

    # Ensure bucket exists
    if not _ensure_bucket(sb_client):
        result["status"] = "error"
        result["error"] = "Failed to create/verify storage bucket"
        return result

    # Discover log files
    log_files = _discover_log_files(db_parent_dir)
    if not log_files:
        logger.info("No log files to upload")
        return result

    # Upload each log file
    for log_file in log_files:
        username = log_file.stem  # e.g. "andreasboelian" from "andreasboelian.log"

        ts = _parse_log_timestamp(log_file)
        if not ts:
            result["failed"] += 1
            continue

        date_str, time_str = ts
        storage_path = _build_upload_path(server_name, username, date_str, time_str)

        if _upload_log_file(sb_client, log_file, storage_path):
            result["uploaded"] += 1
        else:
            result["failed"] += 1

    # Cleanup old logs
    result["cleaned"] = _cleanup_old_logs(sb_client, server_name)

    if result["failed"] > 0 and result["uploaded"] == 0:
        result["status"] = "error"
    elif result["failed"] > 0:
        result["status"] = "partial_error"

    logger.info(
        f"Log upload complete: {result['uploaded']} uploaded, "
        f"{result['failed']} failed, {result['cleaned']} cleaned"
    )
    return result
