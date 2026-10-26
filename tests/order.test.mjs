import test from 'node:test';
import assert from 'node:assert/strict';
import {buildOrderPlan,profitLocked,orderBlock,checkAgainstPlan,expiryText,fmtPrice} from '../cloud/order.mjs';
import {lintDraft} from '../cloud/draft-style.mjs';

const discount=(extra={})=>({'数据完整':true,'净流动性条件':false,'实际利率条件':false,'边缘提示':[],'新开仓倍数':1.0,'不加仓':false,...extra});
const candidate=(extra={})=>({'进场':82314.42,'距现价%':0.99,'止损':83960.71,'目标':75729.27,'R':1646.29,'方向倍数':0.5,'仓位倍数':1.0,'实际倍数':0.5,'15天内碰过':false,...extra});
const snapshot=({screen={},disc=discount(),price=81510.4}={})=>({summary:{
  '指标':{BTC:{price}},
  'L1L4':{'L1折扣判定':disc},
  '做空筛选':{BTC:{'标的':'BTC','现价':price,'门①':'过：不在主升浪区间','门②':'过：不是反模式','门③需要人工核实':false,'费率否决':false,'门③路径':'路A：趋势已破（判断级，小仓位）','候选':[candidate()],...screen}}
}});

test('程序算出新单：盈亏比、仓位、有效期都由代码给出',()=>{
  const plan=buildOrderPlan(snapshot(),'2026-10-20');
  assert.equal(plan.allowed,true);
  const o=plan.order;
  assert.equal(o.ratio_text,'1:4');
  assert.equal(o.size_text,'0.50仓');
  assert.equal(o.expires,'11月4日');
  assert.equal(fmtPrice(o.entry),'82,314.42');
  assert.equal(plan.human_checks.some(item=>item.includes('台账')),true);
  const block=orderBlock(plan);
  assert.equal(block.includes('进场限价 82,314.42｜距现价 +0.99%｜止损 83,960.71'),true);
  assert.equal(block.includes('盈亏比 1:4｜仓位 0.50仓｜有效期到 11月4日'),true);
});

test('刚碰过的价位和宏观折扣都按程序打折，不叠加两次折扣',()=>{
  const plan=buildOrderPlan(snapshot({screen:{'候选':[candidate({'仓位倍数':0.3333,'实际倍数':0.1667,'15天内碰过':true})]},disc:discount({'净流动性条件':true,'实际利率条件':true,'新开仓倍数':0.7})}),'2026-10-20');
  assert.equal(plan.order.size_text,'0.12仓');
  assert.equal(plan.notes.some(item=>item.includes('15天内被碰过')),true);
  assert.equal(plan.notes.some(item=>item.includes('宏观层折扣')),true);
});

test('门①未过、费率否决、路B都不出单，并说明原因',()=>{
  const blocked=buildOrderPlan(snapshot({screen:{'门①':'未过：主升浪，硬性禁止做空','候选':[]}}),'2026-10-20');
  assert.equal(blocked.allowed,false);
  assert.equal(blocked.reasons[0].includes('主升浪'),true);
  assert.equal(orderBlock(blocked).includes('今天不开新单'),true);
  const funding=buildOrderPlan(snapshot({screen:{'费率否决':true,'门③路径':'费率30日均为负→不开新空'}}),'2026-10-20');
  assert.equal(funding.allowed,false);
  const pathB=buildOrderPlan(snapshot({screen:{'门③需要人工核实':'趋势未破，必须配具体基本面利空'}}),'2026-10-20');
  assert.equal(pathB.allowed,false);
  assert.equal(pathB.reasons[0].includes('基本面'),true);
});

test('宏观读数不全、缺做空筛选、缺快照都不出单',()=>{
  assert.equal(buildOrderPlan(snapshot({disc:discount({'数据完整':false})}),'2026-10-20').allowed,false);
  assert.equal(buildOrderPlan({summary:{'指标':{}}},'2026-10-20').allowed,false);
  assert.equal(buildOrderPlan(null,'2026-10-20').allowed,false);
});

