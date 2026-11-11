import { test } from 'node:test'
import assert from 'node:assert/strict'
import { setTimeout as delay } from 'node:timers/promises'

const { createRunner } = await import(process.env.BERVEL_RUNNER_TEST_MODULE || '../../codex-runner/server.mjs')
const env = { BERVEL_CODEX_MODEL: 'controlled', BERVEL_CODEX_API_KEY: 'fixture', BERVEL_CODEX_BASE_URL: 'https://provider.example/v1' }

test('concurrent timeout, client abort and provider error finish once while peers remain held', { timeout: 6000 }, async () => {
  const active = new Set(), calls = [], finished = []
  let enterAll, releaseHeld, fail
  const entered = new Promise(resolve => { enterAll = resolve })
  const held = new Promise(resolve => { releaseHeld = resolve })
  const failure = new Promise(resolve => { fail = resolve })
  const server = createRunner({ env, execute: async (_config, request, signal) => {
    const id = request.request_id
    calls.push(id); active.add(id)
    if (calls.length === 5) enterAll()
    try {
      if (id === 'error') { await failure; throw Error('HTTP 429 private-provider-detail') }
      if (id === 'timeout' || id === 'abort') await delay(10000, undefined, { signal })
      else if (id.startsWith('hold-')) await held
      return id
    } finally { active.delete(id); finished.push(id) }
  } })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const url = `http://127.0.0.1:${server.address().port}/v1/generate`
  const cancelled = new AbortController()
  const post = (id, signal) => fetch(url, { method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ request_id: id, prompt: id, ...(id === 'timeout' ? { timeout_ms: 1000 } : {}) }) })
  const requests = ['hold-a', 'hold-b', 'timeout', 'abort', 'error'].map(id => post(id, id === 'abort' ? cancelled.signal : undefined))
  // Observe rejections immediately so cancellation never creates an unhandled promise.
  const settled = requests.map(request => request.then(response => ({ response }), error => ({ error })))
  try {
    const result = await Promise.race([entered.then(() => 'entered'), delay(1500).then(() => 'late')])
    assert.equal(result, 'entered', 'All five requests must execute before release or failure')
    cancelled.abort(); fail()
    assert.equal((await settled[3]).error?.name, 'AbortError')
    const provider = (await settled[4]).response
    assert.equal(provider.status, 429)
    assert.equal((await provider.json()).error.code, 'MODEL_RATE_LIMITED')
    const timed = (await settled[2]).response
    assert.equal(timed.status, 504)
    const timeout = await timed.json()
    assert.equal(timeout.error.code, 'MODEL_TIMEOUT')
    assert.match(timeout.error.message, /unknown/)
    for (let i = 0; i < 100 && active.has('abort'); i++) await delay(5)
    assert.deepEqual([...active].sort(), ['hold-a', 'hold-b'])
    const recovery = await post('recovery')
    assert.equal(recovery.status, 200)
  } finally {
    cancelled.abort(); fail(); releaseHeld()
    await Promise.all(settled)
    server.closeAllConnections()
    await new Promise(resolve => server.close(resolve))
  }
  assert.equal(active.size, 0)
  assert.deepEqual([...calls].sort(), ['abort', 'error', 'hold-a', 'hold-b', 'recovery', 'timeout'])
  assert.deepEqual([...finished].sort(), [...calls].sort())
  for (const index of [0, 1]) {
    assert.equal((await settled[index]).response.status, 200)
    assert.equal((await (await settled[index]).response.json()).answer, index === 0 ? 'hold-a' : 'hold-b')
  }
})
