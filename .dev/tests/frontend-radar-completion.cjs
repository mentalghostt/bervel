const {test}=require('node:test')
const assert=require('node:assert/strict')
const path=require('node:path')
const {mkdirSync}=require('node:fs')

const ids={test_api:'33333333-3333-4333-8333-333333333333',kontur_api:'44444444-4444-4444-8444-444444444444'}
const sid='11111111-1111-4111-8111-111111111111',jid='55555555-5555-4555-8555-555555555555',stamp='2026-11-10T09:00:00Z'
const schedule={enabled:false,every_days:1,time:'09:00',timezone:'Europe/Moscow'}
const summary={version:'profile-completion-v1',failed_tenders:2,primary_failed:0,detail_failed:0,analysis_failed:1,rating_failed:1,metrics_failed:0}
const warning={stage:'analysis',code:'MODEL_INVALID_OUTPUT',message:'Позиция документа отсутствует в подтверждённом составе закупки.'}
const card=(id='saved')=>({analysis_id:'analysis-'+id,candidate_id:'candidate-'+id,state:'accepted',details_loaded:true,information_state:'full',evaluated_at:stamp,
 tender:{title:'Тендер '+id,number:'N-'+id,link:null},analysis:{coverage_percent:'40',benefit_percent:'25',economics:{scope:'confirmed',revenue:'100',cost:'75'},positions:[]},
 fulfillment:null,assessment:null,review:{state:'complete',decision:'review',recommendation:'Сохранённое ревью по исходному складу.',risks:[],assumptions:[],evidence_refs:[]},rating:{status:'complete',score:83}})
const run=(source='test_api')=>({run_id:ids[source],settings_snapshot_id:sid,created_at:stamp,data:{state:'complete',phase:'complete'},cards:[card()],reports:[]})

test('formatter distinguishes terminal local warnings from failed, stopped and active updates',async()=>{
 const {radarRefreshStatus:status}=await import('../../frontend/src/radarRefreshStatus.ts')
 const value=run();value.data.completion_summary=summary
 assert.equal(status(value),'Обновление завершено с замечаниями: 2 тендеров')
 assert.equal(status({data:{...value.data,completion_summary:{...summary,failed_tenders:0}}}),'База сохранена')
 assert.equal(status(run()),'База сохранена')
 assert.equal(status({data:{...value.data,state:'failed',error:{code:'KONTUR_AUTH_ERROR',message:'Источник требует авторизации.'}}}),'Обновление завершилось ошибкой')
 assert.equal(status({data:{...value.data,state:'failed',error:{code:'USER_STOPPED',message:'Остановлено.'}}}),'Обновление остановлено')
 assert.equal(status({data:{...value.data,state:'running',phase:'analysis',checkpoint:{analysis_completed:377,analysis_total:400}}}),'Анализирую детали тендеров: 377 из 400')
})

test('parser rejects malformed optional completion summary while keeping old DTOs',async()=>{
 const {run:parse}=await import('../../frontend/src/aiRadarRunsApi.ts')
 assert.deepEqual(parse(run(),true),run())
 const valid=run();valid.data.completion_summary=summary
 assert.deepEqual(parse(valid,true).data.completion_summary,summary)
 const missing={...summary};delete missing.rating_failed
 const invalid=[null,[],{},missing,{...summary,version:'profile-completion-v2'},
  ...['failed_tenders','primary_failed','detail_failed','analysis_failed','rating_failed','metrics_failed'].flatMap(key=>[-1,0.5,'1',null,NaN,Infinity].map(value=>({...summary,[key]:value})))]
 for(const value of invalid){const dto=run();dto.data.completion_summary=value;assert.throws(()=>parse(dto,true),/неожиданный формат/,JSON.stringify(value))}
})

