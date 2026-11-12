const { test, before, after } = require('node:test')
const assert = require('node:assert/strict')
const { chromium } = require('playwright')
const { mkdirSync } = require('node:fs')
const { resolve } = require('node:path')

const base = process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5173'
const date = '2026-10-27T10:00:00Z'
const id = '11111111-1111-4111-8111-111111111111'
const other = '22222222-2222-4222-8222-222222222222'
const source = { artifact_id:id, filename:'Удаляемый прайс.csv', media_type:'text/csv', size:120, created_at:date }
const kept = { ...source, artifact_id:other, filename:'Другой файл.csv' }
const evidence = { ...source, row_start:1, row_end:2, total_rows:2, excerpt:'Сохранённая цитата', truncated:false }
const shots = resolve(process.env.BERVEL_TEST_ARTIFACT_ROOT||resolve(__dirname, '../local/workspace-polish-2026-10-28/frontend/legacy-deletion'))
let browser
before(async () => { mkdirSync(shots,{recursive:true}); browser = await chromium.launch({executablePath:process.env.BERVEL_BROWSER_PATH,headless:true}) })
after(async () => { await browser?.close() })

async function open(width=1440, { failReloadOnce=false }={}) {
  const page = await browser.newPage({viewport:{width,height:1000}})
  page.setDefaultTimeout(2500)
  const requests=[], errors=[]
  page.on('request',r=>{if(r.url().includes('/api/'))requests.push(r)})
  page.on('pageerror',e=>errors.push(e.message))
  let deleted=false, fail=false, removes=0, reloadFailed=false
  await page.route('**/api/v0.1/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname
    if(request.method()==='DELETE') {
      removes++
      assert.equal(path,'/api/v0.1/artifacts/'+id)
      assert.equal(url.search,'')
      if(fail)return route.fulfill({status:503,json:{error:{code:'TRANSIENT_DEPENDENCY_ERROR',message:'Unavailable'}}})
      deleted=true
      return route.fulfill({status:204})
    }
    if(path.endsWith('/artifacts')){
      if(deleted&&failReloadOnce&&!reloadFailed){reloadFailed=true;return route.fulfill({status:503,json:{error:{code:'TRANSIENT_DEPENDENCY_ERROR'}}})}
      return route.fulfill({json:{artifacts:deleted?[kept]:[source,kept],next_cursor:null}})
    }
    if(path.endsWith('/knowledge'))return route.fulfill({json:{state:'ready',error:null,total_chunks:2,completed_chunks:2}})
    if(path.endsWith('/preview'))return route.fulfill({json:{kind:'text',media_type:'text/plain',columns:[],rows:[],total_rows:0,next_cursor:null,text:'Содержимое оригинала',notice:''}})
    if(path.endsWith('/artifacts/'+id)||path.endsWith('/content'))return route.fulfill({status:410,json:{error:{code:'ARTIFACT_DELETED',message:'Файл удалён.'}}})
    if(path==='/api/v0.1/chat/sessions'&&request.method()==='GET')return route.fulfill({json:{sessions:[],next_offset:null}})
    if(path.includes('/chat/sessions/'))return route.fulfill({json:{session_id:id,created_at:date,turns:[{turn_id:other,question:'Цена товара?',answer:'Сохранённый ответ',state:'completed',created_at:date,sources:[evidence],error:null}]}})
    throw Error('Unexpected request: '+request.method()+' '+path)
  })
  return {page,requests,errors,fail:()=>{fail=true},get removes(){return removes}}
}

test('delete requires confirmation and cancel changes nothing',async()=>{
  const state=await open(),{page}=state
  try {
    await page.goto(base+'/#/files')
    await page.getByRole('button',{name:source.filename,exact:true}).click()
    await page.locator('.file-card').filter({has:page.getByRole('button',{name:source.filename,exact:true})}).getByRole('button',{name:'Удалить навсегда',exact:true}).click()
    await page.getByRole('dialog',{name:'Удалить файл навсегда?'}).waitFor()
    await page.getByRole('button',{name:'Отмена',exact:true}).click()
    assert.equal(state.removes,0)
    assert.equal(await page.getByRole('button',{name:source.filename,exact:true}).count(),1)
    assert.deepEqual(state.errors,[])
  } finally { await page.close() }
})

