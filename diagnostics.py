"""Warum landen keine Bot-Logs im Bucket?

Der Upload in `log_uploader.py` ist eine Kette aus sieben Filtern. Fällt einer davon
auf null, passiert schlicht nichts — ohne Fehler, ohne Meldung, ohne Spur. Am Mac
sieht man das nicht, und aus der Ferne bisher erst recht nicht.

Dieses Modul geht dieselbe Kette Glied für Glied ab und sagt bei jedem, wie viele
durchkommen. Es ist **rein lesend**: es lädt nichts hoch, schreibt keinen Zustand
und verschickt keine Mail.

Die Filter werden bewusst aus `log_uploader` importiert statt nachgebaut. Eine
nachgebaute Diagnose driftet vom echten Code weg und behauptet irgendwann, alles
sei in Ordnung, während der Upload etwas anderes tut.
"""

import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import db_integrity
import run_state
from config import get_config
from log_uploader import (
    BUCKET_NAME,
    _build_upload_path,
    _discover_log_files,
    _get_allowed_usernames,
    _get_previous_timeslot,
    _parse_log_timestamp,
    _previous_window_start_hour,
    _slot_covers_window,
)

logger = logging.getLogger(__name__)

# Wie viele Beispiele je Auffälligkeit — genug zum Erkennen des Musters,
# wenig genug für eine lesbare Antwort über die Befehlsqueue.
SAMPLE_SIZE = 8
BUCKET_LIST_LIMIT = 20


def _stat(path: Path) -> dict:
    """Größe und Änderungsdatum einer Datei, ohne bei Fehlern zu werfen."""
    try:
        info = path.stat()
        return {
            "bytes": info.st_size,
            "modified": datetime.fromtimestamp(info.st_mtime).isoformat(timespec="seconds"),
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _copy_db(db_path: Path) -> Path:
    """Arbeitskopie der super.db anlegen — wie im Sync (sync.py `_create_temp_db`).

    Nie auf dem Original arbeiten: der Bot schreibt parallel weiter, und eine
    Leseabfrage auf eine WAL-Datenbank kann ihn ausbremsen.
    """
    temp_path = Path(f"/tmp/diag_{datetime.now():%Y%m%d_%H%M%S}.db")
    shutil.copy2(db_path, temp_path)
    return temp_path


def _profile_counts(db_path: Path) -> dict:
    """Gegenprobe zu den erlaubten Accounts: woran scheitert der Filter?

    `_get_allowed_usernames` liefert nur eine Zahl. Wenn die null ist, muss man
    wissen *warum* — kein Gerät heißt "Phone", oder die Profile haben keinen
    Benutzernamen, oder es gibt schlicht keine Geräte.
    """
    counts = {}
    try:
        conn = sqlite3.connect(db_path)
        try:
            def scalar(sql: str) -> int:
                return conn.execute(sql).fetchone()[0]

            counts["devices"] = scalar("SELECT COUNT(*) FROM device")
            counts["devices_named_phone"] = scalar(
                "SELECT COUNT(*) FROM device WHERE LOWER(customName) LIKE '%phone%'"
            )
            counts["profiles"] = scalar("SELECT COUNT(*) FROM profile")
            counts["profiles_with_username"] = scalar(
                "SELECT COUNT(*) FROM profile "
                "WHERE config__username IS NOT NULL AND config__username != ''"
            )
            counts["profiles_on_phone_device"] = scalar(
                "SELECT COUNT(*) FROM profile p JOIN device d ON p.config__device = d.id "
                "WHERE LOWER(d.customName) LIKE '%phone%'"
            )
            counts["device_names"] = [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT customName FROM device "
                    "WHERE customName IS NOT NULL AND customName != '' "
                    f"ORDER BY customName LIMIT {SAMPLE_SIZE}"
                )
            ]
        finally:
            conn.close()
    except Exception as e:
        counts["error"] = f"{type(e).__name__}: {e}"
    return counts


