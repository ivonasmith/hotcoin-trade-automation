// Everything that happens after the reviewer approves the text:
// formal zh/en -> poster job -> Telegram review of images + formal text -> final approval ->
// Notion ledger job + scheduled posts (zh 20:00, en 21:00 Beijing time).
// One Durable Object owns all state, so every transition is a compare-and-set inside a transaction:
// a double tap, a retried webhook or a re-delivered alarm can never repeat a step.
import {beijingDay,eligible,dueAt,weeklyDay,weeklyDueAt,WEEKLY_TIME} from '../calendar.mjs';
import {splitDraft,formalChinese,formalEnglish,stripTradeLog,bodyProblems,TRANSLATE_SYSTEM,translatePrompt,englishProblems,
  issueNumber,posterNames,posterPayload,ledgerPayload,postHeader,limitCashtags} from './formal.mjs';
import {accessToken,checkPostLength,uploadImage,createPost,xErrorReason} from './x.mjs';

export function tgErrorReason(error) {
  const status=Number(error?.status)||null;
  const known={400:'频道找不到或请求格式不对（检查 TG_CHANNEL 是否写成 @频道用户名）',
    401:'机器人令牌无效',403:'机器人在频道里没有发帖权限（要把机器人加为管理员并勾选「发布消息」）',
    429:'Telegram频率限制'}[status];
  const detail=String(error?.providerMessage||error?.message||'').replace(/\d{6,}:[A-Za-z0-9_-]{20,}/g,'[令牌已隐藏]').slice(0,200);
  return (known?`HTTP ${status}：${known}`:(status?`HTTP ${status}`:'网络错误或超时'))+(detail?`（${detail}）`:'');
}
export const GRACE_MS=60*60000;      // allow data/API/review delays; after 60 minutes it waits for the reviewer
export const LEASE_MS=12*60000;
export const MAX_ATTEMPTS=3;
export const WATCHDOG_MS=25*60000;
const LANGS=['zh','en'];
const LANG_NAME={zh:'中文',en:'英文'};
const TIME_TEXT={zh:'20:00',en:'21:00'};
const MAX_UPLOAD=15*1024*1024;
// Sunday: ONE X post carrying both posters (zh + en) with this fixed bilingual caption; the posters hold all the content.
export const WEEKLY_CAPTION='【$BTC\n\n下周的交易计划和大事前瞻来了，速看！ 👇\nThe week ahead plan is coming. Let\'s dive in! 👇】';
const TARGET_NAME={x:'X',tg:'TG频道'};
const TG_CAPTION_MAX=1024,TG_TEXT_MAX=4096;

export class Flow {
  constructor(ctx,env,deps) { this.ctx=ctx; this.s=ctx.storage; this.env=env; this.d=deps; }
  now() { return (this.d.now||Date.now)(); }
  get chat() { return this.env.TELEGRAM_OWNER_ID; }
  say(text,rows) {
    const payload={chat_id:this.chat,text:String(text).slice(0,4000)};
    if(rows) payload.reply_markup={inline_keyboard:rows};
    return this.d.telegram(this.env,'sendMessage',payload);
  }
  async cas(key,from,patch) {
    return this.s.transaction(async tx=>{
      const current=await tx.get(key);
      if(!current || !from.includes(current.status)) return null;
      const next={...current,...(typeof patch==='function'?patch(current):patch)};
      await tx.put(key,next);
      return next;
    });
  }
  flowKey=day=>'flow:'+day;
  // Weekday issues have a number; the Sunday week-ahead has none ("第N期" would be wrong for it).
  label(flow) { return flow?.kind==='weekly'?'周日下周清单':`第${flow?.issue}期`; }
  // Sunday has one combined post, so its slot has no language label.
  slotName(flow,lang) { return flow?.kind==='weekly'?'周日海报帖':`${LANG_NAME[lang]}版`; }
  timeText(flow,lang) { return flow?.kind==='weekly'?WEEKLY_TIME:TIME_TEXT[lang]; }
  async getFlow(day) { return this.s.get(this.flowKey(day)); }
  // Where each language goes. DAILY_TARGETS / WEEKLY_TARGETS = comma list of x, tg (default x only, i.e. the old behaviour).
  // TG goes only to TG_CHANNEL and only for the languages in TG_CHANNEL_LANGS (default zh); X carries the languages in DAILY_X_LANGS / WEEKLY_X_LANGS (default zh,en).
  plan(kind) {
    const raw=kind==='weekly'?this.env.WEEKLY_TARGETS:this.env.DAILY_TARGETS;
    const wanted=String(raw||'x').split(',').map(t=>t.trim().toLowerCase()).filter(t=>TARGET_NAME[t]);
    const tgLangs=String(this.env.TG_CHANNEL_LANGS||'zh').split(',').map(t=>t.trim());
    const xLangs=String((kind==='weekly'?this.env.WEEKLY_X_LANGS:this.env.DAILY_X_LANGS)||'zh,en').split(',').map(t=>t.trim());
    const plan={};
    if(kind==='weekly') return wanted.includes('x')?{zh:['x']}:{};
    for(const lang of LANGS) {
      const targets=[...new Set(wanted)].filter(t=>(t==='x' && xLangs.includes(lang)) || (t==='tg' && this.env.TG_CHANNEL && tgLangs.includes(lang)));
      if(targets.length) plan[lang]=targets;
    }
    return plan;
  }
  targetText(targets) { return targets.map(t=>TARGET_NAME[t]).join(' + '); }
  planText(flow) {
    const plan=this.plan(flow?.kind);
    const parts=LANGS.filter(l=>plan[l]).map(l=>`${flow?.kind==='weekly'?'中英两张海报同一条帖子':LANG_NAME[l]}${this.timeText(flow,l)}发${this.targetText(plan[l])}`);
    return parts.length?parts.join('；'):'没有任何发布目标';
  }
  slotLangs(flow) { return LANGS.filter(lang=>flow?.slots?.[lang]); }

