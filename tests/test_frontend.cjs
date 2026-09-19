const test=require('node:test'),assert=require('node:assert/strict');
const M=require('../docs/assets/math.js');
test('low forecast previews conserve probability and respect observed ceiling',()=>{
 const q=M.Q.map(p=>50+12*p),v=M.adjust(q,15,2,null,60.5);
 assert.ok(v.every(x=>x<=60.5));
 assert.equal(M.between(v,61,null,null,60.5),0);
 assert.ok(Math.abs(M.between(v,null,59,null,60.5)+M.between(v,60,null,null,60.5)-1)<1e-10);
 const D=require('../docs/assets/decision.js');
 assert.equal(D.outcome('temperature_low',{label:'60–61°F'},'NO'),'Low outside 60–61°F');
});
test('low station walkthrough uses low semantics, ceiling and portfolio product identity',()=>{
 const fs=require('node:fs'),vm=require('node:vm'),ids=new Map();
 const element=id=>{if(!ids.has(id))ids.set(id,{value:'',innerHTML:'',hidden:false,addEventListener(){},setAttribute(){},querySelector(){return element('submit');},get valueAsNumber(){return Number(this.value);}});return ids.get(id);};
 const context=vm.createContext({ForecastMath:M,ForecastDecision:require('../docs/assets/decision.js'),structuredClone,Date,console,setInterval(){},fetch:()=>new Promise(()=>{}),document:{getElementById:element,querySelector:element,querySelectorAll:()=>[],addEventListener(){}}});
 vm.runInContext(fs.readFileSync('docs/assets/research.js','utf8'),context);
 vm.runInContext(fs.readFileSync('docs/assets/app.js','utf8'),context);
 const at=new Date().toISOString();
 const q={ticker:'LOW',event_ticker:'LOW-DAY',yes_bid:30,yes_ask:35,no_ask:70,mid:32.5,spread:5,retrieved_at:at,yes_depth:100};
 const e={side:'YES',price:35,fee_rate:.07,ev_cents:10,eligibility:{eligible:false,reasons:['Out-of-sample calibration pending']}};
 const day={kind:'temperature_low',date:'2026-09-19',window_start:at,window_end:new Date(Date.now()+86400000).toISOString(),sources:{},settlement:{verified:true,reasons:[]},data_quality:'ok',fee_verified:true,observed:{min_f:60,max_f:90},distribution:{median:59,p10:55,p90:60.5,quantiles:M.Q.map(p=>Math.min(60.5,55+7*p)),ceiling:60.5},ladder:[{label:'60°F or lower',lo:null,hi:60,model_p:1,market:q,edge:e}]};
 context.fixture={kind:'temperature_low',schema_version:2,generated_at:at,snapshot_id:'low-snapshot',cities:[{city:'Chicago',icao:'KMDW',tz:'America/Chicago',days:{'0':day}}]};
 vm.runInContext("state.temperature_low=fixture;state.city='Chicago';state.kind='temperature_low';drawBoard();drawDetail();drawFreshness();",context);
 assert.match(element('weather-briefing').innerHTML,/Daily low near 59/);
 assert.match(element('weather-briefing').innerHTML,/upper bound/);
 assert.match(element('station-metrics').innerHTML,/Observed minimum/);
 assert.match(element('updated').innerHTML,/Lows/);
 assert.match(element('city-rows').innerHTML,/data-kind="temperature_low"/);
 assert.match(element('practice-result').innerHTML,/Low in 60/);
 const rows=vm.runInContext('portfolioCandidates()',context);
 assert.ok(rows.length && rows.every(r=>r.kind==='temperature_low'));
 element('shift').value=10;element('spread').value=2;
 vm.runInContext('previewAdjustment()',context);
 assert.match(element('adjustment-preview').innerHTML,/Automated low/);
 assert.match(element('adjustment-preview').innerHTML,/60.5/);
});
test('MOS chart uses native TMP samples, matching station and reporting day',()=>{
 const fs=require('node:fs'),vm=require('node:vm'),node={addEventListener(){}};
 const context=vm.createContext({ForecastMath:M,ForecastDecision:require('../docs/assets/decision.js'),Date,console,setInterval(){},fetch:()=>new Promise(()=>{}),document:{getElementById:()=>node,querySelector:()=>node,querySelectorAll:()=>[],addEventListener(){}}});
 vm.runInContext(fs.readFileSync('docs/assets/research.js','utf8'),context);
 vm.runInContext(fs.readFileSync('docs/assets/app.js','utf8'),context);
 context.fixture={window_start:'2026-09-18T06:00:00Z',window_end:'2026-09-19T06:00:00Z',station_guidance:{MOS:{station:'KMDW',status:'ok',issued_at:new Date().toISOString(),mos_maximum:{temperature_f:99},points:[
  {valid_at:'2026-09-18T09:00:00Z',temperature_f:72},
  {valid_at:'2026-09-18T06:00:00+00:00',temperature_f:70},
  {valid_at:'2026-09-18T12:00:00Z',temperature_f:null},
  {valid_at:'invalid',temperature_f:75},
  {valid_at:'2026-09-19T06:00:00Z',temperature_f:76}]}}};
 let result=vm.runInContext("mosChartSeries(fixture,{icao:'KMDW'})",context);
 assert.equal(result.points.length,2);assert.equal(result.points[0].median,70);
 assert.equal(result.markers,true);assert.match(result.name,/native samples/);
 const svg=vm.runInContext("chart([mosChartSeries(fixture,{icao:'KMDW'})],[],'Etc/GMT+6')",context);
 assert.equal((svg.match(/<circle /g)||[]).length,2);assert.doesNotMatch(svg,/NaN|99\.0°F/);
 assert.equal(vm.runInContext("mosChartSeries(fixture,{icao:'KORD'})",context),null);
 context.fixture.station_guidance.MOS.issued_at=new Date(Date.now()-13*3600000).toISOString();
 assert.match(vm.runInContext("mosChartSeries(fixture,{icao:'KMDW'}).name",context),/stale/);
 context.fixture.station_guidance.MOS.points=[];
 assert.equal(vm.runInContext("mosChartSeries(fixture,{icao:'KMDW'})",context),null);
 assert.equal(vm.runInContext("mosChartSeries({},{icao:'KMDW'})",context),null);
 context.fixture.station_guidance.MOS.mos_extrema=[{kind:'minimum',temperature_f:65,period_start:'2026-09-18T01:00:00Z',period_end:'2026-09-18T14:00:00Z'},{kind:'maximum',temperature_f:85,period_start:'2026-09-18T13:00:00Z',period_end:'2026-09-19T01:00:00Z'}];
 const bands=vm.runInContext("mosChartExtrema(fixture,{icao:'KMDW'})",context);
 assert.equal(bands.length,2);assert.equal(bands[0].start,Date.parse(context.fixture.window_start));
 const extremaSVG=vm.runInContext("chart([],[],'Etc/GMT+6',mosChartExtrema(fixture,{icao:'KMDW'}))",context);
 assert.match(extremaSVG,/N 65°/);assert.match(extremaSVG,/X 85°/);
 assert.match(extremaSVG,/Period extremum, not an hourly temperature/);
 assert.doesNotMatch(extremaSVG,/NaN/);
 assert.equal(vm.runInContext("mosChartExtrema(fixture,{icao:'KORD'}).length",context),0);
});
test('Meteoblue expiry blocks only forecasts that include it',()=>{
 const now=Date.now(),stamp=new Date(now).toISOString();
 const d={meteoblue:{expires_at:new Date(now-1000).toISOString()},model_inputs:[{model:'METEOBLUE',included:true}]};
 const e={eligibility:{reasons:[]}},q={retrieved_at:stamp},b={generated_at:stamp};
 assert.ok(M.eligibility(e,q,b,now,d).some(r=>r.includes('Meteoblue input is stale')));
 d.model_inputs[0].included=false;
 assert.equal(M.eligibility(e,q,b,now,d).length,0);
});
test('WeatherNext is visible with zero weight and interpolation attribution',()=>{
 const fs=require('node:fs'),vm=require('node:vm'),elements=new Map();
 const element=id=>{if(!elements.has(id))elements.set(id,{innerHTML:'',value:'',addEventListener(){}});return elements.get(id);};
 const context=vm.createContext({ForecastMath:M,ForecastDecision:require('../docs/assets/decision.js'),Date,console,setInterval(){},fetch:()=>new Promise(()=>{}),document:{getElementById:element,querySelector:element,querySelectorAll:()=>[],addEventListener(){}}});
 vm.runInContext(fs.readFileSync('docs/assets/research.js','utf8'),context);
 vm.runInContext(fs.readFileSync('docs/assets/app.js','utf8'),context);
 context.fixture={distribution:{median:80},weathernext:{note:'Six-hour guidance interpolated to hourly output.',retrieved_at:new Date().toISOString()},model_inputs:[{model:'WEATHERNEXT2',family:'Google AI research',included:false,weight:0,value:79,members:64,status:'Research comparison: ok'}]};
 vm.runInContext("state.kind='temperature';drawModelInputs(fixture)",context);
 const html=element('model-inputs').innerHTML;
 assert.match(html,/Google WeatherNext 2/);assert.match(html,/0\.0%/);
 assert.match(html,/64 members/);assert.match(html,/interpolated/);
 assert.match(html,/Google DeepMind WeatherNext 2 via Open-Meteo/);
 context.correction={observation_ml:{status:'collecting',message:'Collecting settled dates',training_dates:2,required_dates:30}};
 assert.match(vm.runInContext('observationCorrection(correction)',context),/2 distinct settled dates/);
 context.correction.observation_ml={status:'candidate',message:'Research comparison only',training_dates:30,required_dates:30,median:82,p10:79,p90:85,adjustment_f:2,fit_before:'2026-08-01',interval_dates:10,last_training_date:'2026-08-10'};
 const correction=vm.runInContext('observationCorrection(correction)',context);
 assert.match(correction,/Candidate high: 82\.0°F/);
 assert.match(correction,/not used in market probabilities or allocations/);
 context.fixture={distribution:{median:80},model_inputs:[{model:'NDFD',family:'NWS',included:false,weight:0,value:85,status:'Available for comparison; excluded from observation-conditioned blend',nws_guidance:{status:'ok',retrieved_at:new Date().toISOString(),product_generated_at:new Date().toISOString(),issued_at:null,periods:[]}}]};
 vm.runInContext('drawModelInputs(fixture)',context);
 const nws=element('model-inputs').innerHTML;
 assert.match(nws,/85\.0°F/);assert.match(nws,/0\.0%/);
 assert.match(nws,/excluded from observation-conditioned blend/);
 assert.match(nws,/forecast issue time: Not supplied/);
 assert.match(nws,/Product generation is not necessarily the forecast issue time/);
 context.fixture.model_inputs[0].nws_guidance.status='failed';
 context.fixture.model_inputs[0].nws_guidance.retrieved_at=null;
 vm.runInContext('drawModelInputs(fixture)',context);
 assert.match(element('model-inputs').innerHTML,/Retrieved: Not supplied/);
 assert.match(element('model-inputs').innerHTML,/Last attempt/);
});
test('MOS/LAMP panel renders periods, missing guidance and browser-aged issues',()=>{
 const fs=require('node:fs'),vm=require('node:vm');
 const elements=new Map();
 const element=id=>{if(!elements.has(id))elements.set(id,{innerHTML:'',value:'',addEventListener(){}});return elements.get(id);};
 const context=vm.createContext({ForecastMath:M,ForecastDecision:require('../docs/assets/decision.js'),Date,console,setInterval(){},fetch:()=>new Promise(()=>{}),document:{getElementById:element,querySelector:element,querySelectorAll:()=>[],addEventListener(){}}});
 vm.runInContext(fs.readFileSync('docs/assets/research.js','utf8'),context);
 vm.runInContext(fs.readFileSync('docs/assets/app.js','utf8'),context);
 vm.runInContext("drawStationGuidance({}, {tz:'America/Chicago'})",context);
 assert.match(element('station-guidance').innerHTML,/not archived/);
 context.fixture={distribution:{median:80},station_guidance:{LAMP:{station:'KMDW',status:'ok',issued_at:new Date(Date.now()-4*3600000).toISOString(),sampled_max_f:74,points:[],precipitation:[{element:'PPO',hours:0,start:new Date().toISOString(),end:new Date().toISOString(),probability:.2}]}}};
 vm.runInContext("drawStationGuidance(fixture,{tz:'America/Chicago'})",context);
 assert.match(element('station-guidance').innerHTML,/stale/);
 assert.match(element('station-guidance').innerHTML,/including traces/);
 assert.doesNotMatch(element('station-guidance').innerHTML,/below your daily blend/);
 context.fixture.station_guidance.MOS={station:'KMDW',status:'ok',issued_at:new Date().toISOString(),sampled_max_f:74,points:[],mos_maximum:{temperature_f:88,field:'N/X',period_start:new Date().toISOString(),period_end:new Date().toISOString(),period_definition:'07:00–19:00 local standard time'}};
 vm.runInContext("drawStationGuidance(fixture,{tz:'America/Chicago'})",context);
 assert.match(element('station-guidance').innerHTML,/MOS daytime maximum \(N\/X\): 88/);
 assert.match(element('station-guidance').innerHTML,/explicit MOS daytime maximum is 8.*above/);
 context.fixture.station_guidance.MOS.mos_maximum=null;
 vm.runInContext("drawStationGuidance(fixture,{tz:'America/Chicago'})",context);
 assert.match(element('station-guidance').innerHTML,/not provided for this day/);
 assert.doesNotMatch(element('station-guidance').innerHTML,/explicit MOS daytime maximum is/);
});
test('distribution preview conserves bracket probability',()=>{const v=M.Q.map(q=>80+q*10);const p=M.between(v,null,81)+M.between(v,82,87)+M.between(v,88,null);assert.ok(Math.abs(p-1)<1e-8);});
test('manual adjustment respects the observed lower bound',()=>{const v=M.Q.map(q=>80+q*10);const shifted=M.adjust(v,-10,.5,85);assert.ok(shifted.every(x=>x>=85));assert.equal(M.between(shifted,null,84,85),0);});
test('stale quotes disable cached server eligibility in browser',()=>{const now=Date.parse('2026-09-06T12:00Z');const e={eligibility:{eligible:true,reasons:[]}},b={generated_at:'2026-09-06T12:00Z',execution_policy:{max_quote_age_minutes:20}};assert.ok(M.eligibility(e,{retrieved_at:'2026-09-06T11:00Z'},b,now).includes('Quote is stale'));});
test('missing eligibility cannot enable a suggestion',()=>{assert.ok(M.eligibility({}, {},{}).includes('Eligibility not evaluated'));});

