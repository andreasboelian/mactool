"""Integritätsprüfung der super.db, bevor sie für den Upload kopiert wird.

Eine beschädigte SQLite-Datei liefert entweder Müll oder gar nichts — beides hat in
Supabase nichts verloren, und beides fällt sonst niemandem auf. Deshalb läuft
`PRAGMA integrity_check` auf dem Original, *bevor* die Arbeitskopie gezogen wird.
Schlägt die Prüfung fehl, unterbleibt der Upload und es geht eine Mail raus.
"""

import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import mailer
import run_state
from config import get_config

logger = logging.getLogger(__name__)

# Schlüssel im run_state.json — hält auch fest, wann zuletzt gemailt wurde
STATE_KEY = "db_integrity"

CONNECT_TIMEOUT = 30
# SQLite meldet bis zu 100 Defekte — und packt sie in *eine* Zeile mit
# Zeilenumbrüchen. Ungekürzt stünde ein mehrere KB langer Block in Mail,
# Dashboard und run_state.json.
MAX_REPORTED_PROBLEMS = 8


def _integrity_rows(db_path: Path, mode: str) -> list[str]:
    """Run PRAGMA integrity_check and return its output as single lines.

    Immer über eine URI: ein einfaches ``sqlite3.connect(pfad)`` würde eine
    fehlende Datei stillschweigend neu anlegen — und eine leere Datenbank
    besteht jede Prüfung.
    """
    uri = f"{db_path.resolve().as_uri()}?mode={mode}"
    conn = sqlite3.connect(uri, uri=True, timeout=CONNECT_TIMEOUT)
    try:
        rows = conn.execute("PRAGMA integrity_check;").fetchall()
    finally:
        conn.close()

    lines = []
    for row in rows:
        if not row or row[0] is None:
            continue
        lines.extend(line.strip() for line in str(row[0]).splitlines() if line.strip())
    return lines


def check_database(db_path: Path) -> dict:
    """Check one SQLite file.

    Returns {"ok": bool, "detail": str, "seconds": float, "db_path": str}.
    Anything that prevents a clean "ok" counts as not ok — a file we cannot read
    is as useless for the upload as a corrupt one.
    """
    started = time.monotonic()

    def _result(ok: bool, detail: str) -> dict:
        return {
            "ok": ok,
            "detail": detail,
            "seconds": round(time.monotonic() - started, 1),
            "db_path": str(db_path),
        }

    if not db_path.exists():
        return _result(False, f"Datenbank nicht gefunden: {db_path}")

    try:
        messages = _integrity_rows(db_path, "ro")
    except sqlite3.OperationalError as e:
        # WAL-Datenbanken lassen sich ohne vorhandene -shm-Datei nicht read-only
        # öffnen. Das ist kein Defekt — also mit Schreibrecht erneut versuchen.
        # "rw" statt eines normalen connect: legt nichts an, was nicht da ist.
        logger.debug(f"Read-only-Prüfung nicht möglich ({e}) — zweiter Versuch mit Schreibrecht")
        try:
            messages = _integrity_rows(db_path, "rw")
        except Exception as retry_error:
            return _result(False, f"Datenbank nicht lesbar: {type(retry_error).__name__}: {retry_error}")
    except Exception as e:
        return _result(False, f"Datenbank nicht lesbar: {type(e).__name__}: {e}")

    if messages == ["ok"]:
        return _result(True, "ok")

    if not messages:
        return _result(False, "integrity_check lieferte kein Ergebnis")

    problems = messages[:MAX_REPORTED_PROBLEMS]
    if len(messages) > MAX_REPORTED_PROBLEMS:
        problems.append(f"... und {len(messages) - MAX_REPORTED_PROBLEMS} weitere Meldungen")
    return _result(False, " | ".join(problems))


def _alert_body(server_name: str, result: dict) -> str:
    return (
        f"Auf {server_name} war der Integrity Check der Datenbank fehlerhaft.\n\n"
        f"Der Upload nach Supabase wurde deshalb nicht ausgeführt — "
        f"es wurden keine Daten geschrieben.\n\n"
        f"Datenbank : {result['db_path']}\n"
        f"Zeitpunkt : {datetime.now():%Y-%m-%d %H:%M:%S}\n"
        f"Dauer     : {result['seconds']} s\n"
        f"Meldung   : {result['detail']}\n\n"
        f"Der nächste geplante Sync prüft erneut. Bleibt die Datenbank beschädigt, "
        f"muss sie auf dem Mac wiederhergestellt werden.\n"
    )


def _hours_since(timestamp: str | None) -> float | None:
    if not timestamp:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(timestamp)).total_seconds() / 3600
    except ValueError:
        return None


def _should_alert(previous: dict, cooldown_hours: int) -> tuple[bool, str]:
    """Alert on every new failure, then at most once per cooldown window."""
    if previous.get("status") != "error":
        return True, "neue Störung"

    detail = previous.get("detail") or {}
    elapsed = _hours_since(detail.get("last_alert_at"))
    if elapsed is None:
        return True, "kein Versandzeitpunkt hinterlegt"
    if elapsed >= cooldown_hours:
        return True, f"letzte Mail vor {elapsed:.1f} h"
    return False, f"letzte Mail vor {elapsed:.1f} h (Sperre {cooldown_hours} h)"


def verify_before_upload(db_path: Path, server_name: str) -> dict:
    """Check the database and alert by mail if it is broken.

    The caller must not upload anything when ``ok`` is False.
    """
    result = check_database(db_path)

    previous = run_state.get_runs().get(STATE_KEY, {})

    if result["ok"]:
        logger.info(f"Integrity Check ok ({result['seconds']}s): {db_path}")
        run_state.record_run(
            STATE_KEY, "success", f"integrity_check ok ({result['seconds']}s)"
        )
        return result

    logger.error(f"Integrity Check fehlgeschlagen für {db_path}: {result['detail']}")

    config = get_config()
    cooldown = max(0, int(config.alert_mail_cooldown_hours or 0))
    send, reason = _should_alert(previous, cooldown)

    if send:
        alert = mailer.send_alert(
            f"[mactool] {server_name}: Integrity Check der Datenbank fehlerhaft",
            _alert_body(server_name, result),
        )
    else:
        alert = {"status": "suppressed", "reason": reason}
        logger.info(f"Keine weitere Alarm-Mail: {reason}")

    detail = {"integrity": result["detail"], "mail": alert}
    if alert.get("status") == "sent":
        detail["last_alert_at"] = datetime.now().isoformat(timespec="seconds")
    else:
        # Nicht zugestellt heißt: beim nächsten Lauf wieder versuchen
        previous_sent = (previous.get("detail") or {}).get("last_alert_at")
        if previous_sent and alert.get("status") == "suppressed":
            detail["last_alert_at"] = previous_sent

    run_state.record_run(
        STATE_KEY, "error", f"integrity_check: {result['detail']}", detail=detail
    )

    result["alert"] = alert
    return result