  // ---------- Sunday week-ahead: the GitHub job builds pack + both posts from live data, so there is no text-review step;
  // it goes straight to the poster job, then the same final-approval / scheduled-publish path as weekday issues.
  async startWeekly(day,{pack}) {
    if(!weeklyDay(day).ok) return {status:400,error:weeklyDay(day).reason};
    const names={zh_desktop:`poster_zh_周日下周清单_${day}.png`,en_desktop:`poster_en_周日下周清单_${day}.png`};
    const started=await this.s.transaction(async tx=>{
      const existing=await tx.get(this.flowKey(day));
      if(existing && !['poster_failed','final_rejected','archived'].includes(existing.status)) return null;
      const rev=(existing?.rev||0)+1;
      const next={day,kind:'weekly',status:'poster_pending',rev,started:this.now(),slots:{},history:existing?.history||[],
        formal:{zh_post:WEEKLY_CAPTION,en_post:WEEKLY_CAPTION},names,files:{},
        poster_job:`poster:${day}:${rev}`,weekly_pack:pack,warnings:pack.warnings||[]};
      await tx.put(this.flowKey(day),next);
      return next;
    });
    if(!started) return {status:409,error:'这一天已经有一期在处理或已通过，没有重复接收'};
    await this.enqueue({id:started.poster_job,type:'poster',day,rev:started.rev,payload:pack});
    await this.watch(day,started.rev);
    await this.say(`${this.label(started)}的数据已收到，海报任务已排队，通常几分钟内出图，出完后会发来中英文正式稿和 2 张图请你终审。`);
    return {status:200,rev:started.rev};
  }

  // ---------- step 1: text approved -> formal zh/en -> poster job ----------
  async startFormal(day) {
    const draft=await this.s.get('draft:'+day);
    if(!draft) return;
    // Only one run may start; a second tap while one is running (or after) finds the key taken.
    const started=await this.s.transaction(async tx=>{
      const existing=await tx.get(this.flowKey(day));
      if(existing && !['formal_failed','poster_failed','final_rejected','archived'].includes(existing.status)) return null;
      const next={day,status:'formal_generating',rev:(existing?.rev||0)+1,started:this.now(),slots:{},history:existing?.history||[]};
      await tx.put(this.flowKey(day),next);
      return next;
    });
    if(!started) return;
    await this.say('文字稿已通过。正在生成正式中英文稿，并排海报任务（图片由程序生成，不调用图片模型）。');
    try { await this.buildFormal(day,draft,started); }
    catch(error) { await this.fail(day,started.rev,'formal_failed',String(error.message||error)); }
  }

  async fail(day,rev,status,reason,retryAction='formal_retry') {
    const failed=await this.cas(this.flowKey(day),['formal_generating','poster_pending'],current=>
      current.rev===rev?{status,failure:reason,failed:this.now()}:{});
    if(!failed || failed.status!==status) return;
    await this.say(`第一步没有完成，没有发任何东西。原因：${reason}`,[[{text:'🔁 重试',callback_data:`${retryAction}:${day}`}]]);
  }

