const { test, before, after } = require('node:test')
const assert = require('node:assert/strict')
const { chromium } = require('playwright')
const { resolve } = require('node:path')
const { mkdirSync } = require('node:fs')
const base = process.env.BERVEL_FRONTEND_URL || 'http://host.docker.internal:5173'
let browser
before(async () => { mkdirSync(resolve(__dirname, '../local/workspace-polish-2026-10-28/frontend/legacy-chat'), { recursive: true }); browser = await chromium.launch({ headless: true }) })
after(async () => { await browser?.close() })
test('settings default to MOX, save a write-only API key and show a certificate error without retry', async () => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const page = await context.newPage()
  page.setDefaultTimeout(4500)
  let tests = 0, saves = []
  let value = { provider: 'mox', api: { base_url: 'https://api.openai.com/v1', model: 'api-model', api_key_configured: false },
    mox: { base_url: 'https://host.docker.internal:8000/backend-api', model: 'mox-model', credentials_available: true }, configured: true }
  await page.route('**/api/v0.1/settings/model**', async route => {
    const request = route.request()
    assert.equal(request.headers()['x-bervel-web'], '1')
    assert.equal(request.headers().authorization, undefined)
    if (request.url().endsWith('/test')) {
      tests++
      return route.fulfill({ status: 502, json: { error: { code: 'MODEL_TLS_ERROR', message: 'private-debug' } } })
    }
    if (request.method() === 'PUT') {
      const body = request.postDataJSON(); saves.push(body)
      value = { ...value, provider: body.provider }
      if (body.provider === 'api_key') value.api = { base_url: body.base_url, model: body.model, api_key_configured: true }
    }
    return route.fulfill({ json: value })
  })
  try {
    await page.route('**/voice/settings',route=>route.fulfill({json:{configured:false,state:'unconfigured',model:'fixture'}}))
    await page.goto(base)
    await page.getByRole('link', { name: 'Настройки', exact: true }).click()
    await page.getByRole('heading', { name: 'Настройки', exact: true }).waitFor()
    assert.equal(await page.getByRole('radio', { name: 'MOX Access', exact: true }).isChecked(), true)
    assert.equal(await page.getByLabel('API-ключ провайдера', { exact: true }).count(), 0)
    await page.getByRole('button', { name: 'Проверить подключение', exact: true }).click()
    await page.getByRole('alert').filter({ hasText: 'сертификат' }).waitFor()
    assert.equal(tests, 1)
    assert.equal(await page.getByText('private-debug').count(), 0)
    await page.getByRole('radio', { name: 'API-ключ', exact: true }).check()
    await page.getByLabel('API-ключ провайдера', { exact: true }).fill('fixture-private-browser-key')
    await page.getByRole('button', { name: 'Сохранить', exact: true }).click()
    await page.getByRole('status').filter({ hasText: 'сохранены' }).waitFor()
    assert.equal(saves.length, 1)
    assert.equal(saves[0].api_key, 'fixture-private-browser-key')
    assert.equal(await page.getByLabel('API-ключ провайдера', { exact: true }).inputValue(), '')
    const storage = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))
    assert.equal(storage.includes('fixture-private-browser-key'), false)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    await page.screenshot({ path: resolve(__dirname, '../local/workspace-polish-2026-10-28/frontend/legacy-chat/settings-mobile.png'), fullPage: true })
    await page.reload()
    await page.getByRole('heading', { name: 'Настройки', exact: true }).waitFor()
    assert.equal(await page.getByRole('radio', { name: 'API-ключ', exact: true }).isChecked(), true)
    assert.equal(await page.getByLabel('API-ключ провайдера', { exact: true }).inputValue(), '')
    assert.equal(tests, 1)
  } finally { await context.close() }
})
