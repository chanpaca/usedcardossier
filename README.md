# UsedCarDossier

**What a used car will actually cost you, and what is actually wrong with it.**

Every used-car site tells you *which* car to buy. None tell you what owning it
costs. An $11,500 Honda Accord costs **$34,100** to own for five years -- and
$2,750 separates the cheapest and dearest trim of that same model year, in
fuel alone.

This app answers the question buyers actually have, using five free US
government data sources and no API key for any of them.

---

## Run it

Requires Python 3.10 or newer.

```bash
pip install -r requirements.txt
cp .env.example .env        # then put your key in it
uvicorn backend.main:app --reload
```

Open <http://127.0.0.1:8000>.

That is the whole setup. There is **no build step** -- no npm, no bundler, no
compile. FastAPI serves the API and the plain HTML/CSS/JS frontend from one
process.

**Measured from a clean clone: 59 seconds** to a working server (52s pip install,
6s to a healthy response), then 11.7s for a full dossier. Tested on Windows 11
with Python 3.14 into a fresh virtual environment.

### The one key you need

Put **one** of these in `.env`:

```
PORTKEY_API_KEY=...        # course gateway; model is pinned to gpt-6-luna
# or
OPENAI_API_KEY=...         # personal key
```

The app looks for `.env` in **two** places, in order:

1. this repo folder -- `./.env`
2. the parent folder -- `../.env`

The first one found wins, so a key you already keep one level up will be
picked up automatically.

**No key for vehicle data.** NHTSA and fueleconomy.gov are free and keyless.
Without an LLM key the app still runs: the funnel, the cost engine and the
defect analysis all work, you just do not get the written explanations.

### Check it is working

```bash
curl http://127.0.0.1:8000/api/health
```

Shows which `.env` was loaded, whether the LLM is configured, catalog size and
cache state.

---

## What it does

**Discovery.** Body style, then brands, then priorities, then budget. You get a
ranked shortlist with a five-year cost on each car.

**Dossier.** Pick one car -- or paste a VIN and the asking price of a listing
you are looking at -- and get the trim-by-trim breakdown, the known defects
with NHTSA citations, and where every dollar goes.

---

## How it is built

### Python decides and calculates. Agents explain and check.

This is the central design decision. Every dollar is computed in
`backend/tco.py` by ordinary arithmetic. Every defect claim comes from an NHTSA
record. The agents receive those results and turn them into prose; they cannot
change a number. A language model that is never asked to do arithmetic cannot
get the arithmetic wrong.

### Five agents

| Agent | Does | Can it change a number? |
|---|---|---|
| **Profiler** | Turns your stated priorities into ranking weights; flags contradictions | No |
| **Scout** | Explains why each shortlisted car suits you | No -- ranking is done in Python |
| **Dossier** | Explains what separates the trims and what is known to break | No |
| **Analyst** | Makes the cost table legible | No -- explicitly forbidden from arithmetic |
| **Critic** | Re-checks every claim against the raw source data | Removes uncitable claims |

The **Critic** is the safety mechanism. It sees both the prose and the source
data it was supposed to come from, and fails any defect claim without an NHTSA
campaign number or complaint count, any dollar figure that does not match the
computed table verbatim, and any spec that contradicts the EPA record.
High-severity findings are applied before you see the output; all findings are
shown either way.

### Data sources -- all free, all keyless, all US

| Source | Supplies |
|---|---|
| NHTSA vPIC | Original MSRP (`Base Price ($)`), trim, horsepower, body class, from a VIN |
| NHTSA Recalls | Safety campaigns, cited by campaign number |
| NHTSA Complaints | Owner complaints by component -- the defect signal |
| NHTSA NCAP | Crash-test star ratings |
| EPA fueleconomy.gov | Every tested trim, MPG, and annual fuel cost in dollars |
| AAA *Your Driving Costs 2025* | Maintenance, insurance and registration basis |

---

## Two things worth reading before you trust the output

### 1. Prices are modelled, and the model is wide

No free, redistributable corpus of US used-car asking prices exists. Edmunds
closed its public API in 2018, CarMax has none, and the free dataset samples we
checked paywall exactly the price column.

So value is modelled: original MSRP from the VIN decoder, multiplied by a
retention curve fitted to 581 real listings over age, mileage and body style.

**R² = 0.60, median absolute error ≈ 21%.**

That error is published in the UI, not buried. It is why the app shows a price
*band* rather than a number, and why it asks for the actual asking price of the
car you are looking at. Our job is to judge a price, not to guess one.