  async buildFormal(day,draft,flow) {
    const {body}=splitDraft(draft.content);
    if(!body) throw Error('送审稿里找不到正文');
    const problems=bodyProblems(body,draft);
    if(problems.length) throw Error('已通过的正文与程序订单不一致：'+problems.join('；'));
    const snapshot=await this.s.get('snapshot:'+day);
    const issue=issueNumber(snapshot?.ledger,day,(await this.s.get('issues'))||{});
    if(!issue) throw Error('台账没有读取成功，无法确定今天是第几期');
    let english='',feedback='',model='';
    for(let attempt=1;attempt<=3;attempt++) {
      const result=await this.d.translate(this.env,body+(feedback?`\n\n（上一版的问题，请改正：${feedback}）`:''),TRANSLATE_SYSTEM,translatePrompt);
      english=result.content.trim().replace(/^```[a-z]*\n?|```$/g,'').trim(); model=result.model;
      const issues=englishProblems(body,english);
      if(!issues.length) { feedback=''; break; }
      feedback=issues.join('；');
    }
    if(feedback) throw Error('英文稿和中文稿的数字没能核对一致：'+feedback);
    const formal={zh:body,en:english,zh_post:formalChinese(body,postHeader('zh',issue,day)),en_post:formalEnglish(english,postHeader('en',issue,day)),model};
    // The poster job needs the same body text and the same program order.
    const payload=posterPayload({day,issue,draft,snapshot,body,english});
    const names=posterNames(issue);
    const id=`poster:${day}:${flow.rev}`;
    const queued=await this.s.transaction(async tx=>{
      const current=await tx.get(this.flowKey(day));
      if(!current || current.rev!==flow.rev || current.status!=='formal_generating') return false;
      await tx.put(this.flowKey(day),{...current,status:'poster_pending',issue,formal,names,files:{},poster_job:id});
      return true;
    });
    if(!queued) return;
    await this.enqueue({id,type:'poster',day,rev:flow.rev,payload});
    await this.watch(day,flow.rev);
    await this.say(`正式稿已生成，数字核对一致（第${issue}期）。海报任务已排队，通常几分钟内出图。`);
  }

  // If nobody renders the posters in time (runner crashed, workflow never started), say so instead of waiting silently.
  async watch(day,rev) {
    await this.s.transaction(async tx=>{
      const list=(await tx.get('watchdog'))||[];
      await tx.put('watchdog',[...list,{day,rev,at:this.now()+WATCHDOG_MS}]);
    });
    await this.arm();
  }

  // ---------- job queue (polled by GitHub Actions with the pipeline secret) ----------
  async enqueue(job) {
    const added=await this.s.transaction(async tx=>{
      if(await tx.get('job:'+job.id)) return false;
      await tx.put('job:'+job.id,{...job,status:'queued',attempts:0,created:this.now()});
      const open=(await tx.get('jobs-open'))||[];
      await tx.put('jobs-open',[...open,job.id]);
      return true;
    });
    if(added) await this.dispatch();
    return added;
  }
  // Optional fast path: if a GitHub token is configured, start the poll workflow right away instead of waiting for cron.
  async dispatch() {
    if(!this.env.GITHUB_DISPATCH_TOKEN) return;
    try {
      await this.d.provider(`https://api.github.com/repos/${this.env.GITHUB_REPO||'ivonasmith/hotcoin-trade-automation'}/actions/workflows/jobs.yml/dispatches`,{
        method:'POST',headers:{Authorization:'Bearer '+this.env.GITHUB_DISPATCH_TOKEN,Accept:'application/vnd.github+json',
          'X-GitHub-Api-Version':'2022-11-28','User-Agent':'uncleonchain-trade-bot','Content-Type':'application/json'},
        body:JSON.stringify({ref:'main'})},15000);
    } catch { /* the 5-minute poll still picks the job up */ }
  }
  async nextJob(peek) {
    return this.s.transaction(async tx=>{
      const open=(await tx.get('jobs-open'))||[];
      const now=this.now();
      for(const id of open) {
        const job=await tx.get('job:'+id);
        if(!job || job.status==='done' || job.status==='failed') continue;
        if(job.status==='leased' && job.lease_until>now) continue;
        if(job.attempts>=MAX_ATTEMPTS) continue;
        if(peek) return {id:job.id,type:job.type};
        const leased={...job,status:'leased',attempts:job.attempts+1,lease_until:now+LEASE_MS};
        await tx.put('job:'+id,leased);
        return {id:leased.id,type:leased.type,day:leased.day,payload:leased.payload};
      }
      return null;
    });
  }
  async closeJob(id,status,extra={}) {
    return this.s.transaction(async tx=>{
      const job=await tx.get('job:'+id);
      if(!job || job.status!=='leased') return null;
      await tx.put('job:'+id,{...job,status,finished:this.now(),...extra});
      await tx.put('jobs-open',((await tx.get('jobs-open'))||[]).filter(x=>x!==id));
      return job;
    });
  }

