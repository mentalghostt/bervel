const {test} = require('node:test')
const assert = require('node:assert/strict')
const http = require('node:http')
const {chromium} = require('playwright')

const stockId = '11111111-1111-4111-8111-111111111111'
const snapshotId = '22222222-2222-4222-8222-222222222222'
const newerId = '33333333-3333-4333-8333-333333333333'
const time = '2026-11-05T09:00:00Z'
const stock = {artifact_id: stockId, filename: 'Проверочный склад.csv', media_type: 'text/csv', size: 100, created_at: time, usage_role: 'stock'}
const profile = (count = 3076) => ({stock_artifact_id: stockId, stock_sha256: 'a'.repeat(64),
  products: Array.from({length: count}, (_, i) => ({id: `product-${i + 1}`, sku: `B${i + 1}`, name: `Болт ${i + 1}`, unit: 'шт', attributes: {ГОСТ: '7798-70'}, enabled: true, manufactured_before: null, evidence: `warehouse:${stockId}:row:${i + 2}`})),
  experimental_products: [], include_experimental: false, production: [],
  resources: [{group_id: 'Линия 1', calendar: [0, 1, 2, 3, 4], free_capacity_by_day: {}, confirmed_at: null}],
  scoring_policy: {version: 'score-v1', target_margin: '0.30', high_score: 90, max_age_by_source: {stock: '14', production: null, resources: null, costs: null}, future_policy: {enabled: false, factors: ['0', 1, null]}}})

async function withBrowser(width, callback) {
  const upstream = new URL(process.env.BERVEL_FRONTEND_URL || 'http://frontend')
  const proxy = http.createServer((req, res) => {
    const transport = http.request(new URL(req.url, upstream), {method: req.method, headers: req.headers}, response => {res.writeHead(response.statusCode, response.headers); response.pipe(res)})
    transport.on('error', () => {res.writeHead(502);res.end()})
    req.pipe(transport)
  })
  await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve))
  const browser = await chromium.launch({executablePath:process.env.BERVEL_BROWSER_PATH,headless: true, channel: process.env.BERVEL_BROWSER_CHANNEL})
  try {
    const page = await browser.newPage({viewport: {width, height: 1000}})
    page.setDefaultTimeout(15000)
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    await callback(page, `http://127.0.0.1:${proxy.address().port}`)
    assert.deepEqual(errors, [])
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'page must fit viewport; tables scroll within their own region')
  } finally {await browser.close();await new Promise(resolve => proxy.close(resolve))}
}

