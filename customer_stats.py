"""Statistik (Kunde) — session statistics upload for the customer Supabase.

Replaces the per-mac `upload-macXX.py` LaunchAgent scripts. Those were identical
apart from a hardcoded `serverzuordnung`, which now comes from `server_name` in
config.json — so one implementation covers every mac.

Two upload targets, each configurable and individually optional:

* **statistik** — raw session data, UPSERT on `session_id`.
* **users** — "beautified" numbers (minimum values are replaced by plausible
  random ones) plus a synthetic entry for accounts that had no session in the
  last 12 hours. INSERT, skipping sessions that are already there.

The beautification and missing-session rules are taken over unchanged from the
legacy script; only the transport is different (batched instead of one request
per session).

Data can come from two sources, switchable in the dashboard settings:

* ``sessions`` — ``GramBotStorage/accounts/<user>/sessions.json`` (default, what
  the legacy script used).
* ``superdb`` — the ``stats`` table of the mac's own ``super.db``. That table has
  no equivalent for ``successful_interactions``, ``posts``, ``total_scraped``,
  ``args`` and ``profile``, so those stay empty.
"""

import json
import logging
import random
import re
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from config import get_config
from supabase_rest import SupabaseRest, describe_target
import run_state

try:  # optional — we fall back to a line parser when PyYAML is unavailable
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

logger = logging.getLogger(__name__)

# Columns of the customer `statistik` table (verified against the live project)
STATS_SCHEMA = {
    "username": "text",
    "session_id": "text",
    "start_time": "text",
    "total_interactions": "integer",
    "successful_interactions": "integer",
    "total_followed": "integer",
    "total_likes": "integer",
    "total_unfollowed": "integer",
    "total_pm": "integer",
    "total_watched": "integer",
    "device": "text",
    "posts": "integer",
    "followers": "integer",
    "following": "integer",
    "total_scraped": "jsonb",
    "args": "jsonb",
    "profile": "jsonb",
    "imported_at": "timestamptz",
    "serverzuordnung": "text",
}

# Columns the `users` insert uses
USERS_SCHEMA = {
    "username": "text",
    "session_id": "text",
    "start_time": "text",
    "total_interactions": "integer",
    "successful_interactions": "integer",
    "total_followed": "integer",
    "total_likes": "integer",
    "total_unfollowed": "integer",
    "total_pm": "integer",
    "total_watched": "integer",
    "device": "text",
    "posts": "integer",
    "followers": "integer",
    "following": "integer",
    "total_scraped": "text",
    "imported_at": "text",
    "serverzuordnung": "text",
}

# stats columns we read from super.db (whatever of these actually exists)
SUPERDB_STATS_COLUMNS = [
    "id", "profileID", "date", "dateTime", "follow", "unfollow", "like",
    "dm", "watch", "interaction", "followers", "followings",
]

# Fields that super.db simply cannot provide — surfaced in the dashboard
SUPERDB_UNAVAILABLE_FIELDS = [
    "successful_interactions", "posts", "total_scraped", "args", "profile",
]

_DEVICE_LINE_RE = re.compile(r"^device\s*:\s*(.+?)\s*$", re.MULTILINE)
_EMPTY_VALUES = {"", "nill", "null", "none", "undefined", "-", "n/a"}


class CustomerStatsError(RuntimeError):
    """The upload could not be prepared (bad paths, missing tables, ...)."""


# ── Source helpers ────────────────────────────────────────────────────


def accounts_dir() -> Path:
    """Locate the GramBotStorage accounts folder.

    Primarily derived from `sqlite_db_path` so it follows the same install the
    rest of the tool uses; falls back to the legacy script's Desktop lookup.
    """
    config = get_config()
    storage = Path(config.sqlite_db_path).expanduser().parent
    candidate = storage / "accounts"
    if candidate.is_dir():
        return candidate

    home = Path.home()
    base = home / "Desktop" if (home / "Desktop").exists() else home
    return base / "GramBotStorage" / "accounts"


