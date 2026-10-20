// X (Twitter) publishing. Only reached when PUBLISH_ENABLED is exactly "true"; every call goes through the
// injected `request` so tests never touch the network. A post is text plus its two poster images; no threads.
import {seal,unseal} from './security.mjs';

const MAX_IMAGE_BYTES=5*1024*1024;
const NORMAL_POST_LIMIT=280;
const PREMIUM_POST_LIMIT=25000;
const SHORT_WEIGHT_RANGES=[[0,4351],[8192,8205],[8208,8223],[8242,8247]];
const URL_RE=/https?:\/\/[^\s]+/giu;

// X's legacy 280-character limit is weighted: CJK characters count as 2 and most Latin characters count as 1.
// URLs use X's fixed transformed length. Long posts use the same preflight counter with the Premium allowance.
export function weightedLength(text) {
  let total=0,last=0;
  for(const match of String(text).matchAll(URL_RE)) {
    total+=weightedPlainText(String(text).slice(last,match.index))+23;
    last=match.index+match[0].length;
  }
  return total+weightedPlainText(String(text).slice(last));
}

function weightedPlainText(text) {
  let total=0;
  for(const char of text.normalize('NFC')) {
    const code=char.codePointAt(0);
    total+=SHORT_WEIGHT_RANGES.some(([from,to])=>code>=from && code<=to)?1:2;
  }
  return total;
}

export async function postTextLimit({token,request}) {
  const me=await request('https://api.x.com/2/users/me?user.fields=subscription,subscription_type,verified',{
    headers:{Authorization:'Bearer '+token}},30000);
  const user=me?.data||{};
  const paid=value=>Boolean(value) && !['none','free','false','0'].includes(String(value).toLowerCase());
  return (paid(user.subscription) || paid(user.subscription_type) || user.verified===true)?PREMIUM_POST_LIMIT:NORMAL_POST_LIMIT;
}

export async function checkPostLength({token,text,request,premium=false}) {
  // premium=true: the owner has confirmed the account is paid; X may not return the subscription field, so skip the lookup.
  const length=weightedLength(text),limit=premium?PREMIUM_POST_LIMIT:await postTextLimit({token,request});
  if(length>limit) throw Error(`X正文为${length}个加权字符，当前账号上限为${limit}，已在上传图片前停止发布`);
  return {length,limit};
}

export function xErrorReason(error) {
  const parts=[];
  if(error?.status) parts.push(`HTTP ${error.status}`);
  if(error?.providerMessage) parts.push(String(error.providerMessage));
  else if(error?.message) parts.push(String(error.message));
  return [...new Set(parts)].join('：').slice(0,1000)||'X没有返回错误详情';
}

export async function accessToken({storage,env,request,now=Date.now()}) {
  const sealed=await storage.get('x-tokens');
  if(!sealed) throw Error('X尚未授权');
  let tokens=await unseal(sealed,env.TOKEN_ENCRYPTION_KEY);
  const expiresAt=tokens.obtained_at+(Number(tokens.expires_in)||7200)*1000;
  if(now<expiresAt-120000) return tokens.access_token;
  const refreshed=await request('https://api.x.com/2/oauth2/token',{
    method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded',
      Authorization:'Basic '+btoa(`${env.X_CLIENT_ID}:${env.X_CLIENT_SECRET}`)},
    body:new URLSearchParams({grant_type:'refresh_token',refresh_token:tokens.refresh_token}).toString()});
  if(!refreshed.access_token) throw Error('X授权续期失败，需要重新授权');
  tokens={...tokens,...refreshed,refresh_token:refreshed.refresh_token||tokens.refresh_token,obtained_at:now};
  await storage.put('x-tokens',await seal(tokens,env.TOKEN_ENCRYPTION_KEY));
  return tokens.access_token;
}

export async function uploadImage({token,bytes,name,request}) {
  if(bytes.byteLength>MAX_IMAGE_BYTES) throw Error(`图片 ${name} 超过X的5MB上限`);
  const form=new FormData();
  form.append('media',new Blob([bytes],{type:'image/png'}),'poster.png');
  form.append('media_category','tweet_image');
  const result=await request('https://api.x.com/2/media/upload',{method:'POST',headers:{Authorization:'Bearer '+token},body:form},60000);
  const id=result?.data?.id;
  if(!id) throw Error('X没有返回图片编号');
  return String(id);
}

export async function createPost({token,text,mediaIds,request}) {
  const body={text};
  if(mediaIds?.length) body.media={media_ids:mediaIds};
  const result=await request('https://api.x.com/2/tweets',{method:'POST',
    headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(body)},30000);
  const id=result?.data?.id;
  if(!id) throw Error('X没有返回推文编号');
  return String(id);
}
