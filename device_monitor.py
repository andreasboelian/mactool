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

from supabase import Client

from config import get_config

logger = logging.getLogger(__name__)

# State file persists across service restarts
_STATE_FILE = Path(__file__).parent / "device_state.json"

# In-memory state cache: serial → last_known_status
_device_state_cache: Dict[str, str] = {}

# Shared Supabase client (created once per process, reused across cycles)
_sb_client: Client | None = None


def _get_sb_client() -> Client | None:
    """Return a lazily-initialized, process-wide Supabase client.

    Reusing one client avoids spawning a fresh HTTPS session per call, which
    previously made device monitoring hammer Supabase with ~27 new connections
    per cycle (1 query + 1 reset check + 1 per device status update).
    """
    global _sb_client
    if _sb_client is None:
        config = get_config()
        if not config.supabase_key:
            return None
        try:
            from supabase import create_client
            _sb_client = create_client(config.supabase_url, config.supabase_key)
        except Exception as e:
            logger.warning(f"Failed to create Supabase client: {e}")
            return None
    return _sb_client


def _load_state_cache():
    """Load device state from disk (survives service restarts).

    Migrates old format {serial: "online"} to new {serial: {"status": "online", "reported": false}}.
    """
    global _device_state_cache
    if _device_state_cache:
        return  # Already loaded
    try:
        if _STATE_FILE.exists():
            _device_state_cache = json.loads(_STATE_FILE.read_text())
            # Migrate old string format to new dict format
            migrated = False
            for serial, val in _device_state_cache.items():
                if isinstance(val, str):
                    _device_state_cache[serial] = {"status": val, "reported": val == "offline"}
                    migrated = True
            if migrated:
                _save_state_cache()
                logger.info("Migrated device state to new format")
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


