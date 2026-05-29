"""Gemini-backed AI engine for smart_form_tester."""

from __future__ import annotations

import json
import re
import time
from datetime import date
from typing import Any

import google.generativeai as genai
from jinja2 import Template


class AIEngine:

    def __init__(self, config: Any) -> None:
        self.config = config
        self.api_key = getattr(config, "gemini_api_key", "") or getattr(config, "GEMINI_API_KEY", "")
        self.model_name = getattr(config, "gemini_model_name", "") or getattr(config, "GEMINI_MODEL_NAME", "gemini-2.5-flash")
        self.system_prompt = getattr(config, "system_prompt", "") or getattr(config, "SYSTEM_PROMPT", "")
        self.model: Any | None = None

        try:
            if not self.api_key:
                print("[AIEngine] GEMINI_API_KEY is missing. Falling back to rule-based generation.")
                return
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=self.system_prompt,
            )
        except Exception as exc:
            print(f"[AIEngine] Initialization error: {exc}")
            self.model = None

    def generate_baseline_values(self, fields: list[dict]) -> dict:
        try:
            prompt = Template("""
Generate baseline VALID values for these form fields.
Return pure JSON object only, with keys as field indexes:
{"0": <value>, "1": <value>, ...}

Rules:
- range: number within min/max
- select/radio: pick one of the available option values
- checkbox: return true/false
- date: "YYYY-MM-DD"
- For Indian financial/banking contexts, use Indian-style realistic fake values.
- Never include markdown or explanations.

Fields:
{{ fields_json }}
""").render(fields_json=json.dumps(fields, ensure_ascii=True))

            raw = self._generate_json(prompt)
            if not isinstance(raw, dict):
                return self._fallback_baseline_values(fields)

            final: dict[str, Any] = {}
            for field in fields:
                key = str(field.get("index"))
                value = raw.get(key, self._fallback_single_baseline(field))
                final[key] = self._coerce_value_for_field(field, value, mode="valid")
            return final
        except Exception as exc:
            print(f"[AIEngine] generate_baseline_values error: {exc}")
            return self._fallback_baseline_values(fields)

    def generate_single_field_variation(self, field: dict, current_value: Any, variation_type: str) -> Any:
        try:
            if variation_type not in {"invalid", "valid_alternate"}:
                variation_type = "invalid"

            prompt = Template("""
Generate exactly one {{ variation_type }} value for this field.
Return pure JSON only as:
{"value": <single_value>}

Field:
{{ field_json }}

Current value:
{{ current_value_json }}
""").render(
                variation_type=variation_type,
                field_json=json.dumps(field, ensure_ascii=True),
                current_value_json=json.dumps(current_value, ensure_ascii=True),
            )

            raw = self._generate_json(prompt)
            if isinstance(raw, dict) and "value" in raw:
                return self._coerce_value_for_field(
                    field, raw["value"],
                    mode="invalid" if variation_type == "invalid" else "valid"
                )
            return self._fallback_variation(field, variation_type, current_value)
        except Exception as exc:
            print(f"[AIEngine] generate_single_field_variation error: {exc}")
            return self._fallback_variation(field, variation_type, current_value)

    def generate_field_invalid_variations(self, field: dict, baseline_value: Any) -> list[dict]:
        """
        Return a list of {"variation_name": str, "value": any} dicts.
        Each covers a different type of invalidity for thorough field testing.
        """
        try:
            prompt = Template("""
Generate multiple INVALID test values for this form field.
Return a pure JSON array ONLY — no markdown, no explanation:
[
  {"variation_name": "empty", "value": ""},
  {"variation_name": "wrong_format", "value": "..."},
  ...
]

Cover ALL of these variation types that are relevant for this specific field:
- empty          : blank / null / empty string
- wrong_format   : completely wrong format (e.g. "NotADate" for a date field)
- boundary_invalid : value just outside valid range (e.g. month=13, day=32 for dates)
- too_short      : below minimum length
- too_long       : above maximum length
- special_chars  : contains special characters that should be rejected
- wrong_type     : wrong data type (e.g. letters in a number-only field)
- future_date    : a far future date (for DOB or past-only date fields)
- starts_wrong   : starts with an invalid character/digit (e.g. mobile starting with 0 or 1)

Rules:
- Only include variations that would ACTUALLY BE INVALID for this specific field
- For date fields ALWAYS include: empty, wrong_format, boundary_invalid (month=13), future_date
- For Indian mobile/tel: starts_wrong (starts with 0 or 1), too_short (5 digits), contains_letters, empty
- For PAN (pattern [A-Z]{5}[0-9]{4}[A-Z]{1}): empty, too_short, lowercase, special_chars, wrong_format
- For loan reference: empty, special_chars, too_short
- For email: empty, missing_at_sign, missing_domain, wrong_format
- For number: empty, below_min, above_max, contains_letters
- Never return the baseline value as invalid
- Return pure JSON array only

Field:
{{ field_json }}

Current baseline value (this is VALID — do not return this as invalid):
{{ baseline_value_json }}
""").render(
                field_json=json.dumps(field, ensure_ascii=True),
                baseline_value_json=json.dumps(baseline_value, ensure_ascii=True),
            )

            raw = self._generate_json(prompt)
            if isinstance(raw, list):
                result = []
                for item in raw:
                    if isinstance(item, dict) and "variation_name" in item and "value" in item:
                        result.append({
                            "variation_name": str(item["variation_name"]),
                            "value": item["value"],
                        })
                return result if result else self._fallback_multiple_variations(field)
            return self._fallback_multiple_variations(field)

        except Exception as exc:
            print(f"[AIEngine] generate_field_invalid_variations error: {exc}")
            return self._fallback_multiple_variations(field)

    def analyze_page_errors(self, page_text: str, fields: list[dict]) -> dict:
        try:
            prompt = Template("""
Given the page text and form fields, detect visible validation errors.
Return pure JSON object only:
{"field_index": "error message detected", ...}

Rules:
- Keys must be field indexes as strings.
- Values must be concise human-readable error messages found in page text.
- If none found, return {}.

Fields:
{{ fields_json }}

Page text:
{{ page_text }}
""").render(
                fields_json=json.dumps(fields, ensure_ascii=True),
                page_text=page_text or "",
            )

            raw = self._generate_json(prompt)
            if not isinstance(raw, dict):
                return {}
            return {str(k): str(v) for k, v in raw.items()}
        except Exception as exc:
            print(f"[AIEngine] analyze_page_errors error: {exc}")
            return {}

    def _generate_json(self, prompt: str) -> Any:
        if self.model is None:
            raise RuntimeError("Gemini model is not initialized")

        last_exc: Exception | None = None

        for attempt in range(3):
            try:
                response = self.model.generate_content(
                    prompt,
                    generation_config=genai.GenerationConfig(
                        response_mime_type="application/json"
                    ),
                )
                text = self._extract_text_from_response(response)
                if not text:
                    raise ValueError("Empty response from Gemini")
                return self._safe_json_loads(text)

            except Exception as exc:
                last_exc = exc
                error_str = str(exc)

                if "429" in error_str or "quota" in error_str.lower():
                    # Daily quota exhausted — no point retrying, go to fallback immediately
                    if "PerDay" in error_str or "per_day" in error_str.lower():
                        print("[AIEngine] Daily quota exhausted. Switching to rule-based fallback.")
                        raise

                    # Per-minute quota — wait and retry
                    match = re.search(r"retry_delay\s*\{\s*seconds:\s*(\d+)", error_str)
                    wait = int(match.group(1)) + 2 if match else 15
                    print(f"[AIEngine] Rate limited. Waiting {wait}s before retry {attempt + 1}/3...")
                    time.sleep(wait)
                    continue
                else:
                    raise

        raise last_exc  # type: ignore[misc]

    @staticmethod
    def _extract_text_from_response(response: Any) -> str:
        text = getattr(response, "text", "") or ""
        if text.strip():
            return text.strip()
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", None) or []:
                value = getattr(part, "text", "") or ""
                if value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _safe_json_loads(raw_text: str) -> Any:
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            first_brace = text.find("{")
            last_brace = text.rfind("}")
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                return json.loads(text[first_brace: last_brace + 1])
            first_bracket = text.find("[")
            last_bracket = text.rfind("]")
            if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
                return json.loads(text[first_bracket: last_bracket + 1])
            raise

    def _fallback_baseline_values(self, fields: list[dict]) -> dict:
        return {str(f.get("index")): self._fallback_single_baseline(f) for f in fields}

    def _fallback_single_baseline(self, field: dict) -> Any:
        field_type = str(field.get("type", "")).lower()
        label_hint = f"{field.get('label', '')} {field.get('name', '')}".lower()
        options = field.get("options") or []

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
            return "test@example.com"
        if field_type == "tel":
            return "9876543210" if any(t in label_hint for t in ("mobile", "phone", "contact")) else "9123456780"
        if field_type == "password":
            return "Pass@1234"
        if field_type == "date":
            return date.today().strftime("%Y-%m-%d")
        if field_type == "time":
            return "10:30"
        if field_type == "month":
            return date.today().strftime("%Y-%m")
        if field_type == "datetime-local":
            return f"{date.today().strftime('%Y-%m-%d')}T10:30"
        if field_type in {"contenteditable", "textarea"}:
            return "Test content"
        if field_type == "file":
            return ""
        return "TestValue"

    def _fallback_multiple_variations(self, field: dict) -> list[dict]:
        field_type = str(field.get("type", "")).lower()
        label_hint = f"{field.get('label', '')} {field.get('name', '')}".lower()
        variations: list[dict] = []

        variations.append({"variation_name": "empty", "value": ""})

        if field_type == "date":
            variations += [
                {"variation_name": "wrong_format",     "value": "NotADate"},
                {"variation_name": "boundary_invalid", "value": "2099-13-13"},
                {"variation_name": "future_date",      "value": "2099-01-01"},
                {"variation_name": "too_short",        "value": "01-01"},
            ]
        elif field_type == "tel" or any(t in label_hint for t in ("mobile", "phone")):
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
                variations.append({"variation_name": "below_minimum", "value": min_v - 1})
            if max_v is not None:
                variations.append({"variation_name": "above_maximum", "value": max_v + 1})
            variations.append({"variation_name": "contains_letters", "value": "abc123"})
        else:
            variations += [
                {"variation_name": "special_chars",    "value": "@#$%^&*()"},
                {"variation_name": "wrong_format",     "value": "INVALID_123@#$"},
            ]

        return variations

    def _fallback_variation(self, field: dict, variation_type: str, current_value: Any) -> Any:
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
        options = field.get("options") or []

        if field_type in {"select", "radio"}:
            option_values = [opt[0] for opt in options if isinstance(opt, (list, tuple)) and opt]
            return value if value in option_values else (option_values[0] if option_values else value)

        if field_type == "checkbox":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in {"true", "1", "yes", "y", "on"}
            return bool(value)

        if field_type == "checkbox-group":
            option_values = [opt[0] for opt in options if isinstance(opt, (list, tuple)) and opt]
            if isinstance(value, list):
                chosen = [i for i in value if i in option_values]
                if chosen:
                    return chosen
            return [option_values[0]] if mode == "valid" and option_values else []

        if field_type in {"number", "range"}:
            parsed = self._to_float(value)
            if parsed is None:
                parsed = self._midpoint(field.get("min"), field.get("max"), default=0.0)
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
            return date.today().strftime("%Y-%m-%d")

        return value

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return None if value is None or value == "" else float(value)
        except (TypeError, ValueError):
            return None

    def _midpoint(self, min_value: Any, max_value: Any, integer_only: bool = False, default: float = 0.0) -> float | int:
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