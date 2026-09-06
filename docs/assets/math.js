/* Pure forecast calculations shared by previews and node regression tests. */
(function(root){
const Q=[.01,.02,.05,.1,.2,.3,.4,.5,.6,.7,.8,.9,.95,.98,.99];
function erf(x){const sign=x<0?-1:1;x=Math.abs(x);const t=1/(1+.3275911*x);return sign*(1-(((((1.061405429*t-1.453152027)*t)+1.421413741)*t-.284496736)*t+.254829592)*t*Math.exp(-x*x));}
function probit(p){let lo=-9,hi=9;for(let i=0;i<70;i++){const m=(lo+hi)/2;if((1+erf(m/Math.SQRT2))/2<p)lo=m;else hi=m;}return (lo+hi)/2;}
function cdf(v,x,floor=null){if(floor!==null&&x<=floor)return 0;if(x<=v[0]){const s=Math.max((v[1]-v[0])/(probit(Q[1])-probit(Q[0])),.3);return Math.min((1+erf(((x-v[0])/s+probit(Q[0]))/Math.SQRT2))/2,Q[0]);}if(x>=v.at(-1)){const s=Math.max((v.at(-1)-v.at(-2))/(probit(Q.at(-1))-probit(Q.at(-2))),.3);return Math.max((1+erf(((x-v.at(-1))/s+probit(Q.at(-1)))/Math.SQRT2))/2,Q.at(-1));}let i=1;while(v[i]<x)i++;return Q[i-1]+(Q[i]-Q[i-1])*(x-v[i-1])/(v[i]-v[i-1]);}
function between(v,lo,hi,floor=null){return Math.max(0,Math.min(1,(hi==null?1:cdf(v,hi+.5,floor))-(lo==null?0:cdf(v,lo-.5,floor))));}
function adjust(v,shift,spread,floor=null){const mid=v[7];return v.map(x=>Math.max(floor==null?-Infinity:floor,mid+shift+(x-mid)*spread));}
function age(stamp,now=Date.now()){const at=Date.parse(stamp),age=(now-at)/60000;return Number.isFinite(at)&&age>=-1?age:Infinity;}
function eligibility(edge,quote,board,now=Date.now()){const why=[...(edge?.eligibility?.reasons||[])];if(!edge?.eligibility)why.push('Eligibility not evaluated');if(age(quote?.retrieved_at,now)>(board?.execution_policy?.max_quote_age_minutes||20))why.push('Quote is stale');if(age(board?.generated_at,now)>(board?.execution_policy?.max_data_age_minutes||180))why.push('Board is stale');if(quote?.close_time&&Date.parse(quote.close_time)<=now)why.push('Market closed');return [...new Set(why)];}
const api={Q,cdf,between,adjust,age,eligibility};root.ForecastMath=api;if(typeof module!=='undefined')module.exports=api;
})(globalThis);
