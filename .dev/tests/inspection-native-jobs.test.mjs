import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readFile, writeFile, readdir, rm } from 'node:fs/promises'
import { resolve, join, sep } from 'node:path'

const native = await import('../../codex-runner/native-inspection.mjs').catch(error => {
  if (error.code === 'ERR_MODULE_NOT_FOUND') return {}
  throw error
})
const fixtureRoot = resolve('.dev/local/inspection-engineer-2026-10-30/native-cli')
const key = 'actor-private-credential-123456'
const request = (id, session = 'session-one', question = '  Точный вопрос\nбез JSON и обёртки  ') =>
  ({ request_id: id, session_id: session, actor_id: 'actor-one', data_scope: 'scope-one', question, api_key: key })
const done = () => ({ type: 'turn.completed', usage: { input_tokens: 1, output_tokens: 1 } })
function deferred() { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const waitAbort = signal => new Promise(resolve => signal.aborted ? resolve() : signal.addEventListener('abort', resolve, { once: true }))

async function setup(t, stream) {
  assert.equal(typeof native.NativeInspectionJobs, 'function', 'NativeInspectionJobs must implement native CLI execution')
  await mkdir(fixtureRoot, { recursive: true })
  const root = await mkdtemp(join(fixtureRoot, 'jobs-'))
  assert.ok(root.startsWith(fixtureRoot + sep))
  t.after(() => rm(root, { recursive: true, force: true }))
  const calls = { constructors: [], starts: [], resumes: [], runs: [], closes: 0 }
  class FakeCodex {
    constructor(options) { this.options = options; calls.constructors.push(options) }
    thread(id, options) {
      return { id, runStreamed: async (input, turnOptions) => {
        calls.runs.push({ id, input, options: turnOptions, threadOptions: options, codexOptions: this.options })
        return { events: stream ? stream({ id, input, signal: turnOptions.signal, options: this.options }) : (async function* () {
          yield { type: 'thread.started', thread_id: id }
          yield { type: 'item.completed', item: { id: 'answer', type: 'agent_message', text: 'Готово' } }
          yield done()
        })() }
      } }
    }
    startThread(options) { calls.starts.push(options); return this.thread('native-' + calls.starts.length, options) }
    resumeThread(id, options) { calls.resumes.push({ id, options }); return this.thread(id, options) }
  }
  const settings = { home: root, configuration: async () => ({ home: root, cwd: '/legacy', model: 'selected-model',
    provider: 'api_key', baseUrl: 'https://provider.example/v1', apiKey: 'provider-private-credential-123' }) }
  const env = { CODEX_HOME: root, BERVEL_INSPECTION_WORKSPACE: '/workspace/project', BERVEL_API_URL: 'http://api:8011',
    BERVEL_TEST_DATABASE_URL: 'postgresql://dedicated-test-only', PYTHONPATH: '/workspace/project/src',
    BERVEL_HOST_PROJECT_ROOT: 'C:/OSPanel/home/bervel.local', COMPOSE_CONVERT_WINDOWS_PATHS: '1' }
  const options = { settings, env, CodexClass: FakeCodex, observe: async () => ({ baseUrl: 'http://observer/v1',
    token: 'observer-private-credential-123', close: async () => { calls.closes++ } }) }
  const manager = new native.NativeInspectionJobs(options)
  t.after(() => manager.close())
  return { manager, root, calls, options }
}

test('native SDK receives exact question and full CLI options without a planner, schema or history suppression', async t => {
  const { manager, calls, root } = await setup(t)
  const input = request('raw-turn')
  const queued = await manager.submit(input)
  assert.equal(queued.state, 'queued')
  await manager.idle()
  const result = await manager.read(input.request_id)
  assert.equal(result.state, 'completed')
  assert.equal(result.thread_id, 'native-1')
  assert.equal(result.answer, 'Готово')
  assert.equal(result.actor_id, input.actor_id)
  assert.equal(result.data_scope, input.data_scope)
  assert.equal(calls.runs[0].input, input.question)
  assert.deepEqual(Object.keys(calls.runs[0].options), ['signal'])
  assert.deepEqual(calls.starts[0], { model: 'selected-model', workingDirectory: '/workspace/project', skipGitRepoCheck: true,
    sandboxMode: 'danger-full-access', approvalPolicy: 'never', networkAccessEnabled: true, webSearchMode: 'live' })
  const { config, env } = calls.constructors[0]
  assert.notEqual(config.features?.shell_tool, false)
  assert.notEqual(config.history?.persistence, 'none')
  assert.equal(config.model_providers.bervel.request_max_retries, undefined)
  assert.equal(config.model_providers.bervel.stream_max_retries, undefined)
  assert.ok(config.developer_instructions.includes('VPN'))
  assert.equal(env.CODEX_HOME, join(root, 'native-inspection'))
  assert.equal(env.BERVEL_API_KEY, key)
  assert.equal(env.BERVEL_INSPECTION_TURN_ID, 'raw-turn')
  assert.equal(env.BERVEL_EXPORT_RECORD, join(root, 'inspection-exports', 'raw-turn.jsonl'))
  assert.equal(env.BERVEL_TEST_DATABASE_URL, 'postgresql://dedicated-test-only')
  assert.equal(env.PYTHONPATH, '/workspace/project/src')
  assert.equal(env.BERVEL_HOST_PROJECT_ROOT, 'C:/OSPanel/home/bervel.local')
  assert.equal(env.COMPOSE_CONVERT_WINDOWS_PATHS, '1')
})

test('one scoped session starts once and resumes the same durable native thread after manager restart', async t => {
  const { manager, calls, options } = await setup(t)
  await manager.submit(request('one')); await manager.idle()
  await manager.submit(request('two')); await manager.idle()
  const reloaded = new native.NativeInspectionJobs(options)
  t.after(() => reloaded.close())
  await reloaded.submit(request('three')); await reloaded.idle()
  assert.equal(calls.starts.length, 1)
  assert.deepEqual(calls.resumes.map(call => call.id), ['native-1', 'native-1'])
  assert.equal((await reloaded.read('three')).thread_id, 'native-1')
  await reloaded.submit({ ...request('other-actor'), actor_id: 'another-actor' }); await reloaded.idle()
  assert.equal(calls.starts.length, 2)
})

test('different sessions run concurrently and messages within the same session stay serial', async t => {
  const entered = [], first = deferred(), secondSession = deferred(), release = deferred()
  const { manager, calls } = await setup(t, async function* ({ id, input, signal }) {
    entered.push(input)
    if (input === 'first') first.resolve()
    if (input === 'other') secondSession.resolve()
    yield { type: 'thread.started', thread_id: id }
    if (input !== 'follow-up') await Promise.race([release.promise, waitAbort(signal)])
    yield { type: 'item.completed', item: { id: input, type: 'agent_message', text: input } }; yield done()
  })
  await manager.submit(request('first', 's-one', 'first')); await first.promise
  await manager.submit(request('follow', 's-one', 'follow-up'))
  await manager.submit(request('other', 's-two', 'other')); await secondSession.promise
  try { assert.deepEqual(entered, ['first', 'other']) } finally { release.resolve() }
  await manager.idle()
  assert.deepEqual(entered, ['first', 'other', 'follow-up'])
  assert.equal(calls.resumes.length, 1)
})

test('request replay is exact and conflicting request identity never executes a second turn', async t => {
  const { manager, calls } = await setup(t)
  const input = request('replay')
  await manager.submit(input); await manager.idle()
  assert.equal((await manager.submit({ ...input, api_key: 'rotated-actor-credential' })).state, 'completed')
  await assert.rejects(manager.submit({ ...input, question: 'different' }), error => error.code === 'IDEMPOTENCY_CONFLICT')
  await assert.rejects(manager.submit({ ...input, actor_id: 'different' }), error => error.code === 'IDEMPOTENCY_CONFLICT')
  assert.equal(calls.runs.length, 1)
})

test('missing native job has a distinct absence code for read and cancel', async t => {
  const { manager } = await setup(t)
  for (const operation of ['read', 'cancel']) {
    await assert.rejects(manager[operation]('never-created'), error =>
      error.status === 404 && error.code === 'NATIVE_JOB_NOT_FOUND')
  }
})

test('events merge native items, publish early thread identity and read confirmed exports without an answer cap', async t => {
  const hold = deferred(), started = deferred(), answer = 'long native answer '.repeat(3000)
  const card = { kind: 'file', artifact_id: '01111111-2222-3333-4444-555555555555', filename: 'report.txt' }
  const { manager } = await setup(t, async function* ({ id, options, signal }) {
    yield { type: 'thread.started', thread_id: id }
    yield { type: 'item.started', item: { id: 'command', type: 'command_execution', command: 'pwd', aggregated_output: '', status: 'in_progress' } }
    started.resolve(); await Promise.race([hold.promise, waitAbort(signal)])
    yield { type: 'item.completed', item: { id: 'command', type: 'command_execution', command: 'pwd', aggregated_output: 'native full result', status: 'completed', exit_code: 0 } }
    yield { type: 'item.completed', item: { id: 'change', type: 'file_change', changes: [{ path: 'real.py', kind: 'update' }], status: 'completed' } }
    await writeFile(options.env.BERVEL_EXPORT_RECORD, JSON.stringify(card) + '\n')
    yield { type: 'item.completed', item: { id: 'answer', type: 'agent_message', text: answer } }; yield done()
  })
  await manager.submit(request('events')); await started.promise
  try {
    const running = await manager.read('events')
    assert.equal(running.thread_id, 'native-1')
    assert.equal(running.items[0].status, 'in_progress')
  } finally { hold.resolve() }
  await manager.idle()
  const result = await manager.read('events')
  assert.equal(result.answer, answer)
  assert.deepEqual(result.items.map(item => item.id), ['command', 'change', 'answer'])
  assert.equal(result.items[0].aggregated_output, 'native full result')
  assert.deepEqual(result.exports, [card])
})

test('cancel aborts native execution, preserves finished items and never retries the cancelled turn', async t => {
  const started = deferred()
  const { manager, calls } = await setup(t, async function* ({ id, signal }) {
    yield { type: 'thread.started', thread_id: id }
    yield { type: 'item.completed', item: { id: 'saved', type: 'file_change', status: 'completed', changes: [{ path: 'saved.txt', kind: 'add' }] } }
    started.resolve(); await waitAbort(signal); signal.throwIfAborted()
  })
  const input = request('cancel')
  await manager.submit(input); await started.promise
  assert.equal((await manager.cancel('cancel')).cancel_requested, true)
  await manager.idle()
  const result = await manager.read('cancel')
  assert.equal(result.state, 'cancelled')
  assert.equal(result.thread_id, 'native-1')
  assert.equal(result.items[0].id, 'saved')
  await manager.submit(input); await manager.idle()
  assert.equal(calls.runs.length, 1)
})

test('queued cancellation never starts that message', async t => {
  const started = deferred()
  const { manager, calls } = await setup(t, async function* ({ id, signal }) {
    yield { type: 'thread.started', thread_id: id }; started.resolve(); await waitAbort(signal); signal.throwIfAborted()
  })
  await manager.submit(request('hold')); await started.promise
  await manager.submit(request('never-start'))
  assert.equal((await manager.cancel('never-start')).state, 'cancelled')
  await manager.cancel('hold'); await manager.idle()
  assert.equal(calls.runs.length, 1)
})

test('an interrupted durable running/queued marker becomes unknown after restart and is never replayed', async t => {
  const { manager, root, options, calls } = await setup(t)
  const input = request('interrupted')
  await manager.submit(input); await manager.idle()
  const files = await readdir(join(root, 'inspection-jobs', 'jobs'))
  const path = join(root, 'inspection-jobs', 'jobs', files[0])
  const value = JSON.parse(await readFile(path, 'utf8'))
  value.state = 'running'; value.answer = null
  await writeFile(path, JSON.stringify(value))
  const reloaded = new native.NativeInspectionJobs(options)
  t.after(() => reloaded.close())
  assert.equal((await reloaded.read(input.request_id)).state, 'unknown')
  assert.equal((await reloaded.submit(input)).state, 'unknown')
  await reloaded.idle()
  assert.equal(calls.runs.length, 1)
})

test('job files never persist the raw question, app/provider credentials or raw exception strings', async t => {
  const { manager, root } = await setup(t, async function* ({ id }) {
    yield { type: 'thread.started', thread_id: id }
    throw Error(key + ' provider-private-credential-123 upstream debug')
  })
  const input = request('safe-files', 'safe-session', 'DO_NOT_DUPLICATE_USER_QUESTION')
  await manager.submit(input); await manager.idle()
  const result = await manager.read(input.request_id)
  assert.equal(result.state, 'unknown')
  const files = await readdir(join(root, 'inspection-jobs', 'jobs'))
  const stored = (await Promise.all(files.map(file => readFile(join(root, 'inspection-jobs', 'jobs', file), 'utf8')))).join('')
  for (const secret of [key, 'provider-private-credential-123', input.question, 'upstream debug']) assert.equal(stored.includes(secret), false)
})

test('private native routes require their transport token and ordinary generate stays unchanged', async t => {
  const { manager, root } = await setup(t)
  const { createRunner } = await import('../../codex-runner/server.mjs')
  const env = { CODEX_HOME: root, BERVEL_CODEX_MODEL: 'legacy-model', BERVEL_CODEX_API_KEY: 'legacy-credential',
    BERVEL_INSPECTION_TOKEN: 'private-inspection-token' }
  const server = createRunner({ env, inspectionJobs: manager, execute: async (_config, supplied) => supplied.prompt })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise(resolve => server.close(resolve)))
  const url = 'http://127.0.0.1:' + server.address().port
  const post = (path, body, token = '') => fetch(url + path, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token }, body: JSON.stringify(body) })
  assert.equal((await post('/v1/inspection/jobs', request('http'))).status, 401)
  const created = await post('/v1/inspection/jobs', request('http'), env.BERVEL_INSPECTION_TOKEN)
  assert.equal(created.status, 202)
  await manager.idle()
  assert.equal((await fetch(url + '/v1/inspection/jobs/http', { headers: { Authorization: 'Bearer ' + env.BERVEL_INSPECTION_TOKEN } })).status, 200)
  const legacy = await post('/v1/generate', { request_id: 'legacy', prompt: 'ordinary' })
  assert.deepEqual(await legacy.json(), { answer: 'ordinary', model: 'legacy-model' })
})

