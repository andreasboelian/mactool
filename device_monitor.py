"""Device monitoring via ADB and webhook notifications.

Device source: Supabase profile table (all macs).
Status check: ADB on this mac.
Webhook: Triggered when a non-blacklisted device goes offline.
"""

import subprocess
import logging
import json
import requests
import time
import shutil
from pathlib import Path
from typing import Dict

from config import get_config

logger = logging.getLogger(__name__)

# State file persists across service restarts
_STATE_FILE = Path(__file__).parent / "device_state.json"

# In-memory state cache: serial → last_known_status
_device_state_cache: Dict[str, str] = {}


def _load_state_cache():
    """Load device state from disk (survives service restarts)."""
    global _device_state_cache
    if _device_state_cache:
        return  # Already loaded
    try:
        if _STATE_FILE.exists():
            _device_state_cache = json.loads(_STATE_FILE.read_text())
            logger.info(f"Loaded device state: {len(_device_state_cache)} devices")
    except Exception as e:
        logger.warning(f"Failed to load device state: {e}")
        _device_state_cache = {}


def _save_state_cache():
    """Persist device state to disk."""
    try:
        _STATE_FILE.write_text(json.dumps(_device_state_cache, indent=2))
    except Exception as e:
        logger.warning(f"Failed to save device state: {e}")

# Common ADB locations on macOS
_ADB_SEARCH_PATHS = [
    "/usr/local/bin/adb",
    "/opt/homebrew/bin/adb",
    "~/Library/Android/sdk/platform-tools/adb",
    "~/Android/Sdk/platform-tools/adb",
    "/usr/local/share/android-commandlinetools/platform-tools/adb",
]

# Cache resolved ADB path (None = not yet searched, "" = searched but not found)
_adb_path_cache: str | None = None


