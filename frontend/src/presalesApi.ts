export type PresalesSettings = { target_margin_percent: string; special_min_margin_percent: string; seller_contacts: string; offer_terms: string }
export type PresalesSettingsSnapshot = { snapshot_id: string | null; settings: PresalesSettings; requires_save: boolean }
export type PresalesSummary = { request_id: string; title: string; client: string | null; created_at: string; updated_at: string; current_revision_id: string; state: string; phase: string }
export type PresalesJob = { job_id: string; state: string; phase: string; error: { code: string; message: string } | null; stop_requested: boolean; progress: Record<string, unknown> }
export type PresalesRequirement = { name: string; value: string }
export type PresalesCandidate = { sku: string; name: string; differences?: unknown; [key: string]: unknown }
export type PresalesLine = {
  line_id: string; name: string; quantity: string | null; unit: string | null; requirements: PresalesRequirement[]; evidence: Record<string, unknown>[]; client_unit_price: string | null; currency: string | null;
  supply_status?: 'full_stock' | 'partial_or_manufacture' | 'not_manufactured' | 'special_order' | null; needs_review?: boolean; reasons?: string[]; candidates?: PresalesCandidate[]; selected_sku?: string | null;
  stock_quantity?: string | null; production_quantity?: string | null; offered_quantity?: string | null; ready_at?: string | null; unit_cost?: string | null; unit_price?: string | null; line_total?: string | null; margin_percent?: string | null; offers?: Record<string, unknown>[]; special_order?: Record<string, unknown> | null;
}
export type PresalesList = { list_id: string; artifact_id: string; filename: string; complete: boolean; position_count?: number; attribute_count?: number }
export type PresalesOffer = { number: string; date: string; client: string | null; seller_contacts: string; offer_terms: string; lines: Record<string, unknown>[]; totals: Record<string, string>; clarifications: Record<string, unknown>[]; unsupported: Record<string, unknown>[] }
export type PresalesDecisions = { primary_list_id?: string; line_updates?: Record<string, Partial<Pick<PresalesLine, 'name' | 'quantity' | 'unit' | 'requirements' | 'client_unit_price' | 'currency'>>>; approvals?: Record<string, { sku: string; analog_approved?: boolean; adaptation_approved?: boolean; cost_approved?: boolean }>; client?: string; requested_deadline?: string }
// A null approval is a command to remove a saved decision; stored revisions contain only approvals.
export type PresalesDecisionPatch = Omit<PresalesDecisions, 'approvals'> & { approvals?: Record<string, NonNullable<PresalesDecisions['approvals']>[string] | null> }
export type PresalesRevision = {
  revision_id: string; request_id: string; revision_number: number; parent_revision_id: string | null; created_at: string; evaluated_at: string | null; state: string; phase: string; settings_snapshot_id: string; company_snapshot_id: string;
  sources: { artifact_id: string; filename: string; sha256: string }[];
  extracted: { title: string; client: string | null; requested_deadline: string | null; lists: PresalesList[]; primary_list_id: string | null; warnings: string[]; complete: boolean; client_terms: Record<string, unknown> } | null;
  metrics: { product_match_percent: string | null; stock_coverage_percent: string | null; product_match_incomplete: boolean; stock_coverage_incomplete: boolean } | null;
  review: { text: string; issues: string[] } | null; score: number | null; model_error: Record<string, unknown> | null; offer: PresalesOffer | null; artifacts: { pdf: string | null; docx: string | null }; warnings: string[]; decisions: PresalesDecisions; job: PresalesJob | null;
}
export type PresalesRequest = { request: PresalesSummary; revisions: { revision_id: string; revision_number: number; created_at: string; state: string }[]; job: PresalesJob | null }
export type PresalesReceipt = { request_id: string; revision_id: string; job_id: string; state: string }
export type PresalesCommand = { path: string; method: 'POST' | 'PUT'; body: Record<string, unknown> & { command_key: string } }
export const presalesPendingKey = 'bervel.presales.pending.v1'
export const presalesState = (state: string) => ({ queued: 'В очереди', running: 'В работе', complete: 'Готово', completed: 'Готово', stopped: 'Остановлено', failed: 'Ошибка', partial: 'Частичный результат', cancelled: 'Остановлено', pending: 'Ожидает обработки' })[state] || state
export const presalesDate = (value: string | null | undefined) => value && Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeZone: 'Europe/Moscow' }).format(new Date(value)) : value || 'Не указан'
const receiptsKey = 'bervel.presales.receipts.v1'
const reads = new Map<string, Promise<unknown>>()

