const assert=require('node:assert/strict')
const {test,before,after,beforeEach,afterEach}=require('node:test')
const {mkdirSync}=require('node:fs')
const {resolve}=require('node:path')
const {chromium}=require('playwright')
const base=process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1:5173'
const directory=resolve(__dirname,'../local/chat-workspace-2026-10-28/screenshots')
const ids=['11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222']
const timestamp='2026-10-28T08:00:00Z'
const message=(n,question='Вопрос '+n,state='completed')=>({turn_id:`33333333-3333-4333-8333-${String(n).padStart(12,'0')}`,question,answer:state==='completed'?'Ответ '+n+' — подробности '.repeat(35):null,state,created_at:timestamp,sources:[],results:[],actions:[],error:null})
let browser,page,context,sessions,requests,errors,deleteUncertain,listPending
before(async()=>{mkdirSync(directory,{recursive:true});browser=await chromium.launch({headless:true})})
after(async()=>browser?.close())
beforeEach(async()=>{
 context=await browser.newContext({viewport:{width:1440,height:900}});page=await context.newPage();page.setDefaultTimeout(4000)
 sessions=[{session_id:ids[0],created_at:timestamp,turns:[message(1,'Первый диалог')]},{session_id:ids[1],created_at:'2026-10-27T08:00:00Z',turns:[message(2,'Второй диалог')]}]
 requests=[];errors=[];deleteUncertain=false;listPending=false
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('/api/'))requests.push(r)})
 await page.addInitScript(id=>localStorage.setItem('bervel.chat.session_id',id),ids[0])
 await page.route('**/api/v0.1/**',async route=>{
  const r=route.request(),url=new URL(r.url()),id=url.pathname.split('/')[5],session=sessions.find(s=>s.session_id===id)
  if(url.pathname==='/api/v0.1/chat/sessions'&&r.method()==='GET')return route.fulfill({json:{sessions:sessions.map(s=>({session_id:s.session_id,created_at:s.created_at,title:s.turns[0]?.question||'Новый диалог',turn_count:s.turns.length,has_pending:listPending||s.turns.some(t=>['queued','running'].includes(t.state))})),next_offset:null}})
  if(r.method()==='DELETE'){
   if(session?.turns.some(t=>['queued','running'].includes(t.state)))return route.fulfill({status:409,json:{error:{code:'CONFLICT',message:'Busy'}}})
   sessions=sessions.filter(s=>s.session_id!==id)
   if(deleteUncertain){deleteUncertain=false;return route.abort('failed')}
   return route.fulfill({json:{session_id:id,deleted:true}})
  }
  if(url.pathname.includes('/chat/sessions/'))return route.fulfill({status:session?200:404,json:session||{error:{code:'NOT_FOUND'}}})
  return route.fulfill({json:{artifacts:[],next_cursor:null}})
 })
})
afterEach(async()=>{try{assert.deepEqual(errors,[])}finally{await context.close()}})
async function open(){await page.goto(base+'/#/chat');await page.getByRole('textbox',{name:'Сообщение',exact:true}).waitFor();await page.getByRole('button',{name:'Первый диалог',exact:true}).waitFor()}
const card=name=>page.getByRole('listitem').filter({has:page.getByRole('button',{name,exact:true})})
test('server chats can be selected and deleting another chat preserves current transcript and draft',async()=>{
 await open();await page.getByRole('textbox',{name:'Сообщение',exact:true}).fill('Мой черновик')
 await card('Второй диалог').getByRole('button',{name:'Удалить',exact:true}).click()
 await page.getByRole('button',{name:'Второй диалог',exact:true}).waitFor({state:'detached'})
 assert.equal(await page.getByRole('textbox',{name:'Сообщение',exact:true}).inputValue(),'Мой черновик')
 assert.equal(await page.getByRole('article',{name:'Сообщение пользователя'}).textContent(),'ВыПервый диалог')
 assert.equal(requests.filter(r=>r.method()==='DELETE').length,1)
 assert.equal(new URL(requests.find(r=>r.method()==='DELETE').url()).pathname,'/api/v0.1/chat/sessions/'+ids[1])
})
test('switching chats keeps drafts separated and restores exact selected history',async()=>{
 await open();await page.getByRole('textbox',{name:'Сообщение',exact:true}).fill('Черновик первого')
 await page.getByRole('button',{name:'Второй диалог',exact:true}).click();await page.getByRole('article',{name:'Сообщение пользователя'}).filter({hasText:'Второй диалог'}).waitFor()
 assert.equal(await page.getByRole('textbox',{name:'Сообщение',exact:true}).inputValue(),'')
 await page.getByRole('textbox',{name:'Сообщение',exact:true}).fill('Черновик второго')
 await page.getByRole('button',{name:'Первый диалог',exact:true}).click();await page.getByRole('article',{name:'Сообщение пользователя'}).filter({hasText:'Первый диалог'}).waitFor()
 assert.equal(await page.getByRole('textbox',{name:'Сообщение',exact:true}).inputValue(),'Черновик первого')
})
test('deleting selected chat clears correspondence and uncertain retry keeps its command key',async()=>{
 await open();deleteUncertain=true
 await card('Первый диалог').getByRole('button',{name:'Удалить',exact:true}).click();await page.getByRole('alert').waitFor()
 await card('Первый диалог').getByRole('button',{name:'Удалить',exact:true}).click();await page.getByRole('button',{name:'Первый диалог',exact:true}).waitFor({state:'detached'})
 const deletes=requests.filter(r=>r.method()==='DELETE');assert.equal(deletes.length,2)
 const key=new URL(deletes[0].url()).searchParams.get('command_key');assert.ok(key);assert.equal(new URL(deletes[1].url()).searchParams.get('command_key'),key)
 assert.equal(await page.getByRole('article',{name:'Сообщение пользователя'}).count(),0)
 assert.equal(await page.evaluate(()=>localStorage.getItem('bervel.chat.session_id')),null)
})
test('pending session cannot be deleted',async()=>{
 sessions[0].turns=[message(1,'Первый диалог','running')];await open()
 assert.equal(await card('Первый диалог').getByRole('button',{name:'Удалить',exact:true}).isDisabled(),true)
 assert.equal(await card('Второй диалог').getByRole('button',{name:'Удалить',exact:true}).isEnabled(),true)
})
for(const width of [1440,390])test(`only transcript scrolls and input icon controls stay visible at ${width}px`,async()=>{
 sessions[0].turns=Array.from({length:35},(_,n)=>message(n+1,n?'Вопрос '+n:'Первый диалог'))
 await page.setViewportSize({width,height:850});await open()
 assert.equal(await page.getByRole('heading',{name:'Универсальный ассистент',exact:true}).count(),0)
 const input=page.getByRole('textbox',{name:'Сообщение',exact:true}),send=page.getByRole('button',{name:'Отправить',exact:true}),voice=page.getByRole('button',{name:'Записать голосовое сообщение',exact:true})
 assert.equal(await send.locator('svg').count(),1);assert.equal(await voice.locator('svg').count(),1)
 const bounds=await page.evaluate(()=>{const box=s=>document.querySelector(s).getBoundingClientRect().toJSON();return{page:document.documentElement.scrollHeight,width:document.documentElement.scrollWidth,viewport:innerHeight,composer:box('.chat-composer'),field:box('.composer-field'),transcript:box('.chat-transcript'),scroll:document.querySelector('.chat-transcript').scrollHeight}})
 assert.ok(bounds.page<=bounds.viewport+1,JSON.stringify(bounds));assert.ok(bounds.width<=width+1)
 assert.ok(bounds.scroll>bounds.transcript.height)
 const start=await input.boundingBox();await page.locator('.chat-transcript').evaluate(e=>e.scrollTop=0);await page.mouse.wheel(0,1000)
 const end=await input.boundingBox();assert.equal(Math.round(start.y),Math.round(end.y));assert.ok(end.y+end.height<=850)
 for(const control of [send,voice]){const b=await control.boundingBox();assert.ok(b.x>=bounds.field.x&&b.y>=bounds.field.y);assert.ok(b.x+b.width<=bounds.field.right+1&&b.y+b.height<=bounds.field.bottom+1)}
 await page.screenshot({path:resolve(directory,`chat-${width}.png`),fullPage:true})
})
