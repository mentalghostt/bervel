export const voiceMessages: Record<string,string> = {
  VOICE_UNCONFIGURED: 'Сначала добавьте ключ Groq в настройках.',
  VOICE_UNSUPPORTED_AUDIO: 'Этот формат записи не поддерживается. Попробуйте другой браузер.',
  VOICE_TOO_LARGE: 'Запись больше 5 МиБ. Запишите короткое сообщение.',
  VOICE_AUTH_ERROR: 'Groq отклонил ключ. Проверьте его в настройках.',
  VOICE_BUSY: 'Сервис распознавания занят. Повторите позже.',
  VOICE_TIMEOUT: 'Время распознавания истекло. Запишите сообщение ещё раз.',
  VOICE_FAILED: 'Не удалось распознать запись. Попробуйте ещё раз.',
}
export type VoiceSettings = {configured:boolean; state:'configured'|'unconfigured'; model:string}
export async function voiceSettings(apiKey?:string): Promise<VoiceSettings> {
  let response:Response
  try{response = await fetch('/api/v0.1/voice/settings', {method:apiKey === undefined?'GET':'PUT',headers:{'X-Bervel-Web':'1',...(apiKey===undefined?{}:{'Content-Type':'application/json'})},body:apiKey===undefined?undefined:JSON.stringify({api_key:apiKey}),signal:AbortSignal.timeout(30000)})}
  catch{throw Error('Не удалось связаться с настройками Groq. Повторите позже.')}
  const value = await response.json().catch(()=>null)
  if (!response.ok) throw Error(response.status===403?'Недостаточно прав для настроек Groq.':'Не удалось получить настройки Groq.')
  if (typeof value?.configured!=='boolean' || typeof value.model!=='string') throw Error('Неверный ответ настроек Groq.')
  return value
}
export async function transcribeVoice(blob:Blob,signal:AbortSignal):Promise<string> {
  const body = new FormData();body.append('file',blob,blob.type.includes('mp4')?'voice.mp4':'voice.webm')
  let response:Response
  try{response = await fetch('/api/v0.1/voice/transcribe',{method:'POST',headers:{'X-Bervel-Web':'1'},body,signal:AbortSignal.any([signal,AbortSignal.timeout(65000)])})}
  catch{throw Error('Связь с распознаванием прервана. Попробуйте ещё раз.')}
  const value = await response.json().catch(()=>null)
  if (!response.ok) throw Error(voiceMessages[value?.error?.code] || 'Не удалось распознать запись. Проверьте соединение и настройки Groq.')
  if (typeof value?.text!=='string') throw Error('Сервис вернул неверный текст записи.')
  return value.text
}
