// 周日「下周清单」：数据包入口 → 出图任务 → 终审 → 周日 20:00 中英文各发一条。工作日流程的行为见 flow.test.mjs，不在这里重复。
import test from 'node:test';
import assert from 'node:assert/strict';
import {Flow,GRACE_MS,WEEKLY_CAPTION} from '../cloud/flow.mjs';
import {TradeDesk} from '../cloud/worker.mjs';
import {weeklyDay,weeklyDueAt,dueAt} from '../calendar.mjs';

const SUN='2026-10-25';
const ZH='【$BTC下周清单】\n\n下周要盯的外部事件：美联储议息决议。\n\n本周战绩：已了结 17 笔，账户收益 +6.84%。';
const EN='【$BTC Week Ahead】\n\nThe one external event to watch next week: FOMC rate decision.\n\nLast week: 17 closed trades, account return +6.84%.';
const PACK={kind:'weekly',day:SUN,week:{start:'2026-10-26',end:'2026-11-01'},warnings:['日历里还没有代币解锁数据，海报上这一栏会写「未接入」']};

function harness({now=Date.parse(SUN+'T15:00:00+08:00'),env={}}={}) {
  const entries=new Map();let alarm=null,tail=Promise.resolve();
  const storage={get:async k=>structuredClone(entries.get(k)),put:async(k,v)=>entries.set(k,structuredClone(v)),
    delete:async k=>entries.delete(k),transaction:fn=>{const run=tail.then(()=>fn(storage));tail=run.catch(()=>{});return run;},
    setAlarm:async t=>{alarm=t;},deleteAlarm:async()=>{alarm=null;},getAlarm:async()=>alarm};
  const log={telegram:[],uploads:[],x:[]};
  const state={now};
  const full={TELEGRAM_OWNER_ID:'123',PUBLISH_ENABLED:'false',EXPECTED_X_USERNAME:'Uncle_Onchain',
    TOKEN_ENCRYPTION_KEY:'k'.repeat(40),X_CLIENT_ID:'c',X_CLIENT_SECRET:'s',TRADE_LOG_URL:'https://notion.example/ledger',...env};
  const deps={now:()=>state.now,
    telegram:async(e,method,payload)=>{log.telegram.push({method,...payload});return {ok:true};},
    tgUpload:async(e,name,bytes,caption)=>{log.uploads.push({name,caption});return {result:{document:{file_id:'file_'+name}}};},
    tgDownload:async()=>new Uint8Array(3000),
    translate:async()=>{throw Error('周日流程不翻译');},
    provider:async(url,options)=>{log.x.push({url,options});
      if(url.includes('/users/me?')) return {data:{subscription_type:'premium'}};
      if(url.endsWith('/media/upload')) return {data:{id:'m'+log.x.length}};
      if(url.endsWith('/tweets')) return {data:{id:'tw'+log.x.length}};
      throw Error('unexpected '+url);}};
  const flow=new Flow({storage},full,deps);
  return {flow,entries,log,state,alarm:()=>alarm};
}
const texts=h=>h.log.telegram.filter(m=>m.method==='sendMessage').map(m=>m.text);
const png=()=>new Uint8Array(5000);
async function toAwaitingFinal(h) {
  const r=await h.flow.startWeekly(SUN,{pack:PACK,zh_post:ZH,en_post:EN});
  assert.equal(r.status,200);
  const job=await h.flow.nextJob();
  const flow=await h.flow.getFlow(SUN);
  for(const name of Object.values(flow.names)) assert.equal((await h.flow.receiveFile(job.id,name,png())).status,200);
  await h.flow.completeJob(job.id,{ok:true,result:{}});
  return {job,flow:await h.flow.getFlow(SUN)};
}

test('周日日历：只有周日，中英文都是北京时间 20:00，工作日接口不受影响',()=>{
  assert.equal(weeklyDay(SUN).ok,true);
  assert.equal(weeklyDay('2026-10-24').ok,false);
  assert.equal(weeklyDay('2027-01-03').ok,false,'未核验的年份拒绝');
  assert.equal(weeklyDueAt(SUN,'zh'),Date.parse(SUN+'T20:00:00+08:00'));
  assert.equal(weeklyDueAt(SUN,'en'),Date.parse(SUN+'T20:00:00+08:00'));
  assert.throws(()=>weeklyDueAt('2026-10-26','zh'));
  assert.throws(()=>dueAt(SUN,'zh'),'工作日的发布时间对周日仍然抛错');
});

