# Weather board

One page comparing model guidance against live Kalshi prices for the daily
**rain** and **high temperature** city markets, today and tomorrow, ranked by
expected value after fees.

Static site on GitHub Pages. A scheduled Action does all the fetching and
commits JSON into `docs/data/`; the page just reads it. Nothing in the browser
talks to NOMADS or Kalshi — CORS blocks most of it and you cannot decode GRIB
in a tab.

---

# Deploying to GitHub Pages

Nine steps, start to finish.

### 1. Create the repo

```bash
cd kalshi-weather-board
git init
git add -A
git commit -m "initial"
git branch -M main
git remote add origin https://github.com/YOURNAME/YOURREPO.git
git push -u origin main
```

A **public** repo gets Pages on the free tier. Private repos need Pro or
higher. Nothing here contains secrets — the meteoblue key lives in Actions
secrets, never in the repo.

### 2. Turn on Pages

Repo **Settings → Pages** → Source: **GitHub Actions**.

That's it — `.github/workflows/deploy-pages.yml` uploads `docs/` as an
artifact and serves it verbatim.

The alternative, "Deploy from a branch" with folder `/docs`, also works but
has a trap: its folder picker offers `/` and `/docs` whether or not those
directories exist, and it pipes everything through Jekyll unless it finds
`docs/.nojekyll`. If the site folder ends up nested one level down — easy to
do if you push the wrapper directory the zip unpacks into — you get a Ruby
stack trace about a theme stylesheet you never wrote, ending in
`No such file or directory @ dir_chdir0`. The Actions route fails with a
readable message instead.

Your site appears at `https://YOURNAME.github.io/YOURREPO/` within a minute or
two. It will already render, using the sample data described in step 6.

`docs/.nojekyll` is in the repo on purpose — without it Pages runs the files
through Jekyll, which silently drops paths beginning with an underscore.

### 3. Let the Action write back

Repo **Settings → Actions → General → Workflow permissions**:

- Select **Read and write permissions**
- Save

The workflow commits `docs/data/*.json` to `main` on every run. Without this it
fails at the push step with a 403. The workflow already declares
`permissions: contents: write`, but the repo-level default has to allow it.

### 4. Enable Actions

Repo **Actions** tab → **I understand my workflows, go ahead and enable them**.
GitHub disables workflows by default on newly pushed repos.

### 5. Add the meteoblue key (optional)

**Settings → Secrets and variables → Actions → New repository secret**

- Name: `METEOBLUE_KEY`
- Value: your key from `my.meteoblue.com`

Skip this and the source is dropped, with its family weight redistributed. See
the meteoblue section below for why it may be worth buying.

### 6. Run the probe before the first real build

**No terminal?** Actions -> **Probe endpoints** -> Run workflow. Results land
on the run's summary page with notes on how to read them, and as a downloadable
artifact. Everything below happens on the runner instead of your machine, and
`make build` becomes Actions -> Update board -> Run workflow.

**With a terminal:**

```bash
make install     # pip install -r requirements.txt
make probe
```

This is the step people skip and regret. It prints:

- every live Kalshi rain and temperature series with its settlement source and
  fee multiplier
- whether each Open-Meteo ensemble model responds
- the actual GRIB inventory lines from NOMADS, so you can confirm the
  `idx_regex` values in `settings.yml` match a real record

NCEP paths move. `settings.yml` records what was true when this was written,
not what is true today.

### 7. First build

```bash
make build       # writes docs/data/board.json and board_temp.json
make preview     # http://localhost:8000
```

`make preview` is not optional for local viewing. Opening `docs/index.html`
straight from disk gives you an empty page — browsers block `fetch()` on
`file://` URLs, so the JSON never loads. On Pages it works without changes.

The repo ships with **synthetic sample data** in `docs/data/` so the page
renders before you have anything real. Both files carry `"_sample": true` and
the page shows a banner. Your first successful build overwrites them.

### 8. Kick off the Action

**Actions → Update board → Run workflow**. Confirm it goes green and that a
`board …` commit lands on `main`.

Scheduled runs then fire four times daily at 03:40, 09:40, 15:40 and 21:40 UTC.
Adjust the crons in `.github/workflows/update-board.yml` if your cities' local
mornings sit elsewhere.

### 9. Verify the stations

