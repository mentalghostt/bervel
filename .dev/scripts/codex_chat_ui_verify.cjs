// Read-only UI verification. No provider test or document upload is performed.
const { chromium } = require('playwright')
const assert = require('node:assert/strict')
const { mkdirSync, writeFileSync } = require('node:fs')
const { resolve } = require('node:path')
;(async () => {
  const output = resolve(__dirname, '../local/codex-chat')
  mkdirSync(resolve(output, 'screenshots'), { recursive: true })
  const browser = await chromium.launch({ headless: true })
  const results = []
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } })
      const errors = []
      page.on('pageerror', error => errors.push(error.message))
      page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
      await page.goto('http://127.0.0.1/#/settings')
      await page.getByText('Файлы доступа MOX подключены только для чтения.', { exact: false }).waitFor()
      assert.equal(await page.getByRole('radio', { name: 'MOX Access', exact: true }).isChecked(), true)
      assert.equal(await page.getByRole('button', { name: 'Проверить подключение', exact: true }).isEnabled(), true)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
      await page.screenshot({ path: resolve(output, 'screenshots', `settings-mox-live-${width}.png`), fullPage: true })
      await page.getByRole('link', { name: 'Тест чата', exact: true }).click()
      await page.getByRole('heading', { name: 'Тест чата', exact: true }).waitFor()
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
      assert.deepEqual(errors, [])
      results.push({ width, provider: 'mox', overflow: false, browser_errors: errors, provider_requests: 0 })
      await page.close()
    }
    writeFileSync(resolve(output, 'live-ui.json'), JSON.stringify(results, null, 2))
    console.log(JSON.stringify(results))
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exit(1) })
