"""Read-only verification of radar persistence and older application fingerprints."""
import hashlib
import json
from sqlalchemy import select,text
from bervel.core.config import Settings
from bervel.core.infrastructure.database import make_engine
from bervel.modules.tender_radar.infrastructure.tables import radar_runs, radar_batches

payload=input_payload
new_ids=set(payload['artifact_ids'])|set(payload['run_ids'])|set(payload['batch_ids'])
engine=make_engine(Settings.from_environment().database_url)
result={'prior_data':{},'runs':[],'batches':[]}
with engine.connect() as connection:
    for expected in payload['expected_runs']:
        row=connection.execute(select(radar_runs).where(radar_runs.c.run_id==expected['run_id'])).mappings().one()
        assert row['state']=='completed' and row['report']['counts']==expected['counts']
        assert row['model']=='gpt-6.1-sol'
        selected={(s['artifact_id'],s['revision']) for s in row['sources_snapshot']}
        assert selected=={(s['artifact_id'],s['revision']) for s in expected['selected']}
        assert all((s['artifact_id'],s['revision']) in selected for s in row['report']['sources'])
        assert all(item['participation_ready'] is False for item in row['report']['items'])
        assert row['report']['schema_version']=='1.0.0'
        if expected['label']=='current-fasteners':
            recommended=[item for item in row['report']['items'] if item['decision']=='take_in_work']
            assert recommended and all(item['number']!='32616494305' for item in recommended)
        result['runs'].append({'run_id':str(row['run_id']),'label':expected['label'],'counts':row['report']['counts'],'model':row['model'],'selected_files':len(selected)})
    for expected in payload['expected_batches']:
        row=connection.execute(select(radar_batches).where(radar_batches.c.batch_id==expected['batch_id'])).mappings().one()
        assert len(row['data']['records'])==expected['count']
        assert all(item['source_url'].startswith(('https://','http://')) for item in row['data']['records'])
        result['batches'].append({'filename':row['filename'],'count':len(row['data']['records']),'skipped_rows':row['data']['skipped_rows']})
    for name in ('artifacts','artifact_revisions','datasets','dataset_revisions','data_records','process_runs',
                 'process_jobs','process_attempts','input_snapshots','audit','command_receipts'):
        values=sorted(value for value in connection.execute(text('SELECT row_to_json(t)::text FROM '+name+' t')).scalars()
                      if not any(identifier in value for identifier in new_ids))
        result['prior_data'][name]={'count':len(values),'sha256':hashlib.sha256('\n'.join(values).encode()).hexdigest()}
print(json.dumps(result,sort_keys=True))
engine.dispose()
