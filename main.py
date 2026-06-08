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
    parser.add_argument("--data-file", default=None, help="Path to a CSV file with test data rows")
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
    print(f"[Main] Mode: {'DATA FILE' if args.data_file else 'AI GENERATION'}")
    print(f"[Main] Required-only : {config.required_only}")
    print(f"[Main] Reports       : {run_folder}/")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=config.playwright_slow_mo)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(config.default_timeout)

        try:
            runner = TestRunner()
            if args.data_file:
                results = runner.run_with_data(args.url, page, config, args.data_file)
            else:
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
