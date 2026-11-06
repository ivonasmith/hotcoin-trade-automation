// 今日监控计划：把当天的分析结果（快照里的指标 / 关键位 / 做空筛选、台账在场单、程序算出的当日新单）
// 变成主机 15 分钟监控要盯的规则。纯函数，不碰网络和存储；Worker 在快照上传时生成、台账写入后补充，
// 主机通过 GET /pipeline/plan 读取。只报数据，规则和上下文文字里不出现买卖建议。
//
// 规则字段：key（币|条件|价位，主机用它记冷却）、sym、kind、value、src、tag（这个价位是什么）、
// created（可选，缺省按计划生成时间）、band（仅 near：价位上下多少 % 算“接近”）。
// kind：up/down 价格触及；dayup/daydown 日线收盘；h4up/h4down 4h 收盘；surge/plunge 15 分钟涨跌幅 %；near 进入价位附近。
// src：ledger（台账在场单，最高优先、不受静默和上限限制）、order（当日新单）、level（关键位）、ma（MA200）、vol（异常波动）。
import {TRADABLE,fmtPrice,fmtPct} from './order.mjs';

export const PLAN_VERSION=1;
export const MONITOR_COINS=[...TRADABLE];           // 和程序可出单名单、日更取数覆盖的 21 个币一致
const HOUR=3600000;
const BJ_OFFSET=8*HOUR;
export const MAX_PLAN_MS=72*HOUR;                   // 周五的计划最长撑到周一取数
const SRC_RANK={ledger:0,order:1,level:2,ma:3,vol:4};
export const KIND_TEXT={
  up:'价格涨到',down:'价格跌到',dayup:'日线收盘站上',daydown:'日线收盘跌破',
  h4up:'4小时收盘站上',h4down:'4小时收盘跌破',surge:'15分钟涨幅达到',plunge:'15分钟跌幅达到',near:'价格接近'
};

const num=value=>typeof value==='number' && Number.isFinite(value)?value:null;
const clamp=(value,low,high)=>Math.min(high,Math.max(low,value));
const round2=value=>Math.round(value*100)/100;
export const ruleKey=(sym,kind,value)=>`${sym}|${kind}|${Number(value).toPrecision(6)}`;

// 计划有效到下一次工作日 19:00 取数之后 1 小时，最长 72 小时；那之后只剩手动 /watch 规则。
export function planExpiry(now) {
  const bj=new Date(now+BJ_OFFSET);
  let at=Date.UTC(bj.getUTCFullYear(),bj.getUTCMonth(),bj.getUTCDate(),19,0)-BJ_OFFSET;
  for(let i=0;i<8;i++,at+=24*HOUR) {
    const weekday=new Date(at+BJ_OFFSET).getUTCDay();
    if(weekday>=1 && weekday<=5 && at>now+HOUR) return Math.min(at+HOUR,now+MAX_PLAN_MS);
  }
  return now+MAX_PLAN_MS;
}

function nearest(list,side) {
  const items=(Array.isArray(list)?list:[]).filter(level=>num(level?.mid)!==null && num(level?.dist)!==null
    && (side==='above'?level.dist>0:level.dist<0));
  items.sort((a,b)=>Math.abs(a.dist)-Math.abs(b.dist));
  return items[0]||null;
}
// 价位在现价上方就等“涨到”，下方就等“跌到”；没有现价时按 fallbackUp 定。
function touchKind(value,price,fallbackUp) {
  if(price===null) return fallbackUp?'up':'down';
  return value>price?'up':'down';
}
function rule(sym,kind,value,src,tag,extra={}) {
  return {key:ruleKey(sym,kind,value),sym,kind,value,src,tag,...extra};
}

function screenText(screen) {
  if(!screen || typeof screen!=='object') return '';
  const candidates=Array.isArray(screen['候选'])?screen['候选']:[];
  if(candidates.length) return `做空筛选：${candidates.length}个候选压力位`;
  const gate1=String(screen['门①']||''),gate2=String(screen['门②']||'');
  if(gate1.startsWith('未过')) return '做空筛选：门①未过';
  if(gate1.includes('数据不足')) return '做空筛选：数据不足';
  if(gate2.startsWith('撞上')) return '做空筛选：门②撞上反模式';
  if(screen['费率否决']) return '做空筛选：费率否决';
  return '做空筛选：无候选';
}
function analysisText(m,screen) {
  const parts=[];
  if(m['阶段']) parts.push('阶段='+m['阶段']);
  if(num(m.dev200)!==null) parts.push(`距MA200 ${fmtPct(m.dev200)}`);
  if(num(m.rsi_d)!==null) parts.push(`日线RSI ${m.rsi_d}`);
  if(num(m.fund_ann)!==null) parts.push(`资金费率年化 ${fmtPct(m.fund_ann)}`);
  const verdict=screenText(screen);
  if(verdict) parts.push(verdict);
  return parts.join('；');
}
function coinInfo(m,screen) {
  if(!m) return {price:null,ma50:null,atr4_pct:null,near_pct:0.5,rearm_pct:0.5,analysis:''};
  const atrD=num(m.atr_d_pct),atr4=num(m.atr_4_pct);
  return {price:num(m.price),ma50:num(m.ma50),atr4_pct:atr4,
    near_pct:atrD===null?0.5:round2(clamp(0.25*atrD,0.3,1)),   // 判断值：日线 ATR 的四分之一，限定 0.3%–1%
    rearm_pct:atr4===null?0.5:round2(clamp(0.5*atr4,0.3,3)),   // 判断值：4h ATR 的一半，价格离开价位这么远才算重新武装
    analysis:analysisText(m,screen)};
}

