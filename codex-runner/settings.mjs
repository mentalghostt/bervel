import { readFile, writeFile, rename, mkdir, unlink } from 'node:fs/promises'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'

export class SettingsError extends Error {
  constructor(code, message) { super(message); this.code = code }
}
function urlOf(value) {
  const url = new URL(value)
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) throw new Error('Invalid URL')
  return url.toString().replace(/\/$/, '')
}
async function optionalFile(path) {
  if (!path) return null
  try { return await readFile(path, 'utf8') }
  catch (error) { if (error.code === 'ENOENT') return null; throw error }
}
function tomlString(source, key) {
  const top = (source || '').split(/^\s*\[/m)[0]
  const match = top.match(new RegExp('^' + key + '\\s*=\\s*("(?:[^"\\\\]|\\\\.)*")\\s*(?:#.*)?$', 'm'))
  return match ? JSON.parse(match[1]) : undefined
}

export class ProviderSettings {
  constructor(env) {
    this.env = env
    this.home = env.CODEX_HOME || '/data/codex'
    this.file = join(this.home, 'provider-settings.json')
    this.writes = Promise.resolve()
  }
  async profiles() {
    const env = this.env
    const existing = await optionalFile(this.file)
    const stored = existing ? JSON.parse(existing) : {}
    let moxUrl = '', moxModel = '', moxError = null
    try {
      const moxConfig = await optionalFile(env.BERVEL_MOX_CONFIG_FILE)
      const url = new URL(urlOf(env.BERVEL_MOX_BASE_URL || tomlString(moxConfig, 'chatgpt_base_url') || 'https://localhost:8000/backend-api'))
      moxUrl = url.toString().replace(/\/$/, '')
      moxModel = env.BERVEL_MOX_MODEL || tomlString(moxConfig, 'model') || ''
    } catch { moxError = 'Не удалось прочитать настройки MOX. Проверьте файлы подключения на сервере.' }
    return {
      provider: stored.provider || (env.BERVEL_CODEX_API_KEY || env.BERVEL_CODEX_CREDENTIALS_FILE ? 'api_key' : 'mox'),
      api: { base_url: env.BERVEL_CODEX_BASE_URL || 'https://api.openai.com/v1', model: env.BERVEL_CODEX_MODEL || '',
        key: env.BERVEL_CODEX_API_KEY || '', ...stored.api },
      mox: { model: moxModel, ...stored.mox, base_url: moxUrl, error: moxError },
    }
  }
  async moxCredentials() {
    const content = await optionalFile(this.env.BERVEL_MOX_AUTH_FILE)
    if (!content) return null
    const value = JSON.parse(content)
    const apiKey = value.tokens?.access_token
    const accountId = value.tokens?.account_id
    return typeof apiKey === 'string' && apiKey && typeof accountId === 'string' && accountId ? { apiKey, accountId } : null
  }
  async apiCredential(value) {
    if (value.api.key) return value.api.key
    const content = (await optionalFile(this.env.BERVEL_CODEX_CREDENTIALS_FILE))?.trim()
    if (!content) return ''
    const key = content.startsWith('{') ? JSON.parse(content).OPENAI_API_KEY : content
    return typeof key === 'string' ? key.trim() : ''
  }
  async read() {
    const value = await this.profiles()
    let credentials, apiKey = '', moxError = value.mox.error, apiError = null
    try { credentials = await this.moxCredentials() }
    catch { moxError = 'Не удалось прочитать доступ MOX. Проверьте файл доступа на сервере.' }
    try { apiKey = await this.apiCredential(value) }
    catch { apiError = 'Не удалось прочитать внешний API-ключ. Проверьте файл доступа или сохраните новый ключ.' }
    const api = { base_url: value.api.base_url, model: value.api.model, api_key_configured: Boolean(apiKey), error: apiError }
    const mox = { base_url: value.mox.base_url, model: value.mox.model, credentials_available: Boolean(credentials && !moxError), error: moxError }
    return { provider: value.provider, api, mox,
      configured: value.provider === 'mox' ? Boolean(mox.credentials_available && mox.model) : Boolean(api.api_key_configured && api.model) }
  }
  async configuration() {
    const value = await this.profiles()
    let model, baseUrl, apiKey, accountId
    if (value.provider === 'mox') {
      if (value.mox.error) throw new SettingsError('MODEL_UNCONFIGURED', value.mox.error)
      const credentials = await this.moxCredentials()
      model = value.mox.model
      baseUrl = value.mox.base_url.replace(/\/codex\/?$/, '') + '/codex'
      apiKey = credentials?.apiKey
      accountId = credentials?.accountId
    } else {
      model = value.api.model; baseUrl = value.api.base_url; apiKey = await this.apiCredential(value)
    }
    if (!model || !apiKey) throw new SettingsError('MODEL_UNCONFIGURED', 'Доступ к выбранному провайдеру не настроен.')
    return { provider: value.provider, model, baseUrl, apiKey, accountId, home: this.home, cwd: this.env.BERVEL_CODEX_WORKSPACE || '/workspace' }
  }
  save(body) {
    const update = this.writes.then(() => this.saveOnce(body))
    this.writes = update.catch(() => {})
    return update
  }
  async saveOnce(body) {
    try {
      if (!body || typeof body !== 'object' || Array.isArray(body)
        || Object.keys(body).some(key => !['provider', 'model', 'base_url', 'api_key'].includes(key))
        || !['mox', 'api_key'].includes(body.provider) || typeof body.model !== 'string'
        || !body.model.trim() || body.model.length > 200 || /[\x00-\x1f]/.test(body.model)) throw new Error('Invalid fields')
      const value = await this.profiles()
      value.provider = body.provider
      if (body.provider === 'mox') {
        if (body.api_key !== undefined || body.base_url !== undefined) throw new Error('Unexpected MOX fields')
        value.mox.model = body.model.trim()
      } else {
        if (typeof body.base_url !== 'string' || body.base_url.length > 2048) throw new Error('Invalid URL')
        value.api.base_url = urlOf(body.base_url)
        value.api.model = body.model.trim()
        if (body.api_key !== undefined) {
          if (typeof body.api_key !== 'string' || body.api_key.length > 8192 || /[\x00-\x1f\x7f]/.test(body.api_key)) throw new Error('Invalid key')
          if (body.api_key.trim()) value.api.key = body.api_key.trim()
        }
        if (!await this.apiCredential(value)) throw new Error('API key required')
      }
      await mkdir(this.home, { recursive: true, mode: 0o700 })
      const temporary = this.file + '.' + randomUUID()
      try {
        await writeFile(temporary, JSON.stringify({ provider: value.provider, api: value.api, mox: { model: value.mox.model } }), { mode: 0o600, flag: 'wx' })
        await rename(temporary, this.file)
      } finally { await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error }) }
      return await this.read()
    } catch (error) {
      if (error.code === 'EACCES' || error.code === 'EROFS' || error.code === 'ENOSPC') throw new SettingsError('SETTINGS_UNAVAILABLE', 'Хранилище настроек недоступно.')
      throw new SettingsError('INVALID_SETTINGS', 'Проверьте провайдер, модель, HTTPS-адрес и ключ доступа.')
    }
  }
}
