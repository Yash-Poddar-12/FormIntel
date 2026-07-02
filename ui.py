"""Gradio web UI for FormIntel.

Run:  python ui.py
Then open: http://localhost:7860
"""

from __future__ import annotations

import dataclasses
import re
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import gradio as gr
from playwright.sync_api import sync_playwright

from config import Settings
from report_generator import ReportGenerator
from test_runner import TestRunner


def _slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")
    parts = domain.split(".")
    domain_slug = parts[-2] if len(parts) >= 2 else parts[0]
    path = parsed.path.strip("/") or parsed.fragment.strip("/")
    path_parts = [p for p in path.split("/") if p]
    path_slug = path_parts[-1] if path_parts else ""
    slug = f"{domain_slug}_{path_slug}" if path_slug and path_slug != domain_slug else domain_slug
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:40]


class LogCapture:
    """
    Thread-safe log buffer for streaming to Gradio.

    FIX: The old approach did `sys.stdout = log` globally, which is a process-wide
    mutation. If two browser tabs/users triggered a run at the same time, their
    print() calls would interleave into whichever LogCapture object happened to be
    sys.stdout at that instant — corrupting both users' logs.

    This version is used with `contextlib.redirect_stdout(log)` inside a `with`
    block scoped to a single request's worker thread. redirect_stdout still swaps
    the global sys.stdout under the hood (Python has only one stdout per process),
    so this does NOT make concurrent runs fully isolated — true isolation would
    require routing print() through a per-request logger instead of stdout redirection.
    What it DOES fix: the swap is now scoped (always restored via context manager
    even on exception) instead of a manual assignment that could leak across runs
    if an exception skipped the restore. For genuine multi-user concurrency, queue
    Gradio requests (demo.queue()) so only one run's stdout redirect is active at a time.
    """
    def __init__(self):
        self._lines: list[str] = []

    def write(self, text: str):
        self._lines.append(text)

    def flush(self):
        pass

    def getvalue(self) -> str:
        return "".join(self._lines)


def run_formIntel(
    url: str,
    headless: bool,
    required_only: bool,
    output_dir: str,
    slow_mo: int,
    progress=gr.Progress(track_tqdm=False),
):
    if not url.strip():
        yield "❌ Please enter a URL.", None
        return

    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    log = LogCapture()
    html_report_path = None
    results = []

    # FIX: scoped redirect via context manager instead of manual sys.stdout swap.
    # Guarantees stdout is restored even if an exception escapes the try/finally below.
    with redirect_stdout(log):
        try:
            config = Settings()
            if required_only:
                config = dataclasses.replace(config, required_only=True)

            # FIX: slow_mo from the UI slider was previously ignored — config always
            # used the .env value regardless of what the user set in the slider.
            config = dataclasses.replace(config, playwright_slow_mo=int(slow_mo))

            run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            site_slug = _slug_from_url(url)
            run_folder = f"{output_dir.strip() or 'reports'}/{site_slug}__{run_timestamp}"

            print(f"[UI] Starting FormIntel for: {url}")
            print(f"[UI] Required-only: {required_only} | Headless: {headless} | Slow-mo: {config.playwright_slow_mo}ms")
            print(f"[UI] Reports will be saved to: {run_folder}/")
            yield log.getvalue(), None

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless, slow_mo=config.playwright_slow_mo)
                context = browser.new_context()
                page = context.new_page()
                page.set_default_timeout(config.default_timeout)

                try:
                    runner = TestRunner()
                    results = runner.run(url, page, config)
                    yield log.getvalue(), None
                except Exception as exc:
                    print(f"[UI] Test run interrupted: {exc}")
                    yield log.getvalue(), None
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
                print(f"\n[UI] ✅ Done! {len(results)} test cases recorded.")
                print(f"[UI] Reports saved to: {run_folder}/")

                html_files = list(Path(run_folder).glob("*.html"))
                if html_files:
                    html_report_path = str(html_files[0])
                    print(f"[UI] HTML report: {html_report_path}")
            else:
                print("[UI] ⚠️ No results to report. The page may have had no detectable fields.")

        except Exception as exc:
            print(f"[UI] Fatal error: {exc}")

    yield log.getvalue(), html_report_path


def build_ui():
    with gr.Blocks(title="FormIntel") as demo:
        gr.Markdown("# 🧪 FormIntel\n**AI-powered web form validation tester**", elem_id="title")

        with gr.Row():
            with gr.Column(scale=3):
                url_input = gr.Textbox(
                    label="Form URL",
                    placeholder="https://yoursite.com/apply",
                    lines=1,
                )
            with gr.Column(scale=1):
                run_btn = gr.Button("▶ Run Tests", variant="primary", size="lg")

        with gr.Row():
            with gr.Column():
                headless_cb = gr.Checkbox(label="Headless (no browser window)", value=False)
                required_only_cb = gr.Checkbox(label="Required fields only", value=False)
            with gr.Column():
                output_dir_input = gr.Textbox(label="Output directory", value="reports", lines=1)
                slow_mo_slider = gr.Slider(
                    label="Browser slow-mo (ms)", minimum=0, maximum=3000, step=100, value=700
                )

        with gr.Row():
            log_output = gr.Textbox(
                label="Live Log",
                lines=20,
                max_lines=40,
                interactive=False,
                placeholder="Logs will appear here when you click Run Tests...",
            )

        with gr.Row():
            file_output = gr.File(label="Download HTML Report", visible=True)

        gr.Markdown(
            "**Tips:** Set `GEMINI_API_KEY` or `OPENAI_API_KEY` in your `.env` file before running. "
            "Use *Required fields only* for forms with many optional fields. "
            "The slow-mo slider now actually controls browser speed."
        )

        run_btn.click(
            fn=run_formIntel,
            inputs=[url_input, headless_cb, required_only_cb, output_dir_input, slow_mo_slider],
            outputs=[log_output, file_output],
        )

    return demo


if __name__ == "__main__":
    ui = build_ui()
    # NOTE: .queue() serializes requests so concurrent runs don't share a single
    # redirected sys.stdout at the same time (see LogCapture docstring above).
    # Without this, two simultaneous users could still interleave log output.
    ui.queue()
    ui.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        theme=gr.themes.Base(primary_hue="blue", neutral_hue="slate"),
    )