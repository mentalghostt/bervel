const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs')
const directory = path.resolve(process.env.BERVEL_TEST_ARTIFACT_ROOT||path.resolve(__dirname, '../local/presales-pipeline-2026-11-10/frontend'))
fs.mkdirSync(directory, {recursive: true})
process.env.TMP = directory; process.env.TEMP = directory
const {chromium} = require('playwright')
const stamp = '2026-11-10T09:00:00Z'
const settings = {target_margin_percent: '50', special_min_margin_percent: '70', seller_contacts: '', offer_terms: ''}
const request = {request_id: 'request-1', title: 'Крепёж для цеха № 4', client: 'АО Север', created_at: stamp, updated_at: stamp, current_revision_id: 'revision-1', state: 'complete', phase: 'complete'}
const line = {line_id: 'line-1', name: 'Болт М12', quantity: '10', unit: 'шт', requirements: [{name: 'ГОСТ', value: '7798-70'}], evidence: [{filename: 'Заявка.xlsx', location: {sheet: 'Заказ', row: 5}, text: 'Болт М12 — 10 шт'}], client_unit_price: null, currency: 'RUB', supply_status: 'partial_or_manufacture', needs_review: true, reasons: ['Подтвердите аналог'], candidates: [{sku: 'B-12', name: 'Болт М12 оцинкованный', differences: ['Покрытие: цинк'], evidence: 'Каталог предприятия'}], selected_sku: null, stock_quantity: '4', production_quantity: '6', offered_quantity: '10', ready_at: null, unit_cost: null, unit_price: null, line_total: null, margin_percent: null, offers: [], special_order: null}
const revision = {revision_id: 'revision-1', request_id: request.request_id, revision_number: 1, parent_revision_id: null, created_at: stamp, evaluated_at: stamp, state: 'complete', phase: 'complete', settings_snapshot_id: 'settings-1', company_snapshot_id: 'company-1', sources: [{artifact_id: 'file-1', filename: 'Заявка.xlsx', sha256: 'abc'}], extracted: {title: request.title, client: request.client, requested_deadline: null, lists: [{list_id: 'list-1', artifact_id: 'file-1', filename: 'Заявка.xlsx', complete: true}, {list_id: 'list-2', artifact_id: 'file-2', filename: 'Уточнение.txt', complete: false}], primary_list_id: 'list-1', warnings: ['Количество в уточнении отличается'], complete: false, client_terms: {}}, metrics: {product_match_percent: '50', stock_coverage_percent: null, product_match_incomplete: true, stock_coverage_incomplete: true}, review: {text: 'Потребуется подтвердить покрытие аналога и срок производства.', issues: ['Дата клиента не указана']}, score: 0, model_error: null, offer: {number: 'КП-001', date: '2026-11-10', client: request.client, seller_contacts: 'BERVEL · Отдел продаж', offer_terms: 'Условия по согласованию', lines: [{name: 'Гайка М12', quantity: '20', unit: 'шт', currency: 'RUB', unit_price: '10.00', line_total: '200.00'}], totals: {RUB: '200.00'}, clarifications: [{name: 'Болт М12', reason: 'Подтвердите аналог'}], unsupported: [{name: 'Шайба специальная', reason: 'Нет подтверждённого исполнения'}]}, artifacts: {pdf: 'pdf-1', docx: 'docx-1'}, warnings: [], decisions: {}, job: null}
function createState() {return {settings: {snapshot_id: 'settings-1', settings: {...settings}, requires_save: false}, request: {...request}, revision: structuredClone(revision), lines: [structuredClone(line)], writes: [], reads: [], failCommand: false, historyMore: false, linesMore: false}}
async function fixture(page, state) {
 await page.route('**/api/v0.1/**', async route => {
  const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method()
  if (method === 'GET') state.reads.push(p + url.search)
  if (p === '/api/v0.1/presales/settings') {
   if (method === 'GET') return route.fulfill({json: state.settings})
   state.writes.push({path: p, body: req.postDataJSON()}); if (state.failCommand) return route.abort()
   state.settings = {snapshot_id: 'settings-2', settings: req.postDataJSON().settings, requires_save: false}; return route.fulfill({json: state.settings})
  }
  if (p === '/api/v0.1/presales/requests' && method === 'GET') return route.fulfill({json: {items: url.searchParams.get('cursor') === '20' ? [{...state.request, request_id: 'request-2', title: 'Вторая заявка'}] : [state.request], next_cursor: state.historyMore && url.searchParams.get('cursor') !== '20' ? 20 : null}})
  if (p.startsWith('/api/v0.1/presales/') && method === 'POST') {
   const body = req.postDataJSON(); state.writes.push({path: p, body})
   if (state.failCommand) return route.abort()
   if (p.endsWith('/revisions')) {
    state.request.current_revision_id = 'revision-2'
    for (const [lineId, approval] of Object.entries(body.decisions?.approvals || {})) {
     state.revision.decisions.approvals ||= {}
     if (approval === null) delete state.revision.decisions.approvals[lineId]
     else state.revision.decisions.approvals[lineId] = {...state.revision.decisions.approvals[lineId], ...approval}
    }
   }
   return route.fulfill({json: {request_id: 'request-1', revision_id: p.endsWith('/revisions') ? 'revision-2' : 'revision-1', job_id: 'job-1', state: 'queued'}})
  }
  if (/\/revisions\/[^/]+\/lines$/.test(p)) {
   const listId = url.searchParams.get('list_id'), cursor = url.searchParams.get('cursor')
   if (listId) {
    const original = {line_id: 'source-line-1', name: 'Болт М12', quantity: listId === 'list-1' ? '10' : '12', unit: 'шт', requirements: [{name: 'Покрытие', value: listId === 'list-1' ? 'Без покрытия' : 'Цинк'}], evidence: [{filename: listId === 'list-1' ? 'Заявка.xlsx' : 'Уточнение.txt', location: {row: 5}, excerpt: listId === 'list-1' ? 'Болт М12 10 шт без покрытия' : 'Болт М12 12 шт цинк'}], client_unit_price: null, currency: null}
    return route.fulfill({json: {items: cursor === '50' ? [{...original, line_id: 'source-line-2', name: 'Шайба М12', quantity: '24'}] : [original], next_cursor: listId === 'list-1' && cursor !== '50' ? 50 : null}})
   }
   return route.fulfill({json: {items: cursor === '50' ? [{...state.lines[0], line_id: 'line-2', name: 'Гайка М12'}] : state.lines, next_cursor: state.linesMore && cursor !== '50' ? 50 : null}})
  }
  if (/\/revisions\/[^/]+$/.test(p)) return route.fulfill({json: {...state.revision, revision_id: p.split('/').at(-1)}})
  if (p === '/api/v0.1/presales/requests/request-1') return route.fulfill({json: {request: state.request, revisions: [{revision_id: 'revision-1', revision_number: 1, created_at: stamp, state: 'complete'}, {revision_id: 'revision-2', revision_number: 2, created_at: stamp, state: 'complete'}], job: state.revision.job}})
  if (p === '/api/v0.1/artifacts') return route.fulfill({json: {artifacts: url.searchParams.get('cursor') === '20' ? [{artifact_id: 'file-2', filename: 'Уточнение.txt', media_type: 'text/plain', size: 50, created_at: stamp}] : [{artifact_id: 'file-1', filename: 'Заявка.xlsx', media_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', size: 500, created_at: stamp}], next_cursor: url.searchParams.get('cursor') === '20' ? null : 20}})
  if (/\/artifacts\/[^/]+\/preview$/.test(p)) return route.fulfill({json: {kind: 'text', text: 'Оригинал заявки: болт М12', media_type: 'text/plain', next_cursor: null, columns: [], rows: [], total_rows: 0, notice: ''}})
  if (/\/artifacts\/[^/]+\/content$/.test(p)) return route.fulfill({body: Buffer.from('file contents'), contentType: 'application/octet-stream'})
  if (p === '/api/v0.1/settings/model') return route.fulfill({json: {provider: 'mox', configured: true, api: {base_url: '', model: '', api_key_configured: false}, mox: {base_url: '', model: 'gpt-6.1-sol', credentials_available: true}}})
  return route.fulfill({status: 404, json: {error: {message: 'Fixture route missing: ' + p}}})
 })
}
async function browser(width, callback) {
 const instance = await chromium.launch({executablePath:process.env.BERVEL_BROWSER_PATH,headless: true, channel:process.env.BERVEL_BROWSER_CHANNEL})
 try {const context = await instance.newContext({viewport: {width, height: 1000}}), page = await context.newPage(); page.setDefaultTimeout(6000); const errors = []; page.on('pageerror', error => errors.push(error.message)); await callback(page, context); assert.deepEqual(errors, []); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)} finally {await instance.close()}
}
const base = process.env.BERVEL_FRONTEND_URL || 'http://127.0.0.1:5193'
module.exports = {assert, path, directory, stamp, settings, request, line, revision, createState, fixture, browser, base}
