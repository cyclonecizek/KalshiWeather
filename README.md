# Kalshi Weather Forecast Desk

[Open the forecast desk](https://cyclonecizek.github.io/KalshiWeather/)

A station-based research dashboard for Kalshi daily high-temperature and rain markets. It compares weather guidance with executable market quotes, archives forecasts before settlement, and scores them against the outcomes. It does not send orders.

## Weather-first decisions

The Daily briefing starts with station forecasts and four review priorities, then translates each position into its weather outcome. A station workup explains the likely range, uncertainty, observation limitations, and meteorological questions to investigate. Cloud, radar, and frontal prompts are analysis questions, not diagnoses inferred from temperature guidance.

The practice calculator makes YES/NO, cost including estimated fees, possible net gain, maximum loss, and break-even probability explicit. It uses an archived-in-memory quote snapshot until reset, checks its age again, and never enables an order or clears the server's eligibility checks. Its quantity is a hypothetical example, not a stake recommendation. Detailed trading tables are collapsed by default; Trading basics defines the terminology with official Kalshi references.

## Using the desk

- **Market board:** today/tomorrow forecasts, station identifiers, market comparisons, source age, and paper-order eligibility. Enable research comparisons to see rows that fail the policy checks and their reasons.
- **Station detail:** hourly ensemble temperature curves and provisional station observations, the temperature bracket ladder or rain market, source retrieval times, contract evidence, and changes since the previous snapshot. Changes separate full-day guidance/source mix, the observation adjustment, and the market midpoint. This is descriptive accounting, not proof of causality.
- **Performance:** paired model/market Brier scores, log loss, reliability, bias, and interval coverage by city, product, and forecast horizon. Empty results mean no qualifying forecasts have settled yet.
- **Forecast journal:** preview a temperature shift and uncertainty change, or enter a rain probability. Give a reason, then submit the prefilled GitHub issue as the repository owner. The workflow archives the original and adjusted probabilities with the issue's timestamp and scores both after settlement. Issue content is public. Editing an issue does not rewrite its archived forecast.

The page refreshes every minute while visible and when the tab becomes active. Failed fetches retain the last usable snapshot. Quote and board age are checked again in the browser; stale proposals lose eligibility even if the page remains open.

## Forecast and market behavior

Temperature forecasts combine quantile curves within model families and then across families, retaining an allowance for model disagreement. With adequate fresh station observations, remaining-hour ensemble trajectories can move the predicted high down or up. The observed maximum supplies a lower bound with a configurable allowance for reporting differences. A late clock time alone never collapses uncertainty.

Open-Meteo precipitation timestamps denote the end of the preceding hour. A member needs every hourly interval in the configured reporting window; missing values are never replaced with zero. When precipitation observations have complete interval coverage, rain forecasts use remaining-hour trajectories consistent with the observations. Otherwise they retain full-day guidance and show incomplete coverage. Rolling station accumulations are provisional; an incomplete day is not evidence of a dry day.

Quotes support dollar and legacy cent fields, preserve genuine zero prices, and never invent an ask from a bid or last trade. Comparisons use executable asks and taker fees. Depth is bound to the quoted price. Arbitrage checks require a complete, non-overlapping bracket basket and include fees.

All board suggestions and persistent paper proposals use the same policy: settlement verification, source freshness and completeness, model-family coverage, calibration evidence, quote freshness, spread, confirmed depth, remaining time, edge limits, and a shared daily budget. Cost includes fees, with city and contract caps. Proposals are not assumed fills or realized profits. The daily budget resets at UTC midnight.

## Current limitations

- `config/settlement.json` records station/source evidence from live contract rules. Chicago's temperature market uses Midway (`KMDW`); its rain market uses O'Hare (`KORD`).
- The current rules name The Weather Company, while general weather documentation also describes NWS climate reports. The precise source-specific daily reporting window remains unconfirmed. Fixed local-standard-time windows are provisional and `window_verified` remains false. Paper suggestions stay blocked until the definition is confirmed.
- Model version 2 starts a new verification history. Old snapshots are retained but excluded from its performance scores. Exact temperature errors require an actual numeric settlement value; the code never substitutes a winning bracket midpoint.
- Default bias, spread, family weights, and station-versus-grid adjustments are hypotheses awaiting validation. `config/calibration.json` is empty. The research scorer reports candidates after sufficient distinct dates with matching model settings and known settlement times; temperature uses separate bias-fit, spread-calibration, and evaluation periods. It never approves its own calibration or changes trading settings.
- Hourly ensembles carry provider retrieval times; unavailable model-run times are explicitly unknown. NDFD and NBM daily/12-hour products are supplemental guidance and may not exactly match the settlement window. They are omitted from intraday conditioning.
- With `publish_values: false`, Meteoblue is excluded from all new public numeric products, including aggregate blends. Hiding only its individual values could leave them reconstructible from a known blend. The sanitizer removes restricted diagnostics and reconstructible companion fields from checked-out legacy data. It does not rewrite earlier Git history or third-party caches.

## Run locally

Use Python 3.12 and Node 22 or newer. Production Python dependencies are pinned in `requirements.txt`.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m pipeline.selfcheck
python -m pipeline.run
python -m pipeline.performance
```

`pipeline.run` builds both products, validates them, atomically replaces usable boards, and archives uniquely named snapshots. A failed or empty product retains its last good board; a coverage drop below 75% of the previous version-2 board also blocks replacement. `docs/data/status.json` records update health. Partial source availability is visible in the data and eligibility checks. Independent products can succeed even when the other fails; the process still returns a failure exit code for a failed product.

Compatibility commands `python -m pipeline.build` and `python -m pipeline.build_temp` build one product. For a development-only subset, set `WEATHER_CITIES` to comma-separated configured names. Use a separate checkout to avoid replacing full boards with a subset.

```sh
pip install pytest==9.1.1
python -m pytest -q
node --test tests/test_frontend.cjs
node --check docs/assets/app.js
python -m pipeline.publication_check
```

Regression tests cover real failure modes: quote schemas and missing prices, orderbook units and price-matched depth, physical temperature floors, missing precipitation, interval boundaries, observation units, stale signals, shared budgets and fees, restricted-source leakage, settlement selection, time-ordered verification, adjustment provenance, and last-good publication.

## Configuration and automation

- `config/cities.yml`: city inventory and market series.
- `config/settlement.json`: separate station, source, threshold, and reporting-window evidence for each product.
- `config/settings.yml`: providers, family weights, forecast assumptions, and execution gates.
- `config/calibration.json`: manually reviewed eligibility evidence keyed by `city|kind|horizon`, with `model_version`, `validated`, and sample count `n`. A validation entry does not itself apply a fitted correction; configured model parameters must match the reviewed experiment.

GitHub Actions builds both boards on the combined update schedule, scores settled forecasts twice daily, records owner-authored adjustments, runs regression checks on pull requests, and deploys the static `docs/` site. Data writers share one concurrency group. They commit validated data and status, rebase on main, and surface push/rebase failures. Pages deploys after data workflows because commits made with `GITHUB_TOKEN` do not trigger another push workflow.

The repository needs Actions with contents-write access and GitHub Pages configured to use Actions. The adjustment journal also requires Issues to be enabled. An optional `METEOBLUE_KEY` secret is used only when publication is explicitly enabled and licensed; no API key is embedded in the site.

## Data files

`docs/data/board.json` and `board_temp.json` contain schema-version-2 boards. `history/` stores immutable issuance snapshots. `performance.json` contains paired verification records; `outcomes.json` caches final outcomes; `adjustments.json` stores owner adjustments; and `paper/ledger.json` stores paper proposals. History grows with each build and should be archived deliberately rather than deleting the evidence used for scoring.


### Personal budget planner

The daily briefing accepts a total loss budget (default $500), money already committed,
and a choice of the automated model or saved owner forecast adjustments. Saved
adjustments must match the current snapshot; stale adjustments do not silently fall
back to the automated forecast. The planner compares both purchase sides, includes
whole-order fees, and respects verified depth at the displayed price.

Sizing uses quarter Kelly after reducing the chosen outcome probability by 5 percentage
points. It caps a position at 5% of the entered budget, a city at 10%, and total
commitments at 25%, leaving the rest unallocated. These are cautious design defaults,
not a fitted joint weather-risk model or a guarantee against losses. Only one position
per city/product/reporting day is included. The user must enter existing commitments;
there is no account connection and a refresh is a replacement plan, not an instruction
to add positions. Existing server paper-ledger dollar limits are replaced by this
form's explicit budget, while forecast, settlement, calibration, freshness, price,
and liquidity checks still apply. Personal forecast calibration remains pending.

An expandable hypothetical view illustrates sizing while settlement/calibration
verification is pending. It does not change the recommended plan and continues to
block stale or missing data. No order is sent or fill recorded.

### Meteoblue visibility and diagnostics

`temperature.sources.meteoblue.publish_values` is enabled following the repository
owner’s public-display authorization on 2026-09-06. The source requires
`METEOBLUE_KEY` as a repository Actions secret. Setting publication to false disables
API calls and excludes Meteoblue from the public blend. Data updates display
per-station daily high, provider PoP and retrieval time, plus overall source status.
Daily Meteoblue packages do not provide a curve in the hourly chart. Provider PoP
is not the final Kalshi station-event probability.

Public diagnostics distinguish disabled publication, absent credentials, request
failures, daily call limits, and usable data. API exception URLs are not logged
because they may contain credentials. Failed requests count toward the local daily
call budget; expired cached forecasts are not reused as fresh data. A 25-call/day
limit can refresh roughly 25 stations once daily, so an eight-hour cache does not
provide uninterrupted full-network guidance. Set package, credit, call and refresh
budgets according to the actual account allowance.

### Recommendation readiness fixes (September 13, 2026)

The budget form now defaults to explicitly labelled **paper practice**. Select
**Verified allocations** to see sizes only for positions that pass every
check. Paper practice relaxes pending settlement/calibration assumptions only;
known contract-definition mismatches, stale quotes, insufficient guidance,
unknown fees and poor liquidity still block it. No trades are submitted.

Ensemble downloads use batches of four stations, bounded retries, and a
per-station cache shared by rain and temperature builds. Optional source and
backup-observation failures remain visible instead of being added to every
station's failure count. Coverage now requires at least two usable ensemble
forecast centres; correlated ensemble members are not counted as separate
centres. The existing family-weighted forecast calculation is unchanged.

`Refresh market quotes` runs every ten minutes without fetching weather again.
It preserves forecast issuance and archived scoring snapshots, rechecks current
contract definitions/fees/depth, and invalidates failed quotes. GitHub schedules
can be delayed, so the 20-minute quote limit still applies. Forecast freshness
and elapsed reporting-window limits remain enforced.

Domestic reporting windows are documented in [settlement verification](docs/settlement-verification.md).

Forecast skill now shows each group's distinct settled-date count and review
criteria. Scoring publishes `docs/data/calibration_review.json`. The owner can
run **Review model calibration** with a displayed `City|kind|horizon` key.
Approval requires at least 20 distinct settled dates, a negative upper normal
approximation bound for paired Brier difference, and weighted bin calibration
error no greater than 0.10. These are screening criteria, not a profitability
guarantee. The workflow refuses insufficient evidence and non-owner approval,
records an audit hash, and binds approval to the current weather configuration.
It does not fit a new model on the evaluation data or automatically approve one.
Restoring the recommendation path does not manufacture a qualifying track record.

Only archived forecasts carrying the current model fingerprint count toward
calibration approval. Older scores remain visible in Forecast skill but do not
silently validate a changed or unidentifiable model configuration.

## Source verification and model candidates

The station forecast archives `experiments` before settlement: each included
source alone, the full blend with that source removed, and 0.5× / 1.5× changes to
its within-family weight (or family weight for a configured single-source family). Temperature experiments rerun the same distribution
builder with the same observation conditioning, station bias, spread settings,
and exact contract ladder. Rain experiments retain the same observation
override when measurable rain has already been observed. No extra provider
requests are needed. These research forecasts do not change orders or live
probabilities.

`pipeline.performance` also writes `docs/data/model_research.json`, displayed in
**Forecast verification → Which sources improve the forecast?** Select a station,
product, and horizon. Source/removal scores are paired against the full blend on
exactly the same dates. Negative Brier differences favor the experiment. The
new source table uses forecast minus observed temperature bias: positive is warm.
The original verification table explicitly retains observed minus forecast.
Historical temperature medians support point-error scores only, not invented
probability distributions. Historical raw-input scores and newly archived source
experiments with common post-processing are labeled separately.

Weight candidates are selected from the archived removal and weight experiments
using at least 40 earlier dates; the chosen candidate is frozen for 20 later
evaluation dates. Missing chosen-candidate forecasts invalidate that evaluation,
rather than allowing another candidate to be selected from holdout results.
Temperature candidates separate at least 20 bias-fit, 20 spread-calibration, and
20 evaluation dates. Spread uses an empirical 80% residual quantile with a 0.25°F
minimum sigma and a multiplier bounded to 0.5–8. Rain fits a regularized logistic
probability adjustment on 40 earlier dates and tests it on 20 later dates, with
at least five wet and five dry training dates. All fitting excludes other model
fingerprints and requires settlement retrieval before the later period's first
forecast issuance. Unknown retrieval times cannot qualify.

Candidate screens require an approximate 95% paired Brier improvement over both
the full blend and the archived market, probability calibration error at most
10 percentage points, and (for temperature) 70–90% observed coverage of the 80%
interval and no deterioration in MAE. Difference intervals use three-date moving
blocks; they are approximate, not proof of independence. Short records, multiple
comparisons, repeated monitoring, and changing weather regimes limit inference.
A passing candidate is ready for review, never automatically deployed. Review
prospective results before adopting settings, and preserve the existing separate
calibration/eligibility approval. Default within-family weights remain equal;
`member_weights` in the rain or temperature configuration supports reviewed
relative weights. A station/horizon result does not justify a global change.

The separate fitting and evaluation periods follow the principles described in
[probability calibration](https://scikit-learn.org/stable/modules/calibration.html)
and [conformalized quantile regression](https://arxiv.org/abs/1905.03222).
Weather observations are temporally dependent, so no distribution-free coverage
guarantee is claimed here. Interval calibration is checked empirically on later
dates. No settings were fitted to the initial nine-day record.
