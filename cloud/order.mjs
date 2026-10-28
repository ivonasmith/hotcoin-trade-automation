// Program-side trade decision. The drafting model never computes or chooses order numbers or the
// "may we open a new order today" verdict: this module reads the snapshot's 做空筛选 / L1 discount
// results (already computed by the collection scripts per 《操作规格》v6.5 §08) and produces one
// canonical order plus the reasons when there is none. The model only writes it up.
const CORE=['BTC','ETH','SOL','XRP','BNB'];
// Coins the program may write an order for. Core coins come first (they win ties); the rest are screened coins the owner
// treats like majors (10-21): the whole 17-coin daily list plus AAVE/WLD/SUI/INJ (DOGE is a meme by origin, but a top-cap
// long-standing coin, so not treated as a small meme). Anything else that passes the screen still needs a human decision.
const TRADABLE=[...CORE,'UNI','CAKE','HYPE','ENA','TAO','RAY','DOGE','LDO','ARB','NEAR','ZEC','JUP','AAVE','WLD','SUI','INJ'];
const BASE_USD=2000;
const VALID_DAYS=15;
const PRICE_TOLERANCE=0.0001; // 0.01% between sources before we call it a conflict

const num=value=>typeof value==='number' && Number.isFinite(value)?value:null;

export function fmtPrice(value) {
  const n=Number(value);
  if(!Number.isFinite(n)) return String(value);
  if(n>=1000) return n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  if(n>=1) return String(Number(n.toFixed(4)));
  return String(Number(n.toPrecision(4)));
}
export function fmtPct(value) {
  const n=Number(value);
  return `${n>=0?'+':'-'}${Math.abs(n).toFixed(2)}%`;
}
function fmtSize(multiple) {
  return `${(Math.round(multiple*100)/100).toFixed(2)}仓`;
}
export function expiryText(day,days=VALID_DAYS) {
  const date=new Date(Date.parse(day+'T00:00:00Z')+days*86400000);
  return `${date.getUTCMonth()+1}月${date.getUTCDate()}日`;
}

// Why a core coin produced no candidate, in plain words, from the gate fields the screen records.
function whyNoCandidate(symbol,screen) {
  if(!screen) return `$${symbol}：筛选结果缺失`;
  const gate1=String(screen['门①']||''),gate2=String(screen['门②']||'');
  if(gate1.startsWith('未过')) return `$${symbol}：${gate1.replace('未过：','')}`;
  if(gate1.includes('数据不足')) return `$${symbol}：阶段数据不足，算不出能否做空`;
  if(gate2.startsWith('撞上')) return `$${symbol}：${gate2.replace('撞上反模式：','反模式：')}`;
  if(screen['费率否决']) return `$${symbol}：${screen['门③路径']||'费率条件否决'}`;
  if(typeof screen['门③需要人工核实']==='string' && screen['门③需要人工核实']) return `$${symbol}：${screen['门③需要人工核实']}`;
  return `$${symbol}：上方没有够格的压力位`;
}


const ACTIVE=['挂单中','持仓中'];
const MAX_PENDING=5,MAX_SAME_SIDE_MAJOR=3,MAX_NOTIONAL=20000;
// Reads the ledger the collection job copied from Notion. Returns the facts and the blockers it implies.
// A held short is "profit locked" (2026-10-26 rule change) when its moved stop, as booked in the ledger log, sits strictly
// below the entry (a stop-out would still pay) and the live price is still under that stop (so it is in profit and not
// already due to exit). Anything we cannot read (no booked move, no price) is NOT locked: ambiguity never opens room.
export function profitLocked(item,price) {
  if(!item || item.status!=='持仓中' || item.side!=='做空') return false;
  const entry=num(item.entry),stop=num(item.stop_now),last=num(price);
  return entry!==null && stop!==null && last!==null && stop<entry && last<stop;
}
function ledgerView(ledger,day,metrics={}) {
  if(!ledger || ledger.ok!==true || !Array.isArray(ledger.active)) return null;
  const active=ledger.active.filter(item=>ACTIVE.includes(item.status));
  const pending=active.filter(item=>item.status==='挂单中').length;
  const majorShorts=active.filter(item=>item.side==='做空' && TRADABLE.includes(item.symbol));
  const lockedShorts=majorShorts.filter(item=>profitLocked(item,metrics?.[item.symbol]?.price));
  const countedShorts=majorShorts.length-lockedShorts.length;
  const notional=active.reduce((sum,item)=>sum+BASE_USD*(Number(item.size)||0),0);
  const staleDays=ledger.updated_to?Math.floor((Date.parse(day+'T00:00:00Z')-Date.parse(ledger.updated_to+'T00:00:00Z'))/86400000):null;
  return {active,pending,majorShorts,lockedShorts,countedShorts,notional,staleDays,updated_to:ledger.updated_to||null,
    symbolsWithShort:new Set(active.filter(item=>item.side==='做空').map(item=>item.symbol))};
}

