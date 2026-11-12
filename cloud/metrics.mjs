// Post performance tracking for the X posts: record every published tweet id, pull its public metrics on a schedule,
// compare post features against reach, and hand the drafting step a short, evidence-gated style note.
// Pure functions + one fetch helper (injected `request`), so tests never touch the network.
const DAY=86400000;
const KEEP_DAYS=45;       // X only returns public metrics for recent posts reliably; older ones are not refreshed
const MIN_GAP_MS=5*3600e3; // do not snapshot the same post more often than this
const MATURE_MS=24*3600e3; // a post counts as "read" for ranking once it is a day old
export const MIN_POSTS_FOR_STYLE=8; // per language, matured posts needed before any style note reaches the drafting step

// ---- registry ----
export async function recordPost(storage,{day,lang,tweet_id,text,issue,now=Date.now()}) {
  if(!tweet_id) return false;
  const index=(await storage.get('posts-index'))||[];
  if(index.some(item=>item.id===String(tweet_id))) return false;
  index.push({id:String(tweet_id),day,lang,issue:issue||null,posted_at:now,text:String(text||'')});
  await storage.put('posts-index',index.slice(-400));
  return true;
}

// ---- X API ----
const num=value=>Number.isFinite(Number(value))?Number(value):0;
export function parseMetrics(data) {
  const out={},missing=[];
  for(const row of data?.data||[]) {
    const m=row.public_metrics||{};
    out[row.id]={imp:num(m.impression_count),like:num(m.like_count),rt:num(m.retweet_count),reply:num(m.reply_count),
      quote:num(m.quote_count),bookmark:num(m.bookmark_count)};
  }
  for(const err of data?.errors||[]) if(err?.resource_id) missing.push(String(err.resource_id));
  return {metrics:out,missing};
}

export async function fetchMetrics({token,ids,request}) {
  const merged={metrics:{},missing:[]};
  for(let i=0;i<ids.length;i+=100) {
    const batch=ids.slice(i,i+100);
    const data=await request(`https://api.x.com/2/tweets?ids=${batch.join(',')}&tweet.fields=public_metrics,created_at`,
      {headers:{Authorization:'Bearer '+token}},30000);
    const part=parseMetrics(data);
    Object.assign(merged.metrics,part.metrics);
    merged.missing.push(...part.missing);
  }
  return merged;
}

const bjDay=ms=>new Date(ms+8*3600e3).toISOString().slice(0,10);
// One call over the account's own recent posts: registers any post the registry does not know yet (posts made before tracking
// started, or by hand) and stores a read count for each. Replies and retweets are left out.
export async function backfillTimeline({storage,token,userId,request,now=Date.now()}) {
  const data=await request(`https://api.x.com/2/users/${userId}/tweets?max_results=100&exclude=retweets,replies&tweet.fields=public_metrics,created_at,note_tweet`,
    {headers:{Authorization:'Bearer '+token}},30000);
  const index=(await storage.get('posts-index'))||[],store=(await storage.get('post-metrics'))||{};
  let added=0,snapped=0;
  for(const t of data?.data||[]) {
    const text=t.note_tweet?.text||t.text||'';
    const postedAt=Date.parse(t.created_at)||now;
    if(!index.some(item=>item.id===t.id)) {
      index.push({id:String(t.id),day:bjDay(postedAt),lang:/[\u4e00-\u9fff]/.test(text)?'zh':'en',issue:null,posted_at:postedAt,text,backfilled:true});
      added++;
    }
    const m=parseMetrics({data:[t]}).metrics[t.id];
    const shots=store[t.id]||[];
    if(m && (!shots.length || now-shots[shots.length-1].t>=30*60e3)) { store[t.id]=[...shots,{t:now,...m}].slice(-12); snapped++; }
  }
  index.sort((a,b)=>a.posted_at-b.posted_at);
  await storage.put('posts-index',index.slice(-400));
  await storage.put('post-metrics',store);
  return {seen:(data?.data||[]).length,added,snapped};
}