for(const width of [1440,390])test(`delete removes selected row and preview, preserves other file at ${width}px`,async()=>{
  const state=await open(width),{page}=state
  try {
    await page.goto(base+'/#/files')
    await page.getByRole('button',{name:source.filename,exact:true}).click()
    await page.locator('.file-card').filter({has:page.getByRole('button',{name:source.filename,exact:true})}).getByRole('button',{name:'Удалить навсегда',exact:true}).click()
    const dialog=page.getByRole('dialog')
    await dialog.waitFor()
    assert.doesNotMatch(await dialog.innerText(),/редакци/)
    assert.match(await dialog.innerText(),/История чатов и отчёты сохранятся/)
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
    await page.screenshot({path:resolve(shots,`confirm-${width}.png`),fullPage:true})
    await dialog.getByRole('button',{name:'Удалить навсегда',exact:true}).click()
    await page.getByRole('status').filter({hasText:'Файл удалён'}).waitFor()
    assert.equal(await page.getByRole('button',{name:source.filename,exact:true}).count(),0)
    assert.equal(await page.getByRole('button',{name:kept.filename,exact:true}).count(),1)
    assert.equal(await page.getByText('Содержимое оригинала',{exact:true}).count(),0)
    assert.equal(state.removes,1)
    assert.equal(state.requests.filter(r=>r.method()!=='GET').length,1)
    assert.deepEqual(state.errors,[])
    await page.reload()
    await page.getByRole('button',{name:kept.filename,exact:true}).waitFor()
    assert.equal(await page.getByRole('button',{name:source.filename,exact:true}).count(),0)
  } finally { await page.close() }
})

test('failed deletion keeps the selected file and shows an error',async()=>{
  const state=await open(),{page}=state
  try {
    state.fail()
    await page.goto(base+'/#/files')
    await page.getByRole('button',{name:source.filename,exact:true}).click()
    await page.locator('.file-card').filter({has:page.getByRole('button',{name:source.filename,exact:true})}).getByRole('button',{name:'Удалить навсегда',exact:true}).click()
    await page.getByRole('dialog').getByRole('button',{name:'Удалить навсегда',exact:true}).click()
    await page.getByRole('dialog').getByRole('alert').waitFor()
    assert.equal(await page.getByRole('button',{name:source.filename,exact:true}).count(),1)
    assert.equal(await page.getByRole('dialog').getByRole('button',{name:'Отмена',exact:true}).isEnabled(),true)
  } finally { await page.close() }
})

test('list retry works if refreshing remaining files fails after deletion',async()=>{
  const state=await open(1440,{failReloadOnce:true}),{page}=state
  try{
    await page.goto(base+'/#/files')
    await page.getByRole('button',{name:source.filename,exact:true}).click()
    await page.locator('.file-card').filter({has:page.getByRole('button',{name:source.filename,exact:true})}).getByRole('button',{name:'Удалить навсегда',exact:true}).click()
    await page.getByRole('dialog').getByRole('button',{name:'Удалить навсегда',exact:true}).click()
    await page.getByRole('alert').waitFor()
    const reloaded=page.waitForRequest(r=>new URL(r.url()).pathname.endsWith('/artifacts')&&r.method()==='GET')
    await page.getByRole('button',{name:'Повторить',exact:true}).click()
    await reloaded
    await page.getByRole('button',{name:kept.filename,exact:true}).waitFor()
    assert.equal(await page.getByRole('alert').count(),0)
  }finally{await page.close()}
})

test('historical chat answer survives and its exact source link reports deletion',async()=>{
  const {page,errors}=await open()
  try {
    await page.addInitScript(id=>localStorage.setItem('bervel.chat.session_id',id),id)
    await page.goto(base+'/#/chat')
    await page.getByText('Сохранённый ответ',{exact:true}).waitFor()
    await page.getByRole('link',{name:source.filename,exact:true}).click()
    await page.getByRole('alert').filter({hasText:'Файл удалён.'}).waitFor()
    await page.getByRole('link',{name:'Ассистент',exact:true}).click()
    await page.getByText('Сохранённый ответ',{exact:true}).waitFor()
    assert.equal(await page.getByText('Сохранённый ответ',{exact:true}).isVisible(),true)
    assert.equal(await page.getByText('Сохранённая цитата',{exact:true}).count(),0)
    assert.deepEqual(errors,[])
  } finally { await page.close() }
})
