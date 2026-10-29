import { readFile, writeFile, rename, mkdir, unlink } from 'node:fs/promises'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'

export const MAX_AUDIO = 5 * 1024 * 1024
export const VOICE_MODEL = 'grok-voice-transcribe-2.0'
export class VoiceError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code }
}
export class VoiceSettings {
  constructor(env) {
    this.home = env.CODEX_HOME || '/data/codex'
    this.file = join(this.home, 'voice-settings.json')
    this.writes = Promise.resolve()
  }
  async stored() {
    try {
      const value = JSON.parse(await readFile(this.file, 'utf8'))
      if (typeof value.api_key !== 'string' || !value.api_key || value.api_key.length > 8192 || /[\x00-\x20\x7f]/.test(value.api_key)) throw Error('Invalid stored key')
      return value.api_key
    } catch (error) {
      if (error.code === 'ENOENT') return ''
      throw new VoiceError(503, 'VOICE_SETTINGS_UNAVAILABLE', 'Хранилище голосового доступа недоступно.')
    }
  }
  async key() {
    const key = await this.stored()
    if (!key) throw new VoiceError(503, 'VOICE_UNCONFIGURED', 'Добавьте ключ xAI в настройках голоса.')
    return key
  }
  async read() {
    const configured = Boolean(await this.stored())
    return { configured, state: configured ? 'configured' : 'unconfigured', model: VOICE_MODEL }
  }
  save(body) {
    const update = this.writes.then(() => this.saveOnce(body))
    this.writes = update.catch(() => {})
    return update
  }
  async saveOnce(body) {
    if (!body || typeof body !== 'object' || Array.isArray(body) || Object.keys(body).some(key => key !== 'api_key')
      || typeof body.api_key !== 'string' || body.api_key.length > 8192 || /[\x00-\x1f\x7f]/.test(body.api_key)
      || (body.api_key.trim() && /\s/.test(body.api_key.trim()))) {
      throw new VoiceError(422, 'INVALID_VOICE_SETTINGS', 'Проверьте ключ xAI.')
    }
    const key = body.api_key.trim()
    if (!key) return this.read()
    const temporary = this.file + '.' + randomUUID()
    try {
      await mkdir(this.home, { recursive: true, mode: 0o700 })
      await writeFile(temporary, JSON.stringify({ api_key: key }), { mode: 0o600, flag: 'wx' })
      await rename(temporary, this.file)
      return await this.read()
    } catch {
      throw new VoiceError(503, 'VOICE_SETTINGS_UNAVAILABLE', 'Хранилище голосового доступа недоступно.')
    } finally { await unlink(temporary).catch(() => {}) }
  }
}

export function decodeAudio(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)
    || Object.keys(body).some(key => !['audio_base64', 'mime_type', 'filename'].includes(key))
    || typeof body.audio_base64 !== 'string' || typeof body.mime_type !== 'string'
    || typeof body.filename !== 'string' || !body.filename || body.filename.length > 200 || /[\/\\\x00-\x1f\x7f]/.test(body.filename)) {
    throw new VoiceError(422, 'VOICE_INVALID_AUDIO', 'Некорректная аудиозапись.')
  }
  const mime = body.mime_type.split(';', 1)[0].trim().toLowerCase()
  if (!['audio/webm', 'audio/opus', 'audio/ogg', 'audio/mp4', 'audio/x-m4a', 'video/mp4', 'audio/wav', 'audio/x-wav', 'audio/mpeg'].includes(mime)) {
    throw new VoiceError(415, 'VOICE_UNSUPPORTED_AUDIO', 'Этот формат аудио не поддерживается.')
  }
  if (body.audio_base64.length > 4 * Math.ceil(MAX_AUDIO / 3)) throw new VoiceError(413, 'VOICE_TOO_LARGE', 'Аудиозапись превышает 5 МиБ.')
  if (!body.audio_base64 || body.audio_base64.length % 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(body.audio_base64)) {
    throw new VoiceError(422, 'VOICE_INVALID_AUDIO', 'Некорректная аудиозапись.')
  }
  const audio = Buffer.from(body.audio_base64, 'base64')
  if (audio.length > MAX_AUDIO) throw new VoiceError(413, 'VOICE_TOO_LARGE', 'Аудиозапись превышает 5 МиБ.')
  if (!audio.length || audio.toString('base64') !== body.audio_base64) throw new VoiceError(422, 'VOICE_INVALID_AUDIO', 'Некорректная аудиозапись.')
  return { audio, mime }
}

export async function transcribe(key, body, signal, fetchImpl = fetch) {
  const { audio, mime } = decodeAudio(body)
  const form = new FormData()
  form.append('model', VOICE_MODEL)
  form.append('language', 'ru')
  form.append('file', new Blob([audio], { type: mime }), body.filename)
  try {
    signal.throwIfAborted()
    const response = await fetchImpl('https://api.x.ai/v1/stt', {
      method: 'POST', headers: { Authorization: `Bearer ${key}` }, body: form, signal, redirect: 'error',
    })
    if (response.status === 401 || response.status === 403) throw new VoiceError(502, 'VOICE_AUTH_ERROR', 'xAI отклонил ключ доступа. Проверьте настройки голоса.')
    if (response.status === 429) throw new VoiceError(429, 'VOICE_BUSY', 'Лимит распознавания xAI исчерпан. Попробуйте позже.')
    if (!response.ok) throw new VoiceError(502, 'VOICE_FAILED', 'Не удалось распознать речь.')
    // Bound the upstream response too; provider diagnostics never become a user response.
    let size = 0, chunks = []
    for await (const chunk of response.body) {
      size += chunk.length
      if (size > 256 * 1024) { await response.body.cancel().catch(() => {}); throw Error('Response limit') }
      chunks.push(chunk)
    }
    const value = JSON.parse(Buffer.concat(chunks).toString('utf8'))
    if (typeof value.text !== 'string' || value.text.length > 32000) throw Error('Invalid transcript')
    return { text: value.text }
  } catch (error) {
    if (error instanceof VoiceError) throw error
    if (signal.aborted || ['TimeoutError', 'AbortError'].includes(error.name)) throw new VoiceError(504, 'VOICE_TIMEOUT', 'Распознавание прервано или превысило время ожидания.')
    throw new VoiceError(502, 'VOICE_FAILED', 'Не удалось распознать речь.')
  }
}
