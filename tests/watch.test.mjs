import test from 'node:test';
import assert from 'node:assert/strict';
import {TradeDesk} from '../cloud/worker.mjs';
import {parseWatch,addRule,removeRules,applyFired,describeRule} from '../cloud/watch.mjs';

test('/watch 指令解析：币、条件、数字、常驻；乱写会给用法',()=>{
  assert.deepEqual(parseWatch('/watch sol 跌破 95').rule,{sym:'SOL',kind:'down',value:95,repeat:false});
  assert.deepEqual(parseWatch('/watch $BTCUSDT 日收上 82,314.5 常驻').rule,{sym:'BTC',kind:'dayup',value:82314.5,repeat:true});
  assert.equal(parseWatch('/watch ETH 4h收下 2400').rule.kind,'h4down');
  assert.equal(parseWatch('/watch DOGE 急涨 3%').rule.kind,'surge');
  assert.equal(parseWatch('/watch DOGE 急涨 0.1').ok,false);
  assert.equal(parseWatch('/watch DOGE 乱写 3').ok,false);
  assert.equal(parseWatch('/watch SOL 跌破 -1').ok,false);
  assert.match(parseWatch('/watch SOL').error,/用法/);
  assert.equal(parseWatch('/watch 币!! 跌破 1').ok,false);
});
test('加规则去重、限条数；删除按序号、币、all；一次性触发后删除、常驻记录时间',()=>{
  let list=[],seq=0;
  const add=text=>{const r=addRule(list,parseWatch(text).rule,seq+1,1000);if(r.ok){seq++;list=r.list;}return r;};
  assert.equal(add('/watch SOL 跌破 95').ok,true);
  assert.equal(add('/watch SOL 跌破 95').ok,false);
  assert.equal(add('/watch BTC 上破 90000 常驻').ok,true);
  assert.equal(describeRule(list[0]),'#1 SOL 价格跌到 95（提醒一次）');
  assert.deepEqual(applyFired(list,[1,2],5000).map(r=>[r.id,r.last_fired]),[[2,5000]]);
  assert.equal(removeRules(list,'#2').removed[0].sym,'BTC');
  assert.equal(removeRules(list,'sol').list.length,1);
  assert.equal(removeRules(list,'all').list.length,0);
  assert.equal(removeRules(list,'XRP').removed.length,0);
});

function rig() {
  const entries=new Map();
  const storage={get:async k=>entries.get(k),put:async(k,v)=>entries.set(k,v),delete:async k=>entries.delete(k),transaction:async fn=>fn(storage)};
  let alarmAt=null;storage.setAlarm=async t=>{alarmAt=t;};storage.deleteAlarm=async()=>{alarmAt=null;};
  const env={ADMIN_PASSWORD:'p',TOKEN_ENCRYPTION_KEY:'test-key-'.repeat(6),TELEGRAM_WEBHOOK_SECRET:'hook',TELEGRAM_BOT_TOKEN:'tok',
    TELEGRAM_OWNER_ID:'123',PIPELINE_SECRET:'pipe-secret'};
  const desk=new TradeDesk({storage},env);
  const sent=[],old=globalThis.fetch;
  globalThis.fetch=async(url,options)=>{sent.push(JSON.parse(options.body));return Response.json({ok:true});};
  return {desk,entries,sent,alarm:()=>alarmAt,restore:()=>{globalThis.fetch=old;}};
}
const hook=(desk,id,text,actor=123)=>desk.fetch(new Request('https://desk.test/telegram/webhook',{method:'POST',
  headers:{'Content-Type':'application/json','X-Telegram-Bot-Api-Secret-Token':'hook'},
  body:JSON.stringify({update_id:id,message:{chat:{id:actor,type:'private'},from:{id:actor},text}})}));
const pipe=(desk,path,method='GET',body=null,secret='pipe-secret')=>desk.fetch(new Request('https://desk.test'+path,{method,
  headers:{Authorization:'Bearer '+secret,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined}));

test('Telegram 里 /watch /watchlist /unwatch 只认审核人，清单可被服务器读取',async()=>{
  const {desk,sent,restore}=rig();
  try {
    await hook(desk,1,'/watch SOL 跌破 95',999);
    assert.equal(sent.length,0);
    await hook(desk,2,'/watch SOL 跌破 95');
    assert.match(sent.at(-1).text,/已加入监控：#1 SOL 价格跌到 95/);
    await hook(desk,3,'/watch BTC 日收上 82314 常驻');
    await hook(desk,4,'/watchlist');
    assert.match(sent.at(-1).text,/#1 SOL[\s\S]*#2 BTC 日线收盘站上 82314（常驻）/);
    const rules=(await (await pipe(desk,'/pipeline/watchlist')).json()).rules;
    assert.deepEqual(rules.map(r=>r.id),[1,2]);
    assert.equal((await pipe(desk,'/pipeline/watchlist','GET',null,'wrong')).status,403);
    await hook(desk,5,'/unwatch 1');
    assert.match(sent.at(-1).text,/已取消/);
    await hook(desk,6,'/watch');
    assert.match(sent.at(-1).text,/用法/);
  } finally {restore();}
});
test('服务器上报触发：发 Telegram 提醒，一次性规则被删除，常驻规则保留',async()=>{
  const {desk,sent,restore}=rig();
  try {
    await hook(desk,1,'/watch SOL 跌破 95');
    await hook(desk,2,'/watch BTC 上破 90000 常驻');
    const res=await pipe(desk,'/pipeline/notify','POST',{text:'SOL 跌破 95',fired:[1,2]});
    assert.equal(res.status,200);
    assert.equal(sent.at(-1).text,'SOL 跌破 95');
    const rules=(await (await pipe(desk,'/pipeline/watchlist')).json()).rules;
    assert.deepEqual(rules.map(r=>r.id),[2]);
    assert.ok(rules[0].last_fired>0);
    assert.equal((await pipe(desk,'/pipeline/notify','POST',{text:'x'},'wrong')).status,403);
  } finally {restore();}
});
test('主机心跳：第一次心跳挂上检查闹钟；超过15分钟没心跳提醒一次，恢复后再提醒；/host 看状态',async()=>{
  const {desk,entries,sent,alarm,restore}=rig();
  try {
    await hook(desk,1,'/host');
    assert.match(sent.at(-1).text,/还没有连接过/);
    await pipe(desk,'/pipeline/heartbeat','POST',{host:'hk-1',tasks:{collect:'2026-11-06'}});
    assert.ok(alarm()!==null,'心跳后应有闹钟');
    await hook(desk,2,'/host');
    assert.match(sent.at(-1).text,/主机 hk-1：在线[\s\S]*collect：2026-11-06/);
    // 时间到了但心跳新鲜：只续闹钟，不打扰
    const before=sent.length;
    entries.set('host-check',Date.now()-1);
    await desk.alarm();
    assert.equal(sent.length,before);
    assert.ok(entries.get('host-check')>Date.now());
    // 心跳变旧：提醒一次，不再续闹钟
    entries.get('host-heartbeat').at=Date.now()-20*60000;
    entries.set('host-check',Date.now()-1);
    await desk.alarm();
    assert.match(sent.at(-1).text,/主机已有 20 分钟没有心跳/);
    assert.equal(entries.has('host-check'),false);
    // 恢复
    await pipe(desk,'/pipeline/heartbeat','POST',{host:'hk-1',tasks:{}});
    assert.equal(sent.at(-1).text,'主机心跳恢复了。');
    assert.ok(entries.get('host-check')>Date.now());
  } finally {restore();}
});
