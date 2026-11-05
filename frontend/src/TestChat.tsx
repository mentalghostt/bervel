import { useEffect, useMemo, useRef, useState } from 'react'
import { ChatRequestError, cancelInspection, chatApi } from './chatApi'
import type { ChatMode, ChatSession, ChatSource, ChatSummary, ChatTurn, ChatWorkflowPhase } from './chatApi'
import VoiceInput from './VoiceInput'
import AssistantResults from './AssistantResults'
import InspectionMarkdown from './InspectionMarkdown'

const buttonClass = 'rounded-lg border border-line bg-white px-4 py-2.5 text-sm font-bold text-accent hover:bg-accent-soft focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50'
const pending = (turn: ChatTurn) => turn.state === 'queued' || turn.state === 'running'
const inspectionDraftKey = (sessionId: string | null) => `bervel.inspection.draft.${sessionId ?? 'new'}`
const inspectionAttemptKey = 'bervel.inspection.pending_send'
type InspectionAttempt = { sessionId: string; key: string }
function readInspectionAttempt(): InspectionAttempt | null {
  try {
    const raw = sessionStorage.getItem(inspectionAttemptKey)
    if (!raw) return null
    const value = JSON.parse(raw) as Partial<InspectionAttempt> | null
    if (typeof value?.sessionId === 'string' && typeof value.key === 'string' && value.key.startsWith('message:')) {
      return { sessionId: value.sessionId, key: value.key }
    }
  } catch { /* Unavailable browser storage cannot restore a pending command. */ }
  return null
}
function readInspectionDraft(sessionId: string | null): string {
  try { return sessionStorage.getItem(inspectionDraftKey(sessionId)) ?? '' }
  catch { return '' }
}
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
function WorkflowProgress({ turn, inspection }: { turn: ChatTurn; inspection: boolean }) {
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
    {inspection&&<p className="inspection-workflow-timing">Прошло: {Math.floor(value.elapsed_seconds/60)} мин {Math.floor(value.elapsed_seconds%60)} с · Шагов исследования: {value.research_rounds}{value.max_research_rounds===null?'':` из ${value.max_research_rounds}`}</p>}
    {!!history.length && <ol aria-label="Пройденные этапы" className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">{history.map(entry => <li key={entry.phase} aria-current={turn.state !== 'failed' && entry.phase === value.phase ? 'step' : undefined} className={turn.state !== 'failed' && entry.phase === value.phase ? 'font-bold text-accent' : undefined}>
      {workflowLabels[entry.phase]}
    </li>)}</ol>}
  </section>
}
function NativeProgress({turn}:{turn:ChatTurn}) {
  if (turn.state === 'completed') return null
  const terminal=['blocked','failed','partial'].includes(turn.state)
  const problem=turn.error||(terminal?turn.workflow?.reason:null)||(turn.state==='failed'?'Не удалось получить ответ.':null)
  const labels={queued:'Codex CLI в очереди',running:'Codex CLI выполняет запрос…',blocked:'Состояние Codex CLI требует проверки',failed:'Выполнение Codex CLI прервано',partial:'Codex CLI: частичный результат'}
  return <section className="inspection-native-progress" aria-label="Ход выполнения CLI"><p role="status" className="text-sm font-bold">{labels[turn.state]}</p>{problem&&<p role="alert" className="error-note whitespace-pre-wrap wrap-anywhere">{problem}</p>}{turn.progress_text&&<p className="mt-2 text-sm whitespace-pre-wrap wrap-anywhere">{problem&&<><span className="text-muted">Последнее сообщение CLI:</span>{' '}</>}{turn.progress_text}</p>}</section>
}

