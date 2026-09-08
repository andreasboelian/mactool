"""Was der Fernzugriff auf diesem Mac ausführen darf.

Eine feste Liste, kein `eval`, kein Shell. Was hier nicht steht, passiert nicht —
auch dann nicht, wenn jemand mit dem Supabase-Key eine beliebige Zeile in die
Befehlstabelle schreibt.

Die Handler sind absichtlich dünn: sie rufen denselben Code auf, den das Dashboard
schon benutzt. So kann die Ferndiagnose nicht etwas anderes tun als der Knopf.
"""

import logging
import re
from datetime import datetime
from pathlib import Path

from config import get_config, masked_config_dict

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).parent

# Antworten wandern als jsonb durch Postgres und über die Queue zurück. Ein
# ungebremster Log-Tail wären Megabytes pro Abruf.
MAX_LOG_LINES = 2000
DEFAULT_LOG_LINES = 200
MAX_LINE_CHARS = 500
MAX_LISTED_FILES = 200


class CommandError(RuntimeError):
    """Der Befehl ist so nicht ausführbar (falscher Name, falsche Argumente)."""


# ── Lesende Befehle ───────────────────────────────────────────────────


def _cmd_status(args: dict) -> dict:
    """Zustand des Macs. Ohne GitHub-Abfrage — die kostet bis zu 15 s."""
    from status import build_status

    return build_status(check_updates=bool(args.get("check_updates", False)))


def _cmd_config(args: dict) -> dict:
    """Die komplette Konfiguration, Geheimnisse maskiert."""
    return masked_config_dict()


def _resolve_log_file(name: str | None) -> Path:
    """Logdatei auflösen — ausschließlich aus dem logs-Verzeichnis des Tools.

    Der Name wird auf seinen Dateianteil reduziert, bevor er benutzt wird. Ohne
    das wäre `file: "../../../etc/passwd"` ein Leseweg auf beliebige Dateien.
    """
    log_dir = (APP_DIR / "logs").resolve()
    candidate = (log_dir / Path(name or "mactool.log").name).resolve()
    if candidate.parent != log_dir:
        raise CommandError(f"Logdatei außerhalb von {log_dir} ist nicht erlaubt")
    return candidate


