import { lazy, Suspense, useEffect, useState } from 'react'
import VoiceSettings from './VoiceSettings'
import KonturSettings from './KonturSettings'
import PresalesSettings from './PresalesSettings'
const CompanySettingsPage = lazy(() => import('./CompanySettingsPage'))

type Settings = {
  provider: 'mox' | 'api_key'; configured: boolean
  api: { model: string }
  mox: { model: string; credentials_available: boolean }
}
// Models advertised as selectable and API-supported by the MOX Access catalog.
const moxModels = [
  'gpt-6.1-sol', 'gpt-6-astra', 'gpt-6.1-terra', 'gpt-6-sol',
  'gpt-6-luna', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna',
]
const messages: Record<string, string> = {
  MODEL_TLS_ERROR: 'Не удалось проверить сертификат шлюза.',
  MODEL_AUTH_ERROR: 'Шлюз отклонил доступ.',
  MODEL_UNCONFIGURED: 'Доступ к выбранному провайдеру не настроен на сервере.',
  MODEL_BUSY: 'Codex выполняет другой запрос. Дождитесь его завершения.',
  MODEL_TIMEOUT: 'Время ожидания истекло. Исход запроса неизвестен; автоматического повтора нет.',
  MODEL_FAILED: 'Codex не смог завершить запрос. Автоматического повтора нет.',
  INVALID_SETTINGS: 'Не удалось сохранить выбранную модель.',
  SETTINGS_UNAVAILABLE: 'Хранилище настроек недоступно.',
}
async function request(method: string, path = '', body?: unknown): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(`/api/v0.1/settings/model${path}`, { method,
      headers: { 'X-Bervel-Web': '1', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(200_000) })
  } catch { throw new Error('Сервер недоступен или время ожидания истекло. Проверьте состояние перед повтором.') }
  const value = await response.json().catch(() => null)
  if (!response.ok) throw new Error(messages[value?.error?.code] || (response.status === 403
    ? 'Недостаточно прав для изменения настроек.' : 'Не удалось получить ответ сервера настроек.'))
  return value
}
function settings(value: unknown): Settings {
  const v = value as Settings
  if (!v || !['mox', 'api_key'].includes(v.provider) || typeof v.configured !== 'boolean'
    || typeof v.api?.model !== 'string' || typeof v.mox?.model !== 'string'
    || typeof v.mox?.credentials_available !== 'boolean') {
    throw new Error('Сервер вернул неожиданный формат настроек.')
  }
  return v
}
export default function ProviderSettings() {
  const [presalesOpen, setPresalesOpen] = useState(false)
  const [saved, setSaved] = useState<Settings | null>(null)
  const [pendingModel, setPendingModel] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const busy = pendingModel !== null || testing
  const externalProvider = saved?.provider === 'api_key'
  const savedModel = (externalProvider ? saved.api.model : saved?.mox.model) ?? ''
  const unlistedModel = externalProvider || !moxModels.includes(savedModel)
  useEffect(() => {
    let active = true
    request('GET').then(value => { if (active) setSaved(settings(value)) }).catch(reason => { if (active) setError(reason.message) })
    return () => { active = false }
  }, [])
  async function saveModel(model: string) {
    if (busy || !moxModels.includes(model)) return
    setPendingModel(model); setError(null); setNotice(null)
    try {
      setSaved(settings(await request('PUT', '', { provider: 'mox', model })))
      setNotice('Модель сохранена.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Не удалось сохранить настройки.') }
    finally { setPendingModel(null) }
  }
  async function testConnection() {
    if (busy || !saved?.configured) return
    setTesting(true); setError(null); setNotice(null)
    try {
      const value = await request('POST', '/test', {}) as { answer: string; model: string }
      if (typeof value?.answer !== 'string' || typeof value?.model !== 'string') throw new Error('Сервер вернул неожиданный ответ.')
      setNotice(`Ответ Codex (${value.model}): ${value.answer}`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Проверка завершилась ошибкой.') }
    finally { setTesting(false) }
  }
  return <main className="provider-settings" aria-label="Настройки">
    {error && <p role="alert" className="mt-6 max-w-2xl rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-900">{error}</p>}
    <div className="settings-columns workspace-columns"><div>{!saved ? <p role="status" className="mt-6 text-muted">{error ? 'Обновите страницу после проверки сервера.' : 'Получаем настройки…'}</p> :
      <section className="panel">
        <fieldset disabled={busy} aria-busy={busy}>
          <legend className="font-display text-xl font-semibold">Шлюз</legend>
          <label className="mt-4 block text-sm font-bold">Модель
            <select value={pendingModel ?? (unlistedModel ? '' : savedModel)}
              onChange={event => { void saveModel(event.target.value) }}
              aria-describedby={externalProvider ? 'mox-switch-description' : undefined}>
              {unlistedModel && <option value="" disabled>{savedModel || 'Выберите модель'}</option>}
              {moxModels.map(model => <option key={model} value={model}>{model}</option>)}
            </select>
          </label>
          {externalProvider && <p id="mox-switch-description" className="mt-2 text-sm text-muted">Сейчас используется внешний провайдер. Выбор модели переключит запросы на MOX Access.</p>}
          {!externalProvider && !saved.mox.credentials_available && <p className="mt-2 text-sm text-muted">Подключение к шлюзу недоступно.</p>}
          <button type="button" onClick={testConnection} disabled={!saved.configured} className="secondary mt-4">{testing ? 'Проверяем…' : 'Проверить подключение'}</button>
        </fieldset>
        {(pendingModel || notice) && <p role="status" className="mt-4 whitespace-pre-wrap break-words text-sm leading-6">{pendingModel ? 'Сохраняем модель…' : notice}</p>}
      </section>}</div><section className="panel" aria-label="Настройки пресейла"><h2>Пресейл-конвейер</h2><p className="mt-3 text-sm text-muted">Маржа, контакты продавца и условия коммерческого предложения.</p><button type="button" aria-haspopup="dialog" onClick={() => setPresalesOpen(true)} className="secondary mt-4 inline-flex">Настройки пресейла</button></section><VoiceSettings/><Suspense fallback={<p role="status">Открываем настройки радара…</p>}><CompanySettingsPage presentation="radar-policy"/></Suspense><KonturSettings/></div>
    {presalesOpen && <PresalesSettings onClose={() => setPresalesOpen(false)} />}
  </main>
}
