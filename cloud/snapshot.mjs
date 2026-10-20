// Shrinks the daily snapshot before it goes into the drafting prompt.
// The full snapshot can be hundreds of KB (every coin's history, level lists, backtest tables),
// which makes the model call slow enough to time out. The drafter needs the headline facts,
// not every row, so long lists are cut to their first items and the model is told so.
const CORE=['BTC','ETH','SOL','XRP','BNB'];
// [max list items, max string length]
const STAGES=[[12,400],[8,300],[5,200],[3,150],[2,100]];

function round(value) {
  return typeof value==='number' && Number.isFinite(value) && !Number.isInteger(value)
    ? Number(value.toPrecision(7)) : value;
}
export function shrink(value,limits) {
  if(Array.isArray(value)) {
    const kept=value.slice(0,limits.items).map(item=>shrink(item,limits));
    if(value.length>limits.items) kept.push(`…另有${value.length-limits.items}项已省略`);
    return kept;
  }
  if(value && typeof value==='object')
    return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,shrink(item,limits)]));
  if(typeof value==='string')
    return value.length>limits.text?value.slice(0,limits.text)+'…':value;
  return round(value);
}
function onlyCoins(summary,keep=10) {
  const metrics=summary?.['指标'];
  if(!metrics || typeof metrics!=='object') return summary;
  const names=[...CORE.filter(name=>metrics[name]),
    ...Object.keys(metrics).filter(name=>!CORE.includes(name))].slice(0,Math.max(keep,CORE.length));
  return {...summary,'指标':Object.fromEntries(names.map(name=>[name,metrics[name]]))};
}
export function compactSnapshot(snapshot,maxChars=50000) {
  const original=JSON.stringify({report:snapshot.report,summary:snapshot.summary,four_lines:snapshot.four_lines||''}).length;
  const report=snapshot.report||{};
  const base={
    report:{ok:report.ok,started_at:report.started_at,finished_at:report.finished_at,
      fresh_symbols:report.fresh_symbols,excluded_symbols:report.excluded_symbols},
    four_lines:String(snapshot.four_lines||'').slice(0,6000),
  };
  const attempts=STAGES.map(([items,text])=>({summary:snapshot.summary,items,text}));
  attempts.push({summary:onlyCoins(snapshot.summary),items:2,text:100});
  for(const {summary,items,text} of attempts) {
    const body=JSON.stringify({...base,summary:shrink(summary,{items,text})});
    if(body.length<=maxChars) return {body,chars:body.length,original,items,trimmed:body.length<original};
  }
  const body=JSON.stringify({...base,summary:'（体积过大，已省略明细，仅保留送审稿四行与校验报告）'});
  return {body,chars:body.length,original,items:0,trimmed:true};
}
