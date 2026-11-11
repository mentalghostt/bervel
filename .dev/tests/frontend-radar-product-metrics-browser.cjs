const {test} = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const {chromium} = require('playwright')
const rid = '11111111-1111-4111-8111-111111111111'
const now = '2026-11-08T10:00:00Z'
const product = {price: '50000', currency: 'RUB', stage: 'accepting', catalog_match_percent: '50',
  catalog_match_confirmed: 1, catalog_match_unknown: 1, catalog_match_total: 2, catalog_match_incomplete: true}
const base = {analysis_id: 'a', candidate_id: 'c', state: 'needs_data', evaluated_at: now,
  tender: {title: 'Поставка болтов', number: '123', link: null},
  analysis: {coverage_percent: null, benefit_percent: null, economics: {scope: null, revenue: null, cost: null},
    positions: [{id: 'p', name: 'Болт М12', required: '10', covered: '0', shortage: null, unit: 'шт'}]},
  fulfillment: null, assessment: null, review: null, metrics: product}

for (const width of [1440, 390]) test('Technical product metric and uncertainty explanation ' + width, async () => {
  const browser = await chromium.launch({headless: true})
  try {
    const page = await browser.newPage({viewport: {width, height: 1000}})
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/v0.1/**', route => {
      const pathname = new URL(route.request().url()).pathname
      if (pathname.endsWith('/settings/company')) return route.fulfill({json: {snapshot: null}})
      if (pathname.endsWith('/runs/history')) return route.fulfill({json: {entries: [
        {id: rid, kind: 'run', title: 'Проверка продукции', created_at: now, state: 'complete', card_count: 1}], next_offset: null}})
      if (pathname.endsWith('/runs/' + rid)) return route.fulfill({json: {run_id: rid,
        settings_snapshot_id: '22222222-2222-4222-8222-222222222222', created_at: now,
        data: {state: 'complete', phase: 'complete'}, cards: [base], reports: [], candidates: []}})
      return route.fulfill({status: 404, json: {error: {message: 'Unexpected fixture request'}}})
    })
    await page.goto((process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5193') + '/#/ai-tender-radar?run_id=' + rid)
    const value = page.locator('.ai-radar-card-list .ai-radar-metrics dt').filter({hasText: 'Соответствие продукции'}).locator('..').locator('dd')
    await value.waitFor()
    assert.equal(await value.innerText(), '50% *')
    assert.equal(await value.getAttribute('title'), 'Подтверждено 1 из 2 раскрытых позиций; часть данных требует уточнения.')
    assert.equal(await value.getAttribute('aria-label'), await value.getAttribute('title'))
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    assert.deepEqual(errors, [])
    await page.screenshot({path: path.resolve('.dev/local/radar-three-fixes-2026-11-08/screenshots/task-1-product-' + width + '.png'), fullPage: true})
  } finally {await browser.close()}
})
