from decimal import Decimal

from bervel.modules.ai_tender_radar.scoring import score_assessment


def plan(margin='40', stock='0', utilization='0.4', status='feasible'):
    return {'status': status, 'stock_share': stock, 'utilization': utilization, 'reasons': [],
            'full_economics': {'revenue': '100', 'cost': str(100 - Decimal(margin)),
                               'profit': margin, 'margin_percent': margin, 'currency': 'RUB', 'scope': 'full'}}


def test_formula_examples_and_stress_threshold():
    baseline = plan()
    baseline['stress'] = plan(margin=str(Decimal(34) / Decimal(90) * 100), utilization='0.5')
    # Exact stressed economics: 90 - 66 = 24, margin 26 2/3 percent.
    baseline['stress']['full_economics'] = {'revenue': '90', 'cost': '66', 'profit': '24',
        'margin_percent': str(Decimal(24) / Decimal(90) * 100), 'currency': 'RUB', 'scope': 'full'}
    result = score_assessment({}, baseline)
    assert result['score'] == 100
    assert result['stress_score'] == 89
    assert result['priority'] == 'resilient_high'
    assert score_assessment({}, plan(margin='2', stock='1'))['score'] == 7
    tight = plan(utilization='0.95')
    tight['stress'] = plan(status='infeasible')
    assert score_assessment({}, tight)['score'] == 67
    assert score_assessment({}, tight)['stress_score'] == 0


def test_unknown_and_unprofitable_are_distinct():
    assert score_assessment({}, plan(status='infeasible'))['score'] == 0
    assert score_assessment({}, plan(status='needs_review'))['score'] is None
    assert score_assessment({}, plan(margin='0'))['score'] == 0
    partial = plan(); partial['full_economics']['scope'] = 'confirmed'
    assert score_assessment({}, partial)['score'] is None


def test_decimal_half_up_and_monotonicity():
    assert score_assessment({}, plan(margin='1.95', stock='1'))['score'] == 7
    scores = [score_assessment({}, plan(margin=str(n)))['score'] for n in range(1, 41)]
    assert scores == sorted(scores)
    scores = [score_assessment({}, plan(utilization=str(Decimal(n) / 100)))['score'] for n in range(0, 101)]
    assert scores == sorted(scores, reverse=True)
