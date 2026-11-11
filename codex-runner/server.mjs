import http from 'node:http'
import { pathToFileURL } from 'node:url'
import { ProviderSettings, SettingsError } from './settings.mjs'
import { startMoxBridge } from './mox-bridge.mjs'
import { decodeImages, withImageInputs } from './images.mjs'
import { VoiceSettings, VoiceError, decodeAudio, transcribe } from './voice.mjs'
import { checkSchema, matchesSchema, validSources } from './structured.mjs'
import { observeWebResponses, quotaFailure } from './web-evidence.mjs'
import { NativeInspectionJobs, NativeJobError } from './native-inspection.mjs'

const MAX_BODY = 8 * 1024 * 1024

class RunnerError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code }
}

export async function executeCodex(config, request, signal, { CodexClass } = {}) {
  const Codex = CodexClass || (await import('@openai/codex-sdk')).Codex
  const web = request.web_search === true
  const observer = await observeWebResponses(config, signal)
  try {
  const childEnvironment = {
    PATH: process.env.PATH || '', HOME: process.env.HOME || '/home/node',
    CODEX_HOME: config.home, BERVEL_CODEX_API_KEY: observer.token,
    ...(process.env.CODEX_CA_CERTIFICATE ? { CODEX_CA_CERTIFICATE: process.env.CODEX_CA_CERTIFICATE } : {}),
  }
  const codex = new Codex({
    env: childEnvironment,
    config: {
      model_provider: 'bervel',
      model_providers: { bervel: {
        name: 'Configured BERVEL model provider', base_url: observer.baseUrl,
        env_key: 'BERVEL_CODEX_API_KEY', wire_api: 'responses', requires_openai_auth: false,
        supports_websockets: false, supports_standalone_web_search: web,
        request_max_retries: 0, stream_max_retries: 0,
        ...(config.accountId ? { http_headers: { 'ChatGPT-Account-Id': config.accountId } } : {}),
      } },
      features: { shell_tool: false, apps: false, remote_plugin: false, skill_mcp_dependency_install: false, shell_snapshot: false },
      web_search: web ? 'live' : 'disabled', project_doc_max_bytes: 0,
      ...(web ? { tools: { web_search: true } } : {}),
      analytics: { enabled: false }, feedback: { enabled: false }, history: { persistence: 'none' },
      shell_environment_policy: { inherit: 'none' },
    },
  })
  const thread = codex.startThread({ model: config.model, workingDirectory: config.cwd,
    skipGitRepoCheck: true, sandboxMode: 'read-only', approvalPolicy: 'never',
    networkAccessEnabled: false, webSearchMode: web ? 'live' : 'disabled' })
  const result = await withImageInputs(request, input => thread.run(input, { signal, ...(request.output_schema ? { outputSchema: request.output_schema } : {}) }))
  if (typeof result.finalResponse !== 'string' || !result.finalResponse.trim() || result.finalResponse.length > 32000) {
    throw new RunnerError(502, 'MODEL_INVALID_OUTPUT', 'Model returned an invalid answer')
  }
  if (web) {
    const sources = observer.sources()
    if (!observer.searched) {
      if (observer.failure) throw Error('Native search failed')
      throw new RunnerError(502, 'WEB_SEARCH_UNAVAILABLE', 'Configured provider did not perform native web search')
    }
    if (!validSources(sources)) throw new RunnerError(502, 'WEB_SEARCH_FAILED', 'Native web search returned no verified sources')
    return { answer: result.finalResponse, sources }
  }
  return result.finalResponse
  } catch (error) {
    signal.throwIfAborted()
    if ([402, 403, 429].includes(observer.failure?.status) && quotaFailure(observer.failure)) {
      throw new RunnerError(429, 'MODEL_QUOTA_EXCEEDED', 'Model provider quota or billing limit is exhausted; owner action is required')
    }
    if (observer.failure?.status === 429) {
      if (observer.failure.retry_after_unrepresentable) throw new RunnerError(502, 'MODEL_FAILED', 'Provider retry delay cannot be safely scheduled; automatic retry is disabled')
      const failure = new RunnerError(429, 'MODEL_RATE_LIMITED', 'Model provider rate limit reached; retry later')
      if (Number.isFinite(observer.failure.retry_after_seconds)) failure.retry_after_seconds = observer.failure.retry_after_seconds
      throw failure
    }
    if ([401, 403].includes(observer.failure?.status)) throw new RunnerError(502, 'MODEL_AUTH_ERROR', 'Model provider rejected authentication')
    if (observer.failure?.category === 'tls') throw new RunnerError(502, 'MODEL_TLS_ERROR', 'Model connection failed certificate validation')
    if (observer.failure?.category === 'transport') throw new RunnerError(502, 'MODEL_UNAVAILABLE', 'Model transport failed; the request outcome is unknown and must not be automatically repeated')
    if (web && !(error instanceof RunnerError) && !signal.aborted) {
      const classified = safeFailure(error)
      if (['MODEL_AUTH_ERROR', 'MODEL_TLS_ERROR', 'MODEL_TIMEOUT'].includes(classified.code)) throw classified
      if (!observer?.failure || observer.failure.category === 'transport') throw classified
      if (observer?.failure?.status && ![400, 404, 422].includes(observer.failure.status)) throw new RunnerError(502, 'MODEL_FAILED', 'Model provider is unavailable')
      const failure = new RunnerError(502, 'WEB_SEARCH_UNAVAILABLE', 'The configured provider could not complete native web search')
      failure.providerFailure = observer?.failure
      failure.cause = error
      throw failure
    }
    throw error
  } finally { await observer?.close() }
}

