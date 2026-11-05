import { mkdir, readFile, writeFile, rename, readdir } from 'node:fs/promises'
import { join } from 'node:path'
import { createHash, randomUUID } from 'node:crypto'
import { observeWebResponses } from './web-evidence.mjs'

export class NativeJobError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code }
}

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'unknown'])
const FIELDS = ['request_id', 'session_id', 'actor_id', 'data_scope', 'state', 'thread_id', 'answer', 'model',
  'items', 'exports', 'progress', 'error_code', 'created_at', 'updated_at', 'cancel_requested']
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex')
const identity = job => [job.actor_id, job.data_scope, job.session_id]
const fingerprint = request => hash([identity(request), request.question])
const hostInstructions = [
  'Ты Codex внутри BERVEL. Отвечай по-русски и самостоятельно выполняй текущую задачу в рабочем проекте.',
  'Не меняй VPN. Модули Open Server Panel включает и переключает только пользователь.',
  'Не выполняй Git-записи в основной ветке; не переноси проект. Рабочие материалы сохраняй внутри корня проекта.',
  'Не печатай и не экспортируй секреты. Для вложения пользователю используй bervel-export <path> и возвращённые им ссылки.',
  'Схемы в ответе можно оформлять блоком mermaid. Для интерактивных графиков создавай автономный HTML без CDN и сетевых запросов и экспортируй его через bervel-export; он отображается внутри чата. HTML-блоки ответа тоже поддерживают изолированный просмотр.',
].join(' ')

function validate(request) {
  const allowed = ['request_id', 'session_id', 'actor_id', 'data_scope', 'question', 'api_key']
  if (!request || typeof request !== 'object' || Array.isArray(request) || Object.keys(request).some(key => !allowed.includes(key))
    || allowed.some(key => typeof request[key] !== 'string' || !request[key].trim())
    || !/^[A-Za-z0-9_-]{1,128}$/.test(request.request_id)
    || ['session_id', 'actor_id', 'data_scope'].some(key => request[key].length > 128 || /[\x00-\x1f\x7f]/.test(request[key]))
    || request.api_key.length > 8192 || /[\x00-\x1f\x7f]/.test(request.api_key)) {
    throw new NativeJobError(422, 'INVALID_REQUEST', 'Native job requires request, session, actor, scope, question and API credential')
  }
}

function cleanKnown(value, secrets) {
  if (typeof value === 'string') {
    for (const secret of secrets) if (secret) value = value.split(secret).join('[СКРЫТО]')
    return value
  }
  if (Array.isArray(value)) return value.map(item => cleanKnown(item, secrets))
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, cleanKnown(item, secrets)]))
  return value
}

async function optional(path) {
  try { return await readFile(path, 'utf8') }
  catch (error) { if (error.code === 'ENOENT') return null; throw error }
}

async function atomic(path, value) {
  const temporary = path + '.' + randomUUID() + '.tmp'
  await writeFile(temporary, JSON.stringify(value), { mode: 0o600, flag: 'wx', flush: true })
  await rename(temporary, path)
}

/** Transport and durable native CLI jobs only. Planning/context/tools belong to Codex. */
export class NativeInspectionJobs {
  constructor({ settings, env = process.env, CodexClass, observe = observeWebResponses }) {
    this.settings = settings; this.env = env; this.CodexClass = CodexClass; this.observe = observe
    this.home = env.CODEX_HOME || settings.home || '/data/codex'
    this.directory = join(this.home, 'inspection-jobs')
    this.exportsDirectory = join(this.home, 'inspection-exports')
    this.jobs = new Map(); this.tails = new Map(); this.controllers = new Map(); this.secrets = new Map()
    this.submissions = Promise.resolve(); this.writes = Promise.resolve(); this.stopping = false
    this.ready = this.initialize().catch(() => {
      throw new NativeJobError(503, 'NATIVE_STATE_UNAVAILABLE', 'Native inspection state is unavailable; existing jobs are not replayed')
    })
    // Initialization is eager, but its failure belongs to the native endpoint,
    // never an unhandled rejection that can break ordinary generation routes.
    this.ready.catch(() => {})
  }

  async initialize() {
    for (const path of [join(this.directory, 'jobs'), join(this.directory, 'sessions'), this.exportsDirectory,
      join(this.home, 'native-inspection')]) await mkdir(path, { recursive: true, mode: 0o700 })
    for (const name of await readdir(join(this.directory, 'jobs'))) {
      if (!name.endsWith('.json')) continue
      const job = JSON.parse(await readFile(join(this.directory, 'jobs', name), 'utf8'))
      if (['queued', 'running'].includes(job.state)) {
        job.state = 'unknown'; job.error_code = 'NATIVE_OUTCOME_UNKNOWN'; job.updated_at = new Date().toISOString()
        await atomic(join(this.directory, 'jobs', name), job)
      }
      this.jobs.set(job.request_id, job)
    }
  }

