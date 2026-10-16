const assert = require('node:assert/strict')
const { before, after, beforeEach, afterEach, test } = require('node:test')
const { writeFileSync } = require('node:fs')
const { dirname, join } = require('node:path')
const { chromium } = require('playwright')

const baseUrl = process.env.BERVEL_FRONTEND_URL
const token = process.env.BERVEL_E2E_ACCESS_KEY
const reportPath = process.env.BERVEL_E2E_REPORT
if (!baseUrl || !token || !reportPath) throw new Error('Run this test through .dev/scripts/frontend_csv_verify.py')
const results = []
const file = { name: 'browser-live.csv', mimeType: 'application/vnd.ms-excel', buffer: Buffer.from('line,name,quantity,unit\n1,Bolt,10,pcs\n2,Nut,,pcs\n') }
let browser, context, page, errors

before(async () => { browser = await chromium.launch({ headless: true, channel: process.env.BERVEL_BROWSER_CHANNEL || 'msedge' }) })
after(async () => {
  writeFileSync(reportPath, JSON.stringify(results, null, 2))
  await browser?.close()
})
beforeEach(async () => {
  context = await browser.newContext()
  page = await context.newPage()
  page.setDefaultTimeout(8000)
  errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(baseUrl)
  await page.waitForLoadState('networkidle')
  await page.locator('input[type="file"]').setInputFiles(file)
  await page.getByLabel('Ключ доступа к API', { exact: true }).fill(token)
})
afterEach(async () => {
  try { assert.deepEqual(errors, []) } finally { await context.close() }
})
const responseFor = path => page.waitForResponse(response => new URL(response.url()).pathname.endsWith(path))
const success = () => page.getByRole('heading', { name: 'Сохранено в PostgreSQL', exact: true })

test('real browser uploads and processes CSV through Vite and FastAPI', async () => {
  const uploaded = responseFor('/artifacts')
  const extracted = responseFor('/extractions')
  await page.getByRole('button', { name: 'Загрузить в БД', exact: true }).click()
  const artifact = await (await uploaded).json()
  const dataset = await (await extracted).json()
  await success().waitFor()
  assert.equal(await page.getByText('2 строки', { exact: true }).isVisible(), true)
  assert.equal(await page.getByText('Есть пропуски', { exact: true }).isVisible(), true)
  assert.equal(await page.getByText(dataset.dataset_id, { exact: true }).isVisible(), true)
  await page.screenshot({ path: join(dirname(reportPath), 'live-success.png'), fullPage: true })
  results.push({ scenario: 'normal', artifact, dataset })
})

test('real committed result survives a lost HTTP response without duplicated database records', async () => {
  let committed, processingCalls = 0, uploadCalls = 0
  page.on('request', request => { if (new URL(request.url()).pathname.endsWith('/artifacts')) uploadCalls++ })
  await page.route('**/api/v0.1/demo/document-review/extractions', async route => {
    processingCalls++
    if (processingCalls === 1) {
      const response = await route.fetch()
      assert.equal(response.ok(), true)
      committed = await response.json()
      await route.abort('failed')
    } else await route.continue()
  })
  const uploaded = responseFor('/artifacts')
  await page.getByRole('button', { name: 'Загрузить в БД', exact: true }).click()
  const artifact = await (await uploaded).json()
  await page.getByRole('button', { name: 'Повторить', exact: true }).waitFor()
  const retried = responseFor('/extractions')
  await page.getByRole('button', { name: 'Повторить', exact: true }).click()
  const dataset = await (await retried).json()
  await success().waitFor()
  assert.deepEqual(dataset, committed)
  assert.equal(uploadCalls, 1)
  assert.equal(processingCalls, 2)
  results.push({ scenario: 'lost-processing-response', artifact, dataset, uploadCalls, processingCalls })
})

test('invalid CSV preserves its original but publishes no result', async () => {
  await page.locator('input[type="file"]').setInputFiles({ ...file, buffer: Buffer.from('wrong,columns\n1,2\n') })
  const uploaded = responseFor('/artifacts')
  await page.getByRole('button', { name: 'Загрузить в БД', exact: true }).click()
  const artifact = await (await uploaded).json()
  await page.getByRole('alert').waitFor()
  assert.match(await page.getByRole('alert').innerText(), /line,name,quantity,unit/)
  assert.equal(await success().count(), 0)
  results.push({ scenario: 'invalid-csv', artifact, dataset: null })
})
