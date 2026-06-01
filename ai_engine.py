"""AI engine for smart_form_tester.

Supports:
  - OpenAI  (gpt-4.1-mini recommended)
  - Gemini  (gemini-2.5-flash)
  - Rule-based fallback (no API key needed)

Priority: OpenAI > Gemini > Fallback
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from typing import Any

from jinja2 import Template


class AIEngine:

    def __init__(self, config: Any) -> None:
        self.config         = config
        self.provider       = getattr(config, "ai_provider", "fallback")
        self.system_prompt  = getattr(config, "system_prompt", "")
        self._openai_client = None
        self._openai_model  = "gpt-4.1-mini"
        self._gemini_model  = None

        if self.provider == "openai":
            self._init_openai(config)
        elif self.provider == "gemini":
            self._init_gemini(config)
        else:
            print("[AIEngine] No API key — using rule-based fallback for all values.")

    # ------------------------------------------------------------------
    # Initializers
    # ------------------------------------------------------------------

    def _init_openai(self, config: Any) -> None:
        try:
            from openai import OpenAI
            self._openai_client = OpenAI(api_key=getattr(config, "openai_api_key", ""))
            self._openai_model  = getattr(config, "openai_model", "gpt-4.1-mini")
            print(f"[AIEngine] OpenAI ready — model: {self._openai_model}")
        except Exception as exc:
            print(f"[AIEngine] OpenAI init failed: {exc} — switching to rule-based fallback.")
            self.provider = "fallback"

    def _init_gemini(self, config: Any) -> None:
        try:
            import google.generativeai as genai
            self._genai = genai
            genai.configure(api_key=getattr(config, "gemini_api_key", ""))
            self._gemini_model = genai.GenerativeModel(
                model_name=getattr(config, "gemini_model_name", "gemini-2.5-flash"),
                system_instruction=self.system_prompt,
            )
            print(f"[AIEngine] Gemini ready — model: {getattr(config, 'gemini_model_name', 'gemini-2.5-flash')}")
        except Exception as exc:
            print(f"[AIEngine] Gemini init failed: {exc} — switching to rule-based fallback.")
            self.provider = "fallback"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_baseline_values(self, fields: list[dict]) -> dict:
        try:
            prompt = Template("""
Generate baseline VALID values for these form fields.
Return ONLY a pure JSON object with field indexes as string keys:
{"0": <value>, "1": <value>, ...}

Rules:
- range/number  : a number within min/max bounds
- select/radio  : pick one option value exactly as listed in the options array
- checkbox      : true or false
- date          : "YYYY-MM-DD" format only
- Indian mobile : exactly 10 digits starting with 6, 7, 8, or 9
- PAN number    : exactly [A-Z]{5}[0-9]{4}[A-Z] e.g. ABCDE1234F
- email         : realistic fake email
- name fields   : realistic Indian name
- address       : realistic Indian address
- Never include markdown, code blocks, or explanations — pure JSON only

Fields:
{{ fields_json }}
""").render(fields_json=json.dumps(fields, ensure_ascii=True))

            raw = self._call_ai(prompt, expect="dict")
            if not isinstance(raw, dict):
                return self._fallback_baseline_values(fields)

            final: dict[str, Any] = {}
            for field in fields:
                key   = str(field.get("index"))
                value = raw.get(key, self._fallback_single_baseline(field))
                final[key] = self._coerce_value_for_field(field, value, mode="valid")
            return final

        except Exception as exc:
            print(f"[AIEngine] generate_baseline_values error: {exc}")
            return self._fallback_baseline_values(fields)

    def generate_single_field_variation(
        self, field: dict, current_value: Any, variation_type: str
    ) -> Any:
        try:
            if variation_type not in {"invalid", "valid_alternate"}:
                variation_type = "invalid"

            prompt = Template("""
Generate exactly one {{ variation_type }} value for this form field.
Return ONLY pure JSON — no markdown, no explanation:
{"value": <single_value>}

