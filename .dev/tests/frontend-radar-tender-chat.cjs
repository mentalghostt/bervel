const {mkdirSync}=require('node:fs')
const path=require('node:path')
const task=path.resolve(process.env.BERVEL_TEST_ARTIFACT_ROOT||'.dev/local/radar-tender-chat-2026-11-09')
mkdirSync(path.join(task,'browser-tmp'),{recursive:true});process.env.TMP=path.join(task,'browser-tmp');process.env.TEMP=process.env.TMP
const {test}=require('node:test')
const assert=require('node:assert/strict')
const {chromium}=require('playwright')
const rid='33333333-3333-4333-8333-333333333333',sid='11111111-1111-4111-8111-111111111111'
const qid='55555555-5555-4555-8555-555555555555',other='66666666-6666-4666-8666-666666666666'
const stamp='2026-11-09T09:00:00Z',schedule={enabled:false,every_days:1,time:'09:00',timezone:'Europe/Moscow'}
const profile=source=>({source,name:source==='test_api'?'Симуляция контур.закупки':'контур.закупки',run_id:rid,keyword_mode:'auto',keywords:[],schedule,available:true,last_refresh_at:stamp,next_refresh_at:null,quotas:{search:200,detail:200}})
const card={analysis_id:'chat-card',candidate_id:'chat-candidate',state:'primary',information_state:'primary',details_loaded:false,last_seen_at:stamp,brief:{OrderName:'Поставка болтов'},tender:{title:'Поставка болтов',number:'N1',link:null,documents:[]},analysis:null,fulfillment:null,assessment:null,review:null,metrics:{price:'10000',currency:'RUB',stage:'accepting',actuality:'active',trade_type:'fz44',catalog_match_percent:null,stock_coverage_percent:null},rating:{status:'pending',score:null}}
const answer=text=>({state:'complete',recommendation:'',answer:text,risks:[],assumptions:[],evidence_refs:[]})
async function fixture(width,callback){
 const browser=await chromium.launch({headless:true,executablePath:process.env.BERVEL_BROWSER_PATH})
 const page=await browser.newPage({viewport:{width,height:900}});page.setDefaultTimeout(4000)
 const hooks={reports:[],state:'running',posts:[],post:null},errors=[];page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/v0.1/**',route=>{
  const req=route.request(),p=new URL(req.url()).pathname
  if(p.endsWith('/profiles'))return route.fulfill({json:{profiles:[profile('test_api'),profile('kontur_api')]}})
  if(p.endsWith('/profiles/test_api'))return route.fulfill({json:{profile:profile('test_api'),run:{run_id:rid,settings_snapshot_id:sid,created_at:stamp,data:{state:'complete',phase:'complete'},cards:[card,{...card,analysis_id:'another-card',candidate_id:'another'}],reports:hooks.reports}}})
  if(p.endsWith('/question')){hooks.posts.push(req.postDataJSON());return hooks.post?hooks.post(route):route.fulfill({status:202,json:{job_id:qid,run_id:rid,state:'queued'}})}
  if(p.endsWith('/jobs/'+qid))return route.fulfill({json:{job_id:qid,state:hooks.state}})
  return route.fulfill({status:404,json:{error:{message:'Unexpected mock route '+p}}})
 })
 const open=async()=>{await page.goto((process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1:5186')+'/#/ai-tender-radar?profile=test_api&analysis_id=chat-card');await page.getByLabel('Вопрос по тендеру',{exact:true}).waitFor()}
 try{await callback(page,hooks,open);assert.deepEqual(errors,[])}finally{await browser.close()}
}

test('accepted question remains visible before answer and after reload from browser recovery',()=>fixture(1440,async(page,h,open)=>{
 await open();await page.getByLabel('Вопрос по тендеру',{exact:true}).fill('Мой вопрос до ответа')
 assert.equal(await page.locator('[data-author="user"]').count(),0,'Draft must not appear as sent')
 await page.getByRole('button',{name:'Спросить',exact:true}).click()
 await page.locator('[data-author="user"]').getByText('Мой вопрос до ответа',{exact:true}).waitFor()
 assert.equal(await page.locator('[data-author="assistant"]').count(),0)
 await page.reload();await page.locator('[data-author="user"]').getByText('Мой вопрос до ответа',{exact:true}).waitFor()
 assert.equal(h.posts.length,1)
}))

test('server history displays pending, failed and completed questions once without browser recovery',()=>fixture(1440,async(page,h,open)=>{
 h.reports=[{job_id:qid,kind:'question',analysis_id:'chat-card',question:'Первый вопрос',result:null,state:'running',command_key:'saved-first'},
 {job_id:other,kind:'question',analysis_id:'chat-card',question:'Второй вопрос',result:answer('Второй ответ уже готов')},
 {job_id:'77777777-7777-4777-8777-777777777777',kind:'question',analysis_id:'another-card',question:'Чужой вопрос',result:answer('Чужой ответ')}]
 await open();await page.getByText('Первый вопрос',{exact:true}).waitFor()
 assert.deepEqual(await page.locator('[data-author="user"]').allTextContents(),['ВыПервый вопрос','ВыВторой вопрос'])
 assert.equal(await page.getByText('Чужой вопрос',{exact:true}).count(),0)
 await page.evaluate(()=>localStorage.clear());await page.reload();await page.getByText('Первый вопрос',{exact:true}).waitFor()
 h.reports[0]={...h.reports[0],state:'failed',error:{code:'MODEL_OUTCOME_UNKNOWN',message:'Исход ответа неизвестен.'}}
 await page.reload();await page.getByText('Исход ответа неизвестен.',{exact:true}).waitFor()
 h.reports[0]={...h.reports[0],result:answer('Первый ответ позже второго')}
 await page.reload();await page.getByText('Первый ответ позже второго',{exact:true}).waitFor()
 assert.equal(await page.getByText('Первый вопрос',{exact:true}).count(),1)
 assert.equal(await page.locator('[data-author="assistant"]').count(),2)
}))

test('unknown receipt uses durable command key to avoid duplicating an accepted question',()=>fixture(1440,async(page,h,open)=>{
 h.post=async route=>{const body=route.request().postDataJSON();h.reports=[{job_id:qid,kind:'question',analysis_id:'chat-card',question:body.question,result:null,state:'running',command_key:body.command_key}];return route.abort('failed')}
 await open();await page.getByLabel('Вопрос по тендеру',{exact:true}).fill('Вопрос с потерянной квитанцией');await page.getByRole('button',{name:'Спросить',exact:true}).click()
 await page.getByRole('region',{name:'Чат по тендеру',exact:true}).getByRole('button',{name:'Повторить сохранённый вопрос',exact:true}).waitFor()
 await page.reload();await page.getByText('Вопрос с потерянной квитанцией',{exact:true}).waitFor()
 assert.equal(await page.getByText('Вопрос с потерянной квитанцией',{exact:true}).count(),1)
 assert.equal(h.posts.length,1)
}))

test('single line composer submits Enter once and keeps its height for long text',()=>fixture(1440,async(page,h,open)=>{
 await open();const input=page.getByLabel('Вопрос по тендеру',{exact:true}),form=page.getByRole('region',{name:'Чат по тендеру',exact:true}).locator('form')
 assert.equal(await input.evaluate(el=>el.tagName),'INPUT')
 const before=await form.boundingBox();await input.fill('Вопрос '.repeat(150));assert.deepEqual(await form.boundingBox(),before)
 await input.press('Enter');await page.waitForFunction(()=>document.querySelector('[data-author="user"]'))
 await page.keyboard.press('Enter');assert.equal(h.posts.length,1)
 assert.equal(await page.locator('[data-author="user"]').count(),1)
}))

test('a newly accepted question is visible at the end of an existing long history',()=>fixture(1440,async(page,h,open)=>{
 h.reports=Array.from({length:15},(_,i)=>({job_id:`${String(i+1).padStart(8,'0')}-1111-4111-8111-111111111111`,kind:'question',analysis_id:'chat-card',question:'Прежний вопрос '+i,result:answer('Прежний ответ '+i)}))
 let accept
 const requested=new Promise(resolve=>{h.post=route=>new Promise(finish=>{accept=async()=>{await route.fulfill({status:202,json:{job_id:qid,run_id:rid,state:'queued'}});finish()};resolve()})})
 await open();await page.getByLabel('Вопрос по тендеру',{exact:true}).fill('Новый вопрос после истории');await page.getByRole('button',{name:'Спросить',exact:true}).click()
 const message=page.locator('[data-author="user"]').getByText('Новый вопрос после истории',{exact:true});await message.waitFor()
 await page.getByText('Отправка не подтверждена.',{exact:true}).waitFor()
 await requested;await accept()
 await page.waitForFunction(qid=>JSON.parse(localStorage.getItem('bervel.radar.profile.operations.v1')||'[]').some(o=>o.job_id===qid && o.question==='Новый вопрос после истории') && !localStorage.getItem('bervel.radar.profile.question.v1'),qid)
 await page.getByText('Отправка не подтверждена.',{exact:true}).waitFor({state:'hidden'})
 // The acknowledgement replaces the command-keyed pending node with the saved
 // job-keyed message. Measure the accepted message and its viewport atomically.
 const placement=await message.evaluate(node=>{const bounds=node.getBoundingClientRect(),history=node.closest('.radar-chat-history').getBoundingClientRect();return {connected:node.isConnected,top:bounds.top,bottom:bounds.bottom,historyTop:history.top,historyBottom:history.bottom}})
 assert.equal(placement.connected,true);assert.equal(await message.count(),1);assert.equal(h.posts.length,1)
 assert.ok(placement.top>=placement.historyTop && placement.bottom<=placement.historyBottom,'New question should be in the scroll viewport')
}))

for(const width of [1440,390,400])test('only message history scrolls with fixed heading and composer '+width,()=>fixture(width,async(page,h,open)=>{
 h.reports=Array.from({length:35},(_,i)=>({job_id:`${String(i+1).padStart(8,'0')}-1111-4111-8111-111111111111`,kind:'question',analysis_id:'chat-card',question:`Вопрос ${i+1}: ${'Условия поставки и состав заказа. '.repeat(3)}`,result:answer(`Ответ ${i+1}: ${'Данные сохранены в карточке тендера. '.repeat(5)}`)}))
 await open();const chat=page.getByRole('region',{name:'Чат по тендеру',exact:true}),history=chat.locator('.radar-chat-history'),composer=chat.locator('form'),heading=chat.getByRole('heading',{name:'Чат по тендеру',exact:true}),windowHeading=page.locator('.radar-tender-dialog>.dialog-heading')
 const before=await Promise.all([heading.boundingBox(),composer.boundingBox(),windowHeading.boundingBox()])
 const stats=await history.evaluate(el=>({height:el.clientHeight,scrollHeight:el.scrollHeight}));assert.ok(stats.height>=65,'History retains useful visible height');assert.ok(stats.scrollHeight>stats.height)
 await history.hover();await page.mouse.wheel(0,700)
 await page.waitForFunction(()=>document.querySelector('.radar-chat-history').scrollTop>0)
 assert.deepEqual(await Promise.all([heading.boundingBox(),composer.boundingBox(),windowHeading.boundingBox()]),before)
 assert.equal(await page.evaluate(()=>window.scrollY),0);assert.equal(await page.locator('.radar-tender-dialog').evaluate(el=>el.scrollTop),0);assert.equal(await page.locator('.radar-tender-dialog>.dialog-body').evaluate(el=>el.scrollTop),0)
 assert.equal(await history.locator('[data-author="user"]').count(),35);assert.equal(await history.locator('[data-author="assistant"]').count(),35)
 const bounds=await page.locator('.radar-tender-dialog').boundingBox();if(width===1440)assert.equal(bounds.x,100)
 await page.screenshot({path:path.join(task,'chat-scroll-'+width+'.png'),fullPage:false})
}))
