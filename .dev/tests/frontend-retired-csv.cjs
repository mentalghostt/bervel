const { test } = require('node:test')
const assert = require('node:assert/strict')
const { chromium } = require('playwright')

test('root and old CSV URL open original files; uploads never call educational APIs', async () => {
  const browser = await chromium.launch({headless:true})
  const page = await browser.newPage({viewport:{width:390,height:900}})
  const id='11111111-1111-4111-8111-111111111111',requests=[]
  let uploaded=false
  await page.route('**/api/v0.1/**', async route => {
    const request=route.request();requests.push(request)
    if(request.method()==='POST'){
      assert.ok(new URL(request.url()).pathname.endsWith('/artifacts'))
      uploaded=true;return route.fulfill({json:{artifact_id:id}})
    }
    return route.fulfill({json:{artifacts:uploaded?[{artifact_id:id,filename:'any-columns.csv',media_type:'text/csv',size:30,created_at:'2026-10-26T09:00:00Z'}]:[],next_cursor:null}})
  })
  try {
    const base=process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5173'
    await page.goto(base+'/#/')
    await page.getByRole('heading',{name:'Все файлы',exact:true}).waitFor()
    assert.equal(await page.getByRole('link',{name:'Загрузка CSV',exact:true}).count(),0)
    assert.equal(await page.getByText('Учебный CSV',{exact:true}).count(),0)
    await page.getByRole('button',{name:'Загрузить файл',exact:true}).click()
  await page.getByRole('region',{name:'Загрузка файлов',exact:true}).waitFor()
    await page.getByLabel('Файл',{exact:true}).setInputFiles({name:'any-columns.csv',mimeType:'text/csv',buffer:Buffer.from('product;quantity\nBOLT;17\n')})
    await page.getByRole('dialog').getByRole('button',{name:'Загрузить файл',exact:true}).click()
    await page.getByRole('button',{name:'any-columns.csv',exact:true}).waitFor()
    assert.equal(requests.filter(r=>r.method()==='POST').length,1)
    assert.ok(requests.every(r=>!/(extractions|datasets|process-runs)/.test(r.url())))
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  } finally { await browser.close() }
})
