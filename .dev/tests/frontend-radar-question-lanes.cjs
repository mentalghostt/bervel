const {test} = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const {pathToFileURL} = require('node:url')
const {chromium} = require('playwright')
const rid = '11111111-1111-4111-8111-111111111111'
const other = '22222222-2222-4222-8222-222222222222'
const sid = '33333333-3333-4333-8333-333333333333'
const qid = '44444444-4444-4444-8444-444444444444'
const aid = '55555555-5555-4555-8555-555555555555'
const now = '2026-11-08T10:00:00Z'
const command = {path: `/${rid}/question`, body: {command_key: 'saved-question', analysis_id: aid, question: 'Почему?'}}
const card = {analysis_id: aid, candidate_id: aid, state: 'needs_data', evaluated_at: now,
  tender: {title: 'Сохранённый тендер', number: '1', link: null}, analysis: null, review: null,
  assessment: null, fulfillment: null, rating: {status: 'unknown', score: null}}

test('pending commands validate exact lane ownership including rating', async () => {
  const api = await import(pathToFileURL(path.resolve('frontend/src/aiRadarRunsApi.ts')).href)
  const analysis = {path: `/${rid}/rating`, body: {command_key: 'saved-rating'}}
  assert.deepEqual(api.readRunAttempts({question: command, analysis}), {question: command, analysis})
  assert.equal(api.commandLane(command), 'question')
  assert.equal(api.commandLane(analysis), 'analysis')
  assert.throws(() => api.readRunAttempts({analysis: command}))
  assert.throws(() => api.readRunAttempts({question: analysis}))
})