def _cmd_logs(args: dict) -> dict:
    """Das Ende von logs/mactool.log, optional nach einem Muster gefiltert."""
    log_file = _resolve_log_file(args.get("file"))
    lines = min(int(args.get("lines") or DEFAULT_LOG_LINES), MAX_LOG_LINES)
    pattern = args.get("grep")

    if not log_file.exists():
        return {"file": str(log_file), "exists": False, "lines": []}

    try:
        matcher = re.compile(pattern, re.IGNORECASE) if pattern else None
    except re.error as e:
        raise CommandError(f"Ungültiges Suchmuster '{pattern}': {e}")

    # Zeilenweise lesen statt readlines(): mactool.log darf 5 MB groß werden
    # und der Filter wirft das meiste ohnehin weg.
    from collections import deque

    tail: deque = deque(maxlen=lines)
    scanned = 0
    with open(log_file, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            scanned += 1
            if matcher and not matcher.search(line):
                continue
            tail.append(line.rstrip("\n")[:MAX_LINE_CHARS])

    return {
        "file": str(log_file),
        "exists": True,
        "bytes": log_file.stat().st_size,
        "scanned_lines": scanned,
        "grep": pattern,
        "returned": len(tail),
        "lines": list(tail),
    }


def _cmd_files(args: dict) -> dict:
    """Verzeichnisinhalt — entweder die Bot-Logs oder die des Tools."""
    which = (args.get("dir") or "botlogs").lower()

    if which == "botlogs":
        directory = Path(get_config().sqlite_db_path).expanduser().parent / "logs"
    elif which == "mactool":
        directory = APP_DIR / "logs"
    else:
        raise CommandError(f"Unbekanntes Verzeichnis '{which}' (erlaubt: botlogs, mactool)")

    if not directory.exists():
        return {"dir": str(directory), "exists": False, "files": []}

    entries = []
    for path in sorted(directory.iterdir())[:MAX_LISTED_FILES]:
        try:
            info = path.stat()
            entries.append(
                {
                    "name": path.name,
                    "bytes": info.st_size,
                    "modified": datetime.fromtimestamp(info.st_mtime).isoformat(
                        timespec="seconds"
                    ),
                }
            )
        except Exception as e:
            entries.append({"name": path.name, "error": f"{type(e).__name__}: {e}"})

    total = sum(1 for _ in directory.iterdir())
    return {
        "dir": str(directory),
        "exists": True,
        "total_files": total,
        "listed": len(entries),
        "truncated": total > len(entries),
        "files": entries,
    }


def _cmd_diag_upload(args: dict) -> dict:
    import diagnostics

    return diagnostics.diagnose_log_upload()


def _cmd_diag_db(args: dict) -> dict:
    import diagnostics

    return diagnostics.diagnose_database()


def _cmd_versions(args: dict) -> dict:
    from updater import check_for_updates, get_available_versions, get_current_version

    return {
        "current": get_current_version(),
        "available": get_available_versions(),
        "check": check_for_updates(),
    }


def _cmd_legacy_upload(args: dict) -> dict:
    """Status der alten upload-macXX.py-LaunchAgents."""
    import legacy_upload

    return legacy_upload.get_status()


# ── Eingreifende Befehle (brauchen remote_allow_actions) ──────────────


def _cmd_sync(args: dict) -> dict:
    from sync import trigger_sync

    upload_all = args.get("upload_all")
    return trigger_sync(upload_all_logs=True if upload_all is None else bool(upload_all))


def _cmd_customer_stats(args: dict) -> dict:
    from customer_stats import run_customer_stats_upload

    dry_run = bool(args.get("dry_run", False))
    return run_customer_stats_upload(
        trigger="preview" if dry_run else "manual", dry_run=dry_run
    )


def _cmd_bot(args: dict) -> dict:
    import bot_manager

    action = (args.get("action") or "").lower()
    actions = {
        "start": bot_manager.start_bot,
        "stop": bot_manager.stop_bot,
        "restart": bot_manager.restart_bot,
    }
    if action not in actions:
        raise CommandError(f"Unbekannte Aktion '{action}' (erlaubt: start, stop, restart)")

    success = actions[action]()
    return {
        "action": action,
        "success": bool(success),
        "running": bot_manager.is_bot_running(),
        "auto_restart": bot_manager.is_auto_restart_enabled(),
    }


def _cmd_rustdesk(args: dict) -> dict:
    import rustdesk_manager

    action = (args.get("action") or "").lower()
    if action == "start":
        # Wie im Dashboard: Wächter einschalten *und* sofort starten
        rustdesk_manager.set_watch_enabled(True)
        success = rustdesk_manager.start_rustdesk()
    elif action == "stop":
        # Schaltet nur den Wächter ab, beendet kein laufendes RustDesk —
        # sonst würde der Fernzugriff den letzten Notzugang kappen.
        rustdesk_manager.set_watch_enabled(False)
        success = True
    else:
        raise CommandError(f"Unbekannte Aktion '{action}' (erlaubt: start, stop)")

    return {
        "action": action,
        "success": bool(success),
        "running": rustdesk_manager.is_rustdesk_running(),
        "watch": rustdesk_manager.is_watch_enabled(),
    }


def _cmd_update(args: dict) -> dict:
    """Auf eine Version wechseln.

    Achtung: bei Erfolg beendet sich der Prozess zwei Sekunden später selbst
    (launchd startet ihn neu). Der Aufrufer muss das Ergebnis *vor* Ablauf
    dieser Frist zurückgeschrieben haben — darum kümmert sich `remote_agent`.
    """
    from updater import perform_update

    return perform_update(args.get("version") or None)


# ── Registrierung ─────────────────────────────────────────────────────

# name -> (handler, ist_eingreifend)
HANDLERS: dict[str, tuple] = {
    "status": (_cmd_status, False),
    "config": (_cmd_config, False),
    "logs": (_cmd_logs, False),
    "files": (_cmd_files, False),
    "diag-upload": (_cmd_diag_upload, False),
    "diag-db": (_cmd_diag_db, False),
    "versions": (_cmd_versions, False),
    "legacy-upload": (_cmd_legacy_upload, False),
    "sync": (_cmd_sync, True),
    "customer-stats": (_cmd_customer_stats, True),
    "bot": (_cmd_bot, True),
    "rustdesk": (_cmd_rustdesk, True),
    "update": (_cmd_update, True),
}

# Befehle, nach denen sich der Prozess selbst beendet — das Ergebnis muss
# vorher in der Queue stehen, sonst bleibt die Zeile für immer auf "running".
SELF_RESTARTING = {"update"}


def describe() -> list[dict]:
    """Die Befehlsliste, wie sie das Dashboard anzeigen kann."""
    return [
        {"command": name, "requires_actions": needs_actions}
        for name, (_, needs_actions) in sorted(HANDLERS.items())
    ]


def execute(command: str, args: dict | None = None) -> dict:
    """Einen Befehl ausführen und sein Ergebnis liefern.

    Wirft `CommandError` bei unbekanntem Namen, falschen Argumenten oder wenn
    eingreifende Befehle abgeschaltet sind. Alles andere schlägt als die
    Ausnahme durch, die der Handler geworfen hat — der Aufrufer schreibt sie
    als `error` in die Queue.
    """
    args = args or {}
    entry = HANDLERS.get(command)
    if entry is None:
        raise CommandError(
            f"Unbekannter Befehl '{command}' (bekannt: {', '.join(sorted(HANDLERS))})"
        )

    handler, needs_actions = entry
    if needs_actions and not get_config().remote_allow_actions:
        raise CommandError(
            f"'{command}' greift ein und ist auf diesem Mac gesperrt "
            f"(remote_allow_actions = false)"
        )

    logger.info(f"Fernzugriff führt aus: {command} {args or ''}".strip())
    return handler(args)
