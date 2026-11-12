const {test} = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const {pathToFileURL} = require('node:url')
const root = path.resolve(__dirname, '../..')
const moduleAt = file => import(pathToFileURL(path.join(root, 'frontend/src', file)).href)
const rid = '11111111-1111-4111-8111-111111111111'
const base = {analysis_id: 'a', candidate_id: 'c', state: 'failed', evaluated_at: '2026-11-08T10:00:00Z',
  tender: null, analysis: null, fulfillment: null, assessment: null, review: null,
  opportunity: {score: 99, basis: 'unknown', known_share: null, margin_percent: null}}

test('AI score reads only saved comparative rating and missing is not zero', async () => {
  const {resultScore} = await moduleAt('aiRadarReviewSummary.ts')
  assert.equal(resultScore(base), null)
  assert.equal(resultScore({...base, rating: {status: 'complete', score: 92}}), 92)
  assert.equal(resultScore({...base, rating: {status: 'complete', score: 0}}), 0)
  for (const status of ['pending', 'running', 'unknown', 'failed', 'stale']) {
    assert.equal(resultScore({...base, rating: {status, score: null}}), null)
  }
})

test('Run parser validates rating state and score without accepting fabricated numeric unknowns', async () => {
  const {run:parse} = await moduleAt('aiRadarRunsApi.ts')
  const response = rating => ({run_id: rid, settings_snapshot_id: '22222222-2222-4222-8222-222222222222',
    created_at: '2026-11-08T10:00:00Z', data: {state: 'complete', phase: 'complete'},
    cards: [{...base, rating}], reports: []})
  assert.equal(parse(response({status: 'complete', score: 92}),true).cards[0].rating.score, 92)
  for (const rating of [{status: 'complete', score: null}, {status: 'unknown', score: 0},
    {status: 'invented', score: null}, {status: 'complete', score: true}, {status: 'complete', score: 101}]) {
    assert.throws(()=>parse(response(rating),true), /неожиданный формат/)
  }
})
