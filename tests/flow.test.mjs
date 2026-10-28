import test from 'node:test';
import assert from 'node:assert/strict';
import {Flow,GRACE_MS,WATCHDOG_MS} from '../cloud/flow.mjs';
import {stripTradeLog,limitCashtags,numberTokens,numberDiff,englishProblems,issueNumber,splitDraft,ledgerPayload,posterPayload,bodyProblems,posterNames}
  from '../cloud/formal.mjs';
import {seal} from '../cloud/security.mjs';
import {dueAt} from '../calendar.mjs';

const DAY='2026-10-21'; // a Wednesday, publishing day
const BODY='今天我挂一张$ETH远档空单。进场限价2,585.57，距现价+4.54%；止损2,637.28，按日线收盘判断；目标2,378.72，盈亏比1:4，0.12仓，有效期到10月31日。成交前日线收盘站上50日均线（约2,553.10）则作废。';
const EN="Today I'm placing a far-away $ETH short limit order. Entry limit 2,585.57, +4.54% from spot; stop 2,637.28 on a daily close; target 2,378.72, reward-to-risk 1:4, 0.12 size, valid until Oct 31. The order is voided if the daily close gets above the 50-day moving average (about 2,553.10) before it fills.";
const ORDER={symbol:'ETH',side:'空',entry:2585.57,stop:2637.28,target:2378.72,dist_pct:4.54,stop_pct:2.0,ratio:4,ratio_text:'1:4',
  size:0.1167,size_text:'0.12仓',amount_usd:233,expires:'10月31日',cancel_ma50:2553.1,r2:2482.15,r3:2430.44,path:'路A趋势已破'};
const LEDGER={ok:true,updated_to:'2026-10-16',updated_issue:35,equity_usd:10683.72,cumulative_pnl_usd:683.72,
  counts:{closed:17,open:0,pending:1},closed_recent:[],active:[],
  proposals:[{type:'expire',issue:'35',symbol:'ETH',text:'x'},{type:'review',text:'y'},{type:'fill',issue:'34',symbol:'BTC',date:'2026-10-20',text:'z'}]};
const FACTS={ok:true,leg:{day:112},alt:{day:54}};
const SNAPSHOT={day:DAY,ledger:LEDGER,poster_facts:FACTS,summary:{'指标':{ETH:{price:2468.2}}}};

function harness({now=Date.parse(DAY+'T10:00:00+08:00'),env={},translate,provider}={}) {
  const entries=new Map();let alarm=null;
  // A Durable Object transaction is atomic against other requests; the mutex gives the fake storage the same property.
  let tail=Promise.resolve();
  const storage={get:async k=>structuredClone(entries.get(k)),put:async(k,v)=>entries.set(k,structuredClone(v)),
    delete:async k=>entries.delete(k),
    transaction:fn=>{const run=tail.then(()=>fn(storage));tail=run.catch(()=>{});return run;},
    setAlarm:async t=>{alarm=t;},deleteAlarm:async()=>{alarm=null;},getAlarm:async()=>alarm};
  const log={telegram:[],uploads:[],translate:[],x:[],downloads:[]};
  const state={now};
  const full={TELEGRAM_OWNER_ID:'123',TRADE_LOG_URL:'https://notion.example/ledger',PUBLISH_ENABLED:'false',
    EXPECTED_X_USERNAME:'Uncle_Onchain',TOKEN_ENCRYPTION_KEY:'k'.repeat(40),X_CLIENT_ID:'c',X_CLIENT_SECRET:'s',...env};
  const deps={now:()=>state.now,
    telegram:async(e,method,payload)=>{log.telegram.push({method,...payload});return {ok:true};},
    tgUpload:async(e,name,bytes,caption)=>{log.uploads.push({name,size:bytes.byteLength,caption});return {result:{document:{file_id:'file_'+name}}};},
    tgDownload:async(e,id)=>{log.downloads.push(id);return new Uint8Array(3000);},
    translate:translate||(async(e,text)=>{log.translate.push(text);return {content:EN,model:'m'};}),
    provider:provider||(async(url,options)=>{log.x.push({url,options});
      if(url.includes('/users/me?')) return {data:{subscription_type:'premium'}};
      if(url.endsWith('/media/upload')) return {data:{id:'m'+log.x.length}};
      if(url.endsWith('/tweets')) return {data:{id:'tw1'}};
      throw Error('unexpected '+url);})};
  const flow=new Flow({storage},full,deps);
  return {flow,entries,log,state,env:full,alarm:()=>alarm};
}
async function seed(h,{order=ORDER,body=BODY,allowed=true,snapshot=SNAPSHOT}={}) {
  await h.flow.s.put('draft:'+DAY,{status:'text_approved',content:`审核正文：${body}\n\n送审备注：可发`,order,order_allowed:allowed});
  await h.flow.s.put('snapshot:'+DAY,snapshot);
}
const texts=h=>h.log.telegram.filter(m=>m.method==='sendMessage').map(m=>m.text);
const rows=m=>m.reply_markup?.inline_keyboard?.flat().map(b=>b.callback_data)||[];
const png=()=>new Uint8Array(5000);
async function toAwaitingFinal(h) {
  await seed(h);
  await h.flow.startFormal(DAY);
  const job=await h.flow.nextJob();
  const flow=await h.flow.getFlow(DAY);
  for(const name of Object.values(flow.names)) assert.equal((await h.flow.receiveFile(job.id,name,png())).status,200);
  await h.flow.completeJob(job.id,{ok:true,result:{}});
  return flow;
}

