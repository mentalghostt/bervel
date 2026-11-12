const {test} = require('node:test')
const {assert, createState, fixture, browser, base} = require('./frontend-presales-fixtures.cjs')

for (const [progress,expected] of [
 [{completed_batches:2,total_batches:5,completed_lines:4,total_lines:9,completed_steps:17,last_checkpoint:'internal-ledger-key'},'Проверено порций каталога: 2 из 5'],
 [{completed_batches:0,total_batches:0,completed_lines:0,total_lines:9,completed_steps:17,last_checkpoint:'internal-ledger-key'},'Проверено позиций: 0 из 9'],
]) test('active presales shows useful progress: '+expected,()=>browser(390,async page=>{
 const state=createState(); state.revision.job={job_id:'job-1',state:'running',phase:'ai_review',error:null,stop_requested:false,progress}; await fixture(page,state)
 await page.goto(base+'/#/presales?request=request-1&revision=revision-1')
 await page.getByText('Проверяем результат анализа',{exact:true}).waitFor()
 await page.getByText(expected,{exact:true}).waitFor()
 const text=await page.locator('.presales-progress').innerText()
 assert.equal(text.includes('Сохранено шагов'),false); assert.equal(text.includes('internal-ledger-key'),false); assert.equal(state.writes.length,0)
}))
test('lost create receipt survives reload and duplicated tab and retries same body', () => browser(1440, async (page, context) => {
 const state = createState(); state.failCommand = true; await fixture(page, state); await page.goto(base + '/#/presales')
 await page.getByRole('button', {name: 'Новая заявка', exact: true}).click(); await page.getByRole('checkbox', {name: 'Заявка.xlsx'}).check(); await page.getByRole('button', {name: 'Создать заявку', exact: true}).click()
 await page.getByText(/Исход команды неизвестен/).waitFor(); const first = structuredClone(state.writes[0]); await page.reload(); await page.getByRole('button', {name: 'Повторить сохранённую команду'}).waitFor()
 const second = await context.newPage(); await fixture(second, state); await second.goto(base + '/#/presales'); await second.getByRole('button', {name: 'Повторить сохранённую команду'}).waitFor()
 state.failCommand = false; await second.getByRole('button', {name: 'Повторить сохранённую команду'}).click(); await second.waitForURL(/request=request-1/)
 await page.getByRole('button', {name: 'Повторить сохранённую команду'}).click(); await page.waitForURL(/request=request-1/)
 assert.equal(state.writes.length, 2, 'other tab consumes persisted receipt without another POST'); assert.deepEqual(state.writes[1], first)
}))
test('stop and resume commands target persisted job; failed polling preserves result', () => browser(1440, async page => {
 const state = createState(); state.revision.job = {job_id: 'job-1', state: 'running', phase: 'analysis', error: null, stop_requested: false, progress: {completed: 1, total: 2}}
 await fixture(page, state); await page.goto(base + '/#/presales?request=request-1&revision=revision-1'); await page.getByRole('button', {name: 'Остановить', exact: true}).click()
 await page.waitForTimeout(150); assert.match(state.writes[0].path, /\/jobs\/job-1\/stop$/)
 state.revision.job.state = 'stopped'; state.revision.state = 'stopped'; await page.reload(); await page.getByRole('button', {name: 'Продолжить', exact: true}).waitFor(); state.revision.job.state = 'running'; await page.getByRole('button', {name: 'Продолжить', exact: true}).click(); await page.waitForTimeout(150); assert.match(state.writes[1].path, /\/jobs\/job-1\/resume$/)
 await page.route('**/presales/requests/request-1/revisions/revision-1', route => route.abort()); await page.getByRole('alert').filter({hasText: 'Не удалось обновить данные пресейла'}).waitFor()
 assert.equal(await page.getByTestId('presales-score').innerText(), '0 / 100')
}))
test('lost settings receipt retains exact values and command key after reload', () => browser(390, async page => {
 const state = createState(); state.failCommand = true; await fixture(page, state); await page.goto(base + '/#/presales-settings'); const dialog = page.getByRole('dialog', {name: 'Настройки пресейла'}); await dialog.getByLabel('Контакты продавца').fill('Сохранить этот контакт'); await dialog.getByRole('button', {name: 'Сохранить настройки', exact: true}).click(); await dialog.getByText(/Исход команды неизвестен/).waitFor(); const first = structuredClone(state.writes[0]); await page.reload(); await dialog.getByRole('button', {name: 'Повторить сохранённую команду'}).waitFor(); state.failCommand = false; await dialog.getByRole('button', {name: 'Повторить сохранённую команду'}).click(); await dialog.getByRole('status').filter({hasText: 'Настройки сохранены'}).waitFor(); assert.deepEqual(state.writes[1], first); assert.equal(await dialog.getByLabel('Контакты продавца').inputValue(), 'Сохранить этот контакт'); await page.keyboard.press('Escape'); await dialog.waitFor({state: 'detached'}); assert.equal(await page.getByRole('button', {name: 'Повторить сохранённую команду'}).count(), 0)
}))
test('malformed API settings are rejected at the boundary without an untracked write', () => browser(390, async page => {
 const state = createState(); state.settings.settings.target_margin_percent = 50; await fixture(page, state); await page.goto(base + '/#/presales-settings'); await page.getByRole('dialog', {name: 'Настройки пресейла'}).getByRole('alert').filter({hasText: 'неожиданный формат'}).waitFor(); assert.equal(state.writes.length, 0)
}))
test('slow polling never overlaps and a late old revision cannot replace the selected one', () => browser(1440, async page => {
 const state = createState(); state.revision.job = {job_id: 'job-1', state: 'running', phase: 'analysis', error: null, stop_requested: false, progress: {}}; await fixture(page, state)
 let active = 0, maximum = 0, reads = 0
 await page.route('**/presales/requests/request-1/revisions/revision-1', async route => {active++; maximum = Math.max(maximum, active); reads++; await new Promise(resolve => setTimeout(resolve, 2200)); active--; await route.fulfill({json: state.revision}).catch(() => {})})
 await page.goto(base + '/#/presales?request=request-1&revision=revision-1'); await page.getByTestId('presales-score').waitFor(); await page.waitForTimeout(1900); assert.ok(reads >= 2)
 await page.route('**/presales/requests/request-1/revisions/revision-2', route => route.fulfill({json: {...state.revision, revision_id: 'revision-2', score: 99, job: null}}))
 await page.evaluate(() => { window.location.hash = '/presales?request=request-1&revision=revision-2' }); await page.getByTestId('presales-score').filter({hasText: '99 / 100'}).waitFor(); await page.waitForTimeout(2400)
 assert.equal(await page.getByTestId('presales-score').innerText(), '99 / 100'); assert.equal(maximum, 1)
}))
