// Genuine browser -> API -> worker -> Codex/MOX. No routes or model stubs.
const { chromium } = require('playwright')
const assert = require('node:assert/strict')
const { readFileSync, writeFileSync } = require('node:fs')
const { resolve } = require('node:path')

;(async () => {
  const output = resolve(__dirname, '../local/file-memory/live')
  const old = JSON.parse(readFileSync(resolve(__dirname, '../local/codex-chat/tls-live/rag-live.json')))
  const report = { real_model: true, provider: 'mox', uploads: [], turns: [], browser_errors: [] }
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, acceptDownloads: true })
  page.setDefaultTimeout(10000)
  page.on('pageerror', error => report.browser_errors.push(error.message))
  async function session() {
    return page.evaluate(async () => {
      const id = localStorage.getItem('bervel.chat.session_id')
      const response = await fetch('/api/v0.1/chat/sessions/' + id, { headers: { 'X-Bervel-Web': '1' } })
      if (!response.ok) throw Error('Session read failed ' + response.status)
      return response.json()
    })
  }
  async function ask(label, text) {
    await page.getByRole('textbox', { name: 'Сообщение', exact: true }).fill(text)
    const sent = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/messages'))
    await page.getByRole('button', { name: 'Отправить', exact: true }).click()
    const response = await sent; assert.equal(response.status(), 202)
    const accepted = await response.json()
    const deadline = Date.now() + 195000
    while (Date.now() < deadline) {
      const state = await session()
      const turn = state.turns.find(t => t.turn_id === accepted.turn_id)
      if (turn && ['completed', 'failed'].includes(turn.state)) {
        report.session_id = state.session_id; report.turns.push({ label, ...turn })
        writeFileSync(resolve(output, 'file-memory-live.json'), JSON.stringify(report, null, 2))
        assert.equal(turn.state, 'completed', turn.error || 'Model failed')
        await page.getByRole('article', { name: 'Ответ Codex', exact: true }).last().getByText(turn.answer, { exact: true }).waitFor()
        await page.getByRole('article', { name: 'Ответ Codex', exact: true }).last().scrollIntoViewIfNeeded()
        await page.screenshot({ path: resolve(output, label + '.png') })
        console.log(JSON.stringify({ label, answer: turn.answer, sources: turn.sources.map(s => ({ filename: s.filename, unit: s.unit, status: s.status, image_attached: s.image_attached })) }))
        return turn
      }
      await page.waitForTimeout(1000)
    }
    throw Error('Actual model timeout; no automatic retry')
  }
  function source(turn, file, unit) {
    const item = turn.sources.find(s => s.artifact_id === file.artifact_id && s.revision === file.revision)
    assert.ok(item, 'Missing exact artifact/revision for ' + file.filename)
    if (unit) assert.equal(item.unit, unit)
    return item
  }
  try {
    const names = ['memory-stock-6318.csv', 'memory-note-4812.txt', 'memory-contract-7621.pdf', 'memory-picture-9374.png', 'memory-drawing-4826.custom']
    for (const filename of names) {
      await page.goto('http://127.0.0.1/#/files')
      await page.getByLabel('Файл для памяти чата', { exact: true }).setInputFiles(resolve(output, filename))
      const uploaded = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith('/artifacts'))
      await page.getByRole('button', { name: 'Загрузить файл', exact: true }).click()
      const response = await uploaded; assert.equal(response.status(), 200)
      report.uploads.push({ filename, ...await response.json() })
      await page.getByRole('status').filter({ hasText: filename }).waitFor()
    }
    await page.screenshot({ path: resolve(output, 'files-desktop.png'), fullPage: true })
    await page.goto('http://127.0.0.1/#/chat')
    const compared = await ask('old-csv-compare', `Сравни количества RAG_ALPHA_72913 в ${old.uploads[0].filename} и RAG_BETA_48261 в ${old.uploads[1].filename}. Назови оба файла и точные количества.`)
    for (const file of old.uploads) { source(compared, file, 'row'); assert.ok(compared.answer.includes(file.filename)) }
    assert.match(compared.answer, /\b17\b/); assert.match(compared.answer, /\b83\b/)
    const latest = await ask('latest-csv', 'Что в самом последнем CSV? Назови имя, позицию и количество, несмотря на более новые загрузки других типов.')
    source(latest, report.uploads[0], 'row')
    assert.ok(latest.sources.every(s => s.artifact_id === report.uploads[0].artifact_id))
    assert.match(latest.answer, /1374|1[ \u00a0\u202f]374/)
    const note = await ask('text', 'Найди memory-note-4812.txt: какой склад warehouse и резерв reserve? Укажи источник.')
    source(note, report.uploads[1], 'line'); assert.match(note.answer, /KAZAN/); assert.match(note.answer, /246/)
    const pdf = await ask('pdf', 'Найди memory-contract-7621.pdf. Какое значение price у DOCUMENT_PDF_7621? Укажи страницу.')
    source(pdf, report.uploads[2], 'page'); assert.match(pdf.answer, /318/)
    const image = await ask('image', 'Посмотри memory-picture-9374.png. Каково quantity у IMAGE_TOKEN_9374 и какого цвета прямоугольник под текстом? Укажи файл.')
    assert.equal(source(image, report.uploads[3], 'image').image_attached, true)
    assert.match(image.answer, /76/); assert.match(image.answer, /син|голуб/i)
    const unknown = await ask('unknown', 'Найди memory-drawing-4826.custom. Можешь прочитать содержимое? Если нет, прямо укажи это, не придумывай.')
    assert.equal(source(unknown, report.uploads[4], 'metadata').status, 'metadata_only')
    assert.match(unknown.answer, /не проч|недоступ|не поддерж|не могу|нет средств|не удалось/i)
    assert.ok(unknown.answer.includes('memory-drawing-4826.custom'))
    // Download through the UI, including the required CSRF marker, and verify bytes.
    const card = page.getByRole('article', { name: 'Ответ Codex', exact: true }).last().getByRole('listitem').filter({ has: page.getByText('memory-drawing-4826.custom', { exact: true }) })
    const downloaded = page.waitForEvent('download')
    await card.getByRole('button', { name: 'Скачать оригинал', exact: true }).click()
    const download = await downloaded
    const path = resolve(output, 'downloaded.custom'); await download.saveAs(path)
    assert.deepEqual(readFileSync(path), readFileSync(resolve(output, 'memory-drawing-4826.custom')))
    report.download_verified = true
    await page.reload()
    await page.getByText(unknown.answer, { exact: true }).waitFor()
    assert.equal((await session()).turns.length, 6)
    report.reload_verified = true
    await page.setViewportSize({ width: 390, height: 900 })
    await page.getByRole('article', { name: 'Ответ Codex', exact: true }).last().scrollIntoViewIfNeeded()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    await page.screenshot({ path: resolve(output, 'chat-mobile.png') })
    assert.deepEqual(report.browser_errors, []); report.passed = true
    console.log('ACTUAL MODEL ACCEPTANCE PASSED')
  } finally {
    writeFileSync(resolve(output, 'file-memory-live.json'), JSON.stringify(report, null, 2))
    await browser.close()
  }
})().catch(error => { console.error(error); process.exit(1) })
