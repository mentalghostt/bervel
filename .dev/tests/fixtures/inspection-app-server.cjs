const readline = require('node:readline')
const emit = value => process.stdout.write(JSON.stringify(value) + '\n')
let threadId = 'thread-fixture', turnId = null, question = '', timer, deferredStart
const reply = (id, result) => emit({ id, result })
const notify = (method, params) => emit({ method, params: { threadId, turnId, ...params } })
readline.createInterface({ input: process.stdin }).on('line', line => {
  const { id, method, params } = JSON.parse(line)
  if (method === 'initialize') return reply(id, { userAgent: 'fixture' })
  if (method === 'initialized') return
  if (method === 'thread/start' || method === 'thread/resume') {
    threadId = params.threadId || threadId
    return reply(id, { thread: { id: threadId } })
  }
  if (method === 'turn/start') {
    question = params.input[0].text
    turnId = 'turn-fixture'
    if (question === 'interrupt before start reply') deferredStart = id
    else reply(id, { turn: { id: turnId, status: 'inProgress' } })
    notify('turn/started', { turn: { id: turnId, status: 'inProgress' } })
    notify('item/started', { item: { id: 'cmd', type: 'commandExecution', command: 'read-only fixture', status: 'inProgress', cwd: '.', commandActions: [] } })
    return
  }
  if (method === 'turn/steer') {
    if (params.expectedTurnId !== turnId || !turnId) return emit({ id, error: { code: -32600, message: 'No active turn' } })
    if (params.input[0].text === 'reject') return emit({ id, error: { code: -32600, message: 'No active turn' } })
    if (params.input[0].text === 'disconnect') return process.exit(2)
    reply(id, { turnId })
    notify('item/completed', { item: { id: params.clientUserMessageId, type: 'userMessage', content: params.input } })
    clearTimeout(timer)
    timer = setTimeout(() => {
      notify('item/completed', { item: { id: 'cmd', type: 'commandExecution', command: 'read-only fixture', status: 'completed', aggregatedOutput: 'fixture result', exitCode: 0, cwd: '.', commandActions: [] } })
      notify('item/agentMessage/delta', { itemId: 'answer', delta: question + ' + ' + params.input[0].text })
      notify('item/completed', { item: { id: 'answer', type: 'agentMessage', text: question + ' + ' + params.input[0].text, phase: 'final_answer' } })
      notify('turn/completed', { turn: { id: turnId, status: 'completed', error: null } })
      turnId = null
    }, 30)
    return
  }
  if (method === 'turn/interrupt') {
    clearTimeout(timer)
    if (question === 'interrupt before start reply') {
      if (deferredStart === undefined) return emit({ id, error: { code: -32600, message: 'Duplicate interrupt' } })
      reply(deferredStart, { turn: { id: turnId, status: 'inProgress' } })
      deferredStart = undefined
      reply(id, {})
      timer = setTimeout(() => {
        notify('turn/completed', { turn: { id: turnId, status: 'interrupted', error: null } })
        turnId = null
      }, 40)
      return
    }
    reply(id, {})
    notify('turn/completed', { turn: { id: turnId, status: 'interrupted', error: null } })
    turnId = null
  }
})