### 2. Defect risk is measured by component *share*, not complaint count

Raw complaint counts measure popularity as much as quality. Honda sold far more
Accords than Buick sold Regals, so the Accord will always show more complaints.

The first version of this app compared counts against a peer-group median. That
failed, because the peers have wildly different sales volumes -- the median just
tracked whichever models sold in modest numbers. The 2018 Accord hit the maximum
risk penalty **for being popular**, while the Kia Optima looked clean.

The fix is **component share**: what fraction of a car's own complaints concern
a given system, against the same fraction across its segment. Both numerator and
denominator describe the same car, so volume cancels.

The result inverts, and checks out against reality:

```
2018 midsize sedans, ENGINE share   (segment baseline 15.0%)
  Honda Accord    13.2%   0.88x   below average
  Toyota Camry     4.7%   0.31x
  Hyundai Sonata  31.8%   2.12x
  Kia Optima      37.5%   2.50x
```

The method surfaces the Hyundai/Kia **Theta II engine-failure** episode -- a
real, documented, large-scale defect -- from public complaint data alone,
without being told to look for it.

**Limit:** this detects a model unusual among its contemporaries. It cannot
detect a defect shared by an entire segment.

---

## Layout

```
backend/
  config.py          dual-location .env loading, model pinning
  catalog.py         hard filter: body style, brand, year, budget
  pricing.py         MSRP resolution (VIN -> make/model -> body)
  depreciation.py    fitted retention curve, returns a band
  tco.py             five-year cost. Deterministic. No LLM.
  risk.py            component-share defect scoring
  llm.py             LLM client with per-call token accounting
  pipeline.py        orchestration for both flows
  main.py            FastAPI app + static frontend
  agents/
    prompts.py       the five system prompts
    team.py          Profiler, Scout, deterministic ranking
    dossier.py       Dossier, Analyst, Critic
  tools/
    http_cache.py    SQLite response cache
    fueleconomy.py   EPA client
    nhtsa.py         recalls, complaints, NCAP
    vpic.py          VIN decode -> original MSRP
frontend/            plain HTML/CSS/JS, no build step
data/
  catalog.json             models the app can recommend
  depreciation_params.json fitted curve coefficients
  msrp_reference.json      aggregate medians only
  cache/                   seeded HTTP cache
scripts/
  seed_catalog.py        rebuild the catalog from the live APIs
  catalog_from_cache.py  rebuild it offline from the shipped cache (seconds)
  fit_depreciation.py    refit the retention curve (offline, needs pandas)
  measure_tokenomics.py  measure real token cost -> data/tokenomics.json
  safety_tests.py        adversarial tests against the Critic
  capture_screenshots.py regenerate report/images from a running app
report/
  report.html        the written report (editable source)
  slides.html        reveal.js deck (editable source)
  dist/              SELF-CONTAINED copies -- these are the ones to submit
  images/            screenshots, all from real runs
  demo/demo.mp4      2:05 screen recording of the real app
```

### Demo video

`report/demo/demo.mp4` -- 2 minutes 5 seconds, H.264, no audio. A scripted walkthrough
of the real app against live data: the funnel, the ranked shortlist, a full dossier with
the cost table and cited defects, the Critic's fact-check panel, and the methodology
drawer. On-screen captions carry the narration. Regenerate with:

```bash
python -m scripts.record_demo        # needs the server running
```

### Rebuilding the catalog

The repo ships with `data/catalog.json` and a seeded HTTP cache, so a fresh
clone works immediately and offline. To rebuild from the live APIs:

```bash
python -m scripts.seed_catalog
```

Takes several minutes and makes a few thousand cached requests.

---

## Scope and honest limits

- **US market only.** Every data source is a US government dataset.
- **20 brands**, covering the mainstream used market. Exotics, defunct marques
  and low-volume EV startups are excluded: their parts and service economics
  make this a different analysis.
- **$10,000-$50,000.**
- **Comfort is not scored.** No free public dataset measures it, and inventing a
  proxy would be inventing a measurement. It is held neutral and the UI says so.
- **Insurance is a national average** scaled by vehicle value. Your actual
  premium depends on your state, age and record, and will differ.
- **This is not an appraisal.** Always get a pre-purchase inspection.

---

Built by **Chanseo Park** (Green Cohort) for Yale SOM MGT 409, *AI Foundations for Managers*.