test('数字规范化：中文日期/亿/万与英文月份/million/thousand 等价',()=>{
  assert.deepEqual(numberTokens('10月31日 约5.27亿美元 7.6万 2,585.57 +4.54% 1:4','zh'),
    numberTokens('Oct 31, about $527 million, 76,000, 2,585.57, +4.54%, 1:4','en'));
  assert.equal(numberDiff('止损2,637.28','stop 2,637.28').ok,true);
  assert.equal(numberDiff('止损2,637.28','stop 2,637.82').ok,false);
  assert.equal(numberDiff('50日均线','50-day average').ok,true);
  assert.deepEqual(numberTokens('看 https://x.com/a/123 链接 15天','zh'),['15']);
});
test('K/千缩写与区间：80K 与 80,000 等价，区间共用单位',()=>{
  assert.equal(numberDiff('BTC在80K、81K之间','BTC between 80,000 and 81,000').ok,true);
  assert.equal(numberDiff('BTC在80-81K之间','BTC between 80,000 and 81,000').ok,true);
  assert.equal(numberDiff('BTC在80K-81K之间','BTC between $80K-$81K').ok,true);
  assert.equal(numberDiff('回到8万','back to 80K').ok,true);
  assert.equal(numberDiff('3千万美元','x').ok,false);
  assert.equal(numberDiff('80K','80').ok,false);
  assert.equal(numberDiff('5M流入','5 million inflow').ok,true);
});
test('英文稿自检：中文字符、缺数字、多数字、缺币种标记',()=>{
  assert.deepEqual(englishProblems(BODY,EN),[]);
  assert.ok(englishProblems(BODY,EN+' 好').some(p=>p.includes('中文')));
  assert.ok(englishProblems(BODY,EN.replace('2,637.28','2,637')).some(p=>p.includes('缺少')));
  assert.ok(englishProblems(BODY,EN+' In 2026 it works.').some(p=>p.includes('多出')));
  assert.ok(englishProblems(BODY,EN.replace('$ETH','ETH')).some(p=>p.includes('币种')));
});
test('期号：按台账更新日之后的发布日累加，已更新到今天则不加',()=>{
  // 期号只数真正发出去的，不数日历：台账最后一期是周五第35期，周一、周二没发，今天就是第36期
  assert.equal(issueNumber(LEDGER,'2026-10-21'),36);
  assert.equal(issueNumber(LEDGER,'2026-10-19'),36);
  assert.equal(issueNumber({...LEDGER,updated_to:'2026-10-21'},'2026-10-21'),35,'台账已更新到今天，就是台账的号');
  assert.equal(issueNumber(LEDGER,'2026-10-21',{'2026-10-21':36}),36,'今天已终审通过的号不再变');
  assert.equal(issueNumber(LEDGER,'2026-10-22',{'2026-10-21':36}),37,'昨天没新单、台账没更新，今天也接着昨天的号往后数');
  assert.equal(issueNumber(LEDGER,'2026-10-26',{'2026-10-21':36,'2026-10-22':37}),38,'中间停发的日子不占号');
  assert.equal(issueNumber({ok:false},DAY),null);
});
test('splitDraft与正文校验',()=>{
  assert.deepEqual(splitDraft('审核正文：甲\n\n送审备注：乙'),{body:'甲',notes:'乙'});
  assert.deepEqual(bodyProblems(BODY,{order:ORDER,order_allowed:true}),[]);
  assert.ok(bodyProblems(BODY.replace('2,637.28','2,637.82'),{order:ORDER,order_allowed:true}).length);
  assert.ok(bodyProblems('今天不开新单[待核实]',{order:null,order_allowed:false}).length);
});
test('台账任务：只带真正的状态变化，新单带3条条件和程序写的备注',()=>{
  const payload=ledgerPayload({day:DAY,issue:37,draft:{order:ORDER},snapshot:SNAPSHOT});
  assert.equal(payload.proposals.length,2);
  assert.equal(payload.conditions.length,4);
  assert.ok(payload.conditions.some(c=>/先跌到目标/.test(c.condition)),'先跌到目标就作废也要登记');
  assert.match(payload.note,/先跌到目标/);
  assert.equal(payload.order.size,0.12);
  assert.match(payload.note,/2,482\.15/);
  assert.equal(ledgerPayload({day:DAY,issue:37,draft:{order:null},snapshot:{ledger:{proposals:[]}}}),null);
});
test('海报任务载荷缺台账或海报数据时直接拒绝',()=>{
  assert.throws(()=>posterPayload({day:DAY,issue:37,draft:{order:ORDER},snapshot:{ledger:{ok:false}},body:BODY,english:EN}),/台账/);
  assert.throws(()=>posterPayload({day:DAY,issue:37,draft:{order:ORDER},snapshot:{ledger:LEDGER,poster_facts:{ok:false}},body:BODY,english:EN}),/海报/);
  const p=posterPayload({day:DAY,issue:37,draft:{order:ORDER},snapshot:SNAPSHOT,body:BODY,english:EN});
  assert.equal(p.prices.ETH,2468.2);
  assert.equal(p.order.entry,2585.57);
  assert.deepEqual(p.ledger.fills,[{issue:'34',symbol:'BTC'}],'fixture already carries one fill proposal');
  const withFill=posterPayload({day:DAY,issue:37,draft:{order:ORDER},body:BODY,english:EN,
    snapshot:{...SNAPSHOT,ledger:{...LEDGER,proposals:[{type:'fill',issue:36,symbol:'SOL'},{type:'expire',issue:30,symbol:'ETH'}]}}});
  assert.deepEqual(withFill.ledger.fills,[{issue:'36',symbol:'SOL'}],'已核出但还没写入台账的成交，海报按持仓中画');
});

