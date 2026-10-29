const {test}=require('node:test')
const assert=require('node:assert/strict')
const {chromium}=require('playwright')

test('radar pins stock source and shortage filter, displays mock arithmetic without mobile overflow',async()=>{
 const browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined})
 const page=await browser.newPage({viewport:{width:390,height:900}})
 page.setDefaultTimeout(5000)
 const id='11111111-1111-4111-8111-111111111111',stock='22222222-2222-4222-8222-222222222222',date='2026-10-27T12:00:00Z'
 let sent
 const source={artifact_id:stock,filename:'stock-mock.csv'}
 const report={schema_version:'1.2.0',as_of:date,batch:{filename:'tenders.csv'},notice:'Проверка по свободному остатку.',sources:[],
  selection:{stock_source:source,max_shortage_percent:'10'},stock_source:source,stock_kind:'mock',
  funnel:{total:1,after_eligibility:1,after_names:1,semantic_relevant:1,quantity_passed:1,model_requests:1},
  counts:{total:1,model_analysed:1,take_in_work:1,needs_review:0,excluded:0,not_analysed:0},
  items:[{id:'t',number:'001',title:'Поставка метизов',decision:'take_in_work',decision_stage:'quantity',reason:'Позиция подтверждена.',
   price:null,currency:'RUB',deadline_at:date,source_observed_at:date,source_url:'https://example.org/1',risks:[],next_steps:[],evidence_indexes:[],
   stock_check:{status:'pass',allowed_shortage_percent:'10',reasons:[],checks:[{sku:'P1',product:'Болт М16',unit:'шт',required:'1000',available:'900',shortage:'100',shortage_percent:'10',within_tolerance:true,source_row:2}]}}]}
 const run={run_id:id,batch_id:id,created_at:date,state:'completed',focus:'',sources_snapshot:[],report,model:'fixture',error:null}
 await page.route('**/api/v0.1/**',async route=>{
  const path=new URL(route.request().url()).pathname
  if(path.endsWith('/artifacts'))return route.fulfill({json:{artifacts:[{artifact_id:id,filename:'profile.csv',media_type:'text/csv',size:100,created_at:date,usage_role:'procurement',processing_state:'ready',completed_chunks:2,total_chunks:2},{...source,media_type:'text/csv',size:200,created_at:date,usage_role:'stock',processing_state:'ready',completed_chunks:2,total_chunks:2},{artifact_id:'33333333-3333-4333-8333-333333333333',filename:'economics.csv',media_type:'text/csv',size:200,created_at:date,usage_role:'economics',processing_state:'ready',completed_chunks:2,total_chunks:2}],next_cursor:null}})
  if(path.endsWith('/batches'))return route.fulfill({json:[{batch_id:id,artifact_id:id,filename:'tenders.csv',count:1,created_at:date}]})
  if(path.endsWith('/analyse')&&route.request().method()==='POST'){sent=route.request().postDataJSON();return route.fulfill({status:202,json:{...run,state:'queued',report:null}})}
  if(path.endsWith('/runs'))return route.fulfill({json:[]})
  return route.fulfill({json:run})
 })
 try{
  await page.goto((process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1:5174')+'/#/radar')
  await page.getByRole('combobox',{name:'Выгрузка тендеров',exact:true}).selectOption(id)
  await page.getByRole('combobox',{name:'Данные по складу',exact:true}).selectOption(stock)
  await page.getByRole('spinbutton',{name:'Допустимая нехватка, %',exact:true}).fill('101')
  await page.getByRole('button',{name:'Подобрать тендеры',exact:true}).click()
  assert.equal(await page.getByRole('spinbutton',{name:'Допустимая нехватка, %',exact:true}).evaluate(input=>input.validity.rangeOverflow),true)
  assert.equal(sent,undefined)
  assert.equal(await page.getByRole('button',{name:'Подобрать тендеры',exact:true}).isEnabled(),true)
  await page.getByRole('spinbutton',{name:'Допустимая нехватка, %',exact:true}).fill('10')
  await page.getByRole('button',{name:'Подобрать тендеры',exact:true}).click()
  await page.getByRole('heading',{name:'Результат отбора',exact:true}).waitFor()
  assert.deepEqual(sent.stock_source,{artifact_id:stock})
  assert.equal(sent.filters.max_shortage_percent,'10')
  assert.deepEqual(sent.procurement_source,{artifact_id:id})
  await page.getByText('Демонстрационные источники: склад.',{exact:true}).waitFor()
  const table=page.getByRole('table',{name:'Расчёт свободного остатка',exact:true})
  assert.match(await table.innerText(),/1000/)
  assert.match(await table.innerText(),/900/)
  assert.match(await table.innerText(),/100/)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  if(process.env.BERVEL_QA_DIR){
   await page.screenshot({path:process.env.BERVEL_QA_DIR+'/radar-mobile.png',fullPage:true})
   await page.setViewportSize({width:1440,height:1000})
   await page.screenshot({path:process.env.BERVEL_QA_DIR+'/radar-desktop.png',fullPage:true})
  }
 }finally{await browser.close()}
})
