const {test} = require('node:test')
const assert = require('node:assert/strict')
const http = require('node:http')
const {chromium} = require('playwright')

const sid = '11111111-1111-4111-8111-111111111111'
const oldSid = '22222222-2222-4222-8222-222222222222'
const rid = '33333333-3333-4333-8333-333333333333'
const oldRid = '44444444-4444-4444-8444-444444444444'
const qid = '55555555-5555-4555-8555-555555555555'
const aid = '66666666-6666-4666-8666-666666666666'
const stockId = '77777777-7777-4777-8777-777777777777'
const time = '2026-11-05T09:00:00Z'
const profile = {stock_artifact_id: stockId, stock_sha256: 'a'.repeat(64), products: [{id: 'p1', sku: 'B1', name: 'Болт', unit: 'шт', attributes: {}, enabled: true, manufactured_before: null}], experimental_products: [], include_experimental: false, production: [], resources: [], scoring_policy: {}}
const snapshot = {id: sid, created_at: time, profile}
const boilerplate = [
  'Объяснение относится к предоставленному историческому снимку.',
  'Оценка условна относительно других ещё не принятых тендеров.',
  'План является оценкой исполнения, а не резервом.',
  'Оценка условна: другие ещё не принятые тендеры не резервируют склад и мощности.',
  'Стресс: полная прибыль и балл неизвестны.',
]
const review = {state: 'complete', recommendation: 'Нужна проверка заводских данных.', risks: ['Недостаточная маржа.', boilerplate[4]], assumptions: boilerplate.slice(0, 4), evidence_refs: ['assessment']}
function card(i, state, score) {
  return {analysis_id: `analysis-${i}`, candidate_id: `candidate-${i}`, state, evaluated_at: time,
    metrics: {price: String(i * 500000), currency: 'RUB', stage: i === 3 ? 'closed' : 'accepting', catalog_match_percent: String(125 - i * 25)},
    tender: {title: ['Поставка болтов', 'Поставка гаек', 'Обслуживание станка'][i - 1], number: `SIM-${i}`, link: null},
    completeness: {requirements: 'complete', economics: score === null ? 'unknown' : 'complete', schedule: 'complete', reasons: []},
    analysis: {coverage_percent: '100', benefit_percent: '-10.576923076923', model: 'fixture-model', economics: {scope: score === null ? null : 'full', revenue: score === null ? null : '100', cost: score === null ? null : '110'}, positions: [{id: '1:0:7', lot: 1, name: 'Болт М12', required: '10', covered: '10', shortage: '0', unit: 'шт', economics: {status: 'profitable', margin_percent: '35.5', scope: 'full', revenue: '200', cost: '129', profit: '71', currency: 'RUB', reason: null}}]},
    fulfillment: {status: score === null ? 'needs_review' : 'feasible', reasons: [], production_allocations: []},
    rating: {status: score === null ? 'pending' : 'complete', score},
    assessment: {score, ...(i === 1 ? {stress_score: score} : {}), priority: 'high', reasons: score === null ? ['FULL_ECONOMICS_OR_FEASIBILITY_UNKNOWN'] : ['PLAN_NOT_PROFITABLE'], E: null, S: '1', P: null, full_economics: {profit: score === null ? null : i === 2 ? '15' : '-10', margin_percent: score === null ? null : i === 2 ? '15' : '-10', currency: 'RUB'}}, review}
}
const cards = [card(1, 'accepted', 0), card(2, 'needs_data', null), card(3, 'irrelevant', 0)]
const candidates = Array.from({length: 125}, (_, i) => ({id: `brief-${i + 1}`, brief: {OrderName: `Кандидат ${i + 1}`}, state: 'not_loaded', reasons: ['DETAIL_BUDGET']}))
function run(id = rid, settings = oldSid) {return {run_id: id, settings_snapshot_id: settings, created_at: time, data: {state: 'complete', phase: 'complete', checkpoint: {search_attempts: 40, candidates: candidates.map(c => ({id: c.id})), truncated: true, stop_reason: 'search_budget'}, detail_attempts: 3, model_attempts: 6, counts: {looked: 3, accepted: 1, needs_data: 1, irrelevant: 1, failed: 0}, stop_reason: 'detail_budget'}, cards, candidates, reports: []}}
async function withBrowser(width, callback) {
  const upstream = new URL(process.env.BERVEL_FRONTEND_URL || 'http://frontend')
  const proxy = http.createServer((req, res) => {
    const forward = http.request(new URL(req.url, upstream), {method: req.method, headers: req.headers}, response => {res.writeHead(response.statusCode, response.headers);response.pipe(res)})
    forward.on('error', () => {res.writeHead(502);res.end()});req.pipe(forward)
  })
  await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve))
  const browser = await chromium.launch({headless: true, channel: process.env.BERVEL_BROWSER_CHANNEL})
  try {const page = await browser.newPage({viewport: {width, height: 1000}});page.setDefaultTimeout(15000);const errors = [];page.on('pageerror', e => {errors.push(e.message);console.error('PAGE ERROR:',e.message)});await callback(page, `http://127.0.0.1:${proxy.address().port}`);assert.deepEqual(errors, []);assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)} finally {await browser.close();await new Promise(resolve => proxy.close(resolve))}
}


