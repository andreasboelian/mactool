"""Upload bot log files to Supabase Storage bucket."""

import json
import re
import time
import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timedelta

from supabase import Client

logger = logging.getLogger(__name__)

BUCKET_NAME = "bot-logs"
RETENTION_DAYS = 3
UPLOAD_BATCH_SIZE = 5           # files per batch
UPLOAD_DELAY_BETWEEN = 0.5      # seconds between individual uploads
UPLOAD_DELAY_BETWEEN_BATCHES = 5  # seconds between batches
RATE_LIMIT_RETRY_DELAY = 10     # seconds to wait before retrying after rate limit
LOG_TIMESTAMP_RE = re.compile(r"^\[(\d{2})/(\d{2})\s+(\d{2}):(\d{2}):\d{2}\]")

# Persists last cleanup date so we only clean up old logs once per day
_CLEANUP_STATE_FILE = Path(__file__).parent / "log_cleanup_state.json"


def _should_run_cleanup() -> bool:
    """Return True if the daily cleanup has not yet run today."""
    try:
        if _CLEANUP_STATE_FILE.exists():
            data = json.loads(_CLEANUP_STATE_FILE.read_text())
            if data.get("last_cleanup") == datetime.now().strftime("%Y-%m-%d"):
                return False
    except Exception as e:
        logger.debug(f"Cleanup state read failed: {e}")
    return True


def _mark_cleanup_done() -> None:
    """Record that cleanup has run for today."""
    try:
        _CLEANUP_STATE_FILE.write_text(
            json.dumps({"last_cleanup": datetime.now().strftime("%Y-%m-%d")})
        )
    except Exception as e:
        logger.debug(f"Cleanup state write failed: {e}")


def _ensure_bucket(client: Client) -> bool:
    """Verify the storage bucket exists, creating it only if necessary."""
    # First try a lightweight GET to check existence
    try:
        client.storage.get_bucket(BUCKET_NAME)
        logger.debug(f"Bucket '{BUCKET_NAME}' exists")
        return True
    except Exception:
        pass  # bucket may not exist yet, try creating

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
    """Build the storage path: server_name/YYYY-MM-DD_HHMM_username.log (all lowercase)."""
    return f"{server_name}/{date_str}_{time_str}_{username}.log".lower()