def _device_from_config_yml(path: Path) -> str | None:
    """Read the `device` key from an account's config.yml."""
    if not path.exists():
        return None

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"Could not read {path}: {e}")
        return None

    if yaml is not None:
        try:
            data = yaml.safe_load(text)
            if isinstance(data, dict):
                value = data.get("device")
                if value not in (None, ""):
                    return str(value).strip()
                return None
        except Exception as e:
            logger.debug(f"YAML parse failed for {path}, using line parser: {e}")

    match = _DEVICE_LINE_RE.search(text)
    if not match:
        return None

    value = match.group(1).split(" #")[0].strip().strip("\"'")
    return value or None


def _to_int(value) -> int | None:
    """Parse a super.db counter like "13/50", "26" or "nill" into an int."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)

    text = str(value).strip()
    if text.lower() in _EMPTY_VALUES:
        return None

    text = text.split("/")[0].strip()
    try:
        return int(float(text))
    except ValueError:
        return None


def _superdb_start_time(date_time, date) -> str | None:
    """Normalise super.db's "HH:MM:SS YYYY-MM-DD" into "YYYY-MM-DD HH:MM:SS"."""
    if date_time:
        parts = str(date_time).strip().split()
        if len(parts) == 2:
            first, second = parts
            if "-" in second:  # "23:59:57 2026-07-15"
                return f"{second} {first}"
            if "-" in first:  # already "2026-07-15 23:59:57"
                return f"{first} {second}"
    if date:
        return f"{str(date).strip()} 00:00:00"
    return None


def _has_recent_activity(records: list[dict]) -> bool:
    """True when any record started within the last 12 hours."""
    cutoff = datetime.now() - timedelta(hours=12)
    for record in records:
        start = record.get("start_time")
        if not start:
            continue
        try:
            if datetime.fromisoformat(str(start)) >= cutoff:
                return True
        except ValueError:
            continue
    return False


# ── Collectors ────────────────────────────────────────────────────────


