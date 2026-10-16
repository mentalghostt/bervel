const assert = require('node:assert/strict')
const { before, after, beforeEach, afterEach, test } = require('node:test')
const { mkdirSync } = require('node:fs')
const { resolve } = require('node:path')
const { chromium } = require('playwright')

const baseUrl = process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5173'
const screenshots = resolve(__dirname, '../local/frontend/screenshots')
const csv = (name = 'requirements.csv', size) => ({
  name,
  mimeType: 'text/csv',
  buffer: size === undefined
    ? Buffer.from('line,name,quantity,unit\n1,Bolt,10,pcs\n')
    : Buffer.alloc(size, 'x'),
})
let browser, context, page, errors, uploads

before(async () => {
  mkdirSync(screenshots, { recursive: true })
  browser = await chromium.launch({
    headless: true,
    channel: process.env.BERVEL_BROWSER_CHANNEL || 'msedge',
  })
})
after(async () => { await browser?.close() })
beforeEach(async () => {
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  page = await context.newPage()
  page.setDefaultTimeout(2000)
  errors = []
  uploads = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('request', request => {
    if (request.method() !== 'GET' || ['fetch', 'xhr'].includes(request.resourceType())) {
      uploads.push(request.url())
    }
  })
  await page.goto(baseUrl)
  await page.waitForLoadState('networkidle')
})
afterEach(async () => {
  try {
    assert.deepEqual(errors, [], 'The page must have no browser errors')
    assert.deepEqual(uploads, [], 'Selecting a file must never send the file or call the API')
  } finally {
    await context.close()
  }
})

async function drop(files, target = page.getByRole('region', { name: 'Выбор CSV-файла' })) {
  const transfer = await page.evaluateHandle(files => {
    const data = new DataTransfer()
    for (const file of files) {
      data.items.add(new File(['line,name,quantity,unit\n1,Bolt,10,pcs'], file, { type: 'text/csv' }))
    }
    return data
  }, files)
  try {
    await target.dispatchEvent('dragenter', { dataTransfer: transfer })
    await target.dispatchEvent('dragover', { dataTransfer: transfer })
    return await target.evaluate((element, dataTransfer) => {
      const event = new DragEvent('drop', { dataTransfer, bubbles: true, cancelable: true })
      element.dispatchEvent(event)
      return event.defaultPrevented
    }, transfer)
  } finally {
    await transfer.dispose()
  }
}

test('initial screen waits for a file, key and explicit database upload', async () => {
  const upload = page.getByRole('button', { name: 'Загрузить в БД', exact: true })
  await upload.waitFor()
  assert.equal(await upload.isDisabled(), true)
  assert.equal(await page.getByRole('button', { name: 'Выбрать CSV', exact: true }).isVisible(), true)
  assert.equal(await page.getByText('Добавьте CSV и ключ доступа, затем нажмите «Загрузить в БД».', { exact: true }).isVisible(), true)
  await page.screenshot({ path: resolve(screenshots, 'csv-empty-desktop.png'), fullPage: true })
})

test('native file chooser works from the keyboard and displays file name and size', async () => {
  await page.keyboard.press('Tab')
  assert.equal(await page.getByRole('button', { name: 'Выбрать CSV', exact: true }).evaluate(el => el === document.activeElement), true)
  const chooserPromise = page.waitForEvent('filechooser')
  await page.keyboard.press('Enter')
  await (await chooserPromise).setFiles(csv('позиции.csv'))
  await page.getByText('позиции.csv', { exact: true }).waitFor()
  assert.match(await page.getByRole('status').filter({ hasText: 'Файл выбран' }).innerText(), /байт/)
  assert.equal(await page.getByRole('button', { name: 'Загрузить в БД' }).isDisabled(), true)
  await page.screenshot({ path: resolve(screenshots, 'csv-selected-desktop.png'), fullPage: true })
})

