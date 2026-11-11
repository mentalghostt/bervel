const {test} = require('node:test')
const assert = require('node:assert/strict')
const {chromium} = require('playwright')
const base = process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5173'

for (const width of [1440, 390]) test(`retired links stay inactive and AI radar opens at ${width}px`, async () => {
  const browser = await chromium.launch({headless:true})
  try {
    const page = await browser.newPage({viewport:{width,height:900}})
    page.setDefaultTimeout(5000)
    const requests = [], errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/v0.1/**', route => {
      const pathname = new URL(route.request().url()).pathname
      requests.push(pathname)
      if (pathname.endsWith('/artifacts')) return route.fulfill({json:{artifacts:[],next_cursor:null}})
      if (pathname === '/api/v0.1/settings/company') return route.fulfill({json:{snapshot:null}})
      if (pathname === '/api/v0.1/ai-tender-radar/runs') return route.fulfill({json:{runs:[],next_offset:null}})
      if (pathname === '/api/v0.1/ai-tender-radar/cards') return route.fulfill({json:{cards:[],next_offset:null}})
      if (pathname === '/api/v0.1/ai-tender-radar/jobs/active') return route.fulfill({json:null})
      return route.fulfill({status:404,json:{error:{message:'Unexpected API request'}}})
    })
    await page.goto(base + '/#/radar?run=retired')
    await page.getByRole('heading', {name:'Все файлы',exact:true}).waitFor()
    assert.equal(await page.locator('a[href="#/radar"]').count(), 0)
    await page.getByRole('link', {name:'Радар ИИ',exact:true}).click()
    await page.getByRole('heading', {name:'Анализ по профилю предприятия',exact:true}).waitFor()
    assert.equal(await page.getByRole('link', {name:'Радар ИИ',exact:true}).getAttribute('href'), '#/ai-radar-runs')
    await page.getByRole('link', {name:'Ручной анализ',exact:true}).click()
    await page.getByRole('heading', {name:'Тендерный радар ИИ',exact:true}).waitFor()
    await page.getByRole('button', {name:'Новый анализ',exact:true}).click()
    await page.getByLabel('Складской CSV').waitFor()
    await page.getByLabel('Источник закупок').waitFor()
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    assert.equal(requests.some(path => path.startsWith('/api/v0.1/radar/')), false)
    assert.ok(requests.includes('/api/v0.1/ai-tender-radar/cards'))
    assert.deepEqual(errors, [])
    await page.reload()
    await page.getByRole('heading', {name:'Тендерный радар ИИ',exact:true}).waitFor()
  } finally {
    await browser.close()
  }
})