def _find_adb() -> str | None:
    """Find ADB binary. Returns path or None.

    LaunchAgents run with a minimal PATH, so we also ask the user's
    login shell where 'adb' lives.
    """
    global _adb_path_cache
    if _adb_path_cache is not None:
        return _adb_path_cache or None

    config = get_config()
    configured = config.adb_path

    # 1. Check configured path (if it's an absolute path)
    if configured and configured != "adb":
        expanded = Path(configured).expanduser()
        if expanded.exists():
            _adb_path_cache = str(expanded)
            return _adb_path_cache

    # 2. Check if 'adb' is in current PATH
    found = shutil.which("adb")
    if found:
        _adb_path_cache = found
        return _adb_path_cache

    # 3. Ask the login shell (LaunchAgents have a minimal PATH)
    try:
        result = subprocess.run(
            ["/bin/zsh", "-l", "-c", "which adb"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            shell_path = result.stdout.strip()
            if shell_path and Path(shell_path).exists():
                logger.info(f"Found ADB via login shell: {shell_path}")
                _adb_path_cache = shell_path
                return _adb_path_cache
    except Exception as e:
        logger.debug(f"Login shell ADB lookup failed: {e}")

    # 4. Search common macOS locations
    for path in _ADB_SEARCH_PATHS:
        expanded = Path(path).expanduser()
        if expanded.exists():
            logger.info(f"Found ADB at: {expanded}")
            _adb_path_cache = str(expanded)
            return _adb_path_cache

    logger.warning("ADB not found in PATH, login shell, or common locations")
    _adb_path_cache = ""
    return None


# ── ADB Functions ─────────────────────────────────────────────────


def get_adb_devices() -> set[str]:
    """Get list of connected ADB devices (serials)."""
    adb_cmd = _find_adb()
    if not adb_cmd:
        return set()

    try:
        result = subprocess.run(
            [adb_cmd, "devices"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            logger.error(f"ADB devices failed: {result.stderr}")
            return set()

        devices = set()
        for line in result.stdout.split("\n"):
            line = line.strip()
            if not line or line.startswith("List"):
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.add(parts[0])

        logger.debug(f"Found {len(devices)} ADB devices")
        return devices

    except subprocess.TimeoutExpired:
        logger.error("ADB devices command timed out")
        return set()
    except FileNotFoundError:
        logger.error(f"ADB binary not executable: {adb_cmd}")
        return set()
    except Exception as e:
        logger.error(f"Failed to get ADB devices: {e}")
        return set()


def check_device_online(serial: str) -> bool:
    """Check if device is reachable via ADB."""
    adb_cmd = _find_adb()
    if not adb_cmd:
        return False

    try:
        result = subprocess.run(
            [adb_cmd, "-s", serial, "shell", "echo", "ok"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        is_online = result.returncode == 0 and "ok" in result.stdout
        return is_online
    except Exception:
        return False


# ── Device Sources ────────────────────────────────────────────────


def get_devices_from_supabase() -> list[dict]:
    """Get devices from Supabase device table for THIS mac only.

    Queries the 'device' table (not profile!) — one row per physical device.
    Filters by id prefix so each mac only sees its own devices.
    """
    try:
        config = get_config()
        if not config.supabase_key:
            logger.warning("No Supabase key, falling back to local DB")
            return get_devices_from_local_db()

        from supabase import create_client

        client = create_client(config.supabase_url, config.supabase_key)

        # Query the DEVICE table (1 row per device), filtered to this mac
        response = (
            client.table("device")
            .select("*")
            .like("id", f"{config.server_name}_%")
            .execute()
        )

        if not response.data:
            logger.warning("No devices in Supabase device table")
            return get_devices_from_local_db()

        devices = []
        for row in response.data:
            device_id = row.get("id", "")
            if not device_id:
                continue

            # Extract hex part from id (e.g. 'mac17_5e78eccd' → '5e78eccd')
            hex_id = device_id.split("_", 1)[1] if "_" in device_id else device_id

            # Try common name fields
            name = (
                row.get("customName", "")
                or row.get("device__name", "")
                or row.get("name", "")
                or ""
            )

            # ADB serial for online/offline checking
            serial = row.get("device__id", "") or row.get("serial", "") or hex_id

            devices.append({
                "id": hex_id,
                "serial": serial,
                "name": name,
                "mac_id": row.get("mac_id", ""),
            })

        logger.info(f"Got {len(devices)} devices from Supabase device table")
        return devices

    except Exception as e:
        logger.warning(f"Supabase device query failed: {e}, falling back to local DB")
        return get_devices_from_local_db()


def get_devices_from_local_db() -> list[dict]:
    """Fallback: get devices from local SQLite device table."""
    try:
        import sqlite3

        config = get_config()
        db_path = Path(config.sqlite_db_path).expanduser()

        if not db_path.exists():
            return []

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT "id", "customName" FROM device;')
        rows = cursor.fetchall()
        conn.close()

        devices = []
        for row in rows:
            device_id = row[0] or ""
            name = row[1] or ""
            hex_id = device_id
            devices.append(
                {"id": hex_id, "serial": hex_id, "name": name, "mac_id": ""}
            )
        return devices
    except Exception as e:
        logger.error(f"Local DB device query failed: {e}")
        return []


# Keep old name as alias for API compatibility
def get_devices_from_db() -> list[dict]:
    """Get all devices — Supabase first, local DB as fallback."""
    return get_devices_from_supabase()


# ── Webhook ───────────────────────────────────────────────────────


def send_device_offline_webhook(device_id: str, custom_name: str) -> bool:
    """Send webhook notification to n8n when device goes offline."""
    try:
        config = get_config()
        webhook_url = config.webhook_url
        prefix = config.server_name

        # Include mac prefix so n8n can route by mac
        prefixed_id = f"{prefix}_{device_id}" if not device_id.startswith(f"{prefix}_") else device_id

        payload = {
            "server": prefix,
            "device_id": prefixed_id,
            "custom_name": custom_name,
        }

        retry_count = 0
        max_retries = 3

        while retry_count < max_retries:
            try:
                response = requests.post(
                    webhook_url,
                    json=payload,
                    timeout=10,
                )
                if response.status_code in [200, 201, 204]:
                    logger.info(f"Webhook sent for device {device_id}: {custom_name}")
                    return True
                else:
                    logger.warning(
                        f"Webhook returned {response.status_code}: {response.text}"
                    )
                    retry_count += 1
                    if retry_count < max_retries:
                        time.sleep(2**retry_count)

            except requests.Timeout:
                retry_count += 1
                logger.warning(
                    f"Webhook timeout (attempt {retry_count}/{max_retries})"
                )
                if retry_count < max_retries:
                    time.sleep(2**retry_count)

        logger.error(f"Webhook failed after {max_retries} retries for {device_id}")
        return False

    except Exception as e:
        logger.error(f"Failed to send webhook: {e}")
        return False


# ── Monitor Job ───────────────────────────────────────────────────


def run_device_monitor_job():
    """Monitor all devices and send webhook if offline.

    1. Get devices from Supabase device table (this mac only)
    2. Check which ones are connected via ADB on THIS mac
    3. If offline + not blacklisted + was online before → send webhook
       (webhook only fires on online→offline transition, persisted across restarts)
    """
    logger.info("Starting device monitor job...")

    adb_cmd = _find_adb()
    if not adb_cmd:
        logger.warning("ADB not found. Skipping device monitor.")
        return {"status": "adb_not_found"}

    try:
        _load_state_cache()

        config = get_config()
        blacklist = set(config.blacklist)

        all_devices = get_devices_from_supabase()
        if not all_devices:
            logger.warning("No devices found")
            return {"status": "no_devices"}

        adb_online = get_adb_devices()

        result = {"checked": 0, "online": 0, "offline": 0, "blacklisted": 0, "webhooks_sent": 0}

        for device in all_devices:
            serial = device["serial"]
            name = device["name"]

            if serial in blacklist or device["id"] in blacklist:
                result["blacklisted"] += 1
                continue

            result["checked"] += 1

            is_online = serial in adb_online

            cached_state = _device_state_cache.get(serial)
            _device_state_cache[serial] = "online" if is_online else "offline"

            if is_online:
                result["online"] += 1
            else:
                result["offline"] += 1

                # Webhook ONLY on transition: online → offline
                # (not on first check, not if already offline)
                if cached_state == "online":
                    logger.info(f"Device {serial} went OFFLINE (was online). Sending webhook...")
                    send_device_offline_webhook(serial, name)
                    result["webhooks_sent"] += 1
                elif cached_state is None:
                    logger.info(f"Device {serial} is offline (first check, no webhook)")

        _save_state_cache()

        logger.info(f"Device monitor completed: {result}")
        return result

    except Exception as e:
        logger.error(f"Device monitor job failed: {e}")
        return {"status": "error", "error": str(e)}


# ── Cache Management ──────────────────────────────────────────────


def reset_state_cache():
    """Reset the device state cache (memory + disk)."""
    global _device_state_cache
    _device_state_cache = {}
    try:
        if _STATE_FILE.exists():
            _STATE_FILE.unlink()
    except Exception:
        pass


def get_device_state() -> dict:
    """Get current device state from cache."""
    _load_state_cache()
    return _device_state_cache.copy()
