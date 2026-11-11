import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, rm, readFile, writeFile, readdir } from 'node:fs/promises'
import { resolve, join } from 'node:path'
import { NativeInspectionJobs } from '../../codex-runner/native-inspection.mjs'

const request = { request_id: 'parent', session_id: 'session', actor_id: 'actor', data_scope: 'scope', question: 'original', api_key: 'private-test-key' }
const input = (message_id, question = 'follow-up') => ({ message_id, question, session_id: 'session', actor_id: 'actor', data_scope: 'scope' })
function deferred() { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
async function setup(t, steering = async () => ({}), configure) {
  const base = resolve('.dev/local/inspection-steering-2026-11-06/jobs-tests')
  await mkdir(base, { recursive: true })
  const root = await mkdtemp(join(base, 'case-'))
  const started = deferred(), release = deferred(), calls = []
  class FakeCodex {
    startThread() { return this.resumeThread() }
    resumeThread() {
      return { steer: async (text, id) => { calls.push([id, text]); return steering(text, id) },
        runStreamed: async (_input, { signal }) => ({ events: (async function* () {
          yield { type: 'thread.started', thread_id: 'native-thread' }
          started.resolve()
          await Promise.race([release.promise, new Promise(r => signal.addEventListener('abort', r, { once: true }))])
          yield { type: 'item.completed', item: { id: 'answer', type: 'agent_message', text: 'done' } }
          yield { type: 'turn.completed' }
        })() }) }
    }
  }
  const options = { env: { CODEX_HOME: root }, CodexClass: FakeCodex,
    settings: { configuration: async () => { await configure?.(); return { model: 'fixture', provider: 'api_key', apiKey: 'provider-fixture' } } },
    observe: async () => ({ baseUrl: 'http://fixture', token: 'fixture', close: async () => {} }) }
  const manager = new NativeInspectionJobs(options)
  t.after(async () => { release.resolve(); await manager.close(); await rm(root, { recursive: true, force: true }) })
  return { manager, started, release, calls, root, options }
}
async function receipt(manager, id, state) {
  const deadline = Date.now() + 2000
  while (Date.now() < deadline) {
    const result = (await manager.read('parent')).followups?.find(value => value.message_id === id)
    if (result?.state === state) return result
    await new Promise(r => setTimeout(r, 5))
  }
  assert.fail(`No ${state} receipt for ${id}`)
}

test('multiple follow-ups are delivered once in order and replay never starts another steer', async t => {
  const { manager, started, calls } = await setup(t)
  await manager.submit(request); await started.promise
  assert.equal(typeof manager.steer, 'function')
  await manager.steer('parent', input('one', '  first\nraw '))
  await manager.steer('parent', input('two', 'second'))
  await receipt(manager, 'two', 'accepted')
  await manager.steer('parent', input('one', '  first\nraw '))
  assert.deepEqual(calls, [['one', '  first\nraw '], ['two', 'second']])
  await assert.rejects(manager.steer('parent', input('one', 'different')), error => error.code === 'IDEMPOTENCY_CONFLICT')
  await assert.rejects(manager.steer('parent', { ...input('bad'), actor_id: 'another' }), error => error.status === 403)
})

test('messages submitted during native initialization are delivered after the active thread is available', async t => {
  const configReady = deferred()
  t.after(() => configReady.resolve())
  const { manager, started, calls } = await setup(t, undefined, () => configReady.promise)
  await manager.submit(request)
  assert.equal(typeof manager.steer, 'function')
  await manager.steer('parent', input('early'))
  assert.equal((await manager.read('parent')).followups[0].state, 'queued')
  configReady.resolve(); await started.promise
  await receipt(manager, 'early', 'accepted')
  assert.equal(calls.length, 1)
})

test('confirmed rejection is safe to queue later; unknown delivery is never automatically repeated', async t => {
  const { manager, started, calls } = await setup(t, async text => {
    throw Object.assign(new Error('private transport detail'), text === 'reject' ? { code: 'NATIVE_STEER_REJECTED' } : {})
  })
  await manager.submit(request); await started.promise
  assert.equal(typeof manager.steer, 'function')
  await manager.steer('parent', input('rejected', 'reject')); await receipt(manager, 'rejected', 'rejected')
  await manager.steer('parent', input('unknown', 'lost')); await receipt(manager, 'unknown', 'unknown')
  await manager.steer('parent', input('unknown', 'lost'))
  assert.equal(calls.length, 2)
})

test('late and cancelled follow-ups receive a definite rejection without entering CLI', async t => {
  const { manager, started, release, calls } = await setup(t)
  await manager.submit(request); await started.promise
  assert.equal(typeof manager.steer, 'function')
  await manager.cancel('parent')
  await manager.steer('parent', input('cancelled'))
  await receipt(manager, 'cancelled', 'rejected')
  release.resolve(); await manager.idle()
  await manager.steer('parent', input('late'))
  await receipt(manager, 'late', 'rejected')
  assert.equal(calls.length, 0)
})

test('restart retains accepted receipts and converts interrupted sending to unknown without replay or raw input storage', async t => {
  const { manager, started, release, root, options, calls } = await setup(t)
  await manager.submit(request); await started.promise
  assert.equal(typeof manager.steer, 'function')
  await manager.steer('parent', input('saved', 'DO_NOT_STORE_THIS_RAW_INPUT')); await receipt(manager, 'saved', 'accepted')
  release.resolve(); await manager.idle()
  const path = join(root, 'inspection-jobs/jobs', (await readdir(join(root, 'inspection-jobs/jobs')))[0])
  const value = JSON.parse(await readFile(path, 'utf8'))
  assert.equal(JSON.stringify(value).includes('DO_NOT_STORE_THIS_RAW_INPUT'), false)
  value.followups[0].state = 'sending'
  await writeFile(path, JSON.stringify(value))
  const reloaded = new NativeInspectionJobs(options)
  t.after(() => reloaded.close())
  assert.equal((await reloaded.read('parent')).followups[0].state, 'unknown')
  await reloaded.steer('parent', input('saved', 'DO_NOT_STORE_THIS_RAW_INPUT'))
  assert.equal(calls.length, 1)
})

test('completion during durable follow-up save rejects the unsent message so it can become the next turn', async t => {
  const { manager, started, release, calls } = await setup(t)
  await manager.submit(request); await started.promise
  const saved = deferred(), proceed = deferred()
  t.after(() => proceed.resolve())
  const save = manager.save.bind(manager)
  let held = false
  manager.save = async job => {
    await save(job)
    if (!held && job.followups?.some(item => item.message_id === 'boundary' && item.state === 'queued')) {
      held = true; saved.resolve(); await proceed.promise
    }
  }
  const sending = manager.steer('parent', input('boundary'))
  await saved.promise
  release.resolve()
  const deadline = Date.now() + 2000
  while ((await manager.read('parent')).state !== 'completed' && Date.now() < deadline) await new Promise(r => setTimeout(r, 5))
  proceed.resolve(); await sending
  await receipt(manager, 'boundary', 'rejected')
  assert.equal(calls.length, 0)
})
