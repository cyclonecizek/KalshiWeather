const test=require('node:test'),assert=require('node:assert/strict');
const M=require('../docs/assets/math.js');
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
