import test from 'node:test';
import assert from 'node:assert/strict';
import {buildPlan,appendLedgerOrder,planExpiry,describePlan,ruleText,orderRules,ruleKey,MONITOR_COINS} from '../cloud/plan.mjs';
import {TRADABLE} from '../cloud/order.mjs';

const BJ=(y,m,d,h,mi=0)=>Date.UTC(y,m-1,d,h,mi)-8*3600000;
const level=(mid,dist,segs=4)=>({mid,dist,segs});
function metric(price,extra={}) {
  return {price,ma50:price*0.97,ma200:price*0.9,dev200:11.1,rsi_d:57.3,atr_d_pct:4,atr_4_pct:1.5,fund_ann:12.3,'阶段':'宽幅震荡(过三道过滤才可空)',
    '慢档可用':true,
    d1_above:[level(price*1.05,5),level(price*1.1,10)],d1_below:[level(price*0.95,-5),level(price*0.9,-10)],
    h4_above:[level(price*1.02,2,3)],h4_below:[level(price*0.98,-2,3)],...extra};
}
const metrics=Object.fromEntries(MONITOR_COINS.map((sym,i)=>[sym,metric(100+i)]));
const SCREENS={SOL:{'候选':[{'进场':105}],'门①':'过'},BTC:{'候选':[],'门①':'未过：位置'}};
const snapshot=(extra={})=>({day:'2026-11-09',report:{ok:true,finished_at:new Date(BJ(2026,11,9,19,2)).toISOString()},
  summary:{'指标':metrics,'做空筛选':SCREENS},ledger:{ok:true,updated_to:'2026-11-08',active:[]},...extra});
const NOW=BJ(2026,11,9,19,5);
const kinds=(plan,sym)=>plan.rules.filter(r=>r.sym===sym).map(r=>r.kind);

test('监控名单就是程序可出单的 21 个币',()=>{
  assert.deepEqual(MONITOR_COINS,TRADABLE);
  assert.equal(MONITOR_COINS.length,21);
});

test('每个币：日线上下位各触及+日收、4h 上下位只收盘、MA200、急涨急跌，每币最多 9 条',()=>{
  const plan=buildPlan(snapshot(),null,NOW);
  assert.equal(Object.keys(plan.coins).length,21);
  const sol=plan.rules.filter(r=>r.sym==='SOL');
  // 压力位：触及 + 日收站上；支撑位：触及 + 日收跌破；4h 上下各一条收盘；价格在 MA200 上方 → 日收跌破；急涨、急跌
  assert.deepEqual(sol.map(r=>r.kind).sort(),['daydown','daydown','dayup','down','h4down','h4up','plunge','surge','up']);
  for(const sym of MONITOR_COINS) assert.equal(plan.rules.filter(r=>r.sym===sym).length,9,sym);
});

test('具体规则：压力位等涨到、支撑位等跌到；价位取离现价最近的那一个',()=>{
  const plan=buildPlan(snapshot(),null,NOW),price=metrics.SOL.price;
  const get=(kind,src)=>plan.rules.filter(r=>r.sym==='SOL'&&r.kind===kind&&(!src||r.src===src));
  assert.equal(get('up','level')[0].value,price*1.05);
  assert.equal(get('down','level')[0].value,price*0.95);
  assert.equal(get('dayup','level')[0].value,price*1.05);
  assert.equal(get('h4up')[0].value,price*1.02);
  assert.match(get('up','level')[0].tag,/日线压力位（4段接触，分析时距现价\+5\.00%）/);
  assert.equal(get('h4down').length,1);
});

test('价格在 MA200 上方就等日收跌破，下方就等日收站上',()=>{
  const above=buildPlan(snapshot(),null,NOW).rules.find(r=>r.sym==='SOL'&&r.src==='ma');
  assert.equal(above.kind,'daydown');
  assert.equal(above.value,metrics.SOL.ma200);
  const below=buildPlan({...snapshot(),summary:{'指标':{...metrics,SOL:metric(100,{ma200:120})}}},null,NOW).rules.find(r=>r.sym==='SOL'&&r.src==='ma');
  assert.equal(below.kind,'dayup');
});

