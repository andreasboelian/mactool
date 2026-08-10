"""Row parity: our output must match what upload-macXX.py produced.

The legacy row-building logic is copied verbatim below, so any behavioural drift
shows up as a diff instead of being noticed months later in the customer's data.
"""
import json, random, sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import customer_stats as cs

SERVER = "mac17"
FOLDER = Path("GramBotStorage/accounts")

failures = []


def check(name, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + name + ("" if condition else f" :: {detail}"))
    if not condition:
        failures.append(name)


# ── Legacy logic, copied from upload-mac17.py ────────────────────────

def legacy_enforce(field_value, min_value, random_range):
    if field_value is None or field_value < min_value:
        return random.randint(*random_range)
    return field_value


def legacy_has_sessions_in_last_12_hours(data):
    twelve_hours_ago = datetime.now() - timedelta(hours=12)
    for session in data:
        start_time_str = session.get("start_time")
        if start_time_str:
            try:
                start_time = datetime.fromisoformat(start_time_str)
            except Exception:
                continue
            if start_time >= twelve_hours_ago:
                return True
    return False


def legacy_rows(user_folder, data, device_from_config):
    """(users_rows, stats_rows) exactly as the legacy script would build them."""
    users_rows, stats_rows = [], []
    for session in data[-90:]:
        session_id = session.get("id")
        start_time = session.get("start_time")

        raw_followed = session.get("total_followed")
        raw_likes = session.get("total_likes")
        raw_unfollowed = session.get("total_unfollowed")

        if raw_followed == 0 and raw_unfollowed == 0 and raw_likes not in (None, 0):
            total_followed_users = 0
            total_unfollowed_users = 0
        else:
            total_followed_users = legacy_enforce(raw_followed, 20, (40, 50))
            total_unfollowed_users = legacy_enforce(raw_unfollowed, 20, (40, 50))
        total_likes_users = legacy_enforce(raw_likes, 60, (100, 120))

        args = session.get("args") or {}
        profile = session.get("profile") or {}
        total_scraped_dict = session.get("total_scraped") or {}

        row = {
            'username': user_folder,
            'session_id': session_id,
            'start_time': start_time,
            'total_interactions': session.get("total_interactions") or 0,
            'successful_interactions': session.get("successful_interactions") or 0,
            'total_followed': total_followed_users,
            'total_likes': total_likes_users,
            'total_unfollowed': total_unfollowed_users,
            'total_pm': session.get("total_pm") or 0,
            'total_watched': session.get("total_watched") or 0,
            'device': args.get("device") or device_from_config,
            'posts': profile.get("posts") or 0,
            'followers': profile.get("followers") or 0,
            'following': profile.get("following") or 0,
            'total_scraped': ", ".join(total_scraped_dict.keys()),
            'serverzuordnung': 'mac17',
        }
        users_rows.append({k: v for k, v in row.items() if v is not None})

        stats_rows.append({
            "username": user_folder,
            "session_id": session_id,
            "start_time": start_time,
            "total_interactions": session.get("total_interactions"),
            "successful_interactions": session.get("successful_interactions"),
            "total_followed": session.get("total_followed"),
            "total_likes": session.get("total_likes"),
            "total_unfollowed": session.get("total_unfollowed"),
            "total_pm": session.get("total_pm"),
            "total_watched": session.get("total_watched"),
            "device": (session.get("args") or {}).get("device"),
            "posts": (session.get("profile") or {}).get("posts"),
            "followers": (session.get("profile") or {}).get("followers"),
            "following": (session.get("profile") or {}).get("following"),
            "total_scraped": session.get("total_scraped"),
            "args": session.get("args"),
            "profile": session.get("profile"),
            "serverzuordnung": "mac17",
        })
    return users_rows, stats_rows


# ── 1. sessions source ───────────────────────────────────────────────

print("\n[1] sessions.json als Quelle")
accounts = cs.collect_from_sessions(90)
by_name = {a["username"]: a for a in accounts}

check("beide Account-Ordner gefunden", set(by_name) == {"creatif_dr", "leer_account"}, str(set(by_name)))
check("device aus config.yml gelesen", by_name["creatif_dr"]["device"] == "ba56df78",
      repr(by_name["creatif_dr"]["device"]))
check("device mit Quotes+Kommentar geparst", by_name["leer_account"]["device"] == "R58WB00CHYM",
      repr(by_name["leer_account"]["device"]))

raw = json.loads((FOLDER / "creatif_dr" / "sessions.json").read_text())
check("Session-Limit greift", len(by_name["creatif_dr"]["records"]) == min(90, len(raw)),
      f'{len(by_name["creatif_dr"]["records"])} vs {len(raw)}')
check("has_recent identisch zur Legacy-Logik",
      by_name["creatif_dr"]["has_recent"] == legacy_has_sessions_in_last_12_hours(raw))

# Same seed, same order of random calls → rows must be identical
random.seed(1234)
legacy_users, legacy_stats = legacy_rows("creatif_dr", raw, "ba56df78")

random.seed(1234)
account = by_name["creatif_dr"]
new_users, new_stats = [], []
for record in account["records"]:
    sid = cs._session_id(record.get("id"), "sessions", SERVER)
    new_users.append(cs.build_users_row("creatif_dr", record, sid, account["device"], SERVER))
    new_stats.append(cs.build_stats_row("creatif_dr", record, sid, SERVER))


def strip_ts(rows):
    return [{k: v for k, v in r.items() if k != "imported_at"} for r in rows]


