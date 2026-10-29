import {cpSync,mkdirSync} from 'node:fs'
import {fileURLToPath} from 'node:url'

const target=new URL('../public/pdfjs/',import.meta.url)
mkdirSync(target,{recursive:true})
for(const folder of ['cmaps','standard_fonts','wasm','iccs']){
 cpSync(fileURLToPath(new URL('../node_modules/pdfjs-dist/'+folder,import.meta.url)),fileURLToPath(new URL(folder,target)),{recursive:true})
}