test('文字稿通过后：正式稿数字一致，海报任务入队，重复触发不重复',async()=>{
  const h=harness();
  await seed(h);
  await Promise.all([h.flow.startFormal(DAY),h.flow.startFormal(DAY)]);
  await h.flow.startFormal(DAY);
  assert.equal(h.log.translate.length,1,'只翻译一次');
  const flow=await h.flow.getFlow(DAY);
  assert.equal(flow.status,'poster_pending');
  assert.equal(flow.issue,36,'接着10-16的第35期；10-19、10-20没发布，不占期号');
  assert.equal(flow.formal.zh_post,`【热币分析·第36期（${DAY}）】\n`+BODY);
  assert.equal(flow.formal.en_post,`【Hot Coin Desk · Issue 36 (${DAY})】\n`+EN);
  const peek=await h.flow.nextJob(true);
  assert.equal(peek.id,'poster:'+DAY+':1');
  assert.equal(await h.flow.enqueue({id:peek.id,type:'poster',day:DAY,payload:{}}),false);
});
test('英文稿数字对不上：重试后仍不一致就不出图，给重试按钮；改好后能继续',async()=>{
  let n=0;
  const h=harness({translate:async()=>({content:++n<4?EN.replace('2,637.28','2,637.82'):EN,model:'m'})});
  await seed(h);
  await h.flow.startFormal(DAY);
  let flow=await h.flow.getFlow(DAY);
  assert.equal(flow.status,'formal_failed');
  assert.equal(n,3);
  assert.equal(await h.flow.nextJob(true),null);
  const failure=h.log.telegram.at(-1);
  assert.match(failure.text,/没有发任何东西/);
  assert.deepEqual(rows(failure),['formal_retry:'+DAY]);
  await h.flow.startFormal(DAY);
  flow=await h.flow.getFlow(DAY);
  assert.equal(flow.status,'poster_pending');
  assert.equal(flow.rev,2);
});
test('今天的快照还没上传：说清楚是取数没跑完，不是台账坏了，并给重试按钮',async()=>{
  const h=harness();
  await seed(h);
  await h.flow.s.delete('snapshot:'+DAY);
  await h.flow.startFormal(DAY);
  assert.equal((await h.flow.getFlow(DAY)).status,'formal_failed');
  const failure=h.log.telegram.at(-1);
  assert.match(failure.text,/行情快照还没有上传/);
  assert.match(failure.text,/文字稿不用重审/);
  assert.deepEqual(rows(failure),['formal_retry:'+DAY]);
});
test('已通过的正文与程序订单不一致时拦住',async()=>{
  const h=harness();
  await seed(h,{body:BODY.replace('2,637.28','2,637.82')});
  await h.flow.startFormal(DAY);
  assert.equal((await h.flow.getFlow(DAY)).status,'formal_failed');
  assert.equal(h.log.translate.length,0);
});
test('台账读不到时不猜期号',async()=>{
  const h=harness();
  await seed(h,{snapshot:{...SNAPSHOT,ledger:{ok:false}}});
  await h.flow.startFormal(DAY);
  assert.match((await h.flow.getFlow(DAY)).failure,/第几期/);
});
test('任务租约：同一时刻只有一个执行者，过期后可重领，次数有上限',async()=>{
  const h=harness();
  await seed(h);
  await h.flow.startFormal(DAY);
  const first=await h.flow.nextJob();
  assert.ok(first.payload.order);
  assert.equal(await h.flow.nextJob(),null);
  h.state.now+=13*60000;
  assert.ok(await h.flow.nextJob());
  h.state.now+=13*60000;
  assert.ok(await h.flow.nextJob());
  h.state.now+=13*60000;
  assert.equal(await h.flow.nextJob(),null,'第3次租约过期后不再发放');
});
test('图片入口校验：文件名、大小、任务状态、重复上传只发一次',async()=>{
  const h=harness();
  await seed(h);
  await h.flow.startFormal(DAY);
  const flow=await h.flow.getFlow(DAY);
  const id=flow.poster_job;
  assert.equal((await h.flow.receiveFile(id,flow.names.zh_desktop,png())).status,409,'未领取的任务不收图');
  await h.flow.nextJob();
  assert.equal((await h.flow.receiveFile(id,'evil.png',png())).status,400);
  assert.equal((await h.flow.receiveFile(id,flow.names.zh_desktop,new Uint8Array(10))).status,400);
  assert.equal((await h.flow.receiveFile(id,flow.names.zh_desktop,png())).status,200);
  assert.equal((await h.flow.receiveFile(id,flow.names.zh_desktop,png())).duplicate,true);
  assert.equal(h.log.uploads.length,1);
  assert.match(h.log.uploads[0].caption,/待终审，尚未发布/);
});
test('出图完成：发中英文正式稿和按钮，图片没收齐则失败，重复完成不重复发',async()=>{
  const h=harness();
  const flow=await toAwaitingFinal(h);
  assert.equal(h.log.uploads.length,4);
  const messages=texts(h);
  assert.ok(messages.some(t=>t.includes('中文正式稿')&&!t.includes('交易记录：')));
  assert.ok(messages.some(t=>t.includes('英文正式稿')&&!t.includes('Trade log:')));
  assert.ok(messages.some(t=>t.includes('台账链接')&&t.includes('https://notion.example/ledger')),'链接单独给你，供评论区使用');
  const buttons=h.log.telegram.at(-1);
  assert.deepEqual(rows(buttons),[`final_ok:${DAY}:1`,`final_reject:${DAY}:1`]);
  assert.match(JSON.stringify(buttons.reply_markup),/图片与正式稿通过/);
  const count=h.log.telegram.length;
  await h.flow.completeJob(flow.poster_job,{ok:true});
  assert.equal(h.log.telegram.length,count);
});
test('图片没收齐不能进入终审',async()=>{
  const h=harness();
  await seed(h);
  await h.flow.startFormal(DAY);
  const job=await h.flow.nextJob();
  const flow=await h.flow.getFlow(DAY);
  await h.flow.receiveFile(job.id,flow.names.zh_desktop,png());
  await h.flow.completeJob(job.id,{ok:true});
  assert.equal((await h.flow.getFlow(DAY)).status,'poster_failed');
  assert.ok(rows(h.log.telegram.at(-1)).includes('poster_retry:'+DAY));
});
test('出图失败后可重试，新任务编号递增，旧任务的完成回报被忽略',async()=>{
  const h=harness();
  await seed(h);
  await h.flow.startFormal(DAY);
  const job=await h.flow.nextJob();
  await h.flow.completeJob(job.id,{ok:false,error:'浏览器启动失败'});
  assert.match((await h.flow.getFlow(DAY)).failure,/浏览器启动失败/);
  await h.flow.retryPoster(DAY);
  await h.flow.retryPoster(DAY);
  const flow=await h.flow.getFlow(DAY);
  assert.equal(flow.poster_job,'poster:'+DAY+':2');
  assert.equal(flow.status,'poster_pending');
  assert.equal(await h.flow.completeJob(job.id,{ok:true}).then(r=>r.status),200);
  assert.equal((await h.flow.getFlow(DAY)).status,'poster_pending');
});
test('看门狗：25分钟没出图就告诉你，不悄悄等',async()=>{
  const h=harness();
  await seed(h);
  await h.flow.startFormal(DAY);
  assert.ok(h.alarm());
  h.state.now+=WATCHDOG_MS+1000;
  await h.flow.alarm();
  assert.equal((await h.flow.getFlow(DAY)).status,'poster_failed');
  assert.match(h.log.telegram.at(-1).text,/25分钟/);
});

