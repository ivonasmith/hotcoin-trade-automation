import {beijingDay,eligible} from '../calendar.mjs';
import {compactSnapshot} from './snapshot.mjs';
import {DRAFT_SYSTEM,collectTickers,normalizeTickers,lintDraft} from './draft-style.mjs';
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
const DEFAULT_MODEL='claude-sonnet-5-5';
const draftPrompt=hotspots=>`北京时间日期：${beijingDay()}\n\n今日输入包（含Grok热点与可能存在的已验证交易快照）：\n${hotspots}`;
async function anthropicDraft(env,hotspots) {
  if(!env.ANTHROPIC_API_KEY) throw Error('未配置Claude官方API');
  const model=env.ANTHROPIC_MODEL||DEFAULT_MODEL;
  const request={method:'POST',
    headers:{'x-api-key':env.ANTHROPIC_API_KEY,'anthropic-version':'2023-06-01','Content-Type':'application/json'},
    body:JSON.stringify({model,max_tokens:1800,system:DRAFT_SYSTEM,
      messages:[{role:'user',content:draftPrompt(hotspots)}]})};
  const {answer,text}=await withRetry(async()=>{
    const result=await provider('https://api.anthropic.com/v1/messages',request,45000);
    const joined=(result.content||[]).filter(block=>block.type==='text').map(block=>block.text).join('').trim();
    if(!joined) throw Error('模型未返回草稿');
    return {answer:result,text:joined};
  });
  return {content:text,model,usage:answer.usage||null};
}
// Explicit AI_PROVIDER wins. When unset, keep today's behaviour: ApiNebula if configured, else the official API.
function draftProvider(env) {
  if(['anthropic','apinebula','gemini'].includes(env.AI_PROVIDER)) return env.AI_PROVIDER;
  if(env.APINEBULA_API_KEY) return 'apinebula';
  if(env.ANTHROPIC_API_KEY) return 'anthropic';
  return 'apinebula';
}
async function generateDraft(env,hotspots,which) {
  if(which==='gemini') throw Error('Gemini暂未接入送审稿，请在后台改选Claude官方或ApiNebula');
  const make=which==='anthropic'?anthropicDraft:apiNebulaDraft;
  return {...await make(env,hotspots),provider:which};
}
async function apiNebulaDraft(env,hotspots) {
  if(!env.APINEBULA_API_KEY) throw Error('未配置ApiNebula');
  const model=env.APINEBULA_MODEL||DEFAULT_MODEL;
  const request={
    method:'POST',headers:{Authorization:`Bearer ${env.APINEBULA_API_KEY}`,'Content-Type':'application/json'},
    body:JSON.stringify({model,max_tokens:1800,
      messages:[{role:'system',content:DRAFT_SYSTEM},{role:'user',content:draftPrompt(hotspots)}]})};
  // Drafting is slower than the 8-token connection check, so allow 45s per try and retry once on transient failures.
  const result=await withRetry(async()=>{
    const answer=await provider('https://apinebula.ai/v1/chat/completions',request,45000);
    const text=answer.choices?.[0]?.message?.content;
    if(typeof text!=='string' || !text.trim()) throw Error('模型未返回草稿');
    return answer;
  });
  const content=result.choices[0].message.content;
  return {content:content.trim(),model,usage:result.usage||null};
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
  async route(request) {
    const url=new URL(request.url),path=url.pathname;
    const saved=await this.ctx.storage.get('private-config');
    const env={...this.env,...(saved?await unseal(saved,this.env.TOKEN_ENCRYPTION_KEY):{})};
    if(path==='/health' && request.method==='GET')
      return json({ok:true,service:'uncleonchain-trade-bot',mode:this.env.RUN_MODE||'live',
        publishing:this.env.PUBLISH_ENABLED==='true'});
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
        for(const name of ['X_CLIENT_ID','X_CLIENT_SECRET','TELEGRAM_BOT_TOKEN','TELEGRAM_OWNER_ID','GEMINI_API_KEY','ANTHROPIC_API_KEY','ANTHROPIC_MODEL','APINEBULA_API_KEY','APINEBULA_MODEL','AI_PROVIDER']) {
          const value=form.get(name)?.trim();
          if(value) {
            if(value.length>2000 || /[\r\n]/.test(value)) return json({ok:false,error:'字段格式有误'},400);
            if(name==='TELEGRAM_OWNER_ID' && !/^\d+$/.test(value)) return json({ok:false,error:'审核人ID应为数字'},400);
            if(name==='AI_PROVIDER' && !['anthropic','apinebula','gemini'].includes(value)) return json({ok:false,error:'模型供应商无效'},400);
            if(name==='ANTHROPIC_MODEL' && !/^claude-[a-z0-9-]+$/.test(value)) return json({ok:false,error:'Claude模型名格式有误'},400);
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
      ['GEMINI_API_KEY','Gemini API Key（备用）','password'],
      ['ANTHROPIC_API_KEY','Claude官方 API Key','password'],
      ['ANTHROPIC_MODEL','Claude官方模型名（默认 claude-sonnet-5-5）','text'],
      ['APINEBULA_API_KEY','ApiNebula API令牌','password'],
      ['APINEBULA_MODEL','ApiNebula模型名（按模型广场填写）','text']]
      .map(([name,label,type])=>`<p><label>${label}<br><input style="width:95%;font:inherit;padding:8px" name="${name}" type="${type}" autocomplete="off" placeholder="${env[name]?'已保存；留空保持':'待填写'}"></label></p>`).join('');
    const selected=env.AI_PROVIDER||'anthropic';
    const providerName={anthropic:'Claude官方',apinebula:'ApiNebula',gemini:'Gemini'}[selected];
    const providerField=`<p><label>首选撰稿模型<br><select style="width:98%;font:inherit;padding:8px" name="AI_PROVIDER"><option value="apinebula" ${selected==='apinebula'?'selected':''}>ApiNebula（Claude兼容）</option><option value="anthropic" ${selected==='anthropic'?'selected':''}>Claude官方</option><option value="gemini" ${selected==='gemini'?'selected':''}>Gemini</option></select></label></p>`;
    return page('热币交易 · 后台设置',`<p>运行模式：<b>${env.RUN_MODE==='live'?'正式环境':'测试环境'}</b>；发布总开关：<b>${env.PUBLISH_ENABLED==='true'?'已开启':'待端到端验证后开启'}</b></p><ul><li>X应用配置：${ready?'已填写':'待填写 X_CLIENT_ID、X_CLIENT_SECRET'}</li><li>X账号：${account?escapeHTML('@'+account.username):'未授权'}</li><li>Telegram Token：${env.TELEGRAM_BOT_TOKEN?'已填写':'待填写'}</li><li>Telegram审核人：${env.TELEGRAM_OWNER_ID?'已指定':'待指定'}</li><li>首选撰稿模型：${providerName}</li><li>ApiNebula：${env.APINEBULA_API_KEY?'已填写':'待填写'} · ${escapeHTML(env.APINEBULA_MODEL||DEFAULT_MODEL)}</li><li>Claude官方 API：${env.ANTHROPIC_API_KEY?'已填写':'待填写'} · ${escapeHTML(env.ANTHROPIC_MODEL||DEFAULT_MODEL)}</li><li>Gemini API：${env.GEMINI_API_KEY?'已填写':'待填写'}</li><li>今日：${escapeHTML(beijingDay())} · ${escapeHTML(eligible(beijingDay()).reason)}</li></ul><p>Callback URL：<code>${escapeHTML(url.origin+'/oauth/x/callback')}</code></p><h2>填写应用凭据</h2><p>只存入你自己的Cloudflare后台，保存后不回显。留空保留原值。</p><form method="post" action="/admin/config">${hidden}${providerField}${fields}<button>加密保存</button></form><h2>连接账号</h2><form method="post" action="/admin/x/connect">${hidden}<button ${ready?'':'disabled'}>授权 @Uncle_Onchain</button></form><p></p><form method="post" action="/admin/telegram/connect">${hidden}<button ${env.TELEGRAM_BOT_TOKEN?'':'disabled'}>接通Telegram机器人</button></form><p></p><form method="post" action="/admin/apinebula/check">${hidden}<button ${env.APINEBULA_API_KEY?'':'disabled'}>检查ApiNebula连接</button></form><p></p><form method="post" action="/admin/anthropic/check">${hidden}<button ${env.ANTHROPIC_API_KEY?'':'disabled'}>检查Claude官方连接</button></form><p></p><form method="post" action="/admin/gemini/check">${hidden}<button ${env.GEMINI_API_KEY?'':'disabled'}>检查Gemini连接</button></form>`,200,{'Referrer-Policy':'same-origin'});
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
      await reply(draft?`今日送审稿状态：${draft.status}；模型：${draft.model}`:'今天还没有送审稿。');
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
      which=draftProvider(env);
      const generated=await generateDraft(env,hotspots+verified,which);
      // Deterministic safety nets: $ prefix on coins, and a format check the reviewer can see.
      const content=normalizeTickers(generated.content,collectTickers(snapshot,hotspots));
      const lint=lintDraft(content);
      const draft={...generated,content,lint,status:'pending_text_review',created:Date.now(),day};
      await this.ctx.storage.put('draft:'+day,draft);
      const warning=lint.length?`\n\n⚠ 格式自检：${lint.join('；')}。可点“退回重写”。`:'';
      await telegram(env,'sendMessage',{chat_id:chat,text:`${draft.content}${warning}\n\n——\n状态：等待文字审核｜模型：${draft.model}`,
        reply_markup:{inline_keyboard:[[
          {text:'✅ 文字通过',callback_data:`text_ok:${day}`},
          {text:'↩️ 退回重写',callback_data:`text_reject:${day}`}
        ]]}});
    } catch(error) {
      const failure=which==='anthropic'?anthropicFailure(error):apiNebulaFailure(error);
      await this.ctx.storage.put('draft:'+day,{status:'generation_failed',failed:Date.now(),provider:which,
        model:(which==='anthropic'?env.ANTHROPIC_MODEL:env.APINEBULA_MODEL)||DEFAULT_MODEL,http_status:failure.status,failure:failure.label,prompt_chars:promptChars,detail:failure.detail||failure.reason});
      await telegram(env,'sendMessage',{chat_id:chat,text:`送审稿生成失败，素材已保留。原因：${failure.reason}${failure.detail?'平台返回：'+failure.detail:''}（${failure.label}）。${failure.attempts>1?'已自动重试过一次；':''}可发送 /draft 再试。`});
    }
    return json({ok:true});
  }
  async callbackQuery(query,env) {
    const actor=String(query.from?.id||''),chat=query.message?.chat?.id;
    if(!env.TELEGRAM_OWNER_ID || actor!==String(env.TELEGRAM_OWNER_ID) || !chat) return json({ok:true});
    const [action,day]=String(query.data||'').split(':');
    if(!/^\d{4}-\d{2}-\d{2}$/.test(day||'')) return json({ok:true});
    const draft=await this.ctx.storage.get('draft:'+day);
    if(!draft) return json({ok:true});
    if(action==='text_ok' && draft.status==='pending_text_review') {
      draft.status='text_approved';draft.approved=Date.now();
      await this.ctx.storage.put('draft:'+day,draft);
      await telegram(env,'answerCallbackQuery',{callback_query_id:query.id,text:'文字稿已通过'});
      await telegram(env,'sendMessage',{chat_id:chat,text:'文字稿已通过。海报和正式中英文稿生成尚未启用，不会发推。'});
    } else if(action==='text_reject' && draft.status==='pending_text_review') {
      draft.status='text_rejected';draft.rejected=Date.now();
      await this.ctx.storage.put('draft:'+day,draft);
      await telegram(env,'answerCallbackQuery',{callback_query_id:query.id,text:'已退回'});
      await telegram(env,'sendMessage',{chat_id:chat,text:'已退回。发送修改要求后再发送 /draft；当前版本不会发推。'});
    } else {
      await telegram(env,'answerCallbackQuery',{callback_query_id:query.id,text:'该稿件已经处理'});
    }
    return json({ok:true});
  }
}
