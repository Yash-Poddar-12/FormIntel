"""CLI entrypoint for smart_form_tester."""

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
    """
    Convert a URL into a short readable slug for folder/file naming.
    Examples:
      https://myaccount.bajajhousingfinance.in/#/tracker  → bajajhousingfinance
      http://localhost:8000/tests/mock_form.html           → localhost_mock_form
      https://demoqa.com/automation-practice-form         → demoqa_automation-practice-form
    """
    parsed = urlparse(url)

    # Extract domain — remove www. prefix
    domain = parsed.netloc.replace("www.", "")
    # Keep only the main domain name (before first dot for short sites,
    # or second-level domain for longer ones)
    domain_parts = domain.split(".")
    if len(domain_parts) >= 2:
        # e.g. bajajhousingfinance.in → bajajhousingfinance
        domain_slug = domain_parts[-2]
    else:
        domain_slug = domain_parts[0]

    # Extract last meaningful path segment
    path = parsed.path.strip("/")
    if not path:
        # Try fragment (hash routes like /#/tracker/tracker-home)
        path = parsed.fragment.strip("/")
    path_parts = [p for p in path.split("/") if p]
    path_slug = path_parts[-1] if path_parts else ""

    # Combine domain + path slug
    if path_slug and path_slug != domain_slug:
        slug = f"{domain_slug}_{path_slug}"
    else:
        slug = domain_slug

    # Sanitize — only allow alphanumeric, dash, underscore
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")

    return slug[:40]  # Cap at 40 chars


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--headless", action="store_true", default=False)
    parser.add_argument("--output-dir", default="reports")
    args = parser.parse_args()

    config = Settings()
    headless = args.headless or config.playwright_headless
    results = []

    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    site_slug = _slug_from_url(args.url)
    run_folder = f"{args.output_dir}/{site_slug}__{run_timestamp}"

    print(f"[Main] Target : {args.url}")
    print(f"[Main] Run ID : {site_slug}__{run_timestamp}")
    print(f"[Main] Reports: {run_folder}/")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            slow_mo=config.playwright_slow_mo,
        )
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
        reporter.generate(
            results,
            output_dir=run_folder,
            timestamp=run_timestamp,
            site_slug=site_slug,
        )
        print(f"[Main] All reports saved to: {run_folder}/")
        print(f"[Main] Total test cases recorded: {len(results)}")
    else:
        print("[Main] No results to report.")


if __name__ == "__main__":
    main()