function send(response, status, body) {
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' })
  response.end(JSON.stringify(body))
}

async function bodyOf(request, limit = MAX_BODY) {
  let size = 0
  const parts = []
  for await (const part of request) {
    size += part.length
    if (size > limit) throw new RunnerError(413, 'REQUEST_TOO_LARGE', 'Request exceeds the size limit')
    parts.push(part)
  }
  try { return JSON.parse(Buffer.concat(parts).toString('utf8')) }
  catch { throw new RunnerError(422, 'INVALID_REQUEST', 'A JSON request is required') }
}

function safeFailure(error) {
  if (error instanceof NativeJobError) return error
  if (error instanceof RunnerError) return error
  if (error instanceof VoiceError) return error
  if (error?.code === 'WEB_SEARCH_UNAVAILABLE') return new RunnerError(502, 'WEB_SEARCH_UNAVAILABLE', 'Configured provider could not complete native web search')
  if (error instanceof SettingsError) return new RunnerError(error.code === 'INVALID_SETTINGS' ? 422 : 503, error.code, error.message)
  // Classify errors without returning provider output, request text, or credentials.
  const detail = String(error?.message || '') + ' ' + String(error?.cause?.message || '') + ' ' + String(error?.cause?.code || '')
  if (error?.name === 'TimeoutError' || error?.name === 'AbortError') return new RunnerError(504, 'MODEL_TIMEOUT', 'Model request timed out; the outcome is unknown')
  if (/certificate|cert_verify|unknownissuer|unknown issuer|self.signed|tls handshake/i.test(detail)) return new RunnerError(502, 'MODEL_TLS_ERROR', 'Model connection failed certificate validation')
  if (/\b401\b|\b403\b|unauthorized|authentication|sign.in|log.in/i.test(detail)) return new RunnerError(502, 'MODEL_AUTH_ERROR', 'Model provider rejected authentication')
  if (/exceeded retry limit,\s*last status:\s*429\b|\bHTTP(?: status)?\s*429\b/i.test(detail)) return new RunnerError(429, 'MODEL_RATE_LIMITED', 'Model provider rate limit reached; retry later')
  return new RunnerError(502, 'MODEL_FAILED', 'Codex could not complete the request')
}