for (const width of [1440, 390]) test(`company settings: preview, edit, exact recovery and resources at ${width}px`, () => withBrowser(width, async (page, origin) => {
  const sent = []
  let saved = null
  let loseReply = true
  await page.route('**/api/v0.1/**', route => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    if (path === '/api/v0.1/artifacts') return route.fulfill({json: {artifacts: [stock, {...stock, artifact_id: newerId, filename: 'Не склад.csv', usage_role: 'general'}], next_cursor: null}})
    if (path.endsWith('/settings/company/preview')) {
      assert.equal(method, 'POST')
      assert.deepEqual(route.request().postDataJSON(), {stock_artifact_id: stockId, base_snapshot_id: null})
      return route.fulfill({json: {profile: profile(), warnings: ['Производственные данные не подтверждены.']}})
    }
    if (path.endsWith('/settings/company') && method === 'GET') return route.fulfill({json: {snapshot: saved}})
    if (path.endsWith('/settings/company') && method === 'PUT') {
      const body = route.request().postDataJSON();sent.push(body)
      saved = {id: snapshotId, created_at: time, profile: body.profile}
      if (loseReply) {loseReply = false;return route.abort('failed')}
      return route.fulfill({json: {snapshot: saved}})
    }
    return route.fulfill({status: 404, json: {error: {message: 'Unexpected fixture route'}}})
  })
  await page.goto(origin + '/#/company-settings')
  await page.getByRole('main', {name: 'Настройки предприятия', exact: true}).waitFor()
  const csv = page.getByLabel('Складской CSV', {exact: true})
  await csv.selectOption(stockId)
  assert.equal(await csv.locator('option').count(), 3)
  await page.getByRole('button', {name: 'Предпросмотр импорта'}).click()
  await page.getByText('Производственные данные не подтверждены.', {exact: true}).waitFor()
  assert.equal(sent.length, 0, 'preview does not save')
  const table = page.getByRole('region', {name: 'Таблица продукции', exact: true})
  assert.equal(await table.locator('tbody tr').count(), 25)
  assert.equal(await page.getByText('Правила оценки и свежесть данных', {exact: true}).count(), 0)
  assert.equal(await page.getByLabel('Целевая маржа', {exact: true}).count(), 0)
  assert.equal(await page.getByLabel('Порог высокой оценки', {exact: true}).count(), 0)
  await page.getByRole('heading', {name: 'Производственные мощности', exact: true}).waitFor()
  assert.equal(await page.getByText('Ранее производили', {exact: true}).count(), 0)
  await page.getByRole('button', {name: 'Далее', exact: true}).click()
  await page.getByLabel('Название продукции B26', {exact: true}).waitFor()
  await page.getByLabel('Найти продукцию', {exact: true}).fill('Болт 3076')
  assert.equal(await table.locator('tbody tr').count(), 1)
  await page.getByLabel('Включить Болт 3076 в поиск', {exact: true}).uncheck()
  await table.getByRole('button',{name:'Редактировать данные Болт 3076',exact:true}).click()
  await page.getByRole('button', {name: 'Добавить возможность выпуска'}).click()
  await page.getByLabel('Станок или линия', {exact: true}).selectOption('Линия 1')
  assert.equal(await page.getByLabel('Выпуск за рабочий день').inputValue(), '')
  assert.equal(await page.getByLabel('Когда подтверждено', {exact: true}).count(), 0)
  await page.getByLabel('Выпуск за рабочий день').fill('120.5')
  await page.getByLabel('Себестоимость за единицу').fill('4.25')
  await page.getByLabel('Валюта', {exact: true}).fill('RUB')
  await page.getByLabel('Подготовка, рабочих дней').fill('1')
  await page.getByLabel('Стоимость подготовки', {exact: true}).fill('1500')
  await page.getByLabel('Материалы готовы к').fill('2026-11-06T09:00')
  await page.getByLabel('Основание производственных данных', {exact: true}).fill('Технологическая карта № 12')
  await page.getByRole('button', {name: 'Добавить экспериментальный продукт'}).click()
  await page.getByLabel('Название экспериментального продукта', {exact: true}).fill('Болт с новым покрытием')
  await page.getByLabel('Единица экспериментального продукта', {exact: true}).fill('шт')
  await page.getByLabel('Включить эксперимент Болт с новым покрытием', {exact: true}).check()
  await page.getByLabel('Учитывать экспериментальную продукцию в новых прогонах').check()
  assert.equal(await page.getByLabel('Связанный продукт', {exact: true}).count(), 0)
  await page.getByLabel('Свободная доля рабочего дня по умолчанию').fill('0.5')
  assert.equal(await page.getByLabel('Доступность подтверждена', {exact: true}).count(), 0)
  assert.equal(await page.getByText(/Исключения по датам/).count(), 0)
  const resourceActive = page.getByLabel('Станок или линия активны', {exact: true})
  assert.equal(await resourceActive.isChecked(), true)
  await resourceActive.uncheck()
  await page.getByLabel('Пн', {exact: true}).uncheck()
  if (process.env.BERVEL_SCREENSHOTS) await page.screenshot({path: `${process.env.BERVEL_SCREENSHOTS}/company-settings-${width}.png`, fullPage: true})
  await page.getByRole('button', {name: 'Сохранить профиль', exact: true}).click()
  await page.getByRole('button', {name: 'Повторить сохранение', exact: true}).waitFor()
  assert.equal(await page.getByLabel('Включить Болт 3076 в поиск', {exact: true}).isDisabled(), true)
  assert.equal(sent.length, 1)
  assert.equal(sent[0].base_snapshot_id, null)
  assert.equal(sent[0].profile.products.length, 3076)
  assert.equal(sent[0].profile.products[3075].enabled, false)
  assert.equal(sent[0].profile.products[3075].manufactured_before, null, 'editing preserves legacy data without requiring manufacture confirmation')
  assert.equal(sent[0].profile.production[0].units_per_workday, '120.5')
  assert.equal(sent[0].profile.production[0].setup_cost, '1500')
  assert.equal(Object.hasOwn(sent[0].profile.experimental_products[0], 'product_id'), false)
  assert.equal(sent[0].profile.include_experimental, true)
  assert.equal(sent[0].profile.resources[0].default_daily_capacity, '0.5')
  assert.equal(sent[0].profile.resources[0].enabled, false)
  assert.deepEqual(sent[0].profile.resources[0].calendar, [1,2,3,4])
  assert.deepEqual(sent[0].profile.resources[0].free_capacity_by_day, {})
  assert.equal(sent[0].profile.resources[0].confirmed_at, null)
  assert.deepEqual(sent[0].profile.scoring_policy, profile().scoring_policy, 'editing operator fields preserves the complete hidden scoring policy')
  await page.reload()
  await page.getByRole('button', {name: 'Повторить сохранение', exact: true}).click()
  await page.getByRole('status').filter({hasText: 'Профиль сохранён.'}).waitFor()
  assert.deepEqual(sent[1], sent[0], 'retry after reload must retain payload and command key even when the original command committed')
  assert.deepEqual(sent[1].profile.scoring_policy, profile().scoring_policy)
  assert.equal(await page.evaluate(() => sessionStorage.getItem('bervel.company-settings.pending.v1')), null)
  assert.equal(await page.getByRole('button', {name: 'Сохранить профиль', exact: true}).isDisabled(), true)
  await page.getByRole('button', {name: 'Добавить продукт', exact: true}).click()
  const added = table.locator('tbody .company-product-row').last()
  await added.getByRole('textbox', {name: /^Название продукции /}).fill('Деталь без остатка')
  assert.equal(await added.getByRole('textbox', {name: /^Единица /}).inputValue(), 'шт')
  await page.getByRole('button', {name: 'Сохранить профиль', exact: true}).click()
  await page.getByRole('status').filter({hasText: 'Профиль сохранён.'}).waitFor()
  assert.equal(sent[2].profile.products.length, 3077)
  assert.equal(sent[2].profile.products.at(-1).sku, null)
  assert.equal(Object.hasOwn(sent[2].profile.products.at(-1), 'manufactured_before'), false)
  assert.equal(Object.hasOwn(sent[2].profile.products.at(-1), 'evidence'), false)
  assert.equal(sent[2].profile.products.at(-1).enabled, true)
  assert.match(sent[2].profile.products.at(-1).id, /^[\da-f-]{36}$/)
  await page.getByLabel('Найти продукцию', {exact: true}).fill('Деталь без остатка')
  await page.getByLabel('Артикул (необязательно)', {exact: true}).fill('MANUAL-BOLT')
  if (process.env.BERVEL_SCREENSHOTS) await page.screenshot({path: `${process.env.BERVEL_SCREENSHOTS}/company-settings-manual-${width}.png`, fullPage: true})
  await page.getByRole('button', {name: 'Сохранить профиль', exact: true}).click()
  await page.getByRole('status').filter({hasText: 'Профиль сохранён.'}).waitFor()
  assert.equal(sent[3].profile.products.at(-1).sku, 'MANUAL-BOLT')
}))

