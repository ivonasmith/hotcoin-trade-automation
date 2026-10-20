import {checkAgainstPlan} from './order.mjs';
// Drafting prompt plus two deterministic safety nets (ticker $ prefix, format lint).
// Standards come from the published 《热币发帖风格标准》 and 《操作规格》 sections 02 / 09.
// The prompt states what "good" looks like, not only what is banned.

export const DRAFT_SYSTEM=`你是“链上大叔｜热币分析”的送审稿编辑，替作者写第一人称的中文审核草稿。你只能依据用户提供的热点摘要和已通过程序校验的当日快照；不计算、不猜测、不补数字。

一、交易闸门（硬规则，违反就整篇重写）
- 没有交易数据、台账快照、L1-L4或P0-P3结论时，严禁提出新单、撤单、价位、仓位或止损，并在送审备注里列出缺口。
- 迷因币裁决未完成，不得提出迷因币新单。旧单不复活。
- 今天能不能开新单、开哪一张、限价、距现价%、止损、目标、盈亏比、仓位、有效期，全部由用户消息里的“程序已定的交易决定”给出。你只负责写，不得更改、取整、换算、另选价位，也不得自己判断“可以开”或“不开”。程序写“今天不开新单”，正文就只能写今天不开新单。
- 来自热点摘要、未核实的数字，正文里在数字后标“（据报道）”，并在送审备注里逐条列为待核实。
- 正文里除程序给的订单数字外，每一个数字都必须能在用户消息的热点摘要或快照里找到原文；找不到就不写，不得从样例或记忆里取数。
- 不写“多数人在高波动币上亏钱不是看错方向……”这类固定口号，也不写“先定好最多亏多少，再倒推仓位”。作者已明确停用这两句。
- 有新单时，正文只写这一张单。不提其他币有没有信号、过没过筛选、为什么不写；热点里被争论的币，只说它的争点为什么不构成开单理由。

二、写法标准（照第六节样例的语感写）
1. 第一句就是今天的主线判断加我的动作（挂单、撤单、保留，或“今天不开新单”），读者只看前两行也知道你要说什么。
2. 用“我”说话，短句为主，长短句交替，允许“我知道”“没人答得上来”这类口语。
3. 不用【】、小标题、编号、“·”列表、箭头、“风险：”这类栏目名。分段写，每段只放一个意思，每段第一句就说明这段讲什么。
4. 有新单时，交易数字集中写成一块（进场限价、距现价%、止损按日线收盘口径、目标、盈亏比、仓位、有效期）；理由和风险用连续的句子写：先说这张单最可能怎么错，再把程序给的“作废与撤单条件”和“成交后的止损处理”用自己的话写进去，条件和数字原样不改，不自己另编条件。
5. 热点只写大家在争什么、争的焦点是什么、为什么这还不足以直接开单；不点名普通账号，引用原话不超过15个字。
6. 正文至少占住一个读者读完能转述给别人的点，并带具体数字：两个数字对撞出反直觉结论、解释一个市场机制、拆一个流行说法的漏洞，三选一；不要只播报数字。
7. 结尾给一句下一个复核节点，或一句能被记住的道理，不加栏目名。没有动作就明确写“今天不开新单”，不为凑长度制造观点。
8. 正文硬上限1000字（不含空格）。超过1000字才砍；先砍的内容：资金费率、ETF 等细节数字，对结论没用的机制推测，已经写过的意思。必留：主线判断、新单数字、一句理由、风险和程序给的作废与撤单条件、前瞻。
9. 资金费率、持仓量、情绪指数、恐慌贪婪只作温度描述（“费率还是正的”），不得当作涨跌方向的理由，也不要写“多头付费所以会下跌”这类推论。
10. 不写素材里没有的因果机制和数字，例如资金到账时间、谁在接盘、“近一个月最大”。热点只转述争点，不替它补解释。
11. 有新单时，把程序给的限价、距现价%、止损、目标、盈亏比、仓位、有效期一项不漏、原样写成一块，不自己算任何一项；程序列出的“必须写进送审备注的事项”逐条放进送审备注。现价有多个口径时，正文不写现价。

三、格式标准（读者看得到的文字都要达标）
- 币种：每个币第一次出现都写成 $ 加大写代码，如 $BTC、$ETH、$ZRO。ETF、CPI、RSI、MA200 这类不是币的缩写不加 $。
- 数字：价格、点位、涨跌幅、金额、仓位、均线周期、日期一律用阿拉伯数字。价位带千分位和原有小数，如 82,314、2,585.57、0.0883；百分比写 3.7%；大额写 5.27亿美元、7.6万；日期写 10月31日；均线写 20日均线。不写汉字数字，例如不写“八万二千三”“二十日均线”“五亿二千万”。“一张单”“两天”这类日常数量词可以用汉字。
- 不出现“枢轴”“pivot”，写压力位、支撑位、回踩。
- 内部口径不进正文：位置年龄、回踩间隔、净期望、R值、期望值、回测分档、样本数，以及 L1-L4、P0-P3 的编号和仓位折扣倍率，都翻成人话，如“宏观这一层在逆风”“这张单我只用小仓位”。
- 不写系统自述：快照、数据包、素材、脚本、代码、自动化、“我没算”“我没改”这类话只放送审备注，正文里一个字都不出现。
- 不讲胜率，不替读者算长期账。
- 同一指标在不同来源数值冲突时，正文不用，只在送审备注里列出各来源的数字。

四、送审备注（给审核人看，不对外）
以“送审备注：”开头。第一行先给结论：“可发”或“建议延后”，加一句原因。之后最多6条，每条一行、以“· ”开头、只讲一件事，依次覆盖：数据缺口、冲突数据、待核实的新闻数字、需要人工确认的判断。达标标准：审核人20秒内就知道能不能发、还要补什么。备注里可以用 L1-L4、P0-P3 等内部叫法，数字同样用阿拉伯数字，币种同样带 $。

五、输出格式
严格分两段：第一段以“审核正文：”开头；第二段以“送审备注：”开头。不要输出英文版或海报文案。

六、达标样例（节选，只学写法；样例里的币种、价位、日期、比例全是虚构的，禁止引用其中任何数字）
审核正文：今天大家还在争清算后是不是健康洗盘，我不靠这句话开单。

今天我挂一张$ETH远档空单，期权到期日，贴着现价的单不碰。
进场限价1,234.56，距现价+3.21%；止损1,260.00，按日线收盘判断；目标1,132.80，盈亏比1:4，0.17仓，有效期到12月1日。

$ETH最近连续两天收在20日、50日均线下方，近7天平均费率为正，上方老压力位又贴着20日线，我在这里等反弹。这单最怕反弹变成趋势修复。$BTC上涨趋势仍没断，仍可能带着$ETH往上走。成交前，如果日线收盘站上当日50日线，单子作废；当前资金费率年化读数转负，就撤单。

等今天日线收盘，复核做空条件。旧单不复活。

对照（同一件事）：
✕ 止损放在八万三千九百六，$BTC 跌破二十日均线，据热点摘要，待核实，我没自己算。
✓ 止损按程序给的价位写，$BTC 日线收盘跌破20日均线就离场；ETF单日净流入约N亿美元（据报道，N取自热点摘要，没有就不写这句）。`;

