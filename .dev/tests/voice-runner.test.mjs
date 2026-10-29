import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, readFile, stat } from 'node:fs/promises'
import { resolve, join } from 'node:path'

const home = () => mkdtemp(resolve('.dev/local/assistant-capabilities-2026-10-28/native/voice-test-'))
const load = () => import('../../codex-runner/voice.mjs')
test('voice secret is write-only, private, restart durable; invalid replacement cannot lose it', async () => {
  const { VoiceSettings } = await load(), directory = await home()
  const settings = new VoiceSettings({ CODEX_HOME: directory })
  assert.deepEqual(await settings.read(), { configured: false, state: 'unconfigured', model: 'grok-voice-transcribe-2.0' })
  await assert.rejects(settings.key(), error => error.code === 'VOICE_UNCONFIGURED')
  const saved = await settings.save({ api_key: 'fixture-secret' })
  assert.equal(saved.configured, true)
  assert.equal(JSON.stringify(saved).includes('fixture-secret'), false)
  const restarted = new VoiceSettings({ CODEX_HOME: directory })
  assert.equal(await restarted.key(), 'fixture-secret')
  await restarted.save({ api_key: '' })
  for (const value of [{ api_key: 'bad\nkey' }, { api_key: 12 }, { api_key: 'x'.repeat(8193) }, { api_key: 'new', provider: 'mox' }]) {
    await assert.rejects(restarted.save(value), error => error.code === 'INVALID_VOICE_SETTINGS')
    assert.equal(await restarted.key(), 'fixture-secret')
  }
  const raw = await readFile(join(directory, 'voice-settings.json'), 'utf8')
  assert.equal(JSON.parse(raw).api_key, 'fixture-secret')
  if (process.platform !== 'win32') assert.equal((await stat(join(directory, 'voice-settings.json'))).mode & 0o077, 0)
})
test('transcribe uses official REST multipart model/language then file, preserving audio and cancellation', async () => {
  const { transcribe } = await load(), abort = new AbortController(), seen = []
  const audio = Buffer.from([0x1a, 0x45, 0xdf, 0xa3, 1, 2, 3])
  const result = await transcribe('private', { audio_base64: audio.toString('base64'), mime_type: 'audio/webm;codecs=opus', filename: 'recording.webm' }, abort.signal, async (url, options) => {
    seen.push(url)
    assert.equal(url, 'https://api.x.ai/v1/stt')
    assert.equal(options.headers.Authorization, 'Bearer private')
    assert.equal(options.redirect, 'error')
    assert.equal(options.signal, abort.signal)
    assert.deepEqual([...options.body.keys()], ['model', 'language', 'file'])
    assert.equal(options.body.get('model'), 'grok-voice-transcribe-2.0')
    assert.equal(options.body.get('language'), 'ru')
    const file = options.body.get('file')
    assert.equal(file.type, 'audio/webm')
    assert.deepEqual(Buffer.from(await file.arrayBuffer()), audio)
    return Response.json({ text: 'Проверь закупки', duration: 1.2, secret: 'must not leak' })
  })
  assert.deepEqual(result, { text: 'Проверь закупки' })
  assert.equal(seen.length, 1)
})
test('audio rejects wrong MIME, empty/bad base64 and bytes beyond 5MiB before provider call', async () => {
  const { transcribe } = await load(), signal = AbortSignal.timeout(1000)
  for (const bad of [{ mime_type:'text/html' }, {audio_base64:''}, {audio_base64:'!bad'}, {audio_base64:Buffer.alloc(5*1024*1024+1).toString('base64')}, {filename:'../outside.wav'}, {extra:'unexpected'}]) {
    await assert.rejects(transcribe('private', {audio_base64:'AQID',mime_type:'audio/wav',filename:'voice.wav',...bad}, signal, () => assert.fail('no HTTP for invalid audio')), error => ['VOICE_UNSUPPORTED_AUDIO','VOICE_INVALID_AUDIO','VOICE_TOO_LARGE'].includes(error.code))
  }
  for (const mime of ['audio/webm','audio/ogg; codecs=opus','audio/opus','audio/mp4','audio/x-m4a','video/mp4','audio/wav','audio/x-wav','audio/mpeg']) {
    await transcribe('private',{audio_base64:'AQID',mime_type:mime,filename:'recording.audio'},signal,async()=>Response.json({text:''}))
  }
})
test('xAI auth, throttling, malformed response and cancellation are explicit and redacted', async () => {
  const { transcribe } = await load(), body={audio_base64:'AQID',mime_type:'audio/wav',filename:'voice.wav'}
  for(const [status, code] of [[401,'VOICE_AUTH_ERROR'],[403,'VOICE_AUTH_ERROR'],[429,'VOICE_BUSY'],[500,'VOICE_FAILED']]) {
    await assert.rejects(transcribe('private',body,AbortSignal.timeout(1000),async()=>new Response('private provider detail',{status})), error=>error.code===code&&!error.message.includes('private'))
  }
  await assert.rejects(transcribe('private',body,AbortSignal.timeout(1000),async()=>Response.json({text:12})),error=>error.code==='VOICE_FAILED')
  await assert.rejects(transcribe('private',body,AbortSignal.abort(),async()=>{throw new DOMException('private','AbortError')}),error=>error.code==='VOICE_TIMEOUT')
})
