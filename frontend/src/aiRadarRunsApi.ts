import type {Card as LegacyCard, Job, Position} from './aiTenderRadarApi'
type ErrorInfo = {code?: string; message: string}
export type UnitPrices = {stock_cost: string | null; tender_price: string | null; currency: string | null; unit: string | null}
export type RunPosition = {id: string; name: string | null; required: string | null; covered: string | null; shortage: string | null; unit: string | null; unit_prices?: UnitPrices} & Partial<Omit<Position, 'id' | 'name' | 'required' | 'covered' | 'shortage' | 'unit'>>
type RunAnalysis = {coverage_percent: string | null; benefit_percent: string | null;
  economics: {scope: string | null; revenue: string | null; cost: string | null} & Partial<Omit<NonNullable<Job['analysis']>['economics'], 'scope' | 'revenue' | 'cost'>>;
  positions: RunPosition[]} & Partial<Omit<NonNullable<Job['analysis']>, 'economics' | 'positions'>>
export type ReviewDecision = 'participate' | 'review' | 'decline'
export type CardReview = {state: 'complete' | 'failed'; recommendation: string; answer?: string; risks: string[]; assumptions: string[]; evidence_refs: string[]; error?: ErrorInfo; decision?: ReviewDecision; manual_checks?: string[]; prompt_version?: string}
export const purchaseStageLabels = {open: 'Подача заявок', commission: 'Работа комиссии', finished: 'Завершены',
  planned: 'Планируются', cancelled: 'Отменены'} as const
export type PurchaseStage = keyof typeof purchaseStageLabels
export type TenderStage = 'accepting' | 'closed' | 'commission' | 'finished' | 'cancelled' | 'abandoned' | 'planned' | 'unknown'
export type TenderMetrics = {
  actuality?: 'active' | 'inactive'; inactivity_reason?: string | null;
  trade_type?: 'fz44' | 'fz223' | 'pp615' | 'commercial' | 'small' | 'commercial_small' | 'cis' | 'unknown';
  price: string | null; currency: string | null; stage: TenderStage; catalog_match_percent: string | null
  catalog_match_confirmed?: number; catalog_match_unknown?: number; catalog_match_total?: number; catalog_match_incomplete?: boolean
  stock_coverage_percent?: string | null; stock_coverage_incomplete?: boolean
}
export type RatingStatus = 'pending' | 'running' | 'complete' | 'failed' | 'unknown' | 'stale'
export type ComparativeRating = {status: RatingStatus; score: number | null; prompt_version?: string; previous_score?: number}
export type RatingResult = {status: 'running' | 'complete' | 'failed' | 'unknown'; prompt_version: string;
  fingerprint: string; cohort_size: number; request_id: string; started_at: string; model: string | null;
  ratings: {id: string; score: number}[]; completed_requests: string[]; pending_request_id: string | null; error?: ErrorInfo}
export type RefreshWarning = {stage: 'primary' | 'detail' | 'analysis' | 'rating' | 'metrics'; code: string; message: string}
export type CompletionSummary = {version: 'profile-completion-v1'; failed_tenders: number; primary_failed: number;
  detail_failed: number; analysis_failed: number; rating_failed: number; metrics_failed: number}
