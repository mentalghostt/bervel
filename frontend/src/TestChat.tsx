import { useEffect, useMemo, useRef, useState } from 'react'
import { ChatRequestError, chatApi } from './chatApi'
import type { ChatMode, ChatSession, ChatSource, ChatSummary, ChatTurn, ChatWorkflowPhase } from './chatApi'
import VoiceInput from './VoiceInput'
import AssistantResults from './AssistantResults'

const buttonClass = 'rounded-lg border border-line bg-white px-4 py-2.5 text-sm font-bold text-accent hover:bg-accent-soft focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50'
const pending = (turn: ChatTurn) => turn.state === 'queued' || turn.state === 'running'
const ordered = (items: ChatSummary[]) => items.sort((a,b) => b.created_at.localeCompare(a.created_at) || b.session_id.localeCompare(a.session_id))
function summary(session: ChatSession): ChatSummary {
  return { session_id: session.session_id, created_at: session.created_at, title: session.turns[0]?.question.trim().slice(0,120) || 'Новый диалог', turn_count: session.turns.length, has_pending: session.turns.some(pending) }
}

function answerSources(turn: ChatTurn) {
  const sources = new Map<string, Pick<ChatSource, 'artifact_id' | 'filename'>>()
  function add(value: unknown) {
    if (!value || typeof value !== 'object') return
    const ref = value as Record<string, unknown>
    if (typeof ref.artifact_id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(ref.artifact_id) || typeof ref.filename !== 'string') return
    sources.set(ref.artifact_id, { artifact_id: ref.artifact_id, filename: ref.filename })
  }
  turn.sources.forEach(add)
  for (const result of turn.results) {
    if (result.kind !== 'data' || !result.data || typeof result.data !== 'object') continue
    const data = result.data as Record<string, unknown>
    if (result.operation === 'file.read') add(data.file)
    if (result.operation === 'file.list' && Array.isArray(data.artifacts)) data.artifacts.forEach(add)
  }
  return [...sources.values()]
}
function Sources({ turn }: { turn: ChatTurn }) {
  const sources = answerSources(turn)
  if (!sources.length) return null
  return <div className="mt-5 border-t border-line pt-4"><h3 className="mb-2 text-xs font-bold text-muted">Источники ответа</h3>
    <ul className="source-links">{sources.map(source => <li key={source.artifact_id}><a href={`#/files?artifact_id=${encodeURIComponent(source.artifact_id)}`} className="text-accent underline wrap-anywhere">{source.filename}</a></li>)}</ul>
  </div>
}

const workflowLabels: Record<ChatWorkflowPhase, string> = {
  research: 'Исследуем источники', synthesizing: 'Собираем ответ', generating: 'Создаём файл',
  verifying: 'Проверяем результат', completed: 'Завершено', partial: 'Частичный результат', blocked: 'Выполнение заблокировано',
}
function WorkflowProgress({ turn }: { turn: ChatTurn }) {
  const value = turn.workflow
  if (!value) {
    if (pending(turn)) return <p role="status" className="mt-2 text-sm leading-7 text-muted">{turn.state === 'queued' ? 'В очереди…' : 'Готовим ответ…'}</p>
    if (turn.state !== 'partial' && turn.state !== 'blocked') return null
    return <section aria-label="Этапы выполнения" className="mt-3 text-sm leading-6"><p role="status" className="font-bold">{workflowLabels[turn.state]}</p>{turn.error && <p className="mt-2 text-muted">{turn.error}</p>}</section>
  }
  const history = value.history.filter((entry, index, entries) => entries.findIndex(item => item.phase === entry.phase) === index)
  return <section aria-label="Этапы выполнения" className="mt-3 text-sm leading-6">
    <p role="status" className="font-bold">{turn.state === 'failed' ? 'Выполнение прервано' : turn.state === 'queued' ? 'В очереди…' : workflowLabels[value.phase]}</p>
    {value.reason && <p className="mt-2 text-muted whitespace-pre-wrap wrap-anywhere">{value.reason}</p>}
    {!!history.length && <ol aria-label="Пройденные этапы" className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">{history.map(entry => <li key={entry.phase} aria-current={turn.state !== 'failed' && entry.phase === value.phase ? 'step' : undefined} className={turn.state !== 'failed' && entry.phase === value.phase ? 'font-bold text-accent' : undefined}>
      {workflowLabels[entry.phase]}
    </li>)}</ol>}
  </section>
}