  document(job) { return structuredClone(Object.fromEntries(FIELDS.filter(key => job[key] !== undefined).map(key => [key, job[key]]))) }
  jobPath(id) { return join(this.directory, 'jobs', hash(id) + '.json') }
  sessionPath(job) { return join(this.directory, 'sessions', hash(identity(job)) + '.json') }
  exportPath(id) { return join(this.exportsDirectory, id + '.jsonl') }

  save(job) {
    job.updated_at = new Date().toISOString()
    const value = { ...this.document(job), fingerprint: job.fingerprint }
    const write = this.writes.then(() => atomic(this.jobPath(job.request_id), value))
    this.writes = write.catch(() => {})
    return write
  }

  async submit(request) {
    validate(request)
    await this.ready
    const create = this.submissions.then(async () => {
      if (this.stopping) throw new NativeJobError(503, 'NATIVE_UNAVAILABLE', 'Native runner is stopping')
      const existing = this.jobs.get(request.request_id)
      if (existing) {
        if (existing.fingerprint !== fingerprint(request)) throw new NativeJobError(409, 'IDEMPOTENCY_CONFLICT', 'Request ID already belongs to different input')
        return this.read(request.request_id)
      }
      const now = new Date().toISOString()
      const job = { request_id: request.request_id, session_id: request.session_id, actor_id: request.actor_id,
        data_scope: request.data_scope, fingerprint: fingerprint(request), state: 'queued', thread_id: null,
        answer: null, model: null, items: [], exports: [], progress: '', cancel_requested: false, created_at: now, updated_at: now }
      this.jobs.set(job.request_id, job)
      await this.save(job)
      const response = this.document(job)
      const session = hash(identity(job))
      const previous = this.tails.get(session) || Promise.resolve()
      const next = previous.catch(() => {}).then(() => this.run(job, request))
      this.tails.set(session, next)
      // Keep the rejection handled; callers see durable state or an explicit storage failure.
      next.catch(() => {})
      return response
    })
    this.submissions = create.catch(() => {})
    return create
  }

  async exports(job) {
    const source = await optional(this.exportPath(job.request_id))
    if (source === null) return
    const cards = new Map()
    const lines = source.split('\n')
    for (let index = 0; index < lines.length; index++) {
      if (!lines[index].trim()) continue
      let card
      try { card = JSON.parse(lines[index]) }
      catch {
        if (index === lines.length - 1 && !source.endsWith('\n')) break
        throw new NativeJobError(503, 'NATIVE_EXPORT_RECORD_INVALID', 'Export record is invalid; saved artifacts require verification')
      }
      if (card && typeof card === 'object' && typeof card.artifact_id === 'string') cards.set(card.artifact_id, card)
      else throw new NativeJobError(503, 'NATIVE_EXPORT_RECORD_INVALID', 'Export record is invalid; saved artifacts require verification')
    }
    job.exports = cleanKnown([...cards.values()], this.secrets.get(job.request_id) || [])
  }

  async read(id) {
    await this.ready
    const job = this.jobs.get(id)
    if (!job) throw new NativeJobError(404, 'NATIVE_JOB_NOT_FOUND', 'Native job not found')
    await this.exports(job)
    return this.document(job)
  }

  async cancel(id) {
    await this.ready
    const job = this.jobs.get(id)
    if (!job) throw new NativeJobError(404, 'NATIVE_JOB_NOT_FOUND', 'Native job not found')
    if (!TERMINAL.has(job.state)) {
      job.cancel_requested = true
      if (job.state === 'queued') { job.state = 'cancelled'; job.error_code = 'NATIVE_CANCELLED' }
      this.controllers.get(id)?.abort()
      await this.save(job)
    }
    return this.read(id)
  }

