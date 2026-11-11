import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { resolve } from 'node:path'

const module = await import('../../codex-runner/inspection-app-server.mjs').catch(error => {
  if (error.code === 'ERR_MODULE_NOT_FOUND') return {}
  throw error
})
function setup(t) {
  assert.equal(typeof module.InspectionAppServer, 'function', 'InspectionAppServer supports bidirectional native turns')
  const calls = [], children = []
  const codex = new module.InspectionAppServer({ env: { PATH: process.env.PATH }, config: { analytics: { enabled: false } },
    spawnProcess: (command, args, options) => {
      calls.push({ command, args, options })
      const child = spawn(process.execPath, [resolve('.dev/tests/fixtures/inspection-app-server.cjs')], options)
      children.push(child)
      return child
    } })
  t.after(() => { for (const child of children) child.kill() })
  return { codex, calls }
}
const options = { model: 'test-model', workingDirectory: '.', sandboxMode: 'danger-full-access', approvalPolicy: 'never' }

test('stdio adapter steers the running native turn and maps progress without creating another turn', async t => {
  const { codex, calls } = setup(t)
  const thread = codex.startThread(options)
  const { events } = await thread.runStreamed(' original\nraw ', {})
  const seen = []
  for await (const event of events) {
    seen.push(event)
    if (event.type === 'item.started') assert.equal((await thread.steer(' follow-up\nraw ', 'follow-1')).turnId, 'turn-fixture')
  }
  assert.equal(calls.length, 1)
  assert.equal(seen.filter(event => event.type === 'thread.started').length, 1)
  assert.equal(seen.find(event => event.item?.id === 'answer' && event.type === 'item.completed').item.text, ' original\nraw  +  follow-up\nraw ')
  assert.equal(seen.find(event => event.item?.id === 'cmd' && event.type === 'item.completed').item.exit_code, 0)
  assert.equal(seen.at(-1).type, 'turn.completed')
  await assert.rejects(thread.steer('late', 'follow-2'), error => error.code === 'NATIVE_STEER_REJECTED')
})

test('resume preserves native thread identity and cancellation ends the active process', async t => {
  const { codex } = setup(t)
  const controller = new AbortController()
  const thread = codex.resumeThread('existing-thread', options)
  const { events } = await thread.runStreamed('hold', { signal: controller.signal })
  const seen = []
  for await (const event of events) {
    seen.push(event)
    if (event.type === 'item.started') controller.abort()
  }
  assert.equal(seen[0].thread_id, 'existing-thread')
  assert.equal(seen.at(-1).type, 'turn.failed')
})

test('cancellation while the turn start reply is pending interrupts the active turn once', async t => {
  const { codex } = setup(t)
  const controller = new AbortController()
  const thread = codex.startThread(options)
  const { events } = await thread.runStreamed('interrupt before start reply', { signal: controller.signal })
  let last
  for await (const event of events) {
    if (event.type === 'item.started') controller.abort()
    last = event
  }
  assert.equal(last.type, 'turn.failed')
})

test('a confirmed steer rejection and a lost transport have different outcomes', async t => {
  const { codex } = setup(t)
  const thread = codex.startThread(options)
  const { events } = await thread.runStreamed('hold', {})
  await assert.rejects(async () => {
    for await (const event of events) {
      if (event.type !== 'item.started') continue
      await assert.rejects(thread.steer('reject', 'rejected'), error => error.code === 'NATIVE_STEER_REJECTED')
      await assert.rejects(thread.steer('disconnect', 'unknown'), error => error.code !== 'NATIVE_STEER_REJECTED')
    }
  }, /transport|closed/i)
})