test('数据包进来：只排出图任务，海报名固定为中英各一张，不翻译不读台账',async()=>{
  const h=harness();
  const r=await h.flow.startWeekly(SUN,{pack:PACK,zh_post:ZH,en_post:EN});
  assert.equal(r.status,200);
  const flow=await h.flow.getFlow(SUN);
  assert.equal(flow.kind,'weekly');
  assert.equal(flow.status,'poster_pending');
  assert.deepEqual(Object.keys(flow.names).sort(),['en_desktop','zh_desktop']);
  assert.equal(flow.names.zh_desktop,`poster_zh_周日下周清单_${SUN}.png`);
  const job=await h.flow.nextJob();
  assert.equal(job.type,'poster');
  assert.equal(job.payload.kind,'weekly');
  assert.equal(job.id,`poster:${SUN}:1`);
});

test('重复送来数据包：在处理中或已通过的不重复接收；退回后可以重新送',async()=>{
  const h=harness();
  assert.equal((await h.flow.startWeekly(SUN,{pack:PACK,zh_post:ZH,en_post:EN})).status,200);
  assert.equal((await h.flow.startWeekly(SUN,{pack:PACK,zh_post:ZH,en_post:EN})).status,409);
  await h.flow.nextJob();
  const flow=await h.flow.getFlow(SUN);
  await h.flow.s.put('flow:'+SUN,{...flow,status:'final_rejected'});
  const again=await h.flow.startWeekly(SUN,{pack:PACK,zh_post:ZH,en_post:EN});
  assert.equal(again.status,200);
  assert.equal(again.rev,2);
});

test('不是周日的日子不接收',async()=>{
  const h=harness();
  const r=await h.flow.startWeekly('2026-10-24',{pack:PACK,zh_post:ZH,en_post:EN});
  assert.equal(r.status,400);
  assert.equal(await h.flow.getFlow('2026-10-24'),undefined);
});

test('出图完成：发固定帖子文字和程序提示，不要审核、自动排好并带取消按钮；不带台账链接，标题不是「第N期」',async()=>{
  const h=harness();
  await toAwaitingFinal(h);
  const t=texts(h);
  assert.ok(t.some(x=>x.includes('周日下周清单 帖子文字')&&x.includes(WEEKLY_CAPTION)));
  assert.ok(!t.some(x=>x.includes('中文正式稿')),'周日不再有两份正式稿');
  assert.ok(t.some(x=>x.includes('代币解锁数据')),'程序提示要转给审核人');
  assert.ok(!t.some(x=>x.includes('notion.example')),'周日清单没有台账链接');
  assert.ok(!t.some(x=>/第undefined期|第null期/.test(x)));
  const last=h.log.telegram.at(-1);
  assert.deepEqual(last.reply_markup.inline_keyboard.flat().map(b=>b.callback_data),[`cancel_post:${SUN}:zh`]);
  assert.ok(!h.log.telegram.some(m=>JSON.stringify(m.reply_markup||'').includes('final_ok')),'周日没有审核按钮');
  const flow=await h.flow.getFlow(SUN);
  assert.equal(flow.status,'final_approved');
  assert.equal(flow.slots.zh.status,'scheduled');
  assert.equal(h.log.uploads.length,2);
  assert.ok(h.log.uploads.every(u=>u.caption.startsWith('周日下周清单')));
});

test('图片没收齐不能进终审',async()=>{
  const h=harness();
  await h.flow.startWeekly(SUN,{pack:PACK,zh_post:ZH,en_post:EN});
  const job=await h.flow.nextJob();
  const flow=await h.flow.getFlow(SUN);
  await h.flow.receiveFile(job.id,flow.names.zh_desktop,png());
  await h.flow.completeJob(job.id,{ok:true,result:{}});
  assert.equal((await h.flow.getFlow(SUN)).status,'poster_failed');
  assert.ok(texts(h).some(x=>x.includes('2张图片没有收齐')));
});

