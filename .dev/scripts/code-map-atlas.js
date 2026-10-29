// Mermaid presentation adapter; source navigation and source cards stay in the shell.
const A=D.architecture, AN=new Map(A.nodes.map(n=>[n.id,n]));
let renderVersion=0, currentDiagram='', currentDiagramTitle='';
mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:'base',look:'classic',layout:'elk',htmlLabels:false,
  fontFamily:'Segoe UI, Tahoma, sans-serif',maxTextSize:200000,
  themeVariables:{primaryColor:'#fffef9',primaryTextColor:'#24352f',primaryBorderColor:'#8ba18a',
    lineColor:'#64796b',secondaryColor:'#e7eee5',tertiaryColor:'#f5f4ed',fontSize:'15px',
    clusterBkg:'#eef1e8',clusterBorder:'#b9c6b5',edgeLabelBackground:'#f5f4ed',actorBkg:'#fffef9',actorBorder:'#8ba18a'},
  flowchart:{theme:'base',look:'classic',htmlLabels:false,wrappingWidth:220,minNodeWidth:180,curve:'basis',nodeSpacing:26,rankSpacing:28,padding:16,useMaxWidth:false},
  sequence:{theme:'base',look:'classic',useMaxWidth:false,wrap:true,actorMargin:36,messageMargin:36},
  elk:{mergeEdges:false,nodePlacementStrategy:'BRANDES_KOEPF'},
  suppressErrorRendering:true});

