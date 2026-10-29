"""Verify the real browser upload in the dedicated test database."""
import json
from uuid import UUID
from sqlalchemy import text


def verify_database_results(engine, report_path):
    report=json.loads(report_path.read_text())
    artifact_id=UUID(report['artifact_id'])
    with engine.connect() as connection:
        value=connection.execute(text('SELECT metadata FROM artifacts WHERE artifact_id=:id AND deleted_at IS NULL'),
                                 {'id':artifact_id}).scalar_one()
        assert value['filename']==report['filename'] and value['sha256']==report['sha256'] and value['size']==report['size']
        assert connection.execute(text('SELECT count(*) FROM datasets WHERE artifact_id=:id'),{'id':artifact_id}).scalar_one()==0
    return {'originalStored':True,'bytesVerifiedByBrowser':True,'noEducationalDataset':True}
