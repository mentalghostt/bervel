const assert = require('node:assert/strict')
const { before, after, beforeEach, afterEach, test } = require('node:test')
const { mkdirSync } = require('node:fs')
const { resolve } = require('node:path')
const { chromium } = require('playwright')

const baseUrl = process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5173'
const screenshots = resolve(process.env.BERVEL_TEST_ARTIFACT_ROOT||resolve(__dirname, '../local/workspace-polish-2026-10-28/frontend/legacy-chat'))
const sessionId = '11111111-1111-4111-8111-111111111111'
const timestamp = '2026-10-22T10:00:00Z'
const source = { artifact_id: '22222222-2222-4222-8222-222222222222',
  filename: '<img src=x>.csv', created_at: timestamp, row_start: 2, row_end: 5,
  total_rows: 500, excerpt: '<script>alert(1)</script> Болт,7', truncated: true }
const turn = (state = 'completed') => ({ turn_id: '33333333-3333-4333-8333-333333333333',
  question: 'Что в последнем CSV?', answer: state === 'completed' ? '<b>Болт: 7</b>' : null,
  state, created_at: timestamp, sources: [source], error: state === 'failed' ? 'Модель недоступна.' : null })
let browser, context, page, session, requests, pageErrors

before(async () => {
  mkdirSync(screenshots, { recursive: true })
  browser = await chromium.launch({executablePath:process.env.BERVEL_BROWSER_PATH, headless: true })
})
after(async () => { await browser?.close() })
beforeEach(async () => {
  context = await browser.newContext({ viewport: { width: 1440, height: 1100 } })
  page = await context.newPage()
  page.setDefaultTimeout(4500)
  session = { session_id: sessionId, created_at: timestamp, turns: [] }
  requests = []; pageErrors = []
  page.on('pageerror', error => pageErrors.push(error.message))
  page.on('request', request => { if (request.url().includes('/api/v0.1/')) requests.push(request) })
  await page.route('**/api/v0.1/**', async route => {
    const request = route.request()
    if (request.method() === 'GET' && new URL(request.url()).pathname === '/api/v0.1/chat/sessions') return route.fulfill({json:{sessions:[{session_id:session.session_id,created_at:session.created_at,title:session.turns[0]?.question || 'Новый диалог',turn_count:session.turns.length,has_pending:session.turns.some(t=>['queued','running'].includes(t.state))}],next_offset:null}})
    if (request.url().includes('/chat/')) {
      if (request.method() === 'POST' && request.url().endsWith('/messages')) {
        session.turns = [turn('queued')]
        return route.fulfill({ status: 202, json: session.turns[0] })
      }
      return route.fulfill({ json: session })
    }
    return route.fulfill({ json: { artifacts: [], next_cursor: null } })
  })
  await page.goto(baseUrl)
})
afterEach(async () => {
  try {
    assert.deepEqual(pageErrors, [])
    for (const request of requests) {
      assert.equal(request.headers().authorization, undefined)
      assert.equal(request.headers()['x-bervel-web'], '1')
    }
    const storage = await page.evaluate(() => [...Object.values(localStorage), ...Object.values(sessionStorage)])
    assert.equal(storage.every(value => /^(?:(?:session|message):)?[0-9a-f-]{36}$/i.test(value)), true)
  } finally { await context.close() }
})
async function openChat() {
  await page.getByRole('link',{name:'Ассистент',exact:true}).click()
  await page.getByRole('textbox',{name:'Сообщение',exact:true}).waitFor()
}
async function submit() {
  await page.getByRole('textbox', { name: 'Сообщение', exact: true }).fill('Что в последнем CSV?')
  const request = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/messages'))
  await page.getByRole('button', { name: 'Отправить', exact: true }).click()
  await (await request).response()
}
function posts() { return requests.filter(request => request.method() === 'POST' && request.url().endsWith('/messages')) }

