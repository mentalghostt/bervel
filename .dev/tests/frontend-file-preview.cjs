const {test}=require('node:test')
const assert=require('node:assert/strict')
const {chromium}=require('playwright')
const {readFileSync}=require('node:fs')
const {resolve}=require('node:path')
const pdfFixture=readFileSync(resolve(__dirname,'../fixtures/file-preview/two-pages.pdf'))
const files=['Каталог.csv','Схема.png','Документ.pdf','Неизвестный.bin'].map((filename,i)=>({artifact_id:`11111111-1111-4111-8111-11111111111${i}`,filename,media_type:'application/octet-stream',size:100,created_at:'2026-10-22T12:00:00Z',dataset:null}))
const base={kind:'unsupported',media_type:'application/octet-stream',columns:[],rows:[],text:'',notice:'Нет встроенного просмотра',total_rows:0,next_cursor:null}

test('Files displays arbitrary original CSV, image and PDF beside a narrow fixed-width list without an extraction',async()=>{
 const browser=await chromium.launch({executablePath:process.env.BERVEL_BROWSER_PATH,headless:true}),page=await browser.newPage({viewport:{width:1600,height:1000}})
 const errors=[];page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/v0.1/**',route=>{
  const path=new URL(route.request().url()).pathname
  if(path.endsWith('/artifacts'))return route.fulfill({json:{artifacts:files,next_cursor:null}})
  if(path.endsWith('/preview')){
   const f=files.find(f=>path.includes(f.artifact_id)),i=files.indexOf(f)
   return route.fulfill({json:i===0?{...base,kind:'table',columns:['SKU','Покрытие','Остаток'],rows:[['0016','цинк','9007199254740993.1234']],total_rows:1,notice:''}:i===1?{...base,kind:'image',media_type:'image/png',notice:''}:i===2?{...base,kind:'pdf',media_type:'application/pdf',notice:''}:base})
  }
  if(path.endsWith('/content'))return route.fulfill({body:path.includes(files[1].artifact_id)?Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jw1cAAAAASUVORK5CYII=','base64'):pdfFixture})
  return route.fulfill({status:500,json:{}})
 })
 try{
  await page.goto((process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1') + '/#/files')
  await page.getByRole('main',{name:'Файлы',exact:true}).waitFor()
  await page.getByRole('button',{name:'Каталог.csv',exact:true}).click()
  await page.getByRole('cell',{name:'9007199254740993.1234',exact:true}).waitFor()
  assert.equal(await page.getByRole('columnheader',{name:'SKU',exact:true}).isVisible(),true)
  const list=await page.getByRole('region',{name:'Список файлов',exact:true}).boundingBox()
  const preview=await page.getByRole('region',{name:'Просмотр файла',exact:true}).boundingBox()
  assert.ok(list.width<=320&&preview.width>list.width*2)
  assert.ok(list.x<preview.x&&preview.x+preview.width>1500)
  await page.getByRole('button',{name:'Схема.png',exact:true}).click()
  const image=page.getByRole('img',{name:'Схема.png',exact:true});await image.waitFor()
  await page.waitForFunction(()=>document.querySelector('img[alt="Схема.png"]')?.naturalWidth>0)
  await page.getByRole('button',{name:'Документ.pdf',exact:true}).click()
  await page.getByRole('img',{name:'Страница 1 PDF: Документ.pdf',exact:true}).waitFor()
  await page.locator('canvas[data-rendered="true"]').waitFor()
  await page.getByRole('button',{name:'Следующая страница',exact:true}).click()
  await page.getByRole('img',{name:'Страница 2 PDF: Документ.pdf',exact:true}).waitFor()
  await page.locator('canvas[data-rendered="true"]').waitFor()
  await page.getByRole('button',{name:'Предыдущая страница',exact:true}).click()
  await page.getByRole('button',{name:'Следующая страница',exact:true}).click()
  await page.getByRole('img',{name:'Страница 2 PDF: Документ.pdf',exact:true}).waitFor()
  await page.locator('canvas[data-rendered="true"]').waitFor()
  await page.getByRole('button',{name:'Неизвестный.bin',exact:true}).click()
  await page.getByText('Нет встроенного просмотра',{exact:true}).waitFor()
  assert.equal(await page.getByRole('button',{name:'Скачать оригинал',exact:true}).isVisible(),true)
  await page.setViewportSize({width:390,height:844})
  await page.getByRole('button',{name:'Каталог.csv',exact:true}).click()
  await page.getByRole('cell',{name:'0016',exact:true}).waitFor()
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  assert.deepEqual(errors,[])
 }finally{await browser.close()}
})

test('a slow original cannot replace the newly selected file and preview errors can be retried',async()=>{
 const browser=await chromium.launch({executablePath:process.env.BERVEL_BROWSER_PATH,headless:true}),page=await browser.newPage()
 let release;const gate=new Promise(r=>{release=r});let allowPreview=false
 await page.route('**/api/v0.1/**',async route=>{
  const path=new URL(route.request().url()).pathname
  if(path.endsWith('/artifacts'))return route.fulfill({json:{artifacts:files,next_cursor:null}})
  if(path.includes(files[0].artifact_id)){await gate;return route.fulfill({json:{...base,kind:'text',text:'LATE_MUST_NOT_APPEAR'}}).catch(()=>{})}
  if(path.endsWith('/preview'))return route.fulfill(!allowPreview?{status:503,json:{}}:{json:{...base,kind:'text',text:'CURRENT_SELECTED_TEXT',notice:''}})
  return route.fulfill({status:500,json:{}})
 })
 try{
  await page.goto((process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1') + '/#/files')
  await page.getByRole('button',{name:'Каталог.csv',exact:true}).click()
  await page.getByRole('button',{name:'Неизвестный.bin',exact:true}).click()
  await page.getByRole('button',{name:'Повторить просмотр',exact:true}).waitFor()
  allowPreview=true
  await page.getByRole('button',{name:'Повторить просмотр',exact:true}).click()
  await page.getByText('CURRENT_SELECTED_TEXT',{exact:true}).waitFor()
  release();await page.waitForTimeout(100)
  assert.equal(await page.getByText('LATE_MUST_NOT_APPEAR',{exact:true}).count(),0)
 }finally{release();await browser.close()}
})

test('a failed PDF viewer chunk preserves the file list and original download',async()=>{
 const browser=await chromium.launch({executablePath:process.env.BERVEL_BROWSER_PATH,headless:true}),page=await browser.newPage()
 await page.route(/\/(?:assets\/PdfPreview-[^/]+\.js|src\/PdfPreview\.tsx)(?:\?.*)?$/,route=>route.abort())
 await page.route('**/api/v0.1/**',route=>{
  const path=new URL(route.request().url()).pathname
  if(path.endsWith('/artifacts'))return route.fulfill({json:{artifacts:files,next_cursor:null}})
  if(path.endsWith('/preview'))return route.fulfill({json:{...base,kind:'pdf',media_type:'application/pdf',notice:''}})
  return route.fulfill({body:pdfFixture})
 })
 try{
  await page.goto((process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1') + '/#/files')
  await page.getByRole('button',{name:'Документ.pdf',exact:true}).click()
  await page.getByRole('button',{name:'Обновить страницу',exact:true}).waitFor()
  assert.equal(await page.getByRole('region',{name:'Список файлов',exact:true}).isVisible(),true)
  assert.equal(await page.getByRole('button',{name:'Скачать оригинал',exact:true}).isVisible(),true)
 }finally{await browser.close()}
})
