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