  // A PNG from the poster job goes straight to Telegram; the same name is never sent twice.
  async receiveFile(id,name,bytes) {
    const job=await this.s.get('job:'+id);
    if(!job || job.type!=='poster' || job.status!=='leased') return {status:409,error:'任务不在进行中'};
    const flow=await this.getFlow(job.day);
    if(!flow || flow.poster_job!==id) return {status:409,error:'任务已过期'};
    const key=Object.keys(flow.names).find(k=>flow.names[k]===name);
    if(!key) return {status:400,error:'文件名不在本期预期内'};
    if(bytes.byteLength<1000 || bytes.byteLength>MAX_UPLOAD) return {status:400,error:'图片大小异常'};
    if(flow.files?.[key]) return {status:200,duplicate:true};
    const label={zh_desktop:'中文电脑版',zh_mobile:'中文手机版',en_desktop:'英文电脑版',en_mobile:'英文手机版'}[key];
    const sent=await this.d.tgUpload(this.env,name,bytes,`${this.label(flow)} ${label}（待终审，尚未发布）`);
    const fileId=sent?.result?.document?.file_id;
    if(!fileId) throw Error('Telegram没有返回文件编号');
    await this.s.transaction(async tx=>{
      const current=await tx.get(this.flowKey(job.day));
      if(current && current.poster_job===id) await tx.put(this.flowKey(job.day),{...current,files:{...current.files,[key]:{file_id:fileId,bytes:bytes.byteLength}}});
    });
    return {status:200};
  }

  async completeJob(id,body) {
    const job=await this.s.get('job:'+id);
    if(!job) return {status:404};
    if(job.type==='poster') return this.completePoster(job,body);
    if(job.type==='ledger') return this.completeLedger(job,body);
    return {status:400};
  }
  async completePoster(job,body) {
    const {day}=job;
    const flow=await this.getFlow(day);
    if(!flow || flow.poster_job!==job.id) { await this.closeJob(job.id,'done',{stale:true}); return {status:200,stale:true}; }
    const complete=Object.keys(flow.names).every(k=>flow.files?.[k]);
    if(!body.ok || !complete) {
      const closed=await this.closeJob(job.id,'failed',{error:body.error||'图片没有收齐'});
      if(closed) await this.fail(day,flow.rev,'poster_failed',`出图没有完成：${body.error||`${Object.keys(flow.names).length}张图片没有收齐`}`,'poster_retry');
      return {status:200};
    }
    if(!await this.closeJob(job.id,'done')) return {status:200,duplicate:true};
    const moved=await this.cas(this.flowKey(day),['poster_pending'],current=>current.poster_job===job.id?{status:'awaiting_final',rendered:this.now()}:{});
    if(!moved || moved.status!=='awaiting_final') return {status:200};
    await this.arm();
    const f=moved.formal;
    const weekly=moved.kind==='weekly';
    if(weekly) await this.say(`${this.label(moved)} 帖子文字（固定，海报里才有内容；${this.timeText(moved,'zh')} 北京时间，同一条帖子带中、英两张海报）：\n\n${WEEKLY_CAPTION}`);
    else {
      await this.say(`${this.label(moved)} 中文正式稿（${this.timeText(moved,'zh')} 北京时间）：\n\n${stripTradeLog(f.zh_post)}`);
      await this.say(`${this.label(moved)} 英文正式稿（${this.timeText(moved,'en')} 北京时间）：\n\n${stripTradeLog(f.en_post)}`);
    }
    if(weekly && moved.warnings?.length) await this.say(`请核对（程序提示）：\n- ${moved.warnings.join('\n- ')}`);
    if(!weekly && this.env.TRADE_LOG_URL) await this.say(`台账链接（帖子正文不带，评论区你自己发）：${this.env.TRADE_LOG_URL}`);
    await this.say((weekly
      ?`${this.label(moved)}：上面2张海报（中、英各一张）已出齐，内容全部来自日历和台账的实时数据，由程序排版出图。请重点核对：本周新平仓有没有漏、解锁和大会名单对不对。\n通过后：不写台账（本期没有新订单）；发布安排（北京时间）：${this.planText(moved)}，`
      :`${this.label(moved)}：上面4张图和中英文正式稿已备齐，数字与程序订单、中文稿逐项核对一致。\n通过后：台账由程序自动写入；发布安排（北京时间）：${this.planText(moved)}，`)
      +(this.env.PUBLISH_ENABLED==='true'?'到点自动发布。':'目前发布总开关关闭，只演练、不发布。'),
      [[{text:'✅ 图片与正式稿通过',callback_data:`final_ok:${day}:${moved.rev}`},
        {text:'↩️ 退回修改',callback_data:`final_reject:${day}:${moved.rev}`}]]);
    return {status:200};
  }
  async completeLedger(job,body) {
    const closed=await this.closeJob(job.id,body.ok?'done':'failed',body.ok?{result:body.result}:{error:body.error});
    if(!closed) return {status:200,duplicate:true};
    const flow=await this.getFlow(job.day);
    if(flow && flow.ledger_job===job.id) await this.s.put(this.flowKey(job.day),{...flow,ledger_status:body.ok?'written':'failed',ledger_result:body.result||null});
    if(body.ok) {
      const actions=(body.result?.actions||[]);
      await this.say(`台账已由程序写入 Notion：${actions.length?actions.join('；'):'没有需要新增的内容（之前已写过）'}。`);
    } else {
      await this.say(`台账写入没有成功：${body.error}。已保留原台账不动；下次轮询会自动重试。`);
      await this.s.transaction(async tx=>{
        const current=await tx.get('job:'+job.id);
        if(current && job.attempts<MAX_ATTEMPTS) {
          await tx.put('job:'+job.id,{...current,status:'queued'});
          await tx.put('jobs-open',[...new Set([...((await tx.get('jobs-open'))||[]),job.id])]);
        }
      });
    }
    return {status:200};
  }