The board shows a `?` beside every city whose settlement station you have not
confirmed. Open each market's rules page, check the station, then set
`verified: true` in `config/cities.yml`.

This matters more than any modelling on the page. Kalshi's temperature markets
use Midway for Chicago and Hobby for Houston; assume nothing carries over to
rain, and assume nothing carries over between the two market types. Also check
that `lat`/`lon` are the **station's** coordinates, not the city centre — on
scattered-convection days Central Park and JFK are different forecasts.

### Housekeeping

GitHub disables scheduled workflows after **60 days without repo activity**.
The board's own commits count, so an actively running board keeps itself alive;
one that breaks for two months stays broken silently.

---

# What the page shows

**Opportunities** merges both market types into one list ranked by expected
value in cents per contract, net of the quadratic Kalshi fee
`ceil(0.07 × P × (1−P))` and net of crossing the spread. Thin books are
excluded rather than flagged — an 18¢ "edge" on 30 contracts of volume with a
9¢ spread is an empty book, not an opportunity.

**Rain** is a dumbbell strip: each line runs from the market mid to the model
consensus, so the longest line is the biggest disagreement. The table below
groups model columns by family.

**High temperature** is a small multiple per city: filled bars are the model's
probability per bracket, the dashed line is the market's implied distribution
with its overround stripped. A shifted line means the market disagrees about
where the high lands; a flatter or peakier one means it disagrees about how
confident to be.

---

# Method

## Rain

**Threshold.** Kalshi settles Yes only if the station's official daily total is
strictly greater than 0.00", with trace counted as 0. The smallest reportable
increment is 0.01", so ">0" and "≥0.01" are the same event.

**The settlement source is not the NWS.** Daily rain markets settle on **The
Weather Company** data for a station labelled with an NWS-style CLI code. Every
model here targets NWS-style observations, so basis risk survives every
correction you can make.

**Local calendar days, not 24-hour QPF.** NYC today is 04Z–04Z; LA tomorrow is
07Z–07Z. `local_day_window()` converts each station's midnight-to-midnight into
UTC and the GRIB reader picks accumulation records to match, stitching shorter
windows when nothing lines up. A canned 24h field ending at 12Z misses most of
an afternoon of convection.

**The five sources are not five votes.** NBM ingests MOS and (until 2026-10-06)
HREF. NDFD is forecaster-edited NBM. A flat mean counts the blend's opinion
three times, so `blend.py` averages *within* a family and weights *across*:

| Family | Members | Weight |
|---|---|---|
| Convection-allowing | HREF, REFS | 0.35 |
| Blend | NBM, NDFD | 0.30 |
| Statistical | MOS | 0.10 |
| Global ensembles | ECMWF-ENS, GEFS, ICON, GEM, UKMO | 0.25 |

The global ensembles come from Open-Meteo's `/v1/ensemble`, which returns
**individual members** — so we count the fraction whose local-day total clears
0.254 mm. An exact answer to the contract's question, with no inherited POP
definition, and the only family independent of the NWS stack everyone else is
watching.

## High temperature

Different beast. Settles on the **NWS Daily Climate Report**, not The Weather
Company, and it's brackets rather than a binary — you need a distribution.

1. **Members, not means.** Each Open-Meteo member's max over the local calendar
   day: 150+ members across five global ensembles.
2. **Quantile blending, not probability averaging.** Averaging distributions
   pointwise in probability space manufactures uncertainty neither model
   claimed. Two sharp models 6°F apart, each ~7°F wide at 80%:
   probability-averaging gives 9.2°F, Vincentizing gives 5.8°F. Across 2-degree
   brackets that difference is most of the tail pricing.
3. **Deterministic sources get a distribution.** NDFD MaxT and MOS X/N become
   normal curves using each source's historical MAE. The sigmas in
   `settings.yml` are placeholders.
4. **Bias and spread correction.** `bias` is the standing grid-to-station
   difference; `spread_factor` widens the under-dispersed ensemble.
5. **Continuity correction.** The report prints whole degrees, so "75 to 76" is
   continuous temperature in [74.5, 76.5). Skipping this biases every bracket
   by roughly half a degree of mass.
6. **Strip the overround.** Bracket mids sum past 100 because each carries a
   spread. Compare a normalized model to an un-normalized market and you read
   that uniform excess as edge in every bracket at once.