test('future timestamps cannot make a quote look fresh',()=>{
 const now=Date.now();
 assert.equal(M.age(new Date(now+3600000).toISOString(),now),Infinity);
});

const D=require('../docs/assets/decision.js');
test('practice example includes the whole order fee and the full downside',()=>{
 const e=D.example(.6,40,10,.07);
 assert.equal(e.fee,.17);
 assert.equal(e.cost,4.17);
 assert.equal(e.maxLoss,4.17);
 assert.ok(Math.abs(e.winNet-5.83)<1e-8);
 assert.ok(Math.abs(e.breakEven-.417)<1e-8);
 assert.ok(Math.abs(e.expectedNet-1.83)<1e-8);
});
test('high outcome probability can still be a negative value purchase',()=>{
 const e=D.example(.7,85,1,.07);
 assert.ok(e.expectedNet<0);
 assert.ok(e.breakEven>.85);
});
test('missing prices, unknown fees and invalid practice inputs cannot produce a result',()=>{
 for(const args of [[.6,null,1,.07],[.6,40,1,null],[1.2,40,1,.07],[.6,40,1.5,.07],[.6,40,0,.07],[.6,40,1001,.07],[NaN,40,1,.07]])assert.equal(D.example(...args),null);
});
test('NO temperature wording covers the entire complement, not only a colder high',()=>{
 assert.equal(D.outcome('temperature',{label:'80–81°F'},'NO'),'High outside 80–81°F');
 assert.equal(D.outcome('rain',{},'NO'),'No measurable rain at the station');
});
test('positive model edge does not clear missing verification',()=>{
 const action=D.nextStep(['Settlement definition unverified','Out-of-sample calibration pending'],{ev_cents:20});
 assert.equal(action.label,'Wait and investigate');
 assert.ok(action.tasks.some(t=>t.includes('station')));
 assert.ok(action.tasks.some(t=>t.includes('verified track record')));
});
test('suspect price differences rank behind ordinary review candidates',()=>{
 const row={e:{flag:'watch',ev_cents:10},reasons:['calibration pending'],d:{data_quality:'ok'}};
 assert.ok(D.reviewRank({...row,e:{flag:'suspect',ev_cents:80}})<D.reviewRank(row));
});

