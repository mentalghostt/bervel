// Offline artifact builder. Reads source, never imports application modules.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const {spawnSync,execFileSync} = require('node:child_process');
const root = path.resolve(__dirname,'../..');
const out = path.join(root,'.dev/visualizations');
fs.mkdirSync(path.join(out,'code-map-checks'),{recursive:true});
const ts = require(path.join(root,'frontend/node_modules/typescript'));
const python = process.env.CODE_MAP_PYTHON || path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe');
const py = spawnSync(python,[path.join(__dirname,'code-map-python.py')],{encoding:'utf8',maxBuffer:64*1024*1024,env:{...process.env,PYTHONIOENCODING:'utf-8',PYTHONDONTWRITEBYTECODE:'1'}});
if(py.status!==0) throw new Error(py.stderr || `Python unavailable (${python}); set CODE_MAP_PYTHON to Python 3.9+`);
const data = JSON.parse(py.stdout);
const rel = p=>path.relative(root,p).replaceAll('\\','/');
const read = f=>fs.readFileSync(path.join(root,f),'utf8');
function walk(p){return fs.readdirSync(p,{withFileTypes:true}).flatMap(e=>e.isDirectory()&&!['node_modules','dist','__pycache__'].includes(e.name)?walk(path.join(p,e.name)):e.isFile()?[path.join(p,e.name)]:[]);}
const jsFiles = [...walk(path.join(root,'frontend/src')),...walk(path.join(root,'codex-runner')),...walk(path.join(root,'frontend/scripts'))].filter(f=>/\.(tsx?|mjs)$/.test(f));
jsFiles.push(path.join(root,'frontend/vite.config.ts'));
const program = ts.createProgram(jsFiles,{allowJs:true,checkJs:false,jsx:ts.JsxEmit.Preserve,noEmit:true,skipLibCheck:true,noLib:true,types:[],target:ts.ScriptTarget.ESNext,module:ts.ModuleKind.ESNext,moduleResolution:ts.ModuleResolutionKind.Bundler});
const checker = program.getTypeChecker();
const tsIds = new Map(),tsScopes = new Map(),tsSource = new Map();
const allIds = new Map(data.nodes.map(n=>[n.id,n]));
function add(n){if(allIds.has(n.id))throw Error('Duplicate '+n.id);data.nodes.push(n);allIds.set(n.id,n);return n.id;}
function fileNode(file,parser){if(data.files[file])return;const source=read(file);data.files[file]={source,parser};add({id:'file:'+file,name:path.basename(file),kind:'file',file,line:1,end:source.split('\n').length});}
function line(sf,n){return sf.getLineAndCharacterOfPosition(n.getStart(sf)).line+1;}
function names(n){if(ts.isIdentifier(n)||ts.isStringLiteral(n)||ts.isNumericLiteral(n))return [n];if(ts.isObjectBindingPattern(n)||ts.isArrayBindingPattern(n))return n.elements.flatMap(x=>ts.isBindingElement(x)?names(x.name):[]);return [];}
function functionName(n){if(n.name)return n.name.getText().slice(0,100);if(ts.isVariableDeclaration(n.parent))return n.parent.name.getText().slice(0,100);if(ts.isPropertyAssignment(n.parent))return n.parent.name.getText().slice(0,100);return `λ@${line(n.getSourceFile(),n)}:${n.pos}`;}
const isFunction = n=>ts.isFunctionLike(n)&&!ts.isFunctionTypeNode(n)&&!ts.isConstructorTypeNode(n);
for(const abs of jsFiles){
  const file=rel(abs),sf=program.getSourceFile(abs);
  fileNode(file,'TypeScript '+ts.version+' AST');tsSource.set(file,sf);
  for(const d of sf.parseDiagnostics)data.errors.push({file,message:ts.flattenDiagnosticMessageText(d.messageText,'\n')});
  function declare(n,parent){
    tsScopes.set(n,parent);
    let kind,name;
    if(ts.isClassDeclaration(n)||ts.isClassExpression(n)){kind='class';name=n.name?.text||`class@${line(sf,n)}`;}
    else if(isFunction(n)){kind=ts.isMethodDeclaration(n)||ts.isMethodSignature(n)||ts.isConstructorDeclaration(n)||ts.isGetAccessor(n)||ts.isSetAccessor(n)?'method':'function';name=ts.isConstructorDeclaration(n)?'constructor':functionName(n);}
    else if(ts.isInterfaceDeclaration(n)||ts.isTypeAliasDeclaration(n)){kind='type';name=n.name.text;}
    else if(ts.isEnumDeclaration(n)){kind='type';name=n.name.text;}
    else if(ts.isParameter(n)){kind='parameter';}
    else if(ts.isVariableDeclaration(n)){kind=n.name.getText()===n.name.getText().toUpperCase()?'constant':'variable';}
    else if(ts.isPropertyDeclaration(n)||ts.isPropertySignature(n)||ts.isEnumMember(n)){kind='field';}
    else if(ts.isImportSpecifier(n)||ts.isImportClause(n)&&n.name||ts.isNamespaceImport(n)){kind='import';}
    else if(ts.isCatchClause(n)&&n.variableDeclaration){/* variable declared by child */}
    const declarations=[];
    if(kind){
      const bindingNames=name?[{text:name,getText:()=>name}]:names(n.name);
      for(const binding of bindingNames){
        const label=binding.text||binding.getText(sf),start=line(sf,n);
        const id=add({id:`${parent}/${kind}:${label}@${n.getStart(sf)}`,name:label,kind,parent,file,line:start,end:sf.getLineAndCharacterOfPosition(n.end).line+1,annotation:n.type?.getText(sf)||'',value:n.initializer?.getText(sf).slice(0,400)||'',signature:isFunction(n)?n.getText(sf).split('\n')[0].slice(0,240):'',returns:isFunction(n)?n.type?.getText(sf)||'':'',returnExpressions:[],writes:kind==='variable'||kind==='field'||kind==='constant'?[start]:[]});
        tsIds.set(binding,id);declarations.push(id);
      }
      if(declarations.length){tsIds.set(n,declarations[0]);if(n.name&&!ts.isBindingPattern(n.name))tsIds.set(n.name,declarations[0]);}
    }
    let next = declarations.length&&['class','function','method','type'].includes(kind)?declarations[0]:parent;
    if(ts.isBlock(n)&&!isFunction(n.parent)){
      next=add({id:`${parent}/scope:block@${n.getStart(sf)}`,name:`block@${line(sf,n)}`,kind:'scope',parent,file,line:line(sf,n),end:sf.getLineAndCharacterOfPosition(n.end).line+1});
    }
    // JS fields assigned through this.x have no standalone declaration node.
    if(ts.isBinaryExpression(n)&&n.operatorToken.kind===ts.SyntaxKind.EqualsToken&&ts.isPropertyAccessExpression(n.left)&&n.left.expression.kind===ts.SyntaxKind.ThisKeyword){
      let current=parent;while(current&&allIds.get(current)?.kind!=='class')current=allIds.get(current)?.parent;
      if(current){const label=n.left.name.text;let existing=data.nodes.find(x=>x.parent===current&&x.name===label&&x.kind==='field');if(!existing){const id=add({id:`${current}/field:${label}`,name:label,kind:'field',parent:current,file,line:line(sf,n),end:sf.getLineAndCharacterOfPosition(n.end).line+1,value:n.right.getText(sf).slice(0,300),writes:[line(sf,n)]});existing=allIds.get(id);}tsIds.set(n.left,existing.id);}
    }
    ts.forEachChild(n,c=>declare(c,next));
  }
  declare(sf,'file:'+file);
}
function target(n){
  let symbol=checker.getSymbolAtLocation(n);
  if(symbol?.flags&ts.SymbolFlags.Alias){try{symbol=checker.getAliasedSymbol(symbol);}catch{ return undefined; }}
  if(!symbol)return tsIds.get(n);
  return symbol.declarations?.map(d=>ts.isVariableDeclaration(d)&&d.initializer&&isFunction(d.initializer)?tsIds.get(d.initializer):tsIds.get(d)||tsIds.get(d.name)).find(Boolean);
}
function edge(from,to,kind,file,line,extra={}){if(from&&to&&from!==to)data.edges.push({from,to,kind,file,line,certainty:'syntax',...extra});}
for(const [file,sf] of tsSource){
  function references(n){
    const scope=tsScopes.get(n)||'file:'+file;
    if(ts.isCallExpression(n)||ts.isNewExpression(n)){
      const result=target(n.expression)|| (ts.isPropertyAccessExpression(n.expression)?target(n.expression.name):null);
      const call={text:n.expression.getText(sf).slice(0,200),line:line(sf,n),arguments:(n.arguments||[]).map(a=>a.getText(sf).slice(0,160))};
      if(result&&['function','method','class'].includes(allIds.get(result)?.kind)){
        const member=ts.isPropertyAccessExpression(n.expression)||ts.isElementAccessExpression(n.expression);
        const kind=member?'method_ref':'calls',certainty=member?'type-reference':'syntax';
        const proof=member?'TypeScript связал свойство с объявлением через Symbol; это ссылка по типу/члену, не доказанная runtime-реализация.':'TypeScript Symbol связывает лексическое имя/импорт с конкретным объявлением; глобального поиска по имени метода нет.';
        Object.assign(call,{target:result,bindingKind:kind,certainty,proof});edge(scope,result,kind,file,call.line,{certainty,proof});
      }
      else call.reason='Внешний API или динамическая функция; адресат не установлен.';
      const owner=allIds.get(scope);(owner.calls??=[]).push(call);
    }
    if(ts.isIdentifier(n)&&!tsIds.has(n)){
      const result=target(n);if(result)edge(scope,result,'uses',file,line(sf,n));
    }
    if(ts.isReturnStatement(n)){
      let s=scope;while(s&&!['function','method'].includes(allIds.get(s)?.kind))s=allIds.get(s)?.parent;
      if(s)allIds.get(s).returnExpressions.push(n.expression?.getText(sf).slice(0,200)||'undefined');
    }
    if(ts.isImportDeclaration(n)&&ts.isStringLiteral(n.moduleSpecifier)){
      const spec=n.moduleSpecifier.text;
      if(spec.startsWith('.')){
        const base=path.resolve(root,path.dirname(file),spec);
        const found=['','.ts','.tsx','.mjs','/index.ts','/index.tsx'].map(s=>rel(base+s)).find(f=>allIds.has('file:'+f));
        if(found)edge('file:'+file,'file:'+found,'imports',file,line(sf,n));
      }
    }
    ts.forEachChild(n,references);
  }
  references(sf);
}
// Resource/configuration files are indexed as data or text, never executed.
const resources=[...walk(path.join(root,'src/bervel')),...walk(path.join(root,'frontend/src')),...walk(path.join(root,'codex-runner'))].filter(f=>/\.(json|css)$/.test(f)&&!f.endsWith('package-lock.json')).map(rel);
const configs=['pyproject.toml','Dockerfile','compose.yaml','compose.mox.yaml','frontend/Dockerfile','frontend/nginx.conf.template','frontend/19-web-access.sh','frontend/index.html','frontend/package.json','frontend/tsconfig.json','frontend/tsconfig.app.json','frontend/tsconfig.node.json','codex-runner/Dockerfile','docker/postgres/01-databases.sh'];
for(const file of new Set([...resources,...configs])){
  fileNode(file,file.endsWith('.json')?'JSON data':'Text (file only)');
  if(file.endsWith('.json')){
    let value;try{if(file.includes('tsconfig')){const result=ts.parseConfigFileTextToJson(file,data.files[file].source);if(result.error)throw Error(ts.flattenDiagnosticMessageText(result.error.messageText,'\n'));value=result.config;data.files[file].parser='TypeScript JSONC';}else value=JSON.parse(data.files[file].source);}catch(e){data.errors.push({file,message:e.message});continue;}
    // JSON key positions are best indexed by the scanner, avoiding guessed line numbers.
    const scanner=ts.createScanner(ts.ScriptTarget.Latest,false,ts.LanguageVariant.Standard,data.files[file].source);
    let token;while((token=scanner.scan())!==ts.SyntaxKind.EndOfFileToken){
      if(token===ts.SyntaxKind.StringLiteral){const key=scanner.getTokenValue(),pos=scanner.getTokenPos(),end=scanner.getTextPos();const rest=data.files[file].source.slice(end);if(/^\s*:/.test(rest)){const ln=data.files[file].source.slice(0,pos).split('\n').length;add({id:`file:${file}/field:${key}@${pos}`,name:key,kind:'field',parent:'file:'+file,file,line:ln,end:ln,annotation:'JSON key'});}}
    }
  }
}
const groups=[
 ['launch','Запуск и сборка','__main__, worker, app_setup','Точки входа Python. Выбирают режим и собирают конкретные части приложения. Как диспетчер: знает исполнителей и передаёт им нужные инструменты.'],
 ['core','Общая платформа','src/bervel/core','Общие права доступа, документы, соединение с БД и модельный клиент. Бизнес-модули пользуются этими возможностями. Платформа не импортирует конкретные модули.'],
 ['chat','Чат','modules/chat','Переписка: история, очередь ответа и сохранение результата. Свои таблицы chat_sessions и chat_turns; документы и модель передаются извне.'],
 ['tender_radar','Тендерный радар','modules/tender_radar','Импорт карточек закупок, правила отбора, анализ документов и PDF-отчёт. Свои таблицы radar_batches и radar_runs.'],
 ['frontend','Интерфейс','frontend/src · React / TypeScript','Экраны браузера и функции HTTP-запросов. Показывают данные пользователю и обращаются к Python API.'],
 ['runner','Посредник модели','codex-runner · Node.js','Отдельный Node-сервис: соединяет Python с моделью через SDK и выбранные настройки провайдера.'],
 ['runtime','Сборка и запуск','Dockerfile · Compose · конфигурация','Файлы упаковки и запуска. API, worker и migrate используют общий Python-образ с разными командами. Контейнер — запущенная программа; бизнес-модуль — часть исходного кода.']
];
add({id:'root',name:'BERVEL',kind:'project',description:'Карта фактических исходников. Выберите узел, чтобы узнать его роль. Кнопка «Внутрь» раскрывает следующий уровень. Это снимок файлов, а не работающий экземпляр приложения. Сервисы собираются вручную в app_setup.py. Модули могут вызывать публичные операции друг друга; чужие внутренности, таблицы и циклы запрещены. Core не выбирает бизнес-модули.'});
for(const [id,name,subtitle,description]of groups)add({id:'group:'+id,name,subtitle,description,kind:'module',parent:'root'});
// Cross-process HTTP is documented explicitly from its source call site.
// These are architectural links, not imaginary Python/JS function calls.
const transports=[['group:frontend','group:core','frontend/src/api.ts','fetch(`/api/v0.1','HTTP /api/v0.1'],['group:core','group:runner','src/bervel/core/infrastructure/codex.py',"'/v1/generate'",'HTTP /v1/generate']];
for(const[from,to,file,needle,label]of transports){const source=data.files[file].source;const pos=source.indexOf(needle);if(pos<0)throw Error('Transport source changed: '+file);edge(from,to,'transport',file,source.slice(0,pos).split('\n').length,{certainty:'explained',label,proof:'Ручное архитектурное пояснение по HTTP-вызову в указанной строке. Это связь процессов, а не вызов функции другого языка.'});}
function groupFor(f){if(f.startsWith('src/bervel/core/'))return 'core';for(const m of ['chat','tender_radar'])if(f.startsWith(`src/bervel/modules/${m}/`))return m;if(f.startsWith('src/bervel/'))return 'launch';if(f.startsWith('frontend/src/'))return 'frontend';if(f.startsWith('codex-runner/')&&f!=='codex-runner/Dockerfile'&&f!=='codex-runner/package.json')return 'runner';return 'runtime';}
const packages=new Map();
for(const n of [...data.nodes].filter(n=>n.kind==='file')){
  const group=groupFor(n.file);let parent='group:'+group;
  const base=group==='core'?'src/bervel/core':group==='frontend'?'frontend/src':['chat','tender_radar'].includes(group)?`src/bervel/modules/${group}`:group==='runner'?'codex-runner':null;
  if(base){const sub=path.posix.dirname(n.file).slice(base.length).split('/').filter(Boolean);let acc=base;for(const part of sub){acc+='/'+part;const id='package:'+acc;if(!packages.has(id)){add({id,name:part,kind:'package',parent,subtitle:acc,description:'Каталог исходников. Объединяет физические файлы этой части приложения.'});packages.set(id,true);}parent=id;}}
  n.parent=parent;
}
// Reviewed explanations supplement extraction; they do not invent executable calls.
const notes={
 'src/bervel/__main__.py|main':'Разбирает команду migrate / serve / worker. Для serve читает настройки, создаёт FastAPI-приложение и передаёт его Uvicorn. Возвращает 0 при успехе и 1 при известной ошибке. Как главный выключатель с тремя режимами.',
 'src/bervel/core/config.py|Settings':'Набор настроек установки: адрес БД, папка оригиналов, привязки ключей и адреса сервисов. Создание dataclass автоматически вызывает __post_init__. Само создание Settings не подключается к БД.',
 'src/bervel/core/config.py|from_environment':'Читает имена BERVEL_* из окружения процесса; разбирает JSON привязок доступа, создаёт ActorContext, превращает путь в Path, порт — в int и создаёт Settings. Обязательные BERVEL_DATABASE_URL, BERVEL_OBJECT_ROOT и BERVEL_ACCESS_KEYS не заменяются значениями по умолчанию. Реальные значения окружения карта не читает.',
 'src/bervel/core/config.py|__post_init__':'Автоматическая проверка после создания Settings: URL посредника, допустимый host/port, абсолютный путь, ключи и permissions. При ошибке поднимает ValueError. Это проверка настроек, подключения к БД здесь нет.',
 'src/bervel/core/config.py|port':'Поле Settings: порт HTTP-сервера. from_environment превращает BERVEL_PORT в целое число, __post_init__ проверяет диапазон 1–65535, main передаёт поле в uvicorn.run.',
 'src/bervel/__main__.py|settings':'Локальное имя в main, которому присваивается результат Settings.from_environment(). Далее объект передаётся в create_app, а его host и port — в uvicorn.run. Как заполненная карточка параметров запуска.',
 'src/bervel/core/api/app.py|modules':'HTTP-обработчик GET /modules: после проверки module:read возвращает прежний статический JSON из module_catalog.json. Каталог нужен клиенту API и не управляет сборкой, загрузкой модулей или правами.',
 'src/bervel/modules/chat/service.py|select_sources':'Выбирает документы внутри уже закреплённого снимка чата. Если вопрос явно просит последний CSV и не просит сравнение всех/обоих, оставляет первый CSV в упорядоченном каталоге; иначе передаёт весь снимок. Только после этого ChatService обращается к общей DocumentMemory. Правило последнего CSV принадлежит чату.',
 'src/bervel/core/document_memory.py|DocumentMemory':'Общий поиск по явно переданному снимку документов. Читает или строит индекс, оценивает совпадения слов, укладывает фрагменты в бюджет и проверяет оригиналы. Память не выбирает последний CSV и не содержит отдельного флага для чата/радара.',
 'src/bervel/core/csv_memory.py|decode_text':'Общий выбор кодировки CSV: BOM UTF-16, затем UTF-8 с необязательным BOM, затем Windows-1251. Возвращает текст и имя кодировки. Реальные байты получает через content, файлы окружения не читает.',
 'src/bervel/core/csv_memory.py|read_csv':'Общий CSV-декодер для preview и поисковых фрагментов: вызывает decode_text, определяет разделитель (запятая, точка с запятой, TAB или |), сохраняет значения строками. Возвращает заголовок, строки, кодировку и признак усечения. Лимиты: 5 МиБ, 200 колонок, 10000 строк; multiline/quoted cells читаются CSV-парсером.',
 'src/bervel/core/csv_memory.py|parse_csv':'Строит фрагменты CSV для общего поиска, используя тот же read_csv, что и preview. Сохраняет диапазоны строк, заголовок и признак усечения; делит длинные ячейки на части. Это подготовка контекста для чата и радара; учебная публикация dataset удалена.',
 'src/bervel/core/infrastructure/documents.py|INDEX_VERSION':'Текущая версия производного индекса — 4. document_index выбирает только эту версию; индексы 2/3 не подходят. DocumentMemory при отсутствии подходящего индекса читает проверенный оригинал, строит новый и сохраняет его. Переиндексация ленивая: по мере обращений.',
 'src/bervel/app_setup.py|build_services':'Создаёт engine и конкретные реализации общих и модульных операций. Например, ChatService получает PostgresChatRepository(engine, documents), общую память документов и модель. Возвращает ApplicationServices. Как комплектация рабочего места: каждому исполнителю выдаются его инструменты.',
 'src/bervel/app_setup.py|create_app':'Получает Settings, вызывает build_services, создаёт общую FastAPI-основу и подключает оригиналы, preview, чат, настройки и радар. Возвращает приложение. Сетевой сервер запускает uvicorn.run в main.',
 'src/bervel/worker.py|run_worker':'Запускает отдельного фонового исполнителя. Он использует ту же сборку сервисов, проверяет очереди и выполняет поставленные задания.',
 'src/bervel/modules/chat/service.py|ChatService':'Публичные действия чата. Проверяет права, получает источники через общую память документов и вызывает модель. Работа с таблицами переписки передана репозиторию.',
 'src/bervel/modules/chat/infrastructure/repository.py|PostgresChatRepository':'SQL-реализация хранения чата. Методы создают и читают диалоги, ставят сообщения в очередь, забирают и завершают задания в chat_sessions/chat_turns.',
 'src/bervel/core/infrastructure/database.py|make_engine':'Создаёт SQLAlchemy Engine — объект, через который операции получают соединения PostgreSQL. Адрес берётся из переданного аргумента. Этот шаг относится к слою БД, а не к чтению Settings.',
 'frontend/src/App.tsx|App':'Главный React-компонент браузера. По hash адреса выбирает файлы, радар, чат или настройки. Начальный экран — «Файлы». Функции API общаются с Python по HTTP.',
 'frontend/src/App.tsx|view':'Текущее значение window.location.hash в состоянии React. Вместе с setView определяет, какая страница показывается. Одинаковое имя в другом файле — другая переменная.'
};
for(const n of data.nodes){const note=notes[n.file+'|'+n.name];if(note){n.description=note;n.reviewed=true;}}
const fileNotes={
 '__main__.py':'Выбор команды migrate / serve / worker и обработка ошибок запуска.',
 'app_setup.py':'Единственная внешняя сборка платформы и трёх модулей: создаёт реализации и передаёт их в операции.',
 'worker.py':'Последовательный фоновый опрос очередей и исполнение заданий через общую сборку сервисов.',
 'core/config.py':'Чтение и проверка настроек установки, привязки ключей доступа и аутентификация.',
 'core/models.py':'Общие типы данных: пользователь с областью доступа, ссылки на оригиналы и результаты.',
 'core/ports.py':'Контракты общих операций хранения. Описывают, что требуется от внешней реализации.',
 'core/services.py':'Операции с оригиналами, редакциями и чтением наборов данных. Проверяют права до обращения к хранилищу.',
 'core/errors.py':'Единая прикладная ошибка с кодом и сообщением для границ приложения.',
 
 
 
 'core/csv_memory.py':'Общий CSV-декодер read_csv/decode_text для preview и индексатора, затем подготовка поисковых фрагментов. Старого отдельного поиска CsvMemory здесь нет.',
 'core/document_memory.py':'Общий поиск в переданном снимке, чтение и проверка источников для модели. Выбор последнего CSV выполняет сценарий чата до вызова этой памяти.',
 'core/document_models.py':'Типы снимков, фрагментов и состояния прочтения документа.',
 'core/model_gateway.py':'Контракт обращения к модели и безопасные сообщения об ошибках.',
 'core/infrastructure/database.py':'SQLAlchemy Engine, общие SQL-таблицы и запуск линейных миграций PostgreSQL.',
 'core/infrastructure/repository.py':'SQL-реализация общих оригиналов, редакций и наборов данных.',
 'core/infrastructure/documents.py':'SQL-каталог документов и производного индекса, общий для модулей. Версия индекса 4: старые версии не читаются, при обращении индекс строится заново из проверенного оригинала.',
 'core/infrastructure/storage.py':'Сохранение и чтение исходных байтов файлов в папке объектов.',
 'core/infrastructure/commands.py':'Защита повторных команд и аудит в транзакции вызывающего репозитория.',
 'core/infrastructure/codex.py':'HTTP-клиент посредника модели: генерация ответа и запросы настроек.',
 
 'core/infrastructure/document_reader.py':'Извлечение содержимого документов и выбор способа чтения; формирует результат с ограничениями чтения.',
 'core/infrastructure/office_reader.py':'Извлечение текста офисных форматов для общего чтения документов.',
 'core/infrastructure/file_preview.py':'Предварительный просмотр исходного файла. Для CSV использует общий core/csv_memory.read_csv: preview и поиск читают одинаковые значения ячеек.',
 'core/infrastructure/pdf_fonts.py':'Общий выбор шрифтов PDF из ресурсов Python-пакета.',
 'core/api/app.py':'Общая FastAPI-основа, проверка пользователя, HTTP-операции файлов/результатов и выдача статического каталога /modules.',
 
 'core/api/models.py':'Формы входов и ответов общего HTTP API.',
 'core/api/sources.py':'HTTP-формы и представление источников документов.',
 'core/api/provider.py':'HTTP-обработчики чтения, сохранения и тестирования настроек провайдера модели.',
 
 'core/api/preview.py':'HTTP-входы получения предварительного просмотра и оригинала документа.',
 'modules/chat/service.py':'Публичные операции чата: история, очередь и ответ с источниками. select_sources выбирает последний CSV внутри закреплённого снимка, затем передаёт выбор общей памяти документов.',
 'modules/chat/models.py':'Типы результатов и контракт данных переписки.',
 'modules/chat/api.py':'HTTP-входы создания диалога, чтения истории и отправки сообщения.',
 'modules/chat/infrastructure/repository.py':'SQL-операции переписки и очереди сообщений.',
 'modules/chat/infrastructure/tables.py':'SQL-описания таблиц chat_sessions и chat_turns. Здесь объявлена структура, реальные строки БД карта не читает.',
 'modules/tender_radar/service.py':'Публичные операции радара: импорт партий, запуск анализа, чтение результата и отчёта.',
 'modules/tender_radar/analysis.py':'Правила отбора и подготовки анализа закупочных карточек.',
 'modules/tender_radar/models.py':'Типы входов, состояний и результатов тендерного радара.',
 'modules/tender_radar/ports.py':'Контракты внешних операций, необходимые радару.',
 'modules/tender_radar/api.py':'HTTP-входы импорта, партий, анализа и отчётов радара.',
 'modules/tender_radar/infrastructure/importer.py':'Чтение закупочных карточек из входного файла, преобразование в данные радара.',
 'modules/tender_radar/infrastructure/repository.py':'SQL-операции партий и запусков анализа радара.',
 'modules/tender_radar/infrastructure/tables.py':'SQL-описания таблиц radar_batches и radar_runs.',
 'modules/tender_radar/infrastructure/pdf.py':'Формирование PDF из уже сохранённого отчёта радара.',
};
const frontNotes={'App.tsx':"Оболочка и навигация между файлами, радаром, чатом и настройками.",'main.tsx':'Точка входа React: подключает стили и монтирует App в HTML.','api.ts':'HTTP-запросы общих файлов, обработки CSV и чтения результата; проверка формы ответа.','chatApi.ts':'HTTP-запросы чата и проверка полученных сессий, сообщений и источников.','radarApi.ts':'HTTP-запросы партий, анализа и отчётов радара; проверка ответов.','previewApi.ts':'HTTP-запросы данных предварительного просмотра файла.','downloadOriginal.ts':'Получение оригинала для скачивания через API.','FilesPage.tsx':'Экран списка сохранённых файлов и данных.','TestChat.tsx':'Экран переписки, отправки вопроса и показа источников ответа.','ProviderSettings.tsx':'Экран настроек и проверки подключения провайдера модели.','RadarPage.tsx':'Экран импорта закупок, партий и запуска анализа радара.','RadarReport.tsx':'Отображение результата анализа радара в браузере.','FilePreview.tsx':'Выбор подходящего представления предварительного просмотра файла.','PdfPreview.tsx':'PDF-просмотрщик в браузере.','UploadFile.tsx':'Интерфейс выбора и загрузки файла.'};
const runnerNotes={'server.mjs':'HTTP-сервер посредника модели: обработка генерации, настроек и тестового запроса через SDK.','settings.mjs':'Чтение, проверка и сохранение настроек провайдера. Карта читает только этот исходник; внешние файлы с ключами не открывает.','mox-bridge.mjs':'Преобразование запросов/ответов для выбранного MOX-подключения.','images.mjs':'Подготовка входных изображений для запроса к модели.'};
for(const n of data.nodes.filter(n=>n.kind==='file')){
 const f=n.file,key=f.replace('src/bervel/','');let text=fileNotes[key]||(f.startsWith('frontend/src/')?frontNotes[n.name]:f.startsWith('codex-runner/')?runnerNotes[n.name]:null);
 if(f.endsWith('/__init__.py'))text='Вход Python-пакета. Может экспортировать публичные имена для остальных частей приложения; пустой файл только обозначает пакет.';
 if(f.includes('/migrations/versions/')&&n.name!=='__init__.py')text='Исторический шаг изменения структуры БД. upgrade применяет шаг, downgrade описывает обратное изменение. Исходник миграции не является текущими данными БД.';
 if(f.includes('/schemas/')&&f.endsWith('.json'))text='Исполняемая JSON-схема: задаёт допустимую форму данных и используется при проверке.';
 if(text){n.description=text;n.reviewed=true;}
}
function find(file,name,kind){return data.nodes.find(n=>n.file===file&&n.name===name&&(!kind||n.kind===kind));}
const config='src/bervel/core/config.py',setup='src/bervel/app_setup.py';
const settings=find(config,'Settings','class'),post=find(config,'__post_init__'),from=find(config,'from_environment');
edge(settings.id,post.id,'automatic',config,settings.line,{certainty:'explained',label:'dataclass: после создания',proof:'Ручное пояснение механизма @dataclass: сгенерированный __init__ автоматически вызывает __post_init__. Прямого вызова в main нет.'});
const startup=[find('src/bervel/__main__.py','main','function'),from,settings,post,find(setup,'create_app'),find(setup,'build_services')];
data.startup=startup.map(n=>n.id);
data.startupNotes=['main: выбор serve','from_environment: чтение окружения','Settings: создание набора настроек','__post_init__: автоматическая проверка','create_app: создание приложения','build_services: ручная сборка сервисов'];
// Uvicorn is external; this node documents a real call site, not library internals.
const main=startup[0],call=main.calls.find(c=>c.text==='uvicorn.run');
const uv=add({id:'external:uvicorn.run',name:'uvicorn.run',kind:'external',parent:'group:launch',file:main.file,line:call.line,end:call.line,description:'Внешняя функция Uvicorn. Получает готовое приложение, host и port и запускает HTTP-сервер. Исходники библиотеки не входят в карту.'});
const externalProof='В AST main найден вызов uvicorn.run; uvicorn импортирован как внешний модуль. Узел обозначает только место внешнего вызова, без разбора реализации Uvicorn.';
edge(main.id,uv,'external_call',main.file,call.line,{certainty:'external-syntax',proof:externalProof});Object.assign(call,{target:uv,bindingKind:'external_call',certainty:'external-syntax',proof:externalProof});delete call.reason;data.startup.push(uv);data.startupNotes.push('uvicorn.run: сетевой HTTP-сервер');
for(const n of data.nodes)if(n.parent)edge(n.parent,n.id,'contains',n.file,n.line);
const unique=new Map();for(const e of data.edges){const key=[e.from,e.to,e.kind,e.line].join('|');unique.set(key,e);}data.edges=[...unique.values()];
const hashes={};for(const [f,info]of Object.entries(data.files)){info.sha256=crypto.createHash('sha256').update(info.source).digest('hex');hashes[f]=info.sha256;}
const hash=crypto.createHash('sha256').update(JSON.stringify(hashes)).digest('hex');
const git=args=>execFileSync('git',args,{cwd:root,encoding:'utf8'}).trim();
data.meta={root,created:new Date().toISOString(),hash,head:git(['rev-parse','HEAD']),branch:git(['branch','--show-current']),typescript:ts.version,python:JSON.parse(spawnSync(python,['-c','import json,sys; print(json.dumps(sys.version))'],{encoding:'utf8'}).stdout),files:Object.keys(data.files).length};
data.limitations=[
 'Это статический разбор: найдены определения и ссылки в исходнике. Связь «вызывает» — лексическое/квалифицированное имя с найденным определением. «Ссылка на метод» — self/cls, тип TypeScript или тип получателя, выведенный из единственного присваивания конструктора. Реальное выполнение и динамическая диспетчеризация не доказаны. Тип связей выбирается отдельно над диаграммой.',
 'Вызовы через динамические объекты, внешние библиотеки, HTTP и передачу callbacks не восстанавливаются как доказанный сквозной маршрут. Они перечислены в карточках отдельно. JS-типы разрешены TypeScript, без запуска кода.',
 'Python: AST, области функций/классов/comprehensions, импорты, параметры, присваивания и поля self/cls. Тип получателя конструктора выводится только при единственном присваивании; это отдельная категория ссылок. Неизвестный receiver не сопоставляется с чужим методом по имени. Наследование, monkey patching, setattr и полная семантика паттернов не моделируются.',
 'TS/JS: объявления, destructuring, функции, callbacks, методы, классы, типы, параметры, блоки и присваивания this. Свойства произвольных объектов и JSX не становятся отдельными runtime-переменными; JSX-компоненты дают синтаксические ссылки.',
 'JSON: ключи как данные. CSS, TOML, YAML, HTML, Nginx, Dockerfile и shell: физические файлы с исходником, без разбора функций и переменных. Шрифты, картинки и лицензии перечислены как ресурсы без встраивания содержимого.',
 'Исключены node_modules, dist/build, .git, .dev, .env и реальные данные. Файлы внешних auth/config не читаются. Имена окружения видны только в исходном коде; их значения из окружения не загружаются.',
 'Пояснения ключевого маршрута проверены вручную. Для остальных сущностей карточка сообщает только наблюдение по коду и не приписывает бизнес-смысл по одному имени.',
 'Карта — снимок. После правок выполните node .dev/scripts/build-code-map.cjs и откройте HTML заново. SHA-256 каждого файла и общий хеш помогают сверить версию.'
];
data.excludedResources=[...walk(path.join(root,'src/bervel')),...walk(path.join(root,'frontend/src'))].filter(f=>!/\.(py|tsx?|mjs|css|json)$/.test(f)&&!f.endsWith('.pyc')).map(rel);
data.stats={kinds:{},edges:{},unresolved:0,resolved:0};for(const n of data.nodes){data.stats.kinds[n.kind]=(data.stats.kinds[n.kind]||0)+1;for(const c of n.calls||[])data.stats[c.target?'resolved':'unresolved']++;}for(const e of data.edges)data.stats.edges[e.kind]=(data.stats.edges[e.kind]||0)+1;
const baseline=path.join(out,'code-map-checks/source-before.json');if(!fs.existsSync(baseline))fs.writeFileSync(baseline,JSON.stringify({meta:data.meta,hashes,status:git(['status','--porcelain=v1']),index:git(['diff','--cached','--raw'])},null,2));
const template=fs.readFileSync(path.join(__dirname,'code-map-shell.html'),'utf8');
data.architecture=require('./code-map-architecture.cjs')(data,root);
data.diagramEngine={name:'Mermaid.js',version:'12.2.0',layout:'ELK',license:'MIT'};
const mermaid=fs.readFileSync(path.join(__dirname,'vendor/mermaid-12.2.0.min.js'),'utf8');
const engineHash=crypto.createHash('sha256').update(mermaid).digest('hex');
if(engineHash!=='a8a6a6f805b074a8261a2825c86f99c8054acb3b35f50bb7e24a4f9f2c3c3b9f')throw Error('Unexpected Mermaid vendor hash');
data.diagramEngine.sha256=engineHash;
const json=JSON.stringify(data).replaceAll('<','\\u003c').replaceAll('>','\\u003e').replaceAll('&','\\u0026').replaceAll('\u2028','\\u2028').replaceAll('\u2029','\\u2029');
const html=template.replace('/*__MAP_DATA__*/',()=>json)
 .replace('/*__MERMAID__*/',()=>mermaid.replace(/<\/script/gi,'<\\/script'))
 .replace('/*__ATLAS_CSS__*/',()=>fs.readFileSync(path.join(__dirname,'code-map-atlas.css'),'utf8'))
 .replace('/*__ATLAS_JS__*/',()=>fs.readFileSync(path.join(__dirname,'code-map-atlas.js'),'utf8'));
fs.writeFileSync(path.join(out,'bervel-code-map.html'),html);
fs.writeFileSync(path.join(out,'code-map-checks/coverage.json'),JSON.stringify({meta:data.meta,stats:data.stats,errors:data.errors,files:Object.fromEntries(Object.entries(data.files).map(([f,x])=>[f,{parser:x.parser,sha256:x.sha256}])),resources:data.excludedResources,limitations:data.limitations},null,2));
console.log(JSON.stringify({output:rel(path.join(out,'bervel-code-map.html')),meta:data.meta,stats:data.stats,errors:data.errors},null,2));