The board also runs coherence checks that don't depend on your model being
right: ladder gaps or overlaps (usually a parse failure), and real arbitrage
when all-YES costs under 100¢ or all-NO under (n−1)×100¢.

`series_prefixes` is `[KXHIGH]`; add `KXLOW` for lows on the same machinery,
but refit the sigmas — overnight minimum errors behave differently from daytime
maxima, especially on clear calm nights.

---

# meteoblue

**It is not available through Windy's API.** Windy's Point Forecast API serves
arome variants, icon, iconD2, iconEu, gfs, namConus/Hawaii/Alaska, hrrrConus,
hrrrAlaska and canHrdps, plus wave and air-quality models. No meteoblue, no
ECMWF. What Windy shows as "meteoblue AI" is meteoblue's Learning MultiModel —
a licensed display layer combining roughly 30–40 models with station
observations, radar and other sources. Scraping Windy's frontend for it
violates their terms and will break constantly.

The supported route is meteoblue's own Forecast API
(`my.meteoblue.com/packages/basic-day`).

## Running on the free trial

The Free Weather API is **10 million credits, valid one year**, for
non-commercial use. `basic-day` costs about 4,000 credits per call (daily
resolution runs roughly half the hourly rate), so the arithmetic that matters:

| Fetch pattern | Credits/day | Trial lasts |
|---|---|---|
| 23 cities × 4 pipeline runs | 368,000 | ~27 days |
| 23 cities × 1 fetch, 3 cached | 92,000 | ~108 days |

One call returns the whole 7-day series, so a single daily fetch already
covers both today and tomorrow. The other three runs have nothing new to
learn. `fetch_meteoblue` therefore caches to `.cache/meteoblue.json` and
enforces `max_calls_per_day`; when the budget is exhausted it serves a stale
cached value rather than dropping the family, because a six-hour-old mLM run
still beats no mLM at all. Each run prints cumulative calls, estimated credits
spent and what's left of the trial, and warns past 80%.

The workflow restores that cache with `actions/cache` — runners are ephemeral,
so without it you'd pay four calls per city per day regardless. The Actions
cache is also private to the repo rather than served by Pages, which matters
for the next point.

Tune `max_calls_per_day`, `cache_hours`, and the `cities` whitelist in
`settings.yml`. Starting with five cities rather than 23 stretches the trial
past a full season and is plenty to answer whether mLM beats your existing
blend.

## What meteoblue's probabilistic variables buy you

`basic-day` already returns two of these — the pipeline was previously
discarding them.