test('station walkthrough keeps practice estimates anchored across automatic refreshes',()=>{
 const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
 const ids=new Map();
 const element=id=>{
  if(!ids.has(id))ids.set(id,{value:'',innerHTML:'',hidden:false,dataset:{},addEventListener(){},setAttribute(){},focus(){},scrollIntoView(){},querySelector(){return element('submit');},get valueAsNumber(){return this.value===''?NaN:Number(this.value);}});
  return ids.get(id);
 };
 const context=vm.createContext({ForecastMath:M,ForecastDecision:D,structuredClone,Date,console,
  setInterval(){},fetch:()=>new Promise(()=>{}),window:{scrollTo(){}},
  document:{hidden:false,getElementById:element,querySelector:element,querySelectorAll:()=>[],addEventListener(){}}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../docs/assets/research.js'),'utf8'),context);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../docs/assets/app.js'),'utf8'),context);
 const at=new Date().toISOString();
 const q={ticker:'EXAMPLE',yes_bid:35,yes_ask:40,no_ask:65,mid:37.5,spread:5,retrieved_at:at,yes_depth:20};
 const edge={side:'YES',price:40,fee_rate:.07,ev_cents:10,eligibility:{eligible:false,reasons:['Out-of-sample calibration pending']}};
 const d={date:'2026-09-06',window_start:at,window_end:new Date(Date.now()+86400000).toISOString(),kind:'temperature',data_quality:'partial',n_families:1,fee_verified:true,
  distribution:{median:85,p10:80,p90:90,quantiles:M.Q.map(p=>80+10*p),floor:null},sources:{},settlement:{verified:false,reasons:[]},
  ladder:[{label:'85°F or lower',lo:null,hi:85,model_p:.6,market:q,edge},{label:'86°F or higher',lo:86,hi:null,model_p:.4,market:{...q,ticker:'OTHER'},edge}]};
 const board={kind:'temperature',generated_at:at,cities:[{city:'Test station',icao:'KTEST',tz:'UTC',days:{'0':d}}]};
 context.fixture=board;
 vm.runInContext("state.temperature=fixture;state.city='Test station';state.kind='temperature';drawBoard();drawDetail();",context);
 assert.match(element('weather-briefing').innerHTML,/Daily high near 85/);
 assert.match(element('practice-result').innerHTML,/Maximum loss/);
 element('practice-probability').value='73';
 vm.runInContext("state.temperature.cities[0].days['0'].ladder[0].market.yes_ask=70;drawDetail();",context);
 assert.equal(element('practice-probability').value,'73');
 assert.match(element('practice-result').innerHTML,/\$0\.42/);
 assert.match(element('practice-result').innerHTML,/Wait and investigate/);
 vm.runInContext('initPractice(true)',context);
 assert.match(element('practice-result').innerHTML,/\$0\.72/);
 assert.equal(element('practice-probability').value,'60.0');
 // Exercise the actual board form, including hypothetical gates and saved model selection.
 element('bet-budget').value='500'; element('bet-committed').value='0';
 element('bet-model').value='automatic';
 vm.runInContext(`fixture.snapshot_id='current';
 const bd=fixture.cities[0].days['0'];
 Object.assign(bd,{forecast_retrieved_at:fixture.generated_at,data_quality:'ok',n_families:2});
 for(const b of bd.ladder) Object.assign(b.market,{yes_ask:40,no_ask:65,yes_depth:200,no_depth:200,executable:true,status:'open',close_time:new Date(Date.now()+3600000).toISOString()});
 drawBudget();`,context);
 assert.match(element('budget-result').innerHTML,/Suggested new allocation: \$0/);
 assert.match(element('budget-hypothetical').innerHTML,/Hypothetical total: \$24\.60/);
 assert.match(element('budget-result').innerHTML,/calibration pending/);
 element('bet-mode').value='paper';
 vm.runInContext('drawBudget()',context);
 assert.match(element('plan-status').textContent,/Paper practice only/);
 assert.match(element('budget-result').innerHTML,/Paper allocation/);
 assert.match(element('budget-result').innerHTML,/\$24\.60/);
 element('bet-mode').value='verified';
 vm.runInContext('drawBudget()',context);
 assert.match(element('budget-result').innerHTML,/Suggested new allocation: \$0/);

 element('bet-model').value='personal';
 vm.runInContext(`state.adjustments=[{id:'issue-1',city:'Test station',kind:'temperature',date:'2026-09-06',snapshot_id:'current',created_at:fixture.generated_at,tickers:['EXAMPLE','OTHER'],adjusted_probabilities:[.1,.9]}];drawBudget();`,context);
 const candidates=vm.runInContext('portfolioCandidates()',context);
 assert.equal(candidates[0].side,'NO');
 assert.equal(candidates[0].probability,.9);
 assert.equal(candidates[0].depth,200);
 vm.runInContext(`fixture.generated_at=new Date(Date.now()-4*3600000).toISOString();drawBudget();`,context);
 assert.match(element('budget-hypothetical').innerHTML,/Hypothetical total: \$0\.00/);
 assert.match(element('meteoblue-overview').innerHTML,/disabled for public display/);
});