test('和日线位几乎重合的 4h 位不重复；慢档不可用的币不出日线位和 MA200',()=>{
  const dup=metric(100,{h4_above:[level(105.2,5.2)]});
  const slowOff=metric(100,{'慢档可用':false});
  const plan=buildPlan({...snapshot(),summary:{'指标':{...metrics,SOL:dup,ETH:slowOff}}},null,NOW);
  assert.equal(plan.rules.filter(r=>r.sym==='SOL'&&r.kind==='h4up').length,0);
  const eth=plan.rules.filter(r=>r.sym==='ETH');
  assert.deepEqual(eth.map(r=>r.kind).sort(),['h4down','h4up','plunge','surge']);
});

test('异常波动阈值 = 0.8×4h ATR，限定 1%–8%；ATR 缺失按 2%',()=>{
  const plan=buildPlan({...snapshot(),summary:{'指标':{...metrics,SOL:metric(100,{atr_4_pct:0.5}),ETH:metric(100,{atr_4_pct:12}),XRP:metric(100,{atr_4_pct:null}),BNB:metric(100,{atr_4_pct:2.5})}}},null,NOW);
  const surge=sym=>plan.rules.find(r=>r.sym===sym&&r.kind==='surge').value;
  assert.equal(surge('SOL'),1);
  assert.equal(surge('ETH'),8);
  assert.equal(surge('XRP'),2);
  assert.equal(surge('BNB'),2);
});

test('台账挂单：进场价接近+触及，空单还盯 MA50 作废和目标先到',()=>{
  const ledger={ok:true,updated_to:'2026-11-08',active:[{issue:31,symbol:'SOL',side:'做空',status:'挂单中',entry:105,stop:107.1,target:96.6}]};
  const plan=buildPlan(snapshot({ledger}),null,NOW);
  const mine=plan.rules.filter(r=>r.sym==='SOL'&&r.src==='ledger');
  assert.deepEqual(mine.filter(r=>!r.requires).map(r=>r.kind+':'+r.value).sort(),['dayup:'+metrics.SOL.ma50,'down:96.6','near:105','up:105'].sort());
  // 成交后才监控的：止损触及 + 日收确认 + 2R/3R；前提是“进场价触及”提醒发出过
  const post=mine.filter(r=>r.requires);
  assert.deepEqual(post.map(r=>r.kind).sort(),['dayup','down','down','up']);
  assert.ok(post.every(r=>r.requires===ruleKey('SOL','up',105)));
  assert.equal(post.find(r=>r.kind==='up').value,107.1);
  assert.equal(post.find(r=>r.kind==='dayup').value,107.1);
  assert.deepEqual(post.filter(r=>r.kind==='down').map(r=>+r.value.toFixed(2)).sort((a,b)=>a-b),[98.7,100.8]);
  assert.match(post.find(r=>r.kind==='up').tag,/止损价（成交后/);
  assert.match(mine.find(r=>r.kind==='down'&&!r.requires).tag,/成交后=止盈到价；成交前先到则作废/);
  assert.ok(ruleText(post.find(r=>r.kind==='up')).endsWith('进场价触及后才监控'));
  const near=mine.find(r=>r.kind==='near');
  assert.equal(near.band,1);   // 日线 ATR 4% 的四分之一 = 1%
  assert.match(near.note,/^台账：第31期 \$SOL 做空挂单中｜进场 105｜止损 107\.1｜目标 96\.6$/);
  assert.match(mine.find(r=>r.kind==='dayup').tag,/作废条件/);
});

test('台账持仓：止损触及+日收确认（用上移后的止损）、目标、2R/3R',()=>{
  const ledger={ok:true,active:[{issue:30,symbol:'ETH',side:'做空',status:'持仓中',entry:110,stop:112.2,stop_now:109,target:101.2}]};
  const plan=buildPlan(snapshot({ledger,summary:{'指标':{...metrics,ETH:metric(108)},'做空筛选':SCREENS}}),null,NOW);
  const mine=plan.rules.filter(r=>r.sym==='ETH'&&r.src==='ledger');
  const byKind=kind=>mine.filter(r=>r.kind===kind);
  assert.equal(byKind('up')[0].value,109);              // 止损价在现价 108 上方 → 等涨到
  assert.equal(byKind('dayup')[0].value,109);
  assert.match(byKind('up')[0].note,/止损 112\.2（已上移到 109）/);
  assert.match(byKind('up')[0].tag,/按日线收盘判断/);
  assert.deepEqual(byKind('down').map(r=>r.tag.slice(0,3)).sort(),['2R价','3R价','目标价']);
  const risk=112.2-110;
  assert.equal(byKind('down').find(r=>r.tag.startsWith('2R')).value,110-2*risk);
  assert.equal(byKind('down').find(r=>r.tag.startsWith('3R')).value,110-3*risk);
});

