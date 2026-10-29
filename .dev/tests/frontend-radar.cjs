const { test }=require('node:test')
const assert=require('node:assert/strict')
const { chromium }=require('playwright')
test('radar pins three ready role refs, restores legacy report and fits mobile', async()=>{
 const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:390,height:900}})
 const id='11111111-1111-4111-8111-111111111111',second='22222222-2222-4222-8222-222222222222',stock='33333333-3333-4333-8333-333333333333',economics='44444444-4444-4444-8444-444444444444',date='2026-10-22T12:00:00Z'
 let sent
 const run={run_id:id,batch_id:id,created_at:date,state:'completed',focus:'',sources_snapshot:[],model:'test',error:null,
  report:{as_of:date,counts:{total:1,model_analysed:1,take_in_work:1,needs_review:0,excluded:0,not_analysed:0},sources:[],
   batch:{batch_id:id,filename:'current.csv',artifact_id:id},notice:'Первичный разбор, не разрешение участия.',items:[{id:'t1',number:'0033',title:'Поставка болтов М16',decision:'take_in_work',reason:'Профильная группа',price:'1234.50',currency:'RUB',deadline_at:'2026-10-26T10:00:00+03:00',source_url:'https://zakupki.kontur.ru/0033',risks:['Проверить ТЗ'],next_steps:['Запросить документы'],evidence_indexes:[],production_readiness:'unknown',economics:'unknown'}]}}
 await page.route('**/api/v0.1/**',async route=>{
  const r=route.request(),path=new URL(r.url()).pathname
  if(path.endsWith('/report.pdf'))return route.fulfill({contentType:'application/pdf',body:Buffer.from('%PDF-1.4\n%%EOF')})
  if(path.endsWith('/artifacts')) return route.fulfill({json:{artifacts:[{artifact_id:id,filename:'selected.csv',usage_role:'procurement',processing_state:'ready',completed_chunks:2,total_chunks:2,media_type:'text/csv',size:100,created_at:date,dataset:null},{artifact_id:second,filename:'not-selected.txt',media_type:'text/plain',size:100,created_at:date,dataset:null},{artifact_id:stock,filename:'stock.csv',media_type:'text/csv',size:100,created_at:date,usage_role:'stock',processing_state:'ready',completed_chunks:2,total_chunks:2},{artifact_id:economics,filename:'economics.csv',media_type:'text/csv',size:100,created_at:date,usage_role:'economics',processing_state:'ready',completed_chunks:2,total_chunks:2}],next_cursor:null}})
  if(path.endsWith('/radar/batches')) return route.fulfill({json:[{batch_id:id,artifact_id:id,filename:'current.csv',count:6,created_at:date,warnings:[],skipped_rows:0,duplicate_rows:0}]})
  if(path.endsWith('/radar/analyse') && r.method()==='POST'){sent=r.postDataJSON();return route.fulfill({status:202,json:{...run,state:'queued',report:null}})}
  if(path.endsWith('/radar/runs')) return route.fulfill({json:[]})
  return route.fulfill({json:run})
 })
 try{
  await page.goto((process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1') + '/#/radar')
  await page.getByRole('heading',{name:'Тендерный радар',exact:true}).waitFor()
  const start=page.getByRole('button',{name:'Подобрать тендеры',exact:true})
  assert.equal(await start.isEnabled(),true)
  await page.getByRole('combobox',{name:'Выгрузка тендеров',exact:true}).selectOption(id)
  await start.click()
  await page.getByRole('heading',{name:'Поставка болтов М16',exact:true}).waitFor()
  assert.deepEqual(sent.procurement_source,{artifact_id:id})
  assert.deepEqual(sent.stock_source,{artifact_id:stock})
  assert.deepEqual(sent.economics_source,{artifact_id:economics})
  assert.equal(JSON.stringify(sent).includes(second),false)
  await page.getByRole('button',{name:'Скачать PDF',exact:true}).waitFor()
  const downloaded=page.waitForEvent('download')
  await page.getByRole('button',{name:'Скачать PDF',exact:true}).click()
  assert.equal((await downloaded).suggestedFilename(),`bervel-radar-${id}.pdf`)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.reload()
  await page.getByRole('heading',{name:'Поставка болтов М16',exact:true}).waitFor()
  await page.evaluate(()=>localStorage.removeItem('bervel.radar.run_id'))
  await page.goto((process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1') + '/#/radar?run='+id)
  await page.getByRole('heading',{name:'Поставка болтов М16',exact:true}).waitFor()
 } finally {await browser.close()}
})

for(const format of ['json','html']) test(`definite HTTP rejection releases the saved attempt and editable selection (${format})`,async()=>{
 const browser=await chromium.launch({headless:true}),page=await browser.newPage()
 const id='11111111-1111-4111-8111-111111111111',date='2026-10-22T12:00:00Z'
 await page.route('**/api/v0.1/**',route=>{
  const request=route.request(),path=new URL(request.url()).pathname
  if(request.method()==='POST')return format==='json'?route.fulfill({status:422,json:{error:{message:'Отклонён неверный выбор'}}}):route.fulfill({status:403,contentType:'text/html',body:'<p>Forbidden</p>'})
  if(path.endsWith('/artifacts'))return route.fulfill({json:{artifacts:[{artifact_id:id,filename:'profile.csv',media_type:'text/csv',size:100,created_at:date,dataset:null,usage_role:'procurement',processing_state:'ready',completed_chunks:2,total_chunks:2},...['stock','economics'].map((usage_role,i)=>({artifact_id:i?'33333333-3333-4333-8333-333333333333':'22222222-2222-4222-8222-222222222222',filename:usage_role+'.csv',media_type:'text/csv',size:100,created_at:date,usage_role,processing_state:'ready',completed_chunks:2,total_chunks:2}))],next_cursor:null}})
  if(path.endsWith('/batches'))return route.fulfill({json:[{batch_id:id,artifact_id:id,filename:'t.csv',count:6,created_at:date,warnings:[],skipped_rows:0,duplicate_rows:0}]})
  return route.fulfill({json:[]})
 })
 try{
  await page.goto((process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1') + '/#/radar')
  const choice=page.getByRole('combobox',{name:'Выгрузка тендеров',exact:true})
  await choice.selectOption(id);await page.getByRole('button',{name:'Подобрать тендеры',exact:true}).click()
  await page.getByRole('alert').filter({hasText:format==='json'?'Отклонён неверный выбор':'API радара вернул неверный формат ответа.'}).waitFor()
  assert.equal(await choice.isEnabled(),true)
  assert.equal(await page.evaluate(()=>localStorage.getItem('bervel.radar.analyse_attempt')),null)
 }finally{await browser.close()}
})

test('direct assessment displays product fit and quotes independently of exclusion, on mobile', async()=>{
 const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:390,height:900}})
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 page.setDefaultTimeout(5000)
 const id='11111111-1111-4111-8111-111111111111',date='2026-10-22T12:00:00Z'
 const run={run_id:id,batch_id:id,created_at:date,state:'completed',focus:'',sources_snapshot:[],model:'test',error:null,
  report:{schema_version:'1.1.0',as_of:date,counts:{total:1,model_analysed:1,take_in_work:0,needs_review:0,excluded:1,not_analysed:0},sources:[],
   batch:{filename:'current.csv'},notice:'Все импортированные тендеры оценены моделью.',items:[{id:'t1',number:'0033',title:'Поставка болтов М16',
    decision:'excluded',fit:'relevant',reason:'Профильная группа, срок истёк.',product_group:'Болт',tender_quote:'болтов М16',profile_quote:'Болт ГОСТ7798 М16',
    price:null,currency:'RUB',deadline_at:null,source_observed_at:null,source_url:'https://example.org/0033',risks:[],next_steps:[],evidence_indexes:[]}]}}
 await page.route('**/api/v0.1/**',route=>{
  const path=new URL(route.request().url()).pathname
  if(path.endsWith('/artifacts'))return route.fulfill({json:{artifacts:[],next_cursor:null}})
  if(path.endsWith('/batches')||path.endsWith('/runs'))return route.fulfill({json:[]})
  return route.fulfill({json:run})
 })
 try{
  await page.goto((process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1')+'/#/radar?run='+id)
  await page.getByText('Товарное соответствие: Соответствует профилю',{exact:true}).waitFor()
  await page.getByText('Группа: Болт',{exact:true}).waitFor()
  await page.getByText('болтов М16',{exact:true}).waitFor()
  await page.getByText('Болт ГОСТ7798 М16',{exact:true}).waitFor()
  assert.equal(await page.getByRole('button',{name:'Вне анализа (1)',exact:true}).getAttribute('aria-pressed'),'true')
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  if(process.env.BERVEL_RADAR_SCREENSHOTS){
   const folder=process.env.BERVEL_RADAR_SCREENSHOTS
   await page.evaluate(()=>document.fonts.ready)
   await page.screenshot({path:folder+'/radar-direct-mobile.png',fullPage:true})
   await page.setViewportSize({width:1600,height:1000})
   await page.screenshot({path:folder+'/radar-direct-desktop.png',fullPage:true})
  }
  assert.deepEqual(errors,[])
 }finally{await browser.close()}
})
