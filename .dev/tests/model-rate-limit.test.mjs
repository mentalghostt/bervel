import { test } from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { observeWebResponses } from '../../codex-runner/web-evidence.mjs'

const NOW = Date.parse('2030-01-01T00:00:00Z')

async function withProvider(replies, action, options = {}) {
  const calls = [], waits = []
  const controller = new AbortController()
  const provider = http.createServer(async (request, response) => {
    const chunks = []
    for await (const chunk of request) chunks.push(chunk)
    calls.push(Buffer.concat(chunks).toString('utf8'))
    const reply = replies[Math.min(calls.length - 1, replies.length - 1)]
    if (reply.disconnect) { request.socket.destroy(); return }
    response.writeHead(reply.status, { 'Content-Type': 'application/json', ...reply.headers })
    response.end(reply.body || (reply.status === 200 ? '{"ok":true}' : '{"error":{"code":"rate_limit_exceeded","message":"private-provider-detail"}}'))
  })
  await new Promise(resolve => provider.listen(0, '127.0.0.1', resolve))
  const evidence = await observeWebResponses({baseUrl:`http://127.0.0.1:${provider.address().port}`,apiKey:'test-only'},
    AbortSignal.any([controller.signal,AbortSignal.timeout(5000)]), {
      now: () => NOW,
      wait: async (ms, value, {signal}) => {
        waits.push(ms)
        if (options.abortDuringWait) controller.abort()
        signal.throwIfAborted()
      },
    })
  const send = () => fetch(evidence.baseUrl + '/responses', {
    method:'POST',headers:{Authorization:`Bearer ${evidence.token}`,'Content-Type':'application/json'},
    body:'{"model":"fixture","input":"unchanged"}',
  })
  try { await action({send,calls,waits,evidence}) }
  finally { await evidence.close(); provider.closeAllConnections(); await new Promise(resolve => provider.close(resolve)) }
}

test('a rejected response waits ten seconds and retries the same body before returning success', async () => {
  await withProvider([{status:429},{status:200}], async ({send,calls,waits,evidence}) => {
    const response = await send()
    assert.equal(response.status,200)
    assert.deepEqual(await response.json(),{ok:true})
    assert.deepEqual(waits,[10000])
    assert.deepEqual(calls,['{"model":"fixture","input":"unchanged"}','{"model":"fixture","input":"unchanged"}'])
    assert.equal(evidence.failure,null)
  })
})

test('persistent 429 stops after the original request plus three retries and seventy seconds of waits', async () => {
  await withProvider([{status:429}], async ({send,calls,waits,evidence}) => {
    const response = await send()
    assert.equal(response.status,429)
    assert.equal((await response.text()).includes('private-provider-detail'),false)
    assert.equal(calls.length,4)
    assert.deepEqual(waits,[10000,20000,40000])
    assert.equal(evidence.failure.status,429)
    assert.equal(evidence.failure.code,'rate_limit_exceeded')
  })
})

for (const [name,header,expected] of [
  ['seconds','30',30000],
  ['http-date','Tue, 01 Jan 2030 00:00:30 GMT',30000],
  ['http-date-rfc850','Tuesday, 01-Jan-30 00:00:30 GMT',30000],
  ['http-date-asctime','Tue Jan  1 00:00:30 2030',30000],
  ['invalid','not-a-delay',10000],
  ['negative','-1',10000],
  ['zero','0',0],
]) test(`Retry-After ${name} is respected before a successful retry`, async () => {
  await withProvider([{status:429,headers:{'Retry-After':header}},{status:200}], async ({send,waits}) => {
    const response = await send()
    assert.equal(response.status,200);await response.text()
    assert.deepEqual(waits,[expected])
  })
})

test('a cooldown beyond the remaining wait budget is never shortened', async () => {
  await withProvider([{status:429,headers:{'Retry-After':'40'}}], async ({send,calls,waits}) => {
    const response = await send();await response.text()
    assert.equal(response.status,429)
    assert.equal(calls.length,2)
    assert.deepEqual(waits,[40000])
  })
})

test('a cooldown longer than the entire budget does not resend the request', async () => {
  await withProvider([{status:429,headers:{'Retry-After':'71'}}], async ({send,calls,waits}) => {
    const response = await send();await response.text()
    assert.equal(response.status,429)
    assert.equal(calls.length,1)
    assert.deepEqual(waits,[])
  })
})

for (const header of ['Tuesday, 01-Jan-30 00:01:30 GMT','Tue Jan  1 00:01:30 2030','9'.repeat(400)]) {
  test(`an oversized cooldown ${header.slice(0,40)} is never replaced with a shorter fallback`, async () => {
    await withProvider([{status:429,headers:{'Retry-After':header}}], async ({send,calls,waits}) => {
      const response=await send();await response.text()
      assert.equal(response.status,429)
      assert.equal(calls.length,1)
      assert.deepEqual(waits,[])
    })
  })
}

for (const code of ['insufficient_quota','credit_balance_exhausted','organization_spend_limit_exceeded','project_spend_limit_exceeded','organization_usage_limit_exceeded']) {
  test(`quota error ${code} is not retried`, async () => {
    await withProvider([{status:429,headers:{'Retry-After':'0'},body:JSON.stringify({error:{code,message:'private-provider-detail'}})}], async ({send,calls,waits}) => {
      const response=await send();await response.text()
      assert.equal(response.status,429)
      assert.equal(calls.length,1)
      assert.deepEqual(waits,[])
    })
  })
}

test('a transport disconnect with unknown outcome is never retried', async () => {
  await withProvider([{disconnect:true}], async ({send,calls,waits,evidence}) => {
    const response=await send();await response.text()
    assert.equal(response.status,502)
    assert.equal(calls.length,1)
    assert.deepEqual(waits,[])
    assert.equal(evidence.failure.category,'transport')
  })
})

test('cancellation during cooldown does not issue another request', async () => {
  await withProvider([{status:429}], async ({send,calls,waits}) => {
    const response=await send();await response.text()
    assert.equal(response.status,502)
    assert.equal(calls.length,1)
    assert.deepEqual(waits,[10000])
  }, {abortDuringWait:true})
})

test('a transport failure after a rejected response does not leave a stale retryable 429', async () => {
  await withProvider([{status:429},{disconnect:true}], async ({send,calls,waits,evidence}) => {
    const response=await send();await response.text()
    assert.equal(response.status,502)
    assert.equal(calls.length,2)
    assert.deepEqual(waits,[10000])
    assert.equal(evidence.failure.category,'transport')
    assert.equal(evidence.failure.status,undefined)
  })
})
