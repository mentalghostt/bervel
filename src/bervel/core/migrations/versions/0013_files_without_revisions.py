"""Give every immutable original its own identity; preserve historical evidence in place."""
from hashlib import sha256
import json
from uuid import UUID, uuid5
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0013_files_without_revisions'
down_revision = '0012_assistant_workflows'
branch_labels = None
depends_on = None


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class References:
    """Only explicitly named technical locations are transformed, never business payload trees."""
    def __init__(self, originals):
        self.originals = originals
        self.mapped = {}

    def identity(self, artifact_id, number):
        key = UUID(str(artifact_id))
        original = self.originals.get(key)
        if number is None or original is None or number == original['current_revision']:
            return str(key)
        target = uuid5(key, f'bervel:file-revision:{number}')
        self.mapped[target] = original
        return str(target)

    def ref(self, value):
        if not isinstance(value, dict) or 'artifact_id' not in value:
            return value
        result = dict(value)
        number = result.get('artifact_revision', result.get('revision', result.get('expected_revision')))
        result['artifact_id'] = self.identity(result['artifact_id'], number)
        for key in (('artifact_revision',) if 'dataset_id' in result else ('revision', 'artifact_revision', 'expected_revision')):
            result.pop(key, None)
        for key in ('url', 'download_url', 'content_url', 'preview_url'):
            if key in result and isinstance(result[key], str):
                old = str(value['artifact_id'])
                result[key] = result[key].replace(f'/artifacts/{old}/revisions/{number}', f"/artifacts/{result['artifact_id']}")
        return result

    def refs(self, value):
        return [self.ref(v) for v in value] if isinstance(value, list) else self.ref(value)

    def selection(self, value):
        if not isinstance(value, dict): return value
        out = dict(value)
        for key in ('procurement_source', 'stock_source', 'economics_source'):
            if key in out: out[key] = self.ref(out[key])
        return out

    def report(self, value):
        if not isinstance(value, dict): return value
        out = self.selection(value)
        for key in ('sources', 'sources_snapshot'):
            if key in out: out[key] = self.refs(out[key])
        if 'batch' in out: out['batch'] = self.ref(out['batch'])
        if 'selection' in out: out['selection'] = self.selection(out['selection'])
        return out

    def arguments(self, operation, value):
        if not isinstance(value, dict): return value
        if operation.startswith('file.') or operation == 'radar.import': return self.ref(value)
        if operation == 'radar.analyse':
            return {k:self.ref(v) if k in ('procurement','stock','economics') else v for k,v in value.items()}
        return value

    def card(self, value):
        if not isinstance(value, dict): return value
        out = dict(value)
        if out.get('kind') == 'file': return self.ref(out)
        operation, data = out.get('operation', ''), out.get('data')
        if operation == 'file.read' and isinstance(data, dict):
            data = {**data, 'file':self.ref(data.get('file')), 'sources':self.refs(data.get('sources', []))}
        elif operation == 'file.list' and isinstance(data, dict):
            data = {**data, 'artifacts':self.refs(data.get('artifacts', []))}
        elif operation == 'file.latest_ready' and isinstance(data, dict):
            data = {k:self.ref(v) if k in ('procurement','stock','economics') else v for k,v in data.items()}
        elif operation == 'file.history' and isinstance(data, dict):
            data = {k:self.refs(v) if k == 'revisions' else v for k,v in data.items()}
        elif operation in ('file.knowledge','file.prepare','file.role','file.delete','radar.import'):
            data = self.ref(data)
        elif operation == 'radar.batches': data = self.refs(data)
        elif operation == 'radar.read' and isinstance(data, dict):
            data = self.report(data)
            if 'report' in data: data['report'] = self.report(data['report'])
        if 'data' in out: out['data'] = data
        return out

    def document_index(self, value):
        out = dict(value)
        if 'sources' in out: out['sources'] = self.refs(out['sources'])
        if 'chunks' in out:
            out['chunks'] = [{**chunk, **({'source':self.ref(chunk['source'])} if 'source' in chunk else {})}
                             for chunk in out['chunks']]
        return out

    def inputs(self, value):
        if isinstance(value, list): return [self.ref(v) for v in value]
        if not isinstance(value, dict): return value
        out = dict(self.ref(value))
        for key in ('artifact', 'artifact_ref', 'artifacts', 'sources', 'inputs', 'original'):
            if key in out: out[key] = self.refs(out[key])
        if 'input_snapshot' in out: out['input_snapshot'] = self.inputs(out['input_snapshot'])
        return out


