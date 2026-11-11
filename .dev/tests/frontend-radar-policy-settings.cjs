const {test}=require('node:test')
const assert=require('node:assert/strict')
const {mkdirSync}=require('node:fs')
const {resolve}=require('node:path')
const {chromium}=require('playwright')
const base=process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1:5181'
const directory=process.env.BERVEL_SCREENSHOTS||resolve(__dirname,'../local/company-simulation-2026-11-05/catalog-policy-refresh')
const stamp='2026-11-05T09:00:00Z'
const original=()=>({stock_artifact_id:'stock',stock_sha256:'a'.repeat(64),products:[{id:'p1',sku:'B1',name:'Болт',unit:'шт',attributes:{ГОСТ:'7798-70'},enabled:true,manufactured_before:false,evidence:'legacy'}],experimental_products:[{id:'e1',name:'Тазик',unit:'шт',enabled:true,attributes:{material:'aluminium'}}],include_experimental:true,production:[{product_id:'p1',resource_group_id:'Линия 1',units_per_workday:'120',production_unit_cost:'3',currency:'RUB',setup_workdays:'0',material_ready_at:stamp,confirmed_at:stamp,evidence:'SIMULATION'}],resources:[{group_id:'Линия 1',calendar:[0,1,2,3,4],free_capacity_by_day:{'2026-11-09':'0'},default_daily_capacity:'0.75',confirmed_at:stamp}],scoring_policy:{version:'score-v1',target_margin:'0.30',high_score:90,max_age_by_source:{stock:null,production:'30',resources:'7',costs:'7',custom_source:'1.5'},future_policy:{enabled:false,factors:['0',1,null]}}})
const defaults={version:'score-v1',target_margin:'0.30',comfortable_utilization:'0.70',minimum_production_factor:'0.60',high_score:80,medium_score:50}
const snapshot=(profile,id='before')=>({id,created_at:stamp,profile,effective_scoring_policy:Object.fromEntries(Object.entries(defaults).map(([key,value])=>[key,profile.scoring_policy[key]??value]))})
async function browser(width,callback){
 const instance=await chromium.launch({headless:true,channel:process.env.BERVEL_BROWSER_CHANNEL||'msedge'})
 try{const page=await instance.newPage({viewport:{width,height:1000}});page.setDefaultTimeout(7000);const errors=[];page.on('pageerror',e=>errors.push(e.message));await callback(page);assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)}finally{await instance.close()}
}
async function fixtures(page,state,onSave){
 await page.route('**/api/v0.1/**',route=>{
  const req=route.request(),path=new URL(req.url()).pathname
  if(path==='/api/v0.1/settings/model')return route.fulfill({json:{provider:'mox',configured:true,api:{base_url:'https://api.example.test',model:'fixture',api_key_configured:false},mox:{base_url:'https://example.test',model:'fixture',credentials_available:true}}})
  if(path==='/api/v0.1/voice/settings')return route.fulfill({json:{configured:false,model:'fixture'}})
  if(path==='/api/v0.1/artifacts')return route.fulfill({json:{artifacts:[],next_cursor:null}})
  if(path==='/api/v0.1/settings/company')return req.method()==='GET'?route.fulfill({json:{snapshot:state.current}}):onSave(route,req.postDataJSON())
  return route.fulfill({status:404,json:{error:{message:'Unknown fixture route'}}})
 })
}
const card=page=>page.getByRole('region',{name:'Настройки тендерного радара',exact:true})
const margin=page=>card(page).getByLabel('Маржа для максимума по экономике',{exact:true})
const saved=page=>card(page).getByRole('status').filter({hasText:'Профиль сохранён.'})
for(const width of [1440,390])test('radar policy card displays effective values and autosaves only valid edits at '+width,()=>browser(width,async page=>{
 const profile=original(),state={current:snapshot(profile)},writes=[]
 state.current.effective_scoring_policy.comfortable_utilization='0.72'
 await fixtures(page,state,(route,body)=>{writes.push(body);state.current=snapshot(body.profile,'after');return route.fulfill({json:{snapshot:state.current}})})
 await page.goto(base+'/#/settings');await margin(page).waitFor()
 assert.equal(await card(page).getAttribute('class').then(c=>c.split(' ').includes('panel')),true)
 assert.equal(await margin(page).inputValue(),'0.30')
 assert.equal(await card(page).getByLabel('Загрузка без снижения балла',{exact:true}).inputValue(),'0.72')
 assert.equal(await card(page).getByLabel('Балл для высокого приоритета',{exact:true}).inputValue(),'90')
 assert.equal(await card(page).getByLabel('Вес допроизводства при полной загрузке',{exact:true}).inputValue(),'0.60')
 assert.equal(await card(page).getByText(/Допустимый возраст|Коэффициент .* при стрессе/).count(),0)
 assert.equal(await card(page).getByRole('button',{name:'Сохранить настройки радара',exact:true}).count(),0)
 await margin(page).fill('0,35')
 await card(page).getByText(/Введите маржу/).waitFor()
 assert.equal(writes.length,0,'invalid decimals never submit a command')
 const edits={'Метка набора правил':'score-v2','Маржа для максимума по экономике':'0.35','Загрузка без снижения балла':'0.7','Вес допроизводства при полной загрузке':'0.4','Балл для высокого приоритета':'85','Балл для среднего приоритета':'50'}
 for(const [label,value] of Object.entries(edits))await card(page).getByLabel(label,{exact:true}).fill(value)
 await saved(page).waitFor()
 assert.equal(writes.length,1);assert.equal(writes[0].base_snapshot_id,'before')
 const expected={...profile.scoring_policy,version:'score-v2',target_margin:'0.35',comfortable_utilization:'0.7',minimum_production_factor:'0.4',high_score:85}
 assert.deepEqual(writes[0].profile,{...profile,scoring_policy:expected})
 mkdirSync(directory,{recursive:true});await page.screenshot({path:resolve(directory,'radar-policy-'+width+'.png'),fullPage:true})
 await card(page).getByLabel('Балл для среднего приоритета',{exact:true}).fill('95')
 await card(page).getByText(/Балл для среднего приоритета должен быть/).waitFor()
 assert.equal(writes.length,1)
 await card(page).getByLabel('Балл для высокого приоритета',{exact:true}).fill('96')
 await saved(page).waitFor()
 assert.equal(writes.length,2);assert.equal(writes[1].base_snapshot_id,'after')
 assert.deepEqual(writes[1].profile.scoring_policy,{...expected,high_score:96,medium_score:95})
 await page.goto(base+'/#/company-settings');await page.getByLabel('Название продукции B1',{exact:true}).waitFor()
 assert.equal(await card(page).count(),0);assert.equal(await margin(page).count(),0)
 await page.getByRole('heading',{name:'Производственные мощности',exact:true}).waitFor()
}))
test('policy edits during an autosave use the next revision and preserve newer input',()=>browser(1440,async page=>{
 const profile=original(),state={current:snapshot(profile)},writes=[]
 let releaseFirst, acknowledgeFirst
 const firstResponse=new Promise(resolve=>{releaseFirst=resolve})
 const firstRequest=new Promise(resolve=>{acknowledgeFirst=resolve})
 await fixtures(page,state,async (route,body)=>{
  writes.push(body)
  if(writes.length===1){acknowledgeFirst();await firstResponse}
  state.current=snapshot(body.profile,'revision-'+writes.length)
  return route.fulfill({json:{snapshot:state.current}})
 })
 await page.goto(base+'/#/settings')
 await margin(page).fill('0.40')
 await firstRequest
 await margin(page).fill('0.45')
 await card(page).getByLabel('Балл для высокого приоритета',{exact:true}).fill('86')
 assert.equal(writes.length,1)
 releaseFirst()
 await saved(page).waitFor()
 assert.equal(writes.length,2)
 assert.equal(writes[1].base_snapshot_id,'revision-1')
 assert.deepEqual(writes[1].profile,{...profile,scoring_policy:{...profile.scoring_policy,target_margin:'0.45',high_score:86}})
 assert.equal(await margin(page).inputValue(),'0.45')
}))
test('internal navigation keeps an unsent policy draft and queued edits during a save',()=>browser(1440,async page=>{
 const profile=original(),state={current:snapshot(profile)},writes=[]
 let releaseFirst, acknowledgeFirst
 const firstResponse=new Promise(resolve=>{releaseFirst=resolve})
 const firstRequest=new Promise(resolve=>{acknowledgeFirst=resolve})
 await fixtures(page,state,async (route,body)=>{
  writes.push(body)
  if(writes.length===1){acknowledgeFirst();await firstResponse}
  state.current=snapshot(body.profile,'revision-'+writes.length)
  return route.fulfill({json:{snapshot:state.current}})
 })
 await page.goto(base+'/#/settings')
 await margin(page).fill('0.41')
 await page.goto(base+'/#/files')
 await card(page).waitFor({state:'detached'})
 assert.equal(writes.length,0)
 await page.goto(base+'/#/settings')
 await margin(page).waitFor()
 assert.equal(await margin(page).inputValue(),'0.41')
 await firstRequest
 await margin(page).fill('0.46')
 await page.goto(base+'/#/files')
 await card(page).waitFor({state:'detached'})
 await page.goto(base+'/#/settings')
 releaseFirst()
 await saved(page).waitFor()
 assert.equal(writes.length,2)
 assert.equal(writes[1].base_snapshot_id,'revision-1')
 assert.equal(writes[1].profile.scoring_policy.target_margin,'0.46')
}))
test('large policy save survives lost response, navigation to company, reload and exact replay',()=>browser(1440,async page=>{
 const profile=original();profile.products=Array.from({length:6001},(_,i)=>({...profile.products[0],id:'p'+i,attributes:{note:'x'.repeat(1200)}}))
 assert.ok(Buffer.byteLength(JSON.stringify(profile))>5*1024*1024)
 const state={current:snapshot(profile)},writes=[]
 await fixtures(page,state,(route,body)=>{writes.push(body);state.current={id:'after',created_at:stamp,profile:body.profile};return writes.length===1?route.abort('failed'):route.fulfill({json:{snapshot:state.current}})})
 await page.goto(base+'/#/settings');await margin(page).fill('0.45')

 await card(page).getByRole('button',{name:'Повторить сохранение',exact:true}).waitFor()
 assert.equal(await margin(page).isDisabled(),true)
 assert.equal(writes.length,1);assert.deepEqual(writes[0].profile,{...profile,scoring_policy:{...profile.scoring_policy,target_margin:'0.45'}})
 const reference=await page.evaluate(()=>sessionStorage.getItem('bervel.company-settings.pending.v1'));assert.ok(reference.length<1000)
 await page.goto(base+'/#/company-settings');await page.getByRole('button',{name:'Повторить сохранение',exact:true}).waitFor()
 assert.equal(writes.length,1)
 await page.goto(base+'/#/settings');await page.reload();await card(page).getByRole('button',{name:'Повторить сохранение',exact:true}).click()
 await card(page).getByRole('status').filter({hasText:'Профиль сохранён.'}).waitFor()
 assert.equal(writes.length,2);assert.deepEqual(writes[1],writes[0]);assert.equal(await page.evaluate(()=>sessionStorage.getItem('bervel.company-settings.pending.v1')),null)
}))
test('policy conflict cannot overwrite a newer profile; explicit reload uses its version',()=>browser(390,async page=>{
 const profile=original(),state={current:snapshot(profile)},writes=[]
 await fixtures(page,state,(route,body)=>{
  writes.push(body)
  if(writes.length===1){state.current={id:'concurrent',created_at:stamp,profile:{...profile,products:[{...profile.products[0],name:'Изменено другим редактором'}]}};return route.fulfill({status:409,json:{error:{message:'Новая версия'}}})}
  state.current={id:'after',created_at:stamp,profile:body.profile};return route.fulfill({json:{snapshot:state.current}})
 })
 await page.goto(base+'/#/settings');await margin(page).fill('0.40')

 await card(page).getByRole('button',{name:'Заменить правки актуальной версией'}).waitFor()
 assert.equal(writes.length,1);assert.equal(await margin(page).isDisabled(),true)
 assert.equal(await card(page).getByRole('button',{name:'Повторить сохранение',exact:true}).count(),0)
 await card(page).getByRole('button',{name:'Заменить правки актуальной версией'}).click()
 await card(page).getByRole('status').filter({hasText:'Актуальная версия загружена.'}).waitFor()
 const concurrent=structuredClone(state.current.profile)
 await margin(page).fill('0.50')
 await card(page).getByRole('status').filter({hasText:'Профиль сохранён.'}).waitFor()
 assert.equal(writes.length,2);assert.notEqual(writes[1].command_key,writes[0].command_key);assert.equal(writes[1].base_snapshot_id,'concurrent')
 assert.deepEqual(writes[1].profile,{...concurrent,scoring_policy:{...concurrent.scoring_policy,target_margin:'0.50'}})
}))
test('pending legacy company command is replayed from the policy card without rewriting any field',()=>browser(1440,async page=>{
 const command={command_key:'legacy-company-save',base_snapshot_id:'before',profile:original()};command.profile.products[0].enabled=false
 await page.addInitScript(value=>sessionStorage.setItem('bervel.company-settings.pending.v1',JSON.stringify(value)),command)
 const state={current:snapshot(original())},writes=[]
 await fixtures(page,state,(route,body)=>{writes.push(body);return route.fulfill({json:{snapshot:{id:'after',created_at:stamp,profile:body.profile}}})})
 await page.goto(base+'/#/settings');await card(page).getByRole('button',{name:'Повторить сохранение',exact:true}).waitFor()
 assert.equal(await margin(page).isDisabled(),true)
 await card(page).getByRole('button',{name:'Повторить сохранение',exact:true}).click();await card(page).getByRole('status').filter({hasText:'Профиль сохранён.'}).waitFor()
 assert.deepEqual(writes,[command])
}))
test('missing profile is never replaced by an invented policy-only profile',()=>browser(390,async page=>{
 let writes=0;await fixtures(page,{current:null},route=>{writes++;return route.fulfill({status:500})})
 await page.goto(base+'/#/settings');await card(page).getByRole('link',{name:'Настройки предприятия',exact:true}).waitFor()
 assert.equal(await margin(page).count(),0);assert.equal(writes,0)
}))

test('rejected policy remains editable and a storage failure never sends an unrecorded command',()=>browser(1440,async page=>{
 const state={current:snapshot(original())},writes=[]
 await fixtures(page,state,(route,body)=>{writes.push(body);return route.fulfill({status:422,json:{error:{message:'Политика отклонена'}}})})
 await page.goto(base+'/#/settings');await margin(page).fill('0.42')
 await card(page).getByRole('alert').filter({hasText:'Политика отклонена'}).waitFor()
 assert.equal(writes.length,1);assert.equal(await margin(page).isEnabled(),true)
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('bervel.company-settings.pending.v1')),null)
 await page.evaluate(()=>{IDBFactory.prototype.open=function(){throw new DOMException('Storage denied','SecurityError')}})
 await margin(page).fill('0.43')
 await card(page).getByRole('alert').filter({hasText:'Сохранение не отправлено'}).waitFor();assert.equal(writes.length,1)
 assert.equal(await margin(page).isEnabled(),true)
}))
