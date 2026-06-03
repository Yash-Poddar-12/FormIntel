# FormIntel — Complete Project Documentation

## 1. Project Overview

FormIntel is an AI-assisted web form validation tester. It opens a target form in Playwright, detects fields from the live DOM, generates or imports test values, fills the form, submits or advances it, and records the browser, page, alert, and network evidence needed to decide whether each test passed or failed.

The tool is intended for QA engineers, product teams, and developers who need repeatable validation coverage for public or internal forms, especially financial and lending forms where formats such as Indian mobile numbers, PAN, dates, and loan references matter.

FormIntel has two execution modes. AI generation mode runs with `python main.py --url X`; it detects the form, asks the configured AI provider for valid baseline values, converges until a passing submission is found, then runs invalid variations one field at a time. Data-file mode runs with `python main.py --url X --data-file data.csv`; it reads user-supplied CSV rows, matches CSV columns to detected fields, fills those values, and optionally compares actual outcomes with an `expected_result` column.

The N+1 testing strategy means FormIntel first finds one valid baseline test case, then creates one additional test case per invalid variation while keeping every other field at the passing baseline. This isolates the changed field so a failure can be attributed to that specific input rather than to unrelated form state.

## 2. Architecture Diagram (text-based)

```text
CLI (main.py)
  -> TestRunner
     -> FieldDetector
     -> AIEngine
     -> FormFiller
     -> MultiPageHandler
  -> ReportGenerator
```

## 3. File-by-File Reference

### config.py

Purpose: Loads `.env` settings and exposes immutable runtime configuration through the `Settings` dataclass. It also chooses the active AI provider using OpenAI first, Gemini second, and rule-based fallback when no key is present.

Key functions and classes: `_to_bool()` converts permissive string values into booleans so `.env` flags are easy to use. `_to_int()` safely parses integer settings with defaults. `Settings` centralizes config values so the runner, AI engine, and browser setup receive one stable object.

Known limitations: Provider selection is decided at import time. Changing `.env` after import will not affect the current process.

Dependencies: `os`, `dataclasses`, `pathlib`, `python-dotenv`.

### main.py

Purpose: Command-line entrypoint for fresh test runs. It parses flags, starts Playwright, chooses AI generation or data-file mode, and writes reports.

Key functions: `_slug_from_url()` creates a filesystem-safe report folder name from the target URL. `main()` handles CLI parsing, config override for `--required-only`, browser lifecycle, runner invocation, and report generation.

Known limitations: It uses one browser page per run. If the form requires authentication or a pre-filled session, that setup must already be handled outside this entrypoint.

Dependencies: `argparse`, `datetime`, `urllib.parse`, `playwright`, `Settings`, `TestRunner`, `ReportGenerator`.

### main_csv.py

Purpose: Regenerates HTML and CSV reports from an existing JSON report without rerunning browser tests.

Key function: `main()` validates the JSON path, selects an output directory, and calls `ReportGenerator.regenerate_html_from_json()`.

Known limitations: It does not change test results; it only rebuilds report views from previously saved JSON.

Dependencies: `argparse`, `pathlib`, `ReportGenerator`.

### ui.py

Purpose: Provides a Gradio web UI for running FormIntel without the CLI.

Key classes and functions: `LogCapture` buffers stdout safely across UI updates. `_slug_from_url()` mirrors CLI slug behavior. `run_formIntel()` streams logs, runs Playwright, calls `TestRunner.run()`, and exposes the HTML report. `build_ui()` defines the Gradio controls and output panes.

Known limitations: The UI currently runs AI generation mode only; it does not expose `--data-file`.

Dependencies: `gradio`, `playwright`, `Settings`, `TestRunner`, `ReportGenerator`.

### field_detector.py

Purpose: Detects form fields and OR groups from the live DOM. It returns normalized dictionaries with field index, type, label, selector, options, requirement status, constraints, and skip flags.

Key class and methods: `FieldDetector` owns the JavaScript detection scripts. `detect()` executes `_SCRIPT`, validates the result, and normalizes fields for Python. `detect_or_groups()` executes `_OR_GROUP_SCRIPT` to identify visual "OR" separators and map fields before and after those separators.

Design decisions: Detection runs inside the browser because labels, visibility, generated IDs, and React-rendered controls are only reliable in the live DOM. Unique selectors are built from ID, name, attributes, classes, then nth-child fallback. React Select combobox inputs are now marked as `react-select` so the filler can use container-based dropdown handling.

