const { test } = require('node:test')
const assert = require('node:assert/strict')
const { chromium } = require('playwright')
test('uploads an arbitrary original without CSV processing and links exact page and metadata source IDs', async () => {
  const browser = await chromium.launch({executablePath:process.env.BERVEL_BROWSER_PATH, headless: true })
  const page = await browser.newPage({ viewport: { width: 390, height: 900 } })
  page.setDefaultTimeout(4000)
  const requests = []
  const id = '11111111-1111-4111-8111-111111111111'
  const date = '2026-10-22T10:00:00Z'
  await page.route('**/api/v0.1/**', async route => {
    const request = route.request(); requests.push(request)
    if (request.method()==='GET' && new URL(request.url()).pathname==='/api/v0.1/chat/sessions') return route.fulfill({json:{sessions:[],next_offset:null}})
    if (request.method() === 'POST') return route.fulfill({ json: { artifact_id: id } })
    return route.fulfill({ json: { artifacts: [], next_cursor: null } })
  })
  try {
    await page.goto((process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1') + '/#/files')
    await page.getByRole('button',{name:'Загрузить файл',exact:true}).click()
  await page.getByRole('region',{name:'Загрузка файлов',exact:true}).waitFor()
    await page.getByLabel('Файл', { exact: true }).setInputFiles({ name: 'drawing.custom', mimeType: 'application/octet-stream', buffer: Buffer.from([0, 255, 1]) })
    await page.getByRole('dialog').getByRole('button', { name: 'Загрузить файл', exact: true }).click()
    await page.getByRole('status').filter({ hasText: 'drawing.custom' }).waitFor()
    assert.equal(requests.filter(request => request.method() === 'POST').length, 1)
    assert.equal(requests.some(request => request.url().includes('/extractions')), false)
    await page.route('**/chat/sessions/**', route => route.fulfill({ json: { session_id: id, created_at: date,
      turns: [{ turn_id: '22222222-2222-4222-8222-222222222222', question: 'Где данные?', answer: 'contract.pdf, страница 2',
        state: 'completed', created_at: date, error: null, sources: [
          { artifact_id: id, filename: 'contract.pdf', created_at: date, row_start: 2, row_end: 2,
            total_rows: 5, excerpt: 'PDF_FACT_123', truncated: true, unit: 'page', status: 'partial', media_type: 'application/pdf' },
          { artifact_id: '33333333-3333-4333-8333-333333333333', filename: 'drawing.custom', created_at: date, row_start: 0, row_end: 0,
            total_rows: 0, excerpt: 'Содержимое не прочитано', truncated: false, unit: 'metadata', status: 'metadata_only', media_type: 'application/octet-stream' },
        ] }] } }))
    await page.evaluate(value => localStorage.setItem('bervel.chat.session_id', value), id)
    await page.goto((process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1') + '/#/chat')
    await page.getByRole('link',{name:'contract.pdf',exact:true}).waitFor()
    await page.getByRole('link',{name:'drawing.custom',exact:true}).waitFor()
    assert.equal(await page.getByText('PDF_FACT_123',{exact:true}).count(),0)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
  } finally { await browser.close() }
})