def collect_from_sessions(limit: int) -> list[dict]:
    """Read sessions.json of every account folder."""
    folder = accounts_dir()
    if not folder.is_dir():
        raise CustomerStatsError(f"Accounts-Ordner nicht gefunden: {folder}")

    accounts = []
    for entry in sorted(folder.iterdir()):
        if not entry.is_dir():
            continue

        sessions_file = entry / "sessions.json"
        if not sessions_file.exists():
            logger.warning(f"Keine sessions.json für {entry.name}")
            continue

        try:
            data = json.loads(sessions_file.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            logger.error(f"sessions.json von {entry.name} nicht lesbar: {e}")
            continue

        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            logger.error(f"Unerwartetes Format in sessions.json von {entry.name}")
            continue

        records = []
        for session in data:
            if not isinstance(session, dict):
                continue
            args = session.get("args") or {}
            profile = session.get("profile") or {}
            records.append(
                {
                    "id": session.get("id"),
                    "start_time": session.get("start_time"),
                    "total_interactions": session.get("total_interactions"),
                    "successful_interactions": session.get("successful_interactions"),
                    "total_followed": session.get("total_followed"),
                    "total_likes": session.get("total_likes"),
                    "total_unfollowed": session.get("total_unfollowed"),
                    "total_pm": session.get("total_pm"),
                    "total_watched": session.get("total_watched"),
                    "device": args.get("device"),
                    "posts": profile.get("posts"),
                    "followers": profile.get("followers"),
                    "following": profile.get("following"),
                    "total_scraped": session.get("total_scraped"),
                    "args": args or None,
                    "profile": profile or None,
                }
            )

        accounts.append(
            {
                "username": entry.name,
                "device": _device_from_config_yml(entry / "config.yml"),
                # The 12h check looks at every session, not just the uploaded slice
                "has_recent": _has_recent_activity(records),
                "records": records[-limit:],
            }
        )

    logger.info(f"sessions: {len(accounts)} Accounts eingelesen aus {folder}")
    return accounts


def collect_from_superdb(limit: int) -> list[dict]:
    """Read the `stats` table of the mac's own super.db."""
    config = get_config()
    db_path = Path(config.sqlite_db_path).expanduser()
    if not db_path.exists():
        raise CustomerStatsError(f"super.db nicht gefunden: {db_path}")

    temp_path = Path(f"/tmp/customer_stats_{datetime.now():%Y%m%d_%H%M%S}.db")
    shutil.copy2(db_path, temp_path)

    try:
        conn = sqlite3.connect(temp_path)
        try:
            stats_cols = {row[1] for row in conn.execute("PRAGMA table_info(stats);")}
            if not stats_cols:
                raise CustomerStatsError("Tabelle 'stats' existiert nicht in super.db")

            profile_cols = {row[1] for row in conn.execute("PRAGMA table_info(profile);")}
            if "config__username" not in profile_cols:
                raise CustomerStatsError(
                    "Tabelle 'profile' (mit config__username) fehlt in super.db — "
                    "ohne sie lässt sich kein Benutzername zuordnen"
                )

            selected = [c for c in SUPERDB_STATS_COLUMNS if c in stats_cols]
            parts = [f's."{c}" AS "s_{c}"' for c in selected]
            parts.append('p."config__username" AS "p_username"')
            if "config__device" in profile_cols:
                parts.append('p."config__device" AS "p_device"')

            query = (
                f'SELECT {", ".join(parts)} FROM stats s '
                'LEFT JOIN profile p ON p."id" = s."profileID"'
            )
            if "date" in stats_cols:
                query += " WHERE s.\"date\" >= date('now', '-90 days')"

            cursor = conn.execute(query)
            columns = [d[0] for d in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            conn.close()
    finally:
        try:
            temp_path.unlink()
        except OSError as e:
            logger.debug(f"Temp-DB konnte nicht gelöscht werden: {e}")

    grouped: dict[str, dict] = {}
    for row in rows:
        username = (row.get("p_username") or "").strip()
        if not username:
            continue

        record = {
            "id": row.get("s_id"),
            "start_time": _superdb_start_time(row.get("s_dateTime"), row.get("s_date")),
            "total_interactions": _to_int(row.get("s_interaction")),
            "successful_interactions": None,
            "total_followed": _to_int(row.get("s_follow")),
            "total_likes": _to_int(row.get("s_like")),
            "total_unfollowed": _to_int(row.get("s_unfollow")),
            "total_pm": _to_int(row.get("s_dm")),
            "total_watched": _to_int(row.get("s_watch")),
            "device": row.get("p_device"),
            "posts": None,
            "followers": _to_int(row.get("s_followers")),
            "following": _to_int(row.get("s_followings")),
            "total_scraped": None,
            "args": None,
            "profile": None,
        }

        account = grouped.setdefault(
            username, {"username": username, "device": row.get("p_device"), "records": []}
        )
        account["records"].append(record)
        if not account["device"] and row.get("p_device"):
            account["device"] = row.get("p_device")

    accounts = []
    for username in sorted(grouped):
        account = grouped[username]
        # Newest last, same ordering the sessions source produces
        account["records"].sort(key=lambda r: str(r.get("start_time") or ""))
        account["has_recent"] = _has_recent_activity(account["records"])
        account["records"] = account["records"][-limit:]
        accounts.append(account)

    logger.info(f"super.db: {len(accounts)} Accounts aus {len(rows)} stats-Zeilen")
    return accounts


# ── Row builders (rules taken over from the legacy script) ────────────


def _enforce_minimum_or_random(value, minimum: int, random_range: tuple[int, int]):
    """Replace missing/too-low counters with a plausible random value."""
    if value is None or value < minimum:
        return random.randint(*random_range)
    return value


def _session_id(raw_id, source: str, server: str) -> str | None:
    """Build the session id.

    sessions.json ids are UUIDs and stay untouched. super.db ids are small
    integers that repeat across macs, so they get the server prefix the rest of
    the tool uses.
    """
    if raw_id in (None, ""):
        return None
    raw = str(raw_id)
    if source == "superdb" and not raw.startswith(f"{server}_"):
        return f"{server}_{raw}"
    return raw


def build_stats_row(username: str, record: dict, session_id: str, server: str) -> dict:
    """Raw row for the `statistik` table."""
    return {
        "username": username,
        "session_id": session_id,
        "start_time": record.get("start_time"),
        "total_interactions": record.get("total_interactions"),
        "successful_interactions": record.get("successful_interactions"),
        "total_followed": record.get("total_followed"),
        "total_likes": record.get("total_likes"),
        "total_unfollowed": record.get("total_unfollowed"),
        "total_pm": record.get("total_pm"),
        "total_watched": record.get("total_watched"),
        "device": record.get("device"),
        "posts": record.get("posts"),
        "followers": record.get("followers"),
        "following": record.get("following"),
        "total_scraped": record.get("total_scraped"),
        "args": record.get("args"),
        "profile": record.get("profile"),
        "imported_at": datetime.now().isoformat(),
        "serverzuordnung": server,
    }


def build_users_row(
    username: str, record: dict, session_id: str, fallback_device: str | None, server: str
) -> dict:
    """Beautified row for the `users` table."""
    raw_followed = record.get("total_followed")
    raw_likes = record.get("total_likes")
    raw_unfollowed = record.get("total_unfollowed")

    # A session that only liked (no follow/unfollow configured) keeps its zeros
    if raw_followed == 0 and raw_unfollowed == 0 and raw_likes not in (None, 0):
        total_followed = 0
        total_unfollowed = 0
    else:
        total_followed = _enforce_minimum_or_random(raw_followed, 20, (40, 50))
        total_unfollowed = _enforce_minimum_or_random(raw_unfollowed, 20, (40, 50))
    total_likes = _enforce_minimum_or_random(raw_likes, 60, (100, 120))

    scraped = record.get("total_scraped")
    total_scraped = ", ".join(scraped.keys()) if isinstance(scraped, dict) else ""

    row = {
        "username": username,
        "session_id": session_id,
        "start_time": record.get("start_time"),
        "total_interactions": record.get("total_interactions") or 0,
        "successful_interactions": record.get("successful_interactions") or 0,
        "total_followed": total_followed,
        "total_likes": total_likes,
        "total_unfollowed": total_unfollowed,
        "total_pm": record.get("total_pm") or 0,
        "total_watched": record.get("total_watched") or 0,
        "device": record.get("device") or fallback_device,
        "posts": record.get("posts") or 0,
        "followers": record.get("followers") or 0,
        "following": record.get("following") or 0,
        "total_scraped": total_scraped,
        "imported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "serverzuordnung": server,
    }
    return {k: v for k, v in row.items() if v is not None}


def build_missing_users_row(username: str, device: str, server: str) -> dict:
    """Synthetic row for an account without a session in the last 12 hours."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "username": username,
        "session_id": f"missing-{username}-{datetime.now():%Y%m%d%H%M%S%f}",
        "start_time": timestamp,
        "total_interactions": 0,
        "successful_interactions": 0,
        "total_followed": random.randint(40, 50),
        "total_likes": random.randint(100, 120),
        "total_unfollowed": random.randint(40, 50),
        "total_pm": 0,
        "total_watched": 0,
        "device": device,
        "posts": 0,
        "followers": 0,
        "following": 0,
        "total_scraped": "",
        "imported_at": timestamp,
        "serverzuordnung": server,
    }


def _filter_columns(row: dict, available: set[str]) -> dict:
    """Drop keys the target table doesn't have (unknown schema = send all)."""
    if not available:
        return row
    return {k: v for k, v in row.items() if k in available}


def _missing_rows_still_needed(
    api: SupabaseRest, table: str, missing_rows: list[dict]
) -> list[dict]:
    """Keep at most one synthetic entry per account per day.

    The legacy script ran once a night, so one missing entry per day was implicit.
    We run at every sync time, so without this check an inactive account would
    collect several fake sessions a day.
    """
    if not missing_rows:
        return []

    today = datetime.now().strftime("%Y-%m-%d")
    try:
        rows = api.select(
            table,
            {
                "select": "username",
                "session_id": "like.missing-*",
                "start_time": f"gte.{today} 00:00:00",
                "limit": "2000",
            },
        )
        already = {row.get("username") for row in rows}
    except Exception as e:
        logger.warning(
            f"Prüfung auf bereits vorhandene Missing-Einträge fehlgeschlagen "
            f"({e}) — es werden alle geschrieben"
        )
        return missing_rows

    remaining = [row for row in missing_rows if row["username"] not in already]
    if len(remaining) != len(missing_rows):
        logger.info(
            f"Missing-Einträge: {len(missing_rows) - len(remaining)} bereits heute vorhanden"
        )
    return remaining


# ── Main entry point ──────────────────────────────────────────────────


def run_customer_stats_upload(
    trigger: str = "auto", dry_run: bool = False, sample_size: int = 3
) -> dict:
    """Collect session statistics and push them to the customer Supabase."""
    config = get_config()
    server = config.server_name
    source = config.customer_stats_source or "sessions"
    limit = max(1, int(config.customer_stats_session_limit or 90))

    logger.info("=" * 60)
    logger.info(
        f"Statistik (Kunde): Start (Quelle={source}, Limit={limit}, "
        f"Trigger={trigger}, Dry-Run={dry_run})"
    )

    result = {
        "status": "success",
        "source": source,
        "server": server,
        "trigger": trigger,
        "accounts": 0,
        "sessions": 0,
        "missing_sessions": 0,
        "statistik": {"status": "not_configured"},
        "users": {"status": "not_configured"},
    }

    try:
        if source == "superdb":
            accounts = collect_from_superdb(limit)
        else:
            accounts = collect_from_sessions(limit)
    except CustomerStatsError as e:
        logger.error(f"Statistik (Kunde): {e}")
        result["status"] = "error"
        result["error"] = str(e)
        run_state.record_run("customer_stats", "error", str(e), trigger)
        return result
    except Exception as e:
        logger.error(f"Statistik (Kunde): Datenerfassung fehlgeschlagen: {e}", exc_info=True)
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {e}"
        run_state.record_run("customer_stats", "error", str(e), trigger)
        return result

    stats_rows: list[dict] = []
    users_rows: list[dict] = []
    missing_rows: list[dict] = []

    for account in accounts:
        username = account["username"]
        for record in account["records"]:
            session_id = _session_id(record.get("id"), source, server)
            if not session_id:
                continue
            stats_rows.append(build_stats_row(username, record, session_id, server))
            users_rows.append(
                build_users_row(username, record, session_id, account.get("device"), server)
            )

        if not account["has_recent"] and account.get("device"):
            missing_rows.append(
                build_missing_users_row(username, account["device"], server)
            )

    result["accounts"] = len(accounts)
    result["sessions"] = len(stats_rows)
    result["missing_sessions"] = len(missing_rows)

    logger.info(
        f"Statistik (Kunde): {len(accounts)} Accounts, {len(stats_rows)} Sessions, "
        f"{len(missing_rows)} Missing-Einträge"
    )

    if dry_run:
        result["status"] = "dry_run"
        result["preview"] = {
            "statistik": stats_rows[:sample_size],
            "users": users_rows[:sample_size],
            "missing": missing_rows[:sample_size],
        }
        if source == "superdb":
            result["unavailable_fields"] = SUPERDB_UNAVAILABLE_FIELDS
        return result

    # ── Target A: raw statistik (upsert) ──
    stats_api = SupabaseRest(config.customer_stats_url, config.customer_stats_key)
    if stats_api.configured:
        table = config.customer_stats_table
        try:
            available = stats_api.columns(table)
            payload = [_filter_columns(row, available) for row in stats_rows]
            outcome = stats_api.upsert_many(table, payload, on_conflict="session_id")
            result["statistik"] = {
                "status": "success" if not outcome["failed"] else "partial",
                "table": table,
                **outcome,
            }
            logger.info(
                f"Statistik (Kunde) → {table}: {outcome['written']} geschrieben, "
                f"{outcome['failed']} fehlgeschlagen"
            )
        except Exception as e:
            logger.error(f"Upload nach '{table}' fehlgeschlagen: {e}")
            result["statistik"] = {"status": "error", "table": table, "error": str(e)[:400]}

    # ── Target B: beautified users (insert) ──
    users_api = SupabaseRest(config.customer_users_url, config.customer_users_key)
    if users_api.configured:
        table = config.customer_users_table
        try:
            available = users_api.columns(table)

            session_ids = [row["session_id"] for row in users_rows if row.get("session_id")]
            existing = users_api.select_existing(table, "session_id", session_ids)
            new_rows = [r for r in users_rows if r.get("session_id") not in existing]

            pending_missing = _missing_rows_still_needed(users_api, table, missing_rows)

            payload = [
                _filter_columns(row, available) for row in new_rows + pending_missing
            ]
            outcome = users_api.insert_many(table, payload)
            result["users"] = {
                "status": "success" if not outcome["failed"] else "partial",
                "table": table,
                "skipped_existing": len(users_rows) - len(new_rows),
                "missing_written": len(pending_missing),
                **outcome,
            }
            logger.info(
                f"Statistik (Kunde) → {table}: {outcome['written']} neu, "
                f"{len(users_rows) - len(new_rows)} bereits vorhanden, "
                f"{outcome['failed']} fehlgeschlagen"
            )
        except Exception as e:
            logger.error(f"Upload nach '{table}' fehlgeschlagen: {e}")
            result["users"] = {"status": "error", "table": table, "error": str(e)[:400]}

    statuses = [result["statistik"]["status"], result["users"]["status"]]
    if "error" in statuses or "partial" in statuses:
        result["status"] = "partial"
    elif all(status == "not_configured" for status in statuses):
        result["status"] = "not_configured"
        logger.warning("Statistik (Kunde): Kein Ziel konfiguriert (URL/Key fehlen)")

    summary = (
        f"{result['sessions']} Sessions, "
        f"statistik={result['statistik'].get('written', result['statistik']['status'])}, "
        f"users={result['users'].get('written', result['users']['status'])}"
    )
    run_state.record_run("customer_stats", result["status"], summary, trigger)

    # Only retire the legacy LaunchAgent once our own upload actually worked
    if result["status"] == "success" and config.auto_disable_legacy_upload:
        try:
            from legacy_upload import auto_disable_after_success

            legacy = auto_disable_after_success()
            if legacy.get("disabled"):
                result["legacy_upload"] = legacy
        except Exception as e:
            logger.warning(f"Altes Upload-Skript konnte nicht deaktiviert werden: {e}")

    logger.info(f"Statistik (Kunde): Fertig — {summary}")
    logger.info("=" * 60)
    return result


def check_customer_target(target: str) -> dict:
    """Connection test for one of the customer targets."""
    config = get_config()

    if target == "customer_stats":
        return describe_target(
            config.customer_stats_url,
            config.customer_stats_key,
            config.customer_stats_table,
            STATS_SCHEMA,
            unique_column="session_id",
        )
    if target == "customer_users":
        return describe_target(
            config.customer_users_url,
            config.customer_users_key,
            config.customer_users_table,
            USERS_SCHEMA,
        )
    raise ValueError(f"Unbekanntes Ziel: {target}")
