"""Last-run bookkeeping for the statistics uploads.

The uploads run unattended twice a day; without a record of the last run there is
no way to tell from the dashboard whether they actually happened.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_FILE = Path(__file__).parent / "run_state.json"


def _load() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception as e:
        logger.debug(f"Could not read run state: {e}")
    return {}


def record_run(
    name: str,
    status: str,
    summary: str = "",
    trigger: str = "auto",
    detail: dict | None = None,
) -> None:
    """Store the outcome of a run under `name`.

    `detail` holds the per-target outcome so the dashboard can show a dead
    target permanently instead of only in the message right after a run.
    """
    try:
        data = _load()
        entry = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "status": status,
            "summary": summary[:300],
            "trigger": trigger,
        }
        if detail:
            entry["detail"] = detail
        data[name] = entry
        STATE_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.warning(f"Could not write run state for '{name}': {e}")


def get_runs() -> dict:
    """Return all recorded runs."""
    return _load()
