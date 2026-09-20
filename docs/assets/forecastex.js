'use strict';
const ForecastExView=(()=>{
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const n=(v,d=1)=>Number.isFinite(v)?v.toFixed(d):'—';
  const p=v=>Number.isFinite(v)?(100*v).toFixed(0)+'%':'—';
  const kind=r=>r.kind==='temperature_low'?'Low':'High';
  const age=(stamp,now)=>{const m=(now-Date.parse(stamp))/60000;return !Number.isFinite(m)||m<0?'age unknown':m<60?Math.floor(m)+'m ago':(m/60).toFixed(1)+'h ago';};
  const stale=(r,now)=>!Number.isFinite(Date.parse(r.retrieved_at))||now-Date.parse(r.retrieved_at)>30*60000||Date.parse(r.retrieved_at)>now;
  const note='<p class="muted">ForecastEx last trades are indicative, with unknown trade age. Kalshi values are market-implied probabilities. Different settlement sources can produce different outcomes. These comparisons do not change the weather model or bet sizes.</p>';
  const table=(headers,rows)=>`<div class="table-wrap"><table><thead><tr>${headers.map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
  function overview(rows,now=Date.now(),failed=false){
    if(!rows?.length)return '<p>ForecastEx comparisons will appear after the next data refresh.</p>';
    const valid=rows.filter(r=>r.station_match&&Number.isFinite(r.median_difference_f)&&!stale(r,now)&&!failed).sort((a,b)=>Math.abs(b.median_difference_f)-Math.abs(a.median_difference_f));
    return note+(failed?'<p class="notice">Comparison refresh failed. Previously downloaded data is for reference only.</p>':'')+`<p>${rows.filter(r=>r.station_match).length}/${rows.length} station/day/product comparisons have matching contracts. ${valid.length} have a calculable median and a recent download. Trade age is still unknown.</p>`+
      (valid.length?table(['Station / product','Your model','Kalshi median','ForecastEx median*','ForecastEx − Kalshi','Interpretation'],valid.slice(0,12).map(r=>`<tr><td><button class="city-button" data-city="${esc(r.city)}" data-kind="${esc(r.kind)}">${esc(r.city)} · ${kind(r)}</button><span class="sub">${esc(r.station)} · ${esc(r.date)}</span></td><td>${n(r.model_median)}°F</td><td>${n(r.kalshi_median)}°F</td><td>${n(r.median)}°F</td><td>${r.median_difference_f>0?'+':''}${n(r.median_difference_f)}°F</td><td>Investigate settlement and price age${r.window_match?'':'; reporting windows differ'}</td></tr>`)):'<p>No recent, complete median comparisons. See the availability details below.</p>')+
      `<details><summary>Availability and download times</summary>${table(['Station / product','Status','Downloaded'],rows.map(r=>`<tr><td>${esc(r.city)} · ${kind(r)}</td><td>${stale(r,now)?'Download stale; ':''}${esc(r.status==='indicative'?'Indicative trades; price age unknown':r.message)}</td><td>${esc(age(r.retrieved_at,now))}</td></tr>`))}</details>`;
  }
  function detail(r,now=Date.now(),currentSnapshot=null,failed=false){
    if(!r)return '<p>No ForecastEx comparison for this station, product and date. Rain is not included.</p>';
    const outdated=currentSnapshot&&r.snapshot_id!==currentSnapshot;
    const prices=(r.kalshi_price_times||[]).filter(x=>Number.isFinite(Date.parse(x))).sort((a,b)=>Date.parse(a)-Date.parse(b));
    const head=note+`<p>Model issued ${esc(age(r.forecast_issued_at,now))}; oldest Kalshi price retrieval ${esc(age(prices[0],now))}. These are the values captured with this comparison.</p>`+`<p>${esc(r.station)} · ${esc(r.date)} · ${kind(r)}. Downloaded ${esc(age(r.retrieved_at,now))}; trade timestamp unavailable.</p>`+
      (stale(r,now)||failed?'<p class="notice">Comparison download is stale or unavailable. Displayed values are historical reference only.</p>':'')+
      (outdated?'<p class="notice">This comparison is anchored to an earlier forecast snapshot. Its model and Kalshi values below are the archived comparison values.</p>':'');
    if(!r.station_match)return head+`<p>${esc(r.message)}. Nearby stations are not substituted.</p>`;
    const url=/^https:\/\/forecastex\.com\/markets\/U[HL][A-Z]{3}$/.test(r.market_url)?r.market_url:'https://forecastex.com/markets';
    return head+table(['Your model','Kalshi median','ForecastEx median*'],[`<tr><td>${n(r.model_median)}°F</td><td>${n(r.kalshi_median)}°F</td><td>${n(r.median)}°F</td></tr>`])+
      `<p>*ForecastEx median is reconstructed from last-trade threshold prices. ${r.monotonicity_adjusted?'Out-of-order probabilities were pooled to form a monotone curve. ':''}A median is withheld when the 50% crossing lacks adjacent priced thresholds.</p>`+
      `<p>Same station; settlement equivalence is not established. ForecastEx: Weather Underground Daily Observations, ${esc(r.window_start)} to ${esc(r.window_end)}. Kalshi: ${esc(r.kalshi_window_start)} to ${esc(r.kalshi_window_end)}. ${r.window_match?'Reporting boundaries match.':'Reporting boundaries differ, including possible daylight-saving effects.'}</p>`+
      `<p>Next step: inspect the observations and reporting windows before interpreting a gap as forecast disagreement. Confirm executable prices on both exchanges before considering a trade.</p>`+
      table(['Threshold outcome','Your model†','Kalshi†','ForecastEx last YES trade','Difference vs. Kalshi','Open interest'],(r.rows||[]).map(x=>`<tr><td>${r.kind==='temperature_low'?'Below':'Above'} ${n(x.strike,0)}°F</td><td>${p(x.model_probability)}</td><td>${p(x.kalshi_probability)}</td><td>${p(x.probability)}</td><td>${n(x.difference_pp)} pp</td><td>${n(x.open_interest,0)}</td></tr>`))+
      '<p class="muted">†The model and Kalshi columns describe their climate-report outcome at this numerical threshold. They do not forecast Weather Underground settlement directly. Blank Kalshi probabilities mean the threshold splits a market bracket or the ladder is incomplete.</p>'+
      `<details><summary>Compare the temperature ranges</summary>${table(['Range','Your model†','Kalshi†','ForecastEx derived range chance*'],(r.brackets||[]).map(x=>`<tr><td>${esc(x.label)}</td><td>${p(x.model_probability)}</td><td>${p(x.kalshi_probability)}</td><td>${p(x.probability)}</td></tr>`))}<p>ForecastEx range probabilities use differences of the fitted cumulative probabilities. Missing range boundaries remain blank.</p></details>`+
      `<p><a href="${url}" target="_blank" rel="noopener">Open ForecastEx market</a> · <a href="https://data.forecastex.com/regulatory/DailyTemperatureTermsandConditions.pdf" target="_blank" rel="noopener">Read ForecastEx settlement rules</a></p>`;
  }
  function performance(report,product,horizon){
    if(!report)return '<p>Collecting prospective cross-market snapshots. Historical comparisons are not reconstructed.</p>';
    const rows=(report.records||[]).filter(r=>r.kind===product&&(horizon==='all'||r.horizon===horizon));
    const dates=new Set(rows.map(r=>r.date)).size;
    const groups=[...new Set(rows.map(r=>r.city))].sort().map(city=>{
      const rs=rows.filter(r=>r.city===city),settled=rs.filter(r=>Number.isFinite(r.settlement_difference_f));
      const mean=key=>{const ds=[...new Set(rs.filter(r=>Number.isFinite(r[key])).map(r=>r.date))];return ds.length?ds.reduce((s,d)=>{const a=rs.filter(r=>r.date===d&&Number.isFinite(r[key]));return s+a.reduce((sum,r)=>sum+r[key],0)/a.length;},0)/ds.length:null;};
      const matched=[...new Map(settled.map(r=>[r.date,r])).values()];
      return `<tr><td>${esc(city)}</td><td>${new Set(rs.map(r=>r.date)).size}</td><td>${matched.length}</td><td>${p(matched.length?matched.filter(r=>r.settlement_difference_f===0).length/matched.length:null)}</td><td>${n(mean('settlement_difference_f'))}°F</td><td>${n(mean('fx_brier'),3)}</td><td>${n(mean('model_mae_on_kalshi'))} / ${n(mean('kalshi_mae_on_kalshi'))} / ${n(mean('fx_mae_on_kalshi'))}</td></tr>`;
    });
    return `<p>${dates} distinct target dates; ${rows.length} station/date/horizon snapshots. Each date has equal weight within a station. Awaiting outcomes are shown as blanks.</p><p>${esc(report.note)}</p>`+
      (report.errors?.length?`<p class="notice">${report.errors.length} settlement-report retrievals failed. Missing outcomes remain unscored.</p>`:'')+
      table(['Station','Archived dates','Paired outcome dates','Same temperature','ForecastEx − Kalshi settlement†','ForecastEx Brier†','MAE vs. Kalshi outcome: model / Kalshi / ForecastEx'],groups)+
      '<p class="muted">†ForecastEx outcome evidence comes from paired, binary post-expiry settlement prices with zero open interest. An exact temperature requires adjacent resolved thresholds; it is inferred, not independently retrieved from Weather Underground. MAE here is a cross-target diagnostic and is not a claim that one exchange has superior skill. No evidence automatically enables allocations.</p>';
  }
  return {overview,detail,performance};
})();
if(typeof module!=='undefined')module.exports=ForecastExView;
