# FormIntel — Full Project Context & Codex Implementation Prompt

> **How to use this file:**
> Place this file in the root of your FormIntel repository, then open Claude Code (terminal) in that folder and paste the prompt from **Section 5** into it. Claude Code will read this file plus every source file and implement all changes in one pass.

---

## Section 1 — What FormIntel Is

FormIntel is a production-grade Python CLI tool for AI-assisted web form validation testing. It uses **Playwright** for browser automation and **Google Gemini / OpenAI GPT** for AI-generated test data.

### Core Workflow

**Phase 1 — Baseline Convergence**
1. Opens the target URL in a real Chromium browser
2. Scans the DOM to detect all input fields and their metadata
3. Sends field metadata to AI (Gemini or OpenAI), which generates a set of realistic, format-correct **valid** values
4. Fills all fields and submits
5. If validation fails, analyzes errors and retries with improved values (up to 5 iterations)
6. Repeats until a clean passing baseline is found

**Phase 2 — Multi-Variation Testing (N+1 Strategy)**
Once a passing baseline exists, for each field:
- Keeps all other fields at their passing baseline values
- Replaces only that field with an AI-generated **invalid** value
- Submits and records whether validation correctly rejected it
- Covers: empty, wrong_format, boundary_invalid, too_short, too_long, special_chars, wrong_type, future_date, starts_wrong

### Tech Stack
- Python 3.9+
- Playwright (Chromium)
- Google Gemini 2.5 Flash (primary AI) or OpenAI GPT-4.1-mini (fallback)
- Jinja2 for HTML report templating
- python-dotenv for config
- Outputs: HTML report, JSON report, CSV report

### Project Files (as of current state)
```
FormIntel/
├── main.py               # CLI entry point
├── config.py             # Settings, env vars, AI provider selection
├── field_detector.py     # DOM field detection via Playwright evaluate()
├── ai_engine.py          # Gemini + OpenAI + rule-based fallback
├── form_filler.py        # Fills every field type using Playwright
├── test_runner.py        # Phase 1 + Phase 2 orchestration
├── report_generator.py   # HTML + JSON + CSV report generation
├── multi_page_handler.py # Submit/Next click and success/error detection
├── requirements.txt
├── setup.bat / setup.sh
└── README.md
```

---

## Section 2 — What Has Been Built So Far

| Module | Status | Notes |
|--------|--------|-------|
| `config.py` | ✅ Complete | OpenAI priority > Gemini > fallback |
| `field_detector.py` | ✅ Complete | Handles all HTML5 types incl. radio groups, checkbox groups, contenteditable |
| `ai_engine.py` | ✅ Complete | Full OpenAI + Gemini + fallback, rate limit handling, retry logic |
| `form_filler.py` | ✅ Complete | All field types, overlay dismissal, autocomplete dropdown handling |
| `multi_page_handler.py` | ✅ Complete | Submit/Next detection, success/error detection |
| `test_runner.py` | ✅ Complete | Phase 1 convergence + Phase 2 N+1 variation loop |
| `report_generator.py` | ✅ Complete | HTML (dark mode, interactive) + JSON + CSV |
| `main.py` | ✅ Complete | CLI with --url, --headless, --output-dir |
| `main_csv.py` | ❌ Missing | Needs to be created |
| UI entry point (Gradio/Tkinter) | ❌ Missing | Needs to be created |

---

## Section 3 — All Issues Found + Required Fixes

### Issue 1: Dropdown/Autocomplete — After typing, field goes blank

**Problem:** When a field has an autocomplete dropdown (React Select, custom JS dropdowns), Playwright types the value, the dropdown appears, but if it's not properly selected, the field value clears when focus moves to the next field. The current `_handle_autocomplete_dropdown` logic fires, but sometimes the timing is wrong — it checks before the dropdown has actually rendered.

**Fix Required:**
- After filling a text field, wait 800ms (not 600ms) before checking for dropdown
- Add a second check: after calling `_handle_autocomplete_dropdown`, read back the field's current `.value` from the DOM
- If the value is empty or different from what was typed, try again: press `ArrowDown` then `Enter` as a fallback
- Add a `verify_fill` method that reads DOM `.value` of every filled field after the entire `fill_all()` loop completes, returns a dict of `{field_idx: actual_dom_value}`

### Issue 2: Field value verification before submit (False Positives)

**Problem:** Playwright can "fill" a field (no exception thrown), but the form's JS can reject the value and clear the field. This means the form is submitted with blank/default values, leading to a false PASS.

**Fix Required:**
- After `fill_all()` completes (before clicking submit), run a DOM read of every non-file, non-skip field
- For each field, call `element.value` (or `element.textContent` for contenteditable) via `page.evaluate()`
- Compare what was intended vs what is actually in the field
- If the actual value is empty/blank AND the intended value was non-empty, log a warning: `[FormFiller] WARNING: Field {label} value did not stick — intended '{intended}', actual '{actual}'`
- Add this info to the result record as `fill_verification: {field_idx: {intended, actual, stuck: bool}}`
- In `_determine_status`, if any required field has `stuck=False`, add a note to the reason string

### Issue 3: OTP Fields — Wait for user input instead of filling

