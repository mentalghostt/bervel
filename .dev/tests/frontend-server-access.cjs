const assert = require('node:assert/strict')
const { before, after, test } = require('node:test')
const { createServer } = require('node:http')
const { spawn, execFileSync } = require('node:child_process')
const { resolve } = require('node:path')

// An isolated upstream records headers; no project credentials or database writes.
const token = 'proxy-test-only-' + 'a'.repeat(40)
const mode = process.env.BERVEL_PROXY_MODE || 'nginx'
const container = 'bervel-web-access-test-' + process.pid
const requests = []
let upstream, child, baseUrl
before(async () => {
  upstream = createServer((request, response) => {
    requests.push({ path: request.url, authorization: request.headers.authorization })
    response.setHeader('Content-Type', 'application/json')
    response.statusCode = request.headers.authorization === 'Bearer ' + token ? 200 : 401
    response.end(JSON.stringify({ artifacts: [], next_cursor: null }))
  })
  await new Promise(resolve => upstream.listen(0, '0.0.0.0', resolve))
  const port = upstream.address().port
  const probe = createServer()
  await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve))
  const webPort = probe.address().port
  await new Promise(resolve => probe.close(resolve))
  baseUrl = 'http://127.0.0.1:' + webPort
  const env = { ...process.env, BERVEL_WEB_ACCESS_KEY: token,
    BERVEL_API_TARGET: `http://${mode === 'nginx' ? 'host.docker.internal' : '127.0.0.1'}:${port}` }
  if (mode === 'nginx') {
    execFileSync('docker', ['network', 'create', container], { stdio: 'ignore' })
    child = spawn('docker', ['run', '--rm', '--name', container, '--network', container, '-p', `127.0.0.1:${webPort}:80`,
      '-e', 'BERVEL_API_TARGET', '-e', 'BERVEL_WEB_ACCESS_KEY', 'bervel-frontend:local'], { env, stdio: 'ignore' })
  } else {
    child = spawn(process.execPath, [resolve(__dirname, '../../frontend/node_modules/vite/bin/vite.js'), ...(mode === 'preview' ? ['preview'] : []),
      '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'],
    { env, cwd: resolve(__dirname, '../../frontend'), stdio: 'ignore' })
  }
  for (let attempt = 0; attempt < 100; attempt++) {
    if (child.exitCode !== null) throw new Error('Web test server failed to start')
    try { if ((await fetch(baseUrl)).ok) return } catch {}
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  throw new Error('Web test server did not become ready')
})
after(async () => {
  if (mode === 'nginx') {
    try { execFileSync('docker', ['stop', container], { stdio: 'ignore' }) } catch {}
    try { execFileSync('docker', ['network', 'rm', container], { stdio: 'ignore' }) } catch {}
  } else child?.kill()
  await new Promise(resolve => upstream?.close(resolve))
})
test('server adds its own key; a supplied browser key cannot switch the shared scope', async () => {
  for (const authorization of [undefined, 'Bearer a-different-browser-key']) {
    const headers = { 'X-Bervel-Web': '1', ...(authorization ? { Authorization: authorization } : {}) }
    const response = await fetch(baseUrl + '/api/v0.1/artifacts?cursor=0&limit=20', { headers })
    assert.equal(response.status, 200)
    assert.equal(requests.at(-1).authorization === 'Bearer ' + token, true)
    assert.equal((await response.text()).includes(token), false)
  }
})
test('ordinary forms and foreign-site requests cannot use the implicit server key', async () => {
  const count = requests.length
  for (const headers of [{}, { 'X-Bervel-Web': '1', 'Sec-Fetch-Site': 'cross-site' }]) {
    const response = await fetch(baseUrl + '/api/v0.1/artifacts', { method: 'POST', headers, body: 'test' })
    assert.equal(response.status, 403)
  }
  assert.equal(requests.length, count)
})
test('the web proxy does not forward internal worker callbacks', async () => {
  const count = requests.length
  const response = await fetch(baseUrl + '/api/internal/process-runs/claim', { headers: { 'X-Bervel-Web': '1' } })
  assert.equal(response.status, 404)
  assert.equal(requests.length, count)
})
test('server secret is absent from the page and all entry scripts', async () => {
  const html = await (await fetch(baseUrl)).text()
  assert.equal(html.includes(token), false)
  for (const match of html.matchAll(/<script[^>]*src="([^"]+)"/g)) {
    const response = await fetch(new URL(match[1], baseUrl))
    assert.equal((await response.text()).includes(token), false)
  }
})