function InspectionStop({sessionId,turn,onStopped}:{sessionId:string;turn:ChatTurn;onStopped:(turn:ChatTurn)=>void}) {
  const storageKey=`bervel.inspection.stop.${sessionId}.${turn.turn_id}`
  const [busy,setBusy]=useState(false),[error,setError]=useState<string|null>(null)
  const [key,setKey]=useState(()=>localStorage.getItem(storageKey)),inFlight=useRef(false)
  async function stop() {
    if(inFlight.current||turn.cancel_requested)return
    inFlight.current=true;setBusy(true);setError(null)
    const command=key||`stop:${crypto.randomUUID()}`;setKey(command);localStorage.setItem(storageKey,command)
    try {const result=await cancelInspection(sessionId,turn.turn_id,command);onStopped(result);localStorage.removeItem(storageKey);setKey(null)}
    catch(reason){setError(reason instanceof Error?reason.message:'Остановка не подтверждена. Повторите с тем же ключом.')}
    finally{inFlight.current=false;setBusy(false)}
  }
  return <div className="mt-3"><button type="button" className="secondary inspection-stop" disabled={busy||turn.cancel_requested} onClick={()=>void stop()}>{turn.cancel_requested?'Остановка запрошена':busy?'Останавливаем…':key?'Повторить остановку':'Остановить'}</button>{turn.cancel_requested&&<p role="status" className="mt-2 text-xs text-muted">Текущее действие завершается. Новые действия запускаться не будут.</p>}{error&&<p role="alert" className="error-note">{error}</p>}</div>
}

