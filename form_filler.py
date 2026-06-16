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

        # ── Post-fill rescan: pick up dependent dropdowns that became visible ──
        # Example: DemoQA City dropdown only appears after State is selected.
        # These were invisible during the initial detect() so they weren't in `fields`.
        self._fill_newly_visible_react_selects(page, fields, values)

        return validation_messages

    def _fill_newly_visible_react_selects(
        self, page: Page, already_filled_fields: list[dict], values: dict
    ) -> None:
        """
        After filling all known fields, scan for react-select inputs that are NOW
        visible but were not in the original field list (dependent dropdowns like
        DemoQA's City which only appears after State is selected).

        Matches each new input to a value by label similarity against the values
        dict keys, then fills it using _fill_react_select.
        """
        page.wait_for_timeout(600)

        already_selectors = {str(f.get("selector", "")) for f in already_filled_fields}

        # Find all visible react-select inputs on the page not already filled
        new_selects = page.evaluate("""(alreadySelectors) => {
            const results = [];
            const inputs = Array.from(document.querySelectorAll('input[role="combobox"]'));
            for (const el of inputs) {
                const cls = (el.className || '') + (el.getAttribute('id') || '');
                if (!cls.includes('react-select') && !cls.includes('select')) continue;

                // Build selector: prefer id
                let sel = '';
                if (el.id) sel = '#' + el.id;
                else if (el.getAttribute('name')) sel = '[name="' + el.getAttribute('name') + '"]';
                else continue;

                if (alreadySelectors.includes(sel)) continue;

                // Must be visible
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') continue;

                // Read label from aria-label or placeholder
                const label = (el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').toLowerCase();

                // Check if already has a value selected (single-value span present)
                let parent = el.parentElement;
                let hasValue = false;
                for (let i = 0; i < 8; i++) {
                    if (!parent) break;
                    const sv = parent.querySelector('[class*="__single-value"]');
                    if (sv && sv.textContent.trim()) { hasValue = true; break; }
                    parent = parent.parentElement;
                }
                if (hasValue) continue;

                results.push({ selector: sel, label: label });
            }
            return results;
        }""", list(already_selectors))

        if not new_selects:
            return

        print(f"[FormFiller] Post-fill rescan found {len(new_selects)} newly visible react-select(s)")

        for item in new_selects:
            sel = item.get("selector", "")
            label_hint = item.get("label", "")
            if not sel:
                continue

            # Find the best matching value from the values dict by label similarity
            best_value = ""
            best_score = -1
            for key, val in values.items():
                if val is None:
                    continue
                key_lower = str(key).lower().replace("_", " ")
                label_lower = label_hint.lower()
                score = 0
                if key_lower == label_lower:
                    score = 100
                elif key_lower in label_lower or label_lower in key_lower:
                    score = 80
                else:
                    kw = set(key_lower.split())
                    lw = set(label_lower.split())
                    overlap = len(kw & lw)
                    if overlap:
                        score = 50 + overlap
                if score > best_score:
                    best_score = score
                    best_value = str(val)

            print(f"[FormFiller] Filling newly visible react-select '{sel}' (label='{label_hint}') with '{best_value}'")
            try:
                self._fill_react_select(page, sel, best_value)
            except Exception as exc:
                print(f"[FormFiller] Post-fill react-select failed for {sel}: {exc}")

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
                // Exact → substring → first
                const exact  = all.find(o => o.textContent.trim().toLowerCase() === tl);
                const substr = all.find(o => o.textContent.trim().toLowerCase().includes(tl) ||
                                            tl.includes(o.textContent.trim().toLowerCase()));
                const pick = exact || substr || all[0];
                pick.click();
                return true;
            }""", target)

        def _best_match(options: list[str], target: str) -> str | None:
            tl = target.lower()
            # exact
            for o in options:
                if o.lower() == tl:
                    return o
            # substring
            for o in options:
                ol = o.lower()
                if tl in ol or ol in tl:
                    return o
            # word overlap
            tw = set(tl.split())
            best, best_n = None, 0
            for o in options:
                n = len(tw & set(o.lower().split()))
                if n > best_n:
                    best_n, best = n, o
            return best  # None if no overlap at all

        try:
            # ── Step 1: open the dropdown with a REAL Playwright click ──────────
            # JS .click() opens the menu but does NOT transfer real keyboard focus.
            # The real .click() via Playwright does both.
            try:
                # Try clicking the control/container (the visual dropdown button)
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

            # Real Playwright click to transfer keyboard focus
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
            # Keyboard focus is on the input (from real .click() above).
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

                # ── Strategy B2: typed text gave "No options" —
                #    clear the text, reopen the dropdown, pick first available ────
                print(f"[FormFiller] ReactSelect: no options after typing '{search_term}' for '{value}' — clearing and picking first available")
                for _ in range(len(search_term) + 1):
                    page.keyboard.press("Backspace")
                page.wait_for_timeout(400)

                # Reopen by clicking the dropdown indicator (▾ arrow button)
                reopened = page.evaluate("""(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    let p = el.parentElement;
                    for (let i = 0; i < 10; i++) {
                        if (!p) break;
                        // The dropdown indicator sits inside __control
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