// Run after generation: node .dev/scripts/code-map-test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync}=require('node:child_process');
const root = path.resolve(__dirname, '../..');
const htmlPath = path.join(root, '.dev/visualizations/bervel-code-map.html');
assert.ok(fs.existsSync(htmlPath), 'Self-contained code map has not been generated');
const html = fs.readFileSync(htmlPath, 'utf8');
const data = JSON.parse(html.match(/<script id="map-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const ids = new Map(data.nodes.map(n => [n.id,n]));
for(const name of ['ModuleRegistry','ModuleDefinition','default_registry','CsvMemory','ChunkRepository','retrieve','latest_csv','save_chunks'])assert.ok(!data.nodes.some(n=>n.name===name),`Removed runtime entity remains active: ${name}`);
assert.ok(!data.nodes.some(n=>n.file?.endsWith('/manifest.json')||n.file==='src/bervel/core/registry.py'),'Deleted registry/manifests must not remain as active files');
const lookup=(file,name,kind)=>data.nodes.find(n=>n.file===file&&n.name===name&&(!kind||n.kind===kind));
assert.ok(!lookup('src/bervel/app_setup.py','default_schemas','function'),'Educational schema registration is retired');
assert.ok(!data.nodes.some(n=>n.file?.includes('/modules/document_review/')||n.file==='src/bervel/core/processes.py'),'Educational runtime is absent');
assert.ok(lookup('src/bervel/modules/chat/service.py','select_sources','function'),'Chat owns source selection');
const commonRead=lookup('src/bervel/core/csv_memory.py','read_csv','function');
const commonParse=lookup('src/bervel/core/csv_memory.py','parse_csv','function');
const csvPreview=lookup('src/bervel/core/infrastructure/file_preview.py','preview','function');
const documentExtract=lookup('src/bervel/core/infrastructure/document_reader.py','extract','method');
assert.ok(commonRead&&commonParse&&csvPreview&&documentExtract,'Shared CSV decoding and extraction are indexed');
assert.ok(data.edges.some(e=>e.from===csvPreview.id&&e.to===commonRead.id&&e.kind==='calls'),'Preview -> shared CSV reader');
assert.ok(data.edges.some(e=>e.from===commonParse.id&&e.to===commonRead.id&&e.kind==='calls'),'CSV chunks -> shared CSV reader');
assert.ok(data.edges.some(e=>e.from===documentExtract.id&&e.to===commonParse.id&&e.kind==='calls'),'Document indexer -> CSV chunks');
assert.equal(lookup('src/bervel/core/infrastructure/documents.py','INDEX_VERSION','constant').value,'4','Current derived index version');
assert.equal(ids.size, data.nodes.length, 'IDs must be unique');
for(const n of data.nodes) {
  if(n.parent) assert.ok(ids.has(n.parent), `Missing parent ${n.id}`);
  if(n.file) {
    assert.ok(data.files[n.file], n.file);
    assert.ok(n.line >= 1 && n.end >= n.line && n.end <= data.files[n.file].source.split('\n').length, n.id);
  }
}
for(const e of data.edges) {
  assert.ok(ids.has(e.from) && ids.has(e.to), JSON.stringify(e));
  assert.notEqual(e.from,e.to, 'Self loops add no useful navigation');
}
for(const file of Object.keys(data.files)) {
  assert.ok(!/node_modules|\.env|\.dev\/local|credentials/i.test(file), file);
  const actual = fs.readFileSync(path.join(root,file),'utf8');
  assert.equal(data.files[file].source, actual, `Outdated source ${file}`);
}
function walk(dir) {return fs.readdirSync(dir,{withFileTypes:true}).flatMap(e => e.isDirectory() && !['node_modules','dist','__pycache__'].includes(e.name) ? walk(path.join(dir,e.name)) : e.isFile()? [path.join(dir,e.name)] : []);}
const runtime = ['src/bervel','frontend/src','codex-runner'].flatMap(d=>walk(path.join(root,d))).filter(f=>/\.(py|tsx?|mjs|css|json)$/.test(f)&&!f.endsWith('package-lock.json')).map(f=>path.relative(root,f).replaceAll('\\','/'));
for(const f of runtime) assert.ok(data.files[f], `Coverage missing: ${f}`);
const main = data.nodes.find(n=>n.file==='src/bervel/__main__.py'&&n.name==='main'&&n.kind==='function');
const settings = data.nodes.find(n=>n.file==='src/bervel/core/config.py'&&n.name==='from_environment');
assert.ok(data.edges.some(e=>e.from===main.id&&e.to===settings.id&&e.kind==='calls'), 'main -> Settings.from_environment');
assert.ok(data.edges.some(e=>e.kind==='automatic'&&ids.get(e.to).name==='__post_init__'), 'Dataclass post-init must be explicitly automatic');
assert.ok(data.nodes.some(n=>n.kind==='parameter'));
assert.ok(data.nodes.some(n=>n.kind==='variable'));
assert.ok(data.nodes.some(n=>n.file==='frontend/src/App.tsx'&&n.name==='view'&&n.kind==='variable'), 'Destructured state');
assert.ok(data.nodes.some(n=>n.file==='src/bervel/core/config.py'&&n.name==='port'&&n.kind==='field'));
assert.ok(data.nodes.filter(n=>n.name==='settings').length>1,'Scoped names must not be collapsed');
const markupOnly=html.replace(/(<script\b[^>]*>)[\s\S]*?<\/script>/gi,'$1</script>');
assert.ok(!/<script[^>]+src=|<link[^>]+href=/i.test(markupOnly), 'No external assets');
assert.ok(html.includes("connect-src 'none'"),'Network connections are blocked by CSP');
console.log(JSON.stringify({files:Object.keys(data.files).length,nodes:data.nodes.length,edges:data.edges.length,functions:data.nodes.filter(n=>['function','method'].includes(n.kind)).length,variables:data.nodes.filter(n=>['variable','field','parameter','constant'].includes(n.kind)).length,coverage:runtime.length,errors:data.errors},null,2));
assert.equal(data.errors.length,0,'Parser errors');
// The first snapshot used universal newlines for Python. Accept that original
// representation explicitly; source embedded in the final HTML is byte-exact UTF-8.
const refreshBaseline=path.join(root,'.dev/visualizations/code-map-checks/refresh-before.json');
const baselinePath=process.env.CODE_MAP_BASELINE||(fs.existsSync(refreshBaseline)?refreshBaseline:path.join(root,'.dev/visualizations/code-map-checks/source-before.json'));
const baseline=JSON.parse(fs.readFileSync(baselinePath,'utf8'));
const sha=s=>crypto.createHash('sha256').update(s).digest('hex');
for(const[f,hash]of Object.entries(baseline.hashes)){
  const source=fs.readFileSync(path.join(root,f),'utf8');
  assert.ok(sha(source)===hash || baseline.newlineMode!=='exact'&&f.endsWith('.py')&&sha(source.replaceAll('\r\n','\n'))===hash,'Source modified during task: '+f);
}
const git=args=>execFileSync('git',args,{cwd:root,encoding:'utf8'}).trim();
assert.equal(git(['rev-parse','HEAD']),baseline.meta.head,'HEAD unchanged');
assert.equal(git(['branch','--show-current']),baseline.meta.branch,'Branch unchanged');
assert.equal(git(['diff','--cached','--raw']),baseline.index,'Index unchanged');
const before=baseline.status.split('\n').filter(Boolean),after=git(['status','--porcelain=v1']).split('\n').filter(Boolean);
// Leading spaces in the first status entry are trimmed by the snapshot command.
for(const original of before)assert.ok(after.some(s=>s.trimStart()===original.trimStart()),'Pre-existing status changed: '+original);
const newStatuses=after.filter(s=>!before.some(b=>b.trimStart()===s.trimStart()));
for(const status of newStatuses)assert.match(status,/\.dev\/(scripts\/(?:code-map|build-code-map|vendor\/)|visualizations\/)/,'Unexpected modification: '+status);
const moduleOf=id=>{let n=ids.get(id);while(n&&n.parent!=='root')n=ids.get(n.parent);return n?.id;};
assert.equal(data.edges.filter(e=>['imports','calls','method_ref'].includes(e.kind)&&moduleOf(e.from)==='group:core'&&['group:chat','group:tender_radar','group:document_review'].includes(moduleOf(e.to))).length,0,'Core must not acquire invented dependencies on business modules');
const repositoryField=data.nodes.find(n=>n.file==='src/bervel/modules/chat/service.py'&&n.kind==='field'&&n.name==='repository');
assert.equal(repositoryField.value,'repository','Tuple assignment must bind its individual value');
assert.ok(data.edges.some(e=>e.from===repositoryField.id&&e.kind==='uses'&&ids.get(e.to).kind==='parameter'),'Field -> constructor parameter');
assert.ok(data.nodes.some(n=>n.file.endsWith('App.tsx')&&n.kind==='function'&&n.name.startsWith('λ@')),'Callbacks extracted');
for(const n of data.nodes)if(n.parent&&n.file&&ids.get(n.parent).file===n.file){const parent=ids.get(n.parent);if(!['field','variable','constant'].includes(n.kind))assert.ok(n.line>=parent.line&&n.end<=parent.end,'Definition must lie inside lexical parent: '+n.id);}
const report={files:Object.keys(data.files).length,nodes:data.nodes.length,edges:data.edges.length,functions:data.nodes.filter(n=>['function','method'].includes(n.kind)).length,variables:data.nodes.filter(n=>['variable','field','parameter','constant'].includes(n.kind)).length,baselineFiles:Object.keys(baseline.hashes).length,sourceUnchanged:true,branchUnchanged:true,headUnchanged:true,indexUnchanged:true,newStatuses,errors:data.errors};
fs.writeFileSync(path.join(root,'.dev/visualizations/code-map-checks/integrity-report.json'),JSON.stringify(report,null,2));
console.log('Integrity, coverage, scoped bindings and preservation: PASS');
