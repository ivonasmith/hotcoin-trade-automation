// Automatic review for the daily post, replacing the two taps the owner used to do:
// (1) text gate: program checks + an independent reviewer pass over the draft;
// (2) final gate: if the text was auto-approved, the posters are rendered by program from the same numbers,
//     and the program has already cross-checked zh/en numbers, so the final approval is automatic too.
// Anything that fails either gate falls back to the old human buttons: nothing is published unreviewed.
import {splitDraft,bodyProblems} from './formal.mjs';

export const REVIEW_SYSTEM=`你是“链上大叔｜热币分析”的发布前评审员，只做评审，不改写。下面给你：今日素材（热点摘要、程序异动榜、当日快照与程序订单）和一篇待发的中文帖子正文。
逐项检查，有任何一项不合格就判不通过：
1. 事实：正文里除程序订单和战绩外的每一个数字、每一个事件说法，都必须能在素材里找到依据；素材里没有的因果解释、资金动向、人物言论，一律算编造。来自热点摘要的数字必须标“（据报道）”。
2. 订单：正文里的进场限价、止损、目标、盈亏比、仓位、有效期、撤单条件必须和“程序已定的交易决定”完全一致；程序写“今天不开新单”，正文不得出现任何新单、价位建议。
3. 红线：不得出现稳赚、保本、带群、返佣、付费链接、制造焦虑（“再不进就错过”）、喊单式措辞；不得向读者提问收尾；不得点名普通账号，引用原话不超过15个字。
4. 口径：热点只写“争什么”，不替它补解释；资金费率、持仓量、情绪指数只作温度描述，不得当作涨跌方向的理由；不写战绩播报、系统自述、与行情无关的话。
5. 合规：不得包含明显违法违规、辱骂、对具体个人的指控、未经证实的黑客/暴雷/跑路指控（除非素材里有明确来源且标了“据报道”）。
只输出一个 JSON，不要任何别的文字：{"pass":true或false,"issues":["不合格的具体问题，每条一句，指出是哪句话"]}。pass 为 true 时 issues 为空数组。拿不准的按不通过处理。`;

export const reviewPrompt=input=>`请评审下面这篇待发帖子。\n\n${input}`;

// Deterministic gate. Returns a list of problems; empty means the program sees nothing wrong.
export function ruleGate(draft) {
  const problems=[];
  if(!draft || typeof draft.content!=='string') return ['没有稿件'];
  const {body,notes}=splitDraft(draft.content);
  if(!body) problems.push('找不到正文');
  if(draft.truncated) problems.push('稿件在中途被截断');
  if(Array.isArray(draft.lint) && draft.lint.length) problems.push('格式自检未通过：'+draft.lint.join('；'));
  if(body) problems.push(...bodyProblems(body,draft));
  const verdict=(notes.split('\n').map(line=>line.trim()).find(Boolean))||'';
  if(!/可发/.test(verdict) || /建议延后/.test(verdict)) problems.push('送审备注的结论不是“可发”：'+verdict.slice(0,60));
  return problems;
}

// The model is told to return bare JSON; tolerate a code fence or a sentence around it. Anything unreadable fails closed.
export function parseReview(text) {
  const raw=String(text||'');
  const match=raw.match(/\{[\s\S]*\}/);
  if(!match) return {pass:false,issues:['评审员没有返回可读的结果']};
  try {
    const data=JSON.parse(match[0]);
    const issues=Array.isArray(data.issues)?data.issues.map(String).filter(Boolean).slice(0,8):[];
    const pass=data.pass===true && issues.length===0;
    return {pass,issues:pass?[]:(issues.length?issues:['评审员判定不通过，但没有写明原因'])};
  } catch { return {pass:false,issues:['评审员返回的结果不是有效 JSON']}; }
}
