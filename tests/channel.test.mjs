// TG 频道发布通道：目标分配（日常→频道、周日→X）、图文发送、分步进度（重试不重复）、权限被拒、/channelcheck。
import test from 'node:test';
import assert from 'node:assert/strict';
import {Flow,tgErrorReason} from '../cloud/flow.mjs';
import {TradeDesk,photoOk} from '../cloud/worker.mjs';
import {dueAt,weeklyDueAt} from '../calendar.mjs';
import {seal} from '../cloud/security.mjs';

const DAY='2026-10-21';
const SUN='2026-10-25';
const ZH_SHORT='今天我挂一张$ETH远档空单。进场限价2,585.57，止损2,637.28，目标2,378.72。';
const EN_SHORT='Today I am placing a far-away $ETH short limit order. Entry 2,585.57, stop 2,637.28, target 2,378.72.';
const ZH_LONG=ZH_SHORT+'补'.repeat(1100);

function harness({env={},now=Date.parse(DAY+'T10:00:00+08:00')}={}) {
  const entries=new Map();let alarm=null,tail=Promise.resolve();
  const storage={get:async k=>structuredClone(entries.get(k)),put:async(k,v)=>entries.set(k,structuredClone(v)),
    delete:async k=>entries.delete(k),transaction:fn=>{const run=tail.then(()=>fn(storage));tail=run.catch(()=>{});return run;},
    setAlarm:async t=>{alarm=t;},deleteAlarm:async()=>{alarm=null;},getAlarm:async()=>alarm};
  const log={telegram:[],photos:[],x:[],downloads:[]};
  const state={now,photoError:null,textError:null,tweetError:null};
  const full={TELEGRAM_OWNER_ID:'123',PUBLISH_ENABLED:'true',EXPECTED_X_USERNAME:'Uncle_Onchain',
    TOKEN_ENCRYPTION_KEY:'k'.repeat(40),X_CLIENT_ID:'c',X_CLIENT_SECRET:'s',TRADE_LOG_URL:'https://notion.example/ledger',
    TG_CHANNEL:'@UncleOnchain',DAILY_TARGETS:'tg',WEEKLY_TARGETS:'x',...env};
  const deps={now:()=>state.now,
    telegram:async(e,method,payload)=>{
      log.telegram.push({method,...payload});
      if(method==='sendMessage' && String(payload.chat_id)==='@UncleOnchain') {
        if(state.textError) throw state.textError;
        return {ok:true,result:{message_id:200+log.telegram.length}};
      }
      return {ok:true};},
    tgChannelPhoto:async(e,chat,name,bytes,caption)=>{
      if(state.photoError) throw state.photoError;
      log.photos.push({chat,name,size:bytes.byteLength,caption});
      return {ok:true,result:{message_id:77}};},
    tgUpload:async()=>({result:{document:{file_id:'x'}}}),
    tgDownload:async(e,id)=>{log.downloads.push(id);return new Uint8Array(3000);},
    translate:async()=>{throw Error('不翻译');},
    provider:async(url,options)=>{
      log.x.push({url,body:options?.body});
      if(url.includes('/users/me?')) return {data:{subscription_type:'premium'}};
      if(url.endsWith('/media/upload')) return {data:{id:'m'+log.x.length}};
      if(url.endsWith('/tweets')) { if(state.tweetError) throw state.tweetError; return {data:{id:'tw'+log.x.length}}; }
      if(url.includes('oauth2/token')) return {access_token:'a',refresh_token:'b',expires_in:7200};
      throw Error('unexpected '+url);}};
  const flow=new Flow({storage},full,deps);
  return {flow,entries,log,state,alarm:()=>alarm};
}
async function xReady(h) {
  await h.flow.s.put('x-account',{id:'1',username:'Uncle_Onchain'});
  await h.flow.s.put('x-tokens',await seal({access_token:'a',refresh_token:'b',obtained_at:h.state.now,expires_in:7200},'k'.repeat(40)));
}
const file=name=>({file_id:'f_'+name});
async function seedDaily(h,{zh=ZH_SHORT}={}) {
  const names={zh_desktop:'zh_d.png',zh_mobile:'zh_m.png',en_desktop:'en_d.png',en_mobile:'en_m.png'};
  const files=Object.fromEntries(Object.values(names).map(n=>[n,file(n)]));
  // flow.files is keyed by role (zh_desktop…), same as the poster job writes it
  const byRole=Object.fromEntries(Object.keys(names).map(k=>[k,file(names[k])]));
  await h.flow.s.put('flow:'+DAY,{day:DAY,status:'final_approved',rev:1,issue:38,formal:{zh_post:zh,en_post:EN_SHORT},names,files:byRole,slots:{}});
  void files;
}
async function seedWeekly(h) {
  const names={zh_desktop:'wz.png',en_desktop:'we.png'};
  const files={zh_desktop:file('wz.png'),en_desktop:file('we.png')};
  await h.flow.s.put('flow:'+SUN,{day:SUN,kind:'weekly',status:'final_approved',rev:1,formal:{zh_post:ZH_SHORT,en_post:EN_SHORT},names,files,slots:{}});
}
const sent=h=>h.log.telegram.filter(m=>m.method==='sendMessage').map(m=>m.text);

