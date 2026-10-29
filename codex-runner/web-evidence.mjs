import http from 'node:http'
import { randomUUID } from 'node:crypto'
import { setTimeout as delay } from 'node:timers/promises'
import { validSources } from './structured.mjs'

// SDK 0.163 drops Responses URL annotations and standalone search sources.
// Observe only this turn's native provider replies. Request bodies and successful
// response bytes reach the SDK unchanged; model prose never proves a search.
export function collectWebEvidence() {
  let searched = false
  const citations = new Map()
  const acceptSource = source => {
    const citation = { url: source?.url, title: source?.title || source?.url }
    if (validSources([citation]) && citations.size < 100) citations.set(citation.url, citation)
  }
  const acceptItem = item => {
    if (item?.type === 'web_search_call' && item.status === 'completed') {
      searched = true
      for (const source of item.action?.sources || []) if (source.type === 'url') acceptSource(source)
    }
    if (item?.type !== 'message') return
    for (const content of item.content || []) for (const annotation of content.annotations || []) {
      if (annotation.type !== 'url_citation') continue
      acceptSource(annotation)
    }
  }
  return {
    get searched() { return searched },
    sources: () => [...citations.values()],
    acceptSearchResponse(data) {
      if (typeof data?.output !== 'string' || !Array.isArray(data.results)) return
      searched = true
      for (const source of data.results) if (source?.type === 'text_result') acceptSource(source)
    },
    accept(event) {
      if (event?.item) acceptItem(event.item)
      for (const item of event?.response?.output || []) acceptItem(item)
      if (event?.type === 'response.output_text.annotation.added') acceptItem({ type: 'message', content: [{ annotations: [event.annotation] }] })
    },
  }
}