export default function TestChat({ active, mode = 'assistant' }: { active: boolean; mode?: ChatMode }) {
  const inspection = mode === 'inspection'
  const sessionStorageKey = mode === 'inspection' ? 'bervel.inspection.session_id' : 'bervel.chat.session_id'
  const commandStorageKey = mode === 'inspection' ? 'bervel.inspection.command_key' : 'bervel.chat.command_key'
  const {createChat, readChat, sendChat, listChats, deleteChat} = useMemo(() => chatApi(mode), [mode])
  const messageId = mode === 'inspection' ? 'inspection-message' : 'chat-message'
  const hintId = mode === 'inspection' ? 'inspection-hint' : 'chat-hint'
  const [restoredAttempt] = useState(() => inspection ? readInspectionAttempt() : null)
  const [sessionId, setSessionId] = useState(() => {
    const selectedId = (inspection ? sessionStorage.getItem(sessionStorageKey) : null) ?? localStorage.getItem(sessionStorageKey)
    return restoredAttempt?.sessionId ?? (selectedId || null)
  })
  const [session, setSession] = useState<ChatSession | null>(null)
  const [text, setText] = useState(() => inspection ? readInspectionDraft(sessionId) : '')
  const [draftStorageError, setDraftStorageError] = useState(false)
  const [loading, setLoading] = useState(!!sessionId)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [readError, setReadError] = useState<string | null>(null)
  const [retry, setRetry] = useState(0)
  const [command, setCommand] = useState(() => {
    const stored = (inspection ? sessionStorage.getItem(commandStorageKey) : null) ?? localStorage.getItem(commandStorageKey)
    return restoredAttempt?.key ?? (inspection && !sessionId && stored?.startsWith('message:') ? null : stored)
  })
  const inFlight = useRef(false)
  const [messageAttempt, setMessageAttempt] = useState<{ key: string; content: string } | null>(() =>
    restoredAttempt && text.trim() ? { key: restoredAttempt.key, content: text } : null)
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
  const native = mode==='inspection'&&session?.engine==='codex-cli'
  const deletionUnconfirmed = !!sessionId && deleteAttempts.current.has(sessionId)
  const pendingCommand = inspection && command?.startsWith('message:') ? command : undefined
  const backgroundPending = inspection ? chats.filter(item=>item.has_pending && item.session_id!==sessionId)
    .map(item=>item.session_id).sort().join(',') : ''

  function draftFor(id: string | null): string {
    return drafts.current.get(id ?? 'new') ?? (inspection ? readInspectionDraft(id) : '')
  }

  function storeDraft(id: string | null, value: string) {
    if (value) drafts.current.set(id ?? 'new', value)
    else drafts.current.delete(id ?? 'new')
    if (!inspection) return true
    try {
      if (value) sessionStorage.setItem(inspectionDraftKey(id), value)
      else sessionStorage.removeItem(inspectionDraftKey(id))
      setDraftStorageError(false)
      return true
    } catch {
      // A failed write can leave an older draft under the same key.
      try { sessionStorage.removeItem(inspectionDraftKey(id)) } catch { /* Storage unavailable. */ }
      setDraftStorageError(true)
      return false
    }
  }

  function editDraft(value: string) {
    setText(value)
    storeDraft(selected.current, value)
  }

  function touchSummary(id: string) { touchedSummaries.current.set(id,++summaryVersion.current) }
  function mergeChats(incoming: ChatSummary[], previous: ChatSummary[], started: number) {
    const merged = new Map(previous.map(item=>[item.session_id,item]))
    for(const item of incoming) if((touchedSummaries.current.get(item.session_id)||0)<=started) merged.set(item.session_id,item)
    return ordered([...merged.values()].filter(item=>!deleted.current.has(item.session_id)))
  }

  function rememberSelection(id: string | null) {
    if (inspection) {
      try { sessionStorage.setItem(sessionStorageKey, id ?? '') } catch { /* Selection stays in memory. */ }
    }
    else if (id) localStorage.setItem(sessionStorageKey, id)
    else localStorage.removeItem(sessionStorageKey)
  }

  function rememberCommand(value: string | null, sendingTo?: string) {
    if (inspection) {
      try {
        if (value && sendingTo) {
          sessionStorage.setItem(inspectionAttemptKey, JSON.stringify({ sessionId: sendingTo, key: value }))
        } else {
          sessionStorage.removeItem(inspectionAttemptKey)
          if (value) sessionStorage.setItem(commandStorageKey, value)
          else sessionStorage.removeItem(commandStorageKey)
        }
        if (command && localStorage.getItem(commandStorageKey) === command) localStorage.removeItem(commandStorageKey)
      } catch {
        setDraftStorageError(true)
      }
    } else if (value) localStorage.setItem(commandStorageKey, value)
    else localStorage.removeItem(commandStorageKey)
    setCommand(value)
  }

  function unavailableChat(id: string|null, draft: string) {
    if(id) {deleted.current.add(id);storeDraft(id,'');setChats(previous=>previous.filter(item=>item.session_id!==id))}
    selected.current=null;readGeneration.current++;followBottom.current=true
    rememberSelection(null);rememberCommand(null);setMessageAttempt(null)
    setSessionId(null);setSession(null);setLoading(false);setReadError(null);setText(draft)
    storeDraft(null,draft)
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
    if (!active || !backgroundPending) return
    let current = true
    async function refreshStatuses() {
      const remaining = new Set(backgroundPending.split(','))
      const finished = new Set<string>()
      const started = summaryVersion.current
      try {
        let offset: number | null = 0
        while (offset !== null && remaining.size) {
          const page = await listChats(offset)
          if (!current) return
          for (const item of page.sessions) {
            if (remaining.delete(item.session_id) && !item.has_pending) finished.add(item.session_id)
          }
          offset = page.next_offset
        }
      } catch { /* A failed status check is retried without interrupting the open dialog. */ }
      if (!current) return
      for (const id of finished) {
        if (id === selected.current || (touchedSummaries.current.get(id) || 0) > started) finished.delete(id)
        else touchSummary(id)
      }
      if (finished.size) setChats(previous=>previous.map(item=>finished.has(item.session_id)?{...item,has_pending:false}:item))
      timer = setTimeout(() => { void refreshStatuses() }, 3000)
    }
    let timer = setTimeout(() => { void refreshStatuses() }, 3000)
    return () => { current = false; clearTimeout(timer) }
  }, [active, backgroundPending, listChats])

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
        const result = await readChat(sessionId!, pendingCommand)
        if (!current || selected.current !== sessionId || generation !== readGeneration.current) return
        setSession(result); setReadError(null)
        if (pendingCommand && result.accepted_turn_id) {
          if (messageAttempt?.key === pendingCommand) {
            storeDraft(sessionId, '')
            setText('')
          }
          rememberCommand(null)
          setMessageAttempt(null)
          setError(null)
        }
        touchSummary(result.session_id)
        setChats(previous=>ordered([summary(result),...previous.filter(item=>item.session_id!==result.session_id)]))
        if (result.turns.some(pending) || (pendingCommand && !result.accepted_turn_id)) {
          timer = setTimeout(() => { void refresh() }, result.turns.some(pending) ? 1000 : 3000)
        }
      } catch (reason) {
        if (!current || selected.current !== sessionId || generation !== readGeneration.current) return
        if(reason instanceof ChatRequestError && reason.status===404) {unavailableChat(sessionId,inspection ? draftFor(sessionId) : text);return}
        setReadError(reason instanceof Error ? reason.message : 'Не удалось прочитать диалог.')
        if (pendingCommand && (!(reason instanceof ChatRequestError) || reason.status >= 500 || reason.status === 429)) {
          timer = setTimeout(() => { void refresh() }, 3000)
        }
      } finally { if (current && selected.current === sessionId && generation === readGeneration.current) setLoading(false) }
    }
    void refresh(true)
    return () => { current = false; clearTimeout(timer) }
  }, [active, sessionId, sending, retry, pendingCommand])

  useEffect(() => {
    if(active && followBottom.current && transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight
  }, [active,session])

  async function submit() {
    const content = messageAttempt?.content ?? (mode==='inspection'?text:text.trim())
    if (inFlight.current || deleteInFlight.current || (!sessionId&&listInFlight.current) || deletionUnconfirmed || busy || voiceBusy || readError || !content.trim() || (mode!=='inspection'&&content.length>4000)) return
    inFlight.current = true
    setSending(true); setError(null)
    let id = sessionId
    try {
      if (!id) {
        const key = command?.startsWith('session:') ? command : `session:${crypto.randomUUID()}`
        rememberCommand(key)
        const created = await createChat(key)
        rememberSelection(created.session_id)
        id = created.session_id
        selected.current = id; readGeneration.current++
        setSessionId(id); setSession(created)
        touchSummary(id)
        setChats(previous=>ordered([summary(created),...previous.filter(item=>item.session_id!==id)]))
        setNextOffset(previous=>previous===null?null:previous+1)
        if (!inspection || storeDraft(id,content)) storeDraft(null,'')
        rememberCommand(null)
      }
      const key = messageAttempt?.key ?? (command?.startsWith('message:') ? command : `message:${crypto.randomUUID()}`)
      if (inspection) storeDraft(id, content)
      rememberCommand(key, id)
      setMessageAttempt({ key, content })
      const result = await sendChat(id, key, content)
      setSession(previous => {
        if(!previous || previous.session_id !== id) return previous
        return { ...previous, turns: [...previous.turns.filter(item => item.turn_id !== result.turn_id), result] }
      })
      touchSummary(id)
      setChats(previous=>previous.map(item=>item.session_id===id?{...item,title:item.turn_count?item.title:result.question.slice(0,120),turn_count:item.turn_count+(session?.turns.some(turn=>turn.turn_id===result.turn_id)?0:1),has_pending:pending(result)}:item))
      storeDraft(id,'')
      followBottom.current = true
      setMessageAttempt(null)
      rememberCommand(null); setText('')
    } catch (reason) {
      if(reason instanceof ChatRequestError && reason.status===404) unavailableChat(id,content)
      else {
        if (mode === 'inspection' && reason instanceof ChatRequestError && [400, 401, 403, 413, 415, 422].includes(reason.status)) {
          setMessageAttempt(null)
          rememberCommand(null)
        }
        setError(reason instanceof Error ? reason.message : 'Не удалось отправить сообщение.')
      }
    } finally { inFlight.current = false; setSending(false) }
  }

  function newChat() {
    if (inFlight.current || deleteInFlight.current || busy || voiceBusy || command) return
    storeDraft(sessionId,text)
    selected.current = null; readGeneration.current++; followBottom.current = true
    rememberSelection(null)
    setSessionId(null); setSession(null); setText(draftFor(null)); setError(null); setReadError(null); setLoading(false)
    setMessageAttempt(null)
  }

  function chooseChat(id: string) {
    if(id===sessionId || inFlight.current || deleteInFlight.current || voiceBusy || command) return
    storeDraft(sessionId,text)
    selected.current = id; readGeneration.current++; followBottom.current = true
    rememberSelection(id)
    setSessionId(id);setSession(null);setText(draftFor(id));setError(null);setReadError(null);setLoading(true)
  }

  function forgetChat(id: string) {
    deleted.current.add(id);deleteAttempts.current.delete(id);storeDraft(id,'')
    setChats(previous=>previous.filter(item=>item.session_id!==id));setNextOffset(previous=>previous===null?null:Math.max(0,previous-1))
    if(selected.current===id) {
      selected.current=null;readGeneration.current++;followBottom.current=true
      rememberSelection(null)
      setSessionId(null);setSession(null);setText(draftFor(null));setLoading(false);setError(null);setReadError(null)
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
            <span className="chat-title" title={item.title}>{item.title}</span>
          </button>
          <div className="chat-session-footer"><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})}</time>{item.has_pending&&<span className="chat-pending">Готовим ответ…</span>}<button type="button" disabled={listLoading||sending||voiceBusy||!!command||!!deleting||item.has_pending||(item.session_id===sessionId&&hasPending)} onClick={()=>void removeChat(item.session_id)}>{deleting===item.session_id?'Удаляем…':'Удалить'}</button></div>
        </li>)}</ol>
        {listLoading&&<p role="status" className="chat-list-note">Загружаем чаты…</p>}
        {listError&&<div className="chat-list-note"><p role="alert" className="text-red-800">{listError}</p><button type="button" className="secondary" disabled={listLoading||!!deleting} onClick={()=>{setListError(null);setListRetry(value=>value+1)}}>Обновить список</button></div>}
        {nextOffset!==null&&<button type="button" className="secondary chat-more" disabled={listLoading||sending||!!deleting} onClick={()=>void moreChats()}>Ещё чаты</button>}
        {!listLoading&&!listError&&!chats.length&&<p className="chat-list-note">Чатов пока нет</p>}
      </div>
    </aside>
    <section aria-label="Диалог" className="chat-workspace rounded-2xl border border-line bg-white">
      <div className="chat-transcript" ref={transcript} onScroll={event=>{const node=event.currentTarget;followBottom.current=node.scrollHeight-node.scrollTop-node.clientHeight<80}}>
      {native&&<p className="inspection-engine"><strong>Codex CLI</strong><span>Команды и изменения файлов напрямую</span></p>}
      {loading && <p role="status" className="text-sm text-muted">Восстанавливаем диалог…</p>}
      {!loading && !session?.turns.length && <div className="py-8">
        <h2 className="font-display text-xl font-semibold">{mode === 'inspection' ? 'Что проверить?' : 'Начните с вопроса'}</h2>
        {mode === 'inspection' && <p className="mt-3 text-sm leading-6 text-muted">{native?'Codex CLI выполняет команды и изменяет файлы напрямую, без подтверждения патчей. Проверяйте BERVEL, запускайте тесты, создавайте документы и схемы. История и контекст продолжаются в этом диалоге; новый диалог начинает отдельную проверку.':'Отправьте запрос проверки BERVEL. Существующий диалог продолжает сохранённую историю, новый начинает отдельную проверку. Режим выполнения отображается после подключения к диалогу.'}</p>}
      </div>}
      <ol className="space-y-7" aria-label="Сообщения">
        {session?.turns.map(item => <li key={item.turn_id} className="min-w-0 space-y-4">
          <article aria-label="Сообщение пользователя" className="ml-auto max-w-3xl rounded-xl border border-accent/15 bg-accent-soft px-4 py-3 sm:px-5">
            <h2 className="text-xs font-bold text-accent">Вы</h2>
            <p className="mt-2 text-sm leading-7 whitespace-pre-wrap wrap-anywhere">{item.question}</p>
          </article>
          <article aria-label="Ответ Codex" className="max-w-3xl rounded-xl border border-line px-4 py-4 sm:px-5">
            <h2 className="text-xs font-bold text-accent">Codex</h2>
            {native?<NativeProgress turn={item}/>:<WorkflowProgress turn={item} inspection={mode==='inspection'}/>}
            {mode==='inspection'&&pending(item)&&session&&<InspectionStop sessionId={session.session_id} turn={item} onStopped={result=>{setSession(previous=>previous?.session_id===session.session_id?{...previous,turns:previous.turns.map(turn=>turn.turn_id===result.turn_id?result:turn)}:previous);setRetry(value=>value+1)}}/>}
            {item.answer !== null && (mode==='inspection'?<InspectionMarkdown text={item.answer}/>:<p className="mt-2 text-sm leading-7 whitespace-pre-wrap wrap-anywhere">{item.answer}</p>)}
            {!native&&item.state === 'failed' && <p role="alert" className="mt-3 text-sm leading-6 text-red-800">{item.error ?? 'Не удалось получить ответ.'}</p>}
            <AssistantResults results={item.results} actions={item.actions} inspection={mode==='inspection'} active={active} native={native}/>
            <Sources turn={item} />
          </article>
        </li>)}
      </ol>
      {(readError || error) && <p role="alert" className="mt-5 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm leading-6 text-red-800">{readError || error}</p>}
      {readError && <button type="button" className={`${buttonClass} mt-3`} disabled={loading} aria-busy={loading} onClick={() => { setError(null); setRetry(value => value + 1) }}>{loading?'Читаем историю…':'Повторить чтение'}</button>}
      {command?.startsWith('message:') && !sending && <p className="mt-3 text-sm leading-6 text-muted">Отправка не подтверждена. {messageAttempt ? 'Повторите отправку того же сообщения.' : 'Введите прежний вопрос и повторите отправку с сохранённым ключом команды.'}</p>}
      </div>
      <form className="chat-composer" onSubmit={event => { event.preventDefault(); void submit() }}>
        <label htmlFor={messageId} className="sr-only">Сообщение</label>
        <div className="composer-field">
        <textarea id={messageId} rows={3} maxLength={mode==='inspection'?undefined:4000} value={text} disabled={busy || !!readError || deletionUnconfirmed || !!messageAttempt || !!deleting}
          onChange={event => editDraft(event.currentTarget.value)} aria-describedby={hintId}
          className="block w-full text-sm leading-6 disabled:opacity-60" />
        <div className="composer-controls">
          <span id={hintId} className="chat-counter">{text.length}{mode==='inspection'?' символов':'/4000'}</span>
          <div className="composer-actions">{active && <VoiceInput key={sessionId||'new'} disabled={busy || !!readError || deletionUnconfirmed || !!messageAttempt || !!deleting} onBusyChange={setVoiceBusy} onDraft={draft=>{
            const combined=[draftFor(sessionId),draft].filter(Boolean).join('\n')
            editDraft(inspection ? combined : combined.slice(0,4000))
          }}/>}
          <button type="submit" disabled={busy || (!sessionId&&listLoading) || voiceBusy || !!readError || deletionUnconfirmed || !!deleting || (!text.trim() && !messageAttempt)} className="chat-send" aria-label={sending?'Отправляем…':command?'Повторить отправку':'Отправить'} title={command?'Повторить отправку':'Отправить'}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7"/></svg>
          </button>
          </div>
        </div>
        </div>
        {hasPending && <p className="mt-3 text-xs leading-6 text-muted">Дождитесь завершения ответа, чтобы отправить сообщение или начать новый диалог.</p>}
        {draftStorageError && <p role="status" className="mt-3 text-xs leading-6 text-muted">Браузер не сохранил черновик. Скопируйте текст перед обновлением страницы.</p>}
        {deletionUnconfirmed && !deleting && <p role="status" className="mt-3 text-xs leading-6 text-muted">Удаление чата не подтверждено. Повторите его кнопкой «Удалить» в списке.</p>}
      </form>
    </section>
  </main>
}