test('日常默认发频道：只排中文一个位置，英文不排',async()=>{
  const h=harness();
  await seedDaily(h);
  const lines=await h.flow.schedule(DAY);
  const flow=await h.flow.getFlow(DAY);
  assert.deepEqual(Object.keys(flow.slots),['zh']);
  assert.deepEqual(flow.slots.zh.targets,['tg']);
  assert.equal(lines.length,1);
  assert.ok(lines[0].includes('TG频道'));
  assert.equal(h.alarm(),dueAt(DAY,'zh'));
});

test('到点：竖版海报带正文做说明一条发出，不碰 X；闹钟重复触发只发一次',async()=>{
  const h=harness();
  await seedDaily(h);
  await h.flow.schedule(DAY);
  h.state.now=dueAt(DAY,'zh')+10000;
  await Promise.all([h.flow.alarm(),h.flow.alarm()]);
  assert.equal(h.log.photos.length,1);
  assert.deepEqual(h.log.photos[0],{chat:'@UncleOnchain',name:'zh_m.png',size:3000,caption:ZH_SHORT});
  assert.equal(h.log.x.length,0);
  const slot=(await h.flow.getFlow(DAY)).slots.zh;
  assert.equal(slot.status,'posted');
  assert.ok(sent(h).some(t=>t.includes('https://t.me/UncleOnchain/77')));
});

test('正文超过 1024 字：先发图，再把正文作为第二条消息发到频道',async()=>{
  const h=harness();
  await seedDaily(h,{zh:ZH_LONG});
  await h.flow.schedule(DAY);
  h.state.now=dueAt(DAY,'zh')+1000;
  await h.flow.alarm();
  assert.equal(h.log.photos.length,1);
  assert.equal(h.log.photos[0].caption,'');
  const channelTexts=h.log.telegram.filter(m=>m.method==='sendMessage'&&m.chat_id==='@UncleOnchain');
  assert.equal(channelTexts.length,1);
  assert.equal(channelTexts[0].text,ZH_LONG);
  assert.equal((await h.flow.getFlow(DAY)).slots.zh.status,'posted');
});

test('图已发出、正文失败（5xx）：状态未知，程序不自动重发；确认后重发只补正文，不重复发图',async()=>{
  const h=harness();
  await seedDaily(h,{zh:ZH_LONG});
  await h.flow.schedule(DAY);
  h.state.textError=Object.assign(Error('x'),{status:502});
  h.state.now=dueAt(DAY,'zh')+1000;
  await h.flow.alarm();
  let slot=(await h.flow.getFlow(DAY)).slots.zh;
  assert.equal(slot.status,'unknown');
  assert.equal(slot.tg.photo,77);
  assert.ok(sent(h).some(t=>t.includes('可能已经送达TG频道')));
  await h.flow.alarm();
  assert.equal(h.log.photos.length,1,'闹钟再来也不会重发');
  h.state.textError=null;
  await h.flow.retryPost(DAY);
  slot=(await h.flow.getFlow(DAY)).slots.zh;
  assert.equal(slot.status,'posted');
  assert.equal(h.log.photos.length,1,'图不重复');
  assert.equal(h.log.telegram.filter(m=>m.method==='sendMessage'&&m.chat_id==='@UncleOnchain').length,2,'正文失败一次 + 补发一次');
});