const legacyId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const legacyCard = {job_id: legacyId, stock_artifact_id: stockId, updated_at: time, tender: {id: 'old', title: 'Архивные болты', number: 'OLD-1', link: null, position_count: 0, limitations: []}, status: 'complete', coverage_percent: '100', position_coverage_percent: null, benefit_percent: '60', benefit_reason: null, benefit_scope: 'full', mock: true}
const auditReport = {job_id:aid,kind:'audit',result:{status:'complete',checked:3,total:3,findings:[]}}
function fixtureRun() {const value=run();value.data.name='Крепёж ноябрь';value.data.source='test_api';value.data.search_scope={from:'2026-11-01',to:'2026-11-05',source:'test_api'};value.data.unverified_count=7;value.data.scope_complete=false;value.cards=[card(1,'accepted',0), {...card(2,'accepted',82), analysis:{...card(2,'accepted',82).analysis,coverage_percent:'80',benefit_percent:'15'}}, {...card(3,'accepted',99),tender:{title:'Низкое покрытие',number:'LOW',link:null},metrics:{...card(3,'accepted',99).metrics,stock_coverage_percent:'6'},analysis:{...card(3,'accepted',99).analysis,coverage_percent:null}},{...card(2,'needs_data',null),analysis_id:'unknown-4',candidate_id:'unknown-4',tender:{title:'Неподтверждённые гайки',number:'UNKNOWN',link:null},analysis:{...card(2,'needs_data',null).analysis,coverage_percent:null}}];value.reports=[auditReport];return value}
for(const width of [1440,390]) test('Unified history, source form, compact grid and keyboard popup '+width,()=>withBrowser(width,async(page,origin)=>{
 const commands=[];let lose=true;const requested=[]
 const detail = fixtureRun()
 detail.cards[0].review = {...review, risks: [...review.risks, 'unconfirmed: 80.327180301%.']}
 detail.cards[1].review = {...review, decision: 'participate', recommendation: 'Заказ выполним с прибылью.', manual_checks: []}
 detail.cards[3].review = {...review, decision: 'review', recommendation: 'Без стоимости доставки нельзя оценить выгоду.',
   manual_checks: ['Уточнить стоимость доставки до места поставки.']}
 await page.route('**/api/v0.1/**',route=>{const req=route.request(),path=new URL(req.url()).pathname;requested.push(path)
 if(path.endsWith('/settings/company'))return route.fulfill({json:{snapshot}})
 if(path.endsWith('/settings/kontur'))return route.fulfill({json:{configured:true,limits:['search','purchase','results'].map(group=>({group,remaining:200,maximum:200}))}})
 if(path.endsWith('/runs/history'))return route.fulfill({json:{entries:[{id:'legacy',kind:'legacy',title:'Результаты до обновления',created_at:time,state:'complete',card_count:1},{id:rid,kind:'run',title:'Крепёж ноябрь',created_at:time,state:'complete',card_count:3}],next_offset:null}})
 if(path.endsWith('/runs/legacy'))return route.fulfill({json:{entry:{id:'legacy',kind:'legacy',title:'Результаты до обновления',created_at:time,state:'complete',card_count:1},cards:[legacyCard],next_offset:null}})
 if(path.endsWith('/jobs/'+legacyId))return route.fulfill({json:{job_id:legacyId,state:'loaded',stock_artifact_id:stockId,stock:null,tender:legacyCard.tender,analysis:{positions:[],model:null,economics:{revenue:'100',cost:'40',profit:'60',currency:'RUB',margin_percent:'60',scope:'full',reason:null},coverage_reason:null,benefit_clamped:false,model_error:null}}})
 if(path.endsWith('/runs/'+rid))return route.fulfill({json:detail})
 if(path.endsWith('/runs')&&req.method()==='POST'){commands.push(req.postDataJSON());if(lose){lose=false;return route.abort('failed')}return route.fulfill({status:202,json:{run_id:rid,job_id:rid,state:'queued'}})}
 return route.fulfill({status:404,json:{error:{message:'Unexpected '+path}}})})
 await page.goto(origin+'/#/ai-tender-radar')
 const history=page.getByRole('complementary',{name:'История прогонов'})
 await history.getByRole('button',{name:/^Результаты до обновления/}).waitFor()
 assert.match(await history.getByRole('button').first().innerText(),/Результаты до обновления/)
 await history.getByRole('button',{name:/^Результаты до обновления/}).click()
 const title=page.getByRole('button',{name:'Архивные болты',exact:true});await title.waitFor();assert.equal(await title.locator('.ai-radar-metrics dt').filter({hasText:'Оценка ИИ'}).locator('..').locator('dd').innerText(),'Нет оценки')
 await title.focus();await page.keyboard.press('Enter');await page.getByRole('dialog').waitFor();await page.keyboard.press('Escape');await title.waitFor();assert.equal(await title.evaluate(el=>el===document.activeElement),true)
 await history.getByRole('button',{name:/^Крепёж ноябрь/}).click();await page.getByRole('button',{name:'Поставка болтов',exact:true}).waitFor()
 const results = page.locator('.run-results')
 const resultCards = results.locator('.ai-radar-card-list>.ai-radar-card')
 assert.equal(await results.getByRole('heading',{name:`Результаты ${detail.cards.length}`,exact:true}).count(), 1)
 assert.equal(await results.locator('.run-progress,.run-candidates,.run-snapshot,.run-audit-controls,.run-reference-format,.run-audit-report').count(), 0)
 assert.equal(await resultCards.count(), detail.cards.length)
 assert.match(await resultCards.first().innerText(), /Поставка болтов/)
 const metrics = resultCards.first().locator('.ai-radar-metrics')
 assert.deepEqual(await metrics.locator('dt').allTextContents(), ['Стоимость тендера', 'Соответствие продукции', 'Покрытие склада', 'Маржинальность', 'Оценка ИИ'])
 assert.match(await metrics.locator('dd').nth(0).innerText(), /500\s000 ₽/)
 assert.equal(await metrics.locator('dd').nth(1).innerText(), '100%')
 assert.equal(await metrics.locator('dd').nth(3).innerText(), '−10%')
 assert.equal(await metrics.locator('dd').nth(4).innerText(), '0')
 assert.equal(await results.locator('.ai-radar-card-list').evaluate(el => getComputedStyle(el).paddingLeft), '0px')
 if (width >= 800) assert.equal(Math.round((await history.boundingBox()).y), Math.round((await results.boundingBox()).y))
 assert.equal(await resultCards.first().locator('.radar-card-score').count(), 0)
 for (const [index, rounded, square] of [[0, 'borderTopLeftRadius', 'borderBottomLeftRadius'], [detail.cards.length - 1, 'borderBottomLeftRadius', 'borderTopLeftRadius']]) {
   const card = resultCards.nth(index)
   await card.hover()
   assert.notEqual(await card.evaluate((element, corner) => getComputedStyle(element)[corner], rounded), '0px')
   assert.equal(await card.evaluate((element, corner) => getComputedStyle(element)[corner], square), '0px')
 }
 await page.getByLabel('Сортировка карточек').selectOption('benefit')
 assert.match(await resultCards.first().innerText(), /Поставка гаек/)
 const search = page.getByRole('searchbox', {name:'Найти тендер'})
 await search.fill('болтов')
 assert.equal(await resultCards.count(), 1)
 assert.match(await resultCards.first().innerText(), /Поставка болтов/)
 await search.clear()
 await page.getByLabel('Сортировка карточек').selectOption('coverage')
 await page.getByRole('button',{name:'Фильтр',exact:true}).click()
 const filters = page.getByRole('region',{name:'Фильтр результатов'})
 assert.equal(await filters.getByRole('slider').count(), 6)
 assert.equal(await filters.getByRole('slider',{name:/ от$/}).count(), 1)
 const priceTrack = filters.locator('.radar-filter-price-track')
 assert.equal(await priceTrack.getByRole('slider').count(), 2)
 const bounds = await priceTrack.boundingBox()
 const priceMinimum = priceTrack.getByRole('slider',{name:'Стоимость тендера от'})
 await page.mouse.move(bounds.x + 8, bounds.y + bounds.height / 2)
 await page.mouse.down()
 await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2, {steps: 5})
 await page.mouse.up()
 assert.ok(Number(await priceMinimum.inputValue()) > 500000)
 assert.equal(await resultCards.count(), 3)
 const priceMaximum = priceTrack.getByRole('slider',{name:'Стоимость тендера до'})
 await page.mouse.move(bounds.x + bounds.width - 8, bounds.y + bounds.height / 2)
 await page.mouse.down()
 await page.mouse.move(bounds.x + bounds.width * .8, bounds.y + bounds.height / 2, {steps: 5})
 await page.mouse.up()
 assert.ok(Number(await priceMaximum.inputValue()) < 1500000)
 assert.equal(await resultCards.count(), 2)
 await filters.getByRole('button',{name:'Сбросить'}).click()
 await filters.getByLabel('Стадия закупки').selectOption('closed')
 await page.getByRole('button',{name:'Низкое покрытие',exact:true}).waitFor()
 assert.equal(await resultCards.count(), 1)
 await filters.getByRole('button',{name:'Сбросить'}).click()
 const slider = filters.getByRole('slider',{name:'Покрытие склада до'})
 await slider.focus()
 await page.keyboard.press('Home')
 for (let i = 0; i < 6; i++) await page.keyboard.press('ArrowRight')
 assert.equal(await slider.inputValue(), '6')
 assert.equal(await resultCards.count(), 1)
 assert.match(await resultCards.first().innerText(), /Низкое покрытие/)
 await filters.getByRole('button',{name:'Сбросить'}).click()
 await page.keyboard.press('Escape')
 assert.equal(await filters.count(), 0)
 await page.getByRole('button',{name:'Поставка болтов',exact:true}).click()
 const dialog=page.getByRole('dialog')
 await dialog.getByRole('button',{name:'Спросить'}).waitFor()
 const dialogText = await dialog.innerText()
 for (const removed of ['SIM-1','Данные полны','Высокий приоритет','Стресс-сценарий','из 100','Требования:','Цены:','Сроки:','fixture-model','Расчёт и план исполнения','Профиль для пересчёта','исторический снимок']) assert.equal(dialogText.includes(removed), false, removed)
 const dialogMetrics = dialog.locator('.ai-radar-metrics')
 assert.deepEqual(await dialogMetrics.locator('dt').allTextContents(), await metrics.locator('dt').allTextContents())
 assert.deepEqual(await dialogMetrics.locator('dd').allTextContents(), await metrics.locator('dd').allTextContents())
 for (const removed of ['Маржа полного заказа', 'Прибыль полного заказа', 'Основания ревью', ...boilerplate]) assert.equal(dialogText.includes(removed), false, removed)
 assert.match(dialogText, /Не рекомендую участвовать/)
 assert.doesNotMatch(dialogText, /Недостаточная маржа|unconfirmed|80\.327180301|Проверить вручную/)
 assert.equal(await dialog.getByLabel('Вопрос по тендеру').count(), 1)
 assert.ok(dialogText.indexOf('Вывод по расчёту') < dialogText.indexOf('Позиции закупки'))
 const position = dialog.locator('.ai-radar-position summary')
 assert.doesNotMatch(await position.innerText(), /Лот 1|Остаток/)
 assert.match(await position.innerText(), /Цена/)
 assert.equal(await position.locator('small').last().innerText(), 'Маржинальность')
 assert.equal(await position.locator('strong').last().innerText(), '+35,5%')
 assert.ok(await position.evaluate(element => parseFloat(getComputedStyle(element).paddingLeft) >= 16))
 assert.equal(await position.locator('small').first().evaluate(element => getComputedStyle(element, '::before').content), 'none')
 await position.click()
 await position.locator('..').locator('.ai-radar-position-body').waitFor()
 await page.keyboard.press('Escape')
 await page.getByRole('button',{name:'Поставка гаек',exact:true}).click()
 assert.match(await dialog.locator('.run-review').innerText(), /Рекомендую участвовать\. Заказ выполним с прибылью\./)
 assert.equal(await dialog.getByText('Проверить вручную', {exact:true}).count(), 0)
 await page.keyboard.press('Escape')
 await page.getByRole('button',{name:'Неподтверждённые гайки',exact:true}).click()
 assert.match(await dialog.locator('.run-review').innerText(), /Участие требует уточнений/)
 assert.match(await dialog.locator('.run-review').innerText(), /Уточнить стоимость доставки до места поставки\./)
 await page.keyboard.press('Escape')
 await page.getByRole('button',{name:'Новый анализ',exact:true}).click();const form=page.getByRole('dialog');await form.getByLabel('Название прогона').fill('Новая поставка')
 await form.getByLabel('Максимум подробных карточек').fill('50')
 await form.getByText(/Поисковых страниц: до 50/).waitFor()
 assert.equal(await form.getByLabel('Источник закупок').locator('option').count(),3)
 await form.getByLabel('Источник закупок').selectOption('file');await form.getByLabel('Файл закупок').waitFor();await form.getByLabel('Источник закупок').selectOption('kontur_api');assert.equal(await form.getByLabel('Файл закупок').count(),0);await form.getByLabel('Источник закупок').selectOption('test_api');await form.getByRole('button',{name:'Запустить анализ',exact:true}).click()
 await page.getByRole('button',{name:'Повторить сохранённую команду'}).waitFor();await page.reload();await page.getByRole('button',{name:'Повторить сохранённую команду'}).click();await page.getByRole('button',{name:'Поставка болтов',exact:true}).waitFor()
 assert.deepEqual(commands[0],commands[1]);assert.equal(commands[0].name,'Новая поставка');assert.equal(commands[0].source,'test_api');assert.equal(commands[0].detail_max,50);assert.equal(requested.some(path=>path.includes('/jobs/active')),false)
 assert.equal(await page.getByRole('navigation').getByRole('link',{name:'Радар ИИ',exact:true}).getAttribute('href'),'#/ai-tender-radar')
 if(process.env.BERVEL_SCREENSHOTS)await page.screenshot({path:process.env.BERVEL_SCREENSHOTS+'/unified-'+width+'.png',fullPage:true})
 await page.goto(origin+'/#/ai-radar-runs?run_id='+rid);await page.getByRole('button',{name:'Поставка болтов',exact:true}).waitFor()
}))