const candidate=(overrides={})=>({city:'A',event:'A|temperature|today',ticker:'A-HIGH',probability:.65,price:40,feeRate:.07,depth:1000,reasons:[],...overrides});
test('a $500 plan uses whole contracts, fee-inclusive caps, and leaves a reserve',()=>{
 const plan=D.allocate(Array.from({length:20},(_,i)=>candidate({city:`C${i}`,event:`E${i}`,ticker:`T${i}`})),500);
 assert.ok(plan.allocated<=125);
 assert.ok(plan.remaining>=375);
 assert.equal(plan.allocated+plan.remaining,500);
 assert.ok(plan.rows.every(r=>Number.isInteger(r.contracts)&&r.cost<=25));
 for(const r of plan.rows.filter(r=>r.contracts))assert.equal(r.cost,Math.round(D.example(r.probability,r.price,r.contracts,r.feeRate).cost*100)/100);
});
test('same event brackets, duplicate tickers, city exposure and depth are capped',()=>{
 const candidates=[candidate(),candidate({ticker:'A-OTHER'}),candidate({event:'A|rain|today',ticker:'RAIN'}),candidate({event:'A|third',ticker:'THIRD'}),candidate({city:'B',event:'B|high',ticker:'A-HIGH'}),candidate({city:'C',event:'C|high',ticker:'C',depth:3})];
 const plan=D.allocate(candidates,500), funded=plan.rows.filter(r=>r.contracts);
 assert.equal(funded.filter(r=>r.event==='A|temperature|today').length,1);
 assert.equal(funded.filter(r=>r.ticker==='A-HIGH').length,1);
 assert.ok(funded.filter(r=>r.city==='A').reduce((s,r)=>s+r.cost,0)<=50);
 assert.ok(funded.find(r=>r.city==='C').contracts<=3);
});
test('commitments consume the allocation ceiling and invalid budgets fail closed',()=>{
 assert.equal(D.allocate([candidate()],500,125).allocated,0);
 assert.equal(D.allocate([candidate()],500,500).remaining,0);
 for(const args of [[NaN,0],[0,0],[.5,0],[100001,0],[500,-1],[500,501]])assert.equal(D.allocate([candidate()],...args),null);
});
test('no allocation when model advantage vanishes, data is absent, or checks fail',()=>{
 for(const c of [candidate({probability:.46}),candidate({feeRate:null}),candidate({depth:null}),candidate({probability:NaN}),candidate({reasons:['Quote stale']}),candidate({reasons:['Out-of-sample calibration pending']})])assert.equal(D.allocate([c],500).allocated,0);
});
test('saved forecast requires the current snapshot and coherent probabilities',()=>{
 const now=Date.now(),saved={city:'A',date:'today',kind:'temperature',snapshot_id:'new',created_at:new Date(now-1000).toISOString(),tickers:['X','Y'],adjusted_probabilities:[.7,.3],id:'issue-1'};
 const get=items=>D.savedProbability(items,{snapshot_id:'new'},'A',{date:'today'},'temperature','X',now);
 assert.equal(get([saved]).probability,.7);
 assert.equal(get([{...saved,snapshot_id:'old'}]),null);
 assert.equal(get([{...saved,adjusted_probabilities:[.7,.8]}]),null);
 assert.equal(get([{...saved,tickers:['X','X']}]),null);
 assert.equal(get([{...saved,created_at:new Date(now+3600000).toISOString()}]),null);
});

