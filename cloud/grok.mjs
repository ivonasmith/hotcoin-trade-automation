// Daily hot-topic fetch through the xAI API (X search + web search), replacing the owner's manual
// "ask Grok for the last 24h" step. The text is only drafting material: it never changes an order.
// Cost note: X search is billed per post fetched (about $0.005 each) plus tokens, so max_turns caps the spend.
export const GROK_DEFAULT_MODEL='grok-4.7';
export const GROK_MAX_TURNS=3;
const shiftDay=(day,delta)=>new Date(Date.parse(day+'T00:00:00Z')+delta*86400000).toISOString().slice(0,10);

export function grokPrompt(day,coins=[]) {
  const watch=coins.length?`重点关注 ${coins.join('、')}，其次是其他被大量讨论的币`:'重点关注 BTC、ETH、SOL、XRP、BNB，其次是其他被大量讨论的币';
  return `你是加密市场的热点编辑。请用 X 搜索和网页搜索，整理【过去24小时】（截至北京时间 ${day} 19:00）加密市场里被大量讨论的热点。

要求：
1. 只写有来源支撑的事实，每条标出发帖账号或媒体名；没有来源的不写。
2. 分四块，每块3-5条，每条一两句：
   - 宏观与政策：美联储、监管、ETF、美股与美元的联动
   - 币种与板块：${watch}
   - 多空声量与社区情绪：反复出现且几乎没有反对声音的说法要单独标出"一致预期"
   - 风险事件：黑客、大额解锁、爆仓、交易所公告
3. 价格和数字只引用帖子或报道里明确写出的，注明出处；不要自己预测涨跌，不要给交易建议。
4. 查不到的块直接写"本块未查到"，不要编。
5. 总长度不超过900字，用中文，不要开场白和结尾总结。`;
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
  const body={model,input:[{role:'user',content:grokPrompt(day,coins)}],
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
