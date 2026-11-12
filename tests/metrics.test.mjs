import test from 'node:test';
import assert from 'node:assert/strict';
import {recordPost,parseMetrics,collectMetrics,features,report,styleNote,traitCompare,rows,MIN_POSTS_FOR_STYLE} from '../cloud/metrics.mjs';

const mem=()=>{const m=new Map();return {m,get:async k=>m.get(k),put:async(k,v)=>m.set(k,v)};};
const H=3600e3,NOW=Date.UTC(2026,10,20,12);

test('recordPost registers a tweet once',async()=>{
  const s=mem();
  assert.equal(await recordPost(s,{day:'2026-11-12',lang:'zh',tweet_id:'1',text:'a',now:NOW}),true);
  assert.equal(await recordPost(s,{day:'2026-11-12',lang:'zh',tweet_id:'1',text:'a',now:NOW}),false);
  assert.equal(await recordPost(s,{day:'2026-11-12',lang:'zh',tweet_id:null,text:'a'}),false);
  assert.equal(s.m.get('posts-index').length,1);
});

test('parseMetrics reads public_metrics and lists missing posts',()=>{
  const {metrics,missing}=parseMetrics({data:[{id:'1',public_metrics:{impression_count:900,like_count:20,retweet_count:3,reply_count:2,quote_count:1,bookmark_count:5}}],
    errors:[{resource_id:'2',title:'Not Found Error'}]});
  assert.deepEqual(metrics['1'],{imp:900,like:20,rt:3,reply:2,quote:1,bookmark:5});
  assert.deepEqual(missing,['2']);
});

test('collectMetrics snapshots due posts only, asks X once per batch',async()=>{
  const s=mem();
  await recordPost(s,{day:'d',lang:'zh',tweet_id:'1',text:'a',now:NOW-26*H});
  await recordPost(s,{day:'d',lang:'en',tweet_id:'2',text:'b',now:NOW-10*60e3}); // too fresh
  const calls=[];
  const request=async url=>{calls.push(url);return {data:[{id:'1',public_metrics:{impression_count:500,like_count:10}}]};};
  const first=await collectMetrics({storage:s,token:'t',request,now:NOW});
  assert.equal(first.fetched,1);
  assert.match(calls[0],/ids=1&/);
  const again=await collectMetrics({storage:s,token:'t',request,now:NOW+H}); // within the 5h gap
  assert.equal(again.fetched,0);
  const later=await collectMetrics({storage:s,token:'t',request,now:NOW+6*H});
  assert.equal(later.fetched,1);
  assert.equal(s.m.get('post-metrics')['1'].length,2);
});

test('features measure opening and length',()=>{
  const f=features('今天我只挂一张单。$BTC 涨了3%，可能回踩。\n\n第二段。');
  assert.equal(f.first_has_I,true);
  assert.equal(f.first_has_number,true);
  assert.equal(f.hedges,1);
  assert.equal(f.tickers,1);
  assert.equal(f.paragraphs,2);
});

function fixture(n,{lang='zh'}={}) {
  const index=[],store={};
  for(let i=0;i<n;i++) {
    const id=String(100+i),withNum=i%2===0;
    index.push({id,day:`d${i}`,lang,posted_at:NOW-(48+i)*H,text:`${withNum?'今天 3 个数字':'今天没有数字'}开头${'甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳'[i]}\n正文`});
    store[id]=[{t:NOW,imp:withNum?2000+i:800+i,like:10,rt:1,reply:1,quote:0,bookmark:2}];
  }
  return {index,store};
}

test('report refuses style conclusions below the sample gate',()=>{
  const {index,store}=fixture(5);
  const text=report({index,store,now:NOW});
  assert.match(text,/样本不够/);
  assert.doesNotMatch(text,/写法对比/);
});

test('report compares traits once the sample is big enough',()=>{
  const {index,store}=fixture(MIN_POSTS_FOR_STYLE+4);
  const text=report({index,store,now:NOW});
  assert.match(text,/写法对比/);
  assert.match(text,/首句带数字/);
});

test('styleNote stays empty until enough matured zh posts, then shows openings',()=>{
  const small=fixture(MIN_POSTS_FOR_STYLE-1);
  assert.equal(styleNote({...small,now:NOW}),'');
  const en=fixture(20,{lang:'en'});
  assert.equal(styleNote({...en,now:NOW}),'');
  const big=fixture(MIN_POSTS_FOR_STYLE+4);
  const note=styleNote({...big,now:NOW});
  assert.match(note,/读得最多的开头/);
  assert.match(note,/不要照抄/);
});

test('traitCompare needs three posts on each side',()=>{
  const {index,store}=fixture(5);
  assert.deepEqual(traitCompare(rows({index,store,now:NOW})),[]);
});
