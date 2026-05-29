# Implementation Plan: Smart Form Tester

Build a complete, production-ready Python project called `smart_form_tester` using Playwright and the Google Gemini API (gemini-2.5-flash) for AI-assisted form testing. The goal is to detect form fields, generate intelligent baseline valid values, execute form validation variations (N+1 testing strategy), handle multi-page/pagination steps, and produce rich, beautiful HTML and CSV reports.

## Proposed Project Structure

```
smart_form_tester/
├── main.py                  # CLI Entry point & orchestration
├── config.py                # Configuration settings, API keys, constants
├── field_detector.py        # Playwright-based DOM introspection for inputs
├── ai_engine.py             # Google Gemini API wrapper for values & error analysis
├── form_filler.py           # Playwright-based interaction with all field types
├── test_runner.py           # Implementation of N+1 testing & baseline convergence loop
├── report_generator.py      # Generation of HTML & CSV test run reports
├── multi_page_handler.py    # Pagination & multi-step form detection & navigation
└── requirements.txt         # Python dependencies
```

---

## Detailed Components

### 1. Configuration (`config.py`)
Responsible for reading environment variables and providing settings for Playwright and the Gemini API.
- **Environment Variables**:
  - `GEMINI_API_KEY`: API key for accessing Google Gemini.
  - `PLAYWRIGHT_HEADLESS`: Boolean (`True`/`False`) indicating whether browser should run headless.
  - `PLAYWRIGHT_SLOW_MO`: Milliseconds to slow down actions (e.g., 500ms) to simulate human typing and observe visual state.
  - `DEFAULT_TIMEOUT`: Playwright navigation and locator timeout (e.g., 30000ms).
- **Gemini Model**: `gemini-2.5-flash`.
- **System Prompt**: Set system instruction as requested:
  > You are a QA test data generator. Generate realistic, format-correct values for form fields. For financial/banking forms use Indian formats. Always return pure JSON, no explanation. Use response_mime_type='application/json'. For valid values: use real-looking but fake data (no real PAN/Aadhaar). For invalid values: use values that violate the field's expected format.

### 2. Field Detection (`field_detector.py`)
This module scans the DOM of the active page to detect form fields and collect metadata for each field.
- **Supported Fields**:
  - `text`, `email`, `tel`, `number`, `password` (standard inputs)
  - `date`, `datetime-local`, `time`, `month` (date/time inputs)
  - `select/dropdown` (single and multi-select)
  - `radio` buttons (grouped by their `name` attribute or fieldset)
  - `checkbox` (individual and groups)
  - `range/slider` inputs (extracting `min`, `max`, `step`)
  - `textarea`
  - `file` inputs (skipped, flagged in metadata)
  - `contenteditable` divs (with `contenteditable="true"`)
- **Metadata Extraction**:
  Each field is parsed into a dictionary:
  ```json
  {
    "index": int,
    "type": "text|email|select|radio|checkbox|range|date|textarea|file|contenteditable|...",
    "label": "string",
    "name": "string",
    "id": "string",
    "required": bool,
    "min": "value" or null,
    "max": "value" or null,
    "pattern": "regex" or null,
    "options": ["list", "of", "option", "values"],
    "selector": "CSS selector"
  }
  ```
- **Robust Selector Generation**: Use an in-browser JavaScript query executed via Playwright to generate unique CSS selectors for each input (e.g., using IDs, name attributes, specific attribute combos, or fallback hierarchical paths like `form > div:nth-child(2) > input`).

### 3. AI Engine (`ai_engine.py`)
Integrates the `google-generativeai` SDK.
- **Methods**:
  - `generate_baseline_values(fields)`: Submits the list of fields to Gemini. Receives a JSON mapping field indexes to valid values.
  - `generate_single_field_variation(field, current_value, variation_type)`: variation_type is `"valid_alternate"` or `"invalid"`. Returns a single modified value.
  - `analyze_page_errors(page_text, fields)`: Analyzes page text (especially visual error elements and HTML5 `.validationMessage`) to identify fields that caused submission failures, returning a mapping of field indexes to specific error messages.

