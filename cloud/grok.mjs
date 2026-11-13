// Daily hot-topic fetch through the xAI API (X search + web search), replacing the owner's manual
// "ask Grok for the last 24h" step. The text is only drafting material: it never changes an order.
// Cost note: X search is billed per post fetched (about $0.005 each) plus tokens, so max_turns caps the spend.
export const GROK_DEFAULT_MODEL='grok-4.7';
export const GROK_MAX_TURNS=3;
const shiftDay=(day,delta)=>new Date(Date.parse(day+'T00:00:00Z')+delta*86400000).toISOString().slice(0,10);

// 用的是老板手动问 Grok 时的原版提示词（grok_prompt.txt，一字不改），只在前面补一行“当前时间”——接口没有时钟，
// 原文要求“把时间带上”，不补这一行 Grok 不知道现在是几点。
const ORIGINAL_PROMPT=`近 24 小时（截止当前最新时间，请把时间带上）X（推特）上 Web3/加密市场的热门讨论与事件概览。请严格按照以下四段框架输出：

1. 市场全局情绪量化打分
【情绪综合得分】：给出一个介于 −1.0（极度恐慌/看空）到 +1.0（极度狂热/看多）之间的精确分值，并用一句话概括核心原因（必须包含：当天高互动样本主导方向 + 是否出现 Fear & Greed 指数引用或清算/突破类关键词）。
【多空声量观察】：基于过去 24 小时高互动样本，主导方向是偏多 / 偏空 / 均衡；各给出 1 个最能代表多空双方的高互动核心逻辑（不写人名、不编造）。

2. 热门代币与讨论聚焦
提取过去 24 小时提及率、讨论热度暴涨的前 3–5 个代币（按样本互动量粗排），逐个注明：
核心标签/由头（如：巨鲸建仓、技术升级、解锁临近、黑客攻击、上所、ETF 进展、价格突破）
这个由头是新出现的，还是已经连续讨论了好几天（分不清楚就直接写"分不清楚"）
讨论强度相对排名（1 最热）

3. 主要事件与驱动因素
只列出过去 24 小时真正被高互动讨论的核心事件（最多 4 条），按发生的大致时间线陈述。
每条必须注明数据来源类型（KOL 推文 / 项目官方公告 / 链上异动监控 / 媒体报道 / 交易所公告）。
如果当天没有明确事件，就写"过去 24 小时无突出高互动事件"。

4. 社区情绪分化与极端观点
市场目前是否存在严重分歧？用一句话回答。
捕获 1–2 个互动量极高、但属于「逆向思维」或「极端情绪」的典型观点，只描述其核心逻辑，不需要人名。
【一致预期观察】（新增）：反过来看，过去24小时有没有一个说法被反复提及、几乎没有对立声音出现（比如"某个月份历史上都会涨/跌"这类）？如果有，写出这个说法本身，以及它已经连续出现了几天（分不清楚就写"分不清楚"）；如果没有明显的一致预期，直接写"暂无"。`;

export function grokPrompt(day) {
  return `当前时间：北京时间 ${day} 19:00（请以此为“截止当前最新时间”）。请用 X 搜索和网页搜索回答。\n\n${ORIGINAL_PROMPT}`;
}

export function responseText(data) {
  if(typeof data?.output_text==='string' && data.output_text.trim()) return data.output_text.trim();
  const parts=[];
  for(const item of data?.output||[]) {
    if(item?.type!=='message') continue;
    for(const block of item.content||[])
      if(block?.type==='output_text' && block.text) parts.push(block.text);
  }
  return parts.join('\n').trim();
}

export async function grokHotspots(env,day,{coins=[],fetcher=fetch,timeoutMs=240000}={}) {
  if(!env.XAI_API_KEY) throw Error('未配置 xAI 密钥');
  const model=env.XAI_MODEL||GROK_DEFAULT_MODEL;
  const body={model,input:[{role:'user',content:grokPrompt(day)}],
    tools:[{type:'x_search',from_date:shiftDay(day,-1),to_date:shiftDay(day,1)},{type:'web_search'}],
    max_turns:GROK_MAX_TURNS};
  let response,raw;
  try {
    response=await fetcher('https://api.x.ai/v1/responses',{method:'POST',
      headers:{Authorization:'Bearer '+env.XAI_API_KEY,'Content-Type':'application/json'},
      body:JSON.stringify(body),signal:AbortSignal.timeout(timeoutMs)});
    raw=await response.text();
  } catch(cause) {
    const timedOut=cause?.name==='TimeoutError' || cause?.name==='AbortError';
    throw Error(timedOut?'xAI 请求超时':'xAI 网络连接失败');
  }
  let data=null;
  try {data=JSON.parse(raw);} catch {data=null;}
  if(!response.ok) throw Error(`xAI 返回 ${response.status}${data?.error?.message?`：${String(data.error.message).slice(0,120)}`:''}`);
  const text=responseText(data);
  if(!text) throw Error('xAI 没有返回正文');
  const tools=data?.usage?.server_side_tool_usage_details||{};
  const posts=Number(tools.x_posts_fetched)||0;
  // xAI returns the exact charge (tokens + every tool call) as cost_in_usd_ticks, 1 USD = 1e10 ticks; only fall back to the per-post estimate when it is absent.
  const ticks=Number(data?.usage?.cost_in_usd_ticks);
  const exact=Number.isFinite(ticks) && ticks>0;
  const estimated_usd=exact?Math.round(ticks/1e10*1000)/1000
    :Math.round((posts*0.005+(Number(tools.x_users_fetched)||0)*0.01)*1000)/1000;
  return {text,model,posts,searches:Number(tools.x_search_calls)||0,cost_exact:exact,estimated_usd,usage:data?.usage||null};
}
