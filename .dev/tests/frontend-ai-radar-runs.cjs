const {test} = require('node:test')
const assert = require('node:assert/strict')
const http = require('node:http')
const {chromium} = require('playwright')

const sid = '11111111-1111-4111-8111-111111111111'
const oldSid = '22222222-2222-4222-8222-222222222222'
const rid = '33333333-3333-4333-8333-333333333333'
const oldRid = '44444444-4444-4444-8444-444444444444'
const qid = '55555555-5555-4555-8555-555555555555'
const aid = '66666666-6666-4666-8666-666666666666'
const failedQid = '88888888-8888-4888-8888-888888888888'
const historicChild = '99999999-9999-4999-8999-999999999999'
const stockId = '77777777-7777-4777-8777-777777777777'
const time = '2026-11-05T09:00:00Z'
const profile = {stock_artifact_id: stockId, stock_sha256: 'a'.repeat(64), products: [{id: 'p1', sku: 'B1', name: 'Болт', unit: 'шт', attributes: {}, enabled: true, manufactured_before: null}], experimental_products: [], include_experimental: false, production: [], resources: [], scoring_policy: {}}
const snapshot = {id: sid, created_at: time, profile}
const review = {state: 'complete', recommendation: 'Нужна проверка заводских данных.', risks: ['Недостаточная маржа.'], assumptions: ['Карточки независимы.'], evidence_refs: ['assessment']}
function card(i, state, score) {
  return {analysis_id: `analysis-${i}`, candidate_id: `candidate-${i}`, state, evaluated_at: time,
    tender: {title: ['Поставка болтов', 'Поставка гаек', 'Обслуживание станка'][i - 1], number: `SIM-${i}`, link: null},
    completeness: {requirements: 'complete', economics: score === null ? 'unknown' : 'complete', schedule: 'complete', reasons: []},
    analysis: {coverage_percent: '100', benefit_percent: '-10', economics: {scope: score === null ? null : 'full', revenue: score === null ? null : '100', cost: score === null ? null : '110'}, positions: [{id: 'p1', name: 'Болт М12', required: '10', covered: '10', shortage: '0', unit: 'шт'}]},
    fulfillment: {status: score === null ? 'needs_review' : 'feasible', reasons: [], production_allocations: []},
    assessment: {score, stress_score: score, priority: score === null ? 'needs_review' : 'excluded', reasons: score === null ? ['FULL_ECONOMICS_OR_FEASIBILITY_UNKNOWN'] : ['PLAN_NOT_PROFITABLE'], E: null, S: '1', P: null, full_economics: {profit: score === null ? null : '-10', margin_percent: score === null ? null : '-10', currency: 'RUB'}}, review}
}
const cards = [card(1, 'accepted', 0), card(2, 'needs_data', null), card(3, 'irrelevant', 0)]
const candidates = Array.from({length: 125}, (_, i) => ({id: `brief-${i + 1}`, brief: {OrderName: `Кандидат ${i + 1}`}, state: 'not_loaded', reasons: ['DETAIL_BUDGET']}))
function run(id = rid, settings = oldSid) {return {run_id: id, settings_snapshot_id: settings, created_at: time, data: {state: 'complete', phase: 'complete', checkpoint: {search_attempts: 40, candidates: candidates.map(c => ({id: c.id})), truncated: true, stop_reason: 'search_budget'}, detail_attempts: 3, model_attempts: 6, counts: {looked: 3, accepted: 1, needs_data: 1, irrelevant: 1, failed: 0}, stop_reason: 'detail_budget'}, cards, candidates, reports: []}}
async function withBrowser(width, callback) {
  const upstream = new URL(process.env.BERVEL_FRONTEND_URL || 'http://frontend')
  const proxy = http.createServer((req, res) => {
    const forward = http.request(new URL(req.url, upstream), {method: req.method, headers: req.headers}, response => {res.writeHead(response.statusCode, response.headers);response.pipe(res)})
    forward.on('error', () => {res.writeHead(502);res.end()});req.pipe(forward)
  })
  await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve))
  const browser = await chromium.launch({headless: true})
  try {const page = await browser.newPage({viewport: {width, height: 1000}});page.setDefaultTimeout(15000);const errors = [];page.on('pageerror', e => errors.push(e.message));await callback(page, `http://127.0.0.1:${proxy.address().port}`);assert.deepEqual(errors, []);assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)} finally {await browser.close();await new Promise(resolve => proxy.close(resolve))}
}

