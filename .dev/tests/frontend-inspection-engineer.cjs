const assert = require('node:assert/strict')
const {test,before,after,beforeEach,afterEach} = require('node:test')
const {chromium} = require('playwright')
const {mkdirSync} = require('node:fs')
const {resolve} = require('node:path')
const base = process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5173'
const out = resolve(__dirname,'../local/inspection-engineer-2026-10-30/ui')
const sid='22222222-2222-4222-8222-222222222222',tid='33333333-3333-4333-8333-333333333333',aid='44444444-4444-4444-8444-444444444444',pid='55555555-5555-4555-8555-555555555555',time='2026-10-30T10:00:00Z'
let browser,context,page,session,proposal,requests,errors,confirmFailure,imageFailure,cancelFailure
const card=()=>({kind:'code_proposal',proposal_id:pid,session_id:sid,title:'Исправить заголовок',explanation:'Уточнить текст интерфейса.',fingerprint:'a'.repeat(64),status:'pending',diff:'--- a/frontend/src/title.ts\n+++ b/frontend/src/title.ts\n@@ -1 +1 @@\n-old\n+new',files:['frontend/src/title.ts']})
before(async()=>{mkdirSync(out,{recursive:true});browser=await chromium.launch({headless:true})})
after(async()=>browser?.close())
beforeEach(async()=>{
 context=await browser.newContext({viewport:{width:1440,height:1000}})
 await context.addInitScript(id=>localStorage.setItem('bervel.inspection.session_id',id),sid)
 page=await context.newPage();page.setDefaultTimeout(4500)
 errors=[];requests=[];confirmFailure=null;imageFailure=false;cancelFailure=false;proposal=card()
 session={session_id:sid,created_at:time,turns:[{turn_id:tid,question:'Проверь систему',answer:'Готово',state:'completed',created_at:time,sources:[],results:[],actions:[],error:null}]}
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/v0.1/**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname
  requests.push({path,method:req.method(),body:req.postDataJSON(),headers:req.headers()})
  if(path===`/api/v0.1/artifacts/${aid}/content`)return route.fulfill(imageFailure?{status:410,body:''}:{contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="90"><rect width="320" height="90" fill="#e7f1ed"/><text x="20" y="50">BERVEL</text></svg>'})
  if(path===`/api/v0.1/artifacts/${aid}`)return route.fulfill({json:{artifact_id:aid,filename:'diagram.svg',media_type:'image/svg+xml',size:160,created_at:time}})
  if(path===`/api/v0.1/inspection/proposals/${pid}`)return route.fulfill({json:proposal})
  if(path===`/api/v0.1/inspection/proposals/${pid}/confirm`||path===`/api/v0.1/inspection/proposals/${pid}/reject`){
   if(confirmFailure==='network'){confirmFailure=null;return route.abort()}
   if(confirmFailure==='stale')return route.fulfill({status:409,json:{error:{code:'STALE_BASELINE'}}})
   proposal={...proposal,status:path.endsWith('/confirm')?'applied':'rejected'};return route.fulfill({json:proposal})
  }
  if(path.endsWith('/cancel')){
   if(cancelFailure){cancelFailure=false;return route.abort()}
   session.turns[0]={...session.turns[0],state:'blocked',answer:'Остановлено пользователем.'};return route.fulfill({json:session.turns[0]})
  }
  if(path==='/api/v0.1/inspection/sessions')return route.fulfill({json:{sessions:[{session_id:sid,created_at:time,title:'Проверь систему',turn_count:1,has_pending:session.turns[0].state==='running'}],next_offset:null}})
  if(path===`/api/v0.1/inspection/sessions/${sid}`)return route.fulfill({json:session})
  return route.fulfill({json:{artifacts:[],next_cursor:null}})
 })
})
afterEach(async()=>{try{assert.deepEqual(errors,[])}finally{await context.close()}})
const open=async()=>{await page.goto(base+'/#/inspection');await page.getByRole('article',{name:'Ответ Codex',exact:true}).waitFor()}
test('Markdown safely renders headings tables footnotes code and local artifacts only',async()=>{
 session.turns[0].answer=`## Диагностика\n\n**Работает** и *проверено*.\n\n- Первый шаг\n- Второй шаг\n\n| Проверка | Итог |\n| --- | --- |\n| API | OK |\n\nИсточник[^1].\n\n[^1]: Подтверждено тестом.\n\n\`\`\`ts\nconst result = 1\n\`\`\`\n\n[Опасная ссылка](javascript:alert(1))\n\n<script>window.injected=1</script>\n\n![Схема](/api/v0.1/artifacts/${aid}/content)\n\n![Запрещено](https://evil.example/track.svg)`
 await open();await page.getByRole('heading',{name:'Диагностика',exact:true}).waitFor();await page.getByRole('table').waitFor()
 assert.equal(await page.locator('strong').filter({hasText:'Работает'}).count(),1)
 assert.equal(await page.locator('[data-footnotes]').count(),1)
 assert.equal(await page.locator('pre code').innerText(),'const result = 1\n')
 await page.getByRole('img',{name:'Схема',exact:true}).waitFor();await page.waitForFunction(()=>document.querySelector('img[alt="Схема"]')?.naturalWidth>0)
 assert.equal(await page.evaluate(()=>window.injected),undefined)
 assert.equal(await page.locator('a[href^="javascript"],script:not([src]),img[src^="https://evil"]').count(),0)
 assert.ok(requests.find(r=>r.path.endsWith('/content')).headers['x-bervel-web']==='1')
})
test('artifact image failures remain readable',async()=>{
 imageFailure=true;session.turns[0].results=[{kind:'file',artifact_id:aid,filename:'diagram.svg',media_type:'image/svg+xml',download_url:`/api/v0.1/artifacts/${aid}/content`,preview_url:''}]
 await open();await page.getByText(/Не удалось показать изображение/).waitFor();await page.getByRole('button',{name:'Скачать файл',exact:true}).waitFor()
})
test('SVG file result displays inline and revokes its object URL after closing chat',async()=>{
 await context.addInitScript(()=>{window.revokedImages=[];const revoke=URL.revokeObjectURL.bind(URL);URL.revokeObjectURL=url=>{window.revokedImages.push(url);revoke(url)}})
 session.turns[0].results=[{kind:'file',artifact_id:aid,filename:'diagram.svg',media_type:'image/svg+xml',download_url:`/api/v0.1/artifacts/${aid}/content`,preview_url:''}]
 await open();await page.getByRole('img',{name:'diagram.svg',exact:true}).waitFor();await page.waitForFunction(()=>document.querySelector('img[alt="diagram.svg"]')?.naturalWidth>0);const src=await page.getByRole('img',{name:'diagram.svg',exact:true}).getAttribute('src');assert.ok(src.startsWith('blob:'));await page.getByRole('button',{name:'Новый диалог',exact:true}).click();assert.ok(await page.evaluate(src=>window.revokedImages.includes(src),src))
})
test('footnote navigation stays within Inspection instead of changing the application route',async()=>{
 session.turns[0].answer='Проверено[^1].\n\n[^1]: Источник проверки.';await open();await page.locator('[data-footnote-ref]').click();assert.ok(await page.getByRole('main',{name:'Чаты проверки',exact:true}).isVisible());assert.equal(new URL(page.url()).hash,'#/inspection');await page.locator('[data-footnote-backref]').click();assert.equal(new URL(page.url()).hash,'#/inspection')
})
for(const absolute of [false,true])test(`Markdown document link authenticates Blob download without leaving Inspection (${absolute?'absolute':'relative'})`,async()=>{
 const filename='Отчёт-проверки.svg',url=`${absolute?base:''}/api/v0.1/artifacts/${aid}/content`
 session.turns[0].answer=`[${filename}](${url})`;await open();const original=page.url(),downloaded=page.waitForEvent('download',{timeout:4500});await page.getByRole('link',{name:filename,exact:true}).click();const download=await downloaded
 assert.equal(download.suggestedFilename(),filename);assert.ok(download.url().startsWith('blob:'));assert.equal(page.url(),original);assert.ok(await page.getByRole('main',{name:'Чаты проверки',exact:true}).isVisible());assert.equal(requests.find(r=>r.path.endsWith('/content')).headers['x-bervel-web'],'1');assert.ok(await page.getByRole('link',{name:filename,exact:true}).isVisible())
})
test('Markdown document download failure is readable and retry does not navigate',async()=>{
 imageFailure=true;session.turns[0].answer=`[Скачать документ](/api/v0.1/artifacts/${aid}/content "Отчёт-проверки.svg")`;await open();const original=page.url();await page.getByRole('link',{name:'Скачать документ',exact:true}).click();await page.getByRole('alert').filter({hasText:/Не удалось скачать файл/}).waitFor();assert.equal(page.url(),original);assert.ok(await page.getByRole('main',{name:'Чаты проверки',exact:true}).isVisible());imageFailure=false;const downloaded=page.waitForEvent('download',{timeout:4500});await page.getByRole('link',{name:'Скачать документ',exact:true}).click();assert.equal((await downloaded).suggestedFilename(),'Отчёт-проверки.svg');assert.equal(page.url(),original)
})
test('Markdown ordinary external links keep their original navigation',async()=>{
 session.turns[0].answer='[Документация](https://docs.example/manual)';await page.route('https://docs.example/manual',route=>route.fulfill({contentType:'text/html',body:'<h1>Документация</h1>'}));await open();await page.getByRole('link',{name:'Документация',exact:true}).click();assert.equal(page.url(),'https://docs.example/manual')
})
test('test receipts show real summary and explicit log without raw JSON',async()=>{
 session.turns[0].results=[{kind:'inspection_test',suite:'backend',state:'failed',exit_code:1,summary:'Один тест упал.',output:'FAILED test_api: expected 200'}]
 await open();await page.getByText('Один тест упал.',{exact:true}).waitFor();await page.getByText('Вывод тестов',{exact:true}).click();await page.getByText('FAILED test_api: expected 200',{exact:true}).waitFor()
})
test('exact patch confirmation is gated by review and survives saved history reload',async()=>{
 session.turns[0].results=[card()];await open();await page.getByRole('button',{name:'Применить патч',exact:true,disabled:true}).waitFor()
 await page.getByText('Просмотреть точный патч',{exact:true}).click();await page.getByText('Я проверил этот diff и подтверждаю изменение файлов.',{exact:true}).click()
 await page.getByRole('button',{name:'Применить патч',exact:true}).click();await page.getByText('Патч применён',{exact:true}).waitFor()
 const command=requests.find(r=>r.path.endsWith('/confirm'));assert.equal(command.body.fingerprint,card().fingerprint);assert.ok(command.body.command_key)
 await page.reload();await page.getByText('Патч применён',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Применить патч',exact:true}).count(),0)
})
test('reject does not confirm; stale patch requires a new proposal',async()=>{
 session.turns[0].results=[card()];await open();await page.getByRole('button',{name:'Отклонить патч',exact:true}).click();await page.getByText('Патч отклонён',{exact:true}).waitFor();assert.equal(requests.filter(r=>r.path.endsWith('/confirm')).length,0)
})
test('stale confirmation does not silently replace approved patch',async()=>{
 confirmFailure='stale';session.turns[0].results=[card()];await open();await page.getByText('Просмотреть точный патч',{exact:true}).click();await page.getByText('Я проверил этот diff и подтверждаю изменение файлов.',{exact:true}).click();await page.getByRole('button',{name:'Применить патч',exact:true}).click();await page.getByText(/Создайте новое предложение/).waitFor();assert.equal(await page.getByRole('button',{name:'Применить патч',exact:true}).count(),0)
})
test('uncertain confirmation retries the same command key and blocks rejection',async()=>{
 confirmFailure='network';session.turns[0].results=[card()];await open();await page.getByText('Просмотреть точный патч',{exact:true}).click();await page.getByText('Я проверил этот diff и подтверждаю изменение файлов.',{exact:true}).click();await page.getByRole('button',{name:'Применить патч',exact:true}).click();await page.getByRole('button',{name:'Повторить подтверждение',exact:true}).waitFor();assert.ok(await page.getByRole('button',{name:'Отклонить патч',exact:true}).isDisabled());await page.getByRole('button',{name:'Повторить подтверждение',exact:true}).click();await page.getByText('Патч применён',{exact:true}).waitFor();const commands=requests.filter(r=>r.path.endsWith('/confirm'));assert.equal(commands.length,2);assert.equal(commands[0].body.command_key,commands[1].body.command_key)
})
test('uncertain confirmation can be retried after reload without a new decision key',async()=>{
 confirmFailure='network';session.turns[0].results=[card()];await open();await page.getByText('Просмотреть точный патч',{exact:true}).click();await page.getByText('Я проверил этот diff и подтверждаю изменение файлов.',{exact:true}).click();await page.getByRole('button',{name:'Применить патч',exact:true}).click();await page.getByRole('button',{name:'Повторить подтверждение',exact:true}).waitFor();await page.reload();await page.getByRole('button',{name:'Повторить подтверждение',exact:true}).click();await page.getByText('Патч применён',{exact:true}).waitFor();const commands=requests.filter(r=>r.path.endsWith('/confirm'));assert.equal(commands[0].body.command_key,commands[1].body.command_key)
})
test('unknown apply outcome offers status refresh without a new apply',async()=>{
 session.turns[0].results=[card()];proposal.status='unknown';await open();await page.getByText('Исход применения неизвестен',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Применить патч',exact:true}).count(),0);await page.getByRole('button',{name:'Обновить состояние патча',exact:true}).click();assert.equal(requests.filter(r=>r.method==='POST').length,0)
})
test('returning to inspection refreshes proposal status applied by another visitor',async()=>{
 session.turns[0].results=[card()];await open();await page.getByText('Ожидает подтверждения',{exact:true}).waitFor();await page.getByRole('link',{name:'Универсальный ассистент',exact:true}).click();proposal.status='applied';await page.getByRole('link',{name:'Проверка',exact:true}).click();await page.getByText('Патч применён',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Применить патч',exact:true}).count(),0)
})
test('stop with nullable budgets uses cancel only and retries the same key',async()=>{
 cancelFailure=true;session.turns[0]={...session.turns[0],state:'running',answer:null,workflow:{phase:'research',updated_at:time,reason:null,research_rounds:15,max_research_rounds:null,web_searches:0,max_web_searches:2,elapsed_seconds:481,max_research_seconds:null,history:[]}}
 await open();await page.getByText(/Прошло: 8 мин/).waitFor();await page.getByRole('button',{name:'Остановить',exact:true}).click();await page.getByRole('button',{name:'Повторить остановку',exact:true}).waitFor();await page.getByRole('button',{name:'Повторить остановку',exact:true}).click();await page.getByText('Остановлено пользователем.',{exact:true}).waitFor();const commands=requests.filter(r=>r.method==='POST');assert.equal(commands.length,2);assert.ok(commands.every(r=>r.path.endsWith('/cancel')));assert.equal(commands[0].body.command_key,commands[1].body.command_key)
})
for(const width of [1440,390])test(`inspection rich results remain contained at ${width}`,async()=>{
 await page.setViewportSize({width,height:900});session.turns[0].answer='## Проверка\n\n| Компонент | Статус |\n| --- | --- |\n| API | Готов |';session.turns[0].results=[card()];await open();await page.getByText('Просмотреть точный патч',{exact:true}).click();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));const box=await page.getByRole('textbox',{name:'Сообщение',exact:true}).boundingBox();assert.ok(box.y+box.height<=900);await page.screenshot({path:resolve(out,`engineer-${width}.png`),fullPage:true})
})
test('patch and table use readable contrast in the workspace palette',async()=>{
 session.turns[0].answer='| Компонент | Статус |\n| --- | --- |\n| API | Готов |';session.turns[0].results=[card()];await open();await page.getByText('Просмотреть точный патч',{exact:true}).click()
 for(const selector of ['.inspection-markdown th','.inspection-patch-review pre']){
  const contrast=await page.locator(selector).first().evaluate(el=>{
   const style=getComputedStyle(el),lum=color=>{const rgb=color.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4});return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722},fg=lum(style.color),bg=lum(style.backgroundColor);return (Math.max(fg,bg)+.05)/(Math.min(fg,bg)+.05)
  });assert.ok(contrast>=4.5,selector+' contrast '+contrast)
 }
})
