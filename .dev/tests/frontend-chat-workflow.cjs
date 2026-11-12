const assert = require('node:assert/strict')
const { before, after, beforeEach, afterEach, test } = require('node:test')
const { mkdirSync } = require('node:fs')
const { resolve } = require('node:path')
const { chromium } = require('playwright')

const baseUrl = process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5173'
const artifacts = resolve(process.env.BERVEL_TEST_ARTIFACT_ROOT||resolve(__dirname, '../local/long-task-workflow-2026-10-28/ui'))
const sessionId = '11111111-1111-4111-8111-111111111111'
const timestamp = '2026-10-28T10:00:00Z'
const phaseLabels = { research: 'Исследуем источники', synthesizing: 'Собираем ответ', generating: 'Создаём файл', verifying: 'Проверяем результат' }
function workflow(phase = 'research', reason = null) {
  const phases = ['research', 'synthesizing', 'generating', 'verifying']
  const history = phases.slice(0, phases.indexOf(phase) + 1).map(phase => ({ phase, at: timestamp, reason: null }))
  if (!history.length) history.push({ phase, at: timestamp, reason })
  return { phase, updated_at: timestamp, reason, research_rounds: 2, max_research_rounds: 4,
    web_searches: 1, max_web_searches: 2, elapsed_seconds: 42.5, max_research_seconds: 240, history }
}
function turn(state = 'running', phase = 'research', reason = null) {
  return { turn_id: '33333333-3333-4333-8333-333333333333', question: 'Подготовь отчёт в PDF',
    answer: ['completed', 'partial'].includes(state) ? 'Сохранённый результат исследования.' : null,
    state, created_at: timestamp, sources: [], error: state === 'failed' ? 'Техническая ошибка.' : null,
    results: [], actions: [], workflow: workflow(phase, reason) }
}
let browser, context, page, session, requests, errors
before(async () => { mkdirSync(artifacts, { recursive: true }); browser = await chromium.launch({executablePath:process.env.BERVEL_BROWSER_PATH, headless: true }) })
after(async () => { await browser?.close() })
beforeEach(async () => {
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.addInitScript(id => localStorage.setItem('bervel.chat.session_id', id), sessionId)
  page = await context.newPage(); page.setDefaultTimeout(3500)
  session = { session_id: sessionId, created_at: timestamp, turns: [turn()] }
  requests = []; errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => { if (request.url().includes('/api/v0.1/')) requests.push(request) })
  await page.route('**/api/v0.1/**', route => {
    const request = route.request(), path = new URL(request.url()).pathname
    if (request.method() !== 'GET') return route.fulfill({ status: 405, json: { error: 'Reading a workflow must never send a command' } })
    if (path === '/api/v0.1/chat/sessions') return route.fulfill({ json: { sessions: [{ session_id: sessionId,
      created_at: timestamp, title: session.turns[0].question, turn_count: 1,
      has_pending: session.turns.some(item => ['queued', 'running'].includes(item.state)) }], next_offset: null } })
    if (path === '/api/v0.1/chat/sessions/' + sessionId) return route.fulfill({ json: session })
    return route.fulfill({ status: 404, json: {} })
  })
})
afterEach(async () => {
  try {
    assert.deepEqual(errors, [])
    assert.equal(requests.filter(request => request.method() !== 'GET').length, 0, 'restoring/polling a workflow must not send commands')
    for (const request of requests) assert.equal(request.headers().authorization, undefined)
  } finally { await context.close() }
})
async function open() { await page.goto(baseUrl + '/#/chat') }
function result() { return page.getByRole('article', { name: 'Ответ Codex', exact: true }) }
function progress() { return result().getByRole('region', { name: 'Этапы выполнения', exact: true }) }
function reads() { return requests.filter(request => new URL(request.url()).pathname === '/api/v0.1/chat/sessions/' + sessionId).length }

// Missing server-phase rendering, treating partial/blocked as invalid, automatic progress,
// or a command on reload must fail these observable browser checks.
test('server phases survive reload and advance only after the next API read', async () => {
  await open()
  await progress().getByRole('status').filter({ hasText: phaseLabels.research }).waitFor()
  assert.equal(await progress().locator('[aria-current="step"]').innerText(), phaseLabels.research)
  assert.equal(await progress().getByRole('progressbar').count(), 0)
  assert.doesNotMatch(await progress().innerText(), /\d\s*%/)
  await page.waitForTimeout(1100)
  assert.equal(await progress().getByText(phaseLabels.generating, { exact: true }).count(), 0)
  for (const phase of ['synthesizing', 'generating', 'verifying']) {
    session.turns = [turn('running', phase)]
    await progress().getByRole('status').filter({ hasText: phaseLabels[phase] }).waitFor()
    assert.equal(await progress().locator('[aria-current="step"]').innerText(), phaseLabels[phase])
  }
  await page.reload()
  await progress().getByRole('status').filter({ hasText: phaseLabels.verifying }).waitFor()
  assert.match(await progress().getByRole('list').innerText(), /Исследуем источники[\s\S]*Собираем ответ[\s\S]*Создаём файл[\s\S]*Проверяем результат/)
  await page.screenshot({ path: resolve(artifacts, 'workflow-desktop.png'), fullPage: true })
})