test('终审通过：台账任务只入队一次，中英文各排一个时间点，闹钟指向20:00',async()=>{
  const h=harness();
  await toAwaitingFinal(h);
  assert.equal(await h.flow.finalApprove(DAY,1),true);
  assert.equal(await h.flow.finalApprove(DAY,1),false,'重复点击');
  assert.equal(await h.flow.finalReject(DAY,1),false);
  const flow=await h.flow.getFlow(DAY);
  assert.equal(flow.status,'final_approved');
  assert.equal(flow.slots.zh.status,'scheduled');
  assert.equal(flow.slots.en.due,dueAt(DAY,'en'));
  assert.equal(h.alarm(),dueAt(DAY,'zh'));
  const job=await h.flow.nextJob();
  assert.equal(job.type,'ledger');
  assert.equal(await h.flow.nextJob(),null);
  assert.ok(texts(h).at(-1).includes('演练'));
});
test('退回修改：不写台账、不排发布，可用原文字稿重出',async()=>{
  const h=harness();
  await toAwaitingFinal(h);
  assert.equal(await h.flow.finalReject(DAY,1),true);
  assert.equal(await h.flow.finalApprove(DAY,1),false);
  assert.equal(await h.flow.nextJob(true),null);
  assert.equal(h.alarm(),null);
  await h.flow.startFormal(DAY);
  assert.equal((await h.flow.getFlow(DAY)).rev,2);
});
test('终审按钮带期次，旧按钮点了不生效',async()=>{
  const h=harness();
  await toAwaitingFinal(h);
  assert.equal(await h.flow.finalApprove(DAY,0),false);
  assert.equal((await h.flow.getFlow(DAY)).status,'awaiting_final');
});
test('没有新单也没有状态变化时不排台账任务',async()=>{
  const h=harness();
  const body='今天不开新单。';
  await seed(h,{order:null,allowed:false,body,snapshot:{...SNAPSHOT,ledger:{...LEDGER,proposals:[]}}});
  h.flow.d.translate=async()=>({content:'No new order today.',model:'m'});
  await h.flow.startFormal(DAY);
  const job=await h.flow.nextJob();
  const flow=await h.flow.getFlow(DAY);
  for(const name of Object.values(flow.names)) await h.flow.receiveFile(job.id,name,png());
  await h.flow.completeJob(job.id,{ok:true});
  await h.flow.finalApprove(DAY,1);
  assert.equal(await h.flow.nextJob(true),null);
  assert.match(texts(h).at(-1),/不需要写入/);
});
test('台账写入结果：成功汇报，失败保留重试，重复回报不重复发消息',async()=>{
  const h=harness();
  await toAwaitingFinal(h);
  await h.flow.finalApprove(DAY,1);
  const job=await h.flow.nextJob();
  await h.flow.completeJob(job.id,{ok:false,error:'Notion HTTP 403'});
  assert.match(texts(h).at(-1),/没有成功/);
  const again=await h.flow.nextJob();
  assert.equal(again.id,job.id,'失败后重新排队');
  await h.flow.completeJob(job.id,{ok:true,result:{actions:['当前在场新增 ETH 第37期']}});
  const count=h.log.telegram.length;
  await h.flow.completeJob(job.id,{ok:true,result:{actions:[]}});
  assert.equal(h.log.telegram.length,count);
  assert.match(texts(h).at(-1),/台账已由程序写入/);
  assert.equal((await h.flow.getFlow(DAY)).ledger_status,'written');
});