check(f"users-Rows identisch ({len(new_users)} Sessions)",
      strip_ts(new_users) == strip_ts(legacy_users),
      str([(a, b) for a, b in zip(strip_ts(new_users), strip_ts(legacy_users)) if a != b][:1]))
check("statistik-Rows identisch", strip_ts(new_stats) == strip_ts(legacy_stats),
      str([(a, b) for a, b in zip(strip_ts(new_stats), strip_ts(legacy_stats)) if a != b][:1]))

check("users.imported_at Format 'YYYY-MM-DD HH:MM:SS'",
      len(new_users[0]["imported_at"]) == 19 and new_users[0]["imported_at"][10] == " ",
      new_users[0]["imported_at"])
check("statistik.imported_at ist ISO", "T" in new_stats[0]["imported_at"], new_stats[0]["imported_at"])
check("session_id bleibt bei sessions unveraendert",
      new_stats[0]["session_id"] == account["records"][0]["id"], new_stats[0]["session_id"])
check("statistik.total_scraped bleibt dict/None",
      isinstance(new_stats[0]["total_scraped"], (dict, type(None))),
      type(new_stats[0]["total_scraped"]).__name__)
check("users.total_scraped ist Komma-String",
      isinstance(new_users[0]["total_scraped"], str), type(new_users[0]["total_scraped"]).__name__)

# Missing-session row parity
random.seed(99)
legacy_missing = {
    'total_followed': random.randint(40, 50),
    'total_likes': random.randint(100, 120),
    'total_unfollowed': random.randint(40, 50),
}
random.seed(99)
mine = cs.build_missing_users_row("leer_account", "R58WB00CHYM", SERVER)
check("Missing-Row Zufallswerte identisch",
      all(mine[k] == v for k, v in legacy_missing.items()),
      f"{ {k: mine[k] for k in legacy_missing} } vs {legacy_missing}")
check("Missing-Row session_id Praefix", mine["session_id"].startswith("missing-leer_account-"),
      mine["session_id"])
check("leerer Account hat has_recent=False", by_name["leer_account"]["has_recent"] is False)

# ── 2. super.db source ───────────────────────────────────────────────

print("\n[2] super.db als Quelle")
sdb = cs.collect_from_superdb(90)
sdb_by_name = {a["username"]: a for a in sdb}

check("nur Accounts mit Username", set(sdb_by_name) == {"creatif_dr", "zweiter_account"},
      str(set(sdb_by_name)))
check("creatif_dr hat 3 stats-Zeilen", len(sdb_by_name["creatif_dr"]["records"]) == 3,
      str(len(sdb_by_name["creatif_dr"]["records"])))

records = sdb_by_name["creatif_dr"]["records"]
check("chronologisch sortiert (neueste zuletzt)",
      [r["start_time"] for r in records] == sorted(r["start_time"] for r in records))

old = next(r for r in records if str(r["start_time"]).startswith("2026-07-15"))
check('"13/50" → 13', old["total_followed"] == 13, str(old["total_followed"]))
check('"24/140" → 24', old["total_unfollowed"] == 24, str(old["total_unfollowed"]))
check('"26/120" → 26', old["total_likes"] == 26, str(old["total_likes"]))
check("dateTime umgedreht zu 'YYYY-MM-DD HH:MM:SS'", old["start_time"] == "2026-07-15 23:59:57",
      old["start_time"])
check("device aus profile.config__device", old["device"] == "ba56df78", str(old["device"]))
check("nicht verfuegbare Felder sind None",
      all(old[f] is None for f in ("successful_interactions", "posts", "total_scraped", "args", "profile")))

nill = sdb_by_name["zweiter_account"]["records"][0]
check('"nill" → None', nill["followers"] is None and nill["following"] is None,
      f'{nill["followers"]}/{nill["following"]}')

check("has_recent erkennt frische Zeile", sdb_by_name["creatif_dr"]["has_recent"] is True)
check("has_recent False ohne frische Zeile", sdb_by_name["zweiter_account"]["has_recent"] is False)

sid = cs._session_id(old["id"], "superdb", SERVER)
check("super.db session_id bekommt Server-Praefix", sid == "mac17_403480", sid)
check("Praefix wird nicht doppelt gesetzt",
      cs._session_id("mac17_403480", "superdb", SERVER) == "mac17_403480")

srow = cs.build_stats_row("creatif_dr", old, sid, SERVER)
check("statistik-Row aus super.db hat alle Spalten",
      set(srow) == set(cs.STATS_SCHEMA), str(set(cs.STATS_SCHEMA) ^ set(srow)))

urow = cs.build_users_row("creatif_dr", old, sid, "ba56df78", SERVER)
check("users-Row aus super.db verschoenert Likes", urow["total_likes"] >= 60, str(urow["total_likes"]))
check("users-Row aus super.db hat keine None-Werte", all(v is not None for v in urow.values()))

# ── 3. Zahlen-Parser ─────────────────────────────────────────────────

print("\n[3] _to_int")
for value, expected in [
    ("13/50", 13), ("26", 26), ("nill", None), ("undefined", None), ("", None),
    (None, None), ("0", 0), ("0/0", 0), (7, 7), ("  12  ", 12), ("abc", None), ("3.0", 3),
]:
    check(f"_to_int({value!r}) == {expected!r}", cs._to_int(value) == expected, repr(cs._to_int(value)))

print("\n" + ("ALLE TESTS BESTANDEN" if not failures else f"{len(failures)} FEHLGESCHLAGEN: {failures}"))
sys.exit(1 if failures else 0)
