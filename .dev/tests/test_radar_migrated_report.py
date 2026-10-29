"""A genuine legacy saved report must survive migration through HTTP DTOs and PDF."""
import json
import subprocess
from uuid import uuid4, uuid5

from sqlalchemy import text

from test_files_migration import legacy_engine, seed
from test_radar_analysis import NOW, SOURCE, assessment, tender


def test_legacy_saved_report_keeps_numbers_and_exact_sources_through_api_and_pdf(legacy_engine, tmp_path):
    from fastapi import FastAPI
    from http_client import ASGITestClient
    from bervel.core.infrastructure.database import migrate
    from bervel.core.models import ActorContext
    from bervel.modules.tender_radar.analysis import make_report
    from bervel.modules.tender_radar.api import RunResponse, radar_router
    from bervel.modules.tender_radar.infrastructure.repository import PostgresRadarRepository
    from bervel.modules.tender_radar.service import RadarService

    artifact, _, metadata = seed(legacy_engine)
    batch, run = uuid4(), uuid4()
    snapshot = {key: metadata[0][key] for key in
        ('artifact_id', 'revision', 'filename', 'media_type', 'created_at', 'size')}
    current = {**snapshot, 'revision': 2}
    source = {**SOURCE, **snapshot, 'total_rows': 2}
    report = make_report([tender(price='9007199254740993.1234')], [source], [assessment()], NOW)
    report['batch'] = dict(batch_id=str(batch), filename='same.txt', artifact_id=str(artifact), revision=2)
    selection = dict(stock_source=snapshot, economics_source=current, procurement_source=current,
        max_shortage_percent='0')
    report.update(selection=selection, stock_source=snapshot, economics_source=current)
    with legacy_engine.begin() as connection:
        connection.execute(text("INSERT INTO radar_batches VALUES(:id,'scope','actor',:artifact,2,'same.txt',CAST(:data AS jsonb),now())"),
            dict(id=batch, artifact=artifact, data=json.dumps({'records': []})))
        connection.execute(text("""INSERT INTO radar_runs(run_id,batch_id,actor_id,data_scope,actor_permissions,
            sources_snapshot,focus,state,created_at,report,selection,model)
            VALUES(:id,:batch,'actor','scope','[]',CAST(:sources AS jsonb),'','completed',now(),
            CAST(:report AS jsonb),CAST(:selection AS jsonb),'saved-model')"""),
            dict(id=run, batch=batch, sources=json.dumps([snapshot]), report=json.dumps(report),
                selection=json.dumps(selection)))
    migrate(legacy_engine)

    actor = ActorContext('actor', 'scope', frozenset({'process:read', 'artifact:read', 'dataset:read'}))
    repository = PostgresRadarRepository(legacy_engine)
    saved = repository.read(actor, run)
    migrated_id = str(uuid5(artifact, 'bervel:file-revision:1'))
    assert saved['report']['items'] == report['items']
    assert saved['report']['counts'] == report['counts']
    assert saved['report']['as_of'] == report['as_of']
    assert saved['sources_snapshot'][0] == {key: value for key, value in snapshot.items() if key != 'revision'} | {'artifact_id': migrated_id}
    assert saved['report']['sources'][0]['artifact_id'] == migrated_id
    assert saved['report']['stock_source']['artifact_id'] == migrated_id
    assert saved['report']['economics_source']['artifact_id'] == str(artifact)
    assert saved['report']['batch']['artifact_id'] == str(artifact)
    RunResponse.model_validate(saved)

    # This read-only route uses the real service/repository and cannot call a model.
    service = RadarService(repository, None, None, None)
    app = FastAPI()
    app.include_router(radar_router(service, lambda: actor))
    path = '/api/v0.1/radar/runs/' + str(run)
    with ASGITestClient(app) as client:
        response = client.get(path)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['report']['items'][0]['price'] == '9007199254740993.1234'
        assert 'revision' not in result['report']['sources'][0]
        assert 'revision' not in result['selection']['stock_source']
        pdf = client.get(path + '/report.pdf')
        assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF-')
    output = tmp_path / 'migrated-saved-report.pdf'
    output.write_bytes(pdf.content)
    pdf_text = subprocess.check_output(['pdftotext', str(output), '-']).decode()
    assert '9007199254740993,1234' in pdf_text
    assert 'same.txt' in pdf_text and 'saved-model' in pdf_text
    assert 'редакци' not in pdf_text.lower()
