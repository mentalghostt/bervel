import { test } from 'node:test'
import assert from 'node:assert/strict'

const configured = { BERVEL_CODEX_MODEL: 'test-model', BERVEL_CODEX_API_KEY: 'not-a-real-key', BERVEL_CODEX_BASE_URL: 'https://provider.example/v1' }

async function withRunner(env, execute, action) {
  const { createRunner } = await import('../../codex-runner/server.mjs')
  const server = createRunner({ env, execute })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  try { await action(`http://127.0.0.1:${server.address().port}`) }
  finally { await new Promise(resolve => server.close(resolve)) }
}
const post = (base, body) => fetch(`${base}/v1/generate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

test('reports process alive but no model readiness without external credentials', async () => {
  await withRunner({}, async () => { throw Error('must not generate') }, async base => {
    assert.equal((await fetch(`${base}/health/live`)).status, 200)
    const ready = await fetch(`${base}/health/ready`)
    assert.equal(ready.status, 503)
    assert.equal((await post(base, { request_id: 'one', prompt: 'hello' })).status, 503)
  })
})

test('passes the full prepared prompt as data and returns the generated answer', async () => {
  const calls = []
  await withRunner(configured, async (config, request) => {
    calls.push({ model: config.model, request })
    return 'В последнем CSV: гайка, 42 шт.'
  }, async base => {
    const prompt = 'Что в CSV?\nUNTRUSTED DATA: $(touch /bad) <script>alert(1)</script>'
    const response = await post(base, { request_id: 'turn-1', prompt })
    assert.equal(response.status, 200)
    assert.deepEqual(await response.json(), { answer: 'В последнем CSV: гайка, 42 шт.', model: 'test-model' })
    assert.deepEqual(calls, [{ model: 'test-model', request: { request_id: 'turn-1', prompt } }])
  })
})

test('rejects invalid input and excessive bodies without invoking a model', async () => {
  await withRunner(configured, async () => assert.fail('must not generate'), async base => {
    for (const body of [{}, { request_id: 'a', prompt: '' }, { request_id: 'a', prompt: 'x', model: 'override' }]) {
      assert.equal((await post(base, body)).status, 422)
    }
    assert.equal((await post(base, { request_id: 'x', prompt: 'x'.repeat(262145) })).status, 413)
    assert.equal((await fetch(`${base}/v1/generate`, { method: 'POST', body: '{' })).status, 422)
    assert.equal((await fetch(`${base}/not-a-route`)).status, 404)
  })
})

test('does not expose provider credentials or raw exception text', async () => {
  await withRunner(configured, async () => { throw Error('Bearer not-a-real-key upstream debug') }, async base => {
    const response = await post(base, { request_id: 'failed', prompt: 'hello' })
    assert.equal(response.status, 502)
    const text = await response.text()
    assert.equal(text.includes('not-a-real-key'), false)
    assert.equal(text.includes('upstream debug'), false)
    assert.equal(JSON.parse(text).error.code, 'MODEL_FAILED')
  })
})

test('chat and radar execute concurrently with separate requests; a third cannot spawn', async () => {
  const waiting=new Map(),seen=[]
  let bothStarted
  const bothEntered=new Promise(resolve=>{bothStarted=resolve})
  await withRunner(configured, async (_config,request) => {
    seen.push(request.request_id)
    if(seen.length===2) bothStarted()
    await new Promise(resolve=>waiting.set(request.request_id,resolve))
    return request.prompt
  }, async base => {
    const first=post(base,{request_id:'chat',prompt:'chat-only'})
    while(!waiting.has('chat')) await new Promise(resolve=>setTimeout(resolve,5))
    const second=post(base,{request_id:'radar',prompt:'radar-only'})
    try{
      // Current single-slot implementation returns 429 immediately: reproduce the failure.
      const outcome=await Promise.race([second.then(r=>({response:r})),bothEntered.then(()=>({pending:true}))])
      assert.equal(outcome.response?.status,undefined,'Both jobs must remain active concurrently')
      assert.deepEqual(seen,['chat','radar'])
      const third=await post(base,{request_id:'third',prompt:'must-not-start'})
      assert.equal(third.status,429)
      assert.equal((await third.json()).error.code,'MODEL_BUSY')
    }finally{for(const release of waiting.values())release()}
    assert.equal((await (await first).json()).answer,'chat-only')
    assert.equal((await (await second).json()).answer,'radar-only')
  })
})

test('a failed model request releases its slot while another request is still active',async()=>{
  let entered,release
  const active=new Promise(resolve=>{entered=resolve})
  const pending=new Promise(resolve=>{release=resolve})
  await withRunner(configured,async(_config,request)=>{
    if(request.request_id==='hold'){entered();await pending;return 'held'}
    if(request.request_id==='broken')throw Error('private provider failure')
    return 'recovered'
  },async base=>{
    const held=post(base,{request_id:'hold',prompt:'one'})
    await active
    try{
      assert.equal((await post(base,{request_id:'broken',prompt:'two'})).status,502)
      const next=await post(base,{request_id:'recovery',prompt:'three'})
      assert.equal(next.status,200)
      assert.equal((await next.json()).answer,'recovered')
    }finally{release()}
    assert.equal((await held).status,200)
  })
})

test('classifies authentication and TLS failures without repairing them or exposing details', async () => {
  for (const [detail, code] of [['certificate verify failed: hidden-provider-detail', 'MODEL_TLS_ERROR'], ['HTTP 401 unauthorized hidden-provider-detail', 'MODEL_AUTH_ERROR']]) {
    await withRunner(configured, async () => { throw Error(detail) }, async base => {
      const response = await post(base, { request_id: code, prompt: 'hello' })
      assert.equal(response.status, 502)
      const result = await response.json()
      assert.equal(result.error.code, code)
      assert.equal(JSON.stringify(result).includes('hidden-provider-detail'), false)
    })
  }
})