test('机器人没有发帖权限（403）：明确失败，原因写清楚，不当成未知',async()=>{
  const h=harness();
  await seedDaily(h);
  await h.flow.schedule(DAY);
  h.state.photoError=Object.assign(Error('x'),{status:403,providerMessage:'Forbidden: bot is not a member of the channel chat'});
  h.state.now=dueAt(DAY,'zh')+1000;
  await h.flow.alarm();
  const slot=(await h.flow.getFlow(DAY)).slots.zh;
  assert.equal(slot.status,'failed');
  assert.ok(slot.error.includes('TG频道')&&slot.error.includes('发帖权限'));
  assert.equal(h.log.x.length,0);
});

test('周日默认发 X：中英各一条，不进频道',async()=>{
  const h=harness({now:Date.parse(SUN+'T15:00:00+08:00')});
  await xReady(h);
  await seedWeekly(h);
  await h.flow.schedule(SUN);
  const flow=await h.flow.getFlow(SUN);
  assert.deepEqual(Object.keys(flow.slots),['zh','en']);
  assert.deepEqual(flow.slots.zh.targets,['x']);
  h.state.now=weeklyDueAt(SUN,'zh')+5000;
  await h.flow.alarm();
  assert.equal(h.log.x.filter(c=>c.url.endsWith('/tweets')).length,2);
  assert.equal(h.log.photos.length,0);
});

test('频道 + X 同时：X 失败后重试，频道那条不重复',async()=>{
  const h=harness({now:Date.parse(SUN+'T15:00:00+08:00'),env:{WEEKLY_TARGETS:'tg,x',TG_CHANNEL_LANGS:'zh,en'}});
  await xReady(h);
  await seedWeekly(h);
  await h.flow.schedule(SUN);
  h.state.tweetError=Object.assign(Error('x'),{status:403,providerMessage:'duplicate'});
  h.state.now=weeklyDueAt(SUN,'zh')+5000;
  await h.flow.alarm();
  const flow=await h.flow.getFlow(SUN);
  assert.equal(flow.slots.zh.status,'failed');
  assert.equal(flow.slots.en.status,'failed');
  assert.equal(h.log.photos.length,2,'中英各进频道一次');
  h.state.tweetError=null;
  await h.flow.retryPost(SUN);
  const after=await h.flow.getFlow(SUN);
  assert.equal(after.slots.zh.status,'posted');
  assert.equal(after.slots.en.status,'posted');
  assert.equal(h.log.photos.length,2,'重试不重复进频道');
  assert.equal(h.log.x.filter(c=>c.url.endsWith('/tweets')).length,4,'X 失败两次 + 重试两次');
});

test('目标写了频道但没配 TG_CHANNEL：不排任何发布，并说明原因',async()=>{
  const h=harness({env:{TG_CHANNEL:''}});
  await seedDaily(h);
  const lines=await h.flow.schedule(DAY);
  assert.deepEqual((await h.flow.getFlow(DAY)).slots,{});
  assert.ok(lines[0].includes('没有可用的发布目标'));
  assert.equal(h.alarm(),null);
});

test('总开关关闭：频道也只演练，不发图',async()=>{
  const h=harness({env:{PUBLISH_ENABLED:'false'}});
  await seedDaily(h);
  await h.flow.schedule(DAY);
  h.state.now=dueAt(DAY,'zh')+1000;
  await h.flow.alarm();
  assert.equal((await h.flow.getFlow(DAY)).slots.zh.status,'dry_run');
  assert.equal(h.log.photos.length,0);
  assert.ok(sent(h).some(t=>t.includes('【演练，未发布】')&&t.includes('TG频道')));
});

test('终审提示按目标写发布安排',async()=>{
  const h=harness();
  assert.equal(h.flow.planText({kind:'daily'}),'中文20:00发TG频道');
  assert.equal(h.flow.planText({kind:'weekly'}),'中文20:00发X；英文20:00发X');
});

test('图片能否当照片发：尺寸、比例、体积',()=>{
  const png=(w,h,extra=0)=>{
    const b=new Uint8Array(24+extra);
    b.set([137,80,78,71,13,10,26,10],0);
    const v=new DataView(b.buffer);v.setUint32(16,w);v.setUint32(20,h);return b;
  };
  assert.equal(photoOk(png(1080,1920)),true);
  assert.equal(photoOk(png(1080,25000)),false,'宽+高超过10000改发文件');
  assert.equal(photoOk(png(300,6500)),false,'比例超过20改发文件');
  assert.equal(photoOk(new Uint8Array(100)),false,'不是PNG改发文件');
});