const R=require('../docs/assets/research.js');
test('research page distinguishes missing source probabilities and unapplied candidates',()=>{
 const collecting={status:'collecting',train_n:0,test_n:0,reasons:['More dates needed']};
 const html=R.render([{horizon:'morning',kind:'temperature',dates:9,current_dates:2,excluded_prior_dates:7,
  sources:[{mode:'source',model:'<unsafe>',method:'Archived point forecast',dates:9,brier:null,mae_f:2,bias_f:-2,reliability:[]}],weights:collecting,calibration:collecting}]);
 assert.match(html,/&lt;unsafe&gt;/);assert.ok(!html.includes('<unsafe>'));
 assert.match(html,/Missing probability scores/);assert.match(html,/Building evidence/);
 assert.match(html,/not change the live forecast/);
});
test('research review candidate is explicitly not applied',()=>{
 const candidate={status:'review',train_n:40,test_n:20,reasons:[],parameters:{model:'GEFS',mode:'weight',multiplier:.5},original:{brier:.3},holdout:{brier:.1}};
 const html=R.render([{horizon:'morning',kind:'rain',dates:60,current_dates:60,excluded_prior_dates:0,sources:[],weights:candidate,calibration:{status:'collecting',reasons:[]}}]);
 assert.match(html,/Ready for review; not applied/);assert.match(html,/multiply within-family weight by 0.5/);
});

