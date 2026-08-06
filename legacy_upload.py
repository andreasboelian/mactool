"""Retire the legacy `upload-macXX.py` LaunchAgent.

Before the customer statistics upload moved into mactool, every mac ran the
upload as its own LaunchAgent. Once mactool has uploaded successfully at least
once, that agent is redundant — and leaving it in place would write the nightly
`users` rows a second time.

Deliberately narrow: only user LaunchAgents whose Program/ProgramArguments
actually start a script named `upload-*.py` / `upload_*.py` are touched. Nothing
else in ~/Library/LaunchAgents is looked at, and disabling is reversible (the
plist is renamed, never deleted).
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
DISABLED_SUFFIX = ".mactool-disabled"

# Must match the *file name* of the launched script, e.g. "upload-mac17.py"
UPLOAD_SCRIPT_RE = re.compile(r"^upload[-_][\w.-]*\.py$", re.IGNORECASE)

# Never touch mactool's own agent, whatever it is called
PROTECTED_LABEL_PARTS = ("mactool",)


def _read_plist(path: Path) -> dict | None:
    """Read a plist (binary or XML) as a dict via plutil."""
    try:
        result = subprocess.run(
            ["plutil", "-convert", "json", "-o", "-", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            logger.debug(f"plutil failed for {path.name}: {result.stderr.strip()}")
            return None
        data = json.loads(result.stdout)
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.debug(f"Could not read {path.name}: {e}")
        return None


def _upload_script_in(plist: dict) -> str | None:
    """Return the upload script this agent starts, or None."""
    candidates: list[str] = []

    program = plist.get("Program")
    if isinstance(program, str):
        candidates.append(program)

    arguments = plist.get("ProgramArguments")
    if isinstance(arguments, list):
        candidates.extend(a for a in arguments if isinstance(a, str))

    for candidate in candidates:
        if UPLOAD_SCRIPT_RE.match(Path(candidate).name):
            return candidate
    return None


def find_agents() -> list[dict]:
    """Find all LaunchAgents that start an upload script."""
    if not LAUNCH_AGENTS_DIR.is_dir():
        return []

    agents = []
    for path in sorted(LAUNCH_AGENTS_DIR.iterdir()):
        name = path.name
        if not (name.endswith(".plist") or name.endswith(".plist" + DISABLED_SUFFIX)):
            continue

        plist = _read_plist(path)
        if not plist:
            continue

        label = str(plist.get("Label") or path.stem)
        if any(part in label.lower() for part in PROTECTED_LABEL_PARTS):
            continue

        script = _upload_script_in(plist)
        if not script:
            continue

        agents.append(
            {
                "path": str(path),
                "label": label,
                "script": script,
                "enabled": not name.endswith(DISABLED_SUFFIX),
            }
        )

    return agents


def _launchctl(args: list[str]) -> bool:
    try:
        result = subprocess.run(
            ["launchctl"] + args, capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            logger.debug(f"launchctl {' '.join(args)} → {result.stderr.strip()}")
        return result.returncode == 0
    except Exception as e:
        logger.debug(f"launchctl {' '.join(args)} failed: {e}")
        return False


def disable_agent(path_str: str) -> dict:
    """Unload the agent and rename its plist so launchd ignores it."""
    path = Path(path_str)
    if not path.exists():
        return {"status": "error", "error": f"Nicht gefunden: {path}"}
    if path.name.endswith(DISABLED_SUFFIX):
        return {"status": "already_disabled", "path": str(path)}

    plist = _read_plist(path)
    label = str((plist or {}).get("Label") or path.stem)
    if any(part in label.lower() for part in PROTECTED_LABEL_PARTS):
        return {"status": "error", "error": f"Geschütztes Agent-Label: {label}"}
    if not plist or not _upload_script_in(plist):
        return {"status": "error", "error": f"{path.name} startet kein Upload-Skript"}

    # `unload -w` also marks it disabled in launchd's own database
    if not _launchctl(["unload", "-w", str(path)]):
        _launchctl(["bootout", f"gui/{os.getuid()}/{label}"])

    target = path.with_name(path.name + DISABLED_SUFFIX)
    try:
        path.rename(target)
    except OSError as e:
        return {"status": "error", "error": f"Umbenennen fehlgeschlagen: {e}"}

    logger.info(f"Altes Upload-Skript deaktiviert: {label} ({path.name})")
    return {"status": "disabled", "label": label, "path": str(target)}


def enable_agent(path_str: str) -> dict:
    """Restore a previously disabled agent."""
    path = Path(path_str)
    if not path.name.endswith(DISABLED_SUFFIX):
        return {"status": "already_enabled", "path": str(path)}
    if not path.exists():
        return {"status": "error", "error": f"Nicht gefunden: {path}"}

    target = path.with_name(path.name[: -len(DISABLED_SUFFIX)])
    try:
        path.rename(target)
    except OSError as e:
        return {"status": "error", "error": f"Umbenennen fehlgeschlagen: {e}"}

    _launchctl(["load", "-w", str(target)])
    logger.info(f"Altes Upload-Skript reaktiviert: {target.name}")
    return {"status": "enabled", "path": str(target)}


def get_status() -> dict:
    """Summary for the dashboard."""
    agents = find_agents()
    return {
        "found": len(agents),
        "active": sum(1 for a in agents if a["enabled"]),
        "agents": agents,
    }


def auto_disable_after_success() -> dict:
    """Disable still-active upload agents. Called after a successful upload."""
    disabled = []
    for agent in find_agents():
        if not agent["enabled"]:
            continue
        outcome = disable_agent(agent["path"])
        if outcome.get("status") == "disabled":
            disabled.append(agent["label"])
        else:
            logger.warning(
                f"Konnte {agent['label']} nicht deaktivieren: {outcome.get('error')}"
            )
    return {"disabled": disabled}