test('到点演练：发全文预览但不调用X，闹钟重复触发也只演练一次',async()=>{
  const h=harness();
  await toAwaitingFinal(h);
  await h.flow.finalApprove(DAY,1);
  h.state.now=dueAt(DAY,'zh')+30000;
  await Promise.all([h.flow.alarm(),h.flow.alarm()]);
  await h.flow.alarm();
  const flow=await h.flow.getFlow(DAY);
  assert.equal(flow.slots.zh.status,'dry_run');
  assert.equal(flow.slots.en.status,'scheduled');
  assert.equal(h.log.x.length,0);
  assert.equal(texts(h).filter(t=>t.includes('【演练，未发推】')).length,1);
  assert.equal(h.alarm(),dueAt(DAY,'en'));
  h.state.now=dueAt(DAY,'en')+5000;
  await h.flow.alarm();
  assert.equal((await h.flow.getFlow(DAY)).slots.en.status,'dry_run');
  assert.equal(h.alarm(),null);
});
test('目标时间后60分钟内仍自动执行，超过60分钟才询问；补发/放弃只生效一次',async()=>{
  const within=harness({now:dueAt(DAY,'zh')+30*60000});
  await toAwaitingFinal(within);
  await within.flow.finalApprove(DAY,1);
  assert.equal((await within.flow.getFlow(DAY)).slots.zh.status,'scheduled');
  assert.equal(within.log.telegram.some(m=>rows(m).includes(`late_post:${DAY}:zh`)),false);
  await within.flow.alarm();
  assert.equal((await within.flow.getFlow(DAY)).slots.zh.status,'dry_run');

  const h=harness({now:dueAt(DAY,'zh')+GRACE_MS+60000});
  await toAwaitingFinal(h);
  await h.flow.finalApprove(DAY,1);
  let flow=await h.flow.getFlow(DAY);
  assert.equal(flow.slots.zh.status,'missed');
  assert.equal(flow.slots.en.status,'scheduled');
  const ask=h.log.telegram.find(m=>rows(m).includes(`late_post:${DAY}:zh`));
  assert.ok(ask);
  assert.deepEqual(rows(ask),[`late_post:${DAY}:zh`,`late_skip:${DAY}:zh`]);
  assert.equal(h.log.x.length,0);
  // an alarm that fires 61 minutes late for en also waits
  h.state.now=dueAt(DAY,'en')+GRACE_MS+60000;
  await h.flow.alarm();
  flow=await h.flow.getFlow(DAY);
  assert.equal(flow.slots.en.status,'missed');
  assert.equal(h.log.x.length,0);
  assert.equal(await h.flow.lateDecision(DAY,'zh',true),true);
  assert.equal(await h.flow.lateDecision(DAY,'zh',true),false);
  assert.equal(await h.flow.lateDecision(DAY,'zh',false),false);
  assert.equal(await h.flow.lateDecision(DAY,'en',false),true);
  flow=await h.flow.getFlow(DAY);
  assert.equal(flow.slots.zh.status,'dry_run');
  assert.equal(flow.slots.en.status,'skipped');
  assert.equal(h.log.x.length,0,'发布总开关关闭，补发也只演练');
});
test('非发布日不排时间',async()=>{
  const day='2026-10-24'; // Saturday
  const h=harness({now:Date.parse(day+'T10:00:00+08:00')});
  await h.flow.s.put('flow:'+day,{day,status:'awaiting_final',rev:1,issue:1,formal:{},names:{}});
  const lines=await h.flow.schedule(day);
  assert.match(lines[0],/不是发布日/);
  assert.equal(h.alarm(),null);
});

