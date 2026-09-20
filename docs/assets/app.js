'use strict';
const $=id=>document.getElementById(id), M=ForecastMath;
const D = ForecastDecision;
const isTemperature=k=>['temperature','temperature_low'].includes(k);
const temperatureLabel=k=>k==='temperature_low'?'low':'high';
const productLabel=k=>k==='rain'?'Rain':k==='temperature_low'?'Daily low':'Daily high';
let practice = null;
const state={rain:null,temperature:null,temperature_low:null,performance:null,outcomes:{},adjustments:[],paper:[],status:null,refreshErrors:{},day:'0',city:'',kind:'temperature',view:'board',loading:false,lastLoad:null};
let draft=null;
function draftContext(){const live=current();return draft&&draft.city.city===state.city&&draft.board.kind===state.kind&&draft.day.date===live.day?.date?draft:live;}
function resetDraft(){draft=null;$('pop').value=Math.round((current().day?.consensus??.5)*100);$('shift').value=0;$('spread').value=1;$('reason').value='';$('adjustment-message').textContent='';}
const colors=['#23627c','#8d5593','#328277','#b67c24','#5d6eaa','#ab5c4d'];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=(v,d=1)=>Number.isFinite(v)?v.toFixed(d):'—';
const pct=v=>Number.isFinite(v)?(100*v).toFixed(0)+'%':'—';
const time=s=>s?new Date(s).toLocaleString():'Unknown';
const ageText=s=>{const m=M.age(s);return !Number.isFinite(m)?'Age unknown':m<0?'Timestamp in future':m<60?Math.floor(m)+'m old':(m/60).toFixed(1)+'h old';};
const metric=(value,label)=>`<div class="metric"><div class="value">${esc(value)}</div><div class="label">${esc(label)}</div></div>`;
const empty=text=>`<div class="empty">${esc(text)}</div>`;
const cities=()=>[...new Set([...(state.rain?.cities||[]),...(state.temperature?.cities||[]),...(state.temperature_low?.cities||[])].map(c=>c.city))].sort();
function entry(kind,city=state.city){const board=state[kind],c=board?.cities.find(c=>c.city===city);return {board,city:c,day:c?.days[state.day]};}
function current(){return entry(state.kind);}
function filteredCities(){const search=$('search').value.toLowerCase();return cities().filter(name=>{const r=entry('rain',name),t=entry('temperature',name);return [name,r.city?.icao,t.city?.icao].join(' ').toLowerCase().includes(search);});}
function view(name){state.view=name;document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!==name);document.querySelectorAll('nav button').forEach(b=>b.setAttribute('aria-current',b.dataset.view===name?'page':'false'));if(name==='detail')drawDetail();if(name==='performance')drawPerformance();if(name==='journal')drawJournal();}
function signals(){const rows=[];for(const kind of ['rain','temperature','temperature_low']){const b=state[kind];if(!b)continue;for(const c of b.cities){const d=c.days[state.day];if(!d)continue;const brackets=isTemperature(kind)?d.ladder:[{market:d.market,edge:d.edge,model_p:d.consensus,label:'Rain'}];for(const bracket of brackets){const e=bracket.edge,q=bracket.market;if(!e||e.ev_cents<0)continue;const reasons=M.eligibility(e,q,b,Date.now(),d);rows.push({kind,city:c.city,d,b,q,e,bracket,reasons});}}}return rows.sort((a,b)=>b.e.ev_cents-a.e.ev_cents);}
function drawBoard(){drawForecastExOverview();drawBudget();drawMeteoblueOverview();const names=filteredCities(),all=signals().filter(r=>names.includes(r.city)),usable=all.filter(r=>!r.reasons.length);
$('summary').innerHTML=metric(names.length,'Stations to assess')+metric(usable.length,'Ready for paper review')+metric(all.length-usable.length,'Comparisons needing investigation')+metric(state.adjustments.length,'Your recorded forecasts');
drawReviewCards(all);
$('city-rows').innerHTML=names.map(name=>{const t=entry('temperature',name),l=entry('temperature_low',name),r=entry('rain',name),d=t.day,ld=l.day,rd=r.day;const degraded=(d&&d.data_quality!=='ok')||(rd&&rd.data_quality!=='ok')||(ld&&ld.data_quality!=='ok');return `<tr><td><button class="city-button" data-city="${esc(name)}">${esc(name)}</button><span class="sub">High: ${esc(t.city?.icao||'—')} · Low: ${esc(l.city?.icao||'—')} · Rain: ${esc(r.city?.icao||'—')}</span></td><td>${num(d?.distribution?.median)}°<span class="sub">${num(d?.distribution?.p10,0)}–${num(d?.distribution?.p90,0)}° · 80%</span></td><td>${num(d?.market_forecast?.median)}°</td><td>${num(d?.observed?.max_f)}°</td><td><button class="city-button" data-city="${esc(name)}" data-kind="temperature_low">${num(ld?.distribution?.median)}°</button><span class="sub">${num(ld?.distribution?.p10,0)}–${num(ld?.distribution?.p90,0)}° · 80%<br>Market: ${num(ld?.market_forecast?.median)}° · Observed: ${num(ld?.observed?.min_f)}°</span></td><td>${pct(rd?.consensus)}</td><td>${rd?.market?.mid==null?'—':num(rd.market.mid,0)+'%'}</td><td><span class="badge ${degraded?'warn':''}">${degraded?'Partial':'Available'}</span><span class="sub">Rain: ${esc(ageText(r.board?.generated_at))}<br>Highs: ${esc(ageText(t.board?.generated_at))}<br>Lows: ${esc(ageText(l.board?.generated_at))}</span></td></tr>`;}).join('')||'<tr><td colspan="8">No city data matches this view.</td></tr>';
const show=$('research').checked;const rows=all.filter(r=>show||!r.reasons.length).sort((a,b)=>D.reviewRank(b)-D.reviewRank(a));
$('signals').innerHTML=rows.map(r=>`<tr><td><button class="city-button" data-city="${esc(r.city)}" data-kind="${r.kind}">${esc(r.city)}</button></td><td>${esc(D.outcome(r.kind,r.bracket,r.e.side))}</td><td>${r.e.side} / ${num(r.e.price,2)}¢</td><td>${pct(r.e.side==='YES'?r.bracket.model_p:1-r.bracket.model_p)}</td><td class="delta">${num(r.e.ev_cents,2)}¢</td><td>${num(r.e.depth,0)}</td><td>${!r.reasons.length&&r.e.suggested_contracts?num(r.e.suggested_contracts,0)+' / $'+num(r.e.suggested_cost_dollars,2):'—'}</td><td><span class="badge ${r.reasons.length?'warn':''}">${esc(D.nextStep(r.reasons,r.e).label)}</span><span class="sub">${esc(r.reasons.slice(0,2).join('; '))}</span>${r.reasons.length>2?`<details><summary>All ${r.reasons.length} checks</summary>${r.reasons.map(esc).join('<br>')}</details>`:''}</td></tr>`).join('')||'<tr><td colspan="8">No signals meet the selected criteria. Station forecasts remain available above.</td></tr>';
}
function mosChartSeries(day,city) {
 const mos=day.station_guidance?.MOS;
 if(!mos||!['ok','stale'].includes(mos.status)||mos.station!==city.icao)return null;
 const start=Date.parse(day.window_start),end=Date.parse(day.window_end);
 const points=(mos.points||[]).filter(p=>Number.isFinite(p.temperature_f)&&Number.isFinite(Date.parse(p.valid_at))&&Date.parse(p.valid_at)>=start&&Date.parse(p.valid_at)<end)
   .map(p=>({time:new Date(p.valid_at).toISOString(),median:p.temperature_f})).sort((a,b)=>Date.parse(a.time)-Date.parse(b.time));
 if(!points.length)return null;
 const stale=mos.status==='stale'||M.age(mos.issued_at)>720;
 return {name:`GFS MOS TMP (native samples${stale?'; stale':''})`,points,color:'#9c3f10',markers:true};
}
function mosChartExtrema(day,city) {
 const mos=day.station_guidance?.MOS;
 if(!mos||mos.station!==city.icao||!['ok','stale','out_of_range'].includes(mos.status))return [];
 const extrema=mos.mos_extrema??(mos.mos_maximum?[mos.mos_maximum]:[]);
 return extrema.filter(e=>['maximum','minimum'].includes(e.kind)&&Number.isFinite(e.temperature_f)&&Date.parse(e.period_end)>Date.parse(e.period_start))
  .map(e=>({...e,start:Math.max(Date.parse(e.period_start),Date.parse(day.window_start)),end:Math.min(Date.parse(e.period_end),Date.parse(day.window_end)),label:e.kind==='maximum'?'X':'N',color:e.kind==='maximum'?'#a33138':'#245ba5',stale:mos.status==='stale'||M.age(mos.issued_at)>720}))
  .filter(e=>e.end>e.start);
}
function chart(series,observed,tz,extrema=[]){const all=series.flatMap(s=>s.points).concat(observed.map(p=>({time:p.time,median:p.temperature_f})),extrema.flatMap(e=>[{time:new Date(e.start).toISOString(),median:e.temperature_f},{time:new Date(e.end).toISOString(),median:e.temperature_f}])).filter(p=>Number.isFinite(p.median));if(!all.length)return empty('Hourly guidance is not available for this snapshot.');
const W=820,H=310,L=48,R=16,T=16,B=40,times=all.map(p=>Date.parse(p.time)),values=all.map(p=>p.median);const xmin=Math.min(...times),xmax=Math.max(...times),ymin=Math.floor(Math.min(...values)-2),ymax=Math.ceil(Math.max(...values)+2),x=t=>L+(Date.parse(t)-xmin)/(xmax-xmin||1)*(W-L-R),y=v=>H-B-(v-ymin)/(ymax-ymin||1)*(H-B-T);
let s=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Hourly model temperatures and observed station temperatures"><title>Hourly model forecasts and station observations</title>`;
for(let i=0;i<=4;i++){const v=ymin+(ymax-ymin)*i/4;s+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="#e0e7e1"/><text x="${L-8}" y="${y(v)+4}" text-anchor="end" fill="#637470" font-size="11">${v.toFixed(0)}°</text>`;}
for(let i=0;i<=6;i++){const t=new Date(xmin+(xmax-xmin)*i/6),xx=L+(W-L-R)*i/6;const label=t.toLocaleTimeString('en-US',{hour:'numeric',timeZone:tz});s+=`<text x="${xx}" y="${H-14}" text-anchor="middle" font-size="11" fill="#637470">${esc(label)}</text>`;}
series.forEach((a,i)=>{const color=a.color||colors[i%colors.length];if(a.markers){s+=a.points.map(p=>`<circle cx="${x(p.time)}" cy="${y(p.median)}" r="4" fill="${color}" stroke="white" stroke-width="1"><title>${esc(a.name)}: ${esc(num(p.median))}°F at ${esc(new Date(p.time).toLocaleString('en-US',{timeZone:tz}))}</title></circle>`).join('');}else{s+=`<polyline points="${a.points.filter(p=>Number.isFinite(p.median)).map(p=>`${x(p.time)},${y(p.median)}`).join(' ')}" fill="none" stroke="${color}" stroke-width="2"/>`;}});
s+=`<polyline points="${observed.map(p=>`${x(p.time)},${y(p.temperature_f)}`).join(' ')}" fill="none" stroke="#1f3231" stroke-width="3"/>`;
for(const e of extrema){const a=x(new Date(e.start).toISOString()),b=x(new Date(e.end).toISOString()),yy=y(e.temperature_f);s+=`<g><title>MOS ${esc(e.label)} ${num(e.temperature_f)}°F${e.stale?' (stale)':''}: ${esc(new Date(e.period_start).toLocaleString('en-US',{timeZone:tz}))} to ${esc(new Date(e.period_end).toLocaleString('en-US',{timeZone:tz}))}. Period extremum, not an hourly temperature.</title><line x1="${a}" x2="${b}" y1="${yy}" y2="${yy}" stroke="${e.color}" stroke-width="2" stroke-dasharray="6 4"/><text x="${(a+b)/2}" y="${yy-7}" text-anchor="middle" fill="${e.color}" stroke="white" stroke-width="3" paint-order="stroke" font-size="12">${esc(e.label)} ${num(e.temperature_f,0)}°${e.stale?' (stale)':''}</text></g>`;}
return s+'</svg>';}
function drawDetail(){const {board,city,day:d}=current();$('city-select').value=state.city;$('kind-select').value=state.kind;
if(!d){$('station-title').innerHTML=empty('No forecast is available for this station, product, and reporting day.');['weather-briefing','station-metrics','hourly-chart','chart-legend','hourly-values','changes','settlement','brackets','source-status','model-inputs','station-guidance'].forEach(id=>$(id).innerHTML='');$('adjustment-form').hidden=true;document.querySelector('.practice-panel').hidden=true;return;}$('adjustment-form').hidden=false;document.querySelector('.practice-panel').hidden=false;drawWeatherBriefing(d,city);initPractice();
$('station-title').innerHTML=`<h2>${esc(city.city)} <span class="muted">${esc(city.icao)} · ${esc(d.date)}</span></h2>`;
$('station-metrics').innerHTML=isTemperature(state.kind)?metric(num(d.distribution.median)+'°F','Forecast '+temperatureLabel(state.kind))+metric(num(d.market_forecast?.median)+'°F','Market-implied '+temperatureLabel(state.kind))+metric(num(d.observed?.[state.kind==='temperature_low'?'min_f':'max_f'])+'°F',state.kind==='temperature_low'?'Observed minimum':'Observed maximum')+metric(num(d.distribution.p10)+'–'+num(d.distribution.p90)+'°','80% forecast interval'):metric(pct(d.consensus),'Forecast rain probability')+metric(num(d.market.mid)+'%','Market midpoint')+metric(d.observed?.precip_mm==null?'Unknown':num(d.observed.precip_mm,2)+' mm','Observed accumulation')+metric(d.observed?.precip_complete?'Adequate':'Incomplete','Precipitation coverage');
if(!draft)$('pop').value=Math.round((d.consensus??.5)*100);
drawModelInputs(d);drawStationGuidance(d,city);drawForecastExDetail();
const sources=Object.entries(d.sources||{});if(d.weathernext?.hourly?.length)sources.push(['WeatherNext 2 (research)',d.weathernext]);const series=sources.map(([name,v])=>({name,points:v.hourly||[]}));const mos=mosChartSeries(d,city);if(mos)series.push(mos);const extrema=mosChartExtrema(d,city);$('hourly-chart').innerHTML=chart(series,d.observed?.hourly||[],city.reporting_tz||city.tz,extrema);$('chart-note').textContent=`Model member medians; dark line shows station observations. Times use ${city.reporting_tz||city.tz} reporting time. ${mos?'MOS dots show native TMP samples without interpolation; dashed X/N bars show explicit daytime maxima and nighttime minima over their valid periods, not the time the extreme occurs.':'MOS samples are unavailable for this station and reporting day; see the MOS panel for status.'}`;
$('chart-legend').innerHTML=series.map((s,i)=>`<span><i class="swatch" style="background:${s.color||colors[i%colors.length]}"></i>${esc(s.name)}</span>`).join('')+'<span><i class="swatch" style="background:#1f3231"></i>Observed</span>'+extrema.filter((e,i,all)=>all.findIndex(x=>x.label===e.label)===i).map(e=>`<span><i class="swatch" style="background:${e.color}"></i>MOS ${e.label}: ${e.kind} (dashed period)</span>`).join('');
const points=[...new Set(series.flatMap(s=>s.points.map(p=>new Date(p.time).toISOString())))].sort();$('hourly-values').innerHTML=`<table><thead><tr><th>Time</th>${series.map(s=>`<th>${esc(s.name)} °F</th>`).join('')}</tr></thead><tbody>${points.map(t=>`<tr><td>${esc(new Date(t).toLocaleTimeString('en-US',{hour:'numeric',timeZone:city.reporting_tz||city.tz}))}</td>${series.map(s=>`<td>${num(s.points.find(p=>Date.parse(p.time)===Date.parse(t))?.median)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
$('changes').innerHTML=`<p>${esc(d.changes?.summary||'First comparable snapshot')}</p><p class="muted">${esc(d.diagnostics?._intraday_method==='remaining_hour_ensembles'?'Current observations and remaining-hour ensembles determine the daily '+temperatureLabel(state.kind)+'.':d.obs_effect==='remaining_hours'?'Rain probability uses remaining hours in ensemble trajectories consistent with the observations.':'Full-day guidance; no reliable intraday conditioning available.')}</p>`;
const spec=d.settlement||{};$('settlement').innerHTML=`<p>${esc(spec.station||city.station)} · ${esc(spec.source||'Unconfirmed source')}</p><p>${esc(spec.threshold||'')}</p><p class="muted">${esc(time(d.window_start))} to ${esc(time(d.window_end))}</p><p><span class="badge ${spec.verified?'':'warn'}">${spec.verified?'Definition verified':'Confirmation needed'}</span></p><p>${esc((spec.reasons||[]).join('; '))}</p>${spec.rules_url?`<a href="${esc(spec.rules_url)}" target="_blank" rel="noopener">Contract rules</a>`:''}`;
$('bracket-title').textContent=isTemperature(state.kind)?'Temperature brackets':'Rain market';
const brackets=d.ladder||[{label:'Measurable rain',model_p:d.consensus,implied:d.market.mid==null?null:d.market.mid/100,market:d.market,edge:d.edge}];
$('brackets').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Outcome</th><th>Model</th><th>Market</th><th>YES bid / ask</th><th>Change</th></tr></thead><tbody>${brackets.map(b=>`<tr><td>${esc(b.label)}</td><td><span class="bar" style="width:${Math.max(0,b.model_p||0)*130}px"></span>${pct(b.model_p)}</td><td>${pct(b.implied)}</td><td>${num(b.market.yes_bid,2)} / ${num(b.market.yes_ask,2)}¢</td><td>${esc(b.changes?.summary||'—')}</td></tr>`).join('')}</tbody></table></div>`;
const quotes=brackets.map(b=>b.market);$('source-status').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Source</th><th>Retrieved / observed</th><th>Model run</th><th>Coverage</th></tr></thead><tbody>${sources.map(([name,v])=>`<tr><td>${esc(name)}</td><td>${esc(ageText(v.retrieved_at))}</td><td>${v.model_run_at?esc(time(v.model_run_at)):'Not supplied by provider'}</td><td>${v.member_count} members</td></tr>`).join('')}<tr><td>Station observations</td><td>${esc(ageText(d.observed?.latest_at))}</td><td>Observed, not modeled</td><td>${d.observed?pct(d.observed.coverage):'Unknown'}</td></tr><tr><td>Market quotes</td><td>${esc(ageText(quotes.map(q=>q.retrieved_at).filter(Boolean).sort()[0]))}</td><td>Snapshot</td><td>${quotes.filter(q=>q.executable).length}/${quotes.length} executable books</td></tr></tbody></table></div>`;
$('source-status').innerHTML+=meteoblueStatus(board,d);
$('shift-label').hidden=$('spread-label').hidden=state.kind==='rain';$('pop-label').hidden=state.kind!=='rain';$('shift').required=$('spread').required=isTemperature(state.kind);$('pop').required=state.kind==='rain';previewAdjustment();}
function previewAdjustment(){const {board,day:d}=draftContext();if(!d)return;const stale=M.age(board.generated_at)>180||Date.now()>=Date.parse(d.window_end);$('adjustment-form').querySelector('[type=submit]').disabled=stale;$('adjustment-note').textContent=stale?'Refresh to a current snapshot before saving an adjustment.':'Preview anchored to '+time(board.generated_at)+'. The original automated forecast will remain unchanged.';
if(state.kind==='rain'){$('adjustment-preview').innerHTML=`<p>Automated: ${pct(d.consensus)} · Your preview: ${num(Number($('pop').value),0)}%</p>`;return;}
const shift=Number($('shift').value),spread=Number($('spread').value);if(!Number.isFinite(shift)||!Number.isFinite(spread))return;const v=M.adjust(d.distribution.quantiles,shift,spread,d.distribution.floor,d.distribution.ceiling);$('adjustment-preview').innerHTML=`<p>Automated ${temperatureLabel(state.kind)}: ${num(d.distribution.median)}°F · Your preview: ${num(v[7])}°F (${num(v[3])}–${num(v[11])}°, 80% interval)</p><div class="table-wrap"><table><thead><tr><th>Bracket</th><th>Automated</th><th>Your forecast</th></tr></thead><tbody>${d.ladder.map(b=>`<tr><td>${esc(b.label)}</td><td>${pct(b.model_p)}</td><td>${pct(M.between(v,b.lo,b.hi,d.distribution.floor,d.distribution.ceiling))}</td></tr>`).join('')}</tbody></table></div>`;}
function saveAdjustment(event){event.preventDefault();const {board,day:d}=draftContext();if(!d||M.age(board.generated_at)>180||Date.now()>=Date.parse(d.window_end))return;const p={snapshot_id:board.snapshot_id,city:state.city,date:d.date,kind:state.kind,reason:$('reason').value.trim()};if(p.reason.length<10)return;if(isTemperature(state.kind)){p.shift_f=Number($('shift').value);p.spread_factor=Number($('spread').value);}else p.pop_percent=Number($('pop').value);
const title=`Forecast adjustment: ${state.city} ${d.date}`;const body='Record this forecast adjustment against the archived snapshot.\n\n```json\n'+JSON.stringify(p,null,2)+'\n```';const url='https://github.com/cyclonecizek/KalshiWeather/issues/new?title='+encodeURIComponent(title)+'&body='+encodeURIComponent(body);
const a=document.createElement('a');a.href=url;a.target='_blank';a.rel='noopener';a.click();$('adjustment-message').textContent='Submit the prefilled issue in GitHub to save. It will appear here after the adjustment workflow and next refresh.';}
function drawPerformance(){drawForecastExPerformance();drawCalibrationProgress();const h=$('horizon').value,kind=$('perf-kind').value;const groups=(state.performance?.groups||[]).filter(g=>g.kind===kind&&(h==='all'||g.horizon===h));const records=(state.performance?.records||[]).filter(g=>g.kind===kind&&(h==='all'||g.horizon===h));const n=records.length,dates=new Set(records.map(r=>r.date)).size,avg=k=>n?records.reduce((s,r)=>s+r[k],0)/n:null;const exact=records.filter(r=>r.covered80!==null);$('performance-summary').innerHTML=metric(dates,'Distinct forecast dates')+metric(n,'City/date/horizon snapshots')+metric(num(avg('brier'),3),'Model Brier score')+metric(num(avg('market_brier'),3),'Market Brier score')+metric(exact.length?pct(exact.reduce((s,r)=>s+Number(r.covered80),0)/exact.length):'—','80% interval coverage');$('performance-empty').innerHTML=n?'':empty('No settled forecasts from this model version yet. Scores will populate as archived forecasts settle. Earlier model versions are excluded.');
$('performance-rows').innerHTML=groups.map(g=>`<tr><td>${esc(g.city)}</td><td>${esc(g.horizon.replace('_',' '))}</td><td>${g.distinct_dates??g.n}</td><td>${num(g.brier,3)}</td><td>${num(g.market_brier,3)}</td><td>${pct(g.brier_skill)}</td><td>${num(g.bias)}</td><td>${pct(g.coverage80)}</td></tr>`).join('');
const correction=(state.performance?.observation_ml?.records||[]).filter(r=>kind==='temperature'&&(h==='all'||r.horizon===h));
const correctionDates=[...new Set(correction.map(r=>r.date))];
const dateMean=key=>correctionDates.length?correctionDates.reduce((sum,date)=>{const rows=correction.filter(r=>r.date===date);return sum+rows.reduce((s,r)=>s+Number(r[key]),0)/rows.length;},0)/correctionDates.length:null;
$('observation-ml-performance').innerHTML=`<h3>Observation-trained correction · research only</h3><p>${correctionDates.length} prospectively scored dates. MAE: ${num(dateMean('mae'))}°F versus ${num(dateMean('baseline_mae'))}°F for the current forecast. Brier: ${num(dateMean('brier'),3)} versus ${num(dateMean('baseline_brier'),3)}. Experimental 80% coverage: ${pct(dateMean('covered80'))} versus ${pct(dateMean('baseline_covered80'))}. Each date has equal weight. Missing historical candidates are not backfilled.</p>`;
const pairs=records.flatMap(r=>r.pairs),bins=[];for(let i=0;i<10;i++){const values=pairs.filter(([p])=>Math.min(9,Math.floor(p*10))===i);if(values.length)bins.push({n:values.length,p:values.reduce((s,v)=>s+v[0],0)/values.length,y:values.reduce((s,v)=>s+v[1],0)/values.length});}
$('reliability').innerHTML=bins.length?`<svg viewBox="0 0 420 290" role="img" aria-label="Predicted probability versus observed frequency"><line x1="45" y1="245" x2="385" y2="20" stroke="#adbcb3" stroke-dasharray="5 5"/><line x1="45" y1="245" x2="385" y2="245" stroke="#9cafa5"/><line x1="45" y1="245" x2="45" y2="20" stroke="#9cafa5"/>${[0,.25,.5,.75,1].map(p=>`<text x="${45+p*340}" y="263" text-anchor="middle" font-size="10">${pct(p)}</text><text x="35" y="${249-p*225}" text-anchor="end" font-size="10">${pct(p)}</text>`).join('')}${bins.map(b=>`<circle cx="${45+b.p*340}" cy="${245-b.y*225}" r="${Math.min(12,4+Math.sqrt(b.n))}" fill="#23627c"><title>${pct(b.p)} forecast, ${pct(b.y)} observed, n=${b.n}</title></circle>`).join('')}<text x="210" y="286" text-anchor="middle" font-size="11">Forecast probability</text></svg>`:empty('Calibration points appear after settlement.');
drawModelResearch();}

function drawJournal(){const scores=new Map((state.performance?.adjustments||[]).map(x=>[x.id,x]));$('adjustment-history').innerHTML=state.adjustments.slice().reverse().map(a=>{const s=scores.get(a.id);return `<article class="journal-card"><h3>${esc(a.city)} · ${esc(a.date)} · ${esc(a.kind)}</h3><p>${esc(a.reason)}</p><p class="muted">${esc(a.author)} · ${esc(time(a.created_at))} · ${esc(a.id)}</p><p>${s?`Brier: automated ${num(s.automatic_brier,3)} · adjusted ${num(s.adjusted_brier,3)}`:'Awaiting final settlement'}</p></article>`;}).join('')||empty('No saved adjustments yet. Open a station to record your forecast and reasoning.');$('paper-history').innerHTML=state.paper.length?`<div class="table-wrap"><table><thead><tr><th>Created</th><th>City</th><th>Contract</th><th>Side</th><th>Quantity</th><th>Cost + fee</th><th>Status</th></tr></thead><tbody>${state.paper.slice().reverse().map(o=>`<tr><td>${esc(time(o.created_at))}</td><td>${esc(o.city)}</td><td>${esc(o.ticker)}</td><td>${esc(o.side)}</td><td>${num(o.quantity,0)}</td><td>$${num(o.cost_dollars,2)}</td><td>Proposed · no fill assumed</td></tr>`).join('')}</tbody></table></div>`:empty('No paper orders qualify yet. Settlement verification and calibration must pass before proposals are recorded.');}
// Issuance, observation reports, and price refreshes have independent clocks.
function freshnessTime(stamp) {
  if (!stamp || !Number.isFinite(Date.parse(stamp))) return 'Unavailable';
  if (Date.parse(stamp) > Date.now()) return 'Timestamp in future';
  return `<time datetime="${esc(stamp)}" title="${esc(time(stamp))}">${esc(ageText(stamp))}</time>`;
}
function observationFreshness(board) {
  const reports=(board?.cities||[]).map(c=>c.days?.['0']?.observed?.latest_at);
  if (!reports.length) return 'Unavailable';
  const valid=reports.filter(s=>s && Number.isFinite(Date.parse(s)) && Date.parse(s)<=Date.now()).sort((a,b)=>Date.parse(a)-Date.parse(b));
  const missing=reports.length-valid.length;
  if (!valid.length) return `Unavailable (${reports.length} station reports)`;
  const oldest=valid[0],newest=valid[valid.length-1];
  const ages=Date.parse(oldest)===Date.parse(newest)?freshnessTime(oldest):`${freshnessTime(newest)} to ${freshnessTime(oldest)}`;
  return ages+(missing?`<span class="freshness-missing">${missing}/${reports.length} station reports unavailable</span>`:'');
}
function priceFreshness(board) {
  const quotes=(board?.cities||[]).flatMap(c=>Object.values(c.days||{})).flatMap(d=>d.ladder?d.ladder.map(b=>b.market):[d.market]).filter(q=>q?.ticker);
  const valid=quotes.map(q=>q.retrieved_at).filter(s=>s && Number.isFinite(Date.parse(s)) && Date.parse(s)<=Date.now()).sort((a,b)=>Date.parse(a)-Date.parse(b));
  if (!valid.length) return 'Unavailable';
  const missing=quotes.length-valid.length;
  return freshnessTime(valid[0])+(missing?`<span class="freshness-missing">${missing}/${quotes.length} price timestamps unavailable</span>`:'');
}
function drawFreshness() {
  const products=['rain','temperature','temperature_low'];
  const row=(label,values)=>`<tr><th scope="row">${label}</th>${values.map(v=>`<td>${v}</td>`).join('')}</tr>`;
  $('updated').innerHTML=`<table class="freshness-table"><caption>Data freshness</caption><thead><tr><th scope="col">Age</th><th scope="col">Rain</th><th scope="col">Highs</th><th scope="col">Lows</th></tr></thead><tbody>${
    row('Forecast issued',products.map(k=>freshnessTime(state[k]?.generated_at)))+
    row('Observed reports · today',products.map(k=>observationFreshness(state[k])))+
    row('Oldest market price',products.map(k=>priceFreshness(state[k])))
  }</tbody></table><p class="freshness-note">Forecast builds scheduled hourly; delays are possible. Observation ages span stations; prices show the oldest market retrieval across both days. Provider model-run times and individual prices are in Station workup.</p>`;
}
function drawStatus(){const notices=[];for(const kind of ['rain','temperature','temperature_low'])if(state.refreshErrors[kind])notices.push(kind+' refresh failed; showing the last available snapshot.');for(const kind of ['rain','temperature','temperature_low']){const b=state[kind];if(!b){notices.push(`${productLabel(kind)} board unavailable.`);continue;}const m=M.age(b.generated_at);if(m>180)notices.push(`${productLabel(kind)} board is ${ageText(b.generated_at)}. Suggestions are disabled.`);if(b.quote_refresh?.failures)notices.push(`${b.quote_refresh.failures} ${kind} quote refresh(es) failed; affected positions are disabled.`);if(b.errors?.length)notices.push(`${kind}: ${b.errors.length} source warning(s). Some forecasts may be incomplete.`);}$('status').innerHTML=notices.map(n=>`<div class="notice">${esc(n)}</div>`).join('');if(state.status?.status==='degraded')$('status').innerHTML+='<div class="notice">The most recent update was partial. Last usable forecasts remain visible with their original timestamps.</div>';drawFreshness();}
function drawForecastExOverview(){
 if(typeof ForecastExView==='undefined')return;
 const rows=(state.forecastex?.records||[]).filter(r=>{const d=entry(r.kind,r.city).day;return d&&r.date===d.date&&filteredCities().includes(r.city);});
 $('forecastex-overview').innerHTML=ForecastExView.overview(rows,Date.now(),!!state.refreshErrors.forecastex);
}
function drawForecastExDetail(){
 if(typeof ForecastExView==='undefined')return;
 const {board,city,day}=current();
 const r=(state.forecastex?.records||[]).find(r=>r.station===city?.icao&&r.kind===state.kind&&r.date===day?.date);
 $('forecastex-detail').innerHTML=ForecastExView.detail(r,Date.now(),board?.snapshot_id,!!state.refreshErrors.forecastex);
}
function drawForecastExPerformance(){
 if(typeof ForecastExView==='undefined')return;
 $('forecastex-performance').innerHTML=ForecastExView.performance(state.forecastexVerification,$('perf-kind').value,$('horizon').value);
}
function render(){drawStatus();drawBoard();if(state.view==='detail')drawDetail();if(state.view==='performance')drawPerformance();if(state.view==='journal')drawJournal();}
async function load(){if(state.loading)return;state.loading=true;$('refresh').disabled=true;const paths={forecastex:'forecastex.json',forecastexVerification:'forecastex_verification.json',rain:'board.json',temperature:'board_temp.json',temperature_low:'board_low.json',performance:'performance.json',modelResearch:'model_research.json',calibrationReview:'calibration_review.json',outcomes:'outcomes.json',adjustments:'adjustments.json',paper:'paper/ledger.json',status:'status.json'};await Promise.all(Object.entries(paths).map(async([key,path])=>{try{const r=await fetch('data/'+path+'?t='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error(r.status);const data=await r.json();if(['rain','temperature','temperature_low'].includes(key)&&data.schema_version!==2)throw Error('Old data schema');state[key]=data;delete state.refreshErrors[key];}catch(error){state.refreshErrors[key]=String(error.message||error);if(['rain','temperature','temperature_low'].includes(key))console.warn(key+' refresh failed; retaining prior snapshot');}}));const names=cities();if(!names.includes(state.city))state.city=names[0]||'';$('city-select').innerHTML=names.map(n=>`<option>${esc(n)}</option>`).join('');state.loading=false;state.lastLoad=Date.now();$('refresh').disabled=false;render();}
document.querySelectorAll('nav button').forEach(b=>b.addEventListener('click',()=>view(b.dataset.view)));
$('refresh').addEventListener('click',load);$('search').addEventListener('input',drawBoard);$('research').addEventListener('change',drawBoard);
for(const [id,day]of [['today','0'],['tomorrow','1']])$(id).addEventListener('click',()=>{resetDraft();state.day=day;$('today').setAttribute('aria-pressed',day==='0');$('tomorrow').setAttribute('aria-pressed',day==='1');render();});
document.addEventListener('click',e=>{const b=e.target.closest('[data-city]');if(!b)return;resetDraft();state.city=b.dataset.city;state.kind=b.dataset.kind||'temperature';view('detail');window.scrollTo({top:0,behavior:'smooth'});});
$('city-select').addEventListener('change',e=>{resetDraft();state.city=e.target.value;drawDetail();});$('kind-select').addEventListener('change',e=>{resetDraft();state.kind=e.target.value;drawDetail();});
['shift','spread','pop','reason'].forEach(id=>$(id).addEventListener('input',()=>{if(!draft)draft=structuredClone(current());previewAdjustment();}));$('adjustment-form').addEventListener('submit',saveAdjustment);$('reset-adjustment').addEventListener('click',()=>{resetDraft();$('pop').value=Math.round((current().day?.consensus||.5)*100);previewAdjustment();});
$('research-city').addEventListener('change',drawModelResearch);
['horizon','perf-kind'].forEach(id=>$(id).addEventListener('change',drawPerformance));
setInterval(()=>{if(!document.hidden)load();},60000);setInterval(()=>{drawStatus();drawBoard();if(state.view==='detail')drawPractice();},15000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)load();});load();

// Forecaster-facing interpretation; the server's eligibility remains authoritative.
function drawReviewCards(all) {
  const ready = all.filter(r => !r.reasons.length).length;
  $('decision-overview').innerHTML = ready
    ? `<p>${ready} comparisons pass the automated checks for paper practice. Begin with the weather reasoning before considering the price.</p>`
    : '<p>No comparisons are ready for paper proposals right now. Your next useful action is to inspect a station, record your forecast, and resolve the listed data or verification gaps.</p>';
  const seen = new Set();
  const selected = all.slice().sort((a,b) => D.reviewRank(b)-D.reviewRank(a)).filter(r => {
    const key = r.city+'|'+r.kind;
    if (seen.has(key)) return false;
    seen.add(key); return true;
  }).slice(0,4);
  $('review-cards').innerHTML = selected.map(r => {
    const action = D.nextStep(r.reasons,r.e);
    const chance = r.e.side === 'YES' ? r.bracket.model_p : 1-r.bracket.model_p;
    const costs = D.example(chance,r.e.price,1,r.e.fee_rate);
    const weather = isTemperature(r.kind)
      ? `Forecast ${temperatureLabel(r.kind)} ${num(r.d.distribution.median)}°F; central 80% range ${num(r.d.distribution.p10,0)}–${num(r.d.distribution.p90,0)}°F.`
      : `${pct(r.d.consensus)} chance of measurable rain at the station during this reporting day.`;
    return `<article class="review-card"><div class="card-top"><span class="eyebrow">${esc(r.city)} · ${productLabel(r.kind).toUpperCase()}</span><span class="badge ${action.tone==='ready'?'':'warn'}">${esc(action.label)}</span></div><h3>${esc(D.outcome(r.kind,r.bracket,r.e.side))}</h3><p>${esc(weather)}</p><dl class="comparison"><div><dt>Model chance of this position winning</dt><dd>${pct(chance)}</dd></div><div><dt>Probability needed to break even</dt><dd>${costs?num(costs.breakEven*100,1)+'%':'Fee estimate unavailable'}</dd></div></dl><p class="muted">${num(r.e.price,2)}¢ per contract before fees · ${esc(ageText(r.q.retrieved_at))}. A price difference is a hypothesis to verify.</p><p class="next-action">Next: ${esc(action.tasks[0])}</p><button class="primary" data-city="${esc(r.city)}" data-kind="${r.kind}">Work through this station</button></article>`;
  }).join('') || empty('No priced comparisons are available for this view. Select a station below to assess the weather first.');
}

function drawWeatherBriefing(d,city) {
  let headline, facts, challenge;
  if (isTemperature(state.kind)) {
    const peak = d.ladder.slice().sort((a,b)=>b.model_p-a.model_p)[0];
    headline = `Daily ${temperatureLabel(state.kind)} near ${num(d.distribution.median)}°F`;
    facts = `The central 80% of the forecast distribution spans ${num(d.distribution.p10)}–${num(d.distribution.p90)}°F.`;
    if (peak) facts += ` The most likely listed range is ${peak.label}, with a ${pct(peak.model_p)} model probability. Other ranges remain possible.`;
    if (d.distribution.floor != null) facts += ` Observations impose a ${num(d.distribution.floor,d.distribution.ceiling)}°F lower bound after the reporting allowance.`;
    if (d.distribution.ceiling != null) facts += ` Observations impose a ${num(d.distribution.ceiling)}°F upper bound after the reporting allowance.`;
    if (state.kind==='temperature_low') facts += ' This is the minimum over the full reporting day, not just tonight’s overnight low. Lows build a separate verification record.';
    const market = d.market_forecast?.median;
    if (Number.isFinite(market)) facts += ` The model median is ${num(Math.abs(d.distribution.median-market))}°F ${d.distribution.median>=market?'warmer':'cooler'} than traders’ implied median. This does not identify a profitable bracket by itself.`;
    challenge = state.kind==='temperature_low' ? 'Could clearing skies, lighter winds, cold advection, or a late front bring a lower temperature before the reporting day ends? Check both the pre-dawn minimum and late-evening cooling.' : 'What could move the high across the range boundaries? Review cloud persistence, mixing, advection and frontal timing against the remaining hourly guidance.';
  } else {
    headline = `${pct(d.consensus)} chance of measurable station rain`;
    facts = 'This is a station-total event for the reporting day, not the chance of rain somewhere in the city or forecast area.';
    if (Date.now() < Date.parse(d.window_start)) facts += ' This reporting day has not started; observations for it are not expected yet.';
    else if (d.observed?.precip_complete) facts += ` Available observations indicate ${num(d.observed.precip_mm,2)} mm so far; these are provisional, not the final settlement report.`;
    else facts += 'Precipitation observation coverage is incomplete. Missing reports do not establish a dry day.';
    challenge = 'Does the precipitating area reach this station before the reporting window ends? Review radar coverage, storm motion, boundary placement, and remaining convective timing.';
  }
  const gaps = [];
  if (d.data_quality !== 'ok') gaps.push('Some observations or guidance are incomplete.');
  if (!d.settlement?.verified) gaps.push('The settlement definition still needs confirmation.');
  const families = d.n_families;
  if (Number.isFinite(families)) gaps.push(`${families} model ${families===1?'family is':'families are'} represented; shared inputs mean these are not fully independent forecasts.`);
  $('weather-briefing').innerHTML = `<article class="panel weather-brief"><p class="eyebrow">METEOROLOGICAL BRIEFING</p><h2>${esc(headline)}</h2><p>${esc(facts)}</p><div class="forecast-question"><h3>Challenge the guidance</h3><p>${esc(challenge)}</p><p class="muted">These are prompts for your analysis, not diagnosed causes. Use your own radar, satellite, surface and forecast tools.</p></div><p class="muted">${esc(gaps.join(' '))}</p><p class="muted">${esc(city.icao)} · Reporting window: ${esc(time(d.window_start))} to ${esc(time(d.window_end))}. Times shown here use your browser’s local time.</p><button id="jump-to-judgment">Record my forecast reasoning</button></article>`;
  $('jump-to-judgment').addEventListener('click',()=>{$('adjustment-form').scrollIntoView({behavior:'smooth',block:'center'});$('reason').focus({preventScroll:true});});
}

function practiceBrackets() {
  const d = practice.context.day;
  return d.ladder || [{label:'Measurable rain at the station',model_p:d.consensus,market:d.market,edge:d.edge}];
}
function initPractice(force=false) {
  const live = current();
  const key = state.city+'|'+state.kind+'|'+live.day.date;
  if (!practice || practice.key !== key || force) {
    practice = {key, context:structuredClone(live)};
    const brackets = practiceBrackets();
    $('practice-market').innerHTML = brackets.map((b,i)=>`<option value="${i}">${esc(b.label)}</option>`).join('');
    if (isTemperature(state.kind)) $('practice-market').value = String(brackets.indexOf(brackets.reduce((best,b)=>b.model_p>best.model_p?b:best)));
    $('practice-side').value='YES';
    $('practice-quantity').value=1;
    resetPracticeProbability();
  } else drawPractice();
}
function resetPracticeProbability() {
  if (!practice) return;
  const b = practiceBrackets()[Number($('practice-market').value)];
  const p = $('practice-side').value==='YES' ? b.model_p : 1-b.model_p;
  $('practice-probability').value = (100*p).toFixed(1);
  drawPractice();
}
function drawPractice() {
  if (!practice) return;
  const {board,day:d} = practice.context;
  const b = practiceBrackets()[Number($('practice-market').value)], q=b.market;
  const side = $('practice-side').value;
  const price = side==='YES' ? q.yes_ask : q.no_ask;
  const edge=b.edge || d.edge;
  const probability=$('practice-probability').valueAsNumber/100, quantity=$('practice-quantity').valueAsNumber;
  const result=D.example(probability,price,quantity,d.fee_verified?edge?.fee_rate:null);
  const reasons=M.eligibility(edge,q,board,Date.now(),d);
  const action=D.nextStep(reasons,edge||{});
  $('practice-anchor').textContent = `Example anchored to ${time(board.generated_at)}. Quote ${ageText(q.retrieved_at)}. Reset to use the latest loaded snapshot.`;
  let numbers = '';
  if (result) {
    const difference = probability-result.breakEven;
    numbers = `<div class="metrics practice-metrics">${metric('$'+num(result.cost,2),'Total purchase cost + estimated fee')}${metric('$'+num(result.winNet,2),'Net gain if this position wins')}${metric('$'+num(result.maxLoss,2),'Maximum loss if it loses')}${metric(num(result.breakEven*100,1)+'%','Probability needed to break even')}</div><p>Your ${pct(probability)} estimate is ${num(Math.abs(difference)*100,1)} percentage points ${difference>=0?'above':'below'} break-even. ${difference>0?'The example has a positive modeled average, if your probability is accurate.':'At this probability, the example does not have a positive modeled average.'}</p><p class="muted">Estimated entry fee: $${num(result.fee,2)}. Average net result under your probability: ${result.expectedNet<0?'−':'+'}$${num(Math.abs(result.expectedNet),2)}. One trade still settles as a win or loss; this average is not a promised return.</p>`;
    const depth=side==='YES'?q.yes_depth:q.no_depth;
    const confirmedDepth=Number.isFinite(depth)?depth:(edge?.side===side?edge.depth:null);
    if (!Number.isFinite(confirmedDepth) || confirmedDepth<quantity) numbers += '<p class="notice">The quote does not confirm enough contracts at this price for this example. Do not assume this quantity can be filled at the displayed cost.</p>';
  } else numbers = '<p class="notice">A complete purchase price, verified fee estimate, and valid inputs are needed. Enter a probability from 0 to 100 and a whole contract count from 1 to 1,000.</p>';
  $('practice-result').innerHTML = `<h3>${esc(D.outcome(board.kind,b,side))} <span class="muted">(${side})</span></h3><p>Buying ${side} means this position wins if ${side==='YES'?'the listed weather outcome occurs':'the listed weather outcome does not occur'}. ${side==='NO'&&isTemperature(board.kind)?'It can win on either side of the listed temperature range.':''}</p>${numbers}<div class="decision-next"><h3>${esc(action.label)}</h3><ul>${action.tasks.map(t=>`<li>${esc(t)}</li>`).join('')}</ul><p>Changing your estimate here does not clear the data checks or submit an order. To preserve your forecast, use the judgment form below.</p>${reasons.length?`<details><summary>All checks that still need attention</summary><ul>${reasons.map(r=>`<li>${esc(r)}</li>`).join('')}</ul></details>`:''}</div><p class="muted">Assumes the displayed ask, this order’s estimated entry fee, and ordinary $1/$0 settlement. Verify actual fees and rules on Kalshi. Selling early can change the result.</p><p>Market to look up: <code>${esc(q.ticker)}</code> · <a href="https://kalshi.com/" target="_blank" rel="noopener">Open Kalshi and search this ticker</a></p>`;
}

['practice-market','practice-side'].forEach(id=>$(id).addEventListener('change',resetPracticeProbability));
['practice-probability','practice-quantity'].forEach(id=>$(id).addEventListener('input',drawPractice));
$('practice-reset').addEventListener('click',()=>initPractice(true));
document.querySelectorAll('[data-view-link]').forEach(b=>b.addEventListener('click',()=>view(b.dataset.viewLink)));


function portfolioCandidates() {
  const rows=[];
  for(const kind of ['temperature','temperature_low','rain']) {
    const board=state[kind];
    for(const city of board?.cities||[]) {
      const d=city.days[state.day]; if(!d) continue;
      const brackets=d.ladder||[{market:d.market,edge:d.edge,model_p:d.consensus,label:'Measurable rain'}];
      for(const bracket of brackets) {
        const q=bracket.market, e=bracket.edge;
        if(!q || !e) continue;
        const personal=$('bet-model').value==='personal';
        const saved=personal?D.savedProbability(state.adjustments,board,city.city,d,kind,q.ticker):null;
        const yes=personal?saved?.probability:bracket.model_p;
        const rate=d.fee_verified?e.fee_rate:null;
        const yesCost=D.example(yes,q.yes_ask,1,rate), noCost=D.example(Number.isFinite(yes)?1-yes:NaN,q.no_ask,1,rate);
        const side=yesCost && (!noCost || yesCost.expectedNet>=noCost.expectedNet)?'YES':noCost?'NO':e.side;
        const p=Number.isFinite(yes)?(side==='YES'?yes:1-yes):NaN;
        const price=side==='YES'?q.yes_ask:q.no_ask;
        const reasons=M.eligibility(e,q,board,Date.now(),d).filter(r=>!/Portfolio budget exhausted|Paper order already recorded|Edge outside policy range|Insufficient confirmed depth/.test(r));
        if(e.eligibility?.eligible!==true && !e.eligibility?.reasons?.length) reasons.push('Eligibility not confirmed');
        if(d.spread_sensitivity_required){
          for(let i=reasons.length-1;i>=0;i--)if(/Spread sensitivity|Advantage disappears under alternative/.test(reasons[i]))reasons.splice(i,1);
          const check=bracket.spread_sensitivity?.[side];
          if(!check?.complete)reasons.push('Spread sensitivity unavailable');
          else if(check.fragile)reasons.push('Advantage disappears under alternative temperature distributions');
        }
        if(personal && !saved) reasons.push('Save an adjustment for this exact forecast snapshot first');
        if(personal && saved) reasons.push('Personal forecast calibration pending');
        if(!d.fee_verified) reasons.push('Series fee metadata unverified');
        if(!d.settlement?.verified) {reasons.push('Settlement definition unverified');for(const reason of d.settlement?.reasons||[]) if(/station|source|boundary|rules/i.test(reason)) reasons.push('Contract definition mismatch: '+reason);}
        if(d.data_quality!=='ok') reasons.push('Incomplete source data');
        if((d.n_guidance_centres??d.n_families??0)<2) reasons.push('Insufficient model guidance centres');
        if(d.n_guidance_centres==null && d.source_error_count>(board.execution_policy?.max_source_errors??1)) reasons.push('Too many source failures');
        if(Math.max(d.elapsed||0,(Date.now()-Date.parse(d.window_start))/(Date.parse(d.window_end)-Date.parse(d.window_start)))>(board.execution_policy?.max_elapsed??.75)) reasons.push('Reporting window nearly complete');
        if(!Number.isFinite(q.spread) || q.spread>(board.execution_policy?.max_spread_cents??5)) reasons.push('Missing or wide spread');
        if(!q.executable || !['active','open'].includes(q.status)) reasons.push('Executable open market required');
        if(!Number.isFinite(Date.parse(q.close_time))) reasons.push('Market closing time unknown');
        if(M.age(d.forecast_retrieved_at)>(board.execution_policy?.max_data_age_minutes||180)) reasons.push('Forecast stale or age unknown');
        const depth=Number.isFinite(q[side==='YES'?'yes_depth':'no_depth'])?q[side==='YES'?'yes_depth':'no_depth']:e.side===side && e.price===price?e.depth:null;
        const costs=D.example(p,price,1,d.fee_verified?e.fee_rate:null);
        if(!Number.isFinite(depth) || depth<(board.execution_policy?.min_depth??25)) reasons.push('Insufficient confirmed depth');
        if(costs && (costs.expectedNet*100>(board.execution_policy?.max_edge_cents??25) || costs.expectedNet*100<(board.execution_policy?.min_edge_cents??6))) reasons.push('Edge outside policy range');
        rows.push({city:city.city,kind,event:[city.city,kind,d.date].join('|'),ticker:q.ticker,side,
          label:D.outcome(kind,bracket,side),probability:p,price,feeRate:d.fee_verified?e.fee_rate:null,depth,
          reasons,source:personal?(saved?`Your adjustment ${saved.id}`:'No matching saved adjustment'):'Automated model',date:d.date});
      }
    }
  }
  return rows;
}
function budgetTable(plan, hypothetical=false) {
  const rows=plan.rows.filter(r=>r.contracts || r.fraction>0 || /Save an adjustment/.test(r.reasons.join(' ')));
  const table=items=>`<div class="table-wrap"><table><thead><tr><th>Station / outcome</th><th>Model chance / purchase price</th><th>${hypothetical?'Hypothetical':'Suggested'} contracts</th><th>Cost including fees</th><th>Why this amount?</th></tr></thead><tbody>${items.map(r=>`<tr><td><button class="city-button" data-city="${esc(r.city)}" data-kind="${r.kind}">${esc(r.city)}</button><span class="sub">${esc(r.date)} · ${esc(r.label)} · ${esc(r.side)}</span><span class="sub">${esc(r.ticker)} · ${esc(r.source)}</span></td><td>${pct(r.probability)} / ${num(r.price,2)}¢</td><td>${r.contracts}</td><td>$${num(r.cost,2)}<span class="sub">Includes $${num(r.fee,2)} fee</span></td><td>${r.contracts?`Positive buffered model advantage, limited by budget and available contracts. Model-based average net: $${num(r.expectedNet,2)}; actual result can lose the full cost. ${esc((r.pendingChecks||[]).join('; '))}`:esc(r.reasons.join('; '))}</td></tr>`).join('')||'<tr><td colspan="5">No positive, sizable comparisons for this forecast selection.</td></tr>'}</tbody></table></div>`;
  const funded=rows.filter(r=>r.contracts), skipped=rows.filter(r=>!r.contracts);
  const reasons=[...new Set(skipped.flatMap(r=>r.reasons))].slice(0,3);
  return (funded.length?table(funded):'')+(skipped.length?`<p>Checks to resolve: ${esc(reasons.join('; '))}.</p><details><summary>${skipped.length} comparisons receiving $0</summary>${table(skipped)}</details>`:funded.length?'':empty('No positive, sizable comparisons for this forecast selection.'));
}
function drawBudget() {
  const candidates=portfolioCandidates();
  const budget=$('bet-budget').valueAsNumber, committed=$('bet-committed').valueAsNumber;
  const paper=$('bet-mode').value==='paper';
  const practiceCandidates=candidates.map(r=>({...r,pendingChecks:r.reasons.filter(x=>/calibration|Settlement definition unverified/i.test(x)),reasons:r.reasons.filter(x=>!/calibration|Settlement definition unverified/i.test(x))}));
  const plan=D.allocate(paper?practiceCandidates:candidates,budget,committed);
  $('plan-status').textContent=paper?'Paper practice only. Settlement-window and calibration assumptions are unverified. The amounts below are simulated, not cleared for real bets.':'Verified allocations require settlement, calibration, current prices, and usable forecast data. Pending verification does not clear automatically.';
  if(!plan) {$('budget-result').innerHTML=empty('Enter a budget from $1 to $100,000 and an existing commitment between $0 and that budget.');$('budget-hypothetical').innerHTML='';return;}
  $('budget-result').innerHTML=`<div class="metrics">${metric('$'+num(plan.allocated,2),paper?'Paper allocation / simulated maximum loss':'Suggested new allocation / maximum loss')}${metric('$'+num(plan.remaining,2),'Keep uncommitted')}${metric('$'+num(plan.committed,2),'Already committed')}</div><p>${paper?'These are paper-practice sizes only. No real-money allocation is being recommended.':plan.allocated?'Review these model-based sizes at the quoted prices before deciding.':'Suggested new allocation: $0. Keep the money uncommitted until the checks below pass.'} This is a fresh plan, not additional bets on every refresh. No orders are placed or saved as fills.</p>`+budgetTable(plan,paper);
  const hypothetical=D.allocate(candidates.map(r=>({...r,reasons:r.reasons.filter(x=>!/calibration|Settlement definition unverified/i.test(x))})),budget,committed);
  $('budget-hypothetical').innerHTML=`<p>Hypothetical total: $${num(hypothetical.allocated,2)}. Cash remaining: $${num(hypothetical.remaining,2)}. These amounts are not cleared for betting.</p>`+budgetTable(hypothetical,true);
}
function meteoblueStatus(board, day) {
  if(!board) return '<p>Awaiting the next forecast update.</p>';
  const info=board.meteoblue_status;
  const message=info?.message||(board.meteoblue_published?'Detailed source diagnostics will appear after the next data update.':'Meteoblue is disabled for public display and excluded from the published model. Publication must be enabled in the repository configuration before the API is called.');
  const source=day?.meteoblue;
  const older=source&&(source.stale||(source.expires_at&&Date.parse(source.expires_at)<=Date.now()));
  const budget=info?.app_budget;
  const detail=`${budget?`<p>App calls today: ${esc(budget.calls_used)} / ${esc(budget.calls_limit)} (resets at 00 UTC). Provider credit balance is not available here.</p>`:''}${older?'<p>Older Meteoblue guidance: comparison only. Refresh before using it in a decision.</p>':''}${info?.stale_stations?`<p>Older guidance retained for comparison at ${esc(info.stale_stations)} station(s).</p>`:''}`;
  return `<p>${esc(message)}</p>${detail}${day && board.meteoblue_published && !source?'<p>No usable Meteoblue guidance for this station and reporting day.</p>':''}${source?`<p>Daily high: ${num(source.tmax)}°F · Daily low: ${num(source.tmin)}°F · Provider rain probability: ${pct(source.pop)} · Retrieved ${esc(ageText(source.retrieved_at))}.</p><p class="muted">Daily guidance has no hourly curve. Meteoblue lows are comparison only; they do not change low-market probabilities. Provider rain probability uses its own threshold and is not the final blended station probability.</p>`:''}${info?.failures?`<p>${info.failures} station request(s) failed. See the next update for recovery.</p>`:''}`;
}
function drawMeteoblueOverview() {
  $('meteoblue-overview').innerHTML=['temperature','temperature_low','rain'].map(kind=>`<p class="eyebrow">${productLabel(kind)}</p>`+meteoblueStatus(state[kind])).join('');
}
for(const id of ['bet-budget','bet-committed','bet-model','bet-mode']) $(id).addEventListener('input',drawBudget);

function drawCalibrationProgress() {
  const report=state.calibrationReview;
  if(!report){$('calibration-progress').innerHTML=empty('Review progress will appear after the next scoring update.');return;}
  const rows=Object.entries(report.groups||{}).filter(([,r])=>r.kind===$('perf-kind').value && ($('horizon').value==='all'||r.horizon===$('horizon').value));
  $('calibration-progress').innerHTML=`<p>Updated ${esc(ageText(report.generated_at))}. Approval does not alter the forecast probabilities.</p><div class="table-wrap"><table><thead><tr><th>Review key</th><th>Distinct settled dates</th><th>Status</th></tr></thead><tbody>${rows.map(([key,r])=>`<tr><td>${esc(key)}</td><td>${r.n} / ${r.required_dates}</td><td>${r.ready_for_review?'Ready for owner review':esc(r.reasons.join('; '))}</td></tr>`).join('')||'<tr><td colspan="3">No scored groups yet.</td></tr>'}</tbody></table></div>`;
}

function drawStationGuidance(d, city) {
  const guidance=d.station_guidance;
  if(!guidance || !Object.keys(guidance).length){$('station-guidance').innerHTML=empty('MOS and LAMP were not archived in this snapshot. They will appear after a new forecast build.');return;}
  const stamp=t=>t?new Date(t).toLocaleString('en-US',{timeZone:city.tz,month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false,timeZoneName:'short'}):'Not available';
  $('station-guidance').innerHTML=['MOS','LAMP'].map(name=>{
    const g=guidance[name];
    if(!g)return `<h3>${name}</h3><p>No archived guidance.</p>`;
    const points=g.points||[],periods=g.precipitation||[];
    const status=g.status==='ok'&&M.age(g.issued_at)>(name==='LAMP'?180:720)?'stale':g.status;
    const reference=state.kind==='temperature_low'?null:d.distribution?.median;
    const explicit=g.mos_maximum;
    const comparison=name==='MOS'?explicit?.temperature_f:g.sampled_max_f;
    const diff=Number.isFinite(comparison)&&Number.isFinite(reference)?comparison-reference:null;
    const challenge=status==='ok'&&diff!==null&&Math.abs(diff)>=2?`The ${name==='MOS'?'explicit MOS daytime maximum':'sampled LAMP peak'} is ${num(Math.abs(diff))}°F ${diff<0?'below':'above'} your daily blend. Check timing and coverage first, then cloud cover, mixing, and advection before changing your forecast.`:'';
    const maximum=name!=='MOS'?'':explicit?`<p>MOS daytime maximum (${esc(explicit.field)}): ${num(explicit.temperature_f)}°F.</p><p>Valid ${esc(stamp(explicit.period_start))} to ${esc(stamp(explicit.period_end))}. ${esc(explicit.period_definition)}. This is not necessarily the midnight-to-midnight high.</p>`:`<p>MOS daytime maximum (N/X or X/N): ${Object.hasOwn(g,'mos_maximum')?'not provided for this day in this bulletin':'not archived in this snapshot'}. The TMP sampled peak below is not substituted.</p>`;
    return `<h3>${name==='MOS'?'GFS MOS (MAV)':'GFS LAMP (LAV)'} · ${esc(g.station)}</h3><p>${esc(status)} · Issued ${esc(stamp(g.issued_at))} (${esc(ageText(g.issued_at))}) · Retrieved ${esc(ageText(g.retrieved_at))}</p><p>${esc(g.message||'')} ${esc(challenge)}</p>${maximum}${points.length?`<p>TMP sampled peak: ${num(g.sampled_max_f)}°F. Coverage: ${esc(stamp(g.coverage_start))} to ${esc(stamp(g.coverage_end))}.</p><p class="muted">${esc(g.coverage_note)}</p><details><summary>Temperatures, clouds, and winds at native forecast times</summary><div class="table-wrap"><table><thead><tr><th>Valid time</th><th>Temperature</th><th>Dewpoint</th><th>Cloud</th><th>Wind</th></tr></thead><tbody>${points.map(p=>`<tr><td>${esc(stamp(p.valid_at))}</td><td>${num(p.temperature_f)}°F</td><td>${num(p.dewpoint_f)}°F</td><td>${esc(p.cloud||'—')}</td><td>${num(p.wind_direction_degrees,0)}° / ${num(p.wind_speed_kt,0)} kt</td></tr>`).join('')}</tbody></table></div><p>CL clear; FW few; SC scattered; BK broken; OV overcast. MOS uses native 3- or 6-hour samples; LAMP uses hourly samples.</p></details>`:''}${periods.length?`<details><summary>Precipitation probabilities and exact periods</summary><p>PPO is occurrence at the stated hour, including traces. P01, P06, and P12 cover measurable precipitation over 1, 6, and 12 hours. These overlapping probabilities are not added or converted into a daily rain probability.</p><div class="table-wrap"><table><thead><tr><th>Element</th><th>Valid period</th><th>Probability</th><th>Reporting window</th></tr></thead><tbody>${periods.map(p=>`<tr><td>${esc(p.element)}</td><td>${p.hours?esc(stamp(p.start))+' to ':''}${esc(stamp(p.end))}</td><td>${pct(p.probability)}</td><td>${p.crosses_reporting_boundary?'Crosses boundary; full native period shown':'Within reporting day'}</td></tr>`).join('')}</tbody></table></div></details>`:''}<p class="muted">NOAA guidance via Iowa Environmental Mesonet. Archived with this forecast for future verification; no skill claim or automatic adjustment.</p>`;
  }).join('');
}

function nwsProvenance(g) {
  if(!g)return '<p>NWS timestamps and original guidance were not archived in this snapshot.</p>';
  const stamp=t=>t?`${new Date(t).toLocaleString()} (${ageText(t)})`:'Not supplied';
  return `<p>NWS forecast issue time: ${esc(stamp(g.issued_at))}. Retrieved: ${esc(stamp(g.retrieved_at))}.</p><p>Product generated: ${esc(stamp(g.product_generated_at))}. Product generation is not necessarily the forecast issue time.</p>${g.status==='failed'?`<p>Last attempt: ${esc(stamp(g.attempted_at))}.</p>`:''}${(g.periods||[]).map(p=>`<p>Valid ${esc(stamp(p.start))} to ${esc(stamp(p.end))}: ${esc(num(p.value))}${isTemperature(state.kind)?'°F':'% PoP'}. Native NWS period, not necessarily the settlement day.</p>`).join('')}`;
}

function observationCorrection(d) {
 const c=d.observation_ml;if(!c)return '';
 const values=c.features?.values;
 return `<h3>Observation-trained correction</h3><p>${esc(c.message)}. ${esc(c.training_dates)} distinct settled dates available; ${esc(c.required_dates)} required.</p>${c.status==='candidate'?`<p>Candidate high: ${num(c.median)}°F (${c.adjustment_f>=0?'+':''}${num(c.adjustment_f)}°F versus the current forecast). Experimental 80% interval: ${num(c.p10)}–${num(c.p90)}°F.</p><p>Fit dates precede ${esc(c.fit_before)}; the next ${esc(c.interval_dates)} dates calibrate the interval. Latest training date: ${esc(c.last_training_date)}. This correction is not used in market probabilities or allocations.</p>`:''}${values?`<p>Observation minus prior-issued guidance: 1-hour ${num(values[5])}°F, 3-hour ${num(values[6])}°F, 6-hour ${num(values[7])}°F. Recent warming rate: ${num(values[8])}°F/hour.</p>`:''}`;
}
function spreadDiagnostics(d) {
  if(!isTemperature(d.kind))return '';
  const budget=d.diagnostics?._spread_budget,cal=d.spread_calibration;
  if(!budget)return '<h3>Where the spread comes from</h3><p>Spread stages were not archived in this snapshot.</p>';
  const variants=Object.values(d.experiments?.variants||{}).filter(v=>v.mode==='distribution');
  return `<h3>Where the spread comes from</h3><p>${esc(budget.note)}</p><div class="table-wrap"><table><thead><tr><th>Calculation stage</th><th>80% interval width</th></tr></thead><tbody>${budget.stages.map(s=>`<tr><td>${esc(s.stage)}</td><td>${num(s.width80)}°F</td></tr>`).join('')}</tbody></table></div><details><summary>Source widths before and after observations</summary>${budget.sources.map(s=>`<p>${esc(s.model)}: ${num(s.raw_width80)}°F before; ${num(s.conditioned_width80)}°F after conditioning.</p>`).join('')}</details><h3>Alternative distributions</h3><p>Research comparisons only. The mixture averages source probabilities and retains separate scenarios; it adds no blanket or disagreement inflation. Spread ×0.75 and ×1.25 are sensitivity tests, not fitted corrections. GEM is excluded from new guidance; historical scores are retained.</p><div class="table-wrap"><table><thead><tr><th>Alternative</th><th>Median</th><th>80% interval</th></tr></thead><tbody>${variants.map(v=>`<tr><td>${esc(v.model)}</td><td>${num(v.quantiles?.[7])}°F</td><td>${num(v.quantiles?.[3])}–${num(v.quantiles?.[11])}°F</td></tr>`).join('')}</tbody></table></div>${cal?`<h3>Learned bias and spread</h3><p>${esc(cal.message)} ${cal.dates} distinct earlier dates; ${cal.required_dates} required.</p>${cal.status==='candidate'?`<p>Learned shift ${num(cal.shift_f)}°F; spread multiplier ${num(cal.spread_multiplier,2)}. Bias fitted through ${esc(cal.fit_end)}; spread calibrated from ${esc(cal.calibration_start)} through ${esc(cal.train_end)}. Subsequent forecasts are archived before settlement and scored separately.</p>`:''}`:''}<h3>Does the NO advantage survive?</h3><p>Using current quoted asks and estimated entry fees. A nonpositive advantage in any required alternative blocks verified sizing. This screen is not proof that the remaining forecasts are calibrated.</p><div class="table-wrap"><table><thead><tr><th>Bracket</th><th>Operational NO chance</th><th>Smallest alternative advantage</th><th>Check</th></tr></thead><tbody>${(d.ladder||[]).map(b=>{const s=b.spread_sensitivity?.NO;return `<tr><td>${esc(b.label)}</td><td>${pct(1-b.model_p)}</td><td>${num(s?.minimum_ev_cents,2)}¢</td><td>${!s?.complete?'Unavailable':s.fragile?'Spread-sensitive / no robust advantage':'Positive across checked alternatives'}</td></tr>`;}).join('')}</tbody></table></div>`;
}

function drawModelInputs(d) {
  const names={ECMWF_ENS:'ECMWF ensemble',GEFS:'NOAA GEFS',ICON_EPS:'DWD ICON ensemble',GEM_EPS:'Canadian GEM ensemble',UKMO_ENS:'UK Met Office ensemble',NBM:'NOAA National Blend',NBM_T:'NOAA National Blend',NDFD:'NWS forecaster guidance',METEOBLUE:'Meteoblue mLM',WEATHERNEXT2:'Google WeatherNext 2'};
  const temperature=isTemperature(state.kind);
  // Archived boards may predate explicit membership and weight metadata.
  const fallback=temperature?Object.entries(d.diagnostics||{}).filter(([k,v])=>!k.startsWith('_')&&v&&typeof v==='object').map(([model,v])=>({model,value:v.median??v.value,p10:v.p10,p90:v.p90,members:v.n,included:true,status:'Included; weight not archived'})):Object.entries(d.models||{}).map(([model,value])=>({model,value,included:true,status:'Included; weight not archived'}));
  const rows=d.model_inputs||fallback;
  if(!rows.length){$('model-inputs').innerHTML=empty('Individual model values were not archived for this forecast.');return;}
  const active=rows.filter(r=>r.included&&Number.isFinite(r.value));
  const values=active.map(r=>r.value);
  const spread=values.length>1?Math.max(...values)-Math.min(...values):null;
  const format=v=>temperature?num(v)+'°F':pct(v);
  const final=temperature?d.distribution?.median:d.consensus;
  const conditioned=temperature?d.diagnostics?._observation_used:['remaining_hours','observed'].includes(d.obs_effect);
  let note=temperature?'Values are source medians or point forecasts. Ensemble ranges are the 10th–90th percentiles before final blend corrections.':'Included values are source rain probabilities after configured probability corrections.';
  note+=' NWS guidance remains visible for comparison when excluded from the blend; its guidance weight is then zero.';
  note+=' Available models are weighted within each family, then available families are weighted. Weight is a share of that guidance calculation, not a measure of skill or independence.';
  if(state.kind==='temperature_low') note+=' Daily lows currently use full-window ensemble minima only. High-only NBM/NDFD inputs and MOS nighttime N values are not substituted for calendar-day lows.';
  if(d.weathernext) note+=' WeatherNext 2 is a research comparison with zero operational weight. Its research daily values use the same observation and calibration adjustments as the existing forecast; its hourly chart shows unadjusted guidance.';
  if(conditioned) note+=' Station observations condition this forecast; excluded full-day guidance is not used as remaining-day guidance.';
  if(d.obs_effect==='observed') note+=' Measurable rain has already been observed: the final probability uses the observation override rather than the weighted guidance average.';
  $('model-inputs').innerHTML=`<div class="metrics">${metric(format(final),'Final forecast')}${metric(active.length,'Contributing sources')}${metric(spread===null?'—':temperature?num(spread)+'°F':num(spread*100)+' percentage points','Highest minus lowest source')}</div><p>${esc(note)}</p><div class="table-wrap"><table><thead><tr><th>Source</th><th>Family</th><th>${temperature?('Daily '+temperatureLabel(state.kind)):'Rain chance'}</th><th>${temperature?'80% ensemble range':'Members'}</th><th>Guidance weight</th><th>Status</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(names[r.model]||r.model)}${temperature&&r.members?`<br><small>${esc(r.members)} members</small>`:''}</td><td>${esc(r.family||'Not archived')}</td><td>${Number.isFinite(r.value)?esc(format(r.value)):'—'}</td><td>${temperature?(Number.isFinite(r.p10)&&Number.isFinite(r.p90)?esc(num(r.p10)+'–'+num(r.p90)+'°F'):'—'):(r.members??'—')}</td><td>${Number.isFinite(r.weight)?esc(num(r.weight*100,1)+'%'):'Not archived'}</td><td>${esc(r.status)}${r.model==='NDFD'?nwsProvenance(r.nws_guidance):''}${r.model==='METEOBLUE'&&r.retrieved_at?`<p>Retrieved ${esc(ageText(r.retrieved_at))}. ${r.expires_at&&Date.parse(r.expires_at)<=Date.now()?'Older guidance; comparison only.':''}</p>`:''}</td></tr>`).join('')}</tbody></table></div><p class="muted">Compare disagreement with cloud cover, mixing, precipitation timing, and the station observations. Agreement alone does not establish forecast skill.</p>${d.weathernext?`<p>${esc(d.weathernext.note)} Retrieved ${esc(ageText(d.weathernext.retrieved_at))}. Model issue time is not supplied by this API response. <a href="https://open-meteo.com/en/docs/google-weathernext-api" target="_blank" rel="noopener">Google DeepMind WeatherNext 2 via Open-Meteo</a>.</p>`:''}`;
  $('model-inputs').innerHTML+=observationCorrection(d)+spreadDiagnostics(d);
}
