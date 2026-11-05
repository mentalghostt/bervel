import { useEffect, useRef, useState } from 'react'
import { ChatRequestError, decideProposal, readProposal } from './chatApi'
import type { CodeProposal, InspectionTestResult } from './chatApi'
import './InspectionResults.css'

export function InspectionTests({result}:{result:InspectionTestResult}) {
  return <section className={`assistant-result inspection-tests ${result.state}`} aria-label="Результат тестов">
    <p className="eyebrow">ТЕСТЫ · {result.suite}</p><h3>{result.state==='passed'?'Тесты пройдены':'Тесты завершились с ошибкой'}</h3>
    <p className="mt-2 text-sm whitespace-pre-wrap wrap-anywhere">{result.summary}</p><p className="mt-2 text-xs text-muted">Код завершения: {result.exit_code}</p>
    {result.output&&<details className="mt-3"><summary>Вывод тестов</summary><pre className="inspection-log">{result.output}</pre></details>}
  </section>
}
type Attempt={action:'confirm'|'reject';key:string;fingerprint:string}
function savedAttempt(storageKey:string):Attempt|null {
  try { const value=JSON.parse(localStorage.getItem(storageKey)||'null');return value&&['confirm','reject'].includes(value.action)&&typeof value.key==='string'&&typeof value.fingerprint==='string'?value:null } catch {return null}
}
const statusLabels:Record<CodeProposal['status'],string>={pending:'Ожидает подтверждения',applied:'Патч применён',rejected:'Патч отклонён',failed:'Применение завершилось с ошибкой',unknown:'Исход применения неизвестен'}
export function InspectionProposal({result,readOnly=false}:{result:CodeProposal;readOnly?:boolean}) {
  if(!readOnly)return <InteractiveInspectionProposal result={result}/>
  return <section className="assistant-result inspection-proposal" aria-label="Архив предложения изменения кода"><p className="eyebrow">ИСТОРИЯ ИЗМЕНЕНИЯ КОДА</p><h3>{result.title}</h3><p className={`inspection-proposal-status ${result.status}`}>{result.status==='pending'?'Сохранённое предложение':statusLabels[result.status]}</p><p className="text-sm whitespace-pre-wrap wrap-anywhere">{result.explanation}</p><ul className="inspection-patch-files">{result.files.map(path=><li key={path}><code>{path}</code></li>)}</ul><details className="inspection-patch-review"><summary>Просмотреть точный патч</summary><pre aria-label="Точный diff">{result.diff}</pre></details><p className="mt-3 text-xs text-muted">Архив предложения из прежнего режима. Доступен только просмотр; Codex CLI изменяет файлы напрямую.</p></section>
}
function InteractiveInspectionProposal({result}:{result:CodeProposal}) {
  const storageKey=`bervel.inspection.proposal.${result.proposal_id}`
  const [proposal,setProposal]=useState(result),[checked,setChecked]=useState(false),[busy,setBusy]=useState(false)
  const [error,setError]=useState<string|null>(null),[retry,setRetry]=useState(0),[stale,setStale]=useState(false)
  const [reviewed,setReviewed]=useState(false),[reviewOpen,setReviewOpen]=useState(false)
  const [attempt,setAttempt]=useState<Attempt|null>(()=>savedAttempt(storageKey)),inFlight=useRef(false)
  useEffect(()=>{
    let active=true
    readProposal(result.proposal_id).then(value=>{
      if(!active)return
      if(value.session_id!==result.session_id || value.fingerprint!==result.fingerprint || value.diff!==result.diff){setStale(true);setError('Содержимое предложения изменилось. Создайте новое предложение и проверьте его заново.');return}
      setProposal(value);setChecked(true);setError(null)
      if(value.status!=='pending'){localStorage.removeItem(storageKey);setAttempt(null)}
    }).catch(()=>{if(active){setChecked(false);setError('Не удалось проверить текущее состояние патча. Обновите состояние перед подтверждением.')}})
    return()=>{active=false}
  },[result.proposal_id,result.session_id,result.fingerprint,result.diff,storageKey,retry])
  async function decide(action:Attempt['action']) {
    if(inFlight.current||!checked||stale||proposal.status!=='pending'||(action==='confirm'&&!attempt&&(!reviewed||!reviewOpen))||(attempt&&attempt.action!==action))return
    const command=attempt||{action,key:`proposal:${crypto.randomUUID()}`,fingerprint:proposal.fingerprint}
    if(command.fingerprint!==proposal.fingerprint){setStale(true);setError('Сохранённая команда относится к другому патчу. Создайте новое предложение.');return}
    inFlight.current=true;setBusy(true);setAttempt(command);localStorage.setItem(storageKey,JSON.stringify(command));setError(null)
    try {
      const value=await decideProposal(proposal.proposal_id,action,command.key,command.fingerprint)
      if(value.session_id!==proposal.session_id||value.diff!==proposal.diff)throw new Error('Несовместимый ответ подтверждения.')
      setProposal(value);localStorage.removeItem(storageKey);setAttempt(null)
    } catch(reason) {
      if(reason instanceof ChatRequestError&&reason.status>=400&&reason.status<500){
        setAttempt(null);localStorage.removeItem(storageKey);setChecked(false)
        if(reason.status===409){setStale(true);setError('Патч не подтверждён: исходные файлы или состояние предложения изменились. Создайте новое предложение и проверьте новый diff.')}
        else setError(reason.message)
      } else setError('Исход команды неизвестен. Проверьте состояние или повторите ту же команду с сохранённым ключом. Не создавайте повторный патч до проверки.')
    } finally {inFlight.current=false;setBusy(false)}
  }
  return <section className="assistant-result inspection-proposal" aria-label="Предложение изменения кода">
    <p className="eyebrow">ИЗМЕНЕНИЕ КОДА</p><h3>{proposal.title}</h3><p role="status" className={`inspection-proposal-status ${proposal.status}`}>{statusLabels[proposal.status]}</p>
    <p className="text-sm whitespace-pre-wrap wrap-anywhere">{proposal.explanation}</p>
    <p className="mt-3 text-xs font-bold">Файлы ({proposal.files.length})</p><ul className="inspection-patch-files">{proposal.files.map(path=><li key={path}><code>{path}</code></li>)}</ul>
    <details className="inspection-patch-review" onToggle={event=>{setReviewOpen(event.currentTarget.open);if(!event.currentTarget.open)setReviewed(false)}}><summary>Просмотреть точный патч</summary><pre aria-label="Точный diff">{proposal.diff}</pre><p className="inspection-fingerprint">Отпечаток: <code>{proposal.fingerprint}</code></p>
      {proposal.status==='pending'&&!stale&&<label className="inspection-patch-consent"><input type="checkbox" checked={reviewed} disabled={busy||!!attempt} onChange={event=>setReviewed(event.currentTarget.checked)}/> <span>Я проверил этот diff и подтверждаю изменение файлов.</span></label>}
    </details>
    {proposal.status==='pending'&&!stale&&<div className="inspection-proposal-actions"><button type="button" className="secondary" disabled={busy||!checked||(!attempt&&(!reviewOpen||!reviewed))||attempt?.action==='reject'} onClick={()=>void decide('confirm')}>{busy&&attempt?.action==='confirm'?'Применяем…':attempt?.action==='confirm'?'Повторить подтверждение':'Применить патч'}</button><button type="button" className="secondary" disabled={busy||!checked||attempt?.action==='confirm'} onClick={()=>void decide('reject')}>{busy&&attempt?.action==='reject'?'Отклоняем…':attempt?.action==='reject'?'Повторить отклонение':'Отклонить патч'}</button></div>}
    {proposal.status==='applied'&&<p className="mt-3 text-sm">Изменения записаны в проект. Развёртывание и проверка работающего приложения — отдельные действия. Можно отправить сообщение «Проверь результат изменения».</p>}
    {error&&<p role="alert" className="error-note">{error}</p>}
    {(error||proposal.status==='unknown')&&!stale&&<button type="button" className="secondary mt-3" disabled={busy} onClick={()=>setRetry(value=>value+1)}>Обновить состояние патча</button>}
  </section>
}
