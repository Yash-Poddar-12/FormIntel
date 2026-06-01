"""Form filling utilities for smart_form_tester."""

from __future__ import annotations

from playwright.sync_api import Page


class FormFiller:

    def fill_all(self, page: Page, fields: list[dict], values: dict) -> dict:
        validation_messages: dict[str, str] = {}

        for field in fields:
            field_idx = str(field.get("index"))
            field_type = str(field.get("type", "")).lower()
            label = str(field.get("label", ""))
            selector = str(field.get("selector", "")).strip()
            value = values.get(field_idx)

            if field_type == "file" or field.get("skip"):
                print(f"[FormFiller] Skipping file input: {label}")
                validation_messages[field_idx] = ""
                continue

            if not selector:
                print(f"[FormFiller] Missing selector for field index {field_idx}")
                validation_messages[field_idx] = ""
                continue

            try:
                # --- Dismiss any open overlay/dropdown/calendar before each field ---
                self._dismiss_overlays(page)

                page.wait_for_selector(selector, state="visible", timeout=5000)

                if field_type in {"text", "email", "tel", "number", "password", "textarea"}:
                    locator = page.locator(selector)
                    locator.fill("" if value is None else str(value))

                    # Check if this is an autocomplete/combobox — if dropdown opened, select first option
                    page.wait_for_timeout(600)
                    self._handle_autocomplete_dropdown(page, selector)

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
                    # Close the calendar picker that opens after date fill
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
                        # Use JS click to avoid overlay interception
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
                    else:
                        print(f"[FormFiller] checkbox-group without name at field index {field_idx}")

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
                    page.wait_for_timeout(600)
                    self._handle_autocomplete_dropdown(page, selector)

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
                # Try to dismiss any overlay that caused the failure before continuing
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
                except Exception:
                    pass
                continue

        return validation_messages

    def _dismiss_overlays(self, page: Page) -> None:
        """
        Press Escape and click body to close any open:
        - calendar date pickers
        - autocomplete dropdowns
        - React Select menus
        - modal dialogs blocking clicks
        """
        try:
            # Check if any dropdown/overlay is open before pressing Escape
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

    def _handle_autocomplete_dropdown(self, page: Page, selector: str) -> None:
        """
        After typing in a text/combobox field, if a dropdown appeared,
        click the first option to select it and close the dropdown.
        """
        try:
            # Check if an autocomplete dropdown opened
            dropdown_visible = page.evaluate("""(sel) => {
                const el = document.querySelector(sel);
                if (!el) return false;
                // Check aria-expanded
                if (el.getAttribute('aria-expanded') === 'true') return true;
                // Check for React Select menu near this element
                const menu = document.querySelector(
                    '[class*="auto-complete__menu"], [class*="react-select__menu"], ' +
                    '[class*="subjects-auto-complete__menu"]'
                );
                return menu !== null;
            }""", selector)

            if dropdown_visible:
                # Try clicking the first option in any open dropdown
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
                    # Nothing to click — just close with Escape
                    page.keyboard.press("Escape")

                page.wait_for_timeout(400)

        except Exception:
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            except Exception:
                pass