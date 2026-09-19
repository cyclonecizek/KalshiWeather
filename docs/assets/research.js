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
    return `<div class="table-wrap"><table><thead><tr><th>${removal?'Source removed':'Source'}</th><th>Dates</th><th>${removal?'Without source Brier':'Source Brier'}</th><th>Full blend, paired dates</th><th>Difference</th><th>95% difference interval</th><th>MAE °F</th><th>Bias °F</th><th>80% coverage</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(names[r.model]||r.model)}<br><small>${esc(r.method)}</small></td><td>${r.dates}</td><td>${num(r.brier)}</td><td>${num(r.full_blend_brier)}</td><td>${num(r.brier_difference)}</td><td>${esc(interval(r.difference_interval))}</td><td>${num(r.mae_f,2)}</td><td>${num(r.bias_f,2)}</td><td>${pct(r.coverage80)}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function candidate(c, title) {
    const state = c.status === 'review' ? 'Ready for review; not applied' : c.status === 'not_supported' ? 'Holdout does not support adoption' : c.status === 'keep_current' ? 'Keep current settings' : 'Building evidence';
    let content = `<h4>${esc(title)} · ${state}</h4><p>${esc((c.reasons||[]).join(' '))}</p><p>Eligible earlier dates: ${c.train_n??0}. Later evaluation dates: ${c.test_n??0}.</p>`;
    if (c.parameters) {
      const p = c.parameters;
      const description = p.model ? `${names[p.model]||p.model}: ${p.mode==='without'?'remove from blend':`multiply ${p.weight_scope==='family'?'family':'within-family'} weight by ${p.multiplier}`}` : Number.isFinite(p.additional_shift_f) ? `Additional temperature shift ${num(p.additional_shift_f,2)}°F; spread multiplier ${num(p.spread_multiplier,2)}.` : `Rain calibration: logit slope ${num(p.slope,2)}, offset ${num(p.logit_offset,2)}.`;
      content += `<p>${esc(description)}</p><p>Training through ${esc(c.train_end)}. Evaluation ${esc(c.test_start)} to ${esc(c.test_end)}.</p><p>Brier: current ${num(c.original?.brier)}, candidate ${num(c.holdout?.brier)}, market ${num(c.market_brier)}. Paired difference: ${num(c.brier_difference)} (${esc(interval(c.difference_interval))}).</p>`;
      if (Number.isFinite(c.holdout?.mae_f)) content += `<p>MAE: ${num(c.original.mae_f,2)} → ${num(c.holdout.mae_f,2)}°F. Interval coverage: ${pct(c.original.coverage80)} → ${pct(c.holdout.coverage80)}.</p>`;
    }
    return content;
  }
  function render(groups) {
    if (!groups.length) return '<p>No settled evidence for this selection yet.</p>';
    return `<p>Negative Brier differences favor the experiment. Each comparison uses the same dates as the full blend. Positive temperature bias here means too warm. Short records and many comparisons can produce apparent winners by chance.</p>${groups.map(g=>`<section class="research-horizon"><h3>${esc(g.horizon.replace('_',' '))}</h3><p>${g.dates} historical dates; ${g.current_dates} with current settings. ${g.excluded_prior_dates} prior or unidentified dates excluded from candidate fitting.</p><h4>Each source on its own</h4><p>Historical point forecasts support temperature-error scores only. New source experiments include common station and observation corrections. Missing probability scores are shown as —.</p>${table(g.sources.filter(r=>r.mode==='source'))}${reliability(g.sources.filter(r=>r.mode==='source'))}<h4>Does removing a source help?</h4>${table(g.sources.filter(r=>r.mode==='without'),true)}<details><summary>Weight and calibration candidates</summary><p>Weights are selected on earlier dates and frozen for evaluation on 20 later dates. Temperature uses separate bias-fit and spread-calibration periods. These candidates do not change the live forecast or allocation checks.</p>${candidate(g.weights,'Weight candidate')}${candidate(g.calibration,g.kind!=='rain'?'Temperature bias and spread':'Rain probability calibration')}</details></section>`).join('')}`;
  }
  return {render};
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
