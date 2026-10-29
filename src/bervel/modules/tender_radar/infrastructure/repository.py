"""Scoped immutable imports and narrow PostgreSQL-backed radar queue."""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
from uuid import UUID,uuid4
from sqlalchemy import insert,select,text,update
from bervel.core.errors import CoreError
from bervel.modules.tender_radar.infrastructure.tables import radar_batches,radar_runs
from bervel.core.infrastructure.commands import CommandReceipts,digest


def view(row):
    return {**{key:row[key] for key in ('state','focus','sources_snapshot','report','model','error')},
        'selection':row.get('selection') or {'stock_source':None,'max_shortage_percent':'0'},
        'run_id':str(row['run_id']),'batch_id':str(row['batch_id']),'created_at':row['created_at'].isoformat()}


def batch_view(row):
    return {'batch_id':str(row['batch_id']),'artifact_id':str(row['artifact_id']),
        'filename':row['filename'],'created_at':row['created_at'].isoformat(),
        'count':len(row['data']['records']),**{key:row['data'][key] for key in ('warnings','skipped_rows','duplicate_rows')}}


class PostgresRadarRepository:
    def __init__(self,engine):
        self.engine=engine
        self.receipts=CommandReceipts()

    def _batch(self,connection,actor,batch_id):
        row=connection.execute(select(radar_batches).where(radar_batches.c.batch_id==batch_id,radar_batches.c.data_scope==actor.data_scope)).mappings().first()
        if row is None: raise CoreError('NOT_FOUND','Партия тендеров не найдена.')
        return row

    def batch(self,actor,batch_id):
        with self.engine.connect() as connection: return dict(self._batch(connection,actor,batch_id))

    def save_batch(self,actor,metadata,data,key):
        fingerprint=digest({'artifact_id':metadata['artifact_id']})
        with self.engine.begin() as connection:
            repeat=self.receipts.repeat(connection,actor,'radar.import',key,fingerprint)
            if repeat: return batch_view(self._batch(connection,actor,UUID(repeat['batch_id'])))
            row={'batch_id':uuid4(),'actor_id':actor.actor_id,'data_scope':actor.data_scope,
                'artifact_id':UUID(metadata['artifact_id']),
                'filename':metadata['filename'],'created_at':datetime.now(timezone.utc),'data':data}
            connection.execute(insert(radar_batches).values(**row))
            self.receipts.finish(connection,actor,'radar.import',key,fingerprint,{'batch_id':str(row['batch_id'])})
            return batch_view(row)

    def list_batches(self,actor):
        with self.engine.connect() as connection:
            rows=connection.execute(select(radar_batches).where(radar_batches.c.data_scope==actor.data_scope).order_by(radar_batches.c.created_at.desc()).limit(100)).mappings()
            return [batch_view(row) for row in rows]

    def enqueue(self,actor,batch_id,snapshot,focus,key,*,selection=None):
        selection=selection or {'stock_source':None,'max_shortage_percent':'0'}
        command={'batch_id':str(batch_id),'sources':snapshot,'focus':focus}
        if selection.get('pipeline_version') or selection['stock_source'] is not None or selection['max_shortage_percent']!='0':command['selection']=selection
        fingerprint=digest(command)
        with self.engine.begin() as connection:
            self._batch(connection,actor,batch_id)
            repeat=self.receipts.repeat(connection,actor,'radar.start',key,fingerprint)
            if repeat: return view(self._run(connection,actor,UUID(repeat['run_id'])))
            row={'run_id':uuid4(),'batch_id':batch_id,'actor_id':actor.actor_id,'data_scope':actor.data_scope,
                'actor_permissions':sorted(actor.permissions),'sources_snapshot':snapshot,'focus':focus,'state':'queued',
                'selection':selection,
                'created_at':datetime.now(timezone.utc),'report':None,'model':None,'error':None}
            connection.execute(insert(radar_runs).values(**row))
            self.receipts.finish(connection,actor,'radar.start',key,fingerprint,{'run_id':str(row['run_id'])})
            return view(row)

    def _run(self,connection,actor,run_id):
        row=connection.execute(select(radar_runs).where(radar_runs.c.run_id==run_id,radar_runs.c.data_scope==actor.data_scope,radar_runs.c.actor_id==actor.actor_id)).mappings().first()
        if row is None: raise CoreError('NOT_FOUND','Запуск радара не найден.')
        return row

    def read(self,actor,run_id):
        with self.engine.connect() as connection: return view(self._run(connection,actor,run_id))

    def list_runs(self,actor):
        with self.engine.connect() as connection:
            rows=connection.execute(select(radar_runs.c.run_id,radar_runs.c.batch_id,radar_runs.c.state,radar_runs.c.created_at,radar_runs.c.focus)
                .where(radar_runs.c.data_scope==actor.data_scope,radar_runs.c.actor_id==actor.actor_id).order_by(radar_runs.c.created_at.desc()).limit(30)).mappings()
            return [{**dict(row),'run_id':str(row['run_id']),'batch_id':str(row['batch_id']),'created_at':row['created_at'].isoformat()} for row in rows]

    @contextmanager
    def claim(self):
        with self.engine.begin() as owner:
            ids=owner.execute(select(radar_runs.c.run_id).where(radar_runs.c.state.in_(['queued','running'])).order_by(radar_runs.c.created_at)).scalars().all()
            for run_id in ids:
                key=int.from_bytes(hashlib.sha256(('radar:'+str(run_id)).encode()).digest()[:8],'big',signed=True)
                if not owner.execute(text('SELECT pg_try_advisory_xact_lock(:key)'),{'key':key}).scalar(): continue
                with self.engine.begin() as connection:
                    row=connection.execute(select(radar_runs).where(radar_runs.c.run_id==run_id,radar_runs.c.state.in_(['queued','running'])).with_for_update()).mappings().first()
                    if row is None: continue
                    claimed={**row,'interrupted':row['state']=='running'}
                    if not claimed['interrupted']: connection.execute(update(radar_runs).where(radar_runs.c.run_id==run_id).values(state='running'))
                yield claimed
                return
            yield None

    def finish(self,run_id,*,report=None,model=None,error=None):
        with self.engine.begin() as connection:
            values=dict(state='failed' if error else 'completed',error=error)
            if report is not None:values['report']=report
            if model is not None:values['model']=model
            changed=connection.execute(update(radar_runs).where(radar_runs.c.run_id==run_id,radar_runs.c.state=='running').values(**values))
            if changed.rowcount!=1: raise CoreError('STALE_ATTEMPT','Запуск уже завершён.')

    def checkpoint(self,run_id,report,*,model=None):
        with self.engine.begin() as connection:
            changed=connection.execute(update(radar_runs).where(radar_runs.c.run_id==run_id,radar_runs.c.state=='running').values(report=report,model=model))
            if changed.rowcount!=1:raise CoreError('STALE_ATTEMPT','Запуск уже завершён.')