export type RunCard = {analysis_id: string; candidate_id: string; state: 'primary' | 'analyzing' | 'accepted' | 'needs_data' | 'irrelevant' | 'failed'; evaluated_at?: string | null; last_seen_at?: string | null;
  information_state?: 'primary' | 'full'; details_loaded?: boolean; priority_score?: number | null; priority_reason?: string | null;
  refresh_warnings?: RefreshWarning[];
  rating?: ComparativeRating;
  opportunity?: {score: number; basis: 'full' | 'confirmed' | 'unknown'; known_share: string | null; margin_percent: string | null};
  projection?: {version: number; review_stale: boolean; metrics_review_stale?: boolean};
  report_summary?: {decision: ReviewDecision; paragraphs: string[]};
  brief?: Record<string, unknown>;
  metrics?: TenderMetrics;
  tender: {title: string; number: string | null; link: string | null; application_deadline?: string | null;
    documents?: {filename?: string | null; url: string; text?: string | null}[];
    limitations?: NonNullable<Job['tender']>['limitations']} | null;
  completeness?: {requirements: string; economics: string; schedule: string; reasons: string[]; complete?: boolean};
  analysis: RunAnalysis | null;
  fulfillment: {status: string; reasons: string[]; production_allocations: {sku: string | null; quantity: string; ready_at?: string; resource_group_id: string}[]} | null;
  assessment: {score: number | null; stress_score?: number | null; priority: string; reasons: string[]; E: string | null; S: string | null; P: string | null; full_economics: {profit: string | null; margin_percent: string | null; currency: string | null} | null} | null;
  review: CardReview | null; error?: ErrorInfo | null}
export type AuditResult = {status: 'running' | 'complete' | 'partial'; checked: number; total: number; findings: {reason: string; stage: string; severity: string; analysis_id?: string | null}[]; reference_comparison?: {provided: boolean; checked: number; agreements: number; disagreements: number; note?: string}; limitations?: string[]}
export type QuestionReport = {job_id: string; kind: 'question'; analysis_id: string; question: string; result: CardReview | null; state?: JobState; error?: ErrorInfo; command_key?: string}
export type RunReport = QuestionReport | {job_id: string; kind: 'review'; analysis_id: string; result: CardReview} | {job_id: string; kind: 'audit'; result: AuditResult} | {job_id: string; kind: 'rating'; result: RatingResult}
export type Candidate = {id: string; brief: Record<string, unknown>; state: string; reasons: string[]; metrics?: TenderMetrics}
export type Source = 'test_api' | 'kontur_api' | 'file'
export type HistoryEntry = {id: string; kind: 'legacy' | 'run'; title: string; created_at: string | null; state: string; card_count: number; stop_requested?: boolean; stop_reason?: 'USER_STOPPED'; progress?: number}
export type Run = {run_id: string; settings_snapshot_id: string; created_at: string; data: {
  name?: string; source?: Source; search_scope?: {from: string; to: string; source: string}; unverified_count?: number; scope_complete?: boolean;
  purchase_stages?: PurchaseStage[];
  state: 'queued' | 'running' | 'complete' | 'failed'; phase: string; processed?: number; detail_attempts?: number; model_attempts?: number; stop_requested?: boolean;
  effective_budgets?: {detail: number | null}; pending_detail?: string | null;
  checkpoint?: {search_attempts?: number; candidates?: {id: string}[]; stop_reason?: string; truncated?: boolean;
    ranking_completed?: number; ranking_total?: number; analysis_completed?: number; analysis_total?: number; metrics_completed?: number; metrics_total?: number};
  counts?: Record<string, number>; stop_reason?: string; error?: ErrorInfo; resume_kind?: 'individual_rating' | 'metrics';
  completion_summary?: CompletionSummary;
  metrics_job?: {job_id: string; state: JobState; error?: ErrorInfo | null}}; cards?: RunCard[]; reports?: RunReport[]; candidates?: Candidate[]}
