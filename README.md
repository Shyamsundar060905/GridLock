# 🚦 Gridlock 2.0 — Parking-Induced Congestion Intelligence

![Status](https://img.shields.io/badge/status-hackathon%20build-orange)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![LightGBM](https://img.shields.io/badge/LightGBM-tweedie-9ACD32)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)
![FastAPI](https://img.shields.io/badge/FastAPI-read--only-009688?logo=fastapi&logoColor=white)
![Mappls](https://img.shields.io/badge/Maps-Mappls%20SDK-EE2737)
![OpenStreetMap](https://img.shields.io/badge/features-OpenStreetMap-7EBC6F?logo=openstreetmap&logoColor=white)

> Detects illegal-parking **hotspots** in Bengaluru, estimates the **latent
> violation rate** (debiased for patrol coverage), scores each location's
> **Congestion Impact (CIS, 0–100)** with a plain-language explanation, and
> flags **enforcement blind spots** — so patrols target where it matters, not
> just where they already go.

---

## ⚡ Quick start (run the site)

The map already ships with precomputed data in `frontend/public/`, so you only
need the frontend to see the demo.

```bash
# 1. Map (required)
cd frontend
npm install                       # first time only
npm start                         # opens http://localhost:3000
```
> Paste a fresh **Mappls token** into `frontend/src/App.js` (`MAPPLS_TOKEN`) or
> the map loads blank. Tokens expire ~24h — regenerate at apis.mappls.com.

```bash
# 2. CIS API (optional — serves the score contract per hotspot)
cd api
pip install -r requirements.txt
uvicorn main:app --port 8000      # docs at http://localhost:8000/docs
```

```bash
# 3. Retrain / regenerate data (optional)
#    Open model/gridlock_colab.ipynb in Colab → Run all → download outputs/*.json
#    into frontend/public/.  Or locally:
python -m venv .venv && . .venv/Scripts/activate
pip install -r model/requirements.txt
cd model && GRIDLOCK_CSV="../<violations>.csv" python gridlock_pipeline.py
cp outputs/*.json ../frontend/public/
```

---

## The core problem with the data

The dataset (298,450 parking violations, 10 Nov 2023 – 8 Apr 2024) is a
**patrol log, not a violation census**. We only observe violations where an
officer happened to be. Naively ranking cells by violation count just
rediscovers patrol routes.

Two structural biases we correct for:

- **Temporal.** Timestamps are UTC. Converted to IST, violations cluster
  **03:00–12:00, peaking 10–11 AM**, and are near-zero 3 PM–10 PM. Enforcement
  is a *morning shift*; the afternoon/evening — when commercial parking demand
  peaks — is a blind spot. (The widely-quoted "53% midnight–6 AM" is an artifact
  of reading the raw UTC clock.)
- **Spatial.** 50.4% of records are camera/fixed-junction (`BTP*`, unbiased);
  the rest are mobile patrol (biased toward patrolled areas). We treat these as
  two different observational processes.

## How it works

```
violations ─▶ H3 cells + 3h slots ─▶ debiasing ─▶ OSM features ─▶ LightGBM ─▶ priority score
                                       │                                         │
                                       ├─ inverse-probability weighting          ├─ latent violation rate (debiased)
                                       ├─ fixed-junction anchor                  └─ × OSM congestion impact
                                       └─ device-ID negative sampling
```

**Debiasing**
- *Inverse-probability weighting* — estimate patrol intensity per (station,
  hour) from distinct active device-days; up-weight violations seen under light
  patrol.
- *Fixed-junction anchor* — BTP camera records are unbiased; the model sees
  `is_junction` and trusts those samples more.
- *Device-ID negative sampling* — reconstruct each officer's shift from
  `(device_id, date)`; cells adjacent to where they ticketed, with no violation,
  are *patrolled-and-clean* true negatives (distinct from "never observed").

**Features (OSM, fetched free in one bbox query)**
Road class / lanes / oneway / traffic signals (supply), POI counts —
commercial, transit, institutional (demand), metro proximity, plus spatial-lag
neighbour rates (ring-1/ring-2).

**Model** — LightGBM, Tweedie objective (handles zero-inflated counts).
Validated with **spatio-temporal CV**: forward-chaining by month × geographic
quadrant holdout, so we test generalization to under-patrolled zones rather than
re-learning patrol routes.

**Outputs**
- `priority_score` = normalized latent rate × OSM road-criticality (congestion impact).
- **Blind-spot detector** — high predicted impact **and** low observed patrol.
- **Recidivist clusters** — vehicles with ≥6 violations at a concentrated location (likely fleets/auto-stands).

## Repo structure
```
model/      ML pipeline + Colab notebook (the intelligence layer)
  gridlock_pipeline.py   source of truth — runs locally and in Colab
  gridlock_colab.ipynb   generated notebook — upload to Colab to train
  make_notebook.py       regenerates the .ipynb from the .py
api/        Thin read-only FastAPI service over the CIS scores (contract endpoint)
backend/    Legacy descriptive pipeline (Mappls live-traffic enrichment)
frontend/   React + Mappls map — priority hotspots, blind spots, recidivists, CIS
```

## Congestion Impact Score (CIS)
Each hotspot gets an explainable **0–100 CIS** and a Low/Medium/High/Critical
class, composed of four stored, weighted subscores:

`CIS = 100 × (0.30·VLS + 0.20·COS + 0.35·ECS + 0.15·RPS)`

- **VLS** (violation load) — the **debiased latent rate** × mean violation
  severity (vehicle type × offence code), so it doesn't re-inherit patrol bias.
- **COS** (carriageway obstruction) — parked-vehicle width × concurrency ÷ OSM
  road width.
- **ECS** (excess congestion) — live speed deficit vs baseline. No live feed in
  batch, so it uses an OSM demand×capacity proxy flagged `low_confidence`; a
  Mappls Flow feed drops in via the `ECSProvider` interface unchanged.
- **RPS** (recurrence) — days with a violation in the trailing 30.

The four weighted point-contributions are stored and rendered as the
explanation. A **Phase-2** trained classifier (LightGBM + SHAP + calibration)
swaps in behind the same contract once ≥3 months of measured-delay outcomes
exist — interface stubbed in the pipeline. Adapted to this dataset:
`duration_factor` is dropped (`closed_datetime` is 100% NULL) and ECS uses the
proxy described above.

Serve it: `cd api && pip install -r requirements.txt && uvicorn main:app --port 8000`
(`GET /hotspots/{id}` returns the contract; see `api/README.md`).

**Map features:** live traffic flow, raw violation heatmap, debiased/raw
before-after toggle, priority hotspots (red→amber by score), blind spots
(magenta ring), recidivist vehicles (blue), per-hotspot CIS breakdown, and a
ward "Enforcement Priorities" panel.

**Dev tips:** `GRIDLOCK_SAMPLE=0.05` for a fast local smoke test, `GRIDLOCK_OSM=0`
to skip OSM. The pipeline `.py` and the Colab notebook are the same code — edit
the `.py`, then `python model/make_notebook.py` to regenerate the notebook.

## ✅ Roadmap — done vs. left

**Done**
- [x] H3 indexing + UTC→IST temporal fix
- [x] Debiasing: IPW, fixed-junction anchor, device-ID negative sampling
- [x] OSM feature enrichment (roads, POIs, metro)
- [x] LightGBM latent-rate model + spatio-temporal CV + SHAP
- [x] Enforcement blind-spot detector
- [x] Recidivist-vehicle clustering
- [x] Congestion Impact Score (Phase-1 rule classifier) + explanations
- [x] Read-only CIS API (FastAPI)
- [x] React map: hotspots, blind spots, recidivists, CIS panel, before/after toggle

**Left**
- [ ] **Live Mappls Flow feed for ECS** — currently an OSM proxy flagged
      `low_confidence`; the `ECSProvider` interface is ready for it
- [ ] **8-week segment speed baseline** — needs continuous collection (can't be
      backfilled from the historical dataset)
- [ ] **Phase-2 trained CIS classifier** — needs ≥3 months of measured-delay
      outcome labels; interface stubbed (`Phase2Classifier`)
- [ ] **Persistence + scheduler** — today it's batch → static JSON; a DB +
      cron-style recompute would make it a true live service
- [ ] VIIRS night-light equity layer · STGCN ensemble · set-cover patrol routing
      (see below)

## Future work (deferred, by design)

- **VIIRS night-light socioeconomic layer.** Monthly VIIRS Day/Night Band
  composites (NASA Earthdata, free) rasterized over BBMP ward boundaries give a
  current-year economic-activity proxy — better than 2011 census income.
  Enables the **equity analysis**: are high-activity wards getting
  disproportionate enforcement vs low-income wards at equal road criticality?
  Deferred only because it needs Earthdata auth + raster handling; the join key
  (ward boundaries) is already wired.
- **STGCN ensemble.** A spatio-temporal graph conv net (H3 cells as nodes,
  adjacency as edges) to model congestion propagation between cells. Strictly
  more expressive than the tree model for spillover effects; best as a second
  ensemble member.
- **Shift-optimal patrol routing.** Given the next-6h hotspot predictions, solve
  a set-cover / max-coverage assignment of patrol units to cells under travel
  constraints — turning the heatmap into a one-click "plan my route" for an
  officer.
- **Weather & events features.** Hourly rainfall (free historical) and a
  holiday/event calendar to explain temporal variance.

## Data sources
- Violations CSV — organizer-provided (gitignored, not committed).
- Road network & POIs — OpenStreetMap via OSMnx/Overpass (free).
- BBMP ward boundaries — public DataMeet / Open City GeoJSON (`backend/BBMP.geojson`).
- Live traffic (optional map layer) — Mappls Web Maps SDK.
