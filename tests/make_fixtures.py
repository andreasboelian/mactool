#!/usr/bin/env python3
"""Build a throwaway GramBotStorage + config.json for the test suite.

Everything here is synthetic. Never point the tests at a real account folder —
sessions.json contains Instagram passwords in `args`.

Run from inside the working directory you want the fixtures in; `run_all.sh`
does that for you.
"""

import json
import os
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

SESSION_COUNT = 71  # the suite asserts on this number
random.seed(20260810)  # deterministic fixtures


def build_sessions() -> list[dict]:
    """71 sessions, all older than 12h, with the quirks the parser must survive."""
    sessions = []
    start = datetime(2026, 6, 1, 4, 0, 0)
    for i in range(SESSION_COUNT):
        started = start + timedelta(hours=i * 6)
        session = {
            "id": f"{i:08x}-3f70-48d4-8cab-{i:012x}",
            "total_interactions": random.randint(0, 150),
            "successful_interactions": random.randint(0, 60),
            "total_followed": random.choice([0, 5, 25, 43, None]),
            "total_likes": random.choice([0, 37, 80, 110, None]),
            "total_comments": 0,
            "total_pm": 0,
            "total_watched": random.randint(0, 10),
            "total_unfollowed": random.choice([0, 12, 29, None]),
            "total_scraped": {f"source_{i % 3}": 0} if i % 4 else {},
            "start_time": started.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "finish_time": "None",
            "args": {
                "device": "ba56df78",
                "username": "creatif_dr",
                "likes_count": "1-3",
            },
            "profile": {
                "posts": 81 + i,
                "followers": 1700 + i * 3,
                "following": 224,
            },
        }
        sessions.append(session)
    return sessions


def write_accounts(storage: Path):
    accounts = storage / "accounts"

    active = accounts / "creatif_dr"
    active.mkdir(parents=True, exist_ok=True)
    (active / "sessions.json").write_text(json.dumps(build_sessions(), indent=1))
    (active / "config.yml").write_text(
        "device: ba56df78\nusername: creatif_dr\nlikes-count: 1-3\n"
    )

    # No sessions at all + a quoted device with a trailing comment
    empty = accounts / "leer_account"
    empty.mkdir(parents=True, exist_ok=True)
    (empty / "sessions.json").write_text("[]\n")
    (empty / "config.yml").write_text('device: "R58WB00CHYM"  # kommentar\n')


def write_superdb(storage: Path):
    """super.db with the real column names, including the "13/50" / "nill" quirks."""
    db = storage / "super.db"
    if db.exists():
        db.unlink()

    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE stats (
          id TEXT, profileID TEXT, date TEXT, dateTime TEXT, follow TEXT, unfollow TEXT,
          "like" TEXT, comment TEXT, dm TEXT, watch TEXT, interaction TEXT,
          followers TEXT, followings TEXT, blocked TEXT, source_username TEXT,
          followBlocked TEXT, likeBlocked TEXT, logname TEXT
        );
        CREATE TABLE profile (
          id TEXT, config__username TEXT, config__device TEXT, startup_time__time_slot TEXT
        );
        CREATE TABLE device (id TEXT, customName TEXT);
        CREATE TABLE bin (id TEXT, config__username TEXT, noneoption__email TEXT);
        """
    )
    conn.executemany(
        "INSERT INTO profile VALUES (?,?,?,?)",
        [
            ("1", "creatif_dr", "ba56df78", "00.00-23.59"),
            ("68248247", "zweiter_account", "R58WB00CHYM", "00.00-23.59"),
            ("999", "", "XYZ", ""),  # no username → must be skipped
        ],
    )

    now = datetime.now()
    rows = [
        ("403480", "1", "2026-07-15", "23:59:57 2026-07-15", "13/50", "24/140", "26/120",
         "0/0", "0", "4", "11", "3087", "481", "0", "undefined", "0", "0", None),
        ("417551", "1", "2026-07-24", "23:59:14 2026-07-24", "12/50", "32/140", "24/120",
         "0/0", "0", "1", "12", "3109", "484", "0", "undefined", "0", "0", None),
        ("239117", "68248247", "2026-06-21", "23:59:22 2026-06-21", "26", "29", "0",
         "0", "0", "0", "24", "nill", "nill", "0", "undefined", "0", "0", None),
        # Fresh row so has_recent is exercised — stamped at fixture build time
        ("999999", "1", now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S %Y-%m-%d"),
         "5/50", "7/140", "9/120", "0/0", "0", "0", "3", "3120", "486", "0",
         "undefined", "0", "0", None),
        # Orphan: profileID without username → must be skipped
        ("111111", "999", "2026-07-01", "10:00:00 2026-07-01", "1", "1", "1",
         "0", "0", "0", "1", "1", "1", "0", "undefined", "0", "0", None),
    ]
    conn.executemany("INSERT INTO stats VALUES (" + ",".join("?" * 18) + ")", rows)
    conn.commit()
    conn.close()


def write_config():
    Path("config.json").write_text(
        json.dumps(
            {
                "server_name": "mac17",
                "sync_times": ["09:00"],
                "supabase_url": "https://example.supabase.co",
                "supabase_key": "",
                "sqlite_db_path": "GramBotStorage/super.db",
                "customer_stats_source": "sessions",
                "customer_stats_key": "",
                "customer_users_key": "",
            },
            indent=2,
        )
    )


def main():
    storage = Path("GramBotStorage")
    write_accounts(storage)
    write_superdb(storage)
    write_config()

    # A leftover run_state.json from a previous test would break assertions
    repo_state = Path(__file__).resolve().parent.parent / "run_state.json"
    if repo_state.exists():
        repo_state.unlink()

    print(f"Fixtures ready in {Path.cwd()} ({SESSION_COUNT} Sessions)")


if __name__ == "__main__":
    main()