export type RunAttempt = {path: string; body: Record<string, unknown> & {command_key: string}}
export type CommandLane = 'analysis' | 'question'
export type RunAttempts = Partial<Record<CommandLane, RunAttempt>>
export const commandLane = (command: RunAttempt): CommandLane => command.path.endsWith('/question') ? 'question' : 'analysis'
export type JobState = 'waiting' | 'queued' | 'running' | 'loaded' | 'no_match' | 'failed'
export type Receipt = {job_id: string; state: JobState; run_id?: string}
export type Operation = {job_id: string; run_id: string; kind: 'question' | 'audit' | 'resume' | 'rating'; state: JobState; analysis_id?: string; question?: string; error?: ErrorInfo}
export class RunError extends Error {
  readonly rejected: boolean
  constructor(message: string, rejected = false) {super(message);this.rejected = rejected}
}
const root = '/api/v0.1/ai-tender-radar'
const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v)
const text = (v: unknown): v is string => typeof v === 'string'
const id = (v: unknown): v is string => text(v) && v.length > 0
const uuid = (v: unknown): v is string => text(v) && /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(v)
const nullable = (v: unknown) => v === null || text(v)
const numberText = (v: unknown) => v === null || (text(v) && /^-?\d+(?:\.\d+)?$/.test(v))
const integer = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(text)
const timestamp = (v: unknown) => text(v) && Number.isFinite(Date.parse(v)) && /(?:Z|[+-]\d\d:\d\d)$/.test(v)
const errorInfo = (v: unknown) => object(v) && text(v.message) && (v.code === undefined || text(v.code))
const jobState = (v: unknown): v is JobState => ['waiting', 'queued', 'running', 'loaded', 'no_match', 'failed'].includes(String(v))
const optionalInt = (v: unknown) => v === undefined || integer(v)
const purchaseStages = (v: unknown): v is PurchaseStage[] => strings(v) && v.length > 0 &&
  v.length <= Object.keys(purchaseStageLabels).length && v.every(stage => Object.hasOwn(purchaseStageLabels, stage))
