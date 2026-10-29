const {test,before,after}=require('node:test')
const assert=require('node:assert/strict')
const {chromium}=require('playwright')
let browser
const base=process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1:5173'
const stamp='2026-10-27T12:00:00Z'
const ids=['11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222','33333333-3333-4333-8333-333333333333']
const files=['procurement','stock','economics'].map((usage_role,i)=>({artifact_id:ids[i],filename:['Закупки.xlsx','Склад.csv','Экономика.csv'][i],media_type:i?'text/csv':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',size:100,created_at:stamp,usage_role,processing_state:'ready',completed_chunks:2,total_chunks:2,processing_error:null}))
before(async()=>{browser=await chromium.launch({headless:true})})
after(async()=>{await browser.close()})
async function open(width=1440){
 const page=await browser.newPage({viewport:{width,height:1000}});page.setDefaultTimeout(3000)
 const requests=[];page.on('request',r=>{if(r.url().includes('/api/'))requests.push(r)})
 await page.route('**/api/v0.1/**',async route=>{
  const r=route.request(),p=new URL(r.url()).pathname
  if(p==='/api/v0.1/chat/sessions'&&r.method()==='GET')return route.fulfill({json:{sessions:[],next_offset:null}})
  if(p.endsWith('/artifacts'))return route.fulfill({json:{artifacts:files,next_cursor:null}})
  if(p.endsWith('/knowledge'))return route.fulfill({json:{state:'ready',error:null,total_chunks:2,completed_chunks:2}})
  if(p.endsWith('/preview'))return route.fulfill({json:{kind:'text',media_type:'text/plain',columns:[],rows:[],total_rows:0,next_cursor:null,text:'Данные файла',notice:''}})
  if(p.endsWith('/voice/settings'))return route.fulfill({json:{configured:r.method()==='PUT',state:r.method()==='PUT'?'configured':'unconfigured',model:'grok-voice-transcribe-2.0'}})
  if(p.endsWith('/settings/model'))return route.fulfill({json:{provider:'mox',configured:true,api:{base_url:'https://api.openai.com/v1',model:'test',api_key_configured:false},mox:{base_url:'https://example.com',model:'test',credentials_available:true}}})
  if(p.endsWith('/runs')||p.endsWith('/batches'))return route.fulfill({json:[]})
  if(p.endsWith('/tickets'))return route.fulfill({json:{tickets:[],next_cursor:null}})
  throw Error('Unexpected route '+r.method()+' '+p)
 })
 return {page,requests}
}

test('files show AI readiness at names and a modal upload control',async()=>{
 const {page}=await open()
 try{await page.goto(base+'/#/files');await page.getByRole('button',{name:'Загрузить файл',exact:true}).waitFor();assert.ok(await page.getByText('Готов к ИИ анализу',{exact:true}).count()>=3);assert.equal(await page.getByRole('button',{name:files[1].filename,exact:true}).isVisible(),true)}finally{await page.close()}
})

test('radar has exactly three single file selectors and no upload inputs',async()=>{
 const {page}=await open()
 try{
  await page.goto(base+'/#/radar')
  await page.getByRole('combobox',{name:'Выгрузка тендеров',exact:true}).waitFor()
  await page.getByRole('combobox',{name:'Данные по складу',exact:true}).waitFor()
  await page.getByRole('combobox',{name:'Экономика и производство',exact:true}).waitFor()
  assert.equal(await page.locator('input[type=file]').count(),0)
  assert.equal(await page.locator('select[multiple]').count(),0)
  await page.getByLabel('Минимальная маржа, %',{exact:true}).waitFor()
  await page.getByLabel('Запас до закрытия заявок, дней',{exact:true}).waitFor()
  await page.getByLabel('Цена от, ₽',{exact:true}).waitFor()
  await page.getByLabel('Цена до, ₽',{exact:true}).waitFor()
  assert.equal(await page.getByRole('button',{name:'Подобрать тендеры',exact:true}).isEnabled(),true)
 }finally{await page.close()}
})

test('new navigation opens universal assistant and scoped tickets',async()=>{
 const {page}=await open(390)
 try{
  await page.goto(base)
  await page.getByRole('link',{name:'Универсальный ассистент',exact:true}).click()
  await page.getByRole('textbox',{name:'Сообщение',exact:true}).waitFor()
  await page.getByRole('button',{name:'Записать голосовое сообщение',exact:true}).waitFor()
  await page.getByRole('link',{name:'Обращения',exact:true}).click()
  await page.getByRole('heading',{name:'Обращения',exact:true}).waitFor()
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 }finally{await page.close()}
})

test('voice key is write-only and cleared after saving',async()=>{
 const {page,requests}=await open()
 try{
  await page.goto(base+'/#/settings')
  const input=page.getByLabel('API-ключ xAI',{exact:true})
  await input.fill('fixture-voice-key-not-a-real-secret')
  assert.equal(await input.getAttribute('type'),'password')
  await page.getByRole('button',{name:'Сохранить ключ xAI',exact:true}).click()
  await page.getByText('Голос подключён',{exact:true}).waitFor()
  assert.equal(await input.inputValue(),'')
  assert.equal(await page.evaluate(()=>JSON.stringify(localStorage)+JSON.stringify(sessionStorage)).then(s=>s.includes('fixture-voice-key')),false)
  const sent=requests.find(r=>r.method()==='PUT'&&r.url().endsWith('/voice/settings'))
  assert.deepEqual(sent.postDataJSON(),{api_key:'fixture-voice-key-not-a-real-secret'})
 }finally{await page.close()}
})

const reportRun=(extra={})=>({run_id:ids[0],batch_id:ids[0],created_at:stamp,state:'completed',focus:'',sources_snapshot:files,model:'fixture',error:null,report:{schema_version:'1.3.0',as_of:stamp,batch:{filename:'Закупки.xlsx'},notice:'Сохранённый анализ',sources:[],counts:{total:3,model_analysed:2,take_in_work:1,needs_review:1,excluded:0,not_analysed:1},analysis_complete:true,coverage:{input_rows:4,unique_tenders:3,duplicate_rows:1,skipped_rows:0,accounted_rows:4,model_failed_rows:0,complete:true},portion_errors:[],economics_kind:'mock',items:[['take_in_work','Поставка болтов'],['needs_review','Поставка гаек'],['not_analysed','Поставка шайб']].map(([decision,title],i)=>({id:'t'+i,number:'00'+i,title,decision,reason:'Проверено по исходным данным',price:'1000',currency:'RUB',deadline_at:stamp,source_observed_at:stamp,source_url:'https://example.org/'+i,risks:[],next_steps:[],evidence_indexes:[],origins:[{sheet:'Лист1',row:i+2}],mock:true,economics_check:{status:'pass',currency:'RUB',revenue:'900.00',total_cost:'600.00',profit:'300.00',margin_percent:'33.33',budget:'1000',required_days:'4',available_days:'10',reasons:[],lines:[{sku:'P1',quantity:'10',sale_price:'90',revenue:'900',cost:'600',source_row:2}]}}))},...extra})
const sessionId='44444444-4444-4444-8444-444444444444'
async function assistant(page,results=[],actions=[]){
 await page.addInitScript(id=>localStorage.setItem('bervel.chat.session_id',id),sessionId)
 await page.route('**/chat/sessions/'+sessionId,route=>route.fulfill({json:{session_id:sessionId,created_at:stamp,turns:[{turn_id:ids[1],question:'Выполни задачу',answer:'Результат сохранён.',state:'completed',created_at:stamp,sources:[],error:null,results,actions}]}}))
 await page.goto(base+'/#/chat')
 await page.getByText('Результат сохранён.',{exact:true}).waitFor()
}
function fakeMic(page,{denied=false,mp4=false}={}){
 return page.addInitScript(({denied,mp4})=>{
  window.micStops=0;window.micStarts=0
  Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>{if(denied)throw new DOMException('denied','NotAllowedError');return {getTracks:()=>[{stop:()=>window.micStops++}]}}}})
  window.MediaRecorder=class {
   static isTypeSupported(type){return mp4?type==='audio/mp4':type==='audio/webm;codecs=opus'}
   constructor(stream,options){this.mimeType=options.mimeType;this.state='inactive'}
   start(){this.state='recording';window.micStarts++}
   stop(){this.state='inactive';if(this.ondataavailable)this.ondataavailable({data:new Blob(['audio-fixture'],{type:this.mimeType})});queueMicrotask(()=>{if(this.onstop)this.onstop()})}
  }
 },{denied,mp4})
}

