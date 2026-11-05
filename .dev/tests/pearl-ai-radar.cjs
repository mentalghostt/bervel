const {test} = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const {createRequire} = require('node:module')
const frontend = path.resolve(__dirname, '../../frontend')
const frontendRequire = createRequire(path.join(frontend, 'package.json'))
const ts = frontendRequire('typescript')
for (const ext of ['.ts', '.tsx']) require.extensions[ext] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true}}).outputText, filename)
require.extensions['.css'] = () => {}
const React = frontendRequire('react')
const {renderToStaticMarkup} = frontendRequire('react-dom/server')
const {CardMetrics, CardDetails} = require(path.join(frontend, 'src/AiTenderRadarPage.tsx'))
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props))
const economics = {revenue:null,cost:null,currency:null,profit:null,margin_percent:null,status:'unknown',reason:'TENDER_PRICE_UNKNOWN'}
const card = {coverage_percent:null,position_coverage_percent:null,benefit_percent:null,benefit_reason:'TENDER_PRICES_NOT_DISCLOSED'}
test('unknown metrics remain unknown, not zero', () => {
  const html = render(CardMetrics, {card})
  assert.equal((html.match(/data-known="false"/g)||[]).length, 2)
  assert.doesNotMatch(html, />0%/)
  assert.match(html, /Не указаны цены в тендере/)
  assert.match(html, />N\/A<\/dd>/)
})
test('partial benefit names the confirmed volume and displays a percentage', () => {
  const html = render(CardMetrics, {card:{...card,benefit_percent:'30',benefit_reason:null,benefit_scope:'confirmed'}})
  assert.match(html, /по подтверждённому объёму/)
  assert.match(html, />\+30%<\/dd>/)
})
test('partial source coverage is explicitly qualified', () => {
  const html = render(CardMetrics, {card:{...card,position_coverage_percent:'33.333'}})
  assert.match(html, /по раскрытым строкам/)
  assert.match(html, /33,3%/)
})
test('known zero and losses retain their meaning', () => {
  const html = render(CardMetrics, {card:{...card,coverage_percent:'0',benefit_percent:'-12.5'}})
  assert.match(html, />0%/)
  assert.match(html, /data-loss="true"/)
  assert.match(html, /-12,5%/)
})
test('all server stock states and missing-data reasons remain visible', () => {
  const job = {stock_artifact_id:'stock-1',stock:null,tender:{limitations:[]},analysis:{economics,status:'insufficient_data',positions:['full','partial','none','unconfirmed'].map((stock_status,i)=>({id:`1:${i}`,lot:1,name:`Position ${i}`,unit:'шт',required:'10',covered:'0',shortage:'10',stock_status,stock_reason:'MATCH_NOT_CONFIRMED',economics,matches:[],allocations:[]}))}}
  const html = render(CardDetails, {job})
  for (const label of ['Есть полностью','Есть частично','Нет остатка','Не подтверждено','Совпадение подтверждено не для всех возможных вариантов.','Контур не раскрыл цену позиции.']) assert.ok(html.includes(label), label)
  assert.equal((html.match(/class="ai-radar-position"/g)||[]).length, 4)
})
test('absent analysis has an explicit fallback', () => {
  assert.match(render(CardDetails,{job:{analysis:null,tender:null}}), /Результат анализа недоступен/)
})
