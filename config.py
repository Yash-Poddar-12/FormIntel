"""Application configuration for FormIntel."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Force load .env from the project root regardless of where Python is launched from
_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_env_path, override=True)


def _to_bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _to_int(value: str | int | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()
PLAYWRIGHT_HEADLESS: bool = _to_bool(os.getenv("PLAYWRIGHT_HEADLESS"), default=False)
PLAYWRIGHT_SLOW_MO: int = _to_int(os.getenv("PLAYWRIGHT_SLOW_MO"), default=700)
DEFAULT_TIMEOUT: int = _to_int(os.getenv("DEFAULT_TIMEOUT"), default=30000)

# Debug line - remove after confirming key loads
print(f"[Config] API Key loaded: {'YES' if GEMINI_API_KEY else 'NO - CHECK YOUR .env FILE'}")

GEMINI_MODEL_NAME: str = "gemini-2.5-flash"
SYSTEM_PROMPT: str = (
    "You are a QA test data generator. Generate realistic, format-correct values "
    "for form fields. For financial/banking forms use Indian formats. Always return "
    "pure JSON, no explanation. For valid values: use real-looking but fake data "
    "(no real PAN/Aadhaar). For invalid values: use values that violate the "
    "field's expected format."
)
MAX_CONVERGENCE_ITERATIONS: int = 5


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = GEMINI_API_KEY
    playwright_headless: bool = PLAYWRIGHT_HEADLESS
    playwright_slow_mo: int = PLAYWRIGHT_SLOW_MO
    default_timeout: int = DEFAULT_TIMEOUT
    gemini_model_name: str = GEMINI_MODEL_NAME
    system_prompt: str = SYSTEM_PROMPT
    max_convergence_iterations: int = MAX_CONVERGENCE_ITERATIONS