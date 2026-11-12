const { test, before, after } = require('node:test')
const assert = require('node:assert/strict')
const { chromium } = require('playwright')
const base = process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5173'
let browser
before(async () => { browser = await chromium.launch({executablePath:process.env.BERVEL_BROWSER_PATH, headless: true, channel: process.env.BERVEL_BROWSER_CHANNEL }) })
after(async () => { await browser?.close() })
async function open(provider = 'mox', model = 'gpt-6.1-sol') {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
  page.setDefaultTimeout(3500)
  const state = { checks: 0, saves: [], failSave: false, voiceSaves: [] }
  let value = { provider, api: { base_url: 'https://api.example.test/v1', model: 'api-model', api_key_configured: true },
    mox: { base_url: 'https://mox.example.test/backend-api', model, credentials_available: true }, configured: true }
  await page.route('**/api/v0.1/**', route => route.fulfill({ json: { snapshot: null } }))
  await page.route('**/voice/settings', route => {
    if (route.request().method() === 'PUT') state.voiceSaves.push(route.request().postDataJSON())
    return route.fulfill({ json: { configured: true, state: 'configured', model: 'whisper-large-v3-turbo' } })
  })
  await page.route('**/api/v0.1/settings/model**', async route => {
    const request = route.request()
    assert.equal(request.headers()['x-bervel-web'], '1')
    assert.equal(request.headers().authorization, undefined)
    if (request.url().endsWith('/test')) {
      state.checks++
      return route.fulfill({ status: 502, json: { error: { code: 'MODEL_TLS_ERROR', message: 'private-debug' } } })
    }
    if (request.method() === 'PUT') {
      const body = request.postDataJSON()
      state.saves.push(body)
      if (state.failSave) return route.fulfill({ status: 503, json: { error: { code: 'SETTINGS_UNAVAILABLE' } } })
      value = { ...value, provider: body.provider, mox: { ...value.mox, model: body.model } }
    }
    return route.fulfill({ json: value })
  })
  await page.goto(base + '/#/settings')
  return { page, state }
}

test('gateway saves a supported model on selection and tests only on request', async () => {
  const { page, state } = await open()
  try {
    const select = page.getByRole('combobox', { name: 'Модель', exact: true })
    await select.waitFor()
    assert.equal(await select.inputValue(), 'gpt-6.1-sol')
    assert.ok(await select.locator('option:not([disabled])').count() > 1)
    const gateway = page.getByRole('group', { name: 'Шлюз', exact: true })
    assert.equal(await gateway.getByRole('radio').count(), 0)
    assert.equal(await gateway.getByRole('button', { name: 'Сохранить', exact: true }).count(), 0)
    assert.doesNotMatch(await gateway.innerText(), /Адрес шлюза|Файлы доступа MOX|Проверка отправляет один запрос|API-ключ|mox\.example/)
    const saved = page.waitForResponse(response => response.url().endsWith('/settings/model') && response.request().method() === 'PUT')
    await select.selectOption('gpt-6-astra')
    assert.equal((await saved).ok(), true)
    assert.deepEqual(state.saves, [{ provider: 'mox', model: 'gpt-6-astra' }])
    assert.equal(state.checks, 0)
    await page.reload()
    assert.equal(await select.inputValue(), 'gpt-6-astra')
    await gateway.getByRole('button', { name: 'Проверить подключение', exact: true }).click()
    await page.getByRole('alert').filter({ hasText: 'сертификат' }).waitFor()
    assert.equal(state.checks, 1)
    assert.equal(await page.getByText('private-debug').count(), 0)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
  } finally { await page.close() }
})

test('gateway preserves a previous model and active provider until a selection succeeds', async () => {
  const { page, state } = await open('api_key', 'previous-mox-model')
  try {
    const select = page.getByRole('combobox', { name: 'Модель', exact: true })
    await select.waitFor()
    assert.match(await select.locator('option:checked').textContent(), /api-model/)
    assert.notEqual(await select.locator('option:checked').getAttribute('disabled'), null)
    assert.deepEqual(state.saves, [])
    state.failSave = true
    await select.selectOption('gpt-6.1-sol')
    await page.getByRole('alert').filter({ hasText: 'Хранилище настроек недоступно' }).waitFor()
    assert.match(await select.locator('option:checked').textContent(), /api-model/)
    state.failSave = false
    const saved = page.waitForResponse(response => response.url().endsWith('/settings/model') && response.request().method() === 'PUT')
    await select.selectOption('gpt-6.1-sol')
    assert.equal((await saved).ok(), true)
    await page.reload()
    assert.equal(await select.inputValue(), 'gpt-6.1-sol')
  } finally { await page.close() }
})

test('Groq keeps the visible key after saving and starts empty on reload', async () => {
  const { page, state } = await open()
  try {
    const voice = page.getByRole('region', { name: 'Настройки Groq', exact: true })
    const input = voice.getByRole('textbox', { name: 'API-ключ Groq', exact: true })
    const secret = 'fixture-voice-key-not-a-real-secret'
    await input.fill(secret)
    assert.equal(await input.getAttribute('type'), 'text')
    assert.deepEqual(state.voiceSaves, [])
    assert.doesNotMatch(await voice.innerText(), /Распознавание речи для|Голос подключён|Ключ Groq не добавлен|Ключ хранится на сервере/)
    const button = voice.getByRole('button', { name: 'Сохранить ключ Groq', exact: true })
    const saved = page.waitForResponse(response => response.url().endsWith('/voice/settings') && response.request().method() === 'PUT')
    await button.click()
    assert.equal((await saved).ok(), true)
    await input.waitFor()
    await page.waitForFunction(() => !document.querySelector('.voice-settings input')?.disabled)
    assert.equal(await input.inputValue(), secret)
    assert.deepEqual(state.voiceSaves, [{ api_key: secret }])
    assert.equal(await page.evaluate(value => JSON.stringify({ ...localStorage, ...sessionStorage }).includes(value), secret), false)
    await page.reload()
    assert.equal(await input.inputValue(), '')
  } finally { await page.close() }
})
