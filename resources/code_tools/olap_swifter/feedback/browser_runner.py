"""
Playwright Automated Browser Evaluator and Field-Switching Feedback Runner.
Validates the interactive model reviewer UI, tests field-switching interactions,
and captures visual verification snapshots.
"""

import asyncio
from pathlib import Path
from typing import Dict, Any, Optional


class PlaywrightFeedbackRunner:
    """Automates headless browser testing of the OLAP model reviewer UI."""

    def __init__(self, html_path: str = "model_reviewer.html", screenshot_path: str = "model_verification_screenshot.png"):
        self.html_path = Path(html_path).resolve()
        self.screenshot_path = Path(screenshot_path).resolve()

    async def run_verification(self, field_to_switch: Optional[str] = None, new_target_table: Optional[str] = None) -> Dict[str, Any]:
        """
        Runs headless Playwright to inspect the UI, test field-switching,
        and capture visual evidence.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {
                "success": False,
                "error": "Playwright is not installed in the current Python environment."
            }

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1280, "height": 800})

                # Navigate to the local file URL
                file_url = f"file://{self.html_path}"
                await page.goto(file_url)

                # 1. Verify header and status badge
                title = await page.inner_text("h1.title")
                status_text = await page.inner_text("#overall-status")

                # 2. Count rendered fields to ensure NO variable was left out
                rows = await page.query_selector_all("#fields-body tr")
                field_count = len(rows)

                # 3. Simulate interactive field switching if requested
                switched = False
                if field_to_switch and new_target_table:
                    select_selector = f"#select-tbl-{field_to_switch}"
                    select_elem = await page.query_selector(select_selector)
                    if select_elem:
                        await page.select_option(select_selector, new_target_table)
                        # Click Save button
                        await page.click("#btn-save-model")
                        # Wait for confirmation text
                        await page.wait_for_selector("#save-status", state="visible", timeout=2000)
                        switched = True

                # 4. Check join graph text
                max_joins_text = await page.inner_text("#max-joins")

                # 5. Capture visual verification snapshot
                await page.screenshot(path=str(self.screenshot_path), full_page=True)
                await browser.close()

                return {
                    "success": True,
                    "title": title,
                    "status_badge": status_text,
                    "field_count_rendered": field_count,
                    "field_switched": switched,
                    "max_joins": max_joins_text,
                    "screenshot_saved": str(self.screenshot_path)
                }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "hint": "If Chromium browser is missing, run: playwright install chromium"
            }

    def run_sync(self, field_to_switch: Optional[str] = None, new_target_table: Optional[str] = None) -> Dict[str, Any]:
        """Synchronous wrapper around async verification."""
        return asyncio.run(self.run_verification(field_to_switch, new_target_table))
