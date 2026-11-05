const {test,before,after}=require('node:test'),assert=require('node:assert/strict'),{chromium}=require('playwright')
const base=process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1:5173',stamp='2026-10-28T06:00:00Z'
const ids=['11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222','33333333-3333-4333-8333-333333333333']
const files=ids.map((artifact_id,i)=>({artifact_id,filename:['Прайс.txt','Склад.csv','Экономика.csv'][i],media_type:'text/plain',size:120,created_at:stamp,usage_role:['procurement','stock','economics'][i],processing_state:'ready',completed_chunks:2,total_chunks:2}))
let browser
before(async()=>{browser=await chromium.launch({headless:true})});after(async()=>{await browser?.close()})
async function open(width=1440){
 const page=await browser.newPage({viewport:{width,height:900}});page.setDefaultTimeout(3000)
 const requests=[],errors=[];page.on('request',r=>{if(r.url().includes('/api/'))requests.push(r)});page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/v0.1/**',route=>{const r=route.request(),p=new URL(r.url()).pathname
  if(p==='/api/v0.1/chat/sessions'&&r.method()==='GET')return route.fulfill({json:{sessions:[],next_offset:null}})
  if(p.endsWith('/artifacts'))return route.fulfill({json:{artifacts:files,next_cursor:null}})
  if(p.endsWith('/knowledge'))return route.fulfill({json:{state:'ready',error:null,total_chunks:2,completed_chunks:2}})
  if(p.endsWith('/preview'))return route.fulfill({json:{kind:'text',media_type:'text/plain',text:Array.from({length:90},(_,i)=>'Строка '+i).join('\n'),columns:[],rows:[],total_rows:0,next_cursor:null,notice:''}})
  if(ids.some(id=>p.endsWith('/artifacts/'+id)))return route.fulfill({json:files.find(file=>p.endsWith(file.artifact_id))})
  if(p.endsWith('/runs'))return route.fulfill({json:[]})
  if(p.endsWith('/transcription-settings'))return route.fulfill({json:{provider:'openai',language:'ru',model:'gpt-4o-mini-transcribe',key_configured:false}})
  return route.fulfill({json:{}})
 });return {page,requests,errors}
}
test('desktop files list stays narrow and fixed while long text expands the document',async()=>{const {page}=await open();try{
 await page.goto(base+'/#/files');await page.getByRole('button',{name:files[0].filename,exact:true}).click();await page.getByText('Строка 89',{exact:false}).waitFor()
 const list=page.getByRole('region',{name:'Список файлов',exact:true}),detail=page.getByRole('region',{name:'Просмотр файла',exact:true}),lb=await list.boundingBox(),db=await detail.boundingBox()
 assert.ok(lb.width<=320);assert.ok(db.height>lb.height+500);assert.ok(db.y===lb.y)
 assert.equal(await detail.evaluate(n=>getComputedStyle(n).overflowY),'visible');assert.equal(await detail.locator('pre').evaluate(n=>n.scrollHeight<=n.clientHeight),true)
 assert.ok(await page.evaluate(()=>document.documentElement.scrollHeight>innerHeight))
}finally{await page.close()}})
test('upload modal preserves uncertain command and selected role after closing',async()=>{const {page}=await open();const bodies=[];try{
 await page.route('**/api/v0.1/artifacts',route=>{if(route.request().method()==='POST'){bodies.push(route.request().postData());return bodies.length===1?route.abort('failed'):route.fulfill({json:{artifact_id:ids[0]}})}return route.fallback()})
 await page.goto(base+'/#/files');await page.getByRole('button',{name:'Загрузить файл',exact:true}).click();const dialog=page.getByRole('dialog',{name:'Загрузить файл',exact:true});await dialog.waitFor()
 await dialog.getByLabel('Файл',{exact:true}).setInputFiles({name:'new.csv',mimeType:'text/csv',buffer:Buffer.from('a,b\n1,2')});await dialog.getByLabel('Назначение файла',{exact:true}).selectOption('stock');await dialog.getByRole('button',{name:'Загрузить файл',exact:true}).click();await dialog.getByRole('alert').waitFor()
 await dialog.getByRole('button',{name:'Закрыть',exact:true}).click();await page.getByRole('button',{name:'Загрузить файл',exact:true}).click();assert.equal(await dialog.getByLabel('Назначение файла',{exact:true}).inputValue(),'stock');assert.equal(await dialog.getByLabel('Назначение файла',{exact:true}).isEnabled(),false)
 await dialog.getByRole('button',{name:'Повторить загрузку',exact:true}).click();await dialog.getByRole('status').waitFor();assert.equal(bodies.length,2)
 assert.equal(bodies[0].match(/name="command_key"\r\n\r\n([^\r]+)/)[1],bodies[1].match(/name="command_key"\r\n\r\n([^\r]+)/)[1])
}finally{await page.close()}})
test('deleting another card keeps selected preview and uses unique accessible dialogs',async()=>{const {page,requests,errors}=await open();try{
 await page.route('**/artifacts/'+ids[1],route=>route.fulfill({status:204}));await page.goto(base+'/#/files');await page.getByRole('button',{name:files[0].filename,exact:true}).click();await page.getByText('Строка 89',{exact:false}).waitFor()
 const before=requests.filter(r=>r.url().includes('/preview')).length;const card=page.locator('.file-card').filter({has:page.getByRole('button',{name:files[1].filename,exact:true})});await card.getByRole('button',{name:'Удалить навсегда',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Удалить навсегда',exact:true}).click();await page.getByRole('status').filter({hasText:'Файл удалён'}).waitFor()
 assert.equal(await page.getByRole('button',{name:files[0].filename,exact:true}).getAttribute('aria-pressed'),'true');assert.equal(requests.filter(r=>r.url().includes('/preview')).length,before);assert.equal(await page.locator('button button').count(),0)
 assert.equal(await page.evaluate(()=>{const ids=[...document.querySelectorAll('[id]')].map(n=>n.id);return ids.length===new Set(ids).size}),true);assert.deepEqual(errors,[])
}finally{await page.close()}})
for(const status of [200,403,404,410])test('exact file source route handles exact ID with status '+status,async()=>{const {page,requests}=await open();try{
 await page.route('**/artifacts/'+ids[0]+'',route=>route.fulfill({status,json:status===200?{...files[0],filename:'Исторический.txt'}:{error:{code:'ERROR'}}}))
 await page.goto(base+'/#/files?artifact_id='+ids[0])
 if(status===200){await page.getByRole('heading',{name:'Исторический.txt',exact:true}).waitFor();await page.getByText('Строка 89',{exact:false}).waitFor();assert.ok(requests.some(r=>r.url().includes('/'+ids[0]+'/preview')));assert.equal(requests.some(r=>r.url().includes('/'+ids[1]+'/preview')),false)}
 else {await page.getByRole('region',{name:'Просмотр файла',exact:true}).getByRole('alert').waitFor();assert.equal(requests.some(r=>r.url().includes('/preview')),false);if(status===410)await page.getByRole('alert').filter({hasText:'Файл удалён'}).waitFor()}
}finally{await page.close()}})
test('legacy source chunks become one exact link per file ID without excerpts',async()=>{const {page}=await open();try{
 const source={...files[0],row_start:1,row_end:1,total_rows:11,excerpt:'Не раскрывать фрагмент',truncated:true}
 await page.addInitScript(id=>localStorage.setItem('bervel.chat.session_id',id),ids[0]);await page.route('**/chat/sessions/'+ids[0],route=>route.fulfill({json:{session_id:ids[0],created_at:stamp,turns:[{turn_id:ids[1],question:'Вопрос',answer:'Ответ',state:'completed',created_at:stamp,sources:[...Array(11).fill(source),{...source,artifact_id:ids[1]}],actions:[],results:[],error:null}]}}))
 await page.goto(base+'/#/chat');const links=page.locator('a[href^="#/files?artifact_id="]');await links.first().waitFor();assert.equal(await links.count(),2);assert.equal(await links.first().getAttribute('href'),'#/files?artifact_id='+ids[0]);assert.equal(await page.getByText(source.excerpt,{exact:true}).count(),0)
}finally{await page.close()}})
test('desktop and mobile screenshots have wrapped navigation and no page overflow',async()=>{const {page,errors}=await open();try{
 const path=require('node:path'),fs=require('node:fs'),folder=process.env.BERVEL_EVIDENCE_ROOT||path.resolve(__dirname,'../local/workspace-polish-2026-10-28/frontend');fs.mkdirSync(folder,{recursive:true})
 for(const width of [1440,390]){await page.setViewportSize({width,height:900});await page.goto(base+'/#/files');await page.getByRole('button',{name:files[0].filename,exact:true}).click();await page.getByText('Строка 89',{exact:false}).waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.evaluate(()=>document.fonts.ready);await page.screenshot({path:path.join(folder,'files-'+width+'.png'),fullPage:true});await page.screenshot({path:path.join(folder,'files-'+width+'-viewport.png')})}
 assert.deepEqual(errors,[])
}finally{await page.close()}})
test('upload closes during in-flight original and retries role assignment without uploading it again',async()=>{const {page}=await open();let release;let uploads=0,roles=0;try{
 const gate=new Promise(r=>release=r)
 await page.route('**/api/v0.1/artifacts',async route=>{if(route.request().method()!=='POST')return route.fallback();uploads++;await gate;return route.fulfill({json:{artifact_id:ids[0]}})})
 await page.route('**/usage-role',route=>++roles===1?route.fulfill({status:503,json:{error:{code:'UNAVAILABLE'}}}):route.fulfill({json:{artifact_id:ids[0],usage_role:'stock'}}))
 await page.goto(base+'/#/files');await page.getByRole('button',{name:'Загрузить файл',exact:true}).click();const d=page.getByRole('dialog');await d.getByLabel('Файл',{exact:true}).setInputFiles({name:'stock.csv',mimeType:'text/csv',buffer:Buffer.from('a\n1')});await d.getByLabel('Назначение файла',{exact:true}).selectOption('stock');await d.getByRole('button',{name:'Загрузить файл',exact:true}).click();await d.getByRole('button',{name:'Закрыть',exact:true}).click();await page.getByRole('button',{name:'Загрузить файл',exact:true}).click();assert.equal(await d.getByRole('button',{name:'Загружаем…',exact:true}).isDisabled(),true);release();await d.getByRole('alert').waitFor();await d.getByRole('button',{name:'Повторить загрузку',exact:true}).click();await d.getByRole('status').waitFor();assert.equal(uploads,1);assert.equal(roles,2)
}finally{release?.();await page.close()}})
test('late exact-source metadata cannot replace a manually selected other file',async()=>{const {page}=await open();let release;try{
 const gate=new Promise(r=>release=r);await page.route('**/artifacts/'+ids[0]+'',async route=>{await gate;await route.fulfill({json:{...files[0],filename:'Поздний источник'}}).catch(()=>{})})
 await page.goto(base+'/#/files?artifact_id='+ids[0]);await page.getByRole('button',{name:files[1].filename,exact:true}).click();release();await page.getByRole('region',{name:'Просмотр файла',exact:true}).getByRole('heading',{name:files[1].filename,exact:true}).waitFor();await page.waitForTimeout(100);assert.equal(await page.getByRole('heading',{name:'Поздний источник',exact:true}).count(),0)
}finally{release?.();await page.close()}})
test('file read and metadata results share unique links without raw embedded source chunks',async()=>{const {page}=await open();try{
 const source={...files[0],row_start:1,row_end:1,total_rows:1,excerpt:'PRIVATE_CHUNK',truncated:false}
 await page.addInitScript(id=>localStorage.setItem('bervel.chat.session_id',id),ids[0]);await page.route('**/chat/sessions/'+ids[0],route=>route.fulfill({json:{session_id:ids[0],created_at:stamp,turns:[{turn_id:ids[1],question:'Вопрос',answer:'Ответ',state:'completed',created_at:stamp,sources:[source],actions:[],results:[{kind:'data',operation:'file.read',data:{file:files[0],sources:[source]}},{kind:'data',operation:'file.list',data:{artifacts:files,next_cursor:null}}],error:null}]}}))
 await page.goto(base+'/#/chat');const links=page.locator('a[href^="#/files?artifact_id="]');await links.first().waitFor();assert.equal(await links.count(),3);assert.equal(await page.getByText('PRIVATE_CHUNK',{exact:false}).count(),0);assert.equal(await page.getByText('Прочитать файл · данные',{exact:true}).count(),0)
}finally{await page.close()}})
test('CSV long cells and PDF pages expand vertically without an inner scrolling surface',async()=>{const {page}=await open();try{
 const long=Array.from({length:50},(_,i)=>'Ячейка '+i).join('\n')
 await page.route('**/preview*',route=>route.fulfill({json:route.request().url().includes(ids[0])?{kind:'table',media_type:'text/csv',columns:['Большой текст','Другая колонка'],rows:[[long,'x'.repeat(100)]],total_rows:1,next_cursor:null,text:'',notice:''}:{kind:'pdf',media_type:'application/pdf',columns:[],rows:[],total_rows:0,next_cursor:null,text:'',notice:''}}))
 await page.route('**/content',route=>route.fulfill({contentType:'application/pdf',body:require('node:fs').readFileSync(require('node:path').resolve(__dirname,'../fixtures/file-preview/two-pages.pdf'))}))
 await page.goto(base+'/#/files');await page.getByRole('button',{name:files[0].filename,exact:true}).click();const cell=page.getByRole('cell').filter({hasText:'Ячейка 49'});await cell.waitFor();assert.equal(await cell.locator('div').evaluate(n=>n.scrollHeight<=n.clientHeight),true);assert.equal(await page.getByRole('region',{name:'Содержимое CSV',exact:true}).evaluate(n=>n.scrollHeight<=n.clientHeight),true)
 await page.getByRole('button',{name:files[1].filename,exact:true}).click();const canvas=page.locator('canvas[data-rendered="true"]');await canvas.waitFor();assert.equal(await canvas.locator('..').evaluate(n=>n.scrollHeight<=n.clientHeight),true);assert.equal(await canvas.locator('..').evaluate(n=>getComputedStyle(n).overflowY),'visible')
}finally{await page.close()}})
test('resuming one file updates only its list row and preserves another file readiness',async()=>{const {page}=await open();try{
 let resumed=false;const stopped=files.map(file=>({...file,processing_state:'failed',completed_chunks:1,total_chunks:2,processing_error:'Обработка остановлена'}))
 await page.route('**/api/v0.1/artifacts?*',route=>route.fulfill({json:{artifacts:stopped,next_cursor:null}}))
 await page.route('**/artifacts/'+ids[0]+'/knowledge',route=>route.fulfill({json:{state:resumed?'ready':'failed',error:resumed?null:'Обработка остановлена',total_chunks:2,completed_chunks:resumed?2:1}}))
 await page.route('**/artifacts/'+ids[0]+'/knowledge/resume',route=>{assert.deepEqual(route.request().postDataJSON(),{});resumed=true;return route.fulfill({json:{state:'ready',error:null,total_chunks:2,completed_chunks:2}})})
 await page.goto(base+'/#/files?artifact_id='+ids[0]);await page.getByRole('button',{name:'Продолжить подготовку',exact:true}).click();await page.getByRole('region',{name:'Подготовка файла для ИИ',exact:true}).getByText('Готов к ИИ анализу',{exact:true}).waitFor();await page.getByRole('button',{name:files[0].filename,exact:true}).getByText('Готов к ИИ анализу',{exact:true}).waitFor();await page.getByRole('button',{name:files[1].filename,exact:true}).getByText('Обработка',{exact:true}).waitFor()
}finally{await page.close()}})
test('desktop file role and download share one compact toolbar row above the preview',async()=>{const {page}=await open();try{
 await page.goto(base+'/#/files');await page.getByRole('button',{name:files[0].filename,exact:true}).click();const role=page.getByLabel('Назначение выбранного файла',{exact:true}),download=page.getByRole('button',{name:'Скачать оригинал',exact:true});await download.waitFor();const r=await role.boundingBox(),d=await download.boundingBox();assert.ok(Math.abs((r.y+r.height)-(d.y+d.height))<2);assert.ok(d.x>r.x+r.width)
}finally{await page.close()}})
for(const target of [0,1])test('deleting a file cancels only its own pending exact-source metadata (target '+target+')',async()=>{const {page,requests}=await open();let release,deleted=false;try{
 const gate=new Promise(r=>release=r);await page.route('**/api/v0.1/artifacts?*',route=>route.fulfill({json:{artifacts:deleted?files.slice(1):files,next_cursor:null}}));await page.route('**/artifacts/'+ids[target],async route=>{await gate;await route.fulfill({json:{...files[target]}}).catch(()=>{})});await page.route('**/artifacts/'+ids[0],route=>{if(route.request().method()!=='DELETE')return route.fallback();deleted=true;return route.fulfill({status:204})})
 await page.goto(base+'/#/files?artifact_id='+ids[target]);await page.locator('.file-card').filter({has:page.getByRole('button',{name:files[0].filename,exact:true})}).getByRole('button',{name:'Удалить навсегда',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Удалить навсегда',exact:true}).click();await page.getByRole('status').filter({hasText:'Файл удалён'}).waitFor();release();await page.waitForTimeout(100)
 const detail=page.getByRole('region',{name:'Просмотр файла',exact:true});if(target===0){await detail.getByRole('alert').filter({hasText:'Файл удалён'}).waitFor();assert.equal(requests.some(r=>r.url().includes('/'+ids[0]+'/preview')),false)}else{await detail.getByRole('heading',{name:files[1].filename,exact:true}).waitFor();assert.equal(await detail.getByRole('alert').count(),0)}
}finally{release?.();await page.close()}})
