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
                    const cls = (el.className || '') + (el.getAttribute('id') || '');
                    if (cls.includes('react-select')) return false;
                    const name = (el.getAttribute('name') || el.getAttribute('id') || '').toLowerCase();
                    const placeholder = (el.getAttribute('placeholder') || '').toLowerCase();
                    const isTagsName = name.includes('subjects') || name.includes('tags') || name.includes('skills') || name.includes('interests');
                    const isTagsPlaceholder = placeholder.includes('type to search') || placeholder.includes('select multiple');
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
                    page.wait_for_timeout(800)
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
                        selected = page.evaluate(
                            """([sel, val]) => {
                                const el = document.querySelector(sel);
                                if (!el) return false;
                                const opts = Array.from(el.options).filter(o => o.value !== '' && !o.disabled);
                                if (!opts.length) return false;
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

        # NOTE: The post-fill rescan for newly visible dependent fields (e.g. City
        # after State) is now handled by the fill-until-stable loop in
        # test_runner._traverse_multi_page_form, which calls AI to generate proper
        # values for any newly appeared field. The old _fill_newly_visible_react_selects
        # method has been removed because it matched by index keys ("0","1") instead
        # of field labels, causing it to fill the City dropdown with "Arjun".

        return validation_messages

    def verify_fills(self, page: Page, fields: list[dict], intended_values: dict) -> dict:
        """
        After fill_all(), read back actual DOM values and compare to intended.
        Returns: {field_idx: {"intended": x, "actual": y, "stuck": bool}}

        stuck=False means the value did not appear in the DOM — either the form
        silently rejected it (e.g. Angular validation) or the fill tool failed.
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

            # React-select inputs always have .value === "" — the selected value lives
            # in the __single-value span, not the input. Skip them in verification to
            # avoid false "value did not stick" warnings.
            is_react = page.evaluate("""(sel) => {
                const el = document.querySelector(sel);
                if (!el) return false;
                const cls = (el.className || '') + (el.getAttribute('id') || '');
                return cls.includes('react-select') || el.getAttribute('role') === 'combobox';
            }""", selector)
            if is_react:
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

                # FIX: was a confusing double-assignment that only caught empty actual.
                # Now also detects significant truncation (e.g. BHFL 3-char bug).
                if intended_str and not actual_str.strip():
                    # Value completely absent — form silently rejected or fill failed
                    print(
                        f"[FormFiller] WARNING: Field '{label}' (idx {field_idx}) value did not stick — "
                        f"intended '{intended_str[:40]}', actual '{actual_str[:40]}'"
                    )
                    stuck = False
                elif (
                    intended_str
                    and actual_str.strip()
                    and len(actual_str.strip()) < max(2, len(intended_str) // 2)
                ):
                    # Significant truncation detected (e.g. "987654321" truncated to "987")
                    print(
                        f"[FormFiller] WARNING: Field '{label}' (idx {field_idx}) value truncated — "
                        f"intended '{intended_str[:40]}' ({len(intended_str)} chars), "
                        f"actual '{actual_str.strip()[:40]}' ({len(actual_str.strip())} chars)"
                    )
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
        """
        Close any open dropdown/datepicker/autocomplete before filling the next field.
        Presses Escape until no open overlay remains (or max 3 attempts).
        """
        for _ in range(3):
            try:
                overlay_open = page.evaluate("""() => {
                    const openSelectors = [
                        '[class*="dropdown"][style*="display: block"]',
                        '[class*="menu"][style*="display: block"]',
                        '.react-datepicker-popper',
                        '[class*="datepicker"]:not([style*="display: none"])',
                        '[class*="subjects-auto-complete__menu"]',
                        '[class*="react-select__menu"]',
                        '[class*="auto-complete"][class*="menu"]',
                        '[role="listbox"]',
                        '[role="option"]',
                    ];
                    return openSelectors.some(sel => {
                        try {
                            const el = document.querySelector(sel);
                            return el !== null && el.offsetParent !== null;
                        }
                        catch(_) { return false; }
                    });
                }""")
                if not overlay_open:
                    break
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            except Exception:
                break

    def _fill_react_select(self, page: Page, selector: str, value: str) -> None:
        """
        Fill a React Select dropdown. Three-strategy approach:

        Strategy A — Full list match (dropdowns that show all options on open):
            Click control → read all options → click best match or first.

        Strategy B — Typed search (search-only dropdowns like DemoQA State/City):
            Type first few chars → read filtered options → click match or first.
            If still "No options": clear text, reopen, pick first available.

        Strategy C — ArrowDown+Enter last resort.

        Keyboard focus is always established via a real Playwright .click() BEFORE
        any page.keyboard.type() call, so keystrokes go to the react-select input
        and NOT to whatever field previously had focus (e.g. the address textarea).
        """
        def _read_options() -> list[str]:
            return page.evaluate("""() => {
                const sels = [
                    '[role="option"]',
                    '[class*="react-select__option"]',
                    '[class*="select__option"]',
                ];
                for (const s of sels) {
                    const nodes = Array.from(document.querySelectorAll(s));
                    const texts = nodes.map(n => n.textContent.trim()).filter(t => t && t !== 'No options');
                    if (texts.length) return texts;
                }
                return [];
            }""")

        def _click_option(target: str) -> bool:
            """Click the option whose text best matches target. Returns True on success."""
            return page.evaluate("""(target) => {
                const sels = [
                    '[role="option"]',
                    '[class*="react-select__option"]',
                    '[class*="select__option"]',
                ];
                let all = [];
                for (const s of sels) {
                    const nodes = Array.from(document.querySelectorAll(s));
                    if (nodes.length) { all = nodes; break; }
                }
                if (!all.length) return false;
                const tl = target.toLowerCase();
                const exact  = all.find(o => o.textContent.trim().toLowerCase() === tl);
                const substr = all.find(o => o.textContent.trim().toLowerCase().includes(tl) ||
                                            tl.includes(o.textContent.trim().toLowerCase()));
                const pick = exact || substr || all[0];
                pick.click();
                return true;
            }""", target)

        def _best_match(options: list[str], target: str) -> str | None:
            tl = target.lower()
            for o in options:
                if o.lower() == tl:
                    return o
            for o in options:
                ol = o.lower()
                if tl in ol or ol in tl:
                    return o
            tw = set(tl.split())
            best, best_n = None, 0
            for o in options:
                n = len(tw & set(o.lower().split()))
                if n > best_n:
                    best_n, best = n, o
            return best

        try:
            try:
                clicked_container = page.evaluate("""(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    let p = el.parentElement;
                    for (let i = 0; i < 8; i++) {
                        if (!p) break;
                        const cls = p.className || '';
                        if (cls.includes('__control') || cls.includes('__container')) {
                            p.click();
                            return true;
                        }
                        p = p.parentElement;
                    }
                    return false;
                }""", selector)
            except Exception:
                clicked_container = False

            try:
                page.locator(selector).click(timeout=2000)
            except Exception:
                pass
            page.wait_for_timeout(600)

            # ── Strategy A: full list already visible ────────────────────────────
            options_a = _read_options()
            if options_a:
                match = _best_match(options_a, value)
                target = match if match else options_a[0]
                if _click_option(target):
                    page.wait_for_timeout(400)
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
                    if not match:
                        print(f"[FormFiller] ReactSelect: '{value}' not in options, picked first: '{target}'")
                    return

            # ── Strategy B: type to search ───────────────────────────────────────
            if value:
                search_term = value[:4]
                page.keyboard.type(search_term, delay=50)
                page.wait_for_timeout(800)

                options_b = _read_options()
                if options_b:
                    match = _best_match(options_b, value)
                    target = match if match else options_b[0]
                    if _click_option(target):
                        page.wait_for_timeout(400)
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(300)
                        if not match:
                            print(f"[FormFiller] ReactSelect: '{value}' not found after typing '{search_term}', picked first: '{target}'")
                        return

                # ── Strategy B2: typed text gave "No options" — clear and pick first ──
                print(f"[FormFiller] ReactSelect: no options after typing '{search_term}' for '{value}' — clearing and picking first available")
                for _ in range(len(search_term) + 1):
                    page.keyboard.press("Backspace")
                page.wait_for_timeout(400)

                reopened = page.evaluate("""(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    let p = el.parentElement;
                    for (let i = 0; i < 10; i++) {
                        if (!p) break;
                        const indicator = p.querySelector(
                            '[class*="__dropdown-indicator"], [class*="__indicator"]'
                        );
                        if (indicator) { indicator.click(); return true; }
                        p = p.parentElement;
                    }
                    return false;
                }""", selector)
                page.wait_for_timeout(600)

                options_c = _read_options()
                if options_c:
                    if _click_option(options_c[0]):
                        page.wait_for_timeout(400)
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(300)
                        print(f"[FormFiller] ReactSelect: picked first available option '{options_c[0]}'")
                        return

            # ── Strategy C: ArrowDown + Enter last resort ────────────────────────
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(300)
            page.keyboard.press("Enter")
            page.wait_for_timeout(400)
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)

            # ── Verify ───────────────────────────────────────────────────────────
            displayed = page.evaluate("""(sel) => {
                const el = document.querySelector(sel);
                if (!el) return '';
                let p = el.parentElement;
                for (let i = 0; i < 8; i++) {
                    if (!p) break;
                    const v = p.querySelector('[class*="__single-value"], [class*="__multi-value"]');
                    if (v) return v.textContent || '';
                    p = p.parentElement;
                }
                return el.value || '';
            }""", selector)
            if value and not str(displayed).strip():
                print(f"[FormFiller] WARNING: ReactSelect — nothing selected for '{selector}' (wanted '{value}')")

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
                page.keyboard.type(term[:3], delay=50)
                page.wait_for_timeout(800)

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
                        best_option = available[0]

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
                    }""", best_option[:10])
                    if not clicked:
                        page.keyboard.press("ArrowDown")
                        page.wait_for_timeout(200)
                        page.keyboard.press("Enter")
                else:
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
        Scoped to the field's own container to avoid firing on unrelated open menus elsewhere.
        """
        try:
            dropdown_visible = page.evaluate("""(sel) => {
                const el = document.querySelector(sel);
                if (!el) return false;

                if (el.getAttribute('aria-expanded') === 'true') return true;

                const controlled = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
                if (controlled) {
                    const listbox = document.getElementById(controlled);
                    if (listbox && listbox.offsetParent !== null) return true;
                }

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

                const lower = targetText.toLowerCase();
                const match = allOptions.find(o => {
                    const t = o.textContent.trim().toLowerCase();
                    return t.includes(lower) || lower.includes(t);
                });
                const pick = match || allOptions[0];
                pick.click();
                return true;
            }""", intended_value)

            if not clicked:
                page.keyboard.press("ArrowDown")
                page.wait_for_timeout(300)
                page.keyboard.press("Enter")

            page.wait_for_timeout(500)

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