"""Shared indexing use case; business modules receive evidence, never vectors."""
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from bervel.core.chunking import embedding_input, fit_to_model, split_blocks, text_batches, CHUNK_VERSION
from bervel.core.errors import CoreError
from bervel.core.models import ActorContext, ArtifactRef


class EmbeddingClient(Protocol):
    space: str
    dimensions: int
    max_tokens: int
    def count_tokens(self,value:str)->int: ...
    def embed(self,texts:list[str],*,purpose:str='document') -> list[list[float]]: ...


@dataclass(frozen=True)
class RetrievalResult:
    sources: list[dict]
    mode: str
    notice: str | None = None
    reason: str | None = None


class KnowledgeService:
    def __init__(self,repository,artifacts,reader,embeddings:EmbeddingClient):
        self.repository,self.artifacts,self.reader,self.embeddings=repository,artifacts,reader,embeddings

    def status(self,actor,ref):
        self.artifacts.metadata(actor,ref)
        return self.repository.status(actor,ref)

    def resume(self,actor,ref):
        self.artifacts.metadata(actor,ref)
        self.repository.resume(actor,ref)

    def run_once(self) -> bool:
        with self.repository.claim() as job:
            if job is None: return False
            ref=ArtifactRef(job['artifact_id'])
            if job['interrupted']:
                self.repository.finish(ref,'failed','Индексация прервана. Готовые порции сохранены; продолжите явно.')
                return True
            actor=ActorContext('knowledge-indexer',job['data_scope'],frozenset({'artifact:read','dataset:read'}))
            try:
                if job.get('embedding_model') and job['embedding_model']!=self.embeddings.space:
                    raise CoreError('KNOWLEDGE_REINDEX_REQUIRED','Индекс создан другой embedding-моделью; требуется явная переиндексация.')
                original=self.artifacts.read(actor,ref)
                snapshot=original.metadata
                source_status=job['source_status']
                if not job['total_chunks']:
                    index=self.repository.document_index(snapshot)
                    if index is None:
                        index=self.reader.extract(original.content,snapshot['filename'],snapshot['media_type'])
                        self.repository.save_document_index(snapshot,index)
                    if index['status']=='metadata_only':
                        self.repository.finish(ref,'unsupported',index['chunks'][0]['excerpt'])
                        return True
                    blocks=fit_to_model(snapshot['filename'],index['chunks'],self.embeddings.count_tokens,self.embeddings.max_tokens)
                    self.repository.prepare(ref,[{**block,'embedding_input':embedding_input(snapshot['filename'],block),
                        'tokens':self.embeddings.count_tokens(embedding_input(snapshot['filename'],block)),
                        'version':CHUNK_VERSION,'status':index['status']} for block in blocks])
                    source_status=index['status']
                pending=self.repository.pending(ref)
                if pending:
                    vectors=self.embeddings.embed([row['embedding_input'] for row in pending])
                    self.repository.save_vectors(ref,pending,vectors)
                self.repository.finish_portion(ref,source_status)
            except CoreError as error:
                self.repository.finish(ref,'failed',error.message if error.code.startswith(('EMBEDDING_','KNOWLEDGE_'))
                    else 'Не удалось прочитать оригинал для индексации. Проверьте его доступность и целостность.')
            except Exception:
                self.repository.finish(ref,'failed','Не удалось завершить индексацию. Оригинал и готовые порции сохранены.')
            return True

    def retrieve(self,actor:ActorContext,catalog:list[dict],question:str,*,complete:bool=False)->list[dict]:
        return self.retrieve_result(actor,catalog,question,complete=complete).sources

    def retrieve_result(self,actor:ActorContext,catalog:list[dict],question:str,*,complete:bool=False)->RetrievalResult:
        for item in catalog:
            self.artifacts.metadata(actor,ArtifactRef(UUID(item['artifact_id'])))
        unavailable=[]
        indexed=[]
        for item in catalog:
            status=self.status(actor,ArtifactRef(UUID(item['artifact_id'])))
            if status['state'] not in ('ready','partial','unsupported'):
                message=f"Файл «{item['filename']}» ещё не проиндексирован. Проверьте состояние на странице файлов."
                if complete: raise CoreError('KNOWLEDGE_NOT_READY',message)
                unavailable.append({**item,'excerpt':message,'unit':'metadata','row_start':0,'row_end':0,
                    'total_rows':0,'status':'metadata_only','truncated':False})
                continue
            if status.get('embedding_model') and status['embedding_model']!=self.embeddings.space:
                raise CoreError('KNOWLEDGE_REINDEX_REQUIRED','Индекс создан другой embedding-моделью. Требуется явная переиндексация; векторы не смешивались.')
            if status['state']=='unsupported':
                unavailable.append({**item,'excerpt':status['error'] or 'Формат не прочитан.','unit':'metadata',
                    'row_start':0,'row_end':0,'total_rows':0,'status':'metadata_only','truncated':False})
            else: indexed.append(item)
        if complete:
            return RetrievalResult(self.repository.complete(actor,indexed)+unavailable,'complete')
        if not indexed: return RetrievalResult(unavailable,'metadata')
        try:
            queries=fit_to_model('Поисковый запрос',split_blocks([dict(excerpt=question,unit='line',row_start=1,row_end=1,total_rows=1)]),
                self.embeddings.count_tokens,self.embeddings.max_tokens)
            vectors=[]
            for portion in text_batches([q['excerpt'] for q in queries],self.embeddings.count_tokens):
                vectors.extend(self.embeddings.embed(portion,purpose='query'))
        except CoreError as error:
            if error.code not in {'EMBEDDING_REGION_UNAVAILABLE','EMBEDDING_UNAVAILABLE','EMBEDDING_AUTH_ERROR',
                                  'EMBEDDING_RATE_LIMIT','EMBEDDING_UNCONFIGURED','EMBEDDING_INVALID_RESPONSE'}:
                raise
            notice=('Семантический поиск недоступен. Выполнен поиск по словам в готовых фрагментах файлов; '
                    'смысловые совпадения без общих слов могли быть пропущены. Полное покрытие базы не подтверждено.')
            sources=self.repository.lexical_search(actor,indexed,question)
            return RetrievalResult([{**source,'retrieval_mode':'lexical','notice':notice} for source in sources]+unavailable,
                                   'lexical',notice,error.code)
        merged={}
        for vector in vectors:
            for rank,source in enumerate(self.repository.search(actor,indexed,question,vector),1):
                key=(source['artifact_id'],source['ordinal'])
                score,old=merged.get(key,(0,source))
                merged[key]=(score+1/(60+rank),old)
        ordered=sorted(merged.values(),key=lambda value:(-int(value[1].get('named',False)),-value[0],value[1]['artifact_id'],value[1]['ordinal']))
        return RetrievalResult([source for _,source in ordered[:48]]+unavailable,'hybrid')