async function liveHarness(overrides={}) {
  const env={PUBLISH_ENABLED:'true',...overrides.env};
  const h=harness({env,provider:overrides.provider});
  await h.flow.s.put('x-account',{id:'1',username:'Uncle_Onchain'});
  await toAwaitingFinal(h);
  await h.flow.finalApprove(DAY,1);
  h.state.now=dueAt(DAY,'zh')+1000;
  await h.flow.s.put('x-tokens',await seal({access_token:'tok',refresh_token:'ref',obtained_at:h.state.now,expires_in:7200},h.env.TOKEN_ENCRYPTION_KEY));
  return h;
}
test('发布时去掉旧流程存下的链接：已审核通过、正文里带链接的稿子不会连链接一起发出',async()=>{
  const h=await liveHarness();
  const flow=await h.flow.getFlow(DAY);
  await h.flow.s.put('flow:'+DAY,{...flow,formal:{...flow.formal,zh_post:flow.formal.zh_post+'\n\n交易记录：https://notion.example/ledger'}});
  await h.flow.alarm();
  const posted=h.log.x.find(c=>c.url.endsWith('/2/tweets'));
  assert.equal(JSON.parse(posted.options.body).text.includes('notion.example'),false);
});
test('开关打开后：先传两张图再发一条带图推文，只发一次',async()=>{
  const h=await liveHarness();
  await Promise.all([h.flow.alarm(),h.flow.alarm()]);
  const urls=h.log.x.map(c=>c.url);
  assert.deepEqual(urls,['https://api.x.com/2/users/me?user.fields=subscription,subscription_type,verified',
    'https://api.x.com/2/media/upload','https://api.x.com/2/media/upload','https://api.x.com/2/tweets']);
  const body=JSON.parse(h.log.x[3].options.body);
  assert.equal(body.text,`【热币分析·第36期（${DAY}）】\n`+BODY);
  assert.equal(body.media.media_ids.length,2);
  assert.equal(h.log.downloads.length,2);
  assert.equal((await h.flow.getFlow(DAY)).slots.zh.tweet_id,'tw1');
  assert.ok(texts(h).some(t=>t.includes('https://x.com/Uncle_Onchain/status/tw1')));
});
test('发推失败：图片已上传但发推未确认，标记未知，绝不自动重发',async()=>{
  const h=await liveHarness({provider:async(url,options)=>{
    if(url.includes('/users/me?')) return {data:{subscription_type:'premium'}};
    if(url.endsWith('/media/upload')) return {data:{id:'m'}};
    throw Error('请求超时');}});
  await h.flow.alarm();
  const slot=(await h.flow.getFlow(DAY)).slots.zh;
  assert.equal(slot.status,'unknown');
  assert.match(texts(h).at(-1),/可能已经送达X/);
  const before=h.log.x.length;
  await h.flow.alarm();
  assert.equal(h.log.x.length,before,'再次触发不会重发');
});
test('X账号与预期不符时不发',async()=>{
  const h=await liveHarness();
  await h.flow.s.put('x-account',{id:'2',username:'someone_else'});
  await h.flow.alarm();
  assert.equal((await h.flow.getFlow(DAY)).slots.zh.status,'failed');
  assert.equal(h.log.x.length,0);
});
test('X令牌过期时先续期并保存新令牌',async()=>{
  const calls=[];
  const h=await liveHarness({provider:async(url,options)=>{calls.push(url);
    if(url.endsWith('/oauth2/token')) return {access_token:'new',refresh_token:'ref2',expires_in:7200};
    if(url.includes('/users/me?')) return {data:{subscription_type:'premium'}};
    if(url.endsWith('/media/upload')) return {data:{id:'m'}};
    return {data:{id:'tw9'}};}});
  await h.flow.s.put('x-tokens',await seal({access_token:'old',refresh_token:'ref',obtained_at:h.state.now-3*3600000,expires_in:7200},h.env.TOKEN_ENCRYPTION_KEY));
  await h.flow.alarm();
  assert.equal(calls[0],'https://api.x.com/2/oauth2/token');
  assert.equal((await h.flow.getFlow(DAY)).slots.zh.status,'posted');
});
test('普通账号正文超出280加权字符时在上传图片前停止，并带回明确原因',async()=>{
  const h=await liveHarness({provider:async(url,options)=>{
    h.log.x.push({url,options});
    if(url.includes('/users/me?')) return {data:{}};
    throw Error('正文过长时不应继续调用X');
  }});
  const flow=await h.flow.getFlow(DAY);
  await h.flow.s.put('flow:'+DAY,{...flow,formal:{...flow.formal,zh_post:'中'.repeat(141)}});
  await h.flow.alarm();
  const slot=(await h.flow.getFlow(DAY)).slots.zh;
  assert.equal(slot.status,'failed');
  assert.equal(h.log.x.filter(c=>c.url.endsWith('/media/upload')).length,0);
  assert.match(texts(h).at(-1),/282个加权字符/);
  assert.match(texts(h).at(-1),/上限为280/);
});
test('图片文件名符合标准',()=>{
  assert.deepEqual(posterNames(37),{zh_desktop:'poster_zh_第37期.png',zh_mobile:'poster_zh_第37期-手机版.png',
    en_desktop:'poster_en_第37期.png',en_mobile:'poster_en_第37期-手机版.png'});
});

