"""Record the demo video: a scripted walkthrough of the real app.

Drives a real browser against a running server and records it. Nothing is
mocked or sped up -- the agent waits you see are the actual agents working.

    uvicorn backend.main:app --port 8000      # in another terminal
    python -m scripts.record_demo             # writes report/demo/demo.webm

On-screen captions carry the narration, since this produces no audio. Add a
voiceover afterwards if you want one; the pacing leaves room for it.

Warm the cache first by running the same search once, or the discovery step
will sit on a spinner for 30-50s and drag the video out.
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT_DIR = Path(__file__).resolve().parent.parent / "report" / "demo"
VIEWPORT = {"width": 1280, "height": 800}

# Caption overlay. Deliberately plain and high-contrast so it survives
# compression and stays readable at small sizes.
CAPTION_CSS = """
#demo-cap{
  position:fixed; left:0; right:0; bottom:0; z-index:99999;
  background:linear-gradient(transparent,rgba(0,37,74,.97) 32%);
  color:#fff; padding:52px 48px 30px;
  font:500 23px/1.4 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  letter-spacing:-.01em; opacity:0; transition:opacity .35s ease;
  pointer-events:none;
}
#demo-cap.on{opacity:1}
#demo-cap b{color:#8fc2ff; font-weight:700}
#demo-cap .sub{display:block; font-size:16px; opacity:.8; margin-top:6px;
  font-weight:400}