test('名单内的非五大币（UNI、DOGE等）和主流币一样出单；五大币优先；名单外的币仍不出单',()=>{
  for(const symbol of ['UNI','CAKE','HYPE','ENA','TAO','RAY','DOGE','LDO','ARB','NEAR','ZEC','JUP','AAVE','WLD','SUI','INJ']) {
    const alt=snapshot();
    alt.summary['做空筛选'][symbol]={...alt.summary['做空筛选'].BTC,'标的':symbol};
    delete alt.summary['做空筛选'].BTC;
    const plan=buildOrderPlan(alt,'2026-10-20');
    assert.equal(plan.allowed,true,symbol);
    assert.equal(plan.order.symbol,symbol);
  }
  const both=snapshot();
  both.summary['做空筛选'].UNI={...both.summary['做空筛选'].BTC,'标的':'UNI'};
  assert.equal(buildOrderPlan(both,'2026-10-20').order.symbol,'BTC','五大币排在前面');
  const out=snapshot();
  out.summary['做空筛选'].PEPE={...out.summary['做空筛选'].BTC,'标的':'PEPE'};
  delete out.summary['做空筛选'].BTC;
  const plan=buildOrderPlan(out,'2026-10-20');
  assert.equal(plan.allowed,false);
  assert.equal(plan.human_checks.some(item=>item.includes('PEPE')),true);
});

test('名单外的币不出单；价格来源冲突写进备注',()=>{
  const alt=snapshot();
  alt.summary['做空筛选'].ZRO={...alt.summary['做空筛选'].BTC,'标的':'ZRO'};
  delete alt.summary['做空筛选'].BTC;
  const plan=buildOrderPlan(alt,'2026-10-20');
  assert.equal(plan.allowed,false);
  assert.equal(plan.human_checks.some(item=>item.includes('ZRO')),true);
  const conflict=buildOrderPlan(snapshot({screen:{'现价':81480.1}}),'2026-10-20');
  assert.equal(conflict.notes.some(item=>item.includes('不一致')),true);
});

test('正文与程序不一致会被自检拦下',()=>{
  const plan=buildOrderPlan(snapshot(),'2026-10-20');
  const good='审核正文：进场限价82,314.42，距现价+0.99%，止损83,960.71，目标75,729.27，盈亏比1:4，仓位0.50仓，有效期到11月4日。成交前日线收盘站上50日均线就作废。\n\n送审备注：可发。';
  assert.deepEqual(checkAgainstPlan(good,plan),[]);
  assert.equal(lintDraft(good,plan).some(item=>item.includes('程序计算')),false);
  const bad=good.replace('0.50仓','0.17仓').replace('83,960.71','83,900');
  assert.equal(lintDraft(bad,plan).some(item=>item.includes('仓位')&&item.includes('止损')),true);
  const none=buildOrderPlan(snapshot({screen:{'候选':[]}}),'2026-10-20');
  assert.equal(lintDraft(good,none).some(item=>item.includes('不开新单')),true);
  assert.deepEqual(checkAgainstPlan('审核正文：今天不开新单。\n\n送审备注：可发。',none),[]);
});

test('有效期跨月',()=>{ assert.equal(expiryText('2026-10-20'),'11月4日'); assert.equal(expiryText('2026-12-25'),'1月9日'); });

