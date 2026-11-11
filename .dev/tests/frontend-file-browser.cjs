const assert = require('node:assert/strict')
const { before, after, beforeEach, afterEach, test } = require('node:test')
const { mkdirSync } = require('node:fs')
const { resolve } = require('node:path')
const { chromium } = require('playwright')

const baseUrl = process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5173'
const sourceId = '11111111-1111-4111-8111-111111111111'
const resultId = '22222222-2222-4222-8222-222222222222'
const otherId = '33333333-3333-4333-8333-333333333333'
const otherResult = '44444444-4444-4444-8444-444444444444'
const files = [
  { artifact_id: sourceId, filename: 'Позиции завода.csv', media_type: 'text/csv', size: 120, created_at: '2026-10-19T10:00:00Z', dataset: { dataset_id: resultId, revision: 1, record_count: 3, quality_state: 'PARTIAL' } },
  { artifact_id: otherId, filename: 'Новая редакция.csv', media_type: 'text/csv', size: 90, created_at: '2026-10-18T10:00:00Z', dataset: null },
  { artifact_id: '55555555-5555-4555-8555-555555555555', filename: '<img src=x onerror=alert(1)>.csv', media_type: 'text/csv', size: 90, created_at: '2026-10-17T10:00:00Z', dataset: { dataset_id: otherResult, revision: 1, record_count: 1, quality_state: 'COMPLETE' } },
]
function row(name, quantity, line) {
  return { record_id: 'row-' + line, quality_state: quantity === null ? 'PARTIAL' : 'COMPLETE',
    payload: { name, quantity: { state: quantity === null ? 'UNKNOWN' : 'KNOWN', value: quantity, unit: 'piece' } },
    evidence_refs: [{ field_path: '/name', locator: { kind: 'csv_cell', row: line, column: 'name' } }],
    issues: quantity === null ? [{ code: 'MISSING_QUANTITY', field_path: '/quantity/value', message: 'Quantity is absent in source' }] : [] }
}
let browser, context, page, requests, pageErrors, consoleErrors
const screenshots = resolve(__dirname, '../local/workspace-polish-2026-10-28/frontend/legacy-file-browser')

before(async () => {
  mkdirSync(screenshots, { recursive: true })
  browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined })
})
after(async () => { await browser?.close() })
beforeEach(async () => {
  context = await browser.newContext({ viewport: { width: 1440, height: 1100 } })
  page = await context.newPage()
  page.setDefaultTimeout(3500)
  requests = []; pageErrors = []; consoleErrors = []
  page.on('pageerror', error => pageErrors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  page.on('request', request => { if (request.url().includes('/api/v0.1/')) requests.push(request) })
  await page.route('**/api/v0.1/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/artifacts')) {
      return route.fulfill({ json: { artifacts: files, next_cursor: null } })
    }
    if(url.pathname.endsWith('/preview')){
      const first=url.pathname.includes(sourceId),cursor=Number(url.searchParams.get('cursor')||0)
      return route.fulfill({json:{kind:'table',media_type:'text/csv',columns:['Наименование','Количество','Ед. изм.'],
        rows:first?(cursor?[['Гайка','9007199254740993.1234','шт']]:[['Болт','10','шт'],['Шайба','','шт']]):url.pathname.includes(otherId)?[['Новая позиция оригинала','5','шт']]:[['Другой результат','7','шт']],
        total_rows:first?3:1,next_cursor:first&&!cursor?2:null,text:'',notice:''}})
    }
    const isOther = url.pathname.includes(otherResult)
    const cursor = Number(url.searchParams.get('cursor') || 0)
    return route.fulfill({ json: { dataset_id: isOther ? otherResult : resultId, revision: 1,
      records: isOther ? [row('Другой результат', '7', 2)]
        : cursor ? [row('Гайка', '9007199254740993.1234', 4)] : [row('Болт', '10', 2), row('Шайба', null, 3)],
      next_cursor: isOther || cursor ? null : 2 } })
  })
  await page.goto(baseUrl)
})
afterEach(async () => {
  try {
    assert.deepEqual(pageErrors, [])
    assert.deepEqual(consoleErrors.filter(message => !message.startsWith('Failed to load resource:')), [])
    assert.equal(requests.every(request => request.method() === 'GET'), true)
  } finally { await context.close() }
})

