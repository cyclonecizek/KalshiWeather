/* Weather-to-market explanations. These functions never enable an order. */
(function (root) {
  'use strict';
  function outcome(kind, bracket, side) {
    if (kind === 'rain') return side === 'YES' ? 'Measurable rain at the station' : 'No measurable rain at the station';
    const range = bracket.label || 'this temperature range';
    return side === 'YES' ? `High in ${range}` : `High outside ${range}`;
  }

  function nextStep(reasons, edge = {}) {
    const tasks = [];
    if (reasons.some(r => /stale|age unknown|refresh/i.test(r))) tasks.push('Refresh the forecast and prices before comparing them.');
    if (reasons.some(r => /closed|inactive|closing time/i.test(r))) tasks.push('Check whether this market is still open.');
    if (reasons.some(r => /settlement/i.test(r))) tasks.push('Confirm the exact station, reporting day, and final data source in the contract rules.');
    if (reasons.some(r => /calibration/i.test(r))) tasks.push('Record your forecast and practice on paper while this model builds a verified track record.');
    if (reasons.some(r => /source|incomplete|families|forecast stale/i.test(r))) tasks.push('Inspect missing guidance and observation coverage before trusting the probability.');
    if (reasons.some(r => /depth|spread|bid|ask|quote|fee/i.test(r))) tasks.push('Check the current purchase price, fees, and number of contracts available on Kalshi.');
    if (reasons.some(r => /nearly complete/i.test(r))) tasks.push('Review the observed outcome and time remaining; this window is too late for a new paper proposal.');
    if (reasons.some(r => /budget|stake|already recorded/i.test(r))) tasks.push('Review the practice ledger before adding another position.');
    if (edge.flag === 'suspect' || reasons.some(r => /edge outside/i.test(r))) tasks.unshift('Investigate the unusually large price difference for a data or contract mismatch.');
    if (!reasons.length) return {label: 'Review for paper practice', tone: 'ready', tasks: ['Check the weather reasoning, then use the practice calculator. Eligibility is not a recommendation to buy.']};
    if (!tasks.length) tasks.push('Open the detailed checks below and resolve the missing evidence.');
    return {label: /stale|age unknown/i.test(reasons.join(' ')) ? 'Refresh before deciding' : 'Wait and investigate', tone: 'wait', tasks: [...new Set(tasks)]};
  }

  function example(probability, priceCents, quantity, feeRate) {
    if (![probability, priceCents, quantity, feeRate].every(Number.isFinite) ||
        probability < 0 || probability > 1 || priceCents <= 0 || priceCents >= 100 ||
        !Number.isInteger(quantity) || quantity < 1 || quantity > 1000 || feeRate < 0) return null;
    const price = priceCents / 100;
    // Match the server's per-order taker fee, rounded up to a cent.
    const fee = Math.ceil(feeRate * quantity * price * (1 - price) * 100) / 100;
    const cost = quantity * price + fee;
    return {fee, cost, maxLoss: cost, winNet: quantity - cost,
      breakEven: cost / quantity, expectedNet: probability * quantity - cost};
  }

  function reviewRank(row) {
    if (row.e.flag === 'suspect' || row.e.ev_cents > 25) return -10000;
    const ready = row.reasons.length ? 0 : 1000;
    const complete = row.d.data_quality === 'ok' ? 100 : 0;
    return ready + complete + Math.min(25, row.e.ev_cents);
  }

  // Integer cents throughout allocation; a budget is a loss ceiling, not a target.
  function allocate(candidates, budget, committed = 0) {
    if (![budget, committed].every(Number.isFinite) || budget < 1 || budget > 100000 ||
        committed < 0 || committed > budget) return null;
    const total = Math.floor(budget * 100), reserved = Math.ceil(committed * 100);
    const cap = Math.max(0, Math.floor(total * .25) - reserved);
    let spent = 0;
    const cities = new Map(), events = new Set(), tickers = new Set();
    const rows = candidates.map(c => {
      const reasons = [...(c.reasons || [])];
      const one = example(c.probability, c.price, 1, c.feeRate);
      if (!one || !c.ticker || !c.city || !c.event) reasons.push('Missing probability, price, fee, or contract identity');
      if (!Number.isFinite(c.depth) || c.depth < 1) reasons.push('No confirmed contracts available at this price');
      const buffered = c.probability - .05;
      // One-contract rounded fee is a conservative upper bound per contract.
      const fraction = one && one.cost < 1 ? Math.max(0, (buffered - one.cost) / (1 - one.cost)) * .25 : 0;
      if (!fraction) reasons.push('No advantage remains after fees and a 5-point probability buffer');
      return {...c, reasons: [...new Set(reasons)], fraction, contracts: 0, cost: 0, fee: 0, expectedNet: 0};
    }).sort((a,b) => b.fraction-a.fraction || String(a.ticker).localeCompare(String(b.ticker)));
    for (const r of rows) {
      if (r.reasons.length) continue;
      if (tickers.has(r.ticker) || events.has(r.event)) {r.reasons.push('Another position already covers this station, product, and day'); continue;}
      const limit = Math.min(cap-spent, Math.floor(total*.05),
        Math.floor(total*.10)-(cities.get(r.city)||0), Math.floor(total*r.fraction));
      let n = Math.max(0, Math.min(1000, Math.floor(r.depth), Math.floor(limit/r.price)));
      let cost;
      while (n) {
        const e = example(r.probability, r.price, n, r.feeRate);
        cost = Math.ceil(e.cost*100-1e-9);
        if (cost <= limit) {Object.assign(r, {contracts:n, cost:cost/100, fee:e.fee, expectedNet:e.expectedNet}); break;}
        n--;
      }
      if (!n) {r.reasons.push('Budget limit reached or stake smaller than one contract'); continue;}
      spent += cost; cities.set(r.city,(cities.get(r.city)||0)+cost);
      events.add(r.event); tickers.add(r.ticker);
    }
    return {rows, allocated:spent/100, remaining:(total-reserved-spent)/100, committed:reserved/100,
      maxNewAllocation:cap/100, expectedNet:rows.reduce((s,r)=>s+r.expectedNet,0)};
  }

  function savedProbability(adjustments, board, city, day, kind, ticker, now=Date.now()) {
    const saved = adjustments.filter(a => a.city===city && a.date===day.date && a.kind===kind)
      .sort((a,b)=>Date.parse(b.created_at)-Date.parse(a.created_at))[0];
    if (!saved || saved.snapshot_id!==board.snapshot_id ||
        !Number.isFinite(Date.parse(saved.created_at)) || Date.parse(saved.created_at)>now+60000) return null;
    const i = saved.tickers?.indexOf(ticker), p = saved.adjusted_probabilities?.[i];
    if (!(i>=0) || !Number.isFinite(p) || p<0 || p>1) return null;
    const values=saved.adjusted_probabilities;
    if (new Set(saved.tickers).size!==saved.tickers.length || values.length!==saved.tickers.length ||
        !values.every(v=>Number.isFinite(v)&&v>=0&&v<=1) ||
        (kind==='temperature' && Math.abs(values.reduce((s,v)=>s+v,0)-1)>1e-5)) return null;
    return {probability:p, id:saved.id, created_at:saved.created_at};
  }

  const api = {outcome, nextStep, example, reviewRank, allocate, savedProbability};
  root.ForecastDecision = api;
  if (typeof module !== 'undefined') module.exports = api;
})(globalThis);