// 一个币的关键位规则：日线上下各 1 个位（触及 + 日收确认）、4h 上下各 1 个位（只做收盘确认）、MA200 日收、15 分钟异常波动。
function coinRules(sym,m) {
  const rules=[],price=num(m.price),slow=m['慢档可用']!==false;
  const daily=[];
  if(slow) for(const side of ['above','below']) {
    const level=nearest(m['d1_'+side],side);
    if(!level) continue;
    daily.push(level.mid);
    const role=side==='above'?'压力位':'支撑位',up=side==='above';
    const tag=`日线${role}（${level.segs}段接触，分析时距现价${fmtPct(level.dist)}）`;
    rules.push(rule(sym,up?'up':'down',level.mid,'level',tag));
    rules.push(rule(sym,up?'dayup':'daydown',level.mid,'level',tag));
  }
  for(const side of ['above','below']) {
    const level=nearest(m['h4_'+side],side);
    if(!level || daily.some(mid=>Math.abs(level.mid/mid-1)<0.005)) continue;   // 和日线位几乎重合的 4h 位不重复盯
    const role=side==='above'?'压力位':'支撑位';
    rules.push(rule(sym,side==='above'?'h4up':'h4down',level.mid,'level',`4小时${role}（${level.segs}段接触，分析时距现价${fmtPct(level.dist)}）`));
  }
  const ma200=num(m.ma200);
  if(slow && ma200!==null && price!==null)
    rules.push(rule(sym,price<ma200?'dayup':'daydown',ma200,'ma','MA200（日线）'));
  const atr4=num(m.atr_4_pct);
  const threshold=atr4===null?2:round2(clamp(0.8*atr4,1,8));   // 判断值：4h ATR 的 0.8 倍，限定 1%–8%
  const tag=atr4===null?'15分钟异常波动（4h ATR 缺失，按默认 2%）':`15分钟异常波动（阈值 = 0.8 × 4h ATR ${atr4}%）`;
  rules.push(rule(sym,'surge',threshold,'vol',tag),rule(sym,'plunge',threshold,'vol',tag));
  return rules;
}

function ledgerLine(item) {
  const stopText=num(item.stop_now)!==null?`${fmtPrice(item.stop)}（已上移到 ${fmtPrice(item.stop_now)}）`:fmtPrice(item.stop);
  return `${item.issue?`第${item.issue}期 `:''}$${item.symbol} ${item.side}${item.status}｜进场 ${fmtPrice(item.entry)}｜止损 ${stopText}｜目标 ${fmtPrice(item.target)}`;
}
// 台账在场单 / 当日新单的规则。挂单盯进场价（接近 + 触及）和作废条件；持仓盯止损（触及 + 日收确认）、目标、2R/3R。
export function orderRules(item,info,src='ledger',created) {
  const sym=item.symbol,short=item.side==='做空',price=info.price;
  const entry=num(item.entry),stop=num(item.stop),stopNow=num(item.stop_now),target=num(item.target);
  const note=src==='order'?`当日新单（程序订单，是否发布以台账为准）：${ledgerLine(item).replace(/^第\S+期 /,'')}`:`台账：${ledgerLine(item)}`;
  const extra={note,...(created?{created}:{})};
  const rules=[];
  if(item.status==='挂单中') {
    if(entry!==null) {
      const tag=src==='order'?'当日新单进场价':'台账挂单进场价';
      rules.push(rule(sym,'near',entry,src,tag,{...extra,band:info.near_pct}));
      rules.push(rule(sym,touchKind(entry,price,short),entry,src,tag,extra));
    }
    if(short && info.ma50!==null) rules.push(rule(sym,'dayup',info.ma50,src,'作废条件（成交前日线收盘站上MA50）',extra));
    if(target!==null) rules.push(rule(sym,touchKind(target,price,!short),target,src,short?'目标价（成交前先到则作废）':'目标价',extra));
  } else if(item.status==='持仓中') {
    const stopLevel=stopNow!==null?stopNow:stop;
    if(stopLevel!==null) {
      rules.push(rule(sym,touchKind(stopLevel,price,short),stopLevel,src,'止损价（按日线收盘判断，触及不等于触发）',extra));
      rules.push(rule(sym,short?'dayup':'daydown',stopLevel,src,'止损价日线收盘确认',extra));
    }
    if(target!==null) rules.push(rule(sym,touchKind(target,price,!short),target,src,'目标价',extra));
    if(short && entry!==null && stop!==null && stop>entry) {
      const risk=stop-entry;
      for(const [multiple,text] of [[2,'2R价位（规则里止损下移到进场价）'],[3,'3R价位（规则里止损跟随MA20下移）']]) {
        const value=entry-multiple*risk;
        if(value>0 && (price===null || value<price)) rules.push(rule(sym,'down',value,src,text,extra));
      }
    }
  }
  return rules;
}

