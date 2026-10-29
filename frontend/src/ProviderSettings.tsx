import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import VoiceSettings from './VoiceSettings'

type Settings = {
  provider: 'mox' | 'api_key'; configured: boolean
  api: { base_url: string; model: string; api_key_configured: boolean; error?: string | null }
  mox: { base_url: string; model: string; credentials_available: boolean; error?: string | null }
}
const messages: Record<string, string> = {
  MODEL_TLS_ERROR: 'Не удалось проверить сертификат шлюза. Проверка остановлена; сертификаты не изменялись.',
  MODEL_AUTH_ERROR: 'Шлюз отклонил доступ. Проверка остановлена; вход и токены не изменялись.',
  MODEL_UNCONFIGURED: 'Доступ к выбранному провайдеру не настроен на сервере.',
  MODEL_BUSY: 'Codex выполняет другой запрос. Дождитесь его завершения.',
  MODEL_TIMEOUT: 'Время ожидания истекло. Исход запроса неизвестен; автоматического повтора нет.',
  MODEL_FAILED: 'Codex не смог завершить запрос. Автоматического повтора нет.',
  INVALID_SETTINGS: 'Проверьте модель, HTTPS-адрес и ключ доступа.',
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
    || typeof v.api?.model !== 'string' || typeof v.api?.base_url !== 'string' || typeof v.api?.api_key_configured !== 'boolean'
    || typeof v.mox?.model !== 'string' || typeof v.mox?.base_url !== 'string' || typeof v.mox?.credentials_available !== 'boolean'
    || [v.api.error, v.mox.error].some(error => error != null && typeof error !== 'string')) {
    throw new Error('Сервер вернул неожиданный формат настроек.')
  }
  return v
}
const field = 'mt-2 block w-full rounded-lg border border-line bg-white px-3 py-3 text-sm focus-visible:outline-2 focus-visible:outline-accent disabled:bg-paper'
const button = 'rounded-lg border border-line px-5 py-3 text-sm font-bold focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50'

