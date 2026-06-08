"""Core baseline convergence, data-file execution, and variation testing."""

from __future__ import annotations

import csv
import json
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
        passing_baseline_or_side: str | None = None
        baseline_values: dict | None = None
        all_fields: list[dict] = []
        active_fields: list[dict] = []
        or_groups: list[dict] = []

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
                or_groups = detector.detect_or_groups(page, all_fields)
                for group in or_groups:
                    print(f"[OR Group] Detected: {group.get('description', '')}")

                print(f"[Iteration {iteration + 1}] Detected {len(all_fields)} fields total")

                if required_only:
                    active_fields = [f for f in all_fields if f.get("required") and not f.get("skip")]
                    print(f"[Iteration {iteration + 1}] Required-only mode: {len(active_fields)}/{len(all_fields)} fields selected")
                else:
                    active_fields = [f for f in all_fields if not f.get("skip") or f.get("type") == "otp"]

                if len(active_fields) == 0:
                    print(f"[Iteration {iteration + 1}] No active fields - skipping iteration")
                    continue

                if iteration == 0:
                    baseline_values = ai.generate_baseline_values(active_fields)
                else:
                    page_errors = handler.detect_errors(page)
                    _ = ai.analyze_page_errors(" ".join(page_errors), active_fields)
                    baseline_values = ai.generate_baseline_values(active_fields)

                if or_groups:
                    self._apply_or_side_to_values(baseline_values, or_groups, side="before")

                print(f"[Iteration {iteration + 1}] Values: {baseline_values}")

                (
                    validation_msgs,
                    errors,
                    network_responses,
                    fill_verification,
                    success,
                    alert_texts,
                    all_values_used,
                ) = self._traverse_multi_page_form(
                    page=page,
                    url=url,
                    filler=filler,
                    handler=handler,
                    detector=detector,
                    ai=ai,
                    values_override=None,
                    config=config,
                    required_only=required_only,
                    max_pages=10,
                )
                baseline_values = all_values_used

                unstuck_fields = [k for k, v in fill_verification.items() if not v.get("stuck") and v.get("intended")]
                if unstuck_fields:
                    print(f"[Iteration {iteration + 1}] WARNING: {len(unstuck_fields)} fields did not accept their values: {unstuck_fields}")
                status, reason = self._determine_status(
                    validation_msgs,
                    errors,
                    success,
                    alert_texts,
                    fill_verification,
                    network_responses,
                )

                print(f"[Iteration {iteration + 1}] Status: {status} - {reason}")

                results.append({
                    "test_name": f"BASELINE_ITER_{iteration + 1}",
                    "changed_field": None,
                    "changed_value": None,
                    "variation_type": "baseline",
                    "all_values": all_values_used,
                    "validation_messages": validation_msgs,
                    "page_errors": errors,
                    "alert_texts": alert_texts,
                    "network_responses": network_responses,
                    "fill_verification": fill_verification,
                    "status": status,
                    "pass_reason": reason,
                    "url": page.url,
                    "page_number": handler.get_current_page_number(page),
                    "or_groups": or_groups,
                })

                if status == "PASS":
                    passing_baseline = self._page_one_values(all_values_used)
                    passing_baseline_or_side = "before" if or_groups else None
                    print("[Baseline] Passing baseline found. Starting variation tests.")

                    if or_groups:
                        alt_results = self._run_or_alt_baselines(
                            url=url,
                            page=page,
                            ai=ai,
                            filler=filler,
                            handler=handler,
                            detector=detector,
                            active_fields=active_fields,
                            or_groups=or_groups,
                            config=config,
                            required_only=required_only,
                        )
                        results.extend(alt_results)
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
                    "network_responses": [],
                    "fill_verification": {},
                    "status": "ERROR",
                    "pass_reason": str(exc),
                    "url": "",
                    "page_number": 1,
                    "or_groups": or_groups,
                })
                break

        if passing_baseline is None:
            passing_baseline = baseline_values or {}

        if not active_fields:
            print("[TestRunner] No fields detected - cannot run variation tests.")
            return results

        # ----------------------------------------------------------------
        # PHASE 2: MULTI-VARIATION TESTING PER FIELD
        # ----------------------------------------------------------------
        variation_fields = [f for f in active_fields if f.get("type") != "otp"]
        skip_variation_indices: set[int] = set()
        if passing_baseline_or_side == "before":
            skip_variation_indices = self._or_indices_by_side(or_groups, "after")

        for field in variation_fields:
            field_idx_int = int(field["index"])
            if field_idx_int in skip_variation_indices:
                print(f"\n[Field '{field.get('label', field_idx_int)}'] Skipping variation tests; OR alternate side was intentionally blank in baseline.")
                continue

            field_idx = str(field["index"])
            field_label = field.get("label", f"field_{field_idx}")

            variations = ai.generate_field_invalid_variations(
                field, passing_baseline.get(field_idx)
            )

            print(f"\n[Field '{field_label}'] Testing {len(variations)} invalid variations:")
            for v in variations:
                print(f"  - {v['variation_name']}: {repr(v['value'])}")

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

                    (
                        validation_msgs,
                        errors,
                        network_responses,
                        fill_verification,
                        success,
                        alert_texts,
                        all_values_used,
                    ) = self._traverse_multi_page_form(
                        page=page,
                        url=url,
                        filler=filler,
                        handler=handler,
                        detector=detector,
                        ai=ai,
                        values_override=test_values,
                        config=config,
                        required_only=required_only,
                        max_pages=10,
                    )

                    unstuck = [k for k, v in fill_verification.items() if not v.get("stuck") and v.get("intended")]
                    if unstuck:
                        print(f"  [{variation_name}] WARNING: Fields did not accept values: {unstuck}")
                    status, reason = self._determine_status(
                        validation_msgs,
                        errors,
                        success,
                        alert_texts,
                        fill_verification,
                        network_responses,
                    )

                    print(f"  [{variation_name}] -> {status} - {reason}")

                    results.append({
                        "test_name": f"FIELD_{field['index']}_{variation_name.upper()}",
                        "changed_field": field_label,
                        "changed_value": invalid_value,
                        "variation_type": variation_name,
                        "all_values": all_values_used,
                        "validation_messages": validation_msgs,
                        "page_errors": errors,
                        "alert_texts": alert_texts,
                        "network_responses": network_responses,
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
                        "network_responses": [],
                        "fill_verification": {},
                        "status": "ERROR",
                        "pass_reason": str(exc),
                        "url": "",
                        "page_number": 1,
                    })
                    continue

        return results

    def run_with_data(self, url: str, page: Any, config: Settings, data_file_path: str) -> list[dict]:
        detector = FieldDetector()
        filler = FormFiller(
            otp_wait_seconds=getattr(config, "otp_wait_seconds", 180),
            otp_extra_seconds=getattr(config, "otp_extra_seconds", 120),
        )
        handler = MultiPageHandler()
        results: list[dict] = []

        with open(data_file_path, newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            rows = list(reader)

        for row_number, row in enumerate(rows, start=1):
            description = (row.get("description") or "").strip()
            expected = (row.get("expected_result") or "").strip().upper() or None
            values: dict[str, Any] = {}
            matched_fields: dict[str, int] = {}
            fields_to_fill: list[dict] = []

            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)

                try:
                    page.wait_for_selector("input, select, textarea", timeout=10000)
                except Exception:
                    print(f"[DATA_ROW_{row_number}] Warning: No inputs found after 10s wait")

                all_fields = detector.detect(page)
                active_fields = [f for f in all_fields if not f.get("skip") or f.get("type") == "otp"]
                field_matches = self._match_csv_fields(row, active_fields)

                for csv_label, field in field_matches.items():
                    cell_value = row.get(csv_label)
                    if cell_value is None or str(cell_value).strip() == "":
                        continue
                    field_idx = str(field.get("index"))
                    values[field_idx] = cell_value
                    values[csv_label] = cell_value
                    for label_key in self._field_label_candidates(field):
                        values[label_key] = cell_value
                    matched_fields[csv_label] = int(field.get("index"))
                    fields_to_fill.append(field)

                print(f"[DATA_ROW_{row_number}] Matched {len(matched_fields)} fields from CSV")

                (
                    validation_msgs,
                    errors,
                    network_responses,
                    fill_verification,
                    success,
                    alert_texts,
                    all_values_used,
                ) = self._traverse_multi_page_form(
                    page=page,
                    url=url,
                    filler=filler,
                    handler=handler,
                    detector=detector,
                    ai=None,
                    values_override=values,
                    config=config,
                    required_only=False,
                    max_pages=10,
                )
                unstuck = [k for k, v in fill_verification.items() if not v.get("stuck") and v.get("intended")]
                if unstuck:
                    print(f"[DATA_ROW_{row_number}] WARNING: Fields did not accept values: {unstuck}")

                try:
                    body_text = page.inner_text("body")[:5000]
                except Exception:
                    body_text = ""

                actual_status, reason = self._determine_status(
                    validation_msgs,
                    errors,
                    success,
                    alert_texts,
                    fill_verification,
                    network_responses,
                )
                match = actual_status == expected if expected else None

                result = {
                    "test_name": f"DATA_ROW_{row_number}",
                    "description": description,
                    "input_values": dict(row),
                    "matched_fields": matched_fields,
                    "changed_field": None,
                    "changed_value": None,
                    "variation_type": "data_file",
                    "all_values": all_values_used,
                    "validation_messages": validation_msgs,
                    "page_errors": errors,
                    "alert_texts": alert_texts,
                    "network_responses": network_responses,
                    "fill_verification": fill_verification,
                    "status": actual_status,
                    "pass_reason": reason,
                    "body_text": body_text,
                    "url": page.url,
                    "page_number": handler.get_current_page_number(page),
                }
                if expected:
                    result["expected"] = expected
                    result["match"] = match
                results.append(result)

                if expected:
                    print(f"[DATA_ROW_{row_number}] Status: {actual_status} | Expected: {expected} | Match: {match}")
                else:
                    print(f"[DATA_ROW_{row_number}] Status: {actual_status}")

            except Exception as exc:
                print(f"[DATA_ROW_{row_number}] ERROR: {exc}")
                result = {
                    "test_name": f"DATA_ROW_{row_number}",
                    "description": description,
                    "input_values": dict(row),
                    "matched_fields": matched_fields,
                    "changed_field": None,
                    "changed_value": None,
                    "variation_type": "data_file",
                    "all_values": values,
                    "validation_messages": {},
                    "page_errors": [str(exc)],
                    "alert_texts": [],
                    "network_responses": [],
                    "fill_verification": {},
                    "status": "ERROR",
                    "pass_reason": str(exc),
                    "url": "",
                    "page_number": 1,
                }
                if expected:
                    result["expected"] = expected
                    result["match"] = False
                results.append(result)

        return results

    def _traverse_multi_page_form(
        self,
        page: Any,
        url: str,
        filler: FormFiller,
        handler: MultiPageHandler,
        detector: FieldDetector,
        ai: Any,
        values_override: dict | None,
        config: Settings,
        required_only: bool,
        max_pages: int = 10,
    ) -> tuple[dict, list[str], list[dict], dict, bool, list[str], dict]:
        """
        Fill and submit a potentially multi-page form.

        Page 1 keeps raw detector field indexes in accumulated dicts so existing
        baseline/variation code can keep using them. Later pages are namespaced
        as page_N.field_index because detector.detect() re-indexes each page.
        """
        all_validation_msgs: dict = {}
        all_page_errors: list[str] = []
        all_network_responses: list[dict] = []
        all_fill_verification: dict = {}
        all_alert_texts: list[str] = []
        all_values_used: dict = {}

        for page_num in range(1, max_pages + 1):
            page.wait_for_timeout(2000)
            try:
                page.wait_for_selector("input, select, textarea", timeout=8000)
            except Exception:
                pass

            current_fields = detector.detect(page)
            if required_only:
                active = [f for f in current_fields if f.get("required") and not f.get("skip")]
            else:
                active = [f for f in current_fields if not f.get("skip") or f.get("type") == "otp"]

            if not active:
                print(f"[MultiPage] Page {page_num}: no fillable fields detected - stopping traversal")
                break

            page_values, fields_to_fill = self._values_for_traversal_page(
                active=active,
                ai=ai,
                values_override=values_override,
                page_num=page_num,
            )
            if values_override is None and page_values:
                page_or_groups = detector.detect_or_groups(page, current_fields)
                if page_or_groups:
                    for group in page_or_groups:
                        print(f"[OR Group] Detected during traversal: {group.get('description', '')}")
                    self._apply_or_side_to_values(page_values, page_or_groups, side="before")

            if fields_to_fill:
                print(f"[MultiPage] Page {page_num}: filling {len(fields_to_fill)} fields")
                validation_msgs = filler.fill_all(page, fields_to_fill, page_values)
                fill_verification = filler.verify_fills(page, fields_to_fill, page_values)
                self._merge_page_dict(all_validation_msgs, validation_msgs, page_num)
                self._merge_page_dict(all_fill_verification, fill_verification, page_num)
                self._merge_page_dict(all_values_used, page_values, page_num)
            else:
                print(f"[MultiPage] Page {page_num}: no values available - attempting to continue")

            interim_errors = handler.detect_errors(page)
            if interim_errors:
                all_page_errors.extend(interim_errors)
                print(f"[MultiPage] Page {page_num}: errors detected before clicking next - stopping")
                break

            click_result, alert_texts, network_responses = handler.click_submit_or_next(page)
            all_alert_texts.extend(alert_texts)
            all_network_responses.extend(network_responses)

            print(f"[MultiPage] Page {page_num}: click result = {click_result}")

            if click_result == "nothing_clicked":
                print(f"[MultiPage] Page {page_num}: no button found - stopping")
                break

            if click_result == "submitted":
                page.wait_for_timeout(3000)
                post_errors = handler.detect_errors(page)
                all_page_errors.extend(post_errors)
                break

            page.wait_for_timeout(2000)

        final_success = handler.detect_success(page)
        return (
            all_validation_msgs,
            all_page_errors,
            all_network_responses,
            all_fill_verification,
            final_success,
            all_alert_texts,
            all_values_used,
        )

    def _run_or_alt_baselines(
        self,
        url: str,
        page: Any,
        ai: AIEngine,
        filler: FormFiller,
        handler: MultiPageHandler,
        detector: FieldDetector,
        active_fields: list[dict],
        or_groups: list[dict],
        config: Settings,
        required_only: bool,
    ) -> list[dict]:
        alt_results: list[dict] = []
        if not or_groups:
            return alt_results

        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            try:
                page.wait_for_selector("input, select, textarea", timeout=10000)
            except Exception:
                pass

            alt_values = ai.generate_baseline_values(active_fields)
            self._apply_or_side_to_values(alt_values, or_groups, side="after")
            print("[Baseline OR Alt 1] Values:", alt_values)

            (
                validation_msgs,
                errors,
                network_responses,
                fill_verification,
                success,
                alert_texts,
                all_values_used,
            ) = self._traverse_multi_page_form(
                page=page,
                url=url,
                filler=filler,
                handler=handler,
                detector=detector,
                ai=ai,
                values_override=alt_values,
                config=config,
                required_only=required_only,
                max_pages=10,
            )
            unstuck = [k for k, v in fill_verification.items() if not v.get("stuck") and v.get("intended")]
            if unstuck:
                print(f"[Baseline OR Alt 1] WARNING: Fields did not accept values: {unstuck}")
            status, reason = self._determine_status(
                validation_msgs,
                errors,
                success,
                alert_texts,
                fill_verification,
                network_responses,
            )

            print(f"[Baseline OR Alt 1] Status: {status} - {reason}")
            alt_results.append({
                "test_name": "BASELINE_OR_ALT_1",
                "changed_field": None,
                "changed_value": None,
                "variation_type": "baseline_or_alt",
                "all_values": all_values_used,
                "validation_messages": validation_msgs,
                "page_errors": errors,
                "alert_texts": alert_texts,
                "network_responses": network_responses,
                "fill_verification": fill_verification,
                "status": status,
                "pass_reason": reason,
                "url": page.url,
                "page_number": handler.get_current_page_number(page),
                "or_groups": or_groups,
            })
        except Exception as exc:
            print(f"[Baseline OR Alt 1] ERROR: {exc}")
            alt_results.append({
                "test_name": "BASELINE_OR_ALT_1",
                "changed_field": None,
                "changed_value": None,
                "variation_type": "baseline_or_alt",
                "all_values": {},
                "validation_messages": {},
                "page_errors": [str(exc)],
                "alert_texts": [],
                "network_responses": [],
                "fill_verification": {},
                "status": "ERROR",
                "pass_reason": str(exc),
                "url": "",
                "page_number": 1,
                "or_groups": or_groups,
            })

        return alt_results

    def _values_for_traversal_page(
        self,
        active: list[dict],
        ai: Any,
        values_override: dict | None,
        page_num: int,
    ) -> tuple[dict, list[dict]]:
        if values_override is None:
            page_values = ai.generate_baseline_values(active) if ai is not None else {}
            return page_values, active if page_values else []

        if page_num > 1 and ai is not None:
            page_values = ai.generate_baseline_values(active)
            return page_values, active if page_values else []

        page_values: dict[str, Any] = {}
        fields_to_fill: list[dict] = []
        for field in active:
            matched_value = self._value_for_field_from_override(field, values_override)
            if matched_value is None or str(matched_value).strip() == "":
                continue
            field_idx = str(field.get("index"))
            page_values[field_idx] = matched_value
            fields_to_fill.append(field)
        return page_values, fields_to_fill

    @staticmethod
    def _value_for_field_from_override(field: dict, values_override: dict) -> Any:
        field_idx = str(field.get("index"))
        if field_idx in values_override:
            return values_override.get(field_idx)

        field_candidates = TestRunner._field_label_candidates(field)
        for key, value in values_override.items():
            if value is None or str(value).strip() == "":
                continue
            normalized_key = TestRunner._normalize_label(key)
            if not normalized_key or normalized_key in {"expected_result", "description"}:
                continue
            for candidate in field_candidates:
                if TestRunner._match_score(normalized_key, candidate) >= 50:
                    return value
        return None

    @staticmethod
    def _merge_page_dict(target: dict, page_data: dict, page_num: int) -> None:
        for key, value in page_data.items():
            out_key = str(key) if page_num == 1 else f"page_{page_num}.{key}"
            target[out_key] = value

    @staticmethod
    def _page_one_values(values: dict) -> dict:
        return {
            str(key): value
            for key, value in values.items()
            if not str(key).startswith("page_")
        }

    @staticmethod
    def _determine_status(
        validation_msgs: dict,
        page_errors: list[str],
        success: bool,
        alert_texts: list[str] | None = None,
        fill_verification: dict | None = None,
        network_responses: list[dict] | None = None,
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

        # Priority 5: Fill verification - if the field we're testing didn't stick
        if fill_verification:
            unstuck = [k for k, v in fill_verification.items() if not v.get("stuck") and v.get("intended")]
            if unstuck:
                return "PASS", f"Form may have rejected input silently (fields {unstuck} value did not stick)"

        # Priority 6: API/network response text
        if network_responses:
            fail_text, success_text = TestRunner._scan_network_status(network_responses)
            if fail_text:
                return "FAIL", f"Server returned error in API response: {fail_text[:80]}"
            if success_text:
                return "PASS", f"Server API confirmed success: {success_text[:40]}"

        return "PASS", "Values accepted client-side (no browser, alert, or element errors)"

    @staticmethod
    def _apply_or_side_to_values(values: dict, or_groups: list[dict], side: str) -> None:
        blank_side = "after_indices" if side == "before" else "before_indices"
        for group in or_groups:
            for field_index in group.get(blank_side, []):
                values[str(field_index)] = None

    @staticmethod
    def _or_indices_by_side(or_groups: list[dict], side: str) -> set[int]:
        key = f"{side}_indices"
        indices: set[int] = set()
        for group in or_groups:
            indices.update(int(i) for i in group.get(key, []))
        return indices

    @staticmethod
    def _match_csv_fields(row: dict, fields: list[dict]) -> dict[str, dict]:
        skip_columns = {"expected_result", "description"}
        matches: dict[str, dict] = {}
        used_indices: set[int] = set()

        for column in row.keys():
            normalized_column = TestRunner._normalize_label(column)
            if not normalized_column or normalized_column in skip_columns:
                continue

            best_field = None
            best_score = -1
            for field in fields:
                field_index = int(field.get("index"))
                if field_index in used_indices:
                    continue
                for candidate in TestRunner._field_label_candidates(field):
                    score = TestRunner._match_score(normalized_column, candidate)
                    if score > best_score:
                        best_score = score
                        best_field = field

            if best_field is not None and best_score > 0:
                matches[column] = best_field
                used_indices.add(int(best_field.get("index")))

        return matches

    @staticmethod
    def _field_label_candidates(field: dict) -> list[str]:
        raw_values = [
            field.get("label", ""),
            field.get("name", ""),
            field.get("id", ""),
        ]
        return [TestRunner._normalize_label(v) for v in raw_values if TestRunner._normalize_label(v)]

    @staticmethod
    def _normalize_label(value: Any) -> str:
        return " ".join(str(value or "").strip().lower().split())

    @staticmethod
    def _match_score(column_label: str, field_label: str) -> int:
        if not column_label or not field_label:
            return 0
        if column_label == field_label:
            return 100
        if column_label in field_label or field_label in column_label:
            return 80

        column_parts = set(column_label.replace("_", " ").split())
        field_parts = set(field_label.replace("_", " ").split())
        if column_parts and field_parts:
            overlap = column_parts.intersection(field_parts)
            if overlap:
                return 50 + len(overlap)
        return 0

    @staticmethod
    def _scan_network_status(network_responses: list[dict]) -> tuple[str | None, str | None]:
        first_success: str | None = None
        for response in network_responses:
            text = str(response.get("response_text") or "")
            if not text.strip():
                continue
            try:
                payload = json.loads(text)
            except Exception:
                continue

            fail_text = TestRunner._find_json_error(payload)
            if fail_text:
                return fail_text, first_success

            if first_success is None:
                first_success = TestRunner._find_json_success(payload)

        return None, first_success

    @staticmethod
    def _find_json_error(value: Any) -> str | None:
        error_words = ["error", "fail", "failed", "failure", "invalid", "incorrect", "required"]
        if isinstance(value, dict):
            status_value = str(value.get("status", "")).strip().lower()
            if status_value in {"failure", "failed", "error"}:
                return str(value.get("message") or value.get("error") or status_value)

            for key, child in value.items():
                key_text = str(key).strip().lower()
                if key_text in {"error", "errorcode"} and child not in (None, "", False):
                    return str(child)
                if key_text == "message" and child:
                    child_text = str(child)
                    if any(word in child_text.lower() for word in error_words):
                        return child_text

                nested = TestRunner._find_json_error(child)
                if nested:
                    return nested
        elif isinstance(value, list):
            for item in value:
                nested = TestRunner._find_json_error(item)
                if nested:
                    return nested
        return None

    @staticmethod
    def _find_json_success(value: Any) -> str | None:
        success_keys = {"success", "applicationid", "referencenumber", "trackingid"}
        if isinstance(value, dict):
            for key, child in value.items():
                key_text = str(key).strip().lower()
                if key_text in success_keys and child not in (None, "", False):
                    return str(child)
                nested = TestRunner._find_json_success(child)
                if nested:
                    return nested
        elif isinstance(value, list):
            for item in value:
                nested = TestRunner._find_json_success(item)
                if nested:
                    return nested
        return None
