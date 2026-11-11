// Explicit live acceptance against the isolated QA schema; an existing receipt is always reused.
const {test}=require('node:test'), assert=require('node:assert/strict'), path=require('node:path'), fs=require('node:fs')
const root=path.resolve('.dev/local/presales-pipeline-2026-11-10'), directory=path.join(root,'live-browser')
fs.mkdirSync(directory,{recursive:true});process.env.TMP=directory;process.env.TEMP=directory
const {chromium}=require(path.resolve('.dev/node_modules/playwright'))
const base=process.env.BERVEL_PRESALES_LIVE_URL||'http://127.0.0.1:5197'
const stateFile=path.join(root,'qa-run.json')
test('real model and backend create a quoted request in the isolated QA database',{timeout:480000},async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true})
 const page=await browser.newPage({viewport:{width:1440,height:1000},acceptDownloads:true})
 const errors=[];page.on('pageerror',error=>errors.push(error.message));page.setDefaultTimeout(10000)
 try{
  let ready=false
  for(let attempt=0;attempt<60;attempt++){
   try{const response=await page.request.get(base+'/api/v0.1/presales/settings',{headers:{'X-Bervel-Web':'1'},timeout:1500});if(response.ok()){ready=true;break}}catch{}
   await page.waitForTimeout(500)
  }
  assert.ok(ready,'isolated QA API becomes ready before browser actions')
  await page.goto(base+'/#/presales-settings')
  assert.equal(await page.getByLabel('Целевая маржа, %',{exact:true}).inputValue(),'50')
  assert.equal(await page.getByLabel('Минимальная маржа спецзаказа, %').inputValue(),'70')
  const saved=await page.evaluate(async()=>fetch('/api/v0.1/presales/settings',{headers:{'X-Bervel-Web':'1'}}).then(r=>r.json()))
  if(saved.requires_save){await page.getByRole('button',{name:'Сохранить настройки',exact:true}).click();await page.getByText('Настройки сохранены.',{exact:true}).waitFor()}
  let receipt=fs.existsSync(stateFile)?JSON.parse(fs.readFileSync(stateFile,'utf8')):null
  if(!receipt){
   await page.goto(base+'/#/presales');await page.getByRole('button',{name:'Новая заявка',exact:true}).click()
   const dialog=page.getByRole('dialog',{name:'Файлы заявки'})
   await dialog.getByRole('checkbox',{name:/^QA-client-request\.txt/}).check()
   await dialog.getByRole('button',{name:'Создать заявку',exact:true}).click()
   await page.waitForURL(/request=[0-9a-f-]+&revision=[0-9a-f-]+/)
   const params=new URLSearchParams(new URL(page.url()).hash.split('?')[1])
   receipt={request_id:params.get('request'),revision_id:params.get('revision')}
   fs.writeFileSync(stateFile,JSON.stringify(receipt,null,2))
  }else {
   await page.goto(base+`/#/presales?request=${receipt.request_id}&revision=${receipt.revision_id}`)
   if(process.env.BERVEL_QA_EXPLICIT_RESUME==='1'&&!receipt.resumed){
    const accepted=page.waitForResponse(response=>response.url().endsWith('/resume')&&response.request().method()==='POST')
    await page.getByRole('button',{name:'Продолжить',exact:true}).click()
    assert.equal((await accepted).status(),202)
    receipt.resumed=true;fs.writeFileSync(stateFile,JSON.stringify(receipt,null,2))
   }
  }
  let revision
  const until=Date.now()+420000
  while(Date.now()<until){
   revision=await page.evaluate(async({request_id,revision_id})=>fetch(`/api/v0.1/presales/requests/${request_id}/revisions/${revision_id}`,{headers:{'X-Bervel-Web':'1'}}).then(r=>r.json()),receipt)
   if(['complete','failed','stopped'].includes(revision.state))break
   await page.waitForTimeout(1500)
  }
  fs.writeFileSync(path.join(root,'qa-live-result.json'),JSON.stringify(revision,null,2))
  assert.equal(revision.state,'complete',JSON.stringify(revision.job?.error||revision))
  assert.equal(revision.offer.lines.length,2)
  assert.deepEqual(revision.offer.totals,{RUB:'1400.00'})
  assert.equal(revision.metrics.product_match_percent,'100');assert.equal(revision.metrics.stock_coverage_percent,'100')
  assert.equal(typeof revision.score,'number')
  assert.ok(revision.artifacts.pdf&&revision.artifacts.docx)
  await page.reload();await page.getByText(revision.offer.number,{exact:false}).waitFor()
  for(const [kind,label]of[['pdf','Скачать PDF'],['docx','Скачать DOCX']]){
   const pending=page.waitForEvent('download')
   await page.getByRole('button',{name:label,exact:true}).click()
   const downloaded=await pending
   await downloaded.saveAs(path.join(directory,'actual-offer.'+kind))
   assert.ok(fs.statSync(path.join(directory,'actual-offer.'+kind)).size>100)
  }
  await page.screenshot({path:path.join(directory,'actual-1440.png'),fullPage:true})
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(directory,'actual-390.png'),fullPage:true})
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  assert.deepEqual(errors,[])
  console.log('Live receipt:',receipt.request_id,'score:',revision.score,'offer:',revision.offer.number)
 }finally{await browser.close()}
})
