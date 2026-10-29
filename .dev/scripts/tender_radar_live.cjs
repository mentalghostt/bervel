// Actual browser workflow, real PostgreSQL/worker/MOX; no intercepted responses.
const {chromium}=require('playwright')
const assert=require('node:assert/strict')
const {readFileSync,writeFileSync,mkdirSync,existsSync}=require('node:fs')
const {resolve}=require('node:path')
;(async()=>{
 const root=resolve(__dirname,'../..'),output=resolve(root,'.dev/local/tender-radar/live')
 mkdirSync(output,{recursive:true})
 const evidence={real_model:true,uploads:[],batches:[],runs:[],browser_errors:[]}
 const browser=await chromium.launch({headless:true})
 const page=await browser.newPage({viewport:{width:1440,height:1080},acceptDownloads:true})
 page.setDefaultTimeout(10000);page.on('pageerror',error=>evidence.browser_errors.push(error.message))
 async function importList(path){
  await page.getByLabel('Список закупок XLSX/CSV',{exact:true}).setInputFiles(path)
  const uploaded=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith('/artifacts'))
  const imported=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith('/radar/batches'))
  await page.getByRole('button',{name:'Импортировать закупки',exact:true}).click()
  const originalResponse=await uploaded;assert.equal(originalResponse.status(),200)
  evidence.uploads.push({filename:path.split('/').pop(),...await originalResponse.json()})
  const batchResponse=await imported;assert.equal(batchResponse.status(),200,await batchResponse.text())
  const batch=await batchResponse.json();evidence.batches.push(batch)
  await page.getByRole('status').filter({hasText:'импортировано'}).waitFor()
  console.log(JSON.stringify({stage:'import',filename:batch.filename,count:batch.count,skipped:batch.skipped_rows}))
  return batch
 }
 async function uploadProfile(filename,content){
  const summary=page.getByText('Добавить файл профиля',{exact:true})
  if(!await page.getByLabel('Файл для памяти чата',{exact:true}).isVisible())await summary.click()
  await page.getByLabel('Файл для памяти чата',{exact:true}).setInputFiles({name:filename,mimeType:filename.endsWith('.csv')?'text/csv':'text/plain',buffer:content})
  const uploaded=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith('/artifacts'))
  await page.getByRole('button',{name:'Загрузить файл',exact:true}).click()
  const response=await uploaded;assert.equal(response.status(),200)
  const ref={filename,...await response.json()};evidence.uploads.push(ref)
  await page.getByRole('checkbox',{name:filename+' · редакция 1',exact:true}).waitFor()
  return ref
 }
 async function selectOnly(filename){
  for(const checkbox of await page.getByRole('checkbox').all())if(await checkbox.isChecked())await checkbox.uncheck()
  await page.getByRole('checkbox',{name:filename+' · редакция 1',exact:true}).check()
 }
 async function run(label){
  const sent=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith('/radar/runs'))
  await page.getByRole('button',{name:'Подобрать тендеры',exact:true}).click()
  const response=await sent;assert.equal(response.status(),202,await response.text())
  const accepted=await response.json();const deadline=Date.now()+200000
  while(Date.now()<deadline){
   const item=await page.evaluate(async id=>{const r=await fetch('/api/v0.1/radar/runs/'+id,{headers:{'X-Bervel-Web':'1'}});if(!r.ok)throw Error('Read run '+r.status);return r.json()},accepted.run_id)
   if(['completed','failed'].includes(item.state)){
    evidence.runs.push({label,...item});writeFileSync(resolve(output,'acceptance.json'),JSON.stringify(evidence,null,2))
    assert.equal(item.state,'completed',item.error)
    await page.getByRole('heading',{name:'Результат отбора',exact:true}).waitFor()
    await page.getByRole('heading',{name:'Результат отбора',exact:true}).scrollIntoViewIfNeeded()
    await page.screenshot({path:resolve(output,label+'.png')})
    console.log(JSON.stringify({stage:label,run_id:item.run_id,model:item.model,counts:item.report.counts,items:item.report.items.filter(i=>i.decision==='take_in_work').map(i=>({number:i.number,reason:i.reason,risks:i.risks}))}))
    return item
   }
   await page.waitForTimeout(1000)
  }
  throw Error('No final result in200s; no automatic retry')
 }
 try{
  await page.goto('http://127.0.0.1/#/radar')
  let profile
  // A fixture-export timezone failure left a valid archive run and profile.
  // Resume those confirmed originals, without recreating them or repeating a model call.
  if(existsSync(resolve(output,'attempt-1.json'))){
   const prior=JSON.parse(readFileSync(resolve(output,'attempt-1.json')))
   evidence.uploads.push(...prior.uploads);evidence.batches.push(...prior.batches);evidence.runs.push(...prior.runs)
   assert.equal(prior.batches[0].count,2290);assert.equal(prior.runs[0].report.counts.take_in_work,0)
   profile=prior.uploads.find(u=>u.filename==='bervel-product-profile.csv')
   console.log('Resuming verified historical import and completed archive screening')
  }else{
   const archive=await importList(resolve(root,'.dev/data/private/customer-package-2026-10-06/2.xlsx'))
   assert.equal(archive.count,2290);assert.equal(archive.skipped_rows,1)
   profile=await uploadProfile('bervel-product-profile.csv',readFileSync(resolve(root,'.dev/outputs/tender-radar/bervel-product-profile.csv')))
   await selectOnly(profile.filename)
   const historical=await run('historical')
   assert.equal(historical.report.counts.take_in_work,0)
  }
  let current
  if(existsSync(resolve(output,'attempt-2.json'))){
   const prior=JSON.parse(readFileSync(resolve(output,'attempt-2.json')))
   current=prior.batches.find(b=>b.filename==='kontur-public-tenders-2026-10-22.csv')
   evidence.batches.push(current)
   for(const upload of prior.uploads)if(!evidence.uploads.some(u=>u.artifact_id===upload.artifact_id))evidence.uploads.push(upload)
   await page.getByRole('combobox').selectOption(current.batch_id)
  }else current=await importList(resolve(root,'.dev/outputs/tender-radar/kontur-public-tenders-2026-10-22.csv'))
  assert.equal(current.count,6)
  await selectOnly(profile.filename)
  const actual=await run('current-fasteners')
  assert.ok(actual.report.counts.take_in_work>=1,'Must find at least one real current fastener candidate for initial manager review')
  assert.ok(actual.report.items.filter(i=>i.decision==='take_in_work').every(i=>i.number!=='32616494305'))
  assert.ok(actual.report.sources.every(s=>s.artifact_id===profile.artifact_id))
  assert.ok(actual.report.items.every(i=>!i.participation_ready))
  assert.ok(actual.model,'Actual current screening must call the real model')
  const downloading=page.waitForEvent('download');await page.getByRole('button',{name:'Скачать PDF',exact:true}).click()
  const download=await downloading;const saved=resolve(output,'real-report.pdf');await download.saveAs(saved)
  assert.equal(readFileSync(saved).subarray(0,5).toString(),'%PDF-')
  evidence.report_download_verified=true
  const other=await uploadProfile('radar-control-other-profile.txt',Buffer.from('Контрольный альтернативный профиль для проверки выбора файлов. Трубопроводная арматура, задвижки, клапаны. Возможности не подтверждены; это не профиль БЕРВЕЛ. CONTROL_SELECTED_ONLY_6732.'))
  await selectOnly(other.filename)
  const alternative=await run('selected-other-profile')
  assert.ok(alternative.report.sources.every(s=>s.artifact_id===other.artifact_id))
  const former=actual.report.items.filter(i=>i.decision==='take_in_work').map(i=>i.number)
  assert.ok(alternative.report.items.filter(i=>i.decision==='take_in_work').every(i=>!former.includes(i.number)),'Changing selected RAG must remove fastener recommendations')
  // Leave the normal BERVEL result selected after proving alternative isolation.
  await page.evaluate(id=>localStorage.setItem('bervel.radar.run_id',id),actual.run_id)
  await page.reload()
  await page.getByRole('heading',{name:'Результат отбора',exact:true}).waitFor()
  await page.getByRole('heading',{name:actual.report.items.find(i=>i.decision==='take_in_work').title,exact:true}).waitFor()
  evidence.reload_verified=true
  await page.getByRole('heading',{name:'Тендерный радар',exact:true}).scrollIntoViewIfNeeded()
  await page.screenshot({path:resolve(output,'radar-desktop.png')})
  await page.setViewportSize({width:390,height:900})
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.screenshot({path:resolve(output,'radar-mobile.png')})
  assert.deepEqual(evidence.browser_errors,[]);evidence.passed=true
  console.log('REAL TENDER RADAR ACCEPTANCE PASSED')
 }finally{writeFileSync(resolve(output,'acceptance.json'),JSON.stringify(evidence,null,2));await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)})
