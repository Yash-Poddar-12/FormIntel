"""Form filling utilities for FormIntel."""

from __future__ import annotations

import time
import re
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
                try:
                    page.wait_for_selector(selector, state="visible", timeout=3000)
                except Exception:
                    # React Select and similar: input may be hidden; click the container to reveal it.
                    try:
                        page.evaluate("""(sel) => {
                            const el = document.querySelector(sel);
                            if (!el) return false;
                            let parent = el.parentElement;
                            for (let i = 0; i < 5; i++) {
                                if (!parent) break;
                                const cls = parent.className || '';
                                if (cls.includes('control') || cls.includes('container') ||
                                    cls.includes('select') || cls.includes('input-group')) {
                                    parent.click();
                                    return true;
                                }
                                parent = parent.parentElement;
                            }
                            el.click();
                            return true;
                        }""", selector)
                        page.wait_for_timeout(500)
                    except Exception:
                        pass

                is_react_select = page.evaluate("""(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    const cls = (el.className || '') + (el.getAttribute('id') || '');
                    return cls.includes('react-select') ||
                           (el.getAttribute('role') === 'combobox' && el.disabled);
                }""", selector)

                if is_react_select and field_type in {"text", "contenteditable", "react-select"}:
                    self._fill_react_select(page, selector, str(value) if value else "")
                    validation_messages[field_idx] = ""
                    continue

                is_tags_input = page.evaluate("""(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    // Already handled as react-select — skip
                    const cls = (el.className || '') + (el.getAttribute('id') || '');
                    if (cls.includes('react-select')) return false;
                    // Check for tags/multi-select autocomplete patterns
                    const name = (el.getAttribute('name') || el.getAttribute('id') || '').toLowerCase();
                    const placeholder = (el.getAttribute('placeholder') || '').toLowerCase();
                    // Only match explicit subjects/tags/skills/interests field names
                    const isTagsName = name.includes('subjects') || name.includes('tags') || name.includes('skills') || name.includes('interests');
                    // Only match very specific placeholder phrases — NOT generic "add" words
                    // "add" alone is too broad (e.g. "Add mobile number" on Bajaj form)
                    const isTagsPlaceholder = placeholder.includes('type to search') || placeholder.includes('select multiple');
                    // Only match if the element is INSIDE a chip/tag container
                    // (not just a Bootstrap multi-column parent which also uses class "multi")
                    const chipParent = el.closest('[class*="tagsinput"], [class*="chips"], [class*="token"]');
                    return isTagsName || isTagsPlaceholder || chipParent !== null;
                }""", selector)

                if is_tags_input and field_type == "text":
                    self._fill_tags_autocomplete(page, selector, str(value) if value else "")
                    validation_messages[field_idx] = ""
                    continue

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
                    target_value = "" if value is None else str(value)
                    locator = page.locator(selector)
                    try:
                        locator.select_option(target_value)
                    except Exception:
                        # Exact value not in <option> list — try label match, then first non-empty option
                        selected = page.evaluate(
                            """([sel, val]) => {
                                const el = document.querySelector(sel);
                                if (!el) return false;
                                const opts = Array.from(el.options).filter(o => o.value !== '' && !o.disabled);
                                if (!opts.length) return false;
                                // Try case-insensitive label match first
                                const lower = val.toLowerCase();
                                const byLabel = opts.find(o => o.text.toLowerCase().includes(lower) || lower.includes(o.text.toLowerCase()));
                                const pick = byLabel || opts[0];
                                el.value = pick.value;
                                el.dispatchEvent(new Event('change', {bubbles: true}));
                                return true;
                            }""",
                            [selector, target_value],
                        )
                        if not selected:
                            print(f"[FormFiller] select fallback: no options available for {selector}")

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

    def _fill_react_select(self, page: Page, selector: str, value: str) -> None:
        try:
            # Step 1: Click the container to open the dropdown
            page.evaluate("""(sel) => {
                const el = document.querySelector(sel);
                if (!el) return false;
                let parent = el.parentElement;
                let best = null;
                for (let i = 0; i < 8; i++) {
                    if (!parent) break;
                    const cls = parent.className || '';
                    if (cls.includes('react-select__control') || cls.includes('select__control') ||
                        cls.includes('control') || cls.includes('container') || cls.includes('select')) {
                        best = parent;
                    }
                    parent = parent.parentElement;
                }
                (best || el).click();
                return true;
            }""", selector)
            page.wait_for_timeout(500)

            # Step 2: Read all currently visible options BEFORE typing anything
            available_options = page.evaluate("""() => {
                const optionEls = document.querySelectorAll('[role="option"], [class*="react-select__option"], [class*="select__option"]');
                return Array.from(optionEls).map(el => el.textContent.trim()).filter(t => t.length > 0);
            }""")

            if available_options:
                # Step 3: Find the best matching option from what's actually available
                value_lower = value.lower()
                best_match = None
                best_score = -1
                for opt in available_options:
                    opt_lower = opt.lower()
                    if opt_lower == value_lower:
                        best_match = opt
                        best_score = 100
                        break
                    elif value_lower in opt_lower or opt_lower in value_lower:
                        score = 80
                        if score > best_score:
                            best_score = score
                            best_match = opt
                    else:
                        # Word overlap
                        v_words = set(value_lower.split())
                        o_words = set(opt_lower.split())
                        overlap = len(v_words & o_words)
                        if overlap > 0 and overlap > best_score:
                            best_score = overlap
                            best_match = opt

                if best_match:
                    # Click the matching option directly without typing
                    clicked = page.evaluate("""(targetText) => {
                        const optionEls = document.querySelectorAll('[role="option"], [class*="react-select__option"], [class*="select__option"]');
                        for (const el of optionEls) {
                            if (el.textContent.trim().toLowerCase() === targetText.toLowerCase()) {
                                el.click();
                                return true;
                            }
                        }
                        return false;
                    }""", best_match)
                    if clicked:
                        page.wait_for_timeout(400)
                        return

            # Step 4: No pre-loaded options or no match found — type to filter
            if value:
                page.keyboard.type(value[:4], delay=50)
                page.wait_for_timeout(800)

                # Try to click a matching option after filtering
                clicked = page.evaluate("""(targetText) => {
                    const optionEls = document.querySelectorAll('[role="option"], [class*="react-select__option"], [class*="option--is-focused"]');
                    for (const el of optionEls) {
                        const t = el.textContent.trim().toLowerCase();
                        if (t.includes(targetText.toLowerCase().substring(0, 3))) {
                            el.click();
                            return true;
                        }
                    }
                    // Fallback: click first visible option
                    const first = document.querySelector('[role="option"]:first-child, [class*="react-select__option"]:first-child');
                    if (first) { first.click(); return true; }
                    return false;
                }""", value)

                if not clicked:
                    page.keyboard.press("ArrowDown")
                    page.wait_for_timeout(300)
                    page.keyboard.press("Enter")

            page.wait_for_timeout(400)

            # Step 5: Verify something was selected
            displayed = page.evaluate("""(sel) => {
                const el = document.querySelector(sel);
                if (!el) return '';
                let parent = el.parentElement;
                for (let i = 0; i < 8; i++) {
                    if (!parent) break;
                    const valueNode = parent.querySelector('[class*="__single-value"], [class*="__multi-value"]');
                    if (valueNode) return valueNode.textContent || '';
                    parent = parent.parentElement;
                }
                return el.value || '';
            }""", selector)
            if value and not str(displayed).strip():
                print(f"[FormFiller] WARNING: React Select value did not appear selected for {selector}")

        except Exception as exc:
            print(f"[FormFiller] React Select fill failed for {selector}: {exc}")
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            except Exception:
                pass

    def _fill_tags_autocomplete(self, page: Page, selector: str, value_string: str) -> None:
        terms = [term.strip() for term in value_string.split(",") if term.strip()]
        for term in terms:
            try:
                page.locator(selector).click()
                # Type first 3 chars to trigger options
                page.keyboard.type(term[:3], delay=50)
                page.wait_for_timeout(800)

                # Read available options and find best match
                available = page.evaluate("""() => {
                    const opts = document.querySelectorAll('[role="option"], [class*="option"]:not([class*="option--is-disabled"])');
                    return Array.from(opts).map(el => el.textContent.trim()).filter(t => t.length > 0).slice(0, 20);
                }""")

                best_option = None
                if available:
                    term_lower = term.lower()
                    for opt in available:
                        if term_lower in opt.lower() or opt.lower() in term_lower:
                            best_option = opt
                            break
                    if not best_option:
                        best_option = available[0]  # take first if no partial match

                if best_option:
                    clicked = page.evaluate("""(targetText) => {
                        const opts = document.querySelectorAll('[role="option"], [class*="option"]');
                        for (const el of opts) {
                            if (el.textContent.trim().toLowerCase().includes(targetText.toLowerCase())) {
                                el.click();
                                return true;
                            }
                        }
                        return false;
                    }""", best_option[:10])  # use first 10 chars to match
                    if not clicked:
                        page.keyboard.press("ArrowDown")
                        page.wait_for_timeout(200)
                        page.keyboard.press("Enter")
                else:
                    # No options appeared — skip this term
                    page.keyboard.press("Escape")
                    continue

                page.wait_for_timeout(300)
            except Exception as exc:
                print(f"[FormFiller] Tags autocomplete term skipped for '{term}': {exc}")
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
                except Exception:
                    pass    

    def _handle_autocomplete_dropdown(self, page: Page, selector: str, intended_value: str) -> None:
        """
        After typing, if a dropdown appeared FOR THIS SPECIFIC FIELD, click best matching option.
        If no match found, clicks first available option (never leaves blank).
        Falls back to ArrowDown+Enter if JS click fails.

        Crucially: checks that the dropdown is anchored to this field, not some
        other open menu elsewhere on the page (which caused textarea double-fill).
        """
        try:
            dropdown_visible = page.evaluate("""(sel) => {
                const el = document.querySelector(sel);
                if (!el) return false;

                // 1. Field itself says it has an open dropdown
                if (el.getAttribute('aria-expanded') === 'true') return true;

                // 2. Field has aria-controls / aria-owns pointing to a listbox
                const controlled = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
                if (controlled) {
                    const listbox = document.getElementById(controlled);
                    if (listbox && listbox.offsetParent !== null) return true;
                }

                // 3. A dropdown menu is a SIBLING or DESCENDANT of the field's
                //    direct parent container — not just anywhere on the page.
                const container = el.closest(
                    '[class*="auto-complete"], [class*="react-select"], ' +
                    '[class*="subjects-auto-complete"], [class*="combobox"]'
                );
                if (container) {
                    const menu = container.querySelector(
                        '[class*="menu"], [class*="dropdown"], [role="listbox"]'
                    );
                    if (menu && menu.offsetParent !== null) return true;
                }

                return false;
            }""", selector)

            if not dropdown_visible:
                return

            # Try clicking best matching option — fallback to first available if no match
            clicked = page.evaluate("""(targetText) => {
                const optionSelectors = [
                    '[class*="auto-complete__option"]',
                    '[class*="react-select__option"]',
                    '[class*="subjects-auto-complete__option"]',
                    '[id*="react-select"][id*="option"]',
                    '[role="option"]',
                    '[class*="option--is-focused"]',
                ];
                let allOptions = [];
                for (const sel of optionSelectors) {
                    const nodes = Array.from(document.querySelectorAll(sel));
                    if (nodes.length) { allOptions = nodes; break; }
                }
                if (!allOptions.length) return false;

                // Try label match first
                const lower = targetText.toLowerCase();
                const match = allOptions.find(o => {
                    const t = o.textContent.trim().toLowerCase();
                    return t.includes(lower) || lower.includes(t);
                });
                // No match → pick first visible non-disabled option
                const pick = match || allOptions[0];
                pick.click();
                return true;
            }""", intended_value)

            if not clicked:
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