import test from 'node:test';
import assert from 'node:assert/strict';
import {randomToken,sha256,matches,isAdmin,seal,unseal,escapeHTML} from '../cloud/security.mjs';
test('PKCE挑战符合RFC7636向量',async()=>{
  assert.equal(await sha256('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'),
    'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM');
});
test('随机state与管理员身份认证',async()=>{
  assert.notEqual(randomToken(),randomToken());
  assert.equal(await matches('a','b'),false);
  assert.equal(await matches(null,null),false);
  assert.equal(await isAdmin(new Request('https://x.test',{headers:{Authorization:'Basic '+btoa('admin:secret')}}),'secret'),true);
  assert.equal(await isAdmin(new Request('https://x.test',{headers:{Authorization:'Basic '+btoa('other:secret')}}),'secret'),false);
});
test('令牌加密、错误密钥和篡改检测',async()=>{
  const secret='test-only-key-'.repeat(4),payload={access_token:'test-access',refresh_token:'test-refresh'};
  const sealed=await seal(payload,secret);
  assert.deepEqual(await unseal(sealed,secret),payload);
  await assert.rejects(unseal(sealed,'wrong-key'.repeat(5)));
  sealed.data[0]^=1;
  await assert.rejects(unseal(sealed,secret));
});
test('管理页转义外部内容',()=>{
  assert.equal(escapeHTML('<script>"&'), '&lt;script&gt;&quot;&amp;');
});