test('company settings: a stale snapshot cannot overwrite another editor', () => withBrowser(1440, async (page, origin) => {
  let current = {id: snapshotId, created_at: time, profile: profile(1)}
  const writes = []
  await page.route('**/api/v0.1/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/artifacts')) return route.fulfill({json: {artifacts: [stock], next_cursor: null}})
    if (route.request().method() === 'GET') return route.fulfill({json: {snapshot: current}})
    writes.push(route.request().postDataJSON())
    current = {id: newerId, created_at: time, profile: {...profile(1), include_experimental: true}}
    return route.fulfill({status: 409, json: {error: {code: 'SETTINGS_CONFLICT', message: 'Сохранена другая версия.'}}})
  })
  await page.goto(origin + '/#/company-settings')
  await page.getByLabel('Включить Болт 1 в поиск', {exact: true}).uncheck()
  await page.getByRole('button', {name: 'Сохранить профиль', exact: true}).click()
  await page.getByRole('button', {name: 'Заменить правки актуальной версией'}).waitFor()
  assert.equal(writes.length, 1)
  assert.equal(writes[0].base_snapshot_id, snapshotId)
  assert.equal(await page.getByLabel('Включить Болт 1 в поиск', {exact: true}).isDisabled(), true)
  assert.equal(await page.getByRole('button', {name: 'Повторить сохранение', exact: true}).count(), 0)
  await page.getByRole('button', {name: 'Заменить правки актуальной версией'}).click()
  await page.getByRole('status').filter({hasText: 'Актуальная версия загружена.'}).waitFor()
  assert.equal(await page.getByLabel('Включить Болт 1 в поиск', {exact: true}).isChecked(), true)
  assert.equal(await page.getByLabel('Учитывать экспериментальную продукцию в новых прогонах').isChecked(), true)
  assert.equal(writes.length, 1, 'conflict resolution never retries with a fresh key automatically')
}))

test('company settings: rejected save is editable, malformed response remains recoverable', () => withBrowser(390, async (page, origin) => {
  let attempt = 0
  await page.route('**/api/v0.1/**', route => {
    if (new URL(route.request().url()).pathname.endsWith('/artifacts')) return route.fulfill({json: {artifacts: [stock], next_cursor: null}})
    if (route.request().method() === 'GET') return route.fulfill({json: {snapshot: {id: snapshotId, created_at: time, profile: profile(1)}}})
    attempt++
    return attempt === 1 ? route.fulfill({status: 422, json: {error: {message: 'Данные отклонены.'}}}) : route.fulfill({json: {snapshot: {id: newerId, created_at: time, profile: {invalid: true}}}})
  })
  await page.goto(origin + '/#/company-settings')
  const toggle = page.getByLabel('Включить Болт 1 в поиск', {exact: true})
  await toggle.uncheck()
  await page.getByRole('button', {name: 'Сохранить профиль', exact: true}).click()
  await page.getByRole('alert').filter({hasText: 'Данные отклонены.'}).waitFor()
  assert.equal(await toggle.isDisabled(), false)
  assert.equal(await page.evaluate(() => sessionStorage.getItem('bervel.company-settings.pending.v1')), null)
  await page.getByRole('button', {name: 'Сохранить профиль', exact: true}).click()
  await page.getByRole('button', {name: 'Повторить сохранение', exact: true}).waitFor()
  assert.equal(await toggle.isDisabled(), true)
  assert.equal(attempt, 2)
}))