def restart_adb_device(serial: str) -> bool:
    """Reboot a device via ADB."""
    adb_cmd = _find_adb()
    if not adb_cmd:
        logger.error("ADB not found, cannot restart device")
        return False

    try:
        result = subprocess.run(
            [adb_cmd, "-s", serial, "reboot"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            logger.info(f"Reboot command sent to device {serial}")
            return True
        else:
            logger.error(f"ADB reboot failed for {serial}: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"ADB reboot timed out for {serial}")
        return False
    except Exception as e:
        logger.error(f"Failed to reboot device {serial}: {e}")
        return False


# ── Device Sources ────────────────────────────────────────────────


def get_devices_from_supabase() -> list[dict]:
    """Get devices from Supabase device table for THIS mac only.

    Queries the 'device' table (not profile!) — one row per physical device.
    Filters by id prefix so each mac only sees its own devices.
    """
    try:
        client = _get_sb_client()
        if client is None:
            logger.warning("No Supabase key, falling back to local DB")
            return get_devices_from_local_db()

        config = get_config()

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


# ── Supabase Status ──────────────────────────────────────────────


def _batch_update_supabase_status(online_ids: list[str], offline_ids: list[str]) -> None:
    """Update adb_status for many devices in at most two batched requests.

    Groups all devices by status and issues a single .in_(...).update(...) per
    bucket, instead of one PATCH per device. This cuts per-cycle request
    volume from ~N to at most 2.
    """
    client = _get_sb_client()
    if client is None:
        return

    config = get_config()
    prefix = config.server_name

    def _prefix(device_id: str) -> str:
        return device_id if device_id.startswith(f"{prefix}_") else f"{prefix}_{device_id}"

    try:
        if online_ids:
            ids = [_prefix(d) for d in online_ids]
            client.table("device").update({"adb_status": "online"}).in_("id", ids).execute()
        if offline_ids:
            ids = [_prefix(d) for d in offline_ids]
            client.table("device").update({"adb_status": "offline"}).in_("id", ids).execute()
    except Exception as e:
        logger.debug(f"Batched adb_status update failed: {e}")


def _check_supabase_resets() -> set[str]:
    """Check if any device had adb_status externally set to 'online' in Supabase.

    Returns set of device serials that were externally reset.
    """
    try:
        client = _get_sb_client()
        if client is None:
            return set()
        config = get_config()
        response = (
            client.table("device")
            .select("id, device__id")
            .like("id", f"{config.server_name}_%")
            .eq("adb_status", "online")
            .execute()
        )
        reset_serials = set()
        if response.data:
            for row in response.data:
                serial = row.get("device__id", "") or row.get("id", "").split("_", 1)[-1]
                if serial:
                    reset_serials.add(serial)
        return reset_serials
    except Exception as e:
        logger.debug(f"Failed to check Supabase resets: {e}")
        return set()


# ── Webhook ───────────────────────────────────────────────────────


def send_batch_offline_webhook(devices: list[dict]) -> bool:
    """Send ONE batched webhook for all newly-offline devices.

    Payload: {"server": "mac17", "devices": [{"device_id": ..., "custom_name": ..., "serial": ...}]}
    """
    if not devices:
        return True

    try:
        config = get_config()
        webhook_url = config.webhook_url
        if not webhook_url:
            logger.warning("No webhook_url configured, skipping webhook")
            return False

        prefix = config.server_name
        payload = {
            "server": prefix,
            "devices": [
                {
                    "device_id": f"{prefix}_{d['id']}" if not d["id"].startswith(f"{prefix}_") else d["id"],
                    "custom_name": d["name"],
                    "serial": d["serial"],
                }
                for d in devices
            ],
        }

        retry_count = 0
        max_retries = 3

        while retry_count < max_retries:
            try:
                response = requests.post(webhook_url, json=payload, timeout=10)
                if response.status_code in [200, 201, 204]:
                    serials = [d["serial"] for d in devices]
                    logger.info(f"Batch webhook sent for {len(devices)} devices: {serials}")
                    return True
                else:
                    logger.warning(f"Webhook returned {response.status_code}: {response.text}")
                    retry_count += 1
                    if retry_count < max_retries:
                        time.sleep(2**retry_count)
            except requests.Timeout:
                retry_count += 1
                logger.warning(f"Webhook timeout (attempt {retry_count}/{max_retries})")
                if retry_count < max_retries:
                    time.sleep(2**retry_count)

        logger.error(f"Batch webhook failed after {max_retries} retries")
        return False

    except Exception as e:
        logger.error(f"Failed to send batch webhook: {e}")
        return False


# ── Monitor Job ───────────────────────────────────────────────────


def run_device_monitor_job():
    """Monitor all devices, update Supabase status, send batched webhook.

    Flow:
    1. Load state, get devices from Supabase, get ADB online set
    2. Check external resets (adb_status == "online" in Supabase) → clear reported
    3. Per device: check online/offline, update state
    4. Offline + (was online OR unreported) → add to newly_offline, mark reported
    5. First check → reported=False, no webhook
    6. Update adb_status in Supabase
    7. ONE batched webhook for all newly_offline
    8. Save state
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

        # Check for external resets via Supabase
        externally_reset = _check_supabase_resets()
        for serial in externally_reset:
            cached = _device_state_cache.get(serial)
            if isinstance(cached, dict) and cached.get("reported"):
                logger.info(f"Device {serial} externally reset via Supabase")
                cached["reported"] = False

        result = {"checked": 0, "online": 0, "offline": 0, "blacklisted": 0, "webhooks_sent": 0}
        newly_offline = []
        online_ids: list[str] = []
        offline_ids: list[str] = []

        for device in all_devices:
            serial = device["serial"]

            if serial in blacklist or device["id"] in blacklist:
                result["blacklisted"] += 1
                continue

            result["checked"] += 1
            is_online = serial in adb_online

            cached = _device_state_cache.get(serial)
            # Handle old string format (shouldn't happen after migration, but be safe)
            if isinstance(cached, str):
                cached = {"status": cached, "reported": cached == "offline"}

            if is_online:
                result["online"] += 1
                _device_state_cache[serial] = {"status": "online", "reported": False}
            else:
                result["offline"] += 1

                was_online = cached is not None and cached.get("status") == "online"
                was_unreported = (
                    cached is not None
                    and cached.get("status") == "offline"
                    and not cached.get("reported", False)
                )

                if was_online or was_unreported:
                    newly_offline.append(device)
                    _device_state_cache[serial] = {"status": "offline", "reported": True}
                elif cached is None:
                    # First check — don't report
                    _device_state_cache[serial] = {"status": "offline", "reported": False}
                    logger.info(f"Device {serial} is offline (first check, no webhook)")
                else:
                    # Already reported
                    _device_state_cache[serial] = {"status": "offline", "reported": True}

            # Collect for batched Supabase status update
            if is_online:
                online_ids.append(device["id"])
            else:
                offline_ids.append(device["id"])

        # Batched adb_status update (at most 2 requests total)
        _batch_update_supabase_status(online_ids, offline_ids)

        # Send ONE batched webhook for all newly offline devices
        if newly_offline:
            serials = [d["serial"] for d in newly_offline]
            logger.info(f"Devices went offline (reporting): {serials}")
            success = send_batch_offline_webhook(newly_offline)
            if success:
                result["webhooks_sent"] = len(newly_offline)
            else:
                # Rollback reported flag so next cycle retries
                for dev in newly_offline:
                    s = dev["serial"]
                    if s in _device_state_cache:
                        _device_state_cache[s]["reported"] = False

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
    """Get current device state from cache.

    Returns {serial: {"status": "online"|"offline", "reported": bool}}.
    """
    _load_state_cache()
    return _device_state_cache.copy()


def reset_device_reported(serial: str) -> bool:
    """Reset reported flag for a single device so it can be re-reported."""
    _load_state_cache()
    if serial not in _device_state_cache:
        return False
    state = _device_state_cache[serial]
    if isinstance(state, dict):
        state["reported"] = False
    else:
        _device_state_cache[serial] = {"status": state, "reported": False}
    _save_state_cache()
    logger.info(f"Reset reported flag for device {serial}")
    return True


def reset_all_reported() -> int:
    """Reset reported flag for all devices. Returns count of reset devices."""
    _load_state_cache()
    count = 0
    for serial in _device_state_cache:
        state = _device_state_cache[serial]
        if isinstance(state, dict) and state.get("reported"):
            state["reported"] = False
            count += 1
        elif isinstance(state, str):
            _device_state_cache[serial] = {"status": state, "reported": False}
            count += 1
    _save_state_cache()
    logger.info(f"Reset reported flag for {count} devices")
    return count
