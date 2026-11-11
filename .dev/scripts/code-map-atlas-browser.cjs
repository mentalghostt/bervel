// Offline UI regressions. Test artifacts and browser profile stay inside the project.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const {chromium}=require(path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'../..'),folder=path.join(root,'.dev/local/code-map-mermaid/checks');
fs.mkdirSync(folder,{recursive:true});
const candidates=[process.env.CODE_MAP_BROWSER,'C:/Program Files/Google/Chrome/Application/chrome.exe','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',chromium.executablePath()].filter(Boolean);
const executablePath=candidates.find(p=>fs.existsSync(p));
const checks=[],errors=[],requests=[],geometry=[];
let context;
(async()=>{try{
 context=await chromium.launchPersistentContext(path.join(folder,'profile'),{executablePath,headless:true,viewport:{width:1440,height:1000},env:{...process.env,TMP:folder,TEMP:folder},args:['--disable-background-networking','--disable-component-update','--no-first-run']});
 await context.setOffline(true);
 const page=context.pages()[0]||await context.newPage();page.setDefaultTimeout(15000);
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});page.on('request',r=>requests.push(r.url()));
 const wait=async()=>{await page.waitForFunction(()=>document.querySelector('#viewport')?.getAttribute('aria-busy')==='false');assert.equal(await page.locator('.diagram-error').count(),0);};
 const check=name=>{checks.push(name);console.log('PASS '+name);};
 const choose=async name=>{await page.getByRole('button',{name:'Выбрать '+name,exact:true}).click();};
 const enter=async name=>{await choose(name);await page.locator('#details').getByRole('button',{name:/^Внутрь:/}).click();await wait();};
 const audit=async name=>{
   const result=await page.evaluate(()=>{
     const boxes=[...document.querySelectorAll('#diagram g.node')].map(e=>({id:e.dataset.node,rect:e.getBoundingClientRect().toJSON()}));
     const overlaps=[];for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){const a=boxes[i].rect,b=boxes[j].rect;if(Math.min(a.right,b.right)>Math.max(a.left,b.left)+1&&Math.min(a.bottom,b.bottom)>Math.max(a.top,b.top)+1)overlaps.push([boxes[i].id,boxes[j].id]);}
     const crossings=[];
     for(const p of document.querySelectorAll('#diagram path.flowchart-link')){
       const length=p.getTotalLength(),matrix=p.getScreenCTM();
       for(let d=4;d<length-4;d+=5){const local=p.getPointAtLength(d),point=new DOMPoint(local.x,local.y).matrixTransform(matrix);
         for(const n of boxes){const r=n.rect;if(point.x>r.left+3&&point.x<r.right-3&&point.y>r.top+3&&point.y<r.bottom-3){crossings.push({edge:p.id,node:n.id});break;}}
       }
     }
     const labelBoxes=[...document.querySelectorAll('#diagram g.edgeLabel')].map(e=>({text:e.textContent,rect:e.getBoundingClientRect()}));
     const labelOverlaps=[];for(let i=0;i<labelBoxes.length;i++)for(let j=i+1;j<labelBoxes.length;j++){const a=labelBoxes[i].rect,b=labelBoxes[j].rect;if(Math.min(a.right,b.right)>Math.max(a.left,b.left)+2&&Math.min(a.bottom,b.bottom)>Math.max(a.top,b.top)+2)labelOverlaps.push([labelBoxes[i].text,labelBoxes[j].text]);}
     return{count:boxes.length,overlaps,labelOverlaps,crossings:[...new Map(crossings.map(c=>[c.edge+'|'+c.node,c])).values()],overflow:document.documentElement.scrollWidth>innerWidth};
   });geometry.push({name,...result});assert.deepEqual(result.overlaps,[],name+' overlaps');assert.deepEqual(result.crossings,[],name+' edges through nodes');assert.equal(result.overflow,false,name+' overflow');
 };
 await page.goto(pathToFileURL(path.join(root,'.dev/visualizations/bervel-code-map.html')).href);
 await wait();await audit('code overview');assert.equal(await page.locator('#diagram [data-node]').count(),7);
 await page.screenshot({path:path.join(folder,'code-overview.png')});check('Offline Mermaid overview, 7 source groups, no overlapping nodes');
 await choose('Запуск и сборка');await page.getByRole('button',{name:'Как запускается serve',exact:true}).click();await wait();
 assert.equal(await page.locator('#diagram [data-node]').count(),7);await choose('build_services');assert.match(await page.locator('#details').innerText(),/ApplicationServices/);
 await choose('main');await page.reload();await wait();assert.equal(await page.locator('#details h2').innerText(),'main');
 await page.screenshot({path:path.join(folder,'startup.png')});check('serve is inside launch node; current assembly and deep link preserved');
 await page.getByRole('button',{name:'Всё приложение',exact:true}).click();await wait();await enter('Чат');await enter('service.py');await enter('ChatService');
 await choose('__init__');await page.locator('#details').getByRole('button',{name:/^Внутрь:/}).click();await wait();await choose('self');assert.match(await page.locator('#details').innerText(),/self — текущий экземпляр/);assert.ok(await page.locator('#details pre.source').count());check('Module → file → class → method → parameter and actual source');
 for(const query of ['csv_memory.py read_csv','documents.py INDEX_VERSION','App.tsx view']){await page.locator('#search').fill(query);await page.locator('#results .result').first().click();await wait();assert.ok(await page.locator('#details .source').count());}
 await page.locator('#search').fill('TestChat.tsx TestChat');await page.locator('#results .result').first().click();await wait();await page.locator('#details').getByRole('button',{name:/^Внутрь:/}).click();await wait();
 const oldPage=await page.locator('#pagebar').innerText();await page.locator('#pagebar button').last().click();await wait();assert.notEqual(await page.locator('#pagebar').innerText(),oldPage);check('Search and pagination preserve deep source navigation');
 await page.getByRole('button',{name:'Всё приложение',exact:true}).click();await wait();for(const kind of ['calls','method_ref','transport','uses','contains','imports']){await page.locator('#relation-kind').selectOption(kind);await wait();await audit('source '+kind);}check('Every relationship view renders independently');
 await page.getByRole('tab',{name:'C4',exact:true}).click();await wait();assert.match(await page.locator('#details').innerText(),/arc42/);
 const views=await page.locator('#architecture-view option').evaluateAll(es=>es.map(e=>e.value));
 for(const view of views){await page.locator('#architecture-view').selectOption(view);await wait();await audit(view);assert.ok(await page.locator('#diagram svg').count());for(const b of await page.locator('#node-index button').all()){await b.click();assert.match(await page.locator('#details').innerText(),/Контракт и взаимодействие/i);assert.ok(await page.locator('#details .evidence').count());}if(['context','containers','api-components','radar-flow'].includes(view))await page.screenshot({path:path.join(folder,view+'.png')});}
 check(`${views.length} C4/runtime/deployment views; every participant has an evidence-backed arc42 card`);
 await page.locator('#architecture-view').selectOption('containers');await wait();await choose('HTTP API');await page.locator('#details').getByRole('button',{name:'Внутрь →',exact:true}).click();await wait();assert.equal(await page.locator('#architecture-view').inputValue(),'api-components');
 await page.getByRole('button',{name:'На уровень выше',exact:true}).click();await wait();assert.equal(await page.locator('#architecture-view').inputValue(),'containers');
 await choose('Фоновый исполнитель');await page.reload();await wait();assert.equal(await page.locator('#details h2').innerText(),'Фоновый исполнитель');
 await page.locator('#details button.relation').first().click();await wait();assert.equal(await page.locator('#tab-code').getAttribute('aria-selected'),'true');assert.match(await page.locator('#details').innerText(),/run_worker/);check('C4 drill-down/up, reload, and jump to actual source');
 await page.getByRole('tab',{name:'ADD',exact:true}).click();assert.ok(await page.locator('#add-pane').isVisible());assert.equal(await page.locator('.add-steps details').count(),9);
 await page.locator('#prompt-goal').fill('Проверить закупку по выбранным документам');await page.getByRole('button',{name:'Сформировать промпт',exact:true}).click();const prompt=await page.locator('#prompt-output').inputValue();assert.match(prompt,/Проверить закупку/);assert.match(prompt,/НЕ ОПРЕДЕЛЕНО/);assert.match(prompt,/получи мой выбор/);await page.getByRole('button',{name:'Копировать',exact:true}).click();assert.match(await page.locator('#copy-status').innerText(),/Скопировано|Ctrl\+C/);
 await page.screenshot({path:path.join(folder,'add.png'),fullPage:true});check('ADD checklist, explicit unknowns, prompt generation and offline copy');
 await page.getByRole('tab',{name:'C4',exact:true}).click();await wait();await page.locator('#architecture-view').selectOption('context');await wait();
 await page.getByRole('button',{name:'Mermaid',exact:true}).click();assert.match(await page.locator('#details pre').innerText(),/^flowchart/);check('Inspectable Mermaid source');
 const before=await page.locator('#world').getAttribute('style');await page.getByRole('button',{name:'Увеличить',exact:true}).click();assert.notEqual(await page.locator('#world').getAttribute('style'),before);await page.locator('#viewport').focus();const keyBefore=await page.locator('#world').getAttribute('style');await page.keyboard.press('ArrowLeft');assert.notEqual(await page.locator('#world').getAttribute('style'),keyBefore);check('Zoom and keyboard panning');
 await page.getByRole('button',{name:'Читать 100%',exact:true}).click();assert.equal(await page.locator('#zoomvalue').innerText(),'100%');
 await page.evaluate(()=>{document.querySelector('#tab-code').click();document.querySelector('#tab-c4').click();document.querySelector('#tab-add').click();});
 await page.evaluate(()=>window.atlasReady);assert.ok(await page.locator('#add-pane').isVisible());check('100% reading scale and rapid tab switching do not leave a stale renderer');
 await page.setViewportSize({width:390,height:844});await page.waitForTimeout(250);
 for(const tab of ['C4','ADD','Карта кода']){await page.getByRole('tab',{name:tab,exact:true}).click();if(tab!=='ADD')await wait();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,tab+' mobile overflow');await page.screenshot({path:path.join(folder,'mobile-'+(tab==='C4'?'c4':tab==='ADD'?'add':'code')+'.png'),fullPage:true});}check('390px layouts have no horizontal page overflow');
 assert.deepEqual(errors,[]);assert.ok(requests.every(u=>u.startsWith('file:')));check('No JavaScript/console errors or network dependencies while offline');
 fs.writeFileSync(path.join(folder,'browser-report.json'),JSON.stringify({passed:checks.length,checks,errors,requests,geometry},null,2));
 }catch(e){fs.writeFileSync(path.join(folder,'failure.json'),JSON.stringify({message:e.message,checks,errors,requests,geometry},null,2));throw e;}finally{await context?.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
