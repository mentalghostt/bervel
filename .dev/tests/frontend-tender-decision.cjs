const {test} = require('node:test')
const assert = require('node:assert/strict')
const {chromium} = require('playwright')
const fs = require('node:fs')
const path = require('node:path')

const rid = '11111111-1111-4111-8111-111111111111'
const sid = '22222222-2222-4222-8222-222222222222'
const source = 'https://example.com/tender/123'
const time = '2026-11-06T10:00:00Z'
const card = {
  analysis_id: 'analysis-1', candidate_id: 'tender-123', state: 'needs_data', evaluated_at: time,
  tender: {title: 'Поставка болтов', number: '123', link: source},
  analysis: {coverage_percent: '0.27', benefit_percent: '40',
    economics: {scope: 'confirmed', revenue: '690', cost: '414', profit: '276', margin_percent: '40', currency: 'RUB'},
    positions: [{id: 'p1', lot: 1, name: 'Болт М27×100', unit: 'шт', required: '25680', covered: '69', shortage: '25611',
      stock_status: 'partial', unit_prices: {stock_cost: '6', tender_price: '10', currency: 'RUB', unit: 'шт'}}]},
  fulfillment: {status: 'needs_review', reasons: ['PRODUCTION_UNCONFIRMED:p1'], production_allocations: []},
  assessment: {score: null, priority: 'needs_review', E: null, S: null, P: null, reasons: [], full_economics: null},
  rating: {status: 'complete', score: 92},
  opportunity: {version: 1, score: 1, basis: 'confirmed', known_share: '0.0027', margin_percent: '40'},
  projection: {version: 1, review_stale: true},
  report_summary: {decision: 'review', paragraphs: [
    'Подтверждено соответствие болта М27×100. На складе 69 шт.; дополнительно требуется 25 611 шт.',
    'Прибыль обеспеченной части составляет 276 ₽. Для полного заказа нужны стоимость и срок изготовления дефицита.',
  ]},
  review: {state: 'complete', decision: 'review', recommendation: 'Соответствие найденных товаров требованиям не подтверждено.',
    risks: [], assumptions: [], evidence_refs: ['assessment'], manual_checks: ['Проверить соответствие болта.']},
}

for (const width of [1440, 390]) test(`Tender prices, numeric score and coherent report ${width}`, async () => {
  const browser = await chromium.launch({headless: true, executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe'})
  try {
    const page = await browser.newPage({viewport: {width, height: 1000}})
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/v0.1/**', route => {
      const url = new URL(route.request().url())
      if (url.pathname.endsWith('/settings/company')) return route.fulfill({json: {snapshot: null}})
      if (url.pathname.endsWith('/runs/history')) return route.fulfill({json: {entries: [{id: rid, kind: 'run', title: 'Проверка', state: 'complete', created_at: time, card_count: 1}], next_offset: null}})
      if (url.pathname.endsWith('/runs/' + rid)) return route.fulfill({json: {run_id: rid, settings_snapshot_id: sid, created_at: time,
        data: {state: 'complete', phase: 'complete'}, cards: [card], candidates: [], reports: []}})
      return route.fulfill({status: 404, json: {error: {message: 'Unexpected request'}}})
    })
    await page.goto((process.env.BERVEL_FRONTEND_URL || 'http://localhost:5188') + '/#/ai-tender-radar?run_id=' + rid + '&analysis_id=analysis-1')
    const dialog = page.getByRole('dialog')
    await dialog.locator('.ai-radar-position').waitFor()
    assert.equal(await dialog.getByRole('link', {name: 'Поставка болтов', exact: true}).getAttribute('href'), source)
    assert.equal(await dialog.getByRole('link', {name: 'Болт М27×100', exact: true}).getAttribute('href'), source)
    assert.equal(await dialog.getByRole('link', {name: /Открыть источник/}).count(), 0)
    const position = dialog.locator('.ai-radar-position summary')
    const text = await position.innerText()
    assert.doesNotMatch(text, /Лот 1|Позиция 1|Остаток/)
    assert.match(text, /Цена/)
    assert.match(text, /6\s*\/\s*10\s*₽/)
    assert.equal(await dialog.locator('.run-metrics dt').filter({hasText: 'Оценка ИИ'}).locator('..').locator('dd').innerText(), '92')
    const report = await dialog.locator('.run-review').innerText()
    assert.match(report, /25\s611/)
    assert.doesNotMatch(report, /Соответствие найденных товаров требованиям не подтверждено|Проверить вручную|До 2000 символов/)
    assert.equal(await dialog.locator('.run-review .ai-radar-calculation').count(), 0)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    assert.deepEqual(errors, [])
    const output = path.resolve('.dev/local/tender-decision-2026-11-06')
    fs.mkdirSync(output, {recursive: true})
    await page.screenshot({path: path.join(output, `fixture-${width}.png`), fullPage: true})
  } finally {await browser.close()}
})

const realFixturePath = path.resolve('.dev/local/tender-decision-2026-11-06/after-run-fixture.json')
for (const width of [1440, 390]) test(`Saved tender with 15 confirmed positions ${width}`, {skip: !fs.existsSync(realFixturePath)}, async () => {
  const run = JSON.parse(fs.readFileSync(realFixturePath, 'utf8'))
  const actual = run.cards.find(card => card.analysis_id === 'cb18bd26-34aa-4f3f-9586-5f54fe831189')
  const browser = await chromium.launch({headless: true, executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe'})
  try {
    const page = await browser.newPage({viewport: {width, height: 1000}})
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/v0.1/**', route => {
      const url = new URL(route.request().url())
      if (url.pathname.endsWith('/settings/company')) return route.fulfill({json: {snapshot: null}})
      if (url.pathname.endsWith('/runs/history')) return route.fulfill({json: {entries: [{id: run.run_id, kind: 'run', title: 'Проверка', state: 'complete', created_at: run.created_at, card_count: 10}], next_offset: null}})
      if (url.pathname.endsWith('/runs/' + run.run_id)) return route.fulfill({json: run})
      return route.fulfill({status: 404, json: {error: {message: 'Unexpected request'}}})
    })
    await page.goto((process.env.BERVEL_FRONTEND_URL || 'http://localhost:5188') + '/#/ai-tender-radar?run_id=' + run.run_id + '&analysis_id=' + actual.analysis_id)
    const dialog = page.getByRole('dialog')
    await dialog.locator('.ai-radar-position').first().waitFor()
    assert.equal(await dialog.locator('.ai-radar-position').count(), 15)
    const position = dialog.locator('.ai-radar-position summary').first()
    assert.match(await position.innerText(), /69\s*шт\s*\/\s*25\s680\s*шт/)
    assert.match(await position.innerText(), /1\s*\/\s*9\s*₽/)
    assert.equal(await dialog.locator('h3 a').getAttribute('href'), actual.tender.link)
    assert.equal(await dialog.locator('.run-metrics dt').filter({hasText: 'Оценка ИИ'}).locator('..').locator('dd').innerText(), 'Ожидается')
    const report = await dialog.locator('.run-review').innerText()
    assert.match(report, /мощност/)
    assert.doesNotMatch(report, /соответствие найденных товаров требованиям не подтверждено|Прибыль.*недостаточн/)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    await page.screenshot({path: path.resolve(`.dev/local/tender-decision-2026-11-06/real-${width}.png`)})
    await position.scrollIntoViewIfNeeded()
    await page.screenshot({path: path.resolve(`.dev/local/tender-decision-2026-11-06/real-positions-${width}.png`)})
    assert.deepEqual(errors, [])
  } finally {await browser.close()}
})
