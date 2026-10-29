import { useEffect, useRef, useState } from 'react'
import { artifactReady, listAllUploadedArtifacts } from './api'
import type { UploadedArtifact, UsageRole } from './api'
import { analyseRadar, listRadarRuns, readRadar, RadarError } from './radarApi'
import type { AnalyseAttempt, RadarRun } from './radarApi'
import RadarReport, { radarDate } from './RadarReport'

const runKey = 'bervel.radar.run_id', attemptKey = 'bervel.radar.analyse_attempt'
function restoredAttempt(): { attempt: AnalyseAttempt | null; obsoleteKey: string | null } {
  const empty = { attempt: null, obsoleteKey: null }
  try {
    const value = JSON.parse(localStorage.getItem(attemptKey) || 'null')
    if (typeof value?.command_key !== 'string' || !value.command_key || !value.procurement_source || !value.stock_source || !value.economics_source || !value.filters) return empty
    const refs = [value.procurement_source,value.stock_source,value.economics_source]
    // Stored unknown commands must never be reinterpreted as a different set of files.
    if (refs.some(ref => typeof ref !== 'object' || Array.isArray(ref) || Object.keys(ref).length !== 1 || typeof ref.artifact_id !== 'string')) return { attempt: null, obsoleteKey: value.command_key }
    return { attempt: value, obsoleteKey: null }
  } catch { return empty }
}
const sourceCards: {role: UsageRole; label: string}[] = [
  { role: 'procurement', label: 'Выгрузка тендеров' },
  { role: 'stock', label: 'Данные по складу' },
  { role: 'economics', label: 'Экономика и производство' },
]
const newestFirst = <T extends {created_at: string; run_id: string}>(items: T[]) => [...items].sort((a,b) => Date.parse(b.created_at)-Date.parse(a.created_at) || a.run_id.localeCompare(b.run_id))
const stateLabels = { queued: 'В очереди', running: 'Идёт анализ', completed: 'Завершён', failed: 'Ошибка / частичный результат' }
export default function RadarPage() {
  const [files,setFiles] = useState<UploadedArtifact[]>([])
  const [ids,setIds] = useState<Record<string,string>>({})
  const [shortage,setShortage] = useState('0'), [margin,setMargin] = useState('15')
  const [minPrice,setMinPrice] = useState(''), [maxPrice,setMaxPrice] = useState(''), [days,setDays] = useState('0'), [focus,setFocus] = useState('')
  const [error,setError] = useState<string|null>(null), [loading,setLoading] = useState(true), [refresh,setRefresh] = useState(0)
  const [history,setHistory] = useState<Awaited<ReturnType<typeof listRadarRuns>>>([])
  const [runId,setRunId] = useState<string|null>(() => {
    const params = new URLSearchParams(window.location.hash.split('?')[1] || '')
    return params.get('run') || params.get('run_id') || localStorage.getItem(runKey)
  })
  const [restored] = useState(restoredAttempt)
  const [run,setRun] = useState<RadarRun|null>(null), [attempt,setAttempt] = useState<AnalyseAttempt|null>(restored.attempt)
  const obsoleteKey = restored.obsoleteKey
  const [sending,setSending] = useState(false), [readRetry,setReadRetry] = useState(0)
  const inFlight = useRef(false)
  const pending = sending || run?.state === 'queued' || run?.state === 'running'
  useEffect(() => {
    let active = true
    Promise.all([listAllUploadedArtifacts(),listRadarRuns()]).then(([f,h]) => {
      if (!active) return
      setFiles(f); setHistory(newestFirst(h)); setLoading(false)
      setIds(old => Object.fromEntries(sourceCards.map(({role}) => [role, old[role] && f.some(file => file.artifact_id === old[role] && file.usage_role === role && artifactReady(file)) ? old[role] : f.find(file => file.usage_role === role && artifactReady(file))?.artifact_id || ''])))
    }).catch(reason => { if (active) {setError(reason.message);setLoading(false)} })
    return () => {active = false}
  },[refresh])
  useEffect(() => {
    if (!runId) return
    let active = true; let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      try {
        const value = await readRadar(runId)
        if (!active) return
        setRun(value)
        if (value.state === 'queued' || value.state === 'running') timer = setTimeout(() => void poll(),1000)
        else setHistory(old => newestFirst([{run_id:value.run_id,batch_id:value.batch_id,state:value.state,created_at:value.created_at,focus:value.focus},...old.filter(item => item.run_id !== value.run_id)]))
      } catch (reason) {if (active) setError(reason instanceof Error ? reason.message : 'Не удалось прочитать запуск.')}
    }
    void poll(); return () => {active = false;clearTimeout(timer)}
  },[runId,readRetry])
  async function submit() {
    if (inFlight.current || pending || obsoleteKey) return
    const selected = sourceCards.map(({role}) => files.find(file => file.artifact_id === ids[role] && file.usage_role === role && artifactReady(file)))
    if (!attempt && selected.some(file => !file)) return
    const current = attempt || {
      command_key: crypto.randomUUID(),
      procurement_source: {artifact_id:selected[0]!.artifact_id},
      stock_source: {artifact_id:selected[1]!.artifact_id},
      economics_source: {artifact_id:selected[2]!.artifact_id},
      filters: {min_price:minPrice || null,max_price:maxPrice || null,min_days_remaining:Number(days),focus,max_shortage_percent:shortage,min_margin_percent:margin},
    }
    if (!attempt && minPrice && maxPrice && Number(minPrice) > Number(maxPrice)) {setError('Цена от не должна превышать цену до.');return}
    inFlight.current = true;setSending(true);setError(null);setAttempt(current);localStorage.setItem(attemptKey,JSON.stringify(current))
    try {
      const value = await analyseRadar(current)
      setRun(value);setRunId(value.run_id);localStorage.setItem(runKey,value.run_id)
      setAttempt(null);localStorage.removeItem(attemptKey);setReadRetry(v => v+1)
    } catch (reason) {
      if (reason instanceof RadarError && reason.rejected) {setAttempt(null);localStorage.removeItem(attemptKey)}
      setError(reason instanceof Error ? reason.message : 'Не удалось отправить запуск.')
    } finally {inFlight.current = false;setSending(false)}
  }
  return <main className="radar-page">
    <div className="page-heading"><div><h1>Тендерный радар</h1></div><button className="secondary" onClick={() => {setRefresh(v=>v+1);setReadRetry(v=>v+1)}}>Обновить источники</button></div>
    {error && <div role="alert" className="error-note">{error}<button className="ml-3 underline" onClick={() => {setError(null);setRefresh(v=>v+1);setReadRetry(v=>v+1)}}>Повторить чтение</button></div>}
    {obsoleteKey && <p role="alert" className="error-note wrap-anywhere">Сохранённый запуск устарел. Исход прежней отправки неизвестен. Повтор заблокирован: проверьте историю запусков, прежде чем принимать решение о новой отправке. Ключ команды для сверки: <code>{obsoleteKey}</code>. Исходная команда сохранена в браузере.</p>}
    <form onSubmit={event => {event.preventDefault();void submit()}} className="radar-form">
      <div className="radar-sources">{sourceCards.map(({role,label}) => <section key={role} className="source-card" aria-label={`${label}: источник`}>
        <h2>{label}</h2>
        <label className="mt-4 block text-sm"><span className="sr-only">{label}</span><select aria-label={label} value={ids[role] || ''} disabled={!!pending || !!attempt || !!obsoleteKey} onChange={event => setIds(old=>({...old,[role]:event.target.value}))}>
          <option value="">{loading ? 'Получаем файлы…' : 'Нет готового файла'}</option>{files.filter(file=>file.usage_role===role && artifactReady(file)).map(file=><option key={file.artifact_id} value={file.artifact_id}>{file.filename}</option>)}
        </select></label>
        {role==='stock' && <label className="mt-4 block text-sm">Допустимая нехватка, %<input type="number" min="0" max="100" step="0.01" required value={shortage} disabled={!!pending || !!attempt || !!obsoleteKey} onChange={event=>setShortage(event.target.value)}/></label>}
      </section>)}</div>
      <div className="flex justify-between flex-wrap gap-2 my-3 text-xs text-muted"><span>В списках только полностью обработанные файлы нужного назначения.</span><a href="#/files" className="text-accent underline">Загрузить или назначить файлы</a></div>
      <fieldset className="radar-filters"><legend className="sr-only">Фильтры отбора</legend>
        <label>Цена от, ₽<input type="number" min="0" step="0.01" value={minPrice} disabled={!!pending || !!attempt || !!obsoleteKey} onChange={event=>setMinPrice(event.target.value)} placeholder="Без ограничения"/></label>
        <label>Цена до, ₽<input type="number" min="0" step="0.01" value={maxPrice} disabled={!!pending || !!attempt || !!obsoleteKey} onChange={event=>setMaxPrice(event.target.value)} placeholder="Без ограничения"/></label>
        <label>Запас до закрытия заявок, дней<input type="number" min="0" max="3650" step="1" required value={days} disabled={!!pending || !!attempt || !!obsoleteKey} onChange={event=>setDays(event.target.value)}/></label>
        <label>Минимальная маржа, %<input type="number" min="0" max="100" step="0.01" required value={margin} disabled={!!pending || !!attempt || !!obsoleteKey} onChange={event=>setMargin(event.target.value)}/></label>
        <label className="focus-filter">Фокус отбора, если нужен<textarea rows={1} maxLength={1000} value={focus} disabled={!!pending || !!attempt || !!obsoleteKey} onChange={event=>setFocus(event.target.value)} placeholder="Например: болты М16, горячее цинкование"/></label>
        <div className="radar-filter-submit"><button type="submit" className="primary" disabled={!!pending || !!obsoleteKey || (!attempt && sourceCards.some(({role})=>!ids[role]))}>{sending?'Отправляем…':attempt?'Повторить запуск':'Подобрать тендеры'}<span aria-hidden="true"> ↗</span></button></div>
      </fieldset>
      {attempt && !sending && <p className="text-xs text-amber-900 mt-2">Отправка не подтверждена. Повтор использует прежнюю команду и выбор файлов.</p>}
    </form>
    <div className="radar-results workspace-columns">
      <section className="result-column" aria-label="Результаты отбора">
        {pending && <p role="status" className="panel text-accent">{run?.state==='running'?'Анализируем закупки…':'Запуск в очереди…'} Отчёт сохранится, страницу можно закрыть.</p>}
        {run?.state==='failed' && <p role="alert" className="error-note">{run.error || 'Анализ прерван.'} {run.report?'Сохранён частичный результат.':''}</p>}
        {run?.report ? <RadarReport key={run.run_id} run={run}/> : !pending && <div className="panel empty-state"><span className="eyebrow">РЕЗУЛЬТАТ</span><h2>Закупки для вашего производства</h2><p className="text-muted text-sm">Выберите три источника и запустите подбор. Здесь появятся решения, расчёты и PDF.</p></div>}
      </section>
      <aside className="panel history-column" aria-label="История"><h2>История</h2><ul className="mt-4 space-y-2">{history.map(item=><li key={item.run_id}><button className={`history-item ${runId===item.run_id?'selected':''}`} onClick={()=>{setRun(null);setRunId(item.run_id);localStorage.setItem(runKey,item.run_id);setReadRetry(v=>v+1)}}><span>{radarDate(item.created_at)}</span><small>{stateLabels[item.state]} · {item.focus || 'По выбранным источникам'}</small></button></li>)}</ul>{!history.length && <p className="mt-4 text-sm text-muted">Запусков пока нет.</p>}</aside>
    </div>
  </main>
}