export default function ProviderSettings() {
  const [saved, setSaved] = useState<Settings | null>(null)
  const [provider, setProvider] = useState<'mox' | 'api_key'>('mox')
  const [model, setModel] = useState('')
  const [baseUrl, setBaseUrl] = useState('https://api.openai.com/v1')
  const [apiKey, setApiKey] = useState('')
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState<'save' | 'test' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  function apply(value: Settings) {
    setSaved(value); setProvider(value.provider); setModel(value.provider === 'mox' ? value.mox.model : value.api.model)
    setBaseUrl(value.api.base_url); setApiKey(''); setDirty(false)
  }
  useEffect(() => {
    let active = true
    request('GET').then(value => { if (active) apply(settings(value)) }).catch(reason => { if (active) setError(reason.message) })
    return () => { active = false }
  }, [])
  function change() { setDirty(true); setNotice(null); setError(null) }
  function select(value: 'mox' | 'api_key') {
    setProvider(value); setModel(value === 'mox' ? saved?.mox.model || '' : saved?.api.model || '')
    setApiKey(''); change()
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    setBusy('save'); setError(null); setNotice(null)
    try {
      const body = provider === 'mox' ? { provider, model } : { provider, model, base_url: baseUrl, api_key: apiKey }
      apply(settings(await request('PUT', '', body)))
      setNotice('Настройки сохранены. Следующие запросы используют выбранный шлюз.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Не удалось сохранить настройки.') }
    finally { setBusy(null); setApiKey('') }
  }
  async function testConnection() {
    if (busy || dirty) return
    setBusy('test'); setError(null); setNotice(null)
    try {
      const value = await request('POST', '/test', {}) as { answer: string; model: string }
      if (typeof value?.answer !== 'string' || typeof value?.model !== 'string') throw new Error('Сервер вернул неожиданный ответ.')
      setNotice(`Ответ Codex (${value.model}): ${value.answer}`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Проверка завершилась ошибкой.') }
    finally { setBusy(null) }
  }
  return <main className="mx-auto px-5 py-10 sm:px-8 lg:py-14">
    <p className="mb-3 text-xs font-bold tracking-[0.18em] text-accent uppercase">Подключение модели</p>
    <h1 className="font-display text-4xl font-semibold tracking-tight sm:text-5xl">Настройки</h1>
    <p className="mt-4 max-w-2xl leading-7 text-muted">Выбранный шлюз используется для чата и фоновых запросов Codex. Настройки общие для этого приложения.</p>
    {error && <p role="alert" className="mt-6 max-w-2xl rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-900">{error}</p>}
    <div className="settings-columns workspace-columns"><div>{!saved ? <p role="status" className="mt-6 text-muted">{error ? 'Обновите страницу после проверки сервера.' : 'Получаем настройки…'}</p> :
      <form onSubmit={save} className="mt-8 max-w-2xl rounded-2xl border border-line bg-white p-5 sm:p-7">
        <fieldset disabled={busy !== null}>
          <legend className="font-display text-xl font-semibold">Шлюз</legend>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {([['mox', 'MOX Access', 'Использовать доступ через локальный шлюз.'], ['api_key', 'API-ключ', 'Подключиться напрямую к API провайдера.']] as const).map(([value, label, description]) =>
              <label key={value} className={`cursor-pointer rounded-xl border p-4 ${provider === value ? 'border-accent bg-accent-soft' : 'border-line'}`}>
                <span className="flex items-center gap-3 font-bold"><input type="radio" name="provider" aria-label={label} aria-describedby={`provider-${value}-description`} value={value} checked={provider === value} onChange={() => select(value)} className="accent-accent" />{label}</span>
                <span id={`provider-${value}-description`} className="mt-2 block text-sm leading-6 text-muted">{description}</span>
              </label>)}
          </div>
          {(provider === 'mox' ? saved.mox.error : saved.api.error) && <p className="mt-4 text-sm text-red-800">{provider === 'mox' ? saved.mox.error : saved.api.error}</p>}
          <label className="mt-6 block text-sm font-bold">Модель<input required value={model} maxLength={200} onChange={event => { setModel(event.target.value); change() }} className={field} /></label>
          {provider === 'api_key' ? <>
            <label className="mt-5 block text-sm font-bold">Адрес API<input required type="url" value={baseUrl} onChange={event => { setBaseUrl(event.target.value); change() }} className={field} /></label>
            <label className="mt-5 block text-sm font-bold">API-ключ провайдера<input type="password" autoComplete="new-password" spellCheck={false} required={!saved.api.api_key_configured} value={apiKey} maxLength={8192} onChange={event => { setApiKey(event.target.value); change() }} className={field} aria-describedby="key-help" /></label>
            <p id="key-help" className="mt-2 text-sm leading-6 text-muted">{saved.api.api_key_configured ? 'Ключ сохранён. Оставьте поле пустым, чтобы использовать его.' : 'Вставьте ключ провайдера. После сохранения поле очистится.'} Ключ хранится на сервере и не показывается при чтении настроек.</p>
          </> : <div className="mt-5 text-sm leading-6 text-muted">
            <p>Адрес шлюза: <span className="break-all text-ink">{saved.mox.base_url}</span></p>
            <p className="mt-2">{saved.mox.credentials_available ? 'Файлы доступа MOX подключены только для чтения.' : 'Доступ MOX ещё не подключён к серверу приложения.'} Вход и обновление токенов здесь не выполняются.</p>
          </div>}
        </fieldset>
        <div className="mt-7 flex flex-wrap gap-3">
          <button type="submit" disabled={busy !== null || !dirty} className={`${button} border-accent bg-accent text-white`}>{busy === 'save' ? 'Сохраняем…' : 'Сохранить'}</button>
          <button type="button" onClick={testConnection} disabled={busy !== null || dirty || !saved.configured} className={button}>{busy === 'test' ? 'Проверяем…' : 'Проверить подключение'}</button>
        </div>
        <p className="mt-3 text-xs leading-5 text-muted">{dirty ? 'Сначала сохраните изменения для проверки.' : 'Проверка отправляет один запрос «Ответь одним словом: OK».'}</p>
        {notice && <p role="status" className="mt-5 whitespace-pre-wrap break-words rounded-lg bg-accent-soft p-4 text-sm leading-6">{notice}</p>}
      </form>}</div><VoiceSettings/></div>
  </main>
}
