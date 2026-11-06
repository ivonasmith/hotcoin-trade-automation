import test from 'node:test';
import assert from 'node:assert/strict';
import {TradeDesk} from '../cloud/worker.mjs';

function rig() {
  const entries=new Map();
  const storage={get:async k=>entries.get(k),put:async(k,v)=>entries.set(k,v),delete:async k=>entries.delete(k),transaction:async fn=>fn(storage)};
  storage.setAlarm=async()=>{};storage.deleteAlarm=async()=>{};
  const env={ADMIN_PASSWORD:'p',TOKEN_ENCRYPTION_KEY:'test-key-'.repeat(6),TELEGRAM_WEBHOOK_SECRET:'hook',TELEGRAM_BOT_TOKEN:'tok',TELEGRAM_OWNER_ID:'123',PIPELINE_SECRET:'pipe-secret'};
  const desk=new TradeDesk({storage},env);
  const sent=[],old=globalThis.fetch;
  globalThis.fetch=async(url,options)=>{sent.push({url,form:options.body});return Response.json({ok:true});};
  return {desk,sent,restore:()=>{globalThis.fetch=old;}};
}
const post=(desk,body,secret='pipe-secret')=>desk.fetch(new Request('https://desk.test/pipeline/document',{method:'POST',
  headers:{Authorization:'Bearer '+secret,'Content-Type':'application/json'},body:JSON.stringify(body)}));
const b64=text=>Buffer.from(text,'utf8').toString('base64');

test('主机发文件：作为文档发给审核人，类型按扩展名',async()=>{
  const {desk,sent,restore}=rig();
  try {
    const res=await post(desk,{name:'rotation_report.html',content_b64:b64('<p>报告</p>'),caption:'测试'});
    assert.equal(res.status,200);
    assert.match(sent.at(-1).url,/sendDocument$/);
    const file=sent.at(-1).form.get('document');
    assert.equal(file.name,'rotation_report.html');
    assert.equal(file.type,'text/html');
    assert.equal(await file.text(),'<p>报告</p>');
    assert.equal(sent.at(-1).form.get('chat_id'),'123');
    assert.equal(sent.at(-1).form.get('caption'),'测试');
  } finally {restore();}
});

test('主机发文件：没密钥、内容不对、文件名为空都拒绝；大文件可以通过（>100KB），超过 5MB 拒绝',async()=>{
  const {desk,sent,restore}=rig();
  try {
    assert.equal((await post(desk,{name:'a.json',content_b64:b64('{}')},'wrong')).status,403);
    assert.equal((await post(desk,{name:'a.json',content_b64:'###'})).status,400);
    assert.equal((await post(desk,{name:'',content_b64:b64('x')})).status,400);
    assert.equal(sent.length,0);
    assert.equal((await post(desk,{name:'big.json',content_b64:b64('x'.repeat(300000))})).status,200);
    const huge=await desk.fetch(new Request('https://desk.test/pipeline/document',{method:'POST',
      headers:{Authorization:'Bearer pipe-secret','Content-Type':'application/json','Content-Length':String(6*1024*1024)},body:'{}'}));
    assert.equal(huge.status,413);
  } finally {restore();}
});