// Warnings only: the order itself is not changed. The target is a mechanical 4R, so nothing in the order rules checks
// whether support sits on the way down, how close MA200 is, or whether the coin can really travel that far in 15 days.
// Each line is for the reviewer (送审备注), thresholds are judgement, not backtested.
export function riskNotes(symbol,order,m) {
  if(!order || !m) return [];
  const notes=[];
  const atr=num(m.atr_d_pct),price=num(m.price),dev200=num(m.dev200);
  const supports=(Array.isArray(m.d1_below)?m.d1_below:[]).filter(level=>num(level.mid)!==null && num(level.segs)>=3
    && level.mid<(price??order.entry) && level.mid>order.target && level.mid<order.entry);
  for(const level of supports.slice(0,2))
    notes.push(`风险提示：$${symbol} 下方 ${fmtPrice(level.mid)}（距现价${fmtPct(level.dist)}，${level.segs}段接触）落在进场 ${fmtPrice(order.entry)} 与目标 ${fmtPrice(order.target)} 之间；目标是按4R机械算的，没考虑这条承接，到不到目标要你判断`);
  if(atr!==null && atr>0) {
    const targetPct=(order.entry-order.target)/order.entry*100;
    if(targetPct>3*atr)
      notes.push(`风险提示：$${symbol} 目标距进场 ${targetPct.toFixed(1)}%，是日线ATR14（${atr.toFixed(2)}%）的${(targetPct/atr).toFixed(1)}倍，有效期内走到的可能偏低（止损按2%下限算，所以目标被拉远）`);
    if(dev200!==null && dev200>=0 && dev200<=2*atr)
      notes.push(`风险提示：$${symbol} 现价只在MA200上方 ${dev200.toFixed(1)}%（不到2倍日线ATR），下方有长期均线承接，做空空间可能比目标小`);
  }
  return notes;
}