test('found tenders stay out of results until analysis starts',()=>withBrowser(1440,async(page,origin)=>{
 let stopped = false
 let cardState
 const first = {...candidates[0], brief: {OrderName: 'Кандидат 1', NotificationNumber: 'SEARCH-1', CreateDate: time},
   metrics: {price: '900000', currency: 'RUB', stage: 'accepting', catalog_match_percent: null}}
 const found = [first, ...Array.from({length: 2696}, (_, i) => ({
   id: `brief-${i + 2}`, brief: {OrderName: `Кандидат ${i + 2}`, CreateDate: time}, state: 'candidate', reasons: [],
 }))]
 await page.route('**/api/v0.1/**', route => {
   const path = new URL(route.request().url()).pathname
   if(path.endsWith('/settings/company')) return route.fulfill({json:{snapshot}})
   if(path.endsWith('/runs/history')) return route.fulfill({json:{entries:[{id:rid,kind:'run',title:'Крепёж ноябрь',created_at:time,state:'running',card_count:0}],next_offset:null}})
   if(path.endsWith('/runs/'+rid)) {
     const value = run()
     value.data = {state:stopped?'failed':'running',phase:'details',...(stopped?{stop_reason:'USER_STOPPED'}:{})}
     value.cards = cardState ? [{...card(1,cardState,0),candidate_id:first.id,tender:{title:first.brief.OrderName,number:first.brief.NotificationNumber,link:null}}] : []
     if(stopped) value.cards.push({...card(2,'analyzing',null),candidate_id:found[1].id,tender:{title:'Прерванная карточка',number:'STOPPED-2',link:null}})
     value.candidates = found
     return route.fulfill({json:value})
   }
   return route.fulfill({status:404})
 })
 await page.goto(origin+'/#/ai-tender-radar?run_id='+rid)
 await page.getByRole('heading',{name:/^Результаты/}).waitFor()
 assert.equal(await page.locator('.ai-radar-card').count(), 0)
 assert.equal(await page.getByRole('heading',{name:'Результаты 0',exact:true}).count(), 1)
 const row = page.locator('.ai-radar-card').filter({has: page.getByRole('heading',{name:'Кандидат 1',exact:true})})
 await page.getByRole('searchbox',{name:'Найти тендер'}).fill('SEARCH-1')
 assert.equal(await page.locator('.ai-radar-card').count(), 0)
 cardState = 'analyzing'
 await row.locator('time + [data-analysis="analyzing"]').waitFor()
 assert.equal(await row.locator('[data-analysis="analyzing"]').innerText(), 'В анализе')
 assert.equal(await row.count(), 1)
 await row.evaluate(node=>node.setAttribute('data-test-identity','same-row'))
 cardState = 'accepted'
 await row.locator('time + [data-analysis="complete"]').waitFor()
 assert.equal(await row.locator('[data-analysis="complete"]').innerText(), 'Проанализирован')
 assert.equal(await row.getAttribute('data-test-identity'), 'same-row')
 assert.equal(await row.locator('dd').last().innerText(), '0')
 await page.getByRole('searchbox',{name:'Найти тендер'}).fill('')
 stopped = true
 await page.getByText('Прогон остановлен. Полученные карточки сохранены.',{exact:true}).waitFor()
 assert.equal(await page.locator('.ai-radar-card').count(), 1)
 assert.equal(await page.getByText('Прерванная карточка',{exact:true}).count(), 0)
}))

