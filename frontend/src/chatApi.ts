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
}
export type ChatWorkflowPhase = 'research' | 'synthesizing' | 'generating' | 'verifying' | 'completed' | 'partial' | 'blocked'
export type ChatWorkflow = {
  phase: ChatWorkflowPhase; updated_at: string; reason: string | null
  research_rounds: number; max_research_rounds: number; web_searches: number; max_web_searches: number
  elapsed_seconds: number; max_research_seconds: number
  history: { phase: ChatWorkflowPhase; at: string; reason: string | null }[]
}
export type AssistantResult =
  | {kind:'file';artifact_id:string;filename:string;media_type:string;download_url:string;preview_url:string;is_mock?:boolean;notice?:string|null}
  | {kind:'radar_run';run_id:string;state:string;detail_url:string;pdf_url:string;deep_link:string}
  | {kind:'ticket';ticket_id:string;title:string;status:string;detail_url:string;deep_link:string}
  | {kind:'web';answer:string;sources:{title:string;url:string}[]}
  | {kind:'data';operation:string;data:unknown}
export type ActionReceipt={action_id:string;operation:string;state:'running'|'completed'|'failed'|'unknown';result:unknown;error:unknown}
export type ChatSession = { session_id: string; created_at: string; turns: ChatTurn[] }
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
function workflow(value: unknown): ChatWorkflow {
  if (!object(value) || !workflowPhases.includes(String(value.phase)) || !date(value.updated_at)
    || !(value.reason === null || typeof value.reason === 'string')
    || !integer(value.research_rounds) || !integer(value.max_research_rounds)
    || !integer(value.web_searches) || !integer(value.max_web_searches)
    || !duration(value.elapsed_seconds) || !duration(value.max_research_seconds)
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
function turn(value: unknown): ChatTurn {
  if (!object(value) || !uuid(value.turn_id) || typeof value.question !== 'string'
    || !(value.answer === null || typeof value.answer === 'string') || !date(value.created_at)
    || !['queued', 'running', 'completed', 'partial', 'blocked', 'failed'].includes(String(value.state))
    || !Array.isArray(value.sources) || !(value.error === null || typeof value.error === 'string')
    || (value.state === 'completed' && typeof value.answer !== 'string')) throw new Error(unexpected)
  if(value.results !== undefined && !Array.isArray(value.results)) throw Error(unexpected)
  if(value.actions !== undefined && !Array.isArray(value.actions)) throw Error(unexpected)
  const results=(value.results || []) as unknown[]
  if(results.some(item=>!object(item)||!['file','radar_run','ticket','web','data'].includes(String(item.kind))
    || (item.kind==='file'&&(!uuid(item.artifact_id)||typeof item.filename!=='string'||typeof item.media_type!=='string'))
    || (item.kind==='radar_run'&&!uuid(item.run_id)) || (item.kind==='ticket'&&(!uuid(item.ticket_id)||typeof item.title!=='string'))
    || (item.kind==='web'&&(typeof item.answer!=='string'||!Array.isArray(item.sources)||item.sources.some(s=>!object(s)||typeof s.title!=='string'||typeof s.url!=='string')))))throw Error(unexpected)
  const actions=(value.actions || []) as unknown[]
  if(actions.some(item=>!object(item)||typeof item.operation!=='string'||!['running','completed','failed','unknown'].includes(String(item.state))))throw Error(unexpected)
  return { ...value, sources: value.sources.map(source), results, actions,
    workflow: value.workflow === undefined || value.workflow === null ? value.workflow : workflow(value.workflow) } as ChatTurn
}
function session(value: unknown): ChatSession {
  if (!object(value) || !uuid(value.session_id) || !date(value.created_at) || !Array.isArray(value.turns)) throw new Error(unexpected)
  return { session_id: value.session_id, created_at: value.created_at, turns: value.turns.map(turn) }
}
async function request(path: string, body?: unknown, method = body === undefined ? 'GET' : 'POST', mode: ChatMode = 'assistant'): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(`/api/v0.1/${mode === 'inspection' ? 'inspection' : 'chat'}/sessions${path}`, {
      method,
      headers: body === undefined ? { 'X-Bervel-Web': '1' } : { 'X-Bervel-Web': '1', 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(30_000),
    })
  } catch { throw new Error(method === 'DELETE' ? 'Удаление не подтверждено. Повторите его с помощью той же кнопки.' : 'Не удалось получить ответ от API чата. Исход отправки может быть неизвестен; повтор использует тот же ключ команды.') }
  if (!response.ok) {
    const messages: Record<number, string> = {
      401: 'Доступ к чату не настроен на сервере.', 403: 'Недостаточно прав для работы с чатом.',
      404: 'Диалог не найден в области данных. Начните новый диалог.',
      409: 'Команда конфликтует с состоянием диалога. Проверьте историю перед повтором.',
      422: 'Сообщение должно содержать от 1 до 4000 символов.',
    }
    throw new ChatRequestError(messages[response.status] ?? 'API чата временно недоступен. Повторите запрос.', response.status)
  }
  try { return await response.json() } catch { throw new Error(unexpected) }
}
export async function createChat(commandKey: string, mode: ChatMode = 'assistant'): Promise<ChatSession> {
  return session(await request('', { command_key: commandKey }, 'POST', mode))
}
export async function readChat(sessionId: string, mode: ChatMode = 'assistant'): Promise<ChatSession> {
  const result = session(await request(`/${encodeURIComponent(sessionId)}`, undefined, 'GET', mode))
  if (result.session_id !== sessionId) throw new Error(unexpected)
  return result
}
export async function sendChat(sessionId: string, commandKey: string, content: string, mode: ChatMode = 'assistant'): Promise<ChatTurn> {
  return turn(await request(`/${encodeURIComponent(sessionId)}/messages`, { command_key: commandKey, content }, 'POST', mode))
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

export function chatApi(mode: ChatMode) {
  return {
    createChat: (key: string) => createChat(key, mode),
    readChat: (id: string) => readChat(id, mode),
    sendChat: (id: string, key: string, content: string) => sendChat(id, key, content, mode),
    listChats: (offset = 0) => listChats(offset, mode),
    deleteChat: (id: string, key: string) => deleteChat(id, key, mode),
  }
}