test('upload drag/drop preserves selected role, uploads once and never claims ready',async()=>{
 const {page,requests}=await open()
 try{
  let uploads=0,roleWrites=0
  await page.route('**/api/v0.1/artifacts',route=>{if(route.request().method()==='POST'){uploads++;return route.fulfill({status:201,json:{artifact_id:ids[0]}})}return route.fulfill({json:{artifacts:[],next_cursor:null}})})
  await page.route('**/usage-role',route=>{roleWrites++;return route.fulfill({json:{artifact_id:ids[0],usage_role:'stock'}})})
  await page.goto(base+'/#/files')
  await page.route('**/api/v0.1/artifacts?*',route=>route.fulfill({json:{artifacts:[],next_cursor:null}}))
  await page.getByRole('button',{name:'Загрузить файл',exact:true}).click()
  await page.getByLabel('Назначение файла',{exact:true}).selectOption('stock')
  const data=await page.evaluateHandle(()=>{const d=new DataTransfer();d.items.add(new File(['sku,free_quantity\nA,2'],'drop.csv',{type:'text/csv'}));return d})
  await page.getByRole('region',{name:'Загрузка файлов',exact:true}).dispatchEvent('drop',{dataTransfer:data})
  await page.getByText('Выбран: drop.csv',{exact:true}).waitFor()
  await page.getByRole('dialog').getByRole('button',{name:'Загрузить файл',exact:true}).click()
  await page.getByRole('status').filter({hasText:'Обработка для ИИ продолжается'}).waitFor()
  assert.equal(uploads,1);assert.equal(roleWrites,1)
  assert.deepEqual(requests.find(r=>r.method()==='PUT').postDataJSON(),{usage_role:'stock'})
  assert.equal(await page.getByText('Готов к ИИ анализу',{exact:true}).count(),0)
 }finally{await page.close()}
})

test('readiness requires ready and equal chunk counts; processing failures remain explicit',async()=>{
 const {page}=await open()
 try{
  const states=['ready','partial','unsupported','failed','running']
  const f=states.map((processing_state,i)=>({...files[0],artifact_id:'f'+i,filename:'File'+i+'.csv',processing_state,total_chunks:4,completed_chunks:2,processing_error:processing_state==='failed'?'Сервис недоступен':null}))
  await page.route('**/api/v0.1/artifacts?*',route=>route.fulfill({json:{artifacts:f,next_cursor:null}}))
  await page.goto(base+'/#/files');await page.getByRole('button',{name:'File4.csv',exact:true}).waitFor()
  assert.equal(await page.getByText('Готов к ИИ анализу',{exact:true}).count(),0)
  assert.equal(await page.getByText('Обработка',{exact:true}).count(),5)
  await page.getByText('Обработана только часть файла',{exact:true}).waitFor()
  await page.getByText('Формат не поддерживается для анализа',{exact:true}).waitFor()
  await page.getByText('Обработка остановлена: Сервис недоступен',{exact:true}).waitFor()
 }finally{await page.close()}
})

test('role of existing file is editable without changing its ID or bytes',async()=>{
 const {page,requests}=await open()
 try{
  await page.route('**/usage-role',route=>route.fulfill({json:{artifact_id:ids[1],usage_role:'economics'}}))
  await page.goto(base+'/#/files');await page.getByRole('button',{name:files[1].filename,exact:true}).click()
  await page.getByLabel('Назначение выбранного файла',{exact:true}).selectOption('economics')
  await page.waitForFunction(()=>document.querySelector('[aria-label="Назначение выбранного файла"]').disabled===false)
  const writes=requests.filter(r=>r.method()!=='GET')
  assert.equal(writes.length,1);assert.equal(new URL(writes[0].url()).pathname,`/api/v0.1/artifacts/${ids[1]}/usage-role`)
  assert.deepEqual(writes[0].postDataJSON(),{usage_role:'economics'})
 }finally{await page.close()}
})