**Problem:** Some forms have OTP fields (one-time password). The tool currently tries to fill these like any text field, which fails because OTP is sent to user's phone and must be entered manually.

**Fix Required:**
- In `field_detector.py`, detect OTP fields: label/name/id/placeholder contains "otp", "one time", "one-time", "verification code", "verify code", "sms code"
- Mark them as `"type": "otp"` in the metadata
- In `form_filler.py`, when field type is `otp`:
  - Print a prominent message: `[OTP] Waiting for manual OTP entry on field '{label}'. You have 3 minutes.`
  - Wait up to 180 seconds (configurable via `OTP_WAIT_SECONDS` env var, default 180)
  - Poll every 5 seconds to check if the field has a non-empty value (meaning user entered the OTP manually)
  - If user hasn't entered after 180s, prompt once more: `[OTP] Still waiting... 2 more minutes remaining.`
  - Allow up to 120 more seconds (configurable via `OTP_EXTRA_SECONDS`, default 120)
  - If still empty after total wait, skip field and log `[OTP] Timed out waiting for OTP on field '{label}' — skipping`
  - Add `OTP_WAIT_SECONDS=180` and `OTP_EXTRA_SECONDS=120` to `.env` template in `setup.bat` and `setup.sh`

### Issue 4: Required-fields-only mode (Minimum Viable Submission)

**Problem:** The tool currently fills ALL detected fields. For general/public forms, this is overkill and can trigger unexpected validation on optional fields. Users want a mode where only required fields are filled.

**Fix Required:**
- Add a new CLI flag: `--required-only` (boolean, default `False`)
- Add `REQUIRED_ONLY=false` to `.env` and `config.py`
- In `test_runner.py`, if `required_only=True`:
  - Filter `fields` to only those where `field["required"] == True` before generating baseline values and before variation testing
  - Log: `[TestRunner] required-only mode: {len(required_fields)}/{len(all_fields)} fields selected`
- In AI prompts in `ai_engine.py`, when required-only mode is on, include a note: "Only fill required fields. Leave optional fields empty."
- In `field_detector.py` JS script, also check for `aria-required="true"` and for fields inside a `<fieldset required>` parent — these should all be marked `required: true`

### Issue 5: Pass/Fail Detection — Too UI-centric, misses server errors

**Problem:** Current `_determine_status` in `test_runner.py` only checks:
1. Browser `.validationMessage` (HTML5 native)
2. CSS class-based error elements
3. A hardcoded success keyword list

This misses: JS alert/confirm pop-ups, toast notifications, modal error dialogs, `aria-live` announcement regions, server-side error messages returned after form submission (non-URL-change responses), and generic div-based error messages not using error CSS classes.

**Fix Required in `multi_page_handler.py`:**

`detect_errors()` should be expanded to also check:
- `[role="alert"]` elements
- `[aria-live="assertive"]` or `[aria-live="polite"]` elements with non-empty text
- `.toast, .notification, .snackbar, .alert, .message` elements
- Elements containing text matching error patterns: "invalid", "incorrect", "failed", "error", "please enter", "required", "cannot", "not valid", "does not match" — but ONLY if those elements are visible (not hidden by CSS)
- `dialog` elements that are open (`[open]` attribute or `display != none`)
- JS `window.alert()` interception: before any click action, set up a dialog handler in Playwright that captures alert text

**Fix Required in `test_runner.py` → `_determine_status`:**
- Add a new input: `alert_text: str | None` (captured dialog text)
- If `alert_text` is non-empty, that is a FAIL with reason `Alert dialog fired: {alert_text[:80]}`
- Weight the checks: alert > browser validation > page error elements > success keywords > default PASS

**Playwright dialog interception in `multi_page_handler.py` → `click_submit_or_next()`:**
```python
captured_alerts = []
def handle_dialog(dialog):
    captured_alerts.append(dialog.message)
    dialog.accept()
page.on("dialog", handle_dialog)
# ... do the click ...
page.remove_listener("dialog", handle_dialog)
return result, captured_alerts
```

### Issue 6: Variation testing fills "other fields" with old/wrong values

**Problem:** In Phase 2, when testing an invalid value for field X, the code does `test_values = passing_baseline.copy()` then sets `test_values[field_idx] = invalid_value`. This is correct in theory, but when `fill_all()` runs, the form is reloaded fresh — so all fields start empty. The baseline values for all other fields are filled correctly. However, for fields that have autocomplete or dynamic behavior, filling in the baseline value first (for context) can trigger side effects that change OTHER field values (e.g., selecting a city auto-fills state, or selecting a product auto-fills price). This means by the time we reach field X, other fields may have drifted.

**Fix Required:**
- After `fill_all(page, fields, test_values)` completes, run the post-fill verification (Issue 2 fix)
- Log any field whose value drifted from the intended value
- In the result record, note drifted fields in `fill_verification`
- This doesn't change the test outcome but makes it auditable

### Issue 7: Missing `main_csv.py`

**Problem:** A `main_csv.py` was referenced in earlier discussions but never created. It should regenerate reports from existing JSON without re-running tests.

**Fix Required:** Create `main_csv.py` as a standalone script:
- Accepts a JSON report file path as argument
- Regenerates HTML and CSV from it
- Usage: `python main_csv.py reports/mysite__20250601_120000/mysite__20250601_120000.json`

