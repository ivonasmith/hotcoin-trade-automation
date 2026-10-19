import {beijingDay,eligible} from '../calendar.mjs';
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
async function provider(url,options) {
  const response=await fetch(url,{...options,signal:AbortSignal.timeout(25000)});
  let data;
  try {data=await response.json();} catch {throw Error('服务响应无法解析');}
  if(!response.ok || data.ok===false) throw Error(`服务请求失败（HTTP ${response.status}）`);
  return data;
}
async function telegram(env,method,payload) {
  if(!env.TELEGRAM_BOT_TOKEN) throw Error('未配置Telegram');
  return provider(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`,{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
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
        for(const name of ['X_CLIENT_ID','X_CLIENT_SECRET','TELEGRAM_BOT_TOKEN','TELEGRAM_OWNER_ID','GEMINI_API_KEY']) {
          const value=form.get(name)?.trim();
          if(value) {
            if(value.length>2000 || /[\r\n]/.test(value)) return json({ok:false,error:'字段格式有误'},400);
            if(name==='TELEGRAM_OWNER_ID' && !/^\d+$/.test(value)) return json({ok:false,error:'审核人ID应为数字'},400);
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
      if(path==='/admin/gemini/check' && request.method==='POST') {
        if(!env.GEMINI_API_KEY) return page('请先保存Gemini密钥','<a href="/admin">返回后台</a>',409);
        const result=await provider('https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000',{
          headers:{'x-goog-api-key':env.GEMINI_API_KEY}});
        const available=result.models?.some(m=>m.name==='models/gemini-3.5-flash-lite' &&
          m.supportedGenerationMethods?.includes('generateContent'));
        return page(available?'Gemini密钥检查通过':'当前密钥未找到预设模型',
          `<p>${available?'已确认可访问模型列表；实际免费生成额度仍以首次运行验证为准。':'请检查Google AI Studio的可用模型和地区。'}</p><p><a href="/admin">返回后台</a></p>`,available?200:409);
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
      ['GEMINI_API_KEY','Gemini API Key（你已有的免费API密钥）','password']]
      .map(([name,label,type])=>`<p><label>${label}<br><input style="width:95%;font:inherit;padding:8px" name="${name}" type="${type}" autocomplete="off" placeholder="${env[name]?'已保存；留空保持':'待填写'}"></label></p>`).join('');
    return page('热币交易 · 后台设置',`<p>运行模式：<b>${env.RUN_MODE==='live'?'正式环境':'测试环境'}</b>；发布总开关：<b>${env.PUBLISH_ENABLED==='true'?'已开启':'待端到端验证后开启'}</b></p><ul><li>X应用配置：${ready?'已填写':'待填写 X_CLIENT_ID、X_CLIENT_SECRET'}</li><li>X账号：${account?escapeHTML('@'+account.username):'未授权'}</li><li>Telegram Token：${env.TELEGRAM_BOT_TOKEN?'已填写':'待填写'}</li><li>Telegram审核人：${env.TELEGRAM_OWNER_ID?'已指定':'待指定'}</li><li>Gemini API：${env.GEMINI_API_KEY?'已填写':'待填写'}</li><li>今日：${escapeHTML(beijingDay())} · ${escapeHTML(eligible(beijingDay()).reason)}</li></ul><p>Callback URL：<code>${escapeHTML(url.origin+'/oauth/x/callback')}</code></p><h2>填写应用凭据</h2><p>只存入你自己的Cloudflare后台，保存后不回显。留空保留原值。</p><form method="post" action="/admin/config">${hidden}${fields}<button>加密保存</button></form><h2>连接账号</h2><form method="post" action="/admin/x/connect">${hidden}<button ${ready?'':'disabled'}>授权 @Uncle_Onchain</button></form><p></p><form method="post" action="/admin/telegram/connect">${hidden}<button ${env.TELEGRAM_BOT_TOKEN?'':'disabled'}>接通Telegram机器人</button></form><p></p><form method="post" action="/admin/gemini/check">${hidden}<button ${env.GEMINI_API_KEY?'':'disabled'}>检查Gemini连接</button></form>`,200,{'Referrer-Policy':'same-origin'});
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
      await reply('先发送 /hotspots，再粘贴今日Grok热点（可分多条），最后发送 /done。提交热点不会批准发推。');
      return json({ok:true});
    }
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
        response='今日热点已保存。撰稿与发布尚未启用，收到素材不会直接发推。';return;
      }
      if(!text) {response='当前请粘贴文字；文件接收稍后接入。';return;}
      if(text.startsWith('/')) {response='可用命令：/hotspots、/done、/cancel。';return;}
      if(working.parts.join('').length+text.length>50000) {response='内容超过5万字，请精简后重新提交。';return;}
      working.parts.push(text); await tx.put(key,working);
      response=`已收到第${working.parts.length}段；发完请发送 /done。`;
    });
    if(response) await reply(response);
    return json({ok:true});
  }
}
