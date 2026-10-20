import test from 'node:test';
import assert from 'node:assert/strict';
import {buildOrderPlan,orderBlock,checkAgainstPlan,expiryText,fmtPrice} from '../cloud/order.mjs';
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

test('非主流币不出单；价格来源冲突写进备注',()=>{
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
  const good='审核正文：进场限价82,314.42，距现价+0.99%，止损83,960.71，目标75,729.27，盈亏比1:4，仓位0.50仓，有效期到11月4日。\n\n送审备注：可发。';
  assert.deepEqual(checkAgainstPlan(good,plan),[]);
  assert.equal(lintDraft(good,plan).some(item=>item.includes('程序计算')),false);
  const bad=good.replace('0.50仓','0.17仓').replace('83,960.71','83,900');
  assert.equal(lintDraft(bad,plan).some(item=>item.includes('仓位')&&item.includes('止损')),true);
  const none=buildOrderPlan(snapshot({screen:{'候选':[]}}),'2026-10-20');
  assert.equal(lintDraft(good,none).some(item=>item.includes('不开新单')),true);
  assert.deepEqual(checkAgainstPlan('审核正文：今天不开新单。\n\n送审备注：可发。',none),[]);
});

test('有效期跨月',()=>{ assert.equal(expiryText('2026-10-20'),'11月4日'); assert.equal(expiryText('2026-12-25'),'1月9日'); });