### Issue 8: Missing UI Entry Point

**Problem:** No GUI exists. Users must use the CLI. A simple web UI (Gradio) would allow non-technical users to enter a URL and run tests.

**Fix Required:** Create `ui.py` using Gradio:
- Input: URL text box, headless checkbox, required-only checkbox, output directory text box
- Button: "Run FormIntel"
- Live log output panel (streaming)
- When done: show a download link for the HTML report
- Usage: `python ui.py` → opens browser at `http://localhost:7860`

---

## Section 4 — Full Updated Source Code

### `config.py` (updated — adds OTP and required-only settings)

```python
"""Application configuration for FormIntel."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_env_path, override=True)


def _to_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _to_int(value, default):
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


GEMINI_API_KEY:       str  = os.getenv("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY:       str  = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL:         str  = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
PLAYWRIGHT_HEADLESS:  bool = _to_bool(os.getenv("PLAYWRIGHT_HEADLESS"), default=False)
PLAYWRIGHT_SLOW_MO:   int  = _to_int(os.getenv("PLAYWRIGHT_SLOW_MO"), default=700)
DEFAULT_TIMEOUT:      int  = _to_int(os.getenv("DEFAULT_TIMEOUT"), default=30000)
REQUIRED_ONLY:        bool = _to_bool(os.getenv("REQUIRED_ONLY"), default=False)
OTP_WAIT_SECONDS:     int  = _to_int(os.getenv("OTP_WAIT_SECONDS"), default=180)
OTP_EXTRA_SECONDS:    int  = _to_int(os.getenv("OTP_EXTRA_SECONDS"), default=120)

GEMINI_MODEL_NAME: str = "gemini-2.5-flash"
SYSTEM_PROMPT: str = (
    "You are a QA test data generator. Generate realistic, format-correct values "
    "for form fields. For financial/banking forms use Indian formats. Always return "
    "pure JSON, no explanation. For valid values: use real-looking but fake data "
    "(no real PAN/Aadhaar). For invalid values: use values that violate the "
    "field's expected format."
)
MAX_CONVERGENCE_ITERATIONS: int = 5

if OPENAI_API_KEY:
    AI_PROVIDER = "openai"
elif GEMINI_API_KEY:
    AI_PROVIDER = "gemini"
else:
    AI_PROVIDER = "fallback"

print(f"[Config] API Key loaded : {'YES' if (OPENAI_API_KEY or GEMINI_API_KEY) else 'NO'}")
if AI_PROVIDER == "openai":
    print(f"[Config] AI Provider    : OPENAI ({OPENAI_MODEL})")
elif AI_PROVIDER == "gemini":
    print(f"[Config] AI Provider    : GEMINI ({GEMINI_MODEL_NAME})")
else:
    print("[Config] AI Provider    : FALLBACK (rule-based, no API key found)")
print(f"[Config] Required-only  : {REQUIRED_ONLY}")


@dataclass(frozen=True)
class Settings:
    gemini_api_key:             str  = GEMINI_API_KEY
    openai_api_key:             str  = OPENAI_API_KEY
    openai_model:               str  = OPENAI_MODEL
    ai_provider:                str  = AI_PROVIDER
    playwright_headless:        bool = PLAYWRIGHT_HEADLESS
    playwright_slow_mo:         int  = PLAYWRIGHT_SLOW_MO
    default_timeout:            int  = DEFAULT_TIMEOUT
    gemini_model_name:          str  = GEMINI_MODEL_NAME
    system_prompt:              str  = SYSTEM_PROMPT
    max_convergence_iterations: int  = MAX_CONVERGENCE_ITERATIONS
    required_only:              bool = REQUIRED_ONLY
    otp_wait_seconds:           int  = OTP_WAIT_SECONDS
    otp_extra_seconds:          int  = OTP_EXTRA_SECONDS
```

---

### `field_detector.py` (updated — adds OTP detection, improves required detection)

The JS script inside `_SCRIPT` needs these additions:

1. After building each field dict, add OTP detection:
```javascript
const OTP_HINTS = ["otp", "one time", "one-time", "verification code", "verify code", "sms code", "passcode"];
const isOtp = (field) => {
    const combined = [field.label, field.name, field.id,
        toText(node.getAttribute("placeholder")),
        toText(node.getAttribute("aria-label"))].join(" ").toLowerCase();
    return OTP_HINTS.some(hint => combined.includes(hint));
};
```

2. If `isOtp(field)` is true, set `field.type = "otp"` and `field.skip = false`.

3. For required detection, also check:
```javascript
const isRequired = (el) => {
    if (el.required) return true;
    if (el.getAttribute("aria-required") === "true") return true;
    const fieldset = el.closest("fieldset");
    if (fieldset && fieldset.hasAttribute("required")) return true;
    // Check for asterisk in label text
    const label = getElementLabel(el);
    if (label.includes("*")) return true;
    return false;
};
```

---

### `form_filler.py` (full updated — adds OTP wait, post-fill verification, better dropdown timing)

```python
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
```

---

### `multi_page_handler.py` (full updated — adds dialog capture, expanded error detection)

