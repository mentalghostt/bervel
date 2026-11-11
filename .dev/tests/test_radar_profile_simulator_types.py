from test_kontur_simulator import request_simulator, search_body


def test_simulator_exposes_trade_law_in_brief_and_detail(request_simulator):
    status, page = request_simulator('POST', 'search', search_body() | {'Laws': [2]})
    assert status == 200 and page['Items']
    brief = page['Items'][0]
    assert brief['TypeOfTrading'] == 2
    status, full = request_simulator('GET', 'purchases/' + brief['Id'])
    assert status == 200
    assert full['TypeOfTrading'] == brief['TypeOfTrading']
