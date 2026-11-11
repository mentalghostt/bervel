import {useEffect, useMemo, useRef, useState} from 'react'
import type {ReactNode} from 'react'
import {readOperationJob, readRunAttempt, readOperations, RunError, stopRun, submitRunCommand} from './aiRadarRunsApi'
import type {Operation, QuestionReport, Run, RunAttempt, RunCard, RunPosition, TenderMetrics, RatingStatus} from './aiRadarRunsApi'
import {listProfiles, profileSource, readProfile, readProfileCommand, readScheduleCommand, submitProfileCommand, submitScheduleCommand, saveKeywords} from './radarProfilesApi'
import type {ProfileSource, RadarProfile, ProfileCommand, Schedule, ScheduleCommand} from './radarProfilesApi'
import WorkspaceDialog from './WorkspaceDialog'
import RadarResultsFilter, {emptyRadarFilters, matchesRadarFilters, metricValue, resultCoverage, resultMargin, tradeLabels} from './RadarResultsFilter'
import type {RadarFilterCard} from './RadarResultsFilter'
import {formatPieceCoverage, getReviewSummary, resultScore} from './aiRadarReviewSummary'
import {radarRefreshStatus} from './radarRefreshStatus'
import type {Job} from './aiTenderRadarApi'
import './CompanySettingsPage.css'
import './AiRadarRunsPage.css'
import './AiTenderRadarPage.css'
const positionReasons: Record<string, string> = {
  TENDER_SOURCE_INCOMPLETE: 'Контур раскрыл неполный состав закупки.',
  TENDER_OBJECTS_MISSING: 'Контур не раскрыл объекты закупки.',
  LOT_OBJECTS_MISSING: 'В лоте нет раскрытого списка объектов.', LOT_OBJECTS_CONFLICT: 'Состав лота расходится с разбивкой по заказчикам.',
  POSITION_REQUIREMENTS_INCOMPLETE: 'Требования позиции раскрыты частично.',
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
const money = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2})
const percent = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1})
const wholePercent = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0})
const display = (value: string | null | undefined, unit?: string | null, format = number) => value == null ? '—' : `${format.format(Number(value))}${unit ? ` ${unit}` : ''}`
const pct = (value: string | null, signed = false) => value === null ? 'Недостаточно данных' : `${signed && Number(value) > 0 ? '+' : ''}${percent.format(Number(value)).replace(/^-/, '−')}%`
const reason = (code: string) => positionReasons[code] || 'Для расчёта недостаточно подтверждённых данных.'
function TenderCard({title, onOpen, children}: {title: string; onOpen?: () => void; children: ReactNode}) {
  return <article className="ai-radar-card" role={onOpen ? 'button' : undefined} tabIndex={onOpen ? 0 : undefined} aria-label={title} aria-haspopup={onOpen ? 'dialog' : undefined}
    onClick={onOpen ? event => {
      if (event.defaultPrevented) return
      const control = event.target instanceof Element ? event.target.closest('a, button, input, select, textarea, summary, [role="button"], [contenteditable="true"]') : null
      if (control && control !== event.currentTarget) return
      event.currentTarget.focus({preventScroll: true})
      onOpen()
    } : undefined}
    onKeyDown={onOpen ? event => {
      if (event.target !== event.currentTarget || !['Enter', ' '].includes(event.key)) return
      event.preventDefault()
      onOpen()
    } : undefined}>{children}</article>
}
function SourceTitle({title, link}: {title: string; link?: string | null}) {
  return link && /^https?:\/\//i.test(link)
    ? <a className="tender-source-title" href={link} target="_blank" rel="noreferrer noopener" title="Оригинал тендера">{title}</a>
    : <>{title}</>
}
function PositionRow({position, link}: {position: RunPosition; link?: string | null}) {
  const prices = position.unit_prices
  const currency = prices?.currency === 'RUB' ? '₽' : prices?.currency
  return <li className="ai-radar-position">
    <details>
      <summary>
        <span><strong><SourceTitle title={position.name || 'Наименование не раскрыто'} link={link}/></strong></span>
        <span><small>На складе / требуется</small><strong>{display(position.covered, position.unit)} / {display(position.required, position.unit)}</strong></span>
        <span><small title="Себестоимость со склада / цена в тендере за одну единицу">Цена{prices?.unit ? `, за ${prices.unit}` : ''}</small><strong>{display(prices?.stock_cost, undefined, money)} / {display(prices?.tender_price, currency, money)}</strong></span>
        <span><small title={position.economics?.scope === 'confirmed' ? 'По подтверждённому объёму' : undefined}>Маржинальность</small><strong>{position.economics?.margin_percent == null ? '—' : pct(position.economics.margin_percent, true)}</strong></span>
      </summary>
      <div className="ai-radar-position-body">
        {position.mass_conversion && <p>Расчётный эквивалент {display(position.mass_conversion.required_pieces, 'шт.')} · масса одной детали {position.mass_conversion.weight_per_piece_kg.replace('.', ',')} кг</p>}
        <p>Дефицит {display(position.shortage, position.unit)} · Выручка {display(position.economics?.revenue, position.economics?.currency)} · Себестоимость {display(position.economics?.cost, position.economics?.currency)} · Результат {display(position.economics?.profit, position.economics?.currency)}</p>
        {position.stock_reason && <p>{reason(position.stock_reason)}</p>}
        {position.economics?.reason && <p>{reason(position.economics.reason)}</p>}
        {position.matches?.map(match => {
          const allocation = position.allocations?.find(item => item.sku === match.sku)
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

const ratingLabels = {pending: 'Ожидается', running: 'Считается', failed: 'Ошибка', unknown: 'Не получена', stale: 'Устарела', complete: ''}
function CardMetrics({coverage, margin, score, previousScore, ratingStatus, ratingVersion, analyzing, byPosition, marginHint, tenderMetrics}: {coverage: string | null; margin: string | null; score: number | null; previousScore?: number; ratingStatus?: RatingStatus; ratingVersion?: string; analyzing?: boolean; byPosition?: boolean; marginHint?: string; tenderMetrics?: TenderMetrics | null}) {
  const previous = score === null && ratingStatus === 'stale' && previousScore !== undefined
  const displayedScore = score ?? (ratingStatus === 'stale' ? previousScore ?? null : null)
  const roundedMargin = margin === null ? null : wholePercent.format(Number(margin)).replace(/^[-−]0$/, '0').replace(/^-/, '−')
  const price = tenderMetrics?.price ?? null
  const catalog = tenderMetrics?.catalog_match_percent ?? null
  const catalogHint = tenderMetrics?.catalog_match_total != null
    ? `Подтверждено ${tenderMetrics.catalog_match_confirmed ?? 0} из ${tenderMetrics.catalog_match_total} раскрытых позиций${tenderMetrics.catalog_match_incomplete ? '; часть данных требует уточнения' : ''}.`
    : undefined
  const currency = tenderMetrics?.currency
  const pieceCoverage = tenderMetrics?.stock_coverage_percent != null
  const coverageHint = pieceCoverage
    ? (tenderMetrics?.stock_coverage_incomplete ? 'По подтверждённым данным. Для части позиций не хватает данных для расчёта.'
      : 'Доля обеспеченных складом штук в подходящих лотах')
    : undefined
  return <dl className={`ai-radar-metrics${tenderMetrics !== undefined ? ' ai-radar-metrics-expanded' : ''}`}>
    {tenderMetrics !== undefined && <>
      <div><dt>Стоимость тендера</dt><dd data-known={price !== null} title={price === null ? undefined : fmt(price, currency ? ` ${currency}` : '')}>{price === null ? '—' : fmt(price, currency === 'RUB' ? ' ₽' : currency ? ` ${currency}` : '')}</dd></div>
      <div><dt>Соответствие продукции</dt><dd data-known={catalog !== null} title={catalogHint} aria-label={catalogHint}>{catalog === null ? '—' : pct(catalog)}{catalog !== null && tenderMetrics?.catalog_match_incomplete ? ' *' : ''}</dd></div>
    </>}
    <div>
      <dt>{byPosition ? 'Покрытие склада по раскрытым строкам' : 'Покрытие склада'}</dt>
      <dd data-known={coverage !== null} title={coverageHint}>{coverage === null ? '—' : pieceCoverage ? formatPieceCoverage(coverage) : pct(coverage)}</dd>
    </div>
    <div>
      <dt>Маржинальность</dt>
      <dd data-known={margin !== null} data-loss={Number(margin) < 0} title={marginHint}>{roundedMargin === null ? '—' : `${roundedMargin}%`}</dd>
    </div>
    <div><dt>{previous ? 'Оценка до обновления показателей' : 'Оценка ИИ'}</dt><dd className={displayedScore === null ? 'metric-status' : undefined} data-known={displayedScore !== null}
      title={previous ? 'Сохранённая оценка по прежним показателям. Новый балл появится после полного обновления.' : score !== null ? ratingVersion === 'card-individual-rating-v1'
        ? 'Индивидуальная оценка соответствия продукции, покрытия складом, маржинальности и готовности допроизводства за 48 часов до подачи заявки. Не является решением об участии.'
        : 'Сравнительная оценка среди карточек этого профиля по четырём показателям. Не является решением об участии.'
        : ratingStatus === 'unknown' ? 'Исход запроса неизвестен. Возобновите незавершённые оценки.' : analyzing ? 'Оценка появится после анализа этого тендера.' : undefined}>
      {displayedScore ?? (ratingStatus ? ratingLabels[ratingStatus] : 'Нет оценки')}</dd></div>
  </dl>
}

function RunCardMetrics({card}: {card: RadarFilterCard}) {
  return <CardMetrics
    coverage={resultCoverage(card)}
    margin={resultMargin(card)}
    score={resultScore(card)}
    previousScore={card.rating?.previous_score}
    ratingStatus={card.rating?.status ?? 'pending'}
    ratingVersion={card.rating?.prompt_version}
    analyzing={card.state === 'analyzing'}
    tenderMetrics={card.metrics ?? null}
    marginHint={(card.opportunity ? card.opportunity.basis === 'confirmed' : card.assessment?.full_economics?.margin_percent == null && card.analysis?.economics.scope === 'confirmed') ? 'По подтверждённому объёму' : undefined}
  />
}

type CardDetailAnalysis = Omit<NonNullable<RunCard['analysis']>, 'coverage_percent' | 'benefit_percent' | 'economics'> & {economics: Omit<NonNullable<RunCard['analysis']>['economics'], 'scope'> & {scope?: string | null}}
type CardDetailData = {analysis: CardDetailAnalysis | null; tender: {link?: string | null; limitations?: NonNullable<Job['tender']>['limitations']} | null; stock?: Job['stock']; stock_artifact_id?: string}
function CardCalculationSummary({analysis, tender, benefitReason}: Pick<CardDetailData, 'analysis' | 'tender'> & {benefitReason?: string | null}) {
  if (!analysis || !tender) return null
  const economicsReason = benefitReason ?? analysis.economics.reason
  const coverageReason = analysis.coverage_reason === 'PIECE_TOTAL_UNKNOWN' ? null : analysis.coverage_reason
  const limitations = tender.limitations?.filter(limit => limit.code !== 'PIECE_TOTAL_UNKNOWN')
  const {revenue, cost, profit, currency, scope} = analysis.economics
  return <div className="ai-radar-calculation">
    <p className="ai-radar-meta">{scope === 'confirmed' && 'По подтверждённому объёму · '}Выручка {display(revenue, currency, money)} · Себестоимость {display(cost, currency, money)} · Результат {display(profit, currency, money)}</p>
    {analysis.model_error && <p role="alert" className="error-note">{analysis.model_error.message}</p>}
    {coverageReason && <p>{reason(coverageReason)}</p>}
    {economicsReason && <p>{reason(economicsReason)}</p>}
    {analysis.benefit_clamped && <p>Фактическая маржа {pct(analysis.economics.margin_percent ?? null, true)}. Показано фактическое значение маржинальности.</p>}
    {!!limitations?.length && <ul className="ai-radar-limitations">{limitations.map((limit, index) => <li key={index}>{limit.lot && `Лот ${limit.lot} · `}{reason(limit.code)}</li>)}</ul>}
  </div>
}

export function CardDetails({job, benefitReason, showCalculation = true}: {job: CardDetailData; benefitReason?: string | null; showCalculation?: boolean}) {
  const [filter, setFilter] = useState('all')
  if (!job.analysis || !job.tender) return <p>Результат анализа недоступен.</p>
  const {analysis, tender, stock} = job
  return <div className="ai-radar-card-detail" role="region" aria-label="Позиции карточки">
    {stock && <p className="ai-radar-meta">Склад {stock.filename} · {number.format(stock.row_count)} строк{job.stock_artifact_id && <> · <a href={`#/files?artifact_id=${encodeURIComponent(job.stock_artifact_id)}`}>Открыть файл</a></>}</p>}
    {showCalculation && <CardCalculationSummary analysis={analysis} tender={tender} benefitReason={benefitReason}/>}
    <div className="ai-position-heading"><h3>Позиции закупки <span>{analysis.positions.length}</span></h3><p>Раскройте позицию для расчёта и подтверждений.</p></div>
    <div className="position-filters" aria-label="Фильтр позиций">{[['all','Все'],['full','Есть полностью'],['partial','Есть частично'],['none','Нет остатка'],['unconfirmed','Не подтверждено']].map(([value,label])=><button key={value} type="button" aria-pressed={filter===value} onClick={()=>setFilter(value)}>{label}</button>)}</div>
    {analysis.positions.length ? <ul className="ai-radar-positions">{analysis.positions.map(position =>
      (filter === 'all' || position.stock_status === filter) && <PositionRow key={position.id} position={position} link={tender.link}/>
    )}</ul> : <p>Нет раскрытых позиций.</p>}
    {!!analysis.positions.length && !analysis.positions.some(position=>filter==='all'||position.stock_status===filter) && <p className="quiet-state">Позиций по этому фильтру нет.</p>}
  </div>
}


const fmt = (v:string|number|null|undefined,unit='') => v==null?'Неизвестно':number.format(Number(v))+unit
const errorText = (e:unknown) => e instanceof Error?e.message:'Не удалось получить данные.'
const names:Record<ProfileSource,string>={test_api:'Симуляция контур.закупки',kontur_api:'контур.закупки'}
const defaultSchedule:Schedule={enabled:false,every_days:1,time:'09:00',timezone:'Europe/Moscow'}
const refreshKey='bervel.radar.profile.refresh.v1', questionKey='bervel.radar.profile.question.v1', operationsKey='bervel.radar.profile.operations.v1'
const scheduleKey='bervel.radar.profile.schedule.v1'
const reviewDecisions={participate:'Рекомендую участвовать',decline:'Не рекомендую участвовать',review:'Участие требует уточнений'}
const refreshStageLabels={primary:'Первичная оценка',detail:'Подробности',analysis:'Анализ',rating:'Оценка ИИ',metrics:'Показатели'}
const terminal=(state:string)=>['loaded','no_match','failed'].includes(state)
const isActive=(run:Run|null)=>!!run && ['queued','running'].includes(run.data.state)
const dateFormat=new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:'Europe/Moscow'})
const deadlineFormat=new Intl.DateTimeFormat('ru-RU',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Moscow'})
function applicationDeadline(value:string) {
 const date=new Date(value)
 return Number.isFinite(date.getTime())?`${deadlineFormat.format(date)} МСК`:'Не указана'
}
function CardDate({card}:{card:RunCard}) {
 const analyzed=!!card.analysis && !!card.evaluated_at
 const value=analyzed?card.evaluated_at:card.last_seen_at
 return value?<time dateTime={value}>{analyzed?'Анализ':'Последний поиск'}: {dateFormat.format(new Date(value))} МСК</time>:<span>Дата не указана</span>
}
const cardTitle=(card:RunCard)=>card.tender?.title || (typeof card.brief?.OrderName==='string'?card.brief.OrderName:'Название не раскрыто')
function loadRecovery() {
 let refresh:ProfileCommand|null=null,question:RunAttempt|null=null,operations:Operation[]=[],refreshBlocked=false,questionBlocked=false
 let auto:ScheduleCommand|null=null,autoBlocked=false
 try {const raw=localStorage.getItem(refreshKey);if(raw)refresh=readProfileCommand(JSON.parse(raw))}catch{refreshBlocked=true}
 try {const raw=localStorage.getItem(scheduleKey);if(raw)auto=readScheduleCommand(JSON.parse(raw))}catch{autoBlocked=true}
 try {const raw=localStorage.getItem(questionKey);if(raw)question=readRunAttempt(JSON.parse(raw));const saved=localStorage.getItem(operationsKey);if(saved)operations=readOperations(JSON.parse(saved))}catch{questionBlocked=true}
 return {refresh,question,operations,refreshBlocked,questionBlocked,auto,autoBlocked}
}
function initialSource():ProfileSource {
 const url=new URLSearchParams(location.hash.split('?')[1]||'').get('profile');if(profileSource(url))return url
 try{const saved=localStorage.getItem('bervel.radar.profile.selected');if(profileSource(saved))return saved}catch{/* Selection storage does not gate reading. */}
 return 'test_api'
}
function CardStatus({card}:{card:RunCard}) {
 const trade=card.metrics?.trade_type??'unknown'
 return <p className="ai-radar-card-state"><span data-actuality={card.metrics?.actuality}>{card.metrics?.actuality==='active'?'Актуален':card.metrics?.actuality==='inactive'?'Неактуален':'Актуальность не указана'}</span><span>{card.information_state==='primary' || card.details_loaded===false?'Первичная информация':'Подробная информация'}</span><span>{trade==='commercial_small'?'Коммерческие · Малые закупки':tradeLabels[trade]}</span>{card.state==='failed' && <span>Ошибка обработки</span>}{!!card.refresh_warnings?.length && <span>Есть замечания обновления</span>}</p>
}
function ProfileCardDetails({card,run,send,locked,operations,pendingQuestion,error,retry}:{card:RunCard;run:Run;send:(command:RunAttempt)=>void;locked:boolean;operations:Operation[];pendingQuestion:RunAttempt|null;error:string;retry?:()=>void}) {
 const [question,setQuestion]=useState(''),summary=getReviewSummary(card)
 const refreshWarnings=card.refresh_warnings??[]
 const reports=(run.reports??[]).filter((report):report is QuestionReport=>report.kind==='question' && report.analysis_id===card.analysis_id)
 const messages=[...new Map(reports.map(report=>[report.job_id,report])).values()]
 for(const operation of operations)if(operation.kind==='question' && operation.analysis_id===card.analysis_id && !messages.some(report=>report.job_id===operation.job_id))messages.push({job_id:operation.job_id,kind:'question',analysis_id:card.analysis_id,question:operation.question??'',state:operation.state,error:operation.error,result:null})
 const pending=pendingQuestion?.path===`/${run.run_id}/question` && pendingQuestion.body.analysis_id===card.analysis_id && !reports.some(report=>report.command_key===pendingQuestion.body.command_key)?pendingQuestion:null
 const chatLocked=locked || (run.reports??[]).some(report=>report.kind==='question' && report.result===null && report.state!==undefined && !terminal(report.state))
 const chatHistory=useRef<HTMLDivElement>(null)
 const submitted=pending?.body.command_key || operations.filter(operation=>operation.kind==='question' && operation.analysis_id===card.analysis_id).at(-1)?.job_id
 useEffect(()=>{if(submitted && chatHistory.current)chatHistory.current.scrollTop=chatHistory.current.scrollHeight},[submitted])
 const primary=card.information_state==='primary' || card.details_loaded===false
 return <div className="radar-detail-split"><section className="radar-detail-info" aria-label="Информация о тендере"><div className="run-card-heading"><h3><SourceTitle title={cardTitle(card)} link={card.tender?.link}/></h3><p className="ai-radar-meta">{card.tender?.number || card.candidate_id} · <CardDate card={card}/></p><CardStatus card={card}/></div><div className="run-metrics"><RunCardMetrics card={card}/></div>{(card.tender?.application_deadline || typeof card.brief?.ApplicationDeadline==='string') && <p className="ai-radar-meta">Подача заявки до: {applicationDeadline(card.tender?.application_deadline || String(card.brief?.ApplicationDeadline))}</p>}<section className="run-review"><h4>{card.projection?.metrics_review_stale?'Ревью до обновления показателей':'AI-ревью'}</h4>{summary?<>{(summary.paragraphs??[summary.reason]).map((p,i)=><p key={i}>{i===0 && <strong>{reviewDecisions[summary.decision]}. </strong>}{p}</p>)}{!!summary.checks?.length && <p>{summary.checks.join(' ')}</p>}</>:<p className="run-muted">{primary?'Полный анализ появится после получения подробностей при обновлении.':'Ревью пока не получено.'}</p>}{card.error && <p role="alert" className="error-note">{card.error.message}</p>}</section>{primary && <p className="run-partial">Первичная информация. Подробности ещё не получены. Открытие карточки и вопрос не расходуют квоту подробных карточек.{card.priority_score!=null && <> Приоритет получения подробностей: {card.priority_score}/100. {card.priority_reason}</>}</p>}
 {!!refreshWarnings.length && <section className="run-review run-partial" aria-label="Замечания последнего обновления"><h4>Замечания последнего обновления</h4>{card.analysis && refreshWarnings.some(warning=>warning.stage==='analysis') && <p>Показан сохранённый анализ.</p>}<ul>{refreshWarnings.map((warning,index)=><li key={`${warning.stage}:${warning.code}:${index}`}><strong>{refreshStageLabels[warning.stage]}: </strong>{warning.message}</li>)}</ul></section>}
 {card.analysis && card.tender && <CardDetails job={{analysis:card.analysis,tender:card.tender}}/>}
 {!!card.tender?.documents?.length && <section className="radar-tender-documents"><h4>Документы</h4><ul>{card.tender.documents.map((document,i)=><li key={i}>{/^https?:\/\//i.test(document.url)?<a href={document.url} target="_blank" rel="noreferrer noopener">{document.filename || `Документ ${i+1}`}</a>:<span>{document.filename || `Документ ${i+1}`} — ссылка не раскрыта</span>}</li>)}</ul></section>}</section>
 <section className="radar-detail-chat" aria-label="Чат по тендеру"><header className="radar-chat-heading"><h3>Чат по тендеру</h3>{error && <p className="error-note" role="alert">{error}</p>}{retry && <button type="button" className="secondary" onClick={retry}>Повторить сохранённый вопрос</button>}<p className="run-muted">Вопросы и ответы сохраняются в этой карточке.</p>{primary && <p className="run-muted">Доступна первичная информация. Если для ответа нужны позиции или документы, получите подробности через обновление профиля.</p>}</header><div className="radar-chat-history" ref={chatHistory} role="log" aria-label="История сообщений тендера">{messages.map(report=><div className="radar-chat-exchange" key={report.job_id}><div className="radar-chat-message" data-author="user"><strong>Вы</strong><p>{report.question}</p></div>{report.result?.state==='complete'?<div className="radar-chat-message" data-author="assistant"><strong>ИИ</strong><p>{report.result.answer || report.result.recommendation || 'Ответ не получен.'}</p></div>:<p className="run-operation" role="status">{report.result?.error?.message || report.error?.message || (report.state==='failed' || report.result?.state==='failed'?'Ответ не получен.':report.state==='running'?'Готовим ответ…':report.state && terminal(report.state)?'Ответ пока не сохранён.':'Вопрос в очереди…')}</p>}</div>)}{pending && <div className="radar-chat-exchange" key={pending.body.command_key}><div className="radar-chat-message" data-author="user"><strong>Вы</strong><p>{String(pending.body.question)}</p></div><p className="run-operation" role="status">Отправка не подтверждена.</p></div>}{!messages.length && !pending && <p className="run-muted">Задайте первый вопрос об этом тендере.</p>}</div><form onSubmit={e=>{e.preventDefault();if(chatLocked || !question.trim())return;send({path:`/${run.run_id}/question`,body:{command_key:crypto.randomUUID(),analysis_id:card.analysis_id,question:question.trim()}});setQuestion('')}}><label>Вопрос по тендеру<input type="text" value={question} maxLength={2000} required disabled={chatLocked} onChange={e=>setQuestion(e.target.value)} placeholder="Условия, состав закупки, риски"/></label><button type="submit" className="secondary" disabled={chatLocked || !question.trim()}>Спросить</button></form></section></div>
}
export default function AiTenderRadarPage() {
 const [initial]=useState(loadRecovery),[source,setSource]=useState(initialSource),[profiles,setProfiles]=useState<RadarProfile[]>([]),[profile,setProfile]=useState<RadarProfile|null>(null),[run,setRun]=useState<Run|null>(null)
 const [error,setError]=useState(''),[notice,setNotice]=useState(''),[reading,setReading]=useState(true),[revision,setRevision]=useState(0),[profileOpen,setProfileOpen]=useState(false),[refreshOpen,setRefreshOpen]=useState(false),[busy,setBusy]=useState(false),[questionBusy,setQuestionBusy]=useState(false),[saving,setSaving]=useState(false)
 const [pendingRefresh,setPendingRefresh]=useState(initial.refresh),[pendingQuestion,setPendingQuestion]=useState(initial.question),[operations,setOperations]=useState(initial.operations)
 const [mode,setMode]=useState<RadarProfile['keyword_mode']>('auto'),[words,setWords]=useState(''),[detailMax,setDetailMax]=useState(''),[schedule,setSchedule]=useState(defaultSchedule),[scheduleDays,setScheduleDays]=useState('1')
 const [reanalyzeExisting,setReanalyzeExisting]=useState(false)
 const [autoOpen,setAutoOpen]=useState(false),[autoBusy,setAutoBusy]=useState(false),[autoError,setAutoError]=useState(''),[autoNotice,setAutoNotice]=useState(''),[autoReanalyzeExisting,setAutoReanalyzeExisting]=useState(false),[pendingAuto,setPendingAuto]=useState(initial.auto)
 const autoBusyRef=useRef(false)
 const [filters,setFilters]=useState(emptyRadarFilters),[query,setQuery]=useState(''),[sort,setSort]=useState('coverage'),[limit,setLimit]=useState(50),[expanded,setExpanded]=useState<string|null>(()=>new URLSearchParams(location.hash.split('?')[1]||'').get('analysis_id'))
 const sourceRef=useRef(source),busyRef=useRef(false),questionBusyRef=useRef(false),live=useRef(true),readProfileRef=useRef(()=>{}),operationsRef=useRef(operations)
 useEffect(()=>{sourceRef.current=source},[source])
 useEffect(()=>{operationsRef.current=operations},[operations])
 const active=isActive(run),questionLocked=questionBusy || !!pendingQuestion || initial.questionBlocked || operations.some(o=>o.kind==='question' && !terminal(o.state))
 const currentNotice=run?.data.metrics_job?.state==='loaded' && pendingRefresh?.kind!=='metrics' && (!notice || notice==='Обновление показателей принято.')?'Показатели обновлены.':notice
 useEffect(()=>{live.current=true;return()=>{live.current=false}},[])
 useEffect(()=>{let alive=true;const controller=new AbortController();void listProfiles(controller.signal).then(p=>{if(alive)setProfiles(p)}).catch(e=>{if(alive)setError(errorText(e))});return()=>{alive=false;controller.abort()}},[])
 useEffect(()=>{
  let alive=true,inFlight=false,requested=false,polling=false,timer:ReturnType<typeof setTimeout>|undefined
  const controller=new AbortController()
  async function read() {
   if(!alive)return
   if(inFlight){requested=true;return}
   clearTimeout(timer);inFlight=true
   try {
    const value=await readProfile(source,controller.signal)
    if(!alive)return
    polling=isActive(value.run)
    setProfile(value.profile);setRun(value.run);setProfiles(old=>old.length?old.map(p=>p.source===source?value.profile:p):old);setError('')
   } catch(e) {if(alive&&!controller.signal.aborted)setError(errorText(e))}
   finally {
    inFlight=false
    if(alive){
     setReading(false)
     if(requested){requested=false;void read()}
     else if(polling||operationsRef.current.length)timer=setTimeout(()=>void read(),2500)
    }
   }
  }
  readProfileRef.current=()=>void read()
  void read()
  return()=>{alive=false;clearTimeout(timer);controller.abort();readProfileRef.current=()=>{}}
 },[source])
 useEffect(()=>{if(revision)readProfileRef.current()},[revision])
 useEffect(()=>{if(!operations.length)return;let alive=true;const poll=async()=>{const next=await Promise.all(operations.map(async o=>{if(terminal(o.state))return o;try{const job=await readOperationJob(o.job_id);return {...o,state:job.state,error:job.error??undefined}}catch{return o}}));if(!alive)return;const completed=next.filter(o=>terminal(o.state));const remaining=next.filter(o=>!terminal(o.state));try{localStorage.setItem(operationsKey,JSON.stringify(remaining))}catch{setError('Не удалось сохранить состояние вопроса.')}setOperations(remaining);if(completed.length)setRevision(r=>r+1);const failed=next.find(o=>o.state==='failed');if(failed)setError(failed.error?.message || 'Задание вопроса завершилось ошибкой.')};const timer=setTimeout(()=>void poll(),1500);return()=>{alive=false;clearTimeout(timer)}},[operations])
 function updateFilters(value:typeof filters){setLimit(50);setFilters(value)}
 function choose(next:ProfileSource) {if(next===source)return;setError('');setReading(true);setLimit(50);setSource(next);setRun(null);setProfile(null);setExpanded(null);setFilters(emptyRadarFilters);setQuery('');try{localStorage.setItem('bervel.radar.profile.selected',next)}catch{/* Reading remains available. */}const params=new URLSearchParams(location.hash.split('?')[1]||'');params.set('profile',next);params.delete('run_id');params.delete('analysis_id');history.replaceState(null,'',location.hash.split('?')[0]+'?'+params)}
 function openProfiles() {if(profile){setMode(profile.keyword_mode);setWords(profile.keywords.join('\n'))}setProfileOpen(true)}
 function openRefresh() {setDetailMax('');setReanalyzeExisting(profile?.reanalyze_existing??false);setRefreshOpen(true)}
 function openAutoSettings() {setSchedule(profile?.schedule??defaultSchedule);setScheduleDays(String(profile?.schedule.every_days??1));setAutoReanalyzeExisting(profile?.auto_reanalyze_existing??false);setAutoError('');setAutoOpen(true)}
 async function saveWords() {setSaving(true);try{const keywords=mode==='auto'?[]:[...new Set(words.split('\n').map(w=>w.trim()).filter(Boolean))];const p=await saveKeywords(source,mode,keywords);if(sourceRef.current===source){setProfile(p);setProfiles(old=>old.map(v=>v.source===source?p:v));setProfileOpen(false);setNotice('Ключевые слова профиля сохранены.')}}catch(e){setError(errorText(e))}finally{setSaving(false)}}
 async function executeProfileCommand(command:ProfileCommand) {if(busyRef.current)return;busyRef.current=true;setBusy(true);setError('');try{localStorage.setItem(refreshKey,JSON.stringify(command));setPendingRefresh(command);const receipt=await submitProfileCommand(command);localStorage.removeItem(refreshKey);if(!live.current)return;setPendingRefresh(null);setRefreshOpen(false);setNotice(command.kind==='metrics'?'Обновление показателей принято.':command.kind==='resume'?`Возобновление «${names[command.source]}» принято. Сохранённые результаты остаются в базе.`:'Обновление принято. Полученные карточки остаются в базе.');if(sourceRef.current===command.source && receipt.run_id){setRun(old=>old?{...old,data:{...old.data,state:'queued',...(command.kind==='metrics'?{phase:'metrics',metrics_job:{job_id:receipt.job_id,state:receipt.state}}:{})}}:old);setRevision(r=>r+1)}}catch(e){if(live.current){setRefreshOpen(false);setError(errorText(e));if(e instanceof RunError && e.rejected){try{localStorage.removeItem(refreshKey);setPendingRefresh(null)}catch{/* Keep the command until storage is available. */}}}}finally{busyRef.current=false;if(live.current)setBusy(false)}}
 function launchRefresh() {const detail=detailMax.trim()===''?null:Number(detailMax);if(detail!==null && (!Number.isSafeInteger(detail)||detail<0))return;void executeProfileCommand({source,body:{command_key:crypto.randomUUID(),detail_max:detail,reanalyze_existing:reanalyzeExisting}})}
 async function executeScheduleCommand(command:ScheduleCommand) {
  if(autoBusyRef.current)return
  autoBusyRef.current=true;setAutoBusy(true);setAutoError('');setAutoNotice('')
  try {
   localStorage.setItem(scheduleKey,JSON.stringify(command));setPendingAuto(command)
   const saved=await submitScheduleCommand(command)
   localStorage.removeItem(scheduleKey)
   if(!live.current)return
   setPendingAuto(null);setAutoOpen(false);setAutoNotice('Настройки автообновления сохранены.')
   setProfiles(old=>old.map(value=>value.source===command.source?{...value,schedule:saved.schedule,auto_reanalyze_existing:saved.auto_reanalyze_existing}:value))
   if(sourceRef.current===command.source)setProfile(old=>old?{...old,schedule:saved.schedule,auto_reanalyze_existing:saved.auto_reanalyze_existing}:old)
   setRevision(value=>value+1)
  } catch(e) {
   if(live.current){setAutoOpen(false);setAutoError(errorText(e));if(e instanceof RunError && e.rejected){try{localStorage.removeItem(scheduleKey);setPendingAuto(null)}catch{/* Preserve the exact command when storage is unavailable. */}}}
  } finally {autoBusyRef.current=false;if(live.current)setAutoBusy(false)}
 }
 function saveAutoSettings() {const days=Number(scheduleDays);if(!Number.isSafeInteger(days)||days<1 || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(schedule.time))return;void executeScheduleCommand({source,body:{command_key:crypto.randomUUID(),schedule:{...schedule,every_days:days},reanalyze_existing:autoReanalyzeExisting}})}
 async function sendQuestion(command:RunAttempt) {if(questionBusyRef.current)return;questionBusyRef.current=true;setQuestionBusy(true);setError('');try{localStorage.setItem(questionKey,JSON.stringify(command));setPendingQuestion(command);const receipt=await submitRunCommand(command);const op:Operation={job_id:receipt.job_id,run_id:receipt.run_id || command.path.split('/')[1],state:receipt.state,kind:'question',analysis_id:String(command.body.analysis_id),question:String(command.body.question)};const next=[...operations.filter(o=>o.job_id!==op.job_id),op];localStorage.setItem(operationsKey,JSON.stringify(next));localStorage.removeItem(questionKey);if(live.current){setPendingQuestion(null);setOperations(next);setRevision(r=>r+1)}}catch(e){if(live.current){setError(errorText(e));if(e instanceof RunError && e.rejected){try{localStorage.removeItem(questionKey);setPendingQuestion(null)}catch{/* Keep the command until storage is available. */}}}}finally{questionBusyRef.current=false;if(live.current)setQuestionBusy(false)}}
 const cards=useMemo(()=>run?.cards??[],[run]),currencies=[...new Set(cards.flatMap(c=>c.metrics?.currency?[c.metrics.currency]:[]))].sort(),currency=String(filters.currency||(currencies.includes('RUB')?'RUB':currencies[0]||''))
 const hasSavedAnalysis=cards.some(card=>!!card.analysis && card.details_loaded!==false)
 const visible=useMemo(()=>cards.filter(c=>matchesRadarFilters(c,filters,currency) && `${cardTitle(c)} ${c.tender?.number??''} ${c.candidate_id}`.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru'))).sort((a,b)=>{
  const inactive=Number(a.metrics?.actuality==='inactive')-Number(b.metrics?.actuality==='inactive');if(inactive)return inactive
  const key=sort==='benefit'?'margin':sort==='catalog'?'catalog':sort==='price'?'price':sort==='score'?'score':'coverage'
  const av=metricValue(a,key),bv=metricValue(b,key);if(av==null && bv!=null)return 1;if(av!=null && bv==null)return -1
  if(key==='price' && av!=null && bv!=null){const group=(a.metrics?.currency || '\uffff').localeCompare(b.metrics?.currency || '\uffff');if(group)return group}
  return (av==null||bv==null?0:Number(bv)-Number(av)) || a.candidate_id.localeCompare(b.candidate_id)
 }),[cards,filters,currency,query,sort])
 const expandedCard=cards.find(c=>c.analysis_id===expanded),currentOps=operations.filter(o=>o.run_id===run?.run_id)
 const detailQuota=profile?.quotas?.detail,requested=detailMax.trim()===''?null:Number(detailMax),validDetail=requested===null || Number.isSafeInteger(requested)&&requested>=0&&(detailQuota===undefined||requested<=detailQuota)
 return <main className="company-settings radar-runs ai-radar-page radar-profiles-page" aria-label="Радар ИИ"><div className="ui-toolbar radar-profile-toolbar"><button type="button" className="secondary" onClick={openProfiles}>Профили</button><strong>{names[source]}</strong><button type="button" className="primary" disabled={busy || !!pendingRefresh || initial.refreshBlocked || active || profile?.available===false || !profile} onClick={openRefresh}>Обновить</button><button type="button" className="secondary" disabled={autoBusy || !!pendingAuto || initial.autoBlocked || !profile} onClick={openAutoSettings}>Автообновление</button><button type="button" className="secondary" disabled={busy || !!pendingRefresh || initial.refreshBlocked || active || !profile || !hasSavedAnalysis} onClick={()=>void executeProfileCommand({kind:'metrics',source,body:{command_key:crypto.randomUUID()}})}>Обновить показатели</button>{run?.data.state==='failed' && <button type="button" className="secondary" disabled={busy || !!pendingRefresh || initial.refreshBlocked || active || (profile?.available===false && run.data.resume_kind!=='individual_rating' && run.data.resume_kind!=='metrics') || !profile} onClick={()=>void executeProfileCommand({kind:'resume',source,body:{command_key:crypto.randomUUID()}})}>Возобновить</button>}{active && <button type="button" className="secondary" disabled={run?.data.stop_requested} onClick={()=>{if(run)void stopRun(run.run_id).then(()=>setRevision(r=>r+1)).catch(e=>setError(errorText(e)))}}>Стоп</button>}</div>
 {error && <div className="company-alert" role="alert">{error}<button className="company-small" type="button" onClick={()=>{setError('');setRevision(r=>r+1)}}>Повторить чтение</button></div>}{currentNotice && <p className="company-notice" role="status">{currentNotice}</p>}{(initial.refreshBlocked||initial.questionBlocked) && <p className="company-alert">Не удалось прочитать сохранённые команды. Соответствующие команды заблокированы; чтение профилей доступно. Восстановите хранилище браузера и перезагрузите страницу.</p>}
 {pendingRefresh && <div className="company-recovery"><p>Исход {pendingRefresh.kind==='metrics'?'обновления показателей':pendingRefresh.kind==='resume'?'возобновления':'обновления'} «{names[pendingRefresh.source]}» не подтверждён. Повтор отправит те же параметры и ключ.</p><button type="button" className="primary" disabled={busy||initial.refreshBlocked} onClick={()=>void executeProfileCommand(pendingRefresh)}>{pendingRefresh.kind==='metrics'?'Повторить сохранённое обновление показателей':pendingRefresh.kind==='resume'?'Повторить сохранённое возобновление':'Повторить сохранённое обновление'}</button></div>}{pendingQuestion && <div className="company-recovery"><p>Исход вопроса не подтверждён. Сохранены его текст и ключ.</p><button type="button" className="primary" disabled={questionBusy||initial.questionBlocked} onClick={()=>void sendQuestion(pendingQuestion)}>Повторить сохранённый вопрос</button></div>}
 {autoError && <p className="company-alert" role="alert">{autoError}</p>}{autoNotice && <p className="company-notice" role="status">{autoNotice}</p>}{initial.autoBlocked && <p className="company-alert">Не удалось прочитать сохранённые настройки автообновления. Восстановите хранилище браузера и перезагрузите страницу.</p>}{pendingAuto && <div className="company-recovery"><p>Сохранение автообновления «{names[pendingAuto.source]}» не подтверждено.</p><button type="button" className="primary" disabled={autoBusy || initial.autoBlocked} onClick={()=>void executeScheduleCommand(pendingAuto)}>Повторить сохранение автообновления</button></div>}
 {profile?.available===false && <p className="run-partial">Источник не настроен. Сохранённая база доступна для чтения.</p>}{reading && !run && <p role="status" className="run-empty">Читаем профиль…</p>}
 {run && <div className="radar-refresh-progress" role="status"><span>{radarRefreshStatus(run)}</span><span>Поиск: {run.data.checkpoint?.search_attempts??0} / 200</span><span>Подробности: {run.data.detail_attempts??0}{run.data.effective_budgets?.detail!=null?` / ${run.data.effective_budgets.detail}`:''}</span><span>Карточек в базе: {number.format(cards.length)}</span>{run.data.error && <span>{run.data.error.message}</span>}</div>}
 <section className="run-results run-results-panel" aria-label="Результаты профиля"><div className="ai-radar-results-header"><h2>Результаты <span>{number.format(visible.length)} / {number.format(cards.length)}</span></h2><div className="ai-results-controls"><label className="radar-search"><span className="sr-only">Найти тендер</span><input type="search" placeholder="Название или номер" value={query} onChange={e=>{setLimit(50);setQuery(e.target.value)}}/></label><label className="ai-results-sort"><span className="sr-only">Сортировка карточек</span><select value={sort} onChange={e=>{setLimit(50);setSort(e.target.value)}}><option value="coverage">По покрытию склада ↓</option><option value="benefit">По маржинальности ↓</option><option value="price">По стоимости тендера ↓</option><option value="catalog">По соответствию продукции ↓</option><option value="score">По оценке ИИ ↓</option></select></label><RadarResultsFilter cards={cards} filters={filters} setFilters={updateFilters} currency={currency}/></div></div>{sort==='price' && currencies.length>1 && <p className="run-muted radar-currency-note">Стоимость упорядочена отдельно в каждой валюте: {currencies.join(', ')}. Пересчёт валют не выполняется.</p>}<div className="ai-radar-card-list">{visible.slice(0,limit).map(card=><TenderCard key={card.analysis_id} title={cardTitle(card)} onOpen={()=>setExpanded(card.analysis_id)}><div className="ai-radar-card-head"><div className="ai-radar-card-title"><p className="ai-radar-meta">{card.tender?.number || card.candidate_id} · <CardDate card={card}/></p><h3><span className="radar-title-button">{cardTitle(card)}</span></h3><CardStatus card={card}/></div><RunCardMetrics card={card}/></div></TenderCard>)}</div>{!visible.length && !reading && <p className="run-empty">{cards.length?'По этим условиям карточки не найдены.':'База пока пуста. Обновите профиль для поиска тендеров.'}</p>}{visible.length>limit && <div className="radar-pagination"><span>Показано {Math.min(limit,visible.length)} из {number.format(visible.length)}</span><button type="button" className="secondary" onClick={()=>setLimit(n=>n+50)}>Показать ещё 50</button></div>}</section>
 {profileOpen && <WorkspaceDialog title="Профили" onClose={()=>{if(!saving)setProfileOpen(false)}}><div className="radar-profile-settings"><div className="radar-profile-choices">{(['test_api','kontur_api'] as const).map(s=><button type="button" className="ui-choice" aria-pressed={source===s} key={s} disabled={saving} onClick={()=>{const p=profiles.find(v=>v.source===s);choose(s);setMode(p?.keyword_mode??'auto');setWords(p?.keywords.join('\n')??'')}}>{names[s]}</button>)}</div><form onSubmit={e=>{e.preventDefault();void saveWords()}}>{error && <p role="alert" className="error-note">{error}</p>}<label>Ключевые слова<select value={mode} disabled={saving} onChange={e=>setMode(e.target.value as RadarProfile['keyword_mode'])}><option value="auto">Авто — из продукции склада</option><option value="manual">Вручную</option></select></label>{mode==='manual' && <label>Фразы для поиска<textarea required value={words} disabled={saving} onChange={e=>setWords(e.target.value)} placeholder="Одна фраза на строку"/></label>}<button type="submit" className="primary" disabled={saving || !profile || mode==='manual' && !words.trim()}>Сохранить ключевые слова</button></form></div></WorkspaceDialog>}
 {refreshOpen && <WorkspaceDialog title={'Обновить · '+names[source]} onClose={()=>{if(!busy)setRefreshOpen(false)}}><form className="radar-refresh-form" onSubmit={e=>{e.preventDefault();launchRefresh()}}><label>Подробных карточек<input type="number" min="0" step="1" max={detailQuota} value={detailMax} disabled={busy} onChange={e=>setDetailMax(e.target.value)} placeholder="Максимум"/></label><label className="radar-check"><input type="checkbox" checked={reanalyzeExisting} disabled={busy} onChange={e=>setReanalyzeExisting(e.target.checked)}/>Проанализировать на основе измененных показателей</label><button type="submit" className="primary" disabled={busy||!!pendingRefresh||!validDetail}>Обновить базу</button></form></WorkspaceDialog>}
 {autoOpen && <WorkspaceDialog title={'Автообновление · '+names[source]} onClose={()=>{if(!autoBusy)setAutoOpen(false)}}><form className="radar-refresh-form" onSubmit={e=>{e.preventDefault();saveAutoSettings()}}><label className="radar-check"><input type="checkbox" checked={schedule.enabled} disabled={autoBusy} onChange={e=>setSchedule({...schedule,enabled:e.target.checked})}/>Автообновление</label><div className="radar-schedule-fields"><label>Каждые, дней<input type="number" min="1" step="1" required value={scheduleDays} disabled={autoBusy} onChange={e=>setScheduleDays(e.target.value)}/></label><label>Время, МСК<input type="time" required value={schedule.time} disabled={autoBusy} onChange={e=>setSchedule({...schedule,time:e.target.value})}/></label></div><label className="radar-check"><input type="checkbox" checked={autoReanalyzeExisting} disabled={autoBusy} onChange={e=>setAutoReanalyzeExisting(e.target.checked)}/>Проанализировать на основе измененных показателей</label><button type="submit" className="primary" disabled={autoBusy||!!pendingAuto||initial.autoBlocked||!Number.isSafeInteger(Number(scheduleDays))||Number(scheduleDays)<1||!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(schedule.time)}>Сохранить</button></form></WorkspaceDialog>}
 {expandedCard && run && <WorkspaceDialog className="radar-tender-dialog" title="Тендер" onClose={()=>setExpanded(null)}><ProfileCardDetails key={expandedCard.analysis_id} card={expandedCard} run={run} locked={questionLocked || expandedCard.state==='analyzing'} error={error} retry={pendingQuestion && !questionBusy && !initial.questionBlocked?()=>void sendQuestion(pendingQuestion):undefined} send={c=>void sendQuestion(c)} operations={currentOps} pendingQuestion={pendingQuestion}/></WorkspaceDialog>}</main>
}