test('native routes without a configured private token report unavailable instead of falling back to generate', async t => {
  const { createRunner } = await import('../../codex-runner/server.mjs')
  const server = createRunner({ env: {}, execute: async () => assert.fail('native must not use generate') })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise(resolve => server.close(resolve)))
  const response = await fetch('http://127.0.0.1:' + server.address().port + '/v1/inspection/jobs', { method: 'POST', body: JSON.stringify(request('missing')) })
  assert.equal(response.status, 503)
  assert.equal((await response.json()).error.code, 'NATIVE_UNCONFIGURED')
})

test('unavailable native state reports a feature error while legacy generation continues', async t => {
  const { root } = await setup(t)
  const home = join(root, 'broken-native-home')
  await mkdir(home)
  await writeFile(join(home, 'inspection-jobs'), 'This is not a state directory')
  const { createRunner } = await import('../../codex-runner/server.mjs')
  const env = { CODEX_HOME: home, BERVEL_CODEX_MODEL: 'legacy-model', BERVEL_CODEX_API_KEY: 'legacy-credential',
    BERVEL_INSPECTION_TOKEN: 'private-inspection-token' }
  const server = createRunner({ env, execute: async () => 'Legacy still works' })
  // Consume the expected initialization rejection in the test too; the server must
  // handle it independently instead of leaking an unhandled filesystem rejection.
  server.nativeJobs.ready.catch(() => {})
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise(resolve => server.close(resolve)))
  const url = 'http://127.0.0.1:' + server.address().port
  const response = await fetch(url + '/v1/inspection/jobs', { method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + env.BERVEL_INSPECTION_TOKEN }, body: JSON.stringify(request('unavailable')) })
  assert.equal(response.status, 503)
  assert.equal((await response.json()).error.code, 'NATIVE_STATE_UNAVAILABLE')
  const legacy = await fetch(url + '/v1/generate', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ request_id: 'legacy', prompt: 'ordinary' }) })
  assert.equal((await legacy.json()).answer, 'Legacy still works')
})
