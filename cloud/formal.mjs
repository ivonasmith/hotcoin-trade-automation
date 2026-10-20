// Pure helpers for the post-approval flow: formal zh/en text, the number-equality check, issue numbering,
// and the payloads handed to the poster and ledger jobs. No storage, no network.
import {eligible} from '../calendar.mjs';
import {fmtPrice,checkAgainstPlan} from './order.mjs';

export const VALID_DAYS=15;

// "审核正文：…\n\n送审备注：…" -> {body, notes}
export function splitDraft(content) {
  const [head,...rest]=String(content||'').split('送审备注：');
  return {body:head.replace(/^\s*审核正文：/,'').trim(),notes:rest.join('送审备注：').trim()};
}

// The formal Chinese post is the approved body, word for word, plus the trade-log link. No model touches it.
// The opening line announces issue number and date; the program adds it because the program owns the issue number.
export const postHeader=(lang,issue,day)=>lang==='zh'?`【热币分析·第${issue}期（${day}）】`:`【Hot Coin Desk · Issue ${issue} (${day})】`;
export function formalChinese(body,tradeLogUrl,header='') {
  return `${header?header+'\n':''}${body.trim()}\n\n交易记录：${tradeLogUrl}`;
}
export function formalEnglish(text,tradeLogUrl,header='') {
  return `${header?header+'\n':''}${text.trim()}\n\nTrade log: ${tradeLogUrl}`;
}

// A reviewer approved this body; before it becomes the only source, confirm it still matches the program's order.
export function bodyProblems(body,draft) {
  const plan={allowed:Boolean(draft.order_allowed),order:draft.order||null};
  const problems=checkAgainstPlan(body,plan);
  if(/\[|\]|送审备注|待核实/.test(body)) problems.push('正文里混入了送审用的标记');
  return problems;
}

export const TRANSLATE_SYSTEM=`You translate an approved Chinese trading post into an English post for X. The Chinese text is the only source.
Hard rules:
- Keep every number exactly. Prices keep their thousands separators and decimals (2,585.57). Percentages keep their sign (+4.54%). Ratios stay 1:4. Position sizes keep their value ("0.12仓" -> "0.12 size").
- Dates: "10月31日" -> "Oct 31". Money: "5.27亿美元" -> "$527 million"; "7.6万" -> "76,000". Averages: "50日均线" -> "50-day moving average".
- Keep every $TICKER as written.
- Keep abbreviations like 80K, 5M exactly as written; never expand "80K" to "80,000" or the reverse. Ranges like "80-81K" stay in the same form.
- Do not add, remove, round, convert or explain anything. Do not add numbers (no years, no counts, no list numbers).
- First person, short sentences, plain English. No markdown, no headings, no emoji, no hashtags, no link.
- Output only the English post text. It must contain no Chinese characters.`;
export const translatePrompt=zh=>`Translate this approved post:\n\n${zh}`;