Known limitations: Complex custom widgets without input, select, textarea, contenteditable, or combobox nodes may not be detected. OR group detection relies on visible text and geometry, so unusual layouts can confuse it.

Dependencies: `playwright.sync_api.Page`, `typing.Any`.

### ai_engine.py

Purpose: Generates baseline values, invalid variations, and optional page-error analysis through OpenAI, Gemini, or deterministic fallbacks.

Key class and methods: `AIEngine.__init__()` selects and initializes the provider. `_init_openai()` and `_init_gemini()` prepare provider clients. `generate_baseline_values()` returns valid values for every detected active field. `generate_single_field_variation()` returns one valid alternate or invalid value. `generate_field_invalid_variations()` returns multiple invalid cases and falls back on any provider or parse error. `analyze_page_errors()` maps page text back to fields. `_call_ai()`, `_call_openai()`, and `_call_gemini()` route provider calls. `_safe_json_loads()` strips fences, extracts embedded JSON, and now repairs trailing commas and single-quoted JSON-like output. `_fallback_baseline_values()`, `_fallback_single_baseline()`, `_fallback_multiple_variations()`, `_fallback_variation()`, and `_coerce_value_for_field()` keep the tool usable without an API key.

Design decisions: AI is used because field labels and financial-domain formats vary widely across forms. Coercion protects select, checkbox, number, and date fields from unusable AI output.

Known limitations: AI providers may rate-limit, return malformed JSON, or generate invalid values that a field silently rejects before submission.

Dependencies: `json`, `re`, `time`, `datetime.date`, `jinja2.Template`, optional `openai`, optional `google.generativeai`.

### form_filler.py

Purpose: Fills detected fields, handles special widgets, waits for manual OTP entry, and verifies whether values stuck in the DOM.

Key class and methods: `FormFiller.__init__()` stores OTP wait settings. `fill_all()` dispatches by field type and returns browser validation messages. `verify_fills()` reads actual DOM values after filling to catch silent rejection. `_wait_for_otp()` pauses for manual OTP entry. `_dismiss_overlays()` closes open dropdowns or date pickers before the next fill. `_fill_react_select()` opens a React Select container, types the value, selects an option, and verifies displayed selection. `_fill_tags_autocomplete()` fills tags such as subjects one term at a time. `_handle_autocomplete_dropdown()` handles generic dropdowns after normal text entry.

Design decisions: `verify_fills()` exists because tests can otherwise look successful even when a browser-controlled input rejected the value. React Select and tags inputs need dedicated paths because their visible control is often not the raw input element.

Known limitations: Some custom widgets require application-specific option text or asynchronous waits beyond the generic selectors.

Dependencies: `time`, `re`, `playwright.sync_api.Page`.

### multi_page_handler.py

Purpose: Clicks submit, next, continue, proceed, verify, or confirm controls and classifies whether the form advanced or submitted. It also detects page errors, success states, current page number, and relevant network responses.

Key class and methods: `MultiPageHandler.capture_network_response()` captures likely API responses for a timeout window. `click_submit_or_next()` installs dialog and network listeners, clicks the best available control, and returns click classification, alerts, and responses. `detect_success()` scans text and form disappearance. `detect_errors()` collects CSS, ARIA, toast, and small visible error messages. `get_current_page_number()` reads step/page indicators. `_count_visible_field_elements()` helps classify page advancement. `_start_network_capture()` and `_remove_response_listener()` manage response listeners.

Known limitations: Button classification is heuristic. Forms with unusual navigation controls may need custom selectors.

Dependencies: `re`, `playwright.sync_api.Page`.

### test_runner.py

Purpose: Orchestrates baseline convergence, N+1 variation testing, CSV/data-file execution, multi-page traversal, OR-group behavior, status decisions, and CSV field matching.

Key class and methods: `TestRunner.run()` performs AI generation mode. `run_with_data()` performs CSV data-file mode. `_traverse_multi_page_form()` fills and advances multi-page forms, accumulating values and evidence across pages. `_run_or_alt_baselines()` tests the alternate side of OR groups. `_values_for_traversal_page()` chooses page values from AI or overrides. `_value_for_field_from_override()` resolves override values by index or label. `_merge_page_dict()` namespaces later-page evidence as `page_N.index`. `_page_one_values()` extracts baseline values usable for field variations. `_determine_status()` decides PASS, FAIL, or ERROR from alerts, success text, validation messages, page errors, fill verification, and network responses. `_apply_or_side_to_values()` blanks the unused side of OR groups. `_or_indices_by_side()` returns OR-side indexes. `_match_csv_fields()`, `_field_label_candidates()`, `_normalize_label()`, and `_match_score()` map CSV columns to detected fields. `_scan_network_status()`, `_find_json_error()`, and `_find_json_success()` inspect captured API payloads.