| Variable | What it gives | Cost |
|---|---|---|
| `predictability` | 0–100% forecast confidence (Windy's coloured dots) | free, in `basic-day` |
| `precipitation_probability` | POP, but at a **>0.2 mm** bar | free, in `basic-day` |
| `rainspot` | 7×7 precipitation grid around the station | upgrades call to `basic-3h`, ~2× credits |
| `temperature_spread` | meteoblue's own ensemble standard deviation | extra package, +~4,000 credits |

**`temperature_spread` replaces a guess with a measurement.** Every
deterministic source on the temperature board gets a hardcoded sigma from
`deterministic_sigma`, because a point forecast carries no uncertainty with
it. meteoblue ships its own, per city per day, and it widens on genuinely
uncertain days instead of sitting flat. In testing, swapping the 1.9°F
placeholder for a live 3.4°F spread moved the blended 80% interval from
6.7°F to 9.5°F — which is several brackets' worth of probability mass.
Off by default (`use_temperature_spread`) because it costs a package.

**`predictability` widens the whole distribution on hard days.** Nothing else
on the board flags those days *before the fact*. `predictability_k: 0.6` maps
100% → ×1.00, 50% → ×1.30, 0% → ×1.60. Set it to 0 to disable.

**`rainSPOT` measures the area-to-point discount directly.** HREF and REFS
ensprod fields are neighbourhood maxima — "does anywhere within ~40 km get
0.01 inch". The standard relation is

    P(point) ≈ P(area) × E[wet area fraction | anything wet]

and rainSPOT samples that wet fraction instead of making you fit it blind
over a month of outcomes. On a patchy-convection test day with 18 of 49 cells
wet, applying it moved the CAM family from 0.70 to 0.30 and the blended
consensus from 0.56 to 0.44.

That is a large adjustment, which is why `apply_coverage_to_cam` is **off by
default**. meteoblue documents the 7×7 layout and the south-west-to-north-east
ordering but not the cell spacing, and the correction is only valid if the
rainSPOT footprint is comparable to the CAM neighbourhood radius. The coverage
figure is recorded on the board either way — watch it against outcomes for a
few weeks before switching it on.

**Their POP is not your contract.** meteoblue defines precipitation
probability as more than 0.2 mm; Kalshi's bar is 0.254 mm. Theirs answers an
easier question, so it reads high — and they note it is based on ensemble
forecasts and representative of a larger area than the precipitation quantity,
the same neighbourhood inflation HREF and REFS have. `calibration.METEOBLUE`
starts at −0.15 logits for this. Fit the real number from `history/`.

mLM sits in its own family on both boards (0.15 rain, 0.20 temperature) rather
than inside the blend, because it is genuinely independent of the NWS stack.

## Redistribution

`docs/data/*.json` is committed and served publicly by GitHub Pages. Emitting
meteoblue's forecast values there republishes their data — a restriction that
applies independently of whether your own use is commercial.

`publish_values: false` is the default. mLM still feeds the blended consensus;
its individual number just isn't itemised in the published file, and the
diagnostics record `"redacted": true` so you can see it contributed. Set it
true only if your repo is private or you have written permission.

Two things worth knowing if this stops being a test. Trading on the data is a
harder case to call non-commercial than evaluating it, and meteoblue arranges
trials individually rather than on a fixed window — telling their sales team
what you're building may get you a proper evaluation licence for nothing, and
is a better outcome than a key revoked mid-season.

---

# Calibration

`calibration:` (rain, logit offsets) and `temperature.bias` / `spread_factor`
all start neutral. Every run appends to `docs/data/history/`. After ~30 days,
score each source against what actually settled and fit them.

Expect HREF and REFS to need **negative** offsets. Their ensprod fields are
neighbourhood maxima — "does any grid point within ~40 km get 0.01 inch" —
which runs high against a single gauge, sometimes 15–20 points on scattered
convection days. That bias is stable enough to be worth removing, and removing
it is most of the edge in this whole exercise.

Three things on the temperature side are unfitted guesses right now:
`temperature.bias` is empty, `deterministic_sigma` values are placeholders, and
`spread_factor` is a global 1.15. Until those are fitted, the temperature
section maps where the market disagrees with raw model output — a research
starting point, not a signal. Rain tolerates being uncalibrated better, because
a binary is more forgiving than a 2-degree bracket.

---

# The October 6 problem

NCEP retires NAM, SREF, **HREF**, HiresW and NAM MOS on 2026-10-06 at 12Z,
replaced by RRFS and REFS. REFS has been on the NOMADS parallel feed since
around 2026-08-11 and moves to production the same day.

Run both HREF and REFS now and watch how they differ on your cities — your
calibration offsets do not transfer between them. `href:` carries a `retires:`
date and the pipeline skips it automatically afterward, but you will need to
repoint `refs.base` from `.../rrfs/para` to the production path that day. Re-run
`make probe` to find it.

---

# Layout

```
Makefile                 install / probe / build / preview
config/cities.yml        stations, coordinates, timezones
config/settings.yml      families, weights, thresholds, endpoints
pipeline/util.py         local-day windows, POP stitching, Kalshi fee math
pipeline/kalshi.py       series discovery and quotes
pipeline/gribtools.py    .idx byte-range fetch, KD-tree nearest gridpoint
pipeline/tempdist.py     quantile blending, Gaussian tails, bracket integration
pipeline/brackets.py     bracket parsing, overround removal, arbitrage checks
pipeline/blend.py        rain family weighting, EV, Kelly
pipeline/build.py        rain orchestrator
pipeline/build_temp.py   temperature orchestrator
pipeline/probe.py        endpoint discovery
pipeline/sources/        openmeteo, nws_text, gribprob, temp_sources
docs/index.html          the whole site, one file
docs/data/               board.json, board_temp.json, history/
```

# Caveats

None of the network code has been executed — it was written against the
documented shapes of each endpoint. The first `make probe` is where you find
out what needs adjusting; the MOS bulletin parser and the GRIB `idx_regex`
matches are the two most likely candidates.

This is a research tool, not advice. Kalshi positions can lose their full cost.
