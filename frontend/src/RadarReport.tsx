import { useState } from 'react'
import type { Decision,ProductFit,RadarRun } from './radarApi'
import { downloadOriginal } from './downloadOriginal'
import { downloadRadarPdf } from './radarApi'

export const radarDate=(date:string|null)=>date?new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:'Europe/Moscow'}).format(new Date(date))+' МСК':'Неизвестно'
const labels:Record<Decision,string>={take_in_work:'Можно участвовать',needs_review:'Требует внимания',excluded:'Вне анализа',not_analysed:'Вне анализа'}
const keys:Decision[]=['take_in_work','needs_review','excluded']
const fitLabels:Record<ProductFit,string>={relevant:'Соответствует профилю',uncertain:'Недостаточно данных',irrelevant:'Вне профиля'}
const stageLabels:Record<string,string>={eligibility:'Сроки и стадия',names:'Название и описание',meaning:'Смысловая оценка',quantity:'Свободный остаток',economics:'Экономика и производство'}
const stockLabels={pass:'Проходит допуск',insufficient:'Нехватка сверх допуска',unknown:'Требует проверки'}
function sourceNotes(value:string){
 try{const parsed:unknown=JSON.parse(value);if(Array.isArray(parsed)&&parsed.every(p=>p&&typeof p==='object'&&typeof p.note==='string'))return parsed.map(p=>p.note).join('\n')}
 catch{/* Provenance may be ordinary text rather than JSON. */}
 return value
}

