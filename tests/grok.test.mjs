import test from 'node:test';
import assert from 'node:assert/strict';
import {grokHotspots,grokPrompt,responseText} from '../cloud/grok.mjs';

const ok=body=>async(url,options)=>{
  ok.last={url,options,body:JSON.parse(options.body)};
  return {ok:true,status:200,text:async()=>JSON.stringify(body)};
};

test('request asks for x_search with a date window and a turn cap',async()=>{
  const fetcher=ok({output:[{type:'message',content:[{type:'output_text',text:'宏观：…'}]}],
    usage:{server_side_tool_usage_details:{x_posts_fetched:60,x_users_fetched:2}}});
  const result=await grokHotspots({XAI_API_KEY:'k'},'2026-11-10',{coins:['BTC','SOL'],fetcher});
  const body=ok.last.body;
  assert.equal(ok.last.url,'https://api.x.ai/v1/responses');
  assert.equal(ok.last.options.headers.Authorization,'Bearer k');
  assert.equal(body.model,'grok-4.7');
  assert.equal(body.max_turns,3);
  assert.deepEqual(body.tools[0],{type:'x_search',from_date:'2026-11-09',to_date:'2026-11-11'});
  assert.equal(body.tools[1].type,'web_search');
  assert.match(body.input[0].content,/情绪综合得分/);
  assert.equal(result.text,'宏观：…');
  assert.equal(result.posts,60);
  assert.equal(result.estimated_usd,0.32);
});

test('reads output_text when present; errors are readable',async()=>{
  assert.equal(responseText({output_text:' hi '}),'hi');
  await assert.rejects(grokHotspots({},'2026-11-10'),/未配置/);
  const bad=async()=>({ok:false,status:401,text:async()=>JSON.stringify({error:{message:'bad key'}})});
  await assert.rejects(grokHotspots({XAI_API_KEY:'k'},'2026-11-10',{fetcher:bad}),/401.*bad key/);
  const empty=ok({output:[]});
  await assert.rejects(grokHotspots({XAI_API_KEY:'k'},'2026-11-10',{fetcher:empty}),/没有返回正文/);
  assert.match(grokPrompt('2026-11-10'),/一致预期观察/);
});

test('uses the exact cost xAI reports (ticks) and falls back to the per-post estimate',async()=>{
  const exact=ok({output:[{type:'message',content:[{type:'output_text',text:'x'}]}],
    usage:{cost_in_usd_ticks:312500000,server_side_tool_usage_details:{x_posts_fetched:60,x_search_calls:3}}});
  const a=await grokHotspots({XAI_API_KEY:'k'},'2026-11-12',{fetcher:exact});
  assert.equal(a.estimated_usd,0.031);
  assert.equal(a.cost_exact,true);
  assert.equal(a.searches,3);
  const none=ok({output:[{type:'message',content:[{type:'output_text',text:'x'}]}],usage:{server_side_tool_usage_details:{x_posts_fetched:60}}});
  const b=await grokHotspots({XAI_API_KEY:'k'},'2026-11-12',{fetcher:none});
  assert.equal(b.estimated_usd,0.3);
  assert.equal(b.cost_exact,false);
});
