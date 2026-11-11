export type KonturLimit = { group: string; remaining: number; maximum: number }
export type KonturConnection = { configured: boolean; limits: KonturLimit[] }
export type SimulatorQuota = {available: boolean; unlimited: boolean; resettable: boolean; limits: KonturLimit[]}

async function request(method: 'GET' | 'PUT', key?: string, signal?: AbortSignal): Promise<KonturConnection> {
  let response: Response
  try {
    const timeout = AbortSignal.timeout(30_000)
    response = await fetch('/api/v0.1/settings/kontur', {
      method,
      headers: { 'X-Bervel-Web': '1', ...(key === undefined ? {} : { 'Content-Type': 'application/json' }) },
      body: key === undefined ? undefined : JSON.stringify({ api_key: key }),
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    })
  } catch {
    throw new Error('Не удалось получить лимиты Контур.Закупок. Попробуйте обновить их.')
  }
  const value = await response.json().catch(() => null)
  if (!response.ok) throw new Error(value?.error?.message || 'Не удалось получить настройки Контур.Закупок.')
  if (typeof value?.configured !== 'boolean' || !Array.isArray(value.limits)
      || !value.limits.every((limit: KonturLimit) => typeof limit?.group === 'string' && Number.isSafeInteger(limit.remaining)
        && Number.isSafeInteger(limit.maximum) && limit.remaining >= 0 && limit.remaining <= limit.maximum)) {
    throw new Error('Сервер вернул неожиданный формат лимитов Контур.Закупок.')
  }
  return value
}

export const getKonturSettings = (signal?: AbortSignal) => request('GET', undefined, signal)
export const saveKonturSettings = (key: string) => request('PUT', key)

async function simulatorRequest(reset: boolean, signal?: AbortSignal): Promise<SimulatorQuota> {
  let response: Response
  try {
    const timeout=AbortSignal.timeout(30_000)
    response=await fetch('/api/v0.1/settings/kontur/simulation/quota'+(reset?'/reset':''), {
      method:reset?'POST':'GET', headers:{'X-Bervel-Web':'1'},
      signal:signal?AbortSignal.any([signal,timeout]):timeout,
    })
  } catch {
    throw new Error(reset?'Ответ на сброс квоты не получен. Обновите лимиты, чтобы проверить результат.':'Не удалось получить квоту симуляции API.')
  }
  const value=await response.json().catch(()=>null)
  if(!response.ok)throw new Error(value?.error?.message||'Не удалось обновить квоту симуляции API.')
  if(typeof value?.available!=='boolean'||typeof value.unlimited!=='boolean'||typeof value.resettable!=='boolean'||!Array.isArray(value.limits)
    ||!value.limits.every((limit:KonturLimit)=>typeof limit?.group==='string'&&Number.isSafeInteger(limit.remaining)&&Number.isSafeInteger(limit.maximum)&&limit.remaining>=0&&limit.remaining<=limit.maximum)) {
    throw new Error('Сервер вернул неожиданный формат квоты симуляции API.')
  }
  return value
}
export const getSimulatorQuota=(signal?:AbortSignal)=>simulatorRequest(false,signal)
export const resetSimulatorQuota=()=>simulatorRequest(true)
