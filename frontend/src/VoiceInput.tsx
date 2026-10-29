import { useEffect, useRef, useState } from 'react'
import { transcribeVoice } from './voiceApi'

const maxBytes = 5*1024*1024
export default function VoiceInput({onDraft,disabled,onBusyChange}:{onDraft:(text:string)=>void;disabled:boolean;onBusyChange?:(busy:boolean)=>void}) {
  const [state,setState] = useState<'idle'|'permission'|'recording'|'transcribing'>('idle')
  const [error,setError] = useState<string|null>(null), [notice,setNotice] = useState('')
  const recorder = useRef<MediaRecorder|null>(null), stream = useRef<MediaStream|null>(null)
  const request = useRef<AbortController|null>(null), timer = useRef<ReturnType<typeof setTimeout>|undefined>(undefined)
  const generation = useRef(0), mounted = useRef(true)
  function cleanup() {
    generation.current++
    request.current?.abort();request.current=null
    clearTimeout(timer.current)
    const current=recorder.current;recorder.current=null
    if (current) {current.onstop=null;current.ondataavailable=null;if(current.state!=='inactive')current.stop()}
    stream.current?.getTracks().forEach(track=>track.stop());stream.current=null
  }
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;cleanup()}},[])
  useEffect(()=>{onBusyChange?.(state!=='idle');return()=>onBusyChange?.(false)},[state,onBusyChange])
  function cancel() {cleanup();setState('idle');setNotice('Запись отменена.');setError(null)}
  async function start() {
    if(disabled || state!=='idle')return
    setError(null);setNotice('')
    if(!window.isSecureContext){setError('Микрофон доступен по HTTPS или на localhost.');return}
    if(!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder==='undefined'){setError('Браузер не поддерживает запись. Введите сообщение текстом.');return}
    const mime=['audio/webm;codecs=opus','audio/webm','audio/mp4'].find(type=>MediaRecorder.isTypeSupported(type))
    if(!mime){setError('Браузер не поддерживает WebM или MP4 для записи. Введите сообщение текстом.');return}
    const token=++generation.current;setState('permission')
    try {
      const media=await navigator.mediaDevices.getUserMedia({audio:true})
      if(!mounted.current || token!==generation.current){media.getTracks().forEach(track=>track.stop());return}
      stream.current=media
      const current=new MediaRecorder(media,{mimeType:mime});recorder.current=current
      const chunks:Blob[]=[];let bytes=0
      current.ondataavailable=event=>{
        if(!event.data.size || token!==generation.current)return
        bytes+=event.data.size
        if(bytes>maxBytes){cleanup();if(mounted.current){setState('idle');setError('Запись больше 5 МиБ. Запишите короткое сообщение.')}return}
        chunks.push(event.data)
      }
      current.onerror=()=>{cleanup();if(mounted.current){setState('idle');setError('Запись прервана браузером. Попробуйте ещё раз.')}}
      current.onstop=async()=>{
        clearTimeout(timer.current);media.getTracks().forEach(track=>track.stop());stream.current=null;recorder.current=null
        if(token!==generation.current || !mounted.current)return
        const blob=new Blob(chunks,{type:current.mimeType||mime})
        if(!blob.size){setState('idle');setError('Запись пустая. Попробуйте ещё раз.');return}
        const controller=new AbortController();request.current=controller;setState('transcribing')
        try {
          const text=await transcribeVoice(blob,controller.signal)
          if(token!==generation.current || !mounted.current)return
          onDraft(text);setNotice(text.trim()?'Текст добавлен в черновик. Проверьте и отправьте сообщение.':'Речь не найдена. Попробуйте ещё раз.')
        }catch(reason){if(token===generation.current && mounted.current && !controller.signal.aborted)setError(reason instanceof Error?reason.message:'Не удалось распознать запись.')}
        finally{if(token===generation.current && mounted.current){request.current=null;setState('idle')}}
      }
      current.start(250);setState('recording')
      timer.current=setTimeout(()=>{if(current.state==='recording')current.stop()},60000)
    }catch(reason){
      if(token!==generation.current || !mounted.current)return
      cleanup();setState('idle')
      setError(reason instanceof DOMException && ['NotAllowedError','PermissionDeniedError'].includes(reason.name)?'Доступ к микрофону запрещён. Разрешите его в браузере или введите текст.':'Микрофон недоступен. Проверьте устройство и повторите запись.')
    }
  }
  return <div className="voice-input">
    <div className="voice-controls">
      {state==='idle'?<button type="button" className="voice-button" disabled={disabled} onClick={()=>void start()} aria-label="Записать голосовое сообщение" title="Записать голосовое сообщение"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/></svg></button>:<>
        {state==='recording'&&<button type="button" className="voice-button recording" onClick={()=>recorder.current?.stop()} aria-label="Остановить запись" title="Остановить запись"><svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg></button>}
        <button type="button" className="voice-button" onClick={cancel} aria-label="Отменить запись" title="Отменить запись"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button>
      </>}
      <span role="status" className="voice-message">{state==='recording'?'Идёт запись · до 1 минуты / 5 МиБ':state==='permission'?'Разрешите доступ к микрофону':state==='transcribing'?'Распознаём запись…':notice}</span>
    </div>
    {error && <p role="alert" className="voice-message voice-error">{error}</p>}
  </div>
}
