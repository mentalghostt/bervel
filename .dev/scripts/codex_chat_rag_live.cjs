// Genuine MOX + browser + worker + PostgreSQL acceptance. No mocked responses.
const { chromium } = require('playwright')
const assert = require('node:assert/strict')
const { mkdirSync, writeFileSync } = require('node:fs')
const { resolve } = require('node:path')

;(async () => {
  const output = resolve(__dirname, '../local/codex-chat/tls-live')
  mkdirSync(output, { recursive: true })
  const run = Date.now().toString(36)
  const report = { real_model: true, provider: 'mox', uploads: [], turns: [], browser_errors: [] }
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } })
  const page = await context.newPage()
  page.setDefaultTimeout(10000)
  page.on('pageerror', error => report.browser_errors.push(error.message))
  async function readSession() {
    return page.evaluate(async () => {
      const id = localStorage.getItem('bervel.chat.session_id')
      const response = await fetch('/api/v0.1/chat/sessions/' + id, { headers: { 'X-Bervel-Web': '1' } })
      if (!response.ok) throw Error('Session read failed: ' + response.status)
      return response.json()
    })
  }
  async function question(text) {
    await page.getByRole('textbox', { name: 'Сообщение', exact: true }).fill(text)
    const sent = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/messages'))
    await page.getByRole('button', { name: 'Отправить', exact: true }).click()
    const response = await sent
    assert.equal(response.status(), 202)
    const accepted = await response.json()
    const deadline = Date.now() + 195000
    while (Date.now() < deadline) {
      const session = await readSession()
      const turn = session.turns.find(item => item.turn_id === accepted.turn_id)
      if (turn && ['completed', 'failed'].includes(turn.state)) {
        report.session_id = session.session_id
        report.turns.push(turn)
        assert.equal(turn.state, 'completed', turn.error || 'Model failed')
        await page.getByRole('article', { name: 'Ответ Codex', exact: true }).last().getByText(turn.answer, { exact: true }).waitFor()
        return turn
      }
      await page.waitForTimeout(1000)
    }
    throw Error('Model turn timed out; no retry is attempted')
  }
  try {
    for (const [index, label, marker, quantities] of [[0, 'alpha', 'RAG_ALPHA_72913', [17, 29]], [1, 'beta', 'RAG_BETA_48261', [83, 47]]]) {
      const filename = `rag-proof-${label}-${run}.csv`
      const content = `line,name,quantity,unit\n1,${marker},${quantities[0]},pcs\n2,RESERVE_${marker},${quantities[1]},pcs\n`
      writeFileSync(resolve(output, filename), content)
      await page.goto('http://127.0.0.1/')
      await page.locator('input[type="file"]').setInputFiles({ name: filename, mimeType: 'text/csv', buffer: Buffer.from(content) })
      const uploaded = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith('/artifacts'))
      await page.getByRole('button', { name: 'Загрузить в БД', exact: true }).click()
      const originalResponse = await uploaded
      assert.equal(originalResponse.status(), 200)
      const ref = await originalResponse.json()
      report.uploads.push({ ...ref, filename, content })
      await page.getByRole('heading', { name: 'Сохранено в PostgreSQL', exact: true }).waitFor({ timeout: 30000 })
      await page.getByRole('link', { name: 'Тест чата', exact: true }).click()
      const turn = await question('Что в самом последнем csv? Назови имя файла и обе позиции с точными количествами и единицами из этого файла.')
      assert.ok(turn.sources.length)
      assert.ok(turn.sources.every(source => source.artifact_id === ref.artifact_id && source.revision === ref.revision
        && source.filename === filename && source.total_rows === 2 && source.truncated === false))
      assert.ok(turn.answer.includes(marker), 'Answer must contain the unique marker from the actual latest file')
      for (const quantity of quantities) assert.match(turn.answer, new RegExp('\\b' + quantity + '\\b'))
      if (index === 1) assert.equal(turn.answer.includes('RAG_ALPHA_72913'), false, 'The obsolete CSV must not leak into the latest answer')
      await page.screenshot({ path: resolve(output, `chat-${label}.png`), fullPage: true })
      console.log(JSON.stringify({ stage: label, filename, state: turn.state, answer: turn.answer, source: ref }))
    }
    await page.reload()
    await page.getByRole('heading', { name: 'Тест чата', exact: true }).waitFor()
    await page.getByText(report.turns[1].answer, { exact: true }).waitFor()
    assert.equal((await readSession()).turns.length, 2)
    const restored = await question('После обновления страницы какой csv сейчас последний? Назови его имя и количество у позиции RAG_BETA_48261.')
    assert.ok(restored.answer.includes(report.uploads[1].filename))
    assert.match(restored.answer, /\b83\b/)
    assert.ok(restored.sources.every(source => source.artifact_id === report.uploads[1].artifact_id))
    assert.deepEqual(report.browser_errors, [])
    report.passed = true
    await page.screenshot({ path: resolve(output, 'chat-restored.png'), fullPage: true })
    console.log(JSON.stringify({ stage: 'reload', answer: restored.answer, passed: true }))
  } finally {
    writeFileSync(resolve(output, 'rag-live.json'), JSON.stringify(report, null, 2))
    await context.close(); await browser.close()
  }
})().catch(error => { console.error(error); process.exit(1) })