const MONTHS={jan:1,feb:2,mar:3,apr:4,may:5,jun:6,jul:7,aug:8,sep:9,oct:10,nov:11,dec:12};
const canon=value=>String(Number(Number(value).toPrecision(12)));
const stripUrls=text=>String(text).replace(/https?:\/\/\S+/g,' ');
// Every numeric fact in a post as a comparable token, so zh "10月31日" / "5.27亿" equals en "Oct 31" / "$527 million".
export function numberTokens(text,lang) {
  let rest=stripUrls(text);
  // "80-81K" / "80–81千" share one unit: expand to "80K-81K" so both ends scale the same way.
  rest=rest.replace(/(\d[\d,]*(?:\.\d+)?)\s*([-–~～至到])\s*(\d[\d,]*(?:\.\d+)?)\s*([kKmM千万亿])(?![A-Za-z])/g,'$1$4$2$3$4');
  const out=[];
  const take=(regex,fn)=>{ rest=rest.replace(regex,(...m)=>{ out.push(fn(m)); return ' '; }); };
  if(lang==='zh') {
    take(/(\d{1,2})月(\d{1,2})日/g,m=>`d:${Number(m[1])}/${Number(m[2])}`);
    take(/(\d[\d,]*(?:\.\d+)?)\s*亿/g,m=>canon(Number(m[1].replace(/,/g,''))*1e8));
    take(/(\d[\d,]*(?:\.\d+)?)\s*万/g,m=>canon(Number(m[1].replace(/,/g,''))*1e4));
    take(/(\d[\d,]*(?:\.\d+)?)\s*千/g,m=>canon(Number(m[1].replace(/,/g,''))*1e3));
    take(/(\d[\d,]*(?:\.\d+)?)\s*([kKmMbB])(?![A-Za-z])/g,m=>canon(Number(m[1].replace(/,/g,''))*{k:1e3,m:1e6,b:1e9}[m[2].toLowerCase()]));
  } else {
    take(/\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b/gi,
      m=>`d:${MONTHS[m[1].toLowerCase()]}/${Number(m[2])}`);
    take(/(\d[\d,]*(?:\.\d+)?)\s*(billion|million|thousand|bn|mn|[BMK])\b/gi,m=>{
      const unit=m[2].toLowerCase();
      const mult={billion:1e9,bn:1e9,b:1e9,million:1e6,mn:1e6,m:1e6,thousand:1e3,k:1e3}[unit];
      return canon(Number(m[1].replace(/,/g,''))*mult);
    });
  }
  for(const match of rest.matchAll(/\d[\d,]*(?:\.\d+)?/g)) out.push(canon(Number(match[0].replace(/,/g,''))));
  return out.sort();
}
export function numberDiff(zh,en) {
  const a=numberTokens(zh,'zh'),b=numberTokens(en,'en');
  const count=list=>list.reduce((map,x)=>map.set(x,(map.get(x)||0)+1),new Map());
  const ca=count(a),cb=count(b),missing=[],extra=[];
  for(const [key,n] of ca) if((cb.get(key)||0)<n) missing.push(key);
  for(const [key,n] of cb) if((ca.get(key)||0)<n) extra.push(key);
  return {missing,extra,ok:!missing.length && !extra.length};
}
export function englishProblems(zh,en) {
  const problems=[];
  if(/[　-〿一-鿿＀-￯]/.test(en)) problems.push('英文稿里出现了中文字符');
  const diff=numberDiff(zh,en);
  if(diff.missing.length) problems.push(`英文稿缺少这些数字：${diff.missing.join('、')}`);
  if(diff.extra.length) problems.push(`英文稿多出这些数字：${diff.extra.join('、')}`);
  const tickers=[...String(zh).matchAll(/\$[A-Z][A-Z0-9]{1,9}\b/g)].map(m=>m[0]);
  const missing=tickers.filter(t=>!en.includes(t));
  if(missing.length) problems.push(`英文稿缺少币种标记：${[...new Set(missing)].join('、')}`);
  return problems;
}

// Issue numbers count issues that were actually released, never calendar days.
// `used` maps day -> issue for issues the reviewer already gave final approval (the program records it then).
//  - a day that already has an approved issue keeps it (re-runs never renumber);
//  - the ledger already updated to today means the ledger's number is today's;
//  - otherwise the next number follows the highest issue known so far (ledger or approved earlier). Skipped days leave no gap.
export function issueNumber(ledger,day,used={}) {
  if(!ledger || ledger.ok!==true || !Number.isInteger(ledger.updated_issue) || !ledger.updated_to) return null;
  if(Number.isInteger(used[day])) return used[day];
  if(ledger.updated_to>=day) return ledger.updated_issue;
  const earlier=Object.entries(used).filter(([d,n])=>d<day && Number.isInteger(n)).map(([,n])=>n);
  return Math.max(ledger.updated_issue,...earlier)+1;
}

export function posterNames(issue) {
  return {zh_desktop:`poster_zh_第${issue}期.png`,zh_mobile:`poster_zh_第${issue}期-手机版.png`,
    en_desktop:`poster_en_第${issue}期.png`,en_mobile:`poster_en_第${issue}期-手机版.png`};
}

