"""Der Mac holt sich seine Aufträge selbst ab.

Die Macs stehen hinter NAT; von außen kommt niemand herein. Also dreht sich die
Richtung um: statt dass wir den Mac anrufen, schaut der Mac regelmäßig in einen
Briefkasten in der Dashboard-Supabase, arbeitet ab was für ihn dort liegt und legt
die Antwort daneben.

Dafür braucht es keinen offenen Port, keinen Tunnel und keine neuen Zugangsdaten —
`supabase_url` / `supabase_key` liegen ohnehin auf jedem Mac.

Die beiden Tabellen legt `tools/schema_remote.sql` an.
"""

import logging
import platform
import socket
import time
from datetime import datetime, timezone

import remote_commands
from config import get_config
from supabase_rest import SupabaseRest

logger = logging.getLogger(__name__)

# Mehr als das pro Durchlauf wäre bei einem langsamen Befehl ohnehin sinnlos —
# der Rest wartet einfach bis zum nächsten Mal.
MAX_COMMANDS_PER_TICK = 5

HEARTBEAT_INTERVAL_SECONDS = 60

# Ergebnisse landen als jsonb in Postgres. Ein entgleister Befehl soll die Zeile
# nicht auf Megabytes aufblähen.
MAX_RESULT_CHARS = 200_000
MAX_ERROR_CHARS = 2000

_last_heartbeat: float = 0.0