  async run(job, request) {
    if (job.state !== 'queued') return
    const controller = new AbortController()
    this.controllers.set(job.request_id, controller)
    const secrets = [request.api_key, this.env.BERVEL_INSPECTION_TOKEN]
    this.secrets.set(job.request_id, secrets)
    let observer, entered = false, completed = false
    try {
      job.state = 'running'; await this.save(job)
      const config = await this.settings.configuration()
      secrets.push(config.apiKey)
      job.model = config.model
      const signal = controller.signal
      if (config.provider === 'mox') {
        // Existing trust check: no credentials before validating MOX HTTPS.
        await fetch(config.baseUrl, { method: 'HEAD', redirect: 'error', signal: AbortSignal.any([signal, AbortSignal.timeout(10_000)]) })
      }
      signal.throwIfAborted()
      observer = await this.observe(config, signal)
      secrets.push(observer.token)
      const childEnvironment = {
        PATH: this.env.PATH || process.env.PATH || '', HOME: this.env.HOME || '/home/node',
        CODEX_HOME: join(this.home, 'native-inspection'), BERVEL_CODEX_API_KEY: observer.token,
        BERVEL_API_URL: this.env.BERVEL_API_URL || 'http://api:8011', BERVEL_API_KEY: request.api_key,
        BERVEL_INSPECTION_TURN_ID: job.request_id, BERVEL_EXPORT_RECORD: this.exportPath(job.request_id),
      }
      for (const name of ['CODEX_CA_CERTIFICATE', 'NODE_EXTRA_CA_CERTS', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'DOCKER_HOST',
        'BERVEL_TEST_DATABASE_URL', 'COMPOSE_PROJECT_NAME', 'PYTHONPATH', 'BERVEL_HOST_PROJECT_ROOT',
        'COMPOSE_CONVERT_WINDOWS_PATHS']) if (this.env[name]) childEnvironment[name] = this.env[name]
      const Codex = this.CodexClass || (await import('@openai/codex-sdk')).Codex
      const codex = new Codex({ env: childEnvironment, config: {
        model_provider: 'bervel', model_providers: { bervel: {
          name: 'Configured BERVEL model provider', base_url: observer.baseUrl, env_key: 'BERVEL_CODEX_API_KEY', wire_api: 'responses',
          requires_openai_auth: false, supports_websockets: false, supports_standalone_web_search: true,
          ...(config.accountId ? { http_headers: { 'ChatGPT-Account-Id': config.accountId } } : {}),
        } },
        features: { shell_tool: true }, web_search: 'live', tools: { web_search: true },
        developer_instructions: hostInstructions, shell_environment_policy: { inherit: 'all' },
        analytics: { enabled: false }, feedback: { enabled: false },
      } })
      const options = { model: config.model, workingDirectory: this.env.BERVEL_INSPECTION_WORKSPACE || '/workspace/project',
        skipGitRepoCheck: true, sandboxMode: 'danger-full-access', approvalPolicy: 'never', networkAccessEnabled: true, webSearchMode: 'live' }
      const mapped = await optional(this.sessionPath(job))
      const previous = mapped ? JSON.parse(mapped).thread_id : null
      const thread = previous ? codex.resumeThread(previous, options) : codex.startThread(options)
      if (previous) { job.thread_id = previous; await this.save(job) }
      signal.throwIfAborted()
      entered = true
      // This exact string is the sole user input. Native Codex owns tools and context.
      const { events } = await thread.runStreamed(request.question, { signal })
      for await (const raw of events) {
        // Output-only credential masking never changes CLI/model tool input or context.
        const event = cleanKnown(raw, secrets)
        if (event.type === 'thread.started') {
          job.thread_id = event.thread_id
          await atomic(this.sessionPath(job), { actor_id: job.actor_id, data_scope: job.data_scope,
            session_id: job.session_id, thread_id: job.thread_id })
        } else if (['item.started', 'item.updated', 'item.completed'].includes(event.type)) {
          const index = job.items.findIndex(item => item.id === event.item.id)
          if (index < 0) job.items.push(event.item)
          else job.items[index] = event.item
          const item = event.item
          if (['agent_message', 'reasoning'].includes(item.type)) job.progress = item.text
          else if (item.type === 'command_execution') job.progress = item.command
          else if (item.type === 'file_change') job.progress = item.changes.map(change => change.path).join('\n')
          if (event.type === 'item.completed' && item.type === 'agent_message') job.answer = item.text
        } else if (event.type === 'turn.completed') {
          completed = true
        } else if (['turn.failed', 'error'].includes(event.type)) {
          job.error_code = 'NATIVE_TURN_FAILED'
          job.state = 'failed'
        }
        await this.exports(job)
        await this.save(job)
      }
      if (this.stopping) { job.state = 'unknown'; job.error_code = 'NATIVE_OUTCOME_UNKNOWN' }
      else if (job.cancel_requested) { job.state = 'cancelled'; job.error_code = 'NATIVE_CANCELLED_OUTCOME_UNKNOWN' }
      else if (job.state !== 'failed') {
        job.state = completed ? 'completed' : 'unknown'
        if (!completed) job.error_code = 'NATIVE_OUTCOME_UNKNOWN'
      }
    } catch (error) {
      if (job.cancel_requested) { job.state = 'cancelled'; job.error_code = 'NATIVE_CANCELLED_OUTCOME_UNKNOWN' }
      else {
        job.state = entered || this.stopping ? 'unknown' : 'failed'
        job.error_code = entered || this.stopping ? 'NATIVE_OUTCOME_UNKNOWN' :
          error?.code === 'MODEL_UNCONFIGURED' ? 'MODEL_UNCONFIGURED' : 'NATIVE_START_FAILED'
      }
    } finally {
      try { await this.exports(job) }
      catch { job.error_code = 'NATIVE_EXPORT_RECORD_INVALID' }
      await this.save(job)
      this.controllers.delete(job.request_id)
      // Close the transport only; native rollouts and thread mapping remain on disk.
      await observer?.close()
      this.secrets.delete(job.request_id)
    }
  }

  async idle() { await this.ready; await this.submissions; await Promise.all([...this.tails.values()]); await this.writes }

  async close() {
    this.stopping = true
    try { await this.ready }
    catch (error) { if (error.code === 'NATIVE_STATE_UNAVAILABLE') return; throw error }
    for (const job of this.jobs.values()) if (!TERMINAL.has(job.state)) {
      job.state = 'unknown'; job.error_code = 'NATIVE_OUTCOME_UNKNOWN'
      this.controllers.get(job.request_id)?.abort()
      await this.save(job)
    }
    await this.idle()
  }
}
