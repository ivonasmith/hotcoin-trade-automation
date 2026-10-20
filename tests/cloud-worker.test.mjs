import test from 'node:test';
import assert from 'node:assert/strict';
import {TradeDesk} from '../cloud/worker.mjs';
import {beijingDay} from '../calendar.mjs';

function setup(overrides={}) {
  const entries=new Map();
  const storage={get:async k=>entries.get(k),put:async(k,v)=>entries.set(k,v),
    delete:async k=>entries.delete(k),transaction:async fn=>fn(storage)};
  const env={ADMIN_PASSWORD:'test-password',TOKEN_ENCRYPTION_KEY:'test-key-'.repeat(6),
    X_CLIENT_ID:'test-client',X_CLIENT_SECRET:'test-secret',EXPECTED_X_USERNAME:'Uncle_Onchain',
    ...overrides};
  return {desk:new TradeDesk({storage},env),entries,env};
}
function adminRequest(path,method='GET') {
  return new Request('https://desk.test'+path,{method,headers:{
    Authorization:'Basic '+btoa('admin:test-password'),Origin:'https://desk.test'}});
}
test('管理页面保护和跨站请求拒绝',async()=>{
  const {desk}=setup();
  assert.equal((await desk.fetch(new Request('https://desk.test/admin'))).status,401);
  const req=adminRequest('/admin/x/connect','POST');
  req.headers.set('Origin','https://evil.test');
  assert.equal((await desk.fetch(req)).status,403);
});
test('X跳转包含固定回调与PKCE、不暴露client secret',async()=>{
  const {desk,entries}=setup();
  const r=await desk.fetch(adminRequest('/admin/x/connect','POST'));
  assert.equal(r.status,303);
  const target=new URL(r.headers.get('Location'));
  assert.equal(target.origin,'https://x.com');
  assert.equal(target.searchParams.get('redirect_uri'),'https://desk.test/oauth/x/callback');
  assert.equal(target.searchParams.get('code_challenge_method'),'S256');
  assert.equal(target.searchParams.get('scope').includes('offline.access'),true);
  assert.equal(target.toString().includes('test-secret'),false);
  assert.equal(entries.get('oauth-pending').state,target.searchParams.get('state'));
});
test('回调必须有匹配state与同浏览器cookie',async()=>{
  const {desk,entries}=setup();
  entries.set('oauth-pending',{state:'test-state',verifier:'test-verifier',
    expires:Date.now()+60000,origin:'https://desk.test'});
  assert.equal((await desk.fetch(new Request('https://desk.test/oauth/x/callback?state=test-state&code=fake'))).status,400);
  assert.equal(entries.has('oauth-pending'),true);
});
test('合法X授权加密存储且不能重放',async()=>{
  const {desk,entries}=setup();
  await desk.fetch(adminRequest('/admin/x/connect','POST'));
  const state=entries.get('oauth-pending').state;
  const old=globalThis.fetch,calls=[];
  globalThis.fetch=async url=>{
    calls.push(String(url));
    if(String(url).endsWith('/oauth2/token')) return Response.json({access_token:'test-access',refresh_token:'test-refresh'});
    if(String(url).endsWith('/users/me')) return Response.json({data:{id:'123',username:'Uncle_Onchain'}});
    throw Error('Unexpected provider call');
  };
  try {
    const req=()=>new Request(`https://desk.test/oauth/x/callback?state=${state}&code=test`,{
      headers:{Cookie:'desk_oauth='+state}});
    assert.equal((await desk.fetch(req())).status,200);
    assert.equal(JSON.stringify(entries.get('x-tokens')).includes('test-access'),false);
    assert.equal(entries.get('x-account').username,'Uncle_Onchain');
    assert.equal((await desk.fetch(req())).status,400);
    assert.equal(calls.length,2);
    assert.equal(calls.some(url=>url.includes('/tweets')),false);
  } finally {globalThis.fetch=old;}
});
test('Telegram请求验证secret、接收人及分段提交',async()=>{
  const {desk,entries}=setup({TELEGRAM_WEBHOOK_SECRET:'hook-secret',
    TELEGRAM_BOT_TOKEN:'test-token',TELEGRAM_OWNER_ID:'123'});
  const old=globalThis.fetch;
  globalThis.fetch=async()=>Response.json({ok:true});
  const send=(id,text,actor=123,secret='hook-secret')=>desk.fetch(new Request('https://desk.test/telegram/webhook',{
    method:'POST',headers:{'Content-Type':'application/json','X-Telegram-Bot-Api-Secret-Token':secret},
    body:JSON.stringify({update_id:id,message:{chat:{id:actor,type:'private'},from:{id:actor},text}})}));
  try {
    assert.equal((await send(1,'/hotspots',123,'bad-secret')).status,403);
    await send(2,'/hotspots',999);
    assert.equal(entries.has('hotspots-working'),false);
    await send(3,'/hotspots');
    await send(4,'测试热点，不是真实交易数据');
    await send(4,'重复消息应忽略');
    assert.equal(entries.get('hotspots-working').parts.length,1);
    await send(5,'/done');
    assert.equal(entries.has('hotspots-working'),false);
    assert.equal([...entries.keys()].filter(k=>k.startsWith('hotspots:')).length,1);
  } finally {globalThis.fetch=old;}
});
test('Telegram热点完成后生成送审稿并由唯一审核人批准',async()=>{
  const {desk,entries}=setup({TELEGRAM_WEBHOOK_SECRET:'hook-secret',TELEGRAM_BOT_TOKEN:'test-token',
    TELEGRAM_OWNER_ID:'123',APINEBULA_API_KEY:'sk-nebula-secret',APINEBULA_MODEL:'claude-sonnet-5-5'});
  const old=globalThis.fetch;
  const telegramCalls=[];
  globalThis.fetch=async(url,options)=>{
    if(String(url).includes('apinebula.ai')) {
      const body=JSON.parse(options.body);
      assert.equal(body.model,'claude-sonnet-5-5');
      assert.equal(body.temperature,0.2);
      assert.equal(body.messages[0].content.includes('严禁提出新单'),true);
      return Response.json({choices:[{message:{content:'审核正文：今天不开新单。\n\n送审备注：缺交易数据。'}}],usage:{total_tokens:20}});
    }
    telegramCalls.push({url:String(url),body:JSON.parse(options.body)});
    return Response.json({ok:true});
  };
  const send=(id,text)=>desk.fetch(new Request('https://desk.test/telegram/webhook',{method:'POST',
    headers:{'Content-Type':'application/json','X-Telegram-Bot-Api-Secret-Token':'hook-secret'},
    body:JSON.stringify({update_id:id,message:{chat:{id:123,type:'private'},from:{id:123},text}})}));
  try {
    await send(20,'/hotspots');await send(21,'测试热点');await send(22,'/done');
    const draft=[...entries.entries()].find(([key])=>key.startsWith('draft:'))[1];
    assert.equal(draft.status,'pending_text_review');
    assert.equal(telegramCalls.some(call=>call.body.reply_markup?.inline_keyboard),true);
    await desk.fetch(new Request('https://desk.test/telegram/webhook',{method:'POST',
      headers:{'Content-Type':'application/json','X-Telegram-Bot-Api-Secret-Token':'hook-secret'},
      body:JSON.stringify({update_id:23,callback_query:{id:'cb1',from:{id:123},message:{chat:{id:123}},data:`text_ok:${draft.day}`}})}));
    assert.equal(entries.get('draft:'+draft.day).status,'text_approved');
  } finally {globalThis.fetch=old;}
});
test('取数快照必须认证、当天、新鲜且包含核心币',async()=>{
  const {desk,entries}=setup({PIPELINE_SECRET:'pipeline-secret'});
  const metrics=Object.fromEntries(['BTC','ETH','SOL','XRP','BNB'].map(symbol=>[symbol,{price:1}]));
  const payload={day:beijingDay(),report:{ok:true,finished_at:new Date().toISOString()},summary:{指标:metrics},four_lines:'L1-L4'};
  const send=(secret,body=payload)=>desk.fetch(new Request('https://desk.test/pipeline/snapshot',{method:'POST',
    headers:{Authorization:'Bearer '+secret,'Content-Type':'application/json'},body:JSON.stringify(body)}));
  assert.equal((await send('bad')).status,403);
  assert.equal((await send('pipeline-secret')).status,200);
  assert.equal(entries.get('snapshot:'+beijingDay()).four_lines,'L1-L4');
  assert.equal((await send('pipeline-secret',{...payload,day:'2020-01-01'})).status,400);
});
test('凭据加密保存、管理页不回显、空字段保留原值',async()=>{
  const {desk,entries}=setup();
  const save=values=>desk.fetch(new Request('https://desk.test/admin/config',{
    method:'POST',headers:{Authorization:'Basic '+btoa('admin:test-password'),Origin:'https://desk.test'},
    body:new URLSearchParams(values)}));
  assert.equal((await save({X_CLIENT_SECRET:'unique-secret-sentinel',TELEGRAM_OWNER_ID:'123'})).status,303);
  assert.equal(JSON.stringify(entries.get('private-config')).includes('unique-secret-sentinel'),false);
  const text=await (await desk.fetch(adminRequest('/admin'))).text();
  assert.equal(text.includes('unique-secret-sentinel'),false);
  assert.equal((await save({X_CLIENT_SECRET:'',TELEGRAM_OWNER_ID:'456'})).status,303);
  const {unseal}=await import('../cloud/security.mjs');
  const cfg=await unseal(entries.get('private-config'),'test-key-'.repeat(6));
  assert.equal(cfg.X_CLIENT_SECRET,'unique-secret-sentinel');
  assert.equal(cfg.TELEGRAM_OWNER_ID,'456');
  assert.equal((await save({TELEGRAM_OWNER_ID:'not-a-number'})).status,400);
});
test('隐私浏览器null或缺失Origin凭表单token正常保存，伪造token拒绝',async()=>{
  const {desk}=setup();
  const html=await (await desk.fetch(adminRequest('/admin'))).text();
  const token=html.match(/name="_csrf" value="([^"]+)"/)[1];
  for(const origin of ['null',undefined]) {
    const headers={Authorization:'Basic '+btoa('admin:test-password')};
    if(origin) headers.Origin=origin;
    const response=await desk.fetch(new Request('https://desk.test/admin/config',{
      method:'POST',headers,body:new URLSearchParams({_csrf:token,TELEGRAM_OWNER_ID:'123'})}));
    assert.equal(response.status,303);
  }
  for(const origin of ['null','https://evil.test']) {
    const response=await desk.fetch(new Request('https://desk.test/admin/config',{
      method:'POST',headers:{Authorization:'Basic '+btoa('admin:test-password'),Origin:origin},
      body:new URLSearchParams({_csrf:origin==='null'?'forged':token,TELEGRAM_OWNER_ID:'999'})}));
    assert.equal(response.status,403);
    assert.equal((await response.text()).includes('刷新后台'),true);
  }
});
test('Gemini密钥可独立保存、不回显，检查模型列表无需发帖',async()=>{
  const {desk}=setup();
  const req=adminRequest('/admin/config','POST');
  const save=new Request(req,{body:new URLSearchParams({GEMINI_API_KEY:'gemini-test-secret'})});
  assert.equal((await desk.fetch(save)).status,303);
  const html=await (await desk.fetch(adminRequest('/admin'))).text();
  assert.equal(html.includes('Gemini API：已填写'),true);
  assert.equal(html.includes('gemini-test-secret'),false);
  const old=globalThis.fetch;
  globalThis.fetch=async(url,options)=>{
    assert.equal(String(url).startsWith('https://generativelanguage.googleapis.com/v1beta/models'),true);
    assert.equal(String(url).includes('gemini-test-secret'),false);
    assert.equal(options.headers['x-goog-api-key'],'gemini-test-secret');
    return Response.json({models:[{name:'models/gemini-3.5-flash-lite',supportedGenerationMethods:['generateContent']}]});
  };
  try { assert.equal((await desk.fetch(adminRequest('/admin/gemini/check','POST'))).status,200); }
  finally {globalThis.fetch=old;}
});
test('Claude密钥和模型加密保存，连接检查不生成正文',async()=>{
  const {desk}=setup();
  const req=adminRequest('/admin/config','POST');
  const save=new Request(req,{body:new URLSearchParams({ANTHROPIC_API_KEY:'claude-test-secret',
    ANTHROPIC_MODEL:'claude-sonnet-5',AI_PROVIDER:'anthropic'})});
  assert.equal((await desk.fetch(save)).status,303);
  const html=await (await desk.fetch(adminRequest('/admin'))).text();
  assert.equal(html.includes('Claude官方 API：已填写'),true);
  assert.equal(html.includes('claude-test-secret'),false);
  const old=globalThis.fetch;
  globalThis.fetch=async(url,options)=>{
    assert.equal(String(url),'https://api.anthropic.com/v1/models/claude-sonnet-5');
    assert.equal(options.headers['x-api-key'],'claude-test-secret');
    assert.equal(options.headers['anthropic-version'],'2023-06-01');
    assert.equal(options.method,undefined);
    return Response.json({id:'claude-sonnet-5',type:'model',display_name:'Claude Sonnet 5'});
  };
  try { assert.equal((await desk.fetch(adminRequest('/admin/anthropic/check','POST'))).status,200); }
  finally {globalThis.fetch=old;}
});
test('ApiNebula令牌独立保存并用Claude兼容接口做最小检查',async()=>{
  const {desk}=setup();
  const req=adminRequest('/admin/config','POST');
  const save=new Request(req,{body:new URLSearchParams({APINEBULA_API_KEY:'sk-nebula-secret',
    APINEBULA_MODEL:'claude-sonnet-5',AI_PROVIDER:'apinebula'})});
  assert.equal((await desk.fetch(save)).status,303);
  const html=await (await desk.fetch(adminRequest('/admin'))).text();
  assert.equal(html.includes('首选撰稿模型：ApiNebula'),true);
  assert.equal(html.includes('ApiNebula：已填写'),true);
  assert.equal(html.includes('sk-nebula-secret'),false);
  const old=globalThis.fetch;
  globalThis.fetch=async(url,options)=>{
    assert.equal(options.headers.Authorization,'Bearer sk-nebula-secret');
    assert.equal(String(url),'https://apinebula.ai/v1/chat/completions');
    const body=JSON.parse(options.body);
    assert.equal(body.model,'claude-sonnet-5');
    assert.equal(body.max_tokens,8);
    return Response.json({id:'chatcmpl_test',choices:[{message:{role:'assistant',content:'OK'}}]});
  };
  try { assert.equal((await desk.fetch(adminRequest('/admin/apinebula/check','POST'))).status,200); }
  finally {globalThis.fetch=old;}
});
test('ApiNebula错误显示平台原因但遮盖密钥',async()=>{
  const {desk}=setup();
  const saveReq=adminRequest('/admin/config','POST');
  await desk.fetch(new Request(saveReq,{body:new URLSearchParams({APINEBULA_API_KEY:'sk-nebula-secret',
    APINEBULA_MODEL:'claude-sonnet-4-6',AI_PROVIDER:'apinebula'})}));
  const old=globalThis.fetch;
  globalThis.fetch=async()=>Response.json({error:{type:'permission_error',
    message:'route denied for sk-nebula-secret'}},{status:403});
  try {
    const response=await desk.fetch(adminRequest('/admin/apinebula/check','POST'));
    const html=await response.text();
    assert.equal(html.includes('route denied'),true);
    assert.equal(html.includes('sk-nebula-secret'),false);
  } finally {globalThis.fetch=old;}
});
function draftSetup(fetchImpl) {
  const ctx=setup({TELEGRAM_WEBHOOK_SECRET:'hook-secret',TELEGRAM_BOT_TOKEN:'test-token',
    TELEGRAM_OWNER_ID:'123',APINEBULA_API_KEY:'sk-nebula-secret',APINEBULA_MODEL:'claude-sonnet-5-5'});
  const calls={telegram:[],nebula:[]};
  const old=globalThis.fetch;
  globalThis.fetch=async(url,options)=>{
    if(String(url).includes('apinebula.ai')) {calls.nebula.push(JSON.parse(options.body));return fetchImpl(calls.nebula.length);}
    calls.telegram.push(JSON.parse(options.body));
    return Response.json({ok:true});
  };
  const day=beijingDay();
  ctx.entries.set('hotspots:'+day,{day,parts:['测试热点'],submitted:Date.now()});
  return {...ctx,calls,day,restore:()=>{globalThis.fetch=old;}};
}
test('大快照先压缩再给模型，且提示词标注已压缩',async()=>{
  const {desk,entries,calls,day,restore}=draftSetup(()=>Response.json({choices:[{message:{content:'审核正文：ok\n\n送审备注：ok'}}]}));
  const metrics={};
  for(const name of ['BTC','ETH','SOL','XRP','BNB']) metrics[name]={levels:Array.from({length:4000},(_,i)=>({p:i+0.123456789,note:'段'+i}))};
  entries.set('snapshot:'+day,{report:{ok:true,finished_at:new Date().toISOString()},summary:{'指标':metrics},four_lines:'L1：偏多',received_at:Date.now()});
  try {
    const response=await desk.makeDraft(123,desk.env);
    assert.equal(response.status,200);
    const prompt=calls.nebula[0].messages[1].content;
    assert.equal(prompt.length<70000,true);
    assert.equal(prompt.includes('体积已压缩'),true);
    assert.equal(prompt.includes('L1：偏多'),true);
    assert.equal(entries.get('draft:'+day).status,'pending_text_review');
  } finally {restore();}
});
test('请求超时如实显示为超时而不是HTTP 502，并自动重试一次',async()=>{
  const {desk,entries,calls,day,restore}=draftSetup(()=>{throw Object.assign(new Error('timed out'),{name:'TimeoutError'});});
  try {
    await desk.makeDraft(123,desk.env);
    assert.equal(calls.nebula.length,2);
    const record=entries.get('draft:'+day);
    assert.equal(record.status,'generation_failed');
    assert.equal(record.http_status,null);
    assert.equal(record.failure,'请求超时');
    const message=calls.telegram.at(-1).text;
    assert.equal(message.includes('请求超时'),true);
    assert.equal(message.includes('HTTP 502'),false);
    assert.equal(message.includes('/draft'),true);
  } finally {restore();}
});
test('网关返回HTML错误页时显示真实状态码并重试，第二次成功则出稿',async()=>{
  const {desk,entries,calls,day,restore}=draftSetup(n=>n===1
    ?new Response('<html><body><h1>502 Bad Gateway</h1></body></html>',{status:502})
    :Response.json({choices:[{message:{content:'审核正文：ok\n\n送审备注：ok'}}]}));
  try {
    await desk.makeDraft(123,desk.env);
    assert.equal(calls.nebula.length,2);
    assert.equal(entries.get('draft:'+day).status,'pending_text_review');
  } finally {restore();}
});
test('401这类配置错误不重试，直接提示令牌问题',async()=>{
  const {desk,entries,calls,day,restore}=draftSetup(()=>Response.json({error:{message:'bad key'}},{status:401}));
  try {
    await desk.makeDraft(123,desk.env);
    assert.equal(calls.nebula.length,1);
    assert.equal(entries.get('draft:'+day).http_status,401);
    assert.equal(calls.telegram.at(-1).text.includes('令牌'),true);
  } finally {restore();}
});
test('模型漏写$时由程序补上，汉字数字在Telegram消息里被提示',async()=>{
  const draftText='审核正文：今天我只挂一张很小的 BTC 空单，挂在八万二千三附近。ETH 跌得比 BTC 多。ZRO 今天解锁。\n\n送审备注：可发。';
  const {desk,entries,calls,day,restore}=draftSetup(()=>Response.json({choices:[{message:{content:draftText}}]}));
  entries.set('hotspots:'+day,{day,parts:['ZRO今天解锁约2,360万枚'],submitted:Date.now()});
  entries.set('snapshot:'+day,{report:{ok:true,finished_at:new Date().toISOString()},
    summary:{'指标':{BTC:{price:1},ETH:{price:1}}},four_lines:'L1：偏多',received_at:Date.now()});
  try {
    await desk.makeDraft(123,desk.env);
    const message=calls.telegram.at(-1).text;
    assert.equal(message.includes('一张很小的 $BTC 空单'),true);
    assert.equal(message.includes('$ETH 跌得比 BTC'),true);
    assert.equal(message.includes('$ZRO 今天解锁'),true);
    assert.equal(message.includes('⚠ 格式自检'),true);
    assert.equal(message.includes('八万二千三'),true);
    assert.equal(entries.get('draft:'+day).content.includes('$BTC'),true);
  } finally {restore();}
});
