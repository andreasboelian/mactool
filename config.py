"""Configuration management for mactool."""

import json
import os
from dataclasses import dataclass, asdict, field, fields
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)

CONFIG_FILE = Path("config.json")
DEFAULT_CONFIG_FILE = Path(__file__).parent / "config.json.example"


@dataclass
class AppConfig:
    """Application configuration."""

    server_name: str
    sync_times: list[str] = field(default_factory=lambda: ["09:00", "14:30"])
    blacklist: list[str] = field(default_factory=list)
    supabase_url: str = "https://fxreaveeihaawkusmybi.supabase.co"
    supabase_key: str = ""
    bot_app_path: str = "/Applications/botapp.app/Contents/MacOS/BotApp"
    rustdesk_app_path: str = "/Applications/RustDesk.app"
    adb_path: str = "adb"
    sqlite_db_path: str = "~/Desktop/GramBotStorage/super.db"
    webhook_url: str = ""
    device_check_interval_hours: int = 1
    bot_check_interval_minutes: int = 5
    rustdesk_check_interval_minutes: int = 5
    log_level: str = "INFO"
    github_repo: str = ""  # e.g. "username/mactool" — for self-update

    # ── Statistik (Dashboard) ────────────────────────────────────────
    # Target is supabase_url / supabase_key above. The toggle only gates the
    # `stats` table — device, profile, bin and the bot log upload keep running.
    dashboard_stats_enabled: bool = True
    dashboard_table_device: str = "device"
    dashboard_table_profile: str = "profile"
    dashboard_table_stats: str = "stats"
    dashboard_table_bin: str = "bin"

    # ── Statistik (Kunde) ────────────────────────────────────────────
    # Replaces the per-mac `upload-macXX.py` LaunchAgent scripts. Two targets:
    # raw session data (upsert) and the "beautified" users table (insert).
    customer_stats_enabled: bool = False
    customer_stats_source: str = "sessions"  # "sessions" | "superdb"
    customer_stats_session_limit: int = 90
    customer_stats_url: str = "https://eeshaewrbrqipsvtlyng.supabase.co"
    customer_stats_key: str = ""
    customer_stats_table: str = "statistik"
    customer_users_url: str = "https://apltfvenhqwnidmuptdp.supabase.co"
    customer_users_key: str = ""
    customer_users_table: str = "users"
    # Disable the legacy upload LaunchAgent once our own upload succeeded once
    auto_disable_legacy_upload: bool = True

    def save(self):
        """Save configuration to config.json."""
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(asdict(self), f, indent=2)
            logger.info(f"Configuration saved to {CONFIG_FILE}")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            raise

    @staticmethod
    def load() -> "AppConfig":
        """Load configuration from config.json or environment."""

        # Try to load from config.json first
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)

                # Ignore keys this version doesn't know. Without this, running an
                # older version (via the dashboard's version selector) against a
                # config.json written by a newer one raises TypeError and the
                # service falls back to an empty default config.
                known = {f.name for f in fields(AppConfig)}
                unknown = sorted(set(data) - known)
                if unknown:
                    logger.warning(
                        f"Ignoring unknown config keys (newer version?): {', '.join(unknown)}"
                    )

                logger.info(f"Configuration loaded from {CONFIG_FILE}")
                return AppConfig(**{k: v for k, v in data.items() if k in known})
            except Exception as e:
                logger.error(f"Failed to load config from file: {e}")

        # Fallback: try environment variable for supabase_key
        supabase_key = os.getenv("SUPABASE_KEY", "")
        server_name = os.getenv("SERVER_NAME", "default")

        if not supabase_key:
            logger.warning("SUPABASE_KEY not set in environment or config.json")

        config = AppConfig(server_name=server_name, supabase_key=supabase_key)
        logger.info(f"Using configuration with server_name={server_name}")
        return config


_config_instance: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """Get singleton configuration instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = AppConfig.load()
    return _config_instance


def reload_config() -> AppConfig:
    """Reload configuration from file."""
    global _config_instance
    _config_instance = AppConfig.load()
    return _config_instance
