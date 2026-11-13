import test from 'node:test';
import assert from 'node:assert/strict';
import {buildCards,macroView,cardKey,PAPER} from '../cloud/paper.mjs';
import {buildPlan,ruleKey} from '../cloud/plan.mjs';
import {MONITOR_COINS} from '../cloud/plan.mjs';

const level=(mid,dist,segs=4,extra={})=>({mid,dist,segs,days:60,age:120,gap:40,...extra});
function metric(price,extra={}) {
  return {price,ma20:price*0.99,ma50:price*0.97,ma200:price*0.9,dev200:11.1,rsi_d:55,atr_d_pct:4,atr_4_pct:1.5,fund_ann:10,
    '阶段':'宽幅震荡(过三道过滤才可空)','慢档可用':true,
    d1_above:[level(price*1.05,5)],d1_below:[level(price*0.95,-5)],h4_above:[],h4_below:[],...extra};
}
const L1L4=(extra={})=>({'L1折扣判定':{'数据完整':true,'新开仓倍数':1},'四行':{L2:'L2：熊末→牛初（底部已确认）｜第 43 天'},...extra});
const snap=(metrics,screens={},l1l4=L1L4())=>({day:'2026-11-09',summary:{'指标':metrics,'做空筛选':screens,'L1L4':l1l4}});
const only=(sym,m)=>({[sym]:m});

test('做多卡：顺势回踩，止损 0.5×ATR（下限2%）、目标4R、仓位按倍数',()=>{
  const m=metric(100);
  const {cards}=buildCards(snap(only('SOL',m)));
  const card=cards[cardKey('SOL','down',95)];
  assert.equal(card.side,'多');
  assert.equal(card.qualified,true,card.blocks.join());
  assert.equal(card.entry,95);
  assert.equal(card.stop,93.1);        // ATR 4% → 0.5×4=2% → 95×0.98
  assert.equal(card.target,102.6);     // 4R = +8%
  assert.equal(card.r2,98.8);
  assert.equal(card.r3,100.7);
  assert.equal(card.stop_pct,2);
  assert.equal(card.size,1);
  assert.equal(card.notional_usd,2000);
  assert.equal(card.margin_usd,1000);
  assert.equal(card.risk_usd,40);
  assert.equal(card.risk_pct,0.4);
  assert.ok(card.unchecked.length>=1);
});

test('做多卡：ATR 很小时止损用 2% 下限',()=>{
  const card=buildCards(snap(only('SOL',metric(100,{atr_d_pct:1.5})))).cards[cardKey('SOL','down',95)];
  assert.equal(card.stop_pct,2);
  const wide=buildCards(snap(only('SOL',metric(100,{atr_d_pct:8})))).cards[cardKey('SOL','down',95)];
  assert.equal(wide.stop_pct,4);
  assert.equal(wide.risk_usd,80);
});

test('做多卡：MA200 下方不抄底；支撑位在 MA200 下方也不算',()=>{
  const below=buildCards(snap(only('SOL',metric(100,{ma200:110,dev200:-9.1})))).cards[cardKey('SOL','down',95)];
  assert.equal(below.qualified,false);
  assert.match(below.blocks.join(),/MA200下方.*不抄底/);
  const lowLevel=buildCards(snap(only('SOL',metric(100,{ma200:97,dev200:3}))) ).cards[cardKey('SOL','down',95)];
  assert.equal(lowLevel.qualified,false);
  assert.match(lowLevel.blocks.join(),/支撑位.*在MA200.*下方/);
});

