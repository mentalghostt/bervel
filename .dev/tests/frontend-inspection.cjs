const assert=require('node:assert/strict')
const {test,before,after,beforeEach,afterEach}=require('node:test')
const {chromium}=require('playwright')
const {mkdirSync}=require('node:fs')
const {resolve}=require('node:path')
const base=process.env.BERVEL_FRONTEND_URL||'http://host.docker.internal:5173'
const out=resolve(process.env.BERVEL_TEST_ARTIFACT_ROOT||resolve(__dirname,'../local/inspection-2026-10-29/screenshots'))
const ids={assistant:'11111111-1111-4111-8111-111111111111',inspection:'22222222-2222-4222-8222-222222222222'}
const time='2026-10-29T09:00:00Z'
const turn=(question,answer='Прочитан src/bervel/app_setup.py, строки 1–10.')=>({turn_id:'33333333-3333-4333-8333-333333333333',question,answer,state:'completed',created_at:time,sources:[],results:[],actions:[],error:null})
let browser,page,context,requests,sessions,errors
before(async()=>{mkdirSync(out,{recursive:true});browser=await chromium.launch({executablePath:process.env.BERVEL_BROWSER_PATH,headless:true})})
after(async()=>browser?.close())
beforeEach(async()=>{
 context=await browser.newContext({viewport:{width:1440,height:900}});page=await context.newPage();page.setDefaultTimeout(5000)
 requests=[];errors=[];sessions={assistant:{session_id:ids.assistant,created_at:time,turns:[turn('Обычный диалог')]},inspection:{session_id:ids.inspection,created_at:time,turns:[turn('Диалог проверки')]}}
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v0.1/**',async route=>{
  const request=route.request(),url=new URL(request.url()),match=url.pathname.match(/^\/api\/v0\.1\/(chat|inspection)\/sessions(?:\/([^/]+))?(\/messages)?$/)
  if(!match)return route.fulfill({json:{artifacts:[],next_cursor:null}})
  requests.push({path:url.pathname,method:request.method()});const mode=match[1]==='chat'?'assistant':'inspection'
  const current=sessions[mode]
  if(request.method()==='GET'&&!match[2])return route.fulfill({json:{sessions:current?[{session_id:current.session_id,created_at:time,title:current.turns[0]?.question||'Новый диалог',turn_count:current.turns.length,has_pending:false}]:[],next_offset:null}})
  if(request.method()==='POST'&&!match[2]){sessions[mode]={session_id:ids[mode],created_at:time,turns:[]};return route.fulfill({json:sessions[mode]})}
  if(request.method()==='POST'&&match[3]){
   const value=turn(request.postDataJSON().content,'Обращение создано по результатам проверки.')
   value.results=[{kind:'ticket',ticket_id:'44444444-4444-4444-8444-444444444444',title:'Замечание проверки',status:'created'}]
   sessions[mode].turns.push(value);return route.fulfill({status:202,json:value})
  }
  if(request.method()==='DELETE'){sessions[mode]=null;return route.fulfill({json:{session_id:match[2],deleted:true}})}
  return route.fulfill({status:current?.session_id===match[2]?200:404,json:current||{error:{code:'NOT_FOUND'}}})
 })
})
afterEach(async()=>{try{assert.deepEqual(errors,[])}finally{await context.close()}})
async function open(){await page.goto(base+'/#/inspection');await page.getByRole('button',{name:'Диалог проверки',exact:true}).waitFor()}
test('inspection menu and list are separate from the normal assistant',async()=>{
 await open();assert.equal(await page.getByRole('link',{name:'Проверка',exact:true}).getAttribute('aria-current'),'page')
 assert.equal(await page.getByRole('button',{name:'Обычный диалог',exact:true}).count(),0)
 await page.getByRole('button',{name:'Диалог проверки',exact:true}).click();await page.getByText('Прочитан src/bervel/app_setup.py, строки 1–10.',{exact:true}).waitFor()
 assert.equal(requests.some(r=>r.path.includes('/chat/')),false)
})
test('switching sections preserves separate drafts selected IDs and histories',async()=>{
 await open();await page.getByRole('button',{name:'Диалог проверки',exact:true}).click();await page.getByRole('textbox',{name:'Сообщение',exact:true}).fill('Черновик проверки')
 await page.getByRole('link',{name:'Ассистент',exact:true}).click();await page.getByRole('button',{name:'Обычный диалог',exact:true}).click()
 await page.getByRole('textbox',{name:'Сообщение',exact:true}).fill('Черновик ассистента')
 await page.getByRole('link',{name:'Проверка',exact:true}).click()
 await page.getByRole('main',{name:'Чаты проверки',exact:true}).waitFor()
 assert.equal(await page.getByRole('textbox',{name:'Сообщение',exact:true}).inputValue(),'Черновик проверки')
 const storage=await page.evaluate(()=>({normal:localStorage.getItem('bervel.chat.session_id'),review:sessionStorage.getItem('bervel.inspection.session_id')}))
 assert.deepEqual(storage,{normal:ids.assistant,review:ids.inspection})
})
test('new inspection chat sends only to inspection and shows saved ticket link',async()=>{
 sessions.inspection=null;await page.goto(base+'/#/inspection');await page.getByRole('heading',{name:'Что проверить?',exact:true}).waitFor()
 await page.getByRole('textbox',{name:'Сообщение',exact:true}).fill('Исследуй и создай обращение')
 await page.getByRole('button',{name:'Отправить',exact:true}).click()
 try{await page.getByRole('link',{name:'Открыть обращение',exact:true}).waitFor()}catch(error){throw Error(JSON.stringify({requests,text:await page.locator('main:visible').innerText()})+' '+error.message)}
 assert.ok(requests.some(r=>r.method==='POST'&&r.path==='/api/v0.1/inspection/sessions'))
 assert.ok(requests.some(r=>r.method==='POST'&&r.path.endsWith('/messages')))
 assert.equal(requests.some(r=>r.path.includes('/chat/')),false)
})
for(const width of [1440,390])test(`inspection layout keeps input visible at ${width}`,async()=>{
 await page.setViewportSize({width,height:850});await open();await page.getByRole('button',{name:'Диалог проверки',exact:true}).click()
 const input=page.getByRole('textbox',{name:'Сообщение',exact:true});await input.waitFor()
 const bounds=await input.boundingBox();assert.ok(bounds.y+bounds.height<=850)
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1))
 await page.screenshot({path:resolve(out,`inspection-${width}.png`),fullPage:true})
})