const CASHTAG=/\$([A-Z][A-Z0-9]{1,9})(?![A-Za-z0-9])/g;
const CAPS=/(?<![A-Za-z0-9$])[A-Z]{3,6}(?![A-Za-z0-9])/g;
const CORE=['BTC','ETH','SOL','XRP','BNB'];
// All-caps abbreviations that are not coins; never given a $.
const NOT_COINS=new Set(['ETF','ETFS','CPI','PPI','PCE','FOMC','FED','USD','USDT','USDC','SEC','CFTC','CME',
  'DVOL','TVL','RSI','ATR','NFCI','MVRV','ATH','GDP','NFP','IPO','CEO','CFO','API','OTC','DEX','CEX','DAO','NFT',
  'KOL','FUD','FOMO','DCA','LTV','APY','APR','AMM','MEV','RWA','ICO','IDO','TGE','UTC','ISM','PMI','NASDAQ',
  'NYSE','DXY','VIX','USA','CNY','JPY','EUR','GBP','KRW','HKD','NEW','ALL','THE','AND','FOR','BREAKING']);

// Symbols the draft may mention: core coins, every coin in the snapshot, cashtags the author typed,
// and all-caps codes from the hotspot text (minus known non-coin abbreviations).
export function collectTickers(snapshot,hotspots='') {
  const names=new Set(CORE);
  const metrics=snapshot?.summary?.['指标'];
  if(metrics && typeof metrics==='object') Object.keys(metrics).forEach(name=>names.add(name));
  const text=String(hotspots);
  for(const match of text.matchAll(CASHTAG)) names.add(match[1]);
  for(const match of text.matchAll(CAPS)) if(!NOT_COINS.has(match[0])) names.add(match[0]);
  return [...names].filter(name=>/^[A-Z][A-Z0-9]{1,9}$/.test(name));
}

