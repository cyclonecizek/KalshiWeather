'use strict';
const $=id=>document.getElementById(id), M=ForecastMath;
const D = ForecastDecision;
let practice = null;
const state={rain:null,temperature:null,performance:null,outcomes:{},adjustments:[],paper:[],status:null,refreshErrors:{},day:'0',city:'',kind:'temperature',view:'board',loading:false,lastLoad:null};
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
const cities=()=>[...new Set([...(state.rain?.cities||[]),...(state.temperature?.cities||[])].map(c=>c.city))].sort();
function entry(kind,city=state.city){const board=state[kind],c=board?.cities.find(c=>c.city===city);return {board,city:c,day:c?.days[state.day]};}
function current(){return entry(state.kind);}
function filteredCities(){const search=$('search').value.toLowerCase();return cities().filter(name=>{const r=entry('rain',name),t=entry('temperature',name);return [name,r.city?.icao,t.city?.icao].join(' ').toLowerCase().includes(search);});}
function view(name){state.view=name;document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!==name);document.querySelectorAll('nav button').forEach(b=>b.setAttribute('aria-current',b.dataset.view===name?'page':'false'));if(name==='detail')drawDetail();if(name==='performance')drawPerformance();if(name==='journal')drawJournal();}
function signals(){const rows=[];for(const kind of ['rain','temperature']){const b=state[kind];if(!b)continue;for(const c of b.cities){const d=c.days[state.day];if(!d)continue;const brackets=kind==='temperature'?d.ladder:[{market:d.market,edge:d.edge,model_p:d.consensus,label:'Rain'}];for(const bracket of brackets){const e=bracket.edge,q=bracket.market;if(!e||e.ev_cents<0)continue;const reasons=M.eligibility(e,q,b);rows.push({kind,city:c.city,d,b,q,e,bracket,reasons});}}}return rows.sort((a,b)=>b.e.ev_cents-a.e.ev_cents);}
function drawBoard(){const names=filteredCities(),all=signals().filter(r=>names.includes(r.city)),usable=all.filter(r=>!r.reasons.length);
$('summary').innerHTML=metric(names.length,'Stations to assess')+metric(usable.length,'Ready for paper review')+metric(all.length-usable.length,'Comparisons needing investigation')+metric(state.adjustments.length,'Your recorded forecasts');
drawReviewCards(all);
$('city-rows').innerHTML=names.map(name=>{const t=entry('temperature',name),r=entry('rain',name),d=t.day,rd=r.day;const degraded=(d&&d.data_quality!=='ok')||(rd&&rd.data_quality!=='ok');return `<tr><td><button class="city-button" data-city="${esc(name)}">${esc(name)}</button><span class="sub">High: ${esc(t.city?.icao||'—')} · Rain: ${esc(r.city?.icao||'—')}</span></td><td>${num(d?.distribution?.median)}°<span class="sub">${num(d?.distribution?.p10,0)}–${num(d?.distribution?.p90,0)}° · 80%</span></td><td>${num(d?.market_forecast?.median)}°</td><td>${num(d?.observed?.max_f)}°</td><td>${pct(rd?.consensus)}</td><td>${rd?.market?.mid==null?'—':num(rd.market.mid,0)+'%'}</td><td><span class="badge ${degraded?'warn':''}">${degraded?'Partial':'Available'}</span><span class="sub">${esc(ageText(t.board?.generated_at||r.board?.generated_at))}</span></td></tr>`;}).join('')||'<tr><td colspan="7">No city data matches this view.</td></tr>';
const show=$('research').checked;const rows=all.filter(r=>show||!r.reasons.length).sort((a,b)=>D.reviewRank(b)-D.reviewRank(a));
$('signals').innerHTML=rows.map(r=>`<tr><td><button class="city-button" data-city="${esc(r.city)}" data-kind="${r.kind}">${esc(r.city)}</button></td><td>${esc(D.outcome(r.kind,r.bracket,r.e.side))}</td><td>${r.e.side} / ${num(r.e.price,2)}¢</td><td>${pct(r.e.side==='YES'?r.bracket.model_p:1-r.bracket.model_p)}</td><td class="delta">${num(r.e.ev_cents,2)}¢</td><td>${num(r.e.depth,0)}</td><td>${!r.reasons.length&&r.e.suggested_contracts?num(r.e.suggested_contracts,0)+' / $'+num(r.e.suggested_cost_dollars,2):'—'}</td><td><span class="badge ${r.reasons.length?'warn':''}">${esc(D.nextStep(r.reasons,r.e).label)}</span><span class="sub">${esc(r.reasons.slice(0,2).join('; '))}</span>${r.reasons.length>2?`<details><summary>All ${r.reasons.length} checks</summary>${r.reasons.map(esc).join('<br>')}</details>`:''}</td></tr>`).join('')||'<tr><td colspan="8">No signals meet the selected criteria. Station forecasts remain available above.</td></tr>';
}
function chart(series,observed,tz){const all=series.flatMap(s=>s.points).concat(observed.map(p=>({time:p.time,median:p.temperature_f}))).filter(p=>Number.isFinite(p.median));if(!all.length)return empty('Hourly guidance is not available for this snapshot.');
const W=820,H=310,L=48,R=16,T=16,B=40,times=all.map(p=>Date.parse(p.time)),values=all.map(p=>p.median);const xmin=Math.min(...times),xmax=Math.max(...times),ymin=Math.floor(Math.min(...values)-2),ymax=Math.ceil(Math.max(...values)+2),x=t=>L+(Date.parse(t)-xmin)/(xmax-xmin||1)*(W-L-R),y=v=>H-B-(v-ymin)/(ymax-ymin||1)*(H-B-T);
let s=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Hourly model temperatures and observed station temperatures"><title>Hourly model forecasts and station observations</title>`;
for(let i=0;i<=4;i++){const v=ymin+(ymax-ymin)*i/4;s+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="#e0e7e1"/><text x="${L-8}" y="${y(v)+4}" text-anchor="end" fill="#637470" font-size="11">${v.toFixed(0)}°</text>`;}
for(let i=0;i<=6;i++){const t=new Date(xmin+(xmax-xmin)*i/6),xx=L+(W-L-R)*i/6;const label=t.toLocaleTimeString('en-US',{hour:'numeric',timeZone:tz});s+=`<text x="${xx}" y="${H-14}" text-anchor="middle" font-size="11" fill="#637470">${esc(label)}</text>`;}
series.forEach((a,i)=>{s+=`<polyline points="${a.points.filter(p=>Number.isFinite(p.median)).map(p=>`${x(p.time)},${y(p.median)}`).join(' ')}" fill="none" stroke="${colors[i%colors.length]}" stroke-width="2"/>`;});
s+=`<polyline points="${observed.map(p=>`${x(p.time)},${y(p.temperature_f)}`).join(' ')}" fill="none" stroke="#1f3231" stroke-width="3"/>`;
return s+'</svg>';}
function drawDetail(){const {board,city,day:d}=current();$('city-select').value=state.city;$('kind-select').value=state.kind;
if(!d){$('station-title').innerHTML=empty('No forecast is available for this station, product, and reporting day.');['weather-briefing','station-metrics','hourly-chart','chart-legend','hourly-values','changes','settlement','brackets','source-status'].forEach(id=>$(id).innerHTML='');$('adjustment-form').hidden=true;document.querySelector('.practice-panel').hidden=true;return;}$('adjustment-form').hidden=false;document.querySelector('.practice-panel').hidden=false;drawWeatherBriefing(d,city);initPractice();
$('station-title').innerHTML=`<h2>${esc(city.city)} <span class="muted">${esc(city.icao)} · ${esc(d.date)}</span></h2>`;
$('station-metrics').innerHTML=state.kind==='temperature'?metric(num(d.distribution.median)+'°F','Forecast high')+metric(num(d.market_forecast?.median)+'°F','Market-implied high')+metric(num(d.observed?.max_f)+'°F','Observed maximum')+metric(num(d.distribution.p10)+'–'+num(d.distribution.p90)+'°','80% forecast interval'):metric(pct(d.consensus),'Forecast rain probability')+metric(num(d.market.mid)+'%','Market midpoint')+metric(d.observed?.precip_mm==null?'Unknown':num(d.observed.precip_mm,2)+' mm','Observed accumulation')+metric(d.observed?.precip_complete?'Adequate':'Incomplete','Precipitation coverage');
if(!draft)$('pop').value=Math.round((d.consensus??.5)*100);
const sources=Object.entries(d.sources||{}),series=sources.map(([name,v])=>({name,points:v.hourly||[]}));$('hourly-chart').innerHTML=chart(series,d.observed?.hourly||[],city.reporting_tz||city.tz);$('chart-note').textContent=`Model member medians; dark line shows station observations. Times use ${city.reporting_tz||city.tz} reporting time.`;
$('chart-legend').innerHTML=series.map((s,i)=>`<span><i class="swatch" style="background:${colors[i%colors.length]}"></i>${esc(s.name)}</span>`).join('')+'<span><i class="swatch" style="background:#1f3231"></i>Observed</span>';
const points=[...new Set(series.flatMap(s=>s.points.map(p=>p.time)))].sort();$('hourly-values').innerHTML=`<table><thead><tr><th>Time</th>${series.map(s=>`<th>${esc(s.name)} °F</th>`).join('')}</tr></thead><tbody>${points.map(t=>`<tr><td>${esc(new Date(t).toLocaleTimeString('en-US',{hour:'numeric',timeZone:city.reporting_tz||city.tz}))}</td>${series.map(s=>`<td>${num(s.points.find(p=>p.time===t)?.median)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
$('changes').innerHTML=`<p>${esc(d.changes?.summary||'First comparable snapshot')}</p><p class="muted">${esc(d.diagnostics?._intraday_method==='remaining_hour_ensembles'?'Current observations and remaining-hour ensembles determine the high.':d.obs_effect==='remaining_hours'?'Rain probability uses remaining hours in ensemble trajectories consistent with the observations.':'Full-day guidance; no reliable intraday conditioning available.')}</p>`;
const spec=d.settlement||{};$('settlement').innerHTML=`<p>${esc(spec.station||city.station)} · ${esc(spec.source||'Unconfirmed source')}</p><p>${esc(spec.threshold||'')}</p><p class="muted">${esc(time(d.window_start))} to ${esc(time(d.window_end))}</p><p><span class="badge ${spec.verified?'':'warn'}">${spec.verified?'Definition verified':'Confirmation needed'}</span></p><p>${esc((spec.reasons||[]).join('; '))}</p>${spec.rules_url?`<a href="${esc(spec.rules_url)}" target="_blank" rel="noopener">Contract rules</a>`:''}`;
$('bracket-title').textContent=state.kind==='temperature'?'Temperature brackets':'Rain market';
const brackets=d.ladder||[{label:'Measurable rain',model_p:d.consensus,implied:d.market.mid==null?null:d.market.mid/100,market:d.market,edge:d.edge}];
$('brackets').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Outcome</th><th>Model</th><th>Market</th><th>YES bid / ask</th><th>Change</th></tr></thead><tbody>${brackets.map(b=>`<tr><td>${esc(b.label)}</td><td><span class="bar" style="width:${Math.max(0,b.model_p||0)*130}px"></span>${pct(b.model_p)}</td><td>${pct(b.implied)}</td><td>${num(b.market.yes_bid,2)} / ${num(b.market.yes_ask,2)}¢</td><td>${esc(b.changes?.summary||'—')}</td></tr>`).join('')}</tbody></table></div>`;
const quotes=brackets.map(b=>b.market);$('source-status').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Source</th><th>Retrieved / observed</th><th>Model run</th><th>Coverage</th></tr></thead><tbody>${sources.map(([name,v])=>`<tr><td>${esc(name)}</td><td>${esc(ageText(v.retrieved_at))}</td><td>${v.model_run_at?esc(time(v.model_run_at)):'Not supplied by provider'}</td><td>${v.member_count} members</td></tr>`).join('')}<tr><td>Station observations</td><td>${esc(ageText(d.observed?.latest_at))}</td><td>Observed, not modeled</td><td>${d.observed?pct(d.observed.coverage):'Unknown'}</td></tr><tr><td>Market quotes</td><td>${esc(ageText(quotes.map(q=>q.retrieved_at).filter(Boolean).sort()[0]))}</td><td>Snapshot</td><td>${quotes.filter(q=>q.executable).length}/${quotes.length} executable books</td></tr></tbody></table></div>`;
$('shift-label').hidden=$('spread-label').hidden=state.kind==='rain';$('pop-label').hidden=state.kind!=='rain';$('shift').required=$('spread').required=state.kind==='temperature';$('pop').required=state.kind==='rain';previewAdjustment();}
function previewAdjustment(){const {board,day:d}=draftContext();if(!d)return;const stale=M.age(board.generated_at)>180||Date.now()>=Date.parse(d.window_end);$('adjustment-form').querySelector('[type=submit]').disabled=stale;$('adjustment-note').textContent=stale?'Refresh to a current snapshot before saving an adjustment.':'Preview anchored to '+time(board.generated_at)+'. The original automated forecast will remain unchanged.';
if(state.kind==='rain'){$('adjustment-preview').innerHTML=`<p>Automated: ${pct(d.consensus)} · Your preview: ${num(Number($('pop').value),0)}%</p>`;return;}
const shift=Number($('shift').value),spread=Number($('spread').value);if(!Number.isFinite(shift)||!Number.isFinite(spread))return;const v=M.adjust(d.distribution.quantiles,shift,spread,d.distribution.floor);$('adjustment-preview').innerHTML=`<p>Automated high: ${num(d.distribution.median)}°F · Your preview: ${num(v[7])}°F (${num(v[3])}–${num(v[11])}°, 80% interval)</p><div class="table-wrap"><table><thead><tr><th>Bracket</th><th>Automated</th><th>Your forecast</th></tr></thead><tbody>${d.ladder.map(b=>`<tr><td>${esc(b.label)}</td><td>${pct(b.model_p)}</td><td>${pct(M.between(v,b.lo,b.hi,d.distribution.floor))}</td></tr>`).join('')}</tbody></table></div>`;}
function saveAdjustment(event){event.preventDefault();const {board,day:d}=draftContext();if(!d||M.age(board.generated_at)>180||Date.now()>=Date.parse(d.window_end))return;const p={snapshot_id:board.snapshot_id,city:state.city,date:d.date,kind:state.kind,reason:$('reason').value.trim()};if(p.reason.length<10)return;if(state.kind==='temperature'){p.shift_f=Number($('shift').value);p.spread_factor=Number($('spread').value);}else p.pop_percent=Number($('pop').value);
const title=`Forecast adjustment: ${state.city} ${d.date}`;const body='Record this forecast adjustment against the archived snapshot.\n\n```json\n'+JSON.stringify(p,null,2)+'\n```';const url='https://github.com/cyclonecizek/KalshiWeather/issues/new?title='+encodeURIComponent(title)+'&body='+encodeURIComponent(body);
const a=document.createElement('a');a.href=url;a.target='_blank';a.rel='noopener';a.click();$('adjustment-message').textContent='Submit the prefilled issue in GitHub to save. It will appear here after the adjustment workflow and next refresh.';}
function drawPerformance(){const h=$('horizon').value,kind=$('perf-kind').value;const groups=(state.performance?.groups||[]).filter(g=>g.kind===kind&&(h==='all'||g.horizon===h));const records=(state.performance?.records||[]).filter(g=>g.kind===kind&&(h==='all'||g.horizon===h));const n=records.length,avg=k=>n?records.reduce((s,r)=>s+r[k],0)/n:null;const exact=records.filter(r=>r.covered80!==null);$('performance-summary').innerHTML=metric(n,'Paired forecast snapshots')+metric(num(avg('brier'),3),'Model Brier score')+metric(num(avg('market_brier'),3),'Market Brier score')+metric(exact.length?pct(exact.reduce((s,r)=>s+Number(r.covered80),0)/exact.length):'—','80% interval coverage');$('performance-empty').innerHTML=n?'':empty('No settled forecasts from this model version yet. Scores will populate as archived forecasts settle. Earlier model versions are excluded.');
$('performance-rows').innerHTML=groups.map(g=>`<tr><td>${esc(g.city)}</td><td>${esc(g.horizon.replace('_',' '))}</td><td>${g.n}</td><td>${num(g.brier,3)}</td><td>${num(g.market_brier,3)}</td><td>${pct(g.brier_skill)}</td><td>${num(g.bias)}</td><td>${pct(g.coverage80)}</td></tr>`).join('');
const pairs=records.flatMap(r=>r.pairs),bins=[];for(let i=0;i<10;i++){const values=pairs.filter(([p])=>Math.min(9,Math.floor(p*10))===i);if(values.length)bins.push({n:values.length,p:values.reduce((s,v)=>s+v[0],0)/values.length,y:values.reduce((s,v)=>s+v[1],0)/values.length});}
$('reliability').innerHTML=bins.length?`<svg viewBox="0 0 420 290" role="img" aria-label="Predicted probability versus observed frequency"><line x1="45" y1="245" x2="385" y2="20" stroke="#adbcb3" stroke-dasharray="5 5"/><line x1="45" y1="245" x2="385" y2="245" stroke="#9cafa5"/><line x1="45" y1="245" x2="45" y2="20" stroke="#9cafa5"/>${[0,.25,.5,.75,1].map(p=>`<text x="${45+p*340}" y="263" text-anchor="middle" font-size="10">${pct(p)}</text><text x="35" y="${249-p*225}" text-anchor="end" font-size="10">${pct(p)}</text>`).join('')}${bins.map(b=>`<circle cx="${45+b.p*340}" cy="${245-b.y*225}" r="${Math.min(12,4+Math.sqrt(b.n))}" fill="#23627c"><title>${pct(b.p)} forecast, ${pct(b.y)} observed, n=${b.n}</title></circle>`).join('')}<text x="210" y="286" text-anchor="middle" font-size="11">Forecast probability</text></svg>`:empty('Calibration points appear after settlement.');
$('calibration-candidates').innerHTML=groups.filter(g=>g.candidate_calibration).map(g=>{const c=g.candidate_calibration;return `<article class="panel"><h3>${esc(g.city)} · ${esc(g.horizon)} candidate correction</h3><p>Additional bias ${num(c.additional_bias_f)}°F. Trained on ${c.train_n} dates through ${esc(c.train_end)}; tested on ${c.test_n} later dates.</p><p>Holdout MAE: ${num(c.holdout_mae_original)}°F original, ${num(c.holdout_mae_adjusted)}°F adjusted. Review required before adoption.</p></article>`;}).join('');}
function drawJournal(){const scores=new Map((state.performance?.adjustments||[]).map(x=>[x.id,x]));$('adjustment-history').innerHTML=state.adjustments.slice().reverse().map(a=>{const s=scores.get(a.id);return `<article class="journal-card"><h3>${esc(a.city)} · ${esc(a.date)} · ${esc(a.kind)}</h3><p>${esc(a.reason)}</p><p class="muted">${esc(a.author)} · ${esc(time(a.created_at))} · ${esc(a.id)}</p><p>${s?`Brier: automated ${num(s.automatic_brier,3)} · adjusted ${num(s.adjusted_brier,3)}`:'Awaiting final settlement'}</p></article>`;}).join('')||empty('No saved adjustments yet. Open a station to record your forecast and reasoning.');$('paper-history').innerHTML=state.paper.length?`<div class="table-wrap"><table><thead><tr><th>Created</th><th>City</th><th>Contract</th><th>Side</th><th>Quantity</th><th>Cost + fee</th><th>Status</th></tr></thead><tbody>${state.paper.slice().reverse().map(o=>`<tr><td>${esc(time(o.created_at))}</td><td>${esc(o.city)}</td><td>${esc(o.ticker)}</td><td>${esc(o.side)}</td><td>${num(o.quantity,0)}</td><td>$${num(o.cost_dollars,2)}</td><td>Proposed · no fill assumed</td></tr>`).join('')}</tbody></table></div>`:empty('No paper orders qualify yet. Settlement verification and calibration must pass before proposals are recorded.');}
function drawStatus(){const notices=[];for(const kind of ['rain','temperature'])if(state.refreshErrors[kind])notices.push(kind+' refresh failed; showing the last available snapshot.');for(const kind of ['rain','temperature']){const b=state[kind];if(!b){notices.push(`${kind==='rain'?'Rain':'Temperature'} board unavailable.`);continue;}const m=M.age(b.generated_at);if(m>180)notices.push(`${kind==='rain'?'Rain':'Temperature'} board is ${ageText(b.generated_at)}. Suggestions are disabled.`);if(b.errors?.length)notices.push(`${kind}: ${b.errors.length} source warning(s). Some forecasts may be incomplete.`);}$('status').innerHTML=notices.map(n=>`<div class="notice">${esc(n)}</div>`).join('');if(state.status?.status==='degraded')$('status').innerHTML+='<div class="notice">The most recent update was partial. Last usable forecasts remain visible with their original timestamps.</div>';$('updated').textContent=`Rain: ${ageText(state.rain?.generated_at)} · Highs: ${ageText(state.temperature?.generated_at)}`;}
function render(){drawStatus();drawBoard();if(state.view==='detail')drawDetail();if(state.view==='performance')drawPerformance();if(state.view==='journal')drawJournal();}
async function load(){if(state.loading)return;state.loading=true;$('refresh').disabled=true;const paths={rain:'board.json',temperature:'board_temp.json',performance:'performance.json',outcomes:'outcomes.json',adjustments:'adjustments.json',paper:'paper/ledger.json',status:'status.json'};await Promise.all(Object.entries(paths).map(async([key,path])=>{try{const r=await fetch('data/'+path+'?t='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error(r.status);const data=await r.json();if(['rain','temperature'].includes(key)&&data.schema_version!==2)throw Error('Old data schema');state[key]=data;delete state.refreshErrors[key];}catch(error){state.refreshErrors[key]=String(error.message||error);if(['rain','temperature'].includes(key))console.warn(key+' refresh failed; retaining prior snapshot');}}));const names=cities();if(!names.includes(state.city))state.city=names[0]||'';$('city-select').innerHTML=names.map(n=>`<option>${esc(n)}</option>`).join('');state.loading=false;state.lastLoad=Date.now();$('refresh').disabled=false;render();}
document.querySelectorAll('nav button').forEach(b=>b.addEventListener('click',()=>view(b.dataset.view)));
$('refresh').addEventListener('click',load);$('search').addEventListener('input',drawBoard);$('research').addEventListener('change',drawBoard);
for(const [id,day]of [['today','0'],['tomorrow','1']])$(id).addEventListener('click',()=>{resetDraft();state.day=day;$('today').setAttribute('aria-pressed',day==='0');$('tomorrow').setAttribute('aria-pressed',day==='1');render();});
document.addEventListener('click',e=>{const b=e.target.closest('[data-city]');if(!b)return;resetDraft();state.city=b.dataset.city;state.kind=b.dataset.kind||'temperature';view('detail');window.scrollTo({top:0,behavior:'smooth'});});
$('city-select').addEventListener('change',e=>{resetDraft();state.city=e.target.value;drawDetail();});$('kind-select').addEventListener('change',e=>{resetDraft();state.kind=e.target.value;drawDetail();});
['shift','spread','pop','reason'].forEach(id=>$(id).addEventListener('input',()=>{if(!draft)draft=structuredClone(current());previewAdjustment();}));$('adjustment-form').addEventListener('submit',saveAdjustment);$('reset-adjustment').addEventListener('click',()=>{resetDraft();$('pop').value=Math.round((current().day?.consensus||.5)*100);previewAdjustment();});
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
    const weather = r.kind === 'temperature'
      ? `Forecast high ${num(r.d.distribution.median)}°F; central 80% range ${num(r.d.distribution.p10,0)}–${num(r.d.distribution.p90,0)}°F.`
      : `${pct(r.d.consensus)} chance of measurable rain at the station during this reporting day.`;
    return `<article class="review-card"><div class="card-top"><span class="eyebrow">${esc(r.city)} · ${r.kind==='rain'?'RAIN':'DAILY HIGH'}</span><span class="badge ${action.tone==='ready'?'':'warn'}">${esc(action.label)}</span></div><h3>${esc(D.outcome(r.kind,r.bracket,r.e.side))}</h3><p>${esc(weather)}</p><dl class="comparison"><div><dt>Model chance of this position winning</dt><dd>${pct(chance)}</dd></div><div><dt>Probability needed to break even</dt><dd>${costs?pct(costs.breakEven):'Fee estimate unavailable'}</dd></div></dl><p class="muted">${num(r.e.price,2)}¢ per contract before fees · ${esc(ageText(r.q.retrieved_at))}. A price difference is a hypothesis to verify.</p><p class="next-action">Next: ${esc(action.tasks[0])}</p><button class="primary" data-city="${esc(r.city)}" data-kind="${r.kind}">Work through this station</button></article>`;
  }).join('') || empty('No priced comparisons are available for this view. Select a station below to assess the weather first.');
}

function drawWeatherBriefing(d,city) {
  let headline, facts, challenge;
  if (state.kind === 'temperature') {
    const peak = d.ladder.slice().sort((a,b)=>b.model_p-a.model_p)[0];
    headline = `Daily high near ${num(d.distribution.median)}°F`;
    facts = `The central 80% of the forecast distribution spans ${num(d.distribution.p10)}–${num(d.distribution.p90)}°F.`;
    if (peak) facts += ` The most likely listed range is ${peak.label}, with a ${pct(peak.model_p)} model probability. Other ranges remain possible.`;
    if (d.distribution.floor != null) facts += ` Observations impose a ${num(d.distribution.floor)}°F lower bound after the reporting allowance.`;
    const market = d.market_forecast?.median;
    if (Number.isFinite(market)) facts += ` The model median is ${num(Math.abs(d.distribution.median-market))}°F ${d.distribution.median>=market?'warmer':'cooler'} than traders’ implied median. This does not identify a profitable bracket by itself.`;
    challenge = 'What could move the high across the range boundaries? Review cloud persistence, mixing, advection and frontal timing against the remaining hourly guidance.';
  } else {
    headline = `${pct(d.consensus)} chance of measurable station rain`;
    facts = 'This is a station-total event for the reporting day, not the chance of rain somewhere in the city or forecast area.';
    if (d.observed?.precip_complete) facts += ` Available observations indicate ${num(d.observed.precip_mm,2)} mm so far; these are provisional, not the final settlement report.`;
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
    if (state.kind==='temperature') $('practice-market').value = String(brackets.indexOf(brackets.reduce((best,b)=>b.model_p>best.model_p?b:best)));
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
  const reasons=M.eligibility(edge,q,board);
  const action=D.nextStep(reasons,edge||{});
  $('practice-anchor').textContent = `Example anchored to ${time(board.generated_at)}. Quote ${ageText(q.retrieved_at)}. Reset to use the latest loaded snapshot.`;
  let numbers = '';
  if (result) {
    const difference = probability-result.breakEven;
    numbers = `<div class="metrics practice-metrics">${metric('$'+num(result.cost,2),'Total purchase cost + estimated fee')}${metric('$'+num(result.winNet,2),'Net gain if this position wins')}${metric('$'+num(result.maxLoss,2),'Maximum loss if it loses')}${metric(pct(result.breakEven),'Probability needed to break even')}</div><p>Your ${pct(probability)} estimate is ${num(Math.abs(difference)*100,1)} percentage points ${difference>=0?'above':'below'} break-even. ${difference>0?'The example has a positive modeled average, if your probability is accurate.':'At this probability, the example does not have a positive modeled average.'}</p><p class="muted">Estimated entry fee: $${num(result.fee,2)}. Average net result under your probability: ${result.expectedNet<0?'−':'+'}$${num(Math.abs(result.expectedNet),2)}. One trade still settles as a win or loss; this average is not a promised return.</p>`;
    const depth=side==='YES'?q.yes_depth:q.no_depth;
    const confirmedDepth=Number.isFinite(depth)?depth:(edge?.side===side?edge.depth:null);
    if (!Number.isFinite(confirmedDepth) || confirmedDepth<quantity) numbers += '<p class="notice">The quote does not confirm enough contracts at this price for this example. Do not assume this quantity can be filled at the displayed cost.</p>';
  } else numbers = '<p class="notice">A complete purchase price, verified fee estimate, and valid inputs are needed. Enter a probability from 0 to 100 and a whole contract count from 1 to 1,000.</p>';
  $('practice-result').innerHTML = `<h3>${esc(D.outcome(board.kind,b,side))} <span class="muted">(${side})</span></h3><p>Buying ${side} means this position wins if ${side==='YES'?'the listed weather outcome occurs':'the listed weather outcome does not occur'}. ${side==='NO'&&board.kind==='temperature'?'It can win on either side of the listed temperature range.':''}</p>${numbers}<div class="decision-next"><h3>${esc(action.label)}</h3><ul>${action.tasks.map(t=>`<li>${esc(t)}</li>`).join('')}</ul><p>Changing your estimate here does not clear the data checks or submit an order. To preserve your forecast, use the judgment form below.</p>${reasons.length?`<details><summary>All checks that still need attention</summary><ul>${reasons.map(r=>`<li>${esc(r)}</li>`).join('')}</ul></details>`:''}</div><p class="muted">Assumes the displayed ask, this order’s estimated entry fee, and ordinary $1/$0 settlement. Verify actual fees and rules on Kalshi. Selling early can change the result.</p><p>Market to look up: <code>${esc(q.ticker)}</code> · <a href="https://kalshi.com/" target="_blank" rel="noopener">Open Kalshi and search this ticker</a></p>`;
}

['practice-market','practice-side'].forEach(id=>$(id).addEventListener('change',resetPracticeProbability));
['practice-probability','practice-quantity'].forEach(id=>$(id).addEventListener('input',drawPractice));
$('practice-reset').addEventListener('click',()=>initPractice(true));
document.querySelectorAll('[data-view-link]').forEach(b=>b.addEventListener('click',()=>view(b.dataset.viewLink)));
