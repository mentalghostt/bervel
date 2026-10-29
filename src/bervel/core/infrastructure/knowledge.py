"""Scoped PostgreSQL evidence index; exact pgvector search plus lexical ranking."""
from contextlib import contextmanager
from hashlib import sha256
import json
import math

from sqlalchemy import text

from bervel.core.errors import CoreError
from bervel.core.infrastructure.documents import PostgresDocumentRepository
from bervel.core.models import ArtifactRef
from bervel.core.services import authorize
from bervel.core.chunking import BATCH_TOKENS

MODEL='text-embedding-3-small'


def queue_artifact(connection,ref:ArtifactRef):
    connection.execute(text('INSERT INTO knowledge_jobs(artifact_id) VALUES(:id) ON CONFLICT DO NOTHING'),
                       {'id':ref.artifact_id})


def knowledge_lock_key(ref: ArtifactRef) -> int:
    return int.from_bytes(sha256(f'knowledge:{ref.artifact_id}'.encode()).digest()[:8], 'big', signed=True)


class PostgresKnowledgeRepository(PostgresDocumentRepository):
    def __init__(self,engine,*,model=MODEL+':1536',dimensions=1536):
        super().__init__(engine)
        self.model,self.dimensions=model,dimensions
    def status(self,actor,ref):
        authorize(actor,'artifact:read')
        with self.engine.connect() as c:
            row=c.execute(text('''SELECT j.state,j.error,j.total_chunks,j.completed_chunks,
                (SELECT k.model FROM knowledge_chunks k WHERE k.artifact_id=a.artifact_id LIMIT 1) AS embedding_model
                FROM artifacts a LEFT JOIN knowledge_jobs j ON j.artifact_id=a.artifact_id
                WHERE a.artifact_id=:id AND a.data_scope=:scope'''),{'id':ref.artifact_id,'scope':actor.data_scope}).mappings().first()
        if row is None: raise CoreError('NOT_FOUND','Файл не найден.')
        return dict(row) if row['state'] else dict(state='pending',error=None,total_chunks=0,completed_chunks=0)

    def resume(self,actor,ref):
        authorize(actor,'artifact:write')
        self.status(actor,ref)
        with self.engine.begin() as c:
            c.execute(text("UPDATE knowledge_jobs SET state='pending',error=NULL,updated_at=now() WHERE artifact_id=:id AND state='failed'"),{'id':ref.artifact_id})

    @contextmanager
    def claim(self):
        with self.engine.begin() as owner:
            jobs=owner.execute(text("""SELECT j.artifact_id,a.data_scope,j.state,j.total_chunks,j.source_status,
                (SELECT model FROM knowledge_chunks k WHERE k.artifact_id=j.artifact_id LIMIT 1) AS embedding_model
                FROM knowledge_jobs j
                JOIN artifacts a USING(artifact_id) WHERE j.state IN ('pending','running') AND a.deleted_at IS NULL
                ORDER BY j.updated_at,j.artifact_id LIMIT 100""")).mappings().all()
            for job in jobs:
                key=knowledge_lock_key(ArtifactRef(job['artifact_id']))
                if not owner.execute(text('SELECT pg_try_advisory_xact_lock(:key)'),{'key':key}).scalar(): continue
                with self.engine.begin() as c:
                    state=c.execute(text("SELECT state FROM knowledge_jobs WHERE artifact_id=:id AND state IN ('pending','running') FOR UPDATE"),{'id':job['artifact_id']}).scalar()
                    if state is not None:
                        c.execute(text("UPDATE knowledge_jobs SET state='running',updated_at=now() WHERE artifact_id=:id"),{'id':job['artifact_id']})
                if state is None: continue
                yield {**job,'interrupted':state=='running'}
                return
            yield None

    def prepare(self,ref,blocks):
        with self.engine.begin() as c:
            count=c.execute(text('SELECT count(*) FROM knowledge_chunks WHERE artifact_id=:id'),{'id':ref.artifact_id}).scalar()
            if count: return
            rows=[]
            for ordinal,block in enumerate(blocks):
                value=block['embedding_input']
                source={k:v for k,v in block.items() if k not in ('embedding_input','version','tokens')}
                rows.append(dict(id=ref.artifact_id,ordinal=ordinal,version=block['version'],
                    source=json.dumps(source,ensure_ascii=False),value=value,tokens=block['tokens'],hash=sha256(value.encode()).hexdigest(),model=self.model))
            if rows: c.execute(text('''INSERT INTO knowledge_chunks(artifact_id,ordinal,version,source,embedding_input,content_hash,model,tokens)
                VALUES(:id,:ordinal,:version,CAST(:source AS jsonb),:value,:hash,:model,:tokens)'''),rows)
            c.execute(text('UPDATE knowledge_jobs SET total_chunks=:total,source_status=:status WHERE artifact_id=:id'),
                {'id':ref.artifact_id,'total':len(rows),'status':blocks[0]['status'] if blocks else 'ready'})

    def pending(self,ref):
        with self.engine.begin() as c:
            c.execute(text('''UPDATE knowledge_chunks target SET embedding=(SELECT cached.embedding
                FROM knowledge_chunks cached JOIN artifacts donor ON donor.artifact_id=cached.artifact_id
                JOIN artifacts current ON current.artifact_id=target.artifact_id
                WHERE cached.model=target.model AND cached.content_hash=target.content_hash
                  AND donor.data_scope=current.data_scope AND cached.embedding IS NOT NULL LIMIT 1)
                WHERE target.artifact_id=:id AND target.embedding IS NULL
                  AND EXISTS(SELECT 1 FROM knowledge_chunks cached WHERE cached.model=target.model
                    AND cached.content_hash=target.content_hash AND cached.embedding IS NOT NULL)'''),{'id':ref.artifact_id})
            rows=c.execute(text('''SELECT ordinal,embedding_input,tokens FROM knowledge_chunks
                WHERE artifact_id=:id AND embedding IS NULL ORDER BY ordinal LIMIT 64'''),{'id':ref.artifact_id}).mappings().all()
        selected,size=[],0
        for row in rows:
            length=row['tokens']
            if selected and size+length>BATCH_TOKENS: break
            selected.append(dict(row)); size+=length
        return selected

    def save_vectors(self,ref,rows,vectors):
        if len(rows)!=len(vectors): raise CoreError('EMBEDDING_INVALID_RESPONSE','Не все фрагменты получили эмбеддинги.')
        values=[]
        for row,vector in zip(rows,vectors):
            if len(vector)!=self.dimensions or any(type(n) not in (int,float) or not math.isfinite(n) for n in vector) or not any(vector):
                raise CoreError('EMBEDDING_INVALID_RESPONSE','Некорректный вектор фрагмента.')
            values.append(dict(id=ref.artifact_id,ordinal=row['ordinal'],vector=json.dumps(vector)))
        with self.engine.begin() as c:
            c.execute(text('''UPDATE knowledge_chunks SET embedding=CAST(:vector AS public.vector)
                WHERE artifact_id=:id AND ordinal=:ordinal AND embedding IS NULL'''),values)

    def finish(self,ref,state,error=None):
        with self.engine.begin() as c:
            c.execute(text('UPDATE knowledge_jobs SET state=:state,error=:error,updated_at=now() WHERE artifact_id=:id'),
                      dict(id=ref.artifact_id,state=state,error=error))

    def finish_portion(self,ref,status):
        with self.engine.begin() as c:
            c.execute(text('''UPDATE knowledge_jobs SET completed_chunks=(SELECT count(*) FROM knowledge_chunks
                WHERE artifact_id=:id AND embedding IS NOT NULL),updated_at=now(),error=NULL,
                state=CASE WHEN EXISTS(SELECT 1 FROM knowledge_chunks WHERE artifact_id=:id AND embedding IS NULL)
                THEN 'pending' ELSE :done END WHERE artifact_id=:id'''),
                dict(id=ref.artifact_id,done='partial' if status=='partial' else 'ready'))

    def _parameters(self,actor,catalog):
        return {'scope':actor.data_scope,'model':self.model,'dimensions':self.dimensions,
                'refs':json.dumps([{'artifact_id':s['artifact_id'],'filename':s['filename']} for s in catalog])}

    _eligible='''FROM knowledge_chunks k JOIN artifacts a USING(artifact_id)
        JOIN knowledge_jobs j ON j.artifact_id=k.artifact_id
        JOIN jsonb_to_recordset(CAST(:refs AS jsonb)) AS allowed(artifact_id uuid,filename text)
          ON k.artifact_id=allowed.artifact_id
        WHERE a.data_scope=:scope AND a.deleted_at IS NULL AND j.state IN ('ready','partial')
          AND k.model=:model'''

    def has_vectors(self,actor,catalog):
        with self.engine.connect() as c:
            return bool(c.execute(text('SELECT EXISTS(SELECT 1 '+self._eligible+' AND embedding IS NOT NULL)'),self._parameters(actor,catalog)).scalar())

    def complete(self,actor,catalog):
        with self.engine.connect() as c:
            rows=c.execute(text('SELECT k.artifact_id,k.ordinal,k.source '+self._eligible+' ORDER BY k.artifact_id,k.ordinal'),self._parameters(actor,catalog)).mappings().all()
        return self._sources(rows,catalog)

    def lexical_search(self,actor,catalog,question):
        parameters={**self._parameters(actor,catalog),'question':question}
        # Use discriminating terms present in this scoped corpus. A conversational
        # question need not match every word, while a common term alone must not
        # drown out files matching its more specific terms.
        sql='''WITH eligible AS (SELECT k.artifact_id,k.ordinal,k.source,allowed.filename,
                to_tsvector('russian',coalesce(k.source->>'excerpt','')) AS document '''+self._eligible+'''),
            query_terms AS (SELECT unnest(tsvector_to_array(to_tsvector('russian',:question))) AS term),
            term_matches AS (SELECT e.artifact_id,e.ordinal,q.term FROM eligible e
                JOIN query_terms q ON q.term=ANY(tsvector_to_array(e.document))),
            frequencies AS (SELECT term,count(DISTINCT artifact_id) AS files FROM term_matches GROUP BY term),
            anchors AS (SELECT term FROM frequencies WHERE files=(SELECT min(files) FROM frequencies)),
            scored AS (SELECT e.artifact_id,e.ordinal,e.source,
                position(lower(e.filename) in lower(:question))>0 AS named,
                EXISTS(SELECT 1 FROM term_matches m JOIN anchors a USING(term)
                    WHERE m.artifact_id=e.artifact_id AND m.ordinal=e.ordinal) AS lexical_match,
                (SELECT count(*) FROM term_matches m WHERE m.artifact_id=e.artifact_id AND m.ordinal=e.ordinal) AS hits
                FROM eligible e),
            ranked AS (SELECT *,row_number() OVER(PARTITION BY artifact_id ORDER BY hits DESC,ordinal) AS file_rank
                FROM scored WHERE named OR lexical_match)
            SELECT artifact_id,ordinal,source,named,lexical_match FROM ranked
            ORDER BY named DESC,file_rank,hits DESC,artifact_id,ordinal LIMIT 48'''
        with self.engine.connect() as c: rows=c.execute(text(sql),parameters).mappings().all()
        return self._sources(rows,catalog)

    def search(self,actor,catalog,question,vector):
        parameters={**self._parameters(actor,catalog),'question':question,'vector':json.dumps(vector)}
        sql='''WITH eligible AS (SELECT k.*,allowed.filename '''+self._eligible+''' AND embedding IS NOT NULL),
            semantic AS (SELECT artifact_id,ordinal,row_number() OVER(ORDER BY embedding OPERATOR(public.<=>) CAST(:vector AS public.vector),artifact_id,ordinal) AS rank
                FROM eligible ORDER BY embedding OPERATOR(public.<=>) CAST(:vector AS public.vector),artifact_id,ordinal LIMIT 48),
            lexical AS (SELECT artifact_id,ordinal,row_number() OVER(ORDER BY
                (CASE WHEN position(lower(filename) in lower(:question))>0 THEN 1 ELSE 0 END +
                ts_rank_cd(to_tsvector('russian',source->>'excerpt'),plainto_tsquery('russian',:question))) DESC,artifact_id,ordinal) AS rank
                FROM eligible WHERE position(lower(filename) in lower(:question))>0 OR to_tsvector('russian',source->>'excerpt') @@ plainto_tsquery('russian',:question) LIMIT 48),
            named AS (SELECT artifact_id,ordinal,row_number() OVER(PARTITION BY artifact_id
                ORDER BY embedding OPERATOR(public.<=>) CAST(:vector AS public.vector),ordinal) AS rank
                FROM eligible WHERE position(lower(filename) in lower(:question))>0)
            SELECT e.artifact_id,e.ordinal,e.source,
                coalesce(n.rank=1,false) AS named,
                l.rank IS NOT NULL AS lexical_match,
                coalesce(1.0/(60+s.rank),0)+coalesce(1.0/(60+l.rank),0) AS score FROM eligible e
            LEFT JOIN semantic s USING(artifact_id,ordinal) LEFT JOIN lexical l USING(artifact_id,ordinal)
            LEFT JOIN named n USING(artifact_id,ordinal)
            WHERE s.rank IS NOT NULL OR l.rank IS NOT NULL OR n.rank=1 ORDER BY named DESC,score DESC,e.artifact_id,e.ordinal LIMIT 48'''
        with self.engine.connect() as c: rows=c.execute(text(sql),parameters).mappings().all()
        return self._sources(rows,catalog)

    @staticmethod
    def _sources(rows,catalog):
        metadata={s['artifact_id']:s for s in catalog}
        return [{**metadata[str(r['artifact_id'])],**r['source'],'ordinal':r['ordinal'],'named':bool(r.get('named',False)),
            'lexical_match':bool(r.get('lexical_match',False))} for r in rows]
