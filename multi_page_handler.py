"""Multi-page form navigation and outcome detection."""

from __future__ import annotations

import re

from playwright.sync_api import Page


class MultiPageHandler:

    _STATIC_ASSET_PATTERN = re.compile(
        r"\.(?:js|css|png|jpe?g|svg|woff2?|ico)(?:\?|#|$)",
        re.IGNORECASE,
    )

    def capture_network_response(self, page: Page, timeout_ms: int = 5000) -> list[dict]:
        """
        Capture likely form/API responses for timeout_ms and return a compact list.
        """
        captured, handler = self._start_network_capture(page)
        try:
            page.wait_for_timeout(timeout_ms)
        finally:
            self._remove_response_listener(page, handler)
        return captured

    def click_submit_or_next(self, page: Page) -> tuple[str, list[str], list[dict]]:
        """
        Click submit/next-like controls and classify result.
        Returns (result_string, captured_alert_texts, captured_network_responses).
        """
        before_url = page.url
        before_form_count = page.locator("form").count()
        before_field_count = self._count_visible_field_elements(page)

        captured_alerts: list[str] = []
        def handle_dialog(dialog):
            captured_alerts.append(dialog.message)
            dialog.accept()
        page.on("dialog", handle_dialog)

        network_responses, response_handler = self._start_network_capture(page)
        clicked = False
        clicked_kind = "nothing_clicked"

        try:
            # a) strict submit controls first
            submit = page.locator("button[type=submit], input[type=submit]").first
            if submit.count() > 0:
                try:
                    if submit.is_visible() and submit.is_enabled():
                        submit.click()
                        clicked = True
                        clicked_kind = "submitted"
                except Exception as exc:
                    print(f"[MultiPageHandler] Submit click failed: {exc}")

            # b) semantic next/continue style buttons
            if not clicked:
                texts = [
                    "submit", "next", "continue", "proceed", "verify", "confirm",
                    "check-in", "checkin", "search", "retrieve", "go", "find booking",
                ]
                for text in texts:
                    try:
                        button = page.get_by_role("button", name=re.compile(text, re.IGNORECASE)).first
                        if button.count() > 0 and button.is_visible() and button.is_enabled():
                            button.click()
                            clicked = True
                            clicked_kind = "next_clicked" if text in {"next", "continue", "proceed"} else "submitted"
                            break
                    except Exception as exc:
                        print(f"[MultiPageHandler] Role button click failed for '{text}': {exc}")

            # c) any visible enabled button fallback — SCOPED to the form/widget
            # area only. FIX: previously this searched ALL <button> elements on
            # the page with no scoping, so on content-heavy sites (e.g. Air
            # India's check-in page, full of navbar/carousel/chat-widget/scroll
            # buttons) it would click the first visible button it found — often
            # a carousel arrow or scroll-to-top control — producing an endless
            # scroll loop instead of ever reaching the real check-in button.
            # We now require the candidate button to be inside a <form>, OR
            # inside a container that actually holds one of our detected input
            # fields, and we explicitly skip known noise containers (nav,
            # header, footer, chat widgets, cookie banners).
            if not clicked:
                candidates = page.locator(
                    "form button, "
                    "[class*='checkin'] button, [class*='check-in'] button, "
                    "[class*='widget'] button, [class*='booking'] button"
                )
                total = candidates.count()
                for i in range(total):
                    try:
                        btn = candidates.nth(i)
                        if not (btn.is_visible() and btn.is_enabled()):
                            continue
                        in_noise = btn.evaluate("""(el) => {
                            const NOISE = [
                                'nav', 'header', 'footer',
                                '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
                                '[class*="cookie"]', '[class*="consent"]', '[class*="chat"]',
                                '[class*="carousel"]', '[class*="slider"]', '[class*="scroll-top"]',
                                '[class*="back-to-top"]', '[class*="newsletter"]', '[class*="promo"]',
                            ];
                            return NOISE.some(sel => { try { return el.closest(sel) !== null; } catch(_) { return false; } });
                        }""")
                        if in_noise:
                            continue
                        btn.click()
                        clicked = True
                        clicked_kind = "next_clicked"
                        break
                    except Exception:
                        continue

            if not clicked:
                page.remove_listener("dialog", handle_dialog)
                self._remove_response_listener(page, response_handler)
                return "nothing_clicked", [], network_responses

            page.wait_for_timeout(3000)

        finally:
            try:
                page.remove_listener("dialog", handle_dialog)
            except Exception:
                pass
            self._remove_response_listener(page, response_handler)

        after_url = page.url
        after_form_count = page.locator("form").count()
        after_field_count = self._count_visible_field_elements(page)

        url_changed = after_url != before_url
        form_disappeared = before_form_count > 0 and after_form_count == 0
        new_fields_appeared = after_field_count > before_field_count

        if url_changed or form_disappeared or self.detect_success(page):
            return "submitted", captured_alerts, network_responses
        if new_fields_appeared:
            return "next_clicked", captured_alerts, network_responses
        return clicked_kind, captured_alerts, network_responses

    def detect_success(self, page: Page) -> bool:
        """
        Returns True if the page shows clear success indicators after submission.

        FIX: Removed the dead `forms_disappeared` variable that was computed but
        never included in the return value. Keeping it caused false positives on
        SPAs where form disappears during navigation even on failure. Now we only
        return True on explicit success keyword hits in page text.
        """
        try:
            body_text = page.inner_text("body").lower()
        except Exception:
            body_text = ""

        success_keywords = [
            "success", "thank you", "submitted", "application number",
            "reference number", "congratulations", "approved", "received",
        ]
        return any(word in body_text for word in success_keywords)

    def detect_errors(self, page: Page) -> list[str]:
        """
        Collect errors from:
        - CSS class-based error elements
        - ARIA role="alert" elements
        - aria-live regions
        - toast/notification/snackbar/dialog elements
        - Visible elements containing error-pattern text
        """
        errors: list[str] = []
        seen: set[str] = set()

        def _add(text: str) -> None:
            t = text.strip()
            if t and t not in seen:
                seen.add(t)
                errors.append(t)

        # 1) Classic CSS error classes
        css_selectors = (
            ".error, .invalid, .field-error, .form-error, "
            "[class*='error-msg'], [class*='field-error'], "
            "[class*='validation-error'], [aria-invalid='true'], "
            ".ng-invalid.ng-touched, .is-invalid"
        )
        try:
            nodes = page.locator(css_selectors)
            for i in range(nodes.count()):
                try:
                    text = nodes.nth(i).inner_text().strip()
                    _add(text)
                except Exception:
                    continue
        except Exception as exc:
            print(f"[MultiPageHandler] CSS error locator scan failed: {exc}")

        # 2) ARIA role=alert
        try:
            alerts = page.locator("[role='alert']")
            for i in range(alerts.count()):
                try:
                    if alerts.nth(i).is_visible():
                        _add(alerts.nth(i).inner_text())
                except Exception:
                    continue
        except Exception:
            pass

        # 3) aria-live regions
        # FIX: React Select (and other accessible widgets) use a hidden
        # aria-live="polite" region purely to announce selections to screen
        # readers (e.g. "option Delhi, selected."). This is NOT an error — it
        # was being swept into all_page_errors and causing valid form fills
        # to be misreported as FAIL. We now skip aria-live text that matches
        # known selection-announcement phrasing.
        SELECTION_ANNOUNCEMENT_PATTERNS = [
            "selected.", "selected,", "deselected", "menu is open",
            "menu is closed", "results available", "option ",
        ]
        try:
            live_regions = page.locator("[aria-live='assertive'], [aria-live='polite']")
            for i in range(live_regions.count()):
                try:
                    if live_regions.nth(i).is_visible():
                        text = live_regions.nth(i).inner_text().strip()
                        if not text:
                            continue
                        text_lower = text.lower()
                        if any(pat in text_lower for pat in SELECTION_ANNOUNCEMENT_PATTERNS):
                            continue
                        _add(text)
                except Exception:
                    continue
        except Exception:
            pass

        # 4) Toast / notification / snackbar / modal dialog
        try:
            toast_sel = ".toast, .notification, .snackbar, .alert-message, dialog[open]"
            toasts = page.locator(toast_sel)
            for i in range(toasts.count()):
                try:
                    if toasts.nth(i).is_visible():
                        _add(toasts.nth(i).inner_text())
                except Exception:
                    continue
        except Exception:
            pass

        # 5) Visible elements with error-pattern text (only small, targeted elements)
        ERROR_PATTERNS = [
            "is invalid", "is incorrect", "is not valid",
            "please enter", "cannot be", "does not match",
            "invalid format", "required field", "field is required",
        ]
        try:
            visible_text_nodes = page.locator("span, p, small, div.message, div.msg, label.error")
            count = min(visible_text_nodes.count(), 50)
            for i in range(count):
                try:
                    node = visible_text_nodes.nth(i)
                    if not node.is_visible():
                        continue
                    text = node.inner_text().strip().lower()
                    if any(pat in text for pat in ERROR_PATTERNS) and len(text) < 200:
                        _add(node.inner_text().strip())
                except Exception:
                    continue
        except Exception:
            pass

        return errors

    def get_current_page_number(self, page: Page) -> int:
        try:
            body_text = page.inner_text("body")
        except Exception:
            body_text = ""

        if body_text:
            match = re.search(r"\b(?:step|page)\s*(\d+)\b", body_text, flags=re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except (TypeError, ValueError):
                    pass

        try:
            active_steps = page.locator(".step.active, .step--active")
            if active_steps.count() > 0:
                text = active_steps.first.inner_text()
                num_match = re.search(r"(\d+)", text or "")
                if num_match:
                    return int(num_match.group(1))
                return 1
        except Exception:
            pass

        return 1

    def _count_visible_field_elements(self, page: Page) -> int:
        script = """
        () => {
          const selectors = [
            'input:not([type="hidden"])',
            'select',
            'textarea',
            'div[contenteditable="true"]'
          ];
          const nodes = Array.from(document.querySelectorAll(selectors.join(',')));
          const isVisible = (el) => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          };
          return nodes.filter(isVisible).length;
        }
        """
        try:
            value = page.evaluate(script)
            return int(value) if value is not None else 0
        except Exception:
            return 0

    def _start_network_capture(self, page: Page):
        captured: list[dict] = []

        def handle_response(response):
            if len(captured) >= 5:
                return
            try:
                status_code = int(response.status)
                if not (200 <= status_code <= 299 or 400 <= status_code <= 599):
                    return

                url = str(response.url or "")
                if self._STATIC_ASSET_PATTERN.search(url):
                    return

                content_type = str(response.headers.get("content-type", "")).lower()
                if "application/json" not in content_type and "text/html" not in content_type:
                    return

                response_text = response.text()
                captured.append({
                    "url": url,
                    "status_code": status_code,
                    "response_text": (response_text or "")[:500],
                })
            except Exception:
                return

        page.on("response", handle_response)
        return captured, handle_response

    @staticmethod
    def _remove_response_listener(page: Page, handler) -> None:
        try:
            page.remove_listener("response", handler)
        except Exception:
            pass