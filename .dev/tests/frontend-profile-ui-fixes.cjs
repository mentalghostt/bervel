const {test}=require('node:test')
const assert=require('node:assert/strict')
const http=require('node:http')
const fs=require('node:fs')
const {chromium}=require('playwright')
const base=process.env.BERVEL_FRONTEND_URL||'http://bervel-ui-fixes-qa:5173'
const directory=process.env.BERVEL_SCREENSHOTS||'/workspace/.dev/local/profile-ui-fixes-2026-11-05'
const id='11111111-1111-4111-8111-111111111111'
const stamp='2026-11-05T10:00:00Z'
const file={artifact_id:id,filename:'Склад без назначения.csv',media_type:'text/csv',size:6400000,created_at:stamp,usage_role:'general',processing_state:'ready',completed_chunks:1,total_chunks:1}
const title='Добавить форматированные ответы помощника, встроенные изображения, таблицы и создание документов'
const tickets=[title,'Убрать бюджет выполнения задач помощником'].map((title,i)=>({ticket_id:'t'+i,title,status:'created',revision:1,created_at:stamp,updated_at:stamp,problem:'Описание проблемы',expected:'',actual:'',steps:[],context:'',links:[]}))
async function browser(width,callback){
 const proxy=http.createServer((req,res)=>{const next=http.request(new URL(req.url,base),{method:req.method,headers:req.headers},r=>{res.writeHead(r.statusCode,r.headers);r.pipe(res)});next.on('error',()=>{res.writeHead(502);res.end()});req.pipe(next)})
 await new Promise(resolve=>proxy.listen(0,'127.0.0.1',resolve))
 const instance=await chromium.launch({headless:true,channel:process.env.BERVEL_BROWSER_CHANNEL})
 try{const page=await instance.newPage({viewport:{width,height:1000}});page.setDefaultTimeout(8000);const errors=[];page.on('pageerror',e=>errors.push(e.message));await callback(page,`http://127.0.0.1:${proxy.address().port}`);assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)}
 finally{await instance.close();await new Promise(resolve=>proxy.close(resolve))}
}
for(const width of [1440,390])test(`ticket cards are separate selectable cards with a date at ${width}`,()=>browser(width,async(page,origin)=>{
 await page.route('**/api/v0.1/**',route=>{const p=new URL(route.request().url()).pathname;if(p==='/api/v0.1/tickets')return route.fulfill({json:{tickets,next_cursor:null}});if(p.startsWith('/api/v0.1/tickets/'))return route.fulfill({json:tickets.find(t=>p.endsWith(t.ticket_id))});return route.fulfill({status:404,json:{error:{message:'Unknown fixture'}}})})
 await page.goto(origin+'/#/tickets');await page.getByRole('main',{name:'Обращения',exact:true}).waitFor()
 const list=page.getByRole('region',{name:'Список обращений'})
 const button=list.getByRole('button').first();await button.waitFor()
 const style=await button.evaluate(node=>({align:getComputedStyle(node).textAlign,background:getComputedStyle(node.parentElement).backgroundColor,width:node.getBoundingClientRect().width,parent:node.parentElement.getBoundingClientRect().width}))
 assert.equal(style.align,'left');assert.notEqual(style.background,'rgba(0, 0, 0, 0)');assert.ok(style.width>=style.parent-3)
 const time=list.locator('time').first();await time.waitFor();assert.equal(await time.getAttribute('datetime'),stamp)
 assert.equal(await button.locator('.chat-title').evaluate(node=>getComputedStyle(node).webkitLineClamp),'2')
 await button.click();await page.getByRole('region',{name:'Карточка обращения'}).getByRole('heading',{name:title,exact:true}).waitFor()
 assert.equal(await button.getAttribute('aria-current'),'true')
 fs.mkdirSync(directory,{recursive:true});await page.screenshot({path:`${directory}/tickets-${width}.png`,fullPage:true})
}))
test('file upload and details have no assignment; any CSV can be picked as warehouse',()=>browser(1440,async(page,origin)=>{
 const writes=[]
 await page.route('**/api/v0.1/**',route=>{
  const req=route.request(),p=new URL(req.url()).pathname
  if(req.method()!=='GET')writes.push({path:p,method:req.method()})
  if(p==='/api/v0.1/artifacts'&&req.method()==='POST')return route.fulfill({json:{artifact_id:id}})
  if(p==='/api/v0.1/artifacts')return route.fulfill({json:{artifacts:[file],next_cursor:null}})
  if(p.endsWith('/preview'))return route.fulfill({json:{kind:'table',media_type:'text/csv',columns:['sku','product','free_quantity','unit'],rows:[['S1','Bolt','1','pc']],total_rows:1,next_cursor:null,text:'',notice:''}})
  if(p.endsWith('/knowledge'))return route.fulfill({json:{state:'ready',completed_chunks:1,total_chunks:1,error:null}})
  if(p==='/api/v0.1/settings/company')return route.fulfill({json:{snapshot:null}})
  if(p==='/api/v0.1/artifacts/'+id)return route.fulfill({json:file})
  return route.fulfill({status:404,json:{error:{message:'Unknown fixture'}}})
 })
 await page.goto(origin+'/#/files');await page.getByRole('button',{name:file.filename,exact:true}).click()
 assert.equal(await page.getByLabel('Назначение выбранного файла').count(),0)
 assert.equal(await page.getByText('Общий файл',{exact:true}).count(),0)
 await page.getByRole('button',{name:'Загрузить файл',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'Загрузить файл'})
 assert.equal(await dialog.getByLabel('Назначение файла',{exact:true}).count(),0)
 await dialog.getByLabel('Файл',{exact:true}).setInputFiles({name:'new.csv',mimeType:'text/csv',buffer:Buffer.from('sku,product,free_quantity,unit\nS1,Bolt,1,pc\n')})
 await dialog.getByRole('button',{name:'Загрузить файл',exact:true}).click()
 await dialog.getByText(/Файл new.csv сохранён/).waitFor()
 assert.deepEqual(writes,[{path:'/api/v0.1/artifacts',method:'POST'}])
 await dialog.getByRole('button',{name:'Закрыть',exact:true}).click()
 await page.goto(origin+'/#/company-settings');await page.getByRole('main',{name:'Настройки предприятия',exact:true}).waitFor()
 const select=page.getByLabel('Складской CSV',{exact:true});await select.selectOption(id)
 assert.equal(await select.inputValue(),id)
 assert.equal(await page.getByText(/назначьте ему роль|с назначением «Склад»/).count(),0)
 await page.screenshot({path:`${directory}/unlabelled-warehouse.png`,fullPage:true})
}))