Field:
{{ field_json }}

Current baseline value (this is VALID — do NOT return this):
{{ current_value_json }}
""").render(
                variation_type=variation_type,
                field_json=json.dumps(field, ensure_ascii=True),
                current_value_json=json.dumps(current_value, ensure_ascii=True),
            )

            raw = self._call_ai(prompt, expect="dict")
            if isinstance(raw, dict) and "value" in raw:
                return self._coerce_value_for_field(
                    field, raw["value"],
                    mode="invalid" if variation_type == "invalid" else "valid",
                )
            return self._fallback_variation(field, variation_type, current_value)

        except Exception as exc:
            print(f"[AIEngine] generate_single_field_variation error: {exc}")
            return self._fallback_variation(field, variation_type, current_value)

    def generate_field_invalid_variations(
        self, field: dict, baseline_value: Any
    ) -> list[dict]:
        try:
            prompt = Template("""
Generate multiple INVALID test values for this form field.
Return ONLY a pure JSON array — no markdown, no explanation:
[
  {"variation_name": "empty",        "value": ""},
  {"variation_name": "wrong_format", "value": "..."},
  ...
]

Cover ALL variation types relevant for this specific field type:
- empty           : blank / null / empty string
- wrong_format    : completely wrong format (e.g. "NotADate" for a date field)
- boundary_invalid: just outside valid range (month=13, day=32 for dates)
- too_short       : below minimum length
- too_long        : above maximum length
- special_chars   : contains characters that should be rejected
- wrong_type      : wrong data type (letters in a number-only field)
- future_date     : far future date for DOB or past-only date fields
- starts_wrong    : starts with invalid char (mobile starting with 0 or 1)

