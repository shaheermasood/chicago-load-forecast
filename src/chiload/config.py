"""Project paths and settings loaded from configs/ and the environment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "configs"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"

load_dotenv(PROJECT_ROOT / ".env")


class MissingSecretError(RuntimeError):
    """Raised when a required secret is not set."""


def load_yaml(name: str) -> dict[str, Any]:
    """Load a YAML file from configs/ by name (without extension)."""
    with open(CONFIG_DIR / f"{name}.yaml") as f:
        return yaml.safe_load(f)


def eia_api_key() -> str:
    """Return the EIA API key, or raise with instructions if it's missing."""
    key = os.environ.get("EIA_API_KEY", "").strip()
    if not key:
        raise MissingSecretError(
            "EIA_API_KEY is not set. Copy .env.example to .env and add your free key "
            "from https://www.eia.gov/opendata/register.php"
        )
    return key