test('large profile save survives a lost response and reload beyond Web Storage quota',()=>browser(1440,async(page,origin)=>{
 const scoring_policy={version:'score-v1',target_margin:'0.30',high_score:90,max_age_by_source:{stock:null,production:'14'},future_policy:{enabled:false,factors:['0',1,null]}}
 const data={stock_artifact_id:id,stock_sha256:'a'.repeat(64),products:Array.from({length:6001},(_,i)=>({id:'p'+i,sku:'S'+i,name:'Bolt '+i,unit:'pc',enabled:true,manufactured_before:null,attributes:{note:'x'.repeat(1200)}})),experimental_products:[],include_experimental:false,production:[],resources:[],scoring_policy}
 assert.ok(Buffer.byteLength(JSON.stringify(data))>5*1024*1024)
 let saved={id:'22222222-2222-4222-8222-222222222222',created_at:stamp,profile:data}
 const writes=[]
 await page.route('**/api/v0.1/**',route=>{
  const req=route.request(),p=new URL(req.url()).pathname
  if(p==='/api/v0.1/artifacts')return route.fulfill({json:{artifacts:[file],next_cursor:null}})
  if(req.method()==='GET')return route.fulfill({json:{snapshot:saved}})
  const body=req.postDataJSON();writes.push(body)
  saved={id:'33333333-3333-4333-8333-333333333333',created_at:stamp,profile:body.profile}
  if(writes.length===1)return route.abort('failed')
  return route.fulfill({json:{snapshot:saved}})
 })
 await page.goto(origin+'/#/company-settings')
 await page.getByLabel('Включить Bolt 0 в поиск',{exact:true}).uncheck()
 assert.equal(await page.getByRole('button',{name:'Добавить продукт',exact:true}).isEnabled(),true)
 await page.getByRole('button',{name:'Сохранить профиль',exact:true}).click()
 await page.getByRole('button',{name:'Повторить сохранение',exact:true}).waitFor()
 assert.equal(writes.length,1)
 assert.equal(writes[0].profile.products.length,6001)
 assert.deepEqual(writes[0].profile.scoring_policy,scoring_policy)
 const reference=await page.evaluate(()=>sessionStorage.getItem('bervel.company-settings.pending.v1'))
 assert.ok(reference.length<1000,'Web Storage holds a reference, never the large profile')
 await page.reload()
 await page.getByRole('button',{name:'Повторить сохранение',exact:true}).click()
 await page.getByRole('status').filter({hasText:'Профиль сохранён.'}).waitFor()
 assert.equal(writes.length,2)
 assert.deepEqual(writes[1],writes[0])
 assert.deepEqual(writes[1].profile.scoring_policy,scoring_policy)
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('bervel.company-settings.pending.v1')),null)
}))

