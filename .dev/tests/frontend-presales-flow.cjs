const {test} = require('node:test')
const {assert, path, directory, createState, fixture, browser, base} = require('./frontend-presales-fixtures.cjs')
for (const width of [1440, 390]) {
 test('presales settings explicitly save defaults and contacts at ' + width, () => browser(width, async page => {
  const state = createState(); state.settings.snapshot_id = null; state.settings.requires_save = true; await fixture(page, state)
  await page.goto(base + '/#/presales-settings'); await page.getByLabel('Целевая маржа, %', {exact: true}).waitFor()
  assert.equal(await page.getByLabel('Целевая маржа, %', {exact: true}).inputValue(), '50'); assert.equal(await page.getByLabel('Минимальная маржа спецзаказа, %').inputValue(), '70'); assert.equal(state.writes.length, 0)
  await page.getByLabel('Контакты продавца').fill('sales@bervel.test'); await page.getByLabel('Условия КП').fill('Самовывоз')
  await page.getByRole('button', {name: 'Сохранить настройки', exact: true}).click(); await page.getByRole('status').filter({hasText: 'Настройки сохранены'}).waitFor()
  assert.deepEqual(state.writes[0].body.settings, {target_margin_percent: '50', special_min_margin_percent: '70', seller_contacts: 'sales@bervel.test', offer_terms: 'Самовывоз'})
  await page.screenshot({path: path.join(directory, 'settings-' + width + '.png'), fullPage: true})
 }))
 test('presales creates one request from paginated multiselect and previews existing files at ' + width, () => browser(width, async page => {
  const state = createState(); await fixture(page, state); await page.goto(base + '/#/presales')
  await page.getByRole('button', {name: 'Новая заявка', exact: true}).click(); const dialog = page.getByRole('dialog', {name: 'Файлы заявки'})
  await dialog.getByRole('checkbox', {name: 'Заявка.xlsx'}).check(); await dialog.getByRole('button', {name: 'Показать ещё файлы'}).click(); await dialog.getByRole('checkbox', {name: 'Уточнение.txt'}).check()
  await dialog.getByRole('searchbox', {name: 'Найти файл'}).fill('Уточнение'); assert.equal(await dialog.getByRole('checkbox').count(), 1)
  await dialog.getByRole('button', {name: 'Просмотреть Уточнение.txt'}).click(); await dialog.getByText('Оригинал заявки: болт М12', {exact: true}).waitFor()
  await page.screenshot({path: path.join(directory, 'picker-' + width + '.png'), fullPage: true})
  await dialog.getByRole('button', {name: 'Создать заявку', exact: true}).click(); await page.getByRole('heading', {name: 'Крепёж для цеха № 4', exact: true}).waitFor()
  assert.equal(state.writes.length, 1); assert.deepEqual(state.writes[0].body.artifact_ids, ['file-1', 'file-2']); assert.ok(state.writes[0].body.command_key)
  assert.match(page.url(), /request=request-1&revision=revision-1/)
 }))
}
test('invalid margins never submit; first request requires saved settings', () => browser(390, async page => {
 const state = createState(); state.settings.requires_save = true; state.settings.snapshot_id = null; await fixture(page, state); await page.goto(base + '/#/presales'); await page.getByText(/Начальные значения маржи/).waitFor(); assert.equal(await page.getByRole('button', {name: 'Новая заявка', exact: true}).isDisabled(), true)
 await page.goto(base + '/#/presales-settings'); await page.getByLabel('Целевая маржа, %', {exact: true}).fill('100'); assert.equal(await page.getByRole('button', {name: 'Сохранить настройки', exact: true}).isDisabled(), true); assert.equal(state.writes.length, 0)
 await page.getByLabel('Целевая маржа, %', {exact: true}).fill('70'); await page.getByLabel('Минимальная маржа спецзаказа, %').fill('50'); assert.equal(await page.getByRole('button', {name: 'Сохранить настройки', exact: true}).isDisabled(), true)
}))
