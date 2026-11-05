import { test } from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { createRunner, executeCodex } from '../../codex-runner/server.mjs'

const env = { BERVEL_CODEX_MODEL: 'fixture', BERVEL_CODEX_API_KEY: 'private-fixture', BERVEL_CODEX_BASE_URL: 'https://fixture.invalid/v1' }

async function runFailure({ code, type, header = '120', status = 429, disconnect = false }, action) {
  let calls = 0
  const provider = http.createServer(async (request, response) => {
    for await (const chunk of request) {}
    calls++
    if (disconnect) { request.socket.destroy(); return }
    response.writeHead(status, { 'Content-Type': 'application/json', 'Retry-After': header })
    response.end(JSON.stringify({ error: { code, type, message: 'private-fixture private-prompt' } }))
  })
  await new Promise(resolve => provider.listen(0, '127.0.0.1', resolve))
  class Codex {
    constructor(options) { this.options = options }
    startThread() {
      return { run: async () => {
        const response = await fetch(this.options.config.model_providers.bervel.base_url + '/responses', {
          method: 'POST', headers: { authorization: `Bearer ${this.options.env.BERVEL_CODEX_API_KEY}` }, body: '{}',
        })
        await response.text()
        if (!response.ok) throw Error(`exceeded retry limit, last status: ${response.status} private-fixture`)
        return { finalResponse: 'successful answer' }
      } }
    }
  }
  const runner = createRunner({ env, execute: (config, request, signal) => executeCodex({ ...config,
    baseUrl: `http://127.0.0.1:${provider.address().port}` }, request, signal, { CodexClass: Codex }) })
  await new Promise(resolve => runner.listen(0, '127.0.0.1', resolve))
  try {
    const response = await fetch(`http://127.0.0.1:${runner.address().port}/v1/generate`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: 'radar-fixture', prompt: 'private-prompt' }),
    })
    const body = await response.json()
    assert.equal(JSON.stringify(body).includes('private-'), false)
    await action({ response, body, calls })
  } finally {
    runner.closeAllConnections(); provider.closeAllConnections()
    await Promise.all([new Promise(resolve => runner.close(resolve)), new Promise(resolve => provider.close(resolve))])
  }
}

for (const code of ['insufficient_quota', 'credit_balance_exhausted', 'organization_spend_limit_exceeded',
  'project_spend_limit_exceeded', 'organization_usage_limit_exceeded', 'billing_hard_limit_reached', 'billing_not_active']) {
  test(`explicit exhausted quota ${code} is terminal and has no retry instruction`, async () => {
    await runFailure({ code, header: '0' }, ({ response, body, calls }) => {
      assert.equal(response.status, 429)
      assert.equal(body.error.code, 'MODEL_QUOTA_EXCEEDED')
      assert.equal(body.error.retry_after_seconds, undefined)
      assert.equal(calls, 1)
    })
  })
}

test('quota type without a code is terminal before a retry', async () => {
  await runFailure({ type: 'insufficient_quota', header: '0' }, ({ body, calls }) => {
    assert.equal(body.error.code, 'MODEL_QUOTA_EXCEEDED'); assert.equal(calls, 1)
  })
})

test('a long temporary cooldown reaches runner JSON without early retry or shortening', async () => {
  await runFailure({ code: 'rate_limit_exceeded', header: '120.2' }, ({ response, body, calls }) => {
    assert.equal(response.status, 429); assert.equal(body.error.code, 'MODEL_RATE_LIMITED')
    assert.equal(body.error.retry_after_seconds, 121); assert.equal(calls, 1)
  })
})

test('a Retry-After HTTP-date is retained as a safe remaining delay', async () => {
  await runFailure({ code: 'rate_limit_exceeded', header: new Date(Date.now() + 120000).toUTCString() }, ({ body, calls }) => {
    assert.equal(body.error.code, 'MODEL_RATE_LIMITED')
    assert.ok(body.error.retry_after_seconds >= 118 && body.error.retry_after_seconds <= 120)
    assert.equal(calls, 1)
  })
})

test('an unrepresentably long cooldown cannot become a short automatic retry', async () => {
  await runFailure({ code: 'rate_limit_exceeded', header: '9'.repeat(400) }, ({ body, calls }) => {
    assert.equal(body.error.code, 'MODEL_FAILED'); assert.equal(body.error.retry_after_seconds, undefined)
    assert.equal(calls, 1)
  })
})

test('a provider failure after a successful HTTP reply does not create retry metadata', async () => {
  await runFailure({ status: 200 }, ({ response, body, calls }) => {
    assert.equal(response.status, 200); assert.equal(body.answer, 'successful answer'); assert.equal(calls, 1)
    assert.equal(body.error, undefined)
  })
})

test('a disconnect after accepting the POST is an unknown outcome, never a rejected request', async () => {
  await runFailure({ disconnect: true }, ({ response, body, calls }) => {
    assert.equal(response.status, 502); assert.equal(body.error.code, 'MODEL_UNAVAILABLE')
    assert.equal(body.error.retry_after_seconds, undefined); assert.equal(calls, 1)
  })
})
