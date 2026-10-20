import test from 'node:test';
import assert from 'node:assert/strict';
import {TradeDesk} from '../cloud/worker.mjs';
import {beijingDay} from '../calendar.mjs';

const DAY='2026-10-21';
const BODY='今天我挂一张$ETH远档空单。进场限价2,585.57，距现价+4.54%；止损2,637.28，按日线收盘判断；目标2,378.72，盈亏比1:4，0.12仓，有效期到10月31日。成交前日线收盘站上50日均线（约2,553.10）则作废。';
const EN="Today I'm placing a far-away $ETH short limit order. Entry limit 2,585.57, +4.54% from spot; stop 2,637.28 on a daily close; target 2,378.72, reward-to-risk 1:4, 0.12 size, valid until Oct 31. The order is voided if the daily close gets above the 50-day moving average (about 2,553.10) before it fills.";
const ORDER={symbol:'ETH',side:'空',entry:2585.57,stop:2637.28,target:2378.72,dist_pct:4.54,stop_pct:2.0,ratio:4,ratio_text:'1:4',
  size:0.1167,size_text:'0.12仓',amount_usd:233,expires:'10月31日',cancel_ma50:2553.1,r2:2482.15,r3:2430.44};
const SNAPSHOT={day:DAY,ledger:{ok:true,updated_to:'2026-10-16',updated_issue:35,equity_usd:10683.72,cumulative_pnl_usd:683.72,
  counts:{closed:17},closed_recent:[],active:[],proposals:[]},poster_facts:{ok:true,leg:{day:112},alt:{day:54}},
  summary:{'指标':{ETH:{price:2468.2}}}};

function rig(extraEnv={}) {
  const entries=new Map();let alarm=null,tail=Promise.resolve();
  const storage={get:async k=>structuredClone(entries.get(k)),put:async(k,v)=>entries.set(k,structuredClone(v)),
    delete:async k=>entries.delete(k),transaction:fn=>{const run=tail.then(()=>fn(storage));tail=run.catch(()=>{});return run;},
    setAlarm:async t=>{alarm=t;},deleteAlarm:async()=>{alarm=null;}};
  const env={ADMIN_PASSWORD:'pw',TOKEN_ENCRYPTION_KEY:'k'.repeat(40),EXPECTED_X_USERNAME:'Uncle_Onchain',
    TELEGRAM_WEBHOOK_SECRET:'hook',TELEGRAM_BOT_TOKEN:'tg',TELEGRAM_OWNER_ID:'123',PIPELINE_SECRET:'pipe',
    APINEBULA_API_KEY:'sk-x',TRADE_LOG_URL:'https://notion.example/ledger',PUBLISH_ENABLED:'false',...extraEnv};
  const desk=new TradeDesk({storage},env);
  const calls={telegram:[],docs:[],nebula:[],github:[]};
  const old=globalThis.fetch;
  globalThis.fetch=async(url,options={})=>{
    const u=String(url);
    if(u.includes('apinebula.ai')) {calls.nebula.push(JSON.parse(options.body));
      return Response.json({choices:[{message:{content:EN},finish_reason:'stop'}]});}
    if(u.includes('api.github.com')) {calls.github.push({url:u,headers:options.headers});
      if(extraEnv.GITHUB_FAIL) throw Error('boom');
      return Response.json({});}
    if(u.endsWith('/sendDocument')) {const f=options.body;calls.docs.push({name:f.get('document').name,caption:f.get('caption'),chat:f.get('chat_id')});
      return Response.json({ok:true,result:{document:{file_id:'F'+calls.docs.length}}});}
    if(u.includes('api.telegram.org')) {calls.telegram.push({method:u.split('/').pop(),...JSON.parse(options.body)});return Response.json({ok:true});}
    throw Error('unexpected fetch '+u);
  };
  return {desk,entries,env,calls,alarm:()=>alarm,restore:()=>{globalThis.fetch=old;}};
}
const bearer={Authorization:'Bearer pipe'};
const callback=(id,data)=>new Request('https://desk.test/telegram/webhook',{method:'POST',
  headers:{'X-Telegram-Bot-Api-Secret-Token':'hook','Content-Type':'application/json'},
  body:JSON.stringify({update_id:1,callback_query:{id,from:{id:123},message:{chat:{id:123}},data}})});
const seed=async r=>{
  r.entries.set('draft:'+DAY,{status:'pending_text_review',content:`审核正文：${BODY}\n\n送审备注：可发`,order:ORDER,order_allowed:true,model:'m'});
  r.entries.set('snapshot:'+DAY,SNAPSHOT);
};
const sent=r=>r.calls.telegram.filter(c=>c.method==='sendMessage').map(c=>c.text);

