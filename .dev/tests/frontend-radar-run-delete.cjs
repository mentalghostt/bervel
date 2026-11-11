const {test} = require('node:test')
const assert = require('node:assert/strict')
const {chromium} = require('playwright')

const origin = process.env.BERVEL_FRONTEND_URL || 'http://frontend'
const now = '2026-11-06T09:00:00Z'
const archive = {id: 'legacy', kind: 'legacy', title: 'Результаты до обновления', created_at: now, state: 'complete', card_count: 179}
const removable = {id: '11111111-1111-4111-8111-111111111111', kind: 'run', title: 'Проверка крепежа', created_at: now, state: 'complete', card_count: 12}
const active = {id: '22222222-2222-4222-8222-222222222222', kind: 'run', title: 'Текущий поиск', created_at: now, state: 'running', card_count: 3}
const snapshotId = '33333333-3333-4333-8333-333333333333'

for (const width of [1440, 390]) {
  test(`run deletion confirms the chosen entry and refreshes history at ${width}px`, async () => {
    const browser = await chromium.launch({headless: true})
    try {
      const page = await browser.newPage({viewport: {width, height: 950}})
      page.setDefaultTimeout(10000)
      const deletes = []
      const archiveDeletes = []
      const stops = []
      const missingReads = []
      let removed = false
      let activeStopped = false
      let progressKnown = false
      function activeEntry() {
        if (activeStopped) return {...active, state: 'failed', stop_reason: 'USER_STOPPED'}
        return {...active, stop_requested: stops.length > 0}
      }
      await page.route('**/api/v0.1/**', route => {
        const request = route.request()
        const {pathname} = new URL(request.url())
        if (pathname.endsWith('/settings/company')) return route.fulfill({json: {snapshot: null}})
        if (pathname.endsWith('/runs/history')) return route.fulfill({json: {
          entries: [...(archiveDeletes.length ? [] : [archive]), ...(removed ? [] : [removable]), activeEntry()], next_offset: null,
        }})
        if (pathname.endsWith('/runs/legacy') && request.method() === 'DELETE') {
          archiveDeletes.push(request.headers()['x-bervel-web'])
          return route.fulfill({status: 204})
        }
        if (pathname.endsWith('/runs/legacy')) return route.fulfill({json: {entry: archive, cards: [], next_offset: null}})
        if (pathname.endsWith('/runs/' + active.id + '/stop') && request.method() === 'POST') {
          stops.push(request.headers()['x-bervel-web'])
          return route.fulfill({status: 204})
        }
        if (pathname.endsWith('/runs/' + removable.id) && request.method() === 'DELETE') {
          deletes.push(request.headers()['x-bervel-web'])
          if (deletes.length === 1) return route.fulfill({status: 409, json: {error: {message: 'Дождитесь завершения связанных заданий.'}}})
          removed = true
          return route.fulfill({status: 204})
        }
        for (const entry of [removable, activeEntry()]) {
          if (!pathname.endsWith('/runs/' + entry.id)) continue
          if (entry === removable && removed) {
            missingReads.push(pathname)
            return route.fulfill({status: 404, json: {error: {message: 'Прогон удалён.'}}})
          }
          return route.fulfill({json: {run_id: entry.id, settings_snapshot_id: snapshotId, created_at: now,
            data: {name: entry.title, state: entry.state, phase: entry.state === 'running' ? progressKnown ? 'details' : 'search' : entry.state,
              ...(entry.state === 'running' ? {effective_budgets: {detail: 6}, detail_attempts: 3} : {}),
              stop_requested: entry.stop_requested, stop_reason: entry.stop_reason},
            cards: Array.from({length: entry.card_count}, (_, index) => ({
              analysis_id: 'analysis-' + index, candidate_id: 'candidate-' + index, state: 'irrelevant',
              evaluated_at: now, tender: null, analysis: null, fulfillment: null, assessment: null, review: null,
            })), candidates: Array.from({length: 5}, (_, index) => ({
              id: 'pending-' + index, brief: {}, state: 'candidate', reasons: [],
            })), reports: []}})
        }
        return route.fulfill({status: 404})
      })

      await page.goto(origin + '/#/ai-tender-radar?run_id=' + removable.id)
      const history = page.getByRole('complementary', {name: 'История прогонов'})
      const title = history.getByRole('button', {name: new RegExp('^' + removable.title)})
      await title.waitFor()
      const remove = history.getByRole('button', {name: 'Удалить прогон ' + removable.title, exact: true})
      assert.equal(await remove.count(), 1)
      const archiveRemove = history.getByRole('button', {name: 'Удалить архив ' + archive.title, exact: true})
      assert.equal(await archiveRemove.count(), 1)
      assert.equal(await history.getByRole('button', {name: 'Удалить прогон ' + active.title, exact: true}).count(), 0)
      const stop = history.getByRole('button', {name: 'Остановить прогон ' + active.title, exact: true})
      await stop.waitFor()
      const progress = history.getByRole('progressbar', {name: 'Прогресс анализа ' + active.title})
      await progress.waitFor()
      assert.equal(await progress.getAttribute('aria-valuenow'), null)
      progressKnown = true
      await progress.and(page.locator('[aria-valuenow="50"]')).waitFor()
      const circle = await progress.boundingBox()
      const square = await stop.boundingBox()
      assert.ok(circle.width > square.width && circle.x < square.x && circle.y < square.y)
      await remove.click()
      const dialog = page.getByRole('dialog', {name: 'Удалить прогон навсегда?'})
      await dialog.getByText(removable.title, {exact: true}).waitFor()
      assert.match(await dialog.innerText(), /12/)
      await dialog.getByRole('button', {name: 'Отмена', exact: true}).click()
      assert.deepEqual(deletes, [])
      assert.equal(await title.getAttribute('aria-pressed'), 'true')

      await remove.click()
      const confirm = dialog.getByRole('button', {name: 'Удалить навсегда', exact: true})
      await confirm.click()
      await dialog.getByRole('alert').getByText('Дождитесь завершения связанных заданий.', {exact: true}).waitFor()
      await confirm.click()
      await dialog.waitFor({state: 'hidden'})
      await title.waitFor({state: 'detached'})
      assert.deepEqual(deletes, ['1', '1'])
      assert.ok(!page.url().includes(removable.id))
      assert.equal(await history.getByRole('button', {name: new RegExp('^' + archive.title)}).count(), 1)
      assert.equal(await history.getByRole('button', {name: new RegExp('^' + active.title)}).count(), 1)
      await page.reload()
      await history.getByRole('button', {name: new RegExp('^' + archive.title)}).waitFor()
      assert.deepEqual(missingReads, [])
      await stop.click()
      const stopping = history.getByRole('button', {name: 'Останавливаем прогон ' + active.title, exact: true})
      await stopping.waitFor()
      assert.equal(await stopping.isDisabled(), true)
      assert.deepEqual(stops, ['1'])
      activeStopped = true
      const activeTitle = history.getByRole('button', {name: new RegExp('^' + active.title)})
      if (await activeTitle.getAttribute('aria-pressed') !== 'true') await activeTitle.click()
      await history.getByRole('button', {name: 'Удалить прогон ' + active.title, exact: true}).waitFor()
      assert.match(await activeTitle.innerText(), /Остановлен/)
      assert.equal(await progress.count(), 0)
      await page.goto(origin + '/#/ai-tender-radar?run_id=legacy')
      await archiveRemove.click()
      const archiveDialog = page.getByRole('dialog', {name: 'Удалить архив навсегда?'})
      assert.match(await archiveDialog.innerText(), /179/)
      await archiveDialog.getByRole('button', {name: 'Удалить навсегда', exact: true}).click()
      await archiveRemove.waitFor({state: 'detached'})
      assert.deepEqual(archiveDeletes, ['1'])
      assert.ok(!page.url().includes('run_id=legacy'))
      assert.equal(await activeTitle.count(), 1)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    } finally {
      await browser.close()
    }
  })
}

