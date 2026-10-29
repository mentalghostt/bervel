"""Read-only persistent evidence, including unchanged pre-existing rows."""
import hashlib
import json
import sys
from datetime import datetime

from sqlalchemy import select, text
from bervel.core.config import Settings
from bervel.core.infrastructure.database import make_engine, document_indexes
from bervel.modules.chat.infrastructure.tables import chat_turns

report = globals().get('input_report') or json.load(sys.stdin)
assert report['passed'] and report['real_model']
new_ids = {upload['artifact_id'] for upload in report['uploads']} | {report['session_id']}
new_ids.update(turn['turn_id'] for turn in report['turns'])
engine = make_engine(Settings.from_environment().database_url)
result = {'prior_data': {}, 'sources_verified': True}
with engine.connect() as connection:
    rows = connection.execute(select(chat_turns).where(chat_turns.c.session_id == report['session_id']).order_by(chat_turns.c.ordinal)).mappings().all()
    assert len(rows) == len(report['turns']) == 6
    for row, expected in zip(rows, report['turns']):
        assert str(row['turn_id']) == expected['turn_id'] and row['state'] == 'completed'
        normalized = [{**source, 'image_attached': source.get('image_attached', False)} for source in row['sources']]
        assert row['answer'] == expected['answer'] and len(normalized) == len(expected['sources'])
        for actual, source in zip(normalized, expected['sources']):
            assert datetime.fromisoformat(actual['created_at']) == datetime.fromisoformat(source['created_at'])
            assert {key: value for key, value in actual.items() if key != 'created_at'} == {key: value for key, value in source.items() if key != 'created_at'}
        assert row['model'] == 'gpt-6.1-sol'
    result['model'] = rows[0]['model']; result['completed_turns'] = len(rows)
    indexes = connection.execute(select(document_indexes).where(document_indexes.c.artifact_id.in_([item['artifact_id'] for item in report['uploads']]))).mappings().all()
    assert len(indexes) == 5
    result['new_indexes'] = [{'artifact_id': str(row['artifact_id']), 'revision': row['revision'], 'status': row['result']['status'],
                             'units': sorted({chunk['unit'] for chunk in row['result']['chunks']})} for row in indexes]
    for name in ('artifacts', 'artifact_revisions', 'datasets', 'dataset_revisions', 'data_records', 'process_runs',
                 'process_jobs', 'process_attempts', 'input_snapshots', 'audit', 'command_receipts'):
        values = sorted(value for value in connection.execute(text('SELECT row_to_json(t)::text FROM ' + name + ' t')).scalars()
                        if not any(identifier in value for identifier in new_ids))
        result['prior_data'][name] = {'count': len(values), 'sha256': hashlib.sha256('\n'.join(values).encode()).hexdigest()}
print(json.dumps(result, sort_keys=True))
engine.dispose()