export default function RadarReport({run}:{run:RadarRun}){
 const report=run.report!
 const current=report.schema_version==='1.3.0'
 const count=(key:Decision)=>key==='excluded'?(report.counts.excluded||0)+(report.counts.not_analysed||0):report.counts[key]||0
 const [group,setGroup]=useState<Decision>(keys.find(key=>count(key)>0)||'take_in_work')
 const [limit,setLimit]=useState(30)
 const [downloadError,setDownloadError]=useState<string|null>(null)
 const [downloading,setDownloading]=useState(false)
 async function download(){
  if(downloading)return
  setDownloading(true);setDownloadError(null)
  try{
   const blob=await downloadRadarPdf(run.run_id),url=URL.createObjectURL(blob)
   const anchor=document.createElement('a');anchor.href=url;anchor.download=`bervel-radar-${run.run_id}.pdf`;anchor.click()
   setTimeout(()=>URL.revokeObjectURL(url),1000)
  }catch{setDownloadError('Не удалось скачать PDF. Повторите скачивание.')}finally{setDownloading(false)}
 }
 const items=report.items.filter(item=>group==='excluded'?['excluded','not_analysed'].includes(item.decision):item.decision===group)
 return <section aria-label="Отчёт радара" className="panel radar-report">
  <div className="flex flex-wrap items-start justify-between gap-4"><div><h2 className="font-display text-2xl font-semibold">Результат отбора</h2><p className="mt-2 text-sm text-muted">На {radarDate(report.as_of)} · {report.batch.filename}</p></div><button disabled={downloading} onClick={()=>{void download()}} className="rounded-lg border border-line bg-white px-4 py-2.5 text-sm font-bold text-accent">{downloading?'Готовим PDF…':'Скачать PDF'}</button></div>
  {(report.analysis_complete===false || run.state!=='completed') && <p role="note" className="error-note">Анализ не завершён. Ниже сохранённые результаты; ошибки порций перечислены отдельно.</p>}
  {report.coverage && <section className="mt-3 text-xs leading-6" aria-label="Полнота анализа"><h3 className="font-bold">Учёт исходных строк</h3><p>Строк: {report.coverage.input_rows} · уникальных закупок: {report.coverage.unique_tenders} · дублей: {report.coverage.duplicate_rows} · пропущено: {report.coverage.skipped_rows} · учтено: {report.coverage.accounted_rows} · с ошибками модели: {report.coverage.model_failed_rows}</p>{!report.coverage.complete&&<p className="text-amber-900">Есть необработанные строки.</p>}</section>}
  {!!report.portion_errors?.length && <details className="mt-3 text-sm"><summary>Ошибки порций ({report.portion_errors.length})</summary><ul>{report.portion_errors.map((error,i)=><li className="mt-2 wrap-anywhere" key={i}>{error.message} · {error.code}<p className="text-xs text-muted">Закупки: {error.ids.join(', ')}</p></li>)}</ul></details>}
  {report.selection&&<p className="mt-3 text-xs leading-6 text-muted wrap-anywhere">Допустимая нехватка: {report.selection.max_shortage_percent}% по каждому SKU. Ведомость: {report.stock_source?report.stock_source.filename:'не выбрана'}.</p>}
  {report.funnel&&<p className="mt-3 rounded-lg bg-paper p-3 text-sm leading-6">Воронка: {report.funnel.total} всего → {report.funnel.after_eligibility} после сроков → {report.funnel.after_names} после названий и описаний → {report.funnel.semantic_relevant} соответствуют по смыслу → {report.funnel.quantity_passed} проходят по количеству. Запросов к модели: {report.funnel.model_requests}.</p>}
  <p className="mt-2 text-xs leading-6 text-muted">Всего закупок: {report.counts.total}. Модель рассмотрела: {report.counts.model_analysed}. {run.model?`Модель: ${run.model}.`:'Проверка выполнена кодом без вызова модели.'} {!current && 'Исторический первичный отбор: экономика и производственная готовность не подтверждены.'}</p>
  <p className="mt-2 text-xs leading-6 text-muted wrap-anywhere">Выбранные файлы: {run.sources_snapshot.map(s=>s.filename).join('; ')||'Смотрите доказательства в отчёте.'}</p>
  {(report.stock_kind==='mock'||report.economics_kind==='mock')&&<p className="mt-2 text-xs text-amber-900">Демонстрационные источники: {[report.stock_kind==='mock'?'склад':'',report.economics_kind==='mock'?'экономика':''].filter(Boolean).join(', ')}.</p>}
  <div className="mt-5 flex flex-wrap gap-2" aria-label="Категории отчёта">{keys.map(key=><button key={key} onClick={()=>{setGroup(key);setLimit(30)}} aria-pressed={group===key} className={`rounded-lg border px-4 py-3 text-sm font-bold ${group===key?'border-accent bg-accent text-white':'border-line bg-white text-accent'}`}>{!current&&key==='take_in_work'?'Первичный кандидат':labels[key]} ({count(key)})</button>)}</div>
  {!items.length&&<p className="py-7 text-sm text-muted">В этой категории закупок нет.</p>}
  <ol className="mt-5 space-y-4">{items.slice(0,limit).map(item=><li key={item.id} className="min-w-0 rounded-xl border border-line bg-white p-5 sm:p-6">
   <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0 flex-1"><p className="text-xs font-bold text-muted">№{item.number}{item.demo?' · учебный сценарий':''}</p><h3 className="mt-2 font-display text-lg font-semibold wrap-anywhere">{item.title}</h3></div><p className="text-sm font-bold tabular-nums">{item.price?`${Number(item.price).toLocaleString('ru-RU')} ${item.currency}`:'Цена не указана'}</p></div>
   <p className="mt-3 text-sm font-bold">Подать до: {radarDate(item.deadline_at)}</p><p className="mt-1 text-xs leading-6 text-muted">Стадия проверена: {radarDate(item.source_observed_at)} · <a href={item.source_url} target="_blank" rel="noreferrer" className="font-bold text-accent underline">Карточка источника</a></p>
   <p className="mt-4 text-sm leading-7 whitespace-pre-wrap wrap-anywhere">{item.reason}</p>
   {(item.mock||item.demo) && <p className="mt-2 text-xs font-bold text-amber-900">Демонстрационная оценка</p>}
   {!!item.origins?.length && <p className="mt-2 text-xs text-muted">Исходные строки: {item.origins.map(origin=>`${origin.sheet ? origin.sheet+': ' : ''}${origin.row}`).join('; ')}</p>}
   {item.decision_stage&&<p className="mt-2 text-xs font-bold text-muted">Этап решения: {stageLabels[item.decision_stage]}</p>}
   {item.meaning_reason&&item.meaning_reason!==item.reason&&<p className="mt-2 text-sm leading-6 wrap-anywhere">Смысловая оценка: {item.meaning_reason}</p>}
   {item.fit&&<p className="mt-3 text-sm font-bold">Товарное соответствие: {fitLabels[item.fit]}</p>}
   {item.stock_check&&<section className="mt-4 rounded-lg border border-line p-3"><h4 className="text-sm font-bold">Свободный остаток: {stockLabels[item.stock_check.status]}</h4><p className="mt-1 text-xs text-muted">Допуск: {item.stock_check.allowed_shortage_percent}% по каждому SKU.</p>
    {!!item.stock_check.checks.length&&<div className="mt-3 overflow-x-auto"><table className="w-full min-w-[30rem] text-left text-xs leading-6"><caption className="sr-only">Расчёт свободного остатка</caption><thead><tr className="border-b border-line"><th className="p-2">Деталь / SKU</th><th className="p-2">Нужно</th><th className="p-2">Свободно</th><th className="p-2">Нехватка</th></tr></thead><tbody>{item.stock_check.checks.map(c=><tr key={c.sku} className="border-b border-line last:border-0"><td className="p-2"><strong>{c.sku}</strong><br/>{c.product}<br/><span className="text-muted">Строка ведомости: {c.source_row}</span></td><td className="p-2 tabular-nums">{c.required} {c.unit}</td><td className="p-2 tabular-nums">{c.available} {c.unit}</td><td className={`p-2 font-bold tabular-nums ${c.within_tolerance?'text-accent':'text-red-900'}`}>{c.shortage} {c.unit}<br/>{c.shortage_percent}%</td></tr>)}</tbody></table></div>}
    {!!item.stock_check.reasons.length&&<ul className="mt-2 list-disc pl-4 text-xs leading-6">{item.stock_check.reasons.map((reason,i)=><li key={i}>{reason}</li>)}</ul>}
   </section>}
   {item.economics_check && <section className="mt-4 rounded-lg border border-line p-3" aria-label="Экономика закупки"><h4 className="text-sm font-bold">Экономика: {{pass:'Проходит',fail:'Не проходит',unknown:'Требует проверки'}[item.economics_check.status]}</h4><dl className="economics-summary mt-3 text-xs">{[['Выручка',item.economics_check.revenue],['Расходы',item.economics_check.total_cost],['Прибыль',item.economics_check.profit],['Бюджет',item.economics_check.budget]].map(([label,value])=><div key={label}><dt className="text-muted">{label}</dt><dd>{value===null?'Неизвестно':`${value} ${item.economics_check!.currency || ''}`}</dd></div>)}<div><dt className="text-muted">Маржа</dt><dd>{item.economics_check.margin_percent===null?'Неизвестно':item.economics_check.margin_percent+'%'}</dd></div><div><dt className="text-muted">Дней нужно / доступно</dt><dd>{item.economics_check.required_days??'?'} / {item.economics_check.available_days??'?'}</dd></div></dl>
   {!!item.economics_check.reasons.length && <ul className="mt-2 list-disc pl-4 text-xs leading-6">{item.economics_check.reasons.map((reason,i)=><li key={i}>{reason}</li>)}</ul>}
   {!!item.economics_check.lines.length && <details className="mt-3 text-xs"><summary>Расчёт по позициям</summary><div className="overflow-x-auto"><table className="w-full min-w-[28rem] text-left leading-6"><thead><tr><th>SKU</th><th>Количество</th><th>Наша цена</th><th>Выручка</th><th>Расходы</th><th>Строка</th></tr></thead><tbody>{item.economics_check.lines.map((line,i)=><tr key={i}><td>{line.sku}</td><td>{line.quantity}</td><td>{line.sale_price??'?'}</td><td>{line.revenue??'?'}</td><td>{line.cost??'?'}</td><td>{line.source_row}</td></tr>)}</tbody></table></div></details>}</section>}
   {item.product_group&&<div className="mt-3 rounded-lg bg-paper p-4 text-sm leading-6 wrap-anywhere"><p className="font-bold">Группа: {item.product_group}</p><p className="mt-2 text-xs font-bold text-muted">Из тендера</p><blockquote className="whitespace-pre-wrap">{item.tender_quote}</blockquote><p className="mt-2 text-xs font-bold text-muted">Из профиля</p><blockquote className="whitespace-pre-wrap">{item.profile_quote}</blockquote></div>}
   {item.provenance&&<details className="mt-3 text-xs leading-6 text-muted"><summary className="cursor-pointer font-bold">Происхождение сведений о закупке</summary><p className="mt-2 whitespace-pre-wrap wrap-anywhere">{sourceNotes(item.provenance)}</p></details>}
   {item.risks.length>0&&<div className="mt-4 rounded-lg bg-amber-50 p-4"><h4 className="text-xs font-bold text-amber-950">Что проверить</h4><ul className="mt-2 list-disc space-y-1 pl-4 text-sm leading-6 text-amber-950">{item.risks.map((text,i)=><li key={i}>{text}</li>)}</ul></div>}
   {!!item.next_steps.length&&<div className="mt-4"><h4 className="text-xs font-bold text-muted">Следующие действия</h4><ul className="mt-2 list-disc space-y-1 pl-4 text-sm leading-6">{item.next_steps.map((text,i)=><li key={i}>{text}</li>)}</ul></div>}
   {!!item.evidence_indexes.length&&<details className="mt-4 border-t border-line pt-3"><summary className="cursor-pointer text-sm font-bold text-accent">Доказательства из выбранных файлов</summary><ul className="mt-3 space-y-3">{item.evidence_indexes.map(i=>{const s=report.sources[i];return s?<li key={i} className="rounded-lg bg-paper p-3 text-xs leading-6 wrap-anywhere"><p className="font-bold">{s.filename} · {s.unit||'row'} {s.row_start}–{s.row_end}</p>{s.truncated&&<p className="font-bold text-amber-900">Выбранные фрагменты; контекст частичный.</p>}<pre className="mt-2 whitespace-pre-wrap wrap-anywhere">{s.excerpt}</pre><button className="mt-2 font-bold text-accent underline" onClick={()=>{setDownloadError(null);void downloadOriginal(s,s.filename).catch(reason=>setDownloadError(reason instanceof Error&&reason.message==='Файл удалён.'?reason.message:'Не удалось скачать исходный файл.'))}}>Скачать исходный файл</button></li>:null})}</ul></details>}
  </li>)}</ol>
  {limit<items.length&&<button onClick={()=>setLimit(limit+30)} className="mt-4 rounded-lg border border-line bg-white px-4 py-3 text-sm font-bold text-accent">Показать ещё закупки</button>}
  {downloadError&&<p role="alert" className="mt-3 text-red-800">{downloadError}</p>}
 </section>
}