test('出图完成即自动排期：不写台账、不占期号，只排一条帖子在周日 20:00，闹钟指向 20:00',async()=>{
  const h=harness();
  await toAwaitingFinal(h);
  const flow=await h.flow.getFlow(SUN);
  assert.equal(flow.slots.zh.due,Date.parse(SUN+'T20:00:00+08:00'));
  assert.equal(flow.slots.en,undefined,'周日只有一条帖子');
  assert.deepEqual(flow.slots.zh.targets,['x']);
  assert.equal(flow.ledger_job,undefined);
  assert.equal((await h.flow.s.get('issues'))?.[SUN],undefined);
  assert.equal(h.alarm(),Date.parse(SUN+'T20:00:00+08:00'));
  assert.ok(texts(h).some(x=>x.includes('不需要审核')&&x.includes('不写台账')&&x.includes('20:00')));
});

test('取消按钮：到点前取消，不发布；已经开始发布就取消不了',async()=>{
  const h=harness();
  await toAwaitingFinal(h);
  assert.equal(await h.flow.cancelPost(SUN,'zh'),true);
  assert.equal((await h.flow.getFlow(SUN)).slots.zh.status,'skipped');
  h.state.now=Date.parse(SUN+'T20:00:05+08:00');
  await h.flow.alarm();
  assert.equal(h.log.x.length,0);
  assert.equal(texts(h).filter(x=>x.includes('【演练')).length,0);
  assert.equal(await h.flow.cancelPost(SUN,'zh'),false);
});

test('到点演练：一条帖子带两张海报，不调用X，闹钟重复触发不重复',async()=>{
  const h=harness();
  await toAwaitingFinal(h);
  await h.flow.finalApprove(SUN,1);
  h.state.now=Date.parse(SUN+'T20:00:05+08:00');
  await h.flow.alarm();
  await h.flow.alarm();
  const dry=texts(h).filter(x=>x.includes('【演练，未发推】'));
  assert.equal(dry.length,1);
  assert.ok(dry[0].includes('周日下周清单周日海报帖')&&dry[0].includes('附2张图')&&dry[0].includes(WEEKLY_CAPTION));
  assert.equal(h.log.x.length,0);
});
test('开关打开：一条帖子，固定文案，带中英两张海报，只有一个 cashtag',async()=>{
  const h=harness({env:{PUBLISH_ENABLED:'true'}});
  await h.flow.s.put('x-account',{id:'1',username:'Uncle_Onchain'});
  h.flow.d.provider=async(url,options)=>{
    h.log.x.push({url,body:options?.body});
    if(url.includes('/users/me?')) return {data:{subscription_type:'premium'}};
    if(url.endsWith('/media/upload')) return {data:{id:'m'+h.log.x.length}};
    if(url.endsWith('/tweets')) return {data:{id:'tw'+h.log.x.length}};
    if(url.includes('oauth2/token')) return {access_token:'a',refresh_token:'b',expires_in:7200};
    throw Error('unexpected '+url);
  };
  const {seal}=await import('../cloud/security.mjs');
  await h.flow.s.put('x-tokens',await seal({access_token:'a',refresh_token:'b',obtained_at:h.state.now,expires_in:7200},'k'.repeat(40)));
  await toAwaitingFinal(h);
  await h.flow.finalApprove(SUN,1);
  h.state.now=Date.parse(SUN+'T20:00:05+08:00');
  await h.flow.alarm();
  await h.flow.alarm();
  const posts=h.log.x.filter(c=>c.url.endsWith('/tweets'));
  assert.equal(posts.length,1);
  const body=JSON.parse(posts[0].body);
  assert.equal(body.text,WEEKLY_CAPTION);
  assert.equal(body.media.media_ids.length,2);
  assert.equal(h.log.x.filter(c=>c.url.endsWith('/media/upload')).length,2);
  assert.equal((await h.flow.getFlow(SUN)).slots.zh.status,'posted');
});

