# FormIntel

**AI-assisted web form validation testing using Playwright and Google Gemini.**

FormIntel automatically detects all input fields on any web form, generates intelligent test data using AI, executes a structured N+1 validation testing strategy, and produces detailed HTML, JSON, and CSV reports — with zero manual test case writing.

---

## Table of Contents

- [Overview](#overview)
- [How It Works](#how-it-works)
- [Features](#features)
- [Project Structure](#project-structure)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Understanding the Reports](#understanding-the-reports)
- [Understanding Test Results](#understanding-test-results)
- [Supported Field Types](#supported-field-types)
- [Rate Limits and Fallback](#rate-limits-and-fallback)
- [Troubleshooting](#troubleshooting)

---

## Overview

Manual form testing is slow, repetitive, and prone to missed edge cases. FormIntel solves this by combining browser automation with AI-generated test data to systematically validate every field on a form — including boundary conditions, format violations, and missing required values.

It works on any publicly accessible URL, including SPAs (Single Page Applications built with Angular, React, Vue), multi-step forms, and complex financial or banking portals.

---

## How It Works

The tool follows a two-phase testing strategy:

### Phase 1 — Baseline Convergence

1. Opens the target URL in a real Chromium browser via Playwright
2. Scans the DOM to detect all input fields and their metadata
3. Sends field metadata to Google Gemini, which generates a set of realistic, format-correct valid values
4. Fills all fields with those values and submits the form
5. If the form has validation errors, analyzes the errors and retries with improved values
6. Repeats up to 5 times until a clean passing baseline is established

### Phase 2 — N+1 Variation Testing

Once a passing baseline exists, for each field:

1. Keeps all other fields at their passing baseline values
2. Replaces only that field with an AI-generated invalid value
3. Submits the form and records whether validation correctly rejected it
4. Repeats for every variation type relevant to that field (empty, wrong format, boundary values, special characters, etc.)

This means for a 4-field form with 5 variations per field, the tool automatically runs 21+ test cases.

---

## Features

- **Universal field detection** — text, email, tel, number, date, range/slider, select, radio groups, checkboxes, textarea, contenteditable divs
- **AI-generated test data** — Gemini generates realistic Indian-format fake data for financial/banking forms
- **Multi-variation testing per field** — not just one invalid value, but empty, wrong format, boundary invalid, too short, too long, special characters, wrong type, future date, starts with wrong character
- **SPA support** — waits for dynamic content to render before detecting fields
- **Automatic retry with rate limit handling** — respects Gemini API rate limits, waits exact retry delay, falls back to rule-based generation on daily quota exhaustion
- **Rule-based fallback** — works without AI if quota is exhausted or API key is missing
- **Multi-page form support** — detects Next/Continue buttons and handles paginated forms
- **Timestamped run folders** — every run saves to its own subfolder under `reports/`
- **Three report formats** — HTML (interactive), JSON (machine-readable), CSV (Excel-ready)
- **Interactive HTML report** — search, filter by PASS/FAIL/ERROR, click rows to expand full field values

---

## Project Structure

```
FormIntel/
│
├── main.py                  # CLI entry point
├── config.py                # Environment variable loading and settings
├── field_detector.py        # DOM introspection — detects all field types
├── ai_engine.py             # Google Gemini API integration and fallbacks
├── form_filler.py           # Fills every field type using Playwright
├── test_runner.py           # Orchestrates Phase 1 and Phase 2 testing
├── report_generator.py      # Generates HTML, JSON, and CSV reports
├── multi_page_handler.py    # Handles submit/next clicks and success detection
│
├── tests/
│   └── mock_form.html       # Local test form covering all field types
│
├── reports/                 # Auto-created — one subfolder per run
│   └── run_YYYYMMDD_HHMMSS/
│       ├── report_YYYYMMDD_HHMMSS.html
│       ├── report_YYYYMMDD_HHMMSS.json
│       └── report_YYYYMMDD_HHMMSS.csv
│
├── requirements.txt
├── setup.bat                # Windows setup script
├── setup.sh                 # Linux/Mac setup script
├── .env                     # Your API key (never commit this)
├── .gitignore
└── README.md
```

---

## Requirements

| Requirement           | Version                                 |
| --------------------- | --------------------------------------- |
| Python                | 3.9 or higher                           |
| Google Gemini API Key | Free tier (20 req/day) or paid          |
| Operating System      | Windows, macOS, Linux                   |
| Browser               | Chromium (auto-installed by Playwright) |

---

## Installation

### Option A — Automated Setup (Recommended)

**Windows:**

```bat
setup.bat
```

**macOS / Linux:**

```bash
bash setup.sh
```

This creates a virtual environment, installs all dependencies, installs the Chromium browser, and creates a `.env` file.

---

### Option B — Manual Setup

**Step 1 — Create and activate virtual environment:**

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

**Step 2 — Install dependencies:**

```bash
pip install -r requirements.txt
```

**Step 3 — Install Playwright browser:**

```bash
playwright install chromium
```

**Step 4 — Create `.env` file** in the project root:

```
GEMINI_API_KEY=your_api_key_here
PLAYWRIGHT_HEADLESS=false
PLAYWRIGHT_SLOW_MO=700
DEFAULT_TIMEOUT=30000
```

---

## Configuration

All settings are controlled via the `.env` file:

| Variable              | Default      | Description                                                                      |
| --------------------- | ------------ | -------------------------------------------------------------------------------- |
| `GEMINI_API_KEY`      | _(required)_ | Your Google Gemini API key from [Google AI Studio](https://aistudio.google.com/) |
| `PLAYWRIGHT_HEADLESS` | `false`      | Set to `true` to run browser invisibly in background                             |
| `PLAYWRIGHT_SLOW_MO`  | `700`        | Milliseconds between each browser action. Increase if the site is slow           |
| `DEFAULT_TIMEOUT`     | `30000`      | Maximum wait time in milliseconds for page elements                              |

Getting a Gemini API Key:

1. Go to [https://aistudio.google.com/](https://aistudio.google.com/)
2. Sign in with a Google account
3. Click **Get API Key** → **Create API Key**
4. Copy the key and paste it into your `.env` file

---

## Usage

### Test Against the Included Mock Form

Open two terminals in the project folder:

**Terminal 1 — Start local server:**

```bash
python -m http.server 8000
```

**Terminal 2 — Run the tester:**

```bash
python main.py --url "http://localhost:8000/tests/mock_form.html"
```

---

### Test Against Any Real URL

```bash
python main.py --url "https://yoursite.com/apply"
```

Run headless (no visible browser window):

```bash
python main.py --url "https://yoursite.com/apply" --headless
```

Save reports to a specific folder:

```bash
python main.py --url "https://yoursite.com/apply" --output-dir "my_reports"
```

---

### Regenerate HTML Report from Existing JSON

If you want to rebuild the HTML report from a saved JSON file without re-running tests:

```python
from report_generator import ReportGenerator
ReportGenerator().regenerate_html_from_json("reports/run_20250523_120000/report_20250523_120000.json")
```

---

### CLI Arguments

| Argument       | Required | Default   | Description                   |
| -------------- | -------- | --------- | ----------------------------- |
| `--url`        | Yes      | —         | Full URL of the form to test  |
| `--headless`   | No       | `false`   | Run browser without UI        |
| `--output-dir` | No       | `reports` | Root folder for report output |

---

## Understanding the Reports

Every run creates a timestamped subfolder:

```
reports/
└── run_20250529_153702/
    ├── report_20250529_153702.html   ← Open in browser
    ├── report_20250529_153702.json   ← Machine-readable full data
    └── report_20250529_153702.csv    ← Open in Excel
```

### HTML Report

Open the `.html` file in any browser. Features:

- **Summary cards** — Total tests, PASS count, FAIL count, ERROR count, Pass Rate
- **Search bar** — Filter by test name, field name, or value
- **Filter buttons** — Show All / PASS only / FAIL only / ERROR only
- **Variation type tag** — Shows what kind of invalid value was tested (e.g. `empty`, `wrong_format`, `boundary_invalid`)
- **Expandable rows** — Click any row to see all field values used in that test case and full browser validation messages

### JSON Report

Human-readable structured data. Useful for CI/CD integration or custom analysis:

```json
{
  "meta": {
    "generated_at": "20250529_153702",
    "total_tests": 33,
    "pass_count": 5,
    "fail_count": 28,
    "error_count": 0,
    "pass_rate_percent": 15.15
  },
  "results": [
    {
      "test_number": 1,
      "test_name": "BASELINE_ITER_4",
      "changed_field": null,
      "variation_type": "baseline",
      "changed_value": null,
      "status": "PASS",
      "pass_reason": "Values accepted client-side (no browser or element errors)",
      "all_field_values": {
        "0": "9876543210",
        "1": "LN1234567890",
        "2": "1990-05-15",
        "3": "ABCPA1234Z"
      },
      ...
    }
  ]
}
```

### CSV Report

Columns: `Test Number`, `Test Name`, `Changed Field`, `Variation Type`, `Changed Value`, `Status`, `Pass/Fail Reason`, `Page Errors`, `Validation Messages`, `Final URL`, `Page Number`

Designed to be opened directly in Microsoft Excel or Google Sheets for sharing with stakeholders.

---

## Understanding Test Results

### Status Definitions

| Status    | Meaning                                                                                                             |
| --------- | ------------------------------------------------------------------------------------------------------------------- |
| **PASS**  | The form accepted the input — either server confirmed success, or no client-side validation errors fired            |
| **FAIL**  | The form rejected the input — browser validation message appeared, or visible error elements were found on the page |
| **ERROR** | The test itself encountered a technical problem (browser crash, timeout, etc.)                                      |

### What a PASS on an Invalid Value Means

When an invalid value gets a PASS status, that is **not an error in the tool** — it is a **genuine finding**. It means the form did not validate that input correctly.

Examples of real findings discovered:

- Mobile number field accepts 11+ digits (no maximum length validation)
- Date of birth field accepts future dates (no date range validation)
- Loan reference field accepts any length string (no length constraints)

These are validation gaps that should be reported to the development team.

### Baseline Convergence Iterations

If the baseline takes multiple iterations, it means the AI initially generated values that failed format validation. The tool automatically retries with corrected values. This is expected behavior, especially for fields with strict regex patterns like PAN numbers or Indian mobile numbers.

---

## Supported Field Types

| Field Type            | Detection | Filling                     |
| --------------------- | --------- | --------------------------- |
| `text`                | ✓         | ✓                           |
| `email`               | ✓         | ✓                           |
| `tel`                 | ✓         | ✓                           |
| `number`              | ✓         | ✓                           |
| `password`            | ✓         | ✓                           |
| `date`                | ✓         | ✓ (via JS setter)           |
| `datetime-local`      | ✓         | ✓ (via JS setter)           |
| `time`                | ✓         | ✓ (via JS setter)           |
| `month`               | ✓         | ✓ (via JS setter)           |
| `range` / slider      | ✓         | ✓ (via JS event dispatch)   |
| `select` dropdown     | ✓         | ✓                           |
| `select` multi        | ✓         | ✓                           |
| Radio group           | ✓         | ✓                           |
| Checkbox (single)     | ✓         | ✓                           |
| Checkbox group        | ✓         | ✓                           |
| `textarea`            | ✓         | ✓                           |
| `contenteditable` div | ✓         | ✓                           |
| `file`                | ✓         | Skipped (flagged in report) |
| `hidden`              | Ignored   | Ignored                     |

---

## Rate Limits and Fallback

### Google Gemini Free Tier Limits

| Limit               | Value |
| ------------------- | ----- |
| Requests per minute | 5     |
| Requests per day    | 20    |

### How the Tool Handles Limits

- **Per-minute limit**: Automatically waits the exact number of seconds specified in the API error response, then retries up to 3 times
- **Daily limit**: Immediately stops retrying and switches to rule-based fallback for the rest of the run
- **No API key**: Falls back to rule-based generation for all test data

### Rule-Based Fallback Values

When AI is unavailable, the tool uses intelligent rule-based defaults:

| Field Pattern                   | Fallback Value               |
| ------------------------------- | ---------------------------- |
| Mobile / Phone                  | `9876543210`                 |
| Email                           | `test@example.com`           |
| Date                            | Today's date                 |
| PAN-like (label contains "pan") | Rule-based uppercase pattern |
| Number with min/max             | Midpoint of range            |
| Select / Radio                  | First available option       |
| Password                        | `Pass@1234`                  |
| Generic text                    | `TestValue`                  |

To remove daily limits, upgrade to a paid Gemini API plan at [https://ai.dev/rate-limit](https://ai.dev/rate-limit).

---

## Troubleshooting

### `[Iteration 1] Detected 0 fields`

The page is likely a JavaScript-heavy SPA that needs more time to render. Try increasing `PLAYWRIGHT_SLOW_MO` in your `.env` to `1500` and `DEFAULT_TIMEOUT` to `60000`.

### `[Config] API Key loaded: NO`

Your `.env` file is either missing, in the wrong folder, or has quotes around the key. It must look exactly like:

```
GEMINI_API_KEY=AIzaSyXXXXXXXXXXXXXXX
```

No quotes. No spaces around `=`. File must be in the same folder as `main.py`.

### `ModuleNotFoundError`

Your virtual environment is not activated. Run:

```bash
# Windows PowerShell
.\venv\Scripts\Activate.ps1

# macOS / Linux
source venv/bin/activate
```

### `playwright._impl._errors.TargetClosedError`

The browser closed unexpectedly. This usually happens when the machine is under heavy load or the site triggers a security redirect. The tool will save any partial results before exiting.

### Form fills but nothing submits

The submit button may have an unusual label. The tool looks for buttons with text: `submit`, `next`, `continue`, `proceed`, `verify`, `confirm`. If your form uses different text, the fallback clicks the first visible enabled button on the page.

### Daily quota exhausted mid-run

This is expected on the free tier (20 requests/day). The tool detects this immediately, switches to rule-based fallback, and completes the run without crashing. Upgrade to a paid plan or wait until the next day for AI-generated values.

---

## License

This project was developed as an internal QA automation tool. All test data generated is fake and does not represent real individuals or accounts.
