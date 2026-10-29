"""Selection keeps relevant descriptions and ground-truthed per-position demand."""
from decimal import Decimal
import json
import pytest

from bervel.core.errors import CoreError
from test_radar_analysis import SOURCE,NOW,tender,assessment
from test_radar_stock import inventory,position


def test_deadline_and_clear_names_filter_before_model_but_description_and_ambiguity_survive():
    from bervel.modules.tender_radar.preselection import preselect
    rows=[tender('expired',deadline_at='2020-01-01T00:00:00Z'),
          tender('described',title='Поставка метизов',description='Нужны болты М16.'),
          tender('hockey',title='Хоккейные шайбы'),
          tender('cable',title='Кабель силовой',description='Кабель для электропитания.'),
          tender('mixed',title='Кабель',description='Также требуется болт М16.'),
          tender('vague',title='Различные материалы')]
    source=SOURCE|{'excerpt':'Columns: ["product"]\nRow 1: ["Болт"]\nRow 2: ["Гайка"]'}
    candidates,skipped=preselect(rows,[source],NOW)
    assert [r['id'] for r in candidates]==['described','hockey','mixed','vague']
    assert skipped['expired']['stage']=='eligibility' and skipped['cable']['stage']=='names'


def test_unknown_profile_never_uses_fastener_exclusion_dictionary():
    from bervel.modules.tender_radar.preselection import preselect
    rows=[tender(title='Кабель силовой')]
    selected,skipped=preselect(rows,[SOURCE|{'excerpt':'Силовые кабели'}],NOW)
    assert selected==rows and skipped=={}


def test_mixed_profile_cannot_lose_its_explicit_nonfastener_product():
    from bervel.modules.tender_radar.preselection import preselect
    rows=[tender(title='Кабель силовой')]
    selected,skipped=preselect(rows,[SOURCE|{'excerpt':'Болты и силовые кабели'}],NOW)
    assert selected==rows and skipped=={}


@pytest.mark.parametrize('profile,title',[('Болт, акустическая система','Динамик'),('Болт, провод силовой','Кабель силовой')])
def test_synonyms_in_unstructured_mixed_profile_reach_model(profile,title):
    from bervel.modules.tender_radar.preselection import preselect
    rows=[tender(title=title)]
    assert preselect(rows,[SOURCE|{'excerpt':profile}],NOW)==(rows,{})


def test_many_mandatory_positions_are_partitioned_for_the_response_budget():
    from bervel.modules.tender_radar.analysis import batches
    rows=[tender(str(i),description='\n'.join(f'Позиция {j}: Болт М16: 100 шт' for j in range(15))) for i in range(20)]
    portions=batches(rows,[SOURCE],'')
    assert len(portions)>1
    assert [r['id'] for portion in portions for r in portion]==[r['id'] for r in rows]


def test_output_budget_counts_supported_units_in_one_line_lots():
    from bervel.modules.tender_radar.analysis import batches
    description='; '.join(f'Позиция {j}: Болт шестигранный М16 ГОСТ7798-70 8.8 цинк: 1 ед' for j in range(50))
    rows=[tender(str(i),description=description) for i in range(5)]
    portions=batches(rows,[SOURCE],'')
    assert len(portions)==5


def extended(**changes):
    return assessment()|dict(positions=[position(quote='Болт М16: 1000 шт')],positions_complete=True)|changes


def test_verified_position_quantity_is_accepted_and_catalogue_count_never_enters_prompt():
    from bervel.modules.tender_radar.analysis import parse_assessments,model_prompt
    rows=[tender(title='Болт М16: 1000 шт')]
    stock=inventory('987654321')
    result=parse_assessments(rows,[SOURCE],json.dumps({'items':[extended()]}),catalogue=stock['items'])
    assert result[0]['positions'][0]['quantity']=='1000'
    prompt=model_prompt(rows,[SOURCE],'',catalogue=stock['items'])
    assert '987654321' not in prompt
    assert 'STOCK_CATALOGUE' in prompt


@pytest.mark.parametrize('positions,complete',[
    ([position(sku='FOREIGN')],True),([position('999',quote='Болт М16: 1000 шт')],True),
    ([position(unit='кг',quote='Болт М16: 1000 шт')],True),
    ([position(quote='Из чужого тендера: 1000 шт')],True),
    ([position(quote='Болт М16: 1000 шт')]*2,True),([],True),
])
def test_invented_or_duplicated_position_evidence_is_rejected(positions,complete):
    from bervel.modules.tender_radar.analysis import parse_assessments
    body={'items':[extended(positions=positions,positions_complete=complete)]}
    with pytest.raises(CoreError):parse_assessments([tender(title='Болт М16: 1000 шт')],[SOURCE],json.dumps(body),catalogue=inventory()['items'])


def test_report_contains_every_input_and_reproducible_quantity_decision():
    from bervel.modules.tender_radar.analysis import make_report
    rows=[tender(title='Болт М16: 1000 шт'),tender('expired',deadline_at='2020-01-01T00:00:00Z')]
    excluded={'expired':{'stage':'eligibility','reason':'Срок истёк.'}}
    report=make_report(rows,[SOURCE],[extended()],NOW,prefiltered=excluded,stock=inventory(),allowed=Decimal('10'))
    assert report['schema_version']=='1.2.0'
    assert [r['decision'] for r in report['items']]==['needs_review','excluded']
    assert report['items'][0]['stock_check']['checks'][0]['shortage']=='100'
    assert report['items'][1]['fit'] is None and report['items'][1]['decision_stage']=='eligibility'
    assert report['counts']['model_analysed']==1


def test_missing_stock_cannot_accept_a_relevant_tender():
    from bervel.modules.tender_radar.analysis import make_report
    report=make_report([tender(title='Болт М16: 1000 шт')],[SOURCE],[extended()],NOW)
    assert report['items'][0]['decision']=='needs_review'
    assert report['items'][0]['decision_stage']=='quantity'
