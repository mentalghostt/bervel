const assert = require('node:assert/strict')
const { test, before, after, beforeEach, afterEach } = require('node:test')
const { chromium } = require('playwright')
const { resolve } = require('node:path')
const { mkdirSync } = require('node:fs')

const base = process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5177'
const out = resolve(process.env.BERVEL_TEST_ARTIFACT_ROOT||resolve(__dirname, '../local/inspection-steering-2026-11-06/ui'))
const sid = '22222222-2222-4222-8222-222222222222'
const rootId = '33333333-3333-4333-8333-333333333333'
const time = '2026-11-06T10:00:00Z'
const childId = index => `44444444-4444-4444-8444-${String(index).padStart(12, '0')}`
const makeTurn = (overrides = {}) => ({ turn_id: rootId, question: 'Проверь систему', answer: null,
  state: 'running', created_at: time, sources: [], results: [], actions: [], error: null,
  progress_text: 'Читаю исходники.', followup_of: null, delivery_state: null, ...overrides })
let browser, context, page, session, requests, errors, sendBehavior, readBehavior, accepted, heldSend
before(async () => { mkdirSync(out, { recursive: true }); browser = await chromium.launch({executablePath:process.env.BERVEL_BROWSER_PATH, headless: true }) })
after(async () => { await browser?.close() })
beforeEach(async () => {
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.addInitScript(id => {
    localStorage.setItem('bervel.inspection.session_id', id)
    localStorage.setItem('bervel.chat.session_id', id)
  }, sid)
  page = await context.newPage(); page.setDefaultTimeout(5000)
  session = { session_id: sid, created_at: time, engine: 'codex-cli', turns: [makeTurn()] }
  requests = []; errors = []; sendBehavior = 'success'; readBehavior = 'normal'; accepted = new Map(); heldSend = null
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/v0.1/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname
    const body = request.method() === 'POST' ? request.postDataJSON() : null
    requests.push({ path, method: request.method(), body })
    if (/\/(inspection|chat)\/sessions$/.test(path)) return route.fulfill({ json: {
      sessions: [{ session_id: sid, created_at: time, title: session.turns[0].question,
        turn_count: session.turns.length, has_pending: session.turns.some(turn => ['queued', 'running'].includes(turn.state)) }], next_offset: null,
    } })
    if (/\/(inspection|chat)\/sessions\//.test(path)) {
      if (path.endsWith('/messages')) {
        let child = accepted.get(body.command_key)
        if (!child) {
          child = makeTurn({ turn_id: childId(accepted.size + 1), question: body.content, state: 'queued',
            progress_text: null, followup_of: rootId, delivery_state: 'pending' })
          accepted.set(body.command_key, child); session.turns.push(child)
        }
        if (sendBehavior === 'hold') { heldSend = () => route.fulfill({ status: 202, json: child }); return }
        if (sendBehavior === 'lost') return route.abort('connectionfailed')
        return route.fulfill({ status: 202, json: child })
      }
      if (readBehavior === 'hold') return
      const command = url.searchParams.get('pending_command_key')
      return route.fulfill({ json: { ...session,
        accepted_turn_id: readBehavior === 'hide-receipt' ? null : accepted.get(command)?.turn_id ?? null,
      } })
    }
    return route.fulfill({ json: { artifacts: [], next_cursor: null } })
  })
})
afterEach(async () => { try { assert.deepEqual(errors, []) } finally { await context.close() } })
const textbox = () => page.getByRole('textbox', { name: 'Сообщение', exact: true })
const send = () => page.getByRole('button', { name: 'Отправить', exact: true })
const userMessage = content => page.getByRole('article', { name: 'Сообщение пользователя', exact: true }).getByText(content, { exact: true })
const posts = () => requests.filter(request => request.method === 'POST' && request.path.endsWith('/messages'))
async function open(mode = 'inspection') {
  await page.goto(`${base}/#/${mode === 'assistant' ? 'chat' : mode}`)
  await page.getByRole('article', { name: 'Ответ Codex', exact: true }).first().waitFor()
  await page.waitForFunction(() => !document.querySelector('.chat-transcript')?.textContent.includes('Восстанавливаем диалог'))
}
async function waitEnabled() { await page.waitForFunction(() => !document.querySelector('#inspection-message')?.disabled) }

