"""Form filling utilities for smart_form_tester.

What this file does:
  - Fills every field type that Playwright can interact with
  - Handles special cases: React Select autocomplete (DemoQA subjects),
    date pickers that open calendars, range sliders
  - NEW: After filling each field, reads back the actual value to
    verify it was accepted. If nothing was entered (e.g. special chars
    silently rejected), logs a WARNING and marks the field as suspect.
  - NEW: Smarter autocomplete handling — presses Enter to select
    focused option instead of trying to click a moving dropdown
"""

from __future__ import annotations
from playwright.sync_api import Page


class FormFiller:

    def fill_all(self, page: Page, fields: list[dict], values: dict) -> dict:
        """
        Fill all fields with given values.
        Returns dict: {"field_index": "validationMessage or WARNING or empty"}
        """
        validation_messages: dict[str, str] = {}

        for field in fields:
            field_idx  = str(field.get("index"))
            field_type = str(field.get("type", "")).lower()
            label      = str(field.get("label", ""))
            selector   = str(field.get("selector", "")).strip()
            value      = values.get(field_idx)

            # --- Skip file inputs entirely ---
            if field_type == "file" or field.get("skip"):
                print(f"[FormFiller] Skipping file input: {label}")
                validation_messages[field_idx] = ""
                continue

            if not selector:
                print(f"[FormFiller] Missing selector for field {field_idx}")
                validation_messages[field_idx] = ""
                continue

            try:
                # Dismiss any open overlay before touching the next field
                self._dismiss_overlays(page)

                page.wait_for_selector(selector, state="visible", timeout=5000)

                # --------------------------------------------------------
                # Fill based on field type
                # --------------------------------------------------------

                if field_type in {"text", "email", "tel", "number", "password", "textarea"}:
                    locator = page.locator(selector)
                    locator.fill("" if value is None else str(value))
                    page.wait_for_timeout(600)
                    # If a dropdown opened (autocomplete), select the first option
                    self._close_autocomplete(page)

                elif field_type in {"date", "datetime-local", "time", "month"}:
                    # Use JS setter to bypass the browser date picker UI
                    # The native date picker cannot be reliably controlled by Playwright
                    page.evaluate(
                        """([sel, val]) => {
                          const el = document.querySelector(sel);
                          if (!el) return;
                          const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value').set;
                          setter.call(el, val);
                          el.dispatchEvent(new Event('input',  {bubbles: true}));
                          el.dispatchEvent(new Event('change', {bubbles: true}));
                        }""",
                        [selector, "" if value is None else str(value)],
                    )
                    page.wait_for_timeout(400)
                    # Close the calendar popup that some date pickers show
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)

                elif field_type == "select":
                    page.locator(selector).select_option(
                        "" if value is None else str(value)
                    )

                elif field_type == "select-multiple":
                    option_values = value if isinstance(value, list) else [str(value)]
                    page.locator(selector).select_option(
                        [str(v) for v in option_values]
                    )

                elif field_type == "radio":
                    # Click the specific radio that matches the value
                    # Using JS click to avoid overlay interception
                    name = str(field.get("name", "")).strip()
                    if name:
                        page.evaluate(
                            """([name, val]) => {
                              const radios = document.querySelectorAll(
                                `input[type=radio][name="${name}"]`);
                              for (const r of radios) {
                                if (r.value === val) { r.click(); break; }
                              }
                            }""",
                            [name, "" if value is None else str(value)],
                        )
                    else:
                        page.locator(selector).click()

                elif field_type == "checkbox":
                    # Use JS click — avoids overlay interception issues
                    if isinstance(value, str):
                        should_check = value.strip().lower() in {"true", "1", "yes"}
                    else:
                        should_check = bool(value)
                    current = page.locator(selector).is_checked()
                    if should_check != current:
                        page.evaluate(
                            "(sel) => { const el = document.querySelector(sel); if(el) el.click(); }",
                            selector,
                        )

                elif field_type == "checkbox-group":
                    name = str(field.get("name", "")).strip()
                    options = field.get("options") or []
                    value_list = value if isinstance(value, list) else [value]
                    selected_values = {str(v) for v in value_list if v is not None}
                    if name:
                        for option in options:
                            if not isinstance(option, (list, tuple)) or not option:
                                continue
                            opt_value = str(option[0])
                            page.evaluate(
                                """([name, val, shouldCheck]) => {
                                  const cb = document.querySelector(
                                    `input[type=checkbox][name="${name}"][value="${val}"]`);
                                  if (cb && cb.checked !== shouldCheck) cb.click();
                                }""",
                                [name, opt_value, opt_value in selected_values],
                            )

                elif field_type in {"range", "slider"}:
                    # Sliders must be set via JS — Playwright drag is unreliable
                    page.evaluate(
                        """([sel, val]) => {
                          const el = document.querySelector(sel);
                          if (!el) return;
                          const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value').set;
                          setter.call(el, String(val));
                          el.dispatchEvent(new Event('input',  {bubbles: true}));
                          el.dispatchEvent(new Event('change', {bubbles: true}));
                        }""",
                        [selector, "" if value is None else str(value)],
                    )

                elif field_type == "contenteditable":
                    locator = page.locator(selector)
                    locator.click()
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Delete")
                    locator.type("" if value is None else str(value), delay=50)
                    page.wait_for_timeout(600)
                    self._close_autocomplete(page)

                else:
                    page.locator(selector).fill("" if value is None else str(value))

                page.wait_for_timeout(500)

                # --------------------------------------------------------
                # Read back what was actually entered (value verification)
                # This catches cases where special chars are silently rejected
                # e.g. field ignores "@#$" so nothing gets typed
                # --------------------------------------------------------
                native_validation = page.evaluate(
                    "(sel) => { const el = document.querySelector(sel); "
                    "return el ? el.validationMessage || '' : ''; }",
                    selector,
                )
                validation_messages[field_idx] = str(native_validation or "")

                # Only verify read-back for text-type fields
                if field_type in {
                    "text", "email", "tel", "number", "password",
                    "textarea", "date", "datetime-local", "time", "month"
                }:
                    self._verify_fill(
                        page, field_idx, label, selector, value, validation_messages
                    )

            except Exception as exc:
                print(f"[FormFiller] Error filling field {field_idx} ({label}): {exc}")
                validation_messages[field_idx] = ""
                # Try to clean up before next field
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
                except Exception:
                    pass
                continue

        return validation_messages

    # ------------------------------------------------------------------
    # Value Verification
    # Reads back what was actually entered in the field and compares
    # to what we tried to set. Flags if nothing was entered.
    # ------------------------------------------------------------------

    def _verify_fill(
        self,
        page: Page,
        field_idx: str,
        label: str,
        selector: str,
        intended_value: object,
        validation_messages: dict,
    ) -> None:
        """
        After filling a field, read back the actual value.
        If the field is empty when we intended to fill it,
        mark it with a WARNING in validation_messages.

        This catches the case where:
        - Special chars like @#$ are silently ignored by the input
        - The field has a maxlength that cut off the value
        - A JS event handler cleared the field
        """
        try:
            intended = "" if intended_value is None else str(intended_value)
            if not intended:
                return  # We intended empty — no verification needed

            actual = page.evaluate(
                "(sel) => { const el = document.querySelector(sel); "
                "return el ? (el.value || '') : ''; }",
                selector,
            )
            actual = str(actual or "")

            if len(actual.strip()) == 0 and len(intended.strip()) > 0:
                # Nothing was entered at all — this is the critical case
                print(
                    f"[FormFiller] ⚠ WARNING Field {field_idx} ({label}): "
                    f"Nothing was entered. Intended: {repr(intended)}"
                )
                # Only overwrite if no native validation message already set
                if not validation_messages.get(field_idx):
                    validation_messages[field_idx] = "FILL_VERIFICATION_FAILED: field appears empty after fill"

            elif len(actual) < len(intended) * 0.4 and len(intended) > 3:
                # Less than 40% of intended value made it in
                print(
                    f"[FormFiller] ⚠ WARNING Field {field_idx} ({label}): "
                    f"Partial entry. Intended {len(intended)} chars, got {len(actual)}: "
                    f"actual={repr(actual[:30])}"
                )

        except Exception:
            pass  # Verification is best-effort — never crash on this

    # ------------------------------------------------------------------
    # Autocomplete / Dropdown handling
    # After typing in a text field, if a dropdown opened (React Select,
    # MUI Autocomplete, custom combobox), select the first option.
    # Strategy: Press Enter (works for all React Select variants),
    # fallback to JS click on the focused option.
    # ------------------------------------------------------------------

    def _close_autocomplete(self, page: Page) -> None:
        """
        If a dropdown/autocomplete appeared after typing, close it by
        selecting the first/focused option.

        For DemoQA subjects field and similar React Select components:
        - After typing, the dropdown shows a focused option
        - Pressing Enter selects it (most reliable approach)
        - Falls back to JS click if Enter doesn't work
        """
        try:
            dropdown_open = page.evaluate("""() => {
                const menuSelectors = [
                    '[class*="auto-complete__menu"]',
                    '[class*="react-select__menu"]',
                    '[class*="subjects-auto-complete__menu"]',
                    '[class*="dropdown-menu"][style*="display: block"]',
                    '[role="listbox"]',
                ];
                return menuSelectors.some(sel => {
                    try {
                        const el = document.querySelector(sel);
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        return style.display !== 'none' && style.visibility !== 'hidden';
                    } catch(_) { return false; }
                });
            }""")

            if not dropdown_open:
                return

            # Strategy 1: Press Enter — selects focused option in React Select
            page.keyboard.press("Enter")
            page.wait_for_timeout(400)

            # Check if dropdown closed after Enter
            still_open = page.evaluate("""() => {
                return !!document.querySelector(
                    '[class*="auto-complete__menu"], [class*="react-select__menu"], ' +
                    '[class*="subjects-auto-complete__menu"]'
                );
            }""")

            if still_open:
                # Strategy 2: JS click on the focused option
                page.evaluate("""() => {
                    const selectors = [
                        '[class*="option--is-focused"]',
                        '[class*="auto-complete__option"]:first-child',
                        '[class*="react-select__option"]:first-child',
                        '[role="option"][aria-selected="true"]',
                        '[role="option"]:first-child',
                    ];
                    for (const sel of selectors) {
                        const opt = document.querySelector(sel);
                        if (opt) { opt.click(); return; }
                    }
                }""")
                page.wait_for_timeout(400)

                # Strategy 3: Escape to close without selecting (graceful fallback)
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)

        except Exception:
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Overlay dismissal
    # Called before each field to close any calendar pickers or
    # dropdowns left open from the previous field.
    # Only fires if an overlay is actually detected — won't accidentally
    # close a dropdown that was just intentionally opened.
    # ------------------------------------------------------------------

    def _dismiss_overlays(self, page: Page) -> None:
        """
        Close any open date pickers or dropdown menus that would
        block clicks on the next field.
        Does NOT dismiss autocomplete dropdowns (those need _close_autocomplete).
        """
        try:
            overlay_present = page.evaluate("""() => {
                const calendarSelectors = [
                    '.react-datepicker-popper',
                    '[class*="datepicker"][class*="open"]',
                    '[class*="calendar"][style*="display: block"]',
                    '[class*="picker"][style*="display: block"]',
                    '.flatpickr-calendar.open',
                ];
                return calendarSelectors.some(sel => {
                    try { return document.querySelector(sel) !== null; }
                    catch(_) { return false; }
                });
            }""")

            if overlay_present:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)

        except Exception:
            pass