async function fixture(action, {state = 'running', initial, storageFailure = false, readFailure = false, width = 1440} = {}) {
  const browser = await chromium.launch({headless: true})
  try {
    const page = await browser.newPage({viewport: {width, height: 1000}})
    page.setDefaultTimeout(7000)
    const errors = [], commands = []
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(({initial, storageFailure, readFailure}) => {
      if (initial && !sessionStorage.getItem('fixture-ready')) {
        for (const [key, value] of Object.entries(initial)) sessionStorage.setItem(key, JSON.stringify(value))
        sessionStorage.setItem('fixture-ready', '1')
      }
      if (storageFailure) {
        const set = Storage.prototype.setItem
        Storage.prototype.setItem = function(key, value) {
          if (key === 'bervel.runs.pending.question.v2') throw Error('Question storage denied')
          return set.call(this, key, value)
        }
      }
      if (readFailure) {
        const get = Storage.prototype.getItem
        Storage.prototype.getItem = function(key) {
          if (key === 'bervel.runs.pending.question.v2') throw Error('Question storage read denied')
          return get.call(this, key)
        }
      }
    }, {initial, storageFailure, readFailure})
    const hooks = {question: null, launch: null, answered: false}
    await page.route('**/api/v0.1/**', async route => {
      const req = route.request(), pathname = new URL(req.url()).pathname
      if (pathname.endsWith('/settings/company')) return route.fulfill({json: {snapshot: {id: sid, created_at: now, profile: {
        stock_artifact_id: aid, stock_sha256: 'a'.repeat(64), products: [], experimental_products: [],
        include_experimental: false, production: [], resources: [], scoring_policy: {}}}}})
      if (pathname.endsWith('/runs/history')) return route.fulfill({json: {entries: [
        {id: rid, kind: 'run', title: 'Первый прогон', created_at: now, state, card_count: 1},
        {id: other, kind: 'run', title: 'Другой прогон', created_at: now, state: 'complete', card_count: 1}], next_offset: null}})
      if (pathname.endsWith('/question')) {commands.push(req.postDataJSON());return hooks.question ? hooks.question(route) : route.fulfill({status: 202, json: {job_id: qid, state: 'queued'}})}
      if (pathname.endsWith('/runs') && req.method() === 'POST') {commands.push(req.postDataJSON());return hooks.launch ? hooks.launch(route) : route.fulfill({status: 202, json: {run_id: other, job_id: other, state: 'queued'}})}
      if (pathname.endsWith('/jobs/' + qid)) return route.fulfill({json: {job_id: qid, state: hooks.answered ? 'loaded' : 'running'}})
      for (const id of [rid, other]) if (pathname.endsWith('/runs/' + id)) return route.fulfill({json: {run_id: id,
        settings_snapshot_id: sid, created_at: now, data: {state: id === rid ? state : 'complete', phase: 'details'}, cards: [card],
        reports: id === rid && hooks.answered ? [{job_id: qid, kind: 'question', analysis_id: aid, question: 'Почему?',
          result: {state: 'complete', answer: 'Проверено.', recommendation: 'Нужна проверка.', risks: [], assumptions: [], evidence_refs: []}}] : [], candidates: []}})
      return route.fulfill({status: 404, json: {error: {message: 'Unexpected ' + pathname}}})
    })
    await page.goto((process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5193') + '/#/ai-tender-radar?run_id=' + rid + '&analysis_id=' + aid)
    await page.getByRole('textbox', {name: 'Вопрос по тендеру', exact: true}).waitFor()
    await action(page, hooks, commands)
    assert.deepEqual(errors, [])
  } finally {await browser.close()}
}

test('question submits during analysis, allows navigation and its late receipt never selects its run', () => fixture(async (page, hooks, commands) => {
  let finish
  hooks.question = route => new Promise(resolve => {finish = async () => {await route.fulfill({status: 202, json: {job_id: qid, state: 'queued'}});resolve()}})
  await page.getByRole('textbox', {name: 'Вопрос по тендеру', exact: true}).fill('Почему такой результат?')
  assert.equal(await page.getByRole('button', {name: 'Спросить', exact: true}).isEnabled(), true)
  await page.getByRole('button', {name: 'Спросить', exact: true}).click()
  await page.waitForFunction(() => !!sessionStorage.getItem('bervel.runs.pending.question.v2'))
  await page.getByRole('button', {name: 'Закрыть диалог', exact: true}).click()
  await page.getByRole('button').filter({hasText: 'Другой прогон'}).first().click()
  assert.equal(await page.getByRole('button').filter({hasText: 'Другой прогон'}).first().getAttribute('aria-pressed'), 'true')
  await finish()
  await page.waitForFunction(() => !sessionStorage.getItem('bervel.runs.pending.question.v2'))
  assert.equal(await page.getByRole('button').filter({hasText: 'Другой прогон'}).first().getAttribute('aria-pressed'), 'true')
  assert.equal(commands.length, 1)
  await page.screenshot({path: path.resolve('.dev/local/radar-three-fixes-2026-11-08/screenshots/task-3-question-1440.png'), fullPage: true})
}))

test('unknown question survives reload without blocking analysis; exact retry keeps its payload', () => fixture(async (page, hooks, commands) => {
  hooks.question = route => route.abort('failed')
  await page.getByRole('textbox', {name: 'Вопрос по тендеру', exact: true}).fill('Почему?')
  await page.getByRole('button', {name: 'Спросить', exact: true}).click()
  await page.getByRole('button', {name: 'Повторить вопрос', exact: true}).waitFor()
  await page.reload()
  await page.getByRole('button', {name: 'Повторить вопрос', exact: true}).waitFor()
  await page.getByRole('button', {name: 'Закрыть диалог', exact: true}).click()
  assert.equal(await page.getByRole('button', {name: 'Новый анализ', exact: true}).isEnabled(), true)
  hooks.question = null
  await page.getByRole('button', {name: 'Повторить вопрос', exact: true}).click()
  await page.waitForFunction(() => !sessionStorage.getItem('bervel.runs.pending.question.v2'))
  assert.deepEqual(commands[0], commands[1])
  assert.equal(await page.getByRole('button', {name: 'Новый анализ', exact: true}).isEnabled(), true)
}, {state: 'complete'}))

test('two pending commands reload separately and legacy question is migrated losslessly', () => fixture(async (page) => {
  await page.getByRole('button', {name: 'Повторить вопрос', exact: true}).waitFor()
  await page.getByRole('button', {name: 'Повторить сохранённую команду', exact: true}).waitFor()
  const stored = await page.evaluate(() => ({question: JSON.parse(sessionStorage.getItem('bervel.runs.pending.question.v2')),
    analysis: JSON.parse(sessionStorage.getItem('bervel.runs.pending.analysis.v2')), legacy: sessionStorage.getItem('bervel.runs.pending')}))
  assert.deepEqual(stored.question, command)
  assert.equal(stored.analysis.body.command_key, 'saved-analysis')
  assert.equal(stored.legacy, null)
  await page.reload()
  await page.getByRole('button', {name: 'Повторить вопрос', exact: true}).waitFor()
  await page.getByRole('button', {name: 'Повторить сохранённую команду', exact: true}).waitFor()
}, {state: 'complete', initial: {'bervel.runs.pending': command,
  'bervel.runs.pending.analysis.v2': {path: `/${rid}/rating`, body: {command_key: 'saved-analysis'}}}}))

test('question storage failure sends nothing and analysis remains available', () => fixture(async (page, _hooks, commands) => {
  await page.getByRole('textbox', {name: 'Вопрос по тендеру', exact: true}).fill('Почему?')
  await page.getByRole('button', {name: 'Спросить', exact: true}).click()
  await page.getByText('Команда не отправлена:', {exact: false}).waitFor()
  assert.equal(commands.length, 0)
  await page.getByRole('button', {name: 'Закрыть диалог', exact: true}).click()
  assert.equal(await page.getByRole('button', {name: 'Новый анализ', exact: true}).isEnabled(), true)
}, {state: 'complete', storageFailure: true}))

test('unreadable question recovery blocks only its own lane', () => fixture(async (page) => {
  assert.equal(await page.getByRole('textbox', {name: 'Вопрос по тендеру', exact: true}).isDisabled(), true)
  await page.getByRole('button', {name: 'Закрыть диалог', exact: true}).click()
  assert.equal(await page.getByRole('button', {name: 'Новый анализ', exact: true}).isEnabled(), true)
}, {state: 'complete', readFailure: true}))

test('completed question receipt clears while its run is not selected', () => fixture(async (page, hooks) => {
  await page.getByRole('textbox', {name: 'Вопрос по тендеру', exact: true}).fill('Почему?')
  await page.getByRole('button', {name: 'Спросить', exact: true}).click()
  await page.waitForFunction(() => !sessionStorage.getItem('bervel.runs.pending.question.v2'))
  await page.getByRole('button', {name: 'Закрыть диалог', exact: true}).click()
  await page.getByRole('button').filter({hasText: 'Другой прогон'}).first().click()
  hooks.answered = true
  await page.waitForFunction(() => !JSON.parse(sessionStorage.getItem('bervel.runs.operations.v1') || '[]').some(o => o.kind === 'question'))
  await page.locator('.ai-radar-card-list > article').first().click()
  assert.equal(await page.getByRole('button', {name: 'Спросить', exact: true}).isEnabled(), false, 'Empty question cannot submit')
  await page.getByRole('textbox', {name: 'Вопрос по тендеру', exact: true}).fill('Следующий вопрос')
  assert.equal(await page.getByRole('button', {name: 'Спросить', exact: true}).isEnabled(), true)
}, {state: 'complete'}))

test('mobile recovery keeps both exact commands visible without overflow', () => fixture(async page => {
  await page.getByRole('button', {name: 'Закрыть диалог', exact: true}).click()
  await page.getByRole('button', {name: 'Повторить вопрос', exact: true}).waitFor()
  await page.getByRole('button', {name: 'Повторить сохранённую команду', exact: true}).waitFor()
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
  await page.screenshot({path: path.resolve('.dev/local/radar-three-fixes-2026-11-08/screenshots/task-3-recovery-390.png'), fullPage: true})
}, {state: 'complete', width: 390, initial: {'bervel.runs.pending.question.v2': command,
  'bervel.runs.pending.analysis.v2': {path: `/${rid}/rating`, body: {command_key: 'saved-analysis'}}}}))
