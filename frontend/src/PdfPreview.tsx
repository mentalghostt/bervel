import {useEffect,useRef,useState} from 'react'
import {getDocument,GlobalWorkerOptions} from 'pdfjs-dist'
import type {PDFDocumentProxy,RenderTask} from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

GlobalWorkerOptions.workerSrc=workerUrl
const button='rounded-lg border border-line bg-white px-3 py-2 text-xs font-bold text-accent disabled:opacity-40'

export default function PdfPreview({url,filename}:{url:string;filename:string}){
 const [document,setDocument]=useState<PDFDocumentProxy|null>(null),[page,setPage]=useState(1)
 const [width,setWidth]=useState(800),[rendered,setRendered]=useState(false),[error,setError]=useState<string|null>(null),[retry,setRetry]=useState(0)
 const container=useRef<HTMLDivElement>(null),canvas=useRef<HTMLCanvasElement>(null)
 useEffect(()=>{
  const element=container.current;if(!element)return
  const observer=new ResizeObserver(([entry])=>setWidth(Math.max(200,Math.floor(entry.contentRect.width))))
  observer.observe(element);return()=>observer.disconnect()
 },[])
 useEffect(()=>{
  let active=true
  setDocument(null);setPage(1);setRendered(false);setError(null)
  const base=new URL('/pdfjs/',window.location.href).href
  // Core canvas rendering: no scripting/annotation handlers or third-party requests.
  const loading=getDocument({url,cMapUrl:base+'cmaps/',standardFontDataUrl:base+'standard_fonts/',wasmUrl:base+'wasm/',iccUrl:base+'iccs/',
   enableXfa:false,maxImageSize:16000000,canvasMaxAreaInBytes:16000000})
  void loading.promise.then(pdf=>{if(active)setDocument(pdf)}).catch(()=>{if(active)setError('Не удалось прочитать PDF. Он может быть повреждён или защищён паролем. Скачайте оригинал.')})
  return()=>{active=false;void loading.destroy()}
 },[url,retry])
 useEffect(()=>{
  if(!document||!canvas.current)return
  let active=true;let task:RenderTask|undefined
  setRendered(false);setError(null)
  void(async()=>{
   try{
    const source=await document.getPage(page);if(!active||!canvas.current)return
    const raw=source.getViewport({scale:1})
    const scale=Math.min(Math.min(width*window.devicePixelRatio,1600)/raw.width,4096/raw.height,Math.sqrt(4000000/(raw.width*raw.height)))
    const viewport=source.getViewport({scale})
    canvas.current.width=Math.ceil(viewport.width);canvas.current.height=Math.ceil(viewport.height)
    task=source.render({canvas:canvas.current,viewport})
    await task.promise;if(active)setRendered(true)
   }catch{if(active)setError('Не удалось отобразить страницу PDF. Попробуйте открыть файл заново или скачайте оригинал.')}
  })()
  return()=>{active=false;task?.cancel()}
 },[document,page,width])
 return <div ref={container} className="min-w-0">
  {document&&<div className="mb-3 flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted">Страница {page} из {document.numPages}</p><div className="flex gap-2"><button className={button} disabled={page<=1} onClick={()=>setPage(p=>p-1)}>Предыдущая страница</button><button className={button} disabled={page>=document.numPages} onClick={()=>setPage(p=>p+1)}>Следующая страница</button></div></div>}
  {error?<div className="rounded-lg bg-red-50 p-4"><p role="alert" className="text-sm leading-6 text-red-800">{error}</p><button className={`${button} mt-3`} onClick={()=>setRetry(n=>n+1)}>Повторить PDF</button></div>:<>
   {!rendered&&<p role="status" className="mb-3 text-sm text-muted">Отображаем страницу PDF…</p>}
   <div className="rounded-lg border border-line bg-paper p-2"><canvas ref={canvas} role="img" aria-label={`Страница ${page} PDF: ${filename}`} data-rendered={rendered} className={`block h-auto max-w-full bg-white ${rendered?'':'invisible'}`}/></div>
  </>}
 </div>
}