const score = (v: unknown) => v === null || (integer(v) && v <= 100)
function invalid(): never {throw new RunError('Сервер вернул неожиданный формат данных прогона. Проверьте соединение и повторите чтение.')}
function review(v: unknown): v is CardReview {
  return object(v) && ['complete', 'failed'].includes(String(v.state)) && text(v.recommendation) && strings(v.risks) && strings(v.assumptions) && strings(v.evidence_refs) && (v.answer === undefined || text(v.answer)) && (v.error === undefined || errorInfo(v.error))
    && (v.decision === undefined || v.decision === 'participate' || v.decision === 'review' || v.decision === 'decline')
    && (v.manual_checks === undefined || (strings(v.manual_checks) && v.manual_checks.length <= 2))
}
function audit(v: unknown): v is AuditResult {
  if (!object(v) || !['running', 'complete', 'partial'].includes(String(v.status)) || !integer(v.checked) || !integer(v.total) || v.checked > v.total || !Array.isArray(v.findings) || !v.findings.every(f => object(f) && text(f.reason) && text(f.stage) && text(f.severity) && (f.analysis_id === undefined || nullable(f.analysis_id))) || (v.limitations !== undefined && !strings(v.limitations))) return false
  const r = v.reference_comparison
  return r === undefined || (object(r) && typeof r.provided === 'boolean' && integer(r.checked) && integer(r.agreements) && integer(r.disagreements) && (r.note === undefined || text(r.note)))
}
function report(v: unknown): v is RunReport {
  if (!object(v) || !uuid(v.job_id)) return false
  if (v.kind === 'rating') {
    const r = v.result
    return object(r) && ['running', 'complete', 'failed', 'unknown'].includes(String(r.status)) &&
      id(r.prompt_version) && text(r.fingerprint) && /^[a-f\d]{64}$/.test(r.fingerprint) && integer(r.cohort_size) &&
      id(r.request_id) && timestamp(r.started_at) && nullable(r.model) && nullable(r.pending_request_id) &&
      strings(r.completed_requests) && (r.error === undefined || errorInfo(r.error)) &&
      Array.isArray(r.ratings) && r.ratings.every(row => object(row) && id(row.id) && integer(row.score) && row.score <= 100) &&
      new Set(r.ratings.map(row => (row as Record<string, unknown>).id)).size === r.ratings.length &&
      (r.status !== 'complete' || r.ratings.length === r.cohort_size)
  }
  if (v.kind === 'question') return id(v.analysis_id) && text(v.question) &&
    (v.state === undefined || jobState(v.state)) && (v.error === undefined || errorInfo(v.error)) &&
    (v.command_key === undefined || id(v.command_key)) && (v.result === null ? jobState(v.state) : review(v.result))
  return v.kind === 'audit' ? audit(v.result) : v.kind === 'review' && id(v.analysis_id) && review(v.result)
}
function economicDetails(v: Record<string, unknown>) {
  return ['profit', 'margin_percent'].every(key => v[key] === undefined || numberText(v[key])) &&
    (v.currency === undefined || nullable(v.currency)) && (v.reason === undefined || nullable(v.reason)) &&
    (v.status === undefined || ['profitable', 'loss', 'break_even', 'unknown'].includes(String(v.status)))
}
function positionDetails(p: Record<string, unknown>) {
  return (p.unit_prices === undefined || (object(p.unit_prices) && numberText(p.unit_prices.stock_cost) && numberText(p.unit_prices.tender_price) && nullable(p.unit_prices.currency) && nullable(p.unit_prices.unit))) &&
    (p.lot === undefined || integer(p.lot)) && (p.customer === undefined || p.customer === null || integer(p.customer)) &&
    (p.stock_status === undefined || ['full', 'partial', 'none', 'unconfirmed'].includes(String(p.stock_status))) &&
    (p.stock_reason === undefined || nullable(p.stock_reason)) &&
    (p.mass_conversion === undefined || (object(p.mass_conversion) && text(p.mass_conversion.weight_per_piece_kg) && numberText(p.mass_conversion.weight_per_piece_kg) && text(p.mass_conversion.required_pieces) && numberText(p.mass_conversion.required_pieces))) &&
    (p.economics === undefined || (object(p.economics) && economicDetails(p.economics) &&
      ['revenue', 'cost', 'profit', 'margin_percent'].every(key => numberText(p.economics && (p.economics as Record<string, unknown>)[key])) && nullable(p.economics.currency) && nullable(p.economics.reason) && ['profitable', 'loss', 'break_even', 'unknown'].includes(String(p.economics.status)))) &&
    (p.matches === undefined || (Array.isArray(p.matches) && p.matches.every(m => object(m) && id(m.sku) && integer(m.stock_row) && text(m.stock_unit) && numberText(m.stock_free_quantity) && Array.isArray(m.evidence) && m.evidence.every(e => object(e) && [e.requirement, e.tender_value, e.stock_field, e.stock_value].every(text))))) &&
    (p.allocations === undefined || (Array.isArray(p.allocations) && p.allocations.every(a => object(a) && id(a.sku) && text(a.stock_quantity) && numberText(a.stock_quantity) && text(a.stock_unit) && numberText(a.stock_unit_cost) && nullable(a.stock_currency) && text(a.position_quantity) && numberText(a.position_quantity))))
}
function tenderMetrics(v: unknown): v is TenderMetrics {
  if (!object(v)) return false
  if (v.actuality !== undefined && !['active', 'inactive'].includes(String(v.actuality))) return false
  if (v.trade_type !== undefined && !['fz44','fz223','pp615','commercial','small','commercial_small','cis','unknown'].includes(String(v.trade_type))) return false
  const {catalog_match_confirmed: confirmed, catalog_match_unknown: unknown,
    catalog_match_total: total, catalog_match_incomplete: incomplete} = v
  if (![confirmed, unknown, total, incomplete].every(value => value === undefined)
    && !(integer(confirmed) && integer(unknown) && integer(total) && confirmed + unknown <= total
      && typeof incomplete === 'boolean')) return false
  return object(v) && numberText(v.price) && nullable(v.currency) && numberText(v.catalog_match_percent)
    && (v.stock_coverage_percent === undefined || numberText(v.stock_coverage_percent))
    && (v.stock_coverage_incomplete === undefined || typeof v.stock_coverage_incomplete === 'boolean')
    && ['accepting', 'closed', 'commission', 'finished', 'cancelled', 'abandoned', 'planned', 'unknown'].includes(String(v.stage))
}
function card(v: unknown): v is RunCard {
  if (!object(v) || !id(v.analysis_id) || !id(v.candidate_id) || !['primary', 'analyzing', 'accepted', 'needs_data', 'irrelevant', 'failed'].includes(String(v.state)) || (v.error !== undefined && v.error !== null && !errorInfo(v.error))) return false
  if (v.refresh_warnings !== undefined && (!Array.isArray(v.refresh_warnings) || !v.refresh_warnings.every(w => object(w)
    && text(w.stage) && ['primary', 'detail', 'analysis', 'rating', 'metrics'].includes(w.stage) && text(w.code) && text(w.message)))) return false
  if (v.evaluated_at !== undefined && v.evaluated_at !== null && !timestamp(v.evaluated_at)) return false
  if (v.last_seen_at !== undefined && v.last_seen_at !== null && !timestamp(v.last_seen_at)) return false
  if ((v.information_state !== undefined && !['primary','full'].includes(String(v.information_state))) || (v.details_loaded !== undefined && typeof v.details_loaded !== 'boolean') || (v.priority_score !== undefined && !score(v.priority_score)) || (v.priority_reason !== undefined && !nullable(v.priority_reason))) return false
  const {tender: t, completeness: c, analysis: a, fulfillment: f, assessment: s} = v
  const m = v.metrics
  if (v.rating !== undefined && (!object(v.rating) || (v.rating.prompt_version !== undefined && !text(v.rating.prompt_version)) || !['pending', 'running', 'complete', 'failed', 'unknown', 'stale'].includes(String(v.rating.status)) ||
    (v.rating.previous_score !== undefined && (v.rating.status !== 'stale' || !integer(v.rating.previous_score) || v.rating.previous_score > 100)) ||
    (v.rating.status === 'complete' ? !(integer(v.rating.score) && v.rating.score <= 100) : v.rating.score !== null))) return false
  if (v.opportunity !== undefined && (!object(v.opportunity) || !integer(v.opportunity.score) || v.opportunity.score > 100 || !['full', 'confirmed', 'unknown'].includes(String(v.opportunity.basis)) || !numberText(v.opportunity.known_share) || !numberText(v.opportunity.margin_percent))) return false
  if (v.projection !== undefined && (!object(v.projection) || !integer(v.projection.version) || typeof v.projection.review_stale !== 'boolean' || (v.projection.metrics_review_stale !== undefined && typeof v.projection.metrics_review_stale !== 'boolean'))) return false
  if (v.report_summary !== undefined && (!object(v.report_summary) || !['participate', 'review', 'decline'].includes(String(v.report_summary.decision)) || !strings(v.report_summary.paragraphs))) return false
  if (m !== undefined && !tenderMetrics(m)) return false
  if (t !== null && (!object(t) || !text(t.title) || !nullable(t.number) || !nullable(t.link))) return false
  if (object(t) && t.documents !== undefined && (!Array.isArray(t.documents) || !t.documents.every(d => object(d) && text(d.url) && (d.filename === undefined || nullable(d.filename))))) return false
  if (object(t) && t.limitations !== undefined && (!Array.isArray(t.limitations) || !t.limitations.every(l => object(l) && text(l.code) && (l.lot === undefined || integer(l.lot)) && (l.position_id === undefined || text(l.position_id))))) return false
  if (c !== undefined && (!object(c) || !text(c.requirements) || !text(c.economics) || !text(c.schedule) || !strings(c.reasons) || (c.complete !== undefined && typeof c.complete !== 'boolean'))) return false
  if (a !== null && (!object(a) || !numberText(a.coverage_percent) || !numberText(a.benefit_percent) || !object(a.economics) || !nullable(a.economics.scope) || !numberText(a.economics.revenue) || !numberText(a.economics.cost) || !Array.isArray(a.positions) || !a.positions.every(p => object(p) && id(p.id) && nullable(p.name) && nullable(p.unit) && numberText(p.required) && numberText(p.covered) && numberText(p.shortage)))) return false
  if (object(a) && (!object(a.economics) || !economicDetails(a.economics) ||
    (a.model !== undefined && !nullable(a.model)) || (a.model_error !== undefined && a.model_error !== null && !errorInfo(a.model_error)) ||
    (a.coverage_reason !== undefined && !nullable(a.coverage_reason)) || (a.benefit_clamped !== undefined && typeof a.benefit_clamped !== 'boolean') ||
    !Array.isArray(a.positions) || !a.positions.every(p => object(p) && positionDetails(p)))) return false
  if (f !== null && (!object(f) || !text(f.status) || !strings(f.reasons) || !Array.isArray(f.production_allocations) || !f.production_allocations.every(p => object(p) && nullable(p.sku) && text(p.quantity) && numberText(p.quantity) && id(p.resource_group_id) && (p.ready_at === undefined || timestamp(p.ready_at))))) return false
  if (s !== null && (!object(s) || !score(s.score) || (s.stress_score !== undefined && !score(s.stress_score)) || !text(s.priority) || !strings(s.reasons) || ![s.E, s.S, s.P].every(numberText) || (s.full_economics !== null && (!object(s.full_economics) || !numberText(s.full_economics.profit) || !numberText(s.full_economics.margin_percent) || !nullable(s.full_economics.currency))))) return false
  return v.review === null || review(v.review)
}
export function run(v: unknown, detail = false): Run {
  if (!object(v) || !uuid(v.run_id) || !uuid(v.settings_snapshot_id) || !timestamp(v.created_at) || !object(v.data)) return invalid()
  const d = v.data, c = d.checkpoint
  if (d.completion_summary !== undefined && (!object(d.completion_summary) || d.completion_summary.version !== 'profile-completion-v1'
    || ![d.completion_summary.failed_tenders, d.completion_summary.primary_failed, d.completion_summary.detail_failed,
      d.completion_summary.analysis_failed, d.completion_summary.rating_failed, d.completion_summary.metrics_failed].every(integer))) return invalid()
  if (d.metrics_job !== undefined && (!object(d.metrics_job) || !uuid(d.metrics_job.job_id) || !jobState(d.metrics_job.state)
    || (d.metrics_job.error !== undefined && d.metrics_job.error !== null && !errorInfo(d.metrics_job.error)))) return invalid()
  if (d.resume_kind !== undefined && d.resume_kind !== 'individual_rating' && d.resume_kind !== 'metrics') return invalid()
  if (object(c) && ![c.ranking_completed, c.ranking_total, c.analysis_completed, c.analysis_total, c.metrics_completed, c.metrics_total].every(optionalInt)) return invalid()
  if (d.purchase_stages !== undefined && !purchaseStages(d.purchase_stages)) return invalid()
  if (!optionalInt(d.unverified_count) || (d.stop_requested !== undefined && typeof d.stop_requested !== 'boolean') || (d.scope_complete !== undefined && typeof d.scope_complete !== 'boolean') || (d.search_scope !== undefined && (!object(d.search_scope) || !text(d.search_scope.from) || !text(d.search_scope.to) || !text(d.search_scope.source)))) return invalid()
  if ((d.name !== undefined && !text(d.name)) || (d.source !== undefined && !['test_api', 'kontur_api', 'file'].includes(String(d.source)))) return invalid()
  if (!['queued', 'running', 'complete', 'failed'].includes(String(d.state)) || !text(d.phase) || ![d.processed, d.detail_attempts, d.model_attempts].every(optionalInt) || (d.error !== undefined && d.error !== null && !errorInfo(d.error)) || (d.stop_reason !== undefined && !text(d.stop_reason)) || (d.counts !== undefined && (!object(d.counts) || !Object.values(d.counts).every(integer))) || (c !== undefined && (!object(c) || !optionalInt(c.search_attempts) || (c.candidates !== undefined && (!Array.isArray(c.candidates) || !c.candidates.every(v => object(v) && id(v.id)))) || (c.stop_reason !== undefined && !text(c.stop_reason)) || (c.truncated !== undefined && typeof c.truncated !== 'boolean')))) return invalid()
  if ((detail || v.cards !== undefined) && (!Array.isArray(v.cards) || !v.cards.every(card))) return invalid()
  if ((detail || v.reports !== undefined) && (!Array.isArray(v.reports) || !v.reports.every(report))) return invalid()
  if (v.candidates !== undefined && (!Array.isArray(v.candidates) || !v.candidates.every(c => object(c) && id(c.id) && object(c.brief)
    && text(c.state) && strings(c.reasons) && (c.metrics === undefined || tenderMetrics(c.metrics))))) return invalid()
  return v as Run
}
async function request(path: string, body?: unknown, noContentMethod?: 'DELETE' | 'POST'): Promise<unknown> {
  let response: Response
  try {response = await fetch(root + path, {method: noContentMethod ?? (body === undefined ? 'GET' : 'POST'), headers: {'X-Bervel-Web': '1', ...(body === undefined ? {} : {'Content-Type': 'application/json'})}, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(30_000)})}
  catch {throw new RunError(noContentMethod ? 'Ответ сервера не получен. Повторите запрос.' : 'Ответ сервера не получен. Для отправленной команды исход может быть неизвестен; повторите её с прежним ключом.')}
  if (noContentMethod && response.status === 204) return null
  const value: unknown = await response.json().catch(() => null)
  if (!response.ok) throw new RunError(object(value) && object(value.error) && text(value.error.message) ? value.error.message : 'Не удалось выполнить операцию прогона.', response.status >= 400 && response.status < 500)
  if (noContentMethod) return invalid()
  return value
}
export async function listRuns(offset = 0): Promise<{runs: Run[]; next_offset: number | null}> {
  const v = await request(`/runs${offset ? `?offset=${offset}` : ''}`)
  if (!object(v) || !Array.isArray(v.runs) || (v.next_offset !== null && (!integer(v.next_offset) || v.next_offset <= offset))) return invalid()
  return {runs: v.runs.map(v => run(v)), next_offset: v.next_offset as number | null}
}
export async function readRun(runId: string): Promise<Run> {const value = run(await request(`/runs/${encodeURIComponent(runId)}`), true);if (value.run_id !== runId) return invalid();return value}
export async function deleteRun(runId: string): Promise<void> {await request(`/runs/${encodeURIComponent(runId)}`, undefined, 'DELETE')}
export async function stopRun(runId: string): Promise<void> {await request(`/runs/${encodeURIComponent(runId)}/stop`, undefined, 'POST')}
export async function submitRunCommand(command: RunAttempt): Promise<Receipt> {
  const v = await request('/runs' + command.path, command.body)
  if (!object(v) || !uuid(v.job_id) || !jobState(v.state) || (v.run_id !== undefined && !uuid(v.run_id)) || ((command.path === '' || command.path.endsWith('/reanalyze')) && !uuid(v.run_id))) return invalid()
  return v as Receipt
}
export async function readOperationJob(jobId: string): Promise<{job_id: string; state: JobState; error?: ErrorInfo | null}> {
  const v = await request(`/jobs/${encodeURIComponent(jobId)}`)
  if (!object(v) || v.job_id !== jobId || !jobState(v.state) || (v.error !== null && v.error !== undefined && !errorInfo(v.error))) return invalid()
  return v as {job_id: string; state: JobState; error?: ErrorInfo | null}
}
export function readRunAttempt(v: unknown): RunAttempt {
  if (!object(v) || !text(v.path) || !object(v.body) || !id(v.body.command_key) || v.body.command_key.length > 128) return invalid()
  const b = v.body
  if (b.purchase_stages !== undefined && (v.path !== '' || (b.source !== 'test_api' && b.source !== 'kontur_api') || !purchaseStages(b.purchase_stages))) return invalid()
  if (v.path === '') {if (!uuid(b.settings_snapshot_id)) return invalid(); if (b.name !== undefined || b.source !== undefined) {if (!id(b.name) || !b.name.trim() || b.name.length > 200 || !['test_api', 'kontur_api', 'file'].includes(String(b.source)) || (b.source === 'file' && !id(b.source_artifact_id)) || (b.detail_max !== undefined && (!integer(b.detail_max) || b.detail_max < 1))) return invalid()} else if (!integer(b.search_max) || b.search_max < 1 || !integer(b.detail_max) || b.detail_max < 1) return invalid()}
  else {if (!/^\/[\da-f-]{36}\/(?:question|audit|resume|reanalyze|rating)$/i.test(v.path)) return invalid();if (v.path.endsWith('/question') && (!id(b.analysis_id) || !id(b.question) || b.question.length > 2000)) return invalid();if (v.path.endsWith('/audit') && b.reference !== null && !object(b.reference)) return invalid();if (v.path.endsWith('/reanalyze') && (!id(b.analysis_id) || !uuid(b.settings_snapshot_id))) return invalid()}
  return v as RunAttempt
}
export function readOperations(v: unknown): Operation[] {
  if (!Array.isArray(v) || !v.every(o => object(o) && uuid(o.job_id) && uuid(o.run_id) && ['question', 'audit', 'resume', 'rating'].includes(String(o.kind)) && jobState(o.state) && (o.question === undefined || text(o.question)) && (o.analysis_id === undefined || id(o.analysis_id)) && (o.error === undefined || errorInfo(o.error)))) return invalid()
  return v as Operation[]
}