def _timeslot_matches(db_path: Path, slot: str) -> dict:
    """Wie viele Profile decken das zuletzt abgelaufene 2h-Fenster ab?

    Ist das 0, während es sehr wohl erlaubte Accounts gibt, lädt der automatische
    Sync nie etwas hoch — der manuelle Knopf im Dashboard dagegen schon. Ein
    Fehlerbild, das man am Mac nie zu Gesicht bekommt.
    """
    result = {"slot": slot}
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT LOWER(p.config__username), p.[startup_time__time_slot] "
                "FROM profile p JOIN device d ON p.config__device = d.id "
                "WHERE LOWER(d.customName) LIKE '%phone%' "
                "  AND p.config__username IS NOT NULL AND p.config__username != '' "
                "  AND p.[startup_time__time_slot] IS NOT NULL "
                "  AND p.[startup_time__time_slot] != ''"
            ).fetchall()
        finally:
            conn.close()

        window_start_hour = _previous_window_start_hour()
        result["profiles_with_timeslot"] = len(rows)
        matching = [
            username
            for username, slots in rows
            if _slot_covers_window(slots or "", window_start_hour)
        ]
        result["matching"] = len(matching)
        result["sample"] = sorted(matching)[:SAMPLE_SIZE]
        if rows and not matching:
            result["configured_slots"] = sorted({slots for _, slots in rows if slots})[
                :SAMPLE_SIZE
            ]
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def _bucket_state(server_name: str) -> dict:
    """Ist der Bucket erreichbar, und wann kam dort zuletzt etwas an?

    Beantwortet die Frage "hat es je funktioniert und wann hörte es auf" — die
    Dateinamen tragen das Datum im Namen (`YYYY-MM-DD_HHMM_username.log`).
    """
    from device_monitor import _get_sb_client

    client = _get_sb_client()
    if client is None:
        return {"reachable": False, "error": "kein Supabase-Client (Key fehlt?)"}

    state: dict = {"bucket": BUCKET_NAME, "prefix": server_name}
    try:
        client.storage.get_bucket(BUCKET_NAME)
        state["bucket_exists"] = True
    except Exception as e:
        state["bucket_exists"] = False
        state["bucket_error"] = f"{type(e).__name__}: {str(e)[:200]}"

    try:
        files = client.storage.from_(BUCKET_NAME).list(
            server_name,
            options={
                "limit": BUCKET_LIST_LIMIT,
                "sortBy": {"column": "name", "order": "desc"},
            },
        )
        names = [f.get("name", "") for f in (files or [])]
        state["reachable"] = True
        state["objects_listed"] = len(names)
        state["newest"] = names[:SAMPLE_SIZE]
        # Der Dateiname beginnt mit dem Datum — das ist der Tag, an dem zuletzt
        # etwas ankam. Genau die Zahl, die man mit "seit wann fehlt es" vergleicht.
        dates = sorted({n[:10] for n in names if len(n) >= 10})
        if dates:
            state["newest_date"] = dates[-1]
            state["oldest_date"] = dates[0]
    except Exception as e:
        state["reachable"] = False
        state["error"] = f"{type(e).__name__}: {str(e)[:200]}"

    return state


