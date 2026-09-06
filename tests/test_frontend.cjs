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