```python
"""Multi-page form navigation and outcome detection."""

from __future__ import annotations

import re

from playwright.sync_api import Page


class MultiPageHandler:

    def click_submit_or_next(self, page: Page) -> tuple[str, list[str]]:
        """
        Click submit/next-like controls and classify result.
        Returns (result_string, captured_alert_texts).
        """
        before_url = page.url
        before_form_count = page.locator("form").count()
        before_field_count = self._count_visible_field_elements(page)

        # Set up dialog (alert/confirm/prompt) capture
        captured_alerts: list[str] = []
        def handle_dialog(dialog):
            captured_alerts.append(dialog.message)
            dialog.accept()
        page.on("dialog", handle_dialog)

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
                texts = ["submit", "next", "continue", "proceed", "verify", "confirm"]
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
                page.remove_listener("dialog", handle_dialog)
                return "nothing_clicked", []

            page.wait_for_timeout(3000)

        finally:
            try:
                page.remove_listener("dialog", handle_dialog)
            except Exception:
                pass

        after_url = page.url
        after_form_count = page.locator("form").count()
        after_field_count = self._count_visible_field_elements(page)

        url_changed = after_url != before_url
        form_disappeared = before_form_count > 0 and after_form_count == 0
        new_fields_appeared = after_field_count > before_field_count

        if url_changed or form_disappeared or self.detect_success(page):
            return "submitted", captured_alerts
        if new_fields_appeared:
            return "next_clicked", captured_alerts
        return clicked_kind, captured_alerts

    def detect_success(self, page: Page) -> bool:
        try:
            body_text = page.inner_text("body").lower()
        except Exception:
            body_text = ""

        success_keywords = [
            "success", "thank you", "submitted", "application number",
            "reference number", "congratulations", "approved", "received",
        ]
        keyword_hit = any(word in body_text for word in success_keywords)

        try:
            forms_disappeared = page.locator("form").count() == 0
        except Exception:
            forms_disappeared = False

        return keyword_hit or forms_disappeared

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
        try:
            live_regions = page.locator("[aria-live='assertive'], [aria-live='polite']")
            for i in range(live_regions.count()):
                try:
                    if live_regions.nth(i).is_visible():
                        text = live_regions.nth(i).inner_text().strip()
                        if text:
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

        # 5) Visible elements with error-pattern text (only if small, targeted elements)
        ERROR_PATTERNS = [
            "is invalid", "is incorrect", "is not valid",
            "please enter", "cannot be", "does not match",
            "invalid format", "required field", "field is required",
        ]
        try:
            visible_text_nodes = page.locator("span, p, small, div.message, div.msg, label.error")
            count = min(visible_text_nodes.count(), 50)  # cap to avoid scanning whole page
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
```

---

### `test_runner.py` (full updated — required-only mode, fill verification, alert capture, improved status)