def _get_previous_timeslot() -> str:
    """Get the 2-hour timeslot directly before the current time.

    Timeslots are: 00:00-01:59, 02:00-03:59, ..., 22:00-23:59.
    If current time is 12:10, returns "10:00-11:59".
    If current time is 01:30, returns "00:00-01:59".
    If current time is 00:15, returns "22:00-23:59" (previous day's last slot).
    """
    now = datetime.now()
    current_slot_start = (now.hour // 2) * 2
    prev_slot_start = (current_slot_start - 2) % 24
    prev_slot_end = prev_slot_start + 1
    return f"{prev_slot_start:02d}:00-{prev_slot_end:02d}:59"


def _get_allowed_usernames(db_path: Path, upload_all: bool = False) -> set[str]:
    """Get usernames that should have their logs uploaded.

    Filters:
    1. Device must have 'Phone' in customName
    2. When upload_all=False (auto-sync): profile's startup_time__time_slot
       must contain the previous 2h timeslot.
       When upload_all=True (manual "Sync Now"): all Phone usernames are
       returned regardless of timeslot.

    Returns a set of lowercase usernames.
    """
    if upload_all:
        logger.info("upload_all=True — returning ALL Phone usernames (no timeslot filter)")
    else:
        prev_slot = _get_previous_timeslot()
        logger.info(f"Previous timeslot: {prev_slot}")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        if upload_all:
            # No timeslot required — just Phone devices with usernames
            cursor.execute("""
                SELECT LOWER(p.config__username)
                FROM profile p
                JOIN device d ON p.config__device = d.id
                WHERE LOWER(d.customName) LIKE '%phone%'
                  AND p.config__username IS NOT NULL
                  AND p.config__username != ''
            """)
            rows = cursor.fetchall()
            conn.close()
            usernames = {row[0] for row in rows if row[0]}
            logger.info(f"Allowed usernames (upload_all): {len(usernames)} Phone profiles")
            return usernames

        cursor.execute("""
            SELECT LOWER(p.config__username), p.[startup_time__time_slot]
            FROM profile p
            JOIN device d ON p.config__device = d.id
            WHERE LOWER(d.customName) LIKE '%phone%'
              AND p.config__username IS NOT NULL
              AND p.config__username != ''
              AND p.[startup_time__time_slot] IS NOT NULL
              AND p.[startup_time__time_slot] != ''
        """)
        rows = cursor.fetchall()
        conn.close()

        # Filter: only profiles whose time_slot contains the previous slot
        usernames = set()
        for username, time_slot in rows:
            if prev_slot in time_slot:
                usernames.add(username)

        logger.info(
            f"Allowed usernames for slot {prev_slot}: {len(usernames)} "
            f"(of {len(rows)} Phone profiles with timeslots)"
        )
        return usernames
    except Exception as e:
        logger.error(f"Failed to query allowed usernames: {e}")
        return set()


def _discover_log_files(db_parent_dir: Path) -> list[Path]:
    """Find all .log files in the logs subdirectory."""
    logs_dir = db_parent_dir / "logs"
    if not logs_dir.exists():
        logger.info(f"Logs directory not found: {logs_dir}")
        return []

    log_files = sorted(logs_dir.glob("*.log"))
    logger.info(f"Found {len(log_files)} log files in {logs_dir}")
    return log_files


def _is_rate_limit_error(error: Exception) -> bool:
    """Detect transient rate-limit / overload / timeout errors from Supabase Storage."""
    msg = str(error).lower()
    return (
        "429" in msg
        or "503" in msg
        or "rate limit" in msg
        or "too many" in msg
        or "timeout" in msg
        or "timed out" in msg
    )


def _upload_log_file(client: Client, file_path: Path, storage_path: str) -> bool:
    """Upload a single log file to Supabase Storage.

    Retries once after a short delay if the error looks like a rate limit or
    timeout, so a single hiccup doesn't fail a whole batch.
    """
    content = file_path.read_bytes()
    for attempt in (1, 2):
        try:
            client.storage.from_(BUCKET_NAME).upload(
                storage_path,
                content,
                file_options={"content-type": "text/plain", "x-upsert": "true"},
            )
            logger.info(f"Uploaded {file_path.name} → {storage_path}")
            return True
        except Exception as e:
            if attempt == 1 and _is_rate_limit_error(e):
                logger.warning(
                    f"Rate-limited on {file_path.name}, retrying in {RATE_LIMIT_RETRY_DELAY}s: {e}"
                )
                time.sleep(RATE_LIMIT_RETRY_DELAY)
                continue
            logger.error(f"Failed to upload {file_path.name}: {e}")
            return False
    return False


def _cleanup_old_logs(client: Client, server_name: str, retention_days: int = RETENTION_DAYS) -> int:
    """Delete log files older than retention_days from the bucket.

    Runs at most once per day (gated by _CLEANUP_STATE_FILE) to avoid listing
    and deleting on every sync cycle.
    """
    if not _should_run_cleanup():
        logger.debug("Log cleanup already ran today, skipping")
        return 0

    try:
        files = client.storage.from_(BUCKET_NAME).list(
            server_name, options={"limit": 1000}
        )
        if not files:
            _mark_cleanup_done()
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

        _mark_cleanup_done()
        return len(to_delete)

    except Exception as e:
        logger.warning(f"Log cleanup failed (non-fatal): {e}")
        return 0


def upload_bot_logs(
    sb_client: Client,
    server_name: str,
    db_parent_dir: Path,
    db_path: Path,
    upload_all: bool = False,
) -> dict:
    """Upload bot log files to Supabase Storage and clean up old files.

    Only uploads logs for accounts on devices with 'Phone' in customName.
    When upload_all=True, skips the previous-timeslot filter and uploads
    logs for every Phone profile (use for manual "Sync Now" triggers).

    Returns: {"status", "uploaded", "failed", "skipped", "cleaned"}
    """
    result = {"status": "success", "uploaded": 0, "failed": 0, "skipped": 0, "cleaned": 0}

    # Ensure bucket exists
    if not _ensure_bucket(sb_client):
        result["status"] = "error"
        result["error"] = "Failed to create/verify storage bucket"
        return result

    # Get allowed usernames (profiles on 'Phone' devices)
    allowed = _get_allowed_usernames(db_path, upload_all=upload_all)
    if not allowed:
        logger.info("No allowed usernames found (no 'Phone' devices)")
        return result

    # Discover log files
    log_files = _discover_log_files(db_parent_dir)
    if not log_files:
        logger.info("No log files to upload")
        return result

    # Build list of files to upload (only for allowed usernames)
    to_upload = []
    for log_file in log_files:
        username = log_file.stem.lower()

        if username not in allowed:
            result["skipped"] += 1
            continue

        ts = _parse_log_timestamp(log_file)
        if not ts:
            result["failed"] += 1
            continue

        date_str, time_str = ts
        storage_path = _build_upload_path(server_name, username, date_str, time_str)
        to_upload.append((log_file, storage_path))

    # Upload in batches with throttling to avoid overwhelming Supabase Storage
    for i, (log_file, storage_path) in enumerate(to_upload):
        if _upload_log_file(sb_client, log_file, storage_path):
            result["uploaded"] += 1
        else:
            result["failed"] += 1

        # Throttle: short pause between uploads, longer pause between batches
        if i < len(to_upload) - 1:
            if (i + 1) % UPLOAD_BATCH_SIZE == 0:
                logger.debug(f"Batch pause after {i + 1}/{len(to_upload)} uploads")
                time.sleep(UPLOAD_DELAY_BETWEEN_BATCHES)
            else:
                time.sleep(UPLOAD_DELAY_BETWEEN)

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