test('freshness distinguishes product clocks and includes old or missing station reports',()=>{
 const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
 const elements=new Map();
 const element=id=>{if(!elements.has(id))elements.set(id,{innerHTML:'',addEventListener(){}});return elements.get(id);};
 const context=vm.createContext({ForecastMath:M,ForecastDecision:D,Date,console,setInterval(){},fetch:()=>new Promise(()=>{}),
  document:{getElementById:element,querySelectorAll:()=>[],addEventListener(){}}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../docs/assets/research.js'),'utf8'),context);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../docs/assets/app.js'),'utf8'),context);
 const now=Date.now(),ago=minutes=>new Date(now-minutes*60000).toISOString();
 context.rain={generated_at:ago(240),quotes_updated_at:ago(40),cities:[
  {days:{'0':{observed:{latest_at:ago(10)},market:{ticker:'RAIN',retrieved_at:ago(40)}},'1':{market:{ticker:'RAIN-TOMORROW',retrieved_at:ago(5)}}}},
  {days:{'0':{observed:{latest_at:ago(120)}}}},
  {days:{'0':{observed:{latest_at:null}}}},
  {days:{'0':{observed:{latest_at:ago(-60)}}}}
 ]};
 context.highs={generated_at:ago(60),quotes_updated_at:ago(5),cities:[{days:{'0':{ladder:[{market:{ticker:'HIGH',retrieved_at:ago(5)}}]}}}]};
 vm.runInContext("state.rain=rain;state.temperature=highs;state.day='1';drawStatus();",context);
 const html=element('updated').innerHTML;
 for(const stamp of [context.rain.generated_at,context.highs.generated_at,context.rain.quotes_updated_at,context.highs.quotes_updated_at])assert.ok(html.includes(stamp));
 assert.match(html,/10m old<\/time> to .*2\.0h old/);
 assert.match(html,/2\/4 station reports unavailable/);
 assert.match(html,/Observed reports · today/);
 assert.match(element('status').innerHTML,/Rain board is 4\.0h old. Suggestions are disabled/);
 assert.equal(vm.runInContext("freshnessTime('invalid')",context),'Unavailable');
 assert.equal(vm.runInContext('freshnessTime(new Date(Date.now()+3600000).toISOString())',context),'Timestamp in future');
 // Full builds have individual quote times without a batch-refresh timestamp.
 vm.runInContext('state.rain.quotes_updated_at=null;drawStatus()',context);
 assert.ok(element('updated').innerHTML.includes(ago(40)));
 // A newly completed refresh cannot hide a retained stale market quote.
 vm.runInContext('state.rain.quotes_updated_at=new Date().toISOString();drawStatus()',context);
 assert.match(element('updated').innerHTML,/<th scope="row">Oldest market price<\/th><td><time[^>]*>40m old/);
 vm.runInContext("state.rain.cities[0].days['0'].market.retrieved_at=null;drawStatus()",context);
 assert.match(element('updated').innerHTML,/1\/2 price timestamps unavailable/);
 vm.runInContext("state.rain.cities[0].days['1'].market.retrieved_at=null;drawStatus()",context);
 assert.match(element('updated').innerHTML,/<th scope="row">Oldest market price<\/th><td>Unavailable<\/td>/);
});
