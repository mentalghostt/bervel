import { useEffect,useState } from 'react'
import { voiceSettings } from './voiceApi'
export default function VoiceSettings() {
  const [configured,setConfigured]=useState<boolean|null>(null),[key,setKey]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState<string|null>(null)
  useEffect(()=>{let active=true;voiceSettings().then(value=>{if(active)setConfigured(value.configured)}).catch(reason=>{if(active)setError(reason.message)});return()=>{active=false}},[])
  return <section className="panel voice-settings" aria-label="Настройки Groq"><p className="eyebrow">ГОЛОСОВОЙ ВВОД</p><h2>Groq: голосовой ввод</h2><p className="text-sm text-muted mt-2">Распознавание речи для «Универсального ассистента» и «Проверки». Веб-поиск выполняет Codex через своё подключение.</p>
    <p role="status" className="file-state mt-4">{configured===null?'Проверяем подключение…':configured?'Голос подключён':'Ключ Groq не добавлен'}</p>
    <form onSubmit={async event=>{event.preventDefault();if(!key.trim()||busy)return;setBusy(true);setError(null);try{setConfigured((await voiceSettings(key)).configured)}catch(reason){setError(reason instanceof Error?reason.message:'Не удалось сохранить ключ.')}finally{setKey('');setBusy(false)}}}>
      <label className="mt-4 block text-sm font-bold">API-ключ Groq<input type="password" value={key} autoComplete="new-password" spellCheck={false} maxLength={8192} onChange={event=>setKey(event.target.value)} disabled={busy}/></label>
      <p className="text-xs text-muted mt-2">Ключ хранится на сервере. После сохранения поле очистится.</p><button className="primary mt-4" disabled={busy||!key.trim()}>{busy?'Сохраняем…':'Сохранить ключ Groq'}</button>
    </form>{error&&<p role="alert" className="error-note">{error}</p>}
  </section>
}
