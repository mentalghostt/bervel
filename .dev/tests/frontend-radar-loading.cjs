const {test}=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs')
const artifacts=path.resolve(process.env.BERVEL_TEST_ARTIFACT_ROOT||'.dev/local/ngrok-radar-load-2026-11-10/browser')
fs.mkdirSync(path.join(artifacts,'tmp'),{recursive:true});process.env.TEMP=path.join(artifacts,'tmp');process.env.TMP=process.env.TEMP
const {chromium}=require('playwright')
const rid='33333333-3333-4333-8333-333333333333',sid='11111111-1111-4111-8111-111111111111',time='2026-11-10T09:00:00Z'
const profile=source=>({source,name:source,run_id:rid,keyword_mode:'auto',keywords:[],schedule:{enabled:false,every_days:1,time:'09:00',timezone:'Europe/Moscow'},available:true,last_refresh_at:time,next_refresh_at:null})
const card=i=>({analysis_id:'a'+i,candidate_id:'c'+i,state:'primary',last_seen_at:time,information_state:'primary',details_loaded:false,brief:{OrderName:'Tender '+String(i).padStart(3,'0')},tender:{title:'Tender '+String(i).padStart(3,'0'),number:'N'+i,link:null,documents:[]},analysis:null,fulfillment:null,assessment:null,review:null,metrics:{price:String(i),currency:'RUB',stage:'accepting',actuality:'active',trade_type:'commercial_small',catalog_match_percent:'50',stock_coverage_percent:String(i)},rating:{status:'pending',score:null}})
const data={run_id:rid,settings_snapshot_id:sid,created_at:time,data:{state:'complete',phase:'complete',detail_attempts:0,checkpoint:{search_attempts:200}},cards:Array.from({length:103},(_,i)=>card(i+1)),reports:[]}
test('Radar finishes a slow profile response after the old 30 second deadline', {timeout:60000},async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.BERVEL_BROWSER_PATH})
 const page=await browser.newPage();page.setDefaultTimeout(35000)
 await page.route('**/api/v0.1/**',async route=>{const p=new URL(route.request().url()).pathname;if(p.endsWith('/profiles'))return route.fulfill({json:{profiles:[profile('test_api'),profile('kontur_api')]}});if(p.endsWith('/profiles/test_api')){await new Promise(resolve=>setTimeout(resolve,31000));await route.fulfill({json:{profile:profile('test_api'),run:data}}).catch(()=>{});return}return route.fulfill({status:404})})
 try{await page.goto((process.env.BERVEL_FRONTEND_URL||'http://127.0.0.1:5189')+'/#/ai-tender-radar?profile=test_api');await page.locator('.ai-radar-card-list>.ai-radar-card').first().waitFor();assert.equal(await page.getByRole('alert').count(),0)}finally{await browser.close()}
})
