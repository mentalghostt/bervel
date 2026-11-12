const assert=require('node:assert/strict')
const {test,before,after,beforeEach,afterEach}=require('node:test')
const {chromium}=require('playwright')
const {resolve}=require('node:path')
const {mkdirSync}=require('node:fs')
const base=process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1:4173',out=resolve(process.env.BERVEL_TEST_ARTIFACT_ROOT||resolve(__dirname,'../local/inspection-engineer-2026-10-30/native-cli/ui'))
const sid='22222222-2222-4222-8222-222222222222',tid='33333333-3333-4333-8333-333333333333',aid='44444444-4444-4444-8444-444444444444',pid='55555555-5555-4555-8555-555555555555',time='2026-10-30T10:00:00Z'
let browser,context,page,session,requests,errors,imageMedia,cleanQuestion,pauseReadAfterSend,messageSent
before(async()=>{mkdirSync(out,{recursive:true});browser=await chromium.launch({executablePath:process.env.BERVEL_BROWSER_PATH,headless:true})})
after(async()=>browser?.close())
beforeEach(async()=>{
 context=await browser.newContext({viewport:{width:1440,height:1000}});await context.addInitScript(id=>localStorage.setItem('bervel.inspection.session_id',id),sid);page=await context.newPage();page.setDefaultTimeout(4500);requests=[];errors=[];imageMedia='image/svg+xml';cleanQuestion=null;pauseReadAfterSend=false;messageSent=false
 session={session_id:sid,created_at:time,engine:'codex-cli',cli_thread_id:'019a-session-native-thread',turns:[{turn_id:tid,question:'Проверь систему',answer:'## Готово\n\nCLI завершил запрос.',state:'completed',created_at:time,sources:[],results:[],actions:[],error:null,progress_text:null}]}
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v0.1/**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname;requests.push({path,method:req.method(),body:req.postDataJSON(),headers:req.headers()})
  if(path.endsWith(`artifacts/${aid}/content`))return route.fulfill({contentType:'application/octet-stream',body:'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="90"><rect width="320" height="90" fill="#e7f1ed"/><text x="20" y="50">BERVEL CLI</text></svg>'})
  if(path.endsWith(`artifacts/${aid}`))return route.fulfill({json:{artifact_id:aid,filename:'native.svg',media_type:imageMedia,size:160,created_at:time}})
  if(path==='/api/v0.1/chat/sessions')return route.fulfill({json:{sessions:[],next_offset:null}})
  if(path==='/api/v0.1/inspection/sessions'){if(messageSent&&pauseReadAfterSend)return;return route.fulfill({json:{sessions:[{session_id:sid,created_at:time,title:session.turns[0]?.question||'Новый диалог',turn_count:session.turns.length,has_pending:false}],next_offset:null}})}
  if(path===`/api/v0.1/inspection/sessions/${sid}`){if(messageSent&&pauseReadAfterSend)return;return route.fulfill({json:session})}
  if(path.endsWith('/cancel')&&req.method()==='POST'){session.turns[0]={...session.turns[0],state:'blocked',cancel_requested:true,error:null,progress_text:'Остановлено по запросу пользователя.'};return route.fulfill({json:session.turns[0]})}
  if(path.endsWith('/messages')&&req.method()==='POST'){const turn={state:'completed',created_at:time,sources:[],results:[],actions:[],error:null,...session.turns[0],question:cleanQuestion??req.postDataJSON().content,answer:'Получен исходный запрос.',turn_id:'66666666-6666-4666-8666-666666666666'};session.turns.push(turn);messageSent=true;return route.fulfill({json:turn})}
  return route.fulfill({json:{artifacts:[],next_cursor:null}})
 })
})
afterEach(async()=>{try{assert.deepEqual(errors,[])}finally{await context.close()}})
async function open(){await page.goto(base+'/#/inspection');await page.getByRole('article',{name:'Ответ Codex',exact:true}).waitFor()}
test('native inspection submits more than4000characters with literal whitespace preserved',async()=>{
 await open();const text='  Начало\n'+('точный запрос '.repeat(400))+'\n  Конец  ',box=page.getByRole('textbox',{name:'Сообщение',exact:true});await box.fill(text);assert.equal(await box.inputValue(),text);assert.equal(await box.getAttribute('maxlength'),null);await page.getByRole('button',{name:'Отправить',exact:true}).click();await page.getByText('Получен исходный запрос.',{exact:true}).waitFor();assert.equal(requests.find(x=>x.method==='POST'&&x.path.endsWith('/messages')).body.content,text)
})
test('native optimistic sidebar title uses full server-masked question before120character clipping',async()=>{
 const prefix='a'.repeat(100)+' token=',secret='private-native-secret-that-crosses-the-title-boundary-0123456789',raw=prefix+secret+' trailing';cleanQuestion=prefix+'[REDACTED] trailing';pauseReadAfterSend=true;session.turns=[];await page.goto(base+'/#/inspection');await page.getByRole('heading',{name:'Что проверить?',exact:true}).waitFor();await page.getByRole('textbox',{name:'Сообщение',exact:true}).fill(raw);await page.getByRole('button',{name:'Отправить',exact:true}).click();await page.getByRole('button',{name:cleanQuestion.slice(0,120),exact:true}).waitFor();assert.equal(requests.find(x=>x.method==='POST'&&x.path.endsWith('/messages')).body.content,raw);assert.ok(!(await page.locator('.chat-history').innerText()).includes('private-native'));assert.ok(!(await page.locator('.chat-history').innerText()).includes(secret.slice(0,13)))
})
test('native session identifies CLI and displays commentary without legacy workflow or action widgets',async()=>{
 session.turns[0]={...session.turns[0],state:'running',answer:null,progress_text:'Читаю конкретный модуль и исправляю обработчик.',actions:[{action_id:'cmd-1',operation:'command.run',state:'completed',result:null,error:null},{action_id:'change-1',operation:'code.change',state:'completed',result:null,error:null}],workflow:{phase:'research',updated_at:time,reason:null,research_rounds:0,max_research_rounds:null,web_searches:0,max_web_searches:2,elapsed_seconds:5,max_research_seconds:null,history:[]}}
 await open();await page.getByText('Codex CLI',{exact:true}).waitFor();await page.getByText('Читаю конкретный модуль и исправляю обработчик.',{exact:true}).waitFor();assert.equal(await page.getByText('Исследуем источники',{exact:true}).count(),0);assert.equal(await page.getByText('Действия (2)',{exact:true}).count(),0);await page.getByRole('button',{name:'Остановить',exact:true}).waitFor()
})
for(const state of ['blocked','failed'])test(`native terminal ${state} shows explicit uncertainty error instead of stale progress only`,async()=>{
 session.turns[0]={...session.turns[0],state,answer:null,error:'Связь с CLI потеряна. Исход выполнения неизвестен.',progress_text:'Начинаю изменение файла.'};await open();await page.getByRole('alert').filter({hasText:'Связь с CLI потеряна. Исход выполнения неизвестен.'}).waitFor();await page.getByText('Последнее сообщение CLI:',{exact:true}).waitFor();assert.equal(await page.getByRole('heading',{name:'Готово',exact:true}).count(),0);assert.equal(await page.getByText('CLI завершил запрос.',{exact:true}).count(),0)
})
test('native unknown outcome reason remains visible when terminal error field is absent',async()=>{
 session.turns[0]={...session.turns[0],state:'blocked',answer:null,error:null,progress_text:'Последняя команда запущена.',workflow:{phase:'blocked',updated_at:time,reason:'Исход команды неизвестен; результат требует проверки.',research_rounds:0,max_research_rounds:null,web_searches:0,max_web_searches:2,elapsed_seconds:5,max_research_seconds:null,history:[]}};await open();await page.getByRole('alert').filter({hasText:'Исход команды неизвестен; результат требует проверки.'}).waitFor();assert.equal(await page.getByText('Исследуем источники',{exact:true}).count(),0)
})
test('native recoverable transport progress keeps Stop available through reload and cancel',async()=>{
 session.turns[0]={...session.turns[0],state:'running',answer:null,error:null,progress_text:'Связь с CLI временно недоступна. Проверяем состояние; команда может продолжаться.'};await open();await page.getByText('Связь с CLI временно недоступна. Проверяем состояние; команда может продолжаться.',{exact:true}).waitFor();assert.ok(await page.getByRole('button',{name:'Остановить',exact:true}).isEnabled());await page.reload();await page.getByRole('button',{name:'Остановить',exact:true}).click();await page.getByText('Остановлено по запросу пользователя.',{exact:true}).waitFor();const commands=requests.filter(x=>x.method==='POST');assert.equal(commands.length,1);assert.ok(commands[0].path.endsWith('/cancel'));assert.equal(await page.getByRole('button',{name:'Остановить',exact:true}).count(),0)
})
test('native sessions retain old proposal history as read-only without reads or confirmation requests',async()=>{
 session.turns[0].results=[{kind:'code_proposal',proposal_id:pid,session_id:sid,title:'Старый патч',explanation:'Сохранённая история.',fingerprint:'a'.repeat(64),status:'pending',diff:'--- a/example.py\n+++ b/example.py\n-old\n+new',files:['example.py']}];await open();await page.getByRole('heading',{name:'Старый патч',exact:true}).waitFor();await page.getByText('Просмотреть точный патч',{exact:true}).click();assert.equal(await page.getByRole('button',{name:/Применить патч|Отклонить патч/}).count(),0);assert.equal(await page.getByRole('checkbox').count(),0);await page.getByText(/Архив предложения/).waitFor();assert.equal(requests.filter(x=>x.path.includes('/proposals/')).length,0)
})
test('new native hint describes direct CLI edits only when the API confirms engine',async()=>{
 session.turns=[];await page.goto(base+'/#/inspection');await page.getByText(/Codex CLI выполняет команды и изменяет файлы напрямую/).waitFor();assert.equal(await page.getByText(/после вашего подтверждения/).count(),0)
})
test('ordinary assistant retains its4000character input limit',async()=>{
 await page.goto(base+'/#/chat');const box=page.getByRole('textbox',{name:'Сообщение',exact:true});await box.fill('x'.repeat(5000));assert.equal(await box.getAttribute('maxlength'),'4000');assert.equal((await box.inputValue()).length,4000)
})
test('octet-stream metadata never permits HTML as an inline image',async()=>{
 imageMedia='text/html';session.turns[0].answer=`![Недопустимое изображение](/api/v0.1/artifacts/${aid}/content)`;await open();await page.getByRole('alert').filter({hasText:/Не удалось показать изображение/}).waitFor();assert.equal(await page.getByRole('img').count(),0)
})
test('native advertised preview URL resolves only its artifact metadata and canonical octet-stream content',async()=>{
 session.turns[0].answer=`![Схема из preview](/api/v0.1/artifacts/${aid}/preview)`;await open();await page.getByRole('img',{name:'Схема из preview',exact:true}).waitFor();await page.waitForFunction(()=>document.querySelector('img[alt="Схема из preview"]')?.naturalWidth>0);assert.equal(requests.filter(x=>x.path.endsWith(`/artifacts/${aid}/preview`)).length,0);assert.equal(requests.find(x=>x.path.endsWith(`/artifacts/${aid}`)).headers['x-bervel-web'],'1');assert.equal(requests.find(x=>x.path.endsWith(`/artifacts/${aid}/content`)).headers['x-bervel-web'],'1');assert.equal(await page.getByText(/Изображение недоступно: разрешены только файлы BERVEL/).count(),0)
})
for(const width of [1440,390])test(`native exported SVG uses real octet-stream transport and trusted metadata at${width}`,async()=>{
 await page.setViewportSize({width,height:900});session.turns[0].answer=`## Результат CLI\n\n| Проверка | Итог |\n| --- | --- |\n| SVG | Создан |\n\n![Схема CLI](/api/v0.1/artifacts/${aid}/content)\n\n[Скачать схему](/api/v0.1/artifacts/${aid}/content "Описание файла")`;await open();await page.waitForFunction(()=>document.querySelector('img[alt="Схема CLI"]')?.naturalWidth>0);assert.equal(requests.find(x=>x.path.endsWith(`/artifacts/${aid}`)).headers['x-bervel-web'],'1');assert.equal(requests.find(x=>x.path.endsWith('/content')).headers['x-bervel-web'],'1');const downloaded=page.waitForEvent('download');await page.getByRole('link',{name:'Скачать схему',exact:true}).click();assert.equal((await downloaded).suggestedFilename(),'native.svg');assert.equal(new URL(page.url()).hash,'#/inspection');assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:resolve(out,`native-${width}.png`),fullPage:true})
})
