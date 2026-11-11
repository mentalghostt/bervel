import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { createInterface } from 'node:readline'

const require = createRequire(import.meta.url)
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  promise.catch(() => {})
  return { promise, resolve, reject }
}
const transportError = () => new Error('Native app-server transport closed or unavailable')
const rejected = () => Object.assign(new Error('Native turn cannot accept this message'), { code: 'NATIVE_STEER_REJECTED' })

function configArguments(config, prefix = '') {
  return Object.entries(config).flatMap(([key, value]) => {
    const name = prefix ? `${prefix}.${key}` : key
    if (value && typeof value === 'object' && !Array.isArray(value)) return configArguments(value, name)
    return ['-c', `${name}=${JSON.stringify(value)}`]
  })
}

function nativeItem(item) {
  const status = item.status === 'inProgress' ? 'in_progress' : item.status
  const base = { id: item.id, status }
  switch (item.type) {
    case 'agentMessage': return { ...base, type: 'agent_message', text: item.text, phase: item.phase }
    case 'reasoning': return { ...base, type: 'reasoning', text: [...(item.summary || []), ...(item.content || [])].join('\n') }
    case 'commandExecution': return { ...base, type: 'command_execution', command: item.command,
      aggregated_output: item.aggregatedOutput || '', exit_code: item.exitCode ?? null }
    case 'fileChange': return { ...base, type: 'file_change', changes: item.changes.map(change => ({
      path: change.path, kind: typeof change.kind === 'object' ? change.kind.type : change.kind, diff: change.diff })) }
    case 'webSearch': return { ...base, type: 'web_search', query: item.query, status: status || 'completed' }
    case 'mcpToolCall': return { ...base, type: 'mcp_tool_call', server: item.server, tool: item.tool, result: item.result }
    default: return null
  }
}

/** Only the native inspection uses app-server. Ordinary model generation keeps the SDK. */
export class InspectionAppServer {
  constructor({ env, config = {}, spawnProcess = spawn }) {
    this.env = env; this.config = config; this.spawnProcess = spawnProcess
  }
  startThread(options) { return new InspectionThread(this, null, options) }
  resumeThread(id, options) { return new InspectionThread(this, id, options) }
}

class InspectionThread {
  constructor(owner, id, options) {
    this.owner = owner; this.id = id; this.options = options
    this.pending = new Map(); this.sequence = 0; this.events = []; this.items = new Map()
    this.ready = deferred(); this.wake = null; this.ended = false; this.failure = null; this.terminal = false
  }