test('All sources send their own metadata, JSON upload and unconfigured source error',()=>withBrowser(1440,async(page,origin)=>{
 const sent=[];let uploads=0
 await page.route('**/api/v0.1/**',route=>{const req=route.request(),path=new URL(req.url()).pathname
 if(path.endsWith('/settings/company'))return route.fulfill({json:{snapshot}})
 if(path.endsWith('/runs/history'))return route.fulfill({json:{entries:[],next_offset:null}})
 if(path.endsWith('/runs/legacy'))return route.fulfill({json:{entry:{id:'legacy',kind:'legacy',title:'Результаты до обновления',created_at:null,state:'complete',card_count:0},cards:[],next_offset:null}})
 if(path.endsWith('/artifacts')&&req.method()==='POST'){uploads++;return route.fulfill({status:201,json:{artifact_id:stockId}})}
 if(path.endsWith('/runs')&&req.method()==='POST'){sent.push(req.postDataJSON());if(sent.at(-1).source==='kontur_api')return route.fulfill({status:409,json:{error:{message:'Источник Контур.АПИ не настроен'}}});return route.fulfill({status:202,json:{run_id:rid,job_id:rid,state:'queued'}})}
 if(path.endsWith('/runs/'+rid))return route.fulfill({json:fixtureRun()})
 return route.fulfill({status:404,json:{error:{message:path}}})})
 await page.goto(origin+'/#/ai-tender-radar');await page.getByRole('button',{name:'Новый анализ',exact:true}).click();let modal=page.getByRole('dialog');await modal.getByLabel('Название прогона').fill('Контур ноябрь');await modal.getByLabel('Источник закупок').selectOption('kontur_api');await modal.getByRole('button',{name:'Запустить анализ'}).click();await page.getByRole('alert').getByText('Источник Контур.АПИ не настроен',{exact:false}).waitFor();assert.equal(sent[0].source,'kontur_api')
 await page.getByRole('button',{name:'Новый анализ',exact:true}).click();modal=page.getByRole('dialog');await modal.getByLabel('Название прогона').fill('Из файла');await modal.getByLabel('Источник закупок').selectOption('file');await modal.getByRole('button',{name:'Запустить анализ'}).click();await modal.getByText('Выберите файл закупок.',{exact:true}).waitFor();assert.equal(sent.length,1)
 await modal.getByLabel('Файл закупок').setInputFiles({name:'tenders.json',mimeType:'application/json',buffer:Buffer.from('[]')});await modal.getByRole('button',{name:'Запустить анализ'}).click();await page.getByRole('button',{name:'Поставка болтов',exact:true}).waitFor();assert.equal(uploads,1);assert.equal(sent[1].source,'file');assert.equal(sent[1].source_artifact_id,stockId);assert.equal(sent[1].name,'Из файла')
}))
for (const width of [1440,390]) test('New run sends selected stages '+width,()=>withBrowser(width,async(page,origin)=>{
 const previous=fixtureRun()
 let quotaReads=0
 previous.settings_snapshot_id=sid
 previous.data.purchase_stages=['commission','finished']
 await page.route('**/api/v0.1/**',route=>{
   const req=route.request(), path=new URL(req.url()).pathname
   if(path.endsWith('/settings/company'))return route.fulfill({json:{snapshot}})
   if(path.endsWith('/settings/kontur')){quotaReads++;return route.fulfill({json:{configured:true,limits:[
     {group:'search',remaining:200,maximum:200},{group:'purchase',remaining:72,maximum:200}]}})}
   if(path.endsWith('/runs/history'))return route.fulfill({json:{entries:[],next_offset:null}})
   if(path.endsWith('/runs/'+rid))return route.fulfill({json:previous})
   if(path.endsWith('/runs')&&req.method()==='POST')return route.fulfill({status:202,json:{run_id:rid,job_id:rid,state:'queued'}})
   return route.fulfill({status:404,json:{error:{message:path}}})
 })
 await page.goto(origin+'/#/ai-tender-radar?run_id='+rid)
 await page.getByRole('heading',{name:/^Результаты/}).waitFor()
 await page.getByRole('button',{name:'Новый анализ',exact:true}).click()
 const dialog=page.getByRole('dialog')
 const maximum=dialog.getByLabel('Максимум подробных карточек')
 await dialog.getByText('Тестовый API работает без квот.',{exact:true}).waitFor()
 assert.equal(await maximum.getAttribute('max'),null)
 await maximum.fill('1')
 await dialog.getByText(/Поисковых страниц: до 1\.$/).waitFor()
 assert.equal(quotaReads,0)
 await dialog.getByLabel('Источник закупок').selectOption('kontur_api')
 await dialog.getByText(/Доступно подробных запросов: 72/).waitFor()
 assert.equal(await maximum.getAttribute('max'),'72')
 assert.equal(quotaReads,1)
 await dialog.getByLabel('Источник закупок').selectOption('test_api')
 assert.equal(await dialog.getByRole('button',{name:'Максимум',exact:true}).count(),0)
 const dropdown=dialog.locator('.run-stage-select > summary')
 assert.equal(await dropdown.count(),1)
 assert.match(await dropdown.innerText(),/Подача заявок/)
 assert.equal(await dialog.getByRole('checkbox').count(),0)
 await dropdown.focus()
 await page.keyboard.press('Enter')
 const options=dialog.getByRole('group',{name:'Этап',exact:true})
 assert.equal(await options.getByRole('checkbox').count(),5)
 const open=options.getByRole('checkbox',{name:'Подача заявок'})
 assert.equal(await open.isChecked(),true)
 await dialog.getByLabel('Название прогона').fill('Сравнение этапов')
 await open.uncheck()
 assert.equal(await dialog.getByRole('button',{name:'Запустить анализ'}).isDisabled(),true)
 await options.getByRole('checkbox',{name:'Работа комиссии'}).check()
 await options.getByRole('checkbox',{name:'Завершены'}).check()
 await dropdown.click()
 assert.equal(await dialog.getByRole('checkbox').count(),0)
 assert.match(await dropdown.innerText(),/Выбрано 2/)
 await dialog.getByLabel('Максимум подробных карточек').fill('72')
 const submitted=page.waitForRequest(req=>req.method()==='POST'&&new URL(req.url()).pathname.endsWith('/runs'))
 await dialog.getByRole('button',{name:'Запустить анализ'}).click()
 await dialog.waitFor({state:'hidden'})
 const body=(await submitted).postDataJSON()
 assert.deepEqual(body.purchase_stages,['commission','finished'])
 assert.equal(body.detail_max,72)
}))

