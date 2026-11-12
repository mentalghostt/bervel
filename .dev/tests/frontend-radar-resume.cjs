const {mkdirSync}=require('node:fs')
const path=require('node:path')
const artifacts=path.resolve(process.env.BERVEL_TEST_ARTIFACT_ROOT||'.dev/local/radar-retry-2026-11-09/frontend')
mkdirSync(path.join(artifacts,'browser-tmp'),{recursive:true})
process.env.TMP=path.join(artifacts,'browser-tmp');process.env.TEMP=process.env.TMP
const {test}=require('node:test')
const assert=require('node:assert/strict')
const {chromium}=require('playwright')
const root='bervel.radar.profile.refresh.v1'
const ids={test_api:'33333333-3333-4333-8333-333333333333',kontur_api:'44444444-4444-4444-8444-444444444444'}
const sid='11111111-1111-4111-8111-111111111111',jid='55555555-5555-4555-8555-555555555555'
const time='2026-11-09T09:00:00Z',schedule={enabled:false,every_days:1,time:'09:00',timezone:'Europe/Moscow'}
const profile=source=>({source,name:source==='test_api'?'Симуляция контур.закупки':'контур.закупки',run_id:ids[source],keyword_mode:'auto',keywords:[],schedule,available:true,last_refresh_at:time,next_refresh_at:null,quotas:{search:200,detail:200}})
const card={analysis_id:'saved-card',candidate_id:'candidate',state:'primary',last_seen_at:time,information_state:'primary',details_loaded:false,priority_score:75,tender:{title:'Сохранённая карточка',number:'N1',link:null},analysis:null,fulfillment:null,assessment:null,review:null}
const run=(source,state='failed',completed=2172)=>({run_id:ids[source],settings_snapshot_id:sid,created_at:time,data:{state,phase:state==='running'?'ranking':state,detail_attempts:0,checkpoint:{search_attempts:56,ranking_completed:completed,ranking_total:2697},...(state==='failed'?{error:{code:'MODEL_FAILED',message:'Ошибка модели'}}:{})},cards:[card],reports:[]})
const receipt=source=>({run_id:ids[source],job_id:jid,state:'queued'})
const url=source=>(process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1:5182')+'/#/ai-tender-radar?profile='+source

async function fixture(action,{source='test_api',width=1440,state='failed',storageFailure=false,initial}={}) {
 const browser=await chromium.launch({headless:true,executablePath:process.env.BERVEL_BROWSER_PATH})
 try {
  const page=await browser.newPage({viewport:{width,height:width===390?844:1000}});page.setDefaultTimeout(7000)
  const errors=[],commands=[],reads=[]
  page.on('pageerror',e=>errors.push(e.message))
  await page.addInitScript(({storageFailure,initial,root})=>{
   if(initial&&!sessionStorage.getItem('resume-test-seeded')){localStorage.setItem(root,typeof initial==='string'?initial:JSON.stringify(initial));sessionStorage.setItem('resume-test-seeded','1')}
   if(storageFailure){const original=Storage.prototype.setItem;Storage.prototype.setItem=function(key,value){if(key===root)throw Error('Хранилище команды недоступно');return original.call(this,key,value)}}
  },{storageFailure,initial,root})
  const hooks={resume:null,refresh:null,state,completed:2172}
  await page.route('**/api/**',async route=>{
   const request=route.request(),p=new URL(request.url()).pathname
   if(p.endsWith('/profiles'))return route.fulfill({json:{profiles:Object.keys(ids).map(profile)}})
   if(/\/profiles\/(test_api|kontur_api)$/.test(p)){const s=p.split('/').pop();reads.push(s);return route.fulfill({json:{profile:profile(s),run:run(s,hooks.state,hooks.completed)}})}
   if(/\/profiles\/(test_api|kontur_api)\/(resume|refresh)$/.test(p)){
    const s=p.split('/').at(-2),kind=p.split('/').pop(),body=request.postDataJSON()
    commands.push({source:s,kind,body,saved:await page.evaluate(key=>JSON.parse(localStorage.getItem(key)),root)})
    return hooks[kind]?hooks[kind](route,s):route.fulfill({json:receipt(s)})
   }
   return route.fulfill({status:404,json:{error:{message:'Unexpected fixture route '+p}}})
  })
  await page.goto(url(source));await page.getByRole('button',{name:'Сохранённая карточка',exact:true}).waitFor()
  await action({page,hooks,commands,reads})
  assert.deepEqual(errors,[])
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 } finally {await browser.close()}
}

for(const [source,width] of [['test_api',1440],['kontur_api',390]]) test('Продолжение failed сохраняет команду, источник и прогресс, блокирует двойной запуск '+source+' '+width,()=>fixture(async({page,hooks,commands,reads})=>{
 const resume=page.getByRole('button',{name:'Возобновить',exact:true})
 assert.equal(await resume.count(),1,'Failed профиль должен предлагать продолжение прежнего анализа')
 await page.screenshot({path:path.join(artifacts,'resume-failed-'+width+'.png'),fullPage:false})
 let release;const held=new Promise(resolve=>{release=resolve})
 hooks.resume=async(route,s)=>{await held;hooks.state='running';hooks.completed=2173;return route.fulfill({json:receipt(s)})}
 await resume.evaluate(button=>{button.click();button.click()})
 await page.waitForFunction(key=>!!localStorage.getItem(key),root)
 assert.equal(await resume.isDisabled(),true);assert.equal(await page.getByRole('button',{name:'Обновить',exact:true}).isDisabled(),true)
 for(let attempts=0;!commands.length&&attempts<100;attempts++)await new Promise(resolve=>setTimeout(resolve,10))
 assert.equal(commands.length,1);assert.equal(commands[0].source,source);assert.equal(commands[0].kind,'resume')
 assert.deepEqual(commands[0].body,{command_key:commands[0].saved.body.command_key})
 assert.equal(commands[0].saved.kind,'resume');assert.equal(commands[0].saved.source,source)
 assert.match(commands[0].body.command_key,/^[a-f0-9-]{36}$/)
 await page.screenshot({path:path.join(artifacts,'resume-pending-'+width+'.png'),fullPage:false})
 release();await page.getByRole('button',{name:'Стоп',exact:true}).waitFor()
 await page.getByText('Оценка первичной информации: 2 173 из 2 697',{exact:true}).waitFor()
 assert.equal(await page.evaluate(key=>localStorage.getItem(key),root),null)
 assert.equal(await page.getByRole('dialog').count(),0)
 assert.equal(await page.getByRole('button',{name:'Сохранённая карточка',exact:true}).count(),1)
 const previous=reads.length;hooks.completed=2174
 await page.getByText('Оценка первичной информации: 2 174 из 2 697',{exact:true}).waitFor()
 assert.ok(reads.length>previous);assert.equal(await page.getByRole('button',{name:'Возобновить',exact:true}).count(),0)
 await page.screenshot({path:path.join(artifacts,'resume-running-'+width+'.png'),fullPage:false})
},{source,width}))

test('Неизвестная квитанция после reload повторяет resume с прежним ключом и источником',()=>fixture(async({page,hooks,commands})=>{
 let lost=true;hooks.resume=(route,s)=>{if(lost){lost=false;return route.abort('failed')}hooks.state='running';return route.fulfill({json:receipt(s)})}
 await page.getByRole('button',{name:'Возобновить',exact:true}).click()
 await page.getByRole('button',{name:'Повторить сохранённое возобновление',exact:true}).waitFor()
 assert.equal(await page.getByRole('button',{name:'Обновить',exact:true}).isDisabled(),true)
 await page.goto(url('kontur_api'));await page.reload()
 await page.getByRole('button',{name:'Повторить сохранённое возобновление',exact:true}).click()
 await page.waitForFunction(key=>localStorage.getItem(key)===null,root)
 assert.equal(commands.length,2);assert.equal(commands[0].source,'test_api');assert.equal(commands[1].source,'test_api')
 assert.equal(commands[1].kind,'resume');assert.deepEqual(commands[0].body,commands[1].body)
 assert.equal(await page.getByRole('button',{name:'Повторить сохранённое обновление',exact:true}).count(),0)
}))

test('Определённый отказ resume освобождает сохранённую команду и показывает ошибку',()=>fixture(async({page,hooks,commands})=>{
 hooks.resume=route=>route.fulfill({status:409,json:{error:{code:'PROFILE_REFRESH_ACTIVE',message:'Обновление уже выполняется'}}})
 await page.getByRole('button',{name:'Возобновить',exact:true}).click()
 await page.getByRole('alert').filter({hasText:'Обновление уже выполняется'}).waitFor()
 assert.equal(commands.length,1);assert.equal(await page.evaluate(key=>localStorage.getItem(key),root),null)
 assert.equal(await page.getByRole('button',{name:'Обновить',exact:true}).isDisabled(),false)
 assert.equal(await page.getByRole('button',{name:'Повторить сохранённое возобновление',exact:true}).count(),0)
}))

test('Сбой записи команды запрещает POST возобновления',()=>fixture(async({page,commands})=>{
 await page.getByRole('button',{name:'Возобновить',exact:true}).click()
 await page.getByRole('alert').filter({hasText:'Хранилище команды недоступно'}).waitFor()
 assert.equal(commands.length,0)
},{storageFailure:true}))

test('Некорректная квитанция остаётся неизвестной и восстанавливается тем же resume',()=>fixture(async({page,hooks,commands})=>{
 let malformed=true;hooks.resume=(route,s)=>{if(malformed){malformed=false;return route.fulfill({json:{job_id:jid,run_id:'unexpected',state:'queued'}})}hooks.state='running';return route.fulfill({json:receipt(s)})}
 await page.getByRole('button',{name:'Возобновить',exact:true}).click()
 await page.getByRole('button',{name:'Повторить сохранённое возобновление',exact:true}).click()
 await page.waitForFunction(key=>localStorage.getItem(key)===null,root)
 assert.equal(commands.length,2);assert.deepEqual(commands[0].body,commands[1].body)
 assert.equal(commands[0].kind,'resume');assert.equal(commands[1].kind,'resume')
}))

test('Старое обновление восстанавливается, failed refresh и автообновление имеют отдельные попапы',()=>fixture(async({page,hooks,commands})=>{
 hooks.refresh=(route,s)=>route.fulfill({json:receipt(s)})
 await page.getByRole('button',{name:'Повторить сохранённое обновление',exact:true}).click()
 await page.waitForFunction(key=>localStorage.getItem(key)===null,root)
 assert.equal(commands[0].kind,'refresh');assert.deepEqual(commands[0].body,{command_key:'old-refresh',detail_max:0,schedule})
 await page.getByRole('button',{name:'Обновить',exact:true}).click()
 const dialog=page.getByRole('dialog');await dialog.getByLabel('Подробных карточек').fill('0')
  assert.equal(await dialog.getByLabel('Автообновление',{exact:true}).count(),0)
  assert.equal(await dialog.getByLabel('Каждые, дней').count(),0)
  assert.equal(await dialog.getByLabel('Проанализировать на основе измененных показателей',{exact:true}).isChecked(),false)
 await dialog.getByRole('button',{name:'Обновить базу',exact:true}).click()
 await page.waitForFunction(key=>localStorage.getItem(key)===null,root)
 assert.equal(commands.length,2);assert.equal(commands[1].kind,'refresh');assert.equal(commands[1].body.detail_max,0)
  assert.equal(Object.hasOwn(commands[1].body,'schedule'),false)
  assert.equal(commands[1].body.reanalyze_existing,false)
  await page.getByRole('button',{name:'Автообновление',exact:true}).click()
  const autoDialog=page.getByRole('dialog')
  assert.equal(await autoDialog.getByLabel('Подробных карточек').count(),0)
  assert.equal(await autoDialog.getByLabel('Автообновление',{exact:true}).isChecked(),false)
  assert.equal(await autoDialog.getByLabel('Каждые, дней').inputValue(),'1')
  assert.equal(await autoDialog.getByLabel('Время, МСК').inputValue(),'09:00')
  assert.equal(await autoDialog.getByLabel('Проанализировать на основе измененных показателей',{exact:true}).isChecked(),false)
  await autoDialog.getByLabel('Автообновление',{exact:true}).check()
  await autoDialog.getByLabel('Каждые, дней').fill('3')
  assert.equal(await autoDialog.getByRole('button',{name:'Сохранить',exact:true}).isEnabled(),true)
  assert.equal(commands.length,2,'Изменение настроек в отдельном попапе не запускает refresh')
},{initial:{source:'test_api',body:{command_key:'old-refresh',detail_max:0,schedule}}}))

test('Активный и завершённый профиль не предлагает resume, повреждённая команда блокирует запуск',async()=>{
 for(const state of ['running','queued','complete'])await fixture(async({page,commands})=>{
  assert.equal(await page.getByRole('button',{name:'Возобновить',exact:true}).count(),0)
  assert.equal(await page.getByRole('button',{name:'Обновить',exact:true}).isDisabled(),state!=='complete');assert.equal(commands.length,0)
 },{state})
 await fixture(async({page,commands})=>{
  assert.equal(await page.getByRole('button',{name:'Возобновить',exact:true}).isDisabled(),true)
  assert.equal(await page.getByRole('button',{name:'Обновить',exact:true}).isDisabled(),true);assert.equal(commands.length,0)
 },{initial:'{broken'})
})

test('Контракт команд профиля различает resume и старый refresh, проверяет квитанции',async()=>{
 const api=await import('../../frontend/src/radarProfilesApi.ts')
 const resume={kind:'resume',source:'kontur_api',body:{command_key:'recover-1'}}
 const old={source:'test_api',body:{command_key:'refresh-1',detail_max:0,schedule}}
 const invalid=[{...resume,source:'file'},{...resume,kind:'other'},{...resume,body:{command_key:''}},{...resume,body:{command_key:'x'.repeat(129)}},{...resume,body:{command_key:'x',detail_max:0,schedule}}]
 assert.deepEqual(api.readProfileCommand(resume),resume)
 assert.equal(api.readProfileCommand(old).body.detail_max,0)
 for(const command of invalid)assert.throws(()=>api.readProfileCommand(command))
 const originalFetch=global.fetch
 try{
  global.fetch=async(url,options)=>{
   assert.equal(url,'/api/v0.1/ai-tender-radar/profiles/kontur_api/resume')
   assert.equal(options.method,'POST')
   assert.deepEqual(JSON.parse(options.body),resume.body)
   return new Response(JSON.stringify(receipt('kontur_api')))
  }
  assert.deepEqual(await api.submitProfileCommand(resume),receipt('kontur_api'))
 }finally{global.fetch=originalFetch}
})
