const {test} = require('node:test')
const assert = require('node:assert/strict')
const {chromium} = require('playwright')
const stockId = '11111111-1111-4111-8111-111111111111'
const time = '2026-11-05T09:00:00Z'
const product = {id:'p1', sku:'B1', name:'Болт М12', unit:'шт', attributes:{ГОСТ:'7798-70',Материал:'Сталь',Диаметр:'12 мм'}, enabled:true}
const fixture = () => ({stock_artifact_id:stockId, stock_sha256:'a'.repeat(64), products:[product], experimental_products:[], include_experimental:false, production:[], resources:[{group_id:'Линия',calendar:[0,1,2,3,4],free_capacity_by_day:{},confirmed_at:null}], scoring_policy:{}})
for (const width of [1440,390]) test(`official catalogue UI and independent experiment at ${width}`, async () => {
  const browser = await chromium.launch({executablePath:process.env.BERVEL_BROWSER_PATH,headless:true, channel:process.env.BERVEL_BROWSER_CHANNEL})
  try {
    const page = await browser.newPage({viewport:{width,height:1000}})
    let current = {id:'22222222-2222-4222-8222-222222222222',created_at:time,profile:fixture()}
    const sent=[]
    const errors=[]
    page.on('pageerror',e=>errors.push(e.message))
    await page.route('**/api/v0.1/**',route=>{
      const path = new URL(route.request().url()).pathname
      if (path.endsWith('/artifacts')) return route.fulfill({json:{artifacts:[{artifact_id:stockId,filename:'Каталог.csv',media_type:'text/csv',size:100,created_at:time}],next_cursor:null}})
      if (path.endsWith('/settings/company') && route.request().method()==='GET') return route.fulfill({json:{snapshot:current}})
      if (path.endsWith('/settings/company') && route.request().method()==='PUT') {
        const body=route.request().postDataJSON();sent.push(body);current={...current,profile:body.profile}
        return route.fulfill({json:{snapshot:current}})
      }
      return route.fulfill({status:404,json:{error:{message:'Unexpected fixture route'}}})
    })
    await page.goto((process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5179')+'/#/company-settings')
    const main=page.getByRole('main',{name:'Настройки предприятия',exact:true})
    await main.getByLabel('Название продукции B1',{exact:true}).waitFor()
    assert.equal(await main.locator('h1,.detail-kicker,.company-version').count(),0)
    assert.equal(await main.getByText(/Ранее производили|прежнего выпуска|Связь с известной продукцией/).count(),0)
    assert.equal(await main.locator('h2>span').count(),0)
    assert.equal(await main.getByLabel('Учитывать экспериментальную продукцию в новых прогонах').isChecked(),false)
    const row=main.getByRole('region',{name:'Таблица продукции',exact:true}).locator('tbody tr').first()
    const tops=await row.locator(':scope > td > input:not([type=checkbox]), :scope > td > button').evaluateAll(els=>els.map(el=>el.getBoundingClientRect().top))
    assert.ok(Math.max(...tops)-Math.min(...tops)<2,`row controls align: ${tops}`)
    const centers=await row.getByRole('checkbox').evaluate(input=>{
      const checkbox=input.getBoundingClientRect(),cell=input.closest('td').getBoundingClientRect()
      return [checkbox.x+checkbox.width/2-cell.x-cell.width/2,checkbox.y+checkbox.height/2-cell.y-cell.height/2]
    })
    assert.ok(centers.every(delta=>Math.abs(delta)<2),`activity checkbox is centered: ${centers}`)
    assert.equal(await main.locator('.company-table').first().locator('thead th').count(),4,'removed columns have no empty replacement')
    if (process.env.BERVEL_SCREENSHOTS) await page.screenshot({path:`${process.env.BERVEL_SCREENSHOTS}/company-v2-initial-${width}.png`,fullPage:true})
    await main.getByRole('columnheader',{name:'Активность',exact:true}).first().waitFor()
    await row.getByRole('button',{name:'Редактировать данные Болт М12',exact:true}).click()
    const editorCell=main.getByRole('region',{name:'Таблица продукции',exact:true}).locator('td[colspan="4"]')
    assert.ok(await editorCell.evaluate(el=>el.getBoundingClientRect().width >= el.closest('table').getBoundingClientRect().width-2))
    const grid = editorCell.locator('.company-product-attributes')
    const fieldTops = await grid.locator(':scope > label').evaluateAll(fields => fields.map(field => field.getBoundingClientRect().top))
    assert.equal(fieldTops.length, 4)
    if (width === 1440) {
      assert.deepEqual(fieldTops.slice(0, 3), Array(3).fill(fieldTops[0]))
      assert.ok(fieldTops[3] > fieldTops[0])
    } else {
      assert.equal(new Set(fieldTops).size, 4)
    }
    assert.equal(await main.getByLabel('Основание прежнего выпуска',{exact:true}).count(),0)
    await main.getByRole('button',{name:'Добавить возможность выпуска'}).click()
    const capabilityColumns = await editorCell.locator('.company-capability .company-fields').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length)
    assert.equal(capabilityColumns, width === 1440 ? 3 : 1)
    await main.getByLabel('Станок или линия',{exact:true}).selectOption('Линия')
    await main.getByLabel('Подготовка, рабочих дней').fill('0')
    await main.getByLabel('Выпуск за рабочий день').fill('120')
    await main.getByRole('button',{name:'Сохранить профиль',exact:true}).click()
    await main.getByRole('alert').filter({hasText:'требуют источника'}).waitFor()
    assert.equal(sent.length,0,'numeric capacity still needs a production source')
    assert.equal(await main.getByLabel('Когда подтверждено',{exact:true}).count(),0)
    await main.getByLabel('Основание производственных данных',{exact:true}).fill('Техкарта 12')
    await main.getByRole('button',{name:'Добавить экспериментальный продукт'}).click()
    await main.getByLabel('Название экспериментального продукта',{exact:true}).fill('Новый болт')
    await main.getByLabel('Единица экспериментального продукта',{exact:true}).fill('шт')
    const experiments=main.getByRole('region',{name:'Таблица экспериментальной продукции',exact:true})
    await experiments.getByLabel('Артикул (необязательно)',{exact:true}).fill('EXP-B2')
    await experiments.getByLabel('Новый атрибут',{exact:true}).fill('ГОСТ')
    await experiments.getByLabel('Значение',{exact:true}).fill('7798-70')
    await experiments.getByRole('button',{name:'Добавить атрибут',exact:true}).click()
    const experimentalGrid = experiments.locator('.company-product-attributes')
    const experimentalTops = await experimentalGrid.locator(':scope > label').evaluateAll(fields => fields.map(field => field.getBoundingClientRect().top))
    assert.equal(experimentalTops.length, 2)
    assert.equal(experimentalTops[0] === experimentalTops[1], width === 1440)
    await experiments.getByRole('button',{name:'Добавить возможность выпуска',exact:true}).click()
    await experiments.getByLabel('Станок или линия',{exact:true}).selectOption('Линия')
    await experiments.getByLabel('Подготовка, рабочих дней').fill('0')
    assert.equal(await main.getByLabel('Связанный продукт',{exact:true}).count(),0)
    await main.getByLabel('Включить эксперимент Новый болт',{exact:true}).check()
    await main.getByRole('button',{name:'Сохранить профиль',exact:true}).click()
    await main.getByRole('status').filter({hasText:'Профиль сохранён.'}).waitFor()
    assert.equal(sent[0].profile.include_experimental,false,'row switch does not enable global experimental participation')
    assert.deepEqual(sent[0].profile.products[0],product)
    assert.equal(Object.hasOwn(sent[0].profile.experimental_products[0],'product_id'),false)
    assert.equal(sent[0].profile.experimental_products[0].sku,'EXP-B2')
    assert.deepEqual(sent[0].profile.experimental_products[0].attributes,{ГОСТ:'7798-70'})
    assert.equal(sent[0].profile.production[1].product_id,sent[0].profile.experimental_products[0].id)
    await main.getByLabel('Учитывать экспериментальную продукцию в новых прогонах').check()
    await main.getByRole('button',{name:'Сохранить профиль',exact:true}).click()
    await main.getByRole('status').filter({hasText:'Профиль сохранён.'}).waitFor()
    assert.equal(sent[1].profile.include_experimental,true)
    await experiments.getByRole('button',{name:'Удалить эксперимент',exact:true}).click()
    await main.getByRole('button',{name:'Сохранить профиль',exact:true}).click()
    await main.getByRole('status').filter({hasText:'Профиль сохранён.'}).waitFor()
    assert.equal(sent[2].profile.experimental_products.length,0)
    assert.deepEqual(sent[2].profile.production,sent[1].profile.production.slice(0,1))
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
    assert.deepEqual(errors,[])
    if (process.env.BERVEL_SCREENSHOTS) await page.screenshot({path:`${process.env.BERVEL_SCREENSHOTS}/company-v2-${width}.png`,fullPage:true})
  } finally {await browser.close()}
})

test('legacy pending replay retains removed fields exactly', async () => {
  const browser=await chromium.launch({executablePath:process.env.BERVEL_BROWSER_PATH,headless:true,channel:process.env.BERVEL_BROWSER_CHANNEL})
  try {
    const page=await browser.newPage()
    const command={command_key:'44444444-4444-4444-8444-444444444444',base_snapshot_id:null,profile:{...fixture(),scoring_policy:{version:'score-v1',target_margin:'0.30',max_age_by_source:{stock:null},future_policy:{enabled:false,factors:['0',1,null]}},products:[{...product,manufactured_before:false,evidence:'warehouse:legacy'}],experimental_products:[{id:'e1',name:'Эксперимент',unit:'шт',attributes:{},enabled:false,product_id:'p1'}]}}
    await page.addInitScript(value=>sessionStorage.setItem('bervel.company-settings.pending.v1',JSON.stringify(value)),command)
    let sent
    await page.route('**/api/v0.1/**',route=>{
      if(new URL(route.request().url()).pathname.endsWith('/artifacts')) return route.fulfill({json:{artifacts:[],next_cursor:null}})
      if(route.request().method()==='GET') return route.fulfill({json:{snapshot:null}})
      sent=route.request().postDataJSON()
      return route.fulfill({json:{snapshot:{id:'22222222-2222-4222-8222-222222222222',created_at:time,profile:sent.profile}}})
    })
    await page.goto((process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5179')+'/#/company-settings')
    await page.getByRole('button',{name:'Повторить сохранение',exact:true}).click()
    await page.getByRole('status').filter({hasText:'Профиль сохранён.'}).waitFor()
    assert.deepEqual(sent,command)
  } finally {await browser.close()}
})
