"""Form filling utilities for FormIntel."""

from __future__ import annotations

import time
from playwright.sync_api import Page


class FormFiller:

    def __init__(self, otp_wait_seconds: int = 180, otp_extra_seconds: int = 120):
        self.otp_wait_seconds = otp_wait_seconds
        self.otp_extra_seconds = otp_extra_seconds

    def fill_all(self, page: Page, fields: list[dict], values: dict) -> dict:
        """Fill all fields. Returns validation_messages dict."""
        validation_messages: dict[str, str] = {}

        for field in fields:
            field_idx = str(field.get("index"))
            field_type = str(field.get("type", "")).lower()
            label = str(field.get("label", ""))
            selector = str(field.get("selector", "")).strip()
            value = values.get(field_idx)

            if field_type == "file" or field.get("skip"):
                if field_type != "otp":
                    print(f"[FormFiller] Skipping file input: {label}")
                    validation_messages[field_idx] = ""
                    continue

            if not selector:
                print(f"[FormFiller] Missing selector for field index {field_idx}")
                validation_messages[field_idx] = ""
                continue

            # --- OTP special handling ---
            if field_type == "otp":
                msg = self._wait_for_otp(page, selector, label)
                validation_messages[field_idx] = msg
                continue

            try:
                self._dismiss_overlays(page)
                page.wait_for_selector(selector, state="visible", timeout=5000)

                if field_type in {"text", "email", "tel", "number", "password", "textarea"}:
                    locator = page.locator(selector)
                    locator.fill("" if value is None else str(value))
                    page.wait_for_timeout(800)  # increased from 600
                    self._handle_autocomplete_dropdown(page, selector, str(value) if value else "")

                elif field_type in {"date", "datetime-local", "time", "month"}:
                    page.evaluate(
                        """([sel, val]) => {
                          const el = document.querySelector(sel);
                          if (!el) return;
                          const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value').set;
                          nativeInputValueSetter.call(el, val);
                          el.dispatchEvent(new Event('input', {bubbles: true}));
                          el.dispatchEvent(new Event('change', {bubbles: true}));
                        }""",
                        [selector, "" if value is None else str(value)],
                    )
                    page.wait_for_timeout(400)
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)

                elif field_type == "select":
                    page.locator(selector).select_option("" if value is None else str(value))

                elif field_type == "select-multiple":
                    if isinstance(value, list):
                        option_values = [str(v) for v in value]
                    else:
                        option_values = ["" if value is None else str(value)]
                    page.locator(selector).select_option(option_values)

                elif field_type == "radio":
                    name = str(field.get("name", "")).strip()
                    if name:
                        page.evaluate(
                            """([name, val]) => {
                              const radios = document.querySelectorAll(`input[type=radio][name="${name}"]`);
                              for (const r of radios) { if (r.value === val) { r.click(); break; } }
                            }""",
                            [name, "" if value is None else str(value)],
                        )
                    else:
                        page.locator(selector).click()

                elif field_type == "checkbox":
                    locator = page.locator(selector)
                    current = locator.is_checked()
                    if isinstance(value, str):
                        should_check = value.strip().lower() in {"true", "1", "yes"}
                    else:
                        should_check = bool(value)
                    if should_check != current:
                        page.evaluate(
                            "(sel) => { const el = document.querySelector(sel); if(el) el.click(); }",
                            selector
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
                    page.evaluate(
                        """([sel, val]) => {
                          const el = document.querySelector(sel);
                          if (!el) return;
                          const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value').set;
                          nativeInputValueSetter.call(el, String(val));
                          el.dispatchEvent(new Event('input', {bubbles: true}));
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
                    page.wait_for_timeout(800)
                    self._handle_autocomplete_dropdown(page, selector, str(value) if value else "")

                else:
                    page.locator(selector).fill("" if value is None else str(value))

                page.wait_for_timeout(500)
                validation_message = page.evaluate(
                    "(sel) => { const el = document.querySelector(sel); return el ? el.validationMessage || '' : ''; }",
                    selector,
                )
                validation_messages[field_idx] = str(validation_message or "")

            except Exception as exc:
                print(f"[FormFiller] Error filling field {field_idx} ({label}): {exc}")
                validation_messages[field_idx] = ""
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
                except Exception:
                    pass
                continue

        return validation_messages

    def verify_fills(self, page: Page, fields: list[dict], intended_values: dict) -> dict:
        """
        After fill_all(), read back actual DOM values and compare to intended.
        Returns: {field_idx: {"intended": x, "actual": y, "stuck": bool}}
        """
        verification: dict[str, dict] = {}
        for field in fields:
            field_idx = str(field.get("index"))
            field_type = str(field.get("type", "")).lower()
            selector = str(field.get("selector", "")).strip()
            label = str(field.get("label", ""))
            intended = intended_values.get(field_idx)

            if field_type in {"file", "radio", "checkbox", "checkbox-group", "range", "otp"} or field.get("skip"):
                continue
            if not selector:
                continue

            try:
                actual = page.evaluate(
                    """(sel) => {
                        const el = document.querySelector(sel);
                        if (!el) return null;
                        if (el.tagName === 'SELECT') {
                            return el.value;
                        }
                        if (el.getAttribute('contenteditable')) {
                            return el.textContent || '';
                        }
                        return el.value !== undefined ? el.value : el.textContent || '';
                    }""",
                    selector
                )
                intended_str = "" if intended is None else str(intended)
                actual_str = "" if actual is None else str(actual)
                stuck = (intended_str == "" or actual_str.strip() != "")

                if intended_str and not actual_str.strip():
                    print(f"[FormFiller] WARNING: Field '{label}' (idx {field_idx}) value did not stick — "
                          f"intended '{intended_str[:40]}', actual '{actual_str[:40]}'")
                    stuck = False
                else:
                    stuck = True

                verification[field_idx] = {
                    "intended": intended_str,
                    "actual": actual_str,
                    "stuck": stuck,
                }
            except Exception:
                pass

        return verification

    def _wait_for_otp(self, page: Page, selector: str, label: str) -> str:
        """Wait for user to manually enter OTP. Polls until filled or timeout."""
        print(f"\n{'='*60}")
        print(f"[OTP] Waiting for manual OTP entry on field '{label}'.")
        print(f"[OTP] You have {self.otp_wait_seconds // 60} minutes to enter the OTP.")
        print(f"{'='*60}\n")

        deadline = time.time() + self.otp_wait_seconds
        while time.time() < deadline:
            try:
                val = page.evaluate(
                    "(sel) => { const el = document.querySelector(sel); return el ? el.value : ''; }",
                    selector
                )
                if val and str(val).strip():
                    print(f"[OTP] OTP detected in field '{label}': {'*' * len(str(val))}")
                    return ""
            except Exception:
                pass
            time.sleep(5)

        print(f"[OTP] Still waiting... {self.otp_extra_seconds // 60} more minute(s) remaining.")
        extra_deadline = time.time() + self.otp_extra_seconds
        while time.time() < extra_deadline:
            try:
                val = page.evaluate(
                    "(sel) => { const el = document.querySelector(sel); return el ? el.value : ''; }",
                    selector
                )
                if val and str(val).strip():
                    print(f"[OTP] OTP detected in field '{label}'.")
                    return ""
            except Exception:
                pass
            time.sleep(5)

        print(f"[OTP] Timed out waiting for OTP on field '{label}' — skipping.")
        return "OTP_TIMEOUT"

    def _dismiss_overlays(self, page: Page) -> None:
        try:
            overlay_open = page.evaluate("""() => {
                const openSelectors = [
                    '[class*="dropdown"][style*="display: block"]',
                    '[class*="menu"][style*="display: block"]',
                    '.react-datepicker-popper',
                    '[class*="datepicker"]:not([style*="display: none"])',
                    '[aria-expanded="true"]',
                    '[class*="subjects-auto-complete__menu"]',
                    '[class*="react-select__menu"]',
                    '[class*="auto-complete"][class*="menu"]',
                ];
                return openSelectors.some(sel => {
                    try { return document.querySelector(sel) !== null; }
                    catch(_) { return false; }
                });
            }""")
            if overlay_open:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
        except Exception:
            pass

    def _handle_autocomplete_dropdown(self, page: Page, selector: str, intended_value: str) -> None:
        """
        After typing, if a dropdown appeared, click first matching option.
        Falls back to ArrowDown+Enter if click fails.
        Then verifies the value actually stuck.
        """
        try:
            dropdown_visible = page.evaluate("""(sel) => {
                const el = document.querySelector(sel);
                if (!el) return false;
                if (el.getAttribute('aria-expanded') === 'true') return true;
                const menu = document.querySelector(
                    '[class*="auto-complete__menu"], [class*="react-select__menu"], ' +
                    '[class*="subjects-auto-complete__menu"]'
                );
                return menu !== null;
            }""", selector)

            if not dropdown_visible:
                return

            # Try clicking first option
            clicked = page.evaluate("""() => {
                const optionSelectors = [
                    '[class*="auto-complete__option"]:first-child',
                    '[class*="react-select__option"]:first-child',
                    '[class*="subjects-auto-complete__option"]:first-child',
                    '[id*="react-select"][id*="option-0"]',
                    '[role="option"]:first-child',
                    '[class*="option--is-focused"]',
                ];
                for (const sel of optionSelectors) {
                    const opt = document.querySelector(sel);
                    if (opt) { opt.click(); return true; }
                }
                return false;
            }""")

            if not clicked:
                # Fallback: ArrowDown + Enter
                page.keyboard.press("ArrowDown")
                page.wait_for_timeout(300)
                page.keyboard.press("Enter")

            page.wait_for_timeout(500)

            # Verify value stuck after dropdown selection
            actual = page.evaluate(
                "(sel) => { const el = document.querySelector(sel); return el ? (el.value || el.textContent || '') : ''; }",
                selector
            )
            if not str(actual).strip() and intended_value:
                print(f"[FormFiller] Autocomplete value did not stick after dropdown select. "
                      f"Trying ArrowDown+Enter fallback.")
                page.locator(selector).click()
                page.wait_for_timeout(300)
                page.keyboard.press("ArrowDown")
                page.wait_for_timeout(400)
                page.keyboard.press("Enter")
                page.wait_for_timeout(500)

        except Exception:
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            except Exception:
                pass