"""

INSTALL_CAPTION = """
(() => {
  if (document.getElementById('demo-cap')) return;
  const s = document.createElement('style');
  s.textContent = %s;
  document.head.appendChild(s);
  const d = document.createElement('div');
  d.id = 'demo-cap';
  document.body.appendChild(d);
})()
""" % repr(CAPTION_CSS)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("pip install playwright && playwright install chromium",
              file=sys.stderr)
        return 1

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    started = time.time()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(OUT_DIR),
            record_video_size=VIEWPORT,
        )
        page = context.new_page()
        page.set_default_timeout(240_000)

        def caption(text: str, sub: str = "", hold: float = 3.2) -> None:
            """Show a caption, hold it, then fade it out."""
            page.evaluate(INSTALL_CAPTION)
            body = text + (f'<span class="sub">{sub}</span>' if sub else "")
            page.evaluate(
                "([h]) => { const d=document.getElementById('demo-cap');"
                "d.innerHTML=h; d.classList.add('on'); }", [body]
            )
            page.wait_for_timeout(int(hold * 1000))

        def clear_caption(pause: float = 0.5) -> None:
            page.evaluate(
                "() => { const d=document.getElementById('demo-cap');"
                "if(d) d.classList.remove('on'); }"
            )
            page.wait_for_timeout(int(pause * 1000))

        def beat(seconds: float) -> None:
            page.wait_for_timeout(int(seconds * 1000))

        def smooth_scroll(to: int, steps: int = 26, pause: int = 42) -> None:
            page.evaluate(
                "([t,s,p]) => new Promise(r => {"
                " const start=window.scrollY, d=(t-start)/s; let i=0;"
                " const id=setInterval(()=>{window.scrollTo(0,start+d*(++i));"
                " if(i>=s){clearInterval(id);r();}},p);})",
                [to, steps, pause],
            )

        # ---------------- 1. the problem ----------------
        print("  [1/8] opening")
        page.goto(BASE_URL, wait_until="networkidle")
        beat(1.2)
        caption("Every used-car site tells you <b>which</b> car to buy.",
                "None of them tell you what owning it will cost.", 4.2)
        clear_caption()
        caption("An <b>$11,500</b> car costs <b>$34,100</b> to own for five years.",
                "Built on five free US government data sources. No API key for any of them.",
                4.6)
        clear_caption()

        # ---------------- 2. the funnel ----------------
        print("  [2/8] funnel")
        caption("Four questions.", "", 1.8)
        clear_caption(0.3)
        page.click('[data-body="sedan"]')
        beat(1.6)
        caption("Brands you'd consider.", "", 2.0)
        for slug in ("honda", "toyota", "mazda"):
            page.click(f'[data-brand="{slug}"]')
            beat(0.55)
        clear_caption(0.3)
        page.click("#brands-next")
        beat(1.1)

        caption("What matters to you.",
                "These become the ranking weights, set by the Profiler agent.", 2.8)
        for key in ("reliability", "running_cost"):
            page.click(f'[data-pri="{key}"]')
            beat(0.6)
        clear_caption(0.3)
        page.click("#priorities-next")
        beat(1.1)

        caption("Budget, and how you drive.", "", 2.2)
        page.fill("#budget-min", "10000"); beat(0.4)
        page.fill("#budget-max", "25000"); beat(0.4)
        page.fill("#year-min", "2016"); beat(0.7)
        clear_caption(0.3)

        # ---------------- 3. agents running ----------------
        print("  [3/8] discovery")
        page.click("#run-discovery")
        beat(1.0)
        caption("Python filters the catalogue first.",
                "The agents only ever see cars that already fit. That keeps tokens down "
                "and makes an out-of-budget recommendation impossible.", 5.0)
        clear_caption(0.3)
        page.wait_for_selector(".rescard", timeout=300_000)
        beat(1.2)

        # ---------------- 4. results ----------------
        print("  [4/8] results")
        caption("Ranked by <b>five-year cost</b>, not sticker price.", "", 3.4)
        clear_caption(0.3)
        smooth_scroll(520)
        beat(1.6)
        caption("Every card leads with what the car <b>costs you</b>.",
                "Fuel, depreciation, crash rating, and any defect flagged against its segment.",
                4.4)
        clear_caption(0.3)
        smooth_scroll(1180)
        beat(2.0)

        # ---------------- 5. the specific car ----------------
        print("  [5/8] dossier form")
        caption("Looking at one specific car? Paste the VIN and the asking price.", "", 3.4)
        clear_caption(0.3)
        page.click('[data-goto="lookup"]')
        beat(1.2)
        page.fill("#lk-year", "2018"); beat(0.4)
        page.select_option("#lk-make", "Honda"); page.wait_for_timeout(1600)
        page.select_option("#lk-model", "Accord"); beat(0.5)
        page.fill("#lk-mileage", "78000"); beat(0.4)
        page.fill("#lk-price", "11500"); beat(0.4)
        page.fill("#lk-vin", "1HGCV1F13JA000001"); beat(1.0)
        caption("The VIN gives the <b>exact</b> original MSRP, free, from NHTSA.", "", 3.0)
        clear_caption(0.3)
        page.click("#run-dossier")
        beat(0.8)
        caption("Three agents now: Dossier, Analyst, and a <b>Critic</b> that fact-checks them.",
                "", 4.0)
        clear_caption(0.3)
        page.wait_for_selector(".costtable", timeout=300_000)
        beat(1.2)

        # ---------------- 6. the cost table ----------------
        print("  [6/8] cost table")
        page.evaluate("document.querySelector('#dossier-out').scrollIntoView()")
        beat(1.4)
        caption("Where the money actually goes.",
                "Every line shows the exact basis it was computed from.", 4.2)
        clear_caption(0.3)
        page.evaluate("window.scrollBy(0,260)")
        beat(2.2)
        caption("<b>Maintenance alone exceeds the purchase price.</b>",
                "$12,877 against $11,500 paid. All of it computed in Python, never by the model.",
                5.0)
        clear_caption(0.3)
        page.evaluate("window.scrollBy(0,420)")
        beat(2.4)
        caption("Five trims of the same car. <b>$2,750</b> separates cheapest from dearest in fuel.",
                "That difference is invisible on every shopping site.", 4.8)
        clear_caption(0.3)

        # ---------------- 7. defects + critic ----------------
        print("  [7/8] defects and critic")
        page.evaluate("""() => {
            const s=[...document.querySelectorAll('#dossier-out .section')]
              .find(x=>/known problem/i.test(x.querySelector('h3').textContent));
            if(s) s.scrollIntoView();
        }""")
        beat(1.6)
        caption("Known problems &mdash; every one cited to an NHTSA campaign number.",
                "If a claim can't be cited, it never reaches this screen.", 4.8)
        clear_caption(0.3)
        page.evaluate("window.scrollBy(0,380)")
        beat(2.2)

        page.evaluate("""() => {
            const s=[...document.querySelectorAll('#dossier-out .section')]
              .find(x=>/fact check/i.test(x.querySelector('h3').textContent));
            if(s) s.scrollIntoView();
        }""")
        beat(1.4)
        caption("A separate agent re-checked every claim against the raw records.",
                "Tested against five deliberate falsehoods and one clean control: 6 of 6 correct.",
                5.2)
        clear_caption(0.3)

        # ---------------- 8. show your work ----------------
        print("  [8/8] methodology")
        page.evaluate("window.scrollTo(0,0)")
        beat(0.8)
        page.click("#nav-method")
        page.wait_for_selector("#method-body .srcrow", timeout=60_000)
        beat(1.4)
        caption("Every source and every assumption, shown to the user.",
                "Including the value model's 21% median error. Uncertainty is published, not buried.",
                5.0)
        clear_caption(0.3)
        page.evaluate("document.querySelector('.drawer-inner').scrollTop = 420")
        beat(2.6)

        caption("<b>UsedCarDossier</b>",
                "github.com/chanpaca/usedcardossier &middot; MGT 409, Yale School of Management",
                4.6)
        clear_caption(0.8)

        context.close()
        browser.close()

    videos = list(OUT_DIR.glob("*.webm"))
    if not videos:
        print("no video produced", file=sys.stderr)
        return 1
    final = OUT_DIR / "demo.webm"
    videos[0].rename(final)
    elapsed = time.time() - started
    print(f"\n  wrote {final}")
    print(f"  {final.stat().st_size / 1_000_000:.1f} MB, about {elapsed:.0f}s of footage")
    if not 100 <= elapsed <= 210:
        print(f"  ! target is 2-3 minutes; this run was {elapsed / 60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