test('做多卡：距现价超 6%、L1 读数不全、L2 顶部风险区都不合格',()=>{
  const far=buildCards(snap(only('SOL',metric(100,{d1_below:[level(92,-8)]})))).cards[cardKey('SOL','down',92)];
  assert.match(far.blocks.join(),/超过挂单上限 6%/);
  const incomplete=buildCards(snap(only('SOL',metric(100)),{},L1L4({'L1折扣判定':{'数据完整':false}}))).cards[cardKey('SOL','down',95)];
  assert.match(incomplete.blocks.join(),/L1宏观读数不全/);
  const top=buildCards(snap(only('SOL',metric(100)),{},L1L4({'四行':{L2:'L2：牛市·顶部风险区｜第 10 天'}}))).cards[cardKey('SOL','down',95)];
  assert.equal(top.qualified,false);
  assert.match(top.blocks.join(),/多头只减不加/);
});

test('做多卡：碰过打 1/3、L1 折扣 ×0.7、L2 熊市 ×0.5，倍数相乘',()=>{
  const m=metric(100,{d1_below:[level(95,-5,4,{gap:10})]});
  const l1l4=L1L4({'L1折扣判定':{'数据完整':true,'新开仓倍数':0.7},'四行':{L2:'L2：熊市｜第 5 天'}});
  const card=buildCards(snap(only('SOL',m),{},l1l4)).cards[cardKey('SOL','down',95)];
  assert.equal(card.qualified,true);
  assert.equal(card.size,0.1167);   // 1/3 × 0.7 × 0.5
  assert.equal(card.notional_usd,233);
  assert.match(card.size_parts,/L2熊市×0.5/);
});

test('做空卡：做空筛选有候选且三道门过 → 合格，仓位用筛选给的方向倍数',()=>{
  const m=metric(100);
  const screens={SOL:{'候选':[{'进场':105,'止损':107.1,'目标':96.6,'方向倍数':0.5,'仓位倍数':1,'15天内碰过':false}],'门①':'过','门③路径':'MA200下方'}};
  const card=buildCards(snap(only('SOL',m),screens)).cards[cardKey('SOL','up',105)];
  assert.equal(card.side,'空');
  assert.equal(card.qualified,true,card.blocks.join());
  assert.equal(card.stop,107.1);
  assert.equal(card.target,96.6);
  assert.equal(card.size,0.5);
  assert.equal(card.notional_usd,1000);
  assert.equal(card.risk_usd,20);
});

test('做空卡：主升浪 / 路B 需要人工 / 费率否决 / 没有候选 → 不合格并写明原因',()=>{
  const m=metric(100);
  const cand=[{'进场':105,'方向倍数':0.5}];
  const run=screen=>buildCards(snap(only('SOL',m),{SOL:screen})).cards[cardKey('SOL','up',105)];
  assert.match(run({'候选':[],'门①':'未过：主升浪，硬性禁止做空'}).blocks.join(),/主升浪/);
  assert.match(run({'候选':cand,'门③需要人工核实':'价格在MA200上方，趋势未破，必须配具体基本面利空'}).blocks.join(),/基本面利空/);
  assert.match(run({'候选':cand,'费率否决':true,'门③路径':'费率30日均为负→不开新空'}).blocks.join(),/费率30日均为负/);
  const noScreen=buildCards(snap(only('SOL',m))).cards[cardKey('SOL','up',105)];
  assert.equal(noScreen.qualified,false);
});

test('L2 状态从四行文字里读；读不到就是未知，不当成通过也不拦',()=>{
  assert.equal(macroView({'L1L4':L1L4({'四行':{L2:'L2：牛市·顶部风险区｜第 3 天'}})}).l2_top_risk,true);
  assert.equal(macroView({'L1L4':L1L4({'四行':{L2:'L2：熊市｜第 3 天'}})}).l2_bear,true);
  assert.equal(macroView({'L1L4':L1L4({'四行':{L2:'L2：无数据'}})}).l2_known,true);
  assert.equal(macroView({}).l2_known,false);
});

