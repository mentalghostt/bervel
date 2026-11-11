import { useEffect, useRef, useState } from 'react'
import WorkspaceDialog from './WorkspaceDialog'
import { downloadOriginal } from './downloadOriginal'
import { listPresalesSourceLines, presalesDate } from './presalesApi'
import type { PresalesDecisionPatch, PresalesLine, PresalesList, PresalesOffer, PresalesRevision } from './presalesApi'

const supplyLabels = { full_stock: 'Есть полностью', partial_or_manufacture: 'Частично + допроизводим', not_manufactured: 'Не производим', special_order: 'Спецзаказ' }
const reasonLabels: Record<string, string> = {
  SUPPLY_FACTS_UNKNOWN: 'Не хватает данных о количестве или исполнении',
  MANUFACTURE_UNKNOWN: 'Не подтверждена возможность собственного изготовления',
  NOT_MANUFACTURED: 'Предприятие не производит этот товар',
  CAPACITY_UNKNOWN: 'Не хватает подтверждённых данных о производственных мощностях',
  CAPACITY_INSUFFICIENT: 'Производственных мощностей недостаточно для заявленного количества',
  NO_CONFIRMED_CATALOG_EXECUTION: 'Нет подходящего исполнения в подтверждённом каталоге',
  PRODUCTION_NORMS_UNKNOWN: 'Не хватает подтверждённых производственных норм',
  WAREHOUSE_COST_CURRENCY_OR_CONVERSION_UNKNOWN: 'Не подтверждены складская себестоимость, валюта или перевод единиц',
  SPECIAL_ORDER_RECOMMENDATION: 'Рекомендуется рассмотреть спецзаказ',
  CLIENT_PRICE_UNKNOWN: 'Цена клиента не указана',
  CLIENT_PRICE_CURRENCY_UNKNOWN: 'Не подтверждена валюта цены клиента',
  CLIENT_PRICE_BELOW_MINIMUM_MARGIN: 'Цена клиента не обеспечивает минимальную маржу спецзаказа',
  SPECIAL_ORDER_APPROVALS_REQUIRED: 'Для спецзаказа подтвердите адаптацию и базу себестоимости',
  MODEL_NOT_CONFIGURED: 'Модель ИИ не настроена',
  MODEL_INVALID_OUTPUT: 'Модель ИИ вернула некорректный результат',
  MODEL_ERROR: 'Не удалось завершить анализ ИИ',
}
const unknownReasonLabel = 'Требуется уточнить условия исполнения'
const reviewIssueText = (issue: string) => reasonLabels[issue] || (/^[A-Z][A-Z0-9_]+$/.test(issue) ? unknownReasonLabel : issue)
const factLabels: Record<string, string> = { name: 'Название', reason: 'Причина', filename: 'Файл', location: 'Место', row: 'Строка', page: 'Страница', line: 'Строка текста', sheet: 'Лист', section_id: 'Фрагмент', artifact_id: 'Источник', unit: 'Единица', quantity: 'Количество', sku: 'Артикул', evidence: 'Доказательства', requirements: 'Требования', excerpt: 'Фрагмент оригинала', text: 'Текст', value: 'Значение', exact: 'Точное соответствие', related: 'Близкий товар', fields: 'Параметры товара', differences: 'Отличия', actual_fields: 'Подтверждённые параметры', required_value: 'Требуемое значение', requirement_id: 'Требование', field: 'Параметр', actual_value: 'Подтверждённое значение', conflict: 'Противоречие' }
const valueText = (value: unknown): string => value === null || value === undefined || value === '' ? 'Неизвестно' : typeof value === 'boolean' ? value ? 'Да' : 'Нет' : Array.isArray(value) ? value.map(valueText).join('; ') : typeof value === 'object' ? Object.entries(value).map(([key, item]) => `${factLabels[key] || key}: ${valueText(item)}`).join(' · ') : String(value)

