import {Component,lazy,Suspense,useEffect,useRef,useState} from 'react'
import type {ReactNode} from 'react'
import type {UploadedArtifact} from './api'
import {readPreview} from './previewApi'
import type {FilePreview as Preview} from './previewApi'
import {downloadOriginal,readOriginalBlob} from './downloadOriginal'

const button='rounded-lg border border-line bg-white px-4 py-2.5 text-sm font-bold text-accent disabled:opacity-50'
const PdfPreview=lazy(()=>import('./PdfPreview'))

class PdfBoundary extends Component<{children:ReactNode},{failed:boolean}>{
 state={failed:false}
 static getDerivedStateFromError(){return {failed:true}}
 render(){
  if(this.state.failed)return <div className="rounded-lg bg-red-50 p-4"><p role="alert" className="text-sm leading-6 text-red-800">Не удалось загрузить просмотр PDF. Оригинал доступен для скачивания. Обновите страницу и выберите файл снова.</p><button className={`${button} mt-3`} onClick={()=>window.location.reload()}>Обновить страницу</button></div>
  return this.props.children
 }
}

export default function FilePreview({file,toolbarLeading,showDownload=true}:{file:UploadedArtifact;toolbarLeading?:ReactNode;showDownload?:boolean}){
 const [view,setView]=useState<Preview|null>(null),[url,setUrl]=useState<string|null>(null)
 const [loading,setLoading]=useState(true),[error,setError]=useState<string|null>(null),[retry,setRetry]=useState(0)
 const [more,setMore]=useState(false),[downloading,setDownloading]=useState(false),[downloadError,setDownloadError]=useState<string|null>(null)
 const active=useRef<AbortController|null>(null)
 useEffect(()=>{
  const controller=new AbortController();active.current=controller
  let objectUrl:string|undefined
  void(async()=>{
   setLoading(true);setError(null);setView(null);setUrl(null)
   try{
    const value=await readPreview(file,0,controller.signal)
    if(value.kind==='image'||value.kind==='pdf'){
     const blob=await readOriginalBlob(file,controller.signal)
     if(controller.signal.aborted)return
     objectUrl=URL.createObjectURL(new Blob([blob],{type:value.media_type}));setUrl(objectUrl)
    }
    if(!controller.signal.aborted)setView(value)
   }catch(reason){if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:'Не удалось открыть файл.')}
   finally{if(!controller.signal.aborted)setLoading(false)}
  })()
  return()=>{controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl)}
 },[file,retry])
 async function loadMore(){
  if(!view||view.next_cursor===null||more)return
  const current=active.current;setMore(true);setError(null)
  try{const next=await readPreview(file,view.next_cursor,current?.signal);if(!current?.signal.aborted)setView({...next,rows:[...view.rows,...next.rows]})}
  catch(reason){if(!current?.signal.aborted)setError(reason instanceof Error?reason.message:'Не удалось получить строки.')}
  finally{if(!current?.signal.aborted)setMore(false)}
 }
 return <div className="file-preview mt-5 min-w-0">
  {(toolbarLeading||showDownload)&&<div className="preview-toolbar">{toolbarLeading}{showDownload&&<button disabled={downloading} className={button} onClick={()=>{
   setDownloading(true);setDownloadError(null);void downloadOriginal(file,file.filename).catch(reason=>setDownloadError(reason instanceof Error&&reason.message==='Файл удалён.'?reason.message:'Не удалось скачать оригинал.')).finally(()=>setDownloading(false))
  }}>{downloading?'Скачиваем…':'Скачать оригинал'}</button>}</div>}
  {downloadError&&<p role="alert" className="mb-3 text-sm text-red-800">{downloadError}</p>}
  {loading&&<p role="status" className="py-10 text-sm text-muted">Открываем содержимое…</p>}
  {error&&<div className="my-4 rounded-lg bg-red-50 p-4 text-sm"><p role="alert" className="text-red-800">{error}</p><button className={`${button} mt-3`} onClick={()=>{if(view?.kind==='table'&&view.next_cursor!==null){void loadMore()}else{setRetry(v=>v+1)}}}>Повторить просмотр</button></div>}
  {view?.notice&&view.notice!=='Текстовое представление; HTML и команды не выполняются.'&&<p className="mb-4 rounded-lg bg-paper p-4 text-sm leading-6 text-muted">{view.notice}</p>}
  {view?.kind==='table'&&<>
   <p className="mb-3 text-xs text-muted">Показано {view.rows.length} из {view.total_rows} строк · {view.columns.length} колонок</p>
   <div role="region" aria-label="Содержимое CSV" tabIndex={0} className="overflow-x-auto rounded-lg border border-line focus-visible:outline-2 focus-visible:outline-accent">
    <table className="w-full text-left text-sm"><thead className="sticky top-0 bg-paper text-xs text-muted"><tr><th className="border-b border-line px-3 py-3">№</th>{view.columns.map((column,i)=><th scope="col" key={i} className="min-w-32 border-b border-line px-3 py-3">{column}</th>)}</tr></thead>
     <tbody className="divide-y divide-line">{view.rows.map((row,i)=><tr key={i}><td className="px-3 py-3 align-top text-muted tabular-nums">{i+1}</td>{row.map((cell,j)=><td key={j} className="px-3 py-3 align-top"><div className="max-w-96 min-w-32 whitespace-pre-wrap wrap-anywhere">{cell}</div></td>)}</tr>)}</tbody></table>
   </div>
   {view.next_cursor!==null&&<button className={`${button} mt-4`} disabled={more} onClick={()=>{void loadMore()}}>{more?'Читаем…':'Показать ещё строки'}</button>}
  </>}
  {!error&&view?.kind==='image'&&url&&<div className="flex items-center justify-center rounded-lg border border-line bg-paper p-3"><img src={url} alt={file.filename} className="max-h-[78vh] max-w-full object-contain" onError={()=>setError('Изображение повреждено или не поддерживается браузером. Скачайте оригинал.')}/></div>}
  {!error&&view?.kind==='pdf'&&url&&<PdfBoundary><Suspense fallback={<p role="status" className="py-6 text-sm text-muted">Загружаем просмотр PDF…</p>}><PdfPreview url={url} filename={file.filename}/></Suspense></PdfBoundary>}
  {view?.kind==='text'&&<pre className="rounded-lg border border-line bg-paper p-4 text-sm leading-7 whitespace-pre-wrap wrap-anywhere">{view.text}</pre>}
 </div>
}