test('/postnow：没到点也立即发中英文各一条，重复触发不重复发',async()=>{
  const h=await liveHarness();
  h.state.now=dueAt(DAY,'zh')-10*3600000;
  await h.flow.postNow(DAY);
  await h.flow.postNow(DAY);
  const tweets=h.log.x.filter(c=>c.url==='https://api.x.com/2/tweets');
  assert.equal(tweets.length,2);
  const flow=await h.flow.getFlow(DAY);
  assert.equal(flow.slots.zh.status,'posted');
  assert.equal(flow.slots.en.status,'posted');
});
test('/postnow：终审没通过时不发',async()=>{
  const h=harness({env:{PUBLISH_ENABLED:'true'}});
  await toAwaitingFinal(h);
  assert.equal(await h.flow.postNow(DAY),false);
  assert.equal(h.log.x.length,0);
});

test('/reissue：未发布的当天重新走正式稿和出图，期号按新规则；已开始发布的不能重来',async()=>{
  const h=harness();
  await toAwaitingFinal(h);
  await h.flow.finalApprove(DAY,1);
  assert.equal((await h.flow.getFlow(DAY)).status,'final_approved');
  assert.equal(await h.flow.reissue(DAY),true);
  const again=await h.flow.getFlow(DAY);
  assert.equal(again.status,'poster_pending');
  assert.equal(again.rev,2);
  assert.deepEqual(again.slots,{});
  assert.equal((await h.flow.s.get('slots-open')).includes(DAY),false);
  await h.flow.s.put('flow:'+DAY,{...again,status:'final_approved',slots:{zh:{due:1,status:'posted'},en:{due:2,status:'scheduled'}}});
  assert.equal(await h.flow.reissue(DAY),false);
});

test('开头播报期号和日期：中文【热币分析·第N期（日期）】，英文【Hot Coin Desk · Issue N (日期)】，数字核对不受影响',async()=>{
  const h=harness();
  await toAwaitingFinal(h);
  const flow=await h.flow.getFlow(DAY);
  assert.ok(flow.formal.zh_post.startsWith(`【热币分析·第${flow.issue}期（${DAY}）】\n`));
  assert.ok(flow.formal.en_post.startsWith(`【Hot Coin Desk · Issue ${flow.issue} (${DAY})】\n`));
  assert.equal(flow.formal.zh,BODY);
});

