@echo off
setlocal

echo Creating virtual environment...
python -m venv venv
if errorlevel 1 (
  echo Failed to create virtual environment.
  exit /b 1
)

echo Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
  echo Failed to activate virtual environment.
  exit /b 1
)

echo Installing dependencies...
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install requirements.
  exit /b 1
)

echo Installing Playwright Chromium browser...
playwright install chromium
if errorlevel 1 (
  echo Failed to install Playwright Chromium.
  exit /b 1
)

echo Creating .env file...
(
  echo GEMINI_API_KEY=your_key_here
  echo OPENAI_API_KEY=
  echo OPENAI_MODEL=gpt-4.1-mini
  echo PLAYWRIGHT_HEADLESS=false
  echo PLAYWRIGHT_SLOW_MO=700
  echo DEFAULT_TIMEOUT=30000
  echo REQUIRED_ONLY=false
  echo OTP_WAIT_SECONDS=180
  echo OTP_EXTRA_SECONDS=120
) > .env

echo Setup complete. Add your GEMINI_API_KEY to .env then run:
echo python main.py --url "http://localhost:8000/tests/mock_form.html"

endlocal
