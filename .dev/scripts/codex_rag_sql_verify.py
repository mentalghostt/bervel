"""Read-only cross-check of the real browser acceptance and unchanged older rows."""
import hashlib
import json
import sys
from uuid import UUID

from sqlalchemy import select, text
from bervel.core.config import Settings
from bervel.core.infrastructure.database import make_engine, dataset_revisions, csv_chunks
from bervel.modules.chat.infrastructure.tables import chat_turns

originals = [UUID(value) for value in sys.argv[1].split(',')]
session = UUID(sys.argv[2])
engine = make_engine(Settings.from_environment().database_url)
result = {'prior_data': {}}
new_ids = {str(value) for value in originals} | {str(session)}
with engine.connect() as connection:
    for row in connection.execute(select(dataset_revisions).where(dataset_revisions.c.artifact_id.in_(originals))).mappings():
        new_ids.update(str(row[key]) for key in ('dataset_id', 'input_snapshot_id', 'process_run_id'))
    turns = connection.execute(select(chat_turns).where(chat_turns.c.session_id == session).order_by(chat_turns.c.ordinal)).mappings().all()
    assert len(turns) == 3
    for row, expected in zip(turns, [originals[0], originals[1], originals[1]]):
        assert row['state'] == 'completed' and row['model'] == 'gpt-6.1-sol'
        assert row['sources'] and all(source['artifact_id'] == str(expected) for source in row['sources'])
        new_ids.add(str(row['turn_id']))
    result['verified_turns'] = len(turns)
    result['persisted_chunks'] = len(connection.execute(select(csv_chunks).where(csv_chunks.c.artifact_id.in_(originals))).all())
    assert result['persisted_chunks'] >= 2
    for name in ('artifacts', 'artifact_revisions', 'datasets', 'dataset_revisions', 'data_records', 'process_runs',
                 'process_jobs', 'process_attempts', 'input_snapshots', 'audit', 'command_receipts'):
        rows = sorted(value for value in connection.execute(text('SELECT row_to_json(t)::text FROM ' + name + ' t')).scalars()
                      if not any(identifier in value for identifier in new_ids))
        result['prior_data'][name] = {'count': len(rows), 'sha256': hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
print(json.dumps(result, sort_keys=True))
engine.dispose()
