"""Core baseline convergence and N+1 variation execution."""

from __future__ import annotations

from typing import Any

from ai_engine import AIEngine
from config import Settings
from field_detector import FieldDetector
from form_filler import FormFiller
from multi_page_handler import MultiPageHandler


class TestRunner:

    def run(self, url: str, page: Any, config: Settings) -> list[dict]:
        results: list[dict] = []
        ai = AIEngine(config)
        detector = FieldDetector()
        filler = FormFiller()
        handler = MultiPageHandler()

        passing_baseline: dict | None = None
        baseline_values: dict | None = None
        fields: list[dict] = []

        # PHASE 1: BASELINE CONVERGENCE
        for iteration in range(config.max_convergence_iterations):
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                fields = detector.detect(page)
                print(f"[Iteration {iteration + 1}] Detected {len(fields)} fields")

                if iteration == 0:
                    baseline_values = ai.generate_baseline_values(fields)
                else:
                    page_errors = handler.detect_errors(page)
                    _ = ai.analyze_page_errors(" ".join(page_errors), fields)
                    baseline_values = ai.generate_baseline_values(fields)

                print(f"[Iteration {iteration + 1}] Values: {baseline_values}")

                validation_msgs = filler.fill_all(page, fields, baseline_values)
                _ = handler.click_submit_or_next(page)
                page.wait_for_timeout(2000)

                errors = handler.detect_errors(page)
                success = handler.detect_success(page)
                status, reason = self._determine_status(
                    validation_msgs, errors, success
                )

                print(f"[Iteration {iteration + 1}] Status: {status} — {reason}")

                results.append({
                    "test_name": f"BASELINE_ITER_{iteration + 1}",
                    "changed_field": None,
                    "changed_value": None,
                    "all_values": baseline_values,
                    "validation_messages": validation_msgs,
                    "page_errors": errors,
                    "status": status,
                    "pass_reason": reason,
                    "url": page.url,
                    "page_number": handler.get_current_page_number(page),
                })

                if status == "PASS":
                    passing_baseline = baseline_values
                    print("[Baseline] Passing baseline found. Starting variation tests.")
                    break

            except Exception as exc:
                print(f"[Iteration {iteration + 1}] Error: {exc}")
                results.append({
                    "test_name": f"BASELINE_ITER_{iteration + 1}",
                    "changed_field": None,
                    "changed_value": None,
                    "all_values": baseline_values or {},
                    "validation_messages": {},
                    "page_errors": [str(exc)],
                    "status": "ERROR",
                    "pass_reason": str(exc),
                    "url": "",
                    "page_number": 1,
                })
                break

        if passing_baseline is None:
            passing_baseline = baseline_values or {}

        # PHASE 2: N+1 VARIATION TESTING
        for field in fields:
            if field.get("skip"):
                continue

            field_idx = str(field["index"])
            field_label = field.get("label", f"field_{field_idx}")
            test_values: dict = {}

            try:
                invalid_value = ai.generate_single_field_variation(
                    field, passing_baseline.get(field_idx), "invalid"
                )

                test_values = passing_baseline.copy()
                test_values[field_idx] = invalid_value

                print(f"[Variation] '{field_label}' → invalid: {invalid_value}")

                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)

                validation_msgs = filler.fill_all(page, fields, test_values)
                _ = handler.click_submit_or_next(page)
                page.wait_for_timeout(2000)

                errors = handler.detect_errors(page)
                success = handler.detect_success(page)
                status, reason = self._determine_status(
                    validation_msgs, errors, success
                )

                print(f"[Variation] '{field_label}' → {status} — {reason}")

                results.append({
                    "test_name": f"FIELD_{field['index']}_INVALID",
                    "changed_field": field_label,
                    "changed_value": invalid_value,
                    "all_values": test_values,
                    "validation_messages": validation_msgs,
                    "page_errors": errors,
                    "status": status,
                    "pass_reason": reason,
                    "url": page.url,
                    "page_number": handler.get_current_page_number(page),
                })

            except Exception as exc:
                print(f"[Variation] Error on '{field_label}': {exc}")
                results.append({
                    "test_name": f"FIELD_{field['index']}_INVALID",
                    "changed_field": field_label,
                    "changed_value": None,
                    "all_values": test_values,
                    "validation_messages": {},
                    "page_errors": [str(exc)],
                    "status": "ERROR",
                    "pass_reason": str(exc),
                    "url": "",
                    "page_number": 1,
                })
                continue

        return results

    @staticmethod
    def _determine_status(
        validation_msgs: dict,
        page_errors: list[str],
        success: bool,
    ) -> tuple[str, str]:
        """
        Determine PASS or FAIL based on client-side signals only.

        Rules (in priority order):
        1. Server confirmed success (thank you page, URL change) → PASS
        2. Browser HTML5 validation fired on any field → FAIL
           (field.validationMessage is non-empty — format was rejected)
        3. Visible error elements on page (aria-invalid, .error classes) → FAIL
        4. No browser validation errors + no visible error elements → PASS
           (values were format-correct and accepted client-side;
            server rejection due to fake data is ignored intentionally)
        """
        if success:
            return "PASS", "Server confirmed successful submission"

        # Check browser-level HTML5 validation messages
        browser_errors = {
            k: v for k, v in validation_msgs.items()
            if v and str(v).strip()
        }
        if browser_errors:
            fields_with_errors = ", ".join(browser_errors.keys())
            return "FAIL", f"Browser validation failed on fields: {fields_with_errors}"

        # Check visible error elements (CSS-based, not keyword scan)
        if page_errors:
            return "FAIL", f"Visible error elements detected: {page_errors[0][:80]}"

        # No client-side errors — values were valid format-wise
        return "PASS", "Values accepted client-side (no browser or element errors)"