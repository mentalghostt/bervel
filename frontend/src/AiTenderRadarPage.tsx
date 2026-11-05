import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { listAllUploadedArtifacts } from './api'
import type { UploadedArtifact } from './api'
import { activeAiTenderJob, AiRadarError, listAiTenderCards, readAiTenderJob, startAiTenderJob } from './aiTenderRadarApi'
import type { Attempt, Card, CardSort, Job, Position } from './aiTenderRadarApi'
import './AiTenderRadarPage.css'
import WorkspaceDialog from './WorkspaceDialog'

const keys = {stock: 'bervel.ai-radar.stock', phrase: 'bervel.ai-radar.phrase', job: 'bervel.ai-radar.job', attempt: 'bervel.ai-radar.attempt'}
const cardStatus = {complete: 'Расчёт готов', insufficient_data: 'Недостаточно данных', partial: 'Анализ неполный'}
const stockStatus = {full: 'Есть полностью', partial: 'Есть частично', none: 'Нет остатка', unconfirmed: 'Не подтверждено'}
const economicStatus = {profitable: 'Выгодно', loss: 'Убыточно', break_even: 'В ноль', unknown: 'Недостаточно данных'}
const reasons: Record<string, string> = {
  TENDER_SOURCE_INCOMPLETE: 'Контур раскрыл неполный состав закупки.',
  TENDER_OBJECTS_MISSING: 'Контур не раскрыл объекты закупки.',
  LOT_OBJECTS_MISSING: 'В лоте нет раскрытого списка объектов.', LOT_OBJECTS_CONFLICT: 'Состав лота расходится с разбивкой по заказчикам.',
  POSITION_REQUIREMENTS_INCOMPLETE: 'Требования позиции раскрыты частично.', PIECE_TOTAL_UNKNOWN: 'Общее количество деталей в штуках не подтверждено.',
  MATCH_NOT_CONFIRMED: 'Совпадение подтверждено не для всех возможных вариантов.', STOCK_DATA_NOT_CONFIRMED: 'Количество или единицы остатков не подтверждены.',
  MODEL_NOT_COMPLETED: 'Codex не завершил проверку позиции.', MODEL_CONTEXT_LIMIT: 'Позиция превышает лимит контекста анализа.',
  NO_STOCK: 'Нет свободного остатка для этой позиции.', NO_COMPATIBLE_SKU: 'На складе нет совместимого артикула.', INSUFFICIENT_STOCK: 'Свободного остатка недостаточно.',
  TENDER_CURRENCY_UNKNOWN: 'Валюта тендерной цены неизвестна.', TENDER_PRICE_UNKNOWN: 'Контур не раскрыл цену позиции.',
  TENDER_PRICES_NOT_DISCLOSED: 'Не указаны цены в тендере.',
  TENDER_PRICES_CONFLICT: 'Цена за единицу расходится с суммой позиции.', STOCK_COST_OR_CURRENCY_UNKNOWN: 'Нет сопоставимой складской себестоимости.',
  SHORTAGE_COST_UNKNOWN: 'Стоимость исполнения всего объёма неизвестна.', TENDER_ECONOMICS_INCOMPLETE: 'Выручка или себестоимость подтверждены не для всех позиций.',
  REVENUE_NOT_POSITIVE: 'Для расчёта выгоды нужна положительная выручка.',
}
const number = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 3})
const percent = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1})
const date = new Intl.DateTimeFormat('ru-RU', {dateStyle: 'short', timeStyle: 'short', timeZone: 'Europe/Moscow'})
const display = (value: string | null, unit?: string | null) => value === null ? '—' : `${number.format(Number(value))}${unit ? ` ${unit}` : ''}`
const pct = (value: string | null, signed = false) => value === null ? 'Недостаточно данных' : `${signed && Number(value) > 0 ? '+' : ''}${percent.format(Number(value))}%`
const reason = (code: string) => reasons[code] || 'Для расчёта недостаточно подтверждённых данных.'
const message = (error: unknown) => error instanceof Error ? error.message : 'Не удалось получить результат.'
const pending = (state: Job['state']) => state === 'waiting' || state === 'queued' || state === 'running'

