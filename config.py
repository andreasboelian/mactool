"""Configuration management for mactool."""

import json
import os
from dataclasses import dataclass, asdict, field
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
    adb_path: str = "adb"
    sqlite_db_path: str = "~/Desktop/GramBotStorage/super.db"
    webhook_url: str = ""
    device_check_interval_hours: int = 1
    bot_check_interval_minutes: int = 5
    log_level: str = "INFO"
    github_repo: str = ""  # e.g. "username/mactool" — for self-update

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
                logger.info(f"Configuration loaded from {CONFIG_FILE}")
                return AppConfig(**data)
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
