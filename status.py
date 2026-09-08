"""Der Zustandsbericht eines Macs — eine Quelle für Dashboard und Fernzugriff.

Dashboard (`GET /api/status`) und Fernzugriff (`remote_commands`) beantworten
dieselbe Frage: wie geht es diesem Mac. Zwei getrennte Implementierungen driften
auseinander, und dann zeigt die Ferndiagnose etwas anderes als der Bildschirm.
"""

import logging
import platform
import socket

import run_state
from bot_manager import is_auto_restart_enabled, is_bot_running
from config import get_config
from rustdesk_manager import is_rustdesk_running, is_watch_enabled as is_rustdesk_watch_enabled
from updater import check_for_updates, get_current_version

logger = logging.getLogger(__name__)


def build_status(check_updates: bool = True) -> dict:
    """Zustand des Macs.

    check_updates=False lässt den `git fetch` gegen GitHub weg. Der kostet bis zu
    15 Sekunden — vertretbar für einen Seitenaufruf im Dashboard, aber nicht in
    der Poll-Schleife des Fernzugriffs, die währenddessen stillsteht.
    """
    # Erst hier importiert: der Scheduler startet den Fernzugriff, der Fernzugriff
    # fragt diesen Bericht ab — ein Import ganz oben wäre ein Ringschluss.
    from scheduler import get_scheduler

    config = get_config()

    status = {
        "server_name": config.server_name,
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "bot_running": is_bot_running(),
        "auto_restart": is_auto_restart_enabled(),
        "rustdesk_running": is_rustdesk_running(),
        "rustdesk_watch": is_rustdesk_watch_enabled(),
        "dashboard_stats_enabled": config.dashboard_stats_enabled,
        "customer_stats_enabled": config.customer_stats_enabled,
        "customer_stats_source": config.customer_stats_source,
        "last_runs": run_state.get_runs(),
        "sync_times": config.sync_times,
        "jobs": get_scheduler().get_jobs(),
        "version": get_current_version(),
    }

    if check_updates:
        update_info = check_for_updates()
        status["update_available"] = update_info.get("status") == "update_available"
        status["latest_version"] = update_info.get("latest", "")

    return status
