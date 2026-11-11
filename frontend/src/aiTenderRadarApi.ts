export type JobState = 'waiting' | 'queued' | 'running' | 'loaded' | 'no_match' | 'failed'
type AnalysisStatus = 'complete' | 'insufficient_data' | 'partial'
type ErrorInfo = {code: string; message: string}
type Tender = {id: string; title: string; number: string | null; link: string | null; position_count: number;
  limitations: {code: string; lot?: number; position_id?: string}[]}
type Economics = {revenue: string | null; cost: string | null; currency: string | null; profit: string | null;
  margin_percent: string | null; status: 'profitable' | 'loss' | 'break_even' | 'unknown'; reason: string | null;
  scope?: 'full' | 'confirmed' | null}
export type Position = {id: string; lot: number; customer: number | null; name: string | null; unit: string | null;
  required: string | null; covered: string; shortage: string | null;
  mass_conversion?: {weight_per_piece_kg: string; required_pieces: string};
  stock_status: 'full' | 'partial' | 'none' | 'unconfirmed'; stock_reason: string | null; economics: Economics;
  matches: {sku: string; stock_row: number; stock_unit: string; stock_free_quantity: string | null;
    evidence: {requirement: string; tender_value: string; stock_field: string; stock_value: string}[]}[];
  allocations: {sku: string; stock_quantity: string; stock_unit: string; stock_unit_cost: string | null;
    stock_currency: string | null; position_quantity: string}[]}
export type Job = {job_id: string; batch_id: string | null; stock_artifact_id: string; state: JobState; tender: Tender | null; error: ErrorInfo | null;
  stock: {filename: string; sha256: string; row_count: number} | null;
  analysis: {status: AnalysisStatus; coverage_reason: string | null; benefit_clamped: boolean; model_error: ErrorInfo | null;
    model: string | null; positions: Position[]; economics: Economics} | null}
export type Card = {job_id: string; stock_artifact_id: string; updated_at: string; tender: Tender;
  status: AnalysisStatus; coverage_percent: string | null; position_coverage_percent: string | null;
  benefit_percent: string | null; benefit_reason: string | null; benefit_scope?: 'full' | 'confirmed' | null; mock: boolean}
type CardPage = {cards: Card[]; next_offset: number | null}
export type CardSort = 'coverage' | 'benefit'
type StockCommand = {stock_artifact_id: string; command_key: string}
export type Attempt = {cardId: null; body: StockCommand & ({search_phrase: string} | {source_artifact_id: string})} |
  {cardId: string; body: StockCommand}

export class AiRadarError extends Error {
  readonly status?: number
  readonly code?: string
  constructor(message: string, status?: number, code?: string) {
    super(message)
    this.status = status
    this.code = code
  }
  get rejected() { return this.status !== undefined && this.status >= 400 && this.status < 500 }
}

async function request<T>(path: string, body?: StockCommand): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api/v0.1/ai-tender-radar${path}`, {
      method: body ? 'POST' : 'GET', headers: {'X-Bervel-Web': '1', ...(body ? {'Content-Type': 'application/json'} : {})},
      body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(30_000),
    })
  } catch {
    throw new AiRadarError('Не удалось получить ответ от сервера.')
  }
  if (!response.ok) {
    const data = await response.json().catch(() => null) as {error?: ErrorInfo} | null
    throw new AiRadarError(data?.error?.message || 'Тендерный радар ИИ сейчас недоступен.', response.status, data?.error?.code)
  }
  return await response.json() as T
}

export const listAiTenderCards = (offset = 0, sort?: CardSort) =>
  request<CardPage>(`/cards?offset=${offset}${sort ? `&sort=${sort}` : ''}`)
export const readAiTenderJob = (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}`)
export const activeAiTenderJob = () => request<Job | null>('/jobs/active')
export const startAiTenderJob = (attempt: Attempt) => request<{job_id: string; state: JobState}>(
  attempt.cardId ? `/cards/${encodeURIComponent(attempt.cardId)}/reanalyze` :
    'source_artifact_id' in attempt.body ? '/jobs/from-file' : '/jobs', attempt.body)