async function openFiles() {
  await page.getByRole('link', { name: 'Файлы', exact: true }).click()
  await page.getByRole('button', { name: 'Позиции завода.csv', exact: true }).waitFor()
}

test('opens original CSV preview, preserves precision and missing values, and paginates', async () => {
  await openFiles()
  await page.getByRole('button', { name: 'Позиции завода.csv', exact: true }).click()
  await page.getByRole('cell', { name: 'Болт', exact: true }).waitFor()
  assert.equal(await page.getByRole('row').filter({hasText:'Шайба'}).getByRole('cell').nth(2).textContent(), '')
  await page.getByRole('button', { name: 'Показать ещё строки', exact: true }).click()
  await page.getByRole('cell', { name: '9007199254740993.1234', exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Показать ещё строки', exact: true }).count(), 0)
  for (const request of requests) {
    assert.equal(request.headers().authorization, undefined)
    assert.equal(request.headers()['x-bervel-web'], '1')
  }
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0)
  await page.screenshot({ path: resolve(screenshots, 'files-desktop.png'), fullPage: true })
})

test('reads previous uploads immediately after reload without a key field', async () => {
  await openFiles()
  await page.reload()
  await page.getByRole('heading',{name:'Все файлы',exact:true}).waitFor()
  assert.equal(await page.getByLabel('Ключ доступа к API', { exact: true }).count(), 0)
  await page.getByRole('button', { name: 'Позиции завода.csv', exact: true }).waitFor()
})

test('shows originals with no result and escapes filenames', async () => {
  await openFiles()
  assert.equal(await page.getByRole('button', { name: files[2].filename, exact: true }).isVisible(), true)
  assert.equal(await page.locator('img[src="x"]').count(), 0)
  await page.getByRole('button', { name: 'Новая редакция.csv', exact: true }).click()
  await page.getByRole('cell',{name:'Новая позиция оригинала',exact:true}).waitFor()
  assert.equal(requests.filter(request => request.url().includes('/records')).length, 0)
})

test('ignores a delayed response for another selection and refresh clears previous rows', async () => {
  let release
  const gate = new Promise(resolve => { release = resolve })
  await page.route(`**/artifacts/${sourceId}/preview*`, async route => {
    await gate
    await route.fulfill({json:{kind:'text',media_type:'text/plain',columns:[],rows:[],total_rows:0,next_cursor:null,text:'Поздняя строка',notice:''}}).catch(() => {})
  })
  await openFiles()
  await page.getByRole('button', { name: 'Позиции завода.csv', exact: true }).click()
  await page.getByRole('button', { name: files[2].filename, exact: true }).click()
  await page.getByRole('cell', { name: 'Другой результат', exact: true }).waitFor()
  release()
  await page.route('**/api/v0.1/artifacts*', route => route.fulfill({ json: { artifacts: [], next_cursor: null } }))
  await page.getByRole('button', { name: 'Обновить', exact: true }).click()
  assert.equal(await page.getByRole('cell', { name: 'Другой результат', exact: true }).count(), 0)
  await page.getByText('В этой области данных пока нет загруженных файлов.', { exact: true }).waitFor()
  assert.equal(await page.getByText('Поздняя строка', { exact: true }).count(), 0)
})

test('handles a failed list request and allows a retry', async () => {
  await page.getByRole('button', { name: 'Позиции завода.csv', exact: true }).waitFor()
  let attempts = 0
  await page.route('**/api/v0.1/artifacts*', route => route.fulfill(++attempts === 1
    ? { status: 503, json: { error: { code: 'TRANSIENT_DEPENDENCY_ERROR', message: 'Unavailable' } } }
    : { json: { artifacts: files, next_cursor: null } }))
  await page.goto(baseUrl + '/#/files')
  await page.reload()
  await page.getByRole('alert').waitFor()
  await page.getByRole('button', { name: 'Повторить', exact: true }).click()
  await page.getByRole('button', { name: 'Позиции завода.csv', exact: true }).waitFor()
})

test('keeps the page within the mobile viewport and supports keyboard navigation', async () => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openFiles()
  await page.getByRole('button', { name: 'Позиции завода.csv', exact: true }).focus()
  await page.keyboard.press('Enter')
  await page.getByRole('cell', { name: 'Болт', exact: true }).waitFor()
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true)
  await page.screenshot({ path: resolve(screenshots, 'files-mobile.png'), fullPage: true })
})