test('parser validates every public card warning without inventing a score or changing saved analysis',async()=>{
 const {run:parse}=await import('../../frontend/src/aiRadarRunsApi.ts')
 const valid=run();valid.cards[0].refresh_warnings=['primary','detail','analysis','rating','metrics'].map(stage=>({...warning,stage}))
 assert.deepEqual(parse(valid,true).cards[0].refresh_warnings,valid.cards[0].refresh_warnings)
 assert.equal(parse(valid,true).cards[0].rating.score,83)
 assert.equal(parse(valid,true).cards[0].analysis.coverage_percent,'40')
 for(const value of [null,{},'warning',[null],[{}],[{...warning,stage:'review'}],[{...warning,stage:'details'}],[{...warning,stage:['analysis']}],[{...warning,code:3}],[{...warning,message:null}]]){
  const dto=run();dto.cards[0].refresh_warnings=value;assert.throws(()=>parse(dto,true),/неожиданный формат/,JSON.stringify(value))
 }
 const empty=run();empty.cards[0].refresh_warnings=[];assert.deepEqual(parse(empty,true).cards[0].refresh_warnings,[])
})

async function fixture(action,{width=1440,state='running',finished=false}={}){
 const artifacts=path.resolve(process.env.BERVEL_TEST_ARTIFACT_ROOT||'.dev/local/radar-completion-2026-11-10/frontend')
 mkdirSync(path.join(artifacts,'browser-tmp'),{recursive:true});process.env.TMP=path.join(artifacts,'browser-tmp');process.env.TEMP=process.env.TMP
 const {chromium}=require('playwright')
 const browser=await chromium.launch({headless:true,executablePath:process.env.BERVEL_BROWSER_PATH})
 try{
  const page=await browser.newPage({viewport:{width,height:width<650?844:1000}});page.setDefaultTimeout(7000)
  const profiles=Object.keys(ids).map(source=>({source,name:source,run_id:ids[source],keyword_mode:'auto',keywords:[],schedule,available:true,last_refresh_at:stamp,next_refresh_at:null,quotas:{search:200,detail:200},reanalyze_existing:true,auto_reanalyze_existing:false}))
  const h={reads:0,posts:[],finished},errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/**',async route=>{
   const request=route.request(),url=new URL(request.url()).pathname
   if(url.endsWith('/profiles'))return route.fulfill({json:{profiles}})
   if(/\/profiles\/(test_api|kontur_api)$/.test(url)){
    const source=url.split('/').pop(),value=run(source);h.reads++
    value.cards=[{...card('saved'),refresh_warnings:[warning]},{...card('unrated'),rating:{status:'failed',score:null},refresh_warnings:[{stage:'rating',code:'MODEL_INVALID_OUTPUT',message:'Оценка модели не получена.'}]},card('ready')]
    value.data=h.finished?{state:'complete',phase:'complete',completion_summary:summary,checkpoint:{analysis_completed:399,analysis_total:400}}:{state,phase:state==='running'?'analysis':state,checkpoint:{analysis_completed:377,analysis_total:400},...(state==='failed'?{error:{code:'KONTUR_AUTH_ERROR',message:'Источник требует авторизации.'}}:{})}
    return route.fulfill({json:{profile:profiles.find(profile=>profile.source===source),run:value}})
   }
   if(request.method()==='POST'){
    h.posts.push({path:url,body:request.postDataJSON()})
    if(url.endsWith('/schedule')){const p=profiles.find(profile=>profile.source===url.split('/').at(-2));p.schedule=request.postDataJSON().schedule;p.auto_reanalyze_existing=request.postDataJSON().reanalyze_existing;return route.fulfill({json:p})}
    return route.fulfill({status:202,json:{job_id:jid,run_id:ids.test_api,state:'queued'}})
   }
   return route.fulfill({status:404,json:{error:{message:'Unexpected mocked route '+url}}})
  })
  await page.goto((process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1:5194')+'/#/ai-tender-radar?profile=test_api')
  await page.getByRole('button',{name:'Тендер saved',exact:true}).waitFor()
  await action({page,h,artifacts})
  assert.deepEqual(errors,[])
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 }finally{await browser.close()}
}

for(const width of [1440,390])test('browser finishes automatically with visible per-card reasons and retained analysis '+width,()=>fixture(async({page,h,artifacts})=>{
 await page.getByText('Анализирую детали тендеров: 377 из 400',{exact:true}).waitFor()
 assert.equal(await page.getByRole('button',{name:'Обновить',exact:true}).isDisabled(),true)
 assert.equal(await page.getByRole('button',{name:'Автообновление',exact:true}).isEnabled(),true)
 h.finished=true
 await page.getByText('Обновление завершено с замечаниями: 2 тендеров',{exact:true}).waitFor()
 assert.ok(h.reads>=2)
 assert.equal(await page.getByText('Обновление завершилось ошибкой',{exact:true}).count(),0)
 assert.equal(await page.getByRole('button',{name:'Возобновить',exact:true}).count(),0)
 assert.equal(await page.getByRole('button',{name:'Обновить',exact:true}).isEnabled(),true)
 assert.deepEqual(h.posts,[],'GET polling must never become a client resume or refresh loop')
 await page.screenshot({path:path.join(artifacts,'complete-'+width+'.png'),fullPage:false})
 await page.getByRole('button',{name:'Тендер saved',exact:true}).click()
 const popup=page.getByRole('dialog')
 await popup.getByRole('heading',{name:'Замечания последнего обновления',exact:true}).waitFor()
 await popup.getByText(warning.message,{exact:false}).waitFor()
 await popup.getByText('Показан сохранённый анализ.',{exact:true}).waitFor()
 await popup.getByText('Сохранённое ревью по исходному складу.',{exact:false}).waitFor()
 assert.equal(await popup.locator('.ai-radar-metrics dd').filter({hasText:/^83$/}).count(),1)
 await popup.getByRole('heading',{name:'Замечания последнего обновления',exact:true}).scrollIntoViewIfNeeded()
 const warningBox=await popup.getByText(warning.message,{exact:false}).boundingBox()
 assert.ok(warningBox&&warningBox.y>=0&&warningBox.y+warningBox.height<=page.viewportSize().height,'warning reason must be visible after scrolling the tender details')
 await page.screenshot({path:path.join(artifacts,'warning-popup-'+width+'.png'),fullPage:false})
 await popup.getByRole('button',{name:'Закрыть диалог'}).click()
 await page.getByRole('button',{name:'Тендер unrated',exact:true}).click()
 await page.getByRole('dialog').getByText('Оценка модели не получена.',{exact:false}).waitFor()
 assert.equal(await page.getByRole('dialog').locator('.ai-radar-metrics dd').filter({hasText:/^Ошибка$/}).count(),1)
 assert.deepEqual(h.posts,[])
},{width}))

test('browser keeps auto settings independent while server completes the active refresh',()=>fixture(async({page,h})=>{
 await page.getByRole('button',{name:'Автообновление',exact:true}).click()
 const popup=page.getByRole('dialog')
 await popup.getByLabel('Автообновление',{exact:true}).check()
 await popup.getByRole('button',{name:'Сохранить',exact:true}).click()
 await page.getByText('Настройки автообновления сохранены.',{exact:true}).waitFor()
 assert.equal(h.posts.length,1);assert.ok(h.posts[0].path.endsWith('/schedule'));assert.equal(h.posts[0].body.reanalyze_existing,false)
 await page.getByText('Анализирую детали тендеров: 377 из 400',{exact:true}).waitFor()
 h.finished=true;await page.getByText('Обновление завершено с замечаниями: 2 тендеров',{exact:true}).waitFor()
 await page.getByRole('button',{name:'Обновить',exact:true}).click()
 assert.equal(await page.getByRole('dialog').getByLabel('Проанализировать на основе измененных показателей',{exact:true}).isChecked(),true)
 assert.equal(h.posts.length,1)
}))

test('browser reload of terminal partial result reveals its saved-analysis warning',()=>fixture(async({page,h})=>{
 await page.getByRole('button',{name:'Тендер saved',exact:true}).click()
 const popup=page.getByRole('dialog')
 await popup.getByRole('heading',{name:'Замечания последнего обновления',exact:true}).waitFor()
 await popup.getByText(warning.message,{exact:false}).waitFor()
 await popup.getByText('Показан сохранённый анализ.',{exact:true}).waitFor()
 assert.deepEqual(h.posts,[])
},{finished:true}))

test('browser preserves global failure and manual recovery without automatic commands',()=>fixture(async({page,h})=>{
 await page.getByText('Обновление завершилось ошибкой',{exact:true}).waitFor()
 await page.getByText('Источник требует авторизации.',{exact:true}).waitFor()
 assert.equal(await page.getByRole('button',{name:'Возобновить',exact:true}).isEnabled(),true)
 assert.deepEqual(h.posts,[])
},{state:'failed'}))