// Pull metrics for every tracked post that is still inside the window and append a snapshot when it is due.
export async function collectMetrics({storage,token,request,now=Date.now()}) {
  const index=((await storage.get('posts-index'))||[]).filter(item=>now-item.posted_at<=KEEP_DAYS*DAY);
  const store=(await storage.get('post-metrics'))||{};
  const due=index.filter(item=>{
    const shots=store[item.id]||[];
    if(now-item.posted_at<30*60e3) return false; // too fresh to say anything
    return !shots.length || now-shots[shots.length-1].t>=MIN_GAP_MS;
  });
  if(!due.length) return {fetched:0,missing:[]};
  const {metrics,missing}=await fetchMetrics({token,ids:due.map(item=>item.id),request});
  let fetched=0;
  for(const item of due) {
    const m=metrics[item.id];
    if(!m) continue;
    store[item.id]=[...(store[item.id]||[]),{t:now,...m}].slice(-12);
    fetched++;
  }
  await storage.put('post-metrics',store);
  return {fetched,missing};
}

// ---- features ----
const HEDGE=/(可能|或许|也许|大概|不排除|有可能|似乎|perhaps|maybe|might)/gi;
export function features(text,lang='zh') {
  const body=String(text||'').replace(/https?:\/\/\S+/g,'').trim();
  const first=body.split(/\n+/)[0]||'';
  const sentences=body.split(/[。！？!?\n.]+/).map(s=>s.trim()).filter(Boolean);
  const len=lang==='zh'?[...body].length:body.split(/\s+/).filter(Boolean).length;
  const firstLen=lang==='zh'?[...first].length:first.split(/\s+/).filter(Boolean).length;
  return {
    len,
    first_len:firstLen,
    first_has_number:/\d/.test(first),
    first_has_I:lang==='zh'?/我/.test(first):/\bI\b|\bI'm\b|\bmy\b/i.test(first),
    numbers:(body.match(/\d+(?:[.,]\d+)?%?/g)||[]).length,
    tickers:(body.match(/\$[A-Za-z]{2,10}/g)||[]).length,
    avg_sentence:sentences.length?Math.round(len/sentences.length):0,
    hedges:(body.match(HEDGE)||[]).length,
    paragraphs:body.split(/\n{2,}/).filter(Boolean).length
  };
}

// ---- reading ----
const interactions=m=>m.like+m.rt+m.reply+m.quote+m.bookmark;
// One row per post: the latest snapshot, but only once the post has had a day to be read.
export function rows({index,store,now=Date.now()}) {
  const out=[];
  for(const item of index) {
    const shots=store[item.id]||[];
    if(!shots.length) continue;
    const last=shots[shots.length-1];
    const matured=now-item.posted_at>=MATURE_MS;
    out.push({...item,imp:last.imp,inter:interactions(last),er:last.imp>0?interactions(last)/last.imp:0,
      replies:last.reply,bookmarks:last.bookmark,matured,f:features(item.text,item.lang)});
  }
  return out;
}

const median=list=>{if(!list.length) return 0;const s=[...list].sort((a,b)=>a-b),m=s.length>>1;return s.length%2?s[m]:(s[m-1]+s[m])/2;};
const pct=x=>(x*100).toFixed(1)+'%';
const firstLine=text=>String(text||'').replace(/https?:\/\/\S+/g,'').trim().split(/\n+/)[0].slice(0,40);

// Compare "has the trait" vs "does not" on median impressions; only reported when each side has at least 3 posts.
const TRAITS=[
  ['首句带数字',r=>r.f.first_has_number],
  ['首句用“我/I”开口',r=>r.f.first_has_I],
  ['首句偏短（短于本组中位数）',(r,ctx)=>r.f.first_len<ctx.firstMedian],
  ['全文偏短（短于本组中位数）',(r,ctx)=>r.f.len<ctx.lenMedian],
  ['带模糊词（可能/或许…）',r=>r.f.hedges>0],
  ['数字偏多（多于本组中位数）',(r,ctx)=>r.f.numbers>ctx.numMedian]
];
export function traitCompare(group) {
  const ctx={firstMedian:median(group.map(r=>r.f.first_len)),lenMedian:median(group.map(r=>r.f.len)),numMedian:median(group.map(r=>r.f.numbers))};
  const lines=[];
  for(const [name,test] of TRAITS) {
    const yes=group.filter(r=>test(r,ctx)),no=group.filter(r=>!test(r,ctx));
    if(yes.length<3 || no.length<3) continue;
    const a=median(yes.map(r=>r.imp)),b=median(no.map(r=>r.imp));
    if(!(a>0 || b>0)) continue;
    lines.push({name,yes:yes.length,no:no.length,with:a,without:b,ratio:b>0?a/b:null});
  }
  return lines;
}

