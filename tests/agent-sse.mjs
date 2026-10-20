import assert from 'node:assert/strict';
import { createHmac, randomUUID } from 'node:crypto';
import test from 'node:test';

// This integration check uses the SDK installed by the sibling AgentService.
const { createFlueClient } = await import('../../AgentService/node_modules/@flue/sdk/dist/index.mjs');
const base = (process.env.AGENT_PROXY_URL ?? 'http://localhost/agent-service').replace(/\/$/, '');
const secret = process.env.JWT_SECRET;
assert.ok(secret, 'Set JWT_SECRET to the same signing key as AuthGateway');
const alice = randomUUID();
const bob = randomUUID();

function token(userId, signingKey = secret) {
  const encode = value => Buffer.from(JSON.stringify(value)).toString('base64url');
  const payload = {
    UserId: userId,
    'http://schemas.microsoft.com/ws/2008/06/identity/claims/role': 'User',
    exp: Math.floor(Date.now() / 1000) + 600,
    iss: process.env.JWT_ISSUER ?? 'AlfaHackathon',
    aud: process.env.JWT_AUDIENCE ?? 'AlfaHackathon',
  };
  const data = `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode(payload)}`;
  return `${data}.${createHmac('sha256', signingKey).update(data).digest('base64url')}`;
}

const aliceHeaders = { Authorization: `Bearer ${token(alice)}`, 'Content-Type': 'application/json' };
async function createChat() {
  const response = await fetch(`${base}/chats`, {
    method: 'POST',
    headers: { ...aliceHeaders, 'X-User-Id': bob, 'X-User-Role': 'NewsAuthor' },
    body: JSON.stringify({ title: `Proxy integration ${randomUUID()}` }),
    signal: AbortSignal.timeout(10000),
  });
  assert.equal(response.status, 201);
  return response.json();
}

test('nginx and Gateway preserve identity boundaries and query strings', async () => {
  assert.equal((await fetch(`${base}/ready`)).status, 200);
  const forged = await fetch(`${base}/chats`, { headers: { 'X-User-Id': alice } });
  assert.equal(forged.status, 401);
  const invalid = await fetch(`${base}/chats`, {
    headers: { Authorization: `Bearer ${token(alice, 'wrong-key-at-least-thirty-two-bytes')}` },
  });
  assert.equal(invalid.status, 401);
  const chat = await createChat();
  const own = await fetch(`${base}/chats/${chat.id}`, { headers: aliceHeaders });
  assert.equal(own.status, 200);
  const other = await fetch(`${base}/chats/${chat.id}`, {
    headers: { Authorization: `Bearer ${token(bob)}` },
  });
  assert.equal(other.status, 404);
  assert.equal((await fetch(`${base}/chats?limit=invalid`, { headers: aliceHeaders })).status, 400);
});

test('real Flue admission, SSE, replay, history and abort work through both proxies', { timeout: 120000 }, async t => {
  const chat = await createChat();
  let streams = 0;
  let chunks = 0;
  let firstChunkAt;
  let lastChunkAt;
  const client = createFlueClient({
    url: `${base}/agents/assistant/${chat.id}`,
    headers: aliceHeaders,
    fetch: async (input, init) => {
      assert.equal(new URL(String(input)).origin, new URL(base).origin, 'SDK must stay on the public origin');
      const response = await fetch(input, init);
      if (!response.headers.get('content-type')?.includes('text/event-stream')) return response;
      streams++;
      assert.equal(response.headers.get('content-encoding'), null);
      return new Response(response.body.pipeThrough(new TransformStream({
        transform(chunk, controller) {
          chunks++;
          firstChunkAt ??= performance.now();
          lastChunkAt = performance.now();
          controller.enqueue(chunk);
        },
      })), { status: response.status, headers: response.headers });
    },
  });
  const input = {
    message: { kind: 'user', body: 'Техническая проверка потокового ответа. Напиши десять коротких советов, как проверять факты в новостях.' },
    idempotencyKey: randomUUID(),
  };
  const admission = await client.send(input);
  const observation = client.observe({ live: 'sse', signal: AbortSignal.timeout(100000) });
  observation.subscribe(() => {});
  t.after(() => observation.close());
  assert.equal(admission.streamUrl, client.url);
  let deltas = 0;
  const reply = await client.read(admission, {
    signal: AbortSignal.timeout(90000),
    onEvent(event) { if (event.type === 'message-delta' && event.kind === 'text') deltas++; },
  });
  assert.ok(reply.text.trim().length > 0);
  assert.ok(streams > 0, 'SDK used SSE');
  assert.ok(chunks > 1, 'Response arrived in multiple chunks');
  assert.ok(deltas > 0, 'Received model text deltas');
  t.diagnostic(`SSE: ${chunks} chunks, ${deltas} text deltas, ${Math.round(lastChunkAt - firstChunkAt)} ms between first and last chunk`);
  const repeated = await client.send(input);
  assert.equal(repeated.submissionId, admission.submissionId);
  assert.equal(repeated.deduplicated, true);
  const replay = await client.read(admission, { signal: AbortSignal.timeout(10000) });
  assert.equal(replay.text, reply.text);
  const history = await client.history();
  assert.equal(history.messages.filter(message => message.role === 'user').length, 1);
  await client.abort();
});
