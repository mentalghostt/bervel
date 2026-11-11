import { test } from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { gunzipSync } from 'node:zlib'
import { InspectionAppServer } from '../../codex-runner/inspection-app-server.mjs'

test('installed CLI applies an in-flight steer in the same native turn and resumes its history', { timeout: 45000 }, async t => {
  const calls = []
  let firstSeen, releaseFirst
  const first = new Promise(r => { firstSeen = r })
  const hold = new Promise(r => { releaseFirst = r })
  t.after(() => releaseFirst())
  const provider = http.createServer(async (request, response) => {
    if (request.method === 'GET') { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end('{"data":[]}'); return }
    const chunks = []; for await (const chunk of request) chunks.push(chunk)
    const raw = Buffer.concat(chunks)
    const body = JSON.parse(request.headers['content-encoding'] === 'gzip' ? gunzipSync(raw) : raw)
    calls.push(body)
    if (calls.length === 1) { firstSeen(); await hold }
    const updated = JSON.stringify(body.input).includes('FOLLOWUP_123')
    const answer = updated ? 'ACK FOLLOWUP_123' : 'INITIAL'
    const item = { id: 'msg_' + calls.length, type: 'message', role: 'assistant', status: 'completed',
      content: [{ type: 'output_text', text: answer, annotations: [] }] }
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    const event = data => response.write(`event: ${data.type}\ndata: ${JSON.stringify(data)}\n\n`)
    event({ type: 'response.created', response: { id: 'resp_' + calls.length, model: 'test-model', object: 'response', status: 'in_progress', output: [] } })
    event({ type: 'response.output_item.added', output_index: 0, item: { ...item, status: 'in_progress' } })
    event({ type: 'response.output_text.delta', item_id: item.id, output_index: 0, content_index: 0, delta: answer })
    event({ type: 'response.output_item.done', output_index: 0, item })
    event({ type: 'response.completed', response: { id: 'resp_' + calls.length, model: 'test-model', object: 'response', status: 'completed', output: [item], usage: { input_tokens: 10, output_tokens: 10, total_tokens: 20 } } })
    response.end()
  })
  await new Promise(r => provider.listen(0, '127.0.0.1', r))
  t.after(() => new Promise(r => provider.close(r)))
  const home = resolve('.dev/local/inspection-steering-2026-11-06/protocol-' + process.platform)
  await mkdir(home, { recursive: true })
  const env = { PATH: process.env.PATH, HOME: home, CODEX_HOME: home, TEST_PROVIDER_KEY: 'fixture' }
  for (const key of ['SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT']) if (process.env[key]) env[key] = process.env[key]
  const codex = new InspectionAppServer({ env, config: {
    model_provider: 'fixture', model_providers: { fixture: { name: 'Fixture', base_url: `http://127.0.0.1:${provider.address().port}/v1`,
      env_key: 'TEST_PROVIDER_KEY', wire_api: 'responses', requires_openai_auth: false, supports_websockets: false } },
    analytics: { enabled: false }, feedback: { enabled: false },
  } })
  const options = { model: 'test-model', workingDirectory: home, approvalPolicy: 'never', sandboxMode: 'danger-full-access' }
  const thread = codex.startThread(options)
  const { events } = await thread.runStreamed('ORIGINAL_123', { signal: AbortSignal.timeout(30000) })
  const seen = []
  const consume = (async () => { for await (const event of events) seen.push(event) })()
  consume.catch(() => {})
  await Promise.race([first, consume.then(() => { throw new Error('CLI ended before requesting fixture provider') })])
  const accepted = await thread.steer('FOLLOWUP_123', '11111111-1111-4111-8111-111111111111')
  assert.ok(accepted.turnId)
  releaseFirst()
  await consume
  assert.equal(seen.filter(event => event.type === 'turn.completed').length, 1)
  assert.equal(seen.filter(event => event.type === 'item.completed' && event.item.type === 'agent_message').at(-1).item.text, 'ACK FOLLOWUP_123')
  assert.equal(calls.length, 2)
  const resumed = codex.resumeThread(thread.id, options)
  const next = await resumed.runStreamed('Remember my follow-up', { signal: AbortSignal.timeout(10000) })
  for await (const event of next.events) if (event.type === 'thread.started') assert.equal(event.thread_id, thread.id)
  assert.ok(JSON.stringify(calls.at(-1).input).includes('FOLLOWUP_123'))
})