export class PresalesApiError extends Error {
  readonly rejected: boolean
  constructor(message: string, rejected = false) { super(message); this.rejected = rejected }
}
function object(value: unknown): value is Record<string, unknown> { return !!value && typeof value === 'object' && !Array.isArray(value) }
const nullableString = (value: unknown) => value === null || typeof value === 'string'
const strings = (value: unknown) => Array.isArray(value) && value.every(item => typeof item === 'string')
function summary(value: unknown) { return object(value) && typeof value.request_id === 'string' && typeof value.title === 'string' && nullableString(value.client) && typeof value.current_revision_id === 'string' && typeof value.created_at === 'string' && typeof value.state === 'string' && typeof value.phase === 'string' }
function job(value: unknown) { return value === null || object(value) && typeof value.job_id === 'string' && typeof value.state === 'string' && typeof value.phase === 'string' && typeof value.stop_requested === 'boolean' && object(value.progress) && (value.error === null || object(value.error) && typeof value.error.message === 'string') }
function settingsSnapshot(value: unknown) {
  if (!object(value) || !nullableString(value.snapshot_id) || typeof value.requires_save !== 'boolean' || !object(value.settings)) return false
  const settings = value.settings
  return ['target_margin_percent', 'special_min_margin_percent', 'seller_contacts', 'offer_terms'].every(key => typeof settings[key] === 'string')
}
function line(value: unknown) { return object(value) && typeof value.line_id === 'string' && typeof value.name === 'string' && nullableString(value.quantity) && nullableString(value.unit) && Array.isArray(value.requirements) && value.requirements.every(item => object(item) && typeof item.name === 'string' && typeof item.value === 'string') && Array.isArray(value.evidence) && value.evidence.every(object) && (value.candidates === undefined || Array.isArray(value.candidates) && value.candidates.every(item => object(item) && typeof item.sku === 'string' && typeof item.name === 'string')) }
function validateRead(path: string, value: unknown) {
  if (!object(value)) throw new PresalesApiError('Сервер вернул неожиданный формат данных пресейла.')
  let valid = true
  if (valid && path === '/settings') valid = settingsSnapshot(value)
  else if (valid && path.startsWith('/requests?')) valid = Array.isArray(value.items) && value.items.every(summary)
  else if (valid && path.includes('/lines?')) valid = Array.isArray(value.items) && value.items.every(line)
  else if (valid && path.includes('/revisions/')) {
    valid = typeof value.revision_id === 'string' && typeof value.request_id === 'string' && typeof value.state === 'string' && typeof value.phase === 'string' && typeof value.revision_number === 'number'
      && Array.isArray(value.sources) && value.sources.every(source => object(source) && typeof source.artifact_id === 'string' && typeof source.filename === 'string' && typeof source.sha256 === 'string')
      && (value.extracted === null || object(value.extracted) && Array.isArray(value.extracted.lists))
      && (value.metrics === null || object(value.metrics) && nullableString(value.metrics.product_match_percent) && nullableString(value.metrics.stock_coverage_percent) && typeof value.metrics.product_match_incomplete === 'boolean' && typeof value.metrics.stock_coverage_incomplete === 'boolean')
      && (value.score === null || typeof value.score === 'number' && value.score >= 0 && value.score <= 100)
      && (value.review === null || object(value.review) && typeof value.review.text === 'string' && strings(value.review.issues)) && strings(value.warnings) && object(value.decisions) && object(value.artifacts) && job(value.job)
      && (value.offer === null || object(value.offer) && typeof value.offer.number === 'string' && typeof value.offer.date === 'string' && Array.isArray(value.offer.lines) && value.offer.lines.every(object) && object(value.offer.totals) && Array.isArray(value.offer.clarifications) && value.offer.clarifications.every(object) && Array.isArray(value.offer.unsupported) && value.offer.unsupported.every(object))
  } else if (valid) valid = summary(value.request) && Array.isArray(value.revisions) && value.revisions.every(item => object(item) && typeof item.revision_id === 'string' && typeof item.revision_number === 'number') && job(value.job)
  if (valid && path.includes('?cursor=')) {
    const cursor = Number(new URLSearchParams(path.split('?')[1]).get('cursor'))
    valid = value.next_cursor === null || typeof value.next_cursor === 'number' && Number.isSafeInteger(value.next_cursor) && value.next_cursor > cursor
  }
  if (!valid) throw new PresalesApiError('Сервер вернул неожиданный формат данных пресейла. Сохранённый результат не заменён.')
  return value
}
async function request(path: string, options: RequestInit = {}): Promise<unknown> {
  let response: Response
  try { response = await fetch(`/api/v0.1/presales${path}`, { ...options, headers: { 'X-Bervel-Web': '1', ...(options.body ? { 'Content-Type': 'application/json' } : {}) }, signal: AbortSignal.timeout(30_000) }) }
  catch { throw new PresalesApiError(options.method ? 'Исход команды неизвестен. Повторите сохранённую команду с тем же ключом.' : 'Не удалось обновить данные пресейла. Сохранённый результат остаётся доступен.') }
  const value = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = value?.error?.message
    const message = typeof detail === 'string' ? detail : response.status === 409 ? 'Данные изменились. Обновите заявку перед новой командой.' : 'Сервер пресейла не смог выполнить запрос.'
    throw new PresalesApiError(options.method && response.status >= 500 ? `Исход команды неизвестен. ${message}` : message, response.status >= 400 && response.status < 500)
  }
  if (!value || typeof value !== 'object') throw new PresalesApiError(options.method ? 'Исход команды неизвестен: сервер вернул неожиданный ответ.' : 'Сервер вернул неожиданный ответ пресейла.')
  return value
}
function read<T>(path: string): Promise<T> {
  const existing = reads.get(path)
  if (existing) return existing as Promise<T>
  const pending = request(path).then(value => validateRead(path, value)).finally(() => { reads.delete(path) })
  reads.set(path, pending)
  return pending as Promise<T>
}
export const readPresalesSettings = () => read<PresalesSettingsSnapshot>('/settings')
export const listPresalesRequests = (cursor = 0) => read<{ items: PresalesSummary[]; next_cursor: number | null }>(`/requests?cursor=${cursor}&limit=20`)
const requestPath = (id: string) => `/requests/${encodeURIComponent(id)}`
export const readPresalesRequest = (id: string) => read<PresalesRequest>(requestPath(id))
export const readPresalesRevision = (id: string, revision: string) => read<PresalesRevision>(`${requestPath(id)}/revisions/${encodeURIComponent(revision)}`)
export const listPresalesLines = (id: string, revision: string, cursor = 0) => read<{ items: PresalesLine[]; next_cursor: number | null }>(`${requestPath(id)}/revisions/${encodeURIComponent(revision)}/lines?cursor=${cursor}&limit=50`)
export const listPresalesSourceLines = (id: string, revision: string, listId: string, cursor = 0) => read<{ items: PresalesLine[]; next_cursor: number | null }>(`${requestPath(id)}/revisions/${encodeURIComponent(revision)}/lines?cursor=${cursor}&limit=50&list_id=${encodeURIComponent(listId)}`)

