const {test,before,after}=require('node:test')
const assert=require('node:assert/strict')
const fs=require('node:fs'),path=require('node:path')
const {chromium}=require('playwright')
const base=process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1:5180'
const evidence=process.env.BERVEL_TEST_ARTIFACT_ROOT||process.env.BERVEL_EVIDENCE_ROOT||path.resolve(path.resolve(__dirname,'../local/ui-system-2026-11-05/shared'))
const ids=['11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222']
const stamp='2026-11-05T10:00:00Z'
const files=ids.map((artifact_id,i)=>({artifact_id,filename:['Каталог крепежа.txt','Расчёт затрат.txt'][i],media_type:'text/plain',size:234,created_at:stamp,processing_state:'ready',completed_chunks:1,total_chunks:1}))
const tickets=ids.map((ticket_id,i)=>({ticket_id,title:['Проверить выгрузку отчёта','Вопрос по составу каталога'][i],problem:'Состав выгрузки требует проверки.',expected:'Все строки сохранены.',actual:'Нужна проверка.',context:'Контрольный пример.',steps:[],links:[],revision:1,status:'created',created_at:stamp,updated_at:stamp}))
const sessions=ids.map((session_id,i)=>({session_id,title:['Проверка каталога крепежа','Расчёт стоимости поставки'][i],created_at:stamp,turn_count:1,has_pending:false}))
const runs=ids.map((run_id,i)=>({run_id,settings_snapshot_id:ids[0],created_at:i?'2026-11-04T10:00:00Z':stamp,data:{state:'complete',phase:'complete',detail_attempts:0,model_attempts:0,checkpoint:{search_attempts:1,candidates:[]},counts:{}},cards:[],reports:[]}))
let browser
before(async()=>{fs.mkdirSync(evidence,{recursive:true});browser=await chromium.launch({executablePath:process.env.BERVEL_BROWSER_PATH,headless:true})})
after(async()=>{await browser?.close()})
async function open(width){
 const page=await browser.newPage({viewport:{width,height:1000}});page.setDefaultTimeout(10000)
 const errors=[];page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/v0.1/**',route=>{
  const p=new URL(route.request().url()).pathname
  if(p.endsWith('/settings/company'))return route.fulfill({json:{snapshot:null}})
  if(p.endsWith('/settings/model'))return route.fulfill({json:{provider:'mox',configured:true,api:{base_url:'https://example.invalid',model:'test',api_key_configured:false},mox:{base_url:'http://gateway.invalid',model:'test',credentials_available:true}}})
  if(p.endsWith('/voice/settings'))return route.fulfill({json:{configured:true,model:'test'}})
  if(p.endsWith('/artifacts'))return route.fulfill({json:{artifacts:files,next_cursor:null}})
  if(p.endsWith('/preview'))return route.fulfill({json:{kind:'text',text:'Сохранённое содержимое файла.',media_type:'text/plain',columns:[],rows:[],total_rows:0,next_cursor:null,notice:''}})
  if(p.endsWith('/knowledge'))return route.fulfill({json:{state:'ready',total_chunks:1,completed_chunks:1,error:null}})
  if(p.endsWith('/tickets'))return route.fulfill({json:{tickets,next_cursor:null}})
  if(p.includes('/tickets/'))return route.fulfill({json:tickets.find(t=>p.endsWith(t.ticket_id))})
  if(p.endsWith('/sessions'))return route.fulfill({json:{sessions,next_offset:null}})
  if(p.includes('/sessions/'))return route.fulfill({json:{session_id:ids.find(id=>p.endsWith(id)),created_at:stamp,turns:[]}})
  if(p.endsWith('/profiles'))return route.fulfill({json:{profiles:['test_api','kontur_api'].map(source=>({source,name:source,run_id:null,keyword_mode:'auto',keywords:[],schedule:{enabled:false,every_days:1,time:'09:00',timezone:'Europe/Moscow'},available:true,last_refresh_at:null,next_refresh_at:null}))}})
  if(/\/profiles\/(test_api|kontur_api)$/.test(p))return route.fulfill({json:{profile:{source:p.split('/').pop(),name:'Профиль',run_id:null,keyword_mode:'auto',keywords:[],schedule:{enabled:false,every_days:1,time:'09:00',timezone:'Europe/Moscow'},available:true,last_refresh_at:null,next_refresh_at:null},run:null}})
  if(p.endsWith('/runs'))return route.fulfill({json:{runs,next_offset:null}})
  if(p.includes('/runs/'))return route.fulfill({json:runs.find(r=>p.endsWith(r.run_id))})
  if(p.endsWith('/cards'))return route.fulfill({json:{cards:[],next_offset:null}})
  if(p.endsWith('/jobs/active'))return route.fulfill({json:null})
  return route.fulfill({json:{}})
 })
 return {page,errors}
}
const pages=[['files','Файлы','Список файлов'],['chat','Чаты ассистента','Чаты'],['inspection','Чаты проверки','Чаты'],['tickets','Обращения','Список обращений'],['ai-tender-radar','Радар ИИ',null],['settings','Настройки',null]]
for(const width of [1440,390])test(`all workspaces use compact headers and common surfaces at ${width}px`,async()=>{
 const {page,errors}=await open(width)
 try{
  for(const [route,label,listLabel] of pages){
   await page.goto(`${base}/#/${route}`)
   const main=page.getByRole('main',{name:label,exact:true});await main.waitFor()
   await page.evaluate(()=>document.fonts.ready)
   assert.equal(await main.locator(':scope > h1,:scope > header h1,:scope > .page-heading h1').count(),0)
   assert.equal(await page.getByText('Все обращения',{exact:true}).count(),0)
   assert.equal(await page.getByText('Все файлы',{exact:true}).count(),0)
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,route+' page overflow')
   const root=await page.evaluate(()=>{const s=getComputedStyle(document.documentElement);return ['--ui-gap-1','--ui-gap-2','--ui-gap-3','--ui-gap-4','--ui-gap-6','--ui-control-height','--ui-panel-padding','--ui-panel-radius'].map(k=>s.getPropertyValue(k).trim())})
   assert.deepEqual(root,['4px','8px','12px','16px','24px','40px','20px','24px'])
   if(listLabel){const list=main.getByLabel(listLabel,{exact:true});await list.waitFor();assert.equal(await list.evaluate(n=>getComputedStyle(n).paddingTop),'12px')}
   for(const control of await main.locator('.ui-toolbar > button,.ui-toolbar > a').all())assert.equal(Math.round((await control.boundingBox()).height),40)
   await page.screenshot({path:path.join(evidence,`${route}-${width}.png`),fullPage:true})
  }
  assert.deepEqual(errors,[])
 }finally{await page.close()}
})
for(const [route,label,selector,selected] of [['files','Файлы','.file-select','aria-pressed'],['chat','Чаты ассистента','.chat-select','aria-current'],['inspection','Чаты проверки','.chat-select','aria-current'],['tickets','Обращения','.chat-select','aria-current']])test(`${route} keeps keyboard selection and the shared selected surface`,async()=>{
 const {page,errors}=await open(1440)
 try{
  await page.goto(`${base}/#/${route}`)
  const main=page.getByRole('main',{name:label,exact:true}),choice=main.locator(selector).first();await choice.waitFor()
  await choice.focus();await page.keyboard.press('Enter');await page.waitForFunction(({selector,selected})=>document.querySelector(`main:not([hidden]) ${selector}`)?.getAttribute(selected)==='true',{selector,selected})
  const row=selector==='.run-history-item'?choice:choice.locator('..')
  assert.equal(await row.evaluate(n=>getComputedStyle(n).borderRadius),'16px')
  await page.waitForFunction(n=>getComputedStyle(n).backgroundColor==='rgb(234, 241, 227)',await row.elementHandle())
  assert.equal(await choice.evaluate(n=>getComputedStyle(n).outlineStyle),'solid')
  assert.deepEqual(errors,[])
 }finally{await page.close()}
})
test('short file and settings panels do not stretch into empty space',async()=>{
 const {page}=await open(1440)
 try{
  await page.goto(base+'/#/files');const detail=page.getByRole('region',{name:'Просмотр файла',exact:true});await detail.waitFor()
  assert.ok((await detail.boundingBox()).height<240)
  const list=page.getByRole('region',{name:'Список файлов',exact:true});assert.ok((await list.boundingBox()).height<420)
  await page.goto(base+'/#/settings');const voice=page.getByRole('region',{name:'Настройки Groq',exact:true});await voice.waitFor()
  assert.equal(await voice.evaluate(n=>getComputedStyle(n).paddingTop),'20px');assert.equal(await voice.evaluate(n=>getComputedStyle(n).borderRadius),'24px')
  assert.equal(await page.locator('.settings-columns').evaluate(n=>getComputedStyle(n).alignItems),'start')
  const inputs=await page.locator('input:not([type=radio])').all();for(const input of inputs)assert.equal(Math.round((await input.boundingBox()).height),40)
 }finally{await page.close()}
})
test('long list names share readable typography and wrap without touching actions at 390px',async()=>{
 const {page,errors}=await open(390)
 try{
  const title='Каталог высокопрочного промышленного крепежа М12–М24 для производственного цеха'
  await page.route('**/api/v0.1/artifacts?*',r=>r.fulfill({json:{artifacts:[{...files[0],filename:title+'.txt'}],next_cursor:null}}))
  await page.route('**/api/v0.1/chat/sessions?*',r=>r.fulfill({json:{sessions:[{...sessions[0],title}],next_offset:null}}))
  for(const [route,selector] of [['files','.file-name'],['chat','.chat-title']]){
   await page.goto(base+'/#/'+route)
   const name=page.locator(selector).first();await name.waitFor()
   assert.deepEqual(await name.evaluate(n=>{const s=getComputedStyle(n);return [s.fontSize,s.fontWeight,s.lineHeight]}),['13px','500','19.5px'])
   const row=name.locator('xpath=ancestor::*[contains(concat(" ",normalize-space(@class)," ")," ui-choice ")][1]'),footer=row.locator('.ui-choice-footer'),time=footer.locator('time')
   const nb=await name.boundingBox(),fb=await footer.boundingBox();assert.ok(fb.y>=nb.y+nb.height)
   assert.equal(await time.evaluate(n=>getComputedStyle(n).fontSize),'11px')
   if(route==='chat'){
    const tb=await time.boundingBox(),db=await footer.getByRole('button',{name:'Удалить',exact:true}).boundingBox()
    assert.ok(db.x>=tb.x+tb.width-1 || tb.x>=db.x+db.width-1 || db.y>=tb.y+tb.height-1 || tb.y>=db.y+db.height-1)
   }
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
   await page.screenshot({path:path.join(evidence,`${route}-long-390.png`)})
  }
  assert.deepEqual(errors,[])
 }finally{await page.close()}
})