export default function TestChat({ active, mode = 'assistant' }: { active: boolean; mode?: ChatMode }) {
  const sessionStorageKey = mode === 'inspection' ? 'bervel.inspection.session_id' : 'bervel.chat.session_id'
  const commandStorageKey = mode === 'inspection' ? 'bervel.inspection.command_key' : 'bervel.chat.command_key'
  const {createChat, readChat, sendChat, listChats, deleteChat} = useMemo(() => chatApi(mode), [mode])
  const messageId = mode === 'inspection' ? 'inspection-message' : 'chat-message'
  const hintId = mode === 'inspection' ? 'inspection-hint' : 'chat-hint'
  const [sessionId, setSessionId] = useState(() => localStorage.getItem(sessionStorageKey))
  const [session, setSession] = useState<ChatSession | null>(null)
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(!!sessionId)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [readError, setReadError] = useState(false)
  const [retry, setRetry] = useState(0)
  const [command, setCommand] = useState(() => localStorage.getItem(commandStorageKey))
  const inFlight = useRef(false)
  const [messageAttempt, setMessageAttempt] = useState<{ key: string; content: string } | null>(null)
  const [chats, setChats] = useState<ChatSummary[]>([])
  const [listLoading, setListLoading] = useState(false), [listError, setListError] = useState<string|null>(null)
  const [listRetry, setListRetry] = useState(0), [nextOffset, setNextOffset] = useState<number|null>(null)
  const [deleting, setDeleting] = useState<string|null>(null), [voiceBusy, setVoiceBusy] = useState(false)
  const deleteAttempts = useRef(new Map<string,string>()), deleted = useRef(new Set<string>())
  const drafts = useRef(new Map<string,string>()), selected = useRef(sessionId), readGeneration = useRef(0)
  const transcript = useRef<HTMLDivElement>(null), followBottom = useRef(true)
  const deleteInFlight = useRef(false)
  const summaryVersion = useRef(0), touchedSummaries = useRef(new Map<string,number>())
  const listInFlight = useRef(false), listGeneration = useRef(0)
  const hasPending = !!session?.turns.some(pending)
  const busy = loading || sending || hasPending
  const deletionUnconfirmed = !!sessionId && deleteAttempts.current.has(sessionId)

  function touchSummary(id: string) { touchedSummaries.current.set(id,++summaryVersion.current) }
  function mergeChats(incoming: ChatSummary[], previous: ChatSummary[], started: number) {
    const merged = new Map(previous.map(item=>[item.session_id,item]))
    for(const item of incoming) if((touchedSummaries.current.get(item.session_id)||0)<=started) merged.set(item.session_id,item)
    return ordered([...merged.values()].filter(item=>!deleted.current.has(item.session_id)))
  }

  function rememberCommand(value: string | null) {
    if (value) localStorage.setItem(commandStorageKey, value)
    else localStorage.removeItem(commandStorageKey)
    setCommand(value)
  }

  function unavailableChat(id: string|null, draft: string) {
    if(id) {deleted.current.add(id);drafts.current.delete(id);setChats(previous=>previous.filter(item=>item.session_id!==id))}
    selected.current=null;readGeneration.current++;followBottom.current=true
    localStorage.removeItem(sessionStorageKey);rememberCommand(null);setMessageAttempt(null)
    setSessionId(null);setSession(null);setLoading(false);setReadError(false);setText(draft)
    drafts.current.set('new',draft)
    setError('Диалог удалён или недоступен. Проверьте сохранённые результаты перед новым запросом.')
  }

  useEffect(() => {
    const generation = ++listGeneration.current
    if (!active || sending || deleting) { listInFlight.current=false; return }
    let current = true
    listInFlight.current=true
    async function load() {
      await Promise.resolve()
      if (!current) return
      setListLoading(true)
      const started = summaryVersion.current
      try {
        const result = await listChats()
        if (!current || generation !== listGeneration.current) return
        setChats(previous => mergeChats(result.sessions,previous,started))
        setNextOffset(result.next_offset)
      } catch (reason) { if(current) setListError(reason instanceof Error ? reason.message : 'Не удалось загрузить чаты.') }
      finally { if(current && generation===listGeneration.current) {listInFlight.current=false;setListLoading(false)} }
    }
    void load()
    return () => { current = false;listGeneration.current++;listInFlight.current=false }
  }, [active,listRetry,sending,deleting])

  async function moreChats() {
    if(listInFlight.current || inFlight.current || deleteInFlight.current || nextOffset === null) return
    listInFlight.current=true
    const generation = listGeneration.current
    setListLoading(true); setListError(null)
    const started = summaryVersion.current
    try {
      const result = await listChats(nextOffset)
      if(generation!==listGeneration.current) return
      setChats(previous=>mergeChats(result.sessions,previous,started))
      setNextOffset(result.next_offset)
    } catch(reason) { if(generation===listGeneration.current)setListError(reason instanceof Error ? reason.message : 'Не удалось загрузить чаты.') }
    finally { if(generation===listGeneration.current) {listInFlight.current=false;setListLoading(false)} }
  }

  useEffect(() => {
    if (!active || !sessionId || sending) return
    let current = true
    const generation = readGeneration.current
    let timer: ReturnType<typeof setTimeout> | undefined
    async function refresh(first = false) {
      if (first) {
        await Promise.resolve()
        if (!current) return
        setLoading(true)
      }
      try {
        const result = await readChat(sessionId!)
        if (!current || selected.current !== sessionId || generation !== readGeneration.current) return
        setSession(result); setReadError(false)
        touchSummary(result.session_id)
        setChats(previous=>ordered([summary(result),...previous.filter(item=>item.session_id!==result.session_id)]))
        if (result.turns.some(pending)) timer = setTimeout(() => { void refresh() }, 1000)
      } catch (reason) {
        if (!current || selected.current !== sessionId || generation !== readGeneration.current) return
        if(reason instanceof ChatRequestError && reason.status===404) {unavailableChat(sessionId,text);return}
        setReadError(true)
        setError(reason instanceof Error ? reason.message : 'Не удалось прочитать диалог.')
      } finally { if (current && selected.current === sessionId && generation === readGeneration.current) setLoading(false) }
    }
    void refresh(true)
    return () => { current = false; clearTimeout(timer) }
  }, [active, sessionId, sending, retry])

  useEffect(() => {
    if(active && followBottom.current && transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight
  }, [active,session])

  async function submit() {
    const content = messageAttempt?.content ?? text.trim()
    if (inFlight.current || deleteInFlight.current || (!sessionId&&listInFlight.current) || deletionUnconfirmed || busy || voiceBusy || readError || !content || content.length > 4000) return
    inFlight.current = true
    setSending(true); setError(null)
    let id = sessionId
    try {
      if (!id) {
        const key = command?.startsWith('session:') ? command : `session:${crypto.randomUUID()}`
        rememberCommand(key)
        const created = await createChat(key)
        localStorage.setItem(sessionStorageKey, created.session_id)
        id = created.session_id
        selected.current = id; readGeneration.current++
        setSessionId(id); setSession(created)
        touchSummary(id)
        setChats(previous=>ordered([summary(created),...previous.filter(item=>item.session_id!==id)]))
        setNextOffset(previous=>previous===null?null:previous+1)
        drafts.current.delete('new')
        rememberCommand(null)
      }
      const key = messageAttempt?.key ?? (command?.startsWith('message:') ? command : `message:${crypto.randomUUID()}`)
      setMessageAttempt({ key, content })
      rememberCommand(key)
      const result = await sendChat(id, key, content)
      setSession(previous => {
        if(!previous || previous.session_id !== id) return previous
        return { ...previous, turns: [...previous.turns.filter(item => item.turn_id !== result.turn_id), result] }
      })
      touchSummary(id)
      setChats(previous=>previous.map(item=>item.session_id===id?{...item,title:item.turn_count?item.title:content.slice(0,120),turn_count:item.turn_count+(session?.turns.some(turn=>turn.turn_id===result.turn_id)?0:1),has_pending:pending(result)}:item))
      drafts.current.delete(id)
      followBottom.current = true
      setMessageAttempt(null)
      rememberCommand(null); setText('')
    } catch (reason) {
      if(reason instanceof ChatRequestError && reason.status===404) unavailableChat(id,content)
      else setError(reason instanceof Error ? reason.message : 'Не удалось отправить сообщение.')
    } finally { inFlight.current = false; setSending(false) }
  }

  function newChat() {
    if (inFlight.current || deleteInFlight.current || busy || voiceBusy || command) return
    drafts.current.set(sessionId || 'new',text)
    selected.current = null; readGeneration.current++; followBottom.current = true
    localStorage.removeItem(sessionStorageKey)
    setSessionId(null); setSession(null); setText(drafts.current.get('new') || ''); setError(null); setReadError(false); setLoading(false)
    setMessageAttempt(null)
  }

  function chooseChat(id: string) {
    if(id===sessionId || inFlight.current || deleteInFlight.current || voiceBusy || command) return
    drafts.current.set(sessionId || 'new',text)
    selected.current = id; readGeneration.current++; followBottom.current = true
    localStorage.setItem(sessionStorageKey,id)
    setSessionId(id);setSession(null);setText(drafts.current.get(id)||'');setError(null);setReadError(false);setLoading(true)
  }

  function forgetChat(id: string) {
    deleted.current.add(id);deleteAttempts.current.delete(id);drafts.current.delete(id)
    setChats(previous=>previous.filter(item=>item.session_id!==id));setNextOffset(previous=>previous===null?null:Math.max(0,previous-1))
    if(selected.current===id) {
      selected.current=null;readGeneration.current++;followBottom.current=true
      localStorage.removeItem(sessionStorageKey)
      setSessionId(null);setSession(null);setText(drafts.current.get('new')||'');setLoading(false);setError(null);setReadError(false)
    }
  }

  async function removeChat(id: string) {
    if(inFlight.current || listInFlight.current || deleteInFlight.current || voiceBusy || command) return
    deleteInFlight.current = true;setDeleting(id);setListError(null)
    const key=deleteAttempts.current.get(id)||`delete:${crypto.randomUUID()}`
    deleteAttempts.current.set(id,key)
    try {
      await deleteChat(id,key)
      forgetChat(id)
    } catch(reason) {
      if(reason instanceof ChatRequestError && reason.status>=400 && reason.status<500) {
        deleteAttempts.current.delete(id)
        if(reason.status===404) {forgetChat(id);setListError('Диалог уже удалён или недоступен.');return}
      }
      setListError(reason instanceof ChatRequestError && reason.status===409 ? 'Чат занят обработкой. Дождитесь завершения и повторите удаление.' : reason instanceof Error ? reason.message : 'Не удалось удалить чат.')
    }
    finally { deleteInFlight.current=false;setDeleting(null) }
  }

  return <main hidden={!active} className="chat-page" aria-label={mode === 'inspection' ? 'Чаты проверки' : 'Чаты ассистента'}>
    <aside className="chat-history panel" aria-label="Чаты">
      <button type="button" disabled={busy || voiceBusy || !!command || !!deleting} onClick={newChat} className="secondary new-chat">Новый диалог</button>
      <div className="chat-sessions">
        <ol aria-label="Список чатов">{chats.map(item=><li key={item.session_id} className={`chat-session${sessionId===item.session_id?' selected':''}`}>
          <button type="button" className="chat-select" aria-label={item.title} aria-current={sessionId===item.session_id?'true':undefined} disabled={sending||voiceBusy||!!command||!!deleting} onClick={()=>chooseChat(item.session_id)}>
            <span className="chat-title">{item.title}</span><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})}</time>
          </button>
          <div className="chat-session-footer">{item.has_pending&&<span className="chat-pending">Готовим ответ…</span>}<button type="button" disabled={listLoading||sending||voiceBusy||!!command||!!deleting||item.has_pending||(item.session_id===sessionId&&hasPending)} onClick={()=>void removeChat(item.session_id)}>{deleting===item.session_id?'Удаляем…':'Удалить'}</button></div>
        </li>)}</ol>
        {listLoading&&<p role="status" className="chat-list-note">Загружаем чаты…</p>}
        {listError&&<div className="chat-list-note"><p role="alert" className="text-red-800">{listError}</p><button type="button" className="secondary" disabled={listLoading||!!deleting} onClick={()=>{setListError(null);setListRetry(value=>value+1)}}>Обновить список</button></div>}
        {nextOffset!==null&&<button type="button" className="secondary chat-more" disabled={listLoading||sending||!!deleting} onClick={()=>void moreChats()}>Ещё чаты</button>}
        {!listLoading&&!listError&&!chats.length&&<p className="chat-list-note">Чатов пока нет</p>}
      </div>
    </aside>
    <section aria-label="Диалог" className="chat-workspace rounded-2xl border border-line bg-white">
      <div className="chat-transcript" ref={transcript} onScroll={event=>{const node=event.currentTarget;followBottom.current=node.scrollHeight-node.scrollTop-node.clientHeight<80}}>
      {loading && <p role="status" className="text-sm text-muted">Восстанавливаем диалог…</p>}
      {!loading && !session?.turns.length && <div className="py-8">
        <h2 className="font-display text-xl font-semibold">{mode === 'inspection' ? 'Что проверить?' : 'Начните с вопроса'}</h2>
        {mode === 'inspection' && <p className="mt-3 text-sm leading-6 text-muted">Исследуйте код, данные и работу BERVEL. Здесь можно читать систему и создавать обращения. Значения секретов скрыты.</p>}
      </div>}
      <ol className="space-y-7" aria-label="Сообщения">
        {session?.turns.map(item => <li key={item.turn_id} className="min-w-0 space-y-4">
          <article aria-label="Сообщение пользователя" className="ml-auto max-w-3xl rounded-xl border border-accent/15 bg-accent-soft px-4 py-3 sm:px-5">
            <h2 className="text-xs font-bold text-accent">Вы</h2>
            <p className="mt-2 text-sm leading-7 whitespace-pre-wrap wrap-anywhere">{item.question}</p>
          </article>
          <article aria-label="Ответ Codex" className="max-w-3xl rounded-xl border border-line px-4 py-4 sm:px-5">
            <h2 className="text-xs font-bold text-accent">Codex</h2>
            <WorkflowProgress turn={item}/>
            {item.answer !== null && <p className="mt-2 text-sm leading-7 whitespace-pre-wrap wrap-anywhere">{item.answer}</p>}
            {item.state === 'failed' && <p role="alert" className="mt-3 text-sm leading-6 text-red-800">{item.error ?? 'Не удалось получить ответ.'}</p>}
            <AssistantResults results={item.results} actions={item.actions}/>
            <Sources turn={item} />
          </article>
        </li>)}
      </ol>
      {error && <p role="alert" className="mt-5 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm leading-6 text-red-800">{error}</p>}
      {readError && <button type="button" className={`${buttonClass} mt-3`} disabled={loading} aria-busy={loading} onClick={() => { setError(null); setRetry(value => value + 1) }}>{loading?'Читаем историю…':'Повторить чтение'}</button>}
      {command?.startsWith('message:') && !sending && <p className="mt-3 text-sm leading-6 text-muted">Отправка не подтверждена. {messageAttempt ? 'Повторите отправку того же сообщения.' : 'Введите прежний вопрос и повторите отправку с сохранённым ключом команды.'}</p>}
      </div>
      <form className="chat-composer" onSubmit={event => { event.preventDefault(); void submit() }}>
        <label htmlFor={messageId} className="sr-only">Сообщение</label>
        <div className="composer-field">
        <textarea id={messageId} rows={3} maxLength={4000} value={text} disabled={busy || readError || deletionUnconfirmed || !!messageAttempt || !!deleting}
          onChange={event => setText(event.currentTarget.value)} aria-describedby={hintId}
          className="block w-full text-sm leading-6 disabled:opacity-60" />
        <div className="composer-controls">
          <span id={hintId} className="chat-counter">{text.length}/4000</span>
          <div className="composer-actions">{active && <VoiceInput key={sessionId||'new'} disabled={busy || readError || deletionUnconfirmed || !!messageAttempt || !!deleting} onBusyChange={setVoiceBusy} onDraft={draft=>setText(old=>[old,draft].filter(Boolean).join('\n').slice(0,4000))}/>}
          <button type="submit" disabled={busy || (!sessionId&&listLoading) || voiceBusy || readError || deletionUnconfirmed || !!deleting || (!text.trim() && !messageAttempt)} className="chat-send" aria-label={sending?'Отправляем…':command?'Повторить отправку':'Отправить'} title={command?'Повторить отправку':'Отправить'}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7"/></svg>
          </button>
          </div>
        </div>
        </div>
        {hasPending && <p className="mt-3 text-xs leading-6 text-muted">Дождитесь завершения ответа, чтобы отправить сообщение или начать новый диалог.</p>}
        {deletionUnconfirmed && !deleting && <p role="status" className="mt-3 text-xs leading-6 text-muted">Удаление чата не подтверждено. Повторите его кнопкой «Удалить» в списке.</p>}
      </form>
    </section>
  </main>
}