```python
"""Core baseline convergence and N+1 variation execution."""

from __future__ import annotations

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
        baseline_values: dict | None = None
        all_fields: list[dict] = []
        active_fields: list[dict] = []

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
                print(f"[Iteration {iteration + 1}] Detected {len(all_fields)} fields total")

                if required_only:
                    active_fields = [f for f in all_fields if f.get("required") and not f.get("skip")]
                    print(f"[Iteration {iteration + 1}] Required-only mode: {len(active_fields)}/{len(all_fields)} fields selected")
                else:
                    active_fields = [f for f in all_fields if not f.get("skip") or f.get("type") == "otp"]

                if len(active_fields) == 0:
                    print(f"[Iteration {iteration + 1}] No active fields — skipping iteration")
                    continue

                if iteration == 0:
                    baseline_values = ai.generate_baseline_values(active_fields)
                else:
                    page_errors = handler.detect_errors(page)
                    _ = ai.analyze_page_errors(" ".join(page_errors), active_fields)
                    baseline_values = ai.generate_baseline_values(active_fields)

                print(f"[Iteration {iteration + 1}] Values: {baseline_values}")

                validation_msgs = filler.fill_all(page, active_fields, baseline_values)

                # Post-fill verification
                fill_verification = filler.verify_fills(page, active_fields, baseline_values)
                unstuck_fields = [k for k, v in fill_verification.items() if not v.get("stuck") and v.get("intended")]
                if unstuck_fields:
                    print(f"[Iteration {iteration + 1}] WARNING: {len(unstuck_fields)} fields did not accept their values: {unstuck_fields}")

                click_result, alert_texts = handler.click_submit_or_next(page)
                page.wait_for_timeout(2000)

                errors = handler.detect_errors(page)
                success = handler.detect_success(page)
                status, reason = self._determine_status(
                    validation_msgs, errors, success, alert_texts, fill_verification
                )

                print(f"[Iteration {iteration + 1}] Status: {status} — {reason}")

                results.append({
                    "test_name": f"BASELINE_ITER_{iteration + 1}",
                    "changed_field": None,
                    "changed_value": None,
                    "variation_type": "baseline",
                    "all_values": baseline_values,
                    "validation_messages": validation_msgs,
                    "page_errors": errors,
                    "alert_texts": alert_texts,
                    "fill_verification": fill_verification,
                    "status": status,
                    "pass_reason": reason,
                    "url": page.url,
                    "page_number": handler.get_current_page_number(page),
                })

                if status == "PASS":
                    passing_baseline = baseline_values
                    print("[Baseline] Passing baseline found. Starting variation tests.")
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
                    "fill_verification": {},
                    "status": "ERROR",
                    "pass_reason": str(exc),
                    "url": "",
                    "page_number": 1,
                })
                break

        if passing_baseline is None:
            passing_baseline = baseline_values or {}

        if not active_fields:
            print("[TestRunner] No fields detected — cannot run variation tests.")
            return results

        # ----------------------------------------------------------------
        # PHASE 2: MULTI-VARIATION TESTING PER FIELD
        # ----------------------------------------------------------------
        variation_fields = [f for f in active_fields if f.get("type") != "otp"]

        for field in variation_fields:
            field_idx = str(field["index"])
            field_label = field.get("label", f"field_{field_idx}")

            variations = ai.generate_field_invalid_variations(
                field, passing_baseline.get(field_idx)
            )

            print(f"\n[Field '{field_label}'] Testing {len(variations)} invalid variations:")
            for v in variations:
                print(f"  • {v['variation_name']}: {repr(v['value'])}")

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

                    validation_msgs = filler.fill_all(page, active_fields, test_values)

                    # Post-fill verification
                    fill_verification = filler.verify_fills(page, active_fields, test_values)
                    unstuck = [k for k, v in fill_verification.items() if not v.get("stuck") and v.get("intended")]
                    if unstuck:
                        print(f"  [{variation_name}] WARNING: Fields did not accept values: {unstuck}")

                    click_result, alert_texts = handler.click_submit_or_next(page)
                    page.wait_for_timeout(2000)

                    errors = handler.detect_errors(page)
                    success = handler.detect_success(page)
                    status, reason = self._determine_status(
                        validation_msgs, errors, success, alert_texts, fill_verification
                    )

                    print(f"  [{variation_name}] → {status} — {reason}")

                    results.append({
                        "test_name": f"FIELD_{field['index']}_{variation_name.upper()}",
                        "changed_field": field_label,
                        "changed_value": invalid_value,
                        "variation_type": variation_name,
                        "all_values": test_values,
                        "validation_messages": validation_msgs,
                        "page_errors": errors,
                        "alert_texts": alert_texts,
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
                        "fill_verification": {},
                        "status": "ERROR",
                        "pass_reason": str(exc),
                        "url": "",
                        "page_number": 1,
                    })
                    continue

        return results

    @staticmethod
    def _determine_status(
        validation_msgs: dict,
        page_errors: list[str],
        success: bool,
        alert_texts: list[str] | None = None,
        fill_verification: dict | None = None,
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

        # Priority 5: Fill verification — if the field we're testing didn't stick
        if fill_verification:
            unstuck = [k for k, v in fill_verification.items() if not v.get("stuck") and v.get("intended")]
            if unstuck:
                return "PASS", f"Form may have rejected input silently (fields {unstuck} value did not stick)"

        return "PASS", "Values accepted client-side (no browser, alert, or element errors)"
```

---

### `main.py` (updated — adds --required-only flag)

```python
"""CLI entrypoint for FormIntel."""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from config import Settings
from report_generator import ReportGenerator
from test_runner import TestRunner


def _slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")
    domain_parts = domain.split(".")
    if len(domain_parts) >= 2:
        domain_slug = domain_parts[-2]
    else:
        domain_slug = domain_parts[0]

    path = parsed.path.strip("/")
    if not path:
        path = parsed.fragment.strip("/")
    path_parts = [p for p in path.split("/") if p]
    path_slug = path_parts[-1] if path_parts else ""

    if path_slug and path_slug != domain_slug:
        slug = f"{domain_slug}_{path_slug}"
    else:
        slug = domain_slug

    slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:40]


def main() -> None:
    parser = argparse.ArgumentParser(description="FormIntel — AI-powered form validation tester")
    parser.add_argument("--url", required=True, help="Full URL of the form to test")
    parser.add_argument("--headless", action="store_true", default=False, help="Run browser without UI")
    parser.add_argument("--output-dir", default="reports", help="Root folder for report output")
    parser.add_argument("--required-only", action="store_true", default=False,
                        help="Fill and test only required fields (skip optional fields)")
    args = parser.parse_args()

    config = Settings()
    headless = args.headless or config.playwright_headless

    # CLI flag overrides .env for required_only
    import dataclasses
    if args.required_only:
        config = dataclasses.replace(config, required_only=True)

    results = []
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    site_slug = _slug_from_url(args.url)
    run_folder = f"{args.output_dir}/{site_slug}__{run_timestamp}"

    print(f"[Main] Target        : {args.url}")
    print(f"[Main] Run ID        : {site_slug}__{run_timestamp}")
    print(f"[Main] Required-only : {config.required_only}")
    print(f"[Main] Reports       : {run_folder}/")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=config.playwright_slow_mo)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(config.default_timeout)

        try:
            runner = TestRunner()
            results = runner.run(args.url, page, config)
        except Exception as exc:
            print(f"[Main] Test run interrupted: {exc}")
            print("[Main] Saving partial results...")
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    if results:
        reporter = ReportGenerator()
        reporter.generate(results, output_dir=run_folder, timestamp=run_timestamp, site_slug=site_slug)
        print(f"[Main] All reports saved to: {run_folder}/")
        print(f"[Main] Total test cases recorded: {len(results)}")
    else:
        print("[Main] No results to report.")


if __name__ == "__main__":
    main()
```

