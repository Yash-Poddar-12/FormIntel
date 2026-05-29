"""Gemini-backed AI engine for smart_form_tester."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import google.generativeai as genai
from jinja2 import Template


class AIEngine:
    """Generate baseline and variation test data using Gemini with safe fallbacks."""

    def __init__(self, config: Any) -> None:
        self.config = config
        # REPLACE WITH:
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
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[AIEngine] Initialization error: {exc}")
            self.model = None

    def generate_baseline_values(self, fields: list[dict]) -> dict:
        """Return mapping {'0': value, '1': value, ...} for baseline valid data."""
        try:
            prompt = Template(
                """
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
"""
            ).render(fields_json=json.dumps(fields, ensure_ascii=True))

            raw = self._generate_json(prompt)
            if not isinstance(raw, dict):
                return self._fallback_baseline_values(fields)

            final: dict[str, Any] = {}
            for field in fields:
                key = str(field.get("index"))
                value = raw.get(key, self._fallback_single_baseline(field))
                final[key] = self._coerce_value_for_field(field, value, mode="valid")
            return final
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[AIEngine] generate_baseline_values error: {exc}")
            return self._fallback_baseline_values(fields)

    def generate_single_field_variation(
        self, field: dict, current_value: Any, variation_type: str
    ) -> Any:
        """Return one alternative value for a single field."""
        try:
            if variation_type not in {"invalid", "valid_alternate"}:
                variation_type = "invalid"

            prompt = Template(
                """
Generate exactly one {{ variation_type }} value for this field.
Return pure JSON only as:
{"value": <single_value>}

Field:
{{ field_json }}

Current value:
{{ current_value_json }}
"""
            ).render(
                variation_type=variation_type,
                field_json=json.dumps(field, ensure_ascii=True),
                current_value_json=json.dumps(current_value, ensure_ascii=True),
            )

            raw = self._generate_json(prompt)
            if isinstance(raw, dict) and "value" in raw:
                return self._coerce_value_for_field(
                    field, raw["value"], mode="invalid" if variation_type == "invalid" else "valid"
                )
            return self._fallback_variation(field, variation_type, current_value)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[AIEngine] generate_single_field_variation error: {exc}")
            return self._fallback_variation(field, variation_type, current_value)

    def analyze_page_errors(self, page_text: str, fields: list[dict]) -> dict:
        """Return mapping {'field_index': 'error message detected', ...}."""
        try:
            prompt = Template(
                """
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
"""
            ).render(
                fields_json=json.dumps(fields, ensure_ascii=True),
                page_text=page_text or "",
            )

            raw = self._generate_json(prompt)
            if not isinstance(raw, dict):
                return {}
            cleaned: dict[str, str] = {}
            for key, value in raw.items():
                cleaned[str(key)] = str(value)
            return cleaned
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[AIEngine] analyze_page_errors error: {exc}")
            return {}

    def _generate_json(self, prompt: str) -> Any:
        if self.model is None:
            raise RuntimeError("Gemini model is not initialized")
        response = self.model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(response_mime_type="application/json"),
        )
        text = self._extract_text_from_response(response)
        if not text:
            raise ValueError("Empty response from Gemini")
        return self._safe_json_loads(text)

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
            parts = getattr(content, "parts", None) or []
            for part in parts:
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
                return json.loads(text[first_brace : last_brace + 1])
            first_bracket = text.find("[")
            last_bracket = text.rfind("]")
            if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
                return json.loads(text[first_bracket : last_bracket + 1])
            raise

    def _fallback_baseline_values(self, fields: list[dict]) -> dict:
        output: dict[str, Any] = {}
        for field in fields:
            output[str(field.get("index"))] = self._fallback_single_baseline(field)
        return output

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
            return self._midpoint(field.get("min"), field.get("max"), integer_only=False, default=50.0)
        if field_type == "number":
            return self._midpoint(field.get("min"), field.get("max"), integer_only=False, default=100.0)
        if field_type == "email":
            return "test@example.com"
        if field_type == "tel":
            if any(token in label_hint for token in ("mobile", "phone", "contact")):
                return "9876543210"
            return "9123456780"
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
        if field_type == "contenteditable":
            return "Test content"
        if field_type == "textarea":
            return "This is a test input."
        if field_type == "file":
            return ""
        return "TestValue"

    def _fallback_variation(self, field: dict, variation_type: str, current_value: Any) -> Any:
        if variation_type == "invalid":
            field_type = str(field.get("type", "")).lower()
            if field_type in {"checkbox", "checkbox-group"}:
                return False if field_type == "checkbox" else []
            if field_type in {"select", "radio"}:
                return "INVALID_VALUE_123@#$"
            return "" if field.get("required") else "INVALID_VALUE_123@#$"

        baseline = self._fallback_single_baseline(field)
        if baseline != current_value:
            return baseline

        field_type = str(field.get("type", "")).lower()
        if field_type == "email":
            return "alternate.user@example.com"
        if field_type == "tel":
            return "9898989898"
        if field_type == "number":
            return (baseline or 100) + 1 if isinstance(baseline, (int, float)) else 101
        if field_type == "date":
            return "2026-01-15"
        if field_type == "checkbox":
            return not bool(current_value)
        if field_type == "checkbox-group":
            options = field.get("options") or []
            if len(options) > 1:
                return [options[-1][0]]
            return []
        if field_type in {"select", "radio"}:
            options = field.get("options") or []
            if len(options) > 1:
                return options[1][0]
            return options[0][0] if options else ""
        return "AlternateValue"

    def _coerce_value_for_field(self, field: dict, value: Any, mode: str) -> Any:
        field_type = str(field.get("type", "")).lower()
        options = field.get("options") or []

        if field_type in {"select", "radio"}:
            option_values = [opt[0] for opt in options if isinstance(opt, (list, tuple)) and len(opt) > 0]
            if value in option_values:
                return value
            return option_values[0] if option_values else value

        if field_type == "checkbox":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in {"true", "1", "yes", "y", "on"}
            return bool(value)

        if field_type == "checkbox-group":
            option_values = [opt[0] for opt in options if isinstance(opt, (list, tuple)) and len(opt) > 0]
            if isinstance(value, list):
                chosen = [item for item in value if item in option_values]
                if chosen:
                    return chosen
            if mode == "valid" and option_values:
                return [option_values[0]]
            return []

        if field_type in {"number", "range"}:
            parsed = self._to_float(value)
            if parsed is None:
                parsed = self._midpoint(field.get("min"), field.get("max"), integer_only=False, default=0.0)
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
            if value is None or value == "":
                return None
            return float(value)
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
        if integer_only:
            return int(round(mid))
        return mid

