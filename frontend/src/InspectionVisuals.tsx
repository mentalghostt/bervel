import { useEffect, useId, useMemo, useState } from 'react'
import mermaidFallbackUrl from 'mermaid/dist/mermaid.min.js?url'
import { readUploadedArtifact } from './api'
import { readOriginalBlob } from './downloadOriginal'

const policy="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'"

export function HtmlVisualization({html,title='Интерактивный график'}:{html:string;title?:string}) {
 const document=useMemo(()=>`<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${policy}"></head><body>${html}</body></html>`,[html])
 return <figure className="inspection-visual" aria-label={title}>
  <iframe className="inspection-html-frame" title={title} sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={document}/>
  <details className="inspection-visual-source"><summary>Исходный HTML</summary><pre><code>{html}</code></pre></details>
 </figure>
}

export function HtmlArtifact({id,title}:{id:string;title:string}) {
 const [loaded,setLoaded]=useState<{id:string;html?:string;error?:string}|null>(null)
 useEffect(()=>{
  const controller=new AbortController();let current=true
  void(async()=>{
   try{
    const metadata=await readUploadedArtifact({artifact_id:id})
    if(metadata.media_type.split(';')[0].toLowerCase()!=='text/html')throw new Error('Этот файл не является HTML-визуализацией.')
    const html=await (await readOriginalBlob({artifact_id:id},controller.signal)).text()
    if(current)setLoaded({id,html})
   }catch(reason){if(current&&!controller.signal.aborted)setLoaded({id,error:reason instanceof Error?reason.message:'Не удалось загрузить HTML-визуализацию.'})}
  })()
  return()=>{current=false;controller.abort()}
 },[id])
 if(loaded?.id!==id)return <p role="status">Загружаем интерактивный график…</p>
 if(loaded.error)return <p role="alert" className="inspection-visual-error">{loaded.error}</p>
 return <HtmlVisualization html={loaded.html||''} title={`Интерактивный график: ${title}`}/>
}

type Mermaid = typeof import('mermaid')['default']
let renderer: Promise<Mermaid> | undefined
let useStandaloneRenderer = false

function loadStandaloneMermaid(): Promise<Mermaid> {
 return new Promise((resolve,reject)=>{
  const script=document.createElement('script')
  script.src=mermaidFallbackUrl
  script.onload=()=>{
   script.remove()
   const mermaid=(window as Window & {mermaid?: Mermaid}).mermaid
   if(mermaid) resolve(mermaid)
   else reject(new Error('Standalone Mermaid did not initialize'))
  }
  script.onerror=()=>{script.remove();reject(new Error('Standalone Mermaid failed to load'))}
  document.head.append(script)
 })
}

function mermaidRenderer() {
 return renderer??=(useStandaloneRenderer ? loadStandaloneMermaid() : import('mermaid').then(module=>module.default))
  .then(mermaid=>{
   mermaid.initialize({startOnLoad:false,securityLevel:'strict',suppressErrorRendering:true,theme:'default',htmlLabels:false})
   return mermaid
  })
}

export function MermaidVisualization({source}:{source:string}) {
 const id=useId().replace(/[^a-zA-Z0-9]/g,'')
 const [revision,setRevision]=useState(0)
 const [state,setState]=useState<{source:string;revision:number;url?:string;error?:'load'|'render'|'image'}|null>(null)
 useEffect(()=>{
  let current=true,url:string|undefined,host:HTMLDivElement|undefined
  const attempt=mermaidRenderer()
  let loaded=false
  void(async()=>{
   try{
    const mermaid=await attempt
    loaded=true
    if(!current)return
    host=document.createElement('div');host.style.cssText='position:absolute;left:-10000px;top:0;width:800px;visibility:hidden';document.body.append(host)
    const {svg}=await mermaid.render(`inspection-mermaid-${id}-${revision}`,source,host)
    const parsed=new DOMParser().parseFromString(svg,'image/svg+xml')
    if(parsed.querySelector('parsererror')||parsed.documentElement.localName!=='svg')throw new Error('Invalid Mermaid SVG')
    const graphic=parsed.documentElement
    const [, ,width,height]=(graphic.getAttribute('viewBox')||'').trim().split(/[\s,]+/).map(Number)
    if(Number.isFinite(width)&&Number.isFinite(height)&&width>0&&height>0){
      graphic.setAttribute('width',String(width));graphic.setAttribute('height',String(height))
    }
    if(current){url=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(graphic)],{type:'image/svg+xml'}));setState({source,revision,url})}
   }catch(reason){
    const loadFailed=!loaded || (reason instanceof Error && /dynamically imported module|importing a module script failed|unable to preload css/i.test(reason.message))
    if(loadFailed && renderer===attempt){
     // Browsers remember failed module requests for the life of the document.
     useStandaloneRenderer=true
     renderer=undefined
    }
    if(current)setState({source,revision,error:loadFailed?'load':'render'})
   }
   finally{host?.remove()}
  })()
  return()=>{current=false;host?.remove();if(url)URL.revokeObjectURL(url)}
 },[source,id,revision])
 const current=state?.source===source&&state.revision===revision?state:null
 return <figure className="inspection-visual" aria-label="Схема Mermaid">
  {!current&&<p role="status">Строим схему…</p>}
  {current?.url&&<img className="inspection-mermaid-image" src={current.url} alt="Диаграмма Mermaid" onError={()=>setState({source,revision,error:'image'})}/>}
  {current?.error==='load'&&<>
   <p role="alert" className="inspection-visual-error">Не удалось загрузить Mermaid.</p>
   <button type="button" className="secondary" onClick={()=>setRevision(value=>value+1)}>Повторить загрузку</button>
  </>}
  {current?.error==='render'&&<p role="alert" className="inspection-visual-error">Не удалось построить Mermaid-схему. Проверьте исходный код схемы.</p>}
  {current?.error==='image'&&<p role="alert" className="inspection-visual-error">Не удалось показать готовую Mermaid-схему.</p>}
  <details className="inspection-visual-source" open={current?.error==='render'||current?.error==='image'}><summary>Исходный код схемы</summary><pre><code>{source}</code></pre></details>
 </figure>
}
