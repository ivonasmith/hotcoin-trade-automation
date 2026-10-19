import test from 'node:test';
import assert from 'node:assert/strict';
import {TradeDesk} from '../cloud/worker.mjs';

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