test('任务接口必须带流水线密钥，路径和方法受限',async()=>{
  const r=rig();
  try {
    assert.equal((await r.desk.fetch(new Request('https://desk.test/pipeline/jobs/next'))).status,403);
    assert.equal((await r.desk.fetch(new Request('https://desk.test/pipeline/jobs/next',{headers:{Authorization:'Bearer wrong'}}))).status,403);
    const empty=await r.desk.fetch(new Request('https://desk.test/pipeline/jobs/next',{headers:bearer}));
    assert.deepEqual((await empty.json()).job,null);
    assert.equal((await r.desk.fetch(new Request('https://desk.test/pipeline/jobs/..%2Fadmin/complete',{method:'POST',headers:bearer,body:'{}'}))).status,404);
    assert.equal((await r.desk.fetch(new Request('https://desk.test/pipeline/jobs/poster:2026-10-21:1/complete',{method:'GET',headers:bearer}))).status,404);
  } finally {r.restore();}
});
test('从Telegram按钮到终审：文字通过→正式稿→出图→4张图→按钮→终审→台账任务',async()=>{
  const r=rig({GITHUB_DISPATCH_TOKEN:'ghp_x',GITHUB_FAIL:'1'});
  try {
    await seed(r);
    assert.equal((await r.desk.fetch(callback('c1',`text_ok:${DAY}`))).status,200);
    await r.desk.fetch(callback('c1',`text_ok:${DAY}`));          // webhook redelivery
    await r.desk.fetch(callback('c2',`text_ok:${DAY}`));          // second tap
    assert.equal(r.calls.nebula.length,1);
    assert.equal(r.calls.github.length,1,'立即触发工作流，失败也不影响');
    assert.match(r.calls.github[0].url,/workflows\/jobs\.yml\/dispatches$/);
    assert.equal(r.calls.telegram.filter(c=>c.method==='answerCallbackQuery').at(-1).text,'该稿件已经处理');

    const next=await (await r.desk.fetch(new Request('https://desk.test/pipeline/jobs/next',{headers:bearer}))).json();
    assert.equal(next.job.type,'poster');
    assert.equal(next.job.payload.order.entry,2585.57);
    assert.equal(next.job.payload.formal.en,EN);
    const id=encodeURIComponent(next.job.id);
    const names=Object.values((await r.desk.ctx.storage.get('flow:'+DAY)).names);
    for(const name of names) {
      const res=await r.desk.fetch(new Request(`https://desk.test/pipeline/jobs/${id}/file?name=${encodeURIComponent(name)}`,
        {method:'POST',headers:{...bearer,'Content-Type':'image/png'},body:new Uint8Array(4000)}));
      assert.equal(res.status,200);
    }
    assert.deepEqual(r.calls.docs.map(d=>d.name).sort(),[...names].sort());
    assert.equal(r.calls.docs[0].chat,'123');
    const done=await r.desk.fetch(new Request(`https://desk.test/pipeline/jobs/${id}/complete`,{method:'POST',headers:bearer,body:JSON.stringify({ok:true})}));
    assert.equal(done.status,200);
    const last=r.calls.telegram.filter(c=>c.method==='sendMessage').at(-1);
    assert.match(JSON.stringify(last.reply_markup),/图片与正式稿通过.*退回修改/);

    await r.desk.fetch(callback('c3',`final_ok:${DAY}:1`));
    await r.desk.fetch(callback('c4',`final_ok:${DAY}:1`));
    const ledger=await (await r.desk.fetch(new Request('https://desk.test/pipeline/jobs/next',{headers:bearer}))).json();
    assert.equal(ledger.job.type,'ledger');
    assert.equal(ledger.job.payload.conditions.length,4);
    assert.equal((await r.desk.ctx.storage.get('flow:'+DAY)).status,'final_approved');
    assert.equal(sent(r).filter(t=>t.startsWith('图片与正式稿已通过')).length,1);
    await r.desk.alarm();   // alarm entry point works with the merged env
  } finally {r.restore();}
});
test('终审通过后今天不能再重写送审稿',async()=>{
  const r=rig();
  try {
    const day=beijingDay();
    r.entries.set('hotspots:'+day,{day,parts:['x'],submitted:Date.now()});
    r.entries.set('flow:'+day,{day,status:'final_approved'});
    const res=await r.desk.fetch(new Request('https://desk.test/telegram/webhook',{method:'POST',
      headers:{'X-Telegram-Bot-Api-Secret-Token':'hook','Content-Type':'application/json'},
      body:JSON.stringify({update_id:5,message:{chat:{id:123,type:'private'},from:{id:123},text:'/draft'}})}));
    assert.equal(res.status,200);
    assert.match(sent(r).at(-1),/不能再重写/);
    assert.equal(r.calls.nebula.length,0);
  } finally {r.restore();}
});
test('其他人点按钮无效',async()=>{
  const r=rig();
  try {
    await seed(r);
    const req=new Request('https://desk.test/telegram/webhook',{method:'POST',
      headers:{'X-Telegram-Bot-Api-Secret-Token':'hook','Content-Type':'application/json'},
      body:JSON.stringify({update_id:1,callback_query:{id:'z',from:{id:999},message:{chat:{id:999}},data:`text_ok:${DAY}`}})});
    await r.desk.fetch(req);
    assert.equal((await r.desk.ctx.storage.get('draft:'+DAY)).status,'pending_text_review');
  } finally {r.restore();}
});