Field-specific rules:
- date fields       : always include empty, wrong_format, boundary_invalid (2099-13-13), future_date (2099-01-01)
- Indian mobile/tel : starts_wrong (starts with 0 or 1), too_short (5 digits), contains_letters (9876ABCDEF), empty
- PAN               : empty, too_short (ABCDE1234), lowercase (abcde1234f), all_numbers (1234567890), special_chars (ABC@E1234F)
- loan reference    : empty, special_chars (LN#@!), too_short (LN1), wrong_format (INVALID---)
- email             : empty, missing_at (invalidemail.com), missing_domain (test@), wrong_format (@gmail.com)
- number            : empty, below_min, above_max, contains_letters (abc123)
- text/name         : empty, all_numbers (12345), special_chars (!!@@##), too_long (100+ chars)
- Never return the baseline value as an invalid value
- Return pure JSON array only

Field:
{{ field_json }}

Current baseline value (VALID — do NOT include this as invalid):
{{ baseline_value_json }}
""").render(
                field_json=json.dumps(field, ensure_ascii=True),
                baseline_value_json=json.dumps(baseline_value, ensure_ascii=True),
            )

            raw = self._call_ai(prompt, expect="list")
            if isinstance(raw, list):
                result = [
                    {
                        "variation_name": str(item["variation_name"]),
                        "value": item["value"],
                    }
                    for item in raw
                    if isinstance(item, dict)
                    and "variation_name" in item
                    and "value" in item
                ]
                return result if result else self._fallback_multiple_variations(field)
            return self._fallback_multiple_variations(field)

        except Exception as exc:
            print(f"[AIEngine] generate_field_invalid_variations error: {exc}")
            return self._fallback_multiple_variations(field)

    def analyze_page_errors(self, page_text: str, fields: list[dict]) -> dict:
        try:
            prompt = Template("""
Given the page text and form fields, detect visible validation errors.
Return ONLY a pure JSON object — no markdown:
{"field_index": "error message", ...}

Rules:
- Keys must be field indexes as strings
- Values must be concise error messages found in the page text
- Return {} if no errors found

Fields:
{{ fields_json }}

Page text (first 3000 chars):
{{ page_text }}
""").render(
                fields_json=json.dumps(fields, ensure_ascii=True),
                page_text=(page_text or "")[:3000],
            )

            raw = self._call_ai(prompt, expect="dict")
            if not isinstance(raw, dict):
                return {}
            return {str(k): str(v) for k, v in raw.items()}

        except Exception as exc:
            print(f"[AIEngine] analyze_page_errors error: {exc}")
            return {}

    # ------------------------------------------------------------------
    # Core Router — sends to correct provider
    # ------------------------------------------------------------------

    def _call_ai(self, prompt: str, expect: str = "dict") -> Any:
        if self.provider == "openai":
            return self._call_openai(prompt, expect)
        elif self.provider == "gemini":
            return self._call_gemini(prompt, expect)
        else:
            raise RuntimeError("No AI provider available — using fallback")

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------

    def _call_openai(self, prompt: str, expect: str) -> Any:
        last_exc = None

        for attempt in range(3):
            try:
                # OpenAI JSON mode only works reliably with response_format for dict
                # For lists we ask plainly and parse manually
                kwargs: dict[str, Any] = {
                    "model":       self._openai_model,
                    "temperature": 0.2,
                    "max_tokens":  2000,
                    "messages": [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user",   "content": prompt},
                    ],
                }
                if expect == "dict":
                    kwargs["response_format"] = {"type": "json_object"}

                response = self._openai_client.chat.completions.create(**kwargs)
                text = response.choices[0].message.content or ""
                return self._safe_json_loads(text)

            except Exception as exc:
                last_exc  = exc
                error_str = str(exc)

                if "429" in error_str or "rate_limit" in error_str.lower():
                    match = re.search(
                        r"try again in (\d+\.?\d*)s", error_str, re.IGNORECASE
                    )
                    wait = float(match.group(1)) + 1 if match else 10
                    print(
                        f"[AIEngine] OpenAI rate limited. "
                        f"Waiting {wait:.0f}s — retry {attempt + 1}/3..."
                    )
                    time.sleep(wait)
                    continue

                elif "insufficient_quota" in error_str or "billing" in error_str.lower():
                    print("[AIEngine] OpenAI quota exhausted. Switching to rule-based fallback.")
                    self.provider = "fallback"
                    raise

                else:
                    raise

        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Gemini
    # ------------------------------------------------------------------

    def _call_gemini(self, prompt: str, expect: str) -> Any:
        last_exc = None

        for attempt in range(3):
            try:
                import google.generativeai as genai
                response = self._gemini_model.generate_content(
                    prompt,
                    generation_config=genai.GenerationConfig(
                        response_mime_type="application/json"
                    ),
                )
                text = self._extract_gemini_text(response)
                if not text:
                    raise ValueError("Empty response from Gemini")
                return self._safe_json_loads(text)

            except Exception as exc:
                last_exc  = exc
                error_str = str(exc)

                if "429" in error_str or "quota" in error_str.lower():
                    if "PerDay" in error_str or "per_day" in error_str.lower():
                        print(
                            "[AIEngine] Gemini daily quota exhausted. "
                            "Switching to rule-based fallback."
                        )
                        self.provider = "fallback"
                        raise
                    match = re.search(
                        r"retry_delay\s*\{\s*seconds:\s*(\d+)", error_str
                    )
                    wait = int(match.group(1)) + 2 if match else 15
                    print(
                        f"[AIEngine] Gemini rate limited. "
                        f"Waiting {wait}s — retry {attempt + 1}/3..."
                    )
                    time.sleep(wait)
                    continue
                else:
                    raise

        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_gemini_text(response: Any) -> str:
        text = getattr(response, "text", "") or ""
        if text.strip():
            return text.strip()
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                value = getattr(part, "text", "") or ""
                if value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _safe_json_loads(raw_text: str) -> Any:
        text = raw_text.strip()
        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.splitlines()
            lines = lines[1:] if lines and lines[0].startswith("```") else lines
            lines = lines[:-1] if lines and lines[-1].strip().startswith("```") else lines
            text  = "\n".join(lines).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            for start, end in [("{", "}"), ("[", "]")]:
                s = text.find(start)
                e = text.rfind(end)
                if s != -1 and e != -1 and e > s:
                    try:
                        return json.loads(text[s: e + 1])
                    except Exception:
                        pass
            raise

    # ------------------------------------------------------------------
    # Rule-based Fallbacks (used when no AI available)
    # ------------------------------------------------------------------

    def _fallback_baseline_values(self, fields: list[dict]) -> dict:
        return {
            str(f.get("index")): self._fallback_single_baseline(f)
            for f in fields
        }

    def _fallback_single_baseline(self, field: dict) -> Any:
        field_type = str(field.get("type", "")).lower()
        label_hint = f"{field.get('label', '')} {field.get('name', '')}".lower()
        options    = field.get("options") or []

        if field_type in {"select", "radio"}:
            return options[0][0] if options else ""
        if field_type == "checkbox":
            return False
        if field_type == "checkbox-group":
            return [options[0][0]] if options else []
        if field_type == "range":
            return self._midpoint(field.get("min"), field.get("max"), default=50.0)
        if field_type == "number":
            return self._midpoint(field.get("min"), field.get("max"), default=100.0)
        if field_type == "email":
            return "test.user@example.com"
        if field_type == "tel":
            return "9876543210" if any(
                t in label_hint for t in ("mobile", "phone", "contact")
            ) else "9123456780"
        if field_type == "password":
            return "Pass@1234"
        if field_type == "date":
            return "1995-06-15"
        if field_type == "time":
            return "10:30"
        if field_type == "month":
            return date.today().strftime("%Y-%m")
        if field_type == "datetime-local":
            return "1995-06-15T10:30"
        if field_type in {"contenteditable", "textarea"}:
            return "This is a test input for the field."
        if field_type == "file":
            return ""
        # Context-aware text fallbacks
        if any(t in label_hint for t in ("first", "fname")):
            return "Rohan"
        if any(t in label_hint for t in ("last", "lname", "surname")):
            return "Sharma"
        if "name" in label_hint:
            return "Rohan Sharma"
        if "address" in label_hint:
            return "12 MG Road, Pune - 411001"
        if "city" in label_hint:
            return "Pune"
        if "state" in label_hint:
            return "Maharashtra"
        if "zip" in label_hint or "pin" in label_hint or "postal" in label_hint:
            return "411001"
        if "pan" in label_hint:
            return "ABCDE1234F"
        if "loan" in label_hint:
            return "LN1234567890"
        return "TestValue"

    def _fallback_multiple_variations(self, field: dict) -> list[dict]:
        field_type = str(field.get("type", "")).lower()
        label_hint = f"{field.get('label', '')} {field.get('name', '')}".lower()
        variations: list[dict] = [{"variation_name": "empty", "value": ""}]

        if field_type == "date":
            variations += [
                {"variation_name": "wrong_format",     "value": "NotADate"},
                {"variation_name": "boundary_invalid", "value": "2099-13-13"},
                {"variation_name": "future_date",      "value": "2099-01-01"},
                {"variation_name": "too_short",        "value": "01-01"},
            ]
        elif field_type == "tel" or any(
            t in label_hint for t in ("mobile", "phone")
        ):
            variations += [
                {"variation_name": "starts_wrong",     "value": "1234567890"},
                {"variation_name": "too_short",        "value": "98765"},
                {"variation_name": "contains_letters", "value": "9876ABCDEF"},
                {"variation_name": "all_zeros",        "value": "0000000000"},
            ]
        elif "pan" in label_hint:
            variations += [
                {"variation_name": "too_short",        "value": "ABCDE1234"},
                {"variation_name": "lowercase",        "value": "abcde1234f"},
                {"variation_name": "all_numbers",      "value": "1234567890"},
                {"variation_name": "special_chars",    "value": "ABC@E1234F"},
            ]
        elif "loan" in label_hint:
            variations += [
                {"variation_name": "special_chars",    "value": "LN#@!12345"},
                {"variation_name": "too_short",        "value": "LN1"},
                {"variation_name": "wrong_format",     "value": "INVALID---"},
            ]
        elif field_type == "email":
            variations += [
                {"variation_name": "missing_at",       "value": "invalidemail.com"},
                {"variation_name": "missing_domain",   "value": "test@"},
                {"variation_name": "wrong_format",     "value": "@gmail.com"},
            ]
        elif field_type == "number":
            min_v = self._to_float(field.get("min"))
            max_v = self._to_float(field.get("max"))
            if min_v is not None:
                variations.append(
                    {"variation_name": "below_minimum", "value": min_v - 1}
                )
            if max_v is not None:
                variations.append(
                    {"variation_name": "above_maximum", "value": max_v + 1}
                )
            variations.append(
                {"variation_name": "contains_letters", "value": "abc123"}
            )
        else:
            variations += [
                {"variation_name": "special_chars",    "value": "@#$%^&*()"},
                {"variation_name": "all_numbers",      "value": "12345678"},
                {"variation_name": "too_long",         "value": "A" * 120},
            ]

        return variations

    def _fallback_variation(
        self, field: dict, variation_type: str, current_value: Any
    ) -> Any:
        if variation_type == "invalid":
            field_type = str(field.get("type", "")).lower()
            if field_type in {"checkbox", "checkbox-group"}:
                return False if field_type == "checkbox" else []
            if field_type in {"select", "radio"}:
                return "INVALID_VALUE_123@#$"
            return "" if field.get("required") else "INVALID_VALUE_123@#$"
        baseline = self._fallback_single_baseline(field)
        return baseline if baseline != current_value else "AlternateValue"

    def _coerce_value_for_field(self, field: dict, value: Any, mode: str) -> Any:
        field_type = str(field.get("type", "")).lower()
        options    = field.get("options") or []

        if field_type in {"select", "radio"}:
            option_values = [
                opt[0] for opt in options
                if isinstance(opt, (list, tuple)) and opt
            ]
            return value if value in option_values else (
                option_values[0] if option_values else value
            )

        if field_type == "checkbox":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in {"true", "1", "yes", "y", "on"}
            return bool(value)

        if field_type == "checkbox-group":
            option_values = [
                opt[0] for opt in options
                if isinstance(opt, (list, tuple)) and opt
            ]
            if isinstance(value, list):
                chosen = [i for i in value if i in option_values]
                if chosen:
                    return chosen
            return [option_values[0]] if mode == "valid" and option_values else []

        if field_type in {"number", "range"}:
            parsed = self._to_float(value)
            if parsed is None:
                parsed = self._midpoint(
                    field.get("min"), field.get("max"), default=0.0
                )
            min_v = self._to_float(field.get("min"))
            max_v = self._to_float(field.get("max"))
            if min_v is not None and parsed < min_v:
                parsed = min_v
            if max_v is not None and parsed > max_v:
                parsed = max_v
            return int(parsed) if float(parsed).is_integer() else parsed

        if field_type == "date":
            value_text = str(value)
            if len(value_text) == 10 and value_text.count("-") == 2:
                return value_text
            return "1995-06-15"

        return value

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return None if value is None or value == "" else float(value)
        except (TypeError, ValueError):
            return None

    def _midpoint(
        self,
        min_value: Any,
        max_value: Any,
        integer_only: bool = False,
        default: float = 0.0,
    ) -> float | int:
        min_v = self._to_float(min_value)
        max_v = self._to_float(max_value)
        if min_v is not None and max_v is not None:
            mid = (min_v + max_v) / 2.0
        elif min_v is not None:
            mid = min_v
        elif max_v is not None:
            mid = max_v
        else:
            mid = default
        return int(round(mid)) if integer_only else mid