for (const width of [1440, 390]) test(`profile run: saved snapshot, exact receipt recovery, all counters and score zero at ${width}px`, () => withBrowser(width, async (page, origin) => {
  const commands = [];const requested = [];let lose = true;let reads = 0;let navigationPhase = false
  await page.route('**/api/v0.1/**', route => {
    const req = route.request();const path = new URL(req.url()).pathname;requested.push(path)
    if (path.endsWith('/settings/company')) return route.fulfill({json: {snapshot}})
    if (navigationPhase && path.endsWith('/artifacts')) return route.fulfill({json: {artifacts: [], next_cursor: null}})
    if (navigationPhase && path.endsWith('/cards')) return route.fulfill({json: {cards: [], next_offset: null}})
    if (navigationPhase && path.endsWith('/jobs/active')) return route.fulfill({json: null})
    if (path.endsWith('/runs') && req.method() === 'GET') return route.fulfill({json: {runs: [], next_offset: null}})
    if (path.endsWith('/runs') && req.method() === 'POST') {commands.push(req.postDataJSON());if (lose) {lose = false;return route.abort('failed')}return route.fulfill({status: 202, json: {run_id: rid, job_id: rid, state: 'queued'}})}
    if (path.endsWith('/runs/' + rid)) {reads++;const value = run(rid, sid);if (reads === 1) {value.data = {...value.data, state: 'running', phase: 'details', detail_attempts: 1, counts: {looked: 1, analyzing: 1, accepted: 0, needs_data: 0, irrelevant: 0, failed: 0}};value.cards = [{...cards[0], state: 'analyzing', analysis: null, fulfillment: null, assessment: null, review: null}]}return route.fulfill({json: value})}
    return route.fulfill({status: 404, json: {error: {message: 'Unexpected fixture route'}}})
  })
  await page.goto(origin + '/#/ai-radar-runs')
  await page.getByRole('button', {name: 'Запустить анализ', exact: true}).click()
  await page.getByRole('button', {name: 'Повторить сохранённую команду', exact: true}).waitFor()
  assert.equal(await page.getByRole('button', {name: 'Запустить анализ', exact: true}).isDisabled(), true)
  await page.reload()
  await page.getByRole('button', {name: 'Повторить сохранённую команду', exact: true}).click()
  await page.getByRole('heading', {name: 'Поставка болтов', exact: true}).waitFor()
  await page.getByText('Анализ карточки выполняется…', {exact: true}).waitFor()
  assert.equal(await page.getByRole('button', {name: 'Спросить модель', exact: true}).isDisabled(), true)
  if (process.env.BERVEL_SCREENSHOTS) await page.getByRole('article').first().screenshot({path: `${process.env.BERVEL_SCREENSHOTS}/ai-radar-analyzing-${width}.png`})
  assert.deepEqual(commands[0], commands[1])
  assert.equal(commands[0].settings_snapshot_id, sid)
  assert.equal('stock_artifact_id' in commands[0], false)
  assert.equal(requested.some(path => path.includes('/artifacts')), false, 'launch uses saved company snapshot without selecting warehouse again')
  await page.getByText('Недостаточно данных: 1', {exact: true}).waitFor()
  await page.getByText('Соответствие не подтверждено: 1', {exact: true}).waitFor()
  const accepted = page.getByRole('article').filter({has: page.getByRole('heading', {name: 'Поставка болтов', exact: true})})
  const unknown = page.getByRole('article').filter({has: page.getByRole('heading', {name: 'Поставка гаек', exact: true})})
  assert.equal((await accepted.getByLabel('Оценка карточки').innerText()).replace(/\s+/g, ' '), '0 из 100')
  assert.equal(await unknown.getByLabel('Оценка карточки').innerText(), 'Нет оценки')
  assert.equal(await page.getByRole('article').count(), 3)
  await page.getByText('Охват ограничен бюджетом', {exact: false}).waitFor()
  await page.getByText('Карточек просмотрено: 3', {exact: true}).waitFor()
  await page.getByText('Кандидаты поиска (125)', {exact: true}).click()
  const pool = page.getByRole('region', {name: 'Кандидаты поиска', exact: true})
  assert.equal(await pool.locator('tbody tr').count(), 50)
  await page.getByRole('button', {name: 'Следующие кандидаты', exact: true}).click()
  await pool.getByText('Кандидат 51', {exact: true}).waitFor()
  await page.getByRole('button', {name: 'Следующие кандидаты', exact: true}).click()
  assert.equal(await pool.locator('tbody tr').count(), 25)
  await page.getByRole('button', {name: 'Предыдущие кандидаты', exact: true}).click()
  assert.equal(await pool.locator('tbody tr').count(), 50)
  if (process.env.BERVEL_SCREENSHOTS) await page.locator('.run-candidates').screenshot({path: `${process.env.BERVEL_SCREENSHOTS}/ai-radar-candidates-${width}.png`})
  if (process.env.BERVEL_SCREENSHOTS) await page.screenshot({path: `${process.env.BERVEL_SCREENSHOTS}/ai-radar-runs-${width}.png`, fullPage: true})
  navigationPhase = true
  const nav = page.getByRole('navigation', {name: 'Разделы приложения', exact: true})
  assert.equal(await nav.getByRole('link', {name: 'Радар ИИ', exact: true}).getAttribute('href'), '#/ai-radar-runs')
  await page.getByRole('link', {name: 'Ручной анализ', exact: true}).click()
  await page.getByRole('main', {name: 'Ручной анализ', exact: true}).waitFor()
  assert.equal(await nav.getByRole('link', {name: 'Радар ИИ', exact: true}).getAttribute('aria-current'), 'page')
  await nav.getByRole('link', {name: 'Настройки предприятия', exact: true}).click()
  await page.getByRole('main', {name: 'Настройки предприятия', exact: true}).waitFor()
  await nav.getByRole('link', {name: 'Радар ИИ', exact: true}).click()
  await page.getByRole('main', {name: 'Радар ИИ', exact: true}).waitFor()
  await page.getByRole('heading', {name: 'Поставка болтов', exact: true}).waitFor()
  if (process.env.BERVEL_SCREENSHOTS) await page.screenshot({path: `${process.env.BERVEL_SCREENSHOTS}/ai-radar-navigation-${width}.png`, fullPage: true})
}))