export function buildOrderPlan(snapshot,day,now=Date.now()) {
  const summary=snapshot?.summary;
  const plan={allowed:false,order:null,reasons:[],human_checks:[],notes:[],day};
  const finishedAt=Date.parse(snapshot?.report?.finished_at||'');
  if(Number.isFinite(finishedAt)) {
    const hours=(now-finishedAt)/3600000;
    const when=new Date(finishedAt+8*3600000).toISOString().slice(5,16).replace('T',' ');
    plan.notes.push(`行情快照采集完成于北京时间 ${when}（距现在约${hours.toFixed(1)}小时）`
      +(hours>4?'：价格已不新，挂单前必须先对一下交易所实时价，进场价如果已被越过，这张单一挂出去就会立刻成交':''));
  }
  const view=ledgerView(snapshot?.ledger,day,snapshot?.summary?.['指标']);
  const humanChecks=[
    view?'熔断阶梯程序暂未核算（连续止损、账户回撤、利润回吐），请确认没有处于停机档':
      '台账没有读到：请核对熔断状态、已挂单是否不超过5张、同方向主流币是否不超过3个（利润已锁定的不计）',
    '日线收盘口径：止损与作废条件以收盘价判断，请确认今日日线尚未收盘'
  ];
  if(view && view.staleDays!==null && view.staleDays>=2)
    humanChecks.unshift(`台账最后更新到 ${view.updated_to}，已过去${view.staleDays}天，成交、作废、止损状态可能没更新，请先更新台账`);
  const proposals=Array.isArray(snapshot?.ledger?.proposals)?snapshot.ledger.proposals.filter(item=>item.type!=='review'):[];
  if(proposals.length) humanChecks.unshift(`台账有${proposals.length}项应更新（程序只核对、不改台账）：`+proposals.slice(0,3).map(item=>item.text).join('；'));
  if(snapshot?.ledger && snapshot.ledger.ok===false) plan.notes.push('台账读取失败：'+String(snapshot.ledger.error||'').slice(0,120));
  if(!summary || typeof summary!=='object') {
    plan.reasons.push('当日交易快照缺失，不开新单');
    return plan;
  }
  const screens=summary['做空筛选'];
  if(!screens || typeof screens!=='object') {
    plan.reasons.push('快照里没有做空筛选结果，不开新单');
    return plan;
  }
  const discount=summary['L1L4']?.['L1折扣判定'];
  if(!discount || discount['数据完整']!==true) {
    plan.reasons.push('宏观层读数不全，折扣条件判定不了，不能按“未触发”处理，不开新单');
    plan.human_checks.push('宏观层读数不全，需补齐后再判断');
    return plan;
  }
  const multiplier=num(discount['新开仓倍数'])??1;
  if(discount['不加仓']) plan.notes.push('实际利率急升但附加条件未配齐：已有仓位不加仓');
  if((discount['边缘提示']||[]).length) plan.notes.push('折扣条件边缘触发：'+discount['边缘提示'].join('；'));

  const metrics=summary['指标']||{};
  const eligible=[];
  for(const symbol of Object.keys(screens)) {
    const screen=screens[symbol];
    const candidates=Array.isArray(screen?.['候选'])?screen['候选']:[];
    if(!candidates.length) {
      if(CORE.includes(symbol)) plan.reasons.push(whyNoCandidate(symbol,screen));
      continue;
    }
    if(screen['费率否决']) {
      plan.reasons.push(whyNoCandidate(symbol,screen));
      continue;
    }
    if(typeof screen['门③需要人工核实']==='string' && screen['门③需要人工核实']) {
      plan.reasons.push(`$${symbol}：${screen['门③需要人工核实']}`);
      continue;
    }
    if(!TRADABLE.includes(symbol)) {
      plan.human_checks.push(`$${symbol} 过了筛选但不在程序可出单的名单里（名单：${TRADABLE.join('、')}），需要你先决定能不能做，程序不出单`);
      continue;
    }
    if(view?.symbolsWithShort.has(symbol)) {
      plan.reasons.push(`$${symbol}：台账里已经有一张在场的空单，不重复挂`);
      continue;
    }
    const first=candidates.find(item=>num(item['进场']) && num(item['止损']) && num(item['目标']));
    if(first) eligible.push({symbol,screen,candidate:first});
  }
  if(!eligible.length) {
    if(!plan.reasons.length) plan.reasons.push('今天没有币种同时过门①②③并有压力位候选');
    return plan;
  }
  if(view) {
    if(view.pending>=MAX_PENDING) {
      plan.reasons.unshift(`台账里已有${view.pending}张挂单，达到${MAX_PENDING}张上限，要等有单成交、作废或到期才能新增`);
      return plan;
    }
    if(view.countedShorts>=MAX_SAME_SIDE_MAJOR) {
      plan.reasons.unshift(`台账里主流币空单已有${view.majorShorts.length}张`
        +(view.lockedShorts.length?`，其中${view.lockedShorts.length}张利润已锁定不计入，其余${view.countedShorts}张`:'')
        +`，达到同向${MAX_SAME_SIDE_MAJOR}张上限`
        +(view.majorShorts.some(item=>item.status==='持仓中' && num(item.stop_now)===null)
          ?'（持仓中的空单如果止损已上移到进场价以下，要在台账「状态变更记录」里记一条“止损上移”，程序才认它已锁定利润）':''));
      return plan;
    }
  }
  // One new order per post: main coins in fixed order, then the nearest level.
  eligible.sort((a,b)=>TRADABLE.indexOf(a.symbol)-TRADABLE.indexOf(b.symbol)
    ||Math.abs(a.candidate['距现价%']??1e9)-Math.abs(b.candidate['距现价%']??1e9));
  const {symbol,screen,candidate}=eligible[0];
  const entry=candidate['进场'],stop=candidate['止损'],target=candidate['目标'];
  if(!(stop>entry && target<entry)) {
    plan.reasons.push(`$${symbol}：筛选给出的止损/目标方向不对（做空止损应高于进场、目标应低于进场），不出单`);
    return plan;
  }
  const price=num(metrics[symbol]?.price)??num(screen['现价']);
  const otherPrice=num(screen['现价']);
  if(price && otherPrice && Math.abs(price/otherPrice-1)>PRICE_TOLERANCE)
    plan.notes.push(`$${symbol}现价两个来源不一致：指标 ${fmtPrice(price)}，做空筛选 ${fmtPrice(otherPrice)}；正文不写现价`);
  const dist=num(candidate['距现价%']);
  if(price && dist!==null) {
    const recomputed=(entry/price-1)*100;
    if(Math.abs(recomputed-dist)>0.1)
      plan.notes.push(`距现价${fmtPct(dist)}与按现价 ${fmtPrice(price)} 重算的${fmtPct(recomputed)}不一致，以筛选值为准，请核对`);
  }
  const ratio=(entry-target)/(stop-entry);
  const direction=num(candidate['方向倍数'])??0.5;
  const level=num(candidate['仓位倍数'])??1;
  const size=direction*level*multiplier;
  plan.order={
    symbol,side:'空',entry,stop,target,
    dist_pct:dist,
    stop_pct:(stop/entry-1)*100,
    ratio,ratio_text:`1:${Math.round(ratio*10)/10}`,
    size,size_text:fmtSize(size),amount_usd:Math.round(BASE_USD*size),
    expires:expiryText(day),
    discount_multiplier:multiplier,
    touched_recent:Boolean(candidate['15天内碰过']),
    cancel_ma50:num(metrics[symbol]?.ma50),
    r2:entry-2*(stop-entry),r3:entry-3*(stop-entry),
    path:screen['门③路径']||null,
    funding_7d:num(screen['费率7日均']),funding_30d:num(screen['费率30日均'])
  };
  if(view && view.notional+BASE_USD*size>MAX_NOTIONAL) {
    plan.order=null;
    plan.reasons.unshift(`加上这张单，台账里的名义总仓位会超过${MAX_NOTIONAL}美元上限`);
    return plan;
  }
  plan.allowed=true;
  if(view) plan.notes.push(`台账已读取（更新到 ${view.updated_to||'未知'}）：在场${view.active.length}张，挂单${view.pending}/${MAX_PENDING}，主流币空单${view.countedShorts}/${MAX_SAME_SIDE_MAJOR}`
    +(view.lockedShorts.length?`（另有${view.lockedShorts.length}张利润已锁定，不占同向名额：${view.lockedShorts.map(item=>'$'+item.symbol).join('、')}）`:'')
    +`，名义约${Math.round(view.notional)}美元；加上本单仍在上限内`);
  plan.notes.push(...riskNotes(symbol,plan.order,metrics[symbol]));
  if(plan.order.touched_recent) plan.notes.push('这个价位15天内被碰过，仓位已按程序打折');
  if(multiplier<1) plan.notes.push('宏观层折扣已触发，仓位已按程序打折');
  plan.human_checks.unshift(...humanChecks);
  const dropped=eligible.slice(1).map(item=>'$'+item.symbol);
  if(dropped.length) plan.notes.push(`${dropped.join('、')}也过了筛选，本帖只写一张单，需要时请另行安排`);
  return plan;
}