export async function observeWebResponses(config, signal, { wait = delay, now = Date.now } = {}) {
  const evidence = collectWebEvidence()
  let failure = null
  const token = randomUUID()
  const server = http.createServer(async (request, response) => {
    if (request.method !== 'POST' || !['/responses', '/alpha/search'].includes(request.url)) { response.writeHead(404); response.end(); return }
    if (request.headers.authorization !== `Bearer ${token}`) { response.writeHead(401); response.end(); return }
    const aborted = new AbortController()
    const disconnect = () => { if (!response.writableEnded) aborted.abort() }
    response.on('close', disconnect)
    try {
      let size = 0, chunks = []
      for await (const chunk of request) {
        size += chunk.length
        if (size > 8 * 1024 * 1024) throw Error('Request limit')
        chunks.push(chunk)
      }
      const headers = { ...request.headers, authorization: `Bearer ${config.apiKey}` }
      for (const name of ['host', 'connection', 'content-length', 'transfer-encoding', 'accept-encoding']) delete headers[name]
      if (config.accountId) headers['chatgpt-account-id'] = config.accountId
      const raw = Buffer.concat(chunks)
      const requestSignal = AbortSignal.any([signal, aborted.signal])
      let upstream, waited = 0
      const nonRetryableLimits = new Set(['insufficient_quota', 'credit_balance_exhausted',
        'organization_spend_limit_exceeded', 'project_spend_limit_exceeded', 'organization_usage_limit_exceeded'])
      for (let attempt = 0; attempt < 4; attempt++) {
        requestSignal.throwIfAborted()
        upstream = await fetch(config.baseUrl.replace(/\/$/, '') + request.url, {
          method: 'POST', headers, body: raw, redirect: 'error', signal: requestSignal,
        })
        if (upstream.ok) { failure = null; break }
        failure = { status: upstream.status }
        let errorSize = 0, errorChunks = []
        for await (const chunk of upstream.body) {
          errorSize += chunk.length
          if (errorSize > 8192) break
          errorChunks.push(chunk)
        }
        let data = {}
        try { data = JSON.parse(Buffer.concat(errorChunks).toString('utf8')) } catch { /* Unstructured provider errors remain private. */ }
        for (const field of ['code', 'param', 'type']) if (typeof data.error?.[field] === 'string' && /^[a-zA-Z0-9_.-]{1,100}$/.test(data.error[field])) failure[field] = data.error[field]
        // Only an explicitly rejected response is safe to resend. Quota/payment
        // failures, successful streams and unknown transport outcomes are final.
        if (upstream.status !== 429 || request.url !== '/responses' || attempt === 3
          || nonRetryableLimits.has(failure.code) || nonRetryableLimits.has(failure.type)) break
        const retryAfter = upstream.headers.get('retry-after')
        // The asctime HTTP-date format has no zone text but always means UTC.
        const retryDate = /^(?:[a-z]{3} ){2}[ \d]\d \d{2}:\d{2}:\d{2} \d{4}$/i.test(retryAfter || '')
          ? retryAfter + ' GMT' : retryAfter
        const suppliedWait = retryAfter === null ? NaN : /^\d+(?:\.\d+)?$/.test(retryAfter)
          ? Number(retryAfter) * 1000 : /^[a-z]{3,9}[ ,]/i.test(retryAfter) ? Date.parse(retryDate) - now() : NaN
        const pause = Number.isFinite(suppliedWait) ? Math.max(0, suppliedWait) : 10_000 * 2 ** attempt
        // Never shorten a server cooldown to fit our total 70-second wait budget.
        if (suppliedWait > 70_000 - waited || pause > 70_000 - waited) break
        waited += pause
        failure = null
        await wait(pause, undefined, { signal: requestSignal })
      }
      if (!upstream.ok) {
        response.writeHead(upstream.status, { 'Content-Type': 'application/json' })
        response.end('{"error":{"code":"WEB_SEARCH_UNAVAILABLE","message":"Configured provider rejected native search"}}')
        return
      }
      response.writeHead(upstream.status, { 'Content-Type': upstream.headers.get('content-type') || 'text/event-stream' })
      if (request.url === '/alpha/search') {
        const parts = []
        let size = 0
        for await (const chunk of upstream.body) {
          size += chunk.length
          if (size > 1024 * 1024) throw Error('Native search response limit')
          parts.push(chunk)
        }
        const rawResponse = Buffer.concat(parts)
        try { evidence.acceptSearchResponse(JSON.parse(rawResponse.toString('utf8'))) } catch { /* The SDK handles malformed native results. */ }
        response.end(rawResponse)
        return
      }
      let pending = ''
      const decoder = new TextDecoder()
      for await (const chunk of upstream.body) {
        pending += decoder.decode(chunk, { stream: true })
        if (pending.length > 1024 * 1024) throw Error('Event limit')
        let end
        while ((end = pending.indexOf('\n')) >= 0) {
          const line = pending.slice(0, end).trimEnd(); pending = pending.slice(end + 1)
          if (line.startsWith('data: ') && line !== 'data: [DONE]') {
            try { evidence.accept(JSON.parse(line.slice(6))) } catch { /* Non-JSON SSE remains visible to the SDK, which reports failure. */ }
          }
        }
        if (response.destroyed) throw Error('Client disconnected')
        if (!response.write(chunk)) await new Promise(resolve => {
          const done = () => { response.off('drain', done); response.off('close', done); resolve() }
          response.once('drain', done); response.once('close', done)
        })
      }
      response.end()
    } catch (error) {
      if (!failure) failure = { category: /certificate|cert_verify|unknownissuer|self.signed/i.test(String(error?.message) + String(error?.cause?.message)) ? 'tls' : 'transport' }
      if (!response.headersSent) response.writeHead(502, { 'Content-Type': 'application/json' })
      response.end('{"error":{"code":"WEB_SEARCH_UNAVAILABLE","message":"Native search transport unavailable"}}')
    } finally { response.off('close', disconnect) }
  })
  server.maxConnections = 4
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => { server.off('error', reject); resolve() })
  })
  return {
    baseUrl: `http://127.0.0.1:${server.address().port}`, token,
    get searched() { return evidence.searched }, sources: evidence.sources,
    get failure() { return failure },
    close: async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)) },
  }
}
