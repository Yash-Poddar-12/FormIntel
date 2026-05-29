"""Multi-page form navigation and outcome detection."""

from __future__ import annotations

import re

from playwright.sync_api import Page


class MultiPageHandler:
    """Handle submit/next clicks and detect page state signals."""

    def click_submit_or_next(self, page: Page) -> str:
        """Click submit/next-like controls and classify result."""
        before_url = page.url
        before_form_count = page.locator("form").count()
        before_field_count = self._count_visible_field_elements(page)

        clicked = False
        clicked_kind = "nothing_clicked"

        # a) strict submit controls first
        submit = page.locator("button[type=submit], input[type=submit]").first
        if submit.count() > 0:
            try:
                if submit.is_visible() and submit.is_enabled():
                    submit.click()
                    clicked = True
                    clicked_kind = "submitted"
            except Exception as exc:  # pylint: disable=broad-except
                print(f"[MultiPageHandler] Submit click failed: {exc}")

        # b) semantic next/continue style buttons
        if not clicked:
            texts = ["submit", "next", "continue", "proceed", "verify", "confirm"]
            for text in texts:
                try:
                    button = page.get_by_role("button", name=re.compile(text, re.IGNORECASE)).first
                    if button.count() > 0 and button.is_visible() and button.is_enabled():
                        button.click()
                        clicked = True
                        clicked_kind = "next_clicked" if text in {"next", "continue", "proceed"} else "submitted"
                        break
                except Exception as exc:  # pylint: disable=broad-except
                    print(f"[MultiPageHandler] Role button click failed for '{text}': {exc}")

        # c) any visible enabled button fallback
        if not clicked:
            buttons = page.locator("button")
            total = buttons.count()
            for i in range(total):
                try:
                    btn = buttons.nth(i)
                    if btn.is_visible() and btn.is_enabled():
                        btn.click()
                        clicked = True
                        clicked_kind = "next_clicked"
                        break
                except Exception:
                    continue

        if not clicked:
            return "nothing_clicked"

        page.wait_for_timeout(3000)

        after_url = page.url
        after_form_count = page.locator("form").count()
        after_field_count = self._count_visible_field_elements(page)

        url_changed = after_url != before_url
        form_disappeared = before_form_count > 0 and after_form_count == 0
        new_fields_appeared = after_field_count > before_field_count

        if url_changed or form_disappeared or self.detect_success(page):
            return "submitted"
        if new_fields_appeared:
            return "next_clicked"
        return clicked_kind

    def detect_success(self, page: Page) -> bool:
        """Detect likely successful submission state."""
        try:
            body_text = page.inner_text("body").lower()
        except Exception:  # pylint: disable=broad-except
            body_text = ""

        success_keywords = [
            "success",
            "thank you",
            "submitted",
            "application number",
            "reference number",
            "congratulations",
            "approved",
            "received",
        ]
        keyword_hit = any(word in body_text for word in success_keywords)

        try:
            forms_disappeared = page.locator("form").count() == 0
        except Exception:  # pylint: disable=broad-except
            forms_disappeared = False

        return keyword_hit or forms_disappeared

    def detect_errors(self, page: Page) -> list[str]:
        """Collect deduplicated error-like messages from page body and error elements."""
        errors: list[str] = []
        seen: set[str] = set()

        try:
            body_text = page.inner_text("body")
        except Exception:  # pylint: disable=broad-except
            body_text = ""

        body_lower = body_text.lower()
        keywords = [
            "invalid",
            "required",
            "incorrect",
            "error",
            "failed",
            "please enter",
            "must be",
            "not valid",
            "cannot be",
        ]

        if body_text:
            for raw_line in body_text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                line_l = line.lower()
                if any(key in line_l for key in keywords):
                    if line not in seen:
                        seen.add(line)
                        errors.append(line)

            # Fallback if everything is in a single paragraph
            if not errors and any(key in body_lower for key in keywords):
                compact = " ".join(body_text.split())
                if compact and compact not in seen:
                    seen.add(compact)
                    errors.append(compact)

        selectors = (
            ".error, .invalid, .field-error, [class*='error'], "
            "[class*='invalid'], [aria-invalid='true']"
        )
        try:
            nodes = page.locator(selectors)
            total = nodes.count()
            for i in range(total):
                try:
                    text = nodes.nth(i).inner_text().strip()
                    if text and text not in seen:
                        seen.add(text)
                        errors.append(text)
                except Exception:
                    continue
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[MultiPageHandler] Error locator scan failed: {exc}")

        return errors

    def get_current_page_number(self, page: Page) -> int:
        """Infer current step/page number from text or active step indicators."""
        try:
            body_text = page.inner_text("body")
        except Exception:  # pylint: disable=broad-except
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
        except Exception:  # pylint: disable=broad-except
            pass

        return 1

    def _count_visible_field_elements(self, page: Page) -> int:
        """Count visible input-ish elements for page-transition heuristics."""
        script = """
        () => {
          const selectors = [
            'input:not([type="hidden"])',
            'select',
            'textarea',
            'div[contenteditable]',
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