// The block that goes into the drafting prompt.
export function orderBlock(plan) {
  const lines=['程序已定的交易决定（由程序按规格计算，你不得更改、取整、换算或另选价位）：'];
  if(plan.allowed) {
    const o=plan.order;
    lines.push('结论：今天可以开这一张新单。');
    lines.push(`新单：$${o.symbol} ${o.side}单｜进场限价 ${fmtPrice(o.entry)}｜距现价 ${o.dist_pct===null?'待补':fmtPct(o.dist_pct)}｜止损 ${fmtPrice(o.stop)}（按日线收盘判断）｜目标 ${fmtPrice(o.target)}｜盈亏比 ${o.ratio_text}｜仓位 ${o.size_text}｜有效期到 ${o.expires}`);
    lines.push(`作废与撤单条件（原样写，不增不减，不换成别的均线）：成交前日线收盘站上50日均线${o.cancel_ma50?'（目前约 '+fmtPrice(o.cancel_ma50)+'）':''}，这单作废；成交前价格先跌到目标 ${fmtPrice(o.target)}，这单作废；成交前资金费率年化转负，撤单。`);
    lines.push(`成交后的止损处理（字数紧时压成一句，数字不改）：价格到 ${fmtPrice(o.r2)} 时，止损下移到进场价；到 ${fmtPrice(o.r3)} 时，止损跟随20日均线下移；止损只往盈利方向移，一律按日线收盘判断。`);
    lines.push('正文只写这一张单，上面每一项原样写入，不得增加其他新单。');
  } else {
    lines.push('结论：今天不开新单。正文必须明确写“今天不开新单”，严禁出现任何限价、止损价、目标价、盈亏比、仓位或有效期。');
  }
  if(plan.reasons.length) lines.push(plan.allowed
    ?'其他币种的筛选情况（只供你写送审备注，正文一个字都不要提）：\n'+plan.reasons.map(item=>'- '+item).join('\n')
    :'不出单的原因（只供你写送审备注；正文不解释为什么不开，只写“今天不开新单”和行情本身）：\n'+plan.reasons.map(item=>'- '+item).join('\n'));
  const notes=[...plan.notes,...plan.human_checks];
  if(notes.length) lines.push('必须写进送审备注的事项：\n'+notes.map(item=>'- '+item).join('\n'));
  return lines.join('\n');
}