for (const state of ['queued', 'running']) test(`inspection ${state} permits text and voice, then sends two unique followups`, async () => {
  session.turns[0].state = state
  await open(); assert.ok(await textbox().isEnabled())
  assert.ok(await page.getByRole('button', { name: 'Записать голосовое сообщение', exact: true }).isEnabled())
  for (const content of ['Первое уточнение', 'Второе уточнение']) {
    await textbox().fill(content); await send().click()
    await userMessage(content).waitFor(); await waitEnabled()
  }
  assert.equal(posts().length, 2)
  assert.notEqual(posts()[0].body.command_key, posts()[1].body.command_key)
  assert.deepEqual(posts().map(request => request.body.content), ['Первое уточнение', 'Второе уточнение'])
  assert.equal(await page.getByRole('article', { name: 'Ответ Codex', exact: true }).count(), 1)
  assert.equal(await page.getByRole('button', { name: 'Остановить', exact: true }).count(), 1)
})

test('inspection Enter sends once, Shift Enter inserts newline, whitespace does not send', async () => {
  session.turns[0] = makeTurn({ state: 'completed', answer: 'Готово' }); await open()
  await textbox().fill('  '); await textbox().press('Enter'); assert.equal(posts().length, 0)
  await textbox().fill('Первая строка'); await textbox().press('Shift+Enter'); await page.keyboard.insertText('В')
  assert.equal(await textbox().inputValue(), 'Первая строка\nВ')
  await textbox().press('Enter'); await userMessage('Первая строка\nВ').waitFor()
  assert.equal(posts().length, 1); assert.equal(posts()[0].body.content, 'Первая строка\nВ')
})

test('inspection Enter respects composition, legacy IME keyCode and held-key repeats', async () => {
  session.turns[0] = makeTurn({ state: 'completed', answer: 'Готово' }); await open()
  await textbox().fill('Уточнение')
  await textbox().dispatchEvent('compositionstart')
  await textbox().dispatchEvent('keydown', { key: 'Enter', code: 'Enter' })
  await textbox().dispatchEvent('compositionend')
  await textbox().dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true })
  await textbox().dispatchEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 229 })
  await textbox().dispatchEvent('keydown', { key: 'Enter', code: 'Enter', repeat: true })
  assert.equal(posts().length, 0)
  await textbox().press('Enter'); await userMessage('Уточнение').waitFor()
  assert.equal(posts().length, 1)
})

test('HTTP send stays locked and an immediate repeated Enter cannot create duplicates', async () => {
  sendBehavior = 'hold'; await open(); await textbox().fill('Только один раз')
  await textbox().press('Enter'); await page.waitForFunction(() => document.querySelector('#inspection-message')?.disabled)
  await page.keyboard.press('Enter'); await page.keyboard.press('Enter')
  assert.equal(posts().length, 1); assert.ok(await textbox().isDisabled())
  await heldSend(); await waitEnabled(); assert.equal(posts().length, 1)
})

test('delivery labels update without child answers, and final answer follows all clarification messages', async () => {
  session.turns.push(makeTurn({ turn_id: childId(1), question: 'Учитывай склад', state: 'queued', followup_of: rootId, delivery_state: 'pending' }))
  await open(); await page.getByText('Передаём уточнение…', { exact: true }).waitFor()
  session.turns[1].delivery_state = 'sending'
  await page.waitForTimeout(1100)
  assert.equal(await page.getByRole('article', { name: 'Ответ Codex', exact: true }).count(), 1)
  session.turns[1] = { ...session.turns[1], state: 'completed', delivery_state: 'accepted' }
  session.turns[0] = { ...session.turns[0], state: 'completed', answer: 'Итог с учётом склада' }
  await page.getByText('Передано в текущую проверку', { exact: true }).waitFor()
  const transcript = await page.locator('.chat-transcript').innerText()
  assert.ok(transcript.indexOf('Учитывай склад') < transcript.indexOf('Итог с учётом склада'))
  assert.equal(await page.getByRole('article', { name: 'Ответ Codex', exact: true }).count(), 1)
})