  async retryPoster(day) {
    const flow=await this.getFlow(day);
    if(!flow || flow.status!=='poster_failed') return;
    const snapshot=await this.s.get('snapshot:'+day),draft=await this.s.get('draft:'+day);
    const rev=flow.rev+1;
    let payload;
    try { payload=flow.kind==='weekly'?flow.weekly_pack:posterPayload({day,issue:flow.issue,draft,snapshot,body:flow.formal.zh,english:flow.formal.en}); if(!payload) throw Error('周日数据包丢失，请重新运行周日清单'); }
    catch(error) { await this.say(`还不能出图：${error.message}`,[[{text:'🔁 重试出图',callback_data:`poster_retry:${day}`}]]); return; }
    const moved=await this.cas(this.flowKey(day),['poster_failed'],{status:'poster_pending',rev,poster_job:`poster:${day}:${rev}`,files:{},failure:null});
    if(!moved) return;
    await this.enqueue({id:moved.poster_job,type:'poster',day,rev,payload});
    await this.watch(day,rev);
    await this.say('已重新排队出图。');
  }

  // ---------- step 2: final approval ----------
  async finalApprove(day,rev) {
    const approved=await this.cas(this.flowKey(day),['awaiting_final'],current=>current.rev===Number(rev)?{status:'final_approved',approved:this.now()}:{});
    if(!approved || approved.status!=='final_approved') return false;
    if(approved.kind==='weekly') {
      const lines=['图片与正式稿已通过。','台账：周日清单没有新订单，不写台账。'];
      lines.push(...await this.schedule(day));
      await this.say(lines.join('\n'));
      return true;
    }
    // From now on this day owns its issue number, so tomorrow's number follows it even if the ledger has no new order.
    await this.s.transaction(async tx=>{ await tx.put('issues',{...((await tx.get('issues'))||{}),[day]:approved.issue}); });
    const snapshot=await this.s.get('snapshot:'+day),draft=await this.s.get('draft:'+day);
    const lines=['图片与正式稿已通过。'];
    // Notion ledger: written by the program (the Notion token lives in GitHub Actions), append-only.
    const ledger=ledgerPayload({day,issue:approved.issue,draft,snapshot});
    if(ledger) {
      const id=`ledger:${day}:${approved.rev}`;
      await this.s.put(this.flowKey(day),{...(await this.getFlow(day)),ledger_job:id,ledger_status:'queued'});
      await this.enqueue({id,type:'ledger',day,rev:approved.rev,payload:ledger});
      lines.push('台账：已排队，程序会自动写入 Notion（只追加，不改原记录）。');
    } else lines.push('台账：今天没有新单也没有状态变化，不需要写入。');
    const schedule=await this.schedule(day);
    lines.push(...schedule);
    await this.say(lines.join('\n'));
    return true;
  }
  async finalReject(day,rev) {
    const rejected=await this.cas(this.flowKey(day),['awaiting_final'],current=>current.rev===Number(rev)?{status:'final_rejected',rejected:this.now()}:{});
    if(!rejected || rejected.status!=='final_rejected') return false;
    await this.arm();
    if(rejected.kind==='weekly') {
      await this.say('已退回，没有排发布。周日清单没有文字稿可改：到 GitHub 的 Actions 里手动运行「周日下周清单」就会按最新数据重新出一份送审。');
      return true;
    }
    await this.say('已退回，没有写台账也没有排发布。发送修改要求或直接发送 /draft 重新生成送审稿；也可以点下面按钮用同一份文字稿重出正式稿和图片。',
      [[{text:'🔁 用原文字稿重出',callback_data:`formal_retry:${day}`}]]);
    return true;
  }