function Facts({ facts }: { facts: Record<string, unknown>[] }) {
  const hidden = ['name', 'reason', 'message', 'excerpt', 'text', 'line_id', 'section_id', 'artifact_id']
  return <ul className="presales-facts">{facts.map((fact, index) => <li key={index}>{fact.name ? <strong>{String(fact.name)}</strong> : null}{(fact.reason || fact.message) ? <p>{String(fact.reason || fact.message)}</p> : null}{Object.keys(fact).some(key => !hidden.includes(key)) && <span>{valueText(Object.fromEntries(Object.entries(fact).filter(([key]) => !hidden.includes(key))))}</span>}{(fact.excerpt || fact.text) ? <blockquote>{String(fact.excerpt || fact.text)}</blockquote> : null}</li>)}</ul>
}
function SourceListDetails({ list, revision }: { list: PresalesList; revision: PresalesRevision }) {
  const [visible, setVisible] = useState(false), [loaded, setLoaded] = useState(false)
  const [lines, setLines] = useState<PresalesLine[]>([]), [cursor, setCursor] = useState<number | null>(0)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const mounted = useRef(false), inFlight = useRef(false)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  async function load(position: number) {
    if (inFlight.current) return
    inFlight.current = true; setBusy(true); setError('')
    try {
      const page = await listPresalesSourceLines(revision.request_id, revision.revision_id, list.list_id, position)
      if (mounted.current) { setLines(old => position === 0 ? page.items : [...old, ...page.items.filter(line => !old.some(previous => previous.line_id === line.line_id))]); setCursor(page.next_cursor); setLoaded(true) }
    } catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : 'Не удалось прочитать исходный список.') }
    finally { inFlight.current = false; if (mounted.current) setBusy(false) }
  }
  return <div className="presales-source-list">
    <button type="button" className="secondary" aria-expanded={visible} onClick={() => { setVisible(!visible); if (!visible && !loaded) void load(0) }}>{visible ? 'Скрыть позиции' : 'Показать позиции'} {list.filename}</button>
    {visible && <div role="region" aria-label={`Исходный список ${list.filename}`} className="presales-source-list-body">
      <p className="presales-muted">Оригинальные позиции из файла. Количество и требования показаны отдельно от решений менеджера.</p>
      {error && <div className="presales-alert"><p role="alert">{error}</p><button className="secondary" type="button" disabled={busy} onClick={() => void load(cursor ?? 0)}>Повторить загрузку списка {list.filename}</button></div>}
      {!!lines.length && <div className="presales-table-scroll" tabIndex={0} aria-label={`Таблица исходного списка ${list.filename}`}><table className="presales-source-lines"><thead><tr><th>Исходная позиция</th><th>Количество</th><th>Единица</th><th>Характеристики и источники</th></tr></thead><tbody>{lines.map(line => <tr key={line.line_id}><td><strong>{line.name}</strong></td><td>{line.quantity ?? 'Неизвестно'}</td><td>{line.unit ?? 'Неизвестно'}</td><td><p>{line.requirements.map(item => `${item.name}: ${item.value}`).join(' · ') || 'Характеристики не указаны'}</p><details><summary>Источники · {line.evidence.length}</summary><Facts facts={line.evidence} /></details></td></tr>)}</tbody></table></div>}
      {busy && <p role="status" className="presales-muted">Получаем позиции исходного списка…</p>}
      {loaded && !lines.length && !busy && <p className="presales-muted">В этом списке пока нет извлечённых позиций.</p>}
      {loaded && cursor !== null && !error && <button type="button" className="secondary presales-more" disabled={busy} onClick={() => void load(cursor)}>Показать ещё позиции списка {list.filename}</button>}
    </div>}
  </div>
}
function LineEditor({ line, revision, onClose, onSave, busy }: { line: PresalesLine; revision: PresalesRevision; onClose: () => void; onSave: (decisions: PresalesDecisionPatch) => void; busy: boolean }) {
  const [draft, setDraftState] = useState({ name: line.name, quantity: line.quantity || '', unit: line.unit || '', client_unit_price: line.client_unit_price || '', currency: line.currency || '', requirements: line.requirements.map(item => ({ ...item })) })
  const previous = revision.decisions?.approvals?.[line.line_id]
  const [sku, setSku] = useState(previous?.sku || line.selected_sku || '')
  const [analog, setAnalog] = useState(previous?.analog_approved || false), [adaptation, setAdaptation] = useState(previous?.adaptation_approved || false), [cost, setCost] = useState(previous?.cost_approved || false)
  function setDraft(value: typeof draft) {
    if (value.name !== draft.name || value.unit !== draft.unit || JSON.stringify(value.requirements) !== JSON.stringify(draft.requirements)) { setAnalog(false); setAdaptation(false); setCost(false) }
    setDraftState(value)
  }
  const valid = !!draft.name.trim() && (!draft.quantity || /^\d+(?:\.\d+)?$/.test(draft.quantity) && Number(draft.quantity) > 0) && (!draft.client_unit_price || /^\d+(?:\.\d+)?$/.test(draft.client_unit_price) && Number(draft.client_unit_price) > 0)
  function save() {
    const values = { ...draft, quantity: draft.quantity || null, unit: draft.unit || null, client_unit_price: draft.client_unit_price || null, currency: draft.currency || null }
    const patch = Object.fromEntries(Object.entries(values).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(line[key as keyof typeof values])))
    onSave({ ...(Object.keys(patch).length ? { line_updates: { [line.line_id]: patch } } : {}), approvals: { [line.line_id]: sku ? { sku, analog_approved: analog, adaptation_approved: adaptation, cost_approved: cost } : null } })
  }
  const candidates = line.candidates || []
  return <WorkspaceDialog title="Уточнение позиции" onClose={onClose} wide>
    <form className="presales-editor" onSubmit={event => { event.preventDefault(); if (valid) save() }}>
      <section className="presales-original"><h3>Исходные факты</h3><p>{line.name} · {line.quantity ?? 'Неизвестно'} {line.unit}</p><Facts facts={line.evidence} /></section>
      <fieldset disabled={busy}><legend>Уточнения менеджера</legend><p className="presales-muted">Сохранятся в новой редакции. Исходные файлы и прежний результат останутся в истории.</p>
        <label>Наименование<input value={draft.name} onChange={event => setDraft({ ...draft, name: event.target.value })} required /></label>
        <div className="presales-form-grid">{([{ key: 'quantity', label: 'Количество' }, { key: 'unit', label: 'Единица' }, { key: 'client_unit_price', label: 'Цена клиента за единицу' }, { key: 'currency', label: 'Валюта цены клиента' }] as const).map(({ key, label }) => <label key={key}>{label}<input inputMode={key === 'quantity' || key === 'client_unit_price' ? 'decimal' : 'text'} value={draft[key]} onChange={event => setDraft({ ...draft, [key]: event.target.value })} /></label>)}</div>
        <h3>Требования к товару</h3>{draft.requirements.map((item, index) => <div className="presales-requirement" key={index}><label>Параметр {index + 1}<input value={item.name} onChange={event => setDraft({ ...draft, requirements: draft.requirements.map((old, i) => i === index ? { ...old, name: event.target.value } : old) })} /></label><label>Значение {index + 1}<input value={item.value} onChange={event => setDraft({ ...draft, requirements: draft.requirements.map((old, i) => i === index ? { ...old, value: event.target.value } : old) })} /></label><button className="secondary" type="button" aria-label={`Удалить параметр ${index + 1}`} onClick={() => setDraft({ ...draft, requirements: draft.requirements.filter((_, i) => i !== index) })}>×</button></div>)}
        <button type="button" className="secondary" onClick={() => setDraft({ ...draft, requirements: [...draft.requirements, { name: '', value: '' }] })}>Добавить параметр</button>
        {!!candidates.length && <><label>Вариант исполнения<select value={sku} onChange={event => { setSku(event.target.value); setAnalog(false); setAdaptation(false); setCost(false) }}><option value="">Не выбран</option>{candidates.map(candidate => <option key={candidate.sku} value={candidate.sku}>{candidate.sku} · {candidate.name}</option>)}</select></label><div className="presales-candidates">{candidates.map(candidate => <article key={candidate.sku}><h4>{candidate.sku} · {candidate.name}</h4><p>Отличия: {valueText(candidate.differences)}</p><details><summary>Технические доказательства</summary><p>{valueText(Object.fromEntries(Object.entries(candidate).filter(([key]) => !['sku', 'name', 'differences'].includes(key))))}</p></details></article>)}</div>
          <div className="presales-approvals"><label><input type="checkbox" checked={analog} disabled={!sku} onChange={event => setAnalog(event.target.checked)} />Подтверждаю аналог</label><label><input type="checkbox" checked={adaptation} disabled={!sku} onChange={event => setAdaptation(event.target.checked)} />Подтверждаю адаптацию</label><label><input type="checkbox" checked={cost} disabled={!sku} onChange={event => setCost(event.target.checked)} />Подтверждаю базу себестоимости</label></div></>}
        {!valid && <p role="alert" className="presales-validation">Укажите наименование и положительное количество. Для чисел используйте точку.</p>}
        <div className="presales-form-footer"><button className="secondary" type="button" onClick={onClose}>Отмена</button><button className="primary" type="submit" disabled={!valid}>Сохранить новую редакцию</button></div>
      </fieldset>
    </form>
  </WorkspaceDialog>
}
function LineBreakdown({ line }: { line: PresalesLine }) {
  const special = line.special_order
  return <>{!!line.offers?.length && <details><summary>Предложение по SKU</summary><ul className="presales-facts">{line.offers.map((offer, index) => <li key={index}><span>{`${offer.sku} · ${offer.quantity} ${offer.unit || ''} · ${offer.unit_price ?? 'Неизвестно'} ${offer.currency || ''} · сумма ${offer.line_total ?? 'Неизвестно'} ${offer.currency || ''}`}</span><small>Готовность: {presalesDate(typeof offer.ready_at === 'string' ? offer.ready_at : null)}</small></li>)}</ul></details>}{special && <details><summary>Рекомендация спецзаказа</summary><p>{typeof special.reason === 'string' ? special.reason : 'Подтверждённая рекомендация отсутствует.'}</p>{Array.isArray(special.evidence) && <Facts facts={special.evidence} />}<p>Адаптация: {special.adaptation_approved ? 'Подтверждена' : 'Требует проверки'}</p><p>База себестоимости: {special.cost_approved ? 'Подтверждена' : 'Требует проверки'}</p></details>}</>
}
function RequestEditor({ revision, onSave, onClose, busy }: { revision: PresalesRevision; onSave: (decisions: PresalesDecisionPatch) => void; onClose: () => void; busy: boolean }) {
  const extracted = revision.extracted
  const localDeadline = extracted?.requested_deadline && Number.isFinite(Date.parse(extracted.requested_deadline)) ? new Date(extracted.requested_deadline).toLocaleString('sv-SE', { timeZone: 'Europe/Moscow' }).replace(' ', 'T').slice(0, 16) : ''
  const [client, setClient] = useState(extracted?.client || ''), [deadline, setDeadline] = useState(localDeadline), [primary, setPrimary] = useState(extracted?.primary_list_id || '')
  return <WorkspaceDialog title="Уточнение заявки" onClose={onClose}><form className="presales-editor" onSubmit={event => { event.preventDefault(); onSave({ client, requested_deadline: deadline ? `${deadline}:00+03:00` : '', ...(primary ? { primary_list_id: primary } : {}) }) }}><fieldset disabled={busy}>
    <label>Клиент<input value={client} onChange={event => setClient(event.target.value)} /></label><label>Срок клиента<input type="datetime-local" value={deadline} onChange={event => setDeadline(event.target.value)} aria-describedby="presales-deadline-zone" /></label><p id="presales-deadline-zone" className="presales-muted">Дата и время по Москве.</p>
    {!!extracted?.lists.length && <label>Основной список<select value={primary} onChange={event => setPrimary(event.target.value)}><option value="" disabled>Выберите основной список</option>{extracted.lists.map(list => <option key={list.list_id} value={list.list_id}>{list.filename}{list.position_count === undefined ? '' : ` · ${list.position_count} позиций`}</option>)}</select></label>}
    <p className="presales-muted">Уточнения создадут новую редакцию анализа и КП.</p><button type="submit" className="primary">Сохранить новую редакцию</button>
  </fieldset></form></WorkspaceDialog>
}
function Offer({ offer, revision }: { offer: PresalesOffer; revision: PresalesRevision }) {
  const [downloading, setDownloading] = useState(''), [error, setError] = useState('')
  async function download(kind: 'pdf' | 'docx') {
    const id = revision.artifacts?.[kind]; if (!id || downloading) return
    setDownloading(kind); setError('')
    try { await downloadOriginal({ artifact_id: id }, `${offer.number.replace(/[<>:"/\\|?*]/g, '-')}.${kind}`) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Не удалось скачать документ.') }
    finally { setDownloading('') }
  }
  return <section className="panel presales-offer" aria-label="Черновик коммерческого предложения"><header className="presales-section-heading"><div><p className="detail-kicker">КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ</p><h2>Черновик КП</h2></div><div className="ui-toolbar">{(['pdf', 'docx'] as const).map(kind => <button key={kind} type="button" className="secondary" disabled={!revision.artifacts?.[kind] || !!downloading} onClick={() => void download(kind)}>{downloading === kind ? 'Скачиваем…' : `Скачать ${kind.toUpperCase()}`}</button>)}</div></header>
    {error && <p role="alert" className="presales-alert">{error}</p>}
    <div className="presales-offer-letter"><div className="presales-offer-heading"><strong>BERVEL</strong><p>№ {offer.number} · {presalesDate(offer.date)}</p></div><p>{offer.client || 'Клиент не указан'}</p><p className="presales-preline">{offer.seller_contacts}</p>
      {offer.lines.length ? <div className="presales-table-scroll" role="region" aria-label="Товары коммерческого предложения" tabIndex={0}><table><thead><tr><th>Товар и характеристики</th><th>Количество</th><th>Цена</th><th>Сумма</th></tr></thead><tbody>{offer.lines.map((line, index) => <tr key={index}><td>{valueText(line.name)}{line.requirements ? <small>{valueText(line.requirements)}</small> : null}</td><td>{valueText(line.quantity)} {String(line.unit || '')}</td><td>{valueText(line.unit_price)} {String(line.currency || '')}</td><td>{valueText(line.line_total)} {String(line.currency || '')}</td></tr>)}</tbody></table></div> : <p className="presales-muted">Подтверждённых строк предложения пока нет.</p>}
      <dl className="presales-offer-totals">{Object.entries(offer.totals).map(([currency, total]) => <div key={currency}><dt>Итого, {currency}</dt><dd>{total}</dd></div>)}</dl><p className="presales-preline">{offer.offer_terms}</p>
      {!!offer.clarifications.length && <section><h3>Требуют уточнения</h3><Facts facts={offer.clarifications} /></section>}{!!offer.unsupported.length && <section><h3>Не предложены</h3><Facts facts={offer.unsupported} /></section>}
    </div>
  </section>
}
export default function PresalesReview({ revision, lines, nextCursor, loadingLines, onMore, editable, busy, onDecisions }: { revision: PresalesRevision; lines: PresalesLine[]; nextCursor: number | null; loadingLines: boolean; onMore: () => void; editable: boolean; busy: boolean; onDecisions: (decisions: PresalesDecisionPatch) => void }) {
  const [edit, setEdit] = useState<PresalesLine | null>(null), [editRequest, setEditRequest] = useState(false)
  const metrics = revision.metrics, extracted = revision.extracted
  const revisionWarnings = revision.warnings.filter(warning => !extracted?.warnings?.includes(warning))
  return <>
    <dl className="presales-metrics"><div><dt>Соответствие продукции</dt><dd>{metrics?.product_match_percent === null || metrics?.product_match_percent === undefined ? 'Неизвестно' : `${metrics.product_match_percent}%`}{metrics?.product_match_incomplete && <small>Требует уточнения</small>}</dd></div><div><dt>Покрытие склада</dt><dd data-testid="presales-stock">{metrics?.stock_coverage_percent === null || metrics?.stock_coverage_percent === undefined ? 'Неизвестно' : `${metrics.stock_coverage_percent}%`}</dd>{metrics?.stock_coverage_incomplete && <small>Неполные данные</small>}</div><div><dt>Оценка ИИ</dt><dd data-testid="presales-score">{revision.score === null || revision.score === undefined ? 'Не оценено' : `${revision.score} / 100`}</dd></div><div><dt>Срок клиента</dt><dd className="presales-date-value">{presalesDate(extracted?.requested_deadline)}</dd></div></dl>
    {(revision.review || revision.model_error) && <section className="panel presales-ai-review"><h2>Анализ ИИ</h2>{revision.review?.text && <p className="presales-preline">{revision.review.text}</p>}{!!revision.review?.issues.length && <ul>{revision.review.issues.map((issue, i) => <li key={i}>{reviewIssueText(issue)}</li>)}</ul>}{revision.model_error && <p className="presales-alert">Оценка модели не завершена: {valueText(revision.model_error)}</p>}</section>}
    {!!revisionWarnings.length && <ul className="presales-alert">{revisionWarnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>}
    <section className="panel"><header className="presales-section-heading"><div><h2>Состав заявки</h2><p className="presales-muted">Оригинальные требования, исполнение и подтверждённая экономика</p></div><button className="secondary" type="button" disabled={!editable || busy} onClick={() => setEditRequest(true)}>Уточнить заявку</button></header>
      {!!extracted?.warnings?.length && <ul className="presales-alert">{extracted.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>}
      {extracted?.lists && extracted.lists.length > 1 && <details className="presales-lists"><summary>Списки и отличия · {extracted.lists.length}</summary><ul>{extracted.lists.map(list => <li key={list.list_id}><strong>{list.filename}</strong> · {list.position_count === undefined ? '' : `${list.position_count} позиций · `}{list.list_id === extracted.primary_list_id ? 'Основной список' : 'Дополнительный список'}{!list.complete && ' · Разбор неполный'}<SourceListDetails list={list} revision={revision} /></li>)}</ul></details>}
      <div className="presales-table-scroll" role="region" aria-label="Позиции заявки" tabIndex={0}><table className="presales-lines"><thead><tr><th>Позиция и требования</th><th>Исполнение</th><th>Количество</th><th>Экономика</th><th>Проверка</th></tr></thead><tbody>{lines.map(line => <tr key={line.line_id}><td><strong>{line.name}</strong><small>{line.requirements.map(item => `${item.name}: ${item.value}`).join(' · ')}</small><details><summary>Источники · {line.evidence.length}</summary><Facts facts={line.evidence} /></details></td><td><span className={`presales-supply presales-supply-${line.supply_status || 'unknown'}`}>{line.supply_status ? supplyLabels[line.supply_status] : 'Требует данных'}</span><small>SKU: {line.selected_sku || 'Не выбран'}</small><small>Готовность: {presalesDate(line.ready_at)}</small></td><td><dl className="presales-quantities"><div><dt>Запрошено</dt><dd>{line.quantity ?? 'Неизвестно'} {line.unit}</dd></div><div><dt>Склад</dt><dd>{line.stock_quantity ?? 'Неизвестно'}</dd></div><div><dt>Производство</dt><dd>{line.production_quantity ?? 'Неизвестно'}</dd></div><div><dt>Предлагаем</dt><dd>{line.offered_quantity ?? 'Неизвестно'}</dd></div></dl></td><td><small>Себестоимость: {line.unit_cost ?? 'Неизвестно'} {line.currency}</small><small>Цена: {line.unit_price ?? 'Неизвестно'} {line.currency}</small><small>Сумма: {line.line_total ?? 'Неизвестно'} {line.currency}</small><small>Маржа: {line.margin_percent === null || line.margin_percent === undefined ? 'Неизвестно' : `${line.margin_percent}%`}</small><LineBreakdown line={line} /></td><td>{line.needs_review && <span className="presales-check">Требует проверки</span>}{line.reasons?.map((reason, i) => <p className="presales-line-reason" key={i}>{reasonLabels[reason] || unknownReasonLabel}</p>)}{!!line.candidates?.length && <small>Вариантов: {line.candidates.length}</small>}<button type="button" className="secondary" aria-label={`Уточнить ${line.name}`} disabled={!editable || busy} onClick={() => setEdit(line)}>Уточнить</button></td></tr>)}</tbody></table></div>
      {!lines.length && <p className="presales-muted">{['complete', 'failed', 'stopped'].includes(revision.state) ? 'Позиции пока не извлечены. Проверьте предупреждения и источники.' : 'Позиции появятся по мере разбора файлов.'}</p>}
      {nextCursor !== null && <button className="secondary presales-more" type="button" disabled={loadingLines} onClick={onMore}>{loadingLines ? 'Получаем позиции…' : 'Показать ещё позиции'}</button>}
    </section>
    <section className="panel presales-sources"><h2>Исходные документы</h2><ul>{revision.sources.map(source => <li key={source.artifact_id}><a href={`#/files?artifact_id=${encodeURIComponent(source.artifact_id)}`}>{source.filename}</a><small title={source.sha256}>SHA-256: {source.sha256}</small></li>)}</ul><p className="presales-muted">Расчёт от {presalesDate(revision.evaluated_at)}. Снимки предприятия и настроек закреплены в этой редакции.</p></section>
    {revision.offer ? <Offer offer={revision.offer} revision={revision} /> : <section className="panel"><h2>Черновик КП</h2><p className="presales-muted">Будет подготовлен по завершении доступного анализа.</p></section>}
    {edit && <LineEditor line={edit} revision={revision} onClose={() => { if (!busy) setEdit(null) }} busy={busy} onSave={decisions => { setEdit(null); onDecisions(decisions) }} />}
    {editRequest && <RequestEditor revision={revision} onClose={() => { if (!busy) setEditRequest(false) }} busy={busy} onSave={decisions => { setEditRequest(false); onDecisions(decisions) }} />}
  </>
}
