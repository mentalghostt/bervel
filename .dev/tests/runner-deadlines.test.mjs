import { test, mock } from 'node:test'
import assert from 'node:assert/strict'
import { resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { createRunner } from '../../codex-runner/server.mjs'
import { ProviderSettings } from '../../codex-runner/settings.mjs'

const configured = {
  CODEX_HOME: resolve('.dev/local/long-task-workflow-2026-10-28/deadlines/runner-fixture'),
  BERVEL_CODEX_MODEL: 'fixture-model', BERVEL_CODEX_API_KEY: 'fixture-key',
  BERVEL_CODEX_BASE_URL: 'https://provider.fixture/v1',
}
const post = (base, body) => fetch(`${base}/v1/generate`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})
async function withRunner(execute, action) {
  const server = createRunner({ env: configured, execute })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  try { await action(`http://127.0.0.1:${server.address().port}`) }
  finally {
    server.closeIdleConnections()
    await new Promise(resolve => server.close(resolve))
  }
}

test('accepts bounded integer caller budgets and preserves the default contract', async () => {
  const calls = []
  await withRunner(async (_config, request, signal) => {
    calls.push({ request, aborted: signal.aborted })
    return 'fixture answer'
  }, async base => {
    for (const timeout_ms of [1000, 180000, undefined]) {
      const body = { request_id: 'budget', prompt: 'Question', ...(timeout_ms === undefined ? {} : { timeout_ms }) }
      const response = await post(base, body)
      assert.equal(response.status, 200)
      assert.deepEqual(await response.json(), { answer: 'fixture answer', model: 'fixture-model' })
      assert.deepEqual(calls.at(-1), { request: body, aborted: false })
    }
  })
})

test('rejects invalid caller budgets before invoking the model', async () => {
  await withRunner(async () => assert.fail('invalid request must not invoke the model'), async base => {
    for (const timeout_ms of [null, true, '1000', 999, 180001, 1234.5]) {
      const response = await post(base, { request_id: 'invalid', prompt: 'Question', timeout_ms })
      assert.equal(response.status, 422)
      assert.equal((await response.json()).error.code, 'INVALID_REQUEST')
    }
  })
})

test('caller budget aborts execution once and returns the honest unknown-outcome failure', { timeout: 4000 }, async () => {
  let executions = 0
  await withRunner(async (_config, _request, signal) => {
    executions += 1
    await delay(5000, undefined, { signal })
    return 'must not complete'
  }, async base => {
    const started = performance.now()
    const response = await post(base, { request_id: 'short-budget', prompt: 'Question', timeout_ms: 1000 })
    assert.equal(response.status, 504)
    const result = await response.json()
    assert.equal(result.error.code, 'MODEL_TIMEOUT')
    assert.match(result.error.message, /unknown/)
    assert.equal(executions, 1)
    assert.ok(performance.now() - started < 1800, 'request must respect the caller deadline')
  })
})

test('MOX trust preflight consumes the same caller budget as execution', { timeout: 4000 }, async () => {
  const realFetch = globalThis.fetch
  const config = mock.method(ProviderSettings.prototype, 'configuration', async () => ({
    provider: 'mox', baseUrl: 'https://provider.fixture/codex', model: 'fixture-model',
    home: configured.CODEX_HOME, cwd: configured.CODEX_HOME, apiKey: 'fixture-key',
  }))
  let preflights = 0, executions = 0
  const provider = mock.method(globalThis, 'fetch', async (url, options) => {
    if (options?.method !== 'HEAD') return realFetch(url, options)
    assert.equal(String(url), 'https://provider.fixture/codex')
    preflights += 1
    await delay(650, undefined, { signal: options.signal })
    return new Response(null, { status: 200 })
  })
  try {
    await withRunner(async (_config, _request, signal) => {
      executions += 1
      await delay(5000, undefined, { signal })
      return 'must not complete'
    }, async base => {
      const started = performance.now()
      const response = await post(base, { request_id: 'shared-budget', prompt: 'Question', timeout_ms: 1000 })
      assert.equal(response.status, 504)
      assert.equal((await response.json()).error.code, 'MODEL_TIMEOUT')
      assert.equal(preflights, 1)
      assert.equal(executions, 1)
      assert.ok(performance.now() - started < 1450, 'preflight must not reset the model deadline')
    })
  } finally { provider.mock.restore(); config.mock.restore() }
})

test('an exhausted MOX preflight prevents execution and releases the runner slot', { timeout: 4000 }, async () => {
  const realFetch = globalThis.fetch
  const config = mock.method(ProviderSettings.prototype, 'configuration', async () => ({
    provider: 'mox', baseUrl: 'https://provider.fixture/codex', model: 'fixture-model',
    home: configured.CODEX_HOME, cwd: configured.CODEX_HOME, apiKey: 'fixture-key',
  }))
  let preflights = 0, executions = 0
  const provider = mock.method(globalThis, 'fetch', async (url, options) => {
    if (options?.method !== 'HEAD') return realFetch(url, options)
    preflights += 1
    if (preflights === 1) await delay(5000, undefined, { signal: options.signal })
    return new Response(null, { status: 200 })
  })
  try {
    await withRunner(async () => { executions += 1; return 'recovered' }, async base => {
      const failed = await post(base, { request_id: 'expired-preflight', prompt: 'Question', timeout_ms: 1000 })
      assert.equal(failed.status, 504)
      assert.equal((await failed.json()).error.code, 'MODEL_TIMEOUT')
      assert.equal(executions, 0)
      const recovered = await post(base, { request_id: 'recovery', prompt: 'Question', timeout_ms: 1000 })
      assert.equal(recovered.status, 200)
      assert.equal(executions, 1)
      assert.equal(preflights, 2)
    })
  } finally { provider.mock.restore(); config.mock.restore() }
})
