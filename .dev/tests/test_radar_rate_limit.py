"""Final provider throttling must not spend the rest of a radar run on new portions."""
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from bervel.core.errors import CoreError
from bervel.modules.tender_radar.service import RadarService
from bervel.modules.tender_radar.full import validate_filters
from bervel.modules.tender_radar.infrastructure.stock import StockImporter
from bervel.modules.tender_radar.infrastructure.economics import EconomicsImporter
from test_radar_analysis import NOW, SOURCE, assessment, tender


def execute_three_portions(monkeypatch, failure):
    monkeypatch.setattr('bervel.modules.tender_radar.analysis.MAX_BATCH_ITEMS', 1)
    refs = [dict(artifact_id=str(UUID(int=i)),filename=name) for i,name in enumerate(('p.csv','s.csv','e.csv'),1)]
    originals = {
        refs[0]['artifact_id']: b'cached procurement import',
        refs[1]['artifact_id']: 'sku,product,free_quantity,unit,data_kind\nP1,Болт,100,шт,mock\n'.encode(),
        refs[2]['artifact_id']: b'sku,unit,currency,sale_price,unit_cost,fixed_cost,logistics_cost,packaging_per_unit,capacity_per_day,production_lead_days,delivery_lead_days,data_kind\nP1,pcs,RUB,10,2,0,0,0,100,0,1,mock\n',
    }
    class Artifacts:
        def prepared_metadata(self, actor, ref):
            return {'usage_role': ('procurement','stock','economics')[int(ref.artifact_id)-1]}
        def read(self, actor, ref):
            return SimpleNamespace(content=originals[str(ref.artifact_id)],metadata={'filename':refs[int(ref.artifact_id)-1]['filename']})
    class Repository:
        finished = None
        def batch(self, actor, batch_id):
            return dict(batch_id=batch_id,filename='p.csv',artifact_id=UUID(int=1),data={
                'records':[tender(key,origin={'sheet':None,'row':i}) for i,key in enumerate(('first','limited','unattempted'),2)],
                'duplicate_rows':0,'skipped_rows':0})
        def checkpoint(self, run_id, report, **kwargs):
            pass
        def finish(self, run_id, **result):
            self.finished=result
    class Model:
        def __init__(self): self.portions=[]
        def generate(self, request_id, prompt):
            rows=json.loads(prompt.split('\n',1)[1])['TENDERS']
            self.portions.append([row['id'] for row in rows])
            if len(self.portions)==2: raise failure
            return {'model':'fixture','answer':json.dumps({'items':[
                assessment(row['id'],fit='irrelevant',product_group=None,tender_quote=None,profile_quote=None,evidence_indexes=[])
                for row in rows]})}
    repo=Repository();model=Model()
    service=RadarService(repo,Artifacts(),SimpleNamespace(context=lambda *args,**kwargs:[SOURCE]),None,model,
        stock_importer=StockImporter(),economics_importer=EconomicsImporter(),now=lambda:NOW)
    selection=dict(zip(('procurement_source','stock_source','economics_source'),refs),filters=validate_filters({}))
    service._run_full(None,dict(run_id=UUID(int=4),batch_id=UUID(int=5),selection=selection))
    return repo.finished,model.portions


def test_exhausted_rate_limit_preserves_success_and_leaves_following_portion_unattempted(monkeypatch):
    result,portions=execute_three_portions(monkeypatch,CoreError('MODEL_RATE_LIMITED','quota cooldown exhausted'))
    assert portions==[['first'],['limited']]
    report=result['report']
    assert report['counts']['model_analysed']==1
    assert report['counts']['not_analysed']==2
    assert report['funnel']['model_requests']==2
    assert report['coverage']['accounted_rows']==3
    assert not report['analysis_complete']
    assert report['portion_errors'][0]['code']=='MODEL_RATE_LIMITED'
    assert report['portion_errors'][0]['ids']==['limited']
    assert result['error'] and 'лимит' in result['error'].casefold()
    assert [item['id'] for item in report['items']]==['first','limited','unattempted']


@pytest.mark.parametrize('failure',[CoreError('MODEL_TIMEOUT','unknown outcome'),RuntimeError('transport failed')])
def test_other_failed_portions_are_not_replayed(monkeypatch,failure):
    result,portions=execute_three_portions(monkeypatch,failure)
    assert portions==[['first'],['limited'],['unattempted']]
    assert result['report']['counts']['model_analysed']==2
    assert result['report']['counts']['not_analysed']==1
    assert result['error']
