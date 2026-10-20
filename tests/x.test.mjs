import test from 'node:test';
import assert from 'node:assert/strict';
import {weightedLength,postTextLimit,checkPostLength,uploadImage,createPost,xErrorReason} from '../cloud/x.mjs';

test('X加权字符：中文计2、拉丁计1、URL固定计23',()=>{
  assert.equal(weightedLength('ab中'),4);
  assert.equal(weightedLength('看 https://example.com/a/very/long/path 好'),29);
});

test('X账号订阅状态决定280或25000上限，超限在发布前报错',async()=>{
  const normal=async()=>({data:{subscription_type:'none'}}),premium=async()=>({data:{subscription_type:'premium'}});
  assert.equal(await postTextLimit({token:'t',request:normal}),280);
  assert.equal(await postTextLimit({token:'t',request:premium}),25000);
  await assert.rejects(checkPostLength({token:'t',text:'中'.repeat(141),request:normal}),/282个加权字符.*上限为280/);
  assert.deepEqual(await checkPostLength({token:'t',text:'中'.repeat(141),request:premium}),{length:282,limit:25000});
});

test('图片上传严格使用X v2 multipart字段并读取data.id',async()=>{
  let call;
  const id=await uploadImage({token:'tok',bytes:new Uint8Array([1,2,3]),name:'poster.png',request:async(url,options,timeout)=>{
    call={url,options,timeout};return {data:{id:'media-1'}};
  }});
  assert.equal(id,'media-1');
  assert.equal(call.url,'https://api.x.com/2/media/upload');
  assert.equal(call.options.method,'POST');
  assert.equal(call.options.headers.Authorization,'Bearer tok');
  assert.equal(call.options.headers['Content-Type'],undefined,'multipart边界由运行时生成，不能手填Content-Type');
  assert.equal(call.options.body.get('media_category'),'tweet_image');
  assert.equal(call.options.body.get('media_type'),null,'官方v2简单上传没有media_type字段');
  assert.equal(call.options.body.get('media').type,'image/png');
  assert.equal(call.timeout,60000);
});

test('发帖按X v2 JSON格式传media.media_ids并读取data.id',async()=>{
  let call;
  const id=await createPost({token:'tok',text:'hello',mediaIds:['m1','m2'],request:async(url,options,timeout)=>{
    call={url,options,timeout};return {data:{id:'post-1'}};
  }});
  assert.equal(id,'post-1');
  assert.equal(call.url,'https://api.x.com/2/tweets');
  assert.deepEqual(JSON.parse(call.options.body),{text:'hello',media:{media_ids:['m1','m2']}});
  assert.equal(call.options.headers['Content-Type'],'application/json');
});

test('Telegram可展示X返回的HTTP状态和原始错误',()=>{
  const error=Error('服务请求失败');error.status=403;error.providerMessage='You are not allowed to create a Post';
  assert.equal(xErrorReason(error),'HTTP 403：You are not allowed to create a Post');
});

test('已确认付费账号时不再查询订阅字段，直接按长帖上限检查',async()=>{
  const {checkPostLength}=await import('../cloud/x.mjs');
  const calls=[];
  const result=await checkPostLength({token:'t',text:'中'.repeat(600),premium:true,request:async(...a)=>{calls.push(a);return {};}});
  assert.equal(calls.length,0);
  assert.equal(result.limit,25000);
});
