import {run, RunError} from './aiRadarRunsApi'
import type {Receipt, Run} from './aiRadarRunsApi'

export type ProfileSource = 'test_api' | 'kontur_api'
export type Schedule = {enabled: boolean; every_days: number; time: string; timezone: 'Europe/Moscow'}
export type RadarProfile = {source: ProfileSource; name: string; run_id: string | null; keyword_mode: 'auto' | 'manual'; keywords: string[]; schedule: Schedule; available: boolean; last_refresh_at: string | null; next_refresh_at: string | null; quotas?: {search: number; detail: number}; reanalyze_existing?: boolean; auto_reanalyze_existing?: boolean}
export type RefreshCommand = {kind?: 'refresh'; source: ProfileSource; body: {command_key: string; detail_max: number | null; schedule?: Schedule; reanalyze_existing?: boolean}}
export type ScheduleCommand = {source: ProfileSource; body: {command_key: string; schedule: Schedule; reanalyze_existing: boolean}}
export type ResumeCommand = {kind: 'resume'; source: ProfileSource; body: {command_key: string}}
export type MetricsCommand = {kind: 'metrics'; source: ProfileSource; body: {command_key: string}}
export type ProfileCommand = RefreshCommand | ResumeCommand | MetricsCommand
const root = '/api/v0.1/ai-tender-radar/profiles'
const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v)
const uuid = (v:unknown) => typeof v==='string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v)
const nullableTime = (v:unknown) => v===null || typeof v==='string' && Number.isFinite(Date.parse(v))
const validSchedule = (v:unknown):v is Schedule => object(v) && typeof v.enabled==='boolean' && Number.isSafeInteger(v.every_days) && Number(v.every_days)>=1 && /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(String(v.time)) && v.timezone==='Europe/Moscow'
export function profileSource(v: unknown): v is ProfileSource {return v === 'test_api' || v === 'kontur_api'}
function invalid(): never {throw new RunError('Сервер вернул неожиданные данные профиля. Повторите чтение.')}
function profile(v: unknown): RadarProfile {
  if (!object(v) || !profileSource(v.source) || typeof v.name !== 'string' || !(v.run_id === null || uuid(v.run_id)) || !['auto','manual'].includes(String(v.keyword_mode)) || !Array.isArray(v.keywords) || !v.keywords.every(k => typeof k === 'string') || typeof v.available !== 'boolean' || !object(v.schedule) || typeof v.schedule.enabled !== 'boolean' || !Number.isSafeInteger(v.schedule.every_days) || Number(v.schedule.every_days) < 1 || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(String(v.schedule.time)) || v.schedule.timezone !== 'Europe/Moscow') return invalid()
  if (v.reanalyze_existing !== undefined && typeof v.reanalyze_existing !== 'boolean') return invalid()
  if (v.auto_reanalyze_existing !== undefined && typeof v.auto_reanalyze_existing !== 'boolean') return invalid()
  if (!nullableTime(v.last_refresh_at) || !nullableTime(v.next_refresh_at) || (v.quotas!==undefined && (!object(v.quotas) || !['search','detail'].every(key=>Number.isSafeInteger(v.quotas && (v.quotas as Record<string,unknown>)[key]) && Number((v.quotas as Record<string,unknown>)[key])>=0)))) return invalid()
  return v as RadarProfile
}
async function request(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<unknown> {
  let response: Response
  const timeout = AbortSignal.timeout(method === 'GET' ? 120_000 : 30_000)
  try {response = await fetch(root + path, {method, headers: {'X-Bervel-Web':'1', ...(body === undefined ? {} : {'Content-Type':'application/json'})}, body: body === undefined ? undefined : JSON.stringify(body), signal: signal ? AbortSignal.any([signal, timeout]) : timeout})}
  catch (error) {
    if (signal?.aborted) throw error
    throw new RunError(method === 'GET' ? 'Не удалось загрузить профиль. Повторите чтение.' : 'Ответ сервера не получен. Исход команды неизвестен; повторите сохранённую команду с тем же ключом.')
  }
  const value: unknown = await response.json().catch(error => {
    if (signal?.aborted) throw error
    if (method === 'GET') throw new RunError('Не удалось прочитать ответ профиля. Повторите чтение.')
    return null
  })
  if (!response.ok) throw new RunError(object(value) && object(value.error) && typeof value.error.message === 'string' ? value.error.message : 'Не удалось выполнить запрос профиля.', response.status >= 400 && response.status < 500)
  return value
}
export async function listProfiles(signal?: AbortSignal): Promise<RadarProfile[]> {
  const v = await request('', 'GET', undefined, signal)
  if (!object(v) || !Array.isArray(v.profiles)) return invalid()
  const profiles = v.profiles.map(profile)
  if (profiles.length !== 2 || new Set(profiles.map(p => p.source)).size !== 2) return invalid()
  return profiles
}
export async function readProfile(source: ProfileSource, signal?: AbortSignal): Promise<{profile: RadarProfile; run: Run | null}> {
  const v = await request('/' + source, 'GET', undefined, signal)
  if (!object(v)) return invalid()
  const p = profile(v.profile)
  if (p.source !== source) return invalid()
  const value = v.run === null ? null : run(v.run, true)
  if (value && value.run_id !== p.run_id) return invalid()
  return {profile:p,run:value}
}
export async function saveKeywords(source: ProfileSource, keyword_mode: RadarProfile['keyword_mode'], keywords: string[]): Promise<RadarProfile> {return profile(await request('/' + source + '/keywords', 'PUT', {keyword_mode,keywords}))}
export async function submitProfileCommand(command: ProfileCommand): Promise<Receipt> {
  const v = await request('/' + command.source + '/' + (command.kind ?? 'refresh'),'POST',command.body)
  if (!object(v) || !uuid(v.job_id) || !uuid(v.run_id) || typeof v.state !== 'string' || !['waiting','queued','running','loaded','no_match','failed'].includes(v.state)) return invalid()
  return v as Receipt
}
export async function submitScheduleCommand(command: ScheduleCommand): Promise<RadarProfile> {
  const value=profile(await request('/'+command.source+'/schedule','POST',command.body))
  if(value.source!==command.source)return invalid()
  return value
}
export function readScheduleCommand(v:unknown):ScheduleCommand {
  if(!object(v) || Object.keys(v).some(key=>!['source','body'].includes(key)) || !profileSource(v.source) || !object(v.body)
    || Object.keys(v.body).some(key=>!['command_key','schedule','reanalyze_existing'].includes(key))
    || typeof v.body.command_key!=='string' || !v.body.command_key || v.body.command_key.length>128
    || !validSchedule(v.body.schedule) || typeof v.body.reanalyze_existing!=='boolean')return invalid()
  return v as ScheduleCommand
}
export function readProfileCommand(v: unknown): ProfileCommand {
  if (!object(v) || !profileSource(v.source) || !object(v.body) || typeof v.body.command_key !== 'string' || !v.body.command_key || v.body.command_key.length>128) return invalid()
  if (v.kind === 'resume' || v.kind === 'metrics') {
    if (Object.keys(v.body).some(key => key !== 'command_key')) return invalid()
    return v as ResumeCommand
  }
  if (v.kind !== undefined && v.kind !== 'refresh' || !(v.body.detail_max === null || Number.isSafeInteger(v.body.detail_max) && Number(v.body.detail_max) >= 0) || (v.body.schedule !== undefined && !validSchedule(v.body.schedule))) return invalid()
  if (v.body.reanalyze_existing !== undefined && typeof v.body.reanalyze_existing !== 'boolean') return invalid()
  return v as RefreshCommand
}