test('Deep links, paginated history/archive and preserved run operations',()=>withBrowser(1440,async(page,origin)=>{
 const sent=[];let reports=[],failed=true;const legacy={id:'legacy',kind:'legacy',title:'Результаты до обновления',created_at:time,state:'complete',card_count:2};let historyReads=0
 await page.route('**/api/v0.1/**',route=>{const req=route.request(),url=new URL(req.url()),path=url.pathname
 if(path.endsWith('/settings/company'))return route.fulfill({json:{snapshot}})
 if(path.endsWith('/runs/history')){historyReads++;return route.fulfill({json:url.searchParams.get('offset')==='20'?{entries:[{id:oldRid,kind:'run',title:'Предыдущий анализ',created_at:time,state:'complete',card_count:0}],next_offset:null}:{entries:[legacy,{id:rid,kind:'run',title:'Крепёж ноябрь',created_at:time,state:'failed',card_count:3}],next_offset:20}})}
 if(path.endsWith('/runs/legacy'))return route.fulfill({json:{entry:legacy,cards:url.searchParams.get('offset')==='50'?[{...legacyCard,job_id:qid,tender:{...legacyCard.tender,title:'Вторая архивная карточка'}}]:[legacyCard],next_offset:url.searchParams.get('offset')==='50'?null:50}})
 if(path.endsWith('/jobs/'+legacyId))return route.fulfill({json:{job_id:legacyId,state:'loaded',stock_artifact_id:stockId,stock:null,tender:legacyCard.tender,analysis:null}})
 if(req.method()==='POST'){const body=req.postDataJSON();sent.push({path,body});if(path.endsWith('/resume'))failed=false;if(path.endsWith('/question'))reports.push({job_id:qid,kind:'question',analysis_id:body.analysis_id,question:body.question,result:{...review,answer:'Ответ из исторического снимка'}});return route.fulfill({status:202,json:{job_id:qid,run_id:rid,state:'loaded'}})}
 if(path.endsWith('/runs/'+rid)){const value=fixtureRun();value.reports=reports;if(failed){value.data.state='failed';value.data.error={message:'Источник прервал ответ'}}return route.fulfill({json:value})}
 return route.fulfill({status:404,json:{error:{message:path}}})})
 await page.goto(origin+'/#/ai-radar-runs?run_id='+rid+'&analysis_id=analysis-1');let modal=page.getByRole('dialog');await modal.getByRole('button',{name:'Спросить'}).waitFor();await modal.getByLabel('Вопрос по тендеру').fill('Почему ноль?');await modal.getByRole('button',{name:'Спросить'}).click();await modal.getByText('Ответ из исторического снимка',{exact:true}).waitFor();await page.keyboard.press('Escape')
 await page.getByRole('alert').filter({hasText:'Источник прервал ответ'}).waitFor()
 const resume = page.getByRole('button',{name:'Продолжить с сохранённой точки'})
 await resume.click()
 await resume.waitFor({state:'hidden'})
 await page.getByRole('complementary').getByRole('button',{name:'Показать ещё',exact:true}).click();await page.getByRole('button',{name:/^Предыдущий анализ/}).waitFor();assert.ok(historyReads>=2)
 await page.getByRole('button',{name:/^Результаты до обновления/}).click();await page.getByRole('button',{name:'Показать ещё карточки'}).click();await page.getByRole('button',{name:'Вторая архивная карточка',exact:true}).waitFor()
 await page.goto(origin+'/#/ai-tender-radar?job_id='+legacyId);await page.getByRole('dialog').getByRole('heading',{name:'Архивные болты',exact:true}).waitFor();await page.keyboard.press('Escape')
 assert.deepEqual(sent.map(c=>c.path.split('/').at(-1)),['question','resume'])
}))
