"""Settings from the environment. `.env` next to the repo root is loaded once, here."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

APP_ID = os.environ.get("ADAPTY_APP_ID", "9c49efb6-f2ca-479a-8f57-4bb6c8ea2f41")
ACCESS_LEVEL = os.environ.get("ADAPTY_ACCESS_LEVEL", "premium")
COMPENSATION_DAYS = int(os.environ.get("COMPENSATION_DAYS", "7"))
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "")
DEMO_MODE = os.environ.get("DEMO_MODE", "0") == "1"
DB_PATH = Path(os.environ.get("RESOLVE_DB", ROOT / "data" / "resolve.sqlite"))
DEMO_DB_PATH = ROOT / "data" / "demo.sqlite"
SEED_REPO = Path(os.environ.get("SEED_REPO", Path.home() / "hackathon" / "adapty-seed-data-hack"))


class MissingKey(RuntimeError):
    pass


def _key(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingKey(f"{name} is not set; copy .env.example to .env and fill it in, or run with DEMO_MODE=1")
    return value


def adapty_secret() -> str:
    return _key("ADAPTY_SECRET_KEY")


def anthropic_key() -> str:
    return _key("ANTHROPIC_API_KEY")