test('终审通过后记下今天的期号；明天没新单也接着数；/reissue 释放今天的期号',async()=>{
  const h=harness();
  await toAwaitingFinal(h);
  await h.flow.finalApprove(DAY,1);
  assert.deepEqual(await h.flow.s.get('issues'),{[DAY]:36});
  assert.equal(await h.flow.reissue(DAY),true);
  assert.deepEqual(await h.flow.s.get('issues'),{});
  const again=await h.flow.getFlow(DAY);
  assert.equal(again.status,'poster_pending');
  assert.equal(again.rev,2);
  assert.equal(again.issue,36);
  assert.deepEqual(again.slots,{});
  assert.equal((await h.flow.s.get('slots-open')||[]).includes(DAY),false);
  await h.flow.s.put('flow:'+DAY,{...again,status:'final_approved',slots:{zh:{due:1,status:'posted'},en:{due:2,status:'scheduled'}}});
  assert.equal(await h.flow.reissue(DAY),false,'已经开始发布的不能重来');
});

test('X只允许一个 $币种标签：保留第一个，其余去掉$，美元金额不动',()=>{
  assert.equal(limitCashtags('只挂一张$SOL空单。美国现货$BTC ETF净流入5.266亿美元；$SOL继续跌；约$527 million'),
    '只挂一张$SOL空单。美国现货BTC ETF净流入5.266亿美元；SOL继续跌；约$527 million');
  assert.equal(limitCashtags('没有币种标签 $100'),'没有币种标签 $100');
});
test('X以4xx拒绝发帖：确定没发出去，标记失败而不是未知；/retrypost 只重发这一条，且去掉多余的$',async()=>{
  let refuse=true;
  const sent=[];
  const h=await liveHarness({provider:async(url,options)=>{
    if(url.includes('/users/me?')) return {data:{subscription_type:'premium'}};
    if(url.endsWith('/media/upload')) return {data:{id:'m'}};
    if(url.endsWith('/tweets')) {
      if(refuse) { const error=Error('服务请求失败（HTTP 403）');error.status=403;error.providerMessage='Posts are limited to a maximum of one cashtag';throw error; }
      sent.push(JSON.parse(options.body).text); return {data:{id:'tw'+sent.length}};
    }
    throw Error('unexpected '+url);}});
  await h.flow.alarm();
  h.state.now=dueAt(DAY,'en')+1000;
  await h.flow.alarm();
  const after=await h.flow.getFlow(DAY);
  assert.equal(after.slots.zh.status,'failed');
  assert.equal(after.slots.en.status,'failed');
  assert.doesNotMatch(texts(h).at(-1),/可能已经送达X/);
  assert.match(texts(h).join('\n'),/Posts are limited to a maximum of one cashtag/);
  refuse=false;
  assert.equal(await h.flow.retryPost(DAY),true);
  const retried=await h.flow.getFlow(DAY);
  assert.equal(retried.slots.zh.status,'posted');
  assert.equal(retried.slots.en.status,'posted');
  assert.equal(sent.length,2);
  for(const text of sent) assert.ok((text.match(/\$[A-Za-z]/g)||[]).length<=1,'每条最多一个币种标签');
  const count=sent.length;
  assert.equal(await h.flow.retryPost(DAY),false,'已发布的不会再发');
  assert.equal(sent.length,count);
});

test('同一天第二期：前一期发完后收起，新期号接着数；没发完不能收起',async()=>{
  const h=harness();
  await toAwaitingFinal(h);
  await h.flow.finalApprove(DAY,1);
  assert.equal(await h.flow.archiveDay(DAY),false,'还有待发布的，不能收起');
  const flow=await h.flow.getFlow(DAY);
  await h.flow.s.put('flow:'+DAY,{...flow,slots:{zh:{due:1,status:'posted'},en:{due:2,status:'posted'}}});
  assert.equal(await h.flow.archiveDay(DAY),true);
  assert.equal((await h.flow.getFlow(DAY)).status,'archived');
  assert.deepEqual(await h.flow.s.get('issues'),{[DAY+'#1']:36});
  // 台账已更新到今天（第36期），但同一天已有收起的第36期，所以新的一期是第37期
  assert.equal(issueNumber({...LEDGER,updated_to:DAY,updated_issue:36},DAY,await h.flow.s.get('issues')),37);
  // 新热点出新稿、文字通过后，可以重新走一遍正式稿流程
  await seed(h);
  await h.flow.startFormal(DAY);
  const next=await h.flow.getFlow(DAY);
  assert.equal(next.status,'poster_pending');
  assert.equal(next.rev,2);
});

test('帖子正文不带链接；旧流程里已存的链接发布时也会去掉',async()=>{
  assert.equal(stripTradeLog('正文\n\n交易记录：https://n.example/x'),'正文');
  assert.equal(stripTradeLog('body\n\nTrade log: https://n.example/x\n'),'body');
  assert.equal(stripTradeLog('正文里提到 https://a.example 的不动'),'正文里提到 https://a.example 的不动');
});
