'use strict';
// Pure rendering helpers also run in the frontend regression tests.
const ForecastResearch = (() => {
  const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = (x, n=3) => Number.isFinite(x) ? x.toFixed(n) : '—';
  const pct = x => Number.isFinite(x) ? `${(100*x).toFixed(0)}%` : '—';
  const names = {ECMWF_ENS:'ECMWF ensemble', GEFS:'NOAA GEFS', ICON_EPS:'DWD ICON', GEM_EPS:'Canadian GEM', UKMO_ENS:'UK Met Office', NBM:'NOAA National Blend', NBM_T:'NOAA National Blend', NDFD:'NWS forecaster guidance', METEOBLUE:'Meteoblue mLM'};
  const interval = x => x ? `${num(x.low)} to ${num(x.high)}` : 'More dates needed';
  const label = r => `${names[r.model] || r.model}${r.mode==='without'?' removed':r.mode==='weight'?` ×${r.multiplier} weight`:''}`;
  function reliability(rows) {
    return `<details><summary>Source reliability by probability bin</summary><p>Compare forecast probabilities with observed frequencies. Bin counts are correlated bracket forecasts, not independent dates.</p>${rows.filter(r=>r.reliability?.length).map(r=>`<h4>${esc(label(r))} · ${esc(r.method)}</h4><div class="table-wrap"><table><thead><tr><th>Forecast chance</th><th>Observed frequency</th><th>Bracket forecasts</th></tr></thead><tbody>${r.reliability.map(b=>`<tr><td>${pct(b.forecast)}</td><td>${pct(b.observed)}</td><td>${b.n}</td></tr>`).join('')}</tbody></table></div>`).join('') || '<p>No archived probability scores yet.</p>'}</details>`;
  }
  function table(rows, removal=false) {
    if (!rows.length) return `<p>${removal?'Removal experiments will appear after newly archived forecasts settle.':'Individual source scores will appear as forecasts settle.'}</p>`;
    return `<div class="table-wrap"><table><thead><tr><th>${removal?'Source removed':'Source'}</th><th>Dates</th><th>${removal?'Without source Brier':'Source Brier'}</th><th>Full blend, paired dates</th><th>Difference</th><th>95% difference interval</th><th>MAE °F</th><th>Bias °F</th><th>50 / 80 / 90% coverage</th><th>Approx. CRPS</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(names[r.model]||r.model)}<br><small>${esc(r.method)}</small></td><td>${r.dates}</td><td>${num(r.brier)}</td><td>${num(r.full_blend_brier)}</td><td>${num(r.brier_difference)}</td><td>${esc(interval(r.difference_interval))}</td><td>${num(r.mae_f,2)}</td><td>${num(r.bias_f,2)}</td><td>${pct(r.coverage50)} / ${pct(r.coverage80)} / ${pct(r.coverage90)}</td><td>${num(r.crps_approx)}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function candidate(c, title) {
    const state = c.status === 'review' ? 'Ready for review; not applied' : c.status === 'not_supported' ? 'Holdout does not support adoption' : c.status === 'keep_current' ? 'Keep current settings' : 'Building evidence';
    let content = `<h4>${esc(title)} · ${state}</h4><p>${esc((c.reasons||[]).join(' '))}</p><p>Eligible earlier dates: ${c.train_n??0}. Later evaluation dates: ${c.test_n??0}.</p>`;
    if (c.parameters) {
      const p = c.parameters;
      const description = p.mode==='distribution' ? p.model : p.model ? `${names[p.model]||p.model}: ${p.mode==='without'?'remove from blend':`multiply ${p.weight_scope==='family'?'family':'within-family'} weight by ${p.multiplier}`}` : Number.isFinite(p.additional_shift_f) ? `Additional temperature shift ${num(p.additional_shift_f,2)}°F; spread multiplier ${num(p.spread_multiplier,2)}.` : `Rain calibration: logit slope ${num(p.slope,2)}, offset ${num(p.logit_offset,2)}.`;
      content += `<p>${esc(description)}</p><p>Training through ${esc(c.train_end)}. Evaluation ${esc(c.test_start)} to ${esc(c.test_end)}.</p><p>Brier: current ${num(c.original?.brier)}, candidate ${num(c.holdout?.brier)}, market ${num(c.market_brier)}. Paired difference: ${num(c.brier_difference)} (${esc(interval(c.difference_interval))}).</p>`;
      if (Number.isFinite(c.holdout?.mae_f)) content += `<p>MAE: ${num(c.original.mae_f,2)} → ${num(c.holdout.mae_f,2)}°F. Interval coverage: ${pct(c.original.coverage80)} → ${pct(c.holdout.coverage80)}.</p>`;
    }
    return content;
  }
  function render(groups) {
    if (!groups.length) return '<p>No settled evidence for this selection yet.</p>';
    return `<p>Negative Brier differences favor the experiment. Each comparison uses the same dates as the full blend. Positive temperature bias here means too warm. Short records and many comparisons can produce apparent winners by chance.</p>${groups.map(g=>`<section class="research-horizon"><h3>${esc(g.horizon.replace('_',' '))}</h3><p>${g.dates} historical dates; ${g.current_dates} with current settings. ${g.excluded_prior_dates} prior or unidentified dates excluded from candidate fitting.</p><h4>Each source on its own</h4><p>Historical point forecasts support temperature-error scores only. New source experiments include common station and observation corrections. Missing probability scores are shown as —.</p>${table(g.sources.filter(r=>r.mode==='source'))}${reliability(g.sources.filter(r=>r.mode==='source'))}<h4>Does removing a source help?</h4>${table(g.sources.filter(r=>r.mode==='without'),true)}<details><summary>Weight and calibration candidates</summary><p>Weights are selected on earlier dates and frozen for evaluation on 20 later dates. Temperature uses separate bias-fit and spread-calibration periods. These candidates do not change the live forecast or allocation checks.</p>${g.distributions?`<h4>Distribution comparisons</h4>${table(g.sources.filter(r=>r.mode==='distribution'))}${candidate(g.distributions,'Distribution candidate')}`:''}${candidate(g.weights,'Weight candidate')}${candidate(g.calibration,g.kind!=='rain'?'Temperature bias and spread':'Rain probability calibration')}</details></section>`).join('')}`;
  }
  function focused(c) {
    if (!c) return '';
    const status=c.status==='review'?'Ready for owner review; not applied':c.status==='not_supported'?'Evaluation does not support adoption':'Collecting new forecasts';
    return `<article class="notice info"><h4>${esc(c.title)} · ${status}</h4><p>${c.paired_dates??0} paired dates toward ${c.required_dates??20}; ${c.dates??0} registered settled dates. First eligible reporting date: ${esc(c.first_eligible_date)}.</p><p>${esc((c.reasons||[]).join(' '))}</p>${c.holdout?`<p>Brier: existing ${num(c.original?.brier)}, candidate ${num(c.holdout.brier)}. Difference ${num(c.brier_difference)}; 95% date-block interval ${esc(interval(c.difference_interval))}.${Number.isFinite(c.holdout.coverage80)?` 80% coverage: ${pct(c.original?.coverage80)} → ${pct(c.holdout.coverage80)}.`:''}</p>`:''}<p>No live weights or probabilities changed. Results used to choose this hypothesis are excluded from its prospective evaluation.</p></article>`;
  }
  function weatherNextDetails(source) {
    const d=source?.temperature_diagnostics;
    if (!d) return '';
    return `<details><summary>WeatherNext 2 temperature processing</summary><p>Raw median of member daily extrema: ${num(d.raw_median_f,2)}°F. After observations: ${num(d.conditioned_median_f,2)}°F. Final research estimate: ${num(d.final_median_f,2)}°F.</p><p>Configured bias: ${num(d.configured_bias_f,2)}°F. Observation conditioning: ${d.observation_used?'used':'not used'}. Hourly samples: ${d.hourly_points??0}. Native resolution: six hours.</p><p>${esc(d.note)}</p><p>Reporting window: ${esc(d.window_start||'Not archived')} to ${esc(d.window_end||'Not archived')}. Zero operational blend weight.</p></details>`;
  }
  function diagnostics(d) {
    if (!d) return '';
    return `<details><summary>Where does WeatherNext 2 temperature bias enter?</summary><p>${esc(d.note)}</p><div class="table-wrap"><table><thead><tr><th>Processing stage</th><th>Dates</th><th>Bias °F</th><th>MAE °F</th></tr></thead><tbody>${(d.stages||[]).map(s=>`<tr><td>${esc(s.stage)}</td><td>${s.dates}</td><td>${num(s.bias_f,2)}</td><td>${num(s.mae_f,2)}</td></tr>`).join('')}</tbody></table></div><p>Stage diagnostics begin with newly archived forecasts. Missing historical stages are not reconstructed.</p></details>`;
  }
  const historicalRender=render;
  function currentRender(groups) {
    return groups.map(g=>`${focused(g.focused)}${diagnostics(g.weathernext_diagnostics)}${historicalRender([{...g,sources:g.current_sources??g.sources}])}${g.current_sources?`<details><summary>Earlier settings and historical source evidence</summary><p>The main tables use current settings only. These historical results mix settings and availability periods.</p>${table(g.sources.filter(r=>r.mode==='source'))}</details>`:''}`).join('') || historicalRender([]);
  }
  return {render:currentRender, weatherNextDetails};
})();
if (typeof module !== 'undefined') module.exports = ForecastResearch;

function drawModelResearch() {
  const target = document.getElementById('model-research');
  const select = document.getElementById('research-city');
  if (!target || !select) return;
  if (!state.modelResearch) { target.textContent='Source research will appear after the scoring update completes.'; return; }
  const kind = document.getElementById('perf-kind').value;
  const horizon = document.getElementById('horizon').value;
  const groups = (state.modelResearch.groups||[]).filter(g=>g.kind===kind&&(horizon==='all'||g.horizon===horizon));
  const cities = [...new Set(groups.map(g=>g.city))].sort();
  const selected = cities.includes(select.value) ? select.value : cities[0];
  select.replaceChildren(...cities.map(city=>{const option=document.createElement('option');option.value=city;option.textContent=city;return option;}));
  select.value = selected || '';
  target.innerHTML = ForecastResearch.render(groups.filter(g=>g.city===selected));
  if (state.refreshErrors?.modelResearch) target.insertAdjacentHTML('afterbegin','<p class="notice">Research refresh failed; showing the previous report.</p>');
}