test('卡片 key 和监控计划里日线位的触及规则 key 一一对应',()=>{
  const metrics=Object.fromEntries(MONITOR_COINS.map((sym,i)=>[sym,metric(100+i)]));
  const snapshot={...snap(metrics),report:{ok:true,finished_at:new Date().toISOString()},ledger:{ok:true,active:[]}};
  const plan=buildPlan(snapshot,null,Date.now());
  assert.equal(Object.keys(plan.cards).length,42);   // 21 个币 × 上下各一张
  assert.deepEqual(plan.paper,PAPER);
  for(const rule of plan.rules.filter(r=>r.src==='level' && (r.kind==='up'||r.kind==='down') && r.tag.startsWith('日线'))) {
    const card=plan.cards[rule.key];
    assert.ok(card,rule.key);
    assert.equal(card.side,rule.kind==='down'?'多':'空');
    assert.equal(card.entry,rule.value);
  }
  assert.equal(ruleKey('SOL','down',95),cardKey('SOL','down',95));
});

test('慢档不可用的币不出卡',()=>{
  const {cards}=buildCards(snap(only('SOL',metric(100,{'慢档可用':false}))));
  assert.deepEqual(Object.keys(cards),[]);
});

test('涨腿数据和 ATR 随卡片/计划带给主机，主机据此核对闸门',()=>{
  const snapshot={...snap(only('SOL',metric(100))),poster_facts:{ok:true,leg:{day:131,start:'2026-06-30',dd:6.9,peak_pct:47.8}}};
  const {cards,macro}=buildCards(snapshot);
  assert.deepEqual(macro.leg,{ok:true,dir:'up',start:'2026-06-30',day:131,asof:null,dd:6.9,peak_pct:47.8,drop_pct:null});
  // 跌腿（网站 direction=down）：没有腿内回撤，带上跌幅交给主机；主机只标“89天线不亮”
  const down={...snap(only('SOL',metric(100))),poster_facts:{ok:true,leg:{dir:'down',day:51,start:'2026-09-21',asof:'2026-11-11',drop_pct:14.8}}};
  assert.deepEqual(buildCards(down).macro.leg,{ok:true,dir:'down',start:'2026-09-21',day:51,asof:'2026-11-11',dd:null,peak_pct:null,drop_pct:14.8});
  assert.equal(cards[cardKey('SOL','down',95)].atr_d_pct,4);
  assert.deepEqual(buildCards(snap(only('SOL',metric(100)))).macro.leg,{ok:false});
  assert.deepEqual(buildCards({...snap(only('SOL',metric(100))),poster_facts:{ok:false,error:'x'}}).macro.leg,{ok:false});
});

test('做空闸门：L2=牛市回撤·待确认 且 BTC 在跌腿 → 不开新空；涨腿或别的 L2 状态不受影响',()=>{
  const m=metric(100,{'阶段':'MA200下方',ma200:110,dev200:-9});
  const screens={SOL:{'候选':[{'进场':105,'止损':107.1,'目标':96.6,'方向倍数':0.5,'仓位倍数':1,'15天内碰过':false}],'门①':'过','门③路径':'MA200下方'}};
  const l1l4=L1L4({'四行':{L2:'L2：牛市回撤·待确认｜第 12 天'}});
  const down={...snap(only('SOL',m),screens,l1l4),poster_facts:{ok:true,leg:{dir:'down',day:20,start:'2026-10-20',asof:'2026-11-09',drop_pct:12}}};
  const blocked=buildCards(down).cards[cardKey('SOL','up',105)];
  assert.equal(blocked.qualified,false);
  assert.match(blocked.blocks.join(),/牛市回撤·待确认，且BTC在跌腿/);
  const up={...snap(only('SOL',m),screens,l1l4),poster_facts:{ok:true,leg:{day:40,start:'2026-09-30',dd:3,peak_pct:20}}};
  assert.equal(buildCards(up).cards[cardKey('SOL','up',105)].qualified,true);
  const other={...down,summary:{...down.summary,'L1L4':L1L4()}};
  assert.equal(buildCards(other).cards[cardKey('SOL','up',105)].qualified,true);
});
