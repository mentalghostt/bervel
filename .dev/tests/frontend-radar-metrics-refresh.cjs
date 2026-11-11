const {mkdirSync,writeFileSync}=require('node:fs')
const path=require('node:path'),assert=require('node:assert/strict'),{test}=require('node:test')
const artifacts=path.resolve(process.env.BERVEL_TEST_ARTIFACT_ROOT||'.dev/local/radar-metrics-refresh-2026-11-09')
mkdirSync(path.join(artifacts,'browser-tmp'),{recursive:true});process.env.TMP=path.join(artifacts,'browser-tmp');process.env.TEMP=process.env.TMP
const {chromium}=require('playwright')
const ids={test_api:'33333333-3333-4333-8333-333333333333',kontur_api:'44444444-4444-4444-8444-444444444444'}
const sid='11111111-1111-4111-8111-111111111111',jid='55555555-5555-4555-8555-555555555555',stamp='2026-11-09T09:00:00Z',pendingKey='bervel.radar.profile.refresh.v1'
const schedule={enabled:false,every_days:1,time:'09:00',timezone:'Europe/Moscow'}
const profile=source=>({source,name:source==='test_api'?'Симуляция контур.закупки':'контур.закупки',run_id:ids[source],keyword_mode:'auto',keywords:[],schedule,available:true,last_refresh_at:stamp,next_refresh_at:null,quotas:{search:200,detail:200}})
function card(id,{currency='RUB',price='1000000',coverage='40',margin='25',rating={status:'complete',score:83,prompt_version:'card-individual-rating-v1'}}={}){
 return {analysis_id:'analysis-'+id,candidate_id:'candidate-'+id,state:'accepted',details_loaded:true,information_state:'full',evaluated_at:stamp,
  tender:{title:'Тендер '+id,number:'N'+id,link:null,documents:[]},analysis:{coverage_percent:coverage,benefit_percent:margin,economics:{scope:'confirmed',revenue:'1000',cost:'750',profit:'250',margin_percent:margin,currency,reason:null,status:'profitable'},positions:[{id:'p',name:'Болт',required:'100',covered:coverage,shortage:String(100-Number(coverage)),unit:'шт',stock_status:'partial'}]},
  fulfillment:{status:'feasible',reasons:[],production_allocations:[]},assessment:null,
  opportunity:{score:25,basis:'confirmed',known_share:'0.4',margin_percent:margin},review:{state:'complete',decision:'review',recommendation:'Сохранённое ревью по исходному складу.',risks:[],assumptions:[],evidence_refs:[]},
  metrics:{price,currency,stage:'accepting',actuality:'active',trade_type:'fz44',catalog_match_percent:'100',catalog_match_confirmed:1,catalog_match_unknown:0,catalog_match_total:1,stock_coverage_percent:coverage,catalog_match_incomplete:false,stock_coverage_incomplete:false},rating}
}
const run=source=>({run_id:ids[source],settings_snapshot_id:sid,created_at:stamp,data:{state:'complete',phase:'complete',checkpoint:{search_attempts:56},detail_attempts:200},cards:[card('one'),card('two',{price:'100000',coverage:'80',margin:'-200'})],reports:[]})
async function fixture(action,{width=1440,empty=false,available=true}={}){
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'})
 try{
  const page=await browser.newPage({viewport:{width,height:width<650?844:1000},hasTouch:width<650});page.setDefaultTimeout(6500)
  const h={runs:{test_api:run('test_api'),kontur_api:run('kontur_api')},profiles:{test_api:{...profile('test_api'),available},kontur_api:profile('kontur_api')},posts:[],reads:[],metrics:null,resume:null,refresh:null}
  if(empty)h.runs.test_api.cards=[]
  const errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.route('**/api/v0.1/**',route=>{const req=route.request(),p=new URL(req.url()).pathname
   if(p.endsWith('/profiles'))return route.fulfill({json:{profiles:Object.values(h.profiles)}})
   if(/\/profiles\/(test_api|kontur_api)$/.test(p)){const source=p.split('/').pop();h.reads.push(source);return route.fulfill({json:{profile:h.profiles[source],run:h.runs[source]}})}
   if(/\/profiles\/(test_api|kontur_api)\/(metrics|resume|refresh|schedule)$/.test(p)){const source=p.split('/').at(-2),kind=p.split('/').pop(),body=req.postDataJSON();h.posts.push({source,kind,body});if(kind==='schedule'){h.profiles[source]={...h.profiles[source],schedule:body.schedule,auto_reanalyze_existing:body.reanalyze_existing};return route.fulfill({json:h.profiles[source]})}return h[kind]?h[kind](route,source):route.fulfill({status:202,json:{job_id:jid,run_id:ids[source],state:'queued'}})}
   if(p.endsWith('/question')){h.posts.push({kind:'question',body:req.postDataJSON()});return route.fulfill({status:202,json:{job_id:jid,run_id:ids.test_api,state:'queued'}})}
   if(p.endsWith('/jobs/'+jid))return route.fulfill({json:{job_id:jid,state:'running'}})
   return route.fulfill({status:404,json:{error:{message:'Unexpected mocked route '+p}}})
  })
  const open=async(source='test_api')=>{await page.goto((process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1:5188')+'/#/ai-tender-radar?profile='+source);await page.getByRole('button',{name:'Фильтр',exact:true}).waitFor()}
  await action({page,h,open});assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 }finally{await browser.close()}
}

for(const width of [1440,390,400])test('thin range controls retain both price endpoints and keyboard/manual input '+width,()=>fixture(async({page,open})=>{
 await open();await page.getByRole('button',{name:'Фильтр',exact:true}).click()
 const panel=page.getByRole('region',{name:'Фильтр результатов',exact:true}),ranges=panel.getByRole('slider')
 const styles=await ranges.evaluateAll(inputs=>inputs.map(el=>{const s=getComputedStyle(el);return {name:el.getAttribute('aria-label'),height:s.height,padding:s.padding,border:s.borderWidth,radius:s.borderRadius,background:s.backgroundColor}}))
 const prefix=process.env.BERVEL_SCREENSHOT_PREFIX||'filters';writeFileSync(path.join(artifacts,prefix+'-styles-'+width+'.json'),JSON.stringify(styles,null,2));await page.screenshot({path:path.join(artifacts,prefix+'-'+width+'.png'),fullPage:false})
 for(const style of styles){assert.equal(style.height,'26px');assert.equal(style.padding,'0px');assert.equal(style.border,'0px');assert.equal(style.radius,'0px');assert.equal(style.background,'rgba(0, 0, 0, 0)')}
 assert.equal(await panel.getByLabel('Валюта стоимости',{exact:true}).count(),0)
 const low=panel.getByRole('slider',{name:'Стоимость тендера от',exact:true}),high=panel.getByRole('slider',{name:'Стоимость тендера до',exact:true}),track=panel.locator('.radar-filter-price-track')
 await track.scrollIntoViewIfNeeded();const box=await track.boundingBox(),left=box.x+8,right=box.x+box.width-8,y=box.y+box.height/2
 await page.mouse.move(left,y);await page.mouse.down();await page.mouse.move(left+(right-left)*.25,y,{steps:5});await page.mouse.up();assert.ok(Number(await low.inputValue())>240000 && Number(await low.inputValue())<260000)
 await page.mouse.move(left+(right-left)*.25,y);await page.mouse.down();await page.mouse.move(left,y,{steps:5});await page.mouse.up();assert.equal(await low.inputValue(),'0')
 await page.mouse.move(right,y);await page.mouse.down();await page.mouse.move(left+(right-left)*.75,y,{steps:5});await page.mouse.up();assert.ok(Number(await high.inputValue())>740000 && Number(await high.inputValue())<760000)
 await high.focus();await page.keyboard.press('End');assert.equal(await high.inputValue(),'1000000');await low.focus();await page.keyboard.press('End');assert.equal(await low.inputValue(),'1000000');await page.keyboard.press('Tab');assert.equal(await high.evaluate(el=>el===document.activeElement),true)
 await low.focus();await page.keyboard.press('ArrowLeft');assert.equal(await low.inputValue(),'999999');await page.keyboard.press('Home');assert.equal(await low.inputValue(),'0');await high.focus();await page.keyboard.press('Home');assert.equal(await high.inputValue(),'0');await page.keyboard.press('End');assert.equal(await high.inputValue(),'1000000')
 const coverage=panel.getByLabel('Покрытие склада от, число',{exact:true});await coverage.fill('45');await coverage.blur();assert.equal(await panel.getByRole('slider',{name:'Покрытие склада от',exact:true}).inputValue(),'45')
 const margin=panel.getByLabel('Маржинальность от, число',{exact:true});await margin.fill('-500');await margin.blur();assert.equal(await margin.inputValue(),'-200')
 await panel.getByRole('button',{name:'Сбросить',exact:true}).click();assert.equal(await coverage.inputValue(),'0');await page.keyboard.press('Escape');assert.equal(await page.getByRole('button',{name:'Фильтр',exact:true}).evaluate(el=>el===document.activeElement),true)
},{width}))

test('price range prefers RUB without hiding other currencies until a price threshold is set',()=>fixture(async({page,h,open})=>{
 h.runs.test_api.cards=[card('eur',{currency:'EUR',price:'9000000'}),card('rub'),card('usd',{currency:'USD',price:'5000000'})]
 await open();assert.equal(await page.locator('.ai-radar-card-list>.ai-radar-card').count(),3);await page.getByRole('button',{name:'Фильтр',exact:true}).click()
 const low=page.getByRole('slider',{name:'Стоимость тендера от',exact:true});assert.equal(await low.getAttribute('max'),'1000000');assert.match(await page.locator('.radar-filter-price-bounds').innerText(),/₽/)
 await low.focus();await page.keyboard.press('ArrowRight');assert.equal(await page.locator('.ai-radar-card-list>.ai-radar-card').count(),1);assert.match(await page.locator('.ai-radar-card-list>.ai-radar-card').innerText(),/Тендер rub/)
}))

test('requested popup explanations are removed while manual quota and independent auto settings remain',()=>fixture(async({page,h,open})=>{
 await open();await page.getByRole('button',{name:'Профили',exact:true}).click();let dialog=page.getByRole('dialog');assert.equal(await dialog.getByText(/Склад и продукция — из сохранённого профиля/).count(),0);await dialog.getByRole('button',{name:'Закрыть диалог'}).click()
 await page.getByRole('button',{name:'Обновить',exact:true}).click();dialog=page.getByRole('dialog');assert.equal(await dialog.getByText(/Доступно подробных карточек/).count(),0);assert.equal(await dialog.getByText(/Авто расходует доступные квоты поиска/).count(),0)
 assert.equal(await dialog.getByLabel('Подробных карточек',{exact:true}).getAttribute('max'),'200');assert.equal(await dialog.getByRole('checkbox',{name:'Проанализировать на основе измененных показателей',exact:true}).count(),1);assert.equal(await dialog.getByLabel('Время, МСК').count(),0);await dialog.getByRole('button',{name:'Закрыть диалог'}).click()
 await page.getByRole('button',{name:'Автообновление',exact:true}).click();dialog=page.getByRole('dialog');assert.equal(await dialog.getByLabel('Подробных карточек',{exact:true}).count(),0);await dialog.getByRole('checkbox',{name:'Автообновление',exact:true}).check();await dialog.getByLabel('Каждые, дней').fill('3');await dialog.getByLabel('Время, МСК').fill('10:30');await dialog.getByRole('button',{name:'Сохранить',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('dialog[open]'));assert.equal(h.posts.length,1);assert.equal(h.posts[0].kind,'schedule');assert.deepEqual(h.posts[0].body.schedule,{enabled:true,every_days:3,time:'10:30',timezone:'Europe/Moscow'});assert.equal(Object.hasOwn(h.posts[0].body,'detail_max'),false)
}))

test('metrics command is one durable POST, blocks writer commands and keeps the tender chat independent',()=>fixture(async({page,h,open})=>{
 let accept;const requested=new Promise(resolve=>{h.metrics=route=>new Promise(finish=>{accept=async()=>{h.runs.test_api.data={...h.runs.test_api.data,state:'running',phase:'metrics',checkpoint:{metrics_completed:1,metrics_total:2}};await route.fulfill({status:202,json:{job_id:jid,run_id:ids.test_api,state:'queued'}});finish()};resolve()})})
 await open();const button=page.getByRole('button',{name:'Обновить показатели',exact:true});await button.evaluate(el=>{el.click();el.click()});await requested
 assert.equal(h.posts.length,1);assert.equal(h.posts[0].kind,'metrics');assert.deepEqual(Object.keys(h.posts[0].body),['command_key']);const saved=await page.evaluate(key=>JSON.parse(localStorage.getItem(key)),pendingKey);assert.equal(saved.kind,'metrics');assert.deepEqual(saved.body,h.posts[0].body)
 assert.equal(await button.isDisabled(),true);assert.equal(await page.getByRole('button',{name:'Обновить',exact:true}).isDisabled(),true);await accept();await page.getByText('Обновляю показатели: 1 из 2',{exact:true}).waitFor()
 await page.getByRole('button',{name:'Тендер one',exact:true}).click();assert.equal(await page.getByLabel('Вопрос по тендеру',{exact:true}).isEnabled(),true)
}))

test('unknown metrics command survives reload and profile switch with the same command key and source',()=>fixture(async({page,h,open})=>{
 h.metrics=route=>route.abort('failed');await open();await page.getByRole('button',{name:'Обновить показатели',exact:true}).click();await page.getByRole('button',{name:'Повторить сохранённое обновление показателей',exact:true}).waitFor()
 const first=h.posts[0];await page.reload();await page.getByRole('button',{name:'Повторить сохранённое обновление показателей',exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Обновить показатели',exact:true}).isDisabled(),true);assert.equal(await page.getByRole('button',{name:'Обновить',exact:true}).isDisabled(),true)
 await page.getByRole('button',{name:'Профили',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'контур.закупки',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Закрыть диалог'}).click()
 h.metrics=null;await page.getByRole('button',{name:'Повторить сохранённое обновление показателей',exact:true}).click();await page.waitForFunction(key=>!localStorage.getItem(key),pendingKey)
 assert.deepEqual(h.posts[1],first);assert.match(page.url(),/profile=kontur_api/)
}))

test('empty saved base disables metrics and a failed metrics job can resume without source credentials',()=>fixture(async({page,h,open})=>{
 await open();assert.equal(await page.getByRole('button',{name:'Обновить показатели',exact:true}).isDisabled(),true)
 h.runs.test_api.cards=[card('one')];h.runs.test_api.data={state:'failed',phase:'metrics',resume_kind:'metrics',checkpoint:{metrics_completed:0,metrics_total:1},error:{code:'LOCAL_CALCULATION_ERROR',message:'Локальное вычисление прервано'}}
 await page.reload();const resume=page.getByRole('button',{name:'Возобновить',exact:true});await resume.waitFor();assert.equal(await resume.isEnabled(),true);assert.equal(await page.getByRole('button',{name:'Обновить показатели',exact:true}).isEnabled(),true);await resume.click();await page.waitForFunction(key=>!localStorage.getItem(key),pendingKey);assert.equal(h.posts[0].kind,'resume')
},{empty:true,available:false}))

test('completed local metrics receipt is reported while an earlier full refresh still needs resume',()=>fixture(async({page,h,open})=>{
 h.runs.test_api.data={state:'failed',phase:'failed',checkpoint:{search_attempts:56},error:{code:'SOURCE_UNAVAILABLE',message:'Полное обновление не завершено'},metrics_job:{job_id:jid,state:'loaded'}}
 await open();await page.getByText('Показатели обновлены.',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Возобновить',exact:true}).isEnabled(),true);await page.getByText('Полное обновление не завершено',{exact:true}).waitFor()
}))

test('metrics DTO validates receipt/progress and historical score without treating it as a current score',async()=>{
 const {run:parse}=await import('../../frontend/src/aiRadarRunsApi.ts'),{resultScore}=await import('../../frontend/src/aiRadarReviewSummary.ts')
 const value=run('test_api');value.data.metrics_job={job_id:jid,state:'loaded'};value.data.checkpoint={metrics_completed:2,metrics_total:2};value.cards[0].rating={status:'stale',score:null,previous_score:0};assert.equal(resultScore(parse(value,true).cards[0]),null)
 for(const previous_score of [true,-1,101])assert.throws(()=>parse({...value,cards:[{...value.cards[0],rating:{status:'stale',score:null,previous_score}}]},true),/неожиданный формат/)
 assert.throws(()=>parse({...value,data:{...value.data,metrics_job:{job_id:'bad',state:'invented'}}},true),/неожиданный формат/)
 assert.throws(()=>parse({...value,data:{...value.data,checkpoint:{metrics_completed:-1}}},true),/неожиданный формат/)
})

for(const previous of [0,83])test('previous AI score '+previous+' is labelled and excluded from current score filters',()=>fixture(async({page,h,open})=>{
 const changed=card('stale',{coverage:'80',margin:'35',rating:{status:'stale',score:null,previous_score:previous,prompt_version:'card-individual-rating-v1'}});changed.projection={version:1,review_stale:false,metrics_review_stale:true}
 h.runs.test_api.cards=[changed,card('fresh',{rating:{status:'complete',score:42}})];await open();const article=page.getByRole('button',{name:'Тендер stale',exact:true})
 const metric=article.locator('dt').filter({hasText:'Оценка до обновления показателей'}).locator('..');await metric.locator('dd').getByText(String(previous),{exact:true}).waitFor()
 await page.getByRole('button',{name:'Тендер stale',exact:true}).click();await page.getByRole('heading',{name:'Ревью до обновления показателей',exact:true}).waitFor();await page.getByText(/Сохранённое ревью по исходному складу/).waitFor();await page.getByRole('button',{name:'Закрыть диалог'}).click()
 await page.getByRole('button',{name:'Фильтр',exact:true}).click();await page.getByLabel('Оценка ИИ от, число',{exact:true}).fill('1');await page.getByLabel('Оценка ИИ от, число',{exact:true}).blur();assert.equal(await page.locator('.ai-radar-card-list>.ai-radar-card').count(),1);assert.equal(await page.getByRole('button',{name:'Тендер stale',exact:true}).count(),0)
}))
