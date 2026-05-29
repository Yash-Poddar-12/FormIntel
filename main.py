"""CLI entrypoint for FormIntel."""

from __future__ import annotations

import argparse
from datetime import datetime

from playwright.sync_api import sync_playwright

from config import Settings
from report_generator import ReportGenerator
from test_runner import TestRunner


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--headless", action="store_true", default=False)
    parser.add_argument("--output-dir", default="reports")
    args = parser.parse_args()

    config = Settings()
    headless = args.headless or config.playwright_headless
    results = []

    # Each run gets its own timestamped subfolder
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_folder = f"{args.output_dir}/run_{run_timestamp}"

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
        reporter.generate(results, output_dir=run_folder, timestamp=run_timestamp)
        print(f"[Main] All reports saved to: {run_folder}/")
        print(f"[Main] Total test cases recorded: {len(results)}")
    else:
        print("[Main] No results to report.")


if __name__ == "__main__":
    main()