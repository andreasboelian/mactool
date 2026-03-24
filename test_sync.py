#!/usr/bin/env python3
"""Test sync functionality with detailed logging.

Usage: python3 test_sync.py [--dry-run] [--table profile]
"""

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Setup detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("sync_test.log", mode="w"),
    ],
)
logger = logging.getLogger(__name__)


def test_sqlite_access():
    """Test SQLite database access and show table info."""
    from config import get_config

    config = get_config()
    db_path = Path(config.sqlite_db_path).expanduser()

    print(f"\n[1] SQLite Database: {db_path}")
    if not db_path.exists():
        print(f"  ERROR: Database not found!")
        return False

    print(f"  Size: {db_path.stat().st_size / 1024 / 1024:.1f} MB")

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    for table in ["device", "profile", "stats"]:
        cursor.execute(f"SELECT COUNT(*) FROM {table};")
        count = cursor.fetchone()[0]
        cursor.execute(f"PRAGMA table_info({table});")
        cols = cursor.fetchall()
        long_cols = [c[1] for c in cols if len(c[1]) > 63]
        print(f"  {table}: {count} rows, {len(cols)} columns", end="")
        if long_cols:
            print(f" ({len(long_cols)} columns > 63 chars)")
        else:
            print()

    conn.close()
    return True


def test_supabase_connection():
    """Test Supabase connection and schema discovery."""
    import requests
    from config import get_config

    config = get_config()

    print(f"\n[2] Supabase: {config.supabase_url}")
    if not config.supabase_key:
        print("  ERROR: No supabase_key configured!")
        return False

    print(f"  Key: {config.supabase_key[:20]}...")

    headers = {
        "apikey": config.supabase_key,
        "Authorization": f"Bearer {config.supabase_key}",
    }

    # Test connection via OpenAPI
    try:
        resp = requests.get(
            f"{config.supabase_url}/rest/v1/",
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 200:
            schema = resp.json()
            definitions = schema.get("definitions", {})
            tables_found = list(definitions.keys())
            print(f"  Connected! Found {len(tables_found)} tables: {tables_found[:10]}")

            for table in ["device", "profile", "stats"]:
                if table in definitions:
                    cols = list(definitions[table].get("properties", {}).keys())
                    print(f"  {table}: {len(cols)} columns in Supabase")
                else:
                    print(f"  {table}: NOT FOUND in Supabase!")
            return True
        else:
            print(f"  ERROR: HTTP {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def test_adb():
    """Test ADB availability."""
    from device_monitor import _find_adb, get_adb_devices, get_devices_from_db

    print("\n[3] ADB Device Monitor:")
    adb = _find_adb()
    if adb:
        print(f"  ADB found: {adb}")
        devices = get_adb_devices()
        print(f"  Connected devices: {len(devices)}")
        for d in devices:
            print(f"    - {d}")
    else:
        print("  ADB not found (device monitoring will be unavailable)")

    db_devices = get_devices_from_db()
    print(f"  Devices in DB: {len(db_devices)}")
    for d in db_devices[:5]:
        print(f"    - {d['serial']} ({d['name'][:50]})")
    if len(db_devices) > 5:
        print(f"    ... and {len(db_devices) - 5} more")


def test_full_sync():
    """Run full sync and show results."""
    from sync import trigger_sync

    print("\n[4] Running Full Sync...")
    print("  (this may take a moment)")

    result = trigger_sync()

    print(f"\n  Status: {result.get('status')}")

    for table, info in result.get("tables", {}).items():
        status = info.get("status")
        count = info.get("count", "?")
        cols_synced = info.get("columns_synced", "?")
        skipped = info.get("skipped_columns", 0)
        error = info.get("error", "")

        if status == "success":
            print(f"  {table}: {count} rows, {cols_synced} columns synced, {skipped} skipped")
        elif status == "no_data":
            print(f"  {table}: no data")
        else:
            print(f"  {table}: ERROR - {error[:100]}")

    if "column_mapping" in result:
        print(f"\n  Column mapping: {result['column_mapping']}")

    if "skipped_columns" in result:
        for table, cols in result["skipped_columns"].items():
            print(f"\n  Skipped columns in {table} ({len(cols)}):")
            for c in cols[:5]:
                print(f"    - {c}")
            if len(cols) > 5:
                print(f"    ... and {len(cols) - 5} more")

    return result


def main():
    parser = argparse.ArgumentParser(description="Test mactool sync")
    parser.add_argument("--dry-run", action="store_true", help="Only test connections, don't sync")
    args = parser.parse_args()

    print("=" * 70)
    print("EBM Mactool - Sync Test")
    print("=" * 70)

    # Test SQLite
    if not test_sqlite_access():
        print("\nABORTED: SQLite access failed")
        sys.exit(1)

    # Test Supabase
    sb_ok = test_supabase_connection()

    # Test ADB
    test_adb()

    if args.dry_run:
        print("\n--- DRY RUN: Skipping actual sync ---")
        print("\nLog file: sync_test.log")
        return

    if not sb_ok:
        print("\nABORTED: Supabase connection failed. Cannot sync.")
        sys.exit(1)

    # Full sync
    result = test_full_sync()

    print("\n" + "=" * 70)
    if result.get("status") in ["success"]:
        print("RESULT: All tables synced successfully!")
    elif result.get("status") == "partial_error":
        print("RESULT: Partial success - some tables had errors")
    else:
        print(f"RESULT: Sync failed - {result.get('error', 'unknown')}")

    print("=" * 70)
    print(f"\nFull log: sync_test.log")
    print(f"JSON result:")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