function readAttempt(): Attempt | null {
  try {
    const value = JSON.parse(localStorage.getItem(keys.attempt) || 'null') as Attempt | null
    if (!value || typeof value.body?.stock_artifact_id !== 'string' || typeof value.body.command_key !== 'string') return null
    return value.cardId === null &&
      ('search_phrase' in value.body && typeof value.body.search_phrase === 'string' ||
        'source_artifact_id' in value.body && typeof value.body.source_artifact_id === 'string') ||
      typeof value.cardId === 'string' ? value : null
  } catch { return null }
}

function PositionRow({position}: {position: Position}) {
  return <li className="ai-radar-position">
    <details>
      <summary>
        <span><small>Лот {position.lot} · {position.id.split(':').at(-1)}</small><strong>{position.name || 'Наименование не раскрыто'}</strong></span>
        <span><small>На складе / требуется</small><strong>{display(position.covered, position.unit)} / {display(position.required, position.unit)}</strong></span>
        <span><small>Остаток</small><strong>{stockStatus[position.stock_status]}</strong></span>
        <span><small>{position.economics.scope === 'confirmed' ? 'Экономика по подтверждённому объёму' : 'Экономика'}</small><strong>{position.economics.margin_percent === null ? 'N/A' : `${economicStatus[position.economics.status]} ${pct(position.economics.margin_percent, true)}`}</strong></span>
      </summary>
      <div className="ai-radar-position-body">
        {position.mass_conversion && <p>Расчётный эквивалент {display(position.mass_conversion.required_pieces, 'шт.')} · масса одной детали {position.mass_conversion.weight_per_piece_kg.replace('.', ',')} кг</p>}
        <p>Дефицит {display(position.shortage, position.unit)} · Выручка {display(position.economics.revenue, position.economics.currency)} · Себестоимость {display(position.economics.cost, position.economics.currency)} · Результат {display(position.economics.profit, position.economics.currency)}</p>
        {position.stock_reason && <p>{reason(position.stock_reason)}</p>}
        {position.economics.reason && <p>{reason(position.economics.reason)}</p>}
        {position.matches.map(match => {
          const allocation = position.allocations.find(item => item.sku === match.sku)
          return <div key={match.sku} className="ai-radar-evidence">
            <p><strong>{match.sku}</strong> · строка склада {match.stock_row} · свободно {display(match.stock_free_quantity, match.stock_unit)}
              {allocation && <> · выделено {display(allocation.stock_quantity, allocation.stock_unit)} · себестоимость за единицу {display(allocation.stock_unit_cost, allocation.stock_currency)}</>}</p>
            <ul>{match.evidence.map((fact, index) => <li key={index}>{fact.requirement} · {fact.tender_value} → {fact.stock_value} <small>({fact.stock_field})</small></li>)}</ul>
          </div>
        })}
      </div>
    </details>
  </li>
}

export function CardMetrics({card}: {card: Card}) {
  const coverage = card.coverage_percent ?? card.position_coverage_percent ?? null
  const byPosition = card.coverage_percent === null && coverage !== null
  return <dl className="ai-radar-metrics">
    <div>
      <dt>{byPosition ? 'Покрытие склада по раскрытым строкам' : 'Покрытие склада'}</dt>
      <dd data-known={coverage !== null} title={pct(coverage)}>{coverage === null ? '—' : pct(coverage)}</dd>
    </div>
    <div>
      <dt>{card.benefit_percent !== null && card.benefit_scope === 'confirmed' ? 'Экономическая выгода по подтверждённому объёму' : 'Экономическая выгода'}</dt>
      <dd data-known={card.benefit_percent !== null} data-loss={Number(card.benefit_percent) < 0} title={card.benefit_reason ? reason(card.benefit_reason) : undefined}>{card.benefit_percent === null ? 'N/A' : pct(card.benefit_percent, true)}
      </dd>
    </div>
  </dl>
}