export function report({index,store,now=Date.now(),days=14}) {
  const all=rows({index,store,now}).filter(r=>now-r.posted_at<=days*DAY);
  if(!all.length) return '还没有可统计的帖子数据（发布满 30 分钟后才会有第一次读数）。';
  const out=[`近${days}天 ${all.length} 条帖子的阅读情况（X 公开数据；互动 = 赞+转+评+引用+收藏）：`];
  for(const lang of ['zh','en']) {
    const group=all.filter(r=>r.lang===lang),done=group.filter(r=>r.matured);
    if(!group.length) continue;
    const label=lang==='zh'?'中文':'英文';
    out.push(`\n${label}：共${group.length}条，满24小时${done.length}条。` +
      (done.length?`满24小时的中位数：阅读 ${Math.round(median(done.map(r=>r.imp)))}，互动率 ${pct(median(done.map(r=>r.er)))}，收藏 ${median(done.map(r=>r.bookmarks))}。`:'尚无满24小时的帖子。'));
    const ranked=[...done].sort((a,b)=>b.imp-a.imp);
    if(ranked.length>=2) {
      out.push('阅读最高：'+ranked.slice(0,2).map(r=>`${r.day}（${r.imp}读/${pct(r.er)}）「${firstLine(r.text)}」`).join('；'));
      out.push('阅读最低：'+ranked.slice(-2).reverse().map(r=>`${r.day}（${r.imp}读/${pct(r.er)}）「${firstLine(r.text)}」`).join('；'));
    }
    if(done.length>=MIN_POSTS_FOR_STYLE) {
      const cmp=traitCompare(done);
      if(cmp.length) out.push('写法对比（阅读中位数，有 vs 没有）：\n'+cmp.map(c=>`· ${c.name}：${Math.round(c.with)} vs ${Math.round(c.without)}（${c.yes}条/${c.no}条）`).join('\n'));
      out.push(`样本到${MIN_POSTS_FOR_STYLE}条才下写法结论；现在 ${done.length} 条，对比只是线索，别当定论。`);
    } else out.push(`满24小时的帖子 ${done.length}/${MIN_POSTS_FOR_STYLE} 条，样本不够，暂不下写法结论。`);
  }
  return out.join('\n');
}

// The note the drafting step receives. Empty until enough matured Chinese posts exist; shows openings only (the model
// is told to learn the feel, not copy the lines), so a noisy small sample cannot push a rule into every post.
export function styleNote({index,store,now=Date.now()}) {
  const done=rows({index,store,now}).filter(r=>r.lang==='zh' && r.matured && r.imp>0);
  if(done.length<MIN_POSTS_FOR_STYLE) return '';
  const ranked=[...done].sort((a,b)=>b.imp-a.imp);
  const top=ranked.slice(0,3),low=ranked.slice(-3);
  const cmp=traitCompare(done).filter(c=>c.ratio!==null && (c.ratio>=1.5 || c.ratio<=0.67));
  const lines=['近期读者反馈（来自X真实阅读数据，只用来调语感，不改任何订单数字，也不要照抄这些句子）：',
    '读得最多的开头：'+top.map(r=>`「${firstLine(r.text)}」${r.imp}读`).join('；'),
    '读得最少的开头：'+low.map(r=>`「${firstLine(r.text)}」${r.imp}读`).join('；')];
  if(cmp.length) lines.push('写法线索：'+cmp.map(c=>`${c.name}的帖子阅读中位数是没有的${c.ratio.toFixed(1)}倍`).join('；')+`（样本${done.length}条，仅供参考）`);
  return lines.join('\n');
}
