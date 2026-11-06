// 监控清单：用户在 Telegram 里发 /watch 指令，规则存在后台；服务器每 15 分钟读一次清单去检查。
// 这里只放“怎么读指令、怎么描述规则”，检查价格的逻辑在服务器上的 host/monitor.py。
export const MAX_RULES=30;
export const KINDS={
  up:{words:['上破','突破','涨到','>','above'],text:'价格涨到',needs:'price'},
  down:{words:['跌破','跌到','<','below'],text:'价格跌到',needs:'price'},
  dayup:{words:['日收上','日收站上','日线收上'],text:'日线收盘站上',needs:'price'},
  daydown:{words:['日收下','日收跌破','日线收下'],text:'日线收盘跌破',needs:'price'},
  h4up:{words:['4h收上','4h收站上'],text:'4小时收盘站上',needs:'price'},
  h4down:{words:['4h收下','4h收跌破'],text:'4小时收盘跌破',needs:'price'},
  surge:{words:['急涨'],text:'15分钟内涨幅达到',needs:'pct'},
  plunge:{words:['急跌'],text:'15分钟内跌幅达到',needs:'pct'}
};
export const USAGE=[
  '用法：/watch 币 条件 数字 [常驻]',
  '例：/watch SOL 跌破 95　　价格碰到就提醒',
  '例：/watch BTC 日收上 82314　日线收盘站上才提醒',
  '例：/watch ETH 4h收下 2400　4小时收盘跌破才提醒',
  '例：/watch DOGE 急涨 3　　15分钟涨幅 ≥3% 就提醒',
  '默认提醒一次就自动取消；末尾加「常驻」则每次触发都提醒（同一条6小时内不重复）。',
  '查看：/watchlist　　取消：/unwatch 序号 或 /unwatch 币 或 /unwatch all'
].join('\n');

export function parseWatch(text) {
  const tokens=String(text||'').trim().split(/\s+/).slice(1).filter(Boolean);
  const repeat=tokens.some(token=>token==='常驻');
  const rest=tokens.filter(token=>token!=='常驻');
  if(rest.length<3) return {ok:false,error:'参数不够。\n'+USAGE};
  let sym=rest[0].replace(/^\$/,'').toUpperCase().replace(/(USDT|USDC|PERP)$/,'');
  if(!/^[A-Z0-9]{2,15}$/.test(sym)) return {ok:false,error:'币的代码不对，只能是字母和数字，例如 SOL、BTC、1000PEPE。'};
  const word=rest[1].toLowerCase();
  const kind=Object.keys(KINDS).find(key=>KINDS[key].words.some(candidate=>candidate.toLowerCase()===word));
  if(!kind) return {ok:false,error:`没认出条件「${rest[1]}」。\n`+USAGE};
  const value=Number(rest[2].replace(/[,%]/g,''));
  if(!Number.isFinite(value) || value<=0) return {ok:false,error:'数字要大于 0。'};
  if(KINDS[kind].needs==='pct' && (value<0.3 || value>50)) return {ok:false,error:'涨跌幅请填 0.3 到 50 之间的百分数。'};
  return {ok:true,rule:{sym,kind,value,repeat}};
}
export function describeRule(rule) {
  const kind=KINDS[rule.kind];
  const value=kind.needs==='pct'?`${rule.value}%`:String(rule.value);
  return `#${rule.id} ${rule.sym} ${kind.text} ${value}（${rule.repeat?'常驻':'提醒一次'}）`;
}
export function addRule(list,parsed,seq,now) {
  const dup=list.find(item=>item.sym===parsed.sym && item.kind===parsed.kind && item.value===parsed.value);
  if(dup) return {ok:false,error:'已经有这条了：'+describeRule(dup)};
  if(list.length>=MAX_RULES) return {ok:false,error:`最多 ${MAX_RULES} 条，先用 /unwatch 取消几条。`};
  const rule={id:seq,...parsed,created:now,last_fired:null};
  return {ok:true,rule,list:[...list,rule]};
}
export function removeRules(list,target) {
  const key=String(target||'').trim();
  if(!key) return {removed:[],list};
  if(key.toLowerCase()==='all') return {removed:list,list:[]};
  const id=/^#?\d+$/.test(key)?Number(key.replace('#','')):null;
  const sym=key.replace(/^\$/,'').toUpperCase().replace(/(USDT|USDC|PERP)$/,'');
  const removed=list.filter(item=>id!==null?item.id===id:item.sym===sym);
  return {removed,list:list.filter(item=>!removed.includes(item))};
}
// 服务器报告“这些规则触发了”：一次性的删掉，常驻的记下触发时间。
export function applyFired(list,fired,now) {
  const set=new Set((fired||[]).map(Number));
  return list.flatMap(rule=>{
    if(!set.has(rule.id)) return [rule];
    return rule.repeat?[{...rule,last_fired:now}]:[];
  });
}