---

### `main_csv.py` (NEW — regenerate reports from existing JSON)

```python
"""Regenerate HTML and CSV reports from an existing JSON report file.

Usage:
    python main_csv.py reports/mysite__20250601_120000/mysite__20250601_120000.json
    python main_csv.py reports/mysite__20250601_120000/mysite__20250601_120000.json --output-dir reports/regenerated
"""

from __future__ import annotations

import argparse
from pathlib import Path

from report_generator import ReportGenerator


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate FormIntel HTML and CSV reports from an existing JSON file."
    )
    parser.add_argument("json_file", help="Path to the existing JSON report file")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to save regenerated reports (default: same folder as JSON file)",
    )
    args = parser.parse_args()

    json_path = Path(args.json_file)
    if not json_path.exists():
        print(f"[Error] JSON file not found: {args.json_file}")
        return

    output_dir = args.output_dir if args.output_dir else str(json_path.parent)

    print(f"[main_csv] Reading : {json_path}")
    print(f"[main_csv] Output  : {output_dir}/")

    reporter = ReportGenerator()
    reporter.regenerate_html_from_json(str(json_path), output_dir=output_dir)
    print("[main_csv] Done.")


if __name__ == "__main__":
    main()
```

---

### `ui.py` (NEW — Gradio web UI)

```python
"""Gradio web UI for FormIntel.

Run:  python ui.py
Then open: http://localhost:7860
"""

from __future__ import annotations

import dataclasses
import os
import re
import sys
import threading
from datetime import datetime
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse

import gradio as gr
from playwright.sync_api import sync_playwright

from config import Settings
from report_generator import ReportGenerator
from test_runner import TestRunner


def _slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")
    parts = domain.split(".")
    domain_slug = parts[-2] if len(parts) >= 2 else parts[0]
    path = parsed.path.strip("/") or parsed.fragment.strip("/")
    path_parts = [p for p in path.split("/") if p]
    path_slug = path_parts[-1] if path_parts else ""
    slug = f"{domain_slug}_{path_slug}" if path_slug and path_slug != domain_slug else domain_slug
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:40]


class LogCapture:
    """Thread-safe log buffer for streaming to Gradio."""
    def __init__(self):
        self._lines: list[str] = []
        self._lock = threading.Lock()

    def write(self, text: str):
        with self._lock:
            self._lines.append(text)

    def flush(self): pass

    def getvalue(self) -> str:
        with self._lock:
            return "".join(self._lines)


def run_formIntel(
    url: str,
    headless: bool,
    required_only: bool,
    output_dir: str,
    slow_mo: int,
    progress=gr.Progress(track_tqdm=False),
):
    if not url.strip():
        yield "❌ Please enter a URL.", None
        return

    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    log = LogCapture()
    original_stdout = sys.stdout
    sys.stdout = log

    html_report_path = None
    results = []

    try:
        config = Settings()
        if required_only:
            config = dataclasses.replace(config, required_only=True)

        run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        site_slug = _slug_from_url(url)
        run_folder = f"{output_dir.strip() or 'reports'}/{site_slug}__{run_timestamp}"

        print(f"[UI] Starting FormIntel for: {url}")
        print(f"[UI] Required-only: {required_only} | Headless: {headless}")
        print(f"[UI] Reports will be saved to: {run_folder}/")
        yield log.getvalue(), None

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless, slow_mo=config.playwright_slow_mo)
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(config.default_timeout)

            try:
                runner = TestRunner()
                results = runner.run(url, page, config)
                yield log.getvalue(), None
            except Exception as exc:
                print(f"[UI] Test run interrupted: {exc}")
                yield log.getvalue(), None
            finally:
                try:
                    context.close()
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass

        if results:
            reporter = ReportGenerator()
            reporter.generate(results, output_dir=run_folder, timestamp=run_timestamp, site_slug=site_slug)
            print(f"\n[UI] ✅ Done! {len(results)} test cases recorded.")
            print(f"[UI] Reports saved to: {run_folder}/")

            # Find the HTML file
            html_files = list(Path(run_folder).glob("*.html"))
            if html_files:
                html_report_path = str(html_files[0])
                print(f"[UI] HTML report: {html_report_path}")
        else:
            print("[UI] ⚠️ No results to report. The page may have had no detectable fields.")

    except Exception as exc:
        print(f"[UI] Fatal error: {exc}")
    finally:
        sys.stdout = original_stdout

    yield log.getvalue(), html_report_path


def build_ui():
    with gr.Blocks(
        title="FormIntel",
        theme=gr.themes.Base(
            primary_hue="blue",
            neutral_hue="slate",
        ),
        css="""
        .gradio-container { max-width: 900px !important; margin: auto; }
        #title { text-align: center; margin-bottom: 10px; }
        """
    ) as demo:
        gr.Markdown("# 🧪 FormIntel\n**AI-powered web form validation tester**", elem_id="title")

        with gr.Row():
            with gr.Column(scale=3):
                url_input = gr.Textbox(
                    label="Form URL",
                    placeholder="https://yoursite.com/apply",
                    lines=1,
                )
            with gr.Column(scale=1):
                run_btn = gr.Button("▶ Run Tests", variant="primary", size="lg")

        with gr.Row():
            with gr.Column():
                headless_cb = gr.Checkbox(label="Headless (no browser window)", value=False)
                required_only_cb = gr.Checkbox(label="Required fields only", value=False)
            with gr.Column():
                output_dir_input = gr.Textbox(label="Output directory", value="reports", lines=1)
                slow_mo_slider = gr.Slider(
                    label="Browser slow-mo (ms)", minimum=0, maximum=3000, step=100, value=700
                )

        with gr.Row():
            log_output = gr.Textbox(
                label="Live Log",
                lines=20,
                max_lines=40,
                interactive=False,
                placeholder="Logs will appear here when you click Run Tests...",
            )

        with gr.Row():
            file_output = gr.File(label="Download HTML Report", visible=True)

        gr.Markdown(
            "**Tips:** Set `GEMINI_API_KEY` or `OPENAI_API_KEY` in your `.env` file before running. "
            "Use *Required fields only* for forms with many optional fields."
        )

        run_btn.click(
            fn=run_formIntel,
            inputs=[url_input, headless_cb, required_only_cb, output_dir_input, slow_mo_slider],
            outputs=[log_output, file_output],
        )

    return demo


if __name__ == "__main__":
    ui = build_ui()
    ui.launch(server_name="0.0.0.0", server_port=7860, share=False)
```

