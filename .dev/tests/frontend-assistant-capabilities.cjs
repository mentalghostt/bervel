const {test}=require('node:test')
const assert=require('node:assert/strict')
const {chromium}=require('playwright')

test('voice settings expose the Groq key field without service descriptions',async()=>{
 const browser=await chromium.launch({executablePath:process.env.BERVEL_BROWSER_PATH,headless:true,channel:process.env.BERVEL_BROWSER_CHANNEL})
 const page=await browser.newPage()
 try{
  await page.route('**/api/v0.1/**',async route=>{
   const p=new URL(route.request().url()).pathname
   if(p.endsWith('/voice/settings'))return route.fulfill({json:{configured:false,state:'unconfigured',model:'whisper-large-v3-turbo'}})
   if(p.endsWith('/settings/model'))return route.fulfill({json:{provider:'mox',configured:true,api:{base_url:'https://api.openai.com/v1',model:'test',api_key_configured:false},mox:{base_url:'https://example.com',model:'test',credentials_available:true}}})
   if(p.endsWith('/settings/kontur'))return route.fulfill({json:{configured:false,limits:[]}})
   if(p.endsWith('/settings/kontur/simulation/quota'))return route.fulfill({json:{available:false,unlimited:false,resettable:false,limits:[]}})
   if(p.endsWith('/settings/company'))return route.fulfill({json:{snapshot:null}})
   if(p.endsWith('/artifacts'))return route.fulfill({json:{artifacts:[],next_cursor:null}})
   if(p.endsWith('/chat/sessions'))return route.fulfill({json:{sessions:[],next_offset:null}})
   if(p.endsWith('/tickets'))return route.fulfill({json:{tickets:[],next_cursor:null}})

   throw Error('Unexpected route '+p)
  })
  await page.goto((process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1:5173')+'/#/settings')
  const panel=page.getByRole('region',{name:'Настройки Groq'})
  await panel.getByRole('heading',{name:'Groq: голосовой ввод',exact:true}).waitFor({timeout:4000})
  assert.doesNotMatch(await panel.innerText(),/Веб-поиск выполняет Codex|Распознавание речи для/)
  assert.equal(await panel.getByRole('textbox',{name:'API-ключ Groq',exact:true}).count(),1)
 }finally{await browser.close()}
})