export function readPendingPresalesCommand(): PresalesCommand | null {
  const raw = localStorage.getItem(presalesPendingKey)
  if (!raw) return null
  const value: unknown = JSON.parse(raw)
  if (!value || typeof value !== 'object' || !('path' in value) || typeof value.path !== 'string' || !/^\/(settings|requests)(\/|$)/.test(value.path)
    || !('method' in value) || !['POST', 'PUT'].includes(String(value.method)) || !('body' in value) || !value.body || typeof value.body !== 'object' || !('command_key' in value.body) || typeof value.body.command_key !== 'string') throw new Error('Не удалось прочитать сохранённую команду. Не отправляйте новую, пока не проверено её состояние.')
  return value as PresalesCommand
}
export function preparePresalesCommand(path: string, body: Record<string, unknown>, method: 'POST' | 'PUT' = 'POST'): PresalesCommand {
  if (readPendingPresalesCommand()) throw new Error('Сначала проверьте исход сохранённой команды.')
  const command: PresalesCommand = { path, method, body: { ...body, command_key: crypto.randomUUID() } }
  // Persist before the first POST. A storage failure must never send an untracked command.
  localStorage.setItem(presalesPendingKey, JSON.stringify(command))
  return command
}
export async function executePresalesCommand<T = PresalesReceipt>(command: PresalesCommand): Promise<T> {
  const execute = async () => {
    const receipts: Record<string, T> = JSON.parse(localStorage.getItem(receiptsKey) || '{}')
    if (receipts[command.body.command_key]) return receipts[command.body.command_key]
    let result: unknown
    try { result = await request(command.path, { method: command.method, body: JSON.stringify(command.body) }) }
    catch (reason) {
      if (reason instanceof PresalesApiError && reason.rejected && readPendingPresalesCommand()?.body.command_key === command.body.command_key) localStorage.removeItem(presalesPendingKey)
      throw reason
    }
    if (command.path === '/settings') {
      if (!settingsSnapshot(result) || !object(result) || typeof result.snapshot_id !== 'string') throw new PresalesApiError('Исход команды неизвестен: квитанция настроек повреждена.')
    } else if (!result || typeof result !== 'object' || !('request_id' in result) || typeof result.request_id !== 'string' || !('revision_id' in result) || typeof result.revision_id !== 'string') throw new PresalesApiError('Исход команды неизвестен: квитанция заявки повреждена.')
    receipts[command.body.command_key] = result as T
    localStorage.setItem(receiptsKey, JSON.stringify(receipts))
    if (readPendingPresalesCommand()?.body.command_key === command.body.command_key) localStorage.removeItem(presalesPendingKey)
    return result as T
  }
  // Web Locks serializes retries in duplicated tabs. The persisted receipt is then reused.
  return navigator.locks ? navigator.locks.request(`bervel.presales.${command.body.command_key}`, execute) : execute()
}
