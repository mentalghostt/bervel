export type ChatMode = 'assistant' | 'inspection'
export type ChatSource = {
  artifact_id: string; filename: string; created_at: string
  row_start: number; row_end: number; total_rows: number; excerpt: string; truncated: boolean
  unit?: 'row' | 'line' | 'page' | 'image' | 'metadata'
  status?: 'ready' | 'partial' | 'metadata_only'
  media_type?: string
  image_attached?: boolean
}
export type ChatTurn = {
  turn_id: string; question: string; answer: string | null
  state: 'queued' | 'running' | 'completed' | 'partial' | 'blocked' | 'failed'; created_at: string
  sources: ChatSource[]; error: string | null
  results: AssistantResult[]; actions: ActionReceipt[]
  workflow?: ChatWorkflow | null
  cancel_requested?: boolean
  progress_text?: string | null
}
export type ChatWorkflowPhase = 'research' | 'synthesizing' | 'generating' | 'verifying' | 'completed' | 'partial' | 'blocked'
export type ChatWorkflow = {
  phase: ChatWorkflowPhase; updated_at: string; reason: string | null
  research_rounds: number; max_research_rounds: number | null; web_searches: number; max_web_searches: number
  elapsed_seconds: number; max_research_seconds: number | null
  history: { phase: ChatWorkflowPhase; at: string; reason: string | null }[]
}
export type AssistantResult =
  | {kind:'file';artifact_id:string;filename:string;media_type:string;download_url:string;preview_url:string;is_mock?:boolean;notice?:string|null}
  | {kind:'ticket';ticket_id:string;title:string;status:string;detail_url:string;deep_link:string}
  | {kind:'web';answer:string;sources:{title:string;url:string}[]}
  | {kind:'data';operation:string;data:unknown}
  | InspectionTestResult
  | CodeProposal
export type InspectionTestResult = {kind:'inspection_test';suite:string;state:'passed'|'failed';exit_code:number;summary:string;output:string}
export type CodeProposal = {kind:'code_proposal';proposal_id:string;session_id:string;title:string;explanation:string;fingerprint:string;status:'pending'|'applied'|'rejected'|'failed'|'unknown';diff:string;files:string[]}
export type ActionReceipt={action_id:string;operation:string;state:'running'|'completed'|'failed'|'unknown';result:unknown;error:unknown}
export type ChatSession = { session_id: string; created_at: string; turns: ChatTurn[]; engine?:'codex-cli'; cli_thread_id?:string|null; accepted_turn_id?: string | null }
export type ChatSummary = { session_id: string; created_at: string; title: string; turn_count: number; has_pending: boolean }