test('partial result retains its file and reason and stops polling without becoming completed', async () => {
  session.turns = [turn('partial', 'partial', 'Достигнут лимит исследования; данных по региону нет.')]
  session.turns[0].results = [{ kind: 'file', artifact_id: '22222222-2222-4222-8222-222222222222',
    filename: 'Исследование.pdf', media_type: 'application/pdf', download_url: '/api/v0.1/artifacts/22222222-2222-4222-8222-222222222222/original', preview_url: '/api/v0.1/artifacts/22222222-2222-4222-8222-222222222222/preview' }]
  await open()
  await progress().getByRole('status').filter({ hasText: 'Частичный результат' }).waitFor()
  assert.match(await progress().innerText(), /Достигнут лимит исследования; данных по региону нет/)
  await result().getByRole('button', { name: 'Скачать файл', exact: true }).waitFor()
  assert.equal(await result().getByRole('alert').count(), 0)
  assert.equal(await page.getByRole('button', { name: 'Новый диалог', exact: true }).isEnabled(), true)
  const count = reads(); await page.waitForTimeout(1250); assert.equal(reads(), count)
})

test('blocked requires missing input and is distinct from a technical failure', async () => {
  session.turns = [turn('blocked', 'blocked', 'Для продолжения нужен исходный договор.')]
  await open()
  await progress().getByRole('status').filter({ hasText: 'Выполнение заблокировано' }).waitFor()
  assert.match(await progress().innerText(), /Для продолжения нужен исходный договор/)
  assert.equal(await result().getByText('Техническая ошибка.', { exact: true }).count(), 0)
  const count = reads(); await page.waitForTimeout(1250); assert.equal(reads(), count)
  session.turns = [{ ...turn('failed'), workflow: null }]
  await page.reload(); await result().getByRole('alert').filter({ hasText: 'Техническая ошибка.' }).waitFor()
})

test('technical failure keeps the last phase as history without claiming it is still running', async () => {
  session.turns = [turn('failed', 'generating')]
  await open()
  await result().getByRole('alert').filter({ hasText: 'Техническая ошибка.' }).waitFor()
  assert.equal(await progress().getByRole('status').innerText(), 'Выполнение прервано')
  assert.equal(await progress().locator('[aria-current="step"]').count(), 0)
  const count = reads(); await page.waitForTimeout(1250); assert.equal(reads(), count)
})

test('legacy turns with absent or null workflow retain the existing status and answer', async () => {
  session.turns[0].workflow = undefined
  await open(); await result().getByRole('status').filter({ hasText: 'Готовим ответ…' }).waitFor()
  assert.equal(await progress().count(), 0)
  session.turns = [{ ...turn('completed'), workflow: null }]
  await page.reload(); await result().getByText('Сохранённый результат исследования.', { exact: true }).waitFor()
  assert.equal(await progress().count(), 0)
})

test('malformed workflow is rejected before rendering untrusted stage or history', async () => {
  for (const invalid of [
    { ...workflow(), web_searches: -1 },
    { ...workflow(), phase: 'imaginary' },
    { ...workflow(), updated_at: 'not-a-date' },
    { ...workflow(), history: [{ phase: 'research', at: 'not-a-date', reason: null }] },
  ]) {
    session.turns[0].workflow = invalid
    await open()
    await page.getByRole('alert').filter({ hasText: 'API чата вернул несовместимый ответ' }).waitFor()
    assert.equal(await progress().count(), 0)
  }
})

test('web sources are compact, deduplicated by URL and the complete saved list remains accessible on mobile', async () => {
  const first = Array.from({ length: 14 }, (_, i) => ({ title: 'Первичный источник ' + i, url: 'https://example.com/source/' + i }))
  const second = Array.from({ length: 14 }, (_, i) => ({ title: 'Сохранённое название ' + i, url: 'https://example.com/source/' + i }))
  second.push({ title: '<script>unsafe</script>', url: 'javascript:alert(1)' })
  session.turns = [turn('completed', 'completed')]
  session.turns[0].results = [
    { kind: 'web', answer: 'Первый сохранённый поиск.', sources: first },
    { kind: 'web', answer: 'Второй сохранённый поиск.', sources: second },
    { kind: 'web', answer: 'Третий сохранённый поиск.', sources: first },
    { kind: 'web', answer: 'Четвёртый сохранённый поиск.', sources: first },
    { kind: 'web', answer: 'Пятый сохранённый поиск.', sources: first },
  ]
  await page.setViewportSize({ width: 390, height: 844 }); await open()
  await result().getByText('Сохранённый результат исследования.', { exact: true }).waitFor()
  const sources = result().getByRole('group', { name: 'Веб-источники (15)', exact: true })
  await sources.getByText('Веб-источники (15)', { exact: true }).waitFor()
  assert.equal(await sources.locator('a:visible').count(), 0)
  await page.screenshot({ path: resolve(artifacts, 'workflow-sources-mobile-collapsed.png'), fullPage: true })
  await sources.locator('summary').first().click()
  assert.equal(await sources.locator('a:visible').count(), 14)
  assert.equal(await sources.locator('a[href="https://example.com/source/0"]:visible').count(), 1)
  assert.equal(await sources.locator('a[href^="javascript:"]').count(), 0)
  const original = result().getByRole('group', { name: 'Полный сохранённый список (71)', exact: true })
  await original.locator('summary').click()
  await original.getByText('Первый сохранённый поиск.', { exact: true }).waitFor()
  await original.getByText('Второй сохранённый поиск.', { exact: true }).waitFor()
  await original.getByText('Третий сохранённый поиск.', { exact: true }).waitFor()
  await original.getByText('Четвёртый сохранённый поиск.', { exact: true }).waitFor()
  await original.getByText('Пятый сохранённый поиск.', { exact: true }).waitFor()
  assert.equal(await original.getByRole('listitem').count(), 71)
  assert.equal(await original.getByText('Сохранённое название 13', { exact: true }).isVisible(), true)
  assert.equal(await page.locator('script').filter({hasText:'unsafe'}).count(), 0)
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
  await page.screenshot({ path: resolve(artifacts, 'workflow-sources-mobile.png'), fullPage: true })
})