test('unavailable IndexedDB prevents sending an unrecorded save',()=>browser(1440,async(page,origin)=>{
 await page.addInitScript(()=>{IDBFactory.prototype.open=function(){throw new DOMException('Storage denied','SecurityError')}})
 const profile={stock_artifact_id:id,stock_sha256:'a'.repeat(64),products:[{id:'p1',sku:'S1',name:'Bolt',unit:'pc',attributes:{},enabled:true,manufactured_before:null}],experimental_products:[],include_experimental:false,production:[],resources:[],scoring_policy:{}}
 let writes=0
 await page.route('**/api/v0.1/**',route=>{
  const req=route.request(),p=new URL(req.url()).pathname
  if(p==='/api/v0.1/artifacts')return route.fulfill({json:{artifacts:[file],next_cursor:null}})
  if(req.method()==='GET')return route.fulfill({json:{snapshot:{id:'old',created_at:stamp,profile}}})
  writes++;return route.fulfill({status:500,json:{error:{message:'Save should not be sent'}}})
 })
 await page.goto(origin+'/#/company-settings')
 await page.getByLabel('Включить Bolt в поиск',{exact:true}).uncheck()
 await page.getByRole('button',{name:'Сохранить профиль',exact:true}).click()
 await page.getByText(/Хранилище браузера недоступно. Сохранение не отправлено/).waitFor()
 assert.equal(writes,0)
 assert.equal(await page.getByLabel('Включить Bolt в поиск',{exact:true}).isEnabled(),true)
}))

test('a pending command from the previous storage format is replayed unchanged',()=>browser(1440,async(page,origin)=>{
 const profile={stock_artifact_id:id,stock_sha256:'a'.repeat(64),products:[{id:'p1',sku:'S1',name:'Bolt',unit:'pc',attributes:{},enabled:true,manufactured_before:null}],experimental_products:[],include_experimental:false,production:[],resources:[],scoring_policy:{}}
 const command={command_key:'prior-release-command',base_snapshot_id:null,profile}
 await page.addInitScript(value=>sessionStorage.setItem('bervel.company-settings.pending.v1',JSON.stringify(value)),command)
 const writes=[]
 await page.route('**/api/v0.1/**',route=>{
  const req=route.request(),p=new URL(req.url()).pathname
  if(p==='/api/v0.1/artifacts')return route.fulfill({json:{artifacts:[file],next_cursor:null}})
  if(req.method()==='GET')return route.fulfill({json:{snapshot:null}})
  writes.push(req.postDataJSON());return route.fulfill({json:{snapshot:{id:'new',created_at:stamp,profile}}})
 })
 await page.goto(origin+'/#/company-settings')
 await page.getByRole('button',{name:'Повторить сохранение',exact:true}).click()
 await page.getByRole('status').filter({hasText:'Профиль сохранён.'}).waitFor()
 assert.deepEqual(writes,[command])
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('bervel.company-settings.pending.v1')),null)
}))

test('a duplicated pending tab remains usable after the original tab confirms the save',()=>browser(1440,async(page,origin)=>{
 const profile={stock_artifact_id:id,stock_sha256:'a'.repeat(64),products:[{id:'p1',sku:'S1',name:'Bolt',unit:'pc',attributes:{},enabled:true,manufactured_before:null}],experimental_products:[],include_experimental:false,production:[],resources:[],scoring_policy:{}}
 let snapshot={id:'old',created_at:stamp,profile},writes=0
 await page.context().route('**/api/v0.1/**',route=>{
  const req=route.request(),p=new URL(req.url()).pathname
  if(p==='/api/v0.1/artifacts')return route.fulfill({json:{artifacts:[file],next_cursor:null}})
  if(req.method()==='GET')return route.fulfill({json:{snapshot}})
  writes++;snapshot={id:'new',created_at:stamp,profile:req.postDataJSON().profile}
  return writes===1?route.abort('failed'):route.fulfill({json:{snapshot}})
 })
 await page.goto(origin+'/#/company-settings')
 await page.getByLabel('Включить Bolt в поиск',{exact:true}).uncheck()
 await page.getByRole('button',{name:'Сохранить профиль',exact:true}).click()
 await page.getByRole('button',{name:'Повторить сохранение',exact:true}).waitFor()
 const opening=page.waitForEvent('popup')
 await page.evaluate(()=>window.open(location.href,'_blank'))
 const duplicate=await opening
 try{
  await duplicate.getByRole('button',{name:'Повторить сохранение',exact:true}).waitFor()
  await page.getByRole('button',{name:'Повторить сохранение',exact:true}).click()
  await page.getByRole('status').filter({hasText:'Профиль сохранён.'}).waitFor()
  await duplicate.reload()
  const toggle=duplicate.getByLabel('Включить Bolt в поиск',{exact:true});await toggle.waitFor()
  assert.equal(await toggle.isEnabled(),true)
  assert.equal(await toggle.isChecked(),false)
  assert.equal(await duplicate.getByRole('button',{name:'Повторить сохранение',exact:true}).count(),0)
  assert.equal(writes,2)
 }finally{await duplicate.close()}
}))
