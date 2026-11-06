import test from 'node:test';
import assert from 'node:assert/strict';
import {TradeDesk} from '../cloud/worker.mjs';
import {beijingDay} from '../calendar.mjs';

function rig() {
  const entries=new Map();
  const storage={get:async k=>entries.get(k),put:async(k,v)=>entries.set(k,v),delete:async k=>entries.delete(k),transaction:async fn=>fn(storage)};
  storage.setAlarm=async()=>{};storage.deleteAlarm=async()=>{};
  const env={ADMIN_PASSWORD:'p',TOKEN_ENCRYPTION_KEY:'test-key-'.repeat(6),TELEGRAM_WEBHOOK_SECRET:'hook',TELEGRAM_BOT_TOKEN:'tok',
    TELEGRAM_OWNER_ID:'123',PIPELINE_SECRET:'pipe-secret'};
  const desk=new TradeDesk({storage},env);
  const sent=[],old=globalThis.fetch;
  globalThis.fetch=async(url,options)=>{sent.push(JSON.parse(options.body));return Response.json({ok:true});};
  return {desk,entries,sent,env,restore:()=>{globalThis.fetch=old;}};
}
const hook=(desk,id,text)=>desk.fetch(new Request('https://desk.test/telegram/webhook',{method:'POST',
  headers:{'Content-Type':'application/json','X-Telegram-Bot-Api-Secret-Token':'hook'},
  body:JSON.stringify({update_id:id,message:{chat:{id:123,type:'private'},from:{id:123},text}})}));
const pipe=(desk,path,method='GET',body=null,secret='pipe-secret')=>desk.fetch(new Request('https://desk.test'+path,{method,
  headers:{Authorization:'Bearer '+secret,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined}));

const level=(mid,dist,segs=4)=>({mid,dist,segs});
const metric=price=>({price,ma50:price*0.97,ma200:price*0.9,dev200:8,rsi_d:55,atr_d_pct:4,atr_4_pct:1.5,fund_ann:5,'阶段':'宽幅震荡',
  '慢档可用':true,d1_above:[level(price*1.05,5)],d1_below:[level(price*0.95,-5)],h4_above:[level(price*1.02,2)],h4_below:[level(price*0.98,-2)]});
const symbols=['BTC','ETH','SOL','XRP','BNB'];
const snapshot=(extra={})=>({day:beijingDay(),report:{ok:true,finished_at:new Date().toISOString()},
  summary:{'指标':Object.fromEntries(symbols.map((s,i)=>[s,metric(100+i)]))},four_lines:'L1-L4',
  ledger:{ok:true,updated_to:'2026-11-08',active:[{issue:31,symbol:'SOL',side:'做空',status:'挂单中',entry:105,stop:107.1,target:96.6}]},...extra});

test('快照上传后生成今日计划；主机凭密钥读取，没密钥拒绝',async()=>{
  const {desk,entries,restore}=rig();
  try {
    assert.equal((await pipe(desk,'/pipeline/plan')).status,200);
    assert.equal((await (await pipe(desk,'/pipeline/plan')).json()).plan,null);
    const res=await pipe(desk,'/pipeline/snapshot','POST',snapshot());
    assert.equal(res.status,200);
    const body=await res.json();
    assert.ok(body.plan_rules>0);
    const got=await (await pipe(desk,'/pipeline/plan')).json();
    assert.equal(got.ok,true);
    assert.equal(got.plan.rules.length,body.plan_rules);
    assert.deepEqual(Object.keys(got.plan.coins).sort(),[...symbols].sort());
    assert.ok(got.plan.expires_at>got.plan.built_at);
    assert.ok(got.now>0);
    assert.equal(got.plan.rules.some(r=>r.sym==='SOL'&&r.src==='ledger'&&r.kind==='near'),true);
    assert.equal(entries.get('plan:current').day,beijingDay());
    assert.equal((await pipe(desk,'/pipeline/plan','GET',null,'wrong')).status,403);
    assert.equal((await pipe(desk,'/pipeline/plan','POST',{})).status,404);
  } finally {restore();}
});

test('快照被拒收（日期不对）时不动已有计划',async()=>{
  const {desk,entries,restore}=rig();
  try {
    entries.set('plan:current',{marker:'old',rules:[],coins:{},built_at:1,expires_at:2});
    assert.equal((await pipe(desk,'/pipeline/snapshot','POST',snapshot({day:'2020-01-01'}))).status,400);
    assert.equal(entries.get('plan:current').marker,'old');
  } finally {restore();}
});

test('Telegram /plan：总览、单币；只认审核人',async()=>{
  const {desk,sent,restore}=rig();
  try {
    await hook(desk,1,'/plan');
    assert.match(sent.at(-1).text,/还没有监控计划/);
    await pipe(desk,'/pipeline/snapshot','POST',snapshot());
    await hook(desk,2,'/plan');
    assert.match(sent.at(-1).text,/今日监控计划：.*5 个币/);
    await hook(desk,3,'/plan sol');
    assert.match(sent.at(-1).text,/SOL 共 \d+ 条/);
    assert.match(sent.at(-1).text,/价格接近 105（±1%）｜台账挂单进场价/);
    const count=sent.length;
    const other=await desk.fetch(new Request('https://desk.test/telegram/webhook',{method:'POST',
      headers:{'Content-Type':'application/json','X-Telegram-Bot-Api-Secret-Token':'hook'},
      body:JSON.stringify({update_id:4,message:{chat:{id:999,type:'private'},from:{id:999},text:'/plan'}})}));
    assert.equal(other.status,200);
    assert.equal(sent.length,count);   // 非审核人的 /plan 没有任何回复
  } finally {restore();}
});

test('台账任务写入成功后，新挂单进入计划；失败或没有新单时不动计划',async()=>{
  const {desk,entries,env,restore}=rig();
  try {
    await pipe(desk,'/pipeline/snapshot','POST',snapshot({ledger:{ok:true,active:[]}}));
    assert.equal(entries.get('plan:current').rules.some(r=>r.sym==='SOL'&&r.kind==='near'),false);
    const day=beijingDay();
    const job={id:`ledger:${day}:1`,type:'ledger',day,status:'leased',attempts:1,
      payload:{day,issue:31,order:{symbol:'SOL',size:0.2,entry:105,stop:107.1,target:96.6},proposals:[]}};
    entries.set('job:'+job.id,job);
    entries.set('jobs-open',[job.id]);
    const flow=desk.flow(env);
    await flow.completeJob(job.id,{ok:true,result:{actions:['新增']}});
    const near=entries.get('plan:current').rules.find(r=>r.sym==='SOL'&&r.kind==='near');
    assert.equal(near.src,'ledger');
    assert.match(near.note,/第31期/);

    const before=JSON.stringify(entries.get('plan:current'));
    const job2={...job,id:`ledger:${day}:2`,payload:{day,issue:32,order:null,proposals:[]}};
    entries.set('job:'+job2.id,job2);
    await flow.completeJob(job2.id,{ok:true,result:{actions:[]}});
    assert.equal(JSON.stringify(entries.get('plan:current')),before);
    const job3={...job,id:`ledger:${day}:3`,payload:{day,issue:33,order:{symbol:'ETH',size:0.1,entry:120,stop:122,target:110},proposals:[]}};
    entries.set('job:'+job3.id,job3);
    await flow.completeJob(job3.id,{ok:false,error:'Notion 失败'});
    assert.equal(JSON.stringify(entries.get('plan:current')),before);
  } finally {restore();}
});
