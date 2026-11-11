import { useEffect,useRef,useState } from 'react'
import { listTickets,readTicket,saveTicket,TicketRequestError } from './ticketsApi'
import type { Ticket,TicketAttempt } from './ticketsApi'
const empty={title:'',problem:'',expected:'',actual:'',steps:'',context:'',links:''}
function ticketDraft(ticket:Ticket):typeof empty {
 return {title:ticket.title,problem:ticket.problem,expected:ticket.expected,actual:ticket.actual,
  steps:ticket.steps.join('\n'),context:ticket.context,links:ticket.links.join('\n')}
}
const labels:{key:keyof typeof empty;label:string;required?:boolean;rows?:number}[]=[{key:'title',label:'Заголовок',required:true},{key:'problem',label:'Проблема',required:true,rows:3},{key:'expected',label:'Ожидаемое поведение',rows:2},{key:'actual',label:'Фактическое поведение',rows:2},{key:'steps',label:'Шаги воспроизведения',rows:3},{key:'context',label:'Контекст и диагностика',rows:3},{key:'links',label:'Ссылки',rows:2}]
export default function TicketsPage(){
 const [items,setItems]=useState<Ticket[]>([]),[cursor,setCursor]=useState<number|null>(null),[selected,setSelected]=useState<Ticket|null>(null)
 const [draft,setDraft]=useState(empty),[editing,setEditing]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState<string|null>(null),[notice,setNotice]=useState('')
 const [retry,setRetry]=useState(0),[attempt,setAttempt]=useState<TicketAttempt|null>(null)
 const inFlight=useRef(false),selection=useRef(0)
 const lifetime=useRef({active:true})
 useEffect(()=>{const life=lifetime.current;life.active=true;return()=>{life.active=false}},[])
 useEffect(()=>{let active=true;listTickets().then(value=>{if(active){setItems(value.tickets);setCursor(value.next_cursor)}}).catch(reason=>{if(active)setError(reason.message)});return()=>{active=false}},[retry])
 async function open(id:string){
  const token=++selection.current;setBusy(true);setError(null);setNotice('');setEditing(false);setAttempt(null)
  try{const value=await readTicket(id);if(!lifetime.current.active||token!==selection.current)return;setSelected(value);setDraft(ticketDraft(value))}
  catch(reason){if(token===selection.current)setError(reason instanceof Error?reason.message:'Не удалось открыть обращение.')}
  finally{if(token===selection.current)setBusy(false)}
 }
 useEffect(()=>{let active=true;const query=new URLSearchParams(window.location.hash.split('?')[1]||'');const id=query.get('ticket')||query.get('ticket_id');if(id)void Promise.resolve().then(()=>{if(active)void open(id)});return()=>{active=false}},[])
 async function save(){
  if(inFlight.current)return
  const steps=draft.steps.split('\n').map(s=>s.trim()).filter(Boolean)
  const links=draft.links.split('\n').map(s=>s.trim()).filter(Boolean)
  if(!attempt){
   if(steps.length>50||steps.some(step=>step.length>4000)){setError('Допустимо до 50 шагов, каждый до 4000 символов.');return}
   if(links.length>30||links.some(link=>{
    if(link.length>2048)return true
    try{const url=new URL(link);return !['http:','https:'].includes(url.protocol)||!url.hostname||!!url.username||!!url.password}catch{return true}
   })){setError('Допустимо до 30 ссылок HTTP/HTTPS, каждая до 2048 символов, без имени пользователя и пароля.');return}
  }
  const current:TicketAttempt=attempt||{
   title:draft.title,problem:draft.problem,expected:draft.expected,actual:draft.actual,context:draft.context,
   steps,links,
   command_key:crypto.randomUUID(),...(selected?{expected_revision:selected.revision}:{})}
  inFlight.current=true;setBusy(true);setError(null);setAttempt(current)
  try{
   const value=await saveTicket(selected?.ticket_id||null,current)
   setSelected(value);setDraft(ticketDraft(value));setItems(old=>[value,...old.filter(item=>item.ticket_id!==value.ticket_id)]);setEditing(false);setAttempt(null);setNotice('Обращение сохранено.')
  }catch(reason){if(reason instanceof TicketRequestError&&reason.rejected)setAttempt(null);setError(reason instanceof Error?reason.message:'Не удалось сохранить обращение.')}
  finally{inFlight.current=false;setBusy(false)}
 }
 return <main className="tickets-page" aria-label="Обращения"><div className="ui-toolbar"><button className="primary" disabled={busy} onClick={()=>{selection.current++;setSelected(null);setDraft(empty);setEditing(true);setError(null);setAttempt(null);setNotice('')}}>Создать обращение</button></div>
 {error&&<div role="alert" className="error-note">{error}<button className="ml-3 underline" onClick={()=>setRetry(v=>v+1)}>Обновить список</button></div>}
 {notice&&<p role="status" className="file-state">{notice}</p>}
 <div className="ticket-columns workspace-columns"><section className="panel ui-list-panel" aria-label="Список обращений"><div className="chat-sessions"><ul>{items.map(item=><li key={item.ticket_id} className={`chat-session ui-choice${selected?.ticket_id===item.ticket_id?' selected':''}`}><button type="button" className="chat-select ui-choice-select" aria-label={item.title} aria-current={selected?.ticket_id===item.ticket_id?'true':undefined} disabled={busy&&editing} onClick={()=>void open(item.ticket_id)}><span className="chat-title" title={item.title}>{item.title}</span></button><div className="chat-session-footer ui-choice-footer"><time dateTime={item.updated_at}>{item.status==='sent'?'Отправлено':'Создано'} · {new Date(item.updated_at).toLocaleDateString('ru-RU')}</time></div></li>)}</ul>{!items.length&&<p className="chat-list-note">Обращений пока нет.</p>}{cursor!==null&&<button className="secondary chat-more" disabled={busy} onClick={()=>{setBusy(true);void listTickets(cursor).then(value=>{setItems(old=>[...old,...value.tickets.filter(item=>!old.some(o=>o.ticket_id===item.ticket_id))]);setCursor(value.next_cursor)}).catch(reason=>setError(reason.message)).finally(()=>setBusy(false))}}>Показать ещё обращения</button>}</div></section>
 <section className="panel" aria-label="Карточка обращения">
 {busy&&!editing&&<p role="status" className="text-sm text-muted">Получаем обращение…</p>}
 {editing?<form onSubmit={event=>{event.preventDefault();void save()}}><p className="eyebrow">{selected?'РЕДАКТИРОВАНИЕ':'НОВОЕ ОБРАЩЕНИЕ'}</p><h2>{selected?'Изменить обращение':'Опишите проблему'}</h2><fieldset disabled={busy||!!attempt} className="space-y-4 mt-4">{labels.map(({key,label,required,rows})=><label className="block text-sm" key={key}>{label}{rows?<textarea aria-label={label} rows={rows} value={draft[key]} required={required} maxLength={key==='steps'||key==='links'?undefined:8000} onChange={event=>setDraft(old=>({...old,[key]:event.target.value}))}/>:<input aria-label={label} value={draft[key]} required={required} maxLength={200} onChange={event=>setDraft(old=>({...old,[key]:event.target.value}))}/>}</label>)}</fieldset><p className="mt-3 text-xs text-muted">Шаги и ссылки: по одному на строку. До 50 шагов по 4000 символов и до 30 ссылок по 2048 символов. В диагностику не добавляйте ключи и пароли.</p><div className="mt-4 flex flex-wrap gap-2"><button className="primary" disabled={busy}>{busy?'Сохраняем…':attempt?'Повторить сохранение':'Сохранить обращение'}</button><button type="button" className="secondary" disabled={busy} onClick={()=>{setEditing(false);setAttempt(null)}}>Отмена</button></div>{attempt&&!busy&&<p className="mt-2 text-xs text-muted">Сохранение не подтверждено. Повтор использует ту же команду.</p>}</form>
 :selected?<><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="file-state">{selected.status==='sent'?'Отправлено':'Создано'}</p><h2 className="mt-2 wrap-anywhere">{selected.title}</h2></div><button className="secondary" disabled={busy||selected.status!=='created'} onClick={()=>setEditing(true)}>Редактировать обращение</button></div><dl className="mt-5 space-y-5">{labels.filter(({key})=>key!=='title').map(({key,label})=><div key={key}><dt className="text-xs text-muted mb-2">{label}</dt><dd className="text-sm whitespace-pre-wrap wrap-anywhere">{key==='links'?selected.links.map((link,i)=><p key={i}>{/^https?:\/\//i.test(link)?<a className="text-accent underline" href={link} target="_blank" rel="noreferrer">{link}</a>:link}</p>):key==='steps'?selected.steps.map((step,i)=><p key={i}>{i+1}. {step}</p>):selected[key]||'Не указано'}</dd></div>)}</dl><p className="mt-6 text-xs text-muted">Редакция {selected.revision} · Создано {new Date(selected.created_at).toLocaleString('ru-RU')}</p></>
 :!busy&&<div className="ticket-empty"><h2>Выберите обращение</h2><p className="text-muted text-sm">Или создайте новое. Ассистент также может оформить его из диалога.</p></div>}
 </section></div></main>
}

