// Local end-to-end rig: the real Worker (TradeDesk) behind a tiny HTTP server, with Telegram / the model / GitHub
// faked. `/__test/*` routes exist only in this file. Run by tests/test_e2e_local.py.
import http from 'node:http';
import {TradeDesk} from '../cloud/worker.mjs';

const EN="Today I'm placing a far-away $ETH short limit order. Entry limit 2,585.57, +4.54% from spot; stop 2,637.28 on a daily close; target 2,378.72, reward-to-risk 1:4, 0.12 size, valid until Oct 31. The order is voided if the daily close gets above the 50-day moving average (about 2,553.10) before it fills.";
let fixed=Date.parse('2026-10-21T10:00:00+08:00');
Date.now=()=>fixed;

const entries=new Map();let alarm=null,tail=Promise.resolve();
const storage={get:async k=>structuredClone(entries.get(k)),put:async(k,v)=>entries.set(k,structuredClone(v)),
  delete:async k=>entries.delete(k),transaction:fn=>{const run=tail.then(()=>fn(storage));tail=run.catch(()=>{});return run;},
  setAlarm:async t=>{alarm=t;},deleteAlarm:async()=>{alarm=null;}};
const env={ADMIN_PASSWORD:'pw',TOKEN_ENCRYPTION_KEY:'k'.repeat(40),EXPECTED_X_USERNAME:'Uncle_Onchain',
  TELEGRAM_WEBHOOK_SECRET:'hook',TELEGRAM_BOT_TOKEN:'tg',TELEGRAM_OWNER_ID:'123',PIPELINE_SECRET:'pipe',
  APINEBULA_API_KEY:'sk-x',TRADE_LOG_URL:'https://notion.example/ledger',PUBLISH_ENABLED:'false',RUN_MODE:'live'};
const desk=new TradeDesk({storage},env);
const log={telegram:[],docs:[],model:[],x:[]};
globalThis.fetch=async(url,options={})=>{
  const u=String(url);
  if(u.includes('apinebula.ai')) {log.model.push(JSON.parse(options.body));return Response.json({choices:[{message:{content:EN},finish_reason:'stop'}]});}
  if(u.includes('x.com')) {log.x.push(u);return Response.json({data:{id:'1'}});}
  if(u.endsWith('/sendDocument')) {const f=options.body;const doc=f.get('document');
    log.docs.push({name:doc.name,size:doc.size,caption:f.get('caption')});
    return Response.json({ok:true,result:{document:{file_id:'F'+log.docs.length}}});}
  if(u.includes('api.telegram.org')) {log.telegram.push({method:u.split('/').pop(),...JSON.parse(options.body)});return Response.json({ok:true});}
  throw Error('unexpected fetch '+u);
};
const server=http.createServer(async(req,res)=>{
  const chunks=[];for await(const c of req) chunks.push(c);
  const body=Buffer.concat(chunks);
  const url=new URL(req.url,'http://127.0.0.1');
  const send=(status,data)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
  if(url.pathname==='/__test/log') return send(200,{log,alarm,entries:Object.fromEntries(entries)});
  if(url.pathname==='/__test/clock') {fixed=Date.parse(url.searchParams.get('iso'));return send(200,{ok:true});}
  if(url.pathname==='/__test/seed') {for(const [k,v] of Object.entries(JSON.parse(body))) entries.set(k,v);return send(200,{ok:true});}
  if(url.pathname==='/__test/alarm') {await desk.alarm();return send(200,{ok:true});}
  const request=new Request('http://desk.test'+req.url,{method:req.method,headers:req.headers,body:['GET','HEAD'].includes(req.method)?undefined:body});
  const response=await desk.fetch(request);
  res.writeHead(response.status,{'Content-Type':response.headers.get('Content-Type')||'application/json'});
  res.end(Buffer.from(await response.arrayBuffer()));
});
server.listen(0,'127.0.0.1',()=>console.log('PORT '+server.address().port));
