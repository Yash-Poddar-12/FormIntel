"""Core test orchestration for smart_form_tester.

What this file does:
  PHASE 1 — Baseline Convergence:
    Opens the URL, detects fields, asks AI for valid values,
    fills and submits. Retries up to max_iterations until
    the form accepts all values (no browser validation errors).

  NEW — OR Group Detection:
    After finding fields, checks for "OR" separators.
    If found (e.g. "Mobile OR Loan Account Number"),
    generates all pair combinations and tests each one
    as a separate baseline instead of filling all fields.
    For BHFL: tests (Mobile+DOB), (Mobile+PAN),
              (LoanAcc+DOB), (LoanAcc+PAN) = 4 baselines

  PHASE 2 — Variation Testing:
    For each passing baseline + each field in it,
    replaces only that field with an invalid value
    and submits. Records pass/fail per variation type.
"""

from __future__ import annotations
from itertools import product
from typing import Any

from ai_engine import AIEngine
from config import Settings
from field_detector import FieldDetector
from form_filler import FormFiller
from multi_page_handler import MultiPageHandler


class TestRunner:

    def run(self, url: str, page: Any, config: Settings) -> list[dict]:
        results: list[dict] = []
        ai       = AIEngine(config)
        detector = FieldDetector()
        filler   = FormFiller()
        handler  = MultiPageHandler()

        fields: list[dict] = []
        passing_baselines: list[dict] = []  # Can be multiple if OR groups found

        # ----------------------------------------------------------------
        # PHASE 1: BASELINE CONVERGENCE
        # ----------------------------------------------------------------
        for iteration in range(config.max_convergence_iterations):
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)

                try:
                    page.wait_for_selector(
                        "input, select, textarea", timeout=10000
                    )
                except Exception:
                    print(f"[Iteration {iteration + 1}] Warning: No inputs found after 10s")

                fields = detector.detect(page)
                print(f"[Iteration {iteration + 1}] Detected {len(fields)} fields")

                if not fields:
                    print(f"[Iteration {iteration + 1}] No fields — skipping")
                    continue

                # --- Detect OR groups on first iteration only ---
                # OR groups tell us that only ONE field from each group
                # needs to be filled (the others are optional alternatives)
                or_groups: list[dict] = []
                if iteration == 0:
                    or_groups = detector.detect_or_groups(page, fields)

                # --- Generate baseline values for ALL fields ---
                if iteration == 0:
                    baseline_values = ai.generate_baseline_values(fields)
                else:
                    page_errors = handler.detect_errors(page)
                    _ = ai.analyze_page_errors(" ".join(page_errors), fields)
                    baseline_values = ai.generate_baseline_values(fields)

                print(f"[Iteration {iteration + 1}] Values: {baseline_values}")

                # --- If OR groups found, generate pair combinations ---
                # e.g. for BHFL with 2 OR groups of 2 fields each:
                # combinations = [(0,2), (0,3), (1,2), (1,3)]
                # meaning: use field 0 or 1 from group 1, field 2 or 3 from group 2
                if or_groups:
                    combo_value_sets = self._generate_or_combinations(
                        fields, or_groups, baseline_values
                    )
                    print(
                        f"[Iteration {iteration + 1}] OR groups detected — "
                        f"testing {len(combo_value_sets)} combinations"
                    )
                else:
                    combo_value_sets = [baseline_values]

                # --- Test each combination ---
                for combo_idx, combo_values in enumerate(combo_value_sets):
                    combo_label = (
                        f"BASELINE_ITER_{iteration + 1}_COMBO_{combo_idx + 1}"
                        if len(combo_value_sets) > 1
                        else f"BASELINE_ITER_{iteration + 1}"
                    )

                    if combo_idx > 0:
                        # Reload page for each combination
                        page.goto(url, wait_until="domcontentloaded")
                        page.wait_for_timeout(3000)

                    active_fields = self._get_active_fields(
                        fields, combo_values
                    )
                    print(
                        f"  Combo {combo_idx + 1}: filling fields "
                        f"{[f['label'] for f in active_fields]}"
                    )

                    validation_msgs = filler.fill_all(page, active_fields, combo_values)
                    _ = handler.click_submit_or_next(page)
                    page.wait_for_timeout(2000)

                    errors  = handler.detect_errors(page)
                    success = handler.detect_success(page)
                    status, reason = self._determine_status(
                        validation_msgs, errors, success
                    )

                    print(f"  {combo_label}: {status} — {reason}")

                    results.append({
                        "test_name":          combo_label,
                        "changed_field":      None,
                        "changed_value":      None,
                        "variation_type":     "baseline",
                        "all_values":         combo_values,
                        "active_field_labels":[f["label"] for f in active_fields],
                        "validation_messages":validation_msgs,
                        "page_errors":        errors,
                        "status":             status,
                        "pass_reason":        reason,
                        "url":                page.url,
                        "page_number":        handler.get_current_page_number(page),
                    })

                    if status == "PASS":
                        passing_baselines.append({
                            "combo_label":    combo_label,
                            "values":         combo_values,
                            "active_fields":  active_fields,
                            "or_groups":      or_groups,
                        })

                if passing_baselines:
                    print(
                        f"[Baseline] {len(passing_baselines)} passing baseline(s) found. "
                        f"Starting variation tests."
                    )
                    break

            except Exception as exc:
                print(f"[Iteration {iteration + 1}] Error: {exc}")
                results.append({
                    "test_name":          f"BASELINE_ITER_{iteration + 1}",
                    "changed_field":      None,
                    "changed_value":      None,
                    "variation_type":     "baseline",
                    "all_values":         {},
                    "active_field_labels":[],
                    "validation_messages":{},
                    "page_errors":        [str(exc)],
                    "status":             "ERROR",
                    "pass_reason":        str(exc),
                    "url":                "",
                    "page_number":        1,
                })
                break

        if not fields:
            print("[TestRunner] No fields detected — cannot run variation tests.")
            return results

        # Use the first passing baseline for variations
        # (if none passed, use whatever values we last generated)
        if passing_baselines:
            primary = passing_baselines[0]
        else:
            primary = {
                "values":        {},
                "active_fields": fields,
                "or_groups":     [],
            }

        # ----------------------------------------------------------------
        # PHASE 2: MULTI-VARIATION TESTING PER FIELD
        # For each field in the passing baseline:
        #   - Keep all other fields at baseline values
        #   - Replace only this field with an invalid value
        #   - Submit and record result
        # ----------------------------------------------------------------
        test_fields  = primary["active_fields"]
        base_values  = primary["values"]

        for field in test_fields:
            if field.get("skip"):
                continue

            field_idx   = str(field["index"])
            field_label = field.get("label", f"field_{field_idx}")

            # Get all invalid variation types for this field from AI
            variations = ai.generate_field_invalid_variations(
                field, base_values.get(field_idx)
            )

            print(f"\n[Field '{field_label}'] "
                  f"Testing {len(variations)} invalid variations:")
            for v in variations:
                print(f"  • {v['variation_name']}: {repr(v['value'])}")

            for variation in variations:
                variation_name = variation.get("variation_name", "invalid")
                invalid_value  = variation.get("value", "")
                test_values: dict = {}

                try:
                    # All other fields stay at baseline, only this one changes
                    test_values              = base_values.copy()
                    test_values[field_idx]   = invalid_value

                    page.goto(url, wait_until="domcontentloaded")
                    page.wait_for_timeout(5000)
                    try:
                        page.wait_for_selector(
                            "input, select, textarea", timeout=10000
                        )
                    except Exception:
                        pass

                    validation_msgs = filler.fill_all(
                        page, test_fields, test_values
                    )
                    _ = handler.click_submit_or_next(page)
                    page.wait_for_timeout(2000)

                    errors  = handler.detect_errors(page)
                    success = handler.detect_success(page)
                    status, reason = self._determine_status(
                        validation_msgs, errors, success
                    )

                    print(f"  [{variation_name}] → {status} — {reason}")

                    results.append({
                        "test_name":           f"FIELD_{field['index']}_{variation_name.upper()}",
                        "changed_field":       field_label,
                        "changed_value":       invalid_value,
                        "variation_type":      variation_name,
                        "all_values":          test_values,
                        "active_field_labels": [f["label"] for f in test_fields],
                        "validation_messages": validation_msgs,
                        "page_errors":         errors,
                        "status":              status,
                        "pass_reason":         reason,
                        "url":                 page.url,
                        "page_number":         handler.get_current_page_number(page),
                    })

                except Exception as exc:
                    print(f"  [{variation_name}] ERROR: {exc}")
                    results.append({
                        "test_name":           f"FIELD_{field['index']}_{variation_name.upper()}",
                        "changed_field":       field_label,
                        "changed_value":       invalid_value if invalid_value else None,
                        "variation_type":      variation_name,
                        "all_values":          test_values,
                        "active_field_labels": [],
                        "validation_messages": {},
                        "page_errors":         [str(exc)],
                        "status":              "ERROR",
                        "pass_reason":         str(exc),
                        "url":                 "",
                        "page_number":         1,
                    })
                    continue

        return results

    # ------------------------------------------------------------------
    # OR Combination Generator
    # Takes OR groups and generates all pair combinations.
    #
    # Example:
    #   OR Group 0: before=[0], after=[1]  (Mobile OR Loan Account)
    #   OR Group 1: before=[2], after=[3]  (DOB OR PAN)
    #
    #   Combinations:
    #   (0, 2) → Mobile + DOB
    #   (0, 3) → Mobile + PAN
    #   (1, 2) → Loan Account + DOB
    #   (1, 3) → Loan Account + PAN
    # ------------------------------------------------------------------

    def _generate_or_combinations(
        self,
        fields: list[dict],
        or_groups: list[dict],
        baseline_values: dict,
    ) -> list[dict]:
        """
        Generate all valid field value combinations from OR groups.
        Returns a list of value dicts, each representing one combination
        where only one option from each OR group is filled.
        """
        # For each OR group, collect the options (before and after indices)
        group_options = []
        for group in or_groups:
            options = []
            if group["before_indices"]:
                options.append(("before", group["before_indices"]))
            if group["after_indices"]:
                options.append(("after", group["after_indices"]))
            group_options.append(options)

        # Get all field indices that are NOT in any OR group
        # (these are always included in every combination)
        or_field_indices = set()
        for group in or_groups:
            or_field_indices.update(group["before_indices"])
            or_field_indices.update(group["after_indices"])

        non_or_fields = [
            f for f in fields
            if f["index"] not in or_field_indices
        ]

        # Generate all combinations using itertools.product
        # e.g. for 2 groups with 2 options each: 4 combinations
        combinations = []
        for combo in product(*group_options):
            # combo is e.g. (("before", [0]), ("after", [3]))
            # meaning: use field 0 from group 0, field 3 from group 1

            combo_values: dict = {}

            # Add non-OR fields (always present)
            for field in non_or_fields:
                idx = str(field["index"])
                combo_values[idx] = baseline_values.get(idx, "")

            # Add the chosen OR field from each group
            chosen_indices = set()
            for _side, indices in combo:
                for idx in indices:
                    chosen_indices.add(idx)
                    combo_values[str(idx)] = baseline_values.get(str(idx), "")

            # Explicitly leave the unchosen OR fields empty
            for idx in or_field_indices:
                if idx not in chosen_indices:
                    combo_values[str(idx)] = ""

            combinations.append(combo_values)

        return combinations if combinations else [baseline_values]

    def _get_active_fields(
        self, all_fields: list[dict], values: dict
    ) -> list[dict]:
        """
        Return only the fields that have non-empty values in this combination.
        Used to skip filling OR fields that aren't part of the current combo.
        """
        active = []
        for field in all_fields:
            idx = str(field["index"])
            val = values.get(idx)
            # Include if value is non-empty (not None, not "")
            if val is not None and str(val).strip() != "":
                active.append(field)
            elif field.get("type") in {"checkbox", "radio"}:
                # Always include checkboxes and radios
                active.append(field)
        return active

    @staticmethod
    def _determine_status(
        validation_msgs: dict,
        page_errors: list[str],
        success: bool,
    ) -> tuple[str, str]:
        """
        Determine PASS or FAIL based on client-side signals only.

        Priority:
        1. Server confirmed success (thank you page, URL change) → PASS
        2. FILL_VERIFICATION_FAILED in any field → FAIL
           (means the invalid value was silently rejected, nothing entered)
        3. Browser HTML5 validationMessage on any field → FAIL
        4. Visible error elements (aria-invalid, .error classes) → FAIL
        5. No errors at all → PASS (values accepted client-side)
        """
        if success:
            return "PASS", "Server confirmed successful submission"

        # Check for our custom fill verification failures
        fill_failures = {
            k: v for k, v in validation_msgs.items()
            if "FILL_VERIFICATION_FAILED" in str(v)
        }
        if fill_failures:
            return (
                "FAIL",
                f"Fill verification failed — field appears empty after fill: "
                f"fields {list(fill_failures.keys())}",
            )

        # Check browser HTML5 validation messages
        browser_errors = {
            k: v for k, v in validation_msgs.items()
            if v and str(v).strip() and "FILL_VERIFICATION_FAILED" not in str(v)
        }
        if browser_errors:
            return (
                "FAIL",
                f"Browser validation failed on fields: {', '.join(browser_errors.keys())}",
            )

        # Check visible error elements
        if page_errors:
            return "FAIL", f"Visible error elements: {page_errors[0][:80]}"

        return "PASS", "Values accepted client-side (no browser or element errors)"