export function CardDetails({job, benefitReason}: {job: Job; benefitReason?: string | null}) {
  const [filter, setFilter] = useState('all')
  if (!job.analysis || !job.tender) return <p>Результат анализа недоступен.</p>
  const {analysis, tender, stock} = job
  const economicsReason = benefitReason ?? analysis.economics.reason
  return <div className="ai-radar-card-detail">
    {stock && <p className="ai-radar-meta">Склад {stock.filename} · {number.format(stock.row_count)} строк · <a href={`#/files?artifact_id=${encodeURIComponent(job.stock_artifact_id)}`}>Открыть файл</a></p>}
    {analysis.model && <p className="ai-radar-meta">Codex · {analysis.model}</p>}
    <p className="ai-radar-meta">{analysis.economics.scope === 'confirmed' && 'По подтверждённому объёму · '}Выручка {display(analysis.economics.revenue, analysis.economics.currency)} · Себестоимость {display(analysis.economics.cost, analysis.economics.currency)} · Результат {display(analysis.economics.profit, analysis.economics.currency)}</p>
    {analysis.model_error && <p role="alert" className="error-note">{analysis.model_error.message}</p>}
    {analysis.coverage_reason && <p>{reason(analysis.coverage_reason)}</p>}
    {economicsReason && <p>{reason(economicsReason)}</p>}
    {analysis.benefit_clamped && <p>Фактическая маржа {pct(analysis.economics.margin_percent, true)}. Шкала карточки ограничена ±100%.</p>}
    {!!tender.limitations.length && <ul className="ai-radar-limitations">{tender.limitations.map((limit, index) => <li key={index}>{limit.lot && `Лот ${limit.lot} · `}{reason(limit.code)}</li>)}</ul>}
    <div className="ai-position-heading"><h3>Позиции закупки <span>{analysis.positions.length}</span></h3><p>Раскройте позицию для расчёта и подтверждений.</p></div>
    <div className="position-filters" aria-label="Фильтр позиций">{[['all','Все'],['full','Есть полностью'],['partial','Есть частично'],['none','Нет остатка'],['unconfirmed','Не подтверждено']].map(([value,label])=><button key={value} type="button" aria-pressed={filter===value} onClick={()=>setFilter(value)}>{label}</button>)}</div>
    {analysis.positions.length ? <ul className="ai-radar-positions">{analysis.positions.filter(position=>filter==='all'||position.stock_status===filter).map(position => <PositionRow key={position.id} position={position}/>)}</ul> : <p>Нет раскрытых позиций.</p>}
    {!!analysis.positions.length && !analysis.positions.some(position=>filter==='all'||position.stock_status===filter) && <p className="quiet-state">Позиций по этому фильтру нет.</p>}
  </div>
}

