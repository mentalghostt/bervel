const {test} = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const {pathToFileURL} = require('node:url')

const root = path.resolve(__dirname, '../..')
const rid = '11111111-1111-4111-8111-111111111111'
const base = {analysis_id: 'a', candidate_id: 'c', state: 'needs_data', evaluated_at: '2026-11-08T10:00:00Z',
  tender: null, analysis: {coverage_percent: null, benefit_percent: null,
    economics: {scope: null, revenue: null, cost: null}, positions: []},
  fulfillment: null, assessment: null, review: null}
const metrics = {price: null, currency: null, stage: 'unknown', catalog_match_percent: '0',
  catalog_match_confirmed: 0, catalog_match_unknown: 2, catalog_match_total: 2, catalog_match_incomplete: true}
const moduleAt = file => import(pathToFileURL(path.join(root, 'frontend/src', file)).href)

test('Zero confirmed technical rows with unknowns requires review', async () => {
  const {getReviewSummary} = await moduleAt('aiRadarReviewSummary.ts')
  assert.equal(getReviewSummary({...base, metrics}).decision, 'review')
  assert.equal(getReviewSummary({...base, metrics: {...metrics, catalog_match_unknown: 0,
    catalog_match_incomplete: false}}).decision, 'decline')
})

test('Run API accepts complete metadata and rejects impossible counters', async () => {
  const {readRun} = await moduleAt('aiRadarRunsApi.ts')
  const originalFetch = global.fetch
  const response = product => ({run_id: rid, settings_snapshot_id: '22222222-2222-4222-8222-222222222222',
    created_at: '2026-11-08T10:00:00Z', data: {state: 'complete', phase: 'complete'},
    cards: [{...base, metrics: product}], reports: []})
  try {
    global.fetch = async () => new Response(JSON.stringify(response(metrics)))
    assert.equal((await readRun(rid)).cards[0].metrics.catalog_match_unknown, 2)
    for (const invalid of [{catalog_match_unknown: -1}, {catalog_match_confirmed: 3},
      {catalog_match_total: '2'}, {catalog_match_incomplete: 'yes'}]) {
      global.fetch = async () => new Response(JSON.stringify(response({...metrics, ...invalid})))
      await assert.rejects(readRun(rid), /неожиданный формат/)
    }
  } finally {global.fetch = originalFetch}
})