test('file can be replaced, removed, and selected again; cancelling retains selection', async () => {
  const input = page.locator('input[type="file"]')
  await input.setInputFiles(csv('first.csv'))
  await page.getByText('first.csv', { exact: true }).waitFor()
  await input.setInputFiles([])
  assert.equal(await page.getByText('first.csv', { exact: true }).isVisible(), true)
  await input.setInputFiles(csv('second.csv'))
  await page.getByText('second.csv', { exact: true }).waitFor()
  assert.equal(await page.getByText('first.csv', { exact: true }).count(), 0)
  await page.getByRole('button', { name: 'Убрать выбранный файл' }).click()
  assert.equal(await page.getByText('second.csv', { exact: true }).count(), 0)
  await input.setInputFiles(csv('second.csv'))
  await page.getByText('second.csv', { exact: true }).waitFor()
})

test('drag and drop selects one file, rejects multiple files, and guards outside drops', async () => {
  await drop(['dropped.csv'])
  await page.getByText('dropped.csv', { exact: true }).waitFor()
  await drop(['one.csv', 'two.csv'])
  await page.getByRole('alert').waitFor()
  assert.match(await page.getByRole('alert').innerText(), /один файл/)
  assert.equal(await page.getByText('dropped.csv', { exact: true }).count(), 0)
  assert.equal(await drop(['outside.csv'], page.locator('header')), true)
  assert.equal(page.url(), baseUrl + '/')
  assert.equal(await page.getByText('outside.csv', { exact: true }).count(), 0)
})

test('drag feedback stays visible over child controls and resets when leaving the area', async () => {
  const transfer = await page.evaluateHandle(() => {
    const data = new DataTransfer()
    data.items.add(new File(['content'], 'dragged.csv', { type: 'text/csv' }))
    return data
  })
  const area = page.getByRole('region', { name: 'Выбор CSV-файла' })
  const button = page.getByRole('button', { name: 'Выбрать CSV', exact: true })
  try {
    await area.dispatchEvent('dragenter', { dataTransfer: transfer })
    await page.getByText('Отпустите CSV здесь', { exact: true }).waitFor()
    await button.dispatchEvent('dragenter', { dataTransfer: transfer })
    await button.dispatchEvent('dragleave', { dataTransfer: transfer })
    assert.equal(await page.getByText('Отпустите CSV здесь', { exact: true }).isVisible(), true)
    await area.dispatchEvent('dragleave', { dataTransfer: transfer })
    await page.getByText('Перетащите CSV-файл', { exact: true }).waitFor()
  } finally {
    await transfer.dispose()
  }
})

test('rejects wrong extension, empty files, and files above 5 MiB without retaining stale selection', async () => {
  const input = page.locator('input[type="file"]')
  for (const [file, message] of [
    [csv('wrong.xlsx'), /\.csv/],
    [csv('empty.csv', 0), /пуст/],
    [csv('too-big.csv', 5 * 1024 * 1024 + 1), /5 МиБ/],
  ]) {
    await input.setInputFiles(csv('previous.csv'))
    await page.getByText('previous.csv', { exact: true }).waitFor()
    await input.setInputFiles(file)
    await page.getByRole('alert').waitFor()
    assert.match(await page.getByRole('alert').innerText(), message)
    assert.equal(await page.getByText('previous.csv', { exact: true }).count(), 0)
  }
  await page.screenshot({ path: resolve(screenshots, 'csv-error-desktop.png'), fullPage: true })
})

test('accepts uppercase CSV at the exact size limit and keeps mobile layout within the viewport', async () => {
  const name = 'очень-длинное-название-позиций-'.repeat(5) + '.CSV'
  await page.locator('input[type="file"]').setInputFiles(csv(name, 5 * 1024 * 1024))
  await page.getByText(name, { exact: true }).waitFor()
  assert.equal(await page.getByRole('alert').count(), 0)
  await page.setViewportSize({ width: 375, height: 812 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true)
  await page.screenshot({ path: resolve(screenshots, 'csv-selected-mobile.png'), fullPage: true })
})
