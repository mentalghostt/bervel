export type Product = {id: string; sku: string | null; name: string; unit: string; attributes: Record<string, string>; enabled: boolean; manufactured_before?: boolean | null; evidence?: string}
export type ExperimentalProduct = {id: string; sku?: string | null; name: string; unit: string; attributes: Record<string, string>; enabled: boolean; product_id?: string | null}
export type Capability = {product_id: string; resource_group_id: string; units_per_workday: string | null; production_unit_cost: string | null; setup_cost?: string | null; currency: string | null; setup_workdays: string; material_ready_at: string | null; confirmed_at?: string | null; evidence?: string}
export type Resource = {group_id: string; calendar: number[]; enabled?: boolean; free_capacity_by_day?: Record<string, string>; confirmed_at?: string | null; default_daily_capacity?: string | null}
export type Profile = {stock_artifact_id: string; stock_sha256: string; products: Product[]; experimental_products: ExperimentalProduct[]; include_experimental: boolean; production: Capability[]; resources: Resource[]; scoring_policy: Record<string, unknown>}
export type ScoringPolicy = {version: string; target_margin: string; comfortable_utilization: string; minimum_production_factor: string; high_score: number; medium_score: number}
export type Snapshot = {id: string; created_at: string; profile: Profile; effective_scoring_policy?: ScoringPolicy}
export type SaveCommand = {command_key: string; base_snapshot_id: string | null; profile: Profile}