function mergeRules(rules) {
  const byKey=new Map();
  for(const item of rules) {
    const current=byKey.get(item.key);
    if(!current || SRC_RANK[item.src]<SRC_RANK[current.src]) byKey.set(item.key,item);
  }
  return [...byKey.values()];
}

export function buildPlan(snapshot,orderPlan,now=Date.now()) {
  const summary=snapshot?.summary||{},metrics=summary['指标']||{},screens=summary['做空筛选']||{};
  const active=(Array.isArray(snapshot?.ledger?.active)?snapshot.ledger.active:[])
    .filter(item=>item && item.symbol && ['挂单中','持仓中'].includes(item.status));
  const order=orderPlan?.allowed?orderPlan.order:null;
  const symbols=[...new Set([...MONITOR_COINS.filter(sym=>metrics[sym]),...active.map(item=>item.symbol),...(order?[order.symbol]:[])])];
  const coins={},all=[];
  for(const sym of symbols) {
    const m=metrics[sym]||null;
    coins[sym]=coinInfo(m,screens[sym]);
    for(const item of active.filter(entry=>entry.symbol===sym)) all.push(...orderRules(item,coins[sym],'ledger'));
    if(order && order.symbol===sym)
      all.push(...orderRules({symbol:sym,side:'做空',status:'挂单中',entry:order.entry,stop:order.stop,target:order.target},coins[sym],'order'));
    if(m) all.push(...coinRules(sym,m));
  }
  const rules=mergeRules(all);
  const finished=Date.parse(snapshot?.report?.finished_at||'');
  return {version:PLAN_VERSION,day:snapshot?.day||null,built_at:now,expires_at:planExpiry(now),
    snapshot_at:Number.isFinite(finished)?finished:null,ledger_updated_to:snapshot?.ledger?.updated_to||null,
    order_symbol:order?order.symbol:null,coins,rules};
}

// 台账任务写入成功后：把刚写进台账的新挂单加进计划（同一个价位的当日新单规则被台账规则取代）。
export function appendLedgerOrder(plan,ledgerOrder,issue,now=Date.now()) {
  if(!plan || !ledgerOrder?.symbol || now>=plan.expires_at) return plan;
  const sym=ledgerOrder.symbol;
  const info=plan.coins?.[sym]||coinInfo(null,null);
  const item={symbol:sym,side:'做空',status:'挂单中',issue,entry:ledgerOrder.entry,stop:ledgerOrder.stop,target:ledgerOrder.target};
  const added=orderRules(item,info,'ledger',now);
  return {...plan,coins:{...plan.coins,[sym]:info},rules:mergeRules([...added,...plan.rules])};
}

export function ruleText(item) {
  const value=item.kind==='surge' || item.kind==='plunge'?`${item.value}%`
    :item.kind==='near'?`${fmtPrice(item.value)}（±${item.band}%）`:fmtPrice(item.value);
  return `${KIND_TEXT[item.kind]||item.kind} ${value}｜${item.tag}`;
}
const bjStamp=ms=>new Date(ms+BJ_OFFSET).toISOString().slice(5,16).replace('T',' ');
// Telegram /plan 的回复：不带币就是总览，带币就列出这个币的全部规则。
export function describePlan(plan,symbol,now=Date.now()) {
  if(!plan) return '今天还没有监控计划。每个工作日 19:00 取数上传后会自动生成。';
  const expired=now>=plan.expires_at;
  const head=`今日监控计划：${bjStamp(plan.built_at)} 生成（北京时间），${expired?'已过期':'有效到 '+bjStamp(plan.expires_at)}；${plan.rules.length} 条规则，${Object.keys(plan.coins).length} 个币。`;
  const sym=String(symbol||'').replace(/^\$/,'').toUpperCase();
  if(sym) {
    const mine=plan.rules.filter(item=>item.sym===sym);
    if(!mine.length) return head+`\n${sym} 不在计划里。`;
    const info=plan.coins[sym];
    return [head,`${sym} 共 ${mine.length} 条：`,...mine.map(item=>'· '+ruleText(item)),
      info?.analysis?`分析：${info.analysis}`:''].filter(Boolean).join('\n').slice(0,3500);
  }
  const lines=Object.keys(plan.coins).map(name=>{
    const mine=plan.rules.filter(item=>item.sym===name),ledger=mine.filter(item=>item.src==='ledger'||item.src==='order').length;
    return `${name} ${mine.length}条${ledger?`（含台账/新单 ${ledger}）`:''}`;
  });
  return [head,lines.join('、'),'发 /plan 币 看某个币的全部规则，例：/plan SOL'].join('\n').slice(0,3500);
}