  push(event) { this.events.push(event); this.wake?.(); this.wake = null }
  end(error) {
    if (this.ended) return
    this.ended = true; this.failure = error
    this.ready.reject(error || rejected())
    for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error || transportError()) }
    this.pending.clear()
    this.wake?.(); this.wake = null
  }
  send(message) {
    if (!this.child || this.child.stdin.destroyed) throw transportError()
    this.child.stdin.write(JSON.stringify(message) + '\n')
  }
  rpc(method, params, timeout = 30_000) {
    if (this.ended) return Promise.reject(transportError())
    const id = ++this.sequence
    const result = deferred()
    const timer = setTimeout(() => { this.pending.delete(id); result.reject(transportError()) }, timeout)
    this.pending.set(id, { ...result, timer })
    try { this.send({ id, method, params }) }
    catch (error) { this.pending.delete(id); clearTimeout(timer); result.reject(error) }
    return result.promise
  }

  receive(message) {
    if (message.id !== undefined && !message.method) {
      const pending = this.pending.get(message.id)
      if (!pending) return
      this.pending.delete(message.id); clearTimeout(pending.timer)
      if (message.error) pending.reject(Object.assign(new Error('Native RPC rejected'), { rpcRejected: true }))
      else pending.resolve(message.result)
      return
    }
    if (message.id !== undefined && message.method) {
      // No implicit approvals or model tool implementations in the transport.
      this.send({ id: message.id, error: { code: -32601, message: 'Client interaction is unavailable' } })
      return
    }
    const { method, params = {} } = message
    if (params.threadId !== this.id) return
    if (method === 'turn/started') this.turnId = params.turn.id
    if (params.turnId && this.turnId && params.turnId !== this.turnId) return
    if (['item/started', 'item/completed'].includes(method)) {
      const item = nativeItem(params.item)
      if (item) { this.items.set(item.id, item); this.push({ type: method.replace('/', '.'), item }) }
    } else if (method === 'item/agentMessage/delta') {
      const previous = this.items.get(params.itemId) || { id: params.itemId, type: 'agent_message', text: '' }
      const item = { ...previous, text: previous.text + params.delta }
      this.items.set(item.id, item); this.push({ type: 'item.updated', item })
    } else if (method === 'item/commandExecution/outputDelta') {
      const previous = this.items.get(params.itemId)
      if (previous) {
        const item = { ...previous, aggregated_output: previous.aggregated_output + params.delta }
        this.items.set(item.id, item); this.push({ type: 'item.updated', item })
      }
    } else if (method === 'turn/completed' && params.turn.id === this.turnId) {
      this.terminal = true
      this.push({ type: params.turn.status === 'completed' ? 'turn.completed' : 'turn.failed' })
      this.end()
    }
  }

  async start(input, signal) {
    signal?.throwIfAborted()
    const launcher = join(dirname(require.resolve('@openai/codex/package.json')), 'bin', 'codex.js')
    this.child = this.owner.spawnProcess(process.execPath,
      [launcher, ...configArguments(this.owner.config), 'app-server', '--listen', 'stdio://'],
      { env: this.owner.env, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true, detached: process.platform !== 'win32' })
    this.child.on('error', () => this.end(transportError()))
    this.child.on('close', () => { if (!this.terminal) this.end(transportError()) })
    this.child.stdin.on('error', () => this.end(transportError()))
    this.child.stderr.resume() // Diagnostics may contain provider details; never expose them.
    this.lines = createInterface({ input: this.child.stdout })
    this.lines.on('line', line => {
      try { this.receive(JSON.parse(line)) }
      catch { this.end(transportError()) }
    })
    this.onAbort = () => {
      this.abortTimer ??= setTimeout(() => { this.end(transportError()); this.kill() }, 5000)
      if (!this.turnId || this.interruptRequested) return
      this.interruptRequested = true
      this.rpc('turn/interrupt', { threadId: this.id, turnId: this.turnId }).catch(() => {
        this.end(transportError()); this.kill()
      })
    }
    signal?.addEventListener('abort', this.onAbort, { once: true })
    await this.rpc('initialize', { clientInfo: { name: 'bervel_inspection', title: 'BERVEL Проверка', version: '0.1.0' } })
    this.send({ method: 'initialized' })
    signal?.throwIfAborted()
    const params = { model: this.options.model, cwd: this.options.workingDirectory,
      approvalPolicy: this.options.approvalPolicy, sandbox: this.options.sandboxMode }
    const started = await this.rpc(this.id ? 'thread/resume' : 'thread/start', this.id ? { ...params, threadId: this.id } : params)
    this.id = started.thread.id
    this.push({ type: 'thread.started', thread_id: this.id })
    signal?.throwIfAborted()
    const result = await this.rpc('turn/start', { threadId: this.id, input: [{ type: 'text', text: input }] })
    this.turnId = result.turn.id
    this.ready.resolve()
    if (signal?.aborted) this.onAbort()
  }

  async steer(question, messageId) {
    if (this.terminal) throw rejected()
    await this.ready.promise
    if (this.terminal) throw rejected()
    try {
      const result = await this.rpc('turn/steer', { threadId: this.id, expectedTurnId: this.turnId,
        input: [{ type: 'text', text: question }], clientUserMessageId: messageId }, 15_000)
      if (result.turnId !== this.turnId) throw transportError()
      return result
    } catch (error) {
      if (error.rpcRejected) throw rejected()
      throw error
    }
  }

  kill() {
    if (!this.child || this.child.exitCode !== null || !this.child.pid) return
    try {
      if (process.platform !== 'win32') process.kill(-this.child.pid, 'SIGKILL')
      else this.child.kill()
    } catch (error) { if (error.code !== 'ESRCH') throw error }
  }

  async runStreamed(input, { signal } = {}) {
    this.start(input, signal).catch(error => this.end(error))
    const owner = this
    return { events: (async function* () {
      try {
        while (true) {
          while (owner.events.length) yield owner.events.shift()
          if (owner.ended) { if (owner.failure) throw owner.failure; break }
          await new Promise(resolve => { owner.wake = resolve })
        }
      } finally {
        signal?.removeEventListener('abort', owner.onAbort)
        clearTimeout(owner.abortTimer)
        owner.lines?.close()
        owner.child?.stdin.end()
        // EOF asks app-server to shut down; kill only this process group if it stalls.
        if (owner.child && owner.child.exitCode === null) {
          const timer = setTimeout(() => owner.kill(), 1500)
          await new Promise(resolve => { owner.child.once('close', resolve) })
          clearTimeout(timer)
        }
      }
    })() }
  }
}