const unexpected = 'API чата вернул несовместимый ответ. Если приложение обновлялось, обновите страницу. Повторное чтение не запускает анализ заново.'
export class ChatRequestError extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function uuid(value: unknown): value is string { return typeof value === 'string' && uuidPattern.test(value) }
function date(value: unknown): value is string { return typeof value === 'string' && Number.isFinite(Date.parse(value)) }
function integer(value: unknown): value is number { return typeof value === 'number' && Number.isInteger(value) && value >= 0 }
function duration(value: unknown): value is number { return typeof value === 'number' && Number.isFinite(value) && value >= 0 }
const workflowPhases = ['research', 'synthesizing', 'generating', 'verifying', 'completed', 'partial', 'blocked']
function workflow(value: unknown, inspection: boolean): ChatWorkflow {
  if (!object(value) || !workflowPhases.includes(String(value.phase)) || !date(value.updated_at)
    || !(value.reason === null || typeof value.reason === 'string')
    || !integer(value.research_rounds) || !(inspection && value.max_research_rounds === null || integer(value.max_research_rounds))
    || !integer(value.web_searches) || !integer(value.max_web_searches)
    || !duration(value.elapsed_seconds) || !(inspection && value.max_research_seconds === null || duration(value.max_research_seconds))
    || !Array.isArray(value.history) || value.history.some(item => !object(item)
      || !workflowPhases.includes(String(item.phase)) || !date(item.at)
      || !(item.reason === null || typeof item.reason === 'string'))) throw new Error(unexpected)
  return value as ChatWorkflow
}
function source(value: unknown): ChatSource {
  if (!object(value) || !uuid(value.artifact_id)
    || typeof value.filename !== 'string' || !date(value.created_at) || !integer(value.row_start)
    || !integer(value.row_end) || value.row_end < value.row_start || !integer(value.total_rows)
    || typeof value.excerpt !== 'string' || typeof value.truncated !== 'boolean'
    || (value.unit !== undefined && !['row', 'line', 'page', 'image', 'metadata'].includes(String(value.unit)))
    || (value.status !== undefined && !['ready', 'partial', 'metadata_only'].includes(String(value.status)))) throw new Error(unexpected)
  return value as ChatSource
}
function turn(value: unknown, mode: ChatMode = 'assistant'): ChatTurn {
  if (!object(value) || !uuid(value.turn_id) || typeof value.question !== 'string'
    || !(value.answer === null || typeof value.answer === 'string') || !date(value.created_at)
    || !['queued', 'running', 'completed', 'partial', 'blocked', 'failed'].includes(String(value.state))
    || !Array.isArray(value.sources) || !(value.error === null || typeof value.error === 'string')
    || (value.state === 'completed' && typeof value.answer !== 'string')
    || !(value.progress_text === undefined || value.progress_text === null || typeof value.progress_text === 'string')) throw new Error(unexpected)
  if(value.results !== undefined && !Array.isArray(value.results)) throw Error(unexpected)
  if(value.actions !== undefined && !Array.isArray(value.actions)) throw Error(unexpected)
  const results=(value.results || []) as unknown[]
  if(results.some(item=>!object(item)||!['file','ticket','web','data','inspection_test','code_proposal'].includes(String(item.kind))
    || (item.kind==='file'&&(!uuid(item.artifact_id)||typeof item.filename!=='string'||typeof item.media_type!=='string'))
    || (item.kind==='ticket'&&(!uuid(item.ticket_id)||typeof item.title!=='string'))
    || (item.kind==='web'&&(typeof item.answer!=='string'||!Array.isArray(item.sources)||item.sources.some(s=>!object(s)||typeof s.title!=='string'||typeof s.url!=='string')))
    || (item.kind==='inspection_test'&&(typeof item.suite!=='string'||!['passed','failed'].includes(String(item.state))||typeof item.exit_code!=='number'||!Number.isInteger(item.exit_code)||typeof item.summary!=='string'||typeof item.output!=='string'))
    || (item.kind==='code_proposal'&&!validProposal(item))))throw Error(unexpected)
  const actions=(value.actions || []) as unknown[]
  if(actions.some(item=>!object(item)||typeof item.operation!=='string'||!['running','completed','failed','unknown'].includes(String(item.state))))throw Error(unexpected)
  return { ...value, sources: value.sources.map(source), results, actions,
    workflow: value.workflow === undefined || value.workflow === null ? value.workflow : workflow(value.workflow, mode === 'inspection') } as ChatTurn
}
function session(value: unknown, mode: ChatMode): ChatSession {
  if (!object(value) || !uuid(value.session_id) || !date(value.created_at) || !Array.isArray(value.turns)
    || !(value.engine === undefined || value.engine === 'codex-cli')
    || !(value.cli_thread_id === undefined || value.cli_thread_id === null || typeof value.cli_thread_id === 'string')
    || !(value.accepted_turn_id == null || uuid(value.accepted_turn_id))) throw new Error(unexpected)
  const turns = value.turns.map(value=>turn(value,mode))
  if (value.accepted_turn_id && !turns.some(turn => turn.turn_id === value.accepted_turn_id)) throw new Error(unexpected)
  return { session_id: value.session_id, created_at: value.created_at, turns,
    ...(typeof value.accepted_turn_id === 'string' ? {accepted_turn_id:value.accepted_turn_id} : {}),
    ...(value.engine === 'codex-cli' ? {engine:value.engine} : {}),...(value.cli_thread_id === undefined ? {} : {cli_thread_id:value.cli_thread_id as string|null}) }
}
function validProposal(value: unknown): value is CodeProposal {
  return object(value) && value.kind === 'code_proposal' && uuid(value.proposal_id) && uuid(value.session_id)
    && typeof value.title === 'string' && typeof value.explanation === 'string' && typeof value.fingerprint === 'string' && /^[a-f0-9]{64}$/i.test(value.fingerprint)
    && ['pending','applied','rejected','failed','unknown'].includes(String(value.status)) && typeof value.diff === 'string'
    && Array.isArray(value.files) && value.files.every(path=>typeof path === 'string')
}
async function request(path: string, body?: unknown, method = body === undefined ? 'GET' : 'POST', mode: ChatMode = 'assistant', resource = 'sessions'): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(`/api/v0.1/${mode === 'inspection' ? 'inspection' : 'chat'}/${resource}${path}`, {
      method,
      headers: body === undefined ? { 'X-Bervel-Web': '1' } : { 'X-Bervel-Web': '1', 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(30_000),
    })
  } catch {
    if (mode === 'inspection' && method === 'GET') throw new Error('Не удалось прочитать данные чата. Проверьте соединение.')
    throw new Error(method === 'DELETE' ? 'Удаление не подтверждено. Повторите его с помощью той же кнопки.' : 'Не удалось получить ответ от API чата. Исход отправки может быть неизвестен; повтор использует тот же ключ команды.')
  }
  if (!response.ok) {
    const messages: Record<number, string> = {
      401: 'Доступ к чату не настроен на сервере.', 403: 'Недостаточно прав для работы с чатом.',
      404: 'Диалог не найден в области данных. Начните новый диалог.',
      409: 'Команда конфликтует с состоянием диалога. Проверьте историю перед повтором.',
      422: mode==='inspection'?'Сообщение не принято. Проверьте запрос и повторите отправку.':'Сообщение должно содержать от 1 до 4000 символов.',
    }
    const proposalMessages:Record<number,string>={404:'Предложение не найдено или недоступно.',422:'Команда для патча не принята. Обновите состояние предложения.'}
    const inspectionSendMessages:Record<number,string> = {
      400: 'Сообщение не принято. Проверьте текст и отправьте его снова.',
      413: 'Сервер отклонил сообщение из-за размера. Сократите текст и отправьте его снова.',
      415: 'Сервер отклонил формат запроса. Обновите страницу и повторите отправку.',
    }
    const details = resource === 'proposals' ? proposalMessages : mode === 'inspection' && method === 'POST' ? inspectionSendMessages : messages
    throw new ChatRequestError(details[response.status] ?? messages[response.status] ?? 'API чата временно недоступен. Повторите запрос.', response.status)
  }
  try { return await response.json() } catch { throw new Error(unexpected) }
}
export async function createChat(commandKey: string, mode: ChatMode = 'assistant'): Promise<ChatSession> {
  return session(await request('', { command_key: commandKey }, 'POST', mode), mode)
}
export async function readChat(sessionId: string, mode: ChatMode = 'assistant', pendingCommand?: string): Promise<ChatSession> {
  const query = mode === 'inspection' && pendingCommand ? `?pending_command_key=${encodeURIComponent(pendingCommand)}` : ''
  const result = session(await request(`/${encodeURIComponent(sessionId)}${query}`, undefined, 'GET', mode), mode)
  if (result.session_id !== sessionId) throw new Error(unexpected)
  return result
}
export async function sendChat(sessionId: string, commandKey: string, content: string, mode: ChatMode = 'assistant'): Promise<ChatTurn> {
  return turn(await request(`/${encodeURIComponent(sessionId)}/messages`, { command_key: commandKey, content }, 'POST', mode), mode)
}
export async function listChats(offset = 0, mode: ChatMode = 'assistant'): Promise<{ sessions: ChatSummary[]; next_offset: number | null }> {
  const value = await request(`?limit=50&offset=${offset}`, undefined, 'GET', mode)
  if (!object(value) || !Array.isArray(value.sessions) || !(value.next_offset === null || integer(value.next_offset) && value.next_offset > offset)) throw new Error(unexpected)
  const ids = new Set<string>()
  for (const item of value.sessions) {
    if (!object(item) || !uuid(item.session_id) || !date(item.created_at) || typeof item.title !== 'string' || !integer(item.turn_count) || typeof item.has_pending !== 'boolean' || ids.has(item.session_id)) throw new Error(unexpected)
    ids.add(item.session_id)
  }
  return value as { sessions: ChatSummary[]; next_offset: number | null }
}
export async function deleteChat(sessionId: string, commandKey: string, mode: ChatMode = 'assistant'): Promise<void> {
  const value = await request(`/${encodeURIComponent(sessionId)}?command_key=${encodeURIComponent(commandKey)}`, undefined, 'DELETE', mode)
  if (!object(value) || value.session_id !== sessionId || value.deleted !== true) throw new Error(unexpected)
}

