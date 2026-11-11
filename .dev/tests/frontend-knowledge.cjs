const assert=require('node:assert/strict')
const {test}=require('node:test')
const {chromium}=require('playwright')

test('file shows indexing progress and explicit recovery without automatic retry',async()=>{
  const browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined})
  const page=await browser.newPage()
  let resumes=0
  const id='11111111-1111-4111-8111-111111111111'
  await page.route('**/api/v0.1/**',async route=>{
    const path=new URL(route.request().url()).pathname
    if(path.endsWith('/artifacts')) return route.fulfill({json:{artifacts:[{artifact_id:id,filename:'Каталог.csv',media_type:'text/csv',size:128,created_at:'2026-10-27T10:00:00Z'}],next_cursor:null}})
    if(path.endsWith('/preview')) return route.fulfill({json:{kind:'table',media_type:'text/csv',columns:['Товар'],rows:[['Болт']],total_rows:1,next_cursor:null,text:'',notice:''}})
    if(path.endsWith('/resume')) {resumes++; return route.fulfill({json:{state:'pending',error:null,total_chunks:10,completed_chunks:4}})}
    if(path.endsWith('/knowledge')) return route.fulfill({json:resumes?{state:'ready',error:null,total_chunks:10,completed_chunks:10}:{state:'failed',error:'Временно недоступна модель.',total_chunks:10,completed_chunks:4}})
    return route.fulfill({status:404,json:{error:{code:'NOT_FOUND',message:'Unknown route'}}})
  })
  try{
    await page.goto((process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1:5174')+'/#/files')
    await page.getByRole('button',{name:'Каталог.csv',exact:true}).click()
    await page.getByText('Временно недоступна модель.',{exact:true}).waitFor()
    assert.equal(resumes,0)
    await page.getByRole('button',{name:'Продолжить подготовку'}).click()
    await page.getByText('Готов к ИИ анализу',{exact:false}).waitFor()
    assert.equal(resumes,1)
    assert.match(await page.getByRole('region',{name:'Подготовка файла для ИИ'}).innerText(),/10/)
  }finally{await browser.close()}
})
