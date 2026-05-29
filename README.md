# smart_form_tester

`smart_form_tester` is an AI-assisted Playwright form testing tool.
It detects fields, generates test values with Gemini, runs validation scenarios, and produces reports.

## Requirements

- Python 3.9+
- Gemini API key

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:
   `pip install -r requirements.txt`
3. Install Playwright Chromium:
   `playwright install chromium`
4. Create `.env`:
   - `GEMINI_API_KEY=your_key_here`
   - `PLAYWRIGHT_HEADLESS=false`
   - `PLAYWRIGHT_SLOW_MO=700`
   - `DEFAULT_TIMEOUT=30000`

## Run Against Mock Form

1. Start local server (in one terminal):
   `python -m http.server 8000`
2. Run tester (in another terminal):
   `python main.py --url "http://localhost:8000/tests/mock_form.html"`

## Run Against Any Real URL

`python main.py --url "https://yoursite.com/form" --headless`

## Output

The tool generates both HTML and CSV reports in the current folder.