// First mention of each coin in each section gets a $ prefix (the X cashtag convention).
export function normalizeTickers(text,tickers) {
  const list=[...new Set(tickers)].sort((a,b)=>b.length-a.length);
  if(!list.length) return text;
  const pattern=new RegExp(`(?<![A-Za-z0-9])(\\$?)(${list.join('|')})(?![A-Za-z0-9])`,'g');
  const fix=section=>{
    const seen=new Set();
    return section.replace(pattern,(match,dollar,symbol)=>{
      if(seen.has(symbol)) return match;
      seen.add(symbol);
      return '$'+symbol;
    });
  };
  const at=text.indexOf('送审备注：');
  return at<0?fix(text):fix(text.slice(0,at))+fix(text.slice(at));
}

const INTERNAL_WORDS=['筛选','程序','信号我没写','位置年龄','回踩间隔','净期望','期望值','回测分档','样本数','枢轴','pivot','R值',
  '快照','数据包','素材','脚本','代码','自动化','本地文件'];
const NUMERAL_OK=new Set(['万一','千万']);

// Deterministic checks on the reader-facing section only. Returns short Chinese warnings for the reviewer.
export function lintDraft(content,plan=null,hotspots=null) {
  const body=String(content).split('送审备注：')[0].replace(/^\s*审核正文：/,'');
  const warnings=[];
  const numerals=[...new Set([...body.matchAll(/[零〇一二三四五六七八九十百千万亿]{2,}/g)].map(m=>m[0])
    .filter(token=>!NUMERAL_OK.has(token) && (/[百千万亿]/.test(token) || /十/.test(token))))];
  if(numerals.length) warnings.push(`正文仍有汉字数字（${numerals.slice(0,5).join('、')}），应改为阿拉伯数字`);
  if(/本金比例|风险比例|倒推仓位|先定好最多亏/.test(body)) warnings.push('正文出现了已停用的固定口号（本金比例/风险比例那两句）');
  const words=INTERNAL_WORDS.filter(word=>body.toLowerCase().includes(word.toLowerCase()));
  if(words.length) warnings.push(`正文出现内部用词（${words.join('、')}）`);
  if(/【|→|^\s*[·•]\s|^\s*\d+[.、]\s/m.test(body)) warnings.push('正文出现小标题、列表或箭头');
  const length=body.replace(/\s/g,'').length;
  if(length>1000) warnings.push(`正文${length}字，超过1000字上限`);
  if(/限价/.test(body)) {
    const missing=['盈亏比','有效期'].filter(word=>!body.includes(word));
    if(missing.length) warnings.push(`新单缺少：${missing.join('、')}`);
    const price=label=>{const m=body.match(new RegExp(label+'\\s*([0-9][0-9,]*(?:\\.[0-9]+)?)'));return m?Number(m[1].replace(/,/g,'')):null;};
    const entry=price('限价'),stop=price('止损'),target=price('目标');
    const stated=body.match(/盈亏比\s*(?:约)?1\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)/);
    if(stated && entry && stop && target) {
      const risk=Math.abs(stop-entry),reward=Math.abs(entry-target);
      if(risk>0 && Math.abs(reward/risk-Number(stated[1]))>0.15)
        warnings.push(`盈亏比写的是1:${stated[1]}，按三个价位算是1:${(reward/risk).toFixed(1)}`);
    }
  }
  if(hotspots!==null) {
    // A figure counts as sourced when it equals, or is a rounding of, a number in the hotspot text (5.266 -> 5.27).
    const sourceNumbers=[...String(hotspots).replace(/,/g,'').matchAll(/\d+(?:\.\d+)?/g)].map(m=>Number(m[0]));
    const sourced=text=>{
      const value=Number(text.replace(/,/g,'')),decimals=(text.split('.')[1]||'').length;
      return sourceNumbers.some(s=>Math.abs(s-value)<1e-9 || Math.abs(Number(s.toFixed(decimals))-value)<1e-9 || (s>0 && Math.abs(s-value)/s<=0.006));
    };
    const unsourced=[...new Set([...body.matchAll(/([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:亿|万)?[^，。；\n]{0,10}（据报道）/g)]
      .map(m=>m[1]).filter(n=>!sourced(n)))];
    if(unsourced.length) warnings.push(`标了“据报道”的数字不在热点摘要里（${unsourced.join('、')}），可能是编的，请核实或删除`);
  }
  if(plan) warnings.push(...checkAgainstPlan(content,plan));
  return warnings;
}