test('持仓的 R 位只在还没到的时候才盯',()=>{
  const info={price:104,ma50:null,near_pct:0.5};
  const item={symbol:'ETH',side:'做空',status:'持仓中',entry:110,stop:112.2,target:101.2};
  const rules=orderRules(item,info);
  const r2=110-2*(112.2-110),r3=110-3*(112.2-110);   // 2R≈105.6（现价 104 已越过，不盯），3R≈103.4（还没到，盯）
  assert.equal(rules.some(r=>r.value===r2),false);
  assert.equal(rules.some(r=>r.value===r3),true);
});

test('当日新单：进场/MA50/目标，来源标 order；台账里同价位的规则优先',()=>{
  const orderPlan={allowed:true,order:{symbol:'XRP',entry:105.5,stop:107.6,target:97.1,cancel_ma50:99}};
  const ledger={ok:true,active:[{issue:31,symbol:'XRP',side:'做空',status:'挂单中',entry:105.5,stop:107.6,target:97.1}]};
  const onlyOrder=buildPlan(snapshot(),orderPlan,NOW);
  assert.ok(onlyOrder.rules.filter(r=>r.sym==='XRP'&&r.src==='order').length>=4);
  assert.match(onlyOrder.rules.find(r=>r.src==='order').note,/^当日新单（程序订单，是否发布以台账为准）：\$XRP 做空挂单中/);
  const both=buildPlan(snapshot({ledger}),orderPlan,NOW);
  assert.equal(both.rules.filter(r=>r.sym==='XRP'&&r.kind==='near').length,1);
  assert.equal(both.rules.find(r=>r.sym==='XRP'&&r.kind==='near').src,'ledger');
  assert.equal(new Set(both.rules.map(r=>r.key)).size,both.rules.length);   // key 不重复
  assert.equal(both.order_symbol,'XRP');
});

test('不出单的日子不生成新单规则；台账里不在名单的币也会进计划',()=>{
  const ledger={ok:true,active:[{issue:29,symbol:'PONS',side:'做空',status:'挂单中',entry:0.5,stop:0.55,target:0.4}]};
  const plan=buildPlan(snapshot({ledger}),{allowed:false,order:null},NOW);
  assert.equal(plan.order_symbol,null);
  assert.equal(plan.rules.some(r=>r.src==='order'),false);
  const pons=plan.rules.filter(r=>r.sym==='PONS');
  assert.ok(pons.length>=3);
  assert.ok(pons.every(r=>r.src==='ledger'));
  assert.equal(plan.coins.PONS.near_pct,0.5);
});

test('台账写入后补充新挂单；过期的计划不再改',()=>{
  const plan=buildPlan(snapshot(),{allowed:true,order:{symbol:'SOL',entry:105,stop:107.1,target:96.6,cancel_ma50:99}},NOW);
  const added=appendLedgerOrder(plan,{symbol:'SOL',entry:105,stop:107.1,target:96.6},31,NOW+3600000);
  const near=added.rules.find(r=>r.sym==='SOL'&&r.kind==='near');
  assert.equal(near.src,'ledger');
  assert.equal(near.created,NOW+3600000);
  assert.match(near.note,/第31期/);
  assert.equal(added.rules.filter(r=>r.sym==='SOL'&&r.kind==='near').length,1);
  assert.equal(added.built_at,plan.built_at);
  assert.equal(appendLedgerOrder(plan,{symbol:'SOL',entry:1,stop:2,target:0.5},32,plan.expires_at+1),plan);
  assert.equal(appendLedgerOrder(null,{symbol:'SOL'},1,NOW),null);
});

