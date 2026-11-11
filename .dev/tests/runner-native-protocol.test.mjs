import { test } from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { gunzipSync } from 'node:zlib'
const { executeCodex }=await import(process.env.BERVEL_CODEX_SERVER_MODULE || '../../codex-runner/server.mjs')

test('real SDK native web mode sends search capability and keeps verified provider citations', {timeout:30000}, async()=>{
 const calls=[],answer='Python release: official source.'
 const provider=http.createServer(async(request,response)=>{
  if(request.method==='GET'){response.writeHead(200,{'Content-Type':'application/json'});response.end('{"data":[]}');return}
  const chunks=[];for await(const chunk of request)chunks.push(chunk)
  let raw=Buffer.concat(chunks);if(request.headers['content-encoding']==='gzip')raw=gunzipSync(raw)
  calls.push(JSON.parse(raw))
  response.writeHead(200,{'Content-Type':'text/event-stream'})
  const event=data=>response.write(`event: ${data.type}\ndata: ${JSON.stringify(data)}\n\n`)
  const search={id:'ws_fixture',type:'web_search_call',status:'completed',action:{type:'search',query:'Python official current release'}}
  const message={id:'msg_fixture',type:'message',role:'assistant',status:'completed',content:[{type:'output_text',text:answer,annotations:[{type:'url_citation',url:'https://www.python.org/downloads/',title:'Python Downloads',start_index:0,end_index:10}]}]}
  event({type:'response.created',response:{id:'resp_fixture',model:'test-model',object:'response',status:'in_progress',output:[]}})
  for(const [index,item]of[search,message].entries()){
   event({type:'response.output_item.added',output_index:index,item:{...item,status:'in_progress'}})
   if(item.type==='message')event({type:'response.output_text.delta',item_id:item.id,output_index:index,content_index:0,delta:answer})
   event({type:'response.output_item.done',output_index:index,item})
  }
  event({type:'response.completed',response:{id:'resp_fixture',model:'test-model',object:'response',status:'completed',output:[search,message],usage:{input_tokens:10,output_tokens:10,total_tokens:20}}});response.end()
 })
 await new Promise(r=>provider.listen(0,'127.0.0.1',r))
 const root=resolve('.dev/local/assistant-capabilities-2026-10-28/native/protocol-'+process.platform);await mkdir(root,{recursive:true})
 try{
  const result=await executeCodex({home:root,cwd:root,model:'test-model',apiKey:'fixture',baseUrl:`http://127.0.0.1:${provider.address().port}/v1`},{prompt:'Search the web for current Python',web_search:true},AbortSignal.timeout(25000))
  assert.deepEqual(result,{answer,sources:[{url:'https://www.python.org/downloads/',title:'Python Downloads'}]})
  assert.equal(calls.length,1)
  assert.equal(calls[0].tool_choice,'auto')
  assert.equal((calls[0].tools||[]).some(tool=>/shell|apply_patch/.test(tool.type||tool.name||'')),false)
 }finally{await new Promise(r=>provider.close(r))}
})