export async function cancelInspection(sessionId: string, turnId: string, commandKey: string): Promise<ChatTurn> {
  const value = turn(await request(`/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(turnId)}/cancel`, {command_key:commandKey}, 'POST', 'inspection'), 'inspection')
  if(value.turn_id !== turnId) throw new Error(unexpected)
  return value
}
export async function readProposal(id: string): Promise<CodeProposal> {
  const value = await request(`/${encodeURIComponent(id)}`, undefined, 'GET', 'inspection', 'proposals')
  if(!validProposal(value) || value.proposal_id !== id) throw new Error(unexpected)
  return value
}
export async function decideProposal(id: string, action: 'confirm' | 'reject', key: string, fingerprint: string): Promise<CodeProposal> {
  const value = await request(`/${encodeURIComponent(id)}/${action}`, {command_key:key,fingerprint}, 'POST', 'inspection', 'proposals')
  if(!validProposal(value) || value.proposal_id !== id || value.fingerprint !== fingerprint) throw new Error(unexpected)
  return value
}

export function chatApi(mode: ChatMode) {
  return {
    createChat: (key: string) => createChat(key, mode),
    readChat: (id: string, pendingCommand?: string) => readChat(id, mode, pendingCommand),
    sendChat: (id: string, key: string, content: string) => sendChat(id, key, content, mode),
    listChats: (offset = 0) => listChats(offset, mode),
    deleteChat: (id: string, key: string) => deleteChat(id, key, mode),
  }
}
