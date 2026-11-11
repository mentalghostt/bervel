const {test} = require('node:test')
const assert = require('node:assert/strict')
const {chromium} = require('playwright')

const base = process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5187'
const connection = {
  configured: true,
  limits: [
    {group: 'search', remaining: 200, maximum: 200},
    {group: 'purchase', remaining: 157, maximum: 200},
    {group: 'results', remaining: 40, maximum: 50},
  ],
}

for (const width of [1440, 390]) test('saves Contour key and shows separate live quotas at '+width, async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.BERVEL_BROWSER_PATH})
  try {
    const page = await browser.newPage({viewport: {width, height: 900}})
    const writes = []
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/v0.1/**', route => {
      const req = route.request()
      const path = new URL(req.url()).pathname
      if (path === '/api/v0.1/settings/kontur') {
        if (req.method() === 'PUT') writes.push(req.postDataJSON())
        return route.fulfill({json: writes.length ? connection : {configured: false, limits: []}})
      }
      if (path === '/api/v0.1/settings/model') return route.fulfill({json: {provider: 'mox', configured: true, api: {model: 'fixture'}, mox: {model: 'gpt-6.1-sol', credentials_available: true}}})
      if (path === '/api/v0.1/settings/company') return route.fulfill({json: {snapshot: null}})
      return route.fulfill({json: {artifacts: [], next_cursor: null}})
    })
    await page.goto(base+'/#/settings')
    const card = page.getByRole('region', {name: 'Контур.Закупки', exact: true})
    const policy = page.getByRole('region', {name: 'Настройки тендерного радара', exact: true})
    const key = card.getByLabel('API-ключ Контура', {exact: true})
    await key.fill('isolated-fake-key')
    assert.equal(await key.getAttribute('type'), 'password')
    await card.getByRole('button', {name: 'Сохранить ключ Контура'}).click()
    await card.getByRole('status').filter({hasText: 'Ключ сохранён.'}).waitFor()
    assert.deepEqual(writes, [{api_key: 'isolated-fake-key'}])
    assert.equal(await key.inputValue(), '')
    assert.deepEqual(await card.locator('dd').allTextContents(), ['200 / 200', '157 / 200', '40 / 50'])
    await card.getByText('Результаты закупок', {exact: true}).waitFor()
    await card.getByText('До 50 закупок на странице', {exact: true}).waitFor()
    const [settingsBox, quotaBox] = await Promise.all([policy.boundingBox(), card.boundingBox()])
    assert.ok(width > 1000 ? quotaBox.x > settingsBox.x && quotaBox.y === settingsBox.y : quotaBox.y > settingsBox.y)
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    assert.deepEqual(errors, [])
  } finally {
    await browser.close()
  }
})