export function createRunner({ env = process.env, execute = executeCodex, transcribeAudio = transcribe, inspectionJobs } = {}) {
  let voiceActive = 0
  const settings = new ProviderSettings(env)
  const voice = new VoiceSettings(env)
  const nativeJobs = inspectionJobs || (env.BERVEL_INSPECTION_TOKEN ? new NativeInspectionJobs({ settings, env }) : null)
  const server = http.createServer(async (request, response) => {
    const cancelled = new AbortController()
    response.on('close', () => { if (!response.writableEnded) cancelled.abort() })
    try {
      if (request.method === 'GET' && request.url === '/health/live') return send(response, 200, { status: 'ok' })
      if (request.url.startsWith('/v1/inspection/')) {
        if (!env.BERVEL_INSPECTION_TOKEN || !nativeJobs) throw new NativeJobError(503, 'NATIVE_UNCONFIGURED', 'Native inspection transport is not configured')
        if (request.headers.authorization !== 'Bearer ' + env.BERVEL_INSPECTION_TOKEN) throw new NativeJobError(401, 'UNAUTHENTICATED', 'Native inspection transport token is required')
        if (request.method === 'POST' && request.url === '/v1/inspection/jobs') return send(response, 202, await nativeJobs.submit(await bodyOf(request)))
        const followup = request.url.match(/^\/v1\/inspection\/jobs\/([A-Za-z0-9_-]{1,128})\/messages$/)
        if (followup && request.method === 'POST') return send(response, 202, await nativeJobs.steer(followup[1], await bodyOf(request)))
        const match = request.url.match(/^\/v1\/inspection\/jobs\/([A-Za-z0-9_-]{1,128})(\/cancel)?$/)
        if (match && request.method === 'GET' && !match[2]) return send(response, 200, await nativeJobs.read(match[1]))
        if (match && request.method === 'POST' && match[2]) {
          const body = await bodyOf(request)
          if (!body || typeof body !== 'object' || Array.isArray(body) || Object.keys(body).length) throw new NativeJobError(422, 'INVALID_REQUEST', 'Cancellation body must be empty')
          return send(response, 200, await nativeJobs.cancel(match[1]))
        }
        return send(response, 404, { error: { code: 'NOT_FOUND', message: 'Route not found' } })
      }
      if (request.method === 'GET' && request.url === '/health/ready') {
        try { await settings.configuration(); return send(response, 200, { status: 'ok', configured: true }) }
        catch { return send(response, 503, { status: 'unconfigured', configured: false }) }
      }
      if (request.url === '/v1/settings' && request.method === 'GET') return send(response, 200, await settings.read())
      if (request.url === '/v1/settings' && request.method === 'PUT') return send(response, 200, await settings.save(await bodyOf(request)))
      if (request.url === '/v1/voice/settings' && request.method === 'GET') return send(response, 200, await voice.read())
      if (request.url === '/v1/voice/settings' && request.method === 'PUT') return send(response, 200, await voice.save(await bodyOf(request, 16 * 1024)))
      if (request.url === '/v1/transcribe' && request.method === 'POST') {
        const audio = await bodyOf(request)
        decodeAudio(audio)
        const key = await voice.key()
        if (voiceActive >= 2) throw new VoiceError(429, 'VOICE_BUSY', 'Лимит одновременных распознаваний достигнут.')
        voiceActive += 1
        try {
          const result = await transcribeAudio(key, audio, AbortSignal.any([cancelled.signal, AbortSignal.timeout(60_000)]))
          return send(response, 200, result)
        } finally { voiceActive -= 1 }
      }
      const testing = request.method === 'POST' && request.url === '/v1/settings/test'
      if (!testing && (request.method !== 'POST' || request.url !== '/v1/generate')) return send(response, 404, { error: { code: 'NOT_FOUND', message: 'Route not found' } })
      const supplied = await bodyOf(request)
      if (testing && (!supplied || Object.keys(supplied).length)) throw new RunnerError(422, 'INVALID_REQUEST', 'The test uses saved settings')
      const body = testing ? { request_id: 'connection-test', prompt: 'Ответь одним словом: OK' } : supplied
      if (!body || typeof body !== 'object' || Array.isArray(body) || Object.keys(body).some(key => !['request_id', 'prompt', 'images', 'output_schema', 'web_search', 'timeout_ms'].includes(key))
        || typeof body.request_id !== 'string' || !body.request_id.trim() || body.request_id.length > 128
        || typeof body.prompt !== 'string' || !body.prompt.trim()) throw new RunnerError(422, 'INVALID_REQUEST', 'Nonempty request_id and prompt are required')
      if (body.web_search !== undefined && typeof body.web_search !== 'boolean') throw new RunnerError(422, 'INVALID_REQUEST', 'web_search must be boolean')
      if (body.timeout_ms !== undefined && (!Number.isInteger(body.timeout_ms) || body.timeout_ms < 1000 || body.timeout_ms > 180_000)) {
        throw new RunnerError(422, 'INVALID_REQUEST', 'timeout_ms must be an integer between 1000 and 180000')
      }
      if (body.output_schema !== undefined) {
        try {
          if (JSON.stringify(body.output_schema).length > 64 * 1024) throw Error('Schema limit')
          checkSchema(body.output_schema)
        } catch { throw new RunnerError(422, 'INVALID_REQUEST', 'output_schema must use the supported JSON Schema vocabulary') }
      }
      if (Buffer.byteLength(body.prompt) > 256 * 1024) throw new RunnerError(413, 'REQUEST_TOO_LARGE', 'Text context exceeds limit')
      try { decodeImages(body.images) } catch { throw new RunnerError(422, 'INVALID_REQUEST', 'Invalid image attachments') }
      let config
      try { config = await settings.configuration() }
      catch { throw new RunnerError(503, 'MODEL_UNCONFIGURED', 'Configure the model, provider URL and external credentials') }
      const signal = AbortSignal.any([cancelled.signal, AbortSignal.timeout(body.timeout_ms ?? 180_000)])
      if (config.provider === 'mox') {
        // No credentials are sent until HTTPS trust has been verified.
        await fetch(config.baseUrl, { method: 'HEAD', redirect: 'error', signal: AbortSignal.any([signal, AbortSignal.timeout(10_000)]) })
      }
      signal.throwIfAborted()
      const result = await execute(config, body, signal)
      const answer = typeof result === 'string' ? result : result?.answer
      if (typeof answer !== 'string' || !answer.trim() || answer.length > 32000) throw new RunnerError(502, 'MODEL_INVALID_OUTPUT', 'Model returned an invalid answer')
      if (body.output_schema) {
        try { if (!matchesSchema(JSON.parse(answer), body.output_schema)) throw Error('Schema mismatch') }
        catch { throw new RunnerError(502, 'MODEL_INVALID_OUTPUT', 'Model output does not match the requested schema') }
      }
      if (body.web_search && !validSources(result?.sources)) throw new RunnerError(502, 'WEB_SEARCH_FAILED', 'Native web search returned no verified sources')
      send(response, 200, { answer, model: typeof result?.model === 'string' ? result.model : config.model,
        ...(body.web_search ? { sources: result.sources.map(({ title, url }) => ({ title, url })) } : {}) })
    } catch (error) {
      const failure = safeFailure(error)
      if (!response.headersSent) send(response, failure.status, { error: { code: failure.code, message: failure.message,
        ...(failure.code === 'MODEL_RATE_LIMITED' && Number.isFinite(failure.retry_after_seconds)
          && failure.retry_after_seconds >= 0 ? { retry_after_seconds: failure.retry_after_seconds } : {}) } })
    }
  })
  server.nativeJobs = nativeJobs
  return server
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const port = Number(process.env.BERVEL_CODEX_PORT || '8090')
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid runner port')
  const server = createRunner()
  const bridge = await startMoxBridge(process.env)
  server.requestTimeout = 190_000
  server.listen(port, '0.0.0.0', () => console.log(`BERVEL Codex runner listening on ${port}`))
  for (const event of ['SIGINT', 'SIGTERM']) process.on(event, () => server.close(async () => { await server.nativeJobs?.close(); await bridge.close(); process.exit(0) }))
}
