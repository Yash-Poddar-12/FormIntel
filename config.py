"""Application configuration for smart_form_tester."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_env_path, override=True)


def _to_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _to_int(value, default):
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


GEMINI_API_KEY:       str  = os.getenv("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY:       str  = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL:         str  = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
PLAYWRIGHT_HEADLESS:  bool = _to_bool(os.getenv("PLAYWRIGHT_HEADLESS"), default=False)
PLAYWRIGHT_SLOW_MO:   int  = _to_int(os.getenv("PLAYWRIGHT_SLOW_MO"), default=700)
DEFAULT_TIMEOUT:      int  = _to_int(os.getenv("DEFAULT_TIMEOUT"), default=30000)

GEMINI_MODEL_NAME: str = "gemini-2.5-flash"
SYSTEM_PROMPT: str = (
    "You are a QA test data generator. Generate realistic, format-correct values "
    "for form fields. For financial/banking forms use Indian formats. Always return "
    "pure JSON, no explanation. For valid values: use real-looking but fake data "
    "(no real PAN/Aadhaar). For invalid values: use values that violate the "
    "field's expected format."
)
MAX_CONVERGENCE_ITERATIONS: int = 5

# Priority: OpenAI first (higher limits), then Gemini, then rule-based fallback
if OPENAI_API_KEY:
    AI_PROVIDER = "openai"
elif GEMINI_API_KEY:
    AI_PROVIDER = "gemini"
else:
    AI_PROVIDER = "fallback"

print(f"[Config] API Key loaded : {'YES' if (OPENAI_API_KEY or GEMINI_API_KEY) else 'NO'}")
if AI_PROVIDER == "openai":
    print(f"[Config] AI Provider    : OPENAI ({OPENAI_MODEL})")
elif AI_PROVIDER == "gemini":
    print(f"[Config] AI Provider    : GEMINI ({GEMINI_MODEL_NAME})")
else:
    print("[Config] AI Provider    : FALLBACK (rule-based, no API key found)")


@dataclass(frozen=True)
class Settings:
    gemini_api_key:             str  = GEMINI_API_KEY
    openai_api_key:             str  = OPENAI_API_KEY
    openai_model:               str  = OPENAI_MODEL
    ai_provider:                str  = AI_PROVIDER
    playwright_headless:        bool = PLAYWRIGHT_HEADLESS
    playwright_slow_mo:         int  = PLAYWRIGHT_SLOW_MO
    default_timeout:            int  = DEFAULT_TIMEOUT
    gemini_model_name:          str  = GEMINI_MODEL_NAME
    system_prompt:              str  = SYSTEM_PROMPT
    max_convergence_iterations: int  = MAX_CONVERGENCE_ITERATIONS