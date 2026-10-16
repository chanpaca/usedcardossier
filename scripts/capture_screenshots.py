"""Drive the running app and capture the screenshots used in the report.

Offline tooling, not a runtime dependency: Playwright is only needed to
regenerate report/images/.

    pip install playwright && playwright install chromium
    uvicorn backend.main:app --port 8000      # in another terminal
    python -m scripts.capture_screenshots

Every shot is of the real app doing real work against live data. Nothing here
is mocked, which is the point -- the report's figures should be evidence, not
illustration.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT_DIR = Path(__file__).resolve().parent.parent / "report" / "images"
VIEWPORT = {"width": 1240, "height": 900}

# The vehicle the whole report walks through.
CASE = {"year": "2018", "make": "Honda", "model": "Accord",
        "mileage": "78000", "price": "11500", "vin": "1HGCV1F13JA000001"}


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("pip install playwright && playwright install chromium",
              file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
        page.set_default_timeout(180_000)

        def shot(name: str, selector: str | None = None) -> None:
            path = OUT_DIR / name
            if selector:
                page.locator(selector).first.screenshot(path=str(path))
            else:
                page.screenshot(path=str(path))
            print(f"  wrote {path.name}")

        print(f"opening {BASE_URL}")
        page.goto(BASE_URL, wait_until="networkidle")

        # --- 1. the funnel -------------------------------------------------
        shot("01-funnel.png")

        # --- walk the funnel ------------------------------------------------
        page.click('[data-body="sedan"]')
        page.wait_for_timeout(300)
        for slug in ("honda", "toyota", "mazda"):
            page.click(f'[data-brand="{slug}"]')
        page.click("#brands-next")
        page.wait_for_timeout(250)
        for key in ("reliability", "running_cost"):
            page.click(f'[data-pri="{key}"]')
        page.click("#priorities-next")
        page.wait_for_timeout(250)
        page.fill("#budget-min", "10000")
        page.fill("#budget-max", "25000")
        page.fill("#year-min", "2016")

        print("running discovery (live APIs + 2 agents, please wait)")
        started = time.time()
        page.click("#run-discovery")
        page.wait_for_selector(".rescard", timeout=300_000)
        page.wait_for_timeout(600)
        print(f"  discovery returned in {time.time() - started:.1f}s")

        # --- 2. the ranked shortlist ----------------------------------------
        page.evaluate("window.scrollTo(0,0)")
        shot("02-results.png")

        # --- dossier for the walkthrough vehicle ----------------------------
        page.click('[data-goto="lookup"]')
        page.wait_for_timeout(300)
        page.fill("#lk-year", CASE["year"])
        page.select_option("#lk-make", CASE["make"])
        page.wait_for_timeout(1500)
        page.select_option("#lk-model", CASE["model"])
        page.fill("#lk-mileage", CASE["mileage"])
        page.fill("#lk-price", CASE["price"])
        page.fill("#lk-vin", CASE["vin"])

        print("running dossier (3 agents incl. the Critic)")
        started = time.time()
        page.click("#run-dossier")
        page.wait_for_selector(".costtable", timeout=300_000)
        page.wait_for_timeout(800)
        print(f"  dossier returned in {time.time() - started:.1f}s")

        # --- 3-5. the dossier sections --------------------------------------
        sections = page.locator("#dossier-out .section")
        page.evaluate("document.querySelector('#dossier-out').scrollIntoView()")
        page.wait_for_timeout(300)
        shot("03-cost-table.png", "#dossier-out .card")

        count = sections.count()
        for i in range(count):
            heading = sections.nth(i).locator("h3").first.inner_text().lower()
            if "known problem" in heading or "recall" in heading:
                sections.nth(i).scroll_into_view_if_needed()
                page.wait_for_timeout(200)
                shot("04-defects.png", f"#dossier-out .section >> nth={i}")
                break
        for i in range(count):
            heading = sections.nth(i).locator("h3").first.inner_text().lower()
            if "fact check" in heading:
                sections.nth(i).scroll_into_view_if_needed()
                page.wait_for_timeout(200)
                shot("05-critic.png", f"#dossier-out .section >> nth={i}")
                break

        # --- 7. methodology drawer ------------------------------------------
        page.click("#nav-method")
        page.wait_for_selector("#method-body .srcrow", timeout=60_000)
        page.wait_for_timeout(400)
        shot("07-methodology.png")

        browser.close()

    print(f"\nscreenshots in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
