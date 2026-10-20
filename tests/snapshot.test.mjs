import test from 'node:test';
import assert from 'node:assert/strict';
import {compactSnapshot,shrink} from '../cloud/snapshot.mjs';

function bigSnapshot(coins=40,levels=300) {
  const metrics={};
  for(const name of ['BTC','ETH','SOL','XRP','BNB',...Array.from({length:coins-5},(_,i)=>'C'+i)])
    metrics[name]={price:123.456789012,funding:0.000123456789,
      levels:Array.from({length:levels},(_,i)=>({price:i*1.123456789,touches:i,note:'历史接触段 '+i}))};
  return {report:{ok:true,finished_at:'2026-10-20T11:30:00+00:00',fresh_symbols:['BTC'],excluded_symbols:{},junk:'x'.repeat(5000)},
    summary:{'指标':metrics,'L1L4':{summary:'L1偏多；L2中性'},'成交额排行':Array.from({length:200},(_,i)=>({sym:'S'+i,vol:i*1e6}))},
    four_lines:'L1：偏多\nL2：中性\nL3：筹码集中\nL4：费率正常'};
}
test('超大快照被压缩到上限内，保留四行、校验报告和五大币',()=>{
  const snapshot=bigSnapshot();
  const full=JSON.stringify(snapshot).length;
  assert.equal(full>300000,true);
  const out=compactSnapshot(snapshot);
  assert.equal(out.chars<=50000,true);
  assert.equal(out.trimmed,true);
  assert.equal(out.original,full);
  const parsed=JSON.parse(out.body);
  assert.equal(parsed.four_lines.includes('L4：费率正常'),true);
  assert.equal(parsed.report.finished_at,'2026-10-20T11:30:00+00:00');
  assert.equal('junk' in parsed.report,false);
  for(const coin of ['BTC','ETH','SOL','XRP','BNB']) assert.equal(coin in parsed.summary['指标'],true);
  assert.equal(parsed.summary['L1L4'].summary,'L1偏多；L2中性');
});
test('长列表标注省略数量，模型不会把截断当成完整',()=>{
  const out=shrink({a:[1,2,3,4,5]},{items:2,text:50});
  assert.deepEqual(out.a,[1,2,'…另有3项已省略']);
});
test('小快照原样保留不标记压缩',()=>{
  const snapshot={report:{ok:true},summary:{'指标':{BTC:{price:100}}},four_lines:'x'};
  const out=compactSnapshot(snapshot);
  assert.equal(out.trimmed,false);
  assert.equal(JSON.parse(out.body).summary['指标'].BTC.price,100);
});
test('极端情况下宽字典也能压进上限',()=>{
  const out=compactSnapshot(bigSnapshot(400,500),20000);
  assert.equal(out.chars<=20000,true);
});
test('浮点数保留有效位，去掉无意义长尾',()=>{
  assert.equal(shrink(0.1234567891234,{items:2,text:9}),0.1234568);
});
