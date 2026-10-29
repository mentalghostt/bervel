// Read-only verification against the running application. Never prints credentials.
const assert = require('node:assert/strict')
const { readFileSync, writeFileSync, mkdirSync } = require('node:fs')
const { resolve } = require('node:path')
const { chromium } = require('playwright')
const root = resolve(__dirname, '../..')
const output = resolve(root, '.dev/local/web-access')
const base = process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5173'
const line = readFileSync(resolve(root, '.env'), 'utf8').split(/\r?\n/).find(line => line.startsWith('BERVEL_WEB_ACCESS_KEY='))
assert.ok(line, 'Server web access must be configured')
const key = line.split('=', 2)[1].trim().replace(/^['"]|['"]$/g, '')
async function get(path) {
  const response = await fetch(base + '/api/v0.1' + path, { headers: { 'X-Bervel-Web': '1' } })
  assert.equal(response.status, 200)
  const text = await response.text()
  assert.equal(text.includes(key), false)
  return JSON.parse(text)
}
async function main() {
  mkdirSync(output, { recursive: true })
  const data = await get('/artifacts?limit=20')
  const selected = data.artifacts.find(file => file.dataset)
  assert.ok(selected, 'An existing processed file is required for this read-only check')
  const ref = selected.dataset
  const expected = await get(`/datasets/${ref.dataset_id}/revisions/${ref.revision}/records?limit=100`)
  const browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined })
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } })
    const errors = [], requests = [], contentChecks = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('request', request => {
      if (new URL(request.url()).pathname.startsWith('/api/')) requests.push(request)
    })
    page.on('response', response => {
      if (response.ok() && ['document', 'script'].includes(response.request().resourceType())) {
        contentChecks.push(response.text().then(text => !text.includes(key)).catch(() => false))
      }
    })
    await page.goto(base)
    assert.equal(await page.locator('input[type="password"]').count(), 0)
    await page.getByRole('link', { name: 'Загруженные файлы', exact: true }).click()
    await page.getByRole('button', { name: selected.filename, exact: true }).first().click()
    const table = page.getByRole('region', { name: 'Строки выбранного файла', exact: true })
    await table.waitFor()
    const rows = table.locator('tbody tr')
    assert.equal(await rows.count(), expected.records.length)
    for (let i = 0; i < expected.records.length; i++) {
      assert.equal(await rows.nth(i).locator('td').nth(1).innerText(), expected.records[i].payload.name)
      assert.equal(await rows.nth(i).locator('td').nth(2).innerText(), expected.records[i].payload.quantity.value ?? 'Не указано')
    }
    await page.screenshot({ path: resolve(output, 'main-files.png'), fullPage: true })
    await page.reload()
    await page.getByRole('button', { name: selected.filename, exact: true }).first().waitFor()
    assert.equal(await page.locator('input[type="password"]').count(), 0)
    assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0)
    assert.equal(requests.every(request => request.method() === 'GET' && !request.headers().authorization), true)
    assert.deepEqual(errors, [])
    assert.equal((await Promise.all(contentChecks)).every(Boolean), true)
    const report = { origin_verified: base, files_on_first_page: data.artifacts.length,
      rows_compared: expected.records.length, no_key_field: true, reload_without_key: true,
      no_browser_authorization: true, no_browser_storage: true, secret_absent_from_page_and_scripts: true,
      read_only: true, browser_errors: 0 }
    writeFileSync(resolve(output, 'main-read-check.json'), JSON.stringify(report, null, 2))
    console.log(JSON.stringify(report))
  } finally { await browser.close() }
}
main().catch(error => { console.error(error.message); process.exitCode = 1 })