const unexpected = 'Сервер вернул неожиданный формат настроек предприятия.'
const object = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const string = (v: unknown): v is string => typeof v === 'string'
const nullable = (v: unknown): v is string | null => v === null || string(v)
const decimal = (v: unknown): v is string => string(v) && /^\d+(?:\.\d+)?$/.test(v)
const optionalDecimal = (v: unknown) => v === null || decimal(v)
const timestamp = (v: unknown) => v === null || (string(v) && Number.isFinite(Date.parse(v)) && /(?:Z|[+-]\d\d:\d\d)$/.test(v))
const optionalTimestamp = (v: unknown) => v === undefined || timestamp(v)
const dictionary = (v: unknown): v is Record<string, string> => object(v) && Object.values(v).every(string)
const evidence = (v: Record<string, unknown>) => v.evidence === undefined || string(v.evidence)
const identifier = (v: unknown): v is string => string(v) && v.length > 0
const score = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v) && v >= 0 && v <= 100
function readScoringPolicy(v: unknown): ScoringPolicy {
  if (!object(v) || !identifier(v.version) || !decimal(v.target_margin) || !decimal(v.comfortable_utilization)
    || !decimal(v.minimum_production_factor) || !score(v.high_score) || !score(v.medium_score)) throw new Error(unexpected)
  return v as ScoringPolicy
}
export function readCompanyProfile(v: unknown): Profile {
  if (!object(v) || !identifier(v.stock_artifact_id) || !string(v.stock_sha256) || !/^[a-f\d]{64}$/i.test(v.stock_sha256)
    || typeof v.include_experimental !== 'boolean' || !object(v.scoring_policy)
    || !Array.isArray(v.products) || !v.products.every(p => object(p) && identifier(p.id) && nullable(p.sku) && string(p.name) && string(p.unit) && dictionary(p.attributes) && typeof p.enabled === 'boolean' && (p.manufactured_before === undefined || p.manufactured_before === null || typeof p.manufactured_before === 'boolean') && evidence(p))
    || !Array.isArray(v.experimental_products) || !v.experimental_products.every(p => object(p) && identifier(p.id) && (p.sku === undefined || nullable(p.sku)) && string(p.name) && string(p.unit) && dictionary(p.attributes) && typeof p.enabled === 'boolean' && (p.product_id === undefined || nullable(p.product_id)))
    || !Array.isArray(v.production) || !v.production.every(p => object(p) && identifier(p.product_id) && string(p.resource_group_id) && optionalDecimal(p.units_per_workday) && optionalDecimal(p.production_unit_cost) && (p.setup_cost === undefined || optionalDecimal(p.setup_cost)) && nullable(p.currency) && decimal(p.setup_workdays) && timestamp(p.material_ready_at) && optionalTimestamp(p.confirmed_at) && evidence(p))
    || !Array.isArray(v.resources) || !v.resources.every(r => object(r) && identifier(r.group_id) && Array.isArray(r.calendar) && r.calendar.every(d => Number.isInteger(d) && Number(d) >= 0 && Number(d) <= 6) && (r.enabled === undefined || typeof r.enabled === 'boolean') && (r.free_capacity_by_day === undefined || (object(r.free_capacity_by_day) && Object.entries(r.free_capacity_by_day).every(([d, n]) => /^\d{4}-\d{2}-\d{2}$/.test(d) && decimal(n)))) && optionalTimestamp(r.confirmed_at) && (r.default_daily_capacity === undefined || optionalDecimal(r.default_daily_capacity)))) throw new Error(unexpected)
  return v as Profile
}
function snapshot(v: unknown): Snapshot {
  if (!object(v) || !identifier(v.id) || !timestamp(v.created_at) || v.created_at === null) throw new Error(unexpected)
  const effective = v.effective_scoring_policy
  return {id: v.id, created_at: v.created_at as string, profile: readCompanyProfile(v.profile),
    ...(effective === undefined ? {} : {effective_scoring_policy: readScoringPolicy(effective)})}
}
export function readSaveCommand(v: unknown): SaveCommand {
  if (!object(v) || !identifier(v.command_key) || !nullable(v.base_snapshot_id)) throw new Error(unexpected)
  return {command_key: v.command_key, base_snapshot_id: v.base_snapshot_id, profile: readCompanyProfile(v.profile)}
}
export class CompanySettingsError extends Error {
  readonly status?: number
  constructor(message: string, status?: number) { super(message); this.status = status }
  get rejected() { return this.status !== undefined && this.status >= 400 && this.status < 500 }
}
async function request(method: string, suffix = '', body?: unknown): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(`/api/v0.1/settings/company${suffix}`, {method,
      headers: {'X-Bervel-Web': '1', ...(body === undefined ? {} : {'Content-Type': 'application/json'})},
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(30_000)})
  } catch { throw new CompanySettingsError('Ответ сервера не получен. Результат сохранения может быть неизвестен.') }
  const value: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const messages: Record<number, string> = {403: 'Недостаточно прав для изменения настроек.', 404: 'Складской файл не найден в области данных предприятия.', 409: 'Настройки уже изменены в другом окне. Загрузите актуальную версию перед повторным редактированием.', 422: 'Проверьте поля настроек: формат чисел, единицы, валюту и подтверждения.', 503: 'Хранилище настроек недоступно.'}
    let message = messages[response.status] || 'Не удалось выполнить запрос настроек предприятия.'
    if (object(value) && object(value.error) && string(value.error.message)) message += ` ${value.error.message}`
    throw new CompanySettingsError(message, response.status)
  }
  return value
}
export async function getCompanySettings(): Promise<Snapshot | null> {
  const v = await request('GET')
  if (!object(v) || !('snapshot' in v)) throw new Error(unexpected)
  return v.snapshot === null ? null : snapshot(v.snapshot)
}
export async function previewCompanySettings(stock_artifact_id: string, base_snapshot_id: string | null): Promise<{profile: Profile; warnings: string[]}> {
  const v = await request('POST', '/preview', {stock_artifact_id, base_snapshot_id})
  if (!object(v) || !Array.isArray(v.warnings) || !v.warnings.every(string)) throw new Error(unexpected)
  return {profile: readCompanyProfile(v.profile), warnings: v.warnings}
}
export async function saveCompanySettings(command: SaveCommand): Promise<Snapshot> {
  const v = await request('PUT', '', command)
  if (!object(v)) throw new Error(unexpected)
  return snapshot(v.snapshot)
}