function labelText(value){return String(value).replaceAll('&','#38;').replaceAll('"','#34;').replaceAll('<','#60;').replaceAll('>','#62;').replaceAll('\n','<br/>');}
function wrapName(value,width=32){const words=String(value).split(/\s+/),lines=[''];for(const word of words){const i=lines.length-1;if(lines[i]&&lines[i].length+word.length>width)lines.push(word);else lines[i]+=(lines[i]?' ':'')+word;}return lines.join('\n');}
function graphSource(nodes,edges,{groups=[],direction='LR'}={}){
 const aliases=new Map(nodes.map((n,i)=>[n.id,'n'+i]));
 const lines=['flowchart '+direction];
 const row=n=>`${aliases.get(n.id)}["${labelText(n.label)}"]`;
 const grouped=new Set(groups.flatMap(g=>g.nodes));
 for(const [i,g]of groups.entries()){
   lines.push(`subgraph g${i}["${labelText(g.name)}"]`);
   for(const n of nodes.filter(n=>g.nodes.includes(n.id)))lines.push(row(n));
   lines.push('end');
 }
 for(const n of nodes.filter(n=>!grouped.has(n.id)))lines.push(row(n));
 edges.forEach(e=>{const arrow=['imports','method_ref','automatic','uses','transport','contains'].includes(e.kind)?'-.->':'-->';lines.push(`${aliases.get(e.from)} ${arrow}|"${labelText(wrapName(e.label,23))}"| ${aliases.get(e.to)}`);});
 lines.push('classDef external fill:#eff1e9,stroke:#88978a,stroke-dasharray:5 4;');
 const external=nodes.filter(n=>n.external).map(n=>aliases.get(n.id));if(external.length)lines.push('class '+external.join(',')+' external;');
 return {source:lines.join('\n'),aliases};
}
function representative(id,focus){let n=N.get(id);while(n){if(n.parent===focus)return n.id;n=N.get(n.parent);}return null;}
function codeGraph(){
 const focus=N.get(state.focus),kind=$('relation-kind').value;
 let ids=[],edges=[],note='';
 if(state.mode==='startup'){
   ids=D.startup.slice();
   edges=D.edges.filter(e=>ids.includes(e.from)&&ids.includes(e.to)&&['calls','method_ref','external_call','automatic'].includes(e.kind)).map(e=>({from:e.from,to:e.to,kind:e.kind,label:verbs[e.kind]}));
   note='Вложенные вызовы при запуске serve. Номера в карточках поясняют чтение; стрелки показывают вызовы, а не линейный маршрут.';
 }else{
   const all=visibleChildren(state.focus);
   state.page=Math.max(0,Math.min(state.page,Math.max(0,Math.ceil(all.length/pageSize)-1)));
   const shown=all.slice(state.page*pageSize,(state.page+1)*pageSize);ids=shown.slice();
   if(state.focus!=='root')ids.unshift(state.focus);
   if(!ids.length)ids=[state.focus];
   if(kind==='contains')edges=shown.map(to=>({from:state.focus,to,kind,label:'содержит'})).filter(e=>e.from!==e.to);
   if(kind==='contains'&&!ids.includes(state.focus))ids.unshift(state.focus);
   const projected=new Map();let outside=0;
   if(kind!=='contains')for(const e of D.edges){
     if(e.kind!==kind)continue;
     const from=e.from===state.focus?e.from:representative(e.from,state.focus);
     let to=e.to===state.focus?e.to:representative(e.to,state.focus);
     if(!ids.includes(from))continue;
     if(!to&&e.from===state.focus){to=e.to;if(!ids.includes(to)){if(ids.length>=pageSize+7){outside++;continue;}ids.push(to);}}
     if(!ids.includes(to)||from===to)continue;
     const key=from+'|'+to;
     if(projected.has(key))projected.get(key).count++;
     else projected.set(key,{from,to,kind,count:1});
   }
   edges.push(...[...projected.values()].map(e=>({...e,label:verbs[kind]+(e.count>1?' · '+e.count:'')})));
   note=`${all.length} узлов на уровне · ${edges.length} связей на этой странице · ${verbs[kind]||kind}.`;
   if(outside)note+=` Ещё ${outside} внешних обращений доступны в карточке.`;
   if(all.length>pageSize){
     $('pagebar').append(button('←',()=>{state.page--;render();}),el('span','',`${state.page*pageSize+1}–${Math.min(all.length,(state.page+1)*pageSize)} / ${all.length}`),button('→',()=>{state.page++;render();}));
     $('pagebar').firstChild.disabled=state.page===0;$('pagebar').lastChild.disabled=(state.page+1)*pageSize>=all.length;
   }
 }
 const nodes=ids.map(id=>{const n=N.get(id),enter=count(id)?`\n↳ ${count(id)} сущностей`:'';return{id,label:wrapName(n.name)+'\n'+title(n)+enter,external:!ancestors(id).includes(state.focus)&&id!==state.focus&&state.focus!=='root'};});
 return {...graphSource(nodes,edges),ids,note,edges};
}
function highlight(){for(const c of $('diagram').querySelectorAll('[data-node]'))c.classList.toggle('selected',c.dataset.node===(state.tab==='c4'?state.archSelected:state.selected));}
async function drawDiagram(spec){
 const revision=++renderVersion;
 currentDiagram=spec.source;currentDiagramTitle=$('viewname').textContent;
 $('viewport').setAttribute('aria-busy','true');
 try{
   const {svg:markup}=await mermaid.render('atlasGraph'+revision,spec.source);
   if(revision!==renderVersion)return;
   $('diagram').innerHTML=markup;
   const diagram=$('diagram').querySelector('svg'),vb=diagram.viewBox.baseVal;
   state.width=vb.width||1000;state.height=vb.height||600;
   diagram.setAttribute('width',String(state.width));diagram.setAttribute('height',String(state.height));
   diagram.setAttribute('role','group');diagram.setAttribute('aria-label',currentDiagramTitle);
   $('world').style.width=state.width+'px';$('world').style.height=state.height+'px';
   for(const[id,alias]of spec.aliases||[]){
     const node=[...diagram.querySelectorAll('g.node')].find(e=>e.id.startsWith('atlasGraph'+revision+'-flowchart-'+alias+'-'));
     if(!node)continue;
     node.dataset.node=id;node.setAttribute('tabindex','0');node.setAttribute('role','button');
     const name=state.tab==='c4'?AN.get(id).name:N.get(id).name;
     node.setAttribute('aria-label','Выбрать '+name);
     const choose=()=>state.tab==='c4'?selectArchitecture(id):select(id);
     node.addEventListener('click',choose);
     node.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choose();}});
     node.addEventListener('dblclick',()=>{if(state.tab==='c4'){const target=AN.get(id).enter;if(target)openArchitecture(target);}else if(count(id))navigate(id);});
   }
   highlight();fit();
 }catch(error){
   if(revision!==renderVersion)return;
   $('diagram').replaceChildren(el('div','diagram-error','Не удалось построить Mermaid-диаграмму: '+error.message));
   state.width=650;state.height=240;fit();console.error(error);
 }finally{if(revision===renderVersion)$('viewport').setAttribute('aria-busy','false');}
}
function render(){
 updateTabChrome();if(state.tab==='add'){renderVersion++;$('viewport').setAttribute('aria-busy','false');return;}
 $('pagebar').replaceChildren();$('node-index').replaceChildren();
 let spec;
 if(state.tab==='c4'){
   const view=A.views.find(v=>v.id===state.archView)||A.views[0];
   state.archView=view.id;$('architecture-view').value=view.id;
   $('viewname').textContent=view.title;$('viewcount').textContent=(!A.reviewMatchesSnapshot?'Пояснения требуют повторной сверки с изменившимися исходниками. ':'')+view.description;
   $('breadcrumbs').replaceChildren(button('BERVEL',()=>openArchitecture('context'),'flat'),el('span','muted','/ '+view.level));
   $('up').disabled=view.id==='context';
   const nodes=view.nodes.map(id=>{const n=AN.get(id);return{id,label:wrapName(view.labels?.[id]||n.name)+'\n'+n.type+'\n'+n.technology,external:n.type==='Внешняя система'||n.type==='Человек'};});
   spec=view.diagram?{source:view.diagram,aliases:new Map()}:graphSource(nodes,view.edges,{groups:view.groups,direction:view.nodes.length>5||innerWidth<760?'TB':'LR'});
   for(const id of view.nodes)$('node-index').append(button(AN.get(id).name,()=>selectArchitecture(id)));
   $('legend').textContent=view.diagram?'Порядок и тип взаимодействия указаны в самой диаграмме. Карточки участников — кнопками выше.':view.id==='dependencies'?'Пунктир: зависимость исходного кода от контракта / определения. Это не направление runtime-вызова.':'Стрелка: обращающийся → адресат · подпись: действие / протокол · рамка: граница размещения · выбор узла: карточка arc42';
 }else{
   $('viewname').textContent=state.mode==='startup'?'Как запускается serve':N.get(state.focus).name;
   $('breadcrumbs').replaceChildren();for(const id of ancestors(state.focus)){
     if($('breadcrumbs').children.length)$('breadcrumbs').append(el('span','muted','/'));
     $('breadcrumbs').append(button(N.get(id).name,()=>navigate(id),'flat'));
   }
   $('up').disabled=state.focus==='root'&&state.mode!=='startup';
   spec=codeGraph();$('viewcount').textContent=spec.note;
   $('legend').textContent=state.mode==='startup'?'Сплошная: вызов по имени/импорту · пунктир: пояснение dataclass или ссылка на метод.':'Один тип связи за раз. Число на стрелке — количество свёрнутых связей этого типа. Все категории доступны в карточке. Стрелка идёт от потребителя к зависимости.';
 }
 window.atlasReady=drawDiagram(spec);
}
function updateTabChrome(){
 for(const tab of ['code','c4','add']){$('tab-'+tab).setAttribute('aria-selected',String(tab===state.tab));$('tab-'+tab).tabIndex=tab===state.tab?0:-1;}
 $('workspace').hidden=state.tab==='add';$('add-pane').hidden=state.tab!=='add';
 $('code-tools').hidden=state.tab!=='code';$('architecture-nav').hidden=state.tab!=='c4';
 $('code-filters').hidden=state.tab!=='code'||state.mode==='startup';$('node-index').hidden=state.tab!=='c4';
}
function setTab(tab){
 state.tab=tab;render();if(tab==='c4')selectArchitecture(state.archSelected||'system');else if(tab==='code')select(state.selected);save();
}
function openArchitecture(id){
 const view=A.views.find(v=>v.id===id);if(!view)return;
 state.tab='c4';state.archView=id;state.archSelected=view.root||view.nodes[0];render();selectArchitecture(state.archSelected);save();
}
function selectArchitecture(id){
 const n=AN.get(id);if(!n)return;state.archSelected=id;highlight();
 $('details').replaceChildren(el('div','eyebrow','arc42 §5 · '+n.type),el('h2','',n.name),el('span','status-chip',n.status));
 paragraph(n.technology,'facts');
 const actions=el('div','actions');
 if(n.enter&&n.enter!==state.archView)actions.append(button('Внутрь →',()=>openArchitecture(n.enter),'primary'));
 if(id==='assembly')actions.append(button('Как запускается serve',()=>navigate('group:launch','startup')));
 if(actions.children.length)$('details').append(actions);
 for(const[key,caption]of [['purpose','Ответственность и граница'],['contract','Контракт и взаимодействие'],['data','Данные и владелец'],['failure','Ограничения и отказы']]){heading(caption);paragraph(n[key]);}
 heading('Где реализовано · доказательства');
 for(const evidence of n.evidence){
   if(evidence.codeId)$('details').append(button(evidence.path+':'+evidence.line+' ↗',()=>locate(evidence.codeId),'relation'));
   const d=el('details','evidence');d.append(el('summary','',evidence.path+':'+evidence.line),el('pre','source',evidence.excerpt));$('details').append(d);
 }
 paragraph('Карточка описывает код этого снимка. Общие правила, решения и неизвестные условия — в «Общие положения».','subtle');
 $('details').scrollTop=0;$('announcement').textContent='Выбрано: '+n.name;save();
}
function commonArchitecture(){
 $('details').replaceChildren(el('div','eyebrow','arc42 · общие сведения'),el('h2','','Основа архитектуры'));
 for(const[title,text]of A.common){heading(title);paragraph(text);}
 heading('Первоисточники');for(const[name,url]of A.add.sources){const a=el('a','relation',name);a.href=url;a.target='_blank';a.rel='noopener noreferrer';$('details').append(a);}
 $('details').scrollTop=0;
}
function showDiagramSource(){
 $('details').replaceChildren(el('div','eyebrow','Mermaid.js '+D.diagramEngine.version),el('h2','',currentDiagramTitle));
 paragraph('Текст текущей диаграммы. Архитектурные данные и связи исходников преобразуются в Mermaid; ручных SVG-маршрутов нет.');
 $('details').append(el('pre','source source-diagram',currentDiagram));paragraph('Раскладка: ELK. Библиотека встроена в HTML; сеть при просмотре не требуется.','subtle');
}
function buildAdd(){
 const pane=$('add-pane');
 const intro=el('div','add-intro'),lead=el('div');lead.append(el('div','eyebrow','ПРОЕКТИРОВАНИЕ · ATTRIBUTE-DRIVEN DESIGN'),el('h1','','От требований к проверяемому решению'),el('p','',A.add.intro));
 intro.append(lead,el('div','notice','Владелец выбирает цели, ограничения, границы и критерии приёмки. ИИ предлагает варианты и пишет код внутри этих решений. Диаграмма и промпт помогают контролю; уверенность дают также проверки контрактов, правил и отказов.'));pane.append(intro);
 const grid=el('div','add-grid'),steps=el('section','add-steps');steps.setAttribute('aria-label','Шаги проектирования');
 A.add.steps.forEach(([name,question,example,done],i)=>{const d=el('details');d.open=i===0;d.append(el('summary','',name),el('p','',question),el('p','example','BERVEL · '+example),el('p','done',done));steps.append(d);});
 const panel=el('section','prompt-panel');panel.append(el('h2','','Собрать задание для ИИ'),el('p','','Заполните известное. Пробелы останутся явно обозначенными; шаблон запрещает выдавать предположение за принятое решение. Поля живут только в этой открытой странице.'));
 const fields=[['goal','Результат и пользователь','Кто, что делает и какой результат получает'],['owner','Владелец и границы','Существующий / новый модуль, что входит и что исключено'],['rules','Правила, данные и доступ','Инварианты, владелец данных, права, редакции'],['contracts','Контракты и отказы','Входы, выходы, ошибки, повтор, частичный отказ'],['quality','Ограничения и качество','Стек, бюджет, время ответа, нагрузка и способ измерения'],['scope','Разрешённый этап и проверка','Какие файлы/части менять, как доказать готовность']];
 for(const[id,name,hint]of fields){const label=el('label','',name);label.htmlFor='prompt-'+id;const input=el('textarea');input.id='prompt-'+id;input.placeholder=hint;panel.append(label,input);}
 const buttons=el('div','prompt-buttons');buttons.append(button('Сформировать промпт',makePrompt,'primary'));panel.append(buttons);
 const label=el('label','','Готовый текст');label.htmlFor='prompt-output';const output=el('textarea');output.id='prompt-output';output.readOnly=true;
 panel.append(label,output);const copyRow=el('div','prompt-buttons');copyRow.append(button('Копировать',copyPrompt),el('span','',''));const status=el('span');status.id='copy-status';status.setAttribute('role','status');copyRow.append(status);panel.append(copyRow);
 grid.append(steps,panel);pane.append(grid);
 const glossary=el('section','glossary');glossary.append(el('h2','','Словарь для принятия решений'));const dl=el('dl');for(const[term,meaning]of A.add.glossary)dl.append(el('dt','',term),el('dd','',meaning));glossary.append(dl);pane.append(glossary);
 const refs=el('div','references');for(const[name,url]of A.add.sources){const a=el('a','',name);a.href=url;a.target='_blank';a.rel='noopener noreferrer';refs.append(a);}pane.append(refs);makePrompt();
}
function makePrompt(){
 const value=id=>$('prompt-'+id).value.trim()||'[НЕ ОПРЕДЕЛЕНО — выявить вопрос, не придумывать решение]';
 $('prompt-output').value=`Помоги спроектировать и реализовать ограниченный этап BERVEL.

Результат и пользователь: ${value('goal')}
Владелец и границы: ${value('owner')}
Правила, данные и доступ: ${value('rules')}
Контракты и отказы: ${value('contracts')}
Ограничения и качество: ${value('quality')}
Разрешённый этап и проверка: ${value('scope')}

Сначала сверь актуальный код, инструкции проекта и архитектурную карту. Отдели факты от предположений и перечисли неизвестное. Не считай этот шаблон разрешением на изменение архитектуры, данных или инфраструктуры.

Пройди ADD: выбери драйверы качества; найди владельца ответственности; опиши правила, данные, публичные операции и необходимые порты. Сравни только уместные варианты и объясни последствия. Не создавай пустые слои, новый сервис или зависимость без конкретной необходимости и моего выбора.

Покажи затронутые границы C4 и сценарий успеха/отказа/повтора. Для каждого изменяемого контракта укажи вход, выход, ошибки, права и владельца данных. Отделяй зависимость исходного кода от порядка runtime-вызовов. Сборку конкретных реализаций оставляй на внешней границе.

До зависимых изменений получи мой выбор по нерешённым развилкам. Выполни только согласованный этап. Проверь бизнес-правила, контракты, отказ и совместимость; покажи изменённые файлы, результат проверок, ограничения и обновлённую документацию. Затем остановись для приёмки. Не обещай полную независимость модулей или отсутствие неоднозначностей без доказательства.`;
 $('copy-status').textContent='Текст обновлён';
}
async function copyPrompt(){
 const output=$('prompt-output');
 output.focus();output.select();
 if(document.execCommand('copy')){$('copy-status').textContent='Скопировано';return;}
 $('copy-status').textContent='Текст выделен — нажмите Ctrl+C';
 try{if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(output.value);$('copy-status').textContent='Скопировано';}}catch{}
}
function initializeAtlas(){
 for(const tab of ['code','c4','add'])$('tab-'+tab).onclick=()=>setTab(tab);
 document.querySelector('.tabs').addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const tabs=['code','c4','add'];let index=tabs.indexOf(state.tab);index=e.key==='Home'?0:e.key==='End'?2:(index+(e.key==='ArrowRight'?1:2))%3;setTab(tabs[index]);$('tab-'+tabs[index]).focus();});
 for(const view of A.views){const option=el('option','',view.title);option.value=view.id;$('architecture-view').append(option);}
 $('architecture-view').onchange=()=>openArchitecture($('architecture-view').value);
 $('architecture-common').onclick=commonArchitecture;$('diagram-source').onclick=showDiagramSource;
 $('read-scale').onclick=()=>{state.scale=1;state.x=20;state.y=20;transform();};
 $('relation-kind').onchange=()=>{render();save();};
 buildAdd();
}