const ledger=(active,extra={})=>({ok:true,updated_to:'2026-10-20',active,...extra});
const eth={symbol:'ETH',side:'做空',status:'挂单中',size:0.12};
test('台账读到后：不再要求人工核挂单数，并把台账事实写进备注',()=>{
  const snap=snapshot();snap.ledger=ledger([eth]);
  const plan=buildOrderPlan(snap,'2026-10-20');
  assert.equal(plan.allowed,true);
  assert.equal(plan.human_checks.some(item=>item.includes('台账没有读到')),false);
  assert.equal(plan.human_checks.some(item=>item.includes('熔断')),true);
  assert.equal(plan.notes.some(item=>item.includes('挂单1/5')&&item.includes('主流币空单1/3')),true);
});
test('台账：挂单满5张、同向主流币满3张、同币已有空单都不出新单',()=>{
  const full=snapshot();full.ledger=ledger(Array.from({length:5},(_,i)=>({symbol:'X'+i,side:'做多',status:'挂单中',size:0.33})));
  const a=buildOrderPlan(full,'2026-10-20');
  assert.equal(a.allowed,false);assert.equal(a.reasons[0].includes('5张上限'),true);
  const three=snapshot();three.ledger=ledger(['ETH','SOL','XRP'].map(symbol=>({symbol,side:'做空',status:'持仓中',size:0.12})));
  assert.equal(buildOrderPlan(three,'2026-10-20').allowed,false);
  const dup=snapshot();dup.ledger=ledger([{symbol:'BTC',side:'做空',status:'挂单中',size:0.12}]);
  const d=buildOrderPlan(dup,'2026-10-20');
  assert.equal(d.allowed,false);assert.equal(d.reasons.some(item=>item.includes('已经有一张在场的空单')),true);
});
const held=(symbol,stop_now,extra={})=>({symbol,side:'做空',status:'持仓中',size:0.12,entry:100,stop:104,stop_now,...extra});
const withPrices=(prices)=>{const snap=snapshot();Object.assign(snap.summary['指标'],prices);return snap;};
test('同向已有3张，但持仓空单止损已上移到进场价以下、价格仍在止损下方（利润已锁定）→ 不占名额，可以继续开',()=>{
  const snap=withPrices({ETH:{price:90},SOL:{price:90},XRP:{price:90}});
  snap.ledger=ledger([held('ETH',95),held('SOL',98),held('XRP',99)]);
  const plan=buildOrderPlan(snap,'2026-10-26');
  assert.equal(plan.allowed,true);
  assert.equal(plan.notes.some(item=>item.includes('主流币空单0/3')&&item.includes('3张利润已锁定')),true);
});
test('锁定的不算、没锁定的照算：3张里2张锁定→占1个名额，仍可开；3张都没锁定→照旧拒绝',()=>{
  const snap=withPrices({ETH:{price:90},SOL:{price:90},XRP:{price:90}});
  snap.ledger=ledger([held('ETH',95),held('SOL',98),held('XRP',null)]);
  const plan=buildOrderPlan(snap,'2026-10-26');
  assert.equal(plan.allowed,true);
  assert.equal(plan.notes.some(item=>item.includes('主流币空单1/3')),true);
  const none=withPrices({ETH:{price:90},SOL:{price:90},XRP:{price:90}});
  none.ledger=ledger([held('ETH',null),held('SOL',null),held('XRP',null)]);
  const blocked=buildOrderPlan(none,'2026-10-26');
  assert.equal(blocked.allowed,false);
  assert.equal(blocked.reasons[0].includes('止损上移'),true);
});
test('不算"利润已锁定"的情况：止损只到进场价（保本不是盈利）、价格已回到止损上方、读不到价格、挂单中、多单',()=>{
  assert.equal(profitLocked(held('ETH',95),90),true);
  assert.equal(profitLocked(held('ETH',100),90),false);   // stop == entry: breakeven, a gap close can still lose
  assert.equal(profitLocked(held('ETH',95),96),false);    // price already above the moved stop
  assert.equal(profitLocked(held('ETH',95),null),false);  // no price: not locked
  assert.equal(profitLocked(held('ETH',95,{status:'挂单中'}),90),false);
  assert.equal(profitLocked(held('ETH',95,{side:'做多'}),90),false);
  assert.equal(profitLocked(held('ETH',undefined),90),false);
});
test('锁定不放宽其它限制：挂单5张、名义2万、同币已有空单照旧',()=>{
  const snap=withPrices({ETH:{price:90},SOL:{price:90},XRP:{price:90}});
  snap.ledger=ledger([held('ETH',95),held('SOL',98),held('XRP',99),...Array.from({length:5},(_,i)=>({symbol:'X'+i,side:'做空',status:'挂单中',size:0.1}))]);
  assert.equal(buildOrderPlan(snap,'2026-10-26').reasons[0].includes('5张上限'),true);
  const big=withPrices({ETH:{price:90}});
  big.ledger=ledger([held('ETH',95,{size:9.9})]);
  assert.equal(buildOrderPlan(big,'2026-10-26').reasons[0].includes('2万')||buildOrderPlan(big,'2026-10-26').reasons[0].includes('20000'),true);
  const dup=withPrices({BTC:{price:90}});
  dup.ledger=ledger([held('BTC',95)]);
  assert.equal(buildOrderPlan(dup,'2026-10-26').allowed,false);
});
test('台账更新日期太旧会提示先更新；台账读取失败时回到人工核对',()=>{
  const old=snapshot();old.ledger=ledger([eth],{updated_to:'2026-10-16'});
  const plan=buildOrderPlan(old,'2026-10-20');
  assert.equal(plan.human_checks[0].includes('已过去4天'),true);
  const bad=snapshot();bad.ledger={ok:false,error:'没有设置 NOTION_TOKEN'};
  const b=buildOrderPlan(bad,'2026-10-20');
  assert.equal(b.human_checks.some(item=>item.includes('台账没有读到')),true);
  assert.equal(b.notes.some(item=>item.includes('NOTION_TOKEN')),true);
});
test('台账核对出应更新的项目，会写进待人工事项',()=>{
  const snap=snapshot();
  snap.ledger=ledger([eth],{proposals:[{type:'fill',text:'第35期 ETH空单：10-19盘中触及，应记为成交'},{type:'review',text:'止损上移请人工看'}]});
  const plan=buildOrderPlan(snap,'2026-10-20');
  assert.equal(plan.human_checks[0].includes('台账有1项应更新'),true);
  assert.equal(plan.human_checks[0].includes('止损上移请人工看'),false);
});

