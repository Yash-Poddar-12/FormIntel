#!/usr/bin/env bash
set -e

echo "Creating virtual environment..."
python -m venv venv

echo "Activating virtual environment..."
if [ -f "venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
else
  echo "Failed to find venv/bin/activate"
  exit 1
fi

echo "Installing dependencies..."
python -m pip install --upgrade pip
pip install -r requirements.txt

echo "Installing Playwright Chromium browser..."
playwright install chromium

echo "Creating .env file..."
cat > .env << 'EOF'
GEMINI_API_KEY=your_key_here
PLAYWRIGHT_HEADLESS=false
PLAYWRIGHT_SLOW_MO=700
DEFAULT_TIMEOUT=30000
EOF

echo "Setup complete. Add your GEMINI_API_KEY to .env then run:"
echo 'python main.py --url "http://localhost:8000/tests/mock_form.html"'