---

### `requirements.txt` (updated — adds gradio)

```
playwright==1.54.0
google-generativeai==0.8.5
openai>=1.30.0
python-dotenv==1.0.1
Jinja2==3.1.4
gradio>=4.0.0
```

---

### `.env` template (updated — add to `setup.bat` and `setup.sh`)

```
GEMINI_API_KEY=your_key_here
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini
PLAYWRIGHT_HEADLESS=false
PLAYWRIGHT_SLOW_MO=700
DEFAULT_TIMEOUT=30000
REQUIRED_ONLY=false
OTP_WAIT_SECONDS=180
OTP_EXTRA_SECONDS=120
```

---

## Section 5 — Claude Code Prompt (paste this into Claude Code terminal)

```
Read this entire file (CODEX_PROMPT.md) and all existing source files in this repo before making any changes.

I need you to implement all of the following changes to this FormIntel Python project. Apply every change described below precisely.

---

CHANGE 1: config.py
Replace the existing config.py with the full updated version in Section 4 of CODEX_PROMPT.md.
Add REQUIRED_ONLY, OTP_WAIT_SECONDS, OTP_EXTRA_SECONDS to the Settings dataclass and module-level constants.

CHANGE 2: field_detector.py
In the JavaScript _SCRIPT string inside FieldDetector, make these changes:
a) Add OTP detection: after building each field's dict, check if label/name/id/placeholder contains any of ["otp","one time","one-time","verification code","verify code","sms code","passcode"]. If yes, set field.type = "otp".
b) Improve required detection: also check aria-required="true", parent fieldset[required], and whether the label text contains an asterisk "*". If any of these are true, set field.required = true.

CHANGE 3: form_filler.py
Replace the entire form_filler.py with the full updated version in Section 4 of CODEX_PROMPT.md.
Key additions:
- __init__ takes otp_wait_seconds and otp_extra_seconds parameters
- New _wait_for_otp() method: polls DOM every 5s for 3 min, then prompts again for 2 min
- New verify_fills() method: reads back actual DOM values after fill_all() and compares to intended values
- _handle_autocomplete_dropdown() waits 800ms (up from 600ms), adds ArrowDown+Enter fallback, verifies value stuck
- fill_all() calls _handle_autocomplete_dropdown() with the intended_value argument so it can check if value stuck

CHANGE 4: multi_page_handler.py
Replace entire multi_page_handler.py with the full updated version in Section 4 of CODEX_PROMPT.md.
Key additions:
- click_submit_or_next() now returns tuple[str, list[str]] — the second element is a list of captured JS alert/dialog texts. Use page.on("dialog", handler) before clicking and page.remove_listener() after.
- detect_errors() is expanded to also check: [role="alert"], [aria-live] regions, .toast/.snackbar/.notification/dialog[open], and visible short text elements matching error patterns.

CHANGE 5: test_runner.py
Replace entire test_runner.py with the full updated version in Section 4 of CODEX_PROMPT.md.
Key additions:
- required_only mode: if config.required_only is True, filter fields to only those with required=True before Phase 1 and Phase 2
- OTP fields (type="otp") are included in fill_all() but excluded from Phase 2 variation testing
- After every fill_all() call, call filler.verify_fills() and log any fields that did not stick
- click_submit_or_next() now returns (result, alert_texts) — unpack both and pass alert_texts to _determine_status
- _determine_status() takes new parameters: alert_texts and fill_verification. Priority order: alert > success > browser_validation > page_errors > fill_verification > default_pass

CHANGE 6: main.py
Replace entire main.py with the full updated version in Section 4 of CODEX_PROMPT.md.
Key addition: --required-only CLI flag that sets config.required_only = True using dataclasses.replace().

CHANGE 7: CREATE main_csv.py (new file)
Create main_csv.py in the project root with the full code in Section 4 of CODEX_PROMPT.md.

CHANGE 8: CREATE ui.py (new file)
Create ui.py in the project root with the full code in Section 4 of CODEX_PROMPT.md.
This is a Gradio web UI. It must import from config, test_runner, and report_generator.

CHANGE 9: requirements.txt
Add "gradio>=4.0.0" as a new line to requirements.txt.

CHANGE 10: setup.bat and setup.sh
In both setup files, update the .env template section to include all the new variables:
REQUIRED_ONLY=false
OTP_WAIT_SECONDS=180
OTP_EXTRA_SECONDS=120
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini

CHANGE 11: report_generator.py
In _generate_json() and _generate_html_from_json(), handle the new fields added to result records:
- alert_texts: list[str] — new field in results, add to JSON output and show in HTML detail panel as "Alert Dialogs"
- fill_verification: dict — new field in results, add to JSON output and show in HTML detail panel as "Fill Verification"
Update the HTML template to show both of these in the expandable row detail section alongside the existing validation messages and page errors.

---

After implementing all changes, verify:
1. python main.py --help shows --required-only flag
2. python main_csv.py --help works
3. python ui.py imports without errors (but don't launch it)
4. All existing imports in test_runner.py resolve (AIEngine, FieldDetector, FormFiller, MultiPageHandler all imported correctly)

Do NOT run the actual tests (no playwright browser launch). Just implement and verify imports.
```