test('chat navigation is lazy and does not add browser state to the files page', async () => {
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0)
  assert.equal(requests.filter(request => request.url().includes('/chat/')).length, 0)
  await openChat()
  assert.equal(new URL(page.url()).hash, '#/chat')
  assert.equal(await page.getByRole('button', { name: 'Отправить', exact: true }).isDisabled(), true)
})

test('submits once, polls queued work, shows escaped answer and an escaped exact-source link', async () => {
  await openChat()
  await submit()
  await page.getByText('В очереди…', { exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Новый диалог', exact: true }).isDisabled(), true)
  assert.equal(await page.getByRole('button', { name: 'Отправить', exact: true }).isDisabled(), true)
  assert.equal(posts().length, 1)
  assert.equal(posts()[0].postDataJSON().content, 'Что в последнем CSV?')
  session.turns = [turn()]
  await page.getByText('<b>Болт: 7</b>', { exact: true }).waitFor()
  const sourceLink=page.getByRole('link',{name:source.filename,exact:true});await sourceLink.waitFor()
  assert.equal(await sourceLink.getAttribute('href'),'#/files?artifact_id='+source.artifact_id)
  assert.equal(await page.getByText(source.excerpt,{exact:true}).count(),0)
  assert.equal(await page.locator('img[src=x]').count(), 0)
  assert.equal(await page.locator('script').filter({hasText:'alert(1)'}).count(), 0)
  const readCount = requests.filter(request => request.method() === 'GET').length
  await page.waitForTimeout(1300)
  assert.equal(requests.filter(request => request.method() === 'GET').length, readCount)
  await page.screenshot({ path: resolve(screenshots, 'chat-desktop.png'), fullPage: true })
})

test('reload and tab return restore server history; new dialog removes the old conversation', async () => {
  await openChat(); await submit()
  session.turns = [turn()]
  await page.getByText('<b>Болт: 7</b>', { exact: true }).waitFor()
  await page.reload()
  await page.getByText('<b>Болт: 7</b>', { exact: true }).waitFor()
  await page.getByRole('link', { name: 'Файлы', exact: true }).click()
  session.turns[0].answer = 'История с сервера'
  await openChat()
  await page.getByText('История с сервера', { exact: true }).waitFor()
  await page.getByRole('button', { name: 'Новый диалог', exact: true }).click()
  assert.equal(await page.getByText('История с сервера', { exact: true }).count(), 0)
  assert.equal(posts().length, 1)
})

test('network uncertainty retains the command key and retries without a duplicate turn', async () => {
  let attempts = 0
  await page.route('**/chat/sessions/*/messages', async route => {
    if (++attempts === 1) return route.abort('failed')
    session.turns = [turn()]
    return route.fulfill({ status: 202, json: turn() })
  })
  await openChat(); await submit()
  await page.getByRole('alert').waitFor()
  await page.getByRole('button', { name: 'Повторить отправку', exact: true }).click()
  await page.getByText('<b>Болт: 7</b>', { exact: true }).waitFor()
  assert.equal(posts().length, 2)
  assert.equal(posts()[0].postDataJSON().command_key, posts()[1].postDataJSON().command_key)
  assert.equal(await page.locator('.chat-transcript').getByText('Что в последнем CSV?', { exact: true }).count(), 1)
})

test('failed generation is terminal and does not silently retry', async () => {
  await openChat(); await submit()
  session.turns = [turn('failed')]
  await page.getByRole('alert').filter({ hasText: 'Модель недоступна.' }).waitFor()
  await page.waitForTimeout(1200)
  assert.equal(posts().length, 1)
  assert.equal(await page.getByRole('button', { name: 'Новый диалог', exact: true }).isEnabled(), true)
})

test('read errors can be retried and malformed payloads are not rendered', async () => {
  await openChat(); await submit()
  session.turns = [turn()]
  await page.getByText('<b>Болт: 7</b>', { exact: true }).waitFor()
  await page.route('**/chat/sessions/*', route => route.fulfill({ json: { ...session, turns: [{ ...turn(), sources: 'bad' }] } }))
  await page.reload()
  await page.getByRole('alert').waitFor()
  assert.equal(await page.getByText('<b>Болт: 7</b>', { exact: true }).count(), 0)
  await page.unroute('**/chat/sessions/*')
  await page.getByRole('button', { name: 'Повторить чтение', exact: true }).click()
  await page.getByText('<b>Болт: 7</b>', { exact: true }).waitFor()
})

test('incompatible chat response explains page refresh after an application update', async () => {
  await openChat(); await submit()
  await page.route('**/chat/sessions/'+sessionId, route => route.fulfill({json:{...session,turns:[{...turn(),sources:'invalid'}]}}))
  await page.reload()
  await page.getByRole('alert').filter({hasText:'Если приложение обновлялось, обновите страницу'}).waitFor()
  assert.equal(posts().length,1)
})

test('retry read shows pending progress and restores history without resubmitting the question', async () => {
  await openChat(); await submit()
  session.turns=[turn()]
  await page.getByText('<b>Болт: 7</b>',{exact:true}).waitFor()
  let reads=0,release
  const gate=new Promise(resolve=>{release=resolve})
  await page.route('**/chat/sessions/'+sessionId,async route=>{
    if(++reads===1)return route.fulfill({json:{...session,turns:[{...turn(),sources:'invalid'}]}})
    await gate
    return route.fulfill({json:session})
  })
  await page.reload();await page.getByRole('alert').waitFor()
  try{
    await page.getByRole('button',{name:'Повторить чтение',exact:true}).click()
    const progress=page.getByRole('button',{name:'Читаем историю…',exact:true})
    await progress.waitFor()
    assert.equal(await progress.isDisabled(),true)
    assert.equal(await progress.getAttribute('aria-busy'),'true')
  }finally{release()}
  await page.getByText('<b>Болт: 7</b>',{exact:true}).waitFor()
  assert.equal(await page.getByRole('button',{name:'Повторить чтение',exact:true}).count(),0)
  assert.equal(await page.getByRole('textbox',{name:'Сообщение',exact:true}).isEnabled(),true)
  assert.equal(posts().length,1)
  assert.equal(reads,2)
})

test('mobile keyboard flow stays inside viewport and input is limited to 4000 characters', async () => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openChat()
  const input = page.getByRole('textbox', { name: 'Сообщение', exact: true })
  assert.equal(await input.getAttribute('maxlength'), '4000')
  await input.fill('Что в последнем CSV?')
  await page.getByRole('button', { name: 'Отправить', exact: true }).focus()
  const sent = page.waitForResponse(response => response.url().endsWith('/messages'))
  await page.keyboard.press('Enter')
  await sent
  session.turns = [turn()]
  await page.getByText('<b>Болт: 7</b>', { exact: true }).waitFor()
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
  await page.screenshot({ path: resolve(screenshots, 'chat-mobile.png'), fullPage: true })
})

test('reload resumes running work with reads and never submits the question again', async () => {
  await openChat(); await submit()
  await page.getByText('В очереди…', { exact: true }).waitFor()
  session.turns = [turn('running')]
  await page.reload()
  await page.locator('.chat-transcript').getByText('Готовим ответ…', { exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Новый диалог', exact: true }).isDisabled(), true)
  session.turns = [turn()]
  await page.getByText('<b>Болт: 7</b>', { exact: true }).waitFor()
  assert.equal(posts().length, 1)
})

test('creation failure retries the same session command before sending any question', async () => {
  let creates = 0
  await page.route('**/chat/sessions', route => ++creates === 1
    ? route.abort('failed') : route.fulfill({ json: session }))
  await openChat()
  await page.getByRole('textbox', { name: 'Сообщение', exact: true }).fill('Что в последнем CSV?')
  await page.getByRole('button', { name: 'Отправить', exact: true }).click()
  await page.getByRole('alert').waitFor()
  assert.equal(posts().length, 0)
  await page.getByRole('button', { name: 'Повторить отправку', exact: true }).click()
  await page.getByText('В очереди…', { exact: true }).waitFor()
  const commands = requests.filter(request => request.method() === 'POST' && request.url().endsWith('/sessions'))
  assert.equal(commands.length, 2)
  assert.equal(commands[0].postDataJSON().command_key, commands[1].postDataJSON().command_key)
  assert.equal(posts().length, 1)
})

test('navigation during a delayed send preserves one command and restores the answer', async () => {
  let release
  const gate = new Promise(resolve => { release = resolve })
  await page.route('**/chat/sessions/*/messages', async route => {
    await gate
    session.turns = [turn()]
    await route.fulfill({ status: 202, json: turn() })
  })
  await openChat()
  await page.getByRole('textbox', { name: 'Сообщение', exact: true }).fill('Что в последнем CSV?')
  const sent = page.waitForRequest(request => request.url().endsWith('/messages'))
  await page.getByRole('button', { name: 'Отправить', exact: true }).click()
  await sent
  await page.getByRole('link', { name: 'Файлы', exact: true }).click()
  await page.getByRole('main',{name:'Файлы',exact:true}).waitFor()
  release()
  await openChat()
  await page.getByText('<b>Болт: 7</b>', { exact: true }).waitFor()
  assert.equal(posts().length, 1)
})

test('a stale read from a previous tab visit cannot overwrite refreshed history', async () => {
  await openChat(); await submit()
  session.turns = [turn()]
  await page.getByText('<b>Болт: 7</b>', { exact: true }).waitFor()
  await page.getByRole('link', { name: 'Файлы', exact: true }).click()
  let release, delayed = true
  const gate = new Promise(resolve => { release = resolve })
  await page.route('**/chat/sessions/*', async route => {
    if (delayed) {
      delayed = false
      const old = structuredClone(session)
      old.turns[0].answer = 'Устаревшая история'
      await gate
      return route.fulfill({ json: old }).catch(() => {})
    }
    return route.fulfill({ json: session })
  })
  const reading = page.waitForRequest(request => request.method() === 'GET' && request.url().includes('/chat/sessions/'))
  await openChat(); await reading
  await page.getByRole('link', { name: 'Файлы', exact: true }).click()
  session.turns[0].answer = 'Свежая история'
  await openChat()
  await page.getByText('Свежая история', { exact: true }).waitFor()
  release()
  await page.waitForTimeout(150)
  assert.equal(await page.getByText('Устаревшая история', { exact: true }).count(), 0)
  assert.equal(await page.getByText('Свежая история', { exact: true }).isVisible(), true)
})

test('lost POST response and reload retain only the command key and permit an idempotent retry', async () => {
  let attempts = 0
  await page.route('**/chat/sessions/*/messages', async route => {
    session.turns = [turn()]
    if (++attempts === 1) return route.abort('failed')
    return route.fulfill({ status: 202, json: turn() })
  })
  await openChat(); await submit()
  await page.getByRole('alert').waitFor()
  const key = posts()[0].postDataJSON().command_key
  await page.reload()
  await page.getByText('<b>Болт: 7</b>', { exact: true }).waitFor()
  assert.equal(posts().length, 1)
  assert.equal(await page.getByRole('textbox', { name: 'Сообщение', exact: true }).inputValue(), '')
  assert.equal(await page.getByRole('button', { name: 'Новый диалог', exact: true }).isDisabled(), true)
  await page.getByRole('textbox', { name: 'Сообщение', exact: true }).fill('Что в последнем CSV?')
  await page.getByRole('button', { name: 'Повторить отправку', exact: true }).click()
  await page.getByRole('button', { name: 'Новый диалог', exact: true }).waitFor({ state: 'visible' })
  await page.waitForFunction(() => !document.querySelector('textarea').disabled)
  assert.equal(posts().length, 2)
  assert.equal(posts()[1].postDataJSON().command_key, key)
  assert.equal(await page.locator('.chat-transcript').getByText('Что в последнем CSV?', { exact: true }).count(), 1)
})