test('有效期：到下一个工作日 19:00 取数后 1 小时，最长 72 小时',()=>{
  assert.equal(planExpiry(BJ(2026,11,9,19,5)),BJ(2026,11,10,20,0));           // 周一晚 → 周二 20:00
  assert.equal(planExpiry(BJ(2026,11,9,12,0)),BJ(2026,11,9,20,0));            // 周一中午重建 → 当晚 20:00
  assert.equal(planExpiry(BJ(2026,11,6,19,30)),BJ(2026,11,6,19,30)+72*3600000); // 周五晚 → 周一 19:30（封顶 72 小时）
  assert.ok(planExpiry(BJ(2026,11,6,19,30))<BJ(2026,11,9,20,0));
  assert.equal(planExpiry(BJ(2026,11,10,19,20)),BJ(2026,11,12,20,0));         // 周二晚 → 周三是美国联邦假日不取数 → 周四 20:00
});

test('计划体积：21 币全带台账单也远小于 Durable Object 单值上限',()=>{
  const active=MONITOR_COINS.map((sym,i)=>({issue:i,symbol:sym,side:'做空',status:i%2?'挂单中':'持仓中',entry:100+i,stop:103+i,target:90+i}));
  const plan=buildPlan(snapshot({ledger:{ok:true,active}}),null,NOW);
  assert.ok(JSON.stringify(plan).length<100000,String(JSON.stringify(plan).length));
});

test('计划里没有任何买卖建议措辞，上下文只有数据',()=>{
  const ledger={ok:true,active:[{issue:31,symbol:'SOL',side:'做空',status:'挂单中',entry:105,stop:107.1,target:96.6},
    {issue:30,symbol:'ETH',side:'做空',status:'持仓中',entry:110,stop:112.2,stop_now:109,target:101.2}]};
  const plan=buildPlan(snapshot({ledger}),{allowed:true,order:{symbol:'BTC',entry:105,stop:107,target:97,cancel_ma50:99}},NOW);
  const text=JSON.stringify(plan);
  for(const word of ['建议','应该','应当','值得','买入','卖出','抄底','追高','加仓','减仓','入场','可以买','可以卖'])
    assert.equal(text.includes(word),false,word);
});

test('分析上下文：阶段、距 MA200、RSI、费率、做空筛选结论',()=>{
  const plan=buildPlan(snapshot(),null,NOW);
  assert.equal(plan.coins.SOL.analysis,'阶段=宽幅震荡(过三道过滤才可空)；距MA200 +11.10%；日线RSI 57.3；资金费率年化 +12.30%；做空筛选：1个候选压力位');
  assert.match(plan.coins.BTC.analysis,/做空筛选：门①未过$/);
  assert.equal(plan.coins.ETH.analysis.includes('做空筛选'),false);   // 没有这个币的筛选记录就不写，不编造
  const empty=buildPlan(snapshot({summary:{'指标':metrics,'做空筛选':{XRP:{}}}}),null,NOW);
  assert.match(empty.coins.XRP.analysis,/做空筛选：无候选$/);
});

test('/plan 文字：总览和单币',()=>{
  const ledger={ok:true,active:[{issue:31,symbol:'SOL',side:'做空',status:'挂单中',entry:105,stop:107.1,target:96.6}]};
  const plan=buildPlan(snapshot({ledger}),null,NOW);
  const overview=describePlan(plan,'',NOW+3600000);
  assert.match(overview,/今日监控计划：11-09 19:05 生成（北京时间），有效到 11-10 20:00；\d+ 条规则，21 个币/);
  assert.match(overview,/SOL \d+条（含台账\/新单 8）/);
  const sol=describePlan(plan,'sol',NOW);
  assert.match(sol,/SOL 共 \d+ 条/);
  assert.match(sol,/价格接近 105（±1%）｜台账挂单进场价/);
  assert.match(sol,/分析：阶段=/);
  assert.match(describePlan(plan,'ZZZ',NOW),/ZZZ 不在计划里/);
  assert.match(describePlan(plan,'',plan.expires_at+1),/已过期/);
  assert.match(describePlan(null),/还没有监控计划/);
});

test('规则文字',()=>{
  assert.equal(ruleText({kind:'surge',value:2.4,tag:'x'}),'15分钟涨幅达到 2.4%｜x');
  assert.equal(ruleKey('SOL','up',148.2),'SOL|up|148.200');
});
