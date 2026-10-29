import { test } from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
const observer=()=>import('../../codex-runner/web-evidence.mjs')
test('observer retains provider-native evidence and citation annotations; ignores model-invented URLs',async()=>{
 const { observeWebResponses }=await observer()
 const calls=[]
 const provider=http.createServer(async(request,response)=>{
  const chunks=[];for await(const chunk of request)chunks.push(chunk)
  calls.push({path:request.url,authorization:request.headers.authorization,body:JSON.parse(Buffer.concat(chunks))})
  const search={id:'search',type:'web_search_call',status:'completed',action:{type:'search',queries:['official example'],sources:[{type:'url',url:'https://example.com/verified'}]}}
  const message={type:'message',content:[{type:'output_text',text:'Untrusted https://invented.invalid',annotations:[{type:'url_citation',url:'https://example.com/verified',title:'Verified title'}]}]}
  response.writeHead(200,{'Content-Type':'text/event-stream'})
  for(const item of [search,message])response.write(`data: ${JSON.stringify({type:'response.output_item.done',item})}\n\n`)
  response.end('data: [DONE]\n\n')
 })
 await new Promise(r=>provider.listen(0,'127.0.0.1',r))
 try{
  const evidence=await observeWebResponses({baseUrl:`http://127.0.0.1:${provider.address().port}/v1`,apiKey:'fixture-private'},AbortSignal.timeout(3000))
  try{
   const response=await fetch(evidence.baseUrl+'/responses',{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${evidence.token}`},body:JSON.stringify({tools:[{type:'web_search'}]})})
   assert.equal(response.status,200);assert.ok((await response.text()).includes('web_search_call'))
   assert.equal(evidence.searched,true)
   assert.deepEqual(evidence.sources(),[{url:'https://example.com/verified',title:'Verified title'}])
   assert.equal(calls[0].authorization,'Bearer fixture-private');assert.equal(calls[0].path,'/v1/responses')
   assert.equal((await fetch(evidence.baseUrl+'/outside')).status,404)
  }finally{await evidence.close()}
 }finally{await new Promise(r=>provider.close(r))}
})
test('observer does not accept failed searches or citation-free responses as evidence',async()=>{
 const { collectWebEvidence }=await observer()
 for(const output of [[{type:'web_search_call',status:'failed'}],[{type:'message',content:[{type:'output_text',text:'I searched https://example.com'}]}]]){
  const evidence=collectWebEvidence();evidence.accept({response:{output}})
  assert.equal(evidence.searched,false);assert.deepEqual(evidence.sources(),[])
 }
})

test('observer preserves native SDK tool selection and gzip body bytes instead of inventing a hosted tool',async()=>{
 const { observeWebResponses }=await observer()
 const { gzipSync }=await import('node:zlib')
 const original=Buffer.from(JSON.stringify({model:'test',tool_choice:'auto',input:[]}))
 const compressed=gzipSync(original)
 let supplied,encoding
 const provider=http.createServer(async(request,response)=>{
  const chunks=[];for await(const chunk of request)chunks.push(chunk)
  supplied=Buffer.concat(chunks);encoding=request.headers['content-encoding']
  response.writeHead(200,{'Content-Type':'text/event-stream'});response.end('data: [DONE]\n\n')
 })
 await new Promise(r=>provider.listen(0,'127.0.0.1',r))
 const evidence=await observeWebResponses({baseUrl:`http://127.0.0.1:${provider.address().port}/v1`,apiKey:'fixture'},AbortSignal.timeout(3000))
 try{
  const response=await fetch(evidence.baseUrl+'/responses',{method:'POST',headers:{Authorization:`Bearer ${evidence.token}`,'Content-Type':'application/json','Content-Encoding':'gzip'},body:compressed})
  await response.text()
  assert.equal(encoding,'gzip')
  assert.deepEqual(supplied,compressed)
 }finally{await evidence.close();provider.closeAllConnections();await new Promise(r=>provider.close(r))}
})

test('standalone native search results are observed without parsing URLs from model or search prose',async()=>{
 const { observeWebResponses }=await observer()
 const supplied={id:'search-fixture',model:'test',input:[],commands:[],settings:{},max_output_tokens:1000}
 const native={encrypted_output:'opaque-native',output:'Untrusted prose https://invented.invalid',results:[
  {type:'text_result',ref_id:'turn0search0',domain:'python.org',title:'Python Source Releases',url:'https://www.python.org/downloads/source/'},
  {type:'text_result',ref_id:'turn0search1',domain:'invalid',title:'Unsafe',url:'javascript:alert(1)'},
  {type:'unknown_result',title:'Not a native text result',url:'https://invented.invalid'},
 ]}
 let body,path,authorization
 const provider=http.createServer(async(request,response)=>{
  const chunks=[];for await(const chunk of request)chunks.push(chunk)
  body=JSON.parse(Buffer.concat(chunks));path=request.url;authorization=request.headers.authorization
  response.writeHead(200,{'Content-Type':'application/json'});response.end(JSON.stringify(native))
 })
 await new Promise(r=>provider.listen(0,'127.0.0.1',r))
 const evidence=await observeWebResponses({baseUrl:`http://127.0.0.1:${provider.address().port}/codex`,apiKey:'fixture-native'},AbortSignal.timeout(3000))
 try{
  const response=await fetch(evidence.baseUrl+'/alpha/search',{method:'POST',headers:{Authorization:`Bearer ${evidence.token}`,'Content-Type':'application/json'},body:JSON.stringify(supplied)})
  assert.equal(response.status,200)
  assert.deepEqual(await response.json(),native)
  assert.deepEqual(body,supplied);assert.equal(path,'/codex/alpha/search');assert.equal(authorization,'Bearer fixture-native')
  assert.equal(evidence.searched,true)
  assert.deepEqual(evidence.sources(),[{url:'https://www.python.org/downloads/source/',title:'Python Source Releases'}])
 }finally{await evidence.close();provider.closeAllConnections();await new Promise(r=>provider.close(r))}
})

test('failed or malformed standalone native search does not become evidence and a rejected search is not replayed',async()=>{
 const { observeWebResponses }=await observer()
 for(const [status,body]of[[429,'{"error":{"message":"private"}}'],[200,'{"output":"https://invented.invalid"}']]){
  let calls=0
  const provider=http.createServer((_request,response)=>{calls++;response.writeHead(status,{'Content-Type':'application/json'});response.end(body)})
  await new Promise(r=>provider.listen(0,'127.0.0.1',r))
  const evidence=await observeWebResponses({baseUrl:`http://127.0.0.1:${provider.address().port}`,apiKey:'fixture'},AbortSignal.timeout(3000))
  try{
   const response=await fetch(evidence.baseUrl+'/alpha/search',{method:'POST',headers:{Authorization:`Bearer ${evidence.token}`,'Content-Type':'application/json'},body:'{}'})
   await response.text()
   assert.equal(calls,1)
   assert.equal(evidence.searched,false);assert.deepEqual(evidence.sources(),[])
   if(status===429)assert.equal(evidence.failure.status,429)
  }finally{await evidence.close();provider.closeAllConnections();await new Promise(r=>provider.close(r))}
 }
})

test('hosted native sources outside message annotations are retained only for a completed search',async()=>{
 const { collectWebEvidence }=await observer()
 const evidence=collectWebEvidence()
 evidence.accept({item:{type:'web_search_call',status:'in_progress',action:{type:'search',sources:[{type:'url',url:'https://invented.invalid'}]}}})
 assert.deepEqual(evidence.sources(),[])
 evidence.accept({item:{type:'web_search_call',status:'completed',action:{type:'search',sources:[{type:'url',url:'https://example.com/native',title:'Native'}]}}})
 assert.equal(evidence.searched,true);assert.deepEqual(evidence.sources(),[{url:'https://example.com/native',title:'Native'}])
})
