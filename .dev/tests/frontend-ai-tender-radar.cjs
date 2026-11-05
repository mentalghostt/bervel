const {test} = require('node:test')
const assert = require('node:assert/strict')
const http = require('node:http')
const {chromium} = require('playwright')

const stockId = '11111111-1111-4111-8111-111111111111'
const jobId = '22222222-2222-4222-8222-222222222222'
const time = '2026-11-03T09:00:00Z'
const stock = {artifact_id: stockId, filename: 'Контрольный склад.csv', media_type: 'text/csv', size: 100, created_at: time, usage_role: 'stock'}
const tender = {id: 'kontur-control', title: 'Поставка высокопрочных болтов М12', number: '0123456789', link: null, position_count: 1, limitations: []}
const economics = {revenue: '100', cost: '40', currency: 'RUB', profit: '60', status: 'profitable', margin_percent: '60', reason: null}
const position = {id: '1:0:1', lot: 1, customer: null, name: 'Болт М12', unit: 'шт', required: '10', covered: '10', shortage: '0',
  stock_status: 'full', stock_reason: null, economics,
  matches: [{sku: 'B12', stock_row: 2, stock_unit: 'шт', stock_free_quantity: '10',
    evidence: [{requirement: 'Диаметр резьбы', tender_value: '12', stock_field: 'product', stock_value: 'Болт М12'}]}],
  allocations: [{sku: 'B12', stock_quantity: '10', stock_unit: 'шт', stock_unit_cost: '4', stock_currency: 'RUB', position_quantity: '10'}]}
const card = {job_id: jobId, stock_artifact_id: stockId, updated_at: time, tender, status: 'complete', coverage_percent: '100', benefit_percent: '60', mock: true}
const result = {job_id: jobId, stock_artifact_id: stockId, state: 'loaded', stock: {filename: stock.filename, sha256: 'a'.repeat(64), row_count: 2},
  tender, error: null, analysis: {status: 'complete', model: 'fixture', positions: [position], economics, coverage_reason: null, model_error: null, benefit_clamped: false}}

async function withBrowser(width, callback) {
  const upstream = new URL(process.env.BERVEL_FRONTEND_URL || 'http://frontend')
  const proxy = http.createServer((req, res) => {
    const transport = http.request(new URL(req.url, upstream), {method: req.method, headers: req.headers}, response => {
      res.writeHead(response.statusCode, response.headers)
      response.pipe(res)
    })
    transport.on('error', () => {res.writeHead(502);res.end()})
    req.pipe(transport)
  })
  await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve))
  const browser = await chromium.launch({headless: true})
  try {
    const page = await browser.newPage({viewport: {width, height: 920}})
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await callback(page, `http://127.0.0.1:${proxy.address().port}`)
    assert.deepEqual(errors, [])
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
  } finally {
    await browser.close()
    await new Promise(resolve => proxy.close(resolve))
  }
}

for (const width of [1440, 390]) test(`AI radar launch, reload and evidence at ${width}px`, () => withBrowser(width, async (page, origin) => {
  const sent = []
  let state = null
  let failOnce = width === 1440
  await page.route('**/api/v0.1/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/artifacts')) return route.fulfill({json: {artifacts: [stock], next_cursor: null}})
    if (path.endsWith('/cards')) return route.fulfill({json: {cards: state === 'loaded' ? [card] : [], next_offset: null}})
    if (path.endsWith('/jobs/active')) return route.fulfill({json: state === 'running' ? {job_id: jobId, state} : null})
    if (route.request().method() === 'POST') {
      sent.push({path, body: route.request().postDataJSON()})
      if (failOnce) {failOnce = false;return route.abort('failed')}
      state = 'running'
      return route.fulfill({status: 202, json: {job_id: jobId, state}})
    }
    if (path.endsWith(`/jobs/${jobId}`)) return route.fulfill({json: state === 'loaded' ? result : {job_id: jobId, state}})
    return route.fulfill({status: 404, json: {error: {message: 'Unexpected API route'}}})
  })
  await page.goto(origin + '/#/files')
  await page.getByRole('link', {name: 'Тендерный радар ИИ', exact: true}).click()
  await page.getByRole('heading', {name: 'Тендерный радар ИИ', exact: true}).waitFor()
  await page.getByLabel('Складской CSV').selectOption(stockId)
  await page.getByLabel('Поисковая фраза').fill('болты М12')
  await page.getByRole('button', {name: 'Проанализировать следующий тендер'}).click()
  if (width === 1440) {
    await page.getByRole('button', {name: 'Повторить отправку'}).waitFor()
    await page.reload()
    await page.getByRole('button', {name: 'Повторить отправку'}).click()
    assert.deepEqual(sent[0], sent[1])
  }
  assert.equal(sent[0].path, '/api/v0.1/ai-tender-radar/jobs')
  assert.equal(sent[0].body.search_phrase, 'болты М12')
  assert.equal(sent[0].body.stock_artifact_id, stockId)
  await page.getByText('Получаем и анализируем тендер…', {exact: false}).waitFor()
  await page.reload()
  await page.getByText('Получаем и анализируем тендер…', {exact: false}).waitFor()
  state = 'loaded'
  await page.getByRole('heading', {name: tender.title}).waitFor()
  assert.equal(await page.getByText('100%', {exact: true}).count(), 1)
  assert.equal(await page.getByText('+60%', {exact: true}).count(), 1)
  await page.getByRole('button', {name: 'Открыть позиции (1)'}).click()
  await page.getByRole('heading', {name: 'Позиции закупки'}).waitFor()
  await page.locator('.ai-radar-position summary').click()
  await page.getByText('Диаметр резьбы · 12 → Болт М12', {exact: false}).waitFor()
  if (process.env.BERVEL_SCREENSHOTS) await page.screenshot({path: `${process.env.BERVEL_SCREENSHOTS}/ai-radar-${width}.png`, fullPage: true})
  await Promise.all([
    page.waitForRequest(req => req.method() === 'POST' && req.url().endsWith('/reanalyze')),
    page.getByRole('button', {name: 'Пересчитать с выбранным складом'}).click(),
  ])
  assert.equal(sent.at(-1).path, `/api/v0.1/ai-tender-radar/cards/${jobId}/reanalyze`)
  assert.equal(sent.at(-1).body.stock_artifact_id, stockId)
}))
