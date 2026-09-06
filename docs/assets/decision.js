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

  const api = {outcome, nextStep, example, reviewRank};
  root.ForecastDecision = api;
  if (typeof module !== 'undefined') module.exports = api;
})(globalThis);