test('程序给出作废与撤单条件和2R/3R价位，与台账第35期同口径',()=>{
  const snap=snapshot({screen:{'候选':[{...candidate(),'进场':2585.57,'止损':2637.28,'目标':2378.72}]}});
  snap.summary['指标'].BTC.ma50=2553.1;
  const block=orderBlock(buildOrderPlan(snap,'2026-10-20'));
  assert.equal(block.includes('50日均线（目前约 2,553.10）'),true);
  assert.equal(block.includes('2,482.15'),true);
  assert.equal(block.includes('2,430.44'),true);
});

test('风险提示：支撑落在进场与目标之间、目标超过3倍ATR、贴近MA200，只写备注，不改订单',()=>{
  const snap=snapshot({price:100});
  snap.summary['指标'].BTC={price:100,atr_d_pct:2.5,dev200:4.0,
    d1_below:[{mid:96,dist:-4,segs:4},{mid:60,dist:-40,segs:5},{mid:97,dist:-3,segs:2}]};
  snap.summary['做空筛选'].BTC['候选']=[candidate({'进场':102,'止损':104.04,'目标':93.84,'距现价%':2})];
  const plan=buildOrderPlan(snap,'2026-10-20');
  assert.equal(plan.allowed,true);
  assert.equal(plan.order.target,93.84);
  const risk=plan.notes.filter(item=>item.startsWith('风险提示'));
  assert.equal(risk.length,3);
  assert.match(risk[0],/下方 96.*之间/);
  assert.match(risk.join('\n'),/日线ATR14（2\.50%）的3\.2倍/);
  assert.match(risk.join('\n'),/MA200上方 4\.0%/);
  assert.equal(risk.join('').includes('60'),false,'目标以下的支撑不提示');
  assert.equal(orderBlock(plan).includes('风险提示'),true);
});
test('没有支撑/ATR/MA200数据时不加风险提示',()=>{
  const plan=buildOrderPlan(snapshot(),'2026-10-20');
  assert.equal(plan.notes.some(item=>item.startsWith('风险提示')),false);
});

test('备注里写明行情快照的采集时间，超过4小时提示先核对实时价',()=>{
  const snap=snapshot();
  snap.report={finished_at:'2026-10-21T02:00:00Z'};
  const fresh=buildOrderPlan(snap,'2026-10-21',Date.parse('2026-10-21T03:30:00Z'));
  assert.match(fresh.notes[0],/北京时间 10-21 10:00（距现在约1\.5小时）$/);
  const stale=buildOrderPlan(snap,'2026-10-21',Date.parse('2026-10-21T10:00:00Z'));
  assert.match(stale.notes[0],/约8\.0小时）：价格已不新.*立刻成交/);
  assert.equal(buildOrderPlan(snapshot(),'2026-10-21').notes.some(item=>item.includes('行情快照采集')),false);
});

test('战绩块：只给程序算好的数字，亏损照写；台账没读到就明说不写',async()=>{
  const {recordBlock}=await import('../cloud/order.mjs');
  const block=recordBlock({ok:true,updated_to:'2026-10-24',equity_usd:10683.72,cumulative_pnl_usd:683.72,
    counts:{closed:17,open:2,pending:1},closed_recent:[{issue:'35',symbol:'SOL',pct:'-2.1%',usd:'-45.6 USD'}]});
  assert.ok(block.includes('已了结 17 笔')&&block.includes('+683.72美元')&&block.includes('账户收益 +6.84%'));
  assert.ok(block.includes('在手 2 张')&&block.includes('$SOL 第35期 币价-2.1%'));
  assert.ok(recordBlock(null).includes('不写战绩'));
  assert.ok(recordBlock({ok:true,counts:{closed:3,open:0,pending:0},cumulative_pnl_usd:-120,equity_usd:9880}).includes('-120美元'));
});
