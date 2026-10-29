"""Radar evaluates every imported tender using complete selected profiles."""
from datetime import datetime,timezone
from collections.abc import Callable, Sequence
from uuid import UUID
from bervel.core.errors import CoreError
from bervel.core.models import ActorContext,ArtifactRef
from bervel.core.services import authorize,validate_key
from bervel.core.model_gateway import MODEL_FAILURE_MESSAGES
from bervel.core.model_gateway import ModelClient
from bervel.core.services import ArtifactService
from bervel.modules.tender_radar.models import RadarBatch, RadarRun, RadarRunSummary
from bervel.modules.tender_radar.ports import RadarRepository, RadarMemory, TenderImporter,StockImporter,EconomicsImporter
from bervel.modules.tender_radar.analysis import batches,make_report,model_prompt,parse_assessments
from bervel.modules.tender_radar.preselection import preselect
from bervel.modules.tender_radar.stock import percentage,decimal_text
from bervel.modules.tender_radar.full import validate_filters,filter_rows,full_report


def access(actor: ActorContext, permission: str) -> None:
    for required in (permission,'artifact:read','dataset:read'): authorize(actor,required)


class RadarService:
    def __init__(self, repository: RadarRepository, artifacts: ArtifactService, memory: RadarMemory,
                 importer: TenderImporter, model: ModelClient | None = None, *,
                 stock_importer: StockImporter | None = None,
                 economics_importer: EconomicsImporter | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.repository,self.artifacts,self.memory,self.importer,self.model,self.now=repository,artifacts,memory,importer,model,now
        self.stock_importer=stock_importer
        self.economics_importer=economics_importer

    def analyse(self,actor:ActorContext,procurement_ref:ArtifactRef,stock_ref:ArtifactRef,
                economics_ref:ArtifactRef,filters:dict,command_key:str)->RadarRun:
        access(actor,'process:start');access(actor,'dataset:publish');validate_key(command_key)
        filters=validate_filters(filters)
        refs=[procurement_ref,stock_ref,economics_ref]
        if len(set(refs))!=3:raise CoreError('VALIDATION_ERROR','Выберите три разных файла соответствующих ролей.')
        snapshots=[]
        for ref,role in zip(refs,('procurement','stock','economics')):
            meta=self.artifacts.prepared_metadata(actor,ref)
            if meta['usage_role']!=role:raise CoreError('VALIDATION_ERROR','Роль выбранного файла должна быть '+role+'.')
            snapshots.append({k:meta[k] for k in ('artifact_id','filename','media_type','created_at','size')})
        batch=self.import_batch(actor,procurement_ref,'analyse:'+command_key if len(command_key)<=120 else command_key)
        selection=dict(pipeline_version='1.3',procurement_source=snapshots[0],stock_source=snapshots[1],economics_source=snapshots[2],
            filters=filters,max_shortage_percent=filters['max_shortage_percent'])
        return self.repository.enqueue(actor,UUID(batch['batch_id']),snapshots,filters['focus'],command_key,selection=selection)

    def import_batch(self, actor: ActorContext, ref: ArtifactRef, key: str) -> RadarBatch:
        access(actor,'dataset:publish'); validate_key(key)
        original=self.artifacts.read(actor,ref)
        data=self.importer.parse(original.content,original.metadata['filename'])
        return self.repository.save_batch(actor,original.metadata,data,key)

    def list_batches(self, actor: ActorContext) -> list[RadarBatch]:
        access(actor,'dataset:read')
        return self.repository.list_batches(actor)

    def start(self, actor: ActorContext, batch_id: UUID, refs: Sequence[ArtifactRef], focus: str, key: str,
              *, stock_ref: ArtifactRef | None = None,max_shortage_percent='0') -> RadarRun:
        access(actor,'process:start'); validate_key(key)
        try:allowed=percentage(max_shortage_percent)
        except ValueError:raise CoreError('VALIDATION_ERROR','Допустимая нехватка должна быть от0до100процентов с точностью до сотых.') from None
        if not 1<=len(refs)<=10 or len(set(refs))!=len(refs) or len(focus)>1000:
            raise CoreError('VALIDATION_ERROR','Выберите1–10разных файлов и фокус до1000символов.')
        snapshot=[]
        for ref in refs:
            metadata=self.artifacts.metadata(actor,ref)
            snapshot.append({key:metadata[key] for key in ('artifact_id','filename','media_type','created_at','size')})
        stock_snapshot=None
        if stock_ref:
            metadata=self.artifacts.metadata(actor,stock_ref)
            stock_snapshot={name:metadata[name] for name in ('artifact_id','filename','media_type','created_at','size')}
        return self.repository.enqueue(actor,batch_id,snapshot,focus,key,
            selection={'stock_source':stock_snapshot,'max_shortage_percent':decimal_text(allowed)})

    def read(self, actor: ActorContext, run_id: UUID) -> RadarRun:
        access(actor,'process:read')
        return self.repository.read(actor,run_id)

    def list_runs(self, actor: ActorContext) -> list[RadarRunSummary]:
        access(actor,'process:read')
        return self.repository.list_runs(actor)

    def run_once(self) -> bool:
        with self.repository.claim() as run:
            if run is None: return False
            if run['interrupted']:
                self.repository.finish(run['run_id'],error='Исполнение прервано; исход модели неизвестен. Автоматического повтора нет. Создайте новый запуск.')
                return True
            actor=ActorContext(run['actor_id'],run['data_scope'],frozenset(run['actor_permissions']))
            try:
                access(actor,'process:start')
                if (run.get('selection') or {}).get('pipeline_version')=='1.3':
                    self._run_full(actor,run)
                    return True
                batch=self.repository.batch(actor,run['batch_id'])
                # Re-check immutable original even though the import itself is cached.
                self.artifacts.read(actor,ArtifactRef(batch['artifact_id']))
                sources=self.memory.context(actor,run['sources_snapshot'],'',complete=True)
                if not any(s.get('status')!='metadata_only' and s.get('unit')!='metadata' for s in sources):
                    raise CoreError('RAG_UNREADABLE','Выбранные файлы не удалось прочитать. Выберите читаемый CSV/TXT/XLSX/DOCX/PDF или изображение.')
                rows=batch['data']['records']
                selection=run.get('selection') or {'stock_source':None,'max_shortage_percent':'0'}
                stock=None;stock_source=selection['stock_source']
                if stock_source:
                    if self.stock_importer is None:raise CoreError('RADAR_STOCK_INVALID','Не настроено чтение свободных остатков.')
                    original=self.artifacts.read(actor,ArtifactRef(UUID(stock_source['artifact_id'])))
                    stock=self.stock_importer.parse(original.content,original.metadata['filename'])
                catalogue=stock['items'] if stock else []
                candidates,prefiltered=preselect(rows,sources,self.now())
                portions=batches(candidates,sources,run['focus'],catalogue=catalogue)
                assessments=[]
                model=None
                if portions:
                    if self.model is None: raise CoreError('MODEL_UNCONFIGURED','Модель не настроена.')
                    images=self.memory.images(actor,sources)
                    expected_images={s['artifact_id'] for s in sources if s.get('unit')=='image'}
                    attached_images={s['artifact_id'] for s in sources if s.get('image_attached')}
                    if not expected_images<=attached_images:
                        raise CoreError('RAG_PROFILE_INCOMPLETE','Не все изображения профиля удалось приложить к запросу. Допускается до2изображений общим размером до5МиБ; выберите компактный профиль.')
                    for number,portion in enumerate(portions,1):
                        generated=self.model.generate(f"{run['run_id']}:{number}",model_prompt(portion,sources,run['focus'],catalogue=catalogue),**({'images':images} if images else {}))
                        assessments.extend(parse_assessments(portion,sources,generated['answer'],catalogue=catalogue))
                        if model is not None and model!=generated['model']:
                            raise CoreError('RADAR_INVALID_RESPONSE','Модель изменилась во время обработки. Отчёт не опубликован; создайте новый запуск с одной моделью.')
                        model=generated['model']
                report=make_report(rows,sources,assessments,self.now(),prefiltered=prefiltered,stock=stock,
                    allowed=percentage(selection['max_shortage_percent']))
                report['selection']=selection
                report['stock_source']=stock_source
                report['funnel']['model_requests']=len(portions)
                report['batch']={'batch_id':str(batch['batch_id']),'filename':batch['filename'],'artifact_id':str(batch['artifact_id'])}
                self.repository.finish(run['run_id'],report=report,model=model)
            except CoreError as error:
                message=error.message if error.code.startswith(('KNOWLEDGE_','EMBEDDING_','RADAR_STOCK_')) or error.code in {'RAG_UNREADABLE','RAG_PROFILE_INCOMPLETE','RADAR_CONTEXT_LIMIT','RADAR_INVALID_RESPONSE'} else MODEL_FAILURE_MESSAGES.get(error.code,'Не удалось проверить данные радара; результат не опубликован. Проверьте источники и создайте новый запуск.')
                self.repository.finish(run['run_id'],error=message)
            except Exception:
                self.repository.finish(run['run_id'],error='Не удалось завершить радар; непроверенный результат не опубликован. Автоматического повтора нет.')
            return True

    def _run_full(self,actor,run):
        selection=run['selection'];filters=selection['filters'];now=self.now()
        originals=[]
        for name,role in (('procurement_source','procurement'),('stock_source','stock'),('economics_source','economics')):
            snapshot=selection[name];ref=ArtifactRef(UUID(snapshot['artifact_id']))
            meta=self.artifacts.prepared_metadata(actor,ref)
            if meta['usage_role']!=role:raise CoreError('VALIDATION_ERROR','Назначение выбранного файла изменено; создайте новый запуск.')
            originals.append(self.artifacts.read(actor,ref))
        batch=self.repository.batch(actor,run['batch_id']);rows=batch['data']['records']
        if self.stock_importer is None or self.economics_importer is None:
            raise CoreError('RADAR_ECONOMICS_INVALID','Не настроены адаптеры склада и экономики.')
        stock=self.stock_importer.parse(originals[1].content,originals[1].metadata['filename'])
        economics=self.economics_importer.parse(originals[2].content,originals[2].metadata['filename'])
        # Complete context rejects partial indexes. Only stock defines product interests.
        source_ref=selection['stock_source']
        sources=self.memory.context(actor,[source_ref],'',complete=True)
        if not sources or any(s.get('truncated') or s.get('status') not in ('ready',None) for s in sources):
            raise CoreError('RAG_PROFILE_INCOMPLETE','Полный индекс выбранного склада недоступен.')
        candidates,prefiltered=filter_rows(rows,sources,now,filters)
        errors=[];safe=[];assessments=[];model=None;requests=0
        for row in candidates:
            try:batches([row],sources,filters['focus'],catalogue=stock['items']);safe.append(row)
            except CoreError as error:errors.append(dict(ids=[row['id']],code=error.code,message=error.message))
        portions=batches(safe,sources,filters['focus'],catalogue=stock['items'])
        def report(complete):
            result=full_report(rows,sources,assessments,now,prefiltered=prefiltered,stock=stock,economics=economics,
                filters=filters,errors=errors,import_data=batch['data'],complete=complete)
            result.update(selection=selection,stock_source=selection['stock_source'],economics_source=selection['economics_source'])
            result['batch']={k:v for k,v in dict(batch_id=str(batch['batch_id']),filename=batch['filename'],artifact_id=str(batch['artifact_id'])).items()}
            result['funnel']['model_requests']=requests
            return result
        self.repository.checkpoint(run['run_id'],report(False),model=model)
        rate_limited=False
        for number,portion in enumerate(portions,1):
            try:
                if self.model is None:raise CoreError('MODEL_UNCONFIGURED','Модель не настроена.')
                requests+=1
                generated=self.model.generate(f"{run['run_id']}:{number}",model_prompt(portion,sources,filters['focus'],catalogue=stock['items']))
                parsed=parse_assessments(portion,sources,generated['answer'],catalogue=stock['items'])
                if model is not None and generated['model']!=model:raise CoreError('RADAR_INVALID_RESPONSE','Модель изменилась во время прохода.')
                model=generated['model'];assessments.extend(parsed)
            except CoreError as error:
                errors.append(dict(ids=[r['id'] for r in portion],code=error.code,message=MODEL_FAILURE_MESSAGES.get(error.code,error.message)))
                rate_limited=error.code=='MODEL_RATE_LIMITED'
            except Exception:errors.append(dict(ids=[r['id'] for r in portion],code='RADAR_PORTION_FAILED',message='Порция не проверена из-за ошибки исполнения.'))
            self.repository.checkpoint(run['run_id'],report(False),model=model)
            if rate_limited:break
        self.repository.finish(run['run_id'],report=report(not errors),model=model,
            error=('Достигнут лимит запросов провайдера. Ожидание исчерпано; анализ остановлен. '
                'Частичный отчёт сохранён, оставшиеся порции не отправлялись.' if rate_limited else
                'Не все порции проверены. Сохранён частичный отчёт; непроверенные строки отмечены.' if errors else None))
