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
        filler = FormFiller(
            otp_wait_seconds=getattr(config, "otp_wait_seconds", 180),
            otp_extra_seconds=getattr(config, "otp_extra_seconds", 120),
        )
        handler = MultiPageHandler()
        required_only: bool = getattr(config, "required_only", False)

        passing_baseline: dict | None = None
        baseline_values: dict | None = None
        all_fields: list[dict] = []
        active_fields: list[dict] = []

        # ----------------------------------------------------------------
        # PHASE 1: BASELINE CONVERGENCE
        # ----------------------------------------------------------------
        for iteration in range(config.max_convergence_iterations):
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)

                try:
                    page.wait_for_selector("input, select, textarea", timeout=10000)
                except Exception:
                    print(f"[Iteration {iteration + 1}] Warning: No inputs found after 10s wait")

                all_fields = detector.detect(page)
                print(f"[Iteration {iteration + 1}] Detected {len(all_fields)} fields total")

                if required_only:
                    active_fields = [f for f in all_fields if f.get("required") and not f.get("skip")]
                    print(f"[Iteration {iteration + 1}] Required-only mode: {len(active_fields)}/{len(all_fields)} fields selected")
                else:
                    active_fields = [f for f in all_fields if not f.get("skip") or f.get("type") == "otp"]

                if len(active_fields) == 0:
                    print(f"[Iteration {iteration + 1}] No active fields — skipping iteration")
                    continue

                if iteration == 0:
                    baseline_values = ai.generate_baseline_values(active_fields)
                else:
                    page_errors = handler.detect_errors(page)
                    _ = ai.analyze_page_errors(" ".join(page_errors), active_fields)
                    baseline_values = ai.generate_baseline_values(active_fields)

                print(f"[Iteration {iteration + 1}] Values: {baseline_values}")

                validation_msgs = filler.fill_all(page, active_fields, baseline_values)

                # Post-fill verification
                fill_verification = filler.verify_fills(page, active_fields, baseline_values)
                unstuck_fields = [k for k, v in fill_verification.items() if not v.get("stuck") and v.get("intended")]
                if unstuck_fields:
                    print(f"[Iteration {iteration + 1}] WARNING: {len(unstuck_fields)} fields did not accept their values: {unstuck_fields}")

                click_result, alert_texts = handler.click_submit_or_next(page)
                page.wait_for_timeout(2000)

                errors = handler.detect_errors(page)
                success = handler.detect_success(page)
                status, reason = self._determine_status(
                    validation_msgs, errors, success, alert_texts, fill_verification
                )

                print(f"[Iteration {iteration + 1}] Status: {status} — {reason}")

                results.append({
                    "test_name": f"BASELINE_ITER_{iteration + 1}",
                    "changed_field": None,
                    "changed_value": None,
                    "variation_type": "baseline",
                    "all_values": baseline_values,
                    "validation_messages": validation_msgs,
                    "page_errors": errors,
                    "alert_texts": alert_texts,
                    "fill_verification": fill_verification,
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
                    "variation_type": "baseline",
                    "all_values": baseline_values or {},
                    "validation_messages": {},
                    "page_errors": [str(exc)],
                    "alert_texts": [],
                    "fill_verification": {},
                    "status": "ERROR",
                    "pass_reason": str(exc),
                    "url": "",
                    "page_number": 1,
                })
                break

        if passing_baseline is None:
            passing_baseline = baseline_values or {}

        if not active_fields:
            print("[TestRunner] No fields detected — cannot run variation tests.")
            return results

        # ----------------------------------------------------------------
        # PHASE 2: MULTI-VARIATION TESTING PER FIELD
        # ----------------------------------------------------------------
        variation_fields = [f for f in active_fields if f.get("type") != "otp"]

        for field in variation_fields:
            field_idx = str(field["index"])
            field_label = field.get("label", f"field_{field_idx}")

            variations = ai.generate_field_invalid_variations(
                field, passing_baseline.get(field_idx)
            )

            print(f"\n[Field '{field_label}'] Testing {len(variations)} invalid variations:")
            for v in variations:
                print(f"  • {v['variation_name']}: {repr(v['value'])}")

            for variation in variations:
                variation_name = variation.get("variation_name", "invalid")
                invalid_value = variation.get("value", "")
                test_values: dict = {}

                try:
                    test_values = passing_baseline.copy()
                    test_values[field_idx] = invalid_value

                    page.goto(url, wait_until="domcontentloaded")
                    page.wait_for_timeout(5000)
                    try:
                        page.wait_for_selector("input, select, textarea", timeout=10000)
                    except Exception:
                        pass

                    validation_msgs = filler.fill_all(page, active_fields, test_values)

                    # Post-fill verification
                    fill_verification = filler.verify_fills(page, active_fields, test_values)
                    unstuck = [k for k, v in fill_verification.items() if not v.get("stuck") and v.get("intended")]
                    if unstuck:
                        print(f"  [{variation_name}] WARNING: Fields did not accept values: {unstuck}")

                    click_result, alert_texts = handler.click_submit_or_next(page)
                    page.wait_for_timeout(2000)

                    errors = handler.detect_errors(page)
                    success = handler.detect_success(page)
                    status, reason = self._determine_status(
                        validation_msgs, errors, success, alert_texts, fill_verification
                    )

                    print(f"  [{variation_name}] → {status} — {reason}")

                    results.append({
                        "test_name": f"FIELD_{field['index']}_{variation_name.upper()}",
                        "changed_field": field_label,
                        "changed_value": invalid_value,
                        "variation_type": variation_name,
                        "all_values": test_values,
                        "validation_messages": validation_msgs,
                        "page_errors": errors,
                        "alert_texts": alert_texts,
                        "fill_verification": fill_verification,
                        "status": status,
                        "pass_reason": reason,
                        "url": page.url,
                        "page_number": handler.get_current_page_number(page),
                    })

                except Exception as exc:
                    print(f"  [{variation_name}] ERROR: {exc}")
                    results.append({
                        "test_name": f"FIELD_{field['index']}_{variation_name.upper()}",
                        "changed_field": field_label,
                        "changed_value": invalid_value if invalid_value else None,
                        "variation_type": variation_name,
                        "all_values": test_values,
                        "validation_messages": {},
                        "page_errors": [str(exc)],
                        "alert_texts": [],
                        "fill_verification": {},
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
        alert_texts: list[str] | None = None,
        fill_verification: dict | None = None,
    ) -> tuple[str, str]:
        # Priority 1: JS alert/confirm dialog
        if alert_texts:
            first_alert = alert_texts[0][:100]
            error_words = ["invalid", "incorrect", "error", "failed", "cannot", "wrong", "required"]
            if any(w in first_alert.lower() for w in error_words):
                return "FAIL", f"Alert dialog: {first_alert}"
            # Alert might be success confirmation
            success_words = ["success", "submitted", "thank", "confirmed", "approved"]
            if any(w in first_alert.lower() for w in success_words):
                return "PASS", f"Alert confirmed success: {first_alert}"

        # Priority 2: Server confirmed success
        if success:
            return "PASS", "Server confirmed successful submission"

        # Priority 3: Browser native validation messages
        browser_errors = {k: v for k, v in validation_msgs.items() if v and str(v).strip()}
        if browser_errors:
            fields_with_errors = ", ".join(browser_errors.keys())
            return "FAIL", f"Browser validation failed on fields: {fields_with_errors}"

        # Priority 4: Visible page error elements
        if page_errors:
            return "FAIL", f"Visible error elements detected: {page_errors[0][:80]}"

        # Priority 5: Fill verification — if the field we're testing didn't stick
        if fill_verification:
            unstuck = [k for k, v in fill_verification.items() if not v.get("stuck") and v.get("intended")]
            if unstuck:
                return "PASS", f"Form may have rejected input silently (fields {unstuck} value did not stick)"

        return "PASS", "Values accepted client-side (no browser, alert, or element errors)"