Design decisions: Multi-page traversal accumulates values and evidence across pages because validation may happen only after the final submit. OR group handling intentionally blanks one side of alternatives to avoid overfilling mutually exclusive fields.

Known limitations: If a later page requires fields with no CSV override and no AI engine is available, traversal can stop with no values for that page.

Dependencies: `csv`, `json`, `AIEngine`, `Settings`, `FieldDetector`, `FormFiller`, `MultiPageHandler`.

### report_generator.py

Purpose: Converts runner results into CSV, JSON, and HTML reports.

Key class and methods: `ReportGenerator.generate()` creates output paths and writes all report formats. `_generate_csv()` writes a compact tabular summary. `_generate_json()` writes the canonical structured result payload. `_generate_html_from_json()` renders a searchable expandable HTML report. `regenerate_html_from_json()` rebuilds HTML from saved JSON. `_to_json_string()` serializes values for display. `_html_template()` stores the report UI template.

Known limitations: The HTML report is static and reads data baked into the generated file; it does not refresh from live test state.

Dependencies: `csv`, `json`, `datetime`, `pathlib`, `jinja2.Template`.

### check_models.py

Purpose: Developer utility that lists available OpenAI GPT model IDs for the configured API key.

Key behavior: Loads `.env`, creates an OpenAI client, fetches models, filters IDs containing `gpt`, sorts them, and prints them.

Known limitations: It requires network access and an OpenAI key. It is not part of the main test run.

Dependencies: `os`, `openai.OpenAI`, `python-dotenv`.

## 4. Data Flow

1. User runs `python main.py --url X`.
2. `main()` loads `Settings`, applies `--required-only` if present, creates a timestamped report folder, and launches Chromium through Playwright.
3. `TestRunner.run()` creates `AIEngine`, `FieldDetector`, `FormFiller`, and `MultiPageHandler`.
4. Phase 1 navigates to the URL, waits for inputs, detects all fields, detects OR groups, filters active fields, and asks `AIEngine.generate_baseline_values()` for page-one baseline data.
5. `_traverse_multi_page_form()` detects fields on each page, obtains values from AI or overrides, fills with `FormFiller.fill_all()`, verifies with `verify_fills()`, captures validation evidence, clicks next or submit through `MultiPageHandler`, and accumulates values, alerts, errors, network responses, and final success.
6. `_determine_status()` classifies the baseline. If it passes, the runner stores page-one baseline values and starts Phase 2. If it fails, convergence repeats up to `MAX_CONVERGENCE_ITERATIONS`.
7. Phase 2 loops over fields and invalid variations from `AIEngine.generate_field_invalid_variations()`. Each test starts from the passing baseline, changes one field, traverses the form, and records the result.
8. `main()` receives the result list and calls `ReportGenerator.generate()`.
9. The reporter writes CSV, JSON, and HTML files under the run folder.

## 5. The Two Testing Modes

### AI Generation Mode (`python main.py --url X`)

Phase 1 baseline convergence searches for a valid submission. Each iteration redetects the form, generates valid-looking values, applies OR group blanking when needed, traverses all pages, and classifies the outcome. A passing baseline becomes the control case for later tests.

Phase 2 N+1 variations keep the passing baseline fixed and replace one field value at a time with invalid cases such as empty, wrong format, boundary invalid, too short, too long, special characters, or wrong type. This makes each failure easier to attribute.

### Data File Mode (`python main.py --url X --data-file data.csv`)

Data-file mode reads CSV rows with optional `description` and `expected_result` columns plus form-field columns. It detects page fields, matches CSV headers to field labels, names, and IDs using exact, substring, and token-overlap scores, fills non-empty values, traverses multi-page forms, and compares actual status to `expected_result` when provided.

## 6. Key Design Decisions