  // ---------- step 3: schedule zh 20:00 / en 21:00 (Beijing). Never publish late on our own. ----------
  async schedule(day) {
    const lines=[];
    const weekly=(await this.getFlow(day))?.kind==='weekly';
    if(weekly ? !weeklyDay(day).ok : !eligible(day).ok) {
      await this.s.put(this.flowKey(day),{...(await this.getFlow(day)),slots:{}});
      return [`今天（${day}）不是发布日，不排发布时间。`];
    }
    const now=this.now(),slots={},plan=this.plan(weekly?'weekly':'daily');
    if(!Object.keys(plan).length) {
      await this.s.put(this.flowKey(day),{...(await this.getFlow(day)),slots:{}});
      return ['没有可用的发布目标（检查 DAILY_TARGETS / WEEKLY_TARGETS / TG_CHANNEL），没有排发布。'];
    }
    for(const lang of Object.keys(plan)) {
      const due=weekly?weeklyDueAt(day,lang):dueAt(day,lang);
      slots[lang]={due,targets:plan[lang],status:now<=due+GRACE_MS?'scheduled':'missed'};
    }
    await this.s.transaction(async tx=>{
      await tx.put(this.flowKey(day),{...(await tx.get(this.flowKey(day))),slots});
      const open=(await tx.get('slots-open'))||[];
      if(!open.includes(day)) await tx.put('slots-open',[...open,day]);
    });
    for(const lang of Object.keys(plan)) {
      const time=`${day} ${weekly?WEEKLY_TIME:TIME_TEXT[lang]}`;
      if(slots[lang].status==='scheduled') lines.push(`${this.slotName({kind:weekly?'weekly':'daily'},lang)}：北京时间 ${time} 发布到${this.targetText(plan[lang])}${this.env.PUBLISH_ENABLED==='true'?'':'（发布总开关关闭，只演练）'}。`);
      else {
        lines.push(`${this.slotName({kind:weekly?'weekly':'daily'},lang)}：${time} 已经错过，不会擅自补发，等你决定。`);
        await this.askLate(day,lang);
      }
    }
    await this.arm();
    return lines;
  }
  async askLate(day,lang) {
    const flow=await this.getFlow(day);
    await this.say(`${this.slotName(flow,lang)}的计划时间（北京时间 ${day} ${this.timeText(flow,lang)}）已经过了。要现在补发吗？程序不会自己补发。`,
      [[{text:'现在补发',callback_data:`late_post:${day}:${lang}`},{text:'这条不发了',callback_data:`late_skip:${day}:${lang}`}]]);
  }
  async arm() {
    const open=(await this.s.get('slots-open'))||[];
    let next=null;
    for(const day of open) {
      const flow=await this.getFlow(day);
      for(const lang of LANGS) {
        const slot=flow?.slots?.[lang];
        if(slot?.status==='scheduled' && (next===null || slot.due<next)) next=slot.due;
      }
    }
    for(const item of (await this.s.get('watchdog'))||[]) {
      const flow=await this.getFlow(item.day);
      if(flow && flow.rev===item.rev && flow.status==='poster_pending' && (next===null || item.at<next)) next=item.at;
    }
    if(next!==null && this.s.setAlarm) await this.s.setAlarm(Math.max(next,this.now()+1000));
    else if(next===null && this.s.deleteAlarm) await this.s.deleteAlarm();
  }
  async alarm() {
    const dogs=(await this.s.get('watchdog'))||[];
    const rest=[];
    for(const dog of dogs) {
      if(dog.at>this.now()) { rest.push(dog); continue; }
      const flow=await this.getFlow(dog.day);
      if(flow && flow.rev===dog.rev && flow.status==='poster_pending')
        await this.fail(dog.day,dog.rev,'poster_failed','超过25分钟还没有出图（海报任务没人接，或出图中途出错）','poster_retry');
    }
    await this.s.put('watchdog',rest);
    const open=(await this.s.get('slots-open'))||[];
    for(const day of open) {
      for(const lang of LANGS) {
        const flow=await this.getFlow(day);
        const slot=flow?.slots?.[lang];
        if(!slot || slot.status!=='scheduled' || slot.due>this.now()) continue;
        if(this.now()-slot.due>GRACE_MS) {
          const missed=await this.slotCas(day,lang,['scheduled'],{status:'missed'});
          if(missed) await this.askLate(day,lang);
          continue;
        }
        await this.fire(day,lang);
      }
    }
    const left=[];
    for(const day of open) {
      const flow=await this.getFlow(day);
      if(LANGS.some(lang=>flow?.slots?.[lang]?.status==='scheduled')) left.push(day);
    }
    await this.s.put('slots-open',left);
    await this.arm();
  }
  async slotCas(day,lang,from,patch) {
    return this.s.transaction(async tx=>{
      const flow=await tx.get(this.flowKey(day));
      const slot=flow?.slots?.[lang];
      if(!slot || !from.includes(slot.status)) return null;
      const next={...flow,slots:{...flow.slots,[lang]:{...slot,...patch}}};
      await tx.put(this.flowKey(day),next);
      return next.slots[lang];
    });
  }