test('错过 20:00 超过 60 分钟：问你要不要补发，不擅自补',async()=>{
  const h=harness({now:Date.parse(SUN+'T20:00:00+08:00')+GRACE_MS+60000});
  await toAwaitingFinal(h);
  const flow=await h.flow.getFlow(SUN);
  assert.equal(flow.slots.zh.status,'missed');
  assert.ok(texts(h).some(x=>x.includes('周日下周清单')===false&&x.includes('要现在补发吗')&&x.includes('20:00')));
  assert.equal(h.log.x.length,0);
});

// ---- Worker 入口 ----
function rig(extraEnv={}) {
  const entries=new Map();let alarm=null,tail=Promise.resolve();
  const storage={get:async k=>structuredClone(entries.get(k)),put:async(k,v)=>entries.set(k,structuredClone(v)),
    delete:async k=>entries.delete(k),transaction:fn=>{const run=tail.then(()=>fn(storage));tail=run.catch(()=>{});return run;},
    setAlarm:async t=>{alarm=t;},deleteAlarm:async()=>{alarm=null;}};
  const env={ADMIN_PASSWORD:'pw',TOKEN_ENCRYPTION_KEY:'k'.repeat(40),EXPECTED_X_USERNAME:'Uncle_Onchain',TELEGRAM_WEBHOOK_SECRET:'hook',
    TELEGRAM_BOT_TOKEN:'tg',TELEGRAM_OWNER_ID:'123',PIPELINE_SECRET:'pipe',PUBLISH_ENABLED:'false',...extraEnv};
  const desk=new TradeDesk({storage},env);
  const sent=[];
  const oldFetch=globalThis.fetch,oldNow=Date.now;
  globalThis.fetch=async(url,options={})=>{
    const u=String(url);
    if(u.includes('api.telegram.org')) {sent.push({method:u.split('/').pop(),...(typeof options.body==='string'?JSON.parse(options.body):{})});return Response.json({ok:true});}
    throw Error('unexpected fetch '+u);
  };
  Date.now=()=>Date.parse(SUN+'T15:00:00+08:00');
  return {desk,entries,sent,restore:()=>{globalThis.fetch=oldFetch;Date.now=oldNow;}};
}
const post=(body,headers={Authorization:'Bearer pipe'})=>new Request('https://desk.test/pipeline/weekly',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(body)});

test('/pipeline/weekly：要流水线密钥；日期必须是今天；数据包格式检查',async()=>{
  const r=rig();
  try {
    const ok={day:SUN,pack:PACK,zh_post:ZH,en_post:EN};
    assert.equal((await r.desk.fetch(post(ok,{}))).status,403);
    assert.equal((await r.desk.fetch(post(ok,{Authorization:'Bearer wrong'}))).status,403);
    assert.equal((await r.desk.fetch(post({...ok,day:'2026-10-18',pack:{...PACK,day:'2026-10-18'}}))).status,400);
    assert.equal((await r.desk.fetch(post({...ok,pack:{...PACK,kind:'x'}}))).status,400);
    assert.equal(r.entries.get('flow:'+SUN),undefined,'校验没过不能落库');
    const res=await r.desk.fetch(post(ok));
    assert.equal(res.status,200);
    assert.equal(r.entries.get('flow:'+SUN).kind,'weekly');
    assert.equal((await r.desk.fetch(post(ok))).status,409);
    assert.ok(r.sent.some(m=>m.method==='sendMessage'&&String(m.text).includes('周日下周清单')));
  } finally {r.restore();}
});

test('终审按钮在周日没有文字稿时也能用',async()=>{
  const r=rig();
  try {
    await r.desk.fetch(post({day:SUN,pack:PACK,zh_post:ZH,en_post:EN}));
    const flow=r.entries.get('flow:'+SUN);
    r.entries.set('flow:'+SUN,{...flow,status:'awaiting_final'});
    const res=await r.desk.fetch(new Request('https://desk.test/telegram/webhook',{method:'POST',
      headers:{'X-Telegram-Bot-Api-Secret-Token':'hook','Content-Type':'application/json'},
      body:JSON.stringify({update_id:5,callback_query:{id:'cb1',from:{id:123},message:{chat:{id:123}},data:`final_ok:${SUN}:${flow.rev}`}})}));
    assert.equal(res.status,200);
    assert.equal(r.entries.get('flow:'+SUN).status,'final_approved');
  } finally {r.restore();}
});