test('unknown delivery displays its failure beside the user message without a child Stop', async () => {
  session.turns.push(makeTurn({ turn_id: childId(1), question: 'Сверь цены', state: 'blocked', followup_of: rootId,
    delivery_state: 'unknown', error: 'Доставка не подтверждена; проверьте итог.', progress_text: null }))
  await open(); await page.getByRole('alert').filter({ hasText: 'Доставка не подтверждена; проверьте итог.' }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Остановить', exact: true }).count(), 1)
  assert.equal(await page.getByRole('article', { name: 'Ответ Codex', exact: true }).count(), 1)
})

test('stopped undelivered clarification shows its terminal error instead of a transmitting label', async () => {
  session.turns[0] = makeTurn({ state: 'blocked', error: 'Проверка остановлена.' })
  session.turns.push(makeTurn({ turn_id: childId(1), question: 'Отменённое уточнение', state: 'blocked', followup_of: rootId,
    delivery_state: 'pending', error: 'Уточнение не доставлено: проверка остановлена.', progress_text: null }))
  await open()
  await page.getByRole('alert').filter({ hasText: 'Уточнение не доставлено: проверка остановлена.' }).waitFor()
  assert.equal(await page.getByText('Передаём уточнение…', { exact: true }).count(), 0)
  assert.equal(await page.getByRole('button', { name: 'Остановить', exact: true }).count(), 0)
})

test('lost POST response restores receipt on reload without resending, then late polling preserves a fresh draft', async () => {
  sendBehavior = 'lost'; readBehavior = 'hide-receipt'; await open(); await textbox().fill('Сохранённое уточнение')
  await send().click(); await page.getByText(/Отправка не подтверждена/).waitFor()
  assert.ok(await textbox().isDisabled()); assert.equal(posts().length, 1)
  readBehavior = 'normal'; await page.reload(); await waitEnabled()
  assert.equal(await textbox().inputValue(), ''); assert.equal(posts().length, 1)
  await textbox().fill('Новый черновик после восстановления')
  session.turns[1] = { ...session.turns[1], state: 'completed', delivery_state: 'accepted' }
  await page.getByText('Передано в текущую проверку', { exact: true }).waitFor()
  assert.equal(await textbox().inputValue(), 'Новый черновик после восстановления')
  assert.equal(await page.evaluate(id => sessionStorage.getItem(`bervel.inspection.draft.${id}`), sid), 'Новый черновик после восстановления')
})

test('uncertain retry uses original command and text once while an active root is running', async () => {
  sendBehavior = 'lost'; readBehavior = 'hide-receipt'; await open(); await textbox().fill('Повтори без дубля')
  await send().click(); await page.getByText(/Отправка не подтверждена/).waitFor()
  sendBehavior = 'success'
  await page.getByRole('button', { name: 'Повторить отправку', exact: true }).click(); await waitEnabled()
  assert.equal(posts().length, 2); assert.deepEqual(posts()[0].body, posts()[1].body)
  assert.equal(await userMessage('Повтори без дубля').count(), 1)
})

test('an ordinary queued turn detached by the server gets its own response and stop control', async () => {
  session.turns[0] = makeTurn({ state: 'completed', answer: 'Предыдущий итог' })
  session.turns.push(makeTurn({ turn_id: childId(1), question: 'Следующий запрос', state: 'queued' }))
  await open()
  assert.equal(await page.getByRole('article', { name: 'Ответ Codex', exact: true }).count(), 2)
  assert.equal(await page.getByRole('button', { name: 'Остановить', exact: true }).count(), 1)
  assert.ok(await textbox().isEnabled())
})

for (const field of [{ followup_of: 'invalid' }, { delivery_state: 'rejected' }, { delivery_state: ['pending'] }]) test(`inspection rejects malformed optional ${JSON.stringify(field)}`, async () => {
  Object.assign(session.turns[0], field)
  await page.goto(`${base}/#/inspection`)
  await page.getByRole('alert').filter({ hasText: /несовместимый ответ/i }).waitFor()
  assert.ok(await textbox().isDisabled())
})

test('assistant keeps busy input and newline Enter, ignoring inspection-only metadata', async () => {
  session.engine = undefined; session.turns[0].followup_of = 'not-an-assistant-field'; session.turns[0].delivery_state = 'not-an-assistant-field'
  await open('assistant'); assert.ok(await textbox().isDisabled())
  assert.ok(await page.getByRole('button', { name: 'Записать голосовое сообщение', exact: true }).isDisabled())
  session.turns[0] = { ...session.turns[0], state: 'completed', answer: 'Ответ ассистента' }
  await page.getByText('Ответ ассистента', { exact: true }).waitFor()
  await textbox().fill('Строка'); await textbox().press('Enter')
  assert.equal(await textbox().inputValue(), 'Строка\n'); assert.equal(posts().length, 0)
  assert.equal(await textbox().getAttribute('maxlength'), '4000')
})

for (const width of [1440, 390]) test(`steering history and composer fit ${width}px`, async () => {
  await page.setViewportSize({ width, height: 1000 })
  session.turns.push(makeTurn({ turn_id: childId(1), question: 'Дополнительно проверь остатки на складе и срок поставки.',
    state: 'completed', followup_of: rootId, delivery_state: 'accepted' }))
  await open(); await textbox().fill('Следующее уточнение')
  await page.getByText('Передано в текущую проверку', { exact: true }).waitFor()
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
  assert.ok(await textbox().isEnabled()); assert.ok(await send().isEnabled())
  await page.screenshot({ path: resolve(out, `steering-${width}.png`), fullPage: true })
})
