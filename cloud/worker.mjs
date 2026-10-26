import {beijingDay,eligible} from '../calendar.mjs';
import {compactSnapshot} from './snapshot.mjs';
import {buildOrderPlan,orderBlock} from './order.mjs';
import {DRAFT_SYSTEM,collectTickers,normalizeTickers,lintDraft} from './draft-style.mjs';
import {Flow,tgErrorReason} from './flow.mjs';
import {randomToken,sha256,matches,isAdmin,seal,unseal,escapeHTML,adminFormToken,validAdminPost} from './security.mjs';

const SCOPES='tweet.read tweet.write users.read media.write offline.access';
const HEADERS={
  'Cache-Control':'no-store', 'Referrer-Policy':'no-referrer',
  'X-Content-Type-Options':'nosniff',
  'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https://x.com; frame-ancestors 'none'",
};
function json(data,status=200) { return Response.json(data,{status,headers:HEADERS}); }
function page(title,body,status=200,extra={}) {
  return new Response(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHTML(title)}</title><style>body{font:17px/1.7 system-ui;max-width:740px;margin:60px auto;padding:0 22px;color:#172b39}code{word-break:break-all;background:#f0f3f5;padding:4px}button{font:inherit;padding:10px 18px;cursor:pointer}a{color:#146290}li{margin:8px 0}</style><h1>${escapeHTML(title)}</h1>${body}</html>`,
    {status,headers:{...HEADERS,'Content-Type':'text/html; charset=utf-8',...extra}});
}
function csrfCookie(request) {
  return (request.headers.get('Cookie')||'').split(';').map(s=>s.trim())
    .find(s=>s.startsWith('desk_oauth='))?.slice(11);
}
// Errors carry `kind` (timeout | network | non_json) and, when the server answered, the real `status`,
// so callers can tell a slow relay from a gateway error page instead of guessing "502".
async function provider(url,options,timeoutMs=25000) {
  let response,text;
  try {
    response=await fetch(url,{...options,signal:AbortSignal.timeout(timeoutMs)});
    text=await response.text();
  } catch(cause) {
    const timedOut=cause?.name==='TimeoutError' || cause?.name==='AbortError';
    const error=Error(timedOut?'服务请求超时':'服务网络连接失败');
    error.kind=timedOut?'timeout':'network';
    throw error;
  }
  let data=null;
  try {data=JSON.parse(text);} catch {data=null;}
  if(data===null || typeof data!=='object') {
    const error=Error('服务响应无法解析');
    error.kind='non_json';
    error.status=response.status;
    error.providerMessage=text.replace(/<[^>]*>/g,' ').replace(/\s+/g,' ').trim().slice(0,200);
    throw error;
  }
  if(!response.ok || data.ok===false) {
    const error=Error(`服务请求失败（HTTP ${response.status}）`);
    error.status=response.status;
    error.providerType=data?.error?.type||'';
    error.providerMessage=String(data?.error?.message||data?.error_msg||data?.message||data?.msg||data?.detail||
      (Array.isArray(data?.errors)?data.errors.map(item=>item?.detail||item?.message||item?.title).filter(Boolean).join('；'):'')||
      (typeof data?.error==='string'?data.error:'')||JSON.stringify(data)||'').slice(0,500);
    throw error;
  }
  return data;
}
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
// Retry once for conditions that usually clear on their own: timeouts, network drops, 429/5xx, gateway pages.
async function withRetry(run,attempts=2,delayMs=1500) {
  for(let attempt=1;;attempt++) {
    try { return await run(); }
    catch(error) {
      const transient=!error.status || error.status===429 || error.status>=500;
      error.attempts=attempt;
      if(!transient || attempt>=attempts) throw error;
      await sleep(delayMs);
    }
  }
}
function anthropicFailure(error) {
  const status=Number(error?.status)||null;
  const byKind={timeout:'Claude官方API响应超时，没有在等待时间内返回。',
    network:'连不上Claude官方API（网络连接失败）。',
    non_json:'Claude官方API返回的不是正常数据。'}[error?.kind];
  const byStatus={
    400:'请求格式或模型名不被Claude API接受。',
    401:'API Key无效、已撤销，或复制时缺少字符。',
    402:'Claude API账户没有可用预付额度。',
    403:'当前API账户、地区或组织没有访问权限。',
    404:'该API账户目前无法使用填写的模型。',
    429:'Claude API额度或调用频率已达到限制。',
    529:'Claude API当前过载，请稍后重试。',
  }[status];
  const fallback=status?'Claude API暂时无法完成检查。':(error?.message||'Claude API暂时无法完成检查。');
  const label=status?`HTTP ${status}`:({timeout:'请求超时',network:'网络错误'}[error?.kind]||'无状态码');
  const detail=String(error?.providerMessage||'').replace(/sk-[A-Za-z0-9_-]{8,}/g,'[密钥已隐藏]').slice(0,300);
  return {status,label,reason:byKind||byStatus||fallback,detail,attempts:Number(error?.attempts)||1};
}
function bedrockFailure(error) {
  const status=Number(error?.status)||null;
  const byKind={timeout:'亚马逊Bedrock响应超时，没有在等待时间内返回。',
    network:'连不上亚马逊Bedrock（网络连接失败）。',
    non_json:'亚马逊Bedrock返回的不是正常数据。'}[error?.kind];
  const byStatus={
    400:'Bedrock不接受当前模型编号、区域或请求格式。',
    401:'Bedrock API Key无效、已过期，或复制时缺少字符。',
    403:'这把Key没有调用权限：多半是Key的权限没勾Bedrock，或该模型还没在Bedrock里开通访问。',
    404:'这个区域找不到填写的模型编号。',
    408:'Bedrock处理超时。',
    424:'Bedrock模型暂时不可用。',
    429:'Bedrock额度或调用频率已达到限制。',
    503:'Bedrock当前繁忙，请稍后重试。'}[status];
  const fallback=status?'Bedrock暂时无法完成检查。':(error?.message||'Bedrock暂时无法完成检查。');
  const label=status?`HTTP ${status}`:({timeout:'请求超时',network:'网络错误'}[error?.kind]||'无状态码');
  const detail=String(error?.providerMessage||'').replace(/ABSK[A-Za-z0-9+/=_-]{8,}/g,'[密钥已隐藏]').slice(0,300);
  return {status,label,reason:byKind||byStatus||fallback,detail,attempts:Number(error?.attempts)||1};
}
function geminiFailure(error) {
  const status=Number(error?.status)||null;
  const byKind={timeout:'Gemini响应超时，没有在等待时间内返回。',network:'连不上Gemini（网络连接失败）。',
    non_json:'Gemini返回的不是正常数据。'}[error?.kind];
  const byStatus={400:'Gemini不接受当前模型名、请求格式，或所在地区不支持。',401:'Gemini API Key无效。',
    403:'Gemini API Key没有权限，或所在地区不支持。',404:'Gemini找不到填写的模型名。',
    429:'Gemini额度或调用频率已达到限制。',503:'Gemini当前繁忙，请稍后重试。'}[status];
  const fallback=status?'Gemini暂时无法完成请求。':(error?.message||'Gemini暂时无法完成请求。');
  const label=status?`HTTP ${status}`:({timeout:'请求超时',network:'网络错误'}[error?.kind]||'无状态码');
  const detail=String(error?.providerMessage||'').replace(/AIza[A-Za-z0-9_-]{10,}/g,'[密钥已隐藏]').slice(0,300);
  return {status,label,reason:byKind||byStatus||fallback,detail,attempts:Number(error?.attempts)||1};
}
function apiNebulaFailure(error) {
  // `status` is the real HTTP status when the server answered, otherwise null (never a made-up 502).
  const status=Number(error?.status)||null;
  const byKind={timeout:'ApiNebula响应超时，没有在等待时间内返回。',
    network:'连不上ApiNebula（网络连接失败）。',
    non_json:'ApiNebula返回的不是正常数据，像是网关错误页。'}[error?.kind];
  const byStatus={400:'ApiNebula不接受当前模型名或请求格式。',401:'ApiNebula令牌无效或不完整。',
    402:'ApiNebula账户余额不足。',403:'令牌分组或模型列表不允许调用该模型。',
    404:'ApiNebula没有找到该模型或Claude兼容接口。',429:'ApiNebula额度或频率已达到限制。'}[status];
  const fallback=status?'ApiNebula暂时无法完成检查。':(error?.message||'ApiNebula暂时无法完成检查。');
  const reason=byKind||byStatus||fallback;
  const label=status?`HTTP ${status}`:({timeout:'请求超时',network:'网络错误'}[error?.kind]||'无状态码');
  const detail=String(error?.providerMessage||'')
    .replace(/sk-[A-Za-z0-9_-]{8,}/g,'[密钥已隐藏]').slice(0,300);
  return {status,label,reason,detail,attempts:Number(error?.attempts)||1};
}
async function telegram(env,method,payload) {
  if(!env.TELEGRAM_BOT_TOKEN) throw Error('未配置Telegram');
  return provider(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`,{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
}

// Poster PNGs are sent as documents (full resolution; photos would be recompressed).
async function telegramUpload(env,name,bytes,caption) {
  if(!env.TELEGRAM_BOT_TOKEN) throw Error('未配置Telegram');
  const form=new FormData();
  form.append('chat_id',String(env.TELEGRAM_OWNER_ID));
  form.append('caption',caption);
  form.append('document',new Blob([bytes],{type:'image/png'}),name);
  return provider(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendDocument`,{method:'POST',body:form},60000);
}
// Channel post: a real photo when Telegram accepts it (<=10MB, width+height<=10000, ratio<=20), otherwise the original PNG as a file.
function pngSize(bytes) {
  const sig=[137,80,78,71,13,10,26,10];
  if(!bytes || bytes.length<24 || sig.some((b,i)=>bytes[i]!==b)) return null;
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  return {w:view.getUint32(16),h:view.getUint32(20)};
}
export function photoOk(bytes) {
  const size=pngSize(bytes);
  if(!size || !size.w || !size.h || bytes.byteLength>10*1024*1024) return false;
  return size.w+size.h<=10000 && Math.max(size.w,size.h)/Math.min(size.w,size.h)<=20;
}
async function telegramChannelPhoto(env,chat,name,bytes,caption) {
  if(!env.TELEGRAM_BOT_TOKEN) throw Error('未配置Telegram');
  const asPhoto=photoOk(bytes);
  const form=new FormData();
  form.append('chat_id',String(chat));
  if(caption) form.append('caption',caption);
  form.append(asPhoto?'photo':'document',new Blob([bytes],{type:'image/png'}),name);
  return provider(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${asPhoto?'sendPhoto':'sendDocument'}`,{method:'POST',body:form},60000);
}
async function telegramDownload(env,fileId) {
  const info=await telegram(env,'getFile',{file_id:fileId});
  const path=info?.result?.file_path;
  if(!path) throw Error('Telegram没有返回文件路径');
  const response=await fetch(`https://api.telegram.org/file/bot${env.TELEGRAM_BOT_TOKEN}/${path}`,{signal:AbortSignal.timeout(60000)});
  if(!response.ok) throw Error('下载海报失败');
  return new Uint8Array(await response.arrayBuffer());
}
const DEFAULT_MODEL='claude-sonnet-5-5';
const draftPrompt=hotspots=>`北京时间日期：${beijingDay()}\n\n今日输入包（含Grok热点与可能存在的已验证交易快照）：\n${hotspots}`;
async function anthropicDraft(env,hotspots,system=DRAFT_SYSTEM,promptFn=draftPrompt) {
  if(!env.ANTHROPIC_API_KEY) throw Error('未配置Claude官方API');
  const model=env.ANTHROPIC_MODEL||DEFAULT_MODEL;
  const request={method:'POST',
    headers:{'x-api-key':env.ANTHROPIC_API_KEY,'anthropic-version':'2023-06-01','Content-Type':'application/json'},
    body:JSON.stringify({model,max_tokens:4000,system,
      messages:[{role:'user',content:promptFn(hotspots)}]})};
  const {answer,text}=await withRetry(async()=>{
    const result=await provider('https://api.anthropic.com/v1/messages',request,45000);
    const joined=(result.content||[]).filter(block=>block.type==='text').map(block=>block.text).join('').trim();
    if(!joined) throw Error('模型未返回草稿');
    return {answer:result,text:joined};
  });
  return {content:text,model,usage:answer.usage||null,truncated:answer.stop_reason==='max_tokens'};
}
const BEDROCK_MODEL='us.anthropic.claude-sonnet-5-5';
const bedrockUrl=env=>`https://bedrock-runtime.${env.BEDROCK_REGION||'us-east-2'}.amazonaws.com/model/${encodeURIComponent(env.BEDROCK_MODEL||BEDROCK_MODEL)}/converse`;
async function bedrockCall(env,body,timeoutMs) {
  return provider(bedrockUrl(env),{method:'POST',
    headers:{Authorization:`Bearer ${env.BEDROCK_API_KEY}`,'Content-Type':'application/json'},
    body:JSON.stringify(body)},timeoutMs);
}
async function bedrockDraft(env,hotspots,system=DRAFT_SYSTEM,promptFn=draftPrompt) {
  if(!env.BEDROCK_API_KEY) throw Error('未配置亚马逊Bedrock');
  const model=env.BEDROCK_MODEL||BEDROCK_MODEL;
  const body={system:[{text:system}],
    messages:[{role:'user',content:[{text:promptFn(hotspots)}]}],
    inferenceConfig:{maxTokens:4000}};
  const {answer,text}=await withRetry(async()=>{
    const result=await bedrockCall(env,body,70000);
    const joined=(result.output?.message?.content||[]).map(block=>block.text||'').join('').trim();
    if(!joined) throw Error('模型未返回草稿');
    return {answer:result,text:joined};
  });
  return {content:text,model,usage:answer.usage||null};
}
const GEMINI_MODEL='gemini-3.5-flash-lite';
async function geminiDraft(env,hotspots,system=DRAFT_SYSTEM,promptFn=draftPrompt) {
  if(!env.GEMINI_API_KEY) throw Error('未配置Gemini');
  const model=env.GEMINI_MODEL||GEMINI_MODEL;
  const request={method:'POST',headers:{'x-goog-api-key':env.GEMINI_API_KEY,'Content-Type':'application/json'},
    body:JSON.stringify({systemInstruction:{parts:[{text:system}]},
      contents:[{role:'user',parts:[{text:promptFn(hotspots)}]}],
      generationConfig:{maxOutputTokens:4000}})};
  const {answer,text}=await withRetry(async()=>{
    const result=await provider(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`,request,60000);
    const joined=(result.candidates?.[0]?.content?.parts||[]).filter(part=>!part.thought).map(part=>part.text||'').join('').trim();
    if(!joined) throw Error('模型未返回草稿');
    return {answer:result,text:joined};
  });
  return {content:text,model,usage:answer.usageMetadata||null};
}
// Explicit AI_PROVIDER wins. When unset, keep today's behaviour: ApiNebula if configured, else the official API.
function draftProvider(env) {
  if(['anthropic','apinebula','gemini','bedrock'].includes(env.AI_PROVIDER)) return env.AI_PROVIDER;
  if(env.APINEBULA_API_KEY) return 'apinebula';
  if(env.BEDROCK_API_KEY) return 'bedrock';
  if(env.ANTHROPIC_API_KEY) return 'anthropic';
  return 'apinebula';
}
async function generateDraft(env,hotspots,which,system=DRAFT_SYSTEM,promptFn=draftPrompt) {
  const make={anthropic:anthropicDraft,bedrock:bedrockDraft,gemini:geminiDraft}[which]||apiNebulaDraft;
  return {...await make(env,hotspots,system,promptFn),provider:which};
}
async function apiNebulaDraft(env,hotspots,system=DRAFT_SYSTEM,promptFn=draftPrompt) {
  if(!env.APINEBULA_API_KEY) throw Error('未配置ApiNebula');
  const model=env.APINEBULA_MODEL||DEFAULT_MODEL;
  const request={
    method:'POST',headers:{Authorization:`Bearer ${env.APINEBULA_API_KEY}`,'Content-Type':'application/json'},
    body:JSON.stringify({model,max_tokens:4000,
      messages:[{role:'system',content:system},{role:'user',content:promptFn(hotspots)}]})};
  // Drafting is slower than the 8-token connection check, so allow 45s per try and retry once on transient failures.
  const result=await withRetry(async()=>{
    const answer=await provider('https://apinebula.ai/v1/chat/completions',request,45000);
    const text=answer.choices?.[0]?.message?.content;
    if(typeof text!=='string' || !text.trim()) throw Error('模型未返回草稿');
    return answer;
  });
  const content=result.choices[0].message.content;
  return {content:content.trim(),model,usage:result.usage||null,truncated:result.choices[0].finish_reason==='length'};
}
export default {
  async fetch(request,env) {
    // One durable desk owns state; tokens and drafts have no public read route.
    return env.DESK.get(env.DESK.idFromName('uncleonchain')).fetch(request);
  }
};

export class TradeDesk {
  constructor(ctx,env) { this.ctx=ctx; this.env=env; }
  async fetch(request) {
    try { return await this.route(request); }
    catch { return json({ok:false,error:'操作未完成，请检查配置后重试。'},502); }
  }
  async effectiveEnv() {
    const saved=await this.ctx.storage.get('private-config');
    return {...this.env,...(saved?await unseal(saved,this.env.TOKEN_ENCRYPTION_KEY):{})};
  }
  flow(env) {
    return new Flow(this.ctx,env,{telegram,tgUpload:telegramUpload,tgChannelPhoto:telegramChannelPhoto,tgDownload:telegramDownload,provider,
      translate:(e,text,system,promptFn)=>generateDraft(e,text,draftProvider(e),system,promptFn)});
  }
  // Durable Object alarm: publish slots (zh 20:00 / en 21:00 Beijing) and the poster-job watchdog.
  async alarm() {
    await this.flow(await this.effectiveEnv()).alarm();
  }
  async jobs(request,url,path,env) {
    const flow=this.flow(env);
    const rest=path.slice('/pipeline/jobs/'.length).split('/');
    if(rest[0]==='next' && request.method==='GET')
      return json({ok:true,job:await flow.nextJob(url.searchParams.has('peek'))});
    const id=decodeURIComponent(rest[0]||''),action=rest[1];
    if(!/^(poster|ledger):\d{4}-\d{2}-\d{2}:\d+$/.test(id) || request.method!=='POST') return json({ok:false},404);
    if(action==='file') {
      if(Number(request.headers.get('Content-Length')||0)>16*1024*1024) return json({ok:false,error:'图片过大'},413);
      const name=url.searchParams.get('name')||'';
      const bytes=new Uint8Array(await request.arrayBuffer());
      const result=await flow.receiveFile(id,name,bytes);
      return json({ok:result.status===200,...result,status:undefined},result.status);
    }
    if(action==='complete') {
      if(Number(request.headers.get('Content-Length')||0)>200000) return json({ok:false},413);
      const result=await flow.completeJob(id,await request.json());
      return json({ok:result.status===200,...result,status:undefined},result.status);
    }
    return json({ok:false},404);
  }
  async route(request) {
    const url=new URL(request.url),path=url.pathname;
    const saved=await this.ctx.storage.get('private-config');
    const env={...this.env,...(saved?await unseal(saved,this.env.TOKEN_ENCRYPTION_KEY):{})};
    if(path==='/health' && request.method==='GET')
      return json({ok:true,service:'uncleonchain-trade-bot',mode:this.env.RUN_MODE||'live',
        publishing:this.env.PUBLISH_ENABLED==='true',
        targets:{daily:this.env.DAILY_TARGETS||'x',weekly:this.env.WEEKLY_TARGETS||'x',tg_channel:this.env.TG_CHANNEL||null}});
    if(path==='/' && request.method==='GET') {
      const callback=url.origin+'/oauth/x/callback';
      return page('链上大叔 · 热币交易后台',`<p>运行模式：${this.env.RUN_MODE==='live'?'正式环境':'测试环境'}。发布总开关：${this.env.PUBLISH_ENABLED==='true'?'已开启':'待端到端验证后开启'}。</p><p>X设置页的 Callback URI / Redirect URL：</p><p><code>${escapeHTML(callback)}</code></p><p><a href="/admin">进入管理页</a></p>`);
    }
    if(path==='/oauth/x/callback' && request.method==='GET') return this.callback(request,url,env);
    if(path==='/telegram/webhook' && request.method==='POST') {
      if(!await matches(request.headers.get('X-Telegram-Bot-Api-Secret-Token'),env.TELEGRAM_WEBHOOK_SECRET))
        return json({ok:false},403);
      if(Number(request.headers.get('Content-Length')||0)>200000) return json({ok:false},413);
      return this.inbox(await request.json(),env);
    }
    if(path==='/pipeline/snapshot' && request.method==='POST') {
      const auth=request.headers.get('Authorization')||'';
      if(!env.PIPELINE_SECRET || !auth.startsWith('Bearer ') || !await matches(auth.slice(7),env.PIPELINE_SECRET))
        return json({ok:false},403);
      if(Number(request.headers.get('Content-Length')||0)>1500000) return json({ok:false,error:'快照过大'},413);
      const snapshot=await request.json();
      const day=beijingDay();
      if(snapshot.day!==day || snapshot.report?.ok!==true || typeof snapshot.summary!=='object')
        return json({ok:false,error:'快照日期或验证报告无效'},400);
      const finished=Date.parse(snapshot.report.finished_at||'');
      if(!Number.isFinite(finished) || Math.abs(Date.now()-finished)>3*3600000)
        return json({ok:false,error:'快照超过3小时'},409);
      const metrics=snapshot.summary?.['指标'];
      if(!metrics || !['BTC','ETH','SOL','XRP','BNB'].every(symbol=>metrics[symbol]))
        return json({ok:false,error:'核心币数据不完整'},400);
      await this.ctx.storage.put('snapshot:'+day,{...snapshot,received_at:Date.now()});
      return json({ok:true,day,received_at:Date.now()});
    }
    if(path==='/pipeline/weekly' && request.method==='POST') {
      const auth=request.headers.get('Authorization')||'';
      if(!env.PIPELINE_SECRET || !auth.startsWith('Bearer ') || !await matches(auth.slice(7),env.PIPELINE_SECRET))
        return json({ok:false},403);
      if(Number(request.headers.get('Content-Length')||0)>400000) return json({ok:false,error:'数据包过大'},413);
      const body=await request.json();
      const day=beijingDay();
      // The collector reports its own failure here so the reviewer hears about it instead of waiting for a post that will not come.
      if(typeof body.error==='string' && body.day===day) {
        if(!env.TELEGRAM_OWNER_ID) return json({ok:false,error:'未配置Telegram审核人'},409);
        await telegram(env,'sendMessage',{chat_id:Number(env.TELEGRAM_OWNER_ID),
          text:`周日下周清单没有生成，今天不会有送审稿。原因：${body.error.slice(0,500)}\n处理好后，到 GitHub 的 Actions 里手动运行「周日下周清单」即可补上。`});
        return json({ok:true,reported:true});
      }
      const pack=body.pack;
      if(body.day!==day || pack?.kind!=='weekly' || pack.day!==day) return json({ok:false,error:'日期不是今天，或数据包格式不对'},400);
      if(typeof body.zh_post!=='string' || typeof body.en_post!=='string' || !body.zh_post.startsWith('【$BTC下周清单】')
        || !body.en_post.startsWith('【$BTC Week Ahead】') || /[\u4e00-\u9fff]/.test(body.en_post))
        return json({ok:false,error:'帖子文字不符合格式（标题或英文稿含中文）'},400);
      if(!env.TELEGRAM_OWNER_ID) return json({ok:false,error:'未配置Telegram审核人'},409);
      const result=await this.flow(env).startWeekly(day,{pack,zh_post:body.zh_post.trim(),en_post:body.en_post.trim()});
      return json({ok:result.status===200,...result,status:undefined},result.status);
    }
    if(path.startsWith('/pipeline/jobs/')) {
      const auth=request.headers.get('Authorization')||'';
      if(!env.PIPELINE_SECRET || !auth.startsWith('Bearer ') || !await matches(auth.slice(7),env.PIPELINE_SECRET))
        return json({ok:false},403);
      return this.jobs(request,url,path,env);
    }
    if(path.startsWith('/admin')) {
      if(!await isAdmin(request,env.ADMIN_PASSWORD))
        return page('后台登录','<p>请使用管理员凭据登录。</p>',401,{'WWW-Authenticate':'Basic realm="Trade Desk", charset="UTF-8"'});
      if(request.method==='POST' && !await validAdminPost(request,env.ADMIN_PASSWORD))
        return page('请刷新后台页面后重试','<p>表单来源或有效期校验未通过。请重新打开后台设置页，再填写并保存。</p><p><a href="/admin">返回后台设置</a></p>',403);
      if(path==='/admin' && request.method==='GET') return this.admin(url,env);
      if(path==='/admin/config' && request.method==='POST') {
        const raw=await request.text();
        if(raw.length>20000) return json({ok:false},413);
        const form=new URLSearchParams(raw),config={};
        for(const name of ['X_CLIENT_ID','X_CLIENT_SECRET','TELEGRAM_BOT_TOKEN','TELEGRAM_OWNER_ID','GEMINI_API_KEY','GEMINI_MODEL','ANTHROPIC_API_KEY','ANTHROPIC_MODEL','APINEBULA_API_KEY','APINEBULA_MODEL','BEDROCK_API_KEY','BEDROCK_REGION','BEDROCK_MODEL','AI_PROVIDER','GITHUB_DISPATCH_TOKEN','GITHUB_REPO']) {
          const value=form.get(name)?.trim();
          if(value) {
            if(value.length>2000 || /[\r\n]/.test(value)) return json({ok:false,error:'字段格式有误'},400);
            if(name==='TELEGRAM_OWNER_ID' && !/^\d+$/.test(value)) return json({ok:false,error:'审核人ID应为数字'},400);
            if(name==='AI_PROVIDER' && !['anthropic','apinebula','gemini','bedrock'].includes(value)) return json({ok:false,error:'模型供应商无效'},400);
            if(name==='ANTHROPIC_MODEL' && !/^claude-[a-z0-9-]+$/.test(value)) return json({ok:false,error:'Claude模型名格式有误'},400);
            if(name==='BEDROCK_REGION' && !/^[a-z]{2}-[a-z]+-\d$/.test(value)) return json({ok:false,error:'Bedrock区域格式有误，例如 us-east-2'},400);
            if(name==='BEDROCK_MODEL' && !/^[a-zA-Z0-9._:-]+$/.test(value)) return json({ok:false,error:'Bedrock模型编号格式有误'},400);
            if(name==='GITHUB_REPO' && !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(value)) return json({ok:false,error:'仓库名格式应为 owner/repo'},400);
            if(name==='GEMINI_MODEL' && !/^[a-zA-Z0-9._-]+$/.test(value)) return json({ok:false,error:'Gemini模型名格式有误'},400);
            if(name==='APINEBULA_MODEL' && !/^[a-zA-Z0-9._:-]+$/.test(value)) return json({ok:false,error:'ApiNebula模型名格式有误'},400);
            config[name]=value;
          }
        }
        if(!Object.keys(config).length) return page('没有修改','<p>请至少填写一项。</p><a href="/admin">返回</a>',400);
        await this.ctx.storage.transaction(async tx=>{
          const previous=await tx.get('private-config');
          const existing=previous?await unseal(previous,this.env.TOKEN_ENCRYPTION_KEY):{};
          await tx.put('private-config',await seal({...existing,...config},this.env.TOKEN_ENCRYPTION_KEY));
        });
        return new Response(null,{status:303,headers:{...HEADERS,Location:'/admin'}});
      }
      if(path==='/admin/x/connect' && request.method==='POST') return this.connect(url,env);
      if(path==='/admin/draft/run' && request.method==='POST') {
        if(!env.TELEGRAM_OWNER_ID) return json({ok:false,error:'未配置Telegram审核人'},409);
        return this.makeDraft(Number(env.TELEGRAM_OWNER_ID),env,true);
      }
      if(path==='/admin/draft/status' && request.method==='GET') {
        const draft=await this.ctx.storage.get('draft:'+beijingDay());
        return json(draft?{ok:true,status:draft.status,model:draft.model||null,
          http_status:draft.http_status||null,failure:draft.failure||null,prompt_chars:draft.prompt_chars||null,detail:draft.detail||null}:{ok:true,status:'missing'});
      }
      if(path==='/admin/gemini/check' && request.method==='POST') {
        if(!env.GEMINI_API_KEY) return page('请先保存Gemini密钥','<a href="/admin">返回后台</a>',409);
        const result=await provider('https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000',{
          headers:{'x-goog-api-key':env.GEMINI_API_KEY}});
        const available=result.models?.some(m=>m.name==='models/gemini-3.5-flash-lite' &&
          m.supportedGenerationMethods?.includes('generateContent'));
        return page(available?'Gemini密钥检查通过':'当前密钥未找到预设模型',
          `<p>${available?'已确认可访问模型列表；实际免费生成额度仍以首次运行验证为准。':'请检查Google AI Studio的可用模型和地区。'}</p><p><a href="/admin">返回后台</a></p>`,available?200:409);
      }
      if(path==='/admin/anthropic/check' && request.method==='POST') {
        if(!env.ANTHROPIC_API_KEY) return page('请先保存Claude API密钥','<a href="/admin">返回后台</a>',409);
        const model=env.ANTHROPIC_MODEL||DEFAULT_MODEL;
        let result;
        try {
          result=await provider(`https://api.anthropic.com/v1/models/${encodeURIComponent(model)}`,{
            headers:{'x-api-key':env.ANTHROPIC_API_KEY,'anthropic-version':'2023-06-01'}});
        } catch(error) {
          const failure=anthropicFailure(error);
          return page('Claude连接检查未通过',`<p>${escapeHTML(failure.reason)}</p><p>状态：<code>${escapeHTML(failure.label)}</code></p><p>模型：<code>${escapeHTML(model)}</code></p>${failure.detail?`<p>平台返回：<code>${escapeHTML(failure.detail)}</code></p>`:''}<p><a href="/admin">返回后台</a></p>`,409);
        }
        const available=result.id===model;
        return page(available?'Claude连接检查通过':'Claude模型不可用',
          `<p>${available?`已确认API密钥可以访问 ${escapeHTML(model)}；本次检查没有生成正文。`:'请检查API额度、地区权限和模型名。'}</p><p><a href="/admin">返回后台</a></p>`,available?200:409);
      }
      if(path==='/admin/bedrock/check' && request.method==='POST') {
        if(!env.BEDROCK_API_KEY) return page('请先保存Bedrock API Key','<a href="/admin">返回后台</a>',409);
        const model=env.BEDROCK_MODEL||BEDROCK_MODEL;
        try {
          await bedrockCall(env,{messages:[{role:'user',content:[{text:'ping'}]}],inferenceConfig:{maxTokens:16}},30000);
        } catch(error) {
          const failure=bedrockFailure(error);
          return page('Bedrock连接检查未通过',`<p>${escapeHTML(failure.reason)}</p><p>状态：<code>${escapeHTML(failure.label)}</code></p><p>区域：<code>${escapeHTML(env.BEDROCK_REGION||'us-east-2')}</code> · 模型：<code>${escapeHTML(model)}</code></p>${failure.detail?`<p>平台返回：<code>${escapeHTML(failure.detail)}</code></p>`:''}<p><a href="/admin">返回后台</a></p>`,409);
        }
        return page('Bedrock连接检查通过',`<p>已确认这把Key能在 ${escapeHTML(env.BEDROCK_REGION||'us-east-2')} 调用 ${escapeHTML(model)}（仅发送了一个很短的测试请求）。</p><p><a href="/admin">返回后台</a></p>`);
      }
      if(path==='/admin/apinebula/check' && request.method==='POST') {
        if(!env.APINEBULA_API_KEY) return page('请先保存ApiNebula令牌','<a href="/admin">返回后台</a>',409);
        const model=env.APINEBULA_MODEL||DEFAULT_MODEL;
        try {
          const result=await provider('https://apinebula.ai/v1/chat/completions',{
            method:'POST',headers:{Authorization:`Bearer ${env.APINEBULA_API_KEY}`,
              'Content-Type':'application/json'},
            body:JSON.stringify({model,max_tokens:8,messages:[{role:'user',content:'Reply OK'}]})});
          if(!result.id && !result.choices) throw Error('响应格式不兼容');
        } catch(error) {
          const failure=apiNebulaFailure(error);
          return page('ApiNebula连接检查未通过',`<p>${escapeHTML(failure.reason)}</p><p>状态：<code>${escapeHTML(failure.label)}</code></p><p>模型：<code>${escapeHTML(model)}</code></p>${failure.detail?`<p>平台返回：<code>${escapeHTML(failure.detail)}</code></p>`:''}<p><a href="/admin">返回后台</a></p>`,409);
        }
        return page('ApiNebula连接检查通过',`<p>已确认令牌、OpenAI兼容文本接口和Claude模型可以调用。</p><p>模型：<code>${escapeHTML(model)}</code></p><p>本次最多生成8个token。</p><p><a href="/admin">返回后台</a></p>`);
      }
      if(path==='/admin/telegram/connect' && request.method==='POST') {
        if(!env.TELEGRAM_WEBHOOK_SECRET) return json({ok:false,error:'缺少Webhook密钥'},409);
        await telegram(env,'setWebhook',{url:url.origin+'/telegram/webhook',
          secret_token:env.TELEGRAM_WEBHOOK_SECRET,allowed_updates:['message','callback_query']});
        return page('Telegram接入完成','<p>现在打开你的机器人，发送 /start。</p><p><a href="/admin">返回管理页</a></p>');
      }
    }
    return json({ok:false,error:'Not found'},404);
  }
  async admin(url,env) {
    const account=await this.ctx.storage.get('x-account');
    const hidden=`<input type="hidden" name="_csrf" value="${await adminFormToken(env.ADMIN_PASSWORD,url.origin)}">`;
    const ready=Boolean(env.X_CLIENT_ID && env.X_CLIENT_SECRET && env.TOKEN_ENCRYPTION_KEY);
    const fields=[['X_CLIENT_ID','X Client ID','text'],['X_CLIENT_SECRET','X Client Secret','password'],
      ['TELEGRAM_BOT_TOKEN','Telegram Bot Token','password'],['TELEGRAM_OWNER_ID','Telegram审核人ID（接通机器人后获取）','text'],
      ['GEMINI_API_KEY','Gemini API Key','password'],
      ['GEMINI_MODEL','Gemini模型名（默认 gemini-3.5-flash-lite）','text'],
      ['ANTHROPIC_API_KEY','Claude官方 API Key','password'],
      ['ANTHROPIC_MODEL','Claude官方模型名（默认 claude-sonnet-5-5）','text'],
      ['BEDROCK_API_KEY','亚马逊Bedrock API Key（长期）','password'],
      ['BEDROCK_REGION','Bedrock区域（默认 us-east-2）','text'],
      ['BEDROCK_MODEL','Bedrock模型编号（默认 us.anthropic.claude-sonnet-5-5）','text'],
      ['GITHUB_DISPATCH_TOKEN','（选填）GitHub令牌：让海报任务立即启动而不是等5分钟轮询（需 Actions 读写权限）','password'],
      ['GITHUB_REPO','（选填）GitHub仓库 owner/repo（默认 ivonasmith/hotcoin-trade-automation）','text'],
      ['APINEBULA_API_KEY','ApiNebula API令牌','password'],
      ['APINEBULA_MODEL','ApiNebula模型名（按模型广场填写）','text']]
      .map(([name,label,type])=>`<p><label>${label}<br><input style="width:95%;font:inherit;padding:8px" name="${name}" type="${type}" autocomplete="off" placeholder="${env[name]?'已保存；留空保持':'待填写'}"></label></p>`).join('');
    const selected=env.AI_PROVIDER||'anthropic';
    const providerName={anthropic:'Claude官方',bedrock:'亚马逊Bedrock',apinebula:'ApiNebula',gemini:'Gemini'}[selected];
    const providerField=`<p><label>首选撰稿模型<br><select style="width:98%;font:inherit;padding:8px" name="AI_PROVIDER"><option value="apinebula" ${selected==='apinebula'?'selected':''}>ApiNebula（Claude兼容）</option><option value="anthropic" ${selected==='anthropic'?'selected':''}>Claude官方</option><option value="bedrock" ${selected==='bedrock'?'selected':''}>亚马逊Bedrock</option><option value="gemini" ${selected==='gemini'?'selected':''}>Gemini</option></select></label></p>`;
    return page('热币交易 · 后台设置',`<p>运行模式：<b>${env.RUN_MODE==='live'?'正式环境':'测试环境'}</b>；发布总开关：<b>${env.PUBLISH_ENABLED==='true'?'已开启':'待端到端验证后开启'}</b></p><ul><li>X应用配置：${ready?'已填写':'待填写 X_CLIENT_ID、X_CLIENT_SECRET'}</li><li>X账号：${account?escapeHTML('@'+account.username):'未授权'}</li><li>Telegram Token：${env.TELEGRAM_BOT_TOKEN?'已填写':'待填写'}</li><li>Telegram审核人：${env.TELEGRAM_OWNER_ID?'已指定':'待指定'}</li><li>首选撰稿模型：${providerName}</li><li>ApiNebula：${env.APINEBULA_API_KEY?'已填写':'待填写'} · ${escapeHTML(env.APINEBULA_MODEL||DEFAULT_MODEL)}</li><li>Claude官方 API：${env.ANTHROPIC_API_KEY?'已填写':'待填写'} · ${escapeHTML(env.ANTHROPIC_MODEL||DEFAULT_MODEL)}</li><li>亚马逊Bedrock：${env.BEDROCK_API_KEY?'已填写':'待填写'} · ${escapeHTML(env.BEDROCK_REGION||'us-east-2')} · ${escapeHTML(env.BEDROCK_MODEL||BEDROCK_MODEL)}</li><li>Gemini API：${env.GEMINI_API_KEY?'已填写':'待填写'}</li><li>今日：${escapeHTML(beijingDay())} · ${escapeHTML(eligible(beijingDay()).reason)}</li></ul><p>Callback URL：<code>${escapeHTML(url.origin+'/oauth/x/callback')}</code></p><h2>填写应用凭据</h2><p>只存入你自己的Cloudflare后台，保存后不回显。留空保留原值。</p><form method="post" action="/admin/config">${hidden}${providerField}${fields}<button>加密保存</button></form><h2>连接账号</h2><form method="post" action="/admin/x/connect">${hidden}<button ${ready?'':'disabled'}>授权 @Uncle_Onchain</button></form><p></p><form method="post" action="/admin/telegram/connect">${hidden}<button ${env.TELEGRAM_BOT_TOKEN?'':'disabled'}>接通Telegram机器人</button></form><p></p><form method="post" action="/admin/apinebula/check">${hidden}<button ${env.APINEBULA_API_KEY?'':'disabled'}>检查ApiNebula连接</button></form><p></p><form method="post" action="/admin/anthropic/check">${hidden}<button ${env.ANTHROPIC_API_KEY?'':'disabled'}>检查Claude官方连接</button></form><p></p><form method="post" action="/admin/bedrock/check">${hidden}<button ${env.BEDROCK_API_KEY?'':'disabled'}>检查Bedrock连接</button></form><p></p><form method="post" action="/admin/gemini/check">${hidden}<button ${env.GEMINI_API_KEY?'':'disabled'}>检查Gemini连接</button></form>`,200,{'Referrer-Policy':'same-origin'});
  }
  async connect(url,env) {
    if(!env.X_CLIENT_ID || !env.X_CLIENT_SECRET || !env.TOKEN_ENCRYPTION_KEY)
      return json({ok:false,error:'请先配置X应用凭据'},409);
    const state=randomToken(),verifier=randomToken();
    // Only one pending authorization is allowed. New attempt replaces old one.
    await this.ctx.storage.put('oauth-pending',{state,verifier,expires:Date.now()+10*60000,origin:url.origin});
    const target=new URL('https://x.com/i/oauth2/authorize');
    target.search=new URLSearchParams({response_type:'code',client_id:env.X_CLIENT_ID,
      redirect_uri:url.origin+'/oauth/x/callback',scope:SCOPES,state,
      code_challenge:await sha256(verifier),code_challenge_method:'S256'}).toString();
    return new Response(null,{status:303,headers:{...HEADERS,Location:target.toString(),
      'Set-Cookie':`desk_oauth=${state}; HttpOnly; Secure; SameSite=Lax; Path=/oauth/x; Max-Age=600`}});
  }
  async callback(request,url,env) {
    const state=url.searchParams.get('state'),cookie=csrfCookie(request);
    const pending=await this.ctx.storage.transaction(async tx=>{
      const p=await tx.get('oauth-pending');
      if(!p || p.expires<Date.now() || p.origin!==url.origin ||
        !await matches(p.state,state) || !await matches(p.state,cookie)) return null;
      await tx.delete('oauth-pending'); return p;
    });
    if(!pending) return page('授权已失效','<p>请回管理页重新开始授权。</p>',400);
    if(url.searchParams.has('error') || !url.searchParams.get('code'))
      return page('授权未完成','<p>没有获得账号权限，可回管理页重试。</p>',400);
    const tokens=await provider('https://api.x.com/2/oauth2/token',{
      method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded',
        Authorization:'Basic '+btoa(`${env.X_CLIENT_ID}:${env.X_CLIENT_SECRET}`)},
      body:new URLSearchParams({grant_type:'authorization_code',code:url.searchParams.get('code'),
        redirect_uri:pending.origin+'/oauth/x/callback',code_verifier:pending.verifier}).toString()});
    if(!tokens.access_token || !tokens.refresh_token) throw Error('缺少可续期授权');
    const me=await provider('https://api.x.com/2/users/me',{
      headers:{Authorization:'Bearer '+tokens.access_token}});
    if(me.data?.username?.toLowerCase()!==env.EXPECTED_X_USERNAME.toLowerCase())
      return page('账号不符','<p>请用 @Uncle_Onchain 登录X后重新授权。</p>',403);
    await this.ctx.storage.put('x-tokens',await seal({...tokens,obtained_at:Date.now()},env.TOKEN_ENCRYPTION_KEY));
    await this.ctx.storage.put('x-account',{id:me.data.id,username:me.data.username});
    return page('X授权完成',`<p>@${escapeHTML(me.data.username)} 已连接，尚未发布任何推文。</p><p><a href="/admin">返回管理页</a></p>`,200,
      {'Set-Cookie':'desk_oauth=; HttpOnly; Secure; SameSite=Lax; Path=/oauth/x; Max-Age=0'});
  }
  // Read-only: is the bot an admin of the channel and allowed to post? Posts nothing.
  async channelCheck(env,reply) {
    const channel=env.TG_CHANNEL;
    if(!channel) { await reply('还没有配置 TG_CHANNEL（例如 @UncleOnchain），频道发布没有目标。'); return; }
    try {
      const me=(await telegram(env,'getMe',{}))?.result;
      const chat=(await telegram(env,'getChat',{chat_id:channel}))?.result;
      const member=(await telegram(env,'getChatMember',{chat_id:channel,user_id:me.id}))?.result;
      const canPost=member?.status==='creator' || (member?.status==='administrator' && member.can_post_messages!==false);
      await reply(`频道：${chat?.title||channel}（${chat?.type||'?'}）\n机器人：@${me?.username}，身份：${member?.status||'未知'}${member?.status==='administrator'?`，发布消息权限：${member.can_post_messages===false?'没有':'有'}`:''}\n结论：${canPost?'可以发帖':'不能发帖——把机器人设为管理员并勾选「发布消息」'}\n目标设置：日常=${env.DAILY_TARGETS||'x'}，周日=${env.WEEKLY_TARGETS||'x'}，频道语言=${env.TG_CHANNEL_LANGS||'zh'}，总开关=${env.PUBLISH_ENABLED==='true'?'开':'关'}`);
    } catch(error) {
      await reply(`频道检查没通过：${tgErrorReason(error)}`);
    }
  }
  async inbox(update,env) {
    if(update.callback_query) return this.callbackQuery(update.callback_query,env);
    const m=update.message;
    if(!m || m.chat?.type!=='private' || m.from?.is_bot) return json({ok:true});
    const actor=String(m.from.id),chat=m.chat.id,text=(m.text||'').trim();
    const reply=text=>telegram(env,'sendMessage',{chat_id:chat,text});
    if(!env.TELEGRAM_OWNER_ID) {
      if(text==='/start') await reply(`机器人已接通。你的审核人ID为：${actor}。请将此ID填入后台的 TELEGRAM_OWNER_ID，然后开始使用。`);
      return json({ok:true});
    }
    if(actor!==String(env.TELEGRAM_OWNER_ID)) return json({ok:true});
    if(text==='/start') {
      await reply('发送 /hotspots 后粘贴今日Grok热点（可分多条），最后发送 /done。系统会调用Claude生成送审稿，但不会自动发推。');
      return json({ok:true});
    }
    if(text==='/status') {
      const day=beijingDay(),draft=await this.ctx.storage.get('draft:'+day);
      const flow=await this.ctx.storage.get('flow:'+day);
      const slots=flow?.slots?Object.entries(flow.slots).map(([lang,slot])=>`${lang==='zh'?'中文':'英文'}：${slot.status}`).join('，'):'';
      if(!draft && flow?.kind==='weekly') {
        const wslots=flow.slots?Object.entries(flow.slots).map(([lang,slot])=>`${lang==='zh'?'中文':'英文'}：${slot.status}`).join('，'):'';
        await reply(`周日下周清单：${flow.status}${flow.failure?'；原因：'+flow.failure:''}${wslots?'\n发布安排：'+wslots:''}`);
        return json({ok:true});
      }
      await reply(draft?`今日送审稿状态：${draft.status}；模型：${draft.model}${flow?`\n正式流程：${flow.status}${flow.issue?`（第${flow.issue}期）`:''}${flow.failure?'；原因：'+flow.failure:''}${slots?'\n发布安排：'+slots:''}${flow.ledger_status?'\n台账：'+flow.ledger_status:''}`:''}`:'今天还没有送审稿。');
      return json({ok:true});
    }
    if(text==='/reissue') {
      await this.flow(env).reissue(beijingDay());
      return json({ok:true});
    }
    if(text==='/channelcheck') {
      await this.channelCheck(env,reply);
      return json({ok:true});
    }
    if(text==='/retrypost') {
      await this.flow(env).retryPost(beijingDay());
      return json({ok:true});
    }
    if(text==='/postnow') {
      await this.flow(env).postNow(beijingDay());
      return json({ok:true});
    }
    if(text==='/draft') return this.makeDraft(chat,env);
    let response;
    await this.ctx.storage.transaction(async tx=>{
      if(await tx.get('tg-update:'+update.update_id)) return;
      await tx.put('tg-update:'+update.update_id,Date.now());
      const day=beijingDay(),key='hotspots-working',working=await tx.get(key);
      if(text==='/hotspots') {
        await tx.put(key,{day,started:Date.now(),parts:[]});
        response='请发送今日热点，可分多条。发完后发送 /done。'; return;
      }
      if(text==='/cancel') { await tx.delete(key);response='已取消本次输入。';return; }
      if(!working || working.day!==day || Date.now()-working.started>2*3600000) {
        response='请先发送 /hotspots 开始今日输入。';return;
      }
      if(text==='/done') {
        if(!working.parts.length) {response='还没有收到热点正文。';return;}
        await tx.put('hotspots:'+day,{...working,submitted:Date.now()});
        await tx.delete(key);
        response='今日热点已保存，正在生成送审稿。';return;
      }
      if(!text) {response='当前请粘贴文字；文件接收稍后接入。';return;}
      if(text.startsWith('/')) {response='可用命令：/hotspots、/done、/cancel。';return;}
      if(working.parts.join('').length+text.length>50000) {response='内容超过5万字，请精简后重新提交。';return;}
      working.parts.push(text); await tx.put(key,working);
      response=`已收到第${working.parts.length}段；发完请发送 /done。`;
    });
    if(response) await reply(response);
    if(text==='/done' && response==='今日热点已保存，正在生成送审稿。') await this.makeDraft(chat,env);
    return json({ok:true});
  }
  async makeDraft(chat,env,force=false) {
    const day=beijingDay(),saved=await this.ctx.storage.get('hotspots:'+day);
    if(!saved?.parts?.length) {
      await telegram(env,'sendMessage',{chat_id:chat,text:'今天还没有热点。请先发送 /hotspots，粘贴内容后发送 /done。'});
      return json({ok:true});
    }
    const locked=await this.ctx.storage.get('flow:'+day);
    if(locked && ['final_approved'].includes(locked.status)) {
      // Everything already published or skipped: shelve it and let the same date carry the next issue.
      if(!(await this.flow(env).archiveDay(day))) {
        const state=Object.entries(locked.slots||{}).map(([lang,slot])=>`${lang==='zh'?'中文':'英文'}：${slot.status}`).join('，');
        await telegram(env,'sendMessage',{chat_id:chat,text:`今天已通过的那期还没发完（${state||'没有排期'}），不能再重写。发完或处理完后再发 /hotspots；失败的先发 /retrypost。`});
        return json({ok:true});
      }
    } else if(locked) await this.ctx.storage.delete('flow:'+day); // an unfinished flow belongs to the old draft
    const existing=await this.ctx.storage.get('draft:'+day);
    if(existing?.status==='generating' && !force) {
      await telegram(env,'sendMessage',{chat_id:chat,text:'今日送审稿正在生成，请稍候。'});
      return json({ok:true});
    }
    await this.ctx.storage.put('draft:'+day,{status:'generating',started:Date.now()});
    let promptChars=0,which=draftProvider(env);
    try {
      const snapshot=await this.ctx.storage.get('snapshot:'+day);
      const compact=snapshot?compactSnapshot(snapshot):null;
      promptChars=compact?.chars||0;
      const verified=compact?`\n\n已通过程序新鲜度校验的当日交易快照${compact.trimmed?`（体积已压缩：长列表只保留前${compact.items}项，被省略的内容不得推测）`:''}：\n${compact.body}`:
        '\n\n当日交易快照：尚未上传。禁止生成交易动作。';
      const hotspots=saved.parts.join('\n\n');
      const plan=buildOrderPlan(snapshot,day);
      which=draftProvider(env);
      const generated=await generateDraft(env,hotspots+verified+'\n\n'+orderBlock(plan),which);
      // Deterministic safety nets: $ prefix on coins, and a format check the reviewer can see.
      const content=normalizeTickers(generated.content,collectTickers(snapshot,hotspots));
      const lint=lintDraft(content,plan,hotspots);
      if(generated.truncated) lint.push('稿件在中途被截断，送审备注可能不完整，请退回重写');
      if(!content.includes('送审备注：')) lint.push('稿件缺少送审备注');
      const draft={...generated,content,lint,order:plan.order,order_allowed:plan.allowed,status:'pending_text_review',created:Date.now(),day};
      await this.ctx.storage.put('draft:'+day,draft);
      const warning=lint.length?`\n\n⚠ 格式自检：${lint.join('；')}。可点“退回重写”。`:'';
      await telegram(env,'sendMessage',{chat_id:chat,text:`${draft.content}${warning}\n\n——\n状态：等待文字审核｜模型：${draft.model}`,
        reply_markup:{inline_keyboard:[[
          {text:'✅ 文字通过',callback_data:`text_ok:${day}`},
          {text:'↩️ 退回重写',callback_data:`text_reject:${day}`}
        ]]}});
    } catch(error) {
      const failure=({anthropic:anthropicFailure,bedrock:bedrockFailure,gemini:geminiFailure}[which]||apiNebulaFailure)(error);
      await this.ctx.storage.put('draft:'+day,{status:'generation_failed',failed:Date.now(),provider:which,
        model:(which==='anthropic'?env.ANTHROPIC_MODEL:which==='bedrock'?(env.BEDROCK_MODEL||BEDROCK_MODEL):which==='gemini'?(env.GEMINI_MODEL||GEMINI_MODEL):env.APINEBULA_MODEL)||DEFAULT_MODEL,http_status:failure.status,failure:failure.label,prompt_chars:promptChars,detail:failure.detail||failure.reason});
      await telegram(env,'sendMessage',{chat_id:chat,text:`送审稿生成失败，素材已保留。原因：${failure.reason}${failure.detail?'平台返回：'+failure.detail:''}（${failure.label}）。${failure.attempts>1?'已自动重试过一次；':''}可发送 /draft 再试。`});
    }
    return json({ok:true});
  }
  async callbackQuery(query,env) {
    const actor=String(query.from?.id||''),chat=query.message?.chat?.id;
    if(!env.TELEGRAM_OWNER_ID || actor!==String(env.TELEGRAM_OWNER_ID) || !chat) return json({ok:true});
    const [action,day,extra]=String(query.data||'').split(':');
    if(!/^\d{4}-\d{2}-\d{2}$/.test(day||'')) return json({ok:true});
    // Telegram may redeliver a webhook; one callback id is handled once.
    const fresh=await this.ctx.storage.transaction(async tx=>{
      if(await tx.get('tg-cb:'+query.id)) return false;
      await tx.put('tg-cb:'+query.id,Date.now());
      return true;
    });
    const answer=text=>telegram(env,'answerCallbackQuery',{callback_query_id:query.id,text}).catch(()=>{});
    if(!fresh) return json({ok:true});
    const flow=this.flow(env);
    const draft=await this.ctx.storage.get('draft:'+day);
    // The Sunday week-ahead has no text draft; only the text-review actions need one.
    const weekly=(await this.ctx.storage.get('flow:'+day))?.kind==='weekly';
    if(!draft && !weekly) { await answer('找不到这份稿件'); return json({ok:true}); }
    const move=(from,patch)=>this.ctx.storage.transaction(async tx=>{
      const current=await tx.get('draft:'+day);
      if(!current || current.status!==from) return false;
      await tx.put('draft:'+day,{...current,...patch});
      return true;
    });
    if(action==='text_ok') {
      if(await move('pending_text_review',{status:'text_approved',approved:Date.now()})) {
        await answer('文字稿已通过');
        await flow.startFormal(day);
      } else await answer('该稿件已经处理');
    } else if(action==='text_reject') {
      if(await move('pending_text_review',{status:'text_rejected',rejected:Date.now()})) {
        await answer('已退回');
        await telegram(env,'sendMessage',{chat_id:chat,text:'已退回。发送修改要求后再发送 /draft；当前版本不会发推。'});
      } else await answer('该稿件已经处理');
    } else if(action==='formal_retry' && draft?.status==='text_approved') {
      await answer('重新处理中'); await flow.startFormal(day);
    } else if(action==='poster_retry') {
      await answer('重新排队'); await flow.retryPoster(day);
    } else if(action==='final_ok') {
      await answer(await flow.finalApprove(day,extra)?'已通过':'该稿件已经处理');
    } else if(action==='final_reject') {
      await answer(await flow.finalReject(day,extra)?'已退回':'该稿件已经处理');
    } else if(action==='late_post' || action==='late_skip') {
      await answer(action==='late_post'?'处理中':'已记下');
      await flow.lateDecision(day,extra,action==='late_post');
    } else await answer('该稿件已经处理');
    return json({ok:true});
  }
}
