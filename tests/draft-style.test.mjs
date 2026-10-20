import test from 'node:test';
import assert from 'node:assert/strict';
import {DRAFT_SYSTEM,collectTickers,normalizeTickers,lintDraft} from '../cloud/draft-style.mjs';

const TICKERS=['BTC','ETH','SOL','XRP','BNB','ZRO','DOGE'];

test('每个币在每段里第一次出现补$，已有$不重复，已写$的不动',()=>{
  const out=normalizeTickers('审核正文：BTC空单，ETH跌得比BTC多，$SOL不动，SOL继续看。\n\n送审备注：BTC现价三个口径。',TICKERS);
  assert.equal(out,'审核正文：$BTC空单，$ETH跌得比BTC多，$SOL不动，SOL继续看。\n\n送审备注：$BTC现价三个口径。');
});
test('不误伤含币名的长词、ETF等缩写和已有$的币',()=>{
  const out=normalizeTickers('WBTC和BTCUSDT不动，ETF流入，$ETH已有前缀。',TICKERS);
  assert.equal(out,'WBTC和BTCUSDT不动，ETF流入，$ETH已有前缀。');
});
test('紧贴中文也能识别，句中换行不影响',()=>{
  assert.equal(normalizeTickers('今天只挂BTC空单\n以及ZRO解锁',TICKERS),'今天只挂$BTC空单\n以及$ZRO解锁');
});
test('名单来自核心币、快照、$标记和热点摘要里的全大写代码，排除非币缩写',()=>{
  const names=collectTickers({summary:{'指标':{HYPE:{},UNI:{}}}},'ZRO今天解锁，CPI超预期，ETF流入，$DOGE走弱，BREAKING NEW');
  for(const name of ['BTC','HYPE','UNI','ZRO','DOGE']) assert.equal(names.includes(name),true,name);
  for(const name of ['CPI','ETF','NEW','BREAKING']) assert.equal(names.includes(name),false,name);
});
test('用户真实反馈的稿子：补$之后每个币首次出现都带$',()=>{
  const draft='审核正文：今天我只挂一张很小的 BTC 空单。ETH 跌得比 BTC 多，今天不加。ZRO 今天解锁约2,360万枚。\n\n送审备注：可发。';
  const names=collectTickers({summary:{'指标':{BTC:{},ETH:{}}}},'ZRO今天解锁');
  const out=normalizeTickers(draft,names);
  assert.equal(out.includes('一张很小的 $BTC 空单'),true);
  assert.equal(out.includes('$ETH 跌得比 BTC'),true);
  assert.equal(out.includes('$ZRO 今天解锁'),true);
});
test('自检能抓到汉字数字、内部用词和模板格式，达标稿不报警',()=>{
  const bad=lintDraft('审核正文：止损放在八万三千九百六，我没算，快照里带的，二十日均线下方。\n【风险】\n1. 反了\n\n送审备注：可发。');
  assert.equal(bad.some(w=>w.includes('汉字数字')&&w.includes('八万三千九百六')),true);
  assert.equal(bad.some(w=>w.includes('汉字数字')&&w.includes('二十')),true);
  assert.equal(bad.some(w=>w.includes('内部用词')&&w.includes('快照')),true);
  assert.equal(bad.some(w=>w.includes('小标题')),true);
  const good=lintDraft('审核正文：今天我挂一张$BTC空单。进场限价82,314，止损83,960，目标75,730，盈亏比1:4，有效期到10月31日。万一反弹，我两天内撤单；一张单而已。\n\n送审备注：二十分钟内可核对，八万这类字眼只在备注里不检查。');
  assert.deepEqual(good,[]);
});
test('提示词包含正面标准：阿拉伯数字、$前缀、送审备注达标标准，并保留交易闸门',()=>{
  for(const phrase of ['严禁提出新单','1,234.56','$BTC','不写汉字数字','20秒','达标样例','旧单不复活','迷因币裁决未完成'])
    assert.equal(DRAFT_SYSTEM.includes(phrase),true,phrase);
});
test('自检：超长、新单缺盈亏比和有效期、盈亏比算错都会提示；写全且算对不报警',()=>{
  const long='审核正文：'+'字'.repeat(1100)+'\n\n送审备注：可发。';
  assert.equal(lintDraft(long).some(w=>w.includes('超过1000字')),true);
  const missing=lintDraft('审核正文：进场限价82,314.42，止损83,960.71，目标75,729.27，小仓位。\n\n送审备注：可发。');
  assert.equal(missing.some(w=>w.includes('新单缺少')&&w.includes('盈亏比')&&w.includes('有效期')),true);
  const wrong=lintDraft('审核正文：进场限价82,314.42，止损83,960.71，目标75,729.27，盈亏比1:2，有效期到10月31日。\n\n送审备注：可发。');
  assert.equal(wrong.some(w=>w.includes('按三个价位算是1:4.0')),true);
  const ok=lintDraft('审核正文：进场限价82,314.42，距现价+0.99%；止损83,960.71；目标75,729.27，盈亏比1:4，小仓位，有效期到10月31日。\n\n送审备注：可发。');
  assert.deepEqual(ok,[]);
});
test('提示词不再要求强制句，并禁止把费率当方向理由',()=>{
  for(const phrase of ['硬上限1000字','超过1000字才砍','只作温度描述','程序已定的交易决定'])
    assert.equal(DRAFT_SYSTEM.includes(phrase),true,phrase);
});
test('自检：编造的“据报道”数字、系统自述词、超过1000字都会被点出来',()=>{
  const body='审核正文：今天我挂一张$BTC空单。'+'字'.repeat(990)+'昨天ETF净流入约5.27亿美元（据报道）。$ETH也过了筛选，一天只写一张。\n\n送审备注：可发。';
  const warnings=lintDraft(body,null,'热点：亚洲股市下跌，ETF净流入约3.1亿美元');
  assert.equal(warnings.some(w=>w.includes('5.27')&&w.includes('据报道')),true);
  assert.equal(warnings.some(w=>w.includes('内部用词')&&w.includes('筛选')),true);
  assert.equal(warnings.some(w=>w.includes('超过1000字')),true);
  const ok=lintDraft('审核正文：ETF净流入约3.1亿美元（据报道）。\n\n送审备注：可发。',null,'ETF净流入约3.1亿美元');
  assert.equal(ok.some(w=>w.includes('据报道')),false);
});

test('自检：热点里是5.266亿，正文四舍五入写5.27亿不算编造',()=>{
  const body='审核正文：昨天ETF净流入约5.27亿美元（据报道）。\n\n送审备注：可发。';
  assert.equal(lintDraft(body,null,'ETF录得约5.266亿美元净流入').some(w=>w.includes('据报道')),false);
  assert.equal(lintDraft(body,null,'ETF录得约3.1亿美元净流入').some(w=>w.includes('据报道')),true);
});

test('强制句已停用：提示词明确禁止，自检会点出',()=>{
  assert.equal(DRAFT_SYSTEM.includes('必须原样写入这句强制句'),false);
  assert.equal(DRAFT_SYSTEM.includes('已明确停用这两句'),true);
  const warnings=lintDraft('审核正文：多数人在高波动币上亏钱不是看错方向，是按「本金比例」而不是「风险比例」下单。\n\n送审备注：可发。');
  assert.equal(warnings.some(w=>w.includes('已停用')),true);
});
