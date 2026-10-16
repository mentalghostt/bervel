const assert = require('node:assert/strict')
const { before, after, beforeEach, afterEach, test } = require('node:test')
const { mkdirSync } = require('node:fs')
const { resolve } = require('node:path')
const { chromium } = require('playwright')

const baseUrl = process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5174'
const token = 'test-only-key-not-valid-for-real-api'
const artifact = { artifact_id: '11111111-1111-4111-8111-111111111111', revision: 1 }
const dataset = { dataset_id: '22222222-2222-4222-8222-222222222222', revision: 1 }
const summary = { ...dataset, record_count: 2, quality_state: 'PARTIAL' }
const screenshotRoot = resolve(__dirname, '../local/frontend/screenshots')
const csv = { name: 'positions.csv', mimeType: 'application/vnd.ms-excel', buffer: Buffer.from('line,name,quantity,unit\n1,Bolt,10,pcs\n2,Nut,,pcs\n') }
let browser, context, page, requests, pageErrors, consoleErrors, expectedNetworkError

before(async () => {
  mkdirSync(screenshotRoot, { recursive: true })
  browser = await chromium.launch({ headless: true, channel: process.env.BERVEL_BROWSER_CHANNEL || 'msedge' })
})
after(async () => { await browser?.close() })
beforeEach(async () => {
  context = await browser.newContext({ viewport: { width: 1440, height: 1100 } })
  page = await context.newPage()
  page.setDefaultTimeout(3000)
  requests = []
  pageErrors = []
  consoleErrors = []
  expectedNetworkError = false
  page.on('pageerror', error => pageErrors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  await page.route('**/api/v0.1/**', async route => {
    const request = route.request()
    requests.push(request)
    const path = new URL(request.url()).pathname
    const data = path.endsWith('/artifacts') ? artifact
      : path.endsWith('/extractions') ? dataset : summary
    await route.fulfill({ json: data })
  })
  await page.goto(baseUrl)
  await page.waitForLoadState('networkidle')
})
afterEach(async () => {
  try {
    assert.deepEqual(pageErrors, [])
    const unexpected = expectedNetworkError
      ? consoleErrors.filter(message => !message.startsWith('Failed to load resource:'))
      : consoleErrors
    assert.deepEqual(unexpected, [])
  } finally {
    await context.close()
  }
})

async function prepare() {
  await page.locator('input[type="file"]').setInputFiles(csv)
  await page.getByLabel('Ключ доступа к API', { exact: true }).fill(token)
}
const uploadButton = () => page.getByRole('button', { name: 'Загрузить в БД', exact: true })
const retryButton = () => page.getByRole('button', { name: 'Повторить', exact: true })
const success = () => page.getByRole('heading', { name: 'Сохранено в PostgreSQL', exact: true })

test('waits for the file, key and button; sends multipart CSV then processes and reads the saved result', async () => {
  assert.equal(await uploadButton().isDisabled(), true)
  await page.locator('input[type="file"]').setInputFiles(csv)
  assert.equal(await uploadButton().isDisabled(), true)
  await page.getByLabel('Ключ доступа к API', { exact: true }).fill(token)
  assert.equal(await uploadButton().isEnabled(), true)
  assert.equal(requests.length, 0)
  await uploadButton().click()
  await success().waitFor()
  assert.deepEqual(requests.map(request => [request.method(), new URL(request.url()).pathname]), [
    ['POST', '/api/v0.1/artifacts'],
    ['POST', '/api/v0.1/demo/document-review/extractions'],
    ['GET', `/api/v0.1/datasets/${dataset.dataset_id}/revisions/1`],
  ])
  assert.match(requests[0].headers()['content-type'], /multipart\/form-data; boundary=/)
  assert.match(requests[0].postDataBuffer().toString(), /Content-Type: text\/csv/i)
  for (const request of requests) assert.equal(request.headers().authorization === `Bearer ${token}`, true)
  assert.equal(requests[1].postDataJSON().artifact_id, artifact.artifact_id)
  assert.equal(await page.getByText('2 строки', { exact: true }).isVisible(), true)
  assert.equal(await page.getByText('Есть пропуски', { exact: true }).isVisible(), true)
  assert.equal(await page.getByText(dataset.dataset_id, { exact: true }).isVisible(), true)
  assert.equal(await page.getByRole('button', { name: 'Сохранено', exact: true }).isDisabled(), true)
  assert.equal(await page.evaluate(key => JSON.stringify([localStorage, sessionStorage]).includes(key), token), false)
  await page.screenshot({ path: resolve(screenshotRoot, 'csv-upload-success.png'), fullPage: true })
})

test('blocks repeated clicks and editing while upload is running', async () => {
  let release
  const gate = new Promise(resolve => { release = resolve })
  await page.route('**/api/v0.1/artifacts', async route => {
    requests.push(route.request())
    await gate
    await route.fulfill({ json: artifact })
  })
  await prepare()
  await uploadButton().evaluate(button => { button.click(); button.click() })
  await page.getByText('Загружаем файл…', { exact: true }).waitFor()
  try {
    assert.equal(requests.length, 1)
    assert.equal(await page.getByLabel('Ключ доступа к API', { exact: true }).isDisabled(), true)
    assert.equal(await page.getByRole('button', { name: 'Заменить файл', exact: true }).isDisabled(), true)
    assert.equal(await page.getByRole('button', { name: 'Убрать выбранный файл' }).isDisabled(), true)
  } finally {
    release()
  }
  await success().waitFor()
})

test('lost upload response retries with the same command key', async () => {
  expectedNetworkError = true
  let calls = 0
  await page.route('**/api/v0.1/artifacts', async route => {
    requests.push(route.request())
    if (++calls === 1) await route.abort('failed')
    else await route.fulfill({ json: artifact })
  })
  await prepare()
  await uploadButton().click()
  await retryButton().waitFor()
  await retryButton().click()
  await success().waitFor()
  const uploads = requests.filter(request => new URL(request.url()).pathname.endsWith('/artifacts'))
  const key = request => request.postDataBuffer().toString().match(/name="command_key"\r\n\r\n([^\r]+)/)[1]
  assert.equal(uploads.length, 2)
  assert.equal(key(uploads[0]), key(uploads[1]))
})

test('lost processing response retries processing only, preserving both original and command key', async () => {
  expectedNetworkError = true
  let calls = 0
  await page.route('**/api/v0.1/demo/document-review/extractions', async route => {
    requests.push(route.request())
    if (++calls === 1) await route.abort('failed')
    else await route.fulfill({ json: dataset })
  })
  await prepare()
  await uploadButton().click()
  await retryButton().waitFor()
  await retryButton().click()
  await success().waitFor()
  const extractions = requests.filter(request => new URL(request.url()).pathname.endsWith('/extractions'))
  assert.equal(requests.filter(request => new URL(request.url()).pathname.endsWith('/artifacts')).length, 1)
  assert.deepEqual(extractions[0].postDataJSON(), extractions[1].postDataJSON())
})

test('a failed summary read retries GET only and explains that the result was already saved', async () => {
  expectedNetworkError = true
  let calls = 0
  await page.route('**/api/v0.1/datasets/**', async route => {
    requests.push(route.request())
    await route.fulfill(++calls === 1
      ? { status: 503, json: { error: { code: 'TRANSIENT_DEPENDENCY_ERROR', message: 'Database is unavailable' } } }
      : { json: summary })
  })
  await prepare()
  await uploadButton().click()
  await page.getByText('Результат уже записан. Повторите получение сводки.', { exact: true }).waitFor()
  await retryButton().click()
  await success().waitFor()
  assert.equal(requests.filter(request => request.method() === 'POST').length, 2)
  assert.equal(requests.filter(request => request.method() === 'GET').length, 2)
})

test('server CSV validation is shown in Russian and does not produce a success state', async () => {
  expectedNetworkError = true
  await page.route('**/api/v0.1/demo/document-review/extractions', route => route.fulfill({
    status: 422, json: { error: { code: 'VALIDATION_ERROR', message: 'Expected columns: line,name,quantity,unit' } },
  }))
  await prepare()
  await uploadButton().click()
  await page.getByRole('alert').waitFor()
  assert.match(await page.getByRole('alert').innerText(), /line,name,quantity,unit/)
  assert.equal(await success().count(), 0)
  assert.equal(await retryButton().isEnabled(), true)
  await page.screenshot({ path: resolve(screenshotRoot, 'csv-upload-error.png'), fullPage: true })
})

test('invalid key is explained and changing the key starts a fresh attempt', async () => {
  expectedNetworkError = true
  await page.route('**/api/v0.1/artifacts', async route => {
    requests.push(route.request())
    if (route.request().headers().authorization === 'Bearer wrong') {
      await route.fulfill({ status: 401, json: { error: { code: 'UNAUTHORIZED', message: 'A valid Bearer key is required' } } })
    } else await route.fulfill({ json: artifact })
  })
  await prepare()
  await page.getByLabel('Ключ доступа к API', { exact: true }).fill('wrong')
  await uploadButton().click()
  await page.getByRole('alert').waitFor()
  assert.match(await page.getByRole('alert').innerText(), /Ключ доступа не принят/)
  await page.getByLabel('Ключ доступа к API', { exact: true }).fill(token)
  await uploadButton().click()
  await success().waitFor()
})

test('unexpected HTML response is handled; mobile success view fits the screen and keys are not retained on reload', async () => {
  await page.route('**/api/v0.1/artifacts', route => route.fulfill({ contentType: 'text/html', body: '<html>Wrong API address</html>' }))
  await prepare()
  await uploadButton().click()
  await page.getByRole('alert').waitFor()
  assert.match(await page.getByRole('alert').innerText(), /неожиданный ответ/)
  await page.unroute('**/api/v0.1/artifacts')
  await retryButton().click()
  await success().waitFor()
  await page.setViewportSize({ width: 375, height: 812 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
  await page.screenshot({ path: resolve(screenshotRoot, 'csv-upload-success-mobile.png'), fullPage: true })
  await page.reload()
  assert.equal(await page.getByLabel('Ключ доступа к API', { exact: true }).inputValue(), '')
})