def upgrade():
    connection = op.get_bind()
    originals = {r['artifact_id']:dict(r) for r in connection.execute(sa.text('SELECT * FROM artifacts')).mappings()}
    variants = list(connection.execute(sa.text('SELECT * FROM artifact_revisions')).mappings())
    refs = References(originals)
    op.add_column('artifacts', sa.Column('metadata', JSONB, nullable=True))
    # Split identities before redirecting foreign keys. Deleted logical archives remain deleted.
    for variant in variants:
        original = originals[variant['artifact_id']]
        target = UUID(refs.identity(variant['artifact_id'], variant['revision']))
        metadata = refs.ref(variant['metadata'])
        if target == variant['artifact_id']:
            connection.execute(sa.text('UPDATE artifacts SET metadata=CAST(:metadata AS jsonb) WHERE artifact_id=:id'),
                               {'id':target, 'metadata':json.dumps(metadata)})
        else:
            connection.execute(sa.text('''INSERT INTO artifacts(artifact_id,data_scope,owner_module_id,current_revision,deleted_at,usage_role,metadata)
                VALUES(:id,:scope,:owner,1,:deleted,:role,CAST(:metadata AS jsonb))'''),
                dict(id=target,scope=original['data_scope'],owner=original['owner_module_id'],deleted=original['deleted_at'],role=original['usage_role'],metadata=json.dumps(metadata)))
    inspector = sa.inspect(connection)
    indexed = ('csv_chunks','document_indexes','knowledge_jobs','knowledge_chunks')
    related = (*indexed,'dataset_revisions','radar_batches')
    for table in related:
        for fk in inspector.get_foreign_keys(table):
            if fk['referred_table']=='artifact_revisions': op.drop_constraint(fk['name'],table,type_='foreignkey')
        column = 'revision' if table in indexed else 'artifact_revision'
        rows = connection.execute(sa.text(f'SELECT DISTINCT artifact_id,{column} FROM {table}')).mappings().all()
        for row in rows:
            target = UUID(refs.identity(row['artifact_id'], row[column]))
            if target != row['artifact_id']:
                connection.execute(sa.text(f'UPDATE {table} SET artifact_id=:target WHERE artifact_id=:id AND {column}=:number'),
                                   dict(target=target,id=row['artifact_id'],number=row[column]))
        if table in indexed:
            op.drop_constraint(inspector.get_pk_constraint(table)['name'],table,type_='primary')
        op.drop_column(table,column)
        if table in indexed:
            op.create_primary_key(f'{table}_pkey',table,['artifact_id','ordinal'] if table in ('csv_chunks','knowledge_chunks') else ['artifact_id'])
        if table != 'radar_batches': op.create_foreign_key(f'{table}_artifact_id_fkey',table,'artifacts',['artifact_id'],['artifact_id'])
    # Batches may reference purged files whose old variants no longer have rows.
    # Their JSON refs use the same deterministic mapping as all retained source links.
    inspector.clear_cache()
    tables = sa.MetaData()
    def transform(table_name, converters):
        key_names = inspector.get_pk_constraint(table_name)['constrained_columns']
        table = sa.table(table_name, *(sa.column(k) for k in key_names),
                         *(sa.column(k,JSONB) for k in converters))
        for row in connection.execute(sa.select(table)).mappings():
            values = {name:convert(row[name]) for name,convert in converters.items()}
            if any(values[name] != row[name] for name in values):
                connection.execute(table.update().where(*(table.c[k]==row[k] for k in key_names)).values(**values))
    transform('chat_turns', {'snapshot':refs.refs,'sources':refs.refs,'results':lambda v:[refs.card(x) for x in v]})
    transform('radar_runs', {'sources_snapshot':refs.refs,'selection':refs.selection,'report':refs.report})
    transform('knowledge_chunks', {'source':refs.ref})
    transform('document_indexes', {'result':refs.document_index})
    transform('process_jobs', {'command':refs.inputs})
    transform('input_snapshots', {'inputs':refs.inputs})
    transform('dataset_revisions', {'envelope':refs.inputs})
    transform('data_records', {'record':lambda v:{**v,**({key:refs.refs(v[key]) for key in ('evidence','evidence_refs') if key in v})}})
    actions=sa.Table('chat_actions',tables,autoload_with=connection)
    for row in connection.execute(sa.select(actions)).mappings():
        arguments=refs.arguments(row['operation'],row['arguments'])
        connection.execute(actions.update().where(actions.c.action_id==row['action_id']).values(
            arguments=arguments,result=refs.card(row['result']),fingerprint=_digest({'operation':row['operation'],'arguments':arguments})))
    receipts=sa.Table('command_receipts',tables,autoload_with=connection)
    by_ref={(str(v['artifact_id']),v['revision']):v['metadata'] for v in variants}
    for row in connection.execute(sa.select(receipts)).mappings():
        operation=row['operation']; result=row['result']; fingerprint=row['fingerprint']
        converted=refs.ref(result) if operation.startswith('artifact.') or operation=='radar.import' else result
        if operation=='artifact.register' and isinstance(result,dict):
            metadata=by_ref.get((result.get('artifact_id'),result.get('revision')))
            if metadata:
                significant={key:metadata[key] for key in ('sha256','size','filename','media_type','source','source_role')}
                fingerprint=_digest(significant)
        if operation.startswith('chat.') and isinstance(result,dict) and 'result' in result:
            converted={**result,'result':refs.card(result['result'])}
        connection.execute(receipts.update().where(*(receipts.c[k]==row[k] for k in ('data_scope','actor_id','operation','command_key'))).values(result=converted,fingerprint=fingerprint))
    audits=sa.Table('audit',tables,autoload_with=connection)
    for row in connection.execute(sa.select(audits)).mappings():
        if row['operation'].startswith('artifact.') or row['operation']=='radar.import':
            connection.execute(audits.update().where(audits.c.audit_id==row['audit_id']).values(object_ref=refs.ref(row['object_ref'])))
    # No original bytes are recreated for already purged variants. Preserve scope for honest 410/404.
    for target, original in refs.mapped.items():
        connection.execute(sa.text("""INSERT INTO artifacts(artifact_id,data_scope,owner_module_id,current_revision,deleted_at,usage_role,metadata)
            VALUES(:id,:scope,:owner,1,COALESCE(:deleted,now()),:role,NULL) ON CONFLICT DO NOTHING"""),
            dict(id=target,scope=original['data_scope'],owner=original['owner_module_id'],deleted=original['deleted_at'],role=original['usage_role']))
    connection.execute(sa.text("""UPDATE datasets d SET artifact_id=r.artifact_id FROM dataset_revisions r
        WHERE d.dataset_id=r.dataset_id AND d.current_revision=r.revision"""))
    op.drop_table('artifact_revisions')
    op.drop_column('artifacts','current_revision')


def downgrade():
    raise RuntimeError('Independent file identities cannot be merged back into revision histories')