export default function AiTenderRadarPage() {
  const [files, setFiles] = useState<UploadedArtifact[] | null>(null)
  const [stock, setStock] = useState(() => localStorage.getItem(keys.stock) || '')
  const [phrase, setPhrase] = useState(() => localStorage.getItem(keys.phrase) || '')
  const [source, setSource] = useState<'api' | 'file'>('api')
  const [archives, setArchives] = useState<UploadedArtifact[]>([])
  const [archiveId, setArchiveId] = useState('')
  const [attempt, setAttempt] = useState(readAttempt)
  const [jobId, setJobId] = useState(() => localStorage.getItem(keys.job) || '')
  const [job, setJob] = useState<Job | null>(null)
  const [restoring, setRestoring] = useState(true)
  const [sending, setSending] = useState(false)
  const submitLock = useRef(false)
  const [jobError, setJobError] = useState('')
  const [filesError, setFilesError] = useState('')
  const [cardError, setCardError] = useState('')
  const [reload, setReload] = useState(0)
  const [cards, setCards] = useState<Card[] | null>(null)
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [more, setMore] = useState(false)
  const [expanded, setExpanded] = useState('')
  const [setupOpen, setSetupOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<CardSort>('coverage')
  const [details, setDetails] = useState<{id: string; job?: Job; error?: string} | null>(null)
  const cardRead = useRef<{valid: boolean} | null>(null)
  const active = !!jobId && (job?.job_id !== jobId || pending(job.state))
  const stockAvailable = !!files?.some(file => file.artifact_id === stock)
  const blocked = restoring || active || sending
  const sourceReady = source === 'api' ? !!phrase.trim() : archives.some(file => file.artifact_id === archiveId)
  const finishedJob = job && !pending(job.state) ? job.job_id : ''
  const selectedCard = cards?.find(card=>card.job_id===expanded)

  useEffect(() => {
    let mounted = true
    void listAllUploadedArtifacts().then(all => {
      if (!mounted) return
      const available = all.filter(file => file.usage_role === 'stock' && file.filename.toLowerCase().endsWith('.csv'))
      setFiles(available)
      setArchives(all.filter(file => file.filename.toLowerCase().endsWith('.json')))
      setFilesError('')
      if (available.length === 1) setStock(old => old || available[0].artifact_id)
    }).catch(error => { if (mounted) setFilesError(message(error)) })
    void activeAiTenderJob().then(current => {
      if (mounted && current) {
        localStorage.setItem(keys.job, current.job_id)
        setJobId(current.job_id)
      }
    }).catch(error => { if (mounted) setJobError(message(error)) })
      .finally(() => { if (mounted) setRestoring(false) })
    return () => { mounted = false }
  }, [reload])

  useEffect(() => {
    if (!jobId) return
    let mounted = true
    let timer: number | undefined
    async function poll() {
      try {
        const current = await readAiTenderJob(jobId)
        const next = current.batch_id && !pending(current.state) ? await activeAiTenderJob() : null
        if (!mounted) return
        setJob(current)
        setJobError('')
        if (next?.batch_id && next.batch_id === current.batch_id && next.job_id !== jobId) {
          localStorage.setItem(keys.job, next.job_id)
          setJobId(next.job_id)
        } else if (pending(current.state)) timer = window.setTimeout(poll, 1800)
      } catch (error) {
        if (!mounted) return
        setJobError(message(error))
        if (error instanceof AiRadarError && error.status === 404) {
          localStorage.removeItem(keys.job)
          setJobId('')
        } else timer = window.setTimeout(poll, 3000)
      }
    }
    void poll()
    return () => { mounted = false; window.clearTimeout(timer) }
  }, [jobId])

  useEffect(() => {
    const read = {valid: true}
    cardRead.current = read
    void listAiTenderCards(0, sort).then(page => {
      if (!read.valid) return
      setCards(page.cards)
      setNextOffset(page.next_offset)
      setCardError('')
    }).catch(error => { if (read.valid) setCardError(message(error)) })
      .finally(() => { if (read.valid) setMore(false) })
    return () => { read.valid = false }
  }, [finishedJob, reload, sort])

  useEffect(() => {
    if (!expanded) return
    let mounted = true
    void readAiTenderJob(expanded).then(result => { if (mounted) setDetails({id: expanded, job: result}) })
      .catch(error => { if (mounted) setDetails({id: expanded, error: message(error)}) })
    return () => { mounted = false }
  }, [expanded, reload])

  function refresh() { setReload(value => value + 1) }

  function changeSort(value: CardSort) {
    if (cardRead.current) cardRead.current.valid = false
    setCards(null)
    setNextOffset(null)
    setCardError('')
    setMore(false)
    setSort(value)
  }

  async function loadMore() {
    if (nextOffset === null || more) return
    const read = cardRead.current
    setMore(true)
    try {
      const page = await listAiTenderCards(nextOffset, sort)
      if (!read?.valid) return
      setCards(old => [...(old || []), ...page.cards.filter(card => !old?.some(item => item.tender.id === card.tender.id))])
      setNextOffset(page.next_offset)
      setCardError('')
    } catch (error) { if (read?.valid) setCardError(message(error)) }
    finally { if (read?.valid) setMore(false) }
  }

  async function submit(command: Attempt) {
    if (submitLock.current || blocked) return
    submitLock.current = true
    setSending(true)
    setJobError('')
    localStorage.setItem(keys.attempt, JSON.stringify(command))
    setAttempt(command)
    try {
      const receipt = await startAiTenderJob(command)
      localStorage.setItem(keys.job, receipt.job_id)
      localStorage.removeItem(keys.attempt)
      setJobId(receipt.job_id)
      setAttempt(null)
      setSetupOpen(false)
    } catch (error) {
      setJobError(message(error))
      if (error instanceof AiRadarError && error.rejected) {
        localStorage.removeItem(keys.attempt)
        setAttempt(null)
        if (error.code === 'PROCESS_ACTIVE') { setRestoring(true); refresh() }
      }
    } finally {
      submitLock.current = false
      setSending(false)
    }
  }

  function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (attempt) { void submit(attempt); return }
    if (!stockAvailable || !sourceReady) return
    localStorage.setItem(keys.stock, stock)
    if (source === 'api') localStorage.setItem(keys.phrase, phrase.trim())
    const input = source === 'api' ? {search_phrase: phrase.trim()} : {source_artifact_id: archiveId}
    void submit({cardId: null, body: {stock_artifact_id: stock, command_key: crypto.randomUUID(), ...input}})
  }

  function reanalyze(card: Card) {
    if (attempt || !stockAvailable) return
    localStorage.setItem(keys.stock, stock)
    void submit({cardId: card.job_id, body: {stock_artifact_id: stock, command_key: crypto.randomUUID()}})
  }

  return <main className="ai-radar-page">
    <header className="page-heading ai-radar-heading"><div><span className="ai-radar-source">КОНТУР.ЗАКУПКИ · CODEX</span><h1>Тендерный радар ИИ</h1></div><button type="button" className="primary" onClick={()=>setSetupOpen(true)}>Новый анализ</button></header>
    {setupOpen && <WorkspaceDialog title="Новый анализ" onClose={()=>setSetupOpen(false)}>
    <section className="panel ai-radar-launch" aria-labelledby="ai-radar-launch-heading">
      <h2 id="ai-radar-launch-heading" className="sr-only">Источники анализа</h2>
      <form onSubmit={start} data-source={source}>
        <label>Складской CSV<select value={stock} onChange={event => setStock(event.target.value)} disabled={blocked || !!attempt}>
          <option value="">{files === null ? 'Загрузка складов…' : 'Выберите файл'}</option>
          {stock && !stockAvailable && <option value={stock}>Выбранный файл недоступен</option>}
          {files?.map(file => <option value={file.artifact_id} key={file.artifact_id}>{file.filename}</option>)}
        </select></label>
        <label>Источник закупок<select value={source} disabled={blocked || !!attempt}
          onChange={event => setSource(event.target.value === 'file' ? 'file' : 'api')}>
          <option value="api">Контур.АПИ</option><option value="file">Файл</option>
        </select></label>
        {source === 'api' ? <label>Поисковая фраза<input value={phrase} onChange={event => setPhrase(event.target.value)} maxLength={500}
          placeholder="Например, болты М12" disabled={blocked || !!attempt}/></label> :
          <label>Сохранённый JSON<select value={archiveId} disabled={blocked || !!attempt}
            onChange={event => setArchiveId(event.target.value)}>
            <option value="">Выберите файл</option>
            {archives.map(file => <option value={file.artifact_id} key={file.artifact_id}>{file.filename}</option>)}
          </select></label>}
        <button className="primary" type="submit" disabled={blocked || (!attempt && (!stockAvailable || !sourceReady))}>
          {sending ? 'Отправляем…' : attempt ? 'Повторить отправку' : source === 'file' ?
            'Проанализировать весь JSON' : 'Проанализировать следующий тендер'}
        </button>
      </form>
      {source === 'api' && <p className="ai-radar-meta">За один запуск проверяется одна открытая закупка.</p>}
      {files?.length === 0 && <p>В разделе «Файлы» загрузите CSV и назначьте ему роль «Склад».</p>}
      {filesError && <p role="alert" className="error-note">{filesError} <button type="button" className="secondary" onClick={refresh}>Обновить</button></p>}
      {attempt && !sending && <p className="ai-radar-meta">Отправка не подтверждена. Повтор использует ту же команду и тот же склад.</p>}
      {jobError && <p role="alert" className="error-note">{jobError}</p>}
    </section></WorkspaceDialog>}
    {attempt && !setupOpen && <p className="ai-radar-notice">Отправка не подтверждена. <button className="text-action" onClick={()=>setSetupOpen(true)}>Проверить и повторить</button></p>}
    {jobError && <p role="alert" className="error-note">{jobError}</p>}
    {active && <p role="status" className="ai-radar-notice">{job?.state === 'running' ? 'Получаем и анализируем тендер…' : 'Задание в очереди…'} Результат сохранится, страницу можно закрыть.</p>}
    {!active && job?.state === 'failed' && <p role="alert" className="error-note">{job.error?.message || 'Не удалось выполнить анализ.'}</p>}
    {!active && job?.state === 'no_match' && <p role="status" className="ai-radar-notice">В просмотренной выдаче нет новой открытой закупки. Уточните поисковую фразу.</p>}
    {!active && job?.analysis?.status === 'partial' && <div className="ai-radar-notice">
      <p role="status">Анализ неполный. {job.analysis.model_error?.message}</p>
      <button className="secondary" onClick={()=>{setDetails({id:job.job_id,job});setExpanded(job.job_id)}}>Открыть проверенные позиции</button>
    </div>}
    <section className="ai-radar-results" aria-labelledby="ai-radar-results-heading">
      <div className="ai-radar-results-header"><h2 id="ai-radar-results-heading">Результаты <span>{cards?.length ?? '—'}</span></h2><div className="ai-results-controls"><label className="radar-search"><span className="sr-only">Найти тендер</span><input type="search" placeholder="Название или номер" value={query} onChange={event=>setQuery(event.target.value)}/></label>
        <label className="ai-results-sort"><span className="sr-only">Сортировка карточек</span>
          <select value={sort} onChange={event => changeSort(event.target.value === 'benefit' ? 'benefit' : 'coverage')}>
            <option value="coverage">По покрытию склада ↓</option>
            <option value="benefit">По экономической выгоде ↓</option>
          </select>
        </label>
        <button type="button" className="secondary" onClick={refresh}>Обновить</button></div></div>
      {cardError && <p role="alert" className="error-note">{cardError}</p>}
      {!cardError && cards === null && <p role="status">Загружаем карточки…</p>}
      {cards?.length === 0 && <div className="panel ai-radar-empty"><h3>Карточек пока нет</h3><p>Результат первого анализа появится здесь.</p></div>}
      <div className="ai-radar-card-list">{cards?.filter(card=>`${card.tender.title} ${card.tender.number || card.tender.id}`.toLocaleLowerCase('ru').includes(query.trim().toLocaleLowerCase('ru'))).map(card => <article key={card.job_id} className="ai-radar-card">
        <div className="ai-radar-card-head">
          <div className="ai-radar-card-title">
            <p className="ai-radar-meta">{card.tender.number || card.tender.id} · {date.format(new Date(card.updated_at))} МСК</p>
            <h3 title={card.tender.title}>{card.tender.title}</h3>
            <p className="ai-radar-card-state"><span data-status={card.status}>{cardStatus[card.status]}</span>{card.mock && <span>Тестовый склад</span>}
              </p>
          </div>
          <CardMetrics card={card}/>
        </div>
        <div className="ai-radar-card-actions">
          <button className="open-positions" type="button" aria-haspopup="dialog" aria-label={`Открыть позиции: ${card.tender.title}`}
            onClick={() => {setDetails(null);setExpanded(card.job_id)}}>
            <span>Открыть позиции ({card.tender.position_count})</span>
          </button>
        </div>
      </article>)}</div>
      {query && cards && !cards.some(card=>`${card.tender.title} ${card.tender.number || card.tender.id}`.toLocaleLowerCase('ru').includes(query.trim().toLocaleLowerCase('ru'))) && <p className="quiet-state">Среди загруженных тендеров совпадений нет.</p>}
      {nextOffset !== null && <button className="secondary ai-radar-more" type="button" onClick={() => {void loadMore()}} disabled={more}>{more ? 'Загружаем…' : 'Показать ещё'}</button>}
    </section>
    {expanded && <WorkspaceDialog wide title="Позиции тендера" onClose={()=>setExpanded('')}>
      <div className="ai-dialog-heading"><div><p className="ai-radar-meta">{selectedCard?.tender.number || selectedCard?.tender.id || details?.job?.tender?.number || details?.job?.tender?.id}</p><h3>{selectedCard?.tender.title || details?.job?.tender?.title || 'Тендер'}</h3>{(selectedCard?.tender.link || details?.job?.tender?.link) && <a href={selectedCard?.tender.link || details?.job?.tender?.link || undefined} target="_blank" rel="noreferrer">Открыть в Контуре</a>}</div>{selectedCard && <CardMetrics card={selectedCard}/>}</div>
      {details?.id !== expanded ? <p role="status" className="quiet-state">Загружаем позиции…</p> : details.job ? <CardDetails key={expanded} job={details.job} benefitReason={selectedCard?.benefit_reason}/> : <p role="alert" className="error-note">{details.error} <button className="secondary" onClick={refresh}>Повторить чтение</button></p>}
      {selectedCard && <div className="ai-dialog-actions"><label>Склад для пересчёта<select value={stock} disabled={blocked || !!attempt} onChange={event=>setStock(event.target.value)}><option value="">Выберите склад</option>{files?.map(file=><option key={file.artifact_id} value={file.artifact_id}>{file.filename}</option>)}</select></label><button className="secondary" type="button" disabled={blocked || !!attempt || !stockAvailable} onClick={()=>reanalyze(selectedCard)}>Пересчитать с выбранным складом</button></div>}
      {jobError && <p className="error-note" role="alert">{jobError}</p>}
    </WorkspaceDialog>}
  </main>
}
