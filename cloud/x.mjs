// X (Twitter) publishing. Only reached when PUBLISH_ENABLED is exactly "true"; every call goes through the
// injected `request` so tests never touch the network. A post is text plus its two poster images; no threads.
import {seal,unseal} from './security.mjs';

const MAX_IMAGE_BYTES=5*1024*1024;

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
  form.append('media_type','image/png');
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
