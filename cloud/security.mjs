const enc = new TextEncoder();
export function base64url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
}
export function randomToken() { return base64url(crypto.getRandomValues(new Uint8Array(32))); }
export async function sha256(text) {
  return base64url(new Uint8Array(await crypto.subtle.digest('SHA-256',enc.encode(text))));
}
export async function matches(a,b) {
  // Compare fixed-size hashes rather than variable-length secrets.
  if (!a || !b) return false;
  const [x,y] = await Promise.all([sha256(a),sha256(b)]);
  let diff=0;
  for(let i=0;i<x.length;i++) diff |= x.charCodeAt(i)^y.charCodeAt(i);
  return diff===0;
}
export async function isAdmin(request,password) {
  const header=request.headers.get('Authorization')||'';
  if(!header.startsWith('Basic ') || !password) return false;
  try {
    const raw=atob(header.slice(6));
    return raw.startsWith('admin:') && await matches(raw.slice(6),password);
  } catch { return false; }
}
export async function adminFormToken(password,origin,hour=Math.floor(Date.now()/3600000)) {
  const mac=await crypto.subtle.importKey('raw',enc.encode(password),
    {name:'HMAC',hash:'SHA-256'},false,['sign']);
  return base64url(new Uint8Array(await crypto.subtle.sign('HMAC',mac,
    enc.encode(`trade-desk-form:${origin}:${hour}`))));
}
export async function validAdminPost(request,password) {
  const expected=new URL(request.url).origin;
  const origin=request.headers.get('Origin');
  if(origin===expected) return true;
  // Privacy policies and embedded browsers may send null/no Origin for a form.
  // In that case require a secret token from the authenticated page instead.
  if(origin && origin!=='null') return false;
  const raw=await request.clone().text();
  if(raw.length>20000) return false;
  const token=new URLSearchParams(raw).get('_csrf');
  const hour=Math.floor(Date.now()/3600000);
  return await matches(token,await adminFormToken(password,expected,hour)) ||
    await matches(token,await adminFormToken(password,expected,hour-1));
}
async function key(secret) {
  if (!secret || secret.length<32) throw Error('未配置令牌加密密钥');
  return crypto.subtle.importKey('raw',await crypto.subtle.digest('SHA-256',enc.encode(secret)),
    'AES-GCM',false,['encrypt','decrypt']);
}
export async function seal(value,secret) {
  const iv=crypto.getRandomValues(new Uint8Array(12));
  const data=await crypto.subtle.encrypt({name:'AES-GCM',iv},await key(secret),enc.encode(JSON.stringify(value)));
  return {iv:Array.from(iv),data:Array.from(new Uint8Array(data))};
}
export async function unseal(value,secret) {
  const data=await crypto.subtle.decrypt({name:'AES-GCM',iv:new Uint8Array(value.iv)},
    await key(secret),new Uint8Array(value.data));
  return JSON.parse(new TextDecoder().decode(data));
}
export function escapeHTML(value) {
  return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