export function posterPayload({day,issue,draft,snapshot,body,english}) {
  const ledger=snapshot?.ledger,facts=snapshot?.poster_facts;
  if(!ledger || ledger.ok!==true) throw Error('台账没有读取成功，海报里的账户和持仓无法生成');
  if(!facts || facts.ok!==true) throw Error('涨腿/山寨窗口数据没有生成，海报无法出图');
  const metrics=snapshot.summary?.['指标']||{};
  const symbols=new Set((ledger.active||[]).map(row=>row.symbol));
  if(draft.order) symbols.add(draft.order.symbol);
  const prices={};
  for(const symbol of symbols) if(Number.isFinite(metrics[symbol]?.price)) prices[symbol]=metrics[symbol].price;
  const order=draft.order?{symbol:draft.order.symbol,entry:draft.order.entry,stop:draft.order.stop,
    target:draft.order.target,size:draft.order.size}:null;
  return {day,issue,formal:{zh:body,en:english},order,prices,
    ledger:{active:ledger.active||[],counts:ledger.counts||{},equity_usd:ledger.equity_usd,
      cumulative_pnl_usd:ledger.cumulative_pnl_usd,closed_recent:ledger.closed_recent||[],
      updated_to:ledger.updated_to,updated_issue:ledger.updated_issue},
    facts:{leg:facts.leg,alt:facts.alt}};
}

const addDays=(day,n)=>new Date(Date.parse(day+'T00:00:00Z')+n*86400000).toISOString().slice(0,10);
export function ledgerPayload({day,issue,draft,snapshot}) {
  const o=draft.order;
  const proposals=(snapshot?.ledger?.proposals||[]).filter(p=>['fill','cancel','expire','stop_out'].includes(p.type));
  if(!o && !proposals.length) return null;
  const payload={day,issue,order:null,proposals};
  if(o) {
    const risk=Math.round(o.amount_usd*Math.abs(o.stop_pct)/100*10)/10;
    const gain=Math.round(risk*o.ratio*10)/10;
    const expiry=addDays(day,VALID_DAYS);
    payload.order={symbol:o.symbol,tier:'慢档日线',size:Number(o.size.toFixed(2)),entry:o.entry,stop:o.stop,target:o.target};
    payload.note=`程序按已审核送审稿写入。仓位${o.size_text}（名义约${o.amount_usd}美元，止损约亏${risk}美元，到目标约赚${gain}美元）`
      +(o.path?`；放行路径：${o.path}`:'')
      +`；成交前日线收盘站上MA50${o.cancel_ma50?`（约${fmtPrice(o.cancel_ma50)}）`:''}则作废，成交前价格先跌到目标（${fmtPrice(o.target)}）则作废，成交前费率读数转负则撤单；`
      +`成交后2R（${fmtPrice(o.r2)}）止损下移到进场价，3R（${fmtPrice(o.r3)}）跟随MA20下移，止损只往盈利方向移。到期${expiry}。`;
    payload.conditions=[
      {condition:`成交前${o.symbol}日线收盘价≥当日MA50${o.cancel_ma50?`（${day}约${fmtPrice(o.cancel_ma50)}）`:''}`,
        action:'本单作废（趋势已破条件不成立），下一期写明',triggered:`否（未成交；${day}日线未收盘）`,conclusion:'每期复核'},
      {condition:`成交前${o.symbol}价格先跌到目标${fmtPrice(o.target)}`,action:'本单作废（目标已先到，不再追空）',
        triggered:'否（未成交，价格未到目标）',conclusion:'每期复核'},
      {condition:`成交前${o.symbol}资金费率年化转负`,action:'撤单（空头也拥挤）',
        triggered:'否（发布时费率读数未转负）',conclusion:'每期复核'},
      {condition:`成交后价格到${fmtPrice(o.r2)}（2R）/ ${fmtPrice(o.r3)}（3R）`,
        action:`止损下移到进场价${fmtPrice(o.entry)} / 跟随MA20下移`,triggered:'否（未成交）',conclusion:'挂单中'}];
  }
  return payload;
}