---

## Section 6 — Which AI Model to Use

For Claude Code itself (the coding assistant): **Claude Sonnet 4.5** or **Claude Opus 4.6** — both work well. Sonnet is faster and cheaper for iterative coding; use Opus for the hardest architectural decisions.

For FormIntel's internal AI (generating test data):

| Provider | Model | Recommended? | Notes |
|----------|-------|-------------|-------|
| OpenAI | `gpt-4.1-mini` | ✅ **Best for this project** | Fast, cheap, great JSON output, high rate limits |
| OpenAI | `gpt-4.1` | For complex forms | Higher quality but 10x cost |
| Gemini | `gemini-2.5-flash` | ✅ Good free option | Free tier is 20 req/day — hits limit fast on large forms |
| Gemini | `gemini-2.5-pro` | Overkill | Too slow for this use case |

**Recommendation:** Set `OPENAI_API_KEY` in `.env` and leave `GEMINI_API_KEY` empty. The config auto-selects OpenAI if the key is present. GPT-4.1-mini is the sweet spot — accurate JSON, handles Indian formats well, and won't hit rate limits on a 10-field form with 5 variations each (that's ~60 API calls).

---

## Section 7 — How to Run After Implementation

```bash
# Install new dependencies (gradio)
pip install -r requirements.txt

# Test the CLI (original way)
python main.py --url "http://localhost:8000/tests/mock_form.html"

# Test CLI with required-only mode
python main.py --url "https://somesite.com/form" --required-only

# Test CLI with headless + required-only
python main.py --url "https://somesite.com/form" --headless --required-only

# Regenerate report from existing JSON (no browser needed)
python main_csv.py reports/mysite__20250601_120000/mysite__20250601_120000.json

# Launch the web UI
python ui.py
# Then open: http://localhost:7860
```

---

## Section 8 — Project Context Summary (for new chat transfer)

FormIntel is a Python CLI + web UI tool that automates web form validation testing using Playwright (browser automation) and AI (OpenAI GPT or Google Gemini) for test data generation.

**What it does in plain language:** You give it a URL with a form. It opens the form in a real Chromium browser, figures out what all the fields are (name, type, required/optional, options for dropdowns etc.), asks AI to generate realistic valid data for all fields, fills and submits the form, and repeats if it fails validation (up to 5 times). Once a working baseline is found, it then goes field-by-field and tests each one with invalid data (empty, wrong format, too short, special characters, future dates, etc.) to check if the form properly rejects bad input. It generates a beautiful HTML report with a dark-mode dashboard showing all results.

**Current state of the codebase:**
- Core engine is complete and working: field detection, AI value generation, form filling, multi-page handling, reporting
- New features being added via this CODEX_PROMPT.md: OTP field support, fill verification (checking values actually stuck), expanded error detection (alerts, aria-live, toast messages), required-fields-only mode, a new main_csv.py regeneration script, and a Gradio web UI

**Key known issues fixed in this prompt:**
1. Autocomplete dropdowns — after typing, field sometimes goes blank → fixed with better timing and ArrowDown+Enter fallback + post-fill value read-back
2. False positives — form submitted with blank fields Playwright "filled" but JS cleared → fixed with verify_fills() method that reads DOM values before submitting
3. OTP fields — tool tried to fill OTP automatically → fixed with 3+2 minute manual wait loop
4. Pass/Fail too lenient — only checked HTML5 validation, missed alerts, toasts, aria-live → fixed with expanded detect_errors() and alert capture
5. All fields filled even when only required matter → fixed with --required-only flag
6. No GUI → fixed with ui.py (Gradio)
7. No report regeneration script → fixed with main_csv.py
