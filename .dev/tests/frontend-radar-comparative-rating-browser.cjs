const {test} = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const {chromium} = require('playwright')
const rid = '11111111-1111-4111-8111-111111111111'
const sid = '22222222-2222-4222-8222-222222222222'
const jid = '33333333-3333-4333-8333-333333333333'
const now = '2026-11-08T10:00:00Z'
const metrics = {price: '540856.77', currency: 'RUB', stage: 'accepting', catalog_match_percent: null,
  stock_coverage_percent: '5.7543', stock_coverage_incomplete: false}
function card(id, rating) {
  return {analysis_id: id, candidate_id: id, state: 'needs_data', evaluated_at: now,
    tender: {title: 'Тендер ' + id, number: id, link: null}, analysis: null, review: null, assessment: null,
    fulfillment: {status: 'infeasible', reasons: ['CAPACITY_INSUFFICIENT'], production_allocations: []},
    opportunity: {score: 99, basis: 'confirmed', known_share: '0.03', margin_percent: '77.11'}, metrics, rating}
}

for (const width of [1440, 390]) test('Comparative ratings, unknown status, filters and manual command ' + width, async () => {
  const browser = await chromium.launch({headless: true})
  try {
    const page = await browser.newPage({viewport: {width, height: 1000}})
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    let pending = false, posts = 0
    await page.route('**/api/v0.1/**', route => {
      const pathname = new URL(route.request().url()).pathname
      if (pathname.endsWith('/settings/company')) return route.fulfill({json: {snapshot: null}})
      if (pathname.endsWith('/runs/history')) return route.fulfill({json: {entries: [
        {id: rid, kind: 'run', title: 'Сравнительная оценка', created_at: now, state: 'complete', card_count: 3}], next_offset: null}})
      if (pathname.endsWith('/rating')) {
        assert.equal(route.request().method(), 'POST')
        assert.ok(route.request().postDataJSON().command_key)
        posts++
        pending = false
        return route.fulfill({status: 202, json: {job_id: jid, state: 'queued'}})
      }
      if (pathname.endsWith('/jobs/' + jid)) return route.fulfill({json: {job_id: jid, state: 'loaded'}})
      if (pathname.endsWith('/runs/' + rid)) return route.fulfill({json: {run_id: rid,
        settings_snapshot_id: sid, created_at: now, data: {state: 'complete', phase: 'complete'},
        cards: pending ? [card('pending', {status: 'pending', score: null})] : [
          card('unknown', {status: 'unknown', score: null}), card('zero', {status: 'complete', score: 0}),
          card('leader', {status: 'complete', score: 92})], reports: [], candidates: []}})
      return route.fulfill({status: 404, json: {error: {message: 'Unexpected fixture request ' + pathname}}})
    })
    const url = (process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5193') + '/#/ai-tender-radar?run_id=' + rid
    await page.goto(url)
    const cards = page.locator('.ai-radar-card-list > article')
    await cards.first().waitFor()
    const score = card => card.locator('dt').filter({hasText: 'Оценка ИИ'}).locator('..').locator('dd')
    assert.deepEqual(await cards.locator('h3').allTextContents(), ['Тендер leader', 'Тендер zero', 'Тендер unknown'])
    assert.equal(await score(cards.nth(0)).innerText(), '92')
    assert.equal(await score(cards.nth(1)).innerText(), '0')
    assert.equal(await score(cards.nth(2)).innerText(), 'Не получена')
    assert.match(await score(cards.nth(0)).getAttribute('title'), /Не является решением об участии/)
    await page.getByRole('button', {name: 'Фильтр', exact: true}).click()
    const limit = page.getByRole('slider', {name: 'Оценка ИИ до', exact: true})
    await limit.fill('0')
    await page.getByRole('button', {name: 'Готово', exact: true}).click()
    assert.equal(await cards.count(), 1)
    assert.equal(await cards.first().locator('h3').innerText(), 'Тендер zero')
    assert.equal(posts, 0)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    await page.screenshot({path: path.resolve('.dev/local/radar-three-fixes-2026-11-08/screenshots/task-2-rating-' + width + '.png'), fullPage: true})
    pending = true
    await page.reload()
    await page.getByText('Ожидается', {exact: true}).waitFor()
    await page.getByRole('button', {name: 'Оценить ИИ', exact: true}).waitFor({timeout: 2000})
    await page.getByRole('button', {name: 'Оценить ИИ', exact: true}).click()
    await score(cards.first()).filter({hasText: '92'}).waitFor()
    assert.equal(posts, 1)
    assert.deepEqual(errors, [])
  } finally {await browser.close()}
})
