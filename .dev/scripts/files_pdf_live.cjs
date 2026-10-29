// Read-only end-to-end verification of the real deployed application, no model calls.
const {chromium}=require('playwright')
const assert=require('node:assert/strict')
const {mkdirSync,writeFileSync}=require('node:fs')
const {resolve}=require('node:path')
;(async()=>{
 const output=resolve(__dirname,'../local/file-layout/live');mkdirSync(output,{recursive:true})
 const pdfOutput=resolve(__dirname,'../outputs/pdf/bervel-radar-2026-10-22.pdf')
 const evidence={pdf:{},files:[],viewports:[],errors:[]}
 const browser=await chromium.launch({headless:true})
 const page=await browser.newPage({viewport:{width:1600,height:1080},acceptDownloads:true})
 page.on('pageerror',e=>evidence.errors.push(e.message));page.setDefaultTimeout(15000)
 async function api(path){return page.evaluate(async path=>{const r=await fetch('/api/v0.1'+path,{headers:{'X-Bervel-Web':'1'}});if(!r.ok)throw Error('API '+r.status);return r.json()},path)}
 try{
  const id='78ca94fa-6be4-445b-86e2-b06cf5c99804'
  await page.goto('http://127.0.0.1/#/radar?run='+id)
  await page.getByRole('heading',{name:'Результат отбора',exact:true}).waitFor()
  const run=await api('/radar/runs/'+id);assert.equal(run.report.counts.take_in_work,4)
  const download=page.waitForEvent('download');await page.getByRole('button',{name:'Скачать PDF',exact:true}).click()
  const file=await download;assert.equal(file.suggestedFilename(),'bervel-radar-'+id+'.pdf');await file.saveAs(pdfOutput)
  evidence.pdf={run_id:id,saved:pdfOutput,counts:run.report.counts}
  const hashes=await page.evaluate(async id=>{
   const values=[];for(let i=0;i<2;i++){
    const r=await fetch('/api/v0.1/radar/runs/'+id+'/report.pdf',{headers:{'X-Bervel-Web':'1'}});if(!r.ok)throw Error('PDF '+r.status)
    const bytes=await r.arrayBuffer();values.push(Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(v=>v.toString(16).padStart(2,'0')).join(''))
   }return values
  },id);assert.equal(hashes[0],hashes[1]);evidence.pdf.sha256=hashes[0]
  if(process.env.PDF_ONLY==='1'){evidence.passed=true;writeFileSync(resolve(output,'stage1.json'),JSON.stringify(evidence,null,2));return}
  let cursor=0,files=[]
  do{const result=await api('/artifacts?cursor='+cursor+'&limit=100');files.push(...result.artifacts);cursor=result.next_cursor}while(cursor!==null)
  const choices=[files.find(f=>/kontur-public-tenders/.test(f.filename)&&f.artifact_id==='b0db163f-774d-4ca8-89c9-d746d082d99e'),files.find(f=>f.filename.endsWith('.png')),files.find(f=>f.filename.endsWith('.pdf')),files.find(f=>f.filename.endsWith('.xlsx'))].filter(Boolean)
  assert.equal(choices.length,4)
  await page.goto('http://127.0.0.1/#/files')
  await page.getByRole('heading',{name:'Файлы',exact:true}).waitFor()
  for(const f of choices){
   let target=page.getByRole('button',{name:f.filename,exact:true})
   while(await target.count()===0){await page.getByRole('button',{name:'Показать ещё файлы',exact:true}).click();await page.waitForTimeout(100)}
   // Duplicate immutable uploads may have the same name: choose the intended card by its metadata if needed.
   const index=files.filter(item=>item.filename===f.filename).findIndex(item=>item.artifact_id===f.artifact_id)
   while(await target.count()<=index){await page.getByRole('button',{name:'Показать ещё файлы',exact:true}).click();await page.waitForTimeout(100)}
   await target.nth(index).click()
   const result=await api('/artifacts/'+f.artifact_id+'/revisions/'+f.revision+'/preview')
   if(result.kind==='table')await page.getByRole('columnheader',{name:'number',exact:true}).waitFor()
   else if(result.kind==='image')await page.waitForFunction(()=>{const image=document.querySelector('img');return image?.naturalWidth>0})
   else if(result.kind==='pdf'){
    await page.locator('canvas[data-rendered="true"]').waitFor()
    const nonblank=await page.locator('canvas').evaluate(canvas=>{
     const data=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data
     let dark=0;for(let i=0;i<data.length;i+=4)if(data[i]<180&&data[i+1]<180&&data[i+2]<180)dark++
     return dark>100
    });assert.equal(nonblank,true)
   }
   else await page.locator('pre').waitFor()
   evidence.files.push({filename:f.filename,kind:result.kind,columns:result.columns.length,rows:result.total_rows})
   const suffix=result.kind==='pdf'?'pdf':result.kind==='image'?'image':result.kind==='table'?'csv':'office'
   if(suffix==='pdf'){await page.waitForTimeout(500);await page.locator('canvas[data-rendered="true"]').waitFor()}
   // Full-page capture changes the viewport and can trigger a fresh canvas render.
   await page.screenshot({path:resolve(output,'files-'+suffix+'-1600.png'),fullPage:suffix!=='pdf'})
  }
  await page.getByRole('button',{name:choices.find(f=>f.filename.endsWith('.pdf')).filename,exact:true}).click()
  for(const width of [1600,390]){
   await page.setViewportSize({width,height:1000})
   await page.waitForTimeout(200);await page.locator('canvas[data-rendered="true"]').waitFor()
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
   const list=await page.getByRole('region',{name:'Список файлов',exact:true}).boundingBox(),preview=await page.getByRole('region',{name:'Просмотр файла',exact:true}).boundingBox()
   if(width===1600)assert.ok(preview.width/list.width>1.9&&preview.width/list.width<2.1)
   else assert.ok(preview.y>list.y)
   evidence.viewports.push({width,list,preview,no_overflow:true})
   if(width===390){await page.getByRole('region',{name:'Просмотр файла',exact:true}).scrollIntoViewIfNeeded();await page.locator('canvas[data-rendered="true"]').waitFor()}
   await page.screenshot({path:resolve(output,'files-layout-'+width+'.png'),fullPage:false})
  }
  assert.deepEqual(evidence.errors,[]);evidence.passed=true
  writeFileSync(resolve(output,'acceptance.json'),JSON.stringify(evidence,null,2))
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1})
