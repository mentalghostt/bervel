const {mkdirSync}=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{test}=require('node:test')
const artifacts=path.resolve('.dev/local/radar-reanalysis-option-2026-11-10')
mkdirSync(path.join(artifacts,'browser-tmp'),{recursive:true});process.env.TMP=path.join(artifacts,'browser-tmp');process.env.TEMP=process.env.TMP
const {chromium}=require('playwright')
const label='Проанализировать на основе измененных показателей',pendingKey='bervel.radar.profile.refresh.v1',autoKey='bervel.radar.profile.schedule.v1'
const ids={test_api:'33333333-3333-4333-8333-333333333333',kontur_api:'44444444-4444-4444-8444-444444444444'},sid='11111111-1111-4111-8111-111111111111',jid='55555555-5555-4555-8555-555555555555'
const stamp='2026-11-10T09:00:00Z',schedule={enabled:false,every_days:1,time:'09:00',timezone:'Europe/Moscow'}
const profile=source=>({source,name:source==='test_api'?'Симуляция контур.закупки':'контур.закупки',run_id:ids[source],keyword_mode:'auto',keywords:[],schedule,available:true,last_refresh_at:stamp,next_refresh_at:null,quotas:{search:200,detail:200}})
const run=source=>({run_id:ids[source],settings_snapshot_id:sid,created_at:stamp,data:{state:'complete',phase:'complete'},cards:[{analysis_id:'saved-card',candidate_id:'candidate',state:'accepted',details_loaded:true,information_state:'full',evaluated_at:stamp,tender:{title:'Сохранённый тендер',number:'N1',link:null},analysis:{coverage_percent:'40',benefit_percent:'25',economics:{scope:'confirmed',revenue:'100',cost:'75'},positions:[]},fulfillment:null,assessment:null,review:null}],reports:[]})
const retryReady=(page,name)=>page.waitForFunction(name=>[...document.querySelectorAll('button')].some(button=>button.textContent===name && !button.disabled),name)
async function fixture(action,{width=1440,initial,autoInitial,active=false}={}){
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'})
 try{
  const page=await browser.newPage({viewport:{width,height:width<650?844:1000}});page.setDefaultTimeout(5000)
  const h={profiles:{test_api:profile('test_api'),kontur_api:profile('kontur_api')},runs:{test_api:run('test_api'),kontur_api:run('kontur_api')},posts:[],reads:[],refresh:null,schedule:null,read:null},errors=[];page.on('pageerror',e=>errors.push(e.message))
  if(active)h.runs.test_api.data={state:'running',phase:'analysis',checkpoint:{analysis_completed:10,analysis_total:400}}
  if(initial||autoInitial)await page.addInitScript(({key,command,autoKey,autoCommand})=>{if(!sessionStorage.getItem('reanalysis-seeded')){if(command)localStorage.setItem(key,JSON.stringify(command));if(autoCommand)localStorage.setItem(autoKey,JSON.stringify(autoCommand));sessionStorage.setItem('reanalysis-seeded','1')}},{key:pendingKey,command:initial,autoKey,autoCommand:autoInitial})
  await page.route('**/api/v0.1/**',async route=>{const req=route.request(),p=new URL(req.url()).pathname
   if(p.endsWith('/profiles'))return route.fulfill({json:{profiles:Object.values(h.profiles)}})
   if(/\/profiles\/(test_api|kontur_api)$/.test(p)){const source=p.split('/').pop();h.reads.push(source);return h.read?h.read(route,source):route.fulfill({json:{profile:h.profiles[source],run:h.runs[source]}})}
   if(/\/profiles\/(test_api|kontur_api)\/(refresh|metrics|resume|schedule)$/.test(p)){const source=p.split('/').at(-2),kind=p.split('/').pop(),body=req.postDataJSON();h.posts.push({source,kind,body,saved:await page.evaluate(key=>JSON.parse(localStorage.getItem(key)),pendingKey),auto_saved:await page.evaluate(key=>JSON.parse(localStorage.getItem(key)),autoKey)})
    if(kind==='schedule' && h.schedule)return h.schedule(route,source,body)
    if(kind==='schedule'){h.profiles[source]={...h.profiles[source],schedule:body.schedule,auto_reanalyze_existing:body.reanalyze_existing};return route.fulfill({json:h.profiles[source]})}
    if(kind==='refresh' && h.refresh)return h.refresh(route,source,body)
    if(kind==='refresh' && Object.hasOwn(body,'reanalyze_existing'))h.profiles[source].reanalyze_existing=body.reanalyze_existing
    return route.fulfill({status:202,json:{job_id:jid,run_id:ids[source],state:'queued'}})
   }
   return route.fulfill({status:404,json:{error:{message:'Unexpected mocked route '+p}}})
  })
  const open=async(source='test_api')=>{await page.goto((process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1:5191')+'/#/ai-tender-radar?profile='+source);await page.getByRole('button',{name:'Сохранённый тендер',exact:true}).waitFor()}
  const popup=async()=>{await page.getByRole('button',{name:'Обновить',exact:true}).click();await page.getByRole('checkbox',{name:label,exact:true}).waitFor();return page.getByRole('dialog')}
  const autoPopup=async()=>{await page.getByRole('button',{name:'Автообновление',exact:true}).click();await page.getByRole('checkbox',{name:label,exact:true}).waitFor();return page.getByRole('dialog')}
  const settled=async()=>page.waitForFunction(key=>!localStorage.getItem(key) && ![...document.querySelectorAll('button')].find(b=>b.textContent==='Обновить')?.disabled,pendingKey)
  const autoSettled=async()=>page.waitForFunction(key=>!localStorage.getItem(key) && ![...document.querySelectorAll('button')].find(b=>b.textContent==='Автообновление')?.disabled,autoKey)
  await action({page,h,open,popup,autoPopup,settled,autoSettled});assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 }finally{await browser.close()}
}

for(const width of [1440,390])test('old profile defaults to unchecked and reopening discards an unsaved toggle '+width,()=>fixture(async({page,open,popup})=>{
 await open();let dialog=await popup();const checkbox=page.getByRole('checkbox',{name:label,exact:true});assert.equal(await checkbox.isChecked(),false);await checkbox.check();await dialog.getByRole('button',{name:'Закрыть диалог'}).click();dialog=await popup();assert.equal(await checkbox.isChecked(),false)
 assert.equal(await dialog.getByRole('checkbox',{name:'Автообновление',exact:true}).count(),0);assert.equal(await dialog.getByLabel('Каждые, дней').count(),0);assert.equal(await dialog.getByLabel('Время, МСК').count(),0)
 assert.equal(await dialog.getByText(/Доступно подробных карточек/).count(),0);assert.equal(await dialog.getByText(/Авто расходует доступные квоты/).count(),0);await dialog.evaluate(async node=>{await Promise.all(node.getAnimations({subtree:true}).map(animation=>animation.finished.catch(()=>{})))});await page.screenshot({path:path.join(artifacts,'popup-'+width+'.png'),fullPage:false})
},{width}))

for(const selected of [false,true])test('new refresh durably sends explicit '+selected+' and reload uses the saved server preference',()=>fixture(async({page,h,open,popup,settled})=>{
 h.profiles.test_api.reanalyze_existing=!selected;await open();const dialog=await popup();const checkbox=page.getByRole('checkbox',{name:label,exact:true});assert.equal(await checkbox.isChecked(),!selected);await checkbox.setChecked(selected)
 await dialog.getByLabel('Подробных карточек',{exact:true}).fill('0');await dialog.getByRole('button',{name:'Обновить базу',exact:true}).click();await settled()
 assert.equal(h.posts.length,1);assert.equal(h.posts[0].body.reanalyze_existing,selected);assert.equal(typeof h.posts[0].body.reanalyze_existing,'boolean');assert.deepEqual(h.posts[0].saved.body,h.posts[0].body);assert.equal(h.posts[0].body.detail_max,0);assert.equal(Object.hasOwn(h.posts[0].body,'schedule'),false)
 await page.reload();await popup();assert.equal(await page.getByRole('checkbox',{name:label,exact:true}).isChecked(),selected)
}))

test('two profiles restore their own saved preference',()=>fixture(async({page,h,open,popup})=>{
 h.profiles.test_api.reanalyze_existing=true;h.profiles.kontur_api.reanalyze_existing=false;await open();let dialog=await popup();assert.equal(await page.getByRole('checkbox',{name:label,exact:true}).isChecked(),true);await dialog.getByRole('button',{name:'Закрыть диалог'}).click()
 await page.getByRole('button',{name:'Профили',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'контур.закупки',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Закрыть диалог'}).click();dialog=await popup();assert.equal(await page.getByRole('checkbox',{name:label,exact:true}).isChecked(),false);await dialog.getByRole('button',{name:'Закрыть диалог'}).click()
 await page.reload();await popup();assert.equal(await page.getByRole('checkbox',{name:label,exact:true}).isChecked(),false)
}))

test('unknown refresh replays the exact selected flag after reload despite changed profile preference',()=>fixture(async({page,h,open,popup,settled})=>{
 h.refresh=route=>route.abort('failed');await open();const dialog=await popup();await page.getByRole('checkbox',{name:label,exact:true}).check();await dialog.getByRole('button',{name:'Обновить базу',exact:true}).click();await retryReady(page,'Повторить сохранённое обновление')
 const first=h.posts[0];assert.equal(first.body.reanalyze_existing,true);assert.equal(await page.getByRole('button',{name:'Обновить',exact:true}).isDisabled(),true);h.profiles.test_api.reanalyze_existing=false;await page.reload();await page.getByRole('button',{name:'Повторить сохранённое обновление',exact:true}).waitFor()
 h.refresh=null;await page.getByRole('button',{name:'Повторить сохранённое обновление',exact:true}).click();await settled();assert.deepEqual(h.posts[1].body,first.body);assert.deepEqual(h.posts[1].saved.body,first.body);assert.equal(h.posts[1].source,'test_api')
}))

test('legacy saved refresh without the field preserves exact omission even when profile preference is true',()=>fixture(async({page,h,open,settled})=>{
 h.profiles.test_api.reanalyze_existing=true;await open();await page.getByRole('button',{name:'Повторить сохранённое обновление',exact:true}).click();await settled();assert.equal(h.posts.length,1);assert.deepEqual(h.posts[0].body,{command_key:'legacy-command',detail_max:0,schedule});assert.equal(Object.hasOwn(h.posts[0].body,'reanalyze_existing'),false)
},{initial:{source:'test_api',body:{command_key:'legacy-command',detail_max:0,schedule}}}))

for(const invalid of ['false',1,null])test('a saved command rejects nonboolean flag '+JSON.stringify(invalid),()=>fixture(async({page,h,open})=>{
 await open();await page.getByText(/Не удалось прочитать сохранённые команды/).waitFor();assert.equal(await page.getByRole('button',{name:'Обновить',exact:true}).isDisabled(),true);assert.equal(h.posts.length,0)
},{initial:{source:'test_api',body:{command_key:'invalid-command',detail_max:0,schedule,reanalyze_existing:invalid}}}))

test('profile preference must be a boolean when present',()=>fixture(async({page,h,open})=>{
 h.profiles.test_api.reanalyze_existing='false';await page.goto((process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1:5191')+'/#/ai-tender-radar?profile=test_api');await page.getByText(/Сервер вернул неожиданные данные профиля/).first().waitFor();assert.equal(h.posts.length,0)
}))

test('metrics-only command does not acquire the refresh flag',()=>fixture(async({page,h,open,settled})=>{
 h.profiles.test_api.reanalyze_existing=true;await open();await page.getByRole('button',{name:'Обновить показатели',exact:true}).click();await settled();assert.equal(h.posts.length,1);assert.equal(h.posts[0].kind,'metrics');assert.deepEqual(Object.keys(h.posts[0].body),['command_key'])
}))

test('auto settings save during an active full job without starting a refresh or changing its progress',()=>fixture(async({page,h,open,autoPopup,autoSettled})=>{
 h.profiles.test_api.reanalyze_existing=true;h.profiles.test_api.auto_reanalyze_existing=true;await open();assert.equal(await page.getByRole('button',{name:'Обновить',exact:true}).isDisabled(),true)
 const dialog=await autoPopup();assert.equal(await dialog.getByLabel('Подробных карточек',{exact:true}).count(),0);await dialog.getByRole('checkbox',{name:label,exact:true}).uncheck();await dialog.getByRole('checkbox',{name:'Автообновление',exact:true}).check();await dialog.getByLabel('Каждые, дней').fill('3');await dialog.getByLabel('Время, МСК').fill('10:30');await dialog.getByRole('button',{name:'Сохранить',exact:true}).click();await autoSettled()
 assert.equal(h.posts.length,1);assert.equal(h.posts[0].kind,'schedule');assert.deepEqual(h.posts[0].body.schedule,{enabled:true,every_days:3,time:'10:30',timezone:'Europe/Moscow'});assert.equal(h.posts[0].body.reanalyze_existing,false);assert.equal(Object.hasOwn(h.posts[0].body,'detail_max'),false);assert.deepEqual(h.posts[0].auto_saved.body,h.posts[0].body)
 await page.getByText('Анализирую детали тендеров: 10 из 400',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Обновить',exact:true}).isDisabled(),true)
 await autoPopup();assert.equal(await page.getByRole('checkbox',{name:label,exact:true}).isChecked(),false)
},{active:true}))

test('unknown autosave has its own replay and does not block manual or metrics commands',()=>fixture(async({page,h,open,autoPopup,popup,settled,autoSettled})=>{
 h.schedule=route=>route.abort('failed');await open();let dialog=await autoPopup();await dialog.getByRole('checkbox',{name:label,exact:true}).check();await dialog.getByRole('button',{name:'Сохранить',exact:true}).click();await retryReady(page,'Повторить сохранение автообновления');const first=h.posts[0]
 assert.equal(await page.getByRole('button',{name:'Обновить',exact:true}).isEnabled(),true);assert.equal(await page.getByRole('button',{name:'Обновить показатели',exact:true}).isEnabled(),true)
 dialog=await popup();await dialog.getByRole('checkbox',{name:label,exact:true}).uncheck();await dialog.getByRole('button',{name:'Обновить базу',exact:true}).click();await settled();assert.equal(h.posts[1].kind,'refresh');assert.equal(Object.hasOwn(h.posts[1].body,'schedule'),false);assert.equal(await page.evaluate(key=>!!localStorage.getItem(key),autoKey),true)
 await page.reload();await page.getByRole('button',{name:'Повторить сохранение автообновления',exact:true}).waitFor();await page.getByRole('button',{name:'Профили',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'контур.закупки',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Закрыть диалог'}).click();h.schedule=null;await page.getByRole('button',{name:'Повторить сохранение автообновления',exact:true}).click();await autoSettled()
 assert.deepEqual(h.posts[2].body,first.body);assert.equal(h.posts[2].source,'test_api');assert.match(page.url(),/profile=kontur_api/)
}))

test('unknown manual command does not block saving independent auto settings',()=>fixture(async({page,h,open,popup,autoPopup,autoSettled})=>{
 h.refresh=route=>route.abort('failed');await open();let dialog=await popup();await dialog.getByRole('button',{name:'Обновить базу',exact:true}).click();await retryReady(page,'Повторить сохранённое обновление')
 dialog=await autoPopup();await dialog.getByRole('checkbox',{name:label,exact:true}).check();await dialog.getByRole('button',{name:'Сохранить',exact:true}).click();await autoSettled();assert.equal(h.posts[1].kind,'schedule');assert.equal(await page.evaluate(key=>!!localStorage.getItem(key),pendingKey),true)
}))

test('manual and auto preferences are different and independent for each profile',()=>fixture(async({page,h,open,popup,autoPopup})=>{
 h.profiles.test_api.reanalyze_existing=true;h.profiles.test_api.auto_reanalyze_existing=false;h.profiles.kontur_api.reanalyze_existing=false;h.profiles.kontur_api.auto_reanalyze_existing=true
 await open();let dialog=await popup();assert.equal(await dialog.getByRole('checkbox',{name:label,exact:true}).isChecked(),true);await dialog.getByRole('button',{name:'Закрыть диалог'}).click();dialog=await autoPopup();assert.equal(await dialog.getByRole('checkbox',{name:label,exact:true}).isChecked(),false);await dialog.getByRole('button',{name:'Закрыть диалог'}).click()
 await page.getByRole('button',{name:'Профили',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'контур.закупки',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Закрыть диалог'}).click();dialog=await autoPopup();assert.equal(await dialog.getByRole('checkbox',{name:label,exact:true}).isChecked(),true);await dialog.getByRole('button',{name:'Закрыть диалог'}).click();dialog=await popup();assert.equal(await dialog.getByRole('checkbox',{name:label,exact:true}).isChecked(),false)
}))

test('an old autosave receipt cannot overwrite the manual preference while its fresh GET is pending',{timeout:15000},()=>fixture(async({page,h,open,autoPopup,popup,autoSettled})=>{
 h.profiles.test_api.reanalyze_existing=true;h.profiles.test_api.auto_reanalyze_existing=false;let release
 h.schedule=async(route,source,body)=>{h.profiles[source]={...h.profiles[source],schedule:body.schedule,auto_reanalyze_existing:body.reanalyze_existing};h.read=(readRoute,readSource)=>new Promise(resolve=>{release=async()=>{await readRoute.fulfill({json:{profile:h.profiles[readSource],run:h.runs[readSource]}});resolve()}});return route.fulfill({json:{...h.profiles[source],reanalyze_existing:false}})}
 await open();let dialog=await autoPopup();await dialog.getByRole('checkbox',{name:label,exact:true}).check();await dialog.getByRole('button',{name:'Сохранить',exact:true}).click();await autoSettled();dialog=await popup();assert.equal(await dialog.getByRole('checkbox',{name:label,exact:true}).isChecked(),true)
 h.read=null;if(release)await release()
}))