test('频道错误原因：不泄露令牌，常见状态有人话',()=>{
  assert.ok(tgErrorReason({status:403}).includes('管理员'));
  assert.ok(tgErrorReason({status:400,providerMessage:'chat not found'}).includes('TG_CHANNEL'));
  assert.ok(!tgErrorReason({status:401,providerMessage:'bot123456789:AAAAAAAAAAAAAAAAAAAAAAAAAA bad'}).includes('AAAAAAAAAA'));
  assert.ok(tgErrorReason(Error('x')).includes('网络'));
});

test('/channelcheck：只读检查，管理员且能发消息才说可以发帖',async()=>{
  const calls=[],original=globalThis.fetch;
  const answers={getMe:{ok:true,result:{id:9,username:'alpha_bot'}},
    getChat:{ok:true,result:{title:'链上大叔',type:'channel'}},
    getChatMember:{ok:true,result:{status:'administrator',can_post_messages:true}}};
  globalThis.fetch=async url=>{const m=url.split('/').pop();calls.push(m);return new Response(JSON.stringify(answers[m]),{status:200});};
  try {
    const entries=new Map();
    const storage={get:async k=>entries.get(k),put:async(k,v)=>entries.set(k,v),delete:async k=>entries.delete(k),transaction:async fn=>fn(storage)};
    const env={TELEGRAM_BOT_TOKEN:'t',TG_CHANNEL:'@UncleOnchain',DAILY_TARGETS:'tg',WEEKLY_TARGETS:'x',PUBLISH_ENABLED:'true'};
    const desk=new TradeDesk({storage},env);
    const out=[];
    await desk.channelCheck(env,async t=>out.push(t));
    assert.deepEqual(calls,['getMe','getChat','getChatMember'],'只读，不发任何消息');
    assert.ok(out[0].includes('可以发帖')&&out[0].includes('@alpha_bot'));
    answers.getChatMember={ok:true,result:{status:'administrator',can_post_messages:false}};
    out.length=0;
    await desk.channelCheck(env,async t=>out.push(t));
    assert.ok(out[0].includes('不能发帖'));
    answers.getChatMember={ok:false,error_code:400,description:'chat not found'};
    globalThis.fetch=async()=>new Response(JSON.stringify({ok:false,description:'chat not found'}),{status:400});
    out.length=0;
    await desk.channelCheck(env,async t=>out.push(t));
    assert.ok(out[0].includes('没通过'));
    out.length=0;
    await desk.channelCheck({...env,TG_CHANNEL:''},async t=>out.push(t));
    assert.ok(out[0].includes('TG_CHANNEL'));
  } finally { globalThis.fetch=original; }
});

test('日常配置：中文进频道、英文发 X；周日中英都发 X',async()=>{
  const h=harness({env:{DAILY_TARGETS:'tg,x',DAILY_X_LANGS:'en',TG_CHANNEL_LANGS:'zh',WEEKLY_TARGETS:'x'},now:Date.parse(DAY+'T10:00:00+08:00')});
  await xReady(h);
  await seedDaily(h);
  await h.flow.schedule(DAY);
  const flow=await h.flow.getFlow(DAY);
  assert.deepEqual(flow.slots.zh.targets,['tg']);
  assert.deepEqual(flow.slots.en.targets,['x']);
  h.state.now=dueAt(DAY,'zh')+1000;
  await h.flow.alarm();
  assert.equal(h.log.photos.length,1);
  assert.equal(h.log.x.length,0,'中文不发 X');
  h.state.now=dueAt(DAY,'en')+1000;
  await h.flow.alarm();
  const posts=h.log.x.filter(c=>c.url.endsWith('/tweets'));
  assert.equal(posts.length,1);
  assert.ok(JSON.parse(posts[0].body).text.startsWith('Today I am placing'));
  assert.equal(h.flow.plan('weekly').zh[0],'x');
  assert.deepEqual(Object.keys(h.flow.plan('weekly')),['zh','en']);
});