test('deleting the last run keeps the empty selection after reload', async () => {
  const browser = await chromium.launch({headless: true})
  try {
    const page = await browser.newPage()
    page.setDefaultTimeout(10000)
    let removed = false
    const unexpected = []
    await page.route('**/api/v0.1/**', route => {
      const request = route.request()
      const {pathname} = new URL(request.url())
      if (pathname.endsWith('/settings/company')) return route.fulfill({json: {snapshot: null}})
      if (pathname.endsWith('/runs/history')) return route.fulfill({json: {entries: removed ? [] : [removable], next_offset: null}})
      if (pathname.endsWith('/runs/' + removable.id) && request.method() === 'DELETE') {
        removed = true
        return route.fulfill({status: 204})
      }
      if (pathname.endsWith('/runs/' + removable.id) && !removed) return route.fulfill({json: {
        run_id: removable.id, settings_snapshot_id: snapshotId, created_at: now,
        data: {name: removable.title, state: 'complete', phase: 'complete'}, cards: [], reports: [],
      }})
      unexpected.push(pathname)
      return route.fulfill({status: 404})
    })
    await page.goto(origin + '/#/ai-tender-radar?run_id=' + removable.id)
    await page.getByRole('button', {name: 'Удалить прогон ' + removable.title, exact: true}).click()
    await page.getByRole('button', {name: 'Удалить навсегда', exact: true}).click()
    await page.getByText('Прогонов пока нет.', {exact: true}).waitFor()
    await page.reload()
    await page.getByText('Прогонов пока нет.', {exact: true}).waitFor()
    await page.getByRole('heading', {name: 'Результаты появятся здесь', exact: true}).waitFor()
    assert.deepEqual(unexpected, [])
  } finally {
    await browser.close()
  }
})
