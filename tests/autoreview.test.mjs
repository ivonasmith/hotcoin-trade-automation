import test from 'node:test';
import assert from 'node:assert/strict';
import {ruleGate,parseReview} from '../cloud/autoreview.mjs';

const ORDER={symbol:'ETH',side:'空',entry:2585.57,stop:2637.28,target:2378.72,dist_pct:4.54,stop_pct:2.0,ratio:4,ratio_text:'1:4',
  size:0.1167,size_text:'0.12仓',amount_usd:233,expires:'10月31日',cancel_ma50:2553.1,r2:2482.15,r3:2430.44,path:'路A趋势已破'};
const good={content:'审核正文：今天不开新单。$BTC收在20日均线下方，日线收盘站回之前我不碰做多。\n\n送审备注：可发，数据齐全',lint:[],order:null,order_allowed:false};

test('规则闸门：干净的稿件通过',()=>{ assert.deepEqual(ruleGate(good),[]); });

test('规则闸门：自检有问题、被截断、备注不是“可发”、正文混入标记都拦住',()=>{
  assert.match(ruleGate({...good,lint:['正文出现战绩播报']}).join(),/格式自检未通过/);
  assert.match(ruleGate({...good,truncated:true}).join(),/截断/);
  assert.match(ruleGate({...good,content:good.content.replace('可发，数据齐全','建议延后，数据缺口')}).join(),/不是“可发”/);
  assert.match(ruleGate({...good,content:'审核正文：今天不开新单[待核实]。\n\n送审备注：可发'}).join(),/混入了送审用的标记/);
  assert.match(ruleGate({content:'没有正文标记',lint:[]}).join(),/备注|结论/);
  assert.deepEqual(ruleGate(null),['没有稿件']);
});

test('规则闸门：程序说今天不开新单，正文却写了新单就拦住',()=>{
  const bad={...good,content:'审核正文：我挂一张$ETH空单，限价2,585.57，止损2,637.28，目标2,378.72，盈亏比1:4，0.12仓，有效期到10月31日。\n\n送审备注：可发'};
  assert.ok(ruleGate(bad).length>0);
});

test('评审结果解析：只认明确的通过；乱码、缺原因、pass为真却带问题，都按不通过',()=>{
  assert.deepEqual(parseReview('{"pass":true,"issues":[]}'),{pass:true,issues:[]});
  assert.deepEqual(parseReview('好的：\n```json\n{"pass":true,"issues":[]}\n```'),{pass:true,issues:[]});
  assert.equal(parseReview('{"pass":true,"issues":["第二段数字没出处"]}').pass,false);
  const failed=parseReview('{"pass":false,"issues":["编造了资金动向"]}');
  assert.deepEqual(failed,{pass:false,issues:['编造了资金动向']});
  assert.equal(parseReview('{"pass":false}').issues.length,1);
  assert.equal(parseReview('没法评审').pass,false);
  assert.equal(parseReview('{坏掉的json').pass,false);
  assert.equal(parseReview('').pass,false);
});