### 4. Form Filler (`form_filler.py`)
Fills inputs dynamically. Wait for each element to be visible and enabled before interactively filling.
- **Type-specific filling**:
  - `text`, `email`, `tel`, `number`, `password`: `page.locator(selector).fill(value)`
  - `date`/`time` variants: Direct setting via `page.evaluate("(sel, val) => { const el = document.querySelector(sel); el.value = val; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }", selector, value)`
  - `select`: `page.locator(selector).select_option(value)`
  - `radio`: Click the specific radio element matching the option value.
  - `checkbox`: Check/uncheck based on boolean value.
  - `range`/`slider`: Evaluate to set `.value` directly and fire input/change events to trigger JS listeners.
  - `textarea`: `page.locator(selector).fill(value)`
  - `contenteditable`: Locate div, focus, clear text, and type/fill the value.
  - `file`: Skip, but print warning in logs.
- **Browser-level Validation Message Capture**:
  After filling a field, wait 500ms and run `element.validationMessage` in browser context. Capture any native validation errors immediately.

### 5. Multi-Page Handler (`multi_page_handler.py`)
Handles multi-step/paginated forms.
- Detects the presence of "Next", "Continue", or step-based buttons.
- Fills fields visible on the current page, clicks "Next", and loops until the final submit button is active.
- Keeps track of fields across multiple steps.

### 6. Test Runner (`test_runner.py`)
Implements the core test orchestration.
- **Baseline Convergence Loop**:
  1. Detect fields.
  2. Call `generate_baseline_values` to get initial valid set.
  3. Fill form and submit.
  4. Capture outcome (submission success or validation failure).
  5. If validation fails:
     - Run `analyze_page_errors(page_text, fields)`.
     - Request updated baseline values from AI, passing the errors.
     - Repeat (up to `max_iterations`, default 5) until form accepts/passes.
- **N+1 Variation Testing**:
  Once a passing baseline is established:
  - Run 1 Baseline Test Case.
  - Run N Variation Test Cases:
    - For each field, change *only* that field to an invalid value (from `generate_single_field_variation`).
    - Fill all other fields with their passing baseline values.
    - Submit, capture Pass/Fail outcome.
    - (Optionally run a valid alternate test case to ensure changes in valid formats are accepted).

### 7. Report Generator (`report_generator.py`)
Collects all test run records and generates outputs:
- **CSV Report**: Columns: `Test Name`, `Field Changed`, `Changed Value`, `Outcome (PASS/FAIL)`, `Validation Message / Page Error`.
- **HTML Report**: A highly premium visual report:
  - Interactivity: Expandable test case details.
  - Metrics dashboard: Pass rate, run time, total iterations, field count.
  - Sleek design system: Sleek dark/light modes, vibrant status badges, responsive layout, monospace JSON diffs showing baseline vs variation values.

### 8. Entry Point (`main.py`)
- CLI interface accepting a URL and configuration overrides.
- Sets up Playwright instance.
- Directs flow: Pagination -> Field Detection -> Baseline Convergence -> Variations -> Reporting.

---

## User Review Required

> [!IMPORTANT]
> - **Google Gemini Key**: An active Gemini API key is required. Make sure to define it in your environment (`GEMINI_API_KEY`) or in a `.env` file within the folder before running the CLI.
> - **Playwright Installation**: Playwright requires installing browser binaries. We will automate this by adding it to setup commands.

## Open Questions

> [!NOTE]
> 1. **Submission Indicators**: How should the runner detect a successful form submission? We will use a combination of:
>    - URL changes.
>    - Page content modifications (disappearance of form, success messages like "Thank you", "Submitted successfully", "Ref: #").
>    - Lack of red error text/visual labels on the page.
> 2. **Radio/Checkbox Grouping**: If radio buttons have the same name, we treat them as a single field with options. Is this acceptable? (Highly recommended, as it maps directly to standard QA data generation).

---

## Verification Plan

### Automated Tests
1. **Local Test Form**: Create a mock HTML form (`tests/mock_form.html`) containing:
   - All standard HTML5 inputs.
   - Custom styled radio groups, checkbox groups, contenteditable divs, and sliders.
   - Multi-step pagination (Step 1 -> Step 2 -> Submit).
   - Server-side and client-side validations (regex check, mandatory flags).
2. **Run Command**:
   - `python smart_form_tester/main.py tests/mock_form.html --headless`
   - Verify that the convergence loop successfully resolves errors.
   - Verify that the variation tests run and yield expected HTML/CSV reports.

### Manual Verification
- Deploy a demo form locally and launch the CLI without headless mode to visually monitor Playwright's actions, typing speed, and validation checking.
