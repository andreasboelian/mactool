#!/usr/bin/env python3
"""Diagnose column compatibility between SQLite and Supabase.

Usage: python3 diagnose_columns.py

This tool:
1. Reads all columns from SQLite tables (device, profile, stats)
2. Discovers all columns in Supabase tables via OpenAPI
3. Shows: matched, truncated-matched, and missing columns
4. Generates SQL to create missing columns in Supabase
"""

import sqlite3
import json
import sys
import logging
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import get_config

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PG_MAX_IDENTIFIER = 63
TABLES = ["device", "profile", "stats"]
METADATA_COLS = {"mac_id", "ig_server", "imported_at", "change_at"}


def get_sqlite_columns(db_path: str, table: str) -> list[tuple[str, str]]:
    """Get (column_name, column_type) from SQLite table."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table});")
    cols = [(row[1], row[2]) for row in cursor.fetchall()]
    conn.close()
    return cols


def discover_supabase_columns(url: str, key: str, table: str) -> dict[str, str]:
    """Discover columns via OpenAPI spec. Returns {col_name: col_type}."""
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }

    # Method 1: OpenAPI spec
    try:
        resp = requests.get(f"{url}/rest/v1/", headers=headers, timeout=15)
        if resp.status_code == 200:
            schema = resp.json()
            definitions = schema.get("definitions", {})
            if table in definitions:
                props = definitions[table].get("properties", {})
                return {
                    name: info.get("type", info.get("format", "unknown"))
                    for name, info in props.items()
                }
    except Exception as e:
        logger.warning(f"OpenAPI failed for {table}: {e}")

    # Method 2: Query one row
    try:
        from supabase import create_client

        client = create_client(url, key)
        resp = client.table(table).select("*").limit(1).execute()
        if resp.data:
            return {k: type(v).__name__ for k, v in resp.data[0].items()}
    except Exception as e:
        logger.warning(f"Query fallback failed for {table}: {e}")

    return {}


def diagnose_table(
    table: str, sqlite_cols: list[tuple[str, str]], supabase_cols: dict[str, str]
) -> dict:
    """Diagnose column mapping for one table."""
    result = {
        "sqlite_count": len(sqlite_cols),
        "supabase_count": len(supabase_cols),
        "matched": [],
        "truncated_matched": [],
        "missing_in_supabase": [],
        "extra_in_supabase": [],
        "too_long": [],
    }

    sqlite_names = {col[0] for col in sqlite_cols}
    sb_names = set(supabase_cols.keys())

    for col_name, col_type in sqlite_cols:
        if col_name in sb_names:
            result["matched"].append(col_name)
        elif len(col_name) > PG_MAX_IDENTIFIER:
            truncated = col_name[:PG_MAX_IDENTIFIER]
            result["too_long"].append(
                {"sqlite": col_name, "truncated": truncated, "length": len(col_name)}
            )
            if truncated in sb_names:
                result["truncated_matched"].append(
                    {"sqlite": col_name, "supabase": truncated}
                )
            else:
                result["missing_in_supabase"].append(
                    {"name": col_name, "type": col_type, "reason": "too_long_no_match"}
                )
        else:
            result["missing_in_supabase"].append(
                {"name": col_name, "type": col_type, "reason": "not_found"}
            )

    # Columns in Supabase but not in SQLite (excluding metadata)
    for sb_col in sb_names:
        if sb_col not in sqlite_names and sb_col not in METADATA_COLS:
            is_truncated = any(
                col_name[:PG_MAX_IDENTIFIER] == sb_col
                for col_name, _ in sqlite_cols
                if len(col_name) > PG_MAX_IDENTIFIER
            )
            if not is_truncated:
                result["extra_in_supabase"].append(sb_col)

    return result


def generate_migration_sql(table: str, missing: list[dict]) -> list[str]:
    """Generate ALTER TABLE SQL for missing columns."""
    type_map = {
        "TEXT": "text",
        "BOOL": "boolean",
        "INTEGER": "integer",
        "REAL": "double precision",
        "": "text",
    }

    sqls = []
    for col in missing:
        name = col["name"]
        sqlite_type = col.get("type", "TEXT")
        pg_name = name[:PG_MAX_IDENTIFIER]
        pg_type = type_map.get(sqlite_type.upper(), "text")
        sqls.append(
            f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{pg_name}" {pg_type};'
        )

    return sqls


def main():
    print("=" * 70)
    print("EBM Mactool - Column Diagnosis")
    print("=" * 70)

    try:
        config = get_config()
    except Exception as e:
        print(f"\nERROR: Could not load config: {e}")
        print("Make sure config.json exists with supabase_url and supabase_key")
        sys.exit(1)

    db_path = Path(config.sqlite_db_path).expanduser()
    if not db_path.exists():
        print(f"\nERROR: SQLite database not found: {db_path}")
        sys.exit(1)

    print(f"\nSQLite DB: {db_path}")
    print(f"Supabase:  {config.supabase_url}")
    print()

    full_report = {}
    all_migration_sql = []

    for table in TABLES:
        print(f"--- {table.upper()} ---")

        sqlite_cols = get_sqlite_columns(str(db_path), table)
        print(f"  SQLite columns: {len(sqlite_cols)}")

        if not config.supabase_key:
            print("  Supabase: SKIPPED (no key configured)")
            print()
            continue

        supabase_cols = discover_supabase_columns(
            config.supabase_url, config.supabase_key, table
        )
        print(f"  Supabase columns: {len(supabase_cols)}")

        if not supabase_cols:
            print("  WARNING: Could not discover Supabase columns")
            print()
            continue

        diagnosis = diagnose_table(table, sqlite_cols, supabase_cols)
        full_report[table] = diagnosis

        matched = len(diagnosis["matched"])
        truncated = len(diagnosis["truncated_matched"])
        missing = len(diagnosis["missing_in_supabase"])
        extra = len(diagnosis["extra_in_supabase"])
        too_long = len(diagnosis["too_long"])

        print(f"  Matched:           {matched}")
        if truncated:
            print(f"  Truncated match:   {truncated}")
            for t in diagnosis["truncated_matched"]:
                print(f"    {t['sqlite']}")
                print(f"    -> {t['supabase']}")
        if too_long:
            print(f"  Too long (>63ch):  {too_long}")
        if missing:
            print(f"  Missing in SB:     {missing}")
            for m in diagnosis["missing_in_supabase"][:10]:
                print(f"    - {m['name']} ({m['type']}) [{m['reason']}]")
            if missing > 10:
                print(f"    ... and {missing - 10} more")
        if extra:
            print(f"  Extra in SB:       {extra}")
            for e_col in diagnosis["extra_in_supabase"][:5]:
                print(f"    + {e_col}")

        coverage = (matched + truncated) / len(sqlite_cols) * 100 if sqlite_cols else 0
        print(f"  Coverage:          {coverage:.1f}%")

        if diagnosis["missing_in_supabase"]:
            sqls = generate_migration_sql(table, diagnosis["missing_in_supabase"])
            all_migration_sql.extend(sqls)

        print()

    # Save report
    report_path = Path("column_diagnosis.json")
    with open(report_path, "w") as f:
        json.dump(full_report, f, indent=2)
    print(f"Full report saved to: {report_path}")

    if all_migration_sql:
        sql_path = Path("migration_add_columns.sql")
        with open(sql_path, "w") as f:
            f.write("-- Auto-generated migration to add missing columns\n")
            f.write("-- Run this in Supabase SQL Editor\n\n")
            for sql in all_migration_sql:
                f.write(sql + "\n")
        print(f"Migration SQL saved to: {sql_path}")
        print(
            f"  -> Run in Supabase SQL Editor to add {len(all_migration_sql)} missing columns"
        )

    print("\nDone!")


if __name__ == "__main__":
    main()
