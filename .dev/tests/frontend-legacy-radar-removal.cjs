const {test} = require('node:test')
const assert = require('node:assert/strict')
const {chromium} = require('playwright')
const base = process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5173'
const profile = source => ({source, name: source, run_id: null, keyword_mode: 'auto', keywords: [],
  schedule: {enabled: false, every_days: 1, time: '09:00', timezone: 'Europe/Moscow'},
  available: true, last_refresh_at: null, next_refresh_at: null})

for (const width of [1440, 390]) test('retired links stay inactive and profile radar opens at ' + width + 'px', async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.BERVEL_BROWSER_PATH})
  try {
    const page = await browser.newPage({viewport: {width, height: 900}})
    page.setDefaultTimeout(5000)
    const requests = [], errors = [], profiles = ['test_api', 'kontur_api'].map(profile)
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/v0.1/**', route => {
      const request = route.request(), pathname = new URL(request.url()).pathname
      requests.push({path: pathname, method: request.method()})
      if (pathname.endsWith('/artifacts')) return route.fulfill({json: {artifacts: [], next_cursor: null}})
      if (pathname.endsWith('/profiles')) return route.fulfill({json: {profiles}})
      const selected = profiles.find(value => pathname.endsWith('/profiles/' + value.source))
      if (selected) return route.fulfill({json: {profile: selected, run: null}})
      return route.fulfill({status: 404, json: {error: {message: 'Unexpected API request'}}})
    })
    await page.goto(base + '/#/radar?run=retired')
    await page.getByRole('main', {name: 'Файлы', exact: true}).waitFor()
    assert.equal(await page.locator('a[href="#/radar"]').count(), 0)
    const radarLink = page.getByRole('link', {name: 'Радар ИИ', exact: true})
    assert.equal(await radarLink.getAttribute('href'), '#/ai-tender-radar')
    await radarLink.click()
    await page.getByRole('main', {name: 'Радар ИИ', exact: true}).waitFor()
    await page.getByRole('button', {name: 'Профили', exact: true}).waitFor()
    await page.getByText('База пока пуста. Обновите профиль для поиска тендеров.', {exact: true}).waitFor()
    assert.equal(await page.getByRole('complementary', {name: 'История прогонов'}).count(), 0)
    assert.equal(await page.getByRole('button', {name: 'Новый анализ', exact: true}).count(), 0)
    await page.reload()
    await page.getByRole('button', {name: 'Профили', exact: true}).waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    assert.ok(requests.some(request => request.path.endsWith('/profiles/test_api')))
    assert.equal(requests.some(request => request.path.startsWith('/api/v0.1/radar/')), false)
    assert.equal(requests.some(request => /\/(runs\/history|runs\/legacy|cards|jobs\/active)$/.test(request.path)), false)
    assert.equal(requests.some(request => request.method !== 'GET'), false)
    assert.deepEqual(errors, [])
  } finally {
    await browser.close()
  }
})