export function readRunAttempts(v: unknown): RunAttempts {
  if (!object(v) || Object.keys(v).some(key => key !== 'analysis' && key !== 'question')) return invalid()
  const result: RunAttempts = {}
  for (const lane of ['analysis', 'question'] as const) {
    if (v[lane] === undefined) continue
    const command = readRunAttempt(v[lane])
    if (commandLane(command) !== lane) return invalid()
    result[lane] = command
  }
  return result
}

function historyEntry(v: unknown): v is HistoryEntry {return object(v) && id(v.id) && ['legacy', 'run'].includes(String(v.kind)) && text(v.title) && (v.created_at === null || timestamp(v.created_at)) && text(v.state) && integer(v.card_count) && (v.stop_requested === undefined || typeof v.stop_requested === 'boolean') && (v.stop_reason === undefined || v.stop_reason === 'USER_STOPPED') && (v.kind === 'legacy' ? v.id === 'legacy' : uuid(v.id))}
export async function listRunHistory(offset = 0): Promise<{entries: HistoryEntry[]; next_offset: number | null}> {
 const v = await request('/runs/history?limit=20&offset=' + offset)
 if (!object(v) || !Array.isArray(v.entries) || !v.entries.every(historyEntry) || (v.next_offset !== null && (!integer(v.next_offset) || v.next_offset <= offset))) return invalid()
 return v as {entries: HistoryEntry[]; next_offset: number | null}
}
export async function readLegacyCards(offset = 0): Promise<{entry: HistoryEntry; cards: LegacyCard[]; next_offset: number | null}> {
 const v = await request('/runs/legacy?limit=50&offset=' + offset)
 if (!object(v) || !historyEntry(v.entry) || v.entry.kind !== 'legacy' || !Array.isArray(v.cards) || !v.cards.every(c => object(c) && id(c.job_id) && object(c.tender) && text(c.tender.title) && text(c.status) && numberText(c.coverage_percent) && numberText(c.benefit_percent)) || (v.next_offset !== null && (!integer(v.next_offset) || v.next_offset <= offset))) return invalid()
 return v as {entry: HistoryEntry; cards: LegacyCard[]; next_offset: number | null}
}