test('question and manual audit are queued, polled and attached to the historic card snapshot', () => withBrowser(1440, async (page, origin) => {
  const commands = [];let qpoll = 0;let apoll = 0;let questionWrites = 0;let reanalysisWrites = 0
  const asked = 'Почему нет прибыли?\nЧто проверить?'
  const current = run()
  await page.route('**/api/v0.1/**', route => {
    const req = route.request();const path = new URL(req.url()).pathname
    if (path.endsWith('/settings/company')) return route.fulfill({json: {snapshot}})
    if (path.endsWith('/runs')) return route.fulfill({json: {runs: [current], next_offset: null}})
    if (path.endsWith('/runs/' + rid)) return route.fulfill({json: current})
    if (path.endsWith('/runs/' + oldRid)) return route.fulfill({json: run(oldRid, sid)})
    if (path.endsWith('/runs/' + historicChild)) return route.fulfill({json: run(historicChild, oldSid)})
    if (path.endsWith('/reanalyze') && req.method() === 'POST') {commands.push({path, body: req.postDataJSON()});reanalysisWrites++;if (reanalysisWrites === 1) return route.abort('failed');const child = reanalysisWrites === 2 ? oldRid : historicChild;return route.fulfill({status: 202, json: {run_id: child, job_id: child, state: 'queued'}})}
    if (req.method() === 'POST') {commands.push({path, body: req.postDataJSON()});if (path.endsWith('/question')) {questionWrites++;if (questionWrites === 1) return route.abort('failed')}return route.fulfill({status: 202, json: {job_id: path.endsWith('/question') ? questionWrites > 2 ? failedQid : qid : aid, state: 'queued'}})}
    if (path.endsWith('/jobs/' + failedQid)) return route.fulfill({json: {job_id: failedQid, state: 'failed', error: {code: 'MODEL_FAILED', message: 'Модель не завершила ответ.'}}})
    if (path.endsWith('/jobs/' + qid)) {qpoll++;if (qpoll >= 2 && !current.reports.some(r => r.job_id === qid)) current.reports.push({job_id: qid, kind: 'question', analysis_id: 'analysis-1', question: asked, result: {...review, answer: 'По историческому снимку затраты превышают выручку.'}});return route.fulfill({json: {job_id: qid, state: qpoll < 2 ? 'queued' : 'loaded', error: null}})}
    if (path.endsWith('/jobs/' + aid)) {apoll++;current.reports = current.reports.filter(r => r.job_id !== aid);current.reports.push({job_id: aid, kind: 'audit', result: apoll < 2 ? {status: 'running', checked: 1, total: 3, findings: [], limitations: []} : {status: 'partial', checked: 2, total: 3, findings: [{stage: 'reference', reason: 'REFERENCE_DISAGREEMENT', severity: 'warning'}], reference_comparison: {provided: true, checked: 1, agreements: 0, disagreements: 1, note: 'Ручной эталон сравнивается отдельно.'}, limitations: ['Карточки независимы.']}});return route.fulfill({json: {job_id: aid, state: apoll < 2 ? 'running' : 'loaded', error: null}})}
    return route.fulfill({status: 404, json: {error: {message: 'Unexpected fixture route'}}})
  })
  await page.goto(origin + '/#/ai-radar-runs')
  await page.locator('.run-history-item').first().click()
  await page.getByText('Источники и исторический снимок', {exact: true}).click()
  await page.getByText(oldSid, {exact: true}).waitFor()
  const target = page.getByRole('article').filter({has: page.getByRole('heading', {name: 'Поставка болтов', exact: true})})
  await target.getByLabel('Вопрос по этой карточке', {exact: true}).fill(asked)
  await target.getByRole('button', {name: 'Спросить модель', exact: true}).click()
  await page.getByRole('button', {name: 'Повторить сохранённую команду', exact: true}).waitFor()
  await page.reload()
  await page.getByRole('button', {name: 'Повторить сохранённую команду', exact: true}).click()
  await page.getByText('Вопрос поставлен в очередь', {exact: false}).waitFor()
  await page.getByText('По историческому снимку затраты превышают выручку.', {exact: true}).waitFor()
  assert.equal(commands[0].path, `/api/v0.1/ai-tender-radar/runs/${rid}/question`)
  assert.equal(commands[0].body.analysis_id, 'analysis-1')
  assert.equal(commands[0].body.question, asked)
  assert.equal('settings_snapshot_id' in commands[0].body, false)
  assert.deepEqual(commands[0], commands[1], 'a question retry retains historic analysis id, exact question and command key')
  await page.getByLabel('Ручной эталон (JSON)', {exact: true}).setInputFiles({name: 'bad-reference.json', mimeType: 'application/json', buffer: Buffer.from('{"analysis-1":0}')})
  await page.getByRole('alert').filter({hasText: 'Эталон должен содержать'}).waitFor()
  assert.equal(await page.getByRole('button', {name: 'Проверить прогон', exact: true}).isDisabled(), true)
  await page.getByLabel('Ручной эталон (JSON)', {exact: true}).setInputFiles({name: 'manual-reference.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({'analysis-1': {score: 0}}))})
  await page.getByText('manual-reference.json', {exact: false}).waitFor()
  await page.getByRole('button', {name: 'Проверить прогон', exact: true}).click()
  await page.getByText('Проверка поставлена в очередь', {exact: false}).waitFor()
  await page.getByText('Проверяется 1 из 3', {exact: true}).waitFor()
  assert.equal(await page.getByRole('button', {name: 'Проверить прогон', exact: true}).isDisabled(), true, 'a running audit report must retain its pending operation')
  if (process.env.BERVEL_SCREENSHOTS) await page.locator('.run-audit-report').screenshot({path: `${process.env.BERVEL_SCREENSHOTS}/ai-radar-audit-progress.png`})
  await page.getByText('С ручным эталоном: 0 совпадений, 1 расхождение', {exact: true}).waitFor()
  assert.deepEqual(commands[2].body.reference, {'analysis-1': {score: 0}})
  await page.getByText('Проверено 2 из 3', {exact: false}).waitFor()
  await target.getByLabel('Вопрос по этой карточке', {exact: true}).fill('Что проверить?')
  await target.getByRole('button', {name: 'Спросить модель', exact: true}).click()
  await page.getByRole('alert').filter({hasText: 'Модель не завершила ответ.'}).waitFor()
  assert.equal(await target.getByRole('button', {name: 'Спросить модель', exact: true}).isDisabled(), false, 'a failed background job must not lock future questions')
  await target.getByRole('button', {name: 'Пересчитать карточку', exact: true}).click()
  await page.getByRole('button', {name: 'Повторить сохранённую команду', exact: true}).waitFor()
  await page.reload()
  await Promise.all([page.waitForResponse(response => response.url().endsWith('/runs/' + oldRid)), page.getByRole('button', {name: 'Повторить сохранённую команду', exact: true}).click()])
  assert.deepEqual(commands[4], commands[5])
  assert.equal(commands[4].path, `/api/v0.1/ai-tender-radar/runs/${rid}/reanalyze`)
  assert.equal(commands[4].body.settings_snapshot_id, sid)
  assert.equal(commands[4].body.analysis_id, 'analysis-1')
  await page.getByText('Источники и исторический снимок', {exact: true}).click()
  await page.getByText(sid, {exact: true}).waitFor()
  await page.locator('.run-history-item').first().click()
  await target.getByRole('combobox', {name: 'Профиль для пересчёта', exact: true}).selectOption('historic')
  await Promise.all([page.waitForResponse(response => response.url().endsWith('/runs/' + historicChild)), target.getByRole('button', {name: 'Пересчитать карточку', exact: true}).click()])
  assert.equal(commands[6].body.settings_snapshot_id, oldSid)
  assert.equal(commands[6].body.analysis_id, 'analysis-1')
}))

test('history paginates and stale run reads cannot replace the newly selected run', () => withBrowser(390, async (page, origin) => {
  let releaseOld
  const oldGate = new Promise(resolve => {releaseOld = resolve})
  const first = {...run(rid), created_at: '2026-11-05T09:00:00Z'}
  const second = {...run(oldRid), created_at: '2026-10-31T09:00:00Z', cards: [card(1, 'accepted', 0)]}
  second.cards[0] = {...second.cards[0], tender: {...second.cards[0].tender, title: 'Исторический тендер'}}
  await page.route('**/api/v0.1/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/settings/company')) return route.fulfill({json: {snapshot}})
    if (url.pathname.endsWith('/runs')) return route.fulfill({json: url.searchParams.has('offset') ? {runs: [second], next_offset: null} : {runs: [first], next_offset: 30}})
    if (url.pathname.endsWith('/runs/' + rid)) {await oldGate;return route.fulfill({json: first}).catch(() => {})}
    if (url.pathname.endsWith('/runs/' + oldRid)) return route.fulfill({json: second})
    return route.fulfill({status: 404, json: {error: {message: 'Unexpected fixture route'}}})
  })
  await page.goto(origin + '/#/ai-radar-runs')
  await page.locator('.run-history-item').first().click()
  await page.getByRole('button', {name: 'Показать ещё', exact: true}).click()
  await page.locator('.run-history-item').nth(1).waitFor()
  assert.equal(await page.locator('.run-history-item').count(), 2)
  await page.locator('.run-history-item').last().click()
  await page.getByRole('heading', {name: 'Исторический тендер', exact: true}).waitFor()
  releaseOld()
  await page.waitForTimeout(200)
  assert.equal(await page.getByRole('heading', {name: 'Поставка гаек', exact: true}).count(), 0)
}))