- Playwright over Selenium: Playwright has strong auto-waiting, modern browser control, dialog handling, response capture, and reliable selectors for dynamic applications.
- AI-generated values over hardcoded fixtures: Forms vary by domain and label wording, so AI can adapt to labels, options, and financial data formats.
- `verify_fills()` exists because browsers and custom widgets can silently reject a value before submit; without readback, false positives are easy.
- OR group detection exists because alternative identifiers should not both be filled when the form expects one side of an "OR" group.
- Multi-page traversal accumulates across pages because later screens reuse detector indexes; namespacing preserves page-one compatibility and keeps later-page evidence.
- Network responses are captured because validation may happen client-side, server-side, or through an API that never renders a visible error.

## 7. Configuration Reference

| Variable | Type | Default | Controls |
| --- | --- | --- | --- |
| `GEMINI_API_KEY` | string | empty | Enables Gemini when OpenAI is absent. |
| `OPENAI_API_KEY` | string | empty | Enables OpenAI as first-choice provider. |
| `OPENAI_MODEL` | string | `gpt-4.1-mini` | OpenAI chat model used for generation. |
| `PLAYWRIGHT_HEADLESS` | bool | `false` | Whether browser runs without UI unless CLI overrides. |
| `PLAYWRIGHT_SLOW_MO` | int | `700` | Milliseconds of Playwright slow motion. |
| `DEFAULT_TIMEOUT` | int | `30000` | Default Playwright action timeout in milliseconds. |
| `REQUIRED_ONLY` | bool | `false` | Fill and test only required fields. |
| `OTP_WAIT_SECONDS` | int | `180` | Initial manual OTP wait window. |
| `OTP_EXTRA_SECONDS` | int | `120` | Extra OTP wait window after the initial timeout. |

Internal constants: `GEMINI_MODEL_NAME` defaults to `gemini-2.5-flash`, `SYSTEM_PROMPT` defines QA data-generation behavior, and `MAX_CONVERGENCE_ITERATIONS` is `5`.

## 8. CSV Data File Format

CSV files are header-driven. Supported special columns are `description` and `expected_result`. Every other non-empty column is treated as a candidate form field value and matched against detected field label, name, or ID.

Empty cells are skipped, so they do not clear or fill a matched field. `expected_result` should be `PASS`, `FAIL`, or `ERROR`; comparison is case-insensitive after uppercasing. Example:

```csv
description,Mobile Number,PAN,Date of Birth,expected_result
valid applicant,9876543210,ABCDE1234F,1995-06-15,PASS
bad PAN,9876543210,ABC@E1234F,1995-06-15,FAIL
```

## 9. Report Format

The JSON report contains `meta` with totals, pass/fail/error counts, and pass rate, plus `results` with each test case. Result rows include test name, description, changed field, changed value, variation type, status, reason, page errors, validation messages, alerts, network responses, fill verification, all field values, OR groups, final URL, and page number. Data-file rows may also include `expected`, `match`, `input_values`, and `matched_fields`.

The CSV report contains a compact summary with test number, test name, changed field, variation type, changed value, status, optional expected/match columns, reason, errors, validation messages, final URL, and page number.

The HTML report provides summary cards, status filters, search, expandable detail rows, all field values, browser validation messages, page errors, alert text, fill verification, and network response snippets.

## 10. Known Issues and Limitations

- React Select dropdowns are now handled by opening the parent container, typing into the active input, selecting the first option, and checking displayed selected text.
- Tags/autocomplete fields are now handled one term at a time by typing the first three characters and selecting a matching option.
- `input[type=number]` rejecting text variations is expected behavior. If a value does not stick, FormIntel can mark the invalid test as PASS because the browser rejected the bad input before submit.
- OTP fields require manual entry and can time out if the user does not provide the code.
- Forms requiring authentication before the form appears need the browser session prepared before FormIntel reaches the form.
- AI providers can hit rate limits, quota limits, malformed JSON responses, or provider outages; rule-based fallbacks reduce but do not remove this risk.

## 11. Production Readiness Checklist

- Confirm permission to test the target production form.
- Run in required-only mode first to limit submission volume.
- Use safe fake data only; never submit real PAN, Aadhaar, account, or customer information.
- Verify the form's terms, rate limits, CAPTCHA, OTP, and authentication requirements.
- Confirm whether submissions create real applications, leads, tickets, or financial records.
- Use a staging endpoint when possible.
- Configure API keys and quotas for the expected run size.
- Review generated reports for false positives from silent field rejection.
- Check network response capture for sensitive data before sharing reports.
- Validate CSV expected results with product or QA owners.
- Keep Playwright visible for initial production dry runs.
- Archive reports according to the organization's data retention policy.