  // Claim first (scheduled -> publishing), act second: at most one attempt per slot, even if the alarm re-fires.
  // A slot may carry several targets (X, TG channel); each target remembers its own progress so a retry never repeats a finished one.
  async fire(day,lang,{late=false}={}) {
    const claimed=await this.slotCas(day,lang,late?['missed']:['scheduled'],{status:'publishing',claimed:this.now()});
    if(!claimed) return false;
    const flow=await this.getFlow(day);
    const targets=claimed.targets||['x'];
    const live=this.env.PUBLISH_ENABLED==='true';
    let current=targets[0];
    try {
      if(!live) {
        await this.slotCas(day,lang,['publishing'],{status:'dry_run',finished:this.now()});
        const text=stripTradeLog(flow.formal[lang+'_post']);
        const images=targets.includes('x')?this.xKeys(flow,lang).length:1;
        await this.say(`${targets.includes('x')?'【演练，未发推】':'【演练，未发布】'}${this.label(flow)}${this.slotName(flow,lang)}到点了（北京时间 ${this.timeText(flow,lang)}）。发布总开关是关闭的，所以没有真正发布；开启后这里会发布到${this.targetText(targets)}，附${images}张图：\n\n${text}`);
        return true;
      }
      for(const target of targets) {
        current=target;
        if(target==='tg') await this.publishTg(day,lang,flow);
        else await this.publishX(day,lang,flow);
      }
      await this.slotCas(day,lang,['publishing'],{status:'posted',finished:this.now()});
      return true;
    } catch(error) {
      const slot=(await this.getFlow(day)).slots[lang];
      // If the post call itself may have gone through, never retry blindly.
      // An HTTP 4xx means the post was refused, so it definitely did not go out; only timeouts/5xx are unknown.
      const refused=error.status>=400 && error.status<500;
      let ambiguous,reason,where;
      if(current==='tg') {
        ambiguous=!refused;
        reason=tgErrorReason(error);
        where='TG频道';
      } else {
        ambiguous=slot.media_ids && !slot.tweet_id && !refused;
        reason=xErrorReason(error);
        where='X';
      }
      await this.slotCas(day,lang,['publishing'],{status:ambiguous?'unknown':'failed',error:`${where}：${reason}`.slice(0,1000)});
      await this.say(`${this.label(flow)}${this.slotName(flow,lang)}发布到${where}没有成功。返回：${reason}。${ambiguous?`发布请求可能已经送达${where}，请先去确认，程序不会自动重发。`:'程序不会自动重试，请处理后告诉我。'}已经成功的目标不会重复发。`);
      return false;
    }
  }
  // Weekday: that language's posters. Sunday: the zh and en posters together in one post.
  xKeys(flow,lang) {
    const keys=flow.kind==='weekly'?['zh_desktop','en_desktop']:[lang+'_desktop',lang+'_mobile'];
    return keys.filter(key=>flow.names?.[key]);
  }
  async publishX(day,lang,flow) {
    const slot=(await this.getFlow(day)).slots[lang];
    if(slot.tweet_id) return; // finished on an earlier attempt
    const text=limitCashtags(stripTradeLog(flow.formal[lang+'_post']));
    const keys=this.xKeys(flow,lang);
    const account=await this.s.get('x-account');
    if(!account || account.username?.toLowerCase()!==String(this.env.EXPECTED_X_USERNAME).toLowerCase()) throw Error('X账号与预期不符');
    const token=await accessToken({storage:this.s,env:this.env,request:this.d.provider,now:this.now()});
    await checkPostLength({token,text,request:this.d.provider,premium:this.env.X_PREMIUM==='true'});
    const mediaIds=[];
    for(const key of keys) {
      const bytes=await this.d.tgDownload(this.env,flow.files[key].file_id);
      mediaIds.push(await uploadImage({token,bytes,name:flow.names[key],request:this.d.provider}));
    }
    await this.slotCas(day,lang,['publishing'],{media_ids:mediaIds});
    const tweetId=await createPost({token,text,mediaIds,request:this.d.provider});
    await this.slotCas(day,lang,['publishing'],{tweet_id:tweetId});
    await this.say(`已发布${this.label(flow)}${this.slotName(flow,lang)}：https://x.com/${account.username}/status/${tweetId}`);
  }
  // Channel post: the mobile poster (desktop one when only that exists) with the Chinese/English text as caption (<=1024) or a second message.
  async publishTg(day,lang,flow) {
    const channel=this.env.TG_CHANNEL;
    if(!channel) throw Object.assign(Error('没有配置 TG_CHANNEL'),{status:400});
    const slot=(await this.getFlow(day)).slots[lang];
    const done={...(slot.tg||{})};
    const text=stripTradeLog(flow.formal[lang+'_post']);
    if(text.length>TG_TEXT_MAX) throw Object.assign(Error(`正文${text.length}字，超过Telegram单条上限${TG_TEXT_MAX}`),{status:400});
    const key=[lang+'_mobile',lang+'_desktop'].find(k=>flow.names?.[k] && flow.files?.[k]);
    const inCaption=Boolean(key) && text.length<=TG_CAPTION_MAX;
    const save=()=>this.slotCas(day,lang,['publishing'],{tg:done});
    if(key && !done.photo) {
      const bytes=await this.d.tgDownload(this.env,flow.files[key].file_id);
      const res=await this.d.tgChannelPhoto(this.env,channel,flow.names[key],bytes,inCaption?text:'');
      done.photo=res?.result?.message_id||true;
      await save();
    }
    if(!inCaption && !done.text) {
      const res=await this.d.telegram(this.env,'sendMessage',{chat_id:channel,text,disable_web_page_preview:true});
      done.text=res?.result?.message_id||true;
      await save();
    }
    const first=Number.isInteger(done.photo)?done.photo:done.text;
    const link=String(channel).startsWith('@') && Number.isInteger(first)?`：https://t.me/${String(channel).slice(1)}/${first}`:'';
    await this.say(`已发布${this.label(flow)}${this.slotName(flow,lang)}到TG频道 ${channel}${link}`);
  }
  // Start the post-approval flow over for a day that has not published anything (e.g. a wrong issue number was found).
  async reissue(day) {
    const flow=await this.getFlow(day);
    if(!flow) { await this.say('今天还没有走到正式稿这一步，不需要重来。'); return false; }
    const slots=Object.values(flow.slots||{});
    if(slots.some(slot=>!['scheduled','missed','skipped','dry_run'].includes(slot.status))) {
      await this.say('今天已经有一条开始发布或发布过了，不能重来。'); return false;
    }
    const reset=await this.s.transaction(async tx=>{
      const current=await tx.get(this.flowKey(day));
      if(!current) return false;
      await tx.put(this.flowKey(day),{...current,status:'final_rejected',slots:{},rejected:this.now(),failure:null});
      await tx.put('slots-open',((await tx.get('slots-open'))||[]).filter(d=>d!==day));
      const {[day]:released,...issues}=(await tx.get('issues'))||{};
      await tx.put('issues',issues);
      return true;
    });
    if(!reset) return false;
    await this.arm();
    await this.startFormal(day);
    return true;
  }
  // A day's issue has been fully published (or skipped): shelve it so the same date can carry another issue.
  async archiveDay(day) {
    return this.s.transaction(async tx=>{
      const flow=await tx.get(this.flowKey(day));
      if(!flow || flow.status!=='final_approved') return false;
      const slots=Object.values(flow.slots||{});
      if(!slots.length || !slots.every(slot=>['posted','dry_run','skipped'].includes(slot.status))) return false;
      await tx.put(`archive:${day}:${flow.rev}`,{flow,draft:await tx.get('draft:'+day),archived:this.now()});
      const {[day]:issue,...rest}=(await tx.get('issues'))||{};
      if(Number.isInteger(issue)) rest[`${day}#${flow.rev}`]=issue;
      await tx.put('issues',rest);
      await tx.put(this.flowKey(day),{day,status:'archived',rev:flow.rev,slots:{},history:[...(flow.history||[]),{rev:flow.rev,issue:flow.issue}]});
      await tx.delete('draft:'+day);
      return true;
    });
  }
  // Re-send a slot whose last attempt failed (or ended unknown and the reviewer checked X: nothing was posted).
  async retryPost(day) {
    const flow=await this.getFlow(day);
    if(!flow || flow.status!=='final_approved') { await this.say('现在不能重发：今天的图片与正式稿还没有终审通过。'); return false; }
    let fired=0;
    for(const lang of this.slotLangs(flow)) {
      const slot=flow.slots?.[lang];
      if(slot && ['failed','unknown'].includes(slot.status)) {
        const reset=await this.slotCas(day,lang,['failed','unknown'],{status:'scheduled',retry_of:slot.error||slot.status,media_ids:null,error:null});
        if(reset && await this.fire(day,lang)) fired++;
      } else await this.say(`${this.slotName(flow,lang)}当前状态是 ${slot?.status||'没有排期'}，这次不重发。`);
    }
    await this.arm();
    return fired>0;
  }
  // The reviewer asked for the post now instead of waiting for the scheduled time. Same at-most-once claim as the alarm.
  async postNow(day) {
    const flow=await this.getFlow(day);
    if(!flow || flow.status!=='final_approved') { await this.say('现在不能发：今天的图片与正式稿还没有终审通过。'); return false; }
    let fired=0;
    for(const lang of this.slotLangs(flow)) {
      const slot=(await this.getFlow(day)).slots?.[lang];
      if(slot?.status==='scheduled') { await this.fire(day,lang); fired++; }
      else await this.say(`${this.slotName(flow,lang)}当前状态是 ${slot?.status||'没有排期'}，这次不重复发。`);
    }
    await this.arm();
    return fired>0;
  }
  async lateDecision(day,lang,post) {
    if(!LANGS.includes(lang)) return false;
    if(!post) {
      const skipped=await this.slotCas(day,lang,['missed'],{status:'skipped',finished:this.now()});
      if(skipped) await this.say(`已记下：${LANG_NAME[lang]}版这条不发了。`);
      return Boolean(skipped);
    }
    return this.fire(day,lang,{late:true});
  }
}