test('denied browser storage never crashes the page or sends an unrecorded command', () => withBrowser(390, async (page, origin) => {
  let writes = 0
  await page.addInitScript(() => {Storage.prototype.getItem = function() {throw new DOMException('blocked', 'SecurityError')};Storage.prototype.setItem = function() {throw new DOMException('blocked', 'QuotaExceededError')}})
  await page.route('**/api/v0.1/**', route => {if (route.request().method() === 'POST') writes++;return route.fulfill({json: route.request().url().includes('/settings/company') ? {snapshot} : {runs: [], next_offset: null}})})
  await page.goto(origin + '/#/ai-radar-runs')
  await page.getByRole('main', {name: 'Радар ИИ', exact: true}).waitFor()
  await page.getByRole('alert').filter({hasText: 'хранилищ'}).waitFor()
  assert.equal(writes, 0)
  assert.equal(await page.getByRole('button', {name: 'Запустить анализ', exact: true}).isDisabled(), true)
}))

test('malformed run data is an explicit read error and a malformed receipt preserves the command', () => withBrowser(1440, async (page, origin) => {
  let writes = 0
  await page.route('**/api/v0.1/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/settings/company')) return route.fulfill({json: {snapshot}})
    if (route.request().method() === 'POST') {writes++;return route.fulfill({status: 202, json: {job_id: 12, state: 'queued'}})}
    if (path.endsWith('/runs/' + rid)) return route.fulfill({json: {...run(), cards: [{invalid: true}]}})
    return route.fulfill({json: {runs: [run()], next_offset: null}})
  })
  await page.goto(origin + '/#/ai-radar-runs')
  await page.locator('.run-history-item').first().click()
  await page.getByRole('alert').filter({hasText: 'формат'}).waitFor()
  assert.equal(await page.getByRole('article').count(), 0)
  await page.getByRole('button', {name: 'Запустить анализ', exact: true}).click()
  await page.getByRole('button', {name: 'Повторить сохранённую команду', exact: true}).waitFor()
  assert.equal(writes, 1)
  assert.ok(await page.evaluate(() => sessionStorage.getItem('bervel.runs.pending')))
}))