def _client() -> SupabaseRest | None:
    """REST-Client auf die Dashboard-Supabase, oder None wenn nicht konfiguriert."""
    config = get_config()
    if not config.supabase_key:
        return None
    return SupabaseRest(config.supabase_url, config.supabase_key)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: str | None) -> datetime | None:
    """Einen Postgres-Zeitstempel lesen, ohne bei Formatvarianten zu werfen."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    # Postgres liefert Mikrosekunden mit variabler Länge; fromisoformat verlangt
    # vor Python 3.11 exakt 3 oder 6 Stellen.
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    logger.warning(f"Zeitstempel nicht lesbar: {value!r}")
    return None


def _truncate(value, limit: int):
    """Zu lange Antworten kappen, statt sie in Postgres zu schieben."""
    text = str(value)
    if len(text) <= limit:
        return value
    return {"truncated": True, "chars": len(text), "preview": text[:limit]}


def _finish(client: SupabaseRest, table: str, command_id, values: dict) -> None:
    """Eine Zeile abschließen. Scheitert das, bleibt sie auf 'running' stehen."""
    try:
        client.update(
            table,
            {"id": f"eq.{command_id}"},
            {**values, "finished_at": _now().isoformat()},
        )
    except Exception as e:
        logger.error(f"Ergebnis für Befehl {command_id} nicht zurückgeschrieben: {e}")


def _handle(client: SupabaseRest, table: str, row: dict) -> None:
    """Eine geclaimte Zeile ausführen und das Ergebnis zurückschreiben."""
    command_id = row.get("id")
    command = (row.get("command") or "").strip()
    args = row.get("args") or {}
    if not isinstance(args, dict):
        args = {}

    started = time.monotonic()

    # `update` beendet den Prozess zwei Sekunden nach Erfolg. Wer danach noch
    # schreiben will, kommt nicht mehr dazu — die Zeile bliebe für immer auf
    # 'running'. Also vorher vermerken, dass es losging.
    if command in remote_commands.SELF_RESTARTING:
        _finish(
            client,
            table,
            command_id,
            {
                "status": "done",
                "result": {
                    "note": "Der Dienst startet nach diesem Befehl neu. Das eigentliche "
                            "Ergebnis steht im Log und in der Version des nächsten Heartbeats.",
                    "command": command,
                    "args": args,
                },
            },
        )
        try:
            result = remote_commands.execute(command, args)
            logger.info(f"Befehl {command_id} ({command}) → {result}")
        except Exception as e:
            logger.error(f"Befehl {command_id} ({command}) fehlgeschlagen: {e}", exc_info=True)
            _finish(
                client,
                table,
                command_id,
                {"status": "error", "error": _truncate(f"{type(e).__name__}: {e}", MAX_ERROR_CHARS)},
            )
        return

    try:
        result = remote_commands.execute(command, args)
        _finish(
            client,
            table,
            command_id,
            {
                "status": "done",
                "result": _truncate(result, MAX_RESULT_CHARS),
                "error": None,
            },
        )
        logger.info(
            f"Befehl {command_id} ({command}) erledigt in {time.monotonic() - started:.1f}s"
        )
    except remote_commands.CommandError as e:
        # Erwartbar: unbekannter Name, falsche Argumente, Aktionen gesperrt.
        # Kein Stacktrace — das ist eine Antwort, kein Absturz.
        logger.warning(f"Befehl {command_id} ({command}) abgelehnt: {e}")
        _finish(client, table, command_id, {"status": "error", "error": str(e)[:MAX_ERROR_CHARS]})
    except Exception as e:
        logger.error(f"Befehl {command_id} ({command}) fehlgeschlagen: {e}", exc_info=True)
        _finish(
            client,
            table,
            command_id,
            {"status": "error", "error": f"{type(e).__name__}: {e}"[:MAX_ERROR_CHARS]},
        )


def _claim(client: SupabaseRest, table: str, row: dict) -> bool:
    """Eine Zeile für uns reservieren. False heißt: hat schon jemand anders.

    Die Bedingung `status=eq.queued` steckt in der Anfrage selbst, deshalb kann
    genau einer gewinnen — auch wenn versehentlich zwei mactool-Prozesse laufen.
    """
    try:
        changed = client.update(
            table,
            {"id": f"eq.{row['id']}", "status": "eq.queued"},
            {"status": "running", "claimed_at": _now().isoformat()},
            retries=1,
        )
        return bool(changed)
    except Exception as e:
        logger.warning(f"Befehl {row.get('id')} nicht reserviert: {e}")
        return False


def _heartbeat(client: SupabaseRest, config) -> None:
    """Lebenszeichen — höchstens einmal pro Minute.

    Erst dadurch ist von außen sichtbar, welcher Mac überhaupt läuft und auf
    welcher Version. Ohne das merkt man einen toten Mac erst, wenn ein Befehl
    unbeantwortet bleibt.
    """
    global _last_heartbeat
    if time.monotonic() - _last_heartbeat < HEARTBEAT_INTERVAL_SECONDS:
        return

    try:
        from bot_manager import is_bot_running
        from rustdesk_manager import is_rustdesk_running
        from run_state import get_runs
        from updater import get_current_version

        client.upsert_many(
            config.remote_agents_table,
            [
                {
                    "server_name": config.server_name,
                    # Beides senden, mit Absicht: normalerweise überschreibt der
                    # Trigger last_seen mit der Zeit der Datenbank (die Mac-Uhr kann
                    # nachgehen — auf mac07 um sieben Minuten). Fehlt der Trigger,
                    # weil das SQL noch nicht eingespielt ist, bleibt wenigstens die
                    # Mac-Zeit stehen. Schickten wir last_seen gar nicht, behielte
                    # der Upsert beim Aktualisieren den alten Wert für immer bei.
                    "last_seen": _now().isoformat(),
                    "agent_clock": _now().isoformat(),
                    "version": get_current_version(),
                    "hostname": socket.gethostname(),
                    "platform": platform.platform(),
                    "bot_running": is_bot_running(),
                    "rustdesk_running": is_rustdesk_running(),
                    "actions_allowed": config.remote_allow_actions,
                    "last_runs": get_runs(),
                }
            ],
            on_conflict="server_name",
        )
        _last_heartbeat = time.monotonic()
    except Exception as e:
        # Kein Lebenszeichen ist ärgerlich, aber kein Grund, keine Befehle zu holen
        logger.warning(f"Heartbeat fehlgeschlagen: {e}")


def poll_once() -> dict:
    """Ein Durchlauf: Lebenszeichen senden, offene Befehle abarbeiten.

    Wirft nie — der Rückgabewert sagt, was passiert ist. Eine Ausnahme hier würde
    den APScheduler-Job stilllegen und den Fernzugriff damit unbemerkt beenden.
    """
    config = get_config()

    if not config.remote_control_enabled:
        return {"status": "disabled"}

    client = _client()
    if client is None:
        return {"status": "not_configured", "error": "kein supabase_key hinterlegt"}

    table = config.remote_commands_table
    result = {"status": "ok", "server_name": config.server_name, "executed": 0, "expired": 0}

    _heartbeat(client, config)

    try:
        rows = client.select(
            table,
            {
                "server_name": f"eq.{config.server_name}",
                "status": "eq.queued",
                "order": "created_at.asc",
                "limit": MAX_COMMANDS_PER_TICK,
                "select": "id,command,args,expires_at,requested_by",
            },
        )
    except Exception as e:
        logger.warning(f"Befehle nicht abrufbar: {e}")
        return {"status": "error", "error": str(e)[:300]}

    result["pending"] = len(rows)

    for row in rows:
        # Abgelaufenes wird nicht ausgeführt. Ohne das würde ein Mac, der drei
        # Tage aus war, beim Hochfahren einen längst überholten Befehl abarbeiten
        # — im schlimmsten Fall ein 'update' auf eine Version von vorgestern.
        expires_at = _parse_timestamp(row.get("expires_at"))
        if expires_at and expires_at < _now():
            if _claim(client, table, row):
                _finish(
                    client,
                    table,
                    row["id"],
                    {
                        "status": "expired",
                        "error": f"Befehl war seit {row['expires_at']} abgelaufen "
                                 f"und wurde nicht ausgeführt",
                    },
                )
                result["expired"] += 1
            continue

        if not _claim(client, table, row):
            continue

        _handle(client, table, row)
        result["executed"] += 1

    return result


def run_remote_agent_job() -> dict:
    """Scheduler-Einstiegspunkt."""
    try:
        return poll_once()
    except Exception as e:
        logger.error(f"Fernzugriff: unerwarteter Fehler: {e}", exc_info=True)
        return {"status": "error", "error": str(e)[:300]}


def get_agent_state() -> dict:
    """Was das Dashboard über den Fernzugriff anzeigt."""
    config = get_config()
    state = {
        "enabled": config.remote_control_enabled,
        "actions_allowed": config.remote_allow_actions,
        "poll_seconds": config.remote_poll_seconds,
        "commands_table": config.remote_commands_table,
        "agents_table": config.remote_agents_table,
        "configured": bool(config.supabase_key),
        "commands": remote_commands.describe(),
    }

    client = _client()
    if client is None or not config.remote_control_enabled:
        return state

    try:
        pending = client.select(
            config.remote_commands_table,
            {
                "server_name": f"eq.{config.server_name}",
                "status": "in.(queued,running)",
                "select": "id,command,status,created_at",
                "order": "created_at.asc",
                "limit": 20,
            },
        )
        state["pending"] = pending
        state["reachable"] = True
    except Exception as e:
        state["reachable"] = False
        state["error"] = str(e)[:300]

    return state