def diagnose_log_upload() -> dict:
    """Die komplette Upload-Kette durchgehen und das erste blockierende Glied nennen.

    Rückgabe:
        verdict  — eine Zeile Klartext: woran liegt es
        blocking — Liste der Schritte, die den Upload verhindern
        steps    — die Rohdaten je Schritt, zum Vergleich zwischen zwei Macs
    """
    config = get_config()
    steps: dict = {}
    blocking: list[str] = []
    temp_db: Path | None = None

    def block(step: str, message: str) -> None:
        blocking.append(step)
        steps[step]["verdict"] = message

    try:
        # ── 1. Konfiguration ──────────────────────────────────────────
        db_path = Path(config.sqlite_db_path).expanduser()
        steps["config"] = {
            "server_name": config.server_name,
            "sqlite_db_path": str(db_path),
            "supabase_url": config.supabase_url,
            "supabase_key_set": bool(config.supabase_key),
            "db_integrity_check_enabled": config.db_integrity_check_enabled,
            "sync_times": config.sync_times,
        }
        if not config.supabase_key:
            block("config", "Kein Supabase-Key hinterlegt — es kann nichts hochgeladen werden.")

        # ── 2. Datenbank ──────────────────────────────────────────────
        steps["database"] = {"path": str(db_path), "exists": db_path.exists()}
        if not db_path.exists():
            block("database", f"Die super.db fehlt: {db_path}")
        else:
            steps["database"].update(_stat(db_path))
            # Seit v1.0.114 bricht der Sync bei beschädigter DB *vor* dem
            # Log-Upload ab. Eine kaputte Datei ist damit eine mögliche Ursache.
            integrity = db_integrity.check_database(db_path)
            steps["database"]["integrity_ok"] = integrity["ok"]
            steps["database"]["integrity_detail"] = integrity["detail"]
            if not integrity["ok"] and config.db_integrity_check_enabled:
                block(
                    "database",
                    "super.db ist beschädigt — der Sync bricht ab, bevor Logs hochgeladen werden.",
                )

        # ── 3. Logverzeichnis ─────────────────────────────────────────
        log_files: list[Path] = []
        if db_path.exists():
            logs_dir = db_path.parent / "logs"
            steps["log_dir"] = {"path": str(logs_dir), "exists": logs_dir.exists()}
            if not logs_dir.exists():
                block("log_dir", f"Kein Logverzeichnis: {logs_dir} — der Bot legt dort nichts ab.")
            else:
                log_files = _discover_log_files(db_path.parent)
                stats = [(f, _stat(f)) for f in log_files]
                modified = sorted(
                    s["modified"] for _, s in stats if "modified" in s
                )
                steps["log_dir"].update(
                    {
                        "files": len(log_files),
                        "total_bytes": sum(s.get("bytes", 0) for _, s in stats),
                        "newest_modified": modified[-1] if modified else None,
                        "oldest_modified": modified[0] if modified else None,
                        "sample": [f.name for f in log_files[:SAMPLE_SIZE]],
                    }
                )
                if not log_files:
                    block("log_dir", f"{logs_dir} ist leer — es gibt nichts hochzuladen.")

        # Ohne lesbare Datenbank sind die folgenden Schritte nicht auswertbar
        if not db_path.exists():
            steps["verdict_note"] = "Weitere Schritte übersprungen: keine Datenbank."
            return _finish(config, steps, blocking)

        temp_db = _copy_db(db_path)

        # ── 4. Erlaubte Accounts ──────────────────────────────────────
        allowed = _get_allowed_usernames(temp_db, upload_all=True)
        steps["allowed_usernames"] = {
            "count": len(allowed),
            "sample": sorted(allowed)[:SAMPLE_SIZE],
            "counts": _profile_counts(temp_db),
        }
        if not allowed:
            counts = steps["allowed_usernames"]["counts"]
            if counts.get("devices_named_phone") == 0:
                reason = (
                    f"kein Gerät hat 'Phone' im Namen "
                    f"(gefundene Namen: {counts.get('device_names') or 'keine'})"
                )
            elif counts.get("profiles_with_username") == 0:
                reason = "kein Profil hat einen Benutzernamen"
            else:
                reason = "kein Profil hängt an einem Gerät mit 'Phone' im Namen"
            block(
                "allowed_usernames",
                f"Kein Account ist für den Upload freigegeben — {reason}.",
            )

        # ── 5. Zeitfenster (betrifft nur den automatischen Sync) ──────
        slot = _get_previous_timeslot()
        steps["timeslot"] = _timeslot_matches(temp_db, slot)
        if allowed and steps["timeslot"].get("matching") == 0:
            block(
                "timeslot",
                f"Kein Profil deckt das Zeitfenster {slot} ab — der automatische Sync lädt "
                f"nichts hoch, nur der Knopf 'Sync Now' würde es tun.",
            )

        # ── 6. Zuordnung Datei ↔ Account ──────────────────────────────
        matched = [f for f in log_files if f.stem.lower() in allowed]
        unmatched = [f for f in log_files if f.stem.lower() not in allowed]
        steps["file_matching"] = {
            "log_files": len(log_files),
            "matched": len(matched),
            "unmatched": len(unmatched),
            "unmatched_sample": [f.name for f in unmatched[:SAMPLE_SIZE]],
        }
        if log_files and allowed and not matched:
            steps["file_matching"]["hint"] = (
                "Die Dateinamen müssen exakt dem Benutzernamen entsprechen "
                "(Dateiname ohne .log, Groß-/Kleinschreibung egal)."
            )
            block(
                "file_matching",
                f"Keine der {len(log_files)} Logdateien gehört zu einem freigegebenen Account.",
            )

        # ── 7. Zeitstempel in der ersten Zeile ────────────────────────
        parsed, unparsed = [], []
        for log_file in matched:
            timestamp = _parse_log_timestamp(log_file)
            if timestamp:
                parsed.append((log_file, timestamp))
            else:
                unparsed.append(log_file.name)
        steps["timestamps"] = {
            "checked": len(matched),
            "parsed": len(parsed),
            "unparsed": len(unparsed),
            "unparsed_sample": unparsed[:SAMPLE_SIZE],
            "would_upload_as": [
                _build_upload_path(config.server_name, f.stem.lower(), *ts)
                for f, ts in parsed[:SAMPLE_SIZE]
            ],
        }
        if matched and not parsed:
            steps["timestamps"]["hint"] = (
                "Erwartet wird '[MM/TT HH:MM:SS]' am Anfang der ersten Zeile."
            )
            block(
                "timestamps",
                f"Bei allen {len(matched)} zugeordneten Dateien ist der Zeitstempel "
                f"nicht lesbar — sie zählen als 'failed' und werden nicht hochgeladen.",
            )

        # ── 8. Bucket ─────────────────────────────────────────────────
        steps["bucket"] = _bucket_state(config.server_name)
        if config.supabase_key and not steps["bucket"].get("reachable"):
            block(
                "bucket",
                f"Der Bucket '{BUCKET_NAME}' ist nicht erreichbar: "
                f"{steps['bucket'].get('error') or steps['bucket'].get('bucket_error')}",
            )

        # ── 9. Letzter Lauf ───────────────────────────────────────────
        steps["last_runs"] = run_state.get_runs()

    except Exception as e:
        logger.error(f"Diagnose fehlgeschlagen: {e}", exc_info=True)
        steps["error"] = f"{type(e).__name__}: {e}"
    finally:
        if temp_db is not None and temp_db.exists():
            try:
                temp_db.unlink()
            except Exception as e:
                logger.debug(f"Arbeitskopie {temp_db} nicht gelöscht: {e}")

    return _finish(config, steps, blocking)