test('radar reads all file pages and sends exact roles, file IDs and every filter',async()=>{
 const {page,requests}=await open()
 try{
  await page.route('**/api/v0.1/artifacts?*',route=>route.fulfill({json:new URL(route.request().url()).searchParams.get('cursor')==='0'?{artifacts:[files[0]],next_cursor:20}:{artifacts:[...files.slice(1),{...files[1],artifact_id:'unready',filename:'Не готов.csv',completed_chunks:1}],next_cursor:null}}))
  await page.route('**/radar/analyse',route=>route.fulfill({status:202,json:reportRun({state:'queued',report:null})}))
  await page.route('**/radar/runs/'+ids[0],route=>route.fulfill({json:reportRun()}))
  await page.goto(base+'/#/radar');await page.getByRole('button',{name:'Подобрать тендеры',exact:true}).waitFor()
  await page.getByLabel('Данные по складу',{exact:true}).selectOption(ids[1])
  assert.equal(await page.getByLabel('Данные по складу',{exact:true}).locator('option').filter({hasText:'Не готов'}).count(),0)
  for(const [label,value] of [['Цена от, ₽','100'],['Цена до, ₽','2000'],['Запас до закрытия заявок, дней','3'],['Минимальная маржа, %','17.5'],['Допустимая нехватка, %','2.25'],['Фокус отбора, если нужен','Болты М16']])await page.getByLabel(label,{exact:true}).fill(value)
  await page.getByRole('button',{name:'Подобрать тендеры',exact:true}).click()
  await page.getByRole('heading',{name:'Поставка болтов',exact:true}).waitFor()
  const sent=requests.find(r=>r.url().endsWith('/radar/analyse')).postDataJSON()
  for(const [index,role] of ['procurement','stock','economics'].entries())assert.deepEqual(sent[role+'_source'],{artifact_id:ids[index]})
  assert.deepEqual(sent.filters,{min_price:'100',max_price:'2000',min_days_remaining:3,focus:'Болты М16',max_shortage_percent:'2.25',min_margin_percent:'17.5'})
  assert.equal(requests.some(r=>r.method()==='POST'&&/artifacts|batches/.test(r.url())),false)
 }finally{await page.close()}
})

test('radar shows partial economics, combined exclusions, protected PDF, equal cards and natural history page growth',async()=>{
 const {page,requests}=await open()
 try{
  const run=reportRun();run.state='failed';run.error='Порция не завершена';run.report.analysis_complete=false;run.report.portion_errors=[{ids:['t2'],code:'MODEL_TIMEOUT',message:'Истекло ожидание модели'}]
  await page.route('**/radar/runs',route=>route.fulfill({json:Array.from({length:35},(_,i)=>({run_id:'history'+i,batch_id:ids[0],created_at:stamp,state:'completed',focus:'Запуск '+i}))}))
  await page.route('**/radar/runs/'+ids[0],route=>route.fulfill({json:run}))
  await page.route('**/report.pdf',route=>route.fulfill({contentType:'application/pdf',body:'%PDF-1.4\n%%EOF'}))
  await page.goto(base+'/#/radar?run='+ids[0]);await page.getByRole('heading',{name:'Поставка болтов',exact:true}).waitFor()
  await page.getByRole('button',{name:'Можно участвовать (1)',exact:true}).waitFor()
  await page.getByRole('button',{name:'Требует внимания (1)',exact:true}).waitFor()
  await page.getByRole('button',{name:'Вне анализа (1)',exact:true}).click()
  await page.getByRole('heading',{name:'Поставка шайб',exact:true}).waitFor()
  await page.getByRole('button',{name:'Можно участвовать (1)',exact:true}).click()
  assert.match(await page.getByRole('region',{name:'Экономика закупки'}).innerText(),/300.00/)
  await page.getByText('Демонстрационная оценка',{exact:true}).waitFor()
  await page.getByText('Анализ не завершён.',{exact:false}).waitFor()
  await page.getByText('Ошибки порций (1)',{exact:true}).click();await page.getByText('Истекло ожидание модели',{exact:false}).waitFor()
  const download=page.waitForEvent('download');await page.getByRole('button',{name:'Скачать PDF',exact:true}).click();assert.match((await download).suggestedFilename(),/\.pdf$/)
  assert.equal(requests.find(r=>r.url().endsWith('/report.pdf')).headers()['x-bervel-web'],'1')
  const heights=await page.locator('.source-card').evaluateAll(nodes=>nodes.map(n=>n.getBoundingClientRect().height));assert.ok(Math.max(...heights)-Math.min(...heights)<1)
  assert.equal(await page.locator('.history-column').evaluate(n=>n.scrollHeight<=n.clientHeight&&getComputedStyle(n).overflowY==='visible'),true)
  const previous=await page.locator('.result-column').evaluate(n=>n.scrollTop)
  await page.locator('.history-column').evaluate(n=>n.scrollTop=400)
  assert.equal(await page.locator('.result-column').evaluate(n=>n.scrollTop),previous)
  await page.evaluate(()=>document.fonts.ready)
  const fs=require('node:fs');const folder=require('node:path').resolve(__dirname,'../local/workspace-polish-2026-10-28/frontend');fs.mkdirSync(folder,{recursive:true})
  await page.evaluate(()=>window.scrollTo(0,0))
  await page.screenshot({path:folder+'/radar-desktop.png'})
  await page.setViewportSize({width:390,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:folder+'/radar-mobile.png',fullPage:true})
 }finally{await page.close()}
})

test('legacy take_in_work remains a primary candidate without economics confirmation',async()=>{
 const {page}=await open()
 try{
  const run=reportRun();run.report.schema_version='1.2.0'
  await page.route('**/radar/runs/'+ids[0],route=>route.fulfill({json:run}))
  await page.goto(base+'/#/radar?run='+ids[0]);await page.getByRole('button',{name:'Первичный кандидат (1)',exact:true}).waitFor()
  assert.equal(await page.getByRole('button',{name:/^Можно участвовать/}).count(),0)
  await page.getByText('Исторический первичный отбор:',{exact:false}).waitFor()
 }finally{await page.close()}
})

