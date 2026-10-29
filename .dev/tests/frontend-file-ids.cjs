const {test,before,after}=require('node:test')
const assert=require('node:assert/strict')
const {chromium}=require('playwright')
const base=process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1:5173'
const ids=['11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222']
const stamp='2026-10-28T09:00:00Z'
const files=ids.map(artifact_id=>({artifact_id,filename:'Одинаковое имя.txt',media_type:'text/plain',size:10,created_at:stamp,processing_state:'ready',completed_chunks:1,total_chunks:1}))
let browser
before(async()=>{browser=await chromium.launch({headless:true})})
after(async()=>{await browser?.close()})
async function open(){
 const page=await browser.newPage({viewport:{width:1440,height:900}});page.setDefaultTimeout(2500)
 const requests=[];page.on('request',r=>{if(r.url().includes('/api/'))requests.push(r)})
 await page.route('**/api/v0.1/**',route=>{
  const p=new URL(route.request().url()).pathname
  if(p.endsWith('/artifacts'))return route.fulfill({json:{artifacts:files,next_cursor:null}})
  if(p.endsWith('/knowledge'))return route.fulfill({json:{state:'ready',error:null,total_chunks:1,completed_chunks:1}})
  if(p.endsWith('/preview'))return route.fulfill({json:{kind:'text',media_type:'text/plain',text:'Точный файл '+p.split('/').at(-2),columns:[],rows:[],total_rows:0,next_cursor:null,notice:''}})
  if(p.endsWith('/content'))return route.fulfill({contentType:'text/plain',body:'original'})
  if(ids.some(id=>p.endsWith('/artifacts/'+id)))return route.fulfill(route.request().method()==='DELETE'?{status:204}:{json:files.find(f=>p.endsWith(f.artifact_id))})
  return route.fulfill({json:{}})
 });return {page,requests}
}
test('file metadata, preview, readiness, download and deletion address exact ID only',async()=>{const {page,requests}=await open();try{
 await page.goto(base+'/#/files?artifact_id='+ids[1]);await page.getByText('Точный файл '+ids[1],{exact:true}).waitFor()
 await page.getByRole('region',{name:'Подготовка файла для ИИ',exact:true}).getByText('Готов к ИИ анализу',{exact:true}).waitFor()
 const download=page.waitForEvent('download');await page.getByRole('button',{name:'Скачать оригинал',exact:true}).click();await download
 await page.locator('.file-card').nth(0).getByRole('button',{name:'Удалить навсегда',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Удалить навсегда',exact:true}).click();await page.getByRole('status').filter({hasText:'Файл удалён'}).waitFor()
 assert.equal(await page.getByText('Точный файл '+ids[1],{exact:true}).count(),1)
 assert.ok(requests.some(r=>new URL(r.url()).pathname.endsWith('/artifacts/'+ids[1])))
 assert.ok(requests.some(r=>r.method()==='DELETE'&&new URL(r.url()).pathname.endsWith('/artifacts/'+ids[0])))
 assert.equal(requests.some(r=>/revisions|expected_revision/.test(r.url())),false)
 assert.doesNotMatch(await page.locator('body').innerText(),/редакци[яи]/i)
}finally{await page.close()}})
test('obsolete revision bookmark refuses lookup rather than opening different content',async()=>{const {page,requests}=await open();try{
 await page.goto(base+'/#/files?artifact_id='+ids[0]+'&revision=2');await page.getByRole('alert').filter({hasText:'Ссылка устарела'}).waitFor()
 assert.equal(requests.some(r=>/\/artifacts\/[^/?]+(?:\/|$)/.test(new URL(r.url()).pathname)),false)
}finally{await page.close()}})
test('same-name independent uploads get separate IDs while an uncertain retry keeps its command',async()=>{const {page}=await open();const bodies=[];let fail=true;try{
 await page.route('**/api/v0.1/artifacts',route=>{if(route.request().method()!=='POST')return route.fallback();bodies.push(route.request().postData());if(fail){fail=false;return route.abort('failed')}return route.fulfill({json:{artifact_id:ids[bodies.length===2?0:1]}})})
 await page.goto(base+'/#/files');const dialog=page.getByRole('dialog')
 async function choose(){await page.getByRole('button',{name:'Загрузить файл',exact:true}).click();await dialog.getByLabel('Файл',{exact:true}).setInputFiles({name:'same.txt',mimeType:'text/plain',buffer:Buffer.from('same')});await dialog.getByRole('button',{name:'Загрузить файл',exact:true}).click()}
 await choose();await dialog.getByRole('alert').waitFor();await dialog.getByRole('button',{name:'Повторить загрузку',exact:true}).click();await dialog.getByRole('status').waitFor();await dialog.getByRole('button',{name:'Закрыть',exact:true}).click();await choose();await dialog.getByRole('status').waitFor()
 const keys=bodies.map(b=>b.match(/name="command_key"\r\n\r\n([^\r]+)/)[1]);assert.equal(keys[0],keys[1]);assert.notEqual(keys[1],keys[2]);assert.equal(bodies.length,3)
 assert.equal(bodies.some(b=>/name="(?:artifact_id|revision|expected_revision)"/.test(b)),false)
}finally{await page.close()}})
const savedRadarAttempt={command_key:'33333333-3333-4333-8333-333333333333',procurement_source:{artifact_id:ids[0]},stock_source:{artifact_id:ids[1]},economics_source:{artifact_id:ids[0]},filters:{min_price:null,max_price:null,min_days_remaining:0,focus:'Прежняя неизвестная команда',max_shortage_percent:'0',min_margin_percent:'15'}}
test('obsolete saved radar attempt retains its command and blocks POST, including after reload',async()=>{
 for(const width of [1440,390]){const {page,requests}=await open();try{
  const attempt={...savedRadarAttempt,...Object.fromEntries(['procurement_source','stock_source','economics_source'].map(key=>[key,{...savedRadarAttempt[key],revision:1}]))},stored=JSON.stringify(attempt)
  await page.setViewportSize({width,height:900});await page.addInitScript(stored=>localStorage.setItem('bervel.radar.analyse_attempt',stored),stored)
  await page.route('**/api/v0.1/artifacts?*',route=>route.fulfill({json:{artifacts:['procurement','stock','economics'].map((usage_role,i)=>({...files[i%2],artifact_id:i===2?savedRadarAttempt.command_key:ids[i],usage_role})),next_cursor:null}}))
  await page.route('**/api/v0.1/radar/runs',route=>route.fulfill({json:[]}))
  await page.route('**/api/v0.1/radar/analyse',route=>route.fulfill({status:422,json:{error:{message:'Removed file fields are forbidden'}}}))
  await page.goto(base+'/#/radar')
  for(let pass=0;pass<2;pass++){
   const notice=page.getByRole('alert').filter({hasText:'Сохранённый запуск устарел'});await notice.waitFor();assert.match(await notice.innerText(),new RegExp(attempt.command_key));assert.match(await notice.innerText(),/неизвестен/)
   assert.equal(await page.getByRole('button',{name:'Подобрать тендеры',exact:false}).isDisabled(),true)
   await page.locator('form').evaluate(form=>form.requestSubmit());await page.getByRole('button',{name:'Обновить источники',exact:true}).click()
   assert.equal(await page.evaluate(()=>localStorage.getItem('bervel.radar.analyse_attempt')),stored)
   assert.equal(requests.some(r=>r.method()==='POST'),false);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
   if(pass===0)await page.reload()
  }
  const fs=require('node:fs'),path=require('node:path'),folder=process.env.BERVEL_EVIDENCE_ROOT||path.resolve(__dirname,'../local/frontend');fs.mkdirSync(folder,{recursive:true});await page.screenshot({path:path.join(folder,'obsolete-radar-attempt-'+width+'.png')})
 }finally{await page.close()}}
})
test('valid ID-only saved radar attempt retries its exact command after uncertainty and reload',async()=>{const {page,requests}=await open();try{
 const stored=JSON.stringify(savedRadarAttempt),posts=[]
 await page.addInitScript(stored=>localStorage.setItem('bervel.radar.analyse_attempt',stored),stored)
 await page.route('**/api/v0.1/radar/runs',route=>route.fulfill({json:[]}))
 const run={run_id:ids[0],batch_id:ids[1],state:'queued',created_at:stamp,focus:'',sources_snapshot:[],report:null,error:null,model:null}
 await page.route('**/api/v0.1/radar/runs/'+ids[0],route=>route.fulfill({json:run}))
 await page.route('**/api/v0.1/radar/analyse',route=>{posts.push(route.request().postDataJSON());return posts.length===1?route.abort('failed'):route.fulfill({json:run})})
 await page.goto(base+'/#/radar');assert.equal(requests.some(r=>r.method()==='POST'),false)
 await page.getByRole('button',{name:'Повторить запуск',exact:false}).click();await page.getByRole('alert').waitFor();assert.equal(await page.evaluate(()=>localStorage.getItem('bervel.radar.analyse_attempt')),stored)
 await page.reload();await page.getByRole('button',{name:'Повторить запуск',exact:false}).click();await page.getByRole('status').filter({hasText:'Запуск в очереди'}).waitFor()
 await page.waitForFunction(()=>localStorage.getItem('bervel.radar.analyse_attempt')===null)
 assert.deepEqual(posts,[savedRadarAttempt,savedRadarAttempt]);assert.equal(await page.evaluate(()=>localStorage.getItem('bervel.radar.analyse_attempt')),null)
 assert.equal(posts.some(body=>JSON.stringify(body).includes('revision')),false)
}finally{await page.close()}})