// Reviewer-facing warnings when the draft disagrees with the program's decision.
export function checkAgainstPlan(content,plan) {
  const body=String(content).split('送审备注：')[0].replace(/^\s*审核正文：/,'');
  const warnings=[];
  if(!plan.allowed) {
    if(/限价|盈亏比|有效期到|止损\s*[0-9]/.test(body)) warnings.push('程序判定今天不开新单，但正文出现了订单内容');
    return warnings;
  }
  const o=plan.order;
  const expected=[['进场限价',fmtPrice(o.entry)],['止损',fmtPrice(o.stop)],['目标',fmtPrice(o.target)],
    ['盈亏比',o.ratio_text],['仓位',o.size_text],['有效期',o.expires]];
  if(o.dist_pct!==null) expected.push(['距现价',fmtPct(o.dist_pct)]);
  expected.push(['50日均线','50日均线']);
  const normalized=body.replace(/\s/g,'');
  const missing=expected.filter(([,value])=>!normalized.includes(value.replace(/\s/g,''))).map(([label,value])=>`${label} ${value}`);
  if(missing.length) warnings.push(`新单数字与程序计算不一致或缺失：${missing.join('、')}`);
  return warnings;
}

// Track record the drafter may quote. Same arithmetic as the Sunday poster: return = cumulative P&L / (equity - cumulative P&L).
// Only numbers computed here may appear in the post; losses are listed as plainly as wins.
export function recordBlock(ledger) {
  if(!ledger || ledger.ok!==true || !ledger.counts) return '程序给的战绩：台账没读到，正文不写战绩，也不要估数字。';
  const cum=ledger.cumulative_pnl_usd,equity=ledger.equity_usd;
  const lines=['程序给的战绩（正文只能用这里的数字，原样写，不自己算、不取整；亏损和盈利一样如实写，不回避）：'];
  const usd=value=>`${value>=0?'+':'-'}${Math.abs(value).toLocaleString('en-US',{maximumFractionDigits:2})}美元`;
  if(Number.isFinite(cum)) {
    const base=Number.isFinite(equity)?equity-cum:10000;
    const ret=base?cum/base*100:null;
    lines.push(`已了结 ${ledger.counts.closed} 笔，累计盈亏 ${usd(cum)}${ret===null?'':`，账户收益 ${ret>=0?'+':''}${ret.toFixed(2)}%`}（更新到 ${ledger.updated_to||'未知'}）。`);
  }
  const stats=ledger.closed_stats;
  if(stats) lines.push(`这${ledger.counts.closed}笔里赚了 ${stats.wins} 笔、亏了 ${stats.losses} 笔${stats.worst_loss_usd===null?'':`，最大一笔亏损 ${usd(stats.worst_loss_usd)}`}（这些亏损都在公开台账里，一张没删）。`);
  lines.push(`目前在手 ${ledger.counts.open} 张、挂单 ${ledger.counts.pending} 张。`);
  const recent=(ledger.closed_recent||[]).slice(0,3);
  if(recent.length) lines.push('最近了结（新到旧）：'+recent.map(item=>`$${item.symbol} 第${item.issue}期 币价${item.pct}，账户${item.usd}`).join('；'));
  return lines.join('\n');
}
