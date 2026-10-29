// Read-only final report rendering/deep link/download QA; no model calls or uploads.
const {chromium}=require('playwright')
const assert=require('node:assert/strict')
const {readFileSync,writeFileSync}=require('node:fs')
const {resolve}=require('node:path')
;(async()=>{
 const output=resolve(__dirname,'../local/tender-radar/live')
 const proof=JSON.parse(readFileSync(resolve(output,'acceptance.json')))
 const actual=proof.runs.find(r=>r.label==='current-fasteners')
 const browser=await chromium.launch({headless:true})
 const results=[]
 try{
  for(const width of [1440,390]){
   const page=await browser.newPage({viewport:{width,height:1000},acceptDownloads:true})
   const errors=[];page.on('pageerror',error=>errors.push(error.message))
   await page.goto('http://127.0.0.1/#/radar?run='+actual.run_id)
   await page.getByRole('heading',{name:'Результат отбора',exact:true}).waitFor()
   await page.getByRole('button',{name:'В работу сейчас (4)',exact:true}).waitFor()
   await page.getByText('Выбранные файлы: bervel-product-profile.csv (редакция 1)',{exact:true}).waitFor()
   await page.getByRole('heading',{name:'Результат отбора',exact:true}).scrollIntoViewIfNeeded()
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
   await page.screenshot({path:resolve(output,'final-report-'+width+'.png')})
   await page.getByText('Происхождение сведений о закупке',{exact:true}).first().click()
   await page.getByText('Доказательства из выбранных файлов',{exact:true}).first().click()
   if(width===1440){
    const downloadEvent=page.waitForEvent('download');await page.getByRole('button',{name:'Скачать PDF',exact:true}).click()
    const download=await downloadEvent;await download.saveAs(resolve(output,'real-report.pdf'))
    assert.equal(readFileSync(resolve(output,'real-report.pdf')).subarray(0,5).toString(),'%PDF-')
   }
   assert.deepEqual(errors,[]);results.push({width,deep_link:true,candidates:4,no_overflow:true,browser_errors:errors})
   await page.close()
  }
 }finally{await browser.close()}
 writeFileSync(resolve(output,'ui-final.json'),JSON.stringify({passed:true,results},null,2))
 console.log('Read-only report QA passed desktop/mobile/deep-link/download')
})().catch(error=>{console.error(error);process.exit(1)})