test('assistant polls radar card, downloads protected PDF and preserves all typed results after reload',async()=>{
 const {page,requests}=await open()
 try{
  let reads=0
  await page.route('**/radar/runs/'+ids[0],route=>route.fulfill({json:++reads===1?reportRun({state:'running',report:null}):reportRun()}))
  await page.route('**/report.pdf',route=>route.fulfill({contentType:'application/pdf',body:'%PDF-1.4\n%%EOF'}))
  await page.route('**/artifacts/'+ids[2]+'/content',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="100"><rect width="300" height="100" fill="#8addd4"/><text x="10" y="50">BERVEL</text></svg>'}))
  const results=[{kind:'radar_run',run_id:ids[0],state:'queued',detail_url:'/api/v0.1/radar/runs/'+ids[0],pdf_url:'',deep_link:''},{kind:'file',artifact_id:ids[2],filename:'Схема.svg',media_type:'image/svg+xml',download_url:'',preview_url:''},{kind:'ticket',ticket_id:ids[1],title:'Проблема в отчёте',status:'created',deep_link:''},{kind:'web',answer:'Найден ответ',sources:[{title:'Официальный источник',url:'https://example.org/evidence'},{title:'Небезопасная ссылка',url:'javascript:alert(1)'}]},{kind:'data',operation:'files.list',data:{count:3}}]
  await assistant(page,results,[{action_id:ids[0],operation:'radar.analyse',state:'completed',result:null,error:null}])
  await page.getByRole('heading',{name:'Анализ завершён',exact:true}).waitFor()
  assert.ok(reads>=2);assert.equal(await page.getByRole('link',{name:'Открыть отчёт радара'}).getAttribute('href'),'#/radar?run='+ids[0])
  assert.equal(await page.getByRole('img',{name:'Схема.svg'}).evaluate(img=>img.complete&&img.naturalWidth>0),true)
  assert.equal(await page.locator('svg text').filter({hasText:'BERVEL'}).count(),0)
  await page.locator('summary').filter({hasText:/^Веб-источники \(/}).click()
  assert.equal(await page.getByRole('link',{name:'Официальный источник'}).getAttribute('href'),'https://example.org/evidence')
  assert.equal(await page.locator('a[href^="javascript:"]').count(),0)
  await page.getByRole('link',{name:'Открыть обращение'}).waitFor()
  const download=page.waitForEvent('download');await page.getByRole('button',{name:'Скачать PDF',exact:true}).click();await download
  assert.equal(requests.find(r=>r.url().endsWith('/report.pdf')).headers()['x-bervel-web'],'1')
  await page.reload();await page.getByRole('heading',{name:'Схема.svg'}).waitFor()
 }finally{await page.close()}
})

for(const mp4 of [false,true])test('voice '+(mp4?'MP4':'WebM')+' creates editable draft without sending a message',async()=>{
 const {page,requests}=await open()
 try{
  await fakeMic(page,{mp4});await page.route('**/voice/transcribe',route=>route.fulfill({json:{text:'Проверь закупки по складу'}}))
  await page.goto(base+'/#/chat');await page.getByRole('button',{name:'Записать голосовое сообщение',exact:true}).click();await page.getByRole('button',{name:'Остановить запись'}).click()
  await page.waitForFunction(()=>document.querySelector('#chat-message').value==='Проверь закупки по складу')
  await page.getByLabel('Сообщение',{exact:true}).fill('Исправленный текст')
  assert.equal(requests.some(r=>r.url().includes('/chat/sessions')&&r.method()!=='GET'),false)
  const audio=requests.find(r=>r.url().endsWith('/voice/transcribe'))
  assert.match(audio.headers()['content-type'],/multipart\/form-data/);assert.match(audio.postData(),mp4?/filename="voice.mp4"/:/filename="voice.webm"/)
  assert.ok(await page.evaluate(()=>window.micStops)>0)
 }finally{await page.close()}
})

test('voice denied and provider error leave chat usable; cancellation releases tracks and ignores late text',async()=>{
 const {page,requests}=await open()
 try{
  await fakeMic(page,{denied:true});await page.goto(base+'/#/chat');await page.getByRole('button',{name:'Записать голосовое сообщение',exact:true}).click()
  await page.getByRole('alert').filter({hasText:'Доступ к микрофону запрещён'}).waitFor();await page.getByLabel('Сообщение',{exact:true}).fill('Работает без голоса')
  assert.equal(await page.getByRole('button',{name:'Отправить',exact:true}).isEnabled(),true)
  await fakeMic(page);await page.reload()
  let release;const gate=new Promise(r=>release=r)
  await page.route('**/voice/transcribe',async route=>{await gate;await route.fulfill({json:{text:'Поздний текст'}}).catch(()=>{})})
  await page.getByRole('button',{name:'Записать голосовое сообщение',exact:true}).click();await page.getByRole('button',{name:'Остановить запись'}).click();await page.getByText('Распознаём запись…',{exact:true}).waitFor()
  await page.getByRole('button',{name:'Отменить запись'}).click();release();await page.waitForTimeout(100)
  assert.equal(await page.getByLabel('Сообщение',{exact:true}).inputValue(),'')
  await page.unroute('**/voice/transcribe');await page.route('**/voice/transcribe',route=>route.fulfill({status:503,json:{error:{code:'VOICE_UNCONFIGURED'}}}))
  await page.getByRole('button',{name:'Записать голосовое сообщение',exact:true}).click();await page.getByRole('button',{name:'Остановить запись'}).click()
  await page.getByRole('alert').filter({hasText:'Сначала добавьте ключ xAI'}).waitFor()
  await page.getByRole('button',{name:'Записать голосовое сообщение',exact:true}).click();await page.getByRole('link',{name:'Файлы',exact:true}).click()
  await page.waitForFunction(()=>window.micStops>=3)
  const count=requests.filter(r=>r.url().endsWith('/voice/transcribe')).length
  await page.getByRole('link',{name:'Универсальный ассистент',exact:true}).click();await page.getByRole('button',{name:'Записать голосовое сообщение',exact:true}).waitFor()
  assert.equal(requests.filter(r=>r.url().endsWith('/voice/transcribe')).length,count)
 }finally{await page.close()}
})

test('tickets create, detail, full edit and scoped list preserve created status without a send button',async()=>{
 const {page,requests}=await open(390)
 try{
  let saved
  await page.route('**/api/v0.1/tickets*',route=>{const r=route.request();if(r.method()==='POST'){const b=r.postDataJSON();saved={...b,ticket_id:ids[1],status:'created',revision:1,created_at:stamp,updated_at:stamp};return route.fulfill({status:201,json:saved})}return route.fulfill({json:{tickets:saved?[saved]:[],next_cursor:null}})})
  await page.route('**/tickets/'+ids[1],route=>{const r=route.request();if(r.method()==='PUT')saved={...saved,...r.postDataJSON(),revision:2};return route.fulfill({json:saved})})
  await page.goto(base+'/#/tickets');await page.getByRole('button',{name:'Создать обращение',exact:true}).click()
  for(const [label,value] of [['Заголовок','Проблема с файлом'],['Проблема','Не открывается таблица'],['Ожидаемое поведение','Видны строки'],['Фактическое поведение','Пустой экран'],['Шаги воспроизведения','Открыть файлы\nВыбрать таблицу'],['Контекст и диагностика','Браузер Chromium'],['Ссылки','https://example.org/file']])await page.getByLabel(label,{exact:true}).fill(value)
  await page.getByRole('button',{name:'Сохранить обращение',exact:true}).click();await page.getByRole('button',{name:'Редактировать обращение',exact:true}).click()
  await page.getByLabel('Фактическое поведение',{exact:true}).fill('Ошибка загрузки')
  await page.getByRole('button',{name:'Сохранить обращение',exact:true}).click();await page.getByText('Ошибка загрузки',{exact:true}).waitFor()
  assert.equal(saved.status,'created');assert.equal(saved.revision,2)
  assert.equal(await page.getByRole('button',{name:/Отправить/}).count(),0)
  const writes=requests.filter(r=>r.method()!=='GET');assert.equal(writes.length,2);assert.deepEqual(writes[0].postDataJSON().steps,['Открыть файлы','Выбрать таблицу']);assert.equal(writes[1].postDataJSON().expected_revision,1)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 }finally{await page.close()}
})

test('file readiness polling covers later pages and waits for the last fragment',async()=>{
 const {page}=await open()
 try{
  let polls=0
  await page.route('**/api/v0.1/artifacts?*',route=>{const cursor=new URL(route.request().url()).searchParams.get('cursor');if(cursor==='0'){polls++;return route.fulfill({json:{artifacts:[files[0]],next_cursor:20}})}return route.fulfill({json:{artifacts:[{...files[1],processing_state:'ready',completed_chunks:polls>1?2:1}],next_cursor:null}})})
  await page.goto(base+'/#/files');await page.getByRole('button',{name:'Показать ещё файлы',exact:true}).click()
  const stock=page.getByRole('button',{name:files[1].filename,exact:true});await stock.waitFor()
  assert.match(await stock.innerText(),/Обработка/)
  await stock.getByText('Готов к ИИ анализу',{exact:true}).waitFor({timeout:5000})
 }finally{await page.close()}
})

test('not_analysed-only saved report opens the combined outside category immediately',async()=>{
 const {page}=await open()
 try{
  const run=reportRun();run.report.items=run.report.items.slice(2);run.report.counts={total:1,model_analysed:0,take_in_work:0,needs_review:0,excluded:0,not_analysed:1}
  await page.route('**/radar/runs/'+ids[0],route=>route.fulfill({json:run}));await page.goto(base+'/#/radar?run='+ids[0]);await page.getByRole('heading',{name:'Поставка шайб',exact:true}).waitFor()
  assert.equal(await page.getByRole('button',{name:'Вне анализа (1)',exact:true}).getAttribute('aria-pressed'),'true')
 }finally{await page.close()}
})

test('voice refuses unsupported MIME and oversize recording before any API request',async()=>{
 const {page,requests}=await open()
 try{
  await fakeMic(page);await page.goto(base+'/#/chat')
  await page.evaluate(()=>MediaRecorder.isTypeSupported=()=>false)
  await page.getByRole('button',{name:'Записать голосовое сообщение',exact:true}).click();await page.getByRole('alert').filter({hasText:'не поддерживает WebM или MP4'}).waitFor()
  await page.evaluate(()=>{MediaRecorder.isTypeSupported=type=>type.startsWith('audio/webm');MediaRecorder.prototype.stop=function(){this.state='inactive';if(this.ondataavailable)this.ondataavailable({data:new Blob([new Uint8Array(5*1024*1024+1)],{type:this.mimeType})});queueMicrotask(()=>{if(this.onstop)this.onstop()})}})
  await page.getByRole('button',{name:'Записать голосовое сообщение',exact:true}).click();await page.getByRole('button',{name:'Остановить запись'}).click();await page.getByRole('alert').filter({hasText:'Запись больше 5 МиБ'}).waitFor()
  assert.equal(requests.some(r=>r.url().endsWith('/voice/transcribe')),false)
  assert.equal(await page.getByLabel('Сообщение',{exact:true}).isEnabled(),true)
 }finally{await page.close()}
})

test('navigating away aborts pending transcription and a late response cannot change the draft',async()=>{
 const {page}=await open()
 let release
 try{
  await fakeMic(page);const gate=new Promise(r=>release=r)
  await page.route('**/voice/transcribe',async route=>{await gate;await route.fulfill({json:{text:'Ответ отменённой записи'}}).catch(()=>{})})
  await page.goto(base+'/#/chat');await page.getByLabel('Сообщение',{exact:true}).fill('Сохранённый черновик')
  await page.getByRole('button',{name:'Записать голосовое сообщение',exact:true}).click();await page.getByRole('button',{name:'Остановить запись'}).click();await page.getByText('Распознаём запись…',{exact:true}).waitFor()
  await page.getByRole('link',{name:'Файлы',exact:true}).click();await page.getByRole('heading',{name:'Все файлы',exact:true}).waitFor();release()
  await page.getByRole('link',{name:'Универсальный ассистент',exact:true}).click();await page.getByRole('button',{name:'Записать голосовое сообщение',exact:true}).waitFor()
  assert.equal(await page.getByLabel('Сообщение',{exact:true}).inputValue(),'Сохранённый черновик')
 }finally{release?.();await page.close()}
})

test('all workspace pages fit mobile and allow natural document scroll',async()=>{
 const {page}=await open()
 const errors=[];page.on('pageerror',e=>errors.push(e.message))
 try{
  const fs=require('node:fs'),folder=require('node:path').resolve(__dirname,'../local/workspace-polish-2026-10-28/frontend');fs.mkdirSync(folder,{recursive:true})
  for(const width of [1440,390]){
   await page.setViewportSize({width,height:900})
   for(const [route,heading] of [['files','Файлы'],['chat','Универсальный ассистент'],['tickets','Обращения'],['settings','Настройки']]){
    await page.goto(base+'/#/'+route)
    if(route==='chat')await page.getByRole('textbox',{name:'Сообщение',exact:true}).waitFor()
    else await page.getByRole('heading',{name:route==='files'?'Все файлы':heading,exact:true}).waitFor()
    assert.equal(await page.getByRole('link',{name:heading,exact:true}).getAttribute('aria-current'),'page',route+' nav')
    if(route==='files')await page.getByRole('button',{name:files[0].filename,exact:true}).click()
    if(route==='settings')await page.getByLabel('API-ключ xAI',{exact:true}).waitFor()
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,route+' width '+width)
    if(width===1440&&route==='files')assert.equal(await page.getByRole('region',{name:'Просмотр файла',exact:true}).evaluate(n=>getComputedStyle(n).overflowY),'visible')
    await page.evaluate(()=>document.fonts.ready)
    await page.screenshot({path:folder+'/'+route+'-'+(width===390?'mobile':'desktop')+'.png',fullPage:width===390})
   }
  }
  assert.deepEqual(errors,[])
 }finally{await page.close()}
})

test('typing a new assistant message does not restart an already open generated-file preview',async()=>{
 const {page}=await open()
 try{
  let reads=0;await page.route('**/artifacts/'+ids[0]+'/preview*',route=>{reads++;return route.fulfill({json:{kind:'text',media_type:'text/plain',text:'Сохранённый документ',columns:[],rows:[],total_rows:0,next_cursor:null,notice:''}})})
  await assistant(page,[{kind:'file',artifact_id:ids[0],filename:'Документ.txt',media_type:'text/plain',download_url:'',preview_url:''}])
  await page.getByRole('button',{name:'Просмотреть файл',exact:true}).click();await page.getByText('Сохранённый документ',{exact:true}).waitFor();assert.equal(reads,1)
  await page.getByLabel('Сообщение',{exact:true}).fill('Следующий вопрос');await page.waitForTimeout(100)
  assert.equal(reads,1)
 }finally{await page.close()}
})

test('file readiness is a readable badge to the right of each name and file picker is Russian',async()=>{
 const {page}=await open()
 try{
  for(const width of [1440,390]){
   await page.setViewportSize({width,height:900});await page.goto(base+'/#/files')
   const file=page.getByRole('button',{name:files[0].filename,exact:true});await file.waitFor()
   const name=await file.locator('.file-name').boundingBox(),badge=await file.locator('.file-readiness').boundingBox()
   assert.ok(badge.x>=name.x+name.width-1);assert.ok(Math.abs(badge.y-name.y)<2)
   assert.ok(Number(await file.locator('.file-readiness').evaluate(n=>parseFloat(getComputedStyle(n).fontSize)))>=11)
   await page.getByRole('button',{name:'Загрузить файл',exact:true}).click()
   await page.getByRole('button',{name:'Выбрать файл',exact:true}).waitFor()
   const input=page.getByLabel('Файл',{exact:true});assert.ok((await input.boundingBox()).width<=1)
   const chooser=page.waitForEvent('filechooser');await page.getByRole('button',{name:'Выбрать файл',exact:true}).click();await (await chooser).setFiles({name:'chosen.csv',mimeType:'text/csv',buffer:Buffer.from('sku,quantity\\nA,1')});await page.getByText('Выбран: chosen.csv',{exact:true}).waitFor()
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
   await page.getByRole('dialog').getByRole('button',{name:'Закрыть',exact:true}).click()
  }
 }finally{await page.close()}
})

for(const entry of ['created','existing'])test('ticket '+entry+' edit sends exactly the writable API fields',async()=>{
 const {page,requests}=await open()
 const content={title:'Проверка редактирования',problem:'Проблема',expected:'Ожидание',actual:'Было',steps:['Шаг один'],context:'Контекст',links:['https://example.org/issue']}
 let saved={...content,ticket_id:ids[1],status:'created',revision:1,created_at:stamp,updated_at:stamp}
 const accepted=['title','problem','expected','actual','steps','context','links','command_key','expected_revision'].sort()
 try{
  await page.route('**/api/v0.1/tickets*',route=>{const r=route.request();if(r.method()==='POST'){const b=r.postDataJSON();saved={...content,...Object.fromEntries(Object.keys(content).map(k=>[k,b[k]])),ticket_id:ids[1],status:'created',revision:1,created_at:stamp,updated_at:stamp};return route.fulfill({status:201,json:saved})}return route.fulfill({json:{tickets:entry==='existing'?[saved]:[],next_cursor:null}})})
  await page.route('**/tickets/'+ids[1],route=>{const r=route.request();if(r.method()==='PUT'){const b=r.postDataJSON();if(JSON.stringify(Object.keys(b).sort())!==JSON.stringify(accepted))return route.fulfill({status:422,json:{detail:[{type:'extra_forbidden'}]}});saved={...saved,...Object.fromEntries(Object.keys(content).map(k=>[k,b[k]])),revision:2}}return route.fulfill({json:saved})})
  await page.goto(base+'/#/tickets')
  if(entry==='created'){
   await page.getByRole('button',{name:'Создать обращение',exact:true}).click();await page.getByLabel('Заголовок',{exact:true}).fill(content.title);await page.getByLabel('Проблема',{exact:true}).fill(content.problem);await page.getByRole('button',{name:'Сохранить обращение',exact:true}).click()
  }else await page.getByRole('region',{name:'Список обращений',exact:true}).getByRole('button').filter({hasText:content.title}).click()
  await page.getByRole('button',{name:'Редактировать обращение',exact:true}).click();await page.getByLabel('Фактическое поведение',{exact:true}).fill('Исправлено')
  const sent=page.waitForRequest(r=>r.method()==='PUT'&&r.url().endsWith('/tickets/'+ids[1]));await page.getByRole('button',{name:'Сохранить обращение',exact:true}).click();const body=(await sent).postDataJSON()
  assert.deepEqual(Object.keys(body).sort(),accepted)
  assert.equal(body.actual,'Исправлено');assert.equal(body.expected_revision,1);assert.equal(typeof body.command_key,'string')
  await page.getByRole('button',{name:'Редактировать обращение',exact:true}).waitFor();await page.getByRole('region',{name:'Карточка обращения',exact:true}).getByText('Исправлено',{exact:true}).waitFor();assert.equal(saved.revision,2)
  const folder=require('node:path').resolve(__dirname,'../local/workspace-polish-2026-10-28/frontend');require('node:fs').mkdirSync(folder,{recursive:true});require('node:fs').writeFileSync(folder+'/ticket-edit-'+entry+'.json',JSON.stringify(body,null,2))
  assert.equal(requests.filter(r=>r.method()==='PUT').length,1)
 }finally{await page.close()}
})

for(const edit of [false,true])test('ticket '+(edit?'edit':'create')+' uses API field limits and allows correction after definite 422',async()=>{
 const {page}=await open()
 const initial={ticket_id:ids[1],title:'Обращение',problem:'Проблема',expected:'',actual:'',steps:[],context:'',links:[],status:'created',revision:1,created_at:stamp,updated_at:stamp}
 let payloads=[]
 try{
  await page.route('**/api/v0.1/tickets*',route=>{if(route.request().method()==='POST'){payloads.push(route.request().postDataJSON());return payloads.length===1?route.fulfill({status:422,json:{detail:'Rejected content'}}):route.fulfill({status:201,json:{...initial,...payloads.at(-1),title:'Исправленное обращение'}})}return route.fulfill({json:{tickets:edit?[initial]:[],next_cursor:null}})})
  await page.route('**/tickets/'+ids[1],route=>{if(route.request().method()==='PUT'){payloads.push(route.request().postDataJSON());return payloads.length===1?route.fulfill({status:422,json:{detail:'Rejected content'}}):route.fulfill({json:{...initial,...payloads.at(-1),title:'Исправленное обращение',revision:2}})}return route.fulfill({json:initial})})
  await page.goto(base+'/#/tickets')
  if(edit){await page.getByRole('region',{name:'Список обращений',exact:true}).getByRole('button').click();await page.getByRole('button',{name:'Редактировать обращение',exact:true}).click()}else{await page.getByRole('button',{name:'Создать обращение',exact:true}).click();await page.getByLabel('Заголовок',{exact:true}).fill(initial.title);await page.getByLabel('Проблема',{exact:true}).fill(initial.problem)}


  await page.getByRole('button',{name:'Сохранить обращение',exact:true}).click();await page.getByRole('alert').filter({hasText:'Проверьте обязательные поля'}).waitFor()
  assert.equal(await page.getByLabel('Заголовок',{exact:true}).isEditable(),true)
  assert.equal(await page.getByLabel('Проблема',{exact:true}).inputValue(),initial.problem)
  assert.equal(await page.getByLabel('Заголовок',{exact:true}).getAttribute('maxlength'),'200')
  for(const label of ['Проблема','Ожидаемое поведение','Фактическое поведение','Контекст и диагностика'])assert.equal(await page.getByLabel(label,{exact:true}).getAttribute('maxlength'),'8000')
  await page.getByLabel('Заголовок',{exact:true}).fill('Исправленное обращение');await page.getByRole('button',{name:'Сохранить обращение',exact:true}).click();await page.getByRole('button',{name:'Редактировать обращение',exact:true}).waitFor()
  assert.equal(payloads.length,2);assert.notEqual(payloads[0].command_key,payloads[1].command_key);assert.equal(payloads[1].title,'Исправленное обращение')
 }finally{await page.close()}
})

test('ticket uncertain network outcome keeps frozen payload and command for a safe retry',async()=>{
 const {page}=await open()
 const payloads=[]
 try{
  await page.route('**/api/v0.1/tickets*',route=>{if(route.request().method()==='POST'){const b=route.request().postDataJSON();payloads.push(b);return payloads.length===1?route.abort('failed'):route.fulfill({status:201,json:{...b,ticket_id:ids[1],status:'created',revision:1,created_at:stamp,updated_at:stamp}})}return route.fulfill({json:{tickets:[],next_cursor:null}})})
  await page.goto(base+'/#/tickets');await page.getByRole('button',{name:'Создать обращение',exact:true}).click();await page.getByLabel('Заголовок',{exact:true}).fill('Сохранить один раз');await page.getByLabel('Проблема',{exact:true}).fill('Проверка связи');await page.getByRole('button',{name:'Сохранить обращение',exact:true}).click();await page.getByRole('alert').waitFor()
  assert.equal(await page.getByLabel('Заголовок',{exact:true}).isEditable(),false)
  await page.getByRole('button',{name:'Повторить сохранение',exact:true}).click();await page.getByRole('button',{name:'Редактировать обращение',exact:true}).waitFor()
  assert.equal(payloads.length,2);assert.deepEqual(payloads[0],payloads[1])
 }finally{await page.close()}
})

test('ticket validates step and link counts, per-item lengths and safe URL fields before submission',async()=>{
 const {page,requests}=await open()
 try{
  await page.goto(base+'/#/tickets');await page.getByRole('button',{name:'Создать обращение',exact:true}).click();await page.getByLabel('Заголовок',{exact:true}).fill('Границы полей');await page.getByLabel('Проблема',{exact:true}).fill('Проверка')
  for(const steps of [Array.from({length:51},(_,i)=>'Шаг '+i).join('\n'),'А'.repeat(4001)]){
   await page.getByLabel('Шаги воспроизведения',{exact:true}).fill(steps);await page.getByRole('button',{name:'Сохранить обращение',exact:true}).click();await page.getByRole('alert').filter({hasText:'50 шагов'}).waitFor();assert.equal(requests.filter(r=>r.method()==='POST').length,0)
  }
  await page.getByLabel('Шаги воспроизведения',{exact:true}).fill('Шаг один')
  for(const links of [Array.from({length:31},(_,i)=>'https://example.org/'+i).join('\n'),'https://example.org/'+'a'.repeat(2048),'https://user:password@example.org/']){
   await page.getByLabel('Ссылки',{exact:true}).fill(links);await page.getByRole('button',{name:'Сохранить обращение',exact:true}).click();await page.getByRole('alert').filter({hasText:'30 ссылок'}).waitFor();assert.equal(requests.filter(r=>r.method()==='POST').length,0)
  }
 }finally{await page.close()}
})

test('delayed role update changes its file only and cannot overwrite a newly selected detail',async()=>{
 const {page}=await open()
 let release
 try{
  const gate=new Promise(r=>release=r)
  await page.route('**/artifacts/'+ids[0]+'/usage-role',async route=>{await gate;await route.fulfill({json:{artifact_id:ids[0],usage_role:'economics'}})})
  await page.goto(base+'/#/files');await page.getByRole('button',{name:files[0].filename,exact:true}).click()
  await page.getByLabel('Назначение выбранного файла',{exact:true}).selectOption('economics')
  await page.getByRole('button',{name:files[1].filename,exact:true}).click();assert.equal(await page.getByLabel('Назначение выбранного файла',{exact:true}).inputValue(),'stock')
  release();await page.getByRole('button',{name:files[0].filename,exact:true}).getByText('Экономика и производство',{exact:true}).waitFor()
  assert.equal(await page.getByLabel('Назначение выбранного файла',{exact:true}).inputValue(),'stock')
  await page.getByRole('region',{name:'Просмотр файла',exact:true}).getByRole('heading',{name:files[1].filename,exact:true}).waitFor()
 }finally{release?.();await page.close()}
})

test('resuming the only failed file updates its list readiness and clears the old error without refreshing preview',async()=>{
 const {page}=await open()
 try{
  let resumed=false,previews=0
  const file={...files[0],processing_state:'failed',completed_chunks:1,total_chunks:2,processing_error:'Индексирование остановлено'}
  await page.route('**/api/v0.1/artifacts?*',route=>route.fulfill({json:{artifacts:[resumed?{...file,processing_state:'ready',completed_chunks:2,processing_error:null}:file],next_cursor:null}}))
  await page.route('**/knowledge',route=>route.fulfill({json:{state:resumed?'ready':'failed',error:resumed?null:'Индексирование остановлено',total_chunks:2,completed_chunks:resumed?2:1}}))
  await page.route('**/knowledge/resume',route=>{resumed=true;return route.fulfill({json:{state:'pending',error:null,total_chunks:2,completed_chunks:1}})})
  await page.route('**/preview*',route=>{previews++;return route.fulfill({json:{kind:'text',media_type:'text/plain',text:'Не перезагружать предпросмотр',columns:[],rows:[],total_rows:0,next_cursor:null,notice:''}})})
  await page.goto(base+'/#/files');const row=page.getByRole('button',{name:file.filename,exact:true});await row.click();await row.getByText('Обработка остановлена: Индексирование остановлено',{exact:true}).waitFor();await page.getByText('Не перезагружать предпросмотр',{exact:true}).waitFor()
  await page.getByRole('button',{name:'Продолжить подготовку',exact:true}).click()
  await page.getByRole('region',{name:'Подготовка файла для ИИ',exact:true}).getByText('Готов к ИИ анализу',{exact:true}).waitFor()
  await row.getByText('Готов к ИИ анализу',{exact:true}).waitFor({timeout:6000})
  assert.equal(await row.getByText('Обработка остановлена:',{exact:false}).count(),0)
  assert.equal(previews,1)
 }finally{await page.close()}
})

for(const parameter of ['ticket','ticket_id'])test('ticket deep link accepts '+parameter+' and opens the saved card',async()=>{
 const {page,requests}=await open()
 try{
  const ticket={ticket_id:ids[1],title:'Карточка по ссылке',problem:'Проблема',expected:'',actual:'',steps:[],context:'',links:[],status:'created',revision:1,created_at:stamp,updated_at:stamp}
  await page.route('**/tickets/'+ids[1],route=>route.fulfill({json:ticket}))
  await page.goto(base+'/#/tickets?'+parameter+'='+ids[1]);await page.getByRole('region',{name:'Карточка обращения',exact:true}).getByRole('heading',{name:ticket.title,exact:true}).waitFor()
  assert.equal(requests.some(r=>r.method()!=='GET'),false)
 }finally{await page.close()}
})

test('assistant report page receipts use a human readable operation label',async()=>{
 const {page}=await open()
 try{
  await assistant(page,[{kind:'data',operation:'radar.read_page',data:{items:[],total:0}}],[{action_id:ids[0],operation:'radar.read_page',state:'completed',result:null,error:null}])
  await page.getByText('Действия (1)',{exact:true}).click();await page.getByText('Строки отчёта: Выполнено',{exact:true}).waitFor()
  assert.equal(await page.getByText('Строки отчёта · данные',{exact:true}).count(),0)
  assert.equal(await page.getByText('radar.read_page',{exact:false}).count(),0)
 }finally{await page.close()}
})

for(const [operation,label] of [['knowledge.search','Поиск по базе знаний'],['file.read_page','Страница файла'],['file.query','Расчёт по таблице']])test('assistant labels '+operation+' without exposing read/search JSON',async()=>{
 const {page}=await open()
 try{
  await assistant(page,[{kind:'data',operation,data:{diagnostic_marker:'internal result'}}],[{action_id:ids[0],operation,state:'completed',result:null,error:null}])
  await page.getByText('Действия (1)',{exact:true}).click()
  await page.getByText(label+': Выполнено',{exact:true}).waitFor()
  assert.equal(await page.getByText(operation,{exact:false}).count(),0)
  if(operation!=='file.query')assert.equal(await page.getByText('internal result',{exact:false}).count(),0)
 }finally{await page.close()}
})

test('assistant displays exact table totals and mock status as readable results',async()=>{
 const {page}=await open()
 try{
  await assistant(page,[{kind:'data',operation:'file.query',data:{filename:'stock.csv',count:11,sums:{free_quantity:'1234.50'},is_mock:true,notice:'Демонстрационные остатки.',complete:true,group_by:null,groups:[]}}])
  const card=page.getByRole('region',{name:'Расчёт по таблице'})
  await card.getByText('Строк данных: 11',{exact:true}).waitFor()
  await card.getByText('1234.50',{exact:true}).waitFor()
  await card.getByText('Демонстрационные остатки.',{exact:true}).waitFor()
  assert.equal(await card.locator('pre').count(),0)
 }finally{await page.close()}
})

test('generated mock CSV keeps a visible warning on its download card',async()=>{
 const {page}=await open()
 try{
  await assistant(page,[{kind:'file',artifact_id:ids[0],filename:'result.csv',media_type:'text/csv',download_url:'/unused',preview_url:'/unused',is_mock:true,notice:'Демонстрационные данные (mock); не фактический склад.'}])
  await page.getByRole('region',{name:'Созданный файл'}).getByText('Демонстрационные данные (mock); не фактический склад.',{exact:true}).waitFor()
 }finally{await page.close()}
})

test('grouped stock totals show units separately and never mix their grand total',async()=>{
 const {page}=await open()
 try{
  await assistant(page,[{kind:'data',operation:'file.query',data:{filename:'stock.csv',count:11,sums:{free_quantity:'297210'},is_mock:true,complete:true,group_by:'unit',groups:[{value:'шт',count:10,sums:{free_quantity:'94148'}},{value:'комплект',count:1,sums:{free_quantity:'203062'}}]}}])
  const card=page.getByRole('region',{name:'Расчёт по таблице'})
  await card.getByRole('columnheader',{name:'Единица измерения',exact:true}).waitFor()
  await card.getByRole('cell',{name:'94148',exact:true}).waitFor()
  await card.getByRole('cell',{name:'203062',exact:true}).waitFor()
  assert.equal(await card.getByText('297210',{exact:true}).count(),0)
 }finally{await page.close()}
})
