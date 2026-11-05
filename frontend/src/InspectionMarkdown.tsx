import { Children, isValidElement, useEffect, useId, useRef, useState } from 'react'
import type { ComponentProps, MouseEvent, ReactNode } from 'react'
import Markdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { downloadOriginal, readOriginalBlob } from './downloadOriginal'
import { readUploadedArtifact } from './api'
import './InspectionResults.css'
import { HtmlVisualization, MermaidVisualization } from './InspectionVisuals'

// Preview/content image links identify an artifact; neither is used directly as an image URL.
function artifactId(src: string, preview = false): string | null {
  try {
    const url = new URL(src, window.location.origin)
    if(url.origin !== window.location.origin || url.username || url.password || url.search || url.hash) return null
    const match=url.pathname.match(/^\/api\/v0\.1\/artifacts\/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\/(content|preview)$/i)
    return match&&(preview||match[2].toLowerCase()==='content')?match[1]:null
  } catch { return null }
}
const imageTypes = new Set(['image/svg+xml','image/png','image/jpeg','image/gif','image/webp','image/avif'])
function ArtifactDownloadLink({artifact,...props}:ComponentProps<'a'>&{artifact:string}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState<string|null>(null),inFlight=useRef(false)
  async function download(event:MouseEvent<HTMLAnchorElement>) {
    event.preventDefault()
    if(inFlight.current)return
    inFlight.current=true;setBusy(true);setError(null)
    try {
      const metadata = await readUploadedArtifact({ artifact_id: artifact.toLowerCase() })
      await downloadOriginal(metadata, metadata.filename)
    }
    catch(reason){setError(`Не удалось скачать файл. ${reason instanceof Error?reason.message:'Повторите скачивание.'}`)}
    finally{inFlight.current=false;setBusy(false)}
  }
  return <span><a {...props} aria-disabled={busy} aria-busy={busy} onClick={event=>void download(event)} onAuxClick={event=>{if(event.button===1)void download(event)}}/>{busy&&<span role="status" className="inspection-download-note">Скачиваем файл…</span>}{error&&<span role="alert" className="inspection-download-error">{error}</span>}</span>
}
export function ArtifactImage({id,alt}:{id:string;alt:string}) {
  const [loaded,setLoaded] = useState<{id:string;url:string;error:boolean}|null>(null)
  const url=loaded?.id===id?loaded.url:'',error=loaded?.id===id&&loaded.error
  useEffect(()=>{
    const controller=new AbortController();let current=true;let objectUrl:string|undefined
    Promise.all([readOriginalBlob({artifact_id:id},controller.signal),readUploadedArtifact({artifact_id:id})]).then(([blob,metadata])=>{
      // Content is deliberately served as an attachment/octet-stream. Always trust artifact metadata.
      const media=metadata.media_type.split(';')[0].toLowerCase()
      if(!imageTypes.has(media)) throw new Error('Unsupported image')
      if(current){objectUrl=URL.createObjectURL(new Blob([blob],{type:media}));setLoaded({id,url:objectUrl,error:false})}
    }).catch(()=>{if(current&&!controller.signal.aborted)setLoaded({id,url:'',error:true})})
    return()=>{current=false;controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl)}
  },[id])
  return <span className="inspection-image">{url&&!error&&<img src={url} alt={alt} onError={()=>setLoaded({id,url,error:true})}/>} {!url&&!error&&<span role="status">Загружаем изображение…</span>}{error&&<span role="alert">Не удалось показать изображение «{alt || 'Файл'}». Файл удалён, недоступен или имеет неподдерживаемый формат.</span>}</span>
}
const inspectionComponents: Components = {
    pre:({children})=>{
      const code=Children.toArray(children)[0]
      if(isValidElement<{className?:string;children?:ReactNode}>(code)){
        const language=code.props.className,source=String(code.props.children??'').replace(/\n$/,'')
        if(language==='language-mermaid')return <MermaidVisualization source={source}/>
        if(language==='language-html')return <HtmlVisualization html={source}/>
      }
      return <pre>{children}</pre>
    },
    img:({src,alt})=>{const id=typeof src==='string'?artifactId(src,true):null;return id?<ArtifactImage id={id} alt={alt||'Изображение BERVEL'}/>:<span className="text-muted">Изображение недоступно: разрешены только файлы BERVEL{alt?` (${alt})`:''}.</span>},
    a:({node:_node,href,...props})=>{const id=href?artifactId(href):null;return id?<ArtifactDownloadLink {...props} href={href} artifact={id}/>:<a {...props} href={href} onClick={event=>{
      // The app routes through the URL hash; document footnotes must not replace that route.
      if(href?.startsWith('#')&&!href.startsWith('#/')){
        event.preventDefault()
        let id=href.slice(1);try{id=decodeURIComponent(id)}catch{ /* A malformed fragment has no target. */ }
        const target=document.getElementById(id)
        if(target){target.scrollIntoView({block:'nearest'});target.tabIndex=-1;target.focus({preventScroll:true})}
      }
    }}/>},
    table:({children})=><div className="inspection-table-scroll"><table>{children}</table></div>,
}

export default function InspectionMarkdown({text}:{text:string}) {
  const prefix=`inspection-${useId().replace(/[^a-zA-Z0-9]/g,'')}-`
  return <div className="inspection-markdown"><Markdown skipHtml remarkPlugins={[remarkGfm]} remarkRehypeOptions={{clobberPrefix:prefix,footnoteLabel:'Примечания',footnoteBackLabel:'Вернуться к тексту'}} components={inspectionComponents}>{text}</Markdown></div>
}
