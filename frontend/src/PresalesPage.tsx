import { useEffect, useRef, useState } from 'react'
import { usePresalesCommands } from './PresalesCommands'
import PresalesCommandNotice from './PresalesCommandNotice'
import PresalesFilePicker from './PresalesFilePicker'
import PresalesReview from './PresalesReview'
import PresalesSettings from './PresalesSettings'
import { presalesDate, presalesState } from './presalesApi'
import WorkspaceDialog from './WorkspaceDialog'
import { listPresalesLines, listPresalesRequests, readPresalesRequest, readPresalesRevision, readPresalesSettings } from './presalesApi'
import type { PresalesDecisionPatch, PresalesLine, PresalesReceipt, PresalesRequest, PresalesRevision, PresalesSettingsSnapshot, PresalesSummary } from './presalesApi'
import './Presales.css'

const isActive = (state: string | undefined) => state === 'queued' || state === 'running'
const phaseLabels: Record<string, string> = { queued: 'Ожидает обработки', read_sources: 'Читаем исходные документы', extract: 'Извлекаем позиции', match: 'Сравниваем с возможностями предприятия', draft: 'Готовим черновик предложения', export: 'Формируем PDF и DOCX', intake: 'Разбираем документы', extracting: 'Извлекаем позиции', extraction: 'Извлекаем позиции', analysis: 'Сравниваем с возможностями предприятия', analyzing: 'Анализируем заявку', offer: 'Готовим предложение', exports: 'Формируем PDF и DOCX', complete: 'Анализ завершён', stopped: 'Работа приостановлена', failed: 'Нужно проверить ошибку' }
function navigate(request: string, revision: string) { window.location.hash = `/presales?request=${encodeURIComponent(request)}&revision=${encodeURIComponent(revision)}` }
export default function PresalesPage({ initialSettingsOpen = false }: { initialSettingsOpen?: boolean }) {
  const params = new URLSearchParams(window.location.hash.split('?')[1] || '')
  const requestId = params.get('request') || '', revisionId = params.get('revision') || ''
  const [history, setHistory] = useState<PresalesSummary[]>([]), [historyCursor, setHistoryCursor] = useState<number | null>(0)
  const [historyBusy, setHistoryBusy] = useState(true), [historyError, setHistoryError] = useState('')
  const [detail, setDetail] = useState<PresalesRequest | null>(null), [revision, setRevision] = useState<PresalesRevision | null>(null)
  const [lines, setLines] = useState<PresalesLine[]>([]), [linesCursor, setLinesCursor] = useState<number | null>(null), [linesBusy, setLinesBusy] = useState(false)
  const [settings, setSettings] = useState<PresalesSettingsSnapshot | null>(null), [settingsError, setSettingsError] = useState('')
  const [readError, setReadError] = useState(''), [refresh, setRefresh] = useState(0)
  const [picker, setPicker] = useState(false), [reanalyze, setReanalyze] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(initialSettingsOpen)
  const historyFlight = useRef(false), linesFlight = useRef(false), mounted = useRef(false)
  const loadedPages = useRef<number[]>([0])
  const command = usePresalesCommands((pending, result) => {
    if (pending.path === '/settings') { setSettings(result as PresalesSettingsSnapshot); return }
    const receipt = result as PresalesReceipt
    setPicker(false); setReanalyze(false)
    if (receipt.request_id === requestId && receipt.revision_id === revisionId) setRefresh(value => value + 1)
    else navigate(receipt.request_id, receipt.revision_id)
  })
  async function loadHistory(cursor: number) {
    if (historyFlight.current) return
    historyFlight.current = true; setHistoryBusy(true); setHistoryError('')
    try { const page = await listPresalesRequests(cursor); if (mounted.current) { setHistory(old => cursor === 0 ? page.items : [...old, ...page.items.filter(item => !old.some(previous => previous.request_id === item.request_id))]); setHistoryCursor(page.next_cursor) } }
    catch (reason) { if (mounted.current) setHistoryError(reason instanceof Error ? reason.message : 'Не удалось загрузить историю.') }
    finally { historyFlight.current = false; if (mounted.current) setHistoryBusy(false) }
  }
  useEffect(() => {
    mounted.current = true
    historyFlight.current = true
    listPresalesRequests(0).then(page => { if (mounted.current) { setHistory(page.items); setHistoryCursor(page.next_cursor) } }).catch(reason => { if (mounted.current) setHistoryError(reason.message) }).finally(() => { historyFlight.current = false; if (mounted.current) setHistoryBusy(false) })
    readPresalesSettings().then(value => { if (mounted.current) setSettings(value) }).catch(reason => { if (mounted.current) setSettingsError(reason.message) })
    return () => { mounted.current = false }
  }, [])
  useEffect(() => {
    if (!requestId) return
    let active = true, timer: ReturnType<typeof setTimeout> | undefined
    async function update() {
      try {
        const info = await readPresalesRequest(requestId)
        if (!active) return
        if (!revisionId) { navigate(requestId, info.request.current_revision_id); return }
        const value = await readPresalesRevision(requestId, revisionId)
        const pages = await Promise.all(loadedPages.current.map(cursor => listPresalesLines(requestId, revisionId, cursor)))
        if (!active) return
        setDetail(info); setRevision(value); setReadError('')
        setHistory(old => old.map(item => item.request_id === info.request.request_id ? info.request : item))
        setLines(pages.flatMap(page => page.items).filter((line, index, all) => all.findIndex(other => other.line_id === line.line_id) === index))
        setLinesCursor(pages.at(-1)?.next_cursor ?? null)
        const job = info.request.current_revision_id === revisionId ? info.job || value.job : value.job
        if (isActive(job?.state) || isActive(value.state)) timer = setTimeout(() => void update(), 1800)
      } catch (reason) {
        if (active) { setReadError(reason instanceof Error ? reason.message : 'Не удалось обновить заявку.'); timer = setTimeout(() => void update(), 3000) }
      }
    }
    void update()
    return () => { active = false; clearTimeout(timer) }
  }, [requestId, revisionId, refresh])
  async function moreLines() {
    if (linesCursor === null || linesFlight.current) return
    const cursor = linesCursor
    linesFlight.current = true; setLinesBusy(true)
    try {
      const page = await listPresalesLines(requestId, revisionId, cursor)
      if (mounted.current) { if (!loadedPages.current.includes(cursor)) loadedPages.current.push(cursor); setLines(old => [...old, ...page.items.filter(item => !old.some(previous => previous.line_id === item.line_id))]); setLinesCursor(page.next_cursor) }
    } catch (reason) { if (mounted.current) setReadError(reason instanceof Error ? reason.message : 'Не удалось получить позиции.') }
    finally { linesFlight.current = false; if (mounted.current) setLinesBusy(false) }
  }
  const current = detail?.request.current_revision_id === revisionId
  const job = current ? detail?.job || revision?.job : revision?.job
  const steps = job?.progress.completed_steps ?? job?.progress.completed ?? job?.progress.processed
  const total = job?.progress.total
  const visibleProgress = steps === undefined ? '' : `${job?.progress.completed_steps === undefined ? 'Обработано' : 'Сохранено шагов'}: ${String(steps)}${total === undefined ? '' : ` из ${String(total)}`}`
  const active = isActive(job?.state) || isActive(current ? detail?.request.state : revision?.state)
  const editable = !!current && !active && !command.pending
  const blocked = command.busy || !!command.pending
  const requiresSettings = !settings || settings.requires_save || !settings.snapshot_id
  function closeSettings() {
    setSettingsOpen(false)
    command.syncPending()
    if (initialSettingsOpen) window.location.hash = '/presales'
    readPresalesSettings().then(value => { if (mounted.current) { setSettings(value); setSettingsError('') } }).catch(reason => { if (mounted.current) setSettingsError(reason.message) })
  }
  function decisions(value: PresalesDecisionPatch) { void command.run(`/requests/${encodeURIComponent(requestId)}/revisions`, { kind: 'manager_update', base_revision_id: revisionId, decisions: value }) }
  return <main className="presales-page" aria-label="Пресейл-конвейер">
    <header className="presales-heading"><div><p className="detail-kicker">ОТ ЗАЯВКИ ДО ПРЕДЛОЖЕНИЯ</p><h1>Пресейл-конвейер</h1></div><div className="ui-toolbar"><button className="secondary" type="button" aria-haspopup="dialog" disabled={blocked} onClick={() => setSettingsOpen(true)}>Настройки пресейла</button><button className="primary" type="button" disabled={blocked || requiresSettings} onClick={() => setPicker(true)}>Новая заявка <span aria-hidden="true">↗</span></button></div></header>
    <PresalesCommandNotice command={command} />
    {settingsError && <p className="presales-alert" role="alert">{settingsError} <button type="button" className="presales-text-button" aria-haspopup="dialog" disabled={blocked} onClick={() => setSettingsOpen(true)}>Открыть настройки</button></p>}
    {settings?.requires_save && <p className="presales-notice">Перед первой заявкой <button type="button" className="presales-text-button" aria-haspopup="dialog" disabled={blocked} onClick={() => setSettingsOpen(true)}>сохраните настройки пресейла</button>. Начальные значения маржи — 50% и 70%.</p>}
    <div className="presales-workspace">
      <aside className="panel ui-list-panel presales-history" aria-label="История заявок"><header><h2>Заявки</h2><span>{history.length}</span></header><div className="presales-history-scroll">{history.map(item => <a className={`ui-choice presales-history-entry${item.request_id === requestId ? ' selected' : ''}`} key={item.request_id} href={`#/presales?request=${encodeURIComponent(item.request_id)}&revision=${encodeURIComponent(item.current_revision_id)}`} aria-current={item.request_id === requestId ? 'true' : undefined}><strong>{item.title}</strong><span>{item.client || 'Клиент не указан'}</span><footer><time dateTime={item.created_at}>{presalesDate(item.created_at)}</time><span className={isActive(item.state) ? 'presales-running' : ''}>{presalesState(item.state)}</span></footer></a>)}
      {historyBusy && <p role="status" className="presales-muted">Получаем заявки…</p>}{!historyBusy && !historyError && !history.length && <p className="presales-muted">Создайте заявку из файлов клиента. Её анализ и редакции останутся здесь.</p>}
      {historyError && <p role="alert" className="presales-alert">{historyError}</p>}{historyCursor !== null && <button type="button" className="secondary presales-more" disabled={historyBusy} onClick={() => void loadHistory(historyCursor)}>{historyError ? 'Повторить загрузку истории' : 'Показать ещё заявки'}</button>}</div></aside>
      <div className="presales-detail" aria-label="Выбранная заявка">
        {readError && <div className="presales-alert"><p role="alert">{readError}</p><button className="secondary" type="button" onClick={() => setRefresh(value => value + 1)}>Обновить результат</button></div>}
        {!requestId ? <section className="panel presales-empty"><span className="presales-empty-mark" aria-hidden="true">↗</span><p className="detail-kicker">КОМПЛЕКТ ДОКУМЕНТОВ → ГОТОВОЕ КП</p><h2>Начните с заявки клиента</h2><p>Выберите файлы. Конвейер соберёт позиции, проверит склад и производство, рассчитает предложение и подготовит документы.</p><button type="button" className="secondary" disabled={blocked || requiresSettings} onClick={() => setPicker(true)}>Выбрать файлы заявки</button></section> : !revision || !detail ? <section className="panel"><p role="status">Открываем сохранённую редакцию…</p></section> : <>
          <header className="presales-detail-heading"><div><p className="detail-kicker">{detail.request.client || 'Клиент не указан'}</p><h2>{detail.request.title}</h2></div><label>Редакция<select value={revisionId} onChange={event => navigate(requestId, event.target.value)}>{detail.revisions.map(item => <option key={item.revision_id} value={item.revision_id}>№ {item.revision_number} · {presalesDate(item.created_at)} · {presalesState(item.state)}</option>)}</select></label></header>
          {!current && <p className="presales-notice">Открыта сохранённая редакция. Изменения доступны в <a href={`#/presales?request=${encodeURIComponent(requestId)}&revision=${encodeURIComponent(detail.request.current_revision_id)}`}>последней редакции</a>.</p>}
          <section className="presales-progress"><div><span className={`presales-state${active ? ' presales-running' : ''}`}>{presalesState(job?.state || revision.state)}</span><p>{job?.stop_requested ? 'Остановка запрошена. Сохраняем текущий шаг.' : phaseLabels[job?.phase || revision.phase] || job?.phase || revision.phase}</p>{visibleProgress && <small>{visibleProgress}</small>}{job?.error && <p role="alert" className="presales-validation">{job.error.message}</p>}</div><div className="ui-toolbar">{current && job && active && <button type="button" className="secondary" disabled={blocked || job.stop_requested} onClick={() => void command.run(`/requests/${encodeURIComponent(requestId)}/jobs/${encodeURIComponent(job.job_id)}/stop`, {})}>{job.stop_requested ? 'Останавливаем…' : 'Остановить'}</button>}{current && job && !active && ['stopped', 'failed', 'cancelled'].includes(job.state) && <button type="button" className="secondary" disabled={blocked} onClick={() => void command.run(`/requests/${encodeURIComponent(requestId)}/jobs/${encodeURIComponent(job.job_id)}/resume`, {})}>Продолжить</button>}<button type="button" className="secondary" disabled={!editable || blocked || requiresSettings} onClick={() => setReanalyze(true)}>Пересчитать с текущими данными</button></div></section>
          <PresalesReview key={revisionId} revision={revision} lines={lines} nextCursor={linesCursor} loadingLines={linesBusy} onMore={() => void moreLines()} editable={editable} busy={blocked} onDecisions={decisions} />
        </>}
      </div>
    </div>
    {settingsOpen && <PresalesSettings onClose={closeSettings} onSaved={value => { setSettings(value); setSettingsError('') }} />}
    {picker && <PresalesFilePicker onClose={() => { if (!command.busy) setPicker(false) }} disabled={blocked} onCreate={ids => { setPicker(false); void command.run('/requests', { artifact_ids: ids }) }} />}
    {reanalyze && <WorkspaceDialog title="Пересчитать заявку?" onClose={() => setReanalyze(false)}><p>Новая редакция использует текущие настройки пресейла, каталог, склад и возможности предприятия. Предыдущий расчёт и документы сохранятся.</p><div className="presales-form-footer"><button type="button" className="secondary" onClick={() => setReanalyze(false)}>Отмена</button><button type="button" className="primary" disabled={blocked} onClick={() => { setReanalyze(false); void command.run(`/requests/${encodeURIComponent(requestId)}/revisions`, { kind: 'reanalyze', base_revision_id: revisionId }) }}>Создать новую редакцию</button></div></WorkspaceDialog>}
  </main>
}