def _finish(config, steps: dict, blocking: list[str]) -> dict:
    """Aus den Einzelschritten ein Urteil in einem Satz bauen."""
    if steps.get("error"):
        verdict = f"Diagnose unvollständig: {steps['error']}"
    elif not blocking:
        verdict = (
            "Kein blockierendes Glied gefunden — der Upload sollte laufen. "
            "Wenn trotzdem nichts ankommt, sagt 'bucket' und 'last_runs', wo es hakt."
        )
    else:
        verdict = steps[blocking[0]]["verdict"]
        if len(blocking) > 1:
            verdict += f" (zusätzlich betroffen: {', '.join(blocking[1:])})"

    return {
        "server_name": config.server_name,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "verdict": verdict,
        "blocking": blocking,
        "steps": steps,
    }


def diagnose_database() -> dict:
    """Integrität der super.db plus Zeilenzahlen je Tabelle.

    Nutzt `db_integrity.check_database` — also dieselbe Prüfung wie vor dem Sync,
    aber ohne Mailversand und ohne den Zustand zu verändern.
    """
    config = get_config()
    db_path = Path(config.sqlite_db_path).expanduser()

    result: dict = {
        "db_path": str(db_path),
        "exists": db_path.exists(),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    if not db_path.exists():
        result["verdict"] = f"Datenbank nicht gefunden: {db_path}"
        return result

    result.update(_stat(db_path))
    result["integrity"] = db_integrity.check_database(db_path)

    temp_db = None
    try:
        temp_db = _copy_db(db_path)
        conn = sqlite3.connect(temp_db)
        try:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            rows = {}
            for table in tables:
                try:
                    rows[table] = conn.execute(
                        f'SELECT COUNT(*) FROM "{table}"'
                    ).fetchone()[0]
                except Exception as e:
                    rows[table] = f"Fehler: {type(e).__name__}"
            result["row_counts"] = rows
        finally:
            conn.close()
    except Exception as e:
        result["row_counts_error"] = f"{type(e).__name__}: {e}"
    finally:
        if temp_db is not None and temp_db.exists():
            try:
                temp_db.unlink()
            except Exception as e:
                logger.debug(f"Arbeitskopie {temp_db} nicht gelöscht: {e}")

    result["verdict"] = (
        "Datenbank in Ordnung"
        if result["integrity"]["ok"]
        else f"Datenbank beschädigt: {result['integrity']['detail']}"
    )
    return result
